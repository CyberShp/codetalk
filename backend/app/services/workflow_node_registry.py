"""Backend-owned metadata for the workflow designer's node surface.

The authoring graph remains the persistent product contract.  This registry only
describes how each supported node kind is configured and rendered by clients.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


NODE_REGISTRY_SCHEMA_VERSION = 1


def _node(
    *,
    kind: str,
    label: str,
    palette_label: str,
    palette_group: str,
    description: str,
    default_inputs: list[dict[str, Any]] | None = None,
    default_outputs: list[dict[str, Any]] | None = None,
    default_config: dict[str, Any] | None = None,
    config_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "version": 1,
        "ui": {
            "label": label,
            "palette_label": palette_label,
            "palette_group": palette_group,
            "description": description,
        },
        "default_ports": {
            "input_ports": default_inputs or [],
            "output_ports": default_outputs or [],
        },
        "default_config": default_config or {},
        "config_schema": config_schema or {},
        "ui_schema": {
            "inspector": {
                "field_order": list((config_schema or {}).keys()),
            },
        },
    }


_PORT_LIST_SCHEMA = {
    "type": "port_list",
    "items": {
        "id": {"type": "string", "required": True},
        "label": {"type": "string"},
        "type": {"type": "port_type", "required": True},
        "required": {"type": "boolean"},
        "collection": {"type": "boolean"},
    },
}

_BUILTIN_STEP_SCHEMA = {
    "step_id": {"type": "string", "required": True, "label": "步骤 ID"},
    "timeout_sec": {"type": "integer", "minimum": 30, "label": "超时（秒）"},
    "idle_timeout_sec": {"type": "integer", "minimum": 0, "label": "无输出超时（秒）"},
    "failure_policy": {
        "type": "enum",
        "label": "失败策略",
        "options": [
            {"value": "stop", "label": "停止工作流"},
            {"value": "continue_independent", "label": "继续独立分支"},
        ],
    },
    "input_ports": _PORT_LIST_SCHEMA,
    "output_ports": _PORT_LIST_SCHEMA,
}


_NODE_REGISTRY: tuple[dict[str, Any], ...] = (
    _node(
        kind="input",
        label="输入",
        palette_label="输入模块",
        palette_group="input_output",
        description="文件、目录、链接或文字材料。",
        default_outputs=[{"id": "value", "type": "text"}],
        default_config={
            "contract_id": "input",
            "label": "新输入",
            "type": "text",
            "required": False,
            "resolver": "manual",
            "role": "",
        },
        config_schema={
            "contract_id": {"type": "string", "required": True, "label": "输入 ID"},
            "type": {"type": "port_type", "required": True, "label": "类型"},
            "resolver": {"type": "input_resolver", "required": True, "label": "获取方式"},
            "required": {"type": "boolean", "label": "必填"},
            "global_input": {"type": "boolean", "label": "工作流全局输入"},
            "role": {"type": "multiline", "label": "填写提示"},
        },
    ),
    _node(
        kind="output",
        label="输出",
        palette_label="输出模块",
        palette_group="input_output",
        description="声明正式报告和可下载交付文件。",
        default_inputs=[{"id": "value", "type": "markdown", "required": True}],
        default_config={
            "output_id": "output",
            "label": "新输出",
            "type": "markdown",
            "artifact": "output.md",
            "required": True,
        },
        config_schema={
            "output_id": {"type": "string", "required": True, "label": "输出 ID"},
            "type": {"type": "output_type", "required": True, "label": "输出类型"},
            "artifact": {"type": "artifact_name", "required": True, "label": "文件名"},
            "required": {"type": "boolean", "label": "必需交付"},
            "evidence_memory": {"type": "boolean", "label": "写入证据库"},
            "semantic_import": {"type": "boolean", "label": "导入测试语义库"},
        },
    ),
    _node(
        kind="agent",
        label="智能体",
        palette_label="智能体模块",
        palette_group="execution",
        description="调用内置模型或已配置的 Agent 完成分析和生成。",
        default_inputs=[{"id": "repo_path", "type": "directory", "required": True}],
        default_outputs=[{"id": "analysis", "type": "markdown"}],
        default_config={
            "step_id": "agent",
            "goal": "说明该节点要完成的分析目标。",
            "provider": "builtin-llm",
            "mcp_profiles": [],
            "skill_ids": [],
            "required_artifacts": [],
            "timeout_sec": 900,
            "idle_timeout_sec": 120,
            "retry_policy": {"max_attempts": 1, "backoff_seconds": 0},
            "failure_policy": "stop",
        },
        config_schema={
            "step_id": {"type": "string", "required": True, "label": "步骤 ID"},
            "goal": {"type": "multiline", "required": True, "label": "分析目标"},
            "provider": {"type": "provider", "required": True, "label": "执行器"},
            "input_ports": _PORT_LIST_SCHEMA,
            "output_ports": _PORT_LIST_SCHEMA,
            "timeout_sec": {"type": "integer", "minimum": 30, "label": "超时（秒）"},
            "idle_timeout_sec": {"type": "integer", "minimum": 0, "label": "无输出超时（秒）"},
            "failure_policy": _BUILTIN_STEP_SCHEMA["failure_policy"],
            "skill_ids": {"type": "skill_multiselect", "label": "Skills"},
            "mcp_profiles": {"type": "mcp_multiselect", "label": "MCP"},
            "required_artifacts": {"type": "artifact_list", "label": "必须生成的文件"},
        },
    ),
    _node(
        kind="semantic_retrieve",
        label="语义检索",
        palette_label="语义检索",
        palette_group="context",
        description="从测试语义库检索相关历史知识。",
        default_inputs=[{"id": "query", "type": "text", "required": True}],
        default_outputs=[{"id": "evidence", "type": "structured_json"}],
        default_config={
            "step_id": "semantic_retrieve",
            "timeout_sec": 900,
            "idle_timeout_sec": 120,
            "retry_policy": {"max_attempts": 1, "backoff_seconds": 0},
            "failure_policy": "stop",
        },
        config_schema=_BUILTIN_STEP_SCHEMA,
    ),
    _node(
        kind="memory_retrieve",
        label="证据记忆",
        palette_label="证据记忆",
        palette_group="context",
        description="检索已保存的源码证据和历史结果。",
        default_inputs=[{"id": "query", "type": "text", "required": True}],
        default_outputs=[{"id": "evidence", "type": "structured_json"}],
        default_config={
            "step_id": "memory_retrieve",
            "timeout_sec": 900,
            "idle_timeout_sec": 120,
            "retry_policy": {"max_attempts": 1, "backoff_seconds": 0},
            "failure_policy": "stop",
        },
        config_schema=_BUILTIN_STEP_SCHEMA,
    ),
    _node(
        kind="local_scope_discover",
        label="本地源码范围",
        palette_label="本地源码范围",
        palette_group="context",
        description="定位工作区内的源码、符号和测试范围。",
        default_inputs=[{"id": "repo_path", "type": "directory", "required": True}],
        default_outputs=[{"id": "source_scope", "type": "structured_json"}],
        default_config={
            "step_id": "local_scope_discover",
            "timeout_sec": 900,
            "idle_timeout_sec": 120,
            "retry_policy": {"max_attempts": 1, "backoff_seconds": 0},
            "failure_policy": "stop",
        },
        config_schema=_BUILTIN_STEP_SCHEMA,
    ),
    _node(
        kind="evidence_validate",
        label="证据校验",
        palette_label="证据校验",
        palette_group="quality",
        description="核验文件、符号、行号和事实引用。",
        default_inputs=[{"id": "claims", "type": "structured_json", "required": True}],
        default_outputs=[{"id": "validated_claims", "type": "structured_json"}],
        default_config={
            "step_id": "evidence_validate",
            "timeout_sec": 900,
            "idle_timeout_sec": 120,
            "retry_policy": {"max_attempts": 1, "backoff_seconds": 0},
            "failure_policy": "stop",
        },
        config_schema=_BUILTIN_STEP_SCHEMA,
    ),
    _node(
        kind="report_render",
        label="报告生成",
        palette_label="报告生成",
        palette_group="output",
        description="将结构化分析整理为正式报告。",
        default_inputs=[{"id": "analysis", "type": "markdown", "required": True}],
        default_outputs=[{"id": "report", "type": "markdown"}],
        default_config={
            "step_id": "report_render",
            "timeout_sec": 900,
            "idle_timeout_sec": 120,
            "retry_policy": {"max_attempts": 1, "backoff_seconds": 0},
            "failure_policy": "stop",
        },
        config_schema=_BUILTIN_STEP_SCHEMA,
    ),
    _node(
        kind="artifact_export",
        label="交付导出",
        palette_label="交付导出",
        palette_group="output",
        description="收集、整理并导出工作流交付件。",
        default_inputs=[{"id": "artifact", "type": "artifact_ref", "required": True}],
        default_outputs=[{"id": "export", "type": "artifact_ref"}],
        default_config={
            "step_id": "artifact_export",
            "timeout_sec": 900,
            "idle_timeout_sec": 120,
            "retry_policy": {"max_attempts": 1, "backoff_seconds": 0},
            "failure_policy": "stop",
        },
        config_schema=_BUILTIN_STEP_SCHEMA,
    ),
)


SUPPORTED_NODE_KINDS = frozenset(item["kind"] for item in _NODE_REGISTRY)


_PHASE3_EXECUTABLE_KINDS = frozenset({"input", "output", "agent"})
_TECHNICAL_CONFIG_FIELDS = frozenset({"contract_id", "input_id", "output_id", "step_id", "input_ports", "output_ports"})


def node_registry_payload(*, schema_version: int = 3) -> dict[str, Any]:
    """Return the authoring palette for one graph generation.

    V2 remains readable through an explicit legacy request.  The default is the
    Canvas First V3 palette and deliberately exposes only nodes whose handlers
    exist in this release.
    """
    if schema_version == 2:
        return {
            "schema_version": NODE_REGISTRY_SCHEMA_VERSION,
            "nodes": deepcopy(list(_NODE_REGISTRY)),
        }
    if schema_version != 3:
        raise ValueError(f"Unsupported node registry schema_version: {schema_version}")
    nodes = [_phase3_node(item) for item in _NODE_REGISTRY if item["kind"] in _PHASE3_EXECUTABLE_KINDS]
    return {
        "schema_version": NODE_REGISTRY_SCHEMA_VERSION,
        "authoring_schema_version": 3,
        "nodes": nodes,
    }


def node_definition(kind: str) -> dict[str, Any] | None:
    for item in _NODE_REGISTRY:
        if item["kind"] == kind:
            return deepcopy(item)
    return None


def executable_node_definition(kind: str) -> dict[str, Any] | None:
    """Return a Phase 3 palette definition only when the node can execute."""
    if kind not in _PHASE3_EXECUTABLE_KINDS:
        return None
    definition = node_definition(kind)
    return _phase3_node(definition) if definition else None


def _phase3_node(source: dict[str, Any]) -> dict[str, Any]:
    node = deepcopy(source)
    config_schema = node.get("config_schema") if isinstance(node.get("config_schema"), dict) else {}
    node["config_schema"] = {
        key: value for key, value in config_schema.items() if key not in _TECHNICAL_CONFIG_FIELDS
    }
    if node["kind"] == "input":
        if "type" in node["config_schema"]:
            node["config_schema"]["type"]["label"] = "输入类型"
        if "required" in node["config_schema"]:
            node["config_schema"]["required"]["label"] = "是否必填"
    elif node["kind"] == "output":
        if "type" in node["config_schema"]:
            node["config_schema"]["type"]["label"] = "输出类型"
        if "required" in node["config_schema"]:
            node["config_schema"]["required"]["label"] = "必须交付"
    inspector = node.get("ui_schema", {}).get("inspector", {})
    node.setdefault("ui_schema", {}).setdefault("inspector", {})["field_order"] = [
        key for key in inspector.get("field_order", list(node["config_schema"]))
        if key in node["config_schema"]
    ]
    if node["kind"] in {"input", "output"}:
        node["execution"] = {
            "available": True,
            "handler_id": None,
            "handler_version": None,
        }
    else:
        node["execution"] = {
            "available": True,
            "handler_id": "agent",
            "handler_version": 1,
        }
    return node
