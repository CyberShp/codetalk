from __future__ import annotations

import json

import pytest

from test_skill_run_executor import _invocation


class RaisingSkillAgentAdapter:
    def __init__(self, *, raise_at):
        self.raise_at = raise_at

    def create(self, invocation):
        if self.raise_at == "create":
            raise RuntimeError("create exploded")
        return {"session_id": "raising-session"}

    def start(self, session):
        if self.raise_at == "start":
            raise RuntimeError("start exploded")
        return {"started": True}

    def poll(self, session):
        if self.raise_at == "poll":
            raise RuntimeError("poll exploded")
        return {"event": "complete", "status": "completed"}

    def cancel(self, session):
        return {"status": "cancelled"}


def test_skill_agent_lifecycle_times_out_and_cancels(tmp_path, monkeypatch):
    from app.services import skill_run_executor as module
    from app.services.skill_run_executor import (
        ScriptedSkillAgentAdapter,
        SkillRunExecutor,
        SkillRunExecutorError,
    )

    invocation_path, root = _invocation(tmp_path)
    ticks = iter([0.0, 2.0])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks, 2.0))
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    adapter = ScriptedSkillAgentAdapter([{"event": "still_running", "status": "running"}])

    with pytest.raises(SkillRunExecutorError, match="timed out"):
        SkillRunExecutor(adapter=adapter, timeout_seconds=1).execute(invocation_path)

    lifecycle = json.loads((root / "agent_run_lifecycle.json").read_text(encoding="utf-8"))
    assert adapter.cancelled is True
    assert lifecycle["status"] == "timed_out"
    assert [event["event"] for event in lifecycle["events"]] == [
        "create",
        "start",
        "timeout",
        "cancel",
    ]


@pytest.mark.parametrize(
    ("event", "expected_status", "raises"),
    [
        ({"event": "session_lost", "status": "session_lost"}, "session_lost", True),
        ({"event": "restart", "status": "restarted"}, "restarted", False),
        ({"event": "cancelled", "status": "cancelled"}, "cancelled", False),
    ],
)
def test_skill_agent_lifecycle_terminal_states(tmp_path, event, expected_status, raises):
    from app.services.skill_run_executor import ScriptedSkillAgentAdapter, SkillRunExecutor, SkillRunExecutorError

    invocation_path, root = _invocation(tmp_path)
    executor = SkillRunExecutor(adapter=ScriptedSkillAgentAdapter([event]))
    if raises:
        with pytest.raises(SkillRunExecutorError, match=expected_status):
            executor.execute(invocation_path)
    else:
        assert executor.execute(invocation_path)["status"] == expected_status

    lifecycle = json.loads((root / "agent_run_lifecycle.json").read_text(encoding="utf-8"))
    assert lifecycle["status"] == expected_status
    assert lifecycle["events"][-1]["event"] == event["event"]


@pytest.mark.parametrize(
    ("raise_at", "expected_events"),
    [
        ("create", ["create", "failed"]),
        ("start", ["create", "start", "failed"]),
        ("poll", ["create", "start", "failed"]),
    ],
)
def test_skill_agent_lifecycle_persists_adapter_failures(tmp_path, raise_at, expected_events):
    from app.services.skill_run_executor import SkillRunExecutor, SkillRunExecutorError

    invocation_path, root = _invocation(tmp_path)

    with pytest.raises(SkillRunExecutorError, match=f"{raise_at} exploded"):
        SkillRunExecutor(adapter=RaisingSkillAgentAdapter(raise_at=raise_at)).execute(invocation_path)

    lifecycle = json.loads((root / "agent_run_lifecycle.json").read_text(encoding="utf-8"))
    assert lifecycle["status"] == "failed"
    assert [event["event"] for event in lifecycle["events"]] == expected_events
    assert lifecycle["events"][-1]["phase"] == raise_at
    assert lifecycle["events"][-1]["error"] == f"{raise_at} exploded"
