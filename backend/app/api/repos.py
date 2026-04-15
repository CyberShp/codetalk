"""Repository management endpoints (sync, status)."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

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
