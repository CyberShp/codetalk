"""E2E tests for /api/quickstart endpoint."""


async def test_quickstart_returns_job_id(client):
    resp = await client.post("/api/quickstart", json={})
    assert resp.status_code == 200
    assert "job_id" in resp.json()


async def test_quickstart_with_force_takeover(client):
    resp = await client.post("/api/quickstart", json={"forceTakeover": True})
    assert resp.status_code == 200
    assert "job_id" in resp.json()


async def test_quickstart_conflict_when_already_running(client):
    import server
    server._state.running = True
    resp = await client.post("/api/quickstart", json={})
    assert resp.status_code == 409


async def test_quickstart_empty_body_accepted(client):
    resp = await client.post("/api/quickstart")
    assert resp.status_code in (200, 409)
