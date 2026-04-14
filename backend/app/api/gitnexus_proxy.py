"""Proxy endpoint for GitNexus file content.

Used by the frontend to fetch function-level code slices on node click.
"""

from fastapi import APIRouter, HTTPException, Query

import httpx

from app.config import settings

router = APIRouter(prefix="/api/gitnexus", tags=["gitnexus"])


@router.get("/file")
async def get_file_content(
    repo: str = Query(..., description="Repository name"),
    path: str = Query(..., description="File path within repo"),
    start_line: int | None = Query(None, description="Start line (0-indexed)"),
    end_line: int | None = Query(None, description="End line (0-indexed)"),
):
    """Proxy to GitNexus /api/file with line-range support."""
    params: dict[str, str] = {"repo": repo, "path": path}
    if start_line is not None:
        params["startLine"] = str(start_line)
    if end_line is not None:
        params["endLine"] = str(end_line)

    try:
        async with httpx.AsyncClient(
            base_url=settings.gitnexus_base_url,
            timeout=30,
        ) as client:
            resp = await client.get("/api/file", params=params)
            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail="File not found")
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
