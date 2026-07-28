"""Capability snapshot shared by V3 compilation and runtime dispatch."""

from __future__ import annotations

from typing import Any

from app.config import settings


def workflow_handler_capability_snapshot() -> dict[str, Any]:
    """Return a detached compiler capability snapshot.

    A returned copy prevents a draft validation from mutating the process-wide
    registry and keeps publish and trial-run decisions on the exact same data.
    """
    from app.services.governance_plugins.registry import (
        governance_handler_availability_snapshot,
    )
    from app.services.validators import DEFAULT_VALIDATOR_REGISTRY

    from app.services.managed_tool_runtime import managed_tool_runtime

    handlers: dict[str, dict[str, Any]] = {
        "agent": {"versions": [1], "kind": "agent"},
    }
    if settings.workflow_hitl_enabled:
        handlers["human_approval"] = {
            "versions": [1],
            "kind": "human_approval",
        }
    if settings.workflow_subagent_enabled:
        handlers["subagent"] = {"versions": [1], "kind": "subagent"}
    if settings.workflow_tool_enabled:
        tool_capability = managed_tool_runtime().handler_capability()
        if tool_capability is not None:
            handlers["tool"] = tool_capability
    handlers.update(
        {
            validator_id: {"versions": [1], "kind": "validator"}
            for validator_id in DEFAULT_VALIDATOR_REGISTRY.ids()
        }
    )
    for item in governance_handler_availability_snapshot():
        if not item.get("available"):
            continue
        handlers[str(item["handler_id"])] = {
            "versions": [int(item["handler_version"])],
            "kind": str(item["node_kind"]),
            **(
                {"input_ports": [dict(port) for port in item["input_ports"]]}
                if item.get("input_ports")
                else {}
            ),
            **(
                {"output_ports": [dict(port) for port in item["output_ports"]]}
                if item.get("output_ports")
                else {}
            ),
        }
    return {"handlers": handlers}
