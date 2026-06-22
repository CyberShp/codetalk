"""Adapters for optional context-discovery providers."""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.adapters.base import AnalysisRequest, BaseToolAdapter, ToolCapability, ToolHealth, UnifiedResult
from app.config import settings


class FastContextAdapter(BaseToolAdapter):
    def name(self) -> str:
        return "fast-context"

    def capabilities(self) -> list[ToolCapability]:
        return [ToolCapability.CODE_SEARCH]

    async def health_check(self) -> ToolHealth:
        if not settings.fast_context_enabled:
            return ToolHealth(
                is_healthy=False,
                container_status="disabled",
                version="fast-context disabled",
                last_check="fast-context provider is disabled",
            )
        if not settings.fast_context_backend_bridge_enabled:
            return ToolHealth(
                is_healthy=False,
                container_status="unavailable",
                version="no backend MCP bridge",
                last_check="fast-context backend bridge is not configured",
            )
        return ToolHealth(
            is_healthy=True,
            container_status="available",
            version="fast-context backend bridge",
            last_check="fast-context backend bridge configured",
        )

    async def prepare(self, request: AnalysisRequest) -> None:
        return None

    async def analyze(self, request: AnalysisRequest) -> UnifiedResult:
        return UnifiedResult(
            tool_name=self.name(),
            capability=ToolCapability.CODE_SEARCH,
            data={
                "status": "adapter_health_only",
                "message": "fast-context source discovery is invoked by WorkspaceScopeResolver",
            },
        )

    async def stream_logs(self, run_id: str) -> AsyncIterator[str]:
        if False:
            yield run_id
