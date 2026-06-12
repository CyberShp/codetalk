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
from contextlib import suppress
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlparse

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
from app.services.external_agent_discovery import (
    AgentDiscoveryRequest,
    AgentDiscoveryResult,
    expand_agent_query_terms,
    merge_agent_provider_status,
    merge_source_candidates,
    run_external_agent_discovery,
    validate_agent_candidate_file,
)
from app.services.agent_discovery_session import (
    AgentContextPacketInput,
    AgentDiscoverySession,
    create_agent_discovery_session,
)
from app.utils.repo_paths import to_tool_repo_path

logger = logging.getLogger(__name__)

_DIR_SKIP = frozenset({
    "node_modules", "__pycache__", ".git", ".venv", "venv", "dist",
    "build", ".next", "vendor", "coverage", ".tox", ".mypy_cache",
    ".pytest_cache", "target", "out", "bin", "obj",
})

_SOURCE_EXTS = frozenset({
    ".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".go", ".rs", ".java",
    ".c", ".h", ".hh", ".hpp", ".hxx", ".cc", ".cpp", ".cxx", ".ipp", ".inl",
    ".cs", ".rb", ".php",
    ".kt", ".kts", ".swift", ".m", ".scala", ".vue", ".svelte", ".astro", ".mdx",
    ".proto", ".thrift",
})

_SCOPE_ROLES = {"primary", "supporting", "external"}

# Stopwords pruned from analysis-object text before keyword search.
# We mix English and Chinese; case is folded for English only.
_STOPWORDS_EN = frozenset({
    "the", "and", "for", "with", "from", "into", "that", "this",
    "path", "flow", "case", "code", "data", "value", "values",
    "error", "errors", "logic", "long", "short", "handle", "handling",
    "of", "in", "on", "to", "or", "if", "is", "be", "a", "an",
    "analyze", "analysis", "please", "module", "modules", "source", "sources",
    "repo", "repository", "project", "workspace", "find", "locate", "search",
    "target", "object", "objects",
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


def _path_token_singular_plural_variants(token: str) -> list[str]:
    value = (token or "").strip().lower()
    if not value or not re.fullmatch(r"[a-z][a-z0-9]*", value):
        return [value] if value else []
    variants = [value]
    if value.endswith("ies") and len(value) > 4:
        variants.append(value[:-3] + "y")
    if value.endswith("es") and len(value) > 3:
        variants.append(value[:-2])
    if value.endswith("s") and len(value) > 3:
        variants.append(value[:-1])
    elif len(value) > 2:
        variants.append(value + "s")
    return list(dict.fromkeys(variants))


def _path_singular_plural_variants(path_like: str) -> list[str]:
    normalized = re.sub(r"[-_]+", "/", (path_like or "").strip("/").lower())
    parts = [part for part in normalized.split("/") if part]
    if not parts or len(parts) > 5:
        return []
    variants: list[list[str]] = [
        _path_token_singular_plural_variants(part)[:3]
        for part in parts
    ]
    out: list[str] = []

    def visit(index: int, current: list[str]) -> None:
        if len(out) >= 24:
            return
        if index >= len(variants):
            value = "/".join(current)
            if value != normalized:
                out.append(value)
            return
        for item in variants[index]:
            visit(index + 1, [*current, item])

    visit(0, [])
    return list(dict.fromkeys(out))


def _keyword_path_variants(keyword: str) -> list[str]:
    value = (keyword or "").strip().replace("\\", "/")
    if not value:
        return []
    variants: list[str] = []
    seen: set[str] = set()

    def add(item: str) -> None:
        normalized = item.strip("/").lower()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        variants.append(normalized)

    add(value)
    qualified_path = re.sub(r"[:#]+", "/", value)
    add(qualified_path)
    dotted = qualified_path.replace(".", "/")
    add(dotted)
    add(dotted.replace("/", "_"))
    add(dotted.replace("/", "-"))
    separator_path = re.sub(r"[-_\s]+", "/", dotted)
    add(separator_path)
    add(separator_path.replace("/", "_"))
    add(separator_path.replace("/", "-"))
    dotted_snake = "/".join(
        _camel_segment_to_snake(part)
        for part in dotted.split("/")
        if part
    )
    add(dotted_snake)
    add(dotted_snake.replace("/", "_"))
    add(dotted_snake.replace("/", "-"))
    add(dotted_snake.replace("/", "-").replace("_", "-"))
    for singular_plural in _path_singular_plural_variants(dotted):
        add(singular_plural)
        add(singular_plural.replace("/", "_"))
        add(singular_plural.replace("/", "-"))
    suffix_sources = [dotted, separator_path, dotted_snake]
    for source in suffix_sources:
        parts = [part for part in source.split("/") if part]
        for index in range(1, max(1, len(parts) - 1)):
            suffix = "/".join(parts[index:])
            if "/" not in suffix:
                continue
            add(suffix)
            add(suffix.replace("/", "_"))
            add(suffix.replace("/", "-"))
            for singular_plural in _path_singular_plural_variants(suffix):
                add(singular_plural)
                add(singular_plural.replace("/", "_"))
                add(singular_plural.replace("/", "-"))
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    snake = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", snake)
    add(snake)
    add(snake.replace("_", "/"))
    add(snake.replace("_", "-"))
    compact = re.sub(r"[^A-Za-z0-9]+", "", value)
    if compact != value:
        add(compact)
    return variants


def _camel_segment_to_snake(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", text)
    text = re.sub(r"[-\s]+", "_", text)
    return text.lower()


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
    graph, _warning = await _fetch_live_gitnexus_graph_with_warning(repo_path)
    return graph


async def _fetch_live_gitnexus_graph_with_warning(repo_path: str) -> tuple[dict | None, str]:
    """Best-effort live fetch from GitNexus with a user-facing diagnostic."""
    try:
        tool_path = to_tool_repo_path(
            repo_path,
            host_base_path=settings.repos_base_path,
            tool_base_path=settings.tool_repos_base_path,
            local_host_path=settings.local_repos_host_path,
            local_container_path=settings.local_repos_container_path,
        )
    except Exception as exc:
        return None, f"GitNexus repo path translation failed: {_short_error(exc)}"

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
            return resp.json(), ""
    except Exception as exc:
        logger.info("Live GitNexus graph fetch failed: %s", exc)
        return (
            None,
            f"GitNexus live graph fetch failed from {settings.gitnexus_base_url}: {_short_error(exc)}",
        )


def _short_error(exc: Exception, limit: int = 240) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


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
            source_files: list[str] = []
            for file in files:
                path = Path(file)
                if path.suffix.lower() not in _SOURCE_EXTS:
                    continue
                try:
                    path.resolve().relative_to(repo.resolve())
                except Exception:
                    continue
                source_files.append(str(path))
                if len(source_files) >= limit:
                    break
            return source_files
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


def _path_keyword_repo_hits_blocking(
    repo_path: str, keywords: list[str], limit: int
) -> list[str]:
    """Rank source files by path tokens, not content.

    This is the local deterministic half of agentic discovery: a fuzzy string
    like ``payment-webhook`` should match ``services/payments/webhook/handler.ts``
    even when the file content does not contain the exact spelling.
    """
    if not repo_path or not keywords or limit <= 0:
        return []
    root = Path(repo_path)
    if not root.is_dir():
        return []
    folded = list(dict.fromkeys(
        variant
        for kw in keywords
        for variant in _keyword_path_variants(kw)
    ))
    results: list[tuple[str, int]] = []
    for walk_root, dirs, files in _walk(root):
        dirs[:] = [d for d in dirs if d not in _DIR_SKIP and not d.startswith(".")]
        for fname in files:
            full = Path(walk_root) / fname
            if full.suffix.lower() not in _SOURCE_EXTS:
                continue
            rel = full.relative_to(root).as_posix().lower()
            rel_tokenized = re.sub(r"[-_]+", "/", rel)
            dir_tokenized = re.sub(r"[-_]+", "/", str(Path(rel).parent).replace("\\", "/"))
            stem = full.stem.lower()
            score = 0
            matched_parts: set[str] = set()
            for kw in folded:
                kw_tokenized = re.sub(r"[-_]+", "/", kw)
                if kw in rel:
                    score += 4
                elif kw_tokenized in rel_tokenized:
                    score += 4
                if kw_tokenized and (
                    dir_tokenized == kw_tokenized
                    or dir_tokenized.endswith(f"/{kw_tokenized}")
                ):
                    score += 12
                if kw not in rel and kw_tokenized not in rel_tokenized:
                    parts = [p for p in re.split(r"[/_-]+", kw) if p]
                    hit_count = sum(1 for part in parts if part in rel_tokenized)
                    if len(parts) >= 2 and hit_count >= 2:
                        score += hit_count
                    dir_hit_count = sum(1 for part in parts if part in dir_tokenized)
                    if len(parts) >= 2 and dir_hit_count == len(parts):
                        score += 8
                for part in re.split(r"[/_-]+", kw):
                    if part:
                        matched_parts.add(part)
            if score:
                if stem in matched_parts:
                    score += 10
                elif Path(rel).parent.name.lower() in matched_parts:
                    score += 5
                elif any(part and part in stem for part in matched_parts):
                    score += 3
                if full.suffix.lower() in _SOURCE_EXTS:
                    score += 2
                score += _path_keyword_alignment_bonus(rel, folded)
                if rel.startswith((
                    "example/", "examples/", "sample/", "samples/",
                    "test/", "tests/", "doc/", "docs/",
                )):
                    score -= 8
                results.append((str(full), score))
    results.sort(key=lambda item: (-item[1], item[0].lower()))
    return [path for path, _score in results[:limit]]


def _path_keyword_alignment_bonus(rel_path: str, keywords: list[str]) -> int:
    """Reward generic directory continuity without protocol-specific boosts."""
    rel = str(rel_path or "").lower().replace("\\", "/")
    if not rel:
        return 0
    dir_path = str(Path(rel).parent).replace("\\", "/")
    dir_tokenized = re.sub(r"[-_]+", "/", dir_path)
    stem_tokenized = re.sub(r"[-_]+", "/", Path(rel).stem.lower())
    bonus = 0
    for keyword in keywords:
        parts = [part for part in re.split(r"[/_-]+", keyword.lower()) if part]
        if len(parts) < 2:
            continue
        variant = "/".join(parts)
        if dir_tokenized == variant or dir_tokenized.endswith(f"/{variant}"):
            bonus = max(bonus, 12 + len(parts) * 2)
        elif f"/{variant}/" in f"/{dir_tokenized}/":
            bonus = max(bonus, 8 + len(parts))
        ordered_hits = _ordered_path_part_hits(dir_tokenized, parts)
        if ordered_hits >= 2:
            bonus = max(bonus, ordered_hits * 3)
        if stem_tokenized == variant:
            bonus = max(bonus, 8 + len(parts))
    return bonus


def _ordered_path_part_hits(path_text: str, parts: list[str]) -> int:
    if not path_text or not parts:
        return 0
    position = 0
    hits = 0
    for part in parts:
        found = path_text.find(part, position)
        if found < 0:
            continue
        hits += 1
        position = found + len(part)
    return hits


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
                parsed = _parse_ripgrep_line(line)
                if parsed is None:
                    continue
                path, _line_number, text = parsed
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


def _parse_ripgrep_line(raw: str) -> tuple[str, int, str] | None:
    match = re.match(r"^(?P<path>.*?):(?P<line>\d+):(?P<text>.*)$", raw or "")
    if not match:
        return None
    try:
        line_number = int(match.group("line"))
    except ValueError:
        return None
    return match.group("path"), line_number, match.group("text")


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
        module_name = directory.name.lower()
        files.sort(key=lambda p: (
            0 if p.parent == directory and p.stem.lower() == module_name else 1,
            p.relative_to(root).as_posix().lower(),
        ))
        return files

    def _path_hint_candidate_sort_key(path: Path) -> tuple[int, int, str]:
        try:
            rel = path.resolve().relative_to(root).as_posix().lower()
        except Exception:
            rel = path.as_posix().lower()
        score = 0
        if rel.startswith((
            "example/", "examples/", "sample/", "samples/",
            "test/", "tests/", "doc/", "docs/",
        )):
            score += 30
        return (score, rel.count("/"), rel)

    def _suffix_candidate_paths(normalized_hint: str) -> list[Path]:
        hint = normalized_hint.lower().strip("/")
        if not hint:
            return []
        matches: list[Path] = []
        for walk_root, dirs, names in _walk(root):
            dirs[:] = [d for d in dirs if d not in _DIR_SKIP and not d.startswith(".")]
            current = Path(walk_root)
            try:
                rel_dir = current.relative_to(root).as_posix().lower()
            except Exception:
                continue
            if rel_dir == hint or rel_dir.endswith("/" + hint):
                matches.append(current)
            for name in names:
                full = current / name
                if full.suffix.lower() not in _SOURCE_EXTS:
                    continue
                try:
                    rel_file = full.relative_to(root).as_posix().lower()
                except Exception:
                    continue
                if rel_file == hint or rel_file.endswith("/" + hint):
                    matches.append(full)
        matches.sort(key=_path_hint_candidate_sort_key)
        return matches[:64]

    def _candidate_paths_for_hint(normalized_hint: str) -> list[Path]:
        try:
            candidate = Path(normalized_hint)
            if candidate.is_absolute():
                return [candidate.resolve()]
            parts = [part for part in normalized_hint.split("/") if part]
            candidates = [root.joinpath(*parts).resolve()]
            seen_candidates = {str(candidates[0])}
            for index in range(1, len(parts)):
                suffix = parts[index:]
                if not suffix:
                    continue
                suffix_candidate = root.joinpath(*suffix)
                try:
                    if suffix_candidate.exists():
                        resolved_suffix = suffix_candidate.resolve()
                        key = str(resolved_suffix)
                        if key not in seen_candidates:
                            seen_candidates.add(key)
                            candidates.append(resolved_suffix)
                except OSError:
                    continue
            for suffix_candidate in _suffix_candidate_paths(normalized_hint):
                try:
                    resolved_suffix = suffix_candidate.resolve()
                except OSError:
                    continue
                key = str(resolved_suffix)
                if key in seen_candidates:
                    continue
                seen_candidates.add(key)
                candidates.append(resolved_suffix)
            return candidates
        except Exception:
            return []

    for hint in path_hints:
        normalized_hint = _normalize_path_hint(hint)
        if not normalized_hint:
            continue
        for variant in _path_hint_variants(normalized_hint):
            for candidate in _candidate_paths_for_hint(variant):
                try:
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
            if len(results) >= limit:
                break
        if len(results) >= limit:
            break
    return results


def _scope_external_agent_warnings(resolved: list[ResolvedAnalysisObject]) -> list[str]:
    warnings: list[str] = []
    for obj in resolved:
        for warning in obj.warnings:
            text = str(warning).strip()
            if not text:
                continue
            if "claude-code:" not in text and "opencode:" not in text and "external agent" not in text:
                continue
            warnings.append(f"{obj.object_id}: {text}")
            if len(warnings) >= 16:
                return warnings
    return warnings


def _scope_external_agent_status_from_warnings(
    resolved: list[ResolvedAnalysisObject],
) -> dict[str, str]:
    """Recover provider status for preview-only runs without a persisted session."""
    statuses: dict[str, str] = {}
    valid_statuses = {
        "ok",
        "available",
        "unavailable",
        "timeout",
        "invalid_output",
        "rejected_command",
        "configuration_error",
        "error",
    }
    provider_re = re.compile(r"^(claude-code|opencode|external_agent):\s*([a-z_]+)\b")
    rank = {
        "ok": 0,
        "available": 0,
        "unavailable": 1,
        "timeout": 2,
        "invalid_output": 3,
        "rejected_command": 4,
        "configuration_error": 5,
        "error": 6,
    }
    for obj in resolved:
        for warning in obj.warnings:
            match = provider_re.match(str(warning).strip())
            if not match:
                continue
            provider, status = match.groups()
            if status not in valid_statuses:
                continue
            normalized = "available" if status == "ok" else status
            current = statuses.get(provider)
            if current is None or rank.get(normalized, 9) > rank.get(current, 9):
                statuses[provider] = normalized
    return statuses


async def _await_with_agent_cleanup(awaitable, agent_task: asyncio.Task | None):
    try:
        return await awaitable
    except asyncio.CancelledError:
        await _cancel_and_drain_task(agent_task)
        raise


async def _cancel_and_drain_task(task: asyncio.Task | None) -> None:
    if task is None:
        return
    if not task.done():
        task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await task


def _infer_scope_role(path_hint: str) -> str:
    """Infer a conservative evidence role for legacy path_hints."""
    value = _normalize_path_hint(path_hint).lower().strip("/")
    if not value:
        return "supporting"
    first = value.split("/", 1)[0]
    if first in {"lib", "src", "source", "include"}:
        return "primary"
    if first in {"app", "apps", "cli", "cmd", "test", "tests", "example", "examples"}:
        return "supporting"
    if first in {"doc", "docs", "manual", "tutorial"}:
        return "external"
    return "supporting"


def _scope_role_for_path(path: str, hints: list[tuple[str, str]]) -> str:
    """Return the strongest explicit/inferred role matching a file path."""
    normalized = _normalize_path_hint(path).lower()
    best: str | None = None
    rank = {"primary": 3, "supporting": 2, "external": 1}
    for hint, role in hints:
        h = _normalize_path_hint(hint).lower()
        if not h:
            continue
        if normalized == h or normalized.startswith(h.rstrip("/") + "/") or h in normalized:
            if best is None or rank.get(role, 0) > rank.get(best, 0):
                best = role
    return best or "supporting"


def _scope_role_for_keyword_hit(repo_path: str, path: str, keywords: list[str]) -> str:
    """Infer whether a fuzzy local path hit is primary evidence.

    A path-expansion hit is primary when the matched file lives under a module
    directory named by a multi-token query (for example PaymentWebhook ->
    payment/webhook) or the file/directory stem directly matches a query token.
    """
    try:
        rel = Path(path).resolve().relative_to(Path(repo_path).resolve()).as_posix().lower()
    except Exception:
        rel = str(path).replace("\\", "/").lower()
    rel_tokenized = re.sub(r"[-_]+", "/", rel)
    parent = Path(rel).parent.as_posix().lower()
    parent_tokenized = re.sub(r"[-_]+", "/", parent)
    stem = Path(rel).stem.lower()
    parent_name = Path(parent).name.lower()

    for keyword in keywords:
        for variant in _keyword_path_variants(keyword):
            variant_tokenized = re.sub(r"[-_]+", "/", variant.strip("/").lower())
            if not variant_tokenized:
                continue
            parts = [part for part in variant_tokenized.split("/") if part]
            if len(parts) >= 2 and (
                parent_tokenized == variant_tokenized
                or parent_tokenized.endswith(f"/{variant_tokenized}")
                or f"/{variant_tokenized}/" in f"/{rel_tokenized}/"
            ):
                return "primary"
            if len(parts) == 1 and (
                stem == parts[0]
                or parent_name == parts[0]
                or f"/{parts[0]}/" in f"/{parent_tokenized}/"
            ):
                return "primary"
    return "related"


def _record_agent_scope_file(
    session: AgentDiscoverySession | None,
    *,
    object_id: str,
    repo_path: str,
    path: str,
    provider: str,
    reason: str,
    confidence: str = "medium",
    validated: bool = False,
) -> None:
    if session is None:
        return
    validation = validate_agent_candidate_file(repo_path, path)
    normalized = validation.path or normalize_file_key(repo_path, path)
    target = (
        session.ledger.gitnexus_candidates
        if provider == "gitnexus"
        else session.ledger.local_candidates
    )
    target.append({
        "object_id": object_id,
        "path": normalized,
        "provider": provider,
        "reason": reason,
        "confidence": confidence,
    })
    if validated:
        session.ledger.add_validated_file(
            object_id=object_id,
            path=normalized,
            provider=provider,
            reason=reason,
            confidence=confidence,
        )


def _record_agent_scope_results(
    session: AgentDiscoverySession | None,
    *,
    object_id: str,
    repo_path: str,
    results: list,
) -> None:
    if session is None:
        return
    for result in results:
        session.ledger.provider_status[result.provider] = merge_agent_provider_status(
            session.ledger.provider_status.get(result.provider),
            result.status,
        )
        for command in result.commands:
            session.ledger.command_history.append({
                "object_id": object_id,
                "provider": result.provider,
                "command": command,
            })
        for file in result.candidate_files:
            validation = validate_agent_candidate_file(repo_path, file.path)
            if validation.validated and validation.path:
                session.ledger.add_validated_file(
                    object_id=object_id,
                    path=validation.path,
                    provider=result.provider,
                    reason=file.reason or "agent validated source",
                    confidence=file.confidence,
                )
            else:
                session.ledger.add_rejected_file(
                    object_id=object_id,
                    path=file.path,
                    provider=result.provider,
                    reason=validation.validation_error or file.validation_error or "invalid_agent_file",
                )
        for entry in result.candidate_entries:
            item = {
                "object_id": object_id,
                "provider": result.provider,
                "entry_kind": entry.entry_kind,
                "entry_symbol": entry.entry_symbol,
                "entry_file": entry.entry_file,
                "chain": entry.chain,
                "reason": entry.reason,
                "validation_error": entry.validation_error,
            }
            if entry.entry_file:
                validation = validate_agent_candidate_file(
                    repo_path,
                    entry.entry_file,
                    allow_directory_candidates=False,
                )
                item["entry_file"] = validation.path or entry.entry_file
                item["validation_error"] = validation.validation_error
            else:
                validation = None
                item["validation_error"] = item.get("validation_error") or "entry_file_missing"
            if validation is not None and validation.validated:
                item["validation_error"] = None
                session.ledger.add_validated_entry(item)
            else:
                session.ledger.add_rejected_entry(item)
    session.save()


def _agent_scope_error_result(
    session: AgentDiscoverySession | None,
    *,
    goal: str,
    turn_id: str,
    exc: Exception,
) -> AgentDiscoveryResult:
    summary = str(exc).strip() or exc.__class__.__name__
    result = AgentDiscoveryResult(
        provider="external_agent",
        status="error",
        turn_id=turn_id,
        raw_summary=summary,
        warnings=[summary],
    )
    if session is not None:
        session.record_turn(
            provider=result.provider,
            goal=goal,
            prompt=f"{turn_id} failed before provider results were returned",
            raw_output=summary,
            parsed_result={},
            validation_result={"error": summary},
            status=result.status,
        )
    return result


def _agent_requests_source_slices(results: list) -> bool:
    return any(getattr(result, "need_source_slices", None) for result in results)


def _agent_has_rejected_files(session: AgentDiscoverySession | None, object_id: str) -> bool:
    if session is None:
        return False
    return any(
        item.get("object_id") in {"", object_id}
        for item in session.ledger.rejected_files
    )


def _normalize_path_hint(hint: str) -> str:
    """Normalize UI/user path hints before comparing with repo paths."""
    value = (hint or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme.lower() == "file":
        value = f"//{parsed.netloc}{parsed.path}" if parsed.netloc else parsed.path
    elif parsed.scheme.lower() in {"http", "https"}:
        value = _normalize_remote_path_hint(parsed.path)
    value = re.sub(r"[\r\n\t]+", "/", value)
    value = value.replace("\\", "/")
    value = re.sub(r"/+", "/", value)
    value = unquote(value)
    value = re.sub(r"#L\d+(?:[-,~]L?\d+)?$", "", value, flags=re.IGNORECASE)
    value = re.sub(r":\d+:\d+$", "", value)
    value = re.sub(r":\d+(?:-\d+)?$", "", value)
    return value.rstrip("/")


def _normalize_remote_path_hint(path: str) -> str:
    value = unquote(path or "").replace("\\", "/").strip("/")
    if not value:
        return ""
    parts = [part for part in value.split("/") if part]
    for index, part in enumerate(parts):
        if part == "-":
            continue
        if part not in {"blob", "raw", "src"}:
            continue
        next_index = index + 1
        if next_index < len(parts) and parts[next_index] == "-":
            next_index += 1
        file_start = next_index + 1
        if file_start < len(parts):
            return "/".join(parts[file_start:])
    return value


def _path_hint_variants(normalized_hint: str) -> list[str]:
    """Return legacy typo/domain variants for a normalized path hint.

    Generic parent-directory stripping is handled by
    ``_path_hint_search_variants`` below; this helper is intentionally limited
    to known historical spellings so protocol-specific aliases are not the only
    way a path hint can recover.
    """
    hint = normalized_hint.strip("/")
    if not hint:
        return []
    variants = [hint]
    replacements = (
        ("/of/vmf_tcp/", "/nof/nvmf_tcp/"),
        ("of/vmf_tcp/", "nof/nvmf_tcp/"),
        ("/vmf_tcp/", "/nvmf_tcp/"),
        ("vmf_tcp/", "nvmf_tcp/"),
        ("/nvme_tcp/", "/nvmf_tcp/"),
        ("nvme_tcp/", "nvmf_tcp/"),
    )
    for source, target in replacements:
        if source in hint:
            variants.append(hint.replace(source, target))
    return list(dict.fromkeys(variants))


def _path_hint_search_variants(hints: list[str]) -> list[str]:
    """Expand path hints for all discovery backends.

    The important generic rule is suffix matching: when the UI sends a path
    rooted too high or too low (for example ``frontend/app/payments/refund``),
    GitNexus/local/Agent discovery should also try ``app/payments/refund`` and
    ``payments/refund``.  Domain-specific repairs are optional variants, not the
    primary mechanism.
    """
    expanded: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        value = value.strip("/")
        if not value or value in seen:
            return
        seen.add(value)
        expanded.append(value)

    for hint in hints:
        for variant in _path_hint_variants(hint):
            add(variant)
            parts = [part for part in variant.split("/") if part]
            for index in range(1, max(1, len(parts) - 1)):
                suffix = "/".join(parts[index:])
                if "/" in suffix:
                    add(suffix)
    return expanded


def _expand_role_hints(role_hints: list[tuple[str, str]]) -> list[tuple[str, str]]:
    expanded: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for hint, role in role_hints:
        for variant in _path_hint_search_variants([hint]):
            key = (variant, role)
            if key in seen:
                continue
            seen.add(key)
            expanded.append(key)
    return expanded


def _score_symbol_hit(path: str, line: str, symbol: str) -> int:
    folded_path = path.replace("\\", "/").lower()
    stripped = line.strip()
    score = 10

    starts_with_call = re.match(rf"^{re.escape(symbol)}\s*\(", stripped)
    has_statement_semicolon = ";" in stripped
    if _is_symbol_definition_line(stripped, symbol):
        score += 140
    elif starts_with_call and not has_statement_semicolon:
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


def _is_symbol_definition_line(line: str, symbol: str) -> bool:
    line = re.sub(r"^\s*<\?(?:php)?\s*", "", line, flags=re.IGNORECASE)
    escaped = re.escape(symbol)
    if re.match(rf"^(?:async\s+)?def\s+{escaped}\s*\(", line):
        return True
    if re.match(rf"^(?:export\s+)?(?:async\s+)?function\s+{escaped}\s*\(", line):
        return True
    if re.match(
        rf"^(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+{escaped}\s*\(",
        line,
    ):
        return True
    if re.match(
        rf"^func\s+(?:\([^)]*\)\s+)?{escaped}\s*\(",
        line,
    ):
        return True
    if re.match(
        rf"^(?:(?:public|private|fileprivate|internal|open|static|class|"
        rf"mutating|nonmutating|override|final|required|convenience)\s+)*"
        rf"func\s+{escaped}\s*\(",
        line,
    ):
        return True
    if re.match(
        rf"^(?:(?:public|private|protected|internal|override|open|final|"
        rf"abstract|suspend|inline|operator|tailrec)\s+)*fun\s+{escaped}\s*\(",
        line,
    ):
        return True
    if re.match(
        rf"^(?:(?:private|protected|override|final|abstract|implicit|inline|"
        rf"given|transparent)\s+)*def\s+{escaped}\s*(?:\([^)]*\))?"
        rf"\s*(?::\s*[^=]+)?\s*(?:=|\{{|$)",
        line,
    ):
        return True
    if re.match(
        rf"^\s*def\s+(?:(?:self|[A-Z][\w:]*)\.)"
        rf"{escaped}\b",
        line,
    ):
        return True
    if re.match(
        rf"^\s*[-+]\s*\([^)]*\)\s*{escaped}\s*(?::|\{{)",
        line,
    ):
        return True
    if re.match(
        rf"^(?:export\s+)?(?:const|let|var)\s+{escaped}"
        rf"(?:\s*:\s*[^=]+)?\s*=\s*(?:async\s*)?"
        rf"(?:(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>|function\b)",
        line,
    ):
        return True
    if re.match(
        rf"^(?:(?:public|private|protected|static|readonly)\s+)*{escaped}"
        rf"(?:\s*:\s*[^=]+)?\s*=\s*(?:async\s*)?"
        rf"(?:(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>|function\b)",
        line,
    ):
        return True
    if re.match(
        rf"^\s*(?:module\.)?exports\.{escaped}\s*=\s*(?:async\s*)?"
        rf"(?:function\b|(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>)",
        line,
    ):
        return True
    if re.match(
        rf"^{escaped}\s*:\s*(?:async\s*)?"
        rf"(?:(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>|function\b)",
        line,
    ):
        return True
    if re.match(rf"^{escaped}\s*=\s*lambda\b", line):
        return True
    if re.match(
        rf"^(?:static\s+|inline\s+|extern\s+|const\s+|unsigned\s+|signed\s+|"
        rf"void\s+|bool\s+|char\s+|int\s+|long\s+|short\s+|size_t\s+|"
        rf"ssize_t\s+|uint\d+_t\s+|int\d+_t\s+|struct\s+\w+\s+|enum\s+\w+\s+)+"
        rf"[\w\s\*&:<>,~\[\]]*\b{escaped}\s*\([^;]*\)\s*(?:\{{|$)",
        line,
    ):
        return True
    if re.match(
        rf"^(?!(?:if|for|while|switch|return)\b)"
        rf"[\w:<>,~\*&\s]+\s+(?:[\w:]+::)*{escaped}\s*\([^;]*\)\s*(?:\{{|$)",
        line,
    ):
        return True
    return False


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


async def _path_keyword_repo_hits(
    repo_path: str, keywords: list[str], limit: int
) -> list[str]:
    return await asyncio.to_thread(
        _path_keyword_repo_hits_blocking, repo_path, keywords, limit
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
        task_id: str | None = None,
        artifact_dir: Path | None = None,
        external_agents_enabled: bool | None = None,
    ) -> ScopePreview:
        limits = plan.llm_limits
        use_external_agents = (
            settings.external_agents_enabled
            if external_agents_enabled is None
            else external_agents_enabled
        )
        agent_session: AgentDiscoverySession | None = None
        if use_external_agents and settings.agent_discovery_session_enabled:
            session_dir = artifact_dir or (
                settings.outputs_path / task_id if task_id else None
            )
            if session_dir is not None:
                agent_session = create_agent_discovery_session(
                    repo_path=repo_path,
                    goal="workspace_scope",
                    task_id=task_id,
                    workspace_id=ws_id,
                    artifact_dir=session_dir,
                )

        graph = await _load_cached_gitnexus_graph(repo_path)
        used_cache = graph is not None
        live_gitnexus_warning = ""
        if graph is None:
            graph, live_gitnexus_warning = await _fetch_live_gitnexus_graph_with_warning(repo_path)
        index = _GraphIndex(graph)
        gitnexus_available = not index.is_empty

        warnings: list[str] = []
        if not gitnexus_available:
            warnings.append(
                "GitNexus 图谱当前不可用，已退回到本地代码搜索；结果可能不完整。"
            )
            if live_gitnexus_warning:
                warnings.append(live_gitnexus_warning)
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
                agent_session=agent_session,
                external_agents_enabled=use_external_agents,
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
        external_agent_warnings = _scope_external_agent_warnings(resolved)
        external_agent_status = (
            dict(agent_session.ledger.provider_status)
            if agent_session
            else _scope_external_agent_status_from_warnings(resolved)
        )

        return ScopePreview(
            workspace_id=ws_id,
            resolved_objects=resolved,
            estimated_analysis_units=est_units,
            estimated_evidence_cards=est_cards,
            warnings=warnings,
            gitnexus_available=gitnexus_available,
            external_agent_status=external_agent_status,
            external_agent_warnings=external_agent_warnings,
            agent_discovery_session_id=agent_session.session_id if agent_session else None,
            external_agent_turn_count=len(agent_session.turns) if agent_session else 0,
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
        agent_session: AgentDiscoverySession | None = None,
        external_agents_enabled: bool = True,
        ) -> ResolvedAnalysisObject:
        keywords = _tokenize(obj.text)
        expanded_terms = expand_agent_query_terms(obj.text)
        expanded_keywords = list(dict.fromkeys([*keywords, *expanded_terms]))[:32]
        obj_warnings: list[str] = []
        # P0-004: path_hints narrow scope to specific path prefixes
        normalized_path_hints = [
            h for h in (_normalize_path_hint(hint) for hint in obj.path_hints) if h
        ]
        explicit_scope_hints = [
            (_normalize_path_hint(hint.path), hint.role)
            for hint in getattr(obj, "scope_hints", []) or []
            if _normalize_path_hint(hint.path) and hint.role in _SCOPE_ROLES
        ]
        if explicit_scope_hints:
            role_hints = _expand_role_hints(explicit_scope_hints)
        else:
            role_hints = _expand_role_hints(
                [(hint, _infer_scope_role(hint)) for hint in normalized_path_hints]
            )
        search_hints = _path_hint_search_variants([path for path, _role in role_hints])
        path_filter: list[str] | None = search_hints or None
        if agent_session is not None:
            agent_session.objects.append({
                "object_id": obj.id,
                "text": obj.text,
                "kind": obj.kind,
                "goal": "source_scope",
            })
            context_packet = agent_session.build_context_packet(
                AgentContextPacketInput(
                    object_id=obj.id,
                    current_goal="source_scope",
                    analysis_object_text=obj.text,
                    expanded_terms=expanded_keywords,
                    path_hints=search_hints,
                    scope_hints=[
                        {"path": path, "role": role} for path, role in role_hints
                    ],
                    round_index=1,
                )
            )
        else:
            context_packet = None
        agent_task: asyncio.Task | None = None
        if external_agents_enabled:
            agent_task = asyncio.create_task(run_external_agent_discovery(
                AgentDiscoveryRequest(
                    request_id=f"{ws_id}:{obj.id}",
                    repo_path=repo_path,
                    analysis_object_text=obj.text,
                    path_hints=search_hints,
                    scope_hints=[
                        {"path": path, "role": role} for path, role in role_hints
                    ],
                    existing_candidates=[],
                    context_packet=context_packet,
                    goal="source_scope",
                ),
                session=agent_session,
            ))

        if not expanded_keywords:
            await _cancel_and_drain_task(agent_task)
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
            repo_path, search_hints, limits.max_files_per_object
        ):
            key = ("file", normalize_file_key(repo_path, hit))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            _record_agent_scope_file(
                agent_session,
                object_id=obj.id,
                repo_path=repo_path,
                path=hit,
                provider="local",
                reason="path_hints exact source match",
                confidence="high",
                validated=True,
            )
            file_candidates.append(
                ScopeCandidate(
                    path=hit,
                    source="repo_search",
                    confidence="high",
                    reason="分析对象 path_hints 精确命中源码文件",
                    role=_scope_role_for_path(hit, role_hints),
                )
            )

        path_hits = await _await_with_agent_cleanup(
            _path_keyword_repo_hits(
                repo_path, expanded_keywords, limits.max_files_per_object
            ),
            agent_task,
        )
        for hit in path_hits:
            key = ("file", normalize_file_key(repo_path, hit))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            _record_agent_scope_file(
                agent_session,
                object_id=obj.id,
                repo_path=repo_path,
                path=hit,
                provider="local",
                reason="local path expansion matched fuzzy module name",
                confidence="high",
                validated=True,
            )
            file_candidates.append(
                ScopeCandidate(
                    path=hit,
                    source="repo_search",
                    confidence="high",
                    reason="local path expansion matched fuzzy module name",
                    role=_scope_role_for_keyword_hit(repo_path, hit, expanded_keywords),
                )
            )

        if gitnexus_available:
            for node, hits in index.search_files(
                expanded_keywords, limits.max_files_per_object, path_filter=path_filter
            ):
                path = _node_path(node)
                key = ("file", normalize_file_key(repo_path, path))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                _record_agent_scope_file(
                    agent_session,
                    object_id=obj.id,
                    repo_path=repo_path,
                    path=path,
                    provider="gitnexus",
                    reason="gitnexus file keyword match",
                    confidence="high" if hits > 1 else "medium",
                )
                related_nodes.append(node.get("id", ""))
                file_candidates.append(
                    ScopeCandidate(
                        path=path,
                        source="gitnexus",
                        confidence="high" if hits > 1 else "medium",
                        reason=f"GitNexus 文件命中关键字 ({hits} 次)",
                        role=_scope_role_for_path(path, role_hints) if path_filter else "related",
                    )
                )
            for node, hits in index.search_symbols(
                expanded_keywords, limits.max_functions_per_object, path_filter=path_filter
            ):
                sym = node.get("properties", {}).get("name") or node.get("id")
                key = ("symbol", sym)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                if agent_session is not None:
                    agent_session.ledger.gitnexus_candidates.append({
                        "object_id": obj.id,
                        "symbol": sym,
                        "path": _node_path(node) or None,
                        "provider": "gitnexus",
                        "reason": "gitnexus symbol keyword match",
                        "confidence": "high" if hits > 1 else "medium",
                    })
                related_nodes.append(node.get("id", ""))
                symbol_candidates.append(
                    ScopeCandidate(
                        symbol=sym,
                        path=_node_path(node) or None,
                        source="gitnexus",
                        confidence="high" if hits > 1 else "medium",
                        reason=f"GitNexus 符号命中关键字 ({hits} 次)",
                        role=_scope_role_for_path(_node_path(node) or "", role_hints) if path_filter else "related",
                    )
                )

        # Round 4 P1: when the analysis object is an exact code symbol, always
        # also pull local source hits — a wrong/incomplete GitNexus graph (e.g.
        # a same-named repo) must not be able to hide the real implementation
        # files (log_flags.c / log_deprecated.c were silently missed).
        if _looks_like_symbol(obj.text):
            exact_hits = await _await_with_agent_cleanup(
                _exact_symbol_repo_hits(
                    repo_path, obj.text.strip(), limits.max_files_per_object
                ),
                agent_task,
            )
            exact_candidates: list[ScopeCandidate] = []
            for hit in exact_hits:
                key = ("file", normalize_file_key(repo_path, hit))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                _record_agent_scope_file(
                    agent_session,
                    object_id=obj.id,
                    repo_path=repo_path,
                    path=hit,
                    provider="local",
                    reason="exact local symbol match",
                    confidence="high",
                    validated=True,
                )
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
            repo_hits = await _await_with_agent_cleanup(
                _bounded_repo_search(
                    repo_path, expanded_keywords, limits.max_files_per_object
                ),
                agent_task,
            )
            for hit in repo_hits:
                key = ("file", normalize_file_key(repo_path, hit))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                _record_agent_scope_file(
                    agent_session,
                    object_id=obj.id,
                    repo_path=repo_path,
                    path=hit,
                    provider="local",
                    reason="local content search matched keywords",
                    confidence="medium",
                )
                file_candidates.append(
                    ScopeCandidate(
                        path=hit,
                        source="repo_search",
                        confidence="medium",
                        reason="本地代码搜索命中关键字",
                        role="related",
                    )
                )

        try:
            agent_results = await agent_task if agent_task is not None else []
        except Exception as exc:
            agent_results = [
                _agent_scope_error_result(
                    agent_session,
                    goal="source_scope",
                    turn_id=f"{ws_id}:{obj.id}",
                    exc=exc,
                )
            ]
        _record_agent_scope_results(
            agent_session,
            object_id=obj.id,
            repo_path=repo_path,
            results=agent_results,
        )
        if (
            agent_session is not None
            and settings.agent_discovery_max_rounds > 1
            and (
                (not file_candidates and any(r.status == "ok" for r in agent_results))
                or _agent_requests_source_slices(agent_results)
                or (_agent_has_rejected_files(agent_session, obj.id) and bool(file_candidates))
            )
        ):
            for result in agent_results:
                if result.need_source_slices:
                    agent_session.add_source_slices_from_requests(
                        result.need_source_slices,
                        object_id=obj.id,
                    )
            context_packet = agent_session.build_context_packet(
                AgentContextPacketInput(
                    object_id=obj.id,
                    current_goal="source_scope",
                    analysis_object_text=obj.text,
                    expanded_terms=expanded_keywords,
                    path_hints=search_hints,
                    scope_hints=[
                        {"path": path, "role": role} for path, role in role_hints
                    ],
                    existing_tool_candidates=[
                        {
                            "path": cand.path,
                            "symbol": cand.symbol,
                            "source": cand.source,
                            "confidence": cand.confidence,
                            "reason": cand.reason,
                        }
                        for cand in [*file_candidates[:12], *symbol_candidates[:8]]
                    ],
                    round_index=2,
                )
            )
            try:
                round2_results = await run_external_agent_discovery(
                    AgentDiscoveryRequest(
                        request_id=f"{ws_id}:{obj.id}:round2",
                        repo_path=repo_path,
                        analysis_object_text=obj.text,
                        path_hints=search_hints,
                        scope_hints=[
                            {"path": path, "role": role} for path, role in role_hints
                        ],
                        context_packet=context_packet,
                        goal="source_scope",
                    ),
                    session=agent_session,
                )
            except Exception as exc:
                round2_results = [
                    _agent_scope_error_result(
                        agent_session,
                        goal="source_scope",
                        turn_id=f"{ws_id}:{obj.id}:round2",
                        exc=exc,
                    )
                ]
            _record_agent_scope_results(
                agent_session,
                object_id=obj.id,
                repo_path=repo_path,
                results=round2_results,
            )
            agent_results = [*agent_results, *round2_results]
        if agent_results:
            merged_files, agent_warnings = merge_source_candidates(
                repo_path, file_candidates, agent_results
            )
            file_candidates = merged_files[: limits.max_files_per_object]
            obj_warnings.extend(agent_warnings)
            for result in agent_results:
                for file in result.candidate_files:
                    if file.validated:
                        validation = validate_agent_candidate_file(repo_path, file.path)
                        if validation.validated and validation.resolved_path:
                            key = ("file", normalize_file_key(repo_path, validation.resolved_path))
                            seen_keys.add(key)

        # Materials are always consulted but kept low-priority.
        mat_candidates = await _candidate_materials(
            ws_id, expanded_keywords, max(2, limits.max_files_per_object // 4)
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
    ``max(2, max_files_per_object)``, symbols topped up to ``cap + 4``,
    plus one community card) so the preview's card count tracks reality instead
    of summing every raw candidate.
    """
    per_object_cap = max(2, limits.max_files_per_object)
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
