import sqlite3

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _legacy_definition() -> dict:
    return {
        "id": "legacy_module",
        "name": "Legacy module analysis",
        "version": 7,
        "inputs": [{"id": "target", "type": "free_text"}],
        "steps": [{"id": "analyze", "type": "agent_task", "goal": "analyze"}],
        "outputs": [{"id": "report", "type": "markdown", "from": "analyze"}],
    }


def _graph(label: str = "Analyze") -> dict:
    return {
        "schema_version": 2,
        "workflow_id": "new_flow",
        "name": "New flow",
        "description": "",
        "nodes": [
            {"id": "agent", "kind": "agent", "label": label, "position": {"x": 1, "y": 2}, "config": {}},
        ],
        "edges": [],
        "settings": {"stop_on_error": True, "max_parallelism": 1},
    }


def test_workflow_version_migration_is_idempotent_and_preserves_legacy_table(tmp_path):
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workflow_version_store import WorkflowVersionStore

    db_path = tmp_path / "workflows.db"
    WorkflowStore(db_path).save_workflow(_legacy_definition())
    store = WorkflowVersionStore(db_path)

    first = store.initialize_and_migrate()
    second = store.initialize_and_migrate()

    assert first["migrated_workflows"] == 1
    assert second["migrated_workflows"] == 0
    header = store.get_workflow("legacy_module")
    version = store.get_version(header.published_version_id)
    assert version.state == "published"
    assert version.version_number == 1
    assert version.compiled_definition["version"] == 7
    assert version.authoring_graph["read_only"] is True
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT count(*) FROM workflow_definitions").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM workflow_versions").fetchone()[0] == 1
        assert db.execute(
            "SELECT version FROM workbench_schema_meta WHERE component = 'workflow_versions'"
        ).fetchone()[0] == 1


def test_workflow_draft_publish_and_immutable_version_lifecycle(tmp_path):
    from app.services.workflow_version_store import (
        PublishedWorkflowVersionError,
        WorkflowDraftExistsError,
        WorkflowVersionStore,
    )

    store = WorkflowVersionStore(tmp_path / "workflows.db")
    store.initialize_and_migrate()
    header, draft = store.create_workflow(
        workflow_id="new_flow",
        name="New flow",
        description="Analyze code",
        authoring_graph=_graph(),
    )
    assert header.current_draft_version_id == draft.version_id
    assert draft.version_number == 1
    assert draft.state == "draft"

    updated = store.update_draft(draft.version_id, authoring_graph=_graph("Updated"))
    assert updated.authoring_graph["nodes"][0]["label"] == "Updated"
    published = store.publish_version(
        draft.version_id,
        authoring_graph=updated.authoring_graph,
        compiled_definition={"id": "new_flow", "name": "New flow", "version": 1, "inputs": [], "steps": [], "outputs": []},
        compiled_plan={"schema_version": 1, "nodes": []},
        validation={"valid": True, "errors": [], "warnings": []},
    )
    assert published.state == "published"
    assert store.get_workflow("new_flow").published_version_id == published.version_id
    assert store.get_workflow("new_flow").current_draft_version_id is None

    with pytest.raises(PublishedWorkflowVersionError):
        store.update_draft(published.version_id, authoring_graph=_graph("Illegal"))

    next_draft = store.create_draft("new_flow")
    assert next_draft.version_number == 2
    assert next_draft.based_on_version_id == published.version_id
    with pytest.raises(WorkflowDraftExistsError):
        store.create_draft("new_flow")

    republished = store.publish_version(
        next_draft.version_id,
        authoring_graph=next_draft.authoring_graph,
        compiled_definition={"id": "new_flow", "name": "New flow", "version": 2, "inputs": [], "steps": [], "outputs": []},
        compiled_plan={"schema_version": 1, "nodes": []},
        validation={"valid": True, "errors": []},
    )
    assert republished.version_number == 2
    assert [item.version_number for item in store.list_versions("new_flow")] == [2, 1]


def test_workflow_header_update_archive_and_compatibility_definition(tmp_path):
    from app.services.workflow_version_store import WorkflowVersionStore

    store = WorkflowVersionStore(tmp_path / "workflows.db")
    store.initialize_and_migrate()
    store.create_workflow(
        workflow_id="new_flow",
        name="New flow",
        description="Analyze code",
        authoring_graph=_graph(),
    )
    updated = store.update_workflow("new_flow", name="Renamed", description="Changed")
    assert updated.name == "Renamed"
    assert updated.description == "Changed"
    archived = store.archive_workflow("new_flow")
    assert archived.status == "archived"
    assert archived.archived_at


def test_workflow_version_rejects_invalid_identifiers_and_cross_workflow_version(tmp_path):
    from app.services.workflow_version_store import WorkflowVersionStore

    store = WorkflowVersionStore(tmp_path / "workflows.db")
    store.initialize_and_migrate()
    with pytest.raises(ValueError, match="workflow_id"):
        store.create_workflow(
            workflow_id="../escape",
            name="Bad",
            description="",
            authoring_graph=_graph(),
        )


@pytest.mark.asyncio
async def test_workflow_version_api_creates_updates_publishes_and_rejects_mutation(
    tmp_path, monkeypatch
):
    from app.api import agent_workbench, workbench_v2_workflows
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "workbench_v2_enabled", True)

    app = FastAPI()
    app.include_router(agent_workbench.router)
    app.include_router(workbench_v2_workflows.router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows",
            json={
                "id": "new_flow",
                "name": "New flow",
                "description": "Analyze code",
                "authoring_graph": _graph(),
            },
        )
        assert created.status_code == 201
        draft_id = created.json()["current_draft_version_id"]

        listed_headers = await client.get("/api/workbench/workflows")
        assert listed_headers.status_code == 200
        listed_new = next(
            item for item in listed_headers.json() if item.get("id") == "new_flow"
        )
        assert listed_new["v2"]["current_draft_version_id"] == draft_id

        loaded_header = await client.get("/api/workbench/workflows/new_flow")
        assert loaded_header.status_code == 200
        assert loaded_header.json()["authoring_graph"]["schema_version"] == 2

        versions = await client.get("/api/workbench/workflows/new_flow/versions")
        assert versions.status_code == 200
        assert versions.json()["items"][0]["version_id"] == draft_id

        updated = await client.put(
            f"/api/workbench/workflows/new_flow/versions/{draft_id}",
            json={"authoring_graph": _graph("Updated")},
        )
        assert updated.status_code == 200
        assert updated.json()["authoring_graph"]["nodes"][0]["label"] == "Updated"

        published = await client.post(
            f"/api/workbench/workflows/new_flow/versions/{draft_id}/publish",
            json={
                "authoring_graph": _graph("Updated"),
                "compiled_definition": {
                    "id": "new_flow",
                    "name": "New flow",
                    "version": 1,
                    "inputs": [],
                    "steps": [],
                    "outputs": [],
                },
                "compiled_plan": {"schema_version": 1, "nodes": []},
            },
        )
        assert published.status_code == 200
        assert published.json()["state"] == "published"

        immutable = await client.put(
            f"/api/workbench/workflows/new_flow/versions/{draft_id}",
            json={"authoring_graph": _graph("Illegal")},
        )
        assert immutable.status_code == 409

        archived = await client.post("/api/workbench/workflows/new_flow/archive")
        assert archived.status_code == 200
        assert archived.json()["status"] == "archived"
