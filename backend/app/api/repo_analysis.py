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
from app.adapters.codecompass import CodeCompassAdapter
from app.adapters.joern import JoernAdapter
from app.config import settings
from app.database import get_db
from app.models.analysis_snapshot import AnalysisSnapshot
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



_codecompass_instance: CodeCompassAdapter | None = None


def _codecompass() -> CodeCompassAdapter:
    global _codecompass_instance
    if _codecompass_instance is None:
        _codecompass_instance = create_adapter("codecompass")  # type: ignore[assignment]
    return _codecompass_instance


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


# ── CodeCompass endpoints ──


@router.post("/{repo_id}/analysis/codecompass/rebuild")
async def codecompass_rebuild(
    repo_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    """Force re-parse of repo in CodeCompass.

    Clears the cached project so prepare() will invoke CodeCompass_parser
    even if the same repo was previously parsed.
    Use after code changes (git pull) or container restart.
    """
    repo = await _get_repo_or_404(repo_id, db)
    cc = _codecompass()

    cc._current_workspace = None
    CodeCompassAdapter.clear_cached_project(cc.base_url)

    try:
        # Pass raw local_path — prepare() does its own to_tool_repo_path()
        # translation internally (unlike Joern which expects pre-translated).
        await cc.prepare(AnalysisRequest(repo_local_path=repo.local_path))
        return {"status": "rebuilt", "repo_id": str(repo_id)}
    except httpx.ConnectError:
        raise HTTPException(503, "CodeCompass service unavailable")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(503, f"CodeCompass error: {exc.response.status_code}")


@router.get("/{repo_id}/analysis/codecompass/call-graph/{function_name}")
async def codecompass_call_graph(
    repo_id: uuid.UUID,
    function_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Get call graph for a specific function via CodeCompass."""
    repo = await _get_repo_or_404(repo_id, db)
    cc = _codecompass()

    try:
        await cc.prepare(AnalysisRequest(repo_local_path=repo.local_path))
        result = await cc.function_call_graph(function_name)
        return {"function": function_name, "call_graph": result}
    except httpx.ConnectError:
        raise HTTPException(503, "CodeCompass service unavailable")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(503, f"CodeCompass error: {exc.response.status_code}")


@router.get("/{repo_id}/analysis/codecompass/pointer-analysis/{function_name}")
async def codecompass_pointer_analysis(
    repo_id: uuid.UUID,
    function_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Get pointer analysis results for a specific function.

    Returns alias sets, points-to information, and pointer dereference paths.
    Critical for SFMEA: identifies hidden coupling through shared memory.
    """
    repo = await _get_repo_or_404(repo_id, db)
    cc = _codecompass()

    try:
        await cc.prepare(AnalysisRequest(repo_local_path=repo.local_path))
        result = await cc.pointer_analysis_for(function_name)
        return {"function": function_name, "pointer_analysis": result}
    except httpx.ConnectError:
        raise HTTPException(503, "CodeCompass service unavailable")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(503, f"CodeCompass error: {exc.response.status_code}")


@router.get("/{repo_id}/analysis/codecompass/indirect-calls/{function_name}")
async def codecompass_indirect_calls(
    repo_id: uuid.UUID,
    function_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Resolve indirect call targets (function pointers, virtual dispatch).

    Returns concrete functions that a function pointer or virtual call
    may resolve to. Critical for SFMEA: uncovers untested dispatch branches.
    """
    repo = await _get_repo_or_404(repo_id, db)
    cc = _codecompass()

    try:
        await cc.prepare(AnalysisRequest(repo_local_path=repo.local_path))
        result = await cc.indirect_calls(function_name)
        return {"function": function_name, "indirect_calls": result}
    except httpx.ConnectError:
        raise HTTPException(503, "CodeCompass service unavailable")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(503, f"CodeCompass error: {exc.response.status_code}")


class _AliasRequest(BaseModel):
    variable: str
    file_path: str
    line: int


@router.post("/{repo_id}/analysis/codecompass/alias")
async def codecompass_alias_analysis(
    repo_id: uuid.UUID,
    body: _AliasRequest,
    db: AsyncSession = Depends(get_db),
):
    """Get pointer alias set for a variable at a specific location.

    Answers: "what other pointers could point to the same memory as this variable?"
    Critical for SFMEA: quantifies hidden state mutation risk.
    """
    repo = await _get_repo_or_404(repo_id, db)
    cc = _codecompass()

    try:
        await cc.prepare(AnalysisRequest(repo_local_path=repo.local_path))
        result = await cc.alias_analysis(body.variable, body.file_path, body.line)
        return {
            "variable": body.variable,
            "file": body.file_path,
            "line": body.line,
            "aliases": result,
        }
    except httpx.ConnectError:
        raise HTTPException(503, "CodeCompass service unavailable")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(503, f"CodeCompass error: {exc.response.status_code}")


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


@router.get("/{repo_id}/analysis/joern/method/{method_name}/variable/{var_name}/track")
async def variable_tracking(
    repo_id: uuid.UUID,
    method_name: str,
    var_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Track variable usages within a method."""
    repo = await _get_repo_or_404(repo_id, db)
    joern = _joern()
    tool_path = _tool_path(repo)

    try:
        await joern.prepare(AnalysisRequest(repo_local_path=tool_path))
        result = await joern.variable_tracking(method_name, var_name)
        return {"method": method_name, "variable": var_name, "usages": result}
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
    mode: str = "cooccur"  # "cooccur" = both present, "absence" = source present but sink missing


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
        if body.mode == "absence":
            raw_paths = await joern.absence_analysis(body.source, body.sink)
        else:
            raw_paths = await joern.taint_analysis(body.source, body.sink)
        # Reshape Joern raw tuples into TaintPath[] for frontend:
        # Joern returns [[("code","file",line), ...], ...] → [{elements: [{code,filename,line_number}]}]
        paths = _reshape_taint_paths(raw_paths)
        return {"source": body.source, "sink": body.sink, "mode": body.mode, "paths": paths}
    except httpx.ConnectError:
        raise HTTPException(503, "Joern service unavailable")
    finally:
        await joern.cleanup(AnalysisRequest(repo_local_path=tool_path))


class _TaintVerifyRequest(BaseModel):
    method: str
    source: str
    sink: str


@router.post("/{repo_id}/analysis/joern/taint-verify")
async def taint_verify(
    repo_id: uuid.UUID,
    body: _TaintVerifyRequest,
    db: AsyncSession = Depends(get_db),
):
    """Verify a taint path using scoped reachableByFlows.

    Scoped to a single method to avoid full-project timeouts.
    """
    repo = await _get_repo_or_404(repo_id, db)
    joern = _joern()
    tool_path = _tool_path(repo)

    try:
        await joern.prepare(AnalysisRequest(repo_local_path=tool_path))
        raw_flows = await joern.scoped_taint_verify(
            body.method, body.source, body.sink
        )
        # raw_flows is a list of flow paths, each is a list of step dicts
        flows = []
        if isinstance(raw_flows, list):
            for flow in raw_flows:
                if isinstance(flow, list):
                    steps = [
                        {
                            "code": s.get("code", ""),
                            "filename": s.get("file", ""),
                            "line_number": int(s.get("line", -1)),
                        }
                        for s in flow
                        if isinstance(s, dict)
                    ]
                    if steps:
                        flows.append({"elements": steps})
        return {
            "method": body.method,
            "source": body.source,
            "sink": body.sink,
            "verified": len(flows) > 0,
            "flows": flows,
        }
    except httpx.ReadTimeout:
        return {
            "method": body.method,
            "source": body.source,
            "sink": body.sink,
            "verified": False,
            "flows": [],
            "fallback": "timeout",
        }
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


# ── Snapshot persistence (Phase D) ──


class _SnapshotSave(BaseModel):
    risk_matrix: list[dict]
    summary: dict


@router.post("/{repo_id}/analysis/snapshots", status_code=201)
async def save_snapshot(
    repo_id: uuid.UUID,
    body: _SnapshotSave,
    db: AsyncSession = Depends(get_db),
):
    """Persist a risk-matrix snapshot for historical comparison."""
    await _get_repo_or_404(repo_id, db)
    snap = AnalysisSnapshot(
        repository_id=repo_id,
        risk_matrix=body.risk_matrix,  # type: ignore[arg-type]
        summary=body.summary,  # type: ignore[arg-type]
    )
    db.add(snap)
    await db.commit()
    await db.refresh(snap)
    return {
        "id": str(snap.id),
        "repository_id": str(snap.repository_id),
        "summary": snap.summary,
        "created_at": snap.created_at.isoformat() if snap.created_at else None,
    }


@router.get("/{repo_id}/analysis/snapshots")
async def list_snapshots(
    repo_id: uuid.UUID,
    limit: int = Query(default=20, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List recent snapshots for a repo, newest first."""
    await _get_repo_or_404(repo_id, db)
    result = await db.execute(
        select(AnalysisSnapshot)
        .where(AnalysisSnapshot.repository_id == repo_id)
        .order_by(AnalysisSnapshot.created_at.desc())
        .limit(limit)
    )
    snaps = result.scalars().all()
    return {
        "snapshots": [
            {
                "id": str(s.id),
                "summary": s.summary,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in snaps
        ]
    }


@router.get("/{repo_id}/analysis/snapshots/{snapshot_id}")
async def get_snapshot(
    repo_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Retrieve full risk matrix from a specific snapshot."""
    snap = await db.get(AnalysisSnapshot, snapshot_id)
    if not snap or snap.repository_id != repo_id:
        raise HTTPException(404, "Snapshot not found")
    return {
        "id": str(snap.id),
        "repository_id": str(snap.repository_id),
        "risk_matrix": snap.risk_matrix,
        "summary": snap.summary,
        "created_at": snap.created_at.isoformat() if snap.created_at else None,
    }


@router.get("/{repo_id}/analysis/snapshots/diff")
async def diff_snapshots(
    repo_id: uuid.UUID,
    from_id: uuid.UUID = Query(..., alias="from"),
    to_id: uuid.UUID = Query(..., alias="to"),
    db: AsyncSession = Depends(get_db),
):
    """Compare two snapshots: delta in high-risk count and method-level changes."""
    snap_from = await db.get(AnalysisSnapshot, from_id)
    snap_to = await db.get(AnalysisSnapshot, to_id)
    if not snap_from or snap_from.repository_id != repo_id:
        raise HTTPException(404, "Source snapshot not found")
    if not snap_to or snap_to.repository_id != repo_id:
        raise HTTPException(404, "Target snapshot not found")

    sum_from = snap_from.summary or {}
    sum_to = snap_to.summary or {}
    return {
        "from_id": str(from_id),
        "to_id": str(to_id),
        "delta": {
            "total_methods": (sum_to.get("total", 0) - sum_from.get("total", 0)),
            "high_risk": (sum_to.get("high", 0) - sum_from.get("high", 0)),
            "med_risk": (sum_to.get("med", 0) - sum_from.get("med", 0)),
            "avg_complexity": round(
                (sum_to.get("avgComplexity", 0) - sum_from.get("avgComplexity", 0)), 2
            ),
        },
        "from_created": snap_from.created_at.isoformat() if snap_from.created_at else None,
        "to_created": snap_to.created_at.isoformat() if snap_to.created_at else None,
    }


# ── Cross-tool impact radius (Phase F) ──


@router.get("/{repo_id}/analysis/impact-radius/{method_name}")
async def impact_radius(
    repo_id: uuid.UUID,
    method_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Aggregate Joern callers + GitNexus dependencies for impact radius.

    Pure orchestration: calls two tools, merges results.
    """
    repo = await _get_repo_or_404(repo_id, db)
    tool_path = _tool_path(repo)

    # 1. Joern: who calls this method
    joern = _joern()
    callers = []
    callee_files: list[str] = []
    try:
        await joern.prepare(AnalysisRequest(repo_local_path=tool_path))
        callers = await joern.call_context(method_name)
        # Collect unique files touched by callers
        for ctx in callers:
            f = ctx.get("callerFile", "")
            if f and f not in callee_files:
                callee_files.append(f)
    except httpx.ConnectError:
        logger.warning("Joern unavailable for impact-radius")
    except Exception as exc:
        logger.warning("joern call_context failed: %s", exc)
    finally:
        try:
            await joern.cleanup(AnalysisRequest(repo_local_path=tool_path))
        except Exception:
            pass

    # 2. GitNexus: module-level dependencies (best-effort)
    module_deps: list[dict] = []
    try:
        from app.adapters.gitnexus import GitNexusAdapter
        gn = GitNexusAdapter(base_url=settings.gitnexus_base_url)
        await gn.prepare(AnalysisRequest(repo_local_path=tool_path))
        graph_result = await gn.analyze(AnalysisRequest(repo_local_path=tool_path))
        # Extract relationships where source or target files overlap with caller files
        rels = graph_result.data.get("relationships", []) if graph_result.data else []
        seen = set()
        for rel in rels:
            src = rel.get("source", "")
            tgt = rel.get("target", "")
            rel_type = rel.get("type", "")
            # Find module-level deps linked to caller files
            for cf in callee_files:
                cf_base = cf.rsplit("/", 1)[-1].rsplit(".", 1)[0] if "/" in cf else cf
                if cf_base and (cf_base in src or cf_base in tgt):
                    key = (src, tgt, rel_type)
                    if key not in seen:
                        seen.add(key)
                        module_deps.append({"source": src, "target": tgt, "type": rel_type})
        await gn.cleanup(AnalysisRequest(repo_local_path=tool_path))
    except Exception as exc:
        logger.warning("GitNexus unavailable for impact-radius: %s", exc)

    return {
        "method": method_name,
        "callers": callers,
        "caller_files": callee_files,
        "module_dependencies": module_deps,
        "caller_count": len(callers),
        "module_dep_count": len(module_deps),
    }
