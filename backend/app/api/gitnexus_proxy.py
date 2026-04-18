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
            return resp.json()
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
    repo: str | None = None


@router.post("/impact")
async def analyze_impact(body: ImpactRequest):
    """Compose Cypher blast-radius query and proxy to GitNexus /api/query."""
    depth = max(1, min(body.depth, 5))
    target_name = body.target

    # Cypher parameterisation — $target_name is resolved by Neo4j, never
    # interpolated into the query string, preventing injection.
    cypher_params = {"target_name": target_name}
    queries: dict[str, str] = {}

    if body.direction in ("upstream", "both"):
        queries["upstream"] = (
            f"MATCH path=(caller)-[:CALLS*1..{depth}]->"
            "(target {name: $target_name}) "
            "RETURN nodes(path) AS nodes, relationships(path) AS rels"
        )

    if body.direction in ("downstream", "both"):
        queries["downstream"] = (
            "MATCH path=(source {name: $target_name})"
            f"-[:CALLS*1..{depth}]->(callee) "
            "RETURN nodes(path) AS nodes, relationships(path) AS rels"
        )

    params: dict[str, str] = {}
    if body.repo:
        params["repo"] = body.repo

    results: dict[str, list] = {}
    try:
        async with httpx.AsyncClient(
            base_url=settings.gitnexus_base_url, timeout=30
        ) as client:
            for direction, cypher in queries.items():
                resp = await client.post(
                    "/api/query",
                    params=params,
                    json={"cypher": cypher, "parameters": cypher_params},
                )
                resp.raise_for_status()
                results[direction] = resp.json().get("results", [])
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
