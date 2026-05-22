"""Sprint 2: workspace_chat service integration tests.

Tests persist_*, _load_*, and build_chat_messages using the sqlite_db fixture
(V2 services connect via aiosqlite.connect(settings.sqlite_db) directly).
"""

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
    db_path: str, ws_id: str, report_type: str, status: str, content: str | None = None
) -> str:
    rid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
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
