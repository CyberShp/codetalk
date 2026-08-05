"""RED contract for the F014 safe Skill package ZIP importer.

The importer accepts either a single wrapper directory (the official package)
or a rootless package. A Pack split is logical: ``ImportedSkillSource`` items
point at one verified unpacked package root, rather than making copied drafts
or claiming terminal ``skill.*`` IDs before the Task 4 compiler.
"""

from __future__ import annotations

import errno
import hashlib
import importlib
import json
import os
import stat
import struct
import tempfile
import zlib
from dataclasses import is_dataclass
from pathlib import Path
from types import ModuleType
from typing import Any
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest


PINNED_ARCHIVE_SHA256 = "7369ef35d339bc554610754ceb385b78d15f94fc8e1e5435350c4ebcf2b27325"
SCENARIOS = ["custom", "issue-regression", "module-analysis", "root-cause", "special-risk"]


def _importer() -> ModuleType:
    try:
        return importlib.import_module("app.services.skill_package_importer")
    except ModuleNotFoundError as exc:
        if exc.name == "app.services.skill_package_importer":
            pytest.fail("RED: app.services.skill_package_importer has not been implemented")
        raise


def _valid_entries(*, root: str = "official-pack", include_utf8: bool = False) -> list[tuple[str, bytes]]:
    prefix = f"{root}/" if root else ""
    entries = [
        (f"{prefix}SKILL.md", b"# Skill package\n"),
        (f"{prefix}workflow-manifest.json", b"{}"),
        *[(f"{prefix}workflows/{scenario}.md", f"# {scenario}\n".encode()) for scenario in SCENARIOS],
    ]
    if include_utf8:
        entries.extend([
            (f"{prefix}templates/开发给测试讲代码模板.md", "测试说明".encode()),
            (f"{prefix}templates/流程讲解活文档模板.md", "流程说明".encode()),
            (f"{prefix}templates/黑盒测试用例Markdown模板.md", "黑盒说明".encode()),
        ])
    return entries


def _write_zip(path: Path, entries: list[tuple[str | ZipInfo, bytes]], *, compression: int = ZIP_DEFLATED) -> Path:
    with ZipFile(path, "w", compression=compression) as archive:
        for name, payload in entries:
            archive.writestr(name, payload)
    return path


def _write_raw_stored_zip(
    path: Path,
    entries: list[tuple[bytes, bytes]],
    *,
    encrypted_name: bytes | None = None,
    compression_method: int = 0,
) -> Path:
    """Write names the stdlib writer refuses or silently changes (empty/NUL)."""
    local_records: list[bytes] = []
    central_records: list[bytes] = []
    offset = 0
    for name, payload in entries:
        flags = 0x801 if name == encrypted_name else 0x800
        crc = zlib.crc32(payload)
        local = struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, flags, compression_method, 0, 0, crc, len(payload), len(payload), len(name), 0) + name + payload
        central = struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 0x031E, 20, flags, compression_method, 0, 0, crc, len(payload), len(payload), len(name), 0, 0, 0, 0, 0, offset) + name
        local_records.append(local)
        central_records.append(central)
        offset += len(local)
    body = b"".join(local_records)
    central_directory = b"".join(central_records)
    footer = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(entries), len(entries), len(central_directory), len(body), 0)
    path.write_bytes(body + central_directory + footer)
    return path


def _forge_eocd_entry_counts(path: Path, count: int) -> None:
    data = bytearray(path.read_bytes())
    marker = b"PK\x05\x06"
    offset = data.rfind(marker)
    assert offset >= 0
    struct.pack_into("<HH", data, offset + 8, count, count)
    path.write_bytes(data)


def _error(importer: ModuleType, archive: Path, destination: Path, **kwargs: Any) -> BaseException:
    with pytest.raises(importer.SkillPackageImportError) as caught:
        importer.import_skill_package(archive, destination, **kwargs)
    return caught.value


def _assert_error(error: BaseException, *, code: str, path: str | None = None) -> None:
    assert getattr(error, "code") == code
    assert getattr(error, "path", None) == path


def _limits(importer: ModuleType, **overrides: Any) -> Any:
    defaults = dict(
        max_archive_bytes=1_000_000,
        max_entries=100,
        max_path_bytes=4_096,
        max_total_path_bytes=100_000,
        max_path_segments=128,
        max_total_uncompressed_bytes=1_000_000,
        max_entry_uncompressed_bytes=100_000,
        max_compression_ratio=100.0,
    )
    defaults.update(overrides)
    return importer.SkillPackageImportLimits(**defaults)


@pytest.mark.parametrize("ratio", [float("nan"), float("inf"), float("-inf")])
def test_import_limits_reject_nonfinite_compression_ratios(ratio: float) -> None:
    importer = _importer()
    with pytest.raises(ValueError):
        _limits(importer, max_compression_ratio=ratio)


def test_import_limits_reject_boolean_compression_ratio() -> None:
    importer = _importer()
    with pytest.raises(ValueError):
        _limits(importer, max_compression_ratio=True)


@pytest.mark.parametrize(
    "field",
    [
        "max_archive_bytes",
        "max_entries",
        "max_path_bytes",
        "max_total_path_bytes",
        "max_path_segments",
        "max_total_uncompressed_bytes",
        "max_entry_uncompressed_bytes",
    ],
)
def test_import_limits_reject_nonfinite_integer_limit_fields(field: str) -> None:
    importer = _importer()
    with pytest.raises(ValueError):
        _limits(importer, **{field: float("inf")})


def _inventory_rows(result: Any) -> list[tuple[str, str, int]]:
    return [(entry.relative_path, entry.digest, entry.size) for entry in result.inventory]


def test_imports_utf8_wrapper_package_with_exact_inventory_digests_and_one_verified_tree(tmp_path: Path) -> None:
    importer = _importer()
    archive = _write_zip(tmp_path / "package.zip", _valid_entries(include_utf8=True))
    destination = tmp_path / "destination"

    result = importer.import_skill_package(archive, destination)

    expected_entries = _valid_entries(include_utf8=True)
    assert is_dataclass(result) and getattr(type(result), "__dataclass_params__").frozen
    assert result.archive_digest == f"sha256:{hashlib.sha256(archive.read_bytes()).hexdigest()}"
    assert result.archive_root == "official-pack"
    assert _inventory_rows(result) == [
        (name.removeprefix("official-pack/"), f"sha256:{hashlib.sha256(contents).hexdigest()}", len(contents))
        for name, contents in expected_entries
    ]
    for name, contents in expected_entries:
        extracted = destination / name
        assert extracted.read_bytes() == contents
    assert sorted(path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()) == sorted(name for name, _ in expected_entries)


def test_archive_digest_and_extraction_use_one_private_snapshot_when_source_path_changes_after_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    importer = _importer()

    def archive_entries(skill_payload: bytes) -> list[tuple[str, bytes]]:
        return [
            ("official-pack/SKILL.md", skill_payload),
            ("official-pack/workflow-manifest.json", b"{}"),
            ("official-pack/workflows/module-analysis.md", b"# module\n"),
        ]

    archive = _write_zip(tmp_path / "package.zip", archive_entries(b"old"), compression=ZIP_STORED)
    expected_digest = f"sha256:{hashlib.sha256(archive.read_bytes()).hexdigest()}"
    initial_stat = archive.stat()
    original_archive_digest = importer._archive_digest

    def mutating_archive_digest(source: Any) -> str:
        digest = original_archive_digest(source)
        _write_zip(archive, archive_entries(b"new"), compression=ZIP_STORED)
        os.utime(archive, ns=(initial_stat.st_atime_ns, initial_stat.st_mtime_ns))
        return digest

    monkeypatch.setattr(importer, "_archive_digest", mutating_archive_digest)

    result = importer.import_skill_package(archive, tmp_path / "destination")

    assert result.archive_digest == expected_digest
    assert (tmp_path / "destination" / "official-pack" / "SKILL.md").read_bytes() == b"old"
    assert f"sha256:{hashlib.sha256(archive.read_bytes()).hexdigest()}" != expected_digest


def test_archive_open_rejects_symlink_replacement_after_lexical_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    importer = _importer()
    archive = _write_zip(tmp_path / "package.zip", _valid_entries())
    symlink_target = _write_zip(tmp_path / "target.zip", _valid_entries(root="target-pack"))
    original_validate = importer._validate_archive_path

    def replacing_validate(path: Path) -> None:
        original_validate(path)
        path.unlink()
        path.symlink_to(symlink_target)

    monkeypatch.setattr(importer, "_validate_archive_path", replacing_validate)

    error = _error(importer, archive, tmp_path / "destination")

    _assert_error(error, code="unsafe_archive_path")
    assert not (tmp_path / "destination").exists()


def test_rejects_archive_byte_limit_while_copying_private_snapshot(tmp_path: Path) -> None:
    importer = _importer()
    archive = _write_zip(tmp_path / "package.zip", _valid_entries())

    error = _error(importer, archive, tmp_path / "destination", limits=_limits(importer, max_archive_bytes=10))

    _assert_error(error, code="archive_size_limit")
    assert not (tmp_path / "destination").exists()


def test_rejects_entry_count_limit_before_constructing_zipfile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    importer = _importer()
    archive = _write_zip(tmp_path / "package.zip", _valid_entries())

    def forbidden_zipfile(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("entry count limit should be enforced before ZipFile construction")

    monkeypatch.setattr(importer, "ZipFile", forbidden_zipfile)

    error = _error(importer, archive, tmp_path / "destination", limits=_limits(importer, max_entries=1))

    _assert_error(error, code="entry_count_limit")
    assert not (tmp_path / "destination").exists()


def test_rejects_forged_eocd_entry_count_before_constructing_zipfile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    importer = _importer()
    entries = [*_valid_entries(), *[(f"official-pack/extra-{index}.md", b"x") for index in range(20)]]
    archive = _write_zip(tmp_path / "forged-count.zip", entries)
    _forge_eocd_entry_counts(archive, 1)

    def forbidden_zipfile(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("central directory count should be enforced before ZipFile construction")

    monkeypatch.setattr(importer, "ZipFile", forbidden_zipfile)

    error = _error(importer, archive, tmp_path / "destination", limits=_limits(importer, max_entries=10))

    _assert_error(error, code="entry_count_limit")
    assert not (tmp_path / "destination").exists()


def test_rootless_package_is_accepted_when_markers_identify_one_package(tmp_path: Path) -> None:
    importer = _importer()
    archive = _write_zip(tmp_path / "rootless.zip", _valid_entries(root=""))

    result = importer.import_skill_package(archive, tmp_path / "destination")

    assert result.archive_root == ""
    assert (tmp_path / "destination" / "SKILL.md").is_file()


def test_import_accepts_child_destination_under_a_standard_library_lexical_temp_parent() -> None:
    importer = _importer()
    with tempfile.TemporaryDirectory() as temporary_directory:
        parent = Path(temporary_directory)
        archive = _write_zip(parent / "package.zip", _valid_entries())
        destination = parent / "destination"

        result = importer.import_skill_package(archive, destination)

        assert result.archive_root == "official-pack"
        assert (destination / "official-pack" / "SKILL.md").is_file()


def test_workflow_directory_structure_creates_five_logical_declarations_without_prose_inference(tmp_path: Path) -> None:
    importer = _importer()
    entries = _valid_entries()
    entries.append(("official-pack/notes.md", b"This mentions issue regression and root cause, but declares nothing."))
    result = importer.import_skill_package(_write_zip(tmp_path / "package.zip", entries), tmp_path / "destination")

    sources = result.skill_sources
    assert [source.source_scenario_id for source in sources] == SCENARIOS
    assert [source.source_path for source in sources] == [f"workflows/{scenario}.md" for scenario in SCENARIOS]
    assert len({source.draft_root for source in sources}) == 1
    assert sources[0].draft_root == tmp_path / "destination" / "official-pack"
    assert all(type(source).__name__ == "ImportedSkillSource" and is_dataclass(source) and getattr(type(source), "__dataclass_params__").frozen for source in sources)


def test_each_declared_workflow_file_becomes_a_skill_without_reading_its_prose(tmp_path: Path) -> None:
    importer = _importer()
    entries = _valid_entries()
    entries.append(("official-pack/workflows/extra.md", b"# module analysis\n"))
    result = importer.import_skill_package(_write_zip(tmp_path / "package.zip", entries), tmp_path / "destination")
    assert [source.source_scenario_id for source in result.skill_sources] == [*SCENARIOS, "extra"]


@pytest.mark.parametrize(
    ("name", "payload", "code", "path"),
    [
        ("../escape.md", b"x", "unsafe_path", "../escape.md"),
        ("/absolute.md", b"x", "unsafe_path", "/absolute.md"),
        ("C:/escape.md", b"x", "unsafe_path", "C:/escape.md"),
        (r"\\server\share\escape.md", b"x", "unsafe_path", r"\\server\share\escape.md"),
        (r"official-pack\workflows\bad.md", b"x", "unsafe_path", r"official-pack\workflows\bad.md"),
        ("official-pack/base:stream.md", b"x", "unsafe_path", "official-pack/base:stream.md"),
        ("official-pack/CON.md", b"x", "unsafe_path", "official-pack/CON.md"),
        ("official-pack/CON .txt", b"x", "unsafe_path", "official-pack/CON .txt"),
        ("official-pack/CONIN$.md", b"x", "unsafe_path", "official-pack/CONIN$.md"),
        ("official-pack/CONOUT$/file.md", b"x", "unsafe_path", "official-pack/CONOUT$/file.md"),
        ("official-pack/AUX  .md", b"x", "unsafe_path", "official-pack/AUX  .md"),
        ("official-pack/COM1 .log", b"x", "unsafe_path", "official-pack/COM1 .log"),
        ("official-pack/COM\u00b9.md", b"x", "unsafe_path", "official-pack/COM\u00b9.md"),
        ("official-pack/LPT\u00b3/file.md", b"x", "unsafe_path", "official-pack/LPT\u00b3/file.md"),
        ("official-pack/LPT\u00b3 .bin", b"x", "unsafe_path", "official-pack/LPT\u00b3 .bin"),
        ("official-pack/aux/file.md", b"x", "unsafe_path", "official-pack/aux/file.md"),
        ("official-pack/trailing-dot./file.md", b"x", "unsafe_path", "official-pack/trailing-dot./file.md"),
        ("official-pack/trailing-space /file.md", b"x", "unsafe_path", "official-pack/trailing-space /file.md"),
        ("official-pack/control\nname.md", b"x", "unsafe_path", "official-pack/control\nname.md"),
        ("official-pack/tab\tname.md", b"x", "unsafe_path", "official-pack/tab\tname.md"),
        ("official-pack/escape\u001bname.md", b"x", "unsafe_path", "official-pack/escape\u001bname.md"),
        ("official-pack/angle<name>.md", b"x", "unsafe_path", "official-pack/angle<name>.md"),
        ("official-pack/quote\"name.md", b"x", "unsafe_path", "official-pack/quote\"name.md"),
        ("official-pack/pipe|name.md", b"x", "unsafe_path", "official-pack/pipe|name.md"),
        ("official-pack/question?name.md", b"x", "unsafe_path", "official-pack/question?name.md"),
        ("official-pack/star*name.md", b"x", "unsafe_path", "official-pack/star*name.md"),
        ("official-pack/./bad.md", b"x", "unsafe_path", "official-pack/./bad.md"),
        ("official-pack/a/../bad.md", b"x", "unsafe_path", "official-pack/a/../bad.md"),
    ],
)
def test_rejects_unsafe_member_names_before_writing(tmp_path: Path, name: str, payload: bytes, code: str, path: str) -> None:
    importer = _importer()
    archive = _write_zip(tmp_path / "unsafe.zip", [* _valid_entries(), (name, payload)])
    destination = tmp_path / "destination"
    error = _error(importer, archive, destination)
    _assert_error(error, code=code, path=path)
    assert not destination.exists()


@pytest.mark.parametrize(("raw_name", "path"), [(b"", ""), (b"official-pack/nul\x00name.md", "official-pack/nul\x00name.md")])
def test_rejects_empty_and_nul_central_directory_names_before_writing(tmp_path: Path, raw_name: bytes, path: str) -> None:
    raw_entries = [(name.encode("utf-8"), contents) for name, contents in _valid_entries()]
    archive = _write_raw_stored_zip(tmp_path / "unsafe-raw.zip", [*raw_entries, (raw_name, b"x")])
    destination = tmp_path / "destination"
    importer = _importer()
    error = _error(importer, archive, destination)
    _assert_error(error, code="unsafe_path", path=path)
    assert not destination.exists()


def test_invalid_utf8_central_directory_name_is_normalized_to_invalid_archive(tmp_path: Path) -> None:
    raw_entries = [(name.encode("utf-8"), contents) for name, contents in _valid_entries()]
    archive = _write_raw_stored_zip(
        tmp_path / "invalid-utf8.zip",
        [*raw_entries, (b"official-pack/\xff.md", b"x")],
    )
    with pytest.raises(UnicodeDecodeError):
        with ZipFile(archive) as source:
            source.infolist()

    importer = _importer()
    destination = tmp_path / "destination"
    error = _error(importer, archive, destination)
    _assert_error(error, code="invalid_archive")
    assert not destination.exists()
    assert list(tmp_path.glob(f".{destination.name}-*")) == []


def test_rejects_duplicate_casefold_and_nfc_collisions_before_writing(tmp_path: Path) -> None:
    importer = _importer()
    collision_cases = [
        ([("official-pack/README.md", b"one"), ("official-pack/README.md", b"two")], "duplicate_path", "official-pack/README.md"),
        ([("official-pack/Readme.md", b"one"), ("official-pack/README.md", b"two")], "casefold_collision", "official-pack/README.md"),
        ([("official-pack/caf\u00e9.md", b"one"), ("official-pack/cafe\u0301.md", b"two")], "unicode_collision", "official-pack/cafe\u0301.md"),
    ]
    for index, (members, code, path) in enumerate(collision_cases):
        archive_path = tmp_path / f"collision-{index}.zip"
        if code == "duplicate_path":
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive = _write_zip(archive_path, [*_valid_entries(), *members])
        else:
            archive = _write_zip(archive_path, [*_valid_entries(), *members])
        destination = tmp_path / f"destination-{index}"
        error = _error(importer, archive, destination)
        _assert_error(error, code=code, path=path)
        assert not destination.exists()


def test_rejects_combined_nfc_casefold_collisions_before_writing(tmp_path: Path) -> None:
    importer = _importer()
    archive = _write_zip(
        tmp_path / "combined-collision.zip",
        [
            *_valid_entries(),
            ("official-pack/\u00e9.md", b"one"),
            ("official-pack/E\u0301.md", b"two"),
        ],
    )

    error = _error(importer, archive, tmp_path / "destination")

    _assert_error(error, code="canonical_collision", path="official-pack/E\u0301.md")
    assert not (tmp_path / "destination").exists()


def test_rejects_canonical_implicit_ancestor_file_conflict_before_writing(tmp_path: Path) -> None:
    importer = _importer()
    archive = _write_zip(
        tmp_path / "ancestor-collision.zip",
        [*_valid_entries(), ("official-pack/A", b"file"), ("official-pack/a/b.md", b"child")],
    )

    error = _error(importer, archive, tmp_path / "destination")

    _assert_error(error, code="directory_file_conflict", path="official-pack/a")
    assert not (tmp_path / "destination").exists()


def test_rejects_canonical_aliases_between_implicit_directories_before_writing(tmp_path: Path) -> None:
    importer = _importer()
    archive = _write_zip(
        tmp_path / "implicit-directory-collision.zip",
        [*_valid_entries(), ("official-pack/A/x.md", b"one"), ("official-pack/a/y.md", b"two")],
    )

    error = _error(importer, archive, tmp_path / "destination")

    _assert_error(error, code="canonical_collision", path="official-pack/a")
    assert not (tmp_path / "destination").exists()


def test_rejects_directory_file_conflict_and_ambiguous_package_roots(tmp_path: Path) -> None:
    importer = _importer()
    conflict = _write_zip(tmp_path / "conflict.zip", [* _valid_entries(), ("official-pack/a", b"file"), ("official-pack/a/b.md", b"child")])
    error = _error(importer, conflict, tmp_path / "conflict-destination")
    _assert_error(error, code="directory_file_conflict", path="official-pack/a")

    ambiguous = _write_zip(tmp_path / "ambiguous.zip", [* _valid_entries(), ("other/SKILL.md", b"other")])
    error = _error(importer, ambiguous, tmp_path / "ambiguous-destination")
    _assert_error(error, code="ambiguous_archive_root", path="other/SKILL.md")


def test_wrapper_package_rejects_root_level_member_outside_its_prefix(tmp_path: Path) -> None:
    importer = _importer()
    archive = _write_zip(tmp_path / "ambiguous.zip", [*_valid_entries(), ("outside.txt", b"stray")])
    destination = tmp_path / "destination"

    error = _error(importer, archive, destination)

    _assert_error(error, code="ambiguous_archive_root", path="outside.txt")
    assert not destination.exists()


def test_rejects_symlink_and_non_regular_special_file_entries(tmp_path: Path) -> None:
    importer = _importer()
    entries: list[tuple[str | ZipInfo, bytes]] = list(_valid_entries())
    symlink = ZipInfo("official-pack/link")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    special = ZipInfo("official-pack/socket")
    special.create_system = 3
    special.external_attr = (stat.S_IFSOCK | 0o600) << 16
    for index, (entry, code, path) in enumerate(((symlink, "symlink", "official-pack/link"), (special, "special_file", "official-pack/socket"))):
        archive = _write_zip(tmp_path / f"type-{index}.zip", [*entries, (entry, b"payload")])
        error = _error(importer, archive, tmp_path / f"destination-{index}")
        _assert_error(error, code=code, path=path)


def test_rejects_special_unix_type_even_when_name_ends_with_slash(tmp_path: Path) -> None:
    importer = _importer()
    socket_directory = ZipInfo("official-pack/socket/")
    socket_directory.create_system = 3
    socket_directory.external_attr = (stat.S_IFSOCK | 0o600) << 16
    archive = _write_zip(tmp_path / "socket-directory.zip", [*_valid_entries(), (socket_directory, b"")])

    error = _error(importer, archive, tmp_path / "destination")

    _assert_error(error, code="special_file", path="official-pack/socket/")
    assert not (tmp_path / "destination").exists()


def test_rejects_an_encrypted_flag_before_attempting_to_read_the_member(tmp_path: Path) -> None:
    encrypted_name = b"official-pack/encrypted.md"
    raw_entries = [(name.encode("utf-8"), contents) for name, contents in _valid_entries()]
    archive = _write_raw_stored_zip(tmp_path / "encrypted.zip", [*raw_entries, (encrypted_name, b"payload")], encrypted_name=encrypted_name)
    with ZipFile(archive) as source:
        assert source.getinfo(encrypted_name.decode()).flag_bits & 0x1
    importer = _importer()
    error = _error(importer, archive, tmp_path / "destination")
    _assert_error(error, code="encrypted", path=encrypted_name.decode())


def test_rejects_data_bearing_directory_entries_before_writing(tmp_path: Path) -> None:
    importer = _importer()
    directory = ZipInfo("official-pack/payload-directory/")
    directory.create_system = 3
    directory.external_attr = (stat.S_IFDIR | 0o755) << 16
    archive = _write_zip(tmp_path / "directory-payload.zip", [*_valid_entries(), (directory, b"hidden bytes")])

    error = _error(importer, archive, tmp_path / "destination")

    _assert_error(error, code="directory_payload", path="official-pack/payload-directory/")
    assert not (tmp_path / "destination").exists()


def test_unsupported_compression_method_is_normalized_to_import_error(tmp_path: Path) -> None:
    importer = _importer()
    raw_entries = [(name.encode("utf-8"), contents) for name, contents in _valid_entries()]
    archive = _write_raw_stored_zip(tmp_path / "unsupported-compression.zip", raw_entries, compression_method=99)

    error = _error(importer, archive, tmp_path / "destination")

    _assert_error(error, code="invalid_archive")
    assert not (tmp_path / "destination").exists()


def test_malformed_deflate_stream_is_normalized_to_import_error(tmp_path: Path) -> None:
    importer = _importer()
    archive = _write_zip(tmp_path / "malformed-deflate.zip", _valid_entries(), compression=ZIP_DEFLATED)
    data = bytearray(archive.read_bytes())
    first_payload = b"# Skill package\n"
    header_name = b"official-pack/SKILL.md"
    local_header = data.index(header_name)
    payload_start = local_header + len(header_name)
    payload_end = data.index(b"PK\x03\x04", payload_start)
    for index in range(payload_start, payload_end):
        data[index] = 0xFF
    archive.write_bytes(data)

    error = _error(importer, archive, tmp_path / "destination")

    _assert_error(error, code="invalid_archive")
    assert first_payload not in (tmp_path / "destination" / "official-pack" / "SKILL.md").read_bytes() if (tmp_path / "destination" / "official-pack" / "SKILL.md").exists() else True


def test_malformed_lzma_stream_is_normalized_to_import_error(tmp_path: Path) -> None:
    importer = _importer()
    archive = _write_zip(tmp_path / "malformed-lzma.zip", _valid_entries(), compression=14)
    data = bytearray(archive.read_bytes())
    header_name = b"official-pack/SKILL.md"
    local_header = data.index(header_name)
    payload_start = local_header + len(header_name)
    payload_end = data.index(b"PK\x03\x04", payload_start)
    for index in range(payload_start, payload_end):
        data[index] = 0xFF
    archive.write_bytes(data)

    error = _error(importer, archive, tmp_path / "destination")

    _assert_error(error, code="invalid_archive")
    assert not (tmp_path / "destination").exists()


@pytest.mark.parametrize(
    ("limits", "extra_entries", "code", "path"),
    [
        (dict(max_entries=6), [], "entry_count_limit", None),
        (dict(max_entry_uncompressed_bytes=20), [("official-pack/large.md", b"x" * 21)], "entry_size_limit", "official-pack/large.md"),
        (dict(max_total_uncompressed_bytes=4), [], "total_size_limit", None),
        (dict(max_compression_ratio=1.1), [("official-pack/repeated.md", b"x" * 20_000)], "compression_ratio_limit", "official-pack/repeated.md"),
    ],
)
def test_rejects_declared_resource_limits_before_writing(tmp_path: Path, limits: dict[str, Any], extra_entries: list[tuple[str, bytes]], code: str, path: str | None) -> None:
    importer = _importer()
    archive = _write_zip(tmp_path / "limits.zip", [* _valid_entries(), *extra_entries])
    destination = tmp_path / "destination"
    error = _error(importer, archive, destination, limits=_limits(importer, **limits))
    _assert_error(error, code=code, path=path)
    assert not destination.exists()


def test_rejects_path_depth_limit_before_expanding_implicit_prefixes(tmp_path: Path) -> None:
    importer = _importer()
    deep_name = "official-pack/" + "/".join(f"s{index}" for index in range(20)) + "/leaf.md"
    archive = _write_zip(tmp_path / "deep-path.zip", [*_valid_entries(), (deep_name, b"x")])

    error = _error(importer, archive, tmp_path / "destination", limits=_limits(importer, max_path_segments=12))

    _assert_error(error, code="path_depth_limit", path=deep_name)
    assert not (tmp_path / "destination").exists()


def test_rejects_member_path_byte_limit_before_expanding_implicit_prefixes(tmp_path: Path) -> None:
    importer = _importer()
    long_name = "official-pack/" + "/".join("segment" * 8 for _ in range(6)) + "/leaf.md"
    archive = _write_zip(tmp_path / "long-path.zip", [*_valid_entries(), (long_name, b"x")])

    error = _error(importer, archive, tmp_path / "destination", limits=_limits(importer, max_path_bytes=80))

    _assert_error(error, code="path_bytes_limit", path=long_name)
    assert not (tmp_path / "destination").exists()


def test_rejects_total_path_byte_limit_before_expanding_implicit_prefixes(tmp_path: Path) -> None:
    importer = _importer()
    entries = [*_valid_entries(), *[(f"official-pack/long-path-{index}.md", b"x") for index in range(10)]]
    archive = _write_zip(tmp_path / "total-path-bytes.zip", entries)

    error = _error(importer, archive, tmp_path / "destination", limits=_limits(importer, max_total_path_bytes=80))

    _assert_error(error, code="path_bytes_limit")
    assert not (tmp_path / "destination").exists()


def test_preflight_failure_never_partially_extracts_or_replaces_existing_destination(tmp_path: Path) -> None:
    importer = _importer()
    destination = tmp_path / "destination"
    destination.mkdir()
    sentinel = destination / "sentinel.txt"
    sentinel.write_bytes(b"keep me")
    archive = _write_zip(tmp_path / "unsafe.zip", [*_valid_entries(), ("../escape.md", b"escape")])

    error = _error(importer, archive, destination)

    _assert_error(error, code="unsafe_path", path="../escape.md")
    assert sentinel.read_bytes() == b"keep me"
    assert not (destination / "official-pack").exists()


def test_existing_destination_is_not_overwritten_by_a_valid_archive(tmp_path: Path) -> None:
    importer = _importer()
    archive = _write_zip(tmp_path / "package.zip", _valid_entries())
    destination = tmp_path / "destination"
    destination.mkdir()
    sentinel = destination / "sentinel.txt"
    sentinel.write_bytes(b"keep me")

    error = _error(importer, archive, destination)

    _assert_error(error, code="destination_exists")
    assert sentinel.read_bytes() == b"keep me"


def test_destination_parent_replacement_during_import_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    importer = _importer()
    archive = _write_zip(tmp_path / "package.zip", _valid_entries())
    parent = tmp_path / "imports"
    parent.mkdir()
    old_parent = tmp_path / "imports-old"
    original_mkdtemp = importer.tempfile.mkdtemp

    def replacing_mkdtemp(*, prefix: str, dir: Path | str) -> str:
        os.rename(parent, old_parent)
        parent.mkdir()
        return original_mkdtemp(prefix=prefix, dir=dir)

    monkeypatch.setattr(importer.tempfile, "mkdtemp", replacing_mkdtemp)

    error = _error(importer, archive, parent / "destination")

    _assert_error(error, code="unsafe_destination")
    assert not (parent / "destination").exists()
    assert not list(parent.glob(".destination-*"))


def test_actual_bounded_read_detects_crc_failure_and_leaves_no_partial_destination(tmp_path: Path) -> None:
    importer = _importer()
    archive = _write_zip(tmp_path / "crc.zip", _valid_entries(), compression=ZIP_STORED)
    data = bytearray(archive.read_bytes())
    data[data.index(b"# Skill package") + 1] ^= 0xFF
    archive.write_bytes(data)
    destination = tmp_path / "destination"
    error = _error(importer, archive, destination)
    _assert_error(error, code="invalid_archive")
    assert not destination.exists()


def test_bad_zip_and_truncated_archive_fail_closed(tmp_path: Path) -> None:
    importer = _importer()
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    truncated = _write_zip(tmp_path / "truncated.zip", _valid_entries())
    truncated.write_bytes(truncated.read_bytes()[:-12])
    for archive in (bad, truncated):
        error = _error(importer, archive, tmp_path / f"destination-{archive.stem}")
        _assert_error(error, code="invalid_archive")


def test_missing_non_file_and_symlink_destination_fail_without_touching_external_target(tmp_path: Path) -> None:
    importer = _importer()
    archive = _write_zip(tmp_path / "package.zip", _valid_entries())
    missing = _error(importer, tmp_path / "missing.zip", tmp_path / "missing-destination")
    _assert_error(missing, code="invalid_archive_path")
    directory = tmp_path / "archive-directory"
    directory.mkdir()
    non_file = _error(importer, directory, tmp_path / "directory-destination")
    _assert_error(non_file, code="invalid_archive_path")

    archive_link = tmp_path / "archive-link.zip"
    archive_link.symlink_to(archive)
    linked_archive = _error(importer, archive_link, tmp_path / "linked-archive-destination")
    _assert_error(linked_archive, code="unsafe_archive_path")

    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("external", encoding="utf-8")
    destination_link = tmp_path / "destination-link"
    destination_link.symlink_to(external, target_is_directory=True)
    error = _error(importer, archive, destination_link)
    _assert_error(error, code="unsafe_destination")
    assert sentinel.read_text(encoding="utf-8") == "external"


def test_archive_descriptor_open_uses_nonblocking_flags_before_file_type_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    importer = _importer()
    archive = _write_zip(tmp_path / "package.zip", _valid_entries())
    captured_flags: dict[str, int] = {}

    def fake_open(path: Path, flags: int, mode: int = 0o777) -> int:
        captured_flags["flags"] = flags
        raise OSError(errno.ENXIO, "would block")

    monkeypatch.setattr(importer.os, "open", fake_open)

    error = _error(importer, archive, tmp_path / "destination")

    _assert_error(error, code="invalid_archive_path")
    if hasattr(os, "O_NONBLOCK"):
        assert captured_flags["flags"] & os.O_NONBLOCK


def test_limits_and_errors_are_frozen_and_expose_stable_error_fields(tmp_path: Path) -> None:
    importer = _importer()
    limits = _limits(importer)
    assert is_dataclass(limits) and getattr(type(limits), "__dataclass_params__").frozen
    archive = _write_zip(tmp_path / "unsafe.zip", [("../escape.md", b"x")])
    error = _error(importer, archive, tmp_path / "destination")
    assert isinstance(error, ValueError)
    _assert_error(error, code="unsafe_path", path="../escape.md")


@pytest.mark.parametrize(
    "workflow_path",
    [
        "official-pack/workflows/.md",
        "official-pack/workflows/..md",
        "official-pack/workflows/...md",
        "official-pack/workflows/foo bar.md",
    ],
)
def test_rejects_workflow_files_without_valid_source_identity_before_import(tmp_path: Path, workflow_path: str) -> None:
    importer = _importer()
    archive = _write_zip(tmp_path / "invalid-source-id.zip", [*_valid_entries(), (workflow_path, b"# invalid\n")])

    error = _error(importer, archive, tmp_path / "destination")

    _assert_error(error, code="invalid_scenario_id", path=workflow_path.removeprefix("official-pack/"))
    assert not (tmp_path / "destination").exists()


def test_utf8_workflow_file_stems_are_retained_as_source_identities(tmp_path: Path) -> None:
    importer = _importer()
    archive = _write_zip(tmp_path / "utf8-source-id.zip", [*_valid_entries(), ("official-pack/workflows/根因.md", "# 根因\n".encode())])

    result = importer.import_skill_package(archive, tmp_path / "destination")

    assert result.skill_sources[-1].source_scenario_id == "根因"
    assert result.skill_sources[-1].source_path == "workflows/根因.md"


def test_zip_names_round_trip_as_utf8_and_imported_inventory_retains_chinese_names(tmp_path: Path) -> None:
    importer = _importer()
    archive = _write_zip(tmp_path / "utf8.zip", _valid_entries(include_utf8=True))
    with ZipFile(archive) as source:
        chinese_infos = [info for info in source.infolist() if any(ord(character) > 127 for character in info.filename)]
    assert chinese_infos and all(info.flag_bits & 0x800 for info in chinese_infos)
    assert all(info.filename.encode("utf-8").decode("utf-8") == info.filename for info in chinese_infos)
    result = importer.import_skill_package(archive, tmp_path / "destination")
    relative_paths = [entry.relative_path for entry in result.inventory]
    assert "templates/开发给测试讲代码模板.md" in relative_paths
    assert "templates/流程讲解活文档模板.md" in relative_paths
    assert "templates/黑盒测试用例Markdown模板.md" in relative_paths


def test_optional_official_archive_import_matches_pinned_digest_inventory_and_five_scenarios(tmp_path: Path) -> None:
    archive_name = os.environ.get("CODETALKS_V24_ARCHIVE")
    if not archive_name:
        pytest.skip("set CODETALKS_V24_ARCHIVE to run the local official archive importer gate")
    archive = Path(archive_name)
    assert archive.is_file()
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == PINNED_ARCHIVE_SHA256
    importer = _importer()
    result = importer.import_skill_package(archive, tmp_path / "destination")
    source_inventory = json.loads(
        (Path(__file__).parent / "fixtures" / "skills" / "codetalks-v2.4" / "source-inventory.json").read_text(encoding="utf-8")
    )
    assert result.archive_digest == f"sha256:{PINNED_ARCHIVE_SHA256}"
    assert [entry.relative_path for entry in result.inventory] == source_inventory["files"]
    assert {
        "templates/开发给测试讲代码模板.md",
        "templates/流程讲解活文档模板.md",
        "templates/黑盒测试用例Markdown模板.md",
    }.issubset(entry.relative_path for entry in result.inventory)
    assert [source.source_scenario_id for source in result.skill_sources] == SCENARIOS
