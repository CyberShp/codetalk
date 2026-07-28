"""Stable, domain-neutral values exchanged across the Harness boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ProviderCapabilities:
    streaming: bool
    tool_call: bool
    session_resume: bool
    structured_output: bool
    mcp: bool
    skills: bool
    cancellation: bool


@dataclass(frozen=True)
class ProviderResumeToken:
    provider: str
    value: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderSession:
    session_id: str
    provider: str
    resume_token: ProviderResumeToken | None = None
    requires_network: bool = True
    artifact_dir: str = ""
    mcp_profile: str = ""
    prompt_transport: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def run_id(self) -> str:
        """Compatibility alias while workflow callers migrate to session_id."""
        return self.session_id


@dataclass(frozen=True)
class ArtifactCandidate:
    path: str
    kind: str = "file"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CancelResult:
    session_id: str
    status: Literal["cancelled", "already_terminal", "failed"]
    message: str = ""


@dataclass(frozen=True)
class ProviderUnsupported:
    operation: str
    capability: str
    message: str
    code: str = "unsupported_capability"
