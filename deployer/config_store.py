"""Simple JSON file-based config storage for the deployer."""

import json
import os
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / ".deploy-config.json"

# camelCase (frontend) -> snake_case (backend/deployer canonical form)
_KEY_MAP = {
    "apiKey": "api_key",
    "dbUser": "postgres_user",
    "dbPassword": "postgres_password",
    "dbName": "postgres_db",
    "reposPath": "repos_path",
    "portFrontend": "frontend_port",
    "portBackend": "backend_port",
    "portDeepwiki": "deepwiki_port",
    "portDb": "postgres_port",
    "portGitnexus": "gitnexus_port",
    "llmProvider": "llm_provider",
    "corsOrigins": "cors_origins",
}
_KEY_MAP_REV = {v: k for k, v in _KEY_MAP.items()}


def _normalize_to_snake(cfg: dict) -> dict:
    """Convert any camelCase frontend keys to snake_case backend keys."""
    out = {}
    for k, v in cfg.items():
        out[_KEY_MAP.get(k, k)] = v
    return out


def _normalize_to_camel(cfg: dict) -> dict:
    """Convert snake_case backend keys to camelCase for frontend consumption."""
    out = {}
    for k, v in cfg.items():
        out[_KEY_MAP_REV.get(k, k)] = v
    return out


def load_config() -> dict:
    """Load config from .deploy-config.json in snake_case canonical form."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return _normalize_to_snake(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    return get_default_config("compose")


def load_config_for_frontend() -> dict:
    """Load config, resolve provider-specific key to apiKey, return camelCase."""
    cfg = load_config()
    cfg = _pick_api_key_for_frontend(cfg)
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


def _pick_api_key_for_frontend(cfg: dict) -> dict:
    """Pick the correct provider-specific key and surface it as apiKey."""
    provider = cfg.get("llm_provider", "openai")
    provider_key_map = {
        "openai": "openai_api_key",
        "anthropic": "anthropic_api_key",
        "google": "google_api_key",
        "ollama": "ollama_base_url",
    }
    source_field = provider_key_map.get(provider, "openai_api_key")
    cfg["api_key"] = cfg.get(source_field, "")
    return cfg


def save_config(config: dict) -> None:
    """Normalize to snake_case, distribute api_key by provider, and persist."""
    normalized = _normalize_to_snake(config)
    normalized = _distribute_api_key(normalized)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=2)


def get_default_config(mode: str) -> dict:
    """Return a default config dict for the given deployment mode."""
    base = {
        "mode": mode,
        "postgres_user": "codetalks",
        "postgres_password": "changeme",
        "postgres_db": "codetalks",
        "llm_provider": "openai",
        "openai_api_key": "",
        "anthropic_api_key": "",
        "google_api_key": "",
        "ollama_base_url": "http://host.docker.internal:11434",
        "repos_path": "./.repos",
        "frontend_port": 3005,
        "backend_port": 8000,
        "gitnexus_port": 7100,
        "cors_origins": "http://localhost:3000,http://localhost:3005",
    }
    return base
