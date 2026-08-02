"""Versioned local storage for historical test knowledge.

This store deliberately keeps historical material separate from Evidence Memory.
Records returned here are experience leads unless a current run promotes them
through the authority policy.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 3
_SCOPES = {"project", "personal_global"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class KnowledgeStore:
    """SQLite/FTS5 store with explicit schema versions and pre-upgrade backups."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> int:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        had_schema = self._has_schema_table()
        current = self.schema_version() if had_schema else 0
        if had_schema and current < SCHEMA_VERSION:
            self._backup_before_migration(current)
        with self._connect() as db:
            db.executescript(_SCHEMA)
            db.execute(
                "INSERT INTO knowledge_schema (singleton, version, updated_at) VALUES (1, ?, ?) "
                "ON CONFLICT(singleton) DO UPDATE SET version = excluded.version, updated_at = excluded.updated_at",
                (SCHEMA_VERSION, _now()),
            )
        return SCHEMA_VERSION

    def schema_version(self) -> int:
        if not self.db_path.exists() or not self._has_schema_table():
            return 0
        with self._connect() as db:
            row = db.execute("SELECT version FROM knowledge_schema WHERE singleton = 1").fetchone()
        return int(row["version"]) if row else 0

    def register_source(
        self,
        *,
        source_kind: str,
        source_identity: str,
        content: bytes,
        scope: str,
        workspace_identity: str = "",
        project_identity: str = "",
        revision: str = "",
        locators: Iterable[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self.initialize()
        _validate_scope(scope, workspace_identity)
        identity = str(source_identity).strip()
        if not identity:
            raise ValueError("source_identity is required")
        payload = bytes(content)
        sha256 = hashlib.sha256(payload).hexdigest()
        now = _now()
        with self._connect() as db:
            hash_duplicate = db.execute(
                """
                SELECT source_snapshot_id, source_document_id, snapshot_number
                FROM knowledge_source_snapshots
                WHERE sha256 = ?
                ORDER BY created_at
                LIMIT 1
                """,
                (sha256,),
            ).fetchone()
            if hash_duplicate is not None:
                return {
                    "source_document_id": str(hash_duplicate["source_document_id"]),
                    "source_snapshot_id": str(hash_duplicate["source_snapshot_id"]),
                    "snapshot_number": int(hash_duplicate["snapshot_number"]),
                    "sha256": sha256,
                    "duplicate": True,
                }
            document = db.execute(
                "SELECT source_document_id FROM knowledge_source_documents WHERE source_kind = ? AND source_identity = ?",
                (source_kind, identity),
            ).fetchone()
            if document is None:
                document_id = _new_id("src")
                db.execute(
                    """
                    INSERT INTO knowledge_source_documents
                        (source_document_id, source_kind, source_identity, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (document_id, source_kind, identity, now, now),
                )
            else:
                document_id = str(document["source_document_id"])
                db.execute(
                    "UPDATE knowledge_source_documents SET updated_at = ? WHERE source_document_id = ?",
                    (now, document_id),
                )
            existing = db.execute(
                """
                SELECT source_snapshot_id, snapshot_number
                FROM knowledge_source_snapshots
                WHERE source_document_id = ? AND sha256 = ?
                """,
                (document_id, sha256),
            ).fetchone()
            if existing is not None:
                return {
                    "source_document_id": document_id,
                    "source_snapshot_id": str(existing["source_snapshot_id"]),
                    "snapshot_number": int(existing["snapshot_number"]),
                    "sha256": sha256,
                    "duplicate": True,
                }
            max_snapshot = db.execute(
                "SELECT COALESCE(MAX(snapshot_number), 0) AS value FROM knowledge_source_snapshots WHERE source_document_id = ?",
                (document_id,),
            ).fetchone()
            snapshot_number = int(max_snapshot["value"]) + 1
            snapshot_id = _new_id("snap")
            db.execute(
                """
                INSERT INTO knowledge_source_snapshots (
                    source_snapshot_id, source_document_id, snapshot_number, sha256, content,
                    revision, scope, workspace_identity, project_identity, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id, document_id, snapshot_number, sha256, payload, revision,
                    scope, workspace_identity, project_identity, now,
                ),
            )
            for position, locator in enumerate(locators or [], start=1):
                normalized = _normalize_locator(locator)
                db.execute(
                    """
                    INSERT INTO knowledge_source_locators
                        (locator_id, source_snapshot_id, position, kind, locator_json, excerpt, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _new_id("loc"), snapshot_id, position, normalized["kind"],
                        json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                        str(normalized.get("excerpt") or ""), now,
                    ),
                )
        return {
            "source_document_id": document_id,
            "source_snapshot_id": snapshot_id,
            "snapshot_number": snapshot_number,
            "sha256": sha256,
            "duplicate": False,
        }

    def list_source_locators(self, source_snapshot_id: str) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as db:
            rows = db.execute(
                "SELECT locator_json FROM knowledge_source_locators WHERE source_snapshot_id = ? ORDER BY position",
                (source_snapshot_id,),
            ).fetchall()
        return [json.loads(str(row["locator_json"])) for row in rows]

    def create_incident(
        self,
        *,
        title: str,
        summary: str,
        scope: str,
        workspace_identity: str = "",
        source_snapshot_ids: Iterable[str] = (),
        terms: Iterable[str] = (),
        status: str = "active",
    ) -> dict[str, Any]:
        self.initialize()
        _validate_scope(scope, workspace_identity)
        incident_id = _new_id("inc")
        now = _now()
        normalized_terms = _strings(terms)
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO knowledge_incidents
                    (incident_id, title, summary, terms_json, scope, workspace_identity, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (incident_id, title, summary, json.dumps(normalized_terms, ensure_ascii=False), scope, workspace_identity, status, now, now),
            )
            for snapshot_id in dict.fromkeys(str(value) for value in source_snapshot_ids if str(value)):
                db.execute(
                    "INSERT INTO knowledge_incident_sources (incident_id, source_snapshot_id, created_at) VALUES (?, ?, ?)",
                    (incident_id, snapshot_id, now),
                )
            self._upsert_incident_fts(db, incident_id)
        return self.get_incident(incident_id)

    def get_incident(self, incident_id: str) -> dict[str, Any]:
        self.initialize()
        with self._connect() as db:
            row = db.execute("SELECT * FROM knowledge_incidents WHERE incident_id = ?", (incident_id,)).fetchone()
        if row is None:
            raise KeyError(incident_id)
        return _incident_row(row)

    def list_incidents(
        self,
        *,
        query: str = "",
        scope: str | None = None,
        workspace_identity: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List active incidents, using FTS when a search term is supplied."""
        self.initialize()
        max_results = max(1, int(limit))
        if str(query or "").strip():
            records = self.search_experience(
                query,
                scope=scope,
                workspace_identity=workspace_identity,
                limit=max_results * 2,
            )
            return [record for record in records if record["record_type"] == "incident"][:max_results]
        clauses = ["status = 'active'"]
        params: list[Any] = []
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        if workspace_identity:
            clauses.append("workspace_identity = ?")
            params.append(workspace_identity)
        params.append(max_results)
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM knowledge_incidents WHERE {' AND '.join(clauses)} "
                "ORDER BY updated_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [_incident_row(row) for row in rows]

    def get_incident_provenance(self, incident_id: str) -> list[dict[str, Any]]:
        """Return source metadata and exact locators without exposing raw bytes."""
        self.initialize()
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT s.*, d.source_kind, d.source_identity
                FROM knowledge_incident_sources link
                JOIN knowledge_source_snapshots s ON s.source_snapshot_id = link.source_snapshot_id
                JOIN knowledge_source_documents d ON d.source_document_id = s.source_document_id
                WHERE link.incident_id = ? ORDER BY link.created_at
                """,
                (incident_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["locators"] = self.list_source_locators(str(item["source_snapshot_id"]))
            item.pop("content", None)
            result.append(item)
        return result

    def create_pattern(
        self,
        *,
        name: str,
        content: str,
        scope: str,
        workspace_identity: str = "",
        terms: Iterable[str] = (),
        applicability: Iterable[str] = (),
        exclusions: Iterable[str] = (),
        review_state: str = "unreviewed",
        lifecycle_state: str = "active",
    ) -> dict[str, Any]:
        self.initialize()
        _validate_scope(scope, workspace_identity)
        pattern_id = _new_id("pat")
        version_id = _new_id("patver")
        now = _now()
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO knowledge_patterns
                    (pattern_id, name, scope, workspace_identity, active_version_id, review_state, lifecycle_state, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (pattern_id, name, scope, workspace_identity, version_id, review_state, lifecycle_state, now, now),
            )
            self._insert_pattern_version(
                db, pattern_id, version_id, 1, content, terms, applicability, exclusions, now
            )
            self._upsert_pattern_fts(db, pattern_id)
        return self.get_pattern(pattern_id)

    def add_pattern_version(
        self,
        pattern_id: str,
        *,
        content: str,
        terms: Iterable[str] | None = None,
        applicability: Iterable[str] | None = None,
        exclusions: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        self.initialize()
        now = _now()
        with self._connect() as db:
            pattern = db.execute("SELECT * FROM knowledge_patterns WHERE pattern_id = ?", (pattern_id,)).fetchone()
            if pattern is None:
                raise KeyError(pattern_id)
            active = db.execute(
                "SELECT * FROM knowledge_pattern_versions WHERE pattern_version_id = ?",
                (pattern["active_version_id"],),
            ).fetchone()
            version_number = db.execute(
                "SELECT COALESCE(MAX(version_number), 0) AS value FROM knowledge_pattern_versions WHERE pattern_id = ?",
                (pattern_id,),
            ).fetchone()["value"] + 1
            version_id = _new_id("patver")
            self._insert_pattern_version(
                db, pattern_id, version_id, int(version_number), content,
                terms if terms is not None else _json_list(active["terms_json"]),
                applicability if applicability is not None else _json_list(active["applicability_json"]),
                exclusions if exclusions is not None else _json_list(active["exclusions_json"]), now,
            )
            db.execute(
                "UPDATE knowledge_patterns SET active_version_id = ?, updated_at = ? WHERE pattern_id = ?",
                (version_id, now, pattern_id),
            )
            self._upsert_pattern_fts(db, pattern_id)
        return self.get_pattern_version(version_id)

    def get_pattern(self, pattern_id: str) -> dict[str, Any]:
        self.initialize()
        with self._connect() as db:
            row = db.execute("SELECT * FROM knowledge_patterns WHERE pattern_id = ?", (pattern_id,)).fetchone()
        if row is None:
            raise KeyError(pattern_id)
        return dict(row)

    def list_patterns(
        self,
        *,
        query: str = "",
        scope: str | None = None,
        workspace_identity: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List active pattern heads with their current version content."""
        self.initialize()
        max_results = max(1, int(limit))
        if str(query or "").strip():
            records = self.search_experience(
                query,
                scope=scope,
                workspace_identity=workspace_identity,
                limit=max_results * 2,
            )
            return [record for record in records if record["record_type"] == "pattern"][:max_results]
        clauses = ["p.lifecycle_state != 'deprecated'"]
        params: list[Any] = []
        if scope:
            clauses.append("p.scope = ?")
            params.append(scope)
        if workspace_identity:
            clauses.append("p.workspace_identity = ?")
            params.append(workspace_identity)
        params.append(max_results)
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT p.*, v.content, v.terms_json, v.applicability_json, v.exclusions_json,
                       v.version_number, v.created_at AS version_created_at
                FROM knowledge_patterns p
                JOIN knowledge_pattern_versions v ON v.pattern_version_id = p.active_version_id
                WHERE """ + " AND ".join(clauses) + " ORDER BY p.updated_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [_search_pattern_row(row) for row in rows]

    def list_pattern_versions(self, pattern_id: str) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as db:
            exists = db.execute(
                "SELECT 1 FROM knowledge_patterns WHERE pattern_id = ?", (pattern_id,)
            ).fetchone()
            if exists is None:
                raise KeyError(pattern_id)
            rows = db.execute(
                "SELECT * FROM knowledge_pattern_versions WHERE pattern_id = ? ORDER BY version_number DESC",
                (pattern_id,),
            ).fetchall()
        return [_pattern_version_row(row) for row in rows]

    def get_pattern_version(self, pattern_version_id: str) -> dict[str, Any]:
        self.initialize()
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM knowledge_pattern_versions WHERE pattern_version_id = ?", (pattern_version_id,)
            ).fetchone()
        if row is None:
            raise KeyError(pattern_version_id)
        return _pattern_version_row(row)

    def update_pattern_states(
        self,
        pattern_id: str,
        *,
        review_state: str | None = None,
        lifecycle_state: str | None = None,
    ) -> dict[str, Any]:
        self.initialize()
        if review_state is not None and review_state not in {"unreviewed", "confirmed", "rejected"}:
            raise ValueError(f"unsupported pattern review_state: {review_state}")
        if lifecycle_state is not None and lifecycle_state not in {"active", "superseded", "deprecated"}:
            raise ValueError(f"unsupported pattern lifecycle_state: {lifecycle_state}")
        if review_state is None and lifecycle_state is None:
            return self.get_pattern(pattern_id)
        clauses: list[str] = ["updated_at = ?"]
        values: list[Any] = [_now()]
        if review_state is not None:
            clauses.append("review_state = ?")
            values.append(review_state)
        if lifecycle_state is not None:
            clauses.append("lifecycle_state = ?")
            values.append(lifecycle_state)
        values.append(pattern_id)
        with self._connect() as db:
            result = db.execute(
                f"UPDATE knowledge_patterns SET {', '.join(clauses)} WHERE pattern_id = ?", values
            )
            if result.rowcount == 0:
                raise KeyError(pattern_id)
        return self.get_pattern(pattern_id)

    def restore_pattern_version(self, pattern_id: str, pattern_version_id: str) -> dict[str, Any]:
        self.initialize()
        with self._connect() as db:
            exists = db.execute(
                "SELECT 1 FROM knowledge_pattern_versions WHERE pattern_id = ? AND pattern_version_id = ?",
                (pattern_id, pattern_version_id),
            ).fetchone()
            if exists is None:
                raise KeyError(pattern_version_id)
            db.execute(
                "UPDATE knowledge_patterns SET active_version_id = ?, updated_at = ? WHERE pattern_id = ?",
                (pattern_version_id, _now(), pattern_id),
            )
            self._upsert_pattern_fts(db, pattern_id)
        return self.get_pattern(pattern_id)

    def link_incident_pattern(self, incident_id: str, pattern_id: str, pattern_version_id: str) -> dict[str, Any]:
        self.initialize()
        link_id = _new_id("link")
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO knowledge_incident_pattern_links
                    (link_id, incident_id, pattern_id, pattern_version_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (link_id, incident_id, pattern_id, pattern_version_id, _now()),
            )
        return {"link_id": link_id, "incident_id": incident_id, "pattern_id": pattern_id, "pattern_version_id": pattern_version_id}

    def list_pattern_incidents(self, pattern_id: str) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT i.* FROM knowledge_incident_pattern_links link
                JOIN knowledge_incidents i ON i.incident_id = link.incident_id
                WHERE link.pattern_id = ? ORDER BY link.created_at
                """,
                (pattern_id,),
            ).fetchall()
        return [_incident_row(row) for row in rows]

    def create_merge_proposal(
        self,
        *,
        subject_type: str,
        source_id: str,
        candidate_id: str,
        similarity: float,
    ) -> dict[str, Any]:
        self.initialize()
        if subject_type not in {"incident", "pattern"}:
            raise ValueError(f"unsupported merge proposal subject_type: {subject_type}")
        proposal_id = _new_id("merge")
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO knowledge_merge_proposals
                    (proposal_id, subject_type, source_id, candidate_id, similarity, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'proposed', ?, ?)
                """,
                (proposal_id, subject_type, source_id, candidate_id, float(similarity), _now(), _now()),
            )
        return {"proposal_id": proposal_id, "subject_type": subject_type, "source_id": source_id, "candidate_id": candidate_id, "similarity": float(similarity), "status": "proposed"}

    def list_merge_proposals(self, *, subject_type: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        self.initialize()
        clauses: list[str] = []
        params: list[Any] = []
        if subject_type:
            clauses.append("subject_type = ?")
            params.append(subject_type)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as db:
            rows = db.execute(f"SELECT * FROM knowledge_merge_proposals {where} ORDER BY created_at DESC", params).fetchall()
        return [dict(row) for row in rows]

    def create_import_job(self, *, source_count: int, scope: str, workspace_identity: str = "") -> dict[str, Any]:
        self.initialize()
        _validate_scope(scope, workspace_identity)
        job_id = _new_id("job")
        now = _now()
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO knowledge_import_jobs
                    (job_id, source_count, scope, workspace_identity, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'running', ?, ?)
                """,
                (job_id, max(0, int(source_count)), scope, workspace_identity, now, now),
            )
        return self.get_import_job(job_id)

    def get_import_job(self, job_id: str) -> dict[str, Any]:
        self.initialize()
        with self._connect() as db:
            row = db.execute("SELECT * FROM knowledge_import_jobs WHERE job_id = ?", (job_id,)).fetchone()
            stages = db.execute(
                "SELECT * FROM knowledge_import_job_stages WHERE job_id = ? ORDER BY updated_at", (job_id,)
            ).fetchall()
        if row is None:
            raise KeyError(job_id)
        result = dict(row)
        result["stages"] = [dict(stage) for stage in stages]
        return result

    def list_import_jobs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM knowledge_import_jobs ORDER BY updated_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) | {"stages": self.get_import_job(str(row["job_id"]))["stages"]} for row in rows]

    def attach_import_source(
        self,
        job_id: str,
        source_snapshot_id: str,
        *,
        filename: str,
        parser: str,
        parse_status: str,
        parse_error: str = "",
    ) -> None:
        self.initialize()
        with self._connect() as db:
            row = db.execute(
                "SELECT COALESCE(MAX(position), 0) AS value FROM knowledge_import_job_sources WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            position = int(row["value"] or 0) + 1
            db.execute(
                """
                INSERT INTO knowledge_import_job_sources (
                    job_id, source_snapshot_id, position, filename, parser,
                    parse_status, parse_error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, source_snapshot_id) DO UPDATE SET
                    filename = excluded.filename,
                    parser = excluded.parser,
                    parse_status = excluded.parse_status,
                    parse_error = excluded.parse_error
                """,
                (
                    job_id, source_snapshot_id, position, str(filename), str(parser),
                    str(parse_status), str(parse_error), _now(),
                ),
            )

    def list_import_sources(
        self,
        job_id: str,
        *,
        include_content: bool = False,
    ) -> list[dict[str, Any]]:
        self.initialize()
        content_column = ", s.content" if include_content else ""
        with self._connect() as db:
            if db.execute(
                "SELECT 1 FROM knowledge_import_jobs WHERE job_id = ?", (job_id,)
            ).fetchone() is None:
                raise KeyError(job_id)
            rows = db.execute(
                f"""
                SELECT link.*, s.sha256, s.scope, s.workspace_identity,
                       s.project_identity, s.revision{content_column}
                FROM knowledge_import_job_sources link
                JOIN knowledge_source_snapshots s
                  ON s.source_snapshot_id = link.source_snapshot_id
                WHERE link.job_id = ?
                ORDER BY link.position
                """,
                (job_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            if include_content:
                item["content"] = bytes(item["content"])
            item["locators"] = self.list_source_locators(
                str(item["source_snapshot_id"])
            )
            result.append(item)
        return result

    def update_import_status(self, job_id: str, status: str) -> dict[str, Any]:
        self.initialize()
        normalized = str(status).strip()
        if not normalized:
            raise ValueError("import status is required")
        with self._connect() as db:
            result = db.execute(
                "UPDATE knowledge_import_jobs SET status = ?, updated_at = ? WHERE job_id = ?",
                (normalized, _now(), job_id),
            )
            if result.rowcount == 0:
                raise KeyError(job_id)
        return self.get_import_job(job_id)

    def set_import_context(self, job_id: str, context: dict[str, Any]) -> None:
        self.initialize()
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO knowledge_import_job_context (job_id, context_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    context_json = excluded.context_json,
                    updated_at = excluded.updated_at
                """,
                (
                    job_id,
                    json.dumps(dict(context), ensure_ascii=False, sort_keys=True),
                    _now(),
                ),
            )

    def get_import_context(self, job_id: str) -> dict[str, Any]:
        self.initialize()
        with self._connect() as db:
            row = db.execute(
                "SELECT context_json FROM knowledge_import_job_context WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return {}
        value = json.loads(str(row["context_json"]))
        return value if isinstance(value, dict) else {}

    def start_import_stage(self, job_id: str, stage: str) -> dict[str, Any]:
        return self._set_stage(job_id, stage, status="running", increment_attempt=True)

    def complete_import_stage(self, job_id: str, stage: str, *, processed_count: int = 0) -> dict[str, Any]:
        return self._set_stage(job_id, stage, status="completed", processed_count=processed_count)

    def fail_import_stage(self, job_id: str, stage: str, error: str) -> dict[str, Any]:
        return self._set_stage(job_id, stage, status="failed", error=error)

    def retry_import_stage(self, job_id: str, stage: str) -> dict[str, Any]:
        return self._set_stage(job_id, stage, status="pending", increment_attempt=True)

    def record_feedback(
        self,
        *,
        subject_type: str,
        subject_id: str,
        outcome: str,
        workspace_identity: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        self.initialize()
        feedback_id = _new_id("feedback")
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO knowledge_feedback
                    (feedback_id, subject_type, subject_id, outcome, workspace_identity, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (feedback_id, subject_type, subject_id, outcome, workspace_identity, note, _now()),
            )
        return {"feedback_id": feedback_id, "subject_type": subject_type, "subject_id": subject_id, "outcome": outcome, "workspace_identity": workspace_identity, "note": note}

    def search_experience(
        self,
        query: str,
        *,
        scope: str | None = None,
        workspace_identity: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self.initialize()
        fts_query = _fts_query(query)
        if not fts_query:
            return []
        max_results = max(1, int(limit))
        patterns = self._search_patterns(fts_query, scope, workspace_identity, max_results)
        incidents = self._search_incidents(fts_query, scope, workspace_identity, max_results)
        records = [*patterns, *incidents]
        records.sort(key=lambda record: (0 if record["record_type"] == "pattern" else 1, record.get("rank", 0)))
        return records[:max_results]

    def _search_patterns(self, fts_query: str, scope: str | None, workspace_identity: str | None, limit: int) -> list[dict[str, Any]]:
        clauses = ["knowledge_pattern_fts MATCH ?", "p.lifecycle_state != 'deprecated'", "p.review_state != 'rejected'"]
        params: list[Any] = [fts_query]
        if scope:
            clauses.append("p.scope = ?")
            params.append(scope)
        if workspace_identity:
            clauses.append("p.workspace_identity = ?")
            params.append(workspace_identity)
        params.append(limit)
        with self._connect() as db:
            rows = db.execute(
                f"""
                SELECT p.*, v.*, bm25(knowledge_pattern_fts) AS rank
                FROM knowledge_pattern_fts f
                JOIN knowledge_patterns p ON p.pattern_id = f.pattern_id
                JOIN knowledge_pattern_versions v ON v.pattern_version_id = p.active_version_id
                WHERE {' AND '.join(clauses)}
                ORDER BY rank, p.updated_at DESC LIMIT ?
                """,
                params,
            ).fetchall()
        return [_search_pattern_row(row) for row in rows]

    def _search_incidents(self, fts_query: str, scope: str | None, workspace_identity: str | None, limit: int) -> list[dict[str, Any]]:
        clauses = ["knowledge_incident_fts MATCH ?", "i.status = 'active'"]
        params: list[Any] = [fts_query]
        if scope:
            clauses.append("i.scope = ?")
            params.append(scope)
        if workspace_identity:
            clauses.append("i.workspace_identity = ?")
            params.append(workspace_identity)
        params.append(limit)
        with self._connect() as db:
            rows = db.execute(
                f"""
                SELECT i.*, bm25(knowledge_incident_fts) AS rank
                FROM knowledge_incident_fts f
                JOIN knowledge_incidents i ON i.incident_id = f.incident_id
                WHERE {' AND '.join(clauses)}
                ORDER BY rank, i.updated_at DESC LIMIT ?
                """,
                params,
            ).fetchall()
        return [_search_incident_row(row) for row in rows]

    def _set_stage(
        self,
        job_id: str,
        stage: str,
        *,
        status: str,
        error: str = "",
        processed_count: int = 0,
        increment_attempt: bool = False,
    ) -> dict[str, Any]:
        self.initialize()
        now = _now()
        with self._connect() as db:
            if db.execute("SELECT 1 FROM knowledge_import_jobs WHERE job_id = ?", (job_id,)).fetchone() is None:
                raise KeyError(job_id)
            current = db.execute(
                "SELECT attempt FROM knowledge_import_job_stages WHERE job_id = ? AND stage = ?", (job_id, stage)
            ).fetchone()
            attempt = (int(current["attempt"]) if current else 0) + (1 if increment_attempt else 0)
            if attempt == 0:
                attempt = 1
            db.execute(
                """
                INSERT INTO knowledge_import_job_stages
                    (job_id, stage, status, attempt, processed_count, error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, stage) DO UPDATE SET
                    status = excluded.status, attempt = excluded.attempt,
                    processed_count = excluded.processed_count, error = excluded.error, updated_at = excluded.updated_at
                """,
                (job_id, stage, status, attempt, max(0, int(processed_count)), error, now),
            )
            db.execute("UPDATE knowledge_import_jobs SET updated_at = ? WHERE job_id = ?", (now, job_id))
        return {"job_id": job_id, "stage": stage, "status": status, "attempt": attempt, "processed_count": max(0, int(processed_count)), "error": error}

    def _upsert_incident_fts(self, db: sqlite3.Connection, incident_id: str) -> None:
        db.execute("DELETE FROM knowledge_incident_fts WHERE incident_id = ?", (incident_id,))
        row = db.execute("SELECT * FROM knowledge_incidents WHERE incident_id = ?", (incident_id,)).fetchone()
        db.execute(
            "INSERT INTO knowledge_incident_fts (incident_id, scope, workspace_identity, title, summary, terms) VALUES (?, ?, ?, ?, ?, ?)",
            (row["incident_id"], row["scope"], row["workspace_identity"], _fts_text(row["title"]), _fts_text(row["summary"]), _fts_text(" ".join(_json_list(row["terms_json"])))),
        )

    def _insert_pattern_version(self, db: sqlite3.Connection, pattern_id: str, version_id: str, version_number: int, content: str, terms: Iterable[str], applicability: Iterable[str], exclusions: Iterable[str], now: str) -> None:
        db.execute(
            """
            INSERT INTO knowledge_pattern_versions
                (pattern_version_id, pattern_id, version_number, content, terms_json, applicability_json, exclusions_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (version_id, pattern_id, version_number, content, json.dumps(_strings(terms), ensure_ascii=False), json.dumps(_strings(applicability), ensure_ascii=False), json.dumps(_strings(exclusions), ensure_ascii=False), now),
        )

    def _upsert_pattern_fts(self, db: sqlite3.Connection, pattern_id: str) -> None:
        db.execute("DELETE FROM knowledge_pattern_fts WHERE pattern_id = ?", (pattern_id,))
        row = db.execute(
            """
            SELECT p.*, v.content, v.terms_json, v.applicability_json, v.exclusions_json
            FROM knowledge_patterns p JOIN knowledge_pattern_versions v ON v.pattern_version_id = p.active_version_id
            WHERE p.pattern_id = ?
            """,
            (pattern_id,),
        ).fetchone()
        db.execute(
            """
            INSERT INTO knowledge_pattern_fts
                (pattern_id, scope, workspace_identity, name, content, terms, applicability, exclusions)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["pattern_id"], row["scope"], row["workspace_identity"],
                _fts_text(row["name"]), _fts_text(row["content"]),
                _fts_text(" ".join(_json_list(row["terms_json"]))),
                _fts_text(" ".join(_json_list(row["applicability_json"]))),
                _fts_text(" ".join(_json_list(row["exclusions_json"]))),
            ),
        )

    def _has_schema_table(self) -> bool:
        if not self.db_path.exists():
            return False
        with self._connect() as db:
            row = db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'knowledge_schema'").fetchone()
        return row is not None

    def _backup_before_migration(self, version: int) -> Path:
        backup_path = Path(f"{self.db_path}.pre-v{version}.bak")
        if backup_path.exists():
            return backup_path
        with self._connect() as source, sqlite3.connect(str(backup_path)) as target:
            source.backup(target)
        return backup_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _validate_scope(scope: str, workspace_identity: str) -> None:
    if scope not in _SCOPES:
        raise ValueError(f"unsupported knowledge scope: {scope}")
    if scope == "project" and not str(workspace_identity).strip():
        raise ValueError("project knowledge requires workspace_identity")


def _normalize_locator(locator: dict[str, Any]) -> dict[str, Any]:
    value = dict(locator)
    if not str(value.get("kind") or "").strip():
        raise ValueError("source locator kind is required")
    value["kind"] = str(value["kind"])
    return value


def _strings(values: Iterable[str] | None) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values or () if str(value).strip()))


def _json_list(value: str) -> list[str]:
    try:
        raw = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return _strings(raw if isinstance(raw, list) else ())


def _fts_query(query: str) -> str:
    terms: list[str] = []
    for raw in str(query or "").split():
        if not raw.strip():
            continue
        terms.append(raw.replace('"', '""'))
        terms.extend(_cjk_terms(raw))
    return " ".join(f'"{term}"' for term in terms)


def _fts_text(value: str) -> str:
    text = str(value or "")
    return " ".join([text, *_cjk_terms(text)])


def _is_cjk(character: str) -> bool:
    return "\u3400" <= character <= "\u9fff"


def _cjk_terms(value: str) -> list[str]:
    characters = [character for character in str(value) if _is_cjk(character)]
    bigrams = ["".join(characters[index:index + 2]) for index in range(len(characters) - 1)]
    return [*characters, *bigrams]


def _incident_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["terms"] = _json_list(result.pop("terms_json", "[]"))
    return result


def _pattern_version_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for key in ("terms", "applicability", "exclusions"):
        result[key] = _json_list(result.pop(f"{key}_json", "[]"))
    return result


def _search_pattern_row(row: sqlite3.Row) -> dict[str, Any]:
    result = _pattern_version_row(row)
    result["record_id"] = result["pattern_id"]
    result["record_type"] = "pattern"
    result["title"] = result["name"]
    return result


def _search_incident_row(row: sqlite3.Row) -> dict[str, Any]:
    result = _incident_row(row)
    result["record_id"] = result["incident_id"]
    result["record_type"] = "incident"
    return result


_SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS knowledge_schema (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_source_documents (
    source_document_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    source_identity TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_kind, source_identity)
);
CREATE TABLE IF NOT EXISTS knowledge_source_snapshots (
    source_snapshot_id TEXT PRIMARY KEY,
    source_document_id TEXT NOT NULL,
    snapshot_number INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    content BLOB NOT NULL,
    revision TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL,
    workspace_identity TEXT NOT NULL DEFAULT '',
    project_identity TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(source_document_id, sha256),
    UNIQUE(source_document_id, snapshot_number),
    FOREIGN KEY(source_document_id) REFERENCES knowledge_source_documents(source_document_id)
);
CREATE TABLE IF NOT EXISTS knowledge_source_locators (
    locator_id TEXT PRIMARY KEY,
    source_snapshot_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    kind TEXT NOT NULL,
    locator_json TEXT NOT NULL,
    excerpt TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(source_snapshot_id) REFERENCES knowledge_source_snapshots(source_snapshot_id)
);
CREATE TABLE IF NOT EXISTS knowledge_incidents (
    incident_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    terms_json TEXT NOT NULL DEFAULT '[]',
    scope TEXT NOT NULL,
    workspace_identity TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_incident_sources (
    incident_id TEXT NOT NULL,
    source_snapshot_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (incident_id, source_snapshot_id),
    FOREIGN KEY(incident_id) REFERENCES knowledge_incidents(incident_id),
    FOREIGN KEY(source_snapshot_id) REFERENCES knowledge_source_snapshots(source_snapshot_id)
);
CREATE TABLE IF NOT EXISTS knowledge_patterns (
    pattern_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    scope TEXT NOT NULL,
    workspace_identity TEXT NOT NULL DEFAULT '',
    active_version_id TEXT NOT NULL,
    review_state TEXT NOT NULL DEFAULT 'unreviewed',
    lifecycle_state TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_pattern_versions (
    pattern_version_id TEXT PRIMARY KEY,
    pattern_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    content TEXT NOT NULL,
    terms_json TEXT NOT NULL DEFAULT '[]',
    applicability_json TEXT NOT NULL DEFAULT '[]',
    exclusions_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE(pattern_id, version_number),
    UNIQUE(pattern_id, pattern_version_id),
    FOREIGN KEY(pattern_id) REFERENCES knowledge_patterns(pattern_id)
);
CREATE TABLE IF NOT EXISTS knowledge_incident_pattern_links (
    link_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    pattern_id TEXT NOT NULL,
    pattern_version_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(incident_id, pattern_id, pattern_version_id),
    FOREIGN KEY(incident_id) REFERENCES knowledge_incidents(incident_id),
    FOREIGN KEY(pattern_id) REFERENCES knowledge_patterns(pattern_id),
    FOREIGN KEY(pattern_version_id) REFERENCES knowledge_pattern_versions(pattern_version_id),
    FOREIGN KEY(pattern_id, pattern_version_id) REFERENCES knowledge_pattern_versions(pattern_id, pattern_version_id)
);
CREATE TABLE IF NOT EXISTS knowledge_import_jobs (
    job_id TEXT PRIMARY KEY,
    source_count INTEGER NOT NULL,
    scope TEXT NOT NULL,
    workspace_identity TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_import_job_stages (
    job_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    processed_count INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (job_id, stage),
    FOREIGN KEY(job_id) REFERENCES knowledge_import_jobs(job_id)
);
CREATE TABLE IF NOT EXISTS knowledge_import_job_sources (
    job_id TEXT NOT NULL,
    source_snapshot_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    filename TEXT NOT NULL,
    parser TEXT NOT NULL,
    parse_status TEXT NOT NULL,
    parse_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (job_id, source_snapshot_id),
    UNIQUE(job_id, position),
    FOREIGN KEY(job_id) REFERENCES knowledge_import_jobs(job_id),
    FOREIGN KEY(source_snapshot_id) REFERENCES knowledge_source_snapshots(source_snapshot_id)
);
CREATE TABLE IF NOT EXISTS knowledge_import_job_context (
    job_id TEXT PRIMARY KEY,
    context_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES knowledge_import_jobs(job_id)
);
CREATE TABLE IF NOT EXISTS knowledge_feedback (
    feedback_id TEXT PRIMARY KEY,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    workspace_identity TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_merge_proposals (
    proposal_id TEXT PRIMARY KEY,
    subject_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    similarity REAL NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS knowledge_incident_sources_fk_insert
BEFORE INSERT ON knowledge_incident_sources
WHEN NOT EXISTS (SELECT 1 FROM knowledge_incidents WHERE incident_id = NEW.incident_id)
  OR NOT EXISTS (SELECT 1 FROM knowledge_source_snapshots WHERE source_snapshot_id = NEW.source_snapshot_id)
BEGIN SELECT RAISE(ABORT, 'knowledge incident source foreign key violation'); END;
CREATE TRIGGER IF NOT EXISTS knowledge_links_fk_insert
BEFORE INSERT ON knowledge_incident_pattern_links
WHEN NOT EXISTS (SELECT 1 FROM knowledge_incidents WHERE incident_id = NEW.incident_id)
  OR NOT EXISTS (SELECT 1 FROM knowledge_patterns WHERE pattern_id = NEW.pattern_id)
  OR NOT EXISTS (
      SELECT 1 FROM knowledge_pattern_versions
      WHERE pattern_id = NEW.pattern_id AND pattern_version_id = NEW.pattern_version_id
  )
BEGIN SELECT RAISE(ABORT, 'knowledge incident pattern link foreign key violation'); END;
CREATE INDEX IF NOT EXISTS idx_knowledge_snapshots_scope ON knowledge_source_snapshots(scope, workspace_identity);
CREATE INDEX IF NOT EXISTS idx_knowledge_incidents_scope ON knowledge_incidents(scope, workspace_identity, status);
CREATE INDEX IF NOT EXISTS idx_knowledge_patterns_scope ON knowledge_patterns(scope, workspace_identity, lifecycle_state, review_state);
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_incident_fts USING fts5(
    incident_id UNINDEXED, scope UNINDEXED, workspace_identity UNINDEXED,
    title, summary, terms, tokenize = 'unicode61 tokenchars ''_-/.'''
);
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_pattern_fts USING fts5(
    pattern_id UNINDEXED, scope UNINDEXED, workspace_identity UNINDEXED,
    name, content, terms, applicability, exclusions, tokenize = 'unicode61 tokenchars ''_-/.'''
);
"""
