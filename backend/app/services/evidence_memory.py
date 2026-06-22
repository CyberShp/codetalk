"""Lightweight Evidence Memory for CodeTalk analysis workbench.

This is intentionally narrower than clowder-ai's memory system: it stores
validated analysis facts and their provenance so future workflows and agents
can retrieve evidence without treating natural-language summaries as truth.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    run_id: str
    workspace_id: str
    kind: str
    subject_key: str
    status: str
    source: str
    path: str = ""
    symbol: str = ""
    reason: str = ""
    confidence: float | None = None
    text: str = ""
    provenance: dict[str, Any] | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class SourceSliceRecord:
    slice_id: str
    evidence_id: str
    file_path: str
    start_line: int
    end_line: int
    sha256: str
    excerpt: str
    created_at: str


class EvidenceMemoryStore:
    """SQLite-backed evidence memory with search/anchor/recent entry points."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS analysis_runs (
                    run_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    repo_path TEXT NOT NULL,
                    object_text TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence_items (
                    evidence_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    subject_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    path TEXT DEFAULT '',
                    symbol TEXT DEFAULT '',
                    reason TEXT DEFAULT '',
                    confidence REAL,
                    text TEXT DEFAULT '',
                    provenance_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_evidence_workspace
                    ON evidence_items(workspace_id, kind, status);
                CREATE INDEX IF NOT EXISTS idx_evidence_subject
                    ON evidence_items(subject_key);
                CREATE TABLE IF NOT EXISTS evidence_edges (
                    edge_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    from_evidence_id TEXT NOT NULL,
                    to_evidence_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_slices (
                    slice_id TEXT PRIMARY KEY,
                    evidence_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    excerpt TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(
                    evidence_id UNINDEXED,
                    workspace_id UNINDEXED,
                    subject_key,
                    path,
                    symbol,
                    reason,
                    text,
                    tokenize = 'unicode61 tokenchars ''_-/.'''
                );
                """
            )

    def record_analysis_run(
        self,
        *,
        workspace_id: str,
        repo_path: str,
        object_text: str,
        workflow_id: str,
        status: str = "running",
        run_id: str | None = None,
    ) -> str:
        self.initialize()
        rid = run_id or _new_id("run")
        now = _now()
        with self._connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO analysis_runs
                    (run_id, workspace_id, repo_path, object_text, workflow_id, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM analysis_runs WHERE run_id = ?), ?), ?)
                """,
                (rid, workspace_id, repo_path, object_text, workflow_id, status, rid, now, now),
            )
        return rid

    def upsert_evidence_item(
        self,
        *,
        run_id: str,
        workspace_id: str,
        kind: str,
        subject_key: str,
        status: str,
        source: str,
        path: str = "",
        symbol: str = "",
        reason: str = "",
        confidence: float | None = None,
        text: str = "",
        provenance: dict[str, Any] | None = None,
        evidence_id: str | None = None,
    ) -> str:
        self.initialize()
        eid = evidence_id or _new_id("ev")
        now = _now()
        provenance_json = json.dumps(provenance or {}, ensure_ascii=False, sort_keys=True)
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO evidence_items (
                    evidence_id, run_id, workspace_id, kind, subject_key, status, source,
                    path, symbol, reason, confidence, text, provenance_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(evidence_id) DO UPDATE SET
                    run_id = excluded.run_id,
                    workspace_id = excluded.workspace_id,
                    kind = excluded.kind,
                    subject_key = excluded.subject_key,
                    status = excluded.status,
                    source = excluded.source,
                    path = excluded.path,
                    symbol = excluded.symbol,
                    reason = excluded.reason,
                    confidence = excluded.confidence,
                    text = excluded.text,
                    provenance_json = excluded.provenance_json,
                    updated_at = excluded.updated_at
                """,
                (
                    eid, run_id, workspace_id, kind, subject_key, status, source,
                    path, symbol, reason, confidence, text, provenance_json, now, now,
                ),
            )
            db.execute("DELETE FROM evidence_fts WHERE evidence_id = ?", (eid,))
            db.execute(
                """
                INSERT INTO evidence_fts
                    (evidence_id, workspace_id, subject_key, path, symbol, reason, text)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (eid, workspace_id, subject_key, path, symbol, reason, text),
            )
        return eid

    def add_edge(
        self,
        *,
        workspace_id: str,
        from_evidence_id: str,
        to_evidence_id: str,
        relation: str,
    ) -> str:
        self.initialize()
        edge_id = _new_id("edge")
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO evidence_edges
                    (edge_id, workspace_id, from_evidence_id, to_evidence_id, relation, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (edge_id, workspace_id, from_evidence_id, to_evidence_id, relation, _now()),
            )
        return edge_id

    def add_source_slice(
        self,
        *,
        evidence_id: str,
        file_path: str,
        start_line: int,
        end_line: int,
        excerpt: str,
        sha256: str,
    ) -> str:
        self.initialize()
        slice_id = _new_id("slice")
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO source_slices
                    (slice_id, evidence_id, file_path, start_line, end_line, sha256, excerpt, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (slice_id, evidence_id, file_path, start_line, end_line, sha256, excerpt, _now()),
            )
        return slice_id

    def search_analysis_memory(
        self,
        query: str,
        *,
        workspace_id: str | None = None,
        limit: int = 10,
    ) -> list[EvidenceItem]:
        self.initialize()
        fts_query = _fts_query(query)
        if not fts_query:
            return []
        params: list[Any] = [fts_query]
        where = "evidence_fts MATCH ?"
        if workspace_id:
            where += " AND f.workspace_id = ?"
            params.append(workspace_id)
        params.append(limit)
        with self._connect() as db:
            rows = db.execute(
                f"""
                SELECT e.*
                FROM evidence_fts f
                JOIN evidence_items e ON e.evidence_id = f.evidence_id
                WHERE {where}
                ORDER BY bm25(evidence_fts), e.updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [_row_to_evidence(row) for row in rows]

    def resolve_evidence_anchor(self, anchor: str, *, workspace_id: str | None = None) -> list[EvidenceItem]:
        self.initialize()
        params: list[Any] = [anchor, anchor, anchor]
        where = "(subject_key = ? OR path = ? OR symbol = ?)"
        if workspace_id:
            where += " AND workspace_id = ?"
            params.append(workspace_id)
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM evidence_items WHERE {where} ORDER BY updated_at DESC",
                params,
            ).fetchall()
        return [_row_to_evidence(row) for row in rows]

    def list_recent_analysis(self, *, workspace_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        self.initialize()
        params: list[Any] = []
        where = ""
        if workspace_id:
            where = "WHERE workspace_id = ?"
            params.append(workspace_id)
        params.append(limit)
        with self._connect() as db:
            rows = db.execute(
                f"""
                SELECT run_id, workspace_id, repo_path, object_text, workflow_id, status, created_at, updated_at
                FROM analysis_runs
                {where}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_source_slice(self, slice_id: str) -> SourceSliceRecord:
        self.initialize()
        with self._connect() as db:
            row = db.execute("SELECT * FROM source_slices WHERE slice_id = ?", (slice_id,)).fetchone()
        if row is None:
            raise KeyError(slice_id)
        return SourceSliceRecord(**dict(row))

    def list_source_slices(self, evidence_id: str) -> list[SourceSliceRecord]:
        self.initialize()
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT *
                FROM source_slices
                WHERE evidence_id = ?
                ORDER BY start_line, end_line, created_at
                """,
                (evidence_id,),
            ).fetchall()
        return [SourceSliceRecord(**dict(row)) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn


def _row_to_evidence(row: sqlite3.Row) -> EvidenceItem:
    data = dict(row)
    provenance_raw = data.pop("provenance_json", "{}") or "{}"
    try:
        provenance = json.loads(provenance_raw)
    except json.JSONDecodeError:
        provenance = {}
    return EvidenceItem(provenance=provenance, **data)


def _fts_query(query: str) -> str:
    terms = [
        term.replace('"', '""')
        for term in str(query or "").replace("\\", "/").split()
        if term.strip()
    ]
    return " ".join(f'"{term}"' for term in terms)
