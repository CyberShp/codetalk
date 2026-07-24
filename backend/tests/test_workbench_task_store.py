import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _workflow() -> dict:
    return {
        "id": "source-review",
        "name": "Source review",
        "version": 1,
        "inputs": [{"id": "target", "type": "text", "required": True}],
        "steps": [{"id": "scope", "type": "local_scope_discover", "required_artifacts": ["report.md"]}],
        "outputs": [{"id": "report", "type": "markdown", "from": "scope", "artifact": "report.md", "required": True}],
    }


def test_quality_retry_reaudits_parent_and_seeds_report(tmp_path, monkeypatch):
    from app.api import workbench_v2_tasks
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner

    parent_root = tmp_path / "parent"
    parent_agent = parent_root / "agent_runs" / "analyze"
    child_root = tmp_path / "child"
    child_agent = child_root / "agent_runs" / "analyze"
    parent_agent.mkdir(parents=True)
    child_agent.mkdir(parents=True)
    (parent_agent / "report.md").write_text("# 旧报告\n", encoding="utf-8")
    audit = {
        "status": "needs_rework",
        "issue_count": 1,
        "issues": [{
            "artifact": "black_box_cases.json",
            "code": "raw_pdu_harness_missing_scenario_capability",
        }],
    }
    monkeypatch.setattr(
        WorkbenchWorkflowRunner,
        "audit_test_activity_quality",
        lambda self, *, task_run: audit,
    )
    parent = SimpleNamespace(
        task_run_id="task_run_parent",
        quality_status="blocked",
        agent_runs=[{
            "step_id": "analyze",
            "artifact_dir": str(parent_agent),
            "required_artifacts": ["report.md"],
        }],
    )
    prepared = SimpleNamespace(
        artifact_dir=str(child_root),
        task_bundle={
            "retry_seed_results": {
                "analyze": {
                    "step_id": "analyze",
                    "status": "completed",
                    "reused_from_task_run_id": "task_run_parent",
                },
            },
        },
        agent_runs=[{
            "step_id": "analyze",
            "artifact_dir": str(child_agent),
            "required_artifacts": ["report.md"],
        }],
    )

    workbench_v2_tasks._seed_quality_retry_from_parent(
        parent_run=parent,
        prepared=prepared,
    )

    assert (child_agent / "report.md").read_text(encoding="utf-8") == "# 旧报告\n"
    assert json.loads((child_root / "test_activity_quality_audit.json").read_text()) == audit
    assert prepared.task_bundle["retry_source"]["mode"] == "quality_repair"
    assert prepared.task_bundle["retry_source"]["failed_node_ids"] == ["analyze"]
    assert prepared.task_bundle["retry_seed_results"] == {}
    assert prepared.task_bundle["quality_retry_seed"]["copied_artifacts"] == [
        "analyze:report.md"
    ]


def test_quality_blocked_parent_with_green_reaudit_reuses_agent_outputs(
    tmp_path,
    monkeypatch,
):
    from app.api import workbench_v2_tasks
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner

    parent_root = tmp_path / "parent"
    parent_agent = parent_root / "agent_runs" / "analyze"
    child_root = tmp_path / "child"
    child_agent = child_root / "agent_runs" / "analyze"
    parent_agent.mkdir(parents=True)
    child_agent.mkdir(parents=True)
    (parent_agent / "report.md").write_text("# 已核验报告\n", encoding="utf-8")
    (parent_agent / "behavior_claim_validation.json").write_text(
        json.dumps({"status": "verified"}),
        encoding="utf-8",
    )
    (parent_agent / "execution_result.json").write_text(
        json.dumps({"status": "completed"}),
        encoding="utf-8",
    )
    (parent_root / "workflow_execution.json").write_text(
        json.dumps({
            "status": "needs_rework",
            "step_results": [
                {
                    "step_id": "analyze",
                    "type": "agent_task",
                    "status": "completed",
                    "artifact_dir": str(parent_agent),
                    "artifacts": ["report.md"],
                    "validated_outputs": {
                        "artifact_dir": str(parent_agent),
                        "artifacts": ["report.md"],
                    },
                },
                {
                    "step_id": "render",
                    "type": "report_render",
                    "status": "completed",
                    "artifact_dir": str(parent_root / "steps" / "render"),
                },
            ],
            "outputs": [],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        WorkbenchWorkflowRunner,
        "audit_test_activity_quality",
        lambda self, *, task_run: {
            "status": "deliverable",
            "score": 100,
            "issue_count": 0,
            "issues": [],
        },
    )
    parent = SimpleNamespace(
        task_run_id="task_run_parent",
        quality_status="blocked",
        artifact_dir=str(parent_root),
        agent_runs=[{
            "step_id": "analyze",
            "artifact_dir": str(parent_agent),
            "required_artifacts": ["report.md"],
        }],
    )
    plan = {
        "nodes": [
            {"node_id": "analyze", "type": "agent_task", "depends_on": []},
            {"node_id": "render", "type": "report_render", "depends_on": ["analyze"]},
        ],
        "topological_order": ["analyze", "render"],
    }

    seeds, failed_nodes = workbench_v2_tasks._retry_seed_results_from_parent(
        parent,
        plan,
    )
    prepared = SimpleNamespace(
        artifact_dir=str(child_root),
        task_bundle={"retry_seed_results": seeds},
        agent_runs=[{
            "step_id": "analyze",
            "artifact_dir": str(child_agent),
            "required_artifacts": ["report.md"],
        }],
    )
    workbench_v2_tasks._seed_quality_retry_from_parent(
        parent_run=parent,
        prepared=prepared,
    )

    assert failed_nodes == []
    assert set(seeds) == {"analyze"}
    assert prepared.task_bundle["retry_source"] == {
        "task_run_id": "task_run_parent",
        "mode": "quality_revalidation",
        "failed_node_ids": [],
    }
    assert (child_agent / "report.md").read_text(encoding="utf-8") == "# 已核验报告\n"
    reused = prepared.task_bundle["retry_seed_results"]["analyze"]
    assert reused["artifact_dir"] == str(child_agent)
    assert reused["validated_outputs"]["artifact_dir"] == str(child_agent)
    assert prepared.task_bundle["quality_revalidation_seed"] == {
        "audit_status": "deliverable",
        "issue_count": 0,
        "copied_artifacts": ["analyze:report.md"],
        "copied_support_files": [
            "analyze:behavior_claim_validation.json",
            "analyze:execution_result.json",
        ],
    }
    assert json.loads((child_agent / "behavior_claim_validation.json").read_text()) == {
        "status": "verified"
    }


def test_task_store_migration_crud_filters_archive_and_clone(tmp_path):
    from app.services.workbench_task_store import WorkbenchTaskStore

    db_path = tmp_path / "workflows.db"
    store = WorkbenchTaskStore(db_path)

    first = store.initialize_and_migrate()
    second = store.initialize_and_migrate()

    assert first["schema_version"] == 1
    assert second["schema_version"] == 1
    with sqlite3.connect(db_path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(workbench_tasks)")}
    assert {
        "task_id", "name", "workspace_id", "workflow_id", "workflow_version_id",
        "lifecycle_status", "input_values_json", "execution_overrides_json",
        "output_overrides_json", "tags_json", "last_run_id", "archived_at",
    }.issubset(columns)

    task = store.create_task(
        name="SPDK source review",
        description="Review nvmf flow",
        workspace_id="ws-spdk",
        workflow_id="source-review",
        workflow_version_id="wfv-1",
        lifecycle_status="draft",
        input_values={"target": "lib/nvmf"},
        tags=["storage", "nvmf"],
    )
    updated = store.update_task(
        task.task_id,
        name="SPDK NVMf review",
        lifecycle_status="ready",
        input_values={"target": "lib/nvmf/ctrlr.c"},
    )
    assert updated.name == "SPDK NVMf review"
    assert updated.workflow_version_id == "wfv-1"
    assert updated.input_values == {"target": "lib/nvmf/ctrlr.c"}
    assert store.list_tasks(q="nvmf", lifecycle_status="ready") == [updated]
    assert store.list_tasks(workflow_id="source-review", workspace_id="ws-spdk") == [updated]

    clone = store.clone_task(task.task_id, name="SPDK NVMf review copy")
    assert clone.task_id != task.task_id
    assert clone.workflow_version_id == task.workflow_version_id
    assert clone.lifecycle_status == "draft"
    archived = store.archive_task(task.task_id)
    assert archived.lifecycle_status == "archived"
    assert archived.archived_at
    assert store.get_task(task.task_id).task_id == task.task_id


def test_task_store_rejects_workflow_identity_mutation(tmp_path):
    from app.services.workbench_task_store import WorkbenchTaskStore

    store = WorkbenchTaskStore(tmp_path / "workflows.db")
    task = store.create_task(
        name="Frozen workflow task",
        workspace_id="ws-1",
        workflow_id="flow-1",
        workflow_version_id="wfv-1",
    )

    try:
        store.update_task(task.task_id, workflow_version_id="wfv-2")
    except ValueError as exc:
        assert "workflow_version_id" in str(exc)
    else:
        raise AssertionError("workflow version mutation must be rejected")


def test_task_effective_config_uses_explicit_replace_and_keeps_workflow_immutable():
    from app.services.workbench_task_compile import compile_task_configuration

    definition = {
        "id": "flow",
        "name": "Flow",
        "version": 1,
        "inputs": [],
        "steps": [{
            "id": "analyze", "type": "agent_task", "provider": "builtin-llm",
            "mcp_profiles": ["gitnexus"], "skills": ["source-evidence-first"],
            "required_artifacts": ["report.md"],
        }],
        "outputs": [{"id": "report", "type": "markdown", "from": "analyze", "artifact": "report.md", "required": True}],
    }
    plan = {
        "plan_version": 1,
        "workflow_version_id": "wfv-1",
        "topological_order": ["analyze"],
        "nodes": [{
            "node_id": "analyze", "provider": "builtin-llm", "mcp_profiles": ["gitnexus"],
            "skill_ids": ["source-evidence-first"], "output_contracts": [definition["outputs"][0]],
        }],
    }

    compiled = compile_task_configuration(
        compiled_definition=definition,
        compiled_plan=plan,
        execution_overrides={
            "nodes": {
                "analyze": {
                    "provider": {"mode": "inherit"},
                    "mcp_profiles": {"mode": "replace", "value": ["cgc"]},
                    "skill_ids": {"mode": "replace", "value": ["sfmea-analysis"]},
                }
            }
        },
        output_overrides={
            "outputs": {"report": {"label": "测试报告", "artifact": "task-report.md", "enabled": True}},
            "custom_outputs": [{
                "id": "trace", "label": "调用链", "type": "markdown", "from": "analyze",
                "artifact": "trace.md", "required": False,
            }],
        },
    )

    step = compiled["compiled_definition"]["steps"][0]
    assert step["provider"] == "builtin-llm"
    assert step["mcp_profiles"] == ["cgc"]
    assert step["skills"] == ["sfmea-analysis"]
    assert step["required_artifacts"] == ["task-report.md", "trace.md"]
    assert [item["artifact"] for item in compiled["compiled_definition"]["outputs"]] == ["task-report.md", "trace.md"]
    assert definition["steps"][0]["mcp_profiles"] == ["gitnexus"]
    assert definition["outputs"][0]["artifact"] == "report.md"


def test_optional_mindmap_is_absent_by_default_and_expands_to_three_artifacts_when_enabled():
    from app.services.source_driven_test_design import MINDMAP_ARTIFACTS
    from app.services.workbench_task_compile import compile_task_configuration

    definition = {
        "id": "flow",
        "name": "Flow",
        "version": 1,
        "inputs": [],
        "steps": [{
            "id": "analyze",
            "type": "agent_task",
            "execution_mode": "staged",
            "required_artifacts": ["report.md"],
        }],
        "outputs": [
            {
                "id": "report",
                "type": "markdown",
                "from": "analyze",
                "artifact": "report.md",
                "required": True,
            },
            {
                "id": "test_design_mindmap",
                "label": "测试设计脑图",
                "type": "test_design_mindmap",
                "from": "analyze",
                "artifact": MINDMAP_ARTIFACTS[0],
                "companion_artifacts": list(MINDMAP_ARTIFACTS[1:]),
                "required": False,
                "default_enabled": False,
            },
        ],
    }
    plan = {"nodes": [{"node_id": "analyze", "output_contracts": definition["outputs"]}]}

    default = compile_task_configuration(
        compiled_definition=definition,
        compiled_plan=plan,
        execution_overrides={},
        output_overrides={},
    )
    assert [item["id"] for item in default["compiled_definition"]["outputs"]] == ["report"]
    assert default["compiled_definition"]["steps"][0]["required_artifacts"] == ["report.md"]

    enabled = compile_task_configuration(
        compiled_definition=definition,
        compiled_plan=plan,
        execution_overrides={},
        output_overrides={"outputs": {"test_design_mindmap": {"enabled": True}}},
    )
    assert [item["id"] for item in enabled["compiled_definition"]["outputs"]] == [
        "report",
        "test_design_mindmap",
    ]
    assert enabled["compiled_definition"]["steps"][0]["required_artifacts"] == [
        "report.md",
        *MINDMAP_ARTIFACTS,
    ]


def test_mindmap_output_rejects_non_staged_or_external_agent_sources():
    from app.services.workbench_task_compile import (
        TaskConfigurationError,
        compile_task_configuration,
    )

    definition = {
        "steps": [{"id": "analyze", "type": "agent_task", "provider": "claude-code"}],
        "outputs": [{
            "id": "mindmap",
            "type": "test_design_mindmap",
            "from": "analyze",
            "artifact": "test_design_mindmap.json",
        }],
    }

    with pytest.raises(TaskConfigurationError, match="内置模型分阶段"):
        compile_task_configuration(
            compiled_definition=definition,
            compiled_plan={"nodes": [{"node_id": "analyze", "output_contracts": []}]},
            execution_overrides={},
            output_overrides={},
        )


def test_task_effective_config_rejects_required_disable_unsafe_and_unknown_source():
    from app.services.workbench_task_compile import TaskConfigurationError, compile_task_configuration

    definition = {
        "id": "flow", "name": "Flow", "version": 1, "inputs": [],
        "steps": [{"id": "analyze", "type": "agent_task", "required_artifacts": ["report.md"]}],
        "outputs": [{"id": "report", "type": "markdown", "from": "analyze", "artifact": "report.md", "required": True}],
    }
    plan = {"topological_order": ["analyze"], "nodes": [{"node_id": "analyze", "output_contracts": definition["outputs"]}]}

    for output_overrides, marker in [
        ({"outputs": {"report": {"enabled": False}}}, "必需输出"),
        ({"outputs": {"report": {"artifact": "../escape.md"}}}, "artifact"),
        ({"custom_outputs": [{"id": "extra", "type": "markdown", "from": "missing", "artifact": "extra.md"}]}, "来源节点"),
        ({"custom_outputs": [{"id": "extra", "type": "json", "from": "analyze", "artifact": "extra.json"}]}, "Schema"),
    ]:
        with pytest.raises(TaskConfigurationError, match=marker):
            compile_task_configuration(
                compiled_definition=definition,
                compiled_plan=plan,
                execution_overrides={},
                output_overrides=output_overrides,
            )


def test_task_effective_config_rejects_file_output_from_local_step():
    from app.services.workbench_task_compile import TaskConfigurationError, compile_task_configuration

    definition = {
        "id": "flow",
        "name": "Flow",
        "version": 1,
        "inputs": [],
        "steps": [
            {"id": "scope", "type": "local_scope_discover"},
            {"id": "analyze", "type": "agent_task", "provider": "builtin-llm"},
        ],
        "outputs": [],
    }
    plan = {
        "topological_order": ["scope", "analyze"],
        "nodes": [
            {"node_id": "scope", "node_type": "local_scope_discover", "output_contracts": []},
            {"node_id": "analyze", "node_type": "agent_task", "output_contracts": []},
        ],
    }

    with pytest.raises(TaskConfigurationError, match="不能生成任务专用文件"):
        compile_task_configuration(
            compiled_definition=definition,
            compiled_plan=plan,
            execution_overrides={},
            output_overrides={
                "custom_outputs": [
                    {
                        "id": "report",
                        "label": "分析报告",
                        "type": "markdown",
                        "from": "scope",
                        "artifact": "report.md",
                        "required": False,
                    }
                ]
            },
        )

    for execution_overrides, marker in [
        ({"nodes": {"analyze": {"skill_ids": {"mode": "replace", "value": "sfmea"}}}}, "字符串数组"),
        ({"nodes": {"analyze": {"timeout_sec": {"mode": "replace", "value": 0}}}}, "正整数"),
        ({"nodes": {"analyze": {"failure_policy": {"mode": "replace", "value": "ignore"}}}}, "不受支持"),
    ]:
        with pytest.raises(TaskConfigurationError, match=marker):
            compile_task_configuration(
                compiled_definition=definition,
                compiled_plan=plan,
                execution_overrides=execution_overrides,
                output_overrides={},
            )


def test_task_effective_config_keeps_migrated_terminal_only_output_runnable():
    from app.services.workbench_task_compile import compile_task_configuration

    definition = {
        "steps": [{"id": "scope", "type": "local_scope_discover"}],
        "outputs": [{"id": "report", "type": "markdown", "from": "scope"}],
    }
    result = compile_task_configuration(
        compiled_definition=definition,
        compiled_plan={"nodes": [{"node_id": "scope", "output_contracts": []}]},
        execution_overrides={},
        output_overrides={},
    )

    assert result["compiled_definition"]["outputs"] == definition["outputs"]


def test_run_outcomes_keep_quality_and_delivery_independent():
    from app.api.agent_workbench import _derive_task_run_outcomes

    quality_status, delivery_status = _derive_task_run_outcomes(
        execution={"test_activity_quality": {"deliverable": True, "issue_count": 1}},
        run_summary={
            "nodes": [{
                "outputs": [
                    {"artifact": "report.md", "path": "agent/report.md", "status_label": "可下载"},
                    {"artifact": "cases.json", "path": "agent/cases.json", "status_label": "缺少交付文件"},
                ]
            }]
        },
    )

    assert quality_status == "warning"
    assert delivery_status == "partial"


def test_task_configuration_rejects_agent_resource_overrides_for_builtin_nodes():
    from app.services.workbench_task_compile import TaskConfigurationError, compile_task_configuration

    with pytest.raises(TaskConfigurationError, match="仅 Agent 节点"):
        compile_task_configuration(
            compiled_definition={
                "id": "builtin-flow",
                "inputs": [],
                "steps": [{"id": "scope", "type": "local_scope_discover"}],
                "outputs": [],
            },
            compiled_plan={"nodes": [{"node_id": "scope", "type": "local_scope_discover"}]},
            execution_overrides={
                "nodes": {
                    "scope": {"provider": {"mode": "replace", "value": "codex"}}
                }
            },
            output_overrides={},
        )


@pytest.mark.asyncio
async def test_task_api_paginates_all_rows_beyond_the_old_500_item_cap(tmp_path, monkeypatch):
    from app.api import workbench_v2_tasks
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "workbench_v2_enabled", True)
    store = workbench_v2_tasks.task_store()
    store.initialize_and_migrate()
    with store._connect() as db:
        rows = [
            (
                f"task-{index:04d}", f"Task {index:04d}", "", "ws", "flow", "wfv",
                "draft", "{}", "{}", "{}", "[]", None,
                f"2026-07-13T00:{index // 60:02d}:{index % 60:02d}+00:00",
                f"2026-07-13T00:{index // 60:02d}:{index % 60:02d}+00:00", None,
            )
            for index in range(505)
        ]
        db.executemany(
            """
            INSERT INTO workbench_tasks(
                task_id, name, description, workspace_id, workflow_id,
                workflow_version_id, lifecycle_status, input_values_json,
                execution_overrides_json, output_overrides_json, tags_json,
                last_run_id, created_at, updated_at, archived_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    result = await workbench_v2_tasks.list_tasks(page=21, page_size=25)

    assert result["total"] == 505
    assert len(result["items"]) == 5


@pytest.mark.asyncio
async def test_quality_review_result_persists_completed_execution_status(tmp_path, monkeypatch):
    from app.api import agent_workbench
    from app.config import settings
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer, WorkbenchTaskRunStore
    from app.services.workflow_dsl import WorkflowStore

    data_dir = tmp_path / "data"
    run_root = data_dir / "workbench" / "task_runs"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    workflow_store = WorkflowStore(data_dir / "workbench" / "task_workflows.db")
    workflow_store.save_workflow(_workflow())
    prepared = WorkbenchTaskRunPreparer(
        artifact_root=run_root,
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="source-review",
        workspace_id="ws-1",
        repo_path=str(repo),
        inputs={"target": "lib/nvmf"},
    )

    def execute_with_quality_outcome(**_kwargs):
        from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore

        WorkbenchTaskRunEventStore(run_root).mark_outcomes(
            prepared.task_run_id,
            quality_status="blocked",
            delivery_status="none",
        )
        return {
            "status": "needs_rework",
            "execution_status": "completed",
            "test_activity_quality": {"status": "needs_rework", "deliverable": False},
        }

    monkeypatch.setattr(
        agent_workbench,
        "_execute_task_run_with_closure",
        execute_with_quality_outcome,
    )
    await agent_workbench._execute_task_run_background(
        task_run_id=prepared.task_run_id,
        payload=agent_workbench.TaskRunExecuteRequest(),
    )

    stored = WorkbenchTaskRunStore(run_root).load(prepared.task_run_id)
    assert stored.execution_status == "completed"
    assert stored.quality_status == "blocked"


def test_retry_seed_results_reuse_only_successful_nodes_before_failure(tmp_path):
    from app.api.workbench_v2_tasks import _retry_seed_results_from_parent

    parent_dir = tmp_path / "parent"
    parent_dir.mkdir()
    (parent_dir / "workflow_execution.json").write_text(
        json.dumps({
            "status": "failed",
            "step_results": [
                {
                    "step_id": "discover",
                    "type": "local_scope_discover",
                    "status": "completed",
                    "artifact": "scope.json",
                    "validated_outputs": {"artifact": "scope.json"},
                },
                {"step_id": "analyze", "type": "agent_task", "status": "error"},
                {"step_id": "report", "type": "report_render", "status": "blocked"},
            ],
            "outputs": [],
        }),
        encoding="utf-8",
    )
    plan = {
        "nodes": [
            {"node_id": "discover", "depends_on": []},
            {"node_id": "analyze", "depends_on": ["discover"]},
            {"node_id": "report", "depends_on": ["analyze"]},
        ],
    }

    seeds, failed_nodes = _retry_seed_results_from_parent(
        SimpleNamespace(task_run_id="task_run_parent", artifact_dir=str(parent_dir)),
        plan,
    )

    assert failed_nodes == ["analyze"]
    assert set(seeds) == {"discover"}
    assert seeds["discover"]["reused_from_task_run_id"] == "task_run_parent"
    assert seeds["discover"]["validated_outputs"] == {"artifact": "scope.json"}


@pytest.mark.asyncio
async def test_background_exception_finishes_quality_in_blocked_state(tmp_path, monkeypatch):
    from app.api import agent_workbench
    from app.config import settings
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer, WorkbenchTaskRunStore
    from app.services.workflow_dsl import WorkflowStore

    data_dir = tmp_path / "data"
    run_root = data_dir / "workbench" / "task_runs"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    workflow_store = WorkflowStore(data_dir / "workbench" / "task_workflows.db")
    workflow_store.save_workflow(_workflow())
    prepared = WorkbenchTaskRunPreparer(
        artifact_root=run_root,
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="source-review",
        workspace_id="ws-1",
        repo_path=str(repo),
        inputs={"target": "lib/nvmf"},
    )

    def fail_execution(**_kwargs):
        raise RuntimeError("provider transport crashed")

    monkeypatch.setattr(agent_workbench, "_execute_task_run_with_closure", fail_execution)
    await agent_workbench._execute_task_run_background(
        task_run_id=prepared.task_run_id,
        payload=agent_workbench.TaskRunExecuteRequest(),
    )

    failed = WorkbenchTaskRunStore(run_root).load(prepared.task_run_id)
    assert failed.execution_status == "failed"
    assert failed.quality_status == "blocked"
    assert failed.delivery_status == "none"


@pytest.mark.asyncio
async def test_background_cancellation_never_leaves_task_run_stuck_running(tmp_path, monkeypatch):
    from app.api import agent_workbench
    from app.config import settings
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer, WorkbenchTaskRunStore
    from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore
    from app.services.workflow_dsl import WorkflowStore

    data_dir = tmp_path / "data"
    run_root = data_dir / "workbench" / "task_runs"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    workflow_store = WorkflowStore(data_dir / "workbench" / "task_workflows.db")
    workflow_store.save_workflow(_workflow())
    prepared = WorkbenchTaskRunPreparer(
        artifact_root=run_root,
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="source-review",
        workspace_id="ws-1",
        repo_path=str(repo),
        inputs={"target": "lib/nvmf"},
    )

    async def cancelled_to_thread(*_args, **_kwargs):
        raise asyncio.CancelledError()

    async def ready_preflight(_task_run_id):
        return {"status": "ok", "message": ""}

    monkeypatch.setattr(
        agent_workbench,
        "_preflight_task_run_agent_runtimes",
        ready_preflight,
    )
    monkeypatch.setattr(agent_workbench.asyncio, "to_thread", cancelled_to_thread)
    with pytest.raises(asyncio.CancelledError):
        await agent_workbench._execute_task_run_background(
            task_run_id=prepared.task_run_id,
            payload=agent_workbench.TaskRunExecuteRequest(),
        )

    event_store = WorkbenchTaskRunEventStore(run_root)
    assert event_store.current_status(prepared.task_run_id) == "interrupted"
    interrupted = WorkbenchTaskRunStore(run_root).load(prepared.task_run_id)
    assert interrupted.quality_status == "blocked"
    assert interrupted.delivery_status == "none"


@pytest.mark.asyncio
async def test_background_preflight_blocks_unready_agent_before_runner_starts(tmp_path, monkeypatch):
    from app.api import agent_workbench
    from app.config import settings
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer, WorkbenchTaskRunStore
    from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore
    from app.services.workflow_dsl import WorkflowStore

    data_dir = tmp_path / "data"
    run_root = data_dir / "workbench" / "task_runs"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    workflow_store = WorkflowStore(data_dir / "workbench" / "task_workflows.db")
    workflow_store.save_workflow(_workflow())
    prepared = WorkbenchTaskRunPreparer(
        artifact_root=run_root,
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="source-review",
        workspace_id="ws-1",
        repo_path=str(repo),
        inputs={"target": "lib/nvmf"},
    )
    runner_called = False

    async def blocked_preflight(task_run_id: str):
        return {
            "status": "blocked",
            "message": "所选 Agent 未通过启动前可用性检查：内网策略未批准 Agent 访问模型端点。",
        }

    def should_not_run(**_kwargs):
        nonlocal runner_called
        runner_called = True
        raise AssertionError("workflow runner must not be reached after a failed preflight")

    monkeypatch.setattr(agent_workbench, "_preflight_task_run_agent_runtimes", blocked_preflight)
    monkeypatch.setattr(agent_workbench, "_execute_task_run_with_closure", should_not_run)

    await agent_workbench._execute_task_run_background(
        task_run_id=prepared.task_run_id,
        payload=agent_workbench.TaskRunExecuteRequest(),
    )

    stored = WorkbenchTaskRunStore(run_root).load(prepared.task_run_id)
    assert runner_called is False
    assert stored.execution_status == "failed"
    assert stored.quality_status == "blocked"
    events = WorkbenchTaskRunEventStore(run_root).list_after(prepared.task_run_id)
    blocked = [item for item in events if item["event_type"] == "provider_readiness_blocked"]
    assert blocked
    assert blocked[-1]["event_kind"] == "error"
    assert "内网策略未批准" in blocked[-1]["payload"]["user_message"]


@pytest.mark.asyncio
async def test_managed_agent_preflight_uses_frozen_runtime_snapshot(tmp_path, monkeypatch):
    from app.api import agent_workbench

    task_run = SimpleNamespace(
        artifact_dir=str(tmp_path),
        task_bundle={
            "provider_snapshot": {
                "providers": {
                    "agent-runtime:default-codex": {
                        "runtime_id": "default-codex",
                        "runtime_provider": "codex",
                        "command": ["frozen-codex", "exec"],
                        "prompt_transport": "codex_exec_json",
                    }
                }
            }
        },
    )
    captured: list[dict] = []

    class _Store:
        def __init__(self, *_args):
            pass

        def load(self, _task_run_id):
            return task_run

    async def probe(runtime):
        captured.append(runtime)
        return {"success": True, "message": "ready"}

    def unexpected_legacy_lookup(*_args, **_kwargs):
        raise AssertionError("new run snapshots must not read mutable Agent settings")

    monkeypatch.setattr(agent_workbench, "WorkbenchTaskRunStore", _Store)
    monkeypatch.setattr(agent_workbench, "probe_agent_runtime", probe)
    monkeypatch.setattr(agent_workbench, "get_agent_runtime_sync", unexpected_legacy_lookup)

    result = await agent_workbench._preflight_task_run_agent_runtimes("task_run_frozen")

    assert result["status"] == "ready"
    assert captured == [{
        "id": "default-codex",
        "provider": "codex",
        "command": "frozen-codex",
        "args": ["exec"],
        "prompt_transport": "codex_exec_json",
        "enabled": True,
        "env": {},
    }]
    persisted = json.loads((tmp_path / "provider_live_readiness.json").read_text(encoding="utf-8"))
    assert persisted["checks"] == [{
        "provider": "agent-runtime:default-codex",
        "runtime_id": "default-codex",
        "success": True,
        "message": "ready",
    }]


def test_prepared_runs_persist_task_attempt_metadata_and_legacy_defaults(tmp_path):
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer, WorkbenchTaskRunStore
    from app.services.workflow_dsl import WorkflowStore

    repo = tmp_path / "repo"
    repo.mkdir()
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow(_workflow())
    run_store = WorkbenchTaskRunStore(tmp_path / "task-runs")
    prepared = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task-runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="source-review",
        workspace_id="ws-1",
        repo_path=str(repo),
        inputs={"target": "lib/nvmf"},
        task_id="task-1",
        attempt_number=2,
        parent_task_run_id="task_run_parent",
    )

    loaded = run_store.load(prepared.task_run_id)
    assert loaded.task_id == "task-1"
    assert loaded.attempt_number == 2
    assert loaded.parent_task_run_id == "task_run_parent"
    assert loaded.execution_status == "prepared"
    assert loaded.quality_status == "not_checked"
    assert loaded.delivery_status == "none"

    from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore

    WorkbenchTaskRunEventStore(tmp_path / "task-runs").mark_outcomes(
        prepared.task_run_id,
        quality_status="warning",
        delivery_status="partial",
    )
    updated_outcomes = run_store.load(prepared.task_run_id)
    assert updated_outcomes.quality_status == "warning"
    assert updated_outcomes.delivery_status == "partial"

    legacy_dir = tmp_path / "task-runs" / "task_run_legacy"
    legacy_dir.mkdir()
    legacy_payload = {
        "task_run_id": "task_run_legacy",
        "workflow_id": "source-review",
        "workspace_id": "ws-1",
        "repo_path": str(repo),
        "artifact_dir": str(legacy_dir),
        "workflow_snapshot": _workflow(),
        "input_snapshot": {"target": "legacy"},
        "task_bundle": {},
        "agent_runs": [],
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    (legacy_dir / "task_run.json").write_text(json.dumps(legacy_payload), encoding="utf-8")
    legacy = run_store.load("task_run_legacy")
    assert legacy.task_id == ""
    assert legacy.attempt_number == 0
    assert legacy.execution_status == "prepared"


@pytest.mark.asyncio
async def test_task_api_creates_filters_and_associates_multiple_attempts(tmp_path, monkeypatch):
    from app.api import agent_workbench, workbench_v2_tasks
    from app.config import settings
    from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore
    from app.services.workflow_version_store import WorkflowVersionStore

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    sqlite_db = data_dir / "codetalk.db"
    with sqlite3.connect(sqlite_db) as db:
        db.execute("CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT, repo_path TEXT)")
        db.execute(
            "INSERT INTO workspaces(id, name, repo_path) VALUES (?, ?, ?)",
            ("ws-1", "SPDK", str(repo)),
        )
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "sqlite_db", str(sqlite_db))
    monkeypatch.setattr(settings, "workbench_v2_enabled", True)

    version_store = WorkflowVersionStore(data_dir / "workbench" / "workflows.db")
    version_store.initialize_and_migrate()
    _, draft = version_store.create_workflow(
        workflow_id="source-review",
        name="Source review",
        description="Read source",
        authoring_graph={"schema_version": 2, "workflow_id": "source-review"},
    )
    workflow_definition = _workflow()
    workflow_definition["execution_profiles"] = [
        {
            "id": "rapid",
            "label": "速度型",
            "delivery_class": "bounded_analysis",
            "expected_duration_minutes": [10, 25],
            "max_subagents": 1,
        },
        {
            "id": "deep",
            "label": "深度型",
            "delivery_class": "full_test_delivery",
            "expected_duration_minutes": [45, 90],
            "max_subagents": 4,
        },
    ]
    workflow_definition["default_execution_profile"] = "rapid"
    published = version_store.publish_version(
        draft.version_id,
        authoring_graph=draft.authoring_graph,
        compiled_definition=workflow_definition,
        compiled_plan={
            "plan_version": 1,
            "workflow_version_id": draft.version_id,
            "topological_order": ["scope"],
            "nodes": [
                {
                    "node_id": "scope",
                    "type": "local_scope_discover",
                    "depends_on": [],
                    "failure_policy": "stop",
                }
            ],
            "max_parallelism": 1,
            "stop_on_error": True,
        },
        validation={"valid": True, "errors": [], "warnings": []},
    )

    app = FastAPI()
    app.include_router(agent_workbench.router)
    app.include_router(workbench_v2_tasks.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        empty_page = await client.get("/api/workbench/tasks")
        assert empty_page.status_code == 200
        assert empty_page.json()["page_size"] == 25
        assert (await client.get(
            "/api/workbench/tasks", params={"page_size": 101}
        )).status_code == 422

        created = await client.post(
            "/api/workbench/tasks",
            json={
                "name": "SPDK source review",
                "description": "nvmf flow",
                "workspace_id": "ws-1",
                "workflow_id": "source-review",
                "workflow_version_id": published.version_id,
                "lifecycle_status": "ready",
                "input_values": {"target": "lib/nvmf"},
                "output_overrides": {
                    "outputs": {"report": {"artifact": "task-report.md", "label": "Task report"}}
                },
                "tags": ["storage"],
            },
        )
        assert created.status_code == 201
        task_id = created.json()["task_id"]

        compiled = await client.post(f"/api/workbench/tasks/{task_id}/compile")

        first = await client.post(
            f"/api/workbench/tasks/{task_id}/runs",
            json={"execution_profile_id": "deep"},
        )
        first_run_dir = data_dir / "workbench" / "task_runs" / first.json()["task_run_id"]
        (first_run_dir / "workflow_execution.json").write_text(
            json.dumps({
                "status": "failed",
                "step_results": [
                    {"step_id": "scope", "type": "local_scope_discover", "status": "error"}
                ],
                "outputs": [],
            }),
            encoding="utf-8",
        )
        changed_after_first = await client.patch(
            f"/api/workbench/tasks/{task_id}",
            json={
                "input_values": {"target": "lib/changed-after-first"},
                "output_overrides": {
                    "outputs": {"report": {"artifact": "changed-after-first.md"}}
                },
            },
        )
        second = await client.post(
            f"/api/workbench/tasks/{task_id}/runs",
            json={"parent_task_run_id": first.json()["task_run_id"]},
        )
        profile_switch_retry = await client.post(
            f"/api/workbench/tasks/{task_id}/runs",
            json={
                "parent_task_run_id": first.json()["task_run_id"],
                "execution_profile_id": "rapid",
            },
        )
        listed = await client.get(
            "/api/workbench/tasks",
            params={
                "q": "source",
                "lifecycle_status": "ready",
                "workflow_id": "source-review",
                "workspace_id": "ws-1",
                "execution_status": "prepared",
            },
        )
        detail = await client.get(f"/api/workbench/tasks/{task_id}")
        immutable = await client.patch(
            f"/api/workbench/tasks/{task_id}",
            json={"workflow_version_id": "wfv-other"},
        )
        event_store = WorkbenchTaskRunEventStore(data_dir / "workbench" / "task_runs")
        event_store.mark_status(second.json()["task_run_id"], "running")
        archive_blocked = await client.post(f"/api/workbench/tasks/{task_id}/archive")
        event_store.mark_status(second.json()["task_run_id"], "failed")
        archived = await client.post(f"/api/workbench/tasks/{task_id}/archive")

    assert first.status_code == 201
    assert changed_after_first.status_code == 200
    assert first.json()["attempt_number"] == 1
    assert second.status_code == 201
    assert second.json()["attempt_number"] == 2
    assert second.json()["parent_task_run_id"] == first.json()["task_run_id"]
    assert profile_switch_retry.status_code == 422
    assert "沿用父运行" in profile_switch_retry.json()["detail"]
    assert listed.status_code == 200
    assert [item["task_id"] for item in listed.json()["items"]] == [task_id]
    assert listed.json()["items"][0]["latest_run"]["attempt_number"] == 2
    assert detail.status_code == 200
    assert [run["attempt_number"] for run in detail.json()["runs"]] == [2, 1]
    assert immutable.status_code == 422
    assert archive_blocked.status_code == 409
    assert "取消" in archive_blocked.json()["detail"]
    assert archived.status_code == 200
    assert archived.json()["lifecycle_status"] == "archived"
    assert compiled.status_code == 200
    assert compiled.json()["compiled_definition"]["outputs"][0]["artifact"] == "task-report.md"
    run_bundle = json.loads(
        (data_dir / "workbench" / "task_runs" / first.json()["task_run_id"] / "task_run.json").read_text(encoding="utf-8")
    )["task_bundle"]
    retried_bundle = json.loads(
        (data_dir / "workbench" / "task_runs" / second.json()["task_run_id"] / "task_run.json").read_text(encoding="utf-8")
    )["task_bundle"]
    retried_bundle_artifact = json.loads(
        (data_dir / "workbench" / "task_runs" / second.json()["task_run_id"] / "task_bundle.json").read_text(encoding="utf-8")
    )
    assert run_bundle["effective_compiled_definition"]["outputs"][0]["artifact"] == "task-report.md"
    assert run_bundle["execution_profile"]["id"] == "deep"
    assert retried_bundle["inputs"]["target"] == "lib/nvmf"
    assert retried_bundle["effective_compiled_definition"]["outputs"][0]["artifact"] == "task-report.md"
    assert retried_bundle["retry_source"] == {
        "task_run_id": first.json()["task_run_id"],
        "mode": "from_failed_node",
        "failed_node_ids": ["scope"],
    }
    assert retried_bundle["retry_seed_results"] == {}
    assert retried_bundle_artifact == retried_bundle
    assert published.compiled_definition["outputs"][0]["artifact"] == "report.md"


@pytest.mark.asyncio
async def test_task_api_accepts_a_migrated_builtin_style_workflow(tmp_path, monkeypatch):
    from app.api import agent_workbench, workbench_v2_tasks
    from app.config import settings
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workflow_version_store import WorkflowVersionStore

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    sqlite_db = data_dir / "codetalk.db"
    with sqlite3.connect(sqlite_db) as db:
        db.execute("CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT, repo_path TEXT)")
        db.execute(
            "INSERT INTO workspaces(id, name, repo_path) VALUES (?, ?, ?)",
            ("ws-legacy", "Legacy repo", str(repo)),
        )
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "sqlite_db", str(sqlite_db))
    monkeypatch.setattr(settings, "workbench_v2_enabled", True)

    db_path = data_dir / "workbench" / "workflows.db"
    legacy_workflow = _workflow()
    legacy_workflow["inputs"].insert(
        0,
        {
            "id": "repo_path",
            "type": "directory",
            "required": True,
            "resolver": "local",
        },
    )
    WorkflowStore(db_path).save_workflow(legacy_workflow)
    version_store = WorkflowVersionStore(db_path)
    migration = version_store.initialize_and_migrate()
    header = version_store.get_workflow("source-review")
    published = version_store.get_version(header.published_version_id)

    assert migration["upgraded_workflows"] == 1
    assert published.compiled_plan is not None

    app = FastAPI()
    app.include_router(agent_workbench.router)
    app.include_router(workbench_v2_tasks.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/workbench/tasks",
            json={
                "name": "Migrated workflow task",
                "workspace_id": "ws-legacy",
                "workflow_id": "source-review",
                "workflow_version_id": published.version_id,
                "lifecycle_status": "ready",
                "input_values": {
                    "target": "lib/nvmf",
                    "repo_path": "/tmp/forged-repository",
                },
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["input_values"] == {"target": "lib/nvmf"}
        compiled = await client.post(
            f"/api/workbench/tasks/{created.json()['task_id']}/compile"
        )
        attempt = await client.post(
            f"/api/workbench/tasks/{created.json()['task_id']}/runs",
            json={},
        )

    assert compiled.status_code == 200, compiled.text
    assert attempt.status_code == 201, attempt.text
    assert compiled.json()["compiled_plan"]["compatibility_mode"] == "legacy_sequential"
    assert compiled.json()["compiled_definition"]["id"] == "source-review"
    run_payload = json.loads(
        (
            data_dir
            / "workbench"
            / "task_runs"
            / attempt.json()["task_run_id"]
            / "task_run.json"
        ).read_text(encoding="utf-8")
    )
    assert run_payload["input_snapshot"]["repo_path"] == str(repo)


@pytest.mark.asyncio
async def test_task_api_rejects_draft_workflow_and_lists_legacy_runs(tmp_path, monkeypatch):
    from app.api import agent_workbench, workbench_v2_tasks
    from app.config import settings
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workflow_version_store import WorkflowVersionStore

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    sqlite_db = data_dir / "codetalk.db"
    with sqlite3.connect(sqlite_db) as db:
        db.execute("CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT, repo_path TEXT)")
        db.execute("INSERT INTO workspaces VALUES (?, ?, ?)", ("ws-1", "Repo", str(repo)))
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "sqlite_db", str(sqlite_db))
    monkeypatch.setattr(settings, "workbench_v2_enabled", True)

    version_store = WorkflowVersionStore(data_dir / "workbench" / "workflows.db")
    _, draft = version_store.create_workflow(
        workflow_id="draft-flow",
        name="Draft",
        description="",
        authoring_graph={"schema_version": 2, "workflow_id": "draft-flow"},
    )
    legacy_store = WorkflowStore(data_dir / "workbench" / "legacy.db")
    legacy_store.save_workflow(_workflow())
    legacy = WorkbenchTaskRunPreparer(
        artifact_root=data_dir / "workbench" / "task_runs",
        workflow_store=legacy_store,
    ).prepare(
        workflow_id="source-review",
        workspace_id="ws-1",
        repo_path=str(repo),
        inputs={"target": "legacy"},
    )

    app = FastAPI()
    app.include_router(agent_workbench.router)
    app.include_router(workbench_v2_tasks.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rejected = await client.post(
            "/api/workbench/tasks",
            json={
                "name": "Draft task",
                "workspace_id": "ws-1",
                "workflow_id": "draft-flow",
                "workflow_version_id": draft.version_id,
            },
        )
        history = await client.get("/api/workbench/tasks/history/runs")

    assert rejected.status_code == 422
    assert "已发布" in rejected.json()["detail"]
    assert history.status_code == 200
    assert [item["task_run_id"] for item in history.json()["items"]] == [legacy.task_run_id]
    assert history.json()["items"][0]["legacy"] is True


@pytest.mark.asyncio
async def test_new_builtin_task_rejects_superseded_published_version(tmp_path, monkeypatch):
    from app.api import agent_workbench, workbench_v2_tasks
    from app.config import settings
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workflow_presets import get_workflow_preset
    from app.services.workflow_version_store import WorkflowVersionStore

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    sqlite_db = data_dir / "codetalk.db"
    with sqlite3.connect(sqlite_db) as db:
        db.execute("CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT, repo_path TEXT)")
        db.execute("INSERT INTO workspaces VALUES (?, ?, ?)", ("ws-1", "SPDK", str(repo)))
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "sqlite_db", str(sqlite_db))
    monkeypatch.setattr(settings, "workbench_v2_enabled", True)

    definition = get_workflow_preset("module_analysis")["definition"]
    db_path = data_dir / "workbench" / "workflows.db"
    WorkflowStore(db_path).save_workflow(definition)
    version_store = WorkflowVersionStore(db_path)
    version_store.initialize_and_migrate()
    old_version_id = version_store.get_workflow("module_analysis").published_version_id
    with sqlite3.connect(db_path) as db:
        db.execute(
            "UPDATE workflow_versions SET compiled_plan_json = ? WHERE version_id = ?",
            (
                json.dumps({
                    "plan_version": 1,
                    "workflow_version_id": old_version_id,
                    "topological_order": ["shadow"],
                    "nodes": [],
                    "shadow_plan": True,
                }),
                old_version_id,
            ),
        )
    assert version_store.ensure_legacy_published_workflows([definition]) == 1
    current_version_id = version_store.get_workflow("module_analysis").published_version_id
    assert current_version_id != old_version_id
    assert version_store.retire_workflows({"module_analysis"}) == 1

    historical = workbench_v2_tasks.task_store().create_task(
        name="Historical built-in task",
        workspace_id="ws-1",
        workflow_id="module_analysis",
        workflow_version_id=old_version_id,
    )
    request = {
        "name": "New built-in task",
        "workspace_id": "ws-1",
        "workflow_id": "module_analysis",
        "lifecycle_status": "draft",
    }
    app = FastAPI()
    app.include_router(agent_workbench.router)
    app.include_router(workbench_v2_tasks.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rejected = await client.post(
            "/api/workbench/tasks",
            json={**request, "workflow_version_id": old_version_id},
        )
        retired_current = await client.post(
            "/api/workbench/tasks",
            json={**request, "workflow_version_id": current_version_id},
        )
        historical_detail = await client.get(f"/api/workbench/tasks/{historical.task_id}")
        clone_rejected = await client.post(
            f"/api/workbench/tasks/{historical.task_id}/clone",
            json={"name": "Superseded clone"},
        )

    assert rejected.status_code == 409
    assert "已下线" in rejected.json()["detail"]
    assert retired_current.status_code == 409, retired_current.text
    assert "已下线" in retired_current.json()["detail"]
    assert historical_detail.status_code == 200, historical_detail.text
    assert historical_detail.json()["workflow_version_id"] == old_version_id
    assert clone_rejected.status_code == 409
    assert "已下线" in clone_rejected.json()["detail"]


@pytest.mark.asyncio
async def test_archived_custom_workflow_rejects_new_task_and_clone(tmp_path, monkeypatch):
    from app.api import agent_workbench, workbench_v2_tasks
    from app.config import settings
    from app.services.workflow_version_store import WorkflowVersionStore

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    sqlite_db = data_dir / "codetalk.db"
    with sqlite3.connect(sqlite_db) as db:
        db.execute("CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT, repo_path TEXT)")
        db.execute("INSERT INTO workspaces VALUES (?, ?, ?)", ("ws-1", "SPDK", str(repo)))
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "sqlite_db", str(sqlite_db))
    monkeypatch.setattr(settings, "workbench_v2_enabled", True)

    definition = _workflow()
    definition["id"] = "archived-custom"
    definition["name"] = "Archived custom"
    db_path = data_dir / "workbench" / "workflows.db"
    version_store = WorkflowVersionStore(db_path)
    _, draft = version_store.create_workflow(
        workflow_id=definition["id"],
        name=definition["name"],
        description="",
        authoring_graph={"schema_version": 2, "workflow_id": definition["id"]},
    )
    published = version_store.publish_version(
        draft.version_id,
        authoring_graph=draft.authoring_graph,
        compiled_definition=definition,
        compiled_plan={
            "plan_version": 1,
            "workflow_version_id": draft.version_id,
            "topological_order": ["scope"],
            "nodes": [{"node_id": "scope", "type": "local_scope_discover"}],
        },
        validation={"valid": True, "errors": [], "warnings": []},
    )
    historical = workbench_v2_tasks.task_store().create_task(
        name="Historical archived custom task",
        workspace_id="ws-1",
        workflow_id=definition["id"],
        workflow_version_id=published.version_id,
    )
    version_store.archive_workflow(definition["id"])

    request = {
        "name": "New archived custom task",
        "workspace_id": "ws-1",
        "workflow_id": definition["id"],
        "workflow_version_id": published.version_id,
        "lifecycle_status": "draft",
    }
    app = FastAPI()
    app.include_router(agent_workbench.router)
    app.include_router(workbench_v2_tasks.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rejected_create = await client.post("/api/workbench/tasks", json=request)
        historical_detail = await client.get(f"/api/workbench/tasks/{historical.task_id}")
        rejected_clone = await client.post(
            f"/api/workbench/tasks/{historical.task_id}/clone",
            json={"name": "Archived custom clone"},
        )

    assert rejected_create.status_code == 409
    assert rejected_clone.status_code == 409
    assert "已归档" in rejected_create.json()["detail"]
    assert "已归档" in rejected_clone.json()["detail"]
    assert historical_detail.status_code == 200
    assert historical_detail.json()["workflow_version_id"] == published.version_id
