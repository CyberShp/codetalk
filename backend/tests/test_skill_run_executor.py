from __future__ import annotations

import json


def _invocation(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    payload = {
        "invocation_id": "skill_invocation_1",
        "task_run_id": "task_run_1",
        "skill_id": "skill.example",
        "skill_version_id": "skill_version_1",
        "skill_content_digest": "sha256:" + "1" * 64,
        "artifact_root": str(root),
        "inputs": {},
        "selected_deliveries": [],
        "source_zip_path": str(tmp_path / "source.zip"),
        "ir_path": str(tmp_path / "ir.json"),
        "validation_report_path": str(tmp_path / "validation.json"),
    }
    path = root / "skill_invocation.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path, root


def test_skill_run_executor_records_fake_agent_success_lifecycle(tmp_path):
    from app.services.skill_run_executor import ScriptedSkillAgentAdapter, SkillRunExecutor

    invocation_path, root = _invocation(tmp_path)
    result = SkillRunExecutor(
        adapter=ScriptedSkillAgentAdapter([
            {"event": "agent_event", "status": "running", "message": "working"},
            {"event": "done", "status": "completed"},
        ])
    ).execute(invocation_path)

    lifecycle = json.loads((root / "agent_run_lifecycle.json").read_text(encoding="utf-8"))
    assert result["status"] == "completed"
    assert lifecycle["status"] == "completed"
    assert [event["event"] for event in lifecycle["events"]] == [
        "create",
        "start",
        "agent_event",
        "done",
    ]
