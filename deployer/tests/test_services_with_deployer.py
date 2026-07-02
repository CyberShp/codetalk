"""Tests for service routes when _state.deployer is a real NativeDeployer.

Covers:
- /api/services/{service}/restart → KeyError (service not in _start_args)
- /api/services/{service}/stop   → KeyError (service not in _start_args)
- /api/services/{service}/start  → KeyError (service not in _start_args)
- POST /api/services/stop        → calls deployer.stop() when deployer is set
- GET /api/services/status       → shows populated processes dict
- GET /api/services/health       → uses deployer.check_health() when deployer set
"""

import asyncio
import sys
from pathlib import Path

import pytest
import pytest_asyncio

DEPLOYER_DIR = Path(__file__).parent.parent
if str(DEPLOYER_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYER_DIR))


def _make_deployer():
    from deployers.native import NativeDeployer
    cfg = {
        "mode": "native",
        "backend_port": 3004,
        "frontend_port": 3003,
        "gitnexus_port": 7100,
    }
    return NativeDeployer(cfg, asyncio.Queue())


@pytest_asyncio.fixture(autouse=True)
async def reset_state():
    """Ensure _state is cleared before and after every test."""
    import server
    server._state.running = False
    server._state.deployer = None
    server._state.task = None
    server._state.event_queue = None
    yield
    deployer = server._state.deployer
    if deployer is not None and hasattr(deployer, "stop"):
        await deployer.stop()
    server._state.running = False
    server._state.deployer = None
    server._state.task = None
    server._state.event_queue = None


async def test_service_restart_with_deployer_unknown_service_raises_404(client):
    """With a real deployer set, restarting an unknown service raises 404."""
    import server
    server._state.deployer = _make_deployer()
    resp = await client.post("/api/services/unknown_xyz/restart")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["service"] == "unknown_xyz"
    assert detail["action"] == "restart"
    assert "unknown_xyz" in detail["message"]
    assert "available_services" in detail
    assert "backend" in detail["available_services"]


async def test_service_restart_with_deployer_backend_uses_defaults(client, monkeypatch):
    """backend restart must reconstruct default args and reach spawn, not 404."""
    import server

    deployer = _make_deployer()
    spawned: list[tuple[str, list[str], str, dict | None]] = []

    async def fake_spawn(name, cmd, cwd, step_name, step_index, env_extra=None):
        spawned.append((name, cmd, cwd, env_extra))

    monkeypatch.setattr(deployer, "_spawn_process", fake_spawn)
    server._state.deployer = deployer

    resp = await client.post("/api/services/backend/restart")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "service": "backend"}
    assert spawned
    name, cmd, cwd, env_extra = spawned[0]
    assert name == "backend"
    assert "uvicorn" in cmd
    assert cwd.endswith("/backend")
    assert env_extra == {
        "CODETALK_BACKEND_PORT": "3004",
        "CORS_ORIGINS": "http://localhost:3003,http://127.0.0.1:3003",
    }


async def test_service_stop_with_deployer_unknown_service_raises_404(client):
    """stop_service with unknown service raises KeyError → 404."""
    import server
    server._state.deployer = _make_deployer()
    resp = await client.post("/api/services/unknown_xyz/stop")
    assert resp.status_code == 404


async def test_service_stop_with_deployer_backend_no_start_args_raises_404(client):
    """backend not in _start_args → stop_service raises KeyError → 404."""
    import server
    server._state.deployer = _make_deployer()
    resp = await client.post("/api/services/backend/stop")
    assert resp.status_code == 404


async def test_service_start_with_deployer_unknown_service_raises_404(client):
    """start_service with unknown service raises KeyError → 404."""
    import server
    server._state.deployer = _make_deployer()
    resp = await client.post("/api/services/unknown_xyz/start")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["service"] == "unknown_xyz"
    assert detail["action"] == "start"
    assert "available_services" in detail


async def test_service_start_with_deployer_backend_uses_defaults(client, monkeypatch):
    """backend start must reconstruct default args and reach spawn, not 404."""
    import server

    deployer = _make_deployer()
    spawned: list[str] = []

    async def fake_spawn(name, cmd, cwd, step_name, step_index, env_extra=None):
        spawned.append(name)

    monkeypatch.setattr(deployer, "_spawn_process", fake_spawn)
    server._state.deployer = deployer

    resp = await client.post("/api/services/backend/start")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "service": "backend", "action": "started"}
    assert spawned == ["backend"]


async def test_services_stop_all_with_deployer_calls_stop(client):
    """POST /api/services/stop with a deployer set calls deployer.stop()."""
    import server
    deployer = _make_deployer()
    server._state.deployer = deployer
    server._state.running = True

    resp = await client.post("/api/services/stop")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert server._state.running is False


async def test_services_status_with_deployer_shows_processes(client):
    """GET /api/services/status reflects deployer._processes dict."""
    import server
    deployer = _make_deployer()
    server._state.deployer = deployer
    server._state.running = True

    resp = await client.get("/api/services/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is True
    assert "processes" in body
    assert isinstance(body["processes"], dict)


async def test_services_health_with_deployer_uses_real_check(client):
    """GET /api/services/health uses the existing deployer when set."""
    import server
    deployer = _make_deployer()
    server._state.deployer = deployer

    resp = await client.get("/api/services/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "services" in body
    assert isinstance(body["services"], list)


async def test_service_frontend_restart_with_deployer_uses_defaults(client, monkeypatch):
    """frontend restart must rebuild and spawn through default args, not 404."""
    import server

    deployer = _make_deployer()
    events: list[str] = []

    async def fake_install_frontend():
        events.append("install_frontend")

    async def fake_spawn(name, cmd, cwd, step_name, step_index, env_extra=None):
        events.append(
            (
                f"spawn:{name}:{env_extra.get('PORT') if env_extra else ''}",
                env_extra,
            )
        )

    monkeypatch.setattr(deployer, "_step_install_frontend", fake_install_frontend)
    monkeypatch.setattr(deployer, "_spawn_process", fake_spawn)
    server._state.deployer = deployer

    resp = await client.post("/api/services/frontend/restart")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "service": "frontend"}
    assert events == [
        "install_frontend",
        (
            "spawn:frontend:3003",
            {
                "PORT": "3003",
                "CODETALK_FRONTEND_PORT": "3003",
                "CODETALK_BACKEND_PORT": "3004",
                "NEXT_PUBLIC_API_URL": "http://localhost:3004",
            },
        ),
    ]


async def test_service_gitnexus_stop_with_deployer_raises_404(client):
    """gitnexus not in _start_args → stop raises KeyError → 404."""
    import server
    server._state.deployer = _make_deployer()
    resp = await client.post("/api/services/gitnexus/stop")
    assert resp.status_code == 404
