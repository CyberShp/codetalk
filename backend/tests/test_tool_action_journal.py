"""Durability contracts for Attempt-local Tool action records."""

from __future__ import annotations

import json
import multiprocessing
import threading
from pathlib import Path

import pytest


def _begin(journal, *, arguments: dict[str, object] | None = None):
    return journal.begin(
        task_id="task-1",
        attempt_id="attempt-1",
        node_id="tool-node",
        tool_id="text.preview",
        frozen_arguments=arguments or {"text": "hello", "limit": 4},
    )


def _claim_tool_action(
    attempt_dir: str,
    start: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    from app.services.tool_action_journal import ToolActionJournal

    assert start.wait(timeout=10)
    results.put(_begin(ToolActionJournal(attempt_dir)).disposition)


def test_action_key_is_canonical_over_attempt_node_tool_and_frozen_arguments() -> None:
    from app.services.tool_action_journal import compute_tool_action_key

    key = compute_tool_action_key(
        attempt_id="attempt-1",
        node_id="tool-node",
        tool_id="text.preview",
        frozen_arguments={"text": "hello", "options": {"limit": 4, "trim": True}},
    )

    assert key == compute_tool_action_key(
        attempt_id="attempt-1",
        node_id="tool-node",
        tool_id="text.preview",
        frozen_arguments={"options": {"trim": True, "limit": 4}, "text": "hello"},
    )
    assert key != compute_tool_action_key(
        attempt_id="attempt-2",
        node_id="tool-node",
        tool_id="text.preview",
        frozen_arguments={"text": "hello", "options": {"limit": 4, "trim": True}},
    )
    assert key != compute_tool_action_key(
        attempt_id="attempt-1",
        node_id="tool-node",
        tool_id="text.preview",
        frozen_arguments={"text": "goodbye", "options": {"limit": 4, "trim": True}},
    )


def test_begin_creates_durable_prepared_record_and_never_executes_a_tool(
    tmp_path: Path,
) -> None:
    from app.services.tool_action_journal import ToolActionJournal

    journal = ToolActionJournal(tmp_path)

    decision = _begin(journal)

    assert decision.disposition == "execute"
    assert decision.record.status == "prepared"
    assert decision.record.output is None
    record_path = tmp_path / "tool-actions" / f"{decision.record.action_key.removeprefix('sha256:')}.json"
    assert ":" not in record_path.name
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    assert payload["status"] == "prepared"
    assert payload["attempt_id"] == "attempt-1"
    assert payload["frozen_arguments"] == {"limit": 4, "text": "hello"}


def test_completed_action_is_replayed_without_overwriting_its_output(tmp_path: Path) -> None:
    from app.services.tool_action_journal import ToolActionConflict, ToolActionJournal

    journal = ToolActionJournal(tmp_path)
    prepared = _begin(journal)
    completed = journal.complete(prepared.record, output={"preview": "hell"})

    replay = _begin(ToolActionJournal(tmp_path))

    assert completed.status == "completed"
    assert replay.disposition == "completed"
    assert replay.record.output == {"preview": "hell"}
    assert journal.complete(prepared.record, output={"preview": "hell"}) == completed
    with pytest.raises(ToolActionConflict, match="completed output"):
        journal.complete(prepared.record, output={"preview": "different"})


def test_crash_after_prepare_is_indeterminate_and_must_not_be_rerun(tmp_path: Path) -> None:
    from app.services.tool_action_journal import ToolActionJournal

    prepared = _begin(ToolActionJournal(tmp_path))

    after_restart = _begin(ToolActionJournal(tmp_path))

    assert prepared.disposition == "execute"
    assert after_restart.disposition == "indeterminate"
    assert after_restart.record.status == "prepared"
    assert after_restart.record.action_key == prepared.record.action_key


def test_crash_after_completion_replays_durable_output_without_rerunning(tmp_path: Path) -> None:
    from app.services.tool_action_journal import ToolActionJournal

    journal = ToolActionJournal(tmp_path)
    prepared = _begin(journal)
    journal.complete(prepared.record, output={"preview": "hell", "metadata": {"cached": True}})

    after_restart = _begin(ToolActionJournal(tmp_path))

    assert after_restart.disposition == "completed"
    assert after_restart.record.output == {
        "preview": "hell",
        "metadata": {"cached": True},
    }


def test_failed_action_is_terminal_and_preserves_its_structured_failure(tmp_path: Path) -> None:
    from app.services.tool_action_journal import ToolActionJournal

    journal = ToolActionJournal(tmp_path)
    prepared = _begin(journal)
    journal.fail(prepared.record, error={"code": "permission_denied"})

    replay = _begin(ToolActionJournal(tmp_path))

    assert replay.disposition == "failed"
    assert replay.record.error == {"code": "permission_denied"}


def test_transition_rejects_a_record_with_wrong_attempt_ownership(tmp_path: Path) -> None:
    from app.services.tool_action_journal import ToolActionConflict, ToolActionJournal

    journal = ToolActionJournal(tmp_path)
    prepared = _begin(journal)
    foreign_record = prepared.record.__class__(
        **{**prepared.record.__dict__, "task_id": "task-2"}
    )

    with pytest.raises(ToolActionConflict, match="ownership"):
        journal.complete(foreign_record, output={"preview": "hell"})

    assert journal.load(prepared.record.action_key) == prepared.record


def test_load_rejects_invalid_persisted_schema_and_key_mismatch(tmp_path: Path) -> None:
    from app.services.tool_action_journal import (
        ToolActionJournal,
        ToolActionValidationError,
    )

    journal = ToolActionJournal(tmp_path)
    prepared = _begin(journal)
    record_path = tmp_path / "tool-actions" / f"{prepared.record.action_key.removeprefix('sha256:')}.json"
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    payload["action_key"] = "sha256:forged"
    record_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ToolActionValidationError, match="action_key"):
        journal.load(prepared.record.action_key)


def test_process_local_lock_allows_only_one_new_execution_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.tool_action_journal as journal_module

    first_writer_entered = threading.Event()
    release_first_writer = threading.Event()
    original_write = journal_module._write_json_atomic

    def delayed_first_write(path: Path, payload: dict[str, object]) -> None:
        if not first_writer_entered.is_set():
            first_writer_entered.set()
            assert release_first_writer.wait(timeout=5)
        original_write(path, payload)

    monkeypatch.setattr(journal_module, "_write_json_atomic", delayed_first_write)
    decisions = []

    first = threading.Thread(
        target=lambda: decisions.append(_begin(journal_module.ToolActionJournal(tmp_path)))
    )
    second = threading.Thread(
        target=lambda: decisions.append(_begin(journal_module.ToolActionJournal(tmp_path)))
    )
    first.start()
    assert first_writer_entered.wait(timeout=5)
    second.start()
    release_first_writer.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert sorted(decision.disposition for decision in decisions) == [
        "execute",
        "indeterminate",
    ]


def test_interprocess_lock_allows_only_one_new_execution_decision(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(target=_claim_tool_action, args=(str(tmp_path), start, results))
        for _ in range(4)
    ]

    for process in processes:
        process.start()
    start.set()
    dispositions = [results.get(timeout=15) for _ in processes]
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    assert dispositions.count("execute") == 1
    assert dispositions.count("indeterminate") == 3
