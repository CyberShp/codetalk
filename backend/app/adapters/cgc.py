"""CodeGraphContext (CGC) HTTP client adapter.

CGC runs as a separate HTTP daemon (`cgc api start --host 127.0.0.1 --port <PORT>`).
This module provides a typed HTTP client and a BaseToolAdapter wrapper.

IRON LAW: analyze() may ONLY do:
  (a) HTTP calls to the CGC Gateway API
  (b) Response format conversion
No analysis logic (graph traversal, ranking, inference) allowed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import httpx

from app.config import settings
from app.adapters.base import (
    AnalysisRequest,
    BaseToolAdapter,
    ToolCapability,
    ToolHealth,
    UnifiedResult,
)

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT: float = 30.0
_INDEX_POLL_INTERVAL: int = 3  # seconds
_INDEX_POLL_TIMEOUT: int = 600  # seconds


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class CGCUnavailable(RuntimeError):
    """CGC daemon is unreachable (network error or not started)."""


class CGCIndexFailed(RuntimeError):
    """Repository indexing job failed."""


class CGCQueryError(RuntimeError):
    """Tool call returned an error response from the CGC Gateway."""


# ---------------------------------------------------------------------------
# Low-level HTTP client
# ---------------------------------------------------------------------------


class CGCClient:
    """HTTP client for the CGC Gateway API (``cgc api start``)."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = base_url or settings.cgc_base_url
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout, connect=5),
                trust_env=False,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def is_healthy(self) -> bool:
        """Return True if the CGC Gateway responds with a success status."""
        try:
            resp = await self.client.get("/api/v1/status")
            data = resp.json() if resp.status_code < 400 else {}
            return data.get("status") == "ok"
        except Exception as exc:
            logger.debug("CGCClient: health check failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    async def index_repo(
        self,
        path: str,
        is_dependency: bool = False,
        repo_name: str | None = None,
    ) -> str:
        """Start indexing *path* and return the background job ID.

        Raises:
            CGCUnavailable: on network error.
            CGCQueryError: if the gateway returns an error response.
        """
        args: dict = {"path": path, "is_dependency": is_dependency}
        if repo_name:
            args["repo_name"] = repo_name
        result = await self._call_tool("add_code_to_graph", args)
        job_id = result.get("job_id") if isinstance(result, dict) else None
        if not job_id:
            raise CGCQueryError(f"add_code_to_graph returned no job_id: {result!r}")
        return job_id

    async def wait_for_index(self, job_id: str, timeout: int = _INDEX_POLL_TIMEOUT) -> bool:
        """Poll *job_id* until the index job completes or times out.

        Returns True on success.
        Raises CGCIndexFailed on job failure.
        Raises asyncio.TimeoutError on timeout.
        """
        elapsed = 0
        while elapsed < timeout:
            await asyncio.sleep(_INDEX_POLL_INTERVAL)
            elapsed += _INDEX_POLL_INTERVAL
            result = await self._call_tool("check_job_status", {"job_id": job_id})
            # CGC returns {"success": True, "job": {"status": "...", ...}}
            # Fall back to flat dict for backward compatibility with test mocks.
            if isinstance(result, dict):
                job = result.get("job", result)
                status = job.get("status", "") if isinstance(job, dict) else ""
                error_info = (job.get("error") or result.get("error") or "unknown") if isinstance(job, dict) else "unknown"
            else:
                status = ""
                error_info = "unknown"
            if status == "completed":
                return True
            if status == "failed":
                raise CGCIndexFailed(f"CGC index job {job_id} failed: {error_info}")
        raise asyncio.TimeoutError(f"CGC indexing timed out after {timeout}s (job={job_id})")

    # ------------------------------------------------------------------
    # Relationship queries (via analyze_code_relationships)
    # ------------------------------------------------------------------

    async def find_callers(self, func_name: str, repo_path: str | None = None, depth: int | None = None) -> list:
        """Return list of callers of *func_name*."""
        return await self._analyze("find_callers", func_name, repo_path=repo_path, depth=depth)

    async def find_callees(self, func_name: str, repo_path: str | None = None, depth: int | None = None) -> list:
        """Return list of functions called by *func_name*."""
        return await self._analyze("find_callees", func_name, repo_path=repo_path, depth=depth)

    async def call_chain(self, from_func: str, to_func: str, repo_path: str | None = None) -> dict:
        """Return call-chain data from *from_func* to *to_func*."""
        target = f"{from_func}:{to_func}"
        result = await self._analyze("call_chain", target, repo_path=repo_path)
        return result if isinstance(result, dict) else {"chain": result}

    async def module_deps(self, target: str, repo_path: str | None = None) -> dict:
        """Return module dependency data for *target* (e.g. a repo root path)."""
        result = await self._analyze("module_deps", target, repo_path=repo_path)
        return result if isinstance(result, dict) else {"deps": result}

    async def find_complexity(self, repo_path: str | None = None, threshold: int = 10) -> list:
        """Return functions whose cyclomatic complexity meets or exceeds *threshold*.

        *repo_path* is used as the analysis target; pass the repo root path.
        """
        target = repo_path or "."
        raw = await self._analyze("find_complexity", target, repo_path=repo_path)
        items: list = raw if isinstance(raw, list) else (
            raw.get("results", []) if isinstance(raw, dict) else []
        )
        return [item for item in items if _complexity_value(item) >= threshold]

    # ------------------------------------------------------------------
    # Code search
    # ------------------------------------------------------------------

    async def find_code(self, query: str, repo_path: str | None = None) -> list:
        """Search code by keyword / phrase and return matching snippets."""
        args: dict = {"query": query}
        if repo_path:
            args["repo_path"] = repo_path
        result = await self._call_tool("find_code", args)
        if isinstance(result, list):
            return result
        return result.get("results", result.get("matches", [])) if isinstance(result, dict) else []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _analyze(
        self,
        query_type: str,
        target: str,
        repo_path: str | None = None,
        context: str | None = None,
        depth: int | None = None,
    ) -> list | dict:
        args: dict = {"query_type": query_type, "target": target}
        if repo_path:
            args["repo_path"] = repo_path
        if context:
            args["context"] = context
        if depth is not None:
            args["depth"] = depth
        raw = await self._call_tool("analyze_code_relationships", args)
        # CGC wraps: {"success": True, "query_type": ..., "results": <list|dict>}
        # Single-key unwrap in _call_tool never fires on this multi-key response.
        if isinstance(raw, dict) and "results" in raw:
            return raw["results"]
        return raw

    async def _call_tool(self, name: str, arguments: dict) -> list | dict:
        """POST /api/v1/tools/call and return the ``data`` payload.

        Raises:
            CGCUnavailable: on network / connection error.
            CGCQueryError: if the response body contains status=error.
        """
        try:
            resp = await self.client.post(
                "/api/v1/tools/call",
                json={"name": name, "arguments": arguments},
            )
        except httpx.RequestError as exc:
            raise CGCUnavailable(f"CGC daemon unreachable: {exc}") from exc

        try:
            body = resp.json()
        except Exception:
            raise CGCQueryError(
                f"CGC returned non-JSON response (HTTP {resp.status_code})"
            )

        if body.get("status") == "error" or resp.is_error:
            raise CGCQueryError(
                f"CGC tool '{name}' returned error: {body.get('error', resp.status_code)}"
            )

        data = body.get("data", body)
        # Unwrap common single-key dict wrappers (e.g. {"results": [...]})
        if isinstance(data, dict) and len(data) == 1:
            (_, value) = next(iter(data.items()))
            if isinstance(value, (list, dict)):
                return value
        return data


# ---------------------------------------------------------------------------
# BaseToolAdapter wrapper
# ---------------------------------------------------------------------------


class CGCAdapter(BaseToolAdapter):
    """BaseToolAdapter wrapper around CGCClient for the adapter registry."""

    def __init__(self, base_url: str | None = None) -> None:
        self._cgc = CGCClient(base_url=base_url)

    def name(self) -> str:
        return "cgc"

    def capabilities(self) -> list[ToolCapability]:
        return [
            ToolCapability.CODE_SEARCH,
            ToolCapability.CALL_GRAPH,
            ToolCapability.DEPENDENCY_GRAPH,
            ToolCapability.KNOWLEDGE_GRAPH,
        ]

    async def health_check(self) -> ToolHealth:
        healthy = await self._cgc.is_healthy()
        return ToolHealth(
            is_healthy=healthy,
            container_status="running" if healthy else "unreachable",
        )

    async def prepare(self, request: AnalysisRequest) -> None:
        """Index the repository before analysis."""
        job_id = await self._cgc.index_repo(request.repo_local_path)
        await self._cgc.wait_for_index(job_id)

    async def analyze(self, request: AnalysisRequest) -> UnifiedResult:
        """Run complexity + module-dependency analysis on the repository."""
        path = request.repo_local_path
        complexity = await self._cgc.find_complexity(repo_path=path)
        deps = await self._cgc.module_deps(target=path, repo_path=path)
        return UnifiedResult(
            tool_name=self.name(),
            capability=ToolCapability.CALL_GRAPH,
            data={"complexity": complexity, "module_deps": deps},
            raw_output=f"{len(complexity)} high-complexity functions",
        )

    async def stream_logs(self, run_id: str) -> AsyncIterator[str]:
        yield "cgc: indexing repository..."
        yield "cgc: building code graph..."
        yield "cgc: completed"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _complexity_value(item: dict) -> int:
    """Extract cyclomatic complexity from a result item (tolerates key variants)."""
    for key in ("cyclomatic_complexity", "complexity", "value"):
        v = item.get(key)
        if isinstance(v, (int, float)):
            return int(v)
    return 0
