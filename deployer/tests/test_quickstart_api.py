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


async def test_quickstart_port_conflict_returns_chinese_actionable_detail(client, monkeypatch):
    import server

    class FakeNativeDeployer:
        def __init__(self, cfg, event_queue):
            self._processes = {}
            self._start_args = {}

        async def _scan_port_conflicts(self, ports):
            return [{"port": ports[0], "pid": 4321, "process": "python"}]

    monkeypatch.setattr(server, "NativeDeployer", FakeNativeDeployer)

    resp = await client.post("/api/quickstart", json={})

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["message"] == "检测到端口冲突，服务尚未启动"
    assert "修改冲突端口" in detail["hint"]
    assert detail["conflicts"][0]["pid"] == 4321
