import json
import hashlib
from pathlib import Path


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _snapshot(task_dir: Path, task_run_id: str) -> dict:
    definition = {"compiled_contract_version": 3, "declared_outputs": []}
    plan = {
        "compiled_contract_version": 3,
        "nodes": [{"node_id": "agent"}],
        "topological_order": ["agent"],
    }
    _write_json(task_dir / "compiled_definition.json", definition)
    _write_json(task_dir / "compiled_plan.json", plan)
    components = {}
    for component_id, path in (
        ("v3_runtime_contract", task_dir / "compiled_definition.json"),
        ("execution_plan", task_dir / "compiled_plan.json"),
    ):
        components[component_id] = {
            "path": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return {
        "schema_version": 3,
        "snapshot_kind": "codetalk_run_snapshot",
        "identity": {"task_run_id": task_run_id, "task_id": "task-1"},
        "components": components,
    }


def test_rebuild_checkpoint_projection_restores_events_once_and_preserves_task_authority(
    tmp_path: Path,
) -> None:
    from app.services.checkpoint_projection import rebuild_checkpoint_projection
    from app.services.node_checkpoint import NodeCheckpointStore
    from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore

    task_run_id = "attempt-1"
    task_dir = tmp_path / task_run_id
    _write_json(
        task_dir / "task_run.json",
        {
            "task_run_id": task_run_id,
            "task_id": "task-1",
            "status": "running",
            "execution_status": "running",
            "runtime": {"status": "running"},
        },
    )
    _write_json(
        task_dir / "run_snapshot_v3.json",
        _snapshot(task_dir, task_run_id),
    )
    NodeCheckpointStore(task_dir).commit_completed(
        task_id="task-1",
        attempt_id=task_run_id,
        node_id="agent",
        idempotency_key="sha256:agent",
        input_hash="sha256:input",
        output_artifact_hashes={"report.md": "sha256:report"},
        result_snapshot={"status": "completed", "step_id": "agent"},
    )
    NodeCheckpointStore(task_dir).commit_completed(
        task_id="task-1",
        attempt_id=task_run_id,
        node_id="undeclared",
        idempotency_key="sha256:undeclared",
        input_hash="sha256:input",
        output_artifact_hashes={},
        result_snapshot={"status": "completed", "step_id": "undeclared"},
    )
    (task_dir / "checkpoints" / "partial-write.json").write_text(
        "{not-json", encoding="utf-8"
    )

    first = rebuild_checkpoint_projection(tmp_path, task_run_id)
    second = rebuild_checkpoint_projection(tmp_path, task_run_id)

    assert first.restored_checkpoint_events == 1
    assert first.restored_completion_events == 1
    assert first.projection_updated is True
    assert second.restored_checkpoint_events == 0
    assert second.restored_completion_events == 0
    assert second.projection_updated is False

    events = WorkbenchTaskRunEventStore(tmp_path).list_after(task_run_id)
    assert [event["event_type"] for event in events] == [
        "node_checkpoint_committed",
        "node_completed",
    ]
    assert not any("undeclared" in json.dumps(event) for event in events)
    assert [event["payload"]["deduplication_key"] for event in events] == [
        "checkpoint:agent:1",
        "checkpoint-completed:agent:1",
    ]

    task_payload = json.loads((task_dir / "task_run.json").read_text(encoding="utf-8"))
    assert task_payload["status"] == "running"
    assert task_payload["execution_status"] == "running"
    projection = task_payload["checkpoint_projection"]
    assert projection == {
        "schema_version": "checkpoint-projection-v1",
        "source": "node_checkpoints",
        "nodes": {
            "agent": {
                "checkpoint_revision": 1,
                "completed_at": projection["nodes"]["agent"]["completed_at"],
                "idempotency_key": "sha256:agent",
                "output_artifact_hashes": {"report.md": "sha256:report"},
                "status": "completed",
            }
        },
    }
    assert projection["nodes"]["agent"]["completed_at"]
    assert not (task_dir / ".task_run.json.lock").exists()
    assert not (task_dir / ".task_run_events.jsonl.lock").exists()
    assert (tmp_path / ".locks").is_dir()
