"""Deterministic Skill build candidates for F014 Task 5."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from app.services.skill_ir_compiler import compile_codetalks_v24_skill
from app.services.skill_package_validator import SkillPackageValidationError, SkillPackageValidationIssue


_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_FILE_MODE = 0o100644
_STORE_KWARGS = {
    "build_id",
    "draft_id",
    "status",
    "version_id",
    "content_digest",
    "zip_digest",
    "build_root",
    "zip_path",
    "unpacked_root",
    "ir_path",
    "validation_report_path",
    "file_digest_map_path",
    "manifest_path",
    "error_code",
    "error_message",
}


@dataclass(frozen=True)
class SkillBuild:
    build_id: str
    draft_id: str
    status: str
    version_id: str | None
    content_digest: str | None
    zip_digest: str | None
    zip_path: Path
    unpacked_root: Path
    ir_path: Path
    validation_report_path: Path
    file_digest_map_path: Path
    manifest_path: Path


class SkillBuildError(RuntimeError):
    def __init__(self, code: str, build_id: str, message: str | None = None) -> None:
        self.code = code
        self.build_id = build_id
        super().__init__(message or code)


class SkillBuildPipeline:
    def __init__(self, store: Any) -> None:
        self.store = store

    def build_candidate(self, draft_id: str) -> Any:
        draft = self.store.get_draft(draft_id)
        build_id = f"build_{uuid.uuid4().hex}"
        builds_root = self._builds_root(draft)
        final_root = builds_root / build_id
        tmp_root = builds_root / f".{build_id}.tmp-{uuid.uuid4().hex}"
        paths = _build_paths(final_root)

        builds_root.mkdir(parents=True, exist_ok=True)
        self._record_started(draft_id=draft_id, build_id=build_id, paths=paths)

        try:
            tmp_root.mkdir(parents=True, exist_ok=False)
            tmp_paths = _build_paths(tmp_root)
            ir = compile_codetalks_v24_skill(draft.filesystem_path, source_scenario_id=draft.source_scenario_id)
        except SkillPackageValidationError as exc:
            self._record_failed_build(
                build_id=build_id,
                draft_id=draft_id,
                tmp_root=tmp_root,
                final_root=final_root,
                paths=paths,
                code="validation_failed",
                message=str(exc),
                report=_validation_report(False, exc.issues),
            )
            raise SkillBuildError("validation_failed", build_id, str(exc)) from exc

        try:
            _copy_source_tree(Path(draft.filesystem_path), tmp_paths.unpacked_root)
            _write_json_atomic(tmp_paths.ir_path, ir)
            validation_report = _validation_report(True, ())
            file_digest_map = _file_digest_map(ir)
            content_digest = _json_digest(
                {
                    "schema_version": "skill-build-candidate-v1",
                    "skill_id": getattr(draft, "skill_id", ""),
                    "source_scenario_id": getattr(draft, "source_scenario_id", ""),
                    "ir_content_digest": ir["content_digest"],
                    "validation": validation_report,
                    "file_digest_map": file_digest_map,
                    "artifacts": {
                        "source": "source",
                        "ir": "ir/skill-ir-v1.json",
                        "validation_report": "validation/validation-report.json",
                        "file_digest_map": "file-digests.json",
                        "manifest": "manifest.json",
                        "candidate_zip": "candidate.zip",
                    },
                }
            )
            _write_json_atomic(tmp_paths.validation_report_path, validation_report)
            _write_json_atomic(tmp_paths.file_digest_map_path, file_digest_map)
            _write_json_atomic(
                tmp_paths.manifest_path,
                {
                    "schema_version": "skill-build-manifest-v1",
                    "status": "built",
                    "content_digest": content_digest,
                    "ir_content_digest": ir["content_digest"],
                    "review_required": True,
                    "version_id": None,
                    "artifacts": {
                        "source": "source",
                        "ir": "ir/skill-ir-v1.json",
                        "validation_report": "validation/validation-report.json",
                        "file_digest_map": "file-digests.json",
                    },
                },
            )
            _write_deterministic_zip(tmp_paths.zip_path, tmp_root)
            zip_digest = _sha256_path(tmp_paths.zip_path)
            _move_build_dir(tmp_root, final_root)
        except Exception as exc:
            self._record_failed_build(
                build_id=build_id,
                draft_id=draft_id,
                tmp_root=tmp_root,
                final_root=final_root,
                paths=paths,
                code="build_failed",
                message=str(exc),
                report=_validation_report(False, (SkillPackageValidationIssue("build_failed", "build", str(exc)),)),
            )
            raise SkillBuildError("build_failed", build_id, str(exc)) from exc

        built = _skill_build(
            build_id=build_id,
            draft_id=draft_id,
            status="built",
            content_digest=content_digest,
            zip_digest=zip_digest,
            paths=paths,
        )
        recorded = self._record_succeeded(build=built)
        return recorded if recorded is not None else built

    def _record_failed_build(
        self,
        *,
        build_id: str,
        draft_id: str,
        tmp_root: Path,
        final_root: Path,
        paths: SkillBuild,
        code: str,
        message: str,
        report: dict[str, Any],
    ) -> None:
        shutil.rmtree(tmp_root, ignore_errors=True)
        shutil.rmtree(final_root, ignore_errors=True)
        tmp_root.mkdir(parents=True, exist_ok=False)
        tmp_paths = _build_paths(tmp_root)
        _write_json_atomic(tmp_paths.validation_report_path, report)
        _move_build_dir(tmp_root, final_root)
        failed = _skill_build(
            build_id=build_id,
            draft_id=draft_id,
            status="failed",
            content_digest=None,
            zip_digest=None,
            paths=paths,
        )
        self._record_failed(build=failed, error_code=code, error_message=message)

    def _builds_root(self, draft: Any) -> Path:
        data_dir = getattr(self.store, "data_dir", None)
        if data_dir is not None:
            return Path(data_dir) / "skills" / "builds"
        source_path = Path(draft.filesystem_path)
        try:
            return source_path.parents[2] / "builds"
        except IndexError:
            db_path = getattr(self.store, "db_path", None)
            if db_path is not None:
                return Path(db_path).parent / "data" / "skills" / "builds"
            raise

    def _record_started(self, *, draft_id: str, build_id: str, paths: SkillBuild) -> Any:
        build = _skill_build(
            build_id=build_id,
            draft_id=draft_id,
            status="building",
            content_digest=None,
            zip_digest=None,
            paths=paths,
        )
        result = _call_first_present(
            self.store,
            ("record_build_started", "start_build", "create_build", "record_skill_build_started"),
            _record_values(build),
        )
        if result is None:
            self._sqlite_insert_or_update(build)
        return result

    def _record_succeeded(self, *, build: SkillBuild) -> Any:
        values = _record_values(build)
        result = _call_first_present(
            self.store,
            ("record_build_succeeded", "succeed_build", "mark_build_succeeded", "record_skill_build_succeeded"),
            values,
        )
        if result is None:
            result = self._update_build(build)
        if result is None:
            self._sqlite_insert_or_update(build)
        return result

    def _record_failed(self, *, build: SkillBuild, error_code: str, error_message: str) -> Any:
        values = _record_values(build)
        values.update({"error_code": error_code, "error_message": error_message})
        result = _call_first_present(
            self.store,
            ("record_build_failed", "fail_build", "mark_build_failed", "record_skill_build_failed"),
            values,
        )
        if result is None:
            result = self._update_build(build)
        if result is None:
            self._sqlite_insert_or_update(build, error_code=error_code, error_message=error_message)
        return result

    def _update_build(self, build: SkillBuild) -> Any:
        method = getattr(self.store, "update_build", None)
        if not callable(method):
            return None
        return method(
            build.build_id,
            status=build.status,
            version_id=build.version_id,
            content_digest=build.content_digest or "",
            zip_digest=build.zip_digest or "",
            build_root=build.manifest_path.parent,
            zip_path=build.zip_path,
            unpacked_root=build.unpacked_root,
            ir_path=build.ir_path,
            validation_report_path=build.validation_report_path,
            file_digest_map_path=build.file_digest_map_path,
            manifest_path=build.manifest_path,
        )

    def _sqlite_insert_or_update(self, build: SkillBuild, *, error_code: str | None = None, error_message: str | None = None) -> None:
        db_path = getattr(self.store, "db_path", None)
        if db_path is None:
            return
        try:
            with sqlite3.connect(db_path) as db:
                columns = {row[1] for row in db.execute("PRAGMA table_info(skill_builds)").fetchall()}
                if not columns:
                    return
                values = _record_values(build)
                values.update(
                    {
                        "zip_path": str(build.zip_path),
                        "build_root": str(build.manifest_path.parent),
                        "unpacked_root": str(build.unpacked_root),
                        "ir_path": str(build.ir_path),
                        "validation_report_path": str(build.validation_report_path),
                        "file_digest_map_path": str(build.file_digest_map_path),
                        "manifest_path": str(build.manifest_path),
                        "error_code": error_code,
                        "error_message": error_message,
                    }
                )
                now = datetime.now(timezone.utc).isoformat()
                for name in ("created_at", "updated_at", "started_at"):
                    values.setdefault(name, now)
                if build.status in {"built", "failed"}:
                    values.setdefault("completed_at", now)
                row_exists = bool(db.execute("SELECT 1 FROM skill_builds WHERE build_id = ?", (build.build_id,)).fetchone())
                writable = [column for column in columns if column in values]
                if row_exists:
                    update_columns = [column for column in writable if column != "build_id"]
                    if update_columns:
                        assignments = ", ".join(f"{column} = ?" for column in update_columns)
                        db.execute(
                            f"UPDATE skill_builds SET {assignments} WHERE build_id = ?",
                            [values[column] for column in update_columns] + [build.build_id],
                        )
                elif writable:
                    placeholders = ", ".join("?" for _ in writable)
                    db.execute(
                        f"INSERT INTO skill_builds ({', '.join(writable)}) VALUES ({placeholders})",
                        [values[column] for column in writable],
                    )
        except sqlite3.Error:
            return


def _build_paths(root: Path) -> SkillBuild:
    return SkillBuild(
        build_id=root.name,
        draft_id="",
        status="",
        version_id=None,
        content_digest=None,
        zip_digest=None,
        zip_path=root / "candidate.zip",
        unpacked_root=root / "source",
        ir_path=root / "ir" / "skill-ir-v1.json",
        validation_report_path=root / "validation" / "validation-report.json",
        file_digest_map_path=root / "file-digests.json",
        manifest_path=root / "manifest.json",
    )


def _skill_build(
    *,
    build_id: str,
    draft_id: str,
    status: str,
    content_digest: str | None,
    zip_digest: str | None,
    paths: SkillBuild,
) -> SkillBuild:
    return SkillBuild(
        build_id=build_id,
        draft_id=draft_id,
        status=status,
        version_id=None,
        content_digest=content_digest,
        zip_digest=zip_digest,
        zip_path=paths.zip_path,
        unpacked_root=paths.unpacked_root,
        ir_path=paths.ir_path,
        validation_report_path=paths.validation_report_path,
        file_digest_map_path=paths.file_digest_map_path,
        manifest_path=paths.manifest_path,
    )


def _call_first_present(store: Any, names: Iterable[str], values: dict[str, Any]) -> Any:
    for name in names:
        method = getattr(store, name, None)
        if callable(method):
            return _call_with_supported_arguments(method, values)
    return None


def _call_with_supported_arguments(method: Callable[..., Any], values: dict[str, Any]) -> Any:
    signature = inspect.signature(method)
    parameters = list(signature.parameters.values())
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters):
        return method(**{key: value for key, value in values.items() if key in _STORE_KWARGS})
    if len(parameters) == 1 and parameters[0].name not in values:
        return method(values["build"])
    kwargs = {
        parameter.name: values[parameter.name]
        for parameter in parameters
        if parameter.name in values and parameter.kind in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
    }
    return method(**kwargs)


def _record_values(build: SkillBuild) -> dict[str, Any]:
    return {
        "build": build,
        "build_id": build.build_id,
        "draft_id": build.draft_id,
        "status": build.status,
        "version_id": build.version_id,
        "content_digest": build.content_digest or "",
        "zip_digest": build.zip_digest or "",
        "build_root": build.manifest_path.parent,
        "zip_path": build.zip_path,
        "build_dir": build.manifest_path.parent,
        "build_path": build.manifest_path.parent,
        "unpacked_root": build.unpacked_root,
        "ir_path": build.ir_path,
        "validation_report_path": build.validation_report_path,
        "file_digest_map_path": build.file_digest_map_path,
        "manifest_path": build.manifest_path,
    }


def _validation_report(ok: bool, issues: Iterable[SkillPackageValidationIssue]) -> dict[str, Any]:
    return {
        "ok": ok,
        "issues": [{"code": issue.code, "path": issue.path, "message": issue.message} for issue in issues],
    }


def _file_digest_map(ir: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "skill-file-digests-v1",
        "files": {row["path"]: row["digest"] for row in ir.get("source_file_digests", [])},
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _copy_source_tree(source_root: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source_root, destination, symlinks=False)


def _write_deterministic_zip(zip_path: Path, root: Path) -> None:
    members = [path for path in root.rglob("*") if path.is_file() and path != zip_path]
    members.sort(key=lambda path: path.relative_to(root).as_posix())
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in members:
            name = path.relative_to(root).as_posix()
            info = ZipInfo(name, date_time=_ZIP_TIMESTAMP)
            info.compress_type = ZIP_DEFLATED
            info.external_attr = _FILE_MODE << 16
            info.create_system = 3
            archive.writestr(info, path.read_bytes())


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _json_digest(payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _move_build_dir(source: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(destination)
    os.replace(source, destination)
