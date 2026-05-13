"""Export API -- download task reports in various formats."""

import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.services.export_service import export_reports

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["报告导出"])


@router.get("/{task_id}/export")
async def export_task_reports(
    task_id: str,
    fmt: str = Query(default="md", alias="format", description="导出格式: md, docx, xml"),
) -> Response:
    """Download task reports as zip/docx/xml."""
    try:
        file_bytes, filename, content_type = await export_reports(task_id, fmt)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return Response(
        content=file_bytes,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )