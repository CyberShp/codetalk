"""Release status for the single versioned Workbench experience."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/workbench", tags=["workbench-v2-release"])


@router.get("/release")
async def workbench_release_status() -> dict[str, bool]:
    return {"workbench_v2_enabled": True}
