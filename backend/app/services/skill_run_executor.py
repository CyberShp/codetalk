"""Small Skill invocation executor contract used before provider-specific adapters."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, NoReturn, Protocol


class SkillRunExecutorError(RuntimeError):
    """Raised when an invocation cannot complete under the lifecycle contract."""


class SkillAgentAdapter(Protocol):
    def create(self, invocation: dict[str, Any]) -> dict[str, Any]: ...

    def start(self, session: dict[str, Any]) -> dict[str, Any]: ...

    def poll(self, session: dict[str, Any]) -> dict[str, Any]: ...

    def cancel(self, session: dict[str, Any]) -> dict[str, Any]: ...


CLOWDER_LIFECYCLE_STATUS_MAP = {
    "queued": ("created",),
    "running": ("running", "restarted"),
    "succeeded": ("completed",),
    "failed": ("failed", "session_lost", "timed_out"),
    "canceled": ("cancelled",),
}


_CLOWDER_INVOCATION_STATUS_BY_SKILL_STATUS = {
    skill_status: clowder_status
    for clowder_status, skill_statuses in CLOWDER_LIFECYCLE_STATUS_MAP.items()
    for skill_status in skill_statuses
}


def clowder_invocation_status_for_skill_status(status: str) -> str:
    """Project CodeTalk Skill lifecycle status into the Clowder invocation vocabulary."""

    normalized = str(status or "").strip().lower()
    try:
        return _CLOWDER_INVOCATION_STATUS_BY_SKILL_STATUS[normalized]
    except KeyError as exc:
        raise SkillRunExecutorError(f"unknown skill lifecycle status: {status}") from exc


@dataclass
class SkillAgentLifecycle:
    invocation_id: str
    status: str = "created"
    events: list[dict[str, Any]] = field(default_factory=list)

    def append(self, event: str, **payload: Any) -> None:
        self.events.append({"event": event, "at_epoch": time.time(), **payload})


class SkillRunExecutor:
    def __init__(self, *, adapter: SkillAgentAdapter, timeout_seconds: float = 60) -> None:
        self.adapter = adapter
        self.timeout_seconds = max(0.001, float(timeout_seconds))

    def execute(self, invocation_path: str | Path) -> dict[str, Any]:
        invocation_file = Path(invocation_path)
        invocation = json.loads(invocation_file.read_text(encoding="utf-8"))
        artifact_root = Path(invocation["artifact_root"])
        if not artifact_root.is_absolute():
            artifact_root = invocation_file.parent / artifact_root
        artifact_root.mkdir(parents=True, exist_ok=True)
        lifecycle = SkillAgentLifecycle(invocation_id=str(invocation["invocation_id"]))
        lifecycle.append("create")
        try:
            session = self.adapter.create(invocation)
        except Exception as exc:
            _record_adapter_failure(artifact_root, lifecycle, phase="create", exc=exc)
        lifecycle.append("start", session_id=str(session.get("session_id") or ""))
        try:
            started = self.adapter.start(session)
        except Exception as exc:
            _record_adapter_failure(artifact_root, lifecycle, phase="start", exc=exc)
        session = {**session, **started}
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            if time.monotonic() >= deadline:
                lifecycle.status = "timed_out"
                lifecycle.append("timeout")
                try:
                    self.adapter.cancel(session)
                    lifecycle.append("cancel")
                finally:
                    _write_lifecycle(artifact_root, lifecycle)
                raise SkillRunExecutorError("skill agent timed out")
            try:
                event = self.adapter.poll(session)
            except Exception as exc:
                _record_adapter_failure(artifact_root, lifecycle, phase="poll", exc=exc)
            event_type = str(event.get("event") or event.get("status") or "event")
            lifecycle.append(event_type, **{k: v for k, v in event.items() if k != "event"})
            status = str(event.get("status") or "").lower()
            if status in {"completed", "failed", "cancelled", "session_lost", "restarted"}:
                lifecycle.status = status
                _write_lifecycle(artifact_root, lifecycle)
                if status in {"failed", "session_lost"}:
                    raise SkillRunExecutorError(status)
                return {"status": status, "events": lifecycle.events}
            time.sleep(0.01)


class ScriptedSkillAgentAdapter:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = list(events)
        self.cancelled = False

    def create(self, invocation: dict[str, Any]) -> dict[str, Any]:
        return {"session_id": f"fake-{invocation['invocation_id']}"}

    def start(self, session: dict[str, Any]) -> dict[str, Any]:
        return {"started": True}

    def poll(self, session: dict[str, Any]) -> dict[str, Any]:
        if self.events:
            return self.events.pop(0)
        return {"event": "complete", "status": "completed"}

    def cancel(self, session: dict[str, Any]) -> dict[str, Any]:
        self.cancelled = True
        return {"status": "cancelled"}


def _write_lifecycle(root: Path, lifecycle: SkillAgentLifecycle) -> None:
    path = root / "agent_run_lifecycle.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(asdict(lifecycle), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _record_adapter_failure(
    root: Path,
    lifecycle: SkillAgentLifecycle,
    *,
    phase: str,
    exc: Exception,
) -> NoReturn:
    lifecycle.status = "failed"
    lifecycle.append("failed", phase=phase, error=str(exc), error_type=type(exc).__name__)
    _write_lifecycle(root, lifecycle)
    raise SkillRunExecutorError(str(exc)) from exc
