from __future__ import annotations

import asyncio
import inspect
import os
import threading
from dataclasses import replace
from pathlib import Path

import pytest


def _adapter_types():
    try:
        from app.services.provider_adapters.claude_code import ClaudeCliAdapter
        from app.services.provider_adapters.codex_cli import CodexCliAdapter
        from app.services.provider_adapters.opencode import OpenCodeAdapter
    except ModuleNotFoundError as exc:
        pytest.fail(f"explicit CLI provider adapters are missing: {exc}")
    return CodexCliAdapter, ClaudeCliAdapter, OpenCodeAdapter


def _request(provider: str, prompt: str, *, command: list[str] | None = None):
    from app.services.harness_facade import HarnessRunRequest

    return HarnessRunRequest(
        provider=provider,
        command=command or [provider, "--configured-flag"],
        cwd="/repo with spaces",
        workflow_snapshot={"id": "workflow-v3"},
        task_bundle={
            "rendered_user_input": prompt,
            "required_artifacts": ["report.md"],
        },
        mcp_profile="codehub-readonly",
        timeout_seconds=91,
        idle_timeout_seconds=17,
        requires_network=False,
        run_id=f"run-{provider}",
    )


def test_explicit_cli_adapters_declare_provider_capability_differences(tmp_path):
    CodexCliAdapter, ClaudeCliAdapter, OpenCodeAdapter = _adapter_types()

    capabilities = {
        "codex": CodexCliAdapter(tmp_path).capabilities(),
        "claude": ClaudeCliAdapter(tmp_path).capabilities(),
        "opencode": OpenCodeAdapter(tmp_path).capabilities(),
    }

    assert all(value.streaming for value in capabilities.values())
    assert all(value.tool_call for value in capabilities.values())
    assert all(value.session_resume for value in capabilities.values())
    assert all(value.mcp for value in capabilities.values())
    assert all(value.cancellation for value in capabilities.values())
    assert capabilities["codex"].structured_output is True
    assert capabilities["claude"].structured_output is True
    assert capabilities["opencode"].structured_output is False
    assert capabilities["codex"].skills is True
    assert capabilities["claude"].skills is True
    assert capabilities["opencode"].skills is False


@pytest.mark.parametrize(
    ("adapter_index", "provider", "transport", "output_mode"),
    [
        (0, "codex", "codex_exec_json", "stream_json"),
        (1, "claude", "claude_print_arg", "stream_json"),
        (2, "opencode", "opencode_run_arg", "auto"),
    ],
)
def test_cli_adapters_reuse_bridge_and_preserve_prompt_verbatim(
    monkeypatch,
    tmp_path,
    adapter_index,
    provider,
    transport,
    output_mode,
):
    from app.services import agent_cli_bridge

    adapter_type = _adapter_types()[adapter_index]
    captured: dict[str, object] = {}

    async def fake_stream_agent_runtime(**kwargs):
        captured.update(kwargs)
        kwargs["session_update"](
            {
                "session_id": f"{provider}-opaque-session",
                "resume_session_id": f"{provider}:resume/opaque-01",
                "event_type": "session_init",
            }
        )
        yield "first activity"
        yield "\nsecond activity"

    monkeypatch.setattr(
        agent_cli_bridge,
        "stream_agent_runtime",
        fake_stream_agent_runtime,
    )
    prompt = "  first line\n\nsecond line\n\n\nlast line  \n"
    events: list[tuple[str, dict]] = []
    adapter = adapter_type(tmp_path)
    session = adapter.prepare(_request(provider, prompt))

    result = adapter.execute(
        session,
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )

    assert not inspect.isawaitable(result)
    runtime = captured["runtime"]
    assert captured["prompt"] == prompt
    assert captured["cwd"] == "/repo with spaces"
    assert captured["resume_session_id"] is None
    assert runtime["command"] == provider
    assert runtime["args"] == ["--configured-flag"]
    assert runtime["prompt_transport"] == transport
    assert runtime["output_mode"] == output_mode
    assert runtime["activity_timeout_seconds"] == 17
    assert runtime["total_timeout_seconds"] == 91
    assert runtime["env"]["CODETALK_AGENT_ARTIFACT_DIR"] == str(tmp_path.resolve())
    assert result.status == "completed"
    assert result.artifacts == []
    assert result.provider_diagnostics["output"] == "first activity\nsecond activity"
    assert result.provider_diagnostics["resume_token"] == {
        "provider": provider,
        "value": f"{provider}:resume/opaque-01",
    }
    assert session.metadata["resume_token"].value == f"{provider}:resume/opaque-01"
    assert [event_type for event_type, _ in events] == [
        "session_created",
        "activity",
        "activity",
    ]


def test_cli_adapter_executes_with_frozen_config_and_live_runtime_secrets(
    monkeypatch, tmp_path
):
    from app.services import agent_runtimes

    _, _, OpenCodeAdapter = _adapter_types()
    provider_ref = "agent-runtime:phase7-opencode"
    monkeypatch.setattr(
        agent_runtimes,
        "get_agent_runtime_sync",
        lambda runtime_id: {
            "id": runtime_id,
            "enabled": True,
            "env": {
                "OPENCODE_CONFIG_CONTENT": '{"enabled_providers":["changed-after-freeze"]}',
                "OPENCODE_DISABLE_AUTOUPDATE": "0",
                "OPENAI_API_KEY": "live-runtime-secret",
            },
        },
    )
    request = _request(
        provider_ref,
        "run with the prepared provider snapshot",
        command=["/opt/homebrew/bin/opencode", "--pure"],
    )
    request = replace(
        request,
        task_bundle={
            **request.task_bundle,
            "provider_snapshot": {
                "providers": {
                    provider_ref: {
                        "env_hints": {
                            "OPENCODE_CONFIG_CONTENT": '{"enabled_providers":["codetalk-local"]}',
                            "OPENCODE_DISABLE_AUTOUPDATE": "1",
                            "OPENAI_API_KEY": "<redacted>",
                            "CODETALK_AGENT_ARTIFACT_DIR": "/untrusted/provider/path",
                        },
                    },
                },
            },
        },
    )

    session = OpenCodeAdapter(tmp_path).prepare(request)
    runtime_env = session.metadata["runtime"]["env"]

    assert runtime_env["OPENCODE_CONFIG_CONTENT"] == (
        '{"enabled_providers":["codetalk-local"]}'
    )
    assert runtime_env["OPENCODE_DISABLE_AUTOUPDATE"] == "1"
    assert runtime_env["OPENAI_API_KEY"] == "live-runtime-secret"
    assert runtime_env["CODETALK_AGENT_ARTIFACT_DIR"] == str(tmp_path.resolve())


def test_cli_adapter_exposes_only_captured_task_materials_to_the_sandbox(
    monkeypatch, tmp_path
):
    from app.services import agent_cli_bridge

    CodexCliAdapter, _, _ = _adapter_types()
    task_root = tmp_path / "task-run"
    artifact_dir = task_root / "agent_runs" / "analyze"
    material_dir = task_root / "inputs" / "design-doc"
    artifact_dir.mkdir(parents=True)
    material_dir.mkdir(parents=True)
    parsed = material_dir / "parsed_text.txt"
    copied = material_dir / "original" / "design.md"
    copied.parent.mkdir()
    parsed.write_text("parsed", encoding="utf-8")
    copied.write_text("original", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("must remain inaccessible", encoding="utf-8")
    captured: dict[str, object] = {}

    async def fake_stream_agent_runtime(**kwargs):
        captured.update(kwargs)
        if False:
            yield "unreachable"

    monkeypatch.setattr(agent_cli_bridge, "stream_agent_runtime", fake_stream_agent_runtime)
    request = _request("codex", "read the captured design")
    request = replace(
        request,
        task_bundle={
            **request.task_bundle,
            "input_materials": {
                "materials": [
                    {
                        "parsed_text_path": str(parsed),
                        "copied_path": str(copied),
                        "original_path": str(outside),
                    }
                ]
            },
        },
    )
    adapter = CodexCliAdapter(artifact_dir)
    session = adapter.prepare(request)

    result = adapter.execute(session)

    assert result.status == "completed"
    assert captured["runtime"]["sandbox_read_paths"] == [
        str(copied.resolve()),
        str(parsed.resolve()),
    ]
    assert str(outside.resolve()) not in captured["runtime"]["sandbox_read_paths"]


@pytest.mark.parametrize("adapter_index,provider", [(0, "codex"), (1, "claude"), (2, "opencode")])
def test_cli_adapters_resume_with_opaque_provider_token(
    monkeypatch,
    tmp_path,
    adapter_index,
    provider,
):
    from app.services import agent_cli_bridge
    from app.services.provider_adapters.contracts import ProviderResumeToken

    captured: dict[str, object] = {}

    async def fake_stream_agent_runtime(**kwargs):
        captured.update(kwargs)
        yield "resumed"

    monkeypatch.setattr(agent_cli_bridge, "stream_agent_runtime", fake_stream_agent_runtime)
    adapter = _adapter_types()[adapter_index](tmp_path)
    session = adapter.prepare(_request(provider, " resume exactly \n\n"))
    token = ProviderResumeToken(provider=provider, value=f"opaque://{provider}/A:B-01")

    result = adapter.resume(session, token)

    assert not inspect.isawaitable(result)
    assert result.status == "completed"
    assert captured["prompt"] == " resume exactly \n\n"
    assert captured["resume_session_id"] == token.value


def test_cli_adapter_cancel_stops_the_active_bridge_execution(monkeypatch, tmp_path):
    from app.services import agent_cli_bridge

    CodexCliAdapter, _, _ = _adapter_types()
    started = threading.Event()

    async def fake_stream_agent_runtime(**kwargs):
        started.set()
        while not kwargs["is_cancelled"]():
            await asyncio.sleep(0.01)
        if False:
            yield "unreachable"

    monkeypatch.setattr(agent_cli_bridge, "stream_agent_runtime", fake_stream_agent_runtime)
    adapter = CodexCliAdapter(tmp_path)
    session = adapter.prepare(_request("codex", "cancel me"))
    result_box: list[object] = []

    def run_execution() -> None:
        result_box.append(adapter.execute(session))

    execution = threading.Thread(target=run_execution)
    execution.start()
    assert started.wait(timeout=1)

    cancelled = adapter.cancel(session)
    execution.join(timeout=1)

    assert not execution.is_alive()
    assert cancelled.status == "cancelled"
    assert result_box[0].status == "cancelled"
    assert adapter.cancel(session).status == "already_terminal"


def test_cli_adapter_reports_orchestrator_callback_cancellation(monkeypatch, tmp_path):
    from app.services import agent_cli_bridge

    CodexCliAdapter, _, _ = _adapter_types()

    async def fake_stream_agent_runtime(**kwargs):
        assert kwargs["is_cancelled"]() is True
        if False:
            yield "unreachable"

    monkeypatch.setattr(agent_cli_bridge, "stream_agent_runtime", fake_stream_agent_runtime)
    adapter = CodexCliAdapter(tmp_path)
    session = adapter.prepare(_request("codex", "cancelled by orchestrator"))

    result = adapter.execute(session, is_cancelled=lambda: True)

    assert result.status == "cancelled"
    assert result.exit_code is None


def test_cli_adapter_only_reports_files_created_or_changed_during_execution(
    monkeypatch,
    tmp_path,
):
    from app.services import agent_cli_bridge

    CodexCliAdapter, _, _ = _adapter_types()
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    unchanged = artifact_dir / "unchanged.md"
    overwritten = artifact_dir / "report.md"
    metadata_only = artifact_dir / "metadata.md"
    unchanged.write_text("old and unchanged", encoding="utf-8")
    overwritten.write_text("before", encoding="utf-8")
    metadata_only.write_text("same content", encoding="utf-8")
    original_stat = overwritten.stat()

    async def fake_stream_agent_runtime(**_kwargs):
        overwritten.write_text("after!", encoding="utf-8")
        os.utime(
            overwritten,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        metadata_only.chmod(metadata_only.stat().st_mode ^ 0o100)
        nested = artifact_dir / "nested"
        nested.mkdir()
        (nested / "trace.json").write_text("{}", encoding="utf-8")
        if False:
            yield "unreachable"

    monkeypatch.setattr(agent_cli_bridge, "stream_agent_runtime", fake_stream_agent_runtime)
    adapter = CodexCliAdapter(artifact_dir)
    session = adapter.prepare(_request("codex", "artifact scan"))

    result = adapter.execute(session)
    (artifact_dir / "created-after-execution.md").write_text(
        "not produced by this invocation",
        encoding="utf-8",
    )
    candidates = adapter.collect_artifacts(session)

    assert result.status == "completed"
    assert [candidate.path for candidate in candidates] == [
        "metadata.md",
        "nested/trace.json",
        "report.md",
    ]


def test_cli_adapter_does_not_report_preexisting_artifacts(monkeypatch, tmp_path):
    from app.services import agent_cli_bridge

    CodexCliAdapter, _, _ = _adapter_types()
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (artifact_dir / "report.md").write_text("stale result", encoding="utf-8")

    async def fake_stream_agent_runtime(**_kwargs):
        if False:
            yield "unreachable"

    monkeypatch.setattr(agent_cli_bridge, "stream_agent_runtime", fake_stream_agent_runtime)
    adapter = CodexCliAdapter(artifact_dir)
    session = adapter.prepare(_request("codex", "do not trust stale output"))

    adapter.execute(session)

    assert adapter.collect_artifacts(session) == []


def test_cli_adapter_artifact_scan_rejects_directories_symlinks_and_outside_roots(
    monkeypatch,
    tmp_path,
):
    from app.services import agent_cli_bridge

    CodexCliAdapter, _, _ = _adapter_types()
    artifact_dir = tmp_path / "artifacts"
    outside_dir = tmp_path / "outside"
    artifact_dir.mkdir()
    outside_dir.mkdir()
    outside_file = outside_dir / "outside.md"
    outside_file.write_text("outside", encoding="utf-8")

    async def fake_stream_agent_runtime(**_kwargs):
        (artifact_dir / "directory.md").mkdir()
        (artifact_dir / "file-link.md").symlink_to(outside_file)
        (artifact_dir / "directory-link").symlink_to(outside_dir, target_is_directory=True)
        (artifact_dir / "valid.md").write_text("valid", encoding="utf-8")
        if False:
            yield "unreachable"

    monkeypatch.setattr(agent_cli_bridge, "stream_agent_runtime", fake_stream_agent_runtime)
    adapter = CodexCliAdapter(artifact_dir)
    session = adapter.prepare(_request("codex", "artifact boundary"))

    adapter.execute(session)

    assert [candidate.path for candidate in adapter.collect_artifacts(session)] == [
        "valid.md"
    ]
    outside_session = replace(session, artifact_dir=str(outside_dir))
    assert adapter.collect_artifacts(outside_session) == []


def test_cli_adapters_have_no_second_process_or_workflow_state_implementation():
    adapter_dir = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "provider_adapters"
    )
    sources = "\n".join(
        (adapter_dir / filename).read_text(encoding="utf-8")
        for filename in ("cli_base.py", "codex_cli.py", "claude_code.py", "opencode.py")
    )

    assert "create_subprocess" not in sources
    assert "subprocess." not in sources
    assert "event_store" not in sources
    assert "ai_thread_session" not in sources
    assert "AgentRuntimeStore" not in sources
    assert "Task.status" not in sources
