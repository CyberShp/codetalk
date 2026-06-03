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
import os
import re
import shutil
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Callable

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


def _default_cgc_python() -> str | None:
    configured = (settings.cgc_cli_python or "").strip()
    if configured:
        return configured

    env_value = os.environ.get("CGC_CLI_PYTHON", "").strip()
    if env_value:
        return env_value

    candidates = [
        Path(r"D:\coworkers\cgc-venv\Scripts\python.exe"),
        Path(r"D:\coworkers\cgc-venv-throwaway\Scripts\python.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    cgc_bin = shutil.which("cgc")
    if cgc_bin:
        return cgc_bin

    python_bin = shutil.which("python")
    return python_bin


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
        target = f"{from_func}->{to_func}"
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
# CLI fallback client
# ---------------------------------------------------------------------------


class CGCCLIClient:
    """Small async wrapper around the official ``python -m codegraphcontext`` CLI.

    The HTTP Gateway is preferred when healthy.  The CLI fallback exists because
    CGC 0.4.x Gateway jobs can remain pending in some Windows/KuzuDB setups,
    while the official CLI still completes ``index`` and ``analyze`` reliably.
    """

    def __init__(
        self,
        python_exe: str | None = None,
        timeout: int | None = None,
        run: Callable | None = None,
    ) -> None:
        self._python_exe = python_exe or _default_cgc_python()
        self._timeout = timeout or settings.cgc_cli_timeout
        self._run = run or subprocess.run

    def _base_cmd(self) -> list[str]:
        if not self._python_exe:
            raise CGCUnavailable("CGC CLI python executable not found")
        exe = Path(self._python_exe).name.lower()
        if exe.startswith("cgc"):
            return [self._python_exe]
        return [self._python_exe, "-m", "codegraphcontext"]

    async def is_healthy(self) -> bool:
        try:
            result = await self._run_cli(["--version"], timeout=30)
            return result.returncode == 0 or "CodeGraphContext" in (
                (result.stdout or "") + (result.stderr or "")
            )
        except Exception:
            return False

    async def index_repo(self, path: str, is_dependency: bool = False, repo_name: str | None = None) -> str:
        args = ["index", str(Path(path))]
        result = await self._run_cli(args, timeout=self._timeout)
        text = (result.stdout or "") + "\n" + (result.stderr or "")
        if result.returncode != 0 and "Successfully finished indexing" not in text and "already indexed" not in text:
            raise CGCIndexFailed(text.strip() or f"CGC CLI index failed for {path}")
        return "cli-index"

    async def wait_for_index(self, job_id: str, timeout: int = _INDEX_POLL_TIMEOUT) -> bool:
        return True

    async def find_callers(self, func_name: str, repo_path: str | None = None, depth: int | None = None) -> list:
        return await self._analyze_table(["analyze", "callers", func_name])

    async def find_callees(self, func_name: str, repo_path: str | None = None, depth: int | None = None) -> list:
        return await self._analyze_table(["analyze", "calls", func_name])

    async def call_chain(self, from_func: str, to_func: str, repo_path: str | None = None) -> dict:
        rows = await self._analyze_table(["analyze", "chain", from_func, to_func])
        return {"chain": [row.get("name") for row in rows if row.get("name")]}

    async def module_deps(self, target: str, repo_path: str | None = None) -> dict:
        result = await self._run_cli(["analyze", "deps", str(Path(target))], timeout=self._timeout)
        text = (result.stdout or "") + "\n" + (result.stderr or "")
        return {"cli_output": text.strip()} if text.strip() else {}

    async def find_complexity(self, repo_path: str | None = None, threshold: int = 10) -> list:
        result = await self._run_cli(["analyze", "complexity"], timeout=self._timeout)
        return [
            row for row in _parse_cli_table((result.stdout or "") + "\n" + (result.stderr or ""))
            if _complexity_value(row) >= threshold
        ]

    async def find_code(self, query: str, repo_path: str | None = None) -> list:
        return await self._analyze_table(["find", "name", query])

    async def _analyze_table(self, args: list[str]) -> list[dict]:
        result = await self._run_cli(args, timeout=self._timeout)
        text = (result.stdout or "") + "\n" + (result.stderr or "")
        if result.returncode != 0 and not text.strip():
            raise CGCQueryError(f"CGC CLI command failed: {' '.join(args)}")
        return _parse_cli_table(text)

    async def _run_cli(self, args: list[str], timeout: int | None = None):
        cmd = [*self._base_cmd(), *args]
        logger.info("cgc: running CLI: %s", " ".join(cmd))
        return await asyncio.to_thread(
            self._run,
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout or self._timeout,
            cwd=os.getenv("CGC_CLI_CWD") or os.path.expanduser("~/.codegraphcontext"),
        )


def _parse_cli_table(text: str) -> list[dict]:
    rows: list[dict] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or set(stripped) <= {"|", "-", " "}:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells:
            continue
        first = cells[0]
        if not first or first.lower() in {"name", "called function", "caller", "function"}:
            continue
        if first.startswith("+"):
            continue
        row = {"name": first}
        if len(cells) > 1:
            row["location"] = cells[1]
            m = re.search(r":(\d+)$", cells[1])
            if m:
                row["line"] = int(m.group(1))
        if len(cells) > 2:
            row["type"] = cells[2]
        for cell in cells[1:]:
            if cell.isdigit():
                row["complexity"] = int(cell)
                break
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# BaseToolAdapter wrapper
# ---------------------------------------------------------------------------


class CGCAdapter(BaseToolAdapter):
    """BaseToolAdapter wrapper around CGCClient for the adapter registry."""

    # In-flight prepare Futures keyed by (base_url, repo_path, loop_id).
    # When multiple concurrent callers prepare the same path, only one
    # index_repo() job is submitted; the rest await the shared Future.
    _prepare_inflight: dict[tuple[str, str, int], "asyncio.Future[None]"] = {}
    _indexed_count: int = 0
    _last_index_error: str | None = None

    def __init__(self, base_url: str | None = None) -> None:
        self._cgc = CGCClient(base_url=base_url)
        self._cli = CGCCLIClient()

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
            indexed_repos=self._indexed_count,
            last_index_error=self._last_index_error,
        )

    async def prepare(self, request: AnalysisRequest) -> None:
        """Index the repository (true dedup: same path concurrently → one job)."""
        loop = asyncio.get_running_loop()
        configured_paths = request.options.get("cgc_index_paths") if request.options else None
        if isinstance(configured_paths, list):
            index_paths = [str(p) for p in configured_paths if str(p).strip()]
        else:
            index_paths = []
        if not index_paths:
            index_paths = [request.repo_local_path]

        deduped_paths: list[str] = []
        seen_paths: set[str] = set()
        for index_path in index_paths:
            key_path = os.path.normcase(str(Path(index_path)))
            if key_path in seen_paths:
                continue
            seen_paths.add(key_path)
            deduped_paths.append(index_path)

        key = (self._cgc._base_url, "|".join(deduped_paths), id(loop))

        existing = self._prepare_inflight.get(key)
        if existing is not None:
            await asyncio.shield(existing)
            return

        fut: asyncio.Future[None] = loop.create_future()
        fut.add_done_callback(_consume_future_exception)
        self._prepare_inflight[key] = fut
        try:
            for index_path in deduped_paths:
                try:
                    job_id = await self._cgc.index_repo(index_path)
                    await self._cgc.wait_for_index(job_id, timeout=settings.cgc_index_timeout)
                except (CGCUnavailable, CGCQueryError, CGCIndexFailed, asyncio.TimeoutError) as exc:
                    logger.warning("CGC Gateway prepare failed, falling back to CLI: %s", exc)
                    await self._cli.index_repo(index_path)
                    self._cgc = self._cli
            CGCAdapter._indexed_count += 1
            CGCAdapter._last_index_error = None
            fut.set_result(None)
        except asyncio.CancelledError:
            if not fut.done():
                fut.cancel()
            raise
        except Exception as exc:
            CGCAdapter._last_index_error = str(exc)
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            self._prepare_inflight.pop(key, None)

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


def _consume_future_exception(fut: "asyncio.Future[None]") -> None:
    """Mark prepare-dedup Future exceptions observed when no waiter remains."""
    if fut.cancelled():
        return
    try:
        fut.exception()
    except Exception:
        pass
