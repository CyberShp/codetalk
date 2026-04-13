from fastapi import APIRouter

from app.adapters import get_adapter, get_all_adapters

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("")
async def list_tools():
    adapters = get_all_adapters()
    tools = []
    for adapter in adapters:
        health = await adapter.health_check()
        tools.append({
            "name": adapter.name(),
            "capabilities": [c.value for c in adapter.capabilities()],
            "healthy": health.is_healthy,
            "container_status": health.container_status,
        })
    return tools


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
