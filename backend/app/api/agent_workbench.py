"""Agent workbench APIs: workflows, evidence memory, and test semantics."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import settings
from app.services.agent_run_harness import AgentRunHarness, ArtifactValidationHarness
from app.services.evidence_memory import EvidenceMemoryStore
from app.services.external_agent_discovery import (
    external_agent_provider_capabilities,
    external_agent_provider_specs,
    split_agent_command,
)
from app.services.test_semantic_library import (
    SemanticCaseValidationError,
    TestSemanticLibraryStore,
)
from app.services.workbench_task_run import WorkbenchTaskRunPreparer
from app.services.workbench_task_run import WorkbenchTaskRunStore
from app.services.workbench_task_run import build_codetalk_provider_snapshot
from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
from app.services.workflow_dsl import WorkflowStore, WorkflowValidationError
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


@router.post("/workflow-presets/{preset_id}/install", status_code=201)
async def install_builtin_workflow_preset(preset_id: str) -> dict[str, Any]:
    try:
        workflow = install_workflow_preset(_workflow_store(), preset_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown workflow preset: {preset_id}")
    return _workflow_response(workflow.raw)


@router.get("/provider-capabilities")
async def list_provider_capabilities() -> dict[str, Any]:
    """Return a side-effect-free capability matrix for Workbench Agent routing."""
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
        result = WorkbenchWorkflowRunner(_task_runs_dir()).execute_task_run(
            task_run_id,
            timeout_sec=payload.timeout_sec,
            stop_on_error=payload.stop_on_error,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return asdict(result)


@router.post("/task-runs/{task_run_id}/materialize-outputs")
async def materialize_task_run_outputs(task_run_id: str) -> dict[str, Any]:
    try:
        task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")
    workflow_outputs = _read_json(Path(task_run.artifact_dir) / "workflow_outputs.json")
    if not isinstance(workflow_outputs, dict):
        raise HTTPException(
            status_code=400,
            detail="workflow outputs have not been generated",
        )
    evidence_ids, rejected = _materialize_workflow_output_evidence(
        task_run=task_run,
        workflow_outputs=workflow_outputs,
    )
    return {
        "status": "ok" if not rejected else "partial",
        "evidence_count": len(evidence_ids),
        "evidence_ids": evidence_ids,
        "rejected_outputs": rejected,
    }


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


@router.get("/task-runs/{task_run_id}")
async def get_task_run(task_run_id: str) -> dict[str, Any]:
    try:
        task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")
    return asdict(task_run)


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
    max_chars: int = Query(20000, ge=1, le=200000),
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


def _workflow_response(payload: dict[str, Any]) -> dict[str, Any]:
    return dict(payload)


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
        "unavailable_behavior": (
            "CodeTalk records fast-context as unavailable and continues with local "
            "search, GitNexus/CGC, and Agent CLI providers."
        ),
    }


def _codetalk_provider_matrix_items() -> list[dict[str, Any]]:
    return list(build_codetalk_provider_snapshot().values())


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
            rejected.append({"output": output_id, "reason": "output_not_ok"})
            continue
        path = Path(str(output.get("path") or ""))
        if not path.exists() or not path.is_file():
            rejected.append({"output": output_id, "reason": "output_file_missing"})
            continue
        data = path.read_bytes()
        sha256 = hashlib.sha256(data).hexdigest()
        if output.get("sha256") and output.get("sha256") != sha256:
            rejected.append({"output": output_id, "reason": "output_sha256_mismatch"})
            continue
        text = _evidence_text_from_output(path, data, fallback=str(output.get("preview") or ""))
        base_provenance = {
            "task_run_id": task_run.task_run_id,
            "workflow_id": task_run.workflow_id,
            "output": output,
            "artifact": "workflow_outputs.json",
            "sha256": sha256,
            "size_bytes": len(data),
        }
        output_evidence_id = store.upsert_evidence_item(
            run_id=task_run.task_run_id,
            workspace_id=task_run.workspace_id,
            kind="workflow_output",
            subject_key=f"{task_run.task_run_id}/{output_id}",
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
    if path.name == "source_scope.json" or output_id in {"source_scope", "scope"}:
        return (
            _materialize_source_scope_evidence(
                store=store,
                task_run=task_run,
                output=output,
                output_id=output_id,
                output_evidence_id=output_evidence_id,
                path=path,
                data=data,
                sha256=sha256,
            ),
            [],
        )
    if path.name == "evidence_cards.json" or output_id == "evidence_cards":
        return (
            _materialize_evidence_card_output(
                store=store,
                task_run=task_run,
                output=output,
                output_id=output_id,
                output_evidence_id=output_evidence_id,
                path=path,
                data=data,
                sha256=sha256,
            ),
            [],
        )
    if path.name == "uncovered_functions.json" or output_id == "uncovered_functions":
        return (
            _materialize_uncovered_function_evidence(
                store=store,
                task_run=task_run,
                output=output,
                output_id=output_id,
                output_evidence_id=output_evidence_id,
                path=path,
                data=data,
                sha256=sha256,
            ),
            [],
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
                "task_run_id": task_run.task_run_id,
                "workflow_id": task_run.workflow_id,
                "output_id": output_id,
                "output_evidence_id": output_evidence_id,
                "artifact_path": str(path),
                "sha256": sha256,
                "changed_file": item,
                "validation_source": validation_source,
            },
        ))
    return evidence_ids, rejected


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
) -> list[str]:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    evidence_ids: list[str] = []
    seen_files: set[str] = set()
    seen_symbols: set[tuple[str, str]] = set()
    for file_item in _source_scope_file_items(payload):
        candidate_path = _source_scope_item_path(file_item)
        resolved = _validated_repo_source_path(task_run.repo_path, candidate_path)
        if resolved is None:
            continue
        rel_path, resolved_path = resolved
        if rel_path not in seen_files:
            seen_files.add(rel_path)
            source_evidence_id = store.upsert_evidence_item(
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
                    "task_run_id": task_run.task_run_id,
                    "workflow_id": task_run.workflow_id,
                    "output_id": output_id,
                    "output_evidence_id": output_evidence_id,
                    "artifact_path": str(path),
                    "sha256": sha256,
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
                    "task_run_id": task_run.task_run_id,
                    "workflow_id": task_run.workflow_id,
                    "output_id": output_id,
                    "output_evidence_id": output_evidence_id,
                    "artifact_path": str(path),
                    "sha256": sha256,
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
        if resolved is None or not symbol_name:
            continue
        rel_path, _resolved_path = resolved
        symbol_key = (rel_path, symbol_name)
        if symbol_key in seen_symbols:
            continue
        seen_symbols.add(symbol_key)
        line_start = _safe_int(symbol_item.get("line_start"))
        evidence_ids.append(store.upsert_evidence_item(
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
                "task_run_id": task_run.task_run_id,
                "workflow_id": task_run.workflow_id,
                "output_id": output_id,
                "output_evidence_id": output_evidence_id,
                "artifact_path": str(path),
                "sha256": sha256,
                "file_path": rel_path,
                "symbol": symbol_name,
                "line_start": line_start,
            },
        ))
    return evidence_ids


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
) -> list[str]:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    cards = payload if isinstance(payload, list) else payload.get("evidence_cards") if isinstance(payload, dict) else []
    if not isinstance(cards, list):
        return []
    evidence_ids: list[str] = []
    seen_cards: set[str] = set()
    for card in cards:
        if not isinstance(card, dict):
            continue
        candidate_path = _source_scope_item_path(card)
        resolved = _validated_repo_source_path(task_run.repo_path, candidate_path)
        if resolved is None:
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
                "task_run_id": task_run.task_run_id,
                "workflow_id": task_run.workflow_id,
                "output_id": output_id,
                "output_evidence_id": output_evidence_id,
                "artifact_path": str(path),
                "sha256": sha256,
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
    return evidence_ids


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
) -> list[str]:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    evidence_ids: list[str] = []
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
        gap_evidence_id = store.upsert_evidence_item(
            run_id=task_run.task_run_id,
            workspace_id=task_run.workspace_id,
            kind="coverage_gap",
            subject_key=subject_key,
            status="verified_output",
            source=str(output.get("from") or "workflow"),
            path=file_path,
            symbol=function_name,
            reason="Uncovered function came from a locally verified workflow coverage output.",
            text=(
                f"{file_path} {function_name} line_start={line_start} "
                f"hit_count={hit_count}"
            ),
            provenance={
                "task_run_id": task_run.task_run_id,
                "workflow_id": task_run.workflow_id,
                "output_id": output_id,
                "output_evidence_id": output_evidence_id,
                "artifact_path": str(path),
                "sha256": sha256,
                "file_path": file_path,
                "function_name": function_name,
                "line_start": line_start,
                "hit_count": hit_count,
            },
        )
        evidence_ids.append(gap_evidence_id)
        if _validated_repo_source_path(task_run.repo_path, file_path) is not None:
            _add_workbench_source_slice(
                store=store,
                evidence_id=gap_evidence_id,
                repo_path=task_run.repo_path,
                rel_path=file_path,
                line_start=line_start or 1,
            )
    return evidence_ids


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
    try:
        root = task_dir.resolve()
    except OSError:
        return []
    if not root.exists() or not root.is_dir():
        return []
    artifacts: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved != root and root not in resolved.parents:
            continue
        data = resolved.read_bytes()
        relative_path = resolved.relative_to(root).as_posix()
        item: dict[str, Any] = {
            "relative_path": relative_path,
            "path": str(resolved),
            "kind": _artifact_kind(relative_path),
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        preview = _artifact_preview(resolved, data)
        if preview:
            item["preview"] = preview
        artifacts.append(item)
    return artifacts


def _artifact_kind(relative_path: str) -> str:
    name = relative_path.rsplit("/", 1)[-1]
    if relative_path.endswith("/task_bundle.json"):
        return "agent_task_bundle"
    if name == "task_bundle.json":
        return "task_bundle"
    if name == "agent_instructions.json":
        return "agent_instructions"
    if name == "provider_snapshot.json":
        return "provider_snapshot"
    if name == "context_discovery_decision.json":
        return "context_discovery_decision"
    if name == "context_bundle.json":
        return "context_bundle"
    if name == "output_schemas_by_step.json":
        return "output_schemas"
    if name == "memory_retrieval.json":
        return "memory_retrieval"
    if name == "source_read_chain.json":
        return "source_read_chain"
    if name == "evidence_consumption_trajectory.json":
        return "evidence_consumption_trajectory"
    if name == "degraded_retrieval.json":
        return "degraded_retrieval"
    if name == "workflow_outputs.json":
        return "workflow_outputs"
    if name == "workflow_execution.json":
        return "workflow_execution"
    if name == "evidence_validation.json":
        return "evidence_validation"
    if name == "raw_output.txt":
        return "agent_raw_output"
    if name == "agent_run.json":
        return "agent_run"
    if name == "execution_input.json":
        return "agent_execution_input"
    if name.endswith(".json"):
        return "json"
    if name.endswith((".md", ".txt", ".patch", ".diff", ".log")):
        return "text"
    return "artifact"


def _artifact_preview(path: Path, data: bytes, *, max_chars: int = 1200) -> str:
    if path.suffix.lower() not in {".json", ".md", ".txt", ".patch", ".diff", ".log"}:
        return ""
    text = data[: max_chars * 4].decode("utf-8", errors="replace")
    return text[:max_chars]


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


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
