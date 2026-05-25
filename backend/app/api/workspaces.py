import asyncio
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
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
    is_active: bool = True
    created_at: str


class ToggleMaterialBody(BaseModel):
    is_active: bool


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
    last_index_error: str | None = None
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

def _classify_index_error(exc: Exception, base_url: str) -> str:
    import httpx as _httpx
    if isinstance(exc, _httpx.ConnectError):
        return f"GitNexus 未启动或不可达（{base_url}）"
    if isinstance(exc, _httpx.TimeoutException):
        return "GitNexus 连接超时"
    msg = str(exc)
    if "timed out" in msg.lower():
        return "GitNexus 索引超时（>10分钟），请检查 GitNexus 日志"
    if "failed" in msg.lower():
        return f"GitNexus 索引失败：{msg}"
    return msg


async def _index_workspace(ws_id: str, repo_path: str) -> None:
    """Index a workspace repo via GitNexusAdapter; updates indexed: 0=running, 1=done, -1=failed."""
    from app.adapters.base import AnalysisRequest
    from app.adapters.gitnexus import GitNexusAdapter

    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute(
            "UPDATE workspaces SET indexed = 0, index_job = NULL, last_index_error = NULL, "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (ws_id,),
        )
        await db.commit()

    try:
        adapter = GitNexusAdapter(base_url=settings.gitnexus_base_url)

        # T3: 健康预检 — 避免等待 connect timeout 才报错
        health = await adapter.health_check()
        if not health.is_healthy:
            detail = health.last_check or health.container_status or "unreachable"
            error_msg = f"GitNexus 服务未运行，请先启动 GitNexus（{detail}）"
            async with aiosqlite.connect(settings.sqlite_db) as db:
                await db.execute(
                    "UPDATE workspaces SET indexed = -1, last_index_error = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (error_msg, ws_id),
                )
                await db.commit()
            logger.error("Workspace indexing skipped for %s: %s", ws_id, error_msg)
            return

        await adapter.prepare(AnalysisRequest(repo_local_path=repo_path))

        async with aiosqlite.connect(settings.sqlite_db) as db:
            await db.execute(
                "UPDATE workspaces SET indexed = 1, last_index_error = NULL, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (ws_id,),
            )
            await db.commit()
        logger.info("Workspace %s indexed successfully", ws_id)

    except Exception as exc:
        error_msg = _classify_index_error(exc, settings.gitnexus_base_url)
        logger.error("Workspace indexing failed for %s: %s", ws_id, error_msg)
        async with aiosqlite.connect(settings.sqlite_db) as db:
            await db.execute(
                "UPDATE workspaces SET indexed = -1, last_index_error = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (error_msg, ws_id),
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


@router.get("/{ws_id}/export")
async def export_workspace_reports(
    ws_id: str,
    format: str = Query(default="md", pattern="^(md|docx|xml)$"),
    db: aiosqlite.Connection = Depends(get_db),
):
    await _get_workspace_or_404(ws_id, db)
    from app.services.export_service import export_workspace_reports as _export
    try:
        data, filename, content_type = await _export(ws_id, format, db)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{ws_id}/chat/export")
async def export_workspace_chat(
    ws_id: str, db: aiosqlite.Connection = Depends(get_db)
):
    ws = await _get_workspace_or_404(ws_id, db)
    from app.services.export_service import export_workspace_chat as _export_chat
    try:
        data, filename, content_type = await _export_chat(ws_id, ws["name"], db)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
               (id, workspace_id, filename, content_type, file_path, is_active, created_at)
           VALUES (?, ?, ?, ?, ?, TRUE, ?)""",
        (mat_id, ws_id, file.filename or dest.name, content_type, str(dest), now),
    )
    await db.commit()

    result = {
        "id": mat_id,
        "workspace_id": ws_id,
        "filename": file.filename or dest.name,
        "content_type": content_type,
        "file_path": str(dest),
        "is_active": True,
        "created_at": now,
    }

    asyncio.create_task(_embed_material_background(mat_id, ws_id))

    return result


async def _embed_material_background(mat_id: str, ws_id: str) -> None:
    """Fire-and-forget embedding of a single material."""
    try:
        from app.services.material_rag import embed_material
        await embed_material(mat_id, ws_id)
    except Exception as exc:
        logger.warning("Background embedding failed for %s: %s", mat_id, exc)


@router.patch("/{ws_id}/materials/{mat_id}", response_model=WorkspaceMaterialResponse)
async def toggle_material(
    ws_id: str, mat_id: str, body: ToggleMaterialBody, db: aiosqlite.Connection = Depends(get_db)
):
    await _get_workspace_or_404(ws_id, db)
    async with db.execute(
        "SELECT * FROM workspace_materials WHERE id = ? AND workspace_id = ?",
        (mat_id, ws_id),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="材料不存在")
    await db.execute(
        "UPDATE workspace_materials SET is_active = ? WHERE id = ? AND workspace_id = ?",
        (body.is_active, mat_id, ws_id),
    )
    await db.commit()
    mat = dict(row)
    mat["is_active"] = body.is_active

    if body.is_active:
        asyncio.create_task(_embed_material_background(mat_id, ws_id))
    else:
        try:
            from app.services.material_rag import delete_material_chunks
            await delete_material_chunks(mat_id)
        except Exception as exc:
            logger.warning("Chunk cleanup failed for %s: %s", mat_id, exc)

    return mat


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

    try:
        from app.services.material_rag import delete_material_chunks
        await delete_material_chunks(mat_id)
    except Exception as exc:
        logger.warning("Chunk cleanup failed for %s: %s", mat_id, exc)

    file_path = Path(row["file_path"])
    if file_path.exists():
        file_path.unlink()

    await db.execute(
        "DELETE FROM workspace_materials WHERE id = ? AND workspace_id = ?",
        (mat_id, ws_id),
    )
    await db.commit()


@router.get("/{ws_id}/materials/embedding-status")
async def get_embedding_status(
    ws_id: str, db: aiosqlite.Connection = Depends(get_db)
):
    await _get_workspace_or_404(ws_id, db)

    async with db.execute(
        "SELECT value FROM settings WHERE key = 'active_embedding_model_id'"
    ) as cur:
        row = await cur.fetchone()
    active_model_id = row["value"] if row and row["value"] else None

    async with db.execute(
        "SELECT COUNT(*) as cnt FROM workspace_materials "
        "WHERE workspace_id = ? AND is_active = TRUE",
        (ws_id,),
    ) as cur:
        active_count = (await cur.fetchone())["cnt"]

    if active_model_id:
        async with db.execute(
            "SELECT COUNT(DISTINCT material_id) as cnt FROM material_chunks "
            "WHERE workspace_id = ? AND embedding_model_id = ?",
            (ws_id, active_model_id),
        ) as cur:
            embedded_count = (await cur.fetchone())["cnt"]

        async with db.execute(
            "SELECT COUNT(*) as cnt FROM material_chunks "
            "WHERE workspace_id = ? AND embedding_model_id = ?",
            (ws_id, active_model_id),
        ) as cur:
            chunk_count = (await cur.fetchone())["cnt"]
    else:
        embedded_count = 0
        chunk_count = 0

    return {
        "active_materials": active_count,
        "embedded_materials": embedded_count,
        "total_chunks": chunk_count,
        "rag_ready": active_model_id is not None and embedded_count > 0,
    }


@router.post("/{ws_id}/materials/embed")
async def trigger_embedding(
    ws_id: str, db: aiosqlite.Connection = Depends(get_db)
):
    await _get_workspace_or_404(ws_id, db)

    async def _run_embed() -> None:
        try:
            from app.services.material_rag import embed_workspace_materials
            total = await embed_workspace_materials(ws_id)
            logger.info("Workspace %s: embedded %d total chunks", ws_id, total)
        except Exception as exc:
            logger.error("Workspace embedding failed: %s", exc)

    asyncio.create_task(_run_embed())
    return {"status": "embedding_started"}


def _guess_content_type(filename: str) -> str:
    name_lower = filename.lower()
    if any(kw in name_lower for kw in ("req", "requirement", "需求")):
        return "requirements"
    if any(kw in name_lower for kw in ("design", "arch", "设计", "架构")):
        return "design"
    return "other"


# --- T9: Workspace chat endpoints ---

class WorkspaceChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    mode: str = Field(default="freeqa", pattern="^(targeted|freeqa)$")


class WorkspaceChatMessageResponse(BaseModel):
    id: str
    workspace_id: str
    mode: str
    role: str
    content: str
    created_at: str


@router.post("/{ws_id}/chat/stream")
async def workspace_chat_stream(
    ws_id: str,
    body: WorkspaceChatRequest,
    db: aiosqlite.Connection = Depends(get_db),
):
    ws = await _get_workspace_or_404(ws_id, db)

    # Fix 1: indexed gate — chat requires a fully indexed workspace
    if ws["indexed"] != 1:
        raise HTTPException(status_code=409, detail="工作空间尚未完成索引，请等待索引完成后再对话")

    try:
        from app.llm.factory import create_llm_client_from_active
        llm = await create_llm_client_from_active()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"LLM 不可用：{exc}")

    from app.services.workspace_chat import (
        build_chat_messages,
        persist_user_message,
        persist_assistant_reply,
    )

    # Fix 3a: build context first so _load_history() excludes this turn's user message,
    # then persist — message is still saved before streaming begins
    messages = await build_chat_messages(ws_id, ws["repo_path"], body.message, body.mode)

    try:
        await persist_user_message(ws_id, body.mode, body.message)
    except Exception as exc:
        logger.error("Failed to persist user message: %s", exc)

    ws_mode = body.mode

    async def _generate():
        chunks: list[str] = []
        had_error = False
        try:
            async for delta in llm.stream_complete(messages, max_tokens=min(2048, settings.llm_max_output_tokens), temperature=0.5):
                chunks.append(delta)
                yield f"data: {json.dumps({'content': delta, 'done': False}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            logger.error("Workspace chat stream error: %s", exc)
            had_error = True
            yield f"data: {json.dumps({'content': '', 'done': True, 'error': '生成失败，请重试'}, ensure_ascii=False)}\n\n"
        finally:
            # Fix 3b: persist assistant reply independently after stream
            reply = "".join(chunks)
            if reply:
                try:
                    await persist_assistant_reply(ws_id, ws_mode, reply)
                except Exception as exc:
                    logger.error("Failed to persist assistant reply: %s", exc)

        if not had_error:
            yield f"data: {json.dumps({'content': '', 'done': True}, ensure_ascii=False)}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")


@router.get("/{ws_id}/chat/history", response_model=list[WorkspaceChatMessageResponse])
async def workspace_chat_history(
    ws_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    db: aiosqlite.Connection = Depends(get_db),
):
    await _get_workspace_or_404(ws_id, db)
    # Fix 2: return most-recent N messages in chronological order
    async with db.execute(
        "SELECT id, workspace_id, mode, role, content, created_at FROM ("
        "  SELECT id, workspace_id, mode, role, content, created_at"
        "  FROM workspace_chats WHERE workspace_id = ?"
        "  ORDER BY created_at DESC LIMIT ?"
        ") ORDER BY created_at ASC",
        (ws_id, limit),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]
