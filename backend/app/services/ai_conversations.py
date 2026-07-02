"""Persistent AI investigation threads for CodeTalk."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import subprocess
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from app.config import settings
from app.services.agent_cli_bridge import clean_agent_output_text, resolve_agent_cwd, stream_agent_runtime
from app.services.external_agent_discovery import redact_agent_diagnostic_text

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

    async def list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        async with self._connect() as db:
            async with db.execute(
                """
                SELECT *
                FROM ai_messages
                WHERE conversation_id = ?
                ORDER BY created_at ASC
                """,
                (conversation_id,),
            ) as cur:
                return [_message_from_row(row) for row in await cur.fetchall()]

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
            await db.execute("BEGIN")
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
                    (id, conversation_id, status, cursor, created_at)
                VALUES (?, ?, 'queued', 0, ?)
                """,
                (run_id, conversation_id, now),
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

    async def get_message(self, message_id: str) -> dict[str, Any]:
        async with self._connect() as db:
            async with db.execute("SELECT * FROM ai_messages WHERE id = ?", (message_id,)) as cur:
                row = await cur.fetchone()
        if row is None:
            raise KeyError(message_id)
        return _message_from_row(row)

    async def get_run(self, run_id: str) -> dict[str, Any]:
        async with self._connect() as db:
            async with db.execute("SELECT * FROM ai_conversation_runs WHERE id = ?", (run_id,)) as cur:
                row = await cur.fetchone()
        if row is None:
            raise KeyError(run_id)
        return _run_from_row(row)

    async def latest_run(self, conversation_id: str) -> dict[str, Any] | None:
        async with self._connect() as db:
            async with db.execute(
                """
                SELECT *
                FROM ai_conversation_runs
                WHERE conversation_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (conversation_id,),
            ) as cur:
                row = await cur.fetchone()
        return _run_from_row(row) if row else None

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
            cur = await db.execute(
                """
                INSERT INTO ai_run_events
                    (run_id, conversation_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, conversation_id, event_type, _json_dumps(payload), now),
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
                return [_event_from_row(row) for row in await cur.fetchall()]

    async def complete_run(
        self,
        *,
        run_id: str,
        content: str,
        references: list[dict[str, Any]],
        model: str | None = None,
        token_usage: dict[str, Any] | None = None,
    ) -> None:
        run = await self.get_run(run_id)
        now = _now()
        safe_content = redact_agent_diagnostic_text(content)
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
                    _json_dumps(references),
                    _json_dumps(_default_actions()),
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

    async def cancel_run(self, conversation_id: str) -> dict[str, Any] | None:
        run = await self.latest_run(conversation_id)
        if not run or run["status"] not in {"queued", "running"}:
            return run
        now = _now()
        async with self._connect() as db:
            await db.execute(
                "UPDATE ai_conversation_runs SET status = 'cancelled', completed_at = ? WHERE id = ?",
                (now, run["id"]),
            )
            await db.execute(
                "UPDATE ai_conversations SET status = 'idle', updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            await db.commit()
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
            append_refs(await _workspace_material_refs(db, workspace_id))
            if not source_analysis_declined:
                append_refs(await _workspace_source_refs(db, workspace_id, source_query))
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
        payload={"status": "running", "message": _context_status_message(references)},
    )
    prompt = _build_prompt(conversation, messages, references, user_message["content"])
    chunks: list[str] = []
    max_tokens = min(settings.ai_conversation_max_output_tokens, settings.llm_max_output_tokens)
    temperature = 0.5

    async def append_delta(content: str) -> None:
        chunks.append(content)
        await store.append_event(
            run_id=run_id,
            conversation_id=conversation["id"],
            event_type="delta",
            payload={"content": content},
        )

    try:
        current = await store.get_run(run_id)
        if current["status"] == "cancelled":
            return
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
        content = "".join(chunks).strip() or "本轮没有生成有效内容，请换一种问法重试。"
        model = str(getattr(llm, "_model", "") or "")
        await store.complete_run(
            run_id=run_id,
            content=content,
            references=references,
            model=model or None,
        )
    except Exception as exc:
        logger.exception("AI conversation run failed: %s", exc)
        await store.fail_run(run_id, str(exc))


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
        payload={"status": "running", "message": _context_status_message(references)},
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
    prompt = _build_agent_prompt(
        conversation,
        messages,
        references,
        user_message["content"],
        runtime,
        repo_path=repo_path,
    )
    chunks: list[str] = []
    session_updates: list[dict[str, Any]] = []
    try:
        async for delta in stream_agent_runtime(
            runtime=runtime,
            prompt=prompt,
            cwd=cwd,
            resume_session_id=resume_session_id,
            session_update=session_updates.append,
        ):
            current = await store.get_run(run_id)
            if current["status"] == "cancelled":
                return
            for kind, content in _agent_output_segments(delta):
                if kind == "diagnostic":
                    await store.append_event(
                        run_id=run_id,
                        conversation_id=conversation["id"],
                        event_type="delta",
                        payload={"kind": "diagnostic", "content": content},
                    )
                    continue
                chunks.append(content)
                await store.append_event(
                    run_id=run_id,
                    conversation_id=conversation["id"],
                    event_type="delta",
                    payload={"content": content},
                )
        content = "".join(chunks).strip() or "执行器没有返回有效内容，请检查命令输出模式。"
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
        await store.complete_run(
            run_id=run_id,
            content=content,
            references=references,
            model=f"agent:{runtime.get('name') or runtime.get('id')}",
        )
    except Exception as exc:
        message = redact_agent_diagnostic_text(str(exc))
        logger.exception("AI agent runtime run failed: %s", message)
        await store.fail_run(run_id, message)


async def maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _context_status_message(references: list[dict[str, Any]]) -> str:
    source_types = {str(ref.get("source_type") or "") for ref in references}
    parts: list[str] = []
    if "workspace_source" in source_types:
        parts.append("工作区源码")
    if "workspace_material" in source_types:
        parts.append("输入材料")
    if "workspace_report" in source_types:
        parts.append("历史报告")
    if "workbench_task_artifact" in source_types:
        parts.append("任务产物")
    if "semantic_case" in source_types:
        parts.append("语义案例")
    if not parts:
        return "正在准备可用上下文；未找到直接匹配的工作区源码或输入材料。"
    return f"正在读取{'、'.join(parts)}上下文。"


def _agent_output_segments(chunk: str) -> list[tuple[str, str]]:
    text = clean_agent_output_text(str(chunk or ""))
    if not text.strip():
        return []
    segments: list[tuple[str, str]] = []
    diagnostic_buffer: list[str] = []

    def flush_diagnostic() -> None:
        nonlocal diagnostic_buffer
        if diagnostic_buffer:
            segments.append(("diagnostic", "\n".join(diagnostic_buffer)))
            diagnostic_buffer = []

    for line in text.splitlines(keepends=True):
        content = line.strip()
        if not content:
            flush_diagnostic()
            continue
        diagnostic = _agent_diagnostic_text(content)
        if diagnostic:
            flush_diagnostic()
            diagnostic_buffer.append(diagnostic)
        elif diagnostic_buffer and line[:1].isspace():
            diagnostic_buffer.append(redact_agent_diagnostic_text(content))
        else:
            flush_diagnostic()
            segments.append(("answer", line))
    flush_diagnostic()
    return segments


def _agent_diagnostic_text(text: str) -> str:
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
            return redact_agent_diagnostic_text(text[len(prefix):].strip())
    return ""


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
    llm_messages = _build_prompt(conversation, messages, references, user_message)
    lines = [
        "你正在通过 CodeTalks AI 线程作为本机 Agent 执行任务。",
        f"执行器：{runtime.get('name') or runtime.get('id')}",
        f"线程：{conversation.get('title')} ({conversation.get('id')})",
        f"项目/工作区：{conversation.get('workspace_id')}",
        f"源码工作区：{_public_workspace_label(conversation)}",
        "执行要求：CodeTalk 已把执行器工作目录切到绑定工作区；如果线程绑定 workspace，"
        "先检查当前工作目录中的源码和输入材料，再回答；不要只凭模型记忆。",
        _codex_style_answer_instruction(),
        _source_first_contract(references, user_message),
        "",
    ]
    sentinel = str(runtime.get("sentinel_text") or "").strip()
    if str(runtime.get("completion_mode") or "") == "sentinel" and sentinel:
        lines.extend([
            f"本轮回答结束后，请单独输出一行：{sentinel}",
            "不要在正文中解释这个结束标记。",
            "",
        ])
    for message in llm_messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        if role == "system":
            lines.append("系统上下文：")
        elif role == "assistant":
            lines.append("历史助手回复：")
        else:
            lines.append("用户问题：")
        lines.append(content)
        lines.append("")
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


async def _conversation_repo_path(conversation: dict[str, Any]) -> str | None:
    workspace_id = _conversation_workspace_id(conversation)
    if workspace_id == "global":
        return None
    async with aiosqlite.connect(settings.sqlite_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT repo_path FROM workspaces WHERE id = ?", (workspace_id,)) as cur:
            row = await cur.fetchone()
    if row and row["repo_path"]:
        return str(row["repo_path"])
    return None


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
) -> list[ContextReference]:
    async with db.execute("SELECT repo_path FROM workspaces WHERE id = ?", (workspace_id,)) as cur:
        row = await cur.fetchone()
    if not row or not row["repo_path"]:
        return []
    repo = Path(str(row["repo_path"])).expanduser()
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
                ref = _source_file_ref(repo_root, workspace_id, source_path, line=1)
                if ref and ref.source_id not in seen:
                    refs.append(ref)
                    seen.add(ref.source_id)
                    matched_path_hint = True
                if len(refs) >= 4:
                    return refs
            continue
        if _safe_source_file(repo_root, candidate):
            ref = _source_file_ref(repo_root, workspace_id, candidate, line=1)
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
    query_terms = _query_terms(query)
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
    matched = any(term in name_text or term in rel_text for term in query_terms)
    bucket, normalized = _source_candidate_rank(path)
    return (0 if symbol_matched else 1, 0 if matched else 1, bucket, normalized)


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
    return any(term in text for term in terms)


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
    return refs[:3]


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


async def _to_thread(fn: Any, *args: Any, **kwargs: Any) -> Any:
    import asyncio

    return await asyncio.to_thread(fn, *args, **kwargs)


async def _read_text(path: Path) -> str:
    return await _to_thread(path.read_text, "utf-8", "ignore")


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


def _run_from_row(row: aiosqlite.Row) -> dict[str, Any]:
    data = dict(row)
    data["token_usage"] = _json_loads(data.pop("token_usage_json", "{}"), {})
    return data


def _event_from_row(row: aiosqlite.Row) -> dict[str, Any]:
    data = dict(row)
    data["payload"] = _json_loads(data.pop("payload_json", "{}"), {})
    return data


def _default_actions() -> list[dict[str, str]]:
    return [
        {"id": "save_memory", "label": "沉淀到记忆"},
        {"id": "add_test_design", "label": "加入测试设计"},
        {"id": "rerun_plan", "label": "生成复跑建议"},
    ]
