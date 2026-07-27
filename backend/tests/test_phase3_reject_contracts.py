"""Regression contracts for the Phase 3 independent-review blockers."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient


def _v3_graph(*, provider_key: str = "provider_ref", provider: str = "builtin-llm") -> dict:
    return {
        "schema_version": 3,
        "workflow_id": "phase3-review",
        "name": "Phase 3 review",
        "description": "Regression fixture",
        "settings": {"validation_profile": "none"},
        "nodes": [
            {
                "id": "repo-node",
                "kind": "input",
                "label": "Source repository",
                "position": {"x": 0, "y": 0},
                "ports": {"inputs": [], "outputs": [{"id": "value", "type": "directory"}]},
                "config": {"input_id": "repo", "type": "directory", "required": True},
            },
            {
                "id": "design-node",
                "kind": "input",
                "label": "Design document",
                "position": {"x": 0, "y": 180},
                "ports": {"inputs": [], "outputs": [{"id": "value", "type": "file"}]},
                "config": {"input_id": "design_doc", "type": "file", "required": False},
            },
            {
                "id": "analyze",
                "kind": "agent",
                "label": "Analyze",
                "position": {"x": 300, "y": 0},
                "ports": {
                    "inputs": [
                        {"id": "repo_path", "type": "directory", "required": True},
                        {"id": "design_file", "type": "file", "required": False},
                    ],
                    "outputs": [{"id": "report", "type": "artifact"}],
                },
                "config": {"handler_id": "agent", provider_key: provider},
            },
            {
                "id": "report-node",
                "kind": "output",
                "label": "Report",
                "position": {"x": 600, "y": 0},
                "ports": {"inputs": [{"id": "value", "type": "artifact"}], "outputs": []},
                "config": {
                    "output_id": "report",
                    "artifact": "report.md",
                    "media_type": "text/markdown",
                    "required": True,
                },
            },
        ],
        "edges": [
            {
                "id": "repo-to-agent",
                "kind": "data",
                "source": {"node_id": "repo-node", "port_id": "value"},
                "target": {"node_id": "analyze", "port_id": "repo_path"},
            },
            {
                "id": "design-to-agent",
                "kind": "data",
                "source": {"node_id": "design-node", "port_id": "value"},
                "target": {"node_id": "analyze", "port_id": "design_file"},
            },
            {
                "id": "agent-to-report",
                "kind": "data",
                "source": {"node_id": "analyze", "port_id": "report"},
                "target": {"node_id": "report-node", "port_id": "value"},
            },
        ],
    }


def _compile(graph: dict) -> dict:
    from app.services.workflow_contract_v3 import compile_workflow_contract_v3

    return compile_workflow_contract_v3(
        graph,
        capabilities={"handlers": {"agent": {"versions": [1]}}},
        workflow_version_id="wfv_phase3",
    )


def test_node_trial_keeps_every_named_canvas_input_from_source_input_id():
    from app.api.workbench_v2_workflows import _diagnostic_node_trial

    diagnostic = _diagnostic_node_trial(_compile(_v3_graph()), "analyze")

    assert {item["id"] for item in diagnostic["compiled_definition"]["inputs"]} == {
        "repo",
        "design_doc",
    }


@pytest.mark.parametrize(
    "provider",
    ["builtin-llm", "codex", "claude-code", "opencode"],
)
def test_v3_compiler_preserves_the_selected_provider_ref_exactly(provider: str):
    compiled = _compile(_v3_graph(provider=provider))

    assert compiled["validation_result"]["valid"] is True
    assert compiled["compiled_definition"]["steps"][0]["provider"] == provider


def test_v3_compiler_reads_legacy_provider_during_compatibility_window():
    compiled = _compile(_v3_graph(provider_key="provider", provider="claude-code"))

    assert compiled["validation_result"]["valid"] is True
    assert compiled["compiled_definition"]["steps"][0]["provider"] == "claude-code"


def test_canvas_edge_command_reports_occupied_scalar_before_type_mismatch():
    from app.api.workbench_v2_workflows import _assert_edge_ports_exist

    graph = _v3_graph()
    graph["edges"].append(
        {
            "id": "existing-repo-binding",
            "kind": "data",
            "source": {"node_id": "repo-node", "port_id": "value"},
            "target": {"node_id": "analyze", "port_id": "design_file"},
        }
    )

    with pytest.raises(HTTPException) as caught:
        _assert_edge_ports_exist(
            graph,
            {"node_id": "repo-node", "port_id": "value"},
            {"node_id": "analyze", "port_id": "design_file"},
        )

    assert caught.value.detail == {
        "code": "multiple_edges_to_single_input",
        "message": "该输入已绑定",
    }


def _canvas_app() -> FastAPI:
    from app.api import agent_workbench, workbench_v2_workflows

    app = FastAPI()
    app.include_router(agent_workbench.router)
    app.include_router(workbench_v2_workflows.router)
    return app


@pytest.mark.asyncio
async def test_failed_v3_compile_preserves_revision_and_returns_it_to_the_client(
    tmp_path, monkeypatch
):
    from app.api import workbench_v2_workflows
    from app.config import settings
    from app.services.workflow_graph import WorkflowGraphValidationError

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    async with AsyncClient(
        transport=ASGITransport(app=_canvas_app()), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "free_source_analysis", "name": "Invalid compile"},
        )
        draft = created.json()["draft"]
        current_revision = draft["draft_revision"]

        def reject_compile(*args, **kwargs):
            raise WorkflowGraphValidationError(
                {
                    "valid": False,
                    "errors": [{"code": "required_input_unbound", "message": "Required input is not bound"}],
                    "warnings": [],
                }
            )

        monkeypatch.setattr(workbench_v2_workflows, "compile_workflow_graph", reject_compile)
        failed = await client.post(
            f"/api/workbench/workflows/{draft['workflow_id']}/versions/{draft['version_id']}/compile",
            json={"expected_revision": current_revision},
        )
        refreshed = await client.get(
            f"/api/workbench/workflows/{draft['workflow_id']}/versions/{draft['version_id']}"
        )

    assert failed.status_code == 422
    assert failed.json()["detail"]["draft_revision"] == current_revision
    assert refreshed.json()["draft_revision"] == current_revision


def test_v3_runtime_resolves_input_bound_through_server_generated_port_id():
    from app.services.workbench_workflow_runner import _resolve_plan_node_inputs

    resolved = _resolve_plan_node_inputs(
        plan_node={
            "resolved_input_bindings": {
                "port_target_generated": {
                    "source_node_id": "node_input_generated",
                    "source_input_id": "input_generated",
                    "source_port_id": "port_source_generated",
                }
            }
        },
        input_snapshot={"input_generated": {"path": "inputs/design.md"}},
        direct_dependency_outputs={},
    )

    assert resolved == {
        "port_target_generated": {"path": "inputs/design.md"}
    }
