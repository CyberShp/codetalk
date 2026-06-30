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
    _export_docx,
    _export_md_zip,
    _export_xml,
    export_reports,
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
# export_reports — task output directory integration
# ---------------------------------------------------------------------------


class TestExportTaskReports:
    async def test_redacts_secret_values_from_task_report_export(self, tmp_path, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "data_dir", str(tmp_path))
        task_id = "job_redacted_export"
        report_secret = "-".join(["sk", "taskReportLeakValue1234567890"])
        token_secret = "taskReportTokenLeakValue1234567890"
        bearer_secret = "taskReportBearerLeakValue1234567890"
        output_dir = settings.outputs_path / task_id
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "task-report.md").write_text(
            "\n".join(
                [
                    "# Task Report",
                    "task report export complete",
                    f"model key: {report_secret}",
                    "runtime " + "tok" + f"en={token_secret}",
                    "Authorization:" + f" Bearer {bearer_secret}",
                ]
            ),
            encoding="utf-8",
        )

        data, _, _ = await export_reports(task_id, "md")

        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            text = zf.read("task-report.md").decode("utf-8")
        assert "task report export complete" in text
        assert "<redacted>" in text
        assert report_secret not in text
        assert token_secret not in text
        assert bearer_secret not in text
        assert "Authorization: Bearer <redacted>" in text
        assert "token=<redacted>" in text

    async def test_redacts_json_and_yaml_secret_values_from_task_report_export(self, tmp_path, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "data_dir", str(tmp_path))
        task_id = "job_structured_redacted_export"
        json_secret = "taskReportJsonTokenLeakValue1234567890"
        yaml_secret = "taskReportYamlSecretLeakValue1234567890"
        csv_secret = "taskReportCsvSecretLeakValue1234567890"
        output_dir = settings.outputs_path / task_id
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "task-structured-report.md").write_text(
            "\n".join(
                [
                    "# Structured Diagnostics",
                    "task structured export complete",
                    f'{{"access_token": "{json_secret}"}}',
                    f"secret: {yaml_secret}",
                    "name,secret,status",
                    f"agent,{csv_secret},failed",
                ]
            ),
            encoding="utf-8",
        )

        data, _, _ = await export_reports(task_id, "md")

        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            text = zf.read("task-structured-report.md").decode("utf-8")
        assert "task structured export complete" in text
        assert "<redacted>" in text
        assert json_secret not in text
        assert yaml_secret not in text
        assert csv_secret not in text
        assert '"access_token": "<redacted>"' in text
        assert "secret: <redacted>" in text
        assert "agent,<redacted>,failed" in text


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


class TestExportDocx:
    def test_basic_headings_and_paragraphs(self):
        docs = [_ReportDoc(name="r.md", content="# H1\n## H2\n### H3\n- bullet\n| table |\nParagraph.")]
        data, filename, ct = _export_docx(docs, "basic")
        assert filename == "basic.docx"
        assert "wordprocessingml" in ct
        assert len(data) > 0

    def test_frontmatter_stripped_in_docx(self):
        """Lines 172-174: content starting with '---' has frontmatter removed before rendering."""
        content = "---\ntitle: My Report\ndate: 2024-01-01\n---\n# Body Heading\n\nBody content."
        docs = [_ReportDoc(name="fm.md", content=content)]
        data, filename, ct = _export_docx(docs, "fm-test")
        assert filename == "fm-test.docx"
        assert len(data) > 0

    def test_frontmatter_no_closing_marker_not_stripped(self):
        """Line 173: if '---' end marker is missing, content is not stripped."""
        content = "---\ntitle: Unclosed\nno end marker here"
        docs = [_ReportDoc(name="no-end.md", content=content)]
        data, filename, ct = _export_docx(docs, "no-end")
        assert filename == "no-end.docx"
        assert len(data) > 0


class TestDispatch:
    def test_md_format(self):
        docs = [_ReportDoc(name="x.md", content="c")]
        _, filename, _ = _dispatch(docs, "p", "md")
        assert filename.endswith(".zip")

    def test_xml_format(self):
        docs = [_ReportDoc(name="x.md", content="c")]
        _, filename, _ = _dispatch(docs, "p", "xml")
        assert filename.endswith(".xml")

    def test_docx_format(self):
        """Line 142: _dispatch routes docx format to _export_docx."""
        docs = [_ReportDoc(name="x.md", content="# Heading\n\nBody.")]
        _, filename, _ = _dispatch(docs, "p", "docx")
        assert filename.endswith(".docx")

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

    async def test_defaults_to_latest_task_reports_only(self, sqlite_db):
        """Workspace export should not bundle reports from every historical task."""
        ws_id = "ws-exp-latest-task"
        old_time = "2026-01-01T00:00:00+00:00"
        new_time = "2026-01-02T00:00:00+00:00"
        async with aiosqlite.connect(sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'exp', '/r', 1, ?, ?)",
                (ws_id, old_time, new_time),
            )
            await db.execute(
                "INSERT INTO workspace_reports "
                "(id, workspace_id, task_id, report_type, title, content, status, created_at) "
                "VALUES ('old-r', ?, 'old-task', 'module_map', 'old.md', '# Old', 'completed', ?)",
                (ws_id, old_time),
            )
            await db.execute(
                "INSERT INTO workspace_reports "
                "(id, workspace_id, task_id, report_type, title, content, status, created_at) "
                "VALUES ('new-r', ?, 'new-task', 'module_map', 'new.md', '# New', 'completed', ?)",
                (ws_id, new_time),
            )
            await db.commit()

            data, filename, _ = await export_workspace_reports(ws_id, "md", db)

        assert "new-task" in filename
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert zf.namelist() == ["new.md"]
            assert zf.read("new.md").decode("utf-8") == "# New"

    async def test_filters_to_requested_task_id(self, sqlite_db):
        ws_id = "ws-exp-specific-task"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'exp', '/r', 1, ?, ?)",
                (ws_id, now, now),
            )
            for report_id, task_id, title in [
                ("a", "task-a", "a.md"),
                ("b", "task-b", "b.md"),
            ]:
                await db.execute(
                    "INSERT INTO workspace_reports "
                    "(id, workspace_id, task_id, report_type, title, content, status, created_at) "
                    "VALUES (?, ?, ?, 'module_map', ?, ?, 'completed', ?)",
                    (report_id, ws_id, task_id, title, f"# {task_id}", now),
                )
            await db.commit()

            data, filename, _ = await export_workspace_reports(
                ws_id, "md", db, task_id="task-a"
            )

        assert "task-a" in filename
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert zf.namelist() == ["a.md"]
            assert zf.read("a.md").decode("utf-8") == "# task-a"

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

    async def test_redacts_secret_values_from_workspace_report_export(self, sqlite_db):
        ws_id = "ws-exp-redact"
        now = datetime.now(timezone.utc).isoformat()
        report_secret = "-".join(["sk", "workspaceReportLeakValue1234567890"])
        token_secret = "workspaceReportTokenLeakValue1234567890"
        bearer_secret = "workspaceReportBearerLeakValue1234567890"
        async with aiosqlite.connect(sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'exp-redact', '/r', 1, ?, ?)",
                (ws_id, now, now),
            )
            await db.execute(
                "INSERT INTO workspace_reports "
                "(id, workspace_id, report_type, title, content, status, created_at) "
                "VALUES ('rr', ?, 'analysis', 'redact.md', ?, 'completed', ?)",
                (
                    ws_id,
                    "\n".join(
                        [
                            "# Report",
                            "analysis complete",
                            f"model key: {report_secret}",
                            "runtime " + "tok" + f"en={token_secret}",
                            "Authorization:" + f" Bearer {bearer_secret}",
                        ]
                    ),
                    now,
                ),
            )
            await db.commit()

            data, _, _ = await export_workspace_reports(ws_id, "md", db)

        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            text = zf.read("redact.md").decode("utf-8")
        assert "analysis complete" in text
        assert "<redacted>" in text
        assert report_secret not in text
        assert token_secret not in text
        assert bearer_secret not in text
        assert "Authorization: Bearer <redacted>" in text
        assert "token=<redacted>" in text


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

    async def test_redacts_secret_values_from_chat_export(self, sqlite_db):
        ws_id = "ws-chat-redact"
        now = datetime.now(timezone.utc).isoformat()
        user_secret = "-".join(["sk", "workspaceChatUserLeakValue1234567890"])
        assistant_secret = "-".join(["sk", "workspaceChatAssistantLeakValue1234567890"])
        token_secret = "workspaceChatTokenLeakValue1234567890"
        bearer_secret = "workspaceChatBearerLeakValue1234567890"
        async with aiosqlite.connect(sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'chat-redact', '/r', 1, ?, ?)",
                (ws_id, now, now),
            )
            await db.execute(
                "INSERT INTO workspace_chats "
                "(id, workspace_id, mode, role, content, created_at) "
                "VALUES ('cu', ?, 'freeqa', 'user', ?, ?)",
                (ws_id, f"please do not export {user_secret}", "2025-06-01T10:00:00"),
            )
            await db.execute(
                "INSERT INTO workspace_chats "
                "(id, workspace_id, mode, role, content, created_at) "
                "VALUES ('ca', ?, 'freeqa', 'assistant', ?, ?)",
                (
                    ws_id,
                    "\n".join(
                        [
                            "analysis complete",
                            f"agent key: {assistant_secret}",
                            "runtime " + "tok" + f"en={token_secret}",
                            "Authorization:" + f" Bearer {bearer_secret}",
                        ]
                    ),
                    "2025-06-01T10:01:00",
                ),
            )
            await db.commit()

            data, _, _ = await export_workspace_chat(ws_id, "chat-redact", db)

        text = data.decode("utf-8")
        assert "analysis complete" in text
        assert "<redacted>" in text
        assert user_secret not in text
        assert assistant_secret not in text
        assert token_secret not in text
        assert bearer_secret not in text
        assert "Authorization: Bearer <redacted>" in text
        assert "token=<redacted>" in text
