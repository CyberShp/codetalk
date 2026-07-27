import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.database import get_db
from app.services.agent_provider_settings import (
    AGENT_PROVIDER_JSON_KEYS,
    apply_agent_provider_settings,
    read_agent_provider_settings_from_db,
)
from app.services.external_agent_discovery import redact_agent_diagnostic_text
from app.services.network_policy import (
    NetworkEgressBlocked,
    effective_network_mode,
    resolve_agent_network_context,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["设置管理"])


def _deployment_network_policy_snapshot() -> dict[str, object]:
    """Return a read-only, credential-free deployment policy summary."""
    from app.config import settings

    cli_context = resolve_agent_network_context(requires_network=True)
    approved_proxy_config_id = str(settings.approved_proxy_config_id or "").strip()
    approved_proxy_configured = bool(
        approved_proxy_config_id and str(settings.approved_proxy_url or "").strip()
    )
    deployment_egress_policy_id = str(settings.deployment_egress_policy_id or "").strip()
    return {
        "mode": effective_network_mode(),
        "policy_id": str(settings.intranet_network_policy_id or ""),
        "boundary": cli_context.boundary,
        "approved_proxy_configured": approved_proxy_configured,
        "approved_proxy_config_id": approved_proxy_config_id or None,
        "approved_no_proxy": bool(str(settings.approved_no_proxy or "").strip()),
        "approved_ca_configured": bool(str(settings.approved_ca_bundle_path or "").strip()),
        "deployment_egress_policy_id": deployment_egress_policy_id or None,
        "telemetry": "disabled",
        "remote_tracing": "disabled",
        "hosted_mcp": "forbidden",
        "cli_network_ready": cli_context.allowed,
        "cli_block_reason": None if cli_context.allowed else cli_context.reason,
        "cli_remediation": None if cli_context.allowed else cli_context.remediation,
        "source": "deployment",
    }


def _llm_connection_failure_code(error: Exception | str) -> str:
    """Return a stable, non-sensitive diagnostic code for a failed probe."""
    raw = redact_agent_diagnostic_text(str(error))
    normalized = raw.lower()
    if "内网部署策略未批准" in raw:
        return "network_policy_blocked"
    if isinstance(error, NetworkEgressBlocked) or "出站策略拒绝" in raw:
        technical = re.search(r"([a-z][a-z0-9_]+)$", raw)
        return technical.group(1) if technical else "network_policy_blocked"
    if "未知的 api_type" in raw:
        return "unsupported_api_type"
    if "403" in normalized:
        return "http_403"
    if any(marker in normalized for marker in ("certificate", "ssl", "cert_verify")):
        return "tls_ca_verification_failed"
    if "proxy" in normalized or "代理" in raw:
        return "approved_proxy_connection_failed"
    return "model_connection_failed"


def _format_llm_connection_failure(error: Exception | str) -> str:
    """Make probe errors actionable without echoing endpoint credentials."""
    code = _llm_connection_failure_code(error)
    if code in {
        "host_not_allowlisted",
        "direct_address_not_allowlisted",
        "model_endpoint_path_forbidden",
        "strict_compliance_network_disabled",
        "network_policy_blocked",
    }:
        reason = {
            "host_not_allowlisted": "模型地址未获管理员批准",
            "direct_address_not_allowlisted": "模型地址未获管理员批准",
            "model_endpoint_path_forbidden": "模型接口路径未获管理员批准",
            "strict_compliance_network_disabled": "严格合规模式禁止此模型连接",
            "network_policy_blocked": "模型地址未获管理员批准",
        }[code]
        return (
            f"部署网络策略阻止模型连接：{reason}。"
            "请联系管理员检查模型地址和部署出站边界。"
        )
    if code == "http_403":
        return "模型服务拒绝访问。请检查部署批准的模型地址、代理和凭据。"
    if code == "tls_ca_verification_failed":
        return "企业 CA 证书校验失败。请联系管理员配置部署 CA 证书。"
    if code == "approved_proxy_connection_failed":
        return "批准代理连接失败。请联系管理员检查部署代理配置。"
    if code == "unsupported_api_type":
        return "不支持的模型接口类型。请在模型设置中选择受支持的接口类型。"
    return "模型连接失败。请检查模型配置或联系管理员。"


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
    behavior_claim_audit_model_id: str = ""


class AgentProviderSettingsCustomProvider(BaseModel):
    id: str
    command: str
    prompt_transport: str = "stdin"
    fallback_commands: list[str] = Field(default_factory=list)
    readonly_args: list[str] = Field(default_factory=list)
    env_hints: dict[str, str] = Field(default_factory=dict)
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

    @field_validator("env_hints")
    @classmethod
    def _valid_env_hints(cls, value: dict[str, str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, item in (value or {}).items():
            name = str(key or "").strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError(f"unsupported env var name: {name}")
            result[name] = str(item)
        return result


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

    async with db.execute("SELECT * FROM llm_configs WHERE id = ?", (cfg_id,)) as cur:
        row = await cur.fetchone()
    return _row_to_llm(row)


@router.delete("/llm/{cfg_id}", status_code=204)
async def delete_llm_config(cfg_id: str, db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute("SELECT id FROM llm_configs WHERE id = ?", (cfg_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="LLM 配置不存在")

    await db.execute("DELETE FROM llm_configs WHERE id = ?", (cfg_id,))
    # Clear any active model reference that pointed at the deleted config.
    await db.execute(
        "UPDATE settings SET value = '' "
        "WHERE key IN ('active_chat_model_id', 'active_embedding_model_id', "
        "'behavior_claim_audit_model_id') "
        "AND value = ?",
        (cfg_id,),
    )
    await db.commit()


@router.post("/llm/test")
async def test_llm_connection(
    data: LLMConfigCreate, db: aiosqlite.Connection = Depends(get_db)
):
    """Test the same deployment-authorized inference route used at runtime."""
    from app.llm.factory import _create_runtime_llm_client, _load_general_settings

    client = None
    try:
        general = await _load_general_settings(db)
        client = _create_runtime_llm_client(
            api_type=data.api_type,
            base_url=data.base_url,
            api_key=data.api_key,
            model=data.model,
            general=general,
        )

        success, message = await client.health_check()
        return {
            "success": success,
            "message": message if success else _format_llm_connection_failure(message),
            **({} if success else {"code": _llm_connection_failure_code(message)}),
        }

    except Exception as exc:
        return {
            "success": False,
            "message": _format_llm_connection_failure(exc),
            "code": _llm_connection_failure_code(exc),
        }
    finally:
        if client is not None:
            await client.close()


# --- General settings endpoints ---

@router.get("/network-policy")
async def get_deployment_network_policy():
    """Expose a deployment-owned policy snapshot without mutable secrets."""
    return _deployment_network_policy_snapshot()

_GENERAL_KEYS = ("proxy_mode", "proxy_url", "ssl_cert_path",
                 "active_chat_model_id", "active_embedding_model_id",
                 "behavior_claim_audit_model_id")

async def _read_active_ids(db: aiosqlite.Connection) -> tuple[str, str]:
    """Return (active_chat_model_id, active_embedding_model_id) from settings table."""
    async with db.execute(
        "SELECT key, value FROM settings "
        "WHERE key IN ('active_chat_model_id', 'active_embedding_model_id')"
    ) as cur:
        rows = await cur.fetchall()
    stored = {r["key"]: r["value"] for r in rows}
    return stored.get("active_chat_model_id", ""), stored.get("active_embedding_model_id", "")


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
