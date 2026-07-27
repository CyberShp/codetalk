"""Purpose-based egress admission control for CodeTalk runtime integrations."""

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
            "telemetry": "disabled",
            "remote_tracing": "disabled",
            "hosted_mcp": "forbidden",
            "automatic_update": "disabled",
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
    """Drop inherited egress credentials and inject only deployment settings."""
    result = {
        key: value
        for key, value in environment.items()
        if key.upper() not in _INTRANET_BLOCKED_ENV_KEYS | _PROXY_AND_CA_ENV_KEYS
    }
    result.update({
        "DO_NOT_TRACK": "1",
        "CODEX_DISABLE_AUTO_UPDATE": "1",
        "CLAUDE_CODE_DISABLE_TELEMETRY": "1",
        "OPENCODE_DISABLE_TELEMETRY": "1",
        "OPENAI_AGENTS_DISABLE_TRACING": "1",
        "OPENAI_AGENTS_DONT_LOG_MODEL_DATA": "1",
        "OPENAI_AGENTS_DONT_LOG_TOOL_DATA": "1",
        "LANGCHAIN_TRACING_V2": "false",
        "LANGSMITH_TRACING": "false",
        "OTEL_SDK_DISABLED": "true",
        "PIP_NO_INDEX": "1",
        "UV_OFFLINE": "1",
        "NPM_CONFIG_UPDATE_NOTIFIER": "false",
        "NO_UPDATE_NOTIFIER": "1",
        "DISABLE_AUTO_UPDATE": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    })
    if inject_approved is None:
        inject_approved = bool(settings.network_policy_v2_enabled)
    if inject_approved_ca is None:
        inject_approved_ca = inject_approved
    if inject_approved:
        approved_proxy = str(settings.approved_proxy_url or "").strip()
        if approved_proxy:
            result.update({
                "HTTP_PROXY": approved_proxy,
                "HTTPS_PROXY": approved_proxy,
                "ALL_PROXY": approved_proxy,
            })
        approved_no_proxy = str(settings.approved_no_proxy or "").strip()
        if approved_no_proxy:
            result["NO_PROXY"] = approved_no_proxy
    if inject_approved_ca:
        approved_ca = str(settings.approved_ca_bundle_path or "").strip()
        if approved_ca:
            result.update({
                "REQUESTS_CA_BUNDLE": approved_ca,
                "SSL_CERT_FILE": approved_ca,
                "CURL_CA_BUNDLE": approved_ca,
                "NODE_EXTRA_CA_CERTS": approved_ca,
            })
    return result


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
        if host in {"localhost", "localhost.localdomain"}:
            return NetworkDecision(True, "loopback_hostname", host, port)
        if _is_forbidden_autonomous_service(host):
            return NetworkDecision(False, "autonomous_service_forbidden", host, port)
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if host not in {item.lower().rstrip(".") for item in self.allowed_hosts}:
                return NetworkDecision(False, "host_not_allowlisted", host, port)
            try:
                resolved = self.resolver(host, port)
            except OSError:
                return NetworkDecision(False, "hostname_resolution_failed", host, port)
            if not resolved:
                return NetworkDecision(False, "hostname_resolution_failed", host, port)
            # An approved corporate name may resolve to a globally-routable
            # address in a large enterprise. Host approval is the authority;
            # DNS and egress firewalls must be deployment-owned.
            return NetworkDecision(True, "approved_hostname", host, port)
        return NetworkDecision(
            self._address_allowed(address),
            "approved_direct_address" if self._address_allowed(address) else "direct_address_not_allowlisted",
            host,
            port,
        )

    def require_url(self, url: str) -> NetworkDecision:
        decision = self.evaluate_url(url)
        if not decision.allowed:
            raise NetworkEgressBlocked(
                f"运行时出站策略拒绝：{decision.reason}"
            )
        return decision

    def evaluate_model_request_url(self, url: str) -> NetworkDecision:
        """Admit only adapter-defined model API requests after host approval.

        This keeps an approved provider host from becoming a general escape hatch
        for tracing, telemetry, hosted MCP, update or arbitrary SDK endpoints.
        """
        decision = self.evaluate_url(url)
        if not decision.allowed:
            return decision
        path = urlparse(str(url or "").strip()).path.rstrip("/")
        if not any(path.endswith(suffix) for suffix in _MODEL_API_PATH_SUFFIXES):
            return NetworkDecision(
                False,
                "model_endpoint_path_forbidden",
                decision.host,
                decision.port,
            )
        return decision

    def require_model_request_url(self, url: str) -> NetworkDecision:
        decision = self.evaluate_model_request_url(url)
        if not decision.allowed:
            raise NetworkEgressBlocked(
                f"运行时出站策略拒绝：{decision.reason}"
            )
        return decision

    def snapshot(self) -> dict[str, object]:
        return {
            "network_mode": "intranet_controlled_egress",
            "allowed_endpoint_policy_id": self.policy_id,
            "allowed_hosts": sorted(self.allowed_hosts),
            "allowed_cidrs": sorted(self.allowed_cidrs),
            "telemetry": "disabled",
            "remote_tracing": "disabled",
            "hosted_mcp": "forbidden",
            "external_model_api": "approved_only",
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
    if not settings.intranet_network_mode:
        return _url_decision(url, allowed=True, reason="intranet_mode_disabled")
    policy = runtime_network_policy()
    return (
        policy.require_model_request_url(url)
        if model_request
        else policy.require_url(url)
    )


def _v2_runtime_url_decision(url: str, *, model_request: bool) -> NetworkDecision:
    mode = effective_network_mode()
    if mode == "developer":
        host = str(urlparse(str(url or "").strip()).hostname or "").lower().rstrip(".")
        if _is_forbidden_autonomous_service(host):
            raise NetworkEgressBlocked("运行时出站策略拒绝：autonomous_service_forbidden")
        return _url_decision(url, allowed=True, reason="developer_mode")
    if mode == "strict_compliance":
        parsed = urlparse(str(url or "").strip())
        host = str(parsed.hostname or "").lower().rstrip(".")
        if host in {"localhost", "localhost.localdomain", "127.0.0.1", "::1"}:
            return _url_decision(url, allowed=True, reason="strict_loopback_allowed")
        raise NetworkEgressBlocked("运行时出站策略拒绝：strict_compliance_network_disabled")
    policy = runtime_network_policy()
    return (
        policy.require_model_request_url(url)
        if model_request
        else policy.require_url(url)
    )


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
        "offline_agent_allowed": "离线 Agent 无需出站，可直接运行。",
        "developer_mode": "开发模式允许执行；遥测、更新和 Hosted MCP 仍已关闭。",
        "approved_proxy_gateway": "已使用部署批准的代理网关。",
        "deployment_egress_policy": "已使用部署声明的出站策略。",
        "intranet_egress_boundary_required": "请由管理员配置批准代理网关或部署出站策略。",
        "approved_proxy_configuration_missing": "请配置 approved_proxy_url 和 approved_proxy_config_id。",
        "deployment_egress_policy_missing": "请配置 deployment_egress_policy_id。",
        "strict_compliance_os_isolation_required": "严格合规模式要求管理员启用 OS 网络隔离。",
        "strict_compliance_egress_boundary_required": "严格合规模式联网执行需要精细出口网关或部署出站策略。",
        "legacy_intranet_egress_not_certified": "旧内网配置未认证出站边界，请配置新网络策略或部署侧出站控制。",
        "legacy_sandbox_network_disabled": "旧配置已禁用 Agent 网络访问。",
    }
    return messages.get(reason, "请检查部署级网络策略配置。")


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
    if not requires_network:
        return _agent_context(
            allowed=True,
            mode=mode,
            boundary="none",
            reason="offline_agent_allowed",
            environment=environment,
        )
    if settings.intranet_network_mode:
        allowed = bool(settings.intranet_agent_egress_enforced_by_host)
        return _agent_context(
            allowed=allowed,
            mode="intranet",
            boundary="deployment_egress_policy" if allowed else "none",
            reason=("deployment_egress_policy" if allowed else "legacy_intranet_egress_not_certified"),
            environment=environment,
        )
    allowed = bool(settings.external_agent_sandbox_allow_network)
    return _agent_context(
        allowed=allowed,
        mode="developer",
        boundary="none",
        reason="developer_mode" if allowed else "legacy_sandbox_network_disabled",
        environment=environment,
    )


def resolve_agent_network_context(
    *,
    requires_network: bool,
    environment: Mapping[str, str] | None = None,
) -> AgentNetworkContext:
    """Produce the one network decision shared by probe and real CLI execution.

    The caller must tell the policy whether this adapter needs network access.
    This lets intranet mode reject only networked CLIs while preserving offline
    repository agents.  The returned environment is always scrubbed; inherited
    proxy, CA, telemetry and update variables are never evidence of approval.
    """
    source_environment = dict(environment or {})
    if not settings.network_policy_v2_enabled:
        return _legacy_agent_network_context(
            requires_network=requires_network,
            environment=source_environment,
        )

    mode = effective_network_mode()
    boundary = _effective_egress_boundary()
    if mode == "developer":
        inject_proxy = bool(
            requires_network
            and boundary == "approved_proxy_gateway"
            and settings.approved_proxy_url
            and settings.approved_proxy_config_id
        )
        return _agent_context(
            allowed=True,
            mode=mode,
            boundary=boundary,
            reason="developer_mode" if requires_network else "offline_agent_allowed",
            environment=source_environment,
            inject_approved_proxy=inject_proxy,
            inject_approved_ca=requires_network,
        )

    if mode == "strict_compliance":
        if not settings.strict_compliance_os_network_isolation_enabled:
            return _agent_context(
                allowed=False,
                mode=mode,
                boundary=boundary,
                reason="strict_compliance_os_isolation_required",
                environment=source_environment,
                requires_os_network_isolation=True,
            )
        if not requires_network:
            return _agent_context(
                allowed=True,
                mode=mode,
                boundary=boundary,
                reason="offline_agent_allowed",
                environment=source_environment,
                requires_os_network_isolation=True,
            )
        if boundary == "none":
            return _agent_context(
                allowed=False,
                mode=mode,
                boundary=boundary,
                reason="strict_compliance_egress_boundary_required",
                environment=source_environment,
                requires_os_network_isolation=True,
            )

    if not requires_network:
        return _agent_context(
            allowed=True,
            mode=mode,
            boundary=boundary,
            reason="offline_agent_allowed",
            environment=source_environment,
            requires_os_network_isolation=True,
        )
    if boundary == "none":
        return _agent_context(
            allowed=False,
            mode=mode,
            boundary=boundary,
            reason="intranet_egress_boundary_required",
            environment=source_environment,
        )
    if boundary == "approved_proxy_gateway":
        configured = bool(settings.approved_proxy_url and settings.approved_proxy_config_id)
        return _agent_context(
            allowed=configured,
            mode=mode,
            boundary=boundary,
            reason="approved_proxy_gateway" if configured else "approved_proxy_configuration_missing",
            environment=source_environment,
            requires_os_network_isolation=mode == "strict_compliance",
            inject_approved_proxy=configured,
            inject_approved_ca=configured,
        )
    configured = bool(
        settings.deployment_egress_policy_id
        or settings.intranet_agent_egress_enforced_by_host
    )
    return _agent_context(
        allowed=configured,
        mode=mode,
        boundary="deployment_egress_policy",
        reason="deployment_egress_policy" if configured else "deployment_egress_policy_missing",
        environment=source_environment,
        requires_os_network_isolation=mode == "strict_compliance",
        inject_approved_ca=configured,
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
