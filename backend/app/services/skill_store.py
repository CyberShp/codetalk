"""Filesystem-authoritative Skill draft metadata store."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LOCK = threading.RLock()
_SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SkillStoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SkillProject:
    project_id: str
    name: str
    pack_id: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SkillDraft:
    draft_id: str
    project_id: str
    skill_id: str
    source_scenario_id: str
    filesystem_path: Path
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SkillDraftRescan:
    draft_id: str
    file_digests: dict[str, str]
    file_count: int


@dataclass(frozen=True)
class SkillBuild:
    build_id: str
    draft_id: str
    status: str
    version_id: str | None
    content_digest: str
    zip_digest: str
    build_root: Path
    zip_path: Path
    unpacked_root: Path
    ir_path: Path
    validation_report_path: Path
    file_digest_map_path: Path
    manifest_path: Path
    created_at: str
    updated_at: str


class SkillStore:
    """SQLite metadata for Skill Projects, Drafts, Builds, and Versions.

    Draft source bytes are authoritative on disk under ``data_dir``. The
    database deliberately stores only metadata and pointers to filesystem paths.
    """

    def __init__(self, db_path: str | Path, data_dir: str | Path) -> None:
        self.db_path = Path(db_path)
        self.data_dir = Path(data_dir)

    def initialize_and_migrate(self) -> dict[str, int]:
        with _LOCK, self._connect() as db:
            db.execute("PRAGMA foreign_keys = ON")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS skill_schema_meta (
                    component TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS skill_projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    pack_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS skill_drafts (
                    draft_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    source_scenario_id TEXT NOT NULL,
                    filesystem_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES skill_projects(project_id)
                );

                CREATE TABLE IF NOT EXISTS skill_builds (
                    build_id TEXT PRIMARY KEY,
                    draft_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version_id TEXT,
                    content_digest TEXT NOT NULL DEFAULT '',
                    zip_digest TEXT NOT NULL DEFAULT '',
                    build_root TEXT NOT NULL,
                    zip_path TEXT NOT NULL,
                    unpacked_root TEXT NOT NULL,
                    ir_path TEXT NOT NULL,
                    validation_report_path TEXT NOT NULL,
                    file_digest_map_path TEXT NOT NULL,
                    manifest_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(draft_id) REFERENCES skill_drafts(draft_id)
                );

                CREATE TABLE IF NOT EXISTS skill_versions (
                    version_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    draft_id TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    content_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES skill_projects(project_id),
                    FOREIGN KEY(draft_id) REFERENCES skill_drafts(draft_id)
                );

                CREATE INDEX IF NOT EXISTS idx_skill_drafts_project
                    ON skill_drafts(project_id);
                CREATE INDEX IF NOT EXISTS idx_skill_builds_draft
                    ON skill_builds(draft_id, updated_at DESC);
                """
            )
            db.execute(
                """
                INSERT INTO skill_schema_meta(component, version, updated_at)
                VALUES ('skill_store', ?, ?)
                ON CONFLICT(component) DO UPDATE SET
                    version = excluded.version,
                    updated_at = excluded.updated_at
                """,
                (_SCHEMA_VERSION, _now()),
            )
        return {"schema_version": _SCHEMA_VERSION}

    def create_project(
        self,
        *,
        name: str,
        pack_id: str = "",
        project_id: str | None = None,
    ) -> SkillProject:
        self.initialize_and_migrate()
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("project name is required")
        identifier = str(project_id or f"skill_project_{uuid.uuid4().hex}")
        now = _now()
        with _LOCK, self._connect() as db:
            db.execute(
                """
                INSERT INTO skill_projects(project_id, name, pack_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (identifier, clean_name, str(pack_id or "").strip(), now, now),
            )
        return self.get_project(identifier)

    def get_project(self, project_id: str) -> SkillProject:
        self.initialize_and_migrate()
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM skill_projects WHERE project_id = ?", (str(project_id),)
            ).fetchone()
        if row is None:
            raise KeyError(project_id)
        return _project_from_row(row)

    def create_draft_from_source(
        self,
        *,
        project_id: str,
        source_root: str | Path,
        source_scenario_id: str,
        skill_id: str,
        draft_id: str | None = None,
    ) -> SkillDraft:
        self.initialize_and_migrate()
        self.get_project(project_id)
        source_path = Path(source_root)
        self._validate_source_root(source_path)

        identifier = str(draft_id or f"skill_draft_{uuid.uuid4().hex}")
        draft_root = self.data_dir / "skills" / "drafts" / identifier
        filesystem_path = draft_root / "source"
        tmp_path = draft_root / f".source.tmp-{uuid.uuid4().hex}"
        now = _now()
        with _LOCK:
            if draft_root.exists():
                raise SkillStoreError("draft_exists", f"Draft already exists: {identifier}")
            try:
                draft_root.mkdir(parents=True, exist_ok=False)
                shutil.copytree(source_path, tmp_path)
                tmp_path.replace(filesystem_path)
                with self._connect() as db:
                    db.execute("PRAGMA foreign_keys = ON")
                    db.execute(
                        """
                        INSERT INTO skill_drafts(
                            draft_id, project_id, skill_id, source_scenario_id,
                            filesystem_path, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            identifier,
                            str(project_id),
                            str(skill_id or "").strip(),
                            str(source_scenario_id or "").strip(),
                            str(filesystem_path),
                            now,
                            now,
                        ),
                    )
            except Exception:
                shutil.rmtree(draft_root, ignore_errors=True)
                raise
        return self.get_draft(identifier)

    def get_draft(self, draft_id: str) -> SkillDraft:
        self.initialize_and_migrate()
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM skill_drafts WHERE draft_id = ?", (str(draft_id),)
            ).fetchone()
        if row is None:
            raise KeyError(draft_id)
        return _draft_from_row(row)

    def rescan_draft(self, draft_id: str) -> SkillDraftRescan:
        draft = self.get_draft(draft_id)
        file_digests = _digest_files(draft.filesystem_path)
        return SkillDraftRescan(
            draft_id=draft.draft_id,
            file_digests=file_digests,
            file_count=len(file_digests),
        )

    def register_build(
        self,
        *,
        draft_id: str,
        status: str,
        build_id: str | None = None,
        version_id: str | None = None,
        content_digest: str = "",
        zip_digest: str = "",
        build_root: str | Path | None = None,
        zip_path: str | Path | None = None,
        unpacked_root: str | Path | None = None,
        ir_path: str | Path | None = None,
        validation_report_path: str | Path | None = None,
        file_digest_map_path: str | Path | None = None,
        manifest_path: str | Path | None = None,
    ) -> SkillBuild:
        self.initialize_and_migrate()
        self.get_draft(draft_id)
        identifier = str(build_id or f"skill_build_{uuid.uuid4().hex}")
        paths = self._build_paths(
            identifier,
            build_root=build_root,
            zip_path=zip_path,
            unpacked_root=unpacked_root,
            ir_path=ir_path,
            validation_report_path=validation_report_path,
            file_digest_map_path=file_digest_map_path,
            manifest_path=manifest_path,
        )
        now = _now()
        with _LOCK, self._connect() as db:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute(
                """
                INSERT INTO skill_builds(
                    build_id, draft_id, status, version_id, content_digest, zip_digest,
                    build_root, zip_path, unpacked_root, ir_path, validation_report_path,
                    file_digest_map_path, manifest_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier,
                    str(draft_id),
                    str(status or "").strip(),
                    str(version_id).strip() if version_id else None,
                    str(content_digest or ""),
                    str(zip_digest or ""),
                    *(str(paths[name]) for name in _BUILD_PATH_FIELDS),
                    now,
                    now,
                ),
            )
        return self.get_build(identifier)

    def create_build(self, **kwargs: Any) -> SkillBuild:
        return self.register_build(**kwargs)

    def update_build(self, build_id: str, **changes: Any) -> SkillBuild:
        self.initialize_and_migrate()
        field_map = {
            "status": "status",
            "version_id": "version_id",
            "content_digest": "content_digest",
            "zip_digest": "zip_digest",
            "build_root": "build_root",
            "zip_path": "zip_path",
            "unpacked_root": "unpacked_root",
            "ir_path": "ir_path",
            "validation_report_path": "validation_report_path",
            "file_digest_map_path": "file_digest_map_path",
            "manifest_path": "manifest_path",
        }
        unknown = set(changes) - set(field_map)
        if unknown:
            raise ValueError(f"unknown build fields: {', '.join(sorted(unknown))}")
        if not changes:
            return self.get_build(build_id)
        assignments: list[str] = []
        params: list[Any] = []
        for key, value in changes.items():
            assignments.append(f"{field_map[key]} = ?")
            if key == "version_id":
                params.append(str(value).strip() if value else None)
            elif key.endswith("_path") or key == "build_root" or key == "unpacked_root":
                params.append(str(Path(value)))
            else:
                params.append(str(value or ""))
        assignments.append("updated_at = ?")
        params.extend([_now(), str(build_id)])
        with _LOCK, self._connect() as db:
            cursor = db.execute(
                f"UPDATE skill_builds SET {', '.join(assignments)} WHERE build_id = ?",
                params,
            )
            if cursor.rowcount != 1:
                raise KeyError(build_id)
        return self.get_build(build_id)

    def get_build(self, build_id: str) -> SkillBuild:
        self.initialize_and_migrate()
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM skill_builds WHERE build_id = ?", (str(build_id),)
            ).fetchone()
        if row is None:
            raise KeyError(build_id)
        return _build_from_row(row)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def _build_paths(
        self,
        build_id: str,
        *,
        build_root: str | Path | None,
        zip_path: str | Path | None,
        unpacked_root: str | Path | None,
        ir_path: str | Path | None,
        validation_report_path: str | Path | None,
        file_digest_map_path: str | Path | None,
        manifest_path: str | Path | None,
    ) -> dict[str, Path]:
        root = (
            Path(build_root)
            if build_root is not None
            else self.data_dir / "skills" / "builds" / build_id
        )
        return {
            "build_root": root,
            "zip_path": Path(zip_path) if zip_path is not None else root / "candidate.zip",
            "unpacked_root": Path(unpacked_root)
            if unpacked_root is not None
            else root / "unpacked",
            "ir_path": Path(ir_path)
            if ir_path is not None
            else root / "ir" / "skill-ir-v1.json",
            "validation_report_path": Path(validation_report_path)
            if validation_report_path is not None
            else root / "validation" / "validation-report.json",
            "file_digest_map_path": Path(file_digest_map_path)
            if file_digest_map_path is not None
            else root / "file-digests.json",
            "manifest_path": Path(manifest_path) if manifest_path is not None else root / "manifest.json",
        }

    @staticmethod
    def _validate_source_root(source_root: Path) -> None:
        if source_root.is_symlink() or not source_root.exists() or not source_root.is_dir():
            raise SkillStoreError(
                "unsafe_source_root",
                f"Source root must be a real directory: {source_root}",
            )
        for path in source_root.rglob("*"):
            if path.is_symlink():
                raise SkillStoreError(
                    "unsafe_source_root",
                    f"Source root contains an unsafe symlink: {path}",
                )


_BUILD_PATH_FIELDS = (
    "build_root",
    "zip_path",
    "unpacked_root",
    "ir_path",
    "validation_report_path",
    "file_digest_map_path",
    "manifest_path",
)


def _digest_files(root: Path) -> dict[str, str]:
    if not root.exists() or not root.is_dir():
        raise SkillStoreError("missing_draft_source", f"Draft source is missing: {root}")
    digests: dict[str, str] = {}
    files = sorted(
        (item for item in root.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(root).as_posix(),
    )
    for path in files:
        if path.is_symlink():
            raise SkillStoreError(
                "unsafe_draft_source",
                f"Draft source contains an unsafe symlink: {path}",
            )
        rel = path.relative_to(root).as_posix()
        digests[rel] = f"sha256:{_sha256_file(path)}"
    return digests


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_from_row(row: sqlite3.Row) -> SkillProject:
    return SkillProject(
        project_id=str(row["project_id"]),
        name=str(row["name"]),
        pack_id=str(row["pack_id"] or ""),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _draft_from_row(row: sqlite3.Row) -> SkillDraft:
    return SkillDraft(
        draft_id=str(row["draft_id"]),
        project_id=str(row["project_id"]),
        skill_id=str(row["skill_id"]),
        source_scenario_id=str(row["source_scenario_id"]),
        filesystem_path=Path(str(row["filesystem_path"])),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _build_from_row(row: sqlite3.Row) -> SkillBuild:
    return SkillBuild(
        build_id=str(row["build_id"]),
        draft_id=str(row["draft_id"]),
        status=str(row["status"]),
        version_id=str(row["version_id"]) if row["version_id"] else None,
        content_digest=str(row["content_digest"] or ""),
        zip_digest=str(row["zip_digest"] or ""),
        build_root=Path(str(row["build_root"])),
        zip_path=Path(str(row["zip_path"])),
        unpacked_root=Path(str(row["unpacked_root"])),
        ir_path=Path(str(row["ir_path"])),
        validation_report_path=Path(str(row["validation_report_path"])),
        file_digest_map_path=Path(str(row["file_digest_map_path"])),
        manifest_path=Path(str(row["manifest_path"])),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
