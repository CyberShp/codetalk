"""Workflow header/version lifecycle routes for Workbench V2."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.services.external_agent_discovery import external_agent_provider_specs
from app.services.workflow_graph import (
    WorkflowGraphValidationError,
    compile_workflow_graph,
    validate_workflow_graph,
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
    pass


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


@router.post("/workflows/{workflow_id}/versions/{version_id}/validate")
async def validate_workflow_version(workflow_id: str, version_id: str) -> dict[str, Any]:
    _require_v2()
    version = _version_for_workflow(workflow_id, version_id)
    if version.state != "draft":
        raise HTTPException(status_code=409, detail="Published workflow versions are immutable")
    validation = validate_workflow_graph(
        version.authoring_graph,
        capabilities=_workflow_graph_capabilities(),
    )
    workflow_version_store().update_draft(
        version_id,
        authoring_graph=version.authoring_graph,
        validation=validation,
    )
    return validation


@router.post("/workflows/{workflow_id}/versions/{version_id}/compile")
async def compile_workflow_version(workflow_id: str, version_id: str) -> dict[str, Any]:
    _require_v2()
    version = _version_for_workflow(workflow_id, version_id)
    if version.state != "draft":
        raise HTTPException(status_code=409, detail="Published workflow versions are immutable")
    try:
        compiled = compile_workflow_graph(
            version.authoring_graph,
            capabilities=_workflow_graph_capabilities(),
            workflow_version_id=version.version_id,
            workflow_version_number=version.version_number,
        )
    except WorkflowGraphValidationError as exc:
        workflow_version_store().update_draft(
            version_id,
            authoring_graph=version.authoring_graph,
            validation=exc.validation,
        )
        raise HTTPException(status_code=422, detail=exc.validation)
    workflow_version_store().update_draft(
        version_id,
        authoring_graph=version.authoring_graph,
        compiled_definition=compiled["compiled_definition"],
        compiled_plan=compiled["compiled_plan"],
        validation=compiled["validation_result"],
    )
    return compiled


@router.post("/workflows/{workflow_id}/versions/{version_id}/publish")
async def publish_workflow_version(
    workflow_id: str,
    version_id: str,
    payload: WorkflowPublishRequest,
) -> dict[str, Any]:
    _require_v2()
    version = _version_for_workflow(workflow_id, version_id)
    try:
        compiled = compile_workflow_graph(
            version.authoring_graph,
            capabilities=_workflow_graph_capabilities(),
            workflow_version_id=version.version_id,
            workflow_version_number=version.version_number,
        )
        version = workflow_version_store().publish_version(
            version_id,
            authoring_graph=version.authoring_graph,
            compiled_definition=compiled["compiled_definition"],
            compiled_plan=compiled["compiled_plan"],
            validation=compiled["validation_result"],
        )
    except WorkflowGraphValidationError as exc:
        workflow_version_store().update_draft(
            version_id,
            authoring_graph=version.authoring_graph,
            validation=exc.validation,
        )
        raise HTTPException(status_code=422, detail=exc.validation)
    except PublishedWorkflowVersionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (WorkflowVersionError, ValueError) as exc:
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


def _workflow_graph_capabilities() -> dict[str, Any]:
    providers: dict[str, dict[str, Any]] = {
        "builtin-llm": {"available": True, "mcp_profiles": []},
    }
    for provider_id, spec in external_agent_provider_specs().items():
        providers[provider_id] = {
            "available": bool(spec.command),
            "mcp_profiles": sorted(str(item) for item in spec.mcp_profiles),
        }
    return {
        "providers": providers,
        "skills": [
            "artifact-contract",
            "black-box-test-design",
            "coverage-gap-analysis",
            "defect-triage-regression",
            "performance-reliability-testing",
            "sfmea",
            "source-evidence-first",
            "storage-flow-analysis",
            "test-execution-orchestration",
            "test-strategy-planning",
        ],
    }
