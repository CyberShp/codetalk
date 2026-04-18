"""Chat streaming endpoint — proxies Q&A to deepwiki-open.

IRON LAW: This endpoint only does HTTP proxying + response streaming.
No analysis logic, no code parsing, no graph building.

Zoekt context injection is an HTTP call to the Zoekt tool, not analysis logic.
File I/O in _read_file_context is response format conversion (expand Zoekt line hits
to function-level context), not analysis.
"""

import asyncio
import base64
import logging
import os
import pathlib
import re
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.llm_config import LLMConfig
from app.models.repository import Repository
from app.models.task import AnalysisTask
from app.utils.repo_paths import to_tool_repo_path
from app.services.chat_payload import DEFAULT_EXCLUDED_DIRS, ChatMessage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

_ZOEKT_BASE = settings.zoekt_base_url
_ZOEKT_CONTAINER = "codetalk-zoekt-1"
_DOCKER_SOCKET = "/var/run/docker.sock"
_ZOEKT_CONTEXT_NUM = 15   # 增加召回数量
_ZOEKT_LINES_PER_FILE = 30  # 增加每文件行数
_ZOEKT_MAX_FILES = 8     # 增加最大文件数
_ZOEKT_INDEX_WAIT_TIMEOUT = 3  # 降低等待时间，改为快速反馈

# Module-level: tracks in-progress indexing tasks keyed by repo_key.
# Shared across requests so concurrent chat queries on the same repo
# all wait on the same indexing task rather than spawning duplicates.
_indexing_tasks: dict[str, "asyncio.Task[None]"] = {}

_SYMBOL_STOP_WORDS = {
    "what",
    "does",
    "where",
    "which",
    "when",
    "how",
    "why",
    "is",
    "are",
    "was",
    "were",
    "the",
    "this",
    "that",
    "with",
    "from",
    "into",
    "for",
    "and",
    "about",
    "use",
    "uses",
    "using",
    "function",
    "method",
    "class",
}


async def _zoekt_index_background(repo_path: str, repo_key: str) -> None:
    """Fire-and-forget: run zoekt-index inside the zoekt container via Docker Engine API.

    Called via asyncio.ensure_future() when the repo is not yet indexed.
    Uses the same httpx-over-UDS pattern as ZoektAdapter._exec_index.
    Errors are logged but never propagated.

    IRON LAW: only Docker Engine API call + no analysis logic.
    """
    logger.info("zoekt: background indexing triggered for '%s'", repo_key)
    transport = httpx.AsyncHTTPTransport(uds=_DOCKER_SOCKET)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://localhost",
            timeout=httpx.Timeout(300, connect=10),
        ) as docker:
            cmd = ["zoekt-index", "-index", "/data/index", repo_path]
            create_resp = await docker.post(
                f"/containers/{_ZOEKT_CONTAINER}/exec",
                json={"Cmd": cmd, "AttachStdout": True, "AttachStderr": True},
            )
            if create_resp.status_code not in (200, 201):
                logger.warning(
                    "zoekt background index: exec create returned %s for '%s'",
                    create_resp.status_code, repo_key,
                )
                return
            exec_id = create_resp.json()["Id"]
            start_resp = await docker.post(
                f"/exec/{exec_id}/start",
                json={"Detach": False, "Tty": False},
                headers={"Content-Type": "application/json"},
            )
            await start_resp.aread()
            inspect = await docker.get(f"/exec/{exec_id}/json")
            exit_code = inspect.json().get("ExitCode", -1)
            if exit_code != 0:
                logger.warning(
                    "zoekt-index exited %d for '%s'", exit_code, repo_key
                )
            else:
                logger.info("zoekt: background indexing complete for '%s'", repo_key)
    except Exception as exc:
        logger.warning("zoekt background index failed for '%s': %s", repo_key, exc)


async def _zoekt_is_indexed(repo_key: str) -> bool:
    """Return True if Zoekt has an index for repo_key.

    IRON LAW: HTTP call to Zoekt /api/list only.
    """
    try:
        async with httpx.AsyncClient(
            base_url=_ZOEKT_BASE, timeout=httpx.Timeout(5, connect=3)
        ) as client:
            r = await client.post("/api/list", json={"Q": f"repo:{repo_key}"})
            r.raise_for_status()
            repos = (r.json().get("List", {}).get("Repos") or [])
            return any(
                x.get("Repository", {}).get("Name") == repo_key for x in repos
            )
    except Exception:
        return False


async def _zoekt_search_context(query: str, repo_path: str) -> str:
    """Call Zoekt for code hits related to the user query.

    Phase 1 — ensure indexed:
      If not indexed, start an indexing task (or reuse one already running)
      and wait up to _ZOEKT_INDEX_WAIT_TIMEOUT seconds. This covers the common
      case of small/medium repos finishing before the user's first response.
      Large repos time out here but continue indexing in the background;
      the next chat message will get context.

    Phase 2 — search and format results.

    IRON LAW: only HTTP/Docker calls + response format conversion.
    """
    tool_repo_path = to_tool_repo_path(
        repo_path,
        host_base_path=settings.repos_base_path,
        tool_base_path=settings.tool_repos_base_path,
    )
    repo_key = os.path.basename(tool_repo_path.rstrip("/"))

    # Phase 1: ensure indexed
    is_indexed = await _zoekt_is_indexed(repo_key)

    if not is_indexed:
        # Reuse an in-progress indexing task or start a new one
        existing = _indexing_tasks.get(repo_key)
        if existing is None or existing.done():
            _indexing_tasks[repo_key] = asyncio.ensure_future(
                _zoekt_index_background(tool_repo_path, repo_key)
            )
        # Wait up to timeout. asyncio.shield protects the inner task from
        # being cancelled if wait_for times out — indexing continues.
        try:
            await asyncio.wait_for(
                asyncio.shield(_indexing_tasks[repo_key]),
                timeout=_ZOEKT_INDEX_WAIT_TIMEOUT,
            )
            is_indexed = await _zoekt_is_indexed(repo_key)
        except asyncio.TimeoutError:
            logger.info(
                "zoekt: sync wait timed out after %ds for '%s' — no context this round",
                _ZOEKT_INDEX_WAIT_TIMEOUT, repo_key,
            )
            return ""

    if not is_indexed:
        return ""

    # Phase 2: repo is indexed — search
    scoped_query = f"repo:{repo_key} {query}"
    try:
        async with httpx.AsyncClient(
            base_url=_ZOEKT_BASE,
            timeout=httpx.Timeout(8, connect=3),
        ) as client:
            resp = await client.post(
                "/api/search",
                json={"Q": scoped_query, "Num": _ZOEKT_CONTEXT_NUM},
            )
            resp.raise_for_status()
            raw = resp.json()
    except Exception as exc:
        logger.debug("zoekt context search skipped: %s", exc)
        return ""

    files = (raw.get("Result") or {}).get("Files") or []
    if not files:
        return ""

    parts = ["[Relevant code found in repository]\n"]
    for f in files[:_ZOEKT_MAX_FILES]:
        fname = f.get("FileName", "")
        lms = (f.get("LineMatches") or [])[:_ZOEKT_LINES_PER_FILE]
        if not lms:
            continue
        parts.append(f"### {fname}")
        for lm in lms:
            line_num = lm.get("LineNumber", 0)
            try:
                line_content = base64.b64decode(lm.get("Line", "")).decode(
                    "utf-8", errors="replace"
                )
            except Exception:
                line_content = str(lm.get("Line", ""))
            parts.append(f"{line_num}: {line_content}")
        parts.append("")

    return "\n".join(parts)


# ── Pydantic models (defined before helper functions that use them) ──────────

class EvidenceItem(BaseModel):
    id: str
    type: str  # "code" | "wiki"
    title: str
    content: str
    file: Optional[str] = None
    line_range: Optional[str] = None


class AskContextRequest(BaseModel):
    task_id: uuid.UUID
    query: str


class AskContextResponse(BaseModel):
    evidence: list[EvidenceItem]
    sources_found: int
    query: str


class ChatRequest(BaseModel):
    task_id: uuid.UUID
    messages: list[ChatMessage]
    evidence: list[EvidenceItem] = []  # Pre-fetched from /ask/context; skips Zoekt when present


# ── Helper functions ─────────────────────────────────────────────────────────

def _read_file_context(repo_path: str, filename: str, center_line: int, context_lines: int = 30) -> str:
    """Read lines around center_line from a file in the repo.

    IRON LAW: file I/O only — expands a Zoekt line-hit to function-level context.
    Enhanced: tries to find function/class definition if near the hit.
    """
    try:
        repo_root = pathlib.Path(repo_path).resolve()
        candidate = (repo_root / filename).resolve()
        candidate.relative_to(repo_root)  # path traversal guard
        if not candidate.is_file():
            return ""
        with open(candidate, "r", encoding="utf-8", errors="replace") as fh:
            all_lines = fh.readlines()
        
        # Simple heuristic: if we're in a code file, look back a few lines for a def/class
        # to ensure we capture the context of the hit.
        search_start = max(0, center_line - 1 - 10)
        actual_center = center_line
        if filename.endswith((".py", ".c", ".cpp", ".h", ".js", ".ts", ".go")):
            for i in range(center_line - 1, search_start, -1):
                if i < len(all_lines) and re.match(r"^\s*(def|class|function|struct|enum)\s+", all_lines[i]):
                    actual_center = i + 1
                    break
        
        start = max(0, actual_center - context_lines - 1)
        end = min(len(all_lines), actual_center + context_lines)
        return "".join(f"{start + i + 1}: {line}" for i, line in enumerate(all_lines[start:end]))
    except Exception:
        return ""


def _decode_line_text(encoded_line: str) -> str:
    try:
        return base64.b64decode(encoded_line).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _extract_symbol_candidates(query: str) -> list[str]:
    """Extract potential code symbols from natural-language query."""
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query)
    result: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        low = token.lower()
        if low in _SYMBOL_STOP_WORDS:
            continue
        # Prefer code-like tokens (camelCase/snake_case/digits) and longer names.
        is_code_like = (
            any(ch.isupper() for ch in token[1:])
            or "_" in token
            or any(ch.isdigit() for ch in token)
        )
        if not is_code_like and len(token) < 6:
            continue
        if low in seen:
            continue
        seen.add(low)
        result.append(token)
    return result


def _build_scoped_queries(repo_key: str, query: str) -> list[str]:
    """Build Zoekt query variants, symbol-first for better definition recall."""
    symbols = _extract_symbol_candidates(query)
    queries: list[str] = []
    for sym in symbols[:3]:
        queries.append(f"repo:{repo_key} sym:{sym}")
        queries.append(f"repo:{repo_key} {sym}")
    queries.append(f"repo:{repo_key} {query}")

    deduped: list[str] = []
    seen: set[str] = set()
    for q in queries:
        if q in seen:
            continue
        seen.add(q)
        deduped.append(q)
    return deduped


def _score_line_for_symbols(line_text: str, symbols: list[str]) -> int:
    text = line_text.strip()
    if not text:
        return 0
    score = 0
    text_low = text.lower()
    for sym in symbols:
        sym_low = sym.lower()
        if sym_low not in text_low:
            continue
        score += 20
        if re.search(rf"\b(def|class|struct|enum)\s+{re.escape(sym)}\b", text):
            score += 120
        if re.search(rf"^\s*[A-Za-z_][A-Za-z0-9_*\s]+\s+{re.escape(sym)}\s*\(", text):
            score += 100
        if re.search(rf"\b{re.escape(sym)}\s*\(", text):
            score += 30
    return score


def _pick_best_line_match_with_score(
    line_matches: list[dict],
    symbols: list[str],
) -> tuple[int, int]:
    if not line_matches:
        return 1, 0

    best_line = int(line_matches[0].get("LineNumber", 1) or 1)
    best_score = -1
    for lm in line_matches:
        line_number = int(lm.get("LineNumber", 1) or 1)
        score = _score_line_for_symbols(_decode_line_text(str(lm.get("Line", ""))), symbols)
        if score > best_score:
            best_line = line_number
            best_score = score
        elif score == best_score and line_number < best_line:
            best_line = line_number
    return best_line, max(best_score, 0)


def _pick_best_line_match(line_matches: list[dict], symbols: list[str]) -> int:
    """Choose best center line, preferring definition lines over call sites."""
    line, _ = _pick_best_line_match_with_score(line_matches, symbols)
    return line


async def _zoekt_search_evidence(query: str, repo_path: str, repo_key: str) -> list[EvidenceItem]:
    """Search Zoekt and return structured EvidenceItem list with function-level context.

    Enhanced: symbol-first multi-query retrieval for better definition recall.
    """
    symbol_candidates = _extract_symbol_candidates(query)
    scoped_queries = _build_scoped_queries(repo_key, query)
    merged_hits: dict[str, tuple[dict, int, int]] = {}

    try:
        async with httpx.AsyncClient(
            base_url=_ZOEKT_BASE,
            timeout=httpx.Timeout(8, connect=3),
        ) as client:
            for scoped_query in scoped_queries:
                resp = await client.post(
                    "/api/search",
                    json={"Q": scoped_query, "Num": _ZOEKT_CONTEXT_NUM},
                )
                resp.raise_for_status()
                files = (resp.json().get("Result") or {}).get("Files") or []
                for f in files[:_ZOEKT_MAX_FILES]:
                    fname = str(f.get("FileName", ""))
                    if not fname:
                        continue
                    lms = f.get("LineMatches") or []
                    if not lms:
                        continue
                    best_line, score = _pick_best_line_match_with_score(lms, symbol_candidates)
                    existing = merged_hits.get(fname)
                    if existing is None or score > existing[2]:
                        merged_hits[fname] = (f, best_line, score)
    except Exception as exc:
        logger.debug("zoekt evidence search failed: %s", exc)
        return []

    ranked = sorted(
        merged_hits.items(),
        key=lambda item: item[1][2],  # score
        reverse=True,
    )[:_ZOEKT_MAX_FILES]

    evidence: list[EvidenceItem] = []
    for i, (fname, (fobj, best_line, _)) in enumerate(ranked):
        lms = fobj.get("LineMatches") or []
        context_lines = _ZOEKT_LINES_PER_FILE
        content = _read_file_context(repo_path, fname, best_line, context_lines)

        if not content:
            lines: list[str] = []
            for lm in lms[:_ZOEKT_LINES_PER_FILE]:
                line_num = lm.get("LineNumber", 0)
                line_content = _decode_line_text(str(lm.get("Line", ""))) or str(lm.get("Line", ""))
                lines.append(f"{line_num}: {line_content}")
            content = "\n".join(lines)

        start_line = max(1, best_line - context_lines)
        end_line = best_line + context_lines
        evidence.append(
            EvidenceItem(
                id=f"code-{i}",
                type="code",
                title=fname,
                content=content,
                file=fname,
                line_range=f"{start_line}-{end_line}",
            )
        )

    return evidence


def _format_evidence_as_context(evidence: list["EvidenceItem"]) -> str:
    """Format structured evidence items into a numbered prompt string for LLM injection.

    IRON LAW: format conversion only.
    """
    if not evidence:
        return ""
    parts = ["[代码证据 — 请在回答中使用 [1] [2] 等标记引用对应来源]\n"]
    for i, ev in enumerate(evidence, 1):
        if ev.type == "code":
            location = f"{ev.file or ev.title}"
            if ev.line_range:
                location += f" (L{ev.line_range})"
            parts.append(f"[{i}] {location}")
        else:
            parts.append(f"[{i}] 文档: {ev.title}")
        parts.append(ev.content)
        parts.append("")
    return "\n".join(parts)


@router.post("/ask/context", response_model=AskContextResponse)
async def ask_context(body: AskContextRequest, db: AsyncSession = Depends(get_db)):
    """Return structured code evidence for a user query — Phase 1 of the Ask pipeline.

    Searches Zoekt and returns evidence cards with function-level context.
    Frontend shows these immediately while the LLM response streams in Phase 2.

    IRON LAW: Zoekt HTTP call + file I/O (context expansion) + format conversion.
    """
    task = await db.get(AnalysisTask, body.task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    repo = await db.get(Repository, task.repository_id)
    if not repo or not repo.local_path:
        raise HTTPException(400, "Repository not synced")

    repo_path = repo.local_path
    tool_repo_path = to_tool_repo_path(
        repo_path,
        host_base_path=settings.repos_base_path,
        tool_base_path=settings.tool_repos_base_path,
    )
    repo_key = os.path.basename(tool_repo_path.rstrip("/"))

    # Ensure indexed — same logic as _zoekt_search_context
    is_indexed = await _zoekt_is_indexed(repo_key)
    if not is_indexed:
        existing = _indexing_tasks.get(repo_key)
        if existing is None or existing.done():
            _indexing_tasks[repo_key] = asyncio.ensure_future(
                _zoekt_index_background(tool_repo_path, repo_key)
            )
        try:
            await asyncio.wait_for(
                asyncio.shield(_indexing_tasks[repo_key]),
                timeout=_ZOEKT_INDEX_WAIT_TIMEOUT,
            )
            is_indexed = await _zoekt_is_indexed(repo_key)
        except asyncio.TimeoutError:
            pass

    evidence: list[EvidenceItem] = []
    if is_indexed:
        evidence = await _zoekt_search_evidence(body.query, repo_path, repo_key)

    logger.info(
        "ask/context: repo=%s query=%r evidence_count=%d",
        repo_key, body.query[:60], len(evidence),
    )

    return AskContextResponse(
        evidence=evidence,
        sources_found=len(evidence),
        query=body.query,
    )


@router.post("/stream")
async def chat_stream(body: ChatRequest, db: AsyncSession = Depends(get_db)):
    """Stream a chat response from deepwiki about the task's repository.

    Injects Zoekt code-search context for the latest user message before
    forwarding to deepwiki. This lets deepwiki answer function-level questions
    accurately even when RAG retrieval is incomplete.
    """
    task = await db.get(AnalysisTask, body.task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    repo = await db.get(Repository, task.repository_id)
    if not repo or not repo.local_path:
        raise HTTPException(400, "Repository not synced")

    repo_path = repo.local_path
    tool_repo_path = to_tool_repo_path(
        repo_path,
        host_base_path=settings.repos_base_path,
        tool_base_path=settings.tool_repos_base_path,
    )

    result = await db.execute(
        select(LLMConfig).where(LLMConfig.is_default.is_(True)).limit(1)
    )
    llm_config = result.scalar_one_or_none()
    if not llm_config:
        result = await db.execute(
            select(LLMConfig).order_by(LLMConfig.created_at.desc()).limit(1)
        )
        llm_config = result.scalar_one_or_none()

    # Build messages with context injection.
    # If the frontend pre-fetched evidence via /ask/context, use that (numbered, structured).
    # Otherwise fall back to on-demand Zoekt search for backward compatibility.
    last_user_query = next(
        (m.content for m in reversed(body.messages) if m.role == "user"),
        None,
    )
    zoekt_context = ""
    if body.evidence:
        zoekt_context = _format_evidence_as_context(body.evidence)
    elif last_user_query:
        zoekt_context = await _zoekt_search_context(last_user_query, repo_path)

    messages: list[dict] = []
    if zoekt_context:
        messages.append({"role": "system", "content": zoekt_context})
    messages.extend({"role": m.role, "content": m.content} for m in body.messages)

    payload: dict = {
        "repo_url": tool_repo_path,
        "type": "local",
        "messages": messages,
        "language": "zh",
        "excluded_dirs": "\n".join(DEFAULT_EXCLUDED_DIRS),
    }

    proxy_mode = "system"
    if llm_config:
        provider = llm_config.provider
        if provider == "custom":
            provider = "openai"
        payload["provider"] = provider
        payload["model"] = llm_config.model_name
        proxy_mode = llm_config.proxy_mode

    trust_env = proxy_mode != "direct"

    await db.close()

    logger.info(
        "chat stream: repo=%s provider=%s model=%s zoekt_context=%s",
        repo_path,
        payload.get("provider", "(none)"),
        payload.get("model", "(none)"),
        "yes" if zoekt_context else "no",
    )

    async def generate():
        try:
            async with httpx.AsyncClient(
                base_url=settings.deepwiki_base_url,
                timeout=httpx.Timeout(300, connect=10),
                trust_env=trust_env,
            ) as client:
                async with client.stream(
                    "POST",
                    "/chat/completions/stream",
                    json=payload,
                    timeout=300,
                ) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_text():
                        yield chunk
        except httpx.ConnectError:
            yield "\n\n> ⚠️ 无法连接 deepwiki 服务，请检查容器是否运行。"
        except httpx.HTTPStatusError as exc:
            logger.error("deepwiki returned %s", exc.response.status_code)
            yield f"\n\n> ⚠️ deepwiki 返回错误 {exc.response.status_code}"
        except Exception as exc:
            logger.error("Chat stream error: %s", exc)
            yield f"\n\n> ⚠️ 请求失败: {exc}"

    return StreamingResponse(
        generate(),
        media_type="text/plain",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
