"""Contracts for legacy project routes removed from the workspace-first UX."""

import uuid

import httpx

from app.main import app


async def test_removed_project_collection_routes_are_not_mounted():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        listed = await client.get("/api/projects")
        created = await client.post("/api/projects", json={"name": "SPDK"})

    assert listed.status_code == 404
    assert created.status_code == 404


async def test_removed_project_repository_routes_are_not_mounted():
    project_id = uuid.uuid4()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        project = await client.get(f"/api/projects/{project_id}")
        repos = await client.get(f"/api/projects/{project_id}/repositories")

    assert project.status_code == 404
    assert repos.status_code == 404
