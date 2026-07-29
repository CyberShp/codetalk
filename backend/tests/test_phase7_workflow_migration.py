"""Phase 7 migration contracts for the default V3 template catalog."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


EXPECTED_TEMPLATE_IDS = (
    "blank",
    "free_source_analysis",
    "source_with_optional_design",
    "change_impact_analysis",
    "multi_agent_analysis",
    "formal_storage_test_design",
)


def test_phase7_canvas_catalog_is_v3_first_and_domain_neutral() -> None:
    from app.services.workflow_authoring_factory import canvas_template_catalog

    catalog = canvas_template_catalog()

    assert tuple(item["id"] for item in catalog) == EXPECTED_TEMPLATE_IDS
    assert all(item["schema_version"] == 3 for item in catalog)
    assert all(item["presentation"]["lifecycle"] == "active" for item in catalog)
    assert catalog[-1]["presentation"]["scope"] == "professional"
    assert all(
        item["presentation"]["scope"] == "generic" for item in catalog[:-1]
    )


@pytest.mark.parametrize("template", EXPECTED_TEMPLATE_IDS)
def test_phase7_canvas_templates_create_server_owned_v3_graphs(template: str) -> None:
    from app.services.workflow_authoring_factory import build_canvas_graph

    graph = build_canvas_graph(
        workflow_id=f"wf_{template}",
        name="Phase 7 template",
        description="",
        template=template,
    )

    assert graph["schema_version"] == 3
    assert graph["workflow_id"] == f"wf_{template}"
    assert graph["settings"]["validation_profile"] == "artifact_only"
    assert len({node["id"] for node in graph["nodes"]}) == len(graph["nodes"])
    assert len({edge["id"] for edge in graph["edges"]}) == len(graph["edges"])


@pytest.mark.parametrize("template", EXPECTED_TEMPLATE_IDS[1:])
def test_phase7_nonblank_templates_compile_as_executable_v3(template: str) -> None:
    from app.services.workflow_authoring_factory import build_canvas_graph
    from app.services.workflow_graph import compile_workflow_graph
    from app.services.workflow_handler_registry import (
        workflow_handler_capability_snapshot,
    )

    graph = build_canvas_graph(
        workflow_id=f"wf_{template}",
        name="Phase 7 executable template",
        description="",
        template=template,
    )
    compiled = compile_workflow_graph(
        graph,
        capabilities=workflow_handler_capability_snapshot(),
        workflow_version_id=f"wfv_{template}",
    )

    assert compiled["compiled_definition"]["compiled_contract_version"] == 3
    assert compiled["compiled_plan"]["compiled_contract_version"] == 3


@pytest.mark.parametrize("template", EXPECTED_TEMPLATE_IDS[1:])
def test_phase7_server_owned_templates_compile_into_scheduler_accepted_plans(
    template: str,
) -> None:
    from app.services.workflow_authoring_factory import build_canvas_graph
    from app.services.workflow_graph import compile_workflow_graph
    from app.services.workflow_handler_registry import (
        workflow_handler_capability_snapshot,
    )
    from app.services.workflow_scheduler import WorkflowDagScheduler

    graph = build_canvas_graph(
        workflow_id=f"wf_{template}",
        name="Phase 7 scheduler template",
        description="",
        template=template,
    )
    compiled = compile_workflow_graph(
        graph,
        capabilities=workflow_handler_capability_snapshot(),
        workflow_version_id=f"wfv_{template}",
    )

    result = WorkflowDagScheduler().run(
        compiled["compiled_plan"],
        execute_node=lambda node, _dependencies: {
            "node_id": node["node_id"],
            "status": "completed",
            "validated_outputs": {},
        },
    )

    assert result.status == "succeeded"


@pytest.mark.parametrize(
    "template",
    EXPECTED_TEMPLATE_IDS[:-1],
)
def test_generic_phase7_templates_do_not_embed_storage_product_semantics(
    template: str,
) -> None:
    from app.services.workflow_authoring_factory import build_canvas_graph

    graph = build_canvas_graph(
        workflow_id=f"wf_{template}",
        name="Generic template",
        description="",
        template=template,
    )
    serialized = json.dumps(graph, ensure_ascii=False).lower()

    assert "spdk" not in serialized
    assert "iscsi" not in serialized
    assert "sfmea" not in serialized
    assert "black_box" not in serialized


def test_source_design_and_change_templates_expose_explicit_optional_inputs() -> None:
    from app.services.workflow_authoring_factory import build_canvas_graph

    source_design = build_canvas_graph(
        workflow_id="wf_source_design",
        name="Source and design",
        description="",
        template="source_with_optional_design",
    )
    change_impact = build_canvas_graph(
        workflow_id="wf_change_impact",
        name="Change impact",
        description="",
        template="change_impact_analysis",
    )

    design_input = next(
        node
        for node in source_design["nodes"]
        if node["kind"] == "input" and node["config"]["type"] == "file"
    )
    change_input = next(
        node
        for node in change_impact["nodes"]
        if node["kind"] == "input" and node["config"]["type"] == "text"
    )
    assert design_input["config"]["required"] is False
    assert change_input["config"]["required"] is True


def test_free_source_template_exposes_required_analysis_goal_input() -> None:
    from app.services.workflow_authoring_factory import build_canvas_graph

    graph = build_canvas_graph(
        workflow_id="wf_phase7_free_source_goal",
        name="Free source with explicit goal",
        description="",
        template="free_source_analysis",
    )

    inputs = [node for node in graph["nodes"] if node["kind"] == "input"]
    goal = next(node for node in inputs if node["label"] == "分析目标")
    agent = next(node for node in graph["nodes"] if node["kind"] == "agent")

    assert goal["config"]["type"] == "text"
    assert goal["config"]["required"] is True
    assert any(
        edge["source"]["node_id"] == goal["id"]
        and edge["target"]["node_id"] == agent["id"]
        for edge in graph["edges"]
    )


def test_multi_agent_template_has_main_analysis_and_independent_review() -> None:
    from app.services.workflow_authoring_factory import build_canvas_graph

    graph = build_canvas_graph(
        workflow_id="wf_multi_agent",
        name="Multi Agent",
        description="",
        template="multi_agent_analysis",
    )

    agents = [node for node in graph["nodes"] if node["kind"] == "agent"]
    outputs = [node for node in graph["nodes"] if node["kind"] == "output"]
    assert graph["settings"]["max_parallelism"] == 1
    assert {node["label"] for node in agents} == {
        "主分析 Agent",
        "独立复核 Agent",
    }
    assert {node["config"]["artifact"] for node in outputs} == {
        "analysis-primary.md",
        "analysis-review.md",
    }


def test_formal_storage_template_expands_governance_and_declared_outputs() -> None:
    from app.services.workflow_authoring_factory import build_canvas_graph

    graph = build_canvas_graph(
        workflow_id="wf_storage_design",
        name="Formal storage test design",
        description="",
        template="formal_storage_test_design",
    )

    handlers = {
        node.get("config", {}).get("handler_id")
        for node in graph["nodes"]
        if node["kind"] in {"governance", "validator"}
    }
    output_artifacts = {
        node["config"]["artifact"]
        for node in graph["nodes"]
        if node["kind"] == "output"
    }
    assert handlers == {"storage_test_design", "sfmea", "black_box"}
    assert output_artifacts == {
        "flow.md",
        "source-evidence.json",
        "sfmea.json",
        "black-box-cases.json",
    }
    agent = next(node for node in graph["nodes"] if node["kind"] == "agent")
    governance = next(node for node in graph["nodes"] if node["kind"] == "governance")
    evidence_output = next(
        node
        for node in graph["nodes"]
        if node["kind"] == "output"
        and node["config"]["artifact"] == "source-evidence.json"
    )
    source_evidence_port_id = next(
        port["id"]
        for port in agent["ports"]["outputs"]
        if port.get("binding_key") == "source_evidence"
    )
    evidence_targets = {
        edge["target"]["node_id"]
        for edge in graph["edges"]
        if edge["source"]["node_id"] == agent["id"]
        and edge["source"]["port_id"] == source_evidence_port_id
    }
    assert evidence_targets == {evidence_output["id"], governance["id"]}


def test_legacy_presentation_metadata_does_not_mutate_canonical_definitions() -> None:
    from app.services.workflow_presets import (
        builtin_workflow_presets,
        legacy_workflow_presentation,
    )

    before = {
        item["id"]: json.dumps(item["definition"], ensure_ascii=False, sort_keys=True)
        for item in builtin_workflow_presets()
    }
    presentations = legacy_workflow_presentation()
    after = {
        item["id"]: json.dumps(item["definition"], ensure_ascii=False, sort_keys=True)
        for item in builtin_workflow_presets()
    }

    assert set(presentations) == {
        "basic_source_report_codex",
        "basic_source_design_report_builtin",
    }
    assert all(item["lifecycle"] == "legacy" for item in presentations.values())
    assert all(item["scope"] == "spdk_iscsi" for item in presentations.values())
    assert all(item["default"] is False for item in presentations.values())
    assert before == after


def test_active_professional_presets_are_explicitly_opt_in() -> None:
    from app.services.workflow_presets import active_builtin_workflow_presets

    active = {item["id"]: item for item in active_builtin_workflow_presets()}

    assert active["source_flow_sfmea_blackbox"]["presentation"] == {
        "label": "正式存储测试设计（Legacy DSL）",
        "lifecycle": "active",
        "scope": "professional",
        "default": False,
    }
    for preset_id in (
        "basic_source_report_codex",
        "basic_source_design_report_builtin",
    ):
        assert active[preset_id]["presentation"]["lifecycle"] == "legacy"
        assert active[preset_id]["presentation"]["scope"] == "spdk_iscsi"
        assert active[preset_id]["presentation"]["default"] is False


@pytest.mark.asyncio
async def test_legacy_spdk_iscsi_presets_are_visible_without_v2_bootstrap(
    tmp_path, monkeypatch
) -> None:
    from app.api import agent_workbench
    from app.config import settings
    from app.services.workflow_version_store import WorkflowVersionStore

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "workbench_v2_enabled", True)
    app = FastAPI()
    app.include_router(agent_workbench.router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/workbench/workflows")

    assert response.status_code == 200
    listed = {item["id"]: item for item in response.json()}
    legacy_ids = {
        "basic_source_report_codex",
        "basic_source_design_report_builtin",
    }
    assert legacy_ids.issubset(listed)
    assert all(listed[preset_id]["presentation"]["lifecycle"] == "legacy" for preset_id in legacy_ids)
    assert all(listed[preset_id]["presentation"]["default"] is False for preset_id in legacy_ids)

    headers = WorkflowVersionStore(data_dir / "workbench" / "workflows.db").list_workflows(
        include_archived=True
    )
    assert legacy_ids.isdisjoint(header.workflow_id for header in headers)


@pytest.mark.asyncio
async def test_phase7_rollback_mode_keeps_reads_but_rejects_v3_writes_and_runs(
    tmp_path, monkeypatch
) -> None:
    from app.api import agent_workbench, workbench_v2_tasks, workbench_v2_workflows
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "workflow_v3_writes_enabled", False)
    app = FastAPI()
    app.include_router(workbench_v2_workflows.router)
    app.include_router(workbench_v2_tasks.router)
    app.include_router(agent_workbench.router)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        templates = await client.get("/api/workbench/workflow-templates")
        create = await client.post(
            "/api/workbench/workflows/new",
            json={"template": "blank", "name": "Blocked while read-only"},
        )
        run = await client.post(
            "/api/workbench/tasks/historical-task/runs",
            json={},
        )
        legacy_write = await client.post(
            "/api/workbench/workflows",
            json={"id": "legacy-write", "name": "Blocked legacy write"},
        )

    assert templates.status_code == 200
    expected = {
        "code": "workflow_v3_read_only",
        "message": "V3 工作流当前处于只读回滚模式；历史工作流、任务和产物仍可查看与下载。",
    }
    assert create.status_code == 409
    assert create.json()["detail"] == expected
    assert run.status_code == 409
    assert run.json()["detail"] == expected
    assert legacy_write.status_code == 409
    assert legacy_write.json()["detail"] == expected
