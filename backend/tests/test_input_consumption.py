import json


def test_input_consumption_ledger_preserves_named_input_hash_and_stage_usage():
    from app.services.input_consumption import build_input_consumption_ledger

    ledger = build_input_consumption_ledger(
        input_snapshot={"analysis_target": "iSCSI login\ninclude recovery", "design_doc": {"kind": "file", "sha256": "abc"}},
        stage_specs=[{"stage_id": "input_scope"}, {"stage_id": "flow_modeling"}],
    )

    target = next(item for item in ledger["inputs"] if item["input_id"] == "analysis_target")
    assert target["sha256"]
    assert target["consumed_by_stages"] == ["input_scope", "flow_modeling"]
    assert target["summary"] == "iSCSI login include recovery"


def test_input_consumption_ledger_records_named_stage_consumption_from_runtime_event(tmp_path):
    from app.services.input_consumption import (
        build_input_consumption_ledger,
        record_input_consumption_event,
    )

    ledger = build_input_consumption_ledger(
        input_snapshot={
            "analysis_target": "iSCSI login\ninclude recovery",
            "design_doc": {"kind": "file", "sha256": "abc", "original_name": "login-design.md"},
        },
        input_definitions=[
            {"id": "analysis_target", "label": "分析目标", "type": "long_text"},
            {"id": "design_doc", "label": "开发设计文档", "type": "file"},
        ],
        stage_specs=[{"stage_id": "source_evidence"}],
    )
    path = tmp_path / "input_consumption.json"
    path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")

    updated = record_input_consumption_event(
        path,
        payload={
            "stage_id": "source_analysis",
            "status": "running",
            "artifact": "source_analysis.md",
        },
    )

    assert updated["schema_version"] == "input-consumption-v2"
    document = next(item for item in updated["inputs"] if item["input_id"] == "design_doc")
    assert document["label"] == "开发设计文档"
    assert document["input_type"] == "file"
    assert document["stage_consumption"] == [{
        "stage_id": "source_evidence",
        "status": "consumed",
        "consumption_mode": "staged_context",
        "reason": "阶段已接收冻结输入",
        "artifact": "source_analysis.md",
        "claim_ids": [],
    }]


def test_external_agent_delivery_and_validated_artifact_use_shared_ledger(tmp_path):
    from app.services.input_consumption import (
        build_input_consumption_ledger,
        record_external_agent_artifact_consumption,
        record_external_agent_input_delivery,
    )

    ledger = build_input_consumption_ledger(
        input_snapshot={"repo_path": {"kind": "directory", "sha256": "repo"}},
        stage_specs=[
            {"stage_id": "input_scope"},
            {"stage_id": "source_evidence"},
            {"stage_id": "sfmea"},
        ],
        input_definitions=[{"id": "repo_path", "label": "源码工作空间", "type": "directory"}],
    )
    path = tmp_path / "input_consumption.json"
    path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")

    record_external_agent_input_delivery(path, status="running")
    updated = record_external_agent_artifact_consumption(
        path,
        artifacts=["source_scope.json", "sfmea.json"],
    )

    stages = updated["inputs"][0]["stage_consumption"]
    assert stages == [
        {
            "stage_id": "input_scope",
            "status": "consumed",
            "consumption_mode": "agent_invocation_context",
            "reason": "冻结输入已序列化并交付给外部 Agent",
            "artifact": "execution_input.json",
            "claim_ids": [],
        },
        {
            "stage_id": "source_evidence",
            "status": "consumed",
            "consumption_mode": "agent_context_with_validated_artifact",
            "reason": "外部 Agent 已接收冻结输入，且该阶段交付件已通过文件契约验证",
            "artifact": "source_scope.json",
            "claim_ids": [],
        },
        {
            "stage_id": "sfmea",
            "status": "consumed",
            "consumption_mode": "agent_context_with_validated_artifact",
            "reason": "外部 Agent 已接收冻结输入，且该阶段交付件已通过文件契约验证",
            "artifact": "sfmea.json",
            "claim_ids": [],
        },
    ]
