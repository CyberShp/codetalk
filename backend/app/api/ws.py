from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/tasks/{task_id}/logs")
async def task_logs(websocket: WebSocket, task_id: str):
    """WebSocket endpoint for real-time task logs. Stub for Phase 4."""
    await websocket.accept()
    try:
        await websocket.send_json({"level": "INFO", "message": f"Connected to task {task_id} logs", "tool": "system"})
        # Phase 4: will poll task_logs table and stream adapter logs
        while True:
            # Keep connection alive, wait for client messages
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"level": "INFO", "message": "pong", "tool": "system"})
    except WebSocketDisconnect:
        pass
