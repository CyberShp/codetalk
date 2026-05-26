"""Scope resolver for the workspace analysis modal.

Given an :class:`AnalysisPlan` and a workspace, produce a bounded
:class:`ScopePreview`.  Sources tried, in order:

1. Cached GitNexus graph (preferred — it's already on disk after indexing).
2. Live GitNexus search/cluster endpoints (best-effort, swallowed on error).
3. Local repository search via a bounded ``ripgrep``/``grep`` fallback so
   the modal still works when GitNexus is offline.
4. Workspace materials (filename + content snippet match).

Hard caps come from ``plan.llm_limits``.  We always clamp to the
schema-level upper bounds — the user can never widen the fan-out beyond
what the model context can support.

The resolver is intentionally synchronous-friendly: every filesystem or
subprocess call is wrapped in ``asyncio.to_thread`` so a slow disk does
not block the FastAPI event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

import aiosqlite
import httpx

from app.config import settings
from app.schemas.workspace_analysis import (
    AnalysisObject,
    AnalysisPlan,
    ResolvedAnalysisObject,
    ScopeCandidate,
    ScopePreview,
)
from app.utils.repo_paths import to_tool_repo_path

logger = logging.getLogger(__name__)

_DIR_SKIP = frozenset({
    "node_modules", "__pycache__", ".git", ".venv", "venv", "dist",
    "build", ".next", "vendor", "coverage", ".tox", ".mypy_cache",
    ".pytest_cache", "target", "out", "bin", "obj",
})

_SOURCE_EXTS = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
    ".c", ".cpp", ".h", ".hpp", ".cc", ".cxx", ".cs", ".rb", ".php",
    ".kt", ".swift", ".m", ".scala",
})

# Stopwords pruned from analysis-object text before keyword search.
# We mix English and Chinese; case is folded for English only.
_STOPWORDS_EN = frozenset({
    "the", "and", "for", "with", "from", "into", "that", "this",
    "path", "flow", "case", "code", "data", "value", "values",
    "error", "errors", "logic", "long", "short", "handle", "handling",
    "of", "in", "on", "to", "or", "if", "is", "be", "a", "an",
})
_STOPWORDS_CN = frozenset({"流程", "路径", "处理", "分析", "代码"})

_TOKEN_RE = re.compile(r"[\w一-鿿]{2,}", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    raw = [t for t in _TOKEN_RE.findall(text or "")]
    out: list[str] = []
    seen: set[str] = set()
    for tok in raw:
        norm = tok.lower() if tok.isascii() else tok
        if norm in seen:
            continue
        if norm in _STOPWORDS_EN or norm in _STOPWORDS_CN:
            continue
        if norm.isdigit():
            continue
        seen.add(norm)
        out.append(tok)
    return out[:8]  # bound keyword count per object


# ---------------------------------------------------------------------------
# GitNexus cached-graph loader
# ---------------------------------------------------------------------------


def _gitnexus_cache_files(repo_path: str) -> list[Path]:
    """Return cached GitNexus graph JSON files for this repo path, newest-first."""
    import hashlib

    try:
        path_hash = hashlib.md5(str(Path(repo_path).resolve()).encode()).hexdigest()[:8]
    except Exception:
        return []
    cache_dir = settings.outputs_path / ".cache"
    if not cache_dir.is_dir():
        return []
    candidates = list(cache_dir.glob(f"gitnexus_{path_hash}_*.json"))
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates


async def _load_cached_gitnexus_graph(repo_path: str) -> dict | None:
    def _read() -> dict | None:
        for candidate in _gitnexus_cache_files(repo_path):
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Failed to read GitNexus cache %s: %s", candidate, exc)
        return None
    return await asyncio.to_thread(_read)


async def _fetch_live_gitnexus_graph(repo_path: str) -> dict | None:
    """Best-effort live fetch from GitNexus.  None on any error."""
    try:
        tool_path = to_tool_repo_path(
            repo_path,
            host_base_path=settings.repos_base_path,
            tool_base_path=settings.tool_repos_base_path,
            local_host_path=settings.local_repos_host_path,
            local_container_path=settings.local_repos_container_path,
        )
    except Exception:
        return None

    repo_name = Path(tool_path).name
    try:
        async with httpx.AsyncClient(
            base_url=settings.gitnexus_base_url,
            timeout=15,
            trust_env=False,
        ) as client:
            resp = await client.get("/api/graph", params={"repo": repo_name})
            if resp.status_code == 404:
                resp = await client.get("/api/graph")
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.info("Live GitNexus graph fetch failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Index helpers built from a GitNexus graph
# ---------------------------------------------------------------------------


class _GraphIndex:
    """Lightweight in-memory index of a GitNexus graph for keyword matching."""

    def __init__(self, graph: dict | None) -> None:
        self._graph = graph or {}
        self._nodes = self._graph.get("nodes", []) or []
        self._rels = self._graph.get("relationships", []) or []

        self._file_nodes: list[dict] = []
        self._symbol_nodes: list[dict] = []
        self._community_nodes: list[dict] = []

        for node in self._nodes:
            label = node.get("label", "")
            if label in ("File", "Module"):
                self._file_nodes.append(node)
            elif label in ("Function", "Class", "Struct", "Method"):
                self._symbol_nodes.append(node)
            elif label == "Community":
                self._community_nodes.append(node)

        # member -> community mapping (used to resolve "related communities")
        self._member_to_community: dict[str, str] = {}
        for edge in self._rels:
            if edge.get("type") == "MEMBER_OF":
                self._member_to_community[edge.get("sourceId", "")] = edge.get("targetId", "")

        self._community_names: dict[str, str] = {
            c["id"]: c.get("properties", {}).get("name", c["id"])
            for c in self._community_nodes
        }

    @property
    def is_empty(self) -> bool:
        return not self._nodes

    def search_files(
        self, keywords: list[str], limit: int, focused_module: str | None = None
    ) -> list[tuple[dict, float]]:
        return _rank_nodes_by_keywords(self._file_nodes, keywords, limit, focused_module)

    def search_symbols(
        self, keywords: list[str], limit: int, focused_module: str | None = None
    ) -> list[tuple[dict, float]]:
        return _rank_nodes_by_keywords(self._symbol_nodes, keywords, limit, focused_module)

    def communities_for_nodes(self, node_ids: Iterable[str], limit: int) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for nid in node_ids:
            cid = self._member_to_community.get(nid)
            if not cid or cid in seen:
                continue
            seen.add(cid)
            names.append(self._community_names.get(cid, cid))
            if len(names) >= limit:
                break
        return names


def _node_text(node: dict) -> str:
    props = node.get("properties", {}) or {}
    parts: list[str] = [
        str(props.get("name", "")),
        str(props.get("path", "")),
        str(props.get("filePath", "")),
        str(props.get("module", "")),
        str(props.get("namespace", "")),
        str(node.get("id", "")),
    ]
    return " ".join(p for p in parts if p)


def _rank_nodes_by_keywords(
    nodes: list[dict],
    keywords: list[str],
    limit: int,
    focused_module: str | None = None,
) -> list[tuple[dict, float]]:
    if not keywords:
        return []
    folded = [kw.lower() if kw.isascii() else kw for kw in keywords]
    scored: list[tuple[dict, float]] = []
    for node in nodes:
        text = _node_text(node)
        if not text:
            continue
        lower = text.lower()
        hits: float = sum(1 for kw in folded if kw in lower)
        if not hits:
            continue
        # Path continuity: 2x when ≥ 2 keywords hit within 3 consecutive path segments.
        # e.g. keywords ["nvme","tcp","tls"] on "nvme_tcp/trans/tls/x.c" → 2x.
        path = _node_path(node).lower()
        if path and len(folded) >= 2:
            segments = [s for s in re.split(r"[/\\]", path) if s]
            for start in range(len(segments)):
                window = " ".join(segments[start : start + 3])
                if sum(1 for kw in folded if kw in window) >= 2:
                    hits *= 2.0
                    break
        # Focused-module bias: 2x for nodes inside the focused module path prefix.
        if focused_module:
            focused_lower = focused_module.lower()
            if path.startswith(focused_lower) or focused_lower in path:
                hits *= 2.0
        scored.append((node, hits))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[: max(limit, 0)]


# ---------------------------------------------------------------------------
# Repo-search fallback
# ---------------------------------------------------------------------------


def _bounded_repo_search_blocking(
    repo_path: str, keywords: list[str], limit: int
) -> list[str]:
    """Return up to *limit* file paths that match any keyword.

    Uses ripgrep if available (much faster), otherwise falls back to a
    Python file walker.  We never read file content — only file *names*
    and a single grep hit count.  This keeps the preview cheap.
    """
    if not keywords:
        return []

    repo = Path(repo_path)
    if not repo.is_dir():
        return []

    rg = shutil.which("rg")
    if rg:
        try:
            args = [
                rg, "--files-with-matches", "--no-messages", "--smart-case",
                "--max-count", "3",
            ]
            for kw in keywords:
                args.extend(["-e", kw])
            for skip in _DIR_SKIP:
                args.extend(["--glob", f"!{skip}/"])
            args.append(str(repo))
            proc = subprocess.run(
                args, capture_output=True, text=True, timeout=15,
            )
            files = [
                line.strip() for line in proc.stdout.splitlines() if line.strip()
            ]
            return files[:limit]
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.info("ripgrep fallback failed (%s); using python walker", exc)

    # Python fallback: search file names + cheap content scan limited by size.
    folded = [kw.lower() if kw.isascii() else kw for kw in keywords]
    matches: list[tuple[str, int]] = []
    name_hits: list[str] = []
    scanned = 0
    MAX_SCAN = 800  # cap files we actually read
    for root, dirs, files in _walk(repo):
        dirs[:] = [d for d in dirs if d not in _DIR_SKIP and not d.startswith(".")]
        for fname in files:
            ext = Path(fname).suffix
            if ext not in _SOURCE_EXTS:
                continue
            full = Path(root) / fname
            lower_name = fname.lower()
            # Filename match is high signal — always include.
            if any(kw in lower_name for kw in folded):
                name_hits.append(str(full))
                if len(name_hits) >= limit:
                    return name_hits
                continue
            if scanned >= MAX_SCAN:
                continue
            scanned += 1
            try:
                with full.open("r", encoding="utf-8", errors="ignore") as fh:
                    head = fh.read(20_000)
            except OSError:
                continue
            lower_head = head.lower()
            hits = sum(1 for kw in folded if kw in lower_head)
            if hits:
                matches.append((str(full), hits))
    matches.sort(key=lambda x: x[1], reverse=True)
    out = list(dict.fromkeys(name_hits + [m[0] for m in matches]))
    return out[:limit]


def _walk(repo: Path):
    """Lightweight os.walk wrapper that yields the same shape but lets us
    keep the import surface small for testing."""
    import os
    for root, dirs, files in os.walk(repo):
        yield root, dirs, files


async def _bounded_repo_search(
    repo_path: str, keywords: list[str], limit: int
) -> list[str]:
    return await asyncio.to_thread(
        _bounded_repo_search_blocking, repo_path, keywords, limit
    )


# ---------------------------------------------------------------------------
# Materials helper
# ---------------------------------------------------------------------------


async def _candidate_materials(
    ws_id: str, keywords: list[str], limit: int
) -> list[ScopeCandidate]:
    if not keywords:
        return []
    folded = [kw.lower() if kw.isascii() else kw for kw in keywords]
    candidates: list[ScopeCandidate] = []
    try:
        async with aiosqlite.connect(settings.sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT filename, file_path FROM workspace_materials "
                "WHERE workspace_id = ? AND is_active = TRUE",
                (ws_id,),
            ) as cur:
                rows = await cur.fetchall()
    except Exception as exc:
        logger.warning("Material lookup failed: %s", exc)
        return []

    for row in rows:
        fname = (row["filename"] or "").lower()
        if any(kw in fname for kw in folded):
            candidates.append(
                ScopeCandidate(
                    path=row["file_path"],
                    source="material",
                    confidence="medium",
                    reason=f"材料文件名命中关键字：{row['filename']}",
                )
            )
            if len(candidates) >= limit:
                break
    return candidates


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


class WorkspaceScopeResolver:
    """Stateless service used by the preview & analyze endpoints.

    Construction is cheap; instantiate per-request so live GitNexus
    failures don't poison subsequent requests.
    """

    async def resolve(
        self,
        *,
        ws_id: str,
        repo_path: str,
        plan: AnalysisPlan,
    ) -> ScopePreview:
        limits = plan.llm_limits

        graph = await _load_cached_gitnexus_graph(repo_path)
        used_cache = graph is not None
        if graph is None:
            graph = await _fetch_live_gitnexus_graph(repo_path)
        index = _GraphIndex(graph)
        gitnexus_available = not index.is_empty

        warnings: list[str] = []
        if not gitnexus_available:
            warnings.append(
                "GitNexus 图谱当前不可用，已退回到本地代码搜索；结果可能不完整。"
            )
        elif not used_cache:
            warnings.append(
                "GitNexus 缓存不存在，已实时拉取；若仓库尚未完成索引建议先重新索引。"
            )

        resolved: list[ResolvedAnalysisObject] = []
        total_candidates = 0

        for obj in plan.analysis_objects:
            resolved_obj = await self._resolve_object(
                obj=obj,
                ws_id=ws_id,
                repo_path=repo_path,
                index=index,
                limits=limits,
                gitnexus_available=gitnexus_available,
            )
            resolved.append(resolved_obj)
            total_candidates += (
                len(resolved_obj.candidate_files)
                + len(resolved_obj.candidate_symbols)
            )

        # Estimated fan-out per AC-P2: bounded by object count.
        if plan.analysis_objects:
            est_units = min(
                limits.max_analysis_units,
                max(len(plan.analysis_objects), 1),
                12 if len(plan.analysis_objects) <= 6 else len(plan.analysis_objects),
            )
        else:
            est_units = 0
        est_cards = min(limits.max_evidence_cards, total_candidates)

        return ScopePreview(
            workspace_id=ws_id,
            resolved_objects=resolved,
            estimated_analysis_units=est_units,
            estimated_evidence_cards=est_cards,
            warnings=warnings,
            gitnexus_available=gitnexus_available,
        )

    async def _resolve_object(
        self,
        *,
        obj: AnalysisObject,
        ws_id: str,
        repo_path: str,
        index: _GraphIndex,
        limits,
        gitnexus_available: bool,
    ) -> ResolvedAnalysisObject:
        keywords = _tokenize(obj.text)
        obj_warnings: list[str] = []

        if not keywords:
            obj_warnings.append("分析对象过于笼统，未提取到可检索关键字。")
            return ResolvedAnalysisObject(
                object_id=obj.id,
                text=obj.text,
                warnings=obj_warnings,
            )

        file_candidates: list[ScopeCandidate] = []
        symbol_candidates: list[ScopeCandidate] = []
        related_nodes: list[str] = []
        seen_keys: set[tuple[str, str]] = set()

        if gitnexus_available:
            for node, hits in index.search_files(keywords, limits.max_files_per_object):
                path = _node_path(node)
                key = ("file", path)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                related_nodes.append(node.get("id", ""))
                file_candidates.append(
                    ScopeCandidate(
                        path=path,
                        source="gitnexus",
                        confidence="high" if hits > 1 else "medium",
                        reason=f"GitNexus 文件命中关键字 ({hits} 次)",
                    )
                )
            for node, hits in index.search_symbols(keywords, limits.max_functions_per_object):
                sym = node.get("properties", {}).get("name") or node.get("id")
                key = ("symbol", sym)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                related_nodes.append(node.get("id", ""))
                symbol_candidates.append(
                    ScopeCandidate(
                        symbol=sym,
                        path=_node_path(node) or None,
                        source="gitnexus",
                        confidence="high" if hits > 1 else "medium",
                        reason=f"GitNexus 符号命中关键字 ({hits} 次)",
                    )
                )

        # If GitNexus is empty OR returned nothing, fall back to repo search.
        if not file_candidates:
            repo_hits = await _bounded_repo_search(
                repo_path, keywords, limits.max_files_per_object
            )
            for hit in repo_hits:
                key = ("file", hit)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                file_candidates.append(
                    ScopeCandidate(
                        path=hit,
                        source="repo_search",
                        confidence="medium",
                        reason="本地代码搜索命中关键字",
                    )
                )

        # Materials are always consulted but kept low-priority.
        mat_candidates = await _candidate_materials(
            ws_id, keywords, max(2, limits.max_files_per_object // 4)
        )
        for cand in mat_candidates:
            key = ("material", cand.path or "")
            if key in seen_keys:
                continue
            seen_keys.add(key)
            file_candidates.append(cand)

        related_communities = (
            index.communities_for_nodes(related_nodes, limits.max_communities_per_object)
            if gitnexus_available
            else []
        )

        if not file_candidates and not symbol_candidates:
            obj_warnings.append(
                "未在 GitNexus、源码或材料中找到候选证据，请细化描述或增加关键字。"
            )

        return ResolvedAnalysisObject(
            object_id=obj.id,
            text=obj.text,
            candidate_files=file_candidates,
            candidate_symbols=symbol_candidates,
            related_communities=related_communities,
            warnings=obj_warnings,
        )


def _node_path(node: dict) -> str:
    props = node.get("properties", {}) or {}
    for key in ("filePath", "path", "name"):
        val = props.get(key)
        if val:
            return str(val)
    return str(node.get("id", ""))
