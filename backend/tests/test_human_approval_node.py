from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading

import pytest


def test_human_approval_waits_records_immutable_decision_and_rebuilds_projection(
    tmp_path: Path,
) -> None:
    from app.services.human_approval import (
        ApprovalConflict,
        HumanApprovalStore,
        project_approval,
    )

    store = HumanApprovalStore(tmp_path)
    entered_at = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    total_execution_timeout_at = entered_at + timedelta(minutes=5)
    approval_deadline_at = entered_at + timedelta(hours=24)

    waiting = store.enter_waiting(
        task_id="task-1",
        attempt_id="attempt-1",
        node_id="release-approval",
        entered_at=entered_at,
        total_execution_timeout_at=total_execution_timeout_at,
        approval_deadline_at=approval_deadline_at,
        input_context={
            "summary": '{"purpose":"durable checkpoint before approval"}',
            "sha256": "sha256:" + "a" * 64,
            "truncated": False,
        },
    )

    assert waiting.status == "waiting_for_input"
    assert waiting.decision is None
    assert waiting.total_execution_timeout_at == total_execution_timeout_at
    assert waiting.approval_deadline_at == approval_deadline_at
    assert waiting.input_context == {
        "summary": '{"purpose":"durable checkpoint before approval"}',
        "sha256": "sha256:" + "a" * 64,
        "truncated": False,
    }
    assert project_approval(waiting, now=total_execution_timeout_at + timedelta(seconds=1)) == {
        "node_status": "waiting_for_input",
        "approval_status": "pending",
        "delivery_status": "pending",
        "approval_deadline_at": approval_deadline_at.isoformat(),
        "total_execution_timeout_paused": True,
    }

    decided_at = entered_at + timedelta(minutes=2)
    approved = store.decide(
        "release-approval",
        decision="approve",
        actor="reviewer-7",
        reason="release evidence is complete",
        decided_at=decided_at,
    )
    duplicate = store.decide(
        "release-approval",
        decision="approve",
        actor="reviewer-7",
        reason="release evidence is complete",
        decided_at=decided_at,
    )

    assert duplicate == approved
    assert approved.decision is not None
    assert approved.decision.decision == "approve"
    assert approved.decision.actor == "reviewer-7"
    assert approved.decision.reason == "release evidence is complete"
    assert approved.decision.decided_at == decided_at
    assert project_approval(approved) == {
        "node_status": "approved",
        "approval_status": "approved",
        "delivery_status": "pending",
        "approval_deadline_at": approval_deadline_at.isoformat(),
        "total_execution_timeout_paused": False,
        "decision": {
            "decision": "approve",
            "actor": "reviewer-7",
            "reason": "release evidence is complete",
            "decided_at": decided_at.isoformat(),
        },
    }
    assert project_approval(store.load("release-approval")) == project_approval(approved)
    assert (tmp_path / "approvals" / "release-approval.json").is_file()
    assert not list((tmp_path / "approvals").glob("release-approval.json.tmp-*"))

    with pytest.raises(ApprovalConflict, match="already decided"):
        store.decide(
            "release-approval",
            decision="approve",
            actor="other-reviewer",
            reason="this must not overwrite the immutable decision",
            decided_at=decided_at + timedelta(seconds=1),
        )

    with pytest.raises(ApprovalConflict, match="already decided"):
        store.decide(
            "release-approval",
            decision="reject",
            actor="reviewer-8",
            reason="conflicting decision",
            decided_at=decided_at + timedelta(minutes=1),
        )

    persisted = store.load("release-approval")
    assert persisted == approved
    assert persisted.decision is not None
    assert persisted.decision.decision == "approve"


def test_human_approval_preserves_a_rejection_as_the_first_decision(
    tmp_path: Path,
) -> None:
    from app.services.human_approval import (
        ApprovalConflict,
        HumanApprovalStore,
        project_approval,
    )

    store = HumanApprovalStore(tmp_path)
    entered_at = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    store.enter_waiting(
        task_id="task-1",
        attempt_id="attempt-1",
        node_id="risk-approval",
        entered_at=entered_at,
        total_execution_timeout_at=entered_at + timedelta(minutes=5),
        approval_deadline_at=entered_at + timedelta(hours=1),
    )

    rejected = store.decide(
        "risk-approval",
        decision="reject",
        actor="reviewer-9",
        reason="risk remains open",
        decided_at=entered_at + timedelta(minutes=3),
        received_at=entered_at + timedelta(minutes=3),
    )

    assert project_approval(rejected)["node_status"] == "rejected"
    assert project_approval(rejected)["approval_status"] == "rejected"
    assert rejected.decision is not None
    assert rejected.decision.actor == "reviewer-9"
    assert rejected.decision.reason == "risk remains open"
    with pytest.raises(ApprovalConflict, match="already decided"):
        store.decide(
            "risk-approval",
            decision="approve",
            actor="reviewer-10",
            reason="conflicting decision",
            decided_at=entered_at + timedelta(minutes=4),
        )


def test_human_approval_serializes_competing_first_decisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.human_approval as approval_module

    entered_at = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    store_a = approval_module.HumanApprovalStore(tmp_path)
    store_b = approval_module.HumanApprovalStore(tmp_path)
    store_a.enter_waiting(
        task_id="task-1",
        attempt_id="attempt-1",
        node_id="release-approval",
        entered_at=entered_at,
        total_execution_timeout_at=None,
        approval_deadline_at=entered_at + timedelta(hours=1),
    )
    first_writer_entered = threading.Event()
    release_first_writer = threading.Event()
    second_writer_finished = threading.Event()
    original_write = approval_module._write_json_atomic

    def delayed_approve_write(path: Path, payload: dict) -> None:
        decision = payload.get("decision") or {}
        if decision.get("decision") == "approve":
            first_writer_entered.set()
            assert release_first_writer.wait(timeout=2)
        original_write(path, payload)

    monkeypatch.setattr(approval_module, "_write_json_atomic", delayed_approve_write)
    errors: list[BaseException] = []

    def decide(store, decision: str, actor: str) -> None:
        try:
            store.decide(
                "release-approval",
                decision=decision,
                actor=actor,
                reason=f"{decision} reason",
                decided_at=entered_at + timedelta(minutes=1),
                received_at=entered_at + timedelta(minutes=1),
            )
        except BaseException as error:  # pragma: no cover - asserted below
            errors.append(error)
        finally:
            if decision == "reject":
                second_writer_finished.set()

    first = threading.Thread(target=decide, args=(store_a, "approve", "reviewer-a"))
    second = threading.Thread(target=decide, args=(store_b, "reject", "reviewer-b"))
    first.start()
    assert first_writer_entered.wait(timeout=2)
    second.start()

    assert not second_writer_finished.wait(timeout=0.1)
    release_first_writer.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert len(errors) == 1
    assert isinstance(errors[0], approval_module.ApprovalConflict)
    record = store_a.load("release-approval")
    assert record is not None
    assert record.decision is not None
    assert record.decision.decision == "approve"
    assert record.decision.actor == "reviewer-a"


def test_human_approval_claims_expiry_at_the_exact_deadline_without_mutating_record(
    tmp_path: Path,
) -> None:
    from app.services.human_approval import ApprovalExpired, HumanApprovalStore, project_approval

    store = HumanApprovalStore(tmp_path)
    entered_at = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    deadline = entered_at + timedelta(minutes=1)
    store.enter_waiting(
        task_id="task-1",
        attempt_id="attempt-1",
        node_id="release-approval",
        entered_at=entered_at,
        total_execution_timeout_at=None,
        approval_deadline_at=deadline,
    )
    approval_path = tmp_path / "approvals" / "release-approval.json"
    original_record = approval_path.read_bytes()

    receipt = store.claim_expiry("release-approval", now=deadline)

    assert receipt is not None
    assert receipt.node_id == "release-approval"
    assert receipt.expired_at == deadline
    assert receipt.approval_deadline_at == deadline
    assert approval_path.read_bytes() == original_record
    record = store.load("release-approval")
    assert record is not None
    assert project_approval(record, now=deadline)["node_status"] == "timed_out"
    with pytest.raises(ApprovalExpired, match="deadline has passed"):
        store.decide(
            "release-approval",
            decision="approve",
            actor="reviewer",
            reason="late decision cannot backdate itself",
            decided_at=entered_at,
            received_at=deadline,
        )


def test_human_approval_rejects_expiry_receipt_with_mismatched_identity_or_deadline(
    tmp_path: Path,
) -> None:
    from app.services.human_approval import ApprovalValidationError, HumanApprovalStore

    store = HumanApprovalStore(tmp_path)
    entered_at = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    deadline = entered_at + timedelta(minutes=1)
    store.enter_waiting(
        task_id="task-1",
        attempt_id="attempt-1",
        node_id="release-approval",
        entered_at=entered_at,
        total_execution_timeout_at=None,
        approval_deadline_at=deadline,
    )
    assert store.claim_expiry("release-approval", now=deadline) is not None
    receipt_path = tmp_path / "approvals" / "release-approval.receipt.json"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["task_id"] = "other-task"
    payload["approval_deadline_at"] = (deadline + timedelta(seconds=1)).isoformat()
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ApprovalValidationError, match="does not match approval record"):
        store.load_expiry_receipt("release-approval")


def test_human_approval_cancellation_wins_over_later_expiry_or_decision(
    tmp_path: Path,
) -> None:
    from app.services.human_approval import ApprovalConflict, HumanApprovalStore

    store = HumanApprovalStore(tmp_path)
    entered_at = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
    deadline = entered_at + timedelta(minutes=1)
    store.enter_waiting(
        task_id="task-1",
        attempt_id="attempt-1",
        node_id="release-approval",
        entered_at=entered_at,
        total_execution_timeout_at=None,
        approval_deadline_at=deadline,
    )

    receipt = store.claim_cancellation("release-approval", now=entered_at + timedelta(seconds=1))

    assert receipt is not None
    assert store.claim_expiry("release-approval", now=deadline) is None
    with pytest.raises(ApprovalConflict, match="already cancelled"):
        store.decide(
            "release-approval",
            decision="approve",
            actor="reviewer",
            reason="cancellation already won",
            decided_at=entered_at,
            received_at=entered_at + timedelta(seconds=2),
        )


def test_human_approval_rejects_cancellation_receipt_with_mismatched_identity(
    tmp_path: Path,
) -> None:
    from app.services.human_approval import ApprovalValidationError, HumanApprovalStore

    store = HumanApprovalStore(tmp_path)
    entered_at = datetime.now(timezone.utc)
    store.enter_waiting(
        task_id="task-1",
        attempt_id="attempt-1",
        node_id="release-approval",
        entered_at=entered_at,
        total_execution_timeout_at=None,
        approval_deadline_at=entered_at + timedelta(minutes=1),
    )
    assert store.claim_cancellation("release-approval", now=entered_at) is not None
    receipt_path = tmp_path / "approvals" / "release-approval.cancelled.json"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["attempt_id"] = "other-attempt"
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ApprovalValidationError, match="does not match approval record"):
        store.load_cancellation_receipt("release-approval")


def test_human_approval_uses_trusted_receive_time_when_caller_omits_it(
    tmp_path: Path,
) -> None:
    from app.services.human_approval import ApprovalExpired, HumanApprovalStore

    store = HumanApprovalStore(tmp_path)
    entered_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    deadline = entered_at + timedelta(minutes=1)
    store.enter_waiting(
        task_id="task-1",
        attempt_id="attempt-1",
        node_id="release-approval",
        entered_at=entered_at,
        total_execution_timeout_at=None,
        approval_deadline_at=deadline,
    )

    with pytest.raises(ApprovalExpired, match="deadline has passed"):
        store.decide(
            "release-approval",
            decision="approve",
            actor="reviewer",
            reason="client timestamps cannot backdate receipt",
            decided_at=entered_at + timedelta(seconds=1),
        )

    assert store.load_expiry_receipt("release-approval") is not None


def test_human_approval_reports_one_atomic_first_decision_to_concurrent_callers(
    tmp_path: Path,
) -> None:
    from app.services.human_approval import HumanApprovalStore

    store = HumanApprovalStore(tmp_path)
    entered_at = datetime.now(timezone.utc)
    store.enter_waiting(
        task_id="task-1",
        attempt_id="attempt-1",
        node_id="release-approval",
        entered_at=entered_at,
        total_execution_timeout_at=None,
        approval_deadline_at=entered_at + timedelta(hours=1),
    )
    barrier = threading.Barrier(2)
    committed: list[bool] = []

    def decide() -> None:
        barrier.wait(timeout=2)
        _, created = store.decide_with_outcome(
            "release-approval",
            decision="approve",
            actor="reviewer",
            reason="same decision",
            decided_at=entered_at + timedelta(seconds=1),
            received_at=entered_at + timedelta(seconds=1),
        )
        committed.append(created)

    threads = [threading.Thread(target=decide) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert sorted(committed) == [False, True]


def test_human_approval_deadline_beats_cancellation_before_monitor_claims_expiry(
    tmp_path: Path,
) -> None:
    from app.services.human_approval import HumanApprovalStore

    store = HumanApprovalStore(tmp_path)
    entered_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    deadline = entered_at + timedelta(minutes=1)
    store.enter_waiting(
        task_id="task-1",
        attempt_id="attempt-1",
        node_id="release-approval",
        entered_at=entered_at,
        total_execution_timeout_at=None,
        approval_deadline_at=deadline,
    )

    assert store.claim_cancellation(
        "release-approval",
        now=deadline,
    ) is None
    assert store.load_cancellation_receipt("release-approval") is None
    assert store.load_expiry_receipt("release-approval") is not None


def test_human_approval_rejects_expiry_receipt_before_immutable_deadline(
    tmp_path: Path,
) -> None:
    from app.services.human_approval import ApprovalValidationError, HumanApprovalStore

    store = HumanApprovalStore(tmp_path)
    entered_at = datetime.now(timezone.utc)
    deadline = entered_at + timedelta(minutes=1)
    store.enter_waiting(
        task_id="task-1",
        attempt_id="attempt-1",
        node_id="release-approval",
        entered_at=entered_at,
        total_execution_timeout_at=None,
        approval_deadline_at=deadline,
    )
    assert store.claim_expiry("release-approval", now=deadline) is not None
    receipt_path = tmp_path / "approvals" / "release-approval.receipt.json"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["expired_at"] = (deadline - timedelta(seconds=1)).isoformat()
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ApprovalValidationError, match="before approval deadline"):
        store.load_expiry_receipt("release-approval")
