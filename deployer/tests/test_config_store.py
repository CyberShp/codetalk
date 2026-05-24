"""E2E tests for config_store.py — pure function coverage, real file I/O."""

import json
import sys
from pathlib import Path

import pytest

DEPLOYER_DIR = Path(__file__).parent.parent
if str(DEPLOYER_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYER_DIR))

import config_store


# ---------------------------------------------------------------------------
# normalize_to_snake
# ---------------------------------------------------------------------------

def test_normalize_camel_to_snake_api_key():
    result = config_store.normalize_to_snake({"apiKey": "sk-abc"})
    assert result == {"api_key": "sk-abc"}


def test_normalize_camel_to_snake_db_user():
    result = config_store.normalize_to_snake({"dbUser": "codetalks", "dbPassword": "pass"})
    assert result["postgres_user"] == "codetalks"
    assert result["postgres_password"] == "pass"


def test_normalize_unknown_keys_pass_through():
    result = config_store.normalize_to_snake({"unknownKey": 42, "anotherKey": "x"})
    assert result["unknownKey"] == 42
    assert result["anotherKey"] == "x"


def test_normalize_deepwiki_port_alias(isolated_config):
    result = config_store.normalize_to_snake({"deepwiki_port": 8091})
    assert result.get("deepwiki_api_port") == 8091
    assert "deepwiki_port" not in result


def test_normalize_deepwiki_port_not_overwrite_existing():
    result = config_store.normalize_to_snake({"deepwiki_port": 8091, "deepwiki_api_port": 9000})
    assert result["deepwiki_api_port"] == 9000


def test_normalize_frontend_key_map():
    result = config_store.normalize_to_snake({"portFrontend": 3005, "portBackend": 8100})
    assert result["frontend_port"] == 3005
    assert result["backend_port"] == 8100


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

def test_load_config_returns_native_default_when_no_file(isolated_config):
    cfg = config_store.load_config()
    assert cfg["mode"] == "native"
    assert "backend_port" in cfg
    assert "frontend_port" in cfg


def test_load_config_reads_existing_file(isolated_config):
    isolated_config.write_text(json.dumps({"mode": "native", "backend_port": 9999}), encoding="utf-8")
    cfg = config_store.load_config()
    assert cfg["backend_port"] == 9999


def test_load_config_fallback_on_corrupt_json(isolated_config):
    isolated_config.write_text("NOT_JSON", encoding="utf-8")
    cfg = config_store.load_config()
    assert cfg["mode"] == "native"


# ---------------------------------------------------------------------------
# save_config + load_config roundtrip
# ---------------------------------------------------------------------------

def test_save_config_persists_to_file(isolated_config):
    config_store.save_config({"mode": "native", "portBackend": 8888})
    assert isolated_config.exists()
    raw = json.loads(isolated_config.read_text(encoding="utf-8"))
    assert raw["backend_port"] == 8888


def test_save_config_merges_with_existing(isolated_config):
    isolated_config.write_text(json.dumps({"mode": "native", "frontend_port": 3005}), encoding="utf-8")
    config_store.save_config({"portBackend": 9999})
    cfg = config_store.load_config()
    assert cfg["frontend_port"] == 3005
    assert cfg["backend_port"] == 9999


# ---------------------------------------------------------------------------
# _distribute_api_key
# ---------------------------------------------------------------------------

def test_distribute_api_key_openai():
    cfg = {"llm_provider": "openai", "api_key": "sk-openai"}
    result = config_store._distribute_api_key(cfg)
    assert result.get("openai_api_key") == "sk-openai"
    assert "api_key" not in result


def test_distribute_api_key_anthropic():
    cfg = {"llm_provider": "anthropic", "api_key": "ant-key"}
    result = config_store._distribute_api_key(cfg)
    assert result.get("anthropic_api_key") == "ant-key"


def test_distribute_api_key_google():
    cfg = {"llm_provider": "google", "api_key": "goog-key"}
    result = config_store._distribute_api_key(cfg)
    assert result.get("google_api_key") == "goog-key"


def test_distribute_api_key_ollama_empty_does_not_overwrite():
    cfg = {"llm_provider": "ollama", "api_key": "", "ollama_base_url": "http://localhost:11434"}
    result = config_store._distribute_api_key(cfg)
    assert result.get("ollama_base_url") == "http://localhost:11434"


def test_distribute_api_key_none_is_noop():
    cfg = {"llm_provider": "openai"}
    original = dict(cfg)
    result = config_store._distribute_api_key(cfg)
    assert result == original


# ---------------------------------------------------------------------------
# _pick_api_key_for_frontend
# ---------------------------------------------------------------------------

def test_pick_api_key_openai():
    cfg = {"llm_provider": "openai", "openai_api_key": "sk-test"}
    result = config_store._pick_api_key_for_frontend(cfg)
    assert result["api_key"] == "sk-test"


def test_pick_api_key_anthropic():
    cfg = {"llm_provider": "anthropic", "anthropic_api_key": "ant-test"}
    result = config_store._pick_api_key_for_frontend(cfg)
    assert result["api_key"] == "ant-test"


def test_pick_api_key_missing_returns_empty():
    cfg = {"llm_provider": "openai"}
    result = config_store._pick_api_key_for_frontend(cfg)
    assert result["api_key"] == ""
