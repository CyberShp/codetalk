"""V3 authoring and compilation contracts for Phase 6 workflow nodes."""

from __future__ import annotations


def _capabilities() -> dict:
    return {
        "handlers": {
            "tool": {"versions": [1], "kind": "tool"},
            "human_approval": {"versions": [1], "kind": "human_approval"},
            "subagent": {"versions": [1], "kind": "subagent"},
        },
        "providers": {
            "provider_codex_default": {
                "available": True,
                "capabilities": ["cancellation", "streaming"],
            },
        },
    }


def _graph() -> dict:
    return {
        "schema_version": 3,
        "workflow_id": "phase6-explicit-nodes",
        "name": "Explicit Phase 6 nodes",
        "description": "",
        "settings": {
            "validation_profile": "none",
            "stop_on_error": True,
            "max_parallelism": 1,
        },
        "nodes": [
            {
                "id": "request",
                "kind": "input",
                "label": "Request",
                "position": {"x": 0, "y": 0},
                "ports": {"inputs": [], "outputs": [{"id": "value", "type": "structured_json"}]},
                "config": {"input_id": "request", "type": "structured_json", "required": True},
            },
            {
                "id": "local-tool",
                "kind": "tool",
                "label": "Local tool",
                "position": {"x": 240, "y": 0},
                "ports": {
                    "inputs": [{"id": "arguments", "type": "structured_json", "required": True}],
                    "outputs": [{"id": "result", "type": "structured_json", "required": True}],
                },
                "config": {
                    "handler_id": "tool",
                    "handler_version": 1,
                    "tool_id": "workspace.inspect",
                    "required_permissions": ["workspace.read"],
                    "timeout_sec": 60,
                    "idle_timeout_sec": 0,
                    "retry_policy": {"max_attempts": 1, "backoff_seconds": 0},
                    "failure_policy": "stop",
                },
            },
            {
                "id": "research-child",
                "kind": "subagent",
                "label": "Research child",
                "position": {"x": 480, "y": 0},
                "ports": {
                    "inputs": [{"id": "context", "type": "structured_json", "required": True}],
                    "outputs": [{"id": "result", "type": "artifact", "required": True}],
                },
                "config": {
                    "handler_id": "subagent",
                    "handler_version": 1,
                    "session_key": "research",
                    "provider_ref": "provider_codex_default",
                    "provider_capabilities_required": ["streaming", "cancellation"],
                    "goal": "Research only the supplied tool result.",
                    "timeout_sec": 300,
                    "idle_timeout_sec": 30,
                    "retry_policy": {"max_attempts": 2, "backoff_seconds": 1},
                    "failure_policy": "continue_independent",
                },
            },
            {
                "id": "approve-report",
                "kind": "human_approval",
                "label": "Approve report",
                "position": {"x": 720, "y": 0},
                "ports": {
                    "inputs": [{
                        "id": "context",
                        "binding_key": "context",
                        "type": "any",
                        "required": True,
                    }],
                    "outputs": [],
                },
                "config": {
                    "handler_id": "human_approval",
                    "handler_version": 1,
                    "approval_timeout_sec": 3600,
                    "timeout_sec": 60,
                    "idle_timeout_sec": 0,
                    "retry_policy": {"max_attempts": 1, "backoff_seconds": 0},
                    "failure_policy": "stop",
                },
            },
        ],
        "edges": [
            {
                "id": "request-to-tool",
                "kind": "data",
                "source": {"node_id": "request", "port_id": "value"},
                "target": {"node_id": "local-tool", "port_id": "arguments"},
            },
            {
                "id": "tool-to-subagent",
                "kind": "data",
                "source": {"node_id": "local-tool", "port_id": "result"},
                "target": {"node_id": "research-child", "port_id": "context"},
            },
            {
                "id": "subagent-to-approval",
                "kind": "data",
                "source": {"node_id": "research-child", "port_id": "result"},
                "target": {"node_id": "approve-report", "port_id": "context"},
            },
        ],
    }


def test_v3_registry_and_compiler_freeze_explicit_phase6_nodes():
    from app.services.workflow_contract_v3 import compile_workflow_contract_v3
    from app.services.workflow_node_registry import node_registry_payload

    registry = node_registry_payload(schema_version=3, capabilities=_capabilities())
    by_kind = {node["kind"]: node for node in registry["nodes"]}
    assert {"tool", "human_approval", "subagent"} <= set(by_kind)
    assert by_kind["tool"]["execution"] == {
        "available": True,
        "handler_id": "tool",
        "handler_version": 1,
    }
    assert by_kind["human_approval"]["execution"] == {
        "available": True,
        "handler_id": "human_approval",
        "handler_version": 1,
    }
    assert by_kind["subagent"]["execution"] == {
        "available": True,
        "handler_id": "subagent",
        "handler_version": 1,
    }

    compiled = compile_workflow_contract_v3(
        _graph(), capabilities=_capabilities(), workflow_version_id="wfv-phase6"
    )

    assert compiled["validation_result"] == {"valid": True, "errors": [], "warnings": []}
    assert compiled["compiled_definition"] is not None
    assert compiled["compiled_plan"] is not None
    assert compiled["compiled_plan"]["topological_order"] == [
        "local-tool",
        "research-child",
        "approve-report",
    ]
    assert [node["node_id"] for node in compiled["compiled_definition"]["nodes"]] == [
        "local-tool",
        "research-child",
        "approve-report",
    ]
    assert [node["label"] for node in compiled["compiled_plan"]["nodes"]] == [
        "Local tool",
        "Research child",
        "Approve report",
    ]
    assert compiled["compiled_definition"]["validators"] == []

    tool = compiled["compiled_plan"]["nodes"][0]
    assert tool["handler_id"] == "tool"
    assert tool["handler_version"] == 1
    assert tool["tool_id"] == "workspace.inspect"
    assert tool["required_permissions"] == ["workspace.read"]
    assert tool["input_ports"] == [{"id": "arguments", "type": "structured_json", "required": True}]
    assert tool["output_ports"] == [{"id": "result", "type": "structured_json", "required": True}]
    assert tool["timeout_sec"] == 60
    assert tool["failure_policy"] == "stop"

    subagent = compiled["compiled_plan"]["nodes"][1]
    assert subagent["handler_id"] == "subagent"
    assert subagent["handler_version"] == 1
    assert subagent["session_key"] == "research"
    assert subagent["provider_ref"] == "provider_codex_default"
    assert subagent["provider_capabilities_required"] == ["cancellation", "streaming"]
    assert subagent["timeout_sec"] == 300
    assert subagent["idle_timeout_sec"] == 30
    assert subagent["retry_policy"] == {"max_attempts": 2, "backoff_seconds": 1}
    assert subagent["failure_policy"] == "continue_independent"
    assert subagent["resolved_input_bindings"] == {
        "context": {"source_node_id": "local-tool", "source_port_id": "result"}
    }
    # Keep the semantic kind while projecting through the existing provider
    # preparation path. The preparer creates agent_runs exclusively for this type.
    assert subagent["kind"] == "subagent"
    assert subagent["type"] == "agent_task"
    runtime_subagent = next(
        step
        for step in compiled["compiled_definition"]["steps"]
        if step["id"] == "research-child"
    )
    assert runtime_subagent["type"] == "agent_task"
    assert runtime_subagent["provider"] == "provider_codex_default"

    approval = compiled["compiled_plan"]["nodes"][2]
    assert approval["handler_id"] == "human_approval"
    assert approval["handler_version"] == 1
    assert approval["approval_timeout_sec"] == 3600
    assert approval["timeout_sec"] == 60
    assert approval["failure_policy"] == "stop"
    assert approval["depends_on"] == ["research-child"]
    assert approval["resolved_input_bindings"] == {
        "context": {"source_node_id": "research-child", "source_port_id": "result"}
    }


def test_default_handler_snapshot_exposes_implemented_phase6_nodes():
    from app.services.workflow_handler_registry import (
        workflow_handler_capability_snapshot,
    )
    from app.services.workflow_node_registry import (
        SUPPORTED_NODE_KINDS,
        node_registry_payload,
    )

    capabilities = workflow_handler_capability_snapshot()
    assert {
        handler_id: capabilities["handlers"][handler_id]
        for handler_id in ("human_approval", "subagent")
    } == {
        "human_approval": {"versions": [1], "kind": "human_approval"},
        "subagent": {"versions": [1], "kind": "subagent"},
    }
    assert "tool" not in capabilities["handlers"]
    registry = node_registry_payload(schema_version=3, capabilities=capabilities)
    assert {"human_approval", "subagent"} <= {
        node["kind"] for node in registry["nodes"]
    }
    assert "tool" not in {node["kind"] for node in registry["nodes"]}
    assert {"tool", "human_approval", "subagent"} <= SUPPORTED_NODE_KINDS


def test_authoring_factory_builds_explicit_phase6_nodes_with_server_owned_ids():
    from app.services.workflow_authoring_factory import (
        CanvasAuthoringError,
        assert_handler_port_mutation_allowed,
        build_v3_node,
    )

    approval = build_v3_node(
        "human_approval",
        label="Operator approval",
        config={"approval_timeout_sec": 3600},
    )
    assert approval["id"].startswith("node_")
    assert len(approval["ports"]["inputs"]) == 1
    assert approval["ports"]["outputs"] == []
    assert approval["ports"]["inputs"][0]["binding_key"] == "context"
    assert approval["ports"]["inputs"][0]["type"] == "any"
    assert approval["ports"]["inputs"][0]["required"] is True
    assert approval["config"]["handler_id"] == "human_approval"
    assert approval["config"]["handler_version"] == 1
    assert approval["config"]["approval_timeout_sec"] == 3600

    child = build_v3_node(
        "subagent",
        label="Research child",
        config={"session_key": "research", "provider_ref": "provider_codex_default"},
    )
    assert child["id"].startswith("node_")
    assert child["config"]["handler_id"] == "subagent"
    assert child["config"]["session_key"] == "research"
    assert child["config"]["provider_ref"] == "provider_codex_default"
    assert len(child["ports"]["inputs"]) == 1
    assert len(child["ports"]["outputs"]) == 1
    assert child["ports"]["inputs"][0]["id"].startswith("port_")
    assert child["ports"]["outputs"][0]["id"].startswith("port_")
    assert child["ports"]["inputs"][0]["binding_key"] == "context"
    assert child["ports"]["outputs"][0]["binding_key"] == "result"
    try:
        assert_handler_port_mutation_allowed(child)
    except CanvasAuthoringError as exc:
        assert str(exc) == "handler_port_contract_immutable"
    else:
        raise AssertionError("subagent handler ports must remain server-owned")
