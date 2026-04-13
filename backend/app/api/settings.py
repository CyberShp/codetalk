import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.llm_config import LLMConfig
from app.schemas.llm_config import LLMConfigCreate, LLMConfigResponse
from app.utils.crypto import encrypt_key

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


@router.delete("/llm/{config_id}", status_code=204)
async def delete_llm_config(config_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    config = await db.get(LLMConfig, config_id)
    if not config:
        raise HTTPException(status_code=404, detail="LLM config not found")
    await db.delete(config)
    await db.commit()
