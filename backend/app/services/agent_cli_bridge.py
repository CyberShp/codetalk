"""Bridge CodeTalk AI threads to user-configured local agent CLIs."""

from __future__ import annotations

import asyncio
import json
import locale
import os
import re
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
        pending = bytearray()
        while True:
            chunk = await proc.stderr.read(4096)
            if not chunk:
                break
            pending.extend(chunk)
            text = _decode_strict_if_complete(bytes(pending))
            if text is not None:
                stderr_chunks.append(text)
                pending.clear()
        if pending:
            stderr_chunks.append(_decode(bytes(pending)))

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
        pending = bytearray()
        while True:
            raw = await proc.stdout.read(4096)
            if not raw:
                break
            pending.extend(raw)
            text = _decode_strict_if_complete(bytes(pending))
            if text is not None:
                yield text
                pending.clear()
        if pending:
            yield _decode(bytes(pending))


def _parse_event_text(text: str, output_mode: str) -> str | None:
    stripped = _sse_payload_text(_clean_agent_text(text).strip())
    if not stripped:
        return ""
    try:
        event = json.loads(stripped)
    except json.JSONDecodeError:
        return None if output_mode != "plain" else stripped
    if isinstance(event, str):
        return _clean_agent_text(event)
    if not isinstance(event, dict):
        return None
    diagnostic = _diagnostic_event_text(event)
    if diagnostic is not None:
        return diagnostic
    unwrapped = _event_text(event)
    if unwrapped is not None:
        return _clean_agent_text(unwrapped)
    if _looks_like_protocol_noise(event):
        return ""
    return None


def _sse_payload_text(text: str) -> str:
    if not text.startswith("data:") and not text.startswith("event:"):
        return text
    payload_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("event:", "id:", "retry:")):
            continue
        if not stripped.startswith("data:"):
            return text
        payload = stripped.removeprefix("data:").strip()
        if payload == "[DONE]":
            continue
        payload_lines.append(payload)
    return "\n".join(payload_lines)


def _event_text(event: dict[str, Any]) -> str | None:
    for key in ("delta", "text", "content", "message"):
        value = event.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            nested = _event_text(value)
            if nested is not None:
                return nested
        if isinstance(value, list):
            parts = _content_parts(value)
            if parts:
                return "".join(parts)
    part = event.get("part")
    if isinstance(part, dict):
        value = part.get("text") or part.get("content")
        if isinstance(value, str):
            return value
    choices = event.get("choices")
    if isinstance(choices, list):
        parts: list[str] = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            for key in ("delta", "message"):
                value = choice.get(key)
                if isinstance(value, dict):
                    nested = _event_text(value)
                    if nested:
                        parts.append(nested)
            direct = choice.get("text")
            if isinstance(direct, str):
                parts.append(direct)
        if parts:
            return "".join(parts)
    candidates = event.get("candidates")
    if isinstance(candidates, list):
        parts = []
        for candidate in candidates:
            if isinstance(candidate, dict):
                nested = _event_text(candidate)
                if nested:
                    parts.append(nested)
        if parts:
            return "".join(parts)
    return None


def _content_parts(value: list[Any]) -> list[str]:
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            text = item.get("text") or item.get("content")
            if isinstance(text, str):
                parts.append(text)
    return parts


def _looks_like_protocol_noise(event: dict[str, Any]) -> bool:
    keys = set(event)
    if not keys:
        return True
    if keys <= {"id", "index", "created", "created_at", "model", "object", "type", "role", "finish_reason", "usage"}:
        return True
    event_type = str(event.get("type") or event.get("event") or "")
    return event_type in {"message_start", "message_stop", "content_block_start", "content_block_stop", "done"}


def _diagnostic_event_text(event: dict[str, Any]) -> str | None:
    event_type = str(event.get("type") or event.get("event") or event.get("kind") or "").strip().lower()
    if event_type not in {"status", "diagnostic", "thinking", "reasoning", "trace", "error"}:
        return None
    text = _event_error_text(event) if event_type == "error" else _event_text(event)
    if not text:
        return ""
    prefix = "THINKING" if event_type == "reasoning" else event_type.upper()
    return f"{prefix}: {_clean_agent_text(text)}"


def _event_error_text(event: dict[str, Any]) -> str | None:
    error = event.get("error")
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        for key in ("message", "detail", "content", "text"):
            value = error.get(key)
            if isinstance(value, str):
                return value
    return _event_text(event)


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
    text = _decode_strict_if_complete(value)
    if text is not None:
        return text
    return _clean_agent_text(value.decode("utf-8", "replace"))


def _decode_strict_if_complete(value: bytes) -> str | None:
    for encoding in _candidate_decodings():
        try:
            return _clean_agent_text(value.decode(encoding, "strict"))
        except UnicodeDecodeError:
            continue
    return None


def _candidate_decodings() -> list[str]:
    candidates = ["utf-8", "utf-8-sig"]
    preferred = locale.getpreferredencoding(False)
    if preferred:
        candidates.append(preferred)
    candidates.extend(["gb18030", "gbk"])
    deduped: list[str] = []
    for item in candidates:
        normalized = item.strip().lower()
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPINNER_PROGRESS_RE = re.compile(r"^[⠁-⣿⣀-⣿|/\\\-·•●○◐◓◑◒]\s*(?:\d+(?:[./]\d+)?%?|[.\u2026]+)?\s*$")
_PROGRESS_ONLY_RE = re.compile(r"^(?:\d{1,3}%|\d+/\d+|\d{1,4})$")


def _clean_agent_text(value: str) -> str:
    cleaned = _ANSI_RE.sub("", value)
    cleaned = _collapse_terminal_repaints(cleaned)
    return _CONTROL_RE.sub("", cleaned)


def _collapse_terminal_repaints(value: str) -> str:
    normalized = value.replace("\r\n", "\n")
    lines: list[str] = []
    for raw_line in normalized.split("\n"):
        line = raw_line.split("\r")[-1]
        stripped = line.strip()
        if (
            _SPINNER_PROGRESS_RE.match(stripped)
            or _PROGRESS_ONLY_RE.match(stripped)
            or _looks_like_replacement_gibberish(stripped)
        ):
            continue
        lines.append(line)
    return "\n".join(lines)


def _looks_like_replacement_gibberish(value: str) -> bool:
    if len(value) < 3 or "�" not in value:
        return False
    replacement_count = value.count("�")
    return replacement_count >= 3 and replacement_count / max(len(value), 1) >= 0.6


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
