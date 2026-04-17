"""Repository management endpoints (sync, status, search, detail, analyses)."""

import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.adapters import create_adapter
from app.adapters.base import AnalysisRequest
from app.database import get_db
from app.models.repository import Repository
from app.models.task import AnalysisTask
from app.models.wiki_cache_meta import WikiCacheMeta
from app.services import source_manager

router = APIRouter(prefix="/api/repos", tags=["repositories"])


@router.post("/{repo_id}/sync")
async def sync_repository(
    repo_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    repo = await db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")

    try:
        local_path = await source_manager.resolve_source(repo)
        repo.local_path = local_path
        repo.last_indexed_at = datetime.now(timezone.utc)
        await db.commit()
        return {
            "status": "synced",
            "local_path": local_path,
            "last_indexed_at": repo.last_indexed_at.isoformat(),
        }
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.delete("/{repo_id}", status_code=204)
async def delete_repository(repo_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    repo = await db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    await db.delete(repo)
    await db.commit()


class _SearchRequest(BaseModel):
    query: str
    num: int = 50


@router.post("/{repo_id}/search")
async def search_repository(
    repo_id: uuid.UUID,
    body: _SearchRequest,
    db: AsyncSession = Depends(get_db),
):
    """Perform an interactive Zoekt search against a repository.

    Automatically indexes the repo on first call (transparent to callers).
    Results are NOT persisted — this is a real-time search, not a task run.
    """
    if not body.query.strip():
        raise HTTPException(status_code=400, detail="query 不能为空")

    repo = await db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    if not repo.local_path:
        raise HTTPException(
            status_code=400,
            detail="仓库尚未同步，请先执行 sync 再搜索",
        )

    adapter = create_adapter("zoekt")
    request = AnalysisRequest(
        repo_local_path=repo.local_path,
        options={
            "query": body.query.strip(),
            "num": body.num,
            "repo_name": repo.name,
        },
    )

    try:
        await adapter.prepare(request)
        result = await adapter.analyze(request)
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Zoekt 服务不可用，请检查容器状态")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=503, detail=f"Zoekt 返回错误: {exc.response.status_code}")
    except RuntimeError as exc:
        # ZoektAdapter raises RuntimeError for infrastructure failures:
        # container not found, zoekt-index non-zero exit, index not visible after indexing.
        # All of these indicate Zoekt is unavailable or misconfigured — 503, not 500.
        raise HTTPException(status_code=503, detail=f"Zoekt 服务异常: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    search_results = result.data.get("search_results", [])
    return {
        "results": search_results,
        "query": body.query.strip(),
        "repo_name": repo.name,
        "total_matches": sum(len(f.get("matches", [])) for f in search_results),
    }


@router.post("/{repo_id}/sync/cancel")
async def cancel_sync(repo_id: uuid.UUID):
    cancelled = await source_manager.cancel_sync(repo_id)
    if not cancelled:
        raise HTTPException(status_code=409, detail="No active sync to cancel")
    return {"status": "cancelled"}


@router.get("/{repo_id}")
async def get_repo_detail(repo_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get repository with wiki and graph status summary."""
    repo = await db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(404, "Repository not found")

    # Wiki metadata
    wiki_result = await db.execute(
        select(WikiCacheMeta).where(WikiCacheMeta.repository_id == repo.id)
    )
    wiki_meta = wiki_result.scalar_one_or_none()

    # Graph status — check latest completed task with a gitnexus run
    task_result = await db.execute(
        select(AnalysisTask)
        .options(selectinload(AnalysisTask.tool_runs))
        .where(AnalysisTask.repository_id == repo.id)
        .where(AnalysisTask.status == "completed")
        .order_by(AnalysisTask.completed_at.desc())
        .limit(10)
    )
    tasks = task_result.scalars().all()

    graph_ready = False
    graph_stats = None
    graph_analyzed_at = None
    for task in tasks:
        gn_run = next(
            (r for r in task.tool_runs if r.tool_name == "gitnexus" and r.result),
            None,
        )
        if gn_run:
            graph_ready = True
            meta_data = gn_run.result.get("metadata", {})
            graph_stats = {
                "node_count": meta_data.get("node_count", 0),
                "edge_count": meta_data.get("edge_count", 0),
                "process_count": meta_data.get("process_count", 0),
                "community_count": meta_data.get("community_count", 0),
            }
            graph_analyzed_at = (
                task.completed_at.isoformat() if task.completed_at else None
            )
            break

    # Staleness check reused from wiki.py helpers
    from app.api.wiki import _check_staleness

    wiki_stale = False
    if wiki_meta:
        wiki_stale = _check_staleness(wiki_meta, repo)

    return {
        "repo": {
            "id": str(repo.id),
            "name": repo.name,
            "source_type": repo.source_type,
            "source_uri": repo.source_uri,
            "local_path": repo.local_path,
            "branch": repo.branch,
            "last_indexed_at": (
                repo.last_indexed_at.isoformat() if repo.last_indexed_at else None
            ),
        },
        "wiki": {
            "status": "ready" if wiki_meta else "not_generated",
            "generated_at": (
                wiki_meta.generated_at.isoformat() if wiki_meta else None
            ),
            "stale": wiki_stale,
        },
        "graph": {
            "status": "ready" if graph_ready else "not_analyzed",
            "analyzed_at": graph_analyzed_at,
            "stats": graph_stats,
        },
    }


@router.get("/{repo_id}/analyses")
async def list_repo_analyses(
    repo_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """List analysis tasks for a repository, paginated, newest first."""
    repo = await db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(404, "Repository not found")

    count_q = select(func.count()).select_from(AnalysisTask).where(
        AnalysisTask.repository_id == repo_id
    )
    total = (await db.execute(count_q)).scalar()

    items_q = (
        select(AnalysisTask)
        .where(AnalysisTask.repository_id == repo_id)
        .order_by(AnalysisTask.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(items_q)
    tasks = result.scalars().all()

    return {
        "items": [
            {
                "id": str(t.id),
                "task_type": t.task_type,
                "status": t.status,
                "error": t.error,
                "started_at": t.started_at.isoformat() if t.started_at else None,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                "created_at": t.created_at.isoformat(),
            }
            for t in tasks
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
    }
