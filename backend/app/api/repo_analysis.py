"""Repository-level static analysis endpoints.

Wraps Joern + Semgrep adapters for repo-centric access.
Follows the same pattern as repo_graph.py and repos.py.
"""

import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import create_adapter
from app.adapters.base import AnalysisRequest
from app.adapters.joern import JoernAdapter
from app.adapters.semgrep import SemgrepAdapter
from app.config import settings
from app.database import get_db
from app.models.repository import Repository
from app.utils.repo_paths import to_tool_repo_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/repos", tags=["analysis"])


# ── Helpers ──


async def _get_repo_or_404(
    repo_id: uuid.UUID, db: AsyncSession
) -> Repository:
    repo = await db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(404, "Repository not found")
    if not repo.local_path:
        raise HTTPException(400, "Repository not synced — run sync first")
    return repo


def _tool_path(repo: Repository) -> str:
    return to_tool_repo_path(
        repo.local_path,
        host_base_path=settings.repos_base_path,
        tool_base_path=settings.tool_repos_base_path,
    )


def _joern() -> JoernAdapter:
    return create_adapter("joern")  # type: ignore[return-value]


def _semgrep() -> SemgrepAdapter:
    return create_adapter("semgrep")  # type: ignore[return-value]


# ── Combined analysis ──


@router.get("/{repo_id}/analysis/summary")
async def get_analysis_summary(
    repo_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    """Get combined analysis summary from Joern + Semgrep.

    Runs a lightweight health probe against both tools and returns
    their availability plus any cached analysis state.
    """
    repo = await _get_repo_or_404(repo_id, db)
    joern = _joern()
    semgrep = _semgrep()

    joern_health = await joern.health_check()
    semgrep_health = await semgrep.health_check()

    return {
        "repo_id": str(repo_id),
        "repo_name": repo.name,
        "tools": {
            "joern": {
                "healthy": joern_health.is_healthy,
                "status": joern_health.container_status,
                "capabilities": [c.value for c in joern.capabilities()],
            },
            "semgrep": {
                "healthy": semgrep_health.is_healthy,
                "status": semgrep_health.container_status,
                "capabilities": [c.value for c in semgrep.capabilities()],
            },
        },
    }


# ── Joern endpoints ──


class _CpgqlRequest(BaseModel):
    query: str


@router.post("/{repo_id}/analysis/joern/query")
async def joern_custom_query(
    repo_id: uuid.UUID,
    body: _CpgqlRequest,
    db: AsyncSession = Depends(get_db),
):
    """Execute custom CPGQL query on repo's CPG.

    Exposed for advanced users and for Chat/LLM to call.
    """
    repo = await _get_repo_or_404(repo_id, db)
    joern = _joern()
    tool_path = _tool_path(repo)

    try:
        await joern.prepare(AnalysisRequest(repo_local_path=tool_path))
        result = await joern.query_custom(body.query)
        return {"result": result}
    except httpx.ConnectError:
        raise HTTPException(503, "Joern service unavailable")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(503, f"Joern error: {exc.response.status_code}")
    finally:
        await joern.cleanup(AnalysisRequest(repo_local_path=tool_path))


@router.get("/{repo_id}/analysis/joern/methods")
async def joern_methods(
    repo_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    """Get all methods/functions in the repo."""
    repo = await _get_repo_or_404(repo_id, db)
    joern = _joern()
    tool_path = _tool_path(repo)

    try:
        await joern.prepare(AnalysisRequest(repo_local_path=tool_path))
        result = await joern.method_list()
        return {"methods": result}
    except httpx.ConnectError:
        raise HTTPException(503, "Joern service unavailable")
    finally:
        await joern.cleanup(AnalysisRequest(repo_local_path=tool_path))


@router.get("/{repo_id}/analysis/joern/method/{method_name}/all")
async def method_all_analysis(
    repo_id: uuid.UUID,
    method_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Batch: branches + errors + boundaries in ONE CPG import.

    Avoids triple prepare() when frontend queries all three at once.
    """
    repo = await _get_repo_or_404(repo_id, db)
    joern = _joern()
    tool_path = _tool_path(repo)

    try:
        await joern.prepare(AnalysisRequest(repo_local_path=tool_path))
        branches = await joern.function_branches(method_name)
        errors = await joern.error_paths(method_name)
        boundaries = await joern.boundary_values(method_name)
        return {
            "method": method_name,
            "branches": branches,
            "errors": errors,
            "boundaries": boundaries,
        }
    except httpx.ConnectError:
        raise HTTPException(503, "Joern service unavailable")
    finally:
        await joern.cleanup(AnalysisRequest(repo_local_path=tool_path))


@router.get("/{repo_id}/analysis/joern/method/{method_name}/branches")
async def method_branches(
    repo_id: uuid.UUID,
    method_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Get all branches (if/else/switch/try-catch) in a method."""
    repo = await _get_repo_or_404(repo_id, db)
    joern = _joern()
    tool_path = _tool_path(repo)

    try:
        await joern.prepare(AnalysisRequest(repo_local_path=tool_path))
        result = await joern.function_branches(method_name)
        return {"method": method_name, "branches": result}
    except httpx.ConnectError:
        raise HTTPException(503, "Joern service unavailable")
    finally:
        await joern.cleanup(AnalysisRequest(repo_local_path=tool_path))


@router.get("/{repo_id}/analysis/joern/method/{method_name}/errors")
async def method_error_paths(
    repo_id: uuid.UUID,
    method_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Get all error/exception paths in a method."""
    repo = await _get_repo_or_404(repo_id, db)
    joern = _joern()
    tool_path = _tool_path(repo)

    try:
        await joern.prepare(AnalysisRequest(repo_local_path=tool_path))
        result = await joern.error_paths(method_name)
        return {"method": method_name, "errors": result}
    except httpx.ConnectError:
        raise HTTPException(503, "Joern service unavailable")
    finally:
        await joern.cleanup(AnalysisRequest(repo_local_path=tool_path))


@router.get("/{repo_id}/analysis/joern/method/{method_name}/boundaries")
async def method_boundaries(
    repo_id: uuid.UUID,
    method_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Get boundary value comparisons in a method."""
    repo = await _get_repo_or_404(repo_id, db)
    joern = _joern()
    tool_path = _tool_path(repo)

    try:
        await joern.prepare(AnalysisRequest(repo_local_path=tool_path))
        result = await joern.boundary_values(method_name)
        return {"method": method_name, "boundaries": result}
    except httpx.ConnectError:
        raise HTTPException(503, "Joern service unavailable")
    finally:
        await joern.cleanup(AnalysisRequest(repo_local_path=tool_path))


class _TaintRequest(BaseModel):
    source: str
    sink: str


@router.post("/{repo_id}/analysis/joern/taint")
async def taint_analysis(
    repo_id: uuid.UUID,
    body: _TaintRequest,
    db: AsyncSession = Depends(get_db),
):
    """Run taint analysis from source to sink patterns.

    Example: {"source": "getParameter", "sink": "executeQuery"}
    """
    repo = await _get_repo_or_404(repo_id, db)
    joern = _joern()
    tool_path = _tool_path(repo)

    try:
        await joern.prepare(AnalysisRequest(repo_local_path=tool_path))
        raw_paths = await joern.taint_analysis(body.source, body.sink)
        # Reshape Joern raw tuples into TaintPath[] for frontend:
        # Joern returns [[("code","file",line), ...], ...] → [{elements: [{code,filename,line_number}]}]
        paths = _reshape_taint_paths(raw_paths)
        return {"source": body.source, "sink": body.sink, "paths": paths}
    except httpx.ConnectError:
        raise HTTPException(503, "Joern service unavailable")
    finally:
        await joern.cleanup(AnalysisRequest(repo_local_path=tool_path))


def _reshape_taint_paths(raw: object) -> list[dict]:
    """Convert Joern raw taint result to TaintPath[] shape.

    Joern query returns: [[("code","file",line), ...], ...]
    Frontend expects: [{"elements": [{"code": str, "filename": str, "line_number": int}]}]
    Pure format conversion — no analysis logic.
    """
    if not isinstance(raw, list):
        return []
    paths = []
    for path_data in raw:
        if isinstance(path_data, list):
            elements = []
            for step in path_data:
                if isinstance(step, (list, tuple)) and len(step) >= 3:
                    elements.append({
                        "code": str(step[0]),
                        "filename": str(step[1]),
                        "line_number": step[2],
                    })
                elif isinstance(step, dict):
                    elements.append({
                        "code": step.get("code", ""),
                        "filename": step.get("filename", ""),
                        "line_number": step.get("lineNumber") or step.get("line_number"),
                    })
            if elements:
                paths.append({"elements": elements})
        elif isinstance(path_data, dict) and "elements" in path_data:
            paths.append(path_data)
    return paths


# ── Semgrep endpoints ──


@router.post("/{repo_id}/analysis/semgrep/scan")
async def semgrep_scan(
    repo_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    """Trigger a full Semgrep scan with all rule sets + custom rules."""
    repo = await _get_repo_or_404(repo_id, db)
    semgrep = _semgrep()
    tool_path = _tool_path(repo)

    try:
        result = await semgrep.analyze(
            AnalysisRequest(repo_local_path=tool_path)
        )
        return {
            "status": "completed",
            "summary": result.data.get("summary"),
            "categorized": result.data.get("categorized"),
            "findings": result.data.get("findings"),
            "metadata": result.metadata,
        }
    except httpx.ConnectError:
        raise HTTPException(503, "Semgrep service unavailable")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(503, f"Semgrep error: {exc.response.status_code}")


@router.get("/{repo_id}/analysis/semgrep/findings")
async def semgrep_findings(
    repo_id: uuid.UUID,
    severity: str | None = Query(None, description="Filter: INFO, WARNING, ERROR"),
    category: str | None = Query(None, description="Filter: injection, auth_bypass, etc."),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Get Semgrep findings, filterable by severity and category.

    Runs a scan if no cached results, or returns cached. For now, always scans.
    """
    repo = await _get_repo_or_404(repo_id, db)
    semgrep = _semgrep()
    tool_path = _tool_path(repo)

    try:
        if severity:
            raw = await semgrep.scan_with_severity(tool_path, severity)
            findings = raw.get("results", [])
        else:
            result = await semgrep.analyze(
                AnalysisRequest(repo_local_path=tool_path)
            )
            findings = result.data.get("findings", [])

        # Category filter (post-scan)
        if category:
            findings = [
                f for f in findings
                if category.lower() in f.get("check_id", "").lower()
                or category.lower()
                in f.get("extra", {}).get("metadata", {}).get("category", "").lower()
            ]

        # Pagination
        total = len(findings)
        start = (page - 1) * page_size
        page_findings = findings[start : start + page_size]

        return {
            "findings": page_findings,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": (total + page_size - 1) // page_size if total else 0,
        }
    except httpx.ConnectError:
        raise HTTPException(503, "Semgrep service unavailable")


class _IncrementalScanRequest(BaseModel):
    baseline_commit: str


@router.post("/{repo_id}/analysis/semgrep/scan/incremental")
async def semgrep_incremental_scan(
    repo_id: uuid.UUID,
    body: _IncrementalScanRequest,
    db: AsyncSession = Depends(get_db),
):
    """Incremental scan — only new findings since baseline commit."""
    repo = await _get_repo_or_404(repo_id, db)
    semgrep = _semgrep()
    tool_path = _tool_path(repo)

    try:
        result = await semgrep.scan_incremental(tool_path, body.baseline_commit)
        return result
    except httpx.ConnectError:
        raise HTTPException(503, "Semgrep service unavailable")


# ── Combined: Test Points ──


class _TestPointRequest(BaseModel):
    target: str | None = None  # function name, file path, or None for all
    perspective: str = "black_box"


@router.post("/{repo_id}/analysis/test-points")
async def generate_test_points(
    repo_id: uuid.UUID,
    body: _TestPointRequest,
    db: AsyncSession = Depends(get_db),
):
    """Generate black-box test points using Joern + Semgrep + GitNexus + LLM.

    Core pipeline:
    1. Joern: extract control flow, exception paths, boundary values
    2. Semgrep: extract security findings and pattern matches
    3. GitNexus: resolve call chains and process flows
    4. LLM (DeepWiki): translate to black-box test descriptions
    """
    repo = await _get_repo_or_404(repo_id, db)
    tool_path = _tool_path(repo)

    from app.services.test_point_generator import generate_test_points as gen

    try:
        test_points = await gen(
            repo_path=tool_path,
            target=body.target,
            perspective=body.perspective,
        )
        return {
            "status": "completed",
            "target": body.target or "full_repo",
            "perspective": body.perspective,
            "test_points": test_points,
            "count": len(test_points),
        }
    except httpx.ConnectError as exc:
        raise HTTPException(
            503, f"Analysis tool unavailable: {exc}"
        )
    except Exception as exc:
        logger.exception("Test point generation failed")
        raise HTTPException(500, f"Test point generation failed: {exc}")
