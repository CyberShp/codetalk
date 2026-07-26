"""Provider-neutral Harness contracts used by workflow execution and the cockpit."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Protocol


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


@dataclass(frozen=True)
class HarnessRunRequest:
    """Provider-neutral input frozen by CodeTalk before an Agent is launched."""

    provider: str
    command: list[str]
    cwd: str
    workflow_snapshot: dict[str, Any]
    task_bundle: dict[str, Any]
    mcp_profile: str = ""
    prompt_transport: str = ""
    timeout_seconds: int | None = None
    idle_timeout_seconds: float | None = None
    run_id: str | None = None
    turn_id: str = "turn_1"


@dataclass(frozen=True)
class HarnessRunResult:
    """Stable result shape returned to workflows regardless of provider adapter."""

    session_id: str
    status: str
    exit_code: int | None
    started_at: str
    completed_at: str
    duration_ms: int
    timed_out: bool = False
    error: str = ""
    provider_diagnostics: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)


class ProviderAdapter(Protocol):
    """Provider boundary; adapters never own CodeTalk task or artifact state."""

    def prepare(self, request: HarnessRunRequest) -> Any: ...
    def execute(
        self,
        session_id: str,
        *,
        timeout_sec: int = 0,
        idle_timeout_sec: float | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> Any: ...
    def record_raw_output(self, session_id: str, *, stdout: str, stderr: str = "") -> None: ...
    def collect_artifacts(self, session_id: str) -> list[str]: ...


class LocalCliProviderAdapter:
    """Compatibility adapter for the existing local CLI runner."""

    def __init__(self, artifact_dir: Path) -> None:
        self.artifact_dir = artifact_dir

    def prepare(self, request: HarnessRunRequest) -> Any:
        from app.services.agent_run_harness import AgentRunHarness

        return AgentRunHarness(self.artifact_dir).create_run(
            provider=request.provider,
            command=request.command,
            cwd=request.cwd,
            workflow_snapshot=request.workflow_snapshot,
            task_bundle=request.task_bundle,
            mcp_profile=request.mcp_profile,
            prompt_transport=request.prompt_transport,
            timeout_seconds=request.timeout_seconds,
            idle_timeout_seconds=request.idle_timeout_seconds,
            run_id=request.run_id,
            turn_id=request.turn_id,
        )

    def execute(
        self,
        session_id: str,
        *,
        timeout_sec: int = 0,
        idle_timeout_sec: float | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> Any:
        from app.services.agent_run_harness import AgentRunHarness

        return AgentRunHarness(self.artifact_dir).execute_run(
            session_id,
            timeout_sec=timeout_sec,
            idle_timeout_sec=idle_timeout_sec,
            is_cancelled=is_cancelled,
            event_sink=event_sink,
        )

    def record_raw_output(self, session_id: str, *, stdout: str, stderr: str = "") -> None:
        from app.services.agent_run_harness import AgentRunHarness

        AgentRunHarness(self.artifact_dir).record_raw_output(
            session_id,
            stdout=stdout,
            stderr=stderr,
        )

    def collect_artifacts(self, session_id: str) -> list[str]:
        try:
            import json

            payload = json.loads(
                (self.artifact_dir / "agent_output_contract.json").read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, ValueError):
            return []
        declared = payload.get("required_artifacts") if isinstance(payload, dict) else []
        return [
            str(name)
            for name in declared or []
            if isinstance(name, str) and (self.artifact_dir / name).is_file()
        ]


class AgentHarnessFacade:
    """The workflow-owned entry point for external Agent execution.

    The current local CLI runner remains an adapter behind this facade.  Provider SDK
    adapters can therefore be introduced without changing workflow, cockpit, or
    artifact contracts again.
    """

    def __init__(
        self,
        artifact_dir: str | Path,
        *,
        adapter: ProviderAdapter | None = None,
    ) -> None:
        self.artifact_dir = Path(artifact_dir)
        self._adapter: ProviderAdapter = adapter or LocalCliProviderAdapter(self.artifact_dir)

    def prepare(self, request: HarnessRunRequest) -> Any:
        return self._adapter.prepare(request)

    def execute(
        self,
        session_id: str,
        *,
        timeout_sec: int = 0,
        idle_timeout_sec: float | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> HarnessRunResult:
        self._emit_lifecycle_event(
            event_sink,
            "run_started",
            {"session_id": session_id},
        )
        result = self._adapter.execute(
            session_id,
            timeout_sec=timeout_sec,
            idle_timeout_sec=idle_timeout_sec,
            is_cancelled=is_cancelled,
            event_sink=event_sink,
        )
        public_result = HarnessRunResult(
            session_id=result.run_id,
            status=result.status,
            exit_code=result.exit_code,
            started_at=result.started_at,
            completed_at=result.completed_at,
            duration_ms=result.duration_ms,
            timed_out=result.timed_out,
            error=result.error,
            provider_diagnostics=dict(result.provider_diagnostics),
            artifacts=self.collect_artifacts(session_id),
        )
        terminal_kind = (
            "completed"
            if public_result.status == "completed"
            else "cancelled"
            if public_result.status == "cancelled"
            else "failed"
        )
        self._emit_lifecycle_event(
            event_sink,
            terminal_kind,
            {
                "session_id": public_result.session_id,
                "status": public_result.status,
                "duration_ms": public_result.duration_ms,
                "artifact_count": len(public_result.artifacts),
                "error": public_result.error,
            },
        )
        return public_result

    def record_raw_output(
        self,
        session_id: str,
        *,
        stdout: str,
        stderr: str = "",
    ) -> None:
        """Keep legacy diagnostic capture behind the same workflow-facing boundary."""
        self._adapter.record_raw_output(
            session_id,
            stdout=stdout,
            stderr=stderr,
        )

    def collect_artifacts(self, session_id: str) -> list[str]:
        """Return adapter candidates through the workflow-owned artifact boundary."""
        return self._adapter.collect_artifacts(session_id)

    @staticmethod
    def _emit_lifecycle_event(
        event_sink: Callable[[str, dict[str, Any]], None] | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        if event_sink is None:
            return
        normalized = normalize_provider_event(event_type, payload)
        event_sink(
            event_type,
            {
                **payload,
                "harness_event_kind": normalized.kind,
                "harness_visibility": normalized.visibility,
                "harness_user_message": normalized.user_message,
            },
        )


def normalize_provider_event(event_type: str, payload: dict[str, Any] | None = None) -> HarnessEvent:
    """Map raw provider-shaped output into the stable product event vocabulary."""
    data = dict(payload or {})
    raw = str(event_type or "").strip().lower()
    if raw in {"run_started", "session_created"}:
        kind: HarnessEventKind = "run_started" if raw == "run_started" else "session_created"
        return HarnessEvent(kind, "summary", data, "执行器已启动")
    if raw in {"stage_started", "stage_completed"}:
        kind = "stage_started" if raw == "stage_started" else "stage_completed"
        message = "阶段开始执行" if kind == "stage_started" else "阶段执行完成"
        return HarnessEvent(kind, "summary", data, message)
    lifecycle_events: dict[str, tuple[HarnessEventKind, HarnessVisibility, str]] = {
        "thinking_summary": ("thinking_summary", "summary", "执行器正在归纳当前进展"),
        "tool_started": ("tool_started", "summary", "正在调用工具"),
        "tool_completed": ("tool_completed", "summary", "工具调用完成"),
        "artifact_progress": ("artifact_progress", "summary", "正在生成交付文件"),
        "validation_started": ("validation_started", "summary", "正在核验交付质量"),
        "validation_failed": ("validation_failed", "user", "交付质量核验发现问题"),
        "repair_started": ("repair_started", "summary", "正在定向修复未通过项目"),
        "idle": ("idle", "summary", "执行器正在等待下一项有效工作"),
        "blocked": ("blocked", "user", "执行被必要条件阻断"),
        "cancelled": ("cancelled", "user", "执行已取消"),
    }
    if raw in lifecycle_events:
        kind, visibility, message = lifecycle_events[raw]
        return HarnessEvent(kind, visibility, data, message)
    if raw in {"artifact", "artifact_created"}:
        return HarnessEvent("artifact_created", "user", data, "已生成交付文件")
    if raw == "network_egress_blocked":
        return HarnessEvent("network_egress_blocked", "user", data, "受控出站策略已阻止未批准连接")
    if raw in {"stdout", "activity", "agent_output", "tool_use", "tool_result", "source_read"}:
        kind: HarnessEventKind = "source_read" if raw == "source_read" else "activity"
        return HarnessEvent(kind, "summary", data, str(data.get("text") or "执行器正在处理任务"))
    if raw in {"stderr", "trace", "raw", "diagnostic"}:
        return HarnessEvent("diagnostic", "diagnostic", data)
    if raw in {"completed", "done"}:
        return HarnessEvent("completed", "user", data, "执行已完成")
    if raw in {"failed", "error"}:
        return HarnessEvent("failed", "user", data, "执行失败")
    return HarnessEvent("diagnostic", "diagnostic", {**data, "raw_event_type": raw})
