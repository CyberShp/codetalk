"""Domain-neutral request and result values for governance handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


GovernanceNodeKind = Literal["validator", "governance"]
GovernanceStatus = Literal["passed", "warning", "failed"]
DeliveryStatus = Literal["ready", "blocked"]


@dataclass(frozen=True)
class DeclaredGovernanceOutput:
    artifact_id: str
    path: str
    producer_node_id: str
    producer_port_id: str
    producer_port_key: str = ""


@dataclass(frozen=True)
class GovernanceOutputEdge:
    edge_id: str
    source_node_id: str
    source_port_id: str
    target_artifact_id: str


@dataclass(frozen=True)
class GeneratedGovernanceArtifact:
    """A candidate payload; the Orchestrator remains responsible for commit."""

    artifact_id: str
    content: str
    media_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    artifact_id: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationResult:
    status: Literal["passed", "failed"]
    issues: tuple[ValidationIssue, ...] = ()


@dataclass(frozen=True)
class GovernancePluginRequest:
    handler_id: str
    node_id: str
    node_kind: GovernanceNodeKind
    artifact_dir: str
    inputs: dict[str, Any] = field(default_factory=dict)
    required_output_ids: tuple[str, ...] = ()
    requested_output_ids: tuple[str, ...] = ()
    declared_outputs: tuple[DeclaredGovernanceOutput, ...] = ()
    output_edges: tuple[GovernanceOutputEdge, ...] = ()
    blocking: bool = True


@dataclass(frozen=True)
class GovernancePortDescriptor:
    key: str
    label: str
    port_type: str = "artifact"
    required: bool = True
    collection: bool = False


@dataclass(frozen=True)
class GovernancePluginDescriptor:
    handler_id: str
    handler_version: int
    node_kind: GovernanceNodeKind
    capabilities: tuple[str, ...] = ()
    input_ports: tuple[GovernancePortDescriptor, ...] = ()
    output_ports: tuple[GovernancePortDescriptor, ...] = ()


@dataclass(frozen=True)
class GovernancePluginExecution:
    status: Literal["passed", "failed"]
    validation: ValidationResult | None = None
    produced_artifacts: tuple[GeneratedGovernanceArtifact, ...] = ()
    message: str = ""
    error_code: str = ""


@dataclass(frozen=True)
class GovernancePluginResult:
    handler_id: str
    handler_version: int
    node_id: str
    node_kind: GovernanceNodeKind
    governance_status: GovernanceStatus
    delivery_status: DeliveryStatus
    validation: ValidationResult | None = None
    produced_artifacts: tuple[GeneratedGovernanceArtifact, ...] = ()
    message: str = ""
    error_code: str = ""


class GovernancePlugin(Protocol):
    def execute(self, request: GovernancePluginRequest) -> GovernancePluginExecution:
        ...
