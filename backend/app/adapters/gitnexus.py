"""GitNexus knowledge-graph adapter.

IRON LAW: analyze() may ONLY do:
  (a) HTTP calls to the GitNexus bridge API
  (b) Response format conversion
No analysis logic (AST traversal, graph building, community detection).
"""

import asyncio
import logging
from collections import Counter
from collections.abc import AsyncIterator

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
_POLL_TIMEOUT = 600  # max seconds to wait for analysis


class GitNexusAdapter(BaseToolAdapter):
    def __init__(self, base_url: str = "http://gitnexus:7100"):
        self.base_url = base_url
        self._client: httpx.AsyncClient | None = None
        self._repo_name: str = ""

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(60, connect=10),
            )
        return self._client

    def name(self) -> str:
        return "gitnexus"

    def capabilities(self) -> list[ToolCapability]:
        return [
            ToolCapability.KNOWLEDGE_GRAPH,
            ToolCapability.AST_ANALYSIS,
            ToolCapability.DEPENDENCY_GRAPH,
        ]

    async def health_check(self) -> ToolHealth:
        try:
            resp = await self.client.get("/api/info")
            resp.raise_for_status()
            data = resp.json()
            return ToolHealth(
                is_healthy=True,
                container_status="running",
                version=data.get("version"),
            )
        except Exception as exc:
            return ToolHealth(
                is_healthy=False,
                container_status="error",
                last_check=str(exc),
            )

    async def prepare(self, request: AnalysisRequest) -> None:
        """Trigger GitNexus indexing for the repository."""
        tool_repo_path = to_tool_repo_path(
            request.repo_local_path,
            host_base_path=settings.repos_base_path,
            tool_base_path=settings.tool_repos_base_path,
        )
        resp = await self.client.post(
            "/api/analyze",
            json={"path": tool_repo_path},
        )
        resp.raise_for_status()
        job = resp.json()
        job_id = job["jobId"]
        logger.info("gitnexus: analysis job started: %s", job_id)

        elapsed = 0
        while elapsed < _POLL_TIMEOUT:
            await asyncio.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL

            status_resp = await self.client.get(f"/api/analyze/{job_id}")
            status = status_resp.json()

            if status["status"] == "complete":
                self._repo_name = status.get("repoName", "")
                logger.info("gitnexus: indexing complete for %s", self._repo_name)
                # Fire-and-forget embedding so semantic search can upgrade from BM25
                asyncio.ensure_future(self._trigger_embed())
                return
            if status["status"] == "failed":
                raise RuntimeError(
                    f"GitNexus indexing failed: {status.get('error', 'unknown')}"
                )

        raise RuntimeError("GitNexus indexing timed out")

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
