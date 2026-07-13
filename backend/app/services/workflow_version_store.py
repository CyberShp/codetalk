"""Immutable workflow headers and versions for Workbench V2."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORKFLOW_SCHEMA_VERSION = 1
_WORKFLOW_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class WorkflowVersionError(ValueError):
    pass


class WorkflowDraftExistsError(WorkflowVersionError):
    pass


class PublishedWorkflowVersionError(WorkflowVersionError):
    pass


@dataclass(frozen=True)
class WorkflowHeader:
    workflow_id: str
    name: str
    description: str
    status: str
    published_version_id: str | None
    current_draft_version_id: str | None
    created_at: str
    updated_at: str
    archived_at: str | None


@dataclass(frozen=True)
class WorkflowVersion:
    version_id: str
    workflow_id: str
    version_number: int
    state: str
    authoring_graph: dict[str, Any]
    compiled_definition: dict[str, Any] | None
    compiled_plan: dict[str, Any] | None
    validation: dict[str, Any] | None
    based_on_version_id: str | None
    created_at: str
    updated_at: str
    published_at: str | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_version_id() -> str:
    return f"wfv_{uuid.uuid4().hex}"


class WorkflowVersionStore:
    """SQLite store that keeps published workflow versions immutable."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize_and_migrate(self) -> dict[str, int]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        migrated = 0
        with self._connect() as db:
            db.executescript(_SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            try:
                if self._table_exists(db, "workflow_definitions"):
                    rows = db.execute(
                        """
                        SELECT workflow_id, name, definition_json, created_at, updated_at
                        FROM workflow_definitions
                        ORDER BY created_at, workflow_id
                        """
                    ).fetchall()
                    for row in rows:
                        exists = db.execute(
                            "SELECT 1 FROM workflow_headers WHERE workflow_id = ?",
                            (str(row["workflow_id"]),),
                        ).fetchone()
                        if exists:
                            continue
                        self._migrate_legacy_row(db, row)
                        migrated += 1
                db.execute(
                    """
                    INSERT INTO workbench_schema_meta(component, version, updated_at)
                    VALUES ('workflow_versions', ?, ?)
                    ON CONFLICT(component) DO UPDATE SET
                        version = excluded.version,
                        updated_at = excluded.updated_at
                    """,
                    (WORKFLOW_SCHEMA_VERSION, _now()),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {"schema_version": WORKFLOW_SCHEMA_VERSION, "migrated_workflows": migrated}

    def create_workflow(
        self,
        *,
        workflow_id: str,
        name: str,
        description: str,
        authoring_graph: dict[str, Any],
    ) -> tuple[WorkflowHeader, WorkflowVersion]:
        workflow_id = _validated_workflow_id(workflow_id)
        name = _required_text(name, "name")
        graph = _json_object(authoring_graph, "authoring_graph")
        self.initialize_and_migrate()
        now = _now()
        version_id = _new_version_id()
        with self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    """
                    INSERT INTO workflow_headers(
                        workflow_id, name, description, status,
                        published_version_id, current_draft_version_id,
                        created_at, updated_at, archived_at
                    ) VALUES (?, ?, ?, 'active', NULL, ?, ?, ?, NULL)
                    """,
                    (workflow_id, name, str(description or ""), version_id, now, now),
                )
                db.execute(
                    """
                    INSERT INTO workflow_versions(
                        version_id, workflow_id, version_number, state,
                        authoring_graph_json, compiled_definition_json,
                        compiled_plan_json, validation_json, based_on_version_id,
                        created_at, updated_at, published_at
                    ) VALUES (?, ?, 1, 'draft', ?, NULL, NULL, NULL, NULL, ?, ?, NULL)
                    """,
                    (version_id, workflow_id, _dump(graph), now, now),
                )
                db.commit()
            except sqlite3.IntegrityError as exc:
                db.rollback()
                raise WorkflowVersionError(f"workflow already exists: {workflow_id}") from exc
        return self.get_workflow(workflow_id), self.get_version(version_id)

    def get_workflow(self, workflow_id: str) -> WorkflowHeader:
        self.initialize_and_migrate()
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM workflow_headers WHERE workflow_id = ?",
                (_validated_workflow_id(workflow_id),),
            ).fetchone()
        if row is None:
            raise KeyError(workflow_id)
        return _header_from_row(row)

    def list_workflows(self, *, include_archived: bool = False) -> list[WorkflowHeader]:
        self.initialize_and_migrate()
        sql = "SELECT * FROM workflow_headers"
        if not include_archived:
            sql += " WHERE status != 'archived'"
        sql += " ORDER BY updated_at DESC, workflow_id"
        with self._connect() as db:
            rows = db.execute(sql).fetchall()
        return [_header_from_row(row) for row in rows]

    def update_workflow(
        self,
        workflow_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> WorkflowHeader:
        current = self.get_workflow(workflow_id)
        next_name = current.name if name is None else _required_text(name, "name")
        next_description = current.description if description is None else str(description)
        with self._connect() as db:
            db.execute(
                """
                UPDATE workflow_headers
                SET name = ?, description = ?, updated_at = ?
                WHERE workflow_id = ?
                """,
                (next_name, next_description, _now(), current.workflow_id),
            )
        return self.get_workflow(current.workflow_id)

    def archive_workflow(self, workflow_id: str) -> WorkflowHeader:
        current = self.get_workflow(workflow_id)
        now = _now()
        with self._connect() as db:
            db.execute(
                """
                UPDATE workflow_headers
                SET status = 'archived', archived_at = ?, updated_at = ?
                WHERE workflow_id = ?
                """,
                (now, now, current.workflow_id),
            )
        return self.get_workflow(current.workflow_id)

    def create_draft(
        self,
        workflow_id: str,
        *,
        based_on_version_id: str | None = None,
    ) -> WorkflowVersion:
        header = self.get_workflow(workflow_id)
        if header.current_draft_version_id:
            raise WorkflowDraftExistsError(
                f"workflow already has a current draft: {header.current_draft_version_id}"
            )
        base_id = based_on_version_id or header.published_version_id
        base = self.get_version(base_id) if base_id else None
        if base is not None and base.workflow_id != header.workflow_id:
            raise WorkflowVersionError("based_on_version_id belongs to another workflow")
        version_number = self._next_version_number(header.workflow_id)
        version_id = _new_version_id()
        now = _now()
        graph = base.authoring_graph if base else _empty_graph(header)
        with self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    """
                    INSERT INTO workflow_versions(
                        version_id, workflow_id, version_number, state,
                        authoring_graph_json, compiled_definition_json,
                        compiled_plan_json, validation_json, based_on_version_id,
                        created_at, updated_at, published_at
                    ) VALUES (?, ?, ?, 'draft', ?, NULL, NULL, NULL, ?, ?, ?, NULL)
                    """,
                    (
                        version_id,
                        header.workflow_id,
                        version_number,
                        _dump(graph),
                        base.version_id if base else None,
                        now,
                        now,
                    ),
                )
                updated = db.execute(
                    """
                    UPDATE workflow_headers
                    SET current_draft_version_id = ?, updated_at = ?
                    WHERE workflow_id = ? AND current_draft_version_id IS NULL
                    """,
                    (version_id, now, header.workflow_id),
                )
                if updated.rowcount != 1:
                    raise WorkflowDraftExistsError("workflow draft was created concurrently")
                db.commit()
            except Exception:
                db.rollback()
                raise
        return self.get_version(version_id)

    def get_version(self, version_id: str | None) -> WorkflowVersion:
        if not version_id:
            raise KeyError(version_id)
        self.initialize_and_migrate()
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM workflow_versions WHERE version_id = ?",
                (str(version_id),),
            ).fetchone()
        if row is None:
            raise KeyError(version_id)
        return _version_from_row(row)

    def list_versions(self, workflow_id: str) -> list[WorkflowVersion]:
        workflow_id = self.get_workflow(workflow_id).workflow_id
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT * FROM workflow_versions
                WHERE workflow_id = ?
                ORDER BY version_number DESC, created_at DESC
                """,
                (workflow_id,),
            ).fetchall()
        return [_version_from_row(row) for row in rows]

    def update_draft(
        self,
        version_id: str,
        *,
        authoring_graph: dict[str, Any],
        validation: dict[str, Any] | None = None,
        compiled_definition: dict[str, Any] | None = None,
        compiled_plan: dict[str, Any] | None = None,
    ) -> WorkflowVersion:
        current = self.get_version(version_id)
        if current.state != "draft":
            raise PublishedWorkflowVersionError(
                f"published workflow version is immutable: {version_id}"
            )
        graph = _json_object(authoring_graph, "authoring_graph")
        with self._connect() as db:
            db.execute(
                """
                UPDATE workflow_versions
                SET authoring_graph_json = ?, compiled_definition_json = ?,
                    compiled_plan_json = ?, validation_json = ?, updated_at = ?
                WHERE version_id = ? AND state = 'draft'
                """,
                (
                    _dump(graph),
                    _dump_optional(compiled_definition),
                    _dump_optional(compiled_plan),
                    _dump_optional(validation),
                    _now(),
                    version_id,
                ),
            )
        return self.get_version(version_id)

    def publish_version(
        self,
        version_id: str,
        *,
        authoring_graph: dict[str, Any],
        compiled_definition: dict[str, Any],
        compiled_plan: dict[str, Any],
        validation: dict[str, Any],
    ) -> WorkflowVersion:
        current = self.get_version(version_id)
        if current.state != "draft":
            raise PublishedWorkflowVersionError(
                f"published workflow version is immutable: {version_id}"
            )
        graph = _json_object(authoring_graph, "authoring_graph")
        definition = _json_object(compiled_definition, "compiled_definition")
        plan = _json_object(compiled_plan, "compiled_plan")
        validation_payload = _json_object(validation, "validation")
        if validation_payload.get("valid") is not True:
            raise WorkflowVersionError("cannot publish an invalid workflow version")
        now = _now()
        with self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                updated = db.execute(
                    """
                    UPDATE workflow_versions
                    SET state = 'published', authoring_graph_json = ?,
                        compiled_definition_json = ?, compiled_plan_json = ?,
                        validation_json = ?, updated_at = ?, published_at = ?
                    WHERE version_id = ? AND state = 'draft'
                    """,
                    (
                        _dump(graph),
                        _dump(definition),
                        _dump(plan),
                        _dump(validation_payload),
                        now,
                        now,
                        version_id,
                    ),
                )
                if updated.rowcount != 1:
                    raise PublishedWorkflowVersionError(
                        f"published workflow version is immutable: {version_id}"
                    )
                db.execute(
                    """
                    UPDATE workflow_headers
                    SET published_version_id = ?, current_draft_version_id = NULL,
                        status = 'active', archived_at = NULL, updated_at = ?
                    WHERE workflow_id = ? AND current_draft_version_id = ?
                    """,
                    (version_id, now, current.workflow_id, version_id),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return self.get_version(version_id)

    def compatibility_definition(self, workflow_id: str) -> dict[str, Any]:
        header = self.get_workflow(workflow_id)
        if not header.published_version_id:
            raise KeyError(f"workflow has no published version: {workflow_id}")
        version = self.get_version(header.published_version_id)
        if not version.compiled_definition:
            raise KeyError(f"workflow version has no compiled definition: {version.version_id}")
        return json.loads(_dump(version.compiled_definition))

    def _next_version_number(self, workflow_id: str) -> int:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT COALESCE(MAX(version_number), 0) AS max_version
                FROM workflow_versions WHERE workflow_id = ? AND state = 'published'
                """,
                (workflow_id,),
            ).fetchone()
        return int(row["max_version"] or 0) + 1

    def _migrate_legacy_row(self, db: sqlite3.Connection, row: sqlite3.Row) -> None:
        workflow_id = _validated_workflow_id(str(row["workflow_id"]))
        definition = json.loads(str(row["definition_json"]))
        version_id = _new_version_id()
        created_at = str(row["created_at"] or _now())
        updated_at = str(row["updated_at"] or created_at)
        graph = {
            "schema_version": 1,
            "workflow_id": workflow_id,
            "name": str(definition.get("name") or row["name"] or workflow_id),
            "description": str(definition.get("description") or ""),
            "read_only": True,
            "legacy_definition": definition,
        }
        validation = {
            "valid": True,
            "errors": [],
            "warnings": [
                {
                    "code": "legacy_graph_read_only",
                    "message": "Legacy workflow migrated without inventing typed graph dependencies.",
                }
            ],
        }
        db.execute(
            """
            INSERT INTO workflow_headers(
                workflow_id, name, description, status,
                published_version_id, current_draft_version_id,
                created_at, updated_at, archived_at
            ) VALUES (?, ?, ?, 'active', ?, NULL, ?, ?, NULL)
            """,
            (
                workflow_id,
                str(definition.get("name") or row["name"] or workflow_id),
                str(definition.get("description") or ""),
                version_id,
                created_at,
                updated_at,
            ),
        )
        db.execute(
            """
            INSERT INTO workflow_versions(
                version_id, workflow_id, version_number, state,
                authoring_graph_json, compiled_definition_json,
                compiled_plan_json, validation_json, based_on_version_id,
                created_at, updated_at, published_at
            ) VALUES (?, ?, 1, 'published', ?, ?, NULL, ?, NULL, ?, ?, ?)
            """,
            (
                version_id,
                workflow_id,
                _dump(graph),
                _dump(definition),
                _dump(validation),
                created_at,
                updated_at,
                updated_at,
            ),
        )

    @staticmethod
    def _table_exists(db: sqlite3.Connection, name: str) -> bool:
        return db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone() is not None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _validated_workflow_id(value: str) -> str:
    workflow_id = str(value or "").strip()
    if not _WORKFLOW_ID_PATTERN.fullmatch(workflow_id):
        raise ValueError("workflow_id must contain only letters, digits, dot, dash, or underscore")
    return workflow_id


def _required_text(value: str, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _json_object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    return json.loads(_dump(value))


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _dump_optional(value: Any) -> str | None:
    return None if value is None else _dump(_json_object(value, "payload"))


def _load_optional(value: Any) -> dict[str, Any] | None:
    if value is None or str(value) == "":
        return None
    payload = json.loads(str(value))
    return dict(payload) if isinstance(payload, dict) else None


def _header_from_row(row: sqlite3.Row) -> WorkflowHeader:
    return WorkflowHeader(
        workflow_id=str(row["workflow_id"]),
        name=str(row["name"]),
        description=str(row["description"] or ""),
        status=str(row["status"]),
        published_version_id=(
            str(row["published_version_id"]) if row["published_version_id"] else None
        ),
        current_draft_version_id=(
            str(row["current_draft_version_id"])
            if row["current_draft_version_id"]
            else None
        ),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        archived_at=str(row["archived_at"]) if row["archived_at"] else None,
    )


def _version_from_row(row: sqlite3.Row) -> WorkflowVersion:
    return WorkflowVersion(
        version_id=str(row["version_id"]),
        workflow_id=str(row["workflow_id"]),
        version_number=int(row["version_number"]),
        state=str(row["state"]),
        authoring_graph=dict(json.loads(str(row["authoring_graph_json"]))),
        compiled_definition=_load_optional(row["compiled_definition_json"]),
        compiled_plan=_load_optional(row["compiled_plan_json"]),
        validation=_load_optional(row["validation_json"]),
        based_on_version_id=(
            str(row["based_on_version_id"]) if row["based_on_version_id"] else None
        ),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        published_at=str(row["published_at"]) if row["published_at"] else None,
    )


def _empty_graph(header: WorkflowHeader) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "workflow_id": header.workflow_id,
        "name": header.name,
        "description": header.description,
        "nodes": [],
        "edges": [],
        "settings": {"stop_on_error": True, "max_parallelism": 1},
    }


_SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS workbench_schema_meta (
    component TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_headers (
    workflow_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('active', 'archived')),
    published_version_id TEXT,
    current_draft_version_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);
CREATE TABLE IF NOT EXISTS workflow_versions (
    version_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES workflow_headers(workflow_id),
    version_number INTEGER NOT NULL CHECK(version_number > 0),
    state TEXT NOT NULL CHECK(state IN ('draft', 'published', 'archived')),
    authoring_graph_json TEXT NOT NULL,
    compiled_definition_json TEXT,
    compiled_plan_json TEXT,
    validation_json TEXT,
    based_on_version_id TEXT REFERENCES workflow_versions(version_id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT,
    UNIQUE(workflow_id, version_number)
);
CREATE INDEX IF NOT EXISTS idx_workflow_headers_status
    ON workflow_headers(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_workflow_versions_workflow
    ON workflow_versions(workflow_id, version_number DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_versions_current_draft
    ON workflow_versions(workflow_id) WHERE state = 'draft';
"""
