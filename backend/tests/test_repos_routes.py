"""Contracts for legacy repository CRUD/sync routes removed from current UX."""

import uuid

import httpx

from app.main import app


async def test_removed_repo_detail_and_sync_routes_are_not_mounted():
    repo_id = uuid.uuid4()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        detail = await client.get(f"/api/repos/{repo_id}")
        sync = await client.post(f"/api/repos/{repo_id}/sync")
        cancel = await client.post(f"/api/repos/{repo_id}/sync/cancel")

    assert detail.status_code == 404
    assert sync.status_code == 404
    assert cancel.status_code == 404
