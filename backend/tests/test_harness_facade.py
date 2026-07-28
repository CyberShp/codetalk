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


def test_harness_event_normalizer_preserves_required_lifecycle_vocabulary():
    from app.services.harness_facade import normalize_provider_event

    required = {
        "run_started": "run_started",
        "session_created": "session_created",
        "stage_started": "stage_started",
        "thinking_summary": "thinking_summary",
        "tool_started": "tool_started",
        "tool_completed": "tool_completed",
        "source_read": "source_read",
        "artifact_progress": "artifact_progress",
        "validation_started": "validation_started",
        "validation_failed": "validation_failed",
        "repair_started": "repair_started",
        "stage_completed": "stage_completed",
        "idle": "idle",
        "blocked": "blocked",
        "cancelled": "cancelled",
    }

    for raw_event, expected_kind in required.items():
        event = normalize_provider_event(raw_event, {"stage_id": "source_evidence"})
        assert event.kind == expected_kind
        assert event.visibility != "diagnostic"


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


def test_agent_harness_facade_freezes_explicit_offline_requirement(tmp_path):
    import sys

    from app.services.harness_facade import AgentHarnessFacade, HarnessRunRequest

    session = AgentHarnessFacade(tmp_path / "artifacts").prepare(HarnessRunRequest(
        provider="offline-test-agent",
        command=[sys.executable, "-c", "print('ready')"],
        cwd=str(tmp_path),
        workflow_snapshot={"id": "workflow"},
        task_bundle={"required_artifacts": []},
        requires_network=False,
    ))

    assert session.requires_network is False


def test_agent_harness_facade_keeps_product_contract_when_adapter_is_replaced(tmp_path):
    """An SDK adapter must not own CodeTalk's public result or artifact semantics."""
    from types import SimpleNamespace

    from app.services.harness_facade import AgentHarnessFacade, HarnessRunRequest

    class IsolatedAdapter:
        def __init__(self):
            self.prepared = None
            self.executed = None

        def prepare(self, request):
            self.prepared = request
            return SimpleNamespace(run_id="sdk_session", provider=request.provider)

        def execute(self, session_id, **kwargs):
            self.executed = (session_id, kwargs)
            return SimpleNamespace(
                run_id=session_id,
                status="completed",
                exit_code=0,
                started_at="2026-07-27T00:00:00Z",
                completed_at="2026-07-27T00:00:01Z",
                duration_ms=1000,
                timed_out=False,
                error="",
                provider_diagnostics={"adapter": "isolated"},
            )

        def record_raw_output(self, session_id, *, stdout, stderr=""):
            raise AssertionError("not used by this test")

        def collect_artifacts(self, session_id):
            assert session_id == "sdk_session"
            return ["report.md", "invented.md", "../outside.md"]

    adapter = IsolatedAdapter()
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (artifact_dir / "report.md").write_text("verified delivery", encoding="utf-8")
    facade = AgentHarnessFacade(artifact_dir, adapter=adapter)
    events = []
    request = HarnessRunRequest(
        provider="future-sdk",
        command=["unused"],
        cwd=str(tmp_path),
        workflow_snapshot={"id": "workflow"},
        task_bundle={"required_artifacts": ["report.md"]},
    )

    session = facade.prepare(request)
    result = facade.execute(
        session.run_id,
        timeout_sec=12,
        event_sink=lambda kind, payload: events.append((kind, payload)),
    )

    assert adapter.prepared is request
    assert adapter.executed[0] == "sdk_session"
    assert result.session_id == "sdk_session"
    assert result.artifacts == ["report.md"]
    assert result.provider_diagnostics == {"adapter": "isolated"}
    assert any(payload["harness_event_kind"] == "run_started" for _, payload in events)
    assert any(payload["harness_event_kind"] == "completed" for _, payload in events)


def test_phase0_harness_rejects_path_escape_and_undeclared_artifacts(tmp_path):
    """Freeze the artifact boundary before harness internals are refactored."""
    from types import SimpleNamespace

    from app.services.harness_facade import AgentHarnessFacade, HarnessRunRequest

    outside = tmp_path.parent / "phase0-outside.md"
    outside.write_text("outside", encoding="utf-8")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (artifact_dir / "report.md").write_text("declared", encoding="utf-8")
    (artifact_dir / "undeclared.md").write_text("not declared", encoding="utf-8")

    class CandidateAdapter:
        def prepare(self, _request):
            return SimpleNamespace(run_id="phase0-session")

        def execute(self, *_args, **_kwargs):
            raise AssertionError("artifact boundary must be checked before execution")

        def record_raw_output(self, *_args, **_kwargs):
            raise AssertionError("not used")

        def collect_artifacts(self, _session_id):
            return ["report.md", "undeclared.md", "../phase0-outside.md", str(outside)]

    facade = AgentHarnessFacade(artifact_dir, adapter=CandidateAdapter())
    session = facade.prepare(HarnessRunRequest(
        provider="fixture-agent",
        command=["fixture-agent"],
        cwd=str(tmp_path),
        workflow_snapshot={"id": "phase0"},
        task_bundle={"required_artifacts": ["report.md"]},
    ))

    assert facade.collect_artifacts(session.run_id) == ["report.md"]


def test_facade_execute_returns_structured_unsupported_without_lifecycle_events(tmp_path):
    """An Adapter may decline a run before a Provider execution exists."""
    from app.services.harness_facade import AgentHarnessFacade, HarnessRunRequest
    from app.services.provider_adapters.contracts import (
        ProviderCapabilities,
        ProviderSession,
        ProviderUnsupported,
    )

    class UnsupportedRunAdapter:
        def capabilities(self):
            return ProviderCapabilities(
                streaming=False,
                tool_call=False,
                session_resume=False,
                structured_output=False,
                mcp=False,
                skills=False,
                cancellation=False,
            )

        def prepare(self, request):
            return ProviderSession(session_id="unsupported-run", provider=request.provider)

        def execute(self, _session, **_kwargs):
            return ProviderUnsupported(
                operation="run",
                capability="provider_execution",
                message="执行器当前不可运行",
            )

        def collect_artifacts(self, _session):
            raise AssertionError("unsupported runs must not enter artifact collection")

    facade = AgentHarnessFacade(tmp_path, adapter=UnsupportedRunAdapter())
    session = facade.prepare(
        HarnessRunRequest(
            provider="unavailable-provider",
            command=[],
            cwd=str(tmp_path),
            workflow_snapshot={"id": "workflow-v3"},
            task_bundle={"required_artifacts": []},
        )
    )
    events: list[tuple[str, dict]] = []

    result = facade.execute(
        session,
        event_sink=lambda kind, payload: events.append((kind, payload)),
    )

    assert isinstance(result, ProviderUnsupported)
    assert result.operation == "run"
    assert result.code == "unsupported_capability"
    assert events == []
