"""Persistent user Tasks for Workbench V2."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.workbench_sqlite_backup import ensure_workbench_migration_backup


_LOCK = threading.RLock()
_SCHEMA_VERSION = 3
_LIFECYCLE_STATUSES = frozenset({"draft", "ready", "archived"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class WorkbenchTask:
    task_id: str
    name: str
    description: str
    workspace_id: str
    skill_id: str
    skill_version_id: str
    skill_content_digest: str
    lifecycle_status: str
    execution_profile_id: str
    input_values: dict[str, Any]
    execution_overrides: dict[str, Any]
    output_overrides: dict[str, Any]
    tags: list[str]
    last_run_id: str | None
    created_at: str
    updated_at: str
    archived_at: str | None


class WorkbenchTaskStore:
    """SQLite-backed Task store; run artifacts remain the Attempt source of truth."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize_and_migrate(self) -> dict[str, int]:
        ensure_workbench_migration_backup(self.db_path)
        with _LOCK, self._connect() as db:
            existing_columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(workbench_tasks)").fetchall()
            }
            if existing_columns and (
                "workflow_id" in existing_columns
                or "workflow_version_id" in existing_columns
                or "skill_version_id" not in existing_columns
            ):
                db.execute("DROP TABLE workbench_tasks")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS workbench_schema_meta (
                    component TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS workbench_tasks (
                    task_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    workspace_id TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    skill_version_id TEXT NOT NULL,
                    skill_content_digest TEXT NOT NULL,
                    lifecycle_status TEXT NOT NULL,
                    execution_profile_id TEXT NOT NULL DEFAULT '',
                    input_values_json TEXT NOT NULL,
                    execution_overrides_json TEXT NOT NULL,
                    output_overrides_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    last_run_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_workbench_tasks_updated
                    ON workbench_tasks(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_workbench_tasks_skill
                    ON workbench_tasks(skill_id, skill_version_id);
                CREATE INDEX IF NOT EXISTS idx_workbench_tasks_workspace
                    ON workbench_tasks(workspace_id);
                """
            )
            columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(workbench_tasks)").fetchall()
            }
            if "execution_profile_id" not in columns:
                db.execute(
                    "ALTER TABLE workbench_tasks "
                    "ADD COLUMN execution_profile_id TEXT NOT NULL DEFAULT ''"
                )
            db.execute(
                """
                INSERT INTO workbench_schema_meta(component, version, updated_at)
                VALUES ('tasks', ?, ?)
                ON CONFLICT(component) DO UPDATE SET
                    version = excluded.version,
                    updated_at = excluded.updated_at
                """,
                (_SCHEMA_VERSION, _now()),
            )
        return {"schema_version": _SCHEMA_VERSION}

    def create_task(
        self,
        *,
        name: str,
        workspace_id: str,
        skill_id: str,
        skill_version_id: str,
        skill_content_digest: str,
        description: str = "",
        lifecycle_status: str = "draft",
        execution_profile_id: str = "",
        input_values: dict[str, Any] | None = None,
        execution_overrides: dict[str, Any] | None = None,
        output_overrides: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        task_id: str | None = None,
    ) -> WorkbenchTask:
        self.initialize_and_migrate()
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("task name is required")
        for field_name, value in {
            "workspace_id": workspace_id,
            "skill_id": skill_id,
            "skill_version_id": skill_version_id,
            "skill_content_digest": skill_content_digest,
        }.items():
            if not str(value or "").strip():
                raise ValueError(f"{field_name} is required")
        lifecycle = self._lifecycle(lifecycle_status)
        now = _now()
        identifier = str(task_id or f"task_{uuid.uuid4().hex}")
        with _LOCK, self._connect() as db:
            db.execute(
                """
                INSERT INTO workbench_tasks(
                    task_id, name, description, workspace_id, skill_id,
                    skill_version_id, skill_content_digest, lifecycle_status, execution_profile_id, input_values_json,
                    execution_overrides_json, output_overrides_json, tags_json,
                    last_run_id, created_at, updated_at, archived_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL)
                """,
                (
                    identifier,
                    clean_name,
                    str(description or "").strip(),
                    str(workspace_id).strip(),
                    str(skill_id).strip(),
                    str(skill_version_id).strip(),
                    str(skill_content_digest).strip(),
                    lifecycle,
                    str(execution_profile_id or "").strip(),
                    self._json_object(input_values),
                    self._json_object(execution_overrides),
                    self._json_object(output_overrides),
                    self._json_tags(tags),
                    now,
                    now,
                ),
            )
        return self.get_task(identifier)

    def get_task(self, task_id: str) -> WorkbenchTask:
        self.initialize_and_migrate()
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM workbench_tasks WHERE task_id = ?", (str(task_id),)
            ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return _task_from_row(row)

    def list_tasks(
        self,
        *,
        q: str = "",
        lifecycle_status: str = "",
        skill_id: str = "",
        workspace_id: str = "",
        updated_from: str = "",
        updated_to: str = "",
        include_archived: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> list[WorkbenchTask]:
        self.initialize_and_migrate()
        where, params = self._task_filters(
            q=q,
            lifecycle_status=lifecycle_status,
            skill_id=skill_id,
            workspace_id=workspace_id,
            updated_from=updated_from,
            updated_to=updated_to,
            include_archived=include_archived,
        )
        params.extend([max(1, min(int(limit), 500)), max(0, int(offset))])
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM workbench_tasks{where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [_task_from_row(row) for row in rows]

    def count_tasks(
        self,
        *,
        q: str = "",
        lifecycle_status: str = "",
        skill_id: str = "",
        workspace_id: str = "",
        updated_from: str = "",
        updated_to: str = "",
        include_archived: bool = False,
    ) -> int:
        self.initialize_and_migrate()
        where, params = self._task_filters(
            q=q,
            lifecycle_status=lifecycle_status,
            skill_id=skill_id,
            workspace_id=workspace_id,
            updated_from=updated_from,
            updated_to=updated_to,
            include_archived=include_archived,
        )
        with self._connect() as db:
            row = db.execute(
                f"SELECT COUNT(*) AS total FROM workbench_tasks{where}", params
            ).fetchone()
        return int(row["total"] if row else 0)

    def _task_filters(
        self,
        *,
        q: str,
        lifecycle_status: str,
        skill_id: str,
        workspace_id: str,
        updated_from: str,
        updated_to: str,
        include_archived: bool,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if q.strip():
            clauses.append(
                "(lower(name) LIKE ? OR lower(description) LIKE ? OR lower(tags_json) LIKE ?)"
            )
            needle = f"%{q.strip().lower()}%"
            params.extend([needle, needle, needle])
        if lifecycle_status:
            clauses.append("lifecycle_status = ?")
            params.append(self._lifecycle(lifecycle_status))
        elif not include_archived:
            clauses.append("lifecycle_status != 'archived'")
        if skill_id:
            clauses.append("skill_id = ?")
            params.append(skill_id)
        if workspace_id:
            clauses.append("workspace_id = ?")
            params.append(workspace_id)
        if updated_from:
            clauses.append("updated_at >= ?")
            params.append(updated_from)
        if updated_to:
            clauses.append("updated_at <= ?")
            params.append(updated_to)
        return (f" WHERE {' AND '.join(clauses)}" if clauses else "", params)

    def update_task(self, task_id: str, **changes: Any) -> WorkbenchTask:
        self.initialize_and_migrate()
        immutable = {"task_id", "workspace_id", "skill_id", "skill_version_id", "skill_content_digest", "created_at"}
        attempted = immutable.intersection(changes)
        if attempted:
            raise ValueError(f"immutable task fields cannot change: {', '.join(sorted(attempted))}")
        assignments: list[str] = []
        params: list[Any] = []
        field_map = {
            "name": "name",
            "description": "description",
            "lifecycle_status": "lifecycle_status",
            "execution_profile_id": "execution_profile_id",
            "input_values": "input_values_json",
            "execution_overrides": "execution_overrides_json",
            "output_overrides": "output_overrides_json",
            "tags": "tags_json",
            "last_run_id": "last_run_id",
        }
        unknown = set(changes) - set(field_map)
        if unknown:
            raise ValueError(f"unknown task fields: {', '.join(sorted(unknown))}")
        for key, value in changes.items():
            if key == "name":
                value = str(value or "").strip()
                if not value:
                    raise ValueError("task name is required")
            elif key == "description":
                value = str(value or "").strip()
            elif key == "lifecycle_status":
                value = self._lifecycle(str(value))
            elif key == "execution_profile_id":
                value = str(value or "").strip()
            elif key in {"input_values", "execution_overrides", "output_overrides"}:
                value = self._json_object(value)
            elif key == "tags":
                value = self._json_tags(value)
            elif key == "last_run_id":
                value = str(value or "").strip() or None
            assignments.append(f"{field_map[key]} = ?")
            params.append(value)
        if not assignments:
            return self.get_task(task_id)
        assignments.append("updated_at = ?")
        params.extend([_now(), str(task_id)])
        with _LOCK, self._connect() as db:
            cursor = db.execute(
                f"UPDATE workbench_tasks SET {', '.join(assignments)} WHERE task_id = ?",
                params,
            )
            if cursor.rowcount != 1:
                raise KeyError(task_id)
        return self.get_task(task_id)

    def archive_task(self, task_id: str) -> WorkbenchTask:
        self.get_task(task_id)
        now = _now()
        with _LOCK, self._connect() as db:
            db.execute(
                """
                UPDATE workbench_tasks
                SET lifecycle_status = 'archived', archived_at = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (now, now, str(task_id)),
            )
        return self.get_task(task_id)

    def clone_task(self, task_id: str, *, name: str | None = None) -> WorkbenchTask:
        source = self.get_task(task_id)
        return self.create_task(
            name=str(name or f"{source.name} 副本"),
            description=source.description,
            workspace_id=source.workspace_id,
            skill_id=source.skill_id,
            skill_version_id=source.skill_version_id,
            skill_content_digest=source.skill_content_digest,
            lifecycle_status="draft",
            execution_profile_id=source.execution_profile_id,
            input_values=source.input_values,
            execution_overrides=source.execution_overrides,
            output_overrides=source.output_overrides,
            tags=source.tags,
        )

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _lifecycle(value: str) -> str:
        text = str(value or "").strip()
        if text not in _LIFECYCLE_STATUSES:
            raise ValueError(f"unsupported lifecycle_status: {text}")
        return text

    @staticmethod
    def _json_object(value: Any) -> str:
        payload = {} if value is None else value
        if not isinstance(payload, dict):
            raise ValueError("task overrides and inputs must be objects")
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _json_tags(value: Any) -> str:
        payload = [] if value is None else value
        if not isinstance(payload, list):
            raise ValueError("task tags must be an array")
        return json.dumps(
            list(dict.fromkeys(str(item).strip() for item in payload if str(item).strip())),
            ensure_ascii=False,
        )


def _task_from_row(row: sqlite3.Row) -> WorkbenchTask:
    return WorkbenchTask(
        task_id=str(row["task_id"]),
        name=str(row["name"]),
        description=str(row["description"] or ""),
        workspace_id=str(row["workspace_id"]),
        skill_id=str(row["skill_id"]),
        skill_version_id=str(row["skill_version_id"]),
        skill_content_digest=str(row["skill_content_digest"]),
        lifecycle_status=str(row["lifecycle_status"]),
        execution_profile_id=str(row["execution_profile_id"] or ""),
        input_values=dict(json.loads(row["input_values_json"] or "{}")),
        execution_overrides=dict(json.loads(row["execution_overrides_json"] or "{}")),
        output_overrides=dict(json.loads(row["output_overrides_json"] or "{}")),
        tags=[str(item) for item in json.loads(row["tags_json"] or "[]")],
        last_run_id=str(row["last_run_id"]) if row["last_run_id"] else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        archived_at=str(row["archived_at"]) if row["archived_at"] else None,
    )
