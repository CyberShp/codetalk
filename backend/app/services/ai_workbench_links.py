"""Additive links between AI threads and Workbench V2 objects."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from app.config import settings


_RELATION_TYPES = frozenset(
    {
        "task_created_from_ai",
        "run_discussed_by_ai",
        "artifact_referenced_by_ai",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AIWorkbenchLinkStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = str(db_path or settings.sqlite_db)

    async def create_link(
        self,
        *,
        conversation_id: str,
        relation_type: str,
        message_id: str = "",
        ai_run_id: str = "",
        task_id: str = "",
        task_run_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if relation_type not in _RELATION_TYPES:
            raise ValueError(f"unsupported AI Workbench relation_type: {relation_type}")
        values = {
            "conversation_id": str(conversation_id or "").strip(),
            "message_id": str(message_id or "").strip(),
            "ai_run_id": str(ai_run_id or "").strip(),
            "task_id": str(task_id or "").strip(),
            "task_run_id": str(task_run_id or "").strip(),
        }
        if not values["conversation_id"]:
            raise ValueError("conversation_id is required")
        if not values["task_id"] and not values["task_run_id"]:
            raise ValueError("task_id or task_run_id is required")
        identifier = f"aiwbl_{uuid.uuid4().hex}"
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await _ensure_schema(db)
            await db.execute(
                """
                INSERT OR IGNORE INTO ai_workbench_links(
                    id, conversation_id, message_id, ai_run_id, task_id,
                    task_run_id, relation_type, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier,
                    values["conversation_id"],
                    values["message_id"],
                    values["ai_run_id"],
                    values["task_id"],
                    values["task_run_id"],
                    relation_type,
                    json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                    _now(),
                ),
            )
            await db.commit()
            async with db.execute(
                """
                SELECT * FROM ai_workbench_links
                WHERE conversation_id = ? AND message_id = ? AND ai_run_id = ?
                  AND task_id = ? AND task_run_id = ? AND relation_type = ?
                """,
                (
                    values["conversation_id"],
                    values["message_id"],
                    values["ai_run_id"],
                    values["task_id"],
                    values["task_run_id"],
                    relation_type,
                ),
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            raise RuntimeError("AI Workbench link was not persisted")
        return _link_from_row(row)

    async def list_links(
        self,
        *,
        conversation_id: str = "",
        task_id: str = "",
        task_run_id: str = "",
        relation_type: str = "",
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[str] = []
        for column, value in (
            ("conversation_id", conversation_id),
            ("task_id", task_id),
            ("task_run_id", task_run_id),
            ("relation_type", relation_type),
        ):
            text = str(value or "").strip()
            if text:
                clauses.append(f"{column} = ?")
                params.append(text)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await _ensure_schema(db)
            async with db.execute(
                f"SELECT * FROM ai_workbench_links{where} ORDER BY created_at, id",
                params,
            ) as cur:
                rows = await cur.fetchall()
        return [_link_from_row(row) for row in rows]


async def _ensure_schema(db: aiosqlite.Connection) -> None:
    """Keep the additive relation usable during isolated API startup and upgrades."""
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS ai_workbench_links (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            message_id TEXT NOT NULL DEFAULT '',
            ai_run_id TEXT NOT NULL DEFAULT '',
            task_id TEXT NOT NULL DEFAULT '',
            task_run_id TEXT NOT NULL DEFAULT '',
            relation_type TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_workbench_links_identity
            ON ai_workbench_links(
                conversation_id, message_id, ai_run_id, task_id, task_run_id, relation_type
            );
        CREATE INDEX IF NOT EXISTS idx_ai_workbench_links_task
            ON ai_workbench_links(task_id, task_run_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_ai_workbench_links_conversation
            ON ai_workbench_links(conversation_id, created_at);
        """
    )


def _link_from_row(row: aiosqlite.Row) -> dict[str, Any]:
    data = dict(row)
    data["metadata"] = json.loads(str(data.pop("metadata_json", "{}") or "{}"))
    return data
