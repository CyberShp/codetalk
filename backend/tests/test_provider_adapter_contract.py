from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import shutil

import pytest


def _request(prompt: str = " first line\n\nlast line "):
    from app.services.harness_facade import HarnessRunRequest

    return HarnessRunRequest(
        provider="contract-provider",
        command=["contract-provider"],
        cwd="/repo",
        workflow_snapshot={"id": "workflow"},
        task_bundle={
            "rendered_user_input": prompt,
            "required_artifacts": ["report.md"],
        },
    )


class _ContractAdapter:
    def __init__(self, artifact_dir: Path, *, resume_supported: bool = True) -> None:
        from app.services.provider_adapters.contracts import ProviderCapabilities

        self.artifact_dir = artifact_dir
        self._capabilities = ProviderCapabilities(
            streaming=True,
            tool_call=False,
            session_resume=resume_supported,
            structured_output=True,
            mcp=False,
            skills=True,
            cancellation=True,
        )
        self.prepared_request = None
        self.resumed_with = None
        self.cancelled_session = None

    def capabilities(self):
        return self._capabilities

    def prepare(self, request):
        from app.services.provider_adapters.contracts import ProviderResumeToken, ProviderSession

        self.prepared_request = request
        return ProviderSession(
            session_id="provider-session-1",
            provider=request.provider,
            resume_token=ProviderResumeToken(provider=request.provider, value="opaque-1"),
        )

    def execute(self, session, **_kwargs):
        from app.services.harness_facade import HarnessRunResult

        return HarnessRunResult(
            session_id=session.session_id,
            status="completed",
            exit_code=0,
            started_at="2026-07-28T00:00:00Z",
            completed_at="2026-07-28T00:00:01Z",
            duration_ms=1000,
        )

    def resume(self, session, resume_from, **_kwargs):
        self.resumed_with = (session, resume_from)
        event_sink = _kwargs.get("event_sink")
        if event_sink is not None:
            event_sink("activity", {"message": "resumed provider activity"})
        return self.execute(session)

    def cancel(self, session):
        from app.services.provider_adapters.contracts import CancelResult

        self.cancelled_session = session
        return CancelResult(
            session_id=session.session_id,
            status="cancelled",
        )

    def record_raw_output(self, _session, *, stdout, stderr=""):
        return None

    def collect_artifacts(self, _session):
        from app.services.provider_adapters.contracts import ArtifactCandidate

        return [
            ArtifactCandidate(path="report.md"),
            ArtifactCandidate(path="report.md"),
            ArtifactCandidate(path="undeclared.md"),
            ArtifactCandidate(path="../outside.md"),
            ArtifactCandidate(path="linked.md"),
        ]


def test_facade_preserves_verbatim_request_and_exposes_provider_capabilities(tmp_path):
    from app.services.harness_facade import AgentHarnessFacade

    adapter = _ContractAdapter(tmp_path)
    facade = AgentHarnessFacade(tmp_path, adapter=adapter)
    request = _request()

    session = facade.prepare(request)

    assert adapter.prepared_request is request
    assert adapter.prepared_request.task_bundle["rendered_user_input"] == (
        " first line\n\nlast line "
    )
    assert session.session_id == "provider-session-1"
    assert facade.capabilities().streaming is True
    assert facade.capabilities().session_resume is True


def test_facade_resume_and_cancel_use_typed_provider_session(tmp_path):
    from app.services.harness_facade import AgentHarnessFacade

    adapter = _ContractAdapter(tmp_path)
    facade = AgentHarnessFacade(tmp_path, adapter=adapter)
    session = facade.prepare(_request())

    resumed = facade.resume(session, session.resume_token)
    cancelled = facade.cancel(session)

    assert resumed.status == "completed"
    assert adapter.resumed_with == (session, session.resume_token)
    assert cancelled.status == "cancelled"
    assert adapter.cancelled_session is session


def test_facade_supported_resume_uses_standard_lifecycle_and_artifact_boundary(tmp_path):
    from app.services.harness_facade import AgentHarnessFacade, HarnessRunResult

    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (artifact_dir / "report.md").write_text("declared", encoding="utf-8")
    (artifact_dir / "undeclared.md").write_text("hidden", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (artifact_dir / "linked.md").symlink_to(outside)
    adapter = _ContractAdapter(artifact_dir)
    facade = AgentHarnessFacade(artifact_dir, adapter=adapter)
    session = facade.prepare(_request())
    events: list[tuple[str, dict]] = []

    result = facade.resume(
        session,
        session.resume_token,
        event_sink=lambda kind, payload: events.append((kind, payload)),
    )

    assert isinstance(result, HarnessRunResult)
    assert result.session_id == session.session_id
    assert result.status == "completed"
    assert result.artifacts == ["report.md"]
    assert [kind for kind, _payload in events] == [
        "run_started",
        "activity",
        "artifact_created",
        "completed",
    ]
    assert events[2][1]["path"] == "report.md"
    assert events[2][1]["harness_event_kind"] == "artifact_created"
    assert events[3][1]["artifact_count"] == 1


def test_facade_accepts_the_public_session_id_result_contract(tmp_path):
    from app.services.harness_facade import AgentHarnessFacade

    adapter = _ContractAdapter(tmp_path)
    facade = AgentHarnessFacade(tmp_path, adapter=adapter)
    session = facade.prepare(_request())

    result = facade.execute(session)

    assert result.session_id == session.session_id
    assert result.status == "completed"


def test_facade_emits_the_same_artifact_lifecycle_for_every_adapter(tmp_path):
    from app.services.harness_facade import AgentHarnessFacade

    (tmp_path / "report.md").write_text("report", encoding="utf-8")
    adapter = _ContractAdapter(tmp_path)
    facade = AgentHarnessFacade(tmp_path, adapter=adapter)
    session = facade.prepare(_request())
    events: list[tuple[str, dict]] = []

    result = facade.execute(
        session,
        event_sink=lambda kind, payload: events.append((kind, payload)),
    )

    assert result.artifacts == ["report.md"]
    assert [kind for kind, _payload in events] == [
        "run_started",
        "artifact_created",
        "completed",
    ]
    assert events[1][1]["session_id"] == "provider-session-1"
    assert events[1][1]["path"] == "report.md"
    assert events[1][1]["harness_event_kind"] == "artifact_created"


@pytest.mark.parametrize("cancel_moment", ["collect_started", "collect_returned"])
def test_facade_cancellation_fences_artifact_promotion_and_terminal_success(
    tmp_path,
    cancel_moment,
):
    """Cancellation wins until artifact promotion and terminal events commit."""
    from app.services.harness_facade import AgentHarnessFacade, HarnessRunResult
    from app.services.provider_adapters.contracts import (
        ArtifactCandidate,
        ProviderSession,
    )

    artifact_dir = tmp_path / "artifacts"
    staging_dir = tmp_path / "provider-staging"
    staging_dir.mkdir()
    staged_report = staging_dir / "report.md"
    staged_report.write_text("must not be delivered", encoding="utf-8")
    cancelled = {"value": False}

    class Candidates:
        def __iter__(self):
            yield ArtifactCandidate(
                path="report.md",
                metadata={"staged_path": str(staged_report)},
            )
            if cancel_moment == "collect_returned":
                cancelled["value"] = True

    class CancellationRaceAdapter(_ContractAdapter):
        def prepare(self, request):
            return ProviderSession(
                session_id="cancel-race-session",
                provider=request.provider,
                artifact_dir=str(staging_dir),
            )

        def execute(self, session, **_kwargs):
            return HarnessRunResult(
                session_id=session.session_id,
                status="completed",
                exit_code=0,
                started_at="2026-07-28T00:00:00Z",
                completed_at="2026-07-28T00:00:01Z",
                duration_ms=1000,
            )

        def collect_artifacts(self, _session):
            if cancel_moment == "collect_started":
                cancelled["value"] = True
            return Candidates()

    facade = AgentHarnessFacade(
        artifact_dir,
        adapter=CancellationRaceAdapter(artifact_dir),
    )
    session = facade.prepare(_request())
    events: list[tuple[str, dict]] = []

    result = facade.execute(
        session,
        is_cancelled=lambda: cancelled["value"],
        event_sink=lambda kind, payload: events.append((kind, payload)),
    )

    assert result.status == "cancelled"
    assert result.artifacts == []
    assert not (artifact_dir / "report.md").exists()
    assert not any(
        payload.get("harness_event_kind") in {"artifact_created", "completed"}
        for _kind, payload in events
    )
    assert events[-1][1]["harness_event_kind"] == "cancelled"


def test_facade_resume_uses_the_same_artifact_commit_fence(tmp_path):
    from app.services.harness_facade import AgentHarnessFacade, HarnessRunResult
    from app.services.provider_adapters.contracts import (
        ArtifactCandidate,
        ProviderSession,
    )

    artifact_dir = tmp_path / "artifacts"
    staging_dir = tmp_path / "resume-staging"
    staging_dir.mkdir()
    staged_report = staging_dir / "report.md"
    staged_report.write_text("cancelled resumed report", encoding="utf-8")
    cancelled = {"value": False}

    class ResumeRaceAdapter(_ContractAdapter):
        def prepare(self, request):
            session = super().prepare(request)
            return ProviderSession(
                session_id=session.session_id,
                provider=session.provider,
                resume_token=session.resume_token,
                artifact_dir=str(staging_dir),
            )

        def resume(self, session, resume_from, **_kwargs):
            return HarnessRunResult(
                session_id=session.session_id,
                status="completed",
                exit_code=0,
                started_at="2026-07-28T00:00:00Z",
                completed_at="2026-07-28T00:00:01Z",
                duration_ms=1000,
            )

        def collect_artifacts(self, _session):
            cancelled["value"] = True
            return [
                ArtifactCandidate(
                    path="report.md",
                    metadata={"staged_path": str(staged_report)},
                )
            ]

    facade = AgentHarnessFacade(artifact_dir, adapter=ResumeRaceAdapter(artifact_dir))
    session = facade.prepare(_request())
    events: list[tuple[str, dict]] = []

    result = facade.resume(
        session,
        session.resume_token,
        is_cancelled=lambda: cancelled["value"],
        event_sink=lambda kind, payload: events.append((kind, payload)),
    )

    assert result.status == "cancelled"
    assert result.artifacts == []
    assert not (artifact_dir / "report.md").exists()
    assert events[-1][1]["harness_event_kind"] == "cancelled"
    assert not any(
        payload.get("harness_event_kind") in {"artifact_created", "completed"}
        for _kind, payload in events
    )


def _transaction_race_facade(
    tmp_path,
    *,
    cancelled,
    barrier=None,
    cancel_at_barrier=True,
):
    from app.services.harness_facade import (
        AgentHarnessFacade,
        HarnessRunResult,
    )
    from app.services.provider_adapters.contracts import (
        ArtifactCandidate,
        ProviderCapabilities,
        ProviderResumeToken,
        ProviderSession,
    )

    artifact_dir = tmp_path / "artifacts"
    staging_dir = tmp_path / "provider-staging"
    staging_dir.mkdir()
    staged_report = staging_dir / "report.md"
    staged_report.write_text("candidate", encoding="utf-8")
    adapter_state = {"collect_returned": False, "finalize_calls": 0}

    class TransactionAdapter:
        def capabilities(self):
            return ProviderCapabilities(
                streaming=True,
                tool_call=False,
                session_resume=True,
                structured_output=False,
                mcp=False,
                skills=False,
                cancellation=True,
            )

        def prepare(self, request):
            return ProviderSession(
                session_id="transaction-session",
                provider=request.provider,
                resume_token=ProviderResumeToken(
                    provider=request.provider,
                    value="resume-token",
                ),
                artifact_dir=str(staging_dir),
            )

        def execute(self, session, **_kwargs):
            return HarnessRunResult(
                session_id=session.session_id,
                status="completed",
                exit_code=0,
                started_at="2026-07-28T00:00:00Z",
                completed_at="2026-07-28T00:00:01Z",
                duration_ms=1000,
            )

        def resume(self, session, _resume_from, **_kwargs):
            return self.execute(session)

        def collect_artifacts(self, _session):
            candidates = [
                ArtifactCandidate(
                    path="report.md",
                    metadata={"staged_path": str(staged_report)},
                )
            ]
            adapter_state["collect_returned"] = True
            return candidates

        def finalize_artifacts(self, _session):
            adapter_state["finalize_calls"] += 1
            shutil.rmtree(staging_dir, ignore_errors=True)

    class BarrierFacade(AgentHarnessFacade):
        def _artifact_commit_barrier(self, session, artifacts):
            assert adapter_state["collect_returned"] is True
            assert adapter_state["finalize_calls"] == 0
            assert artifacts == ["report.md"]
            assert (artifact_dir / "report.md").read_text(encoding="utf-8") == (
                "candidate"
            )
            if barrier is not None:
                barrier(artifact_dir / "report.md")
            if cancel_at_barrier:
                cancelled["value"] = True

    facade = BarrierFacade(artifact_dir, adapter=TransactionAdapter())
    session = facade.prepare(_request())
    return facade, session, artifact_dir, adapter_state


@pytest.mark.parametrize("operation", ["execute", "resume"])
@pytest.mark.parametrize("existing_content", [None, "previous-run"])
def test_facade_cancels_after_collect_returns_and_rolls_back_provisional_artifact(
    tmp_path,
    operation,
    existing_content,
):
    cancelled = {"value": False}
    facade, session, artifact_dir, adapter_state = _transaction_race_facade(
        tmp_path,
        cancelled=cancelled,
    )
    target = artifact_dir / "report.md"
    if existing_content is not None:
        target.write_text(existing_content, encoding="utf-8")
    events: list[tuple[str, dict]] = []

    if operation == "execute":
        result = facade.execute(
            session,
            is_cancelled=lambda: cancelled["value"],
            event_sink=lambda kind, payload: events.append((kind, payload)),
        )
    else:
        result = facade.resume(
            session,
            session.resume_token,
            is_cancelled=lambda: cancelled["value"],
            event_sink=lambda kind, payload: events.append((kind, payload)),
        )

    assert result.status == "cancelled"
    assert result.artifacts == []
    assert adapter_state["finalize_calls"] == 1
    if existing_content is None:
        assert not target.exists()
    else:
        assert target.read_text(encoding="utf-8") == existing_content
    assert not any(
        payload.get("harness_event_kind") == "completed"
        for _kind, payload in events
    )
    assert events[-1][1]["harness_event_kind"] == "cancelled"


@pytest.mark.parametrize("operation", ["execute", "resume"])
@pytest.mark.parametrize(
    ("existing_content", "newer_content", "expected_content"),
    [
        (None, None, None),
        ("previous-run", None, "previous-run"),
        ("previous-run", "newer-epoch", "newer-epoch"),
    ],
)
def test_facade_artifact_event_cancellation_rolls_back_only_owned_promotion(
    tmp_path,
    operation,
    existing_content,
    newer_content,
    expected_content,
):
    cancelled = {"value": False}
    facade, session, artifact_dir, adapter_state = _transaction_race_facade(
        tmp_path,
        cancelled=cancelled,
        cancel_at_barrier=False,
    )
    target = artifact_dir / "report.md"
    if existing_content is not None:
        target.write_text(existing_content, encoding="utf-8")
    events: list[tuple[str, dict]] = []

    def capture_event(kind, payload):
        events.append((kind, payload))
        if payload.get("harness_event_kind") == "artifact_created":
            if newer_content is not None:
                target.write_text(newer_content, encoding="utf-8")
            cancelled["value"] = True

    if operation == "execute":
        result = facade.execute(
            session,
            is_cancelled=lambda: cancelled["value"],
            event_sink=capture_event,
        )
    else:
        result = facade.resume(
            session,
            session.resume_token,
            is_cancelled=lambda: cancelled["value"],
            event_sink=capture_event,
        )

    assert result.status == "cancelled"
    assert result.artifacts == []
    assert adapter_state["finalize_calls"] == 1
    if expected_content is None:
        assert not target.exists()
    else:
        assert target.read_text(encoding="utf-8") == expected_content
    assert any(
        payload.get("harness_event_kind") == "artifact_created"
        for _kind, payload in events
    )
    assert not any(
        payload.get("harness_event_kind") == "completed"
        for _kind, payload in events
    )
    assert events[-1][1]["harness_event_kind"] == "cancelled"


@pytest.mark.parametrize("operation", ["execute", "resume"])
@pytest.mark.parametrize(
    ("existing_content", "newer_content", "expected_content"),
    [
        (None, None, None),
        ("previous-run", None, "previous-run"),
        ("previous-run", "newer-epoch", "newer-epoch"),
    ],
)
def test_facade_direct_candidate_uses_the_same_owned_commit_transaction(
    tmp_path,
    operation,
    existing_content,
    newer_content,
    expected_content,
):
    from app.services.harness_facade import AgentHarnessFacade, HarnessRunResult
    from app.services.provider_adapters.contracts import (
        ArtifactCandidate,
        ProviderCapabilities,
        ProviderResumeToken,
        ProviderSession,
    )

    artifact_dir = tmp_path / "artifacts"
    cancelled = {"value": False}

    class DirectCandidateAdapter:
        def capabilities(self):
            return ProviderCapabilities(
                streaming=True,
                tool_call=False,
                session_resume=True,
                structured_output=False,
                mcp=False,
                skills=False,
                cancellation=True,
            )

        def prepare(self, request):
            return ProviderSession(
                session_id="direct-session",
                provider=request.provider,
                resume_token=ProviderResumeToken(
                    provider=request.provider,
                    value="resume-token",
                ),
                artifact_dir=str(artifact_dir),
            )

        def execute(self, session, **_kwargs):
            (artifact_dir / "report.md").write_text("candidate", encoding="utf-8")
            return HarnessRunResult(
                session_id=session.session_id,
                status="completed",
                exit_code=0,
                started_at="2026-07-28T00:00:00Z",
                completed_at="2026-07-28T00:00:01Z",
                duration_ms=1000,
            )

        def resume(self, session, _resume_from, **_kwargs):
            return self.execute(session)

        def collect_artifacts(self, _session):
            return [ArtifactCandidate(path="report.md")]

    facade = AgentHarnessFacade(artifact_dir, adapter=DirectCandidateAdapter())
    session = facade.prepare(_request())
    target = artifact_dir / "report.md"
    if existing_content is not None:
        target.write_text(existing_content, encoding="utf-8")
    events: list[tuple[str, dict]] = []

    def capture_event(kind, payload):
        events.append((kind, payload))
        if payload.get("harness_event_kind") == "artifact_created":
            if newer_content is not None:
                target.write_text(newer_content, encoding="utf-8")
            cancelled["value"] = True

    if operation == "execute":
        result = facade.execute(
            session,
            is_cancelled=lambda: cancelled["value"],
            event_sink=capture_event,
        )
    else:
        result = facade.resume(
            session,
            session.resume_token,
            is_cancelled=lambda: cancelled["value"],
            event_sink=capture_event,
        )

    assert result.status == "cancelled"
    assert result.artifacts == []
    if expected_content is None:
        assert not target.exists()
    else:
        assert target.read_text(encoding="utf-8") == expected_content
    assert not any(
        payload.get("harness_event_kind") == "completed"
        for _kind, payload in events
    )
    assert not (artifact_dir.parent / ".harness-transactions").exists()


def _direct_cas_facade(tmp_path):
    from app.services.harness_facade import AgentHarnessFacade, HarnessRunResult
    from app.services.provider_adapters.contracts import (
        ArtifactCandidate,
        ProviderCapabilities,
        ProviderResumeToken,
        ProviderSession,
    )

    artifact_dir = tmp_path / "artifacts"

    class DirectCandidateAdapter:
        def capabilities(self):
            return ProviderCapabilities(
                streaming=True,
                tool_call=False,
                session_resume=True,
                structured_output=False,
                mcp=False,
                skills=False,
                cancellation=True,
            )

        def prepare(self, request):
            return ProviderSession(
                session_id="direct-cas-session",
                provider=request.provider,
                resume_token=ProviderResumeToken(
                    provider=request.provider,
                    value="resume-token",
                ),
                artifact_dir=str(artifact_dir),
            )

        def execute(self, session, **_kwargs):
            (artifact_dir / "report.md").write_text("candidate", encoding="utf-8")
            return HarnessRunResult(
                session_id=session.session_id,
                status="completed",
                exit_code=0,
                started_at="2026-07-28T00:00:00Z",
                completed_at="2026-07-28T00:00:01Z",
                duration_ms=1000,
            )

        def resume(self, session, _resume_from, **_kwargs):
            return self.execute(session)

        def collect_artifacts(self, _session):
            return [ArtifactCandidate(path="report.md")]

    facade = AgentHarnessFacade(artifact_dir, adapter=DirectCandidateAdapter())
    return facade, facade.prepare(_request()), artifact_dir


@pytest.mark.parametrize("candidate_mode", ["staged", "direct"])
@pytest.mark.parametrize("operation", ["execute", "resume"])
@pytest.mark.parametrize("existing_content", [None, "previous-run"])
@pytest.mark.parametrize(
    ("attack", "expected_reason"),
    [
        ("newer_content", "owner_fingerprint_mismatch"),
        ("outside_symlink", "artifact_is_symlink"),
    ],
)
def test_facade_commit_time_cas_rejects_artifact_ownership_or_boundary_drift(
    tmp_path,
    candidate_mode,
    operation,
    existing_content,
    attack,
    expected_reason,
):
    cancelled = {"value": False}
    if candidate_mode == "staged":
        facade, session, artifact_dir, _adapter_state = _transaction_race_facade(
            tmp_path,
            cancelled=cancelled,
            cancel_at_barrier=False,
        )
    else:
        facade, session, artifact_dir = _direct_cas_facade(tmp_path)
    target = artifact_dir / "report.md"
    if existing_content is not None:
        target.write_text(existing_content, encoding="utf-8")
    outside = tmp_path / "outside-report.md"
    outside.write_text("outside-newer-epoch", encoding="utf-8")
    events: list[tuple[str, dict]] = []

    def attack_before_event_returns(kind, payload):
        events.append((kind, payload))
        if payload.get("harness_event_kind") != "artifact_created":
            return
        if attack == "newer_content":
            target.write_text("newer-epoch", encoding="utf-8")
        else:
            target.unlink()
            target.symlink_to(outside)

    kwargs = {
        "is_cancelled": lambda: False,
        "event_sink": attack_before_event_returns,
    }
    if operation == "execute":
        result = facade.execute(session, **kwargs)
    else:
        result = facade.resume(session, session.resume_token, **kwargs)

    assert result.status == "failed"
    assert result.error == "artifact_commit_rejected"
    assert result.artifacts == []
    rejection = result.provider_diagnostics["artifact_commit_rejection"]
    assert rejection["code"] == "artifact_commit_cas_failed"
    assert rejection["rejected_artifacts"] == [
        {"artifact": "report.md", "reason": expected_reason}
    ]
    if attack == "newer_content":
        assert target.read_text(encoding="utf-8") == "newer-epoch"
    else:
        assert target.is_symlink()
        assert target.resolve() == outside.resolve()
    assert not any(
        payload.get("harness_event_kind") == "completed"
        for _kind, payload in events
    )
    assert events[-1][1]["harness_event_kind"] == "failed"
    assert events[-1][1]["artifact_count"] == 0
    assert not (artifact_dir.parent / ".harness-transactions").exists()
    assert not list(artifact_dir.glob(".report.md.harness-backup-*"))


def test_facade_commit_time_cas_rejects_parent_symlink_escape(tmp_path):
    from app.services.harness_facade import AgentHarnessFacade, HarnessRunResult
    from app.services.provider_adapters.contracts import (
        ArtifactCandidate,
        ProviderCapabilities,
        ProviderSession,
    )

    artifact_dir = tmp_path / "artifacts"
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "report.md").write_text("outside-newer", encoding="utf-8")

    class NestedDirectAdapter:
        def capabilities(self):
            return ProviderCapabilities(
                streaming=True,
                tool_call=False,
                session_resume=False,
                structured_output=False,
                mcp=False,
                skills=False,
                cancellation=True,
            )

        def prepare(self, request):
            return ProviderSession(
                session_id="parent-symlink-session",
                provider=request.provider,
                artifact_dir=str(artifact_dir),
            )

        def execute(self, session, **_kwargs):
            nested = artifact_dir / "nested"
            nested.mkdir()
            (nested / "report.md").write_text("candidate", encoding="utf-8")
            return HarnessRunResult(
                session_id=session.session_id,
                status="completed",
                exit_code=0,
                started_at="2026-07-28T00:00:00Z",
                completed_at="2026-07-28T00:00:01Z",
                duration_ms=1000,
            )

        def collect_artifacts(self, _session):
            return [ArtifactCandidate(path="nested/report.md")]

    facade = AgentHarnessFacade(artifact_dir, adapter=NestedDirectAdapter())
    session = facade.prepare(
        replace(
            _request(),
            task_bundle={
                "rendered_user_input": "analyze",
                "required_artifacts": ["nested/report.md"],
            },
        )
    )
    events: list[tuple[str, dict]] = []

    def replace_parent_with_symlink(kind, payload):
        events.append((kind, payload))
        if payload.get("harness_event_kind") == "artifact_created":
            shutil.rmtree(artifact_dir / "nested")
            (artifact_dir / "nested").symlink_to(
                outside_dir,
                target_is_directory=True,
            )

    result = facade.execute(session, event_sink=replace_parent_with_symlink)

    assert result.status == "failed"
    assert result.artifacts == []
    rejection = result.provider_diagnostics["artifact_commit_rejection"]
    assert rejection["rejected_artifacts"] == [
        {"artifact": "nested/report.md", "reason": "artifact_parent_symlink"}
    ]
    assert (artifact_dir / "nested").is_symlink()
    assert (artifact_dir / "nested" / "report.md").read_text(encoding="utf-8") == (
        "outside-newer"
    )
    assert not any(
        payload.get("harness_event_kind") == "completed"
        for _kind, payload in events
    )


def _parent_move_cas_facade(tmp_path, candidate_mode):
    from app.services.harness_facade import AgentHarnessFacade, HarnessRunResult
    from app.services.provider_adapters.contracts import (
        ArtifactCandidate,
        ProviderCapabilities,
        ProviderResumeToken,
        ProviderSession,
    )

    artifact_dir = tmp_path / "artifacts"
    staging_dir = tmp_path / "staging"
    staged_report = staging_dir / "nested" / "report.md"

    class ParentMoveAdapter:
        def capabilities(self):
            return ProviderCapabilities(
                streaming=True,
                tool_call=False,
                session_resume=True,
                structured_output=False,
                mcp=False,
                skills=False,
                cancellation=True,
            )

        def prepare(self, request):
            return ProviderSession(
                session_id=f"parent-move-{candidate_mode}",
                provider=request.provider,
                resume_token=ProviderResumeToken(
                    provider=request.provider,
                    value="resume-token",
                ),
                artifact_dir=str(staging_dir),
            )

        def execute(self, session, **_kwargs):
            if candidate_mode == "staged":
                staged_report.parent.mkdir(parents=True, exist_ok=True)
                staged_report.write_text("candidate", encoding="utf-8")
            else:
                target = artifact_dir / "nested" / "report.md"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("candidate", encoding="utf-8")
            return HarnessRunResult(
                session_id=session.session_id,
                status="completed",
                exit_code=0,
                started_at="2026-07-28T00:00:00Z",
                completed_at="2026-07-28T00:00:01Z",
                duration_ms=1000,
            )

        def resume(self, session, _resume_from, **_kwargs):
            return self.execute(session)

        def collect_artifacts(self, _session):
            metadata = (
                {"staged_path": str(staged_report)}
                if candidate_mode == "staged"
                else {}
            )
            return [ArtifactCandidate(path="nested/report.md", metadata=metadata)]

        def finalize_artifacts(self, _session):
            shutil.rmtree(staging_dir, ignore_errors=True)

    facade = AgentHarnessFacade(artifact_dir, adapter=ParentMoveAdapter())
    session = facade.prepare(
        replace(
            _request(),
            task_bundle={
                "rendered_user_input": "analyze",
                "required_artifacts": ["nested/report.md"],
            },
        )
    )
    return facade, session, artifact_dir


@pytest.mark.parametrize("candidate_mode", ["staged", "direct"])
@pytest.mark.parametrize("operation", ["execute", "resume"])
@pytest.mark.parametrize("existing_content", [None, "previous-run"])
def test_facade_safe_rollback_never_follows_moved_parent_symlink(
    tmp_path,
    candidate_mode,
    operation,
    existing_content,
):
    facade, session, artifact_dir = _parent_move_cas_facade(
        tmp_path,
        candidate_mode,
    )
    target = artifact_dir / "nested" / "report.md"
    if existing_content is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(existing_content, encoding="utf-8")
    moved_parent = tmp_path / "moved-parent"
    events: list[tuple[str, dict]] = []

    def move_parent_before_event_returns(kind, payload):
        events.append((kind, payload))
        if payload.get("harness_event_kind") != "artifact_created":
            return
        target.parent.replace(moved_parent)
        target.parent.symlink_to(moved_parent, target_is_directory=True)
        assert (moved_parent / "report.md").stat().st_ino == target.stat().st_ino

    kwargs = {
        "is_cancelled": lambda: False,
        "event_sink": move_parent_before_event_returns,
    }
    if operation == "execute":
        result = facade.execute(session, **kwargs)
    else:
        result = facade.resume(session, session.resume_token, **kwargs)

    outside_target = moved_parent / "report.md"
    assert result.status == "failed"
    assert result.artifacts == []
    assert result.provider_diagnostics["artifact_commit_rejection"][
        "rejected_artifacts"
    ] == [
        {"artifact": "nested/report.md", "reason": "artifact_parent_symlink"}
    ]
    assert outside_target.read_text(encoding="utf-8") == "candidate"
    assert target.parent.is_symlink()
    assert not list(moved_parent.glob(".*.harness-backup-*"))
    assert not (artifact_dir.parent / ".harness-transactions").exists()
    assert not any(
        payload.get("harness_event_kind") == "completed"
        for _kind, payload in events
    )
    assert events[-1][1]["harness_event_kind"] == "failed"


def test_facade_returns_structured_unsupported_without_calling_resume(tmp_path):
    from app.services.harness_facade import AgentHarnessFacade
    from app.services.provider_adapters.contracts import ProviderUnsupported

    adapter = _ContractAdapter(tmp_path, resume_supported=False)
    facade = AgentHarnessFacade(tmp_path, adapter=adapter)
    session = facade.prepare(_request())

    result = facade.resume(session, session.resume_token)

    assert isinstance(result, ProviderUnsupported)
    assert result.code == "unsupported_capability"
    assert result.operation == "resume"
    assert result.capability == "session_resume"
    assert adapter.resumed_with is None


def test_facade_is_the_only_artifact_candidate_security_boundary(tmp_path):
    from app.services.harness_facade import AgentHarnessFacade

    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (artifact_dir / "report.md").write_text("declared", encoding="utf-8")
    (artifact_dir / "undeclared.md").write_text("hidden", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (artifact_dir / "linked.md").symlink_to(outside)
    adapter = _ContractAdapter(artifact_dir)
    facade = AgentHarnessFacade(artifact_dir, adapter=adapter)
    session = facade.prepare(_request())

    assert facade.collect_artifacts(session) == ["report.md"]


@pytest.mark.parametrize(
    ("token_provider", "token_value", "expected_code"),
    [
        ("claude", "claude-session", "resume_token_provider_mismatch"),
        ("codex", "", "resume_token_missing"),
    ],
)
def test_codex_facade_rejects_invalid_resume_token_without_false_started_event(
    tmp_path,
    token_provider,
    token_value,
    expected_code,
):
    from app.services.harness_facade import AgentHarnessFacade
    from app.services.provider_adapters.codex_cli import CodexCliAdapter
    from app.services.provider_adapters.contracts import (
        ProviderResumeToken,
        ProviderUnsupported,
    )

    request = replace(
        _request(),
        provider="codex",
        command=["codex"],
        run_id="codex-session",
    )
    facade = AgentHarnessFacade(tmp_path, adapter=CodexCliAdapter(tmp_path))
    session = facade.prepare(request)
    events: list[tuple[str, dict]] = []

    result = facade.resume(
        session,
        ProviderResumeToken(provider=token_provider, value=token_value),
        event_sink=lambda kind, payload: events.append((kind, payload)),
    )

    assert isinstance(result, ProviderUnsupported)
    assert result.code == expected_code
    assert events == []


@pytest.mark.parametrize(
    ("provider", "resume_supported"),
    [
        ("builtin", False),
        ("codex", True),
        ("claude", True),
        ("opencode", True),
    ],
)
def test_real_provider_adapters_obey_result_and_unsupported_lifecycle_contract(
    monkeypatch,
    tmp_path,
    provider,
    resume_supported,
):
    """Every production Adapter returns either a run result or typed unsupported."""
    from app.services import agent_cli_bridge
    from app.services.harness_facade import HarnessRunResult
    from app.services.provider_adapters.builtin_model import BuiltinModelAdapter
    from app.services.provider_adapters.claude_code import ClaudeCliAdapter
    from app.services.provider_adapters.cli_base import CliProviderRunResult
    from app.services.provider_adapters.codex_cli import CodexCliAdapter
    from app.services.provider_adapters.contracts import (
        ProviderResumeToken,
        ProviderUnsupported,
    )
    from app.services.provider_adapters.opencode import OpenCodeAdapter

    async def completed_runtime(**_kwargs):
        yield "completed"

    monkeypatch.setattr(agent_cli_bridge, "stream_agent_runtime", completed_runtime)
    adapter_types = {
        "codex": CodexCliAdapter,
        "claude": ClaudeCliAdapter,
        "opencode": OpenCodeAdapter,
    }
    if provider == "builtin":
        adapter = BuiltinModelAdapter(
            tmp_path,
            execute_callable=lambda **_kwargs: {"status": "completed"},
        )
        request = replace(
            _request(),
            provider="builtin",
            command=[],
            run_id="builtin-session",
        )
    else:
        adapter = adapter_types[provider](tmp_path)
        request = replace(
            _request(),
            provider=provider,
            command=[provider],
            run_id=f"{provider}-session",
        )

    session = adapter.prepare(request)
    executed = adapter.execute(session)
    resume_events: list[tuple[str, dict]] = []
    resumed = adapter.resume(
        session,
        ProviderResumeToken(provider="other-provider", value="opaque-token"),
        event_sink=lambda kind, payload: resume_events.append((kind, payload)),
    )

    assert adapter.capabilities().session_resume is resume_supported
    assert not isinstance(executed, ProviderUnsupported)
    assert isinstance(executed, (HarnessRunResult, CliProviderRunResult))
    assert executed.status == "completed"
    assert isinstance(resumed, ProviderUnsupported)
    assert resumed.operation == "resume"
    assert resumed.capability == "session_resume"
    assert resume_events == []
