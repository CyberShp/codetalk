import json
import sqlite3
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

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


_PHASE0_FIXTURE_DIR = Path(__file__).with_name("fixtures") / "harness_workflow_refactor"


def _phase0_fixture(name: str) -> dict:
    return json.loads((_PHASE0_FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _insert_phase0_published_version(db_path: Path, fixture: dict) -> tuple[str, ...]:
    """Insert frozen rows without invoking today's compiler or publisher."""
    header = fixture["workflow_header"]
    version = fixture["workflow_version"]
    serialized = tuple(
        json.dumps(version[field], ensure_ascii=False, sort_keys=True)
        for field in ("authoring_graph", "compiled_definition", "compiled_plan", "validation")
    )
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            INSERT INTO workflow_headers(
                workflow_id, name, description, status, published_version_id,
                current_draft_version_id, created_at, updated_at, archived_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(header[field] for field in (
                "workflow_id", "name", "description", "status", "published_version_id",
                "current_draft_version_id", "created_at", "updated_at", "archived_at",
            )),
        )
        db.execute(
            """
            INSERT INTO workflow_versions(
                version_id, workflow_id, version_number, state, authoring_graph_json,
                compiled_definition_json, compiled_plan_json, validation_json,
                based_on_version_id, created_at, updated_at, published_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version["version_id"], version["workflow_id"], version["version_number"],
                version["state"], *serialized, version["based_on_version_id"],
                version["created_at"], version["updated_at"], version["published_at"],
            ),
        )
    return serialized


def test_phase0_published_v1_and_v2_workflow_fixtures_remain_loadable(tmp_path):
    """Load frozen published rows without regenerating either historical version."""
    from app.services.workflow_version_store import WorkflowVersionStore

    db_path = tmp_path / "workflows.db"
    store = WorkflowVersionStore(db_path)
    store.initialize_and_migrate()

    fixtures = [
        _phase0_fixture("v1-published-workflow.json"),
        _phase0_fixture("v2-published-workflow.json"),
    ]
    frozen_json_by_version = {
        fixture["workflow_version"]["version_id"]: _insert_phase0_published_version(
            db_path, fixture
        )
        for fixture in fixtures
    }

    for fixture in fixtures:
        expected_header = fixture["workflow_header"]
        expected_version = fixture["workflow_version"]
        header = store.get_workflow(expected_header["workflow_id"])
        version = store.get_version(expected_version["version_id"])

        assert asdict(header) == expected_header
        assert asdict(version) == expected_version

        with sqlite3.connect(db_path) as db:
            raw = db.execute(
                """
                SELECT authoring_graph_json, compiled_definition_json,
                       compiled_plan_json, validation_json
                FROM workflow_versions WHERE version_id = ?
                """,
                (expected_version["version_id"],),
            ).fetchone()
        assert raw == frozen_json_by_version[expected_version["version_id"]]


@pytest.mark.asyncio
async def test_phase0_history_lists_frozen_legacy_task_attempt_without_active_task_binding(
    tmp_path, monkeypatch
):
    """Read a frozen historical attempt through the public legacy history route.

    This deliberately inserts the captured rows and attempt payload verbatim.
    It must not recreate legacy Workflow authority in active workbench_tasks.
    """
    from app.api import workbench_v2_tasks
    from app.services.workbench_task_run import WorkbenchTaskRunStore
    from app.services.workflow_version_store import WorkflowVersionStore

    workflow_db = tmp_path / "workflows.db"
    version_store = WorkflowVersionStore(workflow_db)
    version_store.initialize_and_migrate()
    fixtures = [
        _phase0_fixture("v1-published-workflow.json"),
        _phase0_fixture("v2-published-workflow.json"),
    ]
    for fixture in fixtures:
        _insert_phase0_published_version(workflow_db, fixture)

    task_attempt = _phase0_fixture("historical-task-attempt.json")
    task = task_attempt["task"]
    attempt = {**task_attempt["task_run"], "task_id": ""}

    attempt_root = tmp_path / "task_runs"
    attempt_dir = attempt_root / attempt["task_run_id"]
    attempt_dir.mkdir(parents=True)
    (attempt_dir / "task_run.json").write_text(
        json.dumps(attempt, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    run_store = WorkbenchTaskRunStore(attempt_root)

    runtime_db = tmp_path / "runtime.db"
    with sqlite3.connect(runtime_db) as db:
        db.execute(
            "CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT NOT NULL, repo_path TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO workspaces(id, name, repo_path) VALUES (?, ?, ?)",
            (task["workspace_id"], "Historical workspace", "/historical/repositories/example"),
        )
    from app.config import settings

    monkeypatch.setattr(settings, "sqlite_db", str(runtime_db))
    monkeypatch.setattr(workbench_v2_tasks, "task_run_store", lambda: run_store)

    app = FastAPI()
    app.include_router(workbench_v2_tasks.router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/workbench/tasks/history/runs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == [
        {
            "task_run_id": attempt["task_run_id"],
            "task_id": "",
            "attempt_number": attempt["attempt_number"],
            "parent_task_run_id": attempt["parent_task_run_id"],
            "workflow_id": task["workflow_id"],
            "workspace_id": task["workspace_id"],
            "execution_status": attempt["execution_status"],
            "quality_status": attempt["quality_status"],
            "delivery_status": attempt["delivery_status"],
            "started_at": attempt["started_at"],
            "completed_at": attempt["completed_at"],
            "created_at": attempt["created_at"],
            "waiting_reason": "",
            "recovery_actions": [],
            "legacy": True,
        }
    ]
    assert version_store.get_version(fixtures[1]["workflow_version"]["version_id"]).state == "published"


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


def test_workflow_version_migration_reads_missing_legacy_plan_without_rewriting_history(
    tmp_path,
):
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
        frozen_updated_at = db.execute(
            "SELECT updated_at FROM workflow_versions WHERE version_id = ?",
            (header.published_version_id,),
        ).fetchone()[0]

    upgraded = store.initialize_and_migrate()
    version = store.get_version(header.published_version_id)

    assert upgraded["upgraded_workflows"] == 0
    assert version.compiled_plan is not None
    assert version.compiled_plan["workflow_version_id"] == header.published_version_id
    with sqlite3.connect(db_path) as db:
        frozen = db.execute(
            "SELECT compiled_plan_json, updated_at FROM workflow_versions WHERE version_id = ?",
            (header.published_version_id,),
        ).fetchone()
    assert frozen == (None, frozen_updated_at)
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
    stale_version_id = "historical_v2_stale_draft"
    stale_graph = {**_graph(), "workflow_id": "module_analysis"}
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            INSERT INTO workflow_versions(
                version_id, workflow_id, version_number, state,
                authoring_graph_json, compiled_definition_json,
                compiled_plan_json, validation_json, based_on_version_id,
                created_at, updated_at, published_at
            ) VALUES (?, 'module_analysis', 2, 'draft', ?, NULL, NULL, NULL, ?, ?, ?, NULL)
            """,
            (
                stale_version_id,
                json.dumps(stale_graph),
                original_version_id,
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        db.execute(
            "UPDATE workflow_headers SET current_draft_version_id = ? WHERE workflow_id = 'module_analysis'",
            (stale_version_id,),
        )
        db.commit()
    store.archive_workflow("module_analysis")

    changed = store.ensure_legacy_published_workflows([definition])
    header = store.get_workflow("module_analysis")

    assert changed == 1
    assert header.status == "active"
    assert header.archived_at is None
    assert header.current_draft_version_id is None
    assert header.published_version_id == original_version_id
    assert store.get_version(stale_version_id).state == "archived"
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


def test_migrated_legacy_v1_version_requires_explicit_copy_to_v3(tmp_path):
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workflow_version_store import (
        WorkflowVersionError,
        WorkflowVersionStore,
    )

    db_path = tmp_path / "workflows.db"
    WorkflowStore(db_path).save_workflow(_legacy_definition())
    store = WorkflowVersionStore(db_path)
    store.initialize_and_migrate()

    published = store.get_version(store.get_workflow("legacy_module").published_version_id)

    assert published.authoring_graph["schema_version"] == 1
    with pytest.raises(WorkflowVersionError, match="explicit copy-to-v3"):
        store.create_draft("legacy_module", based_on_version_id=published.version_id)

    assert store.get_workflow("legacy_module").current_draft_version_id is None


def test_copying_a_read_only_v1_workflow_requires_explicit_v3_migration(tmp_path):
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workflow_version_store import WorkflowVersionError, WorkflowVersionStore

    db_path = tmp_path / "workflows.db"
    source = _legacy_definition()
    source["id"] = "builtin_source_flow"
    source["name"] = "Built-in source flow"
    WorkflowStore(db_path).save_workflow(source)
    store = WorkflowVersionStore(db_path)
    store.initialize_and_migrate()

    with pytest.raises(WorkflowVersionError, match="explicit copy-to-v3"):
        store.copy_workflow_as_custom_draft(
            "builtin_source_flow",
            workflow_id="builtin_source_flow_custom",
            name="Built-in source flow copy",
        )

    assert store.get_workflow("builtin_source_flow").current_draft_version_id is None
    with pytest.raises(KeyError):
        store.get_workflow("builtin_source_flow_custom")


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


def test_v3_publish_rejects_stale_snapshot_without_overwriting_newer_graph(tmp_path):
    from app.services.workflow_authoring_factory import build_canvas_graph
    from app.services.workflow_version_store import (
        StaleWorkflowDraftError,
        WorkflowVersionStore,
    )

    store = WorkflowVersionStore(tmp_path / "workflows.db")
    graph = build_canvas_graph(
        workflow_id="v3_publish_race",
        name="Initial snapshot",
        description="",
        template="free_source_analysis",
    )
    _, draft = store.create_canvas_workflow(
        workflow_id="v3_publish_race",
        name="Initial snapshot",
        description="",
        authoring_graph=graph,
    )
    stale_graph = deepcopy(draft.authoring_graph)
    newer_graph = deepcopy(draft.authoring_graph)
    newer_graph["name"] = "Newer draft wins"
    updated = store.update_draft(
        draft.version_id,
        authoring_graph=newer_graph,
        expected_revision=draft.draft_revision,
    )

    with pytest.raises(StaleWorkflowDraftError):
        store.publish_version(
            draft.version_id,
            expected_revision=draft.draft_revision,
            authoring_graph=stale_graph,
            compiled_definition={
                "id": "v3_publish_race",
                "name": "Initial snapshot",
                "version": 1,
                "inputs": [],
                "steps": [],
                "outputs": [],
            },
            compiled_plan={"schema_version": 1, "nodes": []},
            validation={"valid": True, "errors": [], "warnings": []},
        )

    current = store.get_version(draft.version_id)
    assert current.state == "draft"
    assert current.authoring_graph["name"] == "Newer draft wins"
    assert current.draft_revision == updated.draft_revision


def test_v3_generic_update_rejects_schema_downgrade_and_preserves_draft(tmp_path):
    from app.services.workflow_authoring_factory import build_canvas_graph
    from app.services.workflow_version_store import WorkflowVersionError, WorkflowVersionStore

    store = WorkflowVersionStore(tmp_path / "workflows.db")
    graph = build_canvas_graph(
        workflow_id="v3_update_immutable",
        name="V3 immutable",
        description="",
        template="free_source_analysis",
    )
    _, draft = store.create_canvas_workflow(
        workflow_id="v3_update_immutable",
        name="V3 immutable",
        description="",
        authoring_graph=graph,
    )
    downgraded = deepcopy(draft.authoring_graph)
    downgraded["schema_version"] = 2

    with pytest.raises(WorkflowVersionError, match="schema_version_immutable"):
        store.update_draft(
            draft.version_id,
            authoring_graph=downgraded,
            expected_revision=draft.draft_revision,
        )

    current = store.get_version(draft.version_id)
    assert current.authoring_graph["schema_version"] == 3
    assert current.draft_revision == draft.draft_revision


def test_v3_publish_rejects_schema_downgrade_and_keeps_draft(tmp_path):
    from app.services.workflow_authoring_factory import build_canvas_graph
    from app.services.workflow_version_store import WorkflowVersionError, WorkflowVersionStore

    store = WorkflowVersionStore(tmp_path / "workflows.db")
    graph = build_canvas_graph(
        workflow_id="v3_publish_immutable",
        name="V3 immutable",
        description="",
        template="free_source_analysis",
    )
    _, draft = store.create_canvas_workflow(
        workflow_id="v3_publish_immutable",
        name="V3 immutable",
        description="",
        authoring_graph=graph,
    )
    downgraded = deepcopy(draft.authoring_graph)
    downgraded["schema_version"] = 2

    with pytest.raises(WorkflowVersionError, match="schema_version_immutable"):
        store.publish_version(
            draft.version_id,
            expected_revision=draft.draft_revision,
            authoring_graph=downgraded,
            compiled_definition={
                "id": "v3_publish_immutable",
                "name": "V3 immutable",
                "version": 1,
                "inputs": [],
                "steps": [],
                "outputs": [],
            },
            compiled_plan={"schema_version": 1, "nodes": []},
            validation={"valid": True, "errors": [], "warnings": []},
        )

    current = store.get_version(draft.version_id)
    assert current.state == "draft"
    assert current.authoring_graph["schema_version"] == 3


def test_v2_draft_rejects_client_authored_v3_schema_and_server_ids(tmp_path):
    from app.services.workflow_authoring_factory import build_canvas_graph
    from app.services.workflow_version_store import WorkflowVersionError, WorkflowVersionStore

    store = WorkflowVersionStore(tmp_path / "workflows.db")
    _, draft = store.create_workflow(
        workflow_id="v2_no_smuggled_v3",
        name="V2 legacy editor",
        description="",
        authoring_graph={**_graph(), "workflow_id": "v2_no_smuggled_v3"},
    )
    smuggled_v3 = build_canvas_graph(
        workflow_id="v2_no_smuggled_v3",
        name="Smuggled V3",
        description="",
        template="free_source_analysis",
    )

    with pytest.raises(WorkflowVersionError, match="schema_version_immutable"):
        store.update_draft(draft.version_id, authoring_graph=smuggled_v3)
    with pytest.raises(WorkflowVersionError, match="schema_version_immutable"):
        store.publish_version(
            draft.version_id,
            authoring_graph=smuggled_v3,
            compiled_definition={
                "id": "v2_no_smuggled_v3",
                "name": "Smuggled V3",
                "version": 1,
                "inputs": [],
                "steps": [],
                "outputs": [],
            },
            compiled_plan={"schema_version": 1, "nodes": []},
            validation={"valid": True, "errors": [], "warnings": []},
        )

    current = store.get_version(draft.version_id)
    assert current.state == "draft"
    assert current.authoring_graph["schema_version"] == 2
    assert current.authoring_graph["nodes"][0]["id"] == "agent"


def test_v3_publish_rejects_client_authored_server_identity(tmp_path):
    from app.services.workflow_authoring_factory import build_canvas_graph, build_v3_node
    from app.services.workflow_version_store import WorkflowVersionError, WorkflowVersionStore

    store = WorkflowVersionStore(tmp_path / "workflows.db")
    graph = build_canvas_graph(
        workflow_id="v3_publish_ids",
        name="V3 server IDs",
        description="",
        template="blank",
    )
    _, draft = store.create_canvas_workflow(
        workflow_id="v3_publish_ids",
        name="V3 server IDs",
        description="",
        authoring_graph=graph,
    )
    client_graph = deepcopy(draft.authoring_graph)
    client_graph["nodes"].append(build_v3_node("agent", label="Client node"))

    with pytest.raises(WorkflowVersionError, match="v3_new_nodes_require_command"):
        store.publish_version(
            draft.version_id,
            expected_revision=draft.draft_revision,
            authoring_graph=client_graph,
            compiled_definition={
                "id": "v3_publish_ids",
                "name": "V3 server IDs",
                "version": 1,
                "inputs": [],
                "steps": [],
                "outputs": [],
            },
            compiled_plan={"schema_version": 1, "nodes": []},
            validation={"valid": True, "errors": [], "warnings": []},
        )

    current = store.get_version(draft.version_id)
    assert current.state == "draft"
    assert current.authoring_graph["nodes"] == []


def test_published_v3_creates_a_v3_draft_without_schema_downgrade(tmp_path):
    from app.services.workflow_authoring_factory import build_canvas_graph
    from app.services.workflow_version_store import WorkflowVersionStore

    store = WorkflowVersionStore(tmp_path / "workflows.db")
    graph = build_canvas_graph(
        workflow_id="v3_next_draft",
        name="V3 next draft",
        description="",
        template="free_source_analysis",
    )
    _, draft = store.create_canvas_workflow(
        workflow_id="v3_next_draft",
        name="V3 next draft",
        description="",
        authoring_graph=graph,
    )
    published = store.publish_version(
        draft.version_id,
        expected_revision=draft.draft_revision,
        authoring_graph=draft.authoring_graph,
        compiled_definition={
            "id": "v3_next_draft",
            "name": "V3 next draft",
            "version": 1,
            "inputs": [],
            "steps": [],
            "outputs": [],
        },
        compiled_plan={"schema_version": 1, "nodes": []},
        validation={"valid": True, "errors": [], "warnings": []},
    )

    next_draft = store.create_draft(
        "v3_next_draft", based_on_version_id=published.version_id
    )

    assert next_draft.authoring_graph["schema_version"] == 3
    assert next_draft.draft_revision == 1
    assert next_draft.authoring_graph["nodes"] == published.authoring_graph["nodes"]


def test_generic_create_rejects_v3_but_canvas_command_accepts_server_graph(tmp_path):
    from app.services.workflow_authoring_factory import build_canvas_graph
    from app.services.workflow_version_store import WorkflowVersionError, WorkflowVersionStore

    store = WorkflowVersionStore(tmp_path / "workflows.db")
    graph = build_canvas_graph(
        workflow_id="v3_creation_boundary",
        name="V3 creation boundary",
        description="",
        template="blank",
    )

    with pytest.raises(WorkflowVersionError, match="create_canvas_workflow"):
        store.create_workflow(
            workflow_id="v3_creation_boundary",
            name="V3 creation boundary",
            description="",
            authoring_graph=graph,
        )

    header, draft = store.create_canvas_workflow(
        workflow_id="v3_creation_boundary",
        name="V3 creation boundary",
        description="",
        authoring_graph=graph,
    )
    assert header.current_draft_version_id == draft.version_id
    assert draft.authoring_graph["schema_version"] == 3


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
        assert next_draft.status_code == 409
        assert next_draft.json()["detail"]["code"] == "legacy_workflow_read_only"
        listed_without_draft = await client.get("/api/workbench/workflows")
        listed_projection = next(
            item for item in listed_without_draft.json() if item.get("id") == "new_flow"
        )
        assert listed_projection["v2"]["published_version_id"] == draft_id
        assert listed_projection["v2"]["current_draft_version_id"] is None
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


@pytest.mark.parametrize("required_capability", ["mcp", "streaming"])
@pytest.mark.asyncio
async def test_v3_publish_rejects_unsupported_builtin_adapter_capability_before_persist(
    tmp_path, monkeypatch, required_capability
):
    """Publish must reject an impossible V3 Adapter contract before it is frozen."""
    from app.api import agent_workbench, workbench_v2_workflows
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    sqlite_db = data_dir / "codetalk.db"
    repo = tmp_path / "repo"
    repo.mkdir()
    with sqlite3.connect(sqlite_db) as db:
        db.execute(
            "CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT, repo_path TEXT)"
        )
        db.execute(
            "INSERT INTO workspaces (id, name, repo_path) VALUES (?, ?, ?)",
            ("ws-capability", "Capability repository", str(repo)),
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
            "/api/workbench/workflows/new",
            json={"template": "free_source_analysis", "name": "Capability gate"},
        )
        assert created.status_code == 201
        workflow_id = created.json()["workflow"]["workflow_id"]
        draft = created.json()["draft"]
        graph = deepcopy(draft["authoring_graph"])
        agent = next(node for node in graph["nodes"] if node["kind"] == "agent")
        agent["config"]["provider_capabilities_required"] = [required_capability]
        updated = await client.put(
            f"/api/workbench/workflows/{workflow_id}/versions/{draft['version_id']}",
            json={
                "authoring_graph": graph,
                "expected_revision": draft["draft_revision"],
            },
        )
        assert updated.status_code == 200

        trial = await client.post(
            f"/api/workbench/workflows/{workflow_id}/versions/{draft['version_id']}/test-run",
            json={
                "workspace_id": "ws-capability",
                "inputs": {},
                "expected_revision": updated.json()["draft_revision"],
            },
        )
        published = await client.post(
            f"/api/workbench/workflows/{workflow_id}/versions/{draft['version_id']}/publish",
            json={"expected_revision": updated.json()["draft_revision"]},
        )
        reloaded = await client.get(
            f"/api/workbench/workflows/{workflow_id}/versions/{draft['version_id']}"
        )

    assert trial.status_code == 422
    assert published.status_code == 422
    for response in (trial, published):
        issue = next(
            item
            for item in response.json()["detail"]["errors"]
            if item["code"] == "provider_capabilities_unsupported"
        )
        assert issue["provider"] == "builtin-llm"
        assert issue["missing_capabilities"] == [required_capability]
        assert "不支持" in issue["message"]
        assert "设置" in issue["message"]
    assert reloaded.json()["state"] == "draft"


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
            json={"workspace_id": "ws-1", "inputs": {}, "node_id": "agent"},
        )

    assert trial.status_code == 201
    payload = trial.json()
    assert payload["status"] == "prepared"
    assert payload["workspace_id"] == "ws-1"
    assert payload["diagnostic"] == {
        "kind": "node_trial",
        "node_id": "agent",
        "not_a_formal_delivery": True,
    }
    assert payload["compiled_plan"]["topological_order"] == ["agent"]
    task_run = WorkbenchTaskRunStore(
        data_dir / "workbench" / "task_runs"
    ).load(payload["task_run_id"])
    assert task_run.repo_path == str(repo.resolve())
    assert task_run.input_snapshot["repo_path"] == str(repo.resolve())
    assert task_run.task_bundle["compiled_plan"]["workflow_version_id"] == draft_id
    assert task_run.workflow_snapshot["id"] == "new_flow"
    assert task_run.task_bundle["trial_run"] is True
    assert task_run.task_bundle["diagnostic"]["not_a_formal_delivery"] is True


@pytest.mark.asyncio
async def test_draft_trial_can_execute_only_the_selected_graph_node_as_diagnostic(
    tmp_path, monkeypatch
):
    from app.api.workbench_v2_workflows import _diagnostic_node_trial
    from app.services.workflow_graph import compile_workflow_graph

    graph = _workspace_graph()
    compiled = compile_workflow_graph(graph, capabilities={"providers": {"builtin-llm": {"available": True, "mcp_profiles": []}}, "skills": []}, workflow_version_id="draft")
    diagnostic = _diagnostic_node_trial(compiled, "agent")

    assert diagnostic["compiled_plan"]["topological_order"] == ["agent"]
    assert diagnostic["compiled_plan"]["nodes"][0]["depends_on"] == []
    assert [item["id"] for item in diagnostic["compiled_definition"]["steps"]] == ["agent"]
    assert [item["id"] for item in diagnostic["compiled_definition"]["inputs"]] == ["repo_path"]


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
