"""Persistent AI investigation threads for CodeTalk."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import re
import shutil
import subprocess
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import aiosqlite

from app.config import settings
from app.llm.base import current_finish_reason
from app.services.agent_cli_bridge import (
    AGENT_ANSWER_DELTA_PREFIX,
    AGENT_FINAL_ANSWER_PREFIX,
    AgentRuntimeError,
    clean_agent_output_text,
    resolve_agent_cwd,
    stream_agent_runtime,
)
from app.services.agent_invocation_contract import (
    agent_invocation_artifact_event_payload,
    agent_invocation_capability_event_payload,
    agent_invocation_capability_manifest,
    build_agent_invocation_execution_contract,
)
from app.services.external_agent_discovery import redact_agent_diagnostic_text
from app.services.test_activity_contract import (
    audit_test_activity_response,
    build_test_activity_contract,
)

logger = logging.getLogger(__name__)

AI_SCOPE_TYPES = {
    "workspace",
    "workbench_task_run",
    "workflow",
    "report",
    "module",
    "requirement_doc",
    "test_case_set",
    "freeform",
}

_MAX_REFERENCE_CHARS = 1200
_MAX_CONTEXT_REFERENCES = 14
_MAX_HISTORY_MESSAGES = 24
_MAX_AGENT_HISTORY_ARTIFACT_CHARS = 12000
_THREAD_INLINE_OUTPUT_LIMIT = 3600
_THREAD_ARTIFACT_KEYWORDS = (
    "sfmea",
    "failure mode",
    "黑盒",
    "测试用例",
    "测试设计",
    "前置条件",
    "预期结果",
    "rpn",
)
_THREAD_ARTIFACT_STREAM_NOTICE = "正在生成结构化产物，完成后会提供下载文件。"
_TEST_ACTIVITY_OUTPUT_TOKEN_BUDGET = 8192
_STALE_INTERNAL_RECORD_SQL = (
    "COALESCE(julianday(updated_at), julianday(created_at), 0) "
    "< julianday('now') - (1.0 / 1440.0)"
)
_SOURCE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hh",
    ".hpp",
    ".py",
    ".rs",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".sh",
    ".md",
    ".rst",
    ".txt",
}
_SOURCE_CITATION_RE = re.compile(
    r"(?P<path>(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:c|cc|cpp|cxx|h|hh|hpp|py|rs|go|java|js|jsx|ts|tsx|sh|md|rst|txt))"
    r":(?P<line>\d{1,7})(?:-(?P<end>\d{1,7}))?"
)
_QUERY_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "workspace",
    "source",
    "code",
    "file",
    "files",
    "read",
    "analyze",
}
_STORAGE_DOMAIN_PATH_HINTS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("nvme-of", "nvmeof", "nvmf", "nvmf target", "target connect"), ("lib/nvmf", "test/nvmf")),
    (("iscsi", "chap", "login digest"), ("lib/iscsi", "test/iscsi_tgt")),
    (("bdev", "block device"), ("lib/bdev", "test/bdev")),
    (("blobstore", "blob store"), ("lib/blob", "test/blobstore")),
    (("ftl",), ("lib/ftl", "test/ftl")),
    (("vhost",), ("lib/vhost", "test/vhost")),
    (("vfio-user", "vfiouser"), ("lib/vfio-user", "lib/vfu_tgt", "test/vfio_user")),
    (("reactor",), ("lib/event", "test/event")),
    (("poller", "thread"), ("lib/thread", "test/thread")),
    (("jsonrpc", "rpc config", "rpc"), ("lib/rpc", "lib/jsonrpc", "test/rpc")),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_workbench_initial_context(
    *,
    scope_type: str,
    scope_id: str,
    initial_context: dict[str, Any],
) -> dict[str, Any]:
    if scope_type != "workbench_task_run":
        return dict(initial_context)
    context = dict(initial_context)
    if "artifact_dir" in context:
        context["artifact_dir"] = "."
    agent_runs = context.get("agent_runs")
    if isinstance(agent_runs, list):
        public_runs: list[Any] = []
        for item in agent_runs:
            if not isinstance(item, dict):
                public_runs.append(item)
                continue
            public_item = dict(item)
            artifact_dir = str(public_item.get("artifact_dir") or "").replace("\\", "/")
            marker = f"/{scope_id}/agent_runs/"
            if marker in artifact_dir:
                public_item["artifact_dir"] = f"agent_runs/{artifact_dir.split(marker, 1)[1].strip('/')}"
            elif artifact_dir.startswith("/") or re.match(r"^[A-Za-z]:/", artifact_dir):
                public_item["artifact_dir"] = ""
            public_runs.append(public_item)
        context["agent_runs"] = public_runs
    return context


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _clip(text: str, limit: int = _MAX_REFERENCE_CHARS) -> str:
    compact = str(text or "").strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def ai_thread_artifact_path(conversation_id: str, run_id: str) -> Path:
    safe_conversation = re.sub(r"[^A-Za-z0-9_.-]+", "-", conversation_id).strip("-") or "conversation"
    safe_run = re.sub(r"[^A-Za-z0-9_.-]+", "-", run_id).strip("-") or "run"
    return settings.outputs_path / "ai_conversations" / safe_conversation / safe_run / "assistant-output.md"


def ai_thread_agent_artifact_dir(conversation_id: str, run_id: str) -> Path:
    return ai_thread_artifact_path(conversation_id, run_id).parent / "agent-artifacts"


async def _write_json_file(path: Path, payload: Any) -> None:
    await _to_thread(
        path.write_text,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        "utf-8",
    )


def _remove_tree_quietly(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


_SOURCE_DUMP_MIN_LINES = 30
_SOURCE_DUMP_MIN_CODE_LINES = 18
_SOURCE_DUMP_MIN_CHARS = 1800
_SOURCE_CODE_LINE_RE = re.compile(
    r"^\s*(?:"
    r"#\s*(?:include|define|ifdef|ifndef|endif|pragma)\b"
    r"|(?://|/\*|\*|#|--)"
    r"|(?:static\s+)?(?:inline\s+)?(?:const\s+)?(?:int|void|bool|char|size_t|uint\d+_t|"
    r"spdk_\w+|struct|enum|typedef|class|def|func|function|package|import|from)\b"
    r"|(?:if|for|while|switch|case|return|goto|else|try|catch)\b"
    r"|[{};]"
    r")",
    re.IGNORECASE,
)


def _govern_visible_assistant_content(
    content: str,
    references: list[dict[str, Any]],
) -> str:
    """Prevent raw source dumps from becoming the visible AI-thread answer."""
    raw_text = clean_agent_output_text(str(content or "")).strip()
    if not raw_text:
        return ""
    paths = _source_reference_paths(references)
    evidence_line = (
        "证据文件：" + "、".join(f"`{path}`" for path in paths[:5])
        if paths
        else "证据文件：工作区源码引用"
    )
    if _looks_like_source_dump(raw_text):
        report = _extract_user_facing_report_after_source_dump(raw_text)
        if report:
            return (
                "CodeTalk 已折叠一段疑似源码全文输出，避免外部 agent 把大文件直接刷进 AI 线程。\n\n"
                f"{evidence_line}\n\n"
                f"{report}"
            )
    text = _legacy_clean_agent_answer_content(raw_text)
    if not _looks_like_source_dump(text):
        return text
    report = _extract_user_facing_report_after_source_dump(text)
    if report:
        return (
            "CodeTalk 已折叠一段疑似源码全文输出，避免外部 agent 把大文件直接刷进 AI 线程。\n\n"
            f"{evidence_line}\n\n"
            f"{report}"
        )
    return (
        "CodeTalk 已折叠一段疑似源码全文输出，避免外部 agent 把大文件直接刷进 AI 线程。\n\n"
        "可见状态：执行器读取了工作区源码，但返回内容主要是源码原文，不是面向用户的分析结论。"
        "请基于证据文件继续追问“流程、风险、SFMEA、黑盒用例”，或重新要求只输出结论与证据摘要。\n\n"
        f"{evidence_line}"
    )


def _extract_user_facing_report_after_source_dump(text: str) -> str:
    matches = list(_LEGACY_AGENT_REPORT_HEADING_RE.finditer(str(text or "")))
    for match in matches:
        candidate = text[match.start() :].strip()
        if _legacy_cleaned_candidate_is_user_facing(candidate):
            return candidate
    return ""


_LEGACY_AGENT_DIAGNOSTIC_MARKERS = (
    "THINKING:",
    "TOOL:",
    "TOOL_USE:",
    "TOOL_RESULT:",
    "REASONING:",
    "TRACE:",
    "DIAGNOSTIC:",
    "STATUS:",
)
_LEGACY_AGENT_REPORT_INTRO_RE = re.compile(
    r"(?m)^(?:我已掌握|下面基于|基于\s*`)",
)
_LEGACY_AGENT_REPORT_HEADING_RE = re.compile(
    r"(?m)^#{1,3}\s+(?:结论|摘要|代码证据|流程|流程梳理|SFMEA|黑盒测试用例|测试用例|风险|用例设计依据)",
)


def _legacy_clean_agent_answer_content(content: str) -> str:
    """Hide legacy agent process leakage that was persisted before diagnostics were split."""
    text = clean_agent_output_text(str(content or "")).strip()
    if not text:
        return ""
    has_diagnostic_marker = any(marker in text for marker in _LEGACY_AGENT_DIAGNOSTIC_MARKERS)
    if not has_diagnostic_marker and not _looks_like_source_dump(text):
        return text
    intro_matches = list(_LEGACY_AGENT_REPORT_INTRO_RE.finditer(text))
    for match in reversed(intro_matches):
        candidate = text[match.start() :].strip()
        if _legacy_cleaned_candidate_is_user_facing(candidate):
            return candidate
    heading_matches = list(_LEGACY_AGENT_REPORT_HEADING_RE.finditer(text))
    for match in reversed(heading_matches):
        candidate = text[match.start() :].strip()
        if _legacy_cleaned_candidate_is_user_facing(candidate):
            return candidate
    if _looks_like_legacy_agent_process_leak(text):
        return (
            "CodeTalk 已折叠旧版 Agent 过程输出，避免把工具调用、源码搜索结果或中间思考直接显示在回答区。\n\n"
            "这条历史消息生成于过程/答案分离修复之前；请展开“Agent 过程”查看执行轨迹，"
            "或使用“下载完整产物”获取已清理的 Markdown 结果。"
        )
    return text


def _looks_like_legacy_agent_process_leak(text: str) -> bool:
    if not any(marker in text for marker in _LEGACY_AGENT_DIAGNOSTIC_MARKERS):
        return False
    lines = [line for line in str(text or "").splitlines() if line.strip()]
    if len(lines) >= 8:
        code_like = sum(
            1
            for line in lines[:80]
            if not line.lstrip().startswith("#") and _SOURCE_CODE_LINE_RE.search(line)
        )
        if code_like >= 4:
            return True
    sourceish_markers = (
        "grep -n",
        "rg ",
        "Bash {",
        "lib/",
        "struct ",
        "rsph",
        "reqh",
        "AuthMethod",
        "content_block",
        "tool_use",
        "tool_result",
    )
    return sum(1 for marker in sourceish_markers if marker in text) >= 2


def _legacy_cleaned_candidate_is_user_facing(candidate: str) -> bool:
    if not candidate:
        return False
    if any(candidate.startswith(marker) for marker in _LEGACY_AGENT_DIAGNOSTIC_MARKERS):
        return False
    lowered = candidate.lower()
    useful_markers = (
        "## 结论",
        "## 摘要",
        "## 代码证据",
        "## 流程",
        "## sfmea",
        "## 黑盒测试用例",
        "## 测试用例",
        "### tc-",
        "tc-01",
    )
    if not any(marker in lowered for marker in useful_markers):
        return False
    lines = [line for line in candidate.splitlines() if line.strip()]
    if not lines:
        return False
    scored_lines = [line for line in lines[:80] if not line.lstrip().startswith("#")]
    code_like = sum(1 for line in scored_lines if _SOURCE_CODE_LINE_RE.search(line))
    return code_like / max(1, len(scored_lines)) < 0.5


def _looks_like_source_dump(text: str) -> bool:
    if len(text) < _SOURCE_DUMP_MIN_CHARS:
        return False
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < _SOURCE_DUMP_MIN_LINES:
        return False
    code_like = sum(1 for line in lines if _SOURCE_CODE_LINE_RE.search(line))
    if code_like < _SOURCE_DUMP_MIN_CODE_LINES:
        return False
    ratio = code_like / max(1, len(lines))
    source_markers = (
        "#include",
        "SPDX-License-Identifier",
        "static ",
        "typedef ",
        "struct ",
        "return ",
        "package ",
        "import ",
        "def ",
        "class ",
    )
    marker_hits = sum(1 for marker in source_markers if marker in text)
    return ratio >= 0.45 and marker_hits >= 2


def _agent_answer_chunk_safe_for_live_stream(content: str) -> bool:
    text = str(content or "")
    if not text.strip():
        return False
    if _looks_like_agent_thin_help_answer(text):
        return False
    if len(text) > 1200:
        return False
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > 24:
        return False
    if _looks_like_source_dump(text):
        return False
    code_like = sum(1 for line in lines if _SOURCE_CODE_LINE_RE.search(line))
    if code_like >= 4:
        return False
    source_markers = (
        "#include",
        "SPDX-License-Identifier",
        "typedef ",
        "struct ",
        "static ",
        "return ",
        "package ",
        "import ",
        "def ",
        "class ",
    )
    marker_hits = sum(1 for marker in source_markers if marker in text)
    if code_like >= 1 and marker_hits >= 1:
        return False
    return marker_hits < 2


def _source_reference_paths(references: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for ref in references:
        if not isinstance(ref, dict):
            continue
        source_type = str(ref.get("source_type") or "")
        metadata = ref.get("metadata") if isinstance(ref.get("metadata"), dict) else {}
        candidate = str(metadata.get("path") or ref.get("title") or ref.get("source_id") or "").strip()
        if not candidate:
            continue
        if source_type and source_type != "workspace_source" and "/" not in candidate:
            continue
        if candidate not in paths:
            paths.append(candidate)
    return paths


@dataclass(frozen=True)
class ContextReference:
    source_type: str
    source_id: str
    title: str
    excerpt: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "title": self.title,
            "excerpt": self.excerpt,
            "metadata": self.metadata,
        }


class AIConversationStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = str(db_path or settings.sqlite_db)

    async def create_conversation(
        self,
        *,
        scope_type: str,
        scope_id: str,
        title: str,
        workspace_id: str | None = None,
        memory_namespace: str | None = None,
        runtime_type: str = "builtin_llm",
        agent_runtime_id: str | None = None,
        initial_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if scope_type not in AI_SCOPE_TYPES:
            raise ValueError(f"Unsupported AI conversation scope_type: {scope_type}")
        if runtime_type not in {"builtin_llm", "agent_runtime"}:
            raise ValueError(f"Unsupported AI conversation runtime_type: {runtime_type}")
        if runtime_type == "agent_runtime" and not (agent_runtime_id or "").strip():
            raise ValueError("agent_runtime_id is required when runtime_type is agent_runtime")
        cid = _new_id("conv")
        now = _now()
        initial = _public_workbench_initial_context(
            scope_type=scope_type,
            scope_id=scope_id,
            initial_context=initial_context or {},
        )
        async with self._connect() as db:
            resolved_workspace_id = await _resolve_workspace_id(
                db,
                scope_type=scope_type,
                scope_id=scope_id,
                initial_context=initial,
                explicit_workspace_id=workspace_id,
            )
            resolved_namespace = _resolve_memory_namespace(
                workspace_id=resolved_workspace_id,
                explicit_memory_namespace=memory_namespace,
                initial_context=initial,
            )
            await db.execute(
                """
                INSERT INTO ai_conversations
                    (id, scope_type, scope_id, workspace_id, memory_namespace, runtime_type, agent_runtime_id,
                     title, status, initial_context_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'idle', ?, ?, ?)
                """,
                (
                    cid,
                    scope_type,
                    scope_id,
                    resolved_workspace_id,
                    resolved_namespace,
                    runtime_type,
                    agent_runtime_id.strip() if agent_runtime_id else None,
                    title.strip() or "AI 调查线程",
                    _json_dumps(initial),
                    now,
                    now,
                ),
            )
            await db.commit()
        return await self.get_conversation(cid)

    async def list_conversations(
        self,
        *,
        scope_type: str | None = None,
        scope_id: str | None = None,
        workspace_id: str | None = None,
        memory_namespace: str | None = None,
        status: str | None = None,
        include_internal: bool = False,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if scope_type:
            clauses.append("scope_type = ?")
            params.append(scope_type)
        if scope_id:
            clauses.append("scope_id = ?")
            params.append(scope_id)
        if workspace_id:
            clauses.append("workspace_id = ?")
            params.append(workspace_id)
        if memory_namespace:
            clauses.append("memory_namespace = ?")
            params.append(memory_namespace)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if not include_internal:
            missing_workspace_internal_filter = (
                ""
                if workspace_id
                else f"""
                    OR (
                        workspace_id IS NOT NULL
                        AND workspace_id != ''
                        AND workspace_id != 'global'
                        AND NOT EXISTS (
                            SELECT 1
                            FROM workspaces visible_ws
                            WHERE visible_ws.id = ai_conversations.workspace_id
                        )
                    )
                """
            )
            clauses.append(
                f"""
                NOT (
                    initial_context_json LIKE '%"codetalk_internal": true%'
                    OR initial_context_json LIKE '%"codetalk_internal":true%'
                    OR initial_context_json LIKE '%"internal_test": true%'
                    OR initial_context_json LIKE '%"internal_test":true%'
                    OR (
                        ({_STALE_INTERNAL_RECORD_SQL})
                        AND (
                            title LIKE '%E2E 裸工具输出验证%'
                            OR title LIKE 'E2E %'
                            OR lower(title) LIKE '%-e2e-%'
                        )
                    )
                    OR (
                        ({_STALE_INTERNAL_RECORD_SQL})
                        AND EXISTS (
                            SELECT 1
                            FROM agent_runtimes internal_runtime
                            WHERE internal_runtime.id = ai_conversations.agent_runtime_id
                              AND (
                                  lower(internal_runtime.name) LIKE '%e2e%'
                                  OR lower(internal_runtime.command) LIKE '%/tmp/codetalk%'
                                  OR lower(internal_runtime.args_json) LIKE '%/tmp/codetalk%'
                                  OR lower(internal_runtime.command) LIKE '%codetalk-agent-%'
                                  OR lower(internal_runtime.args_json) LIKE '%codetalk-agent-%'
                                  OR lower(internal_runtime.fixed_working_dir) LIKE '%codetalk-agent-%'
                                  OR lower(internal_runtime.health_command) LIKE '%codetalk-agent-%'
                                  OR lower(internal_runtime.command) LIKE '%codetalk-claude-%'
                                  OR lower(internal_runtime.args_json) LIKE '%codetalk-claude-%'
                                  OR lower(internal_runtime.fixed_working_dir) LIKE '%codetalk-claude-%'
                                  OR lower(internal_runtime.health_command) LIKE '%codetalk-claude-%'
                                  OR lower(internal_runtime.command) LIKE '%codetalk-ai-%'
                                  OR lower(internal_runtime.args_json) LIKE '%codetalk-ai-%'
                                  OR lower(internal_runtime.fixed_working_dir) LIKE '%codetalk-ai-%'
                                  OR lower(internal_runtime.health_command) LIKE '%codetalk-ai-%'
                              )
                        )
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM workspaces hidden_ws
                        WHERE hidden_ws.id = ai_conversations.workspace_id
                          AND (
                              COALESCE(julianday(hidden_ws.updated_at), julianday(hidden_ws.created_at), 0)
                              < julianday('now') - (1.0 / 1440.0)
                          )
                          AND (
                              lower(hidden_ws.repo_path) LIKE '%/codetalk-ai-%'
                              OR lower(hidden_ws.repo_path) LIKE '%/codetalk_ai_context_panel_%'
                              OR lower(hidden_ws.repo_path) LIKE '%/codetalk-entry-ui-%'
                              OR lower(hidden_ws.name) LIKE '%-e2e-%'
                              OR lower(hidden_ws.name) LIKE 'ai_context_panel_%'
                              OR lower(hidden_ws.name) LIKE 'entry-discovery-ws-%'
                              OR lower(hidden_ws.name) LIKE 'release-click-%'
                          )
                    )
                    {missing_workspace_internal_filter}
                )
                """
            )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 100)))
        async with self._connect() as db:
            async with db.execute(
                f"""
                SELECT *
                FROM ai_conversations
                {where}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                params,
            ) as cur:
                return [_conversation_from_row(row) for row in await cur.fetchall()]

    async def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        async with self._connect() as db:
            async with db.execute(
                "SELECT * FROM ai_conversations WHERE id = ?",
                (conversation_id,),
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            raise KeyError(conversation_id)
        return _conversation_from_row(row)

    async def update_conversation_runtime(
        self,
        conversation_id: str,
        *,
        runtime_type: str,
        agent_runtime_id: str | None,
    ) -> dict[str, Any]:
        if runtime_type not in {"builtin_llm", "agent_runtime"}:
            raise ValueError(f"Unsupported AI conversation runtime_type: {runtime_type}")
        if runtime_type == "agent_runtime" and not (agent_runtime_id or "").strip():
            raise ValueError("agent_runtime_id is required when runtime_type is agent_runtime")
        await self.get_conversation(conversation_id)
        now = _now()
        async with self._connect() as db:
            await db.execute(
                """
                UPDATE ai_conversations
                SET runtime_type = ?, agent_runtime_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    runtime_type,
                    agent_runtime_id.strip() if runtime_type == "agent_runtime" and agent_runtime_id else None,
                    now,
                    conversation_id,
                ),
            )
            await db.commit()
        return await self.get_conversation(conversation_id)

    async def delete_conversation(self, conversation_id: str) -> None:
        conversation = await self.get_conversation(conversation_id)
        latest = await self.latest_run(conversation_id)
        if latest and latest["status"] in {"queued", "running"}:
            raise ValueError("当前线程仍在生成中，请先停止后再删除")
        async with self._connect() as db:
            await db.execute("BEGIN")
            await db.execute("DELETE FROM ai_run_events WHERE conversation_id = ?", (conversation_id,))
            await db.execute("DELETE FROM ai_agent_runtime_sessions WHERE conversation_id = ?", (conversation_id,))
            await db.execute("DELETE FROM ai_conversation_runs WHERE conversation_id = ?", (conversation_id,))
            await db.execute("DELETE FROM ai_messages WHERE conversation_id = ?", (conversation_id,))
            await db.execute("DELETE FROM ai_conversations WHERE id = ?", (conversation_id,))
            await db.commit()
        artifact_root = settings.outputs_path / "ai_conversations" / conversation["id"]
        await _to_thread(_remove_tree_quietly, artifact_root)

    async def list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        async with self._connect() as db:
            async with db.execute(
                "SELECT * FROM ai_conversations WHERE id = ?",
                (conversation_id,),
            ) as cur:
                conversation_row = await cur.fetchone()
            async with db.execute(
                """
                SELECT *
                FROM ai_messages
                WHERE conversation_id = ?
                ORDER BY created_at ASC
                """,
                (conversation_id,),
            ) as cur:
                rows = await cur.fetchall()
            async with db.execute(
                """
                SELECT id, status
                FROM ai_conversation_runs
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ) as cur:
                completed_run_ids = {
                    str(row["id"])
                    for row in await cur.fetchall()
                    if str(row["status"] or "") == "completed"
                }
        messages = [_public_message_from_row(row) for row in rows]
        if conversation_row is not None:
            await self._backfill_legacy_download_artifacts(
                conversation=_conversation_from_row(conversation_row),
                messages=messages,
                completed_run_ids=completed_run_ids,
            )
        return messages

    async def _backfill_legacy_download_artifacts(
        self,
        *,
        conversation: dict[str, Any],
        messages: list[dict[str, Any]],
        completed_run_ids: set[str],
    ) -> None:
        user_message_by_run: dict[str, str] = {}
        updates: list[tuple[str, str, str]] = []
        for message in messages:
            run_id = str(message.get("run_id") or "").strip()
            if message.get("role") == "user" and run_id:
                user_message_by_run[run_id] = str(message.get("content") or "")
                continue
            if message.get("role") != "assistant" or not run_id or run_id not in completed_run_ids:
                continue
            actions = message.get("actions") if isinstance(message.get("actions"), list) else []
            if any(isinstance(action, dict) and action.get("id") == "download_run_artifact" for action in actions):
                continue
            user_content = user_message_by_run.get(run_id, "")
            content = str(message.get("content") or "").strip()
            if not content or not _agent_task_requests_downloadable_artifact(user_content, content):
                continue
            final_content, final_actions = await _prepare_assistant_delivery(
                run_id=run_id,
                conversation=conversation,
                content=content,
                force_artifact=True,
            )
            message["content"] = final_content
            message["actions"] = final_actions
            updates.append((final_content, _json_dumps(final_actions), str(message["id"])))
        if not updates:
            return
        async with self._connect() as db:
            await db.executemany(
                """
                UPDATE ai_messages
                SET content = ?, actions_json = ?
                WHERE id = ?
                """,
                updates,
            )
            await db.commit()

    async def create_user_message_and_run(
        self,
        *,
        conversation_id: str,
        content: str,
        references: list[ContextReference],
    ) -> dict[str, Any]:
        now = _now()
        message_id = _new_id("msg")
        run_id = _new_id("run")
        refs = [item.to_dict() for item in references]
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
                FROM ai_conversation_runs
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ) as cur:
                sequence_row = await cur.fetchone()
            sequence = int(sequence_row["next_sequence"] or 1) if sequence_row else 1
            await db.execute(
                """
                INSERT INTO ai_messages
                    (id, conversation_id, run_id, role, content, references_json, actions_json, created_at)
                VALUES (?, ?, ?, 'user', ?, ?, '[]', ?)
                """,
                (message_id, conversation_id, run_id, content, _json_dumps(refs), now),
            )
            await db.execute(
                """
                INSERT INTO ai_conversation_runs
                    (id, conversation_id, status, sequence, cursor, created_at)
                VALUES (?, ?, 'queued', ?, 0, ?)
                """,
                (run_id, conversation_id, sequence, now),
            )
            await db.execute(
                "UPDATE ai_conversations SET status = 'running', updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            await db.commit()
        await self.append_event(
            run_id=run_id,
            conversation_id=conversation_id,
            event_type="status",
            payload={"status": "queued", "message": "已进入生成队列，正在准备上下文。"},
        )
        return {
            "message": await self.get_message(message_id),
            "run": await self.get_run(run_id),
            "references": refs,
        }

    async def next_queued_run(self, conversation_id: str) -> dict[str, Any] | None:
        async with self._connect() as db:
            async with db.execute(
                """
                SELECT id
                FROM ai_conversation_runs
                WHERE conversation_id = ? AND status = 'running'
                LIMIT 1
                """,
                (conversation_id,),
            ) as cur:
                running = await cur.fetchone()
            if running is not None:
                return None
            async with db.execute(
                """
                SELECT *
                FROM ai_conversation_runs
                WHERE conversation_id = ? AND status = 'queued'
                ORDER BY sequence ASC, created_at ASC, id ASC
                LIMIT 1
                """,
                (conversation_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is not None:
                run = _run_from_row(row)
                run["queue_position"] = await self._queue_position_for_run_row(db, row)
                return run
        return None

    async def get_message(self, message_id: str) -> dict[str, Any]:
        async with self._connect() as db:
            async with db.execute("SELECT * FROM ai_messages WHERE id = ?", (message_id,)) as cur:
                row = await cur.fetchone()
        if row is None:
            raise KeyError(message_id)
        return _public_message_from_row(row)

    async def get_run(self, run_id: str) -> dict[str, Any]:
        async with self._connect() as db:
            async with db.execute("SELECT * FROM ai_conversation_runs WHERE id = ?", (run_id,)) as cur:
                row = await cur.fetchone()
            if row is None:
                raise KeyError(run_id)
            run = _run_from_row(row)
            run["queue_position"] = await self._queue_position_for_run_row(db, row)
        return run

    async def latest_run(self, conversation_id: str) -> dict[str, Any] | None:
        async with self._connect() as db:
            async with db.execute(
                """
                SELECT *
                FROM ai_conversation_runs
                WHERE conversation_id = ?
                ORDER BY sequence DESC, created_at DESC
                LIMIT 1
                """,
                (conversation_id,),
            ) as cur:
                row = await cur.fetchone()
            if row:
                run = _run_from_row(row)
                run["queue_position"] = await self._queue_position_for_run_row(db, row)
                return run
        return None

    async def _queue_position_for_run_row(
        self,
        db: aiosqlite.Connection,
        row: aiosqlite.Row,
    ) -> int:
        if str(row["status"] or "") != "queued":
            return 0
        conversation_id = str(row["conversation_id"] or "")
        sequence = int(row["sequence"] or 0)
        async with db.execute(
            """
            SELECT 1
            FROM ai_conversation_runs
            WHERE conversation_id = ? AND status = 'running'
            LIMIT 1
            """,
            (conversation_id,),
        ) as cur:
            has_running = await cur.fetchone() is not None
        if sequence > 0:
            async with db.execute(
                """
                SELECT COUNT(*) AS count
                FROM ai_conversation_runs
                WHERE conversation_id = ?
                  AND status = 'queued'
                  AND sequence > 0
                  AND sequence <= ?
                """,
                (conversation_id, sequence),
            ) as cur:
                count_row = await cur.fetchone()
        else:
            async with db.execute(
                """
                SELECT COUNT(*) AS count
                FROM ai_conversation_runs
                WHERE conversation_id = ?
                  AND status = 'queued'
                  AND (created_at < ? OR (created_at = ? AND id <= ?))
                """,
                (conversation_id, row["created_at"], row["created_at"], row["id"]),
            ) as cur:
                count_row = await cur.fetchone()
        queued_before_or_equal = int(count_row["count"] or 0) if count_row else 0
        if has_running:
            return queued_before_or_equal
        return max(0, queued_before_or_equal - 1)

    async def mark_run_running(self, run_id: str) -> None:
        run = await self.get_run(run_id)
        now = _now()
        async with self._connect() as db:
            await db.execute(
                """
                UPDATE ai_conversation_runs
                SET status = 'running', started_at = COALESCE(started_at, ?)
                WHERE id = ?
                """,
                (now, run_id),
            )
            await db.execute(
                "UPDATE ai_conversations SET status = 'running', updated_at = ? WHERE id = ?",
                (now, run["conversation_id"]),
            )
            await db.commit()
        await self.append_event(
            run_id=run_id,
            conversation_id=run["conversation_id"],
            event_type="status",
            payload={"status": "running", "message": "已开始生成，正在读取线程上下文。"},
        )

    async def append_event(
        self,
        *,
        run_id: str,
        conversation_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        now = _now()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                """
                SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq
                FROM ai_run_events
                WHERE run_id = ?
                """,
                (run_id,),
            ) as seq_cur:
                seq_row = await seq_cur.fetchone()
            seq = int(seq_row["next_seq"] or 1) if seq_row else 1
            cur = await db.execute(
                """
                INSERT INTO ai_run_events
                    (run_id, conversation_id, seq, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, conversation_id, seq, event_type, _json_dumps(payload), now),
            )
            event_id = int(cur.lastrowid)
            await db.execute(
                "UPDATE ai_conversation_runs SET cursor = ? WHERE id = ?",
                (event_id, run_id),
            )
            await db.commit()
        return {
            "event_id": event_id,
            "run_id": run_id,
            "conversation_id": conversation_id,
            "seq": seq,
            "event_type": event_type,
            "payload": payload,
            "created_at": now,
        }

    async def list_events_after(
        self,
        conversation_id: str,
        *,
        cursor: int = 0,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        async with self._connect() as db:
            async with db.execute(
                """
                SELECT *
                FROM ai_run_events
                WHERE conversation_id = ? AND event_id > ?
                ORDER BY event_id ASC
                LIMIT ?
                """,
                (conversation_id, max(0, cursor), max(1, min(limit, 500))),
            ) as cur:
                return _public_events_from_rows(await cur.fetchall())

    async def list_events_for_run(
        self,
        conversation_id: str,
        run_id: str,
        *,
        limit: int = 200,
        process_only: bool = False,
    ) -> list[dict[str, Any]]:
        capped_limit = max(1, min(limit, 500))
        async with self._connect() as db:
            if process_only:
                async with db.execute(
                    """
                    SELECT *
                    FROM ai_run_events
                    WHERE conversation_id = ? AND run_id = ?
                    ORDER BY seq ASC, event_id ASC
                    """,
                    (conversation_id, run_id),
                ) as cur:
                    events = _public_events_from_rows(await cur.fetchall())
            else:
                async with db.execute(
                    """
                    SELECT *
                    FROM (
                        SELECT *
                        FROM ai_run_events
                        WHERE conversation_id = ? AND run_id = ?
                        ORDER BY seq DESC, event_id DESC
                        LIMIT ?
                    )
                    ORDER BY seq ASC, event_id ASC
                    """,
                    (conversation_id, run_id, capped_limit),
                ) as cur:
                    events = _public_events_from_rows(await cur.fetchall())
        if process_only:
            events = [event for event in events if _is_public_process_event(event)]
        return events[-capped_limit:]

    async def complete_run(
        self,
        *,
        run_id: str,
        content: str,
        references: list[dict[str, Any]],
        evidence_content: str | None = None,
        model: str | None = None,
        token_usage: dict[str, Any] | None = None,
        actions: list[dict[str, Any]] | None = None,
    ) -> None:
        run = await self.get_run(run_id)
        now = _now()
        conversation = await self.get_conversation(run["conversation_id"])
        enriched_references = await _enrich_references_with_answer_citations(
            conversation=conversation,
            references=references,
            content=evidence_content if evidence_content is not None else content,
            db_path=self.db_path,
        )
        safe_content = _govern_visible_assistant_content(
            redact_agent_diagnostic_text(content),
            enriched_references,
        )
        async with self._connect() as db:
            await db.execute("BEGIN")
            await db.execute(
                """
                INSERT INTO ai_messages
                    (id, conversation_id, run_id, role, content, references_json, actions_json, created_at)
                VALUES (?, ?, ?, 'assistant', ?, ?, ?, ?)
                """,
                (
                    _new_id("msg"),
                    run["conversation_id"],
                    run_id,
                    safe_content,
                    _json_dumps(enriched_references),
                    _json_dumps(actions or _default_actions()),
                    now,
                ),
            )
            await db.execute(
                """
                UPDATE ai_conversation_runs
                SET status = 'completed', completed_at = ?, model = ?, token_usage_json = ?
                WHERE id = ?
                """,
                (now, model, _json_dumps(token_usage or {}), run_id),
            )
            await db.execute(
                "UPDATE ai_conversations SET status = 'idle', updated_at = ? WHERE id = ?",
                (now, run["conversation_id"]),
            )
            await db.commit()
        await self.append_event(
            run_id=run_id,
            conversation_id=run["conversation_id"],
            event_type="done",
            payload={"status": "completed"},
        )

    async def fail_run(self, run_id: str, error: str) -> None:
        run = await self.get_run(run_id)
        now = _now()
        async with self._connect() as db:
            await db.execute(
                """
                UPDATE ai_conversation_runs
                SET status = 'failed', error = ?, completed_at = ?
                WHERE id = ?
                """,
                (error, now, run_id),
            )
            await db.execute(
                "UPDATE ai_conversations SET status = 'error', updated_at = ? WHERE id = ?",
                (now, run["conversation_id"]),
            )
            await db.commit()
        await self.append_event(
            run_id=run_id,
            conversation_id=run["conversation_id"],
            event_type="error",
            payload={"status": "failed", "error": error},
        )

    async def reconcile_interrupted_runs(self) -> dict[str, Any]:
        """Mark queued/running runs from a previous process as interrupted."""

        now = _now()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                """
                SELECT *
                FROM ai_conversation_runs
                WHERE status IN ('queued', 'running')
                ORDER BY conversation_id ASC, sequence ASC, created_at ASC
                """
            ) as cur:
                rows = await cur.fetchall()
            runs = [_run_from_row(row) for row in rows]
            for run in runs:
                await db.execute(
                    """
                    UPDATE ai_conversation_runs
                    SET status = 'interrupted', error = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    ("service restarted before AI run completed", now, run["id"]),
                )
            conversation_ids = sorted({str(run["conversation_id"]) for run in runs})
            for conversation_id in conversation_ids:
                async with db.execute(
                    """
                    SELECT 1
                    FROM ai_conversation_runs
                    WHERE conversation_id = ? AND status IN ('queued', 'running')
                    LIMIT 1
                    """,
                    (conversation_id,),
                ) as cur:
                    has_active = await cur.fetchone() is not None
                await db.execute(
                    """
                    UPDATE ai_conversations
                    SET status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    ("running" if has_active else "idle", now, conversation_id),
                )
            await db.commit()

        for run in runs:
            await self.append_event(
                run_id=run["id"],
                conversation_id=run["conversation_id"],
                event_type="error",
                payload={
                    "status": "interrupted",
                    "kind": "service_restart_interrupted",
                    "error": "后端服务重启，本轮 AI 线程生成已中断，请重新发送或继续提问。",
                    "technical_diagnostics": {
                        "previous_status": run["status"],
                    },
                },
            )
        return {
            "status": "ok",
            "interrupted_count": len(runs),
            "runs": [
                {
                    "run_id": str(run["id"]),
                    "conversation_id": str(run["conversation_id"]),
                    "previous_status": str(run["status"]),
                    "sequence": int(run.get("sequence") or 0),
                }
                for run in runs
            ],
        }

    async def cancel_run(self, conversation_id: str) -> dict[str, Any] | None:
        now = _now()
        async with self._connect() as db:
            async with db.execute(
                """
                SELECT *
                FROM ai_conversation_runs
                WHERE conversation_id = ? AND status = 'running'
                ORDER BY sequence ASC, started_at ASC, created_at ASC
                LIMIT 1
                """,
                (conversation_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                async with db.execute(
                    """
                    SELECT *
                    FROM ai_conversation_runs
                    WHERE conversation_id = ? AND status = 'queued'
                    ORDER BY sequence DESC, created_at DESC
                    LIMIT 1
                    """,
                    (conversation_id,),
                ) as cur:
                    row = await cur.fetchone()
            if row is None:
                async with db.execute(
                    """
                    SELECT *
                    FROM ai_conversation_runs
                    WHERE conversation_id = ?
                    ORDER BY sequence DESC, created_at DESC
                    LIMIT 1
                    """,
                    (conversation_id,),
                ) as cur:
                    row = await cur.fetchone()
                return _run_from_row(row) if row is not None else None

            run = _run_from_row(row)
            await db.execute(
                "UPDATE ai_conversation_runs SET status = 'cancelled', completed_at = ? WHERE id = ?",
                (now, run["id"]),
            )
            async with db.execute(
                """
                SELECT 1
                FROM ai_conversation_runs
                WHERE conversation_id = ? AND status IN ('queued', 'running') AND id != ?
                LIMIT 1
                """,
                (conversation_id, run["id"]),
            ) as cur:
                has_active_after_cancel = await cur.fetchone() is not None
            await db.execute(
                "UPDATE ai_conversations SET status = ?, updated_at = ? WHERE id = ?",
                ("running" if has_active_after_cancel else "idle", now, conversation_id),
            )
            await db.commit()
        await self.append_event(
            run_id=run["id"],
            conversation_id=conversation_id,
            event_type="delta",
            payload={"kind": "diagnostic", "content": "用户已停止本轮 Agent。"},
        )
        await self.append_event(
            run_id=run["id"],
            conversation_id=conversation_id,
            event_type="done",
            payload={"status": "cancelled"},
        )
        return await self.get_run(run["id"])

    async def get_agent_runtime_session(
        self,
        *,
        conversation_id: str,
        agent_runtime_id: str,
    ) -> dict[str, Any] | None:
        if not conversation_id or not agent_runtime_id:
            return None
        async with self._connect() as db:
            async with db.execute(
                """
                SELECT *
                FROM ai_agent_runtime_sessions
                WHERE conversation_id = ? AND agent_runtime_id = ?
                """,
                (conversation_id, agent_runtime_id),
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            return None
        data = dict(row)
        data["metadata"] = _json_loads(data.pop("metadata_json", "{}"), {})
        return data

    async def upsert_agent_runtime_session(
        self,
        *,
        conversation_id: str,
        agent_runtime_id: str,
        cli_session_id: str,
        resume_session_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not conversation_id or not agent_runtime_id:
            return
        cli_session_id = str(cli_session_id or "").strip()
        resume_session_id = str(resume_session_id or cli_session_id).strip()
        if not cli_session_id or not resume_session_id:
            return
        now = _now()
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO ai_agent_runtime_sessions
                    (conversation_id, agent_runtime_id, cli_session_id, resume_session_id,
                     metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id, agent_runtime_id) DO UPDATE SET
                    cli_session_id = excluded.cli_session_id,
                    resume_session_id = excluded.resume_session_id,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    conversation_id,
                    agent_runtime_id,
                    cli_session_id,
                    resume_session_id,
                    _json_dumps(metadata or {}),
                    now,
                    now,
                ),
            )
            await db.commit()

    async def delete_agent_runtime_session(
        self,
        *,
        conversation_id: str,
        agent_runtime_id: str,
    ) -> None:
        if not conversation_id or not agent_runtime_id:
            return
        async with self._connect() as db:
            await db.execute(
                """
                DELETE FROM ai_agent_runtime_sessions
                WHERE conversation_id = ? AND agent_runtime_id = ?
                """,
                (conversation_id, agent_runtime_id),
            )
            await db.commit()

    @asynccontextmanager
    async def _connect(self):
        db = await aiosqlite.connect(self.db_path)
        db.row_factory = aiosqlite.Row
        try:
            yield db
        finally:
            await db.close()


async def _resolve_workspace_id(
    db: aiosqlite.Connection,
    *,
    scope_type: str,
    scope_id: str,
    initial_context: dict[str, Any],
    explicit_workspace_id: str | None = None,
) -> str:
    if explicit_workspace_id and explicit_workspace_id.strip():
        return explicit_workspace_id.strip()
    context_workspace = initial_context.get("workspace_id")
    if isinstance(context_workspace, str) and context_workspace.strip():
        return context_workspace.strip()
    if scope_type == "workspace":
        return scope_id
    if scope_type == "module":
        workspace_id, _, _ = scope_id.partition(":")
        if workspace_id:
            return workspace_id
    if scope_type == "report":
        async with db.execute("SELECT workspace_id FROM workspace_reports WHERE id = ?", (scope_id,)) as cur:
            row = await cur.fetchone()
        if row and row["workspace_id"]:
            return str(row["workspace_id"])
    return "global"


def _resolve_memory_namespace(
    *,
    workspace_id: str,
    explicit_memory_namespace: str | None = None,
    initial_context: dict[str, Any] | None = None,
) -> str:
    if explicit_memory_namespace and explicit_memory_namespace.strip():
        return explicit_memory_namespace.strip()
    context_namespace = (initial_context or {}).get("memory_namespace")
    if isinstance(context_namespace, str) and context_namespace.strip():
        return context_namespace.strip()
    return f"workspace:{workspace_id}" if workspace_id and workspace_id != "global" else "global"


def _conversation_workspace_id(conversation: dict[str, Any]) -> str:
    value = conversation.get("workspace_id")
    if isinstance(value, str) and value.strip() and value.strip() != "global":
        return value.strip()
    scope_type = str(conversation.get("scope_type") or "")
    scope_id = str(conversation.get("scope_id") or "")
    initial_context = conversation.get("initial_context")
    if isinstance(initial_context, dict):
        context_workspace = initial_context.get("workspace_id")
        if isinstance(context_workspace, str) and context_workspace.strip():
            return context_workspace.strip()
    if scope_type == "workspace" and scope_id:
        return scope_id
    if scope_type == "module" and ":" in scope_id:
        return scope_id.split(":", 1)[0] or "global"
    return "global"


async def build_context_references(
    *,
    conversation: dict[str, Any],
    user_message: str,
    db_path: str | Path | None = None,
) -> list[ContextReference]:
    db_file = str(db_path or settings.sqlite_db)
    scope_type = str(conversation["scope_type"])
    scope_id = str(conversation["scope_id"])
    workspace_id = _conversation_workspace_id(conversation)
    source_analysis_declined = _source_analysis_declined(user_message)
    refs: list[ContextReference] = []
    seen: set[tuple[str, str]] = set()

    def append_refs(items: list[ContextReference]) -> None:
        for item in items:
            key = (item.source_type, item.source_id)
            if key in seen:
                continue
            refs.append(item)
            seen.add(key)

    async with aiosqlite.connect(db_file) as db:
        db.row_factory = aiosqlite.Row
        if workspace_id != "global":
            source_query = _source_query_for_conversation(conversation, user_message)
            workbench_repo_path = await _workbench_task_repo_path(scope_type, scope_id)
            append_refs(await _workspace_material_refs(db, workspace_id))
            if not source_analysis_declined:
                append_refs(
                    await _workspace_source_refs(
                        db,
                        workspace_id,
                        source_query,
                        fallback_repo_path=workbench_repo_path,
                    )
                )
                append_refs(await _workspace_refs(db, workspace_id))
            append_refs(await _workspace_chat_refs(db, workspace_id))
        if scope_type == "report":
            append_refs(await _report_refs(db, scope_id))
        elif scope_type == "module":
            append_refs(await _module_refs(db, scope_id))
    append_refs(await _workbench_task_refs(scope_type, scope_id))
    if workspace_id != "global" and not source_analysis_declined:
        append_refs(await _evidence_memory_refs(workspace_id, user_message))
        append_refs(await _semantic_case_refs(scope_id, user_message))
    return refs[:_MAX_CONTEXT_REFERENCES]


def _source_query_for_conversation(conversation: dict[str, Any], user_message: str) -> str:
    scope_type = str(conversation.get("scope_type") or "")
    scope_id = str(conversation.get("scope_id") or "")
    if scope_type == "module" and ":" in scope_id:
        _, _, module_path = scope_id.partition(":")
        if module_path.strip():
            return f"{module_path.strip()} {user_message}"
    return user_message


async def run_generation(
    *,
    store: AIConversationStore,
    run_id: str,
    llm: Any,
) -> None:
    run = await store.get_run(run_id)
    conversation = await store.get_conversation(run["conversation_id"])
    messages = await store.list_messages(conversation["id"])
    user_message = next(
        (msg for msg in reversed(messages) if msg["role"] == "user" and msg.get("run_id") == run_id),
        None,
    )
    if not user_message:
        await store.fail_run(run_id, "未找到本轮用户消息")
        return
    references = user_message.get("references") or []
    await store.mark_run_running(run_id)
    await store.append_event(
        run_id=run_id,
        conversation_id=conversation["id"],
        event_type="status",
        payload={
            "status": "running",
            "message": _context_status_message(
                references,
                source_analysis_declined=_source_analysis_declined(user_message["content"]),
            ),
        },
    )
    prompt = _build_prompt(conversation, messages, references, user_message["content"])
    chunks: list[str] = []
    artifact_stream_notice_sent = False
    wants_downloadable_artifact = _agent_task_requests_downloadable_artifact(
        user_message["content"],
        user_message["content"],
    )
    requires_strict_quality_gate = _requires_strict_test_activity_quality_gate(
        user_message["content"]
    )
    requested_token_budget = (
        max(settings.ai_conversation_max_output_tokens, _TEST_ACTIVITY_OUTPUT_TOKEN_BUDGET)
        if requires_strict_quality_gate
        else settings.ai_conversation_max_output_tokens
    )
    max_tokens = min(requested_token_budget, settings.llm_max_output_tokens)
    temperature = 0.5

    async def append_delta(content: str) -> None:
        nonlocal artifact_stream_notice_sent
        chunks.append(content)
        live_content = content
        live_kind = ""
        accumulated = "".join(chunks)
        if _should_compact_live_thread_delta(content, accumulated) or _agent_task_requests_downloadable_artifact(
            user_message["content"],
            accumulated,
        ):
            if artifact_stream_notice_sent:
                return
            artifact_stream_notice_sent = True
            live_content = _THREAD_ARTIFACT_STREAM_NOTICE
            live_kind = "artifact_progress"
        await store.append_event(
            run_id=run_id,
            conversation_id=conversation["id"],
            event_type="delta",
            payload={"content": live_content, **({"kind": live_kind} if live_kind else {})},
        )

    try:
        current = await store.get_run(run_id)
        if current["status"] == "cancelled":
            return
        current_finish_reason.set(None)
        if not settings.ai_conversation_streaming_enabled:
            response = await llm.complete(prompt, max_tokens=max_tokens, temperature=temperature)
            current = await store.get_run(run_id)
            if current["status"] == "cancelled":
                return
            await append_delta(response.content)
        else:
            try:
                async with asyncio.timeout(settings.ai_conversation_stream_timeout_sec):
                    async for delta in llm.stream_complete(
                        prompt,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    ):
                        current = await store.get_run(run_id)
                        if current["status"] == "cancelled":
                            return
                        await append_delta(delta)
            except TimeoutError:
                current = await store.get_run(run_id)
                if current["status"] == "cancelled":
                    return
                if chunks:
                    await append_delta("\n\n[模型流式输出超时，已返回当前可用内容。]")
                else:
                    logger.warning(
                        "AI conversation streaming timed out before first delta; retrying non-stream completion"
                    )
                    async with asyncio.timeout(settings.ai_conversation_stream_timeout_sec):
                        response = await llm.complete(prompt, max_tokens=max_tokens, temperature=temperature)
                    await append_delta(response.content)
        content = _govern_visible_assistant_content(
            "".join(chunks).strip() or "本轮没有生成有效内容，请换一种问法重试。",
            references,
        )
        finish_reason = str(current_finish_reason.get() or "").strip().lower()
        if finish_reason == "length":
            audit = {
                "kind": "test_activity_quality_audit",
                "source": "ai_thread_combined_markdown",
                "status": "needs_rework",
                "deliverable": False,
                "score": 0,
                "issue_count": 1,
                "issues": [
                    {
                        "code": "provider_output_truncated",
                        "artifact": "assistant-output.md",
                        "message": "模型输出达到长度上限，交付件被截断",
                    }
                ],
                "recommendations": [
                    "缩小单轮范围，或改用结构化工作流分步生成并校验交付件。"
                ],
            }
            await _record_ai_thread_quality_audit(
                store=store,
                run_id=run_id,
                conversation=conversation,
                audit=audit,
            )
            await store.fail_run(
                run_id,
                "模型输出达到长度上限，交付件不完整，系统未将其标记为完成。"
                "请点击重试，或使用“代码分析 → 流程梳理 → SFMEA → 黑盒用例”工作流分步生成。",
            )
            return
        if wants_downloadable_artifact and requires_strict_quality_gate:
            repo_path = (
                await _conversation_repo_path(conversation, db_path=store.db_path)
                or _conversation_initial_repo_path(conversation)
            )
            contract = _test_activity_contract_payload(
                user_message=user_message["content"],
                repo_path=repo_path,
            )
            audit = audit_test_activity_response(
                content=content,
                contract=contract,
                repo_path=repo_path or "",
            )
            await _record_ai_thread_quality_audit(
                store=store,
                run_id=run_id,
                conversation=conversation,
                audit=audit,
            )
            if not audit.get("deliverable"):
                issue_messages = [
                    str(item.get("message") or "").strip()
                    for item in audit.get("issues") or []
                    if isinstance(item, dict) and str(item.get("message") or "").strip()
                ]
                summary = "；".join(issue_messages[:3]) or "测试活动交付件不完整"
                await store.fail_run(
                    run_id,
                    f"测试活动产物未通过质量门禁（{audit.get('score', 0)} 分）：{summary}。"
                    "系统未生成下载交付件；请重试或改用结构化工作流补齐缺失内容。",
                )
                return
        model = str(getattr(llm, "_model", "") or "")
        final_content, actions = await _prepare_assistant_delivery(
            run_id=run_id,
            conversation=conversation,
            content=content,
            user_message=user_message["content"],
            force_artifact=_agent_task_requests_downloadable_artifact(user_message["content"], content),
        )
        await store.complete_run(
            run_id=run_id,
            content=final_content,
            references=references,
            evidence_content=content,
            model=model or None,
            actions=actions,
        )
    except Exception as exc:
        logger.exception("AI conversation run failed: %s", exc)
        await store.fail_run(run_id, str(exc))


async def _record_ai_thread_quality_audit(
    *,
    store: AIConversationStore,
    run_id: str,
    conversation: dict[str, Any],
    audit: dict[str, Any],
) -> None:
    audit_path = ai_thread_artifact_path(str(conversation["id"]), run_id).parent / "test_activity_quality_audit.json"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    await _write_json_file(audit_path, audit)
    await store.append_event(
        run_id=run_id,
        conversation_id=str(conversation["id"]),
        event_type="quality_audit",
        payload={
            "status": str(audit.get("status") or "needs_rework"),
            "score": int(audit.get("score") or 0),
            "issue_count": int(audit.get("issue_count") or 0),
            "message": (
                "测试活动质量门禁已通过"
                if audit.get("deliverable")
                else "测试活动质量门禁未通过，系统不会把不完整结果标记为完成"
            ),
        },
    )


async def run_agent_generation(
    *,
    store: AIConversationStore,
    run_id: str,
    runtime: dict[str, Any],
) -> None:
    run = await store.get_run(run_id)
    conversation = await store.get_conversation(run["conversation_id"])
    messages = await store.list_messages(conversation["id"])
    user_message = next(
        (msg for msg in reversed(messages) if msg["role"] == "user" and msg.get("run_id") == run_id),
        None,
    )
    if not user_message:
        await store.fail_run(run_id, "未找到本轮用户消息")
        return
    references = user_message.get("references") or []
    await store.mark_run_running(run_id)
    repo_path = await _conversation_repo_path(conversation)
    await store.append_event(
        run_id=run_id,
        conversation_id=conversation["id"],
        event_type="status",
        payload={
            "status": "running",
            "message": _context_status_message(
                references,
                source_analysis_declined=_source_analysis_declined(user_message["content"]),
            ),
        },
    )
    cwd = resolve_agent_cwd(runtime, repo_path=repo_path)
    runtime_id = str(runtime.get("id") or conversation.get("agent_runtime_id") or "").strip()
    resume_session_id = ""
    if runtime_id and str(runtime.get("session_persistence") or "none") == "resume_args":
        session = await store.get_agent_runtime_session(
            conversation_id=conversation["id"],
            agent_runtime_id=runtime_id,
        )
        if session:
            resume_session_id = str(session.get("resume_session_id") or session.get("cli_session_id") or "")
    prompt_runtime = dict(runtime)
    if str(runtime.get("session_persistence") or "none") == "resume_args" and not resume_session_id:
        prompt_runtime["force_prompt_history"] = True
    prompt = _build_agent_prompt(
        conversation,
        messages,
        references,
        user_message["content"],
        prompt_runtime,
        repo_path=repo_path,
    )
    chunks: list[str] = []
    live_chunks: list[str] = []
    session_updates: list[dict[str, Any]] = []
    artifact_stream_notice_sent = False
    adopted_agent_artifact = False
    agent_artifact_dir = ai_thread_agent_artifact_dir(conversation["id"], run_id).resolve()
    await _to_thread(agent_artifact_dir.mkdir, parents=True, exist_ok=True)
    invocation_manifest = _agent_thread_invocation_manifest(
        conversation=conversation,
        run_id=run_id,
        runtime=runtime,
        prompt=prompt,
        cwd=cwd,
        repo_path=repo_path,
        user_message=user_message["content"],
        references=references,
        artifact_dir=agent_artifact_dir,
        resume_session_id=resume_session_id,
    )
    await _write_json_file(
        agent_artifact_dir / "agent_invocation.json",
        invocation_manifest,
    )
    capability_manifest = agent_invocation_capability_manifest(invocation_manifest)
    await _write_json_file(
        agent_artifact_dir / "capability_manifest.json",
        capability_manifest,
    )
    await store.append_event(
        run_id=run_id,
        conversation_id=conversation["id"],
        event_type="artifact",
        payload=_agent_invocation_artifact_event_payload(invocation_manifest),
    )
    await store.append_event(
        run_id=run_id,
        conversation_id=conversation["id"],
        event_type="artifact",
        payload=agent_invocation_capability_event_payload(
            invocation_manifest,
            artifact="agent-artifacts/capability_manifest.json",
        ),
    )
    runtime_for_turn = dict(runtime)
    runtime_env = dict(runtime_for_turn.get("env") or {})
    runtime_env["CODETALK_AGENT_ARTIFACT_DIR"] = str(agent_artifact_dir)
    runtime_for_turn["env"] = runtime_env
    for milestone in _agent_run_start_milestones(
        runtime=runtime,
        cwd=cwd,
        references=references,
        resume_session_id=resume_session_id,
    ):
        await store.append_event(
            run_id=run_id,
            conversation_id=conversation["id"],
            event_type="delta",
            payload={"kind": "diagnostic", "content": milestone},
        )

    async def run_cancelled() -> bool:
        current = await store.get_run(run_id)
        return current["status"] == "cancelled"

    async def append_live_answer_delta(content: str) -> None:
        nonlocal artifact_stream_notice_sent
        live_content = content
        live_kind = ""
        accumulated_live_content = "".join(live_chunks) + content
        if _should_compact_live_thread_delta(content, accumulated_live_content) or _agent_task_requests_downloadable_artifact(
            user_message["content"],
            accumulated_live_content,
        ):
            if artifact_stream_notice_sent:
                return
            artifact_stream_notice_sent = True
            live_content = _THREAD_ARTIFACT_STREAM_NOTICE
            live_kind = "artifact_progress"
        await store.append_event(
            run_id=run_id,
            conversation_id=conversation["id"],
            event_type="delta",
            payload={"content": live_content, **({"kind": live_kind} if live_kind else {})},
        )
        live_chunks.append(live_content)

    async def append_agent_process_delta(content: str) -> None:
        await store.append_event(
            run_id=run_id,
            conversation_id=conversation["id"],
            event_type="delta",
            payload={"kind": "diagnostic", "content": content},
        )

    async def consume_agent_turn(turn_prompt: str, turn_resume_session_id: str | None) -> list[str]:
        turn_chunks: list[str] = []
        segment_state = _AgentOutputSegmentState()
        async for delta in stream_agent_runtime(
            runtime=runtime_for_turn,
            prompt=turn_prompt,
            cwd=cwd,
            resume_session_id=turn_resume_session_id,
            session_update=session_updates.append,
            stderr_update=append_agent_process_delta,
            is_cancelled=run_cancelled,
        ):
            if await run_cancelled():
                return turn_chunks
            is_final_answer = str(delta or "").startswith(AGENT_FINAL_ANSWER_PREFIX)
            final_answer_parts: list[str] = []
            for kind, content in _agent_output_segments(delta, state=segment_state):
                if kind == "diagnostic":
                    await append_agent_process_delta(content)
                    continue
                if is_final_answer:
                    final_answer_parts.append(content)
                    continue
                turn_chunks.append(content)
                if _agent_answer_chunk_safe_for_live_stream(content):
                    await append_live_answer_delta(content)
            if is_final_answer and final_answer_parts:
                final_answer = "".join(final_answer_parts)
                streaming_answer = "".join(turn_chunks)
                if _agent_final_answer_should_replace_streaming_answer(streaming_answer, final_answer):
                    turn_chunks = final_answer_parts
        return turn_chunks

    try:
        try:
            chunks = await consume_agent_turn(prompt, resume_session_id)
        except AgentRuntimeError as exc:
            if not _agent_resume_error_can_self_heal(exc, resume_session_id=resume_session_id):
                raise
            session_updates.clear()
            if runtime_id:
                await store.delete_agent_runtime_session(
                    conversation_id=conversation["id"],
                    agent_runtime_id=runtime_id,
                )
            resume_session_id = ""
            await store.append_event(
                run_id=run_id,
                conversation_id=conversation["id"],
                event_type="delta",
                payload={
                    "kind": "diagnostic",
                    "content": "旧会话已失效，CodeTalk 已切换为 fresh agent 会话重试本轮任务。",
                },
            )
            fresh_prompt = _build_agent_prompt(
                conversation,
                messages,
                references,
                user_message["content"],
                {**runtime, "force_prompt_history": True},
                repo_path=repo_path,
            )
            chunks = await consume_agent_turn(fresh_prompt, "")
        if await run_cancelled():
            return
        content = _govern_visible_assistant_content(
            "".join(chunks).strip() or "执行器没有返回有效内容，请检查命令输出模式。",
            references,
        )
        agent_artifact_content = await _agent_thread_artifact_content(agent_artifact_dir)
        if agent_artifact_content:
            content = agent_artifact_content
            adopted_agent_artifact = True
        if not adopted_agent_artifact and _agent_answer_requires_repair(user_message["content"], content, references):
            await store.append_event(
                run_id=run_id,
                conversation_id=conversation["id"],
                event_type="delta",
                payload={
                    "kind": "diagnostic",
                    "content": "上一次执行器输出过短，CodeTalk 正在自动续跑以完成原始任务。",
                },
            )
            latest_session_id = _latest_resume_session_id(session_updates) or resume_session_id
            repair_prompt = _build_agent_repair_prompt(
                conversation=conversation,
                references=references,
                user_message=user_message["content"],
                previous_answer=content,
                runtime=runtime,
            )
            chunks = await consume_agent_turn(repair_prompt, latest_session_id)
            if await run_cancelled():
                return
            content = _govern_visible_assistant_content(
                "".join(chunks).strip() or "执行器没有返回有效内容，请检查命令输出模式。",
                references,
            )
            agent_artifact_content = await _agent_thread_artifact_content(agent_artifact_dir)
            if agent_artifact_content:
                content = agent_artifact_content
                adopted_agent_artifact = True
            if not adopted_agent_artifact and _agent_answer_requires_repair(user_message["content"], content, references):
                await store.fail_run(
                    run_id,
                    (
                        "Agent 返回内容不足：已自动续跑一次，但仍未产出可验收的源码分析结论。"
                        "请切换可用执行器或继续追问缺失的证据、SFMEA、流程梳理和黑盒测试用例。"
                    ),
                )
                return
        agent_artifact_content = await _agent_thread_artifact_content(agent_artifact_dir)
        if await run_cancelled():
            return
        if agent_artifact_content:
            content = agent_artifact_content
            adopted_agent_artifact = True
        live_content = "".join(live_chunks)
        if not live_content:
            await append_live_answer_delta(content)
        elif content.startswith(live_content):
            suffix = content[len(live_content) :]
            if suffix:
                await append_live_answer_delta(suffix)
        elif content != live_content.strip():
            await store.append_event(
                run_id=run_id,
                conversation_id=conversation["id"],
                event_type="delta",
                payload={
                    "kind": "diagnostic",
                    "content": "CodeTalk 已在完成时整理执行器输出，最终回答以线程消息为准。",
                },
            )
        if runtime_id and session_updates:
            latest_session = session_updates[-1]
            await store.upsert_agent_runtime_session(
                conversation_id=conversation["id"],
                agent_runtime_id=runtime_id,
                cli_session_id=str(latest_session.get("session_id") or ""),
                resume_session_id=str(latest_session.get("resume_session_id") or latest_session.get("session_id") or ""),
                metadata={
                    "run_id": run_id,
                    "event_type": str(latest_session.get("event_type") or ""),
                },
            )
        final_content, actions = await _prepare_assistant_delivery(
            run_id=run_id,
            conversation=conversation,
            content=content,
            user_message=user_message["content"],
            force_artifact=adopted_agent_artifact
            or _agent_task_requests_downloadable_artifact(user_message["content"], content),
            artifact_only=adopted_agent_artifact,
        )
        artifact_action = next(
            (
                action
                for action in actions
                if isinstance(action, dict) and action.get("id") == "download_run_artifact"
            ),
            None,
        )
        if artifact_action:
            await store.append_event(
                run_id=run_id,
                conversation_id=conversation["id"],
                event_type="delta",
                payload={
                    "kind": "diagnostic",
                    "content": _agent_artifact_ready_milestone(
                        content=content,
                        artifact_href=str(artifact_action.get("href") or ""),
                    ),
                },
            )
        await store.complete_run(
            run_id=run_id,
            content=final_content,
            references=references,
            evidence_content=content,
            model=f"agent:{runtime.get('name') or runtime.get('id')}",
            actions=actions,
        )
    except Exception as exc:
        message = redact_agent_diagnostic_text(str(exc))
        logger.exception("AI agent runtime run failed: %s", message)
        public_message = _public_agent_run_error(message)
        await _record_agent_run_failure(
            store=store,
            run_id=run_id,
            conversation_id=conversation["id"],
            technical_message=message,
            public_message=public_message,
        )


async def maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _context_status_message(
    references: list[dict[str, Any]],
    *,
    source_analysis_declined: bool = False,
) -> str:
    source_types = {str(ref.get("source_type") or "") for ref in references}
    parts: list[str] = []
    graph_refs = [] if source_analysis_declined else _gitnexus_cgc_refs(references)
    if graph_refs:
        parts.append("GitNexus/CGC 图谱产物")
    if "workspace_source" in source_types:
        parts.append("工作区源码")
    if "workspace_material" in source_types:
        parts.append("输入材料")
    if "workspace_report" in source_types and not graph_refs:
        parts.append("历史报告")
    if "workbench_task_artifact" in source_types:
        parts.append("任务产物")
    if "semantic_case" in source_types:
        parts.append("语义案例")
    if not parts:
        if source_analysis_declined:
            return "按用户要求未强制读取 GitNexus/CGC 图谱或工作区源码；正在基于可用上下文回答。"
        return "GitNexus/CGC 图谱产物未命中；未找到直接匹配的工作区源码或输入材料。"
    if not graph_refs and not source_analysis_declined:
        return f"GitNexus/CGC 图谱产物未命中，已降级读取{'、'.join(parts)}上下文。"
    return f"正在读取{'、'.join(parts)}上下文。"


def _agent_run_start_milestones(
    *,
    runtime: dict[str, Any],
    cwd: str | Path | None,
    references: list[dict[str, Any]],
    resume_session_id: str = "",
) -> list[str]:
    runtime_name = redact_agent_diagnostic_text(
        str(runtime.get("name") or runtime.get("id") or "Agent")
    ).strip() or "Agent"
    lines = [f"CodeTalk 已启动 {runtime_name}。"]
    cwd_text = redact_agent_diagnostic_text(str(cwd or "")).strip()
    if cwd_text:
        workspace_name = Path(cwd_text).name or "当前工作区"
        lines.append(f"工作区已绑定：{workspace_name}。")
    if resume_session_id:
        lines.append("会话已延续：沿用当前线程的 Agent 上下文。")
    graph_paths = _public_reference_paths_for_process(references, source_type="", limit=4)
    source_paths = _public_reference_paths_for_process(references, source_type="workspace_source", limit=5)
    material_paths = _public_reference_paths_for_process(references, source_type="workspace_material", limit=3)
    if graph_paths:
        lines.append("图谱/报告证据已准备：" + "、".join(graph_paths))
    if source_paths:
        lines.extend(f"源码证据已准备：{path}" for path in source_paths)
    if material_paths:
        lines.extend(f"材料已准备：{path}" for path in material_paths)
    if not (graph_paths or source_paths or material_paths):
        lines.append("证据未命中可公开引用，Agent 将使用线程上下文继续。")
    return lines


def _agent_artifact_ready_milestone(*, content: str, artifact_href: str) -> str:
    size = len(str(content or "").encode("utf-8", errors="replace"))
    return f"下载产物已准备：约 {size} bytes，正文区仅保留摘要。"


def _public_reference_paths_for_process(
    references: list[dict[str, Any]],
    *,
    source_type: str,
    limit: int,
) -> list[str]:
    values: list[str] = []
    for ref in references:
        if not isinstance(ref, dict):
            continue
        ref_type = str(ref.get("source_type") or "")
        if source_type and ref_type != source_type:
            continue
        if not source_type and ref_type not in {"workspace_report", "gitnexus_artifact", "cgc_artifact"}:
            continue
        metadata = ref.get("metadata") if isinstance(ref.get("metadata"), dict) else {}
        path = str(metadata.get("path") or metadata.get("filename") or ref.get("title") or "").strip()
        if not path:
            continue
        if ref_type == "workspace_report" and not path.startswith("workspace_report:"):
            path = f"workspace_report:{path}"
        elif ref_type == "gitnexus_artifact" and not path.startswith("GitNexus"):
            path = f"GitNexus:{path}"
        elif ref_type == "cgc_artifact" and not path.startswith("CGC"):
            path = f"CGC:{path}"
        path = redact_agent_diagnostic_text(path)
        if path and path not in values:
            values.append(path)
        if len(values) >= max(1, limit):
            break
    return values


def _latest_resume_session_id(session_updates: list[dict[str, Any]]) -> str:
    for item in reversed(session_updates):
        value = str(item.get("resume_session_id") or item.get("session_id") or "").strip()
        if value:
            return value
    return ""


def _public_agent_run_error(error: Exception | str) -> str:
    message = redact_agent_diagnostic_text(str(error or "")).strip()
    lowered = message.lower()
    if (
        "separator is not found" in lowered
        or "chunk exceed the limit" in lowered
        or "limitoverrunerror" in lowered
    ):
        return (
            "执行器返回了过大的单条过程事件，CodeTalk 未能完成解析。"
            "请重试本轮；若仍失败，请切换执行器或减少单次输出。"
        )
    if "403" in lowered or "forbidden" in lowered:
        return "执行器鉴权失败（HTTP 403）。请在设置中检查账号、API Key 或代理权限后重试。"
    if re.fullmatch(r"执行器超时（\d+s）", message):
        return message
    activity_timeout = re.fullmatch(r"执行器连续 (\d+)s 没有输出或进度", message)
    if activity_timeout:
        return (
            f"执行器已连续 {activity_timeout.group(1)} 秒没有输出或进度。请检查 Agent 过程；"
            "若执行器仍在工作，请确认它会持续输出状态事件，否则从本轮重试。"
        )
    if message == "执行器单条过程事件超过安全上限，请减少单次工具输出后重试。":
        return message
    if message == (
        "外部 Agent 请求交互式文件写入权限，CodeTalk 已中止本轮。"
        "请让 Agent 输出最终 Markdown，由 CodeTalk 生成下载产物；不要写入源码工作区。"
    ):
        return (
            "外部 Agent 请求交互式文件写入权限，CodeTalk 已中止本轮。"
            "请让 Agent 输出最终 Markdown，由 CodeTalk 生成下载产物。"
        )
    if (
        message.startswith("启动执行器失败")
        or message.startswith("找不到命令")
        or "permission denied" in lowered
        or "file not found" in lowered
    ):
        return "执行器启动失败。请检查设置中的命令、工作目录和执行权限后重试。"
    return "执行器运行失败。请展开 Agent 过程查看内部诊断，然后重试或切换执行器。"


async def _record_agent_run_failure(
    *,
    store: Any,
    run_id: str,
    conversation_id: str,
    technical_message: str,
    public_message: str,
) -> None:
    if technical_message and technical_message != public_message:
        try:
            await store.append_event(
                run_id=run_id,
                conversation_id=conversation_id,
                event_type="delta",
                payload={"kind": "diagnostic", "content": f"内部诊断：{technical_message}"},
            )
        except Exception as exc:
            logger.warning("Failed to persist folded agent diagnostics for %s: %s", run_id, exc)
    await store.fail_run(run_id, public_message)


def _agent_resume_error_can_self_heal(
    exc: Exception,
    *,
    resume_session_id: str,
) -> bool:
    if not str(resume_session_id or "").strip():
        return False
    message = redact_agent_diagnostic_text(str(exc))
    return bool(
        re.search(
            r"No conversation found with session ID|no rollout found|missing_rollout",
            message,
            re.IGNORECASE,
        )
    )


def _agent_answer_requires_repair(
    user_message: str,
    content: str,
    references: list[dict[str, Any]],
) -> bool:
    if not _agent_task_requires_substantive_answer(user_message, references):
        return False
    if _looks_like_agent_thin_help_answer(content):
        return True
    if _looks_like_agent_no_final_answer_notice(content):
        return True
    if _looks_like_explicit_agent_probe_answer(content):
        return False
    return (
        _agent_task_requires_structured_delivery(user_message)
        or _agent_task_requires_source_grounding(user_message, references)
    ) and _agent_answer_too_thin_for_task(
        content,
        user_message=user_message,
    )


def _agent_task_requires_substantive_answer(
    user_message: str,
    references: list[dict[str, Any]],
) -> bool:
    text = str(user_message or "").lower()
    markers = (
        "源码",
        "代码",
        "工作区",
        "分析",
        "流程",
        "梳理",
        "sfmea",
        "failure mode",
        "黑盒",
        "测试用例",
        "测试设计",
        "风险",
        "证据",
        "spdk",
        "source",
        "code",
        "workflow",
        "test case",
        "black-box",
        "blackbox",
    )
    if _text_has_any_task_marker(text, markers):
        return True
    evidence_types = {
        "workspace_source",
        "workspace_material",
        "workspace_report",
        "workbench_task_artifact",
        "semantic_case",
    }
    return any(str(ref.get("source_type") or "") in evidence_types for ref in references)


def _agent_task_requires_structured_delivery(user_message: str) -> bool:
    text = str(user_message or "").lower()
    markers = (
        "sfmea",
        "failure mode",
        "黑盒",
        "测试用例",
        "测试设计",
        "流程梳理",
        "代码证据",
        "源码证据",
        "test case",
        "black-box",
        "blackbox",
    )
    return _text_has_any_task_marker(text, markers)


def _agent_task_requires_source_grounding(
    user_message: str,
    references: list[dict[str, Any]],
) -> bool:
    text = str(user_message or "").lower()
    markers = (
        "源码",
        "代码",
        "工作区",
        "仓库",
        "文件",
        "spdk",
        "source",
        "code",
        "repo",
        "repository",
        "workspace",
        "file",
        "lib/",
        "test/",
        ".c",
        ".h",
    )
    if _text_has_any_task_marker(text, markers):
        return True
    evidence_types = {
        "workspace_source",
        "workspace_material",
        "workbench_task_artifact",
    }
    return any(str(ref.get("source_type") or "") in evidence_types for ref in references)


def _text_has_any_task_marker(text: str, markers: tuple[str, ...]) -> bool:
    normalized = str(text or "").lower()
    for marker in markers:
        value = marker.lower()
        if not value:
            continue
        if _task_marker_needs_word_boundary(value):
            if re.search(rf"(?<![a-z0-9_]){re.escape(value)}(?![a-z0-9_])", normalized):
                return True
            continue
        if value in normalized:
            return True
    return False


def _task_marker_needs_word_boundary(marker: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9_]+(?: [a-z0-9_]+)*", marker))


def _looks_like_agent_thin_help_answer(content: str) -> bool:
    cleaned = clean_agent_output_text(str(content or "")).strip()
    lowered = cleaned.lower()
    if lowered.startswith(("最终答案", "final answer", "final_answer")):
        return False
    text = re.sub(r"\s+", "", lowered)
    if not text:
        return True
    help_markers = (
        "你好有什么需要帮助",
        "您好有什么需要帮助",
        "请问有什么可以帮",
        "有什么可以帮助",
        "howcanihelp",
        "whatcanido",
        "howmayihelp",
    )
    if any(marker in text for marker in help_markers):
        return True
    generic_done_markers = (
        "已完成",
        "完成了",
        "分析完成",
        "done",
        "completed",
    )
    return len(text) <= 24 and any(marker in text for marker in generic_done_markers)


def _looks_like_agent_no_final_answer_notice(content: str) -> bool:
    cleaned = clean_agent_output_text(str(content or "")).strip()
    text = re.sub(r"\s+", "", cleaned.lower())
    no_answer_markers = (
        "执行器没有返回有效内容",
        "agentdidnotreturnvalidcontent",
        "novalidcontent",
    )
    return any(marker in text for marker in no_answer_markers)


def _looks_like_explicit_agent_probe_answer(content: str) -> bool:
    text = clean_agent_output_text(str(content or "")).strip()
    if not text:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or len(lines) > 12:
        return False
    lowered = text.lower()
    if any(marker in lowered for marker in ("_missing", " missing ", "=false", "error", "failed", "traceback")):
        return False
    has_probe_marker = bool(
        re.search(r"\b[A-Z0-9]+(?:_[A-Z0-9]+)+_(?:OK|REPLY|PASS|PASSED|SUCCESS|FINAL)\b", text)
    )
    has_boolean_evidence = bool(re.search(r"\b[a-zA-Z][a-zA-Z0-9_]{2,}=true\b", text))
    return has_probe_marker and (has_boolean_evidence or len(text) >= 20)


def _agent_answer_too_thin_for_task(content: str, *, user_message: str = "") -> bool:
    text = clean_agent_output_text(str(content or "")).strip()
    lowered = text.lower()
    if len(text) < 80:
        return True
    requested = str(user_message or "").lower()
    if any(marker in requested for marker in ("sfmea", "failure mode")) and not any(
        marker in lowered for marker in ("sfmea", "failure mode", "rpn", "severity", "occurrence")
    ):
        return True
    if any(marker in requested for marker in ("黑盒", "测试用例", "测试设计", "black-box", "blackbox", "test case")):
        has_black_box_json = any(marker in lowered for marker in ("black_box_cases", "blackbox_cases", "test_cases"))
        if not has_black_box_json and not any(marker in lowered for marker in ("黑盒", "测试用例", "test case", "前置条件", "预期结果")):
            return True
        case_markers = _blackbox_case_count(text)
        requested_min_cases = _blackbox_requested_min_case_count(requested)
        if not has_black_box_json and requested_min_cases and case_markers < requested_min_cases:
            return True
        expectation_markers = sum(1 for marker in ("前置", "步骤", "预期", "观测", "失败诊断", "expected") if marker in lowered)
        if not has_black_box_json and case_markers < 2 and expectation_markers < 3:
            return True
        if not has_black_box_json and _blackbox_answer_missing_observability(text):
            return True
        if not has_black_box_json and _blackbox_task_requires_executable_detail(
            requested
        ) and _blackbox_answer_missing_failure_diagnostics(text):
            return True
    if any(marker in requested for marker in ("流程", "梳理", "workflow")) and not any(
        marker in lowered for marker in ("流程", "步骤", "阶段", "flow", "workflow")
    ):
        return True
    if any(marker in requested for marker in ("代码证据", "源码证据", "源码", "代码", "spdk", "source", "code")):
        evidence_markers = ("代码证据", "源码证据", "lib/", "test/", ".c", ".h", "function", "函数")
        if sum(1 for marker in evidence_markers if marker in lowered) < 2:
            return True
        if _source_evidence_missing_specific_flow_anchor(requested, text):
            return True
    lines = [line for line in text.splitlines() if line.strip()]
    return len(lines) <= 2 and len(text) < 220


def _source_evidence_missing_specific_flow_anchor(user_message: str, content: str) -> bool:
    anchors = _specific_flow_anchors(user_message)
    if not anchors:
        return False
    evidence_text = _source_evidence_section_text(content)
    if not evidence_text:
        return False
    evidence_without_paths = re.sub(
        r"\b(?:lib|test|scripts|include)/[^\s`:'）)]+(?::\d+)?",
        " ",
        evidence_text.lower(),
    )
    return not any(anchor in evidence_without_paths for anchor in anchors)


def _specific_flow_anchors(text: str) -> list[str]:
    normalized = str(text or "").lower()
    normalized = normalized.replace("nvme‑of", "nvme-of").replace("nvme_of", "nvme-of")
    tokens = re.findall(r"[a-z][a-z0-9_-]{2,}", normalized)
    behavior_tokens = {
        "auth",
        "chap",
        "complete",
        "connect",
        "digest",
        "disconnect",
        "failover",
        "login",
        "poller",
        "queue",
        "ready",
        "reconnect",
        "reset",
        "submit",
        "timeout",
    }
    anchors: list[str] = []
    for token in tokens:
        clean = token.replace("-", "_")
        candidates = {token, clean}
        if not candidates & behavior_tokens:
            continue
        anchor = clean if clean in behavior_tokens else token
        if anchor not in anchors:
            anchors.append(anchor)
    return anchors[:4]


def _source_evidence_section_text(content: str) -> str:
    lines = str(content or "").splitlines()
    sections: list[str] = []
    collecting = False
    for line in lines:
        stripped = line.strip()
        if re.match(r"^#{1,4}\s+", stripped):
            if collecting:
                break
            heading = stripped.lower()
            collecting = any(
                marker in heading
                for marker in ("代码证据", "源码证据", "source evidence", "code evidence")
            )
            continue
        if collecting:
            sections.append(line)
    if not sections:
        return ""
    evidence_lines = [
        line
        for line in sections
        if re.search(r"\b(?:lib|test|scripts|include)/[^\s`:'）)]+(?::\d+)?", line)
    ]
    return "\n".join(evidence_lines or sections)


def _blackbox_task_requires_executable_detail(requested: str) -> bool:
    text = str(requested or "").lower()
    markers = (
        "完整",
        "详细",
        "详尽",
        "测试设计",
        "失败诊断",
        "诊断线索",
        "可执行",
        "complete",
        "comprehensive",
        "detailed",
        "executable",
        "test design",
        "failure diagnostic",
        "triage",
    )
    return any(marker in text for marker in markers)


def _blackbox_requested_min_case_count(requested: str) -> int:
    text = str(requested or "").lower()
    count_words = {
        "两": 2,
        "二": 2,
        "俩": 2,
        "三": 3,
        "四": 4,
        "五": 5,
    }
    match = re.search(r"(\d{1,2})\s*(?:个|条|组|项)?\s*(?:黑盒)?(?:测试)?用例", text)
    if match:
        return max(1, min(20, int(match.group(1))))
    match = re.search(r"(两|二|俩|三|四|五)\s*(?:个|条|组|项)?\s*(?:黑盒)?(?:测试)?用例", text)
    if match:
        return count_words.get(match.group(1), 0)
    match = re.search(r"\b(two|three|four|five)\s+(?:black[- ]?box\s+)?(?:test\s+)?cases\b", text)
    if match:
        return {"two": 2, "three": 3, "four": 4, "five": 5}[match.group(1)]
    return 0


def _blackbox_case_count(content: str) -> int:
    case_heading_re = re.compile(
        r"^\s*(?:#{1,6}\s*)?(?:[-*]\s*)?(?:\d+[.)、]\s*)?(?:"
        r"TC[-_\s]?\d+"
        r"|用例\s*[:：]"
        r"|用例[一二三四五六七八九十\d]"
        r"|case\s*(?:\d+|[:：])"
        r"|test\s+case\s*(?:\d+|[:：])"
        r")",
        re.IGNORECASE,
    )
    return sum(1 for line in str(content or "").splitlines() if case_heading_re.search(line.strip()))


def _blackbox_answer_missing_observability(content: str) -> bool:
    lowered = clean_agent_output_text(str(content or "")).lower()
    observability_markers = (
        "观测",
        "可观测",
        "日志",
        "指标",
        "metric",
        "status",
        "state",
        "响应",
        "返回码",
        "错误码",
        "rpc",
        "trace",
    )
    return not any(marker in lowered for marker in observability_markers)


def _blackbox_answer_missing_failure_diagnostics(content: str) -> bool:
    lowered = clean_agent_output_text(str(content or "")).lower()
    diagnostic_markers = (
        "失败诊断",
        "诊断",
        "排查",
        "定位",
        "线索",
        "若",
        "如果",
        "否则",
        "root cause",
        "triage",
    )
    return not any(marker in lowered for marker in diagnostic_markers)


def _agent_final_answer_should_replace_streaming_answer(streaming_answer: str, final_answer: str) -> bool:
    streaming = clean_agent_output_text(str(streaming_answer or "")).strip()
    final = clean_agent_output_text(str(final_answer or "")).strip()
    if not final:
        return False
    if not streaming:
        return True
    if streaming in final:
        return True
    if final in streaming:
        return False
    streaming_score = _agent_answer_completeness_score(streaming)
    final_score = _agent_answer_completeness_score(final)
    if final_score > streaming_score:
        return True
    if final_score == streaming_score and len(final) >= len(streaming):
        return True
    return len(final) >= int(len(streaming) * 0.85) and final_score >= streaming_score


def _agent_answer_completeness_score(content: str) -> int:
    text = clean_agent_output_text(str(content or "")).strip()
    if not text:
        return 0
    lowered = text.lower()
    headings = len(re.findall(r"(?m)^#{1,3}\s+\S+", text))
    source_refs = len(re.findall(r"\b(?:lib|test|scripts|include)/[^\s`:'）)]+(?::\d+)?", text))
    numbered_cases = len(
        re.findall(
            r"(?mi)^\s*(?:[-*]|\d+[.)、]|#{2,4})\s*(?:\*\*)?(?:tc[-_ ]?\d+|用例|case|前置条件|步骤)",
            text,
        )
    )
    evidence_markers = sum(
        1
        for marker in (
            "源码锚点",
            "源码证据",
            "代码证据",
            "用例设计依据",
            "sfmea",
            "failure mode",
            "rpn",
            "severity",
            "occurrence",
            "detection",
            "黑盒测试用例",
            "预期结果",
            "观测点",
        )
        if marker in lowered
    )
    line_count = len([line for line in text.splitlines() if line.strip()])
    return (
        min(len(text), 12000) // 120
        + headings * 25
        + source_refs * 12
        + numbered_cases * 20
        + evidence_markers * 18
        + min(line_count, 160)
    )


@dataclass
class _AgentOutputSegmentState:
    diagnostic_active: bool = False
    diagnostic_prefix: str = ""
    diagnostic_streaming_text: bool = False
    tool_answer_active: bool = False


def _agent_output_segments(
    chunk: str,
    *,
    state: _AgentOutputSegmentState | None = None,
) -> list[tuple[str, str]]:
    text = clean_agent_output_text(str(chunk or ""))
    if not text.strip():
        return []
    final_answer_chunk = text.startswith(AGENT_FINAL_ANSWER_PREFIX)
    if text.startswith(AGENT_FINAL_ANSWER_PREFIX):
        text = text[len(AGENT_FINAL_ANSWER_PREFIX) :]
    elif text.startswith(AGENT_ANSWER_DELTA_PREFIX):
        text = text[len(AGENT_ANSWER_DELTA_PREFIX) :]
    segments: list[tuple[str, str]] = []
    diagnostic_buffer: list[str] = []
    diagnostic_prefix = state.diagnostic_prefix if state and state.diagnostic_active else ""
    diagnostic_streaming_text = bool(state and state.diagnostic_active and state.diagnostic_streaming_text)

    def flush_diagnostic() -> None:
        nonlocal diagnostic_buffer
        if diagnostic_buffer:
            segments.append(("diagnostic", "\n".join(diagnostic_buffer)))
            diagnostic_buffer = []

    def close_diagnostic_context() -> None:
        nonlocal diagnostic_prefix, diagnostic_streaming_text
        flush_diagnostic()
        diagnostic_prefix = ""
        diagnostic_streaming_text = False

    for line in text.splitlines(keepends=True):
        if line.startswith(AGENT_FINAL_ANSWER_PREFIX):
            line = line[len(AGENT_FINAL_ANSWER_PREFIX) :]
        elif line.startswith(AGENT_ANSWER_DELTA_PREFIX):
            line = line[len(AGENT_ANSWER_DELTA_PREFIX) :]
        content = line.strip()
        if not content:
            close_diagnostic_context()
            continue
        unwrapped_answer = _strip_agent_final_answer_marker(content)
        if unwrapped_answer != content:
            close_diagnostic_context()
            if not unwrapped_answer:
                continue
            line = unwrapped_answer + ("\n" if line.endswith(("\n", "\r")) else "")
            content = unwrapped_answer
        prefix = _agent_diagnostic_prefix(content)
        diagnostic = _agent_diagnostic_text(content) if prefix else ""
        if prefix:
            flush_diagnostic()
            if diagnostic:
                diagnostic_buffer.append(diagnostic)
            diagnostic_prefix = prefix
            diagnostic_streaming_text = not diagnostic
        elif _looks_like_agent_tool_invocation_line(content):
            flush_diagnostic()
            diagnostic_buffer.append(redact_agent_diagnostic_text(content))
            diagnostic_prefix = "tool:"
            diagnostic_streaming_text = False
        elif (
            not final_answer_chunk
            and not diagnostic_prefix
            and _looks_like_agent_bare_tool_result_line(content)
        ):
            flush_diagnostic()
            diagnostic_buffer.append(redact_agent_diagnostic_text(content))
            diagnostic_prefix = "tool:"
            diagnostic_streaming_text = False
        elif (diagnostic_buffer or diagnostic_prefix) and _agent_diagnostic_continuation(
            content,
            line,
            diagnostic_prefix,
            diagnostic_streaming_text=diagnostic_streaming_text,
            final_answer_chunk=final_answer_chunk,
        ):
            diagnostic_buffer.append(redact_agent_diagnostic_text(content))
        else:
            close_diagnostic_context()
            segments.append(("answer", line))
    flush_diagnostic()
    if state is not None:
        state.diagnostic_active = bool(diagnostic_prefix)
        state.diagnostic_prefix = diagnostic_prefix
        state.diagnostic_streaming_text = diagnostic_streaming_text
    return segments


def _agent_diagnostic_text(text: str) -> str:
    prefix = _agent_diagnostic_prefix(text)
    if prefix:
        return redact_agent_diagnostic_text(text[len(prefix):].strip())
    return ""


_AGENT_FINAL_ANSWER_MARKER_RE = re.compile(
    r"^(?:final\s+answer|final_answer|最终答案)\s*[:：]\s*",
    re.IGNORECASE,
)


def _strip_agent_final_answer_marker(text: str) -> str:
    return _AGENT_FINAL_ANSWER_MARKER_RE.sub("", str(text or "").strip(), count=1).strip()


def _agent_diagnostic_prefix(text: str) -> str:
    lowered = text.lower()
    for prefix in (
        "status:",
        "diagnostic:",
        "thinking:",
        "reasoning:",
        "trace:",
        "error:",
        "tool:",
        "tool_use:",
        "tool_result:",
    ):
        if lowered.startswith(prefix):
            return prefix
    return ""


def _agent_diagnostic_continuation(
    content: str,
    raw_line: str,
    diagnostic_prefix: str,
    *,
    diagnostic_streaming_text: bool = False,
    final_answer_chunk: bool = False,
) -> bool:
    if _looks_like_agent_answer_boundary(content):
        return False
    if raw_line[:1].isspace():
        return True
    lowered_prefix = diagnostic_prefix.lower()
    if lowered_prefix.startswith(("tool:", "tool_use:", "tool_result:")):
        return _looks_like_agent_process_output_line(content) or _looks_like_agent_tool_status_line(content)
    if diagnostic_streaming_text and lowered_prefix.startswith(("thinking:", "reasoning:", "trace:", "diagnostic:")):
        if _looks_like_agent_answer_intro_fragment(content):
            return False
        return not final_answer_chunk
    return _looks_like_agent_process_output_line(content)


def _looks_like_agent_process_output_line(content: str) -> bool:
    text = str(content or "").strip()
    if not text:
        return False
    if _looks_like_agent_bare_tool_result_line(text):
        return True
    if _SOURCE_CODE_LINE_RE.search(text):
        return True
    return False


def _looks_like_agent_bare_tool_result_line(content: str) -> bool:
    text = str(content or "").strip()
    if not text:
        return False
    if re.match(r"^\d{1,7}[:\t]", text):
        return True
    return bool(
        re.match(
            r"^[^\s:]+\.(?:c|h|cc|cpp|cxx|hpp|py|go|rs|ts|tsx|js|java|sh|md):\d+:",
            text,
        )
    )


def _looks_like_agent_tool_status_line(content: str) -> bool:
    text = str(content or "").strip().lower()
    return bool(
        re.match(r"^(?:stdout|stderr|status|exit[_ ]?code|return[_ ]?code|duration|elapsed)\s*[:=]", text)
        or re.match(r"^[a-z_][a-z0-9_.-]{1,60}\s*=\s*\S+", text, re.IGNORECASE)
    )


def _looks_like_agent_tool_invocation_line(content: str) -> bool:
    text = str(content or "").strip()
    if not text:
        return False
    if re.match(r"^(?:[$#>❯➜])\s*(?:rg|grep|find|fd|cat|sed|awk|ls|tree|python\d*|python3|git)\b", text):
        return True
    return bool(
        re.match(
            r"^(?:Bash|Read|Grep|Glob|Edit|Write|Task|TodoWrite)"
            r"(?:\s+\{|\s*\(|\s+(?:command|file(?:_path)?|path|pattern|query)\s*[=:])",
            text,
        )
    )


def _looks_like_agent_answer_intro_fragment(content: str) -> bool:
    text = str(content or "").strip()
    return text.startswith(("我已掌", "下面基", "基于 `", "基于`"))


def _looks_like_agent_answer_boundary(content: str) -> bool:
    text = str(content or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered.startswith((AGENT_FINAL_ANSWER_PREFIX.lower(), "final answer:", "final_answer:", "最终答案：", "最终答案:")):
        return True
    if _LEGACY_AGENT_REPORT_INTRO_RE.match(text):
        return True
    return bool(
        re.match(
            r"^#{1,3}\s*(?:结论|摘要|代码证据|流程|流程梳理|SFMEA|黑盒测试用例|测试用例|风险|用例设计依据|下一步建议)\b",
            text,
            re.IGNORECASE,
        )
    )


def _codex_style_answer_instruction() -> str:
    return (
        "输出格式要求：\n"
        "- 默认使用 Markdown。\n"
        "- 先用 1-2 句话给结论。\n"
        "- 然后使用二级标题分节。\n"
        "- 每节使用短段落或 bullet。\n"
        "- 文件路径、函数名、配置项、命令参数使用 inline code。\n"
        "- 多行命令、日志、补丁、代码必须使用 fenced code block。\n"
        "- 风险、原因、修改点、验证方式分开写。\n"
        "- 黑盒测试用例必须包含前置条件、步骤、预期结果、观测点和失败诊断线索；"
        "黑盒步骤不要要求调用内部函数或修改内部代码。\n"
        "- 不要输出大段无标题文本。\n"
        "- 不要把 STATUS、THINKING、TOOL、TRACE、reasoning、tool_use、tool_result 混入最终答案。"
    )


def _build_prompt(
    conversation: dict[str, Any],
    messages: list[dict[str, Any]],
    references: list[dict[str, Any]],
    user_message: str,
) -> list[dict[str, str]]:
    context_lines = []
    for index, ref in enumerate(references, start=1):
        context_lines.append(
            f"[{index}] {ref.get('source_type')} · {ref.get('title')}\n{ref.get('excerpt')}"
        )
    history = [
        {"role": msg["role"], "content": msg["content"]}
        for msg in messages[-_MAX_HISTORY_MESSAGES:]
        if msg["role"] in {"user", "assistant"}
    ]
    system = (
        "你是 CodeTalks 的 AI 测试调查助手。你要帮助测试人员围绕需求、代码、报告、"
        "Workbench 任务和测试用例持续追问。\n"
        "回答必须使用中文，先给结论，再给证据与下一步测试建议。"
        "如果引用不足，请明确标记“待验证”。\n"
        "当线程绑定 workspace 时，workspace_source 和 workspace_material 是优先证据；"
        "必须先依据源码片段和输入材料回答，再用报告或记忆补充。"
        "不要声称读过未出现在引用里的文件。\n\n"
        f"{_codex_style_answer_instruction()}\n\n"
        f"{_source_first_contract(references, user_message)}\n\n"
        f"{_test_activity_contract_prompt(user_message=user_message, repo_path=_conversation_initial_repo_path(conversation))}\n\n"
        f"线程范围: {conversation['scope_type']} / {conversation['scope_id']}\n"
        f"上下文引用:\n{chr(10).join(context_lines) if context_lines else '（暂无可用引用）'}"
    )
    return [{"role": "system", "content": system}, *history, {"role": "user", "content": user_message}]


def _build_agent_prompt(
    conversation: dict[str, Any],
    messages: list[dict[str, Any]],
    references: list[dict[str, Any]],
    user_message: str,
    runtime: dict[str, Any],
    *,
    repo_path: str | None = None,
) -> str:
    lines = [
        "你正在通过 CodeTalks AI 线程作为本机 Agent 执行任务。",
        f"执行器：{runtime.get('name') or runtime.get('id')}",
        f"线程：{conversation.get('title')} ({conversation.get('id')})",
        f"项目/工作区：{conversation.get('workspace_id')}",
        f"源码工作区：{_public_workspace_label(conversation)}",
        "执行要求：CodeTalk 已把执行器工作目录切到绑定工作区；如果线程绑定 workspace，"
        "先检查当前工作目录中的源码和输入材料，再回答；不要只凭模型记忆。",
        _codex_style_answer_instruction(),
        _agent_artifact_delivery_contract(user_message),
        _source_first_contract(references, user_message),
        _test_activity_contract_prompt(user_message=user_message, repo_path=repo_path or _conversation_initial_repo_path(conversation)),
        "",
    ]
    sentinel = str(runtime.get("sentinel_text") or "").strip()
    if str(runtime.get("completion_mode") or "") == "sentinel" and sentinel:
        lines.extend([
            f"本轮回答结束后，请单独输出一行：{sentinel}",
            "不要在正文中解释这个结束标记。",
            "",
        ])
    history = _agent_prompt_history(messages, user_message, runtime)
    for message in history:
        role = message.get("role", "user")
        content = str(message.get("content") or "")
        if role == "assistant":
            lines.append("历史助手回复：")
        else:
            lines.append("历史用户消息：")
        lines.append(content)
        lines.append("")
    if history:
        lines.append("本轮用户问题：")
    else:
        lines.append("用户问题：")
    lines.append(user_message)
    return "\n".join(lines).strip()


def _agent_artifact_delivery_contract(user_message: str) -> str:
    wants_downloadable_artifact = _agent_task_requests_downloadable_artifact(user_message, user_message)
    lines = [
        "ARTIFACT_DELIVERY_CONTRACT:",
        "  rule: CodeTalk 负责把最终 Markdown 物化为“下载完整产物”；Agent 不要为了满足用户的下载/保存诉求去写源码仓库文件。",
        "  do_not: 不要调用 Write/Edit 或 shell 重定向在源码工作区创建报告、SFMEA、测试用例文件。",
        "  final_answer: 如果用户要求完整报告、SFMEA、黑盒测试用例或可下载文件，请直接输出完整 Markdown 正文；CodeTalk 会自动压缩对话区并生成下载链接。",
        "  auxiliary_files: 只有确有机器可读辅助文件时，才可写入环境变量 CODETALK_AGENT_ARTIFACT_DIR 指向的目录；绝不要写入源码目录。",
    ]
    if wants_downloadable_artifact:
        lines.append("  current_task: 用户正在请求结构化/可下载产物；本轮必须遵守 final_answer 规则，不要发起交互式文件写入权限请求。")
    return "\n".join(lines)


def _test_activity_contract_prompt(*, user_message: str, repo_path: str | None = None) -> str:
    if not _looks_like_test_activity_request(user_message):
        return (
            "TEST_ACTIVITY_CONTRACT:\n"
            "  active: false\n"
            "  rule: 当前问题未识别为测试活动交付件请求；如涉及源码仍遵守 SOURCE_FIRST_CONTRACT。"
        )
    requested_outputs = [
        {"id": artifact.replace(".", "_"), "artifact": artifact, "type": "json" if artifact.endswith(".json") else "markdown"}
        for artifact in _requested_test_activity_outputs(user_message)
    ]
    contract = build_test_activity_contract(
        target=user_message,
        repo_path=repo_path or "",
        workflow_outputs=requested_outputs,
        user_requirements=user_message,
    )
    payload = json.dumps(
        _test_activity_prompt_contract(contract),
        ensure_ascii=False,
        sort_keys=True,
    )
    return "\n".join([
        "TEST_ACTIVITY_CONTRACT:",
        "  active: true",
        "  rule: AI/Agent 只能在契约范围内填充内容，不能自由决定交付件骨架；未验证关注点必须标为 ai_suggested_unverified。",
        "  payload_json: |",
        *[f"    {line}" for line in payload.splitlines()],
    ])


def _test_activity_prompt_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Reference the final user block instead of duplicating its text in the prompt."""

    payload = json.loads(json.dumps(contract, ensure_ascii=False))
    marker = "<CURRENT_USER_MESSAGE>"
    payload["target"] = marker
    payload["user_requirements"] = marker
    rationale = payload.get("focus_rationale")
    if isinstance(rationale, list):
        for item in rationale:
            if isinstance(item, dict) and item.get("source") == "user_explicit_requirement":
                item["summary"] = marker
    return payload


def _test_activity_contract_payload(*, user_message: str, repo_path: str | None = None) -> dict[str, Any]:
    if not _looks_like_test_activity_request(user_message):
        return {
            "contract_version": 1,
            "active": False,
            "target": str(user_message or ""),
            "repo_path": str(repo_path or ""),
            "artifact_contract": {},
        }
    requested_outputs = [
        {"id": artifact.replace(".", "_"), "artifact": artifact, "type": "json" if artifact.endswith(".json") else "markdown"}
        for artifact in _requested_test_activity_outputs(user_message)
    ]
    contract = build_test_activity_contract(
        target=user_message,
        repo_path=repo_path or "",
        workflow_outputs=requested_outputs,
        user_requirements=user_message,
    )
    return {"active": True, **contract}


def _agent_thread_invocation_manifest(
    *,
    conversation: dict[str, Any],
    run_id: str,
    runtime: dict[str, Any],
    prompt: str,
    cwd: str,
    repo_path: str | None,
    user_message: str,
    references: list[dict[str, Any]],
    artifact_dir: Path,
    resume_session_id: str,
) -> dict[str, Any]:
    test_activity_contract = _test_activity_contract_payload(
        user_message=user_message,
        repo_path=repo_path or _conversation_initial_repo_path(conversation),
    )
    mcp_profile = str(runtime.get("mcp_profile") or "").strip()
    skills = [str(item) for item in runtime.get("skills") or [] if str(item).strip()]
    prompt_text = redact_agent_diagnostic_text(prompt)
    return {
        "schema_version": 1,
        "source": "ai_thread",
        "conversation_id": str(conversation.get("id") or ""),
        "run_id": run_id,
        "workspace_id": _conversation_workspace_id(conversation),
        "runtime": {
            "id": str(runtime.get("id") or conversation.get("agent_runtime_id") or ""),
            "name": str(runtime.get("name") or ""),
            "provider": _agent_thread_runtime_provider(runtime, conversation),
            "command": redact_agent_diagnostic_text(str(runtime.get("command") or "")),
            "args": [redact_agent_diagnostic_text(str(item)) for item in runtime.get("args") or []],
            "prompt_transport": str(runtime.get("prompt_transport") or "stdin"),
            "output_mode": str(runtime.get("output_mode") or "auto"),
            "completion_mode": str(runtime.get("completion_mode") or "process_exit"),
            "working_dir_mode": str(runtime.get("working_dir_mode") or "project"),
        },
        "prompt": {
            "text": prompt_text,
            "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "chars": len(prompt),
            "redacted": prompt_text != prompt,
        },
        "cwd": cwd,
        "repo_path": str(repo_path or ""),
        "mcp_profile": mcp_profile,
        "skills": skills,
        "session": {
            "persistence": str(runtime.get("session_persistence") or "none"),
            "resume_session_id": resume_session_id or "",
            "mode": "resume" if resume_session_id else "fresh",
        },
        "execution_contract": build_agent_invocation_execution_contract(
            source_first=not _source_analysis_declined(user_message),
            cwd=cwd,
            repo_path=str(repo_path or ""),
            outputs={
                "user_requested_outputs": _user_requested_outputs_from_message(user_message),
            },
        ),
        "test_activity_contract": test_activity_contract,
        "artifact_contract": test_activity_contract.get("artifact_contract", {}),
        "references": {
            "count": len(references),
            "source_types": sorted({str(ref.get("source_type") or "") for ref in references}),
        },
        "artifact_dir": str(artifact_dir),
    }


def _agent_thread_runtime_provider(
    runtime: dict[str, Any],
    conversation: dict[str, Any],
) -> str:
    explicit = str(runtime.get("provider") or "").strip()
    if explicit:
        return explicit
    runtime_id = str(runtime.get("id") or conversation.get("agent_runtime_id") or "").strip()
    return f"agent-runtime:{runtime_id}" if runtime_id else ""


def _agent_invocation_artifact_event_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    return agent_invocation_artifact_event_payload(
        manifest,
        artifact="agent-artifacts/agent_invocation.json",
    )


def _looks_like_test_activity_request(text: str) -> bool:
    lower = str(text or "").lower()
    markers = (
        "测试",
        "sfmea",
        "fmea",
        "黑盒",
        "用例",
        "测试设计",
        "测试策略",
        "覆盖率",
        "风险",
        "failure mode",
        "black box",
        "test case",
        "test design",
    )
    return any(marker in lower or marker in text for marker in markers)


def _requested_test_activity_outputs(text: str) -> list[str]:
    outputs: list[str] = []
    lower = str(text or "").lower()
    if "sfmea" in lower or "fmea" in lower:
        outputs.append("sfmea.json")
    if "黑盒" in text or "用例" in text or "black box" in lower or "test case" in lower:
        outputs.append("black_box_cases.json")
    if "测试策略" in text or "strategy" in lower:
        outputs.append("test_strategy.md")
    if "测试设计" in text or "test design" in lower:
        outputs.append("test_design.md")
    if "流程" in text or "flow" in lower:
        outputs.append("business_flow.md")
    return outputs or ["business_flow.md", "sfmea.json", "black_box_cases.json"]


def _user_requested_outputs_from_message(text: str) -> list[dict[str, Any]]:
    value = _explicit_requested_output_text(text)
    if not value:
        return []
    return [
        {
            "source": "user_message",
            "value": value,
            "items": _split_user_requested_output_items(value),
        }
    ]


def _explicit_requested_output_text(text: str) -> str:
    source = str(text or "")
    pattern = re.compile(
        r"(?:指定输出|输出文件|输出产物|交付件|交付文件|需要输出|请输出)\s*[:：]\s*(?P<value>.+)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(source)
    if not match:
        return ""
    value = str(match.group("value") or "").strip()
    value = re.split(r"\n\s*\n|(?:^|\n)\s*(?:补充要求|约束|注意|背景)\s*[:：]", value, maxsplit=1)[0]
    return value.strip(" \t\r\n。；;")


def _split_user_requested_output_items(value: str) -> list[str]:
    parts = re.split(r"[\n,，、;；]+", str(value or ""))
    seen: set[str] = set()
    items: list[str] = []
    for part in parts:
        item = part.strip(" \t\r\n。；;")
        if not item or item in seen:
            continue
        seen.add(item)
        items.append(item)
        if len(items) >= 40:
            break
    return items


def _conversation_initial_repo_path(conversation: dict[str, Any]) -> str:
    initial_context = (
        conversation.get("initial_context")
        if isinstance(conversation.get("initial_context"), dict)
        else {}
    )
    return str(initial_context.get("repo_path") or initial_context.get("workspace_path") or "")


def _agent_prompt_history(
    messages: list[dict[str, Any]],
    user_message: str,
    runtime: dict[str, Any],
) -> list[dict[str, str]]:
    if (
        str(runtime.get("session_persistence") or "none") == "resume_args"
        and not runtime.get("force_prompt_history")
    ):
        return []
    history = [
        {
            "role": str(msg.get("role") or ""),
            "content": _agent_prompt_history_content(msg),
        }
        for msg in messages[-_MAX_HISTORY_MESSAGES:]
        if msg.get("role") in {"user", "assistant"}
    ]
    current = str(user_message or "").strip()
    for index in range(len(history) - 1, -1, -1):
        if history[index]["role"] == "user" and history[index]["content"].strip() == current:
            del history[index]
            break
    return history


def _agent_prompt_history_content(message: dict[str, Any]) -> str:
    content = str(message.get("content") or "")
    if message.get("role") != "assistant":
        return content
    actions = message.get("actions") if isinstance(message.get("actions"), list) else []
    has_download_artifact = any(
        isinstance(action, dict) and action.get("id") == "download_run_artifact"
        for action in actions
    )
    if not has_download_artifact:
        return content
    conversation_id = str(message.get("conversation_id") or "").strip()
    run_id = str(message.get("run_id") or "").strip()
    if not conversation_id or not run_id:
        return content
    path = ai_thread_artifact_path(conversation_id, run_id)
    if not path.exists() or not path.is_file():
        return content
    try:
        artifact_text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return content
    cleaned = sanitize_ai_thread_artifact_markdown(artifact_text) or artifact_text
    _header, body = _split_ai_thread_artifact_markdown(cleaned)
    artifact_body = (body if body is not None else cleaned).strip()
    if not artifact_body:
        return content
    if artifact_body == content.strip():
        return content
    return (
        f"{content.strip()}\n\n"
        "历史助手完整下载产物（用于延续上下文）：\n"
        f"{_clip(artifact_body, _MAX_AGENT_HISTORY_ARTIFACT_CHARS)}"
    ).strip()


def _build_agent_repair_prompt(
    *,
    conversation: dict[str, Any],
    references: list[dict[str, Any]],
    user_message: str,
    previous_answer: str,
    runtime: dict[str, Any],
) -> str:
    lines = [
        "你仍在同一个 CodeTalks AI 线程中。",
        f"执行器：{runtime.get('name') or runtime.get('id')}",
        f"线程：{conversation.get('title')} ({conversation.get('id')})",
        f"项目/工作区：{conversation.get('workspace_id')}",
        f"源码工作区：{_public_workspace_label(conversation)}",
        "",
        "上一次执行器输出过短，CodeTalk 判定它不能满足用户的源码分析任务。",
        "不要只问候用户，不要询问“有什么需要帮助”，不要只说已完成。",
        "请继续完成原始任务，并直接输出用户可见的最终答案。",
        "如果前一轮已经查过源码，请复用已有发现；如果没有，请先核对工作区源码和输入材料。",
        "",
        _codex_style_answer_instruction(),
        _agent_artifact_delivery_contract(user_message),
        _source_first_contract(references, user_message),
        "",
        "原始用户任务：",
        user_message.strip(),
        "",
        "上一轮可见输出：",
        _clip(previous_answer, 1000) or "（空）",
        "",
        "本轮必须至少包含：",
        "- `## 结论`",
        "- `## 代码证据`，列出文件路径/函数/关键状态或配置",
        "- `## 流程梳理` 或与原始任务等价的步骤说明",
        "- 如果原始任务要求 SFMEA、黑盒测试或测试设计，必须输出对应章节；长表格/大量用例可以交给 CodeTalk 文件化。",
        "- 黑盒测试用例必须补齐：前置条件、步骤、预期结果、观测点、失败诊断线索。",
    ]
    return "\n".join(lines).strip()


def _source_first_contract(references: list[dict[str, Any]], user_message: str = "") -> str:
    artifact_contract = _source_artifact_priority_contract(references, user_message)
    source_refs = [ref for ref in references if ref.get("source_type") == "workspace_source"]
    material_refs = [ref for ref in references if ref.get("source_type") == "workspace_material"]
    if not source_refs and not material_refs:
        return (
            f"{artifact_contract}\n"
            "SOURCE_FIRST_CONTRACT:\n"
            "  workspace_sources: []\n"
            "  workspace_materials: []\n"
            "  rule: 未找到直接源码或输入材料时，必须说明未验证，不得声称已读取工作区源码。"
        )

    lines = [
        artifact_contract,
        "SOURCE_FIRST_CONTRACT:",
        "  rule: 回答前先读取/核对 workspace_sources 与 workspace_materials；报告、记忆和模型知识只能补充，不能替代。",
        "  workspace_sources:",
    ]
    if source_refs:
        for ref in source_refs[:6]:
            metadata = ref.get("metadata") if isinstance(ref.get("metadata"), dict) else {}
            path = str(metadata.get("path") or ref.get("title") or ref.get("source_id") or "").strip()
            excerpt = _clip(str(ref.get("excerpt") or ""), 500)
            lines.extend(
                [
                    f"    - path: {path or 'unknown'}",
                    f"      title: {ref.get('title') or path or 'workspace source'}",
                    f"      evidence: |",
                ]
            )
            lines.extend(f"        {line}" for line in excerpt.splitlines()[:14])
    else:
        lines.append("    []")

    lines.append("  workspace_materials:")
    if material_refs:
        for ref in material_refs[:4]:
            metadata = ref.get("metadata") if isinstance(ref.get("metadata"), dict) else {}
            material_path = str(metadata.get("filename") or ref.get("title") or ref.get("source_id") or "").strip()
            excerpt = _clip(str(ref.get("excerpt") or ""), 500)
            lines.extend(
                [
                    f"    - path: {material_path or 'unknown'}",
                    f"      title: {ref.get('title') or material_path or 'workspace material'}",
                    f"      evidence: |",
                ]
            )
            lines.extend(f"        {line}" for line in excerpt.splitlines()[:14])
    else:
        lines.append("    []")
    return "\n".join(lines)


def _source_artifact_priority_contract(references: list[dict[str, Any]], user_message: str) -> str:
    declined = _source_analysis_declined(user_message)
    artifact_refs = _gitnexus_cgc_refs(references)
    if declined:
        return "\n".join([
            "SOURCE_ARTIFACT_PRIORITY:",
            "  source_analysis_declined: true",
            "  rule: 用户明确要求不要基于源码；不要强制查 GitNexus/CGC 或工作区源码，只能基于用户提供内容回答并标记限制。",
            "  gitnexus_cgc_artifacts: []",
        ])
    lines = [
        "SOURCE_ARTIFACT_PRIORITY:",
        "  source_analysis_declined: false",
        "  rule: 除非用户明确要求不要基于源码，回答前先查 GitNexus 和 CGC 产物，再核对工作区源码与输入文件；图谱缺失时必须说明降级。",
        "  gitnexus_cgc_artifacts:",
    ]
    if artifact_refs:
        for ref in artifact_refs[:6]:
            metadata = ref.get("metadata") if isinstance(ref.get("metadata"), dict) else {}
            report_type = str(metadata.get("report_type") or "").strip()
            title = str(ref.get("title") or ref.get("source_id") or "workspace report").strip()
            excerpt = _clip(str(ref.get("excerpt") or ""), 360)
            lines.extend([
                f"    - report_type: {report_type or 'unknown'}",
                f"      title: {title}",
                "      evidence: |",
            ])
            lines.extend(f"        {line}" for line in excerpt.splitlines()[:8])
    else:
        lines.append("    []")
    return "\n".join(lines)


def _gitnexus_cgc_refs(references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for ref in references:
        metadata = ref.get("metadata") if isinstance(ref.get("metadata"), dict) else {}
        haystack = " ".join(
            str(value or "")
            for value in (
                ref.get("source_type"),
                ref.get("source_id"),
                ref.get("title"),
                metadata.get("report_type"),
            )
        ).lower()
        if "gitnexus" in haystack or "cgc" in haystack:
            refs.append(ref)
    return refs


def _source_analysis_declined(user_message: str) -> bool:
    text = str(user_message or "").lower()
    declined_markers = (
        "不要基于源码",
        "不基于源码",
        "不要看源码",
        "不用看源码",
        "不要读取源码",
        "别查源码",
        "不要查源码",
        "不要使用源码",
        "只根据我给的描述",
        "只基于我给的内容",
        "do not use source",
        "don't use source",
        "without source",
        "do not read source",
        "do not inspect source",
        "do not use gitnexus",
        "do not use cgc",
    )
    return any(marker in text for marker in declined_markers)


def _public_workspace_label(conversation: dict[str, Any]) -> str:
    workspace_id = str(conversation.get("workspace_id") or "").strip()
    if workspace_id and workspace_id != "global":
        return f"workspace:{workspace_id}"
    return "global"


def repo_path_hint(conversation: dict[str, Any]) -> str:
    context = conversation.get("initial_context")
    if isinstance(context, dict):
        value = context.get("repo_path")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(conversation.get("workspace_id") or "global")


async def _conversation_repo_path(
    conversation: dict[str, Any],
    *,
    db_path: str | Path | None = None,
) -> str | None:
    workspace_id = _conversation_workspace_id(conversation)
    if workspace_id != "global":
        async with aiosqlite.connect(str(db_path or settings.sqlite_db)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT repo_path FROM workspaces WHERE id = ?", (workspace_id,)) as cur:
                row = await cur.fetchone()
        if row and row["repo_path"]:
            return str(row["repo_path"])
    workbench_repo = await _workbench_task_repo_path(
        str(conversation.get("scope_type") or ""),
        str(conversation.get("scope_id") or ""),
    )
    if workbench_repo:
        return workbench_repo
    return None


async def _enrich_references_with_answer_citations(
    *,
    conversation: dict[str, Any],
    references: list[dict[str, Any]],
    content: str,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    workspace_id = _conversation_workspace_id(conversation)
    if workspace_id == "global":
        return references
    repo_path = await _conversation_repo_path(conversation, db_path=db_path)
    if not repo_path:
        return references
    repo = Path(repo_path).expanduser()
    if not repo.exists() or not repo.is_dir():
        return references
    precise_refs = await _to_thread(
        _answer_citation_refs_sync,
        repo,
        workspace_id,
        content,
        references,
    )
    if not precise_refs:
        return references
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ref in [*precise_refs, *references]:
        source_type = str(ref.get("source_type") or "")
        source_id = str(ref.get("source_id") or "")
        key = (source_type, source_id)
        if not source_type or not source_id or key in seen:
            continue
        merged.append(ref)
        seen.add(key)
        if len(merged) >= _MAX_CONTEXT_REFERENCES:
            break
    return merged


def _answer_citation_refs_sync(
    repo: Path,
    workspace_id: str,
    content: str,
    references: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    repo_root = repo.resolve()
    reference_paths = _workspace_source_reference_paths(references)
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cited_path, line_no in _source_citations(str(content or "")):
        for rel_path in _resolve_cited_source_path(repo_root, cited_path, reference_paths):
            candidate = (repo_root / rel_path).resolve()
            if not _safe_source_file(repo_root, candidate):
                continue
            ref = _source_file_ref(repo_root, workspace_id, candidate, line=line_no)
            if not ref or ref.source_id in seen:
                continue
            refs.append(ref.to_dict())
            seen.add(ref.source_id)
            break
        if len(refs) >= 8:
            break
    return refs


def _source_citations(text: str) -> list[tuple[str, int]]:
    citations: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for match in _SOURCE_CITATION_RE.finditer(text or ""):
        cited_path = match.group("path").strip("`'\"()[]{}.,;")
        try:
            line_no = max(1, int(match.group("line")))
        except (TypeError, ValueError):
            continue
        key = (cited_path, line_no)
        if key in seen:
            continue
        citations.append(key)
        seen.add(key)
        if len(citations) >= 24:
            break
    return citations


def _workspace_source_reference_paths(references: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for ref in references:
        if ref.get("source_type") != "workspace_source":
            continue
        metadata = ref.get("metadata") if isinstance(ref.get("metadata"), dict) else {}
        path = str(metadata.get("path") or "").strip()
        if path and path not in paths:
            paths.append(path)
    return paths


def _resolve_cited_source_path(repo_root: Path, cited_path: str, reference_paths: list[str]) -> list[str]:
    normalized = cited_path.strip().strip("/")
    if not normalized or ".." in normalized:
        return []
    if "/" in normalized:
        candidate = (repo_root / normalized).resolve()
        if _safe_source_file(repo_root, candidate):
            return [candidate.relative_to(repo_root).as_posix()]
        return []
    reference_matches = [path for path in reference_paths if Path(path).name == normalized]
    if len(reference_matches) == 1:
        return reference_matches
    rg_matches = _repo_paths_with_basename(repo_root, normalized)
    if len(rg_matches) == 1:
        return rg_matches
    return []


def _repo_paths_with_basename(repo_root: Path, basename: str) -> list[str]:
    try:
        result = subprocess.run(
            [
                "rg",
                "--files",
                "--glob",
                f"**/{basename}",
                "--glob",
                "!**/.git/**",
                "--glob",
                "!**/build/**",
                "--glob",
                "!**/node_modules/**",
            ],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
        )
    except Exception:
        return []
    matches: list[str] = []
    for rel in result.stdout.splitlines():
        candidate = (repo_root / rel).resolve()
        if _safe_source_file(repo_root, candidate):
            normalized = candidate.relative_to(repo_root).as_posix()
            if normalized not in matches:
                matches.append(normalized)
    return matches[:3]


async def _workspace_material_refs(db: aiosqlite.Connection, workspace_id: str) -> list[ContextReference]:
    async with db.execute(
        """
        SELECT id, filename, content_type, file_path
        FROM workspace_materials
        WHERE workspace_id = ? AND is_active = 1
        ORDER BY created_at DESC
        LIMIT 4
        """,
        (workspace_id,),
    ) as cur:
        rows = await cur.fetchall()
    refs: list[ContextReference] = []
    for row in rows:
        path = Path(str(row["file_path"] or ""))
        if not path.exists() or not path.is_file():
            continue
        try:
            text = await _read_text(path)
        except Exception:
            continue
        refs.append(
            ContextReference(
                source_type="workspace_material",
                source_id=str(row["id"]),
                title=str(row["filename"] or path.name),
                excerpt=_clip(text),
                metadata={
                    "workspace_id": workspace_id,
                    "content_type": row["content_type"],
                    "filename": str(row["filename"] or path.name),
                },
            )
        )
    return refs


async def _workspace_source_refs(
    db: aiosqlite.Connection,
    workspace_id: str,
    query: str,
    *,
    fallback_repo_path: str | None = None,
) -> list[ContextReference]:
    async with db.execute("SELECT repo_path FROM workspaces WHERE id = ?", (workspace_id,)) as cur:
        row = await cur.fetchone()
    repo_path = str(row["repo_path"]) if row and row["repo_path"] else str(fallback_repo_path or "")
    if not repo_path:
        return []
    repo = Path(repo_path).expanduser()
    if not repo.exists() or not repo.is_dir():
        return []
    return await _to_thread(_collect_source_refs_sync, repo, workspace_id, query)


def _collect_source_refs_sync(repo: Path, workspace_id: str, query: str) -> list[ContextReference]:
    repo_root = repo.resolve()
    refs: list[ContextReference] = []
    seen: set[str] = set()
    matched_path_hint = False
    for path_hint in _path_hints(query):
        candidate = (repo_root / path_hint).resolve()
        if _safe_source_dir(repo_root, candidate):
            for source_path in _directory_source_candidates(repo_root, candidate, query=query):
                ref = _source_file_ref(
                    repo_root,
                    workspace_id,
                    source_path,
                    line=_best_source_line_for_query(source_path, query),
                )
                if ref and ref.source_id not in seen:
                    refs.append(ref)
                    seen.add(ref.source_id)
                    matched_path_hint = True
                if len(refs) >= 4:
                    return refs
            continue
        if _safe_source_file(repo_root, candidate):
            ref = _source_file_ref(
                repo_root,
                workspace_id,
                candidate,
                line=_best_source_line_for_query(candidate, query),
            )
            if ref and ref.source_id not in seen:
                refs.append(ref)
                seen.add(ref.source_id)
                matched_path_hint = True
        if len(refs) >= 4:
            return refs
    if matched_path_hint and refs:
        return refs

    for term in _query_terms(query):
        for rel_path, line_no in _rg_matches(repo_root, term):
            candidate = (repo_root / rel_path).resolve()
            if not _safe_source_file(repo_root, candidate):
                continue
            ref = _source_file_ref(repo_root, workspace_id, candidate, line=line_no)
            if ref and ref.source_id not in seen:
                refs.append(ref)
                seen.add(ref.source_id)
            if len(refs) >= 4:
                return refs

    if refs:
        return refs
    for rel_path in _repo_file_candidates(repo_root):
        candidate = (repo_root / rel_path).resolve()
        if not _safe_source_file(repo_root, candidate):
            continue
        ref = _source_file_ref(repo_root, workspace_id, candidate, line=1)
        if ref and ref.source_id not in seen:
            refs.append(ref)
            seen.add(ref.source_id)
        if len(refs) >= 2:
            break
    return refs


def _query_terms(text: str) -> list[str]:
    raw = re.findall(r"[A-Za-z_][A-Za-z0-9_./-]{2,}", text or "")
    terms: list[str] = []
    for item in raw:
        term = item.strip("./").lower()
        if len(term) < 3 or term in _QUERY_STOPWORDS:
            continue
        if "/" in term or "." in term:
            continue
        if term not in terms:
            terms.append(term)
        if len(terms) >= 5:
            break
    return terms


def _path_hints(text: str) -> list[str]:
    hints: list[str] = []
    for item in re.findall(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+", text or ""):
        clean = item.strip("/")
        if clean and ".." not in clean and clean not in hints:
            hints.append(clean)
    for hint in _storage_domain_path_hints(text):
        if hint not in hints:
            hints.append(hint)
    return hints[:6]


def _storage_domain_path_hints(text: str) -> list[str]:
    normalized = (text or "").lower()
    if not normalized:
        return []
    normalized = normalized.replace("nvme‑of", "nvme-of").replace("nvme_of", "nvme-of")
    hints: list[str] = []
    for aliases, paths in _STORAGE_DOMAIN_PATH_HINTS:
        if any(alias in normalized for alias in aliases):
            for path in paths:
                if path not in hints:
                    hints.append(path)
    return hints


def _rg_matches(repo_root: Path, term: str) -> list[tuple[str, int]]:
    try:
        result = subprocess.run(
            [
                "rg",
                "--line-number",
                "--no-heading",
                "--smart-case",
                "--max-count",
                "2",
                "--glob",
                "!**/.git/**",
                "--glob",
                "!**/build/**",
                "--glob",
                "!**/node_modules/**",
                term,
                ".",
            ],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
        )
    except Exception:
        return []
    matches: list[tuple[str, int]] = []
    for line in result.stdout.splitlines():
        path_text, sep, rest = line.partition(":")
        if not sep:
            continue
        line_text, _, _ = rest.partition(":")
        try:
            line_no = max(1, int(line_text))
        except ValueError:
            line_no = 1
        if path_text:
            matches.append((path_text, line_no))
    return matches[:6]


def _repo_file_candidates(repo_root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["rg", "--files", "--glob", "!**/.git/**", "--glob", "!**/build/**"],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
        )
    except Exception:
        return []
    candidates: list[str] = []
    for rel in result.stdout.splitlines():
        suffix = Path(rel).suffix.lower()
        if suffix not in _SOURCE_SUFFIXES:
            continue
        if _low_value_fallback_source(rel):
            continue
        candidates.append(rel)
    return sorted(candidates, key=_fallback_source_rank)[:400]


def _low_value_fallback_source(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/").lower()
    name = Path(normalized).name
    if ".min." in name or name.endswith(".bundle.js") or name.endswith(".map"):
        return True
    if normalized.startswith(("doc/", "docs/", "documentation/")):
        return True
    if "/vendor/" in normalized or "/third_party/" in normalized:
        return True
    return False


def _fallback_source_rank(rel_path: str) -> tuple[int, int, str]:
    normalized = rel_path.replace("\\", "/").lower()
    suffix = Path(normalized).suffix.lower()
    if normalized.startswith("lib/nvmf/"):
        domain = 0
    elif normalized.startswith("lib/bdev/"):
        domain = 1
    elif normalized.startswith("lib/iscsi/"):
        domain = 2
    elif normalized.startswith(("lib/blob/", "lib/ftl/", "module/bdev/ftl/")):
        domain = 3
    elif normalized.startswith(("lib/vhost/", "lib/vfio_user/", "lib/vfu_tgt/")):
        domain = 4
    elif normalized.startswith(("lib/thread/", "lib/event/")):
        domain = 5
    elif normalized.startswith(("lib/", "module/")):
        domain = 6
    elif normalized.startswith("test/"):
        domain = 8
    else:
        domain = 9
    if suffix in {".c", ".cc", ".cpp", ".cxx"}:
        kind = 0
    elif suffix in {".h", ".hh", ".hpp"}:
        kind = 1
    elif suffix in {".py", ".go", ".rs", ".java", ".ts", ".tsx", ".js", ".jsx"}:
        kind = 2
    elif suffix == ".sh":
        kind = 3
    elif suffix in {".md", ".rst", ".txt"}:
        kind = 5
    else:
        kind = 4
    return (domain, kind, normalized)


def _safe_source_file(repo_root: Path, path: Path) -> bool:
    try:
        path.relative_to(repo_root)
    except ValueError:
        return False
    return path.exists() and path.is_file() and path.suffix.lower() in _SOURCE_SUFFIXES


def _safe_source_dir(repo_root: Path, path: Path) -> bool:
    try:
        path.relative_to(repo_root)
    except ValueError:
        return False
    return path.exists() and path.is_dir()


def _directory_source_candidates(repo_root: Path, directory: Path, *, query: str = "") -> list[Path]:
    ignored_parts = {".git", "build", "node_modules", ".next", ".venv", "__pycache__"}
    candidates: list[Path] = []
    query_terms = _source_relevance_terms(query)
    symbol_terms = _symbol_query_terms(query)
    try:
        paths = sorted(
            directory.rglob("*"),
            key=lambda path: _source_candidate_rank_for_query(path, query_terms, symbol_terms),
        )
    except Exception:
        return []
    for path in paths:
        if len(candidates) >= 4:
            break
        if any(part in ignored_parts for part in path.parts):
            continue
        resolved = path.resolve()
        if _safe_source_file(repo_root, resolved):
            candidates.append(resolved)
    return candidates


def _source_candidate_rank_for_query(
    path: Path,
    query_terms: list[str],
    symbol_terms: list[str] | None = None,
) -> tuple[int, int, int, str]:
    rel_text = path.as_posix().lower()
    name_text = path.stem.lower()
    symbol_terms = symbol_terms or []
    symbol_matched = _source_file_contains_any(path, symbol_terms)
    content_matched = _source_file_contains_any(path, query_terms)
    matched = any(term in name_text or term in rel_text for term in query_terms)
    bucket, normalized = _source_candidate_rank(path)
    return (0 if symbol_matched else 1, 0 if content_matched else 1, 0 if matched else 1, bucket, normalized)


def _source_relevance_terms(text: str) -> list[str]:
    terms: list[str] = []
    for term in [*_specific_flow_anchors(text), *_query_terms(text)]:
        normalized = term.replace("-", "_").lower()
        if normalized in {"spdk", "nvme", "nvmf", "target", "workspace", "source", "code"}:
            continue
        if len(normalized) < 3 or normalized in terms:
            continue
        terms.append(normalized)
        if len(terms) >= 6:
            break
    return terms


def _best_source_line_for_query(path: Path, query: str) -> int:
    terms = [*_symbol_query_terms(query), *_source_relevance_terms(query)]
    if not terms:
        return 1
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return 1
    best_line = 1
    best_score = 0
    for idx, line in enumerate(lines, start=1):
        previous_line = lines[idx - 2] if idx >= 2 else ""
        next_line = lines[idx] if idx < len(lines) else ""
        score = _source_line_match_score(line, terms, previous_line=previous_line, next_line=next_line)
        if score > best_score:
            best_line = idx
            best_score = score
    return best_line if best_score > 0 else 1


def _source_line_match_score(
    line: str,
    terms: list[str],
    *,
    previous_line: str = "",
    next_line: str = "",
) -> int:
    normalized = line.lower().replace("-", "_")
    matched_terms = [term for term in terms if _source_text_has_term(normalized, term)]
    if not matched_terms:
        return 0

    stripped = line.strip()
    score = len(matched_terms) * 10
    if stripped.startswith(("#", "/*", "*", "//")):
        score -= 8
    if stripped.startswith("#define"):
        score -= 6
    if "(" in stripped and ")" in stripped and not stripped.startswith("#"):
        score += 6
    if re.match(r"^(static\s+)?(inline\s+)?[A-Za-z_][A-Za-z0-9_\s\*]*\s+[A-Za-z_][A-Za-z0-9_]*\s*\(", stripped):
        score += 6
    if _source_line_looks_like_function_definition(stripped, previous_line, next_line):
        score += 14
    score += _source_function_name_intent_score(stripped, matched_terms)
    if "{" in stripped:
        score += 2
    if stripped and stripped.upper() == stripped and any(term.upper() in stripped for term in matched_terms):
        score -= 3
    return score


def _source_line_looks_like_function_definition(line: str, previous_line: str, next_line: str) -> bool:
    if not line or line.startswith(("#", "/*", "*", "//")):
        return False
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*\([^;]*\)\s*$", line):
        return False
    previous = previous_line.strip()
    if not previous or previous.startswith(("#", "//", "/*", "*")):
        return False
    if previous.endswith((";", ")", "}", "{")):
        return False
    if next_line.strip() != "{":
        return False
    return bool(re.search(r"\b[A-Za-z_][A-Za-z0-9_\s\*]*$", previous))


def _source_function_name_intent_score(line: str, matched_terms: list[str]) -> int:
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(", line)
    if not match:
        return 0
    name = match.group(1).lower()
    score = 0
    if "connect" in matched_terms and (name.endswith("_connect") or name.endswith("_cmd_connect")):
        score += 8
    if "connect" in matched_terms and any(token in name for token in ("send", "rsp", "response")):
        score -= 8
    return score


def _source_text_has_term(text: str, term: str) -> bool:
    normalized = str(term or "").replace("-", "_").lower()
    if not normalized:
        return False
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", text))


def _symbol_query_terms(text: str) -> list[str]:
    terms: list[str] = []
    for item in re.findall(r"[A-Za-z_][A-Za-z0-9_]{4,}", text or ""):
        token = item.strip("_")
        if "_" not in token:
            continue
        lowered = token.lower()
        if lowered in _QUERY_STOPWORDS or lowered in terms:
            continue
        terms.append(lowered)
        if len(terms) >= 4:
            break
    return terms


def _source_file_contains_any(path: Path, terms: list[str]) -> bool:
    if not terms or path.suffix.lower() not in _SOURCE_SUFFIXES:
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:262_144].lower()
    except Exception:
        return False
    normalized = text.replace("-", "_")
    return any(_source_text_has_term(normalized, term) for term in terms)


def _source_candidate_rank(path: Path) -> tuple[int, str]:
    suffix = path.suffix.lower()
    if suffix in {
        ".c", ".cc", ".cpp", ".cxx", ".rs", ".go", ".java",
        ".py", ".js", ".jsx", ".ts", ".tsx",
    }:
        bucket = 0
    elif suffix in {".h", ".hh", ".hpp"}:
        bucket = 1
    elif suffix == ".sh":
        bucket = 2
    elif suffix in {".md", ".rst", ".txt"}:
        bucket = 4
    else:
        bucket = 3
    return (bucket, path.as_posix().lower())


def _source_file_ref(
    repo_root: Path,
    workspace_id: str,
    path: Path,
    *,
    line: int,
) -> ContextReference | None:
    try:
        rel = path.relative_to(repo_root).as_posix()
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return None
    if not lines:
        return None
    start = max(1, line - 12)
    end = min(len(lines), line + 40)
    snippet = "\n".join(f"{idx}: {lines[idx - 1]}" for idx in range(start, end + 1))
    return ContextReference(
        source_type="workspace_source",
        source_id=f"{workspace_id}:{rel}:{start}-{end}",
        title=f"{rel}:{line}",
        excerpt=_clip(snippet),
        metadata={
            "workspace_id": workspace_id,
            "path": rel,
            "start_line": start,
            "end_line": end,
        },
    )


async def _workspace_refs(db: aiosqlite.Connection, workspace_id: str) -> list[ContextReference]:
    async with db.execute(
        """
        SELECT id, report_type, title, content, created_at
        FROM workspace_reports
        WHERE workspace_id = ? AND status = 'completed'
          AND content IS NOT NULL AND TRIM(content) != ''
        ORDER BY
          CASE
            WHEN lower(COALESCE(report_type, '') || ' ' || COALESCE(title, '')) LIKE '%gitnexus%' THEN 0
            WHEN lower(COALESCE(report_type, '') || ' ' || COALESCE(title, '')) LIKE '%cgc%' THEN 1
            ELSE 2
          END,
          created_at DESC
        LIMIT 6
        """,
        (workspace_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [
        ContextReference(
            source_type="workspace_report",
            source_id=str(row["id"]),
            title=str(row["title"] or row["report_type"] or "工作空间报告"),
            excerpt=_clip(str(row["content"] or "")),
            metadata={"workspace_id": workspace_id, "report_type": row["report_type"]},
        )
        for row in rows
    ]


async def _workspace_chat_refs(db: aiosqlite.Connection, workspace_id: str) -> list[ContextReference]:
    async with db.execute(
        """
        SELECT id, role, content
        FROM workspace_chats
        WHERE workspace_id = ?
        ORDER BY created_at DESC
        LIMIT 6
        """,
        (workspace_id,),
    ) as cur:
        rows = await cur.fetchall()
    if not rows:
        return []
    excerpt = "\n".join(f"{row['role']}: {_clip(row['content'], 260)}" for row in reversed(rows))
    return [
        ContextReference(
            source_type="workspace_chat_history",
            source_id=workspace_id,
            title="旧工作空间对话摘要",
            excerpt=excerpt,
            metadata={"workspace_id": workspace_id},
        )
    ]


async def _report_refs(db: aiosqlite.Connection, report_id: str) -> list[ContextReference]:
    async with db.execute(
        """
        SELECT id, workspace_id, report_type, title, content
        FROM workspace_reports
        WHERE id = ?
        """,
        (report_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return []
    return [
        ContextReference(
            source_type="workspace_report",
            source_id=str(row["id"]),
            title=str(row["title"] or row["report_type"] or "工作空间报告"),
            excerpt=_clip(str(row["content"] or "")),
            metadata={"workspace_id": row["workspace_id"], "report_type": row["report_type"]},
        )
    ]


async def _module_refs(db: aiosqlite.Connection, scope_id: str) -> list[ContextReference]:
    workspace_id, _, module = scope_id.partition(":")
    if not workspace_id or not module:
        return []
    refs = await _workspace_refs(db, workspace_id)
    for ref in refs:
        ref.metadata["module"] = module
    return refs


async def _workbench_task_refs(scope_type: str, scope_id: str) -> list[ContextReference]:
    if scope_type != "workbench_task_run":
        return []
    safe = scope_id.strip()
    if not safe or "/" in safe or "\\" in safe or ".." in safe:
        return []
    task_dir = settings.data_path / "workbench" / "task_runs" / safe
    candidates = [
        "task_run.json",
        "task_bundle.json",
        "test_activity_contract.json",
        "test_activity_quality_audit.json",
        "task_artifact_manifest.json",
        "workflow_execution.json",
        "artifact_manifest.json",
    ]
    refs: list[ContextReference] = []
    for name in candidates:
        path = task_dir / name
        if not path.exists():
            continue
        try:
            text = await _read_text(path)
        except Exception:
            continue
        refs.append(
            ContextReference(
                source_type="workbench_task_artifact",
                source_id=f"{scope_id}/{name}",
                title=name,
                excerpt=_clip(text),
                metadata={"task_run_id": scope_id, "path": name},
            )
        )
    return refs[:6]


async def _evidence_memory_refs(workspace_id: str, query: str) -> list[ContextReference]:
    try:
        from app.services.evidence_memory import EvidenceMemoryStore

        store = EvidenceMemoryStore(settings.data_path / "workbench" / "evidence_memory.db")
        items = await _to_thread(
            store.search_analysis_memory,
            query or workspace_id,
            workspace_id=workspace_id,
            limit=3,
        )
    except Exception:
        return []
    return [
        ContextReference(
            source_type="evidence_memory",
            source_id=item.evidence_id,
            title=item.subject_key or item.kind,
            excerpt=_clip(item.text or item.reason or item.path),
            metadata={"kind": item.kind, "status": item.status, "workspace_id": item.workspace_id},
        )
        for item in items
    ]


async def _semantic_case_refs(scope_id: str, query: str) -> list[ContextReference]:
    try:
        from app.services.test_semantic_library import TestSemanticLibraryStore

        store = TestSemanticLibraryStore(settings.data_path / "workbench" / "test_semantics.db")
        items = await _to_thread(store.retrieve, query=query or scope_id, limit=3)
    except Exception:
        return []
    refs: list[ContextReference] = []
    for item in items:
        excerpt = "\n".join([
            f"场景: {item.scenario}",
            f"操作: {'; '.join(item.actions)}",
            f"预期: {'; '.join(item.expected)}",
        ])
        refs.append(
            ContextReference(
                source_type="semantic_case",
                source_id=item.semantic_id,
                title=item.case_id,
                excerpt=_clip(excerpt),
                metadata={"feature": item.feature, "module": item.module, "test_level": item.test_level},
            )
        )
    return refs


async def _workbench_task_repo_path(scope_type: str, scope_id: str) -> str | None:
    if scope_type != "workbench_task_run":
        return None
    safe = scope_id.strip()
    if not safe or "/" in safe or "\\" in safe or ".." in safe:
        return None
    task_dir = settings.data_path / "workbench" / "task_runs" / safe
    for name in ("task_run.json", "task_bundle.json"):
        path = task_dir / name
        if not path.exists():
            continue
        try:
            payload = json.loads(await _read_text(path))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        repo_path = str(payload.get("repo_path") or "").strip()
        if repo_path:
            return repo_path
    return None


async def _to_thread(fn: Any, *args: Any, **kwargs: Any) -> Any:
    import asyncio

    return await asyncio.to_thread(fn, *args, **kwargs)


async def _read_text(path: Path) -> str:
    return await _to_thread(path.read_text, "utf-8", "ignore")


_AI_THREAD_AGENT_ARTIFACT_SUFFIX_PRIORITY = {
    ".md": 0,
    ".markdown": 0,
    ".txt": 1,
    ".json": 2,
    ".jsonl": 3,
}

_AI_THREAD_AGENT_AUDIT_ARTIFACT_NAMES = {
    "agent_invocation",
    "agent_replay_plan",
    "capability_manifest",
    "diagnostic",
    "diagnostics",
    "execution_input",
    "execution_result",
    "failure_retry_context",
    "raw_output",
    "stderr",
    "stdout",
    "trace",
}


def _is_agent_audit_artifact_path(path: Path) -> bool:
    parts = [path.stem.lower(), *(part.lower() for part in path.parts[:-1])]
    normalized = {re.sub(r"[^a-z0-9]+", "_", part).strip("_") for part in parts}
    return any(part in _AI_THREAD_AGENT_AUDIT_ARTIFACT_NAMES for part in normalized)


async def _agent_thread_artifact_content(artifact_dir: Path) -> str:
    if not artifact_dir.exists() or not artifact_dir.is_dir():
        return ""

    def collect_candidates() -> list[Path]:
        root = artifact_dir.resolve()
        candidates: list[Path] = []
        for path in artifact_dir.rglob("*"):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix not in _AI_THREAD_AGENT_ARTIFACT_SUFFIX_PRIORITY:
                continue
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if root not in (resolved, *resolved.parents):
                continue
            if _is_agent_audit_artifact_path(path.relative_to(artifact_dir)):
                continue
            if path.stat().st_size <= 0 or path.stat().st_size > 2_000_000:
                continue
            candidates.append(path)
        return sorted(
            candidates,
            key=lambda item: (
                _AI_THREAD_AGENT_ARTIFACT_SUFFIX_PRIORITY.get(item.suffix.lower(), 99),
                -item.stat().st_size,
                str(item.relative_to(artifact_dir)),
            ),
        )

    candidates = await _to_thread(collect_candidates)
    rendered: list[tuple[str, str]] = []
    for path in candidates:
        text = (await _read_text(path)).strip()
        if not text:
            continue
        if path.suffix.lower() == ".json":
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                pass
            else:
                text = json.dumps(parsed, ensure_ascii=False, indent=2)
        text = redact_agent_diagnostic_text(text).strip()
        if not text:
            continue
        rendered.append((str(path.relative_to(artifact_dir)), text))
    if not rendered:
        return ""
    if len(rendered) == 1:
        return rendered[0][1]
    sections = ["# Agent 输出文件包", ""]
    for relative_path, text in rendered:
        sections.extend([f"## {relative_path}", "", text.rstrip(), ""])
    return "\n".join(sections).rstrip() + "\n"


async def _prepare_assistant_delivery(
    *,
    run_id: str,
    conversation: dict[str, Any],
    content: str,
    user_message: str = "",
    force_artifact: bool = False,
    artifact_only: bool = False,
) -> tuple[str, list[dict[str, Any]]]:
    test_activity_actions = _test_activity_task_card_actions(
        conversation=conversation,
        user_message=user_message,
    )
    actions: list[dict[str, Any]] = [*test_activity_actions, *_default_actions()]
    if not force_artifact and not _should_materialize_thread_artifact(content):
        return content, actions
    artifact_path = ai_thread_artifact_path(str(conversation["id"]), run_id)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    title = str(conversation.get("title") or "AI 调查线程")
    artifact_body = "\n".join(
        [
            f"# {title}",
            "",
            f"- conversation_id: {conversation.get('id')}",
            f"- run_id: {run_id}",
            f"- exported_at: {_now()}",
            "",
            content.rstrip(),
            "",
        ]
    )
    await _to_thread(artifact_path.write_text, artifact_body, "utf-8")
    artifact_url = f"/api/ai/conversations/{conversation['id']}/runs/{run_id}/artifact"
    actions = [
        {
            "id": "download_run_artifact",
            "label": "下载完整产物",
            "href": artifact_url,
            "kind": "download",
        },
        *actions,
    ]
    visible = _compact_thread_artifact_preview(content, include_body_snippets=not artifact_only)
    suffix = (
        "完整内容已保存为下载产物。请使用“下载完整产物”获取完整文件。"
        if artifact_only
        else "完整测试设计/SFMEA/黑盒用例已保存为下载产物。请使用“下载完整产物”获取完整产物。"
    )
    return (
        f"{visible}\n\n---\n{suffix}",
        actions,
    )


def _test_activity_task_card_actions(*, conversation: dict[str, Any], user_message: str) -> list[dict[str, Any]]:
    if not _looks_like_test_activity_request(user_message):
        return []
    requested_outputs = [
        {"id": artifact.replace(".", "_"), "artifact": artifact, "type": "json" if artifact.endswith(".json") else "markdown"}
        for artifact in _requested_test_activity_outputs(user_message)
    ]
    contract = build_test_activity_contract(
        target=user_message,
        repo_path=_conversation_initial_repo_path(conversation),
        workflow_outputs=requested_outputs,
        user_requirements=user_message,
    )
    workspace_id = _conversation_workspace_id(conversation)
    workflow_query = {
            "workflow": "source_flow_sfmea_blackbox",
            "target": contract["target"],
            "outputs": ",".join(str(item) for item in contract["required_outputs"]),
    }
    if workspace_id and workspace_id != "global":
        workflow_query["workspace_id"] = workspace_id
    workflow_href = "/workbench?" + urlencode(workflow_query)
    return [
        {
            "id": "test_activity_task_card",
            "kind": "test_activity",
            "label": "测试活动任务卡",
            "target": contract["target"],
            "domain_profiles": contract["domain_profiles"],
            "recommended_outputs": contract["required_outputs"],
            "evidence_policy": contract["evidence_policy"],
            "focus_rationale": contract["focus_rationale"][:6],
            "test_activity_contract": contract,
            "artifact_contract": contract.get("artifact_contract", {}),
            "workflow_template_id": "source_flow_sfmea_blackbox",
            "workspace_id": workspace_id if workspace_id != "global" else "",
            "href": workflow_href,
            "edit_contract_href": "/workbench/designer",
        }
    ]


def sanitize_ai_thread_artifact_markdown(markdown: str) -> str | None:
    text = str(markdown or "")
    header, body = _split_ai_thread_artifact_markdown(text)
    if body is None:
        cleaned = _legacy_clean_agent_answer_content(text)
        return cleaned if cleaned != text.strip() else None
    cleaned_body = _legacy_clean_agent_answer_content(body)
    if cleaned_body == body.strip():
        return None
    return f"{header}{cleaned_body.rstrip()}\n"


def sanitize_ai_thread_artifact_file(path: Path) -> str | None:
    artifact_text = path.read_text(encoding="utf-8", errors="ignore")
    cleaned = sanitize_ai_thread_artifact_markdown(artifact_text)
    if cleaned is None:
        return None
    path.write_text(cleaned, encoding="utf-8")
    return cleaned


def _split_ai_thread_artifact_markdown(markdown: str) -> tuple[str, str | None]:
    text = str(markdown or "")
    if not text.startswith("# "):
        return "", None
    first_break = text.find("\n\n")
    if first_break < 0:
        return "", None
    body_break = text.find("\n\n", first_break + 2)
    if body_break < 0:
        return "", None
    header = text[: body_break + 2]
    body = text[body_break + 2 :]
    return header, body


def _should_materialize_thread_artifact(content: str) -> bool:
    text = str(content or "")
    lowered = text.lower()
    has_keyword = any(keyword in lowered for keyword in _THREAD_ARTIFACT_KEYWORDS)
    if not has_keyword:
        return False
    has_table_or_many_steps = (
        text.count("\n|") >= 4
        or len(re.findall(r"(?m)^\s*\d+[\.)]\s+", text)) >= 8
    )
    return len(text) > _THREAD_INLINE_OUTPUT_LIMIT * 2 or has_table_or_many_steps


def _agent_task_requests_downloadable_artifact(user_message: str, content: str) -> bool:
    requested = str(user_message or "").lower()
    output = str(content or "").lower()
    complete_markers = (
        "完整",
        "全部",
        "全量",
        "详细",
        "详尽",
        "完整的",
        "full",
        "complete",
        "comprehensive",
        "detailed",
    )
    artifact_markers = (
        "sfmea",
        "failure mode",
        "黑盒",
        "测试用例",
        "测试设计",
        "流程梳理",
        "代码分析",
        "black-box",
        "blackbox",
        "test case",
        "test design",
    )
    has_complete_intent = any(marker in requested for marker in complete_markers)
    has_artifact_intent = any(marker in requested for marker in artifact_markers)
    has_structured_output = sum(1 for marker in artifact_markers if marker in output) >= 2
    if has_complete_intent and (has_artifact_intent or has_structured_output):
        return True
    requested_groups = _agent_structured_deliverable_groups(requested)
    output_groups = _agent_structured_deliverable_groups(output)
    if {"code", "blackbox"}.issubset(requested_groups) and {"code", "blackbox"}.issubset(output_groups):
        return True
    if len(requested_groups) >= 3 and len(output_groups) >= 2:
        return True
    return {"sfmea", "blackbox"}.issubset(requested_groups) and len(output_groups) >= 2


def _requires_strict_test_activity_quality_gate(user_message: str) -> bool:
    text = str(user_message or "").lower()
    if not _looks_like_test_activity_request(text):
        return False
    strict_markers = (
        "完整",
        "全部",
        "全量",
        "详细",
        "详尽",
        "交付件",
        "交付文件",
        "输出文件",
        "可下载",
        "full",
        "complete",
        "comprehensive",
        "detailed",
        "deliverable",
        "downloadable",
    )
    return any(marker in text for marker in strict_markers)


def _agent_structured_deliverable_groups(text: str) -> set[str]:
    lowered = str(text or "").lower()
    groups: set[str] = set()
    if any(marker in lowered for marker in ("代码分析", "代码证据", "源码证据", "code evidence", "source evidence")):
        groups.add("code")
    if any(marker in lowered for marker in ("流程梳理", "调用流程", "主链路", "flow", "sequence")):
        groups.add("flow")
    if any(marker in lowered for marker in ("sfmea", "failure mode", "rpn")):
        groups.add("sfmea")
    if any(
        marker in lowered
        for marker in (
            "黑盒",
            "测试用例",
            "测试设计",
            "black-box",
            "blackbox",
            "test case",
            "test design",
        )
    ):
        groups.add("blackbox")
    return groups


def _should_compact_live_thread_delta(content: str, accumulated: str) -> bool:
    """Keep full structured artifacts out of the live reader while preserving final files."""
    return _should_materialize_thread_artifact(accumulated) or _should_materialize_thread_artifact(content)


def _compact_thread_artifact_preview(content: str, *, include_body_snippets: bool = True) -> str:
    text = str(content or "")
    title_match = re.search(r"(?m)^#{1,3}\s+(.+?)\s*$", text)
    title = title_match.group(1).strip() if title_match else "Agent 产物"
    step_count = len(re.findall(r"(?m)^\s*\d+[\.)]\s+", text))
    table_rows = max(0, text.count("\n|") - 1)
    facts = []
    if table_rows:
        facts.append(f"{table_rows} 行表格")
    if step_count:
        facts.append(f"{step_count} 条步骤/用例")
    detail = "，".join(facts) if facts else "完整产物内容"
    lines = [
        f"## {title}",
        "",
        f"已生成结构化产物（{detail}），已保存为下载产物。为避免长表格和完整用例挤占对话区，正文只展示摘要。",
    ]
    if include_body_snippets:
        summary = _artifact_preview_summary(text)
        if summary:
            lines.extend(["", "### 摘要", summary])
        evidence = _artifact_preview_evidence_lines(text)
        if evidence:
            lines.extend(["", "### 证据摘录", *evidence])
        cases = _artifact_preview_case_lines(text)
        if cases:
            lines.extend(["", "### 用例摘录", *cases])
    return "\n".join(lines)


def _artifact_preview_summary(text: str) -> str:
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("```"):
            continue
        if stripped.startswith("|"):
            continue
        if re.match(r"^-\s+(conversation_id|run_id|exported_at|repaired_from_events):", stripped):
            continue
        if "已生成结构化产物" in stripped or "下载完整产物" in stripped:
            continue
        if _looks_like_legacy_agent_process_leak(stripped):
            continue
        return _clip(stripped, 360)
    return ""


def _artifact_preview_evidence_lines(text: str, *, limit: int = 2) -> list[str]:
    evidence: list[str] = []
    lines = str(text or "").splitlines()
    evidence_section: list[str] = []
    in_evidence_section = False
    for line in lines:
        stripped = line.strip()
        if re.match(r"^#{1,4}\s+", stripped):
            heading = stripped.lstrip("#").strip().lower()
            in_evidence_section = any(
                marker in heading
                for marker in ("代码证据", "源码", "锚点", "用例设计依据", "evidence")
            )
            continue
        if in_evidence_section:
            evidence_section.append(line)
    candidate_lines = evidence_section or lines
    for line in candidate_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("```"):
            continue
        if not re.search(
            r"(?i)(?:`?(?:(?:lib|test|scripts|include)/)?[^`\s:]+\.(?:c|h|cc|cpp|py|sh|md)(?::\d+)?`?)",
            stripped,
        ):
            continue
        if _looks_like_legacy_agent_process_leak(stripped):
            continue
        item = stripped if stripped.startswith(("-", "*")) else f"- {stripped}"
        if item not in evidence:
            evidence.append(_clip(item, 220))
        if len(evidence) >= limit:
            break
    return evidence


def _artifact_preview_case_lines(text: str, *, limit: int = 2) -> list[str]:
    cases: list[str] = []
    for match in re.finditer(r"(?m)^#{2,4}\s+((?:TC|Case|用例)[-\s]?\d+[^\n]*)", str(text or ""), re.IGNORECASE):
        item = f"- {match.group(1).strip()}"
        if item not in cases:
            cases.append(_clip(item, 180))
        if len(cases) >= limit:
            break
    return cases


def _conversation_from_row(row: aiosqlite.Row) -> dict[str, Any]:
    data = dict(row)
    data["initial_context"] = _json_loads(data.pop("initial_context_json", "{}"), {})
    data["initial_context"] = _public_workbench_initial_context(
        scope_type=str(data.get("scope_type") or ""),
        scope_id=str(data.get("scope_id") or ""),
        initial_context=(
            data["initial_context"]
            if isinstance(data["initial_context"], dict)
            else {}
        ),
    )
    workspace_id = _conversation_workspace_id(data)
    if data.get("workspace_id") in {None, "", "global"} and workspace_id != "global":
        data["workspace_id"] = workspace_id
    else:
        data["workspace_id"] = str(data.get("workspace_id") or "global")
    namespace = str(data.get("memory_namespace") or "")
    if not namespace or (namespace == "global" and data["workspace_id"] != "global"):
        namespace = f"workspace:{data['workspace_id']}"
    data["memory_namespace"] = namespace or "global"
    data["runtime_type"] = str(data.get("runtime_type") or "builtin_llm")
    data["agent_runtime_id"] = data.get("agent_runtime_id") or None
    return data


def _message_from_row(row: aiosqlite.Row) -> dict[str, Any]:
    data = dict(row)
    data["references"] = _json_loads(data.pop("references_json", "[]"), [])
    data["actions"] = _json_loads(data.pop("actions_json", "[]"), [])
    return data


def _public_message_from_row(row: aiosqlite.Row) -> dict[str, Any]:
    data = _message_from_row(row)
    if data.get("role") == "assistant":
        raw_content = str(data.get("content") or "")
        governed_content = _govern_visible_assistant_content(
            raw_content,
            data.get("references") if isinstance(data.get("references"), list) else [],
        )
        data["content"] = _legacy_artifact_preview_for_message(data, governed_content, raw_content)
    return data


def _legacy_artifact_preview_for_message(
    message: dict[str, Any],
    content: str,
    raw_content: str,
) -> str:
    has_legacy_process_output = any(
        marker in str(raw_content or "") for marker in _LEGACY_AGENT_DIAGNOSTIC_MARKERS
    )
    actions = message.get("actions") if isinstance(message.get("actions"), list) else []
    has_artifact_action = any(
        isinstance(action, dict) and action.get("id") == "download_run_artifact"
        for action in actions
    )
    if not has_artifact_action:
        return content
    has_compact_notice = _is_compact_thread_artifact_notice(content)
    if (
        not has_legacy_process_output
        and not _legacy_cleaned_candidate_is_user_facing(content)
        and not has_compact_notice
    ):
        return content
    conversation_id = str(message.get("conversation_id") or "").strip()
    run_id = str(message.get("run_id") or "").strip()
    if not conversation_id or not run_id:
        return content
    path = ai_thread_artifact_path(conversation_id, run_id)
    if not path.exists() or not path.is_file():
        return content
    try:
        artifact_text = sanitize_ai_thread_artifact_file(path)
        if artifact_text is None:
            artifact_text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return content
    _header, body = _split_ai_thread_artifact_markdown(artifact_text)
    preview_source = body if body is not None else artifact_text
    if not str(preview_source or "").strip():
        return content
    artifact_only_preview = "完整内容已保存为下载产物" in content or "完整文件" in content
    if has_legacy_process_output:
        suffix = "这条历史消息的原始 Agent 过程输出已清理；请使用“下载完整产物”查看完整产物。"
    elif artifact_only_preview:
        suffix = "完整内容已保存为下载产物。请使用“下载完整产物”获取完整文件。"
    else:
        suffix = "完整测试设计/SFMEA/黑盒用例已保存为下载产物。请使用“下载完整产物”获取完整产物。"
    return (
        f"{_compact_thread_artifact_preview(preview_source, include_body_snippets=not artifact_only_preview)}"
        f"\n\n---\n{suffix}"
    )


def _is_compact_thread_artifact_notice(content: str) -> bool:
    text = str(content or "")
    return "已生成结构化产物" in text or "已保存为下载产物" in text


def _run_from_row(row: aiosqlite.Row) -> dict[str, Any]:
    data = dict(row)
    data["token_usage"] = _json_loads(data.pop("token_usage_json", "{}"), {})
    return data


def _event_from_row(row: aiosqlite.Row) -> dict[str, Any]:
    data = dict(row)
    data["payload"] = _json_loads(data.pop("payload_json", "{}"), {})
    return data


_PUBLIC_PROCESS_EVENT_KINDS = {"diagnostic", "thinking", "reasoning", "trace"}
_PUBLIC_TYPED_PROCESS_EVENT_KINDS = {
    "status",
    "error",
    "diagnostic",
    "thinking",
    "reasoning",
    "trace",
    "tool_use",
    "tool_result",
    "artifact",
}


def _public_events_from_rows(rows: list[aiosqlite.Row]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    segment_state = _AgentOutputSegmentState()
    for row in rows:
        events.extend(_public_events_from_event(_event_from_row(row), segment_state))
    return [_with_public_event_metadata(event) for event in events]


def _public_events_from_event(
    event: dict[str, Any],
    segment_state: _AgentOutputSegmentState,
) -> list[dict[str, Any]]:
    if event.get("event_type") != "delta":
        segment_state.diagnostic_active = False
        segment_state.diagnostic_prefix = ""
        segment_state.diagnostic_streaming_text = False
        segment_state.tool_answer_active = False
        return [event]
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return [event]
    kind = str(payload.get("kind") or "")
    if kind:
        if _kind_event_should_be_reclassified_as_answer(payload, segment_state):
            next_event = dict(event)
            next_payload = dict(payload)
            next_payload.pop("kind", None)
            segment_state.diagnostic_active = False
            segment_state.diagnostic_prefix = ""
            segment_state.diagnostic_streaming_text = False
            segment_state.tool_answer_active = True
            return [{**next_event, "payload": next_payload}]
        _advance_agent_segment_state_from_kind_event(payload, segment_state)
        return [event]
    content = payload.get("content")
    if not isinstance(content, str) or not content:
        return [event]
    segments = _agent_output_segments(content, state=segment_state)
    if not segments:
        return []
    public_events: list[dict[str, Any]] = []
    for segment_kind, text in segments:
        next_event = dict(event)
        next_payload = dict(payload)
        next_payload["content"] = text
        if segment_kind == "diagnostic":
            next_payload["kind"] = "diagnostic"
            segment_state.tool_answer_active = False
        public_events.append({**next_event, "payload": next_payload})
    return public_events


def _is_public_process_event(event: dict[str, Any]) -> bool:
    return _public_event_kind(event) in _PUBLIC_TYPED_PROCESS_EVENT_KINDS


def _with_public_event_metadata(event: dict[str, Any]) -> dict[str, Any]:
    next_event = dict(event)
    seq = next_event.get("seq")
    if isinstance(seq, int) and seq > 0:
        next_event["seq"] = seq
    else:
        next_event.pop("seq", None)
    event_id = next_event.get("event_id")
    if "seq" not in next_event and isinstance(event_id, int):
        next_event.setdefault("seq", event_id)
    next_event.setdefault("event_kind", _public_event_kind(next_event))
    return next_event


def _public_event_kind(event: dict[str, Any]) -> str:
    event_type = str(event.get("event_type") or "")
    if event_type == "delta":
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return "answer"
        payload_kind = str(payload.get("kind") or "")
        if payload_kind == "artifact_progress":
            return "artifact"
        if payload_kind in _PUBLIC_PROCESS_EVENT_KINDS:
            return payload_kind
        return "answer"
    if event_type in {
        "status",
        "error",
        "done",
        "answer",
        "diagnostic",
        "thinking",
        "reasoning",
        "trace",
        "tool_use",
        "tool_result",
        "artifact",
    }:
        return event_type
    return event_type or "event"


def _advance_agent_segment_state_from_kind_event(
    payload: dict[str, Any],
    segment_state: _AgentOutputSegmentState,
) -> None:
    kind = str(payload.get("kind") or "")
    content = str(payload.get("content") or "").strip()
    if kind not in _PUBLIC_PROCESS_EVENT_KINDS:
        segment_state.diagnostic_active = False
        segment_state.diagnostic_prefix = ""
        segment_state.diagnostic_streaming_text = False
        segment_state.tool_answer_active = False
        return
    if _looks_like_agent_tool_invocation_line(content):
        segment_state.diagnostic_active = True
        segment_state.diagnostic_prefix = "tool:"
        segment_state.diagnostic_streaming_text = False
        segment_state.tool_answer_active = False
        return
    prefix = _agent_diagnostic_prefix(content)
    if prefix:
        segment_state.diagnostic_active = True
        segment_state.diagnostic_prefix = prefix
        segment_state.diagnostic_streaming_text = not _agent_diagnostic_text(content)
        segment_state.tool_answer_active = False


def _kind_event_should_be_reclassified_as_answer(
    payload: dict[str, Any],
    segment_state: _AgentOutputSegmentState,
) -> bool:
    kind = str(payload.get("kind") or "")
    if kind not in _PUBLIC_PROCESS_EVENT_KINDS:
        return False
    content = str(payload.get("content") or "").strip()
    if not content:
        return False
    if _agent_diagnostic_prefix(content) or _looks_like_agent_tool_invocation_line(content):
        return False
    if _looks_like_agent_process_output_line(content) or _looks_like_agent_tool_status_line(content):
        return False
    if segment_state.tool_answer_active:
        return True
    return segment_state.diagnostic_active and segment_state.diagnostic_prefix.lower().startswith(
        ("tool:", "tool_use:", "tool_result:")
    )


def _default_actions() -> list[dict[str, Any]]:
    return [
        {"id": "save_memory", "label": "沉淀到记忆"},
        {"id": "add_test_design", "label": "加入测试设计"},
        {"id": "rerun_plan", "label": "生成复跑建议"},
    ]
