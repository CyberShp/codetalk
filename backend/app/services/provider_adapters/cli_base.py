"""Thin provider adapters over the existing local CLI bridge."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
import stat
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.services import agent_cli_bridge
from app.services.provider_adapters.contracts import (
    ArtifactCandidate,
    CancelResult,
    ProviderCapabilities,
    ProviderResumeToken,
    ProviderSession,
    ProviderUnsupported,
)


_ARTIFACT_BASELINE_KEY = "_cli_artifact_baseline"
_ARTIFACT_CANDIDATES_KEY = "_cli_artifact_candidates"
_ArtifactFingerprint = tuple[int, int, int, int, int, int, int, int, str]


@dataclass(frozen=True)
class CliProviderRunResult:
    run_id: str
    status: str
    exit_code: int | None
    started_at: str
    completed_at: str
    duration_ms: int
    timed_out: bool = False
    error: str = ""
    provider_diagnostics: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)

    @property
    def session_id(self) -> str:
        return self.run_id


@dataclass
class _ActiveExecution:
    cancel_requested: threading.Event = field(default_factory=threading.Event)


class CliProviderAdapter:
    """Translate Harness values into one invocation of ``stream_agent_runtime``."""

    provider = "cli"
    default_command = ""
    prompt_transport = "stdin"
    output_mode = "plain"
    provider_capabilities = ProviderCapabilities(
        streaming=True,
        tool_call=False,
        session_resume=False,
        structured_output=False,
        mcp=False,
        skills=False,
        cancellation=True,
    )

    def __init__(self, artifact_dir: str | Path) -> None:
        self.artifact_dir = Path(artifact_dir).expanduser().resolve()
        self._active: dict[str, _ActiveExecution] = {}
        self._active_lock = threading.Lock()

    def capabilities(self) -> ProviderCapabilities:
        return self.provider_capabilities

    def prepare(self, request: Any) -> ProviderSession:
        command = [str(item) for item in (request.command or [])]
        executable = command[0] if command else self.default_command
        prompt = request.task_bundle.get("rendered_user_input")
        if not isinstance(prompt, str):
            prompt = request.task_bundle.get("prompt")
        if not isinstance(prompt, str):
            prompt = ""

        runtime = {
            "id": f"harness-{self.provider}",
            "name": self.provider,
            "provider": self.provider,
            "command": executable,
            "args": command[1:],
            "prompt_transport": self.prompt_transport,
            "output_mode": self.output_mode,
            "completion_mode": "process_exit",
            "session_persistence": "resume_args",
            "resume_args": [],
            "timeout_seconds": int(request.timeout_seconds or 120),
            "idle_complete_seconds": max(1, int(request.idle_timeout_seconds or 5)),
            "mcp_profile": str(request.mcp_profile or ""),
            "requires_network": bool(request.requires_network),
            "env": {
                "CODETALK_AGENT_ARTIFACT_DIR": str(self.artifact_dir),
            },
            "sandbox_read_paths": _captured_material_read_paths(
                request.task_bundle,
                artifact_dir=self.artifact_dir,
            ),
        }
        session_id = str(request.run_id or f"{self.provider}-{uuid.uuid4().hex}")
        return ProviderSession(
            session_id=session_id,
            provider=self.provider,
            requires_network=bool(request.requires_network),
            artifact_dir=str(self.artifact_dir),
            mcp_profile=str(request.mcp_profile or ""),
            prompt_transport=self.prompt_transport,
            metadata={
                "runtime": runtime,
                "prompt": prompt,
                "cwd": str(request.cwd or ""),
            },
        )

    def execute(
        self,
        session: ProviderSession,
        *,
        timeout_sec: int = 0,
        idle_timeout_sec: float | None = None,
        is_cancelled: Callable[[], Any] | None = None,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> CliProviderRunResult:
        return _run_sync(
            self._execute_async(
                session,
                resume_session_id=None,
                timeout_sec=timeout_sec,
                idle_timeout_sec=idle_timeout_sec,
                is_cancelled=is_cancelled,
                event_sink=event_sink,
            )
        )

    def resume(
        self,
        session: ProviderSession,
        resume_from: ProviderResumeToken,
        **kwargs: Any,
    ) -> CliProviderRunResult | ProviderUnsupported:
        if resume_from.provider != self.provider:
            return ProviderUnsupported(
                operation="resume",
                capability="session_resume",
                message="会话续接令牌与执行器不匹配",
                code="resume_token_provider_mismatch",
            )
        if not resume_from.value:
            return ProviderUnsupported(
                operation="resume",
                capability="session_resume",
                message="会话续接令牌为空",
                code="resume_token_missing",
            )
        return _run_sync(
            self._execute_async(
                session,
                resume_session_id=resume_from.value,
                timeout_sec=int(kwargs.pop("timeout_sec", 0) or 0),
                idle_timeout_sec=kwargs.pop("idle_timeout_sec", None),
                is_cancelled=kwargs.pop("is_cancelled", None),
                event_sink=kwargs.pop("event_sink", None),
            )
        )

    def cancel(self, session: ProviderSession) -> CancelResult:
        with self._active_lock:
            execution = self._active.get(session.session_id)
        if execution is None:
            return CancelResult(
                session_id=session.session_id,
                status="already_terminal",
                message="执行器当前没有活动进程",
            )
        execution.cancel_requested.set()
        return CancelResult(session_id=session.session_id, status="cancelled")

    def record_raw_output(
        self,
        session: ProviderSession,
        *,
        stdout: str,
        stderr: str = "",
    ) -> None:
        session.metadata["raw_output"] = {"stdout": stdout, "stderr": stderr}

    def collect_artifacts(self, session: ProviderSession) -> list[ArtifactCandidate]:
        root = self._trusted_artifact_root(session)
        recorded = session.metadata.get(_ARTIFACT_CANDIDATES_KEY)
        if root is None or not isinstance(recorded, dict):
            return []
        current = _snapshot_regular_files(root)
        candidates: list[ArtifactCandidate] = []
        for relative_path, fingerprint in recorded.items():
            if current.get(relative_path) != fingerprint:
                continue
            candidates.append(
                ArtifactCandidate(
                    path=relative_path,
                    metadata={"provider": self.provider},
                )
            )
        return sorted(candidates, key=lambda candidate: candidate.path)

    def _trusted_artifact_root(self, session: ProviderSession) -> Path | None:
        requested = Path(session.artifact_dir or self.artifact_dir).expanduser()
        try:
            if requested.is_symlink() or requested.resolve() != self.artifact_dir:
                return None
            if not requested.is_dir():
                return None
        except OSError:
            return None
        return requested

    async def _execute_async(
        self,
        session: ProviderSession,
        *,
        resume_session_id: str | None,
        timeout_sec: int,
        idle_timeout_sec: float | None,
        is_cancelled: Callable[[], Any] | None,
        event_sink: Callable[[str, dict[str, Any]], None] | None,
    ) -> CliProviderRunResult:
        runtime = dict(session.metadata.get("runtime") or {})
        runtime["env"] = dict(runtime.get("env") or {})
        if timeout_sec > 0:
            runtime["timeout_seconds"] = int(timeout_sec)
        if idle_timeout_sec is not None and idle_timeout_sec > 0:
            runtime["idle_complete_seconds"] = max(1, int(idle_timeout_sec))

        execution = _ActiveExecution()
        with self._active_lock:
            if session.session_id in self._active:
                return self._result(
                    session,
                    started_at=_now(),
                    started_clock=time.monotonic(),
                    status="failed",
                    error="同一 Provider session 已有活动执行",
                )
            self._active[session.session_id] = execution

        started_at = _now()
        started_clock = time.monotonic()
        output: list[str] = []
        provider_session: dict[str, Any] = {}
        artifact_root = self._trusted_artifact_root(session)
        session.metadata[_ARTIFACT_BASELINE_KEY] = (
            _snapshot_regular_files(artifact_root) if artifact_root is not None else {}
        )
        session.metadata[_ARTIFACT_CANDIDATES_KEY] = {}

        def combined_cancelled() -> Any:
            if execution.cancel_requested.is_set():
                return True
            if is_cancelled is None:
                return False
            result = is_cancelled()
            if inspect.isawaitable(result):

                async def resolve_external_cancellation() -> bool:
                    cancelled = bool(await result)
                    if cancelled:
                        execution.cancel_requested.set()
                    return cancelled

                return resolve_external_cancellation()
            cancelled = bool(result)
            if cancelled:
                execution.cancel_requested.set()
            return cancelled

        def update_session(update: dict[str, Any]) -> None:
            provider_session.clear()
            provider_session.update(update)
            opaque_value = update.get("resume_session_id") or update.get("session_id")
            if isinstance(opaque_value, str) and opaque_value:
                token = ProviderResumeToken(provider=self.provider, value=opaque_value)
                session.metadata["resume_token"] = token
            if event_sink is not None:
                event_sink(
                    "session_created",
                    {"provider": self.provider, **update},
                )

        def update_stderr(text: str) -> None:
            if event_sink is not None:
                event_sink("diagnostic", {"provider": self.provider, "text": text})

        error = ""
        timed_out = False
        status = "completed"
        exit_code: int | None = 0
        try:
            async for chunk in agent_cli_bridge.stream_agent_runtime(
                runtime=runtime,
                prompt=str(session.metadata.get("prompt", "")),
                cwd=str(session.metadata.get("cwd") or "") or None,
                resume_session_id=resume_session_id,
                session_update=update_session,
                stderr_update=update_stderr,
                is_cancelled=combined_cancelled,
            ):
                output.append(chunk)
                if event_sink is not None:
                    event_sink(
                        "activity",
                        {"provider": self.provider, "text": chunk},
                    )
        except agent_cli_bridge.AgentRuntimeError as exc:
            error = str(exc)
            timed_out = any(
                marker in error for marker in ("超时", "安全运行上限", "没有输出或进度")
            )
            status = "failed"
            exit_code = None
        finally:
            baseline = session.metadata.get(_ARTIFACT_BASELINE_KEY)
            current = (
                _snapshot_regular_files(artifact_root)
                if artifact_root is not None
                else {}
            )
            session.metadata[_ARTIFACT_CANDIDATES_KEY] = {
                path: fingerprint
                for path, fingerprint in current.items()
                if not isinstance(baseline, dict)
                or baseline.get(path) != fingerprint
            }
            with self._active_lock:
                self._active.pop(session.session_id, None)

        if execution.cancel_requested.is_set():
            status = "cancelled"
            exit_code = None
            error = ""
            timed_out = False
        diagnostics: dict[str, Any] = {
            "output": "".join(output),
        }
        token = session.metadata.get("resume_token")
        if isinstance(token, ProviderResumeToken):
            diagnostics["resume_token"] = {
                "provider": token.provider,
                "value": token.value,
            }
        if provider_session:
            diagnostics["provider_session"] = dict(provider_session)
        return self._result(
            session,
            started_at=started_at,
            started_clock=started_clock,
            status=status,
            exit_code=exit_code,
            error=error,
            timed_out=timed_out,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _result(
        session: ProviderSession,
        *,
        started_at: str,
        started_clock: float,
        status: str,
        exit_code: int | None = None,
        error: str = "",
        timed_out: bool = False,
        diagnostics: dict[str, Any] | None = None,
    ) -> CliProviderRunResult:
        return CliProviderRunResult(
            run_id=session.session_id,
            status=status,
            exit_code=exit_code,
            started_at=started_at,
            completed_at=_now(),
            duration_ms=max(0, int((time.monotonic() - started_clock) * 1000)),
            timed_out=timed_out,
            error=error,
            provider_diagnostics=dict(diagnostics or {}),
        )


def _captured_material_read_paths(
    task_bundle: dict[str, Any],
    *,
    artifact_dir: Path,
) -> list[str]:
    """Expose immutable task captures without trusting user-supplied paths.

    A workflow input may retain its original upload path for diagnostics.  The
    provider sandbox only receives files copied beneath this Task Run, so an
    input payload cannot expand Agent read access to an arbitrary local path.
    """

    resolved_artifact_dir = artifact_dir.expanduser().resolve()
    task_root = (
        resolved_artifact_dir.parent.parent
        if resolved_artifact_dir.parent.name == "agent_runs"
        else resolved_artifact_dir
    )
    input_root = task_root / "inputs"
    try:
        input_root = input_root.resolve(strict=True)
    except OSError:
        return []

    input_materials = task_bundle.get("input_materials")
    materials = (
        input_materials.get("materials")
        if isinstance(input_materials, dict)
        else []
    )
    paths: set[str] = set()
    for material in materials or []:
        if not isinstance(material, dict):
            continue
        for key in (
            "chunks_path",
            "copied_path",
            "metadata_path",
            "parsed_text_path",
        ):
            raw_path = str(material.get(key) or "").strip()
            if not raw_path:
                continue
            candidate = Path(raw_path).expanduser()
            try:
                if candidate.is_symlink():
                    continue
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(input_root)
                if not resolved.is_file() or resolved.is_symlink():
                    continue
            except (OSError, ValueError):
                continue
            paths.add(str(resolved))
    return sorted(paths)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snapshot_regular_files(root: Path) -> dict[str, _ArtifactFingerprint]:
    snapshot: dict[str, _ArtifactFingerprint] = {}

    def visit(directory: Path) -> None:
        try:
            entries = list(os.scandir(directory))
        except OSError:
            return
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    visit(path)
                    continue
                fingerprint = _fingerprint_regular_file(path)
                if fingerprint is None:
                    continue
                relative_path = path.relative_to(root).as_posix()
            except (OSError, ValueError):
                continue
            snapshot[relative_path] = fingerprint

    visit(root)
    return snapshot


def _fingerprint_regular_file(path: Path) -> _ArtifactFingerprint | None:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            return None
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
            before.st_gid,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_uid,
            after.st_gid,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            return None
        return (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_uid,
            after.st_gid,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
            digest,
        )
    except OSError:
        return None
    finally:
        os.close(descriptor)


def _run_sync(awaitable: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    results: list[Any] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            results.append(asyncio.run(awaitable))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return results[0]
