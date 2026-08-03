from __future__ import annotations

import json
import time
from types import SimpleNamespace


def test_external_agent_repair_is_the_default_before_terminal_block() -> None:
    from app.config import Settings

    assert Settings(_env_file=None).external_agent_quality_repair_enabled is True


def test_quality_profile_deadlines_cap_rapid_and_deep_runs() -> None:
    from app.services.workbench_workflow_runner import _quality_profile_deadline_seconds

    assert _quality_profile_deadline_seconds("rapid") == 15 * 60
    assert _quality_profile_deadline_seconds("deep") == 90 * 60


def test_explicit_timeout_cannot_bypass_the_rapid_profile_deadline(
    tmp_path, monkeypatch
) -> None:
    from app.services import workbench_workflow_runner as runner

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "task_run.json").write_text("{}", encoding="utf-8")
    task_run = SimpleNamespace(
        artifact_dir=str(task_dir),
        task_bundle={"execution_profile": {"id": "rapid"}},
    )
    monkeypatch.setattr(runner.time, "monotonic", lambda: 1000.0)

    assert runner._quality_run_deadline_monotonic(task_run, timeout_sec=5400) == 1900.0


def test_repairing_is_a_durable_nonterminal_cockpit_state() -> None:
    from app.api.agent_workbench import _TASK_RUN_TERMINAL_STATUSES, _task_run_ui_status

    assert "quality_repairing" not in _TASK_RUN_TERMINAL_STATUSES
    assert _task_run_ui_status(execution={"status": "quality_repairing"}, nodes=[]) == {
        "status": "quality_repairing",
        "label": "正在自动修复质量问题",
    }


def test_execute_request_allows_the_deep_deadline_but_not_more() -> None:
    from pydantic import ValidationError

    from app.api.agent_workbench import TaskRunExecuteRequest

    assert TaskRunExecuteRequest(timeout_sec=90 * 60).timeout_sec == 90 * 60
    try:
        TaskRunExecuteRequest(timeout_sec=90 * 60 + 1)
    except ValidationError:
        pass
    else:
        raise AssertionError("task execution deadline must be capped at 90 minutes")


def test_repair_feedback_keeps_failed_obligations_but_strips_hidden_truth() -> None:
    from app.services.workbench_workflow_runner import _quality_feedback_from_audit

    feedback = _quality_feedback_from_audit(
        {
            "status": "needs_rework",
            "issues": [
                {
                    "artifact": "sfmea.json",
                    "field": "failure_mode",
                    "code": "missing_error_path",
                    "message": "close ERROR-7 cleanup path",
                    "gold_claims": [{"gold_id": "G-SECRET"}],
                    "truth_package": "/hidden/case/truth.json",
                    "details": {
                        "critical_chains": [{"chain_id": "CHAIN-SECRET"}],
                        "observed": "cleanup edge is absent",
                    },
                }
            ],
        },
        required_artifacts=["sfmea.json", "report.md"],
        quality_artifact="quality.json",
    )

    encoded = json.dumps(feedback)
    assert feedback["affected_artifacts"] == ["sfmea.json"]
    assert feedback["repairable_issues"][0]["code"] == "missing_error_path"
    assert feedback["repairable_issues"][0]["field"] == "failure_mode"
    assert feedback["repairable_issues"][0]["details"]["observed"] == "cleanup edge is absent"
    assert "G-SECRET" not in encoded
    assert "CHAIN-SECRET" not in encoded
    assert "/hidden/case" not in encoded


def test_explicit_unrecoverable_issue_skips_model_repair_and_keeps_block_reason() -> None:
    from app.services.workbench_workflow_runner import _quality_feedback_from_audit

    feedback = _quality_feedback_from_audit(
        {
            "status": "invalid",
            "issues": [
                {
                    "artifact": "report.md",
                    "code": "critical_source_contradiction",
                    "message": "source and claim contradict",
                    "unrecoverable": True,
                }
            ],
        },
        required_artifacts=["report.md"],
        quality_artifact="quality.json",
    )

    assert feedback["affected_artifacts"] == []
    assert feedback["repairable_issue_count"] == 0
    assert feedback["non_repairable_issue_count"] == 1
    assert feedback["blocked_reasons"] == ["critical_source_contradiction"]


def test_under_five_minutes_is_suspicious_only_when_the_audit_is_insufficient() -> None:
    from app.services.workbench_workflow_runner import (
        _fast_result_requires_quality_continuation,
    )

    insufficient = {"status": "needs_rework", "issue_count": 2}
    sufficient = {"status": "deliverable", "issue_count": 0}
    assert _fast_result_requires_quality_continuation(
        elapsed_seconds=240,
        audit=insufficient,
        cache_reused=False,
        remaining_seconds=300,
        minimum_remaining_seconds=120,
    ) is True
    assert _fast_result_requires_quality_continuation(
        elapsed_seconds=240,
        audit=sufficient,
        cache_reused=False,
        remaining_seconds=300,
        minimum_remaining_seconds=120,
    ) is False
    assert _fast_result_requires_quality_continuation(
        elapsed_seconds=20,
        audit=insufficient,
        cache_reused=True,
        remaining_seconds=300,
        minimum_remaining_seconds=120,
    ) is False
    assert _fast_result_requires_quality_continuation(
        elapsed_seconds=240,
        audit=insufficient,
        cache_reused=False,
        remaining_seconds=60,
        minimum_remaining_seconds=120,
    ) is False


def test_cold_fast_deliverable_auto_continues_when_work_evidence_is_missing() -> None:
    from app.services.workbench_workflow_runner import (
        _apply_fast_result_work_sufficiency,
    )

    audit, diagnostic = _apply_fast_result_work_sufficiency(
        audit={"status": "deliverable", "deliverable": True, "issues": []},
        elapsed_seconds=120,
        cache_reused=False,
        remaining_seconds=300,
        minimum_remaining_seconds=120,
        repair_artifact="report.md",
    )

    assert diagnostic["status"] == "insufficient"
    assert diagnostic["auto_continue"] is True
    assert audit["status"] == "needs_rework"
    assert audit["issues"][0]["code"] == "fast_result_work_sufficiency_incomplete"


def test_cached_fast_deliverable_is_not_penalized_for_short_elapsed_time() -> None:
    from app.services.workbench_workflow_runner import (
        _apply_fast_result_work_sufficiency,
    )

    audit, diagnostic = _apply_fast_result_work_sufficiency(
        audit={"status": "deliverable", "deliverable": True, "issues": []},
        elapsed_seconds=20,
        cache_reused=True,
        remaining_seconds=300,
        minimum_remaining_seconds=120,
        repair_artifact="report.md",
    )

    assert diagnostic["status"] == "reused"
    assert diagnostic["auto_continue"] is False
    assert audit["status"] == "deliverable"


def test_cold_fast_result_accepts_the_production_profile_evidence_shape() -> None:
    from app.services.workbench_workflow_runner import (
        _apply_fast_result_work_sufficiency,
    )

    audit, diagnostic = _apply_fast_result_work_sufficiency(
        audit={
            "status": "deliverable",
            "deliverable": True,
            "issues": [],
            "profile_execution_evidence": {
                "status": "sufficient",
                "provider_call_count": 2,
                "branch_count": 3,
                "missing_branch_provider_work": [],
                "under_evidenced_branches": [],
            },
            "fact_verification": {"total": 4},
            "quality_axes": {"coverage_breadth": {"total": 8}},
        },
        elapsed_seconds=120,
        cache_reused=False,
        remaining_seconds=300,
        minimum_remaining_seconds=120,
        repair_artifact="report.md",
    )

    assert diagnostic["status"] == "sufficient"
    assert diagnostic["auto_continue"] is False
    assert audit["status"] == "deliverable"


def test_repair_feedback_never_truncates_exact_failed_obligations() -> None:
    from app.services.workbench_workflow_runner import _quality_feedback_from_audit

    issues = [
        {
            "artifact": "report.md",
            "field": f"field-{index}",
            "code": "missing_obligation",
            "message": f"close obligation {index}",
        }
        for index in range(75)
    ]
    feedback = _quality_feedback_from_audit(
        {"status": "needs_rework", "issues": issues},
        required_artifacts=["report.md"],
        quality_artifact="quality.json",
    )

    assert feedback["repairable_issue_count"] == 75
    assert len(feedback["repairable_issues"]) == 75
    assert {item["field"] for item in feedback["repairable_issues"]} == {
        f"field-{index}" for index in range(75)
    }


def test_external_repair_records_an_expired_absolute_deadline(tmp_path) -> None:
    from types import SimpleNamespace

    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner

    result = WorkbenchWorkflowRunner(tmp_path / "runs")._attempt_external_agent_quality_repair(
        task_run=SimpleNamespace(agent_runs=[]),
        step_results=[],
        audit={"status": "needs_rework", "issues": []},
        deadline_monotonic=time.monotonic() - 1,
    )

    assert result["attempted"] is False
    assert result["recordable"] is True
    assert result["reason"] == "workflow_deadline_exceeded"


def test_final_quality_audit_fails_closed_after_the_absolute_deadline(tmp_path) -> None:
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner

    result = WorkbenchWorkflowRunner(
        tmp_path / "runs"
    )._audit_test_activity_quality_before_deadline(
        task_run=SimpleNamespace(),
        deadline_monotonic=time.monotonic() - 1,
    )

    assert result["status"] == "invalid"
    assert result["deliverable"] is False
    assert result["issues"][0]["code"] == "workflow_deadline_exceeded"


def test_slow_final_quality_audit_is_interrupted_at_the_absolute_deadline(
    tmp_path, monkeypatch
) -> None:
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner

    runner = WorkbenchWorkflowRunner(tmp_path / "runs")

    def slow_audit(*, task_run):
        time.sleep(0.08)
        return {"status": "deliverable", "deliverable": True, "issues": []}

    monkeypatch.setattr(runner, "audit_test_activity_quality", slow_audit)
    started = time.monotonic()
    result = runner._audit_test_activity_quality_before_deadline(
        task_run=SimpleNamespace(),
        deadline_monotonic=started + 0.01,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.06
    assert result["issues"][0]["code"] == "workflow_deadline_exceeded"
