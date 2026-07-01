"""E2E tests for services API (/api/services/*)."""


async def test_services_status_returns_200(client):
    resp = await client.get("/api/services/status")
    assert resp.status_code == 200


async def test_services_status_has_running_and_processes(client):
    resp = await client.get("/api/services/status")
    body = resp.json()
    assert "running" in body
    assert "processes" in body
    assert isinstance(body["processes"], dict)


async def test_services_health_returns_200(client):
    resp = await client.get("/api/services/health")
    assert resp.status_code == 200


async def test_services_health_has_services_key(client):
    resp = await client.get("/api/services/health")
    body = resp.json()
    assert "services" in body


async def test_services_stop_all_when_idle_is_ok(client):
    resp = await client.post("/api/services/stop")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True


async def test_services_stop_all_idempotent(client):
    r1 = await client.post("/api/services/stop")
    r2 = await client.post("/api/services/stop")
    assert r1.status_code == r2.status_code == 200


async def test_service_restart_without_deployer_returns_400(client):
    resp = await client.post("/api/services/backend/restart")
    assert resp.status_code == 400


async def test_service_stop_without_deployer_returns_400(client):
    resp = await client.post("/api/services/backend/stop")
    assert resp.status_code == 400


async def test_service_start_without_deployer_returns_400(client):
    resp = await client.post("/api/services/backend/start")
    assert resp.status_code == 400


async def test_unknown_service_restart_without_deployer_returns_400(client):
    resp = await client.post("/api/services/nonexistent_svc/restart")
    assert resp.status_code == 404


async def test_frontend_service_restart_without_deployer_returns_400(client):
    resp = await client.post("/api/services/frontend/restart")
    assert resp.status_code == 400


async def test_gitnexus_service_restart_without_deployer_returns_400(client):
    resp = await client.post("/api/services/gitnexus/restart")
    assert resp.status_code == 400


async def test_services_status_processes_empty_initially(client):
    resp = await client.get("/api/services/status")
    data = resp.json()
    assert data["running"] is False
    assert data["processes"] == {}


async def test_deploy_status_compat_matches_services_status(client):
    """GET /api/deploy/status is a compatibility shim for /api/services/status."""
    resp_deploy = await client.get("/api/deploy/status")
    resp_services = await client.get("/api/services/status")
    assert resp_deploy.status_code == 200
    assert resp_services.status_code == 200
    assert resp_deploy.json() == resp_services.json()


async def test_removed_deepwiki_service_actions_are_rejected_before_deployer(client):
    """Old cached pages must not be able to start removed DeepWiki services."""
    for action in ("start", "stop", "restart"):
        resp = await client.post(f"/api/services/deepwiki-api/{action}")
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["service"] == "deepwiki-api"
        assert "deepwiki-api" not in detail["available_services"]
        assert detail["available_services"] == ["backend", "frontend", "gitnexus", "cgc"]


async def test_services_status_filters_removed_deepwiki_stale_processes(client):
    """Stale deployer process state cannot put removed tools back on the start page."""
    import server

    class FakeProc:
        pid = 12345
        returncode = None

    class FakeDeployer:
        _processes = {
            "backend": FakeProc(),
            "deepwiki-api": FakeProc(),
            "deepwiki-ui": FakeProc(),
        }

    server._state.deployer = FakeDeployer()

    resp = await client.get("/api/services/status")

    assert resp.status_code == 200
    processes = resp.json()["processes"]
    assert list(processes) == ["backend"]
    assert "deepwiki-api" not in processes
    assert "deepwiki-ui" not in processes
