"""E2E tests for /api/deepwiki endpoints."""

import uuid

from httpx import AsyncClient


async def test_list_deepwiki_repos_empty(e2e_client: AsyncClient):
    resp = await e2e_client.get("/api/deepwiki/repos")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_create_deepwiki_repo(e2e_client: AsyncClient, repo_path: str):
    resp = await e2e_client.post(
        "/api/deepwiki/repos",
        json={"name": "Test Repo", "repo_path": repo_path},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Test Repo"
    assert body["repo_path"] == repo_path
    assert body["status"] == "pending"


async def test_get_deepwiki_repo_by_id(e2e_client: AsyncClient, repo_path: str):
    create_resp = await e2e_client.post(
        "/api/deepwiki/repos",
        json={"name": "DW Get", "repo_path": repo_path},
    )
    repo_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/deepwiki/repos/{repo_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == repo_id
    assert body["name"] == "DW Get"


async def test_get_nonexistent_deepwiki_repo(e2e_client: AsyncClient):
    resp = await e2e_client.get(f"/api/deepwiki/repos/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_deepwiki_repo_pages_empty(e2e_client: AsyncClient, repo_path: str):
    create_resp = await e2e_client.post(
        "/api/deepwiki/repos",
        json={"name": "Pages DW", "repo_path": repo_path},
    )
    repo_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/deepwiki/repos/{repo_id}/pages")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_deepwiki_repo_status(e2e_client: AsyncClient, repo_path: str):
    create_resp = await e2e_client.post(
        "/api/deepwiki/repos",
        json={"name": "Status DW", "repo_path": repo_path},
    )
    repo_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/deepwiki/repos/{repo_id}/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "running" in body
    assert "progress" in body


async def test_deepwiki_invalid_page_index(e2e_client: AsyncClient, repo_path: str):
    create_resp = await e2e_client.post(
        "/api/deepwiki/repos",
        json={"name": "PageIdx DW", "repo_path": repo_path},
    )
    repo_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/deepwiki/repos/{repo_id}/pages/999")
    assert resp.status_code == 404


async def test_create_duplicate_repo_path(e2e_client: AsyncClient, repo_path: str):
    """Registering the same repo_path twice should fail with 409."""
    await e2e_client.post(
        "/api/deepwiki/repos",
        json={"name": "First", "repo_path": repo_path},
    )
    resp = await e2e_client.post(
        "/api/deepwiki/repos",
        json={"name": "Second", "repo_path": repo_path},
    )
    assert resp.status_code == 409


async def test_create_deepwiki_repo_nonexistent_path(e2e_client: AsyncClient):
    """Registering a non-existent path returns 422."""
    resp = await e2e_client.post(
        "/api/deepwiki/repos",
        json={"name": "Bad", "repo_path": "/nonexistent/path/does/not/exist"},
    )
    assert resp.status_code == 422


async def test_create_deepwiki_repo_file_path_returns_422(e2e_client: AsyncClient, tmp_path):
    """Registering a file (not a directory) path returns 422."""
    file_path = tmp_path / "file.txt"
    file_path.write_text("content", encoding="utf-8")

    resp = await e2e_client.post(
        "/api/deepwiki/repos",
        json={"name": "FileNotDir", "repo_path": str(file_path)},
    )
    assert resp.status_code == 422


async def test_deepwiki_pages_with_pages_format(e2e_client: AsyncClient, repo_path: str):
    """list_pages returns pages when wiki_data uses the {pages:[...]} format."""
    import aiosqlite
    import json
    from app.config import settings

    create_resp = await e2e_client.post(
        "/api/deepwiki/repos",
        json={"name": "Pages Format", "repo_path": repo_path},
    )
    repo_id = create_resp.json()["id"]

    wiki_data = {"pages": [
        {"id": "p1", "title": "Overview", "content": "Overview content",
         "filePaths": [], "importance": "high", "relatedPages": []},
        {"id": "p2", "title": "API", "content": "API content",
         "filePaths": [], "importance": "medium", "relatedPages": []},
    ]}
    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute(
            "UPDATE deepwiki_repos SET wiki_data = ?, page_count = 2 WHERE id = ?",
            (json.dumps(wiki_data), repo_id),
        )
        await db.commit()

    resp = await e2e_client.get(f"/api/deepwiki/repos/{repo_id}/pages")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    titles = {p["title"] for p in body}
    assert "Overview" in titles
    assert "API" in titles


async def test_deepwiki_pages_with_generated_pages_format(e2e_client: AsyncClient, repo_path: str):
    """list_pages handles legacy {generated_pages:{...}} wiki_data format."""
    import aiosqlite
    import json
    from app.config import settings

    create_resp = await e2e_client.post(
        "/api/deepwiki/repos",
        json={"name": "Legacy Format", "repo_path": repo_path},
    )
    repo_id = create_resp.json()["id"]

    wiki_data = {"generated_pages": {
        "arch": {"id": "arch", "title": "Architecture", "content": "Arch content",
                 "filePaths": ["src/main.py"], "importance": "high", "relatedPages": []},
        "api": {"id": "api", "title": "API Reference", "content": "API content",
                "filePaths": [], "importance": "medium", "relatedPages": ["arch"]},
    }}
    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute(
            "UPDATE deepwiki_repos SET wiki_data = ?, page_count = 2 WHERE id = ?",
            (json.dumps(wiki_data), repo_id),
        )
        await db.commit()

    resp = await e2e_client.get(f"/api/deepwiki/repos/{repo_id}/pages")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    titles = {p["title"] for p in body}
    assert "Architecture" in titles
    assert "API Reference" in titles


async def test_deepwiki_get_page_by_index(e2e_client: AsyncClient, repo_path: str):
    """GET /pages/{index} returns the page at that position."""
    import aiosqlite
    import json
    from app.config import settings

    create_resp = await e2e_client.post(
        "/api/deepwiki/repos",
        json={"name": "Page Index", "repo_path": repo_path},
    )
    repo_id = create_resp.json()["id"]

    wiki_data = {"pages": [
        {"id": "p0", "title": "First", "content": "First content",
         "filePaths": [], "importance": "high", "relatedPages": []},
    ]}
    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute(
            "UPDATE deepwiki_repos SET wiki_data = ?, page_count = 1 WHERE id = ?",
            (json.dumps(wiki_data), repo_id),
        )
        await db.commit()

    resp = await e2e_client.get(f"/api/deepwiki/repos/{repo_id}/pages/0")
    assert resp.status_code == 200
    assert resp.json()["title"] == "First"
    assert resp.json()["content"] == "First content"


async def test_deepwiki_generate_starts_background(e2e_client: AsyncClient, repo_path: str):
    """POST /generate starts wiki generation and returns started status."""
    create_resp = await e2e_client.post(
        "/api/deepwiki/repos",
        json={"name": "Generate Test", "repo_path": repo_path},
    )
    repo_id = create_resp.json()["id"]

    resp = await e2e_client.post(f"/api/deepwiki/repos/{repo_id}/generate")
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


async def test_deepwiki_generate_already_running_returns_409(e2e_client: AsyncClient, repo_path: str):
    """POST /generate on a running repo returns 409."""
    import aiosqlite
    from app.config import settings

    create_resp = await e2e_client.post(
        "/api/deepwiki/repos",
        json={"name": "Already Running", "repo_path": repo_path},
    )
    repo_id = create_resp.json()["id"]

    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute(
            "UPDATE deepwiki_repos SET status = 'running' WHERE id = ?", (repo_id,)
        )
        await db.commit()

    resp = await e2e_client.post(f"/api/deepwiki/repos/{repo_id}/generate")
    assert resp.status_code == 409


async def test_deepwiki_generate_status_in_memory(e2e_client: AsyncClient, repo_path: str):
    """GET /status after generate returns the in-memory status dict."""
    create_resp = await e2e_client.post(
        "/api/deepwiki/repos",
        json={"name": "Status In Mem", "repo_path": repo_path},
    )
    repo_id = create_resp.json()["id"]

    await e2e_client.post(f"/api/deepwiki/repos/{repo_id}/generate")

    status_resp = await e2e_client.get(f"/api/deepwiki/repos/{repo_id}/status")
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert "running" in body
    assert "progress" in body


async def test_deepwiki_pages_invalid_wiki_data_returns_empty(e2e_client: AsyncClient, repo_path: str):
    """GET /pages returns [] when wiki_data is malformed JSON (covers _extract_pages except branch)."""
    import aiosqlite
    from app.config import settings

    create_resp = await e2e_client.post(
        "/api/deepwiki/repos",
        json={"name": "Bad JSON Repo", "repo_path": repo_path},
    )
    repo_id = create_resp.json()["id"]

    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute(
            "UPDATE deepwiki_repos SET wiki_data = ? WHERE id = ?",
            ("not valid json {{{", repo_id),
        )
        await db.commit()

    resp = await e2e_client.get(f"/api/deepwiki/repos/{repo_id}/pages")
    assert resp.status_code == 200
    assert resp.json() == []
