from __future__ import annotations

from copy import deepcopy

import pytest
from app.services.quality_breadth_evaluator import (
    BreadthDimension,
    evaluate_breadth,
    evaluate_breadth_details,
)
from app.services.quality_evaluation_contract import (
    AxisStatus,
    LayerStatus,
    MetricName,
)

CORE_ITEMS = (
    ("ENTRY-001", "entrypoints", False),
    ("FLOW-001", "flows", False),
    ("BRANCH-001", "branches", False),
    ("STATE-001", "states", False),
    ("RESOURCE-CLEANUP-001", "resources", True),
    ("BOUNDARY-001", "boundaries", False),
    ("CONCURRENCY-001", "concurrency", True),
    ("ERROR-RECOVERY-001", "errors", False),
)


def _universe(
    *, extra_items: list[dict[str, object]] | None = None
) -> dict[str, object]:
    items = [
        {
            "item_id": item_id,
            "dimension": dimension,
            "critical": critical,
            "applicability": "required",
            "statement": f"The source behavior covers {dimension} for {item_id}.",
            "evidence_refs": [f"truth://{item_id}"],
            "applicability_evidence_refs": [],
        }
        for item_id, dimension, critical in CORE_ITEMS
    ]
    items.extend(
        {
            "statement": (
                f"The source behavior covers {item['dimension']} for {item['item_id']}."
            ),
            **item,
        }
        for item in (extra_items or [])
    )
    return {
        "schema_version": "quality-breadth-universe-v1",
        "items": items,
    }


def _generated_for(
    universe: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    candidates: list[dict[str, object]] = []
    scenarios: list[dict[str, object]] = []
    for raw in universe["items"]:  # type: ignore[index]
        item = raw  # type: ignore[assignment]
        item_id = item["item_id"]
        candidate_id = f"CAND-{item_id}"
        candidates.append(
            {
                "candidate_id": candidate_id,
                "coverage_item_ids": [item_id],
                "evidence_refs": item["evidence_refs"],
            }
        )
        scenarios.append(
            {
                "scenario_id": f"SCN-{item_id}",
                "candidate_ids": [candidate_id],
                "coverage_item_ids": [item_id],
                "status": "READY",
                "evidence_refs": item["evidence_refs"],
            }
        )
    return {"kind": "scenario_candidates", "items": candidates}, {
        "kind": "test_scenarios",
        "items": scenarios,
    }


def _metric(result: object, name: MetricName):
    return next(
        metric
        for metric in result.metrics  # type: ignore[attr-defined]
        if metric.name is name
    )


def _without_item(artifact: dict[str, object], item_id: str) -> dict[str, object]:
    result = deepcopy(artifact)
    result["items"] = [
        row
        for row in result["items"]  # type: ignore[index]
        if item_id not in row.get("coverage_item_ids", [])  # type: ignore[union-attr]
    ]
    return result


def test_complete_independent_universe_returns_contract_axis_result() -> None:
    universe = _universe()
    candidates, scenarios = _generated_for(universe)

    result = evaluate_breadth(
        universe,
        scenario_candidates=candidates,
        scenarios=scenarios,
        dispositions=[],
    )

    assert result.status is AxisStatus.PASS
    assert (result.numerator, result.denominator) == (8, 8)
    assert result.critical_misses == ()
    assert result.validation_layers.l3.status is LayerStatus.NOT_APPLICABLE
    assert {
        metric.name: (metric.numerator, metric.denominator, metric.miss_ids)
        for metric in result.metrics
    } == {
        MetricName.DISCOVERY_RECALL: (8, 8, ()),
        MetricName.CRITICAL_COVERAGE: (2, 2, ()),
        MetricName.SCENARIO_REALIZATION: (8, 8, ()),
        MetricName.DISPOSITION_COMPLETENESS: (8, 8, ()),
    }


def test_coverage_universe_requires_and_retains_semantic_statement() -> None:
    from app.services.quality_breadth_evaluator import _parse_universe

    universe = _universe()
    universe["items"][0].pop("statement")  # type: ignore[index, union-attr]

    with pytest.raises(ValueError, match="statement"):
        _parse_universe(universe)

    valid = _universe()
    parsed = _parse_universe(valid)
    assert parsed[0].statement == valid["items"][0]["statement"]  # type: ignore[index]


def test_generated_candidates_cannot_define_or_expand_the_denominator() -> None:
    universe = _universe(
        extra_items=[
            {
                "item_id": "BRANCH-002",
                "dimension": "branches",
                "critical": False,
                "applicability": "required",
                "evidence_refs": ["truth://BRANCH-002"],
                "applicability_evidence_refs": [],
            }
        ]
    )
    candidates, scenarios = _generated_for(universe)
    candidates = _without_item(candidates, "BRANCH-002")
    scenarios = _without_item(scenarios, "BRANCH-002")
    candidates["items"].append(  # type: ignore[union-attr]
        {
            "candidate_id": "CAND-GENERATED-ONLY",
            "coverage_item_ids": ["GENERATED-ONLY"],
            "evidence_refs": ["generated://only"],
        }
    )

    result = evaluate_breadth(
        universe,
        scenario_candidates=candidates,
        scenarios=scenarios,
        dispositions=[],
    )

    discovery = _metric(result, MetricName.DISCOVERY_RECALL)
    assert (discovery.numerator, discovery.denominator) == (8, 9)
    assert discovery.miss_ids == ("BRANCH-002",)
    assert "GENERATED-ONLY" not in discovery.miss_ids


def test_happy_path_missing_critical_cleanup_is_a_gating_miss() -> None:
    universe = _universe()
    candidates, scenarios = _generated_for(universe)

    result = evaluate_breadth(
        universe,
        scenario_candidates=_without_item(candidates, "RESOURCE-CLEANUP-001"),
        scenarios=_without_item(scenarios, "RESOURCE-CLEANUP-001"),
        dispositions=[],
    )

    assert result.status is AxisStatus.FAIL
    assert [miss.item_id for miss in result.critical_misses] == ["RESOURCE-CLEANUP-001"]
    critical = _metric(result, MetricName.CRITICAL_COVERAGE)
    assert (critical.numerator, critical.denominator) == (1, 2)
    assert critical.miss_ids == ("RESOURCE-CLEANUP-001",)


def test_discovered_item_without_a_scenario_is_reported_as_unrealized() -> None:
    universe = _universe()
    candidates, scenarios = _generated_for(universe)

    result = evaluate_breadth(
        universe,
        scenario_candidates=candidates,
        scenarios=_without_item(scenarios, "STATE-001"),
        dispositions=[],
    )

    assert _metric(result, MetricName.DISCOVERY_RECALL).miss_ids == ()
    realization = _metric(result, MetricName.SCENARIO_REALIZATION)
    assert (realization.numerator, realization.denominator) == (7, 8)
    assert realization.miss_ids == ("STATE-001",)


@pytest.mark.parametrize(
    ("item_id", "applicability", "known_evidence", "provided_evidence"),
    [
        (
            "BOUNDARY-CONDITIONAL-001",
            "conditional",
            ["truth://BOUNDARY-NA"],
            [],
        ),
        (
            "BOUNDARY-CONDITIONAL-001",
            "conditional",
            ["truth://BOUNDARY-NA"],
            ["claim://untrusted"],
        ),
        (
            "BOUNDARY-001",
            "required",
            [],
            ["truth://BOUNDARY-001"],
        ),
    ],
)
def test_unsupported_not_applicable_disposition_fails_closed(
    item_id: str,
    applicability: str,
    known_evidence: list[str],
    provided_evidence: list[str],
) -> None:
    universe = _universe(
        extra_items=[
            {
                "item_id": "BOUNDARY-CONDITIONAL-001",
                "dimension": "boundaries",
                "critical": False,
                "applicability": "conditional",
                "evidence_refs": ["truth://BOUNDARY-CONDITIONAL-001"],
                "applicability_evidence_refs": ["truth://BOUNDARY-NA"],
            }
        ]
    )
    item = next(
        row
        for row in universe["items"]  # type: ignore[index]
        if row["item_id"] == item_id
    )
    item["applicability"] = applicability
    item["applicability_evidence_refs"] = known_evidence
    candidates, scenarios = _generated_for(universe)

    result = evaluate_breadth(
        universe,
        scenario_candidates=_without_item(candidates, item_id),
        scenarios=_without_item(scenarios, item_id),
        dispositions=[
            {
                "item_id": item_id,
                "disposition": "not_applicable",
                "evidence_refs": provided_evidence,
            }
        ],
    )

    assert result.status is AxisStatus.FAIL
    completeness = _metric(result, MetricName.DISPOSITION_COMPLETENESS)
    assert completeness.miss_ids == (item_id,)


def test_supported_conditional_exclusion_is_complete_and_not_a_scenario_obligation() -> (
    None
):
    universe = _universe(
        extra_items=[
            {
                "item_id": "BOUNDARY-CONDITIONAL-001",
                "dimension": "boundaries",
                "critical": False,
                "applicability": "conditional",
                "evidence_refs": ["truth://BOUNDARY-CONDITIONAL-001"],
                "applicability_evidence_refs": ["truth://BOUNDARY-NA"],
            }
        ]
    )
    candidates, scenarios = _generated_for(universe)

    result = evaluate_breadth(
        universe,
        scenario_candidates=_without_item(candidates, "BOUNDARY-CONDITIONAL-001"),
        scenarios=_without_item(scenarios, "BOUNDARY-CONDITIONAL-001"),
        dispositions=[
            {
                "item_id": "BOUNDARY-CONDITIONAL-001",
                "disposition": "not_applicable",
                "evidence_refs": ["truth://BOUNDARY-NA"],
            }
        ],
    )

    assert result.status is AxisStatus.PASS
    assert (result.numerator, result.denominator) == (9, 9)
    realization = _metric(result, MetricName.SCENARIO_REALIZATION)
    assert (realization.numerator, realization.denominator) == (8, 8)
    assert _metric(result, MetricName.DISPOSITION_COMPLETENESS).miss_ids == ()


def test_conditional_exclusion_requires_every_applicability_evidence_ref() -> None:
    universe = _universe(
        extra_items=[
            {
                "item_id": "BOUNDARY-CONDITIONAL-MULTI",
                "dimension": "boundaries",
                "critical": True,
                "applicability": "conditional",
                "evidence_refs": ["truth://BOUNDARY-CONDITIONAL"],
                "applicability_evidence_refs": [
                    "truth://PLATFORM",
                    "truth://FEATURE-FLAG",
                ],
            }
        ]
    )
    candidates, scenarios = _generated_for(universe)

    result = evaluate_breadth(
        universe,
        scenario_candidates=_without_item(
            candidates, "BOUNDARY-CONDITIONAL-MULTI"
        ),
        scenarios=_without_item(scenarios, "BOUNDARY-CONDITIONAL-MULTI"),
        dispositions=[
            {
                "item_id": "BOUNDARY-CONDITIONAL-MULTI",
                "disposition": "not_applicable",
                "evidence_refs": ["truth://PLATFORM"],
            }
        ],
    )

    assert result.status is AxisStatus.FAIL
    assert "BOUNDARY-CONDITIONAL-MULTI" in {
        miss.item_id for miss in result.critical_misses
    }


def test_duplicate_scenarios_do_not_inflate_coverage() -> None:
    universe = _universe()
    candidates, scenarios = _generated_for(universe)
    duplicate = deepcopy(
        next(
            row
            for row in scenarios["items"]  # type: ignore[index]
            if "FLOW-001" in row["coverage_item_ids"]
        )
    )
    duplicate["scenario_id"] = "SCN-FLOW-001-DUPLICATE"
    second_duplicate = deepcopy(duplicate)
    second_duplicate["scenario_id"] = "SCN-FLOW-001-DUPLICATE-2"
    scenarios["items"].extend([duplicate, second_duplicate])  # type: ignore[union-attr]

    result = evaluate_breadth(
        universe,
        scenario_candidates=candidates,
        scenarios=scenarios,
        dispositions=[],
    )

    realization = _metric(result, MetricName.SCENARIO_REALIZATION)
    assert (realization.numerator, realization.denominator) == (8, 8)
    assert result.numerator == 8


def test_ninety_five_percent_with_one_critical_miss_fails() -> None:
    extras = [
        {
            "item_id": f"FLOW-{index:03d}",
            "dimension": "flows",
            "critical": index == 12,
            "applicability": "required",
            "evidence_refs": [f"truth://FLOW-{index:03d}"],
            "applicability_evidence_refs": [],
        }
        for index in range(2, 14)
    ]
    universe = _universe(extra_items=extras)
    candidates, scenarios = _generated_for(universe)

    result = evaluate_breadth(
        universe,
        scenario_candidates=_without_item(candidates, "FLOW-012"),
        scenarios=_without_item(scenarios, "FLOW-012"),
        dispositions=[],
    )

    assert (result.numerator, result.denominator) == (19, 20)
    assert result.status is AxisStatus.FAIL
    assert [miss.item_id for miss in result.critical_misses] == ["FLOW-012"]


def test_zero_coverage_dimension_cannot_be_compensated_by_other_dimensions() -> None:
    extras = [
        {
            "item_id": f"FLOW-{index:03d}",
            "dimension": "flows",
            "critical": False,
            "applicability": "required",
            "evidence_refs": [f"truth://FLOW-{index:03d}"],
            "applicability_evidence_refs": [],
        }
        for index in range(2, 14)
    ]
    universe = _universe(extra_items=extras)
    candidates, scenarios = _generated_for(universe)

    details = evaluate_breadth_details(
        universe,
        scenario_candidates=_without_item(candidates, "BOUNDARY-001"),
        scenarios=_without_item(scenarios, "BOUNDARY-001"),
        dispositions=[],
    )

    assert (details.axis_result.numerator, details.axis_result.denominator) == (19, 20)
    assert details.axis_result.critical_misses == ()
    assert details.axis_result.status is AxisStatus.FAIL
    boundary = next(
        result
        for result in details.dimensions
        if result.dimension is BreadthDimension.BOUNDARIES
    )
    assert boundary.realized == 0
    assert boundary.realization_miss_ids == ("BOUNDARY-001",)


def test_dimension_diagnostics_preserve_exact_miss_ids() -> None:
    universe = _universe(
        extra_items=[
            {
                "item_id": "BRANCH-002",
                "dimension": "branches",
                "critical": False,
                "applicability": "required",
                "evidence_refs": ["truth://BRANCH-002"],
                "applicability_evidence_refs": [],
            }
        ]
    )
    candidates, scenarios = _generated_for(universe)

    details = evaluate_breadth_details(
        universe,
        scenario_candidates=_without_item(candidates, "BRANCH-002"),
        scenarios=_without_item(scenarios, "BRANCH-002"),
        dispositions=[],
    )

    branches = next(
        result
        for result in details.dimensions
        if result.dimension is BreadthDimension.BRANCHES
    )
    assert branches.denominator == 2
    assert branches.discovery_miss_ids == ("BRANCH-002",)
    assert branches.realization_miss_ids == ("BRANCH-002",)
    assert branches.disposition_miss_ids == ("BRANCH-002",)


def test_optional_protocol_historical_and_mutation_dimensions_are_evaluated_when_present() -> (
    None
):
    optional = [
        {
            "item_id": f"{dimension.upper()}-001",
            "dimension": dimension,
            "critical": False,
            "applicability": "required",
            "evidence_refs": [f"truth://{dimension}"],
            "applicability_evidence_refs": [],
        }
        for dimension in ("protocol", "historical", "mutation")
    ]
    universe = _universe(extra_items=optional)
    candidates, scenarios = _generated_for(universe)

    details = evaluate_breadth_details(
        universe,
        scenario_candidates=candidates,
        scenarios=scenarios,
        dispositions=[],
    )

    assert details.axis_result.status is AxisStatus.PASS
    assert {result.dimension.value for result in details.dimensions} >= {
        "protocol",
        "historical",
        "mutation",
    }
    assert details.axis_result.denominator == 11


def test_applicable_protocol_obligation_enters_denominator_and_critical_gate() -> None:
    protocol_item = {
        "item_id": "PROTOCOL-001",
        "dimension": "protocol",
        "critical": True,
        "applicability": "required",
        "evidence_refs": ["truth://protocol"],
        "applicability_evidence_refs": [],
    }
    universe = _universe(extra_items=[protocol_item])
    candidates, scenarios = _generated_for(universe)

    result = evaluate_breadth(
        universe,
        scenario_candidates=_without_item(candidates, "PROTOCOL-001"),
        scenarios=_without_item(scenarios, "PROTOCOL-001"),
        dispositions=[],
    )

    assert (result.numerator, result.denominator) == (8, 9)
    assert result.status is AxisStatus.FAIL
    assert [miss.item_id for miss in result.critical_misses] == ["PROTOCOL-001"]


@pytest.mark.parametrize(
    "provided_refs",
    [
        ["truth://protocol/version"],
        ["truth://protocol/scoring"],
        ["truth://protocol/selection"],
        ["truth://protocol/version", "truth://protocol/scoring"],
        ["truth://protocol/version", "truth://protocol/selection"],
        ["truth://protocol/scoring", "truth://protocol/selection"],
    ],
)
def test_multi_range_obligation_rejects_every_nonempty_proper_evidence_subset(
    provided_refs: list[str],
) -> None:
    protocol_refs = [
        "truth://protocol/version",
        "truth://protocol/scoring",
        "truth://protocol/selection",
    ]
    universe = _universe(
        extra_items=[
            {
                "item_id": "PROTOCOL-001",
                "dimension": "protocol",
                "critical": True,
                "applicability": "required",
                "evidence_refs": protocol_refs,
                "applicability_evidence_refs": [],
            }
        ]
    )
    candidates, scenarios = _generated_for(universe)
    protocol_scenario = next(
        row
        for row in scenarios["items"]  # type: ignore[index]
        if row["scenario_id"] == "SCN-PROTOCOL-001"
    )
    protocol_scenario["evidence_refs"] = provided_refs

    result = evaluate_breadth(
        universe,
        scenario_candidates=candidates,
        scenarios=scenarios,
        dispositions=[],
    )

    assert result.status is AxisStatus.FAIL
    assert _metric(result, MetricName.SCENARIO_REALIZATION).miss_ids == (
        "PROTOCOL-001",
    )
    assert [miss.item_id for miss in result.critical_misses] == ["PROTOCOL-001"]


def test_one_scenario_can_realize_multiple_independently_evidenced_obligations() -> None:
    universe = _universe()
    candidates, scenarios = _generated_for(universe)
    for item_id in ("FLOW-001", "BRANCH-001"):
        candidates = _without_item(candidates, item_id)
        scenarios = _without_item(scenarios, item_id)
    scenarios["items"].append(  # type: ignore[union-attr]
        {
            "scenario_id": "SCN-MULTI-OBLIGATION",
            "coverage_item_ids": ["FLOW-001", "BRANCH-001"],
            "status": "READY",
            "evidence_refs": ["truth://FLOW-001", "truth://BRANCH-001"],
        }
    )

    result = evaluate_breadth(
        universe,
        scenario_candidates=candidates,
        scenarios=scenarios,
        dispositions=[],
    )

    realization = _metric(result, MetricName.SCENARIO_REALIZATION)
    assert realization.miss_ids == ()
    assert result.status is AxisStatus.PASS


def test_invalid_dispositions_do_not_increase_discovery_recall() -> None:
    universe = _universe()
    dispositions = [
        {
            "item_id": item_id,
            "disposition": "retain",
            "evidence_refs": [f"truth://{item_id}"],
            "covered_by": ["SCN-MISSING"],
        }
        for item_id, _dimension, _critical in CORE_ITEMS
    ]

    result = evaluate_breadth(
        universe,
        scenario_candidates=[],
        scenarios=[],
        dispositions=dispositions,
    )

    discovery = _metric(result, MetricName.DISCOVERY_RECALL)
    assert (discovery.numerator, discovery.denominator) == (0, 8)
    assert discovery.miss_ids == tuple(item_id for item_id, *_ in CORE_ITEMS)


@pytest.mark.parametrize(
    ("singular", "plural"),
    [
        ("entrypoint", "entrypoints"),
        ("flow", "flows"),
        ("branch", "branches"),
        ("state", "states"),
        ("resource", "resources"),
        ("boundary", "boundaries"),
        ("error_recovery", "errors"),
    ],
)
def test_corpus_dimension_aliases_adapt_to_the_canonical_dimension(
    singular: str,
    plural: str,
) -> None:
    universe = _universe()
    item = next(
        row
        for row in universe["items"]  # type: ignore[index]
        if row["dimension"] == plural
    )
    item["dimension"] = singular
    candidates, scenarios = _generated_for(universe)

    details = evaluate_breadth_details(
        universe,
        scenario_candidates=candidates,
        scenarios=scenarios,
        dispositions=[],
    )

    assert details.axis_result.status is AxisStatus.PASS
    assert any(result.dimension.value == plural for result in details.dimensions)


def test_existing_artifact_shapes_match_by_truth_evidence_and_candidate_ids() -> None:
    universe = _universe()
    candidates = {
        "kind": "scenario_candidates",
        "items": [
            {
                "candidate_id": f"CAND-{item_id}",
                "source": dimension,
                "evidence_refs": [f"truth://{item_id}"],
            }
            for item_id, dimension, _critical in CORE_ITEMS
        ],
    }
    scenarios = {
        "kind": "test_scenarios",
        "items": [
            {
                "scenario_id": f"SCN-{item_id}",
                "candidate_ids": [f"CAND-{item_id}"],
                "status": "READY",
                "evidence_refs": [f"truth://{item_id}"],
            }
            for item_id, _dimension, _critical in CORE_ITEMS
        ],
    }

    result = evaluate_breadth(
        universe,
        scenario_candidates=candidates,
        scenarios=scenarios,
        dispositions=[],
    )

    assert result.status is AxisStatus.PASS
    assert (result.numerator, result.denominator) == (8, 8)


def test_existing_disposition_ledgers_flatten_and_accept_covered_by_links() -> None:
    universe = _universe()
    candidates, scenarios = _generated_for(universe)
    branch_scenarios = _without_item(scenarios, "BRANCH-001")

    result = evaluate_breadth(
        universe,
        scenario_candidates=_without_item(candidates, "BRANCH-001"),
        scenarios=[
            branch_scenarios,
            {
                "kind": "black_box_cases",
                "items": [
                    {
                        "case_id": "CASE-BRANCH-001",
                        "status": "READY",
                        "coverage_target_ids": ["BRANCH-001"],
                        "source_or_test_evidence": ["truth://BRANCH-001"],
                    }
                ],
            },
        ],
        dispositions=[
            {
                "kind": "branch_disposition",
                "items": [
                    {
                        "id": "BRANCH-001",
                        "disposition": "retain",
                        "covered_by": ["CASE-BRANCH-001"],
                        "evidence_refs": ["truth://BRANCH-001"],
                    }
                ],
            },
            {"kind": "state_transition_disposition", "items": []},
        ],
    )

    assert result.numerator == result.denominator == 8
    assert _metric(result, MetricName.DISPOSITION_COMPLETENESS).miss_ids == ()
    assert _metric(result, MetricName.SCENARIO_REALIZATION).miss_ids == ()


@pytest.mark.parametrize(
    ("extra_scenarios", "covered_by"),
    [
        ([], ["CASE-MADE-UP"]),
        (
            [
                {
                    "case_id": "CASE-FLOW-ONLY",
                    "status": "READY",
                    "coverage_target_ids": ["FLOW-001"],
                    "source_or_test_evidence": ["truth://FLOW-001"],
                }
            ],
            ["CASE-FLOW-ONLY"],
        ),
        (
            [
                {
                    "case_id": "CASE-BRANCH-PARTIAL",
                    "status": "PARTIAL",
                    "coverage_target_ids": ["BRANCH-001"],
                    "source_or_test_evidence": ["truth://BRANCH-001"],
                }
            ],
            ["CASE-BRANCH-PARTIAL"],
        ),
    ],
)
def test_covered_disposition_rejects_unknown_mismatched_or_unrealized_refs(
    extra_scenarios: list[dict[str, object]],
    covered_by: list[str],
) -> None:
    universe = _universe()
    candidates, scenarios = _generated_for(universe)
    scenarios = _without_item(scenarios, "BRANCH-001")
    scenarios["items"].extend(extra_scenarios)  # type: ignore[union-attr]

    details = evaluate_breadth_details(
        universe,
        scenario_candidates=_without_item(candidates, "BRANCH-001"),
        scenarios=scenarios,
        dispositions=[
            {
                "id": "BRANCH-001",
                "disposition": "retain",
                "covered_by": covered_by,
                "evidence_refs": ["truth://BRANCH-001"],
            }
        ],
    )

    assert details.axis_result.status is AxisStatus.FAIL
    assert details.axis_result.validation_layers.l1.status is LayerStatus.FAIL
    assert details.invalid_disposition_ids == ("BRANCH-001",)
    assert _metric(
        details.axis_result,
        MetricName.SCENARIO_REALIZATION,
    ).miss_ids == ("BRANCH-001",)
    assert _metric(
        details.axis_result,
        MetricName.DISPOSITION_COMPLETENESS,
    ).miss_ids == ("BRANCH-001",)


def test_every_covered_by_reference_must_validate_for_the_same_truth_item() -> None:
    universe = _universe()
    candidates, scenarios = _generated_for(universe)
    scenarios = _without_item(scenarios, "BRANCH-001")
    scenarios["items"].append(  # type: ignore[union-attr]
        {
            "case_id": "CASE-BRANCH-001",
            "status": "READY",
            "coverage_target_ids": ["BRANCH-001"],
            "source_or_test_evidence": ["truth://BRANCH-001"],
        }
    )

    details = evaluate_breadth_details(
        universe,
        scenario_candidates=candidates,
        scenarios=scenarios,
        dispositions=[
            {
                "id": "BRANCH-001",
                "disposition": "covered_by_other",
                "covered_by": ["CASE-BRANCH-001", "CASE-MADE-UP"],
                "evidence_refs": ["truth://BRANCH-001"],
            }
        ],
    )

    assert details.axis_result.validation_layers.l1.status is LayerStatus.FAIL
    assert details.invalid_disposition_ids == ("BRANCH-001",)


def test_covered_disposition_evidence_must_intersect_independent_truth() -> None:
    universe = _universe()
    candidates, scenarios = _generated_for(universe)

    details = evaluate_breadth_details(
        universe,
        scenario_candidates=candidates,
        scenarios=scenarios,
        dispositions=[
            {
                "id": "BRANCH-001",
                "disposition": "merge_into",
                "covered_by": ["SCN-BRANCH-001"],
                "evidence_refs": ["truth://FLOW-001"],
            }
        ],
    )

    assert details.axis_result.status is AxisStatus.FAIL
    assert details.axis_result.validation_layers.l1.status is LayerStatus.FAIL
    assert "BRANCH-001" in details.invalid_disposition_ids


def test_raw_black_box_case_without_realized_status_does_not_realize_truth() -> None:
    universe = _universe()
    candidates, scenarios = _generated_for(universe)
    scenarios = _without_item(scenarios, "BRANCH-001")
    scenarios["items"].append(  # type: ignore[union-attr]
        {
            "case_id": "CASE-BRANCH-UNSTATUSSED",
            "coverage_target_ids": ["BRANCH-001"],
            "source_or_test_evidence": ["truth://BRANCH-001"],
        }
    )

    result = evaluate_breadth(
        universe,
        scenario_candidates=candidates,
        scenarios=scenarios,
        dispositions=[],
    )

    assert _metric(result, MetricName.SCENARIO_REALIZATION).miss_ids == ("BRANCH-001",)


def test_ready_self_target_without_independent_evidence_is_not_realized() -> None:
    universe = _universe()
    candidates, scenarios = _generated_for(universe)

    result = evaluate_breadth(
        universe,
        scenario_candidates=candidates,
        scenarios=[
            _without_item(scenarios, "BRANCH-001"),
            {
                "kind": "black_box_cases",
                "items": [
                    {
                        "case_id": "CASE-BRANCH-NO-EVIDENCE",
                        "status": "READY",
                        "coverage_target_ids": ["BRANCH-001"],
                    }
                ],
            },
        ],
        dispositions=[],
    )

    assert result.status is AxisStatus.FAIL
    assert _metric(result, MetricName.DISCOVERY_RECALL).miss_ids == ()
    assert _metric(result, MetricName.SCENARIO_REALIZATION).miss_ids == ("BRANCH-001",)


def test_candidate_linkage_still_requires_scenario_owned_truth_evidence() -> None:
    universe = _universe()
    candidates, scenarios = _generated_for(universe)
    scenarios = _without_item(scenarios, "BRANCH-001")
    scenarios["items"].append(  # type: ignore[union-attr]
        {
            "scenario_id": "SCN-BRANCH-WRONG-EVIDENCE",
            "candidate_ids": ["CAND-BRANCH-001"],
            "status": "READY",
            "evidence_refs": ["truth://FLOW-001"],
        }
    )

    result = evaluate_breadth(
        universe,
        scenario_candidates=candidates,
        scenarios=scenarios,
        dispositions=[],
    )

    assert result.status is AxisStatus.FAIL
    assert _metric(result, MetricName.SCENARIO_REALIZATION).miss_ids == ("BRANCH-001",)


def test_candidate_artifact_kind_cannot_be_reinterpreted_as_realized_scenario() -> None:
    universe = _universe()
    candidates, scenarios = _generated_for(universe)

    result = evaluate_breadth(
        universe,
        scenario_candidates=candidates,
        scenarios=[
            _without_item(scenarios, "BRANCH-001"),
            {
                "kind": "scenario_candidates",
                "items": [
                    {
                        "id": "SCN-CANDIDATE-AS-SCENARIO",
                        "status": "READY",
                        "coverage_target_ids": ["BRANCH-001"],
                        "evidence_refs": ["truth://BRANCH-001"],
                    }
                ],
            },
        ],
        dispositions=[],
    )

    assert result.status is AxisStatus.FAIL
    assert _metric(result, MetricName.SCENARIO_REALIZATION).miss_ids == ("BRANCH-001",)


@pytest.mark.parametrize("parent_kind", ["scenario_candidates", "unknown_parent"])
def test_parent_artifact_kind_cannot_be_overridden_by_child_kind(
    parent_kind: str,
) -> None:
    universe = _universe()
    candidates, scenarios = _generated_for(universe)

    with pytest.raises(ValueError, match="conflicting artifact kind"):
        evaluate_breadth(
            universe,
            scenario_candidates=candidates,
            scenarios=[
                _without_item(scenarios, "BRANCH-001"),
                {
                    "kind": parent_kind,
                    "items": [
                        {
                            "kind": "test_scenarios",
                            "scenario_id": "SCN-CHILD-KIND-OVERRIDE",
                            "status": "READY",
                            "coverage_target_ids": ["BRANCH-001"],
                            "evidence_refs": ["truth://BRANCH-001"],
                        }
                    ],
                },
            ],
            dispositions=[],
        )


def test_child_may_repeat_the_authoritative_parent_artifact_kind() -> None:
    universe = _universe()
    candidates, scenarios = _generated_for(universe)

    result = evaluate_breadth(
        universe,
        scenario_candidates=candidates,
        scenarios=[
            _without_item(scenarios, "BRANCH-001"),
            {
                "kind": "test_scenarios",
                "items": [
                    {
                        "kind": "test_scenarios",
                        "scenario_id": "SCN-CHILD-SAME-KIND",
                        "status": "READY",
                        "coverage_target_ids": ["BRANCH-001"],
                        "evidence_refs": ["truth://BRANCH-001"],
                    }
                ],
            },
        ],
        dispositions=[],
    )

    assert result.status is AxisStatus.PASS
    assert _metric(result, MetricName.SCENARIO_REALIZATION).miss_ids == ()


def test_disposition_covered_by_accepts_primary_and_case_ids_aliases() -> None:
    universe = _universe()
    candidates, scenarios = _generated_for(universe)

    result = evaluate_breadth(
        universe,
        scenario_candidates=_without_item(candidates, "BRANCH-001"),
        scenarios=[
            _without_item(scenarios, "BRANCH-001"),
            {
                "kind": "black_box_cases",
                "items": [
                    {
                        "scenario_id": "SCN-BRANCH-ALIASED",
                        "case_id": "CASE-BRANCH-PRIMARY",
                        "case_ids": [
                            "CASE-BRANCH-ALIAS-1",
                            "CASE-BRANCH-ALIAS-2",
                        ],
                        "status": "READY",
                        "coverage_target_ids": ["BRANCH-001"],
                        "source_or_test_evidence": ["truth://BRANCH-001"],
                    }
                ],
            },
        ],
        dispositions=[
            {
                "id": "BRANCH-001",
                "disposition": "covered_by_other",
                "covered_by": [
                    "SCN-BRANCH-ALIASED",
                    "CASE-BRANCH-PRIMARY",
                    "CASE-BRANCH-ALIAS-2",
                ],
                "evidence_refs": ["truth://BRANCH-001"],
            }
        ],
    )

    assert result.status is AxisStatus.PASS
    assert _metric(result, MetricName.SCENARIO_REALIZATION).miss_ids == ()


def test_duplicate_scenario_alias_claims_are_rejected() -> None:
    universe = _universe()
    candidates, scenarios = _generated_for(universe)

    with pytest.raises(ValueError, match="duplicate or ambiguous scenario alias"):
        evaluate_breadth(
            universe,
            scenario_candidates=candidates,
            scenarios=[
                scenarios,
                {
                    "kind": "black_box_cases",
                    "items": [
                        {
                            "case_id": "CASE-ONE",
                            "case_ids": ["CASE-SHARED"],
                            "status": "READY",
                            "coverage_target_ids": ["BRANCH-001"],
                            "source_or_test_evidence": ["truth://BRANCH-001"],
                        },
                        {
                            "case_id": "CASE-TWO",
                            "case_ids": ["CASE-SHARED"],
                            "status": "READY",
                            "coverage_target_ids": ["FLOW-001"],
                            "source_or_test_evidence": ["truth://FLOW-001"],
                        },
                    ],
                },
            ],
            dispositions=[],
        )


def test_each_core_dimension_requires_a_non_excludable_truth_item() -> None:
    universe = _universe()
    for row in universe["items"]:  # type: ignore[index]
        row["applicability"] = "conditional"
        row["applicability_evidence_refs"] = [f"truth://NA/{row['item_id']}"]

    with pytest.raises(
        ValueError,
        match="core dimensions require at least one required item",
    ):
        evaluate_breadth(
            universe,
            scenario_candidates=[],
            scenarios=[],
            dispositions=[],
        )


def test_duplicate_universe_ids_are_rejected() -> None:
    universe = _universe()
    universe["items"].append(deepcopy(universe["items"][0]))  # type: ignore[index,union-attr]

    with pytest.raises(ValueError, match="duplicate coverage universe item_id"):
        evaluate_breadth(
            universe,
            scenario_candidates=[],
            scenarios=[],
            dispositions=[],
        )
