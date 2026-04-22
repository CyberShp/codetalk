import asyncio

from fastapi import APIRouter

from app.adapters import get_adapter, get_all_adapters
from app.adapters.base import ToolHealth

router = APIRouter(prefix="/api/tools", tags=["tools"])

_HEALTH_PER_TOOL_TIMEOUT = 3  # seconds per tool health check
_HEALTH_PER_TOOL_TIMEOUTS = {
    "joern": 4,  # give Joern enough room to report "busy" instead of timing out
}
_HEALTH_TOTAL_TIMEOUT = 5  # seconds for all checks combined


def _health_timeout_for(adapter) -> float:
    return _HEALTH_PER_TOOL_TIMEOUTS.get(adapter.name(), _HEALTH_PER_TOOL_TIMEOUT)


async def _check_one(adapter) -> dict:
    try:
        health = await asyncio.wait_for(
            adapter.health_check(),
            timeout=_health_timeout_for(adapter),
        )
    except asyncio.TimeoutError:
        if adapter.name() == "joern":
            health = ToolHealth(is_healthy=True, container_status="busy")
        else:
            health = ToolHealth(is_healthy=False, container_status="timeout")
    except Exception:
        health = ToolHealth(is_healthy=False, container_status="error")
    return {
        "name": adapter.name(),
        "capabilities": [c.value for c in adapter.capabilities()],
        "healthy": health.is_healthy,
        "container_status": health.container_status,
    }


@router.get("")
async def list_tools():
    adapters = get_all_adapters()
    try:
        tools = await asyncio.wait_for(
            asyncio.gather(*[_check_one(a) for a in adapters]),
            timeout=_HEALTH_TOTAL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        tools = [
            {
                "name": a.name(),
                "capabilities": [c.value for c in a.capabilities()],
                "healthy": False,
                "container_status": "timeout",
            }
            for a in adapters
        ]
    return list(tools)


@router.get("/{tool_name}/health")
async def tool_health(tool_name: str):
    adapter = get_adapter(tool_name)
    health = await adapter.health_check()
    return {
        "name": adapter.name(),
        "healthy": health.is_healthy,
        "container_status": health.container_status,
        "version": health.version,
    }
