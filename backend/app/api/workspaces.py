import asyncio
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

_MATERIALS_ROOT = settings.data_path / "workspaces"


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


class WorkspaceReportListItem(BaseModel):
    """Report metadata only — no content. Used in workspace list/detail responses."""
    id: str
    workspace_id: str
    report_type: str
    title: str | None
    status: str
    created_at: str


class WorkspaceReportResponse(BaseModel):
    """Full report including content. Used in single-report fetch."""
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
    indexed: int
    index_job: str | None
    analyze_status: str | None
    analyze_progress: int
    created_at: str
    updated_at: str
    materials: list[WorkspaceMaterialResponse] = []
    reports: list[WorkspaceReportListItem] = []


def _row_to_workspace(row: aiosqlite.Row) -> dict:
    d = dict(row)
    d["indexed"] = int(d.get("indexed", 0))
    d["analyze_progress"] = int(d.get("analyze_progress", 0))
    return d


async def _get_workspace_or_404(ws_id: str, db: aiosqlite.Connection) -> dict:
    async with db.execute("SELECT * FROM workspaces WHERE id = ?", (ws_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"工作空间不存在：{ws_id}")
    return _row_to_workspace(row)


# --- Background task: T6 GitNexus indexing ---

async def _index_workspace(ws_id: str, repo_path: str) -> None:
    """Index a workspace repo via GitNexusAdapter; updates indexed: 0=running, 1=done, -1=failed."""
    from app.adapters.base import AnalysisRequest
    from app.adapters.gitnexus import GitNexusAdapter

    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute(
            "UPDATE workspaces SET indexed = 0, index_job = NULL, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (ws_id,),
        )
        await db.commit()

    try:
        adapter = GitNexusAdapter(base_url=settings.gitnexus_base_url)
        await adapter.prepare(AnalysisRequest(repo_local_path=repo_path))

        async with aiosqlite.connect(settings.sqlite_db) as db:
            await db.execute(
                "UPDATE workspaces SET indexed = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (ws_id,),
            )
            await db.commit()
        logger.info("Workspace %s indexed successfully", ws_id)

    except Exception as exc:
        logger.error("Workspace indexing failed for %s: %s", ws_id, exc)
        async with aiosqlite.connect(settings.sqlite_db) as db:
            await db.execute(
                "UPDATE workspaces SET indexed = -1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (ws_id,),
            )
            await db.commit()


async def _run_workspace_analysis(ws_id: str, repo_path: str) -> None:
    """Background task: run WorkspacePipeline, update analyze_status on failure."""
    from app.services.workspace_pipeline import WorkspacePipeline

    try:
        await WorkspacePipeline().run(ws_id, repo_path)
    except Exception as exc:
        logger.error("Workspace analysis failed for %s: %s", ws_id, exc)
        async with aiosqlite.connect(settings.sqlite_db) as db:
            await db.execute(
                "UPDATE workspaces SET analyze_status = 'failed', "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (ws_id,),
            )
            await db.commit()


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
    repo = Path(data.repo_path)
    if not repo.exists():
        raise HTTPException(status_code=422, detail=f"代码路径不存在：{data.repo_path}")
    if not repo.is_dir():
        raise HTTPException(status_code=422, detail=f"代码路径不是目录：{data.repo_path}")

    ws_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at)
           VALUES (?, ?, ?, 0, ?, ?)""",
        (ws_id, data.name, data.repo_path, now, now),
    )
    await db.commit()

    asyncio.create_task(_index_workspace(ws_id, data.repo_path))

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
        "SELECT id, workspace_id, report_type, title, status, created_at"
        " FROM workspace_reports WHERE workspace_id = ? ORDER BY created_at",
        (ws_id,),
    ) as cur:
        reports = [dict(r) for r in await cur.fetchall()]

    ws["materials"] = materials
    ws["reports"] = reports
    return ws


# --- T6: Index status endpoints ---

@router.get("/{ws_id}/index-status")
async def get_index_status(ws_id: str, db: aiosqlite.Connection = Depends(get_db)):
    ws = await _get_workspace_or_404(ws_id, db)
    return {"indexed": ws["indexed"], "index_job": ws.get("index_job")}


@router.post("/{ws_id}/reindex", status_code=202)
async def reindex_workspace(ws_id: str, db: aiosqlite.Connection = Depends(get_db)):
    ws = await _get_workspace_or_404(ws_id, db)
    asyncio.create_task(_index_workspace(ws_id, ws["repo_path"]))
    return {"status": "indexing", "message": "重新索引已启动"}


# --- T7: Analyze endpoints ---

@router.post("/{ws_id}/analyze", status_code=202)
async def analyze_workspace(ws_id: str, db: aiosqlite.Connection = Depends(get_db)):
    ws = await _get_workspace_or_404(ws_id, db)
    if ws["indexed"] != 1:
        raise HTTPException(status_code=409, detail="工作空间尚未完成索引，请等待索引完成后再生成报告")
    if ws.get("analyze_status") == "running":
        raise HTTPException(status_code=409, detail="报告生成正在进行中")

    await db.execute(
        "UPDATE workspaces SET analyze_status = 'running', analyze_progress = 0, "
        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (ws_id,),
    )
    await db.commit()

    asyncio.create_task(_run_workspace_analysis(ws_id, ws["repo_path"]))
    return {"status": "running", "message": "工作空间分析已启动"}


@router.get("/{ws_id}/analyze-status")
async def get_analyze_status(ws_id: str, db: aiosqlite.Connection = Depends(get_db)):
    ws = await _get_workspace_or_404(ws_id, db)

    # While running, relay live progress from the shadow task
    if ws.get("analyze_status") == "running":
        async with db.execute(
            "SELECT progress FROM tasks WHERE name = ?",
            (f"__ws_{ws_id}",),
        ) as cur:
            task_row = await cur.fetchone()
        if task_row:
            return {
                "analyze_status": "running",
                "analyze_progress": int(task_row["progress"]),
            }

    return {
        "analyze_status": ws.get("analyze_status"),
        "analyze_progress": ws.get("analyze_progress", 0),
    }


@router.get("/{ws_id}/reports/{report_id}", response_model=WorkspaceReportResponse)
async def get_report(
    ws_id: str, report_id: str, db: aiosqlite.Connection = Depends(get_db)
):
    await _get_workspace_or_404(ws_id, db)
    async with db.execute(
        "SELECT * FROM workspace_reports WHERE id = ? AND workspace_id = ?",
        (report_id, ws_id),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="报告不存在")
    return dict(row)


# --- Materials endpoints ---

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
