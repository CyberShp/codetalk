"""User-configured local agent runtimes."""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from app.config import settings

MANAGED_PROVIDER_PROMPT_TRANSPORTS = {"claude_print_arg", "codex_exec_json", "opencode_run_arg"}
PROMPT_TRANSPORTS = {"stdin", "argv_last", *MANAGED_PROVIDER_PROMPT_TRANSPORTS}
OUTPUT_MODES = {"plain", "ndjson", "stream_json", "auto"}
WORKING_DIR_MODES = {"project", "fixed", "none"}
COMPLETION_MODES = {"process_exit", "idle_after_output", "sentinel"}
SESSION_PERSISTENCE_MODES = {"none", "resume_args"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _clean_args(args: list[str] | None) -> list[str]:
    return [str(item) for item in (args or [])]


def _clean_env(env: dict[str, str] | None) -> dict[str, str]:
    return {str(key): str(value) for key, value in (env or {}).items() if str(key).strip()}


def _clean_resume_args(args: list[str] | None) -> list[str]:
    return [str(item) for item in (args or []) if str(item).strip()]


def validate_agent_command(command: str) -> str:
    value = str(command or "").strip()
    if not value:
        raise ValueError("执行器命令不能为空")
    if any(char.isspace() for char in value) and not Path(value).exists():
        parts = value.split()
        arg_hint = (
            f"args={json.dumps(parts[1:], ensure_ascii=False)}"
            if len(parts) > 1
            else "args=[]"
        )
        raise ValueError(
            "Agent Runtime command 只能填写可执行文件，例如 ccr、nga、python 或完整 .exe/.cmd/.bat 路径。"
            f"请把参数拆到 args，例如 command={parts[0]}，{arg_hint}。"
        )
    return value


class AgentRuntimeStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = str(db_path or settings.sqlite_db)

    async def create_runtime(self, data: dict[str, Any]) -> dict[str, Any]:
        rid = f"agent_{uuid.uuid4().hex}"
        now = _now()
        payload = self._normalize_payload(data, partial=False)
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO agent_runtimes
                    (id, name, command, args_json, prompt_transport, output_mode,
                     working_dir_mode, fixed_working_dir, env_json, health_command,
                     timeout_seconds, completion_mode, idle_complete_seconds, sentinel_text,
                     session_persistence, resume_args_json, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rid,
                    payload["name"],
                    payload["command"],
                    _json_dumps(payload["args"]),
                    payload["prompt_transport"],
                    payload["output_mode"],
                    payload["working_dir_mode"],
                    payload["fixed_working_dir"],
                    _json_dumps(payload["env"]),
                    payload["health_command"],
                    payload["timeout_seconds"],
                    payload["completion_mode"],
                    payload["idle_complete_seconds"],
                    payload["sentinel_text"],
                    payload["session_persistence"],
                    _json_dumps(payload["resume_args"]),
                    int(payload["enabled"]),
                    now,
                    now,
                ),
            )
            await db.commit()
        return await self.get_runtime(rid)

    async def list_runtimes(self, *, enabled: bool | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if enabled is not None:
            clauses.append("enabled = ?")
            params.append(1 if enabled else 0)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        async with self._connect() as db:
            async with db.execute(
                f"""
                SELECT * FROM agent_runtimes
                {where}
                ORDER BY
                    CASE id
                        WHEN 'default-claude-code' THEN 0
                        WHEN 'default-codex' THEN 1
                        WHEN 'default-opencode' THEN 2
                        ELSE 10
                    END,
                    updated_at DESC
                """,
                params,
            ) as cur:
                return [_runtime_from_row(row) for row in await cur.fetchall()]

    async def get_runtime(self, runtime_id: str) -> dict[str, Any]:
        async with self._connect() as db:
            async with db.execute("SELECT * FROM agent_runtimes WHERE id = ?", (runtime_id,)) as cur:
                row = await cur.fetchone()
        if row is None:
            raise KeyError(runtime_id)
        return _runtime_from_row(row)

    async def update_runtime(self, runtime_id: str, data: dict[str, Any]) -> dict[str, Any]:
        await self.get_runtime(runtime_id)
        payload = self._normalize_payload(data, partial=True)
        if not payload:
            return await self.get_runtime(runtime_id)
        payload["updated_at"] = _now()
        stored: dict[str, Any] = {}
        for key, value in payload.items():
            if key == "args":
                stored["args_json"] = _json_dumps(value)
            elif key == "resume_args":
                stored["resume_args_json"] = _json_dumps(value)
            elif key == "env":
                stored["env_json"] = _json_dumps(value)
            elif key == "enabled":
                stored[key] = int(bool(value))
            else:
                stored[key] = value
        set_clause = ", ".join(f"{key} = ?" for key in stored)
        async with self._connect() as db:
            await db.execute(
                f"UPDATE agent_runtimes SET {set_clause} WHERE id = ?",
                (*stored.values(), runtime_id),
            )
            await db.commit()
        return await self.get_runtime(runtime_id)

    async def delete_runtime(self, runtime_id: str) -> None:
        async with self._connect() as db:
            cur = await db.execute("DELETE FROM agent_runtimes WHERE id = ?", (runtime_id,))
            await db.commit()
        if cur.rowcount == 0:
            raise KeyError(runtime_id)

    def _normalize_payload(self, data: dict[str, Any], *, partial: bool) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in (
            "name",
            "command",
            "args",
            "prompt_transport",
            "output_mode",
            "working_dir_mode",
            "fixed_working_dir",
            "env",
            "health_command",
            "timeout_seconds",
            "completion_mode",
            "idle_complete_seconds",
            "sentinel_text",
            "session_persistence",
            "resume_args",
            "enabled",
        ):
            if key in data:
                result[key] = data[key]

        if not partial or "name" in result:
            name = str(result.get("name", "")).strip()
            if not name:
                raise ValueError("执行器名称不能为空")
            result["name"] = name
        if not partial or "command" in result:
            result["command"] = validate_agent_command(str(result.get("command", "")))

        result["args"] = _clean_args(result.get("args")) if "args" in result else ([] if not partial else result.get("args"))
        result["resume_args"] = (
            _clean_resume_args(result.get("resume_args"))
            if "resume_args" in result
            else ([] if not partial else result.get("resume_args"))
        )
        result["env"] = _clean_env(result.get("env")) if "env" in result else ({} if not partial else result.get("env"))

        if not partial or "prompt_transport" in result:
            value = str(result.get("prompt_transport") or "stdin").strip()
            if value not in PROMPT_TRANSPORTS:
                raise ValueError(f"不支持的 prompt_transport: {value}")
            result["prompt_transport"] = value
        if not partial or "output_mode" in result:
            value = str(result.get("output_mode") or "plain").strip()
            if value not in OUTPUT_MODES:
                raise ValueError(f"不支持的 output_mode: {value}")
            result["output_mode"] = value
        if not partial or "working_dir_mode" in result:
            value = str(result.get("working_dir_mode") or "project").strip()
            if value not in WORKING_DIR_MODES:
                raise ValueError(f"不支持的 working_dir_mode: {value}")
            result["working_dir_mode"] = value
        if not partial or "fixed_working_dir" in result:
            result["fixed_working_dir"] = str(result.get("fixed_working_dir") or "").strip()
        if not partial or "health_command" in result:
            result["health_command"] = str(result.get("health_command") or "").strip()
        if not partial or "timeout_seconds" in result:
            seconds = int(result.get("timeout_seconds") or 120)
            result["timeout_seconds"] = max(1, min(seconds, 3600))
        if not partial or "completion_mode" in result:
            value = str(result.get("completion_mode") or "process_exit").strip()
            if value not in COMPLETION_MODES:
                raise ValueError(f"不支持的 completion_mode: {value}")
            result["completion_mode"] = value
        if not partial or "idle_complete_seconds" in result:
            seconds = int(result.get("idle_complete_seconds") or 5)
            result["idle_complete_seconds"] = max(1, min(seconds, 300))
        if not partial or "sentinel_text" in result:
            result["sentinel_text"] = str(result.get("sentinel_text") or "").strip()
        if result.get("completion_mode") == "sentinel" and not result.get("sentinel_text"):
            raise ValueError("sentinel completion_mode 需要填写 sentinel_text")
        if not partial or "session_persistence" in result:
            value = str(result.get("session_persistence") or "none").strip()
            if value not in SESSION_PERSISTENCE_MODES:
                raise ValueError(f"不支持的 session_persistence: {value}")
            result["session_persistence"] = value
        provider_manages_resume = result.get("prompt_transport") in MANAGED_PROVIDER_PROMPT_TRANSPORTS
        if (
            result.get("session_persistence") == "resume_args"
            and not result.get("resume_args")
            and not provider_manages_resume
        ):
            raise ValueError("resume_args 会话策略需要填写 resume_args")
        if result.get("resume_args"):
            joined = "\n".join(result["resume_args"])
            if "{session_id}" not in joined and "{resume_session_id}" not in joined:
                raise ValueError("resume_args 必须包含 {session_id} 或 {resume_session_id} 占位符")
        if not partial or "enabled" in result:
            result["enabled"] = bool(result.get("enabled", True))

        return {key: value for key, value in result.items() if value is not None}

    @asynccontextmanager
    async def _connect(self):
        db = await aiosqlite.connect(self.db_path)
        db.row_factory = aiosqlite.Row
        try:
            yield db
        finally:
            await db.close()


def _runtime_from_row(row: aiosqlite.Row) -> dict[str, Any]:
    data = dict(row)
    data["args"] = _json_loads(data.pop("args_json", "[]"), [])
    data["resume_args"] = _json_loads(data.pop("resume_args_json", "[]"), [])
    data["env"] = _json_loads(data.pop("env_json", "{}"), {})
    data["completion_mode"] = data.get("completion_mode") or "process_exit"
    data["idle_complete_seconds"] = int(data.get("idle_complete_seconds") or 5)
    data["sentinel_text"] = data.get("sentinel_text") or ""
    data["session_persistence"] = data.get("session_persistence") or "none"
    data["enabled"] = bool(data.get("enabled", 1))
    return data
