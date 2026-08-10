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
def test_registry_keeps_specialized_adapters_for_managed_transports(
    tmp_path, provider, transport, expected_name
):
    from app.services.provider_adapters.registry import create_provider_adapter

    adapter = create_provider_adapter(
        provider=provider,
        prompt_transport=transport,
        artifact_dir=tmp_path,
        builtin_execute_callable=lambda **_kwargs: {"status": "completed"},
    )

    assert type(adapter).__name__ == expected_name


@pytest.mark.parametrize("transport", ["stdin", "argv_last"])
def test_registry_accepts_arbitrary_cli_provider_without_allowlist(tmp_path, transport):
    from app.services.provider_adapters.cli_base import CliProviderAdapter
    from app.services.provider_adapters.registry import create_provider_adapter

    adapter = create_provider_adapter(
        provider="agent-runtime:internal-company-agent",
        prompt_transport=transport,
        artifact_dir=tmp_path,
    )

    assert isinstance(adapter, CliProviderAdapter)
    assert adapter.provider == "agent-runtime:internal-company-agent"
    assert adapter.prompt_transport == transport
    assert adapter.output_mode == "auto"


def test_registry_does_not_use_unknown_transport_as_adapter_allowlist(tmp_path):
    from app.services.provider_adapters.cli_base import CliProviderAdapter
    from app.services.provider_adapters.registry import (
        create_provider_adapter,
        provider_capability_names,
    )

    adapter = create_provider_adapter(
        provider="agent-runtime:future-agent",
        prompt_transport="future_transport",
        artifact_dir=tmp_path,
    )

    assert isinstance(adapter, CliProviderAdapter)
    assert adapter.prompt_transport == "future_transport"
    assert provider_capability_names(
        provider="agent-runtime:future-agent",
        prompt_transport="future_transport",
    ) == ["cancellation", "streaming"]


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
