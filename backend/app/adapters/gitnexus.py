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
_ANALYZE_BUSY_RETRY_ATTEMPTS = 8
_ANALYZE_BUSY_RETRY_INTERVAL = 2.0


# ---------------------------------------------------------------------------
# /api/repos response parsing (Round 2/3 bug: real service returns a top-level
# array with duplicate repo names like two `spdk`, so detection must tolerate
# multiple shapes AND match by normalized path — not just by name).
# ---------------------------------------------------------------------------

_REPO_NAME_KEYS = ("name", "repo", "repoName", "repo_name", "id")
_REPO_PATH_KEYS = (
    "path", "root", "indexRoot", "index_root", "rootPath", "root_path",
    "dir", "directory", "absolutePath", "absolute_path", "repoPath",
    "repo_path", "localPath", "local_path",
)


def _looks_like_path(s: str) -> bool:
    return isinstance(s, str) and ("/" in s or "\\" in s)


def _basename(p: object) -> str:
    """Separator-agnostic basename — `pathlib.Path` on Linux won't split the
    Windows backslash paths GitNexus reports, so do it manually."""
    if not p:
        return ""
    import re as _re
    parts = [seg for seg in _re.split(r"[\\/]+", str(p)) if seg]
    return parts[-1] if parts else ""


def _norm_repo_path(p: str) -> str:
    """Cross-platform path normalisation for comparison (case-insensitive)."""
    if not p:
        return ""
    return str(p).replace("\\", "/").rstrip("/").lower()


def _extract_repo_entries(payload: object) -> list:
    """Pull the repo list out of whatever shape /api/repos returned.

    Handles: a bare top-level list, ``{"repos": [...]}`` / ``{"data": [...]}``
    wrappers, and a single repo object.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("repos", "data", "items", "results"):
            val = payload.get(key)
            if isinstance(val, list):
                return val
        if any(k in payload for k in _REPO_NAME_KEYS + _REPO_PATH_KEYS):
            return [payload]
    return []


def _entry_names(entry: object) -> list[str]:
    if isinstance(entry, str):
        return [entry, _basename(entry)] if _looks_like_path(entry) else [entry]
    if isinstance(entry, dict):
        out: list[str] = []
        for k in _REPO_NAME_KEYS:
            v = entry.get(k)
            if isinstance(v, str) and v:
                out.append(v)
        for k in _REPO_PATH_KEYS:
            v = entry.get(k)
            if isinstance(v, str) and v:
                out.append(_basename(v))
        return out
    return []


def _entry_paths(entry: object) -> list[str]:
    if isinstance(entry, str):
        return [entry] if _looks_like_path(entry) else []
    if isinstance(entry, dict):
        out: list[str] = []
        for k in _REPO_PATH_KEYS:
            v = entry.get(k)
            if isinstance(v, str) and v:
                out.append(v)
        for v in entry.values():  # any other path-looking value
            if isinstance(v, str) and _looks_like_path(v) and v not in out:
                out.append(v)
        return out
    return []


def _path_matches(target: str, candidate: str) -> bool:
    """True when *candidate* refers to the same or containing repo dir.

    Compares path *components* from the tail: the basenames must match and the
    shorter path must be a component-wise suffix of the longer one.  This
    tolerates host-vs-container prefix differences (``/b/spdk`` ==
    ``/host/a/b/spdk``) while never cross-matching two distinct same-named
    repos (``D:\\...\\spdk`` vs ``E:\\...\\spdk``).

    GitNexus may index a workspace parent while CodeTalk is asked to analyse a
    child directory.  In that case the indexed parent graph is still the right
    graph, so a component-wise parent relation is accepted as well.
    """
    t, c = _norm_repo_path(target), _norm_repo_path(candidate)
    if not t or not c:
        return False
    if t == c:
        return True
    tp = [x for x in t.split("/") if x]
    cp = [x for x in c.split("/") if x]
    if not tp or not cp:
        return False
    n = min(len(tp), len(cp))
    if tp[-1] == cp[-1] and tp[-n:] == cp[-n:]:
        return True
    return _path_components_contain_parent(tp, cp)


def _path_components_contain_parent(target_parts: list[str], candidate_parts: list[str]) -> bool:
    if len(candidate_parts) > len(target_parts):
        return False
    if target_parts[: len(candidate_parts)] == candidate_parts:
        return True
    parent_leaf = candidate_parts[-1]
    for index, part in enumerate(target_parts):
        if part != parent_leaf:
            continue
        candidate_tail = candidate_parts[-(index + 1):]
        if target_parts[: index + 1][-len(candidate_tail):] == candidate_tail:
            return True
    return False


def _entry_id(entry: object) -> str | None:
    if isinstance(entry, dict):
        for k in ("id", "repoId", "repo_id", "uuid"):
            v = entry.get(k)
            if isinstance(v, (str, int)) and str(v):
                return str(v)
    return None


def _entry_stats(entry: object) -> dict:
    """Best-effort extraction of {node_count, edge_count, file_count}."""
    out: dict = {"node_count": None, "edge_count": None, "file_count": None}
    if not isinstance(entry, dict):
        return out
    stats = entry.get("stats") if isinstance(entry.get("stats"), dict) else entry
    for dst, keys in (
        ("node_count", ("nodes", "node_count", "nodeCount")),
        ("edge_count", ("edges", "edge_count", "edgeCount", "relationships")),
        ("file_count", ("files", "file_count", "fileCount")),
    ):
        for k in keys:
            v = stats.get(k)
            if isinstance(v, int):
                out[dst] = v
                break
    return out


def resolve_indexed_repo(payload: object, tool_repo_path: str) -> dict | None:
    """Resolve the indexed GitNexus repo *descriptor* for *tool_repo_path*.

    Returns ``{name, path, id, node_count, edge_count, file_count, ambiguous}``
    or None.  ``ambiguous`` is True when more than one indexed repo shares the
    matched name — in that case ``GET /api/graph?repo=<name>`` cannot be trusted
    to return the right repo, so the caller must disambiguate/verify (Round 4
    P1: graph fetched the wrong same-named repo).
    """
    entries = _extract_repo_entries(payload)
    matched = None
    matched_path = None
    # 1. path match (preferred — the only reliable disambiguator)
    for entry in entries:
        for p in _entry_paths(entry):
            if _path_matches(tool_repo_path, p):
                matched, matched_path = entry, p
                break
        if matched is not None:
            break
    # 2. unique basename fallback
    if matched is None:
        base = _basename(tool_repo_path)
        basename_matches = [e for e in entries if base in _entry_names(e)]
        if len(basename_matches) == 1:
            matched = basename_matches[0]
            paths = _entry_paths(matched)
            matched_path = paths[0] if paths else None
        else:
            return None

    names = _entry_names(matched)
    target_base = _basename(tool_repo_path)
    name = (
        target_base
        if target_base in names
        else (names[0] if names else target_base)
    )
    # ambiguity: how many indexed repos share this name?
    same_name = sum(1 for e in entries if name in _entry_names(e))
    descriptor = {
        "name": name,
        "path": matched_path,
        "id": _entry_id(matched),
        "ambiguous": same_name > 1,
        **_entry_stats(matched),
    }
    return descriptor


def resolve_indexed_repo_name(payload: object, tool_repo_path: str) -> str | None:
    """Back-compat thin wrapper returning just the resolved repo name."""
    descriptor = resolve_indexed_repo(payload, tool_repo_path)
    return descriptor["name"] if descriptor else None


class GitNexusAdapter(BaseToolAdapter):
    _indexed_repo_by_path: dict[tuple[str, str], str] = {}
    _prepare_locks: dict[tuple[str, str, int], asyncio.Lock] = {}

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or settings.gitnexus_base_url
        self._client: httpx.AsyncClient | None = None
        self._repo_name: str = ""
        # Resolved on-disk path of the indexed repo — used to disambiguate
        # same-named repos on graph/embed queries (Round 4 P1).
        self._repo_index_path: str = ""

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
        info_error = ""
        try:
            resp = await self.client.get("/api/info")
            if resp.status_code < 500:
                data = resp.json() if resp.status_code < 400 else {}
                repo_count, repo_detail = await self._repo_count_for_health()
                if repo_count is not None:
                    n_indexed = max(n_indexed, repo_count)
                status = "running" if n_indexed > 0 else "running_no_index"
                last_check = repo_detail
                if n_indexed == 0:
                    last_check = (
                        "GitNexus reachable but no indexed repos are reported; "
                        "graph endpoints may be empty until indexing completes."
                    )
                return ToolHealth(
                    is_healthy=True,
                    container_status=status,
                    version=data.get("version"),
                    indexed_repos=n_indexed,
                    last_check=last_check,
                )
            info_error = f"/api/info HTTP {resp.status_code}"
        except Exception as exc:
            info_error = f"/api/info failed: {exc}"

        # Fallback: probe /api/analyze — even a 4xx proves GitNexus is reachable
        try:
            resp = await self.client.post("/api/analyze", json={})
            if resp.status_code < 500:
                return ToolHealth(
                    is_healthy=True,
                    container_status="running_degraded",
                    indexed_repos=n_indexed,
                    last_check=(
                        f"{info_error}; /api/analyze accepted probe with "
                        f"HTTP {resp.status_code}; graph readiness not verified"
                    ).strip("; "),
                )
            return ToolHealth(
                is_healthy=False,
                container_status="unhealthy",
                last_check=(
                    f"{info_error}; /api/analyze HTTP {resp.status_code}"
                ).strip("; "),
                indexed_repos=n_indexed,
            )
        except Exception as exc:
            return ToolHealth(
                is_healthy=False,
                container_status="error",
                last_check=(f"{info_error}; /api/analyze failed: {exc}").strip("; "),
                indexed_repos=n_indexed,
            )

    async def _repo_count_for_health(self) -> tuple[int | None, str]:
        """Best-effort repo count so health can distinguish process vs graph readiness."""
        try:
            resp = await self.client.get("/api/repos", timeout=10)
            if resp.status_code != 200:
                return None, f"/api/repos HTTP {resp.status_code}"
            return len(_extract_repo_entries(resp.json())), ""
        except Exception as exc:
            return None, f"/api/repos failed: {exc}"

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

            # P0-002 / Round 2-3: the in-process cache is empty after a backend
            # restart or when a different adapter instance indexed the repo.
            # Before paying for a full re-index (3-15 min), ask GitNexus and
            # match by NORMALIZED PATH (the real /api/repos returns a top-level
            # array with duplicate `spdk` names, so name-only matching is unsafe).
            resolved = await self._resolve_repo_for_path(tool_repo_path)
            if resolved:
                self._repo_name = resolved["name"]
                self._repo_index_path = resolved.get("path") or ""
                self._indexed_repo_by_path[cache_key] = self._repo_name
                logger.info(
                    "gitnexus: repo already indexed as %s (resolved by path %s), "
                    "skipping analyze",
                    self._repo_name, resolved.get("path"),
                )
                self._schedule_embed_if_enabled()
                return

            resp = await self._post_analyze_with_busy_retry(tool_repo_path)

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
                        self._schedule_embed_if_enabled()
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

                # P0-002: status=complete but phase=retrying means worker crashed and is
                # still recovering; treat it as in-progress and keep polling.
                _raw_progress = status.get("progress")
                _progress_dict = _raw_progress if isinstance(_raw_progress, dict) else {}
                phase = str(_progress_dict.get("phase") or "")
                if status["status"] == "complete" and phase not in ("retrying", "error"):
                    self._repo_name = status.get("repoName", "") or Path(tool_repo_path).name
                    if not status.get("repoName"):
                        logger.warning(
                            "gitnexus: status missing repoName; falling back to dir name: %s",
                            self._repo_name,
                        )
                    self._indexed_repo_by_path[cache_key] = self._repo_name
                    logger.info("gitnexus: indexing complete for %s", self._repo_name)
                    self._schedule_embed_if_enabled()
                    return
                if status["status"] == "failed":
                    raise RuntimeError(
                        f"GitNexus indexing failed: {status.get('error', 'unknown')}"
                    )

            raise RuntimeError("GitNexus indexing timed out")

    async def _post_analyze_with_busy_retry(self, tool_repo_path: str) -> httpx.Response:
        """Start a GitNexus analyze job, tolerating transient 429 busy replies."""
        payload = {"path": tool_repo_path}
        attempts = max(1, _ANALYZE_BUSY_RETRY_ATTEMPTS)
        for attempt in range(attempts):
            resp = await self.client.post("/api/analyze", json=payload)
            if resp.status_code != 429:
                return resp
            if attempt >= attempts - 1:
                return resp
            logger.info(
                "gitnexus: analyze busy for %s (HTTP 429), retrying %d/%d",
                tool_repo_path,
                attempt + 1,
                attempts - 1,
            )
            await asyncio.sleep(_ANALYZE_BUSY_RETRY_INTERVAL)
        return resp

    async def _repo_exists(self, repo_name: str) -> bool:
        """Lightweight existence check so fresh adapter instances can reuse indexed repos."""
        try:
            resp = await self.client.get("/api/repos", params={"repo": repo_name}, timeout=10)
            if resp.status_code != 200:
                return False
            entries = _extract_repo_entries(resp.json())
            return any(repo_name in _entry_names(entry) for entry in entries)
        except Exception:
            return False

    async def _resolve_repo_for_path(self, tool_repo_path: str) -> dict | None:
        """Resolve the indexed repo *descriptor* for *tool_repo_path* via /api/repos.

        Matches by normalized path so duplicate repo names cannot cause a
        re-index of an already-indexed repo (Round 2/3 bug) and so graph/embed
        queries can disambiguate same-named repos (Round 4 P1).
        """
        try:
            resp = await self.client.get("/api/repos", timeout=10)
            if resp.status_code != 200:
                return None
            return resolve_indexed_repo(resp.json(), tool_repo_path)
        except Exception as exc:
            logger.debug("gitnexus: repo resolve by path failed (non-fatal): %s", exc)
            return None

    def _schedule_embed_if_enabled(self) -> None:
        """Optionally start semantic embedding without making indexing depend on it."""
        if not settings.gitnexus_auto_embed_enabled:
            logger.debug("gitnexus: auto embed disabled; skipping embed trigger")
            return
        asyncio.ensure_future(self._trigger_embed())

    async def _trigger_embed(self) -> None:
        """Start embedding job for the indexed repo (non-blocking).

        Embedding enables hybrid/semantic search; BM25 works without it.
        Only logs the result — never raises.
        """
        params: dict[str, str] = {}
        if self._repo_name:
            params["repo"] = self._repo_name
        # Best-effort disambiguation for same-named repos (Round 4 P1); GitNexus
        # ignores the param if unsupported.
        if self._repo_index_path:
            params["path"] = self._repo_index_path
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
        # Best-effort disambiguation for same-named repos (Round 4 P1).
        if self._repo_index_path:
            params["path"] = self._repo_index_path

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
