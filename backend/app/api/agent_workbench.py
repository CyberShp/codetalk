"""Agent workbench APIs: workflows, evidence memory, and test semantics."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import mimetypes
import re
import sys
import uuid
import zipfile
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.services.agent_run_harness import ArtifactValidationHarness
from app.services.harness_facade import AgentHarnessFacade, HarnessRunRequest
from app.services.agent_provider_settings import apply_persisted_agent_provider_settings
from app.services.agent_runtimes import get_agent_runtime_sync, list_agent_runtimes_sync
from app.services.agent_cli_bridge import probe_agent_runtime
from app.services.evidence_memory import EvidenceMemoryStore
from app.services.external_agent_discovery import (
    external_agent_provider_capabilities,
    external_agent_provider_spec,
    external_agent_provider_specs,
    probe_external_agent_startup,
    redact_agent_diagnostic_text,
    split_agent_command,
)
from app.services.test_semantic_library import (
    SemanticCaseValidationError,
    TestSemanticLibraryStore,
)
from app.services.test_activity_contract import (
    black_box_case_delivery_quality_gaps,
    black_box_expected_result_is_observable,
    black_box_oracle_basis_quality_gaps,
    black_box_steps_are_actionable,
    sfmea_mitigation_is_actionable,
)
from app.services.behavior_claim_validator import build_behavior_claim_audit_readiness
from app.services.workbench_artifact_manifest import (
    TEXT_ARTIFACT_SUFFIXES,
    artifact_preview,
    build_task_artifact_manifest,
    truncate_redacted_text,
    workbench_artifact_kind,
    write_task_artifact_manifest,
)
from app.services.workbench_task_run import WorkbenchTaskRunPreparer
from app.services.workbench_task_run import WorkbenchTaskRunStore
from app.services.workbench_task_run import BUILTIN_LLM_PROVIDER_ID
from app.services.workbench_task_run import agent_runtime_id_from_provider
from app.services.workbench_task_run import _agent_runtime_provider_snapshot_item
from app.services.workbench_task_run import _builtin_llm_provider_snapshot_item
from app.services.workbench_task_run import build_agent_cli_provider_diagnostics
from app.services.workbench_task_run import build_codetalk_provider_snapshot
from app.services.workbench_task_run import _evidence_item_payload
from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore
from app.services.workbench_workflow_runner import build_workflow_rerun_plan
from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
from app.services.workflow_dsl import (
    ALLOWED_INPUT_TYPES,
    ALLOWED_STEP_TYPES,
    WorkflowStore,
    WorkflowValidationError,
    audit_workflow_definition,
    validate_workflow_definition,
)
from app.services.workflow_presets import (
    active_builtin_workflow_presets,
    canonical_builtin_workflow_preset_id,
    get_workflow_preset,
    install_workflow_preset,
    reserved_builtin_workflow_ids,
    restore_builtin_workflow_presets,
)
from app.services.workflow_version_store import workflow_header_status
from app.services.workflow_node_registry import node_registry_payload

router = APIRouter(prefix="/api/workbench", tags=["agent-workbench"])

_TASK_RUN_TERMINAL_STATUSES = {
    "completed",
    "completed_empty",
    "needs_review",
    "failed",
    "error",
    "invalid",
    "needs_rework",
    "quality_blocked",
    "partial",
    "cancelled",
    "canceled",
    "interrupted",
}
_ACTIVE_TASK_RUN_IDS: set[str] = set()


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
    timeout_sec: int = Field(default=0, ge=0, le=3600)


class TaskRunExecuteRequest(BaseModel):
    timeout_sec: int = Field(default=0, ge=0, le=3600)
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
    timeout_sec: int = Field(default=0, ge=0, le=3600)
    stop_on_error: bool = True


class GenerateWorkflowDraftRequest(BaseModel):
    prompt: str = Field(min_length=8, max_length=8000)
    preferred_id: str = Field(default="", max_length=80)
    preferred_name: str = Field(default="", max_length=120)


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
    root = (settings.data_path / "workbench").expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _workflow_store() -> WorkflowStore:
    return WorkflowStore(_workbench_dir() / "workflows.db")


class _WorkflowCatalog:
    """Read-only built-in presets overlaid with user-editable workflows."""

    def __init__(self, store: WorkflowStore) -> None:
        self.store = store

    def get_workflow(self, workflow_id: str):
        if _is_builtin_workflow_id(workflow_id):
            preset = get_workflow_preset(workflow_id)
            return validate_workflow_definition(deepcopy(preset["definition"]))
        return self.store.get_workflow(workflow_id)

    def freeze_workflow_snapshot(self, workflow_id: str) -> dict[str, Any]:
        return dict(self.get_workflow(workflow_id).raw)

    def list_workflows(self):
        builtin_ids = _builtin_workflow_ids()
        builtin = [
            validate_workflow_definition(deepcopy(preset["definition"]))
            for preset in active_builtin_workflow_presets()
        ]
        custom = [
            item for item in self.store.list_workflows()
            if item.id not in builtin_ids
        ]
        return builtin + custom


def _workflow_store_with_builtin_presets() -> _WorkflowCatalog:
    return _WorkflowCatalog(_workflow_store())


def _builtin_workflow_ids() -> set[str]:
    return set(reserved_builtin_workflow_ids())


def _active_builtin_workflow_ids() -> set[str]:
    return {
        str(preset["definition"]["id"])
        for preset in active_builtin_workflow_presets()
    }


def _is_builtin_workflow_id(workflow_id: str) -> bool:
    return str(workflow_id or "").strip() in _builtin_workflow_ids()


def _is_active_builtin_workflow_id(workflow_id: str) -> bool:
    canonical = canonical_builtin_workflow_preset_id(str(workflow_id or "").strip())
    return canonical in _active_builtin_workflow_ids()


def _require_workflow_available_for_new_run(workflow_id: str) -> None:
    if _is_builtin_workflow_id(workflow_id) and not _is_active_builtin_workflow_id(
        workflow_id
    ):
        raise HTTPException(
            status_code=410,
            detail="该内置工作流已下线，仅保留历史任务与运行记录；请选择当前发布工作流。",
        )
    if workflow_header_status(
        _workbench_dir() / "workflows.db",
        workflow_id,
    ) == "archived":
        raise HTTPException(
            status_code=409,
            detail="该自建工作流已归档，仅保留历史任务与运行记录；请恢复工作流或选择其他工作流。",
        )


def _semantic_store() -> TestSemanticLibraryStore:
    return TestSemanticLibraryStore(_workbench_dir() / "test_semantics.db")


def _memory_store() -> EvidenceMemoryStore:
    return EvidenceMemoryStore(_workbench_dir() / "evidence_memory.db")


def _evidence_repo_path(store: EvidenceMemoryStore, item: Any) -> str:
    provenance = item.provenance or {}
    repo_path = str(provenance.get("repo_path") or "").strip()
    if repo_path:
        return repo_path
    try:
        return str(store.get_analysis_run(item.run_id).get("repo_path") or "")
    except KeyError:
        return ""


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


def _public_workbench_artifact_path(path: Path) -> str:
    return path.resolve().relative_to(_workbench_dir().resolve()).as_posix()


def _public_task_artifact_path(task_dir: Path, path: Path) -> str:
    return path.resolve().relative_to(task_dir.resolve()).as_posix()


def _public_repo_path_label(repo_path: Any) -> str:
    text = str(repo_path or "").strip()
    if not text:
        return "local-repo"
    try:
        return f"repo:{Path(text).expanduser().name or 'local-repo'}"
    except (OSError, RuntimeError):
        return "repo:local-repo"


def _redact_public_repo_paths(payload: Any, repo_path: Any) -> Any:
    raw = str(repo_path or "").strip()
    replacements = [raw] if raw else []
    if raw:
        try:
            resolved = str(Path(raw).expanduser().resolve())
            if resolved not in replacements:
                replacements.append(resolved)
        except (OSError, RuntimeError):
            pass
    if isinstance(payload, str):
        redacted = payload
        for item in replacements:
            if item:
                redacted = redacted.replace(item, "<repo>")
        return redacted
    if isinstance(payload, list):
        return [_redact_public_repo_paths(item, repo_path) for item in payload]
    if isinstance(payload, dict):
        return {
            key: _redact_public_repo_paths(value, repo_path)
            for key, value in payload.items()
        }
    return payload


def _public_task_run_payload(task_run: Any) -> dict[str, Any]:
    payload = asdict(task_run)
    private_repo_path = payload.get("repo_path")
    task_root = Path(str(payload.get("artifact_dir") or "")).resolve()
    payload.update(_public_task_run_runtime_summary(task_root))
    input_consumption = _load_public_input_consumption_ledger(task_root)
    if input_consumption:
        payload["input_consumption"] = input_consumption
    payload["run_ui_summary"] = _build_task_run_ui_summary(task_run, task_root)
    if "repo_path" in payload:
        payload["repo_path"] = _public_repo_path_label(payload.get("repo_path"))
    if "artifact_dir" in payload:
        payload["artifact_dir"] = "."
    agent_runs = payload.get("agent_runs")
    if isinstance(agent_runs, list):
        public_agent_runs: list[dict[str, Any]] = []
        for item in agent_runs:
            if not isinstance(item, dict):
                continue
            public_item = dict(item)
            artifact_dir = str(public_item.get("artifact_dir") or "")
            if artifact_dir:
                try:
                    resolved = Path(artifact_dir).resolve()
                    if resolved == task_root:
                        public_item["artifact_dir"] = "."
                    elif task_root in resolved.parents:
                        public_item["artifact_dir"] = resolved.relative_to(task_root).as_posix()
                    else:
                        public_item["artifact_dir"] = ""
                except OSError:
                    public_item["artifact_dir"] = ""
            public_agent_runs.append(public_item)
        payload["agent_runs"] = public_agent_runs
    return _redact_public_repo_paths(payload, private_repo_path)


def _load_public_input_consumption_ledger(task_root: Path) -> dict[str, Any]:
    """Expose the task-owned consumption ledger, never a mutable agent bundle."""
    payload = _read_json(task_root / "input_consumption.json")
    if not isinstance(payload, dict):
        return {}
    if payload.get("schema_version") != "input-consumption-v2":
        return {}
    return payload


def _public_task_run_runtime_summary(task_root: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    task_status = ""
    task_payload = _read_json(task_root / "task_run.json")
    if isinstance(task_payload, dict):
        task_status = str(task_payload.get("status") or "").strip()
        if task_status:
            summary["status"] = task_status
        runtime = task_payload.get("runtime")
        if isinstance(runtime, dict):
            summary["runtime"] = {
                "status": str(runtime.get("status") or task_status or "unknown"),
                "updated_at": str(runtime.get("updated_at") or ""),
                "started_at": str(runtime.get("started_at") or ""),
                "completed_at": str(runtime.get("completed_at") or ""),
            }
    execution = _read_json(task_root / "workflow_execution.json")
    if isinstance(execution, dict):
        status = str(execution.get("status") or "").strip()
        if status and task_status not in {"queued", "running", "cancelled", "interrupted"}:
            summary["status"] = status
        outputs = execution.get("outputs")
        if isinstance(outputs, list):
            summary["outputs"] = _public_workflow_output_summaries(outputs)
        step_results = execution.get("step_results")
        if isinstance(step_results, list):
            summary["step_results"] = step_results
        audit_summary = execution.get("audit_summary")
        if isinstance(audit_summary, dict):
            summary["audit_summary"] = audit_summary
        summary["execution"] = {
            "status": status or "unknown",
            "step_count": len(execution.get("steps") or []),
            "output_count": len(outputs) if isinstance(outputs, list) else 0,
            "outputs": _public_workflow_output_summaries(outputs) if isinstance(outputs, list) else [],
        }

    materialization = _read_json(task_root / "workflow_output_materialization.json")
    if isinstance(materialization, dict):
        summary["evidence_materialization"] = materialization
        semantic_import = materialization.get("semantic_output_import")
        if isinstance(semantic_import, dict):
            summary["semantic_output_import"] = semantic_import
    semantic_import = _read_json(task_root / "semantic_output_import.json")
    if isinstance(semantic_import, dict):
        result = semantic_import.get("result")
        summary["semantic_output_import"] = (
            result if isinstance(result, dict) else semantic_import
        )

    quality = _read_json(task_root / "test_activity_quality_audit.json")
    if isinstance(quality, dict):
        public_quality = {
            key: quality.get(key)
            for key in (
                "status",
                "deliverable",
                "score",
                "issue_count",
                "lint_warning_count",
                "fact_verification",
                "quality_axes",
                # The cockpit renders these concise, user-actionable entries when
                # quality blocks delivery.  Keep detailed claim payloads private,
                # but never hide the reason a user cannot proceed.
                "issues",
                "recommendations",
            )
            if key in quality
        }
        profile_execution = _public_profile_execution_summary(
            quality.get("profile_execution_evidence")
        )
        if profile_execution:
            public_quality["profile_execution"] = profile_execution
        summary["test_activity_quality"] = public_quality

    stage_progress = _public_test_activity_stage_progress(task_root)
    if stage_progress:
        summary["test_activity_stage_progress"] = stage_progress

    acceptance = _read_json(task_root / "task_acceptance_audit.json")
    if task_status not in {"cancelled", "interrupted"} and isinstance(acceptance, dict):
        summary["acceptance_audit"] = {
            "status": str(acceptance.get("status") or "unknown"),
            "summary": acceptance.get("summary") or {},
        }

    manifest = _read_json(task_root / "task_artifact_manifest.json")
    if isinstance(manifest, dict):
        summary["artifact_summary"] = {
            "artifact_count": int(manifest.get("artifact_count") or 0),
            "manifest_path": "task_artifact_manifest.json",
        }
    return summary


def _public_profile_execution_summary(value: Any) -> dict[str, Any]:
    """Expose bounded deep-work proof without leaking prompts or source routes."""
    if not isinstance(value, dict):
        return {}
    branch_requirements = value.get("branch_citation_requirements")
    branch_count = len(branch_requirements) if isinstance(branch_requirements, dict) else 0
    return {
        "profile_id": str(value.get("profile_id") or ""),
        "status": str(value.get("status") or "unknown"),
        "provider_call_count": _public_nonnegative_int(value.get("provider_call_count")),
        "output_tokens": _public_nonnegative_int(value.get("output_tokens")),
        "provider_wait_ms": _public_nonnegative_float(value.get("provider_wait_ms")),
        "reused_stage_count": _public_nonnegative_int(value.get("reused_stage_count")),
        "branch_count": branch_count,
        "missing_branch_provider_work": [
            str(item) for item in value.get("missing_branch_provider_work") or []
            if str(item)
        ],
        "under_evidenced_branches": [
            str(item) for item in value.get("under_evidenced_branches") or []
            if str(item)
        ],
    }


def _public_nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _public_nonnegative_float(value: Any) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def _public_test_activity_stage_progress(task_root: Path) -> dict[str, Any]:
    payload = _read_json(task_root / "test_activity_stage_progress.json")
    if not isinstance(payload, dict):
        return {}
    stages = [
        {
            key: item.get(key)
            for key in (
                "stage_id",
                "name",
                "status",
                "expected_artifacts",
                "present_artifacts",
                "deterministic_gate",
                "fallback",
            )
        }
        for item in payload.get("stages") or []
        if isinstance(item, dict)
    ]
    return {
        "schema_version": str(payload.get("schema_version") or ""),
        "profile_id": str(payload.get("profile_id") or ""),
        "stages": stages,
    }


def _public_workflow_output_summaries(outputs: list[Any]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    public_keys = {
        "artifact",
        "from",
        "id",
        "path",
        "sha256",
        "size_bytes",
        "status",
        "type",
    }
    for item in outputs:
        if not isinstance(item, dict):
            continue
        summaries.append({key: item[key] for key in public_keys if key in item})
    return summaries


def _build_task_run_ui_summary(task_run: Any, task_root: Path) -> dict[str, Any]:
    workflow = task_run.workflow_snapshot if isinstance(task_run.workflow_snapshot, dict) else {}
    contract = (
        task_run.task_bundle.get("workflow_contract")
        if isinstance(task_run.task_bundle, dict)
        and isinstance(task_run.task_bundle.get("workflow_contract"), dict)
        else {}
    )
    task_payload = _read_json(task_root / "task_run.json")
    task_status = (
        str((task_payload or {}).get("status") or "").strip().lower()
        if isinstance(task_payload, dict)
        else ""
    )
    execution = _read_json(task_root / "workflow_execution.json")
    if not isinstance(execution, dict):
        execution = {"status": task_status or "prepared"}
    elif task_status in {"cancelled", "canceled"}:
        execution = {**execution, "status": "cancelled"}
    step_results = {
        str(item.get("step_id") or ""): item
        for item in execution.get("step_results") or []
        if isinstance(item, dict) and str(item.get("step_id") or "")
    }
    outputs = [
        item for item in execution.get("outputs") or []
        if isinstance(item, dict)
    ]
    output_by_key = {
        (
            str(item.get("from") or ""),
            str(item.get("id") or ""),
            str(item.get("artifact") or ""),
        ): item
        for item in outputs
    }
    steps = [
        step for step in workflow.get("steps") or []
        if isinstance(step, dict)
    ]
    compiled_plan = (
        task_run.task_bundle.get("compiled_plan")
        if isinstance(task_run.task_bundle, dict)
        and isinstance(task_run.task_bundle.get("compiled_plan"), dict)
        else {}
    )
    plan_nodes = {
        str(item.get("node_id") or ""): item
        for item in compiled_plan.get("nodes") or []
        if isinstance(item, dict) and str(item.get("node_id") or "")
    }
    event_context = _task_run_ui_event_context(task_root)
    step_labels = {
        str(step.get("id") or ""): str(step.get("name") or step.get("label") or step.get("id") or "")
        for step in steps
    }
    next_by_step: dict[str, list[str]] = {}
    for node_id, plan_node in plan_nodes.items():
        for dependency in plan_node.get("depends_on") or []:
            dependency_id = str(dependency or "")
            if dependency_id:
                next_by_step.setdefault(dependency_id, []).append(node_id)
    nodes = [
        _task_run_ui_node_summary(
            task_run=task_run,
            task_root=task_root,
            step=step,
            plan_node=plan_nodes.get(str(step.get("id") or ""), {}),
            next_node_ids=next_by_step.get(str(step.get("id") or ""), []),
            step_labels=step_labels,
            event_context=event_context.get(str(step.get("id") or ""), {}),
            workflow_contract=contract,
            step_result=step_results.get(str(step.get("id") or "")),
            output_by_key=output_by_key,
        )
        for step in steps
    ]
    active_step_id = _task_run_ui_active_step_id(task_root)
    if active_step_id:
        nodes = [
            {
                **node,
                "status": "running",
                "status_label": _task_run_ui_status_label("running"),
            }
            if node.get("id") == active_step_id
            and node.get("status_label") == _task_run_ui_status_label("prepared")
            else node
            for node in nodes
        ]
    if task_status in {"cancelled", "canceled"}:
        nodes = [
            {
                **node,
                "status": "cancelled",
                "status_label": _task_run_ui_status_label("cancelled"),
            }
            if node.get("status_label") in {"运行中", "运行失败", "等待运行"}
            else node
            for node in nodes
        ]
    # A provider may retain a partial intermediate response while the staged
    # runtime materializes a complete, validated delivery deterministically.
    # Once final quality accepts that delivery, expose the recovery explicitly
    # to users instead of showing a completed run with a contradictory node.
    recovered_partial_steps = {
        str(item).strip()
        for item in execution.get("recovered_partial_steps") or []
        if str(item).strip()
    }
    if (
        execution.get("quality_repaired_to_deliverable") is True
        and str(execution.get("status") or "") in {"completed", "ok", "ready", "success"}
        and recovered_partial_steps
    ):
        nodes = [
            {
                **node,
                "status": "completed",
                "status_label": "已完成（使用保留结果）",
                "recovered_from_partial": True,
            }
            if node.get("id") in recovered_partial_steps
            and node.get("status") == "partial"
            else node
            for node in nodes
        ]
    execution_metadata = _task_run_ui_workflow_execution_metadata(workflow)
    status = _task_run_ui_status(execution=execution, nodes=nodes)
    live_readiness_failures = _task_run_ui_live_readiness_failures(task_root)
    live_readiness_actions = _task_run_ui_live_readiness_actions(task_root)
    live_readiness_checks = [
        item
        for item in (_read_json(task_root / "provider_live_readiness.json") or {}).get("checks") or []
        if isinstance(item, dict)
    ]
    has_failed_agent_runtime = any(
        str(item.get("provider") or "") != "independent-quality-audit"
        and item.get("success") is not True
        for item in live_readiness_checks
    )
    has_failed_independent_quality_audit = any(
        str(item.get("provider") or "") == "independent-quality-audit"
        and item.get("success") is not True
        for item in live_readiness_checks
    )
    # A preflight failure intentionally has no step result.  It must still
    # become the primary run state; otherwise the cockpit misleadingly keeps
    # the run in its queued layout and hides the actionable failure panel.
    if live_readiness_failures and status["status"] not in {"cancelled", "completed"}:
        status = {"status": "failed", "label": "运行失败"}
    failed_node = (
        None
        if status["status"] == "cancelled"
        else next((node for node in nodes if node.get("status_label") == "运行失败"), None)
    )
    running_node = next((node for node in nodes if node.get("status_label") == "运行中"), None)
    waiting_node = next((node for node in nodes if node.get("status_label") == "等待运行"), None)
    current_node = failed_node or running_node or waiting_node or (nodes[-1] if nodes else {})
    failure_reasons = _task_run_ui_failure_reasons(failed_node) if failed_node else []
    if status["status"] == "failed" and not failed_node:
        failure_reasons = _dedupe_strings([
            *live_readiness_failures,
            *failure_reasons,
        ])
    failed_step_result = step_results.get(str((failed_node or {}).get("id") or ""), {})
    failure_recovery = (
        failed_step_result.get("failure_recovery")
        if isinstance(failed_step_result, dict)
        and isinstance(failed_step_result.get("failure_recovery"), dict)
        else {}
    )
    recovery_actions = [
        str(item)
        for item in failure_recovery.get("recommended_actions") or []
        if str(item).strip()
    ]
    preserved_nodes = [node for node in nodes if node.get("status_label") == "已完成"]
    rerun_nodes = [
        node for node in nodes
        if node.get("status_label") in {"运行失败", "等待运行", "运行中断"}
    ]
    failure_class = _task_run_ui_failure_class(failure_reasons)
    stage_progress = _public_test_activity_stage_progress(task_root)
    return {
        "status": status["status"],
        "status_label": status["label"],
        "execution_subject": execution_metadata["execution_subject"],
        "execution_label": execution_metadata["execution_label"],
        "user_message": execution_metadata["user_message"],
        "workflow": {
            "id": str(workflow.get("id") or task_run.workflow_id),
            "name": str(workflow.get("name") or task_run.workflow_id),
            "version": workflow.get("version", 1),
            "execution_subject": execution_metadata["execution_subject"],
            "execution_label": execution_metadata["execution_label"],
            "user_message": execution_metadata["user_message"],
        },
        "current_node": current_node,
        "nodes": nodes,
        "failure": {
            "failed_node_id": str((failed_node or {}).get("id") or ""),
            "reasons": failure_reasons,
            "can_retry": bool(failed_node),
            "preflight_blocked": bool(live_readiness_failures),
            "preflight_kind": (
                "agent_runtime"
                if has_failed_agent_runtime
                else "independent_quality_audit"
                if has_failed_independent_quality_audit
                else ""
            ),
            "user_goal_stage": str((failed_node or {}).get("label") or ""),
            "preserved_node_ids": [str(node.get("id") or "") for node in preserved_nodes],
            "preserved_node_labels": [str(node.get("label") or "") for node in preserved_nodes],
            "rerun_node_ids": [str(node.get("id") or "") for node in rerun_nodes],
            "rerun_node_labels": [str(node.get("label") or "") for node in rerun_nodes],
            "reuse_node_ids": [str(node.get("id") or "") for node in preserved_nodes],
            "reuse_node_labels": [str(node.get("label") or "") for node in preserved_nodes],
            "failure_class": failure_class,
            "failure_kind": str(failure_recovery.get("failure_kind") or ""),
            "recommended_action": (
                live_readiness_actions[0]
                if live_readiness_actions
                else live_readiness_failures[0]
                if live_readiness_failures
                else
                recovery_actions[0]
                if recovery_actions
                else "检查任务输入或工作流输出契约，保存后再创建新 Attempt。"
                if failure_class == "configuration"
                else "保留已完成上游结果，从失败节点创建新 Attempt 重试。"
            ),
            "recommended_actions": _dedupe_strings([
                *live_readiness_failures,
                *recovery_actions,
            ]),
            "actions": (
                ["从失败节点重试", "查看内部诊断", "编辑工作流输出契约"]
                if failed_node
                else ["检查执行器设置", "查看内部诊断"]
                if live_readiness_failures
                else []
            ),
        },
        "deliverables": _task_run_ui_deliverables(
            workflow_contract=contract,
            outputs=outputs,
        ),
        "test_activity_stage_progress": stage_progress,
        "debug_default_collapsed": True,
        "debug_sections": ["raw JSON", "prompt", "diagnostic", "replay plan"],
    }


def _task_run_ui_active_step_id(task_root: Path) -> str:
    events_path = task_root / "task_run_events.jsonl"
    if not events_path.is_file():
        return ""
    active_step_id = ""
    try:
        lines = events_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in lines:
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        step_id = str(payload.get("step_id") or "")
        if event_type == "step_started" and step_id:
            active_step_id = step_id
        elif event_type in {"step_completed", "step_failed", "step_cancelled"} and step_id == active_step_id:
            active_step_id = ""
    return active_step_id


def _task_run_ui_node_summary(
    *,
    task_run: Any,
    task_root: Path,
    step: dict[str, Any],
    plan_node: dict[str, Any],
    next_node_ids: list[str],
    step_labels: dict[str, str],
    event_context: dict[str, Any],
    workflow_contract: dict[str, Any],
    step_result: dict[str, Any] | None,
    output_by_key: dict[tuple[str, str, str], dict[str, Any]],
) -> dict[str, Any]:
    step_id = str(step.get("id") or "")
    execution_contract = _agent_step_execution_contract(task_root=task_root, step_id=step_id)
    status_value = str((step_result or {}).get("status") or "prepared")
    node_outputs = _task_run_ui_node_outputs(
        workflow_contract=workflow_contract,
        step_id=step_id,
        output_by_key=output_by_key,
    )
    execution_metadata = _task_run_ui_step_execution_metadata(
        task_root=task_root,
        step=step,
        step_result=step_result or {},
    )
    dependencies = [
        str(item) for item in plan_node.get("depends_on") or step.get("depends_on") or []
        if str(item)
    ]
    goal = str(
        step.get("goal")
        or (plan_node.get("config") or {}).get("goal")
        or step.get("description")
        or f"完成 {step.get('name') or step_id}"
    ).strip()
    input_rows = _task_run_ui_step_inputs(
        workflow_contract=workflow_contract,
        execution_contract=execution_contract,
    )
    received_inputs = [
        {
            **input_row,
            "value_summary": _task_run_ui_input_value_summary(
                task_run.input_snapshot.get(input_row["id"])
            ),
        }
        for input_row in input_rows
    ]
    return {
        "id": step_id,
        "label": str(step.get("name") or step_id),
        "type": str(step.get("type") or ""),
        "status": status_value,
        "status_label": _task_run_ui_status_label(status_value),
        "provider": execution_metadata["provider"] or str(step.get("provider") or ""),
        "executor": execution_metadata["executor"],
        "executor_label": execution_metadata["executor_label"],
        "method": execution_metadata["method"],
        "user_message": execution_metadata["user_message"],
        "goal": goal,
        "why": str(step.get("why") or step.get("description") or (
            "消费上游节点结果并完成当前工作流阶段。"
            if dependencies
            else "这是工作流的起始阶段，用于建立后续节点所需的输入和证据。"
        )),
        "depends_on": dependencies,
        "dependency_labels": [step_labels.get(item, item) for item in dependencies],
        "next_node_ids": next_node_ids,
        "next_node_labels": [step_labels.get(item, item) for item in next_node_ids],
        "started_at": str(event_context.get("started_at") or ""),
        "completed_at": str(event_context.get("completed_at") or ""),
        "duration_ms": int(event_context.get("duration_ms") or 0),
        "active_tools": [str(item) for item in event_context.get("tools") or []],
        "inputs": input_rows,
        "received_inputs": received_inputs,
        "mcp_profiles": _task_run_ui_step_mcp_profiles(
            step=step,
            execution_contract=execution_contract,
        ),
        "mcp_availability": _task_run_ui_step_mcp_availability(
            execution_contract=execution_contract,
        ),
        "mcp_inputs": _task_run_ui_step_mcp_inputs(
            workflow_contract=workflow_contract,
            execution_contract=execution_contract,
        ),
        "skills": _task_run_ui_step_skills(step=step, execution_contract=execution_contract),
        "outputs": node_outputs,
        "failure_reasons": _task_run_ui_step_failure_reasons(
            step_result=step_result or {},
            outputs=node_outputs,
        ),
        "review_reasons": _task_run_ui_step_review_reasons(step_result=step_result or {}),
    }


def _task_run_ui_event_context(task_root: Path) -> dict[str, dict[str, Any]]:
    path = task_root / "task_run_events.jsonl"
    if not path.is_file():
        return {}
    result: dict[str, dict[str, Any]] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    for line in lines:
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        step_id = str(payload.get("step_id") or payload.get("node_id") or "")
        if not step_id:
            continue
        context = result.setdefault(step_id, {"tools": []})
        event_type = str(event.get("event_type") or "")
        timestamp = str(event.get("created_at") or event.get("timestamp") or "")
        if event_type in {"step_started", "node_started"} and timestamp:
            context.setdefault("started_at", timestamp)
        if event_type in {"step_completed", "step_failed", "step_cancelled", "node_completed", "node_failed"} and timestamp:
            context["completed_at"] = timestamp
        kind = str(event.get("event_kind") or payload.get("kind") or "")
        if kind in {"tool_use", "tool_result"}:
            tool = str(payload.get("tool") or payload.get("name") or "").strip()
            if tool and tool not in context["tools"]:
                context["tools"].append(tool)
    for context in result.values():
        started = _task_run_ui_parse_time(str(context.get("started_at") or ""))
        completed = _task_run_ui_parse_time(str(context.get("completed_at") or ""))
        if started and completed and completed >= started:
            context["duration_ms"] = int((completed - started).total_seconds() * 1000)
    return result


def _task_run_ui_parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _task_run_ui_input_value_summary(value: Any) -> str:
    if value in (None, "", [], {}):
        return "未提供"
    if isinstance(value, dict):
        kind = str(value.get("kind") or "")
        if kind == "file":
            candidate = str(value.get("filename") or value.get("name") or value.get("copied_path") or value.get("original_path") or "")
            return Path(candidate).name if candidate else "已提供文件"
        if kind == "file_set":
            files = [item for item in value.get("files") or [] if isinstance(item, dict)]
            names = [
                Path(str(item.get("filename") or item.get("name") or item.get("copied_path") or item.get("original_path") or "")).name
                for item in files
            ]
            return "、".join(item for item in names if item) or f"{len(files)} 个文件"
        return "已提供结构化输入"
    if isinstance(value, list):
        return f"{len(value)} 项：" + "、".join(_task_run_ui_input_value_summary(item) for item in value[:3])
    text = str(value).strip()
    if not text:
        return "未提供"
    if Path(text).is_absolute():
        return Path(text).name or "已选择工作空间"
    return text if len(text) <= 160 else f"{text[:157]}..."


def _task_run_ui_failure_class(reasons: list[str]) -> str:
    text = " ".join(reasons).lower()
    configuration_markers = (
        "缺少交付文件",
        "输出契约",
        "missing artifact",
        "schema",
        "输入",
        "配置",
    )
    return "configuration" if any(marker in text for marker in configuration_markers) else "runtime"


def _task_run_ui_workflow_execution_metadata(workflow: dict[str, Any]) -> dict[str, str]:
    subject = str(workflow.get("execution_subject") or "").strip()
    label = str(workflow.get("execution_label") or "").strip()
    user_message = str(workflow.get("user_message") or "").strip()
    steps = [step for step in workflow.get("steps") or [] if isinstance(step, dict)]
    step_types = {str(step.get("type") or "") for step in steps}
    has_agent_or_llm = bool(step_types & {"agent_task", "builtin_llm", "llm_task"})
    if not subject and steps and not has_agent_or_llm:
        subject = "local_static"
    if subject == "local_static" and not label:
        label = "本地静态扫描（无 AI）"
    if subject == "local_static" and not user_message:
        user_message = "该工作流只执行本地静态源码扫描，未调用 AI 或外部 Agent。"
    return {
        "execution_subject": subject,
        "execution_label": label,
        "user_message": user_message,
    }


def _task_run_ui_step_execution_metadata(
    *,
    task_root: Path,
    step: dict[str, Any],
    step_result: dict[str, Any],
) -> dict[str, str]:
    step_type = str(step.get("type") or "")
    step_id = str(step.get("id") or "")
    agent_run = (
        _read_json(task_root / "agent_runs" / _safe_segment(step_id, "step_id") / "agent_run.json")
        if step_type == "agent_task" and step_id
        else {}
    )
    provider = str(
        step_result.get("provider")
        or (agent_run or {}).get("provider")
        or step.get("provider")
        or ""
    )
    method = ""
    executor = ""
    executor_label = ""
    user_message = str(step_result.get("user_message") or "")
    if step_type == "local_scope_discover":
        executor = "local_static"
        executor_label = "本地静态扫描（无 AI）"
        provider = provider or "local-search"
        method = "filesystem_source_scan"
        source_scope = _task_run_ui_step_source_scope(step_result)
        discovery = source_scope.get("discovery") if isinstance(source_scope, dict) else {}
        if isinstance(discovery, dict):
            provider = str(discovery.get("provider") or provider)
            method = str(discovery.get("method") or method)
            user_message = user_message or str(discovery.get("user_message") or "")
        user_message = user_message or "本步骤只执行本地静态源码扫描，未调用 AI 或外部 Agent。"
    elif step_type in {"local_source_flow_sfmea_blackbox", "local_blackbox_cases"}:
        executor = "local_static"
        executor_label = "本地静态分析与产物渲染"
        provider = provider or "local-search"
        method = step_type
    elif step_type in {"evidence_validate", "semantic_retrieve", "report_render"}:
        executor = "builtin"
        executor_label = "CodeTalk 内置步骤"
        method = step_type
    elif step_type in {"builtin_llm", "llm_task"}:
        executor = "builtin_llm"
        executor_label = "内置大模型"
        method = step_type
    elif step_type == "agent_task":
        if provider == BUILTIN_LLM_PROVIDER_ID:
            executor = "builtin_llm"
            executor_label = "内置大模型"
            method = (
                "staged_builtin_llm"
                if str(step.get("execution_mode") or "") == "staged"
                else "builtin_llm"
            )
            user_message = user_message or "内置模型按工作流产物契约生成并校验交付件。"
        else:
            executor = "external_agent"
            executor_label = f"外部 Agent：{provider}" if provider else "外部 Agent"
            method = "agent_cli"
    return {
        "executor": executor,
        "executor_label": executor_label,
        "provider": provider,
        "method": method,
        "user_message": user_message,
    }


def _task_run_ui_step_source_scope(step_result: dict[str, Any]) -> dict[str, Any]:
    artifact_dir_value = str(step_result.get("artifact_dir") or "").strip()
    if not artifact_dir_value:
        return {}
    artifact_dir = Path(artifact_dir_value)
    artifact = str(step_result.get("artifact") or "source_scope.json")
    payload = _read_json(artifact_dir / artifact)
    return payload if isinstance(payload, dict) else {}


def _agent_step_execution_contract(*, task_root: Path, step_id: str) -> dict[str, Any]:
    payload = _read_json(task_root / "agent_runs" / _safe_segment(step_id, "step_id") / "task_bundle.json")
    if isinstance(payload, dict) and isinstance(payload.get("execution_contract"), dict):
        return payload["execution_contract"]
    return {}


def _task_run_ui_step_inputs(
    *,
    workflow_contract: dict[str, Any],
    execution_contract: dict[str, Any],
) -> list[dict[str, str]]:
    input_defs = {
        str(item.get("id") or ""): item
        for item in workflow_contract.get("inputs") or []
        if isinstance(item, dict) and str(item.get("id") or "")
    }
    ordered_ids: list[str] = []
    for item in execution_contract.get("user_inputs") or []:
        if isinstance(item, dict):
            ordered_ids.append(str(item.get("input_id") or ""))
    input_materials = execution_contract.get("input_materials")
    if isinstance(input_materials, dict):
        for material in input_materials.get("materials") or []:
            if isinstance(material, dict):
                ordered_ids.append(str(material.get("input_id") or ""))
    mcp = execution_contract.get("mcp")
    if isinstance(mcp, dict):
        for request in mcp.get("requests") or []:
            if isinstance(request, dict):
                ordered_ids.append(str(request.get("input_id") or ""))
    workflow_order = [
        str(item.get("id") or "")
        for item in workflow_contract.get("inputs") or []
        if isinstance(item, dict) and str(item.get("id") or "")
    ]
    if ordered_ids:
        present_ids = {input_id for input_id in ordered_ids if input_id}
        ordered_ids = [
            input_id for input_id in workflow_order if input_id in present_ids
        ] + [
            input_id for input_id in ordered_ids if input_id and input_id not in workflow_order
        ]
    else:
        ordered_ids = workflow_order
    results: list[dict[str, str]] = []
    for input_id in ordered_ids:
        if not input_id or any(item["id"] == input_id for item in results):
            continue
        definition = input_defs.get(input_id, {})
        results.append({
            "id": input_id,
            "role": str(definition.get("role") or input_id),
            "type": str(definition.get("type") or ""),
        })
    return results


def _task_run_ui_step_mcp_profiles(
    *,
    step: dict[str, Any],
    execution_contract: dict[str, Any],
) -> list[str]:
    profiles: list[str] = []
    mcp = execution_contract.get("mcp")
    if isinstance(mcp, dict) and str(mcp.get("profile") or ""):
        profiles.append(str(mcp.get("profile") or ""))
    if str(step.get("mcp_profile") or ""):
        profiles.append(str(step.get("mcp_profile") or ""))
    return _dedupe_strings(profiles)


def _task_run_ui_step_mcp_availability(
    *,
    execution_contract: dict[str, Any],
) -> dict[str, str]:
    mcp = execution_contract.get("mcp")
    availability = mcp.get("availability") if isinstance(mcp, dict) else {}
    if not isinstance(availability, dict):
        return {}
    return {
        "status": str(availability.get("status") or ""),
        "user_message": str(availability.get("user_message") or ""),
        "action": str(availability.get("action") or ""),
    }


def _task_run_ui_step_mcp_inputs(
    *,
    workflow_contract: dict[str, Any],
    execution_contract: dict[str, Any],
) -> list[dict[str, str]]:
    input_defs = {
        str(item.get("id") or ""): item
        for item in workflow_contract.get("inputs") or []
        if isinstance(item, dict) and str(item.get("id") or "")
    }
    mcp = execution_contract.get("mcp")
    requests = mcp.get("requests") if isinstance(mcp, dict) else []
    results: list[dict[str, str]] = []
    for request in requests or []:
        if not isinstance(request, dict):
            continue
        input_id = str(request.get("input_id") or "")
        definition = input_defs.get(input_id, {})
        owner = str(request.get("credential_owner") or "")
        results.append({
            "id": input_id,
            "role": str(definition.get("role") or input_id),
            "type": str(definition.get("type") or request.get("input_type") or ""),
            "credential_owner_label": (
                "由 Agent 使用自身 MCP 凭据读取"
                if owner == "agent_cli"
                else "由 CodeTalk 本地预取"
            ),
        })
    return results


def _task_run_ui_step_skills(
    *,
    step: dict[str, Any],
    execution_contract: dict[str, Any],
) -> list[dict[str, str]]:
    skill_ids: list[str] = []
    skills = execution_contract.get("skills")
    if isinstance(skills, dict):
        skill_ids.extend(str(item) for item in skills.get("ids") or [])
    skill_ids.extend(str(item) for item in step.get("skills") or [])
    return [{"id": item, "label": item} for item in _dedupe_strings(skill_ids)]


def _task_run_ui_node_outputs(
    *,
    workflow_contract: dict[str, Any],
    step_id: str,
    output_by_key: dict[tuple[str, str, str], dict[str, Any]],
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for output in workflow_contract.get("outputs") or []:
        if not isinstance(output, dict) or str(output.get("from") or "") != step_id:
            continue
        output_id = str(output.get("id") or "")
        artifact = str(output.get("artifact") or output.get("path") or "")
        resolved = output_by_key.get((step_id, output_id, artifact), {})
        if not resolved and not artifact:
            # Some output declarations intentionally let the runner infer the
            # report filename. The execution record is authoritative after
            # that inference, so bind the unique source-step/output-id result
            # instead of presenting a generated report as still waiting.
            candidates = [
                candidate
                for (source_step, candidate_id, _), candidate in output_by_key.items()
                if source_step == step_id and candidate_id == output_id
            ]
            if len(candidates) == 1:
                resolved = candidates[0]
                artifact = str(resolved.get("artifact") or "")
        status = str(resolved.get("status") or "waiting")
        item = {
            "id": output_id,
            "artifact": artifact,
            "type": str(output.get("type") or ""),
            "status_label": _task_run_ui_output_status_label(status),
        }
        if str(resolved.get("path") or ""):
            item["path"] = str(resolved.get("path") or "")
        results.append(item)
    return results


def _task_run_ui_deliverables(
    *,
    workflow_contract: dict[str, Any],
    outputs: list[dict[str, Any]],
) -> list[dict[str, str]]:
    output_defs = {
        (str(item.get("from") or ""), str(item.get("id") or ""), str(item.get("artifact") or item.get("path") or "")): item
        for item in workflow_contract.get("outputs") or []
        if isinstance(item, dict)
    }
    deliverables: list[dict[str, str]] = []
    for output in outputs:
        status = str(output.get("status") or "")
        if status not in {"ok", "completed", "ready", "success"}:
            continue
        key = (
            str(output.get("from") or ""),
            str(output.get("id") or ""),
            str(output.get("artifact") or ""),
        )
        definition = output_defs.get(key, {})
        deliverables.append({
            "id": str(output.get("id") or ""),
            "label": str(definition.get("name") or definition.get("id") or output.get("id") or ""),
            "from": str(output.get("from") or ""),
            "artifact": str(output.get("artifact") or ""),
            "path": str(output.get("path") or ""),
            "type": str(definition.get("type") or output.get("type") or ""),
            "status_label": "已生成",
        })
    return deliverables


def _task_run_ui_status(*, execution: dict[str, Any], nodes: list[dict[str, Any]]) -> dict[str, str]:
    status = str(execution.get("status") or "")
    if status in {"cancelled", "canceled"}:
        return {"status": "cancelled", "label": "已取消"}
    if any(node.get("status_label") == "运行失败" for node in nodes):
        return {"status": "failed", "label": "运行失败"}
    if status in {"completed", "ok", "ready", "success"}:
        return {"status": "completed", "label": "运行完成"}
    if status in {"completed_empty"}:
        return {"status": "completed_empty", "label": "完成但信息不足"}
    if status == "partial":
        return {"status": "partial", "label": "部分完成"}
    if status == "quality_blocked":
        return {"status": "quality_blocked", "label": "执行完成，质量待修复"}
    if status in {"needs_review", "needs_rework"}:
        return {"status": status, "label": "需要复核"}
    if status in {"interrupted"}:
        return {"status": "failed", "label": "运行中断"}
    if status in {"invalid", "error", "failed", "failure"}:
        return {"status": "failed", "label": "运行失败"}
    if status in {"running", "queued"}:
        return {"status": "running", "label": "运行中"}
    return {"status": "prepared", "label": "等待运行"}


def _task_run_ui_status_label(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"completed", "ok", "ready", "success"}:
        return "已完成"
    if normalized in {"completed_empty"}:
        return "完成但信息不足"
    if normalized == "partial":
        return "部分完成"
    if normalized == "quality_blocked":
        return "执行完成，质量待修复"
    if normalized in {"blocked", "upstream_blocked"}:
        return "因上游门禁阻断"
    if normalized in {"needs_review"}:
        return "需要复核"
    if normalized in {"running", "queued"}:
        return "运行中"
    if normalized in {"cancelled", "canceled"}:
        return "已取消"
    if normalized in {"invalid", "error", "failed", "failure", "missing"}:
        return "运行失败"
    if normalized in {"prepared", "waiting", "not_started", "idle", ""}:
        return "等待运行"
    if normalized in {"cancelled", "canceled"}:
        return "已取消"
    if normalized in {"interrupted"}:
        return "运行中断"
    if normalized in {"skipped"}:
        return "已跳过"
    return "状态待确认"


def _task_run_ui_output_status_label(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"ok", "completed", "ready", "success"}:
        return "已生成"
    if normalized in {"completed_empty"}:
        return "已生成但信息不足"
    if normalized in {"needs_review"}:
        return "需要复核"
    if normalized in {"missing", "not_found"}:
        return "缺少交付文件"
    if normalized in {"invalid", "error", "failed", "failure"}:
        return "生成失败"
    return "等待生成"


def _task_run_ui_step_review_reasons(*, step_result: dict[str, Any]) -> list[str]:
    status = str(step_result.get("status") or "").strip().lower()
    reasons: list[str] = []
    if status == "completed_empty":
        if str(step_result.get("user_message") or ""):
            reasons.append(str(step_result.get("user_message") or ""))
        else:
            reasons.append("该步骤完成了产物写入，但没有产出足够源码证据或有效信息。")
    elif status == "needs_review":
        if str(step_result.get("user_message") or ""):
            reasons.append(str(step_result.get("user_message") or ""))
        else:
            reasons.append("该步骤已生成产物，但结果需要人工复核后再使用。")
    validation = step_result.get("validation")
    if isinstance(validation, dict) and str(validation.get("reason") or ""):
        reasons.append(_task_run_ui_reason_label(str(validation.get("reason") or "")))
    return _dedupe_strings(reasons)


def _task_run_ui_step_failure_reasons(
    *,
    step_result: dict[str, Any],
    outputs: list[dict[str, str]],
) -> list[str]:
    reasons: list[str] = []
    recovery = step_result.get("failure_recovery")
    if isinstance(recovery, dict) and str(recovery.get("user_message") or "").strip():
        reasons.append(str(recovery["user_message"]).strip())
    validation = step_result.get("validation")
    if isinstance(validation, dict):
        missing = [
            str(item) for item in validation.get("missing_artifacts") or []
            if str(item)
        ]
        if missing:
            reasons.append(
                "Agent 没有生成工作流要求的交付文件："
                f"{'、'.join(missing)}。请从失败节点重试，或检查输出契约。"
            )
        if str(validation.get("reason") or ""):
            reasons.append(_task_run_ui_reason_label(str(validation.get("reason") or "")))
    execution = step_result.get("execution")
    if isinstance(execution, dict) and str(execution.get("error") or ""):
        reasons.append(_task_run_ui_reason_label(str(execution.get("error") or "")))
    if isinstance(execution, dict):
        exit_code = execution.get("exit_code")
        if isinstance(exit_code, int) and exit_code != 0:
            reasons.append(_task_run_ui_reason_label(f"exit code {exit_code}"))
    if str(step_result.get("error") or ""):
        reasons.append(_task_run_ui_reason_label(str(step_result.get("error") or "")))
    for output in outputs:
        if output.get("status_label") == "缺少交付文件" and output.get("artifact"):
            reasons.append(
                "Agent 没有生成工作流要求的交付文件："
                f"{output['artifact']}。请从失败节点重试，或检查输出契约。"
            )
    return _dedupe_strings(reasons)


def _task_run_ui_failure_reasons(failed_node: dict[str, Any] | None) -> list[str]:
    if not failed_node:
        return []
    return [
        str(item) for item in failed_node.get("failure_reasons") or []
        if str(item)
    ] or ["该节点运行失败。请查看内部诊断或从失败节点重试。"]


def _task_run_ui_live_readiness_failures(task_root: Path) -> list[str]:
    payload = _read_json(task_root / "provider_live_readiness.json")
    if not isinstance(payload, dict):
        return []
    failures = []
    for item in payload.get("checks") or []:
        if not isinstance(item, dict) or item.get("success") is True:
            continue
        message = str(item.get("message") or "").strip()
        if message:
            failures.append(message)
    return _dedupe_strings(failures)


def _task_run_ui_live_readiness_actions(task_root: Path) -> list[str]:
    payload = _read_json(task_root / "provider_live_readiness.json")
    if not isinstance(payload, dict):
        return []
    return _dedupe_strings(
        str(item.get("recommended_action") or "").strip()
        for item in payload.get("checks") or []
        if isinstance(item, dict) and item.get("success") is not True
    )


def _task_run_ui_reason_label(reason: str) -> str:
    normalized = str(reason or "").strip()
    lower = normalized.lower()
    if not normalized:
        return ""
    if (
        "missing_artifact" in lower
        or "missing artifact" in lower
        or "artifact file was not produced" in lower
    ):
        return "Agent 没有生成工作流要求的交付文件。请从失败节点重试，或检查输出契约。"
    if "schema" in lower:
        return "结构化产物未通过 Schema 校验。请查看对应 JSON 产物和工作流输出模板。"
    if "command not found" in lower:
        return "找不到执行器命令。请在设置中检查 Agent 命令、PATH 或填写完整可执行文件路径。"
    if "timed out" in lower or "timeout" in lower or "超时" in lower:
        seconds = re.search(r"after\s+(\d+)s", normalized, flags=re.I)
        if "idle" in lower:
            return (
                f"Agent 长时间没有新的输出{f'（{seconds.group(1)} 秒）' if seconds else ''}，"
                "系统判断可能卡住并停止了运行。请查看内部诊断、缩小分析范围，或从失败节点重试。"
            )
        return (
            f"Agent 运行超时{f'（{seconds.group(1)} 秒）' if seconds else ''}。"
            "请缩小分析范围、延长运行超时，或从失败节点重试。"
        )
    if "outofmemoryerror" in lower or "heap" in lower:
        return "内存不足，当前分析对象超过可用堆内存。建议缩小模块范围或调整执行器资源。"
    if "exit code" in lower:
        code = re.search(r"exit code\D*(\d+)", normalized, flags=re.I)
        return f"执行器异常退出{f'，退出码 {code.group(1)}' if code else ''}。请查看内部诊断确认失败节点。"
    exact = {
        "missing_agent_run": "没有找到该节点对应的 Agent 运行记录。请重新准备运行。",
        "missing_run_id": "Agent 运行记录缺少 run id。请重新准备运行。",
        "artifact_json_unreadable": "产物 JSON 无法读取。请检查生成文件是否完整。",
    }
    return exact.get(normalized, normalized)


def _dedupe_strings(items: list[str]) -> list[str]:
    results: list[str] = []
    for item in items:
        value = str(item or "").strip()
        if value and value not in results:
            results.append(value)
    return results


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
    if settings.workbench_v2_enabled and isinstance(payload.get("authoring_graph"), dict):
        from dataclasses import asdict

        from app.services.workflow_version_store import (
            WorkflowVersionError,
            WorkflowVersionStore,
        )

        try:
            header, _draft = WorkflowVersionStore(
                _workbench_dir() / "workflows.db"
            ).create_workflow(
                workflow_id=str(payload.get("id") or ""),
                name=str(payload.get("name") or ""),
                description=str(payload.get("description") or ""),
                authoring_graph=dict(payload["authoring_graph"]),
            )
        except (ValueError, WorkflowVersionError) as exc:
            status_code = 409 if "already exists" in str(exc) else 422
            raise HTTPException(status_code=status_code, detail=str(exc))
        return asdict(header)
    workflow_id = str(payload.get("id") or "").strip() if isinstance(payload, dict) else ""
    if _is_builtin_workflow_id(workflow_id):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "内置工作流预设是只读的，请另存为自定义工作流后再编辑。",
                "workflow_id": workflow_id,
                "suggested_id": f"{workflow_id}_custom",
            },
        )
    try:
        workflow = _workflow_store().save_workflow(payload)
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _workflow_response(workflow.raw)


@router.post("/workflows/audit-draft")
async def audit_workflow_draft(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        audit = audit_workflow_definition(payload)
    except WorkflowValidationError as exc:
        return {
            "status": "invalid",
            "valid": False,
            "error": str(exc),
            "warnings": [],
        }
    return {
        "status": audit.get("status") or "ok",
        "valid": True,
        "error": "",
        "warnings": list(audit.get("warnings") or []),
    }


@router.post("/workflows/generate-draft")
async def generate_workflow_draft(payload: GenerateWorkflowDraftRequest) -> dict[str, Any]:
    prompt_text = payload.prompt.strip()
    generation_id = f"workflow_gen_{uuid.uuid4().hex}"
    messages = _workflow_generation_messages(
        prompt_text,
        preferred_id=payload.preferred_id,
        preferred_name=payload.preferred_name,
    )
    try:
        from app.llm.factory import create_llm_client_from_active

        llm = await create_llm_client_from_active()
        response = await llm.complete(messages, max_tokens=4096, temperature=0.2)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"工作流生成模型不可用：{exc}")

    raw_output = response.content.strip()
    try:
        draft = _extract_workflow_json(raw_output)
        if payload.preferred_id.strip():
            draft["id"] = _safe_workflow_id(payload.preferred_id)
        if payload.preferred_name.strip():
            draft["name"] = payload.preferred_name.strip()
        workflow = validate_workflow_definition(draft)
        audit = audit_workflow_definition(workflow.raw)
    except WorkflowValidationError as exc:
        artifact_path = _write_workflow_generation_artifact(
            generation_id=generation_id,
            prompt=prompt_text,
            raw_output=raw_output,
            workflow=None,
            audit={"status": "invalid", "valid": False, "error": str(exc), "warnings": []},
        )
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"AI 生成的工作流未通过校验：{exc}",
                "generation_id": generation_id,
                "artifact": {"path": _public_workbench_artifact_path(artifact_path)},
            },
        )
    except Exception as exc:
        artifact_path = _write_workflow_generation_artifact(
            generation_id=generation_id,
            prompt=prompt_text,
            raw_output=raw_output,
            workflow=None,
            audit={"status": "invalid", "valid": False, "error": str(exc), "warnings": []},
        )
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"无法解析 AI 工作流 JSON：{exc}",
                "generation_id": generation_id,
                "artifact": {"path": _public_workbench_artifact_path(artifact_path)},
            },
        )

    artifact_path = _write_workflow_generation_artifact(
        generation_id=generation_id,
        prompt=prompt_text,
        raw_output=raw_output,
        workflow=workflow.raw,
        audit=audit,
    )
    workflow_response = _workflow_response(workflow.raw)
    audit_response = {
        "status": audit.get("status") or "ok",
        "valid": True,
        "error": "",
        "warnings": list(audit.get("warnings") or []),
    }
    return {
        "generation_id": generation_id,
        "workflow": workflow_response,
        "audit": audit_response,
        "artifact": {"path": _public_workbench_artifact_path(artifact_path)},
        "model": response.model,
        "usage": response.usage,
    }


@router.get("/workflows")
async def list_workflows() -> list[dict[str, Any]]:
    store = _workflow_store_with_builtin_presets()
    legacy_items = [_workflow_response(item.raw) for item in store.list_workflows()]
    from app.services.workflow_version_store import WorkflowVersionStore

    version_store = WorkflowVersionStore(_workbench_dir() / "workflows.db")
    if not settings.workbench_v2_enabled:
        archived_v2_ids = {
            header.workflow_id
            for header in version_store.list_workflows(include_archived=True)
            if header.status == "archived"
        }
        return [
            item
            for item in legacy_items
            if str(item.get("id") or "") not in archived_v2_ids
        ]
    version_store.ensure_legacy_published_workflows(
        [dict(preset["definition"]) for preset in active_builtin_workflow_presets()]
    )
    version_store.retire_workflows(
        reserved_builtin_workflow_ids().difference(_active_builtin_workflow_ids())
    )
    all_v2_headers = version_store.list_workflows(include_archived=True)
    archived_v2_ids = {
        header.workflow_id for header in all_v2_headers if header.status == "archived"
    }
    v2_items = {
        header.workflow_id: _v2_workflow_compatibility_response(version_store, header)
        for header in all_v2_headers
        if header.status != "archived"
    }
    merged = []
    for item in legacy_items:
        workflow_id = str(item.get("id") or "")
        if workflow_id in archived_v2_ids:
            continue
        v2_item = v2_items.pop(workflow_id, None)
        if v2_item is None:
            merged.append(item)
        elif _is_active_builtin_workflow_id(workflow_id):
            merged.append(
                {
                    **item,
                    "authoring_graph": dict(v2_item.get("authoring_graph") or {}),
                    "v2": dict(v2_item.get("v2") or {}),
                }
            )
        else:
            merged.append(v2_item)
    merged.extend(v2_items.values())
    return merged


@router.post("/workflows/restore-builtins")
async def restore_builtin_workflows() -> dict[str, Any]:
    store = _workflow_store()
    restored = restore_builtin_workflow_presets(store)
    return {
        "status": "ok",
        "restored_count": len(restored),
        "items": [
            _workflow_response(item.raw)
            for item in _WorkflowCatalog(store).list_workflows()
        ],
    }


@router.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: str) -> dict[str, Any]:
    if settings.workbench_v2_enabled and not _is_builtin_workflow_id(workflow_id):
        from app.services.workflow_version_store import WorkflowVersionStore

        version_store = WorkflowVersionStore(_workbench_dir() / "workflows.db")
        try:
            header = version_store.get_workflow(workflow_id)
        except KeyError:
            pass
        else:
            return _v2_workflow_compatibility_response(version_store, header)
    try:
        workflow = _workflow_store_with_builtin_presets().get_workflow(workflow_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown workflow: {workflow_id}")
    return _workflow_response(workflow.raw)


@router.get("/workflows/{workflow_id}/snapshot")
async def get_workflow_snapshot(workflow_id: str) -> dict[str, Any]:
    try:
        return _workflow_store_with_builtin_presets().freeze_workflow_snapshot(workflow_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown workflow: {workflow_id}")


@router.get("/workflow-presets")
async def list_workflow_presets() -> dict[str, Any]:
    return {"items": active_builtin_workflow_presets()}


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
            "test_design_mindmap",
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
            "skill_injection": True,
        },
        "skill_catalog": [
            {
                "id": "source-evidence-first",
                "label": "源码证据优先",
                "source": "codetalk_builtin",
                "default_enabled": True,
                "description": "除非用户明确排除源码，先查工作区、GitNexus 和 CGC 产物，再生成结论。",
                "prompt_hint": "优先读取工作区源码、GitNexus 和 CGC 产物；所有关键结论必须引用真实文件或产物证据。",
            },
            {
                "id": "storage-flow-analysis",
                "label": "存储流程梳理",
                "source": "codetalk_builtin",
                "default_enabled": True,
                "description": "面向 SPDK/存储系统梳理入口、状态迁移、异常分支、恢复路径和可观测行为。",
                "prompt_hint": "按入口、前置条件、关键状态、正常流程、异常流程、恢复路径和外部可观测行为组织分析。",
            },
            {
                "id": "sfmea",
                "label": "SFMEA",
                "source": "codetalk_builtin",
                "default_enabled": True,
                "description": "生成 failure mode、cause、effect、detection、S/O/D/RPN 和 mitigation。",
                "prompt_hint": "SFMEA 每条必须包含 failure mode、cause、effect、detection、severity、occurrence、detection score、RPN、mitigation，并解释评分依据。",
            },
            {
                "id": "black-box-test-design",
                "label": "黑盒测试设计",
                "source": "codetalk_builtin",
                "default_enabled": True,
                "description": "只输出外部输入、操作、预期结果、日志/指标/状态和诊断线索。",
                "prompt_hint": "黑盒用例不得要求修改内部代码或调用内部函数；每条包含前置条件、步骤、预期、观测点和失败诊断线索。",
            },
            {
                "id": "test-strategy-planning",
                "label": "测试策略与计划",
                "source": "codetalk_builtin",
                "default_enabled": False,
                "description": "把测试目标拆成范围、风险、资源、环境、优先级、准入/准出和里程碑。",
                "prompt_hint": "输出测试策略、范围、风险优先级、准入/准出标准、资源/环境依赖、里程碑和未决问题。",
            },
            {
                "id": "coverage-gap-analysis",
                "label": "覆盖率与缺口分析",
                "source": "codetalk_builtin",
                "default_enabled": False,
                "description": "分析覆盖率、入口发现、低覆盖路径、灰盒/黑盒边界和补充建议。",
                "prompt_hint": "结合覆盖率文件、源码入口和现有测试目录，标出覆盖缺口、补充测试建议和证据映射。",
            },
            {
                "id": "test-execution-orchestration",
                "label": "测试执行编排",
                "source": "codetalk_builtin",
                "default_enabled": False,
                "description": "生成执行矩阵、批次、环境准备、数据准备、观测点、失败处置和复跑策略。",
                "prompt_hint": "输出可执行测试矩阵，包含环境、前置条件、批次顺序、并发/长跑安排、观测指标、失败诊断和复跑规则。",
            },
            {
                "id": "defect-triage-regression",
                "label": "缺陷分诊与回归",
                "source": "codetalk_builtin",
                "default_enabled": False,
                "description": "根据失败、日志、patch、风险和历史证据做缺陷分级、回归范围和阻塞判断。",
                "prompt_hint": "输出缺陷分级、复现线索、影响范围、回归测试范围、阻塞/放行建议和需要补充的证据。",
            },
            {
                "id": "performance-reliability-testing",
                "label": "性能与可靠性测试",
                "source": "codetalk_builtin",
                "default_enabled": False,
                "description": "覆盖性能基线、压力、soak、故障恢复、资源泄漏和可观测性指标。",
                "prompt_hint": "输出性能/可靠性测试计划，包含基线、负载模型、时延/吞吐/资源指标、故障注入、soak、退化阈值和诊断数据。",
            },
            {
                "id": "artifact-contract",
                "label": "产物契约",
                "source": "codetalk_builtin",
                "default_enabled": True,
                "description": "要求 Agent 写入声明的 JSON/Markdown artifact，CodeTalk 校验后才接受。",
                "prompt_hint": "必须把结果写入 required_artifacts 声明的文件；终端文字只能作为进度说明，不能替代 artifact。",
            },
        ],
        "semantic_library_import_formats": ["json", "jsonl", "ndjson", "csv", "txt"],
        "artifact_contract": {
            "required_artifacts": "validated locally before outputs are accepted",
            "raw_output": "stored for audit but never accepted as evidence without artifacts",
            "workflow_outputs": "collected from declared outputs and checked before acceptance",
        },
    }


@router.get("/node-registry")
async def get_node_registry() -> dict[str, Any]:
    """Expose backend-owned node metadata for the workflow designer."""
    return node_registry_payload()


@router.get("/core-workflow-readiness")
async def get_core_workflow_readiness() -> dict[str, Any]:
    """Audit the active release workflow as an executable contract."""
    required = [
        _core_workflow_readiness_item(item)
        for item in active_builtin_workflow_presets()
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
    _require_workflow_available_for_new_run(preset_id)
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
    public_path = _public_workbench_artifact_path(destination)
    metadata = {
        "kind": "workbench_input_upload",
        "upload_id": upload_id,
        "input_id": input_id.strip(),
        "filename": filename,
        "content_type": file.content_type or "",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "path": public_path,
        "input_payload": {"path": public_path},
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
    providers.extend(
        _agent_runtime_provider_matrix_item(runtime)
        for runtime in list_agent_runtimes_sync(enabled=True)
    )
    providers.append(_builtin_llm_provider_matrix_item())
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
                startup_probe=next(
                    (item for item in results if str(item.get("provider") or "") == provider),
                    None,
                ),
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
        "repo_path": _public_repo_path_label(payload.repo_path),
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
            "path": _public_workbench_artifact_path(artifact_path),
            "latest_path": _public_workbench_artifact_path(artifact_dir / "deployment_probe_latest.json"),
        },
    }
    response = _redact_public_repo_paths(response, payload.repo_path)
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
        startup_probe = await _run_deployment_probe_provider(provider, payload.repo_path)
        return _run_provider_task_probe_core(
            provider=provider,
            repo_path=payload.repo_path,
            timeout_sec=payload.timeout_sec,
            startup_probe=startup_probe,
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
    store = _memory_store()
    evidence_id = store.upsert_evidence_item(**payload.model_dump())
    source_slice_count = 0
    repo_path = str(payload.provenance.get("repo_path") or "")
    line_start = _safe_int(payload.provenance.get("line_start")) or 1
    if repo_path and payload.path:
        source_slice_id = _add_workbench_source_slice(
            store=store,
            evidence_id=evidence_id,
            repo_path=repo_path,
            rel_path=payload.path,
            line_start=line_start,
        )
        source_slice_count = 1 if source_slice_id else 0
    return {"evidence_id": evidence_id, "source_slice_count": source_slice_count}


@router.get("/memory/search")
async def search_memory(
    q: str = Query(..., min_length=1),
    workspace_id: str = "",
    limit: int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    store = _memory_store()
    items = store.search_analysis_memory(
        q,
        workspace_id=workspace_id or None,
        limit=limit,
    )
    return {
        "items": [
            _evidence_item_payload(
                item,
                source_slices=store.list_source_slices(item.evidence_id),
                repo_path=_evidence_repo_path(store, item),
            )
            for item in items
        ],
    }


@router.get("/memory/evidence/{evidence_id}/source-slices")
async def list_memory_source_slices(evidence_id: str) -> dict[str, Any]:
    store = _memory_store()
    try:
        item = store.get_evidence_item(evidence_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown evidence item: {evidence_id}")
    payload = _evidence_item_payload(
        item,
        source_slices=store.list_source_slices(evidence_id),
        repo_path=_evidence_repo_path(store, item),
    )
    return {"items": payload.get("source_slices", [])}


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
    run = AgentHarnessFacade(artifact_dir).prepare(
        HarnessRunRequest(
            run_id=run_id,
            provider=payload.provider,
            command=payload.command,
            cwd=payload.cwd,
            workflow_snapshot=payload.workflow_snapshot,
            task_bundle=payload.task_bundle,
            mcp_profile=payload.mcp_profile,
        )
    )
    return asdict(run)


@router.post("/agent-runs/{run_id}/raw-output")
async def record_agent_run_raw_output(run_id: str, payload: RawOutputCreate) -> dict[str, Any]:
    artifact_dir = _agent_run_dir(run_id)
    if not (artifact_dir / "agent_run.json").exists():
        raise HTTPException(status_code=404, detail=f"Unknown agent run: {run_id}")
    AgentHarnessFacade(artifact_dir).record_raw_output(
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
        result = AgentHarnessFacade(artifact_dir).execute(
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
        result = AgentHarnessFacade(artifact_dir).execute(
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
    response: Response,
) -> dict[str, Any]:
    try:
        task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")
    event_store = WorkbenchTaskRunEventStore(_task_runs_dir())
    if task_run_id in _ACTIVE_TASK_RUN_IDS:
        raise HTTPException(
            status_code=409,
            detail="上一次任务仍在退出中，请稍候再重新运行。",
        )
    current_status = event_store.current_status(task_run_id)
    if current_status in {"queued", "running"}:
        response.status_code = 202
        return _scheduled_task_run_response(task_run=task_run, status=current_status)

    try:
        event_store.mark_status(task_run_id, "queued")
        event_store.append(
            task_run_id,
            "queued",
            {
                "timeout_sec": payload.timeout_sec,
                "stop_on_error": payload.stop_on_error,
            },
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")
    asyncio.create_task(_execute_task_run_background(task_run_id=task_run_id, payload=payload))
    response.status_code = 202
    queued_task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    return _scheduled_task_run_response(task_run=queued_task_run, status="queued")


async def _execute_task_run_background(
    *,
    task_run_id: str,
    payload: TaskRunExecuteRequest,
) -> None:
    event_store = WorkbenchTaskRunEventStore(_task_runs_dir())
    _ACTIVE_TASK_RUN_IDS.add(task_run_id)
    try:
        if event_store.current_status(task_run_id) == "cancelled":
            event_store.append(
                task_run_id,
                "cancelled",
                {"status": "cancelled", "ignored_before_start": True},
            )
            return
        readiness = await _preflight_task_run_agent_runtimes(task_run_id)
        if readiness["status"] == "blocked":
            message = str(readiness["message"])
            event_store.mark_status_unless(
                task_run_id,
                "failed",
                blocked_statuses={"cancelled"},
                completed_at=datetime.now(timezone.utc).isoformat(),
                error=message,
            )
            event_store.mark_outcomes(
                task_run_id,
                quality_status="blocked",
                delivery_status="none",
            )
            event_store.append(
                task_run_id,
                "provider_readiness_blocked",
                {
                    "status": "failed",
                    "user_message": message,
                    "readiness_artifact": "provider_live_readiness.json",
                },
            )
            return
        started, _ = event_store.mark_status_unless(
            task_run_id,
            "running",
            blocked_statuses={"cancelled"},
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        if not started:
            event_store.append(
                task_run_id,
                "cancelled",
                {"status": "cancelled", "ignored_before_start": True},
            )
            return
        event_store.mark_outcomes(
            task_run_id,
            quality_status="pending",
            delivery_status="none",
        )
        event_store.append(task_run_id, "running", {})
        result = await asyncio.to_thread(
            _execute_task_run_with_closure,
            task_run_id=task_run_id,
            payload=payload,
        )
        status = _terminal_execution_status(result)
        updated, _ = event_store.mark_status_unless(
            task_run_id,
            status,
            blocked_statuses={"cancelled"},
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        if not updated:
            event_store.append(
                task_run_id,
                "cancelled",
                {"status": "cancelled", "ignored_late_result": True},
            )
            return
        event_store.append(
            task_run_id,
            "completed" if status == "completed" else "partial" if status == "partial" else "step_failed",
            {"status": status},
        )
    except asyncio.CancelledError:
        # A task cancellation can arrive while the blocking workflow thread is
        # still unwinding. Persist a visible terminal state before preserving
        # cancellation semantics for the caller or service shutdown path.
        try:
            updated, _ = event_store.mark_status_unless(
                task_run_id,
                "interrupted",
                blocked_statuses={"cancelled"},
                completed_at=datetime.now(timezone.utc).isoformat(),
                error="后台执行在完成前被中断；已保留诊断和已生成的文件。",
            )
            if updated:
                event_store.mark_outcomes(
                    task_run_id,
                    quality_status="blocked",
                    delivery_status="none",
                )
            event_store.append(
                task_run_id,
                "interrupted" if updated else "cancelled",
                {
                    "status": "interrupted" if updated else "cancelled",
                    "user_message": (
                        "后台执行被中断；已保留诊断和已生成的文件，可从中断节点重试。"
                        if updated
                        else "已取消本次工作流运行。"
                    ),
                },
            )
        except KeyError:
            pass
        raise
    except Exception as exc:  # pragma: no cover - defensive path is covered through API state.
        redacted = redact_agent_diagnostic_text(str(exc))
        try:
            updated, _ = event_store.mark_status_unless(
                task_run_id,
                "failed",
                blocked_statuses={"cancelled"},
                completed_at=datetime.now(timezone.utc).isoformat(),
                error=redacted,
            )
            if updated:
                failed_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
                _, delivery_status = _derive_task_run_outcomes(
                    execution={"test_activity_quality": {"deliverable": False}},
                    run_summary=_build_task_run_ui_summary(
                        failed_run,
                        Path(failed_run.artifact_dir),
                    ),
                )
                event_store.mark_outcomes(
                    task_run_id,
                    quality_status="blocked",
                    delivery_status=delivery_status,
                )
            event_store.append(
                task_run_id,
                "step_failed" if updated else "cancelled",
                {
                    "status": "failed" if updated else "cancelled",
                    "error": redacted if updated else "",
                    "user_message": (
                        "工作流后台执行失败，请查看内部诊断后重试。"
                        if updated
                        else "已取消本次工作流运行。"
                    ),
                },
            )
        except KeyError:
            return
    finally:
        _ACTIVE_TASK_RUN_IDS.discard(task_run_id)


async def _preflight_task_run_agent_runtimes(task_run_id: str) -> dict[str, Any]:
    """Probe frozen managed runtimes before a workflow process is allowed to start.

    Preparation can only establish that a command was configured.  This preflight
    uses the same real probe exposed in Settings, records its immutable result with
    the run, and prevents a known-unready managed Agent from producing a misleading
    short-lived "running" task.
    """
    task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    providers = (
        task_run.task_bundle.get("provider_snapshot", {}).get("providers", {})
        if isinstance(task_run.task_bundle, dict)
        else {}
    )
    checks: list[dict[str, Any]] = []
    for provider in sorted(providers) if isinstance(providers, dict) else []:
        runtime_id = agent_runtime_id_from_provider(provider)
        if not runtime_id:
            continue
        provider_snapshot = providers.get(provider)
        runtime = _frozen_runtime_preflight_payload(
            provider=provider,
            provider_snapshot=provider_snapshot if isinstance(provider_snapshot, dict) else {},
        )
        if runtime is None:
            checks.append({
                "provider": provider,
                "runtime_id": runtime_id,
                "success": False,
                "message": "所选 Agent 已不可用或被禁用，请在设置中重新启用后再运行。",
            })
            continue
        result = await probe_agent_runtime(runtime)
        checks.append({
            "provider": provider,
            "runtime_id": runtime_id,
            "success": bool(result.get("success")),
            "message": str(result.get("message") or ""),
        })
    bundle = task_run.task_bundle if isinstance(task_run.task_bundle, dict) else {}
    activity_contract = bundle.get("test_activity_contract")
    quality_readiness = build_behavior_claim_audit_readiness(
        required=bool(
            isinstance(activity_contract, dict)
            and (activity_contract.get("quality_gates") or {}).get(
                "require_independent_behavior_validation"
            )
            and activity_contract.get("artifact_contract")
        ),
        generator_identities=[
            str(item.get("provider") or "")
            for item in (bundle.get("workflow_contract") or {}).get("agent_steps") or []
            if isinstance(item, dict)
        ],
    )
    if quality_readiness.get("status") == "blocked":
        checks.append({
            "provider": "independent-quality-audit",
            "runtime_id": str(quality_readiness.get("mode") or "quality-audit"),
            "success": False,
            "message": str(quality_readiness.get("message") or "独立质量核验尚未就绪。"),
            "recommended_action": str(quality_readiness.get("recommended_action") or ""),
        })
    payload = {
        "schema_version": "provider-live-readiness-v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "quality_audit": quality_readiness,
    }
    task_dir = Path(task_run.artifact_dir)
    _write_json(task_dir / "provider_live_readiness.json", payload)
    failed = next((item for item in checks if not item["success"]), None)
    if failed:
        message = str(failed["message"] or "所选 Agent 尚未就绪。")
        recommended_action = str(failed.get("recommended_action") or "").strip()
        prefix = (
            "独立质量核验启动前检查未通过"
            if str(failed.get("provider") or "") == "independent-quality-audit"
            else "所选 Agent 未通过启动前可用性检查"
        )
        return {
            "status": "blocked",
            "message": f"{prefix}：{message}"
            + (f" {recommended_action}" if recommended_action else ""),
            "checks": checks,
        }
    return {"status": "ready", "checks": checks}


def _frozen_runtime_preflight_payload(
    *,
    provider: str,
    provider_snapshot: dict[str, Any],
) -> dict[str, Any] | None:
    """Build a safe probe request from the immutable run snapshot.

    Old task runs did not persist the runtime provider kind, so they retain the
    legacy Settings fallback. New runs never reinterpret their provider kind,
    command, args, or prompt transport after preparation.
    """
    runtime_id = agent_runtime_id_from_provider(provider)
    command_parts = [
        str(item) for item in provider_snapshot.get("command") or []
        if str(item).strip()
    ]
    runtime_provider = str(provider_snapshot.get("runtime_provider") or "").strip()
    prompt_transport = str(provider_snapshot.get("prompt_transport") or "").strip()
    if runtime_provider and command_parts and prompt_transport:
        return {
            "id": str(provider_snapshot.get("runtime_id") or runtime_id),
            "provider": runtime_provider,
            "command": command_parts[0],
            "args": command_parts[1:],
            "prompt_transport": prompt_transport,
            "enabled": True,
            # Runtime secrets are intentionally not persisted in the task.
            # Agent-owned credentials remain in the process environment.
            "env": {},
        }
    runtime = get_agent_runtime_sync(runtime_id)
    if runtime is None or not bool(runtime.get("enabled", True)):
        return None
    return runtime


def _terminal_execution_status(result: dict[str, Any]) -> str:
    quality = result.get("test_activity_quality")
    if (
        str(result.get("status") or "").strip().lower() == "quality_blocked"
        or isinstance(quality, dict) and quality.get("deliverable") is False
    ):
        # Node execution can finish while the final delivery fails its quality
        # contract. That terminal task state must never be shown as green.
        return "quality_blocked"
    explicit = str(result.get("execution_status") or "").strip().lower()
    if explicit in {"completed", "partial", "failed", "cancelled", "interrupted"}:
        return explicit
    legacy = str(result.get("status") or "completed").strip().lower()
    if legacy in {
        "completed", "completed_empty", "needs_review", "needs_rework",
        "ok", "ready", "success",
    }:
        return "completed"
    if legacy == "cancelled":
        return "cancelled"
    if legacy == "interrupted":
        return "interrupted"
    return "failed"


@router.post("/task-runs/{task_run_id}/cancel")
async def cancel_task_run(task_run_id: str) -> dict[str, Any]:
    try:
        task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")
    event_store = WorkbenchTaskRunEventStore(_task_runs_dir())
    current_status = event_store.current_status(task_run_id)
    if current_status not in {"queued", "running"}:
        return {
            "task_run_id": task_run_id,
            "status": current_status,
            "cancelled": False,
            "reason": "task run is not queued or running",
            "run_ui_summary": _build_task_run_ui_summary(task_run, Path(task_run.artifact_dir)),
        }
    event_store.mark_status(
        task_run_id,
        "cancelled",
        completed_at=datetime.now(timezone.utc).isoformat(),
    )
    event_store.mark_outcomes(
        task_run_id,
        quality_status="not_checked",
        delivery_status="none",
    )
    event_store.append(
        task_run_id,
        "cancelled",
        {
            "status": "cancelled",
            "user_message": "已取消本次工作流运行。正在运行的外部进程可能需要少量时间退出。",
        },
    )
    cancelled = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    return {
        "task_run_id": task_run_id,
        "status": "cancelled",
        "cancelled": True,
        "run_ui_summary": _build_task_run_ui_summary(cancelled, Path(cancelled.artifact_dir)),
    }


def _scheduled_task_run_response(*, task_run: Any, status: str) -> dict[str, Any]:
    task_dir = Path(task_run.artifact_dir)
    return {
        "status": status,
        "task_run_id": task_run.task_run_id,
        "workflow_id": task_run.workflow_id,
        "workspace_id": task_run.workspace_id,
        "task_run": _public_task_run_payload(task_run),
        "run_ui_summary": _build_task_run_ui_summary(task_run, task_dir),
    }


@router.get("/task-runs/{task_run_id}/events")
async def list_task_run_events(
    task_run_id: str,
    after_id: int = Query(default=0, ge=0),
    before_id: int | None = Query(default=None, ge=1),
    tail: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    try:
        WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")
    event_store = WorkbenchTaskRunEventStore(_task_runs_dir())
    if after_id and (before_id is not None or tail):
        raise HTTPException(status_code=422, detail="after_id cannot be combined with before_id or tail")
    if before_id is not None or tail:
        items = event_store.list_before(
            task_run_id,
            before_id=before_id,
            limit=limit,
        )
    else:
        items = event_store.list_after(
            task_run_id,
            after_id=after_id,
            limit=limit,
        )
    first_event_id = min((int(item.get("event_id") or 0) for item in items), default=0)
    return {
        "task_run_id": task_run_id,
        "items": items,
        "last_event_id": max((int(item.get("event_id") or 0) for item in items), default=after_id),
        "first_event_id": first_event_id,
        "has_older": first_event_id > 1,
        "latest_event_id": event_store.latest_event_id(task_run_id),
    }


@router.get("/task-runs/{task_run_id}/events/stream")
async def stream_task_run_events(
    task_run_id: str,
    request: Request,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    poll_ms: int = Query(default=250, ge=50, le=5000),
) -> StreamingResponse:
    try:
        WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")

    async def event_stream():
        cursor = int(after_id)
        event_store = WorkbenchTaskRunEventStore(_task_runs_dir())
        while True:
            if await request.is_disconnected():
                break
            items = event_store.list_after(task_run_id, after_id=cursor, limit=limit)
            for item in items:
                cursor = max(cursor, int(item.get("event_id") or cursor))
                yield _sse_event("task_run_event", item)
            status = event_store.current_status(task_run_id)
            if status in _TASK_RUN_TERMINAL_STATUSES:
                yield _sse_event(
                    "task_run_done",
                    {
                        "task_run_id": task_run_id,
                        "status": status,
                        "last_event_id": cursor,
                    },
                )
                break
            await asyncio.sleep(poll_ms / 1000)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _sse_event(event_type: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return f"event: {event_type}\ndata: {data}\n\n"


def _execute_task_run_with_closure(
    *,
    task_run_id: str,
    payload: TaskRunExecuteRequest,
) -> dict[str, Any]:
    try:
        event_store = WorkbenchTaskRunEventStore(_task_runs_dir())
        result = WorkbenchWorkflowRunner(
            _task_runs_dir(),
            event_sink=lambda event_type, event_payload: event_store.append(
                task_run_id,
                event_type,
                event_payload,
            ),
            is_cancelled=lambda: event_store.current_status(task_run_id) == "cancelled",
        ).execute_task_run(
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
    execution_payload = _read_json(task_dir / "workflow_execution.json")
    if isinstance(execution_payload, dict):
        execution_payload = _reconcile_acceptance_quality(
            task_dir=task_dir,
            execution=execution_payload,
            acceptance=acceptance,
        )
        response.update(execution_payload)
    write_task_artifact_manifest(task_dir, task_run_id=refreshed.task_run_id)
    response["acceptance_audit"] = acceptance
    run_summary = _build_task_run_ui_summary(refreshed, task_dir)
    if _is_diagnostic_trial(refreshed):
        # A designer node trial proves the same compiler/Harness/provider path,
        # but it cannot certify a workflow's full delivery contract or quality.
        # Keep its events and files inspectable without contaminating a formal
        # attempt's status or delivery record.
        quality_status, delivery_status = "not_checked", "none"
        response["diagnostic_only"] = True
    else:
        quality_status, delivery_status = _derive_task_run_outcomes(
            execution=response,
            run_summary=run_summary,
        )
    WorkbenchTaskRunEventStore(_task_runs_dir()).mark_outcomes(
        task_run_id,
        quality_status=quality_status,
        delivery_status=delivery_status,
    )
    refreshed = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    response["quality_status"] = refreshed.quality_status
    response["delivery_status"] = refreshed.delivery_status
    response["run_ui_summary"] = _build_task_run_ui_summary(refreshed, task_dir)
    return response


def _is_diagnostic_trial(task_run: Any) -> bool:
    bundle = getattr(task_run, "task_bundle", None)
    diagnostic = bundle.get("diagnostic") if isinstance(bundle, dict) else None
    return bool(
        isinstance(diagnostic, dict)
        and diagnostic.get("not_a_formal_delivery") is True
    )


def _derive_task_run_outcomes(
    *,
    execution: dict[str, Any],
    run_summary: dict[str, Any],
) -> tuple[str, str]:
    quality = execution.get("test_activity_quality")
    if not isinstance(quality, dict):
        quality_status = "not_checked"
    elif quality.get("deliverable") is False:
        quality_status = "blocked"
    elif int(quality.get("issue_count") or 0) > 0 or str(quality.get("status") or "") in {"warning", "partial"}:
        quality_status = "warning"
    elif quality.get("deliverable") is True:
        quality_status = "passed"
    else:
        quality_status = "not_checked"

    # A quality-blocked run may retain its generated bytes for diagnosis and
    # row-level repair, but those bytes are not a formal user delivery.
    # Counting them as "complete" makes the cockpit show contradictory state.
    if quality_status == "blocked":
        return quality_status, "none"

    expected_outputs: list[dict[str, Any]] = []
    for node in run_summary.get("nodes") or []:
        if isinstance(node, dict):
            expected_outputs.extend(
                output for output in node.get("outputs") or [] if isinstance(output, dict)
            )
    if not expected_outputs:
        expected_outputs = [
            item for item in run_summary.get("deliverables") or [] if isinstance(item, dict)
        ]
    available = sum(
        1
        for output in expected_outputs
        if str(output.get("status_label") or "")
        in {"已生成", "已生成但信息不足", "需要复核", "可下载"}
        and bool(str(output.get("path") or output.get("artifact") or "").strip())
    )
    delivery_status = (
        "none"
        if not expected_outputs or available == 0
        else "complete"
        if available == len(expected_outputs)
        else "partial"
    )
    return quality_status, delivery_status


def _reconcile_persisted_task_run_outcomes(task_run: Any) -> Any:
    """Backfill stale status pairs when an older run is opened.

    Artifact bytes remain inspectable after a quality failure, so historical
    records created before the delivery contract tightened can otherwise show
    the impossible pair "quality blocked / delivery complete" forever.
    """
    if _is_diagnostic_trial(task_run):
        return task_run
    task_dir = Path(task_run.artifact_dir)
    execution = _read_json(task_dir / "workflow_execution.json")
    if not isinstance(execution, dict):
        return task_run
    quality_status, delivery_status = _derive_task_run_outcomes(
        execution=execution,
        run_summary=_build_task_run_ui_summary(task_run, task_dir),
    )
    if (
        quality_status == str(task_run.quality_status or "")
        and delivery_status == str(task_run.delivery_status or "")
    ):
        return task_run
    WorkbenchTaskRunEventStore(_task_runs_dir()).mark_outcomes(
        task_run.task_run_id,
        quality_status=quality_status,
        delivery_status=delivery_status,
    )
    return WorkbenchTaskRunStore(_task_runs_dir()).load(task_run.task_run_id)


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
    quality_gate_result = _workflow_output_quality_gate_result(
        task_run=task_run,
        workflow_outputs=workflow_outputs,
    )
    if quality_gate_result is not None:
        _attach_workflow_output_materialization_audit(
            task_run=task_run,
            workflow_outputs=workflow_outputs,
            result=quality_gate_result,
        )
        _write_workflow_output_materialization_artifact(
            task_run=task_run,
            workflow_outputs_path=workflow_outputs_path,
            workflow_outputs=workflow_outputs,
            result=quality_gate_result,
        )
        deferred_semantic_import = _defer_semantic_output_import_if_configured(
            task_run=task_run,
            reason=str(
                quality_gate_result.get("reason")
                or "test_activity_quality_gate_failed"
            ),
        )
        if deferred_semantic_import is not None:
            quality_gate_result["semantic_output_import"] = deferred_semantic_import
        write_task_artifact_manifest(task_dir, task_run_id=task_run.task_run_id)
        return quality_gate_result
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
    _attach_workflow_output_materialization_audit(
        task_run=task_run,
        workflow_outputs=workflow_outputs,
        result=result,
    )
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


def _defer_semantic_output_import_if_configured(
    *,
    task_run: Any,
    reason: str,
) -> dict[str, Any] | None:
    if not _semantic_import_output_configs(task_run):
        return None
    result = {
        "status": "skipped",
        "reason": str(reason or "test_activity_quality_gate_failed"),
        "imported_count": 0,
        "rejected_count": 0,
        "imported": [],
        "rejected": [],
        "source_ref": f"task_run:{task_run.task_run_id}",
        "source_refs": [],
    }
    _write_semantic_output_import_artifact(
        task_run=task_run,
        mode="auto_deferred",
        result=result,
    )
    return result


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
    quality_gate_result = _workflow_output_quality_gate_result(
        task_run=task_run,
        workflow_outputs=workflow_outputs,
    )
    if quality_gate_result is not None:
        _attach_workflow_output_materialization_audit(
            task_run=task_run,
            workflow_outputs=workflow_outputs,
            result=quality_gate_result,
        )
        _write_workflow_output_materialization_artifact(
            task_run=task_run,
            workflow_outputs_path=workflow_outputs_path,
            workflow_outputs=workflow_outputs,
            result=quality_gate_result,
        )
        deferred_semantic_import = _defer_semantic_output_import_if_configured(
            task_run=task_run,
            reason=str(
                quality_gate_result.get("reason")
                or "test_activity_quality_gate_failed"
            ),
        )
        if deferred_semantic_import is not None:
            quality_gate_result["semantic_output_import"] = deferred_semantic_import
        write_task_artifact_manifest(task_dir, task_run_id=task_run.task_run_id)
        return quality_gate_result
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
    _attach_workflow_output_materialization_audit(
        task_run=task_run,
        workflow_outputs=workflow_outputs,
        result=result,
    )
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


def _workflow_output_quality_gate_result(
    *,
    task_run: Any,
    workflow_outputs: dict[str, Any],
) -> dict[str, Any] | None:
    execution = _read_json(Path(task_run.artifact_dir) / "workflow_execution.json")
    if not isinstance(execution, dict):
        return None
    quality = execution.get("test_activity_quality")
    if not isinstance(quality, dict) or quality.get("status") not in {"needs_rework", "invalid"}:
        return None
    rejected_outputs = [
        {
            "output": str(output.get("id") or ""),
            "reason": "test_activity_quality_gate_failed",
        }
        for output in workflow_outputs.get("outputs") or []
        if isinstance(output, dict) and output.get("status") == "ok"
    ]
    return {
        "status": "skipped",
        "reason": "test_activity_quality_gate_failed",
        "evidence_count": 0,
        "evidence_ids": [],
        "rejected_outputs": rejected_outputs,
        "test_activity_quality": quality,
    }


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
    quality_gate_result = _workflow_output_quality_gate_result(
        task_run=task_run,
        workflow_outputs=workflow_outputs,
    )
    if quality_gate_result is not None:
        result = _empty_semantic_import_result(
            source_ref=f"task_run:{task_run.task_run_id}"
        )
        result["reason"] = str(
            quality_gate_result.get("reason")
            or "test_activity_quality_gate_failed"
        )
        _write_semantic_output_import_artifact(
            task_run=task_run,
            mode="manual_blocked",
            result=result,
        )
        write_task_artifact_manifest(task_dir, task_run_id=task_run.task_run_id)
        return result
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
    return {"items": [_public_task_run_payload(item) for item in items]}


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
        _write_json(
            Path(task_run.artifact_dir) / "provider_live_readiness.json",
            {
                "schema_version": "provider-live-readiness-v1",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "checks": [{
                    "provider": provider_id,
                    "runtime_id": provider_id,
                    "success": True,
                    "message": "CodeTalk smoke Agent is materialized locally for this controlled task-contract check.",
                }],
                "probe_kind": "controlled_smoke_contract",
            },
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
            "task_run": _public_task_run_payload(refreshed),
            "execution": asdict(execution),
            "acceptance_audit": acceptance,
        }
        smoke_artifact = task_dir / "smoke_e2e_result.json"
        result["artifact"] = {"path": _public_task_artifact_path(task_dir, smoke_artifact)}
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
    task_run = _reconcile_persisted_task_run_outcomes(task_run)
    return _public_task_run_payload(task_run)


@router.get("/task-runs/{task_run_id}/rerun-plan")
async def get_task_run_rerun_plan(task_run_id: str) -> dict[str, Any]:
    try:
        task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")
    return _ensure_task_rerun_plan(task_run)


@router.post("/task-runs/{task_run_id}/acceptance-audit")
async def create_task_run_acceptance_audit(task_run_id: str) -> dict[str, Any]:
    try:
        task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")
    runtime_status = WorkbenchTaskRunEventStore(_task_runs_dir()).current_status(task_run_id)
    if runtime_status in {"queued", "running"} or task_run_id in _ACTIVE_TASK_RUN_IDS:
        raise HTTPException(
            status_code=409,
            detail="任务正在运行中，请等待执行完成后再进行验收审计。",
        )
    task_dir = Path(task_run.artifact_dir)
    quality = WorkbenchWorkflowRunner(_task_runs_dir()).audit_test_activity_quality(
        task_run=task_run,
    )
    execution_path = task_dir / "workflow_execution.json"
    execution = _read_json(execution_path)
    if quality and isinstance(execution, dict):
        execution["test_activity_quality"] = quality
        execution = _promote_partial_execution_after_deliverable_quality(
            execution=execution,
            quality=quality,
        )
        quality_base_status = str(execution.get("quality_audit_base_status") or "")
        if (
            quality.get("deliverable") is False
            and str(execution.get("status") or "") in {"completed", "completed_empty"}
        ):
            execution["quality_audit_base_status"] = str(execution.get("status") or "completed")
            execution["status"] = "quality_blocked"
        elif (
            quality.get("deliverable") is True
            and str(execution.get("status") or "") == "quality_blocked"
            and quality_base_status in {"completed", "completed_empty"}
        ):
            execution["status"] = quality_base_status
            execution.pop("quality_audit_base_status", None)
        rerun_plan = build_workflow_rerun_plan(
            task_run=task_run,
            status=str(execution.get("status") or runtime_status or "prepared"),
            step_results=[
                dict(item)
                for item in execution.get("step_results") or []
                if isinstance(item, dict)
            ],
            outputs=[
                dict(item)
                for item in execution.get("outputs") or []
                if isinstance(item, dict)
            ],
        )
        execution["rerun_plan"] = rerun_plan
        _write_json(task_dir / "task_rerun_plan.json", rerun_plan)
        _write_json(execution_path, execution)
    payload = _build_task_acceptance_audit(task_run)
    _write_json(task_dir / "task_acceptance_audit.json", payload)
    reconciled_execution = _read_json(task_dir / "workflow_execution.json")
    if isinstance(reconciled_execution, dict):
        _reconcile_acceptance_quality(
            task_dir=task_dir,
            execution=reconciled_execution,
            acceptance=payload,
        )
        event_store = WorkbenchTaskRunEventStore(_task_runs_dir())
        if (
            str(reconciled_execution.get("status") or "") == "completed"
            and event_store.current_status(task_run_id) == "partial"
        ):
            event_store.mark_status(task_run_id, "completed")
            event_store.append(
                task_run_id,
                "completed",
                {
                    "status": "completed",
                    "reconciled_from_partial": True,
                    "user_message": "最终质量审计已接受保留的结果，运行已恢复为完成。",
                },
            )
        run_summary = _build_task_run_ui_summary(task_run, task_dir)
        if _is_diagnostic_trial(task_run):
            quality_status, delivery_status = "not_checked", "none"
        else:
            quality_status, delivery_status = _derive_task_run_outcomes(
                execution=reconciled_execution,
                run_summary=run_summary,
            )
        WorkbenchTaskRunEventStore(_task_runs_dir()).mark_outcomes(
            task_run_id,
            quality_status=quality_status,
            delivery_status=delivery_status,
        )
    write_task_artifact_manifest(task_dir, task_run_id=task_run.task_run_id)
    return payload


@router.get("/task-runs/{task_run_id}/rerun-plan/validation")
async def validate_task_run_rerun_plan(task_run_id: str) -> dict[str, Any]:
    try:
        task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")
    plan = _ensure_task_rerun_plan(task_run)
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
    runtime_status = WorkbenchTaskRunEventStore(_task_runs_dir()).current_status(task_run_id)
    if task_run_id in _ACTIVE_TASK_RUN_IDS or runtime_status in {"queued", "running"}:
        raise HTTPException(
            status_code=409,
            detail="上一次任务仍在退出中，请稍候再重新运行。",
        )
    plan = _ensure_task_rerun_plan(task_run)
    validation_before = _validate_task_rerun_plan(task_run=task_run, plan=plan)
    if not validation_before.get("can_rerun"):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "task rerun plan is not executable",
                "validation": validation_before,
            },
        )
    event_store = WorkbenchTaskRunEventStore(_task_runs_dir())
    event_store.mark_status(task_run_id, "queued")
    event_store.append(
        task_run_id,
        "queued",
        {
            "rerun": True,
            "timeout_sec": payload.timeout_sec,
            "stop_on_error": payload.stop_on_error,
        },
    )
    _ACTIVE_TASK_RUN_IDS.add(task_run_id)
    try:
        started, _ = event_store.mark_status_unless(
            task_run_id,
            "running",
            blocked_statuses={"cancelled"},
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        if not started:
            event_store.append(
                task_run_id,
                "cancelled",
                {"status": "cancelled", "rerun": True, "ignored_before_start": True},
            )
            execution = {
                "task_run_id": task_run_id,
                "status": "cancelled",
                "step_results": [],
                "outputs": [],
            }
        else:
            event_store.append(task_run_id, "running", {"rerun": True})
            execution = await asyncio.to_thread(
                _execute_task_run_with_closure,
                task_run_id=task_run_id,
                payload=payload,
            )
        execution_status = str(execution.get("status") or "completed")
        updated, _ = event_store.mark_status_unless(
            task_run_id,
            execution_status,
            blocked_statuses={"cancelled"},
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        if not updated:
            execution_status = "cancelled"
            execution["status"] = "cancelled"
        event_store.append(
            task_run_id,
            (
                "cancelled"
                if execution_status == "cancelled"
                else "completed"
                if execution_status in {"completed", "ok", "ready", "success"}
                else "step_failed"
            ),
            {"status": execution_status, "rerun": True},
        )
    except ValueError as exc:
        updated, _ = event_store.mark_status_unless(
            task_run_id,
            "failed",
            blocked_statuses={"cancelled"},
            completed_at=datetime.now(timezone.utc).isoformat(),
            error=str(exc),
        )
        event_store.append(
            task_run_id,
            "step_failed" if updated else "cancelled",
            {
                "status": "failed" if updated else "cancelled",
                "error": str(exc) if updated else "",
                "rerun": True,
            },
        )
        if updated:
            raise HTTPException(status_code=400, detail=str(exc))
        execution = {
            "task_run_id": task_run_id,
            "status": "cancelled",
            "step_results": [],
            "outputs": [],
        }
    except Exception as exc:
        redacted = redact_agent_diagnostic_text(str(exc))
        updated, _ = event_store.mark_status_unless(
            task_run_id,
            "failed",
            blocked_statuses={"cancelled"},
            completed_at=datetime.now(timezone.utc).isoformat(),
            error=redacted,
        )
        event_store.append(
            task_run_id,
            "step_failed" if updated else "cancelled",
            {
                "status": "failed" if updated else "cancelled",
                "error": redacted if updated else "",
                "rerun": True,
                "user_message": (
                    "工作流复跑失败，请查看内部诊断后重试。"
                    if updated
                    else "已取消本次工作流运行。"
                ),
            },
        )
        if updated:
            raise
        execution = {
            "task_run_id": task_run_id,
            "status": "cancelled",
            "step_results": [],
            "outputs": [],
        }
    finally:
        _ACTIVE_TASK_RUN_IDS.discard(task_run_id)
    refreshed_task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    evidence_materialization = execution.get("evidence_materialization") or {}
    task_dir = Path(refreshed_task_run.artifact_dir)
    semantic_output_import = execution.get("semantic_output_import") or {}
    acceptance = execution.get("acceptance_audit") or _build_task_acceptance_audit(
        refreshed_task_run
    )
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
        "execution": {
            key: value
            for key, value in execution.items()
            if key
            not in {
                "evidence_materialization",
                "semantic_output_import",
                "acceptance_audit",
                "run_ui_summary",
            }
        },
        "evidence_materialization": evidence_materialization,
        "semantic_output_import": semantic_output_import,
        "acceptance_audit": acceptance,
        "validation_after": validation_after,
        "run_ui_summary": _build_task_run_ui_summary(refreshed_task_run, task_dir),
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
        "artifact_dir": ".",
        "artifacts": _public_artifact_manifest(task_dir),
    }


@router.get("/task-runs/{task_run_id}/diagnostic-package")
async def download_task_run_diagnostic_package(task_run_id: str) -> StreamingResponse:
    try:
        task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")

    task_dir = Path(task_run.artifact_dir)
    summary = _build_task_run_ui_summary(task_run, task_dir)
    events = WorkbenchTaskRunEventStore(_task_runs_dir()).list_after(
        task_run_id,
        after_id=0,
        limit=1000,
    )
    artifact_manifest = _public_artifact_manifest(task_dir)
    diagnostic_summary = {
        "task_run_id": task_run.task_run_id,
        "workflow_id": task_run.workflow_id,
        "workspace_id": task_run.workspace_id,
        "status": summary.get("status") or task_run.status,
        "failure_reasons": (summary.get("failure") or {}).get("reasons") or [],
        "recommended_actions": (summary.get("failure") or {}).get("recommended_actions") or [],
        "event_count": len(events),
        "artifact_count": len(artifact_manifest),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    def redacted_json(payload: Any) -> bytes:
        serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        return redact_agent_diagnostic_text(serialized).encode("utf-8")

    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("diagnostic_summary.json", redacted_json(diagnostic_summary))
        archive.writestr("task_run.json", redacted_json(_public_task_run_payload(task_run)))
        archive.writestr("events.json", redacted_json(events))
        archive.writestr("artifact_manifest.json", redacted_json(artifact_manifest))

        total_text_bytes = 0
        for path in sorted(task_dir.rglob("*"), key=lambda item: item.as_posix()):
            if not path.is_file() or path.suffix.lower() not in TEXT_ARTIFACT_SUFFIXES:
                continue
            try:
                relative_path = path.resolve().relative_to(task_dir.resolve()).as_posix()
                raw_text = path.read_text(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                continue
            redacted = redact_agent_diagnostic_text(raw_text)[:200_000]
            encoded = redacted.encode("utf-8")
            if total_text_bytes + len(encoded) > 5_000_000:
                break
            total_text_bytes += len(encoded)
            archive.writestr(f"artifacts/{relative_path}", encoded)

    archive_buffer.seek(0)
    headers = {
        "Content-Disposition": f'attachment; filename="{task_run_id}-diagnostic.zip"',
        "Cache-Control": "no-store",
    }
    return StreamingResponse(archive_buffer, media_type="application/zip", headers=headers)


@router.get("/task-runs/{task_run_id}/artifacts/content/{artifact_path:path}")
async def get_task_run_artifact_content(
    task_run_id: str,
    artifact_path: str,
    max_chars: int = Query(50000, ge=1, le=2_000_000),
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


@router.get("/task-runs/{task_run_id}/artifacts/download/{artifact_path:path}")
async def download_task_run_artifact(
    task_run_id: str,
    artifact_path: str,
) -> Response:
    try:
        task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(task_run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}")
    task_dir = Path(task_run.artifact_dir)
    path = _resolve_task_artifact_path(task_dir, artifact_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"Unknown artifact: {artifact_path}")
    data = path.read_bytes()
    if path.suffix.lower() in TEXT_ARTIFACT_SUFFIXES:
        data = redact_agent_diagnostic_text(
            data.decode("utf-8", errors="replace")
        ).encode("utf-8")
    return Response(
        content=data,
        media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(path.name)}",
            "Cache-Control": "no-store",
        },
    )


@router.post("/task-runs/prepare", status_code=201)
async def prepare_task_run(payload: PrepareTaskRunRequest) -> dict[str, Any]:
    _require_workflow_available_for_new_run(payload.workflow_id)
    await apply_persisted_agent_provider_settings()
    try:
        result = WorkbenchTaskRunPreparer(
            artifact_root=_task_runs_dir(),
            workflow_store=_workflow_store_with_builtin_presets(),
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
    return _public_task_run_payload(result)


@router.post("/task-runs/run", status_code=202)
async def prepare_and_execute_task_run(payload: RunTaskRunRequest) -> dict[str, Any]:
    _require_workflow_available_for_new_run(payload.workflow_id)
    await apply_persisted_agent_provider_settings()
    try:
        prepared = WorkbenchTaskRunPreparer(
            artifact_root=_task_runs_dir(),
            workflow_store=_workflow_store_with_builtin_presets(),
            evidence_memory=_memory_store(),
            semantic_library=_semantic_store(),
        ).prepare(
            workflow_id=payload.workflow_id,
            workspace_id=payload.workspace_id,
            repo_path=payload.repo_path,
            inputs=payload.inputs,
            provider_override=payload.provider_override,
        )
        execute_payload = TaskRunExecuteRequest(
            timeout_sec=payload.timeout_sec,
            stop_on_error=payload.stop_on_error,
        )
        event_store = WorkbenchTaskRunEventStore(_task_runs_dir())
        event_store.mark_status(prepared.task_run_id, "queued")
        event_store.append(
            prepared.task_run_id,
            "queued",
            {
                "timeout_sec": execute_payload.timeout_sec,
                "stop_on_error": execute_payload.stop_on_error,
            },
        )
        asyncio.create_task(
            _execute_task_run_background(
                task_run_id=prepared.task_run_id,
                payload=execute_payload,
            )
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown workflow: {payload.workflow_id}")
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    task_run = WorkbenchTaskRunStore(_task_runs_dir()).load(prepared.task_run_id)
    task_dir = Path(task_run.artifact_dir)
    return {
        "status": "queued",
        "task_run_id": task_run.task_run_id,
        "workflow_id": task_run.workflow_id,
        "workspace_id": task_run.workspace_id,
        "task_run": _public_task_run_payload(task_run),
        "run_ui_summary": _build_task_run_ui_summary(task_run, task_dir),
        "artifact": {
            "path": _public_task_artifact_path(task_dir, task_dir / "task_run.json"),
            "manifest_path": _public_task_artifact_path(task_dir, task_dir / "task_artifact_manifest.json"),
        },
    }


def _workflow_response(payload: dict[str, Any]) -> dict[str, Any]:
    response = dict(payload)
    response["audit"] = audit_workflow_definition(payload)
    return response


def _v2_workflow_compatibility_response(version_store: Any, header: Any) -> dict[str, Any]:
    from dataclasses import asdict

    selected_version_id = header.published_version_id or header.current_draft_version_id
    version = version_store.get_version(selected_version_id) if selected_version_id else None
    definition = dict(version.compiled_definition or {}) if version else {}
    response = {
        "id": header.workflow_id,
        "name": header.name,
        "description": header.description,
        "version": int(version.version_number if version else 1),
        "inputs": list(definition.get("inputs") or []),
        "steps": list(definition.get("steps") or []),
        "outputs": list(definition.get("outputs") or []),
        "authoring_graph": dict(version.authoring_graph or {}) if version else {},
        "v2": asdict(header),
    }
    if definition:
        response.update(definition)
        response["authoring_graph"] = dict(version.authoring_graph or {})
        response["v2"] = asdict(header)
    response["audit"] = (
        audit_workflow_definition(definition)
        if definition
        else {"status": "draft", "warnings": []}
    )
    return response


def _workflow_generation_messages(
    user_prompt: str,
    *,
    preferred_id: str = "",
    preferred_name: str = "",
) -> list[dict[str, str]]:
    preferred = []
    if preferred_id.strip():
        preferred.append(f"- preferred id: {_safe_workflow_id(preferred_id)}")
    if preferred_name.strip():
        preferred.append(f"- preferred name: {preferred_name.strip()}")
    preferred_text = "\n".join(preferred) if preferred else "- no preferred id/name supplied"
    return [
        {
            "role": "system",
            "content": (
                "You generate CodeTalk Agent Workbench workflow definitions. "
                "Return exactly one JSON object, no Markdown fences, no prose. "
                "The JSON must validate against CodeTalk's workflow DSL. "
                "Use only allowed input and step types. Prefer source-backed, artifact-first "
                "testing workflows. For storage/code-analysis testing, include repo_path as a "
                "directory input with resolver local, and include structured JSON schemas for "
                "JSON outputs so audit does not warn about missing schemas. "
                "Every agent_task must declare required_artifacts. Outputs that reference agent "
                "artifacts must include from and artifact. Black-box test outputs should use "
                "type test_cases with semantic_import defaults when appropriate. Use the "
                "test_design_mindmap output type only when the user asks for a test-design mind map; "
                "its canonical JSON/HTML/SVG artifacts are generated automatically."
            ),
        },
        {
            "role": "user",
            "content": (
                "Allowed input types: "
                + ", ".join(sorted(ALLOWED_INPUT_TYPES))
                + "\nAllowed step types: "
                + ", ".join(sorted(ALLOWED_STEP_TYPES))
                + "\nCommon output types: markdown, json, test_cases, scope_report, test_design_mindmap.\n"
                "Resolvers: manual, local, agent_mcp.\n"
                f"{preferred_text}\n\n"
                "Required workflow JSON shape:\n"
                "{\n"
                '  "id": "lower_snake_case_id",\n'
                '  "name": "Human readable name",\n'
                '  "version": 1,\n'
                '  "inputs": [{"id":"analysis_object","type":"free_text","required":true}],\n'
                '  "steps": [{"id":"agent_collect","type":"agent_task","provider":"claude-code","goal":"...","required_artifacts":["evidence_cards.json"]}],\n'
                '  "outputs": [{"id":"report","type":"markdown","from":"render_report"}]\n'
                "}\n\n"
                "User workflow request:\n"
                + user_prompt
            ),
        },
    ]


def _extract_workflow_json(raw_output: str) -> dict[str, Any]:
    text = raw_output.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("workflow draft must be a JSON object")
    return payload


def _safe_workflow_id(value: str) -> str:
    candidate = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip().lower()).strip("_")
    candidate = re.sub(r"_+", "_", candidate)
    if not candidate:
        candidate = f"ai_workflow_{uuid.uuid4().hex[:8]}"
    if not re.match(r"^[A-Za-z_]", candidate):
        candidate = f"workflow_{candidate}"
    return candidate[:80]


def _write_workflow_generation_artifact(
    *,
    generation_id: str,
    prompt: str,
    raw_output: str,
    workflow: dict[str, Any] | None,
    audit: dict[str, Any],
) -> Path:
    artifact_dir = _workbench_dir() / "workflow_generations"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{_safe_segment(generation_id, 'generation_id')}.json"
    payload = {
        "kind": "workflow_generation",
        "generation_id": generation_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "prompt": truncate_redacted_text(prompt, 8000),
        "raw_output": truncate_redacted_text(raw_output, 20000),
        "workflow": workflow,
        "audit": audit,
    }
    artifact_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return artifact_path


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
            "Agent CLI 自己持有 MCP 凭证和远端访问权限；CodeTalk 只下发任务包并校验返回产物。"
        ),
        "diagnostics": build_agent_cli_provider_diagnostics(provider_id, spec),
        "unavailable_behavior": (
            "Workflow preparation continues; execution records unavailable or failed "
            "Agent diagnostics without trusting unvalidated output."
        ),
    }


def _agent_runtime_provider_matrix_item(runtime: dict[str, Any]) -> dict[str, Any]:
    item = _agent_runtime_provider_snapshot_item(runtime)
    return {
        **item,
        "non_blocking": True,
        "readonly_args": list(item.get("readonly_args") or []),
    }


def _builtin_llm_provider_matrix_item() -> dict[str, Any]:
    item = _builtin_llm_provider_snapshot_item()
    return {
        **item,
        "provider": BUILTIN_LLM_PROVIDER_ID,
        "non_blocking": True,
        "readonly_args": [],
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
                "当 Agent CLI 持有 fast-context 凭证时，请把这个要求写进工作流任务包，不要让 CodeTalk 直接调用。",
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
    preset_ids = {
        str(item.get("id") or "") for item in active_builtin_workflow_presets()
    }
    required_preset_ids = {"source_flow_sfmea_blackbox"}
    checks = [
        _system_audit_check(
            check_id="workbench_data_dir",
            ok=workbench_dir.exists() and workbench_dir.is_dir(),
            severity="required",
            description="Workbench data directory exists",
            details={"path": "."},
        ),
        _system_audit_check(
            check_id="workflow_store",
            ok=True,
            severity="required",
            description="Workflow store can be constructed",
            details={"path": _public_workbench_artifact_path(_workbench_dir() / "workflows.db")},
        ),
        _system_audit_check(
            check_id="evidence_memory_store",
            ok=True,
            severity="required",
            description="Evidence Memory store can be constructed",
            details={"path": _public_workbench_artifact_path(_workbench_dir() / "evidence_memory.db")},
        ),
        _system_audit_check(
            check_id="semantic_library_store",
            ok=True,
            severity="required",
            description="Test Semantic Library store can be constructed",
            details={"path": _public_workbench_artifact_path(_workbench_dir() / "test_semantics.db")},
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
            details={"path": _public_workbench_artifact_path(_task_runs_dir())},
        ),
        _system_audit_check(
            check_id="agent_runs_dir",
            ok=_agent_runs_dir().exists(),
            severity="required",
            description="Standalone Agent run artifact directory is available",
            details={"path": _public_workbench_artifact_path(_agent_runs_dir())},
        ),
        _system_audit_check(
            check_id="task_acceptance_audit_api",
            ok=True,
            severity="required",
            description="Task-level acceptance audit API is registered",
            details={"endpoint": "POST /api/workbench/task-runs/{task_run_id}/acceptance-audit"},
        ),
        _agent_cli_launch_readiness_check(provider_matrix),
        _codetalk_index_provider_readiness_check(provider_matrix),
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
            if missing_required or _has_degraded_runtime_readiness(checks)
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


def _codetalk_index_provider_readiness_check(provider_matrix: list[dict[str, Any]]) -> dict[str, Any]:
    index_providers = [
        item for item in provider_matrix
        if isinstance(item, dict) and item.get("owner") == "codetalk_index"
    ]
    ready_statuses = {"available", "configured"}
    ready: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for item in index_providers:
        provider_id = str(item.get("provider") or "")
        diagnostics = item.get("diagnostics") if isinstance(item.get("diagnostics"), dict) else {}
        status = str(item.get("status") or "unknown")
        record = {
            "provider": provider_id,
            "display_name": str(item.get("display_name") or provider_id),
            "status": status,
            "codetalk_callable": bool(item.get("codetalk_callable", False)),
            "non_blocking": bool(item.get("non_blocking", True)),
            "health_endpoint": str(diagnostics.get("health_endpoint") or ""),
            "startup_probe_endpoint": str(diagnostics.get("startup_probe_endpoint") or ""),
            "unavailable_behavior": str(item.get("unavailable_behavior") or ""),
            "next_check": _codetalk_index_provider_next_check(provider_id, diagnostics),
        }
        if status in ready_statuses:
            ready.append(record)
        else:
            failed.append(record)

    recommended_actions = [
        (
            f"Configure {item['provider']} and run {item['next_check']}; "
            f"fallback remains non-blocking: {item['unavailable_behavior']}"
        )
        for item in failed
    ]
    return _system_audit_check(
        check_id="codetalk_index_provider_readiness",
        ok=bool(ready) and not failed,
        severity="recommended",
        description="GitNexus/CGC index providers are configured for discovery and call-graph enrichment",
        details={
            "provider_count": len(index_providers),
            "ready_provider_count": len(ready),
            "failed_provider_count": len(failed),
            "ready_provider_ids": [item["provider"] for item in ready],
            "failed_provider_ids": [item["provider"] for item in failed],
            "ready_providers": ready,
            "failed_providers": failed,
            "recommended_actions": recommended_actions,
            "notes": [
                "This checks configuration visibility, not a full graph query.",
                "Run the startup probe for each index provider with the target repo_path for live evidence.",
                "Index provider failure is non-blocking; local search, Evidence Memory, and Agent CLI discovery continue.",
            ],
        },
    )


def _codetalk_index_provider_next_check(provider: str, diagnostics: dict[str, Any]) -> str:
    endpoint = str(diagnostics.get("startup_probe_endpoint") or "")
    if endpoint:
        return f"POST {endpoint}?repo_path=<repo_path>"
    return f"Verify {provider} base URL/configuration and rerun Workbench system audit."


def _core_workflow_readiness_item(preset: dict[str, Any]) -> dict[str, Any]:
    definition = preset.get("definition") if isinstance(preset.get("definition"), dict) else {}
    workflow_id = str(definition.get("id") or preset.get("id") or "")
    inputs = [item for item in definition.get("inputs") or [] if isinstance(item, dict)]
    steps = [item for item in definition.get("steps") or [] if isinstance(item, dict)]
    outputs = [item for item in definition.get("outputs") or [] if isinstance(item, dict)]
    agent_steps = [item for item in steps if item.get("type") == "agent_task"]
    executable_steps = [
        item
        for item in steps
        if item.get("type") in {
            "agent_task",
            "file_ingest",
            "diff_parse",
            "coverage_parse",
            "semantic_retrieve",
            "memory_retrieve",
            "local_scope_discover",
            "local_source_flow_sfmea_blackbox",
            "local_resource_leak_hunt",
            "local_patch_impact_review",
            "local_mr_blackbox_test",
            "evidence_validate",
            "report_render",
            "artifact_export",
        }
    ]
    builtin_steps = [
        str(item.get("id") or "")
        for item in steps
        if item.get("type") != "agent_task" and str(item.get("id") or "")
    ]
    required_artifacts = _unique_preserve_order(
        str(artifact)
        for step in steps
        for artifact in step.get("required_artifacts") or []
        if str(artifact).strip()
    )
    missing: list[dict[str, str]] = []
    if not inputs:
        missing.append({"field": "inputs", "reason": "no inputs declared"})
    if not executable_steps:
        missing.append({"field": "steps", "reason": "no executable step declared"})
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
        str(item.get("resolver") or "") == "agent_mcp" and bool(item.get("required", False))
        for item in inputs
    )
    return {
        "id": workflow_id,
        "name": str(definition.get("name") or preset.get("name") or workflow_id),
        "scenario": _core_workflow_scenario(workflow_id),
        "status": "ready" if not missing else "incomplete",
        "description": str(preset.get("description") or ""),
        "execution_subject": str(definition.get("execution_subject") or ""),
        "execution_label": str(definition.get("execution_label") or ""),
        "user_message": str(definition.get("user_message") or ""),
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
    startup_probe: dict[str, Any] | None,
) -> dict[str, Any]:
    try:
        return _run_provider_task_probe_core(
            provider=provider,
            repo_path=repo_path,
            timeout_sec=timeout_sec,
            startup_probe=startup_probe,
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
    startup_probe: dict[str, Any] | None = None,
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
    startup = startup_probe if isinstance(startup_probe, dict) else {}
    _write_json(
        Path(task_run.artifact_dir) / "provider_live_readiness.json",
        {
            "schema_version": "provider-live-readiness-v1",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "checks": [{
                "provider": provider,
                "runtime_id": provider,
                "success": bool(startup.get("healthy")),
                "message": str(startup.get("message") or "启动探测未返回结果。"),
            }],
            "probe_kind": "deployment_task_contract",
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
        "task_run": _public_task_run_payload(refreshed),
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
    result = _redact_public_repo_paths(result, str(repo))
    artifact_path = task_dir / "provider_task_probe_result.json"
    result["artifact"] = {"path": _public_task_artifact_path(task_dir, artifact_path)}
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
        if not isinstance(provider, dict):
            continue
        provider_id = str(provider.get("provider") or provider.get("tool") or "")
        if not provider_id:
            continue
        health = provider.get("health") if isinstance(provider.get("health"), dict) else {}
        attempts = health.get("attempts") if isinstance(health.get("attempts"), list) else []
        evidence_ids.append(store.upsert_evidence_item(
            run_id=run_id,
            workspace_id=workspace_id,
            kind="provider_startup_probe",
            subject_key=f"{provider_id}:startup_probe",
            status="accepted" if provider.get("healthy") else "rejected",
            source="deployment_probe",
            path=str(artifact.get("path") or ""),
            symbol=provider_id,
            reason=(
                f"provider_startup_probe {provider_id} {provider.get('status')}; "
                f"{provider.get('message') or health.get('reason') or 'no message'}"
            ),
            confidence=1.0 if provider.get("healthy") else 0.2,
            text=json.dumps(
                {
                    "provider_startup_probe": provider_id,
                    "healthy": provider.get("healthy"),
                    "status": provider.get("status"),
                    "message": provider.get("message"),
                    "health_status": health.get("status"),
                    "health_reason": health.get("reason"),
                    "launch_kind": health.get("launch_kind"),
                    "used_fallback": health.get("used_fallback"),
                    "attempt_count": len(attempts),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            provenance={
                "provider": provider_id,
                "probe_id": probe_id,
                "startup_probe_status": provider.get("status"),
                "healthy": provider.get("healthy"),
                "message": provider.get("message"),
                "health": health,
                "artifact_path": str(artifact.get("path") or ""),
            },
        ))
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
            "    'kind': 'synthetic_smoke',",
            "    'source': 'codetalk-smoke-agent',",
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
        "source_flow_sfmea_blackbox": "source_flow_sfmea_blackbox",
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
    public_latest_path = _public_workbench_artifact_path(latest_path)
    latest = _read_json(latest_path)
    if not isinstance(latest, dict):
        return _system_audit_check(
            check_id="latest_deployment_task_probe",
            ok=False,
            severity="recommended",
            description="Latest deployment probe includes task contract evidence",
            details={
                "artifact_path": public_latest_path,
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
            "artifact_path": public_latest_path,
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


def _has_degraded_runtime_readiness(checks: list[dict[str, Any]]) -> bool:
    runtime_check_ids = {
        "agent_cli_launch_readiness",
        "codetalk_index_provider_readiness",
        "latest_deployment_task_probe",
    }
    for item in checks:
        if item.get("id") in runtime_check_ids and item.get("status") != "ok":
            return True
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
        "artifact_dir": _public_task_artifact_path(Path(str(task_run.artifact_dir)), artifact_dir),
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
            path=_public_task_artifact_path(Path(str(task_run.artifact_dir)), path),
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
        path = _workflow_output_artifact_path(task_run, output)
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


def _workflow_output_artifact_path(task_run: Any, output: dict[str, Any]) -> Path:
    raw = str(output.get("path") or "").strip()
    if not raw:
        source_step = _safe_artifact_segment(str(output.get("from") or ""))
        artifact = str(output.get("artifact") or "").strip()
        raw = f"{source_step}/{artifact}" if source_step and artifact else artifact
    path = Path(raw)
    if path.is_absolute():
        return path
    return Path(str(task_run.artifact_dir)) / path


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
    materialized_evidence = _workflow_output_materialized_evidence_summary(
        result.get("evidence_ids") or [],
    )
    rejected_outputs = list(result.get("rejected_outputs") or [])
    materialization_audit = (
        result.get("materialization_audit")
        if isinstance(result.get("materialization_audit"), dict)
        else _workflow_output_materialization_audit(
            task_run=task_run,
            workflow_outputs=workflow_outputs,
            materialized_evidence=materialized_evidence,
            rejected_outputs=rejected_outputs,
        )
    )
    payload = {
        "task_run_id": task_run.task_run_id,
        "workflow_id": task_run.workflow_id,
        "workspace_id": task_run.workspace_id,
        "repo_path": task_run.repo_path,
        "status": result.get("status"),
        "reason": str(result.get("reason") or ""),
        "evidence_count": result.get("evidence_count", 0),
        "evidence_ids": list(result.get("evidence_ids") or []),
        "materialized_evidence": materialized_evidence,
        "rejected_outputs": rejected_outputs,
        "test_activity_quality": (
            result.get("test_activity_quality")
            if isinstance(result.get("test_activity_quality"), dict)
            else {}
        ),
        "materialization_audit": materialization_audit,
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


def _attach_workflow_output_materialization_audit(
    *,
    task_run: Any,
    workflow_outputs: dict[str, Any],
    result: dict[str, Any],
) -> None:
    materialized_evidence = _workflow_output_materialized_evidence_summary(
        result.get("evidence_ids") or [],
    )
    result["materialized_evidence"] = materialized_evidence
    result["materialization_audit"] = _workflow_output_materialization_audit(
        task_run=task_run,
        workflow_outputs=workflow_outputs,
        materialized_evidence=materialized_evidence,
        rejected_outputs=list(result.get("rejected_outputs") or []),
    )


def _workflow_output_materialization_audit(
    *,
    task_run: Any,
    workflow_outputs: dict[str, Any],
    materialized_evidence: list[dict[str, Any]],
    rejected_outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    workflow_snapshot = getattr(task_run, "workflow_snapshot", {}) or {}
    declared_outputs = [
        item for item in workflow_snapshot.get("outputs") or []
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    produced_by_id = {
        str(item.get("id") or "").strip(): item
        for item in workflow_outputs.get("outputs") or []
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    materialized_by_output: dict[str, list[dict[str, Any]]] = {}
    for item in materialized_evidence:
        output_id = str(item.get("output_id") or "").strip()
        if output_id:
            materialized_by_output.setdefault(output_id, []).append(item)
    rejected_by_output: dict[str, list[dict[str, Any]]] = {}
    for item in rejected_outputs:
        output_id = str(item.get("output") or "").strip()
        if output_id:
            rejected_by_output.setdefault(output_id, []).append(item)

    rows: list[dict[str, Any]] = []
    for definition in declared_outputs:
        output_id = str(definition.get("id") or "").strip()
        produced = produced_by_id.get(output_id) or {}
        materialized_items = materialized_by_output.get(output_id, [])
        rejected_items = rejected_by_output.get(output_id, [])
        evidence_memory = definition.get("evidence_memory")
        row: dict[str, Any] = {
            "output_id": output_id,
            "declared_type": str(definition.get("type") or ""),
            "from": str(definition.get("from") or ""),
            "artifact": str(definition.get("artifact") or ""),
            "produced_status": str(produced.get("status") or ""),
            "evidence_memory_declared": _custom_evidence_mapping_enabled(evidence_memory),
            "materialized_count": len(materialized_items),
            "materialized_evidence_ids": [
                str(item.get("evidence_id") or "")
                for item in materialized_items
                if str(item.get("evidence_id") or "")
            ],
            "rejected_count": len(rejected_items),
            "rejection_reasons": _semantic_dedupe([
                str(item.get("reason") or "")
                for item in rejected_items
                if str(item.get("reason") or "")
            ]),
        }
        if isinstance(evidence_memory, dict):
            row["evidence_memory_mapping"] = _workflow_materialization_json_safe(evidence_memory)
        elif evidence_memory is True:
            row["evidence_memory_mapping"] = {"enabled": True}
        row["materialization_status"] = _workflow_output_materialization_status(
            produced=produced,
            materialized_count=len(materialized_items),
            rejected_count=len(rejected_items),
        )
        rows.append(row)

    return {
        "summary": {
            "declared_output_count": len(declared_outputs),
            "produced_output_count": len(produced_by_id),
            "evidence_memory_declared_count": sum(
                1
                for item in declared_outputs
                if _custom_evidence_mapping_enabled(item.get("evidence_memory"))
            ),
            "materialized_output_count": sum(
                1 for output_id in {str(item.get("output_id") or "") for item in materialized_evidence}
                if output_id
            ),
            "rejected_output_count": sum(1 for output_id in rejected_by_output if output_id),
            "materialized_evidence_count": len(materialized_evidence),
            "rejected_item_count": len(rejected_outputs),
        },
        "outputs": rows,
    }


def _workflow_output_materialization_status(
    *,
    produced: dict[str, Any],
    materialized_count: int,
    rejected_count: int,
) -> str:
    if materialized_count and rejected_count:
        return "partial"
    if materialized_count:
        return "accepted"
    if rejected_count:
        return "rejected"
    if not produced:
        return "not_produced"
    status = str(produced.get("status") or "")
    if status and status != "ok":
        return "output_not_ok"
    return "no_evidence"


def _workflow_materialization_json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(key): _workflow_materialization_json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_workflow_materialization_json_safe(item) for item in value]
        return str(value)


def _workflow_output_materialized_evidence_summary(evidence_ids: Any) -> list[dict[str, Any]]:
    ids = (
        [str(item).strip() for item in evidence_ids if str(item).strip()]
        if isinstance(evidence_ids, list)
        else []
    )
    if not ids:
        return []
    try:
        items = _memory_store().list_evidence_items_by_ids(ids)
    except Exception:
        return []
    summary: list[dict[str, Any]] = []
    for item in items:
        provenance = item.provenance or {}
        mapping = provenance.get("evidence_memory_mapping")
        mapping_payload = mapping if isinstance(mapping, dict) else {}
        summary.append({
            "evidence_id": item.evidence_id,
            "kind": item.kind,
            "subject_key": item.subject_key,
            "status": item.status,
            "source": item.source,
            "path": item.path,
            "symbol": item.symbol,
            "output_id": str(provenance.get("output_id") or ""),
            "source_step_id": str(provenance.get("source_step_id") or ""),
            "mapping_kind": str(mapping_payload.get("kind") or ""),
        })
    return summary


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
    path = _workflow_output_artifact_path(task_run, output)
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
        "source_step_id": str(output.get("from") or ""),
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


_SOURCE_EXTENSIONS = {
    ".c", ".h", ".cc", ".cpp", ".hpp", ".py", ".go", ".rs", ".java",
    ".ts", ".tsx", ".js", ".jsx", ".sh", ".json",
}


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


def _public_artifact_manifest(task_dir: Path) -> list[dict[str, Any]]:
    artifacts = _artifact_manifest(task_dir)
    for artifact in artifacts:
        relative_path = str(artifact.get("relative_path") or "")
        if relative_path:
            artifact["path"] = relative_path
    return artifacts


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
        ("input_materials", "input_materials.json", "hashed input material read contract"),
        ("workflow_snapshot", "workflow_snapshot.json", "frozen workflow definition"),
        ("workflow_contract", "workflow_contract.json", "workflow input/output contract"),
        ("task_bundle", "task_bundle.json", "CodeTalk-to-Agent handoff bundle"),
        ("agent_instructions", "agent_instructions.json", "repo-local Agent instructions"),
        ("provider_snapshot", "provider_snapshot.json", "provider capability ownership matrix"),
        ("provider_readiness", "provider_readiness.json", "provider readiness diagnostics"),
        (
            "provider_live_readiness",
            "provider_live_readiness.json",
            "runtime readiness probe recorded before execution",
        ),
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
    checks.append(_acceptance_input_materials_check(
        task_dir=task_dir,
        input_snapshot=task_run.input_snapshot,
    ))
    agent_instruction_policy_expected = _expected_agent_instruction_policy(task_dir)
    provider_readiness = _read_json(task_dir / "provider_readiness.json")
    checks.extend(_acceptance_provider_readiness_checks(provider_readiness))
    live_readiness = _read_json(task_dir / "provider_live_readiness.json")
    checks.extend(_acceptance_live_provider_readiness_checks(live_readiness))

    execution_payload = _read_json(task_dir / "workflow_execution.json")
    workflow_execution_exists = "workflow_execution.json" in artifacts
    workflow_execution_check = _acceptance_file_check(
        check_id="workflow_execution",
        relative_path="workflow_execution.json",
        artifacts=artifacts,
        description="workflow execution result and audit summary",
        severity="required",
        missing_reason="workflow_not_executed_or_execution_artifact_missing",
    )
    if workflow_execution_exists and not isinstance(execution_payload, dict):
        workflow_execution_check.update({
            "status": "invalid",
            "reason": "workflow_execution_invalid_json",
        })
    checks.append(workflow_execution_check)
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
        provider = str(agent_run.get("provider") or "")
        is_builtin_llm_run = provider == BUILTIN_LLM_PROVIDER_ID
        base = f"agent_runs/{step_id}"
        required_agent_artifacts = [
            ("agent_run.json", "Agent run envelope and session policy"),
            ("task_bundle.json", "per-step Agent task bundle"),
            ("workflow_snapshot.json", "per-step workflow snapshot"),
            ("agent_output_contract.json", "per-step Agent output contract"),
            ("execution_result.json", "Agent process result"),
            ("raw_output.txt", "redacted Agent stdout/stderr"),
            ("agent_run_lifecycle.json", "Agent run lifecycle and validation summary"),
        ]
        if is_builtin_llm_run:
            required_agent_artifacts.append((
                "builtin_llm_execution_input.json",
                "built-in LLM prompt and execution contract",
            ))
        else:
            required_agent_artifacts.extend([
                ("execution_input.json", "actual Agent stdin and launch envelope"),
                ("agent_replay_plan.json", "Agent replay plan and audit hashes"),
                ("provider_diagnostics.json", "provider launch/readiness diagnostics"),
            ])
        for suffix, description in required_agent_artifacts:
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
        if agent_instruction_policy_expected and not is_builtin_llm_run:
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
        if not is_builtin_llm_run:
            checks.append(_acceptance_agent_stdin_redaction_check(
                check_id=f"agent_stdin_redaction:{step_id}:execution_input",
                relative_path=f"{base}/execution_input.json",
                task_dir=task_dir,
                description=f"Persisted Agent stdin is redacted for step {step_id}",
            ))
        for artifact_name in agent_run.get("required_artifacts") or []:
            artifact = str(artifact_name)
            checks.append(_acceptance_file_check(
                check_id=f"agent_required_artifact:{step_id}:{artifact}",
                relative_path=f"{base}/{artifact}",
                artifacts=artifacts,
                description=f"required Agent artifact for step {step_id}",
                severity="required",
            ))
            delivery_relative_path = _acceptance_delivery_artifact_path(
                task_dir,
                f"{base}/{artifact}",
            )
            if Path(artifact).name in {"black_box_cases.json", "test_cases.json"}:
                checks.append(_acceptance_black_box_case_quality_check(
                    check_id=f"black_box_case_quality:{step_id}:{Path(artifact).name}",
                    relative_path=delivery_relative_path,
                    task_dir=task_dir,
                    repo_path=str(task_run.repo_path or ""),
                    description=(
                        f"black-box case content quality for canonical delivery from step {step_id}"
                    ),
                ))
            if Path(artifact).name in {"risk_findings.json", "sfmea.json"}:
                checks.append(_acceptance_risk_finding_quality_check(
                    check_id=f"risk_finding_quality:{step_id}:{Path(artifact).name}",
                    relative_path=delivery_relative_path,
                    task_dir=task_dir,
                    repo_path=str(task_run.repo_path or ""),
                    description=(
                        f"SFMEA risk finding content quality for canonical delivery from step {step_id}"
                    ),
                ))
        lifecycle = _read_json(task_dir / base / "agent_run_lifecycle.json")
        turn_count = _safe_int(
            lifecycle.get("turn_count") if isinstance(lifecycle, dict) else None
        )
        failure_recovery_expected = bool(
            isinstance(lifecycle, dict) and lifecycle.get("failure_recovery_artifact")
        ) or f"{base}/failure_recovery.json" in artifacts
        if failure_recovery_expected:
            checks.append(_acceptance_file_check(
                check_id=f"agent_failure_retry_context:{step_id}",
                relative_path=f"{base}/failure_retry_context.json",
                artifacts=artifacts,
                description="structured retry context for failed Agent step",
                severity="required",
                missing_reason="failure_recovery_present_but_retry_context_missing",
            ))
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
            checks.append(_acceptance_agent_stdin_redaction_check(
                check_id=(
                    f"agent_turn_stdin_redaction:{step_id}:"
                    f"turn_{turn_index}:execution_input"
                ),
                relative_path=f"{turn_base}/execution_input.json",
                task_dir=task_dir,
                description=(
                    f"Persisted Agent stdin is redacted for step {step_id} "
                    f"turn {turn_index}"
                ),
            ))
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


def _acceptance_delivery_artifact_path(task_dir: Path, relative_path: str) -> str:
    """Resolve structured quality checks to the user-facing canonical copy.

    Files below ``agent_runs`` are immutable provider diagnostics.  For a
    declared structured delivery that has been deterministically normalized at
    task root, acceptance must inspect that same root file the user downloads;
    otherwise a stale provider draft can veto a corrected delivery.
    """
    name = Path(relative_path).name
    if name in {"sfmea.json", "risk_findings.json", "black_box_cases.json", "test_cases.json"}:
        canonical = task_dir / name
        if canonical.is_file():
            return name
    return relative_path


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
    checks: list[dict[str, Any]] = []
    codetalk_providers = payload.get("codetalk_providers")
    if isinstance(codetalk_providers, dict):
        for provider, item in sorted(codetalk_providers.items()):
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "unknown")
            ok = status in {"available", "configured", "workflow_callable"}
            checks.append({
                "id": f"provider_readiness_codetalk:{provider}",
                "status": "ok" if ok else "missing",
                "severity": "recommended",
                "relative_path": "provider_readiness.json",
                "kind": "provider_readiness",
                "provider": str(provider),
                "owner": str(item.get("owner") or "codetalk"),
                "provider_status": status,
                "codetalk_callable": bool(item.get("codetalk_callable", False)),
                "non_blocking": bool(item.get("non_blocking", True)),
                "startup_probe_endpoint": str(item.get("startup_probe_endpoint") or ""),
                "health_endpoint": str(item.get("health_endpoint") or ""),
                "next_check": str(item.get("next_check") or ""),
                "description": "CodeTalk provider readiness for this task",
                "reason": "" if ok else str(item.get("unavailable_behavior") or status),
            })
    agent_cli_providers = payload.get("agent_cli_providers")
    if not isinstance(agent_cli_providers, dict):
        return checks
    for provider, item in sorted(agent_cli_providers.items()):
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "unknown")
        ok = status in {"available", "configured", "workflow_callable"}
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


def _acceptance_live_provider_readiness_checks(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    checks = payload.get("checks")
    if not isinstance(checks, list):
        return []
    result: list[dict[str, Any]] = []
    for item in checks:
        if not isinstance(item, dict):
            continue
        success = bool(item.get("success"))
        provider = str(item.get("provider") or "Agent")
        result.append({
            "id": f"provider_live_readiness:{provider}",
            "status": "ok" if success else "blocked",
            "severity": "required",
            "relative_path": "provider_live_readiness.json",
            "kind": "provider_live_readiness",
            "provider": provider,
            "runtime_id": str(item.get("runtime_id") or ""),
            "description": "live managed Agent readiness before workflow execution",
            "reason": "" if success else str(item.get("message") or "agent_runtime_unready"),
        })
    return result


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


def _acceptance_input_materials_check(
    *,
    task_dir: Path,
    input_snapshot: dict[str, Any],
) -> dict[str, Any]:
    expected_ids = _expected_input_material_ids(input_snapshot)
    payload = _read_json(task_dir / "input_materials.json")
    base = {
        "id": "input_materials_contract",
        "severity": "required",
        "relative_path": "input_materials.json",
        "kind": "input_materials",
        "description": "hashed file input material contract passed to Agent runs",
        "expected_material_count": len(expected_ids),
        "expected_material_ids": expected_ids,
    }
    if not isinstance(payload, dict):
        return {
            **base,
            "status": "missing",
            "reason": "artifact_json_unreadable",
        }
    material_ids = [
        str(item.get("input_id") or "")
        for item in payload.get("materials") or []
        if isinstance(item, dict)
    ]
    missing_ids = [item for item in expected_ids if item not in material_ids]
    material_count = _safe_int(payload.get("material_count"))
    if material_count != len(material_ids):
        return {
            **base,
            "status": "missing",
            "reason": "material_count_mismatch",
            "material_count": material_count,
            "actual_material_ids": material_ids,
        }
    if missing_ids:
        return {
            **base,
            "status": "missing",
            "reason": "material_ids_missing",
            "material_count": material_count,
            "actual_material_ids": material_ids,
            "missing_material_ids": missing_ids,
        }
    rules = payload.get("rules") if isinstance(payload.get("rules"), dict) else {}
    if expected_ids and rules.get("agent_must_read_materials") is not True:
        return {
            **base,
            "status": "missing",
            "reason": "agent_must_read_materials_rule_missing",
            "material_count": material_count,
            "actual_material_ids": material_ids,
        }
    if rules.get("materials_are_source_truth") is not False:
        return {
            **base,
            "status": "missing",
            "reason": "materials_source_truth_boundary_missing",
            "material_count": material_count,
            "actual_material_ids": material_ids,
        }
    return {
        **base,
        "status": "ok",
        "reason": "",
        "material_count": material_count,
        "actual_material_ids": material_ids,
    }


def _expected_input_material_ids(input_snapshot: dict[str, Any]) -> list[str]:
    material_ids: list[str] = []
    for input_id, value in (input_snapshot or {}).items():
        if not isinstance(value, dict):
            continue
        kind = str(value.get("kind") or "")
        if kind == "file":
            material_ids.append(str(input_id))
        elif kind == "file_set":
            for index, item in enumerate(value.get("files") or []):
                if not isinstance(item, dict):
                    continue
                material_ids.append(str(item.get("input_id") or f"{input_id}_{index + 1}"))
    return material_ids


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


def _acceptance_agent_stdin_redaction_check(
    *,
    check_id: str,
    relative_path: str,
    task_dir: Path,
    description: str,
) -> dict[str, Any]:
    payload = _read_json(task_dir / relative_path)
    base = {
        "id": check_id,
        "severity": "required",
        "relative_path": relative_path,
        "kind": workbench_artifact_kind(relative_path),
        "description": description,
    }
    if not isinstance(payload, dict):
        return {
            **base,
            "status": "missing",
            "reason": "artifact_json_unreadable",
        }
    stdin_sha = str(payload.get("stdin_json_sha256") or "")
    if payload.get("stdin_redacted") is not True:
        return {
            **base,
            "status": "missing",
            "reason": "stdin_redacted_flag_missing",
            "stdin_json_sha256": stdin_sha,
        }
    if not stdin_sha:
        return {
            **base,
            "status": "missing",
            "reason": "stdin_json_sha256_missing",
        }
    return {
        **base,
        "status": "ok",
        "reason": "",
        "stdin_redacted": True,
        "stdin_json_sha256": stdin_sha,
    }


def _acceptance_black_box_case_quality_check(
    *,
    check_id: str,
    relative_path: str,
    task_dir: Path,
    repo_path: str = "",
    description: str,
) -> dict[str, Any]:
    payload = _read_json(task_dir / relative_path)
    base = {
        "id": check_id,
        "severity": "required",
        "relative_path": relative_path,
        "kind": workbench_artifact_kind(relative_path),
        "description": description,
    }
    raw_cases = payload.get("black_box_cases") if isinstance(payload, dict) else payload
    if not isinstance(raw_cases, list):
        return {
            **base,
            "status": "invalid",
            "reason": "black_box_cases_must_be_list",
            "case_count": 0,
            "invalid_count": 0,
            "invalid_cases": [],
        }
    invalid_cases: list[dict[str, Any]] = []
    seen_case_keys: set[str] = set()
    for index, case in enumerate(raw_cases, start=1):
        if not isinstance(case, dict):
            invalid_cases.append({
                "index": index,
                "case_id": "",
                "title": "",
                "reasons": ["case_must_be_object"],
            })
            continue
        reasons = _black_box_case_quality_reasons(case, repo_path=repo_path)
        duplicate_key = _black_box_case_duplicate_key(case)
        if duplicate_key:
            if duplicate_key in seen_case_keys:
                reasons.append("duplicate_black_box_case")
            else:
                seen_case_keys.add(duplicate_key)
        if reasons:
            invalid_cases.append({
                "index": index,
                "case_id": str(case.get("case_id") or ""),
                "title": str(
                    case.get("title")
                    or case.get("scenario")
                    or case.get("scenario_name")
                    or ""
                ),
                "reasons": reasons,
            })
    if invalid_cases:
        return {
            **base,
            "status": "invalid",
            "reason": "black_box_case_quality_failed",
            "case_count": len(raw_cases),
            "invalid_count": len(invalid_cases),
            "invalid_cases": invalid_cases,
        }
    return {
        **base,
        "status": "ok",
        "reason": "",
        "case_count": len(raw_cases),
        "invalid_count": 0,
        "invalid_cases": [],
    }


_BLACK_BOX_WHITE_BOX_RE = re.compile(
    r"(?i)("
    r"\b(?:invoke|call|mock|stub|patch|unit\s*test|internal\s+function|"
    r"direct\s+function|private\s+function|return\s+value)\b|"
    r"\b[a-z0-9_./-]+\.(?:c|cc|cpp|cxx|h|hpp):\d+\b|"
    r"\b[a-z_][a-z0-9_]*->[a-z_][a-z0-9_]*\b|"
    r"\b[a-z_][a-z0-9_]*::[a-z_][a-z0-9_]*\b|"
    r"调用\s*(?:内部|私有)?\s*(?:函数|方法)|"
    r"(?:调用|直接调用)\s*(?:libnvmf|libnvme)[a-z0-9_]*\b|"
    r"(?:内部|私有)(?:函数|方法|变量|状态|字段|调用栈)|"
    r"返回值|调用栈|"
    r"修改[^，。；;\n]*?(?:变量|状态|字段)|"
    r"进入[^，。；;\n]*?:\d+[^，。；;\n]*?分支"
    r")"
)
_BLACK_BOX_FUNCTION_CALL_RE = re.compile(r"(?i)\b[a-z_][a-z0-9_]*_[a-z0-9_]+\s*\(")
_BLACK_BOX_EXTERNAL_COMMAND_CONTEXT_RE = re.compile(
    r"(?i)\b(?:rpc|cli|command|api)\b|(?:RPC|CLI|命令|接口)"
)
_BLACK_BOX_EXTERNAL_PROTOCOL_FIELD_CONTEXT_RE = re.compile(
    r"(?i)\b(?:pdu|packet|frame|request|response|header|digest|opcode|flag)\b|"
    r"(?:报文|数据包|请求|响应|协议|包头|摘要|标志位)"
)
def _black_box_case_duplicate_key(case: dict[str, Any]) -> str:
    parts = [
        _black_box_case_test_directory(case),
        " ".join(_semantic_string_list(case.get("preconditions") or case.get("precondition") or case.get("setup"))),
        " ".join(_semantic_string_list(case.get("steps"))),
        " ".join(_black_box_case_expected(case)),
        " ".join(_semantic_string_list(
            case.get("observability")
            or case.get("observation_points")
            or case.get("observed_outputs")
            or case.get("metrics")
            or case.get("logs")
        )),
        " ".join(_semantic_string_list(
            case.get("diagnostics")
            or case.get("failure_diagnostics")
            or case.get("failure_diagnosis")
            or case.get("triage")
            or case.get("debug_hints")
        )),
    ]
    # Keep Unicode word characters. Test cases may be written in Chinese, and
    # an ASCII-only scrub would collapse otherwise distinct cases.
    normalized = [
        re.sub(r"\s+", " ", re.sub(r"[^\w/]+", " ", str(part).lower())).strip()
        for part in parts
    ]
    if not any(normalized):
        return ""
    return "|".join(normalized)


def _black_box_case_test_directory(case: dict[str, Any]) -> str:
    return str(
        case.get("suggested_spdk_test_dir")
        or case.get("suggested_test_directory")
        or case.get("test_directory")
        or case.get("test_dir")
        or case.get("mapped_test_dir")
        or ""
    ).strip()


def _black_box_case_quality_reasons(
    case: dict[str, Any],
    *,
    repo_path: str = "",
) -> list[str]:
    reasons: list[str] = []
    steps = _semantic_string_list(case.get("steps"))
    expected = _black_box_case_expected(case)
    preconditions = _semantic_string_list(
        case.get("preconditions")
        or case.get("precondition")
        or case.get("setup")
    )
    observability = _semantic_string_list(
        case.get("observability")
        or case.get("observation_points")
        or case.get("observed_outputs")
        or case.get("metrics")
        or case.get("logs")
    )
    diagnostics = _semantic_string_list(
        case.get("diagnostics")
        or case.get("failure_diagnostics")
        or case.get("failure_diagnosis")
        or case.get("triage")
        or case.get("debug_hints")
    )
    test_directory = _black_box_case_test_directory(case)
    if not steps:
        reasons.append("missing_steps")
    elif not _black_box_steps_are_actionable(steps):
        reasons.append("vague_steps")
    if not expected:
        reasons.append("missing_expected")
    elif not _black_box_expected_is_observable(expected):
        reasons.append("vague_expected_result")
    if not preconditions:
        reasons.append("missing_preconditions")
    if not observability:
        reasons.append("missing_observability")
    if not diagnostics:
        reasons.append("missing_diagnostics")
    reasons.extend(
        black_box_case_delivery_quality_gaps(case, repo_path=repo_path)
    )
    reasons.extend(black_box_oracle_basis_quality_gaps(case))
    return _semantic_dedupe(reasons)


def _is_explicit_unverified_mapping(value: str) -> bool:
    marker = "ai_suggested_unverified"
    normalized = str(value or "").strip()
    return normalized == marker or normalized.startswith((marker + ":", marker + "："))


def _is_verified_test_mapping(value: str, *, repo_path: str = "") -> bool:
    normalized = str(value or "").strip().replace("\\", "/")
    normalized = re.sub(r":L?\d+(?:-L?\d+)?$", "", normalized)
    relative = Path(normalized)
    if (
        not normalized
        or relative.is_absolute()
        or ".." in relative.parts
        or not any(
            part.lower() in {"test", "tests", "spec", "specs"}
            for part in relative.parts[:-1]
        )
    ):
        return False
    if relative.parts[0].lower() in {"test", "tests", "spec", "specs"}:
        return True
    if not repo_path:
        return relative.parts[0].lower() in {"test", "tests", "spec", "specs"}
    try:
        repo = Path(repo_path).expanduser().resolve()
        candidate = (repo / relative).resolve()
    except OSError:
        return False
    return (
        repo in candidate.parents
        and candidate.exists()
        and (candidate.is_file() or candidate.is_dir())
    )


def _black_box_component_has_white_box_boundary(value: str) -> bool:
    text = str(value or "")
    for match in _BLACK_BOX_WHITE_BOX_RE.finditer(text):
        token = match.group(0)
        if (
            token.startswith("修改")
            and _BLACK_BOX_EXTERNAL_PROTOCOL_FIELD_CONTEXT_RE.search(token)
            and not re.search(r"(?:内部|私有|变量|状态)", token)
        ):
            continue
        return True
    for match in _BLACK_BOX_FUNCTION_CALL_RE.finditer(text):
        context = text[max(0, match.start() - 80):min(len(text), match.end() + 80)]
        if not _BLACK_BOX_EXTERNAL_COMMAND_CONTEXT_RE.search(context):
            return True
    return False


def _reconcile_acceptance_quality(
    *,
    task_dir: Path,
    execution: dict[str, Any],
    acceptance: dict[str, Any],
) -> dict[str, Any]:
    """Make required acceptance checks the final delivery truth source."""
    missing_required = [
        dict(item)
        for item in acceptance.get("missing_required") or []
        if isinstance(item, dict)
    ]
    if not missing_required:
        return execution

    reconciled = dict(execution)
    if str(reconciled.get("status") or "") in {
        "completed",
        "completed_empty",
        "ok",
        "ready",
        "success",
    }:
        reconciled["quality_audit_base_status"] = str(
            reconciled.get("status") or "completed"
        )
        reconciled["status"] = "quality_blocked"

    quality = _read_json(task_dir / "test_activity_quality_audit.json")
    if not isinstance(quality, dict):
        quality = (
            dict(reconciled.get("test_activity_quality") or {})
            if isinstance(reconciled.get("test_activity_quality"), dict)
            else {}
        )
    existing_issues = [
        dict(item)
        for item in quality.get("issues") or []
        if isinstance(item, dict)
    ]
    existing_ids = {str(item.get("id") or "") for item in existing_issues}
    for item in missing_required:
        issue_id = f"acceptance:{item.get('id') or item.get('reason') or 'required'}"
        if issue_id in existing_ids:
            continue
        existing_issues.append({
            "id": issue_id,
            "type": str(item.get("reason") or "required_acceptance_failed"),
            "artifact": str(item.get("relative_path") or ""),
            "message": "必需验收项未通过，当前结果不能交付。",
        })
        existing_ids.add(issue_id)
    quality.update({
        "kind": "test_activity_quality_audit",
        "status": "needs_rework",
        "deliverable": False,
        "score": min(int(quality.get("score") or 0), 79),
        "issue_count": len(existing_issues),
        "issues": existing_issues,
        "recommendations": ["必需验收项未通过，请查看验收提醒并重跑对应交付件。"],
    })
    reconciled["test_activity_quality"] = quality
    _write_json(task_dir / "test_activity_quality_audit.json", quality)
    _write_json(task_dir / "workflow_execution.json", reconciled)
    return reconciled


def _promote_partial_execution_after_deliverable_quality(
    *,
    execution: dict[str, Any],
    quality: dict[str, Any],
) -> dict[str, Any]:
    """Close a recoverable staged partial once its final deliverable is accepted.

    A built-in model stage can intentionally retain a partial provider result
    while deterministic repair materializes valid artifacts.  Once the final
    quality audit accepts that artifact set, showing the whole task as failed
    is both inaccurate and blocks users from a valid delivery.  Time-budget
    exhaustion and real execution errors remain partial.
    """
    if (
        str(execution.get("status") or "") != "partial"
        or quality.get("deliverable") is not True
    ):
        return execution
    recovered_steps: list[str] = []
    for result in execution.get("step_results") or []:
        if not isinstance(result, dict) or str(result.get("status") or "") != "partial":
            continue
        run = result.get("execution") if isinstance(result.get("execution"), dict) else {}
        if (
            bool(run.get("timed_out"))
            or str(run.get("error") or "").strip()
            or str(run.get("reason") or "") == "workflow_deadline_exceeded"
        ):
            return execution
        validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
        if str(validation.get("status") or "") not in {"ok", "passed", "completed"}:
            return execution
        step_id = str(result.get("step_id") or result.get("node_id") or "").strip()
        if step_id:
            recovered_steps.append(step_id)
    if not recovered_steps:
        return execution
    promoted = dict(execution)
    promoted["status"] = "completed"
    promoted["execution_status"] = "completed"
    promoted["quality_repaired_to_deliverable"] = True
    promoted["recovered_partial_steps"] = recovered_steps
    return promoted


def _black_box_case_expected(case: dict[str, Any]) -> list[str]:
    return _semantic_string_list(
        case.get("expected")
        or case.get("expected_result")
        or case.get("expected_results")
    )


def _black_box_steps_are_actionable(steps: list[str]) -> bool:
    return black_box_steps_are_actionable(steps)


def _black_box_expected_is_observable(expected: list[str]) -> bool:
    return black_box_expected_result_is_observable(expected)


def _acceptance_risk_finding_quality_check(
    *,
    check_id: str,
    relative_path: str,
    task_dir: Path,
    repo_path: str,
    description: str,
) -> dict[str, Any]:
    payload = _read_json(task_dir / relative_path)
    base = {
        "id": check_id,
        "severity": "required",
        "relative_path": relative_path,
        "kind": workbench_artifact_kind(relative_path),
        "description": description,
    }
    raw_findings = payload.get("risk_findings") if isinstance(payload, dict) else payload
    if not isinstance(raw_findings, list):
        return {
            **base,
            "status": "invalid",
            "reason": "risk_findings_must_be_list",
            "finding_count": 0,
            "invalid_count": 0,
            "invalid_findings": [],
        }
    invalid_findings: list[dict[str, Any]] = []
    seen_finding_keys: set[str] = set()
    for index, finding in enumerate(raw_findings, start=1):
        if not isinstance(finding, dict):
            invalid_findings.append({
                "index": index,
                "finding_id": "",
                "summary": "",
                "reasons": ["finding_must_be_object"],
            })
            continue
        reasons = _risk_finding_quality_reasons(finding, repo_path=repo_path)
        duplicate_key = _risk_finding_duplicate_key(finding)
        if duplicate_key:
            if duplicate_key in seen_finding_keys:
                reasons.append("duplicate_risk_finding")
            else:
                seen_finding_keys.add(duplicate_key)
        if reasons:
            invalid_findings.append({
                "index": index,
                "finding_id": str(
                    finding.get("finding_id") or finding.get("sfmea_id") or ""
                ),
                "summary": str(finding.get("summary") or finding.get("failure_mode") or ""),
                "reasons": reasons,
            })
    if invalid_findings:
        return {
            **base,
            "status": "invalid",
            "reason": "risk_finding_quality_failed",
            "finding_count": len(raw_findings),
            "invalid_count": len(invalid_findings),
            "invalid_findings": invalid_findings,
        }
    return {
        **base,
        "status": "ok",
        "reason": "",
        "finding_count": len(raw_findings),
        "invalid_count": 0,
        "invalid_findings": [],
    }


_SFMEA_TEXT_FIELDS = (
    "failure_mode",
    "cause",
    "effect",
    "detection",
    "severity",
    "mitigation",
)
_SFMEA_SCORE_FIELDS = ("severity_score", "occurrence_score", "detection_score", "rpn")


def _risk_finding_duplicate_key(finding: dict[str, Any]) -> str:
    source_candidates = _risk_finding_source_candidates(finding)
    parts = [
        source_candidates[0] if source_candidates else "",
        str(finding.get("function") or finding.get("symbol") or ""),
        str(finding.get("failure_mode") or ""),
        str(finding.get("cause") or ""),
        str(finding.get("effect") or ""),
        str(finding.get("detection") or ""),
        str(finding.get("mitigation") or ""),
        str(_sfmea_score(finding, "severity_score")),
        str(_sfmea_score(finding, "occurrence_score")),
        str(_safe_int(finding.get("detection_score"))),
    ]
    # Keep Unicode word characters. An ASCII-only scrub turns distinct Chinese
    # SFMEA rows into the same empty key and falsely blocks delivery.
    normalized = [
        re.sub(r"\s+", " ", re.sub(r"[^\w/]+", " ", str(part).lower())).strip()
        for part in parts
    ]
    if not any(normalized):
        return ""
    return "|".join(normalized)


def _risk_finding_quality_reasons(finding: dict[str, Any], *, repo_path: str) -> list[str]:
    reasons: list[str] = []
    missing_text = [
        field for field in _SFMEA_TEXT_FIELDS
        if not str(finding.get(field) or "").strip()
    ]
    missing_scores = [field for field in _SFMEA_SCORE_FIELDS if _sfmea_score(finding, field) <= 0]
    if missing_text or missing_scores:
        reasons.append("missing_sfmea_fields")
    score_explanation = str(finding.get("score_explanation") or "").strip()
    if not score_explanation:
        reasons.append("missing_score_explanation")
    mitigation = str(finding.get("mitigation") or "").strip()
    if mitigation and not sfmea_mitigation_is_actionable(mitigation):
        reasons.append("non_actionable_mitigation")
    severity_score = _sfmea_score(finding, "severity_score")
    occurrence_score = _sfmea_score(finding, "occurrence_score")
    detection_score = _sfmea_score(finding, "detection_score")
    rpn = _safe_int(finding.get("rpn"))
    if any(score and not (1 <= score <= 10) for score in (severity_score, occurrence_score, detection_score)):
        reasons.append("sfmea_score_out_of_range")
    if severity_score and occurrence_score and detection_score and rpn:
        if rpn != severity_score * occurrence_score * detection_score:
            reasons.append("rpn_mismatch")
    source_candidates = _risk_finding_source_candidates(finding)
    if not source_candidates:
        reasons.append("source_file_required")
    else:
        resolved_sources = [
            _validated_repo_source_path(repo_path, candidate)
            for candidate in source_candidates
        ]
        if any(item is None for item in resolved_sources):
            reasons.append("source_file_missing")
        else:
            validated_sources = [item for item in resolved_sources if item is not None]
            if validated_sources and not _risk_finding_source_lines_valid(
                finding,
                validated_sources[0][1],
            ):
                reasons.append("source_line_out_of_range")
    return _semantic_dedupe(reasons)


def _sfmea_score(finding: dict[str, Any], field: str) -> int:
    canonical_fields = {
        "severity_score": "severity",
        "occurrence_score": "occurrence",
    }
    canonical = canonical_fields.get(field)
    if canonical and canonical in finding:
        canonical_value = finding.get(canonical)
        if not isinstance(canonical_value, bool) and re.fullmatch(
            r"-?\d+",
            str(canonical_value or "").strip(),
        ):
            return _safe_int(canonical_value)
    return _safe_int(finding.get(field))


def _risk_finding_source_candidates(finding: dict[str, Any]) -> list[str]:
    candidates = [
        str(finding.get("file_path") or "").strip(),
        str(finding.get("path") or "").strip(),
    ]
    raw_evidence = finding.get("source_evidence")
    evidence_items = raw_evidence if isinstance(raw_evidence, list) else [raw_evidence]
    for item in evidence_items:
        if isinstance(item, dict):
            candidate = str(item.get("file_path") or item.get("path") or "").strip()
        else:
            candidate = str(item or "").strip()
            if re.match(
                r"(?i)^(?:EV|SRC|TEST|FLOW)[-_][A-Za-z0-9_-]+(?::L?\d+(?:-L?\d+)?)?$",
                candidate,
            ):
                continue
            if "::" in candidate:
                candidate = candidate.split("::", 1)[0].strip()
            candidate = re.sub(r":\d+(?:-\d+)?$", "", candidate)
        if candidate:
            candidates.append(candidate)
    technical_claims = finding.get("technical_claims")
    claim_items = technical_claims if isinstance(technical_claims, list) else []
    for claim in claim_items:
        if not isinstance(claim, dict):
            continue
        claim_evidence = claim.get("evidence")
        claim_evidence_items = claim_evidence if isinstance(claim_evidence, list) else []
        for evidence in claim_evidence_items:
            if not isinstance(evidence, dict):
                continue
            candidate = str(evidence.get("path") or evidence.get("file_path") or "").strip()
            if candidate:
                candidates.append(candidate)
    if not any(candidates):
        candidates.append(str(finding.get("module") or "").strip())
    return _semantic_dedupe([item for item in candidates if item])


def _risk_finding_source_lines_valid(finding: dict[str, Any], source_path: Path) -> bool:
    start_line = _safe_int(
        finding.get("line_start")
        or finding.get("start_line")
        or finding.get("line")
        or finding.get("lineno")
    )
    end_line = _safe_int(finding.get("line_end") or finding.get("end_line"))
    if start_line <= 0 and end_line <= 0:
        return True
    try:
        line_count = len(source_path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return False
    if line_count <= 0:
        return False
    if start_line <= 0:
        start_line = end_line
    if end_line <= 0:
        end_line = start_line
    return 1 <= start_line <= end_line <= line_count


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
    is_text = path.suffix.lower() in TEXT_ARTIFACT_SUFFIXES
    content = ""
    truncated = False
    content_redacted = False
    if is_text:
        text = data.decode("utf-8", errors="replace")
        redacted = redact_agent_diagnostic_text(text)
        truncated = len(redacted) > max_chars
        content = truncate_redacted_text(redacted, max_chars)
        content_redacted = redacted != text
    return {
        "relative_path": relative_path,
        "path": relative_path,
        "kind": _artifact_kind(relative_path),
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "is_text": is_text,
        "truncated": truncated,
        "content_redacted": content_redacted,
        "content": content,
    }


def _ensure_task_rerun_plan(task_run: Any) -> dict[str, Any]:
    task_dir = Path(str(task_run.artifact_dir))
    path = task_dir / "task_rerun_plan.json"
    payload = _read_json(path)
    if isinstance(payload, dict):
        return payload

    payload = build_workflow_rerun_plan(
        task_run=task_run,
        status="prepared",
        step_results=[],
        outputs=[],
    )
    _write_json(path, payload)
    write_task_artifact_manifest(task_dir, task_run_id=task_run.task_run_id)
    return payload


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
        step_type = str(step.get("type") or "")
        if step_type != "agent_task":
            artifact_dir = task_dir / "steps" / _safe_segment(
                step_id or "step",
                "step_id",
            )
            step_validations.append({
                "step_id": step_id,
                "type": step_type,
                "status": "ready",
                "recommended_action": str(step.get("recommended_action") or ""),
                "failure_kind": str(step.get("failure_kind") or ""),
                "artifact_dir": str(artifact_dir),
                "artifact_dir_exists": artifact_dir.exists() and artifact_dir.is_dir(),
                "missing_artifacts": [
                    str(item) for item in step.get("missing_artifacts") or []
                ],
                "overwrite_risk_artifacts": [],
                "replay_artifacts": [],
            })
            continue
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
        replay_artifacts = _rerun_agent_replay_artifact_checks(
            artifact_dir=artifact_dir,
            artifact_dir_exists=artifact_dir_exists,
        )
        missing_replay_artifact = any(
            item.get("status") == "blocked" for item in replay_artifacts
        )
        status = (
            "ready"
            if artifact_dir_exists and not missing_replay_artifact
            else "blocked"
        )
        step_payload = {
            "step_id": step_id,
            "type": step_type,
            "status": status,
            "recommended_action": str(step.get("recommended_action") or ""),
            "failure_kind": str(step.get("failure_kind") or ""),
            "artifact_dir": str(artifact_dir),
            "artifact_dir_exists": artifact_dir_exists,
            "missing_artifacts": [str(item) for item in step.get("missing_artifacts") or []],
            "overwrite_risk_artifacts": overwrite_risk_artifacts,
            "replay_artifacts": replay_artifacts,
        }
        if not artifact_dir_exists:
            step_payload["reason"] = "agent step artifact directory is missing"
        elif missing_replay_artifact:
            step_payload["reason"] = "agent replay artifact is missing"
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


def _rerun_agent_replay_artifact_checks(
    *,
    artifact_dir: Path,
    artifact_dir_exists: bool,
) -> list[dict[str, Any]]:
    required = [
        "agent_run.json",
        "task_bundle.json",
        "workflow_snapshot.json",
        "agent_output_contract.json",
        "execution_input.json",
        "agent_replay_plan.json",
    ]
    checks: list[dict[str, Any]] = []
    for artifact in required:
        exists = bool(artifact_dir_exists and (artifact_dir / artifact).is_file())
        checks.append({
            "artifact": artifact,
            "status": "ok" if exists else "blocked",
            "path": str(artifact_dir / artifact),
            "reason": "" if exists else "required replay artifact is missing",
        })
    return checks


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
    rerun_id = f"{task_run_id}_rerun_{sequence}"
    relative_artifact_path = f"task_reruns/{rerun_id}/task_rerun_execution.json"
    persisted_result = {
        key: value for key, value in result.items()
        if key != "run_ui_summary"
    }
    payload = {
        **persisted_result,
        "rerun_id": rerun_id,
        "sequence": sequence,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "artifact": {
            "path": relative_artifact_path,
            "latest_alias": "task_rerun_execution.json",
        },
    }
    _write_json(task_dir / relative_artifact_path, payload)
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
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
