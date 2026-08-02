"""E2E tests for /api/quickstart endpoint."""


async def test_quickstart_returns_job_id(client):
    # Ports 3004/3003 may be occupied in dev environments; accept port-conflict 409.
    resp = await client.post("/api/quickstart", json={})
    assert resp.status_code in (200, 409)
    if resp.status_code == 200:
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
    assert "部署任务正在运行" in resp.json()["detail"]


async def test_quickstart_empty_body_accepted(client):
    resp = await client.post("/api/quickstart")
    assert resp.status_code in (200, 409)


async def test_quickstart_does_not_reject_port_conflicts_before_start(client, monkeypatch):
    import server

    class FakeNativeDeployer:
        def __init__(self, cfg, event_queue):
            self._config = cfg
            self._processes = {}
            self._start_args = {}

        async def _scan_port_conflicts(self, ports):
            raise AssertionError("quickstart should resolve ports inside NativeDeployer startup")

        async def _step_install_backend(self):
            return None

        async def _step_generate_config(self):
            return None

        async def _step_install_frontend(self):
            return None

        async def _step_start_services(self):
            return None

        async def _step_health_check(self):
            return None

    monkeypatch.setattr(server, "NativeDeployer", FakeNativeDeployer)

    resp = await client.post("/api/quickstart", json={})

    assert resp.status_code == 200
    assert "job_id" in resp.json()
