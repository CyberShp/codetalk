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
