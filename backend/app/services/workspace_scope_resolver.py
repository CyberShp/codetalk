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

# A bare C/C++/identifier-style analysis object (e.g. ``spdk_log_set_flag``).
_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{2,}$")


def _looks_like_symbol(text: str) -> bool:
    return bool(_SYMBOL_RE.match((text or "").strip()))


def _tokenize(text: str) -> list[str]:
    # Insert spaces at CJK↔ASCII boundaries so "针对iscsi_tgt模块" splits correctly.
    t = re.sub(r"([一-鿿])([a-zA-Z0-9_])", r"\1 \2", text or "")
    t = re.sub(r"([a-zA-Z0-9_])([一-鿿])", r"\1 \2", t)
    raw = [t2 for t2 in _TOKEN_RE.findall(t)]
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
            # Disambiguate same-named repos by path so the preview doesn't show
            # evidence from the wrong repo (Round 4 P1).  Best-effort: GitNexus
            # ignores the path param if unsupported.
            params: dict[str, str] = {"repo": repo_name}
            try:
                from app.adapters.gitnexus import resolve_indexed_repo
                repos_resp = await client.get("/api/repos", timeout=10)
                if repos_resp.status_code == 200:
                    descriptor = resolve_indexed_repo(repos_resp.json(), tool_path)
                    if descriptor:
                        params["repo"] = descriptor["name"]
                        if descriptor.get("path"):
                            params["path"] = descriptor["path"]
            except Exception:
                pass
            resp = await client.get("/api/graph", params=params)
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
                # P0-004: exclude typedef function-pointer aliases that leak across modules.
                # In C codebases (SPDK etc.), typedef'd fp types share the "Function" label
                # but have no real implementation — they pollute unrelated module scopes.
                props = node.get("properties", {}) or {}
                if props.get("kind") == "typedef" or props.get("subkind") == "typedef":
                    continue
                name = str(props.get("name", ""))
                if label == "Function" and (name.endswith("_cb") or name.endswith("_fn")):
                    continue
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
        self,
        keywords: list[str],
        limit: int,
        focused_module: str | None = None,
        path_filter: list[str] | None = None,
    ) -> list[tuple[dict, float]]:
        return _rank_nodes_by_keywords(self._file_nodes, keywords, limit, focused_module, path_filter)

    def search_symbols(
        self,
        keywords: list[str],
        limit: int,
        focused_module: str | None = None,
        path_filter: list[str] | None = None,
    ) -> list[tuple[dict, float]]:
        return _rank_nodes_by_keywords(self._symbol_nodes, keywords, limit, focused_module, path_filter)

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
    path_filter: list[str] | None = None,
) -> list[tuple[dict, float]]:
    if not keywords:
        return []
    # P0-004: if path_hints were provided, restrict to matching nodes only
    active_nodes: list[dict] = nodes
    if path_filter:
        lower_hints = [h.lower() for h in path_filter]
        active_nodes = [
            n for n in nodes
            if any(hint in _normalize_path_hint(_node_path(n)).lower() for hint in lower_hints)
        ]
    folded = [kw.lower() if kw.isascii() else kw for kw in keywords]
    scored: list[tuple[dict, float]] = []
    for node in active_nodes:
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


def _exact_symbol_repo_hits_blocking(
    repo_path: str, symbol: str, limit: int
) -> list[str]:
    """Return source files most likely to define *symbol*.

    A plain ``rg --files-with-matches`` tends to return many CLI/example
    callers before the actual implementation.  For exact-symbol analysis
    objects, definitions must win the evidence budget.
    """
    symbol = (symbol or "").strip()
    if not _looks_like_symbol(symbol):
        return []
    repo = Path(repo_path)
    if not repo.is_dir():
        return []

    hits: dict[str, int] = {}
    rg = shutil.which("rg")
    if rg:
        try:
            args = [
                rg, "--line-number", "--no-heading", "--no-messages",
                "--fixed-strings", symbol,
            ]
            for skip in _DIR_SKIP:
                args.extend(["--glob", f"!{skip}/"])
            args.append(str(repo))
            proc = subprocess.run(
                args, capture_output=True, text=True, timeout=15,
            )
            for line in proc.stdout.splitlines():
                # Windows paths contain a drive colon, so split from the right.
                try:
                    path, lineno, text = line.rsplit(":", 2)
                except ValueError:
                    continue
                score = _score_symbol_hit(path, text, symbol)
                hits[path] = max(hits.get(path, 0), score)
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.info("exact symbol ripgrep failed (%s); using python walker", exc)

    if not hits:
        pattern = re.compile(rf"\b{re.escape(symbol)}\b")
        for root, dirs, files in _walk(repo):
            dirs[:] = [d for d in dirs if d not in _DIR_SKIP and not d.startswith(".")]
            for fname in files:
                ext = Path(fname).suffix
                if ext not in _SOURCE_EXTS:
                    continue
                full = Path(root) / fname
                try:
                    with full.open("r", encoding="utf-8", errors="ignore") as fh:
                        for line in fh:
                            if pattern.search(line):
                                path = str(full)
                                hits[path] = max(
                                    hits.get(path, 0),
                                    _score_symbol_hit(path, line, symbol),
                                )
                                break
                except OSError:
                    continue

    ranked = sorted(hits.items(), key=lambda item: (-item[1], item[0].lower()))
    return [path for path, _ in ranked[:limit]]


def _path_hint_repo_hits_blocking(
    repo_path: str, path_hints: list[str], limit: int
) -> list[str]:
    """Return source files named or scoped by analysis-object path hints.

    Hints may be concrete files or directories.  Directory hints are expanded
    recursively so a user-supplied module path such as ``nvme_tcp/trans/tls``
    becomes a primary source scope instead of a weak keyword.
    """
    if not repo_path or not path_hints or limit <= 0:
        return []
    try:
        root = Path(repo_path).resolve()
    except Exception:
        return []

    results: list[str] = []
    seen: set[str] = set()

    def _append_source(path: Path) -> None:
        if len(results) >= limit:
            return
        if path.suffix.lower() not in _SOURCE_EXTS:
            return
        try:
            resolved = path.resolve()
            resolved.relative_to(root)
        except Exception:
            return
        key = str(resolved)
        if key in seen:
            return
        seen.add(key)
        results.append(key)

    def _source_files_under(directory: Path) -> list[Path]:
        files: list[Path] = []
        for walk_root, dirs, names in _walk(directory):
            dirs[:] = [d for d in dirs if d not in _DIR_SKIP and not d.startswith(".")]
            for name in names:
                full = Path(walk_root) / name
                if full.suffix.lower() in _SOURCE_EXTS:
                    files.append(full)
        files.sort(key=lambda p: p.relative_to(root).as_posix().lower())
        return files

    for hint in path_hints:
        normalized_hint = _normalize_path_hint(hint)
        if not normalized_hint:
            continue
        try:
            candidate = Path(normalized_hint)
            if not candidate.is_absolute():
                candidate = root.joinpath(*[part for part in normalized_hint.split("/") if part])
            candidate = candidate.resolve()
            candidate.relative_to(root)
        except Exception:
            continue

        if candidate.is_file():
            _append_source(candidate)
        elif candidate.is_dir():
            for source in _source_files_under(candidate):
                _append_source(source)
                if len(results) >= limit:
                    break
        if len(results) >= limit:
            break
    return results


def _normalize_path_hint(hint: str) -> str:
    """Normalize UI/user path hints before comparing with repo paths."""
    value = (hint or "").strip()
    if not value:
        return ""
    value = re.sub(r"[\r\n\t]+", "/", value)
    value = value.replace("\\", "/")
    value = re.sub(r"/+", "/", value)
    return value.rstrip("/")


def _score_symbol_hit(path: str, line: str, symbol: str) -> int:
    folded_path = path.replace("\\", "/").lower()
    stripped = line.strip()
    score = 10

    starts_with_call = re.match(rf"^{re.escape(symbol)}\s*\(", stripped)
    has_statement_semicolon = ";" in stripped
    if starts_with_call and not has_statement_semicolon:
        score += 120
    elif re.match(rf"^#\s*define\s+{re.escape(symbol)}\b", stripped):
        score += 110
    elif starts_with_call:
        # A line like ``spdk_log_open(opts->log);`` is a caller, not the
        # implementation. Keep it above arbitrary mentions but below defs.
        score += 45
    elif re.search(rf"\b{re.escape(symbol)}\s*\([^;]*$", stripped):
        score += 80
    elif re.search(rf"\b{re.escape(symbol)}\s*\([^;]*;", stripped):
        score += 50

    if "/lib/" in folded_path or "/src/" in folded_path:
        score += 30
    if "/include/" in folded_path:
        score += 20
    if "/app/" in folded_path or "/examples/" in folded_path or "/test/" in folded_path:
        score -= 15
    return score


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


async def _exact_symbol_repo_hits(
    repo_path: str, symbol: str, limit: int
) -> list[str]:
    return await asyncio.to_thread(
        _exact_symbol_repo_hits_blocking, repo_path, symbol, limit
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
                    role="external",
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

        # Estimated fan-out: run the SAME union-find planner the execution
        # pipeline uses (shared file keys collapse objects into one unit) so the
        # preview never advertises 3 units for work that executes as 1.
        if plan.analysis_objects:
            object_files: list[tuple[str, list[str]]] = []
            for robj in resolved:
                keys: list[str] = []
                for cand in robj.candidate_files:
                    if cand.path:
                        keys.append(normalize_file_key(repo_path, cand.path))
                # Symbol candidates also carry a path and feed the execution-side
                # grouping (evidence cards built from symbols keep file_path),
                # so include them here for parity.
                for cand in robj.candidate_symbols:
                    if cand.path:
                        keys.append(normalize_file_key(repo_path, cand.path))
                object_files.append((robj.object_id, keys))
            est_units = len(
                plan_analysis_units(object_files, limits.max_analysis_units)
            )
        else:
            est_units = 0
        est_cards = estimate_evidence_cards(resolved, limits)

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
        # P0-004: path_hints narrow scope to specific path prefixes
        normalized_path_hints = [
            h for h in (_normalize_path_hint(hint) for hint in obj.path_hints) if h
        ]
        path_filter: list[str] | None = normalized_path_hints or None

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

        for hit in _path_hint_repo_hits_blocking(
            repo_path, normalized_path_hints, limits.max_files_per_object
        ):
            key = ("file", normalize_file_key(repo_path, hit))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            file_candidates.append(
                ScopeCandidate(
                    path=hit,
                    source="repo_search",
                    confidence="high",
                    reason="分析对象 path_hints 精确命中源码文件",
                    role="primary",
                )
            )

        if gitnexus_available:
            for node, hits in index.search_files(
                keywords, limits.max_files_per_object, path_filter=path_filter
            ):
                path = _node_path(node)
                key = ("file", normalize_file_key(repo_path, path))
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
                        role="primary" if path_filter else "related",
                    )
                )
            for node, hits in index.search_symbols(
                keywords, limits.max_functions_per_object, path_filter=path_filter
            ):
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
                        role="primary" if path_filter else "related",
                    )
                )

        # Round 4 P1: when the analysis object is an exact code symbol, always
        # also pull local source hits — a wrong/incomplete GitNexus graph (e.g.
        # a same-named repo) must not be able to hide the real implementation
        # files (log_flags.c / log_deprecated.c were silently missed).
        if _looks_like_symbol(obj.text):
            exact_hits = await _exact_symbol_repo_hits(
                repo_path, obj.text.strip(), limits.max_files_per_object
            )
            exact_candidates: list[ScopeCandidate] = []
            for hit in exact_hits:
                key = ("file", normalize_file_key(repo_path, hit))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                exact_candidates.append(
                    ScopeCandidate(
                        path=hit,
                        symbol=obj.text.strip(),
                        source="repo_search",
                        confidence="high",
                        reason="本地源码精确符号命中，优先作为事实证据（防止图谱漏召回）",
                        role="primary",
                    )
                )
            # Exact local symbol evidence is the most trustworthy grounding when
            # GitNexus repo disambiguation is degraded, so it must not be pushed
            # out by graph-derived callers before evidence-card budgeting.
            file_candidates = exact_candidates + file_candidates

        # If GitNexus is empty OR returned nothing, fall back to repo search.
        if not file_candidates:
            repo_hits = await _bounded_repo_search(
                repo_path, keywords, limits.max_files_per_object
            )
            for hit in repo_hits:
                key = ("file", normalize_file_key(repo_path, hit))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                file_candidates.append(
                    ScopeCandidate(
                        path=hit,
                        source="repo_search",
                        confidence="medium",
                        reason="本地代码搜索命中关键字",
                        role="related",
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


# ---------------------------------------------------------------------------
# Shared analysis-unit planner (P0: preview ↔ execution consistency)
#
# Both the preview (WorkspaceScopeResolver.resolve) and the execution pipeline
# (AnalysisPipeline._group_analysis_units) MUST plan the same number of analysis
# units for the same input.  Previously the preview *estimated* units as
# ``min(max_units, len(objects))`` while execution ran a union-find merge over
# shared files, so 3 objects touching one module collapsed to 1 unit at
# execution time but were advertised as 3 in the preview.  Centralising the
# algorithm here removes that drift.
# ---------------------------------------------------------------------------


def normalize_file_key(repo_path: str, path_str: str) -> str:
    """Normalise a candidate/evidence file path to a stable comparison key.

    Mirrors EvidenceCardBuilder's resolution (relative paths are joined to the
    repo root and resolved) so the preview and the pipeline bucket the same
    files together.
    """
    if not path_str:
        return ""
    try:
        p = Path(path_str)
        if not p.is_absolute() and repo_path:
            p = (Path(repo_path) / path_str).resolve()
        return str(p)
    except Exception:
        return path_str


def plan_analysis_units(
    object_files: list[tuple[str, list[str]]], max_units: int
) -> list[list[str]]:
    """Union-find grouping of analysis objects that share at least one file.

    ``object_files`` is an *ordered* list of ``(object_id, [file_key, ...])``.
    Objects that reference a common file key collapse into one unit.  The
    result is capped at ``max_units`` by merging the tail groups into the last
    bucket — identical to the execution-side cap so counts always agree.

    Returns groups as ordered lists of ``object_id``.
    """
    order = [oid for oid, _ in object_files]
    parent: dict[str, str] = {oid: oid for oid in order}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    file_owner: dict[str, str] = {}
    for oid, files in object_files:
        for key in files:
            if not key:
                continue
            prev = file_owner.get(key)
            if prev is None:
                file_owner[key] = oid
            else:
                union(prev, oid)

    groups: dict[str, list[str]] = {}
    for oid in order:
        groups.setdefault(find(oid), []).append(oid)
    group_list = list(groups.values())

    cap = max(int(max_units or 0), 1)
    if len(group_list) > cap:
        head = group_list[: cap - 1]
        tail_members = [oid for grp in group_list[cap - 1 :] for oid in grp]
        head.append(tail_members)
        group_list = head
    return group_list


def estimate_evidence_cards(resolved_objects, limits) -> int:
    """Estimate the evidence-card count the EvidenceCardBuilder will emit.

    Replicates the builder's per-object caps (files capped at
    ``max(2, max_files_per_object // 2)``, symbols topped up to ``cap + 4``,
    plus one community card) so the preview's card count tracks reality instead
    of summing every raw candidate.
    """
    per_object_cap = max(2, limits.max_files_per_object // 2)
    total = 0
    for r in resolved_objects:
        seen_paths: set[str] = set()
        n_files = 0
        for c in r.candidate_files:
            key = c.path or ""
            if key and key in seen_paths:
                continue
            seen_paths.add(key)
            n_files += 1
            if n_files >= per_object_cap:
                break
        cards = n_files
        for _c in r.candidate_symbols[: limits.max_functions_per_object]:
            if cards >= per_object_cap + 4:
                break
            cards += 1
        if r.related_communities:
            cards += 1
        if cards == 0:
            cards = 1  # builder always emits a "未解析" placeholder card
        total += cards
    return min(limits.max_evidence_cards, total)
