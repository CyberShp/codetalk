"""Lightweight GitNexus HTTP client adapter for Sprint 2.

Provides direct access to GitNexus REST endpoints for code graph,
process detection, community analysis, and code search.

This is the Sprint 2 lightweight client.  The full-stack adapter
(``GitNexusAdapter`` in ``gitnexus.py``) is still used by the
Docker-based pipeline and remains unchanged.
"""

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Timeout presets (seconds)
_INDEX_TIMEOUT = 60.0
_QUERY_TIMEOUT = 30.0


class GitNexusClient:
    """HTTP client for the GitNexus code-intelligence server."""

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = base_url or settings.gitnexus_base_url
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(_QUERY_TIMEOUT, connect=10),
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def index_repo(self, repo_path: str) -> dict[str, Any]:
        """Trigger GitNexus indexing for a repository.

        POST /api/index  {"path": repo_path}
        """
        try:
            resp = await self.client.post(
                "/api/index",
                json={"path": repo_path},
                timeout=_INDEX_TIMEOUT,
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            logger.info("GitNexusClient: indexing triggered for %s", repo_path)
            return data
        except httpx.HTTPStatusError as exc:
            logger.error(
                "GitNexusClient: index_repo HTTP %d: %s",
                exc.response.status_code,
                exc.response.text[:200],
            )
            raise
        except Exception as exc:
            logger.error("GitNexusClient: index_repo failed: %s", exc)
            raise

    async def get_graph(self, repo_path: str) -> dict[str, Any]:
        """Fetch the full code graph (nodes + edges).

        GET /api/graph?repo={repo_path}
        """
        try:
            resp = await self.client.get(
                "/api/graph",
                params={"repo": repo_path},
                timeout=_QUERY_TIMEOUT,
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            node_count = len(data.get("nodes", []))
            edge_count = len(data.get("edges", []))
            logger.info(
                "GitNexusClient: graph loaded -- %d nodes, %d edges",
                node_count,
                edge_count,
            )
            return data
        except httpx.HTTPStatusError as exc:
            logger.error(
                "GitNexusClient: get_graph HTTP %d: %s",
                exc.response.status_code,
                exc.response.text[:200],
            )
            raise
        except Exception as exc:
            logger.error("GitNexusClient: get_graph failed: %s", exc)
            raise

    async def get_processes(self, repo_path: str) -> list[dict[str, Any]]:
        """Fetch detected business processes.

        GET /api/processes?repo={repo_path}
        """
        try:
            resp = await self.client.get(
                "/api/processes",
                params={"repo": repo_path},
                timeout=_QUERY_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            processes: list[dict[str, Any]] = (
                data if isinstance(data, list) else data.get("processes", [])
            )
            logger.info("GitNexusClient: %d processes found", len(processes))
            return processes
        except httpx.HTTPStatusError as exc:
            logger.error(
                "GitNexusClient: get_processes HTTP %d: %s",
                exc.response.status_code,
                exc.response.text[:200],
            )
            raise
        except Exception as exc:
            logger.error("GitNexusClient: get_processes failed: %s", exc)
            raise

    async def get_communities(self, repo_path: str) -> list[dict[str, Any]]:
        """Fetch community/cluster detection results.

        GET /api/communities?repo={repo_path}
        """
        try:
            resp = await self.client.get(
                "/api/communities",
                params={"repo": repo_path},
                timeout=_QUERY_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            communities: list[dict[str, Any]] = (
                data if isinstance(data, list) else data.get("communities", [])
            )
            logger.info("GitNexusClient: %d communities found", len(communities))
            return communities
        except httpx.HTTPStatusError as exc:
            logger.error(
                "GitNexusClient: get_communities HTTP %d: %s",
                exc.response.status_code,
                exc.response.text[:200],
            )
            raise
        except Exception as exc:
            logger.error("GitNexusClient: get_communities failed: %s", exc)
            raise

    async def search(self, repo_path: str, query: str) -> list[dict[str, Any]]:
        """Search code within a repository.

        GET /api/search?repo={repo_path}&q={query}
        """
        try:
            resp = await self.client.get(
                "/api/search",
                params={"repo": repo_path, "q": query},
                timeout=_QUERY_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            results: list[dict[str, Any]] = (
                data if isinstance(data, list) else data.get("results", [])
            )
            logger.info(
                "GitNexusClient: search '%s' returned %d results",
                query,
                len(results),
            )
            return results
        except httpx.HTTPStatusError as exc:
            logger.error(
                "GitNexusClient: search HTTP %d: %s",
                exc.response.status_code,
                exc.response.text[:200],
            )
            raise
        except Exception as exc:
            logger.error("GitNexusClient: search failed: %s", exc)
            raise
