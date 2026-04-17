"""Repository-level wiki endpoints.

Same logic as task-scoped wiki.py, but keyed by repo_id directly.
No task lookup needed.
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

from app.database import get_db
from app.models.repository import Repository
from app.models.wiki_cache_meta import WikiCacheMeta
from app.api.wiki import (
    _orchestrator,
    _generation_status,
    _check_staleness,
    _cache_owner_repo,
    _get_llm_options,
    WikiGenerateRequest,
    WikiExportRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/repos", tags=["repo-wiki"])


@router.get("/{repo_id}/wiki")
async def get_repo_wiki(repo_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get wiki for a repository directly."""
    repo = await db.get(Repository, repo_id)
    if not repo or not repo.local_path:
        raise HTTPException(400, "Repository not synced")

    owner, repo_name = _cache_owner_repo(repo)
    cached = await _orchestrator.get_cached_wiki(owner=owner, repo=repo_name, language="zh")

    if not cached:
        return {"status": "not_generated", "wiki": None, "stale": False}

    # Staleness check
    result = await db.execute(
        select(WikiCacheMeta).where(WikiCacheMeta.repository_id == repo.id)
    )
    meta = result.scalar_one_or_none()
    stale = _check_staleness(meta, repo) if meta else True

    return {"status": "ready", "wiki": cached, "stale": stale}


@router.post("/{repo_id}/wiki/generate")
async def generate_repo_wiki(
    repo_id: uuid.UUID, body: WikiGenerateRequest, db: AsyncSession = Depends(get_db)
):
    """Trigger wiki generation for repo directly."""
    repo = await db.get(Repository, repo_id)
    if not repo or not repo.local_path:
        raise HTTPException(400, "Repository not synced")

    # Concurrency guard is repo-level
    repo_key = str(repo_id)
    if repo_key in _generation_status and _generation_status[repo_key].get("running"):
        raise HTTPException(409, "Wiki generation already in progress for this repository")

    owner, repo_name = _cache_owner_repo(repo)
    language = "zh"
    llm_opts = await _get_llm_options(db)
    provider = llm_opts.get("provider", "openai")
    model = llm_opts.get("model", "gpt-4o")
    proxy_mode = llm_opts.get("proxy_mode", "system")
    repo_local_path = repo.local_path
    repo_branch = repo.branch
    repo_last_indexed = repo.last_indexed_at

    # Release DB before background work
    await db.close()

    if body.force_refresh:
        await _orchestrator.delete_cache(owner=owner, repo=repo_name, language=language)

    _generation_status[repo_key] = {
        "running": True,
        "current": 0,
        "total": 0,
        "page_title": "",
        "error": None,
    }

    import asyncio

    async def _run():
        try:
            async def on_progress(current, total, page_title):
                _generation_status[repo_key].update(
                    current=current, total=total, page_title=page_title
                )

            await _orchestrator.generate_wiki(
                repo_local_path=repo_local_path,
                owner=owner,
                repo=repo_name,
                language=language,
                provider=provider,
                model=model,
                comprehensive=body.comprehensive,
                proxy_mode=proxy_mode,
                on_progress=on_progress,
            )

            # Save cache meta
            from app.database import async_session

            async with async_session() as db2:
                existing = await db2.execute(
                    select(WikiCacheMeta).where(WikiCacheMeta.repository_id == repo_id)
                )
                meta = existing.scalar_one_or_none()
                if meta:
                    meta.branch = repo_branch
                    meta.last_indexed_at = repo_last_indexed
                    meta.wiki_type = "comprehensive" if body.comprehensive else "concise"
                    meta.language = language
                    meta.generated_at = datetime.now(timezone.utc)
                else:
                    meta = WikiCacheMeta(
                        repository_id=repo_id,
                        branch=repo_branch,
                        last_indexed_at=repo_last_indexed,
                        wiki_type="comprehensive" if body.comprehensive else "concise",
                        language=language,
                    )
                    db2.add(meta)
                await db2.commit()

            _generation_status[repo_key]["running"] = False
            logger.info("Wiki generation completed for repo %s", repo_id)

        except Exception as exc:
            logger.exception("Wiki generation failed for repo %s", repo_id)
            _generation_status[repo_key]["running"] = False
            _generation_status[repo_key]["error"] = str(exc)

    asyncio.create_task(_run())

    return {"status": "started", "message": "Wiki generation started in background"}


@router.get("/{repo_id}/wiki/status")
async def repo_wiki_status(repo_id: uuid.UUID):
    """Wiki generation progress, keyed by repo_id."""
    status = _generation_status.get(str(repo_id))
    if not status:
        return {"running": False, "current": 0, "total": 0, "page_title": "", "error": None}
    return status


@router.delete("/{repo_id}/wiki/cache")
async def delete_repo_wiki_cache(repo_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Delete wiki cache for repo."""
    repo = await db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(400, "Repository not found")

    owner, repo_name = _cache_owner_repo(repo)

    # Delete deepwiki cache
    await _orchestrator.delete_cache(owner=owner, repo=repo_name)

    # Delete local meta
    result = await db.execute(
        select(WikiCacheMeta).where(WikiCacheMeta.repository_id == repo.id)
    )
    meta = result.scalar_one_or_none()
    if meta:
        await db.delete(meta)
        await db.commit()

    return {"status": "deleted"}


@router.post("/{repo_id}/wiki/export")
async def export_repo_wiki(
    repo_id: uuid.UUID, body: WikiExportRequest, db: AsyncSession = Depends(get_db)
):
    """Export wiki for repo."""
    repo = await db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(400, "Repository not found")

    owner, repo_name = _cache_owner_repo(repo)

    # First get the cached wiki to extract pages
    cached = await _orchestrator.get_cached_wiki(owner=owner, repo=repo_name)
    if not cached:
        raise HTTPException(404, "No wiki generated yet")

    pages = []
    generated = cached.get("generated_pages", {})
    for page_id, page_data in generated.items():
        pages.append(
            {
                "id": page_data.get("id", page_id),
                "title": page_data.get("title", ""),
                "content": page_data.get("content", ""),
                "filePaths": page_data.get("filePaths", []),
                "importance": page_data.get("importance", "medium"),
                "relatedPages": page_data.get("relatedPages", []),
            }
        )

    export_payload = {
        "repo_url": f"local/{owner}/{repo_name}",
        "pages": pages,
        "format": body.format,
    }

    import httpx

    async with httpx.AsyncClient(
        base_url=settings.deepwiki_base_url,
        timeout=httpx.Timeout(60, connect=10),
    ) as client:
        resp = await client.post("/export/wiki", json=export_payload)
        if resp.status_code != 200:
            raise HTTPException(502, f"deepwiki export failed: HTTP {resp.status_code}")

        content_type = resp.headers.get("content-type", "application/octet-stream")
        filename = f"wiki.{'md' if body.format == 'markdown' else 'json'}"

        return StreamingResponse(
            iter([resp.content]),
            media_type=content_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
