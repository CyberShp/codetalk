"""Workflow definition validation for the Agent workbench."""

from __future__ import annotations

import json
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

    return WorkflowDefinition(
        id=workflow_id,
        name=name,
        version=version,
        inputs=inputs,
        steps=steps,
        outputs=outputs,
        raw=dict(payload),
    )


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
                    "Agent task has no required_artifacts; CodeTalk can run it, "
                    "but artifact validation and evidence replay will be weak."
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
                    "JSON output has no schema; Agent output can still be captured, "
                    "but structured validation will be limited."
                ),
            })
        if "semantic_import" in output.raw and output.type != "test_cases":
            warnings.append({
                "severity": "warning",
                "code": "semantic_import_on_non_test_cases_output",
                "path": f"outputs.{output.id}.semantic_import",
                "message": (
                    "semantic_import is intended for test_cases outputs; CodeTalk may reject "
                    "this output during semantic library import."
                ),
            })

    for workflow_input in workflow.inputs:
        if workflow_input.resolver == "agent_mcp" and not mcp_steps:
            warnings.append({
                "severity": "warning",
                "code": "agent_mcp_input_without_mcp_step",
                "path": f"inputs.{workflow_input.id}.resolver",
                "message": (
                    "Input is marked agent_mcp, but no agent_task declares an mcp_profile; "
                    "Agent CLI may not know which MCP credential profile to use."
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
