"""WorkspaceChat: context-enriched LLM chat for a workspace.

Provides build_chat_messages() to assemble the full message list and
persist_chat() to save the conversation turn to workspace_chats.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import httpx

from app.config import settings
from app.services.analysis_artifacts import (
    format_artifacts_for_report_qa,
    load_analysis_artifact_bundle,
)
from app.utils.repo_paths import to_tool_repo_path

logger = logging.getLogger(__name__)

_MAX_SNIPPET_CHARS = 400
_MAX_MATERIAL_CHARS = 2000
_MAX_REPORT_SUMMARY_CHARS = 500
_MAX_REPORT_QUERY_CHARS = 1400
_MAX_REPORTS_IN_CONTEXT = 8
_SEARCH_LIMIT = 5
_HISTORY_LIMIT = 50
_RECENT_HISTORY_LIMIT = 20
_MEMORY_SUMMARY_LIMIT = 40
_MEMORY_SNIPPET_CHARS = 160
_EVIDENCE_BEGIN = "<!-- CODETALK_EVIDENCE_STATUS_BEGIN -->"
_EVIDENCE_END = "<!-- CODETALK_EVIDENCE_STATUS_END -->"

_REPORT_LABELS: dict[str, str] = {
    "module_map": "项目与模块地图",
    "business_flow": "关键业务流程分析",
    "source_reading": "源码定向阅读记录",
    "test_design": "测试设计输入",
    "requirements": "需求与设计理解",
    "traceability": "需求-设计-代码追踪",
}

# T10: Mode-differentiated system prompts
_TARGETED_SYSTEM = """\
你是代码库结构化分析助手，专注于结合需求/设计文档、历史对话、报告摘要和可用代码证据进行深度分析。

## 代码仓库
{repo_path}
{module_context}
{memory_summary}
{evidence_status_block}
## 相关代码片段
{code_snippets}

## 项目材料摘要（需求/设计文档）
{materials_summary}

## 已生成分析报告摘要
{reports_summary}

## Mode Contract
MODE_TARGETED
- Scope: code snippets + active materials + completed reports + conversation memory.
- Required output sections: Evidence status, Conclusion, Evidence-backed analysis, Gaps / 待验证, Next actions.
- Every concrete claim should point to a visible source category when possible.
- Claims without direct evidence must be marked 待验证.

请使用 Markdown 格式输出详细、结构化的回答，语言：中文。\
"""

_REPORT_QA_SYSTEM = """\
你是 CodeTalk 报告追问助手，专注于基于已生成分析报告、可用代码片段和对话记忆回答后续问题。

## 代码仓库
{repo_path}
{module_context}
{memory_summary}
{evidence_status_block}
## 相关代码片段
{code_snippets}

## CodeTalk 可追溯分析资产
{analysis_artifacts}

## 已生成分析报告相关片段
{reports_summary}

## Mode Contract
MODE_REPORT_QA
- Scope: completed reports + code snippets + conversation memory.
- Treat completed reports as the primary context for follow-up questions.
- If the answer needs source details not present in report snippets or code snippets, say 待验证 and name the missing file/function.
- Do not answer as if materials were loaded unless they appear in the visible context.

请使用中文回答，先给结论，再列报告/代码证据和待验证缺口。\
"""

_FREEQA_SYSTEM = """\
你是代码库问答助手，用于快速回答关于代码仓库的轻量问题。自由问答只使用代码片段和对话记忆，不使用材料和报告摘要。

## 代码仓库
{repo_path}
{module_context}
{memory_summary}
{evidence_status_block}
## 相关代码片段
{code_snippets}

## Mode Contract
MODE_FREEQA
- Scope: code snippets + conversation memory only.
- Do not claim that active materials or reports were used in freeqa mode.
- If code_snippets is 0, do not infer an answer; ask the user to switch to 报告追问/结构化分析 or provide a file/function name.
- Prefer concise answers.
- Claims without direct evidence must be marked 待验证.

请用中文回答，语气自然、简洁，可以使用 Markdown 格式。\
"""


async def _search_gitnexus(repo_path: str, query: str, module: str | None = None) -> list[str]:
    """Return top-5 relevant code snippets from GitNexus. Empty list on any failure."""
    try:
        tool_path = to_tool_repo_path(
            repo_path,
            host_base_path=settings.repos_base_path,
            tool_base_path=settings.tool_repos_base_path,
        )
        repo_name = Path(tool_path).name
        effective_query = f"[{module}] {query}" if module else query

        async with httpx.AsyncClient(
            base_url=settings.gitnexus_base_url,
            timeout=httpx.Timeout(15, connect=5),
            trust_env=False,
        ) as client:
            resp = await client.post(
                "/api/search",
                params={"repo": repo_name},
                json={
                    "query": effective_query,
                    "mode": "hybrid",
                    "limit": _SEARCH_LIMIT,
                    "enrich": True,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        raw: list[dict] = data if isinstance(data, list) else data.get("results", [])
        snippets: list[str] = []
        for item in raw:
            file_ref = item.get("file") or item.get("path") or ""
            content = (item.get("content") or item.get("snippet") or "").strip()
            if content:
                excerpt = content[:_MAX_SNIPPET_CHARS]
                if len(content) > _MAX_SNIPPET_CHARS:
                    excerpt += "…"
                snippets.append(f"```\n// {file_ref}\n{excerpt}\n```")
        return snippets

    except Exception as exc:
        logger.warning("GitNexus search degraded (non-fatal): %s", exc)
        return []


async def _load_materials_text(ws_id: str) -> list[str]:
    """Load material file excerpts from disk (full-text fallback)."""
    rows: list[dict] = []
    async with aiosqlite.connect(settings.sqlite_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT filename, content_type, file_path FROM workspace_materials "
            "WHERE workspace_id = ? AND is_active = TRUE",
            (ws_id,),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    summaries: list[str] = []
    for row in rows:
        try:
            content = await asyncio.to_thread(
                Path(row["file_path"]).read_text, "utf-8", "ignore"
            )
            excerpt = content[:_MAX_MATERIAL_CHARS]
            if len(content) > _MAX_MATERIAL_CHARS:
                excerpt += "…"
            summaries.append(
                f"**[{row['filename']}]** ({row['content_type']})\n{excerpt}"
            )
        except Exception as exc:
            logger.warning("Failed to read material %s: %s", row["file_path"], exc)
    return summaries


async def _load_materials_context(ws_id: str, query: str) -> list[str]:
    """Load material context via RAG retrieval, supplementing unembedded materials with full-text."""
    rag_results: list[dict] = []
    try:
        from app.services.material_rag import retrieve_chunks
        rag_results = await retrieve_chunks(ws_id, query)
    except Exception as exc:
        logger.warning("RAG retrieval failed, falling back to full-text: %s", exc)
        return await _load_materials_text(ws_id)

    if not rag_results:
        return await _load_materials_text(ws_id)

    context = [
        f"**[{c['filename']}]** (相关度: {c['score']})\n{c['content']}"
        for c in rag_results
    ]

    covered_material_ids = {c["material_id"] for c in rag_results}

    from app.services.material_rag import _get_active_embedding_model_id
    active_model_id = await _get_active_embedding_model_id()

    async with aiosqlite.connect(settings.sqlite_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT m.id, m.filename, m.content_type, m.file_path "
            "FROM workspace_materials m "
            "LEFT JOIN material_chunks mc ON m.id = mc.material_id "
            "AND mc.embedding_model_id = ? "
            "WHERE m.workspace_id = ? AND m.is_active = TRUE AND mc.id IS NULL",
            (active_model_id, ws_id),
        ) as cur:
            unembedded = [dict(r) for r in await cur.fetchall()]

    for row in unembedded:
        if row["id"] in covered_material_ids:
            continue
        try:
            content = await asyncio.to_thread(
                Path(row["file_path"]).read_text, "utf-8", "ignore"
            )
            excerpt = content[:_MAX_MATERIAL_CHARS]
            if len(content) > _MAX_MATERIAL_CHARS:
                excerpt += "…"
            context.append(
                f"**[{row['filename']}]** ({row['content_type']})\n{excerpt}"
            )
        except Exception as exc:
            logger.warning("Failed to read unembedded material %s: %s", row["file_path"], exc)

    return context


def _query_terms(query: str | None) -> list[str]:
    if not query:
        return []
    terms: list[str] = []
    seen: set[str] = set()
    for raw in query.replace("_", " ").split():
        term = "".join(ch for ch in raw if ch.isalnum() or ch in {"_", "-"}).lower()
        if len(term) < 2 or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms[:8]


def _select_report_excerpt(content: str, query: str | None = None) -> str:
    content = (content or "").strip()
    if not content:
        return ""
    terms = _query_terms(query)
    if not terms:
        excerpt = content[:_MAX_REPORT_SUMMARY_CHARS]
        if len(content) > _MAX_REPORT_SUMMARY_CHARS:
            excerpt += "…"
        return excerpt

    lower = content.lower()
    hit_positions = [lower.find(term) for term in terms if lower.find(term) >= 0]
    if not hit_positions:
        excerpt = content[:_MAX_REPORT_SUMMARY_CHARS]
        if len(content) > _MAX_REPORT_SUMMARY_CHARS:
            excerpt += "…"
        return excerpt

    center = min(hit_positions)
    half = _MAX_REPORT_QUERY_CHARS // 2
    start = max(0, center - half)
    end = min(len(content), start + _MAX_REPORT_QUERY_CHARS)
    start = max(0, end - _MAX_REPORT_QUERY_CHARS)
    excerpt = content[start:end]
    if start > 0:
        excerpt = "…" + excerpt
    if end < len(content):
        excerpt += "…"
    return excerpt


async def _load_report_summaries(ws_id: str, query: str | None = None) -> list[str]:
    """Load bounded summaries of the most recent completed reports."""
    async with aiosqlite.connect(settings.sqlite_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT report_type, content FROM workspace_reports "
            "WHERE workspace_id = ? AND status = 'completed' "
            "AND content IS NOT NULL AND TRIM(content) != '' "
            "ORDER BY created_at DESC LIMIT ?",
            (ws_id, _MAX_REPORTS_IN_CONTEXT),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    summaries: list[str] = []
    for row in rows:
        content = (row["content"] or "").strip()
        if not content:
            continue
        label = _REPORT_LABELS.get(row["report_type"], row["report_type"])
        excerpt = _select_report_excerpt(content, query)
        summaries.append(f"**{label}**\n{excerpt}")
    return summaries


async def _load_report_analysis_artifacts(
    ws_id: str,
    query: str | None = None,
) -> tuple[list[str], int]:
    """Load compact summaries of artifact JSONs for the latest report tasks."""

    async with aiosqlite.connect(settings.sqlite_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT task_id FROM workspace_reports "
            "WHERE workspace_id = ? AND status = 'completed' "
            "AND task_id IS NOT NULL AND TRIM(task_id) != '' "
            "ORDER BY created_at DESC LIMIT ?",
            (ws_id, _MAX_REPORTS_IN_CONTEXT),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    contexts: list[str] = []
    artifact_count = 0
    seen: set[str] = set()
    for row in rows:
        task_id = str(row.get("task_id") or "").strip()
        if not task_id or task_id in seen:
            continue
        seen.add(task_id)
        bundle = await asyncio.to_thread(
            load_analysis_artifact_bundle,
            settings.outputs_path / task_id,
        )
        if not bundle:
            continue
        artifact_count += len(bundle)
        formatted = format_artifacts_for_report_qa(bundle, query)
        if formatted:
            contexts.append(f"### task_id={task_id}\n{formatted}")
    return contexts, artifact_count


async def _load_history(ws_id: str) -> list[dict]:
    """Load most-recent _HISTORY_LIMIT messages in chronological order for LLM context."""
    async with aiosqlite.connect(settings.sqlite_db) as db:
        db.row_factory = aiosqlite.Row
        # Fix 2: DESC LIMIT gets newest N rows; outer ASC re-sorts for chronological context
        async with db.execute(
            "SELECT role, content FROM ("
            "  SELECT role, content, created_at FROM workspace_chats"
            "  WHERE workspace_id = ? ORDER BY created_at DESC LIMIT ?"
            ") ORDER BY created_at ASC",
            (ws_id, _HISTORY_LIMIT),
        ) as cur:
            return [{"role": r["role"], "content": r["content"]} for r in await cur.fetchall()]


async def _load_all_history(ws_id: str) -> list[dict]:
    """Load full chat history in chronological order for memory compression."""
    async with aiosqlite.connect(settings.sqlite_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT role, content FROM workspace_chats"
            " WHERE workspace_id = ? ORDER BY created_at ASC",
            (ws_id,),
        ) as cur:
            return [{"role": r["role"], "content": r["content"]} for r in await cur.fetchall()]


def _truncate_memory_text(text: str, limit: int = _MEMORY_SNIPPET_CHARS) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _format_memory_summary(older: list[dict]) -> str:
    if not older:
        return ""

    if len(older) <= _MEMORY_SUMMARY_LIMIT:
        selected = older
    else:
        head_count = _MEMORY_SUMMARY_LIMIT // 2
        tail_count = _MEMORY_SUMMARY_LIMIT - head_count
        selected = older[:head_count] + older[-tail_count:]

    bullets = []
    for idx, msg in enumerate(selected, start=1):
        role = "用户" if msg.get("role") == "user" else "助手"
        bullets.append(f"- {idx}. {role}: {_truncate_memory_text(msg.get('content', ''))}")

    omitted = len(older) - len(selected)
    omitted_note = f"\n- omitted_older_messages: {omitted}" if omitted > 0 else ""
    return (
        "## CODETALK_MEMORY_SUMMARY\n"
        "The following compact memory preserves older turns that no longer fit "
        "as full chat messages. Use it as conversation context, but treat old "
        "facts as secondary to current evidence.\n"
        + "\n".join(bullets)
        + omitted_note
        + "\n"
    )


async def _load_history_for_prompt(ws_id: str) -> tuple[str, list[dict]]:
    """Return a compact older-memory block plus recent full messages."""
    history = await _load_all_history(ws_id)
    if len(history) <= _RECENT_HISTORY_LIMIT:
        return "", history

    older = history[:-_RECENT_HISTORY_LIMIT]
    recent = history[-_RECENT_HISTORY_LIMIT:]
    return _format_memory_summary(older), recent


def _build_evidence_notice(
    *,
    mode: str,
    code_snippet_count: int,
    material_count: int | None,
    report_count: int | None,
    memory_summary_present: bool,
    recent_history_count: int,
    analysis_artifact_count: int | None = None,
) -> str:
    mode_label = {
        "targeted": "结构化分析",
        "report_qa": "报告追问",
    }.get(mode, "自由问答")
    material_value = str(material_count) if material_count is not None else "not_used_in_freeqa"
    report_value = str(report_count) if report_count is not None else "not_used_in_freeqa"
    lines = [
        "> **证据状态**",
        f"> - mode: {mode_label} ({mode})",
        f"> - code_snippets: {code_snippet_count}",
        f"> - materials: {material_value}",
        f"> - reports: {report_value}",
        f"> - analysis_artifacts: {analysis_artifact_count if analysis_artifact_count is not None else 'not_used'}",
        f"> - memory_summary: {'yes' if memory_summary_present else 'no'}",
        f"> - recent_history_messages: {recent_history_count}",
    ]
    if code_snippet_count == 0:
        lines.append(
            "> - code_evidence: unavailable_or_no_hits; Claims without direct evidence must be marked 待验证."
        )
    else:
        lines.append("> - code_evidence: direct snippets available; cite file paths when using them.")
    if mode == "freeqa":
        lines.append("> - mode_scope: freeqa does not use active materials or completed reports.")
    elif mode == "report_qa":
        lines.append("> - mode_scope: report_qa uses completed reports and code snippets when available.")
    else:
        lines.append("> - mode_scope: targeted uses active materials and completed reports when available.")
    return "\n".join(lines)


def _evidence_status_block(notice: str) -> str:
    return f"{_EVIDENCE_BEGIN}\n{notice}\n{_EVIDENCE_END}\n"


def extract_evidence_notice(messages: list[dict]) -> str:
    """Extract the user-visible deterministic evidence notice from system prompt."""
    for msg in messages:
        if msg.get("role") != "system":
            continue
        content = msg.get("content") or ""
        start = content.find(_EVIDENCE_BEGIN)
        end = content.find(_EVIDENCE_END)
        if start == -1 or end == -1 or end <= start:
            continue
        return content[start + len(_EVIDENCE_BEGIN):end].strip()
    return ""


async def build_chat_messages(
    ws_id: str,
    repo_path: str,
    user_message: str,
    mode: str,
    module: str | None = None,
) -> list[dict]:
    """Gather workspace context and build the full LLM message list."""
    snippets, history_context = await asyncio.gather(
        _search_gitnexus(repo_path, user_message, module),
        _load_history_for_prompt(ws_id),
    )
    memory_summary, history = history_context

    code_text = "\n\n".join(snippets) if snippets else "（无直接代码片段；工具不可用或未命中，请将相关结论标记为待验证）"
    module_context = f"\n## 聚焦模块\n{module}\n" if module else ""

    if mode == "targeted":
        materials, reports = await asyncio.gather(
            _load_materials_context(ws_id, user_message),
            _load_report_summaries(ws_id, user_message),
        )
        evidence_notice = _build_evidence_notice(
            mode=mode,
            code_snippet_count=len(snippets),
            material_count=len(materials),
            report_count=len(reports),
            memory_summary_present=bool(memory_summary),
            recent_history_count=len(history),
        )
        system_prompt = _TARGETED_SYSTEM.format(
            repo_path=repo_path,
            module_context=module_context,
            memory_summary=memory_summary,
            evidence_status_block=_evidence_status_block(evidence_notice),
            code_snippets=code_text,
            materials_summary="\n\n".join(materials) if materials else "（无）",
            reports_summary="\n\n".join(reports) if reports else "（尚未生成报告）",
        )
    elif mode == "report_qa":
        reports, artifact_result = await asyncio.gather(
            _load_report_summaries(ws_id, user_message),
            _load_report_analysis_artifacts(ws_id, user_message),
        )
        artifact_summaries, artifact_count = artifact_result
        evidence_notice = _build_evidence_notice(
            mode=mode,
            code_snippet_count=len(snippets),
            material_count=0,
            report_count=len(reports),
            memory_summary_present=bool(memory_summary),
            recent_history_count=len(history),
            analysis_artifact_count=artifact_count,
        )
        system_prompt = _REPORT_QA_SYSTEM.format(
            repo_path=repo_path,
            module_context=module_context,
            memory_summary=memory_summary,
            evidence_status_block=_evidence_status_block(evidence_notice),
            code_snippets=code_text,
            analysis_artifacts=(
                "\n\n".join(artifact_summaries)
                if artifact_summaries
                else "(no CodeTalk analysis artifacts found for completed reports)"
            ),
            reports_summary="\n\n".join(reports) if reports else "（尚未生成报告）",
        )
    else:
        evidence_notice = _build_evidence_notice(
            mode=mode,
            code_snippet_count=len(snippets),
            material_count=None,
            report_count=None,
            memory_summary_present=bool(memory_summary),
            recent_history_count=len(history),
        )
        system_prompt = _FREEQA_SYSTEM.format(
            repo_path=repo_path,
            module_context=module_context,
            memory_summary=memory_summary,
            evidence_status_block=_evidence_status_block(evidence_notice),
            code_snippets=code_text,
        )

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})
    return messages


async def persist_user_message(ws_id: str, mode: str, user_message: str) -> None:
    """Persist the user message before streaming starts — never lost even if stream fails."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute(
            "INSERT INTO workspace_chats (id, workspace_id, mode, role, content, created_at) "
            "VALUES (?, ?, ?, 'user', ?, ?)",
            (str(uuid.uuid4()), ws_id, mode, user_message, now),
        )
        await db.commit()


async def persist_assistant_reply(ws_id: str, mode: str, reply: str) -> None:
    """Persist the assistant reply after streaming completes."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute(
            "INSERT INTO workspace_chats (id, workspace_id, mode, role, content, created_at) "
            "VALUES (?, ?, ?, 'assistant', ?, ?)",
            (str(uuid.uuid4()), ws_id, mode, reply, now),
        )
        await db.commit()
