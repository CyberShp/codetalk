import json
import threading
from pathlib import Path

import pytest


def test_checkpoint_seed_requires_matching_frozen_execution_key(tmp_path: Path) -> None:
    from app.services.node_checkpoint import (
        NodeCheckpointStore,
        compute_node_idempotency_key,
    )

    store = NodeCheckpointStore(tmp_path)
    node_definition = {
        "workflow_version_id": "workflow-v1",
        "id": "agent",
        "config": {"goal": "write report", "timeout_sec": 120},
    }
    frozen_inputs = {"target": "storage", "prompt": "analyze"}
    upstream_artifact_hashes = {"source.md": "sha256:source"}
    matching_key = compute_node_idempotency_key(
        node_definition=node_definition,
        frozen_inputs=frozen_inputs,
        upstream_artifact_hashes=upstream_artifact_hashes,
    )
    assert matching_key == compute_node_idempotency_key(
        node_definition={
            "config": {"timeout_sec": 120, "goal": "write report"},
            "id": "agent",
            "workflow_version_id": "workflow-v1",
        },
        frozen_inputs={"prompt": "analyze", "target": "storage"},
        upstream_artifact_hashes={"source.md": "sha256:source"},
    )
    seed_result = {
        "node_status": "completed",
        "artifact_dir": "nodes/agent",
        "validated_outputs": {"report.md": "sha256:report"},
        "handler_result": {"kind": "agent", "provider": "builtin"},
        "governance": {"status": "not_requested"},
    }
    checkpoint = store.commit_completed(
        task_id="task-1",
        attempt_id="attempt-1",
        node_id="agent",
        idempotency_key=matching_key,
        input_hash="sha256:input",
        output_artifact_hashes={"report.md": "sha256:report"},
        result_snapshot=seed_result,
    )

    assert (
        store.load_reusable_seed(
            "agent",
            expected_idempotency_key=matching_key,
        )
        == seed_result
    )
    assert store.load_reusable_seed(
        "agent",
        expected_idempotency_key=compute_node_idempotency_key(
            node_definition={**node_definition, "workflow_version_id": "workflow-v2"},
            frozen_inputs=frozen_inputs,
            upstream_artifact_hashes=upstream_artifact_hashes,
        ),
    ) is None
    assert store.load_reusable_seed(
        "agent",
        expected_idempotency_key=compute_node_idempotency_key(
            node_definition={**node_definition, "id": "agent-retry"},
            frozen_inputs=frozen_inputs,
            upstream_artifact_hashes=upstream_artifact_hashes,
        ),
    ) is None
    assert store.load_reusable_seed(
        "agent",
        expected_idempotency_key=compute_node_idempotency_key(
            node_definition=node_definition,
            frozen_inputs={"target": "storage", "prompt": "reanalyze"},
            upstream_artifact_hashes=upstream_artifact_hashes,
        ),
    ) is None
    assert store.load_reusable_seed(
        "agent",
        expected_idempotency_key=compute_node_idempotency_key(
            node_definition=node_definition,
            frozen_inputs=frozen_inputs,
            upstream_artifact_hashes={"source.md": "sha256:changed-source"},
        ),
    ) is None


def test_checkpoint_rejects_non_json_seed_snapshot(tmp_path: Path) -> None:
    from app.services.node_checkpoint import (
        CheckpointValidationError,
        NodeCheckpointStore,
    )

    with pytest.raises(CheckpointValidationError, match="result_snapshot"):
        NodeCheckpointStore(tmp_path).commit_completed(
            task_id="task-1",
            attempt_id="attempt-1",
            node_id="agent",
            idempotency_key="sha256:same",
            input_hash="sha256:input",
            result_snapshot={"artifact_dir": Path("nodes/agent")},
        )


def test_checkpoint_commit_writes_atomic_node_record(tmp_path: Path) -> None:
    from app.services.node_checkpoint import NodeCheckpointStore

    store = NodeCheckpointStore(tmp_path)

    checkpoint = store.commit_completed(
        task_id="task-1",
        attempt_id="attempt-1",
        node_id="agent",
        idempotency_key="sha256:first",
        input_hash="sha256:input",
        output_artifact_hashes={"report.md": "sha256:report"},
        provider_session={"provider": "builtin", "session_id": "session-1"},
    )

    assert checkpoint.revision == 1
    assert checkpoint.status == "completed"
    checkpoint_path = tmp_path / "checkpoints" / "agent.json"
    assert checkpoint_path.is_file()
    assert not list((tmp_path / "checkpoints").glob("agent.json.tmp-*"))
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert payload["checkpoint_version"] == 1
    assert payload["task_id"] == "task-1"
    assert payload["attempt_id"] == "attempt-1"
    assert payload["node_id"] == "agent"
    assert payload["revision"] == 1
    assert payload["idempotency_key"] == "sha256:first"
    assert payload["input_hash"] == "sha256:input"
    assert payload["output_artifact_hashes"] == {"report.md": "sha256:report"}
    assert payload["provider_session"] == {"provider": "builtin", "session_id": "session-1"}
    assert payload["completed_at"]


def test_checkpoint_commit_is_idempotent_for_same_key_and_hashes(tmp_path: Path) -> None:
    from app.services.node_checkpoint import NodeCheckpointStore

    store = NodeCheckpointStore(tmp_path)
    first = store.commit_completed(
        task_id="task-1",
        attempt_id="attempt-1",
        node_id="agent",
        idempotency_key="sha256:same",
        input_hash="sha256:input",
        output_artifact_hashes={"report.md": "sha256:report"},
    )
    second = store.commit_completed(
        task_id="task-1",
        attempt_id="attempt-1",
        node_id="agent",
        idempotency_key="sha256:same",
        input_hash="sha256:input",
        output_artifact_hashes={"report.md": "sha256:report"},
    )

    assert second == first
    assert store.load("agent") == first


def test_checkpoint_commit_advances_revision_for_new_idempotency_key(tmp_path: Path) -> None:
    from app.services.node_checkpoint import NodeCheckpointStore

    store = NodeCheckpointStore(tmp_path)
    store.commit_completed(
        task_id="task-1",
        attempt_id="attempt-1",
        node_id="agent",
        idempotency_key="sha256:first",
        input_hash="sha256:input-a",
        output_artifact_hashes={"report.md": "sha256:report-a"},
    )

    checkpoint = store.commit_completed(
        task_id="task-1",
        attempt_id="attempt-1",
        node_id="agent",
        idempotency_key="sha256:second",
        input_hash="sha256:input-b",
        output_artifact_hashes={"report.md": "sha256:report-b"},
    )

    assert checkpoint.revision == 2
    assert checkpoint.idempotency_key == "sha256:second"
    assert checkpoint.input_hash == "sha256:input-b"
    assert checkpoint.output_artifact_hashes == {"report.md": "sha256:report-b"}


def test_checkpoint_rejects_conflicting_repeat_for_same_idempotency_key(tmp_path: Path) -> None:
    from app.services.node_checkpoint import CheckpointConflict, NodeCheckpointStore

    store = NodeCheckpointStore(tmp_path)
    store.commit_completed(
        task_id="task-1",
        attempt_id="attempt-1",
        node_id="agent",
        idempotency_key="sha256:same",
        input_hash="sha256:input-a",
        output_artifact_hashes={"report.md": "sha256:report-a"},
    )

    with pytest.raises(CheckpointConflict):
        store.commit_completed(
            task_id="task-1",
            attempt_id="attempt-1",
            node_id="agent",
            idempotency_key="sha256:same",
            input_hash="sha256:input-b",
            output_artifact_hashes={"report.md": "sha256:report-b"},
        )

    assert store.load("agent").input_hash == "sha256:input-a"


def test_checkpoint_rejects_cross_attempt_identity_overwrite(tmp_path: Path) -> None:
    from app.services.node_checkpoint import CheckpointConflict, NodeCheckpointStore

    store = NodeCheckpointStore(tmp_path)
    original = store.commit_completed(
        task_id="task-a",
        attempt_id="attempt-a",
        node_id="agent",
        idempotency_key="sha256:first",
        input_hash="sha256:input-a",
    )

    with pytest.raises(CheckpointConflict, match="节点检查点与当前运行不一致"):
        store.commit_completed(
            task_id="task-b",
            attempt_id="attempt-b",
            node_id="agent",
            idempotency_key="sha256:second",
            input_hash="sha256:input-b",
        )

    assert store.load("agent") == original


def test_checkpoint_conflicts_use_localized_identifier_free_error(tmp_path: Path) -> None:
    from app.services.node_checkpoint import CheckpointConflict, NodeCheckpointStore

    node_id = "internal.release-gate.v3"
    store = NodeCheckpointStore(tmp_path)
    store.commit_completed(
        task_id="task-a",
        attempt_id="attempt-a",
        node_id=node_id,
        idempotency_key="sha256:first",
        input_hash="sha256:input-a",
    )

    with pytest.raises(CheckpointConflict) as raised:
        store.commit_completed(
            task_id="task-b",
            attempt_id="attempt-b",
            node_id=node_id,
            idempotency_key="sha256:second",
            input_hash="sha256:input-b",
        )

    assert str(raised.value) == "节点检查点与当前运行不一致，请重试。"
    assert node_id not in str(raised.value)
    checkpoint = store.load(node_id)
    assert checkpoint is not None
    assert checkpoint.node_id == node_id


def test_checkpoint_commit_serializes_concurrent_revision_updates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.node_checkpoint as checkpoint_module

    store_a = checkpoint_module.NodeCheckpointStore(tmp_path)
    store_b = checkpoint_module.NodeCheckpointStore(tmp_path)
    first_writer_entered = threading.Event()
    release_first_writer = threading.Event()
    second_writer_finished = threading.Event()
    original_write = checkpoint_module._write_json_atomic

    def delayed_first_write(path: Path, payload: dict) -> None:
        if payload["idempotency_key"] == "sha256:first":
            first_writer_entered.set()
            assert release_first_writer.wait(timeout=2)
        original_write(path, payload)

    monkeypatch.setattr(checkpoint_module, "_write_json_atomic", delayed_first_write)
    errors: list[BaseException] = []

    def commit(store, key: str) -> None:
        try:
            store.commit_completed(
                task_id="task-1",
                attempt_id="attempt-1",
                node_id="agent",
                idempotency_key=key,
                input_hash=key,
            )
        except BaseException as error:  # pragma: no cover - asserted below
            errors.append(error)
        finally:
            if key == "sha256:second":
                second_writer_finished.set()

    first = threading.Thread(target=commit, args=(store_a, "sha256:first"))
    second = threading.Thread(target=commit, args=(store_b, "sha256:second"))
    first.start()
    assert first_writer_entered.wait(timeout=2)
    second.start()

    assert not second_writer_finished.wait(timeout=0.1)
    release_first_writer.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not errors
    assert not first.is_alive()
    assert not second.is_alive()
    checkpoint = store_a.load("agent")
    assert checkpoint is not None
    assert checkpoint.revision == 2
    assert checkpoint.idempotency_key == "sha256:second"


def test_checkpoint_load_ignores_incomplete_temp_writes(tmp_path: Path) -> None:
    from app.services.node_checkpoint import NodeCheckpointStore

    checkpoint_dir = tmp_path / "checkpoints"
    checkpoint_dir.mkdir()
    (checkpoint_dir / "agent.json.tmp-crash").write_text(
        json.dumps({
            "checkpoint_version": 1,
            "task_id": "task-1",
            "attempt_id": "attempt-1",
            "node_id": "agent",
            "revision": 99,
            "idempotency_key": "sha256:stale",
            "status": "completed",
        }),
        encoding="utf-8",
    )
    store = NodeCheckpointStore(tmp_path)

    assert store.load("agent") is None
