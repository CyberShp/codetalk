"""Repository-level chat streaming endpoint."""

import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.llm_config import LLMConfig
from app.models.repository import Repository
from app.api.chat import ChatMessage
from app.services.chat_payload import build_deepwiki_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/repos", tags=["repo-chat"])


class RepoChatRequest(BaseModel):
    repo_id: uuid.UUID
    messages: list[ChatMessage]
    file_path: str | None = None
    deep_research: bool = False
    included_files: list[str] | None = None


@router.post("/{repo_id}/chat/stream")
async def repo_chat_stream(
    repo_id: uuid.UUID, body: RepoChatRequest, db: AsyncSession = Depends(get_db)
):
    """Stream chat response for repo, with full deepwiki params."""
    repo = await db.get(Repository, repo_id)
    if not repo or not repo.local_path:
        raise HTTPException(400, "Repository not synced")

    result = await db.execute(
        select(LLMConfig).where(LLMConfig.is_default.is_(True)).limit(1)
    )
    llm_config = result.scalar_one_or_none()
    if not llm_config:
        result = await db.execute(
            select(LLMConfig).order_by(LLMConfig.created_at.desc()).limit(1)
        )
        llm_config = result.scalar_one_or_none()

    payload, trust_env = build_deepwiki_payload(
        repo,
        body.messages,
        llm_config,
        file_path=body.file_path,
        included_files=body.included_files,
        deep_research=body.deep_research,
    )

    await db.close()

    async def generate():
        try:
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
                        yield chunk
        except httpx.ConnectError:
            yield "\n\n> ⚠️ 无法连接 deepwiki 服务。"
        except Exception as exc:
            yield f"\n\n> ⚠️ 请求失败: {exc}"

    return StreamingResponse(
        generate(),
        media_type="text/plain",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
