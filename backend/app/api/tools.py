"""Tool management API -- start, stop, restart, and monitor tool processes."""

import asyncio
import logging
from inspect import isawaitable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.adapters import get_adapter, get_all_adapters
from app.adapters.external_agent import ExternalAgentAdapter
from app.adapters.gitnexus import resolve_indexed_repo
from app.config import settings
from app.services import process_manager as process_manager_module
from app.services.external_agent_discovery import (
    check_provider_health,
    external_agent_provider_capabilities,
    external_agent_provider_spec,
    external_agent_provider_ids,
    redact_agent_diagnostic_text,
)
from app.services.process_manager import ProcessManager
from app.utils.local_client import local_http_client
from app.utils.repo_paths import to_tool_repo_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tools", tags=["tools"])

_HEALTH_TIMEOUT = 4.0  # seconds; adapters slower than this are reported as busy
_MANAGED_STARTUP_PROBE_TOOL_NAMES = {"gitnexus"}


def _get_pm(request: Request) -> ProcessManager:
    """Retrieve the ProcessManager from app.state, or fall back to singleton."""
    pm: ProcessManager | None = getattr(request.app.state, "process_manager", None)
    if pm is not None:
        return pm
    return ProcessManager.get_instance()


async def _check_health(adapter) -> dict[str, Any]:
    try:
        health = await asyncio.wait_for(adapter.health_check(), timeout=_HEALTH_TIMEOUT)
        return {
            "name": adapter.name(),
            "capabilities": [c.value for c in adapter.capabilities()],
            "healthy": health.is_healthy,
            "container_status": health.container_status,
        }
    except asyncio.TimeoutError:
        return {
            "name": adapter.name(),
            "capabilities": [c.value for c in adapter.capabilities()],
            "healthy": True,
            "container_status": "busy",
        }
    except Exception:
        return {
            "name": adapter.name(),
            "capabilities": [c.value for c in adapter.capabilities()],
            "healthy": False,
            "container_status": "error",
        }


def _display_name(name: str) -> str:
    spec = external_agent_provider_spec(name)
    if spec and spec.display_name:
        return spec.display_name
    return {
        "claude-code": "Claude Code",
        "opencode": "OpenCode",
    }.get(name, name)


def _agent_provider_capabilities(name: str) -> dict[str, Any]:
    return external_agent_provider_capabilities(name)


def _agent_provider_diagnostics(name: str) -> dict[str, Any]:
    spec = external_agent_provider_spec(name)
    if spec is None:
        return {}
    diagnostics: dict[str, Any] = {
        "provider": name,
        "configured_command_text": redact_agent_diagnostic_text(spec.command),
        "fallback_command_texts": [
            redact_agent_diagnostic_text(command) for command in spec.fallback_commands
        ],
        "prompt_transport": spec.prompt_transport,
        "startup_probe_endpoint": f"/api/tools/{name}/startup-probe",
        "manual_probe_command": (
            f"POST /api/tools/{name}/startup-probe?repo_path=<repo_path>"
        ),
    }
    try:
        health = check_provider_health(name, spec.command, fallback_commands=spec.fallback_commands)
    except Exception as exc:
        diagnostics["command_resolution"] = {
            "status": "error",
            "reason": redact_agent_diagnostic_text(str(exc)),
        }
        return diagnostics
    attempts = [
        _agent_command_attempt_summary(item)
        for item in health.get("attempts") or []
        if isinstance(item, dict)
    ]
    diagnostics["command_resolution"] = {
        "status": str(health.get("status") or ""),
        "configured_command": redact_agent_diagnostic_text(
            str(health.get("configured_command") or spec.command)
        ),
        "command": redact_agent_diagnostic_text(str(health.get("command") or "")),
        "path": redact_agent_diagnostic_text(str(health.get("path") or "")),
        "launch_kind": str(health.get("launch_kind") or ""),
        "used_fallback": bool(health.get("used_fallback", False)),
        "reason": redact_agent_diagnostic_text(str(health.get("reason") or "")),
        "attempt_count": len(attempts),
        "attempts": attempts,
    }
    return diagnostics


def _agent_command_attempt_summary(item: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "command",
        "status",
        "reason",
        "executable",
        "path",
        "launch_kind",
        "config_hint",
        "profile_config_path",
        "shell_path",
    )
    result: dict[str, Any] = {}
    for field in fields:
        value = item.get(field)
        if value is None:
            continue
        result[field] = redact_agent_diagnostic_text(str(value))
    configured_argv = item.get("configured_argv")
    if isinstance(configured_argv, list):
        result["configured_argv"] = [
            redact_agent_diagnostic_text(str(value)) for value in configured_argv
        ]
    return result


def _adapter_only_tool_names() -> set[str]:
    return {*external_agent_provider_ids(), "fast-context"}


def _runtime_external_agent_adapters(managed_names: set[str]) -> list[ExternalAgentAdapter]:
    existing = {adapter.name() for adapter in get_all_adapters()}
    adapters: list[ExternalAgentAdapter] = []
    for provider in external_agent_provider_ids():
        if provider in existing or provider in managed_names:
            continue
        adapters.append(ExternalAgentAdapter(provider))
    return adapters


def _get_adapter_or_runtime_external_agent(tool_name: str):
    try:
        return get_adapter(tool_name)
    except KeyError:
        if external_agent_provider_spec(tool_name) is not None:
            return ExternalAgentAdapter(tool_name)
        raise


async def _adapter_proc_status(adapter) -> dict[str, Any]:
    try:
        health = await asyncio.wait_for(adapter.health_check(), timeout=_HEALTH_TIMEOUT)
        return {
            "name": adapter.name(),
            "display_name": _display_name(adapter.name()),
            "healthy": health.is_healthy,
            "status": health.container_status,
            "managed": False,
            "capabilities": [c.value for c in adapter.capabilities()],
            "version": health.version,
            "last_check": health.last_check,
            "message": health.last_check or health.version,
            "agent_provider": _agent_provider_capabilities(adapter.name()),
            "agent_provider_diagnostics": _agent_provider_diagnostics(adapter.name()),
        }
    except asyncio.TimeoutError:
        return {
            "name": adapter.name(),
            "display_name": _display_name(adapter.name()),
            "healthy": True,
            "status": "busy",
            "managed": False,
            "capabilities": [c.value for c in adapter.capabilities()],
            "last_check": "health check timed out",
            "message": "health check timed out",
            "agent_provider": _agent_provider_capabilities(adapter.name()),
            "agent_provider_diagnostics": _agent_provider_diagnostics(adapter.name()),
        }
    except Exception as exc:
        message = redact_agent_diagnostic_text(str(exc))
        return {
            "name": adapter.name(),
            "display_name": _display_name(adapter.name()),
            "healthy": False,
            "status": "error",
            "managed": False,
            "capabilities": [c.value for c in adapter.capabilities()],
            "last_check": message,
            "message": message,
            "agent_provider": _agent_provider_capabilities(adapter.name()),
            "agent_provider_diagnostics": _agent_provider_diagnostics(adapter.name()),
        }


@router.get("")
async def list_tools() -> list[dict[str, Any]]:
    """Return health status of all registered tool adapters."""
    adapters = get_all_adapters()
    results = await asyncio.gather(*[_check_health(a) for a in adapters])
    return list(results)


@router.get("/status")
async def get_tools_status() -> dict[str, dict[str, Any]]:
    """Return adapter health status keyed by tool name.

    Response shape per tool:
        {"healthy": bool, "indexed_repos": int, "last_index_error": str | None, ...}
    """
    adapters = get_all_adapters()
    results: dict[str, dict[str, Any]] = {}
    for adapter in adapters:
        try:
            health = await asyncio.wait_for(adapter.health_check(), timeout=_HEALTH_TIMEOUT)
            last_check = health.last_check or ""
            results[adapter.name()] = {
                "healthy": health.is_healthy,
                "indexed_repos": health.indexed_repos,
                "last_index_error": health.last_index_error,
                "container_status": health.container_status,
                "version": health.version,
                "last_check": last_check,
                "message": last_check or health.version or health.last_index_error,
                "capabilities": [c.value for c in adapter.capabilities()],
                "agent_provider": _agent_provider_capabilities(adapter.name()),
            }
        except asyncio.TimeoutError:
            results[adapter.name()] = {
                "healthy": True,
                "indexed_repos": 0,
                "last_index_error": None,
                "container_status": "busy",
                "version": None,
                "last_check": "health check timed out",
                "message": "health check timed out",
                "capabilities": [c.value for c in adapter.capabilities()],
                "agent_provider": _agent_provider_capabilities(adapter.name()),
            }
        except Exception as exc:
            message = redact_agent_diagnostic_text(str(exc))
            results[adapter.name()] = {
                "healthy": False,
                "indexed_repos": 0,
                "last_index_error": message,
                "container_status": "error",
                "version": None,
                "last_check": message,
                "message": message,
                "capabilities": [c.value for c in adapter.capabilities()],
                "agent_provider": _agent_provider_capabilities(adapter.name()),
            }
    return results


@router.get("/procs")
async def get_tool_procs(request: Request) -> list[dict[str, Any]]:
    """Return live status of all registered tool processes (process manager view)."""
    pm = _get_pm(request)
    process_status = await pm.get_all_status()
    managed_names = {str(item.get("name")) for item in process_status if isinstance(item, dict)}
    adapter_status = await asyncio.gather(*[
        _adapter_proc_status(adapter)
        for adapter in [
            *get_all_adapters(),
            *_runtime_external_agent_adapters(managed_names),
        ]
        if adapter.name() in _adapter_only_tool_names() and adapter.name() not in managed_names
    ])
    return [*process_status, *adapter_status]


@router.get("/{tool_name}/health")
async def get_tool_health(tool_name: str) -> dict[str, Any]:
    """Return health status of a specific tool adapter."""
    try:
        adapter = _get_adapter_or_runtime_external_agent(tool_name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")

    try:
        health = await asyncio.wait_for(adapter.health_check(), timeout=_HEALTH_TIMEOUT)
        return {
            "name": adapter.name(),
            "healthy": health.is_healthy,
            "container_status": health.container_status,
            "version": health.version,
            "last_check": health.last_check,
            "message": health.last_check or health.version,
            "agent_provider": _agent_provider_capabilities(adapter.name()),
        }
    except asyncio.TimeoutError:
        return {
            "name": adapter.name(),
            "healthy": True,
            "container_status": "busy",
            "version": None,
            "last_check": "health check timed out",
            "message": "health check timed out",
            "agent_provider": _agent_provider_capabilities(adapter.name()),
        }
    except Exception as exc:
        message = redact_agent_diagnostic_text(str(exc))
        return {
            "name": adapter.name(),
            "healthy": False,
            "container_status": "error",
            "version": None,
            "last_check": message,
            "message": message,
            "agent_provider": _agent_provider_capabilities(adapter.name()),
        }


@router.post("/{tool_name}/startup-probe")
async def startup_probe_tool(
    tool_name: str,
    request: Request,
    repo_path: str | None = None,
) -> dict[str, Any]:
    """Actually start an adapter-only external agent once and report diagnostics."""
    try:
        adapter = _get_adapter_or_runtime_external_agent(tool_name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")

    probe = getattr(adapter, "startup_probe", None)
    if not callable(probe):
        if tool_name in _MANAGED_STARTUP_PROBE_TOOL_NAMES:
            return await _managed_tool_startup_probe(
                tool_name,
                _get_pm(request),
                repo_path=repo_path,
            )
        raise HTTPException(status_code=400, detail=f"Tool does not support startup probe: {tool_name}")

    try:
        return await probe(repo_path=repo_path)
    except asyncio.TimeoutError:
        return {
            "provider": tool_name,
            "healthy": False,
            "status": "timeout",
            "message": "startup probe timed out",
        }
    except Exception as exc:
        message = redact_agent_diagnostic_text(str(exc))
        return {
            "provider": tool_name,
            "healthy": False,
            "status": "error",
            "message": message,
        }


async def _managed_tool_startup_probe(
    tool_name: str,
    pm: ProcessManager,
    *,
    repo_path: str | None = None,
) -> dict[str, Any]:
    initial_health: dict[str, Any] = {}
    try:
        health_check = getattr(pm, "health_check", None)
        if callable(health_check):
            initial_health = await health_check(tool_name)
    except Exception as exc:
        initial_health = {"healthy": False, "status": "error", "last_error": str(exc)}

    repo_index = await _managed_tool_repo_index_diagnostics(tool_name, repo_path)
    if bool(initial_health.get("healthy")):
        return {
            "tool": tool_name,
            "healthy": True,
            "status": "ok",
            "started": False,
            "message": "startup probe ok: existing service already reachable",
            "health": initial_health,
            "diagnostics": _managed_tool_startup_diagnostics(
                tool_name,
                initial_health=initial_health,
                post_start_health=initial_health,
                repo_index=repo_index,
            ),
            "stdout_log": str(settings.data_path / "logs" / "processes" / f"{tool_name}.out.log"),
            "stderr_log": str(settings.data_path / "logs" / "processes" / f"{tool_name}.err.log"),
        }

    try:
        started = await pm.start(tool_name)
    except Exception as exc:
        message = redact_agent_diagnostic_text(str(exc))
        return {
            "tool": tool_name,
            "healthy": False,
            "status": "error",
            "started": False,
            "message": message,
            "diagnostics": _managed_tool_startup_diagnostics(
                tool_name,
                initial_health=initial_health,
                repo_index=repo_index,
            ),
        }

    health: dict[str, Any] = {}
    try:
        health_check = getattr(pm, "health_check", None)
        if callable(health_check):
            health = await health_check(tool_name)
    except Exception as exc:
        health = {"healthy": False, "status": "error", "last_error": str(exc)}

    if not repo_index:
        repo_index = await _managed_tool_repo_index_diagnostics(tool_name, repo_path)
    managed = getattr(pm, "_processes", {}).get(tool_name)
    last_error = (
        str(health.get("last_error") or health.get("error") or "").strip()
        if isinstance(health, dict)
        else ""
    )
    if not last_error and managed is not None:
        last_error = str(getattr(managed, "last_error", "") or "").strip()

    healthy = bool(health.get("healthy")) if isinstance(health, dict) else False
    health_status = str(health.get("status") or "").strip() if isinstance(health, dict) else ""
    status = "ok" if healthy else (health_status or "error")
    if not started and status == "ok":
        status = "error"
    message = "startup probe ok" if healthy else (last_error or f"Failed to start tool: {tool_name}")

    stdout_log = settings.data_path / "logs" / "processes" / f"{tool_name}.out.log"
    stderr_log = settings.data_path / "logs" / "processes" / f"{tool_name}.err.log"
    return {
        "tool": tool_name,
        "healthy": healthy,
        "status": status,
        "started": bool(started),
        "message": redact_agent_diagnostic_text(message),
        "health": health,
        "diagnostics": _managed_tool_startup_diagnostics(
            tool_name,
            initial_health=initial_health,
            post_start_health=health,
            repo_index=repo_index,
        ),
        "stdout_log": str(stdout_log),
        "stderr_log": str(stderr_log),
    }


async def _managed_tool_repo_index_diagnostics(
    tool_name: str,
    repo_path: str | None,
) -> dict[str, Any]:
    if tool_name != "gitnexus" or not repo_path:
        return {}
    return await _gitnexus_repo_readiness(repo_path)


async def _gitnexus_repo_readiness(repo_path: str) -> dict[str, Any]:
    requested_path = str(repo_path or "").strip()
    resolved_repo_path = str(Path(requested_path).expanduser().resolve()) if requested_path else ""
    tool_repo_path = (
        to_tool_repo_path(
            resolved_repo_path,
            host_base_path=settings.repos_base_path,
            tool_base_path=settings.tool_repos_base_path,
        )
        if resolved_repo_path
        else ""
    )
    result: dict[str, Any] = {
        "requested_repo_path": requested_path,
        "resolved_repo_path": resolved_repo_path,
        "tool_repo_path": tool_repo_path,
        "base_url": settings.gitnexus_base_url,
        "service_reachable": False,
        "repo_indexed": False,
        "indexed_repo_count": 0,
    }
    try:
        result["repo_path_exists"] = bool(resolved_repo_path and Path(resolved_repo_path).is_dir())
    except OSError:
        result["repo_path_exists"] = False

    try:
        async with local_http_client(settings.gitnexus_base_url, timeout=10, connect_timeout=3) as client:
            resp = await client.get("/api/repos", timeout=10)
    except Exception as exc:
        result["message"] = f"GitNexus /api/repos unreachable: {redact_agent_diagnostic_text(str(exc))}"
        return result

    result["service_reachable"] = True
    result["repos_status_code"] = resp.status_code
    if resp.status_code != 200:
        result["message"] = f"GitNexus reachable but /api/repos returned HTTP {resp.status_code}"
        return result

    try:
        payload = resp.json()
    except Exception as exc:
        result["message"] = f"GitNexus /api/repos returned invalid JSON: {redact_agent_diagnostic_text(str(exc))}"
        return result

    result["indexed_repo_count"] = _gitnexus_repo_entry_count(payload)
    descriptor = resolve_indexed_repo(payload, tool_repo_path)
    if descriptor is None and isinstance(payload, dict) and isinstance(payload.get("value"), list):
        descriptor = resolve_indexed_repo(payload["value"], tool_repo_path)
    if descriptor:
        result.update({
            "repo_indexed": True,
            "matched_repo_name": descriptor.get("name"),
            "matched_repo_path": descriptor.get("path"),
            "matched_repo_id": descriptor.get("id"),
            "matched_repo_ambiguous": bool(descriptor.get("ambiguous")),
            "node_count": descriptor.get("node_count"),
            "edge_count": descriptor.get("edge_count"),
            "file_count": descriptor.get("file_count"),
            "message": f"GitNexus repo indexed as {descriptor.get('name')}",
        })
        return result

    result["message"] = "GitNexus reachable but this repo is not indexed"
    return result


def _gitnexus_repo_entry_count(payload: object) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("repos", "data", "items", "results", "value"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
        if any(key in payload for key in ("name", "repo", "repoName", "path", "root", "repo_path")):
            return 1
    return 0


def _managed_tool_startup_diagnostics(
    tool_name: str,
    *,
    initial_health: dict[str, Any] | None = None,
    post_start_health: dict[str, Any] | None = None,
    repo_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = process_manager_module.TOOL_REGISTRY.get(tool_name, {})
    command = [str(part) for part in cfg.get("command") or []]
    try:
        resolved_command = process_manager_module._resolve_spawn_command(command)
    except Exception as exc:
        resolved_command = command
        resolve_error = redact_agent_diagnostic_text(str(exc))
    else:
        resolve_error = ""
    stdout_log = settings.data_path / "logs" / "processes" / f"{tool_name}.out.log"
    stderr_log = settings.data_path / "logs" / "processes" / f"{tool_name}.err.log"
    return {
        "configured_command": command,
        "resolved_command": [str(part) for part in resolved_command],
        "resolve_error": resolve_error,
        "cwd": cfg.get("cwd"),
        "health_url": cfg.get("health_url"),
        "health_fallback_url": cfg.get("health_fallback_url"),
        "stdout_log": str(stdout_log),
        "stderr_log": str(stderr_log),
        "initial_health": initial_health or {},
        "post_start_health": post_start_health or {},
        "repo_index": repo_index or {},
    }


@router.post("/{tool_name}/start")
async def start_tool(tool_name: str, request: Request) -> dict[str, Any]:
    """Start a tool process by name."""
    pm = _get_pm(request)
    existing_health = await _existing_healthy_managed_tool(pm, tool_name)
    if existing_health is not None:
        return {
            "success": True,
            "message": f"{tool_name} already running",
            "health": existing_health,
        }

    ok = await pm.start(tool_name)
    if not ok:
        detail = f"Failed to start tool: {tool_name}"
        managed = getattr(pm, "_processes", {}).get(tool_name)
        last_error = getattr(managed, "last_error", None)
        if last_error:
            detail = f"{detail}: {last_error}"
        raise HTTPException(status_code=400, detail=detail)
    return {"success": True, "message": f"{tool_name} started"}


async def _existing_healthy_managed_tool(pm: ProcessManager, tool_name: str) -> dict[str, Any] | None:
    health_check = getattr(pm, "health_check", None)
    if not callable(health_check):
        return None
    try:
        result = health_check(tool_name)
        if isawaitable(result):
            result = await result
    except Exception:
        return None
    if not isinstance(result, dict) or not bool(result.get("healthy")):
        return None
    return result


@router.post("/{tool_name}/stop")
async def stop_tool(tool_name: str, request: Request) -> dict[str, Any]:
    """Stop a tool process by name."""
    pm = _get_pm(request)
    ok = await pm.stop(tool_name)
    if not ok:
        raise HTTPException(status_code=400, detail=f"Failed to stop tool: {tool_name}")
    return {"success": True, "message": f"{tool_name} stopped"}


@router.post("/{tool_name}/restart")
async def restart_tool(tool_name: str, request: Request) -> dict[str, Any]:
    """Restart a tool process by name."""
    pm = _get_pm(request)
    ok = await pm.restart(tool_name)
    if not ok:
        raise HTTPException(status_code=400, detail=f"Failed to restart tool: {tool_name}")
    return {"success": True, "message": f"{tool_name} restarted"}
