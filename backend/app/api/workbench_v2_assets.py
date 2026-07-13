"""Semantic-case and evidence asset-library routes for Workbench V2."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from app.config import settings
from app.services.evidence_memory import EvidenceMemoryStore
from app.services.test_semantic_library import (
    SemanticCase,
    SemanticCaseValidationError,
    TestSemanticLibraryStore,
)


router = APIRouter(prefix="/api/workbench", tags=["workbench-v2-assets"])
_PREVIEW_ID = re.compile(r"^preview_[a-f0-9]{32}$")
_IMPORT_ID = re.compile(r"^import_[a-f0-9]{32}$")


class SemanticCaseUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str | None = None
    feature: str | None = None
    module: str | None = None
    scenario: str | None = None
    preconditions: list[str] | None = None
    actions: list[str] | None = None
    expected: list[str] | None = None
    test_level: str | None = None
    interface: str | None = None
    terms: list[str] | None = None
    assertion_style: str | None = None
    tags: list[str] | None = None
    source_ref: str | None = None


class SemanticImportCommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_id: str
    conflict_strategy: str


def semantic_store() -> TestSemanticLibraryStore:
    return TestSemanticLibraryStore(settings.data_path / "workbench" / "test_semantics.db")


def evidence_store() -> EvidenceMemoryStore:
    return EvidenceMemoryStore(settings.data_path / "workbench" / "evidence_memory.db")


def _require_v2() -> None:
    if not settings.workbench_v2_enabled:
        raise HTTPException(status_code=404, detail="Workbench V2 is not enabled")


def _import_dir() -> Path:
    path = settings.data_path / "workbench" / "semantic_imports"
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.get("/semantic-cases")
async def list_semantic_cases(
    q: str = "",
    feature: str = "",
    module: str = "",
    test_level: str = "",
    interface: str = "",
    tag: str = "",
    status: str = "",
    source: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    _require_v2()
    result = semantic_store().list_cases(
        q=q,
        feature=feature,
        module=module,
        test_level=test_level,
        interface=interface,
        tag=tag,
        status=status,
        source=source,
        page=page,
        page_size=page_size,
    )
    return {
        **result,
        "items": [_semantic_payload(item, q=q) for item in result["items"]],
    }


@router.get("/semantic-cases/facets")
async def semantic_case_facets() -> dict[str, Any]:
    _require_v2()
    return semantic_store().facets()


@router.post("/semantic-cases/import/preview")
async def preview_semantic_import(
    file: UploadFile = File(...), options_json: str = Form("{}")
) -> dict[str, Any]:
    _require_v2()
    try:
        options = json.loads(options_json or "{}")
        if not isinstance(options, dict):
            raise SemanticCaseValidationError("options_json must be an object")
        preview = semantic_store().preview_case_file(
            await file.read(), filename=Path(file.filename or "semantic_cases").name, options=options
        )
    except (json.JSONDecodeError, UnicodeDecodeError, SemanticCaseValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    preview_id = f"preview_{uuid.uuid4().hex}"
    preview["preview_id"] = preview_id
    (_import_dir() / f"{preview_id}.json").write_text(
        json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return preview


@router.post("/semantic-cases/import/commit", status_code=201)
async def commit_semantic_import(payload: SemanticImportCommitRequest) -> dict[str, Any]:
    _require_v2()
    if not _PREVIEW_ID.fullmatch(payload.preview_id):
        raise HTTPException(status_code=422, detail="invalid preview_id")
    preview_path = _import_dir() / f"{payload.preview_id}.json"
    if not preview_path.exists():
        raise HTTPException(status_code=404, detail="Import preview has expired or does not exist")
    preview = json.loads(preview_path.read_text(encoding="utf-8"))
    try:
        result = semantic_store().commit_preview(
            preview, conflict_strategy=payload.conflict_strategy
        )
    except SemanticCaseValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    import_id = f"import_{uuid.uuid4().hex}"
    failure_path = _import_dir() / f"{import_id}.failures.ndjson"
    failure_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in result["failed"]),
        encoding="utf-8",
    )
    preview_path.unlink(missing_ok=True)
    return {
        **result,
        "import_id": import_id,
        "failure_download_url": f"/api/workbench/semantic-cases/imports/{import_id}/failures",
    }


@router.get("/semantic-cases/imports/{import_id}/failures")
async def download_semantic_import_failures(import_id: str) -> Response:
    _require_v2()
    if not _IMPORT_ID.fullmatch(import_id):
        raise HTTPException(status_code=404, detail="Unknown semantic import")
    path = _import_dir() / f"{import_id}.failures.ndjson"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Unknown semantic import")
    return Response(
        content=path.read_bytes(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{import_id}-failures.ndjson"'},
    )


@router.get("/semantic-cases/{semantic_id}")
async def get_semantic_case(semantic_id: str) -> dict[str, Any]:
    _require_v2()
    try:
        return _semantic_payload(semantic_store().get_case(semantic_id))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown semantic case: {semantic_id}")


@router.patch("/semantic-cases/{semantic_id}")
async def update_semantic_case(
    semantic_id: str, payload: SemanticCaseUpdateRequest
) -> dict[str, Any]:
    _require_v2()
    try:
        return _semantic_payload(
            semantic_store().update_case(semantic_id, payload.model_dump(exclude_unset=True))
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown semantic case: {semantic_id}")
    except SemanticCaseValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/semantic-cases/{semantic_id}/deprecate")
async def deprecate_semantic_case(semantic_id: str) -> dict[str, Any]:
    _require_v2()
    try:
        return _semantic_payload(semantic_store().deprecate_case(semantic_id))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown semantic case: {semantic_id}")


@router.post("/semantic-cases/{semantic_id}/restore")
async def restore_semantic_case(semantic_id: str) -> dict[str, Any]:
    _require_v2()
    try:
        return _semantic_payload(semantic_store().restore_case(semantic_id))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown semantic case: {semantic_id}")


@router.get("/evidence")
async def list_evidence_assets(
    q: str = "",
    workspace_id: str = "",
    kind: str = "",
    status: str = "",
    source: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    _require_v2()
    result = evidence_store().list_evidence_assets(
        q=q,
        workspace_id=workspace_id,
        kind=kind,
        status=status,
        source=source,
        page=page,
        page_size=page_size,
    )
    return {**result, "items": [asdict(item) for item in result["items"]]}


@router.get("/evidence/facets")
async def evidence_facets() -> dict[str, Any]:
    _require_v2()
    return evidence_store().facets()


@router.get("/evidence/{evidence_id}")
async def get_evidence_asset(evidence_id: str) -> dict[str, Any]:
    _require_v2()
    store = evidence_store()
    try:
        item = store.get_evidence_item(evidence_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown evidence item: {evidence_id}")
    return {
        **asdict(item),
        "source_slices": [asdict(source_slice) for source_slice in store.list_source_slices(evidence_id)],
    }


def _semantic_payload(item: SemanticCase, *, q: str = "") -> dict[str, Any]:
    payload = asdict(item)
    payload["counts"] = {
        "preconditions": len(item.preconditions),
        "actions": len(item.actions),
        "expected": len(item.expected),
    }
    payload["references"] = _semantic_references(item)
    terms = [term.casefold() for term in q.split() if term.strip()]
    fields = {
        "case_id": item.case_id,
        "feature": item.feature,
        "module": item.module,
        "scenario": item.scenario,
        "terms": " ".join(item.terms),
        "tags": " ".join(item.tags),
    }
    payload["matched_fields"] = [
        field for field, value in fields.items()
        if terms and any(term in value.casefold() for term in terms)
    ]
    return payload


def _semantic_references(item: SemanticCase) -> list[dict[str, Any]]:
    parts = item.source_ref.split(":", 2)
    if len(parts) < 2 or parts[0] != "task_run" or not parts[1]:
        return []
    reference: dict[str, Any] = {"type": "task_run", "task_run_id": parts[1]}
    if len(parts) == 3 and parts[2]:
        reference["output_id"] = parts[2]
    task_run_path = settings.data_path / "workbench" / "task_runs" / parts[1] / "task_run.json"
    if task_run_path.exists():
        try:
            run = json.loads(task_run_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            run = {}
        for key in ("task_id", "workflow_id", "workflow_version_id"):
            if run.get(key):
                reference[key] = str(run[key])
    return [reference]
