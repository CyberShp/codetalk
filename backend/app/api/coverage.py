"""Coverage analysis API — upload, parse, AI-analyze, and retrieve results."""

from __future__ import annotations

import logging
import uuid

import aiosqlite
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.config import settings
from app.services.coverage_analyzer import CoverageAnalyzer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/coverage", tags=["coverage"])

_analyzer = CoverageAnalyzer()


class CoverageAnalysisResponse(BaseModel):
    id: str
    name: str
    source_type: str
    status: str
    overall_line_rate: float
    overall_branch_rate: float
    overall_function_rate: float
    module_count: int
    source_format: str
    created_at: str
    updated_at: str


class CoverageDetailResponse(CoverageAnalysisResponse):
    modules_json: str | None = None
    analysis_results_json: str | None = None


@router.post("/upload", response_model=CoverageAnalysisResponse)
async def upload_coverage(
    files: list[UploadFile] = File(..., description="XML 或 HTML 覆盖率报告文件"),
    name: str = "",
):
    """Upload coverage report files (XML/HTML) for parsing."""
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一个覆盖率文件")

    max_bytes = settings.coverage_max_upload_mb * 1024 * 1024

    parsed_files: list[tuple[str, str]] = []
    for f in files:
        if not f.filename:
            continue
        lower = f.filename.lower()
        if not lower.endswith((".xml", ".html", ".htm")):
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件格式: {f.filename}（仅支持 XML、HTML）",
            )
        content = await f.read()
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=400,
                detail=f"文件 {f.filename} 超过 {settings.coverage_max_upload_mb}MB 限制",
            )
        parsed_files.append((f.filename, content.decode("utf-8", errors="replace")))

    if not parsed_files:
        raise HTTPException(status_code=400, detail="未找到有效的覆盖率文件")

    analysis_id = str(uuid.uuid4())

    try:
        await _analyzer.parse_and_store(analysis_id, parsed_files, name=name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    logger.info("Coverage uploaded: id=%s, files=%d", analysis_id, len(parsed_files))
    return await _get_analysis(analysis_id)


@router.get("/list", response_model=list[CoverageAnalysisResponse])
async def list_analyses():
    """List all coverage analyses, newest first."""
    async with aiosqlite.connect(settings.sqlite_db) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM coverage_analyses ORDER BY created_at DESC"
        )
    return [_row_to_response(dict(r)) for r in rows]


@router.get("/{analysis_id}", response_model=CoverageDetailResponse)
async def get_analysis(analysis_id: str):
    """Get a single coverage analysis with full details."""
    return await _get_analysis_detail(analysis_id)


@router.post("/{analysis_id}/analyze")
async def trigger_analysis(analysis_id: str):
    """Run AI analysis on parsed coverage data."""
    record = await _get_analysis_detail(analysis_id)

    if record.status not in ("parsed", "analyzed"):
        raise HTTPException(
            status_code=400,
            detail=f"当前状态「{record.status}」不支持分析",
        )

    try:
        results = await _analyzer.run_analysis(analysis_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "analysis_id": analysis_id,
        "status": "analyzed",
        "module_results": len(results),
        "results": results,
    }


@router.delete("/{analysis_id}")
async def delete_analysis(analysis_id: str):
    """Delete a coverage analysis."""
    async with aiosqlite.connect(settings.sqlite_db) as db:
        cursor = await db.execute(
            "DELETE FROM coverage_analyses WHERE id = ?", (analysis_id,)
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="覆盖率分析不存在")
    return {"status": "deleted"}


@router.post("/fetch-from-api")
async def fetch_from_intranet_api():
    """Reserved endpoint — fetch coverage from intranet precise testing tool.

    Returns 501 until the intranet tool API is finalized.
    """
    raise HTTPException(
        status_code=501,
        detail="内网精准测试工具 API 尚未对接，请使用文件上传方式",
    )


async def _get_analysis(analysis_id: str) -> CoverageAnalysisResponse:
    async with aiosqlite.connect(settings.sqlite_db) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM coverage_analyses WHERE id = ?", (analysis_id,)
        )
    if not rows:
        raise HTTPException(status_code=404, detail="覆盖率分析不存在")
    return _row_to_response(dict(rows[0]))


async def _get_analysis_detail(analysis_id: str) -> CoverageDetailResponse:
    async with aiosqlite.connect(settings.sqlite_db) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM coverage_analyses WHERE id = ?", (analysis_id,)
        )
    if not rows:
        raise HTTPException(status_code=404, detail="覆盖率分析不存在")
    return _row_to_detail_response(dict(rows[0]))


def _row_to_response(row: dict) -> CoverageAnalysisResponse:
    return CoverageAnalysisResponse(
        id=row["id"],
        name=row.get("name", ""),
        source_type=row.get("source_type", "upload"),
        status=row.get("status", "unknown"),
        overall_line_rate=row.get("overall_line_rate", 0),
        overall_branch_rate=row.get("overall_branch_rate", 0),
        overall_function_rate=row.get("overall_function_rate", 0),
        module_count=row.get("module_count", 0),
        source_format=row.get("source_format", "unknown"),
        created_at=row.get("created_at", ""),
        updated_at=row.get("updated_at", ""),
    )


def _row_to_detail_response(row: dict) -> CoverageDetailResponse:
    return CoverageDetailResponse(
        id=row["id"],
        name=row.get("name", ""),
        source_type=row.get("source_type", "upload"),
        status=row.get("status", "unknown"),
        overall_line_rate=row.get("overall_line_rate", 0),
        overall_branch_rate=row.get("overall_branch_rate", 0),
        overall_function_rate=row.get("overall_function_rate", 0),
        module_count=row.get("module_count", 0),
        source_format=row.get("source_format", "unknown"),
        modules_json=row.get("modules_json"),
        analysis_results_json=row.get("analysis_results_json"),
        created_at=row.get("created_at", ""),
        updated_at=row.get("updated_at", ""),
    )
