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
    providers = [
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
        "command": command,
        "fallback_commands": fallback_commands,
        "readonly_args": list(spec.readonly_args),
        "command_hint_env": spec.command_hint_env,
        "capabilities": external_agent_provider_capabilities(provider_id),
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
        },
        "unavailable_behavior": (
            "CodeTalk records fast-context as unavailable and continues with local "
            "search, GitNexus/CGC, and Agent CLI providers."
        ),
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
        evidence_ids.append(store.upsert_evidence_item(
            run_id=task_run.task_run_id,
            workspace_id=task_run.workspace_id,
            kind="workflow_output",
            subject_key=f"{task_run.task_run_id}/{output_id}",
            status="verified_output",
            source=str(output.get("from") or "workflow"),
            path=str(path),
            reason="Workflow output passed CodeTalk local artifact validation.",
            text=text,
            provenance={
                "task_run_id": task_run.task_run_id,
                "workflow_id": task_run.workflow_id,
                "output": output,
                "artifact": "workflow_outputs.json",
                "sha256": sha256,
                "size_bytes": len(data),
            },
        ))
    return evidence_ids, rejected


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
    if name == "context_bundle.json":
        return "context_bundle"
    if name == "workflow_outputs.json":
        return "workflow_outputs"
    if name == "workflow_execution.json":
        return "workflow_execution"
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
