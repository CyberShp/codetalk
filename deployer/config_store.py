"""Simple JSON file-based config storage for the deployer."""

import json
import os
from pathlib import Path

CONFIG_PATH = Path(
    os.environ.get("CODETALK_DEPLOYER_CONFIG_PATH", Path(__file__).parent / ".deploy-config.json")
)
_PROJECT_ROOT = Path(__file__).parent.parent

# camelCase (frontend) -> snake_case (backend/deployer canonical form)
_KEY_MAP = {
    "apiKey": "api_key",
    "dbUser": "postgres_user",
    "dbPassword": "postgres_password",
    "dbName": "postgres_db",
    "reposPath": "repos_path",
    "portFrontend": "frontend_port",
    "portBackend": "backend_port",
    "portDb": "postgres_port",
    "portGitnexus": "gitnexus_port",
    "llmProvider": "llm_provider",
    "llmBaseUrl": "llm_base_url",
    "llmApiKey": "llm_api_key",
    "llmModel": "llm_model",
    "apiKeyConfigured": "api_key_configured",
    "apiKeyPreview": "api_key_preview",
    "installGitnexus": "install_gitnexus",
    "installCgc": "install_cgc",
    "portCgc": "cgc_port",
    "cgcVenvPath": "cgc_venv_path",
    "corsOrigins": "cors_origins",
    "workspacePath": "workspace_path",
    "forceTakeover": "force_takeover",
    "devMode": "dev_mode",
}
_KEY_MAP_REV = {v: k for k, v in _KEY_MAP.items()}

_REMOVED_LEGACY_TOOL_KEYS = {
    "installDeepwiki",
    "install_deepwiki",
    "deepwikiPath",
    "deepwiki_path",
    "deepwikiApiPort",
    "deepwiki_api_port",
    "deepwikiUiPort",
    "deepwiki_ui_port",
}

_FRONTEND_ONLY_KEYS = {
    "apiKeyConfigured",
    "api_key_configured",
    "apiKeyPreview",
    "api_key_preview",
}

_PROVIDER_SECRET_KEYS = {
    "api_key",
    "llm_api_key",
    "openai_api_key",
    "anthropic_api_key",
    "google_api_key",
}


def _drop_removed_legacy_tool_keys(cfg: dict) -> dict:
    """Remove deployment fields that belonged to removed optional tools."""
    return {
        k: v
        for k, v in cfg.items()
        if k not in _REMOVED_LEGACY_TOOL_KEYS and "deepwiki" not in k.lower()
    }


def normalize_to_snake(cfg: dict) -> dict:
    """Convert any camelCase frontend keys to snake_case backend keys."""
    out = {}
    for k, v in _drop_removed_legacy_tool_keys(cfg).items():
        if k in _FRONTEND_ONLY_KEYS:
            continue
        out[_KEY_MAP.get(k, k)] = v
    return out


def _normalize_to_camel(cfg: dict) -> dict:
    """Convert snake_case backend keys to camelCase for frontend consumption."""
    cfg = _drop_removed_legacy_tool_keys(dict(cfg))
    out = {}
    for k, v in cfg.items():
        out[_KEY_MAP_REV.get(k, k)] = v
    return out


def load_config() -> dict:
    """Load config from .deploy-config.json in snake_case canonical form."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            cleaned = _drop_removed_legacy_tool_keys(normalize_to_snake(raw))
            if cleaned != raw:
                with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                    json.dump(cleaned, f, indent=2)
            return cleaned
        except (json.JSONDecodeError, OSError):
            pass
    return get_default_config("native")


def load_config_for_frontend() -> dict:
    """Load config, summarize provider-specific key safely, return camelCase."""
    cfg = load_config()
    cfg = _summarize_api_key_for_frontend(cfg)
    return _normalize_to_camel(cfg)


def _distribute_api_key(cfg: dict) -> dict:
    """Distribute generic api_key to the correct provider-specific field."""
    api_key = cfg.pop("api_key", None)
    if api_key is None:
        return cfg
    provider = cfg.get("llm_provider", "openai")
    provider_key_map = {
        "openai": "openai_api_key",
        "anthropic": "anthropic_api_key",
        "google": "google_api_key",
        "ollama": "ollama_base_url",
    }
    target_field = provider_key_map.get(provider, "openai_api_key")
    if api_key or target_field != "ollama_base_url":
        cfg[target_field] = api_key
    return cfg


def _mask_secret_preview(secret: str) -> str:
    if not secret:
        return ""
    return f"{secret[:4]}••••••••"


def _summarize_api_key_for_frontend(cfg: dict) -> dict:
    """Surface only whether a provider key exists plus a non-sensitive preview."""
    provider = cfg.get("llm_provider", "openai")
    provider_key_map = {
        "openai": "openai_api_key",
        "anthropic": "anthropic_api_key",
        "google": "google_api_key",
        "ollama": "ollama_base_url",
    }
    source_field = provider_key_map.get(provider, "openai_api_key")
    secret = str(cfg.get(source_field) or "")
    for key in _PROVIDER_SECRET_KEYS:
        cfg.pop(key, None)
    cfg["api_key_configured"] = bool(secret)
    cfg["api_key_preview"] = _mask_secret_preview(secret)
    return cfg


_PORT_KEYS = {"backend_port", "frontend_port", "gitnexus_port", "postgres_port", "cgc_port"}


def _validate_ports(cfg: dict) -> dict:
    for key in _PORT_KEYS:
        if key in cfg:
            val = cfg[key]
            if isinstance(val, str) and val.isdigit():
                val = int(val)
            if not isinstance(val, int) or not (1024 <= val <= 65535):
                raise ValueError(f"{key} must be integer port in [1024, 65535], got {cfg[key]!r}")
            cfg[key] = val
    return cfg


def save_config(config: dict) -> None:
    """Normalize to snake_case, distribute api_key by provider, and persist.

    Merges into the existing config so fields absent from the new payload
    (e.g. llm_* keys after the LLM wizard section was removed) are preserved.
    """
    normalized = normalize_to_snake(config)
    normalized = _distribute_api_key(normalized)
    normalized = _validate_ports(normalized)
    existing: dict = {}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                existing = normalize_to_snake(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    existing.update(normalized)
    existing = _drop_removed_legacy_tool_keys(existing)
    _validate_ports(existing)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)


def get_default_config(mode: str) -> dict:
    """Return a default config dict for the given deployment mode."""
    base = {
        "mode": mode,
        "workspace_path": "./workspace",
        "llm_provider": "openai",
        "openai_api_key": "",
        "anthropic_api_key": "",
        "google_api_key": "",
        "repos_path": "./workspace/repos",
        "frontend_port": 3003,
        "gitnexus_port": 7100,
        "cgc_port": 7072,
        "install_cgc": True,
        "cors_origins": "http://localhost:3003,http://127.0.0.1:3003",
    }
    if mode == "native":
        base["backend_port"] = 3004
        base["ollama_base_url"] = "http://localhost:11434"
    else:
        base["backend_port"] = 3004
        base["ollama_base_url"] = "http://host.docker.internal:11434"
        base["postgres_user"] = "codetalks"
        base["postgres_password"] = "changeme"
        base["postgres_db"] = "codetalks"
    return base
