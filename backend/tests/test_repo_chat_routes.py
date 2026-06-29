"""Contracts for repo chat routes removed in the current AI-thread UX."""

import uuid

import httpx

from app.main import app


async def test_removed_repo_chat_session_routes_are_not_mounted():
    repo_id = uuid.uuid4()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(f"/api/repos/{repo_id}/chat/sessions")

    assert response.status_code == 404


async def test_removed_repo_chat_stream_route_is_not_mounted():
    repo_id = uuid.uuid4()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/repos/{repo_id}/chat/stream",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )

    assert response.status_code == 404
