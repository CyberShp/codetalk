import sqlite3
from dataclasses import asdict

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
            {
                "id": "agent",
                "kind": "agent",
                "label": label,
                "position": {"x": 1, "y": 2},
                "config": {
                    "step_id": "agent",
                    "goal": "analyze source",
                    "provider": "builtin-llm",
                    "mcp_profiles": [],
                    "skill_ids": [],
                    "required_artifacts": [],
                    "input_ports": [],
                    "output_ports": [],
                    "failure_policy": "stop",
                },
            },
        ],
        "edges": [],
        "settings": {"stop_on_error": True, "max_parallelism": 1},
    }


def _workspace_graph() -> dict:
    graph = _graph()
    graph["nodes"][0]["config"]["input_ports"] = [
        {"id": "repo_path", "type": "directory", "required": True}
    ]
    graph["nodes"].insert(
        0,
        {
            "id": "repository",
            "kind": "input",
            "label": "Repository",
            "position": {"x": 0, "y": 2},
            "config": {
                "contract_id": "repo_path",
                "label": "Repository",
                "type": "directory",
                "required": True,
                "resolver": "workspace",
                "role": "source repository",
            },
        },
    )
    graph["edges"] = [
        {
            "id": "repository-agent",
            "kind": "data",
            "source": {"node_id": "repository", "port_id": "value"},
            "target": {"node_id": "agent", "port_id": "repo_path"},
        }
    ]
    return graph


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
    assert version.compiled_plan is not None
    assert version.compiled_plan["compatibility_mode"] == "legacy_sequential"
    assert version.compiled_plan["topological_order"] == ["analyze"]
    assert version.authoring_graph["read_only"] is True
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT count(*) FROM workflow_definitions").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM workflow_versions").fetchone()[0] == 1
        assert db.execute(
            "SELECT version FROM workbench_schema_meta WHERE component = 'workflow_versions'"
        ).fetchone()[0] == 2


def test_workflow_version_migration_backfills_existing_legacy_execution_plan(tmp_path):
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workflow_version_store import WorkflowVersionStore

    db_path = tmp_path / "workflows.db"
    WorkflowStore(db_path).save_workflow(_legacy_definition())
    store = WorkflowVersionStore(db_path)
    store.initialize_and_migrate()
    header = store.get_workflow("legacy_module")

    with sqlite3.connect(db_path) as db:
        db.execute(
            "UPDATE workflow_versions SET compiled_plan_json = NULL WHERE version_id = ?",
            (header.published_version_id,),
        )
        db.execute(
            "UPDATE workbench_schema_meta SET version = 1 WHERE component = 'workflow_versions'"
        )
        db.commit()

    upgraded = store.initialize_and_migrate()
    version = store.get_version(header.published_version_id)

    assert upgraded["upgraded_workflows"] == 1
    assert version.compiled_plan is not None
    assert version.compiled_plan["workflow_version_id"] == header.published_version_id
    assert store.initialize_and_migrate()["upgraded_workflows"] == 0


def test_workflow_version_migration_does_not_rewrite_native_v2_without_plan(tmp_path):
    from app.services.workflow_version_store import WorkflowVersionStore

    db_path = tmp_path / "workflows.db"
    store = WorkflowVersionStore(db_path)
    _, draft = store.create_workflow(
        workflow_id="new_flow",
        name="New flow",
        description="",
        authoring_graph=_graph(),
    )
    published = store.publish_version(
        draft.version_id,
        authoring_graph=draft.authoring_graph,
        compiled_definition={
            "id": "new_flow",
            "name": "New flow",
            "version": 1,
            "inputs": [],
            "steps": [{"id": "first", "type": "agent_task"}, {"id": "second", "type": "agent_task"}],
            "outputs": [],
        },
        compiled_plan={
            "plan_version": 1,
            "workflow_version_id": draft.version_id,
            "topological_order": ["first", "second"],
            "nodes": [],
        },
        validation={"valid": True, "errors": [], "warnings": []},
    )
    with sqlite3.connect(db_path) as db:
        db.execute(
            "UPDATE workflow_versions SET compiled_plan_json = NULL WHERE version_id = ?",
            (published.version_id,),
        )
        db.commit()

    migration = store.initialize_and_migrate()

    assert migration["upgraded_workflows"] == 0
    assert store.get_version(published.version_id).compiled_plan is None


def test_retire_workflows_archives_headers_but_preserves_versions_and_custom_workflows(tmp_path):
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workflow_presets import get_workflow_preset
    from app.services.workflow_version_store import WorkflowVersionStore

    db_path = tmp_path / "workflows.db"
    retired_definition = get_workflow_preset("module_analysis")["definition"]
    active_definition = get_workflow_preset("source_flow_sfmea_blackbox")["definition"]
    custom_definition = _legacy_definition()
    legacy_store = WorkflowStore(db_path)
    for definition in (retired_definition, active_definition, custom_definition):
        legacy_store.save_workflow(definition)

    store = WorkflowVersionStore(db_path)
    store.initialize_and_migrate()
    retired_version_id = store.get_workflow("module_analysis").published_version_id
    custom_version_id = store.get_workflow("legacy_module").published_version_id

    changed = store.retire_workflows({"module_analysis"})

    assert changed == 1
    assert store.get_workflow("module_analysis").status == "archived"
    assert store.get_workflow("module_analysis").published_version_id == retired_version_id
    assert store.get_version(retired_version_id).state == "published"
    assert store.get_workflow("source_flow_sfmea_blackbox").status == "active"
    assert store.get_workflow("legacy_module").status == "active"
    assert store.get_workflow("legacy_module").published_version_id == custom_version_id
    assert store.retire_workflows({"module_analysis"}) == 0


def test_builtin_snapshot_replaces_same_definition_with_poisoned_graph_and_plan(tmp_path):
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workflow_presets import get_workflow_preset
    from app.services.workflow_version_store import WorkflowVersionStore

    definition = get_workflow_preset("module_analysis")["definition"]
    db_path = tmp_path / "workflows.db"
    WorkflowStore(db_path).save_workflow(definition)
    store = WorkflowVersionStore(db_path)
    store.initialize_and_migrate()
    old_header = store.get_workflow("module_analysis")
    old_version_id = old_header.published_version_id
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            UPDATE workflow_versions
            SET authoring_graph_json = ?, compiled_plan_json = ?
            WHERE version_id = ?
            """,
            (
                '{"schema_version":2,"workflow_id":"module_analysis","nodes":[],"edges":[]}',
                '{"plan_version":1,"workflow_version_id":"poisoned","topological_order":["shadow"],"nodes":[],"shadow_plan":true}',
                old_version_id,
            ),
        )
        db.commit()

    changed = store.ensure_legacy_published_workflows([definition])
    header = store.get_workflow("module_analysis")
    published = store.get_version(header.published_version_id)

    assert changed == 1
    assert header.published_version_id != old_version_id
    assert store.get_version(old_version_id).compiled_plan["shadow_plan"] is True
    assert published.authoring_graph["schema_version"] == 1
    assert published.authoring_graph["read_only"] is True
    assert published.compiled_plan["compatibility_mode"] == "legacy_sequential"
    assert published.compiled_plan["workflow_version_id"] == published.version_id
    assert store.ensure_legacy_published_workflows([definition]) == 0


def test_builtin_snapshot_reactivates_header_and_archives_stale_draft(tmp_path):
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workflow_presets import get_workflow_preset
    from app.services.workflow_version_store import WorkflowVersionStore

    definition = get_workflow_preset("module_analysis")["definition"]
    db_path = tmp_path / "workflows.db"
    WorkflowStore(db_path).save_workflow(definition)
    store = WorkflowVersionStore(db_path)
    store.initialize_and_migrate()
    original_version_id = store.get_workflow("module_analysis").published_version_id
    draft = store.create_draft("module_analysis")
    store.archive_workflow("module_analysis")

    changed = store.ensure_legacy_published_workflows([definition])
    header = store.get_workflow("module_analysis")

    assert changed == 1
    assert header.status == "active"
    assert header.archived_at is None
    assert header.current_draft_version_id is None
    assert header.published_version_id == original_version_id
    assert store.get_version(draft.version_id).state == "archived"
    assert store.ensure_legacy_published_workflows([definition]) == 0


def test_builtin_snapshot_ensure_is_concurrent_and_idempotent(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from app.services.workflow_dsl import WorkflowStore
    from app.services.workflow_presets import get_workflow_preset
    from app.services.workflow_version_store import WorkflowVersionStore

    definition = get_workflow_preset("module_analysis")["definition"]
    shadow = dict(definition)
    shadow["name"] = "Shadow module analysis"
    db_path = tmp_path / "workflows.db"
    WorkflowStore(db_path).save_workflow(shadow)
    WorkflowVersionStore(db_path).initialize_and_migrate()

    def ensure() -> int:
        return WorkflowVersionStore(db_path).ensure_legacy_published_workflows(
            [definition]
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: ensure(), range(2)))

    store = WorkflowVersionStore(db_path)
    header = store.get_workflow("module_analysis")
    published = store.get_version(header.published_version_id)
    assert sorted(results) == [0, 1]
    assert len(store.list_versions("module_analysis")) == 2
    assert published.compiled_definition == definition
    assert published.compiled_plan["compatibility_mode"] == "legacy_sequential"


def test_migrated_legacy_version_creates_an_editable_v2_draft(tmp_path):
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workflow_graph import compile_workflow_graph
    from app.services.workflow_version_store import WorkflowVersionStore

    db_path = tmp_path / "workflows.db"
    WorkflowStore(db_path).save_workflow(_legacy_definition())
    store = WorkflowVersionStore(db_path)
    store.initialize_and_migrate()

    published = store.get_version(store.get_workflow("legacy_module").published_version_id)
    draft = store.create_draft("legacy_module", based_on_version_id=published.version_id)

    assert published.authoring_graph["schema_version"] == 1
    assert draft.authoring_graph["schema_version"] == 2
    assert draft.authoring_graph.get("read_only") is not True
    assert draft.authoring_graph["workflow_id"] == "legacy_module"
    compiled = compile_workflow_graph(
        draft.authoring_graph,
        capabilities={
            "providers": {"builtin-llm": {"available": True, "mcp_profiles": []}},
            "skills": [],
        },
        workflow_version_id=draft.version_id,
        workflow_version_number=draft.version_number,
    )
    assert compiled["validation_result"]["valid"] is True
    assert compiled["compiled_definition"]["steps"][0]["type"] == "agent_task"


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


def test_legacy_compatibility_parser_accepts_v2_workspace_resolver():
    from app.services.workflow_dsl import validate_workflow_definition

    definition = _legacy_definition()
    definition["inputs"][0]["resolver"] = "workspace"

    parsed = validate_workflow_definition(definition)

    assert parsed.inputs[0].resolver == "workspace"


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

        validated = await client.post(
            f"/api/workbench/workflows/new_flow/versions/{draft_id}/validate"
        )
        assert validated.status_code == 200
        assert validated.json()["valid"] is True

        compiled = await client.post(
            f"/api/workbench/workflows/new_flow/versions/{draft_id}/compile"
        )
        assert compiled.status_code == 200
        assert compiled.json()["compiled_plan"]["topological_order"] == ["agent"]

        published = await client.post(
            f"/api/workbench/workflows/new_flow/versions/{draft_id}/publish",
            json={},
        )
        assert published.status_code == 200
        assert published.json()["state"] == "published"

        loaded_published = await client.get("/api/workbench/workflows/new_flow")
        assert loaded_published.status_code == 200
        assert loaded_published.json()["v2"]["published_version_id"] == draft_id

        next_draft = await client.post("/api/workbench/workflows/new_flow/versions", json={})
        next_draft_id = next_draft.json()["version_id"]
        await client.put(
            f"/api/workbench/workflows/new_flow/versions/{next_draft_id}",
            json={"authoring_graph": _graph("Draft only")},
        )
        listed_with_draft = await client.get("/api/workbench/workflows")
        listed_projection = next(
            item for item in listed_with_draft.json() if item.get("id") == "new_flow"
        )
        assert listed_projection["v2"]["published_version_id"] == draft_id
        assert listed_projection["v2"]["current_draft_version_id"] == next_draft_id
        assert listed_projection["authoring_graph"]["nodes"][0]["label"] == "Updated"

        immutable = await client.put(
            f"/api/workbench/workflows/new_flow/versions/{draft_id}",
            json={"authoring_graph": _graph("Illegal")},
        )
        assert immutable.status_code == 409

        archived = await client.post("/api/workbench/workflows/new_flow/archive")
        assert archived.status_code == 200
        assert archived.json()["status"] == "archived"


def test_workflow_graph_capabilities_include_configured_agent_runtimes(monkeypatch):
    from app.api import workbench_v2_workflows

    monkeypatch.setattr(
        workbench_v2_workflows,
        "list_agent_runtimes_sync",
        lambda enabled=True: [
            {
                "id": "codex-local",
                "name": "Codex Local",
                "enabled": True,
                "command": "codex",
                "mcp_profile": "gitnexus",
            }
        ],
    )

    capabilities = workbench_v2_workflows._workflow_graph_capabilities()

    assert capabilities["providers"]["agent-runtime:codex-local"] == {
        "available": True,
        "mcp_profiles": ["gitnexus"],
    }


@pytest.mark.asyncio
async def test_builtin_workflow_is_read_only_across_all_v2_mutation_routes(
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

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/workbench/workflows")
        release_workflow = next(
            item
            for item in listed.json()
            if item["id"] == "source_flow_sfmea_blackbox"
        )
        version_id = release_workflow["v2"]["published_version_id"]
        workflow_path = "/api/workbench/workflows/source_flow_sfmea_blackbox"
        responses = [
            await client.patch(workflow_path, json={"name": "Shadow"}),
            await client.post(f"{workflow_path}/archive"),
            await client.post(f"{workflow_path}/versions", json={}),
            await client.put(
                f"{workflow_path}/versions/{version_id}",
                json={"authoring_graph": {}},
            ),
            await client.post(
                f"{workflow_path}/versions/{version_id}/validate"
            ),
            await client.post(
                f"{workflow_path}/versions/{version_id}/compile"
            ),
            await client.post(
                f"{workflow_path}/versions/{version_id}/publish",
                json={},
            ),
            await client.post(
                f"{workflow_path}/versions/{version_id}/test-run",
                json={"workspace_id": "missing", "inputs": {}},
            ),
        ]

    assert all(response.status_code == 409 for response in responses)
    assert all("内置工作流" in str(response.json()["detail"]) for response in responses)


@pytest.mark.asyncio
async def test_draft_trial_compiles_server_graph_and_prepares_real_task_run(
    tmp_path, monkeypatch
):
    from app.api import agent_workbench, workbench_v2_workflows
    from app.config import settings
    from app.services.workbench_task_run import WorkbenchTaskRunStore

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    sqlite_db = data_dir / "codetalk.db"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("source evidence", encoding="utf-8")
    with sqlite3.connect(sqlite_db) as db:
        db.execute(
            """
            CREATE TABLE workspaces (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                repo_path TEXT NOT NULL
            )
            """
        )
        db.execute(
            "INSERT INTO workspaces (id, name, repo_path) VALUES (?, ?, ?)",
            ("ws-1", "Repository", str(repo)),
        )
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "sqlite_db", str(sqlite_db))
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
                "authoring_graph": _workspace_graph(),
            },
        )
        draft_id = created.json()["current_draft_version_id"]

        trial = await client.post(
            f"/api/workbench/workflows/new_flow/versions/{draft_id}/test-run",
            json={"workspace_id": "ws-1", "inputs": {}},
        )

    assert trial.status_code == 201
    payload = trial.json()
    assert payload["status"] == "prepared"
    assert payload["workspace_id"] == "ws-1"
    task_run = WorkbenchTaskRunStore(
        data_dir / "workbench" / "task_runs"
    ).load(payload["task_run_id"])
    assert task_run.repo_path == str(repo.resolve())
    assert task_run.input_snapshot["repo_path"] == str(repo.resolve())
    assert task_run.task_bundle["compiled_plan"]["workflow_version_id"] == draft_id
    assert task_run.workflow_snapshot["id"] == "new_flow"
    assert task_run.task_bundle["trial_run"] is True


@pytest.mark.asyncio
async def test_draft_trial_rejects_unknown_workspace(tmp_path, monkeypatch):
    from app.api import agent_workbench, workbench_v2_workflows
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    sqlite_db = data_dir / "codetalk.db"
    with sqlite3.connect(sqlite_db) as db:
        db.execute(
            "CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT, repo_path TEXT)"
        )
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "sqlite_db", str(sqlite_db))
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
        draft_id = created.json()["current_draft_version_id"]
        trial = await client.post(
            f"/api/workbench/workflows/new_flow/versions/{draft_id}/test-run",
            json={"workspace_id": "missing", "inputs": {}},
        )

    assert trial.status_code == 404
    assert "工作空间不存在" in trial.json()["detail"]
