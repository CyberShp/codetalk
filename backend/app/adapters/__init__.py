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
    from app.adapters.deepwiki import DeepwikiAdapter
    from app.adapters.gitnexus import GitNexusAdapter
    from app.adapters.joern import JoernAdapter
    from app.adapters.semgrep import SemgrepAdapter
    from app.adapters.zoekt import ZoektAdapter

    def deepwiki_factory() -> DeepwikiAdapter:
        return DeepwikiAdapter(base_url=settings.deepwiki_base_url)

    def gitnexus_factory() -> GitNexusAdapter:
        return GitNexusAdapter(base_url=settings.gitnexus_base_url)

    def zoekt_factory() -> ZoektAdapter:
        return ZoektAdapter(
            base_url=settings.zoekt_base_url,
            container_name=settings.zoekt_container_name,
        )

    def joern_factory() -> JoernAdapter:
        return JoernAdapter(base_url=settings.joern_base_url)

    def semgrep_factory() -> SemgrepAdapter:
        return SemgrepAdapter(base_url=settings.semgrep_base_url)

    register_adapter(deepwiki_factory(), factory=deepwiki_factory)
    register_adapter(gitnexus_factory(), factory=gitnexus_factory)
    register_adapter(zoekt_factory(), factory=zoekt_factory)
    register_adapter(joern_factory(), factory=joern_factory)
    register_adapter(semgrep_factory(), factory=semgrep_factory)


_register_defaults()
