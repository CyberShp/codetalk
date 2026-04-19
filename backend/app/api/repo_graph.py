"""Repository-level graph data endpoint."""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters import create_adapter
from app.adapters.base import AnalysisRequest
from app.database import get_db
from app.models.repository import Repository
from app.models.task import AnalysisTask

router = APIRouter(prefix="/api/repos", tags=["repo-graph"])
logger = logging.getLogger(__name__)


def _task_graph_response(tasks: list[AnalysisTask]) -> dict | None:
    for task in tasks:
        gitnexus_run = next(
            (
                r
                for r in task.tool_runs
                if r.tool_name == "gitnexus" and r.status == "completed" and r.result
            ),
            None,
        )
        if gitnexus_run:
            return {
                "status": "ready",
                "graph": gitnexus_run.result.get("graph"),
                "metadata": gitnexus_run.result.get("metadata"),
                "analyzed_at": task.completed_at.isoformat() if task.completed_at else None,
            }
    return None


async def _build_live_graph_response(repo_local_path: str) -> dict:
    adapter = create_adapter("gitnexus")
    request = AnalysisRequest(repo_local_path=repo_local_path)
    try:
        await adapter.prepare(request)
        result = await adapter.analyze(request)
        return {
            "status": "ready",
            "graph": result.data.get("graph"),
            "metadata": result.metadata,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        await adapter.cleanup(request)


@router.get("/{repo_id}/graph")
async def get_repo_graph(repo_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get latest graph data for repo.

    Scans the most recent completed tasks (up to 10) to find the latest one
    that contains a completed gitnexus tool run.  This avoids mis-reporting
    'not_analyzed' when the newest task simply didn't include gitnexus but an
    older task already produced valid graph data.
    """
    repo = await db.get(Repository, repo_id)
    if not repo or not repo.local_path:
        return {"status": "not_analyzed", "graph": None, "metadata": None, "analyzed_at": None}

    result = await db.execute(
        select(AnalysisTask)
        .options(selectinload(AnalysisTask.tool_runs))
        .where(AnalysisTask.repository_id == repo_id)
        .where(AnalysisTask.status == "completed")
        .order_by(AnalysisTask.completed_at.desc())
        .limit(10)
    )
    tasks = result.scalars().all()

    cached = _task_graph_response(tasks)
    if cached:
        return cached

    try:
        return await _build_live_graph_response(repo.local_path)
    except Exception as exc:
        logger.warning("Live GitNexus graph failed for repo %s: %s (%s)", repo_id, exc, type(exc).__name__)

    return {"status": "not_analyzed", "graph": None, "metadata": None, "analyzed_at": None}
