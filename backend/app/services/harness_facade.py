"""Provider-neutral Harness contracts used by workflow execution and the cockpit."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


HarnessEventKind = Literal[
    "run_started", "session_created", "stage_started", "activity", "thinking_summary",
    "tool_started", "tool_completed", "source_read", "artifact_progress", "artifact_created",
    "validation_started", "validation_failed", "repair_started", "stage_completed", "idle",
    "blocked", "failed", "cancelled", "completed", "network_egress_blocked", "diagnostic",
]
HarnessVisibility = Literal["user", "summary", "diagnostic"]


@dataclass(frozen=True)
class HarnessEvent:
    kind: HarnessEventKind
    visibility: HarnessVisibility
    payload: dict[str, Any] = field(default_factory=dict)
    user_message: str = ""


class ProviderAdapter(Protocol):
    """The only provider-facing surface allowed to leak into the durable runtime."""

    def probe(self, request: dict[str, Any]) -> dict[str, Any]: ...
    def start(self, request: dict[str, Any]) -> str: ...
    def cancel(self, session_id: str) -> dict[str, Any]: ...
    def collect_artifacts(self, session_id: str) -> list[dict[str, Any]]: ...


def normalize_provider_event(event_type: str, payload: dict[str, Any] | None = None) -> HarnessEvent:
    """Map raw provider-shaped output into the stable product event vocabulary."""
    data = dict(payload or {})
    raw = str(event_type or "").strip().lower()
    if raw in {"artifact", "artifact_created"}:
        return HarnessEvent("artifact_created", "user", data, "已生成交付文件")
    if raw == "network_egress_blocked":
        return HarnessEvent("network_egress_blocked", "user", data, "内网策略已阻止公网连接")
    if raw in {"stdout", "activity", "tool_use", "tool_result", "source_read"}:
        kind: HarnessEventKind = "source_read" if raw == "source_read" else "activity"
        return HarnessEvent(kind, "summary", data, str(data.get("text") or "执行器正在处理任务"))
    if raw in {"stderr", "trace", "raw", "diagnostic"}:
        return HarnessEvent("diagnostic", "diagnostic", data)
    if raw in {"completed", "done"}:
        return HarnessEvent("completed", "user", data, "执行已完成")
    if raw in {"failed", "error"}:
        return HarnessEvent("failed", "user", data, "执行失败")
    return HarnessEvent("diagnostic", "diagnostic", {**data, "raw_event_type": raw})
