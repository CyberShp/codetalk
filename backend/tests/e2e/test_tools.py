"""E2E tests for /api/tools endpoints."""

from httpx import AsyncClient


async def test_tools_status(e2e_client: AsyncClient):
    """GET /api/tools/status should return a list (may be empty if no PM in state)."""
    resp = await e2e_client.get("/api/tools/status")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)


async def test_start_unknown_tool(e2e_client: AsyncClient):
    """Starting a nonexistent tool should fail."""
    resp = await e2e_client.post("/api/tools/nonexistent-tool/start")
    assert resp.status_code == 400


async def test_stop_unknown_tool(e2e_client: AsyncClient):
    """Stopping a nonexistent tool should fail."""
    resp = await e2e_client.post("/api/tools/nonexistent-tool/stop")
    assert resp.status_code == 400


async def test_restart_unknown_tool(e2e_client: AsyncClient):
    """Restarting a nonexistent tool should fail."""
    resp = await e2e_client.post("/api/tools/nonexistent-tool/restart")
    assert resp.status_code == 400
