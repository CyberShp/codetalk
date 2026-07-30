"""Runtime network passthrough for CodeTalk integrations.

CodeTalk does not own enterprise egress security. Company network controls,
firewalls, endpoint management, and provider credentials are the security
boundary; this module only keeps a stable compatibility shape for callers that
used to ask for a network-policy decision.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Callable, Literal, Mapping
from urllib.parse import urlparse

from app.config import settings


class NetworkEgressBlocked(ValueError):
    """Raised before an unapproved endpoint can be contacted."""


@dataclass(frozen=True)
class NetworkDecision:
    allowed: bool
    reason: str
    host: str
    port: int


NetworkMode = Literal["developer", "intranet", "strict_compliance"]
EgressBoundary = Literal["none", "approved_proxy_gateway", "deployment_egress_policy"]


@dataclass(frozen=True)
class AgentNetworkContext:
    """The single deploy-time decision consumed by CLI probe and execution paths.

    Environment values may contain credentials and are intentionally excluded
    from ``snapshot()``.  Callers pass ``sanitized_environment`` directly to
    subprocess creation and persist only the JSON-safe snapshot.
    """

    allowed: bool
    mode: NetworkMode
    boundary: EgressBoundary
    reason: str
    remediation: str
    sanitized_environment: dict[str, str]
    approved_proxy_config_id: str = ""
    deployment_egress_policy_id: str = ""
    approved_proxy_target: str = ""
    requires_os_network_isolation: bool = False
    policy_v2_enabled: bool = True

    def require_allowed(self) -> "AgentNetworkContext":
        if not self.allowed:
            raise NetworkEgressBlocked(
                f"Agent 网络策略拒绝：{self.reason}。{self.remediation}"
            )
        return self

    def snapshot(self) -> dict[str, object]:
        return {
            "network_policy_v2_enabled": self.policy_v2_enabled,
            "mode": self.mode,
            "boundary": self.boundary,
            "allowed": self.allowed,
            "reason": self.reason,
            "approved_proxy_config_id": self.approved_proxy_config_id or None,
            "deployment_egress_policy_id": self.deployment_egress_policy_id or None,
            "requires_os_network_isolation": self.requires_os_network_isolation,
            "telemetry": "managed_by_environment",
            "remote_tracing": "managed_by_environment",
            "hosted_mcp": "managed_by_environment",
            "automatic_update": "managed_by_environment",
        }


Resolver = Callable[[str, int], list[str]]
_INTRANET_BLOCKED_ENV_KEYS = {
    "ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "FTP_PROXY", "NO_PROXY",
    "OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "LANGSMITH_TRACING", "LANGSMITH_ENDPOINT",
    "LANGSMITH_API_KEY", "OPENAI_TRACING", "OPENAI_TRACE", "CLAUDE_CODE_TELEMETRY",
    "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "UV_INDEX_URL", "UV_EXTRA_INDEX_URL",
    "NPM_CONFIG_REGISTRY", "YARN_REGISTRY", "BUN_INSTALL_REGISTRY",
}
_PROXY_AND_CA_ENV_KEYS = {
    "ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "FTP_PROXY", "NO_PROXY",
    "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR", "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS", "NODE_OPTIONS", "GIT_SSL_CAINFO", "GIT_SSL_CAPATH",
    "NPM_CONFIG_CAFILE", "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH", "AWS_CA_BUNDLE",
    "PIP_CERT", "CARGO_HTTP_CAINFO", "DENO_CERT",
}

# These are product-runtime hard denials, not user-editable configuration.
# Model APIs are different: a deployment may explicitly approve a provider endpoint
# (including one that resolves to a public-looking address). Autonomous traffic is
# never a valid runtime dependency, even when an allow-list was misconfigured.
_FORBIDDEN_AUTONOMOUS_SERVICE_SUFFIXES = (
    "github.com",
    "githubusercontent.com",
    "langchain.com",
    "langsmith.com",
    "npmjs.org",
    "pypi.org",
    "sentry.io",
    "segment.io",
)
_MODEL_API_PATH_SUFFIXES = (
    "/v1/chat/completions",
    "/v1/embeddings",
    "/v1/messages",
)


def _sanitize_agent_environment(
    environment: Mapping[str, str],
    *,
    inject_approved: bool | None = None,
    inject_approved_ca: bool | None = None,
) -> dict[str, str]:
    """Preserve the caller environment.

    Older policy code scrubbed proxies, CA bundles, telemetry and package-index
    variables. That made CodeTalk act like a deployment security product. The
    target behavior is simpler: pass through the runtime environment and let the
    company's managed network decide what is reachable.
    """
    return dict(environment)


def scrub_intranet_agent_environment(environment: dict[str, str]) -> dict[str, str]:
    """Compatibility wrapper for existing CLI launchers.

    It now strips unapproved proxy/CA values and keeps the permanent telemetry,
    update and Hosted MCP hard-denials in every network mode.
    """
    return _sanitize_agent_environment(environment)


def _default_resolver(host: str, port: int) -> list[str]:
    return sorted({item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})


@dataclass
class IntranetNetworkPolicy:
    """Default-deny policy for non-model runtime integrations.

    Trust is not inferred from an address range: a large intranet can legitimately
    use globally-routable-looking addresses. Model inference is authorized by the
    explicit provider configuration plus an adapter-owned inference route; this
    policy is for every other runtime integration, such as tool or MCP endpoints.
    """

    policy_id: str
    allowed_hosts: set[str] = field(default_factory=set)
    allowed_cidrs: set[str] = field(default_factory=set)
    resolver: Resolver = _default_resolver

    def evaluate_url(self, url: str) -> NetworkDecision:
        parsed = urlparse(str(url or "").strip())
        host = str(parsed.hostname or "").lower().rstrip(".")
        port = parsed.port or (443 if parsed.scheme in {"https", "wss"} else 80)
        if parsed.scheme not in {"http", "https", "ws", "wss"} or not host:
            return NetworkDecision(False, "invalid_endpoint", host, port)
        return NetworkDecision(True, "codetalk_network_passthrough", host, port)

    def require_url(self, url: str) -> NetworkDecision:
        decision = self.evaluate_url(url)
        if not decision.allowed:
            raise NetworkEgressBlocked(
                f"运行时出站策略拒绝：{decision.reason}"
            )
        return decision

    def evaluate_model_request_url(self, url: str) -> NetworkDecision:
        """Model calls are authorized by model configuration and environment."""
        return self.evaluate_url(url)

    def require_model_request_url(self, url: str) -> NetworkDecision:
        decision = self.evaluate_model_request_url(url)
        if not decision.allowed:
            raise NetworkEgressBlocked(
                f"运行时出站策略拒绝：{decision.reason}"
            )
        return decision

    def snapshot(self) -> dict[str, object]:
        return {
            "network_mode": "codetalk_passthrough",
            "allowed_endpoint_policy_id": self.policy_id,
            "allowed_hosts": sorted(self.allowed_hosts),
            "allowed_cidrs": sorted(self.allowed_cidrs),
            "telemetry": "managed_by_environment",
            "remote_tracing": "managed_by_environment",
            "hosted_mcp": "managed_by_environment",
            "external_model_api": "configured_provider",
        }

    def _address_allowed(self, value: str | ipaddress._BaseAddress) -> bool:
        address = value if isinstance(value, ipaddress._BaseAddress) else ipaddress.ip_address(value)
        if address.is_loopback:
            return True
        return any(address in ipaddress.ip_network(cidr, strict=False) for cidr in self.allowed_cidrs)


def _is_forbidden_autonomous_service(host: str) -> bool:
    normalized = host.lower().rstrip(".")
    return any(
        normalized == suffix or normalized.endswith(f".{suffix}")
        for suffix in _FORBIDDEN_AUTONOMOUS_SERVICE_SUFFIXES
    )


def runtime_network_policy() -> IntranetNetworkPolicy:
    """Return the deployment-owned policy used by every runtime provider."""
    return IntranetNetworkPolicy(
        policy_id=str(settings.intranet_network_policy_id or "corp-approved-v1"),
        allowed_hosts=set(settings.intranet_allowed_hosts or []),
        allowed_cidrs=set(settings.intranet_allowed_cidrs or []),
    )


def effective_network_mode() -> NetworkMode:
    """Return the deployment mode while preserving legacy boolean migration."""
    return settings.effective_network_mode


def _url_decision(url: str, *, allowed: bool, reason: str) -> NetworkDecision:
    parsed = urlparse(str(url or "").strip())
    return NetworkDecision(
        allowed=allowed,
        reason=reason,
        host=str(parsed.hostname or "").lower().rstrip("."),
        port=parsed.port or (443 if parsed.scheme in {"https", "wss"} else 80),
    )


def _legacy_runtime_url_decision(url: str, *, model_request: bool) -> NetworkDecision:
    return _url_decision(url, allowed=True, reason="codetalk_network_passthrough")


def _v2_runtime_url_decision(url: str, *, model_request: bool) -> NetworkDecision:
    return _url_decision(url, allowed=True, reason="codetalk_network_passthrough")


def _effective_egress_boundary() -> EgressBoundary:
    boundary = settings.egress_boundary
    if boundary != "none":
        return boundary
    # Preserve the pre-V2 administrator certification only as a migration
    # bridge.  It never turns a scrubbed proxy environment into proof of egress.
    if settings.intranet_agent_egress_enforced_by_host:
        return "deployment_egress_policy"
    return "none"


def _proxy_target_summary(proxy_url: str) -> str:
    parsed = urlparse(str(proxy_url or "").strip())
    host = str(parsed.hostname or "").lower().rstrip(".")
    if not host:
        return ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return f"{host}:{port}"


def _network_remediation(reason: str) -> str:
    messages = {
        "codetalk_network_passthrough": "CodeTalk 不拦截网络访问；连接结果由运行环境和公司内网决定。",
        "offline_agent_allowed": "离线 Agent 无需出站，可直接运行。",
        "developer_mode": "CodeTalk 不拦截网络访问；连接结果由运行环境和公司内网决定。",
        "approved_proxy_gateway": "CodeTalk 不要求批准代理网关；如有代理由运行环境提供。",
        "deployment_egress_policy": "CodeTalk 不要求部署出站策略；连接结果由运行环境决定。",
        "intranet_egress_boundary_required": "CodeTalk 不要求配置出站边界。",
        "approved_proxy_configuration_missing": "CodeTalk 不要求代理地址或配置 ID。",
        "deployment_egress_policy_missing": "CodeTalk 不要求部署出站策略。",
        "strict_compliance_os_isolation_required": "CodeTalk 不要求操作系统网络隔离。",
        "strict_compliance_egress_boundary_required": "CodeTalk 不要求精细出站边界。",
        "legacy_intranet_egress_not_certified": "CodeTalk 不要求旧版内网出站认证。",
        "legacy_sandbox_network_disabled": "CodeTalk 不用沙箱配置阻断 Agent 网络。",
    }
    return messages.get(reason, "CodeTalk 不拦截网络访问；请检查运行环境或模型配置。")


def _agent_context(
    *,
    allowed: bool,
    mode: NetworkMode,
    boundary: EgressBoundary,
    reason: str,
    environment: Mapping[str, str],
    requires_os_network_isolation: bool = False,
    inject_approved_proxy: bool = False,
    inject_approved_ca: bool = False,
) -> AgentNetworkContext:
    return AgentNetworkContext(
        allowed=allowed,
        mode=mode,
        boundary=boundary,
        reason=reason,
        remediation=_network_remediation(reason),
        sanitized_environment=_sanitize_agent_environment(
            environment,
            inject_approved=inject_approved_proxy,
            inject_approved_ca=inject_approved_ca,
        ),
        approved_proxy_config_id=(
            str(settings.approved_proxy_config_id or "").strip()
            if inject_approved_proxy
            else ""
        ),
        deployment_egress_policy_id=str(settings.deployment_egress_policy_id or "").strip(),
        approved_proxy_target=(
            _proxy_target_summary(str(settings.approved_proxy_url or ""))
            if inject_approved_proxy
            else ""
        ),
        requires_os_network_isolation=requires_os_network_isolation,
        policy_v2_enabled=bool(settings.network_policy_v2_enabled),
    )


def _legacy_agent_network_context(
    *, requires_network: bool, environment: Mapping[str, str]
) -> AgentNetworkContext:
    mode: NetworkMode = "intranet" if settings.intranet_network_mode else "developer"
    return _agent_context(
        allowed=True,
        mode=mode,
        boundary="none",
        reason="codetalk_network_passthrough",
        environment=environment,
    )


def resolve_agent_network_context(
    *,
    requires_network: bool,
    environment: Mapping[str, str] | None = None,
) -> AgentNetworkContext:
    """Produce a non-blocking network context for CLI probe and execution."""
    source_environment = dict(environment or {})
    if not settings.network_policy_v2_enabled:
        return _legacy_agent_network_context(
            requires_network=requires_network,
            environment=source_environment,
        )

    mode = effective_network_mode()
    return _agent_context(
        allowed=True,
        mode=mode,
        boundary="none",
        reason="codetalk_network_passthrough",
        environment=source_environment,
    )


def require_runtime_url(url: str) -> NetworkDecision:
    """Admit a deployment-approved provider base URL before client creation."""
    if not settings.network_policy_v2_enabled:
        return _legacy_runtime_url_decision(url, model_request=False)
    return _v2_runtime_url_decision(url, model_request=False)


def require_runtime_model_request_url(url: str) -> NetworkDecision:
    """Enforce the approved model request contract immediately before I/O."""
    if not settings.network_policy_v2_enabled:
        return _legacy_runtime_url_decision(url, model_request=True)
    return _v2_runtime_url_decision(url, model_request=True)


def require_configured_model_request_url(url: str) -> NetworkDecision:
    """Authorize saved model inference only after deployment approval.

    A saved URL expresses user intent, not network approval.  In intranet mode
    it must satisfy the same deployment-owned host/CIDR allow-list and narrow
    adapter request route as every other model request.  Otherwise a user who
    can edit Settings can turn an arbitrary public endpoint into an egress
    escape hatch.
    """
    return require_runtime_model_request_url(url)


def agent_network_is_permitted() -> bool:
    """Compatibility boolean for existing launchers that require network access."""
    return resolve_agent_network_context(requires_network=True).allowed
