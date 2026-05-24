"""E2E test fixtures -- real app, real SQLite database, ZERO mocks.

Strategy:
- Build a test FastAPI app that includes ALL the routers from main.py
  but with a no-op lifespan (ProcessManager is skipped).
- Monkeypatch ``settings.sqlite_db`` and ``settings.data_dir`` to point at
  a per-test tmp directory so each test gets an isolated database.
- Initialize the DB with the real schema + migrations BEFORE creating the
  client (httpx ASGITransport does not trigger FastAPI lifespan events).
- NO dependency_overrides -- every request hits the real ``get_db()``.
"""

import os
from contextlib import asynccontextmanager

import aiosqlite
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import _MIGRATIONS, _SCHEMA

# ------------------------------------------------------------------
# Detect DEEPSEEK_API_KEY for LLM-dependent tests
# ------------------------------------------------------------------
HAS_DEEPSEEK = bool(os.environ.get("DEEPSEEK_API_KEY", ""))


# ------------------------------------------------------------------
# No-op lifespan (DB init is done in the fixture instead)
# ------------------------------------------------------------------
@asynccontextmanager
async def _e2e_lifespan(app: FastAPI):
    yield


# ------------------------------------------------------------------
# App builder -- mirrors main.py router setup
# ------------------------------------------------------------------
def _build_e2e_app() -> FastAPI:
    app = FastAPI(title="CodeTalk E2E Test", lifespan=_e2e_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from app.api import (
        coverage,
        export,
        prompts,
        settings as settings_router,
        tasks,
        tools,
    )
    from app.api.deepwiki_pages import router as deepwiki_router
    from app.api.workspaces import router as workspaces_router

    app.include_router(tasks.router)
    app.include_router(settings_router.router)
    app.include_router(tools.router)
    app.include_router(export.router)
    app.include_router(prompts.router)
    app.include_router(coverage.router)
    app.include_router(workspaces_router)
    app.include_router(deepwiki_router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


# ------------------------------------------------------------------
# DB bootstrap -- runs real schema + migrations + seed
# ------------------------------------------------------------------
async def _init_test_db(db_path: str) -> None:
    """Create all tables, run migrations, and seed default prompt template."""
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                await db.execute(stmt)
            except aiosqlite.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        await db.commit()

    from app.api.prompts import seed_default_template

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await seed_default_template(db)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------
@pytest.fixture
async def e2e_client(tmp_path, monkeypatch):
    """AsyncClient wired to the real app with an isolated temp database."""
    db_path = str(tmp_path / "e2e_test.db")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "outputs").mkdir()
    ws_dir = data_dir / "workspaces"
    ws_dir.mkdir()

    # Monkeypatch config (configuration, not mocking)
    monkeypatch.setattr(settings, "sqlite_db", db_path)
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    from app.api import workspaces

    monkeypatch.setattr(workspaces, "_MATERIALS_ROOT", ws_dir)

    # Initialize real DB BEFORE creating the client
    await _init_test_db(db_path)

    app = _build_e2e_app()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://e2e-test"
    ) as client:
        yield client


@pytest.fixture
def repo_path(tmp_path):
    """Create a minimal directory for endpoints that validate Path.exists()."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Test Repo", encoding="utf-8")
    (repo / "main.py").write_text("print('hello')", encoding="utf-8")
    return str(repo)
