from __future__ import annotations

import threading
import time
from pathlib import Path


def _request(
    prompt: str,
    *,
    required_artifacts: list[str] | None = None,
    run_id: str = "builtin-session-1",
):
    from app.services.harness_facade import HarnessRunRequest

    return HarnessRunRequest(
        provider="builtin",
        command=[],
        cwd="/repo",
        workflow_snapshot={"id": "workflow-v3"},
        task_bundle={
            "rendered_user_input": prompt,
            "required_artifacts": required_artifacts or [],
        },
        requires_network=False,
        run_id=run_id,
    )


def test_builtin_adapter_wraps_callable_and_preserves_verbatim_user_input(tmp_path):
    from app.services.harness_facade import AgentHarnessFacade
    from app.services.provider_adapters.builtin_model import BuiltinModelAdapter

    prompt = " first line\n\nlast line \n"
    captured: dict[str, object] = {}

    def execute_callable(*, request, session, event_sink, **_kwargs):
        captured["request"] = request
        captured["session"] = session
        captured["prompt"] = request.task_bundle["rendered_user_input"]
        artifact_dir = Path(session.artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "report.md").write_text("report", encoding="utf-8")
        event_sink("activity", {"text": "model produced output"})
        return {
            "status": "completed",
            "exit_code": 0,
            "artifacts": ["report.md"],
            "provider_diagnostics": {"model": "injected"},
        }

    adapter = BuiltinModelAdapter(
        tmp_path,
        execute_callable=execute_callable,
    )
    facade = AgentHarnessFacade(tmp_path, adapter=adapter)
    session = facade.prepare(_request(prompt, required_artifacts=["report.md"]))
    events: list[tuple[str, dict]] = []

    result = facade.execute(session, event_sink=lambda kind, data: events.append((kind, data)))

    assert captured["prompt"] == prompt
    assert captured["request"].task_bundle["rendered_user_input"] == prompt
    assert captured["session"] is session
    assert result.status == "completed"
    assert result.artifacts == ["report.md"]
    assert [kind for kind, _payload in events] == [
        "run_started",
        "activity",
        "artifact_created",
        "completed",
    ]


def test_builtin_adapter_uses_injected_client_factory_without_network(tmp_path):
    from app.llm.base import LLMResponse
    from app.services.harness_facade import AgentHarnessFacade
    from app.services.provider_adapters.builtin_model import BuiltinModelAdapter

    prompt = "alpha\n\nbeta"
    calls: dict[str, object] = {}

    class FakeClient:
        async def complete(self, messages, max_tokens=4096, temperature=0.3):
            calls["messages"] = messages
            calls["max_tokens"] = max_tokens
            calls["temperature"] = temperature
            return LLMResponse(
                content="# Generated report\n",
                model="offline-fake",
                usage={"total_tokens": 5},
            )

        async def close(self):
            calls["closed"] = True

    async def client_factory():
        calls["factory"] = True
        return FakeClient()

    adapter = BuiltinModelAdapter(tmp_path, client_factory=client_factory)
    facade = AgentHarnessFacade(tmp_path, adapter=adapter)
    session = facade.prepare(_request(prompt, required_artifacts=["report.md"]))

    result = facade.execute(session)

    assert calls["factory"] is True
    assert calls["messages"] == [{"role": "user", "content": prompt}]
    assert calls["closed"] is True
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "# Generated report\n"
    assert result.artifacts == ["report.md"]
    assert result.provider_diagnostics["model"] == "offline-fake"


def test_builtin_complete_adapter_does_not_advertise_streaming(tmp_path):
    from app.services.provider_adapters.builtin_model import BuiltinModelAdapter

    adapter = BuiltinModelAdapter(tmp_path, execute_callable=lambda **_kwargs: None)

    assert adapter.capabilities().streaming is False


def test_builtin_declared_artifact_is_materialized_only_when_facade_collects_it(
    tmp_path,
):
    from app.services.harness_facade import AgentHarnessFacade
    from app.services.provider_adapters.builtin_model import BuiltinModelAdapter

    def execute_callable(*, session, **_kwargs):
        staging_dir = Path(session.artifact_dir)
        staging_dir.mkdir(parents=True, exist_ok=True)
        (staging_dir / "report.md").write_text("accepted", encoding="utf-8")
        return {"status": "completed", "exit_code": 0, "artifacts": ["report.md"]}

    adapter = BuiltinModelAdapter(tmp_path, execute_callable=execute_callable)
    facade = AgentHarnessFacade(tmp_path, adapter=adapter)
    session = facade.prepare(
        _request("prompt", required_artifacts=["report.md"], run_id="accept-run")
    )

    adapter_result = adapter.execute(session)

    assert adapter_result.status == "completed"
    assert adapter_result.artifacts == ["report.md"]
    assert not (tmp_path / "report.md").exists()
    assert (Path(session.artifact_dir) / "report.md").read_text(encoding="utf-8") == (
        "accepted"
    )

    assert facade.collect_artifacts(session) == ["report.md"]
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "accepted"


def test_builtin_adapter_resume_is_structured_unsupported_and_cancel_is_in_memory(tmp_path):
    from app.services.provider_adapters.builtin_model import BuiltinModelAdapter
    from app.services.provider_adapters.contracts import (
        ProviderResumeToken,
        ProviderUnsupported,
    )

    adapter = BuiltinModelAdapter(tmp_path, execute_callable=lambda **_kwargs: None)
    session = adapter.prepare(_request("prompt"))
    before = set(tmp_path.iterdir())

    resumed = adapter.resume(
        session,
        ProviderResumeToken(provider="builtin", value="not-supported"),
    )
    cancelled = adapter.cancel(session)

    assert adapter.capabilities().session_resume is False
    assert isinstance(resumed, ProviderUnsupported)
    assert resumed.operation == "resume"
    assert resumed.capability == "session_resume"
    assert cancelled.status == "cancelled"
    assert set(tmp_path.iterdir()) == before


def test_builtin_adapter_has_no_runner_or_professional_factory_dependency():
    import inspect

    import app.services.provider_adapters.builtin_model as builtin_model

    source = inspect.getsource(builtin_model)

    assert "workbench_workflow_runner" not in source
    assert "create_quality_" not in source
    assert "create_behavior_claim_audit" not in source


def test_builtin_sync_callable_timeout_returns_promptly_and_discards_late_artifact(
    tmp_path,
):
    from app.services.harness_facade import AgentHarnessFacade
    from app.services.provider_adapters.builtin_model import BuiltinModelAdapter

    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def slow_callable(*, session, **_kwargs):
        entered.set()
        release.wait(timeout=1)
        artifact_dir = Path(session.artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "report.md").write_text("late", encoding="utf-8")
        finished.set()
        return {"status": "completed", "artifacts": ["report.md"]}

    adapter = BuiltinModelAdapter(tmp_path, execute_callable=slow_callable)
    facade = AgentHarnessFacade(tmp_path, adapter=adapter)
    session = facade.prepare(
        _request("prompt", required_artifacts=["report.md"], run_id="timeout-run")
    )

    started = time.monotonic()
    try:
        result = facade.execute(session, timeout_sec=0.03)
        elapsed = time.monotonic() - started

        assert entered.is_set()
        assert elapsed < 0.15
        assert result.status == "error"
        assert result.timed_out is True
        assert result.artifacts == []
        assert result.provider_diagnostics == {
            "timeout_sec": 0.03,
            "background_execution_continues": True,
            "cancellation_scope": "result_commit_only",
        }
    finally:
        release.set()

    assert finished.wait(timeout=0.5)
    time.sleep(0.02)
    assert not (tmp_path / "report.md").exists()
    assert adapter.collect_artifacts(session) == []


def test_builtin_sync_callable_does_not_advertise_process_cancellation(tmp_path):
    from app.services.harness_facade import AgentHarnessFacade
    from app.services.provider_adapters.builtin_model import BuiltinModelAdapter

    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def slow_callable(*, session, **_kwargs):
        entered.set()
        release.wait(timeout=1)
        artifact_dir = Path(session.artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "report.md").write_text("cancelled-late", encoding="utf-8")
        finished.set()
        return {"status": "completed", "artifacts": ["report.md"]}

    adapter = BuiltinModelAdapter(tmp_path, execute_callable=slow_callable)
    facade = AgentHarnessFacade(tmp_path, adapter=adapter)
    session = facade.prepare(
        _request("prompt", required_artifacts=["report.md"], run_id="cancel-run")
    )
    outcome: dict[str, object] = {}

    worker = threading.Thread(
        target=lambda: outcome.setdefault("result", facade.execute(session)),
        daemon=True,
    )
    worker.start()
    assert entered.wait(timeout=0.2)

    try:
        unsupported = facade.cancel(session)
        assert adapter.capabilities().cancellation is False
        assert unsupported.operation == "cancel"
        assert unsupported.capability == "cancellation"
        assert worker.is_alive()
    finally:
        release.set()

    worker.join(timeout=0.5)
    assert not worker.is_alive()
    result = outcome["result"]
    assert result.status == "completed"
    assert finished.wait(timeout=0.5)
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "cancelled-late"
    assert [candidate.path for candidate in adapter.collect_artifacts(session)] == ["report.md"]


def test_builtin_late_epoch_cannot_overwrite_newer_committed_result(tmp_path):
    from app.services.harness_facade import AgentHarnessFacade
    from app.services.provider_adapters.builtin_model import BuiltinModelAdapter

    old_entered = threading.Event()
    release_old = threading.Event()
    old_finished = threading.Event()

    def callable_by_run(*, request, session, **_kwargs):
        artifact_dir = Path(session.artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        if request.run_id == "old-run":
            old_entered.set()
            release_old.wait(timeout=1)
            content = "old-late-result"
            old_finished.set()
        else:
            content = "new-committed-result"
        (artifact_dir / "report.md").write_text(content, encoding="utf-8")
        return {"status": "completed", "artifacts": ["report.md"]}

    adapter = BuiltinModelAdapter(tmp_path, execute_callable=callable_by_run)
    facade = AgentHarnessFacade(tmp_path, adapter=adapter)
    old_session = facade.prepare(
        _request("old", required_artifacts=["report.md"], run_id="old-run")
    )
    old_outcome: dict[str, object] = {}
    old_execute = threading.Thread(
        target=lambda: old_outcome.setdefault(
            "result", facade.execute(old_session, timeout_sec=0.03)
        ),
        daemon=True,
    )
    old_execute.start()
    assert old_entered.wait(timeout=0.2)
    old_execute.join(timeout=0.15)

    try:
        assert not old_execute.is_alive()
        new_session = facade.prepare(
            _request("new", required_artifacts=["report.md"], run_id="new-run")
        )
        new_result = facade.execute(new_session, timeout_sec=0.2)
        assert new_result.status == "completed"
        assert new_result.artifacts == ["report.md"]
        assert (tmp_path / "report.md").read_text(encoding="utf-8") == (
            "new-committed-result"
        )
    finally:
        release_old.set()

    assert old_finished.wait(timeout=0.5)
    time.sleep(0.02)
    assert old_outcome["result"].timed_out is True
    assert old_outcome["result"].artifacts == []
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == (
        "new-committed-result"
    )


def test_builtin_runner_callable_writes_only_to_session_staging_after_timeout(
    tmp_path,
    monkeypatch,
):
    """The production runner closure must never receive the final artifact root."""

    import json

    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner

    artifact_dir = tmp_path / "task" / "agent_runs" / "analyze"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "agent_run.json").write_text(
        json.dumps(
            {
                "run_id": "builtin-timeout",
                "provider": "builtin-llm",
                "cwd": str(tmp_path),
                "prompt_transport": "builtin_llm",
            }
        ),
        encoding="utf-8",
    )
    (artifact_dir / "task_bundle.json").write_text(
        json.dumps(
            {
                "task_run_id": "task",
                "compiled_contract_version": 3,
                "required_artifacts": ["report.md"],
            }
        ),
        encoding="utf-8",
    )
    (artifact_dir / "workflow_snapshot.json").write_text(
        json.dumps({"compiled_contract_version": 3}),
        encoding="utf-8",
    )
    (artifact_dir / "agent_output_contract.json").write_text(
        json.dumps({"required_artifacts": ["report.md"]}),
        encoding="utf-8",
    )

    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    observed: dict[str, Path] = {}

    def late_builtin_execution(*, artifact_dir, **_kwargs):
        observed["artifact_dir"] = Path(artifact_dir)
        entered.set()
        release.wait(timeout=1)
        Path(artifact_dir).mkdir(parents=True, exist_ok=True)
        (Path(artifact_dir) / "report.md").write_text("late", encoding="utf-8")
        (Path(artifact_dir) / "execution_result.json").write_text(
            '{"status":"completed-late"}',
            encoding="utf-8",
        )
        (Path(artifact_dir) / "agent_run.json").write_text(
            '{"status":"completed-late"}',
            encoding="utf-8",
        )
        finished.set()
        return {
            "status": "completed",
            "execution": {"status": "completed", "exit_code": 0},
            "artifacts": ["report.md"],
        }

    runner = WorkbenchWorkflowRunner(tmp_path)
    monkeypatch.setattr(runner, "_execute_builtin_llm_step", late_builtin_execution)
    facade, session_id, missing = runner._prepare_provider_facade_for_step(
        step={
            "id": "analyze",
            "type": "agent_task",
            "required_artifacts": ["report.md"],
        },
        agent_run={
            "step_id": "analyze",
            "run_id": "builtin-timeout",
            "provider": "builtin-llm",
            "artifact_dir": str(artifact_dir),
            "required_artifacts": ["report.md"],
        },
        artifact_dir=artifact_dir,
        run_payload=json.loads(
            (artifact_dir / "agent_run.json").read_text(encoding="utf-8")
        ),
        run_id="builtin-timeout",
        timeout_sec=1,
        idle_timeout_sec=None,
    )

    try:
        result = facade.execute(session_id, timeout_sec=0.03)
        assert entered.is_set()
        assert result.status == "error"
        assert result.timed_out is True
        assert missing == []
        assert observed["artifact_dir"] != artifact_dir
        observed["artifact_dir"].relative_to(artifact_dir / ".builtin-model-staging")
    finally:
        release.set()

    assert finished.wait(timeout=0.5)
    time.sleep(0.02)
    assert not (artifact_dir / "report.md").exists()
    assert not (artifact_dir / "execution_result.json").exists()
    assert "completed-late" not in (artifact_dir / "agent_run.json").read_text(
        encoding="utf-8"
    )


def test_builtin_complete_capabilities_do_not_claim_streaming():
    from app.services.provider_adapters.builtin_model import BUILTIN_MODEL_CAPABILITIES
    from app.services.provider_adapters.registry import provider_capability_names

    assert BUILTIN_MODEL_CAPABILITIES.streaming is False
    assert "streaming" not in provider_capability_names(provider="builtin-llm")


def test_builtin_facade_materializes_only_declared_staged_candidates(tmp_path):
    from app.services.harness_facade import AgentHarnessFacade
    from app.services.provider_adapters.builtin_model import BuiltinModelAdapter

    def execute_callable(*, session, **_kwargs):
        staging = Path(session.artifact_dir)
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "report.md").write_text("declared", encoding="utf-8")
        (staging / "undeclared.md").write_text("must stay hidden", encoding="utf-8")
        return {
            "status": "completed",
            "exit_code": 0,
            "artifacts": ["report.md", "undeclared.md"],
        }

    adapter = BuiltinModelAdapter(tmp_path, execute_callable=execute_callable)
    facade = AgentHarnessFacade(tmp_path, adapter=adapter)
    session = facade.prepare(
        _request("prompt", required_artifacts=["report.md"], run_id="declared-only")
    )

    result = facade.execute(session)

    assert result.status == "completed"
    assert result.artifacts == ["report.md"]
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "declared"
    assert not (tmp_path / "undeclared.md").exists()
    assert not Path(session.artifact_dir).exists()
