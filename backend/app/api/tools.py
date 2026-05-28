"""Tool management API -- start, stop, restart, and monitor tool processes."""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.adapters import get_adapter, get_all_adapters
from app.services.process_manager import ProcessManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tools", tags=["tools"])

_HEALTH_TIMEOUT = 4.0  # seconds; adapters slower than this are reported as busy


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
        {"healthy": bool, "indexed_repos": int, "last_index_error": str | None}
    """
    adapters = get_all_adapters()
    results: dict[str, dict[str, Any]] = {}
    for adapter in adapters:
        try:
            health = await asyncio.wait_for(adapter.health_check(), timeout=_HEALTH_TIMEOUT)
            results[adapter.name()] = {
                "healthy": health.is_healthy,
                "indexed_repos": health.indexed_repos,
                "last_index_error": health.last_index_error,
            }
        except asyncio.TimeoutError:
            results[adapter.name()] = {
                "healthy": True,
                "indexed_repos": 0,
                "last_index_error": None,
            }
        except Exception as exc:
            results[adapter.name()] = {
                "healthy": False,
                "indexed_repos": 0,
                "last_index_error": str(exc),
            }
    return results


@router.get("/procs")
async def get_tool_procs(request: Request) -> list[dict[str, Any]]:
    """Return live status of all registered tool processes (process manager view)."""
    pm = _get_pm(request)
    return await pm.get_all_status()


@router.get("/{tool_name}/health")
async def get_tool_health(tool_name: str) -> dict[str, Any]:
    """Return health status of a specific tool adapter."""
    try:
        adapter = get_adapter(tool_name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")

    try:
        health = await asyncio.wait_for(adapter.health_check(), timeout=_HEALTH_TIMEOUT)
        return {
            "name": adapter.name(),
            "healthy": health.is_healthy,
            "container_status": health.container_status,
            "version": health.version,
        }
    except asyncio.TimeoutError:
        return {
            "name": adapter.name(),
            "healthy": True,
            "container_status": "busy",
            "version": None,
        }
    except Exception:
        return {
            "name": adapter.name(),
            "healthy": False,
            "container_status": "error",
            "version": None,
        }


@router.post("/{tool_name}/start")
async def start_tool(tool_name: str, request: Request) -> dict[str, Any]:
    """Start a tool process by name."""
    pm = _get_pm(request)
    ok = await pm.start(tool_name)
    if not ok:
        raise HTTPException(status_code=400, detail=f"Failed to start tool: {tool_name}")
    return {"success": True, "message": f"{tool_name} started"}


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
