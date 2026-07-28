"""Domain-neutral adapter for CodeTalk's configured built-in model."""

from __future__ import annotations

import asyncio
import inspect
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.harness_facade import HarnessRunRequest, HarnessRunResult
from app.services.provider_adapters.contracts import (
    ArtifactCandidate,
    CancelResult,
    ProviderCapabilities,
    ProviderResumeToken,
    ProviderSession,
    ProviderUnsupported,
)


EventSink = Callable[[str, dict[str, Any]], None]
BUILTIN_MODEL_CAPABILITIES = ProviderCapabilities(
    # ``complete()`` returns one terminal response. Activity events emitted
    # afterwards do not make the provider operation a streaming contract.
    streaming=False,
    tool_call=False,
    session_resume=False,
    structured_output=False,
    mcp=False,
    skills=False,
    # The production callable is synchronous and cannot be force-stopped.
    # Timeout handling can reject late results, but that is not provider
    # cancellation and must not satisfy workflow capability requirements.
    cancellation=False,
)


class _BuiltinRunResult(HarnessRunResult):
    """Expose the legacy run_id alias consumed by the current facade."""

    @property
    def run_id(self) -> str:
        return self.session_id


@dataclass
class _SessionState:
    request: HarnessRunRequest
    staging_dir: Path
    cancelled: threading.Event = field(default_factory=threading.Event)
    candidates: list[ArtifactCandidate] = field(default_factory=list)
    accepting_results: bool = True
    terminal: bool = False


class BuiltinModelAdapter:
    """Wrap an injected model callable or client factory behind ProviderAdapter.

    The adapter owns no workflow or Task state. Artifact paths reported by the
    model are candidates only; ``AgentHarnessFacade`` remains the authority that
    narrows them to declared, local files.
    """

    def __init__(
        self,
        artifact_dir: str | Path,
        *,
        execute_callable: Callable[..., Any] | None = None,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        if execute_callable is None and client_factory is None:
            raise ValueError("BuiltinModelAdapter requires a callable or client factory")
        self.artifact_dir = Path(artifact_dir)
        self.execute_callable = execute_callable
        self.client_factory = client_factory
        self._sessions: dict[str, _SessionState] = {}
        self._lock = threading.Lock()

    def capabilities(self) -> ProviderCapabilities:
        return BUILTIN_MODEL_CAPABILITIES

    def prepare(self, request: HarnessRunRequest) -> ProviderSession:
        session_id = str(request.run_id or uuid.uuid4())
        staging_dir = (
            self.artifact_dir
            / ".builtin-model-staging"
            / f"{uuid.uuid4().hex}"
        )
        session = ProviderSession(
            session_id=session_id,
            provider=str(request.provider or "builtin"),
            requires_network=request.requires_network,
            artifact_dir=str(staging_dir),
            mcp_profile=request.mcp_profile,
            prompt_transport=request.prompt_transport,
        )
        with self._lock:
            self._sessions[session_id] = _SessionState(
                request=request,
                staging_dir=staging_dir,
            )
        return session

    def execute(
        self,
        session: ProviderSession,
        *,
        timeout_sec: float = 0,
        idle_timeout_sec: float | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        event_sink: EventSink | None = None,
    ) -> HarnessRunResult:
        state = self._state_for(session)
        started_at = _now()
        started_monotonic = time.monotonic()

        def cancelled() -> bool:
            return state.cancelled.is_set() or bool(is_cancelled and is_cancelled())

        def emit(event_type: str, payload: dict[str, Any] | None = None) -> None:
            data = dict(payload or {})
            with self._lock:
                accepting = state.accepting_results and not state.terminal
            if accepting and event_sink is not None:
                event_sink(event_type, data)

        status = "cancelled" if cancelled() else "completed"
        exit_code: int | None = None if status == "cancelled" else 0
        error = ""
        diagnostics: dict[str, Any] = {}
        candidates: list[ArtifactCandidate] = []
        timed_out = False
        outcome: dict[str, Any] = {}
        finished = threading.Event()

        def invoke() -> None:
            try:
                if self.execute_callable is not None:
                    raw_result = _invoke_with_supported_arguments(
                        self.execute_callable,
                        request=state.request,
                        session=session,
                        client_factory=self.client_factory,
                        event_sink=emit,
                        is_cancelled=cancelled,
                        timeout_sec=timeout_sec,
                        idle_timeout_sec=idle_timeout_sec,
                    )
                    outcome["result"] = _resolve_awaitable(
                        raw_result,
                        timeout_sec=0,
                    )
                else:
                    outcome["result"] = _resolve_awaitable(
                        self._execute_client(
                            state.request,
                            emit,
                            cancelled,
                            state.staging_dir,
                        ),
                        timeout_sec=0,
                    )
            except BaseException as exc:
                outcome["error"] = exc
            finally:
                with self._lock:
                    late = not state.accepting_results or state.terminal
                if late:
                    self._discard_epoch(state)
                finished.set()

        worker: threading.Thread | None = None
        if status != "cancelled":
            worker = threading.Thread(
                target=invoke,
                name=f"builtin-model-{session.session_id}",
                daemon=True,
            )
            worker.start()

        deadline = (
            started_monotonic + float(timeout_sec)
            if timeout_sec and timeout_sec > 0
            else None
        )
        while worker is not None and not finished.is_set():
            if cancelled():
                status = "cancelled"
                exit_code = None
                diagnostics = {
                    "background_execution_continues": True,
                    "cancellation_scope": "result_commit_only",
                }
                break
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                status = "error"
                exit_code = None
                error = "Built-in model execution timed out"
                diagnostics = {
                    "timeout_sec": timeout_sec,
                    "background_execution_continues": True,
                    "cancellation_scope": "result_commit_only",
                }
                timed_out = True
                break
            finished.wait(
                timeout=0.01 if remaining is None else min(0.01, max(remaining, 0))
            )

        if worker is not None and not finished.is_set():
            with self._lock:
                state.accepting_results = False
                state.candidates = []
                state.terminal = True
            self._discard_epoch(state)
        elif worker is not None:
            try:
                if "error" in outcome:
                    raise outcome["error"]
                raw_result = outcome.get("result")
                status, exit_code, error, diagnostics, candidates = self._normalize_result(
                    raw_result,
                    request=state.request,
                )
                if cancelled():
                    status = "cancelled"
                    exit_code = None
                    candidates = []
                candidates = self._stage_candidates(state, candidates)
            except TimeoutError:
                status = "error"
                exit_code = None
                error = "Built-in model execution timed out"
                diagnostics = {"timeout_sec": timeout_sec}
                timed_out = True
                candidates = []
            except asyncio.CancelledError:
                status = "cancelled"
                exit_code = None
                candidates = []
            except Exception as exc:
                status = "error"
                exit_code = None
                error = f"{type(exc).__name__}: {exc}"
                diagnostics = {"exception_type": type(exc).__name__}
                candidates = []
            finally:
                with self._lock:
                    state.candidates = candidates
                    state.accepting_results = False
                    state.terminal = True
                if status in {"cancelled", "error"}:
                    self._discard_epoch(state)
        else:
            with self._lock:
                state.accepting_results = False
                state.candidates = []
                state.terminal = True
            self._discard_epoch(state)

        completed_at = _now()
        return _BuiltinRunResult(
            session_id=session.session_id,
            status=status,
            exit_code=exit_code,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=round((time.monotonic() - started_monotonic) * 1000),
            timed_out=timed_out,
            error=error,
            provider_diagnostics=diagnostics,
            artifacts=[candidate.path for candidate in candidates],
        )

    def resume(
        self,
        session: ProviderSession,
        resume_from: ProviderResumeToken,
        **_kwargs: Any,
    ) -> ProviderUnsupported:
        return ProviderUnsupported(
            operation="resume",
            capability="session_resume",
            message="内置模型不支持 Provider 会话续接，请从节点 checkpoint 重新执行",
        )

    def cancel(self, session: ProviderSession) -> CancelResult:
        state = self._state_for(session)
        with self._lock:
            if state.terminal:
                return CancelResult(
                    session_id=session.session_id,
                    status="already_terminal",
                    message="内置模型执行已经结束",
                )
            state.cancelled.set()
            state.accepting_results = False
        return CancelResult(session_id=session.session_id, status="cancelled")

    def record_raw_output(
        self,
        session: ProviderSession,
        *,
        stdout: str,
        stderr: str = "",
    ) -> None:
        """Builtin execution has no CLI stdout/stderr channel to persist."""

    def collect_artifacts(self, session: ProviderSession) -> list[ArtifactCandidate]:
        state = self._state_for(session)
        with self._lock:
            return list(state.candidates)

    def finalize_artifacts(self, session: ProviderSession) -> None:
        """Release only Provider staging after the Facade decides the transaction."""

        state = self._state_for(session)
        self._discard_staging(state)

    async def _execute_client(
        self,
        request: HarnessRunRequest,
        event_sink: EventSink,
        is_cancelled: Callable[[], bool],
        staging_dir: Path,
    ) -> dict[str, Any]:
        if self.client_factory is None:
            raise RuntimeError("Built-in model client factory is unavailable")
        client = await _maybe_await(self.client_factory())
        prompt = request.task_bundle.get("rendered_user_input", "")
        if not isinstance(prompt, str):
            prompt = str(prompt)
        messages = [{"role": "user", "content": prompt}]
        try:
            if is_cancelled():
                return {"status": "cancelled", "exit_code": None}
            response = await _maybe_await(
                client.complete(messages, max_tokens=12000, temperature=0.2)
            )
            content = str(getattr(response, "content", "") or "")
            if content:
                event_sink("activity", {"text": content})
            candidates = self._write_single_declared_output(
                request,
                content,
                artifact_dir=staging_dir,
            )
            usage = getattr(response, "usage", {})
            return {
                "status": "completed",
                "exit_code": 0,
                "artifacts": candidates,
                "provider_diagnostics": {
                    "model": str(getattr(response, "model", "") or ""),
                    "usage": dict(usage) if isinstance(usage, Mapping) else {},
                    "finish_reason": str(
                        getattr(response, "finish_reason", "") or ""
                    ),
                },
            }
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                await _maybe_await(close())

    def _write_single_declared_output(
        self,
        request: HarnessRunRequest,
        content: str,
        *,
        artifact_dir: Path,
    ) -> list[str]:
        declared = request.task_bundle.get("required_artifacts")
        names = [str(item) for item in declared or [] if isinstance(item, str)]
        if len(names) != 1:
            return []
        relative = Path(names[0])
        if relative.is_absolute() or ".." in relative.parts:
            return []
        artifact_dir.mkdir(parents=True, exist_ok=True)
        target = artifact_dir / relative
        resolved_root = artifact_dir.resolve()
        resolved_target = target.resolve()
        try:
            resolved_target.relative_to(resolved_root)
        except ValueError:
            return []
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return [names[0]]

    def _stage_candidates(
        self,
        state: _SessionState,
        candidates: list[ArtifactCandidate],
    ) -> list[ArtifactCandidate]:
        staged_candidates: list[ArtifactCandidate] = []
        normalized_candidates = list(candidates)
        normalized_candidates.extend(
            ArtifactCandidate(
                path=str(name),
                metadata={"harness_internal": True},
            )
            for name in state.request.task_bundle.get("harness_internal_artifacts") or []
            if isinstance(name, str)
        )
        for prefix in state.request.task_bundle.get("harness_internal_prefixes") or []:
            if not isinstance(prefix, str) or not _safe_relative_path(prefix):
                continue
            prefix_root = state.staging_dir / prefix
            if prefix_root.is_symlink() or not prefix_root.is_dir():
                continue
            for staged_file in prefix_root.rglob("*"):
                if staged_file.is_symlink() or not staged_file.is_file():
                    continue
                normalized_candidates.append(
                    ArtifactCandidate(
                        path=staged_file.relative_to(state.staging_dir).as_posix(),
                        metadata={"harness_internal": True},
                    )
                )
        seen: set[str] = set()
        with self._lock:
            if not state.accepting_results or state.cancelled.is_set():
                return []
            for candidate in normalized_candidates:
                relative_name = candidate.path
                if relative_name in seen or not _safe_relative_path(relative_name):
                    continue
                seen.add(relative_name)
                staged = state.staging_dir / relative_name
                if staged.is_symlink() or not staged.is_file():
                    continue
                try:
                    staged.resolve().relative_to(state.staging_dir.resolve())
                except ValueError:
                    continue
                staged_candidates.append(
                    ArtifactCandidate(
                        path=relative_name,
                        kind=candidate.kind,
                        metadata={
                            **candidate.metadata,
                            "staged_path": str(staged),
                        },
                    )
                )
        return staged_candidates

    def _discard_epoch(self, state: _SessionState) -> None:
        # A rejected epoch owns only its staging directory.  Restoring a
        # start-time snapshot here can delete a newer epoch's accepted result.
        self._discard_staging(state)

    @staticmethod
    def _discard_staging(state: _SessionState) -> None:
        shutil.rmtree(state.staging_dir, ignore_errors=True)

    def _normalize_result(
        self,
        result: Any,
        *,
        request: HarnessRunRequest,
    ) -> tuple[str, int | None, str, dict[str, Any], list[ArtifactCandidate]]:
        read = result.get if isinstance(result, Mapping) else lambda key, default=None: getattr(
            result, key, default
        )
        status = str(read("status", "completed") or "completed")
        exit_code = read("exit_code", 0 if status == "completed" else None)
        error = str(read("error", "") or "")
        raw_diagnostics = read("provider_diagnostics", {})
        diagnostics = (
            dict(raw_diagnostics) if isinstance(raw_diagnostics, Mapping) else {}
        )
        raw_candidates = read("artifacts", [])
        candidates: list[ArtifactCandidate] = []
        for item in raw_candidates or []:
            if isinstance(item, ArtifactCandidate):
                candidates.append(item)
            elif isinstance(item, str):
                candidates.append(ArtifactCandidate(path=item))
            elif isinstance(item, Mapping) and isinstance(item.get("path"), str):
                candidates.append(
                    ArtifactCandidate(
                        path=item["path"],
                        kind=str(item.get("kind") or "file"),
                        metadata=dict(item.get("metadata") or {}),
                    )
                )
        return status, exit_code, error, diagnostics, candidates

    def _state_for(self, session: ProviderSession) -> _SessionState:
        with self._lock:
            state = self._sessions.get(session.session_id)
        if state is None:
            raise ValueError(f"Unknown built-in model session: {session.session_id}")
        return state


def _invoke_with_supported_arguments(
    fn: Callable[..., Any],
    **kwargs: Any,
) -> Any:
    signature = inspect.signature(fn)
    if any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    ):
        return fn(**kwargs)
    supported = {name: value for name, value in kwargs.items() if name in signature.parameters}
    return fn(**supported)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _resolve_awaitable(value: Any, *, timeout_sec: int = 0) -> Any:
    if not inspect.isawaitable(value):
        return value

    async def wait() -> Any:
        if timeout_sec > 0:
            try:
                return await asyncio.wait_for(value, timeout=timeout_sec)
            except asyncio.TimeoutError as exc:
                raise TimeoutError from exc
        return await value

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(wait())

    outcome: dict[str, Any] = {}

    def run_in_thread() -> None:
        try:
            outcome["value"] = asyncio.run(wait())
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()
    thread.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_relative_path(value: str) -> bool:
    path = Path(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts
