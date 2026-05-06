"""Global analysis endpoints (cross-repo scope aggregation)."""

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.analysis_snapshot import AnalysisSnapshot
from app.models.repository import Repository
from app.models.task import AnalysisTask

router = APIRouter(prefix="/api/analysis", tags=["analysis-global"])


def _scope_path_from_target_spec(target_spec: Optional[dict]) -> str:
    """Extract a path prefix from a task's target_spec.

    Empty spec → "/" (whole repo).
    file_paths spec → common directory prefix of all listed paths.
    """
    if not target_spec:
        return "/"
    paths = target_spec.get("paths", [])
    if not paths:
        return "/"
    if len(paths) == 1:
        p = str(paths[0])
        last_slash = p.rfind("/")
        return p[: last_slash + 1] if last_slash >= 0 else "/"
    # Multi-path: common character prefix, truncated to last "/"
    common = ""
    for chars in zip(*[str(p) for p in paths]):
        if len(set(chars)) == 1:
            common += chars[0]
        else:
            break
    last_slash = common.rfind("/")
    return common[: last_slash + 1] if last_slash >= 0 else "/"


def _find_parent_scope(scope_path: str, all_paths: list) -> Optional[str]:
    """Return the longest proper prefix path that is a prefix of scope_path."""
    best: Optional[str] = None
    for p in all_paths:
        if p != scope_path and scope_path.startswith(p):
            if best is None or len(p) > len(best):
                best = p
    return best


@router.get("/scopes")
async def list_analysis_scopes(db: AsyncSession = Depends(get_db)):
    """List all analyzed scopes across repos for the global analysis dashboard."""
    # 1. Query all completed AnalysisTasks with their repo
    stmt = (
        select(AnalysisTask, Repository)
        .join(Repository, AnalysisTask.repository_id == Repository.id)
        .where(AnalysisTask.status == "completed")
        .order_by(AnalysisTask.created_at.desc())
    )
    rows = (await db.execute(stmt)).all()

    # 2. Group by (repo_id, scope_path) — accumulate tools + latest timestamp
    scope_map: dict = {}
    for task, repo in rows:
        scope_path = _scope_path_from_target_spec(task.target_spec or {})
        key = (str(repo.id), scope_path)
        if key not in scope_map:
            scope_map[key] = {
                "repo_id": str(repo.id),
                "repo_name": repo.name,
                "branch": repo.branch,
                "scope_path": scope_path,
                "tools_completed": set(),
                "last_analyzed_at": None,
            }
        for t in (task.tools or []):
            scope_map[key]["tools_completed"].add(str(t))
        if task.completed_at and (
            scope_map[key]["last_analyzed_at"] is None
            or task.completed_at > scope_map[key]["last_analyzed_at"]
        ):
            scope_map[key]["last_analyzed_at"] = task.completed_at

    # 3. Query latest AnalysisSnapshot per repo
    snap_stmt = (
        select(AnalysisSnapshot).order_by(
            AnalysisSnapshot.repository_id,
            AnalysisSnapshot.created_at.desc(),
        )
    )
    snap_rows = (await db.execute(snap_stmt)).scalars().all()
    latest_snap: dict = {}
    for snap in snap_rows:
        rid = str(snap.repository_id)
        if rid not in latest_snap:
            latest_snap[rid] = snap.summary

    # 4. Build per-repo path lists for parent_scope computation
    paths_by_repo: dict = {}
    for repo_id, scope_path in scope_map:
        paths_by_repo.setdefault(repo_id, []).append(scope_path)

    # 5. Build result list
    scopes = []
    for (repo_id, scope_path), data in scope_map.items():
        snap_summary = latest_snap.get(repo_id)
        risk_summary = None
        if snap_summary and scope_path == "/":
            risk_summary = {
                "total": snap_summary.get("total", 0),
                "high": snap_summary.get("high", 0),
                "med": snap_summary.get("med", 0),
            }

        last_ts = data["last_analyzed_at"]
        scopes.append({
            "repo_id": repo_id,
            "repo_name": data["repo_name"],
            "branch": data["branch"],
            "scope_path": scope_path,
            "tools_completed": sorted(data["tools_completed"]),
            "risk_summary": risk_summary,
            "last_analyzed_at": last_ts.isoformat() if last_ts else None,
            "parent_scope": _find_parent_scope(
                scope_path, paths_by_repo.get(repo_id, [])
            ),
        })

    scopes.sort(key=lambda s: (s["repo_name"], s["scope_path"]))
    return {"scopes": scopes}
