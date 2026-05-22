"""Sprint 3: export_service tests.

Pure function tests for md-zip/xml exports, plus DB integration tests
for export_workspace_reports and export_workspace_chat.
"""

import io
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone

import aiosqlite
import pytest

from app.services.export_service import (
    _ReportDoc,
    _dispatch,
    _export_md_zip,
    _export_xml,
    export_workspace_chat,
    export_workspace_reports,
)

pytestmark = [pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# _export_md_zip — pure function
# ---------------------------------------------------------------------------


class TestExportMdZip:
    def test_creates_valid_zip(self):
        docs = [
            _ReportDoc(name="a.md", content="# A"),
            _ReportDoc(name="b.md", content="# B"),
        ]
        data, filename, content_type = _export_md_zip(docs, "test")
        assert filename == "test.zip"
        assert content_type == "application/zip"

        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert set(zf.namelist()) == {"a.md", "b.md"}
            assert zf.read("a.md").decode() == "# A"

    def test_empty_docs_creates_empty_zip(self):
        data, _, _ = _export_md_zip([], "empty")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert zf.namelist() == []


# ---------------------------------------------------------------------------
# _export_xml — pure function
# ---------------------------------------------------------------------------


class TestExportXml:
    def test_creates_valid_xml(self):
        docs = [_ReportDoc(name="report.md", content="body text")]
        data, filename, content_type = _export_xml(docs, "proj")
        assert filename == "proj.xml"
        assert content_type == "application/xml"

        xml_str = data if isinstance(data, str) else data.decode("utf-8")
        root = ET.fromstring(xml_str)
        assert root.tag == "codetalk-reports"
        assert root.attrib["prefix"] == "proj"
        reports = root.findall("report")
        assert len(reports) == 1
        assert reports[0].attrib["filename"] == "report.md"
        assert reports[0].find("content").text == "body text"

    def test_parses_frontmatter(self):
        content = "---\ntitle: Test\nauthor: Bot\n---\nReal content"
        docs = [_ReportDoc(name="fm.md", content=content)]
        data, _, _ = _export_xml(docs, "fm")

        xml_str = data if isinstance(data, str) else data.decode("utf-8")
        root = ET.fromstring(xml_str)
        report = root.find("report")
        meta = report.find("metadata")
        assert meta is not None
        assert meta.find("title").text == "Test"
        assert meta.find("author").text == "Bot"
        assert report.find("content").text == "Real content"

    def test_no_frontmatter(self):
        docs = [_ReportDoc(name="plain.md", content="Just text")]
        data, _, _ = _export_xml(docs, "plain")
        xml_str = data if isinstance(data, str) else data.decode("utf-8")
        root = ET.fromstring(xml_str)
        report = root.find("report")
        assert report.find("metadata") is None
        assert report.find("content").text == "Just text"


# ---------------------------------------------------------------------------
# _dispatch — routing
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_md_format(self):
        docs = [_ReportDoc(name="x.md", content="c")]
        _, filename, _ = _dispatch(docs, "p", "md")
        assert filename.endswith(".zip")

    def test_xml_format(self):
        docs = [_ReportDoc(name="x.md", content="c")]
        _, filename, _ = _dispatch(docs, "p", "xml")
        assert filename.endswith(".xml")

    def test_unsupported_format_raises(self):
        with pytest.raises(ValueError, match="不支持"):
            _dispatch([], "p", "csv")


# ---------------------------------------------------------------------------
# export_workspace_reports — DB integration
# ---------------------------------------------------------------------------


class TestExportWorkspaceReports:
    async def test_exports_completed_reports_only(self, sqlite_db):
        ws_id = "ws-exp"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'exp', '/r', 1, ?, ?)",
                (ws_id, now, now),
            )
            await db.execute(
                "INSERT INTO workspace_reports "
                "(id, workspace_id, report_type, title, content, status, created_at) "
                "VALUES ('r1', ?, 'module_map', 'map.md', '# Map', 'completed', ?)",
                (ws_id, now),
            )
            await db.execute(
                "INSERT INTO workspace_reports "
                "(id, workspace_id, report_type, title, content, status, created_at) "
                "VALUES ('r2', ?, 'business_flow', 'flow', '# Flow', 'pending', ?)",
                (ws_id, now),
            )
            await db.commit()

            data, filename, ct = await export_workspace_reports(ws_id, "md", db)

        assert filename.endswith(".zip")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            assert len(names) == 1
            assert "map.md" in names[0]

    async def test_raises_when_no_completed(self, sqlite_db):
        ws_id = "ws-exp2"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'exp', '/r', 1, ?, ?)",
                (ws_id, now, now),
            )
            await db.commit()
            with pytest.raises(FileNotFoundError):
                await export_workspace_reports(ws_id, "md", db)

    async def test_title_without_md_extension_gets_suffix(self, sqlite_db):
        ws_id = "ws-exp3"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'exp', '/r', 1, ?, ?)",
                (ws_id, now, now),
            )
            await db.execute(
                "INSERT INTO workspace_reports "
                "(id, workspace_id, report_type, title, content, status, created_at) "
                "VALUES ('r3', ?, 'module_map', 'no-ext-title', '# X', 'completed', ?)",
                (ws_id, now),
            )
            await db.commit()

            data, _, _ = await export_workspace_reports(ws_id, "md", db)

        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert "no-ext-title.md" in zf.namelist()


# ---------------------------------------------------------------------------
# export_workspace_chat — DB integration
# ---------------------------------------------------------------------------


class TestExportWorkspaceChat:
    async def test_exports_with_mode_and_role_labels(self, sqlite_db):
        ws_id = "ws-chat"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'chat-ws', '/r', 1, ?, ?)",
                (ws_id, now, now),
            )
            await db.execute(
                "INSERT INTO workspace_chats "
                "(id, workspace_id, mode, role, content, created_at) "
                "VALUES ('c1', ?, 'targeted', 'user', 'Hello', ?)",
                (ws_id, "2025-06-01T10:00:00"),
            )
            await db.execute(
                "INSERT INTO workspace_chats "
                "(id, workspace_id, mode, role, content, created_at) "
                "VALUES ('c2', ?, 'freeqa', 'assistant', 'Hi back', ?)",
                (ws_id, "2025-06-01T10:01:00"),
            )
            await db.commit()

            data, filename, ct = await export_workspace_chat(ws_id, "chat-ws", db)

        text = data.decode("utf-8")
        assert "chat-ws" in text
        assert "结构化分析" in text
        assert "自由问答" in text
        assert "用户" in text
        assert "AI" in text
        assert filename.startswith("chat-")
        assert ct == "text/markdown; charset=utf-8"

    async def test_raises_when_no_messages(self, sqlite_db):
        ws_id = "ws-chat2"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'chat', '/r', 1, ?, ?)",
                (ws_id, now, now),
            )
            await db.commit()
            with pytest.raises(FileNotFoundError):
                await export_workspace_chat(ws_id, "chat", db)

    async def test_unknown_mode_uses_raw_value(self, sqlite_db):
        ws_id = "ws-chat3"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'chat', '/r', 1, ?, ?)",
                (ws_id, now, now),
            )
            await db.execute(
                "INSERT INTO workspace_chats "
                "(id, workspace_id, mode, role, content, created_at) "
                "VALUES ('cx', ?, 'custom_mode', 'user', 'test', ?)",
                (ws_id, "2025-06-01T10:00:00"),
            )
            await db.commit()

            data, _, _ = await export_workspace_chat(ws_id, "chat", db)

        assert "custom_mode" in data.decode("utf-8")
