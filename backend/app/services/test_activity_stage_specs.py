"""Immutable test-activity stage contracts shared by rapid and deep runs."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


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

_RUNTIME_STAGE_MAP = {
    "source_analysis": "source_evidence",
    "breadth_inventory": "breadth_inventory",
    "flow_evidence_pack": "flow_modeling",
    "flow_outline": "flow_modeling",
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


def validate_test_activity_stage_contract(
    *, artifact_dir: str | Path, profile_id: str
) -> dict[str, object]:
    """Fail closed when a required test-activity stage lacks its declared files.

    Progress is deliberately projected from disk instead of trusting a runner
    event.  A stage may only pass when every artifact promised by its immutable
    contract was actually materialized for this task.
    """
    progress = project_test_activity_stage_progress(
        artifact_dir=artifact_dir,
        profile_id=profile_id,
    )
    required = {
        str(spec["stage_id"])
        for spec in default_test_activity_stage_specs(profile_id=profile_id)
        if bool(spec.get("required"))
    }
    incomplete = [
        {
            "stage_id": str(stage.get("stage_id") or ""),
            "name": str(stage.get("name") or ""),
            "status": str(stage.get("status") or "not_requested"),
            "missing_artifacts": [
                name
                for name in stage.get("expected_artifacts") or []
                if name not in (stage.get("present_artifacts") or [])
            ],
        }
        for stage in progress.get("stages") or []
        if isinstance(stage, dict)
        and str(stage.get("stage_id") or "") in required
        and stage.get("status") != "completed"
    ]
    return {
        "kind": "test_activity_stage_contract_validation",
        "schema_version": "test-activity-stage-contract-v1",
        "profile_id": profile_id,
        "status": "passed" if not incomplete else "blocked",
        "required_stage_ids": sorted(required),
        "incomplete_stages": incomplete,
        "progress": progress,
    }


class TestActivityStageProgressTracker:
    """Materialize only the stage state the running task can honestly prove."""

    def __init__(self, artifact_dir: str | Path, *, profile_id: str) -> None:
        self.root = Path(artifact_dir)
        self.profile_id = profile_id
        self.path = self.root / "test_activity_stage_progress.json"
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write(self._initial_progress())

    def update(self, event: dict[str, Any]) -> dict[str, object]:
        payload = self.read()
        runtime_stage_id = str(event.get("stage_id") or "").strip()
        stage_id = _RUNTIME_STAGE_MAP.get(runtime_stage_id)
        if not stage_id:
            return payload
        raw_status = str(event.get("status") or "").strip().lower()
        stages = payload.get("stages") if isinstance(payload.get("stages"), list) else []
        stage = next(
            (
                item
                for item in stages
                if isinstance(item, dict) and item.get("stage_id") == stage_id
            ),
            None,
        )
        if stage is None:
            return payload
        present = self._present_artifacts(stage)
        expected = list(stage.get("expected_artifacts") or [])
        stage["present_artifacts"] = present
        if raw_status in {"failed", "error", "invalid", "blocked"}:
            stage["status"] = "failed"
        elif raw_status in {"cancelled", "canceled"}:
            stage["status"] = "cancelled"
        elif len(present) == len(expected):
            stage["status"] = "completed"
        elif present:
            stage["status"] = "partial"
        elif raw_status in {"running", "started", "queued"}:
            stage["status"] = "running"
        elif raw_status in {"completed", "partial"}:
            # A provider event is not proof that the declared artifact exists.
            stage["status"] = "awaiting_artifacts"
        self._write(payload)
        return payload

    def read(self) -> dict[str, object]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = self._initial_progress()
        return payload if isinstance(payload, dict) else self._initial_progress()

    def _initial_progress(self) -> dict[str, object]:
        projected = project_test_activity_stage_progress(
            artifact_dir=self.root,
            profile_id=self.profile_id,
        )
        for stage in projected["stages"]:
            if isinstance(stage, dict) and stage.get("status") == "not_requested":
                stage["status"] = "pending"
        projected["live"] = True
        return projected

    def _present_artifacts(self, stage: dict[str, Any]) -> list[str]:
        expected = [str(item) for item in stage.get("expected_artifacts") or []]
        available = {
            path.name
            for path in self.root.rglob("*")
            if path.is_file()
        }
        return [name for name in expected if name in available]

    def _write(self, payload: dict[str, object]) -> None:
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self.path)
