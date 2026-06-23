"""Persisted Agent CLI provider settings.

Settings are stored in the existing settings table so a backend restart does
not silently drop Workbench Agent CLI configuration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite

from app.config import settings

AGENT_PROVIDER_KEYS = (
    "claude_code_command",
    "claude_code_config_path",
    "claude_code_fallback_commands",
    "claude_code_mcp_profiles",
    "opencode_command",
    "opencode_fallback_commands",
    "opencode_mcp_profiles",
    "external_agent_custom_providers",
)

AGENT_PROVIDER_JSON_KEYS = frozenset({
    "claude_code_fallback_commands",
    "claude_code_mcp_profiles",
    "opencode_fallback_commands",
    "opencode_mcp_profiles",
    "external_agent_custom_providers",
})


async def apply_persisted_agent_provider_settings(
    sqlite_db: str | Path | None = None,
) -> dict[str, Any]:
    """Load persisted Agent provider settings into runtime settings.

    Missing databases or tables are ignored because fresh deployments may not
    have saved provider settings yet.
    """

    db_path = str(sqlite_db or settings.sqlite_db or "").strip()
    if not db_path:
        return {}
    if db_path != ":memory:":
        try:
            if not Path(db_path).exists():
                return {}
        except OSError:
            return {}
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT key, value FROM settings WHERE key IN ({})".format(
                    ",".join("?" * len(AGENT_PROVIDER_KEYS))
                ),
                AGENT_PROVIDER_KEYS,
            ) as cur:
                rows = await cur.fetchall()
    except (aiosqlite.Error, OSError):
        return {}

    payload: dict[str, Any] = {}
    for row in rows:
        key = str(row["key"])
        value = row["value"]
        if key in AGENT_PROVIDER_JSON_KEYS:
            payload[key] = _json_setting_value(value, _runtime_default_for_key(key))
        else:
            payload[key] = str(value or "")
    apply_agent_provider_settings(payload)
    return payload


def apply_agent_provider_settings(payload: dict[str, Any]) -> None:
    for key in AGENT_PROVIDER_KEYS:
        if key in payload:
            setattr(settings, key, payload[key])


def runtime_agent_provider_defaults() -> dict[str, Any]:
    return {
        "claude_code_command": settings.claude_code_command,
        "claude_code_config_path": settings.claude_code_config_path,
        "claude_code_fallback_commands": list_setting_value(settings.claude_code_fallback_commands),
        "claude_code_mcp_profiles": list_setting_value(settings.claude_code_mcp_profiles),
        "opencode_command": settings.opencode_command,
        "opencode_fallback_commands": list_setting_value(settings.opencode_fallback_commands),
        "opencode_mcp_profiles": list_setting_value(settings.opencode_mcp_profiles),
        "external_agent_custom_providers": custom_provider_setting_value(
            settings.external_agent_custom_providers
        ),
    }


def list_setting_value(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = [part.strip() for part in text.split(",") if part.strip()]
        value = parsed
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def custom_provider_setting_value(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _runtime_default_for_key(key: str) -> Any:
    return runtime_agent_provider_defaults().get(key, [] if key in AGENT_PROVIDER_JSON_KEYS else "")


def _json_setting_value(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except (TypeError, json.JSONDecodeError):
        return default
