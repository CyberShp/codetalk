"""Bridge CodeTalk AI threads to user-configured local agent CLIs."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from app.services.external_agent_discovery import redact_agent_diagnostic_text


class AgentRuntimeError(RuntimeError):
    pass


async def probe_agent_runtime(runtime: dict[str, Any]) -> dict[str, Any]:
    """Run a lightweight command probe for the configured runtime."""
    command = str(runtime.get("command") or "").strip()
    if not command:
        return {"success": False, "message": "执行器命令为空"}
    args = list(runtime.get("args") or [])
    probe_args = _probe_args(runtime, args)
    try:
        proc = await asyncio.create_subprocess_exec(
            command,
            *probe_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_build_env(runtime),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=8)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"success": False, "message": "探测超时"}
    except FileNotFoundError:
        return {"success": False, "message": redact_agent_diagnostic_text(f"找不到命令：{command}")}
    except Exception as exc:
        return {"success": False, "message": f"启动失败：{redact_agent_diagnostic_text(str(exc))}"}
    output = _decode(stdout or stderr).strip()
    if proc.returncode == 0:
        return {"success": True, "message": output or "执行器可启动"}
    message = output or f"命令退出码：{proc.returncode}"
    return {"success": False, "message": redact_agent_diagnostic_text(message)}


async def stream_agent_runtime(
    *,
    runtime: dict[str, Any],
    prompt: str,
    cwd: str | None,
) -> AsyncIterator[str]:
    command = str(runtime.get("command") or "").strip()
    if not command:
        raise AgentRuntimeError("执行器命令为空")
    args = [str(item) for item in (runtime.get("args") or [])]
    prompt_transport = str(runtime.get("prompt_transport") or "stdin")
    if prompt_transport == "argv_last":
        args = [*args, prompt]
        stdin = asyncio.subprocess.DEVNULL
    elif prompt_transport == "stdin":
        stdin = asyncio.subprocess.PIPE
    else:
        raise AgentRuntimeError(f"不支持的 prompt_transport: {prompt_transport}")

    env = _build_env(runtime)
    timeout = int(runtime.get("timeout_seconds") or 120)
    try:
        proc = await asyncio.create_subprocess_exec(
            command,
            *args,
            cwd=cwd or None,
            stdin=stdin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError as exc:
        raise AgentRuntimeError(redact_agent_diagnostic_text(f"找不到命令：{command}")) from exc
    except Exception as exc:
        raise AgentRuntimeError(f"启动执行器失败：{redact_agent_diagnostic_text(str(exc))}") from exc

    stderr_chunks: list[str] = []

    async def _drain_stderr() -> None:
        if proc.stderr is None:
            return
        while True:
            chunk = await proc.stderr.read(4096)
            if not chunk:
                break
            stderr_chunks.append(_decode(chunk))

    stderr_task = asyncio.create_task(_drain_stderr())
    try:
        if prompt_transport == "stdin" and proc.stdin is not None:
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()

        async with asyncio.timeout(timeout):
            async for chunk in _read_stdout(proc, str(runtime.get("output_mode") or "plain")):
                if chunk:
                    yield chunk
            return_code = await proc.wait()
            await stderr_task
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        stderr_task.cancel()
        raise AgentRuntimeError(f"执行器超时（{timeout}s）") from exc
    finally:
        if not stderr_task.done():
            stderr_task.cancel()

    if return_code != 0:
        error = "".join(stderr_chunks).strip()
        raise AgentRuntimeError(redact_agent_diagnostic_text(error or f"执行器退出码：{return_code}"))


async def _read_stdout(proc: asyncio.subprocess.Process, output_mode: str) -> AsyncIterator[str]:
    if proc.stdout is None:
        return
    if output_mode in {"ndjson", "stream_json", "auto"}:
        buffer = ""
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                if buffer.strip():
                    parsed = _parse_event_text(buffer, output_mode)
                    if parsed:
                        yield parsed
                break
            text = _decode(raw)
            parsed = _parse_event_text(text, output_mode)
            if parsed is None and output_mode == "auto":
                yield text
            elif parsed:
                yield parsed
    else:
        while True:
            raw = await proc.stdout.read(4096)
            if not raw:
                break
            yield _decode(raw)


def _parse_event_text(text: str, output_mode: str) -> str | None:
    stripped = text.strip()
    if not stripped:
        return ""
    try:
        event = json.loads(stripped)
    except json.JSONDecodeError:
        return None if output_mode != "plain" else text
    if isinstance(event, str):
        return event
    if not isinstance(event, dict):
        return None
    for key in ("delta", "text", "content", "message"):
        value = event.get(key)
        if isinstance(value, str):
            return value
    part = event.get("part")
    if isinstance(part, dict):
        value = part.get("text") or part.get("content")
        if isinstance(value, str):
            return value
    return None


def _probe_args(runtime: dict[str, Any], args: list[str]) -> list[str]:
    health_command = str(runtime.get("health_command") or "").strip()
    if health_command:
        return [health_command]
    return [*args, "--version"] if args else ["--version"]


def _build_env(runtime: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    for key, value in (runtime.get("env") or {}).items():
        name = str(key).strip()
        if name:
            env[name] = str(value)
    return env


def _decode(value: bytes) -> str:
    return value.decode("utf-8", "replace")


def resolve_agent_cwd(runtime: dict[str, Any], *, repo_path: str | None) -> str | None:
    mode = str(runtime.get("working_dir_mode") or "project")
    if mode == "fixed":
        fixed = str(runtime.get("fixed_working_dir") or "").strip()
        return fixed or None
    if mode == "project":
        path = str(repo_path or "").strip()
        if path and Path(path).exists():
            return path
    return None
