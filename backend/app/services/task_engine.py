"""Task execution engine — orchestrates tool adapters for analysis tasks."""

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import create_adapter
from app.adapters.base import AnalysisRequest, UnifiedResult
from app.database import async_session
from app.models.llm_config import LLMConfig
from app.models.repository import Repository
from app.models.task import AnalysisTask, ToolRun
from app.services import source_manager

logger = logging.getLogger(__name__)


async def run_task(task_id: UUID) -> None:
    async with async_session() as db:
        task = await db.get(AnalysisTask, task_id)
        if not task:
            logger.error("Task %s not found", task_id)
            return

        task.status = "running"
        task.started_at = datetime.now(timezone.utc)
        await db.commit()

        try:
            repo = await db.get(Repository, task.repository_id)
            if not repo:
                raise RuntimeError(f"Repository {task.repository_id} not found")

            local_path = await source_manager.resolve_source(repo)
            if repo.local_path != local_path:
                repo.local_path = local_path
                await db.commit()

            options = await _build_options(task, db)
            request = AnalysisRequest(
                repo_local_path=local_path,
                target_files=task.target_spec.get("files"),
                task_type=task.task_type,
                options=options,
            )

            adapters = []
            for tool_name in task.tools:
                try:
                    adapters.append(create_adapter(tool_name))
                except KeyError:
                    logger.warning("Unknown adapter: %s, skipping", tool_name)

            if not adapters:
                raise RuntimeError("No valid tool adapters for this task")

            tool_runs = await _create_tool_runs(db, task_id, adapters)

            results: list[UnifiedResult] = []
            completed = 0
            for adapter, run in zip(adapters, tool_runs):
                run.status = "running"
                run.started_at = datetime.now(timezone.utc)
                await db.commit()

                try:
                    await adapter.prepare(request)
                    result = await adapter.analyze(request)
                    run.status = "completed"
                    run.completed_at = datetime.now(timezone.utc)
                    run.result = {
                        "documentation": result.data.get("documentation", ""),
                        "file_tree": result.data.get("file_tree", ""),
                        "diagrams": result.diagrams,
                        "metadata": result.metadata,
                    }
                    results.append(result)
                except Exception as exc:
                    logger.error("Adapter %s failed: %s", adapter.name(), exc)
                    run.status = "failed"
                    run.completed_at = datetime.now(timezone.utc)
                    run.error = str(exc)

                completed += 1
                task.progress = int(completed / len(adapters) * 100)
                await db.commit()

            task.completed_at = datetime.now(timezone.utc)
            task.progress = 100

            if not results:
                failed_errors = [r.error for r in tool_runs if r.error]
                task.status = "failed"
                task.error = "; ".join(failed_errors) or "All tool runs failed"
            else:
                task.status = "completed"
                if task.ai_enabled:
                    summary = _build_summary(results)
                    task.ai_summary = summary

            await db.commit()

        except Exception as exc:
            logger.exception("Task %s failed", task_id)
            task.status = "failed"
            task.error = str(exc)
            task.completed_at = datetime.now(timezone.utc)
            await db.commit()


async def _create_tool_runs(
    db: AsyncSession, task_id: UUID, adapters: list
) -> list[ToolRun]:
    runs = []
    for adapter in adapters:
        run = ToolRun(task_id=task_id, tool_name=adapter.name())
        db.add(run)
        runs.append(run)
    await db.commit()
    for run in runs:
        await db.refresh(run)
    return runs


async def _build_options(task: AnalysisTask, db: AsyncSession) -> dict:
    options = dict(task.target_spec.get("options", {}))
    if task.ai_enabled:
        result = await db.execute(
            select(LLMConfig).where(LLMConfig.is_default.is_(True)).limit(1)
        )
        llm_config = result.scalar_one_or_none()
        if llm_config:
            options.setdefault("provider", llm_config.provider)
            options.setdefault("model", llm_config.model_name)
    return options


def _build_summary(results: list[UnifiedResult]) -> str:
    parts = []
    for r in results:
        doc = r.data.get("documentation", "")
        n_diagrams = len(r.diagrams)
        preview = doc[:200] + "..." if len(doc) > 200 else doc
        parts.append(
            f"{r.tool_name}: generated documentation ({len(doc)} chars, "
            f"{n_diagrams} diagrams). Preview: {preview}"
        )
    return " | ".join(parts)
