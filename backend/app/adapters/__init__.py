from collections.abc import Callable

from app.adapters.base import BaseToolAdapter
from app.config import settings

# Shared singletons for health checks / capability queries
TOOL_REGISTRY: dict[str, BaseToolAdapter] = {}
# Factories for per-task instances (avoids mutable state pollution)
ADAPTER_FACTORIES: dict[str, Callable[[], BaseToolAdapter]] = {}


def register_adapter(
    adapter: BaseToolAdapter,
    factory: Callable[[], BaseToolAdapter] | None = None,
) -> None:
    TOOL_REGISTRY[adapter.name()] = adapter
    if factory:
        ADAPTER_FACTORIES[adapter.name()] = factory


def get_adapter(name: str) -> BaseToolAdapter:
    """Return the shared singleton — use for health checks and capability queries only."""
    if name not in TOOL_REGISTRY:
        raise KeyError(f"Unknown tool adapter: {name}")
    return TOOL_REGISTRY[name]


def create_adapter(name: str) -> BaseToolAdapter:
    """Return a fresh instance — use for task execution to avoid shared state."""
    if name not in ADAPTER_FACTORIES:
        raise KeyError(f"Unknown tool adapter: {name}")
    return ADAPTER_FACTORIES[name]()


def get_all_adapters() -> list[BaseToolAdapter]:
    return list(TOOL_REGISTRY.values())


def _register_defaults() -> None:
    from app.adapters.cgc import CGCAdapter
    from app.adapters.codecompass import CodeCompassAdapter
    from app.adapters.context_discovery import FastContextAdapter
    from app.adapters.deepwiki import DeepwikiAdapter
    from app.adapters.external_agent import ExternalAgentAdapter
    from app.adapters.gitnexus import GitNexusAdapter
    from app.adapters.joern import JoernAdapter
    from app.services.external_agent_discovery import external_agent_provider_specs

    def cgc_factory() -> CGCAdapter:
        return CGCAdapter(base_url=settings.cgc_base_url)

    def deepwiki_factory() -> DeepwikiAdapter:
        return DeepwikiAdapter(base_url=settings.deepwiki_api_url)

    def gitnexus_factory() -> GitNexusAdapter:
        return GitNexusAdapter(base_url=settings.gitnexus_base_url)

    def joern_factory() -> JoernAdapter:
        return JoernAdapter(base_url=settings.joern_base_url)

    def codecompass_factory() -> CodeCompassAdapter:
        return CodeCompassAdapter(base_url=settings.codecompass_base_url)

    def fast_context_factory() -> FastContextAdapter:
        return FastContextAdapter()

    def claude_code_factory() -> ExternalAgentAdapter:
        return ExternalAgentAdapter("claude-code", "claude_code_command")

    def opencode_factory() -> ExternalAgentAdapter:
        return ExternalAgentAdapter("opencode", "opencode_command")

    register_adapter(cgc_factory(), factory=cgc_factory)
    register_adapter(deepwiki_factory(), factory=deepwiki_factory)
    register_adapter(gitnexus_factory(), factory=gitnexus_factory)
    register_adapter(joern_factory(), factory=joern_factory)
    register_adapter(codecompass_factory(), factory=codecompass_factory)
    register_adapter(fast_context_factory(), factory=fast_context_factory)
    register_adapter(claude_code_factory(), factory=claude_code_factory)
    register_adapter(opencode_factory(), factory=opencode_factory)
    for provider_id in external_agent_provider_specs():
        if provider_id in {"claude-code", "opencode"}:
            continue

        def custom_factory(provider: str = provider_id) -> ExternalAgentAdapter:
            return ExternalAgentAdapter(provider)

        register_adapter(custom_factory(), factory=custom_factory)


_register_defaults()
