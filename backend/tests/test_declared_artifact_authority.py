"""Phase 1 contracts for V3 declared artifacts during task preparation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _v3_graph(*, profile: str = "artifact_only", schema: dict | None = None) -> dict:
    return {
        "schema_version": 3,
        "workflow_id": "v3_report_only",
        "name": "Report only",
        "settings": {"validation_profile": profile, "stop_on_error": True},
        "nodes": [
            {
                "id": "repo",
                "kind": "input",
                "label": "Repository",
                "position": {"x": 0, "y": 0},
                "ports": {"inputs": [], "outputs": [{"id": "value", "type": "directory"}]},
                "config": {
                    "input_id": "repo",
                    "type": "directory",
                    "required": True,
                    "resolver": "manual",
                },
            },
            {
                "id": "analysis_target",
                "kind": "input",
                "label": "Analysis target",
                "position": {"x": 0, "y": 160},
                "ports": {"inputs": [], "outputs": [{"id": "value", "type": "long_text"}]},
                "config": {
                    "input_id": "analysis_target",
                    "type": "long_text",
                    "required": True,
                    "resolver": "manual",
                },
            },
            {
                "id": "analyze",
                "kind": "agent",
                "label": "Analyze",
                "position": {"x": 320, "y": 0},
                "ports": {
                    "inputs": [
                        {"id": "repo_path", "type": "directory", "required": True},
                        {"id": "analysis_target", "type": "long_text", "required": True},
                    ],
                    "outputs": [{"id": "report", "type": "artifact", "required": True}],
                },
                "config": {
                    "handler_id": "agent",
                    "handler_version": 1,
                    "provider_ref": "builtin-llm",
                    "mcp_profiles": ["gitnexus"],
                    "skill_ids": ["source-evidence-first"],
                    "skill_instructions": ["Preserve user input verbatim."],
                    "goal": "Analyze the supplied source and produce the declared report.",
                    "prompt_template_version": 1,
                    "prompt_template": "{{node_goal}}\n{{bound_inputs}}\n{{output_contract}}",
                    "input_rendering": {
                        "preserve_user_text_verbatim": True,
                        "binding_order": ["repo_path", "analysis_target"],
                    },
                    "timeout_sec": 1200,
                    "idle_timeout_sec": 180,
                    "retry_policy": {"max_attempts": 1, "backoff_seconds": 0},
                    "failure_policy": "stop",
                },
            },
            {
                "id": "report_output",
                "kind": "output",
                "label": "Report",
                "position": {"x": 640, "y": 0},
                "ports": {"inputs": [{"id": "value", "type": "artifact", "required": True}], "outputs": []},
                "config": {
                    "output_id": "report",
                    "artifact": "report.md",
                    "media_type": "application/json" if schema else "text/markdown",
                    "required": True,
                    "schema": schema,
                },
            },
        ],
        "edges": [
            {"id": "repo_to_agent", "kind": "data", "source": {"node_id": "repo", "port_id": "value"}, "target": {"node_id": "analyze", "port_id": "repo_path"}},
            {"id": "target_to_agent", "kind": "data", "source": {"node_id": "analysis_target", "port_id": "value"}, "target": {"node_id": "analyze", "port_id": "analysis_target"}},
            {"id": "agent_to_output", "kind": "data", "source": {"node_id": "analyze", "port_id": "report"}, "target": {"node_id": "report_output", "port_id": "value"}},
        ],
    }


def _capabilities() -> dict:
    return {
        "handlers": {
            "agent": {"versions": [1]},
            "artifact_exists": {"versions": [1]},
            "json_schema": {"versions": [1]},
        }
    }


def _compiled_v3(*, profile: str = "artifact_only", schema: dict | None = None) -> dict:
    from app.services.workflow_contract_v3 import compile_workflow_contract_v3

    compiled = compile_workflow_contract_v3(
        _v3_graph(profile=profile, schema=schema),
        capabilities=_capabilities(),
        workflow_version_id="wfv_v3_report",
    )
    assert compiled["validation_result"]["valid"] is True
    return compiled


def _prepare_v3(tmp_path: Path, *, profile: str = "artifact_only", schema: dict | None = None):
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer
    compiled = _compiled_v3(profile=profile, schema=schema)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "module.c").write_text("int module_entry(void) { return 0; }\n", encoding="utf-8")
    class FrozenWorkflowStore:
        def freeze_workflow_snapshot(self, workflow_id: str) -> dict:
            assert workflow_id == "v3_report_only"
            return json.loads(json.dumps(compiled["compiled_definition"]))

    user_text = "  first line\nsecond line\n" + ("x" * 4096) + "  "
    prepared = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=FrozenWorkflowStore(),
    ).prepare(
        workflow_id="v3_report_only",
        workspace_id="ws-v3",
        repo_path=str(repo),
        inputs={"repo": str(repo), "analysis_target": user_text},
    )
    return prepared, compiled, user_text


@pytest.mark.parametrize(
    ("profile", "schema", "expected_validators", "expected_artifact_status"),
    [
        ("artifact_only", None, ["artifact_exists"], "not_started"),
        ("none", None, [], "not_requested"),
        ("schema", {"type": "object"}, ["artifact_exists", "json_schema"], "not_started"),
    ],
)
def test_v3_preparer_uses_declared_report_only_without_implicit_governance(
    tmp_path, profile, schema, expected_validators, expected_artifact_status
):
    prepared, compiled, user_text = _prepare_v3(tmp_path, profile=profile, schema=schema)
    root = Path(prepared.artifact_dir)

    assert prepared.task_bundle["compiled_contract_version"] == 3
    assert prepared.task_bundle["validation_profile"] == profile
    assert prepared.task_bundle["declared_outputs"] == compiled["compiled_definition"]["declared_outputs"]
    assert prepared.task_bundle["required_artifacts_by_step"] == {"analyze": ["report.md"]}
    assert prepared.agent_runs[0]["required_artifacts"] == ["report.md"]
    assert prepared.execution_status == "queued"
    assert prepared.artifact_validation_status == expected_artifact_status
    assert prepared.governance_status == "not_requested"
    assert prepared.delivery_status == "pending"
    assert [validator["handler_id"] for validator in prepared.task_bundle["validators"]] == expected_validators
    assert prepared.input_snapshot["analysis_target"] == user_text
    assert prepared.task_bundle["inputs"]["analysis_target"] == user_text

    serialized = json.loads((root / "task_bundle.json").read_text(encoding="utf-8"))
    assert serialized["inputs"]["analysis_target"] == user_text
    assert "test_activity_contract" not in serialized
    assert "artifact_contract_v3" not in serialized
    assert not (root / "test_activity_contract.json").exists()
    assert not (root / "artifact_contract_v3.json").exists()
    assert not any("sfmea" in path.name or "black_box" in path.name or "source_scope" in path.name for path in root.rglob("*"))
    run_snapshot = json.loads((root / "run_snapshot_v3.json").read_text(encoding="utf-8"))
    assert "v3_runtime_contract" in run_snapshot["components"]
    from app.services.workbench_task_run import validate_run_snapshot_v3
    assert validate_run_snapshot_v3(root) == []

    agent_bundle = json.loads((root / "agent_runs" / "analyze" / "task_bundle.json").read_text(encoding="utf-8"))
    envelope = agent_bundle["execution_contract"]
    assert agent_bundle["inputs"]["analysis_target"] == user_text
    assert next(
        item for item in envelope["user_inputs"]
        if item["input_id"] == "analysis_target"
    ) == {
        "input_id": "analysis_target",
        "type": "long_text",
        "role": "",
        "value": user_text,
    }
    assert envelope["outputs"]["required_artifacts"] == ["report.md"]
    assert "test_activity_contract" not in envelope


def test_v3_task_run_store_round_trips_all_four_status_axes(tmp_path):
    from app.services.workbench_task_run import WorkbenchTaskRunStore

    prepared, _compiled, _user_text = _prepare_v3(tmp_path)
    root = Path(prepared.artifact_dir)
    store = WorkbenchTaskRunStore(root.parent)

    loaded = store.load(prepared.task_run_id)
    assert (
        loaded.execution_status,
        loaded.artifact_validation_status,
        loaded.governance_status,
        loaded.delivery_status,
    ) == ("queued", "not_started", "not_requested", "pending")
    payload = json.loads((root / "task_run.json").read_text(encoding="utf-8"))
    payload.update({
        "execution_status": "running",
        "artifact_validation_status": "running",
        "governance_status": "waived",
        "delivery_status": "pending",
    })
    (root / "task_run.json").write_text(json.dumps(payload), encoding="utf-8")
    running = store.load(prepared.task_run_id)
    assert (
        running.execution_status,
        running.artifact_validation_status,
        running.governance_status,
        running.delivery_status,
    ) == ("running", "running", "waived", "pending")

    payload.update({
        "execution_status": "completed",
        "artifact_validation_status": "passed",
        "governance_status": "not_requested",
        "delivery_status": "ready",
    })
    (root / "task_run.json").write_text(json.dumps(payload), encoding="utf-8")
    ready = store.load(prepared.task_run_id)
    assert ready.delivery_status == "ready"

    payload["delivery_status"] = "blocked"
    (root / "task_run.json").write_text(json.dumps(payload), encoding="utf-8")
    blocked = store.load(prepared.task_run_id)
    assert blocked.delivery_status == "blocked"


def test_v3_compiled_definition_round_trips_through_real_workflow_store(tmp_path):
    from app.services.workflow_dsl import WorkflowStore

    compiled = _compiled_v3(profile="artifact_only")
    definition = compiled["compiled_definition"]
    store = WorkflowStore(tmp_path / "workflows.db")

    saved = store.save_workflow(definition)
    frozen = store.freeze_workflow_snapshot(saved.id)

    assert frozen["compiled_contract_version"] == 3
    assert [step["id"] for step in frozen["steps"]] == ["analyze"]
    assert [item["handler_id"] for item in frozen["validators"]] == [
        "artifact_exists"
    ]


def test_handler_capabilities_are_generic_and_shared_by_workflow_api():
    from app.api.workbench_v2_workflows import _workflow_graph_capabilities
    from app.services.workflow_handler_registry import workflow_handler_capability_snapshot

    registry = workflow_handler_capability_snapshot()
    assert registry == {
        "handlers": {
            "agent": {"versions": [1], "kind": "agent"},
            "human_approval": {"versions": [1], "kind": "human_approval"},
            "subagent": {"versions": [1], "kind": "subagent"},
            "artifact_exists": {"versions": [1], "kind": "validator"},
            "json_schema": {"versions": [1], "kind": "validator"},
            "source_evidence": {"versions": [1], "kind": "validator"},
            "storage_test_design": {
                "versions": [1],
                "kind": "governance",
                "input_ports": [
                    {
                        "key": "source_evidence",
                        "label": "源码证据",
                        "type": "artifact",
                        "required": True,
                        "collection": False,
                    }
                ],
                "output_ports": [
                    {
                        "key": "sfmea",
                        "label": "SFMEA 风险清单",
                        "type": "artifact",
                        "required": True,
                        "collection": False,
                    },
                    {
                        "key": "black_box_cases",
                        "label": "黑盒测试用例",
                        "type": "artifact",
                        "required": True,
                        "collection": False,
                    },
                ],
            },
            "sfmea": {"versions": [1], "kind": "validator"},
            "black_box": {"versions": [1], "kind": "validator"},
            "independent_review": {"versions": [1], "kind": "validator"},
        }
    }
    api_capabilities = _workflow_graph_capabilities()
    assert api_capabilities["handlers"] == registry["handlers"]
    assert api_capabilities["handlers"]["storage_test_design"]["kind"] == "governance"
    assert "formal_release" not in api_capabilities["handlers"]


def test_v3_task_configuration_rejects_custom_and_unknown_outputs_without_changing_declared_authority():
    from app.services.workbench_task_compile import TaskConfigurationError, compile_task_configuration

    compiled = _compiled_v3()
    with pytest.raises(TaskConfigurationError, match="V3.*custom_outputs"):
        compile_task_configuration(
            compiled_definition=compiled["compiled_definition"],
            compiled_plan=compiled["compiled_plan"],
            execution_overrides={},
            output_overrides={"custom_outputs": [{"id": "sfmea", "from": "analyze", "type": "json", "artifact": "sfmea.json", "schema": {"type": "array"}}]},
        )
    with pytest.raises(TaskConfigurationError, match="未知输出"):
        compile_task_configuration(
            compiled_definition=compiled["compiled_definition"],
            compiled_plan=compiled["compiled_plan"],
            execution_overrides={},
            output_overrides={"outputs": {"sfmea": {"enabled": True}}},
        )


def test_unknown_compiled_contract_version_fails_closed_and_legacy_definition_keeps_legacy_behavior(tmp_path):
    from app.services.workbench_task_compile import TaskConfigurationError, compile_task_configuration
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer

    compiled = _compiled_v3()
    compiled["compiled_definition"]["compiled_contract_version"] = 99
    with pytest.raises(TaskConfigurationError, match="compiled_contract_version"):
        compile_task_configuration(
            compiled_definition=compiled["compiled_definition"],
            compiled_plan=compiled["compiled_plan"],
            execution_overrides={},
            output_overrides={},
        )

    class UnknownContractStore:
        def freeze_workflow_snapshot(self, workflow_id: str) -> dict:
            return {"id": workflow_id, "compiled_contract_version": 99}

    with pytest.raises(ValueError, match="compiled_contract_version"):
        WorkbenchTaskRunPreparer(
            artifact_root=tmp_path / "task_runs",
            workflow_store=UnknownContractStore(),
        ).prepare(
            workflow_id="unknown-contract",
            workspace_id="ws",
            repo_path=".",
            inputs={},
        )

    legacy = {
        "id": "legacy",
        "steps": [{"id": "analyze", "type": "agent_task", "required_artifacts": []}],
        "outputs": [],
    }
    result = compile_task_configuration(
        compiled_definition=legacy,
        compiled_plan={"nodes": [{"node_id": "analyze"}]},
        execution_overrides={},
        output_overrides={"custom_outputs": [{"id": "extra", "from": "analyze", "type": "markdown", "artifact": "extra.md"}]},
    )
    assert result["compiled_definition"]["outputs"] == [{"id": "extra", "label": "extra", "type": "markdown", "from": "analyze", "artifact": "extra.md", "required": False}]
