import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.database import get_db

router = APIRouter(prefix="/api/tasks", tags=["任务管理"])
logger = logging.getLogger(__name__)


# --- Schemas ---

class TaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    repo_path: str = Field(min_length=1, max_length=1000)
    tools: list[str] = Field(default=["gitnexus", "deepwiki"], max_length=10)
    requirements_doc: str | None = None
    design_doc: str | None = None
    analysis_focus: str = Field(min_length=1, max_length=4_000)
    prompt_content: str = Field(min_length=1, max_length=32_000)
    deepwiki_depth: str = Field(default="balanced", pattern="^(fast|balanced|deep)$")


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
    deepwiki_depth: str | None
    material_ids: list[str]
    progress: int
    error_message: str | None
    current_step: str | None
    created_at: str
    updated_at: str


class OutputFileInfo(BaseModel):
    filename: str
    size: int


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)


class ChatMessageResponse(BaseModel):
    id: int
    task_id: str
    role: str
    content: str
    created_at: str


def _row_to_task(row: aiosqlite.Row) -> dict:
    d = dict(row)
    d["tools"] = json.loads(d.get("tools") or "[]")
    d["material_ids"] = json.loads(d.get("material_ids") or "[]")
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
           analysis_focus, prompt_content, deepwiki_depth,
           progress, error_message, created_at, updated_at)
           VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)""",
        (task_id, data.name, data.repo_path, json.dumps(data.tools),
         data.requirements_doc, data.design_doc,
         data.analysis_focus, data.prompt_content, data.deepwiki_depth, now, now),
    )
    await db.commit()
    logger.info("Task created: id=%s, name=%s", task_id, data.name)

    async with db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)) as cur:
        row = await cur.fetchone()
    return _row_to_task(row)


@router.get("", response_model=list[TaskResponse])
async def list_tasks(db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute(
        "SELECT * FROM tasks WHERE name NOT LIKE '__ws_%' ORDER BY created_at DESC"
    ) as cur:
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
    logger.info("Task deleted: id=%s", task_id)


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

    # Health check: verify selected tools are reachable before starting pipeline
    tools = json.loads(task.get("tools") or "[]")
    if "gitnexus" in tools:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(settings.health_check_timeout), trust_env=False) as hc_client:
                hc_resp = await hc_client.get(f"{settings.gitnexus_base_url}/api/info")
                if hc_resp.status_code >= 500:  # pragma: no cover
                    hc_resp.raise_for_status()
        except Exception as exc:
            logger.warning("GitNexus health check failed: %s", exc)
            raise HTTPException(
                status_code=503,
                detail="GitNexus service is not available",
            )

    if "deepwiki" in tools:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(settings.health_check_timeout), trust_env=False) as hc_client:
                hc_resp = await hc_client.get(f"{settings.deepwiki_api_url}/health")
                hc_resp.raise_for_status()  # pragma: no cover
        except Exception as exc:
            logger.warning("DeepWiki health check failed: %s", exc)
            raise HTTPException(
                status_code=503,
                detail="DeepWiki service is not available",
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
    try:
        for f in sorted(output_dir.iterdir()):
            if f.is_file() and f.suffix == ".md":
                files.append({"filename": f.name, "size": f.stat().st_size})
    except OSError:  # pragma: no cover
        logger.exception("Failed to list output dir: %s", output_dir)
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
    except ValueError:  # pragma: no cover
        raise HTTPException(status_code=400, detail="非法文件路径")

    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {filename}")

    content = await asyncio.to_thread(filepath.read_text, "utf-8")
    return {"filename": filename, "content": content}


# --- Chat endpoints ---

@router.get("/{task_id}/chat", response_model=list[ChatMessageResponse])
async def get_chat_history(task_id: str, db: aiosqlite.Connection = Depends(get_db)):
    """Return the full chat history for a task."""
    async with db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="任务不存在")
    async with db.execute(
        "SELECT id, task_id, role, content, created_at FROM task_chats "
        "WHERE task_id = ? ORDER BY id ASC",
        (task_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("/{task_id}/chat")
async def send_chat_message(
    task_id: str,
    body: ChatRequest,
    db: aiosqlite.Connection = Depends(get_db),
):
    """Send a user message and stream back an AI reply via SSE."""
    async with db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = dict(row)

    # Load report files for context (cap each file at 3000 chars)
    output_dir = settings.outputs_path / task_id
    report_context = ""
    md_files: list[Path] = []
    if output_dir.exists():
        for f in sorted(output_dir.iterdir()):
            if f.is_file() and f.suffix == ".md":
                md_files.append(f)
                try:
                    text = await asyncio.to_thread(f.read_text, "utf-8")
                    report_context += f"\n\n## {f.name}\n{text[:3000]}"
                except OSError:  # pragma: no cover
                    pass

    # Guard: refuse chat when no reports exist yet
    if not md_files:
        raise HTTPException(status_code=400, detail="该任务尚无分析报告，无法进行追问")

    # Load prior history for multi-turn
    async with db.execute(
        "SELECT role, content FROM task_chats WHERE task_id = ? ORDER BY id ASC",
        (task_id,),
    ) as cur:
        history_rows = await cur.fetchall()

    # Persist user message before starting stream
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO task_chats (task_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (task_id, "user", body.message, now),
    )
    await db.commit()

    # Build LLM message list
    system_prompt = (
        f"你是 CodeTalk 代码分析助手。当前任务：「{task['name']}」，"
        f"代码仓库：{task['repo_path']}。"
        "以下是对该仓库的分析报告，请根据报告内容回答用户的问题，"
        "如报告中没有相关信息请如实说明。"
        f"{report_context}"
    )
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    for h in history_rows:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": body.message})

    # Acquire LLM client before streaming (fail fast with a proper HTTP error)
    try:
        from app.llm.factory import create_llm_client_from_active
        llm = await create_llm_client_from_active()
    except Exception as exc:
        logger.error("Failed to get LLM client for chat: %s", exc)
        raise HTTPException(status_code=503, detail=f"LLM 不可用：{exc}")

    db_path = settings.sqlite_db

    async def _generate():
        chunks: list[str] = []
        had_error = False
        try:
            async for delta in llm.stream_complete(messages, max_tokens=min(2048, settings.llm_max_output_tokens), temperature=0.5):
                chunks.append(delta)
                yield f"data: {json.dumps({'content': delta, 'done': False}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            logger.error("Chat stream error: %s", exc)
            had_error = True
            yield f"data: {json.dumps({'content': '', 'done': True, 'error': '生成失败，请重试'}, ensure_ascii=False)}\n\n"
        finally:
            reply = "".join(chunks)
            if had_error:
                persist_content = (reply + "\n\n⚠️ 生成失败（响应不完整）") if reply else "⚠️ 生成失败，请重试"
            else:
                persist_content = reply or None
            if persist_content:
                try:
                    async with aiosqlite.connect(db_path) as own_db:
                        now2 = datetime.now(timezone.utc).isoformat()
                        await own_db.execute(
                            "INSERT INTO task_chats (task_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                            (task_id, "assistant", persist_content, now2),
                        )
                        await own_db.commit()
                except Exception as db_exc:
                    logger.error("Failed to persist assistant reply: %s", db_exc)

        if not had_error:
            yield f"data: {json.dumps({'content': '', 'done': True}, ensure_ascii=False)}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")


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
    try:
        for f in sorted(debug_dir.iterdir()):
            if f.is_file():
                files.append({"filename": f.name, "size": f.stat().st_size})
    except OSError:  # pragma: no cover
        logger.exception("Failed to list debug dir: %s", debug_dir)
    return files


@router.get("/{task_id}/steps")
async def get_task_steps(task_id: str, db: aiosqlite.Connection = Depends(get_db)):
    """Return step-log entries for a running/completed task (from steps.jsonl)."""
    async with db.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="任务不存在")

    step_file = settings.outputs_path / task_id / "steps.jsonl"
    if not step_file.exists():
        return []

    try:
        content = await asyncio.to_thread(step_file.read_text, "utf-8")
        return [json.loads(line) for line in content.splitlines() if line.strip()]
    except Exception:
        logger.exception("Failed to read steps file for task %s", task_id)
        return []


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
    except ValueError:  # pragma: no cover
        raise HTTPException(status_code=400, detail="非法文件路径")

    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {filename}")

    content = await asyncio.to_thread(filepath.read_text, "utf-8")
    return {"filename": filename, "content": content}