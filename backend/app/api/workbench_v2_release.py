"""Release switch shared by the V2 frontend and compatibility entry points."""

from fastapi import APIRouter

from app.config import settings


router = APIRouter(prefix="/api/workbench", tags=["workbench-v2-release"])


@router.get("/release")
async def workbench_release_status() -> dict[str, bool]:
    return {"workbench_v2_enabled": settings.workbench_v2_enabled}
