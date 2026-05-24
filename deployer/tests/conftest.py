"""Shared fixtures for deployer E2E tests.

Uses ASGITransport to drive the real FastAPI app in-process.
Zero mocks: config isolation is done by redirecting CONFIG_PATH to a tmp file.
"""

import sys
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport

DEPLOYER_DIR = Path(__file__).parent.parent
if str(DEPLOYER_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYER_DIR))

import config_store  # noqa: E402


@pytest.fixture(scope="session")
def deployer_app():
    from server import app
    return app


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Redirect CONFIG_PATH to a per-test temp file so tests don't share state."""
    monkeypatch.setattr(config_store, "CONFIG_PATH", tmp_path / ".deploy-config.json")
    return tmp_path / ".deploy-config.json"


@pytest.fixture()
async def client(deployer_app):
    async with httpx.AsyncClient(
        transport=ASGITransport(app=deployer_app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest.fixture(autouse=True)
def reset_deploy_state():
    """Reset module-level deployment state before and after every test."""
    import server

    def _clear():
        server._state.running = False
        server._state.deployer = None
        server._state.job_id = None
        server._state.task = None
        server._state.event_queue = None

    _clear()
    yield
    _clear()
