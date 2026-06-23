import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.config import settings as app_settings
from app.database import get_db
from app.services.agent_provider_settings import (
    AGENT_PROVIDER_JSON_KEYS,
    apply_agent_provider_settings,
    read_agent_provider_settings_from_db,
)

logger = logging.getLogger(__name__)

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


class AgentProviderSettingsCustomProvider(BaseModel):
    id: str
    command: str
    prompt_transport: str = "stdin"
    fallback_commands: list[str] = Field(default_factory=list)
    readonly_args: list[str] = Field(default_factory=list)
    supports_mcp: bool = False
    mcp_profiles: list[str] = Field(default_factory=list)
    supports_artifact_export: bool = True
    supports_json_output: bool = True

    @field_validator("id", "command")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("prompt_transport")
    @classmethod
    def _valid_prompt_transport(cls, value: str) -> str:
        text = str(value or "auto").strip() or "auto"
        allowed = {"auto", "stdin", "claude_print_arg", "opencode_run_arg", "argv_last"}
        if text not in allowed:
            raise ValueError(f"unsupported prompt_transport: {text}")
        return text


class AgentProviderSettings(BaseModel):
    claude_code_command: str = "ccr code"
    claude_code_config_path: str = ""
    claude_code_fallback_commands: list[str] = Field(default_factory=list)
    claude_code_mcp_profiles: list[str] = Field(default_factory=list)
    opencode_command: str = "opencode"
    opencode_fallback_commands: list[str] = Field(default_factory=list)
    opencode_mcp_profiles: list[str] = Field(default_factory=list)
    external_agent_custom_providers: list[AgentProviderSettingsCustomProvider] = Field(default_factory=list)


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

        # Re-sync deepwiki if the updated config is currently active.
        active_chat_id, active_embed_id = await _read_active_ids(db)
        if cfg_id in (active_chat_id, active_embed_id):
            env_changed = await _sync_deepwiki_env(db, active_chat_id, active_embed_id)
            if env_changed:
                _schedule_deepwiki_restart()

    async with db.execute("SELECT * FROM llm_configs WHERE id = ?", (cfg_id,)) as cur:
        row = await cur.fetchone()
    return _row_to_llm(row)


@router.delete("/llm/{cfg_id}", status_code=204)
async def delete_llm_config(cfg_id: str, db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute("SELECT id FROM llm_configs WHERE id = ?", (cfg_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="LLM 配置不存在")

    # Capture active IDs before deletion so we know whether to sync deepwiki.
    active_chat_id, active_embed_id = await _read_active_ids(db)
    was_active = cfg_id in (active_chat_id, active_embed_id)

    await db.execute("DELETE FROM llm_configs WHERE id = ?", (cfg_id,))
    # Clear any active model reference that pointed at the deleted config.
    await db.execute(
        "UPDATE settings SET value = '' "
        "WHERE key IN ('active_chat_model_id', 'active_embedding_model_id') "
        "AND value = ?",
        (cfg_id,),
    )
    await db.commit()

    if was_active:
        # Re-read now-cleared active IDs and strip the dead keys from deepwiki .env.
        new_chat_id, new_embed_id = await _read_active_ids(db)
        env_changed = await _sync_deepwiki_env(db, new_chat_id, new_embed_id)
        if env_changed:
            _schedule_deepwiki_restart()


@router.post("/llm/test")
async def test_llm_connection(
    data: LLMConfigCreate, db: aiosqlite.Connection = Depends(get_db)
):
    """Test LLM connectivity using global proxy/SSL settings."""
    from app.llm.anthropic import AnthropicClient
    from app.llm.factory import _load_general_settings, _resolve_proxy
    from app.llm.openai_compat import OpenAICompatClient

    try:
        general = await _load_general_settings(db)
        proxy_url, ssl_cert, force_direct = _resolve_proxy(general)

        kwargs = {
            "base_url": data.base_url,
            "api_key": data.api_key,
            "model": data.model,
            "proxy_url": proxy_url,
            "ssl_cert_path": ssl_cert,
            "force_direct": force_direct,
        }

        if data.api_type == "anthropic":
            client = AnthropicClient(**kwargs)
        elif data.api_type == "openai_compat":
            client = OpenAICompatClient(**kwargs)
        else:
            return {"success": False, "message": f"未知的 api_type: {data.api_type}"}

        success, message = await client.health_check()
        await client.close()
        return {"success": success, "message": message}

    except Exception as exc:
        return {"success": False, "message": f"连接失败: {exc}"}


# --- General settings endpoints ---

_GENERAL_KEYS = ("proxy_mode", "proxy_url", "ssl_cert_path",
                 "active_chat_model_id", "active_embedding_model_id")

_CHAT_ENV_KEYS = frozenset({"OPENAI_BASE_URL", "OPENAI_API_KEY", "LLM_MODEL"})


async def _read_active_ids(db: aiosqlite.Connection) -> tuple[str, str]:
    """Return (active_chat_model_id, active_embedding_model_id) from settings table."""
    async with db.execute(
        "SELECT key, value FROM settings "
        "WHERE key IN ('active_chat_model_id', 'active_embedding_model_id')"
    ) as cur:
        rows = await cur.fetchall()
    stored = {r["key"]: r["value"] for r in rows}
    return stored.get("active_chat_model_id", ""), stored.get("active_embedding_model_id", "")
_EMBED_ENV_KEYS = frozenset({
    "DEEPWIKI_EMBEDDING_BASE_URL",
    "DEEPWIKI_EMBEDDING_API_KEY",
    "DEEPWIKI_EMBEDDING_MODEL",
    "DEEPWIKI_EMBEDDER_TYPE",
    "OPENAI_EMBEDDING_MODEL",
})


def _as_openai_sdk_base_url(base_url: str) -> str:
    """Normalize CodeTalk's root URL into the base URL expected by OpenAI SDK clients."""
    value = (base_url or "").rstrip("/")
    if not value:
        return ""
    if value.endswith("/v1"):
        return value
    return f"{value}/v1"


def _sync_deepwiki_embedder_json(deepwiki_path: str, model: str) -> bool:
    """Make deepwiki-open's OpenAI embedder consume the active embedding config.

    DeepWiki's OpenAIClient reads initialize_kwargs from api/config/embedder.json.
    Keeping credentials as env placeholders avoids writing secrets into JSON while
    still letting the active embedding base URL/key/model differ from chat config.
    """
    if not deepwiki_path or not model:
        return False

    config_path = Path(deepwiki_path) / "api" / "config" / "embedder.json"
    if not config_path.exists():
        return False

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("settings: failed to read deepwiki embedder.json: %s", exc)
        return False

    embedder = config.setdefault("embedder", {})
    before = json.dumps(embedder, sort_keys=True, ensure_ascii=False)
    embedder["client_class"] = "OpenAIClient"
    embedder["initialize_kwargs"] = {
        "api_key": "${DEEPWIKI_EMBEDDING_API_KEY}",
        "base_url": "${DEEPWIKI_EMBEDDING_BASE_URL}",
    }
    # Keep batches modest for intranet/OpenAI-compatible providers; large
    # batches were a common source of opaque 500s in DeepWiki's RAG setup.
    embedder["batch_size"] = 10
    embedder["model_kwargs"] = {"model": model}

    after = json.dumps(embedder, sort_keys=True, ensure_ascii=False)
    if before == after:
        return False

    try:
        config_path.write_text(
            json.dumps(config, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        logger.info("settings: synced deepwiki embedder.json model=%s", model)
        return True
    except OSError as exc:
        logger.warning("settings: failed to write deepwiki embedder.json: %s", exc)
        return False


async def _sync_deepwiki_env(
    db: aiosqlite.Connection,
    active_chat_id: str,
    active_embedding_id: str,
) -> bool:
    """Write active LLM model config into deepwiki's .env for runtime use.

    Returns True if the .env file was written; False if skipped.
    Runs silently: errors are logged but never propagate to the caller so
    a missing deepwiki path or DB row never breaks the settings save.
    """
    deepwiki_path = app_settings.deepwiki_path
    if not deepwiki_path:
        return False

    env_updates: dict[str, str] = {}
    env_removals: set[str] = set()

    if active_chat_id:
        async with db.execute(
            "SELECT base_url, api_key, model FROM llm_configs WHERE id = ?",
            (active_chat_id,),
        ) as cur:
            row = await cur.fetchone()
        if row:
            if row["base_url"]:
                env_updates["OPENAI_BASE_URL"] = _as_openai_sdk_base_url(row["base_url"])
            if row["api_key"]:
                env_updates["OPENAI_API_KEY"] = row["api_key"]
            if row["model"]:
                env_updates["LLM_MODEL"] = row["model"]
    else:
        env_removals.update(_CHAT_ENV_KEYS)

    if active_embedding_id:
        async with db.execute(
            "SELECT base_url, api_key, model FROM llm_configs WHERE id = ?",
            (active_embedding_id,),
        ) as cur:
            row = await cur.fetchone()
        if row:
            if row["base_url"]:
                env_updates["DEEPWIKI_EMBEDDING_BASE_URL"] = _as_openai_sdk_base_url(row["base_url"])
            if row["api_key"]:
                env_updates["DEEPWIKI_EMBEDDING_API_KEY"] = row["api_key"]
            if row["model"]:
                env_updates["DEEPWIKI_EMBEDDING_MODEL"] = row["model"]
                env_updates["OPENAI_EMBEDDING_MODEL"] = row["model"]
            env_updates["DEEPWIKI_EMBEDDER_TYPE"] = "openai"
    else:
        env_removals.update(_EMBED_ENV_KEYS)

    if not env_updates and not env_removals:
        return False

    dot_env = Path(deepwiki_path) / ".env"
    # Nothing to remove from a file that doesn't exist yet.
    if not env_updates and not dot_env.exists():
        return False

    managed_keys = env_removals | set(env_updates.keys())
    try:
        existing: list[str] = (
            dot_env.read_text(encoding="utf-8").splitlines()
            if dot_env.exists()
            else []
        )
        kept = [
            ln for ln in existing
            if not ln.strip()
            or ln.startswith("#")
            or ln.split("=", 1)[0].strip() not in managed_keys
        ]
        new_lines = [f"{k}={v}" for k, v in env_updates.items()]
        dot_env.write_text("\n".join(kept + new_lines) + "\n", encoding="utf-8")
        if "DEEPWIKI_EMBEDDING_MODEL" in env_updates:
            _sync_deepwiki_embedder_json(
                deepwiki_path,
                env_updates["DEEPWIKI_EMBEDDING_MODEL"],
            )
        logger.info(
            "settings: synced deepwiki .env (updated=%s removed=%s)",
            list(env_updates.keys()),
            list(env_removals - set(env_updates.keys())),
        )
        return True
    except OSError as exc:
        logger.warning("settings: failed to sync deepwiki .env: %s", exc)
        return False


def _schedule_deepwiki_restart() -> None:
    """Schedule a background restart of deepwiki-api to apply updated .env values."""
    from app.services.process_manager import ProcessManager
    pm = ProcessManager.get_instance()
    mp = pm._processes.get("deepwiki-api")
    if mp is not None and mp.status == "running":
        asyncio.create_task(pm.restart("deepwiki-api"))
        logger.info("settings: scheduled deepwiki-api restart to apply new .env")


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
    env_changed = await _sync_deepwiki_env(db, data.active_chat_model_id, data.active_embedding_model_id)
    if env_changed:
        _schedule_deepwiki_restart()
    return data


@router.get("/agent-providers", response_model=AgentProviderSettings)
async def get_agent_provider_settings(db: aiosqlite.Connection = Depends(get_db)):
    return AgentProviderSettings(**(await read_agent_provider_settings_from_db(db)))


@router.put("/agent-providers", response_model=AgentProviderSettings)
async def update_agent_provider_settings(
    data: AgentProviderSettings,
    db: aiosqlite.Connection = Depends(get_db),
):
    payload = data.model_dump()
    for key, value in payload.items():
        stored = (
            json.dumps(value, ensure_ascii=False, sort_keys=True)
            if key in AGENT_PROVIDER_JSON_KEYS
            else str(value)
        )
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, stored),
        )
    await db.commit()
    apply_agent_provider_settings(payload)
    return data
