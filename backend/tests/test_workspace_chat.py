"""Sprint 2: workspace_chat service integration tests.

Tests persist_*, _load_*, and build_chat_messages using the sqlite_db fixture
(V2 services connect via aiosqlite.connect(settings.sqlite_db) directly).
"""

import struct
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_workspace(db_path: str, ws_id: str = "ws-1") -> str:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
            "VALUES (?, 'test-ws', '/repo', 1, ?, ?)",
            (ws_id, now, now),
        )
        await db.commit()
    return ws_id


async def _seed_chats(db_path: str, ws_id: str, count: int) -> None:
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    async with aiosqlite.connect(db_path) as db:
        for i in range(count):
            ts = (base + timedelta(seconds=i)).isoformat()
            role = "user" if i % 2 == 0 else "assistant"
            await db.execute(
                "INSERT INTO workspace_chats (id, workspace_id, mode, role, content, created_at) "
                "VALUES (?, ?, 'freeqa', ?, ?, ?)",
                (str(uuid.uuid4()), ws_id, role, f"msg-{i}", ts),
            )
        await db.commit()


async def _seed_report(
    db_path: str,
    ws_id: str,
    report_type: str,
    status: str,
    content: str | None = None,
    created_at: str | None = None,
) -> str:
    rid = str(uuid.uuid4())
    now = created_at or datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO workspace_reports (id, workspace_id, report_type, title, content, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rid, ws_id, report_type, f"T-{report_type}", content, status, now),
        )
        await db.commit()
    return rid


async def _seed_material(
    db_path: str, ws_id: str, filename: str, file_path: str, *, is_active: bool = True
) -> str:
    mid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO workspace_materials "
            "(id, workspace_id, filename, content_type, file_path, is_active, created_at) "
            "VALUES (?, ?, ?, 'other', ?, ?, ?)",
            (mid, ws_id, filename, file_path, is_active, now),
        )
        await db.commit()
    return mid


class TestTrustworthyChatProductContracts:
    async def test_freeqa_includes_trust_contract_when_code_evidence_missing(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)

        with patch(
            "app.services.workspace_chat._search_gitnexus",
            new_callable=AsyncMock,
            return_value=[],
        ):
            from app.services.workspace_chat import build_chat_messages

            msgs = await build_chat_messages(ws_id, "/repo", "what is logging?", "freeqa")

        system = msgs[0]["content"]
        assert "MODE_FREEQA" in system
        assert "CODETALK_EVIDENCE_STATUS_BEGIN" in system
        assert "code_snippets: 0" in system
        assert "Claims without direct evidence must be marked" in system
        assert "materials: not_used_in_freeqa" in system
        assert "reports: not_used_in_freeqa" in system

    async def test_targeted_mode_has_distinct_evidence_contract(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        await _seed_report(sqlite_db, ws_id, "test_design", "completed", "Report body")

        with patch(
            "app.services.workspace_chat._search_gitnexus",
            new_callable=AsyncMock,
            return_value=["```\n// log.c\nspdk_log_open();\n```"],
        ), patch(
            "app.services.workspace_chat._load_materials_context",
            new_callable=AsyncMock,
            return_value=["material one", "material two"],
        ):
            from app.services.workspace_chat import build_chat_messages

            msgs = await build_chat_messages(ws_id, "/repo", "test strategy", "targeted")

        system = msgs[0]["content"]
        assert "MODE_TARGETED" in system
        assert "code_snippets: 1" in system
        assert "materials: 2" in system
        assert "reports: 1" in system
        assert "Required output sections" in system
        assert "Evidence status" in system

    async def test_long_history_adds_memory_summary_without_dropping_recent_context(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        await _seed_chats(sqlite_db, ws_id, 80)

        with patch(
            "app.services.workspace_chat._search_gitnexus",
            new_callable=AsyncMock,
            return_value=[],
        ):
            from app.services.workspace_chat import build_chat_messages

            msgs = await build_chat_messages(ws_id, "/repo", "continue", "freeqa")

        system = msgs[0]["content"]
        assert "CODETALK_MEMORY_SUMMARY" in system
        assert "msg-0" in system
        assert "msg-59" in system
        recent_contents = [m["content"] for m in msgs[1:-1]]
        assert "msg-60" in recent_contents
        assert "msg-79" in recent_contents


# ---------------------------------------------------------------------------
# persist_user_message / persist_assistant_reply
# ---------------------------------------------------------------------------


class TestPersistMessages:
    async def test_persist_user_message(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        from app.services.workspace_chat import persist_user_message

        await persist_user_message(ws_id, "freeqa", "hello world")

        async with aiosqlite.connect(sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM workspace_chats WHERE workspace_id = ?", (ws_id,)
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
        assert len(rows) == 1
        assert rows[0]["role"] == "user"
        assert rows[0]["mode"] == "freeqa"
        assert rows[0]["content"] == "hello world"

    async def test_persist_assistant_reply(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        from app.services.workspace_chat import persist_assistant_reply

        await persist_assistant_reply(ws_id, "targeted", "analysis result")

        async with aiosqlite.connect(sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM workspace_chats WHERE workspace_id = ?", (ws_id,)
            ) as cur:
                rows = [dict(r) for r in await cur.fetchall()]
        assert len(rows) == 1
        assert rows[0]["role"] == "assistant"
        assert rows[0]["mode"] == "targeted"

    async def test_persist_generates_unique_ids(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        from app.services.workspace_chat import persist_user_message

        await persist_user_message(ws_id, "freeqa", "msg1")
        await persist_user_message(ws_id, "freeqa", "msg2")

        async with aiosqlite.connect(sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id FROM workspace_chats WHERE workspace_id = ?", (ws_id,)
            ) as cur:
                ids = [r["id"] for r in await cur.fetchall()]
        assert len(set(ids)) == 2


# ---------------------------------------------------------------------------
# _load_history
# ---------------------------------------------------------------------------


class TestLoadHistory:
    async def test_chronological_order(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        await _seed_chats(sqlite_db, ws_id, 5)

        from app.services.workspace_chat import _load_history

        history = await _load_history(ws_id)
        assert len(history) == 5
        assert history[0]["content"] == "msg-0"
        assert history[4]["content"] == "msg-4"

    async def test_caps_at_50(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        await _seed_chats(sqlite_db, ws_id, 60)

        from app.services.workspace_chat import _load_history

        history = await _load_history(ws_id)
        assert len(history) == 50
        assert history[0]["content"] == "msg-10"
        assert history[-1]["content"] == "msg-59"

    async def test_empty_workspace(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)

        from app.services.workspace_chat import _load_history

        assert await _load_history(ws_id) == []

    async def test_returns_role_and_content_only(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        await _seed_chats(sqlite_db, ws_id, 1)

        from app.services.workspace_chat import _load_history

        history = await _load_history(ws_id)
        assert set(history[0].keys()) == {"role", "content"}


# ---------------------------------------------------------------------------
# _load_report_summaries
# ---------------------------------------------------------------------------


class TestLoadReportSummaries:
    async def test_only_completed(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        await _seed_report(sqlite_db, ws_id, "module_map", "completed", "Good content")
        await _seed_report(sqlite_db, ws_id, "business_flow", "pending", "Pending")
        await _seed_report(sqlite_db, ws_id, "test_design", "failed", "Failed")

        from app.services.workspace_chat import _load_report_summaries

        summaries = await _load_report_summaries(ws_id)
        assert len(summaries) == 1
        assert "项目与模块地图" in summaries[0]

    async def test_label_mapping(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        await _seed_report(sqlite_db, ws_id, "business_flow", "completed", "x")

        from app.services.workspace_chat import _load_report_summaries

        summaries = await _load_report_summaries(ws_id)
        assert "关键业务流程分析" in summaries[0]

    async def test_unknown_type_uses_raw(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        await _seed_report(sqlite_db, ws_id, "custom_type", "completed", "x")

        from app.services.workspace_chat import _load_report_summaries

        summaries = await _load_report_summaries(ws_id)
        assert "custom_type" in summaries[0]

    async def test_truncates_long_content(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        await _seed_report(sqlite_db, ws_id, "module_map", "completed", "y" * 1000)

        from app.services.workspace_chat import _load_report_summaries

        summaries = await _load_report_summaries(ws_id)
        assert summaries[0].endswith("…")

    async def test_skips_empty_content(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        await _seed_report(sqlite_db, ws_id, "module_map", "completed", "")
        await _seed_report(sqlite_db, ws_id, "business_flow", "completed", "   ")

        from app.services.workspace_chat import _load_report_summaries

        assert await _load_report_summaries(ws_id) == []

    async def test_caps_to_recent_completed_reports(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        base = datetime(2025, 1, 1, tzinfo=timezone.utc)
        for i in range(12):
            await _seed_report(
                sqlite_db,
                ws_id,
                "custom_type",
                "completed",
                f"report-{i}",
                (base + timedelta(minutes=i)).isoformat(),
            )

        from app.services.workspace_chat import _load_report_summaries

        summaries = await _load_report_summaries(ws_id)

        assert len(summaries) == 8
        joined = "\n".join(summaries)
        assert "report-0" not in joined
        assert "report-3" not in joined
        assert "report-4" in joined
        assert "report-11" in joined


# ---------------------------------------------------------------------------
# _load_materials_text
# ---------------------------------------------------------------------------


class TestLoadMaterialsText:
    async def test_reads_active_materials(self, sqlite_db, tmp_path):
        ws_id = await _seed_workspace(sqlite_db)
        mat_file = tmp_path / "doc.md"
        mat_file.write_text("# Document content", encoding="utf-8")
        await _seed_material(sqlite_db, ws_id, "doc.md", str(mat_file))

        from app.services.workspace_chat import _load_materials_text

        texts = await _load_materials_text(ws_id)
        assert len(texts) == 1
        assert "doc.md" in texts[0]
        assert "# Document content" in texts[0]

    async def test_skips_inactive(self, sqlite_db, tmp_path):
        ws_id = await _seed_workspace(sqlite_db)
        f = tmp_path / "inactive.md"
        f.write_text("content", encoding="utf-8")
        await _seed_material(sqlite_db, ws_id, "inactive.md", str(f), is_active=False)

        from app.services.workspace_chat import _load_materials_text

        assert await _load_materials_text(ws_id) == []

    async def test_truncates_long_file(self, sqlite_db, tmp_path):
        ws_id = await _seed_workspace(sqlite_db)
        f = tmp_path / "big.md"
        f.write_text("z" * 5000, encoding="utf-8")
        await _seed_material(sqlite_db, ws_id, "big.md", str(f))

        from app.services.workspace_chat import _load_materials_text

        texts = await _load_materials_text(ws_id)
        assert texts[0].endswith("…")

    async def test_graceful_on_missing_file(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        await _seed_material(sqlite_db, ws_id, "ghost.md", "/nonexistent/ghost.md")

        from app.services.workspace_chat import _load_materials_text

        assert await _load_materials_text(ws_id) == []


# ---------------------------------------------------------------------------
# _load_materials_context — exercises real RAG fallback/supplement branches
# ---------------------------------------------------------------------------


class TestLoadMaterialsContext:
    async def test_rag_exception_falls_back_to_full_text(self, sqlite_db, tmp_path):
        ws_id = await _seed_workspace(sqlite_db)
        f = tmp_path / "doc.md"
        f.write_text("Full text fallback content", encoding="utf-8")
        await _seed_material(sqlite_db, ws_id, "doc.md", str(f))

        with patch(
            "app.services.material_rag.retrieve_chunks",
            new_callable=AsyncMock,
            side_effect=RuntimeError("embedding service down"),
        ):
            from app.services.workspace_chat import _load_materials_context

            result = await _load_materials_context(ws_id, "query")

        assert len(result) == 1
        assert "doc.md" in result[0]
        assert "Full text fallback content" in result[0]

    async def test_empty_rag_falls_back_to_full_text(self, sqlite_db, tmp_path):
        ws_id = await _seed_workspace(sqlite_db)
        f = tmp_path / "doc.md"
        f.write_text("Fallback content", encoding="utf-8")
        await _seed_material(sqlite_db, ws_id, "doc.md", str(f))

        with patch(
            "app.services.material_rag.retrieve_chunks",
            new_callable=AsyncMock,
            return_value=[],
        ):
            from app.services.workspace_chat import _load_materials_context

            result = await _load_materials_context(ws_id, "query")

        assert len(result) == 1
        assert "Fallback content" in result[0]

    async def test_rag_results_formatted_with_score(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        mat_id = "mat-rag"
        now = datetime.now(timezone.utc).isoformat()
        blob = struct.pack("3f", 0.1, 0.2, 0.3)
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) "
                "VALUES ('active_embedding_model_id', 'model-x')"
            )
            await db.execute(
                "INSERT INTO workspace_materials "
                "(id, workspace_id, filename, content_type, file_path, is_active, created_at) "
                "VALUES (?, ?, 'doc.md', 'other', '/tmp/doc.md', TRUE, ?)",
                (mat_id, ws_id, now),
            )
            await db.execute(
                "INSERT INTO material_chunks "
                "(id, material_id, workspace_id, embedding_model_id, "
                "chunk_index, content, embedding, created_at) "
                "VALUES ('c1', ?, ?, 'model-x', 0, 'chunk text', ?, ?)",
                (mat_id, ws_id, blob, now),
            )
            await db.commit()

        rag_hit = [{
            "content": "chunk text",
            "filename": "doc.md",
            "material_id": mat_id,
            "score": 0.85,
        }]
        with patch(
            "app.services.material_rag.retrieve_chunks",
            new_callable=AsyncMock,
            return_value=rag_hit,
        ):
            from app.services.workspace_chat import _load_materials_context

            result = await _load_materials_context(ws_id, "query")

        assert len(result) == 1
        assert "doc.md" in result[0]
        assert "0.85" in result[0]
        assert "chunk text" in result[0]

    async def test_supplements_unembedded_materials(self, sqlite_db, tmp_path):
        ws_id = await _seed_workspace(sqlite_db)
        now = datetime.now(timezone.utc).isoformat()
        blob = struct.pack("3f", 0.1, 0.2, 0.3)

        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) "
                "VALUES ('active_embedding_model_id', 'model-x')"
            )
            await db.execute(
                "INSERT INTO workspace_materials "
                "(id, workspace_id, filename, content_type, file_path, is_active, created_at) "
                "VALUES ('mat-emb', ?, 'embedded.md', 'other', '/tmp/e.md', TRUE, ?)",
                (ws_id, now),
            )
            await db.execute(
                "INSERT INTO material_chunks "
                "(id, material_id, workspace_id, embedding_model_id, "
                "chunk_index, content, embedding, created_at) "
                "VALUES ('c1', 'mat-emb', ?, 'model-x', 0, 'embedded chunk', ?, ?)",
                (ws_id, blob, now),
            )
            await db.commit()

        unemb_file = tmp_path / "unembedded.md"
        unemb_file.write_text("Supplementary content", encoding="utf-8")
        await _seed_material(sqlite_db, ws_id, "unembedded.md", str(unemb_file))

        rag_hit = [{
            "content": "embedded chunk",
            "filename": "embedded.md",
            "material_id": "mat-emb",
            "score": 0.9,
        }]
        with patch(
            "app.services.material_rag.retrieve_chunks",
            new_callable=AsyncMock,
            return_value=rag_hit,
        ):
            from app.services.workspace_chat import _load_materials_context

            result = await _load_materials_context(ws_id, "query")

        assert len(result) == 2
        assert any("embedded chunk" in r and "0.9" in r for r in result)
        assert any("Supplementary content" in r for r in result)


# ---------------------------------------------------------------------------
# build_chat_messages
# ---------------------------------------------------------------------------


class TestBuildChatMessages:
    async def test_freeqa_mode(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)

        with patch(
            "app.services.workspace_chat._search_gitnexus",
            new_callable=AsyncMock,
            return_value=[],
        ):
            from app.services.workspace_chat import build_chat_messages

            msgs = await build_chat_messages(ws_id, "/repo", "what is this?", "freeqa")

        assert msgs[0]["role"] == "system"
        assert "代码库问答助手" in msgs[0]["content"]
        assert msgs[-1] == {"role": "user", "content": "what is this?"}

    async def test_targeted_mode_includes_reports(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        await _seed_report(sqlite_db, ws_id, "module_map", "completed", "Module map body")

        with patch(
            "app.services.workspace_chat._search_gitnexus",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.services.workspace_chat._load_materials_context",
            new_callable=AsyncMock,
            return_value=["**[req.md]** mock context"],
        ):
            from app.services.workspace_chat import build_chat_messages

            msgs = await build_chat_messages(ws_id, "/repo", "analyze", "targeted")

        system = msgs[0]["content"]
        assert "结构化分析助手" in system
        assert "项目与模块地图" in system

    async def test_includes_history(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        await _seed_chats(sqlite_db, ws_id, 4)

        with patch(
            "app.services.workspace_chat._search_gitnexus",
            new_callable=AsyncMock,
            return_value=[],
        ):
            from app.services.workspace_chat import build_chat_messages

            msgs = await build_chat_messages(ws_id, "/repo", "next q", "freeqa")

        assert len(msgs) == 6  # system + 4 history + user
        assert msgs[1]["content"] == "msg-0"
        assert msgs[-1]["content"] == "next q"

    async def test_gitnexus_snippets_in_prompt(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)
        snippets = ["```\n// main.py\ndef hello(): pass\n```"]

        with patch(
            "app.services.workspace_chat._search_gitnexus",
            new_callable=AsyncMock,
            return_value=snippets,
        ):
            from app.services.workspace_chat import build_chat_messages

            msgs = await build_chat_messages(ws_id, "/repo", "show code", "freeqa")

        assert "main.py" in msgs[0]["content"]

    async def test_freeqa_omits_materials_section(self, sqlite_db):
        ws_id = await _seed_workspace(sqlite_db)

        with patch(
            "app.services.workspace_chat._search_gitnexus",
            new_callable=AsyncMock,
            return_value=[],
        ):
            from app.services.workspace_chat import build_chat_messages

            msgs = await build_chat_messages(ws_id, "/repo", "q", "freeqa")

        assert "项目材料摘要" not in msgs[0]["content"]
