"""Shared test fixtures for the CodeTalk Lightweight backend."""

from contextlib import asynccontextmanager
from unittest.mock import patch

import aiosqlite
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient

from app.api import settings as settings_router
from app.api import tasks
from app.database import _MIGRATIONS, _SCHEMA, get_db


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


# ---------------------------------------------------------------------------
# V2 fixtures — service-level tests that need settings.sqlite_db patched
# ---------------------------------------------------------------------------


@pytest.fixture
async def sqlite_db(tmp_path):
    """Create an isolated SQLite DB and patch settings.sqlite_db to point to it.

    V2 services (material_rag, workspace_chat, etc.) connect directly via
    ``aiosqlite.connect(settings.sqlite_db)`` instead of FastAPI's get_db
    dependency, so we must monkeypatch the config value.
    """
    db_path = str(tmp_path / "v2_test.db")
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(_SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                await conn.execute(stmt)
            except aiosqlite.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        await conn.commit()

    with patch("app.config.settings.sqlite_db", db_path):
        yield db_path


@pytest.fixture
def test_app_v2() -> FastAPI:
    """FastAPI app including V2 routers (workspaces, settings)."""
    from app.api import workspaces

    app = FastAPI(lifespan=_test_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(tasks.router)
    app.include_router(settings_router.router)
    app.include_router(workspaces.router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


@pytest.fixture
async def client_v2(
    test_app_v2: FastAPI, sqlite_db: str
) -> AsyncClient:
    """AsyncClient for V2 API tests.

    Both the FastAPI get_db dependency AND settings.sqlite_db point to the
    same isolated temporary database.
    """

    async def _override_get_db():
        conn = await aiosqlite.connect(sqlite_db)
        conn.row_factory = aiosqlite.Row
        try:
            yield conn
        finally:
            await conn.close()

    test_app_v2.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=test_app_v2), base_url="http://test"
    ) as ac:
        yield ac
    test_app_v2.dependency_overrides.clear()
