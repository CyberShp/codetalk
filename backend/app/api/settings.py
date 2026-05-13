import uuid
from datetime import datetime, timezone
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.database import get_db

router = APIRouter(prefix="/api/settings", tags=["设置管理"])


# --- LLM Config schemas ---

class LLMConfigCreate(BaseModel):
    name: str
    api_type: str                   # "anthropic" | "openai_compat"
    base_url: str
    api_key: str
    model: str
    max_tokens: int = 4096
    temperature: float = 0.3
    config_json: str | None = None  # raw JSON override from user
    is_chat_model: bool = True
    is_embedding_model: bool = False


class LLMConfigUpdate(BaseModel):
    name: str | None = None
    api_type: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    config_json: str | None = None
    is_chat_model: bool | None = None
    is_embedding_model: bool | None = None


class LLMConfigResponse(BaseModel):
    id: str
    name: str
    api_type: str
    base_url: str
    model: str
    max_tokens: int
    temperature: float
    config_json: str | None
    is_chat_model: bool
    is_embedding_model: bool
    created_at: str


def _row_to_llm(row: aiosqlite.Row) -> dict:
    d = dict(row)
    d["is_chat_model"] = bool(d.get("is_chat_model", 1))
    d["is_embedding_model"] = bool(d.get("is_embedding_model", 0))
    return d


# --- General settings schemas ---

class GeneralSettings(BaseModel):
    proxy_mode: str = "none"        # "none" | "system" | "custom"
    proxy_url: str = ""
    ssl_cert_path: str = ""
    active_chat_model_id: str = ""
    active_embedding_model_id: str = ""


# --- LLM Config endpoints ---

@router.get("/llm", response_model=list[LLMConfigResponse])
async def list_llm_configs(db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute("SELECT * FROM llm_configs ORDER BY created_at DESC") as cur:
        rows = await cur.fetchall()
    return [_row_to_llm(r) for r in rows]


@router.post("/llm", response_model=LLMConfigResponse, status_code=201)
async def create_llm_config(data: LLMConfigCreate, db: aiosqlite.Connection = Depends(get_db)):
    if data.api_type not in ("anthropic", "openai_compat"):
        raise HTTPException(status_code=422, detail="api_type 必须为 anthropic 或 openai_compat")

    cfg_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """INSERT INTO llm_configs
           (id, name, api_type, base_url, api_key, model, max_tokens, temperature,
            config_json, is_chat_model, is_embedding_model, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (cfg_id, data.name, data.api_type, data.base_url, data.api_key, data.model,
         data.max_tokens, data.temperature, data.config_json,
         int(data.is_chat_model), int(data.is_embedding_model), now),
    )
    await db.commit()

    async with db.execute("SELECT * FROM llm_configs WHERE id = ?", (cfg_id,)) as cur:
        row = await cur.fetchone()
    return _row_to_llm(row)


@router.put("/llm/{cfg_id}", response_model=LLMConfigResponse)
async def update_llm_config(
    cfg_id: str, data: LLMConfigUpdate, db: aiosqlite.Connection = Depends(get_db)
):
    async with db.execute("SELECT * FROM llm_configs WHERE id = ?", (cfg_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="LLM 配置不存在")

    updates: dict[str, Any] = {k: v for k, v in data.model_dump(exclude_none=True).items()}
    if "is_chat_model" in updates:
        updates["is_chat_model"] = int(updates["is_chat_model"])
    if "is_embedding_model" in updates:
        updates["is_embedding_model"] = int(updates["is_embedding_model"])

    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        await db.execute(
            f"UPDATE llm_configs SET {set_clause} WHERE id = ?",
            (*updates.values(), cfg_id),
        )
        await db.commit()

    async with db.execute("SELECT * FROM llm_configs WHERE id = ?", (cfg_id,)) as cur:
        row = await cur.fetchone()
    return _row_to_llm(row)


@router.delete("/llm/{cfg_id}", status_code=204)
async def delete_llm_config(cfg_id: str, db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute("SELECT id FROM llm_configs WHERE id = ?", (cfg_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="LLM 配置不存在")
    await db.execute("DELETE FROM llm_configs WHERE id = ?", (cfg_id,))
    await db.commit()


@router.post("/llm/test")
async def test_llm_connection(data: LLMConfigCreate):
    # Sprint 1 stub — real connectivity test added in Sprint 3
    return {"success": True, "message": "连接测试将在 Sprint 3 实现，当前返回模拟成功"}


# --- General settings endpoints ---

_GENERAL_KEYS = ("proxy_mode", "proxy_url", "ssl_cert_path",
                 "active_chat_model_id", "active_embedding_model_id")


@router.get("/general", response_model=GeneralSettings)
async def get_general_settings(db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute(
        "SELECT key, value FROM settings WHERE key IN ({})".format(
            ",".join("?" * len(_GENERAL_KEYS))
        ),
        _GENERAL_KEYS,
    ) as cur:
        rows = await cur.fetchall()

    stored = {r["key"]: r["value"] for r in rows}
    defaults = GeneralSettings().model_dump()
    return {k: stored.get(k, defaults[k]) for k in defaults}


@router.put("/general", response_model=GeneralSettings)
async def update_general_settings(data: GeneralSettings, db: aiosqlite.Connection = Depends(get_db)):
    for key, value in data.model_dump().items():
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
    await db.commit()
    return data
