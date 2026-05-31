"""Unit tests for app/api/tools.py.

Covers the three lines missed by E2E tests:
- Line 19: _get_pm returns pm from app.state when it is set
- Line 37: start_tool returns success dict when pm.start() returns True
- Line 57: restart_tool returns success dict when pm.restart() returns True
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient

from app.api import tools

pytestmark = [pytest.mark.asyncio]


@asynccontextmanager
async def _no_lifespan(app: FastAPI):
    yield


def _make_app(mock_pm) -> FastAPI:
    app = FastAPI(lifespan=_no_lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.include_router(tools.router)
    app.state.process_manager = mock_pm
    return app


@pytest.fixture
async def tools_client():
    mock_pm = MagicMock()
    mock_pm.get_all_status = AsyncMock(return_value=[{"name": "gitnexus", "status": "stopped"}])
    mock_pm.start = AsyncMock(return_value=True)
    mock_pm.stop = AsyncMock(return_value=True)
    mock_pm.restart = AsyncMock(return_value=True)

    app = _make_app(mock_pm)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, mock_pm


class TestGetPmFromAppState:
    async def test_pm_returned_from_app_state(self, tools_client):
        """Line 19: when app.state.process_manager is set, _get_pm returns it
        without falling through to ProcessManager.get_instance()."""
        client, mock_pm = tools_client
        resp = await client.get("/api/tools/procs")
        assert resp.status_code == 200
        mock_pm.get_all_status.assert_called_once()


class TestStartToolSuccess:
    async def test_start_returns_success(self, tools_client):
        """Line 37: start_tool returns success dict when pm.start() returns True."""
        client, mock_pm = tools_client
        resp = await client.post("/api/tools/gitnexus/start")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "gitnexus" in body["message"]

    async def test_start_failure_includes_process_last_error(self):
        """Failed starts should surface ProcessManager.last_error to the UI."""
        mock_pm = MagicMock()
        mock_pm.start = AsyncMock(return_value=False)
        managed = MagicMock()
        managed.last_error = "Working directory does not exist: X"
        mock_pm._processes = {"deepwiki-api": managed}

        app = _make_app(mock_pm)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/tools/deepwiki-api/start")

        assert resp.status_code == 400
        assert "Working directory does not exist: X" in resp.json()["detail"]


class TestRestartToolSuccess:
    async def test_restart_returns_success(self, tools_client):
        """Line 57: restart_tool returns success dict when pm.restart() returns True."""
        client, mock_pm = tools_client
        resp = await client.post("/api/tools/gitnexus/restart")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "gitnexus" in body["message"]


async def test_deepwiki_registry_uses_venv_launcher_and_declared_ports(tmp_path, monkeypatch):
    """DeepWiki native process config should start the real venv launcher on configured ports."""
    from app.config import settings
    from app.services import process_manager

    deepwiki_dir = tmp_path / "deepwiki-open"
    scripts_dir = "Scripts" if process_manager.sys.platform == "win32" else "bin"
    python_name = "python.exe" if process_manager.sys.platform == "win32" else "python"
    venv_python = deepwiki_dir / ".venv" / scripts_dir / python_name
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    (deepwiki_dir / "package.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(settings, "deepwiki_path", str(deepwiki_dir))
    monkeypatch.setattr(settings, "deepwiki_api_port", 8091)
    monkeypatch.setattr(settings, "deepwiki_ui_port", 3001)

    registry = process_manager._build_registry()

    api = registry["deepwiki-api"]
    assert api["command"][0] == str(venv_python)
    assert api["command"][1].endswith("deepwiki_launcher.py")
    assert api["env"]["DEEPWIKI_API_PORT"] == "8091"
    assert api["env"]["PORT"] == "8091"

    ui = registry["deepwiki-ui"]
    assert ui["env"]["PORT"] == "3001"
    assert ui["env"]["SERVER_BASE_URL"] == "http://localhost:8091"
