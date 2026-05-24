"""E2E tests for deploy API (/api/deploy POST/stop, /api/deploy/status, SSE stream)."""


async def test_deploy_compose_mode_rejected(client):
    resp = await client.post("/api/deploy", json={"mode": "compose"})
    assert resp.status_code == 400
    assert "not supported" in resp.json().get("detail", "").lower()


async def test_deploy_k8s_mode_rejected(client):
    resp = await client.post("/api/deploy", json={"mode": "k8s"})
    assert resp.status_code == 400


async def test_deploy_unknown_mode_rejected(client):
    resp = await client.post("/api/deploy", json={"mode": "docker-swarm"})
    assert resp.status_code == 400


async def test_deploy_status_returns_200(client):
    resp = await client.get("/api/deploy/status")
    assert resp.status_code == 200


async def test_deploy_status_has_running_field(client):
    resp = await client.get("/api/deploy/status")
    body = resp.json()
    assert "running" in body
    assert isinstance(body["running"], bool)


async def test_deploy_status_has_processes_field(client):
    resp = await client.get("/api/deploy/status")
    body = resp.json()
    assert "processes" in body
    assert isinstance(body["processes"], dict)


async def test_deploy_stop_when_idle_returns_ok(client):
    resp = await client.post("/api/deploy/stop")
    assert resp.status_code == 200
    assert resp.json().get("ok") is True


async def test_deploy_stream_returns_event_stream(client):
    async with client.stream("GET", "/api/deploy/stream") as resp:
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "text/event-stream" in ct


async def test_deploy_missing_mode_uses_native(client):
    # When mode is missing, server.py defaults to "native" and attempts deploy.
    # This will either succeed with a job_id (200) or fail with port conflict (409).
    resp = await client.post("/api/deploy", json={})
    # Should not be 400 (mode rejection) -- native is the default
    assert resp.status_code != 400


async def test_supplement_deepwiki_without_path_rejected(client):
    resp = await client.post("/api/deploy/supplement/deepwiki", json={})
    assert resp.status_code == 400
    detail = resp.json().get("detail", "")
    assert "deepwikiPath" in detail or "required" in detail.lower()


async def test_supplement_deepwiki_empty_path_rejected(client):
    resp = await client.post("/api/deploy/supplement/deepwiki", json={"deepwikiPath": "   "})
    assert resp.status_code == 400


async def test_supplement_gitnexus_returns_job_id(client):
    resp = await client.post("/api/deploy/supplement/gitnexus")
    assert resp.status_code == 200
    body = resp.json()
    assert "job_id" in body
