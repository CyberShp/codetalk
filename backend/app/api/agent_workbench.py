"""Agent workbench APIs: workflows, evidence memory, and test semantics."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import settings
from app.services.agent_run_harness import AgentRunHarness, ArtifactValidationHarness
from app.services.evidence_memory import EvidenceMemoryStore
from app.services.test_semantic_library import (
    SemanticCaseValidationError,
    TestSemanticLibraryStore,
)
from app.services.workbench_task_run import WorkbenchTaskRunPreparer
from app.services.workflow_dsl import WorkflowStore, WorkflowValidationError

router = APIRouter(prefix="/api/workbench", tags=["agent-workbench"])


class AnalysisRunCreate(BaseModel):
    workspace_id: str
    repo_path: str
    object_text: str
    workflow_id: str
    status: str = "running"
    run_id: str | None = None


class EvidenceItemCreate(BaseModel):
    run_id: str
    workspace_id: str
    kind: str
    subject_key: str
    status: str
    source: str
    path: str = ""
    symbol: str = ""
    reason: str = ""
    confidence: float | None = None
    text: str = ""
    provenance: dict[str, Any] = Field(default_factory=dict)
    evidence_id: str | None = None


class AgentRunCreate(BaseModel):
    provider: str
    command: list[str]
    cwd: str
    workflow_snapshot: dict[str, Any] = Field(default_factory=dict)
    task_bundle: dict[str, Any] = Field(default_factory=dict)
    mcp_profile: str = ""


class RawOutputCreate(BaseModel):
    stdout: str = ""
    stderr: str = ""


class ValidateMrArtifactsRequest(BaseModel):
    required_artifacts: list[str]


class PrepareTaskRunRequest(BaseModel):
    workflow_id: str
    workspace_id: str
    repo_path: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    provider_override: str | None = None


def _workbench_dir() -> Path:
    root = settings.data_path / "workbench"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _workflow_store() -> WorkflowStore:
    return WorkflowStore(_workbench_dir() / "workflows.db")


def _semantic_store() -> TestSemanticLibraryStore:
    return TestSemanticLibraryStore(_workbench_dir() / "test_semantics.db")


def _memory_store() -> EvidenceMemoryStore:
    return EvidenceMemoryStore(_workbench_dir() / "evidence_memory.db")


def _agent_runs_dir() -> Path:
    root = _workbench_dir() / "agent_runs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _task_runs_dir() -> Path:
    root = _workbench_dir() / "task_runs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _agent_run_dir(run_id: str) -> Path:
    value = run_id.strip()
    if not value or "/" in value or "\\" in value or ".." in value:
        raise HTTPException(status_code=400, detail="invalid run_id")
    return _agent_runs_dir() / value


@router.post("/workflows", status_code=201)
async def save_workflow(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        workflow = _workflow_store().save_workflow(payload)
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _workflow_response(workflow.raw)


@router.get("/workflows")
async def list_workflows() -> list[dict[str, Any]]:
    return [_workflow_response(item.raw) for item in _workflow_store().list_workflows()]


@router.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: str) -> dict[str, Any]:
    try:
        workflow = _workflow_store().get_workflow(workflow_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown workflow: {workflow_id}")
    return _workflow_response(workflow.raw)


@router.get("/workflows/{workflow_id}/snapshot")
async def get_workflow_snapshot(workflow_id: str) -> dict[str, Any]:
    try:
        return _workflow_store().freeze_workflow_snapshot(workflow_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown workflow: {workflow_id}")


@router.post("/semantic-cases", status_code=201)
async def upsert_semantic_case(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        semantic_id = _semantic_store().upsert_case(payload)
    except SemanticCaseValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"semantic_id": semantic_id, "case_id": str(payload.get("case_id") or "")}


@router.get("/semantic-cases/search")
async def search_semantic_cases(
    q: str = Query(..., min_length=1),
    module: str = "",
    test_level: str = "",
    limit: int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    items = _semantic_store().retrieve(
        query=q,
        module=module,
        test_level=test_level,
        limit=limit,
    )
    return {"items": [asdict(item) for item in items]}


@router.post("/memory/runs", status_code=201)
async def create_memory_run(payload: AnalysisRunCreate) -> dict[str, Any]:
    run_id = _memory_store().record_analysis_run(**payload.model_dump())
    return {"run_id": run_id}


@router.post("/memory/evidence", status_code=201)
async def create_memory_evidence(payload: EvidenceItemCreate) -> dict[str, Any]:
    evidence_id = _memory_store().upsert_evidence_item(**payload.model_dump())
    return {"evidence_id": evidence_id}


@router.get("/memory/search")
async def search_memory(
    q: str = Query(..., min_length=1),
    workspace_id: str = "",
    limit: int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    items = _memory_store().search_analysis_memory(
        q,
        workspace_id=workspace_id or None,
        limit=limit,
    )
    return {"items": [asdict(item) for item in items]}


@router.get("/memory/recent")
async def recent_memory(
    workspace_id: str = "",
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    return {
        "items": _memory_store().list_recent_analysis(
            workspace_id=workspace_id or None,
            limit=limit,
        )
    }


@router.post("/agent-runs", status_code=201)
async def create_agent_run(payload: AgentRunCreate) -> dict[str, Any]:
    import uuid

    run_id = f"agent_run_{uuid.uuid4().hex}"
    artifact_dir = _agent_run_dir(run_id)
    run = AgentRunHarness(artifact_dir).create_run(
        run_id=run_id,
        provider=payload.provider,
        command=payload.command,
        cwd=payload.cwd,
        workflow_snapshot=payload.workflow_snapshot,
        task_bundle=payload.task_bundle,
        mcp_profile=payload.mcp_profile,
    )
    return asdict(run)


@router.post("/agent-runs/{run_id}/raw-output")
async def record_agent_run_raw_output(run_id: str, payload: RawOutputCreate) -> dict[str, Any]:
    artifact_dir = _agent_run_dir(run_id)
    if not (artifact_dir / "agent_run.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown agent run: {run_id}")
    AgentRunHarness(artifact_dir).record_raw_output(
        run_id,
        stdout=payload.stdout,
        stderr=payload.stderr,
    )
    return {"ok": True}


@router.post("/agent-runs/{run_id}/validate-mr-artifacts")
async def validate_agent_run_mr_artifacts(
    run_id: str,
    payload: ValidateMrArtifactsRequest,
) -> dict[str, Any]:
    artifact_dir = _agent_run_dir(run_id)
    if not (artifact_dir / "agent_run.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown agent run: {run_id}")
    result = ArtifactValidationHarness(artifact_dir).validate_mr_artifacts(
        required_artifacts=payload.required_artifacts,
    )
    return asdict(result)


@router.post("/task-runs/prepare", status_code=201)
async def prepare_task_run(payload: PrepareTaskRunRequest) -> dict[str, Any]:
    try:
        result = WorkbenchTaskRunPreparer(
            artifact_root=_task_runs_dir(),
            workflow_store=_workflow_store(),
        ).prepare(
            workflow_id=payload.workflow_id,
            workspace_id=payload.workspace_id,
            repo_path=payload.repo_path,
            inputs=payload.inputs,
            provider_override=payload.provider_override,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown workflow: {payload.workflow_id}")
    return asdict(result)


def _workflow_response(payload: dict[str, Any]) -> dict[str, Any]:
    return dict(payload)
