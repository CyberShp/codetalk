"""Independent causal-depth evaluation anchored to evaluator-only truth.

The hidden truth package owns ``evidence_catalog_sha256``. P3 loads that
hash-verified truth, constructs the typed catalog, and passes it directly to
``evaluate_depth``. Candidate artifacts can cite catalog refs, but never supply
or replace the catalog itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.services.quality_evaluation_contract import (
    AxisResult,
    AxisStatus,
    CriticalMiss,
    LayerStatus,
    MetricName,
    RatioMetric,
    ValidationLayer,
    ValidationLayerOutcome,
    ValidationLayers,
)

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ExecutionTier(str, Enum):
    STATIC = "S"
    EXECUTABLE = "E"
    HARDWARE = "H"


class DepthNodeKind(str, Enum):
    TRIGGER = "trigger"
    PRECONDITION = "precondition"
    INPUT = "input"
    PRECONDITION_INPUT = "precondition_input"
    ENTRY = "entry"
    CALL = "call"
    CALL_CHAIN = "call_chain"
    STATE_MUTATION = "state_mutation"
    STATE_RESOURCE_MUTATION = "state_resource_mutation"
    RESOURCE_ACQUISITION = "resource_acquisition"
    RESOURCE_OWNERSHIP = "resource_ownership"
    RESOURCE_MUTATION = "resource_mutation"
    RESOURCE_RELEASE = "resource_release"
    DOWNSTREAM_EFFECT = "downstream_effect"
    ERROR_PROPAGATION = "error_propagation"
    CLEANUP = "cleanup"
    RECOVERY = "recovery"
    CLEANUP_RECOVERY = "cleanup_recovery"
    EXTERNAL_OBSERVATION = "external_observation"
    EXECUTABLE_ORACLE = "executable_oracle"


class ObligationStatus(str, Enum):
    CLOSED = "closed"
    OPEN = "open"


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_RUN = "not_run"


class EvidenceBindingCategory(str, Enum):
    NODE = "node"
    EDGE = "edge"
    CHECK = "check"
    L3 = "l3"


class DepthInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RequiredDepthNode(DepthInputModel):
    node_id: NonEmptyString
    kind: DepthNodeKind = Field(strict=False)
    statement: NonEmptyString
    critical: bool = True


class RequiredDepthEdge(DepthInputModel):
    edge_id: NonEmptyString
    source_node_id: NonEmptyString
    target_node_id: NonEmptyString
    statement: NonEmptyString
    critical: bool = True


class RequiredDisconfirmingCheck(DepthInputModel):
    check_id: NonEmptyString
    statement: NonEmptyString
    critical: bool = True


class CriticalDepthChain(DepthInputModel):
    chain_id: NonEmptyString
    nodes: tuple[RequiredDepthNode, ...] = Field(strict=False, min_length=2)
    edges: tuple[RequiredDepthEdge, ...] = Field(strict=False, min_length=1)
    disconfirming_checks: tuple[RequiredDisconfirmingCheck, ...] = Field(
        default=(), strict=False
    )

    @model_validator(mode="after")
    def validate_ordered_graph(self) -> CriticalDepthChain:
        _require_unique(
            (node.node_id for node in self.nodes),
            label="node",
            owner=self.chain_id,
        )
        _require_unique(
            (edge.edge_id for edge in self.edges),
            label="edge",
            owner=self.chain_id,
        )
        _require_unique(
            (check.check_id for check in self.disconfirming_checks),
            label="disconfirming check",
            owner=self.chain_id,
        )
        kind_groups = {
            "trigger": {DepthNodeKind.TRIGGER},
            "precondition_or_input": {
                DepthNodeKind.PRECONDITION,
                DepthNodeKind.INPUT,
                DepthNodeKind.PRECONDITION_INPUT,
            },
            "entry": {DepthNodeKind.ENTRY},
            "call_or_call_chain": {DepthNodeKind.CALL, DepthNodeKind.CALL_CHAIN},
            "state_or_resource_mutation": {
                DepthNodeKind.STATE_MUTATION,
                DepthNodeKind.RESOURCE_MUTATION,
                DepthNodeKind.STATE_RESOURCE_MUTATION,
            },
            "downstream_effect": {DepthNodeKind.DOWNSTREAM_EFFECT},
            "error_propagation": {DepthNodeKind.ERROR_PROPAGATION},
            "cleanup_or_recovery": {
                DepthNodeKind.CLEANUP,
                DepthNodeKind.RECOVERY,
                DepthNodeKind.CLEANUP_RECOVERY,
            },
            "external_observation": {DepthNodeKind.EXTERNAL_OBSERVATION},
            "executable_oracle": {DepthNodeKind.EXECUTABLE_ORACLE},
            "resource_acquisition": {DepthNodeKind.RESOURCE_ACQUISITION},
            "resource_ownership": {DepthNodeKind.RESOURCE_OWNERSHIP},
            "resource_release": {DepthNodeKind.RESOURCE_RELEASE},
        }
        actual_kinds = {node.kind for node in self.nodes}
        missing_kind_ids = [
            kind_id
            for kind_id, accepted_kinds in kind_groups.items()
            if actual_kinds.isdisjoint(accepted_kinds)
        ]
        if missing_kind_ids:
            raise ValueError(
                f"chain {self.chain_id!r} missing required depth kinds: "
                + ", ".join(missing_kind_ids)
            )
        trigger_count = sum(
            node.kind is DepthNodeKind.TRIGGER for node in self.nodes
        )
        if trigger_count != 1:
            raise ValueError(
                f"chain {self.chain_id!r} requires exactly one trigger"
            )
        oracle_count = sum(
            node.kind is DepthNodeKind.EXECUTABLE_ORACLE for node in self.nodes
        )
        if oracle_count != 1:
            raise ValueError(
                f"chain {self.chain_id!r} requires exactly one executable_oracle"
            )
        if not self.disconfirming_checks:
            raise ValueError(
                f"chain {self.chain_id!r} requires at least one disconfirming_check"
            )
        stage_order = (
            "trigger",
            "precondition_or_input",
            "entry",
            "call_or_call_chain",
            "state_or_resource_mutation",
            "downstream_effect",
            "error_propagation",
            "cleanup_or_recovery",
            "external_observation",
            "executable_oracle",
        )
        stage_positions = tuple(
            min(
                index
                for index, node in enumerate(self.nodes)
                if node.kind in kind_groups[stage_id]
            )
            for stage_id in stage_order
        )
        if any(left >= right for left, right in pairwise(stage_positions)):
            raise ValueError(
                f"chain {self.chain_id!r} violates required causal stage order"
            )
        lifecycle_positions = tuple(
            min(
                index
                for index, node in enumerate(self.nodes)
                if node.kind in kind_groups[kind_id]
            )
            for kind_id in (
                "resource_acquisition",
                "resource_ownership",
                "resource_release",
            )
        )
        if not (
            lifecycle_positions[0]
            < lifecycle_positions[1]
            < lifecycle_positions[2]
        ):
            raise ValueError(
                f"chain {self.chain_id!r} violates resource lifecycle order"
            )
        if self.nodes[0].kind is not DepthNodeKind.TRIGGER:
            raise ValueError(
                f"chain {self.chain_id!r} first node must be trigger"
            )
        if self.nodes[-1].kind is not DepthNodeKind.EXECUTABLE_ORACLE:
            raise ValueError(
                f"chain {self.chain_id!r} last node must be executable_oracle"
            )

        positions = {node.node_id: index for index, node in enumerate(self.nodes)}
        for edge in self.edges:
            if edge.source_node_id not in positions or edge.target_node_id not in positions:
                raise ValueError(
                    f"chain {self.chain_id!r} edge {edge.edge_id!r} references an unknown node"
                )
            if positions[edge.source_node_id] >= positions[edge.target_node_id]:
                raise ValueError(
                    f"chain {self.chain_id!r} edge {edge.edge_id!r} must point forward"
                )

        reachable = {self.nodes[0].node_id}
        for node in self.nodes[1:]:
            if any(
                edge.source_node_id in reachable
                and edge.target_node_id == node.node_id
                for edge in self.edges
            ):
                reachable.add(node.node_id)
        all_node_ids = set(positions)
        if reachable != all_node_ids:
            raise ValueError(
                f"chain {self.chain_id!r} contains a node disconnected from its trigger"
            )

        reaches_final = {self.nodes[-1].node_id}
        for node in reversed(self.nodes[:-1]):
            if any(
                edge.source_node_id == node.node_id
                and edge.target_node_id in reaches_final
                for edge in self.edges
            ):
                reaches_final.add(node.node_id)
        if reaches_final != all_node_ids:
            raise ValueError(
                f"chain {self.chain_id!r} contains a node disconnected from its oracle"
            )
        edge_pairs = {
            (edge.source_node_id, edge.target_node_id) for edge in self.edges
        }
        for source, target in pairwise(self.nodes):
            if (source.node_id, target.node_id) not in edge_pairs:
                raise ValueError(
                    f"chain {self.chain_id!r} missing consecutive edge "
                    f"{source.node_id!r} -> {target.node_id!r}"
                )
        return self


class DepthTruth(DepthInputModel):
    case_id: NonEmptyString
    evidence_catalog_sha256: Sha256Digest
    execution_tier: ExecutionTier = Field(strict=False)
    chains: tuple[CriticalDepthChain, ...] = Field(strict=False, min_length=1)

    @model_validator(mode="after")
    def unique_chain_ids(self) -> DepthTruth:
        _require_unique(
            (chain.chain_id for chain in self.chains),
            label="chain",
            owner=self.case_id,
        )
        return self


class ObservedDepthNode(DepthInputModel):
    node_id: NonEmptyString
    status: ObligationStatus = Field(default=ObligationStatus.CLOSED, strict=False)
    evidence_refs: tuple[NonEmptyString, ...] = Field(default=(), strict=False)


class ObservedDepthEdge(DepthInputModel):
    edge_id: NonEmptyString
    status: ObligationStatus = Field(default=ObligationStatus.CLOSED, strict=False)
    evidence_refs: tuple[NonEmptyString, ...] = Field(default=(), strict=False)


class ObservedDisconfirmingCheck(DepthInputModel):
    check_id: NonEmptyString
    status: CheckStatus = Field(strict=False)
    evidence_refs: tuple[NonEmptyString, ...] = Field(default=(), strict=False)


class CandidateDepthChain(DepthInputModel):
    chain_id: NonEmptyString
    nodes: tuple[ObservedDepthNode, ...] = Field(default=(), strict=False)
    edges: tuple[ObservedDepthEdge, ...] = Field(default=(), strict=False)
    disconfirming_checks: tuple[ObservedDisconfirmingCheck, ...] = Field(
        default=(), strict=False
    )
    narrative: str = ""

    @model_validator(mode="after")
    def unique_obligation_ids(self) -> CandidateDepthChain:
        _require_unique(
            (node.node_id for node in self.nodes),
            label="node",
            owner=self.chain_id,
        )
        _require_unique(
            (edge.edge_id for edge in self.edges),
            label="edge",
            owner=self.chain_id,
        )
        _require_unique(
            (check.check_id for check in self.disconfirming_checks),
            label="disconfirming check",
            owner=self.chain_id,
        )
        return self


class L3ChainEvidence(DepthInputModel):
    chain_id: NonEmptyString
    evidence_refs: tuple[NonEmptyString, ...] = Field(strict=False, min_length=1)


class L3ExecutionEvidence(DepthInputModel):
    status: LayerStatus = Field(strict=False)
    chain_evidence: tuple[L3ChainEvidence, ...] = Field(default=(), strict=False)
    limitations: tuple[NonEmptyString, ...] = Field(default=(), strict=False)

    @model_validator(mode="after")
    def validate_status(self) -> L3ExecutionEvidence:
        _require_unique(
            (record.chain_id for record in self.chain_evidence),
            label="L3 chain evidence",
            owner="L3 execution evidence",
        )
        ref_owners: dict[str, str] = {}
        for record in self.chain_evidence:
            for evidence_ref in record.evidence_refs:
                owner = ref_owners.setdefault(evidence_ref, record.chain_id)
                if owner != record.chain_id:
                    raise ValueError("L3 evidence refs must be distinct per chain")
        if self.status is LayerStatus.PASS:
            if self.limitations:
                raise ValueError("L3 pass cannot contain limitations")
            if not self.chain_evidence:
                raise ValueError("L3 pass requires per-chain evidence")
        if self.status is LayerStatus.NOT_RUN:
            if not self.limitations:
                raise ValueError("L3 not_run requires a limitation")
            if self.chain_evidence:
                raise ValueError("L3 not_run cannot contain chain evidence")
        if self.status is LayerStatus.NOT_APPLICABLE:
            if self.limitations:
                raise ValueError("L3 not_applicable cannot contain limitations")
            if self.chain_evidence:
                raise ValueError("L3 not_applicable cannot contain chain evidence")
        return self


class DepthExecutionOracle(DepthInputModel):
    oracle_id: NonEmptyString
    chain_id: NonEmptyString
    command_id: NonEmptyString
    command_sha256: Sha256Digest
    fixture_path: NonEmptyString
    fixture_sha256: Sha256Digest
    expected_result_sha256: Sha256Digest
    evidence_ref: NonEmptyString
    timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=300)]
    requirements: tuple[NonEmptyString, ...] = Field(default=(), strict=False)

    @model_validator(mode="after")
    def validate_contract_identity(self) -> DepthExecutionOracle:
        fixture = PurePosixPath(self.fixture_path)
        if (
            fixture.is_absolute()
            or not fixture.parts
            or any(part in {"", ".", ".."} for part in fixture.parts)
            or fixture.as_posix() != self.fixture_path
        ):
            raise ValueError("fixture_path must be a canonical relative path")
        if not self.evidence_ref.startswith("oracle://"):
            raise ValueError("execution oracle evidence_ref must use oracle://")
        expected_fragment = f"sha256={self.expected_result_sha256}"
        if self.evidence_ref.rpartition("#")[2] != expected_fragment:
            raise ValueError(
                "execution oracle evidence_ref must bind expected_result_sha256"
            )
        _require_unique(
            self.requirements,
            label="execution requirement",
            owner=self.oracle_id,
        )
        return self


class DepthExecutionPlan(DepthInputModel):
    schema_version: Literal["quality-depth-execution-v1"]
    case_id: NonEmptyString
    execution_tier: ExecutionTier = Field(strict=False)
    policy: Literal["disabled", "allowlisted", "unavailable"]
    oracles: tuple[DepthExecutionOracle, ...] = Field(default=(), strict=False)
    limitations: tuple[NonEmptyString, ...] = Field(default=(), strict=False)

    @model_validator(mode="after")
    def validate_execution_policy(self) -> DepthExecutionPlan:
        _require_unique(
            (oracle.oracle_id for oracle in self.oracles),
            label="execution oracle",
            owner=self.case_id,
        )
        _require_unique(
            (oracle.chain_id for oracle in self.oracles),
            label="execution oracle chain",
            owner=self.case_id,
        )
        _require_unique(
            (oracle.evidence_ref for oracle in self.oracles),
            label="execution oracle evidence",
            owner=self.case_id,
        )
        _require_unique(
            self.limitations,
            label="execution limitation",
            owner=self.case_id,
        )
        if self.execution_tier is ExecutionTier.STATIC:
            if self.policy != "disabled" or self.oracles or self.limitations:
                raise ValueError("Tier S execution must be disabled without limitations")
            return self
        if self.policy == "allowlisted":
            if not self.oracles:
                raise ValueError("allowlisted execution requires at least one oracle")
            if self.limitations:
                raise ValueError("allowlisted execution cannot predeclare limitations")
            return self
        if self.policy == "unavailable":
            if self.oracles:
                raise ValueError("unavailable execution cannot declare runnable oracles")
            if not self.limitations:
                raise ValueError("unavailable execution requires a limitation")
            return self
        raise ValueError("Tier E/H execution cannot be disabled")


@dataclass(frozen=True)
class DepthOracleCommandContract:
    """Evaluator-owned command identity; truth may reference but never define argv."""

    command_id: str
    argv: tuple[str, ...]

    def __post_init__(self) -> None:
        command_id = self.command_id.strip()
        if not command_id or command_id != self.command_id:
            raise ValueError("command_id must be non-empty and canonical")
        if not self.argv or not all(isinstance(item, str) and item for item in self.argv):
            raise ValueError("oracle command argv must be non-empty strings")
        executable = Path(self.argv[0])
        if not executable.is_absolute():
            raise ValueError("oracle command executable must be absolute")
        if self.argv.count("{fixture}") != 1:
            raise ValueError("oracle command must contain exactly one {fixture} placeholder")


@dataclass(frozen=True)
class DepthOracleExecutionRun:
    evidence: L3ExecutionEvidence
    audit: dict[str, Any]


def depth_oracle_command_sha256(contract: DepthOracleCommandContract) -> str:
    payload = {
        "schema_version": "quality-depth-command-contract-v1",
        "command_id": contract.command_id,
        "argv": list(contract.argv),
        "network": "disabled",
        "shell": False,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class DepthCandidate(DepthInputModel):
    chains: tuple[CandidateDepthChain, ...] = Field(default=(), strict=False)
    l3: L3ExecutionEvidence | None = None

    @model_validator(mode="after")
    def unique_chain_ids(self) -> DepthCandidate:
        _require_unique(
            (chain.chain_id for chain in self.chains),
            label="candidate chain",
            owner="depth candidate",
        )
        return self


class TrustedEvidenceBinding(DepthInputModel):
    evidence_ref: NonEmptyString
    chain_id: NonEmptyString
    category: EvidenceBindingCategory = Field(strict=False)
    obligation_id: NonEmptyString

    @model_validator(mode="after")
    def validate_evidence_identity(self) -> TrustedEvidenceBinding:
        expected_scheme = {
            EvidenceBindingCategory.NODE: "source",
            EvidenceBindingCategory.EDGE: "source",
            EvidenceBindingCategory.CHECK: "test",
            EvidenceBindingCategory.L3: "oracle",
        }[self.category]
        prefix = f"{expected_scheme}://"
        if not self.evidence_ref.startswith(prefix):
            raise ValueError(
                f"{self.category.value} evidence_ref scheme must be {expected_scheme}://"
            )
        identity, separator, fragment = self.evidence_ref[len(prefix) :].partition("#")
        if (
            not separator
            or not identity
            or not fragment
            or any(character.isspace() for character in self.evidence_ref)
            or "#" in fragment
        ):
            raise ValueError(
                "evidence_ref requires a nonempty source identity and fragment"
            )
        return self


class DepthEvidenceCatalog(DepthInputModel):
    case_id: NonEmptyString
    bindings: tuple[TrustedEvidenceBinding, ...] = Field(strict=False, min_length=1)

    @model_validator(mode="after")
    def unique_bindings(self) -> DepthEvidenceCatalog:
        seen_refs: set[str] = set()
        for binding in self.bindings:
            if binding.evidence_ref in seen_refs:
                raise ValueError(
                    "evidence_ref must bind to one distinct catalog obligation"
                )
            seen_refs.add(binding.evidence_ref)
        _require_unique(
            (
                (
                    binding.evidence_ref,
                    binding.chain_id,
                    binding.category.value,
                    binding.obligation_id,
                )
                for binding in self.bindings
            ),
            label="evidence binding",
            owner=self.case_id,
        )
        return self


def serialize_depth_evidence_catalog(catalog: DepthEvidenceCatalog) -> str:
    """Serialize the complete typed catalog in digest-stable canonical order."""

    if not isinstance(catalog, DepthEvidenceCatalog):
        raise TypeError("canonical serialization requires typed DepthEvidenceCatalog")
    bindings = sorted(
        catalog.bindings,
        key=lambda binding: (
            binding.chain_id,
            binding.category.value,
            binding.obligation_id,
            binding.evidence_ref,
        ),
    )
    payload = {
        "case_id": catalog.case_id,
        "bindings": [
            {
                "evidence_ref": binding.evidence_ref,
                "chain_id": binding.chain_id,
                "category": binding.category.value,
                "obligation_id": binding.obligation_id,
            }
            for binding in bindings
        ],
    }
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def depth_evidence_catalog_sha256(catalog: DepthEvidenceCatalog) -> str:
    """Return the lowercase SHA-256 digest owned by the hidden truth package."""

    serialized = serialize_depth_evidence_catalog(catalog)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _Assessment:
    item_id: str
    chain_id: str
    category: Literal["node", "edge", "check"]
    closed: bool
    matched: bool
    has_evidence: bool
    validation_layer: ValidationLayer
    reason: str
    evidence_refs: tuple[str, ...]
    node_kind: DepthNodeKind | None = None
    source_kind: DepthNodeKind | None = None
    target_kind: DepthNodeKind | None = None


@dataclass(frozen=True)
class _L3Result:
    outcome: ValidationLayerOutcome
    misses: tuple[CriticalMiss, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class _InputIssue:
    item_id: str
    reason: str
    evidence_refs: tuple[str, ...] = ()


DepthTruthInput = DepthTruth | Mapping[str, Any]
DepthCandidateInput = DepthCandidate | Mapping[str, Any]
_BindingKey = tuple[str, EvidenceBindingCategory, str]
_BindingIndex = dict[_BindingKey, frozenset[str]]


def execute_depth_execution_oracles(
    plan: DepthExecutionPlan | Mapping[str, Any],
    catalog: DepthEvidenceCatalog,
    *,
    source_dir: str | Path,
    artifact_dir: str | Path,
    deadline_monotonic: float,
    command_allowlist: Mapping[str, DepthOracleCommandContract],
) -> DepthOracleExecutionRun:
    """Execute only evaluator-owned commands inside an OS-enforced source boundary."""

    validated = (
        plan
        if isinstance(plan, DepthExecutionPlan)
        else DepthExecutionPlan.model_validate(dict(plan))
    )
    if not isinstance(catalog, DepthEvidenceCatalog):
        raise TypeError("execution requires a typed DepthEvidenceCatalog")
    if validated.case_id != catalog.case_id:
        raise ValueError("execution plan case_id does not match evidence catalog")
    tier = validated.execution_tier.value
    base_audit: dict[str, Any] = {
        "schema_version": "quality-depth-execution-audit-v1",
        "case_id": validated.case_id,
        "execution_tier": tier,
        "policy": validated.policy,
        "network": "disabled",
        "runs": [],
    }
    if validated.execution_tier is ExecutionTier.STATIC:
        return DepthOracleExecutionRun(
            evidence=L3ExecutionEvidence(
                status=LayerStatus.NOT_APPLICABLE,
                chain_evidence=(),
                limitations=(),
            ),
            audit={
                **base_audit,
                "status": "not_applicable",
                "sandbox": {"status": "not_required", "engine": "none"},
            },
        )
    if validated.policy == "unavailable":
        return _not_run_execution(
            validated,
            base_audit,
            *validated.limitations,
        )

    source = Path(source_dir).resolve(strict=True)
    if not source.is_dir():
        raise ValueError("execution source boundary must be a directory")
    artifacts = Path(artifact_dir).resolve()
    if artifacts.exists():
        raise FileExistsError(f"immutable execution artifact already exists: {artifacts}")
    if _depth_paths_overlap(source, artifacts):
        raise ValueError("execution artifact directory must be outside the source boundary")

    catalog_refs = {
        (binding.chain_id, binding.evidence_ref)
        for binding in catalog.bindings
        if binding.category is EvidenceBindingCategory.L3
        and binding.obligation_id == "execution"
    }
    plan_refs = {(oracle.chain_id, oracle.evidence_ref) for oracle in validated.oracles}
    if plan_refs != catalog_refs:
        return _not_run_execution(
            validated,
            base_audit,
            "L3_NOT_RUN:EXECUTION_CATALOG_MISMATCH",
        )

    prepared: list[
        tuple[DepthExecutionOracle, DepthOracleCommandContract, Path, str]
    ] = []
    for oracle in validated.oracles:
        contract = command_allowlist.get(oracle.command_id)
        if contract is None:
            return _not_run_execution(
                validated,
                base_audit,
                "L3_NOT_RUN:COMMAND_NOT_ALLOWLISTED",
            )
        command_digest = depth_oracle_command_sha256(contract)
        if command_digest != oracle.command_sha256:
            return _not_run_execution(
                validated,
                base_audit,
                "L3_NOT_RUN:COMMAND_IDENTITY_MISMATCH",
            )
        try:
            fixture = _resolve_depth_fixture(source, oracle.fixture_path)
        except (OSError, ValueError):
            return _not_run_execution(
                validated,
                base_audit,
                "L3_NOT_RUN:FIXTURE_UNAVAILABLE",
            )
        fixture_digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
        if fixture_digest != oracle.fixture_sha256:
            return _not_run_execution(
                validated,
                base_audit,
                "L3_NOT_RUN:FIXTURE_IDENTITY_MISMATCH",
            )
        prepared.append((oracle, contract, fixture, fixture_digest))

    sandbox_engine = _depth_oracle_sandbox_engine()
    if sandbox_engine is None:
        return _not_run_execution(
            validated,
            base_audit,
            "L3_NOT_RUN:OS_ISOLATION_UNAVAILABLE",
        )
    if time.monotonic() >= float(deadline_monotonic):
        return _not_run_execution(
            validated,
            base_audit,
            "L3_NOT_RUN:DEADLINE_EXCEEDED",
        )

    artifacts.parent.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(mode=0o700)
    run_audits: list[dict[str, Any]] = []
    passed: list[L3ChainEvidence] = []
    terminal_limitation = ""
    for oracle, contract, fixture, fixture_digest in prepared:
        remaining = min(
            float(oracle.timeout_seconds),
            max(0.0, float(deadline_monotonic) - time.monotonic()),
        )
        if remaining <= 0:
            terminal_limitation = "L3_NOT_RUN:DEADLINE_EXCEEDED"
            break
        run_root = artifacts / oracle.oracle_id
        run_root.mkdir(mode=0o700)
        command = [
            str(fixture) if item == "{fixture}" else item for item in contract.argv
        ]
        wrapper, _sandbox_audit, cleanup = _depth_oracle_sandbox_wrapper(
            engine=sandbox_engine,
            source=source,
            artifact_dir=run_root,
            executable=Path(command[0]).resolve(strict=True),
        )
        status = "failed"
        exit_code: int | None = None
        result_digest = hashlib.sha256(b"").hexdigest()
        stderr_digest = hashlib.sha256(b"").hexdigest()
        try:
            process = subprocess.Popen(
                [*wrapper, *command],
                cwd=source,
                env=_depth_oracle_environment(run_root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            try:
                stdout, stderr = process.communicate(timeout=remaining)
            except subprocess.TimeoutExpired:
                _terminate_depth_oracle_process(process)
                stdout, stderr = process.communicate()
                result_digest = hashlib.sha256(stdout).hexdigest()
                stderr_digest = hashlib.sha256(stderr).hexdigest()
                terminal_limitation = "L3_NOT_RUN:DEADLINE_EXCEEDED"
                status = "timed_out"
            else:
                exit_code = process.returncode
                result_digest = hashlib.sha256(stdout).hexdigest()
                stderr_digest = hashlib.sha256(stderr).hexdigest()
                if len(stdout) > 1024 * 1024 or len(stderr) > 1024 * 1024:
                    status = "output_limit_exceeded"
                elif exit_code != 0:
                    status = "failed"
                elif result_digest != oracle.expected_result_sha256:
                    status = "result_identity_mismatch"
                else:
                    status = "passed"
                    passed.append(
                        L3ChainEvidence(
                            chain_id=oracle.chain_id,
                            evidence_refs=(oracle.evidence_ref,),
                        )
                    )
        except OSError:
            terminal_limitation = "L3_NOT_RUN:OS_ISOLATION_UNAVAILABLE"
            status = "sandbox_launch_failed"
        finally:
            cleanup()
        run_audits.append(
            {
                "oracle_id": oracle.oracle_id,
                "chain_id": oracle.chain_id,
                "status": status,
                "exit_code": exit_code,
                "command_sha256": oracle.command_sha256,
                "fixture_sha256": fixture_digest,
                "result_sha256": result_digest,
                "stderr_sha256": stderr_digest,
            }
        )
        if status != "passed":
            break

    audit = {
        **base_audit,
        "status": (
            "completed"
            if len(passed) == len(validated.oracles)
            else "not_run" if terminal_limitation else "failed"
        ),
        "sandbox": {"status": "active", "engine": sandbox_engine},
        "runs": run_audits,
    }
    if len(passed) == len(validated.oracles):
        evidence = L3ExecutionEvidence(
            status=LayerStatus.PASS,
            chain_evidence=tuple(passed),
            limitations=(),
        )
    elif terminal_limitation:
        evidence = L3ExecutionEvidence(
            status=LayerStatus.NOT_RUN,
            chain_evidence=(),
            limitations=_unique_strings(
                (f"L3_NOT_RUN:TIER_{tier}", terminal_limitation)
            ),
        )
    else:
        evidence = L3ExecutionEvidence(
            status=LayerStatus.FAIL,
            chain_evidence=tuple(passed),
            limitations=(),
        )
    _write_immutable_depth_execution_audit(artifacts, audit)
    return DepthOracleExecutionRun(evidence=evidence, audit=audit)


def _not_run_execution(
    plan: DepthExecutionPlan,
    base_audit: Mapping[str, Any],
    *limitations: str,
) -> DepthOracleExecutionRun:
    canonical = f"L3_NOT_RUN:TIER_{plan.execution_tier.value}"
    normalized = _unique_strings((canonical, *limitations))
    return DepthOracleExecutionRun(
        evidence=L3ExecutionEvidence(
            status=LayerStatus.NOT_RUN,
            chain_evidence=(),
            limitations=normalized,
        ),
        audit={
            **dict(base_audit),
            "status": "not_run",
            "sandbox": {"status": "not_started", "engine": "none"},
            "limitations": list(normalized),
        },
    )


def _resolve_depth_fixture(source: Path, relative: str) -> Path:
    current = source
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("execution fixture cannot traverse a symlink")
    resolved = current.resolve(strict=True)
    if not resolved.is_file() or not resolved.is_relative_to(source):
        raise ValueError("execution fixture escapes the source boundary")
    if resolved.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("execution fixture exceeds the immutable size limit")
    return resolved


def _depth_paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _depth_oracle_sandbox_engine() -> str | None:
    if sys.platform.startswith("darwin"):
        return "sandbox-exec" if shutil.which("sandbox-exec") else None
    if sys.platform.startswith("linux"):
        return "bubblewrap" if (shutil.which("bwrap") or shutil.which("bubblewrap")) else None
    return None


def _depth_oracle_sandbox_wrapper(
    *,
    engine: str,
    source: Path,
    artifact_dir: Path,
    executable: Path,
) -> tuple[list[str], dict[str, Any], Callable[[], None]]:
    if engine == "sandbox-exec":
        from app.services.agent_sandbox import _macos_profile

        sandbox_exec = shutil.which("sandbox-exec")
        if not sandbox_exec:
            raise OSError("sandbox-exec is unavailable")
        profile_fd, profile_name = tempfile.mkstemp(
            prefix="depth-oracle-", suffix=".sb", dir=artifact_dir
        )
        os.close(profile_fd)
        profile = Path(profile_name)
        profile.write_text(
            _macos_profile(
                read_paths=[source, executable],
                write_paths=[artifact_dir],
                allow_network=False,
            ),
            encoding="utf-8",
        )
        profile.chmod(0o600)

        def cleanup() -> None:
            profile.unlink(missing_ok=True)

        return (
            [sandbox_exec, "-f", str(profile)],
            {"status": "active", "engine": engine},
            cleanup,
        )
    if engine == "bubblewrap":
        bwrap = shutil.which("bwrap") or shutil.which("bubblewrap")
        if not bwrap:
            raise OSError("bubblewrap is unavailable")
        system_roots = [
            Path(value).resolve()
            for value in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc")
            if Path(value).exists()
        ]
        wrapper = [
            bwrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-net",
            "--tmpfs",
            "/",
        ]
        for root in system_roots:
            wrapper.extend(["--ro-bind", str(root), str(root)])
        if not any(executable == root or root in executable.parents for root in system_roots):
            wrapper.extend(["--ro-bind", str(executable), str(executable)])
        wrapper.extend(
            [
                "--ro-bind",
                str(source),
                str(source),
                "--bind",
                str(artifact_dir),
                str(artifact_dir),
                "--dev",
                "/dev",
                "--proc",
                "/proc",
                "--chdir",
                str(source),
            ]
        )
        return wrapper, {"status": "active", "engine": engine}, lambda: None
    raise OSError("unsupported depth execution sandbox")


def _depth_oracle_environment(artifact_dir: Path) -> dict[str, str]:
    return {
        "HOME": str(artifact_dir),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "TMPDIR": str(artifact_dir),
    }


def _terminate_depth_oracle_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        process.terminate()
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            process.kill()
        process.wait(timeout=0.5)


def _write_immutable_depth_execution_audit(
    artifact_dir: Path,
    audit: Mapping[str, Any],
) -> None:
    payload = (
        json.dumps(
            dict(audit),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    manifest = artifact_dir / "execution_audit.json"
    with manifest.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    for child in artifact_dir.rglob("*"):
        child.chmod(0o555 if child.is_dir() else 0o444)
    artifact_dir.chmod(0o555)


def parse_depth_truth(payload: DepthTruthInput) -> DepthTruth:
    """Adapt typed or JSON-shaped truth into the strict evaluator model."""

    if isinstance(payload, DepthTruth):
        return payload
    return DepthTruth.model_validate(dict(payload))


def parse_depth_candidate(payload: DepthCandidateInput) -> DepthCandidate:
    """Adapt typed or JSON-shaped candidate evidence into the strict model."""

    if isinstance(payload, DepthCandidate):
        return payload
    return DepthCandidate.model_validate(dict(payload))


def evaluate_depth(
    truth: DepthTruthInput,
    candidate: DepthCandidateInput,
    catalog: DepthEvidenceCatalog,
) -> AxisResult:
    """Evaluate with P3's typed catalog after hidden-truth hash verification."""

    if not isinstance(catalog, DepthEvidenceCatalog):
        raise TypeError(
            "evaluate_depth requires a typed evidence catalog (DepthEvidenceCatalog)"
        )
    validated_truth = parse_depth_truth(truth)
    if catalog.case_id != validated_truth.case_id:
        raise ValueError("catalog case_id does not match hidden truth case_id")
    catalog_digest = depth_evidence_catalog_sha256(catalog)
    if catalog_digest != validated_truth.evidence_catalog_sha256:
        raise ValueError("catalog digest does not match hidden truth digest")
    validated_candidate = parse_depth_candidate(candidate)
    binding_index = _binding_index(catalog)
    input_issues = _input_issues(
        validated_truth,
        validated_candidate,
        catalog,
    )
    candidate_chains = {
        chain.chain_id: chain for chain in validated_candidate.chains
    }

    assessments_by_chain: list[tuple[CriticalDepthChain, tuple[_Assessment, ...]]] = []
    for truth_chain in validated_truth.chains:
        assessments_by_chain.append(
            (
                truth_chain,
                _assess_chain(
                    truth_chain,
                    candidate_chains.get(truth_chain.chain_id),
                    binding_index,
                ),
            )
        )

    assessments = tuple(
        assessment
        for _, chain_assessments in assessments_by_chain
        for assessment in chain_assessments
    )
    static_misses = _obligation_misses(assessments)
    l0_misses = tuple(
        CriticalMiss(
            item_id=issue.item_id,
            reason=issue.reason,
            validation_layer=ValidationLayer.L0,
            evidence_refs=issue.evidence_refs,
        )
        for issue in input_issues
    )
    l3 = _evaluate_l3(validated_truth, validated_candidate.l3, binding_index)
    critical_misses = _unique_misses((*l0_misses, *static_misses, *l3.misses))
    limitations = _unique_strings(l3.limitations)

    static_numerator = sum(assessment.closed for assessment in assessments)
    static_denominator = len(assessments)
    execution_required = validated_truth.execution_tier is not ExecutionTier.STATIC
    numerator = static_numerator + (l3.outcome.numerator if execution_required else 0)
    denominator = static_denominator + (
        l3.outcome.denominator if execution_required else 0
    )

    status = AxisStatus.PASS
    if critical_misses:
        status = AxisStatus.FAIL
    elif limitations:
        status = AxisStatus.LIMITED

    l1 = _layer_one_outcome(validated_truth, assessments)
    l2 = _layer_two_outcome(validated_truth, assessments)
    evidence_refs = _unique_strings(
        ref
        for assessment in assessments
        for ref in assessment.evidence_refs
    )
    evidence_refs = _unique_strings((*evidence_refs, *l3.outcome.evidence_refs))

    return AxisResult(
        status=status,
        numerator=numerator,
        denominator=denominator,
        critical_misses=critical_misses,
        evidence_refs=evidence_refs,
        limitations=limitations,
        validation_layers=ValidationLayers(
            L0=_layer_zero_outcome(validated_truth, input_issues),
            L1=l1,
            L2=l2,
            L3=l3.outcome,
        ),
        metrics=_metrics(assessments_by_chain),
    )


def _binding_index(catalog: DepthEvidenceCatalog) -> _BindingIndex:
    mutable: dict[_BindingKey, set[str]] = {}
    for binding in catalog.bindings:
        key = (binding.chain_id, binding.category, binding.obligation_id)
        mutable.setdefault(key, set()).add(binding.evidence_ref)
    return {key: frozenset(refs) for key, refs in mutable.items()}


def _input_issues(
    truth: DepthTruth,
    candidate: DepthCandidate,
    catalog: DepthEvidenceCatalog,
) -> tuple[_InputIssue, ...]:
    issues: list[_InputIssue] = []
    truth_chains = {chain.chain_id: chain for chain in truth.chains}
    obligation_ids = {
        chain.chain_id: {
            EvidenceBindingCategory.NODE: {node.node_id for node in chain.nodes},
            EvidenceBindingCategory.EDGE: {edge.edge_id for edge in chain.edges},
            EvidenceBindingCategory.CHECK: {
                check.check_id for check in chain.disconfirming_checks
            },
            EvidenceBindingCategory.L3: {"execution"},
        }
        for chain in truth.chains
    }

    for binding in catalog.bindings:
        if binding.chain_id not in truth_chains:
            issues.append(
                _InputIssue(
                    item_id=f"catalog/chain:{binding.chain_id}",
                    reason="trusted catalog binding references an unknown chain",
                    evidence_refs=(binding.evidence_ref,),
                )
            )
            continue
        if binding.obligation_id not in obligation_ids[binding.chain_id][
            binding.category
        ]:
            issues.append(
                _InputIssue(
                    item_id=(
                        f"catalog/chain:{binding.chain_id}/"
                        f"{binding.category.value}:{binding.obligation_id}"
                    ),
                    reason="trusted catalog binding references an unknown obligation",
                    evidence_refs=(binding.evidence_ref,),
                )
            )

    for candidate_chain in candidate.chains:
        truth_chain = truth_chains.get(candidate_chain.chain_id)
        if truth_chain is None:
            issues.append(
                _InputIssue(
                    item_id=f"candidate/chain:{candidate_chain.chain_id}",
                    reason="candidate references an unknown critical chain",
                )
            )
            continue
        known_ids = {
            "node": {node.node_id for node in truth_chain.nodes},
            "edge": {edge.edge_id for edge in truth_chain.edges},
            "check": {
                check.check_id for check in truth_chain.disconfirming_checks
            },
        }
        observed_groups = (
            ("node", candidate_chain.nodes, "node_id"),
            ("edge", candidate_chain.edges, "edge_id"),
            ("check", candidate_chain.disconfirming_checks, "check_id"),
        )
        for category, observations, id_field in observed_groups:
            for observation in observations:
                obligation_id = getattr(observation, id_field)
                if obligation_id not in known_ids[category]:
                    issues.append(
                        _InputIssue(
                            item_id=(
                                f"candidate/chain:{candidate_chain.chain_id}/"
                                f"{category}:{obligation_id}"
                            ),
                            reason="candidate references an unknown obligation",
                            evidence_refs=observation.evidence_refs,
                        )
                    )

    if candidate.l3 is not None:
        for record in candidate.l3.chain_evidence:
            if record.chain_id not in truth_chains:
                issues.append(
                    _InputIssue(
                        item_id=f"candidate/l3:chain:{record.chain_id}",
                        reason="L3 evidence references an unknown critical chain",
                        evidence_refs=record.evidence_refs,
                    )
                )
        if (
            truth.execution_tier is ExecutionTier.STATIC
            and candidate.l3.status is not LayerStatus.NOT_APPLICABLE
        ):
            issues.append(
                _InputIssue(
                    item_id="candidate/l3:unexpected",
                    reason="static truth cannot claim executable L3 validation",
                )
            )

    by_id: dict[str, _InputIssue] = {}
    for issue in issues:
        by_id.setdefault(issue.item_id, issue)
    return tuple(by_id.values())


def _layer_zero_outcome(
    truth: DepthTruth,
    issues: tuple[_InputIssue, ...],
) -> ValidationLayerOutcome:
    return ValidationLayerOutcome(
        status=LayerStatus.FAIL if issues else LayerStatus.PASS,
        numerator=0 if issues else 1,
        denominator=len(issues) if issues else 1,
        critical_miss_ids=tuple(issue.item_id for issue in issues),
        evidence_refs=(f"truth://{truth.case_id}/critical_chains",),
        limitations=(),
    )


def _assess_chain(
    truth: CriticalDepthChain,
    candidate: CandidateDepthChain | None,
    bindings: _BindingIndex,
) -> tuple[_Assessment, ...]:
    observed_nodes = {node.node_id: node for node in candidate.nodes} if candidate else {}
    observed_edges = {edge.edge_id: edge for edge in candidate.edges} if candidate else {}
    observed_checks = (
        {check.check_id: check for check in candidate.disconfirming_checks}
        if candidate
        else {}
    )
    node_kinds = {node.node_id: node.kind for node in truth.nodes}
    assessments: list[_Assessment] = []

    for node in truth.nodes:
        observed = observed_nodes.get(node.node_id)
        assessments.append(
            _assess_observation(
                item_id=_item_id(truth.chain_id, "node", node.node_id),
                chain_id=truth.chain_id,
                category="node",
                observed=observed,
                trusted_refs=bindings.get(
                    (
                        truth.chain_id,
                        EvidenceBindingCategory.NODE,
                        node.node_id,
                    ),
                    frozenset(),
                ),
                passed=observed is not None
                and observed.status is ObligationStatus.CLOSED,
                missing_reason="required causal node is absent",
                open_reason="required causal node remains open",
                node_kind=node.kind,
            )
        )

    for edge in truth.edges:
        observed = observed_edges.get(edge.edge_id)
        assessments.append(
            _assess_observation(
                item_id=_item_id(truth.chain_id, "edge", edge.edge_id),
                chain_id=truth.chain_id,
                category="edge",
                observed=observed,
                trusted_refs=bindings.get(
                    (
                        truth.chain_id,
                        EvidenceBindingCategory.EDGE,
                        edge.edge_id,
                    ),
                    frozenset(),
                ),
                passed=observed is not None
                and observed.status is ObligationStatus.CLOSED,
                missing_reason="required causal edge is absent",
                open_reason="required causal edge remains open",
                source_kind=node_kinds[edge.source_node_id],
                target_kind=node_kinds[edge.target_node_id],
            )
        )

    for check in truth.disconfirming_checks:
        observed = observed_checks.get(check.check_id)
        assessments.append(
            _assess_observation(
                item_id=_item_id(truth.chain_id, "check", check.check_id),
                chain_id=truth.chain_id,
                category="check",
                observed=observed,
                trusted_refs=bindings.get(
                    (
                        truth.chain_id,
                        EvidenceBindingCategory.CHECK,
                        check.check_id,
                    ),
                    frozenset(),
                ),
                passed=observed is not None and observed.status is CheckStatus.PASS,
                missing_reason="required disconfirming check is absent",
                open_reason="required disconfirming check did not pass",
            )
        )
    return tuple(assessments)


def _assess_observation(
    *,
    item_id: str,
    chain_id: str,
    category: Literal["node", "edge", "check"],
    observed: ObservedDepthNode
    | ObservedDepthEdge
    | ObservedDisconfirmingCheck
    | None,
    trusted_refs: frozenset[str],
    passed: bool,
    missing_reason: str,
    open_reason: str,
    node_kind: DepthNodeKind | None = None,
    source_kind: DepthNodeKind | None = None,
    target_kind: DepthNodeKind | None = None,
) -> _Assessment:
    matched = observed is not None
    candidate_refs = observed.evidence_refs if observed is not None else ()
    evidence_refs = tuple(ref for ref in candidate_refs if ref in trusted_refs)
    candidate_ref_set = frozenset(candidate_refs)
    has_evidence = (
        bool(trusted_refs)
        and len(candidate_refs) == len(candidate_ref_set)
        and candidate_ref_set == trusted_refs
    )
    if not matched:
        closed = False
        layer = ValidationLayer.L2
        reason = missing_reason
    elif not passed:
        closed = False
        layer = ValidationLayer.L2
        reason = open_reason
    elif not has_evidence:
        closed = False
        layer = ValidationLayer.L1
        reason = f"{category} closure lacks trusted evidence bound to this obligation"
    else:
        closed = True
        layer = ValidationLayer.L2
        reason = ""
    return _Assessment(
        item_id=item_id,
        chain_id=chain_id,
        category=category,
        closed=closed,
        matched=matched,
        has_evidence=has_evidence,
        validation_layer=layer,
        reason=reason,
        evidence_refs=evidence_refs,
        node_kind=node_kind,
        source_kind=source_kind,
        target_kind=target_kind,
    )


def _obligation_misses(
    assessments: tuple[_Assessment, ...],
) -> tuple[CriticalMiss, ...]:
    return tuple(
        CriticalMiss(
            item_id=assessment.item_id,
            reason=assessment.reason,
            validation_layer=assessment.validation_layer,
            evidence_refs=assessment.evidence_refs,
        )
        for assessment in assessments
        if not assessment.closed
    )


def _evaluate_l3(
    truth: DepthTruth,
    evidence: L3ExecutionEvidence | None,
    bindings: _BindingIndex,
) -> _L3Result:
    if truth.execution_tier is ExecutionTier.STATIC:
        return _L3Result(
            outcome=ValidationLayerOutcome(
                status=LayerStatus.NOT_APPLICABLE,
                numerator=1,
                denominator=1,
                critical_miss_ids=(),
                evidence_refs=(),
                limitations=(),
            ),
            misses=(),
            limitations=(),
        )

    chain_ids = tuple(chain.chain_id for chain in truth.chains)
    tier_name = truth.execution_tier.value
    canonical_limitation = f"L3_NOT_RUN:TIER_{tier_name}"
    denominator = len(chain_ids)

    if evidence is None or evidence.status is LayerStatus.NOT_RUN:
        limitations = _unique_strings(
            (
                canonical_limitation,
                *(evidence.limitations if evidence is not None else ()),
            )
        )
        return _L3Result(
            outcome=ValidationLayerOutcome(
                status=LayerStatus.NOT_RUN,
                numerator=0,
                denominator=denominator,
                critical_miss_ids=(),
                evidence_refs=(),
                limitations=limitations,
            ),
            misses=(),
            limitations=limitations,
        )

    records = {record.chain_id: record for record in evidence.chain_evidence}
    accepted_by_chain: dict[str, tuple[str, ...]] = {}
    supported_ids: set[str] = set()
    for chain_id in chain_ids:
        record = records.get(chain_id)
        if record is None:
            continue
        trusted_refs = bindings.get(
            (chain_id, EvidenceBindingCategory.L3, "execution"),
            frozenset(),
        )
        accepted_refs = tuple(
            ref for ref in record.evidence_refs if ref in trusted_refs
        )
        accepted_by_chain[chain_id] = accepted_refs
        candidate_ref_set = frozenset(record.evidence_refs)
        if (
            bool(trusted_refs)
            and len(record.evidence_refs) == len(candidate_ref_set)
            and candidate_ref_set == trusted_refs
        ):
            supported_ids.add(chain_id)
    accepted_refs = _unique_strings(
        ref for refs in accepted_by_chain.values() for ref in refs
    )
    if evidence.status is LayerStatus.PASS:
        failed_ids = tuple(chain_id for chain_id in chain_ids if chain_id not in supported_ids)
        if not failed_ids:
            return _L3Result(
                outcome=ValidationLayerOutcome(
                    status=LayerStatus.PASS,
                    numerator=denominator,
                    denominator=denominator,
                    critical_miss_ids=(),
                    evidence_refs=accepted_refs,
                    limitations=(),
                ),
                misses=(),
                limitations=(),
            )
        reason = f"Tier {tier_name} pass lacks executable evidence"
        return _failed_l3(
            chain_ids=failed_ids,
            numerator=len(supported_ids),
            denominator=denominator,
            reason=reason,
            evidence_refs=accepted_refs,
        )

    if evidence.status is LayerStatus.FAIL:
        return _failed_l3(
            chain_ids=chain_ids,
            numerator=0,
            denominator=denominator,
            reason=f"Tier {tier_name} executable oracle failed",
            evidence_refs=accepted_refs,
        )

    return _failed_l3(
        chain_ids=chain_ids,
        numerator=0,
        denominator=denominator,
        reason=f"Tier {tier_name} execution cannot be marked not_applicable",
        evidence_refs=accepted_refs,
    )


def _failed_l3(
    *,
    chain_ids: tuple[str, ...],
    numerator: int,
    denominator: int,
    reason: str,
    evidence_refs: tuple[str, ...],
) -> _L3Result:
    miss_ids = tuple(_l3_item_id(chain_id) for chain_id in chain_ids)
    misses = tuple(
        CriticalMiss(
            item_id=item_id,
            reason=reason,
            validation_layer=ValidationLayer.L3,
            evidence_refs=evidence_refs,
        )
        for item_id in miss_ids
    )
    return _L3Result(
        outcome=ValidationLayerOutcome(
            status=LayerStatus.FAIL,
            numerator=numerator,
            denominator=denominator,
            critical_miss_ids=miss_ids,
            evidence_refs=evidence_refs,
            limitations=(),
        ),
        misses=misses,
        limitations=(),
    )


def _layer_one_outcome(
    truth: DepthTruth,
    assessments: tuple[_Assessment, ...],
) -> ValidationLayerOutcome:
    matched = tuple(assessment for assessment in assessments if assessment.matched)
    if not matched:
        return ValidationLayerOutcome(
            status=LayerStatus.NOT_APPLICABLE,
            numerator=1,
            denominator=1,
            critical_miss_ids=(),
            evidence_refs=(f"truth://{truth.case_id}/candidate-empty",),
            limitations=(),
        )
    unsupported = tuple(
        assessment
        for assessment in matched
        if not assessment.has_evidence
    )
    return ValidationLayerOutcome(
        status=LayerStatus.FAIL if unsupported else LayerStatus.PASS,
        numerator=sum(assessment.has_evidence for assessment in matched),
        denominator=len(matched),
        critical_miss_ids=tuple(assessment.item_id for assessment in unsupported),
        evidence_refs=_unique_strings(
            ref for assessment in matched for ref in assessment.evidence_refs
        ),
        limitations=(),
    )


def _layer_two_outcome(
    truth: DepthTruth,
    assessments: tuple[_Assessment, ...],
) -> ValidationLayerOutcome:
    failed = tuple(
        assessment for assessment in assessments if not assessment.closed
    )
    return ValidationLayerOutcome(
        status=LayerStatus.FAIL if failed else LayerStatus.PASS,
        numerator=sum(assessment.closed for assessment in assessments),
        denominator=len(assessments),
        critical_miss_ids=tuple(assessment.item_id for assessment in failed),
        evidence_refs=_unique_strings(
            (
                f"truth://{truth.case_id}/critical_chains",
                *(
                    ref
                    for assessment in assessments
                    if assessment.closed
                    for ref in assessment.evidence_refs
                ),
            )
        ),
        limitations=(),
    )


def _metrics(
    assessments_by_chain: list[
        tuple[CriticalDepthChain, tuple[_Assessment, ...]]
    ],
) -> tuple[RatioMetric, ...]:
    chain_closures: list[tuple[int, int, tuple[str, ...]]] = []
    for _, assessments in assessments_by_chain:
        chain_closures.append(
            (
                sum(assessment.closed for assessment in assessments),
                len(assessments),
                tuple(
                    assessment.item_id
                    for assessment in assessments
                    if not assessment.closed
                ),
            )
        )

    weakest = min(
        chain_closures,
        key=lambda closure: Fraction(closure[0], closure[1]),
    )
    average = sum(
        (
            Fraction(numerator, denominator)
            for numerator, denominator, _ in chain_closures
        ),
        start=Fraction(),
    ) / len(chain_closures)
    all_chain_misses = _unique_strings(
        miss_id for _, _, miss_ids in chain_closures for miss_id in miss_ids
    )

    state = _assessments_for_kinds(
        assessments_by_chain,
        {
            DepthNodeKind.STATE_MUTATION,
            DepthNodeKind.RESOURCE_MUTATION,
            DepthNodeKind.STATE_RESOURCE_MUTATION,
        },
    )
    resource = _assessments_for_kinds(
        assessments_by_chain,
        {
            DepthNodeKind.RESOURCE_ACQUISITION,
            DepthNodeKind.RESOURCE_OWNERSHIP,
            DepthNodeKind.RESOURCE_MUTATION,
            DepthNodeKind.RESOURCE_RELEASE,
            DepthNodeKind.STATE_RESOURCE_MUTATION,
        },
    )
    recovery = _assessments_for_kinds(
        assessments_by_chain,
        {
            DepthNodeKind.ERROR_PROPAGATION,
            DepthNodeKind.CLEANUP,
            DepthNodeKind.RECOVERY,
            DepthNodeKind.CLEANUP_RECOVERY,
        },
    )
    checks = tuple(
        assessment
        for _, assessments in assessments_by_chain
        for assessment in assessments
        if assessment.category == "check"
    )

    return (
        RatioMetric(
            name=MetricName.MINIMUM_CRITICAL_CHAIN_CLOSURE,
            numerator=weakest[0],
            denominator=weakest[1],
            miss_ids=weakest[2],
        ),
        RatioMetric(
            name=MetricName.AVERAGE_CHAIN_CLOSURE,
            numerator=average.numerator,
            denominator=average.denominator,
            miss_ids=all_chain_misses,
        ),
        _ratio_metric(MetricName.STATE_CLOSURE, state),
        _ratio_metric(MetricName.RESOURCE_LIFECYCLE_CLOSURE, resource),
        _ratio_metric(MetricName.ERROR_RECOVERY_CLOSURE, recovery),
        _ratio_metric(MetricName.DISCONFIRMING_CHECKS, checks),
    )


def _assessments_for_kinds(
    assessments_by_chain: list[
        tuple[CriticalDepthChain, tuple[_Assessment, ...]]
    ],
    kinds: set[DepthNodeKind],
) -> tuple[_Assessment, ...]:
    return tuple(
        assessment
        for _, assessments in assessments_by_chain
        for assessment in assessments
        if (
            assessment.category == "node" and assessment.node_kind in kinds
        )
        or (
            assessment.category == "edge"
            and (
                assessment.source_kind in kinds or assessment.target_kind in kinds
            )
        )
    )


def _ratio_metric(
    name: MetricName,
    assessments: tuple[_Assessment, ...],
) -> RatioMetric:
    if not assessments:
        raise ValueError(f"truth completeness invariant left {name.value} empty")
    return RatioMetric(
        name=name,
        numerator=sum(assessment.closed for assessment in assessments),
        denominator=len(assessments),
        miss_ids=tuple(
            assessment.item_id for assessment in assessments if not assessment.closed
        ),
    )


def _item_id(
    chain_id: str,
    category: Literal["node", "edge", "check"],
    item_id: str,
) -> str:
    return f"chain:{chain_id}/{category}:{item_id}"


def _l3_item_id(chain_id: str) -> str:
    return f"chain:{chain_id}/l3:execution"


def _require_unique(values: Any, *, label: str, owner: str) -> None:
    seen: set[Any] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"{owner} contains duplicate {label} id {value!r}")
        seen.add(value)


def _unique_strings(values: Any) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _unique_misses(values: Any) -> tuple[CriticalMiss, ...]:
    by_id: dict[str, CriticalMiss] = {}
    for value in values:
        by_id.setdefault(value.item_id, value)
    return tuple(by_id.values())
