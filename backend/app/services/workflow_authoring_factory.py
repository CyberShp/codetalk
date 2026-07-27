"""Server-owned construction commands for Canvas First V3 workflows.

The browser owns labels, placement and business configuration.  It never owns
technical identifiers: those are generated once here and persisted with the
draft so concurrent tabs cannot manufacture competing graph identities.
"""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4


CANVAS_TEMPLATES = frozenset({"blank", "free_source_analysis"})
TECHNICAL_ID_FIELDS = frozenset(
    {
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
    }
)


class CanvasAuthoringError(ValueError):
    """A client attempted an invalid V3 authoring command."""


def new_workflow_id() -> str:
    return _identifier("wf")


@lru_cache(maxsize=1)
def backend_commit_sha() -> str:
    """Return the deployed revision without making any network request."""
    configured = os.environ.get("CODETALK_BUILD_COMMIT", "").strip()
    if configured:
        return configured
    root = Path(__file__).resolve().parents[3]
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            check=False,
            text=True,
            timeout=0.5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    revision = result.stdout.strip()
    return revision if result.returncode == 0 and revision else "unknown"


def new_identity(kind: str) -> str:
    return _identifier(kind)


def build_canvas_graph(
    *, workflow_id: str, name: str, description: str, template: str
) -> dict[str, Any]:
    if template not in CANVAS_TEMPLATES:
        raise CanvasAuthoringError("unsupported_canvas_template")
    graph: dict[str, Any] = {
        "schema_version": 3,
        "workflow_id": workflow_id,
        "name": name,
        "description": description,
        "nodes": [],
        "edges": [],
        "settings": {
            "validation_profile": "artifact_only",
            "stop_on_error": True,
            "max_parallelism": 1,
        },
    }
    if template == "blank":
        return graph

    source = build_v3_node(
        "input",
        label="源码工作区",
        position={"x": 80, "y": 220},
        config={
            "type": "directory",
            "required": True,
            "resolver": "workspace",
            "role": "选择已创建的 CodeTalk 工作空间作为本次分析的源码输入。",
        },
    )
    agent = build_v3_node(
        "agent",
        label="源码分析",
        position={"x": 420, "y": 220},
        config={
            "goal": "读取所选工作空间源码，基于真实文件证据完成分析，并且只生成已声明的报告。",
            "provider_ref": "builtin-llm",
            "skill_ids": ["source-evidence-first"],
        },
    )
    output = build_v3_node(
        "output",
        label="分析报告",
        position={"x": 760, "y": 220},
        config={
            "artifact": "report.md",
            "media_type": "text/markdown",
            "required": True,
        },
    )
    graph["nodes"] = [source, agent, output]
    graph["edges"] = [
        build_v3_edge(
            source_node_id=source["id"],
            source_port_id=source["ports"]["outputs"][0]["id"],
            target_node_id=agent["id"],
            target_port_id=agent["ports"]["inputs"][0]["id"],
        ),
        build_v3_edge(
            source_node_id=agent["id"],
            source_port_id=agent["ports"]["outputs"][0]["id"],
            target_node_id=output["id"],
            target_port_id=output["ports"]["inputs"][0]["id"],
        ),
    ]
    return graph


def build_v3_node(
    kind: str,
    *,
    label: str | None = None,
    position: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create one executable Phase 3 node with all technical IDs allocated."""
    requested = dict(config or {})
    _reject_technical_fields(requested)
    from app.services.workflow_node_registry import executable_node_definition

    definition = executable_node_definition(kind)
    if definition is None:
        raise CanvasAuthoringError(f"node_kind_not_executable:{kind}")
    node_id = new_identity("node")
    safe_position = _position(position)
    if kind == "input":
        input_type = str(requested.pop("type", "text"))
        resolver = str(requested.pop("resolver", "manual"))
        node_label = label or str(requested.pop("label", "Input"))
        return {
            "id": node_id,
            "kind": "input",
            "label": node_label,
            "position": safe_position,
            "ports": {
                "inputs": [],
                "outputs": [_port("value", input_type)],
            },
            "config": {
                "input_id": new_identity("input"),
                "type": input_type,
                "required": bool(requested.pop("required", False)),
                "resolver": resolver,
                "role": str(requested.pop("role", "")),
                **requested,
            },
        }
    if kind == "output":
        media_type = str(requested.pop("media_type", "text/markdown"))
        node_label = label or str(requested.pop("label", "Output"))
        return {
            "id": node_id,
            "kind": "output",
            "label": node_label,
            "position": safe_position,
            "ports": {
                "inputs": [_port("value", "markdown", required=True)],
                "outputs": [],
            },
            "config": {
                "output_id": new_identity("output"),
                "artifact": str(requested.pop("artifact", "output.md")),
                "media_type": media_type,
                "required": bool(requested.pop("required", True)),
                **requested,
            },
        }
    if kind == "agent":
        execution = (
            definition.get("execution")
            if isinstance(definition.get("execution"), dict)
            else {}
        )
        handler_id = str(execution.get("handler_id") or "").strip()
        handler_version = execution.get("handler_version")
        if not handler_id or isinstance(handler_version, bool) or not isinstance(handler_version, int):
            raise CanvasAuthoringError(f"node_handler_unavailable:{kind}")
        node_label = label or str(requested.pop("label", "Agent"))
        return {
            "id": node_id,
            "kind": "agent",
            "label": node_label,
            "position": safe_position,
            "ports": {
                "inputs": [_port("repo_path", "directory", required=True)],
                "outputs": [_port("report", "markdown", required=True)],
            },
            "config": {
                "handler_id": handler_id,
                "handler_version": handler_version,
                "provider_ref": str(requested.pop("provider_ref", "builtin-llm")),
                "provider_capabilities_required": list(
                    requested.pop("provider_capabilities_required", [])
                ),
                "mcp_profiles": list(requested.pop("mcp_profiles", [])),
                "skill_ids": list(requested.pop("skill_ids", [])),
                "skill_instructions": list(requested.pop("skill_instructions", [])),
                "goal": str(requested.pop("goal", "Describe the requested result.")),
                "prompt_template_version": 1,
                "prompt_template": "{{node_goal}}\n{{bound_inputs}}\n{{output_contract}}",
                "input_rendering": {
                    "preserve_user_text_verbatim": True,
                    "binding_order": [],
                },
                "timeout_sec": int(requested.pop("timeout_sec", 900)),
                "idle_timeout_sec": int(requested.pop("idle_timeout_sec", 120)),
                "retry_policy": dict(
                    requested.pop("retry_policy", {"max_attempts": 1, "backoff_seconds": 0})
                ),
                "failure_policy": str(requested.pop("failure_policy", "stop")),
                **requested,
            },
        }
    raise CanvasAuthoringError(f"node_kind_not_executable:{kind}")


def build_v3_port(
    *, label: str, port_type: str, required: bool = False, collection: bool = False
) -> dict[str, Any]:
    return _port(label, port_type, required=required, collection=collection)


def build_v3_edge(
    *, source_node_id: str, source_port_id: str, target_node_id: str, target_port_id: str
) -> dict[str, Any]:
    return {
        "id": new_identity("edge"),
        "kind": "data",
        "source": {"node_id": source_node_id, "port_id": source_port_id},
        "target": {"node_id": target_node_id, "port_id": target_port_id},
    }


def assert_v3_technical_ids_preserved(
    existing: dict[str, Any], candidate: dict[str, Any]
) -> None:
    """Reject raw V3 PUTs that add or mutate server-owned identities."""
    if existing.get("schema_version") != 3 or candidate.get("schema_version") != 3:
        raise CanvasAuthoringError("schema_version_immutable")
    if str(candidate.get("workflow_id") or "") != str(existing.get("workflow_id") or ""):
        raise CanvasAuthoringError("workflow_id_immutable")
    _assert_owned_fields_equal(existing, candidate, TECHNICAL_ID_FIELDS)
    old_nodes = _by_id(existing.get("nodes"), "node")
    new_nodes = _by_id(candidate.get("nodes"), "node")
    if set(new_nodes) - set(old_nodes):
        raise CanvasAuthoringError("v3_new_nodes_require_command")
    for node_id in set(old_nodes) & set(new_nodes):
        old_node, new_node = old_nodes[node_id], new_nodes[node_id]
        if str(old_node.get("kind") or "") != str(new_node.get("kind") or ""):
            raise CanvasAuthoringError("node_kind_immutable")
        _assert_owned_fields_equal(old_node, new_node, TECHNICAL_ID_FIELDS)
        for direction in ("inputs", "outputs"):
            old_ports = _by_id((old_node.get("ports") or {}).get(direction), "port")
            new_ports = _by_id((new_node.get("ports") or {}).get(direction), "port")
            if set(new_ports) - set(old_ports):
                raise CanvasAuthoringError("v3_new_ports_require_command")
            for port_id in set(old_ports) & set(new_ports):
                _assert_owned_fields_equal(
                    old_ports[port_id], new_ports[port_id], TECHNICAL_ID_FIELDS
                )
                if str(old_ports[port_id].get("type") or "") != str(new_ports[port_id].get("type") or ""):
                    raise CanvasAuthoringError("port_type_immutable")
        for field in TECHNICAL_ID_FIELDS:
            _assert_config_identity(old_node, new_node, field)
    old_edges = _by_id(existing.get("edges"), "edge")
    new_edges = _by_id(candidate.get("edges"), "edge")
    if set(new_edges) - set(old_edges):
        raise CanvasAuthoringError("v3_new_edges_require_command")
    for edge_id in set(old_edges) & set(new_edges):
        _assert_owned_fields_equal(
            old_edges[edge_id], new_edges[edge_id], TECHNICAL_ID_FIELDS
        )
        for endpoint in ("source", "target"):
            if old_edges[edge_id].get(endpoint) != new_edges[edge_id].get(endpoint):
                raise CanvasAuthoringError("edge_endpoints_immutable")


def migrate_legacy_graph_to_v3(
    source: dict[str, Any], *, workflow_id: str, name: str, description: str
) -> dict[str, Any]:
    """Make an explicit, distinct V3 copy without mutating historical JSON.

    The migration deliberately allocates fresh IDs.  It preserves the useful
    business labels/configuration of input, agent and output nodes while only
    materialising the executable Phase 3 node set.
    """
    graph = build_canvas_graph(
        workflow_id=workflow_id, name=name, description=description, template="blank"
    )
    old_nodes = [item for item in source.get("nodes") or [] if isinstance(item, dict)]
    node_map: dict[str, dict[str, Any]] = {}
    port_map: dict[tuple[str, str], str] = {}
    for old in old_nodes:
        kind = str(old.get("kind") or "")
        if kind not in {"input", "agent", "output"}:
            continue
        config = old.get("config") if isinstance(old.get("config"), dict) else {}
        business = _business_config(config)
        if kind == "agent" and "provider" in business:
            business["provider_ref"] = business.pop("provider")
        node = build_v3_node(
            kind,
            label=str(old.get("label") or config.get("label") or kind.title()),
            position=old.get("position") if isinstance(old.get("position"), dict) else None,
            config=business,
        )
        if kind == "agent":
            _migrate_agent_ports(node, config)
        graph["nodes"].append(node)
        old_id = str(old.get("id") or "")
        node_map[old_id] = node
        if kind == "input":
            port_map[(old_id, "value")] = node["ports"]["outputs"][0]["id"]
        elif kind == "output":
            port_map[(old_id, "value")] = node["ports"]["inputs"][0]["id"]
        else:
            for direction in ("inputs", "outputs"):
                for old_port, new_port in zip(
                    config.get(f"{direction[:-1]}_ports", []) or [],
                    node["ports"][direction],
                ):
                    if isinstance(old_port, dict) and str(old_port.get("id") or ""):
                        port_map[(old_id, str(old_port["id"]))] = new_port["id"]
    for old_edge in source.get("edges") or []:
        if not isinstance(old_edge, dict) or old_edge.get("kind", "data") != "data":
            continue
        source_ref = old_edge.get("source") if isinstance(old_edge.get("source"), dict) else {}
        target_ref = old_edge.get("target") if isinstance(old_edge.get("target"), dict) else {}
        source_id = str(source_ref.get("node_id") or "")
        target_id = str(target_ref.get("node_id") or "")
        source_node, target_node = node_map.get(source_id), node_map.get(target_id)
        if not source_node or not target_node:
            continue
        source_port = port_map.get((source_id, str(source_ref.get("port_id") or "")))
        target_port = port_map.get((target_id, str(target_ref.get("port_id") or "")))
        if source_port and target_port:
            graph["edges"].append(
                build_v3_edge(
                    source_node_id=source_node["id"],
                    source_port_id=source_port,
                    target_node_id=target_node["id"],
                    target_port_id=target_port,
                )
            )
    return graph


def _identifier(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _port(label: str, port_type: str, *, required: bool = False, collection: bool = False) -> dict[str, Any]:
    return {
        "id": new_identity("port"),
        "label": label,
        "type": port_type,
        "required": required,
        "collection": collection,
    }


def _position(value: dict[str, Any] | None) -> dict[str, int]:
    value = value if isinstance(value, dict) else {}
    return {"x": int(value.get("x", 0)), "y": int(value.get("y", 0))}


def _reject_technical_fields(value: dict[str, Any]) -> None:
    found = sorted(TECHNICAL_ID_FIELDS & set(value))
    if found:
        raise CanvasAuthoringError("client_technical_ids_forbidden:" + ",".join(found))


def _by_id(value: Any, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        raise CanvasAuthoringError(f"{label}_collection_invalid")
    result: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict) or not str(item.get("id") or ""):
            raise CanvasAuthoringError(f"{label}_id_invalid")
        item_id = str(item["id"])
        if item_id in result:
            raise CanvasAuthoringError(f"duplicate_{label}_id")
        result[item_id] = item
    return result


def _assert_config_identity(old_node: dict[str, Any], new_node: dict[str, Any], field: str) -> None:
    old_config = old_node.get("config") if isinstance(old_node.get("config"), dict) else {}
    new_config = new_node.get("config") if isinstance(new_node.get("config"), dict) else {}
    if (field in old_config or field in new_config) and old_config.get(field) != new_config.get(field):
        raise CanvasAuthoringError(f"{field}_immutable")


def _assert_owned_fields_equal(
    existing: dict[str, Any], candidate: dict[str, Any], fields: frozenset[str]
) -> None:
    for field in fields:
        if (field in existing or field in candidate) and existing.get(field) != candidate.get(field):
            raise CanvasAuthoringError(f"{field}_immutable")


def _business_config(config: dict[str, Any]) -> dict[str, Any]:
    ignored = TECHNICAL_ID_FIELDS | {"input_ports", "output_ports"}
    return {key: value for key, value in config.items() if key not in ignored}


def _migrate_agent_ports(node: dict[str, Any], config: dict[str, Any]) -> None:
    input_ports = config.get("input_ports") if isinstance(config.get("input_ports"), list) else []
    output_ports = config.get("output_ports") if isinstance(config.get("output_ports"), list) else []
    if input_ports:
        node["ports"]["inputs"] = [
            _port(str(item.get("label") or item.get("id") or "Input"), str(item.get("type") or "text"), required=bool(item.get("required")), collection=bool(item.get("collection")))
            for item in input_ports if isinstance(item, dict)
        ]
    if output_ports:
        node["ports"]["outputs"] = [
            _port(str(item.get("label") or item.get("id") or "Output"), str(item.get("type") or "artifact"), required=bool(item.get("required")), collection=bool(item.get("collection")))
            for item in output_ports if isinstance(item, dict)
        ]
