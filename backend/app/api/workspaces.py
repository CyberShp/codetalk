import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.config import settings
from app.database import get_db

router = APIRouter(prefix="/api/workspaces", tags=["工作空间"])
logger = logging.getLogger(__name__)

_MATERIALS_ROOT = Path(settings.output_dir) / "workspaces"


# --- Schemas ---

class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    repo_path: str = Field(min_length=1, max_length=1000)


class WorkspaceMaterialResponse(BaseModel):
    id: str
    workspace_id: str
    filename: str
    content_type: str
    file_path: str
    created_at: str


class WorkspaceReportResponse(BaseModel):
    id: str
    workspace_id: str
    report_type: str
    title: str | None
    content: str | None
    status: str
    created_at: str


class WorkspaceResponse(BaseModel):
    id: str
    name: str
    repo_path: str
    indexed: bool
    index_job: str | None
    created_at: str
    updated_at: str
    materials: list[WorkspaceMaterialResponse] = []
    reports: list[WorkspaceReportResponse] = []


def _row_to_workspace(row: aiosqlite.Row) -> dict:
    d = dict(row)
    d["indexed"] = bool(d.get("indexed", 0))
    return d


async def _get_workspace_or_404(ws_id: str, db: aiosqlite.Connection) -> dict:
    async with db.execute("SELECT * FROM workspaces WHERE id = ?", (ws_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"工作空间不存在：{ws_id}")
    return _row_to_workspace(row)


# --- Endpoints ---

@router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces(db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute(
        "SELECT * FROM workspaces ORDER BY updated_at DESC"
    ) as cur:
        rows = await cur.fetchall()
    return [_row_to_workspace(r) for r in rows]


@router.post("", response_model=WorkspaceResponse, status_code=201)
async def create_workspace(
    data: WorkspaceCreate, db: aiosqlite.Connection = Depends(get_db)
):
    if not Path(data.repo_path).exists():
        raise HTTPException(status_code=422, detail=f"代码路径不存在：{data.repo_path}")

    ws_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at)
           VALUES (?, ?, ?, 0, ?, ?)""",
        (ws_id, data.name, data.repo_path, now, now),
    )
    await db.commit()

    async with db.execute("SELECT * FROM workspaces WHERE id = ?", (ws_id,)) as cur:
        row = await cur.fetchone()
    return _row_to_workspace(row)


@router.get("/{ws_id}", response_model=WorkspaceResponse)
async def get_workspace(ws_id: str, db: aiosqlite.Connection = Depends(get_db)):
    ws = await _get_workspace_or_404(ws_id, db)

    async with db.execute(
        "SELECT * FROM workspace_materials WHERE workspace_id = ? ORDER BY created_at",
        (ws_id,),
    ) as cur:
        materials = [dict(r) for r in await cur.fetchall()]

    async with db.execute(
        "SELECT * FROM workspace_reports WHERE workspace_id = ? ORDER BY created_at",
        (ws_id,),
    ) as cur:
        reports = [dict(r) for r in await cur.fetchall()]

    ws["materials"] = materials
    ws["reports"] = reports
    return ws


@router.post(
    "/{ws_id}/materials",
    response_model=WorkspaceMaterialResponse,
    status_code=201,
)
async def upload_material(
    ws_id: str,
    file: UploadFile,
    db: aiosqlite.Connection = Depends(get_db),
):
    await _get_workspace_or_404(ws_id, db)

    mat_dir = _MATERIALS_ROOT / ws_id / "materials"
    mat_dir.mkdir(parents=True, exist_ok=True)

    mat_id = str(uuid.uuid4())
    suffix = Path(file.filename or "upload").suffix
    dest = mat_dir / f"{mat_id}{suffix}"
    with dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)

    now = datetime.now(timezone.utc).isoformat()
    content_type = _guess_content_type(file.filename or "")
    await db.execute(
        """INSERT INTO workspace_materials
               (id, workspace_id, filename, content_type, file_path, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (mat_id, ws_id, file.filename or dest.name, content_type, str(dest), now),
    )
    await db.commit()

    return {
        "id": mat_id,
        "workspace_id": ws_id,
        "filename": file.filename or dest.name,
        "content_type": content_type,
        "file_path": str(dest),
        "created_at": now,
    }


@router.delete("/{ws_id}/materials/{mat_id}", status_code=204)
async def delete_material(
    ws_id: str, mat_id: str, db: aiosqlite.Connection = Depends(get_db)
):
    await _get_workspace_or_404(ws_id, db)

    async with db.execute(
        "SELECT file_path FROM workspace_materials WHERE id = ? AND workspace_id = ?",
        (mat_id, ws_id),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="材料不存在")

    file_path = Path(row["file_path"])
    if file_path.exists():
        file_path.unlink()

    await db.execute(
        "DELETE FROM workspace_materials WHERE id = ? AND workspace_id = ?",
        (mat_id, ws_id),
    )
    await db.commit()


def _guess_content_type(filename: str) -> str:
    name_lower = filename.lower()
    if any(kw in name_lower for kw in ("req", "requirement", "需求")):
        return "requirements"
    if any(kw in name_lower for kw in ("design", "arch", "设计", "架构")):
        return "design"
    return "other"
