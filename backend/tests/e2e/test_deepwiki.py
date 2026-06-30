"""E2E deletion-contract tests for removed DeepWiki endpoints."""

import uuid

from httpx import AsyncClient


async def test_deepwiki_repo_collection_removed(e2e_client: AsyncClient, repo_path: str):
    get_resp = await e2e_client.get("/api/deepwiki/repos")
    assert get_resp.status_code == 404

    post_resp = await e2e_client.post(
        "/api/deepwiki/repos",
        json={"name": "Removed DeepWiki", "repo_path": repo_path},
    )
    assert post_resp.status_code == 404


async def test_deepwiki_repo_detail_removed(e2e_client: AsyncClient):
    repo_id = uuid.uuid4()

    for path in (
        f"/api/deepwiki/repos/{repo_id}",
        f"/api/deepwiki/repos/{repo_id}/pages",
        f"/api/deepwiki/repos/{repo_id}/pages/0",
        f"/api/deepwiki/repos/{repo_id}/status",
    ):
        resp = await e2e_client.get(path)
        assert resp.status_code == 404


async def test_deepwiki_generate_removed(e2e_client: AsyncClient):
    resp = await e2e_client.post(f"/api/deepwiki/repos/{uuid.uuid4()}/generate")
    assert resp.status_code == 404
