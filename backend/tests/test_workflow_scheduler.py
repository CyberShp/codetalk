def _plan(*, failure_policy: str = "continue_independent") -> dict:
    return {
        "plan_version": 1,
        "workflow_version_id": "wfv_1",
        "topological_order": ["a", "b", "c"],
        "max_parallelism": 1,
        "nodes": [
            {"node_id": "c", "type": "report_render", "depends_on": ["a"], "failure_policy": "stop"},
            {"node_id": "b", "type": "semantic_retrieve", "depends_on": [], "failure_policy": "stop"},
            {"node_id": "a", "type": "agent_task", "depends_on": [], "failure_policy": failure_policy},
        ],
    }


def test_scheduler_uses_topology_and_only_passes_direct_dependency_outputs():
    from app.services.workflow_scheduler import WorkflowDagScheduler

    calls = []

    def execute(node, direct_dependencies):
        calls.append((node["node_id"], sorted(direct_dependencies)))
        return {
            "node_id": node["node_id"],
            "status": "completed",
            "validated_outputs": {"value": node["node_id"]},
        }

    result = WorkflowDagScheduler().run(_plan(), execute_node=execute)
    assert calls == [("a", []), ("b", []), ("c", ["a"])]
    assert result.status == "succeeded"
    assert result.results_by_node["c"]["direct_dependencies"] == {
        "a": {"value": "a"}
    }


def test_scheduler_blocks_downstream_and_continues_independent_branch():
    from app.services.workflow_scheduler import WorkflowDagScheduler

    executed = []

    def execute(node, direct_dependencies):
        executed.append(node["node_id"])
        return {
            "node_id": node["node_id"],
            "status": "failed" if node["node_id"] == "a" else "completed",
            "validated_outputs": {},
        }

    result = WorkflowDagScheduler().run(_plan(), execute_node=execute)
    assert executed == ["a", "b"]
    assert result.results_by_node["c"]["status"] == "blocked"
    assert result.results_by_node["c"]["blocked_by"] == ["a"]
    assert result.status == "failed"


def test_scheduler_stop_policy_blocks_all_remaining_nodes():
    from app.services.workflow_scheduler import WorkflowDagScheduler

    result = WorkflowDagScheduler().run(
        _plan(failure_policy="stop"),
        execute_node=lambda node, deps: {
            "node_id": node["node_id"],
            "status": "failed",
            "validated_outputs": {},
        },
    )
    assert result.results_by_node["b"]["status"] == "blocked"
    assert result.results_by_node["c"]["status"] == "blocked"


def test_agent_output_event_is_public_output_not_diagnostic(tmp_path):
    from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore

    store = WorkbenchTaskRunEventStore(tmp_path)
    event = store.append("run-1", "agent_output", {"text": "answer"})
    assert event["event_kind"] == "output"


def test_real_workbench_runner_uses_frozen_plan_and_marks_blocked_nodes(tmp_path, monkeypatch):
    import json

    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner

    root = tmp_path / "task_runs"
    task_dir = root / "task_plan"
    task_dir.mkdir(parents=True)
    workflow = {
        "id": "dag-flow",
        "name": "DAG flow",
        "version": 1,
        "inputs": [],
        "steps": [
            {"id": "c", "type": "report_render"},
            {"id": "b", "type": "semantic_retrieve"},
            {"id": "a", "type": "memory_retrieve"},
        ],
        "outputs": [],
    }
    plan = _plan()
    payload = {
        "task_run_id": "task_plan",
        "workflow_id": "dag-flow",
        "workspace_id": "ws",
        "repo_path": str(tmp_path),
        "artifact_dir": str(task_dir),
        "workflow_snapshot": workflow,
        "input_snapshot": {},
        "task_bundle": {"compiled_plan": plan, "context_bundle": {}},
        "agent_runs": [],
        "created_at": "2026-07-13T00:00:00+00:00",
    }
    (task_dir / "task_run.json").write_text(json.dumps(payload), encoding="utf-8")

    calls = []

    def execute_builtin(self, *, task_run, step, prior_step_results):
        calls.append((step["id"], [item["step_id"] for item in prior_step_results]))
        return {
            "step_id": step["id"],
            "type": step["type"],
            "status": "error" if step["id"] == "a" else "completed",
            "artifacts": [],
        }

    monkeypatch.setattr(WorkbenchWorkflowRunner, "_execute_builtin_step", execute_builtin)
    events = []
    result = WorkbenchWorkflowRunner(
        root, event_sink=lambda event_type, body: events.append((event_type, body))
    ).execute_task_run("task_plan")

    assert calls == [("a", []), ("b", [])]
    assert [item["step_id"] for item in result.step_results] == ["a", "b", "c"]
    assert result.step_results[2]["status"] == "blocked"
    assert result.step_results[2]["blocked_by"] == ["a"]
    assert {event_type for event_type, _ in events} >= {
        "node_started",
        "node_failed",
        "node_blocked",
        "run_completed",
    }
