import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text, update

from app.api.router import api_router
from app.config import settings
from app.database import engine, get_db
from app.middleware.session import AnonymousSessionMiddleware

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")

logger = logging.getLogger(__name__)


async def _load_backend_configs() -> None:
    """Load previously applied backend-target configs from DB into runtime settings."""
    from app.services import component_manager as cm

    try:
        async for db in get_db():
            configs = await cm.get_all_configs(db)
            for cfg in configs:
                if not cfg.applied_at:
                    continue
                contract = cm.CONTRACTS.get(cfg.component)
                if not contract:
                    continue
                domain = next(
                    (d for d in contract.domains if d.domain == cfg.domain), None
                )
                if domain and domain.target == "backend":
                    cm._apply_backend_config(cfg)
            break  # get_db is an async generator; one session is enough
    except Exception as exc:
        logger.warning("Could not load backend configs from DB at startup: %s", exc)


async def _recover_orphaned_tasks() -> None:
    """Mark any tasks stuck in 'running' as 'failed' on startup.

    If the backend crashed or restarted while tasks were in-flight, their
    DB rows stay 'running' forever because the in-memory handles are lost.
    """
    from app.models.task import AnalysisTask

    try:
        async for db in get_db():
            result = await db.execute(
                update(AnalysisTask)
                .where(AnalysisTask.status == "running")
                .values(
                    status="failed",
                    error="任务因服务重启而中断，请重新执行。",
                    completed_at=datetime.now(timezone.utc),
                )
                .returning(AnalysisTask.id)
            )
            orphans = result.scalars().all()
            if orphans:
                await db.commit()
                logger.warning(
                    "Recovered %d orphaned running task(s): %s",
                    len(orphans),
                    [str(oid) for oid in orphans],
                )
            break
    except Exception as exc:
        logger.warning("Could not recover orphaned tasks: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verify database connection
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
    # Recover orphaned tasks from previous crashes
    await _recover_orphaned_tasks()
    # Load persisted backend-target configs (tool URLs, docker_host, etc.)
    await _load_backend_configs()
    yield
    # Shutdown
    await engine.dispose()


app = FastAPI(
    title="CodeTalks",
    description="Code Analysis Orchestration Platform",
    version="0.1.0",
    lifespan=lifespan,
)

_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AnonymousSessionMiddleware)

app.include_router(api_router)


@app.get("/")
async def root():
    return {"name": "CodeTalks", "version": "0.1.0", "status": "running"}


@app.get("/health")
async def health():
    return {"status": "ok"}
