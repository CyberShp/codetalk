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


def test_scheduler_reuses_seeded_successful_nodes_and_starts_at_failed_node():
    from app.services.workflow_scheduler import WorkflowDagScheduler

    plan = {
        "plan_version": 1,
        "topological_order": ["discover", "analyze", "report"],
        "max_parallelism": 1,
        "nodes": [
            {"node_id": "discover", "type": "local_scope_discover", "depends_on": []},
            {"node_id": "analyze", "type": "agent_task", "depends_on": ["discover"]},
            {"node_id": "report", "type": "report_render", "depends_on": ["analyze"]},
        ],
    }
    executed: list[str] = []
    events: list[tuple[str, dict]] = []

    result = WorkflowDagScheduler(event_sink=lambda kind, payload: events.append((kind, payload))).run(
        plan,
        seed_results={
            "discover": {
                "node_id": "discover",
                "step_id": "discover",
                "type": "local_scope_discover",
                "status": "completed",
                "validated_outputs": {"artifact": "scope.json"},
                "reused_from_task_run_id": "task_run_parent",
            }
        },
        execute_node=lambda node, _deps: (
            executed.append(node["node_id"])
            or {"status": "completed", "validated_outputs": {"artifact": f"{node['node_id']}.json"}}
        ),
    )

    assert executed == ["analyze", "report"]
    assert result.results_by_node["discover"]["reused_from_task_run_id"] == "task_run_parent"
    assert result.results_by_node["analyze"]["direct_dependencies"] == {
        "discover": {"artifact": "scope.json"}
    }
    assert ("node_reused", {"node_id": "discover", "source_task_run_id": "task_run_parent"}) in events


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


def test_real_runner_passes_parent_successes_to_retry_scheduler(tmp_path, monkeypatch):
    import json

    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner

    root = tmp_path / "task_runs"
    task_dir = root / "task_retry"
    task_dir.mkdir(parents=True)
    workflow = {
        "id": "dag-flow",
        "name": "DAG flow",
        "version": 1,
        "inputs": [],
        "steps": [
            {"id": "a", "type": "memory_retrieve"},
            {"id": "b", "type": "semantic_retrieve"},
            {"id": "c", "type": "report_render"},
        ],
        "outputs": [],
    }
    payload = {
        "task_run_id": "task_retry",
        "workflow_id": "dag-flow",
        "workspace_id": "ws",
        "repo_path": str(tmp_path),
        "artifact_dir": str(task_dir),
        "workflow_snapshot": workflow,
        "input_snapshot": {},
        "task_bundle": {
            "compiled_plan": _plan(),
            "context_bundle": {},
            "retry_seed_results": {
                "a": {
                    "node_id": "a",
                    "step_id": "a",
                    "type": "memory_retrieve",
                    "status": "completed",
                    "validated_outputs": {"artifact": "parent-memory.json"},
                    "reused_from_task_run_id": "task_run_parent",
                }
            },
        },
        "agent_runs": [],
        "created_at": "2026-07-13T00:00:00+00:00",
    }
    (task_dir / "task_run.json").write_text(json.dumps(payload), encoding="utf-8")
    calls: list[tuple[str, list[str]]] = []

    def execute_builtin(self, *, task_run, step, prior_step_results):
        calls.append((step["id"], [item["step_id"] for item in prior_step_results]))
        return {"step_id": step["id"], "type": step["type"], "status": "completed"}

    monkeypatch.setattr(WorkbenchWorkflowRunner, "_execute_builtin_step", execute_builtin)
    result = WorkbenchWorkflowRunner(root).execute_task_run("task_retry")

    assert calls == [("b", []), ("c", ["a"])]
    assert result.step_results[0]["reused_from_task_run_id"] == "task_run_parent"
