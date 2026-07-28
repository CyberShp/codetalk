from __future__ import annotations

import json
import multiprocessing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _claim_execution_lease(
    attempt_dir: str,
    start: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    from app.services.workflow_execution_lease import WorkflowExecutionLeaseStore

    assert start.wait(timeout=10)
    lease = WorkflowExecutionLeaseStore(
        attempt_dir,
        attempt_id="attempt-multiprocess",
    ).acquire(ttl=timedelta(seconds=30))
    results.put(lease.owner_token if lease is not None else None)


def test_execution_lease_acquire_heartbeat_release_and_reacquire(tmp_path: Path) -> None:
    from app.services.workflow_execution_lease import WorkflowExecutionLeaseStore

    store = WorkflowExecutionLeaseStore(tmp_path, attempt_id="attempt-1")
    started_at = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)

    lease = store.acquire(ttl=timedelta(seconds=30), now=started_at)

    assert lease is not None
    assert lease.attempt_id == "attempt-1"
    assert lease.acquired_at == started_at
    assert lease.heartbeat_at == started_at
    assert lease.expires_at == started_at + timedelta(seconds=30)
    assert store.acquire(ttl=timedelta(seconds=30), now=started_at + timedelta(seconds=1)) is None

    renewed = store.heartbeat(
        lease,
        ttl=timedelta(seconds=45),
        now=started_at + timedelta(seconds=5),
    )

    assert renewed.owner_token == lease.owner_token
    assert renewed.acquired_at == started_at
    assert renewed.heartbeat_at == started_at + timedelta(seconds=5)
    assert renewed.expires_at == started_at + timedelta(seconds=50)
    assert store.load() == renewed
    assert store.release(lease)
    assert store.load() is None
    assert store.acquire(ttl=timedelta(seconds=30), now=started_at + timedelta(seconds=6)) is not None


def test_execution_lease_allows_takeover_only_after_expiry(tmp_path: Path) -> None:
    from app.services.workflow_execution_lease import (
        ExecutionLeaseLost,
        WorkflowExecutionLeaseStore,
    )

    store = WorkflowExecutionLeaseStore(tmp_path, attempt_id="attempt-1")
    started_at = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
    original = store.acquire(ttl=timedelta(seconds=10), now=started_at)

    assert original is not None
    assert store.acquire(ttl=timedelta(seconds=10), now=started_at + timedelta(seconds=9)) is None

    replacement = store.acquire(ttl=timedelta(seconds=10), now=started_at + timedelta(seconds=11))

    assert replacement is not None
    assert replacement.owner_token != original.owner_token
    assert store.load() == replacement
    with pytest.raises(ExecutionLeaseLost, match="no longer owns"):
        store.heartbeat(
            original,
            ttl=timedelta(seconds=10),
            now=started_at + timedelta(seconds=12),
        )
    assert not store.release(original)
    assert store.load() == replacement


def test_execution_lease_fails_closed_on_malformed_record(tmp_path: Path) -> None:
    from app.services.workflow_execution_lease import (
        ExecutionLeaseValidationError,
        WorkflowExecutionLeaseStore,
    )

    store = WorkflowExecutionLeaseStore(tmp_path, attempt_id="attempt-1")
    store.lease_path.parent.mkdir(parents=True, exist_ok=True)
    malformed = {"execution_lease_version": 1, "attempt_id": "attempt-1"}
    store.lease_path.write_text(json.dumps(malformed), encoding="utf-8")

    with pytest.raises(ExecutionLeaseValidationError, match="owner_token"):
        store.acquire(
            ttl=timedelta(seconds=10),
            now=datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
        )

    assert json.loads(store.lease_path.read_text(encoding="utf-8")) == malformed


def test_execution_lease_has_one_winner_across_processes(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_claim_execution_lease,
            args=(str(tmp_path), start, results),
        )
        for _ in range(4)
    ]

    for process in processes:
        process.start()
    start.set()
    outcomes = [results.get(timeout=15) for _ in processes]
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    winners = [owner_token for owner_token in outcomes if owner_token is not None]
    assert len(winners) == 1
    assert len(set(winners)) == 1
