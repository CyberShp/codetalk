"""Workflow definition validation for the Agent workbench."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


ALLOWED_STEP_TYPES = frozenset({
    "agent_task",
    "file_ingest",
    "diff_parse",
    "coverage_parse",
    "semantic_retrieve",
    "memory_retrieve",
    "local_scope_discover",
    "local_source_flow_sfmea_blackbox",
    "local_resource_leak_hunt",
    "local_patch_impact_review",
    "local_mr_blackbox_test",
    "evidence_validate",
    "report_render",
    "artifact_export",
})

ALLOWED_INPUT_TYPES = frozenset({
    "free_text",
    "text",
    "long_text",
    "file",
    "file_set",
    "directory",
    "diff",
    "patch",
    "coverage_report",
    "mr_link",
    "external_link",
    "git_ref",
    "semantic_library_ref",
    "agent_provider_selector",
    "mcp_profile_selector",
    "enum",
    "boolean",
    "number",
})

ALLOWED_JSON_SCHEMA_TYPES = frozenset({
    "object",
    "array",
    "string",
    "number",
    "integer",
    "boolean",
    "null",
})


class WorkflowValidationError(ValueError):
    pass


@dataclass(frozen=True)
class WorkflowInput:
    id: str
    type: str
    required: bool = False
    role: str = ""
    resolver: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowStep:
    id: str
    type: str
    goal: str = ""
    provider: str = ""
    mcp_profile: str = ""
    required_artifacts: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowOutput:
    id: str
    type: str
    source: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowDefinition:
    id: str
    name: str
    version: int
    inputs: list[WorkflowInput]
    steps: list[WorkflowStep]
    outputs: list[WorkflowOutput]
    raw: dict[str, Any] = field(default_factory=dict)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowStore:
    """Persistent store for user-editable workflow definitions."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS workflow_definitions (
                    workflow_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    definition_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def save_workflow(self, payload: dict[str, Any]) -> WorkflowDefinition:
        workflow = validate_workflow_definition(payload)
        self.initialize()
        now = _now()
        definition_json = json.dumps(workflow.raw, ensure_ascii=False, sort_keys=True)
        with self._connect() as db:
            existing = db.execute(
                "SELECT created_at FROM workflow_definitions WHERE workflow_id = ?",
                (workflow.id,),
            ).fetchone()
            created_at = str(existing["created_at"]) if existing else now
            db.execute(
                """
                INSERT OR REPLACE INTO workflow_definitions
                    (workflow_id, version, name, definition_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (workflow.id, workflow.version, workflow.name, definition_json, created_at, now),
            )
        return workflow

    def get_workflow(self, workflow_id: str) -> WorkflowDefinition:
        self.initialize()
        with self._connect() as db:
            row = db.execute(
                "SELECT definition_json FROM workflow_definitions WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
        if row is None:
            raise KeyError(workflow_id)
        return validate_workflow_definition(json.loads(str(row["definition_json"])))

    def freeze_workflow_snapshot(self, workflow_id: str) -> dict[str, Any]:
        return dict(self.get_workflow(workflow_id).raw)

    def list_workflows(self) -> list[WorkflowDefinition]:
        self.initialize()
        with self._connect() as db:
            rows = db.execute(
                "SELECT definition_json FROM workflow_definitions ORDER BY updated_at DESC"
            ).fetchall()
        return [validate_workflow_definition(json.loads(str(row["definition_json"]))) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn


def validate_workflow_definition(payload: dict[str, Any]) -> WorkflowDefinition:
    if not isinstance(payload, dict):
        raise WorkflowValidationError("workflow definition must be an object")
    workflow_id = _required_str(payload, "id")
    name = _required_str(payload, "name")
    version = payload.get("version", 1)
    if not isinstance(version, int) or version < 1:
        raise WorkflowValidationError("workflow version must be a positive integer")

    inputs = [_parse_input(item) for item in _list(payload, "inputs")]
    steps = [_parse_step(item) for item in _list(payload, "steps")]
    outputs = [_parse_output(item) for item in _list(payload, "outputs")]

    seen_inputs: set[str] = set()
    for workflow_input in inputs:
        if workflow_input.id in seen_inputs:
            raise WorkflowValidationError(f"duplicate workflow input id: {workflow_input.id}")
        seen_inputs.add(workflow_input.id)

    seen_steps: set[str] = set()
    for step in steps:
        if step.id in seen_steps:
            raise WorkflowValidationError(f"duplicate workflow step id: {step.id}")
        seen_steps.add(step.id)

    seen_outputs: set[str] = set()
    for output in outputs:
        if output.id in seen_outputs:
            raise WorkflowValidationError(f"duplicate workflow output id: {output.id}")
        seen_outputs.add(output.id)
        if output.source and _is_plain_step_reference(output.source) and output.source not in seen_steps:
            raise WorkflowValidationError(f"unknown workflow output source step: {output.source}")

    _validate_canvas_execution_contract(
        payload,
        input_ids=seen_inputs,
        step_ids=seen_steps,
        output_ids=seen_outputs,
    )

    return WorkflowDefinition(
        id=workflow_id,
        name=name,
        version=version,
        inputs=inputs,
        steps=steps,
        outputs=outputs,
        raw=dict(payload),
    )


def _validate_canvas_execution_contract(
    payload: dict[str, Any],
    *,
    input_ids: set[str],
    step_ids: set[str],
    output_ids: set[str],
) -> None:
    ui = payload.get("ui")
    if not isinstance(ui, dict):
        return
    layout = ui.get("layout")
    if not isinstance(layout, dict):
        return
    raw_nodes = layout.get("nodes") or []
    raw_edges = layout.get("edges") or []
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise WorkflowValidationError("workflow canvas nodes and edges must be lists")
    hidden_nodes = {str(value) for value in layout.get("hidden_node_ids") or []}
    hidden_edges = {str(value) for value in layout.get("hidden_edge_ids") or []}
    step_by_id = {
        str(item.get("id") or ""): item
        for item in payload.get("steps") or []
        if isinstance(item, dict) and str(item.get("id") or "")
    }
    output_by_id = {
        str(item.get("id") or ""): item
        for item in payload.get("outputs") or []
        if isinstance(item, dict) and str(item.get("id") or "")
    }
    nodes: dict[str, dict[str, Any]] = {}
    for item in raw_nodes:
        if not isinstance(item, dict):
            raise WorkflowValidationError("workflow canvas node must be an object")
        node_id = str(item.get("id") or "").strip()
        if not node_id or node_id in hidden_nodes:
            continue
        if node_id in nodes:
            raise WorkflowValidationError(f"duplicate workflow canvas node id: {node_id}")
        nodes[node_id] = item

    for node_id, node in nodes.items():
        if str(node.get("source") or "") != "canvas":
            continue
        config = node.get("config") if isinstance(node.get("config"), dict) else {}
        contract_id = _safe_canvas_contract_id(config.get("id") or node_id)
        kind = str(node.get("kind") or "context")
        expected = input_ids if kind == "input" else step_ids if kind == "agent" else output_ids if kind == "output" else None
        if expected is not None and contract_id not in expected:
            raise WorkflowValidationError(
                f"canvas {kind} node {contract_id} is missing from workflow "
                f"{'inputs' if kind == 'input' else 'steps' if kind == 'agent' else 'outputs'}"
            )

    adjacency: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for item in raw_edges:
        if not isinstance(item, dict):
            raise WorkflowValidationError("workflow canvas edge must be an object")
        edge_id = str(item.get("id") or "")
        if edge_id in hidden_edges:
            continue
        source = str(item.get("source") or "").strip()
        target = str(item.get("target") or "").strip()
        if source not in nodes or target not in nodes:
            raise WorkflowValidationError(f"workflow canvas edge {edge_id or '<unnamed>'} references an unknown node")
        adjacency[source].append(target)
        source_node = nodes[source]
        target_node = nodes[target]
        if str(source_node.get("source") or "") != "canvas" or str(target_node.get("source") or "") != "canvas":
            continue
        source_config = source_node.get("config") if isinstance(source_node.get("config"), dict) else {}
        target_config = target_node.get("config") if isinstance(target_node.get("config"), dict) else {}
        source_contract_id = _safe_canvas_contract_id(source_config.get("id") or source)
        target_contract_id = _safe_canvas_contract_id(target_config.get("id") or target)
        source_kind = str(source_node.get("kind") or "context")
        target_kind = str(target_node.get("kind") or "context")
        if source_kind == "agent" and target_kind == "agent":
            dependencies = {
                str(value) for value in step_by_id.get(target_contract_id, {}).get("depends_on") or []
            }
            if source_contract_id not in dependencies:
                raise WorkflowValidationError(
                    f"canvas edge {source_contract_id} -> {target_contract_id} is missing from step dependencies"
                )
        if source_kind == "agent" and target_kind == "output":
            output = output_by_id.get(target_contract_id, {})
            output_source = str(output.get("from") or output.get("source") or "")
            if output_source != source_contract_id:
                raise WorkflowValidationError(
                    f"canvas edge {source_contract_id} -> {target_contract_id} is missing from output source"
                )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise WorkflowValidationError("workflow canvas contains a cycle")
        if node_id in visited:
            return
        visiting.add(node_id)
        for target in adjacency.get(node_id, []):
            visit(target)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in nodes:
        visit(node_id)


def _safe_canvas_contract_id(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip()).strip("_")
    return text or "node"


def audit_workflow_definition(payload: dict[str, Any]) -> dict[str, Any]:
    workflow = validate_workflow_definition(payload)
    warnings: list[dict[str, Any]] = []
    agent_steps = [step for step in workflow.steps if step.type == "agent_task"]
    mcp_steps = [step for step in agent_steps if step.mcp_profile]

    for step in agent_steps:
        if not step.required_artifacts:
            warnings.append({
                "severity": "warning",
                "code": "agent_task_missing_required_artifacts",
                "path": f"steps.{step.id}.required_artifacts",
                "message": (
                    "Agent 节点未声明必需交付文件；CodeTalk 仍可运行，"
                    "但产物验收和证据回放能力会变弱。"
                ),
            })

    for output in workflow.outputs:
        schema = output.raw.get("schema") or output.raw.get("json_schema")
        if output.type == "json" and not isinstance(schema, dict):
            warnings.append({
                "severity": "warning",
                "code": "json_output_missing_schema",
                "path": f"outputs.{output.id}.schema",
                "message": (
                    "JSON 输出缺少 Schema；Agent 产物仍会被保存，"
                    "但结构化校验能力会受限。建议在输出模板中补充 schema。"
                ),
            })
        if "semantic_import" in output.raw and output.type != "test_cases":
            warnings.append({
                "severity": "warning",
                "code": "semantic_import_on_non_test_cases_output",
                "path": f"outputs.{output.id}.semantic_import",
                "message": (
                    "semantic_import 主要用于测试用例输出；"
                    "该输出导入语义库时可能被拒绝。"
                ),
            })
        if "evidence_memory" in output.raw and output.type != "json":
            warnings.append({
                "severity": "warning",
                "code": "evidence_memory_on_non_json_output",
                "path": f"outputs.{output.id}.evidence_memory",
                "message": (
                    "evidence_memory 主要用于 JSON 输出；"
                    "CodeTalk 只能从本地校验过的结构化 JSON 产物中固化证据。"
                ),
            })

    for workflow_input in workflow.inputs:
        if workflow_input.resolver == "agent_mcp" and not mcp_steps:
            warnings.append({
                "severity": "warning",
                "code": "agent_mcp_input_without_mcp_step",
                "path": f"inputs.{workflow_input.id}.resolver",
                "message": (
                    "该输入标记为由 Agent MCP 读取，但没有 Agent 节点声明 mcp_profile；"
                    "Agent CLI 可能无法判断应使用哪个 MCP 凭据配置。"
                ),
            })

    return {
        "status": "warning" if warnings else "ok",
        "warnings": warnings,
    }


def _parse_input(item: Any) -> WorkflowInput:
    if not isinstance(item, dict):
        raise WorkflowValidationError("workflow input must be an object")
    input_id = _required_str(item, "id")
    input_type = _required_str(item, "type")
    if input_type not in ALLOWED_INPUT_TYPES:
        raise WorkflowValidationError(f"unsupported workflow input type: {input_type}")
    resolver = str(item.get("resolver") or "")
    if resolver and resolver not in {"agent_mcp", "local", "manual"}:
        raise WorkflowValidationError(f"unsupported workflow input resolver: {resolver}")
    schema = item.get("schema") or item.get("json_schema")
    if schema is not None:
        _validate_input_schema_definition(schema)
    return WorkflowInput(
        id=input_id,
        type=input_type,
        required=bool(item.get("required", False)),
        role=str(item.get("role") or ""),
        resolver=resolver,
        raw=dict(item),
    )


def _parse_step(item: Any) -> WorkflowStep:
    if not isinstance(item, dict):
        raise WorkflowValidationError("workflow step must be an object")
    step_id = _required_str(item, "id")
    step_type = _required_str(item, "type")
    if step_type not in ALLOWED_STEP_TYPES:
        raise WorkflowValidationError(f"unsupported workflow step type: {step_type}")
    required_artifacts = [str(value) for value in item.get("required_artifacts") or []]
    for artifact in required_artifacts:
        if not _is_safe_artifact_path(artifact):
            raise WorkflowValidationError(f"unsafe required artifact path: {artifact}")
    return WorkflowStep(
        id=step_id,
        type=step_type,
        goal=str(item.get("goal") or ""),
        provider=str(item.get("provider") or ""),
        mcp_profile=str(item.get("mcp_profile") or ""),
        required_artifacts=required_artifacts,
        raw=dict(item),
    )


def _parse_output(item: Any) -> WorkflowOutput:
    if not isinstance(item, dict):
        raise WorkflowValidationError("workflow output must be an object")
    output_type = _required_str(item, "type")
    schema = item.get("schema") or item.get("json_schema")
    if schema is not None:
        if output_type != "json":
            raise WorkflowValidationError("workflow output schema requires json output type")
        _validate_output_schema_definition(schema)
    artifact_path = str(item.get("artifact") or item.get("path") or "").strip()
    if artifact_path and not _is_safe_artifact_path(artifact_path):
        raise WorkflowValidationError(f"unsafe output artifact path: {artifact_path}")
    if "semantic_import" in item:
        _validate_semantic_import_definition(item.get("semantic_import"))
    if "evidence_memory" in item:
        _validate_evidence_memory_definition(item.get("evidence_memory"))
    return WorkflowOutput(
        id=_required_str(item, "id"),
        type=output_type,
        source=str(item.get("from") or item.get("source") or ""),
        raw=dict(item),
    )


def _is_plain_step_reference(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if "{{" in text or "}}" in text:
        return False
    if "/" in text or "\\" in text:
        return False
    return True


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise WorkflowValidationError(f"workflow {key} is required")
    return value


def _is_safe_artifact_path(value: str) -> bool:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return False
    posix = PurePosixPath(text)
    windows = PureWindowsPath(text)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or windows.root:
        return False
    return not any(part in {"", ".", ".."} for part in posix.parts)


def _validate_output_schema_definition(schema: Any) -> None:
    if not isinstance(schema, dict):
        raise WorkflowValidationError("workflow output schema must be an object")
    _validate_schema_definition(schema, label="workflow output schema")


def _validate_input_schema_definition(schema: Any) -> None:
    if not isinstance(schema, dict):
        raise WorkflowValidationError("workflow input schema must be an object")
    _validate_schema_definition(schema, label="workflow input schema")


def _validate_schema_definition(schema: dict[str, Any], *, label: str) -> None:
    _validate_schema_type(schema)
    required = schema.get("required")
    if required is not None:
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise WorkflowValidationError(f"{label} required must be a list of strings")
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            raise WorkflowValidationError(f"{label} properties must be an object")
        for field_name, property_schema in properties.items():
            if not isinstance(field_name, str):
                raise WorkflowValidationError(f"{label} property names must be strings")
            if not isinstance(property_schema, dict):
                raise WorkflowValidationError(
                    f"{label} property {field_name} must be an object"
                )
            _validate_schema_type(property_schema, field_name=field_name)
    enum = schema.get("enum")
    if enum is not None and not isinstance(enum, list):
        raise WorkflowValidationError(f"{label} enum must be a list")
    min_length = schema.get("minLength")
    if min_length is not None and (not isinstance(min_length, int) or min_length < 0):
        raise WorkflowValidationError(f"{label} minLength must be a non-negative integer")


def _validate_semantic_import_definition(value: Any) -> None:
    if isinstance(value, bool):
        return
    if not isinstance(value, dict):
        raise WorkflowValidationError("workflow output semantic_import must be a boolean or object")
    if "enabled" in value and not isinstance(value.get("enabled"), bool):
        raise WorkflowValidationError("workflow output semantic_import enabled must be a boolean")
    defaults = value.get("defaults")
    if defaults is not None and not isinstance(defaults, dict):
        raise WorkflowValidationError("workflow output semantic_import defaults must be an object")


def _validate_evidence_memory_definition(value: Any) -> None:
    if isinstance(value, bool):
        return
    if not isinstance(value, dict):
        raise WorkflowValidationError("workflow output evidence_memory must be a boolean or object")
    if "enabled" in value and not isinstance(value.get("enabled"), bool):
        raise WorkflowValidationError("workflow output evidence_memory enabled must be a boolean")
    for field in (
        "kind",
        "subject_key_field",
        "subject_field",
        "id_field",
        "path_field",
        "symbol_field",
        "reason_field",
        "status",
    ):
        item = value.get(field)
        if item is not None and not isinstance(item, str):
            raise WorkflowValidationError(
                f"workflow output evidence_memory {field} must be a string"
            )
    text_fields = value.get("text_fields")
    if text_fields is not None:
        if not isinstance(text_fields, list) or not all(
            isinstance(item, str) for item in text_fields
        ):
            raise WorkflowValidationError(
                "workflow output evidence_memory text_fields must be a list of strings"
            )


def _validate_schema_type(schema: dict[str, Any], *, field_name: str = "$") -> None:
    schema_type = schema.get("type")
    if schema_type is None:
        return
    if not isinstance(schema_type, str) or schema_type not in ALLOWED_JSON_SCHEMA_TYPES:
        raise WorkflowValidationError(f"unsupported schema type for {field_name}: {schema_type}")


def _list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key) or []
    if not isinstance(value, list):
        raise WorkflowValidationError(f"workflow {key} must be a list")
    return value
