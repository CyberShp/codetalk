"""Contracts for the removed wiki orchestrator runtime."""

import importlib.util


def test_removed_wiki_orchestrator_module_is_not_present():
    assert importlib.util.find_spec("app.services.wiki_orchestrator") is None


def test_removed_wiki_prompt_module_is_not_present():
    assert importlib.util.find_spec("app.services.wiki_prompts") is None
