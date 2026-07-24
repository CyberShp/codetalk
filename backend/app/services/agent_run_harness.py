"""Agent run and artifact validation harness for CodeTalk workflows."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable

from app.config import settings
from app.services.agent_cli_bridge import (
    AGENT_ANSWER_DELTA_PREFIX,
    AGENT_FINAL_ANSWER_PREFIX,
    _decode as _decode_agent_cli_output,
    _parse_event_text,
    _prompt_argument_or_file_bootstrap,
)
from app.services.agent_sandbox import (
    AgentSandboxError,
    cleanup_isolated_runtime_directories,
    codex_command_for_outer_sandbox,
    filtered_agent_environment,
    prepare_isolated_codex_home as _prepare_isolated_codex_home,
    prepare_isolated_runtime_tmp as _prepare_isolated_runtime_tmp,
    prepare_agent_sandbox,
)
from app.services.network_policy import agent_network_is_permitted, scrub_intranet_agent_environment
from app.services.harness_facade import normalize_provider_event
from app.services.agent_invocation_contract import (
    agent_invocation_artifact_event_payload,
    agent_invocation_capability_event_payload,
    agent_invocation_capability_manifest,
    build_agent_invocation_execution_contract,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json_sha256(payload: Any) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


_MAX_ARG_PROMPT_BYTES = 24000
_DEFAULT_AGENT_TOTAL_TIMEOUT_SEC = 900
_DEFAULT_AGENT_IDLE_TIMEOUT_SEC = 300.0


def _public_agent_process_output(
    stream: str,
    chunk: str,
    *,
    stream_state: dict[Any, Any],
) -> str:
    text = _redact(chunk).strip()
    if not text:
        return ""
    if _is_known_codex_runtime_noise(text):
        return ""
    if stream == "stdout" and text.startswith("{"):
        parsed = _parse_event_text(
            text,
            "stream_json",
            stream_state=stream_state,
        )
        if not parsed:
            return ""
        if parsed.startswith((AGENT_FINAL_ANSWER_PREFIX, AGENT_ANSWER_DELTA_PREFIX)):
            return "Agent 正在整理最终回答与交付件。"
        text = parsed.strip()
    return text[-2000:]


def _is_known_codex_runtime_noise(text: str) -> bool:
    lowered = text.lower()
    if (
        "codex_core_skills::loader" in lowered
        and "failed to read skills symlink dir" in lowered
        and "operation not permitted" in lowered
    ):
        return True
    return (
        "failed to load models cache" in lowered
        and "runtime-codex-home" in lowered
        and "operation not permitted" in lowered
    )


def _default_agent_session_policy() -> dict[str, Any]:
    return {
        "external_session_mode": "disposable_process",
        "resume_supported": False,
        "resume_source": "none",
        "continuity_owner": "codetalk_task_bundle",
        "memory_sources": [
            "task_bundle",
            "evidence_memory",
            "source_slices",
            "validated_artifacts",
        ],
        "raw_output_reuse": "never_without_validation",
        "context_overflow_strategy": "source_slice_request_turn",
    }


_QUALITY_RETRY_REDUNDANT_CONTEXT_KEYS = (
    "context_bundle",
    "local_source_context",
    "memory_retrieval",
    "source_read_chain",
    "evidence_consumption_trajectory",
    "provider_snapshot",
    "provider_readiness",
    "workflow_contract",
    "execution_contract",
    "output_schemas_by_step",
    "semantic_import_outputs_by_step",
)

_AGENT_PROMPT_TASK_BUNDLE_OMITTED_KEYS = frozenset(
    (
        *_QUALITY_RETRY_REDUNDANT_CONTEXT_KEYS,
        "test_activity_contract",
        # These are persisted for audit and system-side validation.  They are
        # deliberately projected into the dedicated compact contracts below,
        # rather than being handed to an Agent as duplicate prompt bulk.
        "agent_instructions",
        "agent_mcp_requests",
        "artifact_contract_v3",
        "expected_output_schemas",
        "required_artifacts_by_step",
        "semantic_import_outputs_by_step",
        "stage_specs",
    )
)

_AGENT_PROMPT_VERBATIM_TASK_BUNDLE_KEYS = frozenset({
    "goal",
    "inputs",
    "input_context",
    "input_materials",
    "analysis_targets",
    "user_inputs",
    "user_text",
    "original_user_request",
    "resolved_inputs",
})

_AGENT_PROMPT_EXTENSION_BUDGET_CHARACTERS = 128_000
_AGENT_PROMPT_MAX_OMISSION_NAMES = 64
_AGENT_PROMPT_MAX_OMISSION_NAME_CHARACTERS = 120


def _agent_prompt_omission_label(key: Any) -> str:
    text = str(key)
    if len(text) <= _AGENT_PROMPT_MAX_OMISSION_NAME_CHARACTERS:
        return text
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]
    prefix_length = _AGENT_PROMPT_MAX_OMISSION_NAME_CHARACTERS - len(digest) - 2
    return f"{text[:prefix_length]}…#{digest}"

_AGENT_PROMPT_EXECUTION_CONTRACT_KEYS = (
    "contract_version",
    "goal",
    "repo_path",
    "analysis_targets",
    "user_inputs",
    "input_materials",
    "outputs",
    "mcp",
    "skills",
    "execution_rules",
    "source_context",
    "source_analysis_limits",
    "workflow",
)

_AGENT_PROMPT_MAX_SOURCE_FILES = 6
_AGENT_PROMPT_MAX_SOURCE_EXCERPT_CHARACTERS = 1400
_AGENT_PROMPT_MAX_PROFESSIONAL_CONSTRAINTS = 12
_AGENT_PROMPT_MAX_WORKFLOW_STEPS = 8


def _source_context_for_agent_prompt(source_context: Any) -> dict[str, Any]:
    """Project verified evidence into the small analysis context an Agent needs."""
    if not isinstance(source_context, dict):
        return {}
    files: list[dict[str, Any]] = []
    for item in source_context.get("files") or []:
        if not isinstance(item, dict) or len(files) >= _AGENT_PROMPT_MAX_SOURCE_FILES:
            continue
        excerpt = str(item.get("excerpt") or "")
        files.append({
            key: item[key]
            for key in (
                "file_path", "start_line", "end_line", "symbols", "matched_terms",
                "kind", "source_kind", "sha256", "reason",
            )
            if key in item
        } | {"excerpt": excerpt[:_AGENT_PROMPT_MAX_SOURCE_EXCERPT_CHARACTERS]})
    return {
        key: source_context[key]
        for key in ("repo_revision", "query", "evidence_gaps", "source_scope")
        if key in source_context
    } | {
        "files": files,
        "projection": {
            "max_files": _AGENT_PROMPT_MAX_SOURCE_FILES,
            "max_excerpt_characters": _AGENT_PROMPT_MAX_SOURCE_EXCERPT_CHARACTERS,
            "source_of_truth": "CodeTalk verified Source Evidence Pack",
        },
    }


def _output_summary_for_agent_prompt(value: Any) -> Any:
    if isinstance(value, list):
        return [
            {
                key: item[key]
                for key in ("id", "output_id", "artifact", "path", "type", "schema_type", "schema_required")
                if key in item
            }
            for item in value
            if isinstance(item, dict)
        ]
    if not isinstance(value, dict):
        return value
    return {
        key: _output_summary_for_agent_prompt(item)
        if key in {"declared_outputs", "expected_output_schemas"}
        else item
        for key, item in value.items()
        if key in {"declared_outputs", "expected_output_schemas", "required_artifacts"}
    }


def _workflow_for_agent_prompt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = {
        key: value[key]
        for key in ("id", "name", "version", "description", "execution_label", "execution_subject")
        if key in value
    }
    result["inputs"] = [
        {
            key: item[key]
            for key in ("id", "label", "role", "type", "required")
            if key in item
        }
        for item in value.get("inputs") or []
        if isinstance(item, dict)
    ]
    result["outputs"] = _output_summary_for_agent_prompt(value.get("outputs") or [])
    result["steps"] = [
        {
            key: item[key]
            for key in ("id", "label", "name", "type", "provider")
            if key in item
        }
        for item in (value.get("steps") or [])[:_AGENT_PROMPT_MAX_WORKFLOW_STEPS]
        if isinstance(item, dict)
    ]
    return result


def _task_bundle_for_agent_prompt(task_bundle: dict[str, Any]) -> dict[str, Any]:
    """Keep Agent prompts bounded without dropping user-authored inputs or files."""
    prompt_bundle: dict[str, Any] = {}
    omitted = set(task_bundle).intersection(_AGENT_PROMPT_TASK_BUNDLE_OMITTED_KEYS)
    extension_characters = 0
    for key, value in task_bundle.items():
        if key in _AGENT_PROMPT_TASK_BUNDLE_OMITTED_KEYS:
            continue
        if key in _AGENT_PROMPT_VERBATIM_TASK_BUNDLE_KEYS:
            prompt_bundle[key] = value
            continue
        try:
            value_characters = len(
                json.dumps({str(key): value}, ensure_ascii=False, default=str)
            )
        except (TypeError, ValueError):
            value_characters = _AGENT_PROMPT_EXTENSION_BUDGET_CHARACTERS + 1
        if (
            extension_characters + value_characters
            > _AGENT_PROMPT_EXTENSION_BUDGET_CHARACTERS
        ):
            omitted.add(key)
            continue
        prompt_bundle[key] = value
        extension_characters += value_characters
    omission_labels = sorted(_agent_prompt_omission_label(key) for key in omitted)
    prompt_bundle["context_omissions"] = omission_labels[
        :_AGENT_PROMPT_MAX_OMISSION_NAMES
    ]
    prompt_bundle["context_omission_count"] = len(omission_labels)
    prompt_bundle["context_omissions_truncated"] = (
        len(omission_labels) > _AGENT_PROMPT_MAX_OMISSION_NAMES
    )
    prompt_bundle["context_extension_characters"] = extension_characters
    prompt_bundle["context_extension_budget_characters"] = (
        _AGENT_PROMPT_EXTENSION_BUDGET_CHARACTERS
    )
    prompt_bundle["context_artifact_rule"] = (
        "Complete discovery and audit payloads remain in task_bundle.json and sibling "
        "artifacts under artifact_dir. Read those files when needed. Every user-authored "
        "value remains verbatim in inputs, user_inputs, or input_materials."
    )
    retry_feedback = task_bundle.get("retry_quality_feedback")
    if isinstance(retry_feedback, dict) and retry_feedback:
        prompt_bundle["quality_retry_context_omissions"] = [
            key for key in _QUALITY_RETRY_REDUNDANT_CONTEXT_KEYS if key in task_bundle
        ]
        prompt_bundle["quality_retry_context_rule"] = (
            "Omitted discovery payloads are already represented by protected artifacts in "
            "artifact_dir. Read those artifacts directly; preserve every character in inputs "
            "and input_materials."
        )
    return prompt_bundle


def _execution_contract_for_agent_prompt(
    execution_contract: dict[str, Any],
) -> dict[str, Any]:
    result = {
        key: execution_contract[key]
        for key in _AGENT_PROMPT_EXECUTION_CONTRACT_KEYS
        if key in execution_contract
        and key not in {"source_context", "outputs", "workflow"}
    }
    if "source_context" in execution_contract:
        result["source_context"] = _source_context_for_agent_prompt(
            execution_contract["source_context"]
        )
    if "outputs" in execution_contract:
        result["outputs"] = _output_summary_for_agent_prompt(
            execution_contract["outputs"]
        )
    if "workflow" in execution_contract:
        result["workflow"] = _workflow_for_agent_prompt(
            execution_contract["workflow"]
        )
    return result


def _test_activity_contract_for_agent_prompt(contract: dict[str, Any]) -> dict[str, Any]:
    """Keep quality rules system-owned while giving the Agent useful task guardrails.

    Regex-based correction rules and full schemas are Validator implementation
    details.  Sending them to the generating Agent consumes context and creates
    a Goodhart incentive to phrase around a rule instead of grounding claims in
    verified source evidence.
    """
    result = {
        key: contract[key]
        for key in (
            "contract_version", "target", "required_outputs", "executor_requirements",
            "evidence_policy", "black_box_boundary", "focus_rationale",
        )
        if key in contract
    }
    if "user_requirements" in contract:
        result["user_requirements"] = str(contract["user_requirements"])
    domain_profiles = contract.get("domain_profiles")
    if isinstance(domain_profiles, list):
        result["domain_profiles"] = [str(item) for item in domain_profiles[:12]]
    quality_gates = contract.get("quality_gates")
    if isinstance(quality_gates, dict):
        result["quality_gates"] = {
            str(key): value
            for key, value in quality_gates.items()
            if isinstance(value, (bool, int, float, str))
        }
    constraints: list[dict[str, Any]] = []
    for item in contract.get("professional_constraints") or []:
        if not isinstance(item, dict) or len(constraints) >= _AGENT_PROMPT_MAX_PROFESSIONAL_CONSTRAINTS:
            continue
        constraints.append({
            key: item[key]
            for key in ("id", "assertion", "evidence")
            if key in item
        })
    if constraints:
        result["professional_constraints"] = constraints
    result["validator_ownership"] = {
        "full_schema": "CodeTalk validator",
        "regex_correction_rules": "CodeTalk validator",
        "required_agent_behavior": "Use verified source evidence, distinguish facts from hypotheses, and write only declared artifacts.",
    }
    return result


def _output_contract_for_agent_prompt(
    output_contract: dict[str, Any],
) -> dict[str, Any]:
    return {
        key: value
        for key, value in output_contract.items()
        if key not in {"execution_contract", "test_activity_contract"}
    }


def _artifact_contract_reference(
    output_contract: dict[str, Any],
    *,
    artifact_dir: str,
) -> dict[str, Any]:
    return {
        "source": "agent_output_contract",
        "artifact_dir": artifact_dir,
        "required_artifacts": list(output_contract.get("required_artifacts") or []),
        "expected_output_schemas": list(
            output_contract.get("expected_output_schemas") or []
        ),
        "rule": (
            "Write every required artifact under artifact_dir; the complete contract is also "
            "materialized as agent_output_contract.json."
        ),
    }


def _agent_output_contract_payload(
    *,
    run: "AgentRunRecord",
    task_bundle: dict[str, Any],
    workflow_snapshot: dict[str, Any],
) -> dict[str, Any]:
    retry_required_artifacts = [
        str(item)
        for item in task_bundle.get("quality_retry_required_artifacts") or []
        if str(item).strip()
    ]
    required_artifacts = retry_required_artifacts or [
        str(item) for item in task_bundle.get("required_artifacts") or []
    ]
    expected_output_schemas = [
        item for item in task_bundle.get("expected_output_schemas") or []
        if isinstance(item, dict)
    ]
    if retry_required_artifacts:
        retry_names = {Path(item).name for item in retry_required_artifacts}
        expected_output_schemas = [
            item
            for item in expected_output_schemas
            if Path(str(item.get("artifact") or item.get("path") or "")).name
            in retry_names
        ]
    expected_semantic_outputs = [
        item for item in task_bundle.get("expected_semantic_outputs") or []
        if isinstance(item, dict)
    ]
    black_box_generation_policy = (
        task_bundle.get("black_box_generation_policy")
        if isinstance(task_bundle.get("black_box_generation_policy"), dict)
        else {}
    )
    input_materials = (
        task_bundle.get("input_materials")
        if isinstance(task_bundle.get("input_materials"), dict)
        else {}
    )
    skills = [str(item) for item in task_bundle.get("skills") or [] if str(item)]
    skill_instructions = [
        item for item in task_bundle.get("skill_instructions") or []
        if isinstance(item, dict)
    ]
    execution_contract = (
        task_bundle.get("execution_contract")
        if isinstance(task_bundle.get("execution_contract"), dict)
        else {}
    )
    test_activity_contract = (
        task_bundle.get("test_activity_contract")
        if isinstance(task_bundle.get("test_activity_contract"), dict)
        else execution_contract.get("test_activity_contract")
        if isinstance(execution_contract.get("test_activity_contract"), dict)
        else {}
    )
    retry_validation_feedback = (
        task_bundle.get("retry_validation_feedback")
        if isinstance(task_bundle.get("retry_validation_feedback"), dict)
        else {}
    )
    retry_quality_feedback = (
        task_bundle.get("retry_quality_feedback")
        if isinstance(task_bundle.get("retry_quality_feedback"), dict)
        else {}
    )
    return {
        "contract_version": 1,
        "run_id": run.run_id,
        "turn_id": run.turn_id,
        "provider": run.provider,
        "step_id": str(task_bundle.get("step_id") or ""),
        "goal": str(task_bundle.get("goal") or ""),
        "workflow_id": str(task_bundle.get("workflow_id") or workflow_snapshot.get("id") or ""),
        "mcp_profile": run.mcp_profile,
        "skills": skills,
        "skill_injection": {
            "enabled": bool(skills),
            "source": "workflow_agent_step",
            "ids": skills,
            "instructions": skill_instructions,
            "rule": "Selected skills are task-method constraints injected through task_bundle and must shape the final artifacts.",
        },
        "execution_contract": execution_contract,
        "test_activity_contract": test_activity_contract,
        "artifact_dir": run.artifact_dir,
        "required_artifacts": required_artifacts,
        "expected_output_schemas": expected_output_schemas,
        "expected_semantic_outputs": expected_semantic_outputs,
        "input_materials": {
            "material_count": int(input_materials.get("material_count") or 0),
            "read_order": [str(item) for item in input_materials.get("read_order") or []],
            "rules": input_materials.get("rules") if isinstance(input_materials.get("rules"), dict) else {},
        },
        "black_box_generation_policy": black_box_generation_policy,
        "retry_validation_feedback": retry_validation_feedback,
        "retry_quality_feedback": retry_quality_feedback,
        "evidence_rules": {
            "raw_output_reuse": "never_without_validation",
            "required_artifacts_are_authoritative": True,
            "codetalk_validates_before_evidence": True,
            "unvalidated_agent_claims": "diagnostic_only",
            "technical_claim_protocol": {
                "literal_quote_required": True,
                "ellipsis_forbidden": True,
                "prefer_unindented_exact_fragment": True,
                "instructions": (
                    "For every technical_claims.evidence entry, provide a repo-relative path, "
                    "an exact Lstart-Lend range, and a literal source substring contained in that "
                    "range. Copy a short unindented token or code fragment verbatim when possible; "
                    "do not replace whitespace, summarize, or use '...'. If no exact quote is "
                    "available, write the item as a hypothesis or evidence gap without a "
                    "technical_claim. CodeTalk will locally re-read and SHA256-validate every claim "
                    "anchor and will reject a mismatched quote."
                ),
            },
            "test_activity_writing_protocol": {
                "sfmea": (
                    "Each mitigation must use two explicit clauses: '整改：<concrete product/config/code "
                    "change>; 验证：<executable test, log, metric, or monitor check>'. A test-only "
                    "sentence is not a mitigation. Each technical claim statement is one factual sentence "
                    "under 240 characters and is different from its source quote."
                ),
                "black_box_cases": (
                    "test_dimension is a machine contract, not a display label. Use exactly one of: "
                    "normal_path, invalid_input, resource_pressure, timeout, reconnect, concurrency, "
                    "recovery, performance, long_steady_state, resource_wraparound, resource_cleanup, "
                    "upstream_error_propagation. Cover every required dimension at least once; put Chinese "
                    "explanation in scenario_name, never in test_dimension."
                ),
                "claim_anchor_limits": {
                    "max_source_lines": 160,
                    "max_quote_characters": 6000,
                    "preferred_source_lines": 32,
                },
            },
            "evidence_card_symbol_validation": {
                "code_files": "symbols must occur in executable source, not only comments or strings",
                "shell_files": (
                    "symbols must occur in executable shell outside comments, quoted data, and heredoc bodies; "
                    "for script-level or heredoc-backed test evidence use the exact filename as the sole symbol"
                ),
                "metadata_files": (
                    "for JSON/index metadata use an empty symbols list plus exact sha256 and line_count"
                ),
            },
        },
        "execution_rules": {
            "readonly_env": True,
            "readonly_env_var": "CODETALK_AGENT_READONLY",
            "artifact_dir_env_var": "CODETALK_AGENT_ARTIFACT_DIR",
            "repo_path_env_var": "CODETALK_REPO_PATH",
            "path_resolution": {
                "source_reads": (
                    "Use $CODETALK_REPO_PATH/<repo-relative-path> for every source or "
                    "test read; do not rely on a bare relative path."
                ),
                "artifact_reads_and_writes": (
                    "Use $CODETALK_AGENT_ARTIFACT_DIR/<artifact-name> for every task "
                    "artifact read or write; do not rely on the current directory."
                ),
            },
            "network_and_mcp_credentials_owner": "agent_cli",
            "codetalk_may_not_fetch_agent_owned_mcp_inputs": True,
            "long_running_services_allowed": False,
        },
        "source_slice_protocol": {
            "request_artifact": "source_slice_requests.json",
            "request_schema": {
                "need_source_slices": [
                    {
                        "file_path": "repo-relative source path",
                        "start_line": 1,
                        "end_line": 120,
                        "symbol": "optional symbol",
                        "reason": "why more source context is needed",
                    }
                ]
            },
            "response_in_task_bundle": "requested_source_slices",
            "max_slices_per_turn": 24,
        },
        "audit_artifacts": [
            "agent_run.json",
            "task_bundle.json",
            "agent_output_contract.json",
            "agent_invocation.json",
            "execution_input.json",
            "execution_result.json",
            "raw_output.txt",
            "agent_run_lifecycle.json",
        ],
    }


def _workflow_agent_invocation_payload(
    *,
    run: "AgentRunRecord",
    task_bundle: dict[str, Any],
    workflow_snapshot: dict[str, Any],
    agent_output_contract: dict[str, Any] | None = None,
    stdin_payload_obj: dict[str, Any] | None = None,
    stdin_payload: str = "",
    prompt_transport: str = "",
) -> dict[str, Any]:
    contract = agent_output_contract if isinstance(agent_output_contract, dict) else _agent_output_contract_payload(
        run=run,
        task_bundle=task_bundle,
        workflow_snapshot=workflow_snapshot,
    )
    execution_contract = (
        task_bundle.get("execution_contract")
        if isinstance(task_bundle.get("execution_contract"), dict)
        else contract.get("execution_contract")
        if isinstance(contract.get("execution_contract"), dict)
        else {}
    )
    test_activity_contract = (
        task_bundle.get("test_activity_contract")
        if isinstance(task_bundle.get("test_activity_contract"), dict)
        else contract.get("test_activity_contract")
        if isinstance(contract.get("test_activity_contract"), dict)
        else execution_contract.get("test_activity_contract")
        if isinstance(execution_contract.get("test_activity_contract"), dict)
        else {}
    )
    skills = [str(item) for item in task_bundle.get("skills") or [] if str(item)]
    stdin_obj = stdin_payload_obj if isinstance(stdin_payload_obj, dict) else {}
    prompt_payload: dict[str, Any] = {
        "transport": prompt_transport or "pending_execution",
        "redacted": True,
    }
    if stdin_payload:
        prompt_payload.update({
            "stdin_json_sha256": hashlib.sha256(stdin_payload.encode("utf-8")).hexdigest(),
            "chars": len(stdin_payload),
            "stdin": _redact_replay_payload(stdin_obj),
        })
    return {
        "schema_version": 1,
        "source": "workflow",
        "run_id": run.run_id,
        "turn_id": run.turn_id,
        "runtime": {
            "provider": run.provider,
            "command": _redact_command_list(run.command),
        },
        "prompt": prompt_payload,
        "cwd": run.cwd,
        "repo_path": run.cwd,
        "workflow": {
            "id": str(workflow_snapshot.get("id") or ""),
            "version": workflow_snapshot.get("version"),
            "step_count": len(workflow_snapshot.get("steps") or []),
        },
        "task_bundle": task_bundle,
        "mcp_profile": run.mcp_profile,
        "skills": skills,
        "session": run.session_policy,
        "execution_contract": build_agent_invocation_execution_contract(
            source_first=True,
            cwd=run.cwd,
            repo_path=run.cwd,
            extra=execution_contract,
        ),
        "test_activity_contract": test_activity_contract,
        "artifact_contract": contract,
        "artifact_dir": run.artifact_dir,
    }


@dataclass(frozen=True)
class AgentRunRecord:
    run_id: str
    turn_id: str
    provider: str
    command: list[str]
    cwd: str
    artifact_dir: str
    mcp_profile: str = ""
    prompt_transport: str = ""
    timeout_seconds: int | None = None
    idle_timeout_seconds: float | None = None
    session_policy: dict[str, Any] = field(default_factory=_default_agent_session_policy)
    status: str = "created"
    created_at: str = field(default_factory=_now)


@dataclass(frozen=True)
class ArtifactValidationResult:
    status: str
    provenance_status: str
    accepted_artifacts: list[str] = field(default_factory=list)
    rejected_artifacts: list[dict[str, str]] = field(default_factory=list)
    accepted_artifact_details: list[dict[str, Any]] = field(default_factory=list)
    rejected_artifact_details: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AgentRunExecutionResult:
    run_id: str
    status: str
    exit_code: int | None
    started_at: str
    completed_at: str
    duration_ms: int
    timed_out: bool = False
    error: str = ""
    provider_diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass
class _SubprocessExecutionResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    timeout_kind: str = ""
    cancelled: bool = False
    error: str = ""


class AgentRunHarness:
    """Writes the reproducible envelope around an external Agent CLI run."""

    def __init__(self, artifact_dir: str | Path) -> None:
        self.artifact_dir = Path(artifact_dir)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

    def create_run(
        self,
        *,
        provider: str,
        command: list[str],
        cwd: str,
        workflow_snapshot: dict[str, Any],
        task_bundle: dict[str, Any],
        mcp_profile: str = "",
        prompt_transport: str = "",
        timeout_seconds: int | None = None,
        idle_timeout_seconds: float | None = None,
        run_id: str | None = None,
        turn_id: str = "turn_1",
    ) -> AgentRunRecord:
        run = AgentRunRecord(
            run_id=run_id or _new_id("agent_run"),
            turn_id=turn_id or "turn_1",
            provider=provider,
            command=[str(part) for part in command],
            cwd=cwd,
            artifact_dir=str(self.artifact_dir),
            mcp_profile=mcp_profile,
            prompt_transport=str(prompt_transport or ""),
            timeout_seconds=timeout_seconds,
            idle_timeout_seconds=idle_timeout_seconds,
        )
        self._write_json("agent_run.json", asdict(run))
        self._write_json("task_bundle.json", task_bundle)
        self._write_json("workflow_snapshot.json", workflow_snapshot)
        agent_output_contract = _agent_output_contract_payload(
            run=run,
            task_bundle=task_bundle,
            workflow_snapshot=workflow_snapshot,
        )
        self._write_json(
            "agent_output_contract.json",
            agent_output_contract,
        )
        invocation_manifest = _workflow_agent_invocation_payload(
            run=run,
            task_bundle=task_bundle,
            workflow_snapshot=workflow_snapshot,
            agent_output_contract=agent_output_contract,
        )
        self._write_json("agent_invocation.json", invocation_manifest)
        self._write_json(
            "capability_manifest.json",
            agent_invocation_capability_manifest(invocation_manifest),
        )
        return run

    def record_raw_output(self, run_id: str, *, stdout: str, stderr: str = "") -> None:
        run_payload = self._read_json_file("agent_run.json")
        turn_id = (
            str(run_payload.get("turn_id") or "turn_1")
            if isinstance(run_payload, dict)
            else "turn_1"
        )
        payload = "\n".join(part for part in [stdout, stderr] if part)
        self._write_text("raw_output.txt", _redact(payload))
        self._write_json(
            "runtime_events.jsonl",
            {
                "event": "raw_output_recorded",
                "run_id": run_id,
                "turn_id": turn_id,
                "created_at": _now(),
            },
            append_jsonl=True,
        )

    def execute_run(
        self,
        run_id: str,
        *,
        timeout_sec: int = 0,
        idle_timeout_sec: float | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> AgentRunExecutionResult:
        try:
            return self._execute_run(
                run_id,
                timeout_sec=timeout_sec,
                idle_timeout_sec=idle_timeout_sec,
                is_cancelled=is_cancelled,
                event_sink=event_sink,
            )
        finally:
            cleanup_isolated_runtime_directories(self.artifact_dir)

    def _execute_run(
        self,
        run_id: str,
        *,
        timeout_sec: int = 0,
        idle_timeout_sec: float | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> AgentRunExecutionResult:
        run_payload = self._read_json_file("agent_run.json")
        if not isinstance(run_payload, dict) or run_payload.get("run_id") != run_id:
            raise ValueError(f"unknown agent run: {run_id}")
        configured_command = [str(part) for part in run_payload.get("command") or []]
        if not configured_command:
            raise ValueError("agent run command is empty")
        command = _resolve_local_process_command(configured_command)
        cwd = str(run_payload.get("cwd") or "")
        if not cwd:
            raise ValueError("agent run cwd is empty")
        effective_timeout_sec = _effective_agent_timeout_sec(timeout_sec, run_payload)
        effective_idle_timeout_sec = _effective_agent_idle_timeout_sec(
            idle_timeout_sec,
            run_payload,
        )

        task_bundle = self._read_json_file("task_bundle.json")
        workflow_snapshot = self._read_json_file("workflow_snapshot.json")
        agent_output_contract = self._read_json_file("agent_output_contract.json")
        turn_id = str(run_payload.get("turn_id") or "turn_1")
        context_discovery_decision_summary = _context_discovery_decision_summary(
            task_bundle if isinstance(task_bundle, dict) else {}
        )
        agent_instruction_policy = _agent_instruction_policy_summary(
            task_bundle if isinstance(task_bundle, dict) else {}
        )
        provider_diagnostics = _provider_diagnostics_snapshot(
            run_payload=run_payload,
            task_bundle=task_bundle if isinstance(task_bundle, dict) else {},
        )
        session_policy = (
            run_payload.get("session_policy")
            if isinstance(run_payload.get("session_policy"), dict)
            else _default_agent_session_policy()
        )
        if isinstance(task_bundle, dict) and isinstance(workflow_snapshot, dict):
            agent_output_contract = _agent_output_contract_payload(
                run=AgentRunRecord(
                    run_id=run_id,
                    turn_id=turn_id,
                    provider=str(run_payload.get("provider") or ""),
                    command=configured_command,
                    cwd=cwd,
                    artifact_dir=str(self.artifact_dir),
                    mcp_profile=str(run_payload.get("mcp_profile") or ""),
                    prompt_transport=str(run_payload.get("prompt_transport") or ""),
                    session_policy=session_policy,
                    status=str(run_payload.get("status") or "created"),
                    created_at=str(run_payload.get("created_at") or _now()),
                ),
                task_bundle=task_bundle,
                workflow_snapshot=workflow_snapshot,
            )
            self._write_json("agent_output_contract.json", agent_output_contract)
        self._write_json("provider_diagnostics.json", provider_diagnostics)
        execution_contract = (
            task_bundle.get("execution_contract")
            if isinstance(task_bundle, dict)
            and isinstance(task_bundle.get("execution_contract"), dict)
            else agent_output_contract.get("execution_contract")
            if isinstance(agent_output_contract, dict)
            and isinstance(agent_output_contract.get("execution_contract"), dict)
            else {}
        )
        test_activity_contract = (
            task_bundle.get("test_activity_contract")
            if isinstance(task_bundle, dict)
            and isinstance(task_bundle.get("test_activity_contract"), dict)
            else agent_output_contract.get("test_activity_contract")
            if isinstance(agent_output_contract, dict)
            and isinstance(agent_output_contract.get("test_activity_contract"), dict)
            else execution_contract.get("test_activity_contract")
            if isinstance(execution_contract.get("test_activity_contract"), dict)
            else {}
        )
        runtime_contract = {
            "provider": str(run_payload.get("provider") or ""),
            "cwd": cwd,
            "repo_path": cwd,
            "mcp_profile": str(run_payload.get("mcp_profile") or ""),
        }
        compact_execution_contract = _execution_contract_for_agent_prompt(
            execution_contract
        )
        compact_test_activity_contract = _test_activity_contract_for_agent_prompt(
            test_activity_contract
        )
        compact_output_contract = _output_contract_for_agent_prompt(
            agent_output_contract if isinstance(agent_output_contract, dict) else {}
        )
        stdin_payload_obj = {
            "run_id": run_id,
            "turn_id": turn_id,
            "provider": run_payload.get("provider") or "",
            "runtime": runtime_contract,
            "mcp_profile": run_payload.get("mcp_profile") or "",
            "session_policy": session_policy,
            "workflow_snapshot": workflow_snapshot if isinstance(workflow_snapshot, dict) else {},
            "task_bundle": (
                _task_bundle_for_agent_prompt(task_bundle)
                if isinstance(task_bundle, dict)
                else {}
            ),
            "execution_contract": compact_execution_contract,
            "test_activity_contract": compact_test_activity_contract,
            "agent_output_contract": compact_output_contract,
            "artifact_contract": _artifact_contract_reference(
                compact_output_contract,
                artifact_dir=str(self.artifact_dir),
            ),
            "context_discovery_decision_summary": context_discovery_decision_summary,
            "agent_instruction_policy": agent_instruction_policy,
            "provider_diagnostics": provider_diagnostics,
            "artifact_dir": str(self.artifact_dir),
        }
        stdin_payload = json.dumps(stdin_payload_obj, ensure_ascii=False)
        invocation_manifest = _workflow_agent_invocation_payload(
            run=AgentRunRecord(
                run_id=run_id,
                turn_id=turn_id,
                provider=str(run_payload.get("provider") or ""),
                command=configured_command,
                cwd=cwd,
                artifact_dir=str(self.artifact_dir),
                mcp_profile=str(run_payload.get("mcp_profile") or ""),
                prompt_transport=str(run_payload.get("prompt_transport") or ""),
                session_policy=session_policy,
                status=str(run_payload.get("status") or "created"),
                created_at=str(run_payload.get("created_at") or _now()),
            ),
            task_bundle=task_bundle if isinstance(task_bundle, dict) else {},
            workflow_snapshot=workflow_snapshot if isinstance(workflow_snapshot, dict) else {},
            agent_output_contract=agent_output_contract if isinstance(agent_output_contract, dict) else {},
            stdin_payload_obj=stdin_payload_obj,
            stdin_payload=stdin_payload,
            prompt_transport=str(run_payload.get("prompt_transport") or "stdin"),
        )
        self._write_json("agent_invocation.json", invocation_manifest)
        capability_manifest = agent_invocation_capability_manifest(invocation_manifest)
        self._write_json("capability_manifest.json", capability_manifest)
        _emit_agent_run_event(
            event_sink,
            "artifact",
            agent_invocation_artifact_event_payload(
                invocation_manifest,
                artifact="agent_invocation.json",
                extra={
                    "run_id": run_id,
                    "turn_id": turn_id,
                    "provider": str(run_payload.get("provider") or ""),
                },
            ),
        )
        _emit_agent_run_event(
            event_sink,
            "artifact",
            agent_invocation_capability_event_payload(
                invocation_manifest,
                artifact="capability_manifest.json",
                extra={
                    "run_id": run_id,
                    "turn_id": turn_id,
                    "provider": str(run_payload.get("provider") or ""),
                },
            ),
        )
        task_bundle_sha256 = _json_sha256(task_bundle if isinstance(task_bundle, dict) else {})
        workflow_snapshot_sha256 = _json_sha256(
            workflow_snapshot if isinstance(workflow_snapshot, dict) else {}
        )
        agent_output_contract_sha256 = _json_sha256(
            agent_output_contract if isinstance(agent_output_contract, dict) else {}
        )
        runtime_tmp_dir = _prepare_isolated_runtime_tmp(self.artifact_dir)
        env_hints = {
            "CODETALK_AGENT_READONLY": "1",
            "CODETALK_REPO_PATH": cwd,
            "CODETALK_AGENT_ARTIFACT_DIR": str(self.artifact_dir),
            "CODETALK_TEMP_DIR": str(runtime_tmp_dir),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_KEY_0": "core.excludesFile",
            "GIT_CONFIG_VALUE_0": os.devnull,
            "TEMP": str(runtime_tmp_dir),
            "TMP": str(runtime_tmp_dir),
            "TMPDIR": str(runtime_tmp_dir),
            "TMPPREFIX": str(runtime_tmp_dir / "zsh"),
            # Agent-generated reports may legitimately contain Chinese evidence
            # labels.  Keep the platform Python shim from choosing an ASCII
            # source encoding while the sandboxed task is materializing files.
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        env_hints.update(_agent_provider_env_hints(str(run_payload.get("provider") or "")))
        launch_command, command_resolution = _launch_command_from_provider_health(
            command,
            provider_diagnostics,
        )
        unresolved_launch_command = list(launch_command)
        launch_command = _resolve_local_process_command(launch_command)
        if launch_command != unresolved_launch_command and "reason" not in command_resolution:
            command_resolution = {
                **command_resolution,
                "reason": "ad_hoc_command_preserved",
                "local_executable_resolution": "python_to_current_interpreter",
            }
        if configured_command != command and "reason" not in command_resolution:
            command_resolution = {
                **command_resolution,
                "reason": "ad_hoc_command_preserved",
                "local_executable_resolution": "python_to_current_interpreter",
            }
        invocation_candidates = _agent_process_invocation_candidates_for_harness(
            provider=str(run_payload.get("provider") or ""),
            command=launch_command,
            prompt=stdin_payload,
            prompt_transport=str(run_payload.get("prompt_transport") or ""),
            artifact_dir=str(self.artifact_dir),
        )
        process_command, stdin_payload_bytes, prompt_transport, prompt_transport_reason = invocation_candidates[0]
        codex_runtime_home, codex_runtime_read_targets = _prepare_isolated_codex_home(
            provider=str(run_payload.get("provider") or ""),
            command=process_command,
            artifact_dir=self.artifact_dir,
            include_user_skills=False,
        )
        if codex_runtime_home is not None:
            env_hints["CODEX_HOME"] = str(codex_runtime_home)
        prompt_file_path: str | None = None
        if (
            prompt_transport not in {"stdin", "codex_exec_json"}
            and len(stdin_payload.encode("utf-8")) > _MAX_ARG_PROMPT_BYTES
        ):
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix="codetalk-workflow-prompt-",
                suffix=".json",
                dir=runtime_tmp_dir,
                delete=False,
            ) as prompt_file:
                prompt_file.write(stdin_payload)
                prompt_file_path = prompt_file.name
            env_hints["CODETALK_AGENT_PROMPT_FILE"] = prompt_file_path
            prompt_bootstrap = _prompt_argument_or_file_bootstrap(
                stdin_payload,
                prompt_file_path=prompt_file_path,
            )
            invocation_candidates = _agent_process_invocation_candidates_for_harness(
                provider=str(run_payload.get("provider") or ""),
                command=launch_command,
                prompt=prompt_bootstrap,
                prompt_transport=str(run_payload.get("prompt_transport") or ""),
                artifact_dir=str(self.artifact_dir),
            )
            invocation_candidates = [
                (command_value, input_bytes, transport, "large_payload_prompt_file")
                for command_value, input_bytes, transport, _reason in invocation_candidates[:1]
            ]
            process_command, stdin_payload_bytes, prompt_transport, prompt_transport_reason = (
                invocation_candidates[0]
            )
        self._write_json(
            "execution_input.json",
            {
                "run_id": run_id,
                "turn_id": turn_id,
                "provider": run_payload.get("provider") or "",
                "command": configured_command,
                "launch_command": launch_command,
                "command_resolution": command_resolution,
                "process_command": process_command,
                "prompt_transport": prompt_transport,
                "prompt_transport_reason": prompt_transport_reason,
                "transport_attempts": [],
                "cwd": cwd,
                "timeout_sec": effective_timeout_sec,
                "idle_timeout_sec": effective_idle_timeout_sec,
                "mcp_profile": run_payload.get("mcp_profile") or "",
                "session_policy": session_policy,
                "env_hints": _redact_replay_payload(env_hints),
                "task_bundle_sha256": task_bundle_sha256,
                "workflow_snapshot_sha256": workflow_snapshot_sha256,
                "agent_output_contract_sha256": agent_output_contract_sha256,
                "context_discovery_decision_summary": context_discovery_decision_summary,
                "agent_instruction_policy": agent_instruction_policy,
                "provider_diagnostics": provider_diagnostics,
                "agent_output_contract": (
                    agent_output_contract if isinstance(agent_output_contract, dict) else {}
                ),
                "stdin": _redact_replay_payload(stdin_payload_obj),
                "stdin_redacted": True,
                "stdin_json_sha256": hashlib.sha256(
                    stdin_payload.encode("utf-8")
                ).hexdigest(),
            },
        )
        _emit_agent_run_event(
            event_sink,
            "artifact",
            {
                "artifact": "execution_input.json",
                "artifact_kind": "execution_input",
                "content": "执行输入已准备，用户输入、工作区、MCP、skills 与输出契约已进入 Agent stdin。",
                "run_id": run_id,
                "turn_id": turn_id,
                "provider": str(run_payload.get("provider") or ""),
                "prompt_transport": prompt_transport,
                "prompt_transport_reason": prompt_transport_reason,
                "stdin_json_sha256": hashlib.sha256(stdin_payload.encode("utf-8")).hexdigest(),
            },
        )

        started_at = _now()
        self._write_json(
            "runtime_events.jsonl",
            {
                "event": "agent_execution_input_prepared",
                "run_id": run_id,
                "turn_id": turn_id,
                "artifact": "execution_input.json",
                "task_bundle_sha256": task_bundle_sha256,
                "workflow_snapshot_sha256": workflow_snapshot_sha256,
                "agent_output_contract_sha256": agent_output_contract_sha256,
                "context_discovery_decision_summary": context_discovery_decision_summary,
                "agent_instruction_policy": agent_instruction_policy,
                "provider_diagnostics_artifact": "provider_diagnostics.json",
                "agent_output_contract_artifact": "agent_output_contract.json",
                "created_at": started_at,
            },
            append_jsonl=True,
        )
        _emit_agent_run_event(
            event_sink,
            "tool_use",
            {
                "tool": "agent_cli",
                "run_id": run_id,
                "turn_id": turn_id,
                "provider": str(run_payload.get("provider") or ""),
                "input": {
                    "command": _redact_command_list(process_command),
                    "cwd_label": _repo_path_label(cwd),
                    "prompt_transport": prompt_transport,
                    "mcp_profile": str(run_payload.get("mcp_profile") or ""),
                },
            },
        )
        self._write_json(
            "runtime_events.jsonl",
            {
                "event": "agent_run_started",
                "run_id": run_id,
                "turn_id": turn_id,
                "command": configured_command,
                "launch_command": launch_command,
                "command_resolution": command_resolution,
                "process_command": process_command,
                "prompt_transport": prompt_transport,
                "prompt_transport_reason": prompt_transport_reason,
                "created_at": started_at,
            },
            append_jsonl=True,
        )
        started = datetime.now(timezone.utc)
        env = _agent_process_env_for_harness(
            provider=str(run_payload.get("provider") or ""),
            repo_path=cwd,
            command=configured_command,
            prompt_transport=str(run_payload.get("prompt_transport") or ""),
            artifact_dir=self.artifact_dir,
        )
        env.update(env_hints)
        if settings.intranet_network_mode:
            env = scrub_intranet_agent_environment(env)
        env = _prefer_native_macos_git_path(env)
        env = _prepend_vetted_analysis_tool_paths(env)
        env["CODETALK_AGENT_ARTIFACT_DIR"] = str(self.artifact_dir.resolve())
        try:
            sandbox = prepare_agent_sandbox(
                runtime={
                    "sandbox_mode": settings.external_agent_sandbox_mode,
                    "sandbox_allow_network": agent_network_is_permitted(),
                    "sandbox_read_paths": [
                        str(path) for path in _task_run_read_roots(self.artifact_dir)
                    ] + [str(path) for path in codex_runtime_read_targets],
                    "sandbox_write_paths": settings.external_agent_sandbox_write_paths,
                    "sandbox_command": process_command[0] if process_command else "",
                    "sandbox_codex_home": str(codex_runtime_home or ""),
                    "sandbox_codex_include_user_skills": False,
                },
                cwd=cwd,
                artifact_dir=self.artifact_dir,
            )
        except AgentSandboxError as exc:
            raise RuntimeError(str(exc)) from exc
        invocation_candidates = _finalize_invocation_candidates_for_sandbox(
            invocation_candidates,
            sandbox_active=sandbox.status == "active",
        )
        process_command, stdin_payload_bytes, prompt_transport, prompt_transport_reason = (
            invocation_candidates[0]
        )
        execution_input = self._read_json_file("execution_input.json")
        if isinstance(execution_input, dict):
            execution_input["process_command"] = process_command
            execution_input["prompt_transport"] = prompt_transport
            execution_input["prompt_transport_reason"] = prompt_transport_reason
            execution_input["sandbox_status"] = sandbox.status
            self._write_json("execution_input.json", execution_input)
        self._write_json(
            "runtime_events.jsonl",
            {
                "event": "agent_launch_finalized",
                "run_id": run_id,
                "turn_id": turn_id,
                "process_command": process_command,
                "sandbox_status": sandbox.status,
                "created_at": _now(),
            },
            append_jsonl=True,
        )

        process_stream_state: dict[Any, Any] = {}

        def emit_process_output(stream: str, chunk: str) -> bool:
            text = _public_agent_process_output(
                stream,
                chunk,
                stream_state=process_stream_state,
            )
            if not text:
                return False
            _emit_agent_run_event(
                event_sink,
                "agent_output",
                {
                    "tool": "agent_cli",
                    "run_id": run_id,
                    "turn_id": turn_id,
                    "provider": str(run_payload.get("provider") or ""),
                    "stream": stream,
                    "content": text,
                },
            )
            return True

        exit_code: int | None = None
        stdout = ""
        stderr = ""
        timed_out = False
        error = ""
        transport_attempts: list[dict[str, Any]] = []
        for candidate_index, (
            candidate_command,
            candidate_stdin,
            candidate_transport,
            candidate_reason,
        ) in enumerate(invocation_candidates):
            process_command = _resolve_local_process_command(candidate_command)
            stdin_payload_bytes = candidate_stdin
            prompt_transport = candidate_transport
            if candidate_reason in {"large_payload_forced_stdin", "large_payload_prompt_file"}:
                prompt_transport_reason = candidate_reason
            else:
                prompt_transport_reason = (
                    f"transport_fallback_from_{candidate_reason}"
                    if candidate_reason
                    else ""
                )
            attempt: dict[str, Any] = {
                "attempt_index": candidate_index + 1,
                "process_command": _redact_command_list(process_command),
                "prompt_transport": candidate_transport,
                "prompt_transport_reason": prompt_transport_reason,
                "sandbox": sandbox.audit,
            }
            try:
                completed = _run_cancellable_subprocess(
                    [*sandbox.wrapper, *process_command],
                    cwd=cwd,
                    input_bytes=candidate_stdin,
                    timeout=effective_timeout_sec,
                    idle_timeout=effective_idle_timeout_sec,
                    env=env,
                    is_cancelled=is_cancelled,
                    output_sink=emit_process_output,
                )
                exit_code = completed.exit_code
                stdout = completed.stdout
                stderr = completed.stderr
                timed_out = completed.timed_out
                error = completed.error
                attempt["exit_code"] = exit_code
                attempt["status"] = (
                    "cancelled"
                    if completed.cancelled
                    else "timeout"
                    if completed.timed_out
                    else "completed"
                    if exit_code == 0
                    else "error"
                )
                if completed.error:
                    attempt["error"] = _redact(completed.error)
                if completed.timeout_kind:
                    attempt["timeout_kind"] = completed.timeout_kind
                attempt["stderr_excerpt"] = _redact(stderr[:4000])
                attempt["stdout_excerpt"] = _redact(stdout[:4000])
                if completed.cancelled:
                    transport_attempts.append(attempt)
                    break
            except OSError as exc:
                exit_code = None
                stdout = ""
                stderr = ""
                error = str(exc)
                attempt["status"] = "error"
                attempt["error"] = _redact(error)
            transport_attempts.append(attempt)
            if exit_code == 0:
                timed_out = False
                error = ""
                break
            if candidate_index >= len(invocation_candidates) - 1:
                break

        if prompt_file_path:
            try:
                Path(prompt_file_path).unlink(missing_ok=True)
            except OSError:
                pass

        completed_at = _now()
        duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        cancelled = any(item.get("status") == "cancelled" for item in transport_attempts)
        status = "cancelled" if cancelled else "timeout" if timed_out else "completed" if exit_code == 0 else "error"
        execution_input = self._read_json_file("execution_input.json")
        if isinstance(execution_input, dict):
            execution_input["process_command"] = process_command
            execution_input["prompt_transport"] = prompt_transport
            execution_input["prompt_transport_reason"] = prompt_transport_reason
            execution_input["transport_attempts"] = transport_attempts
            self._write_json("execution_input.json", execution_input)
        self.record_raw_output(run_id, stdout=stdout, stderr=stderr)
        _emit_agent_run_event(
            event_sink,
            "tool_result",
            {
                "tool": "agent_cli",
                "run_id": run_id,
                "turn_id": turn_id,
                "status": status,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "timeout_kind": next(
                    (
                        str(item.get("timeout_kind") or "")
                        for item in transport_attempts
                        if item.get("timeout_kind")
                    ),
                    "",
                ),
                "stdout_tail": _redact(stdout[-4000:]),
                "stderr_tail": _redact(stderr[-4000:]),
                "error": _redact(error),
            },
        )
        _emit_agent_run_event(
            event_sink,
            "artifact",
            {
                "artifact": "raw_output.txt",
                "artifact_kind": "agent_raw_output",
                "content": "Agent 原始输出已保存为诊断产物，默认折叠。",
                "run_id": run_id,
                "turn_id": turn_id,
                "status": status,
            },
        )
        result = AgentRunExecutionResult(
            run_id=run_id,
            status=status,
            exit_code=exit_code,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            timed_out=timed_out,
            error=error,
            provider_diagnostics={
                **_provider_diagnostics_result_summary(provider_diagnostics),
                **_command_resolution_result_summary(command_resolution),
            },
        )
        self._write_json("execution_result.json", asdict(result))
        _emit_agent_run_event(
            event_sink,
            "artifact",
            {
                "artifact": "execution_result.json",
                "artifact_kind": "execution_result",
                "content": "Agent 执行结果已保存。",
                "run_id": run_id,
                "turn_id": turn_id,
                "status": status,
                "exit_code": exit_code,
            },
        )
        self._write_json(
            "agent_replay_plan.json",
            _agent_replay_plan_payload(
                run_payload=run_payload,
                run_id=run_id,
                turn_id=turn_id,
                status=status,
                cwd=cwd,
                timeout_sec=effective_timeout_sec,
                idle_timeout_sec=effective_idle_timeout_sec,
                command=configured_command,
                launch_command=launch_command,
                command_resolution=command_resolution,
                process_command=process_command,
                prompt_transport=prompt_transport,
                prompt_transport_reason=prompt_transport_reason,
                transport_attempts=transport_attempts,
                env_hints=env_hints,
                artifact_dir=self.artifact_dir,
                task_bundle_sha256=task_bundle_sha256,
                workflow_snapshot_sha256=workflow_snapshot_sha256,
                agent_output_contract_sha256=agent_output_contract_sha256,
                stdin_json_sha256=hashlib.sha256(stdin_payload.encode("utf-8")).hexdigest(),
                agent_instruction_policy=agent_instruction_policy,
                sandbox_audit=sandbox.audit,
            ),
        )
        self._write_json(
            "runtime_events.jsonl",
            {
                "event": "agent_run_completed",
                "run_id": run_id,
                "turn_id": turn_id,
                "status": status,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "error": error,
                "replay_plan_artifact": "agent_replay_plan.json",
                "created_at": completed_at,
            },
            append_jsonl=True,
        )
        self._write_json("agent_run.json", {**run_payload, "status": status})
        return result

    def _write_json(self, filename: str, payload: Any, *, append_jsonl: bool = False) -> None:
        path = self.artifact_dir / filename
        if append_jsonl:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
            return
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def _write_text(self, filename: str, content: str) -> None:
        (self.artifact_dir / filename).write_text(content, encoding="utf-8")

    def _read_json_file(self, filename: str) -> Any:
        try:
            return json.loads((self.artifact_dir / filename).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None


def _emit_agent_run_event(
    event_sink: Callable[[str, dict[str, Any]], None] | None,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    if event_sink is None:
        return
    try:
        public_payload = {key: value for key, value in payload.items() if value not in (None, "")}
        normalized = normalize_provider_event(event_type, public_payload)
        event_sink(event_type, {
            **public_payload,
            "harness_event_kind": normalized.kind,
            "harness_visibility": normalized.visibility,
            "harness_user_message": normalized.user_message,
        })
    except Exception:
        return


def _repo_path_label(path: str) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    return Path(text).name or text


class ArtifactValidationHarness:
    """Validates Agent-produced artifacts before they become evidence."""

    def __init__(self, artifact_dir: str | Path) -> None:
        self.artifact_dir = Path(artifact_dir)

    def validate_required_artifacts(self, *, required_artifacts: list[str]) -> ArtifactValidationResult:
        accepted: list[str] = []
        rejected: list[dict[str, str]] = []
        accepted_details: list[dict[str, Any]] = []
        rejected_details: list[dict[str, str]] = []
        for artifact in required_artifacts:
            safe_artifact = _safe_required_artifact(artifact)
            if not safe_artifact:
                item = {"artifact": artifact, "reason": "invalid_artifact_path", "path": ""}
                rejected.append({"artifact": artifact, "reason": item["reason"]})
                rejected_details.append(item)
                continue
            path = self.artifact_dir / safe_artifact
            if not path.exists():
                item = {
                    "artifact": artifact,
                    "reason": "missing_required_artifact",
                    "path": str(path),
                }
                rejected.append({"artifact": artifact, "reason": item["reason"]})
                rejected_details.append(item)
            elif path.is_dir():
                item = {
                    "artifact": artifact,
                    "reason": "artifact_is_directory",
                    "path": str(path),
                }
                rejected.append({"artifact": artifact, "reason": item["reason"]})
                rejected_details.append(item)
            else:
                accepted.append(safe_artifact)
                accepted_details.append(_artifact_detail(path, artifact=safe_artifact))
        return ArtifactValidationResult(
            status="invalid" if rejected else "ok",
            provenance_status="agent_artifact_present" if not rejected else "unverified_agent_claim",
            accepted_artifacts=accepted,
            rejected_artifacts=rejected,
            accepted_artifact_details=accepted_details,
            rejected_artifact_details=rejected_details,
        )

    def validate_mr_artifacts(self, *, required_artifacts: list[str]) -> ArtifactValidationResult:
        accepted: list[str] = []
        rejected: list[dict[str, str]] = []
        accepted_details: list[dict[str, Any]] = []
        rejected_details: list[dict[str, str]] = []
        warnings: list[str] = []

        for artifact in required_artifacts:
            safe_artifact = _safe_required_artifact(artifact)
            if not safe_artifact:
                item = {"artifact": artifact, "reason": "invalid_artifact_path", "path": ""}
                rejected.append({"artifact": artifact, "reason": item["reason"]})
                rejected_details.append(item)
                continue
            path = self.artifact_dir / safe_artifact
            if not path.exists():
                item = {
                    "artifact": artifact,
                    "reason": "missing_required_artifact",
                    "path": str(path),
                }
                rejected.append({"artifact": artifact, "reason": item["reason"]})
                rejected_details.append(item)
            elif path.is_dir():
                item = {
                    "artifact": artifact,
                    "reason": "artifact_is_directory",
                    "path": str(path),
                }
                rejected.append({"artifact": item["artifact"], "reason": item["reason"]})
                rejected_details.append(item)
            else:
                accepted.append(safe_artifact)
                accepted_details.append(_artifact_detail(path, artifact=safe_artifact))
        if rejected:
            return ArtifactValidationResult(
                status="invalid",
                provenance_status="unverified_agent_claim",
                accepted_artifacts=accepted,
                rejected_artifacts=rejected,
                accepted_artifact_details=accepted_details,
                rejected_artifact_details=rejected_details,
            )

        snapshot = self._read_json("mr_snapshot.json")
        diff_text = (self.artifact_dir / "diff.patch").read_text(encoding="utf-8")
        changed_files = self._read_json("changed_files.json")
        if not isinstance(snapshot, dict):
            item = {
                "artifact": "mr_snapshot.json",
                "reason": "invalid_json_object",
                "path": str(self.artifact_dir / "mr_snapshot.json"),
            }
            rejected.append({"artifact": item["artifact"], "reason": item["reason"]})
            rejected_details.append(item)
        if not isinstance(changed_files, list):
            item = {
                "artifact": "changed_files.json",
                "reason": "invalid_json_array",
                "path": str(self.artifact_dir / "changed_files.json"),
            }
            rejected.append({"artifact": item["artifact"], "reason": item["reason"]})
            rejected_details.append(item)

        for field_name in (
            "source", "mcp_profile", "mr_url", "project", "mr_id", "title",
            "source_branch", "target_branch", "base_commit", "head_commit",
            "diff_sha256", "changed_files_count",
        ):
            if isinstance(snapshot, dict) and snapshot.get(field_name) in {None, ""}:
                item = {
                    "artifact": "mr_snapshot.json",
                    "reason": f"missing_{field_name}",
                    "path": str(self.artifact_dir / "mr_snapshot.json"),
                }
                rejected.append({"artifact": item["artifact"], "reason": item["reason"]})
                rejected_details.append(item)

        if isinstance(snapshot, dict):
            actual_sha = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
            if snapshot.get("diff_sha256") != actual_sha:
                item = {
                    "artifact": "diff.patch",
                    "reason": "diff_sha256_mismatch",
                    "path": str(self.artifact_dir / "diff.patch"),
                }
                rejected.append({"artifact": item["artifact"], "reason": item["reason"]})
                rejected_details.append(item)

        if isinstance(changed_files, list):
            diff_paths = _paths_from_unified_diff(diff_text)
            for item in changed_files:
                item_path = str((item or {}).get("path") or "").replace("\\", "/")
                if item_path and item_path not in diff_paths:
                    warnings.append(f"changed file not present in diff: {item_path}")

        return ArtifactValidationResult(
            status="invalid" if rejected else "ok",
            provenance_status="agent_mcp_provenance" if not rejected else "unverified_agent_claim",
            accepted_artifacts=accepted,
            rejected_artifacts=rejected,
            accepted_artifact_details=accepted_details,
            rejected_artifact_details=rejected_details,
            warnings=warnings,
        )

    def _read_json(self, filename: str) -> Any:
        try:
            return json.loads((self.artifact_dir / filename).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None


def _paths_from_unified_diff(diff_text: str) -> set[str]:
    paths: set[str] = set()
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            for candidate in parts[-2:]:
                cleaned = re.sub(r"^[ab]/", "", candidate).replace("\\", "/")
                if cleaned:
                    paths.add(cleaned)
        elif line.startswith(("--- a/", "+++ b/")):
            paths.add(line[6:].replace("\\", "/"))
    return paths


def _artifact_detail(path: Path, *, artifact: str) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "artifact": artifact,
        "path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }


def _safe_required_artifact(artifact: Any) -> str:
    text = str(artifact or "").strip().replace("\\", "/")
    if not text:
        return ""
    posix = PurePosixPath(text)
    windows = PureWindowsPath(text)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or windows.root:
        return ""
    if any(part in {"", ".", ".."} for part in posix.parts):
        return ""
    return posix.as_posix()


def _effective_agent_timeout_sec(timeout_sec: int | float | None, run_payload: dict[str, Any]) -> int:
    requested = _coerce_positive_number(timeout_sec)
    if requested is not None:
        return max(1, int(requested))
    configured = _coerce_positive_number(run_payload.get("timeout_seconds"))
    if configured is not None:
        return max(1, int(configured))
    return _DEFAULT_AGENT_TOTAL_TIMEOUT_SEC


def _effective_agent_idle_timeout_sec(
    idle_timeout_sec: int | float | None,
    run_payload: dict[str, Any],
) -> float | None:
    requested = _coerce_positive_number(idle_timeout_sec)
    if requested is not None:
        return float(requested)
    configured = _coerce_positive_number(run_payload.get("idle_timeout_seconds"))
    if configured is not None:
        return float(configured)
    return _DEFAULT_AGENT_IDLE_TIMEOUT_SEC


def _coerce_positive_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _agent_replay_plan_payload(
    *,
    run_payload: dict[str, Any],
    run_id: str,
    turn_id: str,
    status: str,
    cwd: str,
    timeout_sec: int,
    idle_timeout_sec: float | None,
    command: list[str],
    launch_command: list[str],
    command_resolution: dict[str, Any],
    process_command: list[str],
    prompt_transport: str,
    prompt_transport_reason: str,
    transport_attempts: list[dict[str, Any]],
    env_hints: dict[str, str],
    artifact_dir: Path,
    task_bundle_sha256: str,
    workflow_snapshot_sha256: str,
    agent_output_contract_sha256: str,
    stdin_json_sha256: str,
    agent_instruction_policy: dict[str, Any],
    sandbox_audit: dict[str, Any],
) -> dict[str, Any]:
    artifact_hashes = _replay_artifact_hashes(
        artifact_dir,
        [
            "agent_run.json",
            "task_bundle.json",
            "workflow_snapshot.json",
            "agent_output_contract.json",
            "execution_input.json",
            "execution_result.json",
            "raw_output.txt",
        ],
    )
    artifact_hashes.update({
        "task_bundle_sha256": task_bundle_sha256,
        "workflow_snapshot_sha256": workflow_snapshot_sha256,
        "agent_output_contract_sha256": agent_output_contract_sha256,
        "stdin_json_sha256": stdin_json_sha256,
    })
    return {
        "version": 1,
        "replay_status": "ready" if status in {"completed", "error", "timeout"} else "recorded",
        "run_id": run_id,
        "turn_id": turn_id,
        "provider": str(run_payload.get("provider") or ""),
        "mcp_profile": str(run_payload.get("mcp_profile") or ""),
        "status": status,
        "artifact_dir": str(artifact_dir),
        "cwd": cwd,
        "timeout_sec": timeout_sec,
        "idle_timeout_sec": idle_timeout_sec,
        "command": _redact_command_list(command),
        "launch_command": _redact_command_list(launch_command),
        "process_command": _redact_command_list(process_command),
        "command_resolution": _redact_replay_payload(command_resolution),
        "prompt_transport": prompt_transport,
        "prompt_transport_reason": prompt_transport_reason,
        "transport_attempts": transport_attempts,
        "prompt_source": (
            "execution_input.json:stdin"
            if prompt_transport == "stdin"
            else "execution_input.json:process_command"
        ),
        "agent_instruction_policy": agent_instruction_policy,
        "env_hints": env_hints,
        "artifact_hashes": artifact_hashes,
        "replay_steps": [
            "Inspect agent_replay_plan.json, execution_input.json, and agent_output_contract.json.",
            "Restore the same cwd and readonly environment variables.",
            "Pass execution_input.json['stdin'] through the recorded prompt transport.",
            "Compare regenerated required artifacts with accepted artifact hashes before using them as evidence.",
        ],
        "safety_boundary": {
            "readonly_env_required": True,
            "codetalk_validates_outputs": True,
            "raw_output_is_diagnostic_only": True,
            "remote_mcp_credentials_owner": "agent_cli",
            "os_sandbox": str(sandbox_audit.get("status") or "unknown"),
            "os_sandbox_engine": str(sandbox_audit.get("engine") or ""),
            "os_sandbox_policy_artifact": "sandbox_policy.json",
        },
    }


def _replay_artifact_hashes(artifact_dir: Path, names: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name in names:
        path = artifact_dir / name
        if not path.exists() or not path.is_file():
            continue
        try:
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
    return hashes


def _redact_command_list(command: list[str]) -> list[str]:
    return [_redact(str(part)) for part in command]


def _resolve_local_process_command(command: list[str]) -> list[str]:
    if not command:
        return []
    resolved = [str(part) for part in command]
    executable = resolved[0]
    if os.name == "nt" and not PureWindowsPath(executable).is_absolute():
        located = shutil.which(executable)
        if located:
            resolved[0] = located
            return resolved
    if executable != "python" or shutil.which(executable):
        return resolved
    if sys.executable:
        resolved[0] = sys.executable
    return resolved


def _redact_replay_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        result: dict[str, Any] = {}
        for key, value in payload.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                result[key_text] = "<redacted>"
            else:
                result[key_text] = _redact_replay_payload(value)
        return result
    if isinstance(payload, list):
        return [_redact_replay_payload(item) for item in payload]
    if isinstance(payload, str):
        return _redact(payload)
    return payload


def _is_sensitive_key(key: str) -> bool:
    return bool(
        re.search(
            r"(?i)(api[-_]?key|token|access[-_]?token|secret|password)",
            key or "",
        )
    )


def _context_discovery_decision_summary(task_bundle: dict[str, Any]) -> dict[str, Any]:
    decision = task_bundle.get("context_discovery_decision")
    if not isinstance(decision, dict):
        return {}
    summary: dict[str, Any] = {}
    for provider, payload in decision.items():
        if not isinstance(provider, str) or not isinstance(payload, dict):
            continue
        item: dict[str, Any] = {}
        for key in (
            "requested_by_agent_instructions",
            "codetalk_callable",
            "agent_owned_possible",
            "fallback_path",
            "warnings",
        ):
            if key in payload:
                item[key] = payload[key]
        if item:
            summary[provider] = item
    return summary


def _agent_instruction_policy_summary(task_bundle: dict[str, Any]) -> dict[str, Any]:
    instructions = task_bundle.get("agent_instructions")
    decision = task_bundle.get("context_discovery_decision")
    files_payload: list[dict[str, Any]] = []
    fast_context_requested_by_files: list[str] = []
    if isinstance(decision, dict):
        fast_context = decision.get("fast-context")
        if isinstance(fast_context, dict):
            fast_context_requested_by_files = [
                str(item)
                for item in fast_context.get("requested_by_files") or []
                if str(item)
            ]
    if isinstance(instructions, dict):
        for item in instructions.get("files") or []:
            if not isinstance(item, dict):
                continue
            relative_path = str(item.get("relative_path") or "").strip()
            content = str(item.get("content") or "")
            sha256 = str(item.get("sha256") or "").strip()
            if not relative_path:
                continue
            lower_content = content.lower()
            files_payload.append({
                "relative_path": relative_path,
                "sha256": sha256,
                "content_chars": len(content),
                "contains_fast_context": (
                    "fast-context" in lower_content
                    or "fast_context" in lower_content
                    or "mcp__fast-context__fast_context_search" in lower_content
                ),
                "content_excerpt": _redact(content[:500]),
            })
    fast_context_first = any(item.get("contains_fast_context") for item in files_payload)
    if isinstance(decision, dict):
        fast_context = decision.get("fast-context")
        if isinstance(fast_context, dict):
            fast_context_first = fast_context_first or bool(
                fast_context.get("requested_by_agent_instructions")
            )
    return {
        "files": files_payload,
        "file_count": len(files_payload),
        "fast_context_first": fast_context_first,
        "fast_context_requested_by_files": fast_context_requested_by_files,
        "raw_output_reuse": "never_without_validation",
        "codetalk_validates_agent_claims": True,
    }


def _agent_process_invocation_for_harness(
    *,
    provider: str,
    command: list[str],
    prompt: str,
) -> tuple[list[str], bytes, str]:
    """Reuse external-agent prompt transport rules for Workbench task runs."""
    try:
        from app.services.external_agent_discovery import _agent_process_invocation

        return _agent_process_invocation(provider, command, prompt)
    except Exception:
        return list(command), prompt.encode("utf-8"), "stdin"


def _agent_process_invocation_candidates_for_harness(
    *,
    provider: str,
    command: list[str],
    prompt: str,
    prompt_transport: str = "",
    artifact_dir: str = "",
) -> list[tuple[list[str], bytes, str, str]]:
    """Reuse external-agent transport fallback rules for Workbench task runs."""
    explicit_transport = str(prompt_transport or "").strip()
    if explicit_transport:
        explicit_candidate = _explicit_agent_runtime_invocation_candidate(
            command=command,
            prompt=prompt,
            prompt_transport=explicit_transport,
            artifact_dir=artifact_dir,
        )
        if explicit_candidate is not None:
            return [explicit_candidate]
    try:
        from app.services.external_agent_discovery import _agent_process_invocation_candidates

        return _agent_process_invocation_candidates(provider, command, prompt)
    except Exception:
        command_value, stdin_payload, transport = _agent_process_invocation_for_harness(
            provider=provider,
            command=command,
            prompt=prompt,
        )
        return [(command_value, stdin_payload, transport, "")]


def _explicit_agent_runtime_invocation_candidate(
    *,
    command: list[str],
    prompt: str,
    prompt_transport: str,
    artifact_dir: str = "",
) -> tuple[list[str], bytes, str, str] | None:
    if not command:
        return None
    executable, *base_args = [str(part) for part in command]
    if prompt_transport == "stdin":
        return list(command), prompt.encode("utf-8"), "stdin", "agent_runtime_prompt_transport"
    if prompt_transport == "argv_last":
        return [*command, prompt], b"", "argv_last", "agent_runtime_prompt_transport"
    try:
        from app.services.agent_cli_bridge import (
            _claude_print_args,
            _codex_exec_json_args,
            _opencode_run_args,
        )
    except Exception:
        return None
    if prompt_transport == "claude_print_arg":
        return (
            [executable, *_claude_print_args(base_args, prompt)],
            b"",
            prompt_transport,
            "agent_runtime_prompt_transport",
        )
    if prompt_transport == "codex_exec_json":
        args = _codex_exec_json_args(base_args, prompt)
        if artifact_dir:
            args = _append_option_value_once(args, "--add-dir", artifact_dir)
            # Run Codex from the writable task-artifact root.  The repository
            # remains an explicit read-only root through CODETALK_REPO_PATH and
            # the outer sandbox, so project-level instructions cannot lure the
            # Agent into creating temporary generators inside user source.
            args = _append_option_value_once(args, "--cd", artifact_dir)
        return (
            [executable, *args],
            prompt.encode("utf-8"),
            prompt_transport,
            "agent_runtime_prompt_transport",
        )
    if prompt_transport == "opencode_run_arg":
        return (
            [executable, *_opencode_run_args(base_args, prompt)],
            b"",
            prompt_transport,
            "agent_runtime_prompt_transport",
        )
    return None


def _append_option_value_once(args: list[str], flag: str, value: str) -> list[str]:
    result = list(args)
    for index, item in enumerate(result[:-1]):
        if item == flag and result[index + 1] == value:
            return result
    result.extend([flag, value])
    return result


def _codex_external_sandbox_command(
    command: list[str],
    *,
    sandbox_active: bool,
) -> list[str]:
    return codex_command_for_outer_sandbox(
        command,
        sandbox_active=sandbox_active,
    )


def _finalize_invocation_candidates_for_sandbox(
    candidates: list[tuple[list[str], bytes, str, str]],
    *,
    sandbox_active: bool,
) -> list[tuple[list[str], bytes, str, str]]:
    return [
        (
            _codex_external_sandbox_command(
                candidate_command,
                sandbox_active=sandbox_active,
            ),
            candidate_stdin,
            candidate_transport,
            candidate_reason,
        )
        for (
            candidate_command,
            candidate_stdin,
            candidate_transport,
            candidate_reason,
        ) in candidates
    ]


def _task_run_read_roots(artifact_dir: Path) -> list[Path]:
    resolved = artifact_dir.resolve()
    if resolved.parent.name != "agent_runs":
        return []
    return [resolved.parent.parent]


def _prefer_native_macos_git_path(
    env: dict[str, str],
    *,
    platform_name: str = sys.platform,
    native_git: Path = Path("/Library/Developer/CommandLineTools/usr/bin/git"),
    exists: Callable[[Path], bool] = Path.exists,
) -> dict[str, str]:
    result = dict(env)
    if not str(platform_name).lower().startswith("darwin") or not exists(native_git):
        return result
    native_bin = str(native_git.parent)
    current_path = str(result.get("PATH") or "")
    path_parts = [part for part in current_path.split(os.pathsep) if part]
    if native_bin not in path_parts:
        result["PATH"] = os.pathsep.join([native_bin, *path_parts])
    return result


def _vetted_analysis_tool_bin_paths(
    *,
    platform_name: str = sys.platform,
    exists: Callable[[Path], bool] = Path.is_dir,
) -> list[str]:
    """Return installation roots for CodeTalk's fixed local analysis toolchain.

    Service launchers often receive a deliberately minimal PATH.  Do not inherit
    arbitrary user bin directories just to make an Agent shell convenient: these
    directories only cover the package-manager and OS roots where CodeTalk's
    approved read-only analysis commands (notably ``rg`` and ``jq``) are installed.
    """
    platform = str(platform_name).lower()
    if platform.startswith("darwin"):
        candidates = (
            Path("/opt/homebrew/bin"),
            Path("/usr/local/bin"),
            Path("/Library/Developer/CommandLineTools/usr/bin"),
            Path("/usr/bin"),
            Path("/bin"),
        )
    elif platform.startswith("linux"):
        candidates = (Path("/usr/local/bin"), Path("/usr/bin"), Path("/bin"))
    else:
        return []
    return [str(path) for path in candidates if exists(path)]


def _prepend_vetted_analysis_tool_paths(
    env: dict[str, str],
    *,
    platform_name: str = sys.platform,
) -> dict[str, str]:
    """Make the fixed local analysis toolchain visible to isolated Agent shells."""
    result = dict(env)
    current_parts = [part for part in str(result.get("PATH") or "").split(os.pathsep) if part]
    prefix = _vetted_analysis_tool_bin_paths(platform_name=platform_name)
    result["PATH"] = os.pathsep.join([*prefix, *[part for part in current_parts if part not in prefix]])
    return result


def _base_agent_process_env_for_harness(
    *,
    provider: str,
    repo_path: str,
    artifact_dir: Path,
) -> dict[str, str]:
    """Use the same environment hints as source discovery, including CCR config."""
    try:
        from app.services.external_agent_discovery import _agent_process_env

        return _agent_process_env(
            provider,
            repo_path,
            artifact_dir=artifact_dir,
        )
    except Exception:
        return filtered_agent_environment()


def _agent_process_env_for_harness(
    *,
    provider: str,
    repo_path: str,
    artifact_dir: Path,
    command: list[str] | None = None,
    prompt_transport: str = "",
) -> dict[str, str]:
    env = _base_agent_process_env_for_harness(
        provider=provider,
        repo_path=repo_path,
        artifact_dir=artifact_dir,
    )
    if provider != "agent-runtime:default-claude-code" or not command:
        return env
    try:
        from app.services.agent_cli_bridge import _build_env

        credential_env = _build_env({
            "id": "default-claude-code",
            "provider": "claude",
            "command": str(command[0]),
            "args": [str(item) for item in command[1:]],
            "prompt_transport": str(prompt_transport or ""),
        })
        token = str(credential_env.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
        if token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    except Exception:
        # Authentication readiness is reported by the actual CLI invocation.
        # Never weaken the sandbox or expose the user's Keychain as a fallback.
        pass
    return env


def _launch_command_from_provider_health(
    configured_command: list[str],
    provider_diagnostics: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    health = provider_diagnostics.get("health")
    if not isinstance(health, dict) or health.get("status") != "available":
        return list(configured_command), {"source": "configured_command"}
    argv = health.get("argv")
    if not isinstance(argv, list) or not argv:
        return list(configured_command), {"source": "configured_command", "reason": "health_argv_missing"}
    launch_kind = str(health.get("launch_kind") or "")
    health_attempts = [
        attempt for attempt in health.get("attempts") or []
        if isinstance(attempt, dict)
    ]
    active_attempt = health_attempts[-1] if health_attempts else {}
    active_resolution = (
        active_attempt.get("resolution")
        if isinstance(active_attempt.get("resolution"), dict)
        else {}
    )
    should_use_health_argv = (
        bool(provider_diagnostics.get("provider_snapshot_present"))
        or bool(health.get("used_fallback", False))
        or launch_kind in {"powershell", "powershell-profile", "powershell-script"}
    )
    if not should_use_health_argv:
        return list(configured_command), {
            "source": "configured_command",
            "health_status": "available",
            "reason": "ad_hoc_command_preserved",
            "health_attempt_count": len(health_attempts),
            "active_attempt_resolution": active_resolution,
        }
    launch_command = [str(part) for part in argv]
    return launch_command, {
        "source": "provider_health",
        "used_fallback": bool(health.get("used_fallback", False)),
        "launch_kind": launch_kind,
        "configured_command": str(health.get("configured_command") or ""),
        "path": str(health.get("path") or ""),
        "health_attempt_count": len(health_attempts),
        "active_attempt_resolution": active_resolution,
    }


def _provider_diagnostics_snapshot(
    *,
    run_payload: dict[str, Any],
    task_bundle: dict[str, Any],
) -> dict[str, Any]:
    provider = str(run_payload.get("provider") or "").strip()
    snapshot = task_bundle.get("provider_snapshot")
    provider_info: dict[str, Any] = {}
    if isinstance(snapshot, dict):
        providers = snapshot.get("providers")
        if isinstance(providers, dict):
            raw_provider = providers.get(provider)
            if isinstance(raw_provider, dict):
                provider_info = raw_provider
    diagnostics = provider_info.get("diagnostics") if isinstance(provider_info, dict) else {}
    if not isinstance(diagnostics, dict):
        diagnostics = {}
    health = _agent_provider_health_snapshot(
        provider=provider,
        command=str(diagnostics.get("configured_command_text") or " ".join(
            str(part) for part in run_payload.get("command") or []
        )).strip(),
        fallback_commands=[
            str(command).strip()
            for command in diagnostics.get("fallback_command_texts") or []
            if str(command).strip()
        ],
    )
    return {
        "provider": provider,
        "status": str(provider_info.get("status") or "unknown") if provider_info else "unknown",
        "provider_snapshot_present": bool(provider_info),
        "owner": str(provider_info.get("owner") or "agent_cli") if provider_info else "agent_cli",
        "agent_owned": bool(provider_info.get("agent_owned", True)) if provider_info else True,
        "codetalk_callable": bool(provider_info.get("codetalk_callable", False)) if provider_info else False,
        "command": [str(part) for part in run_payload.get("command") or []],
        "cwd": str(run_payload.get("cwd") or ""),
        "mcp_profile": str(run_payload.get("mcp_profile") or ""),
        "diagnostics": diagnostics,
        "health": health,
        "credential_boundary": str(provider_info.get("credential_boundary") or "") if provider_info else "",
        "unavailable_behavior": str(provider_info.get("unavailable_behavior") or "") if provider_info else "",
    }


def _agent_provider_health_snapshot(
    *,
    provider: str,
    command: str,
    fallback_commands: list[str],
) -> dict[str, Any]:
    if not provider:
        return {"status": "unknown", "reason": "missing provider"}
    try:
        from app.services.external_agent_discovery import (
            check_provider_health,
            redact_agent_diagnostic_text,
        )

        health = check_provider_health(
            provider,
            command,
            fallback_commands=fallback_commands,
        )
        return _redact_diagnostic_payload(health, redact_agent_diagnostic_text)
    except Exception as exc:
        return {
            "status": "error",
            "reason": _redact(str(exc)),
        }


def _agent_provider_env_hints(provider: str) -> dict[str, str]:
    if not provider:
        return {}
    try:
        from app.services.external_agent_discovery import external_agent_provider_env_hints

        return {
            str(key): str(value)
            for key, value in external_agent_provider_env_hints(provider).items()
            if str(key)
        }
    except Exception:
        return {}


def _redact_diagnostic_payload(payload: Any, redactor: Any) -> Any:
    if isinstance(payload, dict):
        return {
            str(key): _redact_diagnostic_payload(value, redactor)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_redact_diagnostic_payload(item, redactor) for item in payload]
    if isinstance(payload, str):
        return redactor(payload)
    return payload


def _provider_diagnostics_result_summary(payload: dict[str, Any]) -> dict[str, Any]:
    diagnostics = payload.get("diagnostics")
    if not isinstance(diagnostics, dict):
        diagnostics = {}
    health = payload.get("health")
    if not isinstance(health, dict):
        health = {}
    return {
        "artifact": "provider_diagnostics.json",
        "provider": str(payload.get("provider") or ""),
        "status": str(payload.get("status") or ""),
        "owner": str(payload.get("owner") or ""),
        "agent_owned": bool(payload.get("agent_owned", False)),
        "codetalk_callable": bool(payload.get("codetalk_callable", False)),
        "health_status": str(health.get("status") or "unknown"),
        "launch_kind": str(health.get("launch_kind") or ""),
        "used_fallback": bool(health.get("used_fallback", False)),
        "startup_probe_endpoint": str(diagnostics.get("startup_probe_endpoint") or ""),
        "prompt_transport": str(
            diagnostics.get("startup_probe_transport")
            or diagnostics.get("prompt_transport")
            or ""
        ),
        "mcp_credentials_owner": str(diagnostics.get("mcp_credentials_owner") or ""),
    }


def _command_resolution_result_summary(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    summary: dict[str, Any] = {
        "command_resolution_source": str(payload.get("source") or ""),
    }
    if "reason" in payload:
        summary["command_resolution_reason"] = str(payload.get("reason") or "")
    if "used_fallback" in payload:
        summary["command_resolution_used_fallback"] = bool(payload.get("used_fallback", False))
    if "launch_kind" in payload:
        summary["command_resolution_launch_kind"] = str(payload.get("launch_kind") or "")
    return {key: value for key, value in summary.items() if value not in {"", None}}


_SECRET_RE = re.compile(
    r"(?i)\b(api[-_]?key|token|access[-_]?token|secret|password)\s*=\s*[^\s]+"
)
_SECRET_COLON_RE = re.compile(
    r"(?i)([\"']?\b(api[-_]?key|token|access[-_]?token|secret|password)\b[\"']?\s*:\s*)"
    r"([\"'])?[^\"'\s,}\]]+([\"'])?"
)
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")


def _redact(text: str) -> str:
    value = _SECRET_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text or "")
    value = _SECRET_COLON_RE.sub(
        lambda m: f"{m.group(1)}{m.group(3) or ''}<redacted>{m.group(4) or ''}",
        value,
    )
    return _BEARER_RE.sub(r"\1<redacted>", value)


def _decode_subprocess_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return _decode_agent_cli_output(value)
    return _decode_agent_cli_output(value.encode("utf-8", errors="surrogatepass"))


def _run_cancellable_subprocess(
    command: list[str],
    *,
    cwd: str,
    input_bytes: bytes | None,
    timeout: int,
    idle_timeout: float | None,
    env: dict[str, str],
    is_cancelled: Callable[[], bool] | None = None,
    output_sink: Callable[[str, str], bool] | None = None,
) -> _SubprocessExecutionResult:
    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        **popen_kwargs,
    )
    captured: dict[str, Any] = {}
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    lock = threading.Lock()
    started = time.monotonic()
    last_activity = {"at": started}

    def _mark_activity() -> None:
        with lock:
            last_activity["at"] = time.monotonic()

    def _read_stream(pipe: Any, chunks: list[bytes], name: str) -> None:
        pending = b""
        try:
            while True:
                chunk = os.read(pipe.fileno(), 4096)
                if not chunk:
                    break
                chunks.append(chunk)
                if output_sink is None:
                    _mark_activity()
                    continue
                pending += chunk
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    if output_sink(name, _decode_subprocess_text(line + b"\n")):
                        _mark_activity()
            if pending and output_sink is not None:
                if output_sink(name, _decode_subprocess_text(pending)):
                    _mark_activity()
        except BaseException as exc:  # pragma: no cover - defensive bridge for OS process edge cases.
            captured[f"{name}_error"] = exc

    def _write_stdin() -> None:
        try:
            if process.stdin is None:
                return
            if input_bytes:
                process.stdin.write(input_bytes)
                process.stdin.flush()
        except BrokenPipeError:
            return
        except BaseException as exc:  # pragma: no cover - defensive bridge for OS process edge cases.
            captured["stdin_error"] = exc
        finally:
            try:
                if process.stdin is not None:
                    process.stdin.close()
            except OSError:
                pass

    stdout_thread = threading.Thread(
        target=_read_stream,
        args=(process.stdout, stdout_chunks, "stdout"),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_read_stream,
        args=(process.stderr, stderr_chunks, "stderr"),
        daemon=True,
    )
    stdin_thread = threading.Thread(target=_write_stdin, daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    stdin_thread.start()
    timed_out = False
    timeout_kind = ""
    cancelled = False
    while process.poll() is None:
        if is_cancelled is not None and _safe_is_cancelled(is_cancelled):
            cancelled = True
            _terminate_process_group(process)
            break
        elapsed = time.monotonic() - started
        with lock:
            inactive_for = time.monotonic() - last_activity["at"]
        hard_timeout = max(3600.0, float(timeout) * 4)
        if elapsed > hard_timeout:
            timed_out = True
            timeout_kind = "hard"
            _terminate_process_group(process)
            break
        if inactive_for > max(1, int(timeout)):
            timed_out = True
            timeout_kind = "total"
            _terminate_process_group(process)
            break
        if idle_timeout is not None and idle_timeout > 0:
            if inactive_for > float(idle_timeout):
                timed_out = True
                timeout_kind = "idle"
                _terminate_process_group(process)
                break
        time.sleep(0.1)
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        _kill_process_group(process)
    _terminate_process_group(process)
    stdin_thread.join(1)
    stdout_thread.join(3)
    stderr_thread.join(3)
    if stdout_thread.is_alive() or stderr_thread.is_alive():
        _kill_process_group(process)
        stdout_thread.join(1)
        stderr_thread.join(1)
    error_obj = (
        captured.get("stdout_error")
        or captured.get("stderr_error")
        or captured.get("stdin_error")
    )
    if error_obj is not None:
        raise OSError(str(error_obj))
    stdout = b"".join(stdout_chunks)
    stderr = b"".join(stderr_chunks)
    return _SubprocessExecutionResult(
        exit_code=process.returncode,
        stdout=_decode_subprocess_text(stdout),
        stderr=_decode_subprocess_text(stderr),
        timed_out=timed_out,
        timeout_kind=timeout_kind,
        cancelled=cancelled,
        error=(
            "agent run cancelled by user"
            if cancelled
            else f"agent run idle timed out after {idle_timeout}s without output"
            if timed_out and timeout_kind == "idle"
            else f"agent run timed out after {timeout}s"
            if timed_out
            else ""
        ),
    )


def _safe_is_cancelled(is_cancelled: Callable[[], bool]) -> bool:
    try:
        return bool(is_cancelled())
    except Exception:
        return False


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        if process.poll() is not None:
            return
        try:
            process.terminate()
        except OSError:
            pass
        return

    process_group_id = process.pid
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        try:
            if process.poll() is None:
                process.terminate()
        except OSError:
            pass
        return
    if process.poll() is None:
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
    deadline = time.monotonic() + 1.0
    while _sync_process_group_exists(process_group_id) and time.monotonic() < deadline:
        time.sleep(0.02)
    if _sync_process_group_exists(process_group_id):
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except OSError:
            pass


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        if process.poll() is not None:
            return
        try:
            process.kill()
        except OSError:
            pass
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        try:
            if process.poll() is None:
                process.kill()
        except OSError:
            pass


def _sync_process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
