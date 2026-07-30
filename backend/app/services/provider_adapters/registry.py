"""Explicit mapping from frozen workflow provider settings to adapters."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from app.services.provider_adapters.builtin_model import BUILTIN_MODEL_CAPABILITIES
from app.services.provider_adapters.claude_code import ClaudeCliAdapter
from app.services.provider_adapters.contracts import ProviderCapabilities
from app.services.provider_adapters.codex_cli import CodexCliAdapter
from app.services.provider_adapters.opencode import OpenCodeAdapter
from app.services.provider_adapters.safe_builtin_model import BuiltinModelAdapter
from app.services.workbench_artifact_path_authority import (
    install_workbench_artifact_path_authority,
)


# WorkbenchWorkflowRunner imports this registry before it loads a Task Run. Install
# the reconciliation seam here so persisted human-readable paths cannot become
# execution authority even for old Task Runs created before the fix.
install_workbench_artifact_path_authority()


_TRANSPORT_ADAPTERS = {
    "codex_exec_json": CodexCliAdapter,
    "claude_print_arg": ClaudeCliAdapter,
    "opencode_run_arg": OpenCodeAdapter,
}


def create_provider_adapter(
    *,
    provider: str,
    prompt_transport: str,
    artifact_dir: str | Path,
    builtin_execute_callable: Callable[..., Any] | None = None,
) -> Any | None:
    """Return an adapter only for a provider contract CodeTalk owns explicitly.

    Unknown providers deliberately return ``None`` so callers must choose the
    named legacy compatibility path instead of silently guessing a transport.
    """

    provider_id = str(provider or "").strip().lower()
    transport = str(prompt_transport or "").strip().lower()
    if provider_id in {"builtin", "builtin-llm", "builtin_llm"} or transport == "builtin_llm":
        if builtin_execute_callable is None:
            raise ValueError("builtin provider requires an execute callable")
        return BuiltinModelAdapter(
            artifact_dir,
            execute_callable=builtin_execute_callable,
        )

    adapter_type = _TRANSPORT_ADAPTERS.get(transport)
    if adapter_type is None:
        return None
    return adapter_type(artifact_dir)


def missing_provider_capabilities(
    adapter: Any,
    required: Iterable[str] | None,
) -> list[str]:
    """Return every requested capability the selected adapter cannot provide."""

    capabilities = adapter.capabilities()
    missing = {
        str(name)
        for name in required or []
        if not bool(getattr(capabilities, str(name), False))
    }
    return sorted(missing)


def provider_capability_names(
    *,
    provider: str = "",
    prompt_transport: str = "",
) -> list[str] | None:
    """Project the Adapter contract into the compiler's capability snapshot."""

    provider_id = str(provider or "").strip().lower()
    transport = str(prompt_transport or "").strip().lower()
    if provider_id in {"builtin", "builtin-llm", "builtin_llm"} or transport == "builtin_llm":
        capabilities = BUILTIN_MODEL_CAPABILITIES
    else:
        adapter_type = _TRANSPORT_ADAPTERS.get(transport)
        if adapter_type is None:
            return None
        capabilities = adapter_type(Path(".")).capabilities()
    return sorted(
        name
        for name in ProviderCapabilities.__dataclass_fields__
        if bool(getattr(capabilities, name, False))
    )
