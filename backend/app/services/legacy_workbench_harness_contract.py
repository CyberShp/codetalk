"""Compatibility contract for frozen V1/V2 specialist Workbench runs.

New workflow contracts must not import these fields into the generic Harness
payload. This module exists only to preserve the already-published V1/V2
prompt and diagnostic artifacts until their validators are migrated.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


_MAX_PROFESSIONAL_CONSTRAINTS = 12


def legacy_prompt_omitted_keys() -> set[str]:
    return {"test_activity_contract"}


def without_legacy_contract_fields(contract: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in contract.items()
        if key not in {"execution_contract", "test_activity_contract"}
    }


def is_legacy_workbench_harness_contract(
    *,
    task_bundle: dict[str, Any],
    workflow_snapshot: dict[str, Any],
) -> bool:
    raw_version = task_bundle.get("compiled_contract_version")
    if raw_version is None:
        raw_version = workflow_snapshot.get("compiled_contract_version")
    if raw_version is None:
        for key in ("compiled_definition", "compiled_plan"):
            candidate = task_bundle.get(key)
            if isinstance(candidate, dict) and candidate.get("compiled_contract_version") is not None:
                raw_version = candidate.get("compiled_contract_version")
                break
    if raw_version in (None, ""):
        return True
    if isinstance(raw_version, bool):
        return False
    try:
        return int(raw_version) in {1, 2}
    except (TypeError, ValueError):
        return False


def compact_legacy_prompt_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Project the frozen specialist contract into the historical Agent prompt."""

    result = {
        key: contract[key]
        for key in (
            "contract_version",
            "target",
            "required_outputs",
            "executor_requirements",
            "evidence_policy",
            "black_box_boundary",
            "focus_rationale",
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
        if not isinstance(item, dict) or len(constraints) >= _MAX_PROFESSIONAL_CONSTRAINTS:
            continue
        constraints.append(
            {
                key: item[key]
                for key in ("id", "assertion", "evidence")
                if key in item
            }
        )
    if constraints:
        result["professional_constraints"] = constraints
    result["validator_ownership"] = {
        "full_schema": "CodeTalk validator",
        "regex_correction_rules": "CodeTalk validator",
        "required_agent_behavior": (
            "Use verified source evidence, distinguish facts from hypotheses, "
            "and write only declared artifacts."
        ),
    }
    return result


def build_legacy_workbench_harness_contract(
    *,
    run: Any,
    task_bundle: dict[str, Any],
    workflow_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Reproduce the historical V1/V2 Agent output contract verbatim."""

    retry_required_artifacts = [
        str(item)
        for item in task_bundle.get("quality_retry_required_artifacts") or []
        if str(item).strip()
    ]
    required_artifacts = retry_required_artifacts or [
        str(item) for item in task_bundle.get("required_artifacts") or []
    ]
    expected_output_schemas = [
        item
        for item in task_bundle.get("expected_output_schemas") or []
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
        item
        for item in task_bundle.get("expected_semantic_outputs") or []
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
        item
        for item in task_bundle.get("skill_instructions") or []
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
        "workflow_id": str(
            task_bundle.get("workflow_id") or workflow_snapshot.get("id") or ""
        ),
        "mcp_profile": run.mcp_profile,
        "skills": skills,
        "skill_injection": {
            "enabled": bool(skills),
            "source": "workflow_agent_step",
            "ids": skills,
            "instructions": skill_instructions,
            "rule": (
                "Selected skills are task-method constraints injected through task_bundle "
                "and must shape the final artifacts."
            ),
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
            "rules": (
                input_materials.get("rules")
                if isinstance(input_materials.get("rules"), dict)
                else {}
            ),
        },
        "black_box_generation_policy": black_box_generation_policy,
        "retry_validation_feedback": retry_validation_feedback,
        "retry_quality_feedback": retry_quality_feedback,
        "evidence_rules": _legacy_evidence_rules(),
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


def legacy_prompt_fields(
    *,
    task_bundle: dict[str, Any],
    output_contract: dict[str, Any],
    execution_contract: dict[str, Any],
) -> dict[str, Any]:
    activity = (
        task_bundle.get("test_activity_contract")
        if isinstance(task_bundle.get("test_activity_contract"), dict)
        else output_contract.get("test_activity_contract")
        if isinstance(output_contract.get("test_activity_contract"), dict)
        else execution_contract.get("test_activity_contract")
        if isinstance(execution_contract.get("test_activity_contract"), dict)
        else {}
    )
    return {"test_activity_contract": compact_legacy_prompt_contract(activity)}


def legacy_invocation_manifest_fields(
    *,
    task_bundle: dict[str, Any],
    output_contract: dict[str, Any],
    execution_contract: dict[str, Any],
) -> dict[str, Any]:
    activity = (
        task_bundle.get("test_activity_contract")
        if isinstance(task_bundle.get("test_activity_contract"), dict)
        else output_contract.get("test_activity_contract")
        if isinstance(output_contract.get("test_activity_contract"), dict)
        else execution_contract.get("test_activity_contract")
        if isinstance(execution_contract.get("test_activity_contract"), dict)
        else {}
    )
    return {
        "test_activity_contract": activity,
        "artifact_contract": output_contract,
    }


def legacy_invocation_artifact_event_fields(
    manifest: dict[str, Any],
) -> dict[str, Any]:
    activity = (
        manifest.get("test_activity_contract")
        if isinstance(manifest.get("test_activity_contract"), dict)
        else {}
    )
    artifact_contract = (
        manifest.get("artifact_contract")
        if isinstance(manifest.get("artifact_contract"), dict)
        else {}
    )
    if not activity and not artifact_contract:
        return {}
    return {
        "test_activity_contract": _public_test_activity_contract(activity),
        "artifact_contract": _public_artifact_contract(
            artifact_contract,
            required_outputs=activity.get("required_outputs"),
        ),
    }


def legacy_required_outputs(manifest: dict[str, Any]) -> list[str]:
    activity = manifest.get("test_activity_contract")
    if not isinstance(activity, dict):
        return []
    return [str(item) for item in activity.get("required_outputs") or [] if str(item).strip()]


def _legacy_evidence_rules() -> dict[str, Any]:
    return {
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
        "completion_protocol": {
            "owner": "codetalk_harness",
            "instructions": (
                "Write every declared artifact, then immediately finish the Agent turn with a short "
                "completion summary. Do not run a second full-repository, full-artifact, or custom "
                "Python validation pass after writing files: CodeTalk owns schema validation, exact "
                "source re-read, claim verification, quality gates, report materialization, and any "
                "scoped repair. Before writing, you may make only small targeted checks needed to "
                "avoid malformed JSON or a missing declared artifact."
            ),
            "post_write_agent_work": "forbidden_except_missing_artifact_fix",
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
    }


def _public_test_activity_contract(contract: dict[str, Any]) -> dict[str, Any]:
    if not contract:
        return {}
    return {
        "target": str(contract.get("target") or ""),
        "domain_profiles": [
            str(item) for item in contract.get("domain_profiles") or [] if str(item).strip()
        ],
        "required_outputs": [
            str(item) for item in contract.get("required_outputs") or [] if str(item).strip()
        ],
    }


def _public_artifact_contract(
    contract: dict[str, Any],
    *,
    required_outputs: Any = None,
) -> dict[str, Any]:
    outputs = [
        str(item)
        for item in (
            contract.get("required_outputs")
            or contract.get("required_artifacts")
            or required_outputs
            or []
        )
        if str(item).strip()
    ]
    template_source = (
        contract.get("artifact_contract")
        if isinstance(contract.get("artifact_contract"), dict)
        else contract
    )
    if not contract and not outputs:
        return {}
    return {
        "required_outputs": outputs,
        "templates": sorted(
            str(key)
            for key in template_source.keys()
            if str(key).strip() and isinstance(template_source, dict)
        ),
        "artifact_dir_policy": str(contract.get("artifact_dir_policy") or ""),
        "download_delivery": bool(contract.get("download_delivery")),
    }
