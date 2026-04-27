"""CodeCompass code comprehension adapter.

IRON LAW: analyze() may ONLY do:
  (a) HTTP calls to the CodeCompass web server
  (b) Response format conversion
No code parsing, AST traversal, or graph building.
"""

import asyncio
import logging
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

# Supported languages for CodeCompass analysis
_SUPPORTED_EXTENSIONS = {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hxx", ".cs", ".py"}


class CodeCompassAdapter(BaseToolAdapter):
    _parsed_projects: dict[str, str] = {}
    _prepare_locks: dict[tuple[str, int], asyncio.Lock] = {}

    def __init__(self, base_url: str = "http://codecompass:6251"):
        self.base_url = base_url
        self._current_workspace: str | None = None

    @classmethod
    def _prepare_lock_for(cls, base_url: str) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        key = (base_url, id(loop))
        lock = cls._prepare_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            cls._prepare_locks[key] = lock
        return lock

    @classmethod
    def clear_cached_project(cls, base_url: str | None = None) -> None:
        """Invalidate parse cache so next prepare() re-runs the parser.

        Called after git pull or repo update to ensure fresh analysis.
        """
        if base_url:
            cls._parsed_projects.pop(base_url, None)
        else:
            cls._parsed_projects.clear()

    def name(self) -> str:
        return "codecompass"

    def capabilities(self) -> list[ToolCapability]:
        return [
            ToolCapability.CALL_GRAPH,
            ToolCapability.POINTER_ANALYSIS,
            ToolCapability.DEPENDENCY_GRAPH,
            ToolCapability.ARCHITECTURE_DIAGRAM,
        ]

    async def health_check(self) -> ToolHealth:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=httpx.Timeout(5, connect=3)
            ) as client:
                resp = await client.get("/")
                if resp.status_code < 500:
                    return ToolHealth(is_healthy=True, container_status="running")
                return ToolHealth(
                    is_healthy=False,
                    container_status="error",
                    last_check=f"HTTP {resp.status_code}",
                )
        except Exception as exc:
            return ToolHealth(
                is_healthy=False,
                container_status="error",
                last_check=str(exc),
            )

    async def prepare(self, request: AnalysisRequest) -> None:
        """Run CodeCompass_parser to build the analysis database.

        This is a heavy operation (several minutes for large projects).
        Uses docker exec to invoke the parser inside the container.
        """
        tool_repo_path = to_tool_repo_path(
            request.repo_local_path,
            host_base_path=settings.repos_base_path,
            tool_base_path=settings.tool_repos_base_path,
        )
        project_name = tool_repo_path.rstrip("/").split("/")[-1]

        async with self._prepare_lock_for(self.base_url):
            # Check if already parsed
            if self._parsed_projects.get(self.base_url) == project_name:
                logger.info("codecompass: project %s already parsed, skipping", project_name)
                self._current_workspace = project_name
                return

            # Check if repo has supported files (use local path, not tool path)
            if not self._has_supported_files(request.repo_local_path):
                logger.info("codecompass: no supported files in %s, skipping parse", project_name)
                self._current_workspace = None
                return

            # Invoke parser via wrapper endpoint inside the container.
            # tool_repo_path is the container-visible path (e.g. /data/repos/<uuid>).
            try:
                async with httpx.AsyncClient(
                    base_url=self.base_url,
                    timeout=httpx.Timeout(600, connect=10),
                ) as client:
                    resp = await client.post(
                        "/api/parse",
                        json={
                            "project_name": project_name,
                            "source_path": tool_repo_path,
                        },
                    )
                    resp.raise_for_status()
                    self._current_workspace = project_name
                    self._parsed_projects[self.base_url] = project_name
                    logger.info("codecompass: parsed project %s", project_name)
            except httpx.ConnectError:
                raise
            except Exception as exc:
                logger.warning("codecompass: parse failed: %s", exc)
                raise

    async def analyze(self, request: AnalysisRequest) -> UnifiedResult:
        """Query CodeCompass web API for analysis results.

        HTTP calls + response format conversion ONLY.
        """
        if self._current_workspace is None:
            return UnifiedResult(
                tool_name="codecompass",
                capability=ToolCapability.CALL_GRAPH,
                data={"unsupported": True},
                raw_output="CodeCompass only supports C/C++, C#, Python",
                metadata={"skipped": True, "reason": "unsupported_language"},
            )

        results: dict = {}
        project = self._current_workspace

        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(60, connect=10),
        ) as client:
            # 1. File list
            try:
                resp = await client.get(f"/api/{project}/files")
                if resp.status_code == 200:
                    results["files"] = resp.json()
            except Exception as exc:
                logger.warning("codecompass: files query failed: %s", exc)
                results["files"] = {"error": str(exc)}

            # 2. Call graph
            try:
                resp = await client.get(f"/api/{project}/call-graph")
                if resp.status_code == 200:
                    results["call_graph"] = resp.json()
            except Exception as exc:
                logger.warning("codecompass: call-graph query failed: %s", exc)
                results["call_graph"] = {"error": str(exc)}

            # 3. Dependency graph
            try:
                resp = await client.get(f"/api/{project}/dependencies")
                if resp.status_code == 200:
                    results["dependencies"] = resp.json()
            except Exception as exc:
                logger.warning("codecompass: dependencies query failed: %s", exc)
                results["dependencies"] = {"error": str(exc)}

            # 4. Pointer analysis
            try:
                resp = await client.get(f"/api/{project}/pointer-analysis")
                if resp.status_code == 200:
                    results["pointer_analysis"] = resp.json()
            except Exception as exc:
                logger.warning("codecompass: pointer-analysis query failed: %s", exc)
                results["pointer_analysis"] = {"error": str(exc)}

            # 5. Class hierarchy (C++ specific)
            try:
                resp = await client.get(f"/api/{project}/class-hierarchy")
                if resp.status_code == 200:
                    results["class_hierarchy"] = resp.json()
            except Exception as exc:
                logger.warning("codecompass: class-hierarchy query failed: %s", exc)
                results["class_hierarchy"] = {"error": str(exc)}

        diagrams = []
        # Extract SVG diagrams if present in call_graph or dependencies
        for key in ("call_graph", "dependencies", "class_hierarchy"):
            data = results.get(key, {})
            if isinstance(data, dict) and "svg" in data:
                diagrams.append({"type": key, "format": "svg", "content": data["svg"]})

        return UnifiedResult(
            tool_name="codecompass",
            capability=ToolCapability.CALL_GRAPH,
            data={"codecompass_analysis": results},
            raw_output=f"{len(results)} analysis categories queried",
            diagrams=diagrams,
            metadata={
                "project": project,
                "query_count": len(results),
            },
        )

    # ── High-level query methods (exposed to API) ──

    async def function_call_graph(self, function_name: str) -> dict:
        """Get call graph for a specific function."""
        if not self._current_workspace:
            return {"error": "no project loaded"}
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=httpx.Timeout(30, connect=5)
        ) as client:
            resp = await client.get(
                f"/api/{self._current_workspace}/call-graph/{function_name}"
            )
            resp.raise_for_status()
            return resp.json()

    async def pointer_analysis_for(self, function_name: str) -> dict:
        """Get pointer analysis results for a specific function."""
        if not self._current_workspace:
            return {"error": "no project loaded"}
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=httpx.Timeout(30, connect=5)
        ) as client:
            resp = await client.get(
                f"/api/{self._current_workspace}/pointer-analysis/{function_name}"
            )
            resp.raise_for_status()
            return resp.json()

    async def indirect_calls(self, function_name: str) -> dict:
        """Resolve function pointer / virtual call targets."""
        if not self._current_workspace:
            return {"error": "no project loaded"}
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=httpx.Timeout(30, connect=5)
        ) as client:
            resp = await client.get(
                f"/api/{self._current_workspace}/indirect-calls/{function_name}"
            )
            resp.raise_for_status()
            return resp.json()

    async def alias_analysis(self, variable: str, file_path: str, line: int) -> dict:
        """Get pointer alias set for a variable at a specific location."""
        if not self._current_workspace:
            return {"error": "no project loaded"}
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=httpx.Timeout(30, connect=5)
        ) as client:
            resp = await client.get(
                f"/api/{self._current_workspace}/alias",
                params={"variable": variable, "file": file_path, "line": line},
            )
            resp.raise_for_status()
            return resp.json()

    async def stream_logs(self, run_id: str) -> AsyncIterator[str]:
        yield "codecompass: parsing source code..."
        yield "codecompass: building AST and call graph..."
        yield "codecompass: running pointer analysis..."
        yield "codecompass: analysis complete"

    async def cleanup(self, request: AnalysisRequest) -> None:
        """No-op: keep parsed data for subsequent queries."""
        pass

    @staticmethod
    def _has_supported_files(repo_path: str, max_depth: int = 5) -> bool:
        """Check if repository contains files with supported extensions.

        Uses os.walk with depth limit for broad coverage without full traversal.
        """
        import os
        from pathlib import Path

        root = Path(repo_path)
        if not root.exists():
            return False

        root_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            current_depth = len(Path(dirpath).parts) - root_depth
            if current_depth >= max_depth:
                dirnames.clear()
                continue
            # Skip hidden/vendor directories early
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fname in filenames:
                if Path(fname).suffix in _SUPPORTED_EXTENSIONS:
                    return True
        return False
