"""Deterministic input-consumption ledger for frozen workflow inputs."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


_RUNTIME_STAGE_ALIASES = {
    "source_analysis": "source_evidence",
    "breadth_inventory": "breadth_inventory",
    "flow_evidence_pack": "flow_modeling",
    "flow_outline": "flow_modeling",
    "business_flow": "flow_modeling",
    "developer_explanation": "flow_modeling",
    "scenario_expansion": "scenario_expansion",
    "sfmea": "sfmea",
    "black_box_cases": "black_box_design",
    "test_design_governance": "black_box_design",
    "coverage_judge": "independent_judge",
    "behavior_claim_validation": "independent_judge",
    "test_design_mindmap": "publish",
    "publish": "publish",
}

_ARTIFACT_STAGE_ALIASES = {
    "source_analysis.md": "source_evidence",
    "source_scope.json": "source_evidence",
    "evidence_cards.json": "source_evidence",
    "entrypoints.json": "breadth_inventory",
    "flows.json": "breadth_inventory",
    "flow_outline.json": "flow_modeling",
    "flow_cards.json": "flow_modeling",
    "scenario_candidates.json": "scenario_expansion",
    "sfmea.json": "sfmea",
    "风险点与SFMEA.md": "sfmea",
    "black_box_cases.json": "black_box_design",
    "黑盒测试设计.md": "black_box_design",
    "judge_report.json": "independent_judge",
    "claim_evidence_ledger.json": "independent_judge",
    "task_artifact_manifest.json": "publish",
}


def build_input_consumption_ledger(
    *,
    input_snapshot: dict[str, Any],
    stage_specs: list[dict[str, Any]],
    input_definitions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Freeze named inputs and the stage-level evidence of their use.

    ``consumed_by_stages`` remains for legacy consumers.  The V2 records are
    intentionally initialized as ``planned``; a runtime event is the only
    thing allowed to promote an input to ``consumed`` or ``reused``.
    """
    stages = [str(item.get("stage_id") or "") for item in stage_specs if str(item.get("stage_id") or "")]
    definitions = {
        str(item.get("id") or ""): item
        for item in input_definitions or []
        if isinstance(item, dict) and str(item.get("id") or "")
    }
    return {
        "schema_version": "input-consumption-v2",
        "inputs": [
            {
                "input_id": str(input_id),
                "label": _input_label(input_id, definitions.get(str(input_id))),
                "input_type": _input_type(value, definitions.get(str(input_id))),
                "sha256": _input_hash(value),
                "summary": _summary(value),
                "consumed_by_stages": stages,
                "consumption_mode": "frozen_task_bundle",
                "stage_consumption": [
                    {
                        "stage_id": stage_id,
                        "status": "planned",
                        "consumption_mode": "frozen_task_bundle",
                        "reason": "等待阶段接收冻结输入",
                        "artifact": "",
                        "claim_ids": [],
                    }
                    for stage_id in stages
                ],
            }
            for input_id, value in input_snapshot.items()
        ],
    }


def scope_input_consumption_ledger(
    ledger: dict[str, Any], *, input_snapshot: dict[str, Any]
) -> dict[str, Any]:
    """Return only the consumption records an Agent is allowed to receive."""
    allowed_input_ids = {str(input_id) for input_id in input_snapshot}
    return {
        **ledger,
        "inputs": [
            dict(item)
            for item in ledger.get("inputs") or []
            if isinstance(item, dict)
            and str(item.get("input_id") or "") in allowed_input_ids
        ],
    }


def record_input_consumption_event(
    path: str | Path,
    *,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Promote planned input records only when an actual stage reports activity."""
    ledger_path = Path(path)
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(ledger, dict) or ledger.get("schema_version") != "input-consumption-v2":
        return ledger if isinstance(ledger, dict) else {}
    raw_stage_id = str(payload.get("stage_id") or "").strip()
    stage_id = _RUNTIME_STAGE_ALIASES.get(raw_stage_id, raw_stage_id)
    if not stage_id:
        return ledger
    raw_status = str(payload.get("status") or "").lower()
    is_reused = bool(payload.get("reused")) or str(payload.get("event_type") or "") == "stage_reused"
    if is_reused:
        status, mode, reason = "reused", "validated_stage_cache", "已复用通过校验的阶段结果"
    elif raw_status in {"running", "started", "completed", "partial"}:
        status, mode, reason = "consumed", "staged_context", "阶段已接收冻结输入"
    elif raw_status in {"failed", "error", "invalid", "blocked", "cancelled", "canceled"}:
        status, mode, reason = "attempted", "staged_context", "阶段开始后未完成，输入可能已被部分消费"
    else:
        return ledger
    mode = str(payload.get("consumption_mode") or mode)
    reason = str(payload.get("reason") or reason)
    artifact = str(payload.get("artifact") or "")
    changed = False
    for input_record in ledger.get("inputs") or []:
        if not isinstance(input_record, dict):
            continue
        for record in input_record.get("stage_consumption") or []:
            if not isinstance(record, dict) or record.get("stage_id") != stage_id:
                continue
            if record.get("status") == "reused" and status == "consumed":
                continue
            record.update({
                "status": status,
                "consumption_mode": mode,
                "reason": reason,
                "artifact": artifact,
                "claim_ids": [
                    str(value) for value in payload.get("claim_ids") or [] if str(value)
                ],
            })
            changed = True
    if changed:
        ledger_path.write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return ledger


def record_external_agent_input_delivery(
    path: str | Path,
    *,
    status: str,
) -> dict[str, Any]:
    """Record the one fact an external Agent contract can prove directly.

    The harness serializes every frozen input into the Agent invocation.  That
    proves delivery to the Agent context, but not semantic use in every later
    test-design stage.  Keep that distinction explicit in the shared ledger.
    """
    return record_input_consumption_event(
        path,
        payload={
            "stage_id": "input_scope",
            "status": status,
            "consumption_mode": "agent_invocation_context",
            "reason": "冻结输入已序列化并交付给外部 Agent",
            "artifact": "execution_input.json",
        },
    )


def record_external_agent_artifact_consumption(
    path: str | Path,
    *,
    artifacts: list[str],
) -> dict[str, Any]:
    """Attach validated Agent artifacts to their canonical test-activity stage.

    This is intentionally weaker than claiming semantic per-file reasoning:
    it records that all frozen inputs were available to the Agent and that the
    named stage's declared artifact passed physical contract validation.
    """
    updated: dict[str, Any] = {}
    for artifact in artifacts:
        artifact_name = Path(str(artifact)).name
        stage_id = _ARTIFACT_STAGE_ALIASES.get(artifact_name)
        if not stage_id:
            continue
        updated = record_input_consumption_event(
            path,
            payload={
                "stage_id": stage_id,
                "status": "completed",
                "artifact": str(artifact),
                "consumption_mode": "agent_context_with_validated_artifact",
                "reason": "外部 Agent 已接收冻结输入，且该阶段交付件已通过文件契约验证",
            },
        )
    return updated


def _input_hash(value: Any) -> str:
    if isinstance(value, dict) and str(value.get("sha256") or ""):
        return str(value["sha256"])
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _summary(value: Any) -> str:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()[:240]
    if isinstance(value, dict):
        return str(value.get("original_name") or value.get("original_path") or value.get("kind") or "结构化输入")[:240]
    return str(value)[:240]


def _input_label(input_id: Any, definition: dict[str, Any] | None) -> str:
    return str((definition or {}).get("label") or input_id)


def _input_type(value: Any, definition: dict[str, Any] | None) -> str:
    configured = str((definition or {}).get("type") or "").strip()
    if configured:
        return configured
    if isinstance(value, dict):
        return str(value.get("kind") or "structured")
    return "text"
