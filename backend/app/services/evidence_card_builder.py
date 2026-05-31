"""Evidence card builder.

An *evidence card* is a small, structured snippet of repo evidence
attached to an :class:`AnalysisUnit`.  Cards are deliberately tiny
(300–800 Chinese characters of source plus a few metadata fields) so
that downstream LLM calls can stay under the 8K output budget.

Cards are produced from three sources:

* GitNexus graph hints (file/symbol nodes that the scope resolver already
  ranked).
* Source file snippets read directly from disk so the LLM gets real code
  rather than secondhand summaries.
* Workspace materials referenced by the scope candidates.

We never read entire repositories indiscriminately (§6.1 of the spec) —
each card includes only the leading bytes of the file plus the relevant
GitNexus-derived structural notes.  When evidence is uncertain we mark
it as ``待验证`` per §15.3.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import httpx

from app.config import settings
from app.schemas.workspace_analysis import (
    LLMLimits,
    ResolvedAnalysisObject,
    ScopeCandidate,
)

logger = logging.getLogger(__name__)

_MAX_SNIPPET_BYTES = 4_000  # ~ 600-800 Chinese chars worth of code per card
_SOURCE_EXTS = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
    ".c", ".cpp", ".h", ".hpp", ".cc", ".cxx", ".cs", ".rb", ".php",
    ".kt", ".swift", ".m", ".scala",
})


@dataclass
class EvidenceCard:
    """One evidence card associated with an analysis unit."""

    card_id: str
    object_id: str
    title: str
    source: str  # 'gitnexus' | 'repo_search' | 'material' | 'manual'
    confidence: str  # 'high' | 'medium' | 'low'
    file_path: str | None = None
    symbol: str | None = None
    snippet: str = ""
    notes: list[str] = field(default_factory=list)
    needs_verification: bool = False

    def to_dict(self) -> dict:
        return {
            "card_id": self.card_id,
            "object_id": self.object_id,
            "title": self.title,
            "source": self.source,
            "confidence": self.confidence,
            "file_path": self.file_path,
            "symbol": self.symbol,
            "snippet": self.snippet,
            "notes": self.notes,
            "needs_verification": self.needs_verification,
        }

    def to_markdown(self) -> str:
        verify = " [待验证]" if self.needs_verification else ""
        head = f"#### {self.title}{verify}"
        meta_parts = [f"来源: {self.source}", f"置信度: {self.confidence}"]
        if self.file_path:
            meta_parts.append(f"文件: `{self.file_path}`")
        if self.symbol:
            meta_parts.append(f"符号: `{self.symbol}`")
        meta = " · ".join(meta_parts)
        lines = [head, meta]
        if self.notes:
            lines.append("")
            lines.extend(f"- {n}" for n in self.notes)
        if self.snippet:
            lang = Path(self.file_path).suffix.lstrip(".") if self.file_path else ""
            lines.extend(["", f"```{lang}", self.snippet.rstrip(), "```"])
        return "\n".join(lines)


def _read_snippet_blocking(
    path: str,
    max_bytes: int = _MAX_SNIPPET_BYTES,
    symbol: str | None = None,
) -> str:
    p = Path(path)
    if not p.is_file():
        return ""
    try:
        if symbol:
            focused = _read_symbol_window(p, symbol, max_bytes)
            if focused:
                return focused
        raw = p.read_bytes()[:max_bytes]
        text = raw.decode("utf-8", errors="replace")
        if len(p.read_bytes()) > max_bytes:
            text = text + "\n…（已截断）"
        return text
    except OSError as exc:
        logger.warning("Failed to read snippet for %s: %s", path, exc)
        return ""


def _read_symbol_window(p: Path, symbol: str, max_bytes: int) -> str:
    """Read a compact window around the first exact symbol occurrence."""
    return _read_symbol_window_from_text(
        p.read_text(encoding="utf-8", errors="replace"), symbol, max_bytes
    )


def _read_symbol_window_from_text(text: str, symbol: str, max_bytes: int) -> str:
    pattern = re.compile(rf"\b{re.escape(symbol)}\b")
    lines = text.splitlines()
    hits = [idx for idx, line in enumerate(lines) if pattern.search(line)]
    first_hit = hits[0] if hits else None
    hit = next(
        (idx for idx in hits if _looks_like_function_definition(lines, idx, symbol)),
        first_hit,
    )
    if hit is None:
        return ""

    # If a nearby earlier occurrence led into the definition (for example a
    # wrapper call just before the function body), keep that context.  The end
    # budget is definition-anchored so tail cleanup such as free(ext_buf) is not
    # clipped off.
    start_anchor = first_hit if first_hit is not None and 0 <= hit - first_hit <= 80 else hit
    start = max(0, start_anchor - 20)
    end = min(len(lines), hit + 120)
    numbered = [
        f"{idx + 1}: {line}"
        for idx, line in enumerate(lines[start:end], start=start)
    ]
    text = "\n".join(numbered)
    raw = text.encode("utf-8")
    if len(raw) > max_bytes:
        text = raw[:max_bytes].decode("utf-8", errors="ignore") + "\n…（已截断）"
    return text


def _looks_like_function_definition(lines: list[str], idx: int, symbol: str) -> bool:
    """Prefer a C-style function definition over an earlier call site."""
    block = "\n".join(lines[idx:min(len(lines), idx + 8)])
    symbol_pos = block.find(symbol)
    if symbol_pos < 0:
        return False

    after_symbol = block[symbol_pos:]
    brace_pos = after_symbol.find("{")
    if brace_pos < 0:
        return False
    semicolon_pos = after_symbol.find(";")
    if semicolon_pos >= 0 and semicolon_pos < brace_pos:
        return False

    line = lines[idx].strip()
    if line.endswith(";"):
        return False

    starts_with_symbol = re.match(rf"^{re.escape(symbol)}\s*\(", line) is not None
    same_line_return_type = re.match(
        rf"^(?:static\s+)?[\w\s\*]+?\b{re.escape(symbol)}\s*\(",
        line,
    ) is not None
    return starts_with_symbol or same_line_return_type


async def _read_snippet(path: str, symbol: str | None = None) -> str:
    return await asyncio.to_thread(_read_snippet_blocking, path, symbol=symbol)


def _repo_relative_path(repo_path: str, full_path: Path) -> str:
    try:
        return str(full_path.resolve().relative_to(Path(repo_path).resolve())).replace("\\", "/")
    except Exception:
        return str(full_path).replace("\\", "/")


async def _read_snippet_from_gitnexus(
    *,
    repo_name: str,
    repo_path: str,
    full_path: Path,
    symbol: str | None,
    max_bytes: int = _MAX_SNIPPET_BYTES,
) -> tuple[str, str]:
    """Read source through GitNexus /api/file before local fallback."""
    if not repo_name:
        return "", ""
    rel_path = _repo_relative_path(repo_path, full_path)
    if settings.gitnexus_source_reader != "http_only":
        cli_text, cli_note = await _read_snippet_from_gitnexus_cli(
            repo_name=repo_name,
            repo_path=repo_path,
            rel_path=rel_path,
            symbol=symbol,
            max_bytes=max_bytes,
        )
        if cli_text:
            return cli_text, cli_note
    param_sets = [
        {"repo": repo_path, "path": rel_path},
        {"repo": repo_name, "path": rel_path},
    ]
    try:
        async with httpx.AsyncClient(
            base_url=settings.gitnexus_base_url,
            timeout=10,
            trust_env=False,
        ) as client:
            for params in param_sets:
                resp = await client.get("/api/file", params=params)
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                payload = resp.json()
                raw = payload.get("content")
                if isinstance(raw, list):
                    text = "\n".join(str(item) for item in raw)
                else:
                    text = str(raw or "")
                if not text:
                    continue
                if symbol:
                    focused = _read_symbol_window_from_text(text, symbol, max_bytes)
                    if focused:
                        return focused, f"源码由 GitNexus /api/file 读取：{rel_path}"
                encoded = text.encode("utf-8")
                if len(encoded) > max_bytes:
                    text = encoded[:max_bytes].decode("utf-8", errors="ignore") + "\n…（已截断）"
                return text, f"源码由 GitNexus /api/file 读取：{rel_path}"
    except Exception as exc:
        logger.debug("GitNexus /api/file read failed for %s: %s", rel_path, exc)
    return "", ""


async def _read_snippet_from_gitnexus_cli(
    *,
    repo_name: str,
    repo_path: str,
    rel_path: str,
    symbol: str | None,
    max_bytes: int = _MAX_SNIPPET_BYTES,
) -> tuple[str, str]:
    return await asyncio.to_thread(
        _read_snippet_from_gitnexus_cli_blocking,
        repo_name=repo_name,
        repo_path=repo_path,
        rel_path=rel_path,
        symbol=symbol,
        max_bytes=max_bytes,
    )


def _read_snippet_from_gitnexus_cli_blocking(
    *,
    repo_name: str,
    repo_path: str,
    rel_path: str,
    symbol: str | None,
    max_bytes: int = _MAX_SNIPPET_BYTES,
) -> tuple[str, str]:
    """Read indexed symbol content through the GitNexus CLI.

    The CLI has symbol-oriented source commands, so this is intentionally used
    before the HTTP file endpoint for exact symbol evidence.  If it cannot
    produce content for the requested file/symbol, callers fall back to HTTP.
    """
    if not symbol:
        return "", ""
    cli_bin = _resolve_gitnexus_cli_bin()
    if not cli_bin:
        logger.info("gitnexus: CLI source read skipped; binary not found")
        return "", ""

    file_commands = [
        [
            cli_bin,
            "file",
            "-r",
            repo_path,
            rel_path,
        ],
        [
            cli_bin,
            "file",
            "-r",
            repo_name,
            rel_path,
        ],
    ]
    for cmd in file_commands:
        text = _run_gitnexus_cli_file_command(
            cmd=cmd,
            repo_path=repo_path,
            rel_path=rel_path,
            symbol=symbol,
            max_bytes=max_bytes,
        )
        if text:
            return text, f"source read through GitNexus CLI file: {rel_path}"

    context_commands = [
        [
            cli_bin,
            "context",
            symbol,
            "-r",
            repo_path,
            "-f",
            rel_path,
            "--content",
        ],
        [
            cli_bin,
            "context",
            symbol,
            "-r",
            repo_name,
            "-f",
            rel_path,
            "--content",
        ],
    ]
    for cmd in context_commands:
        try:
            logger.info("gitnexus: reading source through CLI: %s", " ".join(cmd))
            proc = subprocess.run(
                cmd,
                cwd=repo_path if Path(repo_path).is_dir() else None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=settings.gitnexus_cli_timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.debug("gitnexus: CLI source read failed for %s: %s", rel_path, exc)
            continue
        if proc.returncode != 0:
            logger.debug(
                "gitnexus: CLI source read returned %s for %s: %s",
                proc.returncode,
                rel_path,
                (proc.stderr or proc.stdout)[-500:],
            )
            continue
        text = _extract_gitnexus_cli_content(proc.stdout, rel_path, symbol)
        if not text:
            continue
        if symbol:
            focused = _read_symbol_window_from_text(text, symbol, max_bytes)
            if focused:
                text = focused
        encoded = text.encode("utf-8")
        if len(encoded) > max_bytes:
            text = encoded[:max_bytes].decode("utf-8", errors="ignore") + "\n...(truncated)"
        return text, f"source read through GitNexus CLI: {rel_path}"
    return "", ""


def _run_gitnexus_cli_file_command(
    *,
    cmd: list[str],
    repo_path: str,
    rel_path: str,
    symbol: str | None,
    max_bytes: int,
) -> str:
    try:
        logger.info("gitnexus: reading file through CLI: %s", " ".join(cmd))
        proc = subprocess.run(
            cmd,
            cwd=repo_path if Path(repo_path).is_dir() else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=settings.gitnexus_cli_timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("gitnexus: CLI file read failed for %s: %s", rel_path, exc)
        return ""
    if proc.returncode != 0:
        logger.debug(
            "gitnexus: CLI file read returned %s for %s: %s",
            proc.returncode,
            rel_path,
            (proc.stderr or proc.stdout)[-500:],
        )
        return ""
    text = _extract_gitnexus_cli_file_content(proc.stdout)
    if not text:
        return ""
    if symbol:
        focused = _read_symbol_window_from_text(text, symbol, max_bytes)
        if focused:
            text = focused
    encoded = text.encode("utf-8")
    if len(encoded) > max_bytes:
        text = encoded[:max_bytes].decode("utf-8", errors="ignore") + "\n...(truncated)"
    return text


def _resolve_gitnexus_cli_bin() -> str:
    configured = settings.gitnexus_bin
    candidates = []
    if configured:
        candidates.append(configured)
        found = shutil.which(configured)
        if found:
            return found
    root = Path(__file__).resolve().parents[3]
    if configured and Path(configured).is_file():
        return str(Path(configured))
    candidates.extend([
        str(root / "workspace" / "gitnexus" / "node_modules" / ".bin" / "gitnexus.cmd"),
        str(root / "workspace" / "gitnexus" / "node_modules" / ".bin" / "gitnexus"),
    ])
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    return ""


def _extract_gitnexus_cli_content(stdout: str, rel_path: str, symbol: str) -> str:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout if symbol in stdout else ""

    candidates: list[tuple[str, str]] = []

    def visit(value):
        if isinstance(value, dict):
            content = value.get("content")
            path = str(value.get("filePath") or value.get("path") or "")
            name = str(value.get("name") or "")
            if isinstance(content, str) and content and (symbol in content or name == symbol):
                candidates.append((path, content))
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    markdown = payload.get("markdown") if isinstance(payload, dict) else None
    if isinstance(markdown, str):
        for match in re.finditer(r'"filePath":"(?P<path>(?:\\.|[^"\\])*)".*?"content":"(?P<content>(?:\\.|[^"\\])*)"', markdown):
            try:
                path = json.loads(f'"{match.group("path")}"')
                content = json.loads(f'"{match.group("content")}"')
            except json.JSONDecodeError:
                continue
            if symbol in content:
                candidates.append((path, content))

    normalized_rel = rel_path.replace("\\", "/").lower()
    for path, content in candidates:
        if path.replace("\\", "/").lower() == normalized_rel:
            return content
    return ""


def _extract_gitnexus_cli_file_content(stdout: str) -> str:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout
    raw = payload.get("content") if isinstance(payload, dict) else ""
    if isinstance(raw, list):
        return "\n".join(str(item) for item in raw)
    return str(raw or "")


class EvidenceCardBuilder:
    """Build a bounded list of evidence cards from a resolved scope."""

    def __init__(
        self,
        *,
        repo_path: str,
        limits: LLMLimits,
        gitnexus_repo: str | None = None,
    ) -> None:
        self._repo_path = repo_path
        self._limits = limits
        self._gitnexus_repo = gitnexus_repo or ""

    async def build_cards(
        self,
        resolved_objects: Iterable[ResolvedAnalysisObject],
    ) -> list[EvidenceCard]:
        cards: list[EvidenceCard] = []
        budget = self._limits.max_evidence_cards

        for resolved in resolved_objects:
            if len(cards) >= budget:
                break
            object_cards = await self._build_for_object(resolved)
            for card in object_cards:
                if len(cards) >= budget:
                    break
                cards.append(card)

        return cards

    async def _build_for_object(
        self,
        resolved: ResolvedAnalysisObject,
    ) -> list[EvidenceCard]:
        seen: set[tuple[str, str]] = set()
        per_object_cap = max(2, self._limits.max_files_per_object // 2)
        cards: list[EvidenceCard] = []

        # Files first — they ground the LLM in real source.
        for cand in resolved.candidate_files:
            if len(cards) >= per_object_cap:
                break
            card = await self._card_for_file(resolved, cand)
            if card is None:
                continue
            key = ("file", card.file_path or "")
            if key in seen:
                continue
            seen.add(key)
            cards.append(card)

        # Symbols add structural pointers even without snippets.
        for cand in resolved.candidate_symbols[: self._limits.max_functions_per_object]:
            if len(cards) >= per_object_cap + 4:
                break
            sym = cand.symbol or "(unknown symbol)"
            key = ("symbol", sym)
            if key in seen:
                continue
            seen.add(key)
            cards.append(
                EvidenceCard(
                    card_id=f"sym_{resolved.object_id}_{sym}",
                    object_id=resolved.object_id,
                    title=f"符号候选：{sym}",
                    source=cand.source,
                    confidence=cand.confidence,
                    file_path=cand.path,
                    symbol=sym,
                    snippet="",
                    notes=[cand.reason],
                    needs_verification=cand.source != "gitnexus",
                )
            )

        if resolved.related_communities:
            cards.append(
                EvidenceCard(
                    card_id=f"comm_{resolved.object_id}",
                    object_id=resolved.object_id,
                    title=f"相关 GitNexus 社区（仅供导航）",
                    source="gitnexus",
                    confidence="low",
                    notes=[
                        "GitNexus 社区命名仅作导航参考，最终结论须以源码为准。",
                        "命中社区：" + ", ".join(resolved.related_communities),
                    ],
                    needs_verification=True,
                )
            )

        if not cards:
            cards.append(
                EvidenceCard(
                    card_id=f"warn_{resolved.object_id}",
                    object_id=resolved.object_id,
                    title=f"未能解析的分析对象：{resolved.text}",
                    source="manual",
                    confidence="low",
                    notes=[
                        "未在 GitNexus、源码或材料中找到证据；"
                        "建议在描述中加入具体函数名或文件名。",
                    ],
                    needs_verification=True,
                )
            )

        return cards

    async def _card_for_file(
        self,
        resolved: ResolvedAnalysisObject,
        cand: ScopeCandidate,
    ) -> EvidenceCard | None:
        path_str = cand.path
        if not path_str:
            return None

        snippet = ""
        full = Path(path_str)
        if not full.is_absolute():
            try:
                full = (Path(self._repo_path) / path_str).resolve()
            except Exception:
                full = Path(path_str)
        suffix = full.suffix
        notes = [cand.reason]
        if suffix in _SOURCE_EXTS or cand.source == "material":
            source_note = ""
            if cand.source != "material" and self._gitnexus_repo:
                snippet, source_note = await _read_snippet_from_gitnexus(
                    repo_name=self._gitnexus_repo,
                    repo_path=self._repo_path,
                    full_path=full,
                    symbol=cand.symbol,
                )
            if source_note:
                notes.append(source_note)
            else:
                snippet = await _read_snippet(str(full), cand.symbol)
                if cand.source != "material":
                    notes.append("GitNexus /api/file 未返回源码，已降级读取本地源码")

        return EvidenceCard(
            card_id=f"file_{resolved.object_id}_{full.name}",
            object_id=resolved.object_id,
            title=f"代码证据：{full.name}",
            source=cand.source,
            confidence=cand.confidence,
            file_path=str(full),
            symbol=cand.symbol,
            snippet=snippet,
            notes=notes,
            needs_verification=cand.source != "gitnexus" and not snippet,
        )
