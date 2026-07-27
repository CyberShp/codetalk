"""Factory that creates the right LLM client from a DB config record."""

import json
import logging
import sqlite3

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


def _resolve_proxy(
    general: dict[str, str],
) -> tuple[str | None, str | None, bool]:
    """Determine proxy/SSL/direct-connect from general settings.

    Returns (proxy_url, ssl_cert_path, force_direct).
    force_direct=True → httpx uses trust_env=False to bypass system proxy.
    """
    ssl_cert = general.get("ssl_cert_path") or None
    if settings.intranet_network_mode:
        # Model requests have their own explicit endpoint admission. Do not let
        # environment or user-configured proxies turn that narrow route into a
        # general external transport.
        return None, ssl_cert, True
    mode = general.get("proxy_mode", "none")
    if mode == "none":
        return None, ssl_cert, True
    if mode == "custom":
        url = general.get("proxy_url", "")
        return (url or None), ssl_cert, False
    # "system" — let httpx discover system proxy via environment
    return None, ssl_cert, False


async def create_llm_client(
    config_id: str,
    *,
    model_override: str | None = None,
) -> BaseLLMClient:
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
    if model_override:
        model = model_override

    # A saved model selects an adapter/model, but never expands deployment
    # egress policy. The client validates the configured endpoint against the
    # deployment allow-list and narrow inference route immediately before I/O.

    proxy_url, ssl_cert, force_direct = _resolve_proxy(general)

    if api_type == "anthropic":
        return AnthropicClient(
            base_url=base_url,
            api_key=api_key,
            model=model,
            proxy_url=proxy_url,
            ssl_cert_path=ssl_cert,
            force_direct=force_direct,
            enforce_network_policy=True,
            configured_model_endpoint=True,
        )
    if api_type == "openai_compat":
        return OpenAICompatClient(
            base_url=base_url,
            api_key=api_key,
            model=model,
            proxy_url=proxy_url,
            ssl_cert_path=ssl_cert,
            force_direct=force_direct,
            enforce_network_policy=True,
            configured_model_endpoint=True,
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


async def create_behavior_claim_audit_llm_client(
) -> tuple[BaseLLMClient, str, str] | None:
    """Create the explicitly configured, independent L2 audit model.

    This intentionally does not fall back to the active generator model.  An
    absent audit configuration is a quality-gate precondition failure, rather
    than a reason to let the generator validate its own output.
    """
    async with aiosqlite.connect(settings.sqlite_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT value FROM settings WHERE key = 'behavior_claim_audit_model_id'"
        ) as cur:
            selected = await cur.fetchone()
        config_id = str(selected["value"] or "") if selected else ""
        if not config_id:
            return None
        async with db.execute(
            "SELECT model FROM llm_configs WHERE id = ?", (config_id,)
        ) as cur:
            config = await cur.fetchone()
    if not config:
        raise ValueError("独立质量核验模型配置不存在，请在设置中重新选择")
    return await create_llm_client(config_id), config_id, str(config["model"] or "")


async def create_quality_repair_llm_client() -> BaseLLMClient | None:
    """Create a separate client for bounded quality repairs.

    The configured independent audit model is also the right default for a
    repair that must correct a fast generator's factual overreach.  This
    returns a fresh client because validation and repair may overlap in a
    workflow lifecycle and must not share mutable provider state.
    """
    try:
        async with aiosqlite.connect(settings.sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT value FROM settings WHERE key = 'behavior_claim_audit_model_id'"
            ) as cur:
                selected = await cur.fetchone()
    except sqlite3.OperationalError as exc:
        logger.warning(
            "Quality repair model route unavailable; using primary model: %s", exc
        )
        return None
    config_id = str(selected["value"] or "") if selected else ""
    return await create_llm_client(config_id) if config_id else None


def _automatic_source_analysis_model(
    *,
    api_type: str,
    base_url: str,
    model: str,
) -> str | None:
    normalized_url = base_url.rstrip("/").lower()
    if (
        api_type == "openai_compat"
        and normalized_url in {"https://api.deepseek.com", "https://api.deepseek.com/v1"}
        and model.strip().lower() in {"deepseek-reasoner", "deepseek-v4-pro"}
    ):
        return "deepseek-v4-flash"
    return None


async def create_source_analysis_llm_client() -> BaseLLMClient | None:
    """Resolve the optional fast-model route for staged source analysis.

    The selector may be an LLM config id or the exact configured model name.
    Missing/invalid selectors deliberately fall back to the caller's active
    client so context and output limits still apply.
    """
    selector = str(settings.source_analysis_model or "auto").strip()
    if not selector:
        return None
    try:
        async with aiosqlite.connect(settings.sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            if selector.lower() != "auto":
                async with db.execute(
                    """
                    SELECT id
                    FROM llm_configs
                    WHERE id = ? OR model = ?
                    ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END, created_at DESC
                    LIMIT 1
                    """,
                    (selector, selector, selector),
                ) as cur:
                    row = await cur.fetchone()
                if row:
                    return await create_llm_client(str(row["id"]))
            async with db.execute(
                """
                SELECT c.id, c.api_type, c.base_url, c.model
                FROM settings s
                JOIN llm_configs c ON c.id = s.value
                WHERE s.key = 'active_chat_model_id'
                LIMIT 1
                """
            ) as cur:
                active = await cur.fetchone()
    except sqlite3.OperationalError as exc:
        logger.warning(
            "Source analysis model route unavailable; using active model: %s",
            exc,
        )
        return None
    if selector.lower() != "auto":
        logger.warning(
            "source_analysis_model=%s does not match an LLM config; using active model",
            selector,
        )
        return None
    if not active:
        return None
    automatic_model = _automatic_source_analysis_model(
        api_type=str(active["api_type"] or ""),
        base_url=str(active["base_url"] or ""),
        model=str(active["model"] or ""),
    )
    if not automatic_model:
        return None
    logger.info(
        "Source analysis auto-routed from %s to %s",
        active["model"],
        automatic_model,
    )
    return await create_llm_client(
        str(active["id"]),
        model_override=automatic_model,
    )
