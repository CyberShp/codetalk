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
from app.utils.repo_paths import to_tool_repo_path

logger = logging.getLogger(__name__)

_MAX_SNIPPET_CHARS = 400
_MAX_MATERIAL_CHARS = 2000
_MAX_REPORT_SUMMARY_CHARS = 500
_SEARCH_LIMIT = 5
_HISTORY_LIMIT = 50

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
你是代码库结构化分析助手，专注于结合需求/设计文档对代码进行深度分析。

## 代码仓库
{repo_path}
{module_context}
## 相关代码片段
{code_snippets}

## 项目材料摘要（需求/设计文档）
{materials_summary}

## 已生成分析报告摘要
{reports_summary}

请使用 Markdown 格式输出详细、结构化的回答，语言：中文。\
"""

_FREEQA_SYSTEM = """\
你是代码库问答助手，可以轻松回答关于代码仓库的各类问题。

## 代码仓库
{repo_path}
{module_context}
## 相关代码片段
{code_snippets}

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


async def _load_report_summaries(ws_id: str) -> list[str]:
    """Load first _MAX_REPORT_SUMMARY_CHARS of each completed report."""
    async with aiosqlite.connect(settings.sqlite_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT report_type, content FROM workspace_reports "
            "WHERE workspace_id = ? AND status = 'completed'",
            (ws_id,),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    summaries: list[str] = []
    for row in rows:
        content = (row["content"] or "").strip()
        if not content:
            continue
        label = _REPORT_LABELS.get(row["report_type"], row["report_type"])
        excerpt = content[:_MAX_REPORT_SUMMARY_CHARS]
        if len(content) > _MAX_REPORT_SUMMARY_CHARS:
            excerpt += "…"
        summaries.append(f"**{label}**\n{excerpt}")
    return summaries


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


async def build_chat_messages(
    ws_id: str,
    repo_path: str,
    user_message: str,
    mode: str,
    module: str | None = None,
) -> list[dict]:
    """Gather workspace context and build the full LLM message list."""
    snippets, history = await asyncio.gather(
        _search_gitnexus(repo_path, user_message, module),
        _load_history(ws_id),
    )

    code_text = "\n\n".join(snippets) if snippets else "（无相关代码片段）"
    module_context = f"\n## 聚焦模块\n{module}\n" if module else ""

    if mode == "targeted":
        materials, reports = await asyncio.gather(
            _load_materials_context(ws_id, user_message),
            _load_report_summaries(ws_id),
        )
        system_prompt = _TARGETED_SYSTEM.format(
            repo_path=repo_path,
            module_context=module_context,
            code_snippets=code_text,
            materials_summary="\n\n".join(materials) if materials else "（无）",
            reports_summary="\n\n".join(reports) if reports else "（尚未生成报告）",
        )
    else:
        system_prompt = _FREEQA_SYSTEM.format(
            repo_path=repo_path,
            module_context=module_context,
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
