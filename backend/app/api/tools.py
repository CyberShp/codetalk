"""Tool management API -- start, stop, restart, and monitor tool processes."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.services.process_manager import ProcessManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tools", tags=["tools"])


def _get_pm(request: Request) -> ProcessManager:
    """Retrieve the ProcessManager from app.state, or fall back to singleton."""
    pm: ProcessManager | None = getattr(request.app.state, "process_manager", None)
    if pm is not None:
        return pm
    return ProcessManager.get_instance()


@router.get("/status")
async def get_tools_status(request: Request) -> list[dict[str, Any]]:
    """Return live status of all registered tool processes."""
    pm = _get_pm(request)
    return await pm.get_all_status()


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
