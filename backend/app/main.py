import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.services.process_manager import ProcessManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure data directories and SQLite tables exist on startup
    settings.data_path.mkdir(parents=True, exist_ok=True)
    settings.outputs_path.mkdir(parents=True, exist_ok=True)
    settings.tiktoken_cache_path.mkdir(parents=True, exist_ok=True)
    await init_db()

    # Initialize ProcessManager (tools are NOT auto-started -- user controls via API)
    pm = ProcessManager.get_instance()
    pm.start_monitoring()
    app.state.process_manager = pm
    logger.info("CodeTalk Lightweight backend started on port 8100")

    yield

    # Graceful shutdown: stop all managed tool processes
    await pm.shutdown_all()
    logger.info("CodeTalk backend shut down")


app = FastAPI(title="CodeTalk Lightweight API", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.api import tasks, settings as settings_router, tools, export, prompts, coverage  # noqa: E402

app.include_router(tasks.router)
app.include_router(settings_router.router)
app.include_router(tools.router)
app.include_router(export.router)
app.include_router(prompts.router)
app.include_router(coverage.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
