import logging
import logging.handlers
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import configure_runtime_temp_environment, settings
from app.database import init_db
from app.services.ai_conversations import AIConversationStore
from app.services.process_manager import ProcessManager
from app.services.workbench_task_run_events import reconcile_interrupted_task_runs
from app.services.workflow_startup_recovery import reconcile_v3_startup_recovery

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")

_log_dir = settings.data_path / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
_fh = logging.handlers.RotatingFileHandler(
    _log_dir / "backend.log", maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8",
)
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s — %(message)s"))
logging.getLogger().addHandler(_fh)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure data directories and SQLite tables exist on startup
    configure_runtime_temp_environment(settings)
    settings.data_path.mkdir(parents=True, exist_ok=True)
    settings.outputs_path.mkdir(parents=True, exist_ok=True)
    settings.tiktoken_cache_path.mkdir(parents=True, exist_ok=True)
    await init_db()
    from app.services.workbench_task_store import WorkbenchTaskStore
    from app.services.workflow_presets import (
        active_builtin_workflow_presets,
        reserved_builtin_workflow_ids,
    )
    from app.services.workflow_version_store import WorkflowVersionStore

    workflow_versions = WorkflowVersionStore(
        settings.data_path / "workbench" / "workflows.db"
    )
    workflow_migration = workflow_versions.initialize_and_migrate()
    builtin_versions = workflow_versions.ensure_legacy_published_workflows(
        [dict(preset["definition"]) for preset in active_builtin_workflow_presets()]
    )
    active_builtin_ids = {
        str(preset["id"]) for preset in active_builtin_workflow_presets()
    }
    retired_builtins = workflow_versions.retire_workflows(
        reserved_builtin_workflow_ids().difference(active_builtin_ids)
    )
    task_migration = WorkbenchTaskStore(
        settings.data_path / "workbench" / "workflows.db"
    ).initialize_and_migrate()
    if settings.workflow_tool_enabled:
        from app.services.managed_tool_runtime import managed_tool_runtime

        tool_runtime = managed_tool_runtime()
        logger.info(
            "Managed workflow Tool runtime ready: tools=%s",
            sorted(tool_runtime.tools_by_id),
        )
    logger.info(
        "Workbench V2 migrations ready: workflows=%s builtin_versions=%s retired_builtins=%s tasks=%s",
        workflow_migration,
        builtin_versions,
        retired_builtins,
        task_migration,
    )
    ai_reconcile = await AIConversationStore().reconcile_interrupted_runs()
    if ai_reconcile.get("interrupted_count"):
        logger.warning("Reconciled interrupted AI conversation runs: %s", ai_reconcile)
    task_runs_root = settings.data_path / "workbench" / "task_runs"
    v3_recovery = reconcile_v3_startup_recovery(task_runs_root)
    from app.api.agent_workbench import (
        schedule_recovered_v3_task_run,
        schedule_task_run_human_approval_expiries,
    )

    scheduled = [
        decision.task_run_id
        for decision in v3_recovery
        if decision.action == "resume"
        and schedule_recovered_v3_task_run(decision.task_run_id)
    ]
    expiry_monitors = [
        decision.task_run_id
        for decision in v3_recovery
        if decision.action == "waiting_for_input"
        and schedule_task_run_human_approval_expiries(decision.task_run_id)
    ]
    reconcile = reconcile_interrupted_task_runs(
        task_runs_root,
        exclude_task_run_ids={decision.task_run_id for decision in v3_recovery},
    )
    if v3_recovery:
        logger.info(
            "Reconciled V3 startup recovery: decisions=%s scheduled=%s expiry_monitors=%s",
            v3_recovery,
            scheduled,
            expiry_monitors,
        )
    if reconcile.get("interrupted_count"):
        logger.warning("Reconciled interrupted workbench task runs: %s", reconcile)

    # Initialize ProcessManager (tools are NOT auto-started -- user controls via API)
    pm = ProcessManager.get_instance()
    pm.start_monitoring()
    app.state.process_manager = pm
    logger.info("CodeTalk Lightweight backend started on port %s", settings.backend_port)

    yield

    from app.api.agent_workbench import shutdown_task_run_human_approval_expiry_monitors

    await shutdown_task_run_human_approval_expiry_monitors()
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

from app.api import agent_runtimes, agent_workbench, ai_conversations, tasks, settings as settings_router, tools, export, prompts, coverage, ws, workbench_v2_assets, workbench_v2_release, workbench_v2_tasks, workbench_v2_workflows  # noqa: E402
from app.api.repo_analysis import router as repo_analysis_router  # noqa: E402
from app.api.workspaces import router as workspaces_router  # noqa: E402

app.include_router(tasks.router)
app.include_router(agent_workbench.router)
app.include_router(workbench_v2_workflows.router)
app.include_router(workbench_v2_tasks.router)
app.include_router(workbench_v2_assets.router)
app.include_router(workbench_v2_release.router)
app.include_router(ai_conversations.router)
app.include_router(agent_runtimes.router)
app.include_router(settings_router.router)
app.include_router(tools.router)
app.include_router(export.router)
app.include_router(prompts.router)
app.include_router(coverage.router)
app.include_router(ws.router)
app.include_router(workspaces_router)
app.include_router(repo_analysis_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
