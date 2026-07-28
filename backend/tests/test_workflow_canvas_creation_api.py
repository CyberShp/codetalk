"""Phase 3 Canvas First API contracts.

These tests deliberately describe the new V3 authoring boundary before its
implementation.  They must stay red until the backend owns all technical IDs
and the three designer resources have independent failure contracts.
"""

from __future__ import annotations

import sqlite3
from copy import deepcopy

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _canvas_app() -> FastAPI:
    from app.api import agent_workbench, workbench_v2_workflows

    app = FastAPI()
    app.include_router(agent_workbench.router)
    app.include_router(workbench_v2_workflows.router)
    return app


def _technical_ids(graph: dict) -> dict[str, tuple[str, ...]]:
    values: dict[str, list[str]] = {
        "nodes": [],
        "ports": [],
        "inputs": [],
        "outputs": [],
        "edges": [],
    }
    for node in graph["nodes"]:
        values["nodes"].append(node["id"])
        for direction in ("inputs", "outputs"):
            values["ports"].extend(port["id"] for port in node["ports"][direction])
        if node["kind"] == "input":
            values["inputs"].append(node["config"]["input_id"])
        if node["kind"] == "output":
            values["outputs"].append(node["config"]["output_id"])
    values["edges"].extend(edge["id"] for edge in graph["edges"])
    return {key: tuple(value) for key, value in values.items()}


async def _create_file_input_trial_draft(client: AsyncClient) -> dict:
    """Build a V3 draft through public commands with one required file input."""
    created = await client.post(
        "/api/workbench/workflows/new",
        json={"template": "free_source_analysis", "name": "Atomic trial inputs"},
    )
    assert created.status_code == 201
    draft = created.json()["draft"]
    workflow_id = draft["workflow_id"]
    version_id = draft["version_id"]
    graph = draft["authoring_graph"]
    revision = draft["draft_revision"]
    agent = next(node for node in graph["nodes"] if node["kind"] == "agent")

    added_input = await client.post(
        f"/api/workbench/workflows/{workflow_id}/versions/{version_id}/nodes",
        json={
            "expected_revision": revision,
            "kind": "input",
            "label": "开发设计文档",
            "position": {"x": 80, "y": 420},
            "config": {
                "type": "file",
                "required": True,
                "resolver": "local",
            },
        },
    )
    assert added_input.status_code == 201
    input_node = added_input.json()["node"]
    revision = added_input.json()["draft"]["draft_revision"]

    added_port = await client.post(
        f"/api/workbench/workflows/{workflow_id}/versions/{version_id}"
        f"/nodes/{agent['id']}/ports",
        json={
            "expected_revision": revision,
            "direction": "inputs",
            "label": "design_doc",
            "type": "file",
            "required": True,
        },
    )
    assert added_port.status_code == 201
    target_port = added_port.json()["port"]
    revision = added_port.json()["draft"]["draft_revision"]

    added_edge = await client.post(
        f"/api/workbench/workflows/{workflow_id}/versions/{version_id}/edges",
        json={
            "expected_revision": revision,
            "source": {
                "node_id": input_node["id"],
                "port_id": input_node["ports"]["outputs"][0]["id"],
            },
            "target": {"node_id": agent["id"], "port_id": target_port["id"]},
        },
    )
    assert added_edge.status_code == 201
    return {
        "workflow_id": workflow_id,
        "version_id": version_id,
        "revision": added_edge.json()["draft"]["draft_revision"],
        "input_id": input_node["config"]["input_id"],
    }


def _assert_no_trial_persistence(data_dir) -> None:
    workbench_root = data_dir / "workbench"
    assert not (workbench_root / "trial_workflows.db").exists()
    assert not (workbench_root / "task_runs").exists()


def _configure_trial_workspace(tmp_path, monkeypatch):
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    sqlite_db = data_dir / "codetalk.db"
    repo = tmp_path / "repo"
    repo.mkdir()
    with sqlite3.connect(sqlite_db) as db:
        db.execute(
            "CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT NOT NULL, repo_path TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO workspaces (id, name, repo_path) VALUES (?, ?, ?)",
            ("ws-1", "Repository", str(repo)),
        )
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "sqlite_db", str(sqlite_db))
    return data_dir


@pytest.mark.asyncio
@pytest.mark.parametrize("template", ["blank", "free_source_analysis"])
async def test_canvas_create_api_generates_v3_draft_without_client_technical_ids(
    tmp_path, monkeypatch, template
):
    """Only a display name reaches the create command; IDs belong to the server."""
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "workbench_v2_enabled", True)

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/workbench/workflows/new",
            json={
                "template": template,
                "name": "Source review",
                "description": "Canvas-first acceptance fixture",
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["workflow"]["workflow_id"].startswith("wf_")
    assert payload["draft"]["version_id"].startswith("wfv_")
    assert payload["draft"]["authoring_graph"]["schema_version"] == 3
    assert payload["meta"]["backend_commit_sha"]
    assert payload["designer_url"] == (
        f"/workflows/{payload['workflow']['workflow_id']}/designer"
    )


@pytest.mark.asyncio
async def test_legacy_create_endpoint_rejects_client_authored_v3_graph(
    tmp_path, monkeypatch
):
    from app.config import settings
    from app.services.workflow_authoring_factory import build_canvas_graph

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "workbench_v2_enabled", True)
    graph = build_canvas_graph(
        workflow_id="client_v3_bypass",
        name="Client V3 bypass",
        description="",
        template="free_source_analysis",
    )

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/workbench/workflows",
            json={
                "id": "client_v3_bypass",
                "name": "Client V3 bypass",
                "description": "",
                "authoring_graph": graph,
            },
        )
        listed = await client.get("/api/workbench/workflows")

    assert response.status_code == 422
    assert "create_canvas_workflow" in response.json()["detail"]
    assert all(
        (item.get("workflow_id") or item.get("id")) != "client_v3_bypass"
        for item in listed.json()
    )


@pytest.mark.asyncio
async def test_free_source_template_owns_stable_ids_across_rename_move_save_and_refresh(
    tmp_path, monkeypatch
):
    """Moving or relabelling a node must never replace V3 technical identities."""
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "workbench_v2_enabled", True)

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "free_source_analysis", "name": "Initial name"},
        )
        assert created.status_code == 201
        payload = created.json()
        workflow_id = payload["workflow"]["workflow_id"]
        version_id = payload["draft"]["version_id"]
        graph = payload["draft"]["authoring_graph"]
        revision = payload["draft"]["draft_revision"]
        before = _technical_ids(graph)
        assert all(before.values())
        assert {node["kind"] for node in graph["nodes"]} == {
            "input",
            "agent",
            "output",
        }

        edited = deepcopy(graph)
        edited["name"] = "Renamed canvas"
        edited["nodes"][0]["label"] = "Source workspace"
        edited["nodes"][0]["position"] = {"x": 180, "y": 260}
        saved = await client.put(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}",
            json={"authoring_graph": edited, "expected_revision": revision},
        )
        refreshed = await client.get(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}"
        )

    assert saved.status_code == 200
    assert refreshed.status_code == 200
    assert _technical_ids(saved.json()["authoring_graph"]) == before
    assert _technical_ids(refreshed.json()["authoring_graph"]) == before


@pytest.mark.asyncio
async def test_v3_draft_rejects_stale_put_and_server_commands_without_overwriting(
    tmp_path, monkeypatch
):
    """Every V3 mutation is a compare-and-swap, while V2 remains compatible."""
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "free_source_analysis", "name": "Concurrent canvas"},
        )
        assert created.status_code == 201
        created_draft = created.json()["draft"]
        workflow_id = created_draft["workflow_id"]
        version_id = created_draft["version_id"]
        read_by_a = await client.get(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}"
        )
        read_by_b = await client.get(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}"
        )
        assert read_by_a.status_code == read_by_b.status_code == 200
        initial_a = read_by_a.json()
        initial_b = read_by_b.json()
        assert initial_a["draft_revision"] == initial_b["draft_revision"]
        initial_revision = initial_a["draft_revision"]
        graph_a = deepcopy(initial_a["authoring_graph"])
        graph_b = deepcopy(initial_b["authoring_graph"])
        graph_a["name"] = "A saved this"
        graph_b["name"] = "B must not overwrite this"
        agent = next(node for node in graph_b["nodes"] if node["kind"] == "agent")
        source = next(node for node in graph_b["nodes"] if node["kind"] == "input")

        saved_by_a = await client.put(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}",
            json={
                "authoring_graph": graph_a,
                "expected_revision": initial_revision,
            },
        )
        stale_put = await client.put(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}",
            json={
                "authoring_graph": graph_b,
                "expected_revision": initial_revision,
            },
        )
        stale_port = await client.post(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}"
            f"/nodes/{agent['id']}/ports",
            json={
                "expected_revision": initial_revision,
                "direction": "inputs",
                "label": "design_doc",
                "type": "file",
            },
        )
        stale_edge = await client.post(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}/edges",
            json={
                "expected_revision": initial_revision,
                "source": {
                    "node_id": source["id"],
                    "port_id": source["ports"]["outputs"][0]["id"],
                },
                "target": {
                    "node_id": agent["id"],
                    "port_id": agent["ports"]["inputs"][0]["id"],
                },
            },
        )
        missing_revision = await client.put(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}",
            json={"authoring_graph": graph_a},
        )
        missing_command_revision = await client.post(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}"
            f"/nodes/{agent['id']}/ports",
            json={"direction": "inputs", "label": "without_revision", "type": "file"},
        )
        refreshed = await client.get(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}"
        )

    assert saved_by_a.status_code == 200
    assert saved_by_a.json()["draft_revision"] == initial_revision + 1
    for response in (stale_put, stale_port, stale_edge):
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "stale_draft"
        assert "刷新" in response.json()["detail"]["message"]
    for response in (missing_revision, missing_command_revision):
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "expected_revision_required"
    assert refreshed.json()["draft_revision"] == initial_revision + 1
    assert refreshed.json()["authoring_graph"]["name"] == "A saved this"


@pytest.mark.asyncio
async def test_v3_validate_and_compile_advance_and_return_draft_revision(
    tmp_path, monkeypatch
):
    """Derived validation writes must obey CAS without breaking trial preparation."""
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "free_source_analysis", "name": "Revision compile"},
        )
        payload = created.json()
        workflow_id = payload["workflow"]["workflow_id"]
        version_id = payload["draft"]["version_id"]
        initial_revision = payload["draft"]["draft_revision"]
        validated = await client.post(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}/validate",
            json={"expected_revision": initial_revision},
        )
        compiled = await client.post(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}/compile",
            json={"expected_revision": initial_revision + 1},
        )
        refreshed = await client.get(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}"
        )

    assert validated.status_code == 200
    assert validated.json()["valid"] is True
    assert validated.json()["draft_revision"] == initial_revision + 1
    assert compiled.status_code == 200
    assert compiled.json()["draft_revision"] == initial_revision + 2
    assert refreshed.json()["draft_revision"] == initial_revision + 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "body"),
    [
        ("validate", {}),
        ("compile", {}),
        ("publish", {}),
        ("test-run", {"workspace_id": "not-resolved", "inputs": {}}),
    ],
)
async def test_v3_derived_operations_require_expected_revision(
    tmp_path, monkeypatch, operation, body
):
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "free_source_analysis", "name": "Revision required"},
        )
        draft = created.json()["draft"]
        response = await client.post(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/{operation}",
            json=body,
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "expected_revision_required"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["validate", "compile", "publish"])
async def test_v3_derived_operations_return_stale_draft_when_snapshot_loses_race(
    tmp_path, monkeypatch, operation
):
    from app.api import workbench_v2_workflows
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app(), raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "free_source_analysis", "name": "Race loser"},
        )
        draft = created.json()["draft"]
        workflow_id = draft["workflow_id"]
        version_id = draft["version_id"]
        revision = draft["draft_revision"]
        advanced = False

        def advance_draft() -> None:
            nonlocal advanced
            if advanced:
                return
            advanced = True
            store = workbench_v2_workflows.workflow_version_store()
            current = store.get_version(version_id)
            graph = deepcopy(current.authoring_graph)
            graph["name"] = "Concurrent winner"
            store.update_draft(
                version_id,
                authoring_graph=graph,
                expected_revision=current.draft_revision,
            )

        if operation == "validate":
            original = workbench_v2_workflows.validate_workflow_graph

            def race(*args, **kwargs):
                result = original(*args, **kwargs)
                advance_draft()
                return result

            monkeypatch.setattr(workbench_v2_workflows, "validate_workflow_graph", race)
        else:
            original = workbench_v2_workflows.compile_workflow_graph

            def race(*args, **kwargs):
                result = original(*args, **kwargs)
                advance_draft()
                return result

            monkeypatch.setattr(workbench_v2_workflows, "compile_workflow_graph", race)

        response = await client.post(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}/{operation}",
            json={"expected_revision": revision},
        )
        refreshed = await client.get(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}"
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "stale_draft"
    assert refreshed.json()["state"] == "draft"
    assert refreshed.json()["authoring_graph"]["name"] == "Concurrent winner"
    assert refreshed.json()["draft_revision"] == revision + 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("inputs_factory", "expected_message"),
    [
        (lambda _input_id, _tmp_path: {}, "required input"),
        (
            lambda input_id, tmp_path: {
                input_id: str(tmp_path / "does-not-exist" / "design.md")
            },
            "does-not-exist",
        ),
    ],
)
async def test_v3_trial_input_4xx_is_atomic_before_revision_and_persistence(
    tmp_path, monkeypatch, inputs_factory, expected_message
):
    """Deterministic input failures must precede CAS and every persistent write."""
    data_dir = _configure_trial_workspace(tmp_path, monkeypatch)

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app(), raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        draft = await _create_file_input_trial_draft(client)
        failed = await client.post(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/test-run",
            json={
                "workspace_id": "ws-1",
                "inputs": inputs_factory(draft["input_id"], tmp_path),
                "expected_revision": draft["revision"],
            },
        )
        refreshed = await client.get(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}"
        )

    assert failed.status_code == 422
    assert expected_message in str(failed.json()["detail"])
    assert refreshed.json()["draft_revision"] == draft["revision"]
    assert refreshed.json()["compiled_definition"] is None
    assert refreshed.json()["compiled_plan"] is None
    _assert_no_trial_persistence(data_dir)


@pytest.mark.asyncio
async def test_v3_trial_stale_after_input_preflight_fails_closed_without_persistence(
    tmp_path, monkeypatch
):
    """The sole CAS must close the race between pure preflight and persistence."""
    from app.api import workbench_v2_workflows
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer

    data_dir = _configure_trial_workspace(tmp_path, monkeypatch)
    original_preflight = WorkbenchTaskRunPreparer.preflight_inputs
    advanced = False

    def preflight_then_advance(*, workflow_snapshot, inputs):
        nonlocal advanced
        original_preflight(workflow_snapshot=workflow_snapshot, inputs=inputs)
        if advanced:
            return
        advanced = True
        store = workbench_v2_workflows.workflow_version_store()
        current = store.get_version(version_id)
        graph = deepcopy(current.authoring_graph)
        graph["name"] = "Concurrent winner after preflight"
        store.update_draft(
            version_id,
            authoring_graph=graph,
            expected_revision=current.draft_revision,
        )

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app(), raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "free_source_analysis", "name": "Post-preflight race"},
        )
        draft = created.json()["draft"]
        workflow_id = draft["workflow_id"]
        version_id = draft["version_id"]
        revision = draft["draft_revision"]
        monkeypatch.setattr(
            WorkbenchTaskRunPreparer,
            "preflight_inputs",
            staticmethod(preflight_then_advance),
        )
        stale = await client.post(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}/test-run",
            json={
                "workspace_id": "ws-1",
                "inputs": {},
                "expected_revision": revision,
            },
        )
        refreshed = await client.get(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}"
        )

    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_draft"
    assert refreshed.json()["draft_revision"] == revision + 1
    assert refreshed.json()["authoring_graph"]["name"] == (
        "Concurrent winner after preflight"
    )
    _assert_no_trial_persistence(data_dir)


@pytest.mark.asyncio
async def test_v3_trial_never_reports_a_post_commit_prepare_fault_as_4xx(
    tmp_path, monkeypatch
):
    """After CAS, an unexpected preparer fault is a server failure, not user input 4xx."""
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer

    _configure_trial_workspace(tmp_path, monkeypatch)

    def fail_after_commit(self, **_kwargs):
        raise ValueError("unexpected late preparation fault")

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app(), raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "free_source_analysis", "name": "Late fault"},
        )
        draft = created.json()["draft"]
        monkeypatch.setattr(WorkbenchTaskRunPreparer, "prepare", fail_after_commit)
        failed = await client.post(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/test-run",
            json={
                "workspace_id": "ws-1",
                "inputs": {},
                "expected_revision": draft["draft_revision"],
            },
        )

    assert failed.status_code == 500
    assert "试运行提交后准备失败" in str(failed.json()["detail"])


@pytest.mark.asyncio
async def test_v3_stale_trial_has_no_side_effects_then_current_trial_advances_revision(
    tmp_path, monkeypatch
):
    from app.api import workbench_v2_workflows
    from app.config import settings
    from app.services.workbench_task_run import WorkbenchTaskRunStore

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    sqlite_db = data_dir / "codetalk.db"
    repo = tmp_path / "repo"
    repo.mkdir()
    with sqlite3.connect(sqlite_db) as db:
        db.execute(
            "CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT NOT NULL, repo_path TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO workspaces (id, name, repo_path) VALUES (?, ?, ?)",
            ("ws-1", "Repository", str(repo)),
        )
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "sqlite_db", str(sqlite_db))

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app(), raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "free_source_analysis", "name": "Trial race"},
        )
        draft = created.json()["draft"]
        workflow_id = draft["workflow_id"]
        version_id = draft["version_id"]
        revision = draft["draft_revision"]
        original_compile = workbench_v2_workflows.compile_workflow_graph

        def compile_then_advance(*args, **kwargs):
            result = original_compile(*args, **kwargs)
            store = workbench_v2_workflows.workflow_version_store()
            current = store.get_version(version_id)
            graph = deepcopy(current.authoring_graph)
            graph["name"] = "Trial concurrent winner"
            store.update_draft(
                version_id,
                authoring_graph=graph,
                expected_revision=current.draft_revision,
            )
            return result

        monkeypatch.setattr(
            workbench_v2_workflows, "compile_workflow_graph", compile_then_advance
        )
        stale = await client.post(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}/test-run",
            json={
                "workspace_id": "ws-1",
                "inputs": {},
                "expected_revision": revision,
            },
        )

        _assert_no_trial_persistence(data_dir)

        monkeypatch.setattr(
            workbench_v2_workflows, "compile_workflow_graph", original_compile
        )
        current_revision = revision + 1
        prepared = await client.post(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}/test-run",
            json={
                "workspace_id": "ws-1",
                "inputs": {},
                "expected_revision": current_revision,
            },
        )

    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_draft"
    assert prepared.status_code == 201
    assert prepared.json()["draft_revision"] == current_revision + 1
    task_run = WorkbenchTaskRunStore(
        data_dir / "workbench" / "task_runs"
    ).load(prepared.json()["task_run_id"])
    assert task_run.task_run_id == prepared.json()["task_run_id"]


@pytest.mark.asyncio
async def test_canvas_create_rejects_client_supplied_technical_ids(tmp_path, monkeypatch):
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "workbench_v2_enabled", True)

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/workbench/workflows/new",
            json={
                "template": "blank",
                "name": "No client IDs",
                "workflow_id": "wf_user_supplied",
                "node_id": "node_user_supplied",
                "port_id": "port_user_supplied",
                "input_id": "input_user_supplied",
                "output_id": "output_user_supplied",
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "client_technical_ids_forbidden"


@pytest.mark.parametrize(
    "field",
    [
        "workflow_id",
        "version_id",
        "node_id",
        "port_id",
        "input_id",
        "output_id",
        "edge_id",
        "step_id",
        "contract_id",
        "handler_id",
        "handler_version",
        "validator_type",
    ],
)
def test_v3_node_factory_rejects_every_client_owned_identity_field(field):
    """Node commands may carry business config, never runtime identity."""
    from app.services.workflow_authoring_factory import CanvasAuthoringError, build_v3_node

    with pytest.raises(CanvasAuthoringError, match="client_technical_ids_forbidden"):
        build_v3_node("agent", config={field: "client-owned"})


def test_v3_agent_handler_is_selected_by_the_server_registry(monkeypatch):
    from app.services.workflow_authoring_factory import build_v3_node
    from app.services import workflow_node_registry

    monkeypatch.setattr(
        workflow_node_registry,
        "executable_node_definition",
        lambda kind: {
            "kind": kind,
            "execution": {
                "available": True,
                "handler_id": "registry-agent",
                "handler_version": 7,
            },
        },
    )

    node = build_v3_node(
        "agent",
        config={
            "goal": "Inspect the selected module",
            "provider_ref": "builtin-llm",
        },
    )
    assert node["config"]["handler_id"] == "registry-agent"
    assert node["config"]["handler_version"] == 7
    assert node["config"]["goal"] == "Inspect the selected module"
    assert node["config"]["provider_ref"] == "builtin-llm"


@pytest.mark.asyncio
async def test_v3_agent_handler_injection_cannot_persist_or_reach_compiled_plan(
    tmp_path, monkeypatch
):
    """A rejected raw PUT leaves the registry-owned handler executable."""
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "free_source_analysis", "name": "Handler ownership"},
        )
        draft = created.json()["draft"]
        workflow_id = draft["workflow_id"]
        version_id = draft["version_id"]
        revision = draft["draft_revision"]
        injected_graph = deepcopy(draft["authoring_graph"])
        injected_agent = next(
            node for node in injected_graph["nodes"] if node["kind"] == "agent"
        )
        injected_agent["config"]["handler_id"] = "artifact_exists"
        injected_agent["config"]["handler_version"] = 999

        rejected = await client.put(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}",
            json={
                "authoring_graph": injected_graph,
                "expected_revision": revision,
            },
        )
        persisted = await client.get(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}"
        )
        compiled = await client.post(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}/compile",
            json={"expected_revision": revision},
        )

    assert rejected.status_code == 422
    persisted_agent = next(
        node
        for node in persisted.json()["authoring_graph"]["nodes"]
        if node["kind"] == "agent"
    )
    assert persisted_agent["config"]["handler_id"] == "agent"
    assert persisted_agent["config"]["handler_version"] == 1
    assert compiled.status_code == 200
    compiled_agent = next(
        node
        for node in compiled.json()["compiled_plan"]["nodes"]
        if node["kind"] == "agent"
    )
    assert compiled_agent["handler_id"] == "agent"
    assert compiled_agent["handler_version"] == 1


@pytest.mark.asyncio
async def test_v3_validator_handler_can_switch_only_through_server_command_and_publish(
    tmp_path, monkeypatch
):
    """The visible Validator selector must persist through a server-owned command."""
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "free_source_analysis", "name": "Switch validator"},
        )
        draft = created.json()["draft"]
        graph = draft["authoring_graph"]
        declared_output = next(node for node in graph["nodes"] if node["kind"] == "output")
        added = await client.post(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/nodes",
            json={
                "expected_revision": draft["draft_revision"],
                "kind": "validator",
                "label": "交付件校验",
                "position": {"x": 820, "y": 420},
                "config": {
                    "handler_id": "artifact_exists",
                    "required_outputs": [declared_output["config"]["output_id"]],
                    "blocking": True,
                },
            },
        )
        assert added.status_code == 201
        validator = added.json()["node"]

        switched = await client.patch(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/nodes/{validator['id']}/handler",
            json={
                "expected_revision": added.json()["draft"]["draft_revision"],
                "handler_id": "json_schema",
            },
        )
        assert switched.status_code == 200
        refreshed_without_schema = await client.get(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}"
        )
        rejected_compile = await client.post(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/compile",
            json={"expected_revision": switched.json()["draft"]["draft_revision"]},
        )
        assert rejected_compile.status_code == 422
        rejected_publish = await client.post(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/publish",
            json={"expected_revision": switched.json()["draft"]["draft_revision"]},
        )
        assert rejected_publish.status_code == 422

        configured_graph = deepcopy(switched.json()["draft"]["authoring_graph"])
        configured_output = next(
            node for node in configured_graph["nodes"] if node["kind"] == "output"
        )
        configured_output["config"].update(
            {
                "type": "json",
                "media_type": "application/json",
                "artifact": "report.json",
                "schema": {"type": "object"},
            }
        )
        configured = await client.put(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}",
            json={
                "authoring_graph": configured_graph,
                "expected_revision": switched.json()["draft"]["draft_revision"],
            },
        )
        assert configured.status_code == 200, configured.text
        refreshed_with_schema = await client.get(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}"
        )
        compiled = await client.post(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/compile",
            json={"expected_revision": configured.json()["draft_revision"]},
        )
        published = await client.post(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/publish",
            json={"expected_revision": compiled.json()["draft_revision"]},
        )

    switched_node = switched.json()["node"]
    assert switched_node["kind"] == "validator"
    assert switched_node["config"]["handler_id"] == "json_schema"
    assert switched_node["config"]["handler_version"] == 1
    assert switched_node["config"]["required_outputs"] == [
        declared_output["config"]["output_id"]
    ]
    refreshed_node = next(
        node
        for node in refreshed_without_schema.json()["authoring_graph"]["nodes"]
        if node["id"] == validator["id"]
    )
    assert refreshed_node == switched_node
    for rejected in (rejected_compile, rejected_publish):
        [issue] = [
            item
            for item in rejected.json()["detail"]["errors"]
            if item["code"] == "json_schema_required_output_schema_invalid"
        ]
        assert issue["node_id"] == validator["id"]
        assert issue["output_id"] == declared_output["config"]["output_id"]
        assert issue["field"] == "required_outputs"
        assert issue["message"] == (
            "JSON 结构校验所验收的交付件“分析报告”缺少有效的 JSON Schema；"
            "请在输出节点选择 JSON 类型并配置结构规则。"
        )
    assert configured.status_code == 200
    persisted_output = next(
        node
        for node in refreshed_with_schema.json()["authoring_graph"]["nodes"]
        if node["id"] == declared_output["id"]
    )
    assert persisted_output["config"]["schema"] == {"type": "object"}
    assert compiled.status_code == 200
    compiled_validator = next(
        node
        for node in compiled.json()["compiled_plan"]["nodes"]
        if node["node_id"] == validator["id"]
    )
    assert compiled_validator["handler_id"] == "json_schema"
    assert published.status_code == 200


@pytest.mark.asyncio
async def test_v3_explicit_validator_without_declared_outputs_blocks_compile_and_publish(
    tmp_path, monkeypatch
):
    """A visible Validator cannot publish as an implicit no-op."""
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "free_source_analysis", "name": "Empty validator"},
        )
        draft = created.json()["draft"]
        added = await client.post(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/nodes",
            json={
                "expected_revision": draft["draft_revision"],
                "kind": "validator",
                "label": "交付件校验",
                "position": {"x": 820, "y": 420},
                "config": {
                    "handler_id": "artifact_exists",
                    "required_outputs": [],
                    "blocking": True,
                },
            },
        )
        assert added.status_code == 201
        revision = added.json()["draft"]["draft_revision"]
        compiled = await client.post(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/compile",
            json={"expected_revision": revision},
        )
        published = await client.post(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/publish",
            json={"expected_revision": revision},
        )

    for response in (compiled, published):
        assert response.status_code == 422
        issue = next(
            item for item in response.json()["detail"]["errors"]
            if item["code"] == "validator_required_outputs_empty"
        )
        assert issue["node_id"] == added.json()["node"]["id"]
        assert issue["field"] == "required_outputs"
        assert issue["message"] == (
            "Validator 至少选择一个已声明交付件；"
            "请在节点属性的“验收交付件”中完成选择。"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile", "output_patch", "expected_code"),
    [
        (
            "schema",
            {
                "type": "json",
                "media_type": "application/json",
                "artifact": "profile-report.json",
                "schema": {"type": "unsupported"},
            },
            "json_schema_required_output_schema_invalid",
        ),
        (
            "storage_test_design",
            {
                "type": "markdown",
                "media_type": "text/markdown",
                "artifact": "custom-professional-result.md",
                "schema": {"type": "array"},
                "validation_roles": ["sfmea", "black_box"],
            },
            "validator_output_media_type_incompatible",
        ),
    ],
)
async def test_v3_profile_output_incompatibility_blocks_compile_and_publish(
    tmp_path, monkeypatch, profile, output_patch, expected_code
):
    """Compile and publish must share the same Profile-expanded output gate."""
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "free_source_analysis", "name": "Profile output gate"},
        )
        draft = created.json()["draft"]
        graph = deepcopy(draft["authoring_graph"])
        graph["settings"]["validation_profile"] = profile
        output = next(node for node in graph["nodes"] if node["kind"] == "output")
        output["config"].update(output_patch)
        saved = await client.put(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}",
            json={
                "authoring_graph": graph,
                "expected_revision": draft["draft_revision"],
            },
        )
        assert saved.status_code == 200, saved.text
        revision = saved.json()["draft_revision"]
        compiled = await client.post(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/compile",
            json={"expected_revision": revision},
        )
        published = await client.post(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/publish",
            json={"expected_revision": revision},
        )

    for response in (compiled, published):
        assert response.status_code == 422
        issues = response.json()["detail"]["errors"]
        issue = next(item for item in issues if item["code"] == expected_code)
        assert issue["output_id"] == output["config"]["output_id"]
        assert "交付件" in issue["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_handler", ["agent", "not_registered"])
async def test_v3_validator_handler_command_rejects_wrong_kind_or_unknown_handler(
    tmp_path, monkeypatch, requested_handler
):
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "blank", "name": "Reject validator injection"},
        )
        draft = created.json()["draft"]
        added = await client.post(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/nodes",
            json={
                "expected_revision": draft["draft_revision"],
                "kind": "validator",
                "config": {"handler_id": "artifact_exists"},
            },
        )
        response = await client.patch(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/nodes/{added.json()['node']['id']}/handler",
            json={
                "expected_revision": added.json()["draft"]["draft_revision"],
                "handler_id": requested_handler,
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "validator_handler_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["change", "remove", "add"])
async def test_v3_put_freezes_server_owned_semantic_port_binding_keys(
    tmp_path, monkeypatch, mutation
):
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "free_source_analysis", "name": "Frozen semantic ports"},
        )
        draft = created.json()["draft"]
        added = await client.post(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/nodes",
            json={
                "expected_revision": draft["draft_revision"],
                "kind": "governance",
                "config": {"handler_id": "storage_test_design"},
            },
        )
        revision = added.json()["draft"]["draft_revision"]
        graph = deepcopy(added.json()["draft"]["authoring_graph"])
        governance = next(node for node in graph["nodes"] if node["kind"] == "governance")
        if mutation == "change":
            governance["ports"]["outputs"][0]["binding_key"] = "black_box_cases"
        elif mutation == "remove":
            governance["ports"]["outputs"][0].pop("binding_key")
        else:
            agent = next(node for node in graph["nodes"] if node["kind"] == "agent")
            agent["ports"]["outputs"][0]["binding_key"] = "sfmea"

        response = await client.put(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}",
            json={"authoring_graph": graph, "expected_revision": revision},
        )

    assert response.status_code == 422
    expected = (
        "binding_key_immutable"
        if mutation == "add"
        else "handler_port_contract_immutable"
    )
    assert response.json()["detail"] == expected


@pytest.mark.asyncio
async def test_v3_put_keeps_semantic_binding_while_custom_output_filename_is_editable(
    tmp_path, monkeypatch
):
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "free_source_analysis", "name": "Custom physical output"},
        )
        draft = created.json()["draft"]
        added = await client.post(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/nodes",
            json={
                "expected_revision": draft["draft_revision"],
                "kind": "governance",
                "config": {"handler_id": "storage_test_design"},
            },
        )
        graph = deepcopy(added.json()["draft"]["authoring_graph"])
        output = next(node for node in graph["nodes"] if node["kind"] == "output")
        output["config"]["artifact"] = "team-risk-register.json"
        output["label"] = "团队风险清单"
        saved = await client.put(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}",
            json={
                "authoring_graph": graph,
                "expected_revision": added.json()["draft"]["draft_revision"],
            },
        )

    assert saved.status_code == 200
    saved_governance = next(
        node
        for node in saved.json()["authoring_graph"]["nodes"]
        if node["kind"] == "governance"
    )
    assert [port["binding_key"] for port in saved_governance["ports"]["outputs"]] == [
        "sfmea",
        "black_box_cases",
    ]
    saved_output = next(
        node for node in saved.json()["authoring_graph"]["nodes"] if node["kind"] == "output"
    )
    assert saved_output["config"]["artifact"] == "team-risk-register.json"


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["add", "update", "delete"])
async def test_handler_owned_ports_reject_api_mutation_commands(
    tmp_path, monkeypatch, command
):
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "blank", "name": f"Reject handler port {command}"},
        )
        draft = created.json()["draft"]
        added = await client.post(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/nodes",
            json={
                "expected_revision": draft["draft_revision"],
                "kind": "governance",
                "config": {"handler_id": "storage_test_design"},
            },
        )
        added_payload = added.json()
        governance = added_payload["node"]
        revision = added_payload["draft"]["draft_revision"]
        base = (
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/nodes/{governance['id']}/ports"
        )
        if command == "add":
            response = await client.post(
                base,
                json={
                    "expected_revision": revision,
                    "direction": "inputs",
                    "label": "Extra",
                    "type": "artifact",
                },
            )
        elif command == "update":
            response = await client.patch(
                f"{base}/{governance['ports']['inputs'][0]['id']}",
                json={"expected_revision": revision, "required": False},
            )
        else:
            response = await client.request(
                "DELETE",
                f"{base}/{governance['ports']['inputs'][0]['id']}",
                json={"expected_revision": revision},
            )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "handler_port_contract_immutable"
    assert "系统维护" in response.json()["detail"]["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["remove", "required", "collection", "extra"])
async def test_handler_owned_ports_reject_raw_put_contract_mutation(
    tmp_path, monkeypatch, mutation
):
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "blank", "name": f"Raw handler port {mutation}"},
        )
        draft = created.json()["draft"]
        added = await client.post(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/nodes",
            json={
                "expected_revision": draft["draft_revision"],
                "kind": "governance",
                "config": {"handler_id": "storage_test_design"},
            },
        )
        payload = added.json()
        graph = deepcopy(payload["draft"]["authoring_graph"])
        governance = next(node for node in graph["nodes"] if node["kind"] == "governance")
        if mutation == "remove":
            governance["ports"]["inputs"] = []
        elif mutation == "required":
            governance["ports"]["inputs"][0]["required"] = False
        elif mutation == "collection":
            governance["ports"]["inputs"][0]["collection"] = True
        else:
            governance["ports"]["inputs"].append({
                "id": governance["ports"]["outputs"][0]["id"],
                "binding_key": "extra",
                "label": "Extra",
                "type": "artifact",
                "required": False,
                "collection": False,
            })
        response = await client.put(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}",
            json={
                "authoring_graph": graph,
                "expected_revision": payload["draft"]["draft_revision"],
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "handler_port_contract_immutable"


@pytest.mark.asyncio
async def test_validate_api_reports_unbound_storage_source_evidence_in_chinese(
    tmp_path, monkeypatch
):
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "blank", "name": "Missing source evidence"},
        )
        draft = created.json()["draft"]
        added = await client.post(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/nodes",
            json={
                "expected_revision": draft["draft_revision"],
                "kind": "governance",
                "config": {"handler_id": "storage_test_design"},
            },
        )
        response = await client.post(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/validate",
            json={"expected_revision": added.json()["draft"]["draft_revision"]},
        )

    assert response.status_code == 200
    issue = next(
        item for item in response.json()["errors"]
        if item["code"] == "required_input_unbound"
    )
    assert "源码证据" in issue["message"]
    assert "未连接" in issue["message"]


@pytest.mark.asyncio
async def test_v3_put_preserves_identity_while_business_config_remains_editable(
    tmp_path, monkeypatch
):
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "free_source_analysis", "name": "Business edit"},
        )
        draft = created.json()["draft"]
        graph = deepcopy(draft["authoring_graph"])
        agent = next(node for node in graph["nodes"] if node["kind"] == "agent")
        agent["config"]["goal"] = "Analyze only the selected transport module"
        agent["config"]["provider_ref"] = "team-provider"

        saved = await client.put(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}",
            json={
                "authoring_graph": graph,
                "expected_revision": draft["draft_revision"],
            },
        )

    assert saved.status_code == 200
    saved_agent = next(
        node
        for node in saved.json()["authoring_graph"]["nodes"]
        if node["kind"] == "agent"
    )
    assert saved_agent["config"]["goal"] == "Analyze only the selected transport module"
    assert saved_agent["config"]["provider_ref"] == "team-provider"
    assert saved_agent["config"]["handler_id"] == "agent"
    assert saved_agent["config"]["handler_version"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["add_port", "add_edge", "delete_port"])
async def test_v3_canvas_commands_reject_unrelated_client_identity_fields(
    tmp_path, monkeypatch, command
):
    """Every mutation endpoint rejects identity injection instead of ignoring it."""
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "free_source_analysis", "name": f"Reject {command}"},
        )
        draft = created.json()["draft"]
        workflow_id = draft["workflow_id"]
        version_id = draft["version_id"]
        revision = draft["draft_revision"]
        graph = draft["authoring_graph"]
        source = next(node for node in graph["nodes"] if node["kind"] == "input")
        agent = next(node for node in graph["nodes"] if node["kind"] == "agent")

        if command == "add_port":
            response = await client.post(
                f"/api/workbench/workflows/{workflow_id}/versions/{version_id}"
                f"/nodes/{agent['id']}/ports",
                json={
                    "expected_revision": revision,
                    "direction": "inputs",
                    "label": "design_doc",
                    "type": "file",
                    "handler_id": "client-handler",
                },
            )
        elif command == "add_edge":
            response = await client.post(
                f"/api/workbench/workflows/{workflow_id}/versions/{version_id}/edges",
                json={
                    "expected_revision": revision,
                    "source": {
                        "node_id": source["id"],
                        "port_id": source["ports"]["outputs"][0]["id"],
                    },
                    "target": {
                        "node_id": agent["id"],
                        "port_id": agent["ports"]["inputs"][0]["id"],
                    },
                    "step_id": "client-step",
                },
            )
        else:
            response = await client.request(
                "DELETE",
                f"/api/workbench/workflows/{workflow_id}/versions/{version_id}"
                f"/nodes/{agent['id']}/ports/{agent['ports']['inputs'][0]['id']}",
                json={
                    "expected_revision": revision,
                    "contract_id": "client-contract",
                },
            )

        persisted = await client.get(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}"
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "client_technical_ids_forbidden"
    assert persisted.json()["draft_revision"] == revision
    assert persisted.json()["authoring_graph"] == graph


@pytest.mark.asyncio
async def test_v3_port_commands_update_business_fields_and_delete_bound_edges(
    tmp_path, monkeypatch
):
    """Port CRUD keeps the server ID stable and never leaves a dangling edge."""
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "free_source_analysis", "name": "Port CRUD"},
        )
        payload = created.json()
        workflow_id = payload["workflow"]["workflow_id"]
        version_id = payload["draft"]["version_id"]
        graph = payload["draft"]["authoring_graph"]
        revision = payload["draft"]["draft_revision"]
        agent = next(node for node in graph["nodes"] if node["kind"] == "agent")
        added = await client.post(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}"
            f"/nodes/{agent['id']}/ports",
            json={
                "expected_revision": revision,
                "direction": "inputs",
                "label": "design_doc",
                "type": "file",
                "required": False,
            },
        )
        assert added.status_code == 201
        port_id = added.json()["port"]["id"]
        revision = added.json()["draft"]["draft_revision"]
        updated = await client.patch(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}"
            f"/nodes/{agent['id']}/ports/{port_id}",
            json={
                "expected_revision": revision,
                "label": "开发设计文档",
                "type": "file",
                "required": True,
            },
        )
        revision = updated.json()["draft"]["draft_revision"]
        removed = await client.request(
            "DELETE",
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}"
            f"/nodes/{agent['id']}/ports/{port_id}",
            json={"expected_revision": revision},
        )

    assert updated.status_code == 200
    updated_port = updated.json()["port"]
    assert updated_port == {
        "id": port_id,
        "label": "开发设计文档",
        "type": "file",
        "required": True,
        "collection": False,
    }
    assert removed.status_code == 200
    removed_graph = removed.json()["draft"]["authoring_graph"]
    assert all(
        port["id"] != port_id
        for node in removed_graph["nodes"]
        for direction in ("inputs", "outputs")
        for port in node.get("ports", {}).get(direction, [])
    )
    assert all(
        edge["source"]["port_id"] != port_id
        and edge["target"]["port_id"] != port_id
        for edge in removed_graph["edges"]
    )


@pytest.mark.asyncio
async def test_v3_edge_command_rejects_a_second_edge_to_non_collection_input(
    tmp_path, monkeypatch
):
    """Server commands must not rely on compiler ordering to reject overwritten bindings."""
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "free_source_analysis", "name": "Single binding"},
        )
        payload = created.json()
        workflow_id = payload["workflow"]["workflow_id"]
        version_id = payload["draft"]["version_id"]
        graph = payload["draft"]["authoring_graph"]
        revision = payload["draft"]["draft_revision"]
        source = next(node for node in graph["nodes"] if node["kind"] == "input")
        agent = next(node for node in graph["nodes"] if node["kind"] == "agent")
        duplicate = await client.post(
            f"/api/workbench/workflows/{workflow_id}/versions/{version_id}/edges",
            json={
                "expected_revision": revision,
                "source": {
                    "node_id": source["id"],
                    "port_id": source["ports"]["outputs"][0]["id"],
                },
                "target": {
                    "node_id": agent["id"],
                    "port_id": agent["ports"]["inputs"][0]["id"],
                },
            },
        )

    assert duplicate.status_code == 422
    assert duplicate.json()["detail"]["code"] == "multiple_edges_to_single_input"


def test_v3_palette_contains_only_backend_declared_executable_nodes():
    """The designer must not present nodes that cannot execute in this release."""
    from app.services.workflow_handler_registry import workflow_handler_capability_snapshot
    from app.services.workflow_node_registry import node_registry_payload

    handlers = workflow_handler_capability_snapshot()["handlers"]
    registry = node_registry_payload()
    nodes = registry["nodes"]

    assert {node["kind"] for node in nodes} >= {"input", "output", "agent"}
    assert all("execution" in node for node in nodes)
    for node in nodes:
        execution = node["execution"]
        assert execution["available"] is True
        if node["kind"] not in {"input", "output"}:
            assert execution["handler_id"] in handlers
    assert "semantic_retrieve" not in {node["kind"] for node in nodes}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "failing_symbol", "failure_kind"),
    [
        ("/api/workbench/workflow-capabilities", "ALLOWED_INPUT_TYPES", "workflow_capabilities_unavailable"),
        ("/api/workbench/node-registry", "node_registry_payload", "node_registry_unavailable"),
        ("/api/workbench/provider-capabilities", "list_agent_runtimes_sync", "provider_capabilities_unavailable"),
    ],
)
async def test_designer_resources_fail_independently_with_structured_metadata(
    tmp_path, monkeypatch, endpoint, failing_symbol, failure_kind
):
    """One failed resource must expose a retryable envelope without hiding peers."""
    from app.api import agent_workbench
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    if failing_symbol == "ALLOWED_INPUT_TYPES":
        monkeypatch.setattr(agent_workbench, failing_symbol, None)
    else:
        def unavailable(*_args, **_kwargs):
            raise RuntimeError("simulated resource outage")

        monkeypatch.setattr(agent_workbench, failing_symbol, unavailable)

    peers = {
        "/api/workbench/workflow-capabilities",
        "/api/workbench/node-registry",
        "/api/workbench/provider-capabilities",
    } - {endpoint}
    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app(), raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        failed = await client.get(endpoint)
        peer_responses = [await client.get(peer) for peer in sorted(peers)]

    assert failed.status_code == 503
    error = failed.json()["error"]
    assert error["kind"] == failure_kind
    assert error["endpoint"] == endpoint
    assert error["status"] == 503
    assert error["retryable"] is True
    assert error["backend_commit_sha"]
    assert all(response.status_code == 200 for response in peer_responses)
    assert all(response.json()["meta"]["backend_commit_sha"] for response in peer_responses)
