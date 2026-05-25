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
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

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


def _read_snippet_blocking(path: str, max_bytes: int = _MAX_SNIPPET_BYTES) -> str:
    p = Path(path)
    if not p.is_file():
        return ""
    try:
        raw = p.read_bytes()[:max_bytes]
        text = raw.decode("utf-8", errors="replace")
        if len(p.read_bytes()) > max_bytes:
            text = text + "\n…（已截断）"
        return text
    except OSError as exc:
        logger.warning("Failed to read snippet for %s: %s", path, exc)
        return ""


async def _read_snippet(path: str) -> str:
    return await asyncio.to_thread(_read_snippet_blocking, path)


class EvidenceCardBuilder:
    """Build a bounded list of evidence cards from a resolved scope."""

    def __init__(self, *, repo_path: str, limits: LLMLimits) -> None:
        self._repo_path = repo_path
        self._limits = limits

    async def build_cards(
        self,
        resolved_objects: Iterable[ResolvedAnalysisObject],
    ) -> list[EvidenceCard]:
        cards: list[EvidenceCard] = []
        seen: set[tuple[str, str]] = set()
        budget = self._limits.max_evidence_cards

        for resolved in resolved_objects:
            if len(cards) >= budget:
                break
            object_cards = await self._build_for_object(resolved, seen)
            for card in object_cards:
                if len(cards) >= budget:
                    break
                cards.append(card)

        return cards

    async def _build_for_object(
        self,
        resolved: ResolvedAnalysisObject,
        seen: set[tuple[str, str]],
    ) -> list[EvidenceCard]:
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
        if suffix in _SOURCE_EXTS or cand.source == "material":
            snippet = await _read_snippet(str(full))

        return EvidenceCard(
            card_id=f"file_{resolved.object_id}_{full.name}",
            object_id=resolved.object_id,
            title=f"代码证据：{full.name}",
            source=cand.source,
            confidence=cand.confidence,
            file_path=str(full),
            snippet=snippet,
            notes=[cand.reason],
            needs_verification=cand.source != "gitnexus" and not snippet,
        )
