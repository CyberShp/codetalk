"""Private-network admission control for CodeTalk runtime integrations."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Callable
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


Resolver = Callable[[str, int], list[str]]
_INTRANET_BLOCKED_ENV_KEYS = {
    "ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "FTP_PROXY", "NO_PROXY",
    "OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "LANGSMITH_TRACING", "LANGSMITH_ENDPOINT",
    "LANGSMITH_API_KEY", "OPENAI_TRACING", "OPENAI_TRACE", "CLAUDE_CODE_TELEMETRY",
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
    "/v1/messages",
    "/v1/models",
)


def scrub_intranet_agent_environment(environment: dict[str, str]) -> dict[str, str]:
    """Remove inherited public-egress channels before a provider subprocess starts."""
    result = {
        key: value
        for key, value in environment.items()
        if key.upper() not in _INTRANET_BLOCKED_ENV_KEYS
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
    })
    return result


def _default_resolver(host: str, port: int) -> list[str]:
    return sorted({item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})


@dataclass
class IntranetNetworkPolicy:
    """Default-deny policy for public endpoints in intranet deployments."""

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
                f"公网出口已被内网策略拒绝：{decision.reason}"
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
                f"公网出口已被内网策略拒绝：{decision.reason}"
            )
        return decision

    def snapshot(self) -> dict[str, object]:
        return {
            "network_mode": "intranet_deny_public",
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


def require_runtime_url(url: str) -> NetworkDecision:
    """Admit a deployment-approved provider base URL before client creation."""
    if not settings.intranet_network_mode:
        parsed = urlparse(str(url or "").strip())
        return NetworkDecision(
            allowed=True,
            reason="intranet_mode_disabled",
            host=str(parsed.hostname or "").lower().rstrip("."),
            port=parsed.port or (443 if parsed.scheme in {"https", "wss"} else 80),
        )
    return runtime_network_policy().require_url(url)


def require_runtime_model_request_url(url: str) -> NetworkDecision:
    """Enforce the approved model request contract immediately before I/O."""
    if not settings.intranet_network_mode:
        parsed = urlparse(str(url or "").strip())
        return NetworkDecision(
            allowed=True,
            reason="intranet_mode_disabled",
            host=str(parsed.hostname or "").lower().rstrip("."),
            port=parsed.port or (443 if parsed.scheme in {"https", "wss"} else 80),
        )
    return runtime_network_policy().require_model_request_url(url)


def agent_network_is_permitted() -> bool:
    """Fail closed until deployment has certified its private-only firewall."""
    if not settings.intranet_network_mode:
        return bool(settings.external_agent_sandbox_allow_network)
    return bool(settings.intranet_agent_egress_enforced_by_host)
