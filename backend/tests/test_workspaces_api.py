"""Sprint 2: workspaces API contract tests.

Uses client_v2 fixture (FastAPI test client + settings.sqlite_db patch).
Background tasks (indexing, embedding) are suppressed via autouse fixture.
"""

import asyncio
import struct
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Suppress background tasks globally for this module
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def background_tasks(monkeypatch):
    """Capture workspace API background tasks without patching global asyncio."""
    from app.api import workspaces

    captured: list[str] = []

    def _capture(coro):
        captured.append(getattr(coro, "__qualname__", repr(coro)))
        if hasattr(coro, "close"):
            coro.close()

    monkeypatch.setattr(workspaces, "_schedule_background_task", _capture)
    return captured


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_ws(db_path: str, ws_id: str, indexed: int = 1) -> str:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
            "VALUES (?, 'test-ws', '/repo', ?, ?, ?)",
            (ws_id, indexed, now, now),
        )
        await db.commit()
    return ws_id


async def _seed_material(
    db_path: str, ws_id: str, mat_id: str, file_path: str, *, is_active: bool = True
) -> str:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO workspace_materials "
            "(id, workspace_id, filename, content_type, file_path, is_active, created_at) "
            "VALUES (?, ?, 'test.md', 'other', ?, ?, ?)",
            (mat_id, ws_id, file_path, is_active, now),
        )
        await db.commit()
    return mat_id


# ---------------------------------------------------------------------------
# Workspace CRUD
# ---------------------------------------------------------------------------


class TestWorkspaceCRUD:
    async def test_list_empty(self, client_v2):
        resp = await client_v2.get("/api/workspaces")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_create_with_valid_dir(self, client_v2, tmp_path, background_tasks):
        repo_dir = tmp_path / "my_repo"
        repo_dir.mkdir()

        resp = await client_v2.post(
            "/api/workspaces",
            json={"name": "new-ws", "repo_path": str(repo_dir)},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "new-ws"
        assert data["indexed"] == 0
        assert any("_index_workspace" in c for c in background_tasks)

    async def test_create_rejects_nonexistent_path(self, client_v2):
        resp = await client_v2.post(
            "/api/workspaces",
            json={"name": "bad", "repo_path": "/nonexistent/repo"},
        )
        assert resp.status_code == 422

    async def test_create_rejects_file_path(self, client_v2, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("x")
        resp = await client_v2.post(
            "/api/workspaces",
            json={"name": "bad", "repo_path": str(f)},
        )
        assert resp.status_code == 422

    async def test_get_workspace_with_children(self, client_v2, sqlite_db):
        ws_id = "ws-detail"
        await _seed_ws(sqlite_db, ws_id)
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspace_materials "
                "(id, workspace_id, filename, content_type, file_path, is_active, created_at) "
                "VALUES ('m1', ?, 'doc.md', 'other', '/tmp/d.md', TRUE, ?)",
                (ws_id, now),
            )
            await db.execute(
                "INSERT INTO workspace_reports "
                "(id, workspace_id, report_type, title, status, created_at) "
                "VALUES ('r1', ?, 'module_map', 'Map', 'completed', ?)",
                (ws_id, now),
            )
            await db.commit()

        resp = await client_v2.get(f"/api/workspaces/{ws_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["materials"]) == 1
        assert len(data["reports"]) == 1
        assert data["materials"][0]["filename"] == "doc.md"

    async def test_get_nonexistent_404(self, client_v2):
        resp = await client_v2.get("/api/workspaces/no-such-id")
        assert resp.status_code == 404

    async def test_list_after_create(self, client_v2, tmp_path):
        repo = tmp_path / "r"
        repo.mkdir()
        await client_v2.post(
            "/api/workspaces", json={"name": "a", "repo_path": str(repo)}
        )
        resp = await client_v2.get("/api/workspaces")
        assert len(resp.json()) == 1


# ---------------------------------------------------------------------------
# Index / Analyze
# ---------------------------------------------------------------------------


class TestIndexAndAnalyze:
    async def test_index_status(self, client_v2, sqlite_db):
        await _seed_ws(sqlite_db, "ws-idx", indexed=1)
        resp = await client_v2.get("/api/workspaces/ws-idx/index-status")
        assert resp.status_code == 200
        assert resp.json()["indexed"] == 1

    async def test_reindex_202(self, client_v2, sqlite_db, background_tasks):
        await _seed_ws(sqlite_db, "ws-ri", indexed=1)
        resp = await client_v2.post("/api/workspaces/ws-ri/reindex")
        assert resp.status_code == 202
        assert any("_index_workspace" in c for c in background_tasks)

    async def test_analyze_requires_indexed(self, client_v2, sqlite_db):
        await _seed_ws(sqlite_db, "ws-noidx", indexed=0)
        resp = await client_v2.post("/api/workspaces/ws-noidx/analyze")
        assert resp.status_code == 409

    async def test_analyze_ok_when_indexed(self, client_v2, sqlite_db, background_tasks):
        await _seed_ws(sqlite_db, "ws-az", indexed=1)
        resp = await client_v2.post("/api/workspaces/ws-az/analyze")
        assert resp.status_code == 202
        task_id = resp.json()["task_id"]
        assert task_id
        assert any("_run_workspace_analysis" in c for c in background_tasks)

        status_resp = await client_v2.get("/api/workspaces/ws-az/analyze-status")
        assert status_resp.status_code == 200
        status_body = status_resp.json()
        assert status_body["analyze_status"] == "running"
        assert status_body["task_id"] == task_id

    async def test_analyze_blocks_when_already_running(self, client_v2, sqlite_db):
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces "
                "(id, name, repo_path, indexed, analyze_status, created_at, updated_at) "
                "VALUES ('ws-dup', 'ws', '/r', 1, 'running', ?, ?)",
                (now, now),
            )
            await db.commit()
        resp = await client_v2.post("/api/workspaces/ws-dup/analyze")
        assert resp.status_code == 409

    async def test_analyze_status_non_running_returns_stored(self, client_v2, sqlite_db):
        """get_analyze_status: workspace not running → returns stored status (line 294 fallthrough)."""
        await _seed_ws(sqlite_db, "ws-status-idle", indexed=1)
        resp = await client_v2.get("/api/workspaces/ws-status-idle/analyze-status")
        assert resp.status_code == 200
        body = resp.json()
        assert "analyze_status" in body
        assert "analyze_progress" in body

    async def test_analyze_status_done_keeps_latest_task_id(self, client_v2, sqlite_db):
        ws_id = "ws-status-done-task"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces "
                "(id, name, repo_path, indexed, analyze_status, analyze_progress, created_at, updated_at) "
                "VALUES (?, 'ws', '/repo', 1, 'done', 100, ?, ?)",
                (ws_id, now, now),
            )
            await db.execute(
                "INSERT INTO tasks (id, name, repo_path, status, tools, "
                "analysis_focus, prompt_content, deepwiki_depth, workspace_id, progress, created_at, updated_at) "
                "VALUES ('latest-done-task', ?, '/repo', 'completed', '[]', "
                "'focus', 'prompt', 'balanced', ?, 100, ?, ?)",
                (f"__ws_{ws_id}", ws_id, now, now),
            )
            await db.commit()

        resp = await client_v2.get(f"/api/workspaces/{ws_id}/analyze-status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["analyze_status"] == "done"
        assert body["analyze_progress"] == 100
        assert body["task_id"] == "latest-done-task"

    async def test_analyze_status_shows_shadow_task_progress(self, client_v2, sqlite_db):
        """get_analyze_status: shadow task record exists → returns its live progress (line 289)."""
        ws_id = "ws-shadow-prog"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces "
                "(id, name, repo_path, indexed, analyze_status, created_at, updated_at) "
                "VALUES (?, 'ws', '/repo', 1, 'running', ?, ?)",
                (ws_id, now, now),
            )
            await db.execute(
                "INSERT INTO tasks (id, name, repo_path, status, tools, "
                "analysis_focus, prompt_content, deepwiki_depth, progress, created_at, updated_at) "
                "VALUES (?, ?, '/repo', 'running', '[]', 'focus', 'prompt', 'balanced', 42, ?, ?)",
                (str(uuid.uuid4()), f"__ws_{ws_id}", now, now),
            )
            await db.commit()

        resp = await client_v2.get(f"/api/workspaces/{ws_id}/analyze-status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["analyze_status"] == "running"
        assert body["analyze_progress"] == 42

    async def test_analyze_status_ignores_completed_legacy_shadow_task(self, client_v2, sqlite_db):
        """A completed legacy __ws_* task must not be exposed as the live task id."""
        ws_id = "ws-shadow-old"
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces "
                "(id, name, repo_path, indexed, analyze_status, analyze_progress, created_at, updated_at) "
                "VALUES (?, 'ws', '/repo', 1, 'running', 17, ?, ?)",
                (ws_id, now, now),
            )
            await db.execute(
                "INSERT INTO tasks (id, name, repo_path, status, tools, "
                "analysis_focus, prompt_content, deepwiki_depth, progress, created_at, updated_at) "
                "VALUES ('old-completed-task', ?, '/repo', 'completed', '[]', "
                "'focus', 'prompt', 'balanced', 100, ?, ?)",
                (f"__ws_{ws_id}", now, now),
            )
            await db.commit()

        resp = await client_v2.get(f"/api/workspaces/{ws_id}/analyze-status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["analyze_status"] == "running"
        assert body["analyze_progress"] == 17
        assert body["task_id"] is None


# ---------------------------------------------------------------------------
# Materials lifecycle
# ---------------------------------------------------------------------------


class TestMaterials:
    async def test_upload(self, client_v2, sqlite_db, background_tasks, tmp_path):
        await _seed_ws(sqlite_db, "ws-up")
        f = tmp_path / "requirements.md"
        f.write_text("# Req\nFeature A")
        resp = await client_v2.post(
            "/api/workspaces/ws-up/materials",
            json={"file_path": str(f)},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["filename"] == "requirements.md"
        assert data["content_type"] == "requirements"
        assert data["is_active"] is True
        assert any("_embed_material_background" in c for c in background_tasks)

    async def test_upload_design_content_type(self, client_v2, sqlite_db, tmp_path):
        await _seed_ws(sqlite_db, "ws-up2")
        f = tmp_path / "architecture_v2.pdf"
        f.write_bytes(b"pdf-data")
        resp = await client_v2.post(
            "/api/workspaces/ws-up2/materials",
            json={"file_path": str(f)},
        )
        assert resp.status_code == 201
        assert resp.json()["content_type"] == "design"

    async def test_upload_other_content_type(self, client_v2, sqlite_db, tmp_path):
        """_guess_content_type: filename with no keyword → 'other'."""
        await _seed_ws(sqlite_db, "ws-up3")
        f = tmp_path / "notes.txt"
        f.write_text("some notes")
        resp = await client_v2.post(
            "/api/workspaces/ws-up3/materials",
            json={"file_path": str(f)},
        )
        assert resp.status_code == 201
        assert resp.json()["content_type"] == "other"

    async def test_toggle_to_inactive(self, client_v2, sqlite_db, tmp_path):
        await _seed_ws(sqlite_db, "ws-tg")
        f = tmp_path / "d.md"
        f.write_text("x")
        await _seed_material(sqlite_db, "ws-tg", "m-tg", str(f))

        with patch(
            "app.services.material_rag.delete_material_chunks",
            new_callable=AsyncMock,
        ):
            resp = await client_v2.patch(
                "/api/workspaces/ws-tg/materials/m-tg",
                json={"is_active": False},
            )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    async def test_delete_removes_file(self, client_v2, sqlite_db, tmp_path):
        await _seed_ws(sqlite_db, "ws-dl")
        f = tmp_path / "del.md"
        f.write_text("x")
        await _seed_material(sqlite_db, "ws-dl", "m-dl", str(f))

        with patch(
            "app.services.material_rag.delete_material_chunks",
            new_callable=AsyncMock,
        ):
            resp = await client_v2.delete("/api/workspaces/ws-dl/materials/m-dl")
        assert resp.status_code == 204
        assert not f.exists()

    async def test_delete_nonexistent_404(self, client_v2, sqlite_db):
        await _seed_ws(sqlite_db, "ws-d4")
        resp = await client_v2.delete("/api/workspaces/ws-d4/materials/no-such")
        assert resp.status_code == 404

    async def test_toggle_inactive_cleanup_exception_swallowed(
        self, client_v2, sqlite_db, tmp_path
    ):
        """toggle_material: delete_material_chunks raises → exception swallowed (lines 434-435)."""
        await _seed_ws(sqlite_db, "ws-tg-exc")
        f = tmp_path / "exc.md"
        f.write_text("x")
        await _seed_material(sqlite_db, "ws-tg-exc", "m-tg-exc", str(f))

        with patch(
            "app.services.material_rag.delete_material_chunks",
            AsyncMock(side_effect=RuntimeError("cleanup failed")),
        ):
            resp = await client_v2.patch(
                "/api/workspaces/ws-tg-exc/materials/m-tg-exc",
                json={"is_active": False},
            )
        assert resp.status_code == 200

    async def test_delete_material_cleanup_exception_swallowed(
        self, client_v2, sqlite_db, tmp_path
    ):
        """delete_material: delete_material_chunks raises → exception swallowed (lines 457-458)."""
        await _seed_ws(sqlite_db, "ws-dl-exc")
        f = tmp_path / "del_exc.md"
        f.write_text("x")
        await _seed_material(sqlite_db, "ws-dl-exc", "m-dl-exc", str(f))

        with patch(
            "app.services.material_rag.delete_material_chunks",
            AsyncMock(side_effect=RuntimeError("cleanup failed")),
        ):
            resp = await client_v2.delete("/api/workspaces/ws-dl-exc/materials/m-dl-exc")
        assert resp.status_code == 204

    async def test_toggle_nonexistent_404(self, client_v2, sqlite_db):
        await _seed_ws(sqlite_db, "ws-t4")
        resp = await client_v2.patch(
            "/api/workspaces/ws-t4/materials/no-such",
            json={"is_active": True},
        )
        assert resp.status_code == 404

    async def test_toggle_to_active_schedules_embedding(
        self, client_v2, sqlite_db, tmp_path, background_tasks
    ):
        await _seed_ws(sqlite_db, "ws-tga")
        f = tmp_path / "doc.md"
        f.write_text("content")
        await _seed_material(sqlite_db, "ws-tga", "m-tga", str(f), is_active=False)

        resp = await client_v2.patch(
            "/api/workspaces/ws-tga/materials/m-tga",
            json={"is_active": True},
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is True
        assert any("_embed_material_background" in c for c in background_tasks)

    async def test_trigger_embedding_schedules_task(
        self, client_v2, sqlite_db, background_tasks
    ):
        await _seed_ws(sqlite_db, "ws-emb")
        resp = await client_v2.post("/api/workspaces/ws-emb/materials/embed")
        assert resp.status_code == 200
        assert resp.json()["status"] == "embedding_started"
        assert any("_run_embed" in c for c in background_tasks)


# ---------------------------------------------------------------------------
# Embedding status
# ---------------------------------------------------------------------------


class TestEmbeddingStatus:
    async def test_no_model_configured(self, client_v2, sqlite_db):
        await _seed_ws(sqlite_db, "ws-e0")
        resp = await client_v2.get("/api/workspaces/ws-e0/materials/embedding-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["embedded_materials"] == 0
        assert data["rag_ready"] is False

    async def test_with_chunks(self, client_v2, sqlite_db):
        ws_id = "ws-e1"
        await _seed_ws(sqlite_db, ws_id)
        blob = struct.pack("3f", 0.1, 0.2, 0.3)
        now = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) "
                "VALUES ('active_embedding_model_id', 'model-x')"
            )
            await db.execute(
                "INSERT INTO workspace_materials "
                "(id, workspace_id, filename, content_type, file_path, is_active, created_at) "
                "VALUES ('mat-e', ?, 'doc.md', 'other', '/tmp/d.md', TRUE, ?)",
                (ws_id, now),
            )
            await db.execute(
                "INSERT INTO material_chunks "
                "(id, material_id, workspace_id, embedding_model_id, "
                "chunk_index, content, embedding, created_at) "
                "VALUES ('c1', 'mat-e', ?, 'model-x', 0, 'text', ?, ?)",
                (ws_id, blob, now),
            )
            await db.commit()

        resp = await client_v2.get(f"/api/workspaces/{ws_id}/materials/embedding-status")
        data = resp.json()
        assert data["active_materials"] == 1
        assert data["embedded_materials"] == 1
        assert data["total_chunks"] == 1
        assert data["rag_ready"] is True

    async def test_mismatched_model_not_counted(self, client_v2, sqlite_db):
        ws_id = "ws-e2"
        await _seed_ws(sqlite_db, ws_id)
        blob = struct.pack("3f", 0.1, 0.2, 0.3)
        now = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) "
                "VALUES ('active_embedding_model_id', 'model-NEW')"
            )
            await db.execute(
                "INSERT INTO workspace_materials "
                "(id, workspace_id, filename, content_type, file_path, is_active, created_at) "
                "VALUES ('mat-e2', ?, 'doc.md', 'other', '/tmp/d.md', TRUE, ?)",
                (ws_id, now),
            )
            await db.execute(
                "INSERT INTO material_chunks "
                "(id, material_id, workspace_id, embedding_model_id, "
                "chunk_index, content, embedding, created_at) "
                "VALUES ('c2', 'mat-e2', ?, 'model-OLD', 0, 'text', ?, ?)",
                (ws_id, blob, now),
            )
            await db.commit()

        resp = await client_v2.get(f"/api/workspaces/{ws_id}/materials/embedding-status")
        data = resp.json()
        assert data["active_materials"] == 1
        assert data["embedded_materials"] == 0
        assert data["rag_ready"] is False


# ---------------------------------------------------------------------------
# Chat endpoints
# ---------------------------------------------------------------------------


class TestChatEndpoints:
    async def test_stream_requires_indexed(self, client_v2, sqlite_db):
        await _seed_ws(sqlite_db, "ws-ch0", indexed=0)
        resp = await client_v2.post(
            "/api/workspaces/ws-ch0/chat/stream",
            json={"message": "hello", "mode": "freeqa"},
        )
        assert resp.status_code == 409

    async def test_stream_indexed_no_llm_returns_503(self, client_v2, sqlite_db):
        """workspace_chat_stream: indexed=1 but LLM not configured → 503 (lines 574-575)."""
        await _seed_ws(sqlite_db, "ws-ch0-503", indexed=1)
        resp = await client_v2.post(
            "/api/workspaces/ws-ch0-503/chat/stream",
            json={"message": "hello", "mode": "freeqa"},
        )
        assert resp.status_code == 503

    async def test_history_chronological(self, client_v2, sqlite_db):
        ws_id = "ws-ch1"
        await _seed_ws(sqlite_db, ws_id)
        base = datetime(2025, 6, 1, tzinfo=timezone.utc)
        async with aiosqlite.connect(sqlite_db) as db:
            for i in range(5):
                ts = (base + timedelta(seconds=i)).isoformat()
                role = "user" if i % 2 == 0 else "assistant"
                await db.execute(
                    "INSERT INTO workspace_chats "
                    "(id, workspace_id, mode, role, content, created_at) "
                    "VALUES (?, ?, 'freeqa', ?, ?, ?)",
                    (str(uuid.uuid4()), ws_id, role, f"msg-{i}", ts),
                )
            await db.commit()

        resp = await client_v2.get(f"/api/workspaces/{ws_id}/chat/history")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 5
        assert data[0]["content"] == "msg-0"
        assert data[4]["content"] == "msg-4"

    async def test_history_respects_limit(self, client_v2, sqlite_db):
        ws_id = "ws-ch2"
        await _seed_ws(sqlite_db, ws_id)
        base = datetime(2025, 6, 1, tzinfo=timezone.utc)
        async with aiosqlite.connect(sqlite_db) as db:
            for i in range(10):
                ts = (base + timedelta(seconds=i)).isoformat()
                await db.execute(
                    "INSERT INTO workspace_chats "
                    "(id, workspace_id, mode, role, content, created_at) "
                    "VALUES (?, ?, 'freeqa', 'user', ?, ?)",
                    (str(uuid.uuid4()), ws_id, f"msg-{i}", ts),
                )
            await db.commit()

        resp = await client_v2.get(
            f"/api/workspaces/{ws_id}/chat/history", params={"limit": 3}
        )
        data = resp.json()
        assert len(data) == 3
        assert data[0]["content"] == "msg-7"
        assert data[2]["content"] == "msg-9"

    async def test_history_nonexistent_ws_404(self, client_v2):
        resp = await client_v2.get("/api/workspaces/no-such/chat/history")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# _classify_index_error — pure function (lines 97-108)
# ---------------------------------------------------------------------------


class TestClassifyIndexError:
    def _call(self, exc, base_url="http://localhost:8080"):
        from app.api.workspaces import _classify_index_error
        return _classify_index_error(exc, base_url)

    def test_connect_error(self):
        import httpx
        exc = httpx.ConnectError("connection refused")
        result = self._call(exc, "http://localhost:8080")
        assert "localhost:8080" in result
        assert "未启动" in result

    def test_timeout_exception(self):
        import httpx
        exc = httpx.TimeoutException("read timed out")
        result = self._call(exc)
        assert "超时" in result

    def test_message_contains_timed_out(self):
        exc = RuntimeError("operation timed out after 10 minutes")
        result = self._call(exc)
        assert "超时" in result

    def test_message_contains_failed(self):
        exc = RuntimeError("indexing failed due to error")
        result = self._call(exc)
        assert "失败" in result

    def test_generic_exception(self):
        exc = RuntimeError("something unexpected")
        result = self._call(exc)
        assert result == "something unexpected"


# ---------------------------------------------------------------------------
# Background task functions — direct tests (lines 113-179, 401-405)
# ---------------------------------------------------------------------------


class TestBackgroundTasks:
    async def test_index_workspace_success(self, sqlite_db):
        """_index_workspace: healthy adapter + prepare() succeeds → indexed=1."""
        ws_id = "ws-bg-ok"
        await _seed_ws(sqlite_db, ws_id, indexed=0)

        mock_health = MagicMock()
        mock_health.is_healthy = True

        mock_adapter = MagicMock()
        mock_adapter.health_check = AsyncMock(return_value=mock_health)
        mock_adapter.prepare = AsyncMock()

        with patch("app.adapters.gitnexus.GitNexusAdapter", return_value=mock_adapter):
            from app.api.workspaces import _index_workspace
            await _index_workspace(ws_id, "/repo")

        async with aiosqlite.connect(sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT indexed FROM workspaces WHERE id = ?", (ws_id,)) as cur:
                row = await cur.fetchone()
        assert row["indexed"] == 1

    async def test_index_workspace_unhealthy(self, sqlite_db):
        """_index_workspace: health.is_healthy=False → indexed=-1 with error set."""
        ws_id = "ws-bg-unhealthy"
        await _seed_ws(sqlite_db, ws_id, indexed=0)

        mock_health = MagicMock()
        mock_health.is_healthy = False
        mock_health.last_check = "timeout"
        mock_health.container_status = None

        mock_adapter = MagicMock()
        mock_adapter.health_check = AsyncMock(return_value=mock_health)

        with patch("app.adapters.gitnexus.GitNexusAdapter", return_value=mock_adapter):
            from app.api.workspaces import _index_workspace
            await _index_workspace(ws_id, "/repo")

        async with aiosqlite.connect(sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT indexed, last_index_error FROM workspaces WHERE id = ?", (ws_id,)
            ) as cur:
                row = await cur.fetchone()
        assert row["indexed"] == -1
        assert row["last_index_error"] is not None

    async def test_index_workspace_exception(self, sqlite_db):
        """_index_workspace: exception from adapter → indexed=-1 via _classify_index_error."""
        ws_id = "ws-bg-exc"
        await _seed_ws(sqlite_db, ws_id, indexed=0)

        mock_adapter = MagicMock()
        mock_adapter.health_check = AsyncMock(side_effect=RuntimeError("network error"))

        with patch("app.adapters.gitnexus.GitNexusAdapter", return_value=mock_adapter):
            from app.api.workspaces import _index_workspace
            await _index_workspace(ws_id, "/repo")

        async with aiosqlite.connect(sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT indexed, last_index_error FROM workspaces WHERE id = ?", (ws_id,)
            ) as cur:
                row = await cur.fetchone()
        assert row["indexed"] == -1
        assert row["last_index_error"] is not None

    async def test_run_workspace_analysis_failure(self, sqlite_db):
        """_run_workspace_analysis: exception → analyze_status='failed'."""
        ws_id = "ws-bg-anal-fail"
        await _seed_ws(sqlite_db, ws_id, indexed=1)

        with patch(
            "app.services.workspace_pipeline.WorkspacePipeline.run",
            AsyncMock(side_effect=RuntimeError("pipeline boom")),
        ):
            from app.api.workspaces import _run_workspace_analysis
            await _run_workspace_analysis(ws_id, "/repo")

        async with aiosqlite.connect(sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT analyze_status FROM workspaces WHERE id = ?", (ws_id,)
            ) as cur:
                row = await cur.fetchone()
        assert row["analyze_status"] == "failed"

    async def test_embed_material_background_success(self):
        """_embed_material_background: calls embed_material with correct args."""
        from app.api.workspaces import _embed_material_background

        mock_embed = AsyncMock()
        with patch("app.services.material_rag.embed_material", mock_embed):
            await _embed_material_background("mat-123", "ws-123")

        mock_embed.assert_called_once_with("mat-123", "ws-123")

    async def test_embed_material_background_exception_swallowed(self):
        """_embed_material_background: exception is logged but not re-raised."""
        from app.api.workspaces import _embed_material_background

        with patch(
            "app.services.material_rag.embed_material",
            AsyncMock(side_effect=RuntimeError("embed failed")),
        ):
            await _embed_material_background("mat-err", "ws-err")


# ---------------------------------------------------------------------------
# workspace_chat_stream — streaming path (lines 577-617)
# ---------------------------------------------------------------------------


class TestChatStream:
    async def test_stream_prepends_visible_evidence_status(self, client_v2, sqlite_db):
        """workspace_chat_stream: evidence status in the system prompt is user-visible and persisted."""
        ws_id = "ws-stream-evidence-status"
        await _seed_ws(sqlite_db, ws_id, indexed=1)

        async def _mock_stream(messages, **kwargs):
            yield "answer body"

        mock_llm = MagicMock()
        mock_llm.stream_complete = _mock_stream
        persisted_reply = AsyncMock()
        messages = [
            {
                "role": "system",
                "content": (
                    "<!-- CODETALK_EVIDENCE_STATUS_BEGIN -->\n"
                    "> Evidence status: degraded\n"
                    "> code_snippets: 0\n"
                    "<!-- CODETALK_EVIDENCE_STATUS_END -->"
                ),
            },
            {"role": "user", "content": "hi"},
        ]

        with patch(
            "app.llm.factory.create_llm_client_from_active",
            AsyncMock(return_value=mock_llm),
        ):
            with patch(
                "app.services.workspace_chat.build_chat_messages",
                AsyncMock(return_value=messages),
            ):
                with patch("app.services.workspace_chat.persist_user_message", AsyncMock()):
                    with patch(
                        "app.services.workspace_chat.persist_assistant_reply",
                        persisted_reply,
                    ):
                        resp = await client_v2.post(
                            f"/api/workspaces/{ws_id}/chat/stream",
                            json={"message": "What is this?", "mode": "freeqa"},
                        )

        assert resp.status_code == 200
        body = resp.content.decode("utf-8")
        assert "Evidence status: degraded" in body
        assert "answer body" in body
        persisted = persisted_reply.await_args.args[2]
        assert "Evidence status: degraded" in persisted
        assert "answer body" in persisted

    async def test_stream_mocked_llm(self, client_v2, sqlite_db):
        """workspace_chat_stream: mocked LLM yields tokens, SSE events stream back."""
        ws_id = "ws-stream-ok"
        await _seed_ws(sqlite_db, ws_id, indexed=1)

        async def _mock_stream(messages, **kwargs):
            yield "Hello"
            yield " World"

        mock_llm = MagicMock()
        mock_llm.stream_complete = _mock_stream

        with patch(
            "app.llm.factory.create_llm_client_from_active",
            AsyncMock(return_value=mock_llm),
        ):
            with patch(
                "app.services.workspace_chat.build_chat_messages",
                AsyncMock(return_value=[{"role": "user", "content": "hi"}]),
            ):
                with patch("app.services.workspace_chat.persist_user_message", AsyncMock()):
                    with patch(
                        "app.services.workspace_chat.persist_assistant_reply", AsyncMock()
                    ):
                        resp = await client_v2.post(
                            f"/api/workspaces/{ws_id}/chat/stream",
                            json={"message": "What is this?", "mode": "freeqa"},
                        )

        assert resp.status_code == 200
        body = resp.content.decode("utf-8")
        assert "Hello" in body
        assert '"done": false' in body or '"done":false' in body

    async def test_stream_llm_error_yields_error_event(self, client_v2, sqlite_db):
        """workspace_chat_stream: stream error yields done+error SSE event."""
        ws_id = "ws-stream-err"
        await _seed_ws(sqlite_db, ws_id, indexed=1)

        async def _failing_stream(messages, **kwargs):
            raise RuntimeError("LLM exploded")
            yield  # make it an async generator

        mock_llm = MagicMock()
        mock_llm.stream_complete = _failing_stream

        with patch(
            "app.llm.factory.create_llm_client_from_active",
            AsyncMock(return_value=mock_llm),
        ):
            with patch(
                "app.services.workspace_chat.build_chat_messages",
                AsyncMock(return_value=[]),
            ):
                with patch("app.services.workspace_chat.persist_user_message", AsyncMock()):
                    with patch(
                        "app.services.workspace_chat.persist_assistant_reply", AsyncMock()
                    ):
                        resp = await client_v2.post(
                            f"/api/workspaces/{ws_id}/chat/stream",
                            json={"message": "hello?", "mode": "freeqa"},
                        )

        assert resp.status_code == 200
        body = resp.content.decode("utf-8")
        assert "error" in body

    async def test_stream_cancel_persists_partial_reply(self, client_v2, sqlite_db):
        """workspace_chat_stream: cancelled stream still saves partial assistant context."""
        ws_id = "ws-stream-cancel"
        await _seed_ws(sqlite_db, ws_id, indexed=1)

        async def _cancelled_stream(messages, **kwargs):
            yield "partial answer"
            raise asyncio.CancelledError()

        mock_llm = MagicMock()
        mock_llm.stream_complete = _cancelled_stream
        persisted_reply = AsyncMock()

        with patch(
            "app.llm.factory.create_llm_client_from_active",
            AsyncMock(return_value=mock_llm),
        ):
            with patch(
                "app.services.workspace_chat.build_chat_messages",
                AsyncMock(return_value=[{"role": "user", "content": "hi"}]),
            ):
                with patch("app.services.workspace_chat.persist_user_message", AsyncMock()):
                    with patch(
                        "app.services.workspace_chat.persist_assistant_reply",
                        persisted_reply,
                    ):
                        resp = await client_v2.post(
                            f"/api/workspaces/{ws_id}/chat/stream",
                            json={"message": "hello?", "mode": "freeqa"},
                        )

        assert resp.status_code == 200
        body = resp.content.decode("utf-8")
        assert "partial answer" in body
        persisted = persisted_reply.await_args.args[2]
        assert "partial answer" in persisted
        assert "interrupted" in persisted

    async def test_stream_persist_user_message_exception_swallowed(
        self, client_v2, sqlite_db
    ):
        """workspace_chat_stream: persist_user_message raises → swallowed (lines 589-590)."""
        ws_id = "ws-stream-persist-err"
        await _seed_ws(sqlite_db, ws_id, indexed=1)

        async def _ok_stream(messages, **kwargs):
            yield "ok"

        mock_llm = MagicMock()
        mock_llm.stream_complete = _ok_stream

        with patch(
            "app.llm.factory.create_llm_client_from_active",
            AsyncMock(return_value=mock_llm),
        ):
            with patch(
                "app.services.workspace_chat.build_chat_messages",
                AsyncMock(return_value=[]),
            ):
                with patch(
                    "app.services.workspace_chat.persist_user_message",
                    AsyncMock(side_effect=RuntimeError("persist failed")),
                ):
                    with patch(
                        "app.services.workspace_chat.persist_assistant_reply", AsyncMock()
                    ):
                        resp = await client_v2.post(
                            f"/api/workspaces/{ws_id}/chat/stream",
                            json={"message": "hello?", "mode": "freeqa"},
                        )

        assert resp.status_code == 200

    async def test_stream_persist_reply_exception_swallowed(self, client_v2, sqlite_db):
        """workspace_chat_stream: persist_assistant_reply raises → swallowed (lines 611-612)."""
        ws_id = "ws-stream-reply-err"
        await _seed_ws(sqlite_db, ws_id, indexed=1)

        async def _ok_stream(messages, **kwargs):
            yield "Some text"

        mock_llm = MagicMock()
        mock_llm.stream_complete = _ok_stream

        with patch(
            "app.llm.factory.create_llm_client_from_active",
            AsyncMock(return_value=mock_llm),
        ):
            with patch(
                "app.services.workspace_chat.build_chat_messages",
                AsyncMock(return_value=[]),
            ):
                with patch("app.services.workspace_chat.persist_user_message", AsyncMock()):
                    with patch(
                        "app.services.workspace_chat.persist_assistant_reply",
                        AsyncMock(side_effect=RuntimeError("reply persist failed")),
                    ):
                        resp = await client_v2.post(
                            f"/api/workspaces/{ws_id}/chat/stream",
                            json={"message": "hello?", "mode": "freeqa"},
                        )

        assert resp.status_code == 200
        body = resp.content.decode("utf-8")
        assert "Some text" in body


class TestAnalyzeCoverageGapFlags:
    """AnalyzeRequest forwards the coverage gap test-design selection."""

    async def test_forwards_explicit_coverage_flags(self, client_v2, sqlite_db):
        from app.api import workspaces as wsmod

        ws_id = await _seed_ws(sqlite_db, "ws-cov-flags", indexed=1)
        with patch.object(wsmod, "_run_workspace_analysis", new=AsyncMock()) as m:
            resp = await client_v2.post(
                f"/api/workspaces/{ws_id}/analyze",
                json={
                    "include_coverage_gaps": False,
                    "coverage_analysis_ids": ["cov-1", "cov-2"],
                },
            )
        assert resp.status_code == 202
        m.assert_called_once()
        assert m.call_args.kwargs["include_coverage_gaps"] is False
        assert m.call_args.kwargs["coverage_analysis_ids"] == ["cov-1", "cov-2"]

    async def test_defaults_enable_coverage_gaps(self, client_v2, sqlite_db):
        from app.api import workspaces as wsmod

        ws_id = await _seed_ws(sqlite_db, "ws-cov-default", indexed=1)
        with patch.object(wsmod, "_run_workspace_analysis", new=AsyncMock()) as m:
            resp = await client_v2.post(f"/api/workspaces/{ws_id}/analyze")
        assert resp.status_code == 202
        assert m.call_args.kwargs["include_coverage_gaps"] is True
        assert m.call_args.kwargs["coverage_analysis_ids"] is None
