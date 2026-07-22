def test_harness_event_normalizer_maps_provider_noise_to_product_events():
    from app.services.harness_facade import normalize_provider_event

    activity = normalize_provider_event("stdout", {"text": "rg lib/iscsi"})
    artifact = normalize_provider_event("artifact", {"path": "sfmea.json"})
    blocked = normalize_provider_event("network_egress_blocked", {"reason": "public_address"})

    assert activity.kind == "activity"
    assert activity.visibility == "summary"
    assert artifact.kind == "artifact_created"
    assert artifact.visibility == "user"
    assert blocked.kind == "network_egress_blocked"
    assert blocked.visibility == "user"


def test_harness_event_normalizer_hides_raw_provider_diagnostics_from_user_output():
    from app.services.harness_facade import normalize_provider_event

    event = normalize_provider_event("stderr", {"text": "ANSI startup banner"})

    assert event.kind == "diagnostic"
    assert event.visibility == "diagnostic"
    assert event.user_message == ""


def test_agent_harness_emits_facade_fields_without_breaking_legacy_event_type():
    from app.services.agent_run_harness import _emit_agent_run_event

    received = []
    _emit_agent_run_event(
        lambda event_type, payload: received.append((event_type, payload)),
        "agent_output",
        {"content": "reading source"},
    )

    event_type, payload = received[0]
    assert event_type == "agent_output"
    assert payload["harness_event_kind"] == "activity"
    assert payload["harness_visibility"] == "summary"
