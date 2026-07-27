from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.services.workbench_task_run import PreparedWorkbenchTaskRun
from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore
from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner, WorkbenchWorkflowExecutionResult
from app.services.workflow_run_status import derive_delivery_status, validate_status_axes


def _task_run(tmp_path: Path, *, contract_version: int | None = 3, profile: str = "artifact_only", outputs: list[dict] | None = None, validators: list[dict] | None = None) -> PreparedWorkbenchTaskRun:
    run_id = "v3-run"
    artifact_dir = tmp_path / run_id / "agent"
    artifact_dir.mkdir(parents=True)
    declared_outputs = outputs if outputs is not None else [{
        "output_id": "report", "artifact": "report.md", "required": True,
        "producer_step_id": "agent", "schema": None,
    }]
    plan_nodes = [{
        "node_id": "agent", "kind": "agent", "type": "agent_task", "handler_id": "agent",
        "required_outputs": [item["output_id"] for item in declared_outputs if item.get("required")],
    }]
    plan_nodes.extend(validators if validators is not None else [{
        "node_id": "validator", "kind": "validator", "handler_id": "artifact_exists",
        "required_outputs": [item["output_id"] for item in declared_outputs if item.get("required")],
    }])
    definition = {
        "compiled_contract_version": contract_version,
        "validation_profile": profile,
        "declared_outputs": declared_outputs,
        "outputs": declared_outputs,
    }
    plan = {
        "compiled_contract_version": contract_version,
        "nodes": plan_nodes,
        "topological_order": [item["node_id"] for item in plan_nodes],
    }
    return PreparedWorkbenchTaskRun(
        task_run_id=run_id,
        workflow_id="wf-v3",
        workspace_id="workspace",
        repo_path=str(tmp_path),
        artifact_dir=str(tmp_path / run_id),
        workflow_snapshot={"steps": [{"id": "agent", "type": "agent_task"}]},
        input_snapshot={},
        task_bundle={"compiled_definition": definition, "compiled_plan": plan},
        agent_runs=[{"step_id": "agent", "artifact_dir": str(artifact_dir)}],
    )


def _persist_task_run(root: Path, task_run: PreparedWorkbenchTaskRun) -> None:
    task_dir = root / task_run.task_run_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "task_run.json").write_text(json.dumps({
        "task_run_id": task_run.task_run_id,
        "workflow_id": task_run.workflow_id,
        "workspace_id": task_run.workspace_id,
        "repo_path": task_run.repo_path,
        "artifact_dir": task_run.artifact_dir,
        "workflow_snapshot": task_run.workflow_snapshot,
        "input_snapshot": task_run.input_snapshot,
        "task_bundle": task_run.task_bundle,
        "agent_runs": task_run.agent_runs,
    }), encoding="utf-8")


def _complete_agent_step(runner: WorkbenchWorkflowRunner, task_run: PreparedWorkbenchTaskRun) -> None:
    def execute(**_: object) -> dict:
        return {"step_id": "agent", "type": "agent_task", "status": "completed", "artifact_dir": task_run.agent_runs[0]["artifact_dir"]}
    runner._execute_agent_step = execute  # type: ignore[method-assign]


def test_v3_report_success_uses_only_declared_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.workbench_workflow_runner as runner_module

    task_run = _task_run(tmp_path)
    Path(task_run.agent_runs[0]["artifact_dir"], "report.md").write_text("# Report\n", encoding="utf-8")
    Path(task_run.agent_runs[0]["artifact_dir"], "unexpected.json").write_text("{}", encoding="utf-8")
    _persist_task_run(tmp_path, task_run)
    events: list[tuple[str, dict]] = []
    runner = WorkbenchWorkflowRunner(
        tmp_path,
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )
    _complete_agent_step(runner, task_run)
    for forbidden in (
        "materialize_artifact_contract_v3_outputs",
        "normalize_materialized_sfmea_risk_contract",
        "refresh_test_activity_contract",
    ):
        monkeypatch.setattr(
            runner_module,
            forbidden,
            lambda *args, **kwargs: pytest.fail(f"V3 must not invoke {forbidden}"),
        )

    result = runner.execute_task_run(task_run.task_run_id)

    assert result.execution_status == "completed"
    assert result.artifact_validation_status == "passed"
    assert result.governance_status == "not_requested"
    assert result.delivery_status == "ready"
    assert [item["artifact"] for item in result.outputs] == ["report.md"]
    assert "unexpected.json" not in json.dumps(result.outputs)
    execution = json.loads((tmp_path / task_run.task_run_id / "workflow_execution.json").read_text(encoding="utf-8"))
    assert execution["artifact_validation_status"] == "passed"
    assert execution["governance_status"] == "not_requested"
    assert execution["delivery_status"] == "ready"
    assert not (tmp_path / task_run.task_run_id / "test_activity_stage_progress.json").exists()
    assert events[-1] == (
        "v3_status_updated",
        {
            "status": "completed",
            "execution_status": "completed",
            "artifact_validation_status": "passed",
            "governance_status": "not_requested",
            "delivery_status": "ready",
            "legacy_delivery_status": "complete",
            "quality_status": "passed",
            "compiled_contract_version": 3,
        },
    )


def test_v3_missing_declared_artifact_blocks_delivery_without_failing_execution(tmp_path: Path) -> None:
    task_run = _task_run(tmp_path)
    _persist_task_run(tmp_path, task_run)
    runner = WorkbenchWorkflowRunner(tmp_path)
    _complete_agent_step(runner, task_run)

    result = runner.execute_task_run(task_run.task_run_id)

    assert result.execution_status == "completed"
    assert result.artifact_validation_status == "failed"
    assert result.delivery_status == "blocked"
    assert result.status == "completed"


def test_v3_none_does_not_block_missing_artifact(tmp_path: Path) -> None:
    task_run = _task_run(tmp_path, profile="none", validators=[])
    _persist_task_run(tmp_path, task_run)
    runner = WorkbenchWorkflowRunner(tmp_path)
    _complete_agent_step(runner, task_run)

    result = runner.execute_task_run(task_run.task_run_id)

    assert result.execution_status == "completed"
    assert result.artifact_validation_status == "not_requested"
    assert result.delivery_status == "ready"


def test_v3_schema_validator_checks_only_declared_schema(tmp_path: Path) -> None:
    outputs = [{
        "output_id": "report", "artifact": "report.json", "required": True,
        "producer_step_id": "agent", "schema": {"type": "object", "required": ["title"]},
    }]
    validators = [
        {"node_id": "exists", "kind": "validator", "handler_id": "artifact_exists", "required_outputs": ["report"]},
        {"node_id": "schema", "kind": "validator", "handler_id": "json_schema", "required_outputs": ["report"]},
    ]
    task_run = _task_run(tmp_path, profile="schema", outputs=outputs, validators=validators)
    Path(task_run.agent_runs[0]["artifact_dir"], "report.json").write_text("{}", encoding="utf-8")
    _persist_task_run(tmp_path, task_run)
    runner = WorkbenchWorkflowRunner(tmp_path)
    _complete_agent_step(runner, task_run)

    result = runner.execute_task_run(task_run.task_run_id)

    assert result.execution_status == "completed"
    assert result.artifact_validation_status == "failed"
    assert result.delivery_status == "blocked"
    assert result.outputs[0]["reason"] == "schema_validation_failed"


def test_unknown_contract_version_fails_closed(tmp_path: Path) -> None:
    task_run = _task_run(tmp_path, contract_version=99)
    _persist_task_run(tmp_path, task_run)

    result = WorkbenchWorkflowRunner(tmp_path).execute_task_run(task_run.task_run_id)

    assert result.execution_status == "failed"
    assert result.delivery_status == "blocked"
    assert result.step_results[0]["error"] == "unsupported_compiled_contract_version"


def test_string_contract_version_does_not_coerce_into_supported_v3(tmp_path: Path) -> None:
    task_run = _task_run(tmp_path)
    task_run.task_bundle["compiled_definition"]["compiled_contract_version"] = "3"
    task_run.task_bundle["compiled_plan"]["compiled_contract_version"] = "3"
    _persist_task_run(tmp_path, task_run)

    result = WorkbenchWorkflowRunner(tmp_path).execute_task_run(task_run.task_run_id)

    assert result.execution_status == "failed"
    assert result.delivery_status == "blocked"
    assert result.step_results[0]["error"] == "unsupported_compiled_contract_version"


def test_missing_contract_version_uses_unchanged_legacy_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task_run = _task_run(tmp_path, contract_version=None)
    task_run.task_bundle["compiled_definition"].pop("compiled_contract_version")
    task_run.task_bundle["compiled_plan"].pop("compiled_contract_version")
    _persist_task_run(tmp_path, task_run)
    runner = WorkbenchWorkflowRunner(tmp_path)
    expected = WorkbenchWorkflowExecutionResult("v3-run", "completed", "a", "b", "completed")
    monkeypatch.setattr(runner, "_execute_legacy_task_run", lambda **_: expected)

    assert runner.execute_task_run(task_run.task_run_id) is expected


@pytest.mark.parametrize(
    ("execution", "artifact", "governance", "delivery"),
    [
        ("completed", "running", "not_requested", "pending"),
        ("completed", "failed", "not_requested", "blocked"),
        ("completed", "passed", "warning", "ready"),
    ],
)
def test_four_axis_status_rules(execution: str, artifact: str, governance: str, delivery: str) -> None:
    assert derive_delivery_status(
        execution_status=execution,
        artifact_validation_status=artifact,
        governance_status=governance,
    ) == delivery
    validate_status_axes(
        execution_status=execution,
        artifact_validation_status=artifact,
        governance_status=governance,
        delivery_status=delivery,
    )


def test_invalid_four_axis_combination_is_rejected() -> None:
    with pytest.raises(ValueError, match="invalid workflow status combination"):
        validate_status_axes(
            execution_status="completed",
            artifact_validation_status="failed",
            governance_status="not_requested",
            delivery_status="ready",
        )


def test_v3_input_binding_uses_declared_input_id_when_canvas_node_id_differs() -> None:
    from app.services.workbench_workflow_runner import _resolve_plan_node_inputs

    user_text = "  first line\nsecond line  "
    resolved = _resolve_plan_node_inputs(
        plan_node={
            "resolved_input_bindings": {
                "analysis_target": {
                    "source_node_id": "target-node",
                    "source_input_id": "analysis_target",
                    "source_port_id": "value",
                }
            }
        },
        input_snapshot={"analysis_target": user_text},
        direct_dependency_outputs={},
    )

    assert resolved == {"analysis_target": user_text}


def test_event_store_persists_v3_axes_and_rejects_impossible_delivery(tmp_path: Path) -> None:
    task_run = _task_run(tmp_path)
    _persist_task_run(tmp_path, task_run)
    store = WorkbenchTaskRunEventStore(tmp_path)

    persisted = store.mark_v3_outcomes(
        task_run.task_run_id,
        execution_status="completed",
        artifact_validation_status="failed",
        governance_status="not_requested",
        delivery_status="blocked",
        quality_status="blocked",
        legacy_delivery_status="none",
    )

    assert persisted["execution_status"] == "completed"
    assert persisted["artifact_validation_status"] == "failed"
    assert persisted["delivery_status"] == "blocked"
    with pytest.raises(ValueError, match="invalid workflow status combination"):
        store.mark_v3_outcomes(
            task_run.task_run_id,
            execution_status="completed",
            artifact_validation_status="failed",
            governance_status="not_requested",
            delivery_status="ready",
            quality_status="passed",
            legacy_delivery_status="complete",
        )


def test_api_read_reconciliation_preserves_v3_four_axis_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.api.agent_workbench as workbench_api
    from app.services.workbench_task_run import WorkbenchTaskRunStore

    task_run = _task_run(tmp_path)
    _persist_task_run(tmp_path, task_run)
    store = WorkbenchTaskRunEventStore(tmp_path)
    store.mark_v3_outcomes(
        task_run.task_run_id,
        execution_status="completed",
        artifact_validation_status="passed",
        governance_status="not_requested",
        delivery_status="ready",
        quality_status="passed",
        legacy_delivery_status="complete",
    )
    (tmp_path / task_run.task_run_id / "workflow_execution.json").write_text(
        json.dumps({
            "compiled_contract_version": 3,
            "execution_status": "completed",
            "artifact_validation_status": "passed",
            "governance_status": "not_requested",
            "delivery_status": "ready",
            "legacy_delivery_status": "complete",
            "quality_status": "passed",
            "outputs": [{"artifact": "report.md", "status": "ok"}],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(workbench_api, "_task_runs_dir", lambda: tmp_path)

    reconciled = workbench_api._reconcile_persisted_task_run_outcomes(
        WorkbenchTaskRunStore(tmp_path).load(task_run.task_run_id)
    )

    assert reconciled.delivery_status == "ready"
    assert reconciled.artifact_validation_status == "passed"
    assert reconciled.governance_status == "not_requested"


def test_v3_cancel_api_persists_valid_blocked_four_axis_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.api.agent_workbench as workbench_api
    from app.services.workbench_task_run import WorkbenchTaskRunStore

    task_run = _task_run(tmp_path)
    _persist_task_run(tmp_path, task_run)
    event_store = WorkbenchTaskRunEventStore(tmp_path)
    event_store.mark_status(task_run.task_run_id, "queued")
    monkeypatch.setattr(workbench_api, "_task_runs_dir", lambda: tmp_path)

    response = asyncio.run(workbench_api.cancel_task_run(task_run.task_run_id))
    cancelled = WorkbenchTaskRunStore(tmp_path).load(task_run.task_run_id)

    assert response["cancelled"] is True
    assert (
        cancelled.execution_status,
        cancelled.artifact_validation_status,
        cancelled.governance_status,
        cancelled.delivery_status,
    ) == ("cancelled", "not_started", "not_requested", "blocked")


def test_reexecuting_completed_v3_run_resets_all_axes_before_queue_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.api.agent_workbench as workbench_api
    from app.services.workbench_task_run import WorkbenchTaskRunStore

    task_run = _task_run(tmp_path)
    _persist_task_run(tmp_path, task_run)
    event_store = WorkbenchTaskRunEventStore(tmp_path)
    event_store.mark_status(task_run.task_run_id, "completed")
    event_store.mark_v3_outcomes(
        task_run.task_run_id,
        execution_status="completed",
        artifact_validation_status="passed",
        governance_status="not_requested",
        delivery_status="ready",
        quality_status="passed",
        legacy_delivery_status="complete",
    )
    monkeypatch.setattr(workbench_api, "_task_runs_dir", lambda: tmp_path)

    def discard_background(coroutine: object) -> object:
        coroutine.close()  # type: ignore[attr-defined]
        return object()

    monkeypatch.setattr(workbench_api.asyncio, "create_task", discard_background)
    response = workbench_api.Response()

    result = asyncio.run(
        workbench_api.execute_task_run_workflow(
            task_run.task_run_id,
            workbench_api.TaskRunExecuteRequest(),
            response,
        )
    )
    queued = WorkbenchTaskRunStore(tmp_path).load(task_run.task_run_id)

    assert response.status_code == 202
    assert result["status"] == "queued"
    assert (
        queued.execution_status,
        queued.artifact_validation_status,
        queued.governance_status,
        queued.delivery_status,
    ) == ("queued", "not_started", "not_requested", "pending")


def test_v3_acceptance_audit_endpoint_does_not_invoke_legacy_test_activity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.api.agent_workbench as workbench_api

    task_run = _task_run(tmp_path)
    _persist_task_run(tmp_path, task_run)
    WorkbenchTaskRunEventStore(tmp_path).mark_status(task_run.task_run_id, "completed")
    monkeypatch.setattr(workbench_api, "_task_runs_dir", lambda: tmp_path)
    monkeypatch.setattr(
        WorkbenchWorkflowRunner,
        "audit_test_activity_quality",
        lambda *args, **kwargs: pytest.fail("V3 must not invoke legacy Test Activity audit"),
    )

    result = asyncio.run(
        workbench_api.create_task_run_acceptance_audit(task_run.task_run_id)
    )

    assert result == {
        "status": "not_applicable",
        "reason": "frozen_contract_uses_validation_profile",
        "compiled_contract_version": 3,
    }
    assert not (tmp_path / task_run.task_run_id / "task_acceptance_audit.json").exists()


@pytest.mark.parametrize("compiled_contract_version", [3, 99])
def test_frozen_contract_api_projection_keeps_four_axes_and_skips_legacy_acceptance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    compiled_contract_version: int,
) -> None:
    from app.api import agent_workbench

    task_run = _task_run(tmp_path)
    _persist_task_run(tmp_path, task_run)
    result = WorkbenchWorkflowExecutionResult(
        task_run_id=task_run.task_run_id,
        status="completed",
        started_at="start",
        completed_at="end",
        execution_status="completed",
        artifact_validation_status="failed",
        governance_status="not_requested",
        delivery_status="blocked",
        legacy_delivery_status="none",
        quality_status="blocked",
        compiled_contract_version=compiled_contract_version,
    )

    class FakeRunner:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def execute_task_run(self, *_: object, **__: object) -> WorkbenchWorkflowExecutionResult:
            return result

    monkeypatch.setattr(agent_workbench, "_task_runs_dir", lambda: tmp_path)
    monkeypatch.setattr(agent_workbench, "WorkbenchWorkflowRunner", FakeRunner)
    monkeypatch.setattr(
        agent_workbench,
        "_materialize_task_run_outputs_if_available",
        lambda **_: pytest.fail("V3 must not enter legacy acceptance materialization"),
    )

    response = agent_workbench._execute_task_run_with_closure(
        task_run_id=task_run.task_run_id,
        payload=agent_workbench.TaskRunExecuteRequest(),
    )

    persisted = json.loads((tmp_path / task_run.task_run_id / "task_run.json").read_text(encoding="utf-8"))
    assert response["delivery_status"] == "blocked"
    assert response["legacy_delivery_status"] == "none"
    assert persisted["execution_status"] == "completed"
    assert persisted["artifact_validation_status"] == "failed"
    assert persisted["delivery_status"] == "blocked"
