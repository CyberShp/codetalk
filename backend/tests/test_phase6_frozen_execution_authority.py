from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_v3_attempt(root: Path, *, omit_component: str = "") -> tuple[str, Path]:
    attempt_id = "frozen-authority-attempt"
    attempt_dir = root / attempt_id
    agent_dir = attempt_dir / "agent_runs" / "agent"
    agent_dir.mkdir(parents=True)
    frozen_definition = {
        "compiled_contract_version": 3,
        "validation_profile": "none",
        "declared_outputs": [],
        "steps": [{"id": "agent", "type": "agent_task", "goal": "frozen goal"}],
    }
    frozen_plan = {
        "compiled_contract_version": 3,
        "nodes": [{
            "node_id": "agent",
            "kind": "agent",
            "type": "agent_task",
            "handler_id": "agent",
            "resolved_input_bindings": {
                "request": {
                    "source_node_id": "request",
                    "source_input_id": "request",
                    "source_port_id": "value",
                },
            },
        }],
        "topological_order": ["agent"],
    }
    frozen_inputs = {"request": {"text": "frozen input"}}
    frozen_agent_runs = {
        "schema_version": 1,
        "agent_runs": [{
            "step_id": "agent",
            "provider": "agent-runtime:codex",
            "artifact_dir": str(agent_dir),
        }],
    }
    frozen_components = {
        "v3_runtime_contract": ("compiled_definition.json", frozen_definition),
        "execution_plan": ("compiled_plan.json", frozen_plan),
        "input_snapshot": ("input_snapshot.json", frozen_inputs),
        "agent_execution_descriptors": (
            "agent_execution_descriptors.json",
            frozen_agent_runs,
        ),
    }
    components: dict[str, dict[str, str]] = {}
    for component_id, (name, payload) in frozen_components.items():
        _write_json(attempt_dir / name, payload)
        if component_id != omit_component:
            components[component_id] = {"path": name, "sha256": _sha256(attempt_dir / name)}
    _write_json(
        attempt_dir / "run_snapshot_v3.json",
        {
            "schema_version": 3,
            "snapshot_kind": "codetalk_run_snapshot",
            "execution_contract": {"compiled_contract_version": 3},
            "components": components,
        },
    )
    _write_json(
        attempt_dir / "task_run.json",
        {
            "task_run_id": attempt_id,
            "workflow_id": "workflow-v3",
            "workspace_id": "workspace",
            "repo_path": str(root),
            "artifact_dir": str(attempt_dir),
            "workflow_snapshot": {
                "compiled_contract_version": 3,
                "steps": [{"id": "agent", "type": "agent_task", "goal": "mutable goal"}],
            },
            "input_snapshot": {"request": {"text": "mutable input"}},
            "task_bundle": {
                "compiled_contract_version": 3,
                "compiled_definition": frozen_definition,
                "compiled_plan": frozen_plan,
            },
            "agent_runs": [{
                "step_id": "agent",
                "provider": "mutated-provider",
                "artifact_dir": str(attempt_dir / "mutable-agent"),
            }],
        },
    )
    return attempt_id, agent_dir


@pytest.mark.parametrize(
    "missing_component",
    [
        "v3_runtime_contract",
        "execution_plan",
        "input_snapshot",
        "agent_execution_descriptors",
    ],
)
def test_v3_runner_fails_closed_when_required_frozen_authority_is_omitted(
    tmp_path: Path,
    missing_component: str,
) -> None:
    attempt_id, _ = _write_v3_attempt(tmp_path, omit_component=missing_component)
    runner = WorkbenchWorkflowRunner(tmp_path)
    runner._execute_agent_step = lambda **_: pytest.fail(  # type: ignore[method-assign]
        "an incomplete frozen V3 authority must not dispatch an agent"
    )

    result = runner.execute_task_run(attempt_id)

    assert result.execution_status == "failed"
    assert result.delivery_status == "blocked"


def test_v3_runner_ignores_mutable_task_projection_for_execution_authority(
    tmp_path: Path,
) -> None:
    attempt_id, frozen_agent_dir = _write_v3_attempt(tmp_path)
    captured: dict[str, object] = {}
    runner = WorkbenchWorkflowRunner(tmp_path)

    def execute(**kwargs: object) -> dict:
        captured.update(kwargs)
        return {
            "step_id": "agent",
            "type": "agent_task",
            "status": "completed",
            "artifact_dir": str(frozen_agent_dir),
        }

    runner._execute_agent_step = execute  # type: ignore[method-assign]

    result = runner.execute_task_run(attempt_id)

    assert result.execution_status == "completed"
    assert captured["step"] == {
        "id": "agent",
        "type": "agent_task",
        "goal": "frozen goal",
    }
    assert captured["resolved_inputs"] == {"request": {"text": "frozen input"}}
    assert captured["agent_run"] == {
        "step_id": "agent",
        "provider": "agent-runtime:codex",
        "artifact_dir": str(frozen_agent_dir),
    }


def test_v3_snapshot_marker_prevents_downgrade_when_compiled_authority_is_removed(
    tmp_path: Path,
) -> None:
    attempt_id, _ = _write_v3_attempt(tmp_path)
    attempt_dir = tmp_path / attempt_id
    snapshot_path = attempt_dir / "run_snapshot_v3.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["components"].pop("v3_runtime_contract")
    snapshot["components"].pop("execution_plan")
    _write_json(snapshot_path, snapshot)
    (attempt_dir / "compiled_definition.json").unlink()
    (attempt_dir / "compiled_plan.json").unlink()
    task_path = attempt_dir / "task_run.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task["workflow_snapshot"] = {
        "steps": [{"id": "agent", "type": "agent_task"}],
    }
    task["task_bundle"] = {}
    _write_json(task_path, task)
    runner = WorkbenchWorkflowRunner(tmp_path)
    runner._execute_agent_step = lambda **_: pytest.fail(  # type: ignore[method-assign]
        "a V3 attempt must not downgrade into mutable legacy execution"
    )

    result = runner.execute_task_run(attempt_id)

    assert result.execution_status == "failed"
    assert result.delivery_status == "blocked"
