"""Sprint 2: workspaces API contract tests.

Uses client_v2 fixture (FastAPI test client + settings.sqlite_db patch).
Background tasks (indexing, embedding) are suppressed via autouse fixture.
"""

import struct
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Suppress background tasks globally for this module
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def background_tasks(monkeypatch):
    """Capture asyncio.create_task calls. Returns list of scheduled coroutine qualnames."""
    import asyncio

    captured: list[str] = []

    def _capture(coro, *, name=None):
        captured.append(getattr(coro, "__qualname__", repr(coro)))
        if hasattr(coro, "close"):
            coro.close()

    monkeypatch.setattr(asyncio, "create_task", _capture)
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
        assert any("_run_workspace_analysis" in c for c in background_tasks)

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


# ---------------------------------------------------------------------------
# Materials lifecycle
# ---------------------------------------------------------------------------


class TestMaterials:
    async def test_upload(self, client_v2, sqlite_db, background_tasks):
        await _seed_ws(sqlite_db, "ws-up")
        resp = await client_v2.post(
            "/api/workspaces/ws-up/materials",
            files={"file": ("requirements.md", b"# Req\nFeature A", "text/markdown")},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["filename"] == "requirements.md"
        assert data["content_type"] == "requirements"
        assert data["is_active"] is True
        assert any("_embed_material_background" in c for c in background_tasks)

    async def test_upload_design_content_type(self, client_v2, sqlite_db):
        await _seed_ws(sqlite_db, "ws-up2")
        resp = await client_v2.post(
            "/api/workspaces/ws-up2/materials",
            files={"file": ("architecture_v2.pdf", b"pdf-data", "application/pdf")},
        )
        assert resp.status_code == 201
        assert resp.json()["content_type"] == "design"

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

    async def test_toggle_nonexistent_404(self, client_v2, sqlite_db):
        await _seed_ws(sqlite_db, "ws-t4")
        resp = await client_v2.patch(
            "/api/workspaces/ws-t4/materials/no-such",
            json={"is_active": True},
        )
        assert resp.status_code == 404


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
