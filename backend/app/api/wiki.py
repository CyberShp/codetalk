"""Wiki endpoints — multi-page wiki generation + cache management.

IRON LAW: These endpoints only orchestrate HTTP calls to deepwiki.
No analysis logic, no code parsing, no graph building.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.llm_config import LLMConfig
from app.models.repository import Repository
from app.models.task import AnalysisTask
from app.models.wiki_cache_meta import WikiCacheMeta
from app.services.wiki_orchestrator import WikiOrchestrator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["wiki"])

_orchestrator = WikiOrchestrator(base_url=settings.deepwiki_base_url)

# ── In-memory generation status tracking (keyed by repository_id, not task_id) ──
# Wiki cache is a repo-level resource. The lock must be repo-level to prevent
# concurrent generation from different tasks writing the same deepwiki cache.
_generation_status: dict[str, dict] = {}  # key = str(repository_id)


class WikiGenerateRequest(BaseModel):
    comprehensive: bool = True
    force_refresh: bool = False


class WikiExportRequest(BaseModel):
    format: str = "markdown"  # markdown | json


# ── Helpers ──

def _cache_owner_repo(repo: Repository) -> tuple[str, str]:
    """Map Repository to deepwiki cache key components."""
    return "local", str(repo.id)


async def _get_llm_options(db: AsyncSession) -> dict:
    """Resolve LLM config, same logic as task_engine._build_options."""
    result = await db.execute(
        select(LLMConfig).where(LLMConfig.is_default.is_(True)).limit(1)
    )
    llm_config = result.scalar_one_or_none()
    if not llm_config:
        result = await db.execute(
            select(LLMConfig).order_by(LLMConfig.created_at.desc()).limit(1)
        )
        llm_config = result.scalar_one_or_none()

    if not llm_config:
        return {}

    provider = llm_config.provider
    if provider == "custom":
        provider = "openai"
    return {
        "provider": provider,
        "model": llm_config.model_name,
        "proxy_mode": llm_config.proxy_mode,
    }


def _check_staleness(
    meta: WikiCacheMeta,
    repo: Repository,
) -> bool:
    """Returns True if the cached wiki is stale (repo changed since generation).

    Only checks branch and last_indexed_at — these indicate the repo data has changed.
    wiki_type and language are user preferences, not freshness indicators.
    """
    if meta.branch != repo.branch:
        return True
    # Compare last_indexed_at — if repo has been re-synced since wiki was generated
    if repo.last_indexed_at and meta.last_indexed_at:
        if repo.last_indexed_at > meta.last_indexed_at:
            return True
    elif repo.last_indexed_at and not meta.last_indexed_at:
        return True
    return False


# ── Endpoints ──

@router.get("/{task_id}/wiki")
async def get_wiki(task_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get wiki for task's repository. Returns cached wiki + staleness flag."""
    task = await db.get(AnalysisTask, task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    repo = await db.get(Repository, task.repository_id)
    if not repo or not repo.local_path:
        raise HTTPException(400, "Repository not synced")

    owner, repo_name = _cache_owner_repo(repo)
    language = "zh"

    # Check deepwiki cache
    cached = await _orchestrator.get_cached_wiki(
        owner=owner, repo=repo_name, language=language
    )
    if not cached:
        return {"status": "not_generated", "wiki": None, "stale": False}

    # Check staleness via local metadata
    result = await db.execute(
        select(WikiCacheMeta).where(
            WikiCacheMeta.repository_id == repo.id
        )
    )
    meta = result.scalar_one_or_none()

    stale = False
    if meta:
        stale = _check_staleness(meta, repo)
    else:
        # No meta = we don't know provenance, treat as potentially stale
        stale = True

    return {
        "status": "ready",
        "wiki": cached,
        "stale": stale,
    }


@router.post("/{task_id}/wiki/generate")
async def generate_wiki(
    task_id: uuid.UUID,
    body: WikiGenerateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Trigger wiki generation. Returns immediately, runs in background."""
    task = await db.get(AnalysisTask, task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    repo = await db.get(Repository, task.repository_id)
    if not repo or not repo.local_path:
        raise HTTPException(400, "Repository not synced")

    # Concurrency guard is repo-level: wiki cache + meta are per-repo, not per-task.
    repo_key = str(repo.id)
    if repo_key in _generation_status and _generation_status[repo_key].get("running"):
        raise HTTPException(409, "Wiki generation already in progress for this repository")

    owner, repo_name = _cache_owner_repo(repo)
    language = "zh"
    llm_opts = await _get_llm_options(db)
    provider = llm_opts.get("provider", "openai")
    model = llm_opts.get("model", "gpt-4o")
    proxy_mode = llm_opts.get("proxy_mode", "system")
    repo_local_path = repo.local_path
    repo_id = repo.id
    repo_branch = repo.branch
    repo_last_indexed = repo.last_indexed_at

    # Release DB before background work
    await db.close()

    if body.force_refresh:
        await _orchestrator.delete_cache(
            owner=owner, repo=repo_name, language=language
        )

    _generation_status[repo_key] = {
        "running": True,
        "current": 0,
        "total": 0,
        "page_title": "",
        "error": None,
    }

    async def _run():
        try:
            async def on_progress(current, total, page_title):
                _generation_status[repo_key].update(
                    current=current, total=total, page_title=page_title
                )

            result = await _orchestrator.generate_wiki(
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
                    select(WikiCacheMeta).where(
                        WikiCacheMeta.repository_id == repo_id
                    )
                )
                meta = existing.scalar_one_or_none()
                if meta:
                    meta.branch = repo_branch
                    meta.last_indexed_at = repo_last_indexed
                    meta.wiki_type = (
                        "comprehensive" if body.comprehensive else "concise"
                    )
                    meta.language = language
                    meta.generated_at = datetime.now(timezone.utc)
                else:
                    meta = WikiCacheMeta(
                        repository_id=repo_id,
                        branch=repo_branch,
                        last_indexed_at=repo_last_indexed,
                        wiki_type=(
                            "comprehensive" if body.comprehensive else "concise"
                        ),
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


@router.get("/{task_id}/wiki/status")
async def wiki_status(task_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get wiki generation progress (keyed by repo, resolved from task)."""
    task = await db.get(AnalysisTask, task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    repo_key = str(task.repository_id)
    status = _generation_status.get(repo_key)
    if not status:
        return {"running": False, "current": 0, "total": 0, "page_title": "", "error": None}
    return status


@router.delete("/{task_id}/wiki/cache")
async def delete_wiki_cache(
    task_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    """Delete wiki cache for the task's repository."""
    task = await db.get(AnalysisTask, task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    repo = await db.get(Repository, task.repository_id)
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


@router.post("/{task_id}/wiki/export")
async def export_wiki(
    task_id: uuid.UUID,
    body: WikiExportRequest,
    db: AsyncSession = Depends(get_db),
):
    """Export wiki via deepwiki's /export/wiki endpoint."""
    task = await db.get(AnalysisTask, task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    repo = await db.get(Repository, task.repository_id)
    if not repo:
        raise HTTPException(400, "Repository not found")

    owner, repo_name = _cache_owner_repo(repo)

    # First get the cached wiki to extract pages
    cached = await _orchestrator.get_cached_wiki(
        owner=owner, repo=repo_name
    )
    if not cached:
        raise HTTPException(404, "No wiki generated yet")

    pages = []
    generated = cached.get("generated_pages", {})
    for page_id, page_data in generated.items():
        pages.append({
            "id": page_data.get("id", page_id),
            "title": page_data.get("title", ""),
            "content": page_data.get("content", ""),
            "filePaths": page_data.get("filePaths", []),
            "importance": page_data.get("importance", "medium"),
            "relatedPages": page_data.get("relatedPages", []),
        })

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
