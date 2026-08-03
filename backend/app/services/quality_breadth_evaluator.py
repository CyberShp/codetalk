"""Independent coverage-universe evaluation for the Breadth quality axis."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

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


class BreadthDimension(str, Enum):
    ENTRYPOINTS = "entrypoints"
    FLOWS = "flows"
    BRANCHES = "branches"
    STATES = "states"
    RESOURCES = "resources"
    BOUNDARIES = "boundaries"
    CONCURRENCY = "concurrency"
    ERRORS = "errors"
    PROTOCOL = "protocol"
    HISTORICAL = "historical"
    MUTATION = "mutation"


class Applicability(str, Enum):
    REQUIRED = "required"
    CONDITIONAL = "conditional"


CORE_DIMENSIONS = frozenset(
    {
        BreadthDimension.ENTRYPOINTS,
        BreadthDimension.FLOWS,
        BreadthDimension.BRANCHES,
        BreadthDimension.STATES,
        BreadthDimension.RESOURCES,
        BreadthDimension.BOUNDARIES,
        BreadthDimension.CONCURRENCY,
        BreadthDimension.ERRORS,
    }
)

_DIMENSION_ALIASES = {
    "entrypoint": BreadthDimension.ENTRYPOINTS,
    "flow": BreadthDimension.FLOWS,
    "branch": BreadthDimension.BRANCHES,
    "state": BreadthDimension.STATES,
    "resource": BreadthDimension.RESOURCES,
    "boundary": BreadthDimension.BOUNDARIES,
    "error": BreadthDimension.ERRORS,
    "error_recovery": BreadthDimension.ERRORS,
}

_REALIZED_STATUSES = frozenset(
    {
        "ready",
        "executable",
        "covered",
        "pass",
        "passed",
        "realized",
        "complete",
        "completed",
    }
)
_COVERED_DISPOSITIONS = frozenset(
    {"covered", "retain", "covered_by_other", "merge_into", "realized"}
)
_SCENARIO_ARTIFACT_KINDS = frozenset(
    {"", "test_scenarios", "black_box_cases", "black_box_test_cases"}
)


@dataclass(frozen=True)
class CoverageUniverseItem:
    item_id: str
    statement: str
    dimension: BreadthDimension
    critical: bool
    applicability: Applicability
    evidence_refs: tuple[str, ...]
    applicability_evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class BreadthDimensionResult:
    dimension: BreadthDimension
    denominator: int
    discovered: int
    realized: int
    disposed: int
    discovery_miss_ids: tuple[str, ...]
    realization_miss_ids: tuple[str, ...]
    disposition_miss_ids: tuple[str, ...]


@dataclass(frozen=True)
class BreadthEvaluationDetails:
    axis_result: AxisResult
    dimensions: tuple[BreadthDimensionResult, ...]
    invalid_disposition_ids: tuple[str, ...]


@dataclass(frozen=True)
class _ArtifactRow:
    payload: Mapping[str, Any]
    artifact_kind: str


@dataclass(frozen=True)
class _GeneratedItem:
    item_id: str
    identity_ids: frozenset[str]
    artifact_kind: str
    coverage_item_ids: frozenset[str]
    evidence_refs: frozenset[str]
    candidate_ids: frozenset[str]
    status: str


@dataclass(frozen=True)
class _Disposition:
    item_id: str
    coverage_item_ids: frozenset[str]
    evidence_refs: frozenset[str]
    disposition: str
    realization_refs: frozenset[str]


ArtifactInput = Mapping[str, Any] | Sequence[Mapping[str, Any]]


def evaluate_breadth(
    universe: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    scenario_candidates: ArtifactInput,
    scenarios: ArtifactInput,
    dispositions: ArtifactInput,
) -> AxisResult:
    """Evaluate generated artifacts against an independent coverage universe."""

    return evaluate_breadth_details(
        universe,
        scenario_candidates=scenario_candidates,
        scenarios=scenarios,
        dispositions=dispositions,
    ).axis_result


def evaluate_breadth_details(
    universe: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    scenario_candidates: ArtifactInput,
    scenarios: ArtifactInput,
    dispositions: ArtifactInput,
) -> BreadthEvaluationDetails:
    """Return the contract result plus exact misses grouped by dimension."""

    truth_items = _parse_universe(universe)
    by_id = {item.item_id: item for item in truth_items}
    evidence_to_ids = _evidence_index(truth_items)

    candidate_rows = tuple(
        _parse_generated_item(
            row,
            id_fields=("candidate_id", "id"),
            alias_fields=(),
            default_status="ready",
        )
        for row in _artifact_rows(scenario_candidates)
    )
    candidate_matches = {
        row.item_id: _matched_truth_ids(row, by_id, evidence_to_ids)
        for row in candidate_rows
    }
    candidate_coverage = {
        candidate_id: matches
        for candidate_id, matches in candidate_matches.items()
        if candidate_id
    }

    scenario_rows = tuple(
        _parse_generated_item(
            row,
            id_fields=("scenario_id", "case_id", "id"),
            alias_fields=("scenario_id", "case_id", "case_ids"),
            default_status="unknown",
        )
        for row in _artifact_rows(scenarios)
    )
    realized_ids: set[str] = set()
    scenario_outcomes: dict[str, tuple[bool, frozenset[str]]] = {}
    for row in scenario_rows:
        potential_matches = set(_matched_truth_ids(row, by_id, evidence_to_ids))
        for candidate_id in row.candidate_ids:
            potential_matches.update(candidate_coverage.get(candidate_id, ()))
        verified_matches = frozenset(
            item_id
            for item_id in potential_matches
            if frozenset(by_id[item_id].evidence_refs).issubset(row.evidence_refs)
        )
        is_realized = (
            row.artifact_kind in _SCENARIO_ARTIFACT_KINDS
            and row.status in _REALIZED_STATUSES
        )
        outcome = (is_realized, verified_matches)
        for identity_id in row.identity_ids:
            if identity_id in scenario_outcomes:
                raise ValueError(
                    f"duplicate or ambiguous scenario alias: {identity_id}"
                )
            scenario_outcomes[identity_id] = outcome
        if is_realized:
            realized_ids.update(verified_matches)

    disposition_rows = tuple(
        _parse_disposition(row) for row in _artifact_rows(dispositions)
    )
    disposition_matches: list[tuple[_Disposition, frozenset[str]]] = []
    for row in disposition_rows:
        matches = set(row.coverage_item_ids & by_id.keys())
        if not matches:
            matches.update(
                evidence_id
                for ref in row.evidence_refs
                for evidence_id in evidence_to_ids.get(ref, ())
            )
        disposition_matches.append((row, frozenset(matches)))

    valid_exclusions: set[str] = set()
    valid_covered_dispositions: set[str] = set()
    invalid_dispositions: set[str] = set()
    for row, matches in disposition_matches:
        for item_id in matches:
            item = by_id[item_id]
            if row.disposition == "not_applicable":
                if _supported_exclusion(item, row):
                    valid_exclusions.add(item_id)
                else:
                    invalid_dispositions.add(item_id)
            elif row.disposition in _COVERED_DISPOSITIONS:
                if _supported_covered_disposition(
                    item,
                    row,
                    scenario_outcomes=scenario_outcomes,
                ):
                    valid_covered_dispositions.add(item_id)
                else:
                    invalid_dispositions.add(item_id)

    all_ids = set(by_id)
    active_ids = all_ids - valid_exclusions
    discovered_ids = (
        set().union(*candidate_matches.values()) if candidate_matches else set()
    )
    discovered_ids.update(realized_ids)
    # A coverage disposition can attest to a realized scenario but cannot
    # independently turn discovery evidence into scenario realization.
    closed_ids = realized_ids | valid_exclusions

    discovery_misses = _ordered_ids(truth_items, all_ids - discovered_ids)
    active_realization_misses = _ordered_ids(truth_items, active_ids - realized_ids)
    disposition_misses = _ordered_ids(truth_items, all_ids - closed_ids)
    critical_ids = {item.item_id for item in truth_items if item.critical}
    critical_miss_ids = _ordered_ids(truth_items, critical_ids - closed_ids)

    zero_dimensions = {
        dimension
        for dimension in BreadthDimension
        if (dimension_ids := _ids_for_dimension(truth_items, dimension) & active_ids)
        and not (dimension_ids & realized_ids)
    }
    l1_failed = bool(invalid_dispositions)
    l2_failed = bool(critical_miss_ids or zero_dimensions)
    status = AxisStatus.FAIL if l1_failed or l2_failed else AxisStatus.PASS

    critical_misses = tuple(
        CriticalMiss(
            item_id=item_id,
            reason=(
                f"critical {by_id[item_id].dimension.value} obligation was not "
                "realized or validly disposed"
            ),
            validation_layer=ValidationLayer.L2,
            evidence_refs=by_id[item_id].evidence_refs,
        )
        for item_id in critical_miss_ids
    )
    axis_result = AxisResult(
        status=status,
        numerator=len(closed_ids),
        denominator=len(truth_items),
        critical_misses=critical_misses,
        evidence_refs=_ordered_unique(
            ref for item in truth_items for ref in item.evidence_refs
        ),
        limitations=(),
        validation_layers=ValidationLayers(
            L0=_layer(LayerStatus.PASS, 1, 1),
            L1=_layer(
                LayerStatus.FAIL if l1_failed else LayerStatus.PASS,
                len(truth_items) - len(invalid_dispositions),
                len(truth_items),
            ),
            L2=_layer(
                LayerStatus.FAIL if l2_failed else LayerStatus.PASS,
                len(closed_ids),
                len(truth_items),
                critical_miss_ids=critical_miss_ids if l2_failed else (),
                evidence_refs=_ordered_unique(
                    ref for item in truth_items for ref in item.evidence_refs
                ),
            ),
            L3=_layer(LayerStatus.NOT_APPLICABLE, 0, 1),
        ),
        metrics=(
            RatioMetric(
                name=MetricName.DISCOVERY_RECALL,
                numerator=len(all_ids & discovered_ids),
                denominator=len(truth_items),
                miss_ids=discovery_misses,
            ),
            RatioMetric(
                name=MetricName.CRITICAL_COVERAGE,
                numerator=len(critical_ids & closed_ids),
                denominator=len(critical_ids),
                miss_ids=critical_miss_ids,
            ),
            RatioMetric(
                name=MetricName.SCENARIO_REALIZATION,
                numerator=len(active_ids & realized_ids),
                denominator=len(active_ids),
                miss_ids=active_realization_misses,
            ),
            RatioMetric(
                name=MetricName.DISPOSITION_COMPLETENESS,
                numerator=len(all_ids & closed_ids),
                denominator=len(truth_items),
                miss_ids=disposition_misses,
            ),
        ),
    )

    dimension_results = tuple(
        _dimension_result(
            dimension,
            truth_items=truth_items,
            active_ids=active_ids,
            discovered_ids=discovered_ids,
            realized_ids=realized_ids,
            valid_disposition_ids=valid_exclusions | valid_covered_dispositions,
            closed_ids=closed_ids,
        )
        for dimension in BreadthDimension
        if _ids_for_dimension(truth_items, dimension)
    )
    return BreadthEvaluationDetails(
        axis_result=axis_result,
        dimensions=dimension_results,
        invalid_disposition_ids=_ordered_ids(truth_items, invalid_dispositions),
    )


def _parse_universe(
    universe: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> tuple[CoverageUniverseItem, ...]:
    rows = _artifact_rows(universe)
    items: list[CoverageUniverseItem] = []
    seen: set[str] = set()
    for artifact_row in rows:
        row = artifact_row.payload
        item_id = _required_string(row, "item_id", "id")
        statement = _required_string(row, "statement")
        if item_id in seen:
            raise ValueError(f"duplicate coverage universe item_id: {item_id}")
        seen.add(item_id)
        raw_dimension = _required_string(row, "dimension").lower()
        try:
            dimension = _DIMENSION_ALIASES.get(raw_dimension)
            if dimension is None:
                dimension = BreadthDimension(raw_dimension)
        except ValueError as exc:
            raise ValueError(f"unsupported breadth dimension: {raw_dimension}") from exc
        critical = row.get("critical")
        if not isinstance(critical, bool):
            raise TypeError(
                f"coverage universe item {item_id} requires boolean critical"
            )
        raw_applicability = str(row.get("applicability") or "required").strip().lower()
        try:
            applicability = Applicability(raw_applicability)
        except ValueError as exc:
            raise ValueError(
                f"unsupported applicability for {item_id}: {raw_applicability}"
            ) from exc
        evidence_refs = _strings(row.get("evidence_refs"))
        if not evidence_refs:
            raise ValueError(f"coverage universe item {item_id} requires evidence_refs")
        applicability_evidence_refs = _strings(row.get("applicability_evidence_refs"))
        if (
            applicability is Applicability.CONDITIONAL
            and not applicability_evidence_refs
        ):
            raise ValueError(
                f"conditional coverage universe item {item_id} requires applicability_evidence_refs"
            )
        items.append(
            CoverageUniverseItem(
                item_id=item_id,
                statement=statement,
                dimension=dimension,
                critical=critical,
                applicability=applicability,
                evidence_refs=evidence_refs,
                applicability_evidence_refs=applicability_evidence_refs,
            )
        )
    if not items:
        raise ValueError("coverage universe must contain at least one item")
    missing_core = sorted(
        dimension.value
        for dimension in CORE_DIMENSIONS - {item.dimension for item in items}
    )
    if missing_core:
        raise ValueError(
            "coverage universe is missing required dimensions: "
            + ", ".join(missing_core)
        )
    required_core = {
        item.dimension for item in items if item.applicability is Applicability.REQUIRED
    }
    excludable_core = sorted(
        dimension.value for dimension in CORE_DIMENSIONS - required_core
    )
    if excludable_core:
        raise ValueError(
            "coverage universe core dimensions require at least one required item: "
            + ", ".join(excludable_core)
        )
    if not any(item.critical for item in items):
        raise ValueError("coverage universe must identify at least one critical item")
    return tuple(items)


def _artifact_rows(
    value: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    inherited_kind: str = "",
) -> tuple[_ArtifactRow, ...]:
    if isinstance(value, Mapping):
        declared_kind = str(value.get("kind") or "").strip().lower()
        if inherited_kind and declared_kind and declared_kind != inherited_kind:
            raise ValueError(
                "conflicting artifact kind: "
                f"parent={inherited_kind}, child={declared_kind}"
            )
        artifact_kind = inherited_kind or declared_kind
        nested = value.get("items")
        if nested is None:
            return (_ArtifactRow(payload=value, artifact_kind=artifact_kind),)
        return _artifact_rows(nested, inherited_kind=artifact_kind)
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError("breadth artifact must be an object or an array of objects")
    if not all(isinstance(row, Mapping) for row in value):
        raise ValueError("breadth artifact rows must be objects")
    rows: list[_ArtifactRow] = []
    for row in value:
        rows.extend(_artifact_rows(row, inherited_kind=inherited_kind))
    return tuple(rows)


def _parse_generated_item(
    artifact_row: _ArtifactRow,
    *,
    id_fields: tuple[str, ...],
    alias_fields: tuple[str, ...],
    default_status: str,
) -> _GeneratedItem:
    row = artifact_row.payload
    item_id = _required_string(row, *id_fields)
    aliases = {item_id}
    for field in alias_fields:
        aliases.update(_strings(row.get(field)))
    return _GeneratedItem(
        item_id=item_id,
        identity_ids=frozenset(aliases),
        artifact_kind=artifact_row.artifact_kind,
        coverage_item_ids=frozenset(
            _strings(
                row.get("coverage_item_ids")
                or row.get("universe_item_ids")
                or row.get("obligation_ids")
                or row.get("coverage_target_ids")
            )
        ),
        evidence_refs=frozenset(
            _strings(
                [
                    *_strings(row.get("evidence_refs")),
                    *_strings(row.get("source_or_test_evidence")),
                ]
            )
        ),
        candidate_ids=frozenset(_strings(row.get("candidate_ids"))),
        status=str(row.get("status") or default_status).strip().lower(),
    )


def _parse_disposition(artifact_row: _ArtifactRow) -> _Disposition:
    row = artifact_row.payload
    direct_id = _required_string(
        row,
        "item_id",
        "coverage_item_id",
        "universe_item_id",
        "id",
        "resource_id",
        "flow_id",
    )
    return _Disposition(
        item_id=direct_id,
        coverage_item_ids=frozenset(
            {
                direct_id,
                *_strings(
                    row.get("coverage_item_ids")
                    or row.get("universe_item_ids")
                    or row.get("obligation_ids")
                ),
            }
        ),
        evidence_refs=frozenset(_strings(row.get("evidence_refs"))),
        disposition=str(row.get("disposition") or row.get("status") or "")
        .strip()
        .lower(),
        realization_refs=frozenset(
            _strings(
                row.get("scenario_ids")
                or row.get("case_ids")
                or row.get("test_case_ids")
                or row.get("covered_by")
            )
        ),
    )


def _matched_truth_ids(
    row: _GeneratedItem,
    by_id: Mapping[str, CoverageUniverseItem],
    evidence_to_ids: Mapping[str, frozenset[str]],
) -> frozenset[str]:
    matches = set(row.coverage_item_ids & by_id.keys())
    matches.update(
        item_id for ref in row.evidence_refs for item_id in evidence_to_ids.get(ref, ())
    )
    return frozenset(matches)


def _supported_exclusion(item: CoverageUniverseItem, disposition: _Disposition) -> bool:
    return (
        item.applicability is Applicability.CONDITIONAL
        and bool(disposition.evidence_refs)
        and bool(
            disposition.evidence_refs & frozenset(item.applicability_evidence_refs)
        )
    )


def _supported_covered_disposition(
    item: CoverageUniverseItem,
    disposition: _Disposition,
    *,
    scenario_outcomes: Mapping[
        str,
        tuple[bool, frozenset[str]],
    ],
) -> bool:
    if not disposition.evidence_refs & frozenset(item.evidence_refs):
        return False
    if not disposition.realization_refs:
        return False
    for realization_ref in disposition.realization_refs:
        outcome = scenario_outcomes.get(realization_ref)
        if outcome is None:
            return False
        is_realized, matched_ids = outcome
        if not is_realized or item.item_id not in matched_ids:
            return False
    return True


def _dimension_result(
    dimension: BreadthDimension,
    *,
    truth_items: tuple[CoverageUniverseItem, ...],
    active_ids: set[str],
    discovered_ids: set[str],
    realized_ids: set[str],
    valid_disposition_ids: set[str],
    closed_ids: set[str],
) -> BreadthDimensionResult:
    all_dimension_ids = _ids_for_dimension(truth_items, dimension)
    active_dimension_ids = all_dimension_ids & active_ids
    return BreadthDimensionResult(
        dimension=dimension,
        denominator=len(active_dimension_ids),
        discovered=len(all_dimension_ids & discovered_ids),
        realized=len(active_dimension_ids & realized_ids),
        disposed=len(all_dimension_ids & valid_disposition_ids),
        discovery_miss_ids=_ordered_ids(
            truth_items,
            all_dimension_ids - discovered_ids,
        ),
        realization_miss_ids=_ordered_ids(
            truth_items,
            active_dimension_ids - realized_ids,
        ),
        disposition_miss_ids=_ordered_ids(
            truth_items,
            all_dimension_ids - closed_ids,
        ),
    )


def _layer(
    status: LayerStatus,
    numerator: int,
    denominator: int,
    *,
    critical_miss_ids: tuple[str, ...] = (),
    evidence_refs: tuple[str, ...] = (),
) -> ValidationLayerOutcome:
    return ValidationLayerOutcome(
        status=status,
        numerator=numerator,
        denominator=denominator,
        critical_miss_ids=critical_miss_ids,
        evidence_refs=evidence_refs,
        limitations=(),
    )


def _evidence_index(
    items: Iterable[CoverageUniverseItem],
) -> dict[str, frozenset[str]]:
    index: dict[str, set[str]] = {}
    for item in items:
        for ref in item.evidence_refs:
            index.setdefault(ref, set()).add(item.item_id)
    return {ref: frozenset(item_ids) for ref, item_ids in index.items()}


def _ids_for_dimension(
    items: Iterable[CoverageUniverseItem],
    dimension: BreadthDimension,
) -> set[str]:
    return {item.item_id for item in items if item.dimension is dimension}


def _ordered_ids(
    items: Iterable[CoverageUniverseItem],
    selected: set[str],
) -> tuple[str, ...]:
    return tuple(item.item_id for item in items if item.item_id in selected)


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    values = value if isinstance(value, (list, tuple, set, frozenset)) else (value,)
    return tuple(dict.fromkeys(text for raw in values if (text := str(raw).strip())))


def _required_string(row: Mapping[str, Any], *fields: str) -> str:
    for field in fields:
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(f"breadth artifact row requires one of: {', '.join(fields)}")
