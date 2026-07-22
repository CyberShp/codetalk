"""Profile-aware, layered artifact declarations for test workflow runs."""

from __future__ import annotations


def default_artifact_contract_v3(*, profile_id: str) -> dict[str, object]:
    if profile_id not in {"rapid", "deep"}:
        raise ValueError(f"未知执行档位：{profile_id}")
    deep = profile_id == "deep"
    artifacts = [
        _item("快速分析报告.md", "deliverable", not deep, "markdown"),
        _item("覆盖缺口与建议.md", "deliverable", not deep, "markdown"),
        _item("完整分析报告.md", "deliverable", deep, "markdown"),
        _item("开发给测试讲代码.md", "deliverable", deep, "markdown"),
        _item("流程状态资源与异常传播.md", "deliverable", deep, "markdown"),
        _item("风险点与SFMEA.md", "deliverable", deep, "markdown"),
        _item("sfmea.json", "deliverable", True, "json"),
        _item("黑盒测试设计.md", "deliverable", deep, "markdown"),
        _item("black_box_cases.json", "deliverable", True, "json"),
        _item("source_analysis.md", "supporting", True, "markdown"),
        _item("source_scope.json", "supporting", True, "json"),
        _item("evidence_cards.json", "supporting", True, "json"),
        _item("claim_evidence_ledger.json", "supporting", True, "json"),
        _item("input_consumption.json", "supporting", True, "json"),
        _item("task_artifact_manifest.json", "supporting", True, "json"),
        _item("provider_diagnostics.json", "diagnostic", False, "json"),
        _item("runtime_events.jsonl", "diagnostic", False, "jsonl"),
    ]
    return {
        "schema_version": "artifact-contract-v3",
        "profile_id": profile_id,
        "delivery_class": "full_test_delivery" if deep else "bounded_analysis",
        "artifacts": artifacts,
    }


def _item(artifact: str, layer: str, required: bool, format_name: str) -> dict[str, object]:
    return {"artifact": artifact, "layer": layer, "required": required, "format": format_name, "downloadable": layer != "diagnostic"}
