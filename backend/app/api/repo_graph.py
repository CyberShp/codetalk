"""Repository-level graph data endpoint."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.task import AnalysisTask

router = APIRouter(prefix="/api/repos", tags=["repo-graph"])


@router.get("/{repo_id}/graph")
async def get_repo_graph(repo_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get latest graph data for repo.

    Scans the most recent completed tasks (up to 10) to find the latest one
    that contains a completed gitnexus tool run.  This avoids mis-reporting
    'not_analyzed' when the newest task simply didn't include gitnexus but an
    older task already produced valid graph data.
    """
    result = await db.execute(
        select(AnalysisTask)
        .options(selectinload(AnalysisTask.tool_runs))
        .where(AnalysisTask.repository_id == repo_id)
        .where(AnalysisTask.status == "completed")
        .order_by(AnalysisTask.completed_at.desc())
        .limit(10)
    )
    tasks = result.scalars().all()

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

    return {"status": "not_analyzed", "graph": None, "metadata": None, "analyzed_at": None}
