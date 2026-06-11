"""E2E tests for /api/workspaces endpoints."""

import os
import uuid

import pytest
from httpx import AsyncClient

HAS_DEEPSEEK = bool(os.environ.get("DEEPSEEK_API_KEY", ""))


# -- List --

async def test_list_workspaces_empty(e2e_client: AsyncClient):
    resp = await e2e_client.get("/api/workspaces")
    assert resp.status_code == 200
    assert resp.json() == []


# -- CRUD --

async def test_create_workspace(e2e_client: AsyncClient, repo_path: str):
    resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Test Workspace", "repo_path": repo_path},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Test Workspace"
    assert body["repo_path"] == repo_path
    assert body["id"]


async def test_get_workspace_by_id(e2e_client: AsyncClient, repo_path: str):
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "WS Detail", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/workspaces/{ws_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == ws_id
    assert body["name"] == "WS Detail"
    assert "materials" in body
    assert "reports" in body


async def test_get_nonexistent_workspace(e2e_client: AsyncClient):
    resp = await e2e_client.get(f"/api/workspaces/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_create_workspace_invalid_path(e2e_client: AsyncClient):
    resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Bad WS", "repo_path": "/nonexistent/path"},
    )
    assert resp.status_code == 422


async def test_workspace_missing_name(e2e_client: AsyncClient, repo_path: str):
    """Omitting the name field should fail validation."""
    resp = await e2e_client.post(
        "/api/workspaces",
        json={"repo_path": repo_path},
    )
    assert resp.status_code == 422


# -- Materials --

async def test_workspace_materials_empty(e2e_client: AsyncClient, repo_path: str):
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Mat WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/workspaces/{ws_id}")
    assert resp.status_code == 200
    assert resp.json()["materials"] == []


async def test_upload_material(e2e_client: AsyncClient, repo_path: str, tmp_path):
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Upload WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    test_file = tmp_path / "requirements.txt"
    test_file.write_text("flask==3.0\nfastapi==0.115", encoding="utf-8")

    with open(test_file, "rb") as f:
        resp = await e2e_client.post(
            f"/api/workspaces/{ws_id}/materials",
            files={"file": ("requirements.txt", f, "text/plain")},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["filename"] == "requirements.txt"
    assert body["workspace_id"] == ws_id


async def test_materials_appear_after_upload(e2e_client: AsyncClient, repo_path: str, tmp_path):
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Mat List WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    test_file = tmp_path / "design.md"
    test_file.write_text("# Design Doc\nArchitecture overview.", encoding="utf-8")

    with open(test_file, "rb") as f:
        await e2e_client.post(
            f"/api/workspaces/{ws_id}/materials",
            files={"file": ("design.md", f, "text/plain")},
        )

    resp = await e2e_client.get(f"/api/workspaces/{ws_id}")
    assert resp.status_code == 200
    materials = resp.json()["materials"]
    assert len(materials) >= 1
    assert any(m["filename"] == "design.md" for m in materials)


async def test_delete_material(e2e_client: AsyncClient, repo_path: str, tmp_path):
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Del Mat WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    test_file = tmp_path / "to_delete.txt"
    test_file.write_text("temp content", encoding="utf-8")

    with open(test_file, "rb") as f:
        mat_resp = await e2e_client.post(
            f"/api/workspaces/{ws_id}/materials",
            files={"file": ("to_delete.txt", f, "text/plain")},
        )
    mat_id = mat_resp.json()["id"]

    resp = await e2e_client.delete(f"/api/workspaces/{ws_id}/materials/{mat_id}")
    assert resp.status_code == 204


# -- Analyze --

async def test_analyze_workspace_not_indexed_uses_local_fallback(
    e2e_client: AsyncClient, repo_path: str
):
    """Analyze should start with a warning when GitNexus indexing is not ready."""
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Analyze WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    resp = await e2e_client.post(f"/api/workspaces/{ws_id}/analyze")
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "running"
    assert any("GitNexus" in warning for warning in body["warnings"])


# -- Reports --

async def test_workspace_reports_empty(e2e_client: AsyncClient, repo_path: str):
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Report WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/workspaces/{ws_id}")
    assert resp.status_code == 200
    assert resp.json()["reports"] == []


# -- Edge cases --

async def test_workspace_name_very_long(e2e_client: AsyncClient, repo_path: str):
    long_name = "A" * 200
    resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": long_name, "repo_path": repo_path},
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == long_name


async def test_workspace_name_too_long(e2e_client: AsyncClient, repo_path: str):
    too_long = "A" * 201
    resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": too_long, "repo_path": repo_path},
    )
    assert resp.status_code == 422


@pytest.mark.skipif(not HAS_DEEPSEEK, reason="DEEPSEEK_API_KEY not set")
async def test_workspace_chat_not_indexed(e2e_client: AsyncClient, repo_path: str):
    """Chat should fail if workspace is not indexed."""
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Chat WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    resp = await e2e_client.post(
        f"/api/workspaces/{ws_id}/chat/stream",
        json={"message": "What is this repo about?", "mode": "freeqa"},
    )
    assert resp.status_code == 409


async def test_workspace_index_status(e2e_client: AsyncClient, repo_path: str):
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Index Status WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/workspaces/{ws_id}/index-status")
    assert resp.status_code == 200
    body = resp.json()
    assert "indexed" in body


async def test_workspace_analyze_status(e2e_client: AsyncClient, repo_path: str):
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Analyze Status WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/workspaces/{ws_id}/analyze-status")
    assert resp.status_code == 200
    body = resp.json()
    assert "analyze_status" in body
    assert "analyze_progress" in body


async def test_workspace_embedding_status(e2e_client: AsyncClient, repo_path: str):
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Embed Status WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/workspaces/{ws_id}/materials/embedding-status")
    assert resp.status_code == 200
    body = resp.json()
    assert "active_materials" in body
    assert "embedded_materials" in body
    assert "rag_ready" in body


async def test_workspace_reindex_accepted(e2e_client: AsyncClient, repo_path: str):
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Reindex WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    resp = await e2e_client.post(f"/api/workspaces/{ws_id}/reindex")
    assert resp.status_code == 202
    assert resp.json().get("status") == "indexing"


async def test_workspace_trigger_embed(e2e_client: AsyncClient, repo_path: str):
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Trigger Embed WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    resp = await e2e_client.post(f"/api/workspaces/{ws_id}/materials/embed")
    assert resp.status_code == 200
    assert resp.json().get("status") == "embedding_started"


async def test_workspace_chat_history_empty(e2e_client: AsyncClient, repo_path: str):
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Chat History WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/workspaces/{ws_id}/chat/history")
    assert resp.status_code == 200
    assert resp.json() == []


# -- Export endpoints --

async def test_workspace_export_no_reports_returns_404(e2e_client: AsyncClient, repo_path: str):
    """GET /export with no completed reports should return 404."""
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Export 404 WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/workspaces/{ws_id}/export")
    assert resp.status_code == 404


async def test_workspace_export_invalid_format_returns_422(e2e_client: AsyncClient, repo_path: str):
    """GET /export with unsupported format fails validation."""
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Export Fmt WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    resp = await e2e_client.get(
        f"/api/workspaces/{ws_id}/export",
        params={"format": "pdf"},
    )
    assert resp.status_code == 422


async def test_workspace_export_nonexistent_workspace_returns_404(e2e_client: AsyncClient):
    resp = await e2e_client.get(f"/api/workspaces/{uuid.uuid4()}/export")
    assert resp.status_code == 404


async def test_workspace_chat_export_no_messages_returns_404(e2e_client: AsyncClient, repo_path: str):
    """GET /chat/export with no chat messages returns 404."""
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Chat Export 404 WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/workspaces/{ws_id}/chat/export")
    assert resp.status_code == 404


async def test_workspace_chat_export_nonexistent_workspace_returns_404(e2e_client: AsyncClient):
    resp = await e2e_client.get(f"/api/workspaces/{uuid.uuid4()}/chat/export")
    assert resp.status_code == 404


async def test_workspace_export_with_completed_report(e2e_client: AsyncClient, repo_path: str):
    """Export returns 200 with file content when a completed report exists."""
    import aiosqlite
    from app.config import settings

    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Export With Report WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    report_id = str(uuid.uuid4())
    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute(
            "INSERT INTO workspace_reports (id, workspace_id, report_type, title, content, status)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (report_id, ws_id, "analysis", "Summary Report", "# Summary\n\nContent here.", "completed"),
        )
        await db.commit()

    resp = await e2e_client.get(f"/api/workspaces/{ws_id}/export", params={"format": "md"})
    assert resp.status_code == 200
    assert len(resp.content) > 0


async def test_workspace_chat_export_with_messages(e2e_client: AsyncClient, repo_path: str):
    """Chat export returns Markdown file when messages exist."""
    import aiosqlite
    from app.config import settings

    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Chat Export WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute(
            "INSERT INTO workspace_chats (id, workspace_id, mode, role, content, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), ws_id, "freeqa", "user", "What does this repo do?", "2024-01-01T00:00:00"),
        )
        await db.execute(
            "INSERT INTO workspace_chats (id, workspace_id, mode, role, content, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), ws_id, "freeqa", "assistant", "This repo does X.", "2024-01-01T00:00:01"),
        )
        await db.commit()

    resp = await e2e_client.get(f"/api/workspaces/{ws_id}/chat/export")
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")
    assert "工作空间对话记录" in content
    assert "Chat Export WS" in content


async def test_workspace_export_md_zip_format(e2e_client: AsyncClient, repo_path: str):
    """Export as zip returns correct content-type."""
    import aiosqlite
    from app.config import settings

    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Zip Export WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    report_id = str(uuid.uuid4())
    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute(
            "INSERT INTO workspace_reports (id, workspace_id, report_type, title, content, status)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (report_id, ws_id, "analysis", "Report", "# Report\nContent.", "completed"),
        )
        await db.commit()

    resp = await e2e_client.get(f"/api/workspaces/{ws_id}/export", params={"format": "md"})
    assert resp.status_code == 200
    assert "zip" in resp.headers.get("content-type", "")


async def test_workspace_export_xml_format(e2e_client: AsyncClient, repo_path: str):
    """Export as XML returns XML content."""
    import aiosqlite
    from app.config import settings

    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "XML Export WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    report_id = str(uuid.uuid4())
    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute(
            "INSERT INTO workspace_reports (id, workspace_id, report_type, title, content, status)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (report_id, ws_id, "analysis", "Report", "# Report\nContent.", "completed"),
        )
        await db.commit()

    resp = await e2e_client.get(f"/api/workspaces/{ws_id}/export", params={"format": "xml"})
    assert resp.status_code == 200
    assert "xml" in resp.headers.get("content-type", "")


async def test_workspace_export_docx_format(e2e_client: AsyncClient, repo_path: str):
    """Export as docx returns docx content-type (python-docx must be installed)."""
    import aiosqlite
    from app.config import settings

    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Docx Export WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    import uuid as _uuid
    report_id = str(_uuid.uuid4())
    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute(
            "INSERT INTO workspace_reports (id, workspace_id, report_type, title, content, status)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (report_id, ws_id, "analysis", "Report", "# Heading\n\nBody paragraph.", "completed"),
        )
        await db.commit()

    resp = await e2e_client.get(f"/api/workspaces/{ws_id}/export", params={"format": "docx"})
    assert resp.status_code == 200
    assert "wordprocessingml" in resp.headers.get("content-type", "")


async def test_workspace_export_report_with_frontmatter(e2e_client: AsyncClient, repo_path: str):
    """Export handles reports with YAML frontmatter stripping (xml and docx paths)."""
    import aiosqlite
    from app.config import settings

    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Frontmatter Export WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    import uuid as _uuid
    content_with_fm = "---\ntitle: My Report\ndate: 2024-01-01\n---\n# Body\n\nContent here."
    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute(
            "INSERT INTO workspace_reports (id, workspace_id, report_type, title, content, status)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (str(_uuid.uuid4()), ws_id, "analysis", "FM Report", content_with_fm, "completed"),
        )
        await db.commit()

    resp = await e2e_client.get(f"/api/workspaces/{ws_id}/export", params={"format": "xml"})
    assert resp.status_code == 200
    xml_body = resp.content.decode("utf-8")
    assert "<metadata>" in xml_body
    assert "My Report" in xml_body


async def test_toggle_material_active(e2e_client: AsyncClient, repo_path: str, tmp_path):
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Toggle Mat WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    test_file = tmp_path / "toggle.txt"
    test_file.write_text("content to toggle", encoding="utf-8")

    with open(test_file, "rb") as f:
        mat_resp = await e2e_client.post(
            f"/api/workspaces/{ws_id}/materials",
            files={"file": ("toggle.txt", f, "text/plain")},
        )
    assert mat_resp.status_code == 201
    mat_id = mat_resp.json()["id"]

    resp = await e2e_client.patch(
        f"/api/workspaces/{ws_id}/materials/{mat_id}",
        json={"is_active": False},
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


async def test_create_workspace_file_path_returns_422(e2e_client: AsyncClient, tmp_path):
    """Creating a workspace with a file path (not directory) returns 422."""
    file_path = tmp_path / "not_a_dir.txt"
    file_path.write_text("content", encoding="utf-8")

    resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "File Path WS", "repo_path": str(file_path)},
    )
    assert resp.status_code == 422


async def test_analyze_indexed_workspace(e2e_client: AsyncClient, repo_path: str):
    """POST /analyze on an indexed workspace starts analysis."""
    import aiosqlite
    from app.config import settings

    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Indexed Analyze WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute("UPDATE workspaces SET indexed = 1 WHERE id = ?", (ws_id,))
        await db.commit()

    resp = await e2e_client.post(f"/api/workspaces/{ws_id}/analyze")
    assert resp.status_code == 202
    assert resp.json()["status"] == "running"


async def test_analyze_already_running_returns_409(e2e_client: AsyncClient, repo_path: str):
    """POST /analyze when analysis is already running returns 409."""
    import aiosqlite
    from app.config import settings

    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Already Analyzing WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute(
            "UPDATE workspaces SET indexed = 1, analyze_status = 'running' WHERE id = ?",
            (ws_id,),
        )
        await db.commit()

    resp = await e2e_client.post(f"/api/workspaces/{ws_id}/analyze")
    assert resp.status_code == 409


async def test_get_report_by_id(e2e_client: AsyncClient, repo_path: str):
    """GET /{ws_id}/reports/{report_id} returns a specific report."""
    import aiosqlite
    from app.config import settings

    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Get Report WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    report_id = str(uuid.uuid4())
    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute(
            "INSERT INTO workspace_reports (id, workspace_id, report_type, title, content, status)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (report_id, ws_id, "analysis", "Test Report", "# Report Content", "completed"),
        )
        await db.commit()

    resp = await e2e_client.get(f"/api/workspaces/{ws_id}/reports/{report_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == report_id
    assert body["title"] == "Test Report"


async def test_get_report_not_found_returns_404(e2e_client: AsyncClient, repo_path: str):
    """GET /{ws_id}/reports/{report_id} with missing report returns 404."""
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "No Report WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/workspaces/{ws_id}/reports/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_toggle_material_to_active(e2e_client: AsyncClient, repo_path: str, tmp_path):
    """PATCH material with is_active=True triggers background embedding."""
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Toggle Active WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    test_file = tmp_path / "activate.txt"
    test_file.write_text("content to activate", encoding="utf-8")

    with open(test_file, "rb") as f:
        mat_resp = await e2e_client.post(
            f"/api/workspaces/{ws_id}/materials",
            files={"file": ("activate.txt", f, "text/plain")},
        )
    mat_id = mat_resp.json()["id"]

    # First deactivate
    await e2e_client.patch(
        f"/api/workspaces/{ws_id}/materials/{mat_id}",
        json={"is_active": False},
    )

    # Then activate — triggers create_task(_embed_material_background)
    resp = await e2e_client.patch(
        f"/api/workspaces/{ws_id}/materials/{mat_id}",
        json={"is_active": True},
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True


async def test_toggle_nonexistent_material_returns_404(e2e_client: AsyncClient, repo_path: str):
    """PATCH non-existent material returns 404."""
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Toggle 404 WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    resp = await e2e_client.patch(
        f"/api/workspaces/{ws_id}/materials/{uuid.uuid4()}",
        json={"is_active": True},
    )
    assert resp.status_code == 404


async def test_delete_nonexistent_material_returns_404(e2e_client: AsyncClient, repo_path: str):
    """DELETE non-existent material returns 404."""
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Delete 404 WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    resp = await e2e_client.delete(
        f"/api/workspaces/{ws_id}/materials/{uuid.uuid4()}"
    )
    assert resp.status_code == 404


async def test_workspace_embedding_status_with_active_model(e2e_client: AsyncClient, repo_path: str):
    """Embedding status with an active model configured returns chunk counts."""
    import aiosqlite
    from app.config import settings

    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Embed Active Model WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("active_embedding_model_id", "test-embed-model"),
        )
        await db.commit()

    resp = await e2e_client.get(f"/api/workspaces/{ws_id}/materials/embedding-status")
    assert resp.status_code == 200
    body = resp.json()
    assert "active_materials" in body
    assert "embedded_materials" in body
    assert body["total_chunks"] == 0


async def test_workspace_chat_indexed_no_llm_returns_503(e2e_client: AsyncClient, repo_path: str):
    """POST /chat/stream on an indexed workspace with no LLM returns 503."""
    import aiosqlite
    from app.config import settings

    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Chat LLM WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute("UPDATE workspaces SET indexed = 1 WHERE id = ?", (ws_id,))
        await db.commit()

    resp = await e2e_client.post(
        f"/api/workspaces/{ws_id}/chat/stream",
        json={"message": "What does this repo do?", "mode": "freeqa"},
    )
    assert resp.status_code == 503


async def test_workspace_chat_not_indexed_returns_409(e2e_client: AsyncClient, repo_path: str):
    """POST /chat/stream on a workspace with indexed=0 returns 409 immediately."""
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Chat Not Indexed WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    resp = await e2e_client.post(
        f"/api/workspaces/{ws_id}/chat/stream",
        json={"message": "Hello?", "mode": "freeqa"},
    )
    assert resp.status_code == 409


async def test_analyze_status_after_trigger_shows_running(e2e_client: AsyncClient, repo_path: str):
    """GET /analyze-status after POST /analyze returns running status (exercises lines 249-255)."""
    import aiosqlite
    from app.config import settings

    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Analyze Status Running WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute("UPDATE workspaces SET indexed = 1 WHERE id = ?", (ws_id,))
        await db.commit()

    await e2e_client.post(f"/api/workspaces/{ws_id}/analyze")

    status_resp = await e2e_client.get(f"/api/workspaces/{ws_id}/analyze-status")
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert "analyze_status" in body


async def test_workspace_chat_history_empty(e2e_client: AsyncClient, repo_path: str):
    """GET /chat/history on a workspace with no messages returns empty list."""
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "History Empty WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/workspaces/{ws_id}/chat/history")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_workspace_chat_history_with_messages(e2e_client: AsyncClient, repo_path: str):
    """GET /chat/history returns seeded messages in chronological order."""
    import aiosqlite
    from app.config import settings

    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "History With Msgs WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute(
            "INSERT INTO workspace_chats (id, workspace_id, mode, role, content, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), ws_id, "freeqa", "user", "First question", "2024-01-01T00:00:00"),
        )
        await db.execute(
            "INSERT INTO workspace_chats (id, workspace_id, mode, role, content, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), ws_id, "freeqa", "assistant", "First answer", "2024-01-01T00:00:01"),
        )
        await db.commit()

    resp = await e2e_client.get(f"/api/workspaces/{ws_id}/chat/history")
    assert resp.status_code == 200
    messages = resp.json()
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"


async def test_workspace_chat_history_limit_param(e2e_client: AsyncClient, repo_path: str):
    """GET /chat/history with limit=1 returns only the most recent message."""
    import aiosqlite
    from app.config import settings

    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "History Limit WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    async with aiosqlite.connect(settings.sqlite_db) as db:
        for i in range(3):
            await db.execute(
                "INSERT INTO workspace_chats (id, workspace_id, mode, role, content, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), ws_id, "freeqa", "user", f"Message {i}", f"2024-01-01T00:0{i}:00"),
            )
        await db.commit()

    resp = await e2e_client.get(f"/api/workspaces/{ws_id}/chat/history", params={"limit": 1})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_trigger_embedding_returns_started(e2e_client: AsyncClient, repo_path: str):
    """POST /materials/embed starts background embedding and returns immediately."""
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Embed Trigger WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    resp = await e2e_client.post(f"/api/workspaces/{ws_id}/materials/embed")
    assert resp.status_code == 200
    assert resp.json()["status"] == "embedding_started"


async def test_trigger_embedding_nonexistent_workspace_returns_404(e2e_client: AsyncClient):
    """POST /materials/embed on non-existent workspace returns 404."""
    resp = await e2e_client.post(f"/api/workspaces/{uuid.uuid4()}/materials/embed")
    assert resp.status_code == 404


async def test_workspace_export_docx_rich_markdown(e2e_client: AsyncClient, repo_path: str):
    """Export as docx with rich markdown exercises all heading levels, bullets, and table rows."""
    import aiosqlite
    from app.config import settings

    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Rich Docx WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    rich_content = (
        "# H1 Heading\n\n"
        "## H2 Section\n\n"
        "### H3 Subsection\n\n"
        "- First bullet item\n"
        "- Second bullet item\n\n"
        "| col1 | col2 | col3 |\n\n"
        "Regular paragraph text.\n"
    )
    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute(
            "INSERT INTO workspace_reports (id, workspace_id, report_type, title, content, status)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), ws_id, "analysis", "Rich Report", rich_content, "completed"),
        )
        await db.commit()

    resp = await e2e_client.get(f"/api/workspaces/{ws_id}/export", params={"format": "docx"})
    assert resp.status_code == 200
    assert "wordprocessingml" in resp.headers.get("content-type", "")


async def test_upload_design_material_content_type(e2e_client: AsyncClient, repo_path: str, tmp_path):
    """Uploading a 'design*.md' file sets content_type='design' via _guess_content_type."""
    create_resp = await e2e_client.post(
        "/api/workspaces",
        json={"name": "Design Upload WS", "repo_path": repo_path},
    )
    ws_id = create_resp.json()["id"]

    design_file = tmp_path / "design.md"
    design_file.write_text("# Architecture design doc", encoding="utf-8")

    with open(design_file, "rb") as f:
        resp = await e2e_client.post(
            f"/api/workspaces/{ws_id}/materials",
            files={"file": ("design.md", f, "text/markdown")},
        )
    assert resp.status_code == 201
    assert resp.json()["content_type"] == "design"
