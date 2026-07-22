"""Private-network admission control for CodeTalk runtime integrations."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse


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
            if all(self._address_allowed(value) for value in resolved):
                return NetworkDecision(True, "approved_internal_hostname", host, port)
            return NetworkDecision(False, "resolved_public_address", host, port)
        return NetworkDecision(
            self._address_allowed(address),
            "approved_private_address" if self._address_allowed(address) else "public_address",
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

    def snapshot(self) -> dict[str, object]:
        return {
            "network_mode": "intranet_deny_public",
            "allowed_endpoint_policy_id": self.policy_id,
            "allowed_hosts": sorted(self.allowed_hosts),
            "allowed_cidrs": sorted(self.allowed_cidrs),
            "telemetry": "disabled",
            "remote_tracing": "disabled",
            "hosted_mcp": "forbidden",
            "external_model_api": "forbidden",
        }

    def _address_allowed(self, value: str | ipaddress._BaseAddress) -> bool:
        address = value if isinstance(value, ipaddress._BaseAddress) else ipaddress.ip_address(value)
        if address.is_loopback or address.is_private or address.is_link_local:
            return True
        return any(address in ipaddress.ip_network(cidr, strict=False) for cidr in self.allowed_cidrs)
