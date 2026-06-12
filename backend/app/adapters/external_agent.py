from collections.abc import AsyncIterator
from dataclasses import asdict

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
    probe_external_agent_startup,
    provider_fallback_commands,
    redact_agent_diagnostic_text,
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
        attempts = health.get("attempts") or []
        misconfigured = _active_attempt_has_configuration_error(health)
        ok = health.get("status") == "available" and not misconfigured
        attempt_summary = "; ".join(
            _format_attempt_summary(item)
            for item in attempts
            if isinstance(item, dict)
        )
        launch = str(health.get("launch_kind") or "").strip()
        launch_summary = f"launch={launch}" if launch else ""
        details = [
            part
            for part in [
                str(health.get("reason") or "").strip(),
                launch_summary,
                attempt_summary,
                _format_runtime_diagnostic(health.get("diagnostic")),
            ]
            if part
        ]
        last_check = "; ".join(details)
        return ToolHealth(
            is_healthy=ok,
            container_status="available" if ok else ("misconfigured" if misconfigured else "unavailable"),
            version=redact_agent_diagnostic_text(str(health.get("path") or health.get("reason") or "")),
            last_check=redact_agent_diagnostic_text(last_check),
        )

    async def startup_probe(self, repo_path: str | None = None) -> dict:
        return await probe_external_agent_startup(self._provider, repo_path=repo_path)

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
        data = [asdict(result) for result in results]
        return UnifiedResult(
            tool_name=self._provider,
            capability=ToolCapability.CODE_SEARCH,
            data={"results": data},
        )

    async def stream_logs(self, run_id: str) -> AsyncIterator[str]:
        if False:
            yield run_id
        return


def _format_attempt_summary(item: dict) -> str:
    launch = item.get("launch_kind")
    launch_suffix = f" ({launch})" if launch else ""
    reason = str(item.get("reason") or "").strip()
    config_hint = str(item.get("config_hint") or "").strip()
    detail = reason or config_hint
    reason_suffix = f": {detail}" if detail else ""
    return redact_agent_diagnostic_text(
        f"{item.get('command')} => {item.get('status')}{launch_suffix}{reason_suffix}"
    )


def _active_attempt_has_configuration_error(health: object) -> bool:
    if not isinstance(health, dict):
        return False
    if health.get("status") == "configuration_error":
        return True
    attempts = health.get("attempts")
    if not isinstance(attempts, list):
        return False
    if health.get("used_fallback"):
        active_attempts = [attempts[-1]] if attempts else []
    else:
        active_attempts = attempts
    return any(_has_configuration_error(item) for item in active_attempts)


def _has_configuration_error(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("status") == "configuration_error":
        return True
    hint = str(item.get("config_hint") or "").lower()
    return "ccr_config_path" in hint and "default config not found" in hint


def _format_runtime_diagnostic(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    parts = [
        str(value.get("summary") or "").strip(),
        str(value.get("command_hint") or "").strip(),
    ]
    return redact_agent_diagnostic_text("; ".join(part for part in parts if part))
