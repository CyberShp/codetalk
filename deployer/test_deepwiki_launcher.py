"""Tests for the native DeepWiki launcher."""

from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path


_MODULE_PATH = Path(__file__).with_name("deepwiki_launcher.py")
_SPEC = importlib.util.spec_from_file_location("deepwiki_launcher_for_test", _MODULE_PATH)
assert _SPEC and _SPEC.loader
deepwiki_launcher = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(deepwiki_launcher)


def test_load_deepwiki_dotenv_overrides_inherited_env(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "OPENAI_API_KEY=fresh-chat-key\n"
        "DEEPWIKI_EMBEDDING_API_KEY=fresh-embed-key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "stale-system-key")
    monkeypatch.delenv("DEEPWIKI_EMBEDDING_API_KEY", raising=False)

    assert deepwiki_launcher._load_deepwiki_dotenv(str(dotenv)) is True

    assert os.environ["OPENAI_API_KEY"] == "fresh-chat-key"
    assert os.environ["DEEPWIKI_EMBEDDING_API_KEY"] == "fresh-embed-key"


def test_simple_dotenv_fallback_parser_handles_export_and_quotes():
    assert deepwiki_launcher._parse_simple_dotenv_line(
        'export OPENAI_BASE_URL="https://example.test/v1"'
    ) == ("OPENAI_BASE_URL", "https://example.test/v1")


def test_quiet_secret_prone_dependency_logs():
    loggers = [
        logging.getLogger("adalflow.core.component"),
        logging.getLogger("api.openai_client"),
    ]
    previous = [logger.level for logger in loggers]
    try:
        for logger in loggers:
            logger.setLevel(logging.INFO)
        deepwiki_launcher._quiet_secret_prone_dependency_logs()
        assert [logger.level for logger in loggers] == [logging.WARNING, logging.WARNING]
    finally:
        for logger, level in zip(loggers, previous):
            logger.setLevel(level)
