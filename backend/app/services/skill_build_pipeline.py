"""Deterministic Skill build candidates for F014 Task 5."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from app.services.skill_ir_compiler import compile_codetalks_v24_skill
from app.services.skill_package_validator import (
    SCHEMA_DIR,
    SkillPackageValidationError,
    SkillPackageValidationIssue,
)


_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_FILE_MODE = 0o100644
_PUBLISH_LOCK = threading.RLock()
_PUBLISH_LOCK_STALE_SECONDS = 300
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


@dataclass(frozen=True)
class SkillPublishedVersion:
    version_id: str
    build_id: str
    draft_id: str
    skill_id: str
    content_digest: str
    review_evidence_digest: str
    version_root: Path
    source_zip_path: Path
    unpacked_root: Path
    ir_path: Path
    validation_report_path: Path
    review_records_path: Path
    manifest_path: Path


class SkillBuildError(RuntimeError):
    def __init__(self, code: str, build_id: str, message: str | None = None) -> None:
        self.code = code
        self.build_id = build_id
        super().__init__(message or code)


class SkillPublicationError(RuntimeError):
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

    def publish_build(self, build_id: str) -> Any:
        with _PUBLISH_LOCK:
            build = self.store.get_build(str(build_id))
            if getattr(build, "status", None) != "built":
                raise SkillPublicationError("build_not_publishable", str(build_id))
            if getattr(build, "version_id", None):
                method = getattr(self.store, "get_version", None)
                if callable(method):
                    return method(build.version_id)
                raise SkillPublicationError("already_published", str(build_id))
            full_review = self._required_full_review(str(build_id))
            if full_review is None:
                raise SkillPublicationError("full_review_required", str(build_id))
            draft = self.store.get_draft(build.draft_id)
            version_id = f"skill_version_{build.build_id}"
            versions_root = self._versions_root()
            final_root = versions_root / version_id
            tmp_root = versions_root / f".{version_id}.tmp-{uuid.uuid4().hex}"
            lock_root = versions_root / f".{version_id}.lock"
            paths = _version_paths(version_id, final_root)
            versions_root.mkdir(parents=True, exist_ok=True)
            _acquire_publish_lock(lock_root, version_id, build_id=str(build_id))

            try:
                try:
                    tmp_root.mkdir(parents=True, exist_ok=False)
                    tmp_paths = _version_paths(version_id, tmp_root)
                    _copy_source_tree(Path(build.unpacked_root), tmp_paths.unpacked_root)
                    _copy_file(build.ir_path, tmp_paths.ir_path)
                    _copy_file(build.validation_report_path, tmp_paths.validation_report_path)
                    reviews = self._review_records(str(build_id), content_digest=str(build.content_digest))
                    _write_json_atomic(tmp_paths.review_records_path, reviews)
                    _write_deterministic_zip(tmp_paths.source_zip_path, tmp_paths.unpacked_root)
                    manifest = _version_manifest(
                        version_id=version_id,
                        build=build,
                        draft=draft,
                        content_digest=str(build.content_digest),
                        review_evidence_digest=str(full_review.review_evidence_digest),
                        reviews=reviews,
                    )
                    _write_json_atomic(tmp_paths.manifest_path, manifest)
                    _make_tree_contents_read_only(tmp_root)
                    self._verify_release_bundle(
                        paths=tmp_paths,
                        build=build,
                        draft=draft,
                        reviews=reviews,
                        manifest=manifest,
                    )
                except Exception:
                    _remove_tree(tmp_root)
                    raise

                published = SkillPublishedVersion(
                    version_id=version_id,
                    build_id=str(build_id),
                    draft_id=str(build.draft_id),
                    skill_id=str(getattr(draft, "skill_id", "")),
                    content_digest=str(build.content_digest),
                    review_evidence_digest=str(full_review.review_evidence_digest),
                    version_root=paths.version_root,
                    source_zip_path=paths.source_zip_path,
                    unpacked_root=paths.unpacked_root,
                    ir_path=paths.ir_path,
                    validation_report_path=paths.validation_report_path,
                    review_records_path=paths.review_records_path,
                    manifest_path=paths.manifest_path,
                )
                moved_final = False
                metadata_recorded = False
                try:
                    method = getattr(self.store, "get_version", None)
                    if final_root.exists() and callable(method):
                        try:
                            existing = method(version_id)
                        except KeyError:
                            recovered = self._recover_existing_final_version(
                                version_id=version_id,
                                build=build,
                                draft=draft,
                                final_root=final_root,
                            )
                            _remove_tree(tmp_root)
                            return recovered
                        _remove_tree(tmp_root)
                        return existing
                    _move_build_dir(tmp_root, final_root)
                    moved_final = True
                    _make_tree_read_only(final_root)
                    self._verify_release_bundle(
                        paths=paths,
                        build=build,
                        draft=draft,
                        reviews=reviews,
                        manifest=manifest,
                    )
                    recorded = self._record_published_version(published, draft)
                    metadata_recorded = True
                    self._verify_release_bundle(
                        paths=paths,
                        build=build,
                        draft=draft,
                        reviews=reviews,
                        manifest=manifest,
                    )
                    recorded_version_id = getattr(recorded, "version_id", version_id)
                    if recorded_version_id != version_id:
                        _remove_tree(final_root)
                        return method(recorded_version_id) if callable(method) else recorded
                except Exception:
                    if moved_final:
                        _remove_tree(final_root)
                    _remove_tree(tmp_root)
                    rollback = getattr(self.store, "delete_version_metadata", None)
                    if callable(rollback) and metadata_recorded:
                        rollback(version_id=version_id, build_id=str(build_id))
                    raise
                if callable(method):
                    return method(version_id)
                return recorded if recorded is not None else published
            finally:
                _remove_tree(lock_root)

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

    def _versions_root(self) -> Path:
        data_dir = getattr(self.store, "data_dir", None)
        if data_dir is not None:
            return Path(data_dir) / "skills" / "versions"
        db_path = getattr(self.store, "db_path", None)
        if db_path is not None:
            return Path(db_path).parent / "data" / "skills" / "versions"
        raise SkillPublicationError("missing_store_data_dir", "")

    def _required_full_review(self, build_id: str) -> Any | None:
        method = getattr(self.store, "required_full_review_for_build", None)
        if callable(method):
            return method(build_id)
        return None

    def _verify_candidate_digest_from_paths(
        self,
        build: Any,
        draft: Any,
        *,
        source_root: Path,
        ir_path: Path,
        validation_report_path: Path,
    ) -> None:
        ir = json.loads(Path(ir_path).read_text(encoding="utf-8"))
        ir_digest = _ir_content_digest(ir)
        if ir.get("content_digest") != ir_digest:
            raise SkillPublicationError("ir_digest_mismatch", str(build.build_id))
        validation_report = json.loads(Path(validation_report_path).read_text(encoding="utf-8"))
        file_digest_map = _source_file_digest_map(source_root)
        expected = _json_digest(
            {
                "schema_version": "skill-build-candidate-v1",
                "skill_id": getattr(draft, "skill_id", ""),
                "source_scenario_id": getattr(draft, "source_scenario_id", ""),
                "ir_content_digest": ir_digest,
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
        if expected != build.content_digest:
            raise SkillPublicationError("candidate_digest_mismatch", str(build.build_id))

    def _review_records(self, build_id: str, *, content_digest: str) -> list[dict[str, Any]]:
        method = getattr(self.store, "list_reviews_for_build", None)
        if not callable(method):
            return []
        records = []
        for review in method(build_id):
            document = json.loads(Path(review.record_path).read_text(encoding="utf-8"))
            _validate_review_document(document)
            if document["content_digest"] != content_digest or document["content_digest"] != review.content_digest:
                raise SkillPublicationError("review_content_digest_mismatch", build_id)
            evidence_digest = _json_digest(document["review_evidence"])
            if evidence_digest != document["review_evidence_digest"] or evidence_digest != review.review_evidence_digest:
                raise SkillPublicationError("review_evidence_digest_mismatch", build_id)
            records.append(
                {
                    "review_id": document["review_id"],
                    "content_digest": document["content_digest"],
                    "review_evidence_digest": document["review_evidence_digest"],
                    "review_evidence": document["review_evidence"],
                    "patch_decisions": self._patch_decisions_for_review(document["review_id"]),
                }
            )
        return records

    def _verify_reviewed_bytes(self, reviews: list[dict[str, Any]], *, source_root: Path) -> None:
        current = _source_file_digest_map(source_root)["files"]
        for record in reviews:
            evidence = record["review_evidence"]
            reviewed = {item["path"]: item["digest"] for item in evidence["reviewed_file_digests"]}
            if evidence["review_kind"] == "full" and reviewed != current:
                raise SkillPublicationError("reviewed_bytes_mismatch", record["review_id"])
            for path, digest in reviewed.items():
                if current.get(path) != digest:
                    raise SkillPublicationError("reviewed_bytes_mismatch", record["review_id"])

    def _verify_release_bundle(
        self,
        *,
        paths: SkillPublishedVersion,
        build: Any,
        draft: Any,
        reviews: list[dict[str, Any]],
        manifest: dict[str, Any],
    ) -> None:
        self._verify_candidate_digest_from_paths(
            build,
            draft,
            source_root=paths.unpacked_root,
            ir_path=paths.ir_path,
            validation_report_path=paths.validation_report_path,
        )
        self._verify_reviewed_bytes(reviews, source_root=paths.unpacked_root)
        recorded_reviews = json.loads(paths.review_records_path.read_text(encoding="utf-8"))
        if recorded_reviews != reviews:
            raise SkillPublicationError("review_records_mismatch", str(build.build_id))
        recorded_manifest = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
        if recorded_manifest != manifest:
            raise SkillPublicationError("version_manifest_mismatch", str(build.build_id))
        _verify_source_zip(paths.source_zip_path, paths.unpacked_root)

    def _patch_decisions_for_review(self, review_id: str) -> list[dict[str, str]]:
        method = getattr(self.store, "list_patch_decisions_for_review", None)
        if not callable(method):
            return []
        return [
            {
                "review_id": decision.review_id,
                "patch_id": decision.patch_id,
                "decision": decision.decision,
                "proposal_state": decision.proposal_state,
                "actor": decision.actor,
                "decided_at": decision.decided_at,
            }
            for decision in method(review_id)
        ]

    def _wait_for_existing_version(self, version_id: str) -> Any | None:
        method = getattr(self.store, "get_version", None)
        if not callable(method):
            return None
        for _ in range(50):
            try:
                return method(version_id)
            except KeyError:
                time.sleep(0.02)
        return None

    def _recover_existing_final_version(self, *, version_id: str, build: Any, draft: Any, final_root: Path) -> Any:
        paths = _version_paths(version_id, final_root)
        full_review = self._required_full_review(str(build.build_id))
        if full_review is None:
            raise SkillPublicationError("full_review_required", str(build.build_id))
        reviews = self._review_records(str(build.build_id), content_digest=str(build.content_digest))
        manifest = _version_manifest(
            version_id=version_id,
            build=build,
            draft=draft,
            content_digest=str(build.content_digest),
            review_evidence_digest=str(full_review.review_evidence_digest),
            reviews=reviews,
        )
        self._verify_release_bundle(
            paths=paths,
            build=build,
            draft=draft,
            reviews=reviews,
            manifest=manifest,
        )
        published = SkillPublishedVersion(
            version_id=version_id,
            build_id=str(build.build_id),
            draft_id=str(build.draft_id),
            skill_id=str(getattr(draft, "skill_id", "")),
            content_digest=str(build.content_digest),
            review_evidence_digest=str(full_review.review_evidence_digest),
            version_root=paths.version_root,
            source_zip_path=paths.source_zip_path,
            unpacked_root=paths.unpacked_root,
            ir_path=paths.ir_path,
            validation_report_path=paths.validation_report_path,
            review_records_path=paths.review_records_path,
            manifest_path=paths.manifest_path,
        )
        return self._record_published_version(published, draft)

    def _record_version(self, version: SkillPublishedVersion, draft: Any) -> Any:
        method = getattr(self.store, "register_version", None)
        if callable(method):
            return method(
                version_id=version.version_id,
                project_id=str(draft.project_id),
                draft_id=version.draft_id,
                build_id=version.build_id,
                skill_id=version.skill_id,
                content_digest=version.content_digest,
                review_evidence_digest=version.review_evidence_digest,
                version_root=version.version_root,
                source_zip_path=version.source_zip_path,
                unpacked_root=version.unpacked_root,
                ir_path=version.ir_path,
                validation_report_path=version.validation_report_path,
                review_records_path=version.review_records_path,
                manifest_path=version.manifest_path,
            )
        return None

    def _record_published_version(self, version: SkillPublishedVersion, draft: Any) -> Any:
        method = getattr(self.store, "record_published_version", None)
        if callable(method):
            return method(
                version_id=version.version_id,
                project_id=str(draft.project_id),
                draft_id=version.draft_id,
                build_id=version.build_id,
                skill_id=version.skill_id,
                content_digest=version.content_digest,
                review_evidence_digest=version.review_evidence_digest,
                version_root=version.version_root,
                source_zip_path=version.source_zip_path,
                unpacked_root=version.unpacked_root,
                ir_path=version.ir_path,
                validation_report_path=version.validation_report_path,
                review_records_path=version.review_records_path,
                manifest_path=version.manifest_path,
            )
        self._record_version(version, draft)
        self._mark_build_published(version.build_id, version.version_id)
        return None

    def _mark_build_published(self, build_id: str, version_id: str) -> None:
        method = getattr(self.store, "update_build", None)
        if callable(method):
            method(build_id, version_id=version_id)

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


def _version_paths(version_id: str, root: Path) -> SkillPublishedVersion:
    return SkillPublishedVersion(
        version_id=version_id,
        build_id="",
        draft_id="",
        skill_id="",
        content_digest="",
        review_evidence_digest="",
        version_root=root,
        source_zip_path=root / "source-package.zip",
        unpacked_root=root / "source",
        ir_path=root / "ir" / "skill-ir-v1.json",
        validation_report_path=root / "validation" / "validation-report.json",
        review_records_path=root / "reviews" / "skill-reviews.json",
        manifest_path=root / "manifest.json",
    )


def _version_manifest(
    *,
    version_id: str,
    build: Any,
    draft: Any,
    content_digest: str,
    review_evidence_digest: str,
    reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": "skill-version-manifest-v1",
        "version_id": version_id,
        "build_id": str(build.build_id),
        "draft_id": str(build.draft_id),
        "skill_id": str(getattr(draft, "skill_id", "")),
        "content_digest": content_digest,
        "review_evidence_digest": review_evidence_digest,
        "review_records": [record["review_id"] for record in reviews],
        "artifacts": {
            "source_package": "source-package.zip",
            "source": "source",
            "ir": "ir/skill-ir-v1.json",
            "validation_report": "validation/validation-report.json",
            "review_records": "reviews/skill-reviews.json",
        },
    }


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


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _source_file_digest_map(root: Path) -> dict[str, Any]:
    files: dict[str, str] = {}
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise SkillPublicationError("unsafe_candidate_source", root.name)
        files[path.relative_to(root).as_posix()] = _sha256_path(path)
    return {"schema_version": "skill-file-digests-v1", "files": files}


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


def _verify_source_zip(zip_path: Path, source_root: Path) -> None:
    expected = {
        path.relative_to(source_root).as_posix(): _sha256_path(path)
        for path in sorted((item for item in source_root.rglob("*") if item.is_file()), key=lambda item: item.relative_to(source_root).as_posix())
    }
    try:
        with ZipFile(zip_path) as archive:
            actual_names = archive.namelist()
            if actual_names != sorted(expected):
                raise SkillPublicationError("source_zip_mismatch", str(zip_path))
            for name in actual_names:
                digest = f"sha256:{hashlib.sha256(archive.read(name)).hexdigest()}"
                if digest != expected[name]:
                    raise SkillPublicationError("source_zip_mismatch", name)
    except SkillPublicationError:
        raise
    except Exception as exc:
        raise SkillPublicationError("source_zip_invalid", str(zip_path), str(exc)) from exc


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _json_digest(payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _ir_content_digest(ir: dict[str, Any]) -> str:
    unsigned = json.loads(json.dumps(ir, ensure_ascii=False))
    unsigned["content_digest"] = "sha256:" + "0" * 64
    return _json_digest(unsigned)


def _schema_document(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _review_schema_validator() -> Draft202012Validator:
    resources = [Resource.from_contents(_schema_document(path)) for path in SCHEMA_DIR.glob("*.schema.json")]
    registry = Registry().with_resources((resource.id(), resource) for resource in resources)
    return Draft202012Validator(
        _schema_document(SCHEMA_DIR / "skill-review-v1.schema.json"),
        registry=registry,
        format_checker=FormatChecker(),
    )


def _validate_review_document(document: dict[str, Any]) -> None:
    errors = sorted(_review_schema_validator().iter_errors(document), key=lambda error: list(error.absolute_path))
    if errors:
        raise SkillPublicationError("invalid_review_record", str(document.get("review_id", "")), errors[0].message)
    _validate_review_decision_for_publication(document)


def _validate_review_decision_for_publication(document: dict[str, Any]) -> None:
    evidence = document.get("review_evidence")
    if not isinstance(evidence, dict):
        raise SkillPublicationError("invalid_review_record", str(document.get("review_id", "")), "review_evidence must be an object")
    decision = str(evidence.get("decision") or "")
    findings = evidence.get("findings") if isinstance(evidence.get("findings"), list) else []
    if decision == "approved" and findings:
        raise SkillPublicationError("review_findings_unresolved", str(document.get("review_id", "")))
    if decision == "acknowledged":
        for finding in findings:
            disposition = str(finding.get("disposition") or "") if isinstance(finding, dict) else ""
            if disposition not in {"acknowledged", "resolved"}:
                raise SkillPublicationError("review_findings_unresolved", str(document.get("review_id", "")))


def _move_build_dir(source: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(destination)
    os.replace(source, destination)


def _make_tree_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_file():
            path.chmod(0o444)
        elif path.is_dir():
            path.chmod(0o555)
    root.chmod(0o555)


def _make_tree_contents_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_file():
            path.chmod(0o444)
        elif path.is_dir() and path != root:
            path.chmod(0o555)


def _make_tree_writable(root: Path) -> None:
    if not root.exists():
        return
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts)):
        if path.is_dir():
            path.chmod(0o755)
        elif path.is_file():
            path.chmod(0o644)
    root.chmod(0o755)


def _remove_tree(root: Path) -> None:
    if root.exists():
        _make_tree_writable(root)
        shutil.rmtree(root, ignore_errors=True)


def _acquire_publish_lock(lock_root: Path, version_id: str, *, build_id: str) -> None:
    deadline = time.monotonic() + 30
    while True:
        try:
            lock_root.mkdir(parents=True, exist_ok=False)
            try:
                _write_json_atomic(
                    lock_root / "owner.json",
                    {
                        "pid": os.getpid(),
                        "created_at_epoch": time.time(),
                        "build_id": build_id,
                        "version_id": version_id,
                    },
                )
            except Exception:
                _remove_tree(lock_root)
                raise
            return
        except FileExistsError:
            if _try_reclaim_publish_lock(lock_root):
                continue
            if time.monotonic() >= deadline:
                raise SkillPublicationError("publish_lock_timeout", version_id)
            time.sleep(0.05)


def _try_reclaim_publish_lock(lock_root: Path) -> bool:
    owner_path = lock_root / "owner.json"
    owner_bytes = _read_publish_lock_owner_bytes(owner_path)
    if not _publish_lock_snapshot_is_reclaimable(lock_root, owner_bytes):
        return False
    if _read_publish_lock_owner_bytes(owner_path) != owner_bytes:
        return False
    _remove_tree(lock_root)
    return True


def _read_publish_lock_owner_bytes(owner_path: Path) -> bytes | None:
    try:
        return owner_path.read_bytes()
    except OSError:
        return None


def _publish_lock_snapshot_is_reclaimable(lock_root: Path, owner_bytes: bytes | None) -> bool:
    try:
        owner = json.loads(owner_bytes.decode("utf-8")) if owner_bytes is not None else None
    except Exception:
        owner = None
    if not isinstance(owner, dict):
        try:
            return time.time() - lock_root.stat().st_mtime > _PUBLISH_LOCK_STALE_SECONDS
        except OSError:
            return True

    pid = owner.get("pid")
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return True
    if pid_int <= 0:
        return True
    if pid_int == os.getpid():
        return False
    try:
        os.kill(pid_int, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except OSError:
        return True

    created_at = owner.get("created_at_epoch")
    try:
        return time.time() - float(created_at) > _PUBLISH_LOCK_STALE_SECONDS and pid_int <= 0
    except (TypeError, ValueError):
        return False
