"""Phase 7 API contract: historical V1/V2 workflow history is read-only."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _app() -> FastAPI:
    from app.api import workbench_v2_workflows

    app = FastAPI()
    app.include_router(workbench_v2_workflows.router)
    return app


def _workflow_state(store, workflow_id: str) -> dict:
    header = store.get_workflow(workflow_id)
    return {
        "header": {
            "workflow_id": header.workflow_id,
            "status": header.status,
            "published_version_id": header.published_version_id,
            "current_draft_version_id": header.current_draft_version_id,
        },
        "versions": [
            {
                "version_id": version.version_id,
                "version_number": version.version_number,
                "state": version.state,
                "schema_version": version.authoring_graph["schema_version"],
                "based_on_version_id": version.based_on_version_id,
            }
            for version in store.list_versions(workflow_id)
        ],
    }


def _legacy_read_only_detail(workflow_id: str, version_id: str) -> dict[str, str]:
    version_url = f"/api/workbench/workflows/{workflow_id}/versions/{version_id}"
    return {
        "code": "legacy_workflow_read_only",
        "message": "Historical V1/V2 workflows are read-only; review the migration preview and confirm a V3 copy.",
        "migration_preview_url": f"{version_url}/migration-preview",
        "copy_to_v3_url": f"{version_url}/copy-to-v3",
    }


def _publish_v2_workflow(store, workflow_id: str):
    _header, draft = store.create_workflow(
        workflow_id=workflow_id,
        name="Published legacy V2",
        description="",
        authoring_graph={
            "schema_version": 2,
            "workflow_id": workflow_id,
            "name": "Published legacy V2",
            "description": "",
            "nodes": [],
            "edges": [],
            "settings": {"stop_on_error": True, "max_parallelism": 1},
        },
    )
    return store.publish_version(
        draft.version_id,
        authoring_graph=draft.authoring_graph,
        compiled_definition={"workflow_id": workflow_id},
        compiled_plan={"workflow_version_id": draft.version_id},
        validation={"valid": True, "errors": [], "warnings": []},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("schema_version", [1, 2])
async def test_generic_create_draft_rejects_published_legacy_base_without_writes(
    tmp_path, monkeypatch, schema_version: int
) -> None:
    from app.config import settings
    from app.services.workflow_version_store import WorkflowVersionStore

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "workflow_v3_writes_enabled", True)
    store = WorkflowVersionStore(data_dir / "workbench" / "workflows.db")
    workflow_id = f"legacy_v{schema_version}_published"
    if schema_version == 1:
        store.ensure_legacy_published_workflows(
            [
                {
                    "id": workflow_id,
                    "name": "Published legacy V1",
                    "version": 1,
                    "inputs": [],
                    "steps": [{"id": "render", "type": "report_render"}],
                    "outputs": [
                        {"id": "report", "type": "markdown", "from": "render"}
                    ],
                }
            ]
        )
        base_version_id = store.get_workflow(workflow_id).published_version_id
    else:
        base_version_id = _publish_v2_workflow(store, workflow_id).version_id
    assert base_version_id is not None
    before = _workflow_state(store, workflow_id)
    assert before == {
        "header": {
            "workflow_id": workflow_id,
            "status": "active",
            "published_version_id": base_version_id,
            "current_draft_version_id": None,
        },
        "versions": [
            {
                "version_id": base_version_id,
                "version_number": 1,
                "state": "published",
                "schema_version": schema_version,
                "based_on_version_id": None,
            }
        ],
    }

    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/workbench/workflows/{workflow_id}/versions", json={}
        )

    assert response.status_code == 409
    assert response.json()["detail"] == _legacy_read_only_detail(
        workflow_id, base_version_id
    )
    assert _workflow_state(store, workflow_id) == before


@pytest.mark.asyncio
async def test_compatibility_copy_rejects_legacy_v2_custom_draft_without_writes(
    tmp_path, monkeypatch
) -> None:
    from app.config import settings
    from app.services.workflow_version_store import WorkflowVersionStore

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "workflow_v3_writes_enabled", True)
    store = WorkflowVersionStore(data_dir / "workbench" / "workflows.db")
    workflow_id = "legacy_custom_v2_draft"
    _header, draft = store.create_workflow(
        workflow_id=workflow_id,
        name="Custom legacy V2 draft",
        description="",
        authoring_graph={
            "schema_version": 2,
            "workflow_id": workflow_id,
            "name": "Custom legacy V2 draft",
            "description": "",
            "nodes": [],
            "edges": [],
            "settings": {"stop_on_error": True, "max_parallelism": 1},
        },
    )
    before = _workflow_state(store, workflow_id)
    assert before == {
        "header": {
            "workflow_id": workflow_id,
            "status": "active",
            "published_version_id": None,
            "current_draft_version_id": draft.version_id,
        },
        "versions": [
            {
                "version_id": draft.version_id,
                "version_number": 1,
                "state": "draft",
                "schema_version": 2,
                "based_on_version_id": None,
            }
        ],
    }

    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        response = await client.post(f"/api/workbench/workflows/{workflow_id}/copy")

    assert response.status_code == 409
    assert response.json()["detail"] == _legacy_read_only_detail(
        workflow_id, draft.version_id
    )
    assert _workflow_state(store, workflow_id) == before
    assert [header.workflow_id for header in store.list_workflows(include_archived=True)] == [
        workflow_id
    ]


@pytest.mark.asyncio
async def test_generic_create_draft_allows_a_published_v3_base(tmp_path, monkeypatch) -> None:
    from app.config import settings
    from app.services.workflow_authoring_factory import build_canvas_graph
    from app.services.workflow_graph import compile_workflow_graph
    from app.services.workflow_handler_registry import workflow_handler_capability_snapshot
    from app.services.workflow_version_store import WorkflowVersionStore

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "workflow_v3_writes_enabled", True)
    store = WorkflowVersionStore(data_dir / "workbench" / "workflows.db")
    workflow_id = "published_v3_base"
    graph = build_canvas_graph(
        workflow_id=workflow_id,
        name="Published V3",
        description="",
        template="free_source_analysis",
    )
    _header, draft = store.create_canvas_workflow(
        workflow_id=workflow_id,
        name="Published V3",
        description="",
        authoring_graph=graph,
    )
    compiled = compile_workflow_graph(
        draft.authoring_graph,
        capabilities=workflow_handler_capability_snapshot(),
        workflow_version_id=draft.version_id,
    )
    published = store.publish_version(
        draft.version_id,
        expected_revision=draft.draft_revision,
        authoring_graph=draft.authoring_graph,
        compiled_definition=compiled["compiled_definition"],
        compiled_plan=compiled["compiled_plan"],
        validation=compiled["validation_result"],
    )
    before = _workflow_state(store, workflow_id)

    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/workbench/workflows/{workflow_id}/versions", json={}
        )

    assert response.status_code == 201
    created = response.json()
    assert created["authoring_graph"]["schema_version"] == 3
    after = _workflow_state(store, workflow_id)
    assert before == {
        "header": {
            "workflow_id": workflow_id,
            "status": "active",
            "published_version_id": published.version_id,
            "current_draft_version_id": None,
        },
        "versions": [
            {
                "version_id": published.version_id,
                "version_number": 1,
                "state": "published",
                "schema_version": 3,
                "based_on_version_id": None,
            }
        ],
    }
    assert after == {
        "header": {
            "workflow_id": workflow_id,
            "status": "active",
            "published_version_id": published.version_id,
            "current_draft_version_id": created["version_id"],
        },
        "versions": [
            {
                "version_id": created["version_id"],
                "version_number": 2,
                "state": "draft",
                "schema_version": 3,
                "based_on_version_id": published.version_id,
            },
            before["versions"][0],
        ],
    }
