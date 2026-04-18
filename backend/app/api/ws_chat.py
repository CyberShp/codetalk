"""WebSocket chat relay endpoint — streams deepwiki responses to the frontend.

Protocol
--------
Client → Server (JSON):
  {"action": "chat", "messages": [...], "file_path": "...", "included_files": [...], "deep_research": false}
  {"action": "stop"}

Server → Client (JSON, one per frame):
  {"type": "chunk", "content": "..."}
  {"type": "research_round", "round": 2, "max": 5}
  {"type": "done"}
  {"type": "error", "message": "..."}
"""

import asyncio
import logging
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.llm_config import LLMConfig
from app.models.repository import Repository
from app.services.chat_payload import ChatMessage, build_deepwiki_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/repos", tags=["ws-chat"])

_RESEARCH_COMPLETE_MARKERS = (
    "## Final Conclusion",
    "## 最终结论",
    "# Final Conclusion",
    "# 最终结论",
)
_MAX_RESEARCH_ROUNDS = 5

# Sentinel value placed in the chat queue when the WS reader task exits
_DISCONNECT = object()


def _is_research_complete(text: str) -> bool:
    return any(marker in text for marker in _RESEARCH_COMPLETE_MARKERS)


@router.websocket("/{repo_id}/chat/ws")
async def ws_chat(
    websocket: WebSocket,
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    await websocket.accept()

    # Load repo + LLM config once per connection
    repo = await db.get(Repository, repo_id)
    if not repo or not repo.local_path:
        await websocket.send_json({"type": "error", "message": "Repository not synced"})
        await websocket.close()
        return

    result = await db.execute(
        select(LLMConfig).where(LLMConfig.is_default.is_(True)).limit(1)
    )
    llm_config = result.scalar_one_or_none()
    if not llm_config:
        result = await db.execute(
            select(LLMConfig).order_by(LLMConfig.created_at.desc()).limit(1)
        )
        llm_config = result.scalar_one_or_none()

    await db.close()

    # Shared state between the reader and processor tasks
    chat_queue: asyncio.Queue[Any] = asyncio.Queue()
    stop_event = asyncio.Event()

    async def ws_reader() -> None:
        """Continuously read incoming WS frames and dispatch to stop_event / chat_queue."""
        try:
            while True:
                raw = await websocket.receive_json()
                action = raw.get("action")
                if action == "stop":
                    stop_event.set()
                elif action == "chat":
                    # New chat request — reset stop flag and enqueue payload
                    stop_event.clear()
                    await chat_queue.put(raw)
        except WebSocketDisconnect:
            stop_event.set()
            await chat_queue.put(_DISCONNECT)
        except Exception as exc:
            logger.debug("ws_reader exiting: %s", exc)
            stop_event.set()
            await chat_queue.put(_DISCONNECT)

    async def stream_one_round(
        messages: list[ChatMessage],
        file_path: str | None,
        included_files: list[str] | None,
        excluded_dirs: list[str] | None,
        deep_research: bool,
        is_continuation: bool,
    ) -> str:
        """Stream a single deepwiki round. Returns full accumulated text.

        Raises on connection or streaming errors (callers must NOT continue
        Deep Research on failure).
        """
        payload, trust_env = build_deepwiki_payload(
            repo,
            messages,
            llm_config,
            file_path=file_path,
            included_files=included_files,
            excluded_dirs=excluded_dirs,
            # Only inject [DEEP RESEARCH] on first round; deepwiki tracks iterations
            deep_research=deep_research and not is_continuation,
        )

        full = ""
        async with httpx.AsyncClient(
            base_url=settings.deepwiki_base_url,
            timeout=httpx.Timeout(300, connect=10),
            trust_env=trust_env,
        ) as client:
            async with client.stream(
                "POST",
                "/chat/completions/stream",
                json=payload,
                timeout=300,
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_text():
                    if stop_event.is_set():
                        return full
                    full += chunk
                    await websocket.send_json({"type": "chunk", "content": chunk})

        return full

    async def handle_chat(raw: dict) -> None:
        """Handle a single chat action payload end-to-end."""
        messages = [
            ChatMessage(role=m["role"], content=m["content"])
            for m in raw.get("messages", [])
        ]
        file_path: str | None = raw.get("file_path")
        included_files: list[str] | None = raw.get("included_files")
        excluded_dirs: list[str] | None = raw.get("excluded_dirs")
        deep_research: bool = bool(raw.get("deep_research", False))

        round_num = 1
        if deep_research:
            await websocket.send_json(
                {"type": "research_round", "round": round_num, "max": _MAX_RESEARCH_ROUNDS}
            )

        # Wrap stream in a cancellable task so stop_event can interrupt immediately
        async def _stream_cancellable(*args, **kwargs) -> str:
            return await stream_one_round(*args, **kwargs)

        try:
            stream_task = asyncio.ensure_future(
                _stream_cancellable(
                    messages, file_path, included_files, excluded_dirs, deep_research, is_continuation=False
                )
            )
            stop_waiter = asyncio.ensure_future(stop_event.wait())

            done, pending = await asyncio.wait(
                {stream_task, stop_waiter}, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()

            if stop_event.is_set():
                if not stream_task.done():
                    stream_task.cancel()
                await websocket.send_json({"type": "done"})
                return

            last_response = stream_task.result()  # raises if stream_one_round raised
        except Exception as exc:
            logger.warning("stream_one_round failed (round 1): %s", exc)
            await websocket.send_json({"type": "error", "message": str(exc)})
            return

        # Build accumulated history for continuation rounds
        acc_messages = [
            ChatMessage(role=m.role, content=m.content) for m in messages
        ] + [ChatMessage(role="assistant", content=last_response)]

        # Deep Research auto-continuation loop (backend-driven)
        if deep_research:
            while (
                not stop_event.is_set()
                and not _is_research_complete(last_response)
                and round_num < _MAX_RESEARCH_ROUNDS
            ):
                round_num += 1
                await websocket.send_json(
                    {"type": "research_round", "round": round_num, "max": _MAX_RESEARCH_ROUNDS}
                )

                acc_messages = acc_messages + [
                    ChatMessage(role="user", content="Continue the research")
                ]

                await asyncio.sleep(0.5)

                try:
                    stream_task = asyncio.ensure_future(
                        _stream_cancellable(
                            acc_messages,
                            file_path,
                            included_files,
                            excluded_dirs,
                            deep_research=True,
                            is_continuation=True,
                        )
                    )
                    stop_waiter = asyncio.ensure_future(stop_event.wait())

                    done, pending = await asyncio.wait(
                        {stream_task, stop_waiter}, return_when=asyncio.FIRST_COMPLETED
                    )
                    for t in pending:
                        t.cancel()

                    if stop_event.is_set():
                        if not stream_task.done():
                            stream_task.cancel()
                        break

                    last_response = stream_task.result()
                except Exception as exc:
                    logger.warning("stream_one_round failed (round %d): %s", round_num, exc)
                    await websocket.send_json({"type": "error", "message": str(exc)})
                    return

                acc_messages = acc_messages + [
                    ChatMessage(role="assistant", content=last_response)
                ]

        await websocket.send_json({"type": "done"})

    # Start the reader task and process chat payloads from the queue
    reader_task = asyncio.ensure_future(ws_reader())
    try:
        while True:
            raw = await chat_queue.get()
            if raw is _DISCONNECT:
                break
            await handle_chat(raw)
    except Exception as exc:
        logger.exception("WS chat processor unexpected error: %s", exc)
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
            await websocket.close()
        except Exception:
            pass
    finally:
        reader_task.cancel()
        try:
            await reader_task
        except (asyncio.CancelledError, Exception):
            pass
