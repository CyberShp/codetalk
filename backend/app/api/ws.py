import uuid as _uuid_mod

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter()

_task_subscribers: dict[str, set[WebSocket]] = {}


@router.websocket("/ws/tasks/{task_id}/logs")
async def task_logs(
    websocket: WebSocket,
    task_id: str,
    db: AsyncSession = Depends(get_db),
):
    await websocket.accept()

    # Register subscriber FIRST — closing the TOCTOU window between the DB
    # check below and any in-flight broadcast from task_engine.
    if task_id not in _task_subscribers:
        _task_subscribers[task_id] = set()
    _task_subscribers[task_id].add(websocket)

    try:
        # State replay: if task is already in a terminal state, push the
        # current status immediately so late-connecting clients don't stall.
        try:
            from app.models.task import AnalysisTask

            task = await db.get(AnalysisTask, _uuid_mod.UUID(task_id))
            if task and task.status in ("completed", "failed", "cancelled"):
                msg = task.error if task.status == "failed" else "索引重建完成"
                await websocket.send_json(
                    {
                        "type": "progress",
                        "task_id": task_id,
                        "progress": task.progress,
                        "status": task.status,
                        "message": msg,
                    }
                )
                return  # terminal — no need to keep connection open
        except Exception:
            pass  # invalid UUID, task not found — fall through to live loop

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


async def broadcast_task_event(task_id: str, event: dict) -> None:
    """Broadcast a structured pipeline event to all WebSocket subscribers for a task."""
    subscribers = _task_subscribers.get(task_id, set())
    if not subscribers:
        return
    dead: set[WebSocket] = set()
    for ws in subscribers:
        try:
            await ws.send_json(event)
        except Exception:
            dead.add(ws)
    subscribers -= dead
