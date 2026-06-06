from collections.abc import AsyncIterator

from app.adapters.base import (
    AnalysisRequest,
    BaseToolAdapter,
    ToolCapability,
    ToolHealth,
    UnifiedResult,
)
from app.config import settings
from app.services.external_agent_discovery import (
    AgentDiscoveryRequest,
    check_provider_health,
    provider_fallback_commands,
    run_external_agent_discovery,
)


class ExternalAgentAdapter(BaseToolAdapter):
    def __init__(self, provider: str, command_attr: str) -> None:
        self._provider = provider
        self._command_attr = command_attr

    def name(self) -> str:
        return self._provider

    def capabilities(self) -> list[ToolCapability]:
        return [ToolCapability.CODE_SEARCH]

    async def health_check(self) -> ToolHealth:
        command = str(getattr(settings, self._command_attr, "") or "")
        health = check_provider_health(
            self._provider,
            command,
            fallback_commands=provider_fallback_commands(self._provider),
        )
        ok = health.get("status") == "available"
        return ToolHealth(
            is_healthy=ok,
            container_status="available" if ok else "unavailable",
            version=str(health.get("path") or health.get("reason") or ""),
        )

    async def prepare(self, request: AnalysisRequest) -> None:
        return None

    async def analyze(self, request: AnalysisRequest) -> UnifiedResult:
        goal = request.options.get("goal") or "source_scope"
        results = await run_external_agent_discovery(
            AgentDiscoveryRequest(
                request_id=str(request.options.get("request_id") or "adapter"),
                repo_path=request.repo_local_path,
                analysis_object_text=str(request.options.get("analysis_object_text") or ""),
                path_hints=request.target_files or [],
                coverage_hit=request.options.get("coverage_hit"),
                existing_candidates=request.options.get("existing_candidates") or [],
                goal=goal,
            ),
            providers=[self._provider],
        )
        data = [result.__dict__ for result in results]
        return UnifiedResult(
            tool_name=self._provider,
            capability=ToolCapability.CODE_SEARCH,
            data={"results": data},
        )

    async def stream_logs(self, run_id: str) -> AsyncIterator[str]:
        if False:
            yield run_id
        return
