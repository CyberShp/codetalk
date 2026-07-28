import json
import multiprocessing
from pathlib import Path
from typing import Any

import pytest


def _claim_child_session_in_separate_process(
    attempt_dir: str,
    start: Any,
    results: Any,
) -> None:
    """Claim the same deterministic session from a fresh interpreter process."""
    from app.services.child_session import ChildSessionStore

    start.wait(timeout=10)
    claim = ChildSessionStore(
        attempt_dir,
        parent_attempt_id="attempt-01",
        parent_node_id="research",
    ).claim_or_inspect(
        session_key="review-source",
        provider="builtin",
        input_summary={"goal": "review the supplied source"},
    )
    results.put((claim.disposition, claim.session.status))


def test_claim_or_inspect_reuses_completed_session_snapshot_and_output(
    tmp_path: Path,
) -> None:
    from app.services.child_session import ChildSessionStore

    store = ChildSessionStore(
        tmp_path,
        parent_attempt_id="attempt-01",
        parent_node_id="research",
    )

    claimed = store.claim_or_inspect(
        session_key="review-source",
        provider="builtin",
        input_summary={"goal": "review the supplied source"},
    )
    assert claimed.disposition == "claimed"
    assert claimed.session.status == "running"

    completed = store.complete(
        claimed.session.session_id,
        {"child_outputs": {"report.md": "artifacts/report.md"}},
    )

    replay = store.claim_or_inspect(
        session_key="review-source",
        provider="builtin",
        input_summary={"goal": "review the supplied source"},
    )

    assert replay.disposition == "completed"
    assert replay.snapshot == completed.to_snapshot()
    assert replay.output == {"child_outputs": {"report.md": "artifacts/report.md"}}


def test_claim_or_inspect_allows_only_one_concurrent_process_claim(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_claim_child_session_in_separate_process,
            args=(str(tmp_path), start, results),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    claims = [results.get(timeout=5) for _ in processes]

    assert sorted(claims) == [("claimed", "running"), ("in_progress", "running")]


@pytest.mark.parametrize(
    ("status", "expected_disposition", "expected_reason"),
    [
        ("failed", "failed", "prior_session_failed"),
        ("unexpected", "indeterminate", "unknown_prior_status"),
        ("completed", "indeterminate", "completed_output_missing"),
    ],
)
def test_claim_or_inspect_fails_closed_for_nonreusable_prior_sessions(
    tmp_path: Path,
    status: str,
    expected_disposition: str,
    expected_reason: str,
) -> None:
    from app.services.child_session import ChildSessionStore

    store = ChildSessionStore(
        tmp_path,
        parent_attempt_id="attempt-01",
        parent_node_id="research",
    )
    session = store.create(
        session_key="review-source",
        provider="builtin",
        input_summary={"goal": "review the supplied source"},
    )
    store.update_status(session.session_id, status)

    inspected = store.claim_or_inspect(
        session_key="review-source",
        provider="builtin",
        input_summary={"goal": "review the supplied source"},
    )

    assert inspected.disposition == expected_disposition
    assert inspected.reason == expected_reason
    assert inspected.session.status == status


def test_child_session_is_stable_and_checkpoint_serializable(tmp_path: Path) -> None:
    from app.services.child_session import ChildSessionStore

    store = ChildSessionStore(
        tmp_path,
        parent_attempt_id="attempt-01",
        parent_node_id="research",
    )

    first = store.create(
        session_key="review-source",
        provider="codex-cli",
        input_summary={"goal": "review the supplied source"},
    )
    second = store.create(
        session_key="review-source",
        provider="codex-cli",
        input_summary={"goal": "review the supplied source"},
    )

    assert first.session_id == second.session_id
    assert first.session_id.startswith("child_")
    assert "/" not in first.session_id
    assert first.status == "queued"
    assert first.artifact_dir == (
        f"nodes/research/child_sessions/{first.session_id}/artifacts"
    )
    assert json.loads(json.dumps(first.to_snapshot())) == {
        "artifact_dir": first.artifact_dir,
        "input_summary": {"goal": "review the supplied source"},
        "parent_attempt_id": "attempt-01",
        "parent_node_id": "research",
        "provider": "codex-cli",
        "session_id": first.session_id,
        "status": "queued",
    }


def test_child_session_events_and_status_are_isolated_below_parent_node(
    tmp_path: Path,
) -> None:
    from app.services.child_session import ChildSessionStore

    store = ChildSessionStore(
        tmp_path,
        parent_attempt_id="attempt-01",
        parent_node_id="research",
    )
    session = store.create(
        session_key="review-source",
        provider="builtin",
        input_summary="source review",
    )

    running = store.update_status(session.session_id, "running")
    event = store.append_event(
        session.session_id,
        "child_output",
        {"message": "working"},
    )

    assert running.status == "running"
    assert event["event_type"] == "child_output"
    assert event["session_id"] == session.session_id
    session_dir = (
        tmp_path / "nodes" / "research" / "child_sessions" / session.session_id
    )
    assert (session_dir / "artifacts").is_dir()
    assert [json.loads(line) for line in (session_dir / "events.jsonl").read_text().splitlines()] == [
        event
    ]
    assert store.snapshot(session.session_id)["status"] == "running"


def test_child_session_collects_only_declared_regular_outputs(tmp_path: Path) -> None:
    from app.services.child_session import ChildSessionStore

    store = ChildSessionStore(
        tmp_path,
        parent_attempt_id="attempt-01",
        parent_node_id="research",
    )
    session = store.create(
        session_key="write-report",
        provider="builtin",
        input_summary={"goal": "write a report"},
    )
    artifact_dir = tmp_path / session.artifact_dir
    (artifact_dir / "report.md").write_text("# report\n", encoding="utf-8")
    (artifact_dir / "private.txt").write_text("not declared\n", encoding="utf-8")

    assert store.collect_declared_outputs(session.session_id, ["report.md"]) == {
        "report.md": session.artifact_dir + "/report.md"
    }
    assert store.collect_declared_outputs(session.session_id, []) == {}


@pytest.mark.parametrize("declared_output", ["../outside.txt", "/outside.txt", "dir/../../outside.txt"])
def test_child_session_rejects_traversal_in_declared_outputs(
    tmp_path: Path,
    declared_output: str,
) -> None:
    from app.services.child_session import ChildSessionStore, ChildSessionValidationError

    store = ChildSessionStore(
        tmp_path,
        parent_attempt_id="attempt-01",
        parent_node_id="research",
    )
    session = store.create(
        session_key="write-report",
        provider="builtin",
        input_summary="write report",
    )

    with pytest.raises(ChildSessionValidationError, match="declared output"):
        store.collect_declared_outputs(session.session_id, [declared_output])


def test_child_session_rejects_symlinked_declared_output(tmp_path: Path) -> None:
    from app.services.child_session import ChildSessionStore, ChildSessionValidationError

    store = ChildSessionStore(
        tmp_path,
        parent_attempt_id="attempt-01",
        parent_node_id="research",
    )
    session = store.create(
        session_key="write-report",
        provider="builtin",
        input_summary="write report",
    )
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    (tmp_path / session.artifact_dir / "report.md").symlink_to(outside)

    with pytest.raises(ChildSessionValidationError, match="symlink"):
        store.collect_declared_outputs(session.session_id, ["report.md"])
