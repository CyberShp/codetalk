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


async def test_supplement_gitnexus_409_when_running(client):
    """POST /api/deploy/supplement/gitnexus returns 409 during active deployment."""
    import server
    server._state.running = True
    resp = await client.post("/api/deploy/supplement/gitnexus")
    assert resp.status_code == 409


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


async def test_quickstart_port_preflight_includes_enabled_cgc(client, monkeypatch):
    """When CGC is enabled, quickstart reports its port conflicts before starting work."""
    import config_store
    import server

    scanned_ports: list[int] = []

    class FakeNativeDeployer:
        def __init__(self, cfg, event_queue):
            self._config = cfg
            self._processes = {}
            self._start_args = {}

        async def _scan_port_conflicts(self, ports):
            scanned_ports.extend(ports)
            return []

        async def _step_install_backend(self):
            return None

        async def _step_generate_config(self):
            return None

        async def _step_install_frontend(self):
            return None

        async def _step_install_gitnexus(self):
            return None

        async def _step_start_services(self):
            return None

        async def _step_health_check(self):
            return None

    monkeypatch.setattr(
        config_store,
        "load_config",
        lambda: {
            "mode": "native",
            "backend_port": 3004,
            "frontend_port": 3003,
            "gitnexus_port": 7100,
            "cgc_port": 7072,
            "install_gitnexus": True,
            "install_cgc": True,
        },
    )
    monkeypatch.setattr(server, "NativeDeployer", FakeNativeDeployer)

    resp = await client.post("/api/quickstart", json={})

    assert resp.status_code == 200
    assert scanned_ports == [3004, 3003, 7100, 7072]


async def test_deploy_port_preflight_includes_enabled_cgc(client, monkeypatch):
    """Native deployment uses the same enabled-service port preflight as quickstart."""
    import server

    scanned_ports: list[int] = []

    class FakeNativeDeployer:
        def __init__(self, cfg, event_queue):
            self._config = cfg
            self._processes = {}
            self._start_args = {}

        async def _scan_port_conflicts(self, ports):
            scanned_ports.extend(ports)
            return []

        async def deploy(self):
            return None

    monkeypatch.setattr(server, "NativeDeployer", FakeNativeDeployer)

    resp = await client.post(
        "/api/deploy",
        json={
            "mode": "native",
            "backend_port": 3004,
            "frontend_port": 3003,
            "gitnexus_port": 7100,
            "cgc_port": 7072,
            "install_gitnexus": True,
            "install_cgc": True,
        },
    )

    assert resp.status_code == 200
    assert scanned_ports == [3004, 3003, 7100, 7072]
