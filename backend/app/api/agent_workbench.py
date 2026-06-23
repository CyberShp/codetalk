"""Agent workbench APIs: workflows, evidence memory, and test semantics."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from app.config import settings
from app.services.agent_run_harness import AgentRunHarness, ArtifactValidationHarness
from app.services.agent_provider_settings import apply_persisted_agent_provider_settings
from app.services.evidence_memory import EvidenceMemoryStore
from app.services.external_agent_discovery import (
    external_agent_provider_capabilities,
    external_agent_provider_spec,
    external_agent_provider_specs,
    probe_external_agent_startup,
    split_agent_command,
)
from app.services.test_semantic_library import (
    SemanticCaseValidationError,
    TestSemanticLibraryStore,
)
from app.services.workbench_artifact_manifest import (
    artifact_preview,
    build_task_artifact_manifest,
    workbench_artifact_kind,
    write_task_artifact_manifest,
)
from app.services.workbench_task_run import WorkbenchTaskRunPreparer
from app.services.workbench_task_run import WorkbenchTaskRunStore
from app.services.workbench_task_run import build_agent_cli_provider_diagnostics
from app.services.workbench_task_run import build_codetalk_provider_snapshot
from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
from app.services.workflow_dsl import (
    ALLOWED_INPUT_TYPES,
    ALLOWED_STEP_TYPES,
    WorkflowStore,
    WorkflowValidationError,
    audit_workflow_definition,
)
from app.services.workflow_presets import (
    builtin_workflow_presets,
    install_workflow_preset,
)

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


class AgentRunExecuteRequest(BaseModel):
    timeout_sec: int = Field(default=90, ge=1, le=3600)


class TaskRunExecuteRequest(BaseModel):
    timeout_sec: int = Field(default=90, ge=1, le=3600)
    stop_on_error: bool = True


class ValidateMrArtifactsRequest(BaseModel):
    required_artifacts: list[str]


class MaterializeEvidenceRequest(BaseModel):
    required_artifacts: list[str]
    object_text: str = ""


class ImportSemanticOutputsRequest(BaseModel):
    output_ids: list[str] = Field(default_factory=list)
    defaults: dict[str, Any] = Field(default_factory=dict)


class PrepareTaskRunRequest(BaseModel):
    workflow_id: str
    workspace_id: str
    repo_path: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    provider_override: str | None = None


class RunTaskRunRequest(PrepareTaskRunRequest):
    timeout_sec: int = Field(default=90, ge=1, le=3600)
    stop_on_error: bool = True


class DeploymentProbeRequest(BaseModel):
    repo_path: str = ""
    providers: list[str] = Field(default_factory=list)
    task_contract_probe: bool = False
    timeout_sec: int = Field(default=30, ge=1, le=300)


class SmokeE2ERequest(BaseModel):
    repo_path: str = ""
    timeout_sec: int = Field(default=30, ge=1, le=300)


class ProviderTaskProbeRequest(BaseModel):
    provider: str
    repo_path: str = ""
    timeout_sec: int = Field(default=30, ge=1, le=300)


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


def _input_uploads_dir() -> Path:
    root = _workbench_dir() / "input_uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _deployment_probes_dir() -> Path:
    root = _workbench_dir() / "deployment_probes"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _agent_run_dir(run_id: str) -> Path:
    value = _safe_segment(run_id, "run_id")
    return _agent_runs_dir() / value


def _task_agent_run_dir(task_run_id: str, step_id: str) -> Path:
    task_value = _safe_segment(task_run_id, "task_run_id")
    step_value = _safe_segment(step_id, "step_id")
    return _task_runs_dir() / task_value / "agent_runs" / step_value


def _safe_segment(value: str, label: str) -> str:
    value = value.strip()
    if not value or "/" in value or "\\" in value or ".." in value:
        raise HTTPException(status_code=400, detail=f"invalid {label}")
    return value


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


@router.get("/workflow-presets")
async def list_workflow_presets() -> dict[str, Any]:
    return {"items": builtin_workflow_presets()}


@router.get("/workflow-capabilities")
async def get_workflow_capabilities() -> dict[str, Any]:
    """Return the declarative workflow surface available to user-defined tasks."""
    return {
        "status": "ok",
        "input_types": sorted(ALLOWED_INPUT_TYPES),
        "input_resolvers": ["agent_mcp", "local", "manual"],
        "step_types": sorted(ALLOWED_STEP_TYPES),
        "output_types": [
            "json",
            "markdown",
            "text",
            "patch",
            "diff",
            "test_cases",
            "scope_report",
        ],
        "input_features": {
            "json_schema_validation": True,
            "file_copy_and_hash": True,
            "text_extraction_chunks": True,
            "agent_owned_mcp_inputs": True,
        },
        "output_features": {
            "json_schema_validation": True,
            "workflow_output_materialization": True,
            "semantic_case_import_from_outputs": True,
            "sha256_and_size_recorded": True,
        },
        "agent_cli_features": {
            "agent_owned_mcp_credentials": True,
            "provider_selection": True,
            "startup_probe": True,
            "required_artifacts_validation": True,
            "source_slice_second_turn": True,
        },
        "semantic_library_import_formats": ["json", "jsonl", "ndjson", "csv", "txt"],
        "artifact_contract": {
            "required_artifacts": "validated locally before outputs are accepted",
            "raw_output": "stored for audit but never accepted as evidence without artifacts",
            "workflow_outputs": "collected from declared outputs and checked before acceptance",
        },
    }


@router.get("/core-workflow-readiness")
async def get_core_workflow_readiness() -> dict[str, Any]:
    """Audit the four built-in workflow scenarios as executable contracts."""
    workflows = [_core_workflow_readiness_item(item) for item in builtin_workflow_presets()]
    required = [
        item for item in workflows
        if item.get("id") in {
            "module_analysis",
            "resource_leak_hunt",
            "mr_blackbox_test",
            "patch_impact_review",
        }
    ]
    missing_required = [
        item for item in required
        if item.get("status") != "ready"
    ]
    return {
        "status": "ready" if not missing_required else "incomplete",
        "summary": {
            "workflow_count": len(required),
            "missing_required": len(missing_required),
            "agent_step_count": sum(int(item.get("agent_step_count") or 0) for item in required),
            "output_count": sum(int(item.get("output_count") or 0) for item in required),
        },
        "workflows": required,
        "missing_required": missing_required,
        "notes": [
            "Readiness means preset structure, artifact contract, and output contract are declared.",
            "Runtime readiness still depends on provider startup probes and task acceptance audits.",
        ],
    }


@router.post("/workflow-presets/{preset_id}/install", status_code=201)
async def install_builtin_workflow_preset(preset_id: str) -> dict[str, Any]:
    try:
        workflow = install_workflow_preset(_workflow_store(), preset_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown workflow preset: {preset_id}")
    return _workflow_response(workflow.raw)


@router.post("/input-files/upload", status_code=201)
async def upload_workbench_input_file(
    file: UploadFile = File(...),
    input_id: str = Form(""),
) -> dict[str, Any]:
    filename = Path(file.filename or "input").name or "input"
    upload_id = f"input_{uuid.uuid4().hex}"
    upload_dir = _input_uploads_dir() / upload_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    data = await file.read()
    max_bytes = settings.coverage_max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"input file exceeds {settings.coverage_max_upload_mb}MB limit",
        )
    destination = upload_dir / filename
    destination.write_bytes(data)
    metadata = {
        "kind": "workbench_input_upload",
        "upload_id": upload_id,
        "input_id": input_id.strip(),
        "filename": filename,
        "content_type": file.content_type or "",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "path": str(destination),
        "input_payload": {"path": str(destination)},
    }
    (upload_dir / "upload_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return metadata


@router.get("/provider-capabilities")
async def list_provider_capabilities() -> dict[str, Any]:
    """Return a side-effect-free capability matrix for Workbench Agent routing."""
    await apply_persisted_agent_provider_settings()
    providers = _codetalk_provider_matrix_items() + [
        _agent_cli_provider_matrix_item(provider_id, spec)
        for provider_id, spec in external_agent_provider_specs().items()
    ]
    providers.append(_fast_context_provider_matrix_item())
    providers.sort(key=lambda item: (str(item.get("owner")), str(item.get("provider"))))
    return {
        "status": "ok",
        "providers": providers,
        "notes": [
            "Agent CLI providers may call their own MCP tools with their own credentials.",
            "CodeTalk validates Agent artifacts before materializing evidence.",
            "Unavailable providers are non-blocking for workflow preparation.",
            "CodeTalk-callable providers and Agent-owned providers have separate credential boundaries.",
        ],
    }


@router.get("/system-audit")
async def get_workbench_system_audit() -> dict[str, Any]:
    """Return a machine-readable readiness audit for the Workbench control plane."""
    return _build_workbench_system_audit()


@router.post("/deployment-probe")
async def run_workbench_deployment_probe(payload: DeploymentProbeRequest) -> dict[str, Any]:
    """Run startup probes for Agent CLI providers and persist deployment evidence."""
    await apply_persisted_agent_provider_settings()
    provider_specs = external_agent_provider_specs()
    requested = [
        str(provider).strip()
        for provider in payload.providers
        if str(provider).strip()
    ]
    provider_ids = requested or list(provider_specs)
    provider_ids = [
        provider for provider in provider_ids
        if provider in provider_specs
    ]
    started_at = datetime.now(timezone.utc)
    results = await asyncio.gather(*[
        _run_deployment_probe_provider(provider, payload.repo_path)
        for provider in provider_ids
    ])
    if payload.task_contract_probe:
        task_probe_results = await asyncio.gather(*[
            _run_deployment_task_probe_provider(
                provider,
                repo_path=payload.repo_path,
                timeout_sec=payload.timeout_sec,
            )
            for provider in provider_ids
        ])
        task_probe_by_provider = {
            str(item.get("provider") or ""): item
            for item in task_probe_results
            if isinstance(item, dict)
        }
        results = [
            {
                **item,
                "task_probe": task_probe_by_provider.get(str(item.get("provider") or "")),
            }
            for item in results
        ]
    completed_at = datetime.now(timezone.utc)
    healthy = [item for item in results if item.get("healthy")]
    failed = [item for item in results if not item.get("healthy")]
    task_probe_items = [
        item.get("task_probe")
        for item in results
        if isinstance(item.get("task_probe"), dict)
    ]
    task_probe_ready = [
        item for item in task_probe_items
        if isinstance(item, dict) and item.get("status") == "ready"
    ]
    task_probe_failed = [
        item for item in task_probe_items
        if isinstance(item, dict) and item.get("status") != "ready"
    ]
    probe_id = f"deploy_probe_{uuid.uuid4().hex}"
    status = "healthy" if results and not failed else "degraded"
    if payload.task_contract_probe and task_probe_failed:
        status = "degraded"
    if not results:
        status = "unavailable"
    artifact_dir = _deployment_probes_dir()
    artifact_path = artifact_dir / f"{probe_id}.json"
    response = {
        "probe_id": probe_id,
        "status": status,
        "repo_path": payload.repo_path,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_ms": int((completed_at - started_at).total_seconds() * 1000),
        "summary": {
            "provider_count": len(results),
            "healthy_count": len(healthy),
            "failed_count": len(failed),
            "task_contract_probe": payload.task_contract_probe,
            "task_ready_count": len(task_probe_ready),
            "task_failed_count": len(task_probe_failed),
        },
        "providers": results,
        "artifact": {
            "path": str(artifact_path),
            "latest_path": str(artifact_dir / "deployment_probe_latest.json"),
        },
    }
    evidence_ids = _materialize_deployment_probe_evidence(response)
    response["evidence_ids"] = evidence_ids
    response["evidence_count"] = len(evidence_ids)
    _write_json(artifact_path, response)
    _write_json(artifact_dir / "deployment_probe_latest.json", response)
    response["artifact"]["sha256"] = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    response["artifact"]["size_bytes"] = artifact_path.stat().st_size
    _write_json(artifact_path, response)
    _write_json(artifact_dir / "deployment_probe_latest.json", response)
    return response


@router.post("/provider-task-probe")
async def run_workbench_provider_task_probe(payload: ProviderTaskProbeRequest) -> dict[str, Any]:
    """Execute a real configured provider through the task harness artifact contract."""
    await apply_persisted_agent_provider_settings()
    provider = str(payload.provider or "").strip()
    if not provider:
        raise HTTPException(status_code=422, detail="provider is required")
    try:
        return _run_provider_task_probe_core(
            provider=provider,
            repo_path=payload.repo_path,
            timeout_sec=payload.timeout_sec,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/semantic-cases", status_code=201)
async def upsert_semantic_case(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        semantic_id = _semantic_store().upsert_case(payload)
    except SemanticCaseValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"semantic_id": semantic_id, "case_id": str(payload.get("case_id") or "")}


@router.post("/semantic-cases/import", status_code=201)
async def import_semantic_cases(payload: Any = Body(...)) -> dict[str, Any]:
    try:
        return _semantic_store().import_cases(payload)
    except SemanticCaseValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/semantic-cases/import-file", status_code=201)
async def import_semantic_case_file(
    file: UploadFile = File(...),
    defaults_json: str = Form("{}"),
) -> dict[str, Any]:
    try:
        defaults = json.loads(defaults_json or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"invalid defaults_json: {exc.msg}")
    if not isinstance(defaults, dict):
        raise HTTPException(status_code=422, detail="defaults_json must be an object")
    try:
        return _semantic_store().import_case_file(
            await file.read(),
            filename=Path(file.filename or "semantic_cases").name,
            defaults=defaults,
        )
    except (SemanticCaseValidationError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


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


@router.get("/memory/evidence/{evidence_id}/source-slices")
async def list_memory_source_slices(evidence_id: str) -> dict[str, Any]:
    return {"items": [asdict(item) for item in _memory_store().list_source_slices(evidence_id)]}


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


@router.post("/agent-runs/{run_id}/execute")
async def execute_agent_run(
    run_id: str,
    payload: AgentRunExecuteRequest,
) -> dict[str, Any]:
    artifact_dir = _agent_run_dir(run_id)
    if not (artifact_dir / "agent_run.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown agent run: {run_id}")
    try:
        result = AgentRunHarness(artifact_dir).execute_run(
            run_id,
            timeout_sec=payload.timeout_sec,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return asdict(result)


@router.post("/task-runs/{task_run_id}/agent-runs/{step_id}/execute")
async def execute_task_agent_run(
    task_run_id: str,
    step_id: str,
    payload: AgentRunExecuteRequest,
) -> dict[str, Any]:
    artifact_dir = _task_agent_run_dir(task_run_id, step_id)
    agent_run_path = artifact_dir / "agent_run.json"
    if not agent_run_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Unknown task agent run: {task_run_id}/{step_id}",
        )
    try:
        import json

        run_payload = json.loads(agent_run_path.read_text(encoding="utf-8"))
        run_id = str(run_payload.get("run_id") or "")
        result = AgentRunHarness(artifact_dir).execute_run(
            run_id,
            timeout_sec=payload.timeout_sec,
        )
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid agent_run.json")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return asdict(result)


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


@router.post("/task-runs/{task_run_id}/agent-runs/{step_id}/validate-mr-artifacts")
async def validate_task_agent_run_mr_artifacts(
    task_run_id: str,
    step_id: str,
    payload: ValidateMrArtifactsRequest,
) -> dict[str, Any]:
    artifact_dir = _task_agent_run_dir(task_run_id, step_id)
    if not (artifact_dir / "agent_run.json").exists():
        raise HTTPException(
            status_code=404,
            detail=f"Unknown task agent run: {task_run_id}/{step_id}",
        )
    result = ArtifactValidationHarness(artifact_dir).validate_mr_artifacts(
        required_artifacts=payload.required_artifacts,
    )
    return asdict(result)


@router.post("/task-runs/{task_run_id}/agent-runs/{step_id}/materialize-evidence")
async def materialize_task_agent_run_evidence(
    task_run_id: str,
    step_id: str,
    payload: MaterializeEvidenceRequest,
) -> dict[str, Any]:
    artifact_dir = _task_agent_run_dir(task_run_id, step_id)
    if not (artifact_dir / "agent_run.json").exists():
        raise HTTPException(
            status_code=404,
            detail=f"Unknown task agent run: {task_run_id}/{step_id}",
        )
    try:
        task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")

    validation = ArtifactValidationHarness(artifact_dir).validate_mr_artifacts(
        required_artifacts=payload.required_artifacts,
    )
    if validation.status != "ok":
        return {
            "status": validation.status,
            "validation": asdict(validation),
            "evidence_count": 0,
            "evidence_ids": [],
        }
    evidence_ids = _materialize_mr_artifact_evidence(
        task_run=task_run,
        step_id=step_id,
        artifact_dir=artifact_dir,
        object_text=payload.object_text,
        required_artifacts=payload.required_artifacts,
    )
    return {
        "status": "ok",
        "validation": asdict(validation),
        "evidence_count": len(evidence_ids),
        "evidence_ids": evidence_ids,
    }


@router.post("/task-runs/{task_run_id}/execute")
async def execute_task_run_workflow(
    task_run_id: str,
    payload: TaskRunExecuteRequest,
) -> dict[str, Any]:
    try:
        return _execute_task_run_with_closure(task_run_id=task_run_id, payload=payload)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _execute_task_run_with_closure(
    *,
    task_run_id: str,
    payload: TaskRunExecuteRequest,
) -> dict[str, Any]:
    try:
        result = WorkbenchWorkflowRunner(_task_runs_dir()).execute_task_run(
            task_run_id,
            timeout_sec=payload.timeout_sec,
            stop_on_error=payload.stop_on_error,
        )
        task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    except KeyError:
        raise
    response = asdict(result)
    response["evidence_materialization"] = _materialize_task_run_outputs_if_available(
        task_run=task_run,
    )
    refreshed = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    response["semantic_output_import"] = (
        response["evidence_materialization"].get("semantic_output_import") or {}
    )
    acceptance = _build_task_acceptance_audit(refreshed)
    task_dir = Path(refreshed.artifact_dir)
    _write_json(task_dir / "task_acceptance_audit.json", acceptance)
    write_task_artifact_manifest(task_dir, task_run_id=refreshed.task_run_id)
    response["acceptance_audit"] = acceptance
    return response


def _materialize_task_run_outputs_if_available(*, task_run: Any) -> dict[str, Any]:
    task_dir = Path(task_run.artifact_dir)
    workflow_outputs_path = task_dir / "workflow_outputs.json"
    workflow_outputs = _read_json(workflow_outputs_path)
    if not isinstance(workflow_outputs, dict):
        return {
            "status": "skipped",
            "reason": "workflow_outputs_missing",
            "evidence_count": 0,
            "evidence_ids": [],
            "rejected_outputs": [],
        }
    evidence_ids, rejected = _materialize_workflow_output_evidence(
        task_run=task_run,
        workflow_outputs=workflow_outputs,
    )
    result = {
        "status": "ok" if not rejected else "partial",
        "evidence_count": len(evidence_ids),
        "evidence_ids": evidence_ids,
        "rejected_outputs": rejected,
    }
    _write_workflow_output_materialization_artifact(
        task_run=task_run,
        workflow_outputs_path=workflow_outputs_path,
        workflow_outputs=workflow_outputs,
        result=result,
    )
    result["semantic_output_import"] = _auto_import_semantic_outputs_if_available(
        task_run=task_run,
    )
    write_task_artifact_manifest(task_dir, task_run_id=task_run.task_run_id)
    return result


def _auto_import_semantic_outputs_if_available(*, task_run: Any) -> dict[str, Any]:
    task_dir = Path(task_run.artifact_dir)
    workflow_outputs = _read_json(task_dir / "workflow_outputs.json")
    if not isinstance(workflow_outputs, dict):
        return {
            "status": "skipped",
            "reason": "workflow_outputs_missing",
            "imported_count": 0,
            "rejected_count": 0,
            "imported": [],
            "rejected": [],
            "source_refs": [],
        }

    output_configs = _semantic_import_output_configs(task_run)
    if not output_configs:
        return {
            "status": "skipped",
            "reason": "no_semantic_import_outputs",
            "imported_count": 0,
            "rejected_count": 0,
            "imported": [],
            "rejected": [],
            "source_refs": [],
        }

    combined = _empty_semantic_import_result(source_ref=f"task_run:{task_run.task_run_id}")
    for output_id, defaults in output_configs:
        result = _import_workflow_outputs_as_semantic_cases(
            task_run=task_run,
            workflow_outputs=workflow_outputs,
            output_ids=[output_id],
            defaults=defaults,
        )
        _merge_semantic_import_result(combined, result)

    combined["status"] = _semantic_import_status(combined)
    _write_semantic_output_import_artifact(
        task_run=task_run,
        mode="auto",
        result=combined,
    )
    write_task_artifact_manifest(task_dir, task_run_id=task_run.task_run_id)
    return combined


def _semantic_import_output_configs(task_run: Any) -> list[tuple[str, dict[str, Any]]]:
    configs: list[tuple[str, dict[str, Any]]] = []
    workflow_snapshot = getattr(task_run, "workflow_snapshot", {}) or {}
    for output in workflow_snapshot.get("outputs") or []:
        if not isinstance(output, dict):
            continue
        output_id = str(output.get("id") or "").strip()
        if not output_id:
            continue
        semantic_import = output.get("semantic_import")
        if semantic_import is True:
            configs.append((output_id, {}))
            continue
        if not isinstance(semantic_import, dict):
            continue
        enabled = semantic_import.get("enabled", True)
        if enabled is False:
            continue
        defaults = semantic_import.get("defaults") or {}
        configs.append((output_id, dict(defaults) if isinstance(defaults, dict) else {}))
    return configs


def _empty_semantic_import_result(*, source_ref: str) -> dict[str, Any]:
    return {
        "status": "skipped",
        "imported_count": 0,
        "rejected_count": 0,
        "imported": [],
        "rejected": [],
        "source_ref": source_ref,
        "source_refs": [],
    }


def _merge_semantic_import_result(target: dict[str, Any], result: dict[str, Any]) -> None:
    imported = [item for item in result.get("imported") or [] if isinstance(item, dict)]
    rejected = [item for item in result.get("rejected") or [] if isinstance(item, dict)]
    source_refs = [str(item) for item in result.get("source_refs") or [] if str(item)]
    target["imported"].extend(imported)
    target["rejected"].extend(rejected)
    target["source_refs"] = _semantic_dedupe([
        *[str(item) for item in target.get("source_refs") or []],
        *source_refs,
    ])
    if len(target["source_refs"]) == 1:
        target["source_ref"] = target["source_refs"][0]
    target["imported_count"] = len(target["imported"])
    target["rejected_count"] = len(target["rejected"])


def _semantic_import_status(result: dict[str, Any]) -> str:
    imported_count = int(result.get("imported_count") or 0)
    rejected_count = int(result.get("rejected_count") or 0)
    if imported_count and not rejected_count:
        return "ok"
    if imported_count and rejected_count:
        return "partial"
    if rejected_count:
        return "failed"
    return "skipped"


def _write_semantic_output_import_artifact(
    *,
    task_run: Any,
    mode: str,
    result: dict[str, Any],
) -> None:
    task_dir = Path(task_run.artifact_dir)
    _write_json(task_dir / "semantic_output_import.json", {
        "mode": mode,
        "task_run_id": task_run.task_run_id,
        "workflow_id": task_run.workflow_id,
        "workspace_id": task_run.workspace_id,
        "repo_path": task_run.repo_path,
        "result": result,
    })


@router.post("/task-runs/{task_run_id}/materialize-outputs")
async def materialize_task_run_outputs(task_run_id: str) -> dict[str, Any]:
    try:
        task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")
    task_dir = Path(task_run.artifact_dir)
    workflow_outputs_path = task_dir / "workflow_outputs.json"
    workflow_outputs = _read_json(workflow_outputs_path)
    if not isinstance(workflow_outputs, dict):
        raise HTTPException(
            status_code=400,
            detail="workflow outputs have not been generated",
        )
    evidence_ids, rejected = _materialize_workflow_output_evidence(
        task_run=task_run,
        workflow_outputs=workflow_outputs,
    )
    result = {
        "status": "ok" if not rejected else "partial",
        "evidence_count": len(evidence_ids),
        "evidence_ids": evidence_ids,
        "rejected_outputs": rejected,
    }
    _write_workflow_output_materialization_artifact(
        task_run=task_run,
        workflow_outputs_path=workflow_outputs_path,
        workflow_outputs=workflow_outputs,
        result=result,
    )
    result["semantic_output_import"] = _auto_import_semantic_outputs_if_available(
        task_run=task_run,
    )
    write_task_artifact_manifest(task_dir, task_run_id=task_run.task_run_id)
    return result


@router.post("/task-runs/{task_run_id}/semantic-cases/import-outputs", status_code=201)
async def import_task_run_outputs_as_semantic_cases(
    task_run_id: str,
    payload: ImportSemanticOutputsRequest,
) -> dict[str, Any]:
    try:
        task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")
    task_dir = Path(task_run.artifact_dir)
    workflow_outputs = _read_json(task_dir / "workflow_outputs.json")
    if not isinstance(workflow_outputs, dict):
        raise HTTPException(
            status_code=400,
            detail="workflow outputs have not been generated",
        )
    result = _import_workflow_outputs_as_semantic_cases(
        task_run=task_run,
        workflow_outputs=workflow_outputs,
        output_ids=payload.output_ids,
        defaults=payload.defaults,
    )
    _write_json(task_dir / "semantic_output_import.json", {
        "mode": "manual",
        "task_run_id": task_run.task_run_id,
        "workflow_id": task_run.workflow_id,
        "workspace_id": task_run.workspace_id,
        "repo_path": task_run.repo_path,
        "result": result,
    })
    write_task_artifact_manifest(task_dir, task_run_id=task_run.task_run_id)
    return result


@router.get("/task-runs")
async def list_task_runs(
    workspace_id: str = "",
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    items = WorkbenchTaskRunStore(_task_runs_dir()).list(
        workspace_id=workspace_id or None,
        limit=limit,
    )
    return {"items": [asdict(item) for item in items]}


@router.post("/task-runs/smoke-e2e")
async def run_task_smoke_e2e(payload: SmokeE2ERequest) -> dict[str, Any]:
    """Run a self-contained Workbench task E2E through Agent harness and acceptance audit."""
    repo_path = str(payload.repo_path or "").strip() or str(_workbench_dir())
    repo = Path(repo_path).expanduser().resolve()
    if not repo.exists() or not repo.is_dir():
        raise HTTPException(status_code=422, detail=f"repo_path does not exist: {repo}")

    provider_id = "codetalk-smoke-agent"
    script_path = _ensure_smoke_agent_script()
    workflow = _smoke_e2e_workflow(provider_id)
    old_custom_providers = getattr(settings, "external_agent_custom_providers", [])
    settings.external_agent_custom_providers = _with_smoke_agent_provider(
        old_custom_providers,
        provider_id=provider_id,
        script_path=script_path,
    )
    try:
        _workflow_store().save_workflow(workflow)
        task_run = WorkbenchTaskRunPreparer(
            artifact_root=_task_runs_dir(),
            workflow_store=_workflow_store(),
            evidence_memory=_memory_store(),
            semantic_library=_semantic_store(),
        ).prepare(
            workflow_id=workflow["id"],
            workspace_id="codetalk-smoke",
            repo_path=str(repo),
            inputs={"analysis_object": "codetalk smoke e2e"},
        )
        execution = WorkbenchWorkflowRunner(_task_runs_dir()).execute_task_run(
            task_run.task_run_id,
            timeout_sec=payload.timeout_sec,
            stop_on_error=True,
        )
        refreshed = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run.task_run_id)
        acceptance = _build_task_acceptance_audit(refreshed)
        task_dir = Path(refreshed.artifact_dir)
        _write_json(task_dir / "task_acceptance_audit.json", acceptance)
        result = {
            "status": acceptance.get("status") or execution.status,
            "workflow_id": workflow["id"],
            "task_run_id": refreshed.task_run_id,
            "task_run": asdict(refreshed),
            "execution": asdict(execution),
            "acceptance_audit": acceptance,
        }
        smoke_artifact = task_dir / "smoke_e2e_result.json"
        result["artifact"] = {"path": str(smoke_artifact)}
        _write_json(smoke_artifact, result)
        write_task_artifact_manifest(task_dir, task_run_id=refreshed.task_run_id)
        result["artifact"]["sha256"] = hashlib.sha256(smoke_artifact.read_bytes()).hexdigest()
        result["artifact"]["size_bytes"] = smoke_artifact.stat().st_size
        _write_json(smoke_artifact, result)
        write_task_artifact_manifest(task_dir, task_run_id=refreshed.task_run_id)
        return result
    finally:
        settings.external_agent_custom_providers = old_custom_providers


@router.get("/task-runs/{task_run_id}")
async def get_task_run(task_run_id: str) -> dict[str, Any]:
    try:
        task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")
    return asdict(task_run)


@router.get("/task-runs/{task_run_id}/rerun-plan")
async def get_task_run_rerun_plan(task_run_id: str) -> dict[str, Any]:
    try:
        task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")
    path = Path(task_run.artifact_dir) / "task_rerun_plan.json"
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=404,
            detail="task rerun plan has not been generated",
        )
    return payload


@router.post("/task-runs/{task_run_id}/acceptance-audit")
async def create_task_run_acceptance_audit(task_run_id: str) -> dict[str, Any]:
    try:
        task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")
    task_dir = Path(task_run.artifact_dir)
    payload = _build_task_acceptance_audit(task_run)
    _write_json(task_dir / "task_acceptance_audit.json", payload)
    write_task_artifact_manifest(task_dir, task_run_id=task_run.task_run_id)
    return payload


@router.get("/task-runs/{task_run_id}/rerun-plan/validation")
async def validate_task_run_rerun_plan(task_run_id: str) -> dict[str, Any]:
    try:
        task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")
    path = Path(task_run.artifact_dir) / "task_rerun_plan.json"
    plan = _read_json(path)
    if not isinstance(plan, dict):
        raise HTTPException(
            status_code=404,
            detail="task rerun plan has not been generated",
        )
    return _validate_task_rerun_plan(task_run=task_run, plan=plan)


@router.get("/task-runs/{task_run_id}/rerun-plan/history")
async def get_task_run_rerun_history(task_run_id: str) -> dict[str, Any]:
    try:
        task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")
    history = _read_json(Path(task_run.artifact_dir) / "task_rerun_history.json")
    if not isinstance(history, dict):
        return {
            "task_run_id": task_run.task_run_id,
            "count": 0,
            "records": [],
        }
    records = history.get("records") if isinstance(history.get("records"), list) else []
    return {
        "task_run_id": str(history.get("task_run_id") or task_run.task_run_id),
        "count": len(records),
        "records": records,
    }


@router.post("/task-runs/{task_run_id}/rerun-plan/execute")
async def execute_task_run_rerun_plan(
    task_run_id: str,
    payload: TaskRunExecuteRequest,
) -> dict[str, Any]:
    try:
        task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")
    plan_path = Path(task_run.artifact_dir) / "task_rerun_plan.json"
    plan = _read_json(plan_path)
    if not isinstance(plan, dict):
        raise HTTPException(
            status_code=404,
            detail="task rerun plan has not been generated",
        )
    validation_before = _validate_task_rerun_plan(task_run=task_run, plan=plan)
    if not validation_before.get("can_rerun"):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "task rerun plan is not executable",
                "validation": validation_before,
            },
        )
    try:
        execution = WorkbenchWorkflowRunner(_task_runs_dir()).execute_task_run(
            task_run_id,
            timeout_sec=payload.timeout_sec,
            stop_on_error=payload.stop_on_error,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    refreshed_task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    evidence_materialization = _materialize_task_run_outputs_if_available(
        task_run=refreshed_task_run,
    )
    task_dir = Path(refreshed_task_run.artifact_dir)
    semantic_output_import = evidence_materialization.get("semantic_output_import") or {}
    acceptance = _build_task_acceptance_audit(refreshed_task_run)
    _write_json(task_dir / "task_acceptance_audit.json", acceptance)
    refreshed_plan = _read_json(Path(refreshed_task_run.artifact_dir) / "task_rerun_plan.json")
    validation_after = (
        _validate_task_rerun_plan(task_run=refreshed_task_run, plan=refreshed_plan)
        if isinstance(refreshed_plan, dict)
        else {}
    )
    result = {
        "status": "executed",
        "validation_before": validation_before,
        "execution": asdict(execution),
        "evidence_materialization": evidence_materialization,
        "semantic_output_import": semantic_output_import,
        "acceptance_audit": acceptance,
        "validation_after": validation_after,
    }
    _write_task_rerun_execution_artifacts(
        task_dir=task_dir,
        result=result,
    )
    write_task_artifact_manifest(
        task_dir,
        task_run_id=refreshed_task_run.task_run_id,
    )
    return result


@router.get("/task-runs/{task_run_id}/artifacts")
async def list_task_run_artifacts(task_run_id: str) -> dict[str, Any]:
    try:
        task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")
    task_dir = Path(task_run.artifact_dir)
    return {
        "task_run_id": task_run.task_run_id,
        "artifact_dir": str(task_dir),
        "artifacts": _artifact_manifest(task_dir),
    }


@router.get("/task-runs/{task_run_id}/artifacts/content/{artifact_path:path}")
async def get_task_run_artifact_content(
    task_run_id: str,
    artifact_path: str,
    max_chars: int = Query(50000, ge=1, le=200000),
) -> dict[str, Any]:
    try:
        task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")
    task_dir = Path(task_run.artifact_dir)
    path = _resolve_task_artifact_path(task_dir, artifact_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"Unknown artifact: {artifact_path}")
    return _artifact_content_payload(task_dir, path, max_chars=max_chars)


@router.post("/task-runs/prepare", status_code=201)
async def prepare_task_run(payload: PrepareTaskRunRequest) -> dict[str, Any]:
    await apply_persisted_agent_provider_settings()
    try:
        result = WorkbenchTaskRunPreparer(
            artifact_root=_task_runs_dir(),
            workflow_store=_workflow_store(),
            evidence_memory=_memory_store(),
            semantic_library=_semantic_store(),
        ).prepare(
            workflow_id=payload.workflow_id,
            workspace_id=payload.workspace_id,
            repo_path=payload.repo_path,
            inputs=payload.inputs,
            provider_override=payload.provider_override,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown workflow: {payload.workflow_id}")
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return asdict(result)


@router.post("/task-runs/run", status_code=201)
async def prepare_and_execute_task_run(payload: RunTaskRunRequest) -> dict[str, Any]:
    await apply_persisted_agent_provider_settings()
    try:
        prepared = WorkbenchTaskRunPreparer(
            artifact_root=_task_runs_dir(),
            workflow_store=_workflow_store(),
            evidence_memory=_memory_store(),
            semantic_library=_semantic_store(),
        ).prepare(
            workflow_id=payload.workflow_id,
            workspace_id=payload.workspace_id,
            repo_path=payload.repo_path,
            inputs=payload.inputs,
            provider_override=payload.provider_override,
        )
        execution = _execute_task_run_with_closure(
            task_run_id=prepared.task_run_id,
            payload=TaskRunExecuteRequest(
                timeout_sec=payload.timeout_sec,
                stop_on_error=payload.stop_on_error,
            ),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown workflow: {payload.workflow_id}")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    task_dir = Path(prepared.artifact_dir)
    return {
        "status": execution.get("status") or "unknown",
        "task_run_id": prepared.task_run_id,
        "workflow_id": prepared.workflow_id,
        "workspace_id": prepared.workspace_id,
        "task_run": asdict(prepared),
        "execution": execution,
        "evidence_materialization": execution.get("evidence_materialization") or {},
        "semantic_output_import": execution.get("semantic_output_import") or {},
        "acceptance_audit": execution.get("acceptance_audit") or {},
        "artifact": {
            "path": str(task_dir / "task_run.json"),
            "manifest_path": str(task_dir / "task_artifact_manifest.json"),
        },
    }


def _workflow_response(payload: dict[str, Any]) -> dict[str, Any]:
    response = dict(payload)
    response["audit"] = audit_workflow_definition(payload)
    return response


def _agent_cli_provider_matrix_item(provider_id: str, spec: Any) -> dict[str, Any]:
    command = split_agent_command(spec.command) if spec.command else []
    fallback_commands = [
        split_agent_command(command_text)
        for command_text in spec.fallback_commands
        if command_text
    ]
    status = "configured" if command else "missing_command"
    return {
        "provider": provider_id,
        "display_name": spec.display_name or provider_id,
        "owner": "agent_cli",
        "status": status,
        "non_blocking": True,
        "codetalk_callable": False,
        "agent_owned": True,
        "command": command,
        "fallback_commands": fallback_commands,
        "readonly_args": list(spec.readonly_args),
        "command_hint_env": spec.command_hint_env,
        "capabilities": external_agent_provider_capabilities(provider_id),
        "credential_boundary": (
            "Agent CLI owns its own MCP credentials and remote access; CodeTalk only "
            "passes task bundles and validates returned artifacts."
        ),
        "diagnostics": build_agent_cli_provider_diagnostics(provider_id, spec),
        "unavailable_behavior": (
            "Workflow preparation continues; execution records unavailable or failed "
            "Agent diagnostics without trusting unvalidated output."
        ),
    }


def _fast_context_provider_matrix_item() -> dict[str, Any]:
    enabled = bool(getattr(settings, "fast_context_enabled", False))
    bridge_enabled = bool(getattr(settings, "fast_context_backend_bridge_enabled", False))
    if not enabled:
        status = "disabled"
    elif not bridge_enabled:
        status = "bridge_disabled"
    else:
        status = "configured"
    return {
        "provider": "fast-context",
        "display_name": "fast-context",
        "owner": "codetalk_mcp_bridge",
        "status": status,
        "non_blocking": True,
        "codetalk_callable": status == "configured",
        "agent_owned": False,
        "command": [],
        "fallback_commands": [],
        "readonly_args": [],
        "command_hint_env": "",
        "capabilities": {
            "provider": "fast-context",
            "supports_mcp": True,
            "mcp_profiles": [],
            "supports_artifact_export": False,
            "supports_json_output": True,
            "prompt_transport": "mcp",
            "supports_source_discovery": True,
            "supports_call_graph": False,
            "supports_source_slices": False,
            "supports_black_box_terms": False,
        },
        "credential_boundary": (
            "CodeTalk can call this MCP only when the backend bridge exposes it; "
            "otherwise Agent CLIs may still have their own fast-context MCP."
        ),
        "diagnostics": {
            "owner": "codetalk_mcp_bridge",
            "status": status,
            "codetalk_callable": status == "configured",
            "health_endpoint": "",
            "startup_probe_endpoint": "",
            "credential_boundary": (
                "CodeTalk can call fast-context only through an exposed backend MCP bridge. "
                "Agent CLIs may still call their own MCP servers with their own credentials."
            ),
            "troubleshooting": [
                "If AGENTS.md requires fast-context but this bridge is disabled, CodeTalk records the gap and uses local search plus Agent CLI discovery.",
                "When an Agent CLI owns fast-context credentials, expose that requirement in the workflow task bundle instead of expecting CodeTalk to call it.",
            ],
        },
        "unavailable_behavior": (
            "CodeTalk records fast-context as unavailable and continues with local "
            "search, GitNexus/CGC, and Agent CLI providers."
        ),
    }


def _codetalk_provider_matrix_items() -> list[dict[str, Any]]:
    return list(build_codetalk_provider_snapshot().values())


def _build_workbench_system_audit() -> dict[str, Any]:
    workbench_dir = _workbench_dir()
    provider_matrix = _codetalk_provider_matrix_items() + [
        _agent_cli_provider_matrix_item(provider_id, spec)
        for provider_id, spec in external_agent_provider_specs().items()
    ]
    provider_matrix.append(_fast_context_provider_matrix_item())
    preset_ids = {str(item.get("id") or "") for item in builtin_workflow_presets()}
    required_preset_ids = {
        "module_analysis",
        "resource_leak_hunt",
        "mr_blackbox_test",
        "patch_impact_review",
    }
    checks = [
        _system_audit_check(
            check_id="workbench_data_dir",
            ok=workbench_dir.exists() and workbench_dir.is_dir(),
            severity="required",
            description="Workbench data directory exists",
            details={"path": str(workbench_dir)},
        ),
        _system_audit_check(
            check_id="workflow_store",
            ok=True,
            severity="required",
            description="Workflow store can be constructed",
            details={"path": str(_workbench_dir() / "workflows.db")},
        ),
        _system_audit_check(
            check_id="evidence_memory_store",
            ok=True,
            severity="required",
            description="Evidence Memory store can be constructed",
            details={"path": str(_workbench_dir() / "evidence_memory.db")},
        ),
        _system_audit_check(
            check_id="semantic_library_store",
            ok=True,
            severity="required",
            description="Test Semantic Library store can be constructed",
            details={"path": str(_workbench_dir() / "test_semantics.db")},
        ),
        _system_audit_check(
            check_id="workflow_presets",
            ok=required_preset_ids.issubset(preset_ids),
            severity="required",
            description="Required editable workflow presets are registered",
            details={
                "required": sorted(required_preset_ids),
                "available": sorted(preset_ids),
            },
        ),
        _system_audit_check(
            check_id="provider_capability_matrix",
            ok=bool(provider_matrix),
            severity="required",
            description="Provider capability matrix is available",
            details={
                "provider_count": len(provider_matrix),
                "providers": [str(item.get("provider") or "") for item in provider_matrix],
            },
        ),
        _system_audit_check(
            check_id="agent_cli_provider_registry",
            ok=any(item.get("owner") == "agent_cli" for item in provider_matrix),
            severity="required",
            description="Agent CLI providers are registered for harness execution",
            details={
                "providers": [
                    str(item.get("provider") or "")
                    for item in provider_matrix
                    if item.get("owner") == "agent_cli"
                ],
            },
        ),
        _system_audit_check(
            check_id="task_runs_dir",
            ok=_task_runs_dir().exists(),
            severity="required",
            description="Task run artifact directory is available",
            details={"path": str(_task_runs_dir())},
        ),
        _system_audit_check(
            check_id="agent_runs_dir",
            ok=_agent_runs_dir().exists(),
            severity="required",
            description="Standalone Agent run artifact directory is available",
            details={"path": str(_agent_runs_dir())},
        ),
        _system_audit_check(
            check_id="task_acceptance_audit_api",
            ok=True,
            severity="required",
            description="Task-level acceptance audit API is registered",
            details={"endpoint": "POST /api/workbench/task-runs/{task_run_id}/acceptance-audit"},
        ),
        _agent_cli_launch_readiness_check(provider_matrix),
        _latest_deployment_task_probe_check(),
        _system_audit_check(
            check_id="external_agent_sandbox",
            ok=False,
            severity="recommended",
            description="OS-level sandbox for external Agent CLI is not implemented in this phase",
            details={
                "residual_risk": (
                    "Current controls are prompt-level readonly rules, process timeouts, "
                    "provider diagnostics, and local evidence validation."
                )
            },
        ),
    ]
    required_checks = [item for item in checks if item["severity"] == "required"]
    missing_required = [
        item for item in required_checks
        if item["status"] not in {"ok", "accepted"}
    ]
    missing_recommended = [
        item for item in checks
        if item["severity"] == "recommended" and item["status"] not in {"ok", "accepted"}
    ]
    return {
        "status": "ready" if not missing_required else "incomplete",
        "runtime_status": (
            "degraded"
            if missing_required or _has_missing_agent_cli_launch_readiness(checks)
            else "healthy"
        ),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "required_checks": len(required_checks),
            "missing_required": len(missing_required),
            "recommended_checks": len(checks) - len(required_checks),
            "missing_recommended": len(missing_recommended),
        },
        "checks": checks,
        "missing_required": missing_required,
        "missing_recommended": missing_recommended,
        "notes": [
            "This audits the Workbench control plane, not a real intranet Agent CLI E2E run.",
            "Run provider startup probes and task-level acceptance audits before marking a deployment healthy.",
        ],
    }


def _core_workflow_readiness_item(preset: dict[str, Any]) -> dict[str, Any]:
    definition = preset.get("definition") if isinstance(preset.get("definition"), dict) else {}
    workflow_id = str(definition.get("id") or preset.get("id") or "")
    inputs = [item for item in definition.get("inputs") or [] if isinstance(item, dict)]
    steps = [item for item in definition.get("steps") or [] if isinstance(item, dict)]
    outputs = [item for item in definition.get("outputs") or [] if isinstance(item, dict)]
    agent_steps = [item for item in steps if item.get("type") == "agent_task"]
    builtin_steps = [
        str(item.get("id") or "")
        for item in steps
        if item.get("type") != "agent_task" and str(item.get("id") or "")
    ]
    required_artifacts = _unique_preserve_order(
        str(artifact)
        for step in agent_steps
        for artifact in step.get("required_artifacts") or []
        if str(artifact).strip()
    )
    missing: list[dict[str, str]] = []
    if not inputs:
        missing.append({"field": "inputs", "reason": "no inputs declared"})
    if not agent_steps:
        missing.append({"field": "steps", "reason": "no agent_task declared"})
    if not outputs:
        missing.append({"field": "outputs", "reason": "no outputs declared"})
    for step in agent_steps:
        step_id = str(step.get("id") or "")
        if not str(step.get("provider") or "").strip():
            missing.append({"field": f"steps.{step_id}.provider", "reason": "missing provider"})
        if not step.get("required_artifacts"):
            missing.append({
                "field": f"steps.{step_id}.required_artifacts",
                "reason": "missing required artifacts",
            })
    audit = audit_workflow_definition(definition)
    warnings = [
        item for item in audit.get("warnings") or []
        if isinstance(item, dict)
    ]
    agent_mcp_required = any(
        str(item.get("resolver") or "") == "agent_mcp"
        for item in inputs
    )
    return {
        "id": workflow_id,
        "name": str(definition.get("name") or preset.get("name") or workflow_id),
        "scenario": _core_workflow_scenario(workflow_id),
        "status": "ready" if not missing else "incomplete",
        "description": str(preset.get("description") or ""),
        "input_count": len(inputs),
        "required_inputs": [
            str(item.get("id") or "")
            for item in inputs
            if bool(item.get("required", False)) and str(item.get("id") or "")
        ],
        "agent_step_count": len(agent_steps),
        "agent_steps": [
            {
                "id": str(item.get("id") or ""),
                "provider": str(item.get("provider") or ""),
                "mcp_profile": str(item.get("mcp_profile") or ""),
                "required_artifacts": [
                    str(artifact) for artifact in item.get("required_artifacts") or []
                ],
            }
            for item in agent_steps
        ],
        "agent_mcp_required": agent_mcp_required,
        "builtin_steps": builtin_steps,
        "required_artifacts": required_artifacts,
        "output_count": len(outputs),
        "outputs": [
            {
                "id": str(item.get("id") or ""),
                "type": str(item.get("type") or ""),
                "from": str(item.get("from") or item.get("source") or ""),
                "artifact": str(item.get("artifact") or item.get("path") or ""),
                "has_schema": isinstance(item.get("schema") or item.get("json_schema"), dict),
            }
            for item in outputs
        ],
        "missing_required": missing,
        "warnings": warnings,
    }


async def _run_deployment_probe_provider(provider: str, repo_path: str) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    try:
        result = await probe_external_agent_startup(provider, repo_path=repo_path or None)
    except Exception as exc:
        completed_at = datetime.now(timezone.utc)
        return {
            "provider": provider,
            "healthy": False,
            "status": "error",
            "message": str(exc),
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_ms": int((completed_at - started_at).total_seconds() * 1000),
        }
    completed_at = datetime.now(timezone.utc)
    if not isinstance(result, dict):
        result = {
            "provider": provider,
            "healthy": False,
            "status": "error",
            "message": "startup probe returned non-object result",
        }
    item = dict(result)
    item.setdefault("provider", provider)
    item["healthy"] = bool(item.get("healthy", False))
    item["status"] = str(item.get("status") or ("ok" if item["healthy"] else "error"))
    item["message"] = str(item.get("message") or "")
    item["started_at"] = started_at.isoformat()
    item["completed_at"] = completed_at.isoformat()
    item["duration_ms"] = int((completed_at - started_at).total_seconds() * 1000)
    return item


async def _run_deployment_task_probe_provider(
    provider: str,
    *,
    repo_path: str,
    timeout_sec: int,
) -> dict[str, Any]:
    try:
        return _run_provider_task_probe_core(
            provider=provider,
            repo_path=repo_path,
            timeout_sec=timeout_sec,
        )
    except Exception as exc:
        return {
            "provider": provider,
            "status": "error",
            "message": str(exc),
            "summary": {
                "execution_status": "not_started",
                "task_contract_status": "error",
                "missing_artifacts": ["agent_task_probe.json"],
            },
        }


def _run_provider_task_probe_core(
    *,
    provider: str,
    repo_path: str,
    timeout_sec: int,
) -> dict[str, Any]:
    spec = external_agent_provider_spec(provider)
    if spec is None:
        raise ValueError(f"Unknown provider: {provider}")
    if not str(spec.command or "").strip():
        raise ValueError(f"Provider has no configured command: {provider}")
    resolved_repo_path = str(repo_path or "").strip() or str(_workbench_dir())
    repo = Path(resolved_repo_path).expanduser().resolve()
    if not repo.exists() or not repo.is_dir():
        raise ValueError(f"repo_path does not exist: {repo}")

    workflow = _provider_task_probe_workflow(provider)
    _workflow_store().save_workflow(workflow)
    task_run = WorkbenchTaskRunPreparer(
        artifact_root=_task_runs_dir(),
        workflow_store=_workflow_store(),
        evidence_memory=_memory_store(),
        semantic_library=_semantic_store(),
    ).prepare(
        workflow_id=workflow["id"],
        workspace_id="codetalk-provider-probe",
        repo_path=str(repo),
        inputs={
            "analysis_object": "codetalk provider task probe",
            "provider": provider,
        },
    )
    execution = WorkbenchWorkflowRunner(_task_runs_dir()).execute_task_run(
        task_run.task_run_id,
        timeout_sec=timeout_sec,
        stop_on_error=True,
    )
    refreshed = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run.task_run_id)
    acceptance = _build_task_acceptance_audit(refreshed)
    task_dir = Path(refreshed.artifact_dir)
    _write_json(task_dir / "task_acceptance_audit.json", acceptance)
    required_artifacts = ["agent_task_probe.json"]
    step_result = _first_step_result(execution.step_results, step_id="agent_task_probe")
    validation = (
        step_result.get("validation")
        if isinstance(step_result.get("validation"), dict)
        else {}
    )
    contract_status = "ok" if validation.get("status") == "ok" else "failed"
    status = (
        "ready"
        if execution.status == "completed" and acceptance.get("status") == "ready"
        else "degraded"
    )
    result = {
        "status": status,
        "provider": provider,
        "workflow_id": workflow["id"],
        "task_run_id": refreshed.task_run_id,
        "task_run": asdict(refreshed),
        "execution": asdict(execution),
        "acceptance_audit": acceptance,
        "contract": {
            "step_id": "agent_task_probe",
            "required_artifacts": required_artifacts,
            "validation": validation,
        },
        "summary": {
            "execution_status": execution.status,
            "step_status": str(step_result.get("status") or ""),
            "task_contract_status": contract_status,
            "missing_required": acceptance.get("summary", {}).get("missing_required", 0),
            "missing_artifacts": validation.get("missing") or [],
        },
    }
    artifact_path = task_dir / "provider_task_probe_result.json"
    result["artifact"] = {"path": str(artifact_path)}
    _write_json(artifact_path, result)
    write_task_artifact_manifest(task_dir, task_run_id=refreshed.task_run_id)
    result["artifact"]["sha256"] = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    result["artifact"]["size_bytes"] = artifact_path.stat().st_size
    _write_json(artifact_path, result)
    write_task_artifact_manifest(task_dir, task_run_id=refreshed.task_run_id)
    return result


def _materialize_deployment_probe_evidence(response: dict[str, Any]) -> list[str]:
    store = _memory_store()
    probe_id = str(response.get("probe_id") or "")
    workspace_id = "codetalk-deployment"
    repo_path = str(response.get("repo_path") or "")
    run_id = store.record_analysis_run(
        workspace_id=workspace_id,
        repo_path=repo_path,
        object_text=f"deployment probe {probe_id}",
        workflow_id="workbench_deployment_probe",
        status=str(response.get("status") or "unknown"),
        run_id=f"deployment_probe:{probe_id}" if probe_id else None,
    )
    artifact = response.get("artifact") if isinstance(response.get("artifact"), dict) else {}
    summary = response.get("summary") if isinstance(response.get("summary"), dict) else {}
    evidence_ids = [
        store.upsert_evidence_item(
            run_id=run_id,
            workspace_id=workspace_id,
            kind="deployment_probe",
            subject_key=probe_id or "latest",
            status="accepted" if response.get("status") in {"healthy", "degraded"} else "rejected",
            source="deployment_probe",
            path=str(artifact.get("path") or ""),
            reason=(
                f"deployment probe {response.get('status')}; "
                f"healthy {summary.get('healthy_count', 0)}/{summary.get('provider_count', 0)}; "
                f"task ready {summary.get('task_ready_count', 0)}/{summary.get('provider_count', 0)}"
            ),
            confidence=1.0,
            text=json.dumps(
                {
                    "probe_id": probe_id,
                    "status": response.get("status"),
                    "summary": summary,
                    "providers": [
                        str(item.get("provider") or item.get("tool") or "")
                        for item in response.get("providers") or []
                        if isinstance(item, dict)
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            provenance={
                "probe_id": probe_id,
                "artifact_path": str(artifact.get("path") or ""),
                "latest_artifact_path": str(artifact.get("latest_path") or ""),
                "summary": summary,
            },
        )
    ]
    for provider in response.get("providers") or []:
        if not isinstance(provider, dict) or not isinstance(provider.get("task_probe"), dict):
            continue
        task_probe = provider["task_probe"]
        provider_id = str(provider.get("provider") or provider.get("tool") or "")
        task_summary = (
            task_probe.get("summary")
            if isinstance(task_probe.get("summary"), dict)
            else {}
        )
        task_artifact = (
            task_probe.get("artifact")
            if isinstance(task_probe.get("artifact"), dict)
            else {}
        )
        evidence_ids.append(store.upsert_evidence_item(
            run_id=run_id,
            workspace_id=workspace_id,
            kind="provider_task_probe",
            subject_key=f"{provider_id}:agent_task_probe",
            status="accepted" if task_probe.get("status") == "ready" else "rejected",
            source="deployment_probe",
            path=str(task_artifact.get("path") or ""),
            symbol=provider_id,
            reason=(
                f"provider_task_probe {provider_id} {task_probe.get('status')}; "
                f"contract {task_summary.get('task_contract_status', 'unknown')}"
            ),
            confidence=1.0 if task_probe.get("status") == "ready" else 0.2,
            text=json.dumps(
                {
                    "provider_task_probe": provider_id,
                    "status": task_probe.get("status"),
                    "summary": task_summary,
                    "task_run_id": task_probe.get("task_run_id"),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            provenance={
                "provider": provider_id,
                "probe_id": probe_id,
                "task_probe_status": task_probe.get("status"),
                "task_run_id": task_probe.get("task_run_id"),
                "artifact_path": str(task_artifact.get("path") or ""),
                "summary": task_summary,
            },
        ))
    return evidence_ids


def _ensure_smoke_agent_script() -> Path:
    script_path = _workbench_dir() / "smoke_agent.py"
    script_path.write_text(
        "\n".join([
            "import json",
            "import os",
            "import pathlib",
            "import sys",
            "",
            "payload = json.loads(sys.stdin.read() or '{}')",
            "artifact_dir = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
            "bundle = payload.get('task_bundle') or {}",
            "query = bundle.get('context_bundle', {}).get('query') or 'codetalk smoke e2e'",
            "source_scope = {",
            "    'query': query,",
            "    'files': [{'path': 'smoke/source.c', 'reason': 'smoke harness synthetic source', 'validated': True}],",
            "    'symbols': [{'name': 'codetalk_smoke_entry', 'file': 'smoke/source.c'}],",
            "}",
            "evidence_cards = [{",
            "    'title': 'Smoke E2E evidence',",
            "    'kind': 'source_scope',",
            "    'path': 'smoke/source.c',",
            "    'reason': 'Generated by codetalk-smoke-agent to validate Agent Run Harness artifact flow.',",
            "}]",
            "(artifact_dir / 'source_scope.json').write_text(json.dumps(source_scope), encoding='utf-8')",
            "(artifact_dir / 'evidence_cards.json').write_text(json.dumps(evidence_cards), encoding='utf-8')",
            "print(json.dumps({'status': 'ok', 'raw_summary': 'codetalk_smoke_e2e_ok'}))",
            "",
        ]),
        encoding="utf-8",
    )
    return script_path


def _smoke_e2e_workflow(provider_id: str) -> dict[str, Any]:
    return {
        "id": "codetalk_smoke_e2e",
        "name": "CodeTalk Smoke E2E",
        "version": 1,
        "inputs": [
            {"id": "analysis_object", "type": "free_text", "required": True},
        ],
        "steps": [
            {
                "id": "discover_scope",
                "type": "agent_task",
                "provider": provider_id,
                "goal": "Produce smoke source scope and evidence artifacts.",
                "required_artifacts": ["source_scope.json", "evidence_cards.json"],
            },
            {"id": "validate_evidence", "type": "evidence_validate"},
            {"id": "render_report", "type": "report_render"},
        ],
        "outputs": [
            {
                "id": "scope",
                "type": "json",
                "from": "discover_scope",
                "artifact": "source_scope.json",
            },
            {
                "id": "evidence_cards",
                "type": "json",
                "from": "discover_scope",
                "artifact": "evidence_cards.json",
            },
            {"id": "report", "type": "markdown", "from": "render_report"},
        ],
    }


def _provider_task_probe_workflow(provider_id: str) -> dict[str, Any]:
    safe_provider = "".join(
        char if char.isalnum() else "_"
        for char in provider_id.lower()
    ).strip("_") or "agent"
    return {
        "id": f"codetalk_provider_task_probe_{safe_provider}",
        "name": f"CodeTalk Provider Task Probe: {provider_id}",
        "version": 1,
        "inputs": [
            {"id": "analysis_object", "type": "free_text", "required": True},
            {"id": "provider", "type": "free_text", "required": True},
        ],
        "steps": [
            {
                "id": "agent_task_probe",
                "type": "agent_task",
                "provider": provider_id,
                "goal": (
                    "Validate that this Agent CLI can receive the CodeTalk task bundle "
                    "and write the required artifact named agent_task_probe.json. The "
                    "artifact must be JSON with status, provider, and observed inputs. "
                    "Do not modify repository files."
                ),
                "required_artifacts": ["agent_task_probe.json"],
            },
            {"id": "validate_evidence", "type": "evidence_validate"},
            {"id": "render_report", "type": "report_render"},
        ],
        "outputs": [
            {
                "id": "agent_task_probe",
                "type": "json",
                "from": "agent_task_probe",
                "artifact": "agent_task_probe.json",
            },
            {"id": "report", "type": "markdown", "from": "render_report"},
        ],
    }


def _with_smoke_agent_provider(
    current: Any,
    *,
    provider_id: str,
    script_path: Path,
) -> list[dict[str, Any]]:
    providers: list[dict[str, Any]] = []
    if isinstance(current, list):
        providers.extend(item for item in current if isinstance(item, dict))
    elif isinstance(current, str) and current.strip():
        try:
            parsed = json.loads(current)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            providers.extend(item for item in parsed if isinstance(item, dict))
        elif isinstance(parsed, dict):
            providers.append(parsed)
    providers = [
        item for item in providers
        if str(item.get("id") or item.get("provider") or "") != provider_id
    ]
    providers.append({
        "id": provider_id,
        "command": f'"{sys.executable}" "{script_path}"',
        "prompt_transport": "stdin",
        "supports_artifact_export": True,
        "supports_json_output": True,
    })
    return providers


def _first_step_result(step_results: list[Any], *, step_id: str) -> dict[str, Any]:
    for item in step_results:
        if isinstance(item, dict) and str(item.get("step_id") or "") == step_id:
            return item
    return {}


def _core_workflow_scenario(workflow_id: str) -> str:
    return {
        "module_analysis": "module_analysis",
        "resource_leak_hunt": "risk_hunt",
        "mr_blackbox_test": "mr_blackbox_test",
        "patch_impact_review": "patch_impact_review",
    }.get(workflow_id, workflow_id or "workflow")


def _unique_preserve_order(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _agent_cli_launch_readiness_check(provider_matrix: list[dict[str, Any]]) -> dict[str, Any]:
    agent_providers = [
        item for item in provider_matrix
        if isinstance(item, dict) and item.get("owner") == "agent_cli"
    ]
    available: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for item in agent_providers:
        provider_id = str(item.get("provider") or "")
        diagnostics = item.get("diagnostics") if isinstance(item.get("diagnostics"), dict) else {}
        resolution = (
            diagnostics.get("command_resolution")
            if isinstance(diagnostics, dict) and isinstance(diagnostics.get("command_resolution"), dict)
            else {}
        )
        status = str(resolution.get("status") or item.get("status") or "").strip()
        record = {
            "provider": provider_id,
            "display_name": str(item.get("display_name") or provider_id),
            "status": status,
            "command": resolution.get("command") or item.get("command") or [],
            "reason": resolution.get("reason") or "",
            "used_fallback": bool(resolution.get("used_fallback", False)),
            "startup_probe_endpoint": str(diagnostics.get("startup_probe_endpoint") or ""),
            "command_hint_env": _agent_cli_command_hint_env(item, diagnostics, resolution),
        }
        if status == "available":
            available.append(record)
        else:
            failed.append(record)

    recommended_actions = _agent_cli_launch_recommended_actions(failed)
    return _system_audit_check(
        check_id="agent_cli_launch_readiness",
        ok=bool(available),
        severity="recommended",
        description="At least one Agent CLI provider can be resolved by the backend process",
        details={
            "provider_count": len(agent_providers),
            "available_provider_count": len(available),
            "available_provider_ids": [item["provider"] for item in available],
            "failed_provider_ids": [item["provider"] for item in failed],
            "available_providers": available,
            "failed_providers": failed,
            "recommended_actions": recommended_actions,
            "notes": [
                "This is a launch-resolution check, not a full prompt execution proof.",
                "Run each startup_probe_endpoint from the Workbench tools page for execution-level evidence.",
            ],
        },
    )


def _agent_cli_command_hint_env(
    item: dict[str, Any],
    diagnostics: dict[str, Any],
    resolution: dict[str, Any],
) -> str:
    recipe = diagnostics.get("probe_recipe") if isinstance(diagnostics.get("probe_recipe"), dict) else {}
    diagnostic = resolution.get("diagnostic") if isinstance(resolution.get("diagnostic"), dict) else {}
    return str(
        item.get("command_hint_env")
        or recipe.get("command_env")
        or diagnostic.get("command_hint_env")
        or ""
    )


def _agent_cli_launch_recommended_actions(failed: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    seen: set[str] = set()
    for item in failed:
        provider = str(item.get("provider") or "agent")
        env_name = str(item.get("command_hint_env") or "").strip()
        endpoint = str(item.get("startup_probe_endpoint") or "").strip()
        reason = str(item.get("reason") or "command unavailable").strip()
        if env_name:
            action = (
                f"Set {env_name} to the full {provider} CLI command or executable path; "
                f"then run {endpoint or 'the startup probe'}."
            )
        else:
            action = (
                f"Configure a command for {provider}; then run "
                f"{endpoint or 'the startup probe'}."
            )
        if reason:
            action = f"{action} Last resolution failure: {reason}."
        if action not in seen:
            seen.add(action)
            actions.append(action)
    if not actions:
        actions.append("Configure at least one Agent CLI provider and run its startup probe.")
    return actions


def _latest_deployment_task_probe_check() -> dict[str, Any]:
    latest_path = _deployment_probes_dir() / "deployment_probe_latest.json"
    latest = _read_json(latest_path)
    if not isinstance(latest, dict):
        return _system_audit_check(
            check_id="latest_deployment_task_probe",
            ok=False,
            severity="recommended",
            description="Latest deployment probe includes task contract evidence",
            details={
                "artifact_path": str(latest_path),
                "reason": "deployment_probe_latest.json has not been generated",
                "recommended_action": "Run Workbench Provider Matrix -> Task probe all",
            },
        )

    summary = latest.get("summary") if isinstance(latest.get("summary"), dict) else {}
    task_contract_probe = bool(summary.get("task_contract_probe"))
    task_ready_count = int(summary.get("task_ready_count") or 0)
    task_failed_count = int(summary.get("task_failed_count") or 0)
    provider_count = int(summary.get("provider_count") or 0)
    providers = latest.get("providers") if isinstance(latest.get("providers"), list) else []
    failed_providers = [
        str(item.get("provider") or item.get("tool") or "")
        for item in providers
        if isinstance(item, dict)
        and isinstance(item.get("task_probe"), dict)
        and item["task_probe"].get("status") != "ready"
    ]
    ok = task_contract_probe and provider_count > 0 and task_failed_count == 0
    reason = ""
    if not task_contract_probe:
        reason = "latest deployment probe did not run task_contract_probe"
    elif task_failed_count:
        reason = "one or more providers failed the task artifact contract"
    elif provider_count <= 0:
        reason = "latest deployment probe did not include providers"

    return _system_audit_check(
        check_id="latest_deployment_task_probe",
        ok=ok,
        severity="recommended",
        description="Latest deployment probe includes task contract evidence",
        details={
            "artifact_path": str(latest_path),
            "probe_id": str(latest.get("probe_id") or ""),
            "status": str(latest.get("status") or ""),
            "task_contract_probe": task_contract_probe,
            "provider_count": provider_count,
            "task_ready_count": task_ready_count,
            "task_failed_count": task_failed_count,
            "failed_providers": failed_providers,
            "reason": reason,
            "recommended_action": "Run Workbench Provider Matrix -> Task probe all",
        },
    )


def _has_missing_agent_cli_launch_readiness(checks: list[dict[str, Any]]) -> bool:
    for item in checks:
        if item.get("id") == "agent_cli_launch_readiness":
            return item.get("status") != "ok"
    return False


def _system_audit_check(
    *,
    check_id: str,
    ok: bool,
    severity: str,
    description: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": "ok" if ok else "missing",
        "severity": severity,
        "description": description,
        "details": details or {},
    }


def _materialize_mr_artifact_evidence(
    *,
    task_run: Any,
    step_id: str,
    artifact_dir: Path,
    object_text: str,
    required_artifacts: list[str],
) -> list[str]:
    agent_run = _read_json(artifact_dir / "agent_run.json")
    snapshot = _read_json(artifact_dir / "mr_snapshot.json")
    changed_files = _read_json(artifact_dir / "changed_files.json")
    provider = str(agent_run.get("provider") or "external_agent") if isinstance(agent_run, dict) else "external_agent"
    run_id = task_run.task_run_id
    workspace_id = task_run.workspace_id
    store = _memory_store()
    store.record_analysis_run(
        run_id=run_id,
        workspace_id=workspace_id,
        repo_path=task_run.repo_path,
        object_text=object_text or _object_text_from_task_run(task_run, snapshot),
        workflow_id=task_run.workflow_id,
        status="completed",
    )
    evidence_ids: list[str] = []
    provenance_base = {
        "task_run_id": task_run.task_run_id,
        "step_id": step_id,
        "provider": provider,
        "artifact_dir": str(artifact_dir),
        "provenance_status": "agent_mcp_provenance",
    }
    if isinstance(snapshot, dict):
        mr_url = str(snapshot.get("mr_url") or "")
        evidence_ids.append(store.upsert_evidence_item(
            run_id=run_id,
            workspace_id=workspace_id,
            kind="merge_request",
            subject_key=mr_url or f"{task_run.task_run_id}/{step_id}/mr",
            status="agent_mcp_verified",
            source=provider,
            reason="MR metadata was produced by Agent MCP and verified against required artifacts.",
            text=" ".join(str(snapshot.get(key) or "") for key in ("project", "title", "source_branch", "target_branch")),
            provenance={**provenance_base, "artifact": "mr_snapshot.json", "snapshot": snapshot},
        ))
    for artifact in required_artifacts:
        path = artifact_dir / artifact
        evidence_ids.append(store.upsert_evidence_item(
            run_id=run_id,
            workspace_id=workspace_id,
            kind="agent_artifact",
            subject_key=f"{task_run.task_run_id}/{step_id}/{artifact}",
            status="verified_artifact",
            source=provider,
            path=str(path),
            reason="Required Agent artifact passed CodeTalk validation.",
            text=artifact,
            provenance={**provenance_base, "artifact": artifact},
        ))
    if isinstance(changed_files, list):
        for item in changed_files:
            if not isinstance(item, dict):
                continue
            changed_path = str(item.get("path") or "").replace("\\", "/")
            if not changed_path:
                continue
            evidence_ids.append(store.upsert_evidence_item(
                run_id=run_id,
                workspace_id=workspace_id,
                kind="changed_file",
                subject_key=changed_path,
                status="agent_mcp_verified",
                source=provider,
                path=changed_path,
                reason="Changed file came from Agent MCP MR artifacts and CodeTalk validation.",
                text=" ".join(str(item.get(key) or "") for key in ("path", "status", "old_path", "new_path")),
                provenance={**provenance_base, "artifact": "changed_files.json", "changed_file": item},
            ))
    return evidence_ids


def _materialize_workflow_output_evidence(
    *,
    task_run: Any,
    workflow_outputs: dict[str, Any],
) -> tuple[list[str], list[dict[str, str]]]:
    store = _memory_store()
    store.record_analysis_run(
        run_id=task_run.task_run_id,
        workspace_id=task_run.workspace_id,
        repo_path=task_run.repo_path,
        object_text=_object_text_from_task_run(task_run, workflow_outputs),
        workflow_id=task_run.workflow_id,
        status="completed",
    )
    evidence_ids: list[str] = []
    rejected: list[dict[str, str]] = []
    for output in workflow_outputs.get("outputs") or []:
        if not isinstance(output, dict):
            continue
        output_id = str(output.get("id") or "").strip()
        if not output_id:
            rejected.append({"output": "", "reason": "missing_output_id"})
            continue
        if output.get("status") != "ok":
            rejected.append(_workflow_output_rejection_detail(output, reason="output_not_ok"))
            continue
        path = Path(str(output.get("path") or ""))
        if not _is_workflow_output_path_within_task_artifacts(task_run, path):
            rejected.append({
                "output": output_id,
                "reason": "output_path_outside_task_artifacts",
                "path": str(path),
            })
            continue
        if not path.exists() or not path.is_file():
            rejected.append({"output": output_id, "reason": "output_file_missing"})
            continue
        data = path.read_bytes()
        sha256 = hashlib.sha256(data).hexdigest()
        if output.get("sha256") and output.get("sha256") != sha256:
            rejected.append({"output": output_id, "reason": "output_sha256_mismatch"})
            continue
        text = _evidence_text_from_output(path, data, fallback=str(output.get("preview") or ""))
        workflow_output_definition = _workflow_output_definition(task_run, output_id)
        workflow_outputs_artifact = _workflow_outputs_artifact_ref(task_run)
        base_provenance = {
            "task_run_id": task_run.task_run_id,
            "workflow_id": task_run.workflow_id,
            "output_id": output_id,
            "output_status": str(output.get("status") or ""),
            "output_type": str(output.get("type") or ""),
            "source_step_id": str(output.get("from") or ""),
            "output": output,
            "artifact": "workflow_outputs.json",
            "workflow_outputs_artifact": workflow_outputs_artifact,
            "agent_output_contract": _agent_output_contract_ref(
                task_run=task_run,
                step_id=str(output.get("from") or ""),
            ),
            **_agent_run_audit_refs(
                task_run=task_run,
                step_id=str(output.get("from") or ""),
            ),
            "schema_status": _workflow_output_schema_status(
                output=output,
                output_definition=workflow_output_definition,
            ),
            "schema_required": _workflow_output_schema_required(workflow_output_definition),
            "sha256": sha256,
            "size_bytes": len(data),
        }
        workflow_output_subject = f"{task_run.task_run_id}/{output_id}"
        output_evidence_id = store.upsert_evidence_item(
            evidence_id=_stable_workflow_evidence_id(
                task_run=task_run,
                kind="workflow_output",
                subject_key=workflow_output_subject,
                output_id=output_id,
            ),
            run_id=task_run.task_run_id,
            workspace_id=task_run.workspace_id,
            kind="workflow_output",
            subject_key=workflow_output_subject,
            status="verified_output",
            source=str(output.get("from") or "workflow"),
            path=str(path),
            reason="Workflow output passed CodeTalk local artifact validation.",
            text=text,
            provenance=base_provenance,
        )
        evidence_ids.append(output_evidence_id)
        structured_ids, structured_rejected = _materialize_structured_workflow_output_evidence(
            store=store,
            task_run=task_run,
            output=output,
            output_id=output_id,
            output_evidence_id=output_evidence_id,
            path=path,
            data=data,
            sha256=sha256,
        )
        evidence_ids.extend(structured_ids)
        rejected.extend(structured_rejected)
    return evidence_ids, rejected


def _stable_workflow_evidence_id(
    *,
    task_run: Any,
    kind: str,
    subject_key: str,
    output_id: str,
) -> str:
    seed = "\n".join([
        str(getattr(task_run, "task_run_id", "")),
        str(getattr(task_run, "workspace_id", "")),
        str(kind),
        str(output_id),
        str(subject_key),
    ])
    return f"ev_{hashlib.sha256(seed.encode('utf-8')).hexdigest()}"


def _workflow_output_definition(task_run: Any, output_id: str) -> dict[str, Any]:
    workflow_snapshot = getattr(task_run, "workflow_snapshot", {}) or {}
    for item in workflow_snapshot.get("outputs") or []:
        if isinstance(item, dict) and str(item.get("id") or "") == output_id:
            return item
    return {}


def _workflow_output_schema_status(
    *,
    output: dict[str, Any],
    output_definition: dict[str, Any],
) -> str:
    if output.get("schema_errors"):
        return "failed"
    schema = output_definition.get("schema") or output_definition.get("json_schema")
    return "validated" if isinstance(schema, dict) else "not_declared"


def _workflow_output_schema_required(output_definition: dict[str, Any]) -> list[str]:
    schema = output_definition.get("schema") or output_definition.get("json_schema")
    if not isinstance(schema, dict):
        return []
    return [str(item) for item in schema.get("required") or []]


def _workflow_outputs_artifact_ref(task_run: Any) -> dict[str, Any]:
    path = Path(str(task_run.artifact_dir)) / "workflow_outputs.json"
    return _task_artifact_ref(task_run=task_run, path=path)


def _agent_output_contract_ref(*, task_run: Any, step_id: str) -> dict[str, Any]:
    return _agent_step_artifact_ref(
        task_run=task_run,
        step_id=step_id,
        filename="agent_output_contract.json",
    )


def _agent_run_audit_refs(*, task_run: Any, step_id: str) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    for key, filename in (
        ("agent_run", "agent_run.json"),
        ("agent_execution_input", "execution_input.json"),
        ("agent_execution_result", "execution_result.json"),
        ("agent_replay_plan", "agent_replay_plan.json"),
    ):
        ref = _agent_step_artifact_ref(
            task_run=task_run,
            step_id=step_id,
            filename=filename,
        )
        if ref:
            refs[key] = ref
    return refs


def _agent_step_artifact_ref(
    *,
    task_run: Any,
    step_id: str,
    filename: str,
) -> dict[str, Any]:
    safe_step_id = _safe_artifact_segment(step_id)
    if not safe_step_id:
        return {}
    safe_filename = _safe_artifact_segment(filename)
    if not safe_filename:
        return {}
    path = Path(str(task_run.artifact_dir)) / "agent_runs" / safe_step_id / safe_filename
    ref = _task_artifact_ref(task_run=task_run, path=path)
    if not ref:
        return {}
    ref["artifact"] = f"agent_runs/{safe_step_id}/{safe_filename}"
    return ref


def _task_artifact_ref(*, task_run: Any, path: Path) -> dict[str, Any]:
    try:
        task_root = Path(str(task_run.artifact_dir)).resolve()
        resolved = path.resolve()
    except OSError:
        return {}
    if resolved != task_root and task_root not in resolved.parents:
        return {}
    if not resolved.exists() or not resolved.is_file():
        return {}
    try:
        data = resolved.read_bytes()
    except OSError:
        return {}
    return {
        "artifact": resolved.relative_to(task_root).as_posix(),
        "path": str(resolved),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }


def _safe_artifact_segment(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text or "/" in text or text in {".", ".."}:
        return ""
    return text


def _is_workflow_output_path_within_task_artifacts(task_run: Any, path: Path) -> bool:
    try:
        task_root = Path(str(task_run.artifact_dir)).resolve()
        resolved = path.resolve()
        return resolved == task_root or task_root in resolved.parents
    except OSError:
        return False


def _workflow_output_rejection_detail(output: dict[str, Any], *, reason: str) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "output": str(output.get("id") or ""),
        "reason": reason,
    }
    fields = {
        "status": "output_status",
        "reason": "output_reason",
        "artifact": "artifact",
        "path": "path",
        "from": "from",
    }
    for source_key, target_key in fields.items():
        value = output.get(source_key)
        if isinstance(value, str) and value:
            detail[target_key] = value
    schema_errors = output.get("schema_errors")
    if isinstance(schema_errors, list):
        detail["schema_errors"] = [str(item) for item in schema_errors]
    return detail


def _write_workflow_output_materialization_artifact(
    *,
    task_run: Any,
    workflow_outputs_path: Path,
    workflow_outputs: dict[str, Any],
    result: dict[str, Any],
) -> None:
    workflow_outputs_sha = ""
    workflow_outputs_size = 0
    try:
        data = workflow_outputs_path.read_bytes()
        workflow_outputs_sha = hashlib.sha256(data).hexdigest()
        workflow_outputs_size = len(data)
    except OSError:
        pass
    payload = {
        "task_run_id": task_run.task_run_id,
        "workflow_id": task_run.workflow_id,
        "workspace_id": task_run.workspace_id,
        "repo_path": task_run.repo_path,
        "status": result.get("status"),
        "evidence_count": result.get("evidence_count", 0),
        "evidence_ids": list(result.get("evidence_ids") or []),
        "rejected_outputs": list(result.get("rejected_outputs") or []),
        "workflow_outputs_artifact": {
            "path": str(workflow_outputs_path),
            "sha256": workflow_outputs_sha,
            "size_bytes": workflow_outputs_size,
            "output_count": len(workflow_outputs.get("outputs") or []),
        },
    }
    artifact_path = Path(task_run.artifact_dir) / "workflow_output_materialization.json"
    artifact_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _import_workflow_outputs_as_semantic_cases(
    *,
    task_run: Any,
    workflow_outputs: dict[str, Any],
    output_ids: list[str],
    defaults: dict[str, Any],
) -> dict[str, Any]:
    selected_ids = {str(item).strip() for item in output_ids if str(item).strip()}
    import_payloads: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    source_refs: list[str] = []
    for output in workflow_outputs.get("outputs") or []:
        if not isinstance(output, dict):
            continue
        output_id = str(output.get("id") or "").strip()
        if selected_ids and output_id not in selected_ids:
            continue
        output_cases, output_rejected, source_ref = _semantic_cases_from_workflow_output(
            task_run=task_run,
            output=output,
            defaults=defaults,
        )
        import_payloads.extend(output_cases)
        rejected.extend(output_rejected)
        if source_ref:
            source_refs.append(source_ref)

    imported_result = _semantic_store().import_cases({
        "source_ref": source_refs[0] if len(source_refs) == 1 else f"task_run:{task_run.task_run_id}",
        "cases": import_payloads,
    })
    rejected.extend(imported_result.get("rejected") or [])
    result = {
        **imported_result,
        "rejected_count": len(rejected),
        "rejected": rejected,
        "source_ref": source_refs[0] if len(source_refs) == 1 else f"task_run:{task_run.task_run_id}",
        "source_refs": source_refs,
    }
    result["status"] = _semantic_import_status(result)
    return result


def _semantic_cases_from_workflow_output(
    *,
    task_run: Any,
    output: dict[str, Any],
    defaults: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    output_id = str(output.get("id") or "").strip()
    if not _workflow_output_looks_like_test_cases(output):
        return [], [{"output": output_id, "reason": "output_is_not_test_cases"}], ""
    if output.get("status") != "ok":
        return [], [_workflow_output_rejection_detail(output, reason="output_not_ok")], ""
    path = Path(str(output.get("path") or ""))
    if not _is_workflow_output_path_within_task_artifacts(task_run, path):
        return [], [{
            "output": output_id,
            "reason": "output_path_outside_task_artifacts",
            "path": str(path),
        }], ""
    if not path.exists() or not path.is_file():
        return [], [{"output": output_id, "reason": "output_file_missing"}], ""
    data = path.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    if output.get("sha256") and output.get("sha256") != sha256:
        return [], [{"output": output_id, "reason": "output_sha256_mismatch"}], ""
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [], [{"output": output_id, "reason": "invalid_json", "detail": str(exc)}], ""
    raw_cases = parsed.get("black_box_cases") if isinstance(parsed, dict) else parsed
    if not isinstance(raw_cases, list):
        return [], [{"output": output_id, "reason": "test_cases_must_be_list"}], ""
    source_ref = f"task_run:{task_run.task_run_id}:{output_id}"
    cases: list[dict[str, Any]] = []
    for index, item in enumerate(raw_cases, start=1):
        if not isinstance(item, dict):
            continue
        semantic_case = _semantic_case_from_black_box_case(
            task_run=task_run,
            output=output,
            output_id=output_id,
            case=item,
            index=index,
            defaults=defaults,
            source_ref=source_ref,
        )
        cases.append(semantic_case)
    return cases, [], source_ref


def _workflow_output_looks_like_test_cases(output: dict[str, Any]) -> bool:
    output_id = str(output.get("id") or "").lower()
    output_type = str(output.get("type") or "").lower()
    artifact = Path(str(output.get("artifact") or output.get("path") or "")).name.lower()
    return (
        output_type == "test_cases"
        or output_id in {"black_box_cases", "test_cases"}
        or artifact in {"black_box_cases.json", "test_cases.json"}
    )


def _semantic_case_from_black_box_case(
    *,
    task_run: Any,
    output: dict[str, Any],
    output_id: str,
    case: dict[str, Any],
    index: int,
    defaults: dict[str, Any],
    source_ref: str,
) -> dict[str, Any]:
    title = str(case.get("title") or case.get("scenario") or f"{output_id} case {index}").strip()
    module = str(defaults.get("module") or _object_text_from_task_run(task_run, {}))
    steps = _semantic_string_list(case.get("steps"))
    inputs = str(case.get("inputs") or "").strip()
    if inputs and inputs not in steps:
        steps = [inputs, *steps]
    expected = _semantic_string_list(case.get("expected"))
    expected.extend(_semantic_string_list(case.get("observable_signals")))
    tags = _semantic_dedupe([
        *_semantic_string_list(defaults.get("tags")),
        "generated_from_task_output",
        str(output.get("from") or "workflow"),
        output_id,
    ])
    terms = _semantic_dedupe([
        *_semantic_string_list(defaults.get("terms")),
        *_semantic_terms_from_text(title),
        *_semantic_terms_from_text(inputs),
    ])
    return {
        **defaults,
        "case_id": str(
            case.get("case_id")
            or f"{task_run.task_run_id}_{output_id}_{index:03d}"
        ),
        "feature": str(defaults.get("feature") or task_run.workflow_id),
        "module": module,
        "scenario": title,
        "preconditions": _semantic_string_list(case.get("preconditions")),
        "actions": steps or [title],
        "expected": _semantic_dedupe(expected) or ["Expected behavior is observable from the generated black-box case."],
        "test_level": str(defaults.get("test_level") or "black_box"),
        "interface": str(case.get("entry_kind") or defaults.get("interface") or ""),
        "terms": terms,
        "assertion_style": str(defaults.get("assertion_style") or "observable signals"),
        "tags": tags,
        "source_ref": source_ref,
        "status": str(defaults.get("status") or "active"),
    }


def _semantic_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item)]
    return []


def _semantic_terms_from_text(text: str) -> list[str]:
    words = [
        item.strip("._-:/").lower()
        for item in str(text or "").split()
        if len(item.strip("._-:/")) >= 3
    ]
    return words[:12]


def _semantic_dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _materialize_structured_workflow_output_evidence(
    *,
    store: EvidenceMemoryStore,
    task_run: Any,
    output: dict[str, Any],
    output_id: str,
    output_evidence_id: str,
    path: Path,
    data: bytes,
    sha256: str,
) -> tuple[list[str], list[dict[str, str]]]:
    workflow_output_definition = _workflow_output_definition(task_run, output_id)
    evidence_mapping = workflow_output_definition.get("evidence_memory")
    if _custom_evidence_mapping_enabled(evidence_mapping):
        return _materialize_custom_json_output_evidence(
            store=store,
            task_run=task_run,
            output=output,
            output_definition=workflow_output_definition,
            output_id=output_id,
            output_evidence_id=output_evidence_id,
            path=path,
            data=data,
            sha256=sha256,
        )
    if path.name == "source_scope.json" or output_id in {"source_scope", "scope"}:
        return _materialize_source_scope_evidence(
            store=store,
            task_run=task_run,
            output=output,
            output_id=output_id,
            output_evidence_id=output_evidence_id,
            path=path,
            data=data,
            sha256=sha256,
        )
    if path.name == "evidence_cards.json" or output_id == "evidence_cards":
        return _materialize_evidence_card_output(
            store=store,
            task_run=task_run,
            output=output,
            output_id=output_id,
            output_evidence_id=output_evidence_id,
            path=path,
            data=data,
            sha256=sha256,
        )
    if path.name == "uncovered_functions.json" or output_id == "uncovered_functions":
        return _materialize_uncovered_function_evidence(
            store=store,
            task_run=task_run,
            output=output,
            output_id=output_id,
            output_evidence_id=output_evidence_id,
            path=path,
            data=data,
            sha256=sha256,
        )
    if path.name != "changed_files.json" and output_id != "changed_files":
        return [], []
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return [], []
    if not isinstance(payload, list):
        return [], []
    return _materialize_changed_file_output(
        store=store,
        task_run=task_run,
        output=output,
        output_id=output_id,
        output_evidence_id=output_evidence_id,
        path=path,
        sha256=sha256,
        payload=payload,
    )


def _custom_evidence_mapping_enabled(value: Any) -> bool:
    if value is True:
        return True
    if not isinstance(value, dict):
        return False
    return bool(value.get("enabled", True))


def _materialize_custom_json_output_evidence(
    *,
    store: EvidenceMemoryStore,
    task_run: Any,
    output: dict[str, Any],
    output_definition: dict[str, Any],
    output_id: str,
    output_evidence_id: str,
    path: Path,
    data: bytes,
    sha256: str,
) -> tuple[list[str], list[dict[str, str]]]:
    mapping = output_definition.get("evidence_memory")
    mapping_payload = mapping if isinstance(mapping, dict) else {}
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [], [{"output": output_id, "reason": "invalid_json", "detail": str(exc)}]
    items = _custom_evidence_items(payload, output_id=output_id)
    evidence_ids: list[str] = []
    rejected: list[dict[str, str]] = []
    kind = _custom_evidence_kind(mapping_payload)
    status = str(mapping_payload.get("status") or "verified_output").strip() or "verified_output"
    subject_field = str(
        mapping_payload.get("subject_key_field")
        or mapping_payload.get("subject_field")
        or mapping_payload.get("id_field")
        or ""
    ).strip()
    path_field = str(mapping_payload.get("path_field") or "").strip()
    symbol_field = str(mapping_payload.get("symbol_field") or "").strip()
    reason_field = str(mapping_payload.get("reason_field") or "reason").strip()
    text_fields = _mapping_string_list(mapping_payload.get("text_fields"))
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            rejected.append({
                "output": output_id,
                "reason": "mapped_item_not_object",
                "index": str(index),
            })
            continue
        subject_key = _mapping_value(item, subject_field) if subject_field else ""
        if not subject_key:
            subject_key = str(
                item.get("id")
                or item.get("finding_id")
                or item.get("case_id")
                or f"{output_id}:{index}"
            ).strip()
        mapped_path = _mapping_value(item, path_field) if path_field else ""
        safe_path = _safe_mapping_path(mapped_path)
        if mapped_path and not safe_path:
            rejected.append({
                "output": output_id,
                "reason": "mapped_path_is_unsafe",
                "path": mapped_path,
                "index": str(index),
            })
            continue
        symbol = _mapping_value(item, symbol_field) if symbol_field else ""
        reason = (
            _mapping_value(item, reason_field)
            or f"Custom workflow output item came from locally verified output {output_id}."
        )
        text = _custom_evidence_text(item, text_fields=text_fields, fallback=reason)
        evidence_id = store.upsert_evidence_item(
            evidence_id=_stable_workflow_evidence_id(
                task_run=task_run,
                kind=kind,
                subject_key=subject_key,
                output_id=output_id,
            ),
            run_id=task_run.task_run_id,
            workspace_id=task_run.workspace_id,
            kind=kind,
            subject_key=subject_key,
            status=status,
            source=str(output.get("from") or "workflow"),
            path=safe_path,
            symbol=symbol,
            reason=reason,
            text=text,
            provenance={
                **_structured_workflow_output_provenance(
                    task_run=task_run,
                    output=output,
                    output_id=output_id,
                    output_evidence_id=output_evidence_id,
                    path=path,
                    sha256=sha256,
                ),
                "item_index": index,
                "item": item,
                "evidence_memory_mapping": {
                    "kind": kind,
                    "subject_key_field": subject_field,
                    "path_field": path_field,
                    "symbol_field": symbol_field,
                    "text_fields": text_fields,
                },
            },
        )
        evidence_ids.append(evidence_id)
    return evidence_ids, rejected


def _custom_evidence_items(payload: Any, *, output_id: str) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "findings", "evidence", output_id):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        return [payload]
    return []


def _custom_evidence_kind(mapping: dict[str, Any]) -> str:
    kind = str(mapping.get("kind") or "workflow_output_item").strip()
    normalized = re.sub(r"[^A-Za-z0-9_.:-]+", "_", kind).strip("_")
    return normalized or "workflow_output_item"


def _mapping_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _mapping_value(item: dict[str, Any], field_path: str) -> str:
    if not field_path:
        return ""
    value: Any = item
    for part in str(field_path).split("."):
        if not isinstance(value, dict):
            return ""
        value = value.get(part)
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


def _safe_mapping_path(value: str) -> str:
    text = str(value or "").replace("\\", "/").strip()
    if not text:
        return ""
    candidate = Path(text)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        return ""
    return text.strip("/")


def _custom_evidence_text(
    item: dict[str, Any],
    *,
    text_fields: list[str],
    fallback: str,
) -> str:
    parts = [_mapping_value(item, field) for field in text_fields]
    if not any(parts):
        for field in ("summary", "title", "scenario", "reason", "description"):
            value = _mapping_value(item, field)
            if value:
                parts.append(value)
    text = " ".join(part for part in parts if part).strip()
    if text:
        return text
    return fallback or json.dumps(item, ensure_ascii=False, sort_keys=True)[:1200]


def _materialize_changed_file_output(
    *,
    store: EvidenceMemoryStore,
    task_run: Any,
    output: dict[str, Any],
    output_id: str,
    output_evidence_id: str,
    path: Path,
    sha256: str,
    payload: list[Any],
) -> tuple[list[str], list[dict[str, str]]]:
    patch_paths = _patch_snapshot_paths_for_task(task_run)
    evidence_ids: list[str] = []
    rejected: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        changed_path = str(item.get("path") or "").replace("\\", "/").strip()
        if not changed_path:
            continue
        validation_source = _changed_file_validation_source(
            task_run=task_run,
            changed_path=changed_path,
            patch_paths=patch_paths,
        )
        if not validation_source:
            rejected.append({
                "output": output_id,
                "path": changed_path,
                "reason": "changed_file_not_in_repo_or_patch_snapshot",
            })
            continue
        evidence_ids.append(store.upsert_evidence_item(
            evidence_id=_stable_workflow_evidence_id(
                task_run=task_run,
                kind="changed_file",
                subject_key=changed_path,
                output_id=output_id,
            ),
            run_id=task_run.task_run_id,
            workspace_id=task_run.workspace_id,
            kind="changed_file",
            subject_key=changed_path,
            status="verified_output",
            source=str(output.get("from") or "workflow"),
            path=changed_path,
            reason="Changed file came from a locally verified workflow output.",
            text=" ".join(
                str(item.get(key) or "")
                for key in ("path", "status", "old_path", "new_path")
            ),
            provenance={
                **_structured_workflow_output_provenance(
                    task_run=task_run,
                    output=output,
                    output_id=output_id,
                    output_evidence_id=output_evidence_id,
                    path=path,
                    sha256=sha256,
                ),
                "changed_file": item,
                "validation_source": validation_source,
            },
        ))
    return evidence_ids, rejected


def _structured_workflow_output_provenance(
    *,
    task_run: Any,
    output: dict[str, Any],
    output_id: str,
    output_evidence_id: str,
    path: Path,
    sha256: str,
) -> dict[str, Any]:
    return {
        "task_run_id": task_run.task_run_id,
        "workflow_id": task_run.workflow_id,
        "output_id": output_id,
        "output_evidence_id": output_evidence_id,
        "workflow_output_evidence_id": output_evidence_id,
        "artifact_path": str(path),
        "sha256": sha256,
        **_agent_run_audit_refs(
            task_run=task_run,
            step_id=str(output.get("from") or ""),
        ),
    }


def _changed_file_validation_source(
    *,
    task_run: Any,
    changed_path: str,
    patch_paths: set[str],
) -> str:
    normalized = changed_path.replace("\\", "/").strip("/")
    candidate = Path(normalized)
    if (
        not normalized
        or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        return ""
    try:
        repo_root = Path(str(task_run.repo_path)).resolve()
        repo_candidate = (repo_root / candidate).resolve()
    except OSError:
        repo_candidate = None
        repo_root = None
    if repo_candidate is not None and repo_root is not None:
        if (
            (repo_candidate == repo_root or repo_root in repo_candidate.parents)
            and repo_candidate.exists()
        ):
            return "repo"
    if normalized in patch_paths:
        return "patch_snapshot"
    return ""


def _patch_snapshot_paths_for_task(task_run: Any) -> set[str]:
    try:
        task_root = Path(str(task_run.artifact_dir)).resolve()
    except OSError:
        return set()
    if not task_root.exists() or not task_root.is_dir():
        return set()
    paths: set[str] = set()
    for patch_path in task_root.rglob("*"):
        if (
            not patch_path.is_file()
            or patch_path.suffix.lower() not in {".patch", ".diff"}
        ):
            continue
        try:
            resolved = patch_path.resolve()
        except OSError:
            continue
        if resolved != task_root and task_root not in resolved.parents:
            continue
        try:
            text = resolved.read_text(encoding="utf-8", errors="replace")
            paths.update(_changed_paths_from_patch_text(text))
        except OSError:
            continue
    return paths


def _changed_paths_from_patch_text(diff_text: str) -> set[str]:
    paths: set[str] = set()
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            for candidate in parts[-2:]:
                cleaned = _clean_diff_path(candidate)
                if cleaned:
                    paths.add(cleaned)
        elif line.startswith(("--- ", "+++ ")):
            cleaned = _clean_diff_path(line[4:].strip())
            if cleaned:
                paths.add(cleaned)
    return paths


def _clean_diff_path(value: str) -> str:
    text = str(value or "").strip().strip('"').replace("\\", "/")
    if not text or text == "/dev/null":
        return ""
    if text.startswith("a/") or text.startswith("b/"):
        text = text[2:]
    return text.strip("/")


def _materialize_source_scope_evidence(
    *,
    store: EvidenceMemoryStore,
    task_run: Any,
    output: dict[str, Any],
    output_id: str,
    output_evidence_id: str,
    path: Path,
    data: bytes,
    sha256: str,
) -> tuple[list[str], list[dict[str, str]]]:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return [], []
    if not isinstance(payload, dict):
        return [], []
    evidence_ids: list[str] = []
    rejected: list[dict[str, str]] = []
    seen_files: set[str] = set()
    seen_symbols: set[tuple[str, str]] = set()
    for file_item in _source_scope_file_items(payload):
        candidate_path = _source_scope_item_path(file_item)
        resolved = _validated_repo_source_path(task_run.repo_path, candidate_path)
        if resolved is None:
            if candidate_path:
                rejected.append({
                    "output": output_id,
                    "reason": "source_scope_path_not_verified",
                    "path": candidate_path,
                })
            continue
        rel_path, resolved_path = resolved
        if rel_path not in seen_files:
            seen_files.add(rel_path)
            source_evidence_id = store.upsert_evidence_item(
                evidence_id=_stable_workflow_evidence_id(
                    task_run=task_run,
                    kind="source_file",
                    subject_key=rel_path,
                    output_id=output_id,
                ),
                run_id=task_run.task_run_id,
                workspace_id=task_run.workspace_id,
                kind="source_file",
                subject_key=rel_path,
                status="verified_output",
                source=str(output.get("from") or "workflow"),
                path=rel_path,
                reason=_source_scope_item_reason(file_item) or (
                    "Source file came from a locally verified workflow source scope output."
                ),
                text=f"{rel_path} {_source_scope_item_reason(file_item)}".strip(),
                provenance={
                    **_structured_workflow_output_provenance(
                        task_run=task_run,
                        output=output,
                        output_id=output_id,
                        output_evidence_id=output_evidence_id,
                        path=path,
                        sha256=sha256,
                    ),
                    "file_path": rel_path,
                    "resolved_path": str(resolved_path),
                },
            )
            evidence_ids.append(source_evidence_id)
            _add_workbench_source_slice(
                store=store,
                evidence_id=source_evidence_id,
                repo_path=task_run.repo_path,
                rel_path=rel_path,
                line_start=_source_scope_item_line_start(file_item),
            )
        for symbol_item in _source_scope_item_symbols(file_item):
            symbol_name = _source_scope_symbol_name(symbol_item)
            if not symbol_name:
                continue
            symbol_key = (rel_path, symbol_name)
            if symbol_key in seen_symbols:
                continue
            seen_symbols.add(symbol_key)
            line_start = _safe_int(symbol_item.get("line_start") if isinstance(symbol_item, dict) else None)
            evidence_ids.append(store.upsert_evidence_item(
                evidence_id=_stable_workflow_evidence_id(
                    task_run=task_run,
                    kind="symbol",
                    subject_key=f"{rel_path}:{symbol_name}",
                    output_id=output_id,
                ),
                run_id=task_run.task_run_id,
                workspace_id=task_run.workspace_id,
                kind="symbol",
                subject_key=f"{rel_path}:{symbol_name}",
                status="verified_output",
                source=str(output.get("from") or "workflow"),
                path=rel_path,
                symbol=symbol_name,
                reason="Symbol came from a locally verified workflow source scope output.",
                text=f"{rel_path} {symbol_name} line_start={line_start}",
                provenance={
                    **_structured_workflow_output_provenance(
                        task_run=task_run,
                        output=output,
                        output_id=output_id,
                        output_evidence_id=output_evidence_id,
                        path=path,
                        sha256=sha256,
                    ),
                    "file_path": rel_path,
                    "symbol": symbol_name,
                    "line_start": line_start,
                },
            ))
    for symbol_item in _source_scope_top_level_symbols(payload):
        if not isinstance(symbol_item, dict):
            continue
        candidate_path = _source_scope_item_path(symbol_item)
        resolved = _validated_repo_source_path(task_run.repo_path, candidate_path)
        symbol_name = _source_scope_symbol_name(symbol_item)
        if resolved is None:
            if candidate_path:
                rejected.append({
                    "output": output_id,
                    "reason": "source_scope_path_not_verified",
                    "path": candidate_path,
                })
            continue
        if not symbol_name:
            continue
        rel_path, _resolved_path = resolved
        symbol_key = (rel_path, symbol_name)
        if symbol_key in seen_symbols:
            continue
        seen_symbols.add(symbol_key)
        line_start = _safe_int(symbol_item.get("line_start"))
        evidence_ids.append(store.upsert_evidence_item(
            evidence_id=_stable_workflow_evidence_id(
                task_run=task_run,
                kind="symbol",
                subject_key=f"{rel_path}:{symbol_name}",
                output_id=output_id,
            ),
            run_id=task_run.task_run_id,
            workspace_id=task_run.workspace_id,
            kind="symbol",
            subject_key=f"{rel_path}:{symbol_name}",
            status="verified_output",
            source=str(output.get("from") or "workflow"),
            path=rel_path,
            symbol=symbol_name,
            reason="Symbol came from a locally verified workflow source scope output.",
            text=f"{rel_path} {symbol_name} line_start={line_start}",
            provenance={
                **_structured_workflow_output_provenance(
                    task_run=task_run,
                    output=output,
                    output_id=output_id,
                    output_evidence_id=output_evidence_id,
                    path=path,
                    sha256=sha256,
                ),
                "file_path": rel_path,
                "symbol": symbol_name,
                "line_start": line_start,
            },
        ))
    return evidence_ids, rejected


def _materialize_evidence_card_output(
    *,
    store: EvidenceMemoryStore,
    task_run: Any,
    output: dict[str, Any],
    output_id: str,
    output_evidence_id: str,
    path: Path,
    data: bytes,
    sha256: str,
) -> tuple[list[str], list[dict[str, str]]]:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return [], []
    cards = payload if isinstance(payload, list) else payload.get("evidence_cards") if isinstance(payload, dict) else []
    if not isinstance(cards, list):
        return [], []
    evidence_ids: list[str] = []
    rejected: list[dict[str, str]] = []
    seen_cards: set[str] = set()
    for card in cards:
        if not isinstance(card, dict):
            continue
        candidate_path = _source_scope_item_path(card)
        resolved = _validated_repo_source_path(task_run.repo_path, candidate_path)
        if resolved is None:
            if candidate_path:
                detail = {
                    "output": output_id,
                    "reason": "evidence_card_path_not_verified",
                    "path": candidate_path,
                }
                card_id = str(card.get("card_id") or card.get("id") or "").strip()
                if card_id:
                    detail["card_id"] = card_id
                rejected.append(detail)
            continue
        rel_path, _resolved_path = resolved
        card_id = str(card.get("card_id") or card.get("id") or f"{rel_path}:{card.get('symbol') or ''}").strip()
        if not card_id or card_id in seen_cards:
            continue
        seen_cards.add(card_id)
        symbol = str(card.get("symbol") or card.get("function_name") or card.get("entry_symbol") or "").strip()
        reason = str(card.get("reason") or card.get("title") or "Evidence card came from a locally verified workflow output.").strip()
        excerpt = str(card.get("excerpt") or card.get("text") or card.get("summary") or "").strip()
        card_evidence_id = store.upsert_evidence_item(
            evidence_id=_stable_workflow_evidence_id(
                task_run=task_run,
                kind="evidence_card",
                subject_key=card_id,
                output_id=output_id,
            ),
            run_id=task_run.task_run_id,
            workspace_id=task_run.workspace_id,
            kind="evidence_card",
            subject_key=card_id,
            status="verified_output",
            source=str(output.get("from") or "workflow"),
            path=rel_path,
            symbol=symbol,
            reason=reason,
            text=" ".join(part for part in [rel_path, symbol, reason, excerpt] if part),
            provenance={
                **_structured_workflow_output_provenance(
                    task_run=task_run,
                    output=output,
                    output_id=output_id,
                    output_evidence_id=output_evidence_id,
                    path=path,
                    sha256=sha256,
                ),
                "card": card,
            },
        )
        evidence_ids.append(card_evidence_id)
        _add_workbench_source_slice(
            store=store,
            evidence_id=card_evidence_id,
            repo_path=task_run.repo_path,
            rel_path=rel_path,
            line_start=_safe_int(card.get("line_start") or card.get("start_line") or card.get("line") or 1),
        )
    return evidence_ids, rejected


def _materialize_uncovered_function_evidence(
    *,
    store: EvidenceMemoryStore,
    task_run: Any,
    output: dict[str, Any],
    output_id: str,
    output_evidence_id: str,
    path: Path,
    data: bytes,
    sha256: str,
) -> tuple[list[str], list[dict[str, str]]]:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return [], []
    if not isinstance(payload, list):
        return [], []
    evidence_ids: list[str] = []
    rejected: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        file_path = str(item.get("file_path") or "").replace("\\", "/").strip()
        function_name = str(item.get("function_name") or item.get("symbol") or "").strip()
        if not file_path or not function_name:
            continue
        line_start = _safe_int(item.get("line_start"))
        hit_count = _safe_int(item.get("hit_count"))
        subject_key = f"{file_path}:{function_name}"
        source_verified = _validated_repo_source_path(task_run.repo_path, file_path) is not None
        if not source_verified:
            rejected.append({
                "output": output_id,
                "path": file_path,
                "function_name": function_name,
                "reason": "coverage_source_path_not_verified",
            })
        gap_evidence_id = store.upsert_evidence_item(
            evidence_id=_stable_workflow_evidence_id(
                task_run=task_run,
                kind="coverage_gap",
                subject_key=subject_key,
                output_id=output_id,
            ),
            run_id=task_run.task_run_id,
            workspace_id=task_run.workspace_id,
            kind="coverage_gap",
            subject_key=subject_key,
            status="verified_output" if source_verified else "needs_source_validation",
            source=str(output.get("from") or "workflow"),
            path=file_path,
            symbol=function_name,
            reason=(
                "Uncovered function came from a locally verified workflow coverage output."
                if source_verified
                else "Coverage output was parsed, but its source path was not verified in the repository."
            ),
            text=(
                f"{file_path} {function_name} line_start={line_start} "
                f"hit_count={hit_count}"
            ),
            provenance={
                **_structured_workflow_output_provenance(
                    task_run=task_run,
                    output=output,
                    output_id=output_id,
                    output_evidence_id=output_evidence_id,
                    path=path,
                    sha256=sha256,
                ),
                "file_path": file_path,
                "function_name": function_name,
                "line_start": line_start,
                "hit_count": hit_count,
                "source_verified": source_verified,
            },
        )
        evidence_ids.append(gap_evidence_id)
        if source_verified:
            _add_workbench_source_slice(
                store=store,
                evidence_id=gap_evidence_id,
                repo_path=task_run.repo_path,
                rel_path=file_path,
                line_start=line_start or 1,
            )
    return evidence_ids, rejected


_SOURCE_EXTENSIONS = {".c", ".h", ".cc", ".cpp", ".hpp", ".py", ".go", ".rs", ".java", ".ts", ".tsx", ".js", ".jsx"}


def _source_scope_file_items(payload: dict[str, Any]) -> list[Any]:
    items: list[Any] = []
    for key in ("files", "source_files", "candidate_files"):
        value = payload.get(key)
        if isinstance(value, list):
            items.extend(value)
    return items


def _source_scope_top_level_symbols(payload: dict[str, Any]) -> list[Any]:
    value = payload.get("symbols")
    return value if isinstance(value, list) else []


def _source_scope_item_path(item: Any) -> str:
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return ""
    for key in ("path", "file_path", "file", "entry_file"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _source_scope_item_reason(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    return str(item.get("reason") or item.get("evidence") or "").strip()


def _source_scope_item_symbols(item: Any) -> list[Any]:
    if not isinstance(item, dict):
        return []
    value = item.get("symbols")
    if isinstance(value, list):
        return value
    return []


def _source_scope_symbol_name(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return ""
    for key in ("name", "symbol", "function_name", "entry_symbol"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _source_scope_item_line_start(item: Any) -> int:
    if not isinstance(item, dict):
        return 1
    for key in ("line_start", "start_line", "line"):
        value = _safe_int(item.get(key))
        if value > 0:
            return value
    for symbol_item in _source_scope_item_symbols(item):
        if isinstance(symbol_item, dict):
            for key in ("line_start", "start_line", "line"):
                value = _safe_int(symbol_item.get(key))
                if value > 0:
                    return value
    return 1


def _validated_repo_source_path(repo_path: str, candidate_path: str) -> tuple[str, Path] | None:
    candidate_text = str(candidate_path or "").replace("\\", "/").strip()
    if not candidate_text:
        return None
    try:
        repo = Path(repo_path).resolve()
    except OSError:
        return None
    candidate = Path(candidate_text)
    try:
        resolved = candidate.resolve() if candidate.is_absolute() else (repo / candidate).resolve()
    except OSError:
        return None
    if resolved != repo and repo not in resolved.parents:
        return None
    if not resolved.exists() or not resolved.is_file():
        return None
    if resolved.suffix.lower() not in _SOURCE_EXTENSIONS:
        return None
    try:
        rel_path = resolved.relative_to(repo).as_posix()
    except ValueError:
        return None
    return rel_path, resolved


def _add_workbench_source_slice(
    *,
    store: EvidenceMemoryStore,
    evidence_id: str,
    repo_path: str,
    rel_path: str,
    line_start: int = 1,
) -> str | None:
    resolved = _validated_repo_source_path(repo_path, rel_path)
    if resolved is None:
        return None
    normalized_path, resolved_path = resolved
    try:
        data = resolved_path.read_bytes()
    except OSError:
        return None
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if not lines:
        return None
    max_lines = _safe_int(getattr(settings, "agent_discovery_source_slice_lines", 120)) or 120
    max_lines = max(1, max_lines)
    anchor = line_start if line_start > 0 else 1
    start_line = max(1, anchor - (max_lines // 2))
    end_line = min(len(lines), start_line + max_lines - 1)
    start_line = max(1, min(start_line, max(1, end_line - max_lines + 1)))
    excerpt = "\n".join(lines[start_line - 1:end_line])
    return store.add_source_slice(
        evidence_id=evidence_id,
        file_path=normalized_path,
        start_line=start_line,
        end_line=end_line,
        excerpt=excerpt,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _evidence_text_from_output(path: Path, data: bytes, *, fallback: str) -> str:
    if path.suffix.lower() in {".json", ".md", ".txt", ".patch", ".diff", ".log"}:
        return data[:16000].decode("utf-8", errors="replace")
    return fallback


def _object_text_from_task_run(task_run: Any, snapshot: Any) -> str:
    if isinstance(snapshot, dict) and snapshot.get("mr_url"):
        return str(snapshot["mr_url"])
    for value in (task_run.input_snapshot or {}).values():
        if isinstance(value, str) and value:
            return value
    return task_run.workflow_id


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _artifact_manifest(task_dir: Path) -> list[dict[str, Any]]:
    return build_task_artifact_manifest(task_dir)


def _build_task_acceptance_audit(task_run: Any) -> dict[str, Any]:
    task_dir = Path(task_run.artifact_dir)
    artifacts = {
        item.get("relative_path"): item
        for item in build_task_artifact_manifest(task_dir)
        if isinstance(item, dict)
    }
    checks: list[dict[str, Any]] = []
    required_root = [
        ("task_run", "task_run.json", "prepared task run snapshot"),
        ("input_snapshot", "input_snapshot.json", "frozen user inputs"),
        ("workflow_snapshot", "workflow_snapshot.json", "frozen workflow definition"),
        ("workflow_contract", "workflow_contract.json", "workflow input/output contract"),
        ("task_bundle", "task_bundle.json", "CodeTalk-to-Agent handoff bundle"),
        ("agent_instructions", "agent_instructions.json", "repo-local Agent instructions"),
        ("provider_snapshot", "provider_snapshot.json", "provider capability ownership matrix"),
        ("provider_readiness", "provider_readiness.json", "provider readiness diagnostics"),
        ("agent_mcp_requests", "agent_mcp_requests.json", "Agent-owned MCP boundary"),
        (
            "context_discovery_decision",
            "context_discovery_decision.json",
            "fast-context/local/index/Agent fallback decision",
        ),
        ("context_bundle", "context_bundle.json", "memory and semantic context bundle"),
        ("memory_retrieval", "memory_retrieval.json", "Evidence Memory retrieval trace"),
        ("source_read_chain", "source_read_chain.json", "source-read audit chain"),
        (
            "evidence_consumption_trajectory",
            "evidence_consumption_trajectory.json",
            "retrieved/read/used trajectory",
        ),
        ("degraded_retrieval", "degraded_retrieval.json", "degraded provider decisions"),
        (
            "black_box_generation_policy",
            "black_box_generation_policy.json",
            "semantic-library usage boundary for black-box case generation",
        ),
        ("task_artifact_manifest", "task_artifact_manifest.json", "artifact inventory"),
    ]
    for check_id, relative_path, description in required_root:
        checks.append(_acceptance_file_check(
            check_id=check_id,
            relative_path=relative_path,
            artifacts=artifacts,
            description=description,
            severity="required",
        ))
    agent_instruction_policy_expected = _expected_agent_instruction_policy(task_dir)
    provider_readiness = _read_json(task_dir / "provider_readiness.json")
    checks.extend(_acceptance_provider_readiness_checks(provider_readiness))

    execution_payload = _read_json(task_dir / "workflow_execution.json")
    workflow_execution_exists = "workflow_execution.json" in artifacts
    checks.append(_acceptance_file_check(
        check_id="workflow_execution",
        relative_path="workflow_execution.json",
        artifacts=artifacts,
        description="workflow execution result and audit summary",
        severity="required",
        missing_reason="workflow_not_executed_or_execution_artifact_missing",
    ))
    if "workflow_outputs.json" in artifacts:
        checks.append(_acceptance_file_check(
            check_id="workflow_outputs",
            relative_path="workflow_outputs.json",
            artifacts=artifacts,
            description="collected workflow outputs",
            severity="required" if workflow_execution_exists else "recommended",
        ))
        checks.extend(_acceptance_workflow_output_checks(
            _read_json(task_dir / "workflow_outputs.json"),
        ))
    evidence_memory_expected = _workflow_declares_evidence_memory(task_run.workflow_snapshot)
    if "workflow_output_materialization.json" in artifacts or evidence_memory_expected:
        checks.append(_acceptance_file_check(
            check_id="workflow_output_materialization",
            relative_path="workflow_output_materialization.json",
            artifacts=artifacts,
            description="accepted/rejected Evidence Memory materialization",
            severity="required" if evidence_memory_expected else "recommended",
            missing_reason="evidence_memory_declared_but_materialization_artifact_missing",
        ))
    semantic_import_expected = _workflow_declares_semantic_import(task_run.workflow_snapshot)
    if "semantic_import_outputs_by_step.json" in artifacts or semantic_import_expected:
        checks.append(_acceptance_file_check(
            check_id="semantic_import_outputs",
            relative_path="semantic_import_outputs_by_step.json",
            artifacts=artifacts,
            description="semantic import output contract passed to Agent runs",
            severity="required" if semantic_import_expected else "recommended",
            missing_reason="semantic_import_declared_but_contract_artifact_missing",
        ))
    if "semantic_output_import.json" in artifacts or semantic_import_expected:
        checks.append(_acceptance_file_check(
            check_id="semantic_output_import",
            relative_path="semantic_output_import.json",
            artifacts=artifacts,
            description="semantic library import for declared test-case outputs",
            severity="required" if semantic_import_expected else "recommended",
            missing_reason="semantic_import_declared_but_artifact_missing",
        ))

    rerun_plan_severity = "recommended"
    if isinstance(execution_payload, dict) and execution_payload.get("status") in {
        "invalid",
        "error",
        "timeout",
    }:
        rerun_plan_severity = "required"
    if "task_rerun_plan.json" in artifacts or rerun_plan_severity == "required":
        checks.append(_acceptance_file_check(
            check_id="task_rerun_plan",
            relative_path="task_rerun_plan.json",
            artifacts=artifacts,
            description="rerun plan for incomplete or failed Agent work",
            severity=rerun_plan_severity,
        ))

    for agent_run in task_run.agent_runs or []:
        if not isinstance(agent_run, dict):
            continue
        step_id = str(agent_run.get("step_id") or "")
        if not step_id:
            continue
        base = f"agent_runs/{step_id}"
        for suffix, description in [
            ("agent_run.json", "Agent run envelope and session policy"),
            ("task_bundle.json", "per-step Agent task bundle"),
            ("workflow_snapshot.json", "per-step workflow snapshot"),
            ("agent_output_contract.json", "per-step Agent output contract"),
            ("execution_input.json", "actual Agent stdin and launch envelope"),
            ("execution_result.json", "Agent process result"),
            ("agent_replay_plan.json", "Agent replay plan and audit hashes"),
            ("raw_output.txt", "redacted Agent stdout/stderr"),
            ("provider_diagnostics.json", "provider launch/readiness diagnostics"),
            ("agent_run_lifecycle.json", "Agent run lifecycle and validation summary"),
        ]:
            relative_path = f"{base}/{suffix}"
            check_name = suffix.removesuffix(".json").removesuffix(".txt")
            if check_name == "agent_run":
                check_id = f"agent_run:{step_id}"
            else:
                check_id = f"agent_{check_name}:{step_id}"
            checks.append(_acceptance_file_check(
                check_id=check_id,
                relative_path=relative_path,
                artifacts=artifacts,
                description=description,
                severity="required",
            ))
        if agent_instruction_policy_expected:
            checks.extend([
                _acceptance_agent_instruction_policy_check(
                    check_id=f"agent_instruction_policy:{step_id}:execution_input",
                    relative_path=f"{base}/execution_input.json",
                    task_dir=task_dir,
                    expected=agent_instruction_policy_expected,
                    description=f"Agent instruction policy in step {step_id} execution input",
                ),
                _acceptance_agent_instruction_policy_check(
                    check_id=f"agent_instruction_policy:{step_id}:agent_replay_plan",
                    relative_path=f"{base}/agent_replay_plan.json",
                    task_dir=task_dir,
                    expected=agent_instruction_policy_expected,
                    description=f"Agent instruction policy in step {step_id} replay plan",
                ),
            ])
        for artifact_name in agent_run.get("required_artifacts") or []:
            artifact = str(artifact_name)
            checks.append(_acceptance_file_check(
                check_id=f"agent_required_artifact:{step_id}:{artifact}",
                relative_path=f"{base}/{artifact}",
                artifacts=artifacts,
                description=f"required Agent artifact for step {step_id}",
                severity="required",
            ))
        lifecycle = _read_json(task_dir / base / "agent_run_lifecycle.json")
        turn_count = _safe_int(
            lifecycle.get("turn_count") if isinstance(lifecycle, dict) else None
        )
        source_slice_request_count = _safe_int(
            lifecycle.get("source_slice_request_count") if isinstance(lifecycle, dict) else None
        )
        injected_source_slice_count = _safe_int(
            lifecycle.get("injected_source_slice_count") if isinstance(lifecycle, dict) else None
        )
        if source_slice_request_count:
            checks.append(_acceptance_file_check(
                check_id=f"agent_source_slice_requests:{step_id}",
                relative_path=f"{base}/source_slice_requests.json",
                artifacts=artifacts,
                description="Agent-requested source slice list",
                severity="required",
            ))
        if source_slice_request_count or injected_source_slice_count:
            checks.append(_acceptance_file_check(
                check_id=f"agent_source_slices:{step_id}",
                relative_path=f"{base}/source_slices.json",
                artifacts=artifacts,
                description="CodeTalk-validated source slices injected into the next turn",
                severity="required",
            ))
        for turn_index in range(1, turn_count + 1):
            turn_base = f"{base}/turns/turn_{turn_index}"
            for suffix, description in [
                ("task_bundle.json", "per-turn Agent task bundle"),
                ("agent_output_contract.json", "per-turn Agent output contract"),
                ("execution_input.json", "per-turn Agent launch envelope"),
                ("execution_result.json", "per-turn Agent process result"),
                ("agent_replay_plan.json", "per-turn Agent replay plan and audit hashes"),
                ("raw_output.txt", "per-turn redacted stdout/stderr"),
                ("provider_diagnostics.json", "per-turn provider diagnostics"),
            ]:
                relative_path = f"{turn_base}/{suffix}"
                check_name = suffix.removesuffix(".json").removesuffix(".txt")
                checks.append(_acceptance_file_check(
                    check_id=f"agent_turn_{check_name}:{step_id}:turn_{turn_index}",
                    relative_path=relative_path,
                    artifacts=artifacts,
                    description=description,
                    severity="required",
                ))
            if agent_instruction_policy_expected:
                checks.extend([
                    _acceptance_agent_instruction_policy_check(
                        check_id=(
                            f"agent_turn_instruction_policy:{step_id}:"
                            f"turn_{turn_index}:execution_input"
                        ),
                        relative_path=f"{turn_base}/execution_input.json",
                        task_dir=task_dir,
                        expected=agent_instruction_policy_expected,
                        description=(
                            f"Agent instruction policy in step {step_id} "
                            f"turn {turn_index} execution input"
                        ),
                    ),
                    _acceptance_agent_instruction_policy_check(
                        check_id=(
                            f"agent_turn_instruction_policy:{step_id}:"
                            f"turn_{turn_index}:agent_replay_plan"
                        ),
                        relative_path=f"{turn_base}/agent_replay_plan.json",
                        task_dir=task_dir,
                        expected=agent_instruction_policy_expected,
                        description=(
                            f"Agent instruction policy in step {step_id} "
                            f"turn {turn_index} replay plan"
                        ),
                    ),
                ])
            if source_slice_request_count and turn_index == 1:
                checks.append(_acceptance_file_check(
                    check_id=(
                        f"agent_turn_source_slice_requests:{step_id}:turn_{turn_index}"
                    ),
                    relative_path=f"{turn_base}/source_slice_requests.json",
                    artifacts=artifacts,
                    description="per-turn Agent source slice request artifact",
                    severity="required",
                    missing_reason="not_present_for_this_turn",
                ))
            if (
                (source_slice_request_count or injected_source_slice_count)
                and turn_count > 1
                and turn_index == turn_count
            ):
                checks.append(_acceptance_file_check(
                    check_id=f"agent_turn_source_slices:{step_id}:turn_{turn_index}",
                    relative_path=f"{turn_base}/source_slices.json",
                    artifacts=artifacts,
                    description="per-turn injected source slice artifact",
                    severity="required",
                    missing_reason="not_present_for_this_turn",
                ))

    required_checks = [item for item in checks if item.get("severity") == "required"]
    missing_required = [
        item for item in required_checks
        if item.get("status") not in {"ok", "accepted"}
    ]
    recommended_missing = [
        item for item in checks
        if item.get("severity") == "recommended" and item.get("status") not in {"ok", "accepted"}
    ]
    return {
        "task_run_id": task_run.task_run_id,
        "workflow_id": task_run.workflow_id,
        "workspace_id": task_run.workspace_id,
        "status": "ready" if not missing_required else "incomplete",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "required_checks": len(required_checks),
            "missing_required": len(missing_required),
            "recommended_checks": len(checks) - len(required_checks),
            "missing_recommended": len(recommended_missing),
            "artifact_count": len(artifacts),
        },
        "checks": checks,
        "missing_required": missing_required,
        "missing_recommended": recommended_missing,
    }


def _workflow_declares_semantic_import(workflow_snapshot: Any) -> bool:
    if not isinstance(workflow_snapshot, dict):
        return False
    for output in workflow_snapshot.get("outputs") or []:
        if not isinstance(output, dict):
            continue
        semantic_import = output.get("semantic_import")
        if semantic_import is True:
            return True
        if isinstance(semantic_import, dict) and semantic_import.get("enabled", True) is not False:
            return True
    return False


def _workflow_declares_evidence_memory(workflow_snapshot: Any) -> bool:
    if not isinstance(workflow_snapshot, dict):
        return False
    for output in workflow_snapshot.get("outputs") or []:
        if not isinstance(output, dict):
            continue
        evidence_memory = output.get("evidence_memory")
        if evidence_memory is True:
            return True
        if isinstance(evidence_memory, dict) and evidence_memory.get("enabled", True) is not False:
            return True
    return False


def _acceptance_provider_readiness_checks(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    agent_cli_providers = payload.get("agent_cli_providers")
    if not isinstance(agent_cli_providers, dict):
        return []
    checks: list[dict[str, Any]] = []
    for provider, item in sorted(agent_cli_providers.items()):
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "unknown")
        ok = status in {"available", "configured"}
        reason = str(item.get("reason") or "")
        deployment_evidence = (
            item.get("deployment_evidence")
            if isinstance(item.get("deployment_evidence"), dict)
            else {}
        )
        checks.append({
            "id": f"provider_readiness_agent:{provider}",
            "status": "ok" if ok else "missing",
            "severity": "required",
            "relative_path": "provider_readiness.json",
            "kind": "provider_readiness",
            "provider": str(provider),
            "provider_status": status,
            "configured_command": str(item.get("configured_command") or ""),
            "command": str(item.get("command") or ""),
            "used_fallback": bool(item.get("used_fallback", False)),
            "startup_probe_endpoint": str(item.get("startup_probe_endpoint") or ""),
            "deployment_evidence_conflict": bool(item.get("deployment_evidence_conflict", False)),
            "deployment_task_probe_status": str(deployment_evidence.get("task_probe_status") or ""),
            "deployment_probe_id": str(deployment_evidence.get("probe_id") or ""),
            "deployment_evidence_status": str(deployment_evidence.get("evidence_status") or ""),
            "deployment_evidence_source": str(deployment_evidence.get("evidence_source") or ""),
            "description": "Agent CLI provider readiness for this task",
            "reason": reason or ("" if ok else status),
        })
    return checks


def _acceptance_workflow_output_checks(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    outputs = payload.get("outputs")
    if not isinstance(outputs, list):
        return []
    checks: list[dict[str, Any]] = []
    for index, item in enumerate(outputs):
        if not isinstance(item, dict):
            continue
        output_id = str(item.get("id") or f"output_{index + 1}")
        status = str(item.get("status") or "unknown")
        ok = status == "ok"
        schema_errors = item.get("schema_errors") if isinstance(item.get("schema_errors"), list) else []
        checks.append({
            "id": f"workflow_output:{output_id}",
            "status": "ok" if ok else "missing",
            "severity": "required",
            "relative_path": "workflow_outputs.json",
            "kind": "workflow_outputs",
            "output_id": output_id,
            "output_status": status,
            "output_type": str(item.get("type") or ""),
            "artifact": str(item.get("artifact") or ""),
            "producer_step": str(item.get("from") or ""),
            "reason": str(item.get("reason") or ("" if ok else status)),
            "schema_errors": [str(error) for error in schema_errors],
            "description": "declared workflow output status",
        })
    return checks


def _expected_agent_instruction_policy(task_dir: Path) -> dict[str, Any]:
    payload = _read_json(task_dir / "agent_instructions.json")
    if not isinstance(payload, dict):
        return {}
    files = [
        item for item in payload.get("files") or []
        if isinstance(item, dict) and str(item.get("relative_path") or "").strip()
    ]
    if not files:
        return {}
    return {
        "files": [
            {
                "relative_path": str(item.get("relative_path") or ""),
                "sha256": str(item.get("sha256") or ""),
            }
            for item in files
        ],
        "file_count": len(files),
    }


def _acceptance_agent_instruction_policy_check(
    *,
    check_id: str,
    relative_path: str,
    task_dir: Path,
    expected: dict[str, Any],
    description: str,
) -> dict[str, Any]:
    expected_files = [
        item for item in expected.get("files") or []
        if isinstance(item, dict) and str(item.get("relative_path") or "")
    ]
    payload = _read_json(task_dir / relative_path)
    base = {
        "id": check_id,
        "severity": "required",
        "relative_path": relative_path,
        "kind": workbench_artifact_kind(relative_path),
        "description": description,
        "expected_files": expected_files,
    }
    if not isinstance(payload, dict):
        return {
            **base,
            "status": "missing",
            "reason": "artifact_json_unreadable",
        }
    policy = payload.get("agent_instruction_policy")
    if not isinstance(policy, dict):
        return {
            **base,
            "status": "missing",
            "reason": "agent_instruction_policy_missing",
        }
    policy_files = [
        item for item in policy.get("files") or []
        if isinstance(item, dict) and str(item.get("relative_path") or "")
    ]
    policy_by_path = {
        str(item.get("relative_path") or ""): str(item.get("sha256") or "")
        for item in policy_files
    }
    missing_files = [
        item for item in expected_files
        if policy_by_path.get(str(item.get("relative_path") or ""))
        != str(item.get("sha256") or "")
    ]
    if missing_files:
        return {
            **base,
            "status": "missing",
            "reason": "agent_instruction_policy_incomplete",
            "policy_file_count": len(policy_files),
            "missing_files": missing_files,
        }
    return {
        **base,
        "status": "ok",
        "reason": "",
        "policy_file_count": len(policy_files),
        "fast_context_first": bool(policy.get("fast_context_first")),
    }


def _acceptance_file_check(
    *,
    check_id: str,
    relative_path: str,
    artifacts: dict[Any, Any],
    description: str,
    severity: str,
    missing_reason: str = "artifact_missing",
) -> dict[str, Any]:
    artifact = artifacts.get(relative_path)
    if isinstance(artifact, dict):
        return {
            "id": check_id,
            "status": "ok",
            "severity": severity,
            "relative_path": relative_path,
            "kind": artifact.get("kind") or workbench_artifact_kind(relative_path),
            "sha256": artifact.get("sha256") or "",
            "size_bytes": artifact.get("size_bytes") or 0,
            "description": description,
        }
    return {
        "id": check_id,
        "status": "missing",
        "severity": severity,
        "relative_path": relative_path,
        "kind": workbench_artifact_kind(relative_path),
        "description": description,
        "reason": missing_reason,
    }


def _artifact_kind(relative_path: str) -> str:
    return workbench_artifact_kind(relative_path)


def _artifact_preview(path: Path, data: bytes, *, max_chars: int = 1200) -> str:
    return artifact_preview(path, data, max_chars=max_chars)


def _resolve_task_artifact_path(task_dir: Path, artifact_path: str) -> Path:
    normalized = str(artifact_path or "").replace("\\", "/").strip("/")
    if not normalized:
        raise HTTPException(status_code=400, detail="artifact path is required")
    relative = Path(normalized)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise HTTPException(status_code=400, detail="invalid artifact path")
    try:
        root = task_dir.resolve()
        resolved = (root / relative).resolve()
    except OSError:
        raise HTTPException(status_code=400, detail="invalid artifact path")
    if resolved != root and root not in resolved.parents:
        raise HTTPException(status_code=400, detail="artifact path escapes task directory")
    return resolved


def _artifact_content_payload(task_dir: Path, path: Path, *, max_chars: int) -> dict[str, Any]:
    data = path.read_bytes()
    relative_path = path.resolve().relative_to(task_dir.resolve()).as_posix()
    is_text = path.suffix.lower() in {".json", ".md", ".txt", ".patch", ".diff", ".log"}
    content = ""
    truncated = False
    if is_text:
        text = data.decode("utf-8", errors="replace")
        truncated = len(text) > max_chars
        content = text[:max_chars]
    return {
        "relative_path": relative_path,
        "path": str(path.resolve()),
        "kind": _artifact_kind(relative_path),
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "is_text": is_text,
        "truncated": truncated,
        "content": content,
    }


def _validate_task_rerun_plan(*, task_run: Any, plan: dict[str, Any]) -> dict[str, Any]:
    task_dir = Path(str(task_run.artifact_dir))
    checks = [
        _rerun_file_check("task_run", task_dir / "task_run.json"),
        _rerun_file_check("input_snapshot", task_dir / "input_snapshot.json"),
        _rerun_file_check("task_bundle", task_dir / "task_bundle.json"),
        _rerun_file_check("workflow_snapshot", task_dir / "workflow_snapshot.json"),
        _rerun_repo_check(str(task_run.repo_path or "")),
    ]
    plan_task_run_id = str(plan.get("task_run_id") or "")
    if plan_task_run_id != task_run.task_run_id:
        checks.append({
            "id": "plan_task_run_id",
            "status": "blocked",
            "reason": "plan task_run_id does not match requested task run",
            "expected": task_run.task_run_id,
            "actual": plan_task_run_id,
        })

    agent_runs_by_step = {
        str(item.get("step_id") or ""): item
        for item in task_run.agent_runs
        if isinstance(item, dict)
    }
    step_validations: list[dict[str, Any]] = []
    for step in plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("step_id") or "")
        agent_run = agent_runs_by_step.get(step_id, {})
        artifact_dir = Path(str(agent_run.get("artifact_dir") or ""))
        artifact_dir_exists = bool(artifact_dir and artifact_dir.exists() and artifact_dir.is_dir())
        overwrite_risk_artifacts = [
            {
                "artifact": str(artifact or ""),
                "exists": bool(artifact_dir_exists and (artifact_dir / str(artifact or "")).exists()),
            }
            for artifact in step.get("overwrite_risk_artifacts") or []
        ]
        status = "ready" if artifact_dir_exists else "blocked"
        step_payload = {
            "step_id": step_id,
            "status": status,
            "recommended_action": str(step.get("recommended_action") or ""),
            "failure_kind": str(step.get("failure_kind") or ""),
            "artifact_dir": str(artifact_dir),
            "artifact_dir_exists": artifact_dir_exists,
            "missing_artifacts": [str(item) for item in step.get("missing_artifacts") or []],
            "overwrite_risk_artifacts": overwrite_risk_artifacts,
        }
        if not artifact_dir_exists:
            step_payload["reason"] = "agent step artifact directory is missing"
        step_validations.append(step_payload)

    blocked = any(item.get("status") == "blocked" for item in checks + step_validations)
    return {
        "task_run_id": task_run.task_run_id,
        "status": "blocked" if blocked else "ready",
        "can_rerun": not blocked,
        "plan_status": str(plan.get("status") or ""),
        "checks": checks,
        "steps": step_validations,
    }


def _rerun_file_check(check_id: str, path: Path) -> dict[str, Any]:
    exists = path.exists() and path.is_file()
    return {
        "id": check_id,
        "status": "ok" if exists else "blocked",
        "path": str(path),
        "reason": "" if exists else "required task-run artifact is missing",
    }


def _rerun_repo_check(repo_path: str) -> dict[str, Any]:
    path = Path(repo_path) if repo_path else Path()
    exists = bool(repo_path and path.exists() and path.is_dir())
    return {
        "id": "repo_path",
        "status": "ok" if exists else "blocked",
        "path": repo_path,
        "reason": "" if exists else "repo path is missing or not a directory",
    }


def _write_task_rerun_execution_artifacts(
    *,
    task_dir: Path,
    result: dict[str, Any],
) -> None:
    history_path = task_dir / "task_rerun_history.json"
    history = _read_json(history_path)
    records = history.get("records") if isinstance(history, dict) else []
    if not isinstance(records, list):
        records = []
    sequence = len(records) + 1
    task_run_id = str(result.get("execution", {}).get("task_run_id") or "")
    payload = {
        **result,
        "rerun_id": f"{task_run_id}_rerun_{sequence}",
        "sequence": sequence,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(task_dir / "task_rerun_execution.json", payload)
    records.append(payload)
    _write_json(
        history_path,
        {
            "task_run_id": task_run_id,
            "count": len(records),
            "records": records,
        },
    )


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
