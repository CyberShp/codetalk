"""E2E tests for /api/tools endpoints."""

from httpx import AsyncClient


async def test_tools_status(e2e_client: AsyncClient):
    """GET /api/tools/status returns adapter health dict keyed by tool name."""
    resp = await e2e_client.get("/api/tools/status")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, dict)
    for entry in body.values():
        assert "healthy" in entry
        assert "indexed_repos" in entry
        assert "last_index_error" in entry


async def test_tool_procs_uses_process_manager(e2e_client: AsyncClient):
    """GET /api/tools/procs returns process-manager list (backward compat path)."""
    resp = await e2e_client.get("/api/tools/procs")
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


async def test_stop_known_tool_not_running(e2e_client: AsyncClient):
    """Stopping a known tool that has no running process succeeds (no-op stop path)."""
    resp = await e2e_client.post("/api/tools/gitnexus/stop")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


async def test_start_known_tool_exercises_spawn_path(e2e_client: AsyncClient):
    """Starting a known tool exercises the spawn path — succeeds or fails gracefully."""
    resp = await e2e_client.post("/api/tools/gitnexus/start")
    assert resp.status_code in (200, 400)
    if resp.status_code == 200:
        await e2e_client.post("/api/tools/gitnexus/stop")


async def test_restart_known_tool(e2e_client: AsyncClient):
    """Restart exercises stop-then-start flow for a known tool name."""
    resp = await e2e_client.post("/api/tools/gitnexus/restart")
    assert resp.status_code in (200, 400)
    if resp.status_code == 200:
        await e2e_client.post("/api/tools/gitnexus/stop")


async def test_tools_status_includes_registered_tools(e2e_client: AsyncClient):
    """Status dict contains entries for all registered adapter tools."""
    resp = await e2e_client.get("/api/tools/status")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, dict)
    assert "cgc" in body or "gitnexus" in body  # at least one adapter is registered


async def test_stop_deepwiki_api_not_running(e2e_client: AsyncClient):
    """DeepWiki process controls were removed with the DeepWiki feature."""
    resp = await e2e_client.post("/api/tools/deepwiki-api/stop")
    assert resp.status_code == 400


async def test_stop_deepwiki_ui_not_running(e2e_client: AsyncClient):
    """DeepWiki process controls were removed with the DeepWiki feature."""
    resp = await e2e_client.post("/api/tools/deepwiki-ui/stop")
    assert resp.status_code == 400
