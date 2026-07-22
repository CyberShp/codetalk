"""Immutable test-activity stage contracts shared by rapid and deep runs."""

from __future__ import annotations

import copy


_STAGES = (
    ("input_scope", "输入解析与范围冻结", ["execution_input.json"], "input_bindings_verified", "use_frozen_inputs"),
    ("source_evidence", "确定性源码证据整理", ["source_scope.json", "evidence_cards.json"], "source_evidence_verified", "deterministic_evidence_pack"),
    ("breadth_inventory", "入口与模块广度盘点", ["entry_inventory.json"], "entry_inventory_complete", "mark_evidence_gap"),
    ("flow_modeling", "流程、状态、资源与异常传播建模", ["flow_model.json"], "flow_claims_linked", "mark_evidence_gap"),
    ("scenario_expansion", "测试场景扩展", ["scenario_matrix.json"], "scenario_coverage_checked", "bounded_scenarios"),
    ("sfmea", "SFMEA 与风险映射", ["sfmea.json", "风险点与SFMEA.md"], "sfmea_schema_verified", "block_delivery"),
    ("black_box_design", "黑盒测试设计", ["black_box_cases.json", "黑盒测试设计.md"], "black_box_boundary_verified", "block_delivery"),
    ("independent_judge", "独立质量审查与定向修复", ["judge_report.json"], "claim_quality_verified", "quality_blocked"),
    ("publish", "交付件发布", ["完整分析报告.md"], "artifact_contract_verified", "block_delivery"),
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
