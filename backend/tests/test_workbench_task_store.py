import asyncio
import json
import logging
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


def _publish_test_skill_version(data_dir, source_root):
    from app.services.skill_build_pipeline import SkillBuildPipeline
    from app.services.skill_store import SkillStore
    from test_skill_build_pipeline import _full_review_evidence, _record_review, _write_v24_source

    _write_v24_source(source_root)
    skill_store = SkillStore(data_dir / "skills" / "skills.db", data_dir)
    project = skill_store.create_project(name="CodeTalk Pack", pack_id="pack.codetalks")
    draft = skill_store.create_draft_from_source(
        project_id=project.project_id,
        source_root=source_root,
        source_scenario_id="module-analysis",
        skill_id="skill.codetalks-module-full-analysis",
    )
    pipeline = SkillBuildPipeline(skill_store)
    build = pipeline.build_candidate(draft.draft_id)
    _record_review(skill_store, build.build_id, _full_review_evidence(decision="approved"))
    return pipeline.publish_build(build.build_id)


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
    (parent_agent / "entrypoints.json").write_text("[]", encoding="utf-8")
    (parent_agent / "flows.json").write_text("[]", encoding="utf-8")
    (parent_agent / "scenario_candidates.json").write_text("[]", encoding="utf-8")
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
    assert prepared.task_bundle["quality_retry_seed"]["copied_support_files"] == [
        "analyze:entrypoints.json",
        "analyze:flows.json",
        "analyze:scenario_candidates.json",
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


def test_quality_retry_does_not_reuse_outputs_when_final_acceptance_is_invalid(
    tmp_path,
    monkeypatch,
):
    from app.api import workbench_v2_tasks
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner

    parent_root = tmp_path / "workbench" / "task_runs" / "task_run_parent"
    parent_agent = parent_root / "agent_runs" / "analyze"
    child_root = tmp_path / "child"
    child_agent = child_root / "agent_runs" / "analyze"
    parent_agent.mkdir(parents=True)
    child_agent.mkdir(parents=True)
    (parent_agent / "black_box_cases.json").write_text("[]", encoding="utf-8")
    (parent_root / "task_acceptance_audit.json").write_text(
        json.dumps({"checks": [{
            "status": "invalid",
            "reason": "black_box_case_quality_failed",
            "relative_path": "agent_runs/analyze/black_box_cases.json",
            "invalid_cases": [{"case_id": "BB-09", "reasons": ["vague_steps"]}],
        }]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        WorkbenchWorkflowRunner,
        "audit_test_activity_quality",
        lambda self, *, task_run: {
            "status": "deliverable", "deliverable": True, "issue_count": 0, "issues": []
        },
    )
    parent = SimpleNamespace(
        task_run_id="task_run_parent",
        quality_status="blocked",
        artifact_dir=str(parent_root),
        agent_runs=[{
            "step_id": "analyze",
            "artifact_dir": str(parent_agent),
            "required_artifacts": ["black_box_cases.json"],
        }],
    )
    prepared = SimpleNamespace(
        artifact_dir=str(child_root),
        task_bundle={"retry_seed_results": {"analyze": {"status": "completed"}}},
        agent_runs=[{
            "step_id": "analyze",
            "artifact_dir": str(child_agent),
            "required_artifacts": ["black_box_cases.json"],
        }],
    )

    workbench_v2_tasks._seed_quality_retry_from_parent(
        parent_run=parent,
        prepared=prepared,
    )

    assert prepared.task_bundle["retry_seed_results"] == {}
    assert prepared.task_bundle["retry_source"]["mode"] == "quality_repair"
    assert prepared.task_bundle["retry_source"]["failed_node_ids"] == ["analyze"]
    seeded_audit = json.loads((child_root / "test_activity_quality_audit.json").read_text())
    assert seeded_audit["issues"][0]["code"] == "black_box_case_quality_failed"


def test_task_store_migration_crud_filters_archive_and_clone(tmp_path):
    from app.services.workbench_task_store import WorkbenchTaskStore

    db_path = tmp_path / "workflows.db"
    store = WorkbenchTaskStore(db_path)

    first = store.initialize_and_migrate()
    second = store.initialize_and_migrate()

    assert first["schema_version"] == 3
    assert second["schema_version"] == 3
    with sqlite3.connect(db_path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(workbench_tasks)")}
    assert {
        "task_id", "name", "workspace_id", "skill_id", "skill_version_id",
        "skill_content_digest",
        "lifecycle_status", "execution_profile_id", "input_values_json", "execution_overrides_json",
        "output_overrides_json", "tags_json", "last_run_id", "archived_at",
    }.issubset(columns)
    assert "workflow_id" not in columns
    assert "workflow_version_id" not in columns

    task = store.create_task(
        name="SPDK source review",
        description="Review nvmf flow",
        workspace_id="ws-spdk",
        skill_id="skill.source-review",
        skill_version_id="skill_version_1",
        skill_content_digest="sha256:" + "1" * 64,
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
    assert updated.skill_version_id == "skill_version_1"
    assert updated.skill_content_digest == "sha256:" + "1" * 64
    assert updated.input_values == {"target": "lib/nvmf/ctrlr.c"}
    assert store.list_tasks(q="nvmf", lifecycle_status="ready") == [updated]
    assert store.list_tasks(skill_id="skill.source-review", workspace_id="ws-spdk") == [updated]

    clone = store.clone_task(task.task_id, name="SPDK NVMf review copy")
    assert clone.task_id != task.task_id
    assert clone.skill_version_id == task.skill_version_id
    assert clone.skill_content_digest == task.skill_content_digest
    assert clone.lifecycle_status == "draft"
    archived = store.archive_task(task.task_id)
    assert archived.lifecycle_status == "archived"
    assert archived.archived_at
    assert store.get_task(task.task_id).task_id == task.task_id


def test_task_store_rejects_skill_identity_mutation(tmp_path):
    from app.services.workbench_task_store import WorkbenchTaskStore

    store = WorkbenchTaskStore(tmp_path / "workflows.db")
    task = store.create_task(
        name="Frozen Skill task",
        workspace_id="ws-1",
        skill_id="skill.flow-1",
        skill_version_id="skill_version_1",
        skill_content_digest="sha256:" + "1" * 64,
    )

    try:
        store.update_task(task.task_id, skill_version_id="skill_version_2")
    except ValueError as exc:
        assert "skill_version_id" in str(exc)
    else:
        raise AssertionError("Skill Version mutation must be rejected")


def test_task_store_destructively_rebuilds_legacy_workflow_binding_with_backup(tmp_path):
    from app.services.workbench_task_store import WorkbenchTaskStore

    db_path = tmp_path / "workflows.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE workbench_tasks (
                task_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                workspace_id TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                workflow_version_id TEXT NOT NULL,
                lifecycle_status TEXT NOT NULL,
                input_values_json TEXT NOT NULL,
                execution_overrides_json TEXT NOT NULL,
                output_overrides_json TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                last_run_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived_at TEXT
            )
            """
        )
        db.execute(
            """
            INSERT INTO workbench_tasks VALUES (
                'task_legacy', 'Legacy', '', 'ws-1', 'flow-1', 'wfv-1',
                'draft', '{}', '{}', '{}', '[]', NULL,
                '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', NULL
            )
            """
        )

    result = WorkbenchTaskStore(db_path).initialize_and_migrate()

    assert result["schema_version"] == 3
    backups = list(tmp_path.glob("workflows.pre-workbench-v2.*.bak"))
    assert backups
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("SELECT workflow_id FROM workbench_tasks").fetchone() == ("flow-1",)
    with sqlite3.connect(db_path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(workbench_tasks)")}
        assert "workflow_id" not in columns
        assert {"skill_id", "skill_version_id", "skill_content_digest"}.issubset(columns)
        assert db.execute("SELECT COUNT(*) FROM workbench_tasks").fetchone() == (0,)


@pytest.mark.asyncio
async def test_task_api_binds_skill_version_without_workflow_fields(tmp_path, monkeypatch):
    from app.api import workbench_v2_tasks
    from app.config import settings
    from app.services.skill_build_pipeline import SkillBuildPipeline
    from app.services.skill_store import SkillStore
    from test_skill_build_pipeline import _full_review_evidence, _record_review, _write_v24_source

    data_dir = tmp_path / "data"
    repo = tmp_path / "repo"
    source = tmp_path / "source"
    data_dir.mkdir()
    repo.mkdir()
    _write_v24_source(source)
    sqlite_db = data_dir / "codetalk.db"
    with sqlite3.connect(sqlite_db) as db:
        db.execute("CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT, repo_path TEXT)")
        db.execute("INSERT INTO workspaces VALUES (?, ?, ?)", ("ws-1", "SPDK", str(repo)))
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "sqlite_db", str(sqlite_db))
    monkeypatch.setattr(settings, "workbench_v2_enabled", True)

    skill_store = SkillStore(data_dir / "skills" / "skills.db", data_dir)
    project = skill_store.create_project(name="CodeTalk Pack", pack_id="pack.codetalks")
    draft = skill_store.create_draft_from_source(
        project_id=project.project_id,
        source_root=source,
        source_scenario_id="module-analysis",
        skill_id="skill.codetalks-module-full-analysis",
    )
    pipeline = SkillBuildPipeline(skill_store)
    build = pipeline.build_candidate(draft.draft_id)
    _record_review(skill_store, build.build_id, _full_review_evidence(decision="approved"))
    version = pipeline.publish_build(build.build_id)

    app = FastAPI()
    app.include_router(workbench_v2_tasks.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/workbench/tasks",
            json={
                "name": "Skill-first module analysis",
                "workspace_id": "ws-1",
                "skill_version_id": version.version_id,
                "lifecycle_status": "ready",
                "input_values": {"repo_path": "/must/be/ignored"},
                "output_overrides": {"selected_deliveries": []},
            },
        )
        rejected_workflow_payload = await client.post(
            "/api/workbench/tasks",
            json={
                "name": "Legacy workflow payload",
                "workspace_id": "ws-1",
                "workflow_id": "module_analysis",
                "workflow_version_id": "wfv-1",
                "skill_version_id": version.version_id,
            },
        )
        listed = await client.get(
            "/api/workbench/tasks",
            params={"skill_id": version.skill_id, "workspace_id": "ws-1"},
        )
        detail = await client.get(f"/api/workbench/tasks/{created.json()['task_id']}")
        compiled = await client.post(f"/api/workbench/tasks/{created.json()['task_id']}/compile")
        immutable = await client.patch(
            f"/api/workbench/tasks/{created.json()['task_id']}",
            json={"skill_version_id": "skill_version_other"},
        )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["skill_id"] == version.skill_id
    assert body["skill_version_id"] == version.version_id
    assert body["skill_content_digest"] == version.content_digest
    assert "workflow_id" not in body
    assert "workflow_version_id" not in body
    assert body["input_values"] == {}
    assert rejected_workflow_payload.status_code == 422
    assert listed.status_code == 200
    assert [item["task_id"] for item in listed.json()["items"]] == [body["task_id"]]
    assert detail.status_code == 200
    assert detail.json()["skill_version"]["version_id"] == version.version_id
    assert compiled.status_code == 200
    assert compiled.json()["skill_version"]["content_digest"] == version.content_digest
    assert compiled.json()["skill_ir"]["skill_id"] == version.skill_id
    assert compiled.json()["skill_plan"]["compiled_contract_version"] == 3
    assert immutable.status_code == 422


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


def test_run_outcomes_keep_limited_coverage_warning_downloadable_not_blocked():
    from app.api.agent_workbench import _derive_task_run_outcomes

    quality_status, delivery_status = _derive_task_run_outcomes(
        execution={"test_activity_quality": {
            "status": "warning",
            "deliverable": False,
            "issue_count": 0,
            "quality_axes": {"coverage_breadth": {"status": "warning"}},
        }},
        run_summary={"nodes": [{"outputs": [
            {"artifact": "report.md", "path": "agent/report.md", "status_label": "已生成"},
            {"artifact": "cases.json", "path": "agent/cases.json", "status_label": "已生成"},
        ]}]},
    )

    assert quality_status == "warning"
    assert delivery_status == "partial"


def test_run_outcomes_never_mark_quality_blocked_artifacts_as_formal_delivery():
    from app.api.agent_workbench import _derive_task_run_outcomes

    quality_status, delivery_status = _derive_task_run_outcomes(
        execution={
            "test_activity_quality": {
                "deliverable": False,
                "issue_count": 3,
            }
        },
        run_summary={
            "nodes": [{
                "outputs": [
                    {
                        "artifact": "report.md",
                        "path": "agent/report.md",
                        "status_label": "可下载",
                    },
                    {
                        "artifact": "sfmea.json",
                        "path": "agent/sfmea.json",
                        "status_label": "可下载",
                    },
                ]
            }]
        },
    )

    assert quality_status == "blocked"
    assert delivery_status == "none"


def test_opening_run_reconciles_stale_blocked_delivery_status(tmp_path, monkeypatch):
    from app.api import agent_workbench
    from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore
    from app.services.workbench_task_run import WorkbenchTaskRunStore

    monkeypatch.setattr(agent_workbench, "_task_runs_dir", lambda: tmp_path)
    task_run_id = "task-run-stale-delivery"
    artifact_dir = tmp_path / task_run_id
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "task_run.json").write_text(
        json.dumps(
            {
                "task_run_id": task_run_id,
                "workflow_id": "workflow-stale-delivery",
                "workspace_id": "workspace-stale-delivery",
                "repo_path": str(tmp_path),
                "artifact_dir": str(artifact_dir),
                "workflow_snapshot": {},
                "input_snapshot": {},
                "task_bundle": {},
                "execution_status": "completed",
                "quality_status": "blocked",
                "delivery_status": "complete",
            }
        ),
        encoding="utf-8",
    )
    (artifact_dir / "workflow_execution.json").write_text(
        json.dumps({"test_activity_quality": {"deliverable": False, "issue_count": 2}}),
        encoding="utf-8",
    )
    WorkbenchTaskRunEventStore(tmp_path).mark_outcomes(
        task_run_id,
        quality_status="blocked",
        delivery_status="complete",
    )

    reconciled = agent_workbench._reconcile_persisted_task_run_outcomes(
        WorkbenchTaskRunStore(tmp_path).load(task_run_id)
    )

    assert reconciled.quality_status == "blocked"
    assert reconciled.delivery_status == "none"


def test_node_diagnostic_trial_is_never_a_formal_delivery():
    from app.api.agent_workbench import _is_diagnostic_trial

    assert _is_diagnostic_trial(
        SimpleNamespace(
            task_bundle={
                "diagnostic": {
                    "kind": "node_trial",
                    "not_a_formal_delivery": True,
                }
            }
        )
    )
    assert not _is_diagnostic_trial(SimpleNamespace(task_bundle={}))


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
                f"task-{index:04d}", f"Task {index:04d}", "", "ws",
                "skill.flow", "skill_version_1", "sha256:" + "1" * 64,
                "draft", "{}", "{}", "{}", "[]", None,
                f"2026-07-13T00:{index // 60:02d}:{index % 60:02d}+00:00",
                f"2026-07-13T00:{index // 60:02d}:{index % 60:02d}+00:00", None,
            )
            for index in range(505)
        ]
        db.executemany(
            """
            INSERT INTO workbench_tasks(
                task_id, name, description, workspace_id, skill_id,
                skill_version_id, skill_content_digest, lifecycle_status, input_values_json,
                execution_overrides_json, output_overrides_json, tags_json,
                last_run_id, created_at, updated_at, archived_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    events = WorkbenchTaskRunEventStore(run_root).list_after(prepared.task_run_id)
    assert events[-1]["event_type"] == "quality_blocked"
    assert events[-1]["payload"]["status"] == "quality_blocked"
    assert events[-1]["payload"]["execution_status"] == "completed"


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
async def test_background_exception_logs_only_redacted_traceback(
    tmp_path,
    monkeypatch,
    caplog,
):
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

    secret = "backgroundBearerSecret1234567890"

    def fail_execution(**_kwargs):
        raise RuntimeError(
            f"provider transport crashed Authorization: Bearer {secret}"
        )

    monkeypatch.setattr(agent_workbench, "_execute_task_run_with_closure", fail_execution)
    caplog.set_level(logging.ERROR, logger="app.api.agent_workbench")
    await agent_workbench._execute_task_run_background(
        task_run_id=prepared.task_run_id,
        payload=agent_workbench.TaskRunExecuteRequest(),
    )

    failed = WorkbenchTaskRunStore(run_root).load(prepared.task_run_id)
    assert failed.execution_status == "failed"
    assert failed.quality_status == "blocked"
    assert failed.delivery_status == "none"
    assert "Traceback (most recent call last)" in caplog.text
    assert "Authorization: Bearer <redacted>" in caplog.text
    assert secret not in caplog.text
    assert all(secret not in str(record.exc_text or "") for record in caplog.records)


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
            "message": "所选 Agent 未通过启动前可用性检查：运行环境暂时无法连接模型端点。",
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
    assert "运行环境暂时无法连接模型端点" in blocked[-1]["payload"]["user_message"]


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
                        "requires_network": False,
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
        "requires_network": False,
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
    from app.api import workbench_v2_tasks
    from app.config import settings
    from app.services.artifact_profiles import ArtifactProfileStore
    from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore

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

    version = _publish_test_skill_version(data_dir, tmp_path / "source")
    artifact_profile = ArtifactProfileStore(
        data_dir / "workbench" / "artifact_profiles.db"
    ).create_profile(
        {
            "name": "Protocol review",
            "artifacts": [
                {
                    "id": "review",
                    "filename": "protocol-review.md",
                    "format": "markdown",
                    "required": True,
                }
            ],
        }
    )
    other_artifact_profile = ArtifactProfileStore(
        data_dir / "workbench" / "artifact_profiles.db"
    ).create_profile(
        {
            "name": "Other output",
            "artifacts": [
                {
                    "id": "other",
                    "filename": "other.md",
                    "format": "markdown",
                    "required": True,
                }
            ],
        }
    )

    app = FastAPI()
    app.include_router(workbench_v2_tasks.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        empty_page = await client.get("/api/workbench/tasks")
        oversized_page = await client.get("/api/workbench/tasks", params={"page_size": 101})
        created = await client.post(
            "/api/workbench/tasks",
            json={
                "name": "SPDK Skill review",
                "description": "nvmf flow",
                "workspace_id": "ws-1",
                "skill_version_id": version.version_id,
                "lifecycle_status": "ready",
                "input_values": {"input.source": "/ignored"},
                "output_overrides": {"selected_deliveries": ["delivery.developer-test-code-explanation"]},
                "tags": ["storage"],
            },
        )
        task_id = created.json()["task_id"]
        listed = await client.get(
            "/api/workbench/tasks",
            params={"q": "skill", "skill_id": version.skill_id, "workspace_id": "ws-1"},
        )
        compiled = await client.post(f"/api/workbench/tasks/{task_id}/compile")
        unknown_profile = await client.post(
            f"/api/workbench/tasks/{task_id}/runs",
            json={"artifact_profile_id": "apro_missing"},
        )
        first = await client.post(
            f"/api/workbench/tasks/{task_id}/runs",
            json={"artifact_profile_id": artifact_profile["id"]},
        )
        first_run_dir = data_dir / "workbench" / "task_runs" / first.json()["task_run_id"]
        from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
        execution = WorkbenchWorkflowRunner(data_dir / "workbench" / "task_runs").execute_task_run(
            first.json()["task_run_id"],
            stop_on_error=True,
        )
        failed_node_id = compiled.json()["skill_plan"]["topological_order"][0]
        (first_run_dir / "workflow_execution.json").write_text(
            json.dumps({
                "status": "failed",
                "step_results": [
                    {"step_id": failed_node_id, "type": "skill_step", "status": "error"}
                ],
                "outputs": [],
            }),
            encoding="utf-8",
        )
        changed_after_first = await client.patch(
            f"/api/workbench/tasks/{task_id}",
            json={"tags": ["storage", "changed-after-first"]},
        )
        second = await client.post(
            f"/api/workbench/tasks/{task_id}/runs",
            json={"parent_task_run_id": first.json()["task_run_id"]},
        )
        artifact_profile_switch_retry = await client.post(
            f"/api/workbench/tasks/{task_id}/runs",
            json={
                "parent_task_run_id": first.json()["task_run_id"],
                "artifact_profile_id": other_artifact_profile["id"],
            },
        )
        listed_after_runs = await client.get(
            "/api/workbench/tasks",
            params={
                "q": "skill",
                "skill_id": version.skill_id,
                "workspace_id": "ws-1",
            },
        )
        detail = await client.get(f"/api/workbench/tasks/{task_id}")
        immutable = await client.patch(
            f"/api/workbench/tasks/{task_id}",
            json={"skill_content_digest": "sha256:" + "2" * 64},
        )
        event_store = WorkbenchTaskRunEventStore(data_dir / "workbench" / "task_runs")
        event_store.mark_status(second.json()["task_run_id"], "running")
        archive_blocked = await client.post(f"/api/workbench/tasks/{task_id}/archive")
        event_store.mark_status(second.json()["task_run_id"], "failed")
        archived = await client.post(f"/api/workbench/tasks/{task_id}/archive")

    assert empty_page.status_code == 200
    assert empty_page.json()["page_size"] == 25
    assert oversized_page.status_code == 422
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["skill_id"] == version.skill_id
    assert body["skill_version_id"] == version.version_id
    assert body["skill_content_digest"] == version.content_digest
    assert body["input_values"] == {}
    assert "workflow_id" not in body
    assert "workflow_version_id" not in body
    assert listed.status_code == 200
    assert [item["task_id"] for item in listed.json()["items"]] == [task_id]
    assert compiled.status_code == 200
    assert compiled.json()["skill_version"]["version_id"] == version.version_id
    assert compiled.json()["skill_plan"]["compiled_contract_version"] == 3
    assert compiled.json()["selected_deliveries"] == ["delivery.developer-test-code-explanation"]
    assert unknown_profile.status_code == 422
    assert "交付件档案不存在" in unknown_profile.json()["detail"]
    assert first.status_code == 201
    assert first.json()["attempt_number"] == 1
    assert execution.execution_status == "completed"
    assert second.status_code == 201
    assert second.json()["attempt_number"] == 2
    assert second.json()["parent_task_run_id"] == first.json()["task_run_id"]
    assert changed_after_first.status_code == 200
    assert artifact_profile_switch_retry.status_code == 422
    assert "沿用父运行的交付件档案" in artifact_profile_switch_retry.json()["detail"]
    assert listed_after_runs.status_code == 200
    assert [item["task_id"] for item in listed_after_runs.json()["items"]] == [task_id]
    assert listed_after_runs.json()["items"][0]["latest_run"]["attempt_number"] == 2
    assert detail.status_code == 200
    assert [run["attempt_number"] for run in detail.json()["runs"]] == [2, 1]
    assert immutable.status_code == 422
    assert archive_blocked.status_code == 409
    assert "取消" in archive_blocked.json()["detail"]
    assert archived.status_code == 200
    assert archived.json()["lifecycle_status"] == "archived"
    run_bundle = json.loads(
        (data_dir / "workbench" / "task_runs" / first.json()["task_run_id"] / "task_run.json").read_text(encoding="utf-8")
    )["task_bundle"]
    retried_bundle = json.loads(
        (data_dir / "workbench" / "task_runs" / second.json()["task_run_id"] / "task_run.json").read_text(encoding="utf-8")
    )["task_bundle"]
    retried_bundle_artifact = json.loads(
        (data_dir / "workbench" / "task_runs" / second.json()["task_run_id"] / "task_bundle.json").read_text(encoding="utf-8")
    )
    invocation_payload = json.loads(
        (data_dir / "workbench" / "task_runs" / first.json()["task_run_id"] / "skill_invocation.json").read_text(encoding="utf-8")
    )
    assert run_bundle["skill_version_id"] == version.version_id
    assert run_bundle["skill_content_digest"] == version.content_digest
    assert run_bundle["compiled_plan"]["compiled_contract_version"] == 3
    assert run_bundle["compiled_plan"]["skill_id"] == version.skill_id
    assert run_bundle["effective_compiled_definition"]["id"] == version.skill_id
    assert "workflow_version_id" not in run_bundle
    assert run_bundle["workflow_id"] == version.skill_id
    assert run_bundle["skill_invocation"] == invocation_payload
    assert run_bundle["skill_judge_required"] is True
    assert invocation_payload["schema_version"] == "skill-run-invocation-v1"
    assert invocation_payload["skill_version_id"] == version.version_id
    assert invocation_payload["skill_content_digest"] == version.content_digest
    assert invocation_payload["task_id"] == task_id
    assert invocation_payload["selected_delivery_ids"] == ["delivery.developer-test-code-explanation"]
    assert invocation_payload["judge"]["required"] is True
    compiled_plan_artifact = json.loads(
        (data_dir / "workbench" / "task_runs" / first.json()["task_run_id"] / "compiled_plan.json").read_text(encoding="utf-8")
    )
    assert compiled_plan_artifact["compiled_contract_version"] == 3
    assert run_bundle["artifact_profile"]["resolution_source"] == "run_selection"
    assert run_bundle["artifact_profile"]["profile_id"] == artifact_profile["id"]
    assert run_bundle["artifact_profile"]["artifacts"][0]["filename"] == "protocol-review.md"
    assert retried_bundle["skill_version_id"] == version.version_id
    assert retried_bundle["skill_content_digest"] == version.content_digest
    assert retried_bundle["skill_invocation"]["skill_version_id"] == version.version_id
    assert retried_bundle["artifact_profile"]["resolution_source"] == "parent_attempt"
    assert retried_bundle["artifact_profile"]["profile_id"] == artifact_profile["id"]
    assert retried_bundle["artifact_profile"]["profile_version"] == artifact_profile["version"]
    assert retried_bundle["effective_compiled_definition"]["id"] == version.skill_id
    assert retried_bundle["retry_source"] == {
        "task_run_id": first.json()["task_run_id"],
        "mode": "from_failed_node",
        "failed_node_ids": [failed_node_id],
    }
    assert retried_bundle["retry_seed_results"] == {}
    assert retried_bundle_artifact == retried_bundle


@pytest.mark.asyncio
async def test_task_api_rejects_migrated_workflow_binding_payload(tmp_path, monkeypatch):
    from app.api import workbench_v2_tasks
    from app.config import settings

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

    app = FastAPI()
    app.include_router(workbench_v2_tasks.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rejected = await client.post(
            "/api/workbench/tasks",
            json={
                "name": "Migrated workflow task",
                "workspace_id": "ws-legacy",
                "workflow_id": "source-review",
                "workflow_version_id": "wfv_legacy",
            },
        )

    assert rejected.status_code == 422
    detail = rejected.json()["detail"]
    assert any(item["loc"][-1] == "skill_version_id" for item in detail)
    assert any(item["loc"][-1] == "workflow_id" for item in detail)


@pytest.mark.asyncio
async def test_task_api_rejects_workflow_payload_and_lists_legacy_runs(tmp_path, monkeypatch):
    from app.api import workbench_v2_tasks
    from app.config import settings
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer
    from app.services.workflow_dsl import WorkflowStore

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
    app.include_router(workbench_v2_tasks.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rejected = await client.post(
            "/api/workbench/tasks",
            json={
                "name": "Draft task",
                "workspace_id": "ws-1",
                "workflow_id": "draft-flow",
                "workflow_version_id": "wfv_draft",
            },
        )
        history = await client.get("/api/workbench/tasks/history/runs")

    assert rejected.status_code == 422
    assert any(item["loc"][-1] == "skill_version_id" for item in rejected.json()["detail"])
    assert history.status_code == 200
    assert [item["task_run_id"] for item in history.json()["items"]] == [legacy.task_run_id]
    assert history.json()["items"][0]["legacy"] is True


@pytest.mark.asyncio
async def test_new_builtin_task_rejects_workflow_version_payload(tmp_path, monkeypatch):
    from app.api import workbench_v2_tasks
    from app.config import settings

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

    app = FastAPI()
    app.include_router(workbench_v2_tasks.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rejected = await client.post(
            "/api/workbench/tasks",
            json={
                "name": "New built-in task",
                "workspace_id": "ws-1",
                "workflow_id": "module_analysis",
                "workflow_version_id": "wfv_old",
            },
        )

    assert rejected.status_code == 422
    assert any(item["loc"][-1] == "skill_version_id" for item in rejected.json()["detail"])


@pytest.mark.asyncio
async def test_archived_custom_workflow_payload_is_not_a_task_binding(tmp_path, monkeypatch):
    from app.api import workbench_v2_tasks
    from app.config import settings

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

    app = FastAPI()
    app.include_router(workbench_v2_tasks.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rejected = await client.post(
            "/api/workbench/tasks",
            json={
                "name": "New archived custom task",
                "workspace_id": "ws-1",
                "workflow_id": "archived-custom",
                "workflow_version_id": "wfv_archived",
            },
        )

    assert rejected.status_code == 422
    assert any(item["loc"][-1] == "skill_version_id" for item in rejected.json()["detail"])
