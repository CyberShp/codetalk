from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/tools", tags=["工具管理"])

_TOOL_NAMES = ("gitnexus", "deepwiki")


@router.get("/status")
async def get_tools_status():
    return [
        {"name": "gitnexus", "display_name": "GitNexus", "healthy": False, "status": "unknown"},
        {"name": "deepwiki", "display_name": "DeepWiki", "healthy": False, "status": "unknown"},
    ]


@router.post("/{tool_name}/restart")
async def restart_tool(tool_name: str):
    if tool_name not in _TOOL_NAMES:
        raise HTTPException(status_code=404, detail=f"未知工具: {tool_name}")
    return {"success": True, "message": f"{tool_name} 重启指令已发送"}
