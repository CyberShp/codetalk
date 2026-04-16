"""Repository management endpoints (sync, status, search)."""

import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import create_adapter
from app.adapters.base import AnalysisRequest
from app.database import get_db
from app.models.repository import Repository
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
