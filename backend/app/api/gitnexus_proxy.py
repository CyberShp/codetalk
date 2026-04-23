"""Proxy endpoints for GitNexus operations.

Used by the frontend to:
- Fetch function-level code slices on node click (/file)
- Search the knowledge graph by symbol (/search)
- Execute raw Cypher queries (/query)
- List all processes (/processes)
- Fetch single process detail (/process)
- List all clusters/communities (/clusters)
- Fetch single cluster detail (/cluster)
- Analyze impact/blast radius via Cypher composition (/impact)
- Regex code search (/grep)
- List indexed repos (/repos)

Iron law: pure HTTP proxy + format pass-through, zero analysis logic.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

import httpx

from app.config import settings

router = APIRouter(prefix="/api/gitnexus", tags=["gitnexus"])


def _coerce_line_number(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    return None


def _coerce_content(payload: dict) -> str:
    raw = payload.get("content")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        return "\n".join(str(item) for item in raw)
    if raw is None:
        return ""
    return str(raw)


def _normalize_file_slice(
    payload: dict,
    *,
    requested_path: str,
    requested_start_line: int | None,
    requested_end_line: int | None,
) -> dict[str, object]:
    content = _coerce_content(payload)
    line_count = len(content.splitlines()) if content else 0

    start_line = (
        _coerce_line_number(payload.get("startLine"))
        or _coerce_line_number(payload.get("start_line"))
        or _coerce_line_number(payload.get("lineStart"))
        or _coerce_line_number(payload.get("line_start"))
        or requested_start_line
        or 1
    )
    end_line = (
        _coerce_line_number(payload.get("endLine"))
        or _coerce_line_number(payload.get("end_line"))
        or _coerce_line_number(payload.get("lineEnd"))
        or _coerce_line_number(payload.get("line_end"))
        or requested_end_line
    )
    if end_line is None:
        end_line = start_line + max(line_count - 1, 0)

    total_lines = (
        _coerce_line_number(payload.get("totalLines"))
        or _coerce_line_number(payload.get("total_lines"))
        or _coerce_line_number(payload.get("lineCount"))
        or _coerce_line_number(payload.get("line_count"))
        or line_count
    )

    actual_path = payload.get("actualPath") or payload.get("actual_path") or payload.get("path") or payload.get("filePath") or payload.get("file_path") or requested_path

    return {
        "content": content,
        "startLine": start_line,
        "endLine": end_line,
        "totalLines": total_lines,
        "actualPath": str(actual_path),
    }


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
            return _normalize_file_slice(
                resp.json(),
                requested_path=path,
                requested_start_line=start_line,
                requested_end_line=end_line,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


class SearchRequest(BaseModel):
    query: str
    repo: str | None = None
    mode: str = "hybrid"  # "hybrid" | "semantic" | "bm25"
    limit: int = 10
    enrich: bool = True


@router.post("/search")
async def search_knowledge_graph(body: SearchRequest):
    """Proxy to GitNexus POST /api/search — symbol search in knowledge graph.

    Pure proxy: forward query params + body, return GitNexus response as-is.
    """
    params: dict[str, str] = {}
    if body.repo:
        params["repo"] = body.repo

    payload = {
        "query": body.query,
        "mode": body.mode,
        "limit": body.limit,
        "enrich": body.enrich,
    }

    try:
        async with httpx.AsyncClient(
            base_url=settings.gitnexus_base_url,
            timeout=30,
        ) as client:
            resp = await client.post("/api/search", params=params, json=payload)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


class CypherRequest(BaseModel):
    cypher: str
    repo: str | None = None


@router.post("/query")
async def cypher_query(body: CypherRequest):
    """Proxy to GitNexus POST /api/query — execute raw Cypher query."""
    params: dict[str, str] = {}
    if body.repo:
        params["repo"] = body.repo

    try:
        async with httpx.AsyncClient(
            base_url=settings.gitnexus_base_url, timeout=30
        ) as client:
            resp = await client.post("/api/query", params=params, json={"cypher": body.cypher})
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/processes")
async def list_processes(repo: str | None = Query(None)):
    """Proxy to GitNexus GET /api/processes — list all processes."""
    params: dict[str, str] = {}
    if repo:
        params["repo"] = repo

    try:
        async with httpx.AsyncClient(
            base_url=settings.gitnexus_base_url, timeout=30
        ) as client:
            resp = await client.get("/api/processes", params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/process")
async def get_process(
    name: str = Query(...),
    repo: str | None = Query(None),
):
    """Proxy to GitNexus GET /api/process — single process detail."""
    params: dict[str, str] = {"name": name}
    if repo:
        params["repo"] = repo

    try:
        async with httpx.AsyncClient(
            base_url=settings.gitnexus_base_url, timeout=30
        ) as client:
            resp = await client.get("/api/process", params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/clusters")
async def list_clusters(repo: str | None = Query(None)):
    """Proxy to GitNexus GET /api/clusters — list all clusters/communities."""
    params: dict[str, str] = {}
    if repo:
        params["repo"] = repo

    try:
        async with httpx.AsyncClient(
            base_url=settings.gitnexus_base_url, timeout=30
        ) as client:
            resp = await client.get("/api/clusters", params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/cluster")
async def get_cluster(
    name: str = Query(...),
    repo: str | None = Query(None),
):
    """Proxy to GitNexus GET /api/cluster — single cluster detail."""
    params: dict[str, str] = {"name": name}
    if repo:
        params["repo"] = repo

    try:
        async with httpx.AsyncClient(
            base_url=settings.gitnexus_base_url, timeout=30
        ) as client:
            resp = await client.get("/api/cluster", params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


class ImpactRequest(BaseModel):
    target: str                  # Symbol name (function/class/method)
    direction: str = "both"      # "upstream" | "downstream" | "both"
    depth: int = 3               # Traversal depth (clamped 1-5)
    limit: int = 30              # Max items per depth layer
    repo: str | None = None


@router.post("/impact")
async def analyze_impact(body: ImpactRequest):
    """Compose per-depth Cypher queries and proxy to GitNexus /api/query.

    Returns results grouped by depth layer so the frontend can show
    direct (depth=1) vs transitive (depth=2,3) callers separately.

    GitNexus uses Kùzu (not Neo4j).  Key dialect differences:
    - All edges share label ``CodeRelation``; edge type is a ``type`` property.
    - Variable-length paths use ``(r, _ | WHERE r.type = "CALLS")``.
    - Parameter binding requires ``WHERE n.name = $p``, not ``{name: $p}``.
    - Response JSON key is ``result`` (singular).
    """
    depth = max(1, min(body.depth, 5))
    limit = max(1, min(body.limit, 100))
    target_name = body.target

    cypher_params = {"target_name": target_name}
    params: dict[str, str] = {}
    if body.repo:
        params["repo"] = body.repo

    directions: list[str] = []
    if body.direction in ("upstream", "both"):
        directions.append("upstream")
    if body.direction in ("downstream", "both"):
        directions.append("downstream")

    # Build per-depth queries for each direction.
    # Uses Kùzu recursive-rel filter with exact depth range *d..d.
    # First query is slow (cold Kùzu cache), subsequent queries are fast (<100ms).
    tasks: list[tuple[str, int, str]] = []  # (direction, depth, cypher)
    for d in range(1, depth + 1):
        calls_filter = f'[:CodeRelation*{d}..{d} (r, _ | WHERE r.type = "CALLS")]'
        for direction in directions:
            if direction == "upstream":
                cypher = (
                    f"MATCH (caller)-{calls_filter}->(target) "
                    "WHERE target.name = $target_name "
                    "RETURN DISTINCT caller.name AS name, caller.filePath AS filePath, "
                    f"caller.startLine AS startLine, caller.endLine AS endLine LIMIT {limit}"
                )
            else:
                cypher = (
                    f"MATCH (source)-{calls_filter}->(callee) "
                    "WHERE source.name = $target_name "
                    "RETURN DISTINCT callee.name AS name, callee.filePath AS filePath, "
                    f"callee.startLine AS startLine, callee.endLine AS endLine LIMIT {limit}"
                )
            tasks.append((direction, d, cypher))

    # Execute all queries
    results: dict[str, list] = {d: [] for d in directions}
    try:
        async with httpx.AsyncClient(
            base_url=settings.gitnexus_base_url, timeout=60
        ) as client:
            for direction, d, cypher in tasks:
                resp = await client.post(
                    "/api/query",
                    params=params,
                    json={"cypher": cypher, "parameters": cypher_params},
                )
                resp.raise_for_status()
                raw = resp.json().get("result", [])
                # Filter out non-function nodes (File, Macro, etc.)
                items = [
                    r for r in raw
                    if r.get("startLine") is not None
                ]
                results[direction].append({
                    "depth": d,
                    "items": items,
                    "total": len(items),
                    "limited": len(raw) >= limit,
                })
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code, detail=exc.response.text
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return {"target": target_name, "depth": depth, **results}


@router.get("/grep")
async def grep_code(
    pattern: str = Query(..., description="Regex pattern"),
    repo: str | None = Query(None),
    glob: str | None = Query(None, description="File glob filter, e.g. *.py"),
):
    """Proxy to GitNexus GET /api/grep — regex code search."""
    params: dict[str, str] = {"pattern": pattern}
    if repo:
        params["repo"] = repo
    if glob:
        params["glob"] = glob

    try:
        async with httpx.AsyncClient(
            base_url=settings.gitnexus_base_url, timeout=30
        ) as client:
            resp = await client.get("/api/grep", params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code, detail=exc.response.text
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/repos")
async def list_repos(repo: str | None = Query(None)):
    """Proxy to GitNexus GET /api/repos — list indexed repositories."""
    params: dict[str, str] = {}
    if repo:
        params["repo"] = repo

    try:
        async with httpx.AsyncClient(
            base_url=settings.gitnexus_base_url, timeout=30
        ) as client:
            resp = await client.get("/api/repos", params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code, detail=exc.response.text
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
