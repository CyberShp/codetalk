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
    assert blocked.user_message == "受控出站策略已阻止未批准连接"
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


def test_agent_harness_facade_runs_local_adapter_and_collects_normalized_result(tmp_path, monkeypatch):
    """The workflow-facing facade, not the CLI adapter, owns the stable result shape."""
    import sys

    from app.config import settings
    from app.services.harness_facade import AgentHarnessFacade, HarnessRunRequest

    # This is a local deterministic adapter contract test, not a deployment
    # Agent execution. Production intranet Agent launches remain fail-closed.
    monkeypatch.setattr(settings, "intranet_network_mode", False)

    workspace = tmp_path / "repo"
    workspace.mkdir()
    artifact_dir = tmp_path / "artifacts"
    facade = AgentHarnessFacade(artifact_dir)
    events = []
    request = HarnessRunRequest(
        provider="local-test-agent",
        command=[
            sys.executable,
            "-c",
            "from pathlib import Path; Path('report.md').write_text('ready', encoding='utf-8'); print('completed')",
        ],
        cwd=str(artifact_dir),
        workflow_snapshot={"id": "workflow"},
        task_bundle={"required_artifacts": ["report.md"]},
    )

    session = facade.prepare(request)
    result = facade.execute(session.run_id, timeout_sec=10, event_sink=lambda kind, payload: events.append((kind, payload)))

    assert result.status == "completed"
    assert result.session_id == session.run_id
    assert result.artifacts == ["report.md"]
    assert any(payload["harness_event_kind"] == "completed" for _, payload in events)
