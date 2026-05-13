"""Factory that creates the right LLM client from a DB config record."""

import json
import logging

import aiosqlite

from app.config import settings
from app.llm.anthropic import AnthropicClient
from app.llm.base import BaseLLMClient
from app.llm.openai_compat import OpenAICompatClient

logger = logging.getLogger(__name__)


async def _load_general_settings(db: aiosqlite.Connection) -> dict[str, str]:
    """Load proxy/ssl settings from the settings table."""
    keys = ("proxy_mode", "proxy_url", "ssl_cert_path")
    placeholders = ",".join("?" * len(keys))
    async with db.execute(
        f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
        keys,
    ) as cur:
        rows = await cur.fetchall()
    return {r["key"]: r["value"] for r in rows}


def _resolve_proxy(general: dict[str, str]) -> str | None:
    """Determine the proxy URL from general settings."""
    mode = general.get("proxy_mode", "none")
    if mode == "custom":
        url = general.get("proxy_url", "")
        return url if url else None
    return None


async def create_llm_client(config_id: str) -> BaseLLMClient:
    """Read an llm_configs row and return the appropriate client instance.

    Args:
        config_id: UUID of the llm_configs row.

    Returns:
        An AnthropicClient or OpenAICompatClient ready to use.

    Raises:
        ValueError: If config_id not found or api_type is unknown.
    """
    async with aiosqlite.connect(settings.sqlite_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM llm_configs WHERE id = ?", (config_id,)
        ) as cur:
            row = await cur.fetchone()

        if not row:
            raise ValueError(f"LLM 配置不存在: {config_id}")

        cfg = dict(row)
        general = await _load_general_settings(db)

    api_type: str = cfg["api_type"]
    base_url: str = cfg["base_url"]
    api_key: str = cfg["api_key"]
    model: str = cfg["model"]

    # config_json may contain overrides
    if cfg.get("config_json"):
        try:
            overrides = json.loads(cfg["config_json"])
            base_url = overrides.get("base_url", base_url)
            model = overrides.get("model", model)
        except json.JSONDecodeError:
            logger.warning("无法解析 config_json，使用默认配置")

    proxy_url = _resolve_proxy(general)
    ssl_cert = general.get("ssl_cert_path") or None

    if api_type == "anthropic":
        return AnthropicClient(
            base_url=base_url,
            api_key=api_key,
            model=model,
            proxy_url=proxy_url,
            ssl_cert_path=ssl_cert,
        )
    if api_type == "openai_compat":
        return OpenAICompatClient(
            base_url=base_url,
            api_key=api_key,
            model=model,
            proxy_url=proxy_url,
            ssl_cert_path=ssl_cert,
        )

    raise ValueError(f"未知的 api_type: {api_type}")


async def create_llm_client_from_active() -> BaseLLMClient:
    """Create an LLM client from the active_chat_model_id setting.

    Raises:
        ValueError: If no active chat model is configured.
    """
    async with aiosqlite.connect(settings.sqlite_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT value FROM settings WHERE key = 'active_chat_model_id'"
        ) as cur:
            row = await cur.fetchone()

    if not row or not row["value"]:
        raise ValueError("未配置活跃的聊天模型，请先在设置中选择 LLM 模型")

    return await create_llm_client(row["value"])
