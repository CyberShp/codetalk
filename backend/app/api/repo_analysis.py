"""Repository-level static analysis endpoints.

Wraps Joern adapter for repo-centric CPG access.
Follows the same pattern as repo_graph.py and repos.py.
"""

import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import create_adapter
from app.adapters.base import AnalysisRequest
from app.adapters.joern import JoernAdapter
from app.config import settings
from app.database import get_db
from app.models.llm_config import LLMConfig
from app.models.repository import Repository
from app.utils.repo_paths import to_tool_repo_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/repos", tags=["analysis"])

# In-memory scan result cache: repo_id → findings list.


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


# Shared Joern adapter: CPG import is expensive (3+ min for large repos).
# Reusing the instance lets prepare() skip re-import when the same repo
# is already loaded.
_joern_instance: JoernAdapter | None = None


def _joern() -> JoernAdapter:
    global _joern_instance
    if _joern_instance is None:
        _joern_instance = create_adapter("joern")  # type: ignore[assignment]
    return _joern_instance



# ── Combined analysis ──


@router.get("/{repo_id}/analysis/summary")
async def get_analysis_summary(
    repo_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    """Get analysis summary from Joern CPG engine.

    Runs a lightweight health probe and returns availability
    plus capabilities.
    """
    repo = await _get_repo_or_404(repo_id, db)
    joern = _joern()

    joern_health = await joern.health_check()

    return {
        "repo_id": str(repo_id),
        "repo_name": repo.name,
        "tools": {
            "joern": {
                "healthy": joern_health.is_healthy,
                "status": joern_health.container_status,
                "capabilities": [c.value for c in joern.capabilities()],
            },
        },
    }


# ── Joern endpoints ──


@router.post("/{repo_id}/analysis/joern/rebuild")
async def joern_rebuild(
    repo_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    """Force re-import of repo into Joern CPG.

    Clears the cached project name so prepare() will run a fresh
    importCode() even if the same repo was previously loaded.
    Use after code changes or Joern container restart.
    """
    repo = await _get_repo_or_404(repo_id, db)
    joern = _joern()
    tool_path = _tool_path(repo)

    # Clear cached project so prepare() won't skip re-import
    joern._imported_project = None
    JoernAdapter.clear_cached_project(joern.base_url)

    try:
        await joern.prepare(AnalysisRequest(repo_local_path=tool_path))
        return {"status": "rebuilt", "repo_id": str(repo_id)}
    except httpx.ConnectError:
        raise HTTPException(503, "Joern service unavailable")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(503, f"Joern error: {exc.response.status_code}")


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
    """Batch: branches + errors + boundaries + cross-function context in ONE CPG import.

    Returns both intra-function analysis AND inter-procedural context:
    - branches: control flow within the function
    - errors: exception/error paths within the function
    - boundaries: boundary value comparisons within the function
    - callContext: who calls this function and from what control flow
    - calleeImpact: what this function calls and their error returns
    """
    repo = await _get_repo_or_404(repo_id, db)
    joern = _joern()
    tool_path = _tool_path(repo)

    try:
        await joern.prepare(AnalysisRequest(repo_local_path=tool_path))
        branches = await joern.function_branches(method_name)
        errors = await joern.error_paths(method_name)
        boundaries = await joern.boundary_values(method_name)
        # Cross-function context — catch errors individually so partial results still return
        call_ctx = []
        callee_imp = []
        try:
            call_ctx = await joern.call_context(method_name)
        except Exception as exc:
            logger.warning("joern: call_context failed for %s: %s", method_name, exc)
        try:
            callee_imp = await joern.callee_impact(method_name)
        except Exception as exc:
            logger.warning("joern: callee_impact failed for %s: %s", method_name, exc)
        return {
            "method": method_name,
            "branches": branches,
            "errors": errors,
            "boundaries": boundaries,
            "callContext": call_ctx,
            "calleeImpact": callee_imp,
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


@router.get("/{repo_id}/analysis/joern/method/{method_name}/cfg")
async def method_cfg(
    repo_id: uuid.UUID,
    method_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Get the Control Flow Graph in DOT format for a method."""
    repo = await _get_repo_or_404(repo_id, db)
    joern = _joern()
    tool_path = _tool_path(repo)

    try:
        await joern.prepare(AnalysisRequest(repo_local_path=tool_path))
        dot = await joern.cfg_dot(method_name)
        return {"method": method_name, "dot": dot}
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
    """Convert Joern taint co-occurrence result to TaintPath[] shape.

    New format (method co-occurrence): [{method, file, elements: [{code, filename, line_number, is_source}]}]
    Legacy format (reachableBy): [[{code, filename, line_number}, ...], ...]
    Frontend expects: [{"elements": [{"code": str, "filename": str, "line_number": int, "is_source"?: bool}], "method"?: str, "file"?: str}]
    Pure format conversion — no analysis logic.
    """
    if not isinstance(raw, list):
        return []
    paths = []
    for path_data in raw:
        if isinstance(path_data, dict):
            # New co-occurrence format: {method, file, elements: [...]}
            if "elements" in path_data:
                elements = []
                for step in (path_data["elements"] if isinstance(path_data["elements"], list) else []):
                    if isinstance(step, dict):
                        ln = step.get("line") or step.get("line_number") or step.get("lineNumber") or -1
                        elements.append({
                            "code": step.get("code", ""),
                            "filename": step.get("file") or step.get("filename", ""),
                            "line_number": int(ln) if ln is not None else -1,
                            "is_source": step.get("role") == "source" if "role" in step else step.get("is_source", False),
                        })
                if elements:
                    entry: dict = {"elements": elements}
                    if path_data.get("method"):
                        entry["method"] = path_data["method"]
                    if path_data.get("file"):
                        entry["file"] = path_data["file"]
                    paths.append(entry)
        elif isinstance(path_data, list):
            # Legacy reachableBy format: [step, step, ...]
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
    return paths



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
    """Generate black-box test points using Joern + GitNexus + LLM.

    Core pipeline:
    1. Joern: extract control flow, exception paths, boundary values + cross-function context
    2. GitNexus: resolve call chains and process flows
    3. LLM (DeepWiki): translate to black-box test descriptions
    """
    repo = await _get_repo_or_404(repo_id, db)
    tool_path = _tool_path(repo)

    # Read user's LLM config for DeepWiki
    result = await db.execute(
        select(LLMConfig).where(LLMConfig.is_default.is_(True)).limit(1)
    )
    llm_cfg = result.scalar_one_or_none()
    llm_config = None
    if llm_cfg:
        provider = llm_cfg.provider
        if provider == "custom":
            provider = "openai"
        llm_config = {"provider": provider, "model": llm_cfg.model_name}

    from app.services.test_point_generator import generate_test_points as gen

    try:
        test_points = await gen(
            repo_path=tool_path,
            target=body.target,
            perspective=body.perspective,
            llm_config=llm_config,
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
