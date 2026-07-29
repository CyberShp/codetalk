"""Phase 7 deployment policy for reversible V3 write access."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import settings


WORKFLOW_V3_READ_ONLY_DETAIL: dict[str, Any] = {
    "code": "workflow_v3_read_only",
    "message": "V3 工作流当前处于只读回滚模式；历史工作流、任务和产物仍可查看与下载。",
}

WORKFLOW_V3_SCHEDULER_AUTHORITY_DETAIL: dict[str, Any] = {
    "code": "workflow_v3_scheduler_authority",
    "message": "V3 任务只能通过工作流调度器执行；不能单独执行任务内的 Agent 节点。",
}


def workflow_v3_writes_enabled() -> bool:
    return bool(settings.workflow_v3_writes_enabled)


def is_v3_attempt_candidate(
    artifact_dir: str | Path,
    task_payload: Any | None = None,
) -> bool:
    """Identify V3 attempts without trusting only mutable projections.

    Partial or corrupt V3 state must remain on the V3 policy path. Otherwise a
    damaged snapshot could downgrade into a legacy direct-execution path.
    """
    root = Path(artifact_dir)
    frozen: dict[str, dict[str, Any]] = {}
    for filename in (
        "task_run.json",
        "workflow_snapshot.json",
        "task_bundle.json",
        "compiled_definition.json",
        "compiled_plan.json",
        "agent_execution_descriptors.json",
        "run_snapshot_v3.json",
    ):
        path = root / filename
        if not path.exists():
            continue
        value = _read_json(path)
        if not isinstance(value, dict):
            return True
        frozen[filename] = value

    payload = _task_projection(task_payload)
    if not payload:
        payload = frozen.get("task_run.json", {})

    snapshot = frozen.get("run_snapshot_v3.json")
    if isinstance(snapshot, dict):
        execution_contract = snapshot.get("execution_contract")
        if isinstance(execution_contract, dict) and _has_contract_version(
            execution_contract.get("compiled_contract_version")
        ):
            return True
        components = snapshot.get("components")
        if isinstance(components, dict) and "v3_runtime_contract" in components:
            return True

    candidates = [
        payload.get("workflow_snapshot"),
        payload.get("task_bundle"),
        frozen.get("workflow_snapshot.json"),
        frozen.get("task_bundle.json"),
        frozen.get("compiled_definition.json"),
        frozen.get("compiled_plan.json"),
    ]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if _has_contract_version(candidate.get("compiled_contract_version")):
            return True
        for key in ("compiled_definition", "compiled_plan"):
            nested = candidate.get(key)
            if isinstance(nested, dict) and _has_contract_version(
                nested.get("compiled_contract_version")
            ):
                return True
    return False


def v3_attempt_ids(artifact_root: str | Path) -> set[str]:
    """Return V3 attempt IDs without changing recovery or runtime state."""
    root = Path(artifact_root)
    if not root.exists():
        return set()
    return {
        attempt_dir.name
        for attempt_dir in root.iterdir()
        if attempt_dir.is_dir()
        and is_v3_attempt_candidate(attempt_dir)
    }


def _task_projection(task_payload: Any | None) -> dict[str, Any]:
    if isinstance(task_payload, dict):
        return task_payload
    if task_payload is None:
        return {}
    return {
        "workflow_snapshot": getattr(task_payload, "workflow_snapshot", {}),
        "task_bundle": getattr(task_payload, "task_bundle", {}),
    }


def _has_contract_version(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
