"""Workflow header/version lifecycle routes for Workbench V2."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.services.agent_runtimes import list_agent_runtimes_sync
from app.services.evidence_memory import EvidenceMemoryStore
from app.services.external_agent_discovery import external_agent_provider_specs
from app.services.test_semantic_library import TestSemanticLibraryStore
from app.services.workbench_task_run import WorkbenchTaskRunPreparer
from app.services.workflow_graph import (
    WorkflowGraphValidationError,
    compile_workflow_graph,
    validate_workflow_graph,
)
from app.services.workflow_presets import builtin_workflow_presets
from app.services.workflow_dsl import WorkflowStore
from app.services.workflow_version_store import (
    PublishedWorkflowVersionError,
    WorkflowDraftExistsError,
    WorkflowVersionError,
    WorkflowVersionStore,
)


router = APIRouter(prefix="/api/workbench", tags=["workbench-v2-workflows"])
_BUILTIN_WORKFLOW_IDS = frozenset(
    str(preset["definition"]["id"]) for preset in builtin_workflow_presets()
)


class WorkflowHeaderUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class WorkflowDraftCreateRequest(BaseModel):
    based_on_version_id: str | None = None


class WorkflowDraftUpdateRequest(BaseModel):
    authoring_graph: dict[str, Any]


class WorkflowPublishRequest(BaseModel):
    pass


class WorkflowTrialRunRequest(BaseModel):
    workspace_id: str = Field(min_length=1)
    inputs: dict[str, Any] = Field(default_factory=dict)


def workflow_version_store() -> WorkflowVersionStore:
    return WorkflowVersionStore(settings.data_path / "workbench" / "workflows.db")


def _require_v2() -> None:
    if not settings.workbench_v2_enabled:
        raise HTTPException(status_code=404, detail="Workbench V2 is not enabled")


def _require_mutable_workflow(workflow_id: str) -> None:
    if workflow_id in _BUILTIN_WORKFLOW_IDS:
        raise HTTPException(status_code=409, detail="内置工作流是只读的，请另存为自定义工作流")


@router.patch("/workflows/{workflow_id}")
async def update_workflow_header(
    workflow_id: str, payload: WorkflowHeaderUpdateRequest
) -> dict[str, Any]:
    _require_v2()
    _require_mutable_workflow(workflow_id)
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
    _require_mutable_workflow(workflow_id)
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
    _require_mutable_workflow(workflow_id)
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
    _require_mutable_workflow(workflow_id)
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
    _require_mutable_workflow(workflow_id)
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
    _require_mutable_workflow(workflow_id)
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
    _require_mutable_workflow(workflow_id)
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


@router.post(
    "/workflows/{workflow_id}/versions/{version_id}/test-run",
    status_code=201,
)
async def prepare_workflow_trial_run(
    workflow_id: str,
    version_id: str,
    payload: WorkflowTrialRunRequest,
) -> dict[str, Any]:
    """Compile a draft server-side and prepare a real, isolated run snapshot."""
    _require_v2()
    _require_mutable_workflow(workflow_id)
    version = _version_for_workflow(workflow_id, version_id)
    if version.state != "draft":
        raise HTTPException(status_code=409, detail="只能试运行工作流草稿")
    workspace = _resolve_workspace(payload.workspace_id)
    repo_path = Path(str(workspace["repo_path"])).expanduser().resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise HTTPException(
            status_code=422,
            detail=f"工作空间源码目录不可用：{repo_path}",
        )
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

    root = settings.data_path / "workbench"
    trial_workflow_store = WorkflowStore(root / "trial_workflows.db")
    trial_workflow_store.save_workflow(compiled["compiled_definition"])
    resolved_inputs = dict(payload.inputs)
    for input_definition in compiled["compiled_definition"].get("inputs") or []:
        if str(input_definition.get("resolver") or "") == "workspace":
            resolved_inputs[str(input_definition["id"])] = str(repo_path)
    try:
        prepared = WorkbenchTaskRunPreparer(
            artifact_root=root / "task_runs",
            workflow_store=trial_workflow_store,
            evidence_memory=EvidenceMemoryStore(root / "evidence_memory.db"),
            semantic_library=TestSemanticLibraryStore(root / "test_semantics.db"),
        ).prepare(
            workflow_id=workflow_id,
            workspace_id=payload.workspace_id,
            repo_path=str(repo_path),
            inputs=resolved_inputs,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"工作流输入不完整或无效：{exc}",
        ) from exc
    prepared.task_bundle["compiled_plan"] = compiled["compiled_plan"]
    prepared.task_bundle["workflow_version_id"] = version.version_id
    prepared.task_bundle["trial_run"] = True
    task_run_file = Path(prepared.artifact_dir) / "task_run.json"
    task_run_file.write_text(
        json.dumps(asdict(prepared), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    workflow_version_store().update_draft(
        version_id,
        authoring_graph=version.authoring_graph,
        compiled_definition=compiled["compiled_definition"],
        compiled_plan=compiled["compiled_plan"],
        validation=compiled["validation_result"],
    )
    return {
        "status": "prepared",
        "task_run_id": prepared.task_run_id,
        "workflow_id": workflow_id,
        "workflow_version_id": version_id,
        "workspace_id": payload.workspace_id,
        "compiled_plan": compiled["compiled_plan"],
    }


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
    for runtime in list_agent_runtimes_sync(enabled=True):
        runtime_id = str(runtime.get("id") or "").strip()
        if not runtime_id:
            continue
        mcp_profile = str(runtime.get("mcp_profile") or "").strip()
        providers[f"agent-runtime:{runtime_id}"] = {
            "available": bool(str(runtime.get("command") or "").strip()),
            "mcp_profiles": [mcp_profile] if mcp_profile else [],
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


def _resolve_workspace(workspace_id: str) -> dict[str, Any]:
    try:
        with sqlite3.connect(str(settings.sqlite_db)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT id, name, repo_path FROM workspaces WHERE id = ?",
                (workspace_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail=f"工作空间存储不可用：{exc}")
    if row is None:
        raise HTTPException(status_code=404, detail=f"工作空间不存在：{workspace_id}")
    return dict(row)
