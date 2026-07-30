"""Provider-neutral Harness contracts used by workflow execution and the cockpit."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Protocol

from app.services.provider_adapters.contracts import (
    ArtifactCandidate,
    CancelResult,
    ProviderCapabilities,
    ProviderResumeToken,
    ProviderSession,
    ProviderUnsupported,
)
from app.services.tool_dispatch import (
    ToolCallError,
    ToolCallRequest,
    ToolCallResult,
    ToolDispatcher,
)
from app.services.tool_action_journal import ToolActionContext, ToolActionJournal


HarnessEventKind = Literal[
    "run_started", "session_created", "stage_started", "activity", "thinking_summary",
    "tool_requested", "tool_started", "tool_completed", "tool_failed", "source_read", "artifact_progress", "artifact_created",
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
    requires_network: bool = True
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


@dataclass
class _ArtifactPromotion:
    target: Path
    backup: Path | None
    owner_fingerprint: tuple[int, int, int, int, str]
    artifact: str | None = None


@dataclass
class _ArtifactBaselineEntry:
    target: Path
    backup: Path | None
    original_fingerprint: tuple[int, int, int, int, str] | None


@dataclass
class _ArtifactBaseline:
    entries: dict[Path, _ArtifactBaselineEntry] = field(default_factory=dict)
    backup_root: Path | None = None
    backup_root_resolved: Path | None = None


@dataclass
class _ArtifactTransaction:
    artifact_root: Path
    artifact_root_resolved: Path
    artifacts: list[str] = field(default_factory=list)
    promotions: list[_ArtifactPromotion] = field(default_factory=list)
    unused_baseline_backups: list[Path] = field(default_factory=list)
    baseline_backup_root: Path | None = None
    backup_root_resolved: Path | None = None
    committed: bool = False


class ProviderAdapter(Protocol):
    """Provider boundary; adapters never own CodeTalk task or artifact state."""

    def capabilities(self) -> ProviderCapabilities: ...
    def prepare(self, request: HarnessRunRequest) -> ProviderSession: ...
    def execute(
        self,
        session: ProviderSession,
        *,
        timeout_sec: int = 0,
        idle_timeout_sec: float | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> Any: ...
    def resume(
        self,
        session: ProviderSession,
        resume_from: ProviderResumeToken,
        **kwargs: Any,
    ) -> Any: ...
    def cancel(self, session: ProviderSession) -> CancelResult: ...
    def record_raw_output(
        self, session: ProviderSession, *, stdout: str, stderr: str = ""
    ) -> None: ...
    def collect_artifacts(
        self, session: ProviderSession
    ) -> list[ArtifactCandidate]: ...


class LocalCliProviderAdapter:
    """Compatibility adapter for the existing local CLI runner."""

    def __init__(self, artifact_dir: Path) -> None:
        self.artifact_dir = artifact_dir

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True,
            tool_call=False,
            session_resume=False,
            structured_output=False,
            mcp=True,
            skills=True,
            cancellation=False,
        )

    def prepare(self, request: HarnessRunRequest) -> Any:
        from app.services.agent_run_harness import AgentRunHarness

        run = AgentRunHarness(self.artifact_dir).create_run(
            provider=request.provider,
            command=request.command,
            cwd=request.cwd,
            workflow_snapshot=request.workflow_snapshot,
            task_bundle=request.task_bundle,
            mcp_profile=request.mcp_profile,
            prompt_transport=request.prompt_transport,
            timeout_seconds=request.timeout_seconds,
            idle_timeout_seconds=request.idle_timeout_seconds,
            requires_network=request.requires_network,
            run_id=request.run_id,
            turn_id=request.turn_id,
        )
        return ProviderSession(
            session_id=run.run_id,
            provider=request.provider,
            requires_network=run.requires_network,
            artifact_dir=run.artifact_dir,
            mcp_profile=run.mcp_profile,
            prompt_transport=run.prompt_transport,
        )

    def execute(
        self,
        session: ProviderSession,
        *,
        timeout_sec: int = 0,
        idle_timeout_sec: float | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> Any:
        from app.services.agent_run_harness import AgentRunHarness

        return AgentRunHarness(self.artifact_dir).execute_run(
            session.session_id,
            timeout_sec=timeout_sec,
            idle_timeout_sec=idle_timeout_sec,
            is_cancelled=is_cancelled,
            event_sink=event_sink,
        )

    def resume(
        self,
        session: ProviderSession,
        resume_from: ProviderResumeToken,
        **kwargs: Any,
    ) -> ProviderUnsupported:
        return ProviderUnsupported(
            operation="resume",
            capability="session_resume",
            message="当前兼容 CLI Adapter 不支持会话续接",
        )

    def cancel(self, session: ProviderSession) -> CancelResult:
        return CancelResult(
            session_id=session.session_id,
            status="failed",
            message="当前兼容 CLI Adapter 仅支持执行期间取消回调",
        )

    def record_raw_output(
        self, session: ProviderSession, *, stdout: str, stderr: str = ""
    ) -> None:
        from app.services.agent_run_harness import AgentRunHarness

        AgentRunHarness(self.artifact_dir).record_raw_output(
            session.session_id,
            stdout=stdout,
            stderr=stderr,
        )

    def collect_artifacts(self, session: ProviderSession) -> list[ArtifactCandidate]:
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
            ArtifactCandidate(path=str(name))
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
        tool_dispatcher: ToolDispatcher | None = None,
        granted_tool_permissions: Iterable[str] = (),
        tool_action_journal: ToolActionJournal | None = None,
        tool_action_context: ToolActionContext | None = None,
    ) -> None:
        self.artifact_dir = Path(artifact_dir)
        self._adapter: ProviderAdapter = adapter or LocalCliProviderAdapter(self.artifact_dir)
        self._tool_dispatcher = tool_dispatcher
        self._granted_tool_permissions = tuple(
            str(permission) for permission in granted_tool_permissions
        )
        self._tool_action_journal = tool_action_journal
        self._tool_action_context = tool_action_context
        self._sessions: dict[str, ProviderSession] = {}

    def capabilities(self) -> ProviderCapabilities:
        capability_reader = getattr(self._adapter, "capabilities", None)
        if callable(capability_reader):
            return capability_reader()
        return ProviderCapabilities(
            streaming=False,
            tool_call=False,
            session_resume=False,
            structured_output=False,
            mcp=False,
            skills=False,
            cancellation=False,
        )

    def prepare(self, request: HarnessRunRequest) -> Any:
        raw_session = self._adapter.prepare(request)
        session = self._normalize_session(raw_session, request)
        session_id = session.session_id
        self._sessions[session_id] = session
        self._write_harness_contract(session_id=session_id, request=request)
        return session

    def execute(
        self,
        session: str | ProviderSession,
        *,
        timeout_sec: int = 0,
        idle_timeout_sec: float | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> HarnessRunResult | ProviderUnsupported:
        provider_session = self._resolve_session(session)
        return self._run_adapter_operation(
            provider_session,
            event_sink=event_sink,
            is_cancelled=is_cancelled,
            invoke=lambda operation_event_sink: self._adapter.execute(
                self._adapter_session_argument(provider_session),
                timeout_sec=timeout_sec,
                idle_timeout_sec=idle_timeout_sec,
                is_cancelled=is_cancelled,
                event_sink=operation_event_sink,
            ),
        )

    def _run_adapter_operation(
        self,
        provider_session: ProviderSession,
        *,
        event_sink: Callable[[str, dict[str, Any]], None] | None,
        is_cancelled: Callable[[], bool] | None,
        invoke: Callable[[Callable[[str, dict[str, Any]], Any] | None], Any],
    ) -> HarnessRunResult | ProviderUnsupported:
        session_id = provider_session.session_id
        started = False

        def emit_started() -> None:
            nonlocal started
            if started:
                return
            started = True
            self._emit_lifecycle_event(
                event_sink,
                "run_started",
                {"session_id": session_id},
            )

        def operation_event_sink(kind: str, payload: dict[str, Any]) -> Any:
            emit_started()
            if kind == "tool_requested" or str(
                payload.get("harness_event_kind") or ""
            ) == "tool_requested":
                return self._dispatch_tool_request(payload, event_sink=event_sink)
            normalized_kind = str(payload.get("harness_event_kind") or "")
            if not normalized_kind:
                normalized_kind = normalize_provider_event(kind, payload).kind
            if normalized_kind in {
                "artifact_created",
                "completed",
                "failed",
                "cancelled",
            }:
                return
            if event_sink is not None:
                event_sink(kind, payload)
            return None

        baseline = self._snapshot_declared_artifacts(provider_session)
        try:
            result = invoke(
                operation_event_sink
                if event_sink is not None or self._tool_dispatcher is not None
                else None
            )
        except Exception:
            self._discard_artifact_baseline(baseline)
            raise
        if isinstance(result, ProviderUnsupported):
            self._discard_artifact_baseline(baseline)
            return result
        emit_started()
        public_result = HarnessRunResult(
            session_id=str(
                getattr(result, "session_id", "")
                or getattr(result, "run_id", "")
                or session_id
            ),
            status=result.status,
            exit_code=result.exit_code,
            started_at=result.started_at,
            completed_at=result.completed_at,
            duration_ms=result.duration_ms,
            timed_out=result.timed_out,
            error=result.error,
            provider_diagnostics=dict(result.provider_diagnostics),
        )
        transaction: _ArtifactTransaction | None = None
        try:
            transaction = self._prepare_artifact_transaction(
                provider_session,
                is_cancelled=is_cancelled,
                baseline=baseline,
            )
            self._artifact_commit_barrier(provider_session, transaction.artifacts)
        except Exception:
            if transaction is not None:
                self._rollback_artifact_transaction(transaction)
            self._finalize_adapter_artifacts(provider_session)
            raise
        if public_result.status == "cancelled" or self._cancel_requested(is_cancelled):
            self._rollback_artifact_transaction(transaction)
            self._finalize_adapter_artifacts(provider_session)
            return self._cancelled_result(public_result, event_sink=event_sink)
        public_result = HarnessRunResult(
            **{
                **public_result.__dict__,
                "artifacts": transaction.artifacts,
            }
        )
        try:
            for artifact in public_result.artifacts:
                self._emit_lifecycle_event(
                    event_sink,
                    "artifact_created",
                    {
                        "session_id": public_result.session_id,
                        "path": artifact,
                    },
                )
                if self._cancel_requested(is_cancelled):
                    self._rollback_artifact_transaction(transaction)
                    self._finalize_adapter_artifacts(provider_session)
                    return self._cancelled_result(
                        public_result,
                        event_sink=event_sink,
                    )
            self._finalize_adapter_artifacts(provider_session)
        except Exception:
            self._rollback_artifact_transaction(transaction)
            raise
        if self._cancel_requested(is_cancelled):
            self._rollback_artifact_transaction(transaction)
            return self._cancelled_result(public_result, event_sink=event_sink)
        commit_rejections = self._commit_artifact_transaction(transaction)
        if commit_rejections:
            self._rollback_artifact_transaction(transaction)
            return self._artifact_commit_rejected_result(
                public_result,
                rejections=commit_rejections,
                event_sink=event_sink,
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

    def _dispatch_tool_request(
        self,
        payload: dict[str, Any],
        *,
        event_sink: Callable[[str, dict[str, Any]], None] | None,
    ) -> dict[str, Any]:
        tool_id = str(payload.get("tool_id") or "").strip()
        arguments = payload.get("arguments")
        self._emit_lifecycle_event(
            event_sink,
            "tool_requested",
            {"tool_id": tool_id, "arguments": arguments},
        )
        if self._tool_dispatcher is None:
            result = ToolCallResult(
                tool_id=tool_id,
                status="failed",
                error=ToolCallError(
                    code="tool_dispatch_unavailable",
                    message="No CodeTalk tool dispatcher is configured.",
                ),
            )
        else:
            action = None
            result = None
            if (
                self._tool_action_journal is not None
                and self._tool_action_context is not None
                and isinstance(arguments, dict)
            ):
                provider_call_id = str(payload.get("tool_call_id") or "").strip()
                if not provider_call_id:
                    result = ToolCallResult(
                        tool_id=tool_id,
                        status="failed",
                        error=ToolCallError(
                            code="tool_call_id_required",
                            message=(
                                "Provider Tool requests require a stable tool_call_id "
                                "when durable action replay is enabled."
                            ),
                        ),
                    )
                else:
                    call_suffix = hashlib.sha256(
                        provider_call_id.encode("utf-8")
                    ).hexdigest()[:16]
                    action = self._tool_action_journal.begin(
                        task_id=self._tool_action_context.task_id,
                        attempt_id=self._tool_action_context.attempt_id,
                        node_id=f"{self._tool_action_context.node_id}:tool:{call_suffix}",
                        tool_id=tool_id,
                        frozen_arguments=arguments,
                    )
            if result is None and action is not None and action.disposition == "completed":
                result = ToolCallResult(
                    tool_id=tool_id,
                    status="completed",
                    output=action.record.output,
                )
            elif result is None and action is not None and action.disposition in {"failed", "indeterminate"}:
                persisted_error = dict(action.record.error or {})
                result = ToolCallResult(
                    tool_id=tool_id,
                    status="failed",
                    error=ToolCallError(
                        code=str(
                            persisted_error.get("code")
                            or "tool_action_indeterminate"
                        ),
                        message=str(
                            persisted_error.get("message")
                            or "A prior Tool action may have executed before interruption."
                        ),
                        details=dict(persisted_error.get("details") or {}),
                    ),
                )
            elif result is None:
                result = self._tool_dispatcher.dispatch(ToolCallRequest(
                    tool_id=tool_id,
                    arguments=arguments,
                    granted_permissions=self._granted_tool_permissions,
                ))
                if action is not None:
                    if result.status == "completed":
                        self._tool_action_journal.complete(
                            action.record,
                            output=result.output,
                        )
                    else:
                        self._tool_action_journal.fail(
                            action.record,
                            error=(
                                asdict(result.error)
                                if result.error is not None
                                else {
                                    "code": "tool_execution_failed",
                                    "message": "Local tool handler failed.",
                                    "details": {},
                                }
                            ),
                        )
        response = asdict(result)
        self._emit_lifecycle_event(
            event_sink,
            "tool_completed" if result.status == "completed" else "tool_failed",
            {
                "tool_id": tool_id,
                "status": result.status,
                "error": asdict(result.error) if result.error is not None else None,
            },
        )
        return response

    def resume(
        self,
        session: str | ProviderSession,
        resume_from: ProviderResumeToken | None,
        **kwargs: Any,
    ) -> HarnessRunResult | ProviderUnsupported:
        provider_session = self._resolve_session(session)
        if not self.capabilities().session_resume:
            return ProviderUnsupported(
                operation="resume",
                capability="session_resume",
                message="所选执行器不支持会话续接",
            )
        if resume_from is None:
            return ProviderUnsupported(
                operation="resume",
                capability="session_resume",
                message="缺少可用的会话续接令牌",
                code="resume_token_missing",
            )
        event_sink = kwargs.get("event_sink")
        return self._run_adapter_operation(
            provider_session,
            event_sink=event_sink,
            is_cancelled=kwargs.get("is_cancelled"),
            invoke=lambda operation_event_sink: self._adapter.resume(
                self._adapter_session_argument(provider_session),
                resume_from,
                **{**kwargs, "event_sink": operation_event_sink},
            ),
        )

    def cancel(
        self, session: str | ProviderSession
    ) -> CancelResult | ProviderUnsupported:
        provider_session = self._resolve_session(session)
        if not self.capabilities().cancellation:
            return ProviderUnsupported(
                operation="cancel",
                capability="cancellation",
                message="所选执行器不支持主动取消",
            )
        return self._adapter.cancel(provider_session)

    def record_raw_output(
        self,
        session: str | ProviderSession,
        *,
        stdout: str,
        stderr: str = "",
    ) -> None:
        """Keep legacy diagnostic capture behind the same workflow-facing boundary."""
        provider_session = self._resolve_session(session)
        self._adapter.record_raw_output(
            self._adapter_session_argument(provider_session),
            stdout=stdout,
            stderr=stderr,
        )

    def collect_artifacts(
        self,
        session: str | ProviderSession,
        *,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> list[str]:
        """Collect and immediately commit artifacts outside an execution lifecycle."""
        provider_session = self._resolve_session(session)
        transaction: _ArtifactTransaction | None = None
        finalized = False
        try:
            transaction = self._prepare_artifact_transaction(
                provider_session,
                is_cancelled=is_cancelled,
            )
            self._artifact_commit_barrier(provider_session, transaction.artifacts)
            if self._cancel_requested(is_cancelled):
                self._rollback_artifact_transaction(transaction)
                return []
            self._finalize_adapter_artifacts(provider_session)
            finalized = True
            if self._cancel_requested(is_cancelled):
                self._rollback_artifact_transaction(transaction)
                return []
            commit_rejections = self._commit_artifact_transaction(transaction)
            if commit_rejections:
                self._rollback_artifact_transaction(transaction)
                return []
            return transaction.artifacts
        except Exception:
            if transaction is not None:
                self._rollback_artifact_transaction(transaction)
            raise
        finally:
            if not finalized:
                self._finalize_adapter_artifacts(provider_session)

    def _prepare_artifact_transaction(
        self,
        provider_session: ProviderSession,
        *,
        is_cancelled: Callable[[], bool] | None,
        baseline: _ArtifactBaseline | None = None,
    ) -> _ArtifactTransaction:
        """Validate candidates and provisionally promote them without finalizing."""

        session_id = provider_session.session_id
        declared = set(self._declared_artifacts(session_id))
        internal = set(self._internal_artifacts(session_id))
        internal_prefixes = tuple(self._internal_artifact_prefixes(session_id))
        artifact_root = self.artifact_dir.absolute()
        transaction = _ArtifactTransaction(
            artifact_root=artifact_root,
            artifact_root_resolved=artifact_root.resolve(),
            unused_baseline_backups=[
                entry.backup
                for entry in (baseline.entries if baseline is not None else {}).values()
                if entry.backup is not None
            ],
            baseline_backup_root=(
                baseline.backup_root if baseline is not None else None
            ),
            backup_root_resolved=(
                baseline.backup_root_resolved if baseline is not None else None
            ),
        )
        accepted_set: set[str] = set()
        adapter_session = self._adapter_session_argument(provider_session)
        candidates = self._adapter.collect_artifacts(adapter_session)
        try:
            for candidate in candidates:
                if self._cancel_requested(is_cancelled):
                    break
                candidate_path = (
                    candidate.path
                    if isinstance(candidate, ArtifactCandidate)
                    else candidate
                )
                if not isinstance(candidate_path, str):
                    continue
                is_internal = candidate_path in internal or any(
                    candidate_path.startswith(prefix) for prefix in internal_prefixes
                )
                if candidate_path not in declared and not is_internal:
                    continue
                if candidate_path in accepted_set:
                    continue
                path = Path(candidate_path)
                if path.is_absolute() or ".." in path.parts:
                    continue
                resolved = (self.artifact_dir / path).resolve()
                try:
                    resolved.relative_to(self.artifact_dir.resolve())
                except ValueError:
                    continue

                metadata = (
                    candidate.metadata
                    if isinstance(candidate, ArtifactCandidate)
                    else {}
                )
                staged_path = metadata.get("staged_path")
                staged_candidate = False
                staged_promotion: _ArtifactPromotion | None = None
                if isinstance(staged_path, str) and staged_path:
                    staging_root = Path(provider_session.artifact_dir)
                    staged = Path(staged_path)
                    if staging_root.is_symlink() or staged.is_symlink():
                        continue
                    try:
                        staged.resolve().relative_to(staging_root.resolve())
                    except ValueError:
                        continue
                    if not staged.is_file():
                        continue
                    if self._cancel_requested(is_cancelled):
                        break
                    resolved.parent.mkdir(parents=True, exist_ok=True)
                    baseline_entry = (
                        baseline.entries.get(resolved)
                        if baseline is not None
                        else None
                    )
                    backup = (
                        baseline_entry.backup
                        if baseline_entry is not None
                        else None
                    )
                    if backup in transaction.unused_baseline_backups:
                        transaction.unused_baseline_backups.remove(backup)
                    if resolved.exists() or resolved.is_symlink():
                        if backup is None:
                            backup = self._copy_transaction_backup(
                                transaction,
                                artifact=path,
                                source=resolved,
                            )
                    try:
                        staged.replace(resolved)
                    except OSError:
                        raise
                    owner_fingerprint = self._artifact_fingerprint(resolved)
                    if owner_fingerprint is None:
                        if backup is not None and (
                            backup.exists() or backup.is_symlink()
                        ):
                            backup.replace(resolved)
                        continue
                    staged_promotion = _ArtifactPromotion(
                        target=resolved,
                        backup=backup,
                        owner_fingerprint=owner_fingerprint,
                    )
                    transaction.promotions.append(staged_promotion)
                    staged_candidate = True

                if resolved.is_file() and candidate_path in declared:
                    if not staged_candidate:
                        owner_fingerprint = self._artifact_fingerprint(resolved)
                        if owner_fingerprint is None:
                            continue
                        baseline_entry = (
                            baseline.entries.get(resolved)
                            if baseline is not None
                            else None
                        )
                        backup = (
                            baseline_entry.backup
                            if baseline_entry is not None
                            else None
                        )
                        if backup in transaction.unused_baseline_backups:
                            transaction.unused_baseline_backups.remove(backup)
                        transaction.promotions.append(
                            _ArtifactPromotion(
                                target=resolved,
                                backup=backup,
                                owner_fingerprint=owner_fingerprint,
                                artifact=candidate_path,
                            )
                        )
                    elif staged_promotion is not None:
                        staged_promotion.artifact = candidate_path
                    transaction.artifacts.append(candidate_path)
                    accepted_set.add(candidate_path)
        except Exception:
            self._rollback_artifact_transaction(transaction)
            raise
        return transaction

    def _artifact_commit_barrier(
        self,
        session: ProviderSession,
        artifacts: list[str],
    ) -> None:
        """Test seam after collection/promotion and before the sole commit point."""

    def _snapshot_declared_artifacts(
        self,
        session: ProviderSession,
    ) -> _ArtifactBaseline:
        baseline = _ArtifactBaseline()
        root = self.artifact_dir.resolve()
        try:
            for candidate_path in self._declared_artifacts(session.session_id):
                path = Path(candidate_path)
                if path.is_absolute() or ".." in path.parts:
                    continue
                target = (self.artifact_dir / path).resolve()
                try:
                    target.relative_to(root)
                except ValueError:
                    continue
                original_fingerprint = self._artifact_fingerprint(target)
                backup: Path | None = None
                if original_fingerprint is not None:
                    if baseline.backup_root is None:
                        baseline.backup_root = (
                            self.artifact_dir.absolute().parent
                            / ".harness-transactions"
                            / uuid.uuid4().hex
                        )
                    backup = baseline.backup_root / path
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    if baseline.backup_root_resolved is None:
                        baseline.backup_root_resolved = baseline.backup_root.resolve()
                    shutil.copy2(target, backup)
                baseline.entries[target] = _ArtifactBaselineEntry(
                    target=target,
                    backup=backup,
                    original_fingerprint=original_fingerprint,
                )
        except OSError:
            self._discard_artifact_baseline(baseline)
            raise
        return baseline

    @staticmethod
    def _discard_artifact_baseline(
        baseline: _ArtifactBaseline,
    ) -> None:
        if baseline.backup_root is not None:
            AgentHarnessFacade._remove_baseline_backup_root(baseline.backup_root)

    @staticmethod
    def _remove_baseline_backup_root(backup_root: Path) -> None:
        transaction_parent = backup_root.parent
        shutil.rmtree(backup_root, ignore_errors=True)
        try:
            transaction_parent.rmdir()
        except OSError:
            pass

    @staticmethod
    def _artifact_fingerprint(path: Path) -> tuple[int, int, int, int, str] | None:
        try:
            if path.is_symlink() or not path.is_file():
                return None
            stat_result = path.stat()
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            return (
                int(stat_result.st_dev),
                int(stat_result.st_ino),
                int(stat_result.st_size),
                int(stat_result.st_mtime_ns),
                digest.hexdigest(),
            )
        except OSError:
            return None

    def _copy_transaction_backup(
        self,
        transaction: _ArtifactTransaction,
        *,
        artifact: Path,
        source: Path,
    ) -> Path:
        if self._artifact_path_boundary_rejection(transaction, source):
            raise OSError("artifact path changed before transaction backup")
        if transaction.baseline_backup_root is None:
            transaction.baseline_backup_root = (
                transaction.artifact_root.parent
                / ".harness-transactions"
                / uuid.uuid4().hex
            )
        backup = transaction.baseline_backup_root / artifact
        backup.parent.mkdir(parents=True, exist_ok=True)
        if transaction.backup_root_resolved is None:
            transaction.backup_root_resolved = (
                transaction.baseline_backup_root.resolve()
            )
        shutil.copy2(source, backup)
        return backup

    def _rollback_artifact_transaction(
        self,
        transaction: _ArtifactTransaction,
    ) -> None:
        if transaction.committed:
            return
        for promotion in reversed(transaction.promotions):
            if self._artifact_path_boundary_rejection(
                transaction,
                promotion.target,
            ):
                continue
            if promotion.backup is not None and not self._private_backup_is_safe(
                transaction,
                promotion.backup,
            ):
                continue
            target_is_owned = (
                self._artifact_fingerprint(promotion.target)
                == promotion.owner_fingerprint
            )
            if target_is_owned:
                if self._artifact_path_boundary_rejection(
                    transaction,
                    promotion.target,
                ):
                    continue
                promotion.target.unlink(missing_ok=True)
                if (
                    promotion.backup is not None
                    and self._artifact_path_boundary_rejection(
                        transaction,
                        promotion.target,
                    )
                    == ""
                    and self._private_backup_is_safe(
                        transaction,
                        promotion.backup,
                    )
                ):
                    promotion.backup.replace(promotion.target)
        self._cleanup_transaction_backups(transaction)
        transaction.artifacts.clear()

    def _commit_artifact_transaction(
        self,
        transaction: _ArtifactTransaction,
    ) -> list[dict[str, str]]:
        """The only point after which cancellation cannot revoke delivery."""

        if transaction.committed:
            return []
        rejections = self._artifact_commit_rejections(transaction)
        if rejections:
            return rejections
        transaction.committed = True
        self._cleanup_transaction_backups(transaction)
        return []

    def _artifact_commit_rejections(
        self,
        transaction: _ArtifactTransaction,
    ) -> list[dict[str, str]]:
        promotions = {
            promotion.artifact: promotion
            for promotion in transaction.promotions
            if promotion.artifact is not None
        }
        rejections: list[dict[str, str]] = []
        for artifact in transaction.artifacts:
            promotion = promotions.get(artifact)
            if promotion is None:
                reason = "artifact_transaction_owner_missing"
            else:
                reason = self._artifact_commit_rejection_reason(
                    transaction,
                    promotion,
                )
            if reason:
                rejections.append({"artifact": artifact, "reason": reason})
        return rejections

    def _artifact_commit_rejection_reason(
        self,
        transaction: _ArtifactTransaction,
        promotion: _ArtifactPromotion,
    ) -> str:
        boundary_rejection = self._artifact_path_boundary_rejection(
            transaction,
            promotion.target,
        )
        if boundary_rejection:
            return boundary_rejection
        if not promotion.target.is_file():
            return "artifact_not_regular_file"
        if (
            self._artifact_fingerprint(promotion.target)
            != promotion.owner_fingerprint
        ):
            return "owner_fingerprint_mismatch"
        return ""

    @staticmethod
    def _artifact_path_boundary_rejection(
        transaction: _ArtifactTransaction,
        target: Path,
    ) -> str:
        root = transaction.artifact_root
        try:
            if root.is_symlink():
                return "artifact_root_symlink"
            if root.resolve() != transaction.artifact_root_resolved:
                return "artifact_root_changed"
            target.relative_to(root)
        except (OSError, ValueError):
            return "artifact_outside_root"

        if target.is_symlink():
            return "artifact_is_symlink"

        parent = target.parent
        while True:
            if parent.is_symlink():
                return "artifact_parent_symlink"
            if parent == root:
                break
            if parent == parent.parent:
                return "artifact_outside_root"
            parent = parent.parent

        try:
            target.resolve().relative_to(transaction.artifact_root_resolved)
        except (OSError, ValueError):
            return "artifact_outside_root"
        return ""

    def _private_backup_is_safe(
        self,
        transaction: _ArtifactTransaction,
        backup: Path,
    ) -> bool:
        root = transaction.baseline_backup_root
        root_resolved = transaction.backup_root_resolved
        if root is None or root_resolved is None:
            return False
        if not self._transaction_backup_root_is_safe(transaction):
            return False
        try:
            backup.relative_to(root)
        except ValueError:
            return False
        if backup.is_symlink() or not backup.is_file():
            return False
        parent = backup.parent
        while True:
            if parent.is_symlink():
                return False
            if parent == root:
                break
            if parent == parent.parent:
                return False
            parent = parent.parent
        try:
            backup.resolve().relative_to(root_resolved)
        except (OSError, ValueError):
            return False
        return True

    @staticmethod
    def _transaction_backup_root_is_safe(
        transaction: _ArtifactTransaction,
    ) -> bool:
        root = transaction.baseline_backup_root
        root_resolved = transaction.backup_root_resolved
        if root is None or root_resolved is None:
            return False
        expected_parent = (
            transaction.artifact_root.parent / ".harness-transactions"
        )
        if root.parent != expected_parent:
            return False
        try:
            if expected_parent.is_symlink() or root.is_symlink():
                return False
            if root.resolve() != root_resolved:
                return False
        except OSError:
            return False
        return root.is_dir()

    def _cleanup_transaction_backups(
        self,
        transaction: _ArtifactTransaction,
    ) -> None:
        root = transaction.baseline_backup_root
        if root is None:
            return
        if not root.exists() and not root.is_symlink():
            return
        if not self._transaction_backup_root_is_safe(transaction):
            return
        transaction_parent = root.parent
        shutil.rmtree(root)
        expected_parent = (
            transaction.artifact_root.parent / ".harness-transactions"
        )
        if (
            transaction_parent == expected_parent
            and not transaction_parent.is_symlink()
        ):
            try:
                transaction_parent.rmdir()
            except OSError:
                pass

    @staticmethod
    def _cancel_requested(is_cancelled: Callable[[], bool] | None) -> bool:
        if is_cancelled is None:
            return False
        try:
            return bool(is_cancelled())
        except Exception:
            return False

    def _finalize_adapter_artifacts(self, session: ProviderSession) -> None:
        finalize = getattr(self._adapter, "finalize_artifacts", None)
        if callable(finalize):
            finalize(self._adapter_session_argument(session))

    def _cancelled_result(
        self,
        result: HarnessRunResult,
        *,
        event_sink: Callable[[str, dict[str, Any]], None] | None,
    ) -> HarnessRunResult:
        cancelled = HarnessRunResult(
            session_id=result.session_id,
            status="cancelled",
            exit_code=result.exit_code,
            started_at=result.started_at,
            completed_at=result.completed_at,
            duration_ms=result.duration_ms,
            timed_out=False,
            error="运行已取消，未交付任何产物",
            provider_diagnostics=dict(result.provider_diagnostics),
            artifacts=[],
        )
        self._emit_lifecycle_event(
            event_sink,
            "cancelled",
            {
                "session_id": cancelled.session_id,
                "status": cancelled.status,
                "duration_ms": cancelled.duration_ms,
                "artifact_count": 0,
                "error": cancelled.error,
            },
        )
        return cancelled

    def _artifact_commit_rejected_result(
        self,
        result: HarnessRunResult,
        *,
        rejections: list[dict[str, str]],
        event_sink: Callable[[str, dict[str, Any]], None] | None,
    ) -> HarnessRunResult:
        diagnostics = dict(result.provider_diagnostics)
        diagnostics["artifact_commit_rejection"] = {
            "code": "artifact_commit_cas_failed",
            "rejected_artifacts": rejections,
        }
        rejected = HarnessRunResult(
            session_id=result.session_id,
            status="failed",
            exit_code=result.exit_code,
            started_at=result.started_at,
            completed_at=result.completed_at,
            duration_ms=result.duration_ms,
            timed_out=False,
            error="artifact_commit_rejected",
            provider_diagnostics=diagnostics,
            artifacts=[],
        )
        self._emit_lifecycle_event(
            event_sink,
            "failed",
            {
                "session_id": rejected.session_id,
                "status": rejected.status,
                "duration_ms": rejected.duration_ms,
                "artifact_count": 0,
                "error": rejected.error,
                "artifact_commit_rejection": diagnostics[
                    "artifact_commit_rejection"
                ],
            },
        )
        return rejected

    def _normalize_session(
        self,
        raw_session: Any,
        request: HarnessRunRequest,
    ) -> ProviderSession:
        if isinstance(raw_session, ProviderSession):
            return raw_session
        session_id = str(
            getattr(raw_session, "session_id", "")
            or getattr(raw_session, "run_id", "")
            or request.run_id
            or ""
        )
        return ProviderSession(
            session_id=session_id,
            provider=str(getattr(raw_session, "provider", "") or request.provider),
            requires_network=bool(
                getattr(raw_session, "requires_network", request.requires_network)
            ),
            artifact_dir=str(getattr(raw_session, "artifact_dir", "") or ""),
            mcp_profile=str(getattr(raw_session, "mcp_profile", "") or ""),
            prompt_transport=str(
                getattr(raw_session, "prompt_transport", "") or ""
            ),
        )

    def _resolve_session(self, session: str | ProviderSession) -> ProviderSession:
        if isinstance(session, ProviderSession):
            return session
        known = self._sessions.get(str(session))
        if known is not None:
            return known
        return ProviderSession(session_id=str(session), provider="")

    def _adapter_session_argument(self, session: ProviderSession) -> Any:
        if callable(getattr(self._adapter, "capabilities", None)):
            return session
        return session.session_id

    def _write_harness_contract(
        self,
        *,
        session_id: str,
        request: HarnessRunRequest,
    ) -> None:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        required = request.task_bundle.get("required_artifacts")
        declared = [str(item) for item in required or [] if isinstance(item, str)]
        internal = [
            str(item)
            for item in request.task_bundle.get("harness_internal_artifacts") or []
            if isinstance(item, str)
        ]
        internal_prefixes = [
            str(item)
            for item in request.task_bundle.get("harness_internal_prefixes") or []
            if isinstance(item, str)
        ]
        payload = {
            "contract_version": 1,
            "session_id": session_id,
            "required_artifacts": declared,
            "internal_artifacts": internal,
            "internal_artifact_prefixes": internal_prefixes,
        }
        (self.artifact_dir / "harness_contract.json").write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )

    def _declared_artifacts(self, session_id: str) -> list[str]:
        return self._contract_artifacts(session_id, "required_artifacts")

    def _internal_artifacts(self, session_id: str) -> list[str]:
        return self._contract_artifacts(session_id, "internal_artifacts")

    def _internal_artifact_prefixes(self, session_id: str) -> list[str]:
        return [
            prefix if prefix.endswith("/") else f"{prefix}/"
            for prefix in self._contract_artifacts(
                session_id, "internal_artifact_prefixes"
            )
        ]

    def _contract_artifacts(self, session_id: str, field: str) -> list[str]:
        try:
            payload = json.loads(
                (self.artifact_dir / "harness_contract.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return []
        if not isinstance(payload, dict) or payload.get("session_id") != session_id:
            return []
        return [
            str(item)
            for item in payload.get(field) or []
            if isinstance(item, str)
        ]

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
        return HarnessEvent("network_egress_blocked", "user", data, "运行环境返回连接不可用")
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
