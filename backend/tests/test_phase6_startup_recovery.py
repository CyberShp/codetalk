from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_v3_attempt(
    root: Path,
    *,
    task_run_id: str = "task_run_recovery",
    status: str = "running",
) -> Path:
    attempt_dir = root / task_run_id
    agent_dir = attempt_dir / "agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "report.md").write_text("# Recovered report\n", encoding="utf-8")
    definition = {
        "compiled_contract_version": 3,
        "validation_profile": "artifact_only",
        "declared_outputs": [{
            "output_id": "report",
            "artifact": "report.md",
            "required": True,
            "producer_step_id": "agent",
            "schema": None,
        }],
        "outputs": [],
        "steps": [{"id": "agent", "type": "agent_task"}],
    }
    plan = {
        "compiled_contract_version": 3,
        "nodes": [
            {
                "node_id": "agent",
                "kind": "agent",
                "type": "agent_task",
                "handler_id": "agent",
                "required_outputs": ["report"],
            },
            {
                "node_id": "validator",
                "kind": "validator",
                "handler_id": "artifact_exists",
                "required_outputs": ["report"],
                "depends_on": ["agent"],
            },
        ],
        "topological_order": ["agent", "validator"],
    }
    if status == "waiting_for_input":
        plan["nodes"].insert(1, {
            "node_id": "approval",
            "kind": "human_approval",
            "type": "human_approval",
            "handler_id": "human_approval",
            "depends_on": ["agent"],
        })
        plan["topological_order"].insert(1, "approval")
    _write_json(attempt_dir / "compiled_definition.json", definition)
    _write_json(attempt_dir / "compiled_plan.json", plan)
    _write_json(attempt_dir / "input_snapshot.json", {})
    _write_json(
        attempt_dir / "agent_execution_descriptors.json",
        {
            "schema_version": 1,
            "agent_runs": [{"step_id": "agent", "artifact_dir": str(agent_dir)}],
        },
    )
    snapshot = {
        "schema_version": 3,
        "snapshot_kind": "codetalk_run_snapshot",
        "identity": {
            "task_run_id": task_run_id,
            "task_id": "task-recovery",
            "attempt_number": 1,
            "parent_task_run_id": "",
        },
        "components": {
            "v3_runtime_contract": {
                "path": "compiled_definition.json",
                "sha256": _sha256(attempt_dir / "compiled_definition.json"),
            },
            "execution_plan": {
                "path": "compiled_plan.json",
                "sha256": _sha256(attempt_dir / "compiled_plan.json"),
            },
            "input_snapshot": {
                "path": "input_snapshot.json",
                "sha256": _sha256(attempt_dir / "input_snapshot.json"),
            },
            "agent_execution_descriptors": {
                "path": "agent_execution_descriptors.json",
                "sha256": _sha256(
                    attempt_dir / "agent_execution_descriptors.json"
                ),
            },
        },
    }
    _write_json(attempt_dir / "run_snapshot_v3.json", snapshot)
    _write_json(
        attempt_dir / "task_run.json",
        {
            "task_run_id": task_run_id,
            "task_id": "task-recovery",
            "workflow_id": "workflow-recovery",
            "workspace_id": "workspace",
            "repo_path": str(root),
            "artifact_dir": str(attempt_dir),
            "workflow_snapshot": {"steps": [{"id": "agent", "type": "agent_task"}]},
            "input_snapshot": {},
            "task_bundle": {"compiled_definition": definition, "compiled_plan": plan},
            "agent_runs": [{"step_id": "agent", "artifact_dir": str(agent_dir)}],
            "status": status,
            "execution_status": status,
            "artifact_validation_status": "not_started",
            "governance_status": "not_requested",
            "delivery_status": "pending",
            "quality_status": "pending",
            "runtime": {"status": status},
        },
    )
    return attempt_dir


def _replace_frozen_plan(attempt_dir: Path, plan: dict) -> None:
    """Update a test fixture plan together with its immutable snapshot hash."""

    plan_path = attempt_dir / "compiled_plan.json"
    _write_json(plan_path, plan)
    snapshot_path = attempt_dir / "run_snapshot_v3.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["components"]["execution_plan"]["sha256"] = _sha256(plan_path)
    _write_json(snapshot_path, snapshot)


def test_startup_recovery_reuses_checkpoint_and_completes_same_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import agent_workbench
    from app.services.node_checkpoint import NodeCheckpointStore
    from app.services.workflow_startup_recovery import reconcile_v3_startup_recovery
    from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner

    data_dir = tmp_path / "data"
    task_runs = data_dir / "workbench" / "task_runs"
    attempt_dir = _write_v3_attempt(task_runs)
    task_run_id = attempt_dir.name
    executions = 0

    def complete_once(**_: object) -> dict:
        nonlocal executions
        executions += 1
        return {
            "step_id": "agent",
            "type": "agent_task",
            "status": "completed",
            "artifact_dir": str(attempt_dir / "agent"),
        }

    def crash_after_checkpoint(event_type: str, _: dict) -> None:
        if event_type == "node_completed":
            raise RuntimeError("crash after durable checkpoint")

    interrupted = WorkbenchWorkflowRunner(task_runs, event_sink=crash_after_checkpoint)
    interrupted._execute_agent_step = complete_once  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="durable checkpoint"):
        interrupted.execute_task_run(task_run_id)
    assert executions == 1

    decisions = reconcile_v3_startup_recovery(task_runs)

    assert [(item.task_run_id, item.action, item.recovered_node_ids) for item in decisions] == [
        (task_run_id, "resume", ("agent",)),
    ]
    monkeypatch.setattr(agent_workbench.settings, "data_dir", str(data_dir))
    monkeypatch.setattr(
        WorkbenchWorkflowRunner,
        "_execute_agent_step",
        lambda *_args, **_kwargs: pytest.fail("checkpointed agent must not execute again"),
    )
    asyncio.run(
        agent_workbench._execute_task_run_background(
            task_run_id=task_run_id,
            payload=agent_workbench.TaskRunExecuteRequest(),
        )
    )

    store = WorkbenchTaskRunEventStore(task_runs)
    assert executions == 1
    assert store.current_status(task_run_id) == "completed"
    assert (attempt_dir / "workflow_execution.json").is_file()
    events = store.list_after(task_run_id, limit=200)
    assert sum(
        event["event_type"] == "node_checkpoint_committed"
        and event["payload"].get("node_id") == "agent"
        for event in events
    ) == 1
    assert any(
        event["event_type"] == "node_reused"
        and event["payload"].get("node_id") == "agent"
        for event in events
    )


def test_startup_recovery_preserves_waiting_and_excludes_v3_from_legacy(
    tmp_path: Path,
) -> None:
    from app.services.human_approval import HumanApprovalStore
    from app.services.workflow_startup_recovery import reconcile_v3_startup_recovery
    from app.services.workbench_task_run_events import (
        WorkbenchTaskRunEventStore,
        reconcile_interrupted_task_runs,
    )
    from datetime import datetime, timedelta, timezone

    root = tmp_path / "task_runs"
    attempt_dir = _write_v3_attempt(root, task_run_id="task_run_waiting", status="waiting_for_input")
    HumanApprovalStore(attempt_dir).enter_waiting(
        task_id="task-recovery",
        attempt_id="task_run_waiting",
        node_id="approval",
        entered_at=datetime.now(timezone.utc),
        total_execution_timeout_at=None,
        approval_deadline_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    legacy_dir = root / "task_run_legacy"
    _write_json(
        legacy_dir / "task_run.json",
        {"task_run_id": "task_run_legacy", "status": "running", "runtime": {"status": "running"}},
    )

    decisions = reconcile_v3_startup_recovery(root)
    legacy = reconcile_interrupted_task_runs(
        root,
        exclude_task_run_ids={item.task_run_id for item in decisions},
    )

    assert [(item.task_run_id, item.action) for item in decisions] == [
        ("task_run_waiting", "waiting_for_input"),
    ]
    store = WorkbenchTaskRunEventStore(root)
    assert store.current_status("task_run_waiting") == "waiting_for_input"
    assert legacy["task_runs"] == [{"task_run_id": "task_run_legacy", "previous_status": "running"}]


def test_startup_recovery_preserves_earned_axes_while_waiting_for_later_approval(
    tmp_path: Path,
) -> None:
    from app.services.human_approval import HumanApprovalStore
    from app.services.workflow_startup_recovery import reconcile_v3_startup_recovery

    root = tmp_path / "task_runs"
    attempt_dir = _write_v3_attempt(
        root,
        task_run_id="task_run_later_approval",
        status="waiting_for_input",
    )
    plan_path = attempt_dir / "compiled_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    agent, first_approval, validator = plan["nodes"]
    first_approval["depends_on"] = ["validator"]
    second_approval = {
        "node_id": "approval_after_validation",
        "kind": "human_approval",
        "type": "human_approval",
        "handler_id": "human_approval",
        "depends_on": ["approval"],
    }
    plan["nodes"] = [agent, validator, first_approval, second_approval]
    plan["topological_order"] = [
        "agent",
        "validator",
        "approval",
        "approval_after_validation",
    ]
    _replace_frozen_plan(attempt_dir, plan)

    entered_at = datetime.now(timezone.utc)
    approvals = HumanApprovalStore(attempt_dir)
    approvals.enter_waiting(
        task_id="task-recovery",
        attempt_id=attempt_dir.name,
        node_id="approval",
        entered_at=entered_at,
        total_execution_timeout_at=None,
        approval_deadline_at=entered_at + timedelta(hours=1),
    )
    approvals.decide(
        "approval",
        decision="approve",
        actor="reviewer",
        reason="first approval completed",
        decided_at=entered_at + timedelta(seconds=1),
        received_at=entered_at + timedelta(seconds=1),
    )
    approvals.enter_waiting(
        task_id="task-recovery",
        attempt_id=attempt_dir.name,
        node_id="approval_after_validation",
        entered_at=entered_at + timedelta(seconds=2),
        total_execution_timeout_at=None,
        approval_deadline_at=entered_at + timedelta(hours=1),
    )
    task_path = attempt_dir / "task_run.json"
    task_payload = json.loads(task_path.read_text(encoding="utf-8"))
    task_payload.update({
        "artifact_validation_status": "passed",
        "governance_status": "passed",
        "delivery_status": "ready",
        "quality_status": "passed",
    })
    _write_json(task_path, task_payload)

    decisions = reconcile_v3_startup_recovery(root)

    assert [(item.task_run_id, item.action) for item in decisions] == [
        (attempt_dir.name, "waiting_for_input"),
    ]
    recovered = json.loads(task_path.read_text(encoding="utf-8"))
    assert recovered["artifact_validation_status"] == "passed"
    assert recovered["governance_status"] == "passed"
    assert recovered["delivery_status"] == "pending"
    assert recovered["quality_status"] == "pending"


def test_startup_recovery_keeps_invalid_approval_diagnostics_private(
    tmp_path: Path,
) -> None:
    from app.services.human_approval import HumanApprovalStore
    from app.services.workflow_startup_recovery import reconcile_v3_startup_recovery
    from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore

    root = tmp_path / "task_runs"
    attempt_dir = _write_v3_attempt(
        root,
        task_run_id="task_run_invalid_approval",
        status="waiting_for_input",
    )
    entered_at = datetime.now(timezone.utc)
    approvals = HumanApprovalStore(attempt_dir)
    approvals.enter_waiting(
        task_id="task-recovery",
        attempt_id=attempt_dir.name,
        node_id="approval",
        entered_at=entered_at,
        total_execution_timeout_at=None,
        approval_deadline_at=entered_at + timedelta(hours=1),
    )
    approval_path = attempt_dir / "approvals" / "approval.json"
    approval_path.write_text("{not valid JSON", encoding="utf-8")

    decisions = reconcile_v3_startup_recovery(root)

    assert [(item.task_run_id, item.action) for item in decisions] == [
        (attempt_dir.name, "failed"),
    ]
    public_task = json.loads((attempt_dir / "task_run.json").read_text(encoding="utf-8"))
    assert public_task["runtime"]["error"] == "工作流恢复校验失败，请重新运行。"
    assert "approval" not in public_task["runtime"]["error"]
    failure = WorkbenchTaskRunEventStore(root).list_after(attempt_dir.name)[-1]
    assert failure["event_type"] == "v3_startup_recovery_failed"
    assert failure["payload"] == {
        "status": "failed",
        "user_message": "工作流恢复校验失败，请重新运行。",
        "deduplication_key": "v3-startup-recovery:failed",
    }


def test_startup_recovery_claims_expired_approval_and_persists_timed_out(
    tmp_path: Path,
) -> None:
    from app.services.human_approval import HumanApprovalStore
    from app.services.workflow_startup_recovery import reconcile_v3_startup_recovery
    from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore

    root = tmp_path / "task_runs"
    attempt_dir = _write_v3_attempt(root, task_run_id="task_run_expired", status="waiting_for_input")
    entered_at = datetime.now(timezone.utc) - timedelta(hours=2)
    approval_store = HumanApprovalStore(attempt_dir)
    approval_store.enter_waiting(
        task_id="task-recovery",
        attempt_id=attempt_dir.name,
        node_id="approval",
        entered_at=entered_at,
        total_execution_timeout_at=None,
        approval_deadline_at=entered_at + timedelta(hours=1),
    )
    approval_path = attempt_dir / "approvals" / "approval.json"
    original_approval = approval_path.read_bytes()
    task_path = attempt_dir / "task_run.json"
    task_payload = json.loads(task_path.read_text(encoding="utf-8"))
    task_payload["artifact_validation_status"] = "passed"
    task_payload["governance_status"] = "waived"
    task_payload["delivery_status"] = "ready"
    task_payload["quality_status"] = "passed"
    task_path.write_text(json.dumps(task_payload), encoding="utf-8")

    decisions = reconcile_v3_startup_recovery(root)

    assert [(item.task_run_id, item.action) for item in decisions] == [
        (attempt_dir.name, "timed_out"),
    ]
    assert WorkbenchTaskRunEventStore(root).current_status(attempt_dir.name) == "timed_out"
    timed_out_payload = json.loads(task_path.read_text(encoding="utf-8"))
    assert timed_out_payload["artifact_validation_status"] == "passed"
    assert timed_out_payload["governance_status"] == "waived"
    assert timed_out_payload["delivery_status"] == "blocked"
    assert timed_out_payload["quality_status"] == "blocked"
    assert approval_path.read_bytes() == original_approval
    assert (attempt_dir / "approvals" / "approval.receipt.json").is_file()


def test_startup_recovery_does_not_resume_a_durably_cancelled_wait(
    tmp_path: Path,
) -> None:
    from app.services.human_approval import HumanApprovalStore
    from app.services.workflow_startup_recovery import reconcile_v3_startup_recovery
    from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore

    root = tmp_path / "task_runs"
    attempt_dir = _write_v3_attempt(root, task_run_id="task_run_cancelled_wait", status="waiting_for_input")
    entered_at = datetime.now(timezone.utc)
    approval_store = HumanApprovalStore(attempt_dir)
    approval_store.enter_waiting(
        task_id="task-recovery",
        attempt_id=attempt_dir.name,
        node_id="approval",
        entered_at=entered_at,
        total_execution_timeout_at=None,
        approval_deadline_at=entered_at + timedelta(hours=1),
    )
    assert approval_store.claim_cancellation("approval", now=entered_at) is not None

    decisions = reconcile_v3_startup_recovery(root)

    assert [(item.task_run_id, item.action) for item in decisions] == [
        (attempt_dir.name, "cancelled"),
    ]
    assert WorkbenchTaskRunEventStore(root).current_status(attempt_dir.name) == "cancelled"


def test_checkpoint_recovery_rollback_preserves_waiting_but_interrupts_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import settings
    from app.services.human_approval import HumanApprovalStore
    from app.services.workflow_startup_recovery import reconcile_v3_startup_recovery
    from app.services.workbench_task_run_events import reconcile_interrupted_task_runs
    from datetime import datetime, timedelta, timezone

    root = tmp_path / "task_runs"
    _write_v3_attempt(root, task_run_id="task_run_running", status="running")
    waiting_dir = _write_v3_attempt(
        root,
        task_run_id="task_run_waiting_rollback",
        status="waiting_for_input",
    )
    HumanApprovalStore(waiting_dir).enter_waiting(
        task_id="task-recovery",
        attempt_id=waiting_dir.name,
        node_id="approval",
        entered_at=datetime.now(timezone.utc),
        total_execution_timeout_at=None,
        approval_deadline_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    monkeypatch.setattr(settings, "workflow_checkpoint_reuse_enabled", False)

    decisions = reconcile_v3_startup_recovery(root)
    legacy = reconcile_interrupted_task_runs(
        root,
        exclude_task_run_ids={item.task_run_id for item in decisions},
    )

    assert [(item.task_run_id, item.action, item.recovered_node_ids) for item in decisions] == [
        (waiting_dir.name, "waiting_for_input", ()),
    ]
    assert legacy["task_runs"] == [{
        "task_run_id": "task_run_running",
        "previous_status": "running",
    }]


def test_startup_recovery_resumes_decided_approval_despite_stale_waiting_projection(
    tmp_path: Path,
) -> None:
    from app.services.human_approval import HumanApprovalStore
    from app.services.workflow_startup_recovery import reconcile_v3_startup_recovery
    from datetime import datetime, timedelta, timezone

    root = tmp_path / "task_runs"
    attempt_dir = _write_v3_attempt(
        root,
        task_run_id="task_run_decided_before_queue",
        status="waiting_for_input",
    )
    entered_at = datetime.now(timezone.utc)
    approval_store = HumanApprovalStore(attempt_dir)
    approval_store.enter_waiting(
        task_id="task-recovery",
        attempt_id=attempt_dir.name,
        node_id="approval",
        entered_at=entered_at,
        total_execution_timeout_at=entered_at + timedelta(seconds=41),
        approval_deadline_at=entered_at + timedelta(hours=1),
    )
    approval_store.decide(
        "approval",
        decision="approve",
        actor="reviewer",
        reason="approved before backend process exited",
        decided_at=entered_at + timedelta(seconds=1),
    )

    decisions = reconcile_v3_startup_recovery(root)

    assert [(item.task_run_id, item.action) for item in decisions] == [
        (attempt_dir.name, "resume"),
    ]


def test_hitl_rollback_fails_pending_wait_without_deleting_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import settings
    from app.services.human_approval import HumanApprovalStore
    from app.services.workflow_startup_recovery import reconcile_v3_startup_recovery
    from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore
    from datetime import datetime, timedelta, timezone

    root = tmp_path / "task_runs"
    attempt_dir = _write_v3_attempt(
        root,
        task_run_id="task_run_hitl_rollback",
        status="waiting_for_input",
    )
    entered_at = datetime.now(timezone.utc)
    approval_store = HumanApprovalStore(attempt_dir)
    approval_store.enter_waiting(
        task_id="task-recovery",
        attempt_id=attempt_dir.name,
        node_id="approval",
        entered_at=entered_at,
        total_execution_timeout_at=None,
        approval_deadline_at=entered_at + timedelta(hours=1),
    )
    monkeypatch.setattr(settings, "workflow_hitl_enabled", False)

    decisions = reconcile_v3_startup_recovery(root)

    assert [(item.task_run_id, item.action, item.reason) for item in decisions] == [
        (
            attempt_dir.name,
            "failed",
            "phase6_feature_disabled:human_approval",
        ),
    ]
    assert WorkbenchTaskRunEventStore(root).current_status(attempt_dir.name) == "failed"
    assert approval_store.load("approval") is not None


def test_startup_recovery_fails_closed_for_invalid_v3_snapshot(tmp_path: Path) -> None:
    from app.services.workflow_startup_recovery import reconcile_v3_startup_recovery
    from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore

    root = tmp_path / "task_runs"
    attempt_dir = _write_v3_attempt(root, task_run_id="task_run_tampered")
    (attempt_dir / "compiled_plan.json").write_text("{}", encoding="utf-8")

    decisions = reconcile_v3_startup_recovery(root)

    assert [(item.task_run_id, item.action) for item in decisions] == [
        ("task_run_tampered", "failed"),
    ]
    store = WorkbenchTaskRunEventStore(root)
    assert store.current_status("task_run_tampered") == "failed"
    assert store.list_after("task_run_tampered")[-1]["event_type"] == "v3_startup_recovery_failed"


@pytest.mark.asyncio
async def test_startup_recovery_reschedules_when_prior_queue_event_survived_process_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import agent_workbench
    from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore

    data_dir = tmp_path / "data"
    task_runs = data_dir / "workbench" / "task_runs"
    attempt_dir = _write_v3_attempt(task_runs, task_run_id="task_run_double_restart")
    task_run_id = attempt_dir.name
    store = WorkbenchTaskRunEventStore(task_runs)
    store.append_once(
        task_run_id,
        "queued",
        {"source": "startup_recovery"},
        deduplication_key="v3-startup-recovery:queued",
    )

    resumed: list[str] = []

    async def record_resume(*, task_run_id: str, payload: object) -> None:
        resumed.append(task_run_id)

    monkeypatch.setattr(agent_workbench.settings, "data_dir", str(data_dir))
    monkeypatch.setattr(agent_workbench, "_execute_task_run_background", record_resume)
    agent_workbench._ACTIVE_TASK_RUN_IDS.discard(task_run_id)
    try:
        assert agent_workbench.schedule_recovered_v3_task_run(task_run_id) is True
        await asyncio.sleep(0)
        assert resumed == [task_run_id]
        events = store.list_after(task_run_id)
        assert sum(
            event["payload"].get("deduplication_key")
            == "v3-startup-recovery:queued"
            for event in events
        ) == 1
    finally:
        agent_workbench._ACTIVE_TASK_RUN_IDS.discard(task_run_id)


def test_frozen_v3_snapshot_cannot_be_downgraded_by_mutable_task_projection(
    tmp_path: Path,
) -> None:
    from app.services.workflow_startup_recovery import reconcile_v3_startup_recovery

    root = tmp_path / "task_runs"
    attempt_dir = _write_v3_attempt(root, task_run_id="task_run_projection_tamper")
    task_path = attempt_dir / "task_run.json"
    payload = json.loads(task_path.read_text(encoding="utf-8"))
    payload["workflow_snapshot"] = {}
    payload["task_bundle"] = {}
    task_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    (attempt_dir / "compiled_definition.json").unlink()
    (attempt_dir / "compiled_plan.json").unlink()

    decisions = reconcile_v3_startup_recovery(root)

    assert [(item.task_run_id, item.action) for item in decisions] == [
        ("task_run_projection_tamper", "failed"),
    ]


def test_startup_recovery_uses_attempt_identity_when_task_id_is_empty(tmp_path: Path) -> None:
    from app.services.node_checkpoint import NodeCheckpointStore
    from app.services.workflow_startup_recovery import reconcile_v3_startup_recovery

    root = tmp_path / "task_runs"
    attempt_dir = _write_v3_attempt(root, task_run_id="task_run_without_task")
    task_path = attempt_dir / "task_run.json"
    task_payload = json.loads(task_path.read_text(encoding="utf-8"))
    task_payload["task_id"] = ""
    task_path.write_text(json.dumps(task_payload, sort_keys=True), encoding="utf-8")
    snapshot_path = attempt_dir / "run_snapshot_v3.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["identity"]["task_id"] = ""
    snapshot_path.write_text(json.dumps(snapshot, sort_keys=True), encoding="utf-8")
    NodeCheckpointStore(attempt_dir).commit_completed(
        task_id=attempt_dir.name,
        attempt_id=attempt_dir.name,
        node_id="agent",
        idempotency_key="sha256:agent",
        input_hash="sha256:input",
        output_artifact_hashes={},
        result_snapshot={"step_id": "agent", "status": "completed"},
    )

    decisions = reconcile_v3_startup_recovery(root)

    assert [(item.task_run_id, item.action, item.recovered_node_ids) for item in decisions] == [
        (attempt_dir.name, "resume", ("agent",)),
    ]


def test_recovered_v3_scheduler_queues_once_without_new_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import agent_workbench
    from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore

    data_dir = tmp_path / "data"
    task_runs = data_dir / "workbench" / "task_runs"
    attempt_dir = _write_v3_attempt(task_runs, status="running")
    monkeypatch.setattr(agent_workbench.settings, "data_dir", str(data_dir))
    scheduled: list[object] = []

    def capture(coro: object) -> None:
        coro.close()  # type: ignore[attr-defined]
        scheduled.append(coro)

    monkeypatch.setattr(agent_workbench.asyncio, "create_task", capture)
    try:
        assert agent_workbench.schedule_recovered_v3_task_run(attempt_dir.name) is True
        assert agent_workbench.schedule_recovered_v3_task_run(attempt_dir.name) is False
    finally:
        agent_workbench._ACTIVE_TASK_RUN_IDS.discard(attempt_dir.name)

    store = WorkbenchTaskRunEventStore(task_runs)
    assert len(scheduled) == 1
    assert store.current_status(attempt_dir.name) == "queued"
    events = store.list_after(attempt_dir.name)
    assert [event["event_type"] for event in events] == ["queued"]
    assert events[0]["payload"]["deduplication_key"] == "v3-startup-recovery:queued"
    assert sorted(path.name for path in task_runs.iterdir() if path.is_dir()) == [
        ".locks",
        attempt_dir.name,
    ]


def test_execution_timeout_deadline_is_persisted_before_background_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import agent_workbench

    data_dir = tmp_path / "data"
    task_runs = data_dir / "workbench" / "task_runs"
    attempt_dir = _write_v3_attempt(task_runs, status="prepared")
    monkeypatch.setattr(agent_workbench.settings, "data_dir", str(data_dir))

    deadline = agent_workbench._persist_task_run_total_execution_deadline(
        attempt_dir.name,
        timeout_sec=37,
    )

    task_payload = json.loads(
        (attempt_dir / "task_run.json").read_text(encoding="utf-8")
    )
    assert deadline is not None
    assert task_payload["runtime"]["total_execution_timeout_at"] == deadline


def test_recovered_v3_scheduler_uses_persisted_remaining_timeout_without_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import agent_workbench
    from app.services.workbench_task_run import WorkbenchTaskRunStore

    data_dir = tmp_path / "data"
    task_runs = data_dir / "workbench" / "task_runs"
    attempt_dir = _write_v3_attempt(task_runs)
    deadline = datetime.now(timezone.utc) + timedelta(seconds=37)
    task_path = attempt_dir / "task_run.json"
    task_payload = json.loads(task_path.read_text(encoding="utf-8"))
    task_payload["runtime"]["total_execution_timeout_at"] = deadline.isoformat()
    _write_json(task_path, task_payload)
    monkeypatch.setattr(agent_workbench.settings, "data_dir", str(data_dir))

    task_run = WorkbenchTaskRunStore(task_runs).load(attempt_dir.name)
    remaining = agent_workbench._task_run_resume_timeout_sec(task_run)

    assert 1 <= remaining <= 37


def test_recovered_v3_scheduler_keeps_timeout_enabled_after_deadline_elapsed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import agent_workbench
    from app.services.workbench_task_run import WorkbenchTaskRunStore

    data_dir = tmp_path / "data"
    task_runs = data_dir / "workbench" / "task_runs"
    attempt_dir = _write_v3_attempt(task_runs)
    task_path = attempt_dir / "task_run.json"
    task_payload = json.loads(task_path.read_text(encoding="utf-8"))
    task_payload["runtime"]["total_execution_timeout_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    _write_json(task_path, task_payload)
    monkeypatch.setattr(agent_workbench.settings, "data_dir", str(data_dir))

    task_run = WorkbenchTaskRunStore(task_runs).load(attempt_dir.name)

    assert agent_workbench._task_run_resume_timeout_sec(task_run) == 1


def test_recovered_v3_scheduler_keeps_timeout_disabled_when_no_deadline_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import agent_workbench
    from app.services.workbench_task_run import WorkbenchTaskRunStore

    data_dir = tmp_path / "data"
    task_runs = data_dir / "workbench" / "task_runs"
    attempt_dir = _write_v3_attempt(task_runs)
    monkeypatch.setattr(agent_workbench.settings, "data_dir", str(data_dir))

    task_run = WorkbenchTaskRunStore(task_runs).load(attempt_dir.name)

    assert agent_workbench._task_run_resume_timeout_sec(task_run) == 0


def test_approval_wait_freezes_remaining_execution_budget(
    tmp_path: Path,
) -> None:
    from app.api import agent_workbench
    from app.services.human_approval import HumanApprovalStore

    attempt_dir = _write_v3_attempt(
        tmp_path / "task_runs",
        status="waiting_for_input",
    )
    entered_at = datetime.now(timezone.utc) - timedelta(hours=2)
    store = HumanApprovalStore(attempt_dir)
    record = store.enter_waiting(
        task_id="task-recovery",
        attempt_id=attempt_dir.name,
        node_id="approval",
        entered_at=entered_at,
        total_execution_timeout_at=entered_at + timedelta(seconds=41),
        approval_deadline_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    assert agent_workbench._approval_total_execution_budget_sec(record) == 41


def test_recovered_decided_approval_rearms_frozen_execution_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import agent_workbench
    from app.services.human_approval import HumanApprovalStore
    from app.services.workbench_task_run import WorkbenchTaskRunStore

    data_dir = tmp_path / "data"
    task_runs = data_dir / "workbench" / "task_runs"
    attempt_dir = _write_v3_attempt(task_runs, status="waiting_for_input")
    task_path = attempt_dir / "task_run.json"
    task_payload = json.loads(task_path.read_text(encoding="utf-8"))
    task_payload["runtime"]["total_execution_timeout_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=1)
    ).isoformat()
    _write_json(task_path, task_payload)
    entered_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    approval_store = HumanApprovalStore(attempt_dir)
    approval_store.enter_waiting(
        task_id="task-recovery",
        attempt_id=attempt_dir.name,
        node_id="approval",
        entered_at=entered_at,
        total_execution_timeout_at=entered_at + timedelta(seconds=41),
        approval_deadline_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    approval_store.decide(
        "approval",
        decision="approve",
        actor="reviewer",
        reason="resume after restart",
        decided_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(agent_workbench.settings, "data_dir", str(data_dir))
    task_run = WorkbenchTaskRunStore(task_runs).load(attempt_dir.name)

    remaining = agent_workbench._task_run_resume_timeout_sec(task_run)

    assert remaining == 41
    refreshed = json.loads(task_path.read_text(encoding="utf-8"))
    rearmed = datetime.fromisoformat(
        refreshed["runtime"]["total_execution_timeout_at"]
    )
    assert 39 <= (rearmed - datetime.now(timezone.utc)).total_seconds() <= 41
    assert refreshed["runtime"]["total_execution_timeout_rearm_count"] == 1

    refreshed["runtime"]["total_execution_timeout_at"] = (
        datetime.now(timezone.utc) + timedelta(seconds=17)
    ).isoformat()
    _write_json(task_path, refreshed)

    resumed_again = agent_workbench._task_run_resume_timeout_sec(task_run)

    assert 1 <= resumed_again <= 17


def test_v3_runner_does_not_execute_after_persisted_total_deadline(
    tmp_path: Path,
) -> None:
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner

    task_runs = tmp_path / "task_runs"
    attempt_dir = _write_v3_attempt(task_runs)
    task_path = attempt_dir / "task_run.json"
    task_payload = json.loads(task_path.read_text(encoding="utf-8"))
    task_payload["runtime"]["total_execution_timeout_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    _write_json(task_path, task_payload)
    runner = WorkbenchWorkflowRunner(task_runs)
    executions = 0

    def execute_agent(**_: object) -> dict:
        nonlocal executions
        executions += 1
        return {"step_id": "agent", "status": "completed"}

    runner._execute_agent_step = execute_agent  # type: ignore[method-assign]

    result = runner.execute_task_run(attempt_dir.name, timeout_sec=1)

    assert executions == 0
    assert result.execution_status == "timed_out"
    assert result.step_results[0]["error"] == "节点执行失败，请重试。"
    assert result.step_results[0]["technical_diagnostics"]["error"] == (
        "total_execution_timeout"
    )
