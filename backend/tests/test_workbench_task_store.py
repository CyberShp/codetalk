import json
import sqlite3

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _workflow() -> dict:
    return {
        "id": "source-review",
        "name": "Source review",
        "version": 1,
        "inputs": [{"id": "target", "type": "text", "required": True}],
        "steps": [{"id": "scope", "type": "local_scope_discover"}],
        "outputs": [{"id": "report", "type": "markdown", "from": "scope"}],
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
    assert loaded.quality_status == "not_evaluated"
    assert loaded.delivery_status == "pending"

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
    published = version_store.publish_version(
        draft.version_id,
        authoring_graph=draft.authoring_graph,
        compiled_definition=_workflow(),
        compiled_plan={
            "plan_version": 1,
            "workflow_version_id": draft.version_id,
            "topological_order": ["scope"],
            "nodes": [],
            "max_parallelism": 1,
            "stop_on_error": True,
        },
        validation={"valid": True, "errors": [], "warnings": []},
    )

    app = FastAPI()
    app.include_router(agent_workbench.router)
    app.include_router(workbench_v2_tasks.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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
                "tags": ["storage"],
            },
        )
        assert created.status_code == 201
        task_id = created.json()["task_id"]

        first = await client.post(f"/api/workbench/tasks/{task_id}/runs", json={})
        second = await client.post(
            f"/api/workbench/tasks/{task_id}/runs",
            json={"parent_task_run_id": first.json()["task_run_id"]},
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
    assert first.json()["attempt_number"] == 1
    assert second.status_code == 201
    assert second.json()["attempt_number"] == 2
    assert second.json()["parent_task_run_id"] == first.json()["task_run_id"]
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
