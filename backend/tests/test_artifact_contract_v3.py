def test_deep_artifact_contract_declares_deliverables_supporting_and_diagnostics():
    from app.services.artifact_contract_v3 import default_artifact_contract_v3

    contract = default_artifact_contract_v3(profile_id="deep")
    layers = {item["layer"] for item in contract["artifacts"]}
    required = {item["artifact"] for item in contract["artifacts"] if item["required"]}

    assert layers == {"deliverable", "supporting", "diagnostic"}
    assert {"完整分析报告.md", "风险点与SFMEA.md", "黑盒测试设计.md"} <= required


def test_rapid_contract_is_bounded_without_claiming_deep_deliverables():
    from app.services.artifact_contract_v3 import default_artifact_contract_v3

    contract = default_artifact_contract_v3(profile_id="rapid")
    report = next(item for item in contract["artifacts"] if item["artifact"] == "快速分析报告.md")
    deep_report = next(item for item in contract["artifacts"] if item["artifact"] == "完整分析报告.md")

    assert report["required"] is True
    assert deep_report["required"] is False
    assert contract["delivery_class"] == "bounded_analysis"
