import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.database import get_db

router = APIRouter(prefix="/api/tasks", tags=["任务管理"])
logger = logging.getLogger(__name__)


# --- Schemas ---

class TaskCreate(BaseModel):
    name: str
    repo_path: str
    tools: list[str] = ["gitnexus", "deepwiki"]
    requirements_doc: str | None = None
    design_doc: str | None = None
    analysis_focus: str = Field(min_length=1, max_length=4_000)
    prompt_content: str = Field(min_length=1, max_length=32_000)


class TaskResponse(BaseModel):
    id: str
    name: str
    repo_path: str
    status: str
    tools: list[str]
    requirements_doc: str | None
    design_doc: str | None
    analysis_focus: str | None
    prompt_content: str | None
    progress: int
    error_message: str | None
    created_at: str
    updated_at: str


class OutputFileInfo(BaseModel):
    filename: str
    size: int


def _row_to_task(row: aiosqlite.Row) -> dict:
    d = dict(row)
    d["tools"] = json.loads(d.get("tools") or "[]")
    return d


# --- Endpoints ---

@router.post("", response_model=TaskResponse, status_code=201)
async def create_task(data: TaskCreate, db: aiosqlite.Connection = Depends(get_db)):
    if not Path(data.repo_path).exists():
        raise HTTPException(status_code=422, detail=f"代码路径不存在：{data.repo_path}")

    now = datetime.now(timezone.utc).isoformat()
    task_id = str(uuid.uuid4())
    await db.execute(
        """INSERT INTO tasks (id, name, repo_path, status, tools, requirements_doc, design_doc,
           analysis_focus, prompt_content, progress, error_message, created_at, updated_at)
           VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, 0, NULL, ?, ?)""",
        (task_id, data.name, data.repo_path, json.dumps(data.tools),
         data.requirements_doc, data.design_doc,
         data.analysis_focus, data.prompt_content, now, now),
    )
    await db.commit()

    async with db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)) as cur:
        row = await cur.fetchone()
    return _row_to_task(row)


@router.get("", response_model=list[TaskResponse])
async def list_tasks(db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute("SELECT * FROM tasks ORDER BY created_at DESC") as cur:
        rows = await cur.fetchall()
    return [_row_to_task(r) for r in rows]


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str, db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _row_to_task(row)


@router.delete("/{task_id}", status_code=204)
async def delete_task(task_id: str, db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="任务不存在")
    await db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    await db.commit()


# --- Sprint 3: Pipeline execution endpoints ---

@router.post("/{task_id}/run")
async def run_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Trigger the analysis pipeline as a background task."""
    async with db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = dict(row)
    if task["status"] == "running":
        raise HTTPException(status_code=409, detail="任务正在运行中")

    # Health check: verify GitNexus is reachable before starting pipeline
    tools = json.loads(task.get("tools") or "[]")
    if "gitnexus" in tools:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5)) as hc_client:
                hc_resp = await hc_client.get(f"{settings.gitnexus_base_url}/api/health")
                hc_resp.raise_for_status()
        except Exception as exc:
            logger.warning("GitNexus health check failed: %s", exc)
            raise HTTPException(
                status_code=503,
                detail="GitNexus service is not available",
            )

    # Reset status
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE tasks SET status = 'running', progress = 0, error_message = NULL, "
        "updated_at = ? WHERE id = ?",
        (now, task_id),
    )
    await db.commit()

    # Launch pipeline in background
    from app.services.analysis_pipeline import AnalysisPipeline

    pipeline = AnalysisPipeline()
    background_tasks.add_task(pipeline.run, task_id)

    return {"task_id": task_id, "status": "running", "message": "分析管道已启动"}


@router.get("/{task_id}/output", response_model=list[OutputFileInfo])
async def list_output_files(task_id: str, db: aiosqlite.Connection = Depends(get_db)):
    """List output files for a completed task."""
    async with db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="任务不存在")

    output_dir = settings.outputs_path / task_id
    if not output_dir.exists():
        return []

    files: list[dict] = []
    for f in sorted(output_dir.iterdir()):
        if f.is_file():
            files.append({"filename": f.name, "size": f.stat().st_size})

    return files


@router.get("/{task_id}/output/{filename}")
async def read_output_file(
    task_id: str,
    filename: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Read a specific output file content."""
    async with db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="任务不存在")

    output_dir = settings.outputs_path / task_id
    filepath = output_dir / filename

    # Prevent path traversal
    try:
        filepath.resolve().relative_to(output_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="非法文件路径")

    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {filename}")

    content = filepath.read_text(encoding="utf-8")
    return {"filename": filename, "content": content}


@router.get("/{task_id}/debug", response_model=list[OutputFileInfo])
async def list_debug_files(task_id: str, db: aiosqlite.Connection = Depends(get_db)):
    """List LLM debug snapshot files for a task."""
    async with db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="任务不存在")

    debug_dir = settings.outputs_path / task_id / "debug"
    if not debug_dir.exists():
        return []

    files: list[dict] = []
    for f in sorted(debug_dir.iterdir()):
        if f.is_file():
            files.append({"filename": f.name, "size": f.stat().st_size})
    return files


@router.get("/{task_id}/debug/{filename}")
async def read_debug_file(
    task_id: str,
    filename: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Read a specific LLM debug snapshot file."""
    async with db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="任务不存在")

    debug_dir = settings.outputs_path / task_id / "debug"
    filepath = debug_dir / filename

    try:
        filepath.resolve().relative_to(debug_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="非法文件路径")

    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {filename}")

    content = filepath.read_text(encoding="utf-8")
    return {"filename": filename, "content": content}