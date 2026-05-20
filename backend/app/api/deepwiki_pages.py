"""DeepWiki independent subsystem API — repo registry + wiki generation.

Prefix: /api/deepwiki
Does NOT conflict with the legacy /api/tasks/{id}/wiki endpoints in wiki.py.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.database import get_db
from app.adapters.deepwiki import DeepWikiClient

router = APIRouter(prefix="/api/deepwiki", tags=["DeepWiki"])
logger = logging.getLogger(__name__)

# In-memory progress tracking keyed by repo_id
_generation_status: dict[str, dict[str, Any]] = {}


# ── Schemas ──

class DeepWikiRepoCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    repo_path: str = Field(min_length=1, max_length=1000)


# ── Helpers ──

def _row_to_repo(row: aiosqlite.Row) -> dict[str, Any]:
    return dict(row)


async def _get_repo_or_404(repo_id: str, db: aiosqlite.Connection) -> dict[str, Any]:
    async with db.execute(
        "SELECT * FROM deepwiki_repos WHERE id = ?", (repo_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"DeepWiki 仓库不存在：{repo_id}")
    return _row_to_repo(row)


# ── Endpoints ──

@router.get("/repos")
async def list_repos(db: aiosqlite.Connection = Depends(get_db)):
    """List all registered DeepWiki repos (wiki_data excluded for brevity)."""
    async with db.execute(
        "SELECT id, repo_path, name, page_count, status, progress, created_at, updated_at"
        " FROM deepwiki_repos ORDER BY updated_at DESC"
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("/repos", status_code=201)
async def create_repo(
    data: DeepWikiRepoCreate, db: aiosqlite.Connection = Depends(get_db)
):
    """Register a local repo path for DeepWiki indexing."""
    if not Path(data.repo_path).exists():
        raise HTTPException(
            status_code=422, detail=f"仓库路径不存在：{data.repo_path}"
        )

    repo_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    try:
        await db.execute(
            """INSERT INTO deepwiki_repos
                   (id, repo_path, name, page_count, status, progress, created_at, updated_at)
               VALUES (?, ?, ?, 0, 'pending', 0, ?, ?)""",
            (repo_id, data.repo_path, data.name, now, now),
        )
        await db.commit()
    except aiosqlite.IntegrityError:
        raise HTTPException(
            status_code=409, detail=f"该路径已注册：{data.repo_path}"
        )

    async with db.execute(
        "SELECT * FROM deepwiki_repos WHERE id = ?", (repo_id,)
    ) as cur:
        row = await cur.fetchone()
    return _row_to_repo(row)


@router.get("/repos/{repo_id}")
async def get_repo(repo_id: str, db: aiosqlite.Connection = Depends(get_db)):
    """Get repo detail including wiki_data (parsed page list)."""
    repo = await _get_repo_or_404(repo_id, db)

    raw = repo.get("wiki_data")
    if raw:
        try:
            wiki = json.loads(raw)
            repo["pages"] = wiki.get("pages", [])
        except (json.JSONDecodeError, TypeError):
            repo["pages"] = []
    else:
        repo["pages"] = []

    return repo


@router.post("/repos/{repo_id}/generate")
async def generate_wiki(repo_id: str, db: aiosqlite.Connection = Depends(get_db)):
    """Trigger wiki generation in background. Returns immediately."""
    repo = await _get_repo_or_404(repo_id, db)

    if repo["status"] == "running":
        raise HTTPException(status_code=409, detail="Wiki 生成正在进行中")

    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE deepwiki_repos SET status = 'running', progress = 0, updated_at = ? WHERE id = ?",
        (now, repo_id),
    )
    await db.commit()

    _generation_status[repo_id] = {"running": True, "progress": 0, "error": None}

    repo_path = repo["repo_path"]

    async def _run() -> None:
        client = DeepWikiClient()
        try:
            wiki_data = await client.get_wiki_structure(repo_path)
            pages = wiki_data.get("pages", [])
            page_count = len(pages) if isinstance(pages, list) else 0
            done_at = datetime.now(timezone.utc).isoformat()
            async with aiosqlite.connect(settings.sqlite_db) as db2:
                db2.row_factory = aiosqlite.Row
                await db2.execute(
                    """UPDATE deepwiki_repos
                       SET status = 'completed', progress = 100, page_count = ?,
                           wiki_data = ?, updated_at = ?
                       WHERE id = ?""",
                    (page_count, json.dumps(wiki_data), done_at, repo_id),
                )
                await db2.commit()
            _generation_status[repo_id] = {
                "running": False, "progress": 100, "error": None
            }
            logger.info(
                "DeepWiki generation completed for %s (%d pages)", repo_id, page_count
            )
        except Exception as exc:
            error_at = datetime.now(timezone.utc).isoformat()
            logger.error("DeepWiki generation failed for %s: %s", repo_id, exc)
            async with aiosqlite.connect(settings.sqlite_db) as db2:
                db2.row_factory = aiosqlite.Row
                await db2.execute(
                    "UPDATE deepwiki_repos SET status = 'failed', updated_at = ? WHERE id = ?",
                    (error_at, repo_id),
                )
                await db2.commit()
            _generation_status[repo_id] = {
                "running": False, "progress": 0, "error": str(exc)
            }
        finally:
            await client.close()

    asyncio.create_task(_run())
    return {"status": "started", "message": "Wiki 生成已在后台启动"}


@router.get("/repos/{repo_id}/status")
async def get_status(repo_id: str, db: aiosqlite.Connection = Depends(get_db)):
    """Get live generation progress for a repo."""
    await _get_repo_or_404(repo_id, db)
    status = _generation_status.get(repo_id)
    if not status:
        return {"running": False, "progress": 0, "error": None}
    return status
