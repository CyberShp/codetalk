"""Local test semantic library for black-box case generation."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SemanticCaseValidationError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return f"sem_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class SemanticCase:
    semantic_id: str
    case_id: str
    feature: str
    module: str
    scenario: str
    preconditions: list[str]
    actions: list[str]
    expected: list[str]
    test_level: str
    interface: str
    terms: list[str]
    assertion_style: str
    tags: list[str]
    source_ref: str
    status: str
    created_at: str
    updated_at: str


class TestSemanticLibraryStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS semantic_cases (
                    semantic_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL UNIQUE,
                    feature TEXT DEFAULT '',
                    module TEXT DEFAULT '',
                    scenario TEXT DEFAULT '',
                    preconditions_json TEXT DEFAULT '[]',
                    actions_json TEXT DEFAULT '[]',
                    expected_json TEXT DEFAULT '[]',
                    test_level TEXT DEFAULT '',
                    interface TEXT DEFAULT '',
                    terms_json TEXT DEFAULT '[]',
                    assertion_style TEXT DEFAULT '',
                    tags_json TEXT DEFAULT '[]',
                    source_ref TEXT DEFAULT '',
                    status TEXT DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_semantic_cases_module
                    ON semantic_cases(module, status, test_level);
                CREATE VIRTUAL TABLE IF NOT EXISTS semantic_case_fts USING fts5(
                    semantic_id UNINDEXED,
                    case_id,
                    feature,
                    module,
                    scenario,
                    terms,
                    tags,
                    assertion_style,
                    tokenize = 'unicode61 tokenchars ''_-/.'''
                );
                """
            )

    def upsert_case(self, payload: dict[str, Any]) -> str:
        self.initialize()
        case_id = str(payload.get("case_id") or "").strip()
        if not case_id:
            raise SemanticCaseValidationError("case_id is required")
        now = _now()
        with self._connect() as db:
            existing = db.execute(
                "SELECT semantic_id, created_at FROM semantic_cases WHERE case_id = ?",
                (case_id,),
            ).fetchone()
            semantic_id = str(existing["semantic_id"]) if existing else _new_id()
            created_at = str(existing["created_at"]) if existing else now
            fields = _normalize_case_payload(payload)
            db.execute(
                """
                INSERT OR REPLACE INTO semantic_cases (
                    semantic_id, case_id, feature, module, scenario,
                    preconditions_json, actions_json, expected_json, test_level,
                    interface, terms_json, assertion_style, tags_json, source_ref,
                    status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    semantic_id,
                    case_id,
                    fields["feature"],
                    fields["module"],
                    fields["scenario"],
                    json.dumps(fields["preconditions"], ensure_ascii=False),
                    json.dumps(fields["actions"], ensure_ascii=False),
                    json.dumps(fields["expected"], ensure_ascii=False),
                    fields["test_level"],
                    fields["interface"],
                    json.dumps(fields["terms"], ensure_ascii=False),
                    fields["assertion_style"],
                    json.dumps(fields["tags"], ensure_ascii=False),
                    fields["source_ref"],
                    fields["status"],
                    created_at,
                    now,
                ),
            )
            db.execute("DELETE FROM semantic_case_fts WHERE semantic_id = ?", (semantic_id,))
            db.execute(
                """
                INSERT INTO semantic_case_fts
                    (semantic_id, case_id, feature, module, scenario, terms, tags, assertion_style)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    semantic_id,
                    case_id,
                    fields["feature"],
                    fields["module"],
                    fields["scenario"],
                    " ".join(fields["terms"]),
                    " ".join(fields["tags"]),
                    fields["assertion_style"],
                ),
            )
        return semantic_id

    def retrieve(
        self,
        *,
        query: str,
        module: str = "",
        test_level: str = "",
        limit: int = 10,
        include_deprecated: bool = False,
    ) -> list[SemanticCase]:
        self.initialize()
        params: list[Any] = [_fts_query(query)]
        where = "semantic_case_fts MATCH ?"
        if module:
            where += " AND c.module = ?"
            params.append(module)
        if test_level:
            where += " AND c.test_level = ?"
            params.append(test_level)
        if not include_deprecated:
            where += " AND c.status = 'active'"
        params.append(limit)
        with self._connect() as db:
            rows = db.execute(
                f"""
                SELECT c.*
                FROM semantic_case_fts f
                JOIN semantic_cases c ON c.semantic_id = f.semantic_id
                WHERE {where}
                ORDER BY bm25(semantic_case_fts), c.updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [_row_to_case(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn


def _normalize_case_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "feature": str(payload.get("feature") or ""),
        "module": str(payload.get("module") or ""),
        "scenario": str(payload.get("scenario") or ""),
        "preconditions": _string_list(payload.get("preconditions")),
        "actions": _string_list(payload.get("actions")),
        "expected": _string_list(payload.get("expected")),
        "test_level": str(payload.get("test_level") or "black_box"),
        "interface": str(payload.get("interface") or ""),
        "terms": _string_list(payload.get("terms")),
        "assertion_style": str(payload.get("assertion_style") or ""),
        "tags": _string_list(payload.get("tags")),
        "source_ref": str(payload.get("source_ref") or ""),
        "status": str(payload.get("status") or "active"),
    }


def _row_to_case(row: sqlite3.Row) -> SemanticCase:
    data = dict(row)
    return SemanticCase(
        semantic_id=data["semantic_id"],
        case_id=data["case_id"],
        feature=data["feature"],
        module=data["module"],
        scenario=data["scenario"],
        preconditions=_json_list(data["preconditions_json"]),
        actions=_json_list(data["actions_json"]),
        expected=_json_list(data["expected_json"]),
        test_level=data["test_level"],
        interface=data["interface"],
        terms=_json_list(data["terms_json"]),
        assertion_style=data["assertion_style"],
        tags=_json_list(data["tags_json"]),
        source_ref=data["source_ref"],
        status=data["status"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item)]
    return []


def _json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return _string_list(parsed)


def _fts_query(query: str) -> str:
    terms = [part.replace('"', '""') for part in str(query or "").split() if part.strip()]
    return " ".join(f'"{term}"' for term in terms) or '""'
