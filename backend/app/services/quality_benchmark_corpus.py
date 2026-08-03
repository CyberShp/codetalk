"""Fail-closed corpus identity and truth-isolation contracts for F012 benchmarks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import unquote

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from app.services.quality_benchmark_semantic_judge import (
    materialize_semantic_evidence_ref,
)
from app.services.quality_depth_evaluator import (
    DepthEvidenceCatalog,
    DepthExecutionPlan,
    DepthTruth,
    EvidenceBindingCategory,
    depth_evidence_catalog_sha256,
)


REGISTRY_SCHEMA_VERSION = "quality-benchmark-registry-v1"
CASE_SCHEMA_VERSION = "quality-benchmark-case-v1"
CORPUS_ROOT_ENV = "CODETALK_QUALITY_CORPUS_ROOT"
GENERATOR_SURFACES = frozenset(
    {
        "task_input",
        "prompt_capture",
        "retrieval_index",
        "bundle",
        "generator_manifest",
    }
)
PROJECT_DOMAIN_TAGS = {
    "spdk": frozenset({"storage"}),
    "femu": frozenset({"storage"}),
    "nvme-csd": frozenset({"storage"}),
    "open-cas-linux": frozenset({"storage"}),
    "phosphor-nvme": frozenset({"bmc"}),
    "phosphor-state-manager": frozenset({"bmc"}),
    "bmcweb": frozenset({"bmc"}),
    "lmcache": frozenset({"kv-cache", "kvcache"}),
    "mooncake": frozenset({"kv-cache", "kvcache"}),
    "rdma-core": frozenset({"rdma", "rdma-roce"}),
    "ucx": frozenset({"rdma", "roce", "rdma-roce"}),
    "perftest": frozenset({"rdma", "roce", "rdma-roce"}),
}

# Baseline strata stay stable for four-domain calibration; selector tags above
# distinguish generic RDMA from cases that explicitly exercise RoCE behavior.
BASELINE_PROJECT_STRATA = {
    "spdk": "storage",
    "femu": "storage",
    "nvme-csd": "storage",
    "open-cas-linux": "storage",
    "phosphor-nvme": "bmc",
    "phosphor-state-manager": "bmc",
    "bmcweb": "bmc",
    "lmcache": "kv-cache",
    "mooncake": "kv-cache",
    "rdma-core": "rdma-roce",
    "ucx": "rdma-roce",
    "perftest": "rdma-roce",
}

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ProjectId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*$")]
SafeComponent = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
]
CommitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
CanonicalOrigin = Annotated[
    str,
    StringConstraints(
        pattern=r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git$"
    ),
]
TruthPackageVersion = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]*$")]
TruthFile = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*\.json$")
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Tier = Literal["S", "E", "H"]
MAX_PERCENT_DECODE_ROUNDS = 4
MALFORMED_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


class QualityCorpusError(ValueError):
    """Raised when registry, case, or repository identity validation fails."""


class TruthIsolationError(QualityCorpusError):
    """Raised when truth paths could enter a generator-visible surface."""


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class DepthTierDisposition(ContractModel):
    tier: Literal["E", "H"]
    status: Literal["unavailable"]
    requirements: tuple[NonEmptyString, ...] = Field(strict=False, min_length=1)
    limitation: NonEmptyString

    @model_validator(mode="after")
    def require_unique_requirements(self) -> "DepthTierDisposition":
        if len(self.requirements) != len(set(self.requirements)):
            raise ValueError("depth tier disposition requirements must be unique")
        return self


class RegistryTestExecution(ContractModel):
    policy: Literal["case_allowlist_only"]
    loader_execution: Literal["forbidden"]
    network: Literal["disabled"]


class QualityBenchmarkProject(ContractModel):
    id: ProjectId
    source_dir: SafeComponent
    origin: CanonicalOrigin
    commit: CommitSha
    expected_tree: CommitSha
    license: NonEmptyString
    tiers: list[Tier]
    test_execution: RegistryTestExecution

    @field_validator("tiers")
    @classmethod
    def require_unique_tiers(cls, tiers: list[Tier]) -> list[Tier]:
        if not tiers:
            raise ValueError("tiers must not be empty")
        if len(tiers) != len(set(tiers)):
            raise ValueError("tiers must be unique")
        return tiers


class QualityBenchmarkRegistry(ContractModel):
    schema_version: Literal[REGISTRY_SCHEMA_VERSION]
    truth_package_version: TruthPackageVersion
    projects: list[QualityBenchmarkProject]

    @model_validator(mode="after")
    def require_unique_projects(self) -> "QualityBenchmarkRegistry":
        if not self.projects:
            raise ValueError("projects must not be empty")
        ids = [project.id for project in self.projects]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate project id")
        source_dirs = [project.source_dir for project in self.projects]
        if len(source_dirs) != len(set(source_dirs)):
            raise ValueError("duplicate project source_dir")
        return self


class TruthFileDescriptor(ContractModel):
    path: TruthFile
    sha256: Sha256


class TruthPackagePaths(ContractModel):
    gold_claims: TruthFileDescriptor
    coverage_universe: TruthFileDescriptor
    critical_chains: TruthFileDescriptor
    execution_oracles: TruthFileDescriptor

    @model_validator(mode="after")
    def require_unique_truth_files(self) -> "TruthPackagePaths":
        descriptors = (
            self.gold_claims,
            self.coverage_universe,
            self.critical_chains,
            self.execution_oracles,
        )
        paths = [descriptor.path for descriptor in descriptors]
        identities = [
            (descriptor.path, descriptor.sha256) for descriptor in descriptors
        ]
        if len(paths) != len(set(paths)) or len(identities) != len(set(identities)):
            raise ValueError("duplicate truth descriptor")
        return self


class CaseTestExecution(ContractModel):
    policy: Literal["disabled", "allowlisted"]
    commands: list[NonEmptyString]

    @model_validator(mode="after")
    def validate_commands(self) -> "CaseTestExecution":
        if self.policy == "disabled" and self.commands:
            raise ValueError("disabled test execution cannot declare commands")
        if self.policy == "allowlisted" and not self.commands:
            raise ValueError("allowlisted test execution requires commands")
        return self


class QualityBenchmarkCase(ContractModel):
    schema_version: Literal[CASE_SCHEMA_VERSION]
    case_id: SafeComponent
    project_id: ProjectId
    truth_package_version: TruthPackageVersion
    tier: Tier
    truth_package: TruthPackagePaths
    test_execution: CaseTestExecution


class ResolvedQualityProject(ContractModel):
    id: ProjectId
    path: Path
    origin: CanonicalOrigin
    commit: CommitSha
    expected_tree: CommitSha


@dataclass(frozen=True)
class QualityBaselineCaseIdentity:
    """Hash-bound identity for one authoritative Tier-S calibration case."""

    case_id: str
    project_id: str
    domain: str
    tier: str
    source_revision: str
    truth_package_version: str
    case_sha256: str
    truth_sha256: tuple[tuple[str, str, str], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "project_id": self.project_id,
            "domain": self.domain,
            "tier": self.tier,
            "source_revision": self.source_revision,
            "truth_package_version": self.truth_package_version,
            "case_sha256": self.case_sha256,
            "truth_sha256": {
                name: {"path": path, "sha256": digest}
                for name, path, digest in self.truth_sha256
            },
        }


@dataclass(frozen=True)
class QualityBaselineCorpusIdentity:
    """Runtime-authoritative registry and case snapshot for baseline freezing."""

    registry_path: Path
    registry_sha256: str
    corpus_sha256: str
    truth_package_version: str
    cases: tuple[QualityBaselineCaseIdentity, ...]

    @property
    def case_map(self) -> dict[str, QualityBaselineCaseIdentity]:
        return {case.case_id: case for case in self.cases}

    def as_dict(self) -> dict[str, Any]:
        return {
            "registry_sha256": self.registry_sha256,
            "corpus_sha256": self.corpus_sha256,
            "truth_package_version": self.truth_package_version,
            "cases": {
                case.case_id: case.as_dict()
                for case in sorted(self.cases, key=lambda item: item.case_id)
            },
        }


def load_quality_registry(path: str | Path) -> QualityBenchmarkRegistry:
    """Load the runtime-authoritative registry without touching project sources."""

    payload = _read_json_object(path, label="quality registry")
    try:
        return QualityBenchmarkRegistry.model_validate(payload)
    except ValidationError as exc:
        raise QualityCorpusError(f"invalid quality registry: {exc}") from exc


def load_quality_case(
    path: str | Path,
    *,
    registry: QualityBenchmarkRegistry,
    source_dir: str | Path | None = None,
) -> QualityBenchmarkCase:
    """Load a case declaration and bind it to an accepted registry version."""

    source = Path(path)
    payload = _read_json_object(source, label="quality benchmark case")
    try:
        case = QualityBenchmarkCase.model_validate(payload)
    except ValidationError as exc:
        raise QualityCorpusError(f"invalid quality benchmark case: {exc}") from exc

    projects = {project.id: project for project in registry.projects}
    project = projects.get(case.project_id)
    if project is None:
        raise QualityCorpusError(f"unknown project_id: {case.project_id}")
    if case.truth_package_version != registry.truth_package_version:
        raise QualityCorpusError(
            "truth_package_version does not match the registry truth package version"
        )
    if case.tier not in project.tiers:
        raise QualityCorpusError(
            f"tier {case.tier} is not declared for project_id {case.project_id}"
        )
    _validate_truth_package_files(source, case.truth_package)
    _validate_depth_truth_package(
        source,
        case=case,
        project=project,
        source_dir=source_dir,
    )
    return case


def load_quality_baseline_corpus(
    registry_path: str | Path,
) -> QualityBaselineCorpusIdentity:
    """Derive the complete calibration corpus from the formal registry and cases."""

    source = Path(registry_path)
    if source.is_symlink():
        raise QualityCorpusError(
            f"quality registry must be a regular non-symlink file: {source}"
        )
    try:
        resolved_registry = source.resolve(strict=True)
    except OSError as exc:
        raise QualityCorpusError(f"quality registry is unavailable: {source}") from exc
    if not resolved_registry.is_file():
        raise QualityCorpusError(
            f"quality registry must be a regular non-symlink file: {resolved_registry}"
        )
    registry = load_quality_registry(resolved_registry)
    project_map = {project.id: project for project in registry.projects}
    if set(project_map) != set(BASELINE_PROJECT_STRATA):
        missing = sorted(set(BASELINE_PROJECT_STRATA) - set(project_map))
        extra = sorted(set(project_map) - set(BASELINE_PROJECT_STRATA))
        raise QualityCorpusError(
            "baseline domain authority does not match the formal registry "
            f"(missing={missing}, extra={extra})"
        )

    case_paths = sorted(
        resolved_registry.parent.glob("projects/*/*/case.json")
    )
    identities: list[QualityBaselineCaseIdentity] = []
    seen_projects: set[str] = set()
    seen_cases: set[str] = set()
    for case_path in case_paths:
        case = load_quality_case(case_path, registry=registry)
        if case.tier != "S":
            continue
        if case.project_id in seen_projects:
            raise QualityCorpusError(
                f"baseline requires exactly one Tier-S case for {case.project_id}"
            )
        if case.case_id in seen_cases:
            raise QualityCorpusError(f"duplicate baseline case_id: {case.case_id}")
        project = project_map[case.project_id]
        truth_descriptors = (
            ("gold_claims", case.truth_package.gold_claims),
            ("coverage_universe", case.truth_package.coverage_universe),
            ("critical_chains", case.truth_package.critical_chains),
            ("execution_oracles", case.truth_package.execution_oracles),
        )
        identities.append(
            QualityBaselineCaseIdentity(
                case_id=case.case_id,
                project_id=case.project_id,
                domain=BASELINE_PROJECT_STRATA[case.project_id],
                tier=case.tier,
                source_revision=project.commit,
                truth_package_version=case.truth_package_version,
                case_sha256=hashlib.sha256(case_path.read_bytes()).hexdigest(),
                truth_sha256=tuple(
                    (name, descriptor.path, descriptor.sha256)
                    for name, descriptor in truth_descriptors
                ),
            )
        )
        seen_projects.add(case.project_id)
        seen_cases.add(case.case_id)

    if seen_projects != set(project_map):
        missing = sorted(set(project_map) - seen_projects)
        raise QualityCorpusError(
            f"formal registry projects without exactly one Tier-S case: {missing}"
        )
    if len(identities) != 12:
        raise QualityCorpusError(
            f"baseline requires exactly 12 registry-derived cases, got {len(identities)}"
        )

    registry_sha256 = hashlib.sha256(resolved_registry.read_bytes()).hexdigest()
    canonical = {
        "registry_sha256": registry_sha256,
        "truth_package_version": registry.truth_package_version,
        "cases": {
            item.case_id: item.as_dict()
            for item in sorted(identities, key=lambda value: value.case_id)
        },
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return QualityBaselineCorpusIdentity(
        registry_path=resolved_registry,
        registry_sha256=registry_sha256,
        corpus_sha256=hashlib.sha256(encoded).hexdigest(),
        truth_package_version=registry.truth_package_version,
        cases=tuple(sorted(identities, key=lambda value: value.case_id)),
    )


def resolve_quality_project(
    project_id: str,
    *,
    registry: QualityBenchmarkRegistry,
    corpus_root: str | Path | None = None,
) -> ResolvedQualityProject:
    """Resolve and verify a pinned local repository using read-only git metadata."""

    projects = {project.id: project for project in registry.projects}
    project = projects.get(project_id)
    if project is None:
        raise QualityCorpusError(f"unknown project id: {project_id}")

    configured_root = corpus_root
    if configured_root is None:
        configured_root = os.environ.get(CORPUS_ROOT_ENV)
        if not configured_root:
            raise QualityCorpusError(f"{CORPUS_ROOT_ENV} is required")

    root = Path(configured_root).expanduser()
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        raise QualityCorpusError(f"corpus root is unavailable: {root}") from exc
    if not resolved_root.is_dir():
        raise QualityCorpusError(f"corpus root is not a directory: {resolved_root}")

    candidate = resolved_root / project.source_dir
    try:
        resolved_project = candidate.resolve(strict=True)
    except OSError as exc:
        raise QualityCorpusError(f"project source is unavailable: {candidate}") from exc
    try:
        resolved_project.relative_to(resolved_root)
    except ValueError as exc:
        raise QualityCorpusError(
            f"project source path escapes corpus root: {project.source_dir}"
        ) from exc
    if not resolved_project.is_dir():
        raise QualityCorpusError(f"project source is not a git repository: {resolved_project}")
    git_directory = resolved_project / ".git"
    if not git_directory.is_dir():
        raise QualityCorpusError(
            f"project source requires an in-tree .git directory: {resolved_project}"
        )
    try:
        resolved_git_directory = git_directory.resolve(strict=True)
        resolved_git_directory.relative_to(resolved_project)
        resolved_git_directory.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise QualityCorpusError(
            f"git directory escapes project or corpus root: {git_directory}"
        ) from exc

    actual_commit = _read_git_metadata(resolved_project, "rev-parse", "HEAD")
    actual_tree = _read_git_metadata(resolved_project, "rev-parse", "HEAD^{tree}")
    actual_origin = _read_git_metadata(
        resolved_project, "config", "--get", "remote.origin.url"
    )
    if actual_commit != project.commit:
        raise QualityCorpusError(
            f"commit mismatch for {project.id}: expected {project.commit}, got {actual_commit}"
        )
    if actual_tree != project.expected_tree:
        raise QualityCorpusError(
            "expected_tree mismatch for "
            f"{project.id}: expected {project.expected_tree}, got {actual_tree}"
        )
    if actual_origin != project.origin:
        raise QualityCorpusError(
            f"origin mismatch for {project.id}: expected {project.origin}, got {actual_origin}"
        )

    return ResolvedQualityProject(
        id=project.id,
        path=resolved_project,
        origin=project.origin,
        commit=project.commit,
        expected_tree=project.expected_tree,
    )


def resolve_registered_corpus(
    registry: QualityBenchmarkRegistry,
    *,
    corpus_root: str | Path | None = None,
) -> tuple[ResolvedQualityProject, ...]:
    """Verify every registry entry without cloning, checkout, or project execution."""

    return tuple(
        resolve_quality_project(
            project.id, registry=registry, corpus_root=corpus_root
        )
        for project in registry.projects
    )


def validate_truth_isolation(
    *,
    generator_surfaces: Mapping[str, Any],
    truth_paths: Iterable[str | Path],
) -> None:
    """Reject truth path references in every generator-visible data surface."""

    surface_names = set(generator_surfaces)
    missing = GENERATOR_SURFACES - surface_names
    unknown = surface_names - GENERATOR_SURFACES
    if missing:
        raise TruthIsolationError(
            f"missing generator surface: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise TruthIsolationError(
            f"unknown generator surface: {', '.join(sorted(unknown))}"
        )

    paths = [Path(path) for path in truth_paths]
    if not paths:
        raise TruthIsolationError("truth_paths must not be empty")
    tokens = {
        token
        for path in paths
        for token in (
            _normalise_for_comparison(str(path))[0],
            _normalise_for_comparison(path.name)[0],
        )
        if token
    }
    if not tokens:
        raise TruthIsolationError("truth_paths must contain named files")
    suspicious_tokens = {
        Path(token).name.removesuffix(".json")
        for token in tokens
        if Path(token).name.removesuffix(".json")
    }

    for surface_name in sorted(GENERATOR_SURFACES):
        for value in _iter_surface_strings(generator_surfaces[surface_name]):
            normalised, malformed = _normalise_for_comparison(value)
            if re.search(r"%[0-9A-Fa-f]{2}", normalised):
                raise TruthIsolationError(
                    f"residual percent encoding in {surface_name}"
                )
            if malformed and any(
                token in normalised for token in suspicious_tokens
            ):
                raise TruthIsolationError(
                    f"malformed percent encoding near truth token in {surface_name}"
                )
            for token in tokens:
                if token in normalised:
                    raise TruthIsolationError(
                        f"truth path {token!r} leaked into {surface_name}"
                    )


def quality_registry_json_schema() -> dict[str, Any]:
    """Return the Draft 2020-12 registry schema for authoring and CI diagnostics."""

    schema = QualityBenchmarkRegistry.model_json_schema(mode="validation")
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema}


def quality_case_json_schema() -> dict[str, Any]:
    """Return the Draft 2020-12 case schema for authoring and CI diagnostics."""

    schema = QualityBenchmarkCase.model_json_schema(mode="validation")
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema}


def _read_json_object(path: str | Path, *, label: str) -> dict[str, Any]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QualityCorpusError(f"cannot read {label} {source}: {exc}") from exc
    if not isinstance(payload, dict):
        raise QualityCorpusError(f"{label} must be a JSON object")
    return payload


def _validate_truth_package_files(
    case_path: Path, truth_package: TruthPackagePaths
) -> None:
    try:
        resolved_case = case_path.resolve(strict=True)
    except OSError as exc:
        raise QualityCorpusError(f"quality benchmark case is unavailable: {case_path}") from exc
    if not resolved_case.is_file():
        raise QualityCorpusError(
            f"quality benchmark case is not a regular file: {resolved_case}"
        )
    case_root = resolved_case.parent
    descriptors = {
        "gold_claims": truth_package.gold_claims,
        "coverage_universe": truth_package.coverage_universe,
        "critical_chains": truth_package.critical_chains,
        "execution_oracles": truth_package.execution_oracles,
    }
    for name, descriptor in descriptors.items():
        candidate = case_root / descriptor.path
        try:
            resolved_truth = candidate.resolve(strict=True)
        except OSError as exc:
            raise QualityCorpusError(
                f"missing truth file {name}: {candidate}"
            ) from exc
        try:
            resolved_truth.relative_to(case_root)
        except ValueError as exc:
            raise QualityCorpusError(
                f"truth path escape for {name}: {descriptor.path}"
            ) from exc
        if not resolved_truth.is_file():
            raise QualityCorpusError(
                f"truth file must be regular for {name}: {resolved_truth}"
            )
        try:
            content = resolved_truth.read_bytes()
        except OSError as exc:
            raise QualityCorpusError(
                f"cannot read truth file {name}: {resolved_truth}"
            ) from exc
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != descriptor.sha256:
            raise QualityCorpusError(
                f"sha256 mismatch for truth file {name}: "
                f"expected {descriptor.sha256}, got {actual_sha256}"
            )
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QualityCorpusError(
                f"truth file {name} must contain a JSON object or list"
            ) from exc
        if not isinstance(payload, (dict, list)):
            raise QualityCorpusError(
                f"truth file {name} must contain a JSON object or list"
            )


def _validate_depth_truth_package(
    case_path: Path,
    *,
    case: QualityBenchmarkCase,
    project: QualityBenchmarkProject,
    source_dir: str | Path | None,
) -> None:
    case_root = case_path.resolve(strict=True).parent
    critical_path = case_root / case.truth_package.critical_chains.path
    execution_path = case_root / case.truth_package.execution_oracles.path
    critical_payload = _read_json_object(
        critical_path,
        label="critical_chains truth",
    )
    execution_payload = _read_json_object(
        execution_path,
        label="execution_oracles truth",
    )
    try:
        truth = DepthTruth.model_validate(critical_payload)
        catalog_payload = execution_payload.get("evidence_catalog")
        if catalog_payload is None and {
            "case_id",
            "bindings",
        }.issubset(execution_payload):
            catalog_payload = {
                "case_id": execution_payload["case_id"],
                "bindings": execution_payload["bindings"],
            }
        if not isinstance(catalog_payload, Mapping):
            raise ValueError("execution_oracles requires evidence_catalog")
        catalog = DepthEvidenceCatalog.model_validate(catalog_payload)
        plan_payload = execution_payload.get("execution_plan")
        if not isinstance(plan_payload, Mapping):
            raise ValueError("execution_oracles requires execution_plan")
        plan = DepthExecutionPlan.model_validate(plan_payload)
        raw_dispositions = execution_payload.get("tier_dispositions")
        if not isinstance(raw_dispositions, list):
            raise ValueError("execution_oracles requires tier_dispositions")
        dispositions = tuple(
            DepthTierDisposition.model_validate(item) for item in raw_dispositions
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise QualityCorpusError(f"invalid depth truth package: {exc}") from exc

    identities = {truth.case_id, catalog.case_id, plan.case_id, case.case_id}
    if len(identities) != 1:
        raise QualityCorpusError("depth truth package case identity mismatch")
    if truth.execution_tier.value != case.tier or plan.execution_tier.value != case.tier:
        raise QualityCorpusError("depth truth execution tier does not match case tier")
    if depth_evidence_catalog_sha256(catalog) != truth.evidence_catalog_sha256:
        raise QualityCorpusError("depth evidence catalog digest mismatch")

    expected_disposition_tiers = set(project.tiers) - {case.tier}
    actual_disposition_tiers = {item.tier for item in dispositions}
    if len(dispositions) != len(actual_disposition_tiers):
        raise QualityCorpusError("duplicate depth tier disposition")
    if actual_disposition_tiers != expected_disposition_tiers:
        raise QualityCorpusError(
            "depth tier dispositions do not cover every declared non-case tier"
        )

    expected_obligations = {
        (
            chain.chain_id,
            EvidenceBindingCategory.NODE,
            node.node_id,
        )
        for chain in truth.chains
        for node in chain.nodes
    }
    expected_obligations.update(
        (
            chain.chain_id,
            EvidenceBindingCategory.EDGE,
            edge.edge_id,
        )
        for chain in truth.chains
        for edge in chain.edges
    )
    expected_obligations.update(
        (
            chain.chain_id,
            EvidenceBindingCategory.CHECK,
            check.check_id,
        )
        for chain in truth.chains
        for check in chain.disconfirming_checks
    )
    expected_obligations.update(
        (chain.chain_id, EvidenceBindingCategory.L3, "execution")
        for chain in truth.chains
    )
    actual_obligations = {
        (binding.chain_id, binding.category, binding.obligation_id)
        for binding in catalog.bindings
    }
    if actual_obligations != expected_obligations:
        raise QualityCorpusError(
            "depth evidence catalog must bind every and only declared obligation"
        )

    if source_dir is None:
        return
    source = Path(source_dir).resolve(strict=True)
    if not source.is_dir():
        raise QualityCorpusError("depth source boundary must be a directory")
    for binding in catalog.bindings:
        if binding.category is EvidenceBindingCategory.L3:
            continue
        try:
            materialize_semantic_evidence_ref(binding.evidence_ref, source)
        except (OSError, UnicodeError, ValueError) as exc:
            raise QualityCorpusError(
                "depth evidence range cannot be materialized: "
                f"{binding.evidence_ref}"
            ) from exc


def _read_git_metadata(project: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(project), *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise QualityCorpusError(
            f"cannot verify git metadata for {project}: {' '.join(arguments)}"
        ) from exc
    value = result.stdout.strip()
    if not value:
        raise QualityCorpusError(
            f"empty git metadata for {project}: {' '.join(arguments)}"
        )
    return value


def _iter_surface_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, Path):
        yield str(value)
        return
    if isinstance(value, Mapping):
        for key, nested in value.items():
            yield from _iter_surface_strings(key)
            yield from _iter_surface_strings(nested)
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for nested in value:
            yield from _iter_surface_strings(nested)
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    raise TruthIsolationError(
        f"unsupported generator surface value type: {type(value).__name__}"
    )


def _normalise_for_comparison(value: str) -> tuple[str, bool]:
    normalised = _normalise_unicode_path(value)
    malformed = bool(MALFORMED_PERCENT_ESCAPE.search(normalised))
    for _ in range(MAX_PERCENT_DECODE_ROUNDS):
        try:
            decoded = unquote(normalised, errors="strict")
        except UnicodeDecodeError:
            malformed = True
            break
        decoded = _normalise_unicode_path(decoded)
        malformed = malformed or bool(MALFORMED_PERCENT_ESCAPE.search(decoded))
        if decoded == normalised:
            break
        normalised = decoded
    else:
        if re.search(r"%[0-9A-Fa-f]{2}", normalised):
            malformed = True
    return normalised, malformed


def _normalise_unicode_path(value: str) -> str:
    return unicodedata.normalize("NFKC", value).replace("\\", "/").casefold()
