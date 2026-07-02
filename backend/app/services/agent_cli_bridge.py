"""Bridge CodeTalk AI threads to user-configured local agent CLIs."""

from __future__ import annotations

import asyncio
import json
import locale
import os
import re
import shutil
import tempfile
import unicodedata
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Callable

from app.services.external_agent_discovery import redact_agent_diagnostic_text
from app.services.agent_runtimes import MANAGED_PROVIDER_PROMPT_TRANSPORTS, validate_agent_command

AGENT_FINAL_ANSWER_PREFIX = "__CODETALK_AGENT_FINAL_ANSWER__:"
AGENT_ANSWER_DELTA_PREFIX = "__CODETALK_AGENT_ANSWER_DELTA__:"


class AgentRuntimeError(RuntimeError):
    pass


async def probe_agent_runtime(runtime: dict[str, Any]) -> dict[str, Any]:
    """Run a lightweight command probe for the configured runtime."""
    command = str(runtime.get("command") or "").strip()
    try:
        command = validate_agent_command(command)
    except ValueError as exc:
        return {"success": False, "message": str(exc)}
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
        return {"success": False, "message": await _missing_command_message(command)}
    except Exception as exc:
        return {"success": False, "message": f"启动失败：{redact_agent_diagnostic_text(str(exc))}"}
    stdout_text = _decode(stdout).strip() if stdout else ""
    stderr_text = _decode(stderr).strip() if stderr else ""
    if proc.returncode == 0:
        return {"success": True, "message": stdout_text or stderr_text or "执行器可启动"}
    message = stderr_text or stdout_text or f"命令退出码：{proc.returncode}"
    return {"success": False, "message": redact_agent_diagnostic_text(message)}


async def stream_agent_runtime(
    *,
    runtime: dict[str, Any],
    prompt: str,
    cwd: str | None,
    resume_session_id: str | None = None,
    session_update: Callable[[dict[str, Any]], None] | None = None,
) -> AsyncIterator[str]:
    command = str(runtime.get("command") or "").strip()
    try:
        command = validate_agent_command(command)
    except ValueError as exc:
        raise AgentRuntimeError(str(exc)) from exc
    args = _runtime_args(runtime, resume_session_id=resume_session_id)
    prompt_transport = str(runtime.get("prompt_transport") or "stdin")
    write_prompt_to_stdin = False
    if prompt_transport == "argv_last":
        args = [*args, prompt]
        stdin = asyncio.subprocess.DEVNULL
    elif prompt_transport == "stdin":
        stdin = asyncio.subprocess.PIPE
        write_prompt_to_stdin = True
    elif prompt_transport == "claude_print_arg":
        args = _claude_print_args(args, prompt, resume_session_id=resume_session_id)
        stdin = asyncio.subprocess.DEVNULL
    elif prompt_transport == "codex_exec_json":
        args = _codex_exec_json_args(args, prompt, resume_session_id=resume_session_id)
        stdin = asyncio.subprocess.PIPE
        write_prompt_to_stdin = True
    elif prompt_transport == "opencode_run_arg":
        args = _opencode_run_args(args, prompt, resume_session_id=resume_session_id)
        stdin = asyncio.subprocess.DEVNULL
    else:
        raise AgentRuntimeError(f"不支持的 prompt_transport: {prompt_transport}")

    env = _build_env(runtime)
    prompt_file_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="codetalk-agent-prompt-",
            suffix=".md",
            delete=False,
        ) as prompt_file:
            prompt_file.write(prompt)
            prompt_file_path = prompt_file.name
        env["CODETALK_AGENT_PROMPT_FILE"] = prompt_file_path
    except Exception:
        prompt_file_path = None
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
        if prompt_file_path:
            try:
                Path(prompt_file_path).unlink(missing_ok=True)
            except Exception:
                pass
        raise AgentRuntimeError(await _missing_command_message(command)) from exc
    except Exception as exc:
        if prompt_file_path:
            try:
                Path(prompt_file_path).unlink(missing_ok=True)
            except Exception:
                pass
        raise AgentRuntimeError(f"启动执行器失败：{redact_agent_diagnostic_text(str(exc))}") from exc

    stderr_chunks: list[str] = []
    completed_by_policy = False
    activity_queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)

    def mark_activity() -> None:
        if activity_queue.full():
            return
        activity_queue.put_nowait(None)

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
                mark_activity()
                pending.clear()
        if pending:
            stderr_chunks.append(_decode(bytes(pending)))
            mark_activity()

    stderr_task = asyncio.create_task(_drain_stderr())
    try:
        if write_prompt_to_stdin and proc.stdin is not None:
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()

        async with asyncio.timeout(timeout):
            async for chunk in _read_stdout(
                proc,
                str(runtime.get("output_mode") or "plain"),
                runtime=runtime,
                session_update=session_update,
                activity_queue=activity_queue,
            ):
                if chunk:
                    yield chunk
            if proc.returncode is None:
                completed_by_policy = _completion_mode(runtime) in {"idle_after_output", "sentinel"}
                if completed_by_policy:
                    await _terminate_process(proc)
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
        if prompt_file_path:
            try:
                Path(prompt_file_path).unlink(missing_ok=True)
            except Exception:
                pass

    if return_code != 0 and not completed_by_policy:
        error = "".join(stderr_chunks).strip()
        raise AgentRuntimeError(redact_agent_diagnostic_text(error or f"执行器退出码：{return_code}"))


async def _missing_command_message(command: str) -> str:
    where_detail = ""
    if os.name == "nt":
        where = shutil.which("where.exe") or "where"
        try:
            proc = await asyncio.create_subprocess_exec(
                where,
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
            output = _decode(stdout or stderr).strip()
            where_detail = f"\nwhere {command}: {output or '未找到'}"
        except Exception:
            where_detail = f"\nwhere {command}: 未找到"
    return redact_agent_diagnostic_text(
        f"找不到命令：{command}。系统 PATH 中找不到该命令。请确认："
        "1. 命令在普通 cmd.exe/终端中可执行；"
        "2. 它不是只在 PowerShell profile 中生效的 alias；"
        "3. 如需使用 .exe/.cmd/.bat，请填写完整路径。"
        f"{where_detail}"
    )


def _completion_mode(runtime: dict[str, Any]) -> str:
    return str(runtime.get("completion_mode") or "process_exit").strip()


def _runtime_args(runtime: dict[str, Any], *, resume_session_id: str | None = None) -> list[str]:
    base_args = [str(item) for item in (runtime.get("args") or [])]
    if str(runtime.get("session_persistence") or "none") != "resume_args":
        return base_args
    if str(runtime.get("prompt_transport") or "") in MANAGED_PROVIDER_PROMPT_TRANSPORTS:
        return base_args
    session_id = str(resume_session_id or "").strip()
    if not session_id:
        return base_args
    resume_args = [str(item) for item in (runtime.get("resume_args") or [])]
    if not resume_args:
        return base_args
    return [
        item.replace("{session_id}", session_id).replace("{resume_session_id}", session_id)
        for item in resume_args
    ]


def _claude_print_args(
    base_args: list[str],
    prompt: str,
    *,
    resume_session_id: str | None = None,
) -> list[str]:
    args = list(base_args)
    args = _ensure_option_value(args, "--output-format", "stream-json", aliases=("--output-format",))
    args = _ensure_flag(args, "--include-partial-messages")
    args = _ensure_flag(args, "--verbose")
    session_id = str(resume_session_id or "").strip()
    if session_id and "--resume" not in args:
        args.extend(["--resume", session_id])
    return _insert_or_replace_prompt_value(args, prompt, flags=("-p", "--print"))


def _codex_exec_json_args(
    base_args: list[str],
    prompt: str,
    *,
    resume_session_id: str | None = None,
) -> list[str]:
    args = list(base_args)
    try:
        exec_index = args.index("exec")
    except ValueError:
        args.append("exec")
        exec_index = len(args) - 1
    session_id = str(resume_session_id or "").strip()
    if session_id and "resume" not in args[exec_index + 1 : exec_index + 3]:
        args[exec_index + 1 : exec_index + 1] = ["resume", session_id]
    args = _ensure_flag(args, "--json")
    return args


def _opencode_run_args(
    base_args: list[str],
    prompt: str,
    *,
    resume_session_id: str | None = None,
) -> list[str]:
    args = list(base_args)
    if "run" not in args:
        args.append("run")
    session_id = str(resume_session_id or "").strip()
    if session_id and "--session" not in args:
        args.extend(["--session", session_id])
    if "--format" not in args:
        args.extend(["--format", "json"])
    args.append(prompt)
    return args


def _insert_or_replace_prompt_value(args: list[str], prompt: str, *, flags: tuple[str, ...]) -> list[str]:
    result = list(args)
    for index, token in enumerate(result):
        if token not in flags:
            continue
        if index + 1 < len(result) and not result[index + 1].startswith("-"):
            result[index + 1] = prompt
        else:
            result.insert(index + 1, prompt)
        return result
    return [*result, flags[0], prompt]


def _ensure_flag(args: list[str], flag: str) -> list[str]:
    return list(args) if flag in args else [*args, flag]


def _ensure_option_value(
    args: list[str],
    option: str,
    value: str,
    *,
    aliases: tuple[str, ...],
) -> list[str]:
    result = list(args)
    for index, token in enumerate(result):
        if token not in aliases:
            continue
        if index + 1 < len(result):
            result[index + 1] = value
        else:
            result.append(value)
        return result
    return [*result, option, value]


async def _terminate_process(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=2)
    except TimeoutError:
        proc.kill()
        await proc.wait()


async def _read_stdout(
    proc: asyncio.subprocess.Process,
    output_mode: str,
    *,
    runtime: dict[str, Any] | None = None,
    session_update: Callable[[dict[str, Any]], None] | None = None,
    activity_queue: asyncio.Queue[None] | None = None,
) -> AsyncIterator[str]:
    if proc.stdout is None:
        return
    runtime = runtime or {}
    completion_mode = _completion_mode(runtime)
    idle_seconds = max(1, int(runtime.get("idle_complete_seconds") or 5))
    sentinel = str(runtime.get("sentinel_text") or "").strip()
    saw_output = False

    async def read_with_idle(read_coro_factory):
        nonlocal saw_output
        if completion_mode == "idle_after_output" and saw_output:
            read_task = asyncio.create_task(read_coro_factory())
            while True:
                wait_tasks: set[asyncio.Task[Any]] = {read_task}
                timeout_task = asyncio.create_task(asyncio.sleep(idle_seconds))
                wait_tasks.add(timeout_task)
                activity_task: asyncio.Task[Any] | None = None
                if activity_queue is not None:
                    activity_task = asyncio.create_task(activity_queue.get())
                    wait_tasks.add(activity_task)
                done, pending = await asyncio.wait(wait_tasks, return_when=asyncio.FIRST_COMPLETED)
                if read_task in done:
                    for task in pending:
                        task.cancel()
                    return read_task.result()
                if timeout_task in done:
                    read_task.cancel()
                    if activity_task is not None:
                        activity_task.cancel()
                    return None
                timeout_task.cancel()
                if activity_task is not None and activity_task in done:
                    continue
        return await read_coro_factory()

    def apply_completion_policy(parsed: str) -> tuple[str, bool]:
        if completion_mode != "sentinel" or not sentinel:
            return parsed, False
        if sentinel not in parsed:
            return parsed, False
        return parsed.replace(sentinel, ""), True

    if output_mode in {"ndjson", "stream_json", "auto"}:
        buffer = ""
        stream_state: dict[int, str] = {}
        while True:
            raw = await read_with_idle(proc.stdout.readline)
            if raw is None:
                break
            if not raw:
                if buffer.strip():
                    parsed = _parse_event_text(
                        buffer,
                        output_mode,
                        session_update=session_update,
                        stream_state=stream_state,
                    )
                    if parsed:
                        parsed, done = apply_completion_policy(parsed)
                        if parsed:
                            saw_output = True
                            yield parsed
                        if done:
                            break
                break
            text = _decode(raw)
            parsed = _parse_event_text(
                text,
                output_mode,
                session_update=session_update,
                stream_state=stream_state,
            )
            if parsed is None and output_mode == "auto":
                parsed, done = apply_completion_policy(text)
                if parsed:
                    saw_output = True
                    yield parsed
                if done:
                    break
            elif parsed:
                parsed, done = apply_completion_policy(parsed)
                if parsed:
                    saw_output = True
                    yield parsed
                if done:
                    break
    else:
        pending = bytearray()
        while True:
            raw = await read_with_idle(lambda: proc.stdout.read(4096))
            if raw is None:
                break
            if not raw:
                break
            pending.extend(raw)
            text = _decode_strict_if_complete(bytes(pending))
            if text is not None:
                text, done = apply_completion_policy(text)
                if text:
                    saw_output = True
                    yield text
                pending.clear()
                if done:
                    break
        if pending:
            text, _done = apply_completion_policy(_decode(bytes(pending)))
            if text:
                yield text


def _parse_event_text(
    text: str,
    output_mode: str,
    *,
    session_update: Callable[[dict[str, Any]], None] | None = None,
    stream_state: dict[int, str] | None = None,
) -> str | None:
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
    session = _agent_session_update(event)
    if session and session_update is not None:
        session_update(session)
    stream_block_text = _stream_content_block_event_text(event, stream_state=stream_state)
    if stream_block_text is not None:
        return stream_block_text
    diagnostic = _diagnostic_event_text(event)
    if diagnostic is not None:
        return diagnostic
    unwrapped = _event_text(event)
    if unwrapped is not None:
        return _clean_agent_text(unwrapped)
    if _looks_like_protocol_noise(event):
        return ""
    if output_mode == "auto" and _looks_like_agent_json_envelope(event):
        return ""
    return None


def _looks_like_agent_json_envelope(event: dict[str, Any]) -> bool:
    return bool(
        set(event)
        & {"type", "event", "kind", "item", "message", "role", "subtype", "session_id", "thread_id"}
    )


def _agent_session_update(event: dict[str, Any]) -> dict[str, Any] | None:
    event_type = str(event.get("type") or event.get("event") or event.get("kind") or "").strip()
    session_id = _first_event_string(
        event,
        ("session_id", "sessionId", "sessionID", "thread_id", "threadId"),
    )
    resume_session_id = _first_event_string(
        event,
        ("resume_session_id", "resumeSessionId", "next_session_id", "nextSessionId", "sessionID"),
    )
    state = event.get("state")
    if isinstance(state, dict):
        resume_session_id = resume_session_id or _first_event_string(
            state,
            ("resume_session_id", "resumeSessionId", "session_id", "sessionId"),
        )
    metadata = event.get("metadata")
    if isinstance(metadata, dict):
        resume_session_id = resume_session_id or _first_event_string(
            metadata,
            ("resume_session_id", "resumeSessionId", "session_id", "sessionId"),
        )
    if not session_id and event_type in {"thread.started", "session_init"}:
        session_id = resume_session_id
    if not resume_session_id:
        resume_session_id = session_id
    if not session_id and resume_session_id:
        session_id = resume_session_id
    if not session_id or not resume_session_id:
        return None
    return {
        "session_id": session_id,
        "resume_session_id": resume_session_id,
        "event_type": event_type or "unknown",
    }


def _first_event_string(event: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


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


def _stream_content_block_event_text(
    event: dict[str, Any],
    *,
    stream_state: dict[int, str] | None,
) -> str | None:
    stream_event = _stream_content_block_event(event)
    if stream_event is None:
        return None
    stream_type = str(stream_event.get("type") or "").strip()
    index = _stream_content_block_index(stream_event)
    if stream_type == "content_block_start":
        block = stream_event.get("content_block")
        block_type = _stream_content_block_type(block)
        if stream_state is not None:
            stream_state[index] = block_type
        return ""
    if stream_type == "content_block_stop":
        if stream_state is not None:
            stream_state.pop(index, None)
        return ""
    if stream_type != "content_block_delta":
        return None
    delta = stream_event.get("delta")
    if not isinstance(delta, dict):
        return None
    delta_type = str(delta.get("type") or "").strip()
    active_block_type = (stream_state or {}).get(index, "")
    if delta_type == "thinking_delta" and isinstance(delta.get("thinking"), str):
        return _diagnostic_lines("THINKING", str(delta["thinking"]))
    if delta_type != "text_delta" or not isinstance(delta.get("text"), str):
        return None
    text = str(delta["text"])
    if active_block_type in {"tool_use", "tool_result", "function_call", "function_result"}:
        return _diagnostic_lines("TOOL", text)
    if active_block_type in {"thinking", "reasoning", "thought", "analysis"}:
        return _diagnostic_lines("THINKING", text)
    return text


def _stream_content_block_event(event: dict[str, Any]) -> dict[str, Any] | None:
    if str(event.get("type") or "").strip() == "stream_event":
        wrapped = event.get("event")
        return wrapped if isinstance(wrapped, dict) else None
    event_type = str(event.get("type") or "").strip()
    if event_type in {"content_block_start", "content_block_delta", "content_block_stop"}:
        return event
    return None


def _stream_content_block_index(event: dict[str, Any]) -> int:
    value = event.get("index")
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _stream_content_block_type(block: Any) -> str:
    if isinstance(block, dict):
        return str(block.get("type") or block.get("kind") or "").strip().lower()
    return ""


def _event_text(event: dict[str, Any]) -> str | None:
    if str(event.get("type") or "").strip() == "result":
        value = _event_result_text(event)
        return f"{AGENT_FINAL_ANSWER_PREFIX}{value}" if value else None
    if str(event.get("type") or "").strip() == "stream_event":
        stream_event = event.get("event")
        if isinstance(stream_event, dict):
            stream_type = str(stream_event.get("type") or "").strip()
            if stream_type == "content_block_delta":
                delta = stream_event.get("delta")
                if isinstance(delta, dict):
                    delta_type = str(delta.get("type") or "").strip()
                    if delta_type == "text_delta" and isinstance(delta.get("text"), str):
                        return str(delta["text"])
                    if delta_type == "thinking_delta" and isinstance(delta.get("thinking"), str):
                        return f"THINKING: {delta['thinking']}"
            if stream_type == "content_block_stop":
                return ""
        return None
    codex_item = event.get("item")
    if isinstance(codex_item, dict):
        if str(codex_item.get("type") or "").strip() == "agent_message":
            value = codex_item.get("text") or codex_item.get("content")
            if isinstance(value, str):
                return f"{AGENT_FINAL_ANSWER_PREFIX}{value}"
            delta = (
                codex_item.get("delta")
                or codex_item.get("text_delta")
                or codex_item.get("content_delta")
            )
            return f"{AGENT_ANSWER_DELTA_PREFIX}{delta}" if isinstance(delta, str) else None
        process_text = _codex_item_process_text(codex_item)
        if process_text:
            return process_text
        return None
    if str(event.get("type") or "").strip() == "assistant":
        message = event.get("message")
        if isinstance(message, dict) and str(message.get("role") or "assistant").strip() == "assistant":
            value = message.get("content")
            if isinstance(value, str):
                return f"{AGENT_FINAL_ANSWER_PREFIX}{value}"
            if isinstance(value, list):
                parts = _content_parts(value)
                answer = "".join(parts)
                if not answer:
                    return ""
                if _only_diagnostic_parts(answer):
                    return answer
                return f"{AGENT_FINAL_ANSWER_PREFIX}{answer}"
        return None
    if str(event.get("type") or "").strip() == "message" and str(event.get("role") or "").strip() == "assistant":
        value = event.get("content")
        if isinstance(value, str):
            return f"{AGENT_FINAL_ANSWER_PREFIX}{value}"
        if isinstance(value, list):
            parts = _content_parts(value)
            return f"{AGENT_FINAL_ANSWER_PREFIX}{''.join(parts)}" if parts else ""
        return None
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
        tool_text = _opencode_part_tool_text(part)
        if tool_text:
            return tool_text
        value = part.get("text") or part.get("content")
        if isinstance(value, str):
            return value
    for key in ("data", "payload"):
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


def _event_result_text(event: dict[str, Any]) -> str:
    for key in ("result", "summary", "final", "final_answer", "output"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return _clean_agent_text(value).strip()
    return ""


def _only_diagnostic_parts(text: str) -> bool:
    lines = [line.strip().lower() for line in str(text or "").splitlines() if line.strip()]
    return bool(lines) and all(
        line.startswith(("tool:", "thinking:", "reasoning:", "trace:", "diagnostic:", "status:", "error:"))
        for line in lines
    )


def _content_parts(value: list[Any]) -> list[str]:
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            item_type = str(item.get("type") or item.get("kind") or "").strip().lower()
            text = item.get("text") or item.get("content")
            if isinstance(text, str):
                cleaned = _clean_agent_text(text)
                if not cleaned:
                    continue
                if item_type in {"thinking", "reasoning", "thought", "analysis"}:
                    parts.append(_diagnostic_lines("THINKING", cleaned) + "\n")
                elif item_type in {"tool_use", "tool_result", "function_call", "function_result"}:
                    parts.append(_diagnostic_lines("TOOL", cleaned) + "\n")
                else:
                    parts.append(cleaned)
            elif item_type in {"tool_use", "tool_result", "function_call", "function_result"}:
                tool_name = str(item.get("name") or item.get("tool") or item.get("function") or item_type).strip()
                tool_input = item.get("input") or item.get("arguments") or item.get("state")
                suffix = ""
                if isinstance(tool_input, dict) and tool_input:
                    suffix = f" {json.dumps(tool_input, ensure_ascii=False)[:300]}"
                parts.append(f"TOOL: {tool_name}{suffix}\n")
    return parts


def _opencode_part_tool_text(part: dict[str, Any]) -> str:
    part_type = str(part.get("type") or part.get("kind") or "").strip().lower()
    if part_type not in {"tool_use", "tool_result", "function_call", "function_result"}:
        return ""
    tool_name = str(part.get("tool") or part.get("name") or part.get("function") or part_type).strip()
    state = part.get("state")
    tool_input = None
    if isinstance(state, dict):
        tool_input = state.get("input") or state.get("arguments")
    tool_input = tool_input or part.get("input") or part.get("arguments")
    suffix = ""
    if isinstance(tool_input, dict) and tool_input:
        suffix = f" {json.dumps(tool_input, ensure_ascii=False)[:300]}"
    return f"{tool_name or part_type}{suffix}".strip()


def _codex_item_process_text(item: dict[str, Any]) -> str:
    item_type = str(item.get("type") or "").strip()
    if item_type == "todo_list":
        tasks = item.get("todo_items") if isinstance(item.get("todo_items"), list) else item.get("items")
        if not isinstance(tasks, list):
            return ""
        entries: list[str] = []
        for index, task in enumerate(tasks):
            if not isinstance(task, dict):
                continue
            task_id = str(task.get("id") or f"task-{index}").strip()
            subject = str(task.get("content") or task.get("text") or "").strip()
            status = str(task.get("status") or ("completed" if task.get("completed") is True else "pending")).strip()
            if subject:
                entries.append(f"{task_id}={status}: {subject[:120]}")
        if not entries:
            return ""
        return f"task_progress {'; '.join(entries)}"
    if item_type == "mcp_tool_call":
        server = str(item.get("server") or "unknown").strip()
        tool = str(item.get("tool") or "unknown").strip()
        args = item.get("arguments")
        suffix = f" {json.dumps(args, ensure_ascii=False)[:300]}" if isinstance(args, dict) and args else ""
        return f"mcp:{server}/{tool}{suffix}"
    if item_type == "command_execution":
        sections: list[str] = []
        command = str(item.get("command") or "").strip()
        if command:
            sections.append(f"command: {command}")
        status = str(item.get("status") or "completed").strip()
        sections.append(f"status: {status}")
        exit_code = item.get("exit_code")
        if isinstance(exit_code, int):
            sections.append(f"exit_code: {exit_code}")
        output = str(item.get("aggregated_output") or "").strip()
        if output:
            sections.append(output)
        return "\n".join(sections)
    if item_type == "file_change":
        changes = item.get("changes")
        change_count = len(changes) if isinstance(changes, list) else 0
        status = str(item.get("status") or "completed").strip()
        return f"file_change status={status} changes={change_count}"
    if item_type == "web_search":
        return "web_search count=1"
    if item_type == "reasoning":
        text = str(item.get("text") or "").strip()
        return f"THINKING: {text}" if text else ""
    if item_type == "error":
        message = str(item.get("message") or "").strip()
        return f"ERROR: {message}" if message else ""
    return ""


def _looks_like_protocol_noise(event: dict[str, Any]) -> bool:
    keys = set(event)
    if not keys:
        return True
    if keys <= {"id", "index", "created", "created_at", "model", "object", "type", "role", "finish_reason", "usage"}:
        return True
    event_type = str(event.get("type") or event.get("event") or "")
    if event_type == "assistant":
        message = event.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list):
                return all(
                    isinstance(item, dict)
                    and str(item.get("type") or "").strip() in {"tool_use", "tool_result", "thinking"}
                    for item in content
                )
    if event_type in {
        "message_start",
        "message_stop",
        "content_block_start",
        "content_block_stop",
        "done",
        "system",
        "thread.started",
        "turn.started",
        "turn.completed",
        "result",
    }:
        return True
    if event_type in {"item.started", "item.updated", "item.completed"}:
        item = event.get("item")
        return not (
            isinstance(item, dict)
            and str(item.get("type") or "")
            in {
                "agent_message",
                "todo_list",
                "mcp_tool_call",
                "command_execution",
                "file_change",
                "web_search",
                "reasoning",
                "error",
            }
        )
    if event_type.startswith("response.") and event_type not in {
        "response.output_text.delta",
        "response.reasoning_text.delta",
        "response.refusal.delta",
    }:
        return True
    return False


def _diagnostic_event_text(event: dict[str, Any]) -> str | None:
    event_type = str(event.get("type") or event.get("event") or event.get("kind") or "").strip().lower()
    tool_event = event_type in {"tool_use", "tool_result", "function_call", "function_result"}
    codex_item_event = event_type in {"item.started", "item.updated", "item.completed"}
    codex_item = event.get("item") if codex_item_event else None
    codex_item_type = str(codex_item.get("type") or "").strip() if isinstance(codex_item, dict) else ""
    assistant_tool_event = False
    if event_type == "assistant":
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        assistant_tool_event = isinstance(content, list) and any(
            isinstance(item, dict)
            and str(item.get("type") or "").strip() in {"tool_use", "tool_result"}
            for item in content
        )
    response_reasoning_event = event_type in {"response.reasoning_text.delta", "response.refusal.delta"}
    if (
        event_type not in {"status", "diagnostic", "thinking", "reasoning", "trace", "error"}
        and not tool_event
        and not assistant_tool_event
        and not response_reasoning_event
        and codex_item_type
        not in {
            "todo_list",
            "mcp_tool_call",
            "command_execution",
            "file_change",
            "web_search",
            "reasoning",
            "error",
        }
    ):
        return None
    text = _event_error_text(event) if event_type == "error" else _event_text(event)
    if not text:
        return ""
    if codex_item_type == "todo_list":
        prefix = "STATUS"
    elif codex_item_type == "reasoning":
        prefix = "THINKING"
    elif codex_item_type == "error":
        prefix = "ERROR"
    elif codex_item_type in {"mcp_tool_call", "command_execution", "file_change", "web_search"}:
        prefix = "TOOL"
    elif tool_event or assistant_tool_event:
        prefix = "TOOL"
    elif response_reasoning_event:
        prefix = "THINKING"
    else:
        prefix = "THINKING" if event_type == "reasoning" else event_type.upper()
    cleaned = _clean_agent_text(text).strip()
    if cleaned.lower().startswith(("tool:", "thinking:", "reasoning:", "trace:", "diagnostic:", "status:", "error:")):
        return cleaned
    return _diagnostic_lines(prefix, cleaned)


def _diagnostic_lines(prefix: str, text: str) -> str:
    cleaned = _clean_agent_text(str(text or ""))
    if not cleaned:
        return ""
    lines = cleaned.splitlines()
    if not lines:
        return f"{prefix}: {cleaned}"
    suffix = "\n" if cleaned.endswith(("\n", "\r")) else ""
    return "\n".join(f"{prefix}: {line}" if line.strip() else "" for line in lines) + suffix


def _event_error_text(event: dict[str, Any]) -> str | None:
    error = event.get("error")
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        for key in ("message", "detail", "content", "text"):
            value = error.get(key)
            if isinstance(value, str):
                return value
        data = error.get("data")
        if isinstance(data, dict):
            for key in ("message", "detail", "content", "text"):
                value = data.get(key)
                if isinstance(value, str):
                    return value
        name = error.get("name")
        if isinstance(name, str):
            return name
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
    if not env.get("CODETALK_AGENT_ARTIFACT_DIR"):
        env["CODETALK_AGENT_ARTIFACT_DIR"] = tempfile.mkdtemp(
            prefix="codetalk-agent-runtime-"
        )
    return env


def _decode(value: bytes) -> str:
    if _looks_like_short_binary_noise_bytes(value):
        return ""
    text = _decode_strict_if_complete(value)
    if text is not None:
        return text
    return _clean_agent_text(_decode_mixed_terminal_bytes(value))


def _decode_strict_if_complete(value: bytes) -> str | None:
    best_text: str | None = None
    for encoding in _candidate_decodings():
        try:
            decoded = _clean_agent_text(value.decode(encoding, "strict"))
        except UnicodeDecodeError:
            continue
        if encoding.startswith("utf-8") and _is_printable_ascii_text(value):
            return decoded
        if not _looks_like_mojibake(decoded):
            return decoded
        if best_text is None or _mojibake_score(decoded) < _mojibake_score(best_text):
            best_text = decoded
    utf16_text = _decode_utf16_if_plausible(value)
    if utf16_text is not None and (
        best_text is None or _mojibake_score(utf16_text) < _mojibake_score(best_text)
    ):
        return utf16_text
    if best_text is not None:
        return best_text
    return None


def _is_printable_ascii_text(value: bytes) -> bool:
    return all(byte in {9, 10, 13} or 32 <= byte < 127 for byte in value)


def _decode_mixed_terminal_bytes(value: bytes) -> str:
    """Decode noisy CLI output where terminal repaint noise and text use mixed encodings."""
    parts: list[str] = []
    for raw_line in value.splitlines(keepends=True):
        has_newline = raw_line.endswith((b"\n", b"\r"))
        line = raw_line.rstrip(b"\r\n")
        repaint = line.split(b"\r")[-1]
        parts.append(_decode_bytes_best_effort(repaint))
        if has_newline:
            parts.append("\n")
    return "".join(parts)


def _looks_like_short_binary_noise_bytes(value: bytes) -> bool:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if not lines:
        return False
    for line in lines:
        if not (3 <= len(line) <= 7):
            return False
        if any(byte < 0x80 for byte in line):
            return False
        if len(line) % 2 == 0:
            return False
    return True


def _decode_bytes_best_effort(value: bytes) -> str:
    for encoding in _candidate_decodings():
        try:
            return value.decode(encoding, "strict")
        except UnicodeDecodeError:
            continue
    return value.decode("utf-8", "replace")


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


def _decode_utf16_if_plausible(value: bytes) -> str | None:
    if len(value) < 4 or len(value) % 2 != 0:
        return None
    candidates: list[str] = []
    for encoding in ("utf-16", "utf-16le", "utf-16be"):
        try:
            decoded = _clean_agent_text(value.decode(encoding, "strict"))
        except (UnicodeDecodeError, UnicodeError):
            continue
        if decoded.strip():
            candidates.append(decoded)
    if not candidates:
        return None
    candidates.sort(key=_mojibake_score)
    best = candidates[0]
    return best if not _looks_like_mojibake(best) else None


def _looks_like_mojibake(value: str) -> bool:
    return _mojibake_score(value) >= 3


def _mojibake_score(value: str) -> int:
    stripped = value.strip()
    if not stripped:
        return 0
    replacement_count = stripped.count("�")
    control_count = sum(1 for char in stripped if ord(char) < 32 and char not in "\n\t")
    private_or_invalid = sum(
        1
        for char in stripped
        if unicodedata.category(char) in {"Co", "Cs", "Cn"}
    )
    suspicious_ascii = sum(1 for char in stripped if char in "{}[]~^`")
    dominant_repeat = 0
    if len(stripped) >= 20:
        most_common = max(stripped.count(char) for char in set(stripped))
        if most_common / len(stripped) > 0.45:
            dominant_repeat = 6
    return (
        (replacement_count * 3)
        + (control_count * 2)
        + (private_or_invalid * 4)
        + suspicious_ascii
        + dominant_repeat
    )


_ANSI_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07]*(?:\x07|\x1b\\)"
    r"|\x1b(?:[@-Z\\-_]|\([A-Za-z0-9]|\)[A-Za-z0-9]|\*[A-Za-z0-9]|\+[A-Za-z0-9]|[#%][A-Za-z0-9])"
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_CJK_MOJIBAKE_MARKERS = (
    "榛戠爜",
    "涓",
    "鍚",
    "骞",
    "鐨",
    "妯",
    "绋",
)
_PROGRESS_GLYPHS = r"■□▪▫⬝●○·•⠁-⣿⣀-⣿◐◓◑◒"
_SPINNER_PROGRESS_RE = re.compile(
    rf"^[\s{_PROGRESS_GLYPHS}|/\\\-]+(?:\d+(?:[./]\d+)?%?|[.\u2026]+)?\s*$"
)
_PROGRESS_GLYPH_PREFIX_RE = re.compile(rf"^[\s{_PROGRESS_GLYPHS}|/\\\-]{{4,}}")
_PROGRESS_ONLY_RE = re.compile(r"^(?:\d{1,3}%|\d+/\d+|\d{1,4})$")
_PROGRESS_STATUS_RE = re.compile(
    r"^(?:progress|loading|reading|scanning|generating|thinking|tokens?|"
    r"进度|加载中?|读取中?|扫描中?|生成中?|思考中?)"
    r"[\s:：.\-_/\\]*(?:\d{1,3}%|\d+/\d+|\d{1,6})\s*$",
    re.IGNORECASE,
)
_CLI_BANNER_RE = re.compile(
    r"^(?:"
    r"(?:claude(?:\s+code)?|codex|gemini|opencode|nga)(?:\s+(?:cli|code))?\s+v?\d"
    r"|cwd\s*:"
    r"|working directory\s*:"
    r"|session(?:\s+id)?\s*:"
    r"|thread(?:\s+id)?\s*:"
    r"|initiali[sz]ing\b"
    r"|starting\b"
    r"|thinking[.…]*$"
    r"|>\s+.+$"
    r")",
    re.IGNORECASE,
)
_TUI_BORDER_RE = re.compile(r"^[╭╮╰╯│─┌┐└┘├┤┬┴┼═║╔╗╚╝╠╣╦╩╬\s]+$")


def _clean_agent_text(value: str) -> str:
    cleaned = _ANSI_RE.sub("", value)
    cleaned = _apply_backspace_repaints(cleaned)
    cleaned = _collapse_terminal_repaints(cleaned)
    return _CONTROL_RE.sub("", cleaned)


def clean_agent_output_text(value: str) -> str:
    """Normalize terminal control noise before text is classified or displayed."""
    return _clean_agent_text(value)


def _apply_backspace_repaints(value: str) -> str:
    chars: list[str] = []
    for char in value:
        if char == "\b":
            if chars and chars[-1] not in "\n\r":
                chars.pop()
            continue
        chars.append(char)
    return "".join(chars)


def _collapse_terminal_repaints(value: str) -> str:
    normalized = value.replace("\r\n", "\n")
    lines: list[str] = []
    for raw_line in normalized.split("\n"):
        line = raw_line.split("\r")[-1]
        line = _strip_progress_glyph_prefix(line)
        stripped = line.strip()
        if (
            _SPINNER_PROGRESS_RE.match(stripped)
            or _PROGRESS_ONLY_RE.match(stripped)
            or _PROGRESS_STATUS_RE.match(stripped)
            or _looks_like_replacement_gibberish(stripped)
            or _looks_like_short_binary_gibberish(stripped)
            or _looks_like_mojibake_numeric_noise(stripped)
            or _looks_like_cli_ui_noise(stripped)
        ):
            continue
        lines.append(line)
    return "\n".join(lines)


def _strip_progress_glyph_prefix(value: str) -> str:
    return _PROGRESS_GLYPH_PREFIX_RE.sub("", value)


def _looks_like_cli_ui_noise(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if _TUI_BORDER_RE.match(stripped):
        return True
    normalized = stripped.strip("│ ").strip()
    return bool(_CLI_BANNER_RE.match(normalized))


def _looks_like_replacement_gibberish(value: str) -> bool:
    if len(value) < 3 or "�" not in value:
        return False
    replacement_count = value.count("�")
    return replacement_count >= 3 and replacement_count / max(len(value), 1) >= 0.6


def _looks_like_short_binary_gibberish(value: str) -> bool:
    if not 2 <= len(value) <= 6:
        return False
    if any(char.isascii() and char.isalnum() for char in value):
        return False
    cjk_count = sum(1 for char in value if _is_cjk(char))
    other_letter_count = sum(1 for char in value if char.isalpha() and not _is_cjk(char))
    return cjk_count > 0 and other_letter_count > 0


def _looks_like_mojibake_numeric_noise(value: str) -> bool:
    if not 4 <= len(value) <= 80:
        return False
    if not any(char.isdigit() for char in value):
        return False
    if _contains_cjk_sentence_punctuation(value):
        return False
    if _mojibake_score(value) >= 3:
        return True
    if any(marker in value for marker in _CJK_MOJIBAKE_MARKERS):
        return True
    cjk_count = sum(1 for char in value if _is_cjk(char))
    latin_letter_count = sum(1 for char in value if char.isalpha() and char.isascii())
    non_ascii_latin_count = sum(
        1
        for char in value
        if char.isalpha() and not char.isascii() and not _is_cjk(char)
    )
    digit_count = sum(1 for char in value if char.isdigit())
    if digit_count >= 3 and non_ascii_latin_count >= 2 and len(value.split()) <= 2:
        return True
    return (
        digit_count >= 3
        and cjk_count > 0
        and (latin_letter_count + non_ascii_latin_count) >= 2
        and len(value.split()) <= 2
    )


def _contains_cjk_sentence_punctuation(value: str) -> bool:
    return any(char in value for char in "，。！？；：、")


def _is_cjk(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x2A6DF
        or 0x2A700 <= codepoint <= 0x2B73F
        or 0x2B740 <= codepoint <= 0x2B81F
        or 0x2B820 <= codepoint <= 0x2CEAF
    )


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
