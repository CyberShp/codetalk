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
        resp = await client.get("/api/tools/status")
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


class TestRestartToolSuccess:
    async def test_restart_returns_success(self, tools_client):
        """Line 57: restart_tool returns success dict when pm.restart() returns True."""
        client, mock_pm = tools_client
        resp = await client.post("/api/tools/gitnexus/restart")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "gitnexus" in body["message"]
