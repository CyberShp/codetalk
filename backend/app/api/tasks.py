import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.database import get_db

router = APIRouter(prefix="/api/tasks", tags=["任务管理"])


# --- Schemas ---

class TaskCreate(BaseModel):
    name: str
    repo_path: str
    tools: list[str] = ["gitnexus", "deepwiki"]
    requirements_doc: str | None = None
    design_doc: str | None = None


class TaskResponse(BaseModel):
    id: str
    name: str
    repo_path: str
    status: str
    tools: list[str]
    requirements_doc: str | None
    design_doc: str | None
    progress: int
    error_message: str | None
    created_at: str
    updated_at: str


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
           progress, error_message, created_at, updated_at)
           VALUES (?, ?, ?, 'pending', ?, ?, ?, 0, NULL, ?, ?)""",
        (task_id, data.name, data.repo_path, json.dumps(data.tools),
         data.requirements_doc, data.design_doc, now, now),
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
