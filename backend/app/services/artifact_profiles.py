"""Versioned local output artifact profiles for Workbench runs."""

from __future__ import annotations

import json
import csv
import hashlib
import io
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


class ArtifactProfileValidationError(ValueError):
    pass


class ArtifactProfileConflictError(RuntimeError):
    pass


class ArtifactProfileNotFoundError(KeyError):
    pass


_ARTIFACT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ALLOWED_FORMATS = {"markdown", "json", "csv", "xlsx", "text"}
_RESERVED_FILENAMES = {
    "manifest.json",
    "artifact_validation.json",
    "deliverables.zip",
    "output_contract.json",
}
_UNSAFE_TRUE_KEYS = {
    "allow_unverified_evidence",
    "allow_external_paths",
    "skip_manifest",
    "skip_path_validation",
    "disable_evidence_validation",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_profile_id() -> str:
    return f"apro_{uuid.uuid4().hex}"


class ArtifactProfileStore:
    """SQLite store whose profile versions are immutable after creation."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS artifact_profiles (
                    profile_id TEXT PRIMARY KEY,
                    current_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifact_profile_versions (
                    profile_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    restored_from_version INTEGER,
                    PRIMARY KEY (profile_id, version),
                    FOREIGN KEY (profile_id) REFERENCES artifact_profiles(profile_id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS artifact_workspace_bindings (
                    workspace_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (profile_id) REFERENCES artifact_profiles(profile_id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS artifact_feature_bindings (
                    feature_tag TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (profile_id) REFERENCES artifact_profiles(profile_id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS artifact_profile_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def create_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = _normalize_profile(payload)
        self.initialize()
        profile_id = str(payload.get("id") or _new_profile_id()).strip()
        if not _ARTIFACT_ID_RE.fullmatch(profile_id.replace("apro_", "a", 1)):
            raise ArtifactProfileValidationError("profile id is invalid")
        now = _now()
        with self._connect() as db:
            if db.execute(
                "SELECT 1 FROM artifact_profiles WHERE profile_id = ?", (profile_id,)
            ).fetchone():
                raise ArtifactProfileConflictError(f"artifact profile already exists: {profile_id}")
            db.execute(
                "INSERT INTO artifact_profiles VALUES (?, 1, ?, ?)",
                (profile_id, now, now),
            )
            self._insert_version(
                db,
                profile_id=profile_id,
                version=1,
                payload=normalized,
                created_at=now,
            )
        return self.get_profile(profile_id)

    def update_profile(
        self,
        profile_id: str,
        payload: dict[str, Any],
        *,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        normalized = _normalize_profile(payload)
        self.initialize()
        now = _now()
        with self._connect() as db:
            row = db.execute(
                "SELECT current_version FROM artifact_profiles WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
            if row is None:
                raise ArtifactProfileNotFoundError(profile_id)
            current = int(row["current_version"])
            if expected_version is not None and expected_version != current:
                raise ArtifactProfileConflictError(
                    f"artifact profile current version is {current}; expected {expected_version}"
                )
            new_version = current + 1
            self._insert_version(
                db,
                profile_id=profile_id,
                version=new_version,
                payload=normalized,
                created_at=now,
            )
            db.execute(
                "UPDATE artifact_profiles SET current_version = ?, updated_at = ? WHERE profile_id = ?",
                (new_version, now, profile_id),
            )
        return self.get_profile(profile_id)

    def restore_version(self, profile_id: str, *, version: int) -> dict[str, Any]:
        source = self.get_profile(profile_id, version=version)
        payload = {key: value for key, value in source.items() if key not in _SYSTEM_FIELDS}
        self.initialize()
        now = _now()
        with self._connect() as db:
            row = db.execute(
                "SELECT current_version FROM artifact_profiles WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
            if row is None:
                raise ArtifactProfileNotFoundError(profile_id)
            new_version = int(row["current_version"]) + 1
            self._insert_version(
                db,
                profile_id=profile_id,
                version=new_version,
                payload=payload,
                created_at=now,
                restored_from_version=version,
            )
            db.execute(
                "UPDATE artifact_profiles SET current_version = ?, updated_at = ? WHERE profile_id = ?",
                (new_version, now, profile_id),
            )
        return self.get_profile(profile_id)

    def get_profile(self, profile_id: str, *, version: int | None = None) -> dict[str, Any]:
        self.initialize()
        with self._connect() as db:
            profile = db.execute(
                "SELECT * FROM artifact_profiles WHERE profile_id = ?", (profile_id,)
            ).fetchone()
            if profile is None:
                raise ArtifactProfileNotFoundError(profile_id)
            selected_version = int(version or profile["current_version"])
            row = db.execute(
                """
                SELECT * FROM artifact_profile_versions
                WHERE profile_id = ? AND version = ?
                """,
                (profile_id, selected_version),
            ).fetchone()
            if row is None:
                raise ArtifactProfileNotFoundError(f"{profile_id}@{selected_version}")
        return _version_row_to_profile(row)

    def list_profiles(self) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as db:
            rows = db.execute(
                "SELECT profile_id FROM artifact_profiles ORDER BY updated_at DESC, profile_id"
            ).fetchall()
        return [self.get_profile(str(row["profile_id"])) for row in rows]

    def list_versions(self, profile_id: str) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT * FROM artifact_profile_versions
                WHERE profile_id = ? ORDER BY version DESC
                """,
                (profile_id,),
            ).fetchall()
        if not rows:
            raise ArtifactProfileNotFoundError(profile_id)
        return [_version_row_to_profile(row) for row in rows]

    def bind_workspace(self, workspace_id: str, profile_id: str) -> None:
        normalized_key = _required_binding_key(workspace_id, "workspace id")
        self._require_profile(profile_id)
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO artifact_workspace_bindings VALUES (?, ?, ?)
                ON CONFLICT(workspace_id) DO UPDATE SET
                    profile_id = excluded.profile_id,
                    updated_at = excluded.updated_at
                """,
                (normalized_key, profile_id, _now()),
            )

    def bind_feature_tag(self, feature_tag: str, profile_id: str) -> None:
        normalized_key = _required_binding_key(feature_tag, "feature tag").casefold()
        self._require_profile(profile_id)
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO artifact_feature_bindings VALUES (?, ?, ?)
                ON CONFLICT(feature_tag) DO UPDATE SET
                    profile_id = excluded.profile_id,
                    updated_at = excluded.updated_at
                """,
                (normalized_key, profile_id, _now()),
            )

    def set_user_default(self, profile_id: str) -> None:
        self._require_profile(profile_id)
        self.initialize()
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO artifact_profile_settings VALUES ('user_default', ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value,
                    updated_at = excluded.updated_at
                """,
                (profile_id, _now()),
            )

    def clear_user_default(self) -> None:
        self.initialize()
        with self._connect() as db:
            db.execute("DELETE FROM artifact_profile_settings WHERE setting_key = 'user_default'")

    def resolve_profile(
        self,
        *,
        selected_profile_id: str = "",
        workspace_id: str = "",
        feature_tags: Iterable[str] = (),
        builtin_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.initialize()
        if selected_profile_id:
            return {
                "source": "run_selection",
                "profile": self.get_profile(selected_profile_id),
            }
        with self._connect() as db:
            if workspace_id:
                row = db.execute(
                    "SELECT profile_id FROM artifact_workspace_bindings WHERE workspace_id = ?",
                    (workspace_id.strip(),),
                ).fetchone()
                if row:
                    return {
                        "source": "workspace_binding",
                        "profile": self.get_profile(str(row["profile_id"])),
                    }
            for raw_tag in feature_tags:
                tag = str(raw_tag).strip().casefold()
                if not tag:
                    continue
                row = db.execute(
                    "SELECT profile_id FROM artifact_feature_bindings WHERE feature_tag = ?",
                    (tag,),
                ).fetchone()
                if row:
                    return {
                        "source": f"feature_tag:{tag}",
                        "profile": self.get_profile(str(row["profile_id"])),
                    }
            row = db.execute(
                "SELECT setting_value FROM artifact_profile_settings WHERE setting_key = 'user_default'"
            ).fetchone()
        if row:
            return {
                "source": "user_default",
                "profile": self.get_profile(str(row["setting_value"])),
            }
        if builtin_profile is not None:
            _normalize_profile(builtin_profile)
            return {"source": "builtin_default", "profile": dict(builtin_profile)}
        return {"source": "none", "profile": None}

    def _require_profile(self, profile_id: str) -> None:
        self.get_profile(profile_id)

    def _insert_version(
        self,
        db: sqlite3.Connection,
        *,
        profile_id: str,
        version: int,
        payload: dict[str, Any],
        created_at: str,
        restored_from_version: int | None = None,
    ) -> None:
        db.execute(
            """
            INSERT INTO artifact_profile_versions
                (profile_id, version, payload_json, created_at, restored_from_version)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                version,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                created_at,
                restored_from_version,
            ),
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


_SYSTEM_FIELDS = {
    "id",
    "version",
    "created_at",
    "restored_from_version",
}


def _version_row_to_profile(row: sqlite3.Row) -> dict[str, Any]:
    payload = json.loads(str(row["payload_json"]))
    return {
        "id": str(row["profile_id"]),
        "version": int(row["version"]),
        **payload,
        "created_at": str(row["created_at"]),
        "restored_from_version": row["restored_from_version"],
    }


def _normalize_profile(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ArtifactProfileValidationError("artifact profile must be an object")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ArtifactProfileValidationError("artifact profile name is required")
    _reject_unsafe_safety_override(payload)
    raw_artifacts = payload.get("artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise ArtifactProfileValidationError("artifact profile artifacts must be a non-empty list")
    artifacts: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_filenames: set[str] = set()
    for raw in raw_artifacts:
        if not isinstance(raw, dict):
            raise ArtifactProfileValidationError("artifact definition must be an object")
        artifact_id = str(raw.get("id") or "").strip()
        if not _ARTIFACT_ID_RE.fullmatch(artifact_id):
            raise ArtifactProfileValidationError(f"artifact id is invalid: {artifact_id or '<empty>'}")
        if artifact_id in seen_ids:
            raise ArtifactProfileValidationError(f"duplicate artifact id: {artifact_id}")
        seen_ids.add(artifact_id)
        filename = _safe_filename(raw.get("filename"))
        filename_key = filename.casefold()
        if filename_key in seen_filenames:
            raise ArtifactProfileValidationError(f"duplicate artifact filename: {filename}")
        if filename_key in _RESERVED_FILENAMES:
            raise ArtifactProfileValidationError(f"artifact filename is reserved: {filename}")
        seen_filenames.add(filename_key)
        artifact_format = str(raw.get("format") or "").strip().casefold()
        if artifact_format not in _ALLOWED_FORMATS:
            raise ArtifactProfileValidationError(
                f"unsupported artifact format for {artifact_id}: {artifact_format or '<empty>'}"
            )
        normalized = dict(raw)
        normalized.update(
            {
                "id": artifact_id,
                "filename": filename,
                "format": artifact_format,
                "required": bool(raw.get("required", False)),
            }
        )
        artifacts.append(normalized)
    scope = payload.get("scope") or {}
    if not isinstance(scope, dict):
        raise ArtifactProfileValidationError("artifact profile scope must be an object")
    return {
        "name": name,
        "description": str(payload.get("description") or "").strip(),
        "scope": dict(scope),
        "artifacts": artifacts,
    }


def _safe_filename(value: Any) -> str:
    filename = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(filename)
    if (
        not filename
        or path.is_absolute()
        or ":" in filename
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ArtifactProfileValidationError(
            f"artifact filename must be workspace-relative: {filename or '<empty>'}"
        )
    return path.as_posix()


def _reject_unsafe_safety_override(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in _UNSAFE_TRUE_KEYS and item is True:
                raise ArtifactProfileValidationError(
                    f"artifact profile cannot weaken global safety: {key}"
                )
            _reject_unsafe_safety_override(item)
    elif isinstance(value, list):
        for item in value:
            _reject_unsafe_safety_override(item)


def _required_binding_key(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ArtifactProfileValidationError(f"{label} is required")
    return normalized


def validate_profile_artifacts(
    artifact_root: str | Path,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Validate materialized files without executing profile-provided code."""

    normalized = _normalize_profile(profile)
    root = Path(artifact_root).resolve()
    results: list[dict[str, Any]] = []
    accepted = True
    for artifact in normalized["artifacts"]:
        target = root / artifact["filename"]
        required = bool(artifact["required"])
        result: dict[str, Any] = {
            "id": artifact["id"],
            "filename": artifact["filename"],
            "required": required,
            "status": "accepted",
            "errors": [],
        }
        resolved = target.resolve(strict=False)
        if not resolved.is_relative_to(root):
            result["status"] = "rejected"
            result["errors"].append("artifact path leaves the artifact root")
        elif not target.exists():
            result["status"] = "missing" if required else "optional_missing"
            if required:
                result["errors"].append("required artifact is missing")
        elif not target.is_file():
            result["status"] = "rejected"
            result["errors"].append("artifact is not a regular file")
        else:
            data = target.read_bytes()
            result["size"] = len(data)
            result["sha256"] = hashlib.sha256(data).hexdigest()
            if not data:
                result["errors"].append("artifact is empty")
            else:
                result["errors"].extend(_validate_artifact_content(data, artifact))
            if result["errors"]:
                result["status"] = "rejected"
        if result["status"] in {"missing", "rejected"}:
            accepted = False
        results.append(result)
    return {
        "accepted": accepted,
        "profile_id": str(profile.get("id") or ""),
        "profile_version": int(profile.get("version") or 0),
        "artifacts": results,
    }


def write_output_contract_snapshot(
    task_dir: str | Path,
    *,
    task_run_id: str,
    resolution: dict[str, Any],
) -> dict[str, Any]:
    profile = resolution.get("profile")
    if not isinstance(profile, dict):
        raise ArtifactProfileValidationError("resolved artifact profile is required")
    normalized = _normalize_profile(profile)
    payload = {
        "schema_version": 1,
        "task_run_id": str(task_run_id),
        "resolution_source": str(resolution.get("source") or ""),
        "profile_id": str(profile.get("id") or ""),
        "profile_version": int(profile.get("version") or 0),
        "name": normalized["name"],
        "description": normalized["description"],
        "artifacts": normalized["artifacts"],
        "safety": {
            "evidence_validation_required": True,
            "manifest_required": True,
            "workspace_relative_paths_required": True,
        },
        "created_at": _now(),
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    snapshot = {**payload, "sha256": digest}
    root = Path(task_dir)
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / ".output_contract.json.tmp"
    temporary.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(root / "output_contract.json")
    return snapshot


def apply_artifact_profile_to_task_bundle(
    task_bundle: dict[str, Any],
    output_contract: dict[str, Any],
) -> dict[str, Any]:
    result = dict(task_bundle)
    result["artifact_profile"] = dict(output_contract)
    return result


def _validate_artifact_content(data: bytes, artifact: dict[str, Any]) -> list[str]:
    artifact_format = artifact["format"]
    schema = artifact.get("schema") or {}
    if not isinstance(schema, dict):
        return ["artifact schema must be an object"]
    if artifact_format == "json":
        try:
            value = json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return [f"invalid JSON: {exc}"]
        required_keys = _string_values(schema.get("required_keys"))
        if required_keys:
            if not isinstance(value, dict):
                return ["JSON artifact must be an object"]
            missing = [key for key in required_keys if key not in value]
            if missing:
                return [f"missing JSON keys: {', '.join(missing)}"]
    elif artifact_format == "markdown":
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            return [f"invalid UTF-8 Markdown: {exc}"]
        headings = {
            match.group(1).strip().casefold()
            for match in re.finditer(r"^#{1,6}\s+(.+?)\s*$", text, re.MULTILINE)
        }
        required_sections = _string_values(schema.get("required_sections"))
        missing = [section for section in required_sections if section.casefold() not in headings]
        if missing:
            return [f"missing Markdown sections: {', '.join(missing)}"]
    elif artifact_format == "csv":
        try:
            reader = csv.reader(io.StringIO(data.decode("utf-8-sig")))
            header = next(reader, [])
        except UnicodeDecodeError as exc:
            return [f"invalid UTF-8 CSV: {exc}"]
        required_columns = _string_values(schema.get("required_columns"))
        missing = [column for column in required_columns if column not in header]
        if missing:
            return [f"missing CSV columns: {', '.join(missing)}"]
    return []


def _string_values(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
