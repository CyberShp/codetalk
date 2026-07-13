"""Workflow header/version lifecycle routes for Workbench V2."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.services.workflow_dsl import (
    WorkflowValidationError,
    audit_workflow_definition,
    validate_workflow_definition,
)
from app.services.workflow_version_store import (
    PublishedWorkflowVersionError,
    WorkflowDraftExistsError,
    WorkflowVersionError,
    WorkflowVersionStore,
)


router = APIRouter(prefix="/api/workbench", tags=["workbench-v2-workflows"])


class WorkflowHeaderUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class WorkflowDraftCreateRequest(BaseModel):
    based_on_version_id: str | None = None


class WorkflowDraftUpdateRequest(BaseModel):
    authoring_graph: dict[str, Any]


class WorkflowPublishRequest(BaseModel):
    authoring_graph: dict[str, Any]
    compiled_definition: dict[str, Any]
    compiled_plan: dict[str, Any] = Field(default_factory=dict)


def workflow_version_store() -> WorkflowVersionStore:
    return WorkflowVersionStore(settings.data_path / "workbench" / "workflows.db")


def _require_v2() -> None:
    if not settings.workbench_v2_enabled:
        raise HTTPException(status_code=404, detail="Workbench V2 is not enabled")


@router.patch("/workflows/{workflow_id}")
async def update_workflow_header(
    workflow_id: str, payload: WorkflowHeaderUpdateRequest
) -> dict[str, Any]:
    _require_v2()
    try:
        header = workflow_version_store().update_workflow(
            workflow_id,
            name=payload.name,
            description=payload.description,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown workflow: {workflow_id}")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return asdict(header)


@router.post("/workflows/{workflow_id}/archive")
async def archive_workflow_header(workflow_id: str) -> dict[str, Any]:
    _require_v2()
    try:
        return asdict(workflow_version_store().archive_workflow(workflow_id))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown workflow: {workflow_id}")


@router.get("/workflows/{workflow_id}/versions")
async def list_workflow_versions(workflow_id: str) -> dict[str, Any]:
    _require_v2()
    try:
        items = workflow_version_store().list_versions(workflow_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown workflow: {workflow_id}")
    return {"items": [asdict(item) for item in items]}


@router.post("/workflows/{workflow_id}/versions", status_code=201)
async def create_workflow_draft(
    workflow_id: str, payload: WorkflowDraftCreateRequest
) -> dict[str, Any]:
    _require_v2()
    try:
        version = workflow_version_store().create_draft(
            workflow_id,
            based_on_version_id=payload.based_on_version_id,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown workflow: {workflow_id}")
    except WorkflowDraftExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except WorkflowVersionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return asdict(version)


@router.get("/workflows/{workflow_id}/versions/{version_id}")
async def get_workflow_version(workflow_id: str, version_id: str) -> dict[str, Any]:
    _require_v2()
    version = _version_for_workflow(workflow_id, version_id)
    return asdict(version)


@router.put("/workflows/{workflow_id}/versions/{version_id}")
async def update_workflow_draft(
    workflow_id: str,
    version_id: str,
    payload: WorkflowDraftUpdateRequest,
) -> dict[str, Any]:
    _require_v2()
    _version_for_workflow(workflow_id, version_id)
    try:
        version = workflow_version_store().update_draft(
            version_id,
            authoring_graph=payload.authoring_graph,
        )
    except PublishedWorkflowVersionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return asdict(version)


@router.post("/workflows/{workflow_id}/versions/{version_id}/publish")
async def publish_workflow_version(
    workflow_id: str,
    version_id: str,
    payload: WorkflowPublishRequest,
) -> dict[str, Any]:
    _require_v2()
    _version_for_workflow(workflow_id, version_id)
    try:
        compiled = validate_workflow_definition(payload.compiled_definition)
        audit = audit_workflow_definition(compiled.raw)
        validation = {
            "valid": True,
            "errors": [],
            "warnings": list(audit.get("warnings") or []),
            "validator": "legacy-dsl-phase-1",
        }
        version = workflow_version_store().publish_version(
            version_id,
            authoring_graph=payload.authoring_graph,
            compiled_definition=compiled.raw,
            compiled_plan=payload.compiled_plan,
            validation=validation,
        )
    except PublishedWorkflowVersionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (WorkflowValidationError, WorkflowVersionError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return asdict(version)


def _version_for_workflow(workflow_id: str, version_id: str):
    try:
        version = workflow_version_store().get_version(version_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown workflow version: {version_id}")
    if version.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail=f"Unknown workflow version: {version_id}")
    return version
