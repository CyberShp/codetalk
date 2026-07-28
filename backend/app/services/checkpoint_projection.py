"""Rebuild disposable task-run projections from durable V3 node checkpoints."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.node_checkpoint import (
    CheckpointValidationError,
    NodeCheckpoint,
    NodeCheckpointStore,
)
from app.services.workbench_task_run import validate_run_snapshot_v3
from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore


@dataclass(frozen=True)
class CheckpointProjectionRebuild:
    task_run_id: str
    completed_node_ids: tuple[str, ...]
    restored_checkpoint_events: int
    restored_completion_events: int
    projection_updated: bool


def rebuild_checkpoint_projection(
    artifact_root: str | Path,
    task_run_id: str,
) -> CheckpointProjectionRebuild:
    """Restore checkpoint-backed display state for one immutable V3 attempt.

    This function never changes a checkpoint or derives terminal task status.
    A startup coordinator may call it before scheduling the first incomplete
    node, then use its own frozen-plan recovery policy to resume execution.
    """
    root = Path(artifact_root)
    clean_task_run_id = _safe_segment(task_run_id)
    attempt_dir = root / clean_task_run_id
    identity, allowed_node_ids = _snapshot_identity(attempt_dir, clean_task_run_id)
    checkpoints = _completed_checkpoints(attempt_dir, identity, allowed_node_ids)
    event_store = WorkbenchTaskRunEventStore(root)
    restored_checkpoint_events = 0
    restored_completion_events = 0
    for checkpoint in checkpoints:
        checkpoint_key = f"checkpoint:{checkpoint.node_id}:{checkpoint.revision}"
        _, appended = event_store.append_once(
            clean_task_run_id,
            "node_checkpoint_committed",
            {
                "node_id": checkpoint.node_id,
                "revision": checkpoint.revision,
                "source": "checkpoint_projection_rebuild",
            },
            deduplication_key=checkpoint_key,
        )
        restored_checkpoint_events += int(appended)
        completion_key = f"checkpoint-completed:{checkpoint.node_id}:{checkpoint.revision}"
        _, appended = event_store.append_once(
            clean_task_run_id,
            "node_completed",
            {
                "node_id": checkpoint.node_id,
                "step_id": checkpoint.node_id,
                "status": "completed",
                "revision": checkpoint.revision,
                "source": "checkpoint_projection_rebuild",
            },
            deduplication_key=completion_key,
        )
        restored_completion_events += int(appended)
    projection_updated = event_store.replace_checkpoint_projection(
        clean_task_run_id,
        _checkpoint_projection(checkpoints),
    )
    return CheckpointProjectionRebuild(
        task_run_id=clean_task_run_id,
        completed_node_ids=tuple(checkpoint.node_id for checkpoint in checkpoints),
        restored_checkpoint_events=restored_checkpoint_events,
        restored_completion_events=restored_completion_events,
        projection_updated=projection_updated,
    )


def _snapshot_identity(
    attempt_dir: Path,
    task_run_id: str,
) -> tuple[dict[str, str], frozenset[str]]:
    errors = validate_run_snapshot_v3(attempt_dir)
    if errors:
        raise ValueError("; ".join(errors))
    snapshot = _read_json(attempt_dir / "run_snapshot_v3.json")
    identity = snapshot.get("identity") if isinstance(snapshot, dict) else None
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("schema_version") != 3
        or snapshot.get("snapshot_kind") != "codetalk_run_snapshot"
        or not isinstance(identity, dict)
        or str(identity.get("task_run_id") or "") != task_run_id
    ):
        raise ValueError(f"task run {task_run_id} has no valid immutable V3 snapshot")
    components = snapshot.get("components")
    execution_plan = components.get("execution_plan") if isinstance(components, dict) else None
    plan_path = str(execution_plan.get("path") or "") if isinstance(execution_plan, dict) else ""
    plan = _read_json(attempt_dir / plan_path)
    if (
        not isinstance(plan, dict)
        or plan.get("compiled_contract_version") != 3
        or not isinstance(plan.get("topological_order"), list)
        or not isinstance(plan.get("nodes"), list)
    ):
        raise ValueError(f"task run {task_run_id} has no valid frozen V3 execution plan")
    declared_nodes = {
        str(node.get("node_id") or "")
        for node in plan["nodes"]
        if isinstance(node, dict) and str(node.get("node_id") or "")
    }
    ordered_nodes = tuple(
        str(node_id)
        for node_id in plan["topological_order"]
        if str(node_id or "")
    )
    if not ordered_nodes or any(node_id not in declared_nodes for node_id in ordered_nodes):
        raise ValueError(f"task run {task_run_id} has an invalid frozen V3 topological order")
    return (
        {
            "task_id": str(identity.get("task_id") or task_run_id),
            "task_run_id": task_run_id,
        },
        frozenset(ordered_nodes),
    )


def _completed_checkpoints(
    attempt_dir: Path,
    identity: dict[str, str],
    allowed_node_ids: frozenset[str],
) -> list[NodeCheckpoint]:
    store = NodeCheckpointStore(attempt_dir)
    checkpoints: list[NodeCheckpoint] = []
    for path in sorted(store.checkpoint_dir.glob("*.json")):
        try:
            checkpoint = store.load(path.stem)
        except (CheckpointValidationError, OSError, ValueError):
            continue
        if checkpoint is None:
            continue
        if (
            checkpoint.status == "completed"
            and checkpoint.task_id == identity["task_id"]
            and checkpoint.attempt_id == identity["task_run_id"]
            and checkpoint.node_id in allowed_node_ids
        ):
            checkpoints.append(checkpoint)
    return checkpoints


def _checkpoint_projection(checkpoints: list[NodeCheckpoint]) -> dict[str, Any]:
    return {
        "schema_version": "checkpoint-projection-v1",
        "source": "node_checkpoints",
        "nodes": {
            checkpoint.node_id: {
                "checkpoint_revision": checkpoint.revision,
                "completed_at": checkpoint.completed_at,
                "idempotency_key": checkpoint.idempotency_key,
                "output_artifact_hashes": dict(checkpoint.output_artifact_hashes),
                "status": "completed",
            }
            for checkpoint in checkpoints
        },
    }


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _safe_segment(value: str) -> str:
    text = str(value or "").strip()
    if not text or "/" in text or "\\" in text or ".." in text:
        raise KeyError(value)
    return text
