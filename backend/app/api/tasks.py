import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.task import AnalysisTask, ToolRun
from app.schemas.task import TaskCreate, TaskDetailResponse, TaskResponse, ToolRunResponse
from app.services.task_engine import run_task

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    status: str | None = None,
    repository_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(AnalysisTask).order_by(AnalysisTask.created_at.desc())
    if status:
        query = query.where(AnalysisTask.status == status)
    if repository_id:
        query = query.where(AnalysisTask.repository_id == repository_id)
    result = await db.execute(query)
    return [TaskResponse.model_validate(t) for t in result.scalars().all()]


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
    asyncio.create_task(run_task(task.id))
    return TaskResponse.model_validate(task)


@router.get("/{task_id}", response_model=TaskDetailResponse)
async def get_task(task_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AnalysisTask).options(selectinload(AnalysisTask.tool_runs)).where(AnalysisTask.id == task_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskDetailResponse(
        **TaskResponse.model_validate(task).model_dump(),
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
