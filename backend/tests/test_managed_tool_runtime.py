"""Composition tests for the process-local managed Tool runtime."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _preview_tool():
    from app.services.tool_dispatch import ToolDefinition

    return ToolDefinition(
        tool_id="text.preview",
        input_schema={
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
            "additionalProperties": False,
        },
        required_permissions=("workspace.read",),
        handler=lambda arguments: {"preview": arguments["text"][:4]},
    )


def _write_tool_manifest(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_manifest_directory_loads_only_an_explicit_builtin_implementation(
    tmp_path: Path,
):
    from app.services.managed_tool_runtime import managed_tool_runtime
    from app.services.tool_dispatch import ToolCallRequest

    manifest_dir = tmp_path / "managed-tools"
    manifest_dir.mkdir()
    _write_tool_manifest(
        manifest_dir / "json-echo.json",
        {
            "tool_id": "json.echo",
            "implementation": "json_echo",
            "input_schema": {
                "type": "object",
                "required": ["text"],
                "properties": {"text": {"type": "string"}},
                "additionalProperties": False,
            },
            "required_permissions": ["workspace.read"],
        },
    )

    runtime = managed_tool_runtime(manifest_dir=manifest_dir)
    result = runtime.dispatcher.dispatch(
        ToolCallRequest(
            tool_id="json.echo",
            arguments={"text": "hello"},
            granted_permissions=("workspace.read",),
        )
    )

    assert runtime.granted_permissions == ("workspace.read",)
    assert result.status == "completed"
    assert result.output == {"text": "hello"}


def test_configured_manifest_directory_is_the_default_composition_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.config import settings
    from app.services.managed_tool_runtime import managed_tool_runtime

    manifest_dir = tmp_path / "managed-tools"
    manifest_dir.mkdir()
    _write_tool_manifest(
        manifest_dir / "json-echo.json",
        {
            "tool_id": "json.echo",
            "implementation": "json_echo",
            "input_schema": {"type": "object"},
            "required_permissions": [],
        },
    )
    monkeypatch.setattr(
        settings,
        "workflow_managed_tool_manifest_dir",
        str(manifest_dir),
    )

    runtime = managed_tool_runtime()

    assert sorted(runtime.tools_by_id) == ["json.echo"]


@pytest.mark.parametrize(
    "manifest_name,payloads",
    [
        ("invalid.json", ["{"]),
        (
            "unknown-implementation.json",
            [{
                "tool_id": "json.echo",
                "implementation": "shell",
                "input_schema": {"type": "object"},
                "required_permissions": [],
            }],
        ),
        (
            "duplicate.json",
            [
                {
                    "tool_id": "json.echo",
                    "implementation": "json_echo",
                    "input_schema": {"type": "object"},
                    "required_permissions": [],
                },
                {
                    "tool_id": "json.echo",
                    "implementation": "json_echo",
                    "input_schema": {"type": "object"},
                    "required_permissions": [],
                },
            ],
        ),
        (
            "invalid-permissions.json",
            [{
                "tool_id": "json.echo",
                "implementation": "json_echo",
                "input_schema": {"type": "object"},
                "required_permissions": ["workspace.read", "workspace.read"],
            }],
        ),
        (
            "invalid-schema.json",
            [{
                "tool_id": "json.echo",
                "implementation": "json_echo",
                "input_schema": {"type": "array"},
                "required_permissions": [],
            }],
        ),
    ],
)
def test_manifest_directory_fails_closed_for_invalid_tool_contracts(
    tmp_path: Path,
    manifest_name: str,
    payloads: list[object],
):
    from app.services.managed_tool_runtime import managed_tool_runtime

    manifest_dir = tmp_path / "managed-tools"
    manifest_dir.mkdir()
    for index, payload in enumerate(payloads):
        path = manifest_dir / f"{index}-{manifest_name}"
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            _write_tool_manifest(path, payload)

    with pytest.raises(ValueError):
        managed_tool_runtime(manifest_dir=manifest_dir)


def test_empty_managed_tool_registry_hides_tool_capability_and_palette():
    from app.services.workflow_handler_registry import (
        workflow_handler_capability_snapshot,
    )
    from app.services.workflow_node_registry import node_registry_payload

    capabilities = workflow_handler_capability_snapshot()

    assert "tool" not in capabilities["handlers"]
    assert "tool" not in {
        str(node["kind"]) for node in node_registry_payload()["nodes"]
    }


def test_managed_runtime_validates_frozen_tool_plan_before_execution(monkeypatch):
    from app.services import managed_tool_runtime
    from app.services.workflow_handler_registry import (
        workflow_handler_capability_snapshot,
    )

    monkeypatch.setattr(
        managed_tool_runtime,
        "MANAGED_TOOL_REGISTRY",
        {"text.preview": _preview_tool()},
    )

    runtime = managed_tool_runtime.managed_tool_runtime()
    capabilities = workflow_handler_capability_snapshot()

    assert capabilities["handlers"]["tool"]["tool_ids"] == ["text.preview"]
    assert runtime.validate_frozen_plan_nodes([
        {
            "node_id": "preview",
            "kind": "tool",
            "tool_id": "text.preview",
            "required_permissions": ["workspace.read"],
        }
    ]) == []
    errors = runtime.validate_frozen_plan_nodes([
        {
            "node_id": "unknown",
            "kind": "tool",
            "tool_id": "missing.tool",
            "required_permissions": [],
        },
        {
            "node_id": "overprivileged",
            "kind": "tool",
            "tool_id": "text.preview",
            "required_permissions": ["workspace.write"],
        },
    ])

    assert errors == [
        {
            "node_id": "unknown",
            "code": "tool_not_registered",
            "message": "Frozen workflow references an unmanaged tool.",
            "tool_id": "missing.tool",
        },
        {
            "node_id": "overprivileged",
            "code": "tool_permissions_invalid",
            "message": "Frozen workflow tool permissions do not match the managed contract.",
            "tool_id": "text.preview",
            "expected_permissions": ["workspace.read"],
            "requested_permissions": ["workspace.write"],
        },
    ]


def test_api_composition_injects_one_managed_runtime_into_runner(monkeypatch):
    from app.api import agent_workbench
    from app.services import managed_tool_runtime
    from app.services.workbench_workflow_runner import WorkbenchWorkflowExecutionResult

    monkeypatch.setattr(
        managed_tool_runtime,
        "MANAGED_TOOL_REGISTRY",
        {"text.preview": _preview_tool()},
    )
    runtime = managed_tool_runtime.managed_tool_runtime()
    monkeypatch.setattr(
        managed_tool_runtime,
        "managed_tool_runtime",
        lambda: runtime,
    )
    captured: dict[str, object] = {}

    class FakeEventStore:
        def __init__(self, *_args):
            pass

        def append(self, *_args, **_kwargs):
            return {}

        def current_status(self, _task_run_id):
            return "running"

        def mark_v3_outcomes(self, _task_run_id, **kwargs):
            captured["outcomes"] = kwargs

    class FakeStore:
        def __init__(self, *_args):
            pass

        def load(self, _task_run_id):
            return SimpleNamespace(task_run_id="run-1")

    class FakeRunner:
        def __init__(self, _root, **kwargs):
            captured.update(kwargs)

        def execute_task_run(self, _task_run_id, **_kwargs):
            return WorkbenchWorkflowExecutionResult(
                task_run_id="run-1",
                status="completed",
                started_at="2026-07-28T00:00:00+00:00",
                completed_at="2026-07-28T00:00:01+00:00",
                execution_status="completed",
                artifact_validation_status="not_requested",
                governance_status="not_requested",
                delivery_status="ready",
                legacy_delivery_status="complete",
                quality_status="passed",
                compiled_contract_version=3,
            )

    monkeypatch.setattr(agent_workbench, "WorkbenchTaskRunEventStore", FakeEventStore)
    monkeypatch.setattr(agent_workbench, "WorkbenchTaskRunStore", FakeStore)
    monkeypatch.setattr(agent_workbench, "WorkbenchWorkflowRunner", FakeRunner)

    response = agent_workbench._execute_task_run_with_closure(
        task_run_id="run-1",
        payload=agent_workbench.TaskRunExecuteRequest(),
    )

    assert response == asdict(FakeRunner(None).execute_task_run("run-1"))
    assert captured["tool_dispatcher"] is runtime.dispatcher
    assert captured["granted_tool_permissions"] == runtime.granted_permissions


def test_runner_passes_its_managed_runtime_to_provider_facade(tmp_path: Path, monkeypatch):
    from app.services import managed_tool_runtime
    from app.services.provider_adapters.contracts import ProviderCapabilities, ProviderSession
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
    import app.services.workbench_workflow_runner as runner_module

    class Adapter:
        def capabilities(self):
            return ProviderCapabilities(
                streaming=True,
                tool_call=True,
                session_resume=False,
                structured_output=False,
                mcp=False,
                skills=False,
                cancellation=False,
            )

        def prepare(self, request):
            return ProviderSession(
                session_id="provider-session",
                provider=request.provider,
                artifact_dir=str(tmp_path),
            )

    monkeypatch.setattr(
        managed_tool_runtime,
        "MANAGED_TOOL_REGISTRY",
        {"text.preview": _preview_tool()},
    )
    monkeypatch.setattr(runner_module, "create_provider_adapter", lambda **_kwargs: Adapter())
    runtime = managed_tool_runtime.managed_tool_runtime()
    runner = WorkbenchWorkflowRunner(
        tmp_path,
        tool_dispatcher=runtime.dispatcher,
        granted_tool_permissions=runtime.granted_permissions,
    )

    facade, _session_id, missing = runner._prepare_provider_facade_for_step(
        step={"id": "agent", "type": "agent_task"},
        agent_run={
            "step_id": "agent",
            "run_id": "provider-session",
            "provider": "managed-provider",
            "artifact_dir": str(tmp_path),
        },
        artifact_dir=tmp_path,
        run_payload={},
        run_id="provider-session",
        timeout_sec=1,
        idle_timeout_sec=None,
    )

    assert missing == []
    assert facade._tool_dispatcher is runtime.dispatcher
    assert facade._granted_tool_permissions == runtime.granted_permissions
