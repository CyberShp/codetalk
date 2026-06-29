"""Repository-level static analysis endpoints.

Wraps Joern adapter for repo-centric CPG access.
Follows the same pattern as repo_graph.py and repos.py.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
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
from app.models.repository import Repository
from app.models.task import AnalysisTask
from app.services import task_engine
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

# Joern is a single-instance CPG server; concurrent prepare/query/cleanup
# sequences on different repos would corrupt shared state.
# Semaphore(1) serialises all Joern operations without requiring a DB lock.
_joern_lock = asyncio.Semaphore(1)


def _joern() -> JoernAdapter:
    global _joern_instance
    if _joern_instance is None:
        _joern_instance = create_adapter("joern")  # type: ignore[assignment]
    return _joern_instance


# Same single-instance concern for CodeCompass.
_codecompass_lock = asyncio.Semaphore(1)

_codecompass_instance: CodeCompassAdapter | None = None


def _codecompass() -> CodeCompassAdapter:
    global _codecompass_instance
    if _codecompass_instance is None:
        _codecompass_instance = create_adapter("codecompass")  # type: ignore[assignment]
    return _codecompass_instance


async def _find_active_rebuild(
    db: AsyncSession, repo_id: uuid.UUID, task_type: str
) -> AnalysisTask | None:
    """Return the most recent pending/running rebuild task for this repo, if any."""
    stmt = (
        select(AnalysisTask)
        .where(
            AnalysisTask.repository_id == repo_id,
            AnalysisTask.task_type == task_type,
            AnalysisTask.status.in_(["pending", "running"]),
        )
        .order_by(AnalysisTask.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


# ── Combined analysis ──


@router.get("/{repo_id}/analysis/summary")
async def get_analysis_summary(
    repo_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    """Get analysis summary from all analysis engines.

    Runs lightweight health probes and returns availability
    plus capabilities for each tool.
    """
    repo = await _get_repo_or_404(repo_id, db)
    joern = _joern()
    cc = _codecompass()

    joern_health = await joern.health_check()
    cc_health = await cc.health_check()

    return {
        "repo_id": str(repo_id),
        "repo_name": repo.name,
        "tools": {
            "joern": {
                "healthy": joern_health.is_healthy,
                "status": joern_health.container_status,
                "capabilities": [c.value for c in joern.capabilities()],
            },
            "codecompass": {
                "healthy": cc_health.is_healthy,
                "status": cc_health.container_status,
                "capabilities": [c.value for c in cc.capabilities()],
            },
        },
    }


# ── Joern endpoints ──


@router.post("/{repo_id}/analysis/joern/rebuild")
async def joern_rebuild(
    repo_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)
):
    """Async re-import of repo into Joern CPG.

    Returns immediately with a task_id. Use the WebSocket endpoint
    /ws/tasks/{task_id}/logs to receive progress events.
    If a rebuild is already running, returns the existing task_id.
    """
    await _get_repo_or_404(repo_id, db)
    session_id = getattr(request.state, "session_id", None)

    existing = await _find_active_rebuild(db, repo_id, "joern_rebuild")
    if existing:
        return {"status": "reused", "task_id": str(existing.id), "message": "已有重建任务在运行"}

    task = AnalysisTask(
        repository_id=repo_id,
        task_type="joern_rebuild",
        tools=["joern"],
        status="pending",
        target_spec={},
        session_id=str(session_id) if session_id else None,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    handle = asyncio.create_task(task_engine.run_rebuild(task.id, "joern"))
    task_engine.register_task(task.id, handle)

    return {"status": "started", "task_id": str(task.id)}


# ── CodeCompass endpoints ──


@router.post("/{repo_id}/analysis/codecompass/rebuild")
async def codecompass_rebuild(
    repo_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)
):
    """Async re-parse of repo in CodeCompass.

    Returns immediately with a task_id. Use the WebSocket endpoint
    /ws/tasks/{task_id}/logs to receive progress events.
    If a rebuild is already running, returns the existing task_id.
    """
    await _get_repo_or_404(repo_id, db)
    session_id = getattr(request.state, "session_id", None)

    existing = await _find_active_rebuild(db, repo_id, "codecompass_rebuild")
    if existing:
        return {"status": "reused", "task_id": str(existing.id), "message": "已有重建任务在运行"}

    task = AnalysisTask(
        repository_id=repo_id,
        task_type="codecompass_rebuild",
        tools=["codecompass"],
        status="pending",
        target_spec={},
        session_id=str(session_id) if session_id else None,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    handle = asyncio.create_task(task_engine.run_rebuild(task.id, "codecompass"))
    task_engine.register_task(task.id, handle)

    return {"status": "started", "task_id": str(task.id)}


@router.get("/{repo_id}/analysis/codecompass/call-graph/{function_name}")
async def codecompass_call_graph(
    repo_id: uuid.UUID,
    function_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Get call graph for a specific function via CodeCompass."""
    repo = await _get_repo_or_404(repo_id, db)
    cc = _codecompass()

    async with _codecompass_lock:
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

    async with _codecompass_lock:
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

    async with _codecompass_lock:
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

    async with _codecompass_lock:
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

    async with _joern_lock:
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


def _method_lines(m: dict) -> int:
    """Extract line count from a Joern method dict."""
    line = int(m.get("line", 0) or 0)
    line_end = int(m.get("lineEnd", line) or line)
    return max(1, line_end - line + 1)


def _risk_level(complexity: int, lines: int = 1) -> str:
    """Match frontend riskLevel(): HIGH if complexity>15 OR density>0.5, MED if >8 OR >0.2."""
    density = complexity / max(1, lines)
    if complexity > 15 or density > 0.5:
        return "HIGH"
    if complexity > 8 or density > 0.2:
        return "MED"
    return "LOW"


def _aggregate_methods(methods: list[dict]) -> dict:
    """Compute aggregation stats over a list of method dicts."""
    total = len(methods)
    high = sum(1 for m in methods if _risk_level(int(m.get("complexity") or 0), _method_lines(m)) == "HIGH")
    med = sum(1 for m in methods if _risk_level(int(m.get("complexity") or 0), _method_lines(m)) == "MED")
    low = total - high - med
    avg_c = (
        round(sum(int(m.get("complexity") or 0) for m in methods) / total, 2)
        if total > 0
        else 0.0
    )
    return {"high_risk": high, "med_risk": med, "low_risk": low, "avg_complexity": avg_c}


@router.get("/{repo_id}/analysis/joern/methods")
async def joern_methods(
    repo_id: uuid.UUID,
    scope: str | None = Query(default=None, description="Path prefix filter, e.g. 'net/tcp/'"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    sort: str = Query(default="complexity", pattern="^(name|complexity|line|risk|density)$"),
    db: AsyncSession = Depends(get_db),
):
    """Get paginated methods/functions in the repo.

    Joern has no native pagination, so filtering/sorting/slicing is done in Python.
    """
    repo = await _get_repo_or_404(repo_id, db)
    joern = _joern()
    tool_path = _tool_path(repo)

    async with _joern_lock:
        try:
            await joern.prepare(AnalysisRequest(repo_local_path=tool_path))
            raw = await joern.method_list()
            # Joern returns error string when CPG is not loaded (e.g. fresh container).
            # Guard: only accept list[dict]; anything else → helpful 503.
            if not isinstance(raw, list) or (raw and not isinstance(raw[0], dict)):
                raise HTTPException(503, "Joern CPG 未加载，请先点击「重新构建索引」导入代码")
            all_methods: list[dict] = raw
        except httpx.ConnectError:
            raise HTTPException(503, "Joern service unavailable")
        except HTTPException:
            raise
        finally:
            await joern.cleanup(AnalysisRequest(repo_local_path=tool_path))

    # Scope filter
    if scope:
        all_methods = [m for m in all_methods if str(m.get("filename", "")).startswith(scope)]

    # Sort — risk and density are computed in Python since Joern has no native support
    if sort == "name":
        all_methods.sort(key=lambda m: str(m.get("name", "")))
    elif sort == "complexity":
        all_methods.sort(key=lambda m: int(m.get("complexity") or 0), reverse=True)
    elif sort == "risk":
        def _risk_key(m: dict) -> float:
            c = int(m.get("complexity") or 0)
            d = c / _method_lines(m)
            return c * (1 + d)
        all_methods.sort(key=_risk_key, reverse=True)
    elif sort == "density":
        all_methods.sort(
            key=lambda m: int(m.get("complexity") or 0) / _method_lines(m),
            reverse=True,
        )
    else:  # line
        all_methods.sort(key=lambda m: int(m.get("line") or 0))

    total = len(all_methods)
    aggregation = _aggregate_methods(all_methods)

    offset = (page - 1) * size
    page_methods = all_methods[offset: offset + size]

    return {
        "methods": page_methods,
        "total": total,
        "page": page,
        "size": size,
        "aggregation": aggregation,
    }


@router.get("/{repo_id}/analysis/stats")
async def analysis_stats(
    repo_id: uuid.UUID,
    scope: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Fast aggregated stats for the Pulse panel.

    When scope is None, reads from the latest AnalysisSnapshot first.
    Falls back to calling Joern if no snapshot exists or scope is set.
    """
    await _get_repo_or_404(repo_id, db)

    # Snapshot fast-path: only for full-repo (no scope)
    if not scope:
        snap_result = await db.execute(
            select(AnalysisSnapshot)
            .where(AnalysisSnapshot.repository_id == repo_id)
            .order_by(AnalysisSnapshot.created_at.desc())
            .limit(1)
        )
        snap = snap_result.scalar_one_or_none()
        if snap and snap.summary:
            s = snap.summary
            return {
                "total": s.get("total", 0),
                "high": s.get("high", 0),
                "med": s.get("med", 0),
                "low": s.get("total", 0) - s.get("high", 0) - s.get("med", 0),
                "avgComplexity": s.get("avgComplexity", 0),
            }

    # No snapshot or scope is set — query Joern
    repo = await _get_repo_or_404(repo_id, db)
    joern = _joern()
    tool_path = _tool_path(repo)

    async with _joern_lock:
        try:
            await joern.prepare(AnalysisRequest(repo_local_path=tool_path))
            raw = await joern.method_list()
            if not isinstance(raw, list) or (raw and not isinstance(raw[0], dict)):
                raise HTTPException(503, "Joern CPG 未加载，请先点击「重新构建索引」导入代码")
            all_methods: list[dict] = raw
        except httpx.ConnectError:
            raise HTTPException(503, "Joern service unavailable")
        except HTTPException:
            raise
        finally:
            await joern.cleanup(AnalysisRequest(repo_local_path=tool_path))

    if scope:
        all_methods = [m for m in all_methods if str(m.get("filename", "")).startswith(scope)]

    agg = _aggregate_methods(all_methods)
    return {
        "total": len(all_methods),
        "high": agg["high_risk"],
        "med": agg["med_risk"],
        "low": agg["low_risk"],
        "avgComplexity": agg["avg_complexity"],
    }


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

    async with _joern_lock:
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

    async with _joern_lock:
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

    async with _joern_lock:
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

    async with _joern_lock:
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

    async with _joern_lock:
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

    async with _joern_lock:
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

    async with _joern_lock:
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

    async with _joern_lock:
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
    3. LLM: translate to black-box test descriptions
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
    """Persist a risk-matrix snapshot for historical comparison.

    Idempotent within a 30-second window: if a snapshot with an identical
    summary (total, high, med, avgComplexity, sample_size) already exists
    for this repo in the last 30 s, returns the existing one instead of
    creating a duplicate.
    """
    await _get_repo_or_404(repo_id, db)

    # Deduplication window — protects against concurrent tab writes.
    # Key covers all summary fields that distinguish one snapshot from another:
    # total + high + med captures the full risk distribution;
    # avgComplexity detects complexity drift even when counts are equal;
    # sample_size distinguishes sampled vs complete snapshots of the same size.
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=30)
    total_val = body.summary.get("total")
    high_val = body.summary.get("high")
    if total_val is not None and high_val is not None:
        dedup_result = await db.execute(
            select(AnalysisSnapshot)
            .where(
                AnalysisSnapshot.repository_id == repo_id,
                AnalysisSnapshot.created_at >= cutoff,
            )
            .order_by(AnalysisSnapshot.created_at.desc())
            .limit(1)
        )
        recent = dedup_result.scalar_one_or_none()
        if recent and recent.summary:
            if (recent.summary.get("total") == total_val
                    and recent.summary.get("high") == high_val
                    and recent.summary.get("med") == body.summary.get("med")
                    and recent.summary.get("avgComplexity") == body.summary.get("avgComplexity")
                    and recent.summary.get("sample_size") == body.summary.get("sample_size")):
                return {
                    "id": str(recent.id),
                    "repository_id": str(recent.repository_id),
                    "summary": recent.summary,
                    "created_at": recent.created_at.isoformat() if recent.created_at else None,
                    "deduplicated": True,
                }

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
    summary = snap.summary or {}
    return {
        "id": str(snap.id),
        "repository_id": str(snap.repository_id),
        "risk_matrix": snap.risk_matrix,
        # is_sampled=True means risk_matrix is a paginated batch, not the complete repo set.
        # summary stats (total/high/med) are always complete — sourced from /stats endpoint.
        "is_sampled": bool(summary.get("is_sampled", False)),
        "sample_size": summary.get("sample_size"),
        "summary": summary,
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
        # delta is always summary-level (total/high/med/avgComplexity); method-level diff
        # is only meaningful when is_sampled=False on both snapshots.
        "delta": {
            "total_methods": (sum_to.get("total", 0) - sum_from.get("total", 0)),
            "high_risk": (sum_to.get("high", 0) - sum_from.get("high", 0)),
            "med_risk": (sum_to.get("med", 0) - sum_from.get("med", 0)),
            "avg_complexity": round(
                (sum_to.get("avgComplexity", 0) - sum_from.get("avgComplexity", 0)), 2
            ),
        },
        "from_is_sampled": bool(sum_from.get("is_sampled", False)),
        "to_is_sampled": bool(sum_to.get("is_sampled", False)),
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
    async with _joern_lock:
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
