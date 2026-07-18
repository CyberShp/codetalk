"""Authoring Graph V2 validation and deterministic compilation."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any


GRAPH_SCHEMA_VERSION = 2
PLAN_VERSION = 1
SUPPORTED_NODE_KINDS = frozenset({
    "input",
    "output",
    "agent",
    "semantic_retrieve",
    "memory_retrieve",
    "local_scope_discover",
    "evidence_validate",
    "report_render",
    "artifact_export",
})
EXECUTION_NODE_KINDS = SUPPORTED_NODE_KINDS - {"input", "output"}
SUPPORTED_EDGE_KINDS = frozenset({"data", "dependency"})
SUPPORTED_RESOLVERS = frozenset({"manual", "workspace", "local", "agent_mcp"})
SUPPORTED_FAILURE_POLICIES = frozenset({"stop", "continue_independent"})
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class WorkflowGraphValidationError(ValueError):
    def __init__(self, validation: dict[str, Any]) -> None:
        self.validation = validation
        message = "; ".join(
            str(item.get("message") or item.get("code") or "invalid graph")
            for item in validation.get("errors") or []
        )
        super().__init__(message or "invalid workflow graph")


def validate_workflow_graph(
    graph: dict[str, Any], *, capabilities: dict[str, Any] | None = None
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not isinstance(graph, dict):
        return _validation_result([_issue("graph_not_object", "Authoring graph must be an object")], [])
    if graph.get("schema_version") != GRAPH_SCHEMA_VERSION:
        errors.append(_issue("schema_version_invalid", "Authoring graph schema_version must be 2", field="schema_version"))
    workflow_id = str(graph.get("workflow_id") or "").strip()
    if not _SAFE_ID.fullmatch(workflow_id):
        errors.append(_issue("workflow_id_invalid", "Workflow ID contains unsafe characters", field="workflow_id"))
    if not str(graph.get("name") or "").strip():
        errors.append(_issue("workflow_name_missing", "Workflow name is required", field="name"))

    raw_nodes = graph.get("nodes")
    raw_edges = graph.get("edges")
    if not isinstance(raw_nodes, list):
        errors.append(_issue("nodes_not_array", "nodes must be an array", field="nodes"))
        raw_nodes = []
    if not isinstance(raw_edges, list):
        errors.append(_issue("edges_not_array", "edges must be an array", field="edges"))
        raw_edges = []

    nodes: dict[str, dict[str, Any]] = {}
    node_ids_seen: set[str] = set()
    step_ids_seen: set[str] = set()
    input_contracts_seen: set[str] = set()
    output_contracts_seen: set[str] = set()
    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict):
            errors.append(_issue("node_not_object", "Each node must be an object"))
            continue
        node_id = str(raw_node.get("id") or "").strip()
        if not _SAFE_ID.fullmatch(node_id):
            errors.append(_issue("node_id_invalid", "Node ID contains unsafe characters", node_id=node_id or None, field="id"))
            continue
        if node_id in node_ids_seen:
            errors.append(_issue("duplicate_node_id", f"Duplicate node ID: {node_id}", node_id=node_id, field="id"))
            continue
        node_ids_seen.add(node_id)
        nodes[node_id] = raw_node
        kind = str(raw_node.get("kind") or "")
        if kind not in SUPPORTED_NODE_KINDS:
            errors.append(_issue("node_kind_unsupported", f"Unsupported node kind: {kind}", node_id=node_id, field="kind"))
            continue
        config = _config(raw_node)
        if kind == "input":
            contract_id = str(config.get("contract_id") or node_id).strip()
            if not _SAFE_ID.fullmatch(contract_id) or contract_id in input_contracts_seen:
                errors.append(_issue("input_contract_id_invalid", f"Input contract ID is invalid or duplicated: {contract_id}", node_id=node_id, field="contract_id"))
            input_contracts_seen.add(contract_id)
            _validate_input_node(node_id, config, errors)
        elif kind == "output":
            output_id = str(config.get("output_id") or node_id).strip()
            if not _SAFE_ID.fullmatch(output_id) or output_id in output_contracts_seen:
                errors.append(_issue("output_contract_id_invalid", f"Output contract ID is invalid or duplicated: {output_id}", node_id=node_id, field="output_id"))
            output_contracts_seen.add(output_id)
        else:
            step_id = str(config.get("step_id") or node_id).strip()
            if not _SAFE_ID.fullmatch(step_id) or step_id in step_ids_seen:
                errors.append(_issue("step_id_invalid", f"Execution step ID is invalid or duplicated: {step_id}", node_id=node_id, field="step_id"))
            step_ids_seen.add(step_id)
            _validate_execution_node(node_id, kind, config, capabilities or {}, errors, warnings)

    edge_ids: set[str] = set()
    valid_edges: list[dict[str, Any]] = []
    degree: dict[str, int] = defaultdict(int)
    scalar_input_bindings: dict[tuple[str, str], str] = {}
    for raw_edge in raw_edges:
        if not isinstance(raw_edge, dict):
            errors.append(_issue("edge_not_object", "Each edge must be an object"))
            continue
        edge_id = str(raw_edge.get("id") or "").strip()
        if not _SAFE_ID.fullmatch(edge_id):
            errors.append(_issue("edge_id_invalid", "Edge ID contains unsafe characters", field="id"))
            continue
        if edge_id in edge_ids:
            errors.append(_issue("duplicate_edge_id", f"Duplicate edge ID: {edge_id}", field="id"))
            continue
        edge_ids.add(edge_id)
        kind = str(raw_edge.get("kind") or "")
        if kind not in SUPPORTED_EDGE_KINDS:
            errors.append(_issue("edge_kind_unsupported", f"Unsupported edge kind: {kind}", field="kind"))
            continue
        source = raw_edge.get("source") if isinstance(raw_edge.get("source"), dict) else {}
        target = raw_edge.get("target") if isinstance(raw_edge.get("target"), dict) else {}
        source_id = str(source.get("node_id") or "").strip()
        target_id = str(target.get("node_id") or "").strip()
        if source_id not in nodes or target_id not in nodes:
            errors.append(_issue("edge_node_missing", f"Edge {edge_id} references an unknown node", field="node_id"))
            continue
        if source_id == target_id:
            errors.append(_issue("graph_cycle", f"Self-cycle at node {source_id}", node_id=source_id))
        source_port = str(source.get("port_id") or "").strip()
        target_port = str(target.get("port_id") or "").strip()
        source_ports = _output_ports(nodes[source_id])
        target_ports = _input_ports(nodes[target_id])
        if kind == "dependency":
            source_ports.setdefault("done", "any")
            target_ports.setdefault("start", "any")
        if source_port not in source_ports:
            errors.append(_issue("source_port_missing", f"Unknown source port {source_port or '<empty>'}", node_id=source_id, field="port_id"))
        if target_port not in target_ports:
            errors.append(_issue("target_port_missing", f"Unknown target port {target_port or '<empty>'}", node_id=target_id, field="port_id"))
        if kind == "data" and source_port in source_ports and target_port in target_ports:
            source_type = source_ports[source_port]
            target_type = target_ports[target_port]
            if not _types_compatible(source_type, target_type):
                errors.append(_issue("port_type_mismatch", f"端口类型不兼容：{source_type} → {target_type}", node_id=target_id, field=target_port))
            binding_key = (target_id, target_port)
            if not _input_port_is_collection(nodes[target_id], target_port):
                if binding_key in scalar_input_bindings:
                    errors.append(_issue(
                        "multiple_edges_to_single_input",
                        f"该输入已绑定：{target_id}.{target_port}",
                        node_id=target_id,
                        field=target_port,
                    ))
                else:
                    scalar_input_bindings[binding_key] = edge_id
        degree[source_id] += 1
        degree[target_id] += 1
        valid_edges.append(raw_edge)

    cycle_nodes = _cycle_nodes(nodes, valid_edges)
    if cycle_nodes and not any(item["code"] == "graph_cycle" for item in errors):
        errors.append(_issue("graph_cycle", f"Workflow graph contains a cycle: {', '.join(cycle_nodes)}", node_id=cycle_nodes[0]))

    if len(nodes) > 1:
        for node_id in sorted(nodes):
            if degree.get(node_id, 0) == 0:
                errors.append(_issue("orphan_node", "Node is isolated from the workflow graph", node_id=node_id))

    _validate_required_bindings(nodes, valid_edges, errors)
    _validate_artifact_contracts(nodes, valid_edges, errors)

    settings = graph.get("settings") if isinstance(graph.get("settings"), dict) else {}
    max_parallelism = settings.get("max_parallelism", 1)
    if max_parallelism != 1:
        errors.append(_issue("max_parallelism_unsupported", "Workbench V2 currently requires max_parallelism = 1", field="max_parallelism"))
    if not isinstance(settings.get("stop_on_error", True), bool):
        errors.append(_issue("stop_on_error_invalid", "stop_on_error must be boolean", field="stop_on_error"))
    return _validation_result(errors, warnings)


def compile_workflow_graph(
    graph: dict[str, Any],
    *,
    capabilities: dict[str, Any] | None,
    workflow_version_id: str,
    workflow_version_number: int = 1,
) -> dict[str, Any]:
    validation = validate_workflow_graph(graph, capabilities=capabilities)
    if not validation["valid"]:
        raise WorkflowGraphValidationError(validation)
    nodes = {str(item["id"]): item for item in graph["nodes"]}
    edges = list(graph["edges"])
    execution_graph_ids = sorted(
        node_id for node_id, node in nodes.items() if node.get("kind") in EXECUTION_NODE_KINDS
    )
    graph_dependencies = _execution_dependencies(nodes, edges)
    graph_order = _stable_topological_order(execution_graph_ids, graph_dependencies)
    step_id_by_graph_id = {
        node_id: str(_config(nodes[node_id]).get("step_id") or node_id)
        for node_id in execution_graph_ids
    }
    topological_order = [step_id_by_graph_id[node_id] for node_id in graph_order]

    input_nodes = sorted(
        (node for node in nodes.values() if node.get("kind") == "input"),
        key=lambda node: str(_config(node).get("contract_id") or node["id"]),
    )
    output_nodes = sorted(
        (node for node in nodes.values() if node.get("kind") == "output"),
        key=lambda node: str(_config(node).get("output_id") or node["id"]),
    )
    compiled_inputs = [_compiled_input(node) for node in input_nodes]
    compiled_outputs = [
        _compiled_output(node, nodes, edges, step_id_by_graph_id) for node in output_nodes
    ]
    plan_nodes: list[dict[str, Any]] = []
    compiled_steps: list[dict[str, Any]] = []
    for graph_node_id in graph_order:
        node = nodes[graph_node_id]
        config = _config(node)
        step_id = step_id_by_graph_id[graph_node_id]
        depends_on = sorted(
            step_id_by_graph_id[source]
            for source in graph_dependencies.get(graph_node_id, set())
        )
        bindings = _resolved_bindings(graph_node_id, nodes, edges, step_id_by_graph_id)
        output_contracts = [
            output for output in compiled_outputs if output.get("from") == step_id
        ]
        step = _compiled_step(node, step_id=step_id, depends_on=depends_on, bindings=bindings)
        compiled_steps.append(step)
        plan_nodes.append({
            "node_id": step_id,
            "graph_node_id": graph_node_id,
            "type": step["type"],
            "depends_on": depends_on,
            "resolved_input_bindings": bindings,
            "input_ports": _compiled_ports(config.get("input_ports")),
            "output_ports": _compiled_ports(config.get("output_ports")),
            "provider": str(config.get("provider") or "") if node.get("kind") == "agent" else "",
            "mcp_profiles": sorted(_strings(config.get("mcp_profiles"))),
            "skill_ids": sorted(_strings(config.get("skill_ids"))),
            "output_contracts": output_contracts,
            "timeout_sec": int(config.get("timeout_sec") or 900),
            "idle_timeout_sec": int(config.get("idle_timeout_sec") or 0),
            "retry_policy": _retry_policy(config),
            "failure_policy": str(config.get("failure_policy") or "stop"),
        })

    compiled_definition = {
        "id": str(graph["workflow_id"]),
        "name": str(graph["name"]),
        "description": str(graph.get("description") or ""),
        "version": int(workflow_version_number),
        "inputs": compiled_inputs,
        "steps": compiled_steps,
        "outputs": compiled_outputs,
    }
    settings = graph.get("settings") if isinstance(graph.get("settings"), dict) else {}
    compiled_plan = {
        "plan_version": PLAN_VERSION,
        "workflow_version_id": str(workflow_version_id),
        "topological_order": topological_order,
        "nodes": plan_nodes,
        "max_parallelism": 1,
        "stop_on_error": bool(settings.get("stop_on_error", True)),
    }
    return {
        "compiled_definition": compiled_definition,
        "compiled_plan": compiled_plan,
        "validation_result": validation,
    }


def compile_legacy_workflow(
    definition: dict[str, Any], *, workflow_version_id: str = "legacy"
) -> dict[str, Any]:
    steps = [dict(item) for item in definition.get("steps") or [] if isinstance(item, dict)]
    nodes: list[dict[str, Any]] = []
    previous_id = ""
    for step in steps:
        step_id = str(step.get("id") or "")
        dependencies = [previous_id] if previous_id else []
        nodes.append({
            "node_id": step_id,
            "graph_node_id": step_id,
            "type": str(step.get("type") or ""),
            "depends_on": dependencies,
            "resolved_input_bindings": {},
            "provider": str(step.get("provider") or ""),
            "mcp_profiles": _strings(step.get("mcp_profiles") or step.get("mcp_profile")),
            "skill_ids": _strings(step.get("skills") or step.get("skill_ids")),
            "output_contracts": [
                dict(output) for output in definition.get("outputs") or []
                if isinstance(output, dict)
                and str(output.get("from") or output.get("source") or "") == step_id
            ],
            "timeout_sec": int(step.get("timeout_sec") or 900),
            "idle_timeout_sec": int(step.get("idle_timeout_sec") or 0),
            "retry_policy": _retry_policy(step),
            "failure_policy": str(step.get("failure_policy") or "stop"),
        })
        previous_id = step_id
    return {
        "plan_version": PLAN_VERSION,
        "workflow_version_id": str(workflow_version_id),
        "topological_order": [str(step.get("id") or "") for step in steps],
        "nodes": nodes,
        "max_parallelism": 1,
        "stop_on_error": True,
        "compatibility_mode": "legacy_sequential",
    }


def _validate_input_node(node_id: str, config: dict[str, Any], errors: list[dict[str, Any]]) -> None:
    if config.get("required") and not str(config.get("label") or "").strip():
        errors.append(_issue("required_input_label_missing", "Required input needs a label", node_id=node_id, field="label"))
    if config.get("required") and not str(config.get("type") or "").strip():
        errors.append(_issue("required_input_type_missing", "Required input needs a type", node_id=node_id, field="type"))
    resolver = str(config.get("resolver") or "manual")
    if resolver not in SUPPORTED_RESOLVERS:
        errors.append(_issue("input_resolver_unsupported", f"Unsupported input resolver: {resolver}", node_id=node_id, field="resolver"))
    if resolver == "agent_mcp" and str(config.get("type") or "") not in {"mr_link", "external_link", "url", "text"}:
        errors.append(_issue("agent_mcp_input_type_incompatible", "agent_mcp resolver is not supported for this input type", node_id=node_id, field="resolver"))


def _validate_execution_node(
    node_id: str,
    kind: str,
    config: dict[str, Any],
    capabilities: dict[str, Any],
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> None:
    _validate_port_definitions(node_id, "input", config.get("input_ports"), errors)
    _validate_port_definitions(node_id, "output", config.get("output_ports"), errors)
    if kind != "agent":
        return
    if not str(config.get("goal") or "").strip():
        errors.append(_issue("agent_goal_missing", "Agent node requires a goal", node_id=node_id, field="goal"))
    provider = str(config.get("provider") or "").strip()
    providers = capabilities.get("providers") if isinstance(capabilities.get("providers"), dict) else {}
    provider_capability = providers.get(provider) if provider else None
    if not provider:
        errors.append(_issue("provider_missing", "Agent node requires a provider", node_id=node_id, field="provider"))
    elif providers and not isinstance(provider_capability, dict):
        errors.append(_issue("provider_unknown", f"Unknown provider: {provider}", node_id=node_id, field="provider"))
    elif isinstance(provider_capability, dict) and provider_capability.get("available") is False:
        errors.append(_issue("provider_unavailable", f"Provider is unavailable: {provider}", node_id=node_id, field="provider"))
    compatible_mcp = set(_strings((provider_capability or {}).get("mcp_profiles")))
    for profile in _strings(config.get("mcp_profiles")):
        if not isinstance(provider_capability, dict) or profile not in compatible_mcp:
            errors.append(_issue("mcp_incompatible", f"MCP profile {profile} is not compatible with provider {provider}", node_id=node_id, field="mcp_profiles"))
    known_skills = set(_strings(capabilities.get("skills")))
    for skill_id in _strings(config.get("skill_ids")):
        if known_skills and skill_id not in known_skills:
            warnings.append(_issue("skill_unknown", f"Skill is not registered: {skill_id}", node_id=node_id, field="skill_ids"))
    failure_policy = str(config.get("failure_policy") or "stop")
    if failure_policy not in SUPPORTED_FAILURE_POLICIES:
        errors.append(_issue("failure_policy_unsupported", f"Unsupported failure policy: {failure_policy}", node_id=node_id, field="failure_policy"))
    retry = _retry_policy(config)
    if retry != {"max_attempts": 1, "backoff_seconds": 0}:
        errors.append(_issue("retry_policy_unsupported", "Only one immediate attempt is supported", node_id=node_id, field="retry_policy"))


def _validate_required_bindings(
    nodes: dict[str, dict[str, Any]], edges: list[dict[str, Any]], errors: list[dict[str, Any]]
) -> None:
    for node_id, node in nodes.items():
        config = _config(node)
        if node.get("kind") == "input" and config.get("required") and not config.get("global_input"):
            bound = any(
                edge.get("kind") == "data"
                and _endpoint(edge, "source", "node_id") == node_id
                and nodes.get(_endpoint(edge, "target", "node_id"), {}).get("kind") in EXECUTION_NODE_KINDS
                for edge in edges
            )
            if not bound:
                errors.append(_issue("required_input_unbound", "Required input is not bound to an execution node", node_id=node_id))
        if node.get("kind") == "output" and config.get("required"):
            incoming = [
                edge for edge in edges
                if edge.get("kind") == "data" and _endpoint(edge, "target", "node_id") == node_id
            ]
            if not incoming:
                errors.append(_issue("required_output_unbound", "Required output has no execution source", node_id=node_id))
            elif nodes.get(_endpoint(incoming[0], "source", "node_id"), {}).get("kind") not in EXECUTION_NODE_KINDS:
                errors.append(_issue("required_output_source_invalid", "Required output must be sourced from an execution node", node_id=node_id))
        if node.get("kind") in EXECUTION_NODE_KINDS:
            incoming_ports = {
                _endpoint(edge, "target", "port_id")
                for edge in edges
                if edge.get("kind") == "data" and _endpoint(edge, "target", "node_id") == node_id
            }
            for port in config.get("input_ports") or []:
                if (
                    isinstance(port, dict)
                    and port.get("required")
                    and str(port.get("id") or "") not in incoming_ports
                ):
                    port_id = str(port.get("id") or "")
                    errors.append(_issue("required_port_unbound", f"Required execution input port is unbound: {port_id}", node_id=node_id, field=port_id))


def _validate_artifact_contracts(
    nodes: dict[str, dict[str, Any]], edges: list[dict[str, Any]], errors: list[dict[str, Any]]
) -> None:
    artifacts: dict[str, str] = {}
    output_artifacts_by_source: dict[str, set[str]] = defaultdict(set)
    for node_id, node in nodes.items():
        if node.get("kind") != "output":
            continue
        config = _config(node)
        artifact = str(config.get("artifact") or "").strip()
        if artifact:
            if not _safe_artifact(artifact):
                errors.append(_issue("unsafe_artifact", f"Artifact path is unsafe: {artifact}", node_id=node_id, field="artifact"))
            if artifact in artifacts:
                errors.append(_issue("duplicate_output_artifact", f"Output artifact is duplicated: {artifact}", node_id=node_id, field="artifact"))
            artifacts[artifact] = node_id
        incoming = next(
            (
                edge for edge in edges
                if edge.get("kind") == "data" and _endpoint(edge, "target", "node_id") == node_id
            ),
            None,
        )
        if incoming:
            source_id = _endpoint(incoming, "source", "node_id")
            output_artifacts_by_source[source_id].add(artifact)
            declared_source = str(config.get("source_node_id") or source_id)
            declared_port = str(config.get("source_port_id") or _endpoint(incoming, "source", "port_id"))
            if declared_source != source_id or declared_port != _endpoint(incoming, "source", "port_id"):
                errors.append(_issue("output_source_mismatch", "Output source fields do not match its data edge", node_id=node_id))
    for node_id, node in nodes.items():
        if node.get("kind") != "agent":
            continue
        required = set(_strings(_config(node).get("required_artifacts")))
        for artifact in required:
            if not _safe_artifact(artifact):
                errors.append(_issue("unsafe_artifact", f"Artifact path is unsafe: {artifact}", node_id=node_id, field="required_artifacts"))
        declared_outputs = {item for item in output_artifacts_by_source.get(node_id, set()) if item}
        if required != declared_outputs:
            errors.append(_issue("required_artifacts_output_mismatch", "Agent required_artifacts must match connected Output artifacts", node_id=node_id, field="required_artifacts"))


def _compiled_input(node: dict[str, Any]) -> dict[str, Any]:
    config = _config(node)
    payload = {
        "id": str(config.get("contract_id") or node["id"]),
        "label": str(config.get("label") or node.get("label") or node["id"]),
        "type": str(config.get("type") or "text"),
        "required": bool(config.get("required")),
        "resolver": str(config.get("resolver") or "manual"),
        "role": str(config.get("role") or ""),
    }
    for key in ("default_value", "schema"):
        if key in config:
            payload[key] = config[key]
    return payload


def _compiled_step(
    node: dict[str, Any], *, step_id: str, depends_on: list[str], bindings: dict[str, Any]
) -> dict[str, Any]:
    config = _config(node)
    kind = str(node.get("kind") or "")
    step_type = "agent_task" if kind == "agent" else kind
    payload: dict[str, Any] = {
        "id": step_id,
        "type": step_type,
        "depends_on": depends_on,
        "input_bindings": bindings,
        "failure_policy": str(config.get("failure_policy") or "stop"),
    }
    if kind == "agent":
        payload.update({
            "goal": str(config.get("goal") or ""),
            "provider": str(config.get("provider") or ""),
            "mcp_profiles": sorted(_strings(config.get("mcp_profiles"))),
            "skills": sorted(_strings(config.get("skill_ids"))),
            "skill_instructions": list(config.get("skill_instructions") or []),
            "required_artifacts": sorted(_strings(config.get("required_artifacts"))),
            "timeout_sec": int(config.get("timeout_sec") or 900),
            "idle_timeout_sec": int(config.get("idle_timeout_sec") or 0),
            "retry_policy": _retry_policy(config),
        })
        mcp_profiles = payload["mcp_profiles"]
        payload["mcp_profile"] = mcp_profiles[0] if len(mcp_profiles) == 1 else ""
    else:
        for key, value in sorted(config.items()):
            if key not in {"step_id", "input_ports", "output_ports"} and key not in payload:
                payload[key] = value
    return payload


def _compiled_output(
    node: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    step_id_by_graph_id: dict[str, str],
) -> dict[str, Any]:
    config = _config(node)
    incoming = next(
        edge for edge in edges
        if edge.get("kind") == "data" and _endpoint(edge, "target", "node_id") == node["id"]
    )
    source_graph_id = _endpoint(incoming, "source", "node_id")
    payload = {
        "id": str(config.get("output_id") or node["id"]),
        "label": str(config.get("label") or node.get("label") or node["id"]),
        "type": str(config.get("type") or "text"),
        "artifact": str(config.get("artifact") or ""),
        "required": bool(config.get("required")),
        "from": step_id_by_graph_id[source_graph_id],
        "source_port_id": _endpoint(incoming, "source", "port_id"),
    }
    for key in (
        "schema",
        "evidence_memory",
        "semantic_import",
        "quality_rules",
        "companion_artifacts",
        "default_enabled",
    ):
        if key in config:
            payload[key] = config[key]
    return payload


def _resolved_bindings(
    target_id: str,
    nodes: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    step_ids: dict[str, str],
) -> dict[str, Any]:
    bindings: dict[str, Any] = {}
    for edge in edges:
        if edge.get("kind") != "data" or _endpoint(edge, "target", "node_id") != target_id:
            continue
        source_id = _endpoint(edge, "source", "node_id")
        source_node = nodes[source_id]
        source_ref = (
            step_ids[source_id]
            if source_node.get("kind") in EXECUTION_NODE_KINDS
            else str(_config(source_node).get("contract_id") or source_id)
        )
        target_port = _endpoint(edge, "target", "port_id")
        is_collection = _input_port_is_collection(nodes[target_id], target_port)
        if target_port in bindings and not is_collection:
            raise WorkflowGraphValidationError(_validation_result([
                _issue(
                    "multiple_edges_to_single_input",
                    f"该输入已绑定：{target_id}.{target_port}",
                    node_id=target_id,
                    field=target_port,
                )
            ], []))
        binding = {
            "source_node_id": source_ref,
            "source_port_id": _endpoint(edge, "source", "port_id"),
        }
        if is_collection:
            bindings.setdefault(target_port, []).append(binding)
        else:
            bindings[target_port] = binding
    for target_port, value in bindings.items():
        if isinstance(value, list):
            bindings[target_port] = sorted(
                value,
                key=lambda item: (item["source_node_id"], item["source_port_id"]),
            )
    return {key: bindings[key] for key in sorted(bindings)}


def _validate_port_definitions(
    node_id: str,
    direction: str,
    value: Any,
    errors: list[dict[str, Any]],
) -> None:
    seen: set[str] = set()
    prefix = "input" if direction == "input" else "output"
    label = "输入" if direction == "input" else "输出"
    for index, item in enumerate(value or []):
        if not isinstance(item, dict):
            errors.append(_issue(
                f"{prefix}_port_invalid",
                f"{label}端口定义无效",
                node_id=node_id,
                field=f"{prefix}_ports.{index}",
            ))
            continue
        port_id = str(item.get("id") or "").strip()
        field = f"{prefix}_ports.{index}.id"
        if not port_id:
            errors.append(_issue(
                f"{prefix}_port_id_missing",
                f"{label}端口名称不能为空",
                node_id=node_id,
                field=field,
            ))
            continue
        if not _SAFE_ID.fullmatch(port_id):
            errors.append(_issue(
                f"{prefix}_port_id_invalid",
                f"{label}端口名称包含非法字符：{port_id}",
                node_id=node_id,
                field=field,
            ))
        if port_id in seen:
            errors.append(_issue(
                f"duplicate_{prefix}_port_id",
                f"{label}端口名称重复：{port_id}",
                node_id=node_id,
                field=field,
            ))
        seen.add(port_id)


def _execution_dependencies(
    nodes: dict[str, dict[str, Any]], edges: list[dict[str, Any]]
) -> dict[str, set[str]]:
    dependencies: dict[str, set[str]] = {
        node_id: set() for node_id, node in nodes.items() if node.get("kind") in EXECUTION_NODE_KINDS
    }
    for edge in edges:
        source = _endpoint(edge, "source", "node_id")
        target = _endpoint(edge, "target", "node_id")
        if source in dependencies and target in dependencies:
            dependencies[target].add(source)
    return dependencies


def _stable_topological_order(node_ids: list[str], dependencies: dict[str, set[str]]) -> list[str]:
    remaining = {node_id: set(dependencies.get(node_id, set())) for node_id in node_ids}
    order: list[str] = []
    while remaining:
        ready = sorted(node_id for node_id, deps in remaining.items() if not deps)
        if not ready:
            raise WorkflowGraphValidationError(
                _validation_result([_issue("graph_cycle", "Workflow graph contains a cycle")], [])
            )
        for node_id in ready:
            order.append(node_id)
            remaining.pop(node_id)
        for deps in remaining.values():
            deps.difference_update(ready)
    return order


def _cycle_nodes(nodes: dict[str, dict[str, Any]], edges: list[dict[str, Any]]) -> list[str]:
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    for edge in edges:
        source = _endpoint(edge, "source", "node_id")
        target = _endpoint(edge, "target", "node_id")
        if source in adjacency and target in adjacency:
            adjacency[source].add(target)
    visiting: set[str] = set()
    visited: set[str] = set()
    cycle: list[str] = []

    def visit(node_id: str) -> bool:
        if node_id in visiting:
            cycle.append(node_id)
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        for target in sorted(adjacency[node_id]):
            if visit(target):
                cycle.append(node_id)
                return True
        visiting.remove(node_id)
        visited.add(node_id)
        return False

    for node_id in sorted(nodes):
        if visit(node_id):
            return sorted(set(cycle))
    return []


def _input_ports(node: dict[str, Any]) -> dict[str, str]:
    kind = str(node.get("kind") or "")
    config = _config(node)
    if kind == "output":
        return {"value": str(config.get("type") or "any")}
    if kind in EXECUTION_NODE_KINDS:
        return _port_map(config.get("input_ports"))
    return {}


def _output_ports(node: dict[str, Any]) -> dict[str, str]:
    kind = str(node.get("kind") or "")
    config = _config(node)
    if kind == "input":
        return {"value": str(config.get("type") or "any")}
    if kind in EXECUTION_NODE_KINDS:
        return _port_map(config.get("output_ports"))
    return {}


def _input_port_is_collection(node: dict[str, Any], port_id: str) -> bool:
    if node.get("kind") == "output":
        return False
    for item in _config(node).get("input_ports") or []:
        if isinstance(item, dict) and str(item.get("id") or "") == port_id:
            return bool(item.get("collection", False))
    return False


def _port_map(value: Any) -> dict[str, str]:
    ports: dict[str, str] = {}
    for item in value or []:
        if isinstance(item, dict) and str(item.get("id") or "").strip():
            ports[str(item["id"])] = str(item.get("type") or "any")
    return ports


def _compiled_ports(value: Any) -> list[dict[str, Any]]:
    ports = [
        {
            "id": str(item.get("id") or ""),
            "type": str(item.get("type") or "any"),
            **({"required": bool(item.get("required"))} if "required" in item else {}),
            **({"collection": bool(item.get("collection"))} if "collection" in item else {}),
        }
        for item in value or []
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    return sorted(ports, key=lambda item: item["id"])


def _types_compatible(source: str, target: str) -> bool:
    return source == target or source == "any" or target == "any"


def _safe_artifact(value: str) -> bool:
    text = str(value or "").strip().replace("\\", "/")
    if not text or PurePosixPath(text).is_absolute() or PureWindowsPath(text).is_absolute():
        return False
    return ".." not in PurePosixPath(text).parts and not text.endswith("/")


def _retry_policy(config: dict[str, Any]) -> dict[str, int]:
    raw = config.get("retry_policy") if isinstance(config.get("retry_policy"), dict) else {}
    return {
        "max_attempts": int(raw.get("max_attempts", 1)),
        "backoff_seconds": int(raw.get("backoff_seconds", 0)),
    }


def _config(node: dict[str, Any]) -> dict[str, Any]:
    return dict(node.get("config") or {}) if isinstance(node.get("config"), dict) else {}


def _endpoint(edge: dict[str, Any], side: str, key: str) -> str:
    endpoint = edge.get(side) if isinstance(edge.get(side), dict) else {}
    return str(endpoint.get(key) or "")


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(item) for item in value or [] if str(item).strip()]


def _issue(
    code: str,
    message: str,
    *,
    node_id: str | None = None,
    field: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if node_id:
        payload["node_id"] = node_id
    if field:
        payload["field"] = field
    return payload


def _validation_result(
    errors: list[dict[str, Any]], warnings: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "valid": not errors,
        "errors": sorted(errors, key=lambda item: (str(item.get("node_id") or ""), str(item["code"]), str(item.get("field") or ""))),
        "warnings": sorted(warnings, key=lambda item: (str(item.get("node_id") or ""), str(item["code"]), str(item.get("field") or ""))),
    }
