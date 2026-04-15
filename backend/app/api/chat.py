"""Chat streaming endpoint — proxies Q&A to deepwiki-open.

IRON LAW: This endpoint only does HTTP proxying + response streaming.
No analysis logic, no code parsing, no graph building.
"""

import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.llm_config import LLMConfig
from app.models.repository import Repository
from app.models.task import AnalysisTask

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    task_id: uuid.UUID
    messages: list[ChatMessage]


@router.post("/stream")
async def chat_stream(body: ChatRequest, db: AsyncSession = Depends(get_db)):
    """Stream a chat response from deepwiki about the task's repository."""
    task = await db.get(AnalysisTask, body.task_id)
    if not task:
        raise HTTPException(404, "Task not found")

    repo = await db.get(Repository, task.repository_id)
    if not repo or not repo.local_path:
        raise HTTPException(400, "Repository not synced")

    # Capture DB values before entering the stream generator
    repo_path = repo.local_path

    result = await db.execute(
        select(LLMConfig).where(LLMConfig.is_default.is_(True)).limit(1)
    )
    llm_config = result.scalar_one_or_none()
    if not llm_config:
        result = await db.execute(
            select(LLMConfig).order_by(LLMConfig.created_at.desc()).limit(1)
        )
        llm_config = result.scalar_one_or_none()

    payload: dict = {
        "repo_url": repo_path,
        "messages": [{"role": m.role, "content": m.content} for m in body.messages],
        "language": "zh",
    }

    proxy_mode = "system"
    if llm_config:
        provider = llm_config.provider
        if provider == "custom":
            provider = "openai"
        payload["provider"] = provider
        payload["model"] = llm_config.model_name
        proxy_mode = llm_config.proxy_mode

    trust_env = proxy_mode != "direct"

    # Release the DB session before opening the potentially long-lived stream.
    await db.close()

    logger.info(
        "chat stream: repo=%s provider=%s model=%s",
        repo_path,
        payload.get("provider", "(none)"),
        payload.get("model", "(none)"),
    )

    async def generate():
        try:
            async with httpx.AsyncClient(
                base_url="http://deepwiki:8001",
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
                        yield chunk
        except httpx.ConnectError:
            yield "\n\n> ⚠️ 无法连接 deepwiki 服务，请检查容器是否运行。"
        except httpx.HTTPStatusError as exc:
            logger.error("deepwiki returned %s", exc.response.status_code)
            yield f"\n\n> ⚠️ deepwiki 返回错误 {exc.response.status_code}"
        except Exception as exc:
            logger.error("Chat stream error: %s", exc)
            yield f"\n\n> ⚠️ 请求失败: {exc}"

    return StreamingResponse(
        generate(),
        media_type="text/plain",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
