from __future__ import annotations

import json


def _write_invocation(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "skill_invocation.json").write_text(
        json.dumps(
            {
                "skill_version_id": "skill_version_1",
                "skill_content_digest": "sha256:" + "1" * 64,
                "inputs": {"input.source": "/repo"},
                "selected_deliveries": ["delivery.report"],
                "required_artifact_ids": ["artifact.report"],
                "artifact_root": "artifacts",
                "judge": {
                    "required": True,
                    "isolated_session": True,
                    "artifact_ids": ["artifact.report"],
                },
            }
        ),
        encoding="utf-8",
    )


def test_required_skill_judge_moves_from_pending_validation_to_ready(tmp_path):
    from app.services.skill_judge import evaluate_skill_judge

    _write_invocation(tmp_path)
    pending = evaluate_skill_judge(tmp_path, required=True)
    assert pending["status"] == "PENDING_VALIDATION"
    assert pending["ready"] is False
    assert "producer_transcript" not in pending["judge_input"]
    assert pending["judge_input"]["artifact_root"] == "artifacts"
    assert pending["judge_input"]["required_artifact_ids"] == ["artifact.report"]
    assert pending["judge_input"]["judge"]["artifact_ids"] == ["artifact.report"]

    (tmp_path / "skill_judge_report.json").write_text(
        json.dumps({"status": "READY", "ready": True}),
        encoding="utf-8",
    )
    ready = evaluate_skill_judge(tmp_path, required=True)
    assert ready["status"] == "READY"
    assert ready["ready"] is True


def test_optional_missing_skill_judge_warns_without_ready(tmp_path):
    from app.services.skill_judge import evaluate_skill_judge

    _write_invocation(tmp_path)
    result = evaluate_skill_judge(tmp_path, required=False)
    assert result["status"] == "WARNING"
    assert result["warnings"] == ["judge_report_missing"]
