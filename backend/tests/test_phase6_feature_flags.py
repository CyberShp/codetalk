from __future__ import annotations

import pytest


_FLAG_ENV = {
    "workflow_checkpoint_reuse_enabled": "CODETALK_WORKFLOW_CHECKPOINT_REUSE_ENABLED",
    "workflow_hitl_enabled": "CODETALK_WORKFLOW_HITL_ENABLED",
    "workflow_tool_enabled": "CODETALK_WORKFLOW_TOOL_ENABLED",
    "workflow_subagent_enabled": "CODETALK_WORKFLOW_SUBAGENT_ENABLED",
}


def test_phase6_feature_flags_default_on_and_support_explicit_rollback(monkeypatch):
    from app.config import Settings

    for env_name in _FLAG_ENV.values():
        monkeypatch.delenv(env_name, raising=False)
    defaults = Settings(_env_file=None)
    assert {
        field: getattr(defaults, field)
        for field in _FLAG_ENV
    } == {field: True for field in _FLAG_ENV}

    for env_name in _FLAG_ENV.values():
        monkeypatch.setenv(env_name, "0")
    rolled_back = Settings(_env_file=None)
    assert {
        field: getattr(rolled_back, field)
        for field in _FLAG_ENV
    } == {field: False for field in _FLAG_ENV}


@pytest.mark.parametrize(
    ("kind", "setting_name"),
    [
        ("human_approval", "workflow_hitl_enabled"),
        ("subagent", "workflow_subagent_enabled"),
    ],
)
def test_disabled_interactive_feature_is_hidden_and_rejected_by_authoring(
    monkeypatch,
    kind: str,
    setting_name: str,
):
    from app.config import settings
    from app.services.workflow_authoring_factory import CanvasAuthoringError, build_v3_node
    from app.services.workflow_handler_registry import workflow_handler_capability_snapshot
    from app.services.workflow_node_registry import node_registry_payload

    monkeypatch.setattr(settings, setting_name, False, raising=False)
    capabilities = workflow_handler_capability_snapshot()
    assert kind not in capabilities["handlers"]
    assert kind not in {
        node["kind"]
        for node in node_registry_payload(schema_version=3, capabilities=capabilities)["nodes"]
    }
    with pytest.raises(CanvasAuthoringError, match=f"node_kind_not_executable:{kind}"):
        build_v3_node(kind)


def test_tool_requires_both_feature_flag_and_managed_registry(monkeypatch):
    from app.config import settings
    from app.services import managed_tool_runtime
    from app.services.tool_dispatch import ToolDefinition
    from app.services.workflow_handler_registry import workflow_handler_capability_snapshot

    definition = ToolDefinition(
        tool_id="json.echo",
        input_schema={"type": "object"},
        required_permissions=(),
        handler=lambda arguments: dict(arguments),
    )
    monkeypatch.setattr(
        managed_tool_runtime,
        "MANAGED_TOOL_REGISTRY",
        {"json.echo": definition},
    )
    monkeypatch.setattr(settings, "workflow_tool_enabled", False, raising=False)
    assert "tool" not in workflow_handler_capability_snapshot()["handlers"]

    monkeypatch.setattr(settings, "workflow_tool_enabled", True, raising=False)
    assert workflow_handler_capability_snapshot()["handlers"]["tool"]["tool_ids"] == [
        "json.echo"
    ]


@pytest.mark.parametrize(
    ("kind", "setting_name"),
    [
        ("human_approval", "workflow_hitl_enabled"),
        ("tool", "workflow_tool_enabled"),
        ("subagent", "workflow_subagent_enabled"),
    ],
)
def test_disabled_frozen_phase6_node_fails_before_any_node_side_effect(
    tmp_path,
    monkeypatch,
    kind: str,
    setting_name: str,
):
    from app.config import settings
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
    from test_v3_workflow_runner import _persist_task_run, _task_run

    task_run = _task_run(tmp_path, profile="none", validators=[])
    node = task_run.task_bundle["compiled_plan"]["nodes"][0]
    node.update({"kind": kind, "type": "tool" if kind == "tool" else "agent_task"})
    if kind == "tool":
        node.update({
            "handler_id": "tool",
            "tool_id": "json.echo",
            "required_permissions": [],
        })
        task_run.workflow_snapshot["steps"] = []
    _persist_task_run(tmp_path, task_run)
    monkeypatch.setattr(settings, setting_name, False)
    runner = WorkbenchWorkflowRunner(tmp_path)
    runner._execute_agent_step = lambda **_: pytest.fail(  # type: ignore[method-assign]
        "disabled Phase 6 node must fail before provider execution"
    )

    result = runner.execute_task_run(task_run.task_run_id)

    assert result.status == "error"
    assert result.execution_status == "failed"
    assert result.step_results == [{
        "step_id": "agent",
        "type": kind,
        "status": "error",
        "error": "phase6_feature_disabled",
        "feature": kind,
    }]
    attempt_dir = tmp_path / task_run.task_run_id
    assert not (attempt_dir / "checkpoints").exists()
    assert not (attempt_dir / "approvals").exists()
    assert not (attempt_dir / "nodes").exists()


def test_tool_rollback_does_not_parse_invalid_manifest_for_basic_workflow(
    tmp_path,
    monkeypatch,
):
    from app.config import settings
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
    from test_v3_workflow_runner import _complete_agent_step, _persist_task_run, _task_run

    task_run = _task_run(tmp_path, profile="none", validators=[])
    _persist_task_run(tmp_path, task_run)
    monkeypatch.setattr(settings, "workflow_tool_enabled", False)
    monkeypatch.setattr(
        settings,
        "workflow_managed_tool_manifest_dir",
        str(tmp_path / "missing-manifests"),
    )
    runner = WorkbenchWorkflowRunner(tmp_path)
    _complete_agent_step(runner, task_run)

    result = runner.execute_task_run(task_run.task_run_id)

    assert result.execution_status == "completed"
