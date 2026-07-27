"""Phase 3 migration contracts for legacy editor preservation and V3 copies."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


_FIXTURE_DIR = Path(__file__).with_name("fixtures") / "harness_workflow_refactor"


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _canvas_app() -> FastAPI:
    from app.api import agent_workbench, workbench_v2_workflows

    app = FastAPI()
    app.include_router(agent_workbench.router)
    app.include_router(workbench_v2_workflows.router)
    return app


def _stable_v2_identity(graph: dict) -> dict:
    return {
        "workflow_id": graph["workflow_id"],
        "node_ids": [node["id"] for node in graph["nodes"]],
        "edge_ids": [edge["id"] for edge in graph["edges"]],
        "contract_ids": [
            node["config"].get("contract_id")
            for node in graph["nodes"]
            if node["kind"] == "input"
        ],
        "step_ids": [
            node["config"].get("step_id")
            for node in graph["nodes"]
            if node["kind"] == "agent"
        ],
        "output_ids": [
            node["config"].get("output_id")
            for node in graph["nodes"]
            if node["kind"] == "output"
        ],
    }


def test_phase3_v2_draft_fixture_is_a_frozen_editable_legacy_snapshot():
    fixture = _fixture("v2-draft-canvas-compatibility.json")
    graph = fixture["workflow_version"]["authoring_graph"]

    assert fixture["fixture_kind"] == "historical_v2_draft_canvas_compatibility"
    assert fixture["workflow_version"]["state"] == "draft"
    assert graph["schema_version"] == 2
    assert _stable_v2_identity(graph) == {
        "workflow_id": "phase3_v2_draft_canvas",
        "node_ids": ["repo", "analyze", "report"],
        "edge_ids": ["repo-analyze", "analyze-report"],
        "contract_ids": ["repo_path"],
        "step_ids": ["analyze"],
        "output_ids": ["report"],
    }


def test_v3_identity_guard_rejects_candidate_schema_downgrade():
    from app.services.workflow_authoring_factory import (
        CanvasAuthoringError,
        assert_v3_technical_ids_preserved,
        build_canvas_graph,
    )

    existing = build_canvas_graph(
        workflow_id="v3_identity_guard",
        name="V3 identity guard",
        description="",
        template="free_source_analysis",
    )
    candidate = deepcopy(existing)
    candidate["schema_version"] = 2

    with pytest.raises(CanvasAuthoringError, match="schema_version_immutable"):
        assert_v3_technical_ids_preserved(existing, candidate)


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
def test_v3_identity_guard_rejects_config_identity_injection_or_mutation(field):
    from app.services.workflow_authoring_factory import (
        CanvasAuthoringError,
        assert_v3_technical_ids_preserved,
        build_canvas_graph,
    )

    existing = build_canvas_graph(
        workflow_id="v3_config_identity_guard",
        name="V3 config identity guard",
        description="",
        template="free_source_analysis",
    )
    candidate = deepcopy(existing)
    agent = next(node for node in candidate["nodes"] if node["kind"] == "agent")
    agent["config"][field] = 999 if field == "handler_version" else "client-owned"

    with pytest.raises(CanvasAuthoringError):
        assert_v3_technical_ids_preserved(existing, candidate)


def test_v3_identity_guard_rejects_graph_version_identity_injection():
    from app.services.workflow_authoring_factory import (
        CanvasAuthoringError,
        assert_v3_technical_ids_preserved,
        build_canvas_graph,
    )

    existing = build_canvas_graph(
        workflow_id="v3_version_identity_guard",
        name="V3 version identity guard",
        description="",
        template="free_source_analysis",
    )
    candidate = deepcopy(existing)
    candidate["version_id"] = "client-version"

    with pytest.raises(CanvasAuthoringError, match="version_id_immutable"):
        assert_v3_technical_ids_preserved(existing, candidate)


@pytest.mark.asyncio
async def test_v2_draft_stays_in_legacy_editor_and_explicit_copy_creates_v3(
    tmp_path, monkeypatch
):
    """No read or edit may silently migrate a saved V2 draft to V3."""
    from app.config import settings

    fixture = _fixture("v2-draft-canvas-compatibility.json")
    graph = fixture["workflow_version"]["authoring_graph"]
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "workbench_v2_enabled", True)

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows",
            json={
                "id": fixture["workflow_header"]["workflow_id"],
                "name": fixture["workflow_header"]["name"],
                "description": fixture["workflow_header"]["description"],
                "authoring_graph": graph,
            },
        )
        assert created.status_code == 201
        version_id = created.json()["current_draft_version_id"]
        loaded = await client.get(
            f"/api/workbench/workflows/{fixture['workflow_header']['workflow_id']}/versions/{version_id}"
        )
        copied = await client.post(
            f"/api/workbench/workflows/{fixture['workflow_header']['workflow_id']}"
            f"/versions/{version_id}/copy-to-v3"
        )
        loaded_again = await client.get(
            f"/api/workbench/workflows/{fixture['workflow_header']['workflow_id']}/versions/{version_id}"
        )

    assert loaded.status_code == 200
    assert loaded.json().get("editor_mode") == "legacy"
    assert copied.status_code == 201
    copied_payload = copied.json()
    assert copied_payload["source_version_id"] == version_id
    assert copied_payload["draft"]["authoring_graph"]["schema_version"] == 3
    assert copied_payload["migration_preview"]["source_schema_version"] == 2
    assert loaded_again.status_code == 200
    assert loaded_again.json()["authoring_graph"] == graph


@pytest.mark.asyncio
async def test_copy_to_v3_structurally_rejects_an_existing_v3_version(
    tmp_path, monkeypatch
):
    """A V3 source must not be remigrated into a graph with silently lost edges."""
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "free_source_analysis", "name": "Already V3"},
        )
        draft = created.json()["draft"]
        before = await client.get("/api/workbench/workflows")
        copied = await client.post(
            f"/api/workbench/workflows/{draft['workflow_id']}"
            f"/versions/{draft['version_id']}/copy-to-v3"
        )
        after = await client.get("/api/workbench/workflows")

    assert copied.status_code == 409
    assert copied.json()["detail"] == {
        "code": "copy_to_v3_source_schema_invalid",
        "message": "Only legacy V1 or V2 workflow versions can be copied to V3.",
    }
    assert after.json() == before.json()


@pytest.mark.asyncio
async def test_v2_save_preserves_technical_ids_and_historical_v1_fixture_is_read_only(
    tmp_path, monkeypatch
):
    """Legacy data stays readable and only explicit V3 copy is allowed to transform it."""
    from app.config import settings

    v1 = _fixture("v1-published-workflow.json")
    v2 = _fixture("v2-draft-canvas-compatibility.json")
    graph = deepcopy(v2["workflow_version"]["authoring_graph"])
    before = _stable_v2_identity(graph)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "workbench_v2_enabled", True)
    from app.services.workflow_version_store import WorkflowVersionStore

    WorkflowVersionStore(data_dir / "workbench" / "workflows.db").ensure_legacy_published_workflows(
        [v1["workflow_version"]["compiled_definition"]]
    )

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows",
            json={
                "id": v2["workflow_header"]["workflow_id"],
                "name": v2["workflow_header"]["name"],
                "description": v2["workflow_header"]["description"],
                "authoring_graph": graph,
            },
        )
        assert created.status_code == 201
        version_id = created.json()["current_draft_version_id"]
        graph["nodes"][0]["label"] = "Repository moved"
        graph["nodes"][0]["position"] = {"x": 200, "y": 260}
        saved = await client.put(
            f"/api/workbench/workflows/{v2['workflow_header']['workflow_id']}/versions/{version_id}",
            json={"authoring_graph": graph},
        )
        v1_response = await client.get(
            f"/api/workbench/workflows/{v1['workflow_header']['workflow_id']}"
        )
        v1_versions = await client.get(
            f"/api/workbench/workflows/{v1['workflow_header']['workflow_id']}/versions"
        )
        v1_version_id = next(
            item["version_id"]
            for item in v1_versions.json()["items"]
            if item["state"] == "published"
        )
        v1_copied = await client.post(
            f"/api/workbench/workflows/{v1['workflow_header']['workflow_id']}"
            f"/versions/{v1_version_id}/copy-to-v3"
        )
        v1_after_copy = await client.get(
            f"/api/workbench/workflows/{v1['workflow_header']['workflow_id']}"
        )

    assert saved.status_code == 200
    assert _stable_v2_identity(saved.json()["authoring_graph"]) == before
    assert v1_response.status_code == 200
    assert v1_response.json().get("editor_mode") == "read_only_legacy"
    assert v1_response.json()["authoring_graph"] == v1["workflow_version"]["authoring_graph"]
    assert v1_copied.status_code == 201
    copied_graph = v1_copied.json()["draft"]["authoring_graph"]
    assert copied_graph["schema_version"] == 3
    assert {node["kind"] for node in copied_graph["nodes"]} >= {"input", "agent", "output"}
    assert copied_graph["edges"]
    assert v1_after_copy.json()["authoring_graph"] == v1["workflow_version"]["authoring_graph"]
