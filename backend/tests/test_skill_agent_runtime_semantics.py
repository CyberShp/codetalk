from __future__ import annotations

from types import SimpleNamespace


def test_skill_agent_uses_selected_runtime_completion_semantics(monkeypatch):
    from app.services import skill_agent_adapter as module

    monkeypatch.setattr(
        module,
        "get_agent_runtime_sync",
        lambda _runtime_id: {
            "output_mode": "auto",
            "completion_mode": "idle_after_output",
            "idle_complete_seconds": 7,
            "sentinel_text": "",
            "session_persistence": "none",
            "resume_args": [],
            "timeout_seconds": 90,
        },
    )
    session = SimpleNamespace(
        metadata={
            "runtime": {
                "output_mode": "plain",
                "completion_mode": "process_exit",
                "idle_complete_seconds": 5,
                "timeout_seconds": 1800,
                "activity_timeout_seconds": 1800,
                "total_timeout_seconds": 1800,
            }
        }
    )

    module._apply_configured_runtime(
        session,
        {"runtime_config_id": "internal-agent"},
        hard_timeout_seconds=1800,
    )

    runtime = session.metadata["runtime"]
    assert runtime["output_mode"] == "auto"
    assert runtime["completion_mode"] == "idle_after_output"
    assert runtime["idle_complete_seconds"] == 7
    assert runtime["timeout_seconds"] == 90
    assert runtime["activity_timeout_seconds"] == 90
    assert runtime["total_timeout_seconds"] == 1800


def test_skill_agent_hard_timeout_still_caps_runtime(monkeypatch):
    from app.services import skill_agent_adapter as module

    monkeypatch.setattr(
        module,
        "get_agent_runtime_sync",
        lambda _runtime_id: {
            "completion_mode": "process_exit",
            "timeout_seconds": 3600,
        },
    )
    session = SimpleNamespace(metadata={"runtime": {}})

    module._apply_configured_runtime(
        session,
        {"runtime_config_id": "internal-agent"},
        hard_timeout_seconds=900,
    )

    runtime = session.metadata["runtime"]
    assert runtime["timeout_seconds"] == 900
    assert runtime["activity_timeout_seconds"] == 900
    assert runtime["total_timeout_seconds"] == 900
