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

# In-memory progress tracking keyed by repo_id; survives only within a process lifetime.
# On startup, database.py resets any DB rows stuck in 'running' to 'failed', so the two
# sources of truth (in-memory and DB) are always consistent after restart.
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


def _extract_pages(wiki_data_raw: str | None) -> list[dict[str, Any]]:
    """Normalize wiki_data JSON to a stable page list.

    Handles two shapes returned by different DeepWiki versions:
    - {"pages": [...]}  — from adapter's get_wiki_structure direct response
    - {"generated_pages": {id: {...}}}  — from legacy DeepWiki wiki.py flow
    """
    if not wiki_data_raw:
        return []
    try:
        wiki = json.loads(wiki_data_raw)
    except (json.JSONDecodeError, TypeError):
        return []

    if "pages" in wiki and isinstance(wiki["pages"], list):
        return wiki["pages"]

    generated = wiki.get("generated_pages", {})
    if isinstance(generated, dict) and generated:
        return [
            {
                "id": page_data.get("id", page_id),
                "title": page_data.get("title", page_id),
                "content": page_data.get("content", ""),
                "filePaths": page_data.get("filePaths", []),
                "importance": page_data.get("importance", "medium"),
                "relatedPages": page_data.get("relatedPages", []),
            }
            for page_id, page_data in generated.items()
        ]

    return []


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
    repo_path = Path(data.repo_path)
    if not repo_path.exists():
        raise HTTPException(
            status_code=422, detail=f"路径不存在：{data.repo_path}"
        )
    if not repo_path.is_dir():
        raise HTTPException(
            status_code=422, detail=f"路径不是目录：{data.repo_path}"
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
    """Get repo metadata (wiki_data excluded — use /pages and /pages/{index} for content)."""
    repo = await _get_repo_or_404(repo_id, db)
    repo.pop("wiki_data", None)
    return repo


@router.get("/repos/{repo_id}/pages")
async def list_pages(repo_id: str, db: aiosqlite.Connection = Depends(get_db)):
    """List all pages for a repo (id + title only, no heavy content)."""
    repo = await _get_repo_or_404(repo_id, db)
    pages = _extract_pages(repo.get("wiki_data"))
    return [{"id": p["id"], "title": p["title"]} for p in pages]


@router.get("/repos/{repo_id}/pages/{page_index}")
async def get_page(
    repo_id: str, page_index: int, db: aiosqlite.Connection = Depends(get_db)
):
    """Get a single page by zero-based index."""
    repo = await _get_repo_or_404(repo_id, db)
    pages = _extract_pages(repo.get("wiki_data"))
    if page_index < 0 or page_index >= len(pages):
        raise HTTPException(status_code=404, detail="页面不存在")
    return pages[page_index]


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

    async def _update_progress(pct: int) -> None:
        _generation_status[repo_id] = {"running": True, "progress": pct, "error": None}
        async with aiosqlite.connect(settings.sqlite_db) as db2:
            db2.row_factory = aiosqlite.Row
            await db2.execute(
                "UPDATE deepwiki_repos SET progress = ?, updated_at = ? WHERE id = ?",
                (pct, datetime.now(timezone.utc).isoformat(), repo_id),
            )
            await db2.commit()

    async def _run() -> None:
        client = DeepWikiClient()
        try:
            await _update_progress(20)
            wiki_data = await client.get_wiki_structure(repo_path)
            await _update_progress(90)
            pages = _extract_pages(json.dumps(wiki_data))
            page_count = len(pages)
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
                    "UPDATE deepwiki_repos SET status = 'failed', progress = 0, updated_at = ?"
                    " WHERE id = ?",
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
    """Get live generation progress for a repo.

    Returns in-memory status (updated during the active run) merged with DB status
    as fallback, so the caller always gets a consistent answer even after restart.
    """
    repo = await _get_repo_or_404(repo_id, db)
    mem = _generation_status.get(repo_id)
    if mem:
        return mem
    return {
        "running": repo["status"] == "running",
        "progress": repo["progress"],
        "error": None,
    }
