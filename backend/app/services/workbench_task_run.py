"""Prepare reproducible workbench task runs from workflow definitions."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import shutil
import subprocess
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.harness_facade import AgentHarnessFacade, HarnessRunRequest
from app.services.agent_runtimes import get_agent_runtime_sync
from app.services.evidence_memory import EvidenceMemoryStore
from app.services.external_agent_discovery import (
    check_provider_health,
    external_agent_provider_capabilities,
    external_agent_provider_spec,
    redact_agent_diagnostic_text,
    split_agent_command,
)
from app.services import legacy_workflow_execution as legacy_execution
from app.services.input_consumption import (
    build_input_consumption_ledger,
    scope_input_consumption_ledger,
)
from app.services.network_policy import IntranetNetworkPolicy
from app.services.test_semantic_library import TestSemanticLibraryStore
from app.services.workbench_artifact_manifest import write_task_artifact_manifest
from app.services.workbench_skills import resolve_workbench_skill_instructions
from app.services.workbench_input_ingest import (
    ingest_workbench_inputs,
    validate_workbench_inputs,
)
from app.services.workflow_dsl import WorkflowStore
from app.services.workflow_output_presets import selected_output_content_presets
from app.services.workbench_task_compile import (
    TaskConfigurationError,
    compiled_contract_version,
)

AGENT_RUNTIME_PROVIDER_PREFIX = "agent-runtime:"
_SENSITIVE_ENV_KEY_RE = re.compile(
    r"(?i)(api[-_]?key|access[-_]?key|token|secret|password|passwd|passphrase|credential|private[-_]?key|authorization|cookie)"
)
BUILTIN_LLM_PROVIDER_ID = "builtin-llm"
MANAGED_RUNTIME_ALIASES = {
    "claude-code": ("default-claude-code", "claude", "claude_print_arg"),
    "opencode": ("default-opencode", "opencode", "opencode_run_arg"),
    "codex": ("default-codex", "codex", "codex_exec_json"),
}
SOURCE_EXTENSIONS = frozenset({
    ".c", ".h", ".cc", ".cpp", ".hpp", ".py", ".go", ".rs", ".java",
    ".ts", ".tsx", ".js", ".jsx", ".sh", ".json",
})
SOURCE_SCAN_IGNORED_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", ".next", "dist", "build",
    "out", "target", "__pycache__", ".venv", "venv", "task_runs",
    "workbench", ".codetalk", ".codehub",
})
_GIT_SOURCE_FILE_CACHE: dict[tuple[str, str, int, tuple[str, ...]], tuple[str, ...]] = {}
_GIT_SOURCE_FILE_CACHE_LOCK = threading.Lock()
SAFE_RUNTIME_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_COMPATIBILITY_EXECUTION_PROFILES = (
    {
        "id": "rapid",
        "label": "速度型",
        "delivery_class": "bounded_analysis",
        "expected_duration_minutes": [8, 20],
        "max_subagents": 1,
    },
    {
        "id": "deep",
        "label": "深度型",
        "delivery_class": "full_test_delivery",
        "expected_duration_minutes": [40, 90],
        "max_subagents": 4,
    },
)
_DEEP_SOURCE_CONTEXT_BUDGET = {
    "limit": 24,
    "min_source_files": 12,
    "min_test_files": 0,
    "max_candidates_to_read": 240,
    "excerpt_radius": 80,
}
_DEEP_MODULE_EVIDENCE_HINTS = {
    "lib/bdev": [
        ("lib/bdev/bdev.c", "struct spdk_bdev_mgr", "bdev global manager state"),
        ("lib/bdev/bdev.c", "struct spdk_bdev_shared_resource", "shared channel retry resources"),
        ("lib/bdev/bdev.c", "enum bdev_io_retry_state", "NOMEM retry state model"),
        ("lib/bdev/bdev.c", "bdev_queue_nomem_io_head", "NOMEM retry queue head"),
        ("lib/bdev/bdev.c", "bdev_ch_retry_io", "NOMEM retry poller"),
        ("lib/bdev/bdev.c", "bdev_io_should_split", "I/O split decision"),
        ("lib/bdev/bdev.c", "bdev_io_split", "I/O split execution"),
        ("lib/bdev/bdev.c", "spdk_bdev_readv_blocks_ext", "public read path"),
        ("lib/bdev/bdev.c", "bdev_io_submit", "bdev I/O submit path"),
        ("lib/bdev/bdev.c", "bdev_io_complete", "bdev completion path"),
        ("lib/bdev/bdev.c", "spdk_bdev_open_ext", "public open path"),
        ("lib/bdev/bdev.c", "spdk_bdev_close", "descriptor close path"),
        ("lib/bdev/bdev.c", "spdk_bdev_register", "device register lifecycle"),
        ("lib/bdev/bdev.c", "spdk_bdev_unregister", "device unregister lifecycle"),
        ("lib/bdev/bdev.c", "spdk_bdev_module_claim_bdev", "module claim arbitration"),
        ("lib/bdev/bdev.c", "bdev_abort_queued_io", "queued I/O abort path"),
        ("lib/bdev/bdev.c", "spdk_bdev_abort", "public abort path"),
        ("lib/bdev/bdev.c", "spdk_bdev_reset", "public reset path"),
        ("lib/bdev/bdev.c", "spdk_bdev_quiesce", "module quiesce path"),
        ("lib/bdev/bdev.c", "bdev_lock_lba_range", "LBA lock range path"),
        ("lib/bdev/part.c", "spdk_bdev_part_submit_request", "partition submit remap"),
        ("lib/bdev/bdev_zone.c", "spdk_bdev_get_zone_info", "zoned namespace info path"),
    ],
    "lib/blob": [
        ("lib/blob/blobstore.h", "spdk_blob_state", "blob state model"),
        ("lib/blob/blobstore.h", "struct spdk_blob", "blob runtime state"),
        ("lib/blob/blobstore.h", "struct spdk_blob_store", "blobstore global state"),
        ("lib/blob/blobstore.h", "struct spdk_bs_channel", "blobstore channel resources"),
        ("lib/blob/blobstore.c", "spdk_bs_load", "load existing blobstore"),
        ("lib/blob/blobstore.c", "spdk_bs_init", "initialize new blobstore"),
        ("lib/blob/blobstore.c", "spdk_bs_unload", "unload blobstore"),
        ("lib/blob/blobstore.c", "spdk_bs_destroy", "destroy blobstore"),
        ("lib/blob/blobstore.c", "bs_create_blob", "create blob"),
        ("lib/blob/blobstore.c", "bs_open_blob", "open blob"),
        ("lib/blob/blobstore.c", "spdk_blob_close", "close blob"),
        ("lib/blob/blobstore.c", "bs_delete_blob", "delete blob"),
        ("lib/blob/blobstore.c", "blob_request_submit_op", "blob io split"),
        ("lib/blob/blobstore.c", "blob_request_submit_op_single", "single blob io"),
        ("lib/blob/blobstore.c", "bs_allocate_and_copy_cluster", "thin provisioning allocation"),
        ("lib/blob/blobstore.c", "blob_persist", "metadata persist"),
        ("lib/blob/blobstore.c", "blob_persist_complete", "metadata persist completion"),
        ("lib/blob/request.c", "bs_sequence_start", "request sequence start"),
        ("lib/blob/request.c", "bs_sequence_finish", "request sequence completion"),
        ("lib/blob/request.c", "bs_user_op_abort", "request abort"),
    ],
    "lib/nvme": [
        ("lib/nvme/nvme.c", "spdk_nvme_probe", "probe public entry"),
        ("lib/nvme/nvme.c", "spdk_nvme_connect", "connect public entry"),
        ("lib/nvme/nvme.c", "spdk_nvme_probe_poll_async", "async probe poll"),
        ("lib/nvme/nvme_transport.c", "nvme_transport_register", "transport registry"),
        ("lib/nvme/nvme_transport.c", "nvme_transport_ctrlr_connect_qpair", "transport qpair connect"),
        ("lib/nvme/nvme_internal.h", "enum nvme_ctrlr_state", "controller state model"),
        ("lib/nvme/nvme_internal.h", "enum nvme_qpair_state", "qpair state model"),
        ("lib/nvme/nvme_internal.h", "struct spdk_nvme_qpair", "qpair runtime state"),
        ("lib/nvme/nvme_internal.h", "struct spdk_nvme_ctrlr", "controller runtime state"),
        ("lib/nvme/nvme_ctrlr.c", "nvme_ctrlr_set_state", "controller state transition"),
        ("lib/nvme/nvme_ctrlr.c", "nvme_ctrlr_process_init", "controller init fsm"),
        ("lib/nvme/nvme_ctrlr.c", "spdk_nvme_ctrlr_alloc_io_qpair", "io qpair allocation"),
        ("lib/nvme/nvme_ctrlr.c", "spdk_nvme_ctrlr_free_io_qpair", "io qpair free"),
        ("lib/nvme/nvme_ctrlr.c", "nvme_ctrlr_reconnect_io_qpair", "qpair reconnect"),
        ("lib/nvme/nvme_ns_cmd.c", "_nvme_ns_cmd_rw", "namespace io split"),
        ("lib/nvme/nvme_qpair.c", "nvme_qpair_submit_request", "qpair submit"),
        ("lib/nvme/nvme_qpair.c", "nvme_qpair_manual_complete_request", "qpair completion"),
        ("lib/nvme/nvme_poll_group.c", "spdk_nvme_poll_group_process_completions", "poll group completions"),
        ("lib/nvme/nvme_pcie_common.c", "nvme_pcie_qpair_submit_tracker", "pcie submit"),
        ("lib/nvme/nvme_tcp.c", "nvme_tcp_qpair_submit_request", "tcp submit"),
        ("lib/nvme/nvme_rdma.c", "nvme_rdma_ctrlr_connect_qpair", "rdma connect"),
    ],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def resolve_execution_profile(
    workflow_snapshot: dict[str, Any],
    *,
    execution_profile_id: str = "",
) -> dict[str, Any]:
    """Resolve and freeze the run policy without mutating its workflow version."""
    profiles = [
        dict(item)
        for item in workflow_snapshot.get("execution_profiles") or []
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    if not profiles:
        profiles = [dict(item) for item in _COMPATIBILITY_EXECUTION_PROFILES]
    requested_id = str(
        execution_profile_id
        or workflow_snapshot.get("default_execution_profile")
        or profiles[0].get("id")
        or ""
    ).strip()
    selected = next(
        (item for item in profiles if str(item.get("id") or "").strip() == requested_id),
        None,
    )
    if selected is None:
        available = ", ".join(str(item.get("id")) for item in profiles)
        raise ValueError(f"执行档位不可用：{requested_id}（可选：{available}）")
    return json.loads(json.dumps(selected, ensure_ascii=False))


def _optional_positive_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _source_context_budget_for_step(
    step: dict[str, Any],
    *,
    execution_profile: dict[str, Any],
) -> dict[str, int]:
    profile_id = str(execution_profile.get("id") or "").strip().lower()
    raw_limit = _optional_positive_int(step.get("source_context_limit"))
    raw_min_source = _optional_positive_int(step.get("source_context_min_source_files"))
    raw_min_test = _optional_positive_int(step.get("source_context_min_test_files"))
    if profile_id == "deep":
        return {
            "limit": max(raw_limit or 12, _DEEP_SOURCE_CONTEXT_BUDGET["limit"]),
            "min_source_files": max(
                raw_min_source or 1,
                _DEEP_SOURCE_CONTEXT_BUDGET["min_source_files"],
            ),
            "min_test_files": (
                raw_min_test
                if raw_min_test is not None
                else _DEEP_SOURCE_CONTEXT_BUDGET["min_test_files"]
            ),
            "max_candidates_to_read": _DEEP_SOURCE_CONTEXT_BUDGET[
                "max_candidates_to_read"
            ],
            "excerpt_radius": _DEEP_SOURCE_CONTEXT_BUDGET["excerpt_radius"],
        }
    return {
        "limit": max(1, raw_limit or 12),
        "min_source_files": max(1, raw_min_source or 1),
        "min_test_files": max(0, raw_min_test if raw_min_test is not None else 2),
        "max_candidates_to_read": 80,
        "excerpt_radius": 4,
    }


def _deep_module_evidence_hints(
    *,
    execution_profile: dict[str, Any],
    query: str,
    search_roots: list[str] | None = None,
) -> list[dict[str, Any]]:
    if str(execution_profile.get("id") or "").strip().lower() != "deep":
        return []
    query_text = str(query or "").lower()
    roots = {str(value).strip().lower() for value in search_roots or [] if str(value).strip()}
    hints: list[dict[str, Any]] = []
    for module_root, module_hints in _DEEP_MODULE_EVIDENCE_HINTS.items():
        if module_root not in query_text and module_root not in roots:
            continue
        for path, term, label in module_hints:
            hints.append({
                "path": path,
                "term": term,
                "label": label,
                "contract_required": True,
                "allow_same_file": False,
            })
    return hints


@dataclass(frozen=True)
class PreparedWorkbenchTaskRun:
    task_run_id: str
    workflow_id: str
    workspace_id: str
    repo_path: str
    artifact_dir: str
    workflow_snapshot: dict[str, Any]
    input_snapshot: dict[str, Any]
    task_bundle: dict[str, Any]
    execution_profile: dict[str, Any] = field(default_factory=dict)
    task_id: str = ""
    attempt_number: int = 0
    parent_task_run_id: str = ""
    execution_status: str = "prepared"
    quality_status: str = "not_checked"
    artifact_validation_status: str = "not_started"
    governance_status: str = "not_requested"
    delivery_status: str = "none"
    started_at: str = ""
    completed_at: str = ""
    agent_runs: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=_now)


class WorkbenchTaskRunPreparer:
    """Freezes workflow/input state and creates Agent run envelopes."""

    def __init__(
        self,
        *,
        artifact_root: str | Path,
        workflow_store: WorkflowStore,
        evidence_memory: EvidenceMemoryStore | None = None,
        semantic_library: TestSemanticLibraryStore | None = None,
    ) -> None:
        self.artifact_root = Path(artifact_root)
        self.workflow_store = workflow_store
        self.evidence_memory = evidence_memory
        self.semantic_library = semantic_library

    @staticmethod
    def preflight_inputs(
        *, workflow_snapshot: dict[str, Any], inputs: dict[str, Any]
    ) -> None:
        """Reject deterministic input failures without creating run state."""
        try:
            compiled_contract_version(workflow_snapshot)
        except TaskConfigurationError as exc:
            raise ValueError(str(exc)) from exc
        validate_workbench_inputs(
            input_definitions=[
                item
                for item in workflow_snapshot.get("inputs") or []
                if isinstance(item, dict)
            ],
            inputs=dict(inputs or {}),
        )

    def prepare(
        self,
        *,
        workflow_id: str,
        workspace_id: str,
        repo_path: str,
        inputs: dict[str, Any],
        provider_override: str | None = None,
        task_id: str = "",
        attempt_number: int = 0,
        parent_task_run_id: str = "",
        execution_profile_id: str = "",
        workflow_snapshot_override: dict[str, Any] | None = None,
        task_context: dict[str, Any] | None = None,
    ) -> PreparedWorkbenchTaskRun:
        workflow_snapshot = (
            dict(workflow_snapshot_override)
            if isinstance(workflow_snapshot_override, dict)
            else self.workflow_store.freeze_workflow_snapshot(workflow_id)
        )
        try:
            contract_version = compiled_contract_version(workflow_snapshot)
        except TaskConfigurationError as exc:
            raise ValueError(str(exc)) from exc
        is_v3_contract = contract_version == 3
        execution_profile = resolve_execution_profile(
            workflow_snapshot,
            execution_profile_id=execution_profile_id,
        )
        network_policy = IntranetNetworkPolicy(
            policy_id=settings.intranet_network_policy_id,
            allowed_hosts=set(settings.intranet_allowed_hosts),
            allowed_cidrs=set(settings.intranet_allowed_cidrs),
        ).snapshot()
        stage_specs = [] if is_v3_contract else legacy_execution.default_test_activity_stage_specs(
            profile_id=str(execution_profile["id"])
        )
        artifact_contract_v3 = {} if is_v3_contract else legacy_execution.default_artifact_contract_v3(
            profile_id=str(execution_profile["id"])
        )
        has_agent_step = any(
            isinstance(step, dict) and step.get("type") == "agent_task"
            for step in workflow_snapshot.get("steps") or []
        )
        if provider_override and not has_agent_step:
            raise ValueError(
                "provider override requires an agent_task step; "
                "the selected workflow only contains built-in steps"
            )
        task_run_id = _new_id("task_run")
        artifact_dir = self.artifact_root / _safe_segment(task_run_id)
        self.preflight_inputs(
            workflow_snapshot=workflow_snapshot,
            inputs=inputs,
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)

        required_artifacts_by_step = {
            str(step.get("id")): [str(item) for item in step.get("required_artifacts") or []]
            for step in workflow_snapshot.get("steps") or []
            if isinstance(step, dict) and step.get("type") == "agent_task"
        }
        output_schemas_by_step = build_output_schemas_by_step(workflow_snapshot)
        semantic_import_outputs_by_step = build_semantic_import_outputs_by_step(
            workflow_snapshot
        )
        input_snapshot = ingest_workbench_inputs(
            input_definitions=[
                item for item in workflow_snapshot.get("inputs") or []
                if isinstance(item, dict)
            ],
            inputs=dict(inputs or {}),
            artifact_dir=artifact_dir,
        )
        input_consumption = build_input_consumption_ledger(
            input_snapshot=input_snapshot,
            input_definitions=[
                item for item in workflow_snapshot.get("inputs") or []
                if isinstance(item, dict)
            ],
            stage_specs=stage_specs,
        )
        task_context_payload = _task_context_payload(task_context)
        task_query_hints = _task_context_query_hints(task_context_payload)
        context_bundle = build_workbench_context_bundle(
            workspace_id=workspace_id,
            repo_path=repo_path,
            input_snapshot=input_snapshot,
            query_hints=task_query_hints,
            evidence_memory=self.evidence_memory,
            semantic_library=self.semantic_library,
        )
        source_context_memo: dict[tuple[Any, ...], dict[str, Any]] = {}

        def prepared_source_context(
            query: str,
            *,
            limit: int = 12,
            min_source_files: int = 1,
            min_test_files: int = 2,
            max_candidates_to_read: int = 80,
            excerpt_radius: int = 4,
            evidence_hints: list[dict[str, Any]] | None = None,
            search_roots: list[str] | None = None,
        ) -> dict[str, Any]:
            normalized_hints = tuple(
                (
                    str(item.get("path") or "").strip(),
                    str(item.get("term") or "").strip(),
                    str(item.get("label") or "").strip(),
                )
                for item in evidence_hints or []
                if isinstance(item, dict)
            )
            normalized_roots = tuple(
                str(value).strip() for value in search_roots or [] if str(value).strip()
            )
            key = (
                str(repo_path or ""),
                str(query or ""),
                limit,
                min_source_files,
                min_test_files,
                max_candidates_to_read,
                excerpt_radius,
                normalized_hints,
                normalized_roots,
            )
            if key not in source_context_memo:
                source_context_memo[key] = build_local_source_context(
                    repo_path=repo_path,
                    query=query,
                    limit=limit,
                    min_source_files=min_source_files,
                    min_test_files=min_test_files,
                    max_candidates_to_read=max_candidates_to_read,
                    excerpt_radius=excerpt_radius,
                    evidence_hints=evidence_hints,
                    search_roots=list(normalized_roots),
                )
            return json.loads(json.dumps(source_context_memo[key]))

        agent_source_steps = [
            step
            for step in workflow_snapshot.get("steps") or []
            if isinstance(step, dict) and step.get("type") == "agent_task"
        ]
        agent_source_budgets = [
            _source_context_budget_for_step(step, execution_profile=execution_profile)
            for step in agent_source_steps
        ]
        task_source_context_limit = max(
            [budget["limit"] for budget in agent_source_budgets] or [12]
        )
        task_source_context_min_test_files = max(
            [budget["min_test_files"] for budget in agent_source_budgets] or [2]
        )
        task_source_context_min_source_files = max(
            [budget["min_source_files"] for budget in agent_source_budgets] or [1]
        )
        task_source_context_max_candidates = max(
            [budget["max_candidates_to_read"] for budget in agent_source_budgets]
            or [80]
        )
        task_source_context_excerpt_radius = max(
            [budget["excerpt_radius"] for budget in agent_source_budgets] or [4]
        )
        task_source_evidence_hints = [
            dict(item)
            for step in agent_source_steps
            for item in step.get("source_evidence_hints") or []
            if isinstance(item, dict)
        ]
        task_source_search_roots = _unique_strings(
            str(value)
            for step in agent_source_steps
            for value in step.get("source_context_search_roots") or []
            if str(value).strip()
        )
        task_source_evidence_hints.extend(
            _deep_module_evidence_hints(
                execution_profile=execution_profile,
                query=str(context_bundle.get("query") or ""),
                search_roots=task_source_search_roots,
            )
        )
        local_source_context = prepared_source_context(
            str(context_bundle.get("query") or ""),
            limit=task_source_context_limit,
            min_source_files=task_source_context_min_source_files,
            min_test_files=task_source_context_min_test_files,
            max_candidates_to_read=task_source_context_max_candidates,
            excerpt_radius=task_source_context_excerpt_radius,
            evidence_hints=task_source_evidence_hints,
            search_roots=task_source_search_roots,
        )
        context_bundle["local_source_context"] = local_source_context
        agent_instructions = collect_agent_instructions(
            repo_path=repo_path,
            input_snapshot=input_snapshot,
        )
        input_context = build_input_context(input_snapshot)
        input_materials = build_input_materials(
            workflow_snapshot=workflow_snapshot,
            input_snapshot=input_snapshot,
            input_context=input_context,
        )
        provider_snapshot = build_agent_provider_snapshot(
            workflow_snapshot=workflow_snapshot,
            provider_override=provider_override,
        )
        workflow_contract = build_workflow_contract(
            workflow_snapshot=workflow_snapshot,
            provider_snapshot=provider_snapshot,
        )
        workflow_contract["local_source_context"] = local_source_context
        agent_mcp_requests = build_agent_mcp_requests(
            workflow_snapshot=workflow_snapshot,
            input_snapshot=input_snapshot,
            workflow_contract=workflow_contract,
        )
        context_discovery_decision = build_context_discovery_decision(
            agent_instructions=agent_instructions,
            provider_snapshot=provider_snapshot,
        )
        context_artifacts = build_context_artifact_payloads(
            context_bundle=context_bundle,
            context_discovery_decision=context_discovery_decision,
            evidence_memory_configured=self.evidence_memory is not None,
            semantic_library_configured=self.semantic_library is not None,
        )
        black_box_generation_policy = (
            {} if is_v3_contract else build_black_box_generation_policy(
                context_bundle=context_bundle,
            )
        )
        test_activity_contract: dict[str, Any] | None = None
        if not is_v3_contract:
            test_activity_contract = legacy_execution.build_test_activity_contract(
                target=_test_activity_target(
                    workflow_snapshot=workflow_snapshot,
                    input_snapshot=input_snapshot,
                    context_bundle=context_bundle,
                ),
                repo_path=repo_path,
                workflow_outputs=_test_activity_requested_outputs(workflow_snapshot),
                user_requirements=_test_activity_user_requirements(
                    workflow_snapshot=workflow_snapshot,
                    input_snapshot=input_snapshot,
                ),
            )
            workflow_contract["test_activity_contract"] = test_activity_contract
        quality_readiness = (
            {
                "status": "not_required",
                "required": False,
                "mode": "v3_explicit_validation_profile",
                "message": "V3 工作流未显式启用专业治理。",
                "recommended_action": "",
            }
            if is_v3_contract
            else legacy_execution.build_behavior_claim_audit_readiness(
                required=bool(
                    (test_activity_contract.get("quality_gates") or {}).get(
                        "require_independent_behavior_validation"
                    )
                    and test_activity_contract.get("artifact_contract")
                ),
                generator_identities=[
                    str(item.get("provider") or "")
                    for item in workflow_contract.get("agent_steps") or []
                    if isinstance(item, dict)
                ],
            )
        )
        provider_readiness = build_provider_readiness_report(
            repo_path=repo_path,
            provider_snapshot=provider_snapshot,
            deployment_evidence=[
                item for item in context_bundle.get("deployment_evidence") or []
                if isinstance(item, dict)
            ],
            quality_readiness=quality_readiness,
        )
        task_bundle = {
            "task_run_id": task_run_id,
            "task_id": str(task_id or ""),
            "attempt_number": max(0, int(attempt_number)),
            "parent_task_run_id": str(parent_task_run_id or ""),
            "workflow_id": workflow_id,
            "compiled_contract_version": contract_version,
            "validation_profile": str(workflow_snapshot.get("validation_profile") or ""),
            "declared_inputs": [
                dict(item) for item in workflow_snapshot.get("declared_inputs") or []
                if isinstance(item, dict)
            ],
            "declared_outputs": [
                dict(item) for item in workflow_snapshot.get("declared_outputs") or []
                if isinstance(item, dict)
            ],
            "validators": [
                dict(item) for item in workflow_snapshot.get("validators") or []
                if isinstance(item, dict)
            ],
            "execution_profile": execution_profile,
            "task_context": task_context_payload,
            "network_policy": network_policy,
            "input_consumption": input_consumption,
            "workspace_id": workspace_id,
            "repo_path": repo_path,
            "inputs": input_snapshot,
            "input_context": input_context,
            "input_materials": input_materials,
            "workflow_contract": workflow_contract,
            "agent_mcp_requests": agent_mcp_requests,
            "agent_instructions": agent_instructions,
            "provider_snapshot": provider_snapshot,
            "provider_readiness": provider_readiness,
            "quality_readiness": quality_readiness,
            "context_discovery_decision": context_discovery_decision,
            "context_bundle": context_bundle,
            "local_source_context": local_source_context,
            "memory_retrieval": context_artifacts["memory_retrieval"],
            "source_read_chain": context_artifacts["source_read_chain"],
            "evidence_consumption_trajectory": context_artifacts["evidence_consumption_trajectory"],
            "degraded_retrieval": context_artifacts["degraded_retrieval"],
            # The canonical V3 snapshot is materialized after every frozen
            # component is written.  Child harness envelopes receive the
            # stable path, never a mutable in-memory copy of the run state.
            "run_snapshot_path": "run_snapshot_v3.json",
            "required_artifacts_by_step": required_artifacts_by_step,
            "output_schemas_by_step": output_schemas_by_step,
            "semantic_import_outputs_by_step": semantic_import_outputs_by_step,
            "created_at": _now(),
        }
        if is_v3_contract:
            # This is the exact immutable definition read from the task-local
            # store.  The task API may later attach its matching compiled plan;
            # neither is rebuilt from a mutable draft or live registry.
            task_bundle["compiled_definition"] = json.loads(
                json.dumps(workflow_snapshot, ensure_ascii=False)
            )
        if not is_v3_contract:
            task_bundle["stage_specs"] = stage_specs
            task_bundle["artifact_contract_v3"] = artifact_contract_v3
            task_bundle["black_box_generation_policy"] = black_box_generation_policy
            task_bundle["test_activity_contract"] = test_activity_contract

        agent_runs: list[dict[str, Any]] = []
        for step in workflow_snapshot.get("steps") or []:
            if not isinstance(step, dict) or step.get("type") != "agent_task":
                continue
            step = {
                **step,
                "skill_instructions": resolve_workbench_skill_instructions(
                    step.get("skills") or [],
                    step.get("skill_instructions") or [],
                ),
            }
            step_id = str(step.get("id") or f"step_{len(agent_runs) + 1}")
            provider = _canonical_agent_provider(
                str(provider_override or step.get("provider") or "claude-code")
            )
            command = _agent_task_provider_command(provider)
            runtime_limits = _agent_task_runtime_limits(
                provider,
                step=step if is_v3_contract else None,
            )
            prompt_transport = _agent_task_prompt_transport(provider)
            step_input_snapshot = _scoped_input_snapshot_for_step(step, input_snapshot)
            step_input_consumption = scope_input_consumption_ledger(
                input_consumption,
                input_snapshot=step_input_snapshot,
            )
            step_input_context = build_input_context(step_input_snapshot)
            step_input_materials = build_input_materials(
                workflow_snapshot=workflow_snapshot,
                input_snapshot=step_input_snapshot,
                input_context=step_input_context,
            )
            step_context_bundle = build_workbench_context_bundle(
                workspace_id=workspace_id,
                repo_path=repo_path,
                input_snapshot=step_input_snapshot,
                query_hints=task_query_hints,
                evidence_memory=self.evidence_memory,
                semantic_library=self.semantic_library,
            )
            step_source_context_budget = _source_context_budget_for_step(
                step,
                execution_profile=execution_profile,
            )
            step_source_evidence_hints = [
                dict(item)
                for item in step.get("source_evidence_hints") or []
                if isinstance(item, dict)
            ]
            step_source_search_roots = [
                str(value)
                for value in step.get("source_context_search_roots") or []
                if str(value).strip()
            ]
            step_source_evidence_hints.extend(
                _deep_module_evidence_hints(
                    execution_profile=execution_profile,
                    query=str(step_context_bundle.get("query") or ""),
                    search_roots=step_source_search_roots,
                )
            )
            step_local_source_context = prepared_source_context(
                str(step_context_bundle.get("query") or ""),
                limit=step_source_context_budget["limit"],
                min_source_files=step_source_context_budget["min_source_files"],
                min_test_files=step_source_context_budget["min_test_files"],
                max_candidates_to_read=step_source_context_budget[
                    "max_candidates_to_read"
                ],
                excerpt_radius=step_source_context_budget["excerpt_radius"],
                evidence_hints=step_source_evidence_hints,
                search_roots=step_source_search_roots,
            )
            step_context_bundle["local_source_context"] = step_local_source_context
            step_agent_instructions = collect_agent_instructions(
                repo_path=repo_path,
                input_snapshot=step_input_snapshot,
            )
            step_workflow_contract = json.loads(json.dumps(workflow_contract))
            step_workflow_contract["local_source_context"] = step_local_source_context
            step_agent_mcp_requests = build_agent_mcp_requests(
                workflow_snapshot=workflow_snapshot,
                input_snapshot=step_input_snapshot,
                workflow_contract=step_workflow_contract,
            )
            step_context_discovery_decision = build_context_discovery_decision(
                agent_instructions=step_agent_instructions,
                provider_snapshot=provider_snapshot,
            )
            step_context_artifacts = build_context_artifact_payloads(
                context_bundle=step_context_bundle,
                context_discovery_decision=step_context_discovery_decision,
                evidence_memory_configured=self.evidence_memory is not None,
                semantic_library_configured=self.semantic_library is not None,
            )
            step_black_box_generation_policy = (
                {} if is_v3_contract else build_black_box_generation_policy(
                    context_bundle=step_context_bundle,
                )
            )
            step_test_activity_contract: dict[str, Any] | None = None
            if not is_v3_contract:
                step_test_activity_contract = legacy_execution.build_test_activity_contract(
                    target=_test_activity_target(
                        workflow_snapshot=workflow_snapshot,
                        input_snapshot=step_input_snapshot,
                        context_bundle=step_context_bundle,
                    ),
                    repo_path=repo_path,
                    workflow_outputs=_test_activity_requested_outputs(workflow_snapshot),
                    user_requirements=_test_activity_user_requirements(
                        workflow_snapshot=workflow_snapshot,
                        input_snapshot=step_input_snapshot,
                    ),
                )
                step_workflow_contract["test_activity_contract"] = step_test_activity_contract
            execution_contract = build_executor_handoff_contract(
                workflow_snapshot=workflow_snapshot,
                workflow_contract=step_workflow_contract,
                input_snapshot=step_input_snapshot,
                input_materials=step_input_materials,
                agent_mcp_requests=step_agent_mcp_requests,
                repo_path=repo_path,
                step=step,
                step_id=step_id,
                provider=provider,
                required_artifacts=required_artifacts_by_step.get(step_id, []),
                expected_output_schemas=output_schemas_by_step.get(step_id, []),
                expected_semantic_outputs=semantic_import_outputs_by_step.get(step_id, []),
                test_activity_contract=step_test_activity_contract,
                task_context=task_context_payload,
                execution_profile=execution_profile,
            )
            step_bundle = {
                **task_bundle,
                "inputs": step_input_snapshot,
                "input_consumption": step_input_consumption,
                "input_context": step_input_context,
                "input_materials": step_input_materials,
                "workflow_contract": step_workflow_contract,
                "agent_mcp_requests": step_agent_mcp_requests,
                "agent_instructions": step_agent_instructions,
                "context_discovery_decision": step_context_discovery_decision,
                "context_bundle": step_context_bundle,
                "local_source_context": step_local_source_context,
                "memory_retrieval": step_context_artifacts["memory_retrieval"],
                "source_read_chain": step_context_artifacts["source_read_chain"],
                "evidence_consumption_trajectory": step_context_artifacts["evidence_consumption_trajectory"],
                "degraded_retrieval": step_context_artifacts["degraded_retrieval"],
                "step_id": step_id,
                "goal": step.get("goal") or "",
                "skills": [str(item) for item in step.get("skills") or []],
                "skill_instructions": [
                    item for item in step.get("skill_instructions") or []
                    if isinstance(item, dict)
                ],
                "required_artifacts": required_artifacts_by_step.get(step_id, []),
                "expected_output_schemas": output_schemas_by_step.get(step_id, []),
                "expected_semantic_outputs": semantic_import_outputs_by_step.get(step_id, []),
                "mcp_profile": step.get("mcp_profile") or "",
                "execution_contract": execution_contract,
            }
            if not is_v3_contract:
                step_bundle["black_box_generation_policy"] = step_black_box_generation_policy
                step_bundle["test_activity_contract"] = step_test_activity_contract
            agent_run = AgentHarnessFacade(
                artifact_dir / "agent_runs" / step_id
            ).prepare(
                HarnessRunRequest(
                    provider=provider,
                    command=command,
                    cwd=repo_path,
                    workflow_snapshot=workflow_snapshot,
                    task_bundle=step_bundle,
                    mcp_profile=str(step.get("mcp_profile") or ""),
                    prompt_transport=prompt_transport,
                    timeout_seconds=runtime_limits.get("timeout_seconds"),
                    idle_timeout_seconds=runtime_limits.get("idle_timeout_seconds"),
                    requires_network=bool(runtime_limits.get("requires_network", True)),
                    run_id=f"{task_run_id}_{step_id}",
                )
            )
            agent_runs.append({
                "step_id": step_id,
                "run_id": agent_run.run_id,
                "provider": provider,
                "artifact_dir": agent_run.artifact_dir,
                "mcp_profile": agent_run.mcp_profile,
                "prompt_transport": agent_run.prompt_transport,
                "required_artifacts": required_artifacts_by_step.get(step_id, []),
                **runtime_limits,
            })

        result = PreparedWorkbenchTaskRun(
            task_run_id=task_run_id,
            workflow_id=workflow_id,
            workspace_id=workspace_id,
            repo_path=repo_path,
            artifact_dir=str(artifact_dir),
            workflow_snapshot=workflow_snapshot,
            input_snapshot=input_snapshot,
            task_bundle=task_bundle,
            execution_profile=execution_profile,
            task_id=str(task_id or ""),
            attempt_number=max(0, int(attempt_number)),
            parent_task_run_id=str(parent_task_run_id or ""),
            execution_status="queued" if is_v3_contract else "prepared",
            artifact_validation_status=(
                "not_requested"
                if is_v3_contract
                and str(workflow_snapshot.get("validation_profile") or "") == "none"
                else "not_started"
            ),
            governance_status="not_requested" if is_v3_contract else "not_started",
            delivery_status="pending" if is_v3_contract else "none",
            agent_runs=agent_runs,
        )
        if is_v3_contract:
            _write_json(
                artifact_dir / "agent_execution_descriptors.json",
                {"schema_version": 1, "agent_runs": agent_runs},
            )
        _write_json(artifact_dir / "task_run.json", asdict(result))
        _write_json(artifact_dir / "workflow_snapshot.json", workflow_snapshot)
        if is_v3_contract:
            _write_json(artifact_dir / "compiled_definition.json", workflow_snapshot)
        _write_json(artifact_dir / "execution_profile.json", execution_profile)
        _write_json(artifact_dir / "network_policy.json", network_policy)
        if not is_v3_contract:
            _write_json(artifact_dir / "stage_specs.json", stage_specs)
            _write_json(artifact_dir / "artifact_contract_v3.json", artifact_contract_v3)
        _write_json(artifact_dir / "input_consumption.json", input_consumption)
        # Consumption events are appended while an Agent runs. Freeze the
        # prepared input ledger separately so retries validate the original
        # bindings instead of treating normal runtime observations as tampering.
        _write_json(artifact_dir / "input_consumption_snapshot.json", input_consumption)
        _write_json(artifact_dir / "workflow_contract.json", workflow_contract)
        _write_json(artifact_dir / "agent_mcp_requests.json", agent_mcp_requests)
        _write_json(artifact_dir / "input_snapshot.json", input_snapshot)
        _write_json(artifact_dir / "input_context.json", input_context)
        _write_json(artifact_dir / "input_materials.json", input_materials)
        _write_json(artifact_dir / "agent_instructions.json", agent_instructions)
        _write_json(artifact_dir / "provider_snapshot.json", provider_snapshot)
        _write_json(artifact_dir / "provider_readiness.json", provider_readiness)
        _write_json(artifact_dir / "quality_readiness.json", quality_readiness)
        _write_json(artifact_dir / "context_discovery_decision.json", context_discovery_decision)
        _write_json(artifact_dir / "context_bundle.json", context_bundle)
        _write_json(artifact_dir / "local_source_context.json", local_source_context)
        _write_json(artifact_dir / "output_schemas_by_step.json", output_schemas_by_step)
        _write_json(
            artifact_dir / "semantic_import_outputs_by_step.json",
            semantic_import_outputs_by_step,
        )
        _write_json(artifact_dir / "memory_retrieval.json", context_artifacts["memory_retrieval"])
        _write_json(artifact_dir / "source_read_chain.json", context_artifacts["source_read_chain"])
        _write_json(
            artifact_dir / "evidence_consumption_trajectory.json",
            context_artifacts["evidence_consumption_trajectory"],
        )
        _write_json(artifact_dir / "degraded_retrieval.json", context_artifacts["degraded_retrieval"])
        if not is_v3_contract:
            _write_json(artifact_dir / "black_box_generation_policy.json", black_box_generation_policy)
            _write_json(artifact_dir / "test_activity_contract.json", test_activity_contract)
        _write_json(artifact_dir / "task_bundle.json", task_bundle)
        _write_json(
            artifact_dir / "run_snapshot_v3.json",
            build_run_snapshot_v3(
                artifact_dir=artifact_dir,
                task_run_id=task_run_id,
                task_id=str(task_id or ""),
                attempt_number=max(0, int(attempt_number)),
                parent_task_run_id=str(parent_task_run_id or ""),
                workflow_snapshot=workflow_snapshot,
            ),
        )
        write_task_artifact_manifest(artifact_dir, task_run_id=task_run_id)
        return result


class WorkbenchTaskRunStore:
    """Loads prepared task-run artifacts back into the Workbench."""

    def __init__(self, artifact_root: str | Path) -> None:
        self.artifact_root = Path(artifact_root)

    def load(self, task_run_id: str) -> PreparedWorkbenchTaskRun:
        task_run_dir = self.artifact_root / _safe_segment(task_run_id)
        payload = _read_json(task_run_dir / "task_run.json")
        if not isinstance(payload, dict):
            raise KeyError(task_run_id)
        return _prepared_task_run_from_payload(payload)

    def list(
        self,
        *,
        workspace_id: str | None = None,
        task_id: str | None = None,
        limit: int = 50,
    ) -> list[PreparedWorkbenchTaskRun]:
        if not self.artifact_root.exists():
            return []
        runs: list[PreparedWorkbenchTaskRun] = []
        for path in self.artifact_root.iterdir():
            if not path.is_dir():
                continue
            payload = _read_json(path / "task_run.json")
            if not isinstance(payload, dict):
                continue
            if workspace_id and payload.get("workspace_id") != workspace_id:
                continue
            if task_id is not None and str(payload.get("task_id") or "") != task_id:
                continue
            try:
                runs.append(_prepared_task_run_from_payload(payload))
            except (KeyError, TypeError, ValueError):
                continue
        runs.sort(key=lambda item: item.created_at, reverse=True)
        return runs[: max(1, int(limit))]


def build_workbench_context_bundle(
    *,
    workspace_id: str,
    repo_path: str = "",
    input_snapshot: dict[str, Any],
    query_hints: list[str] | None = None,
    evidence_memory: EvidenceMemoryStore | None = None,
    semantic_library: TestSemanticLibraryStore | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    query = _context_query_from_inputs(input_snapshot, query_hints=query_hints)
    evidence = []
    deployment_evidence = []
    semantic_cases = []
    if query and evidence_memory is not None:
        evidence = [
            _evidence_item_payload(
                item,
                source_slices=evidence_memory.list_source_slices(item.evidence_id),
                repo_path=repo_path,
            )
            for item in evidence_memory.search_analysis_memory(
                query,
                workspace_id=workspace_id,
                limit=limit,
            )
        ]
        deployment_evidence = [
            _evidence_item_payload(item, source_slices=[], repo_path=repo_path)
            for item in evidence_memory.list_evidence_items(
                workspace_id="codetalk-deployment",
                kinds=("deployment_probe", "provider_task_probe"),
                sources=("deployment_probe",),
                limit=limit,
            )
        ]
    if query and semantic_library is not None:
        semantic_cases = [
            _semantic_case_payload(item)
            for item in semantic_library.retrieve(
                query=query,
                limit=limit,
            )
        ]
    return {
        "query": query,
        "evidence": evidence,
        "deployment_evidence": deployment_evidence,
        "semantic_cases": semantic_cases,
        "limits": {
            "evidence": limit,
            "semantic_cases": limit,
        },
    }


def build_local_source_context(
    *,
    repo_path: str,
    query: str,
    limit: int = 8,
    min_source_files: int = 1,
    min_test_files: int = 2,
    max_candidates_to_read: int = 80,
    max_files_scanned: int = 5000,
    max_file_bytes: int = 768 * 1024,
    excerpt_radius: int = 4,
    path_hints: list[str] | None = None,
    search_roots: list[str] | None = None,
    evidence_hints: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    repo = Path(str(repo_path or ""))
    repo_name_tokens = set(_source_query_tokens(repo.name.replace("-", " ")))
    repo_name_tokens.update(
        f"lib{token}" for token in tuple(repo_name_tokens) if len(token) >= 3
    )
    tokens = [
        token
        for token in _source_query_tokens(query)
        if token != repo.name.lower() and token not in repo_name_tokens
    ]
    mandatory_protocol_tokens = {
        token for token in tokens
        if token in {"cbit", "partial_text_parameter"}
    }
    # A Login + CHAP analysis cannot substantiate authentication or duplicate
    # parameter behavior from call sites alone. Reserve the two implementation
    # definitions as bounded, SHA-validated context so downstream black-box
    # claims either cite the real behavior or remain explicit hypotheses.
    protocol_evidence_hints: list[dict[str, Any]] = []
    if {"iscsi", "login", "chap"}.issubset(set(tokens)):
        protocol_evidence_hints = [
            {
                "path": "lib/iscsi/iscsi.c",
                "term": "iscsi_auth_params",
                "label": "iSCSI CHAP authentication implementation",
                "contract_required": True,
            },
            {
                "path": "lib/iscsi/param.c",
                "term": "iscsi_parse_params",
                "label": "iSCSI Login parameter parser implementation",
                "contract_required": True,
            },
        ]
    base = {
        "provider": "local-source-search",
        "query": query[:2000],
        "repo_path": str(repo_path or ""),
        "files": [],
        "file_count": 0,
        "rules": {
            "source_first": True,
            "authority": "current local source files are hash-validated during prepare",
            "bounded_scan": True,
        },
    }
    if not repo_path:
        return {**base, "status": "skipped", "reason": "repo_path_missing"}
    try:
        root = repo.resolve()
    except OSError:
        return {**base, "status": "skipped", "reason": "repo_path_unresolved"}
    if not root.exists() or not root.is_dir():
        return {**base, "status": "skipped", "reason": "repo_path_not_directory"}
    repo_revision = _git_repo_revision(root)
    effective_roots = _source_search_roots(
        root=root,
        query=query,
        path_hints=path_hints,
        search_roots=search_roots,
    )
    git_files = _git_source_file_paths(
        root=root,
        repo_revision=repo_revision,
        search_roots=effective_roots,
    )
    explicit_path_candidates = _explicit_source_path_candidates(
        root=root,
        query=query,
        tokens=tokens,
        max_file_bytes=max_file_bytes,
        max_candidates=max(1, min(24, max_candidates_to_read // 2)),
        tracked_paths=git_files,
    )
    ranked_path_candidates = _rank_source_candidates_by_path(
        root=root,
        tokens=tokens,
        max_files_scanned=max_files_scanned,
        max_file_bytes=max_file_bytes,
        relative_paths=git_files,
        search_roots=effective_roots,
    )
    explicit_paths = {
        str(item.get("file_path") or "") for item in explicit_path_candidates
    }
    candidates = [
        *explicit_path_candidates,
        *[
            item for item in ranked_path_candidates
            if str(item.get("file_path") or "") not in explicit_paths
        ],
    ]
    ranked_content_candidates = _content_priority_source_candidates(
        root=root,
        tokens=tokens,
        path_candidates=candidates,
        max_file_bytes=max_file_bytes,
        search_roots=effective_roots,
        tracked_paths=git_files,
    )
    content_candidates = [
        *explicit_path_candidates,
        *[
            item for item in ranked_content_candidates
            if str(item.get("file_path") or "") not in explicit_paths
        ],
    ]
    reserved_content_slots = min(len(content_candidates), max_candidates_to_read)
    path_read_limit = max(0, max_candidates_to_read - reserved_content_slots)
    promoted_tests = [
        item
        for item in content_candidates
        if _local_source_classification(str(item.get("file_path") or "")) == "test"
    ][: max(0, min_test_files)]
    promoted_test_paths = {
        str(item.get("file_path") or "") for item in promoted_tests
    }
    promoted_content = [
        item
        for item in content_candidates
        if str(item.get("file_path") or "") not in promoted_test_paths
    ][: max(0, reserved_content_slots - len(promoted_tests))]
    promoted_content.extend(promoted_tests)
    promoted_paths = {
        str(item.get("file_path") or "") for item in promoted_content
    }
    remaining_path_candidates = [
        item
        for item in candidates
        if str(item.get("file_path") or "") not in promoted_paths
    ]
    candidates_to_read = [
        *promoted_content,
        *remaining_path_candidates[:path_read_limit],
        *remaining_path_candidates[path_read_limit:],
        *content_candidates[reserved_content_slots:],
    ]
    scored = _materialize_source_evidence_hints(
        root=root,
        evidence_hints=[*(evidence_hints or []), *protocol_evidence_hints],
        max_file_bytes=max_file_bytes,
        excerpt_radius=max(8, excerpt_radius),
    )
    for item in candidates_to_read[:max_candidates_to_read]:
        rel_path = str(item["file_path"])
        if _is_unrequested_vendor_plugin_path(
            rel_path,
            query=query,
        ) or _is_unrequested_bundled_dependency_path(
            rel_path,
            query=query,
        ) or _is_unrequested_platform_path(rel_path, query=query):
            continue
        source_path = _safe_repo_source_file(root, root / rel_path)
        if source_path is None:
            continue
        try:
            data = source_path.read_bytes()
        except OSError:
            continue
        text = data.decode("utf-8", errors="replace")
        score = int(item["score"])
        rel_lower = rel_path.lower()
        classification = _local_source_classification(rel_path)
        path_parts = {
            part for part in rel_lower.replace("\\", "/").split("/") if part
        }
        if classification == "source" and path_parts.intersection(
            {"lib", "src", "app", "include"}
        ):
            score += 12
        if classification == "test":
            score -= 4
        excerpt, start_line, end_line = _source_excerpt(
            text,
            tokens=tokens,
            radius=excerpt_radius,
        )
        sha256 = hashlib.sha256(data).hexdigest()
        excerpt_lower = excerpt.lower()
        matched_terms = _unique_strings(
            token
            for token in tokens
            if _source_token_matches_line(token, excerpt_lower)
        )
        score += len(matched_terms) * 4
        # A protocol-specific test obligation must survive generic timeout/auth
        # noise.  It remains a normal, SHA-validated source slice; this only
        # reserves it during bounded deterministic evidence selection.
        protocol_anchor = bool(
            mandatory_protocol_tokens.intersection(matched_terms)
            and classification == "source"
            and source_path.suffix.lower() == ".c"
        )
        if protocol_anchor:
            score += 20_000
        if tokens and score <= 0:
            continue
        scored.append({
            "file_path": rel_path,
            "score": score,
            "content_match_count": int(item.get("content_match_count") or 0),
            "behavior_score": _source_excerpt_behavior_score(excerpt),
            "explicit_path": bool(item.get("explicit_path")),
            "matched_terms": matched_terms,
            "start_line": start_line,
            "end_line": end_line,
            "excerpt": excerpt,
            "sha256": sha256,
            "size_bytes": len(data),
            "line_count": _line_count_text(text),
            "symbols": _unique_strings([
                *_source_symbols(excerpt),
                *_enclosing_source_symbols(text, start_line=start_line),
            ])[:12],
            "classification": classification,
            "status": "validated_source_file",
            "protocol_anchor": protocol_anchor,
        })
    deduplicated: list[dict[str, Any]] = []
    seen_slices: set[tuple[str, int, int]] = set()
    for item in scored:
        slice_key = (
            str(item.get("file_path") or ""),
            int(item.get("start_line") or 0),
            int(item.get("end_line") or 0),
        )
        if slice_key in seen_slices:
            continue
        seen_slices.add(slice_key)
        deduplicated.append(item)
    scored = deduplicated
    scored.sort(
        key=lambda item: (
            not bool(item.get("explicit_path")),
            not bool(item.get("contract_required")),
            not bool(item.get("evidence_hint")),
            not bool(item.get("symbols")),
            -int(item.get("score") or 0),
            str(item.get("file_path") or ""),
        )
    )
    required_candidates = [
        item for item in scored if bool(item.get("contract_required"))
    ]
    # A hint can require several functions from the same central file.  The
    # first bounded context is a *path breadth* contract, however: retaining
    # all of those slices here would consume the source quota before parser,
    # lifecycle and session implementations can enter the ledger. Additional
    # same-file slices are expanded later from this SHA-validated selection.
    required_files: list[dict[str, Any]] = []
    required_paths: set[tuple[str, str, str]] = set()
    for item in required_candidates:
        classification = str(item.get("classification") or "source")
        key = (
            classification,
            str(item.get("file_path") or ""),
            (
                f"{int(item.get('start_line') or 0)}:{int(item.get('end_line') or 0)}"
                if bool(item.get("allow_same_file"))
                else ""
            ),
        )
        if key in required_paths:
            continue
        required_paths.add(key)
        required_files.append(item)
    required_slice_keys = {
        (
            str(item.get("file_path") or ""),
            int(item.get("start_line") or 0),
            int(item.get("end_line") or 0),
        )
        for item in required_files
    }
    # A workflow contract may promise named evidence anchors.  Retain those
    # locally verified slices before filling the remaining bounded budget with
    # relevance-ranked material.  This is not an unbounded prompt expansion:
    # an invalid preset with more required slices than its limit is clipped and
    # will be surfaced by the normal output contract rather than silently
    # claiming a missing anchor was consumed.
    required_files = required_files[:limit]
    remaining_limit = max(0, limit - len(required_files))
    required_test_count = sum(
        1
        for item in required_files
        if _local_source_classification(str(item.get("file_path") or "")) == "test"
    )
    required_source_paths = {
        str(item.get("file_path") or "")
        for item in required_files
        if _local_source_classification(str(item.get("file_path") or "")) == "source"
    }
    selected_remaining = _select_source_and_test_evidence(
        [
            item
            for item in scored
            if (
                str(item.get("file_path") or ""),
                int(item.get("start_line") or 0),
                int(item.get("end_line") or 0),
            ) not in required_slice_keys
            # Same-file required slices are expanded deterministically after
            # breadth selection. Do not let them displace an unrepresented
            # source path in this first evidence ledger.
            and (
                _local_source_classification(str(item.get("file_path") or ""))
                != "source"
                or str(item.get("file_path") or "") not in required_source_paths
            )
        ],
        limit=remaining_limit,
        min_source_files=min_source_files,
        min_test_files=max(0, min_test_files - required_test_count),
        coverage_tokens=tokens,
    )
    files = [*required_files, *selected_remaining]
    return {
        **base,
        "status": "ready" if files else "empty",
        "file_count": len(files),
        "files": files,
        "scanned_file_count": min(
            len(candidates) + len(content_candidates), max_files_scanned
        ),
        "token_count": len(tokens),
        "tokens": tokens[:48],
        "repo_revision": repo_revision,
        "file_discovery": "git_ls_files" if git_files is not None else "bounded_recursive",
        "search_roots": effective_roots,
    }


def _materialize_source_evidence_hints(
    *,
    root: Path,
    evidence_hints: list[dict[str, Any]] | None,
    max_file_bytes: int,
    excerpt_radius: int,
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    seen_hints: set[tuple[str, str]] = set()
    for index, hint in enumerate(evidence_hints or []):
        if not isinstance(hint, dict):
            continue
        rel_path = str(hint.get("path") or "").strip().replace("\\", "/")
        if rel_path.startswith("./"):
            rel_path = rel_path[2:]
        term = str(hint.get("term") or "").strip()
        if not rel_path or not term or (rel_path, term) in seen_hints:
            continue
        seen_hints.add((rel_path, term))
        source_path = (root / rel_path).resolve()
        try:
            source_path.relative_to(root)
        except ValueError:
            continue
        if (
            not source_path.is_file()
            or source_path.suffix.lower() not in SOURCE_EXTENSIONS
        ):
            continue
        try:
            data = source_path.read_bytes()
        except OSError:
            continue
        if len(data) > max_file_bytes:
            continue
        text = data.decode("utf-8", errors="replace")
        if term.lower() not in text.lower():
            continue
        excerpt, start_line, end_line = _source_hint_excerpt(
            text,
            term=term,
            radius=excerpt_radius,
            max_chars=4000,
        )
        cards.append({
            "file_path": rel_path,
            "score": 100_000 - index,
            "matched_terms": [term],
            "start_line": start_line,
            "end_line": end_line,
            "excerpt": excerpt,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
            "line_count": _line_count_text(text),
            "symbols": _unique_strings([
                *_source_symbols(excerpt),
                *_enclosing_source_symbols(text, start_line=start_line),
            ])[:12],
            "classification": _local_source_classification(rel_path),
            "status": "validated_source_file",
            "evidence_hint": True,
            "contract_required": bool(hint.get("contract_required")),
            "allow_same_file": bool(hint.get("allow_same_file")),
            "evidence_label": str(hint.get("label") or "").strip(),
        })
    return cards


def _explicit_source_path_candidates(
    *,
    root: Path,
    query: str,
    tokens: list[str],
    max_file_bytes: int,
    max_candidates: int,
    tracked_paths: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Turn existing source paths typed by the user into deterministic priorities."""
    raw_paths = re.findall(
        r"(?<![A-Za-z0-9_.-])(?:/?[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+",
        str(query or ""),
    )
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    allowed_paths = set(tracked_paths) if tracked_paths is not None else None
    try:
        resolved_root = root.resolve()
    except OSError:
        return []

    for raw_index, raw_path in enumerate(_unique_strings(raw_paths)):
        path = Path(raw_path)
        candidate_path = path if path.is_absolute() else root / path
        try:
            resolved = candidate_path.resolve()
            relative = resolved.relative_to(resolved_root)
        except (OSError, ValueError):
            continue
        if not relative.parts or str(relative) == "." or not resolved.exists():
            continue
        source_paths: list[Path]
        if resolved.is_file():
            source_paths = [resolved]
        elif resolved.is_dir():
            source_paths = list(
                _iter_source_files(
                    resolved_root,
                    search_roots=[relative.as_posix()],
                )
            )[: max_candidates]
        else:
            continue
        ranked: list[dict[str, Any]] = []
        for source_path in source_paths:
            source_path = _safe_repo_source_file(resolved_root, source_path)
            if source_path is None:
                continue
            try:
                data = source_path.read_bytes()
                rel_path = source_path.relative_to(resolved_root).as_posix()
            except (OSError, ValueError):
                continue
            if (
                len(data) > max_file_bytes
                or rel_path in seen
                or (allowed_paths is not None and rel_path not in allowed_paths)
            ):
                continue
            text = data.decode("utf-8", errors="replace").lower()
            matched = [token for token in tokens if token in text]
            ranked.append({
                "file_path": rel_path,
                "score": 100_000 - raw_index * 100 + min(99, len(matched) * 3),
                "matched_terms": matched,
                "content_match_count": sum(text.count(token) for token in matched),
                "explicit_path": True,
            })
        ranked.sort(
            key=lambda item: (
                -len(item.get("matched_terms") or []),
                -int(item.get("content_match_count") or 0),
                str(item.get("file_path") or ""),
            )
        )
        for item in ranked:
            path_key = str(item.get("file_path") or "")
            if path_key in seen:
                continue
            seen.add(path_key)
            candidates.append(item)
            if len(candidates) >= max_candidates:
                return candidates
    return candidates


def _select_source_and_test_evidence(
    scored: list[dict[str, Any]],
    *,
    limit: int,
    min_source_files: int = 1,
    min_test_files: int = 2,
    coverage_tokens: list[str] | None = None,
) -> list[dict[str, Any]]:
    selection_limit = max(0, limit)
    coverage = set(coverage_tokens or [])
    selected: list[dict[str, Any]] = []
    eligible = [item for item in scored if _source_evidence_is_deliverable(item)]
    remaining = list(eligible)
    covered_terms: set[str] = set()

    def implementation_bonus(item: dict[str, Any]) -> int:
        suffix = Path(str(item.get("file_path") or "")).suffix.lower()
        content_matches = max(0, int(item.get("content_match_count") or 0))
        return (
            (20 if suffix == ".c" else 8 if suffix in {".h", ".hpp"} else 0)
            + (12 if item.get("symbols") else 0)
            + min(24, content_matches.bit_length() * 3)
            + min(16, max(0, int(item.get("behavior_score") or 0)) * 2)
        )

    def risk_evidence_bonus(item: dict[str, Any]) -> int:
        """Prefer verified implementation risk anchors over normal rejects.

        A source-first test workflow needs enough evidence of lifecycle,
        capacity, concurrency and error-propagation behavior before it can
        request a scored SFMEA.  These terms are only a ranking signal: the
        selected excerpt and SHA remain the sole admissible evidence.
        """
        text = " ".join(
            str(item.get(key) or "")
            for key in ("evidence_label", "matched_terms", "excerpt")
        ).lower()
        return 10_000 if re.search(
            r"todo.*mutex|synchronization|capacity|connections|timeout|"
            r"cleanup|destruct|digest error|header digest|data digest|"
            r"reconnect|retry|resource|release|free|error completion",
            text,
        ) else 0

    def is_risk_evidence(item: dict[str, Any]) -> bool:
        return risk_evidence_bonus(item) > 0

    # Keep a bounded set of independently verified lifecycle/capacity/error
    # anchors when adding test files.  Without this reserve, the test-file
    # quota can evict exactly the source slices that make a risk SFMEA
    # defensible, leaving only normal protocol rejection behavior.
    risk_reserve = min(8, max(0, selection_limit - min_test_files))

    def replacement_index_for_test() -> int:
        risk_count = sum(is_risk_evidence(item) for item in selected)
        return next(
            (
                index
                for index in range(len(selected) - 1, -1, -1)
                if str(selected[index].get("classification") or "source") != "test"
                and (
                    not is_risk_evidence(selected[index])
                    or risk_count > risk_reserve
                )
            ),
            -1,
        )

    while remaining and len(selected) < selection_limit:
        if not selected:
            candidate_index = 0
        else:
            candidate_index = max(
                range(len(remaining)),
                key=lambda index: (
                    int(remaining[index].get("score") or 0)
                    + implementation_bonus(remaining[index])
                    + risk_evidence_bonus(remaining[index])
                    + (20_000 if remaining[index].get("protocol_anchor") else 0)
                    + 8
                    * len(
                        (
                            set(remaining[index].get("matched_terms") or [])
                            & coverage
                        )
                        - covered_terms
                    ),
                    bool(remaining[index].get("evidence_hint")),
                    int(remaining[index].get("score") or 0),
                    str(remaining[index].get("file_path") or ""),
                ),
            )
        candidate = remaining.pop(candidate_index)
        selected.append(candidate)
        covered_terms.update(set(candidate.get("matched_terms") or []) & coverage)
    if limit < 2 or not selected:
        return selected
    selected_classes = {str(item.get("classification") or "source") for item in selected}
    for required_class in ("source", "test"):
        if required_class in selected_classes:
            continue
        replacement = next(
            (
                item
                for item in eligible
                if str(item.get("classification") or "source") == required_class
                and item not in selected
            ),
            None,
        )
        if replacement is None:
            continue
        replace_index = next(
            (
                index
                for index in range(len(selected) - 1, -1, -1)
                if str(selected[index].get("classification") or "source")
                != required_class
            ),
            len(selected) - 1,
        )
        selected[replace_index] = replacement
        selected_classes.add(required_class)
    desired_test_files = min(max(0, min_test_files), limit - 1)
    selected_paths = {str(item.get("file_path") or "") for item in selected}
    test_count = sum(
        str(item.get("classification") or "source") == "test"
        for item in selected
    )
    for candidate in eligible:
        if test_count >= desired_test_files:
            break
        if str(candidate.get("classification") or "source") != "test":
            continue
        if str(candidate.get("file_path") or "") in selected_paths:
            continue
        replace_index = replacement_index_for_test()
        if replace_index < 0:
            break
        selected_paths.discard(str(selected[replace_index].get("file_path") or ""))
        selected[replace_index] = candidate
        selected_paths.add(str(candidate.get("file_path") or ""))
        test_count += 1
    if desired_test_files:
        def test_relevance(item: dict[str, Any]) -> tuple[bool, int, int, str]:
            return (
                bool(item.get("evidence_hint")),
                int(item.get("score") or 0),
                implementation_bonus(item),
                str(item.get("file_path") or ""),
            )

        while True:
            selected_test_indexes = [
                index
                for index, item in enumerate(selected)
                if str(item.get("classification") or "source") == "test"
            ]
            unselected_tests = [
                item
                for item in eligible
                if str(item.get("classification") or "source") == "test"
                and str(item.get("file_path") or "") not in selected_paths
            ]
            if not selected_test_indexes or not unselected_tests:
                break
            weakest_index = min(
                selected_test_indexes,
                key=lambda index: test_relevance(selected[index]),
            )
            strongest = max(unselected_tests, key=test_relevance)
            if test_relevance(strongest) <= test_relevance(selected[weakest_index]):
                break
            selected_paths.discard(str(selected[weakest_index].get("file_path") or ""))
            selected[weakest_index] = strongest
            selected_paths.add(str(strongest.get("file_path") or ""))

    # A delivery contract counts independently reviewable paths, not just
    # slices.  Keep detailed slices from a central implementation file for
    # later expansion, but reserve distinct source/test files here so the
    # evidence pack also covers parser, lifecycle, session and test behavior.
    desired_source_paths = min(
        max(0, int(min_source_files)),
        max(0, selection_limit - min(max(0, int(min_test_files)), selection_limit)),
    )
    desired_test_paths = min(
        max(0, int(min_test_files)),
        max(0, selection_limit - desired_source_paths),
    )

    def candidate_rank(item: dict[str, Any]) -> tuple[int, int, int, int, str]:
        return (
            int(item.get("score") or 0),
            implementation_bonus(item),
            risk_evidence_bonus(item),
            int(bool(item.get("evidence_hint"))),
            str(item.get("file_path") or ""),
        )

    for required_class, desired_paths in (
        ("source", desired_source_paths),
        ("test", desired_test_paths),
    ):
        while True:
            class_indexes = [
                index
                for index, item in enumerate(selected)
                if str(item.get("classification") or "source") == required_class
            ]
            class_paths = {
                str(selected[index].get("file_path") or "")
                for index in class_indexes
            }
            if len(class_paths) >= desired_paths:
                break
            replacement_candidates = [
                item
                for item in eligible
                if str(item.get("classification") or "source") == required_class
                and str(item.get("file_path") or "") not in class_paths
            ]
            duplicate_indexes = [
                index
                for index in class_indexes
                if sum(
                    str(selected[other].get("file_path") or "")
                    == str(selected[index].get("file_path") or "")
                    for other in class_indexes
                ) > 1
            ]
            if not replacement_candidates or not duplicate_indexes:
                break
            replace_index = min(
                duplicate_indexes,
                key=lambda index: candidate_rank(selected[index]),
            )
            selected[replace_index] = max(replacement_candidates, key=candidate_rank)
    selected.sort(
        key=lambda item: (-int(item.get("score") or 0), str(item.get("file_path") or ""))
    )
    return selected


def _source_evidence_is_deliverable(item: dict[str, Any]) -> bool:
    """Mirror evidence validation before a candidate consumes a source slot."""
    if item.get("symbols"):
        return True
    suffix = Path(str(item.get("file_path") or "")).suffix.lower()
    return suffix in {".json", ".sh"}


def _source_excerpt_behavior_score(excerpt: str) -> int:
    """Measure executable branch/error/resource behavior in a bounded C slice."""
    lowered = str(excerpt or "").lower()
    patterns = (
        r"\bif\s*\(",
        r"\b(?:else|switch|case|goto)\b",
        r"\breturn\b",
        r"\b(?:free|close|cleanup|release|destroy|unlink)\s*\(",
        r"\b(?:retry|timeout|error|failed?|errno|null)\b",
        r"(?:!=|==|<=|>=|<\s*0|>\s*0)",
    )
    return sum(len(re.findall(pattern, lowered)) for pattern in patterns)


def _rank_source_candidates_by_path(
    *,
    root: Path,
    tokens: list[str],
    max_files_scanned: int,
    max_file_bytes: int,
    relative_paths: tuple[str, ...] | None = None,
    search_roots: list[str] | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    scanned = 0
    source_paths = (
        (root / relative_path for relative_path in relative_paths)
        if relative_paths is not None
        else _iter_source_files(root, search_roots=search_roots)
    )
    for source_path in source_paths:
        if scanned >= max_files_scanned:
            break
        scanned += 1
        source_path = _safe_repo_source_file(root, source_path)
        if source_path is None:
            continue
        try:
            stat = source_path.stat()
        except OSError:
            continue
        if stat.st_size > max_file_bytes:
            continue
        try:
            rel_path = source_path.relative_to(root).as_posix()
        except ValueError:
            continue
        lower_path = rel_path.lower()
        matched = [token for token in tokens if token in lower_path]
        score = len(matched) * 8
        if not tokens:
            score = 1
        if score <= 0:
            continue
        candidates.append({
            "file_path": rel_path,
            "score": score,
            "matched_terms": matched,
        })
    candidates.sort(key=lambda item: (-int(item["score"]), str(item["file_path"])))
    return candidates


def _content_priority_source_candidates(
    *,
    root: Path,
    tokens: list[str],
    path_candidates: list[dict[str, Any]],
    max_file_bytes: int,
    search_roots: list[str] | None = None,
    tracked_paths: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    rg = shutil.which("rg")
    if not rg or not tokens:
        return []
    path_term_counts = {
        token: sum(
            token in set(candidate.get("matched_terms") or [])
            for candidate in path_candidates
        )
        for token in tokens
    }
    priority_terms = [
        token
        for token in sorted(
            tokens,
            key=lambda token: (
                path_term_counts.get(token, 0),
                -len(token),
                tokens.index(token),
            ),
        )
        if not re.fullmatch(r"[0-9a-f]{7,}", token)
    ][:32]
    if not priority_terms:
        return []
    command = [
        rg,
        "--count-matches",
        "-i",
        "--no-messages",
        "--max-filesize",
        str(max_file_bytes),
        "-e",
        "|".join(re.escape(token) for token in priority_terms),
        "--",
        *(search_roots or ["."]),
    ]
    try:
        result = subprocess.run(
            command,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    path_candidates_by_path = {
        str(candidate.get("file_path") or ""): candidate
        for candidate in path_candidates
    }
    allowed_paths = set(tracked_paths) if tracked_paths is not None else None
    candidates: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for raw_match in result.stdout.splitlines():
        raw_path, separator, raw_count = raw_match.rpartition(":")
        rel_path = raw_path.strip().removeprefix("./").replace("\\", "/")
        if (
            not separator
            or not rel_path
            or rel_path in seen_paths
            or (allowed_paths is not None and rel_path not in allowed_paths)
            or Path(rel_path).suffix.lower() not in SOURCE_EXTENSIONS
        ):
            continue
        seen_paths.add(rel_path)
        existing = path_candidates_by_path.get(rel_path) or {}
        try:
            content_match_count = int(raw_count.strip())
        except ValueError:
            content_match_count = 1
        candidates.append({
            **existing,
            "file_path": rel_path,
            "score": int(existing.get("score") or 0),
            "matched_terms": list(existing.get("matched_terms") or []),
            "content_priority": True,
            "content_match_count": content_match_count,
        })
    candidates.sort(
        key=lambda item: (
            _local_source_classification(str(item.get("file_path") or "")) == "test",
            -int(item.get("content_match_count") or 0),
            -int(item.get("score") or 0),
            str(item.get("file_path") or ""),
        )
    )
    source_candidates = [
        item
        for item in candidates
        if _local_source_classification(str(item.get("file_path") or "")) != "test"
    ]
    test_candidates = [
        item
        for item in candidates
        if _local_source_classification(str(item.get("file_path") or "")) == "test"
    ]
    # rg may emit paths in a different order across processes. Keep bounded,
    # independently ranked pools so test evidence cannot disappear at an
    # arbitrary pre-sort cutoff.
    return [*source_candidates[:2048], *test_candidates[:512]]


def _is_unrequested_vendor_plugin_path(path: str, *, query: str) -> bool:
    """Keep generic risk terms from pulling unrelated vendor plugins into scope."""
    normalized = str(path or "").replace("\\", "/").lower().lstrip("./")
    parts = [part for part in normalized.split("/") if part]
    if len(parts) < 3 or parts[0] not in {"plugin", "plugins"}:
        return False
    vendor = parts[1]
    query_lower = str(query or "").lower()
    return (
        vendor not in query_lower
        and f"plugins/{vendor}" not in query_lower
        and f"plugin/{vendor}" not in query_lower
    )


def _is_unrequested_platform_path(path: str, *, query: str) -> bool:
    normalized = str(path or "").replace("\\", "/").lower().lstrip("./")
    query_lower = str(query or "").lower()
    requests_linux = bool(re.search(r"\blinux\b", query_lower))
    requests_windows = bool(re.search(r"\b(?:windows|win32)\b", query_lower))
    windows_specific = bool(
        re.search(
            r"(?:^|/)(?:windows|win32)(?:/|$)|"
            r"(?:^|/)(?:win|[^/]*(?:-win|_win|_win32|_windows))\.[^.]+$",
            normalized,
        )
    )
    return requests_linux and not requests_windows and windows_specific


def _is_unrequested_bundled_dependency_path(path: str, *, query: str) -> bool:
    normalized = str(path or "").replace("\\", "/").lower().lstrip("./")
    first = normalized.split("/", 1)[0]
    if first not in {"vendor", "third_party", "third-party"}:
        return False
    query_lower = str(query or "").lower().replace("-", "_")
    return first.replace("-", "_") not in query_lower


def _iter_source_files(root: Path, *, search_roots: list[str] | None = None):
    stack = [
        candidate
        for candidate in (
            [root / value for value in search_roots]
            if search_roots
            else [root]
        )
        if candidate.exists() and candidate.is_dir()
    ]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda item: item.name)
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name in SOURCE_SCAN_IGNORED_DIRS:
                    continue
                stack.append(entry)
                continue
            if entry.suffix.lower() in SOURCE_EXTENSIONS:
                yield entry


def _safe_repo_source_file(root: Path, candidate: Path) -> Path | None:
    """Resolve a regular source file without following links outside the repo."""
    try:
        if candidate.is_symlink():
            return None
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        if not resolved.is_file():
            return None
    except (OSError, ValueError):
        return None
    return resolved


def _git_repo_revision(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def _git_source_file_paths(
    *,
    root: Path,
    repo_revision: str,
    search_roots: list[str],
) -> tuple[str, ...] | None:
    if not repo_revision:
        return None
    try:
        index_result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-path", "index"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        index_path = Path(index_result.stdout.strip())
        if not index_path.is_absolute():
            index_path = root / index_path
        index_signature = index_path.stat().st_mtime_ns if index_path.exists() else 0
    except (OSError, subprocess.SubprocessError):
        index_signature = 0
    cache_key = (
        str(root),
        repo_revision,
        index_signature,
        tuple(search_roots),
    )
    with _GIT_SOURCE_FILE_CACHE_LOCK:
        cached = _GIT_SOURCE_FILE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    command = ["git", "-C", str(root), "ls-files", "-z"]
    if search_roots:
        command.extend(["--", *search_roots])
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    paths = tuple(
        value.decode("utf-8", errors="replace")
        for value in result.stdout.split(b"\0")
        if value
        and Path(value.decode("utf-8", errors="replace")).suffix.lower()
        in SOURCE_EXTENSIONS
    )
    with _GIT_SOURCE_FILE_CACHE_LOCK:
        if len(_GIT_SOURCE_FILE_CACHE) >= 64:
            _GIT_SOURCE_FILE_CACHE.pop(next(iter(_GIT_SOURCE_FILE_CACHE)))
        _GIT_SOURCE_FILE_CACHE[cache_key] = paths
    return paths


def _source_search_roots(
    *,
    root: Path,
    query: str,
    path_hints: list[str] | None,
    search_roots: list[str] | None,
) -> list[str]:
    explicit_requested = [
        str(value).strip().replace("\\", "/").strip("/")
        for value in [*(search_roots or []), *(path_hints or [])]
        if str(value).strip()
    ]
    query_text = str(query or "")
    inferred_requested = []
    for match in re.finditer(
        r"(?<![A-Za-z0-9_.-])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+",
        query_text,
    ):
        if match.start() > 0 and query_text[match.start() - 1] in {"/", "\\"}:
            continue
        inferred_requested.append(match.group(0).strip("/"))
    requested = [*explicit_requested, *inferred_requested]
    safe = []
    for value in _unique_strings(requested):
        candidate = (root / value).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.exists():
            safe.append(value)
    explicit_count = len(_unique_strings(explicit_requested))
    inferred_unique = _unique_strings(inferred_requested)
    safe_inferred = {
        value
        for value in safe
        if value in inferred_unique
    }
    if (
        not explicit_count
        and inferred_unique
        and len(safe_inferred) < len(inferred_unique)
    ):
        # Paths mentioned in prose are relevance hints, not a safe exclusive
        # search root. A stale or conventionalized path must not hide valid
        # entry points elsewhere in the repository.
        return []
    if not explicit_count and safe and re.search(
        r"源码|源代码|\bsource\b|\bcode\b",
        str(query or ""),
        flags=re.IGNORECASE,
    ):
        test_parts = {"test", "tests", "spec", "specs"}
        if all(
            any(part.lower() in test_parts for part in value.split("/"))
            for value in safe
        ):
            # A test-directory mention is an evidence request, not permission to
            # exclude production source from a source-first analysis.
            return []
    return safe[:16]


def _local_source_classification(path: str) -> str:
    normalized = path.replace("\\", "/").lower().lstrip("./")
    parts = [part for part in normalized.split("/") if part]
    is_test = any(
        part in {"test", "tests", "spec", "specs"} for part in parts[:-1]
    )
    return "test" if is_test else "source"


def _source_query_tokens(query: str) -> list[str]:
    query_without_paths = re.sub(
        r"(?<![A-Za-z0-9_.-])(?:/[A-Za-z0-9_.-]+){2,}",
        " ",
        str(query or ""),
    )
    raw_tokens = re.findall(
        r"[A-Za-z_][A-Za-z0-9_]{2,}", query_without_paths.lower()
    )
    if re.search(r"\bio\b", query_without_paths, flags=re.IGNORECASE):
        raw_tokens.append("io")
    semantic_expansions = (
        # Login text can be fragmented across PDUs.  This is a protocol-level
        # test obligation, so preserve the C-bit handling evidence even when a
        # tester only describes the broader iSCSI Login flow in natural language.
        (r"iscsi.*login|login.*iscsi|iSCSI.*登录|登录.*iSCSI", [
            "cbit", "partial_text_parameter",
        ]),
        (r"nvme[- ]?o[- ]?f|nvme\s+over\s+fabrics", ["nvmf", "fabrics"]),
        (r"dh[- ]?hmac[- ]?chap", ["dhchap"]),
        (r"资源(?:清理|释放|泄漏|耗尽)|长时间运行", ["cleanup", "release", "close", "refcount"]),
        (r"断线重连|重新连接|恢复", ["reconnect", "retry", "recovery"]),
        (r"认证|鉴权", ["auth", "authenticate"]),
        (r"超时", ["timeout"]),
        (r"并发|竞态", ["concurrent", "race", "lock"]),
        (r"回滚|部分成功", ["rollback"]),
        (r"子任务|子连接|子控制器", ["child"]),
        (r"异常传播|错误传播|失败传播|上游异常", ["error", "fail", "propagate", "return"]),
        (r"最终成功覆盖|吞掉错误|忽略错误", ["continue", "ret", "err"]),
        (r"翻转|溢出", ["wrap", "overflow", "counter"]),
    )
    semantic_tokens: list[str] = []
    for pattern, values in semantic_expansions:
        if re.search(pattern, query_without_paths, flags=re.IGNORECASE):
            semantic_tokens.extend(values)
    raw_tokens = [*semantic_tokens, *raw_tokens]
    stop = {
        "the", "and", "for", "with", "from", "this", "that", "shall",
        "must", "should", "when", "then", "only", "path", "file",
        "tmp", "volumes", "media",
        "analysis", "analyze", "source", "code", "evidence", "output",
        "report", "workflow", "markdown", "json", "sfmea", "black",
        "box", "case", "cases", "test", "tests", "testing", "unit",
        "linux", "commit", "current",
    }
    return _unique_strings(token for token in raw_tokens if token not in stop)[:48]


def _source_excerpt(
    text: str,
    *,
    tokens: list[str],
    radius: int,
    max_chars: int = 3000,
) -> tuple[str, int, int]:
    lines = text.splitlines()
    if not lines:
        return "", 0, 0
    lower_lines = [line.lower() for line in lines]
    token_weights = {
        # Protocol fragmentation is a mandatory iSCSI Login test concern.
        # Prefer the concrete implementation branch over a generic auth helper
        # when both exist in the same selected source file.
        "cbit": 10,
        "partial_text_parameter": 10,
        "chap": 8,
        "login": 7,
        "auth": 7,
        "authenticate": 7,
        "failure": 6,
        "failed": 6,
        "reject": 6,
        "reset": 5,
        "reconnect": 5,
        "session": 4,
        "timeout": 4,
        "connect": 8,
        "io": 6,
    }
    best_index = 0
    # A matched log/error line can receive a negative syntactic adjustment
    # below (it is a call expression, not a definition).  It must still win
    # over an unrelated first line; otherwise phrase hints silently collapse
    # to line 1 and lose their source evidence.
    best_score = -10_000
    for index, line in enumerate(lower_lines):
        matched = [token for token in tokens if _source_token_matches_line(token, line)]
        if not matched:
            continue
        score = sum(token_weights.get(token, 1) for token in matched)
        if re.search(r"^\s*(?:static\s+)?(?:inline\s+)?[A-Za-z_][\w\s\*\(\)]{0,80}\s+[A-Za-z_][A-Za-z0-9_]*\s*\(", line):
            score += 3
        if "(" in line and not re.match(r"^\s*(?:if|for|while|switch)\b", line):
            signature_tail = "\n".join(lines[index : min(len(lines), index + 6)])
            brace_index = signature_tail.find("{")
            semicolon_index = signature_tail.find(";")
            if brace_index >= 0 and (semicolon_index < 0 or brace_index < semicolon_index):
                score += 3
            elif semicolon_index >= 0:
                score -= 4
        if re.search(r"\b(if|case|return|SPDK_ERRLOG|SPDK_NOTICELOG)\b", line):
            score += 2
        if score > best_score:
            best_score = score
            best_index = index
    hit_index = best_index
    start = max(0, hit_index - radius)
    selected_tail = "\n".join(lines[hit_index : min(len(lines), hit_index + 6)])
    selected_brace = selected_tail.find("{")
    selected_semicolon = selected_tail.find(";")
    selected_is_definition = (
        not re.match(r"^\s*(?:if|for|while|switch)\b", lines[hit_index])
        and selected_brace >= 0
        and (selected_semicolon < 0 or selected_brace < selected_semicolon)
    )
    if selected_is_definition:
        start = hit_index
        if hit_index > 0 and "(" not in lines[hit_index - 1] and lines[hit_index - 1].strip():
            start = hit_index - 1
    end = min(len(lines), hit_index + (18 if selected_is_definition else radius + 1))
    excerpt = "\n".join(lines[start:end])
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars]
        end = start + len(excerpt.splitlines())
    return excerpt, start + 1, end


def _source_hint_excerpt(
    text: str,
    *,
    term: str,
    radius: int,
    max_chars: int = 4000,
) -> tuple[str, int, int]:
    lines = text.splitlines()
    if not lines:
        return "", 0, 0
    escaped = re.escape(str(term or "").strip())
    if escaped:
        term_text = str(term or "").strip()
        type_pattern = (
            re.compile(rf"^\s*{escaped}\b")
            if re.match(r"^(?:struct|enum|union)\s+", term_text)
            else re.compile(rf"^\s*(?:struct|enum|union)\s+{escaped}\b")
        )
        same_line_function_pattern = re.compile(
            rf"^\s*(?:static\s+)?(?:inline\s+)?[A-Za-z_][\w\s\*\(\)]{{0,100}}\s+{escaped}\s*\("
        )
        split_line_function_pattern = re.compile(rf"^\s*{escaped}\s*\(")
        for index, line in enumerate(lines):
            is_definition = bool(
                same_line_function_pattern.search(line)
                or type_pattern.search(line)
            )
            if not is_definition and split_line_function_pattern.search(line):
                prefix = lines[index - 1].strip() if index > 0 else ""
                signature_tail = "\n".join(lines[index : min(len(lines), index + 6)])
                is_definition = bool(
                    prefix
                    and re.match(
                        r"^(?:static\s+)?(?:inline\s+)?(?:void|int|bool|char|size_t|uint\d+_t|struct\s+\w+|\w+_t|\w+\s*\*)",
                        prefix,
                    )
                    and "{"
                    in signature_tail
                    and (
                        ";" not in signature_tail
                        or signature_tail.find("{") < signature_tail.find(";")
                    )
                )
            if not is_definition:
                continue
            start = index
            if split_line_function_pattern.search(line) and index > 0:
                start = index - 1
            if start > 0 and lines[start - 1].strip().startswith("/*"):
                start = index - 1
            end = min(len(lines), index + max(18, min(80, radius)))
            excerpt = "\n".join(lines[start:end])
            if len(excerpt) > max_chars:
                excerpt = excerpt[:max_chars]
                end = start + len(excerpt.splitlines())
            return excerpt, start + 1, end
    return _source_excerpt(
        text,
        tokens=[str(term or "").lower()],
        radius=radius,
        max_chars=max_chars,
    )


def _source_token_matches_line(token: str, line: str) -> bool:
    if token in {"connect", "io"}:
        return bool(re.search(rf"(?:^|[^a-z0-9]){re.escape(token)}(?:[^a-z0-9]|$)", line))
    return token in line


def _source_symbols(text: str) -> list[str]:
    symbols: list[str] = []
    pattern = re.compile(
        r"^\s*(?:static\s+)?(?:inline\s+)?[A-Za-z_][\w\s\*\(\)]{0,80}\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
        flags=re.MULTILINE,
    )
    for match in pattern.finditer(text[:20000]):
        name = match.group(1)
        if name in {"if", "for", "while", "switch", "return", "sizeof"}:
            continue
        symbols.append(name)
    supplemental_patterns = (
        re.compile(
            r"^\s*(?:(?:static|inline|extern|const|volatile)\s+)*"
            r"(?:struct\s+|enum\s+|union\s+)?[A-Za-z_][A-Za-z0-9_]*"
            r"(?:\s+[A-Za-z_][A-Za-z0-9_]*)*\s+\*+\s*"
            r"([A-Za-z_][A-Za-z0-9_]*)\s*\(",
            re.MULTILINE,
        ),
        re.compile(r"^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE),
        re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE),
        re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*\{", re.MULTILINE),
    )
    for supplemental in supplemental_patterns:
        symbols.extend(match.group(1) for match in supplemental.finditer(text[:20000]))
    return _unique_strings(symbols)


def _enclosing_source_symbols(text: str, *, start_line: int) -> list[str]:
    """Return the nearest C function definition preceding a verified slice."""
    if start_line <= 0:
        return []
    lines = text.splitlines()
    end = min(len(lines), start_line)
    start = max(0, end - 256)
    window = "\n".join(lines[start:end])
    symbols = _source_symbols(window)
    return symbols[-1:] if symbols else []


def _line_count_text(text: str) -> int:
    return len(text.splitlines()) if text else 0


def build_input_context(input_snapshot: dict[str, Any], *, preview_chars: int = 4000) -> dict[str, Any]:
    inputs: list[dict[str, Any]] = []
    for input_id, value in input_snapshot.items():
        if not isinstance(value, dict):
            continue
        kind = str(value.get("kind") or "")
        if kind == "file":
            inputs.append(
                _input_context_file(
                    input_id=str(input_id),
                    payload=value,
                    preview_chars=preview_chars,
                )
            )
        elif kind == "file_set":
            files = [
                _input_context_file(
                    input_id=str(file_payload.get("input_id") or f"{input_id}_{index + 1}"),
                    payload=file_payload,
                    preview_chars=preview_chars,
                )
                for index, file_payload in enumerate(value.get("files") or [])
                if isinstance(file_payload, dict)
            ]
            inputs.append({
                "input_id": str(input_id),
                "kind": "file_set",
                "count": int(value.get("count") or len(files)),
                "manifest_path": str(value.get("manifest_path") or ""),
                "files": files,
            })
    return {
        "inputs": inputs,
        "file_count": sum(
            len(item.get("files") or [item])
            for item in inputs
            if isinstance(item, dict)
        ),
        "preview_chars_per_file": preview_chars,
    }


def _scoped_input_snapshot_for_step(
    step: dict[str, Any], input_snapshot: dict[str, Any]
) -> dict[str, Any]:
    bindings = step.get("input_bindings")
    if not isinstance(bindings, dict):
        return dict(input_snapshot)
    source_ids = {
        str(binding.get("source_node_id") or "")
        for binding in bindings.values()
        if isinstance(binding, dict)
    }
    return {
        input_id: value
        for input_id, value in input_snapshot.items()
        if input_id in source_ids
    }


def build_input_materials(
    *,
    workflow_snapshot: dict[str, Any],
    input_snapshot: dict[str, Any],
    input_context: dict[str, Any],
) -> dict[str, Any]:
    input_defs = {
        str(item.get("id") or ""): item
        for item in workflow_snapshot.get("inputs") or []
        if isinstance(item, dict) and str(item.get("id") or "")
    }
    context_by_id = _input_context_files_by_id(input_context)
    materials: list[dict[str, Any]] = []
    for input_id, value in input_snapshot.items():
        if not isinstance(value, dict):
            continue
        definition = input_defs.get(str(input_id), {})
        kind = str(value.get("kind") or "")
        if kind == "file":
            materials.append(
                _input_material_payload(
                    input_id=str(input_id),
                    payload=value,
                    definition=definition,
                    context=context_by_id.get(str(input_id), {}),
                )
            )
        elif kind == "file_set":
            for index, file_payload in enumerate(value.get("files") or []):
                if not isinstance(file_payload, dict):
                    continue
                file_input_id = str(file_payload.get("input_id") or f"{input_id}_{index + 1}")
                materials.append(
                    _input_material_payload(
                        input_id=file_input_id,
                        payload=file_payload,
                        definition=definition,
                        context=context_by_id.get(file_input_id, {}),
                        parent_input_id=str(input_id),
                    )
                )
    return {
        "kind": "input_materials",
        "material_count": len(materials),
        "read_order": [str(item.get("input_id") or "") for item in materials],
        "materials": materials,
        "rules": {
            "agent_must_read_materials": bool(materials),
            "materials_are_source_truth": False,
            "source_truth_rule": (
                "Input materials provide requirements, design, coverage, diff, or user intent; "
                "source-code evidence still requires local source validation or accepted artifacts."
            ),
            "hash_verification": "sha256 identifies the copied material captured for this task run",
            "large_context_strategy": "read parsed_text_path first, then use chunks_path selectively",
        },
    }


def _input_context_files_by_id(input_context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in input_context.get("inputs") or []:
        if not isinstance(item, dict):
            continue
        if item.get("kind") == "file_set":
            for file_item in item.get("files") or []:
                if isinstance(file_item, dict):
                    input_id = str(file_item.get("input_id") or "")
                    if input_id:
                        result[input_id] = file_item
            continue
        input_id = str(item.get("input_id") or "")
        if input_id:
            result[input_id] = item
    return result


def _input_material_payload(
    *,
    input_id: str,
    payload: dict[str, Any],
    definition: dict[str, Any],
    context: dict[str, Any],
    parent_input_id: str = "",
) -> dict[str, Any]:
    role = str(definition.get("role") or "").strip()
    return {
        "input_id": input_id,
        "parent_input_id": parent_input_id,
        "input_type": str(definition.get("type") or ""),
        "material_role": role or input_id,
        "resolver": str(definition.get("resolver") or ""),
        "filename": str(payload.get("filename") or context.get("filename") or ""),
        "suffix": str(payload.get("suffix") or context.get("suffix") or ""),
        "size_bytes": int(payload.get("size_bytes") or context.get("size_bytes") or 0),
        "sha256": str(payload.get("sha256") or context.get("sha256") or ""),
        "original_path": str(payload.get("original_path") or ""),
        "copied_path": str(payload.get("copied_path") or ""),
        "parsed_text_path": str(payload.get("parsed_text_path") or ""),
        "chunks_path": str(payload.get("chunks_path") or ""),
        "chunk_count": int(context.get("chunk_count") or 0),
        "metadata_path": str(payload.get("metadata_path") or ""),
        "text_truncated": bool(context.get("text_truncated", False)),
        "parse_warnings": [str(item) for item in payload.get("parse_warnings") or []],
        "agent_action": "read parsed_text_path first; use chunks_path when more context is needed",
        "evidence_boundary": "material_context_only_not_source_evidence",
    }


def _input_context_file(
    *,
    input_id: str,
    payload: dict[str, Any],
    preview_chars: int,
) -> dict[str, Any]:
    parsed_text_path = str(payload.get("parsed_text_path") or "")
    chunks_path = str(payload.get("chunks_path") or "")
    chunks = _read_json(Path(chunks_path)) if chunks_path else None
    chunk_count = len(chunks) if isinstance(chunks, list) else 0
    return {
        "input_id": input_id,
        "kind": "file",
        "filename": str(payload.get("filename") or ""),
        "suffix": str(payload.get("suffix") or ""),
        "size_bytes": int(payload.get("size_bytes") or 0),
        "sha256": str(payload.get("sha256") or ""),
        "original_path": str(payload.get("original_path") or ""),
        "copied_path": str(payload.get("copied_path") or ""),
        "parsed_text_path": parsed_text_path,
        "chunks_path": chunks_path,
        "chunk_count": chunk_count,
        "text_preview": _read_text_preview(parsed_text_path, preview_chars),
        "text_truncated": _text_file_exceeds(parsed_text_path, preview_chars),
        "parse_warnings": [str(item) for item in payload.get("parse_warnings") or []],
    }


def _read_text_preview(path_text: str, max_chars: int) -> str:
    if not path_text:
        return ""
    try:
        return Path(path_text).read_text(encoding="utf-8", errors="replace")[:max_chars]
    except OSError:
        return ""


def _text_file_exceeds(path_text: str, max_chars: int) -> bool:
    if not path_text:
        return False
    try:
        return len(Path(path_text).read_text(encoding="utf-8", errors="replace")) > max_chars
    except OSError:
        return False


def build_output_schemas_by_step(workflow_snapshot: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    schemas: dict[str, list[dict[str, Any]]] = {}
    for output in workflow_snapshot.get("outputs") or []:
        if not isinstance(output, dict):
            continue
        schema = output.get("schema") or output.get("json_schema")
        if not isinstance(schema, dict):
            continue
        source_step = str(output.get("from") or output.get("source") or "").strip()
        if not source_step:
            continue
        schemas.setdefault(source_step, []).append({
            "output_id": str(output.get("id") or ""),
            "artifact": str(output.get("artifact") or output.get("path") or ""),
            "type": str(output.get("type") or ""),
            "schema": dict(schema),
        })
    return schemas


def build_semantic_import_outputs_by_step(
    workflow_snapshot: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    outputs: dict[str, list[dict[str, Any]]] = {}
    for output in workflow_snapshot.get("outputs") or []:
        if not isinstance(output, dict):
            continue
        semantic_import = output.get("semantic_import")
        if semantic_import is True:
            semantic_payload: dict[str, Any] = {"enabled": True}
        elif isinstance(semantic_import, dict):
            if semantic_import.get("enabled", True) is False:
                continue
            semantic_payload = {
                "enabled": True,
                **dict(semantic_import),
            }
        else:
            continue
        source_step = str(output.get("from") or output.get("source") or "").strip()
        if not source_step:
            continue
        outputs.setdefault(source_step, []).append({
            "output_id": str(output.get("id") or ""),
            "artifact": str(output.get("artifact") or output.get("path") or ""),
            "type": str(output.get("type") or ""),
            "semantic_import": semantic_payload,
        })
    return outputs


def _test_activity_target(
    *,
    workflow_snapshot: dict[str, Any],
    input_snapshot: dict[str, Any],
    context_bundle: dict[str, Any],
) -> str:
    preferred_keys = (
        "analysis_object",
        "target_scope",
        "module",
        "test_target",
        "requirements",
        "design",
        "mr_link",
        "patch_diff",
    )
    parts = [
        str(input_snapshot.get(key) or "").strip()
        for key in preferred_keys
        if str(input_snapshot.get(key) or "").strip()
    ]
    query = str(context_bundle.get("query") or "").strip()
    if query:
        parts.append(query)
    if not parts:
        parts.append(str(workflow_snapshot.get("name") or workflow_snapshot.get("id") or ""))
    return " ".join(_unique_strings(parts))[:2000]


def _test_activity_user_requirements(
    *,
    workflow_snapshot: dict[str, Any],
    input_snapshot: dict[str, Any],
) -> str:
    parts: list[str] = []
    for item in workflow_snapshot.get("inputs") or []:
        if not isinstance(item, dict):
            continue
        input_id = str(item.get("id") or "")
        value = input_snapshot.get(input_id)
        if isinstance(value, (str, int, float)) and str(value).strip():
            role = str(item.get("role") or item.get("label") or input_id)
            parts.append(f"{role}: {value}")
    for step in workflow_snapshot.get("steps") or []:
        if not isinstance(step, dict):
            continue
        goal = str(step.get("goal") or "").strip()
        if goal:
            parts.append(f"step:{step.get('id')}: {goal}")
        for instruction in step.get("skill_instructions") or []:
            if isinstance(instruction, dict) and str(instruction.get("body") or "").strip():
                parts.append(str(instruction.get("body")).strip())
    return "\n".join(parts)[:6000]


def _test_activity_requested_outputs(workflow_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        output for output in workflow_snapshot.get("outputs") or []
        if isinstance(output, dict)
    ]


def build_workflow_contract(
    *,
    workflow_snapshot: dict[str, Any],
    provider_snapshot: dict[str, Any],
) -> dict[str, Any]:
    steps = provider_snapshot.get("steps") or {}
    providers = provider_snapshot.get("providers") or {}
    agent_steps = [
        _workflow_contract_agent_step(
            step,
            provider_payload=_workflow_contract_provider_payload(
                step,
                providers=providers,
                steps=steps,
            ),
            step_payload=steps.get(str(step.get("id") or ""), {}) if isinstance(steps, dict) else {},
        )
        for step in workflow_snapshot.get("steps") or []
        if isinstance(step, dict) and step.get("type") == "agent_task"
    ]
    return {
        "workflow_id": str(workflow_snapshot.get("id") or ""),
        "workflow_name": str(workflow_snapshot.get("name") or ""),
        "version": workflow_snapshot.get("version", 1),
        "inputs": [
            _workflow_contract_input(item)
            for item in workflow_snapshot.get("inputs") or []
            if isinstance(item, dict)
        ],
        "agent_mcp_inputs": _workflow_contract_agent_mcp_inputs(
            workflow_snapshot=workflow_snapshot,
            agent_steps=agent_steps,
        ),
        "agent_steps": agent_steps,
        "outputs": [
            _workflow_contract_output(item)
            for item in workflow_snapshot.get("outputs") or []
            if isinstance(item, dict)
        ],
    }


def _workflow_contract_input(item: dict[str, Any]) -> dict[str, Any]:
    resolver = str(item.get("resolver") or "")
    schema = item.get("schema") or item.get("json_schema")
    schema_required = []
    schema_type = ""
    if isinstance(schema, dict):
        schema_required = [str(value) for value in schema.get("required") or []]
        schema_type = str(schema.get("type") or "")
    payload = {
        "id": str(item.get("id") or ""),
        "type": str(item.get("type") or ""),
        "required": bool(item.get("required", False)),
        "role": str(item.get("role") or ""),
        "resolver": resolver,
        "agent_owned": resolver == "agent_mcp",
    }
    if isinstance(schema, dict):
        payload["has_schema"] = True
        payload["schema_type"] = schema_type
        payload["schema_required"] = schema_required
        payload["schema"] = dict(schema)
    return payload


def _workflow_contract_agent_mcp_inputs(
    *,
    workflow_snapshot: dict[str, Any],
    agent_steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    mcp_steps = [
        step for step in agent_steps
        if bool(step.get("agent_owned_mcp"))
    ]
    if not mcp_steps:
        return []
    requests: list[dict[str, Any]] = []
    for item in workflow_snapshot.get("inputs") or []:
        if not isinstance(item, dict) or str(item.get("resolver") or "") != "agent_mcp":
            continue
        required_artifacts_by_step = {
            str(step.get("id") or ""): [str(value) for value in step.get("required_artifacts") or []]
            for step in mcp_steps
            if str(step.get("id") or "")
        }
        requests.append({
            "input_id": str(item.get("id") or ""),
            "input_type": str(item.get("type") or ""),
            "role": str(item.get("role") or ""),
            "resolver": "agent_mcp",
            "credential_owner": "agent_cli",
            "codetalk_fetch_allowed": False,
            "agent_step_ids": [str(step.get("id") or "") for step in mcp_steps if str(step.get("id") or "")],
            "mcp_profiles": _unique_strings(
                str(step.get("mcp_profile") or "")
                for step in mcp_steps
                if str(step.get("mcp_profile") or "")
            ),
            "required_artifacts_by_step": required_artifacts_by_step,
            "validation_rule": (
                "Agent CLI must fetch this input through its own MCP credentials and return "
                "required artifacts; CodeTalk validates artifacts instead of fetching the remote resource."
            ),
        })
    return requests


def build_agent_mcp_requests(
    *,
    workflow_snapshot: dict[str, Any],
    input_snapshot: dict[str, Any],
    workflow_contract: dict[str, Any],
) -> list[dict[str, Any]]:
    del workflow_snapshot
    requests: list[dict[str, Any]] = []
    for contract in workflow_contract.get("agent_mcp_inputs") or []:
        if not isinstance(contract, dict):
            continue
        input_id = str(contract.get("input_id") or "")
        if not input_id or input_id not in input_snapshot:
            continue
        required_artifacts_by_step = {
            str(step_id): [str(value) for value in values or []]
            for step_id, values in (contract.get("required_artifacts_by_step") or {}).items()
        }
        required_artifacts = _unique_strings(
            artifact
            for values in required_artifacts_by_step.values()
            for artifact in values
        )
        requests.append({
            "input_id": input_id,
            "input_type": str(contract.get("input_type") or ""),
            "value": input_snapshot[input_id],
            "resolver": "agent_mcp",
            "credential_owner": "agent_cli",
            "codetalk_fetch_allowed": False,
            "agent_step_ids": [str(value) for value in contract.get("agent_step_ids") or []],
            "mcp_profiles": [str(value) for value in contract.get("mcp_profiles") or []],
            "required_artifacts_by_step": required_artifacts_by_step,
            "artifact_validation": {
                "strategy": "required_artifacts",
                "codetalk_remote_fetch": False,
                "required_artifacts": required_artifacts,
            },
        })
    return requests


def _executor_handoff_source_analysis_limits(
    *,
    step: dict[str, Any],
    execution_profile: dict[str, Any] | None,
) -> dict[str, int]:
    profile_budget = _source_context_budget_for_step(
        step,
        execution_profile=execution_profile or {},
    )
    limits: dict[str, int] = {
        "max_files": profile_budget["limit"],
        "min_source_files": profile_budget["min_source_files"],
        "min_test_files": profile_budget["min_test_files"],
    }
    explicit_fields = (
        ("max_files", "source_analysis_max_files"),
        ("max_evidence_anchors", "source_analysis_max_evidence_anchors"),
        ("min_source_files", "source_analysis_min_source_files"),
        ("min_test_files", "source_analysis_min_test_files"),
    )
    for key, field in explicit_fields:
        if step.get(field) is None:
            continue
        floor = 0 if key == "min_test_files" else 1
        limits[key] = max(floor, int(step[field]))
    return limits


def build_executor_handoff_contract(
    *,
    workflow_snapshot: dict[str, Any],
    workflow_contract: dict[str, Any],
    input_snapshot: dict[str, Any],
    input_materials: dict[str, Any],
    agent_mcp_requests: list[dict[str, Any]],
    repo_path: str,
    step: dict[str, Any],
    step_id: str,
    provider: str,
    required_artifacts: list[str],
    expected_output_schemas: list[dict[str, Any]],
    expected_semantic_outputs: list[dict[str, Any]],
    test_activity_contract: dict[str, Any] | None = None,
    task_context: dict[str, Any] | None = None,
    execution_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the user-facing execution contract passed to an Agent or builtin LLM."""
    input_defs = {
        str(item.get("id") or ""): item
        for item in workflow_snapshot.get("inputs") or []
        if isinstance(item, dict) and str(item.get("id") or "")
    }
    scalar_inputs = _scalar_execution_inputs(
        input_snapshot=input_snapshot,
        input_defs=input_defs,
    )
    task_targets = _task_context_analysis_targets(task_context)
    source_context = (
        workflow_contract.get("local_source_context")
        if isinstance(workflow_contract.get("local_source_context"), dict)
        else {}
    )
    contract = {
        "contract_version": 1,
        "workflow": {
            "id": str(workflow_snapshot.get("id") or ""),
            "name": str(workflow_snapshot.get("name") or ""),
            "version": workflow_snapshot.get("version", 1),
        },
        "executor": {
            "provider": provider,
            "step_id": step_id,
            "step_type": str(step.get("type") or ""),
        },
        "repo_path": str(repo_path or ""),
        "goal": str(step.get("goal") or ""),
        "analysis_targets": task_targets + [
            item for item in scalar_inputs
            if _looks_like_analysis_target(item)
        ],
        "user_inputs": scalar_inputs,
        "input_materials": {
            "material_count": int(input_materials.get("material_count") or 0),
            "read_order": [str(item) for item in input_materials.get("read_order") or []],
            "materials": [
                item for item in input_materials.get("materials") or []
                if isinstance(item, dict)
            ],
            "rules": (
                input_materials.get("rules")
                if isinstance(input_materials.get("rules"), dict)
                else {}
            ),
        },
        "source_context": _execution_source_context(
            source_context=source_context,
        ),
        "source_coverage_policy": _source_coverage_policy(
            source_context=source_context,
        ),
        "source_analysis_limits": _executor_handoff_source_analysis_limits(
            step=step,
            execution_profile=execution_profile,
        ),
        "mcp": {
            "profile": str(step.get("mcp_profile") or ""),
            "availability": _mcp_execution_availability(
                workflow_contract=workflow_contract,
                step_id=step_id,
                provider=provider,
                mcp_profile=str(step.get("mcp_profile") or ""),
            ),
            "requests": [
                request for request in agent_mcp_requests
                if isinstance(request, dict)
                and (
                    step_id in [str(value) for value in request.get("agent_step_ids") or []]
                    or not request.get("agent_step_ids")
                )
            ],
            "rule": (
                "Use the selected MCP profile for agent_mcp inputs. If the executor cannot call MCP "
                "directly, state the limitation and rely on provided local materials only."
            ),
        },
        "skills": {
            "ids": [str(item) for item in step.get("skills") or []],
            "instructions": [
                item for item in step.get("skill_instructions") or []
                if isinstance(item, dict)
            ],
            "rule": "Apply these skills as method constraints when producing the declared artifacts.",
        },
        "outputs": {
            "required_artifacts": [str(item) for item in required_artifacts],
            "declared_outputs": _declared_outputs_for_step(
                workflow_contract=workflow_contract,
                step_id=step_id,
                required_artifacts=required_artifacts,
            ),
            "user_requested_outputs": _user_requested_outputs(scalar_inputs),
            "expected_output_schemas": expected_output_schemas,
            "expected_semantic_outputs": expected_semantic_outputs,
            "artifact_requirements": _artifact_requirements_for_builtin_outputs(
                required_artifacts=required_artifacts,
                declared_outputs=_declared_outputs_for_step(
                    workflow_contract=workflow_contract,
                    step_id=step_id,
                    required_artifacts=required_artifacts,
                ),
            ),
            "rule": (
                "Produce every required artifact under the executor artifact directory. "
                "JSON artifacts must satisfy their declared schemas."
            ),
        },
    }
    if task_context:
        contract["task_context"] = dict(task_context)
    # Test Activity is a legacy specialist contract.  Its absence is meaningful
    # for V3: an empty placeholder would still make downstream code infer that
    # governance was requested.
    if test_activity_contract is not None:
        contract["test_activity_contract"] = dict(test_activity_contract)
    return contract


def _mcp_execution_availability(
    *,
    workflow_contract: dict[str, Any],
    step_id: str,
    provider: str,
    mcp_profile: str,
) -> dict[str, Any]:
    profile = str(mcp_profile or "").strip()
    step_contract = next(
        (
            item
            for item in workflow_contract.get("agent_steps") or []
            if isinstance(item, dict) and str(item.get("id") or "") == step_id
        ),
        {},
    )
    supported_profiles = [
        str(item)
        for item in (step_contract.get("mcp_profiles") if isinstance(step_contract, dict) else []) or []
        if str(item).strip()
    ]
    supports_mcp = bool(step_contract.get("supports_mcp")) if isinstance(step_contract, dict) else False
    if not profile:
        return {
            "status": "not_requested",
            "user_message": "当前 Agent 节点未声明 MCP profile，将使用工作区输入和 CodeTalk 预取上下文。",
            "action": "如需远端 MR/工具数据，请在 Agent 节点选择 MCP profile。",
        }
    if profile in supported_profiles:
        return {
            "status": "direct",
            "user_message": f"{provider} 声明支持 MCP profile：{profile}，Agent 可直接使用自己的 MCP 凭证。",
            "action": "保持当前执行器与 MCP 配置，运行后检查 AgentInvocation 和产物。",
            "supported_profiles": supported_profiles,
        }
    if supports_mcp and not supported_profiles:
        return {
            "status": "direct_unverified",
            "user_message": f"{provider} 声明支持 MCP，但未列出可用 profile；本次会把 {profile} 交给 Agent，并要求其报告是否可用。",
            "action": "建议在设置页运行执行器探测，或在失败后切换到已声明该 MCP 的执行器。",
            "supported_profiles": supported_profiles,
        }
    prefetch_profiles = [
        item
        for item in re.split(r"[+,;\s]+", profile.lower())
        if item
    ]
    if prefetch_profiles and all(
        item in {"gitnexus", "cgc", "codehub-mcp"}
        for item in prefetch_profiles
    ):
        return {
            "status": "codetalk_prefetch",
            "user_message": f"{provider} 未声明 {profile}；CodeTalk 会优先查工作区/GitNexus/CGC 产物，把可验证证据注入任务包。",
            "action": "如果必须由 Agent 直接访问 MCP，请换成声明该 profile 的执行器；否则按本地证据继续运行。",
            "supported_profiles": supported_profiles,
        }
    return {
        "status": "unavailable",
        "user_message": f"{provider} 未声明 MCP profile：{profile}，CodeTalk 也没有该 profile 的预取通道。",
        "action": "更换执行器、修改 MCP profile，或把相关输入文件上传为普通输入材料。",
        "supported_profiles": supported_profiles,
    }


def _execution_source_context(*, source_context: dict[str, Any]) -> dict[str, Any]:
    files = [
        item for item in source_context.get("files") or []
        if isinstance(item, dict)
    ]
    return {
        "provider": str(source_context.get("provider") or "local-source-search"),
        "status": str(source_context.get("status") or "unknown"),
        "repo_revision": str(source_context.get("repo_revision") or ""),
        "file_discovery": str(source_context.get("file_discovery") or ""),
        "tokens": [
            str(token)
            for token in source_context.get("tokens") or []
            if str(token).strip()
        ],
        "source_first": bool((source_context.get("rules") or {}).get("source_first", False)),
        "rule": (
            "Read and cite these current repo source excerpts before making source-based claims. "
            "If no source files are available, say so explicitly and keep conclusions limited."
        ),
        "files": [
            {
                "file_path": str(item.get("file_path") or ""),
                "start_line": item.get("start_line"),
                "end_line": item.get("end_line"),
                "sha256": str(item.get("sha256") or ""),
                "classification": str(
                    item.get("classification")
                    or _local_source_classification(str(item.get("file_path") or ""))
                ),
                "status": str(item.get("status") or "validated_source_file"),
                "score": int(item.get("score") or 0),
                "content_match_count": int(item.get("content_match_count") or 0),
                "behavior_score": int(item.get("behavior_score") or 0),
                "matched_terms": [str(term) for term in item.get("matched_terms") or []],
                "symbols": _source_context_symbols_for_excerpt(item),
                "excerpt": str(item.get("excerpt") or ""),
            }
            for item in files
        ],
    }


def _source_coverage_policy(*, source_context: dict[str, Any]) -> dict[str, Any]:
    files = [
        str(item.get("file_path") or "").strip()
        for item in source_context.get("files") or []
        if isinstance(item, dict) and str(item.get("file_path") or "").strip()
    ]
    return {
        "evidence_files": _unique_strings(files),
        "rules": [
            "Coverage claims must distinguish verified source excerpts, files actually read by the executor, and files not analyzed.",
            "Do not list a file as uncovered if it appears in source-evidence.json, execution_contract.source_context.files, or the report body as analyzed evidence.",
            "Do not claim a file was fully covered unless the executor actually read the complete file; line excerpts are partial coverage.",
            "When scope is partial, write the precise missing functions, branches, or line ranges instead of a broad uncovered file list.",
        ],
    }


def _source_context_symbols_for_excerpt(item: dict[str, Any]) -> list[str]:
    excerpt = str(item.get("excerpt") or "")
    declared = _unique_strings(
        str(symbol) for symbol in item.get("symbols") or [] if str(symbol).strip()
    )
    present = [symbol for symbol in declared if symbol in excerpt]
    if present:
        return present
    inferred = _source_symbols(excerpt)
    if inferred:
        return inferred[:12]
    return _source_identifiers_from_excerpt(excerpt)[:12]


def _source_identifiers_from_excerpt(excerpt: str) -> list[str]:
    keywords = {
        "break", "case", "char", "const", "continue", "do", "else", "enum",
        "for", "if", "int", "return", "sizeof", "static", "struct", "switch",
        "uint8_t", "uint32_t", "uint64_t", "void", "while",
    }
    return _unique_strings(
        token
        for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", excerpt)
        if token not in keywords and not token.isdigit()
    )


def _scalar_execution_inputs(
    *,
    input_snapshot: dict[str, Any],
    input_defs: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    inputs: list[dict[str, Any]] = []
    for input_id, value in input_snapshot.items():
        definition = input_defs.get(str(input_id), {})
        if isinstance(value, (dict, list)):
            continue
        if str(definition.get("resolver") or "") == "agent_mcp":
            continue
        inputs.append({
            "input_id": str(input_id),
            "type": str(definition.get("type") or ""),
            "role": str(definition.get("role") or ""),
            "value": value,
        })
    return inputs


def _looks_like_analysis_target(item: dict[str, Any]) -> bool:
    marker = " ".join(
        str(item.get(key) or "").lower()
        for key in ("input_id", "role", "type")
    )
    return any(token in marker for token in ("analysis", "target", "目标", "对象", "范围"))


def _task_context_payload(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    name = str(value.get("name") or "").strip()
    description = str(value.get("description") or "").strip()
    tags = _unique_strings(str(item) for item in value.get("tags") or [])
    payload = {
        "name": name,
        "description": description,
        "tags": tags,
    }
    return {key: item for key, item in payload.items() if item not in ("", [])}


def _task_context_query_hints(value: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    for key in ("name", "description"):
        text = str(value.get(key) or "").strip()
        if text:
            hints.append(text)
    hints.extend(str(item) for item in value.get("tags") or [] if str(item).strip())
    return hints


def _task_context_analysis_targets(value: dict[str, Any] | None) -> list[dict[str, Any]]:
    context = _task_context_payload(value)
    text = " ".join(_task_context_query_hints(context)).strip()
    if not text:
        return []
    return [{
        "input_id": "task_context",
        "role": "任务目标",
        "type": "task_context",
        "value": text,
    }]


def _artifact_requirements_for_builtin_outputs(
    *,
    required_artifacts: list[str],
    declared_outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    required = {str(item).strip() for item in required_artifacts if str(item).strip()}
    requirements: list[dict[str, Any]] = []
    for output in declared_outputs:
        artifact = str(output.get("artifact") or "").strip()
        output_id = str(output.get("id") or output.get("output_id") or "").strip()
        output_type = str(output.get("type") or "").strip()
        if artifact not in required:
            continue
        if _is_source_claim_markdown_output(output):
            requirements.append({
                "artifact": artifact,
                "format": "markdown",
                "items": "source_claims",
                "rules": [
                    "Every source-based claim must be entailed by the cited execution_contract.source_context.files excerpt.",
                    "Before writing risk, defect, leak, missing cleanup, missing validation, unhandled error, or not released, check the cited excerpt for direct counter-evidence.",
                    "If the excerpt shows protective behavior such as free(...), put(...), close(...), return-on-error, bounds checks, or state checks, describe that behavior instead of claiming the opposite.",
                    "If the provided excerpt is ambiguous or incomplete, state that the current evidence does not prove the risk instead of asserting it as a defect.",
                    "Follow execution_contract.source_coverage_policy when writing covered/uncovered scope: do not mark evidence files as uncovered, and do not call excerpt-level analysis full-file coverage.",
                ],
            })
        if artifact == "source-evidence.json" or (
            output_type == "json" and output_id in {"source_evidence", "source-evidence"}
        ):
            requirements.append({
                "artifact": artifact,
                "format": "json_array",
                "items": "source_evidence_card",
                "required_fields": [
                    "file_path",
                    "start_line",
                    "end_line",
                    "excerpt",
                    "symbols",
                    "sha256",
                ],
                "rules": [
                    "Each card must copy file_path, start_line, end_line, sha256 and excerpt from one execution_contract.source_context.files item.",
                    "excerpt must exactly equal the full text for start_line..end_line from the current repo file.",
                    "symbols must be a non-empty array and every symbol must appear literally inside excerpt.",
                    "Do not wrap source evidence cards inside summary objects such as source_files_read or evidence_to_conclusion_mapping.",
                ],
            })
    return requirements


def _is_source_claim_markdown_output(output: dict[str, Any]) -> bool:
    artifact = str(output.get("artifact") or "").strip().lower()
    output_type = str(output.get("type") or "").strip().lower()
    if (
        output_type not in {"markdown", "md", "text/markdown"}
        and not artifact.endswith(".md")
    ):
        return False
    preset_text = json.dumps(
        output.get("content_presets") or [],
        ensure_ascii=False,
        sort_keys=True,
    ).lower()
    source_markers = (
        "source_evidence",
        "flow_doc",
        "storage_flow",
        "source",
        "源码",
        "证据",
        "流程",
    )
    return any(marker in preset_text for marker in source_markers)


def _user_requested_outputs(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requested: list[dict[str, Any]] = []
    for item in inputs:
        marker = " ".join(
            str(item.get(key) or "").lower()
            for key in ("input_id", "role", "type")
        )
        if not any(
            token in marker
            for token in (
                "requested_output",
                "requested_outputs",
                "output",
                "outputs",
                "deliverable",
                "artifact",
                "交付",
                "输出",
                "产物",
                "报告",
                "文件",
            )
        ):
            continue
        value = str(item.get("value") or "").strip()
        if not value:
            continue
        requested.append({
            "input_id": str(item.get("input_id") or ""),
            "role": str(item.get("role") or ""),
            "value": value,
            "items": _split_requested_output_items(value),
        })
    return requested


def _split_requested_output_items(value: str) -> list[str]:
    parts = re.split(r"[\n,，、;；]+", str(value or ""))
    return _unique_strings(part.strip() for part in parts if part.strip())[:40]


def _declared_outputs_for_step(
    *,
    workflow_contract: dict[str, Any],
    step_id: str,
    required_artifacts: list[str],
) -> list[dict[str, Any]]:
    required = {str(item) for item in required_artifacts}
    outputs: list[dict[str, Any]] = []
    for output in workflow_contract.get("outputs") or []:
        if not isinstance(output, dict):
            continue
        artifact = str(output.get("artifact") or "")
        source_step = str(output.get("from") or "")
        if source_step != step_id and artifact not in required:
            continue
        declared = dict(output)
        content_presets = _content_presets_for_declared_output(declared)
        if content_presets:
            declared["content_presets"] = content_presets
        else:
            declared.pop("content_presets", None)
        outputs.append(declared)
    return outputs


def _content_presets_for_declared_output(output: dict[str, Any]) -> list[Any]:
    artifact = str(output.get("artifact") or "").strip()
    output_id = str(output.get("id") or output.get("output_id") or "").strip()
    output_type = str(output.get("type") or "").strip()
    if artifact == "source-evidence.json" or (
        output_type == "json" and output_id in {"source_evidence", "source-evidence"}
    ):
        return []
    if isinstance(output.get("content_presets"), list):
        return list(output.get("content_presets") or [])
    return selected_output_content_presets(output.get("content_preset_ids"))


def _workflow_contract_provider_payload(
    step: dict[str, Any],
    *,
    providers: Any,
    steps: Any,
) -> Any:
    if not isinstance(providers, dict):
        return {}
    step_id = str(step.get("id") or "")
    step_payload = steps.get(step_id, {}) if isinstance(steps, dict) else {}
    provider = (
        str(step_payload.get("provider") or "")
        if isinstance(step_payload, dict)
        else ""
    )
    if not provider:
        provider = str(step.get("provider") or "claude-code")
    return providers.get(provider, {})


def _workflow_contract_agent_step(
    step: dict[str, Any],
    *,
    provider_payload: Any,
    step_payload: Any,
) -> dict[str, Any]:
    provider = str(
        (step_payload or {}).get("provider")
        if isinstance(step_payload, dict)
        else step.get("provider") or "claude-code"
    )
    capabilities = (
        provider_payload.get("capabilities")
        if isinstance(provider_payload, dict) and isinstance(provider_payload.get("capabilities"), dict)
        else {}
    )
    mcp_profile = str(step.get("mcp_profile") or "")
    supports_mcp = bool(capabilities.get("supports_mcp"))
    return {
        "id": str(step.get("id") or ""),
        "provider": provider,
        "mcp_profile": mcp_profile,
        "goal": str(step.get("goal") or ""),
        "skills": [str(item) for item in step.get("skills") or []],
        "skill_instructions": [
            item for item in step.get("skill_instructions") or []
            if isinstance(item, dict)
        ],
        "required_artifacts": [str(item) for item in step.get("required_artifacts") or []],
        "prompt_transport": str(capabilities.get("prompt_transport") or ""),
        "supports_mcp": supports_mcp,
        "mcp_profiles": list(capabilities.get("mcp_profiles") or []),
        "agent_owned_mcp": bool(mcp_profile or supports_mcp),
    }


def _workflow_contract_output(item: dict[str, Any]) -> dict[str, Any]:
    schema = item.get("schema") or item.get("json_schema")
    schema_required = []
    schema_type = ""
    if isinstance(schema, dict):
        schema_required = [str(value) for value in schema.get("required") or []]
        schema_type = str(schema.get("type") or "")
    payload = {
        "id": str(item.get("id") or ""),
        "type": str(item.get("type") or ""),
        "from": str(item.get("from") or item.get("source") or ""),
        "artifact": str(item.get("artifact") or item.get("path") or ""),
        "has_schema": isinstance(schema, dict),
        "schema_type": schema_type,
        "schema_required": schema_required,
    }
    semantic_import = item.get("semantic_import")
    if semantic_import is True:
        payload["semantic_import"] = {"enabled": True}
    elif isinstance(semantic_import, dict) and semantic_import.get("enabled", True) is not False:
        payload["semantic_import"] = {"enabled": True, **dict(semantic_import)}
    if isinstance(item.get("content_presets"), list):
        payload["content_presets"] = [
            dict(preset)
            for preset in item.get("content_presets") or []
            if isinstance(preset, dict)
        ]
    else:
        content_presets = selected_output_content_presets(item.get("content_preset_ids"))
        if content_presets:
            payload["content_presets"] = content_presets
    return payload


def _unique_strings(values: Any) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value)
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique


def build_agent_provider_snapshot(
    *,
    workflow_snapshot: dict[str, Any],
    provider_override: str | None = None,
) -> dict[str, Any]:
    providers: dict[str, dict[str, Any]] = {}
    steps: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for step in workflow_snapshot.get("steps") or []:
        if not isinstance(step, dict) or step.get("type") != "agent_task":
            continue
        step_id = str(step.get("id") or f"step_{len(steps) + 1}")
        provider = _canonical_agent_provider(
            str(provider_override or step.get("provider") or "claude-code")
        )
        steps[step_id] = {
            "provider": provider,
            "mcp_profile": str(step.get("mcp_profile") or ""),
            "provider_override": bool(provider_override),
        }
        if provider in providers:
            continue
        runtime = _agent_runtime_for_provider(provider)
        if runtime is not None:
            providers[provider] = _agent_runtime_provider_snapshot_item(runtime)
            continue
        if provider == BUILTIN_LLM_PROVIDER_ID:
            providers[provider] = _builtin_llm_provider_snapshot_item()
            continue
        spec = external_agent_provider_spec(provider)
        if spec is None:
            providers[provider] = {
                "provider": provider,
                "status": "unknown_provider",
                "owner": "agent_cli",
                "codetalk_callable": False,
                "agent_owned": True,
                "command": [provider],
                "fallback_commands": [],
                "capabilities": {},
                "prompt_transport": "",
                "credential_boundary": (
                    "Provider is not configured; CodeTalk cannot launch it or validate its capability claims."
                ),
                "diagnostics": _unknown_agent_cli_provider_diagnostics(provider),
            }
            warnings.append(f"{provider}: provider is not configured")
            continue
        providers[provider] = {
            "provider": provider,
            "status": "configured" if spec.command else "missing_command",
            "owner": "agent_cli",
            "codetalk_callable": False,
            "agent_owned": True,
            "display_name": spec.display_name or provider,
            "command": split_agent_command(spec.command) if spec.command else [],
            "fallback_commands": [
                split_agent_command(command)
                for command in spec.fallback_commands
                if command
            ],
            "readonly_args": list(spec.readonly_args),
            "env_hint_keys": sorted(spec.env_hints),
            "env_hints": _redact_provider_env_hints(spec.env_hints),
            "command_hint_env": spec.command_hint_env,
            "prompt_transport": spec.prompt_transport,
            "capabilities": external_agent_provider_capabilities(provider),
            "credential_boundary": (
                "Agent CLI 自己持有 MCP 凭证和远端访问权限；CodeTalk 只下发任务包并校验返回产物。"
            ),
            "diagnostics": build_agent_cli_provider_diagnostics(provider, spec),
        }
        if not spec.command:
            warnings.append(f"{provider}: command is not configured")
    return {
        "created_at": _now(),
        "providers": providers,
        "codetalk_providers": build_codetalk_provider_snapshot(),
        "steps": steps,
        "warnings": warnings,
    }


def agent_runtime_provider_id(runtime_id: str) -> str:
    return f"{AGENT_RUNTIME_PROVIDER_PREFIX}{runtime_id}"


def agent_runtime_id_from_provider(provider: str | None) -> str:
    value = str(provider or "").strip()
    if value.startswith(AGENT_RUNTIME_PROVIDER_PREFIX):
        return value[len(AGENT_RUNTIME_PROVIDER_PREFIX):]
    return ""


def _agent_runtime_for_provider(provider: str | None) -> dict[str, Any] | None:
    runtime_id = agent_runtime_id_from_provider(provider)
    if not runtime_id:
        return None
    runtime = get_agent_runtime_sync(runtime_id)
    if not runtime or not runtime.get("enabled", True):
        return None
    return runtime


def _canonical_agent_provider(provider: str) -> str:
    value = str(provider or "").strip()
    if not value or value == BUILTIN_LLM_PROVIDER_ID or value.startswith(AGENT_RUNTIME_PROVIDER_PREFIX):
        return value
    alias_contract = MANAGED_RUNTIME_ALIASES.get(value)
    if not alias_contract:
        return value
    runtime_id, expected_provider, expected_transport = alias_contract
    runtime = get_agent_runtime_sync(runtime_id)
    if not runtime or not runtime.get("enabled", True) or not str(runtime.get("command") or "").strip():
        return value
    if (
        str(runtime.get("provider") or "").strip() != expected_provider
        or str(runtime.get("prompt_transport") or "").strip() != expected_transport
        or not _runtime_command_available(str(runtime.get("command") or ""))
    ):
        return value
    return agent_runtime_provider_id(runtime_id)


def _runtime_command_available(command: str) -> bool:
    parts = split_agent_command(str(command or ""))
    if not parts:
        return False
    executable = Path(parts[0]).expanduser()
    if executable.is_absolute():
        return executable.is_file()
    return shutil.which(parts[0]) is not None


def _agent_task_provider_command(provider: str) -> list[str]:
    runtime = _agent_runtime_for_provider(provider)
    if runtime is not None:
        command = split_agent_command(str(runtime.get("command") or ""))
        return [*command, *[str(item) for item in runtime.get("args") or []]] or [provider]
    spec = external_agent_provider_spec(provider)
    return split_agent_command(spec.command) if spec and spec.command else [provider]


def _agent_task_runtime_limits(
    provider: str,
    *,
    step: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime = _agent_runtime_for_provider(provider)
    if runtime is None:
        return {}
    timeout_seconds = _positive_int(runtime.get("timeout_seconds"), default=900)
    prompt_transport = str(runtime.get("prompt_transport") or "stdin").strip()
    if prompt_transport == "codex_exec_json":
        idle_timeout_seconds = timeout_seconds
    else:
        idle_timeout_seconds = max(
            300,
            _positive_int(runtime.get("workflow_idle_timeout_seconds"), default=0)
            or _positive_int(runtime.get("idle_timeout_seconds"), default=0)
            or _positive_int(runtime.get("idle_complete_seconds"), default=0)
            or 300,
        )
    if step is not None:
        timeout_seconds = (
            _positive_int(step.get("timeout_sec"), default=0)
            or timeout_seconds
        )
        idle_timeout_seconds = (
            _positive_int(step.get("idle_timeout_sec"), default=0)
            or idle_timeout_seconds
        )
    return {
        "timeout_seconds": timeout_seconds,
        "idle_timeout_seconds": idle_timeout_seconds,
        "requires_network": bool(runtime.get("requires_network", True)),
    }


def _agent_task_prompt_transport(provider: str) -> str:
    runtime = _agent_runtime_for_provider(provider)
    if runtime is None:
        return ""
    return str(runtime.get("prompt_transport") or "stdin").strip() or "stdin"


def _positive_int(value: Any, *, default: int = 0) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _agent_runtime_provider_capabilities(runtime: dict[str, Any]) -> dict[str, Any]:
    prompt_transport = str(runtime.get("prompt_transport") or "stdin")
    mcp_profile = str(runtime.get("mcp_profile") or "").strip()
    return {
        "provider": agent_runtime_provider_id(str(runtime.get("id") or "")),
        "supports_mcp": bool(mcp_profile),
        "mcp_profiles": [mcp_profile] if mcp_profile else [],
        "supports_artifact_export": True,
        "supports_json_output": True,
        "prompt_transport": prompt_transport,
        "supports_source_discovery": True,
        "supports_call_graph": False,
        "supports_source_slices": True,
        "supports_black_box_terms": True,
    }


def _agent_runtime_provider_snapshot_item(runtime: dict[str, Any]) -> dict[str, Any]:
    provider = agent_runtime_provider_id(str(runtime.get("id") or ""))
    command = [
        *split_agent_command(str(runtime.get("command") or "")),
        *[str(item) for item in runtime.get("args") or []],
    ]
    return {
        "provider": provider,
        "status": "configured" if command else "missing_command",
        "owner": "agent_runtime",
        "codetalk_callable": False,
        "agent_owned": True,
        "display_name": str(runtime.get("name") or runtime.get("id") or provider),
        # This is deliberately capability-only. Secrets remain in the runtime
        # configuration, while the provider kind and invocation shape are frozen
        # so a later Settings edit cannot silently reinterpret an old task run.
        "runtime_id": str(runtime.get("id") or ""),
        "runtime_provider": str(runtime.get("provider") or ""),
        "command": command,
        "fallback_commands": [],
        "readonly_args": [],
        "env_hint_keys": sorted(str(key) for key in (runtime.get("env") or {})),
        "env_hints": _redact_provider_env_hints(runtime.get("env") or {}),
        "command_hint_env": "",
        "prompt_transport": str(runtime.get("prompt_transport") or "stdin"),
        "requires_network": bool(runtime.get("requires_network", True)),
        "capabilities": _agent_runtime_provider_capabilities(runtime),
        "credential_boundary": (
            "用户在设置页配置的 Agent Runtime 持有自身 CLI、环境变量和可能的 MCP 凭证；"
            "CodeTalk 只下发任务包并校验返回产物。"
        ),
        "diagnostics": {
            "owner": "agent_runtime",
            "configured_command_text": " ".join(command),
            "fallback_command_texts": [],
            "prompt_transport": str(runtime.get("prompt_transport") or "stdin"),
            "requires_network": bool(runtime.get("requires_network", True)),
            "startup_probe_endpoint": f"/api/settings/agent-runtimes/{runtime.get('id')}/probe",
            "runtime_id": str(runtime.get("id") or ""),
            "working_dir_mode": str(runtime.get("working_dir_mode") or "project"),
        },
        "unavailable_behavior": (
            "Workflow preparation continues; execution uses the Agent Runtime configured in Settings."
        ),
    }


def _builtin_llm_provider_snapshot_item() -> dict[str, Any]:
    return {
        "provider": BUILTIN_LLM_PROVIDER_ID,
        "status": "workflow_callable",
        "owner": "codetalk_builtin_llm",
        "codetalk_callable": True,
        "agent_owned": False,
        "display_name": "内置模型",
        "command": [],
        "fallback_commands": [],
        "readonly_args": [],
        "env_hint_keys": [],
        "env_hints": {},
        "command_hint_env": "",
        "prompt_transport": "builtin_llm",
        "capabilities": {
            "provider": BUILTIN_LLM_PROVIDER_ID,
            "supports_mcp": False,
            "mcp_profiles": [],
            "supports_artifact_export": True,
            "supports_json_output": True,
            "prompt_transport": "builtin_llm",
            "supports_source_discovery": True,
            "supports_call_graph": False,
            "supports_source_slices": True,
            "supports_black_box_terms": True,
        },
        "credential_boundary": "内置模型使用 CodeTalk 当前活跃聊天模型配置；CodeTalk 负责把工作流合同转成模型消息并落盘产物。",
        "diagnostics": {
            "owner": "codetalk_builtin_llm",
            "status": "workflow_callable",
            "reason": "工作流 runner 会把 execution_contract 发送给当前活跃聊天模型，并按声明产物落盘。",
        },
        "unavailable_behavior": "如果未配置活跃聊天模型，运行时会给出 LLM 配置错误。",
    }


def build_provider_readiness_report(
    *,
    repo_path: str,
    provider_snapshot: dict[str, Any],
    deployment_evidence: list[dict[str, Any]] | None = None,
    quality_readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    repo = _repo_readiness(repo_path)
    codetalk_providers = {
        provider: _codetalk_readiness_payload(provider, payload, repo_path=repo_path)
        for provider, payload in (provider_snapshot.get("codetalk_providers") or {}).items()
        if isinstance(payload, dict)
    }
    deployment_by_provider = _deployment_evidence_by_provider(deployment_evidence or [])
    agent_cli_providers = {
        provider: _agent_cli_readiness_payload(
            provider,
            payload,
            deployment_evidence=deployment_by_provider.get(provider),
        )
        for provider, payload in (provider_snapshot.get("providers") or {}).items()
        if isinstance(payload, dict)
    }
    blocking_reasons: list[str] = []
    warnings: list[str] = []
    if repo["status"] != "available":
        blocking_reasons.append("repo_path_missing")
    if isinstance(quality_readiness, dict) and quality_readiness.get("status") == "blocked":
        blocking_reasons.append("independent_quality_audit_not_ready")
    for provider, payload in codetalk_providers.items():
        if payload.get("status") in {"missing_config", "unavailable", "error"}:
            warnings.append(f"codetalk_provider_unavailable:{provider}")
    for provider, payload in agent_cli_providers.items():
        if payload.get("status") in {"unavailable", "missing_command", "unknown_provider", "error"}:
            warnings.append(f"agent_cli_unavailable:{provider}")
        if payload.get("deployment_evidence_conflict"):
            warnings.append(f"agent_cli_conflicts_with_deployment_probe:{provider}")
    summary_status = "blocked" if blocking_reasons else "degraded" if warnings else "ready"
    return {
        "created_at": _now(),
        "repo": repo,
        "codetalk_providers": codetalk_providers,
        "agent_cli_providers": agent_cli_providers,
        "quality_audit": dict(quality_readiness or {}),
        "summary": {
            "status": summary_status,
            "blocking_reasons": blocking_reasons,
            "warnings": warnings,
            "non_blocking_policy": (
                "Unavailable GitNexus, CGC, fast-context, or Agent CLI providers are recorded "
                "as degraded; CodeTalk continues with any remaining local, memory, semantic, "
                "and validated artifact paths."
            ),
        },
    }


def _repo_readiness(repo_path: str) -> dict[str, Any]:
    path = Path(str(repo_path or ""))
    exists = bool(repo_path and path.exists())
    is_dir = exists and path.is_dir()
    git_dir = path / ".git" if is_dir else Path()
    return {
        "path": str(repo_path or ""),
        "status": "available" if is_dir else "missing",
        "exists": exists,
        "is_dir": is_dir,
        "git_metadata_present": bool(is_dir and git_dir.exists()),
        "local_search_available": is_dir,
        "reason": "" if is_dir else "repo path does not exist or is not a directory",
    }


def _codetalk_readiness_payload(
    provider: str,
    payload: dict[str, Any],
    *,
    repo_path: str,
) -> dict[str, Any]:
    diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    return {
        "provider": provider,
        "owner": str(payload.get("owner") or "codetalk"),
        "status": str(payload.get("status") or "unknown"),
        "codetalk_callable": bool(payload.get("codetalk_callable", False)),
        "non_blocking": bool(payload.get("non_blocking", True)),
        "startup_probe_endpoint": str(diagnostics.get("startup_probe_endpoint") or ""),
        "health_endpoint": str(diagnostics.get("health_endpoint") or ""),
        "repo_path": repo_path,
        "unavailable_behavior": str(payload.get("unavailable_behavior") or ""),
        "next_check": _codetalk_provider_next_check(provider, diagnostics),
    }


def _codetalk_provider_next_check(provider: str, diagnostics: dict[str, Any]) -> str:
    endpoint = str(diagnostics.get("startup_probe_endpoint") or "")
    if endpoint:
        return f"POST {endpoint}?repo_path=<repo_path>"
    if provider == "local-search":
        return "Verify repo.path exists and is readable by the CodeTalk backend process."
    return "No startup probe is available for this provider."


def _agent_cli_readiness_payload(
    provider: str,
    payload: dict[str, Any],
    *,
    deployment_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    resolution = (
        diagnostics.get("command_resolution")
        if isinstance(diagnostics.get("command_resolution"), dict)
        else {}
    )
    status = str(resolution.get("status") or payload.get("status") or "unknown")
    readiness = {
        "provider": provider,
        "owner": "agent_cli",
        "status": status,
        "configured_command": str(resolution.get("configured_command") or ""),
        "command": str(resolution.get("command") or ""),
        "used_fallback": bool(resolution.get("used_fallback", False)),
        "reason": str(resolution.get("reason") or ""),
        "attempt_count": int(resolution.get("attempt_count") or 0),
        "startup_probe_endpoint": str(diagnostics.get("startup_probe_endpoint") or ""),
        "health_endpoint": str(diagnostics.get("health_endpoint") or ""),
        "manual_probe_command": str(diagnostics.get("manual_probe_command") or ""),
        "credential_boundary": str(payload.get("credential_boundary") or ""),
        "unavailable_behavior": str(payload.get("unavailable_behavior") or ""),
    }
    if deployment_evidence:
        readiness["deployment_evidence"] = deployment_evidence
        readiness["deployment_evidence_conflict"] = _deployment_evidence_conflicts_with_status(
            status=status,
            deployment_evidence=deployment_evidence,
        )
    return readiness


def _deployment_evidence_by_provider(
    deployment_evidence: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in deployment_evidence:
        if not isinstance(item, dict):
            continue
        provider = _provider_from_deployment_evidence(item)
        if not provider or provider in result:
            continue
        result[provider] = _deployment_evidence_summary(item)
    return result


def _provider_from_deployment_evidence(item: dict[str, Any]) -> str:
    provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
    provider = str(provenance.get("provider") or item.get("symbol") or "").strip()
    if provider:
        return provider
    subject_key = str(item.get("subject_key") or "").strip()
    if ":" in subject_key:
        return subject_key.split(":", 1)[0].strip()
    return ""


def _deployment_evidence_summary(item: dict[str, Any]) -> dict[str, Any]:
    provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
    return {
        "provider": _provider_from_deployment_evidence(item),
        "evidence_id": str(item.get("evidence_id") or ""),
        "kind": str(item.get("kind") or ""),
        "subject_key": str(item.get("subject_key") or ""),
        "evidence_status": str(item.get("status") or ""),
        "evidence_source": str(item.get("source") or ""),
        "probe_id": str(provenance.get("probe_id") or ""),
        "task_probe_status": str(provenance.get("task_probe_status") or ""),
        "path": str(item.get("path") or ""),
        "reason": str(item.get("reason") or ""),
    }


def _deployment_evidence_conflicts_with_status(
    *,
    status: str,
    deployment_evidence: dict[str, Any],
) -> bool:
    if status not in {"unavailable", "missing_command", "unknown_provider", "error"}:
        return False
    return (
        deployment_evidence.get("task_probe_status") == "ready"
        or deployment_evidence.get("evidence_status") == "accepted"
    )


def build_codetalk_provider_snapshot() -> dict[str, dict[str, Any]]:
    providers = [
        _codetalk_provider_snapshot_item(
            provider="local-search",
            display_name="Local repo search",
            owner="codetalk_builtin",
            status="available",
            capabilities={
                "provider": "local-search",
                "supports_mcp": False,
                "mcp_profiles": [],
                "supports_artifact_export": False,
                "supports_json_output": True,
                "prompt_transport": "none",
                "supports_source_discovery": True,
                "supports_call_graph": False,
                "supports_source_slices": True,
                "supports_black_box_terms": False,
            },
            unavailable_behavior="Always available when the repository path is readable.",
        ),
        _codetalk_provider_snapshot_item(
            provider="gitnexus",
            display_name="GitNexus",
            owner="codetalk_index",
            status="configured" if getattr(settings, "gitnexus_base_url", "") else "missing_config",
            capabilities={
                "provider": "gitnexus",
                "supports_mcp": False,
                "mcp_profiles": [],
                "supports_artifact_export": False,
                "supports_json_output": True,
                "prompt_transport": "http",
                "supports_source_discovery": True,
                "supports_call_graph": True,
                "supports_source_slices": False,
                "supports_black_box_terms": False,
            },
            unavailable_behavior="CodeTalk records GitNexus as unavailable and continues with local search, CGC, memory, and Agent CLI providers.",
        ),
        _codetalk_provider_snapshot_item(
            provider="cgc",
            display_name="CGC",
            owner="codetalk_index",
            status="configured" if getattr(settings, "cgc_base_url", "") else "missing_config",
            capabilities={
                "provider": "cgc",
                "supports_mcp": False,
                "mcp_profiles": [],
                "supports_artifact_export": False,
                "supports_json_output": True,
                "prompt_transport": "http_or_cli",
                "supports_source_discovery": True,
                "supports_call_graph": True,
                "supports_source_slices": False,
                "supports_black_box_terms": False,
            },
            unavailable_behavior="CodeTalk records CGC as unavailable and continues with local search, GitNexus, memory, and Agent CLI providers.",
        ),
        _codetalk_provider_snapshot_item(
            provider="evidence-memory",
            display_name="Evidence Memory",
            owner="codetalk_memory",
            status="available",
            capabilities={
                "provider": "evidence-memory",
                "supports_mcp": False,
                "mcp_profiles": [],
                "supports_artifact_export": False,
                "supports_json_output": True,
                "prompt_transport": "none",
                "supports_source_discovery": True,
                "supports_call_graph": False,
                "supports_source_slices": True,
                "supports_black_box_terms": False,
            },
            unavailable_behavior="If no memory facts exist, CodeTalk continues with live discovery providers.",
        ),
        _codetalk_provider_snapshot_item(
            provider="semantic-library",
            display_name="Semantic Test Library",
            owner="codetalk_memory",
            status="available",
            capabilities={
                "provider": "semantic-library",
                "supports_mcp": False,
                "mcp_profiles": [],
                "supports_artifact_export": False,
                "supports_json_output": True,
                "prompt_transport": "none",
                "supports_source_discovery": False,
                "supports_call_graph": False,
                "supports_source_slices": False,
                "supports_black_box_terms": True,
            },
            unavailable_behavior="If no semantic cases match, black-box generation falls back to validated entries and source evidence.",
        ),
    ]
    return {item["provider"]: item for item in providers}


def _codetalk_provider_snapshot_item(
    *,
    provider: str,
    display_name: str,
    owner: str,
    status: str,
    capabilities: dict[str, Any],
    unavailable_behavior: str,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "display_name": display_name,
        "owner": owner,
        "status": status,
        "non_blocking": True,
        "codetalk_callable": status in {"available", "configured"},
        "agent_owned": False,
        "command": [],
        "fallback_commands": [],
        "readonly_args": [],
        "command_hint_env": "",
        "capabilities": capabilities,
        "credential_boundary": "CodeTalk owns this provider and validates any materialized evidence locally.",
        "unavailable_behavior": unavailable_behavior,
        "diagnostics": _codetalk_provider_diagnostics(
            provider=provider,
            owner=owner,
            status=status,
            codetalk_callable=status in {"available", "configured"},
        ),
    }


def build_agent_cli_provider_diagnostics(provider: str, spec: Any) -> dict[str, Any]:
    """Return side-effect-free launch diagnostics for Agent-owned providers."""
    command_text = str(getattr(spec, "command", "") or "").strip()
    fallback_texts = [
        str(command).strip()
        for command in getattr(spec, "fallback_commands", []) or []
        if str(command).strip()
    ]
    prompt_transport = str(getattr(spec, "prompt_transport", "") or "auto").strip() or "auto"
    command_hint_env = str(getattr(spec, "command_hint_env", "") or "").strip()
    env_hints = _redact_provider_env_hints(
        getattr(spec, "env_hints", {}) or {}
    )
    manual_probe = (
        f"POST /api/tools/{provider}/startup-probe with repo_path, then verify the "
        f"same backend shell can launch: {command_text or provider}"
    )
    hints = [
        (
            "PowerShell profile, PATH, and service account environment may differ from "
            "an interactive terminal; verify the backend process can resolve the command."
        ),
        (
            "For CCR/Claude Code Router, prefer configuring claude_code_command as "
            "`ccr code` and run the startup probe to validate non-interactive launch mode."
        ),
        (
            "MCP credentials belong to the Agent CLI process. CodeTalk passes task "
            "bundles and validates artifacts; it does not fetch protected MR data itself."
        ),
    ]
    if command_hint_env:
        hints.append(f"Override this provider with {command_hint_env} when backend PATH differs.")
    diagnostics = {
        "health_endpoint": f"/api/tools/{provider}/health",
        "startup_probe_endpoint": f"/api/tools/{provider}/startup-probe",
        "configured_command_text": command_text,
        "fallback_command_texts": fallback_texts,
        "env_hint_keys": sorted(env_hints),
        "env_hints": env_hints,
        "prompt_transport": prompt_transport,
        "startup_probe_transport": prompt_transport,
        "manual_probe_command": manual_probe,
        "probe_recipe": _agent_cli_probe_recipe(
            provider=provider,
            command_text=command_text,
            fallback_texts=fallback_texts,
            command_hint_env=command_hint_env,
            env_hint_keys=sorted(env_hints),
        ),
        "mcp_credentials_owner": "agent_cli",
        "codetalk_validation_role": (
            "CodeTalk treats Agent output as candidate evidence until local artifact, "
            "path, schema, or source-slice validation accepts it."
        ),
        "troubleshooting": hints,
    }
    command_resolution = _agent_cli_command_resolution(provider, command_text, fallback_texts)
    if command_resolution:
        diagnostics["command_resolution"] = command_resolution
    return diagnostics


def _redact_provider_env_hints(environment: dict[str, Any]) -> dict[str, str]:
    return {
        str(key): (
            "<redacted>"
            if _SENSITIVE_ENV_KEY_RE.search(str(key))
            else _redact_provider_env_value(value)
        )
        for key, value in sorted(environment.items())
    }


def _redact_provider_env_value(value: Any) -> str:
    text = str(value)
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return redact_agent_diagnostic_text(text)
    redacted = _redact_provider_json_value(payload)
    return json.dumps(redacted, ensure_ascii=False, sort_keys=True)


def _redact_provider_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "<redacted>"
                if _SENSITIVE_ENV_KEY_RE.search(str(key))
                else _redact_provider_json_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_provider_json_value(item) for item in value]
    if isinstance(value, str):
        return redact_agent_diagnostic_text(value)
    return value


def _agent_cli_probe_recipe(
    *,
    provider: str,
    command_text: str,
    fallback_texts: list[str],
    command_hint_env: str,
    env_hint_keys: list[str] | None = None,
) -> dict[str, Any]:
    env_name = command_hint_env or f"{provider.upper().replace('-', '_')}_COMMAND"
    environment_checks = ["PATH"]
    if provider == "claude-code":
        environment_checks.extend(["CCR_CONFIG_PATH", "CLAUDE_CODE_CONFIG_PATH"])
    for key in env_hint_keys or []:
        if key not in environment_checks:
            environment_checks.append(key)
    return {
        "startup_probe_http": f"POST /api/tools/{provider}/startup-probe?repo_path=<repo_path>",
        "backend_command": command_text or provider,
        "fallback_commands": list(fallback_texts),
        "command_env": env_name,
        "command_env_example": f"{env_name}={command_text or provider}",
        "environment_checks": environment_checks,
        "notes": [
            "Run the startup probe from the CodeTalk UI first; it uses the backend process environment.",
            "If your terminal works but CodeTalk does not, configure the full executable path with command_env.",
            "对于 Agent 持有的 MCP，请把凭证保留在 Agent CLI 环境里；CodeTalk 只下发任务包。",
        ],
    }


def _agent_cli_command_resolution(
    provider: str,
    command_text: str,
    fallback_texts: list[str],
) -> dict[str, Any]:
    if not command_text:
        return {
            "status": "missing_command",
            "reason": "provider command is not configured",
            "used_fallback": False,
            "attempt_count": 0,
            "attempts": [],
        }
    try:
        health = check_provider_health(provider, command_text, fallback_commands=fallback_texts)
    except Exception as exc:
        return {
            "status": "error",
            "reason": redact_agent_diagnostic_text(str(exc)),
            "used_fallback": False,
            "attempt_count": 0,
            "attempts": [],
        }
    if not isinstance(health, dict):
        return {}
    attempts = [
        _agent_cli_command_resolution_attempt(item)
        for item in health.get("attempts") or []
        if isinstance(item, dict)
    ]
    return {
        "status": str(health.get("status") or ""),
        "configured_command": redact_agent_diagnostic_text(
            str(health.get("configured_command") or command_text)
        ),
        "command": redact_agent_diagnostic_text(str(health.get("command") or "")),
        "path": redact_agent_diagnostic_text(str(health.get("path") or "")),
        "launch_kind": str(health.get("launch_kind") or ""),
        "used_fallback": bool(health.get("used_fallback", False)),
        "reason": redact_agent_diagnostic_text(str(health.get("reason") or "")),
        "attempt_count": len(attempts),
        "attempts": attempts,
        "diagnostic": _agent_cli_command_resolution_diagnostic(health.get("diagnostic")),
    }


def _agent_cli_command_resolution_attempt(item: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "command",
        "status",
        "reason",
        "executable",
        "argv",
        "configured_argv",
        "path",
        "launch_kind",
        "config_hint",
        "profile_config_path",
        "shell_path",
        "diagnostic",
        "resolution",
    )
    result: dict[str, Any] = {}
    for key in keys:
        value = item.get(key)
        if value is None:
            continue
        if key in {"argv", "configured_argv"} and isinstance(value, list):
            result[key] = [
                redact_agent_diagnostic_text(str(part))
                for part in value
            ]
            continue
        if key == "diagnostic" and isinstance(value, dict):
            result[key] = _agent_cli_command_resolution_diagnostic(value)
            continue
        if key == "resolution" and isinstance(value, dict):
            result[key] = _agent_cli_command_resolution_detail(value)
            continue
        result[key] = redact_agent_diagnostic_text(str(value)) if isinstance(value, str) else value
    return result


def _agent_cli_command_resolution_detail(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, item in value.items():
        if item is None:
            continue
        if isinstance(item, str):
            result[str(key)] = redact_agent_diagnostic_text(item)
        elif isinstance(item, list):
            result[str(key)] = [
                redact_agent_diagnostic_text(str(part))
                for part in item
            ]
        elif isinstance(item, dict):
            result[str(key)] = _agent_cli_command_resolution_detail(item)
        elif isinstance(item, (int, float, bool)):
            result[str(key)] = item
        else:
            result[str(key)] = redact_agent_diagnostic_text(str(item))
    return result


def _agent_cli_command_resolution_diagnostic(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key in (
        "cwd",
        "summary",
        "command_hint_env",
        "command_hint",
        "path_entry_count",
        "path_entries",
        "checked_common_dirs",
    ):
        item = value.get(key)
        if item is None:
            continue
        if isinstance(item, str):
            result[key] = redact_agent_diagnostic_text(item)
        elif isinstance(item, list):
            result[key] = [
                redact_agent_diagnostic_text(str(part))
                for part in item
            ]
        elif isinstance(item, (int, float, bool)):
            result[key] = item
        else:
            result[key] = redact_agent_diagnostic_text(str(item))
    return result


def _unknown_agent_cli_provider_diagnostics(provider: str) -> dict[str, Any]:
    return {
        "health_endpoint": f"/api/tools/{provider}/health",
        "startup_probe_endpoint": f"/api/tools/{provider}/startup-probe",
        "configured_command_text": "",
        "fallback_command_texts": [],
        "prompt_transport": "",
        "startup_probe_transport": "",
        "manual_probe_command": f"Configure external_agent_custom_providers for {provider}.",
        "probe_recipe": {
            "startup_probe_http": f"POST /api/tools/{provider}/startup-probe?repo_path=<repo_path>",
            "backend_command": provider,
            "fallback_commands": [],
            "command_env": "EXTERNAL_AGENT_CUSTOM_PROVIDERS",
            "command_env_example": (
                f'EXTERNAL_AGENT_CUSTOM_PROVIDERS=[{{"id":"{provider}",'
                f'"command":"{provider} run","prompt_transport":"stdin"}}]'
            ),
            "environment_checks": ["PATH"],
            "notes": [
                "Provider is referenced by a workflow but missing from CodeTalk settings.",
                "Add command and prompt_transport before running the startup probe.",
            ],
        },
        "mcp_credentials_owner": "agent_cli",
        "codetalk_validation_role": "No Agent output can be trusted until the provider is configured and artifacts validate.",
        "troubleshooting": [
            "Provider is referenced by a workflow but missing from CodeTalk settings.",
            "Add it to external_agent_custom_providers with command and prompt_transport.",
        ],
    }


def _codetalk_provider_diagnostics(
    *,
    provider: str,
    owner: str,
    status: str,
    codetalk_callable: bool,
) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "owner": owner,
        "status": status,
        "codetalk_callable": codetalk_callable,
        "health_endpoint": "",
        "startup_probe_endpoint": "",
        "credential_boundary": "CodeTalk owns this provider and uses backend credentials/configuration.",
        "troubleshooting": [],
    }
    if provider in {"gitnexus", "cgc"}:
        diagnostics["health_endpoint"] = f"/api/tools/{provider}/health"
        diagnostics["startup_probe_endpoint"] = f"/api/tools/{provider}/startup-probe"
        diagnostics["troubleshooting"] = [
            f"Run the startup probe for {provider} with repo_path before trusting graph/index coverage.",
            "Unavailable index providers are non-blocking; local search and Agent CLI discovery continue.",
        ]
    elif provider == "local-search":
        diagnostics["troubleshooting"] = [
            "Requires only a readable repo_path; failures usually mean the task repository path is wrong."
        ]
    elif provider == "evidence-memory":
        diagnostics["troubleshooting"] = [
            "No matches means no prior validated facts, not an infrastructure failure."
        ]
    elif provider == "semantic-library":
        diagnostics["troubleshooting"] = [
            "No matches means imported test semantics do not cover this module or feature yet."
        ]
    return diagnostics


def build_context_discovery_decision(
    *,
    agent_instructions: dict[str, Any],
    provider_snapshot: dict[str, Any],
) -> dict[str, Any]:
    requested_files = _instruction_files_requesting_fast_context(agent_instructions)
    codetalk_callable = bool(
        getattr(settings, "context_discovery_enabled", True)
        and getattr(settings, "fast_context_enabled", True)
        and getattr(settings, "fast_context_backend_bridge_enabled", False)
    )
    providers = provider_snapshot.get("providers") or {}
    steps = provider_snapshot.get("steps") or {}
    agent_mcp_providers = [
        provider
        for provider, payload in providers.items()
        if isinstance(payload, dict)
        and bool((payload.get("capabilities") or {}).get("supports_mcp"))
    ]
    agent_steps_with_mcp_profile = [
        step_id
        for step_id, payload in steps.items()
        if isinstance(payload, dict) and str(payload.get("mcp_profile") or "").strip()
    ]
    warnings: list[str] = []
    if requested_files and not codetalk_callable:
        if not getattr(settings, "context_discovery_enabled", True):
            warnings.append("fast-context requested by AGENTS.md but context discovery is disabled")
        elif not getattr(settings, "fast_context_enabled", True):
            warnings.append("fast-context requested by AGENTS.md but provider is disabled")
        else:
            warnings.append("fast-context requested by AGENTS.md but backend MCP bridge is unavailable")
    if requested_files and not agent_mcp_providers and not agent_steps_with_mcp_profile:
        warnings.append("no Agent CLI step advertises MCP support or an MCP profile")
    return {
        "fast-context": {
            "requested_by_agent_instructions": bool(requested_files),
            "requested_by_files": requested_files,
            "codetalk_provider": "fast-context",
            "codetalk_callable": codetalk_callable,
            "codetalk_settings": {
                "context_discovery_enabled": bool(getattr(settings, "context_discovery_enabled", True)),
                "fast_context_enabled": bool(getattr(settings, "fast_context_enabled", True)),
                "fast_context_backend_bridge_enabled": bool(
                    getattr(settings, "fast_context_backend_bridge_enabled", False)
                ),
            },
            "fallback_path": [
                "local_search",
                "gitnexus",
                "cgc",
                "agent_cli",
            ],
            "agent_cli_mcp_possible": bool(agent_mcp_providers or agent_steps_with_mcp_profile),
            "agent_cli_mcp_providers": agent_mcp_providers,
            "agent_cli_mcp_steps": agent_steps_with_mcp_profile,
            "agent_cli_credential_boundary": (
                "Agent CLI may use its own MCP credentials; CodeTalk validates only returned artifacts."
            ),
            "warnings": warnings,
        }
    }


def build_context_artifact_payloads(
    *,
    context_bundle: dict[str, Any],
    context_discovery_decision: dict[str, Any],
    evidence_memory_configured: bool,
    semantic_library_configured: bool,
) -> dict[str, Any]:
    query = str(context_bundle.get("query") or "")
    evidence = [
        item for item in context_bundle.get("evidence") or []
        if isinstance(item, dict)
    ]
    deployment_evidence = [
        item for item in context_bundle.get("deployment_evidence") or []
        if isinstance(item, dict)
    ]
    semantic_cases = [
        item for item in context_bundle.get("semantic_cases") or []
        if isinstance(item, dict)
    ]
    local_source_context = (
        context_bundle.get("local_source_context")
        if isinstance(context_bundle.get("local_source_context"), dict)
        else {}
    )
    local_source_files = [
        item for item in local_source_context.get("files") or []
        if isinstance(item, dict)
    ]
    memory_retrieval = {
        "provider": "evidence-memory",
        "query": query,
        "retrieved_count": len(evidence),
        "deployment_retrieved_count": len(deployment_evidence),
        "limit": (context_bundle.get("limits") or {}).get("evidence"),
        "authority_rule": (
            "retrieval is navigation only; source evidence requires validated source_slices "
            "or current local source files"
        ),
        "items": [
            {
                "evidence_id": item.get("evidence_id") or "",
                "kind": item.get("kind") or "",
                "subject_key": item.get("subject_key") or "",
                "status": item.get("status") or "",
                "source": item.get("source") or "",
                "source_read_status": item.get("source_read_status") or "no_source_slices",
                "usable_as_source_evidence": bool(item.get("usable_as_source_evidence")),
                "source_slice_count": len(item.get("source_slices") or []),
                "reuse_reason": _memory_reuse_reason(item),
                "source_slice_refs": _source_slice_refs(item.get("source_slices") or []),
            }
            for item in evidence
        ],
        "deployment_items": [
            {
                "evidence_id": item.get("evidence_id") or "",
                "kind": item.get("kind") or "",
                "subject_key": item.get("subject_key") or "",
                "status": item.get("status") or "",
                "source": item.get("source") or "",
                "symbol": item.get("symbol") or "",
                "path": item.get("path") or "",
                "reuse_reason": _deployment_memory_reuse_reason(item),
                "provenance": item.get("provenance") or {},
            }
            for item in deployment_evidence
        ],
    }
    reads: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for item in evidence:
        evidence_id = str(item.get("evidence_id") or "")
        events.append({
            "event": "memory_retrieved",
            "provider": "evidence-memory",
            "evidence_id": evidence_id,
            "subject_key": item.get("subject_key") or "",
            "usable_as_source_evidence": bool(item.get("usable_as_source_evidence")),
            "reuse_reason": _memory_reuse_reason(item),
        })
        for source_slice in item.get("source_slices") or []:
            if not isinstance(source_slice, dict):
                continue
            verified = source_slice.get("integrity_status") == "verified_current"
            read = {
                "event": "source_slice_attached" if verified else "source_slice_stale",
                "evidence_id": evidence_id,
                "slice_id": source_slice.get("slice_id") or "",
                "file_path": source_slice.get("file_path") or "",
                "start_line": source_slice.get("start_line"),
                "end_line": source_slice.get("end_line"),
                "sha256": source_slice.get("sha256") or "",
                "current_sha256": source_slice.get("current_sha256") or "",
                "status": "validated_source_slice" if verified else "stale_source_slice",
                "validation_error": source_slice.get("validation_error") or "",
                "excerpt_chars": len(str(source_slice.get("excerpt") or "")),
            }
            reads.append(read)
            events.append(read)
    for item in deployment_evidence:
        events.append({
            "event": "deployment_evidence_retrieved",
            "provider": "evidence-memory",
            "evidence_id": item.get("evidence_id") or "",
            "kind": item.get("kind") or "",
            "subject_key": item.get("subject_key") or "",
            "status": item.get("status") or "",
            "source": item.get("source") or "",
            "reuse_reason": _deployment_memory_reuse_reason(item),
        })
    for item in semantic_cases:
        events.append({
            "event": "semantic_case_retrieved",
            "provider": "semantic-library",
            "semantic_id": item.get("semantic_id") or "",
            "case_id": item.get("case_id") or "",
            "terms": item.get("terms") or [],
            "reuse_reason": "query matched semantic library case; use terms to align black-box wording",
        })
    for item in local_source_files:
        read = {
            "event": "local_source_file_read",
            "provider": str(local_source_context.get("provider") or "local-source-search"),
            "file_path": item.get("file_path") or "",
            "start_line": item.get("start_line"),
            "end_line": item.get("end_line"),
            "sha256": item.get("sha256") or "",
            "current_sha256": item.get("sha256") or "",
            "status": "validated_source_file",
            "symbols": item.get("symbols") or [],
            "excerpt_chars": len(str(item.get("excerpt") or "")),
            "matched_terms": item.get("matched_terms") or [],
            "reason": "current local source excerpt attached during task preparation",
        }
        reads.append(read)
        events.append({
            **read,
            "reuse_reason": "current local source file was scanned and hash-validated during task preparation",
        })
    source_read_chain = {
        "query": query,
        "reads": reads,
        "read_count": len(reads),
        "rejected": [],
        "authority_rule": (
            "validated source slices or current local source files may support source evidence"
        ),
    }
    evidence_consumption_trajectory = {
        "query": query,
        "task_phase": "prepare",
        "scoring_policy": "navigation_only_not_authority",
        "events": events,
    }
    degraded_retrieval = {
        "query": query,
        "non_blocking": True,
        "degraded": _degraded_retrieval_items(
            context_bundle=context_bundle,
            context_discovery_decision=context_discovery_decision,
            evidence_memory_configured=evidence_memory_configured,
            semantic_library_configured=semantic_library_configured,
        ),
    }
    return {
        "memory_retrieval": memory_retrieval,
        "source_read_chain": source_read_chain,
        "evidence_consumption_trajectory": evidence_consumption_trajectory,
        "degraded_retrieval": degraded_retrieval,
    }


def build_black_box_generation_policy(*, context_bundle: dict[str, Any]) -> dict[str, Any]:
    semantic_cases = [
        item for item in context_bundle.get("semantic_cases") or []
        if isinstance(item, dict)
    ]
    evidence_items = [
        item for item in context_bundle.get("evidence") or []
        if isinstance(item, dict)
    ]
    semantic_terms: list[dict[str, Any]] = []
    for item in semantic_cases:
        terms = [str(term) for term in item.get("terms") or [] if str(term)]
        if not terms:
            continue
        semantic_terms.append({
            "case_id": str(item.get("case_id") or ""),
            "feature": str(item.get("feature") or ""),
            "module": str(item.get("module") or ""),
            "terms": terms,
            "test_level": str(item.get("test_level") or ""),
            "reuse_rule": "terminology_only_not_source_truth",
        })
    evidence_refs = _unique_strings(
        str(item.get("evidence_id") or "")
        for item in evidence_items
        if str(item.get("evidence_id") or "")
    )
    evidence_subjects = _unique_strings(
        str(item.get("subject_key") or item.get("path") or item.get("symbol") or "")
        for item in evidence_items
        if str(item.get("subject_key") or item.get("path") or item.get("symbol") or "")
    )
    source_slice_count = sum(
        1
        for item in evidence_items
        for source_slice in (item.get("source_slices") or [])
        if isinstance(source_slice, dict)
    )
    return {
        "provider": "semantic-library",
        "query": str(context_bundle.get("query") or ""),
        "semantic_terms": semantic_terms,
        "semantic_case_count": len(semantic_cases),
        "semantic_term_count": sum(len(item["terms"]) for item in semantic_terms),
        "evidence_memory_ref_count": len(evidence_refs),
        "evidence_memory_refs": evidence_refs[:20],
        "evidence_memory_subjects": evidence_subjects[:20],
        "evidence_memory_source_slice_count": source_slice_count,
        "authority_rule": (
            "semantic-library matches may shape black-box wording but cannot prove source behavior or entry reachability"
        ),
        "evidence_memory_authority_rule": (
            "Evidence Memory can provide prior validated context and source-slice references "
            "but cannot by itself prove external reachability for a black-box case"
        ),
        "allowed_uses": [
            "black_box_case_wording",
            "test_taxonomy_alignment",
            "observable_assertion_style",
            "source_context_hint",
            "prior_evidence_traceability",
        ],
        "must_not_use_semantics_as": [
            "source_evidence",
            "entry_verification",
            "artifact_validation",
        ],
        "must_not_use_evidence_memory_as": [
            "entry_verification",
            "external_reachability_proof",
            "artifact_validation_without_current_files",
        ],
    }


def _memory_reuse_reason(item: dict[str, Any]) -> str:
    if bool(item.get("usable_as_source_evidence")):
        return (
            "query matched prior evidence; source slices are attached and may be used as source evidence"
        )
    if item.get("source_slices"):
        return "query matched prior evidence; navigation only because source slices are stale or unverified"
    return "query matched prior evidence; navigation only because no source slices are attached"


def _deployment_memory_reuse_reason(item: dict[str, Any]) -> str:
    return (
        "deployment evidence describes Agent provider readiness; "
        "use for routing and diagnostics only"
    )


def _source_slice_refs(source_slices: list[Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for source_slice in source_slices:
        if not isinstance(source_slice, dict):
            continue
        refs.append({
            "slice_id": source_slice.get("slice_id") or "",
            "file_path": source_slice.get("file_path") or "",
            "start_line": source_slice.get("start_line"),
            "end_line": source_slice.get("end_line"),
            "sha256": source_slice.get("sha256") or "",
        })
    return refs


def _degraded_retrieval_items(
    *,
    context_bundle: dict[str, Any],
    context_discovery_decision: dict[str, Any],
    evidence_memory_configured: bool,
    semantic_library_configured: bool,
) -> list[dict[str, Any]]:
    degraded: list[dict[str, Any]] = []
    fast_context = context_discovery_decision.get("fast-context") or {}
    if (
        isinstance(fast_context, dict)
        and fast_context.get("requested_by_agent_instructions")
        and not fast_context.get("codetalk_callable")
    ):
        settings_snapshot = fast_context.get("codetalk_settings") or {}
        if not settings_snapshot.get("context_discovery_enabled", True):
            reason = "context_discovery_disabled"
        elif not settings_snapshot.get("fast_context_enabled", True):
            reason = "provider_disabled"
        else:
            reason = "backend_mcp_bridge_unavailable"
        degraded.append({
            "provider": "fast-context",
            "reason": reason,
            "fallback_path": fast_context.get("fallback_path") or [],
            "warnings": fast_context.get("warnings") or [],
        })
    evidence = context_bundle.get("evidence") or []
    semantic_cases = context_bundle.get("semantic_cases") or []
    if not evidence_memory_configured:
        degraded.append({
            "provider": "evidence-memory",
            "reason": "store_not_configured",
            "fallback_path": ["local_search", "gitnexus", "cgc", "agent_cli"],
        })
    elif not evidence:
        degraded.append({
            "provider": "evidence-memory",
            "reason": "no_matching_evidence",
            "fallback_path": ["local_search", "gitnexus", "cgc", "agent_cli"],
        })
    if not semantic_library_configured:
        degraded.append({
            "provider": "semantic-library",
            "reason": "store_not_configured",
            "fallback_path": ["validated_entries", "source_evidence", "agent_cli"],
        })
    elif not semantic_cases:
        degraded.append({
            "provider": "semantic-library",
            "reason": "no_matching_cases",
            "fallback_path": ["validated_entries", "source_evidence", "agent_cli"],
        })
    return degraded


def _instruction_files_requesting_fast_context(agent_instructions: dict[str, Any]) -> list[str]:
    requested: list[str] = []
    for item in agent_instructions.get("files") or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").lower()
        if "fast-context" not in content and "fast_context" not in content:
            continue
        relative_path = str(item.get("relative_path") or item.get("path") or "").strip()
        if relative_path:
            requested.append(relative_path)
    return requested


def collect_agent_instructions(
    *,
    repo_path: str | Path,
    input_snapshot: dict[str, Any],
    max_chars_per_file: int = 24000,
) -> dict[str, Any]:
    repo_root = Path(repo_path)
    files: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        root = repo_root.resolve()
    except OSError:
        return {
            "files": files,
            "warnings": ["repo_path could not be resolved"],
            "policy": _agent_instruction_policy(),
        }
    if not root.exists() or not root.is_dir():
        return {
            "files": files,
            "warnings": ["repo_path is not an existing directory"],
            "policy": _agent_instruction_policy(),
        }

    seen: set[Path] = set()
    for candidate in _agent_instruction_candidates(root, input_snapshot):
        try:
            path = candidate.resolve()
        except OSError:
            continue
        if path in seen or not _is_within(path, root):
            continue
        seen.add(path)
        if not path.exists() or not path.is_file():
            continue
        data = path.read_bytes()
        content = data.decode("utf-8", errors="replace")
        truncated = len(content) > max_chars_per_file
        files.append({
            "relative_path": path.relative_to(root).as_posix(),
            "path": str(path),
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
            "content": content[:max_chars_per_file],
            "truncated": truncated,
        })
    return {
        "files": files,
        "warnings": warnings,
        "policy": _agent_instruction_policy(),
    }


def _agent_instruction_candidates(root: Path, input_snapshot: dict[str, Any]) -> list[Path]:
    candidates = [root / "AGENTS.md"]
    for hint in _input_path_hints(input_snapshot):
        path = Path(hint)
        if path.is_absolute():
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if not _is_within(resolved, root):
                continue
            relative = resolved.relative_to(root)
        else:
            relative = path
        if any(part in {"", ".", ".."} for part in relative.parts):
            continue
        current = root
        for part in relative.parts[:-1]:
            current = current / part
            candidates.append(current / "AGENTS.md")
        if len(relative.parts):
            try:
                is_directory = (root / relative).is_dir()
            except OSError:
                is_directory = False
            if is_directory:
                candidates.append(root / relative / "AGENTS.md")
    return candidates


def _input_path_hints(input_snapshot: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    for value in input_snapshot.values():
        if isinstance(value, str):
            if _looks_like_path(value):
                hints.append(value)
        elif isinstance(value, dict):
            for key in ("value", "path", "original_path", "copied_path", "filename"):
                item = value.get(key)
                if item and _looks_like_path(str(item)):
                    hints.append(str(item))
        elif isinstance(value, (list, tuple)):
            for item in value:
                text = str(item)
                if _looks_like_path(text):
                    hints.append(text)
    return hints


def _looks_like_path(value: str) -> bool:
    text = value.strip()
    if not text or ("/" not in text and "\\" not in text):
        return False
    if "\x00" in text or "\n" in text or "\r" in text:
        return False
    encoded = text.encode("utf-8", errors="surrogatepass")
    if len(encoded) > 4096:
        return False
    components = text.replace("\\", "/").split("/")
    return all(len(part.encode("utf-8", errors="surrogatepass")) <= 255 for part in components)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _agent_instruction_policy() -> dict[str, Any]:
    return {
        "scope": "task",
        "source": "repo_AGENTS_md",
        "preferred_code_locator": "fast-context",
        "fast_context_required": False,
        "unavailable_provider_behavior": "record warning and continue",
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def build_run_snapshot_v3(
    *,
    artifact_dir: Path,
    task_run_id: str,
    task_id: str,
    attempt_number: int,
    parent_task_run_id: str,
    workflow_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Create the immutable, task-owned V3 run contract.

    Individual component files remain materialized so the cockpit, harness and
    support tooling can consume them without parsing a monolith.  This schema
    is the one authoritative index: every immutable input to a run is named
    here and pinned to the bytes that were actually written before execution.
    Runtime status, retries and generated output deliberately stay outside it.
    """
    is_v3_contract = _is_v3_compiled_contract(workflow_snapshot)
    component_paths = {
        "workflow_definition": "workflow_snapshot.json",
        "input_snapshot": "input_snapshot.json",
        "input_consumption": "input_consumption_snapshot.json",
        "execution_profile": "execution_profile.json",
        "network_policy": "network_policy.json",
        "workflow_contract": "workflow_contract.json",
        "provider_capability": "provider_snapshot.json",
        "provider_readiness": "provider_readiness.json",
    }
    if is_v3_contract:
        component_paths["v3_runtime_contract"] = "compiled_definition.json"
        component_paths["agent_execution_descriptors"] = (
            "agent_execution_descriptors.json"
        )
    else:
        component_paths.update({
            "stage_specs": "stage_specs.json",
            "artifact_contract": "artifact_contract_v3.json",
            "quality_readiness": "quality_readiness.json",
        })
    # The root task bundle is intentionally absent.  It is a compatibility
    # projection which receives scheduler/retry runtime state after prepare;
    # treating it as immutable made ordinary V2 attempts fail their own guard.
    # A compiled execution plan, in contrast, is an explicit frozen component.
    if (artifact_dir / "compiled_plan.json").is_file():
        component_paths["execution_plan"] = "compiled_plan.json"
    components: dict[str, dict[str, str]] = {}
    for component_id, relative_path in component_paths.items():
        path = artifact_dir / relative_path
        if not path.is_file():
            raise RuntimeError(
                f"cannot freeze run snapshot; missing component: {relative_path}"
            )
        components[component_id] = {
            "path": relative_path,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return {
        "schema_version": 3,
        "snapshot_kind": "codetalk_run_snapshot",
        "execution_contract": {
            "compiled_contract_version": compiled_contract_version(workflow_snapshot),
        },
        "created_at": _now(),
        "identity": {
            "task_run_id": task_run_id,
            "task_id": task_id,
            "attempt_number": attempt_number,
            "parent_task_run_id": parent_task_run_id,
            "workflow_id": str(workflow_snapshot.get("id") or ""),
            "workflow_version": workflow_snapshot.get("version"),
        },
        "components": components,
    }


def refresh_run_snapshot_v3(artifact_dir: str | Path) -> dict[str, Any]:
    """Freeze post-prepare published-plan data before the task may execute.

    V2 task creation attaches the already-published compiled graph after the
    generic preparer has ingested inputs.  Materialize only that immutable
    graph as its own component; scheduler/retry fields in ``task_bundle`` are
    deliberately not part of the run-input integrity boundary.
    """
    root = Path(artifact_dir)
    existing = _read_json(root / "run_snapshot_v3.json")
    task_run = _read_json(root / "task_run.json")
    workflow_snapshot = _read_json(root / "workflow_snapshot.json")
    task_bundle = _read_json(root / "task_bundle.json")
    if not isinstance(task_run, dict) or not isinstance(workflow_snapshot, dict):
        raise RuntimeError("cannot refresh V3 run snapshot without prepared task artifacts")
    if isinstance(task_bundle, dict) and isinstance(task_bundle.get("compiled_plan"), dict):
        _write_json(root / "compiled_plan.json", task_bundle["compiled_plan"])
    if isinstance(task_bundle, dict) and isinstance(task_bundle.get("compiled_definition"), dict):
        _write_json(root / "compiled_definition.json", task_bundle["compiled_definition"])
    agent_descriptors_path = root / "agent_execution_descriptors.json"
    if not agent_descriptors_path.is_file():
        _write_json(
            agent_descriptors_path,
            {
                "schema_version": 1,
                "agent_runs": [
                    dict(item)
                    for item in task_run.get("agent_runs") or []
                    if isinstance(item, dict)
                ],
            },
        )
    prior_identity = existing.get("identity") if isinstance(existing, dict) else {}
    snapshot = build_run_snapshot_v3(
        artifact_dir=root,
        task_run_id=str(task_run.get("task_run_id") or ""),
        task_id=str(task_run.get("task_id") or ""),
        attempt_number=max(0, int(task_run.get("attempt_number") or 0)),
        parent_task_run_id=str(task_run.get("parent_task_run_id") or ""),
        workflow_snapshot=workflow_snapshot,
    )
    if isinstance(existing, dict) and str(existing.get("created_at") or ""):
        snapshot["created_at"] = str(existing["created_at"])
    if isinstance(prior_identity, dict) and prior_identity.get("workflow_id"):
        snapshot["identity"]["workflow_id"] = str(prior_identity["workflow_id"])
    _write_json(root / "run_snapshot_v3.json", snapshot)
    return snapshot


def validate_run_snapshot_v3(artifact_dir: str | Path) -> list[str]:
    """Verify that a prepared V3 run still refers to the frozen component bytes."""
    root = Path(artifact_dir)
    snapshot = _read_json(root / "run_snapshot_v3.json")
    if not isinstance(snapshot, dict):
        return ["运行快照缺失或无法读取：run_snapshot_v3.json"]
    if snapshot.get("schema_version") != 3 or snapshot.get("snapshot_kind") != "codetalk_run_snapshot":
        return ["运行快照版本不受支持：run_snapshot_v3.json"]
    components = snapshot.get("components")
    if not isinstance(components, dict) or not components:
        return ["运行快照未声明冻结组件：run_snapshot_v3.json"]
    errors: list[str] = []
    for component_id, descriptor in components.items():
        if not isinstance(descriptor, dict):
            errors.append(f"运行快照组件定义无效：{component_id}")
            continue
        relative_path = str(descriptor.get("path") or "").strip().replace("\\", "/")
        expected_sha256 = str(descriptor.get("sha256") or "").strip().lower()
        path = root / relative_path
        if (
            not relative_path
            or Path(relative_path).is_absolute()
            or ".." in Path(relative_path).parts
            or not path.is_file()
        ):
            errors.append(f"运行快照组件缺失：{component_id}（{relative_path or '未知路径'}）")
            continue
        actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if len(expected_sha256) != 64 or actual_sha256 != expected_sha256:
            errors.append(f"运行快照组件校验失败：{component_id}（{relative_path}）")
    return errors


class FrozenCompiledPlanAuthorityError(ValueError):
    """Raised when a frozen compiled plan cannot be safely accepted."""

    def __init__(self) -> None:
        super().__init__("Frozen compiled plan is unavailable or invalid.")


class FrozenV3ExecutionAuthorityError(ValueError):
    """Raised when a V3 attempt lacks a complete snapshot-authorized contract."""

    def __init__(self) -> None:
        super().__init__("Frozen V3 execution authority is unavailable or invalid.")


def load_frozen_v3_execution_authority(
    artifact_dir: str | Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Load every immutable value that may influence V3 execution."""
    required = {
        "v3_runtime_contract": "compiled_definition.json",
        "execution_plan": "compiled_plan.json",
        "input_snapshot": "input_snapshot.json",
        "agent_execution_descriptors": "agent_execution_descriptors.json",
    }
    try:
        if validate_run_snapshot_v3(artifact_dir):
            raise FrozenV3ExecutionAuthorityError()
        root = Path(artifact_dir)
        snapshot = _read_json(root / "run_snapshot_v3.json")
        components = snapshot.get("components") if isinstance(snapshot, dict) else None
        if not isinstance(components, dict):
            raise FrozenV3ExecutionAuthorityError()
        loaded: dict[str, Any] = {}
        for component_id, expected_path in required.items():
            descriptor = components.get(component_id)
            if not isinstance(descriptor, dict):
                raise FrozenV3ExecutionAuthorityError()
            relative_path = _normalized_snapshot_component_path(descriptor.get("path"))
            expected_sha256 = descriptor.get("sha256")
            if (
                relative_path != expected_path
                or not isinstance(expected_sha256, str)
                or len(expected_sha256) != 64
            ):
                raise FrozenV3ExecutionAuthorityError()
            component_bytes = (root / expected_path).read_bytes()
            if hashlib.sha256(component_bytes).hexdigest() != expected_sha256.lower():
                raise FrozenV3ExecutionAuthorityError()
            loaded[component_id] = json.loads(component_bytes)
        definition = loaded["v3_runtime_contract"]
        plan = loaded["execution_plan"]
        inputs = loaded["input_snapshot"]
        descriptors = loaded["agent_execution_descriptors"]
        if (
            not isinstance(definition, dict)
            or not isinstance(plan, dict)
            or not isinstance(inputs, dict)
            or not isinstance(descriptors, dict)
            or descriptors.get("schema_version") != 1
            or not isinstance(descriptors.get("agent_runs"), list)
            or any(not isinstance(item, dict) for item in descriptors["agent_runs"])
        ):
            raise FrozenV3ExecutionAuthorityError()
        if definition.get("compiled_contract_version") != 3 or plan.get("compiled_contract_version") != 3:
            raise FrozenV3ExecutionAuthorityError()
        return definition, plan, inputs, [dict(item) for item in descriptors["agent_runs"]]
    except FrozenV3ExecutionAuthorityError:
        raise
    except (OSError, TypeError, UnicodeError, json.JSONDecodeError, ValueError):
        raise FrozenV3ExecutionAuthorityError() from None


def load_frozen_compiled_plan(artifact_dir: str | Path) -> dict[str, Any]:
    """Return the snapshot-authorized compiled plan, or a safe failure.

    Consumers must use this instead of reading ``compiled_plan.json`` directly
    when the frozen snapshot is the execution authority.
    """
    try:
        if validate_run_snapshot_v3(artifact_dir):
            raise FrozenCompiledPlanAuthorityError()
        root = Path(artifact_dir)
        snapshot = _read_json(root / "run_snapshot_v3.json")
        components = snapshot.get("components") if isinstance(snapshot, dict) else None
        descriptor = components.get("execution_plan") if isinstance(components, dict) else None
        if not isinstance(descriptor, dict):
            raise FrozenCompiledPlanAuthorityError()
        relative_path = _normalized_snapshot_component_path(descriptor.get("path"))
        expected_sha256 = descriptor.get("sha256")
        if (
            relative_path != "compiled_plan.json"
            or not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
        ):
            raise FrozenCompiledPlanAuthorityError()
        plan_bytes = (root / "compiled_plan.json").read_bytes()
        if hashlib.sha256(plan_bytes).hexdigest() != expected_sha256.lower():
            raise FrozenCompiledPlanAuthorityError()
        plan = json.loads(plan_bytes)
        if not isinstance(plan, dict):
            raise FrozenCompiledPlanAuthorityError()
        return plan
    except FrozenCompiledPlanAuthorityError:
        raise
    except (OSError, TypeError, UnicodeError, json.JSONDecodeError, ValueError):
        raise FrozenCompiledPlanAuthorityError() from None


def _normalized_snapshot_component_path(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return posixpath.normpath(value.strip().replace("\\", "/"))


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _prepared_task_run_from_payload(payload: dict[str, Any]) -> PreparedWorkbenchTaskRun:
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    workflow_snapshot = dict(payload.get("workflow_snapshot") or {})
    task_bundle = dict(payload.get("task_bundle") or {})
    is_v3_contract = _is_v3_compiled_contract(workflow_snapshot, task_bundle=task_bundle)
    return PreparedWorkbenchTaskRun(
        task_run_id=str(payload["task_run_id"]),
        workflow_id=str(payload["workflow_id"]),
        workspace_id=str(payload["workspace_id"]),
        repo_path=str(payload["repo_path"]),
        artifact_dir=str(payload["artifact_dir"]),
        workflow_snapshot=workflow_snapshot,
        input_snapshot=dict(payload.get("input_snapshot") or {}),
        task_bundle=task_bundle,
        task_id=str(payload.get("task_id") or ""),
        attempt_number=max(0, int(payload.get("attempt_number") or 0)),
        parent_task_run_id=str(payload.get("parent_task_run_id") or ""),
        execution_status=_normalized_execution_status(
            payload.get("execution_status")
            or payload.get("status")
            or runtime.get("status")
            or ("queued" if is_v3_contract else "prepared"),
            v3=is_v3_contract,
        ),
        quality_status=_normalized_quality_status(payload.get("quality_status")),
        artifact_validation_status=_normalized_artifact_validation_status(
            payload.get("artifact_validation_status"),
            default="not_started" if is_v3_contract else "not_checked",
            v3=is_v3_contract,
        ),
        governance_status=_normalized_governance_status(
            payload.get("governance_status"),
            default="not_requested" if is_v3_contract else "not_started",
            v3=is_v3_contract,
        ),
        delivery_status=_normalized_delivery_status(
            payload.get("delivery_status"), v3=is_v3_contract
        ),
        started_at=str(payload.get("started_at") or runtime.get("started_at") or ""),
        completed_at=str(payload.get("completed_at") or runtime.get("completed_at") or ""),
        agent_runs=[
            dict(item) for item in payload.get("agent_runs") or []
            if isinstance(item, dict)
        ],
        created_at=str(payload.get("created_at") or ""),
    )


def _safe_segment(value: str) -> str:
    text = str(value or "").strip()
    if not text or text in {".", ".."} or ".." in text or not SAFE_RUNTIME_ID_RE.fullmatch(text):
        raise KeyError(value)
    return text


def _normalized_quality_status(value: Any) -> str:
    status = str(value or "").strip()
    if status == "not_evaluated" or not status:
        return "not_checked"
    return status if status in {"not_checked", "pending", "passed", "warning", "blocked"} else "not_checked"


def _normalized_execution_status(value: Any, *, v3: bool = False) -> str:
    status = str(value or "").strip().lower()
    if v3:
        return status if status in {
            "queued", "running", "waiting_for_input", "completed", "failed", "cancelled", "timed_out"
        } else "queued"
    if status in {"completed_empty", "needs_review", "needs_rework", "ok", "ready", "success"}:
        return "completed"
    if status in {"invalid", "error"}:
        return "failed"
    return status if status in {
        "prepared", "queued", "running", "completed", "partial", "failed", "cancelled", "interrupted"
    } else "prepared"


def _normalized_delivery_status(value: Any, *, v3: bool = False) -> str:
    status = str(value or "").strip()
    if v3:
        return status if status in {"pending", "ready", "blocked"} else "pending"
    if not status:
        return "none"
    return status if status in {"none", "partial", "complete"} else "none"


def _normalized_artifact_validation_status(
    value: Any, *, default: str, v3: bool = False
) -> str:
    status = str(value or "").strip()
    if v3:
        return status if status in {
            "not_requested", "not_started", "running", "passed", "failed"
        } else default
    return status if status in {
        "not_checked", "not_started", "pending", "passed", "warning", "blocked", "failed"
    } else default


def _normalized_governance_status(
    value: Any, *, default: str, v3: bool = False
) -> str:
    status = str(value or "").strip()
    if v3:
        return status if status in {
            "not_requested", "running", "passed", "warning", "failed", "waived"
        } else default
    return status if status in {
        "not_requested", "not_started", "pending", "passed", "warning", "blocked", "failed"
    } else default


def _is_v3_compiled_contract(
    workflow_snapshot: dict[str, Any], *, task_bundle: dict[str, Any] | None = None
) -> bool:
    bundle = task_bundle or {}
    raw = bundle.get("compiled_contract_version")
    if raw is None:
        raw = workflow_snapshot.get("compiled_contract_version")
    if raw is None:
        for key in ("compiled_definition", "compiled_plan"):
            candidate = bundle.get(key)
            if isinstance(candidate, dict) and candidate.get("compiled_contract_version") is not None:
                raw = candidate.get("compiled_contract_version")
                break
    # Any explicit frozen contract uses the V3 status axes, including unknown
    # versions that the Runner will reject.  Treating an unsupported version as
    # legacy would hide the fail-closed result behind old quality projections.
    return raw is not None and raw != ""


def _context_query_from_inputs(
    input_snapshot: dict[str, Any],
    *,
    query_hints: list[str] | None = None,
) -> str:
    parts: list[str] = [str(item) for item in query_hints or [] if str(item).strip()]
    for value in input_snapshot.values():
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for key in ("value", "text", "filename", "original_path", "path"):
                if value.get(key):
                    parts.append(str(value[key]))
            parsed_text_path = value.get("parsed_text_path")
            if parsed_text_path:
                parsed = _read_text_prefix(Path(str(parsed_text_path)), max_chars=4000)
                if parsed:
                    parts.append(parsed)
        elif isinstance(value, (list, tuple)):
            parts.extend(str(item) for item in value if str(item))
    query = " ".join(part.strip() for part in parts if part and part.strip())
    return " ".join(query.split())[:8000]


def _read_text_prefix(path: Path, *, max_chars: int) -> str:
    try:
        if not path.exists() or not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except OSError:
        return ""


def _evidence_item_payload(
    item: Any,
    *,
    source_slices: list[Any] | None = None,
    repo_path: str = "",
) -> dict[str, Any]:
    source_slice_payloads = [
        _source_slice_payload(source_slice, repo_path=repo_path)
        for source_slice in (source_slices or [])
    ]
    verified_source_slices = [
        source_slice for source_slice in source_slice_payloads
        if source_slice.get("integrity_status") == "verified_current"
    ]
    payload = {
        "evidence_id": item.evidence_id,
        "run_id": item.run_id,
        "workspace_id": item.workspace_id,
        "kind": item.kind,
        "subject_key": item.subject_key,
        "status": item.status,
        "source": item.source,
        "path": item.path,
        "symbol": item.symbol,
        "reason": item.reason,
        "confidence": item.confidence,
        "text": item.text,
        "provenance": item.provenance or {},
        "source_read_status": _source_read_status(source_slice_payloads),
        "usable_as_source_evidence": bool(verified_source_slices),
    }
    if source_slice_payloads:
        payload["source_slices"] = source_slice_payloads
    return payload


def _source_slice_payload(item: Any, *, repo_path: str = "") -> dict[str, Any]:
    integrity = _source_slice_integrity(
        repo_path=repo_path,
        file_path=str(item.file_path),
        expected_sha256=str(item.sha256),
    )
    return {
        "slice_id": item.slice_id,
        "evidence_id": item.evidence_id,
        "file_path": item.file_path,
        "start_line": item.start_line,
        "end_line": item.end_line,
        "sha256": item.sha256,
        "integrity_status": integrity["status"],
        "current_sha256": integrity["current_sha256"],
        "validation_error": integrity["validation_error"],
        "excerpt": item.excerpt,
        "created_at": item.created_at,
    }


def _source_read_status(source_slices: list[dict[str, Any]]) -> str:
    if not source_slices:
        return "no_source_slices"
    if any(item.get("integrity_status") == "verified_current" for item in source_slices):
        return "source_slices_attached"
    return "source_slices_stale"


def _source_slice_integrity(
    *,
    repo_path: str,
    file_path: str,
    expected_sha256: str,
) -> dict[str, str]:
    if not repo_path:
        return {
            "status": "repo_path_missing",
            "current_sha256": "",
            "validation_error": "repo_path_missing",
        }
    repo = Path(repo_path)
    try:
        repo_resolved = repo.resolve()
    except OSError:
        return {
            "status": "repo_unavailable",
            "current_sha256": "",
            "validation_error": "repo_unavailable",
        }
    candidate = Path(str(file_path or "").replace("\\", "/"))
    if candidate.is_absolute():
        path = candidate
    else:
        path = repo_resolved / candidate
    try:
        resolved = path.resolve()
    except OSError:
        return {
            "status": "file_missing",
            "current_sha256": "",
            "validation_error": "file_missing",
        }
    try:
        resolved.relative_to(repo_resolved)
    except ValueError:
        return {
            "status": "outside_repo",
            "current_sha256": "",
            "validation_error": "outside_repo",
        }
    if not resolved.exists() or not resolved.is_file():
        return {
            "status": "file_missing",
            "current_sha256": "",
            "validation_error": "file_missing",
        }
    try:
        current_sha = hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError:
        return {
            "status": "read_failed",
            "current_sha256": "",
            "validation_error": "read_failed",
        }
    if expected_sha256 and current_sha == expected_sha256:
        return {
            "status": "verified_current",
            "current_sha256": current_sha,
            "validation_error": "",
        }
    return {
        "status": "hash_mismatch",
        "current_sha256": current_sha,
        "validation_error": "hash_mismatch",
    }


def _semantic_case_payload(item: Any) -> dict[str, Any]:
    return {
        "semantic_id": item.semantic_id,
        "case_id": item.case_id,
        "feature": item.feature,
        "module": item.module,
        "scenario": item.scenario,
        "preconditions": list(item.preconditions),
        "actions": list(item.actions),
        "expected": list(item.expected),
        "test_level": item.test_level,
        "interface": item.interface,
        "terms": list(item.terms),
        "assertion_style": item.assertion_style,
        "tags": list(item.tags),
        "source_ref": item.source_ref,
        "status": item.status,
    }
