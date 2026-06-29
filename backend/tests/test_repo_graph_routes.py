"""Contracts for legacy repo graph routes removed from the current product."""

import uuid

import httpx

from app.main import app


async def test_removed_repo_graph_route_is_not_mounted():
    repo_id = uuid.uuid4()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(f"/api/repos/{repo_id}/graph")

    assert response.status_code == 404
