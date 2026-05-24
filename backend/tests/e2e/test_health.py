"""E2E tests for /health endpoint."""

import asyncio
import time

from httpx import AsyncClient


async def test_health_returns_ok(e2e_client: AsyncClient):
    resp = await e2e_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


async def test_health_response_time(e2e_client: AsyncClient):
    """Health endpoint should respond quickly (< 2s)."""
    start = time.monotonic()
    resp = await e2e_client.get("/health")
    elapsed = time.monotonic() - start
    assert resp.status_code == 200
    assert elapsed < 2.0, f"Health check took {elapsed:.2f}s, expected < 2s"


async def test_health_concurrent_requests(e2e_client: AsyncClient):
    """Multiple concurrent health requests should all succeed."""
    tasks = [e2e_client.get("/health") for _ in range(10)]
    responses = await asyncio.gather(*tasks)
    for resp in responses:
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
