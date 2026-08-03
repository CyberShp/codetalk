"""Runtime environment passthrough helpers.

CodeTalk does not implement network approval, endpoint allow-lists, proxy/CA
injection, or Agent egress decisions.  The operating system and the user's
General Settings own connectivity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


class NetworkEgressBlocked(RuntimeError):
    """Legacy exception name retained only for import stability; never raised."""


@dataclass(frozen=True)
class RuntimeEnvironmentContext:
    sanitized_environment: dict[str, str]
    requires_network: bool
    allowed: bool = True
    reason: str = "runtime_environment_passthrough"
    remediation: str = "请检查运行环境、模型配置或 CLI 自身网络设置。"
    requires_os_network_isolation: bool = False

    def require_allowed(self) -> "RuntimeEnvironmentContext":
        return self

    def snapshot(self) -> dict[str, object]:
        return {
            "source": "runtime_environment",
            "requires_network": self.requires_network,
            "allowed": True,
        }


def resolve_agent_network_context(
    *,
    requires_network: bool,
    environment: Mapping[str, str] | None = None,
) -> RuntimeEnvironmentContext:
    return RuntimeEnvironmentContext(
        sanitized_environment=dict(environment or {}),
        requires_network=requires_network,
    )


def scrub_intranet_agent_environment(environment: dict[str, str]) -> dict[str, str]:
    return dict(environment)


def require_runtime_url(url: str) -> None:
    return None


def require_runtime_model_request_url(url: str) -> None:
    return None


def require_configured_model_request_url(url: str) -> None:
    return None


def agent_network_is_permitted() -> bool:
    return True
