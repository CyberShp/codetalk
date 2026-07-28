from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("provider", "transport", "expected_name"),
    [
        ("builtin-llm", "builtin_llm", "BuiltinModelAdapter"),
        ("agent-runtime:team-codex", "codex_exec_json", "CodexCliAdapter"),
        ("agent-runtime:team-claude", "claude_print_arg", "ClaudeCliAdapter"),
        ("agent-runtime:team-opencode", "opencode_run_arg", "OpenCodeAdapter"),
    ],
)
def test_registry_resolves_only_explicit_provider_contracts(
    tmp_path, provider, transport, expected_name
):
    from app.services.provider_adapters.registry import create_provider_adapter

    adapter = create_provider_adapter(
        provider=provider,
        prompt_transport=transport,
        artifact_dir=tmp_path,
        builtin_execute_callable=lambda **_kwargs: {"status": "completed"},
    )

    assert adapter is not None
    assert type(adapter).__name__ == expected_name


def test_registry_leaves_unknown_provider_to_explicit_legacy_compatibility(tmp_path):
    from app.services.provider_adapters.registry import create_provider_adapter

    assert create_provider_adapter(
        provider="local-python",
        prompt_transport="stdin",
        artifact_dir=tmp_path,
    ) is None


def test_required_provider_capabilities_are_not_silently_ignored(tmp_path):
    from app.services.provider_adapters.registry import (
        create_provider_adapter,
        missing_provider_capabilities,
    )

    adapter = create_provider_adapter(
        provider="builtin-llm",
        prompt_transport="builtin_llm",
        artifact_dir=tmp_path,
        builtin_execute_callable=lambda **_kwargs: {"status": "completed"},
    )

    assert missing_provider_capabilities(
        adapter,
        ["streaming", "cancellation"],
    ) == ["cancellation", "streaming"]
    assert missing_provider_capabilities(
        adapter,
        ["session_resume", "mcp", "unknown_future_capability"],
    ) == ["mcp", "session_resume", "unknown_future_capability"]
