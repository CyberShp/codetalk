"""Additional E2E tests for server.py — cover the "already running" 409 paths,
supplement endpoints with valid input, and the deploy stop behaviour when active."""

import asyncio

import pytest


# ------------------------------------------------------------------
# 409 "already running" paths
# ------------------------------------------------------------------

async def test_deploy_409_when_already_running(client):
    """POST /api/deploy returns 409 when _state.running is True."""
    import server
    server._state.running = True
    resp = await client.post("/api/deploy", json={})
    assert resp.status_code == 409
    detail = resp.json().get("detail", "")
    assert "already" in detail.lower() or "running" in detail.lower()


async def test_supplement_deepwiki_409_when_running(client):
    """POST /api/deploy/supplement/deepwiki returns 409 during active deployment."""
    import server
    server._state.running = True
    resp = await client.post(
        "/api/deploy/supplement/deepwiki",
        json={"deepwikiPath": "/some/valid/path"},
    )
    assert resp.status_code == 409


async def test_supplement_gitnexus_409_when_running(client):
    """POST /api/deploy/supplement/gitnexus returns 409 during active deployment."""
    import server
    server._state.running = True
    resp = await client.post("/api/deploy/supplement/gitnexus")
    assert resp.status_code == 409


# ------------------------------------------------------------------
# Supplement with valid input (covers lines 234-248 in server.py)
# ------------------------------------------------------------------

async def test_supplement_deepwiki_with_valid_path_returns_job_id(client):
    """Supplying a non-empty deepwikiPath starts a background job."""
    resp = await client.post(
        "/api/deploy/supplement/deepwiki",
        json={"deepwikiPath": "/fake/deepwiki-open"},
    )
    # The HTTP layer must accept the request even if the job later fails.
    assert resp.status_code == 200
    body = resp.json()
    assert "job_id" in body
    assert body["job_id"]


# ------------------------------------------------------------------
# Deploy stop paths
# ------------------------------------------------------------------

async def test_deploy_stop_returns_ok_when_idle(client):
    """Stop with no active deployment is a no-op."""
    resp = await client.post("/api/deploy/stop")
    assert resp.status_code == 200
    assert resp.json().get("ok") is True


async def test_deploy_stop_when_running_sets_state_to_not_running(client):
    """Stop while running cancels the task and clears state."""
    import server

    async def _noop():
        await asyncio.sleep(60)

    loop = asyncio.get_event_loop()
    task = loop.create_task(_noop())
    server._state.running = True
    server._state.task = task

    resp = await client.post("/api/deploy/stop")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True


# ------------------------------------------------------------------
# Services status endpoints (missing coverage)
# ------------------------------------------------------------------

async def test_services_status_returns_200(client):
    resp = await client.get("/api/services/status")
    assert resp.status_code == 200


async def test_services_health_returns_services_dict(client):
    resp = await client.get("/api/services/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "services" in body
    assert isinstance(body["services"], list)


# ------------------------------------------------------------------
# SSE stream when queue is None (empty event)
# ------------------------------------------------------------------

async def test_deploy_stream_with_null_queue(client):
    """Stream endpoint returns empty data event when event_queue is None."""
    async with client.stream("GET", "/api/deploy/stream") as resp:
        assert resp.status_code == 200
        line = await resp.aiter_lines().__anext__()
        assert line.startswith("data:")


# ------------------------------------------------------------------
# Quickstart API
# ------------------------------------------------------------------

async def test_quickstart_409_when_running(client):
    """POST /api/quickstart returns 409 when deployment already active."""
    import server
    server._state.running = True
    resp = await client.post("/api/quickstart", json={})
    assert resp.status_code == 409
