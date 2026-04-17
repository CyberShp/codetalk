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
from app.utils.repo_paths import to_tool_repo_path

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

    repo_path = to_tool_repo_path(
        repo.local_path,
        host_base_path=settings.repos_base_path,
        tool_base_path=settings.tool_repos_base_path,
    )

    # Build full payload for deepwiki
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
        "type": "local",
        "messages": [{"role": m.role, "content": m.content} for m in body.messages],
        "language": "zh",
    }

    # File context
    if body.file_path:
        payload["filePath"] = body.file_path
    if body.included_files:
        payload["included_files"] = "\n".join(body.included_files)

    # Deep research tag injection
    if body.deep_research and payload["messages"]:
        last = payload["messages"][-1]
        if last["role"] == "user":
            last["content"] = f"[DEEP RESEARCH] {last['content']}"

    # LLM config
    if llm_config:
        provider = llm_config.provider
        if provider == "custom":
            provider = "openai"
        payload["provider"] = provider
        payload["model"] = llm_config.model_name

    proxy_mode = llm_config.proxy_mode if llm_config else "system"
    trust_env = proxy_mode != "direct"

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
