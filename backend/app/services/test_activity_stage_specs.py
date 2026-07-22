"""Immutable test-activity stage contracts shared by rapid and deep runs."""

from __future__ import annotations

import copy
from pathlib import Path


_STAGES = (
    ("input_scope", "输入解析与范围冻结", ["input_snapshot.json", "input_consumption.json"], "input_bindings_verified", "use_frozen_inputs"),
    ("source_evidence", "确定性源码证据整理", ["source_analysis.md", "source_scope.json", "evidence_cards.json"], "source_evidence_verified", "deterministic_evidence_pack"),
    ("breadth_inventory", "入口与模块广度盘点", ["entrypoints.json", "flows.json"], "entry_inventory_complete", "mark_evidence_gap"),
    ("flow_modeling", "流程、状态、资源与异常传播建模", ["flow_outline.json", "flow_cards.json"], "flow_claims_linked", "mark_evidence_gap"),
    ("scenario_expansion", "测试场景扩展", ["scenario_candidates.json"], "scenario_coverage_checked", "bounded_scenarios"),
    ("sfmea", "SFMEA 与风险映射", ["sfmea.json", "风险点与SFMEA.md"], "sfmea_schema_verified", "block_delivery"),
    ("black_box_design", "黑盒测试设计", ["black_box_cases.json", "黑盒测试设计.md"], "black_box_boundary_verified", "block_delivery"),
    ("independent_judge", "独立质量审查与定向修复", ["judge_report.json", "claim_evidence_ledger.json"], "claim_quality_verified", "quality_blocked"),
    ("publish", "交付件发布", ["task_artifact_manifest.json"], "artifact_contract_verified", "block_delivery"),
)


def default_test_activity_stage_specs(*, profile_id: str) -> list[dict[str, object]]:
    if profile_id not in {"rapid", "deep"}:
        raise ValueError(f"未知执行档位：{profile_id}")
    result = []
    for stage_id, name, outputs, gate, fallback in _STAGES:
        required = not (profile_id == "rapid" and stage_id == "independent_judge")
        budget = {"max_key_flows": 3 if profile_id == "rapid" else 12, "max_subagents": 1 if profile_id == "rapid" else 4}
        result.append({
            "stage_id": stage_id, "name": name, "purpose": name,
            "input_ports": ["frozen_inputs", "source_evidence"],
            "required_evidence": ["source_evidence_pack"],
            "executor_policy": "deterministic_first", "skill": stage_id,
            "mcp_policy": "preferred", "output_artifacts": outputs,
            "deterministic_gate": gate, "model_gate": "claim_evidence_consistent",
            "budget": budget, "retry_policy": "failed_items_only", "fallback": fallback,
            "required": required,
        })
    return copy.deepcopy(result)


def project_test_activity_stage_progress(
    *, artifact_dir: str | Path, profile_id: str
) -> dict[str, object]:
    """Project frozen stage contracts from artifacts actually written by a run."""
    root = Path(artifact_dir)
    available = {
        path.name
        for path in root.rglob("*")
        if path.is_file()
    } if root.is_dir() else set()
    stages: list[dict[str, object]] = []
    for spec in default_test_activity_stage_specs(profile_id=profile_id):
        expected = [str(item) for item in spec["output_artifacts"]]
        present = [name for name in expected if name in available]
        if len(present) == len(expected):
            status = "completed"
        elif present:
            status = "partial"
        else:
            status = "not_requested"
        stages.append({
            "stage_id": str(spec["stage_id"]),
            "name": str(spec["name"]),
            "status": status,
            "expected_artifacts": expected,
            "present_artifacts": present,
            "deterministic_gate": str(spec["deterministic_gate"]),
            "fallback": str(spec["fallback"]),
        })
    return {
        "kind": "test_activity_stage_progress",
        "schema_version": "test-activity-stage-progress-v1",
        "profile_id": profile_id,
        "stages": stages,
    }
