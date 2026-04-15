import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.llm_config import LLMConfig
from app.schemas.llm_config import LLMConfigCreate, LLMConfigResponse, LLMConfigUpdate
from app.utils.crypto import decrypt_key, encrypt_key

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _to_response(c: LLMConfig) -> LLMConfigResponse:
    return LLMConfigResponse(
        id=c.id, provider=c.provider, model_name=c.model_name,
        has_api_key=bool(c.api_key_encrypted),
        base_url=c.base_url, proxy_mode=c.proxy_mode,
        is_default=c.is_default, created_at=c.created_at,
    )


@router.get("/llm", response_model=list[LLMConfigResponse])
async def get_llm_configs(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(LLMConfig).order_by(LLMConfig.created_at.desc()))
    return [_to_response(c) for c in result.scalars().all()]


@router.post("/llm", response_model=LLMConfigResponse, status_code=201)
async def save_llm_config(data: LLMConfigCreate, db: AsyncSession = Depends(get_db)):
    encrypted = encrypt_key(data.api_key) if data.api_key else None
    config = LLMConfig(
        provider=data.provider, model_name=data.model_name,
        api_key_encrypted=encrypted, base_url=data.base_url,
        proxy_mode=data.proxy_mode, is_default=data.is_default,
    )
    if data.is_default:
        result = await db.execute(select(LLMConfig).where(LLMConfig.is_default.is_(True)))
        for existing in result.scalars().all():
            existing.is_default = False
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return _to_response(config)


@router.put("/llm/{config_id}", response_model=LLMConfigResponse)
async def update_llm_config(
    config_id: uuid.UUID, data: LLMConfigUpdate, db: AsyncSession = Depends(get_db),
):
    config = await db.get(LLMConfig, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="LLM config not found")
    if data.provider is not None:
        config.provider = data.provider
    if data.model_name is not None:
        config.model_name = data.model_name
    if data.api_key is not None:
        config.api_key_encrypted = encrypt_key(data.api_key) if data.api_key else None
    if data.base_url is not None:
        config.base_url = data.base_url or None
    if data.proxy_mode is not None:
        config.proxy_mode = data.proxy_mode
    await db.commit()
    await db.refresh(config)
    return _to_response(config)


@router.patch("/llm/{config_id}/default", response_model=LLMConfigResponse)
async def set_default_llm_config(
    config_id: uuid.UUID, db: AsyncSession = Depends(get_db),
):
    config = await db.get(LLMConfig, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="LLM config not found")
    result = await db.execute(select(LLMConfig).where(LLMConfig.is_default.is_(True)))
    for existing in result.scalars().all():
        existing.is_default = False
    config.is_default = True
    await db.commit()
    await db.refresh(config)
    return _to_response(config)


@router.post("/llm/{config_id}/test")
async def test_llm_config(
    config_id: uuid.UUID, db: AsyncSession = Depends(get_db),
):
    config = await db.get(LLMConfig, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="LLM config not found")
    if not config.api_key_encrypted:
        return {"success": False, "message": "未设置 API Key"}
    if not config.base_url:
        return {"success": False, "message": "未设置 Base URL"}

    api_key = decrypt_key(config.api_key_encrypted)
    trust_env = config.proxy_mode != "direct"

    try:
        async with httpx.AsyncClient(
            base_url=config.base_url,
            timeout=httpx.Timeout(30, connect=10),
            trust_env=trust_env,
        ) as client:
            resp = await client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": config.model_name,
                    "messages": [{"role": "user", "content": "Hello"}],
                    "max_tokens": 32,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return {"success": True, "message": content[:100]}
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:300]
        return {"success": False, "message": f"HTTP {exc.response.status_code}: {body}"}
    except Exception as exc:
        return {"success": False, "message": str(exc)[:300]}


@router.delete("/llm/{config_id}", status_code=204)
async def delete_llm_config(config_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    config = await db.get(LLMConfig, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="LLM config not found")
    was_default = config.is_default
    await db.delete(config)
    await db.flush()
    if was_default:
        result = await db.execute(
            select(LLMConfig).order_by(LLMConfig.created_at.asc()).limit(1)
        )
        next_config = result.scalar_one_or_none()
        if next_config:
            next_config.is_default = True
    await db.commit()
