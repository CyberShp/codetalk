"""Immutable V3 workflow contracts for new canvas-authored workflows.

This module is intentionally pure: callers provide the handler capability snapshot
that is used for validation, and the compiled result carries every field a runner
needs.  It does not consult a live registry or create another runtime state store.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any


COMPILED_CONTRACT_VERSION = 3
_SCHEMA_VERSION = 3
_VALIDATION_PROFILES = frozenset({
    "none",
    "artifact_only",
    "schema",
    "source_evidence",
    "storage_test_design",
    "formal_release",
})
_PROFILE_HANDLERS = {
    "none": (),
    "artifact_only": ("artifact_exists",),
    "schema": ("artifact_exists", "json_schema"),
    "source_evidence": ("artifact_exists", "source_evidence"),
    "storage_test_design": ("artifact_exists", "storage_test_design"),
    "formal_release": (
        "artifact_exists",
        "storage_test_design",
        "independent_review",
        "human_approval",
    ),
}


def validate_workflow_contract_v3(
    graph: dict[str, Any], *, capabilities: dict[str, Any] | None = None,
    require_executable: bool = False,
) -> dict[str, Any]:
    """Validate a V3 authoring graph without mutating it.

    A draft can contain handlers that are currently unavailable.  Such a graph is
    saveable and receives warnings; a publish or trial-run request must pass
    ``require_executable=True`` and fails closed instead.
    """
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not isinstance(graph, dict):
        return _result([_issue("graph_not_object", "Workflow graph must be an object")], warnings)
    if graph.get("schema_version") != _SCHEMA_VERSION:
        errors.append(_issue("schema_version_invalid", "Workflow schema_version must be 3", field="schema_version"))

    nodes = _node_map(graph.get("nodes"), errors)
    edges = _edges(graph.get("edges"), nodes, errors)
    _validate_edge_ports(nodes, edges, errors)
    _validate_execution_graph(nodes, edges, errors)
    _validate_scalar_data_bindings(nodes, edges, errors)
    profile = _profile(graph, errors)
    outputs = _declared_outputs(nodes, edges, errors)
    _validate_explicit_validators(nodes, {item["output_id"] for item in outputs}, errors)

    _validate_provider_capability_requirements(
        nodes,
        capabilities,
        errors,
        warnings,
        require_executable=require_executable,
    )

    requested_handlers = _requested_handlers(nodes, outputs, profile)
    available = _handler_capabilities(capabilities)
    for handler_id, handler_version, node_id in requested_handlers:
        if _handler_available(available, handler_id, handler_version):
            continue
        issue = _issue(
            "handler_unavailable" if require_executable else "handler_unavailable_draft",
            f"Handler is not available: {handler_id}@{handler_version}",
            node_id=node_id,
            field="handler_id",
            handler_id=handler_id,
            handler_version=handler_version,
        )
        (errors if require_executable else warnings).append(issue)
    return _result(errors, warnings)


def compile_workflow_contract_v3(
    graph: dict[str, Any], *, capabilities: dict[str, Any] | None,
    workflow_version_id: str, workflow_version_number: int = 1,
    require_executable: bool = True,
) -> dict[str, Any]:
    """Compile a V3 graph into the sole runtime definition and execution plan."""
    validation = validate_workflow_contract_v3(
        graph, capabilities=capabilities, require_executable=require_executable
    )
    if not validation["valid"]:
        return {
            "compiled_definition": None,
            "compiled_plan": None,
            "validation_result": validation,
        }

    nodes = {str(node["id"]): node for node in graph.get("nodes", []) if isinstance(node, dict) and node.get("id")}
    ignored_errors: list[dict[str, Any]] = []
    edges = _edges(graph.get("edges"), nodes, ignored_errors)
    profile = _profile(graph, ignored_errors)
    outputs = _declared_outputs(nodes, edges, ignored_errors)
    inputs = _declared_inputs(nodes)
    executable_nodes = _compile_graph_nodes(nodes, edges, outputs)
    profile_validators = _profile_validators(profile, outputs, executable_nodes)
    all_nodes = executable_nodes + profile_validators
    topological_order = [node["node_id"] for node in all_nodes]
    # WorkflowStore's compatibility ``steps`` projection contains executable
    # work only. V3 validators remain authoritative in ``validators`` and the
    # compiled plan; projecting them as legacy steps would make an otherwise
    # valid V3 contract impossible to persist.
    runtime_steps = [
        _runtime_step(node, outputs)
        for node in executable_nodes
    ]
    plan_nodes = [_plan_node(node) for node in all_nodes]
    settings = graph.get("settings") if isinstance(graph.get("settings"), dict) else {}
    compiled_definition = {
        # Existing WorkflowStore/Task Prepare consumers still use these fields.
        "id": str(graph.get("workflow_id") or ""),
        "name": str(graph.get("name") or ""),
        "description": str(graph.get("description") or ""),
        "version": int(workflow_version_number),
        "inputs": inputs,
        "steps": runtime_steps,
        "outputs": outputs,
        # V3 fields are part of the same immutable definition, not another store.
        "compiled_contract_version": COMPILED_CONTRACT_VERSION,
        "validation_profile": profile,
        "declared_inputs": inputs,
        "declared_outputs": outputs,
        "validators": [node for node in all_nodes if node["kind"] == "validator"],
        "nodes": all_nodes,
    }
    compiled_plan = {
        # Scheduler plan version remains stable; contract version selects V3
        # semantics without making a second runtime definition.
        "plan_version": 1,
        "compiled_contract_version": COMPILED_CONTRACT_VERSION,
        "workflow_version_id": str(workflow_version_id),
        "topological_order": topological_order,
        "nodes": plan_nodes,
        "max_parallelism": int(settings.get("max_parallelism", 1)),
        "stop_on_error": bool(settings.get("stop_on_error", True)),
        "settings": {
            "stop_on_error": bool(settings.get("stop_on_error", True)),
            "max_parallelism": int(settings.get("max_parallelism", 1)),
            "validation_profile": profile,
        },
    }
    return {
        "compiled_definition": compiled_definition,
        "compiled_plan": compiled_plan,
        "validation_result": validation,
    }


def _result(errors: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    return {"valid": not errors, "errors": errors, "warnings": warnings}


def _issue(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **{key: value for key, value in extra.items() if value is not None}}


def _node_map(raw_nodes: Any, errors: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not isinstance(raw_nodes, list):
        errors.append(_issue("nodes_not_array", "nodes must be an array", field="nodes"))
        return {}
    nodes: dict[str, dict[str, Any]] = {}
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            errors.append(_issue("node_not_object", "Each node must be an object"))
            continue
        node_id = str(raw.get("id") or "").strip()
        if not node_id:
            errors.append(_issue("node_id_missing", "Node ID is required", field="id"))
            continue
        if node_id in nodes:
            errors.append(_issue("duplicate_node_id", f"Duplicate node ID: {node_id}", node_id=node_id, field="id"))
            continue
        kind = str(raw.get("kind") or "").strip()
        if not kind:
            errors.append(_issue("node_kind_missing", "Node kind is required", node_id=node_id, field="kind"))
            continue
        nodes[node_id] = raw
    return nodes


def _edges(raw_edges: Any, nodes: dict[str, dict[str, Any]], errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(raw_edges, list):
        errors.append(_issue("edges_not_array", "edges must be an array", field="edges"))
        return []
    valid: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_edges:
        if not isinstance(raw, dict):
            errors.append(_issue("edge_not_object", "Each edge must be an object"))
            continue
        edge_id = str(raw.get("id") or "").strip()
        if not edge_id or edge_id in seen:
            errors.append(_issue("edge_id_invalid", "Edge ID is missing or duplicated", field="id"))
            continue
        seen.add(edge_id)
        source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
        target = raw.get("target") if isinstance(raw.get("target"), dict) else {}
        source_id = str(source.get("node_id") or "").strip()
        target_id = str(target.get("node_id") or "").strip()
        if source_id not in nodes or target_id not in nodes:
            errors.append(_issue("edge_node_missing", "Edge references an unknown node", field="node_id"))
            continue
        valid.append(raw)
    return sorted(valid, key=lambda item: str(item["id"]))


def _profile(graph: dict[str, Any], errors: list[dict[str, Any]]) -> str:
    settings = graph.get("settings") if isinstance(graph.get("settings"), dict) else {}
    profile = str(settings.get("validation_profile") or "artifact_only")
    if profile not in _VALIDATION_PROFILES:
        errors.append(_issue("validation_profile_invalid", f"Unsupported validation profile: {profile}", field="validation_profile"))
    return profile


def _validate_execution_graph(
    nodes: dict[str, dict[str, Any]], edges: list[dict[str, Any]], errors: list[dict[str, Any]]
) -> None:
    """Reject cycles before compilation can produce a misleading fallback order."""
    executable_ids = sorted(
        node_id for node_id, node in nodes.items() if node.get("kind") not in {"input", "output"}
    )
    dependencies = _dependencies(executable_ids, edges, nodes)
    cycle = _cycle_nodes(executable_ids, dependencies)
    if cycle:
        errors.append(_issue(
            "graph_cycle",
            f"Workflow graph contains a cycle: {', '.join(cycle)}",
            node_id=cycle[0],
        ))


def _validate_edge_ports(
    nodes: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> None:
    """Validate data bindings before they become an immutable run contract."""
    for edge in edges:
        if edge.get("kind", "data") != "data":
            continue
        edge_id = str(edge.get("id") or "")
        source = edge.get("source") if isinstance(edge.get("source"), dict) else {}
        target = edge.get("target") if isinstance(edge.get("target"), dict) else {}
        source_id = str(source.get("node_id") or "")
        target_id = str(target.get("node_id") or "")
        source_port = str(source.get("port_id") or "")
        target_port = str(target.get("port_id") or "")
        source_type = _port_type(nodes[source_id], "outputs", source_port)
        target_type = _port_type(nodes[target_id], "inputs", target_port)
        if source_type is None:
            errors.append(_issue(
                "source_port_missing",
                f"Unknown source port: {source_id}.{source_port or '<empty>'}",
                node_id=source_id,
                field="port_id",
                edge_id=edge_id,
            ))
        if target_type is None:
            errors.append(_issue(
                "target_port_missing",
                f"Unknown target port: {target_id}.{target_port or '<empty>'}",
                node_id=target_id,
                field="port_id",
                edge_id=edge_id,
            ))
        if source_type is None or target_type is None:
            continue
        if not _types_compatible(source_type, target_type):
            errors.append(_issue(
                "port_type_mismatch",
                f"Port types are incompatible: {source_type} -> {target_type}",
                node_id=target_id,
                field=target_port,
                edge_id=edge_id,
                source_type=source_type,
                target_type=target_type,
            ))


def _validate_scalar_data_bindings(
    nodes: dict[str, dict[str, Any]], edges: list[dict[str, Any]], errors: list[dict[str, Any]]
) -> None:
    """A scalar target input has one binding; edge order must never decide it."""
    occupied: set[tuple[str, str]] = set()
    for edge in edges:
        if edge.get("kind", "data") != "data":
            continue
        target = edge.get("target") if isinstance(edge.get("target"), dict) else {}
        target_id = str(target.get("node_id") or "")
        target_port = str(target.get("port_id") or "")
        target_node = nodes.get(target_id)
        if target_node is None or _input_port_is_collection(target_node, target_port):
            continue
        binding = (target_id, target_port)
        if binding in occupied:
            errors.append(_issue(
                "multiple_edges_to_single_input",
                f"Input is already bound: {target_id}.{target_port}",
                node_id=target_id,
                field=target_port,
            ))
            continue
        occupied.add(binding)


def _cycle_nodes(node_ids: list[str], dependencies: dict[str, set[str]]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(node_id: str) -> list[str] | None:
        if node_id in visiting:
            start = stack.index(node_id)
            return stack[start:] + [node_id]
        if node_id in visited:
            return None
        visiting.add(node_id)
        stack.append(node_id)
        for dependency in sorted(dependencies.get(node_id, set())):
            cycle = visit(dependency)
            if cycle:
                return cycle
        stack.pop()
        visiting.remove(node_id)
        visited.add(node_id)
        return None

    for node_id in sorted(node_ids):
        cycle = visit(node_id)
        if cycle:
            return cycle
    return []


def _declared_inputs(nodes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node_id, node in sorted(nodes.items()):
        if node.get("kind") != "input":
            continue
        config = _config(node)
        result.append({
            "input_id": str(config.get("input_id") or node_id),
            # Legacy Task Prepare consumes id/resolver.  They are a projection of
            # the V3 input declaration, not an independently editable contract.
            "id": str(config.get("input_id") or node_id),
            "label": str(node.get("label") or config.get("label") or node_id),
            "type": str(config.get("type") or _port_type(node, "outputs", "value") or "text"),
            "required": bool(config.get("required", False)),
            "resolver": str(config.get("resolver") or "manual"),
        })
    return result


def _declared_outputs(nodes: dict[str, dict[str, Any]], edges: list[dict[str, Any]], errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node_id, node in sorted(nodes.items()):
        if node.get("kind") != "output":
            continue
        config = _config(node)
        output_id = str(config.get("output_id") or node_id)
        if output_id in seen:
            errors.append(_issue("duplicate_output_id", f"Duplicate output ID: {output_id}", node_id=node_id, field="output_id"))
            continue
        seen.add(output_id)
        producer = _output_producer(node_id, edges, nodes)
        if producer is None:
            errors.append(_issue("output_producer_missing", "Output must be connected to its producer", node_id=node_id, field="value", output_id=output_id))
            continue
        artifact = str(config.get("artifact") or "").strip()
        if not artifact:
            errors.append(_issue("output_artifact_missing", "Output artifact is required", node_id=node_id, field="artifact", output_id=output_id))
            continue
        if not _safe_artifact(artifact):
            errors.append(_issue(
                "output_artifact_unsafe",
                f"Output artifact must stay inside the run artifact directory: {artifact}",
                node_id=node_id,
                field="artifact",
                output_id=output_id,
            ))
            continue
        media_type = str(config.get("media_type") or config.get("type") or "application/octet-stream")
        result.append({
            "output_id": output_id,
            # Existing WorkflowStore/Task Prepare reads id/type/from.  Keep the
            # compatibility names on the declared output itself so there is still
            # exactly one output authority.
            "id": output_id,
            "label": str(node.get("label") or config.get("label") or output_id),
            "artifact": artifact,
            "media_type": media_type,
            "type": _legacy_output_type(media_type),
            "required": bool(config.get("required", False)),
            "schema": config.get("schema"),
            "producer_step_id": producer,
            "from": producer,
        })
    return sorted(result, key=lambda item: item["output_id"])


def _output_producer(output_node_id: str, edges: list[dict[str, Any]], nodes: dict[str, dict[str, Any]]) -> str | None:
    producers: list[str] = []
    for edge in edges:
        target = edge.get("target") if isinstance(edge.get("target"), dict) else {}
        source = edge.get("source") if isinstance(edge.get("source"), dict) else {}
        if str(target.get("node_id") or "") != output_node_id:
            continue
        source_id = str(source.get("node_id") or "")
        if source_id in nodes and nodes[source_id].get("kind") not in {"input", "output"}:
            producers.append(source_id)
    return sorted(producers)[0] if len(producers) == 1 else None


def _validate_explicit_validators(nodes: dict[str, dict[str, Any]], declared: set[str], errors: list[dict[str, Any]]) -> None:
    for node_id, node in sorted(nodes.items()):
        if node.get("kind") != "validator":
            continue
        required_outputs = _config(node).get("required_outputs")
        if not isinstance(required_outputs, list):
            errors.append(_issue("validator_required_outputs_invalid", "Validator required_outputs must be an array", node_id=node_id, field="required_outputs"))
            continue
        for output_id in sorted({str(item) for item in required_outputs} - declared):
            errors.append(_issue("validator_output_not_declared", f"Validator requires undeclared output: {output_id}", node_id=node_id, field="required_outputs", output_id=output_id))


def _validate_provider_capability_requirements(
    nodes: dict[str, dict[str, Any]],
    capabilities: dict[str, Any] | None,
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    *,
    require_executable: bool,
) -> None:
    """Fail closed when a publish/preflight snapshot cannot satisfy an Agent node.

    The compiler deliberately consumes a detached capability snapshot.  It must
    not instantiate adapters or guess that an arbitrary legacy/custom command
    supports a capability merely because it is configured as available.
    """
    providers = _provider_capability_snapshot(capabilities)
    if providers is None:
        # Pure/offline compiler callers may intentionally omit runtime
        # capabilities. Publish and trial-run routes always supply this snapshot.
        return
    for node_id, node in sorted(nodes.items()):
        if node.get("kind") not in {"agent", "builtin_model"}:
            continue
        config = _config(node)
        required = sorted(set(_strings(config.get("provider_capabilities_required"))))
        provider = str(config.get("provider_ref") or config.get("provider") or "").strip()
        entry = providers.get(provider)
        available = _declared_provider_capabilities(entry)
        if available is None:
            capability_suffix = (
                f"：{', '.join(required)}" if required else ""
            )
            _provider_capability_issue(
                errors,
                warnings,
                require_executable=require_executable,
                code="provider_capabilities_unknown",
                message=(
                    f"无法确认执行器“{provider or '未选择'}”的能力{capability_suffix}。"
                    "自定义或旧执行器不会被猜测为支持；请在设置中完成能力探测，"
                    "或选择受支持的执行器后重新发布。"
                ),
                node_id=node_id,
                provider=provider,
                required_capabilities=required,
            )
            continue
        if not required:
            continue
        missing = sorted(set(required) - available)
        if missing:
            _provider_capability_issue(
                errors,
                warnings,
                require_executable=require_executable,
                code="provider_capabilities_unsupported",
                message=(
                    f"执行器“{provider}”不支持所需能力：{', '.join(missing)}。"
                    "请调整 Agent 节点的能力要求，或在设置中选择支持这些能力的执行器后重新发布。"
                ),
                node_id=node_id,
                provider=provider,
                required_capabilities=required,
                missing_capabilities=missing,
            )


def _provider_capability_snapshot(capabilities: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(capabilities, dict):
        return None
    providers = capabilities.get("providers")
    return providers if isinstance(providers, dict) else None


def _declared_provider_capabilities(entry: Any) -> set[str] | None:
    if not isinstance(entry, dict):
        return None
    declared = entry.get("capabilities")
    if isinstance(declared, dict):
        return {str(name) for name, enabled in declared.items() if enabled is True}
    if isinstance(declared, (list, tuple, set)):
        return {str(item) for item in declared if str(item).strip()}
    return None


def _provider_capability_issue(
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    *,
    require_executable: bool,
    code: str,
    message: str,
    node_id: str,
    provider: str,
    required_capabilities: list[str],
    missing_capabilities: list[str] | None = None,
) -> None:
    issue = _issue(
        code if require_executable else f"{code}_draft",
        message,
        node_id=node_id,
        field="provider_capabilities_required",
        provider=provider,
        required_capabilities=required_capabilities,
        missing_capabilities=missing_capabilities,
    )
    (errors if require_executable else warnings).append(issue)


def _requested_handlers(nodes: dict[str, dict[str, Any]], outputs: list[dict[str, Any]], profile: str) -> list[tuple[str, int, str | None]]:
    requested: list[tuple[str, int, str | None]] = []
    for node_id, node in sorted(nodes.items()):
        if node.get("kind") in {"input", "output"}:
            continue
        config = _config(node)
        handler_id = str(config.get("handler_id") or config.get("validator_type") or node.get("kind"))
        requested.append((handler_id, _positive_int(config.get("handler_version"), 1), node_id))
    for handler_id, _required_outputs in _profile_handler_outputs(profile, outputs):
        requested.append((handler_id, 1, f"profile:{profile}"))
    return requested


def _handler_capabilities(capabilities: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(capabilities, dict):
        return {}
    handlers = capabilities.get("handlers")
    return handlers if isinstance(handlers, dict) else {}


def _handler_available(handlers: dict[str, Any], handler_id: str, version: int) -> bool:
    entry = handlers.get(handler_id)
    if entry is True:
        return True
    if isinstance(entry, (list, tuple, set)):
        return version in entry
    if not isinstance(entry, dict):
        return False
    versions = entry.get("versions")
    return versions is None or version in versions


def _compile_graph_nodes(nodes: dict[str, dict[str, Any]], edges: list[dict[str, Any]], outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    executable_ids = sorted(node_id for node_id, node in nodes.items() if node.get("kind") not in {"input", "output"})
    dependencies = _dependencies(executable_ids, edges, nodes)
    ordered_ids = _topological_order(executable_ids, dependencies)
    output_ids_by_producer: dict[str, list[str]] = defaultdict(list)
    for output in outputs:
        output_ids_by_producer[output["producer_step_id"]].append(output["output_id"])
    return [
        _compiled_node(nodes[node_id], node_id, sorted(dependencies[node_id]), _bindings(node_id, edges, nodes), sorted(output_ids_by_producer[node_id]))
        for node_id in ordered_ids
    ]


def _dependencies(executable_ids: list[str], edges: list[dict[str, Any]], nodes: dict[str, dict[str, Any]]) -> dict[str, set[str]]:
    dependencies = {node_id: set() for node_id in executable_ids}
    for edge in edges:
        source = edge.get("source") if isinstance(edge.get("source"), dict) else {}
        target = edge.get("target") if isinstance(edge.get("target"), dict) else {}
        source_id = str(source.get("node_id") or "")
        target_id = str(target.get("node_id") or "")
        if target_id in dependencies and source_id in dependencies:
            dependencies[target_id].add(source_id)
    return dependencies


def _topological_order(node_ids: list[str], dependencies: dict[str, set[str]]) -> list[str]:
    remaining = {node_id: set(dependencies[node_id]) for node_id in node_ids}
    result: list[str] = []
    while remaining:
        ready = sorted(node_id for node_id, deps in remaining.items() if not deps)
        if not ready:
            # Validation of graph cycles belongs to a later compiler expansion;
            # keep this deterministic for the minimal V3 contract.
            return sorted(node_ids)
        for node_id in ready:
            result.append(node_id)
            remaining.pop(node_id)
        ready_set = set(ready)
        for deps in remaining.values():
            deps.difference_update(ready_set)
    return result


def _bindings(node_id: str, edges: list[dict[str, Any]], nodes: dict[str, dict[str, Any]]) -> dict[str, dict[str, str]]:
    bindings: dict[str, dict[str, str]] = {}
    for edge in edges:
        if edge.get("kind", "data") != "data":
            continue
        source = edge.get("source") if isinstance(edge.get("source"), dict) else {}
        target = edge.get("target") if isinstance(edge.get("target"), dict) else {}
        if str(target.get("node_id") or "") != node_id:
            continue
        source_id = str(source.get("node_id") or "")
        if source_id not in nodes:
            continue
        target_port = str(target.get("port_id") or "")
        if target_port in bindings:
            # Validation has already rejected this graph.  Retaining the first
            # sorted edge here prevents a private helper from order-overwriting
            # a scalar binding if called independently in the future.
            continue
        binding = {
            "source_node_id": source_id,
            "source_port_id": str(source.get("port_id") or ""),
        }
        if nodes[source_id].get("kind") == "input":
            binding["source_input_id"] = str(
                _config(nodes[source_id]).get("input_id") or source_id
            )
        bindings[target_port] = binding
    return {key: bindings[key] for key in sorted(bindings)}


def _compiled_node(node: dict[str, Any], node_id: str, depends_on: list[str], bindings: dict[str, Any], required_outputs: list[str]) -> dict[str, Any]:
    config = _config(node)
    return {
        "node_id": node_id,
        "graph_node_id": node_id,
        "kind": str(node.get("kind")),
        "handler_id": str(config.get("handler_id") or config.get("validator_type") or node.get("kind")),
        "handler_version": _positive_int(config.get("handler_version"), 1),
        "depends_on": depends_on,
        "resolved_input_bindings": bindings,
        "input_ports": _ports(node, "inputs"),
        "output_ports": _ports(node, "outputs"),
        "provider_ref": config.get("provider_ref") or config.get("provider"),
        "provider_capabilities_required": sorted(_strings(config.get("provider_capabilities_required"))),
        "mcp_profiles": sorted(_strings(config.get("mcp_profiles"))),
        "skill_ids": sorted(_strings(config.get("skill_ids"))),
        "skill_instructions": list(_strings(config.get("skill_instructions"))),
        "goal": str(config.get("goal") or ""),
        "prompt_template_version": _positive_int(config.get("prompt_template_version"), 1),
        "prompt_template": str(config.get("prompt_template") or ""),
        "input_rendering": _input_rendering(config),
        "timeout_sec": _positive_int(config.get("timeout_sec"), 900),
        "idle_timeout_sec": _nonnegative_int(config.get("idle_timeout_sec"), 0),
        "retry_policy": _retry_policy(config),
        "failure_policy": str(config.get("failure_policy") or "stop"),
        "required_outputs": required_outputs,
    }


def _profile_validators(profile: str, outputs: list[dict[str, Any]], prior_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    validators: list[dict[str, Any]] = []
    output_by_id = {item["output_id"]: item for item in outputs}
    for handler_id, required_outputs in _profile_handler_outputs(profile, outputs):
        if not required_outputs and handler_id not in {"storage_test_design", "independent_review", "human_approval"}:
            continue
        node_id = f"profile_{handler_id}_" + ("_".join(required_outputs) or "workflow")
        depends_on = sorted({output_by_id[item]["producer_step_id"] for item in required_outputs if item in output_by_id})
        if prior_nodes and not depends_on:
            depends_on = [prior_nodes[-1]["node_id"]]
        validators.append({
            "node_id": node_id,
            "graph_node_id": None,
            "kind": "validator",
            "handler_id": handler_id,
            "handler_version": 1,
            "depends_on": depends_on,
            "resolved_input_bindings": {},
            "input_ports": [],
            "output_ports": [],
            "provider_ref": None,
            "provider_capabilities_required": [],
            "mcp_profiles": [],
            "skill_ids": [],
            "skill_instructions": [],
            "goal": "",
            "prompt_template_version": 1,
            "prompt_template": "",
            "input_rendering": {"preserve_user_text_verbatim": True, "binding_order": []},
            "timeout_sec": 900,
            "idle_timeout_sec": 0,
            "retry_policy": {"max_attempts": 1, "backoff_seconds": 0},
            "failure_policy": "stop",
            "required_outputs": required_outputs,
            "generated_by_validation_profile": True,
        })
    return validators


def _profile_handler_outputs(profile: str, outputs: list[dict[str, Any]]) -> list[tuple[str, list[str]]]:
    required = [item["output_id"] for item in outputs if item["required"]]
    with_schema = [item["output_id"] for item in outputs if item.get("schema") is not None]
    result: list[tuple[str, list[str]]] = []
    for handler_id in _PROFILE_HANDLERS.get(profile, ()):
        if handler_id == "json_schema":
            result.append((handler_id, with_schema))
        elif handler_id in {"storage_test_design", "independent_review", "human_approval"}:
            result.append((handler_id, required))
        else:
            result.append((handler_id, required))
    return result


def _runtime_step(node: dict[str, Any], outputs: list[dict[str, Any]]) -> dict[str, Any]:
    """Project a frozen V3 node into the legacy Task Prepare step shape."""
    required_artifacts = [
        str(output["artifact"])
        for output in outputs
        if output["producer_step_id"] == node["node_id"] and output["required"]
    ]
    return {
        "id": node["node_id"],
        "type": _runner_type(node["kind"]),
        "handler_id": node["handler_id"],
        "handler_version": node["handler_version"],
        "depends_on": node["depends_on"],
        "provider": node["provider_ref"],
        "provider_capabilities_required": node["provider_capabilities_required"],
        "mcp_profiles": node["mcp_profiles"],
        "skills": node["skill_ids"],
        "skill_instructions": node["skill_instructions"],
        "goal": node["goal"],
        "prompt_template_version": node["prompt_template_version"],
        "prompt_template": node["prompt_template"],
        "input_rendering": node["input_rendering"],
        "timeout_sec": node["timeout_sec"],
        "idle_timeout_sec": node["idle_timeout_sec"],
        "retry_policy": node["retry_policy"],
        "failure_policy": node["failure_policy"],
        "required_outputs": node["required_outputs"],
        "required_artifacts": required_artifacts,
    }


def _plan_node(node: dict[str, Any]) -> dict[str, Any]:
    """Keep plan nodes compatible with the existing scheduler dispatch table."""
    runner_type = _runner_type(node["kind"])
    # Only agent nodes need a legacy scheduler alias.  Leaving other nodes
    # unchanged keeps V3 validator definitions and plan entries identical.
    return {**node, "type": runner_type} if runner_type != node["kind"] else dict(node)


def _runner_type(kind: str) -> str:
    return "agent_task" if kind in {"agent", "builtin_model"} else kind


def _config(node: dict[str, Any]) -> dict[str, Any]:
    config = node.get("config")
    return config if isinstance(config, dict) else {}


def _ports(node: dict[str, Any], direction: str) -> list[dict[str, Any]]:
    ports = node.get("ports") if isinstance(node.get("ports"), dict) else {}
    raw = ports.get(direction)
    if not isinstance(raw, list):
        raw = _config(node).get(f"{direction[:-1]}_ports")
    if not isinstance(raw, list):
        return []
    return [
        {key: item[key] for key in ("id", "type", "required", "collection") if key in item}
        for item in raw if isinstance(item, dict) and item.get("id")
    ]


def _port_type(node: dict[str, Any], direction: str, port_id: str) -> str | None:
    for port in _ports(node, direction):
        if port.get("id") == port_id:
            return str(port.get("type") or "")
    return None


def _input_port_is_collection(node: dict[str, Any], port_id: str) -> bool:
    for port in _ports(node, "inputs"):
        if port.get("id") == port_id:
            return bool(port.get("collection", False))
    return False


def _types_compatible(source: str, target: str) -> bool:
    return source == target or source == "any" or target == "any"


def _safe_artifact(value: str) -> bool:
    text = str(value or "").strip().replace("\\", "/")
    if not text or PurePosixPath(text).is_absolute() or PureWindowsPath(text).is_absolute():
        return False
    return ".." not in PurePosixPath(text).parts and not text.endswith("/")


def _strings(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _positive_int(value: Any, default: int) -> int:
    return value if isinstance(value, int) and value > 0 else default


def _nonnegative_int(value: Any, default: int) -> int:
    return value if isinstance(value, int) and value >= 0 else default


def _retry_policy(config: dict[str, Any]) -> dict[str, int]:
    raw = config.get("retry_policy") if isinstance(config.get("retry_policy"), dict) else {}
    return {
        "max_attempts": _positive_int(raw.get("max_attempts"), 1),
        "backoff_seconds": _nonnegative_int(raw.get("backoff_seconds"), 0),
    }


def _input_rendering(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("input_rendering") if isinstance(config.get("input_rendering"), dict) else {}
    return {
        "preserve_user_text_verbatim": bool(raw.get("preserve_user_text_verbatim", True)),
        "binding_order": _strings(raw.get("binding_order")),
    }


def _legacy_output_type(media_type: str) -> str:
    """Derive the legacy output type from the authoritative V3 media type."""
    return {
        "text/markdown": "markdown",
        "application/json": "json",
        "text/plain": "text",
    }.get(media_type, media_type)
