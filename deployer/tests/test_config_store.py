"""E2E tests for config_store.py — pure function coverage, real file I/O."""

import json
import sys
from pathlib import Path

import pytest

DEPLOYER_DIR = Path(__file__).parent.parent
if str(DEPLOYER_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOYER_DIR))

import config_store


def test_config_path_can_be_isolated_by_environment(monkeypatch, tmp_path):
    custom_path = tmp_path / "browser-e2e-config.json"
    monkeypatch.setenv("CODETALK_DEPLOYER_CONFIG_PATH", str(custom_path))

    import importlib

    reloaded = importlib.reload(config_store)
    try:
        assert reloaded.CONFIG_PATH == custom_path
        reloaded.save_config({"mode": "native", "portBackend": 4567})
        assert custom_path.exists()
        assert json.loads(custom_path.read_text(encoding="utf-8"))["backend_port"] == 4567
    finally:
        monkeypatch.delenv("CODETALK_DEPLOYER_CONFIG_PATH", raising=False)
        importlib.reload(config_store)


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


def test_normalize_frontend_key_map():
    result = config_store.normalize_to_snake({"portFrontend": 3003, "portBackend": 3004})
    assert result["frontend_port"] == 3003
    assert result["backend_port"] == 3004


def test_normalize_drops_removed_deepwiki_keys():
    result = config_store.normalize_to_snake(
        {
            "installDeepwiki": True,
            "deepwikiPath": "/tmp/deepwiki",
            "deepwikiApiPort": 8091,
            "deepwikiUiPort": 3001,
            "deepwikiBaseUrl": "http://localhost:8091",
            "deepwiki_provider": "openai",
            "portFrontend": 3003,
        }
    )
    assert result == {"frontend_port": 3003}


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

def test_load_config_returns_native_default_when_no_file(isolated_config):
    cfg = config_store.load_config()
    assert cfg["mode"] == "native"
    assert "backend_port" in cfg
    assert "frontend_port" in cfg


def test_load_config_reads_existing_file(isolated_config):
    isolated_config.write_text(
        json.dumps(
            {
                "mode": "native",
                "backend_port": 9999,
                "install_deepwiki": True,
                "deepwiki_path": "/tmp/deepwiki",
                "deepwiki_api_port": 8091,
                "deepwiki_ui_port": 3001,
                "deepwiki_base_url": "http://localhost:8091",
                "legacy_deepwiki_provider": "openai",
            }
        ),
        encoding="utf-8",
    )
    cfg = config_store.load_config()
    assert cfg["backend_port"] == 9999
    assert "install_deepwiki" not in cfg
    assert "deepwiki_path" not in cfg
    assert "deepwiki_api_port" not in cfg
    assert "deepwiki_ui_port" not in cfg
    assert "deepwiki_base_url" not in cfg
    assert "legacy_deepwiki_provider" not in cfg
    raw = json.loads(isolated_config.read_text(encoding="utf-8"))
    assert "install_deepwiki" not in raw
    assert "deepwiki_path" not in raw
    assert "deepwiki_api_port" not in raw
    assert "deepwiki_ui_port" not in raw
    assert "deepwiki_base_url" not in raw
    assert "legacy_deepwiki_provider" not in raw


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
    isolated_config.write_text(
        json.dumps({
            "mode": "native",
            "frontend_port": 3003,
            "install_deepwiki": True,
            "deepwiki_base_url": "http://localhost:8091",
        }),
        encoding="utf-8",
    )
    config_store.save_config({"portBackend": 9999})
    cfg = config_store.load_config()
    assert cfg["frontend_port"] == 3003
    assert cfg["backend_port"] == 9999
    assert "install_deepwiki" not in cfg
    assert "deepwiki_base_url" not in cfg
    raw = json.loads(isolated_config.read_text(encoding="utf-8"))
    assert "install_deepwiki" not in raw
    assert "deepwiki_base_url" not in raw


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
# _summarize_api_key_for_frontend
# ---------------------------------------------------------------------------

def test_summarize_api_key_openai():
    cfg = {"llm_provider": "openai", "openai_api_key": "sk-test"}
    result = config_store._summarize_api_key_for_frontend(cfg)
    assert result["api_key_configured"] is True
    assert result["api_key_preview"] == "sk-t••••••••"
    assert "api_key" not in result


def test_summarize_api_key_anthropic():
    cfg = {"llm_provider": "anthropic", "anthropic_api_key": "ant-test"}
    result = config_store._summarize_api_key_for_frontend(cfg)
    assert result["api_key_configured"] is True
    assert result["api_key_preview"] == "ant-••••••••"
    assert "api_key" not in result


def test_summarize_api_key_missing_returns_empty():
    cfg = {"llm_provider": "openai"}
    result = config_store._summarize_api_key_for_frontend(cfg)
    assert result["api_key_configured"] is False
    assert result["api_key_preview"] == ""
    assert "api_key" not in result
