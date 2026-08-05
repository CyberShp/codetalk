"""Fail-closed importer for source Skill package ZIP archives."""

from __future__ import annotations

import errno
import hashlib
import lzma
import math
import os
import shutil
import stat
import struct
import tempfile
import unicodedata
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from zipfile import BadZipFile, ZipFile, ZipInfo

from app.services.skill_package_paths import SkillPackagePathError, validate_member_name


@dataclass(frozen=True)
class SkillPackageImportLimits:
    max_archive_bytes: int = 100 * 1024 * 1024
    max_entries: int = 10_000
    max_path_bytes: int = 4 * 1024
    max_total_path_bytes: int = 10 * 1024 * 1024
    max_path_segments: int = 256
    max_total_uncompressed_bytes: int = 100 * 1024 * 1024
    max_entry_uncompressed_bytes: int = 25 * 1024 * 1024
    max_compression_ratio: float = 100.0

    def __post_init__(self) -> None:
        integer_limits = (
            self.max_archive_bytes,
            self.max_entries,
            self.max_path_bytes,
            self.max_total_path_bytes,
            self.max_path_segments,
            self.max_total_uncompressed_bytes,
            self.max_entry_uncompressed_bytes,
        )
        if (
            any(isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0 for limit in integer_limits)
            or isinstance(self.max_compression_ratio, bool)
            or not math.isfinite(self.max_compression_ratio)
            or self.max_compression_ratio <= 0
        ):
            raise ValueError("Skill package import limits must be positive")


@dataclass(frozen=True)
class ImportedPackageFile:
    relative_path: str
    digest: str
    size: int


@dataclass(frozen=True)
class ImportedSkillSource:
    source_scenario_id: str
    source_path: str
    draft_root: Path


@dataclass(frozen=True)
class SkillPackageImportResult:
    archive_digest: str
    archive_root: str
    inventory: tuple[ImportedPackageFile, ...]
    skill_sources: tuple[ImportedSkillSource, ...]


class SkillPackageImportError(ValueError):
    def __init__(self, code: str, path: str | None = None) -> None:
        self.code = code
        self.path = path
        message = code if path is None else f"{code}: {path}"
        super().__init__(message)


@dataclass(frozen=True)
class _Member:
    info: ZipInfo
    name: str
    is_directory: bool


@dataclass(frozen=True)
class _DirectorySignature:
    device: int
    inode: int
    mode: int


def _archive_digest(source: object) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _canonical_path_key(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def _enforce_entry_count_limit(source: BinaryIO, limits: SkillPackageImportLimits) -> None:
    current = source.tell()
    try:
        source.seek(0, os.SEEK_END)
        size = source.tell()
        if size < 22:
            raise SkillPackageImportError("invalid_archive")
        window_size = min(size, 65_557)
        source.seek(size - window_size)
        tail = source.read(window_size)
        marker = b"PK\x05\x06"
        offset = tail.rfind(marker)
        if offset < 0 or offset + 22 > len(tail):
            raise SkillPackageImportError("invalid_archive")
        (
            _signature,
            disk_number,
            central_directory_disk,
            disk_entries,
            total_entries,
            central_directory_size,
            central_directory_offset,
            comment_length,
        ) = struct.unpack_from("<IHHHHIIH", tail, offset)
        if offset + 22 + comment_length != len(tail) or disk_number or central_directory_disk or disk_entries != total_entries:
            raise SkillPackageImportError("invalid_archive")
        if (
            total_entries == 0xFFFF
            or central_directory_size == 0xFFFFFFFF
            or central_directory_offset == 0xFFFFFFFF
            or central_directory_offset + central_directory_size > size
        ):
            raise SkillPackageImportError("entry_count_limit")
        count = 0
        consumed = 0
        source.seek(central_directory_offset)
        while consumed < central_directory_size:
            header = source.read(46)
            if len(header) != 46:
                raise SkillPackageImportError("invalid_archive")
            (
                central_signature,
                _version_made_by,
                _version_needed,
                _flags,
                _compression_method,
                _modified_time,
                _modified_date,
                _crc,
                _compressed_size,
                _uncompressed_size,
                name_length,
                extra_length,
                comment_field_length,
                _disk_start,
                _internal_attrs,
                _external_attrs,
                _local_header_offset,
            ) = struct.unpack("<IHHHHHHIIIHHHHHII", header)
            if central_signature != 0x02014B50:
                raise SkillPackageImportError("invalid_archive")
            record_size = 46 + name_length + extra_length + comment_field_length
            if record_size < 46 or consumed + record_size > central_directory_size:
                raise SkillPackageImportError("invalid_archive")
            count += 1
            if count > limits.max_entries:
                raise SkillPackageImportError("entry_count_limit")
            source.seek(record_size - 46, os.SEEK_CUR)
            consumed += record_size
        if count != total_entries:
            raise SkillPackageImportError("invalid_archive")
        if count > limits.max_entries:
            raise SkillPackageImportError("entry_count_limit")
    finally:
        source.seek(current)


def _open_archive_file(archive: Path) -> BinaryIO:
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        fd = os.open(archive, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise SkillPackageImportError("unsafe_archive_path") from exc
        if exc.errno in {errno.ENOENT, errno.ENOTDIR, errno.EISDIR}:
            raise SkillPackageImportError("invalid_archive_path") from exc
        raise SkillPackageImportError("invalid_archive_path") from exc
    try:
        descriptor_stat = os.fstat(fd)
        if not stat.S_ISREG(descriptor_stat.st_mode):
            raise SkillPackageImportError("invalid_archive_path")
        if not hasattr(os, "O_NOFOLLOW") and archive.is_symlink():
            raise SkillPackageImportError("unsafe_archive_path")
        return os.fdopen(fd, "rb")
    except Exception:
        os.close(fd)
        raise


def _directory_signature(path: Path) -> _DirectorySignature:
    try:
        details = path.stat()
    except (OSError, RuntimeError) as exc:
        raise SkillPackageImportError("unsafe_destination") from exc
    if not stat.S_ISDIR(details.st_mode):
        raise SkillPackageImportError("unsafe_destination")
    return _DirectorySignature(details.st_dev, details.st_ino, stat.S_IFMT(details.st_mode))


def _validate_directory_signature(path: Path, expected: _DirectorySignature) -> None:
    if _directory_signature(path) != expected:
        raise SkillPackageImportError("unsafe_destination")


def _copy_archive_snapshot(archive: Path, limits: SkillPackageImportLimits) -> tuple[BinaryIO, str]:
    snapshot: BinaryIO | None = None
    try:
        with _open_archive_file(archive) as archive_file:
            snapshot = tempfile.TemporaryFile()
            digest = hashlib.sha256()
            actual = 0
            for chunk in iter(lambda: archive_file.read(1024 * 1024), b""):
                actual += len(chunk)
                if actual > limits.max_archive_bytes:
                    raise SkillPackageImportError("archive_size_limit")
                digest.update(chunk)
                snapshot.write(chunk)
        snapshot.seek(0)
        archive_digest = _archive_digest(snapshot)
        snapshot.seek(0)
        expected_digest = f"sha256:{digest.hexdigest()}"
        if archive_digest != expected_digest:
            raise SkillPackageImportError("invalid_archive")
        return snapshot, archive_digest
    except Exception:
        if snapshot is not None:
            snapshot.close()
        raise


def _validate_archive_path(path: Path) -> None:
    if path.is_symlink():
        raise SkillPackageImportError("unsafe_archive_path")
    if not path.is_file():
        raise SkillPackageImportError("invalid_archive_path")


def _entry_kind(info: ZipInfo) -> bool:
    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    if info.flag_bits & 0x1:
        raise SkillPackageImportError("encrypted", info.orig_filename)
    if kind == stat.S_IFLNK:
        raise SkillPackageImportError("symlink", info.orig_filename)
    if kind and kind not in {stat.S_IFREG, stat.S_IFDIR}:
        raise SkillPackageImportError("special_file", info.orig_filename)
    is_directory = info.is_dir() or kind == stat.S_IFDIR
    if is_directory and (info.file_size or info.compress_size):
        raise SkillPackageImportError("directory_payload", info.orig_filename)
    return is_directory


def _valid_source_scenario_id(source_id: str) -> bool:
    if not source_id or source_id in {".", ".."} or source_id.startswith(".") or source_id.endswith("."):
        return False
    return not any(character.isspace() or unicodedata.category(character).startswith("C") for character in source_id)


def _source_scenario_id(relative_path: str) -> str | None:
    if not relative_path.startswith("workflows/") or not relative_path.endswith(".md"):
        return None
    source_id = relative_path[len("workflows/") : -3]
    if "/" in source_id:
        return None
    if not _valid_source_scenario_id(source_id):
        raise SkillPackageImportError("invalid_scenario_id", relative_path)
    return source_id


def _validate_implicit_prefixes(members: list[_Member]) -> None:
    prefixes: dict[str, str] = {}
    for member in members:
        parts = member.name.split("/")
        prefix_count = len(parts) if member.is_directory else len(parts) - 1
        for index in range(1, prefix_count + 1):
            prefix = "/".join(parts[:index])
            key = _canonical_path_key(prefix)
            existing = prefixes.get(key)
            if existing is not None and existing != prefix:
                raise SkillPackageImportError("canonical_collision", prefix)
            prefixes[key] = prefix


def _preflight(source: ZipFile, limits: SkillPackageImportLimits) -> tuple[list[_Member], str]:
    infos = source.infolist()
    if len(infos) > limits.max_entries:
        raise SkillPackageImportError("entry_count_limit")
    members: list[_Member] = []
    exact: set[str] = set()
    folded: set[str] = set()
    nfc: set[str] = set()
    canonical: set[str] = set()
    files: set[str] = set()
    directories: set[str] = set()
    total = 0
    total_path_bytes = 0
    for info in infos:
        # ZipInfo.filename silently truncates at NUL; orig_filename preserves it.
        original = info.orig_filename
        is_directory = _entry_kind(info)
        try:
            validate_member_name(original, is_directory=is_directory)
        except SkillPackagePathError as exc:
            raise SkillPackageImportError("unsafe_path", exc.path) from exc
        name = original[:-1] if is_directory and original.endswith("/") else original
        path_bytes = len(name.encode("utf-8"))
        if path_bytes > limits.max_path_bytes:
            raise SkillPackageImportError("path_bytes_limit", original)
        total_path_bytes += path_bytes
        if total_path_bytes > limits.max_total_path_bytes:
            raise SkillPackageImportError("path_bytes_limit")
        if len(name.split("/")) > limits.max_path_segments:
            raise SkillPackageImportError("path_depth_limit", original)
        if name in exact:
            raise SkillPackageImportError("duplicate_path", original)
        exact.add(name)
        casefolded = name.casefold()
        if casefolded in folded:
            raise SkillPackageImportError("casefold_collision", original)
        folded.add(casefolded)
        unicode_name = unicodedata.normalize("NFC", name)
        if unicode_name in nfc:
            raise SkillPackageImportError("unicode_collision", original)
        nfc.add(unicode_name)
        canonical_name = _canonical_path_key(name)
        if canonical_name in canonical:
            raise SkillPackageImportError("canonical_collision", original)
        canonical.add(canonical_name)
        if not is_directory:
            if info.file_size > limits.max_entry_uncompressed_bytes:
                raise SkillPackageImportError("entry_size_limit", original)
            total += info.file_size
            if total > limits.max_total_uncompressed_bytes:
                raise SkillPackageImportError("total_size_limit")
            if info.file_size and (info.compress_size == 0 or info.file_size / info.compress_size > limits.max_compression_ratio):
                raise SkillPackageImportError("compression_ratio_limit", original)
            files.add(name)
        else:
            directories.add(name)
        members.append(_Member(info, name, is_directory))
    canonical_files = {_canonical_path_key(file_name) for file_name in files}
    for file_name in files:
        parts = file_name.split("/")
        for index in range(1, len(parts)):
            ancestor = "/".join(parts[:index])
            if _canonical_path_key(ancestor) in canonical_files:
                raise SkillPackageImportError("directory_file_conflict", ancestor)
    for directory in directories:
        if _canonical_path_key(directory) in canonical_files:
            raise SkillPackageImportError("directory_file_conflict", directory)
    _validate_implicit_prefixes(members)
    roots = {member.name.rsplit("/", 1)[0] if "/" in member.name else "" for member in members if not member.is_directory and member.name.rsplit("/", 1)[-1] == "SKILL.md"}
    if len(roots) != 1:
        path = None
        if roots:
            root = sorted(roots)[-1]
            path = f"{root + '/' if root else ''}SKILL.md"
        raise SkillPackageImportError("ambiguous_archive_root", path)
    root = roots.pop()
    if root:
        prefix = f"{root}/"
        for member in members:
            if member.name != root and not member.name.startswith(prefix):
                raise SkillPackageImportError("ambiguous_archive_root", member.info.orig_filename)
    for member in members:
        if member.is_directory:
            continue
        relative_path = member.name[len(root) + 1 :] if root else member.name
        _source_scenario_id(relative_path)
    return members, root


def _write_member(source: ZipFile, member: _Member, staging: Path, limits: SkillPackageImportLimits) -> ImportedPackageFile:
    target = staging.joinpath(*member.name.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    actual = 0
    try:
        with source.open(member.info, "r") as input_file, target.open("xb") as output_file:
            while True:
                chunk = input_file.read(min(1024 * 1024, limits.max_entry_uncompressed_bytes + 1))
                if not chunk:
                    break
                actual += len(chunk)
                if actual > limits.max_entry_uncompressed_bytes:
                    raise SkillPackageImportError("entry_size_limit", member.info.orig_filename)
                digest.update(chunk)
                output_file.write(chunk)
    except SkillPackageImportError:
        raise
    except (BadZipFile, EOFError, OSError, NotImplementedError, lzma.LZMAError, zlib.error) as exc:
        raise SkillPackageImportError("invalid_archive") from exc
    if actual != member.info.file_size:
        raise SkillPackageImportError("invalid_archive")
    return ImportedPackageFile(member.name, f"sha256:{digest.hexdigest()}", actual)


def import_skill_package(
    archive_path: str | Path,
    destination: str | Path,
    *,
    limits: SkillPackageImportLimits | None = None,
) -> SkillPackageImportResult:
    """Verify and install a Skill package ZIP without bulk ZIP extraction."""
    archive = Path(archive_path)
    destination_path = Path(destination)
    active_limits = limits or SkillPackageImportLimits()
    _validate_archive_path(archive)
    if ".." in destination_path.parts:
        raise SkillPackageImportError("unsafe_destination")
    if destination_path.is_symlink():
        raise SkillPackageImportError("unsafe_destination")
    try:
        destination_parent = destination_path.parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SkillPackageImportError("unsafe_destination") from exc
    destination_parent_signature = _directory_signature(destination_parent)
    canonical_destination = destination_parent / destination_path.name
    try:
        archive_file, archive_digest = _copy_archive_snapshot(archive, active_limits)
        with archive_file:
            _enforce_entry_count_limit(archive_file, active_limits)
            with ZipFile(archive_file) as source:
                members, archive_root = _preflight(source, active_limits)
                _validate_directory_signature(destination_parent, destination_parent_signature)
                if canonical_destination.exists() or canonical_destination.is_symlink():
                    raise SkillPackageImportError("destination_exists")
                staging = Path(tempfile.mkdtemp(prefix=f".{canonical_destination.name}-", dir=destination_parent))
                try:
                    inventory: list[ImportedPackageFile] = []
                    for member in members:
                        target = staging.joinpath(*member.name.split("/"))
                        if member.is_directory:
                            target.mkdir(parents=True, exist_ok=True)
                        else:
                            extracted = _write_member(source, member, staging, active_limits)
                            relative_path = member.name[len(archive_root) + 1 :] if archive_root else member.name
                            inventory.append(ImportedPackageFile(relative_path, extracted.digest, extracted.size))
                    _validate_directory_signature(destination_parent, destination_parent_signature)
                    if canonical_destination.exists() or canonical_destination.is_symlink():
                        raise SkillPackageImportError("destination_exists")
                    os.rename(staging, canonical_destination)
                except Exception:
                    shutil.rmtree(staging, ignore_errors=True)
                    raise
    except SkillPackageImportError:
        raise
    except (BadZipFile, EOFError, OSError, UnicodeDecodeError) as exc:
        raise SkillPackageImportError("invalid_archive") from exc
    draft_root = canonical_destination.joinpath(*archive_root.split("/")) if archive_root else canonical_destination
    sources = tuple(
        ImportedSkillSource(source_id, relative_path, draft_root)
        for member in members
        if not member.is_directory
        and (relative_path := (member.name[len(archive_root) + 1 :] if archive_root else member.name)).startswith("workflows/")
        and relative_path.endswith(".md")
        and "/" not in relative_path[len("workflows/") : -3]
        and (source_id := _source_scenario_id(relative_path)) is not None
    )
    return SkillPackageImportResult(archive_digest, archive_root, tuple(inventory), sources)
