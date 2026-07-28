"""Recover immutable V3 attempts after a backend process restart."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from app.config import settings
from app.services.checkpoint_projection import rebuild_checkpoint_projection
from app.services.human_approval import ApprovalValidationError, HumanApprovalStore, project_approval
from app.services.workflow_run_status import (
    ARTIFACT_VALIDATION_STATUSES,
    GOVERNANCE_STATUSES,
    derive_delivery_status,
    legacy_delivery_status,
    legacy_quality_status,
)
from app.services.workbench_task_run import validate_run_snapshot_v3
from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore


RecoveryAction = Literal["resume", "waiting_for_input", "timed_out", "cancelled", "failed"]
_RECOVERABLE_STATUSES = frozenset({"queued", "running", "waiting_for_input"})
_PUBLIC_RECOVERY_FAILURE_MESSAGE = "工作流恢复校验失败，请重新运行。"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class V3StartupRecoveryDecision:
    task_run_id: str
    action: RecoveryAction
    recovered_node_ids: tuple[str, ...]
    reason: str = ""


def reconcile_v3_startup_recovery(
    artifact_root: str | Path,
) -> list[V3StartupRecoveryDecision]:
    """Classify V3 attempts before legacy restart reconciliation runs.

    Run snapshots remain authoritative.  A malformed V3 candidate fails closed
    rather than falling through to the legacy interruption path.
    """
    root = Path(artifact_root)
    if not root.exists():
        return []
    store = WorkbenchTaskRunEventStore(root)
    decisions: list[V3StartupRecoveryDecision] = []
    for attempt_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        task_run_id = attempt_dir.name
        payload = _read_json(attempt_dir / "task_run.json")
        if not isinstance(payload, dict):
            continue
        status = _status(payload)
        if status not in _RECOVERABLE_STATUSES or not _is_v3_candidate(attempt_dir, payload):
            continue
        try:
            contract = _load_frozen_v3_contract(
                attempt_dir=attempt_dir,
                task_run_id=task_run_id,
                task_payload=payload,
            )
        except (OSError, ValueError, ApprovalValidationError) as error:
            reason = str(error) or "immutable V3 recovery validation failed"
            _mark_failed(store, task_run_id, reason)
            decisions.append(V3StartupRecoveryDecision(task_run_id, "failed", (), reason))
            continue

        try:
            cancelled_node_ids = _cancelled_human_approval_node_ids(
                attempt_dir=attempt_dir,
                task_run_id=task_run_id,
                task_id=contract["task_id"],
                plan=contract["plan"],
            )
        except (OSError, ValueError, ApprovalValidationError) as error:
            reason = str(error) or "human approval cancellation recovery failed"
            _mark_failed(store, task_run_id, reason)
            decisions.append(V3StartupRecoveryDecision(task_run_id, "failed", (), reason))
            continue
        if cancelled_node_ids:
            _mark_cancelled(store, task_run_id)
            decisions.append(
                V3StartupRecoveryDecision(
                    task_run_id,
                    "cancelled",
                    (),
                    "human_approval_cancelled",
                )
            )
            continue

        try:
            expired_node_ids = _claim_expired_human_approvals(
                attempt_dir=attempt_dir,
                task_run_id=task_run_id,
                task_id=contract["task_id"],
                plan=contract["plan"],
            )
        except (OSError, ValueError, ApprovalValidationError) as error:
            reason = str(error) or "human approval expiry recovery failed"
            _mark_failed(store, task_run_id, reason)
            decisions.append(V3StartupRecoveryDecision(task_run_id, "failed", (), reason))
            continue
        if expired_node_ids:
            _mark_timed_out(store, task_run_id)
            decisions.append(
                V3StartupRecoveryDecision(
                    task_run_id,
                    "timed_out",
                    (),
                    "human_approval_expired",
                )
            )
            continue

        try:
            waiting_for_input = _has_pending_human_approval(
                attempt_dir=attempt_dir,
                task_run_id=task_run_id,
                task_id=contract["task_id"],
                plan=contract["plan"],
            )
        except (OSError, ValueError, ApprovalValidationError) as error:
            reason = str(error) or "human approval waiting recovery failed"
            _mark_failed(store, task_run_id, reason)
            decisions.append(V3StartupRecoveryDecision(task_run_id, "failed", (), reason))
            continue
        if waiting_for_input:
            if not settings.workflow_hitl_enabled:
                reason = "phase6_feature_disabled:human_approval"
                _mark_failed(store, task_run_id, reason)
                decisions.append(
                    V3StartupRecoveryDecision(task_run_id, "failed", (), reason)
                )
                continue
            recovered_node_ids: tuple[str, ...] = ()
            if settings.workflow_checkpoint_reuse_enabled:
                try:
                    recovered_node_ids = rebuild_checkpoint_projection(
                        root,
                        task_run_id,
                    ).completed_node_ids
                except (OSError, ValueError, ApprovalValidationError) as error:
                    reason = str(error) or "checkpoint projection recovery failed"
                    _mark_failed(store, task_run_id, reason)
                    decisions.append(
                        V3StartupRecoveryDecision(task_run_id, "failed", (), reason)
                    )
                    continue
            _mark_waiting(store, task_run_id)
            decisions.append(
                V3StartupRecoveryDecision(
                    task_run_id,
                    "waiting_for_input",
                    recovered_node_ids,
                )
            )
            continue
        if not settings.workflow_checkpoint_reuse_enabled:
            continue
        try:
            projection = rebuild_checkpoint_projection(root, task_run_id)
        except (OSError, ValueError, ApprovalValidationError) as error:
            reason = str(error) or "checkpoint projection recovery failed"
            _mark_failed(store, task_run_id, reason)
            decisions.append(V3StartupRecoveryDecision(task_run_id, "failed", (), reason))
            continue
        decisions.append(
            V3StartupRecoveryDecision(
                task_run_id,
                "resume",
                projection.completed_node_ids,
            )
        )
    return decisions


def _load_frozen_v3_contract(
    *,
    attempt_dir: Path,
    task_run_id: str,
    task_payload: dict[str, Any],
) -> dict[str, Any]:
    errors = validate_run_snapshot_v3(attempt_dir)
    if errors:
        raise ValueError("; ".join(errors))
    snapshot = _read_json(attempt_dir / "run_snapshot_v3.json")
    identity = snapshot.get("identity") if isinstance(snapshot, dict) else None
    components = snapshot.get("components") if isinstance(snapshot, dict) else None
    if not isinstance(identity, dict) or not isinstance(components, dict):
        raise ValueError("V3 snapshot identity or components are missing")
    frozen_task_id = str(identity.get("task_id") or "")
    if str(identity.get("task_run_id") or "") != task_run_id:
        raise ValueError("V3 snapshot task_run_id does not match its Attempt directory")
    if frozen_task_id != str(task_payload.get("task_id") or ""):
        raise ValueError("V3 snapshot task_id does not match task projection")
    definition = _component_json(attempt_dir, components, "v3_runtime_contract")
    plan = _component_json(attempt_dir, components, "execution_plan")
    if (
        not isinstance(definition, dict)
        or not isinstance(plan, dict)
        or definition.get("compiled_contract_version") != 3
        or plan.get("compiled_contract_version") != 3
    ):
        raise ValueError("V3 frozen definition and execution plan must both use contract version 3")
    nodes = plan.get("nodes")
    topological_order = plan.get("topological_order")
    if not isinstance(nodes, list) or not isinstance(topological_order, list):
        raise ValueError("V3 frozen execution plan is malformed")
    declared_node_ids = {
        str(node.get("node_id") or "")
        for node in nodes
        if isinstance(node, dict) and str(node.get("node_id") or "")
    }
    if not topological_order or any(
        str(node_id or "") not in declared_node_ids for node_id in topological_order
    ):
        raise ValueError("V3 frozen execution plan has an invalid topological order")
    return {"task_id": frozen_task_id or task_run_id, "plan": plan}


def _component_json(attempt_dir: Path, components: dict[str, Any], component_id: str) -> Any:
    descriptor = components.get(component_id)
    if not isinstance(descriptor, dict):
        raise ValueError(f"V3 snapshot missing required component: {component_id}")
    relative_path = str(descriptor.get("path") or "")
    if not relative_path or Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
        raise ValueError(f"V3 snapshot component path is unsafe: {component_id}")
    payload = _read_json(attempt_dir / relative_path)
    if not isinstance(payload, dict):
        raise ValueError(f"V3 snapshot component is not a JSON object: {component_id}")
    return payload


def _has_pending_human_approval(
    *,
    attempt_dir: Path,
    task_run_id: str,
    task_id: str,
    plan: dict[str, Any],
) -> bool:
    store = HumanApprovalStore(attempt_dir)
    for node in plan.get("nodes") or []:
        if not isinstance(node, dict) or str(node.get("kind") or "") != "human_approval":
            continue
        node_id = str(node.get("node_id") or "")
        try:
            record = store.load(node_id)
        except (OSError, ValueError, ApprovalValidationError) as error:
            raise ApprovalValidationError(f"approval {node_id} is invalid: {error}") from error
        if record is None:
            continue
        if record.attempt_id != task_run_id or record.task_id != (task_id or task_run_id):
            raise ApprovalValidationError(f"approval {node_id} identity does not match frozen Attempt")
        if (
            store.load_expiry_receipt(node_id) is None
            and store.load_cancellation_receipt(node_id) is None
            and project_approval(record).get("node_status") == "waiting_for_input"
        ):
            return True
    return False


def _claim_expired_human_approvals(
    *,
    attempt_dir: Path,
    task_run_id: str,
    task_id: str,
    plan: dict[str, Any],
) -> tuple[str, ...]:
    store = HumanApprovalStore(attempt_dir)
    expired: list[str] = []
    now = datetime.now(timezone.utc)
    for node in plan.get("nodes") or []:
        if not isinstance(node, dict) or str(node.get("kind") or "") != "human_approval":
            continue
        node_id = str(node.get("node_id") or "")
        try:
            record = store.load(node_id)
        except (OSError, ValueError, ApprovalValidationError) as error:
            raise ApprovalValidationError(f"approval {node_id} is invalid: {error}") from error
        if record is None:
            continue
        if record.attempt_id != task_run_id or record.task_id != (task_id or task_run_id):
            raise ApprovalValidationError(f"approval {node_id} identity does not match frozen Attempt")
        try:
            receipt = store.claim_expiry(node_id, now=now)
        except (OSError, ValueError, ApprovalValidationError) as error:
            raise ApprovalValidationError(f"approval {node_id} expiry claim is invalid: {error}") from error
        if receipt is not None:
            expired.append(node_id)
    return tuple(expired)


def _cancelled_human_approval_node_ids(
    *,
    attempt_dir: Path,
    task_run_id: str,
    task_id: str,
    plan: dict[str, Any],
) -> tuple[str, ...]:
    store = HumanApprovalStore(attempt_dir)
    cancelled: list[str] = []
    for node in plan.get("nodes") or []:
        if not isinstance(node, dict) or str(node.get("kind") or "") != "human_approval":
            continue
        node_id = str(node.get("node_id") or "")
        try:
            record = store.load(node_id)
            receipt = store.load_cancellation_receipt(node_id)
        except (OSError, ValueError, ApprovalValidationError) as error:
            raise ApprovalValidationError(f"approval {node_id} cancellation receipt is invalid: {error}") from error
        if record is None or receipt is None:
            continue
        if (
            record.attempt_id != task_run_id
            or record.task_id != (task_id or task_run_id)
            or receipt.attempt_id != task_run_id
            or receipt.task_id != (task_id or task_run_id)
            or receipt.node_id != node_id
        ):
            raise ApprovalValidationError(f"approval {node_id} identity does not match frozen Attempt")
        cancelled.append(node_id)
    return tuple(cancelled)


def _mark_waiting(store: WorkbenchTaskRunEventStore, task_run_id: str) -> None:
    payload = store.mark_status(task_run_id, "waiting_for_input")
    artifact_status = str(payload.get("artifact_validation_status") or "")
    governance_status = str(payload.get("governance_status") or "")
    if artifact_status not in ARTIFACT_VALIDATION_STATUSES:
        artifact_status = "not_started"
    if governance_status not in GOVERNANCE_STATUSES:
        governance_status = "not_requested"
    delivery_status = derive_delivery_status(
        execution_status="waiting_for_input",
        artifact_validation_status=artifact_status,
        governance_status=governance_status,
    )
    store.mark_v3_outcomes(
        task_run_id,
        execution_status="waiting_for_input",
        artifact_validation_status=artifact_status,
        governance_status=governance_status,
        delivery_status=delivery_status,
        quality_status=legacy_quality_status(
            execution_status="waiting_for_input",
            artifact_validation_status=artifact_status,
            governance_status=governance_status,
        ),
        legacy_delivery_status=legacy_delivery_status(delivery_status=delivery_status),
    )
    store.append_once(
        task_run_id,
        "node_waiting",
        {"status": "waiting_for_input", "source": "startup_recovery"},
        deduplication_key="v3-startup-recovery:waiting",
    )


def _mark_timed_out(store: WorkbenchTaskRunEventStore, task_run_id: str) -> None:
    transitioned, payload = store.mark_status_unless(
        task_run_id,
        "timed_out",
        blocked_statuses={
            "prepared", "queued", "running", "cancelled", "completed", "failed",
            "timed_out", "interrupted", "partial", "quality_blocked",
        },
        completed_at=datetime.now(timezone.utc).isoformat(),
        error="human_approval_expired",
    )
    if not transitioned:
        return
    _mark_terminal_outcomes(store, task_run_id, "timed_out", payload)
    store.append_once(
        task_run_id,
        "human_approval_timed_out",
        {
            "status": "timed_out",
            "reason": "approval_deadline_expired",
            "user_message": "人工审批已超时，本次工作流运行已结束。",
        },
        deduplication_key="human-approval:timed-out",
    )


def _mark_cancelled(store: WorkbenchTaskRunEventStore, task_run_id: str) -> None:
    transitioned, payload = store.mark_status_unless(
        task_run_id,
        "cancelled",
        blocked_statuses={
            "prepared", "queued", "running", "cancelled", "completed", "failed",
            "timed_out", "interrupted", "partial", "quality_blocked",
        },
        completed_at=datetime.now(timezone.utc).isoformat(),
    )
    if not transitioned:
        return
    _mark_terminal_outcomes(store, task_run_id, "cancelled", payload)
    store.append_once(
        task_run_id,
        "cancelled",
        {"status": "cancelled", "source": "startup_recovery"},
        deduplication_key="v3-startup-recovery:human-approval-cancelled",
    )


def _mark_terminal_outcomes(
    store: WorkbenchTaskRunEventStore,
    task_run_id: str,
    execution_status: Literal["timed_out", "cancelled"],
    payload: dict[str, Any],
) -> None:
    artifact_status = str(payload.get("artifact_validation_status") or "")
    governance_status = str(payload.get("governance_status") or "")
    if artifact_status not in ARTIFACT_VALIDATION_STATUSES:
        artifact_status = "not_started"
    if governance_status not in GOVERNANCE_STATUSES:
        governance_status = "not_requested"
    delivery_status = derive_delivery_status(
        execution_status=execution_status,
        artifact_validation_status=artifact_status,
        governance_status=governance_status,
    )
    store.mark_v3_outcomes(
        task_run_id,
        execution_status=execution_status,
        artifact_validation_status=artifact_status,
        governance_status=governance_status,
        delivery_status=delivery_status,
        quality_status=legacy_quality_status(
            execution_status=execution_status,
            artifact_validation_status=artifact_status,
            governance_status=governance_status,
        ),
        legacy_delivery_status=legacy_delivery_status(delivery_status=delivery_status),
    )


def _mark_failed(store: WorkbenchTaskRunEventStore, task_run_id: str, reason: str) -> None:
    logger.warning("V3 startup recovery failed for task run %s: %s", task_run_id, reason)
    store.mark_status(
        task_run_id,
        "failed",
        completed_at=datetime.now(timezone.utc).isoformat(),
        error=_PUBLIC_RECOVERY_FAILURE_MESSAGE,
    )
    store.mark_v3_outcomes(
        task_run_id,
        execution_status="failed",
        artifact_validation_status="not_started",
        governance_status="not_requested",
        delivery_status="blocked",
        quality_status="blocked",
        legacy_delivery_status="none",
    )
    store.append_once(
        task_run_id,
        "v3_startup_recovery_failed",
        {"status": "failed", "user_message": _PUBLIC_RECOVERY_FAILURE_MESSAGE},
        deduplication_key="v3-startup-recovery:failed",
    )


def _is_v3_candidate(attempt_dir: Path, task_payload: dict[str, Any]) -> bool:
    snapshot = _read_json(attempt_dir / "run_snapshot_v3.json")
    components = snapshot.get("components") if isinstance(snapshot, dict) else None
    if isinstance(components, dict) and "v3_runtime_contract" in components:
        return True
    if (attempt_dir / "compiled_definition.json").exists():
        # A partial write or tampered JSON still identifies this as an attempted
        # V3 run. Treating it as legacy would silently discard recoverability.
        return True
    for candidate in (
        task_payload.get("workflow_snapshot"),
        task_payload.get("task_bundle"),
        _read_json(attempt_dir / "compiled_definition.json"),
        _read_json(attempt_dir / "compiled_plan.json"),
    ):
        if isinstance(candidate, dict) and candidate.get("compiled_contract_version") == 3:
            return True
        if isinstance(candidate, dict):
            for key in ("compiled_definition", "compiled_plan"):
                nested = candidate.get(key)
                if isinstance(nested, dict) and nested.get("compiled_contract_version") == 3:
                    return True
    return False


def _status(payload: dict[str, Any]) -> str:
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    return str(payload.get("status") or runtime.get("status") or "")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
