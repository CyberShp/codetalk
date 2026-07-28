"""Local, immutable Human Approval records for workflow attempt artifacts."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from app.services.interprocess_file_lock import exclusive_file_lock


_NODE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
ApprovalDecisionValue = Literal["approve", "reject"]


class ApprovalConflict(RuntimeError):
    """Raised when an approval record would receive a conflicting change."""


class ApprovalExpired(RuntimeError):
    """Raised when a decision arrives after the approval deadline."""


class ApprovalNotFound(LookupError):
    """Raised when a decision is submitted before a node enters waiting."""


class ApprovalValidationError(ValueError):
    """Raised when an approval artifact is malformed."""


@dataclass(frozen=True)
class ApprovalDecision:
    decision: ApprovalDecisionValue
    actor: str
    reason: str
    decided_at: datetime

    def to_payload(self) -> dict[str, str]:
        return {
            "decision": self.decision,
            "actor": self.actor,
            "reason": self.reason,
            "decided_at": _timestamp(self.decided_at),
        }


@dataclass(frozen=True)
class ApprovalExpiryReceipt:
    """Append-only evidence that expiry won the approval arbitration."""

    task_id: str
    attempt_id: str
    node_id: str
    approval_deadline_at: datetime
    expired_at: datetime

    def to_payload(self) -> dict[str, str]:
        return {
            "outcome": "timed_out",
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "node_id": self.node_id,
            "approval_deadline_at": _timestamp(self.approval_deadline_at),
            "expired_at": _timestamp(self.expired_at),
        }


@dataclass(frozen=True)
class ApprovalCancellationReceipt:
    """Append-only evidence that cancellation won the approval arbitration."""

    task_id: str
    attempt_id: str
    node_id: str
    cancelled_at: datetime

    def to_payload(self) -> dict[str, str]:
        return {
            "outcome": "cancelled",
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "node_id": self.node_id,
            "cancelled_at": _timestamp(self.cancelled_at),
        }


@dataclass(frozen=True)
class HumanApprovalRecord:
    approval_version: int
    task_id: str
    attempt_id: str
    node_id: str
    status: Literal["waiting_for_input", "approved", "rejected"]
    entered_at: datetime
    total_execution_timeout_at: datetime | None
    approval_deadline_at: datetime
    decision: ApprovalDecision | None
    input_context: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "approval_version": self.approval_version,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "node_id": self.node_id,
            "status": self.status,
            "entered_at": _timestamp(self.entered_at),
            "total_execution_timeout_at": (
                _timestamp(self.total_execution_timeout_at)
                if self.total_execution_timeout_at is not None
                else None
            ),
            "approval_deadline_at": _timestamp(self.approval_deadline_at),
            "decision": self.decision.to_payload() if self.decision else None,
            "input_context": self.input_context,
        }


class HumanApprovalStore:
    """Persist one Human Approval record per node in an attempt artifact directory."""

    def __init__(self, attempt_dir: str | Path) -> None:
        self.attempt_dir = Path(attempt_dir)
        self.approval_dir = self.attempt_dir / "approvals"

    def enter_waiting(
        self,
        *,
        task_id: str,
        attempt_id: str,
        node_id: str,
        entered_at: datetime,
        total_execution_timeout_at: datetime | None,
        approval_deadline_at: datetime,
        input_context: dict[str, Any] | None = None,
    ) -> HumanApprovalRecord:
        """Record a node's explicit waiting state without consuming execution timeout."""
        record = HumanApprovalRecord(
            approval_version=1,
            task_id=_required_text(task_id, "task_id"),
            attempt_id=_required_text(attempt_id, "attempt_id"),
            node_id=_clean_node_id(node_id),
            status="waiting_for_input",
            entered_at=_as_utc(entered_at, "entered_at"),
            total_execution_timeout_at=(
                _as_utc(total_execution_timeout_at, "total_execution_timeout_at")
                if total_execution_timeout_at is not None
                else None
            ),
            approval_deadline_at=_as_utc(approval_deadline_at, "approval_deadline_at"),
            decision=None,
            input_context=_clean_input_context(input_context),
        )
        if record.approval_deadline_at <= record.entered_at:
            raise ApprovalValidationError("approval_deadline_at must be after entered_at")

        self.approval_dir.mkdir(parents=True, exist_ok=True)
        with _approval_lock(self.approval_dir):
            existing = self.load(record.node_id)
            if existing is not None:
                if _same_waiting_request(existing, record):
                    return existing
                raise ApprovalConflict(f"approval node {record.node_id} already exists")
            _write_json_atomic(self._approval_path(record.node_id), record.to_payload())
            return record

    def decide(
        self,
        node_id: str,
        *,
        decision: ApprovalDecisionValue,
        actor: str,
        reason: str,
        decided_at: datetime,
        received_at: datetime | None = None,
    ) -> HumanApprovalRecord:
        """Commit the first decision; repeats of that decision return it unchanged."""
        record, _ = self.decide_with_outcome(
            node_id,
            decision=decision,
            actor=actor,
            reason=reason,
            decided_at=decided_at,
            received_at=received_at,
        )
        return record

    def decide_with_outcome(
        self,
        node_id: str,
        *,
        decision: ApprovalDecisionValue,
        actor: str,
        reason: str,
        decided_at: datetime,
        received_at: datetime | None = None,
    ) -> tuple[HumanApprovalRecord, bool]:
        """Commit a decision and atomically report whether this call created it."""
        self.approval_dir.mkdir(parents=True, exist_ok=True)
        with _approval_lock(self.approval_dir):
            return self._decide_locked(
                node_id,
                decision=decision,
                actor=actor,
                reason=reason,
                decided_at=decided_at,
                received_at=received_at,
            )

    def _decide_locked(
        self,
        node_id: str,
        *,
        decision: ApprovalDecisionValue,
        actor: str,
        reason: str,
        decided_at: datetime,
        received_at: datetime | None,
    ) -> tuple[HumanApprovalRecord, bool]:
        record = self.load(node_id)
        if record is None:
            raise ApprovalNotFound(f"approval node {_clean_node_id(node_id)} was not found")
        expiry_receipt = self.load_expiry_receipt(record.node_id)
        if expiry_receipt is not None:
            raise ApprovalExpired(f"approval node {record.node_id} deadline has passed")
        if self.load_cancellation_receipt(record.node_id) is not None:
            raise ApprovalConflict(f"approval node {record.node_id} already cancelled")
        clean_decision = _clean_decision(decision)
        proposed_decision = ApprovalDecision(
            decision=clean_decision,
            actor=_required_text(actor, "actor"),
            reason=_required_text(reason, "reason"),
            decided_at=_as_utc(decided_at, "decided_at"),
        )
        if record.decision is not None:
            if record.decision == proposed_decision:
                return record, False
            raise ApprovalConflict(f"approval node {record.node_id} already decided")

        resolved_at = (
            _as_utc(received_at, "received_at")
            if received_at is not None
            else datetime.now(timezone.utc)
        )
        if resolved_at >= record.approval_deadline_at:
            self._claim_expiry_locked(record, resolved_at)
            raise ApprovalExpired(f"approval node {record.node_id} deadline has passed")
        resolved = HumanApprovalRecord(
            approval_version=record.approval_version,
            task_id=record.task_id,
            attempt_id=record.attempt_id,
            node_id=record.node_id,
            status="approved" if clean_decision == "approve" else "rejected",
            entered_at=record.entered_at,
            total_execution_timeout_at=record.total_execution_timeout_at,
            approval_deadline_at=record.approval_deadline_at,
            decision=proposed_decision,
            input_context=record.input_context,
        )
        _write_json_atomic(self._approval_path(record.node_id), resolved.to_payload())
        return resolved, True

    def claim_expiry(
        self,
        node_id: str,
        *,
        now: datetime,
    ) -> ApprovalExpiryReceipt | None:
        """Durably claim expiry without altering the original approval record."""
        self.approval_dir.mkdir(parents=True, exist_ok=True)
        with _approval_lock(self.approval_dir):
            record = self.load(node_id)
            if record is None:
                raise ApprovalNotFound(f"approval node {_clean_node_id(node_id)} was not found")
            return self._claim_expiry_locked(record, _as_utc(now, "now"))

    def _claim_expiry_locked(
        self,
        record: HumanApprovalRecord,
        now: datetime,
    ) -> ApprovalExpiryReceipt | None:
        receipt = self.load_expiry_receipt(record.node_id)
        if receipt is not None:
            return receipt
        if (
            self.load_cancellation_receipt(record.node_id) is not None
            or record.decision is not None
            or now < record.approval_deadline_at
        ):
            return None
        receipt = ApprovalExpiryReceipt(
            task_id=record.task_id,
            attempt_id=record.attempt_id,
            node_id=record.node_id,
            approval_deadline_at=record.approval_deadline_at,
            expired_at=now,
        )
        _write_json_atomic(self._expiry_receipt_path(record.node_id), receipt.to_payload())
        return receipt

    def load_expiry_receipt(self, node_id: str) -> ApprovalExpiryReceipt | None:
        path = self._expiry_receipt_path(node_id)
        if not path.is_file():
            return None
        receipt = _expiry_receipt_from_payload(json.loads(path.read_text(encoding="utf-8")))
        record = self.load(node_id)
        if record is None:
            raise ApprovalValidationError("approval expiry receipt has no approval record")
        if (
            receipt.task_id != record.task_id
            or receipt.attempt_id != record.attempt_id
            or receipt.node_id != record.node_id
            or receipt.approval_deadline_at != record.approval_deadline_at
        ):
            raise ApprovalValidationError("approval expiry receipt does not match approval record")
        if receipt.expired_at < receipt.approval_deadline_at:
            raise ApprovalValidationError("approval expiry receipt is before approval deadline")
        return receipt

    def claim_cancellation(
        self,
        node_id: str,
        *,
        now: datetime,
    ) -> ApprovalCancellationReceipt | None:
        """Durably prevent a waiting approval from being decided or expired later."""
        self.approval_dir.mkdir(parents=True, exist_ok=True)
        with _approval_lock(self.approval_dir):
            record = self.load(node_id)
            if record is None:
                raise ApprovalNotFound(f"approval node {_clean_node_id(node_id)} was not found")
            resolved_at = _as_utc(now, "now")
            receipt = self.load_cancellation_receipt(record.node_id)
            if receipt is not None:
                return receipt
            if (
                record.decision is not None
                or self.load_expiry_receipt(record.node_id) is not None
            ):
                return None
            if resolved_at >= record.approval_deadline_at:
                self._claim_expiry_locked(record, resolved_at)
                return None
            receipt = ApprovalCancellationReceipt(
                task_id=record.task_id,
                attempt_id=record.attempt_id,
                node_id=record.node_id,
                cancelled_at=resolved_at,
            )
            _write_json_atomic(self._cancellation_receipt_path(record.node_id), receipt.to_payload())
            return receipt

    def load_cancellation_receipt(self, node_id: str) -> ApprovalCancellationReceipt | None:
        path = self._cancellation_receipt_path(node_id)
        if not path.is_file():
            return None
        receipt = _cancellation_receipt_from_payload(json.loads(path.read_text(encoding="utf-8")))
        record = self.load(node_id)
        if record is None:
            raise ApprovalValidationError("approval cancellation receipt has no approval record")
        if (
            receipt.task_id != record.task_id
            or receipt.attempt_id != record.attempt_id
            or receipt.node_id != record.node_id
        ):
            raise ApprovalValidationError(
                "approval cancellation receipt does not match approval record"
            )
        return receipt

    def load(self, node_id: str) -> HumanApprovalRecord | None:
        path = self._approval_path(node_id)
        if not path.is_file():
            return None
        return _record_from_payload(json.loads(path.read_text(encoding="utf-8")))

    def _approval_path(self, node_id: str) -> Path:
        return self.approval_dir / f"{_clean_node_id(node_id)}.json"

    def _expiry_receipt_path(self, node_id: str) -> Path:
        return self.approval_dir / f"{_clean_node_id(node_id)}.receipt.json"

    def _cancellation_receipt_path(self, node_id: str) -> Path:
        return self.approval_dir / f"{_clean_node_id(node_id)}.cancelled.json"


def project_approval(
    record: HumanApprovalRecord,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build replaceable task/event projection data from the immutable artifact."""
    current_time = _as_utc(now, "now") if now is not None else datetime.now(timezone.utc)
    if record.decision is None:
        if current_time >= record.approval_deadline_at:
            return {
                "node_status": "timed_out",
                "approval_status": "expired",
                "delivery_status": "blocked",
                "approval_deadline_at": _timestamp(record.approval_deadline_at),
                "total_execution_timeout_paused": False,
            }
        return {
            "node_status": "waiting_for_input",
            "approval_status": "pending",
            "delivery_status": "pending",
            "approval_deadline_at": _timestamp(record.approval_deadline_at),
            "total_execution_timeout_paused": True,
        }

    return {
        "node_status": record.status,
        "approval_status": "approved" if record.decision.decision == "approve" else "rejected",
        "delivery_status": "pending",
        "approval_deadline_at": _timestamp(record.approval_deadline_at),
        "total_execution_timeout_paused": False,
        "decision": record.decision.to_payload(),
    }


def _record_from_payload(payload: Any) -> HumanApprovalRecord:
    if not isinstance(payload, dict):
        raise ApprovalValidationError("approval payload must be an object")
    decision_payload = payload.get("decision")
    decision = _decision_from_payload(decision_payload) if decision_payload is not None else None
    status = str(payload.get("status") or "")
    if status not in {"waiting_for_input", "approved", "rejected"}:
        raise ApprovalValidationError("unsupported approval status")
    if (status == "waiting_for_input") != (decision is None):
        raise ApprovalValidationError("approval status and decision disagree")
    if decision is not None and status != ("approved" if decision.decision == "approve" else "rejected"):
        raise ApprovalValidationError("approval status and decision disagree")
    record = HumanApprovalRecord(
        approval_version=int(payload.get("approval_version") or 0),
        task_id=_required_text(payload.get("task_id"), "task_id"),
        attempt_id=_required_text(payload.get("attempt_id"), "attempt_id"),
        node_id=_clean_node_id(payload.get("node_id")),
        status=status,
        entered_at=_parse_timestamp(payload.get("entered_at"), "entered_at"),
        total_execution_timeout_at=(
            _parse_timestamp(payload["total_execution_timeout_at"], "total_execution_timeout_at")
            if payload.get("total_execution_timeout_at") is not None
            else None
        ),
        approval_deadline_at=_parse_timestamp(
            payload.get("approval_deadline_at"), "approval_deadline_at"
        ),
        decision=decision,
        input_context=_clean_input_context(payload.get("input_context")),
    )
    if record.approval_version != 1:
        raise ApprovalValidationError("unsupported approval_version")
    if record.approval_deadline_at <= record.entered_at:
        raise ApprovalValidationError("approval_deadline_at must be after entered_at")
    return record


def _decision_from_payload(payload: Any) -> ApprovalDecision:
    if not isinstance(payload, dict):
        raise ApprovalValidationError("decision must be an object")
    return ApprovalDecision(
        decision=_clean_decision(payload.get("decision")),
        actor=_required_text(payload.get("actor"), "actor"),
        reason=_required_text(payload.get("reason"), "reason"),
        decided_at=_parse_timestamp(payload.get("decided_at"), "decided_at"),
    )


def _expiry_receipt_from_payload(payload: Any) -> ApprovalExpiryReceipt:
    if not isinstance(payload, dict) or payload.get("outcome") != "timed_out":
        raise ApprovalValidationError("invalid approval expiry receipt")
    return ApprovalExpiryReceipt(
        task_id=_required_text(payload.get("task_id"), "task_id"),
        attempt_id=_required_text(payload.get("attempt_id"), "attempt_id"),
        node_id=_clean_node_id(payload.get("node_id")),
        approval_deadline_at=_parse_timestamp(
            payload.get("approval_deadline_at"), "approval_deadline_at"
        ),
        expired_at=_parse_timestamp(payload.get("expired_at"), "expired_at"),
    )


def _cancellation_receipt_from_payload(payload: Any) -> ApprovalCancellationReceipt:
    if not isinstance(payload, dict) or payload.get("outcome") != "cancelled":
        raise ApprovalValidationError("invalid approval cancellation receipt")
    return ApprovalCancellationReceipt(
        task_id=_required_text(payload.get("task_id"), "task_id"),
        attempt_id=_required_text(payload.get("attempt_id"), "attempt_id"),
        node_id=_clean_node_id(payload.get("node_id")),
        cancelled_at=_parse_timestamp(payload.get("cancelled_at"), "cancelled_at"),
    )


def _same_waiting_request(
    existing: HumanApprovalRecord,
    proposed: HumanApprovalRecord,
) -> bool:
    return existing == proposed or (
        existing.status == "waiting_for_input"
        and existing.task_id == proposed.task_id
        and existing.attempt_id == proposed.attempt_id
        and existing.node_id == proposed.node_id
        and existing.entered_at == proposed.entered_at
        and existing.total_execution_timeout_at == proposed.total_execution_timeout_at
        and existing.approval_deadline_at == proposed.approval_deadline_at
        and existing.input_context == proposed.input_context
    )


def _clean_input_context(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ApprovalValidationError("input_context must be an object")
    summary = value.get("summary")
    digest = value.get("sha256")
    truncated = value.get("truncated")
    if not isinstance(summary, str) or len(summary.encode("utf-8")) > 4096:
        raise ApprovalValidationError("input_context summary is invalid")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ApprovalValidationError("input_context sha256 is invalid")
    if not isinstance(truncated, bool):
        raise ApprovalValidationError("input_context truncated is invalid")
    return {"summary": summary, "sha256": digest, "truncated": truncated}


def _clean_node_id(node_id: object) -> str:
    value = _required_text(node_id, "node_id")
    if not _NODE_ID_RE.fullmatch(value) or "/" in value or "\\" in value:
        raise ApprovalValidationError("invalid approval node_id")
    return value


def _clean_decision(value: object) -> ApprovalDecisionValue:
    if value not in {"approve", "reject"}:
        raise ApprovalValidationError("decision must be approve or reject")
    return value


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ApprovalValidationError(f"{field_name} is required")
    return text


def _as_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ApprovalValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ApprovalValidationError(f"{field_name} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ApprovalValidationError(f"{field_name} must be an ISO timestamp") from error
    return _as_utc(parsed, field_name)


def _timestamp(value: datetime) -> str:
    return _as_utc(value, "timestamp").isoformat()


def _approval_lock(approval_dir: Path):
    return exclusive_file_lock(approval_dir / ".lock")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    content = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f"{path.name}.tmp-", dir=str(path.parent), text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
