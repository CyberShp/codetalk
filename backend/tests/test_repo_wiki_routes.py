"""Contracts for repo wiki routes removed from the current product surface."""

import uuid

import httpx

from app.main import app


async def test_removed_repo_wiki_routes_are_not_mounted():
    repo_id = uuid.uuid4()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        wiki = await client.get(f"/api/repos/{repo_id}/wiki")
        status = await client.get(f"/api/repos/{repo_id}/wiki/status")

    assert wiki.status_code == 404
    assert status.status_code == 404


async def test_removed_repo_wiki_mutation_routes_are_not_mounted():
    repo_id = uuid.uuid4()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        generate = await client.post(f"/api/repos/{repo_id}/wiki/generate", json={})
        export = await client.post(f"/api/repos/{repo_id}/wiki/export", json={"format": "markdown"})

    assert generate.status_code == 404
    assert export.status_code == 404
