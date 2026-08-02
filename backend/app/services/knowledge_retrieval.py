"""Federated, bounded retrieval over local experience and injectable legacy stores."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from app.services.knowledge_store import KnowledgeStore


class LegacyKnowledgeProvider(Protocol):
    provider_name: str

    def search(self, query: str, *, limit: int, workspace_identity: str | None = None) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class RetrievalResult:
    records: list[dict[str, Any]]
    fts_candidate_count: int
    embedding_status: str
    provider_statuses: list[dict[str, str]]


class FederatedKnowledgeRetriever:
    """Project-first FTS retrieval with optional, bounded embedding reranking."""

    def __init__(
        self,
        store: KnowledgeStore,
        *,
        legacy_providers: Iterable[LegacyKnowledgeProvider] = (),
        embedder: Callable[[str, list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.store = store
        self.legacy_providers = list(legacy_providers)
        self.embedder = embedder

    def retrieve(
        self,
        query: str,
        *,
        workspace_identity: str | None = None,
        max_results: int = 12,
        scopes: Iterable[str] = ("project", "personal_global"),
        include_experience: bool = True,
    ) -> RetrievalResult:
        requested = max(1, int(max_results))
        candidate_limit = min(100, requested * 4)
        selected_scopes = {str(scope) for scope in scopes}
        project_records = (
            self.store.search_experience(query, scope="project", workspace_identity=workspace_identity, limit=candidate_limit)
            if include_experience and workspace_identity and "project" in selected_scopes
            else []
        )
        global_records = (
            self.store.search_experience(
                query,
                scope="personal_global",
                limit=candidate_limit,
            )
            if include_experience and "personal_global" in selected_scopes
            else []
        )
        local_records = _dedupe_records([*project_records, *global_records])[:candidate_limit]
        fts_candidate_count = len(local_records)
        records = list(local_records)
        embedding_status = "degraded_unavailable"
        if self.embedder is not None:
            try:
                records = list(self.embedder(query, records))
                embedding_status = "applied"
            except Exception:
                embedding_status = "degraded_error"
        records = _dedupe_records(records)
        records = _project_first(records, workspace_identity)
        provider_statuses: list[dict[str, str]] = []
        for provider in self.legacy_providers:
            try:
                legacy = provider.search(query, limit=candidate_limit, workspace_identity=workspace_identity)
            except Exception as exc:
                provider_statuses.append({"provider": provider.provider_name, "status": "degraded_error", "error": type(exc).__name__})
                continue
            provider_statuses.append({"provider": provider.provider_name, "status": "available"})
            for record in legacy:
                copied = dict(record)
                copied.setdefault("provider", provider.provider_name)
                copied.setdefault("record_id", f"{provider.provider_name}:{copied.get('title', '')}")
                record_scope = str(copied.get("scope") or "personal_global")
                if record_scope not in selected_scopes:
                    continue
                if record_scope == "project" and not workspace_identity:
                    continue
                record_identity = str(copied.get("workspace_identity") or "")
                if (
                    record_scope == "project"
                    and record_identity
                    and record_identity != workspace_identity
                ):
                    continue
                records.append(copied)
        return RetrievalResult(records=_project_first(_dedupe_records(records), workspace_identity)[:requested], fts_candidate_count=fts_candidate_count, embedding_status=embedding_status, provider_statuses=provider_statuses)


class SemanticLibraryKnowledgeProvider:
    provider_name = "semantic_cases"

    def __init__(self, store: Any) -> None:
        self.store = store

    def search(
        self,
        query: str,
        *,
        limit: int,
        workspace_identity: str | None = None,
    ) -> list[dict[str, Any]]:
        del workspace_identity
        return [
            {
                **_object_payload(item),
                "record_id": f"semantic:{getattr(item, 'semantic_id', '')}",
                "record_type": "semantic_case",
                "scope": "personal_global",
            }
            for item in self.store.retrieve(query=query, limit=limit)
        ]


class EvidenceMemoryKnowledgeProvider:
    provider_name = "evidence_memory"

    def __init__(self, store: Any, *, workspace_id: str) -> None:
        self.store = store
        self.workspace_id = workspace_id

    def search(
        self,
        query: str,
        *,
        limit: int,
        workspace_identity: str | None = None,
    ) -> list[dict[str, Any]]:
        del workspace_identity
        return [
            {
                **_object_payload(item),
                "record_id": f"evidence:{getattr(item, 'evidence_id', '')}",
                "record_type": "evidence_memory",
                "scope": "project",
            }
            for item in self.store.search_analysis_memory(
                query,
                workspace_id=self.workspace_id,
                limit=limit,
            )
        ]


class WorkspaceMaterialKnowledgeProvider:
    provider_name = "materials"

    def __init__(self, db_path: str | Path, *, workspace_id: str) -> None:
        self.db_path = Path(db_path)
        self.workspace_id = workspace_id

    def search(
        self,
        query: str,
        *,
        limit: int,
        workspace_identity: str | None = None,
    ) -> list[dict[str, Any]]:
        terms = [term.casefold() for term in query.split() if term.strip()]
        if not terms:
            return []
        with sqlite3.connect(str(self.db_path)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT id, filename, content_type, file_path
                FROM workspace_materials
                WHERE workspace_id = ? AND is_active = TRUE
                ORDER BY created_at DESC
                """,
                (self.workspace_id,),
            ).fetchall()
        matches: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            path = Path(str(row["file_path"] or ""))
            try:
                content = path.read_text(encoding="utf-8", errors="replace")[:100_000]
            except OSError:
                continue
            haystack = f"{row['filename']}\n{content}".casefold()
            score = sum(1 for term in terms if term in haystack)
            if not score:
                continue
            matches.append((score, {
                "record_id": f"material:{row['id']}",
                "record_type": "material",
                "title": str(row["filename"]),
                "content": content[:4000],
                "content_type": str(row["content_type"] or ""),
                "source_path": str(path),
                "scope": "project",
                "workspace_identity": str(workspace_identity or ""),
            }))
        matches.sort(key=lambda item: (-item[0], item[1]["record_id"]))
        return [item for _score, item in matches[: max(1, int(limit))]]


def _object_payload(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    return dict(vars(value))


def _dedupe_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for record in records:
        key = str(record.get("record_id") or record.get("pattern_id") or record.get("incident_id") or record.get("title") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(record)
    return result


def _project_first(records: list[dict[str, Any]], workspace_identity: str | None) -> list[dict[str, Any]]:
    if not workspace_identity:
        return records
    return sorted(
        records,
        key=lambda record: 0 if record.get("scope") == "project" and record.get("workspace_identity") == workspace_identity else 1,
    )
