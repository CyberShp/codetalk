from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

_task_subscribers: dict[str, set[WebSocket]] = {}


@router.websocket("/ws/tasks/{task_id}/logs")
async def task_logs(websocket: WebSocket, task_id: str):
    await websocket.accept()
    if task_id not in _task_subscribers:
        _task_subscribers[task_id] = set()
    _task_subscribers[task_id].add(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        _task_subscribers.get(task_id, set()).discard(websocket)
        if task_id in _task_subscribers and not _task_subscribers[task_id]:
            del _task_subscribers[task_id]


async def broadcast_task_progress(
    task_id: str, progress: int, status: str, message: str
) -> None:
    """Broadcast progress to all WebSocket subscribers for a task."""
    subscribers = _task_subscribers.get(task_id, set())
    dead: set[WebSocket] = set()
    payload = {
        "type": "progress",
        "task_id": task_id,
        "progress": progress,
        "status": status,
        "message": message,
    }
    for ws in subscribers:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.add(ws)
    subscribers -= dead
