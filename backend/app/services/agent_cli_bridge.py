"""Bridge CodeTalk AI threads to user-configured local agent CLIs."""

from __future__ import annotations

import asyncio
import json
import locale
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Callable

from app.config import settings
from app.services.agent_sandbox import (
    AgentSandboxError,
    cleanup_isolated_runtime_directories,
    codex_command_for_outer_sandbox,
    filtered_agent_environment,
    prepare_isolated_codex_home,
    prepare_isolated_runtime_tmp,
    prepare_agent_sandbox,
)
from app.services.network_policy import agent_network_is_permitted, scrub_intranet_agent_environment
from app.services.external_agent_discovery import redact_agent_diagnostic_text
from app.services.agent_runtimes import MANAGED_PROVIDER_PROMPT_TRANSPORTS, validate_agent_command

AGENT_FINAL_ANSWER_PREFIX = "__CODETALK_AGENT_FINAL_ANSWER__:"
AGENT_ANSWER_DELTA_PREFIX = "__CODETALK_AGENT_ANSWER_DELTA__:"
CHAT_TOOL_CALL_STATE_KEY = "__codetalk_chat_tool_calls__"
AGENT_STREAM_RECORD_LIMIT = 16 * 1024 * 1024
MAX_AGENT_ARG_PROMPT_BYTES = 24_000


class AgentRuntimeError(RuntimeError):
    pass


async def probe_agent_runtime(runtime: dict[str, Any]) -> dict[str, Any]:
    """Run a lightweight command probe for the configured runtime."""
    command = str(runtime.get("command") or "").strip()
    try:
        command = validate_agent_command(command)
    except ValueError as exc:
        return {"success": False, "message": str(exc)}
    command = _resolve_agent_command(command)
    args = list(runtime.get("args") or [])
    probe_args = _probe_args(runtime, args)
    env, owned_artifact_dir = _build_env_with_artifact_ownership(
        runtime,
        include_claude_auth=False,
    )
    probe_temp_dir = Path(env["CODETALK_AGENT_ARTIFACT_DIR"]).resolve()
    for temp_name in ("CODETALK_TEMP_DIR", "TMPDIR", "TMP", "TEMP"):
        env[temp_name] = str(probe_temp_dir)
    isolate_process_group = os.name != "nt"
    process_kwargs: dict[str, Any] = {}
    if isolate_process_group:
        process_kwargs["start_new_session"] = True
    proc: asyncio.subprocess.Process | None = None
    try:
        try:
            proc = await asyncio.create_subprocess_exec(
                command,
                *probe_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                **process_kwargs,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=8)
            except asyncio.TimeoutError:
                return {"success": False, "message": "探测超时"}
            except asyncio.CancelledError:
                raise
        except FileNotFoundError:
            return {"success": False, "message": await _missing_command_message(command)}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return {"success": False, "message": f"启动失败：{redact_agent_diagnostic_text(str(exc))}"}
        stdout_text = _decode(stdout).strip() if stdout else ""
        stderr_text = _decode(stderr).strip() if stderr else ""
        if proc.returncode == 0:
            network_block = managed_agent_network_block_message(runtime)
            if network_block:
                return {"success": False, "message": network_block}
            if str(runtime.get("prompt_transport") or "") == "claude_print_arg":
                auth_result = await _probe_claude_auth_in_runtime_sandbox(
                    runtime=runtime,
                    command=command,
                )
                if not auth_result["success"]:
                    return auth_result
            if str(runtime.get("prompt_transport") or "") == "codex_exec_json":
                readiness_result = await _probe_codex_model_in_runtime_sandbox(
                    runtime=runtime,
                    command=command,
                )
                if not readiness_result["success"]:
                    return readiness_result
                return readiness_result
            return {"success": True, "message": stdout_text or stderr_text or "执行器可启动"}
        message = stderr_text or stdout_text or f"命令退出码：{proc.returncode}"
        return {"success": False, "message": redact_agent_diagnostic_text(message)}
    finally:
        if proc is not None:
            await _terminate_process(proc, process_group=isolate_process_group)
        _cleanup_owned_artifact_dir(owned_artifact_dir)


async def _probe_claude_auth_in_runtime_sandbox(
    *, runtime: dict[str, Any], command: str
) -> dict[str, Any]:
    failure = {
        "success": False,
        "message": "Claude Code 可启动，但隔离环境无法读取登录状态，请重新登录或检查 Agent 隔离配置。",
    }
    try:
        with tempfile.TemporaryDirectory(
            prefix="codetalk-claude-probe-",
            dir=settings.ensure_runtime_temp_path(),
        ) as temp_dir:
            artifact_dir = Path(temp_dir).resolve()
            env = _build_env(runtime, artifact_dir_override=artifact_dir)
            for temp_name in ("CODETALK_TEMP_DIR", "TMPDIR", "TMP", "TEMP"):
                env[temp_name] = str(artifact_dir)
            sandbox_runtime = {
                **runtime,
                "sandbox_mode": runtime.get("sandbox_mode") or settings.external_agent_sandbox_mode,
                "sandbox_allow_network": runtime.get(
                    "sandbox_allow_network",
                    agent_network_is_permitted(),
                ),
                "intranet_require_os_sandbox": settings.intranet_network_mode,
                "sandbox_write_paths": runtime.get(
                    "sandbox_write_paths",
                    settings.external_agent_sandbox_write_paths,
                ),
                "sandbox_command": command,
                "sandbox_read_paths": _command_runtime_read_paths(command),
            }
            sandbox = prepare_agent_sandbox(
                runtime=sandbox_runtime,
                cwd=str(artifact_dir),
                artifact_dir=artifact_dir,
            )
            readiness_args = _claude_print_args(
                _runtime_args(runtime, resume_session_id=None),
                "仅回复 CODETALK_PROBE_OK",
                resume_session_id=None,
            )
            readiness_args = _ensure_option_value(
                readiness_args,
                "--max-turns",
                "1",
                aliases=("--max-turns",),
            )
            readiness_command = [command, *readiness_args]
            if sandbox.wrapper:
                readiness_command = [*sandbox.wrapper, *readiness_command]
            isolate_process_group = os.name != "nt"
            process_kwargs: dict[str, Any] = {}
            if isolate_process_group:
                process_kwargs["start_new_session"] = True
            readiness: asyncio.subprocess.Process | None = None
            try:
                readiness = await asyncio.create_subprocess_exec(
                    *readiness_command,
                    cwd=str(artifact_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                    **process_kwargs,
                )
                try:
                    readiness_stdout, readiness_stderr = await asyncio.wait_for(
                        readiness.communicate(),
                        timeout=settings.external_agent_startup_probe_timeout_sec,
                    )
                except asyncio.TimeoutError:
                    return {
                        "success": False,
                        "message": "Claude Code 已登录，但真实模型请求探测超时。请检查网络或代理后重试。",
                    }
                except asyncio.CancelledError:
                    raise
            finally:
                if readiness is not None:
                    await _terminate_process(
                        readiness,
                        process_group=isolate_process_group,
                    )
            readiness_text = _decode(readiness_stdout or readiness_stderr).strip()
            return _claude_readiness_result(
                readiness_text,
                returncode=int(readiness.returncode or 0),
            )
    except (AgentSandboxError, FileNotFoundError, OSError):
        return failure


async def _probe_codex_model_in_runtime_sandbox(
    *, runtime: dict[str, Any], command: str
) -> dict[str, Any]:
    """Verify that Codex can make a real request with the configured model."""
    failure = {
        "success": False,
        "message": "Codex 可启动，但真实模型请求失败。请检查登录状态、模型配置或网络。",
    }
    try:
        with tempfile.TemporaryDirectory(
            prefix="codetalk-codex-probe-",
            dir=settings.ensure_runtime_temp_path(),
        ) as temp_dir:
            artifact_dir = Path(temp_dir).resolve()
            env = _build_env(
                runtime,
                include_claude_auth=False,
                artifact_dir_override=artifact_dir,
            )
            for temp_name in ("CODETALK_TEMP_DIR", "TMPDIR", "TMP", "TEMP"):
                env[temp_name] = str(artifact_dir)
            codex_runtime_home, codex_runtime_read_targets = prepare_isolated_codex_home(
                provider=str(runtime.get("name") or runtime.get("id") or ""),
                command=[command, *_runtime_args(runtime, resume_session_id=None)],
                artifact_dir=artifact_dir,
            )
            if codex_runtime_home is not None:
                env["CODEX_HOME"] = str(codex_runtime_home)
            sandbox_runtime = {
                **runtime,
                "sandbox_mode": runtime.get("sandbox_mode") or settings.external_agent_sandbox_mode,
                "sandbox_allow_network": runtime.get(
                    "sandbox_allow_network",
                    agent_network_is_permitted(),
                ),
                "intranet_require_os_sandbox": settings.intranet_network_mode,
                "sandbox_write_paths": runtime.get(
                    "sandbox_write_paths",
                    settings.external_agent_sandbox_write_paths,
                ),
                "sandbox_command": command,
                "sandbox_codex_home": str(codex_runtime_home) if codex_runtime_home else "",
                "sandbox_read_paths": [
                    *list(runtime.get("sandbox_read_paths") or []),
                    *_command_runtime_read_paths(command),
                    *[str(path) for path in codex_runtime_read_targets],
                    *([str(Path(command).parent)] if Path(command).parent != Path(".") else []),
                ],
            }
            sandbox = prepare_agent_sandbox(
                runtime=sandbox_runtime,
                cwd=str(artifact_dir),
                artifact_dir=artifact_dir,
            )
            readiness_args = _codex_exec_json_args(
                _runtime_args(runtime, resume_session_id=None),
                "Reply exactly CODETALK_PROBE_OK",
                resume_session_id=None,
            )
            readiness_args = _ensure_flag(readiness_args, "--ignore-user-config")
            readiness_args = _ensure_flag(readiness_args, "--ignore-rules")
            # The readiness probe intentionally runs in an isolated empty
            # directory. Codex otherwise exits before making its minimal model
            # request, while an actual workflow runs inside a Git workspace.
            readiness_args = _ensure_flag(readiness_args, "--skip-git-repo-check")
            readiness_command = [command, *readiness_args]
            if sandbox.wrapper:
                readiness_command = [*sandbox.wrapper, *readiness_command]
            isolate_process_group = os.name != "nt"
            process_kwargs: dict[str, Any] = {}
            if isolate_process_group:
                process_kwargs["start_new_session"] = True
            readiness: asyncio.subprocess.Process | None = None
            try:
                readiness = await asyncio.create_subprocess_exec(
                    *readiness_command,
                    cwd=str(artifact_dir),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                    **process_kwargs,
                )
                if readiness.stdin is not None:
                    readiness.stdin.write(b"Reply exactly CODETALK_PROBE_OK")
                    await readiness.stdin.drain()
                    readiness.stdin.close()
                try:
                    readiness_stdout, readiness_stderr = await asyncio.wait_for(
                        readiness.communicate(),
                        timeout=settings.external_agent_startup_probe_timeout_sec,
                    )
                except asyncio.TimeoutError:
                    return {
                        "success": False,
                        "message": "Codex 可启动，但真实模型请求探测超时。请检查网络或代理后重试。",
                    }
                except asyncio.CancelledError:
                    raise
            finally:
                if readiness is not None:
                    await _terminate_process(
                        readiness,
                        process_group=isolate_process_group,
                    )
            readiness_text = "\n".join(
                part for part in (_decode(readiness_stdout), _decode(readiness_stderr)) if part
            ).strip()
            return _codex_readiness_result(
                readiness_text,
                returncode=int(readiness.returncode or 0),
            )
    except (AgentSandboxError, FileNotFoundError, OSError):
        return failure


def _claude_readiness_result(text: str, *, returncode: int) -> dict[str, Any]:
    payloads: list[dict[str, Any]] = []
    for line in str(text or "").splitlines() or [str(text or "")]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    result_payload = next(
        (payload for payload in reversed(payloads) if payload.get("type") == "result"),
        payloads[-1] if payloads else {},
    )
    is_error = bool(result_payload.get("is_error")) if result_payload else True
    status = result_payload.get("api_error_status") if result_payload else None
    lower = str(text or "").lower()
    if "not inside a trusted directory" in lower or "skip-git-repo-check" in lower:
        return {
            "success": False,
            "message": "Codex 探测目录未被识别为 Git 工作区。请检查 Codex 启动参数或隔离配置。",
        }
    if status == 403 or "api error: 403" in lower or "request not allowed" in lower:
        return {
            "success": False,
            "message": "Claude Code 已登录，但真实模型请求被拒绝（HTTP 403）。请重新登录并检查账号或代理权限。",
        }
    if returncode != 0 or is_error:
        return {
            "success": False,
            "message": "Claude Code 已登录，但真实模型请求失败。请检查账号、网络或代理配置。",
        }
    result_text = str(result_payload.get("result") or "")
    if "CODETALK_PROBE_OK" not in result_text:
        return {
            "success": False,
            "message": "Claude Code 已响应，但未返回预期确认标记。请检查命令参数和输出格式。",
        }
    return {"success": True, "message": "Claude Code 已登录，真实模型请求可用"}


def _codex_readiness_result(text: str, *, returncode: int) -> dict[str, Any]:
    payloads: list[dict[str, Any]] = []
    for line in str(text or "").splitlines() or [str(text or "")]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    lower = str(text or "").lower()
    if "requires a newer version of codex" in lower or "model metadata" in lower:
        return {
            "success": False,
            "message": "Codex 可启动，但当前模型不受本机 CLI 支持。请升级 Codex CLI 或改用受支持模型。",
        }
    if "status\\\":401" in lower or "status\\\":403" in lower or "authentication" in lower:
        return {
            "success": False,
            "message": "Codex 可启动，但真实模型请求未获授权。请重新登录并检查账号或代理权限。",
        }
    completed = any(payload.get("type") == "turn.completed" for payload in payloads)
    answer_text = "\n".join(
        str((payload.get("item") or {}).get("text") or "")
        for payload in payloads
        if isinstance(payload.get("item"), dict)
    )
    if returncode != 0 or not completed:
        return {
            "success": False,
            "message": "Codex 可启动，但真实模型请求失败。请检查登录状态、模型配置或网络。",
        }
    if "CODETALK_PROBE_OK" not in answer_text:
        return {
            "success": False,
            "message": "Codex 已响应，但未返回预期确认标记。请检查模型或运行参数。",
        }
    return {"success": True, "message": "Codex 已登录，真实模型请求可用"}


def managed_agent_network_block_message(runtime: dict[str, Any]) -> str:
    """Return a shared readiness failure before a managed Agent can egress."""
    prompt_transport = str(runtime.get("prompt_transport") or "").strip()
    provider = str(runtime.get("provider") or "").strip().lower()
    command_name = Path(str(runtime.get("command") or "")).name.lower()
    managed_command_names = {
        "codex": {"codex", "codex.exe"},
        "claude": {"claude", "claude.exe", "ccr", "ccr.cmd"},
        "opencode": {"opencode", "opencode.cmd", "opencode.exe"},
        "nga": {"nga", "nga.cmd", "nga.exe"},
    }
    if (
        prompt_transport not in MANAGED_PROVIDER_PROMPT_TRANSPORTS
        or provider not in {"codex", "claude", "opencode", "nga"}
        or command_name not in managed_command_names[provider]
        or not settings.intranet_network_mode
        or agent_network_is_permitted()
    ):
        return ""
    return (
        "内网策略未批准 Agent 访问模型端点：当前部署没有可审计的 Agent 出口网关。"
        "请使用内置模型的已批准 Provider Adapter，或由部署管理员配置受控出口并完成流量捕获验收后重试。"
    )


async def stream_agent_runtime(
    *,
    runtime: dict[str, Any],
    prompt: str,
    cwd: str | None,
    resume_session_id: str | None = None,
    session_update: Callable[[dict[str, Any]], None] | None = None,
    stderr_update: Callable[[str], Any] | None = None,
    is_cancelled: Callable[[], Any] | None = None,
) -> AsyncIterator[str]:
    command = str(runtime.get("command") or "").strip()
    try:
        command = validate_agent_command(command)
    except ValueError as exc:
        raise AgentRuntimeError(str(exc)) from exc
    command = _resolve_agent_command(command)
    configured_runtime_args = [
        str(item)
        for item in [
            *list(runtime.get("args") or []),
            *list(runtime.get("resume_args") or []),
        ]
    ]
    args = _runtime_args(runtime, resume_session_id=resume_session_id)
    prompt_transport = str(runtime.get("prompt_transport") or "stdin")
    network_block = managed_agent_network_block_message(runtime)
    if network_block:
        raise AgentRuntimeError(network_block)
    env, owned_artifact_dir = _build_env_with_artifact_ownership(runtime)
    artifact_dir = Path(env["CODETALK_AGENT_ARTIFACT_DIR"]).expanduser().resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    runtime_tmp_dir = prepare_isolated_runtime_tmp(artifact_dir)
    for temp_name in ("CODETALK_TEMP_DIR", "TMPDIR", "TMP", "TEMP"):
        env[temp_name] = str(runtime_tmp_dir)
    env["TMPPREFIX"] = str(runtime_tmp_dir / "zsh")
    prompt_file_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="codetalk-agent-prompt-",
            suffix=".md",
            dir=str(runtime_tmp_dir),
            delete=False,
        ) as prompt_file:
            prompt_file.write(prompt)
            prompt_file_path = prompt_file.name
        env["CODETALK_AGENT_PROMPT_FILE"] = prompt_file_path
    except Exception:
        prompt_file_path = None
    prompt_argument = _prompt_argument_or_file_bootstrap(
        prompt,
        prompt_file_path=prompt_file_path,
    )
    write_prompt_to_stdin = False
    if prompt_transport == "argv_last":
        args = [*args, prompt_argument]
        stdin = asyncio.subprocess.DEVNULL
    elif prompt_transport == "stdin":
        stdin = asyncio.subprocess.PIPE
        write_prompt_to_stdin = True
    elif prompt_transport == "claude_print_arg":
        args = _claude_print_args(args, prompt_argument, resume_session_id=resume_session_id)
        stdin = asyncio.subprocess.DEVNULL
    elif prompt_transport == "codex_exec_json":
        args = _codex_add_writable_artifact_dir(args, runtime, command=command)
        args = _codex_exec_json_args(args, prompt, resume_session_id=resume_session_id)
        stdin = asyncio.subprocess.PIPE
        write_prompt_to_stdin = True
    elif prompt_transport == "opencode_run_arg":
        args = _opencode_run_args(args, prompt_argument, resume_session_id=resume_session_id)
        stdin = asyncio.subprocess.DEVNULL
    else:
        cleanup_isolated_runtime_directories(artifact_dir)
        _cleanup_owned_artifact_dir(owned_artifact_dir)
        raise AgentRuntimeError(f"不支持的 prompt_transport: {prompt_transport}")
    timeout = int(runtime.get("timeout_seconds") or 120)
    hard_timeout = max(3600, timeout * 4)
    isolate_process_group = os.name != "nt"
    process_kwargs: dict[str, Any] = {}
    if isolate_process_group:
        process_kwargs["start_new_session"] = True
    sandbox_runtime = {
        **runtime,
        "sandbox_mode": runtime.get("sandbox_mode") or settings.external_agent_sandbox_mode,
        "sandbox_allow_network": runtime.get(
            "sandbox_allow_network",
            agent_network_is_permitted(),
        ),
        "intranet_require_os_sandbox": settings.intranet_network_mode,
        "sandbox_write_paths": runtime.get(
            "sandbox_write_paths",
            settings.external_agent_sandbox_write_paths,
        ),
        "sandbox_command": command,
        "sandbox_read_paths": [
            *list(runtime.get("sandbox_read_paths") or []),
            *_command_runtime_read_paths(command),
            *_configured_runtime_read_paths(configured_runtime_args),
            *([prompt_file_path] if prompt_file_path else []),
        ],
    }
    codex_runtime_home, codex_runtime_read_targets = prepare_isolated_codex_home(
        provider=str(runtime.get("name") or runtime.get("id") or ""),
        command=[command, *args],
        artifact_dir=artifact_dir,
    )
    if codex_runtime_home is not None:
        env["CODEX_HOME"] = str(codex_runtime_home)
        sandbox_runtime["sandbox_codex_home"] = str(codex_runtime_home)
        sandbox_runtime["sandbox_read_paths"] = [
            *sandbox_runtime["sandbox_read_paths"],
            *[str(path) for path in codex_runtime_read_targets],
            str(Path(command).parent),
        ]
    try:
        sandbox = prepare_agent_sandbox(
            runtime=sandbox_runtime,
            cwd=cwd,
            artifact_dir=artifact_dir,
        )
    except AgentSandboxError as exc:
        if prompt_file_path:
            Path(prompt_file_path).unlink(missing_ok=True)
        cleanup_isolated_runtime_directories(artifact_dir)
        _cleanup_owned_artifact_dir(owned_artifact_dir)
        raise AgentRuntimeError(str(exc)) from exc
    if stderr_update is not None:
        update_result = stderr_update(f"Agent 隔离：{sandbox.message}")
        if asyncio.iscoroutine(update_result):
            await update_result
    process_command = codex_command_for_outer_sandbox(
        [command, *args],
        sandbox_active=sandbox.status == "active",
    )
    command, args = process_command[0], process_command[1:]
    if sandbox.wrapper:
        command, args = sandbox.wrapper[0], [*sandbox.wrapper[1:], command, *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            command,
            *args,
            cwd=cwd or None,
            stdin=stdin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            **process_kwargs,
        )
    except FileNotFoundError as exc:
        if prompt_file_path:
            try:
                Path(prompt_file_path).unlink(missing_ok=True)
            except Exception:
                pass
        cleanup_isolated_runtime_directories(artifact_dir)
        _cleanup_owned_artifact_dir(owned_artifact_dir)
        raise AgentRuntimeError(await _missing_command_message(command)) from exc
    except Exception as exc:
        if prompt_file_path:
            try:
                Path(prompt_file_path).unlink(missing_ok=True)
            except Exception:
                pass
        cleanup_isolated_runtime_directories(artifact_dir)
        _cleanup_owned_artifact_dir(owned_artifact_dir)
        raise AgentRuntimeError(f"启动执行器失败：{redact_agent_diagnostic_text(str(exc))}") from exc

    stderr_chunks: list[str] = []
    completed_by_policy = False
    cancelled_by_request = False
    saw_stdout_output = False
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
                await _emit_stderr_updates(text, stderr_update)
                if _stderr_reports_productive_activity(text):
                    mark_activity()
                pending.clear()
        if pending:
            text = _decode(bytes(pending))
            stderr_chunks.append(text)
            await _emit_stderr_updates(text, stderr_update)
            if _stderr_reports_productive_activity(text):
                mark_activity()

    stderr_task = asyncio.create_task(_drain_stderr())
    cancel_task: asyncio.Task[None] | None = None
    if is_cancelled is not None:

        async def _watch_cancel() -> None:
            nonlocal cancelled_by_request
            while proc.returncode is None:
                try:
                    result = is_cancelled()
                    if asyncio.iscoroutine(result):
                        result = await result
                    if result:
                        cancelled_by_request = True
                        await _terminate_process(proc, process_group=isolate_process_group)
                        return
                except Exception:
                    return
                await asyncio.sleep(0.1)

        cancel_task = asyncio.create_task(_watch_cancel())
    try:
        if write_prompt_to_stdin and proc.stdin is not None:
            proc.stdin.write(prompt.encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()

        stream_completion: dict[str, Any] = {"completed": False, "error": ""}
        async with asyncio.timeout(hard_timeout):
            async for chunk in _read_stdout(
                proc,
                str(runtime.get("output_mode") or "plain"),
                runtime=runtime,
                session_update=session_update,
                activity_queue=activity_queue,
                completion_state=stream_completion,
            ):
                if chunk:
                    if _looks_like_unattended_permission_request(chunk):
                        raise AgentRuntimeError(
                            "外部 Agent 请求交互式文件写入权限，CodeTalk 已中止本轮。"
                            "请让 Agent 输出最终 Markdown，由 CodeTalk 生成下载产物；不要写入源码工作区。"
                        )
                    saw_stdout_output = True
                    yield chunk
            if proc.returncode is None:
                completion_mode = _completion_mode(runtime)
                completed_by_policy = (
                    stream_completion["completed"]
                    or completion_mode == "sentinel"
                    or (completion_mode == "idle_after_output" and saw_stdout_output)
                )
                if completed_by_policy:
                    await _terminate_process(proc, process_group=isolate_process_group)
            return_code = await proc.wait()
            await stderr_task
    except TimeoutError as exc:
        await _terminate_process(proc, process_group=isolate_process_group)
        stderr_task.cancel()
        raise AgentRuntimeError(f"执行器超过安全运行上限（{hard_timeout}s）") from exc
    finally:
        await _terminate_process(proc, process_group=isolate_process_group)
        if not stderr_task.done():
            stderr_task.cancel()
        if cancel_task is not None and not cancel_task.done():
            cancel_task.cancel()
        if prompt_file_path:
            try:
                Path(prompt_file_path).unlink(missing_ok=True)
            except Exception:
                pass
        cleanup_isolated_runtime_directories(artifact_dir)
        _cleanup_owned_artifact_dir(owned_artifact_dir)

    if (
        return_code == 1
        and prompt_transport == "codex_exec_json"
        and saw_stdout_output
        and not completed_by_policy
        and not cancelled_by_request
    ):
        return

    if return_code != 0 and not completed_by_policy and not cancelled_by_request:
        error = "".join(stderr_chunks).strip()
        structured_error = str(stream_completion.get("error") or "").strip()
        raise AgentRuntimeError(
            redact_agent_diagnostic_text(structured_error or error or f"执行器退出码：{return_code}")
        )


async def _emit_stderr_updates(text: str, callback: Callable[[str], Any] | None) -> None:
    if callback is None:
        return
    for line in _stderr_progress_lines(text):
        result = callback(line)
        if asyncio.iscoroutine(result):
            await result


def _stderr_progress_lines(text: str) -> list[str]:
    cleaned = clean_agent_output_text(text)
    lines: list[str] = []
    for raw in cleaned.splitlines():
        line = redact_agent_diagnostic_text(raw).strip()
        if not line or _looks_like_agent_initialization_noise(line):
            continue
        lines.append(_public_agent_progress_line(line))
    return lines[-20:]


def _stderr_reports_productive_activity(text: str) -> bool:
    lines = _stderr_progress_lines(text)
    return any("正在自动重试" not in line for line in lines)


def _looks_like_agent_initialization_noise(value: str) -> bool:
    lowered = str(value or "").lower()
    return (
        (
            "codex_core_plugins::manifest" in lowered
            and "ignoring interface.defaultprompt" in lowered
        )
        or (
            "codex_models_manager" in lowered
            and "operation not permitted" in lowered
            and ("models cache" in lowered or "cache ttl" in lowered)
        )
        or (
            "codex_core_skills::loader" in lowered
            and "failed to read skills symlink dir" in lowered
            and "operation not permitted" in lowered
        )
    )


def _public_agent_progress_line(value: str) -> str:
    reconnect = re.search(r"Reconnecting\.\.\.\s*(\d+/\d+)", value, re.IGNORECASE)
    if reconnect is None:
        reconnect = re.search(r"retrying sampling request\s*\((\d+/\d+)", value, re.IGNORECASE)
    if reconnect is not None:
        return f"Agent 连接中断，正在自动重试（{reconnect.group(1)}）。"
    return value


def _resolve_agent_command(command: str, *, platform_name: str | None = None) -> str:
    """Resolve Windows npm/cmd shims before asyncio hands off to CreateProcess."""
    value = str(command or "").strip()
    if (platform_name or os.name) != "nt":
        return value
    path = Path(value)
    if path.is_absolute() or path.parent != Path("."):
        return value
    resolved = shutil.which(value)
    return resolved or value


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


def _configured_runtime_read_paths(args: list[str]) -> list[str]:
    """Allow trusted runtime wrapper/config arguments through the read-only sandbox."""

    paths: list[str] = []
    for raw_arg in args:
        value = str(raw_arg or "").strip()
        candidates = [value]
        if value.startswith("@"):
            candidates.append(value[1:])
        if value.startswith("-") and "=" in value:
            candidates.append(value.split("=", 1)[1])
        for candidate in candidates:
            path = Path(candidate).expanduser()
            if not path.is_absolute():
                continue
            try:
                if path.exists():
                    resolved = str(path.resolve())
                    if resolved not in paths:
                        paths.append(resolved)
            except (OSError, RuntimeError):
                continue
    return paths


def _command_runtime_read_paths(command: str) -> list[str]:
    """Expose an explicitly configured virtualenv runtime read-only.

    macOS sandbox-exec can launch ``.venv/bin/python`` while still denying the
    interpreter's sibling ``pyvenv.cfg``. The configured command is trusted,
    so expose only its owning virtualenv when that marker is present.
    """

    try:
        executable = Path(command).expanduser()
        if not executable.is_absolute():
            executable = Path(os.path.abspath(executable))
        if not executable.exists():
            return []
    except (OSError, RuntimeError):
        return []
    for parent in (executable.parent, executable.parent.parent):
        if (parent / "pyvenv.cfg").is_file():
            return [str(parent)]
    return []


def _prompt_argument_or_file_bootstrap(prompt: str, *, prompt_file_path: str | None) -> str:
    if len(str(prompt or "").encode("utf-8")) <= MAX_AGENT_ARG_PROMPT_BYTES:
        return prompt
    if not prompt_file_path:
        raise AgentRuntimeError(
            "Agent 输入超过命令行安全上限，但临时任务文件创建失败。请检查临时目录权限后重试。"
        )
    return (
        "CodeTalk 的完整用户任务过长，已写入环境变量 CODETALK_AGENT_PROMPT_FILE 指向的 UTF-8 文件。"
        "必须先完整读取该文件，并把文件全部内容作为本轮唯一用户任务执行；不要只回复这条引导。"
    )


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


def _codex_add_writable_artifact_dir(
    base_args: list[str],
    runtime: dict[str, Any],
    *,
    command: str = "",
) -> list[str]:
    args = list(base_args)
    command_name = Path(str(command or "")).stem.lower()
    if command_name != "codex" and not runtime.get("supports_add_dir"):
        return args
    runtime_env = runtime.get("env") if isinstance(runtime.get("env"), dict) else {}
    artifact_dir = str(runtime_env.get("CODETALK_AGENT_ARTIFACT_DIR") or "").strip()
    if not artifact_dir:
        return args
    for index, item in enumerate(args[:-1]):
        if item == "--add-dir" and args[index + 1] == artifact_dir:
            return args
    try:
        exec_index = args.index("exec")
    except ValueError:
        exec_index = 0
    args[exec_index:exec_index] = ["--add-dir", artifact_dir]
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


async def _terminate_process(
    proc: asyncio.subprocess.Process,
    *,
    process_group: bool = False,
) -> None:
    if process_group and os.name != "nt":
        process_group_id = int(getattr(proc, "pid", 0) or 0)
        if process_group_id <= 0:
            return
        if proc.returncode is not None and not _process_group_exists(process_group_id):
            return
        try:
            os.killpg(process_group_id, signal.SIGTERM)
        except ProcessLookupError:
            if proc.returncode is None:
                await proc.wait()
            return

        deadline = asyncio.get_running_loop().time() + 2
        if proc.returncode is None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(proc.wait()),
                    timeout=max(0.001, deadline - asyncio.get_running_loop().time()),
                )
            except TimeoutError:
                pass
        while (
            _process_group_exists(process_group_id)
            and asyncio.get_running_loop().time() < deadline
        ):
            await asyncio.sleep(0.02)
        if _process_group_exists(process_group_id):
            try:
                os.killpg(process_group_id, signal.SIGKILL)
            except OSError:
                pass
        if proc.returncode is None:
            await proc.wait()
        return

    if proc.returncode is not None:
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=2)
    except TimeoutError:
        proc.kill()
        await proc.wait()


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _read_stdout(
    proc: asyncio.subprocess.Process,
    output_mode: str,
    *,
    runtime: dict[str, Any] | None = None,
    session_update: Callable[[dict[str, Any]], None] | None = None,
    activity_queue: asyncio.Queue[None] | None = None,
    completion_state: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    if proc.stdout is None:
        return
    runtime = runtime or {}
    completion_mode = _completion_mode(runtime)
    idle_seconds = max(1, int(runtime.get("idle_complete_seconds") or 5))
    activity_timeout_seconds = max(1, int(runtime.get("timeout_seconds") or 120))
    sentinel = str(runtime.get("sentinel_text") or "").strip()
    saw_output = False

    async def read_with_idle(read_coro_factory):
        nonlocal saw_output
        complete_on_idle = completion_mode == "idle_after_output" and saw_output
        wait_seconds = idle_seconds if complete_on_idle else activity_timeout_seconds
        read_task = asyncio.create_task(read_coro_factory())
        while True:
            wait_tasks: set[asyncio.Task[Any]] = {read_task}
            timeout_task = asyncio.create_task(asyncio.sleep(wait_seconds))
            wait_tasks.add(timeout_task)
            activity_task: asyncio.Task[Any] | None = None
            if activity_queue is not None:
                activity_task = asyncio.create_task(activity_queue.get())
                wait_tasks.add(activity_task)
            try:
                done, pending = await asyncio.wait(
                    wait_tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except BaseException:
                for task in wait_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*wait_tasks, return_exceptions=True)
                raise
            if read_task in done:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                return read_task.result()
            if timeout_task in done:
                read_task.cancel()
                if activity_task is not None:
                    activity_task.cancel()
                await asyncio.gather(
                    read_task,
                    *(task for task in (activity_task,) if task is not None),
                    return_exceptions=True,
                )
                if complete_on_idle:
                    return None
                raise AgentRuntimeError(f"执行器连续 {activity_timeout_seconds}s 没有输出或进度")
            timeout_task.cancel()
            await asyncio.gather(timeout_task, return_exceptions=True)
            if activity_task is not None and activity_task in done:
                continue

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
            raw = await read_with_idle(lambda: _read_agent_stream_record(proc.stdout))
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
            structured_error = _structured_agent_error_text(text)
            if structured_error and completion_state is not None:
                completion_state["error"] = structured_error
            stream_completed = _managed_stream_completed(text, runtime)
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
            if stream_completed:
                if completion_state is not None:
                    completion_state["completed"] = True
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


def _managed_stream_completed(text: str, runtime: dict[str, Any]) -> bool:
    if str(runtime.get("prompt_transport") or "").strip() != "codex_exec_json":
        return False
    stripped = _sse_payload_text(_clean_agent_text(text).strip())
    if not stripped:
        return False
    try:
        event = json.loads(stripped)
    except json.JSONDecodeError:
        return False
    return isinstance(event, dict) and str(event.get("type") or "").strip() == "turn.completed"


def _structured_agent_error_text(text: str) -> str:
    stripped = _sse_payload_text(_clean_agent_text(text).strip())
    if not stripped:
        return ""
    try:
        event = json.loads(stripped)
    except json.JSONDecodeError:
        return ""
    if not isinstance(event, dict):
        return ""
    message = event.get("message")
    nested_error = str(message.get("error") or "") if isinstance(message, dict) else ""
    is_error = bool(event.get("is_error")) or bool(nested_error)
    status = str(event.get("api_error_status") or "").strip()
    result = str(event.get("result") or "").strip()
    combined = " ".join(part for part in (nested_error, status, result) if part).lower()
    if not is_error:
        return ""
    if "authentication" in combined or "authenticate" in combined or status in {"401", "403"}:
        status_label = f"（HTTP {status}）" if status else ""
        return f"执行器认证失败{status_label}。请重新登录该执行器或在设置中配置有效凭据后重试。"
    if status:
        return f"执行器请求失败（HTTP {status}）。请检查执行器配置和服务权限后重试。"
    return redact_agent_diagnostic_text(result or nested_error or "执行器返回结构化错误。")


async def _read_agent_stream_record(
    reader: asyncio.StreamReader,
    *,
    max_bytes: int = AGENT_STREAM_RECORD_LIMIT,
) -> bytes:
    record = bytearray()
    while True:
        try:
            chunk = await reader.readuntil(b"\n")
        except asyncio.LimitOverrunError as exc:
            chunk = await reader.read(max(1, exc.consumed))
        except asyncio.IncompleteReadError as exc:
            chunk = exc.partial
            if chunk:
                record.extend(chunk)
            if len(record) > max_bytes:
                raise AgentRuntimeError("执行器单条过程事件超过安全上限，请减少单次工具输出后重试。")
            return bytes(record)
        record.extend(chunk)
        if len(record) > max_bytes:
            raise AgentRuntimeError("执行器单条过程事件超过安全上限，请减少单次工具输出后重试。")
        if chunk.endswith(b"\n"):
            return bytes(record)


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
    unwrapped = _event_text(event, stream_state=stream_state)
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


def _event_text(event: dict[str, Any], *, stream_state: dict[Any, Any] | None = None) -> str | None:
    output_text_done = _response_output_text_done(event)
    if output_text_done is not None:
        return output_text_done
    response_text = _response_completed_text(event)
    if response_text is not None:
        return response_text
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
        item_type = str(codex_item.get("type") or "").strip()
        if str(event.get("type") or "").strip() == "item.updated" and item_type == "command_execution":
            return None
        if item_type == "agent_message":
            value = codex_item.get("text") or codex_item.get("content")
            if isinstance(value, str):
                return f"{AGENT_FINAL_ANSWER_PREFIX}{value}"
            delta = (
                codex_item.get("delta")
                or codex_item.get("text_delta")
                or codex_item.get("content_delta")
            )
            return f"{AGENT_ANSWER_DELTA_PREFIX}{delta}" if isinstance(delta, str) else None
        if item_type in {"message", "output_message"} and str(codex_item.get("role") or "assistant").strip() == "assistant":
            value = codex_item.get("content")
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
    chat_tool_text = _chat_tool_call_text(event, stream_state=stream_state)
    if chat_tool_text:
        return _diagnostic_lines("TOOL", chat_tool_text)
    for key in ("delta", "text", "content", "message"):
        value = event.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            nested = _event_text(value, stream_state=stream_state)
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
            nested = _event_text(value, stream_state=stream_state)
            if nested is not None:
                return nested
        if isinstance(value, list):
            parts = _content_parts(value)
            if parts:
                return "".join(parts)
    choices = event.get("choices")
    if isinstance(choices, list):
        parts: list[str] = []
        saw_choice_protocol = False
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            saw_choice_protocol = True
            for key in ("delta", "message"):
                value = choice.get(key)
                if isinstance(value, dict):
                    parts.extend(_chat_choice_payload_parts(value, final=key == "message", stream_state=stream_state))
            direct = choice.get("text")
            if isinstance(direct, str):
                parts.append(_chat_choice_answer_text(direct, final=False))
        if parts:
            return "".join(parts)
        if saw_choice_protocol:
            return ""
    candidates = event.get("candidates")
    if isinstance(candidates, list):
        parts = []
        for candidate in candidates:
            if isinstance(candidate, dict):
                nested = _event_text(candidate, stream_state=stream_state)
                if nested:
                    parts.append(nested)
        if parts:
            return "".join(parts)
    return None


def _response_output_text_done(event: dict[str, Any]) -> str | None:
    if str(event.get("type") or "").strip() != "response.output_text.done":
        return None
    text = event.get("text") or event.get("content") or event.get("output_text")
    if not isinstance(text, str):
        return None
    cleaned = _clean_agent_text(text).strip()
    if not cleaned:
        return ""
    return f"{AGENT_FINAL_ANSWER_PREFIX}{cleaned}"


def _response_completed_text(event: dict[str, Any]) -> str | None:
    if str(event.get("type") or "").strip() != "response.completed":
        return None
    response = event.get("response")
    if not isinstance(response, dict):
        return None
    output = response.get("output")
    if not isinstance(output, list):
        return None
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip()
        role = str(item.get("role") or "assistant").strip()
        if item_type not in {"message", "output_message"} or role != "assistant":
            continue
        content = item.get("content")
        if isinstance(content, str):
            parts.append(_clean_agent_text(content))
        elif isinstance(content, list):
            parts.extend(_content_parts(content))
    answer = "".join(part for part in parts if part)
    if not answer:
        return None
    if _only_diagnostic_parts(answer):
        return answer
    return f"{AGENT_FINAL_ANSWER_PREFIX}{answer}"


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


def _chat_tool_call_text(event: dict[str, Any], *, stream_state: dict[Any, Any] | None = None) -> str:
    entries: list[str] = []
    function_call = event.get("function_call")
    if isinstance(function_call, dict):
        text = _chat_streamed_tool_call_text(function_call, stream_state=stream_state, key="function_call")
        if text:
            entries.append(text)
    tool_calls = event.get("tool_calls")
    if isinstance(tool_calls, list):
        for index, item in enumerate(tool_calls):
            if not isinstance(item, dict):
                continue
            function = item.get("function")
            key = str(item.get("index") if item.get("index") is not None else item.get("id") or index)
            text = _chat_streamed_tool_call_text(
                function if isinstance(function, dict) else item,
                stream_state=stream_state,
                key=key,
            )
            if text:
                entries.append(text)
    return "\n".join(entries)


def _chat_choice_payload_parts(
    value: dict[str, Any],
    *,
    final: bool,
    stream_state: dict[Any, Any] | None = None,
) -> list[str]:
    parts: list[str] = []
    has_tool_call = _chat_payload_has_tool_call(value)
    tool_text = _chat_tool_call_text(value, stream_state=stream_state)
    answer_text = _chat_choice_payload_answer_text(value)
    if tool_text:
        parts.append(_diagnostic_lines("TOOL", tool_text))
        if answer_text:
            parts.append("\n")
    if answer_text:
        parts.append(_chat_choice_answer_text(answer_text, final=final))
    if parts:
        return parts
    if has_tool_call:
        return []
    nested = _event_text(value, stream_state=stream_state)
    return [nested] if nested else []


def _chat_payload_has_tool_call(value: dict[str, Any]) -> bool:
    return isinstance(value.get("function_call"), dict) or isinstance(value.get("tool_calls"), list)


def _chat_streamed_tool_call_text(
    value: dict[str, Any],
    *,
    stream_state: dict[Any, Any] | None,
    key: str,
) -> str:
    arguments = value.get("arguments")
    if not isinstance(arguments, str):
        arguments = value.get("input")
    if not isinstance(arguments, str) and stream_state is None:
        return _function_call_text(value)
    state = stream_state.setdefault(CHAT_TOOL_CALL_STATE_KEY, {})
    if not isinstance(state, dict):
        state = {}
        stream_state[CHAT_TOOL_CALL_STATE_KEY] = state
    call_state = state.setdefault(str(key), {"name": "", "arguments": ""})
    if not isinstance(call_state, dict):
        call_state = {"name": "", "arguments": ""}
        state[str(key)] = call_state
    name = str(value.get("name") or value.get("tool") or value.get("function") or "").strip()
    if name:
        call_state["name"] = name
    if not isinstance(arguments, str):
        return ""
    call_state["arguments"] = str(call_state.get("arguments") or "") + arguments
    completed = _completed_chat_tool_call_text(call_state)
    if completed:
        state.pop(str(key), None)
    return completed


def _completed_chat_tool_call_text(call_state: dict[str, Any]) -> str:
    arguments = str(call_state.get("arguments") or "")
    if not arguments.strip():
        return ""
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return ""
    name = str(call_state.get("name") or "function_call").strip() or "function_call"
    if isinstance(parsed, dict):
        suffix = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))[:300]
    else:
        suffix = arguments.strip()[:300]
    return f"{name} {suffix}".strip()


def _chat_choice_payload_answer_text(value: dict[str, Any]) -> str:
    content = value.get("content")
    if isinstance(content, str) and content:
        return content
    if isinstance(content, list):
        parts = _content_parts(content)
        if parts:
            return "".join(parts)
    text = value.get("text")
    return text if isinstance(text, str) and text else ""


def _chat_choice_answer_text(value: str, *, final: bool) -> str:
    if value.startswith((AGENT_FINAL_ANSWER_PREFIX, AGENT_ANSWER_DELTA_PREFIX)):
        return value
    if _only_diagnostic_parts(value):
        return value
    prefix = AGENT_FINAL_ANSWER_PREFIX if final else AGENT_ANSWER_DELTA_PREFIX
    return f"{prefix}{value}"


def _function_call_text(value: dict[str, Any]) -> str:
    name = str(value.get("name") or value.get("tool") or value.get("function") or "function_call").strip()
    arguments = value.get("arguments") or value.get("input")
    suffix = ""
    if isinstance(arguments, str) and arguments.strip():
        suffix = f" {arguments.strip()[:300]}"
    elif isinstance(arguments, dict) and arguments:
        suffix = f" {json.dumps(arguments, ensure_ascii=False)[:300]}"
    return f"{name or 'function_call'}{suffix}".strip()


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
            sections.append(_compact_command_output(output))
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


def _compact_command_output(output: str, *, edge_lines: int = 3) -> str:
    """Keep process diagnostics useful without streaming entire command transcripts."""
    cleaned = str(output or "").strip()
    lines = cleaned.splitlines()
    if len(lines) <= edge_lines * 2 and len(cleaned) <= 1_200:
        return cleaned
    if len(lines) <= edge_lines * 2:
        return f"output: {len(cleaned)} chars; preview: {cleaned[:900]}..."
    omitted = len(lines) - (edge_lines * 2)
    return "\n".join(
        [
            *lines[:edge_lines],
            f"... {omitted} lines omitted ...",
            *lines[-edge_lines:],
        ]
    )


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
    if event_type == "item.updated" and codex_item_type == "command_execution":
        return None
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
    status_event = event_type in {"status", "log", "progress", "info", "debug", "warning", "warn"}
    if (
        event_type not in {"diagnostic", "thinking", "reasoning", "trace", "error"}
        and not status_event
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
    elif status_event:
        prefix = "STATUS"
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


def _build_env(
    runtime: dict[str, Any],
    *,
    include_claude_auth: bool = True,
    artifact_dir_override: str | Path | None = None,
) -> dict[str, str]:
    env, _owned_artifact_dir = _build_env_with_artifact_ownership(
        runtime,
        include_claude_auth=include_claude_auth,
        artifact_dir_override=artifact_dir_override,
        create_artifact_dir=artifact_dir_override is not None,
    )
    return env


def _build_env_with_artifact_ownership(
    runtime: dict[str, Any],
    *,
    include_claude_auth: bool = True,
    artifact_dir_override: str | Path | None = None,
    create_artifact_dir: bool = True,
) -> tuple[dict[str, str], Path | None]:
    credential_runtime = include_claude_auth and _is_trusted_managed_claude_runtime(runtime)
    env = filtered_agent_environment(
        {} if credential_runtime else runtime.get("env") or {}
    )
    if credential_runtime:
        _inject_claude_oauth_token(runtime, env)
    owned_artifact_dir: Path | None = None
    if artifact_dir_override is not None:
        artifact_dir = Path(artifact_dir_override).expanduser().resolve()
        artifact_dir.mkdir(parents=True, exist_ok=True)
        env["CODETALK_AGENT_ARTIFACT_DIR"] = str(artifact_dir)
    elif create_artifact_dir and not env.get("CODETALK_AGENT_ARTIFACT_DIR"):
        runtime_temp_dir = settings.ensure_runtime_temp_path()
        owned_artifact_dir = Path(
            tempfile.mkdtemp(
                prefix="codetalk-agent-runtime-",
                dir=runtime_temp_dir,
            )
        )
        env["CODETALK_AGENT_ARTIFACT_DIR"] = str(owned_artifact_dir)
    if settings.intranet_network_mode:
        env = scrub_intranet_agent_environment(env)
    return env, owned_artifact_dir


def _cleanup_owned_artifact_dir(artifact_dir: Path | None) -> None:
    if artifact_dir is None:
        return
    shutil.rmtree(artifact_dir, ignore_errors=True)


def _inject_claude_oauth_token(runtime: dict[str, Any], env: dict[str, str]) -> None:
    if not _is_trusted_managed_claude_runtime(runtime) or env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return
    security = shutil.which("security")
    if not security:
        return
    try:
        result = subprocess.run(
            [security, "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if result.returncode != 0:
            return
        payload = json.loads(result.stdout)
        oauth = payload.get("claudeAiOauth") if isinstance(payload, dict) else None
        token = str((oauth or {}).get("accessToken") or "").strip()
        if token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return


def _is_trusted_managed_claude_runtime(runtime: dict[str, Any]) -> bool:
    if (
        sys.platform != "darwin"
        or str(runtime.get("id") or "") != "default-claude-code"
        or str(runtime.get("provider") or "") != "claude"
        or str(runtime.get("prompt_transport") or "") != "claude_print_arg"
        or list(runtime.get("args") or [])
        or list(runtime.get("resume_args") or [])
    ):
        return False
    configured_command = str(runtime.get("command") or "").strip()
    trusted_command = shutil.which("claude")
    if not configured_command or not trusted_command:
        return False
    try:
        configured_path = Path(configured_command).expanduser()
        resolved_configured = (
            str(configured_path)
            if configured_path.is_absolute()
            else shutil.which(configured_command)
        )
        return bool(
            resolved_configured
            and Path(resolved_configured).resolve() == Path(trusted_command).resolve()
        )
    except (OSError, RuntimeError):
        return False


def _decode(value: bytes) -> str:
    if _looks_like_short_binary_noise_bytes(value):
        return ""
    text = _decode_strict_if_complete(value)
    if text is not None:
        return text
    return _clean_agent_text(_decode_mixed_terminal_bytes(value))


def _decode_strict_if_complete(value: bytes) -> str | None:
    # A partial UTF-8 code point can also be valid GB18030. Do not fall back to
    # a legacy encoding until the UTF-8 sequence is complete, otherwise stream
    # chunk boundaries turn valid Chinese output into mojibake.
    try:
        value.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        if exc.reason == "unexpected end of data" and exc.end == len(value):
            return None
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
    r"\x1b[PX^_][^\x1b\x9c]*(?:\x1b\\|\x9c)"
    r"|\x9d[^\x07\x9c]*(?:\x07|\x9c)"
    r"|\x90[^\x9c]*(?:\x9c)"
    r"|\x98[^\x9c]*(?:\x9c)"
    r"|\x9e[^\x9c]*(?:\x9c)"
    r"|\x9f[^\x9c]*(?:\x9c)"
    r"|\x1b\[[0-?]*[ -/]*[@-~]"
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
    r"|usage\s*:\s*(?:claude(?:\s+code)?|codex|gemini|opencode|nga)\b"
    r"|cwd\s*:"
    r"|working directory\s*:"
    r"|session(?:\s+id)?\s*:"
    r"|thread(?:\s+id)?\s*:"
    r"|model\s*:\s*(?:claude|codex|gemini|opencode|nga)\b"
    r"|context\s*:\s*(?:/|[A-Za-z]:[\\/]|~)"
    r"|welcome\s+to\s+(?:claude(?:\s+code)?|codex|gemini|opencode|nga)\b"
    r"|ready(?:\s+for\b|\s*$)"
    r"|tips?\s*:"
    r"|press\s+(?:ctrl|cmd|command|enter|esc)\b"
    r"|session\s+ready\b"
    r"|initiali[sz]ing\b"
    r"|starting\b"
    r"|thinking[.…]*$"
    r"|>\s+.+$"
    r")",
    re.IGNORECASE,
)
_TUI_BORDER_RE = re.compile(r"^[╭╮╰╯│─┌┐└┘├┤┬┴┼═║╔╗╚╝╠╣╦╩╬\s]+$")
_CLI_HELP_START_RE = re.compile(
    r"^usage\s*:\s*(?:claude(?:\s+code)?|codex|gemini|opencode|nga)\b",
    re.IGNORECASE,
)
_CLI_HELP_CONTINUATION_RE = re.compile(
    r"^(?:"
    r"(?:options?|commands?|arguments?|flags?)\s*:?\s*$"
    r"|(?:-{1,2}|/)[A-Za-z?][\w?.-]*(?:\s|=|,|$)"
    r"|(?:model|context|cwd|working directory|provider|profile|session(?:\s+id)?|thread(?:\s+id)?)\s*:"
    r")",
    re.IGNORECASE,
)


def _clean_agent_text(value: str) -> str:
    cleaned = _strip_incomplete_terminal_escape_suffix(value)
    cleaned = _ANSI_RE.sub("", cleaned)
    cleaned = _apply_backspace_repaints(cleaned)
    cleaned = _collapse_terminal_repaints(cleaned)
    cleaned = _CONTROL_RE.sub("", cleaned)
    return _repair_cjk_mojibake_fragments(cleaned)


def clean_agent_output_text(value: str) -> str:
    """Normalize terminal control noise before text is classified or displayed."""
    return _clean_agent_text(value)


def _looks_like_unattended_permission_request(value: str) -> bool:
    text = _clean_agent_text(str(value or "")).lower()
    return (
        "requested permissions" in text
        and ("haven't granted" in text or "not granted" in text)
    ) or (
        "permission" in text
        and "write" in text
        and "grant" in text
        and "not" in text
    )


_CJK_MOJIBAKE_STARTERS = frozenset(
    "锛銆灏瘯鐧诲綍绛栫暐寤虹珛鏃朵粠鎺ュ湪繛缁ф壙坄歚"
)


def _repair_cjk_mojibake_fragments(value: str) -> str:
    """Repair local UTF-8-as-GBK fragments without re-encoding normal Chinese text."""
    if not value or not any(char in value for char in _CJK_MOJIBAKE_STARTERS):
        return value
    parts: list[str] = []
    index = 0
    max_fragment = 240
    while index < len(value):
        char = value[index]
        if char not in _CJK_MOJIBAKE_STARTERS:
            parts.append(char)
            index += 1
            continue

        best_end = index + 1
        best_text = char
        limit = min(len(value), index + max_fragment)
        for end in range(index + 1, limit + 1):
            segment = value[index:end]
            if "\n" in segment or "\r" in segment:
                break
            repaired = _decode_gb18030_wrapped_utf8(segment)
            if repaired is None:
                continue
            repaired = repaired.replace("\\n", "\n")
            if not any(_is_cjk(item) for item in repaired):
                continue
            if _cjk_mojibake_marker_count(repaired) >= _cjk_mojibake_marker_count(segment):
                continue
            best_end = end
            best_text = repaired
        parts.append(best_text)
        index = best_end
    return "".join(parts)


def _decode_gb18030_wrapped_utf8(value: str) -> str | None:
    try:
        return value.encode("gb18030").decode("utf-8")
    except UnicodeError:
        return None


def _cjk_mojibake_marker_count(value: str) -> int:
    return sum(1 for char in value if char in _CJK_MOJIBAKE_STARTERS)


def _strip_incomplete_terminal_escape_suffix(value: str) -> str:
    """Drop terminal control tails that were split before their terminator arrived."""
    cleaned = value
    for marker in ("\x1b]", "\x1bP", "\x1bX", "\x1b^", "\x1b_", "\x9d", "\x90", "\x98", "\x9e", "\x9f"):
        index = cleaned.rfind(marker)
        if index == -1:
            continue
        tail = cleaned[index:]
        if (
            "\x07" not in tail
            and "\x1b\\" not in tail
            and "\x9c" not in tail
            and "\n" not in tail
            and "\r" not in tail
        ):
            cleaned = cleaned[:index]
    for marker in ("\x1b[", "\x9b"):
        index = cleaned.rfind(marker)
        if index != -1 and not _has_complete_csi_sequence(cleaned[index:], marker=marker):
            cleaned = cleaned[:index]
    return cleaned


def _has_complete_csi_sequence(value: str, *, marker: str) -> bool:
    tail = value[len(marker) :]
    return bool(re.match(r"[0-?]*[ -/]*[@-~]", tail))


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
    cli_help_block = False
    fenced_code = False
    for raw_line in normalized.split("\n"):
        line = raw_line.split("\r")[-1]
        original_stripped = line.strip()
        if fenced_code:
            lines.append(line)
            if original_stripped.startswith("```"):
                fenced_code = False
            continue
        if original_stripped.startswith("```"):
            lines.append(line)
            fenced_code = True
            cli_help_block = False
            continue
        if _looks_like_cli_help_start(original_stripped):
            cli_help_block = True
            continue
        line = _strip_progress_glyph_prefix(line)
        stripped = line.strip()
        if cli_help_block:
            if not stripped or _looks_like_cli_help_continuation(original_stripped) or _looks_like_cli_help_continuation(stripped):
                continue
            cli_help_block = False
        if (
            _SPINNER_PROGRESS_RE.match(stripped)
            or _PROGRESS_ONLY_RE.match(stripped)
            or _PROGRESS_STATUS_RE.match(stripped)
            or _looks_like_replacement_gibberish(stripped)
            or _looks_like_short_binary_gibberish(stripped)
            or _looks_like_mojibake_numeric_noise(stripped)
            or _looks_like_symbol_numeric_noise(stripped)
            or _looks_like_cli_ui_noise(stripped)
        ):
            continue
        lines.append(line)
    return "\n".join(lines)


def _strip_progress_glyph_prefix(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("|") and stripped.endswith("|"):
        return value
    match = _PROGRESS_GLYPH_PREFIX_RE.match(value)
    if match is None or not match.group(0).strip():
        return value
    return value[match.end() :]


def _looks_like_cli_ui_noise(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if _TUI_BORDER_RE.match(stripped):
        return True
    normalized = stripped.strip("│ ").strip()
    return bool(_CLI_BANNER_RE.match(normalized))


def _looks_like_cli_help_start(value: str) -> bool:
    return bool(_CLI_HELP_START_RE.match(value.strip()))


def _looks_like_cli_help_continuation(value: str) -> bool:
    return bool(_CLI_HELP_CONTINUATION_RE.match(value.strip()))


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


def _looks_like_symbol_numeric_noise(value: str) -> bool:
    stripped = value.strip()
    if not 4 <= len(stripped) <= 100:
        return False
    if _contains_cjk_sentence_punctuation(stripped):
        return False
    digit_count = sum(1 for char in stripped if char.isdigit())
    if digit_count < 4:
        return False
    symbol_count = sum(
        1
        for char in stripped
        if unicodedata.category(char).startswith("S") or _is_terminal_drawing_char(char)
    )
    if symbol_count < 3:
        return False
    non_space_count = sum(1 for char in stripped if not char.isspace())
    if non_space_count == 0:
        return False
    letter_count = sum(1 for char in stripped if char.isalpha())
    return letter_count == 0 and (symbol_count + digit_count) / non_space_count >= 0.8


def _is_terminal_drawing_char(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x2500 <= codepoint <= 0x257F
        or 0x2580 <= codepoint <= 0x259F
        or 0x25A0 <= codepoint <= 0x25FF
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
