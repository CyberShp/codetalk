from __future__ import annotations

from app.services.knowledge_retrieval import FederatedKnowledgeRetriever
from app.services.knowledge_store import KnowledgeStore


class _LegacyProvider:
    provider_name = "legacy_semantic"

    def search(self, query, *, limit, workspace_identity=None):
        return [{"record_type": "legacy", "title": f"legacy {query}", "scope": "personal_global"}]


class _UnavailableLegacyProvider:
    provider_name = "legacy_evidence"

    def search(self, query, *, limit, workspace_identity=None):
        raise RuntimeError("legacy store unavailable")


def test_retrieval_ranks_current_project_before_global_and_searches_chinese_and_english(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.sqlite3")
    store.create_pattern(
        name="global window update delay",
        content="DTOE window update becomes slow in degraded network.",
        scope="personal_global",
        terms=["DTOE", "window"],
    )
    project = store.create_pattern(
        name="项目窗口资源迟缓",
        content="网络亚健康下 win 窗口资源更新迟缓。",
        scope="project",
        workspace_identity="codehub.example/storage/array",
        terms=["DTOE", "窗口资源"],
    )

    result = FederatedKnowledgeRetriever(store, legacy_providers=[_LegacyProvider()]).retrieve(
        "DTOE 窗口资源",
        workspace_identity="codehub.example/storage/array",
        max_results=5,
    )

    assert result.embedding_status == "degraded_unavailable"
    assert result.records[0]["record_id"] == project["pattern_id"]
    assert any(record["record_type"] == "legacy" for record in result.records)
    assert result.provider_statuses == [{"provider": "legacy_semantic", "status": "available"}]


def test_retrieval_matches_partial_chinese_terms_and_keeps_project_first_after_embedding(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.sqlite3")
    global_pattern = store.create_pattern(
        name="全局窗口资源", content="网络亚健康时窗口资源更新迟缓", scope="personal_global"
    )
    project_pattern = store.create_pattern(
        name="项目窗口资源", content="DTOE 的窗口资源更新迟缓", scope="project", workspace_identity="codehub.example/storage/array"
    )

    def reverse_embedder(_query, records):
        return list(reversed(records))

    result = FederatedKnowledgeRetriever(store, embedder=reverse_embedder).retrieve(
        "窗口", workspace_identity="codehub.example/storage/array", max_results=3
    )

    assert result.embedding_status == "applied"
    assert result.records[0]["record_id"] == project_pattern["pattern_id"]
    assert global_pattern["pattern_id"] in [record["record_id"] for record in result.records]


def test_retrieval_bounds_fts_candidates_and_reports_embedding_failure_explicitly(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.sqlite3")
    for index in range(12):
        store.create_pattern(
            name=f"resource recovery {index}",
            content="cmdsn resource recovery behavior",
            scope="personal_global",
        )

    def unavailable_embedder(_query, _records):
        raise RuntimeError("embedding provider offline")

    result = FederatedKnowledgeRetriever(store, legacy_providers=[_UnavailableLegacyProvider()], embedder=unavailable_embedder).retrieve(
        "cmdsn resource",
        max_results=2,
    )

    assert len(result.records) == 2
    assert result.fts_candidate_count <= 8
    assert result.embedding_status == "degraded_error"
    assert result.provider_statuses == [{"provider": "legacy_evidence", "status": "degraded_error", "error": "RuntimeError"}]


def test_typed_legacy_providers_preserve_source_identity(tmp_path):
    import sqlite3
    from types import SimpleNamespace

    from app.services.knowledge_retrieval import (
        EvidenceMemoryKnowledgeProvider,
        SemanticLibraryKnowledgeProvider,
        WorkspaceMaterialKnowledgeProvider,
    )

    semantic = SimpleNamespace(
        retrieve=lambda **_kwargs: [
            SimpleNamespace(semantic_id="sem-1", scenario="CmdSN recovery")
        ]
    )
    evidence = SimpleNamespace(
        search_analysis_memory=lambda *_args, **_kwargs: [
            SimpleNamespace(evidence_id="ev-1", text="current evidence")
        ]
    )
    material_path = tmp_path / "incident.md"
    material_path.write_text("DTOE window update delay", encoding="utf-8")
    materials_db = tmp_path / "materials.sqlite3"
    with sqlite3.connect(materials_db) as db:
        db.execute(
            """
            CREATE TABLE workspace_materials (
                id TEXT, workspace_id TEXT, filename TEXT, content_type TEXT,
                file_path TEXT, is_active BOOLEAN, created_at TEXT
            )
            """
        )
        db.execute(
            "INSERT INTO workspace_materials VALUES (?, ?, ?, ?, ?, TRUE, ?)",
            ("mat-1", "ws-1", "incident.md", "text/markdown", str(material_path), "now"),
        )

    semantic_records = SemanticLibraryKnowledgeProvider(semantic).search(
        "CmdSN", limit=3
    )
    evidence_records = EvidenceMemoryKnowledgeProvider(
        evidence, workspace_id="ws-1"
    ).search("evidence", limit=3)
    material_records = WorkspaceMaterialKnowledgeProvider(
        materials_db, workspace_id="ws-1"
    ).search(
        "DTOE window", limit=3, workspace_identity="codehub.local/storage/array"
    )

    assert semantic_records[0]["record_id"] == "semantic:sem-1"
    assert evidence_records[0]["record_id"] == "evidence:ev-1"
    assert material_records[0]["record_id"] == "material:mat-1"
    assert material_records[0]["workspace_identity"] == "codehub.local/storage/array"
