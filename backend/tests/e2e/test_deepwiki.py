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
