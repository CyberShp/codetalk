"""GitNexus knowledge-graph adapter.

IRON LAW: analyze() may ONLY do:
  (a) HTTP calls to the GitNexus bridge API
  (b) Response format conversion
No analysis logic (AST traversal, graph building, community detection).
"""

import asyncio
import logging
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import httpx

from app.config import settings
from app.utils.repo_paths import to_tool_repo_path

from .base import (
    AnalysisRequest,
    BaseToolAdapter,
    ToolCapability,
    ToolHealth,
    UnifiedResult,
)

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 2  # seconds between job status polls
_POLL_TIMEOUT = 1800  # max seconds to wait for analysis (30 min for large repos)


class GitNexusAdapter(BaseToolAdapter):
    _indexed_repo_by_path: dict[tuple[str, str], str] = {}
    _prepare_locks: dict[tuple[str, str, int], asyncio.Lock] = {}

    def __init__(self, base_url: str = "http://gitnexus:7100"):
        self.base_url = base_url
        self._client: httpx.AsyncClient | None = None
        self._repo_name: str = ""

    @classmethod
    def _prepare_lock_for(cls, base_url: str, tool_repo_path: str) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        key = (base_url, tool_repo_path, id(loop))
        lock = cls._prepare_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            cls._prepare_locks[key] = lock
        return lock

    @classmethod
    def clear_cached_repo(cls, base_url: str, tool_repo_path: str | None = None) -> None:
        if tool_repo_path is None:
            stale = [key for key in cls._indexed_repo_by_path if key[0] == base_url]
            for key in stale:
                cls._indexed_repo_by_path.pop(key, None)
            return
        cls._indexed_repo_by_path.pop((base_url, tool_repo_path), None)

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(60, connect=10),
                trust_env=False,
            )
        return self._client

    def name(self) -> str:
        return "gitnexus"

    @property
    def current_repo_name(self) -> str:
        return self._repo_name

    def capabilities(self) -> list[ToolCapability]:
        return [
            ToolCapability.KNOWLEDGE_GRAPH,
            ToolCapability.AST_ANALYSIS,
            ToolCapability.DEPENDENCY_GRAPH,
        ]

    async def health_check(self) -> ToolHealth:
        n_indexed = len(self._indexed_repo_by_path)
        try:
            resp = await self.client.get("/api/info")
            if resp.status_code < 500:
                data = resp.json() if resp.status_code < 400 else {}
                return ToolHealth(
                    is_healthy=True,
                    container_status="running",
                    version=data.get("version"),
                    indexed_repos=n_indexed,
                )
        except Exception:
            pass

        # Fallback: probe /api/analyze — even a 4xx proves GitNexus is reachable
        try:
            resp = await self.client.post("/api/analyze", json={})
            if resp.status_code < 500:
                return ToolHealth(
                    is_healthy=True,
                    container_status="running",
                    indexed_repos=n_indexed,
                )
            return ToolHealth(
                is_healthy=False,
                container_status="unhealthy",
                last_check=f"HTTP {resp.status_code}",
                indexed_repos=n_indexed,
            )
        except Exception as exc:
            return ToolHealth(
                is_healthy=False,
                container_status="error",
                last_check=str(exc),
                indexed_repos=n_indexed,
            )

    async def prepare(
        self,
        request: AnalysisRequest,
        on_progress: Callable[[int], Awaitable[None]] | None = None,
    ) -> None:
        """Trigger GitNexus indexing for the repository."""
        tool_repo_path = to_tool_repo_path(
            request.repo_local_path,
            host_base_path=settings.repos_base_path,
            tool_base_path=settings.tool_repos_base_path,
        )
        cache_key = (self.base_url, tool_repo_path)

        async with self._prepare_lock_for(self.base_url, tool_repo_path):
            cached_repo_name = self._indexed_repo_by_path.get(cache_key)
            if cached_repo_name and await self._repo_exists(cached_repo_name):
                self._repo_name = cached_repo_name
                logger.info(
                    "gitnexus: repo already indexed for %s, skipping analyze",
                    cached_repo_name,
                )
                return
            if cached_repo_name:
                self.clear_cached_repo(self.base_url, tool_repo_path)

            resp = await self.client.post(
                "/api/analyze",
                json={"path": tool_repo_path},
            )

            if resp.status_code == 409:
                body = resp.json() if resp.content else {}
                existing_job_id = body.get("jobId")
                if existing_job_id:
                    # A job is already running for this path — poll it
                    logger.info(
                        "gitnexus: 409 conflict — joining existing job %s", existing_job_id
                    )
                    job_id = existing_job_id
                else:
                    # Path may be covered by a parent-repo job already indexed
                    repo_name = body.get("repoName") or body.get("repo")
                    if repo_name:
                        logger.info(
                            "gitnexus: 409 conflict — repo already indexed as %s", repo_name
                        )
                        self._repo_name = repo_name
                        self._indexed_repo_by_path[cache_key] = repo_name
                        asyncio.ensure_future(self._trigger_embed())
                        return
                    raise RuntimeError(
                        "GitNexus 正在分析一个包含此路径的父项目，请等待该任务完成后再试"
                    )
            elif resp.is_error:
                resp.raise_for_status()
            else:
                job = resp.json()
                job_id = job["jobId"]
                logger.info("gitnexus: analysis job started: %s", job_id)

            elapsed = 0
            while elapsed < _POLL_TIMEOUT:
                await asyncio.sleep(_POLL_INTERVAL)
                elapsed += _POLL_INTERVAL

                status_resp = await self.client.get(f"/api/analyze/{job_id}")
                status = status_resp.json()

                if on_progress:
                    raw = next(
                        (status[k] for k in ("progress", "percentage", "percent")
                         if k in status and status[k] is not None),
                        None,
                    )
                    # GitNexus may return progress as dict e.g. {"current": 50, "total": 100}
                    if isinstance(raw, dict):
                        raw = raw.get("current") or raw.get("percent") or raw.get("value")
                    if isinstance(raw, (int, float)):
                        pct = min(99, int(raw))
                    elif isinstance(raw, str):
                        try:
                            pct = min(99, int(raw))
                        except ValueError:
                            pct = min(99, int(elapsed / _POLL_TIMEOUT * 100))
                    else:
                        pct = min(99, int(elapsed / _POLL_TIMEOUT * 100))
                    await on_progress(pct)

                if status["status"] == "complete":
                    self._repo_name = status.get("repoName", "") or Path(tool_repo_path).name
                    if not status.get("repoName"):
                        logger.warning(
                            "gitnexus: status missing repoName; falling back to dir name: %s",
                            self._repo_name,
                        )
                    self._indexed_repo_by_path[cache_key] = self._repo_name
                    logger.info("gitnexus: indexing complete for %s", self._repo_name)
                    # Fire-and-forget embedding so semantic search can upgrade from BM25
                    asyncio.ensure_future(self._trigger_embed())
                    return
                if status["status"] == "failed":
                    raise RuntimeError(
                        f"GitNexus indexing failed: {status.get('error', 'unknown')}"
                    )

            raise RuntimeError("GitNexus indexing timed out")

    async def _repo_exists(self, repo_name: str) -> bool:
        """Lightweight existence check so fresh adapter instances can reuse indexed repos."""
        try:
            resp = await self.client.get("/api/repos", params={"repo": repo_name}, timeout=10)
            if resp.status_code != 200:
                return False
            repos = resp.json().get("repos", [])
            names = {
                repo if isinstance(repo, str) else (repo.get("name") or repo.get("repo") or "")
                for repo in repos
            }
            return repo_name in names
        except Exception:
            return False

    async def _trigger_embed(self) -> None:
        """Start embedding job for the indexed repo (non-blocking).

        Embedding enables hybrid/semantic search; BM25 works without it.
        Only logs the result — never raises.
        """
        params: dict[str, str] = {}
        if self._repo_name:
            params["repo"] = self._repo_name
        try:
            resp = await self.client.post("/api/embed", params=params, timeout=10)
            if resp.status_code == 202:
                job_id = resp.json().get("jobId", "")
                logger.info("gitnexus: embedding job started: %s", job_id)
            else:
                logger.warning(
                    "gitnexus: embed returned unexpected status %d", resp.status_code
                )
        except Exception as exc:
            logger.warning("gitnexus: embed trigger failed (non-fatal): %s", exc)

    async def analyze(self, request: AnalysisRequest) -> UnifiedResult:
        """Fetch the knowledge graph + intelligence from GitNexus bridge API."""
        params: dict[str, str] = {}
        if self._repo_name:
            params["repo"] = self._repo_name

        resp = await self.client.get(
            "/api/graph",
            params=params,
            timeout=120,
        )
        resp.raise_for_status()
        graph = resp.json()

        nodes = graph.get("nodes", [])
        relationships = graph.get("relationships", [])

        processes, communities, intelligence = _structure_intelligence(
            nodes, relationships
        )

        logger.info(
            "gitnexus: graph loaded — %d nodes, %d edges, "
            "%d processes, %d communities",
            len(nodes),
            len(relationships),
            len(processes),
            len(communities),
        )

        return UnifiedResult(
            tool_name="gitnexus",
            capability=ToolCapability.KNOWLEDGE_GRAPH,
            data={
                "graph": {
                    "nodes": nodes,
                    "edges": relationships,
                    "processes": processes,
                    "communities": communities,
                    "intelligence": intelligence,
                },
            },
            raw_output=f"{len(nodes)} nodes, {len(relationships)} edges",
            metadata={
                "repo_name": self._repo_name,
                "node_count": len(nodes),
                "edge_count": len(relationships),
                "process_count": len(processes),
                "community_count": len(communities),
            },
        )

    async def stream_logs(self, run_id: str) -> AsyncIterator[str]:
        yield "gitnexus: indexing repository..."
        yield "gitnexus: building knowledge graph..."
        yield "gitnexus: completed"

    async def cleanup(self, request: AnalysisRequest) -> None:
        self._repo_name = ""


# ---------------------------------------------------------------------------
# Response format conversion helpers (no analysis logic — pure restructuring)
# ---------------------------------------------------------------------------


def _structure_intelligence(
    nodes: list[dict], relationships: list[dict]
) -> tuple[list[dict], list[dict], dict]:
    """Restructure flat graph into Process paths, Community groups, and metrics.

    This is response format conversion only: it reorganises data already
    returned by the GitNexus /api/graph endpoint into a shape the frontend
    can render as intelligence overlays.
    """
    # 1. Build STEP_IN_PROCESS lookup: processId → sorted steps
    process_steps: dict[str, list[dict]] = {}
    for edge in relationships:
        if edge.get("type") == "STEP_IN_PROCESS":
            pid = edge["targetId"]
            process_steps.setdefault(pid, []).append(
                {"symbolId": edge["sourceId"], "step": edge.get("step", 0)}
            )
    for steps in process_steps.values():
        steps.sort(key=lambda s: s["step"])

    # 2. Count community members from MEMBER_OF edges
    community_members: Counter[str] = Counter()
    for edge in relationships:
        if edge.get("type") == "MEMBER_OF":
            community_members[edge["targetId"]] += 1

    # 3. Extract Process / Community nodes, enrich with step/member data
    processes: list[dict] = []
    communities: list[dict] = []
    for node in nodes:
        if node.get("label") == "Process":
            processes.append({**node, "steps": process_steps.get(node["id"], [])})
        elif node.get("label") == "Community":
            communities.append(
                {**node, "memberCount": community_members.get(node["id"], 0)}
            )

    # 4. Edge-type distribution
    edge_counts: Counter[str] = Counter()
    for edge in relationships:
        edge_counts[edge.get("type", "UNKNOWN")] += 1

    # 5. Aggregate intelligence metrics
    cross_community = [p for p in processes if p.get("properties", {}).get("processType") == "cross_community"]
    cohesion_values = [
        c["properties"]["cohesion"]
        for c in communities
        if "cohesion" in c.get("properties", {})
    ]
    low_cohesion = [v for v in cohesion_values if v < 0.5]

    intelligence: dict = {
        "edge_types": dict(edge_counts.most_common()),
        "process_summary": {
            "total": len(processes),
            "cross_community": len(cross_community),
            "avg_steps": (
                sum(p.get("properties", {}).get("stepCount", 0) for p in processes) / len(processes)
                if processes
                else 0
            ),
        },
        "community_summary": {
            "total": len(communities),
            "avg_cohesion": sum(cohesion_values) / len(cohesion_values) if cohesion_values else 0,
            "low_cohesion_count": len(low_cohesion),
        },
    }

    return processes, communities, intelligence
