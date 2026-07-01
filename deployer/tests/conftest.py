"""Shared fixtures for deployer E2E tests.

Uses ASGITransport to drive the real FastAPI app in-process.
Zero mocks: config isolation is done by redirecting CONFIG_PATH to a tmp file.
"""

import asyncio
import gc
import sys
from contextlib import suppress
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
async def reset_deploy_state():
    """Reset module-level deployment state before and after every test.

    Recreates the entire DeploymentState (including the asyncio.Lock) so that
    each test starts with a fresh lock bound to the current event loop. Any
    background deployment task started by a test is cancelled and awaited before
    the loop closes so asyncio subprocess transports do not leak.
    """
    import server

    async def _cleanup():
        task = server._state.task
        deployer = server._state.deployer
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        if deployer is not None and hasattr(deployer, "stop"):
            with suppress(Exception):
                await deployer.stop()
        server._state = server.DeploymentState()
        gc.collect()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    server._state = server.DeploymentState()
    yield
    await _cleanup()
