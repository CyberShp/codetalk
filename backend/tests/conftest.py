"""Shared test fixtures for the CodeTalk Lightweight backend."""

from contextlib import asynccontextmanager

import aiosqlite
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient

from app.api import settings as settings_router
from app.api import tasks
from app.database import _SCHEMA, get_db


@asynccontextmanager
async def _test_lifespan(app: FastAPI):
    yield


@pytest.fixture
def test_app() -> FastAPI:
    """Minimal FastAPI app with no side-effecting lifespan (no ProcessManager)."""
    app = FastAPI(lifespan=_test_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(tasks.router)
    app.include_router(settings_router.router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


@pytest.fixture
async def db(tmp_path) -> aiosqlite.Connection:
    """Isolated SQLite connection per test."""
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    conn.row_factory = aiosqlite.Row
    await conn.executescript(_SCHEMA)
    await conn.commit()
    yield conn
    await conn.close()


@pytest.fixture
async def client(test_app: FastAPI, db: aiosqlite.Connection) -> AsyncClient:
    """AsyncClient with get_db overridden to use the isolated test DB."""

    async def _override_get_db():
        yield db

    test_app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as ac:
        yield ac
    test_app.dependency_overrides.clear()
