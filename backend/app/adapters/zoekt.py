"""Zoekt code-search adapter.

IRON LAW: analyze() may ONLY do:
  (a) HTTP calls to the Zoekt API
  (b) Response format conversion
No text search, regex matching, result ranking, or any analysis logic.
"""

import base64
import logging
import os
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

DOCKER_SOCKET = "/var/run/docker.sock"
_INDEX_TIMEOUT = 300  # seconds — large repos can take a while


class ZoektAdapter(BaseToolAdapter):
    def __init__(
        self,
        base_url: str = "http://zoekt:6070",
        container_name: str = "codetalk-zoekt-1",
    ):
        self.base_url = base_url
        self.container_name = container_name
        self._client: httpx.AsyncClient | None = None
        self._indexed_repo_name: str = ""

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(30, connect=10),
            )
        return self._client

    def _docker_client(self) -> httpx.AsyncClient:
        """Return an httpx client that speaks Docker Engine API over the Unix socket.

        Mirrors the pattern already used in component_manager — no extra deps needed.
        """
        transport = httpx.AsyncHTTPTransport(uds=DOCKER_SOCKET)
        return httpx.AsyncClient(
            transport=transport,
            base_url="http://localhost",
            timeout=httpx.Timeout(_INDEX_TIMEOUT, connect=10),
        )

    def name(self) -> str:
        return "zoekt"

    def capabilities(self) -> list[ToolCapability]:
        return [ToolCapability.CODE_SEARCH]

    async def health_check(self) -> ToolHealth:
        try:
            resp = await self.client.get("/healthz")
            if resp.status_code == 200:
                return ToolHealth(is_healthy=True, container_status="running")
            return ToolHealth(
                is_healthy=False,
                container_status="unhealthy",
                last_check=f"HTTP {resp.status_code}",
            )
        except Exception as exc:
            return ToolHealth(
                is_healthy=False,
                container_status="error",
                last_check=str(exc),
            )

    async def prepare(self, request: AnalysisRequest) -> None:
        """Index the repository via docker exec zoekt-index inside the zoekt container.

        Zoekt names the repo after the directory basename — there is no -name flag.
        In production repo_local_path is /data/repos/<uuid>, so the index key is the UUID.
        The human-readable repo_name option is kept in metadata only.
        """
        repo_path = request.repo_local_path
        # Zoekt uses the directory basename as the repo name — we must match this.
        index_key = os.path.basename(repo_path.rstrip("/"))
        display_name = request.options.get("repo_name") or index_key

        # Skip re-indexing if already indexed (unless caller sets force_reindex)
        if not request.options.get("force_reindex"):
            if await self._is_indexed(index_key):
                logger.info(
                    "zoekt: repo '%s' already indexed, skipping", index_key
                )
                self._indexed_repo_name = index_key
                self._display_name = display_name
                return

        logger.info(
            "zoekt: indexing '%s' (display: '%s') at %s",
            index_key, display_name, repo_path,
        )
        await self._exec_index(repo_path)

        # Confirm the index actually appeared
        if not await self._is_indexed(index_key):
            raise RuntimeError(
                f"zoekt: zoekt-index exited cleanly but repo '{index_key}' "
                "is not visible in /api/list — check container logs"
            )

        self._indexed_repo_name = index_key
        self._display_name = display_name
        logger.info("zoekt: indexing complete for '%s'", index_key)

    async def _is_indexed(self, repo_name: str) -> bool:
        """Return True if repo_name appears in Zoekt's /api/list.

        Zoekt JSON API uses POST with JSON body {"Q": "..."}.
        Response: {"List": {"Repos": [{"Repository": {"Name": "..."}}]}}
        """
        try:
            resp = await self.client.post(
                "/api/list", json={"Q": f"repo:{repo_name}"}
            )
            resp.raise_for_status()
            repos = (resp.json().get("List", {}).get("Repos") or [])
            return any(
                r.get("Repository", {}).get("Name") == repo_name for r in repos
            )
        except Exception:
            return False

    async def _exec_index(self, repo_path: str) -> None:
        """Run zoekt-index inside the running zoekt container via Docker Engine API.

        zoekt-index does not accept a -name flag; it derives the repo name from
        the directory basename automatically.  Uses the same httpx-over-UDS pattern
        as component_manager — no extra dependencies needed.
        """
        cmd = ["zoekt-index", "-index", "/data/index", repo_path]

        async with self._docker_client() as docker:
            # Step 1: create exec instance
            create_resp = await docker.post(
                f"/containers/{self.container_name}/exec",
                json={
                    "Cmd": cmd,
                    "AttachStdout": True,
                    "AttachStderr": True,
                },
            )
            if create_resp.status_code == 404:
                raise RuntimeError(
                    f"zoekt container '{self.container_name}' not found — "
                    "is it running? (`docker compose up zoekt`)"
                )
            create_resp.raise_for_status()
            exec_id = create_resp.json()["Id"]

            # Step 2: start exec (blocking — waits for the command to finish)
            start_resp = await docker.post(
                f"/exec/{exec_id}/start",
                json={"Detach": False, "Tty": False},
                headers={"Content-Type": "application/json"},
            )
            # Body is a Docker multiplexed stream; we consume it but only need exit code
            await start_resp.aread()

            # Step 3: inspect exit code
            inspect_resp = await docker.get(f"/exec/{exec_id}/json")
            inspect_resp.raise_for_status()
            exit_code = inspect_resp.json().get("ExitCode", -1)

        if exit_code != 0:
            raise RuntimeError(
                f"zoekt-index failed with exit code {exit_code} "
                f"for repo path '{repo_path}'"
            )

    async def analyze(self, request: AnalysisRequest) -> UnifiedResult:
        """Search via POST /api/search — HTTP call + response format conversion only.

        Zoekt JSON API uses POST with JSON body {"Q": "...", "Num": N}.
        """
        query = request.options.get("query", "")
        if not query:
            raise ValueError("zoekt analyze requires options.query")

        # Build scoped query: constrain to the indexed repo
        scoped_query = query
        if self._indexed_repo_name:
            scoped_query = f"repo:{self._indexed_repo_name} {query}"

        # Further constrain by target files if provided
        if request.target_files:
            file_filters = " ".join(f"file:{f}" for f in request.target_files)
            scoped_query = f"{file_filters} {scoped_query}"

        num = request.options.get("num", 50)
        resp = await self.client.post(
            "/api/search",
            json={"Q": scoped_query, "Num": num},
        )
        resp.raise_for_status()
        raw = resp.json()

        search_results = _convert_search_results(raw)

        return UnifiedResult(
            tool_name="zoekt",
            capability=ToolCapability.CODE_SEARCH,
            data={"search_results": search_results, "query": query},
            raw_output=resp.text,
            metadata={
                "index_key": self._indexed_repo_name,
                "display_name": getattr(self, "_display_name", self._indexed_repo_name),
                "result_count": len(search_results),
                "scoped_query": scoped_query,
            },
        )

    async def stream_logs(self, run_id: str) -> AsyncIterator[str]:
        yield "zoekt: indexing repository..."
        yield "zoekt: search index ready"
        yield "zoekt: completed"

    async def cleanup(self, request: AnalysisRequest) -> None:
        self._indexed_repo_name = ""
        self._display_name = ""


# ---------------------------------------------------------------------------
# Response format conversion — no analysis logic, pure reshaping
# ---------------------------------------------------------------------------


def _convert_search_results(raw: dict) -> list[dict]:
    """Reshape Zoekt /api/search response into a flat list.

    Zoekt returns:
      {"Result": {"Files": [{"FileName", "Repository", "LineMatches": [...]}]}}

    LineMatches[].Line is base64-encoded — we decode it here.

    We return:
      [{"file", "repo", "matches": [{"line_number", "line_content"}]}]
    """
    files = (raw.get("Result") or {}).get("Files") or []
    output = []
    for f in files:
        matches = [
            {
                "line_number": lm.get("LineNumber", 0),
                "line_content": _decode_line(lm.get("Line", "")),
            }
            for lm in (f.get("LineMatches") or [])
        ]
        output.append(
            {
                "file": f.get("FileName", ""),
                "repo": f.get("Repository", ""),
                "matches": matches,
            }
        )
    return output


def _decode_line(line: str) -> str:
    """Decode a base64-encoded line from Zoekt's API response."""
    try:
        return base64.b64decode(line).decode("utf-8", errors="replace")
    except Exception:
        return line
