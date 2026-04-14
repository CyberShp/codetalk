"""GitNexus knowledge-graph adapter.

IRON LAW: analyze() may ONLY do:
  (a) HTTP calls to the GitNexus bridge API
  (b) Response format conversion
No analysis logic (AST traversal, graph building, community detection).
"""

import asyncio
import logging
from collections.abc import AsyncIterator

import httpx

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
        resp = await self.client.post(
            "/api/analyze",
            json={"path": request.repo_local_path},
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
                return
            if status["status"] == "failed":
                raise RuntimeError(
                    f"GitNexus indexing failed: {status.get('error', 'unknown')}"
                )

        raise RuntimeError("GitNexus indexing timed out")

    async def analyze(self, request: AnalysisRequest) -> UnifiedResult:
        """Fetch the knowledge graph from GitNexus bridge API."""
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

        logger.info(
            "gitnexus: graph loaded — %d nodes, %d edges",
            len(nodes),
            len(relationships),
        )

        return UnifiedResult(
            tool_name="gitnexus",
            capability=ToolCapability.KNOWLEDGE_GRAPH,
            data={
                "graph": {
                    "nodes": nodes,
                    "edges": relationships,
                },
            },
            raw_output=f"{len(nodes)} nodes, {len(relationships)} edges",
            metadata={
                "repo_name": self._repo_name,
                "node_count": len(nodes),
                "edge_count": len(relationships),
            },
        )

    async def stream_logs(self, run_id: str) -> AsyncIterator[str]:
        yield "gitnexus: indexing repository..."
        yield "gitnexus: building knowledge graph..."
        yield "gitnexus: completed"

    async def cleanup(self, request: AnalysisRequest) -> None:
        self._repo_name = ""
