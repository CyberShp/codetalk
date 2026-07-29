"""Immutable V3 workflow contracts for new canvas-authored workflows.

This module is intentionally pure: callers provide the handler capability snapshot
that is used for validation, and the compiled result carries every field a runner
needs.  It does not consult a live registry or create another runtime state store.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from app.services.workflow_output_presets import selected_output_content_presets


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
_PROFILE_NODE_SPECS = {
    "none": (),
    "artifact_only": (("artifact_exists", "validator"),),
    "schema": (("artifact_exists", "validator"), ("json_schema", "validator")),
    "source_evidence": (
        ("artifact_exists", "validator"),
        ("source_evidence", "validator"),
    ),
    "storage_test_design": (
        ("artifact_exists", "validator"),
        ("sfmea", "validator"),
        ("black_box", "validator"),
    ),
    "formal_release": (
        ("artifact_exists", "validator"),
        ("sfmea", "validator"),
        ("black_box", "validator"),
        ("independent_review", "validator"),
        ("human_approval", "human_approval"),
    ),
}
_PROFILE_REQUIRED_OUTPUT_ROLES = {
    "source_evidence": ("source_evidence",),
    "storage_test_design": ("sfmea", "black_box"),
    "formal_release": ("sfmea", "black_box", "independent_review"),
}
_JSON_OUTPUT_HANDLERS = frozenset({
    "json_schema",
    "source_evidence",
    "sfmea",
    "black_box",
    "black_box_cases",
})
_PROFESSIONAL_ARRAY_HANDLERS = frozenset({
    "source_evidence",
    "sfmea",
    "black_box",
    "black_box_cases",
})
_GOVERNANCE_OUTPUT_REQUIREMENTS = {
    "storage_test_design": {
        "sfmea": "sfmea",
        "black_box_cases": "black_box_cases",
    },
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
    _validate_required_input_bindings(nodes, edges, errors)
    _validate_handler_port_contracts(nodes, capabilities, errors)
    profile = _profile(graph, errors)
    outputs = _declared_outputs(nodes, edges, errors)
    _validate_profile_output_bindings(profile, outputs, errors)
    _validate_explicit_validators(nodes, outputs, errors)
    _validate_publish_output_compatibility(nodes, profile, outputs, errors)
    _validate_governance_outputs(nodes, edges, errors)

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
    profile_nodes = _profile_nodes(profile, outputs, executable_nodes)
    all_nodes = executable_nodes + profile_nodes
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


def _validate_required_input_bindings(
    nodes: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> None:
    """Every required executable input must resolve before publish or compile."""
    bound = {
        (
            str((edge.get("target") or {}).get("node_id") or ""),
            str((edge.get("target") or {}).get("port_id") or ""),
        )
        for edge in edges
        if edge.get("kind", "data") == "data"
    }
    for node_id, node in sorted(nodes.items()):
        if node.get("kind") in {"input", "output"}:
            continue
        for port in _ports(node, "inputs"):
            if not bool(port.get("required", False)):
                continue
            port_id = str(port.get("id") or "")
            if (node_id, port_id) in bound:
                continue
            binding_key = str(port.get("binding_key") or "")
            label = str(
                port.get("label")
                or {"source_evidence": "源码证据"}.get(binding_key)
                or binding_key
                or port_id
                or "未命名输入"
            )
            errors.append(_issue(
                "required_input_unbound",
                f"必填输入“{label}”未连接，请从上游节点连接后再发布。",
                node_id=node_id,
                field=port_id,
                binding_key=binding_key or None,
            ))


def _validate_handler_port_contracts(
    nodes: dict[str, dict[str, Any]],
    capabilities: dict[str, Any] | None,
    errors: list[dict[str, Any]],
) -> None:
    """Validator/Governance ports are owned by their registered handler."""
    handlers = _handler_capabilities(capabilities)
    for node_id, node in sorted(nodes.items()):
        kind = str(node.get("kind") or "")
        if kind not in {"validator", "governance"}:
            continue
        handler_id = str(_config(node).get("handler_id") or "")
        descriptor = handlers.get(handler_id)
        if not isinstance(descriptor, dict) or descriptor.get("kind") != kind:
            continue
        for direction, descriptor_key in (
            ("inputs", "input_ports"),
            ("outputs", "output_ports"),
        ):
            expected = _semantic_port_contract(descriptor.get(descriptor_key))
            actual = _semantic_port_contract(_ports(node, direction), graph_ports=True)
            if expected == actual:
                continue
            errors.append(_issue(
                "handler_port_contract_mismatch",
                "处理器端口契约已由系统注册，不能增加、删除或修改语义、类型、必填和集合属性。",
                node_id=node_id,
                field=direction,
                handler_id=handler_id,
                expected_ports=expected,
                actual_ports=actual,
            ))


def _semantic_port_contract(value: Any, *, graph_ports: bool = False) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        key = str(item.get("binding_key" if graph_ports else "key") or "").strip()
        result.append({
            "key": key,
            "type": str(item.get("type") or ""),
            "required": bool(item.get("required", False)),
            "collection": bool(item.get("collection", False)),
        })
    return sorted(result, key=lambda item: (item["key"], item["type"]))


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
        producer_port_key = _port_binding_key(
            nodes[producer[0]],
            "outputs",
            producer[1],
        )
        content_presets = selected_output_content_presets(
            config.get("content_preset_ids") or config.get("content_presets")
        )
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
            **(
                {"validation_roles": sorted(set(_strings(config.get("validation_roles"))))}
                if _strings(config.get("validation_roles"))
                else {}
            ),
            **({"content_presets": content_presets} if content_presets else {}),
            "producer_step_id": producer[0],
            "producer_port_id": producer[1],
            "producer_port_key": producer_port_key,
            "from": producer[0],
        })
    return sorted(result, key=lambda item: item["output_id"])


def _validate_profile_output_bindings(
    profile: str,
    outputs: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> None:
    """Professional shortcuts bind through explicit output roles, never names."""
    for handler_id in _PROFILE_REQUIRED_OUTPUT_ROLES.get(profile, ()):
        selected = [
            item
            for item in outputs
            if handler_id in _strings(item.get("validation_roles"))
        ]
        if not selected:
            message = (
                "源码证据验收需要一个显式标记 validation_roles: "
                "[\"source_evidence\"] 的声明输出，请连接证据 Artifact 后再发布。"
                if handler_id == "source_evidence"
                else f"Validation profile requires an output bound to {handler_id}"
            )
            errors.append(_issue(
                "profile_output_binding_missing",
                message,
                field="validation_roles",
                handler_id=handler_id,
            ))
        elif handler_id in {"sfmea", "black_box"} and len(selected) != 1:
            errors.append(_issue(
                "profile_output_binding_ambiguous",
                f"Validation profile requires exactly one output bound to {handler_id}",
                field="validation_roles",
                handler_id=handler_id,
            ))


def _output_producer(
    output_node_id: str,
    edges: list[dict[str, Any]],
    nodes: dict[str, dict[str, Any]],
) -> tuple[str, str] | None:
    producers: list[tuple[str, str]] = []
    for edge in edges:
        target = edge.get("target") if isinstance(edge.get("target"), dict) else {}
        source = edge.get("source") if isinstance(edge.get("source"), dict) else {}
        if str(target.get("node_id") or "") != output_node_id:
            continue
        source_id = str(source.get("node_id") or "")
        if source_id in nodes and nodes[source_id].get("kind") not in {"input", "output"}:
            producers.append((source_id, str(source.get("port_id") or "")))
    return sorted(producers)[0] if len(producers) == 1 else None


def _validate_explicit_validators(
    nodes: dict[str, dict[str, Any]],
    outputs: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> None:
    declared = {item["output_id"] for item in outputs}
    for node_id, node in sorted(nodes.items()):
        if node.get("kind") != "validator":
            continue
        config = _config(node)
        required_outputs = config.get("required_outputs")
        if _ports(node, "outputs"):
            errors.append(_issue(
                "validator_output_ports_forbidden",
                "Validator nodes are read-only and cannot declare output ports",
                node_id=node_id,
                field="outputs",
            ))
        if not isinstance(required_outputs, list):
            errors.append(_issue("validator_required_outputs_invalid", "Validator required_outputs must be an array", node_id=node_id, field="required_outputs"))
            continue
        if not required_outputs:
            errors.append(_issue(
                "validator_required_outputs_empty",
                "Validator 至少选择一个已声明交付件；请在节点属性的“验收交付件”中完成选择。",
                node_id=node_id,
                field="required_outputs",
            ))
            continue
        for output_id in sorted({str(item) for item in required_outputs} - declared):
            errors.append(_issue("validator_output_not_declared", f"Validator requires undeclared output: {output_id}", node_id=node_id, field="required_outputs", output_id=output_id))


def _validate_publish_output_compatibility(
    nodes: dict[str, dict[str, Any]],
    profile: str,
    outputs: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> None:
    """Apply one output contract gate to explicit and Profile-generated handlers."""
    output_by_id = {item["output_id"]: item for item in outputs}
    output_node_ids = {
        str(_config(node).get("output_id") or node_id): node_id
        for node_id, node in nodes.items()
        if node.get("kind") == "output"
    }
    checked: set[tuple[str, str]] = set()
    for requirement in _collect_output_contract_requirements(nodes, profile, outputs):
        handler_id = requirement["handler_id"]
        explicit_node_id = requirement["node_id"]
        required_outputs = requirement["output_ids"]
        if handler_id not in _JSON_OUTPUT_HANDLERS:
            continue
        for output_id in sorted(required_outputs & set(output_by_id)):
            if (handler_id, output_id) in checked:
                continue
            checked.add((handler_id, output_id))
            output = output_by_id[output_id]
            issue_node_id = explicit_node_id or output_node_ids.get(output_id)
            schema_problem = _json_schema_definition_problem(output.get("schema"))

            if handler_id == "json_schema" and schema_problem:
                errors.append(_issue(
                    "json_schema_required_output_schema_invalid",
                    (
                        f"JSON 结构校验所验收的交付件“{output['label']}”缺少有效的 JSON Schema；"
                        "请在输出节点选择 JSON 类型并配置结构规则。"
                    ),
                    node_id=issue_node_id,
                    field="required_outputs",
                    output_id=output_id,
                    handler_id=handler_id,
                    schema_error=schema_problem,
                ))
                continue

            if not _json_compatible_media_type(output.get("media_type")):
                errors.append(_issue(
                    "validator_output_media_type_incompatible",
                    (
                        f"交付件“{output['label']}”必须使用 JSON 数据格式，才能执行"
                        f"“{_validator_label(handler_id)}”；请在输出节点将输出类型改为 JSON。"
                    ),
                    node_id=issue_node_id,
                    field="media_type",
                    output_id=output_id,
                    handler_id=handler_id,
                    media_type=output.get("media_type"),
                ))
                continue

            if handler_id in _PROFESSIONAL_ARRAY_HANDLERS and (
                schema_problem or output.get("schema", {}).get("type") != "array"
            ):
                errors.append(_issue(
                    "professional_output_schema_incompatible",
                    (
                        f"交付件“{output['label']}”需要有效的 JSON 数组结构规则，才能执行"
                        f"“{_validator_label(handler_id)}”；请在输出节点将结构规则设为 JSON 数组。"
                    ),
                    node_id=issue_node_id,
                    field="schema",
                    output_id=output_id,
                    handler_id=handler_id,
                    schema_error=schema_problem or "$.type must be array",
                ))


def _collect_output_contract_requirements(
    nodes: dict[str, dict[str, Any]],
    profile: str,
    outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collect every publish-time output requirement from semantic authorities.

    Validator nodes bind declared output IDs directly. Profile-generated validators
    bind explicit output roles. Governance nodes bind through their frozen handler
    and producer-port semantic key. Labels and artifact filenames are deliberately
    excluded from all three paths.
    """
    requirements: list[dict[str, Any]] = []
    for node_id, node in sorted(nodes.items()):
        config = _config(node)
        if node.get("kind") == "validator":
            requirements.append({
                "handler_id": str(
                    config.get("handler_id") or config.get("validator_type") or ""
                ),
                "node_id": node_id,
                "output_ids": set(_strings(config.get("required_outputs"))),
            })
            continue
        if node.get("kind") != "governance":
            continue
        handler_id = str(config.get("handler_id") or "")
        semantic_requirements = _GOVERNANCE_OUTPUT_REQUIREMENTS.get(handler_id, {})
        for output in outputs:
            if output.get("producer_step_id") != node_id:
                continue
            producer_port_key = str(output.get("producer_port_key") or "")
            professional_handler = semantic_requirements.get(producer_port_key)
            if professional_handler:
                requirements.append({
                    "handler_id": professional_handler,
                    "node_id": None,
                    "output_ids": {output["output_id"]},
                })

    for handler_id, kind, required_outputs in _profile_handler_outputs(profile, outputs):
        if kind == "validator":
            requirements.append({
                "handler_id": handler_id,
                "node_id": None,
                "output_ids": set(required_outputs),
            })
    return requirements


def _json_compatible_media_type(value: Any) -> bool:
    media_type = str(value or "").split(";", 1)[0].strip().lower()
    return media_type == "application/json" or (
        media_type.startswith("application/") and media_type.endswith("+json")
    )


def _validator_label(handler_id: str) -> str:
    return {
        "json_schema": "JSON 结构校验",
        "source_evidence": "源码证据验收",
        "sfmea": "SFMEA 验收",
        "black_box": "黑盒测试验收",
        "black_box_cases": "黑盒测试验收",
    }.get(handler_id, handler_id)


def _json_schema_definition_problem(
    schema: Any, *, path: str = "$", require_non_empty: bool = True
) -> str:
    """Validate the JSON Schema subset supported by the runtime Validator."""
    if not isinstance(schema, dict):
        return f"{path} must be an object"
    if require_non_empty and not schema:
        return f"{path} must be a non-empty object"
    allowed_types = {"object", "array", "string", "integer", "number", "boolean", "null"}
    schema_type = schema.get("type")
    if require_non_empty and schema_type is None:
        return f"{path}.type is required"
    if schema_type is not None and schema_type not in allowed_types:
        return f"{path}.type is unsupported"
    if "required" in schema and not (
        isinstance(schema["required"], list)
        and all(isinstance(item, str) for item in schema["required"])
    ):
        return f"{path}.required must be a string array"
    for keyword in ("minLength", "maxLength", "minItems", "maxItems"):
        if keyword in schema and not (
            isinstance(schema[keyword], int)
            and not isinstance(schema[keyword], bool)
            and schema[keyword] >= 0
        ):
            return f"{path}.{keyword} must be a non-negative integer"
    for keyword in ("minimum", "maximum"):
        if keyword in schema and not (
            isinstance(schema[keyword], (int, float))
            and not isinstance(schema[keyword], bool)
        ):
            return f"{path}.{keyword} must be a number"
    if "enum" in schema and not (
        isinstance(schema["enum"], list) and bool(schema["enum"])
    ):
        return f"{path}.enum must be a non-empty array"
    additional = schema.get("additionalProperties", True)
    if not isinstance(additional, (bool, dict)):
        return f"{path}.additionalProperties must be a boolean or object"
    if isinstance(additional, dict):
        problem = _json_schema_definition_problem(
            additional,
            path=f"{path}.additionalProperties",
            require_non_empty=False,
        )
        if problem:
            return problem
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return f"{path}.properties must be an object"
    for key, child in properties.items():
        if not isinstance(child, dict):
            return f"{path}.properties.{key} must be an object"
        problem = _json_schema_definition_problem(
            child, path=f"{path}.properties.{key}", require_non_empty=False
        )
        if problem:
            return problem
    if "items" in schema:
        if not isinstance(schema["items"], dict):
            return f"{path}.items must be an object"
        return _json_schema_definition_problem(
            schema["items"], path=f"{path}.items", require_non_empty=False
        )
    return ""


def _validate_governance_outputs(
    nodes: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> None:
    """Require every generated Governance port to map to one declared Output."""
    for node_id, node in sorted(nodes.items()):
        if node.get("kind") != "governance":
            continue
        output_ports = _ports(node, "outputs")
        if not output_ports:
            errors.append(_issue(
                "governance_output_ports_required",
                "Governance nodes must declare at least one generated output port",
                node_id=node_id,
                field="outputs",
            ))
            continue
        for port in output_ports:
            if not bool(port.get("required", False)):
                continue
            port_id = str(port.get("id") or "")
            declarations = [
                edge for edge in edges
                if edge.get("kind", "data") == "data"
                and str((edge.get("source") or {}).get("node_id") or "") == node_id
                and str((edge.get("source") or {}).get("port_id") or "") == port_id
                and nodes.get(str((edge.get("target") or {}).get("node_id") or ""), {}).get("kind") == "output"
            ]
            if not declarations:
                errors.append(_issue(
                    "governance_output_not_declared",
                    f"Governance output must connect to a declared Output: {port_id}",
                    node_id=node_id,
                    field=port_id,
                ))
            elif len(declarations) > 1:
                errors.append(_issue(
                    "governance_output_multiple_declarations",
                    f"Governance output must have exactly one declared Output: {port_id}",
                    node_id=node_id,
                    field=port_id,
                ))


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
        if node.get("kind") not in {"agent", "builtin_model", "subagent"}:
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
    for handler_id, _kind, _required_outputs in _profile_handler_outputs(profile, outputs):
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
    output_by_id = {item["output_id"]: item for item in outputs}
    for node_id in executable_ids:
        if nodes[node_id].get("kind") != "validator":
            continue
        for output_id in _strings(_config(nodes[node_id]).get("required_outputs")):
            output = output_by_id.get(output_id)
            if output and output["producer_step_id"] != node_id:
                dependencies[node_id].add(output["producer_step_id"])
    ordered_ids = _topological_order(executable_ids, dependencies)
    output_ids_by_producer: dict[str, list[str]] = defaultdict(list)
    for output in outputs:
        output_ids_by_producer[output["producer_step_id"]].append(output["output_id"])
    result: list[dict[str, Any]] = []
    for node_id in ordered_ids:
        node = nodes[node_id]
        produced_outputs = sorted(output_ids_by_producer[node_id])
        required_outputs = (
            sorted(set(_strings(_config(node).get("required_outputs"))))
            if node.get("kind") == "validator"
            else produced_outputs
        )
        result.append(_compiled_node(
            node,
            node_id,
            sorted(dependencies[node_id]),
            _bindings(node_id, edges, nodes),
            required_outputs,
        ))
    return result


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
    compiled = {
        "node_id": node_id,
        "graph_node_id": node_id,
        "kind": str(node.get("kind")),
        # The compiled plan is frozen with the graph's presentation label so
        # run summaries never need to recover it from mutable authoring data.
        "label": str(node.get("label") or "").strip(),
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
        "blocking": bool(config.get("blocking", True)),
        "required_outputs": required_outputs,
    }
    if node.get("kind") == "tool":
        compiled["tool_id"] = str(config.get("tool_id") or "")
        compiled["required_permissions"] = sorted(_strings(config.get("required_permissions")))
    elif node.get("kind") == "subagent":
        compiled["session_key"] = str(config.get("session_key") or "")
    elif node.get("kind") == "human_approval":
        compiled["approval_timeout_sec"] = _positive_int(
            config.get("approval_timeout_sec"), 86400
        )
    return compiled


def _profile_nodes(profile: str, outputs: list[dict[str, Any]], prior_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    generated_nodes: list[dict[str, Any]] = []
    output_by_id = {item["output_id"]: item for item in outputs}
    explicit_validators = {
        (
            str(node.get("handler_id") or ""),
            tuple(sorted(_strings(node.get("required_outputs")))),
        )
        for node in prior_nodes
        if node.get("kind") == "validator"
    }
    previous_profile_node_id: str | None = None
    for handler_id, kind, required_outputs in _profile_handler_outputs(profile, outputs):
        if not required_outputs and handler_id not in {"sfmea", "black_box", "independent_review", "human_approval"}:
            continue
        if kind == "validator" and (
            handler_id,
            tuple(sorted(required_outputs)),
        ) in explicit_validators:
            continue
        node_id = f"profile_{handler_id}_" + ("_".join(required_outputs) or "workflow")
        depends_on = sorted({output_by_id[item]["producer_step_id"] for item in required_outputs if item in output_by_id})
        if prior_nodes and not depends_on:
            depends_on = [prior_nodes[-1]["node_id"]]
        if previous_profile_node_id:
            depends_on = sorted(set(depends_on) | {previous_profile_node_id})
        input_ports: list[dict[str, Any]] = []
        resolved_input_bindings: dict[str, dict[str, str]] = {}
        if handler_id == "source_evidence":
            for output_id in required_outputs:
                output = output_by_id[output_id]
                input_ports.append({
                    "id": output_id,
                    "binding_key": "source_evidence",
                    "type": "artifact",
                    "required": True,
                })
                resolved_input_bindings[output_id] = {
                    "source_node_id": output["producer_step_id"],
                    "source_port_id": output["producer_port_id"],
                    "source_output_id": output_id,
                }
        generated_nodes.append({
            "node_id": node_id,
            "graph_node_id": None,
            "kind": kind,
            "handler_id": handler_id,
            "handler_version": 1,
            "depends_on": depends_on,
            "resolved_input_bindings": resolved_input_bindings,
            "input_ports": input_ports,
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
            "blocking": True,
            "required_outputs": required_outputs,
            "generated_by_validation_profile": True,
            "validation_profile": profile,
        })
        if kind == "human_approval":
            generated_nodes[-1]["approval_timeout_sec"] = 86400
        previous_profile_node_id = node_id
    return generated_nodes


def _profile_handler_outputs(
    profile: str, outputs: list[dict[str, Any]]
) -> list[tuple[str, str, list[str]]]:
    required = [item["output_id"] for item in outputs if item["required"]]
    with_schema = [item["output_id"] for item in outputs if item.get("schema") is not None]
    result: list[tuple[str, str, list[str]]] = []
    for handler_id, kind in _PROFILE_NODE_SPECS.get(profile, ()):
        if handler_id == "json_schema":
            result.append((handler_id, kind, with_schema))
        elif handler_id in {
            "source_evidence",
            "sfmea",
            "black_box",
            "independent_review",
        }:
            result.append((
                handler_id,
                kind,
                [
                    item["output_id"]
                    for item in outputs
                    if handler_id in _strings(item.get("validation_roles"))
                ],
            ))
        elif handler_id == "human_approval":
            result.append((handler_id, kind, required))
        else:
            result.append((handler_id, kind, required))
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
        "label": node["label"],
        "name": node["label"],
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
    # Provider-backed agent and subagent nodes share the established scheduler
    # alias; other V3 node definitions remain unchanged.
    return {**node, "type": runner_type} if runner_type != node["kind"] else dict(node)


def _runner_type(kind: str) -> str:
    return "agent_task" if kind in {"agent", "builtin_model", "subagent"} else kind


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
        {
            key: item[key]
            for key in (
                "id",
                "label",
                "type",
                "required",
                "collection",
                "binding_key",
            )
            if key in item
        }
        for item in raw if isinstance(item, dict) and item.get("id")
    ]


def _port_type(node: dict[str, Any], direction: str, port_id: str) -> str | None:
    for port in _ports(node, direction):
        if port.get("id") == port_id:
            return str(port.get("type") or "")
    return None


def _port_binding_key(node: dict[str, Any], direction: str, port_id: str) -> str:
    for port in _ports(node, direction):
        if port.get("id") == port_id:
            return str(port.get("binding_key") or port_id)
    return str(port_id)


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
