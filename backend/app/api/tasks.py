import asyncio
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.repository import Repository
from app.models.task import AnalysisTask, ToolRun
from app.schemas.task import TaskCreate, TaskDetailResponse, TaskResponse, ToolRunResponse
from app.services import task_engine

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _serialize_task(task: AnalysisTask) -> TaskResponse:
    payload = TaskResponse.model_validate(task).model_dump()
    payload["repository_name"] = task.repository.name if task.repository else None
    return TaskResponse(**payload)


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    status: str | None = None,
    repository_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(AnalysisTask)
        .options(selectinload(AnalysisTask.repository))
        .order_by(AnalysisTask.created_at.desc())
    )
    if status:
        query = query.where(AnalysisTask.status == status)
    if repository_id:
        query = query.where(AnalysisTask.repository_id == repository_id)
    result = await db.execute(query)
    return [_serialize_task(t) for t in result.scalars().all()]


@router.post("", response_model=TaskResponse, status_code=201)
async def create_task(data: TaskCreate, db: AsyncSession = Depends(get_db)):
    task = AnalysisTask(
        repository_id=data.repository_id,
        task_type=data.task_type.value,
        tools=data.tools,
        ai_enabled=data.ai_enabled,
        target_spec=data.target_spec,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    await db.refresh(task, attribute_names=["repository"])
    handle = asyncio.create_task(task_engine.run_task(task.id))
    task_engine.register_task(task.id, handle)
    return _serialize_task(task)


@router.get("/{task_id}", response_model=TaskDetailResponse)
async def get_task(task_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AnalysisTask)
        .options(
            selectinload(AnalysisTask.tool_runs),
            selectinload(AnalysisTask.repository),
        )
        .where(AnalysisTask.id == task_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskDetailResponse(
        **_serialize_task(task).model_dump(),
        tool_runs=[ToolRunResponse.model_validate(r) for r in task.tool_runs],
    )


@router.get("/{task_id}/results")
async def get_task_results(task_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ToolRun).where(ToolRun.task_id == task_id).order_by(ToolRun.started_at)
    )
    runs = result.scalars().all()
    return {
        "task_id": str(task_id),
        "tool_runs": [ToolRunResponse.model_validate(r).model_dump() for r in runs],
    }


@router.post("/{task_id}/cancel")
async def cancel_task_endpoint(task_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    task = await db.get(AnalysisTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in ("pending", "running"):
        raise HTTPException(status_code=409, detail="Task is not cancellable")

    cancelled = await task_engine.cancel_task(task_id)
    if not cancelled:
        task.status = "cancelled"
        task.completed_at = datetime.now(timezone.utc)
        await db.commit()

    return {"status": "cancelled"}


@router.delete("/{task_id}", status_code=204)
async def delete_task(task_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    task = await db.get(AnalysisTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status in ("pending", "running"):
        await task_engine.cancel_task(task_id)
    await db.delete(task)
    await db.commit()


_FILE_PREVIEW_LINES = 300  # default max lines returned when no range is given


@router.get("/{task_id}/file")
async def get_task_file(
    task_id: uuid.UUID,
    path: str = Query(..., description="File path relative to repo root"),
    start: int | None = Query(None, description="Start line (1-based, inclusive)"),
    end: int | None = Query(None, description="End line (1-based, inclusive)"),
    db: AsyncSession = Depends(get_db),
):
    """Return a slice of a file from the task's repository.

    Used by the wiki source citation resolver to display referenced code.
    Path traversal is blocked — only files inside repo.local_path are served.
    """
    task = await db.get(AnalysisTask, task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    repo = await db.get(Repository, task.repository_id)
    if not repo or not repo.local_path:
        raise HTTPException(400, "Repository not synced")

    repo_root = Path(repo.local_path).resolve()

    # Try the path as-is first (relative to repo root)
    candidate = (repo_root / path.lstrip("/")).resolve()
    try:
        candidate.relative_to(repo_root)
    except ValueError:
        raise HTTPException(400, "Invalid path")

    # If not found directly, search by basename within the repo (citation may omit dirs)
    if not candidate.is_file():
        basename = os.path.basename(path)
        # Sort by depth (fewest path parts = shallowest) for deterministic results
        matches = sorted(repo_root.rglob(basename), key=lambda p: len(p.parts))
        if not matches:
            raise HTTPException(404, f"File not found: {path}")
        candidate = matches[0]

    try:
        all_lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines(
            keepends=True
        )
    except OSError as exc:
        raise HTTPException(500, f"Could not read file: {exc}")

    total = len(all_lines)
    if start is not None and end is not None:
        s = max(0, start - 1)
        e = min(total, end)
        content = "".join(all_lines[s:e])
        start_line = s + 1
        end_line = e
    else:
        content = "".join(all_lines[:_FILE_PREVIEW_LINES])
        start_line = 1
        end_line = min(total, _FILE_PREVIEW_LINES)

    return {
        "content": content,
        "startLine": start_line,
        "endLine": end_line,
        "totalLines": total,
        "actualPath": str(candidate.relative_to(repo_root)),
    }
