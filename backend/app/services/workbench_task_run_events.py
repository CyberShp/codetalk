"""Persistent events for Agent Workbench task-run execution."""

from __future__ import annotations

import json
import os
import re
import threading
import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.external_agent_discovery import redact_agent_diagnostic_text
from app.services.interprocess_file_lock import exclusive_file_lock
from app.services.workflow_run_status import validate_status_axes


_LOCK = threading.RLock()
SAFE_RUNTIME_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkbenchTaskRunEventStore:
    """Append-only per-task event log backed by the task-run artifact directory."""

    def __init__(self, artifact_root: str | Path) -> None:
        self.artifact_root = Path(artifact_root)

    def append(
        self,
        task_run_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with _LOCK:
            events_path = self._events_path(task_run_id)
            with _file_lock(self._lock_path(task_run_id, "events")):
                return self._append_locked(task_run_id, event_type, payload)

    def append_once(
        self,
        task_run_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        deduplication_key: str,
    ) -> tuple[dict[str, Any], bool]:
        """Append one projection event per durable deduplication key.

        The key is persisted inside the public event payload so a later process
        can recover the same event without introducing a separate event index.
        """
        clean_key = str(deduplication_key or "").strip()
        if not clean_key:
            raise ValueError("deduplication_key is required")
        clean_payload = dict(payload or {})
        supplied_key = clean_payload.get("deduplication_key")
        if supplied_key not in (None, clean_key):
            raise ValueError("payload deduplication_key must match the argument")
        clean_payload["deduplication_key"] = clean_key
        with _LOCK:
            events_path = self._events_path(task_run_id)
            with _file_lock(self._lock_path(task_run_id, "events")):
                existing = self._find_by_deduplication_key(events_path, clean_key)
                if existing is not None:
                    return _public_event(existing), False
                return self._append_locked(task_run_id, event_type, clean_payload), True

    def list_after(
        self,
        task_run_id: str,
        *,
        after_id: int = 0,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with _LOCK:
            events_path = self._events_path(task_run_id)
            if not events_path.exists():
                return []
            items: list[dict[str, Any]] = []
            for line in events_path.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                try:
                    event_id = int(event.get("event_id") or 0)
                except (TypeError, ValueError):
                    continue
                if event_id > after_id:
                    items.append(_public_event(event))
            return items[: max(1, int(limit))]

    def list_before(
        self,
        task_run_id: str,
        *,
        before_id: int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return the newest page strictly before an event id, oldest first."""

        with _LOCK:
            events_path = self._events_path(task_run_id)
            if not events_path.exists():
                return []
            items: list[dict[str, Any]] = []
            boundary = int(before_id) if before_id is not None else None
            for line in events_path.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                    event_id = int(event.get("event_id") or 0)
                except (AttributeError, json.JSONDecodeError, TypeError, ValueError):
                    continue
                if boundary is None or event_id < boundary:
                    items.append(_public_event(event))
            return items[-max(1, int(limit)):]

    def latest_event_id(self, task_run_id: str) -> int:
        """Return the global event tail independently of the requested page."""
        with _LOCK:
            events_path = self._events_path(task_run_id)
            if not events_path.exists():
                return 0
            latest = 0
            for line in events_path.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                    event_id = int(event.get("event_id") or 0)
                except (AttributeError, json.JSONDecodeError, TypeError, ValueError):
                    continue
                latest = max(latest, event_id)
            return latest

    def mark_status(self, task_run_id: str, status: str, **extra: Any) -> dict[str, Any]:
        with _LOCK:
            task_path = self._task_path(task_run_id)
            with _file_lock(self._lock_path(task_run_id, "task")):
                payload = _read_json(task_path)
                if not isinstance(payload, dict):
                    raise KeyError(task_run_id)
                runtime = dict(payload.get("runtime") or {})
                runtime.update({
                    "status": str(status),
                    "updated_at": _now(),
                    **extra,
                })
                payload["status"] = str(status)
                payload["execution_status"] = str(status)
                if extra.get("started_at"):
                    payload["started_at"] = str(extra["started_at"])
                if extra.get("completed_at"):
                    payload["completed_at"] = str(extra["completed_at"])
                payload["runtime"] = runtime
                _write_json(task_path, payload)
                return payload

    def mark_status_unless(
        self,
        task_run_id: str,
        status: str,
        *,
        blocked_statuses: set[str],
        **extra: Any,
    ) -> tuple[bool, dict[str, Any]]:
        """Atomically avoid replacing an authoritative terminal status."""
        with _LOCK:
            task_path = self._task_path(task_run_id)
            with _file_lock(self._lock_path(task_run_id, "task")):
                payload = _read_json(task_path)
                if not isinstance(payload, dict):
                    raise KeyError(task_run_id)
                current = str(payload.get("status") or "").strip()
                if not current:
                    runtime = payload.get("runtime")
                    if isinstance(runtime, dict):
                        current = str(runtime.get("status") or "").strip()
                if current in blocked_statuses:
                    return False, payload
                runtime = dict(payload.get("runtime") or {})
                runtime.update({
                    "status": str(status),
                    "updated_at": _now(),
                    **extra,
                })
                payload["status"] = str(status)
                payload["execution_status"] = str(status)
                if extra.get("started_at"):
                    payload["started_at"] = str(extra["started_at"])
                if extra.get("completed_at"):
                    payload["completed_at"] = str(extra["completed_at"])
                payload["runtime"] = runtime
                _write_json(task_path, payload)
                return True, payload

    def current_status(self, task_run_id: str) -> str:
        payload = _read_json(self._task_path(task_run_id))
        if isinstance(payload, dict):
            status = str(payload.get("status") or "").strip()
            if status:
                return status
            runtime = payload.get("runtime")
            if isinstance(runtime, dict):
                status = str(runtime.get("status") or "").strip()
                if status:
                    return status
        return "prepared"

    @staticmethod
    def _public_status_from_v3_execution_status(execution_status: str) -> str:
        status = str(execution_status or "").strip()
        if status == "completed":
            return "completed"
        if status == "waiting_for_input":
            return "waiting_for_input"
        if status == "cancelled":
            return "cancelled"
        if status == "timed_out":
            return "timed_out"
        if status in {"running", "queued", "prepared"}:
            return status
        return "failed"

    def mark_outcomes(
        self,
        task_run_id: str,
        *,
        quality_status: str,
        delivery_status: str,
    ) -> dict[str, Any]:
        if quality_status not in {"not_checked", "pending", "passed", "warning", "blocked"}:
            raise ValueError(f"invalid quality status: {quality_status}")
        if delivery_status not in {"none", "partial", "complete"}:
            raise ValueError(f"invalid delivery status: {delivery_status}")
        with _LOCK:
            task_path = self._task_path(task_run_id)
            with _file_lock(self._lock_path(task_run_id, "task")):
                payload = _read_json(task_path)
                if not isinstance(payload, dict):
                    raise KeyError(task_run_id)
                payload["quality_status"] = quality_status
                payload["delivery_status"] = delivery_status
                _write_json(task_path, payload)
                return payload

    def mark_v3_outcomes(
        self,
        task_run_id: str,
        *,
        execution_status: str,
        artifact_validation_status: str,
        governance_status: str,
        delivery_status: str,
        quality_status: str,
        legacy_delivery_status: str,
    ) -> dict[str, Any]:
        """Persist V3's four axes without changing the legacy caller contract.

        ``delivery_status`` is the V3 public axis.  The old three-value value
        survives under ``legacy_delivery_status`` for clients that have not yet
        adopted four-axis rendering.
        """
        validate_status_axes(
            execution_status=execution_status,
            artifact_validation_status=artifact_validation_status,
            governance_status=governance_status,
            delivery_status=delivery_status,
        )
        if quality_status not in {"not_checked", "pending", "passed", "warning", "blocked"}:
            raise ValueError(f"invalid quality status: {quality_status}")
        if legacy_delivery_status not in {"none", "partial", "complete"}:
            raise ValueError(f"invalid legacy delivery status: {legacy_delivery_status}")
        with _LOCK:
            task_path = self._task_path(task_run_id)
            with _file_lock(self._lock_path(task_run_id, "task")):
                payload = _read_json(task_path)
                if not isinstance(payload, dict):
                    raise KeyError(task_run_id)
                public_status = self._public_status_from_v3_execution_status(
                    execution_status
                )
                payload.update({
                    "status": public_status,
                    "execution_status": execution_status,
                    "artifact_validation_status": artifact_validation_status,
                    "governance_status": governance_status,
                    "delivery_status": delivery_status,
                    "legacy_delivery_status": legacy_delivery_status,
                    "quality_status": quality_status,
                })
                runtime = dict(payload.get("runtime") or {})
                runtime.update({
                    "status": public_status,
                    "execution_status": execution_status,
                    "artifact_validation_status": artifact_validation_status,
                    "governance_status": governance_status,
                    "delivery_status": delivery_status,
                    "legacy_delivery_status": legacy_delivery_status,
                    "quality_status": quality_status,
                    "updated_at": _now(),
                })
                if execution_status == "completed":
                    runtime.pop("error", None)
                payload["runtime"] = runtime
                _write_json(task_path, payload)
                return payload

    def replace_checkpoint_projection(
        self,
        task_run_id: str,
        projection: dict[str, Any],
    ) -> bool:
        """Atomically replace only the checkpoint-derived task projection."""
        with _LOCK:
            task_path = self._task_path(task_run_id)
            with _file_lock(self._lock_path(task_run_id, "task")):
                payload = _read_json(task_path)
                if not isinstance(payload, dict):
                    raise KeyError(task_run_id)
                if payload.get("checkpoint_projection") == projection:
                    return False
                payload["checkpoint_projection"] = projection
                _write_json(task_path, payload)
                return True

    def _events_path(self, task_run_id: str) -> Path:
        return self.artifact_root / _safe_segment(task_run_id) / "task_run_events.jsonl"

    def _task_path(self, task_run_id: str) -> Path:
        return self.artifact_root / _safe_segment(task_run_id) / "task_run.json"

    def _lock_path(self, task_run_id: str, kind: str) -> Path:
        """Keep durable coordination files outside the Attempt artifact root."""
        clean_id = _safe_segment(task_run_id)
        digest = hashlib.sha256(
            f"{clean_id}:{kind}".encode("utf-8")
        ).hexdigest()
        return self.artifact_root / ".locks" / f"{digest}.lock"

    def _append_locked(
        self,
        task_run_id: str,
        event_type: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        events_path = self._events_path(task_run_id)
        event = {
            "event_id": self._next_event_id(events_path),
            "task_run_id": task_run_id,
            "event_type": str(event_type),
            "payload": _redact_public_payload(dict(payload or {})),
            "created_at": _now(),
        }
        event = _with_public_event_metadata(event)
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event

    @staticmethod
    def _find_by_deduplication_key(
        events_path: Path,
        deduplication_key: str,
    ) -> dict[str, Any] | None:
        if not events_path.exists():
            return None
        for line in events_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = event.get("payload") if isinstance(event, dict) else None
            if isinstance(payload, dict) and payload.get("deduplication_key") == deduplication_key:
                return event
        return None

    @staticmethod
    def _next_event_id(events_path: Path) -> int:
        if not events_path.exists():
            return 1
        count = 0
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                count += 1
        return count + 1


def reconcile_interrupted_task_runs(
    artifact_root: str | Path,
    *,
    exclude_task_run_ids: set[str] | None = None,
) -> dict[str, Any]:
    root = Path(artifact_root)
    store = WorkbenchTaskRunEventStore(root)
    excluded = {str(value) for value in (exclude_task_run_ids or set())}
    reconciled: list[dict[str, str]] = []
    if not root.exists():
        return {"status": "ok", "interrupted_count": 0, "task_runs": []}
    for task_dir in root.iterdir():
        if not task_dir.is_dir():
            continue
        task_run_id = task_dir.name
        if task_run_id in excluded:
            continue
        payload = _read_json(task_dir / "task_run.json")
        if not isinstance(payload, dict):
            continue
        status = str(payload.get("status") or (payload.get("runtime") or {}).get("status") or "")
        if status not in {"queued", "running"}:
            continue
        try:
            active_step_id = _active_step_id(store.list_after(task_run_id, limit=10_000))
            store.mark_status(
                task_run_id,
                "interrupted",
                completed_at=_now(),
                error="service restarted before task run completed",
            )
            interruption_payload: dict[str, Any] = {
                "status": "interrupted",
                "kind": "service_restart_interrupted",
                "user_message": "后端服务重启，本次工作流运行已中断，请重新运行。",
                "technical_diagnostics": {
                    "previous_status": status,
                },
            }
            if active_step_id:
                interruption_payload["step_id"] = active_step_id
            store.append(
                task_run_id,
                "step_failed",
                interruption_payload,
            )
            reconciled.append({"task_run_id": task_run_id, "previous_status": status})
        except KeyError:
            continue
    return {
        "status": "ok",
        "interrupted_count": len(reconciled),
        "task_runs": reconciled,
    }


def _active_step_id(events: list[dict[str, Any]]) -> str:
    """Recover the active workflow step without trusting process-local state."""
    active_step_id = ""
    for event in events:
        event_type = str(event.get("event_type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        step_id = str(payload.get("step_id") or payload.get("node_id") or "").strip()
        if event_type in {"step_started", "node_started"} and step_id:
            active_step_id = step_id
        elif event_type in {"step_completed", "step_failed", "step_cancelled", "node_completed", "node_failed"}:
            if step_id and step_id == active_step_id:
                active_step_id = ""
    return active_step_id


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _redact_public_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact_public_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_public_payload(item) for item in value]
    if isinstance(value, str):
        return redact_agent_diagnostic_text(value)
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


@contextmanager
def _file_lock(lock_path: Path):
    with exclusive_file_lock(lock_path):
        yield


def _safe_segment(value: str) -> str:
    text = str(value or "").strip()
    if not text or text in {".", ".."} or ".." in text or not SAFE_RUNTIME_ID_RE.fullmatch(text):
        raise KeyError(value)
    return text


def _with_public_event_metadata(event: dict[str, Any]) -> dict[str, Any]:
    next_event = dict(event)
    event_id = next_event.get("event_id")
    seq = next_event.get("seq")
    if isinstance(seq, int) and seq > 0:
        next_event["seq"] = seq
    elif isinstance(event_id, int):
        next_event["seq"] = event_id
    else:
        next_event["seq"] = 0
    next_event.setdefault("event_kind", _public_event_kind(next_event))
    return next_event


def _public_event(event: dict[str, Any]) -> dict[str, Any]:
    redacted = _redact_public_payload(event)
    return _with_public_event_metadata(redacted)


def _public_event_kind(event: dict[str, Any]) -> str:
    event_type = str(event.get("event_type") or "").strip().lower()
    if event_type in {
        "queued", "running", "step_started", "step_completed", "cancelled", "interrupted",
        "node_queued", "node_started", "node_progress", "node_completed", "node_blocked",
        "node_reused",
        "quality_started", "quality_completed",
    }:
        return "status"
    if event_type in {"completed", "done", "run_completed"}:
        return "done"
    if event_type in {"artifact_created", "artifact", "artifact_progress"}:
        return "artifact"
    if event_type == "agent_output":
        return "output"
    if event_type in {"step_failed", "node_failed", "failed", "error", "provider_readiness_blocked"}:
        return "error"
    if event_type in {"thinking", "reasoning", "diagnostic", "trace", "tool_use", "tool_result"}:
        return event_type
    return "diagnostic"
