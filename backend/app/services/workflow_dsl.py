"""Workflow definition validation for the Agent workbench."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
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

    seen_steps: set[str] = set()
    for step in steps:
        if step.id in seen_steps:
            raise WorkflowValidationError(f"duplicate workflow step id: {step.id}")
        seen_steps.add(step.id)

    return WorkflowDefinition(
        id=workflow_id,
        name=name,
        version=version,
        inputs=inputs,
        steps=steps,
        outputs=outputs,
        raw=dict(payload),
    )


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
    return WorkflowStep(
        id=step_id,
        type=step_type,
        goal=str(item.get("goal") or ""),
        provider=str(item.get("provider") or ""),
        mcp_profile=str(item.get("mcp_profile") or ""),
        required_artifacts=[str(value) for value in item.get("required_artifacts") or []],
        raw=dict(item),
    )


def _parse_output(item: Any) -> WorkflowOutput:
    if not isinstance(item, dict):
        raise WorkflowValidationError("workflow output must be an object")
    return WorkflowOutput(
        id=_required_str(item, "id"),
        type=_required_str(item, "type"),
        source=str(item.get("from") or item.get("source") or ""),
        raw=dict(item),
    )


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise WorkflowValidationError(f"workflow {key} is required")
    return value


def _list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key) or []
    if not isinstance(value, list):
        raise WorkflowValidationError(f"workflow {key} must be a list")
    return value
