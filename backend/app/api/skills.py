"""Skill-first project, draft, review, and release APIs for F014."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict

from app.config import settings
from app.services.skill_build_pipeline import SkillBuildError, SkillBuildPipeline, SkillPublicationError
from app.services.skill_package_importer import SkillPackageImportError, import_skill_package
from app.services.skill_package_paths import SkillPackagePathError, validate_member_name
from app.services.skill_presets import codetalk_preset_payload
from app.services.skill_review import ReviewProvenance, SkillReviewError, SkillReviewService
from app.services.skill_store import SkillStore, SkillStoreError


router = APIRouter(prefix="/api/skills", tags=["skills"])


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    pack_id: str = ""


class CreateDraftFromSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_root: str
    source_scenario_id: str
    skill_id: str


class WriteDraftFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_path: str
    content: str


class ImportSkillPackageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id_prefix: str = "skill.imported"


class RunReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str = "full"
    changed_paths: list[str] | None = None
    purpose: str = "skill release review"
    session_id: str = "api-review-session"
    provider: str = "deepseek"
    requested_model: str = "deepseek-v4-flash"
    effective_model: str = "deepseek-v4-flash"
    response_model: str = "deepseek-v4-flash"
    declared_context_window_tokens: int = 200000
    requested_max_output_tokens: int = 4096


class RecordReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_evidence: dict[str, Any]


class PatchDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    actor: str


def skill_store() -> SkillStore:
    return SkillStore(
        db_path=settings.data_path / "skills" / "skills.db",
        data_dir=settings.data_path,
    )


@router.get("/presets")
async def list_presets() -> dict[str, Any]:
    return {"items": codetalk_preset_payload(settings.data_path)}


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def create_project(payload: CreateProjectRequest) -> dict[str, Any]:
    try:
        return _project_payload(skill_store().create_project(name=payload.name, pack_id=payload.pack_id))
    except ValueError as exc:
        raise _api_error(422, "invalid_project", str(exc)) from exc


@router.get("/projects/{project_id}")
async def get_project(project_id: str) -> dict[str, Any]:
    store = skill_store()
    try:
        return _project_payload(store.get_project(project_id))
    except KeyError as exc:
        raise _api_error(404, "project_not_found", project_id) from exc


@router.post("/projects/{project_id}/drafts/from-source", status_code=status.HTTP_201_CREATED)
async def create_draft_from_source(project_id: str, payload: CreateDraftFromSourceRequest) -> dict[str, Any]:
    store = skill_store()
    try:
        draft = store.create_draft_from_source(
            project_id=project_id,
            source_root=payload.source_root,
            source_scenario_id=payload.source_scenario_id,
            skill_id=payload.skill_id,
        )
        return _draft_payload(draft)
    except KeyError as exc:
        raise _api_error(404, "project_not_found", project_id) from exc
    except (ValueError, SkillStoreError) as exc:
        raise _api_error(422, _error_code(exc, "invalid_draft_source"), str(exc)) from exc


@router.post("/projects/{project_id}/imports", status_code=status.HTTP_201_CREATED)
async def import_project_package(
    project_id: str,
    file: UploadFile = File(...),
    skill_id_prefix: str = Form("skill.imported"),
) -> dict[str, Any]:
    store = skill_store()
    try:
        store.get_project(project_id)
    except KeyError as exc:
        raise _api_error(404, "project_not_found", project_id) from exc

    import_root = settings.data_path / "skills" / "imports"
    import_root.mkdir(parents=True, exist_ok=True)
    upload_path = import_root / f"upload_{uuid.uuid4().hex}.zip"
    destination = import_root / f"package_{uuid.uuid4().hex}"
    try:
        with upload_path.open("xb") as handle:
            shutil.copyfileobj(file.file, handle)
        imported = import_skill_package(upload_path, destination)
        drafts = [
            store.create_draft_from_source(
                project_id=project_id,
                source_root=source.draft_root,
                source_scenario_id=source.source_scenario_id,
                skill_id=f"{skill_id_prefix}.{source.source_scenario_id}",
            )
            for source in imported.skill_sources
        ]
        return {
            "archive_digest": imported.archive_digest,
            "archive_root": imported.archive_root,
            "inventory_count": len(imported.inventory),
            "drafts": [_draft_payload(draft) for draft in drafts],
        }
    except SkillPackageImportError as exc:
        raise _api_error(422, exc.code, exc.path or str(exc)) from exc
    except SkillStoreError as exc:
        raise _api_error(422, exc.code, str(exc)) from exc
    finally:
        upload_path.unlink(missing_ok=True)


@router.get("/drafts/{draft_id}")
async def get_draft(draft_id: str) -> dict[str, Any]:
    try:
        return _draft_payload(skill_store().get_draft(draft_id))
    except KeyError as exc:
        raise _api_error(404, "draft_not_found", draft_id) from exc


@router.post("/drafts/{draft_id}/files", status_code=status.HTTP_201_CREATED)
async def write_draft_file(draft_id: str, payload: WriteDraftFileRequest) -> dict[str, Any]:
    store = skill_store()
    try:
        draft = store.get_draft(draft_id)
    except KeyError as exc:
        raise _api_error(404, "draft_not_found", draft_id) from exc
    try:
        relative_path = validate_member_name(payload.relative_path)
        target = _safe_draft_file_target(Path(draft.filesystem_path), relative_path)
        target.write_text(payload.content, encoding="utf-8")
    except (SkillPackagePathError, ValueError, OSError) as exc:
        raise _api_error(422, "invalid_draft_file", payload.relative_path) from exc
    rescan = store.rescan_draft(draft_id)
    return {
        "draft_id": draft_id,
        "relative_path": relative_path,
        "digest": rescan.file_digests.get(relative_path, ""),
        "file_count": rescan.file_count,
    }


@router.post("/drafts/{draft_id}/builds", status_code=status.HTTP_201_CREATED)
async def build_draft(draft_id: str) -> dict[str, Any]:
    store = skill_store()
    try:
        return _build_payload(SkillBuildPipeline(store).build_candidate(draft_id))
    except KeyError as exc:
        raise _api_error(404, "draft_not_found", draft_id) from exc
    except SkillBuildError as exc:
        raise _api_error(422, exc.code, str(exc)) from exc


@router.get("/builds/{build_id}")
async def get_build(build_id: str) -> dict[str, Any]:
    try:
        return _build_payload(skill_store().get_build(build_id))
    except KeyError as exc:
        raise _api_error(404, "build_not_found", build_id) from exc


@router.post("/builds/{build_id}/reviews/run", status_code=status.HTTP_201_CREATED)
async def run_build_review(build_id: str, payload: RunReviewRequest) -> dict[str, Any]:
    store = skill_store()
    try:
        provenance = ReviewProvenance(
            purpose=payload.purpose,
            session_id=payload.session_id,
            provider=payload.provider,
            requested_model=payload.requested_model,
            effective_model=payload.effective_model,
            response_model=payload.response_model,
            declared_context_window_tokens=payload.declared_context_window_tokens,
            requested_max_output_tokens=payload.requested_max_output_tokens,
        )
        review = SkillReviewService(store).review_build(
            build_id,
            scope=payload.scope,
            provenance=provenance,
            changed_paths=payload.changed_paths,
        )
        return _review_payload(store.get_review(review.review_id))
    except KeyError as exc:
        raise _api_error(404, "build_not_found", build_id) from exc
    except SkillReviewError as exc:
        raise _api_error(422, "invalid_review", str(exc)) from exc


@router.post("/builds/{build_id}/reviews", status_code=status.HTTP_201_CREATED)
async def record_build_review(build_id: str, payload: RecordReviewRequest) -> dict[str, Any]:
    store = skill_store()
    try:
        review = SkillReviewService(store).record_review(build_id=build_id, review_evidence=payload.review_evidence)
        return _review_payload(review)
    except KeyError as exc:
        raise _api_error(404, "build_not_found", build_id) from exc
    except SkillReviewError as exc:
        raise _api_error(422, "invalid_review", str(exc)) from exc


@router.get("/reviews/{review_id}")
async def get_review(review_id: str) -> dict[str, Any]:
    store = skill_store()
    try:
        return _review_payload(store.get_review(review_id))
    except KeyError as exc:
        raise _api_error(404, "review_not_found", review_id) from exc


@router.post("/reviews/{review_id}/patches/{patch_id}/decision", status_code=status.HTTP_201_CREATED)
async def decide_review_patch(review_id: str, patch_id: str, payload: PatchDecisionRequest) -> dict[str, Any]:
    store = skill_store()
    try:
        store.get_review(review_id)
        decision = SkillReviewService(store).decide_patch(
            review_id,
            patch_id,
            decision=payload.decision,
            actor=payload.actor,
        )
        return {
            "review_id": decision.review_id,
            "patch_id": decision.patch_id,
            "decision": decision.decision,
            "proposal_state": decision.proposal_state,
            "actor": decision.actor,
            "decided_at": decision.decided_at,
        }
    except KeyError as exc:
        code = "review_not_found" if str(exc).strip("'") == review_id else "patch_not_found"
        message = review_id if code == "review_not_found" else patch_id
        raise _api_error(404, code, message) from exc
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise _api_error(409, "review_record_unavailable", review_id) from exc
    except SkillReviewError as exc:
        raise _api_error(422, "invalid_patch_decision", str(exc)) from exc


@router.post("/builds/{build_id}/publish", status_code=status.HTTP_201_CREATED)
async def publish_build(build_id: str) -> dict[str, Any]:
    store = skill_store()
    try:
        return _version_payload(SkillBuildPipeline(store).publish_build(build_id))
    except KeyError as exc:
        raise _api_error(404, "build_not_found", build_id) from exc
    except SkillPublicationError as exc:
        status_code = (
            409
            if exc.code
            in {
                "full_review_required",
                "build_not_publishable",
                "publish_lock_timeout",
                "review_findings_unresolved",
            }
            else 422
        )
        raise _api_error(status_code, exc.code, str(exc)) from exc


@router.get("/versions")
async def list_versions(skill_id: str = Query(default="")) -> dict[str, Any]:
    versions = skill_store().list_versions(skill_id=skill_id or None)
    return {"items": [_version_payload(version) for version in versions]}


@router.get("/versions/{version_id}")
async def get_version(version_id: str) -> dict[str, Any]:
    store = skill_store()
    try:
        return _version_payload(store.get_version(version_id))
    except KeyError as exc:
        raise _api_error(404, "version_not_found", version_id) from exc


@router.get("/versions/{version_id}/manifest")
async def get_version_manifest(version_id: str) -> dict[str, Any]:
    store = skill_store()
    try:
        version = store.get_version(version_id)
    except KeyError as exc:
        raise _api_error(404, "version_not_found", version_id) from exc
    return _read_json_record(
        Path(version.manifest_path),
        code="version_manifest_unavailable",
        label=version_id,
    )


@router.get("/versions/{version_id}/ir")
async def get_version_ir(version_id: str) -> dict[str, Any]:
    store = skill_store()
    try:
        version = store.get_version(version_id)
    except KeyError as exc:
        raise _api_error(404, "version_not_found", version_id) from exc
    return _read_json_record(
        Path(version.ir_path),
        code="version_ir_unavailable",
        label=version_id,
    )


def _project_payload(project: Any) -> dict[str, Any]:
    return {
        "project_id": project.project_id,
        "name": project.name,
        "pack_id": project.pack_id,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


def _draft_payload(draft: Any) -> dict[str, Any]:
    return {
        "draft_id": draft.draft_id,
        "project_id": draft.project_id,
        "skill_id": draft.skill_id,
        "source_scenario_id": draft.source_scenario_id,
        "filesystem_path": str(draft.filesystem_path),
        "created_at": draft.created_at,
        "updated_at": draft.updated_at,
    }


def _build_payload(build: Any) -> dict[str, Any]:
    return {
        "build_id": build.build_id,
        "draft_id": build.draft_id,
        "status": build.status,
        "version_id": build.version_id,
        "content_digest": build.content_digest,
        "zip_digest": build.zip_digest,
        "build_root": str(build.build_root),
        "zip_path": str(build.zip_path),
        "unpacked_root": str(build.unpacked_root),
        "ir_path": str(build.ir_path),
        "validation_report_path": str(build.validation_report_path),
        "file_digest_map_path": str(build.file_digest_map_path),
        "manifest_path": str(build.manifest_path),
    }


def _review_payload(review: Any) -> dict[str, Any]:
    record = _read_json_record(
        Path(review.record_path),
        code="review_record_unavailable",
        label=review.review_id,
    )
    return {
        "review_id": review.review_id,
        "build_id": review.build_id,
        "review_kind": review.review_kind,
        "decision": review.decision,
        "content_digest": review.content_digest,
        "review_evidence_digest": review.review_evidence_digest,
        "record_path": str(review.record_path),
        "review_evidence": record["review_evidence"],
        "created_at": review.created_at,
        "updated_at": review.updated_at,
    }


def _version_payload(version: Any) -> dict[str, Any]:
    return {
        "version_id": version.version_id,
        "project_id": version.project_id,
        "draft_id": version.draft_id,
        "build_id": version.build_id,
        "skill_id": version.skill_id,
        "content_digest": version.content_digest,
        "review_evidence_digest": version.review_evidence_digest,
        "version_root": str(version.version_root),
        "source_zip_path": str(version.source_zip_path),
        "unpacked_root": str(version.unpacked_root),
        "ir_path": str(version.ir_path),
        "validation_report_path": str(version.validation_report_path),
        "review_records_path": str(version.review_records_path),
        "manifest_path": str(version.manifest_path),
        "created_at": version.created_at,
    }


def _read_json_record(path: Path, *, code: str, label: str) -> dict[str, Any]:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise _api_error(409, code, label) from exc
    if not isinstance(record, dict):
        raise _api_error(409, code, label)
    return record


def _safe_draft_file_target(draft_root: Path, relative_path: str) -> Path:
    root = draft_root.resolve(strict=True)
    parts = relative_path.split("/")
    parent = root
    for segment in parts[:-1]:
        if parent.is_symlink():
            raise SkillPackagePathError(relative_path)
        candidate = parent / segment
        if candidate.exists():
            if candidate.is_symlink() or not candidate.is_dir():
                raise SkillPackagePathError(relative_path)
            candidate.resolve(strict=True).relative_to(root)
        parent = candidate
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink():
        raise SkillPackagePathError(relative_path)
    parent.resolve(strict=True).relative_to(root)
    target = parent / parts[-1]
    if target.exists() and target.is_symlink():
        raise SkillPackagePathError(relative_path)
    target.resolve(strict=False).parent.relative_to(root)
    return target


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _error_code(exc: BaseException, fallback: str) -> str:
    return str(getattr(exc, "code", "") or fallback)
