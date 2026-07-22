def test_default_test_activity_stage_specs_are_ordered_and_have_contracts():
    from app.services.test_activity_stage_specs import default_test_activity_stage_specs

    stages = default_test_activity_stage_specs(profile_id="deep")

    assert [stage["stage_id"] for stage in stages] == [
        "input_scope", "source_evidence", "breadth_inventory", "flow_modeling",
        "scenario_expansion", "sfmea", "black_box_design", "independent_judge", "publish",
    ]
    assert all(stage["purpose"] and stage["output_artifacts"] for stage in stages)
    assert stages[1]["deterministic_gate"] == "source_evidence_verified"
    assert stages[-1]["fallback"] == "block_delivery"


def test_rapid_profile_reduces_scope_but_keeps_evidence_and_black_box_hard_gates():
    from app.services.test_activity_stage_specs import default_test_activity_stage_specs

    stages = {item["stage_id"]: item for item in default_test_activity_stage_specs(profile_id="rapid")}

    assert stages["source_evidence"]["required"] is True
    assert stages["black_box_design"]["deterministic_gate"] == "black_box_boundary_verified"
    assert stages["independent_judge"]["required"] is False
    assert stages["scenario_expansion"]["budget"]["max_key_flows"] == 3
