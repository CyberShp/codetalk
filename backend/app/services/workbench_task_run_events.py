"""Persistent events for Agent Workbench task-run execution."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LOCK = threading.RLock()


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
            event = {
                "event_id": self._next_event_id(events_path),
                "task_run_id": task_run_id,
                "event_type": str(event_type),
                "payload": dict(payload or {}),
                "created_at": _now(),
            }
            event = _with_public_event_metadata(event)
            events_path.parent.mkdir(parents=True, exist_ok=True)
            with events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            return event

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
                    items.append(_with_public_event_metadata(event))
            return items[: max(1, int(limit))]

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

    def _events_path(self, task_run_id: str) -> Path:
        return self.artifact_root / _safe_segment(task_run_id) / "task_run_events.jsonl"

    def _task_path(self, task_run_id: str) -> Path:
        return self.artifact_root / _safe_segment(task_run_id) / "task_run.json"

    @staticmethod
    def _next_event_id(events_path: Path) -> int:
        if not events_path.exists():
            return 1
        count = 0
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                count += 1
        return count + 1


def reconcile_interrupted_task_runs(artifact_root: str | Path) -> dict[str, Any]:
    root = Path(artifact_root)
    store = WorkbenchTaskRunEventStore(root)
    reconciled: list[dict[str, str]] = []
    if not root.exists():
        return {"status": "ok", "interrupted_count": 0, "task_runs": []}
    for task_dir in root.iterdir():
        if not task_dir.is_dir():
            continue
        task_run_id = task_dir.name
        payload = _read_json(task_dir / "task_run.json")
        if not isinstance(payload, dict):
            continue
        status = str(payload.get("status") or (payload.get("runtime") or {}).get("status") or "")
        if status not in {"queued", "running"}:
            continue
        try:
            store.mark_status(
                task_run_id,
                "interrupted",
                completed_at=_now(),
                error="service restarted before task run completed",
            )
            store.append(
                task_run_id,
                "step_failed",
                {
                    "status": "interrupted",
                    "kind": "service_restart_interrupted",
                    "user_message": "后端服务重启，本次工作流运行已中断，请重新运行。",
                    "technical_diagnostics": {
                        "previous_status": status,
                    },
                },
            )
            reconciled.append({"task_run_id": task_run_id, "previous_status": status})
        except KeyError:
            continue
    return {
        "status": "ok",
        "interrupted_count": len(reconciled),
        "task_runs": reconciled,
    }


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _safe_segment(value: str) -> str:
    text = str(value or "").strip()
    if not text or "/" in text or "\\" in text or ".." in text:
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


def _public_event_kind(event: dict[str, Any]) -> str:
    event_type = str(event.get("event_type") or "").strip().lower()
    if event_type in {
        "queued", "running", "step_started", "step_completed", "cancelled", "interrupted",
        "node_queued", "node_started", "node_progress", "node_completed", "node_blocked",
        "quality_started", "quality_completed",
    }:
        return "status"
    if event_type in {"completed", "done", "run_completed"}:
        return "done"
    if event_type in {"artifact_created", "artifact", "artifact_progress"}:
        return "artifact"
    if event_type == "agent_output":
        return "output"
    if event_type in {"step_failed", "node_failed", "failed", "error"}:
        return "error"
    if event_type in {"thinking", "reasoning", "diagnostic", "trace", "tool_use", "tool_result"}:
        return event_type
    return "diagnostic"
