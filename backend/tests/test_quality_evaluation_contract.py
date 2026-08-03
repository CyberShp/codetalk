from __future__ import annotations

import importlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "quality_evaluation"
SCHEMA_PATH = (
    Path(__file__).parents[2]
    / "benchmarks"
    / "quality"
    / "schemas"
    / "quality-evaluation-v1.schema.json"
)


def _contract():
    try:
        return importlib.import_module("app.services.quality_evaluation_contract")
    except ModuleNotFoundError:
        pytest.fail(
            "quality_evaluation_contract is not implemented; this is the expected P0 RED"
        )


def _metric(name: str, numerator: int = 1, denominator: int = 1) -> dict[str, object]:
    return {
        "name": name,
        "numerator": numerator,
        "denominator": denominator,
        "miss_ids": [],
    }


def _layers() -> dict[str, object]:
    return {
        "L0": {
            "status": "pass",
            "numerator": 1,
            "denominator": 1,
            "critical_miss_ids": [],
            "evidence_refs": ["artifact://task_artifact_manifest.json"],
            "limitations": [],
        },
        "L1": {
            "status": "pass",
            "numerator": 1,
            "denominator": 1,
            "critical_miss_ids": [],
            "evidence_refs": ["source://lib/example.c#L10-L20"],
            "limitations": [],
        },
        "L2": {
            "status": "pass",
            "numerator": 1,
            "denominator": 1,
            "critical_miss_ids": [],
            "evidence_refs": ["claim://claim-001"],
            "limitations": [],
        },
        "L3": {
            "status": "not_run",
            "numerator": 0,
            "denominator": 1,
            "critical_miss_ids": [],
            "evidence_refs": [],
            "limitations": ["L3_NOT_RUN"],
        },
    }


def _axis(name: str, *, benchmark: bool) -> dict[str, object]:
    required_metrics = {
        "accuracy": ["claim_precision"],
        "breadth": [
            "discovery_recall",
            "critical_coverage",
            "scenario_realization",
            "disposition_completeness",
        ],
        "depth": [
            "minimum_critical_chain_closure",
            "average_chain_closure",
            "state_closure",
            "resource_lifecycle_closure",
            "error_recovery_closure",
            "disconfirming_checks",
        ],
    }
    metrics = [_metric(metric_name) for metric_name in required_metrics[name]]
    if name == "accuracy" and benchmark:
        metrics.append(_metric("gold_recall"))
    return {
        "status": "limited",
        "numerator": 1,
        "denominator": 1,
        "critical_misses": [],
        "evidence_refs": [f"artifact://{name}.json"],
        "limitations": ["L3_NOT_RUN"],
        "validation_layers": _layers(),
        "metrics": metrics,
    }


def _snapshot(*, benchmark: bool) -> dict[str, object]:
    return {
        name: _axis(name, benchmark=benchmark)
        for name in ("accuracy", "breadth", "depth")
    }


def _report(*, scope: str = "operational") -> dict[str, object]:
    benchmark = scope == "independent_benchmark"
    report: dict[str, object] = {
        "schema_version": "quality-evaluation-v1",
        "scope": scope,
        "run_ref": "run-001",
        "benchmark_identity": None,
        "delivery_status": "limited",
        "first_pass": _snapshot(benchmark=benchmark),
        "final_after_auto_repair": _snapshot(benchmark=benchmark),
        "repair_summary": {
            "attempt_count": 0,
            "elapsed_seconds": 0,
            "terminal_block_reason": None,
        },
        "hard_failures": [],
        "limitations": ["L3_NOT_RUN"],
    }
    if benchmark:
        report["benchmark_identity"] = {
            "case_id": "rdma-core-cq-error-recovery-001",
            "source_revision": "a" * 40,
            "truth_package_version": "1",
        }
    return report


def _ready_report() -> dict[str, object]:
    report = _report()
    report["delivery_status"] = "ready"
    report["limitations"] = []
    for snapshot_name in ("first_pass", "final_after_auto_repair"):
        snapshot = report[snapshot_name]
        for axis_name in ("accuracy", "breadth", "depth"):
            axis = snapshot[axis_name]  # type: ignore[index]
            axis["status"] = "pass"
            axis["limitations"] = []
            l3 = axis["validation_layers"]["L3"]
            l3["status"] = "pass"
            l3["numerator"] = 1
            l3["evidence_refs"] = ["oracle://case-001"]
            l3["limitations"] = []
    return report


def _validate(payload: dict[str, object]):
    return _contract().validate_quality_evaluation(payload)


def _fixture_payload(name: str) -> dict[str, object]:
    payload = json.loads((FIXTURE_ROOT / name).read_text())
    base_name = payload.pop("$base", None)
    if not base_name:
        return payload
    base = deepcopy(json.loads((FIXTURE_ROOT / base_name).read_text()))
    for mutation in payload["mutations"]:
        target = base
        segments = mutation["path"].strip("/").split("/")
        for segment in segments[:-1]:
            target = target[int(segment)] if isinstance(target, list) else target[segment]
        key = segments[-1]
        if mutation["op"] == "append":
            target[key].append(mutation["value"])
        else:
            target[key] = mutation["value"]
    return base


def _schema_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))


def test_accepts_operational_and_independent_benchmark_scopes() -> None:
    assert _validate(_report()).scope.value == "operational"
    assert _validate(_report(scope="independent_benchmark")).scope.value == (
        "independent_benchmark"
    )


def test_rejects_unknown_scope() -> None:
    with pytest.raises(ValidationError, match="scope"):
        _validate(_report(scope="self_scored"))


@pytest.mark.parametrize("source_revision", ["main", "abc123", "g" * 40, "A" * 40])
def test_independent_benchmark_requires_canonical_40_character_commit(
    source_revision: str,
) -> None:
    report = _report(scope="independent_benchmark")
    report["benchmark_identity"]["source_revision"] = source_revision  # type: ignore[index]

    with pytest.raises(ValidationError, match="source_revision"):
        _validate(report)


def test_independent_benchmark_requires_truth_identity() -> None:
    report = _report(scope="independent_benchmark")
    report["benchmark_identity"] = None

    with pytest.raises(ValidationError, match="benchmark_identity"):
        _validate(report)


def test_operational_scope_rejects_benchmark_identity() -> None:
    report = _report()
    report["benchmark_identity"] = {
        "case_id": "hidden-case",
        "source_revision": "b" * 40,
        "truth_package_version": "1",
    }

    with pytest.raises(ValidationError, match="benchmark_identity"):
        _validate(report)


def test_operational_scope_cannot_claim_gold_recall() -> None:
    report = _report()
    accuracy = report["first_pass"]["accuracy"]  # type: ignore[index]
    accuracy["metrics"].append(_metric("gold_recall"))  # type: ignore[union-attr]

    with pytest.raises(ValidationError, match="gold_recall"):
        _validate(report)


def test_independent_benchmark_requires_gold_recall_in_both_snapshots() -> None:
    report = _report(scope="independent_benchmark")
    accuracy = report["final_after_auto_repair"]["accuracy"]  # type: ignore[index]
    accuracy["metrics"] = [  # type: ignore[index]
        metric
        for metric in accuracy["metrics"]  # type: ignore[index]
        if metric["name"] != "gold_recall"
    ]

    with pytest.raises(ValidationError, match="gold_recall"):
        _validate(report)


@pytest.mark.parametrize(
    "field",
    ["overall_score", "weighted_score", "aggregate_score", "quality_score"],
)
def test_rejects_aggregate_score_fields(field: str) -> None:
    report = _report()
    report[field] = 100

    with pytest.raises(ValidationError, match=field):
        _validate(report)


def test_rejects_zero_axis_denominator() -> None:
    report = _report()
    report["first_pass"]["breadth"]["denominator"] = 0  # type: ignore[index]

    with pytest.raises(ValidationError, match="denominator"):
        _validate(report)


def test_rejects_zero_metric_and_layer_denominators() -> None:
    for path in ("metric", "layer"):
        report = _report()
        accuracy = report["first_pass"]["accuracy"]  # type: ignore[index]
        if path == "metric":
            accuracy["metrics"][0]["denominator"] = 0  # type: ignore[index]
        else:
            accuracy["validation_layers"]["L1"]["denominator"] = 0  # type: ignore[index]

        with pytest.raises(ValidationError, match="denominator"):
            _validate(report)


def test_axis_with_critical_miss_must_fail() -> None:
    report = _report()
    accuracy = report["first_pass"]["accuracy"]  # type: ignore[index]
    accuracy["status"] = "pass"  # type: ignore[index]
    accuracy["critical_misses"] = [  # type: ignore[index]
        {
            "item_id": "claim-critical-001",
            "reason": "contradicted by exact source evidence",
            "validation_layer": "L2",
            "evidence_refs": ["source://lib/example.c#L10-L20"],
        }
    ]

    with pytest.raises(ValidationError, match="critical_misses"):
        _validate(report)


def test_layer_critical_miss_also_requires_axis_failure() -> None:
    report = _report()
    accuracy = report["first_pass"]["accuracy"]  # type: ignore[index]
    accuracy["status"] = "pass"  # type: ignore[index]
    l2 = accuracy["validation_layers"]["L2"]  # type: ignore[index]
    l2["status"] = "fail"  # type: ignore[index]
    l2["critical_miss_ids"] = ["claim-critical-001"]  # type: ignore[index]

    with pytest.raises(ValidationError, match="validation layer"):
        _validate(report)


def test_report_with_failed_axis_must_be_not_ready() -> None:
    report = _report()
    report["final_after_auto_repair"]["depth"]["status"] = "fail"  # type: ignore[index]

    with pytest.raises(ValidationError, match="delivery_status"):
        _validate(report)


def test_final_not_run_layer_requires_limited_axis_and_report() -> None:
    report = _ready_report()
    l3 = report["final_after_auto_repair"]["depth"]["validation_layers"]["L3"]  # type: ignore[index]
    l3["status"] = "not_run"  # type: ignore[index]
    l3["numerator"] = 0  # type: ignore[index]
    l3["limitations"] = ["L3_NOT_RUN"]  # type: ignore[index]

    with pytest.raises(ValidationError, match="limited"):
        _validate(report)

    report["final_after_auto_repair"]["depth"]["status"] = "limited"  # type: ignore[index]
    report["delivery_status"] = "limited"
    report["repair_summary"]["attempt_count"] = 1  # type: ignore[index]
    assert _validate(report).delivery_status.value == "limited"


@pytest.mark.parametrize("location", ["axis", "layer"])
def test_final_axis_or_layer_limitation_requires_limited_status(location: str) -> None:
    report = _ready_report()
    accuracy = report["final_after_auto_repair"]["accuracy"]  # type: ignore[index]
    if location == "axis":
        accuracy["limitations"] = ["HARDWARE_UNAVAILABLE"]  # type: ignore[index]
    else:
        accuracy["validation_layers"]["L3"]["limitations"] = [  # type: ignore[index]
            "HARDWARE_UNAVAILABLE"
        ]

    with pytest.raises(ValidationError, match="limited"):
        _validate(report)


@pytest.mark.parametrize("value", ["1", True])
@pytest.mark.parametrize(
    "target",
    ["axis_numerator", "metric_denominator", "layer_numerator", "attempt_count", "elapsed_seconds"],
)
def test_contract_strictly_rejects_string_and_boolean_counts(
    target: str,
    value: object,
) -> None:
    report = _report()
    accuracy = report["first_pass"]["accuracy"]  # type: ignore[index]
    if target == "axis_numerator":
        accuracy["numerator"] = value  # type: ignore[index]
    elif target == "metric_denominator":
        accuracy["metrics"][0]["denominator"] = value  # type: ignore[index]
    elif target == "layer_numerator":
        accuracy["validation_layers"]["L1"]["numerator"] = value  # type: ignore[index]
    else:
        report["repair_summary"][target] = value  # type: ignore[index]

    with pytest.raises(ValidationError):
        _validate(report)


def test_changed_final_snapshot_requires_a_recorded_repair_attempt() -> None:
    report = _report()
    report["final_after_auto_repair"]["accuracy"]["numerator"] = 0  # type: ignore[index]

    with pytest.raises(ValidationError, match="attempt_count"):
        _validate(report)

    report["repair_summary"]["attempt_count"] = 1  # type: ignore[index]
    assert _validate(report).repair_summary.attempt_count == 1


@pytest.mark.parametrize("invariant", ["numerator_order", "snapshot_repair"])
def test_public_validator_rejects_pydantic_only_invariants(invariant: str) -> None:
    contract = _contract()
    validate = getattr(contract, "validate_quality_evaluation", None)
    assert callable(validate), "public fail-closed validator is required"
    report = _report()
    if invariant == "numerator_order":
        report["first_pass"]["accuracy"]["numerator"] = 2  # type: ignore[index]
    else:
        report["final_after_auto_repair"]["accuracy"]["numerator"] = 0  # type: ignore[index]

    with pytest.raises(ValidationError):
        validate(report)


@pytest.mark.parametrize("layer", ["L0", "L1", "L2", "L3"])
def test_requires_every_validation_layer(layer: str) -> None:
    report = _report()
    del report["first_pass"]["accuracy"]["validation_layers"][layer]  # type: ignore[index]

    with pytest.raises(ValidationError, match=layer):
        _validate(report)


def test_rejects_unknown_validation_layer_status() -> None:
    report = _report()
    report["first_pass"]["accuracy"]["validation_layers"]["L2"][  # type: ignore[index]
        "status"
    ] = "assumed"

    with pytest.raises(ValidationError, match="status"):
        _validate(report)


def test_requires_first_and_final_snapshots() -> None:
    for field in ("first_pass", "final_after_auto_repair"):
        report = _report()
        del report[field]

        with pytest.raises(ValidationError, match=field):
            _validate(report)


def test_rejects_unknown_fields_at_nested_levels() -> None:
    report = _report()
    report["first_pass"]["accuracy"]["mystery"] = True  # type: ignore[index]

    with pytest.raises(ValidationError, match="mystery"):
        _validate(report)


def test_canonical_fixtures_validate_or_fail_closed() -> None:
    valid_names = ("valid_operational.json", "valid_independent_benchmark.json")
    invalid_names = (
        "invalid_aggregate_score.json",
        "invalid_critical_miss_pass.json",
        "invalid_operational_gold_recall.json",
        "invalid_truth_leakage.json",
        "invalid_unknown_field.json",
    )

    for name in valid_names:
        assert _validate(_fixture_payload(name)).schema_version == (
            "quality-evaluation-v1"
        )
    for name in invalid_names:
        with pytest.raises(ValidationError):
            _validate(_fixture_payload(name))


def test_serialization_is_deterministic_and_round_trips() -> None:
    contract = _contract()
    report = _validate(_report(scope="independent_benchmark"))

    first = contract.serialize_quality_evaluation(report)
    second = contract.serialize_quality_evaluation(
        contract.QualityEvaluationReport.model_validate_json(first)
    )

    assert first == second
    assert first.endswith("\n")
    assert "overall_score" not in first


def test_committed_json_schema_matches_generated_contract() -> None:
    contract = _contract()
    committed = json.loads(SCHEMA_PATH.read_text())

    assert committed == contract.quality_evaluation_json_schema()


def test_json_schema_expresses_scope_identity_and_gold_recall_conditions() -> None:
    schema = _contract().quality_evaluation_json_schema()
    encoded = json.dumps(schema, sort_keys=True)

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert '"allOf"' in encoded
    assert '"const": "operational"' in encoded
    assert '"const": "independent_benchmark"' in encoded
    assert encoded.count('"const": "gold_recall"') == 4


def test_schema_runtime_authority_metadata_is_explicit() -> None:
    metadata = json.loads(SCHEMA_PATH.read_text())["x-codetalk-runtime-validation"]

    assert metadata["authority"] == "pydantic"
    assert metadata["public_validator"] == (
        "app.services.quality_evaluation_contract.validate_quality_evaluation"
    )
    assert {item["id"] for item in metadata["invariants"]} == {
        "numerator_lte_denominator",
        "snapshot_change_requires_positive_attempt_count",
    }


def test_draft_2020_schema_rejects_terminal_block_reason_with_ready() -> None:
    report = _ready_report()
    report["repair_summary"]["terminal_block_reason"] = "repair exhausted"  # type: ignore[index]

    assert list(_schema_validator().iter_errors(report))


@pytest.mark.parametrize("wrong_status", ["limited", "not_ready"])
def test_draft_2020_schema_requires_ready_for_clean_pass(wrong_status: str) -> None:
    report = _ready_report()
    report["delivery_status"] = wrong_status

    assert list(_schema_validator().iter_errors(report))


@pytest.mark.parametrize("wrong_status", ["ready", "not_ready"])
@pytest.mark.parametrize("limited_by", ["axis", "report"])
def test_draft_2020_schema_requires_limited_for_limitations(
    limited_by: str,
    wrong_status: str,
) -> None:
    report = _ready_report()
    if limited_by == "axis":
        report["final_after_auto_repair"]["depth"]["status"] = "limited"  # type: ignore[index]
        report["final_after_auto_repair"]["depth"]["limitations"] = [  # type: ignore[index]
            "L3_NOT_RUN"
        ]
        report["repair_summary"]["attempt_count"] = 1  # type: ignore[index]
    else:
        report["limitations"] = ["L3_NOT_RUN"]
    report["delivery_status"] = wrong_status

    assert list(_schema_validator().iter_errors(report))


@pytest.mark.parametrize("wrong_status", ["ready", "limited"])
@pytest.mark.parametrize("failed_by", ["axis", "hard_failure"])
def test_draft_2020_schema_requires_not_ready_for_failures(
    failed_by: str,
    wrong_status: str,
) -> None:
    report = _ready_report()
    if failed_by == "axis":
        report["final_after_auto_repair"]["accuracy"]["status"] = "fail"  # type: ignore[index]
        report["repair_summary"]["attempt_count"] = 1  # type: ignore[index]
    else:
        report["hard_failures"] = [
            {
                "code": "CRITICAL_CONTRADICTION",
                "message": "critical claim contradicted",
                "evidence_refs": ["claim://claim-001"],
                "unrecoverable": True,
            }
        ]
    report["delivery_status"] = wrong_status

    assert list(_schema_validator().iter_errors(report))


def test_draft_2020_schema_accepts_exact_delivery_status_vectors() -> None:
    clean = _ready_report()
    limited = _ready_report()
    limited["limitations"] = ["L3_NOT_RUN"]
    limited["delivery_status"] = "limited"
    failed = _ready_report()
    failed["final_after_auto_repair"]["accuracy"]["status"] = "fail"  # type: ignore[index]
    failed["repair_summary"]["attempt_count"] = 1  # type: ignore[index]
    failed["delivery_status"] = "not_ready"
    terminal = _ready_report()
    terminal["repair_summary"]["terminal_block_reason"] = "repair exhausted"  # type: ignore[index]
    terminal["delivery_status"] = "not_ready"

    validator = _schema_validator()
    for report in (clean, limited, failed, terminal):
        assert not list(validator.iter_errors(report))


@pytest.mark.parametrize(
    ("axis_name", "metric_name"),
    [
        ("accuracy", "claim_precision"),
        ("breadth", "discovery_recall"),
        ("breadth", "critical_coverage"),
        ("breadth", "scenario_realization"),
        ("breadth", "disposition_completeness"),
        ("depth", "minimum_critical_chain_closure"),
        ("depth", "average_chain_closure"),
        ("depth", "state_closure"),
        ("depth", "resource_lifecycle_closure"),
        ("depth", "error_recovery_closure"),
        ("depth", "disconfirming_checks"),
    ],
)
def test_draft_2020_schema_requires_every_axis_metric(
    axis_name: str,
    metric_name: str,
) -> None:
    report = _ready_report()
    axis = report["final_after_auto_repair"][axis_name]  # type: ignore[index]
    axis["metrics"] = [  # type: ignore[index]
        metric for metric in axis["metrics"] if metric["name"] != metric_name  # type: ignore[index]
    ]

    assert list(_schema_validator().iter_errors(report))


def test_draft_2020_schema_keeps_scope_specific_gold_rules() -> None:
    operational = _ready_report()
    operational["final_after_auto_repair"]["accuracy"]["metrics"].append(  # type: ignore[index]
        _metric("gold_recall")
    )
    benchmark = _report(scope="independent_benchmark")
    benchmark_accuracy = benchmark["final_after_auto_repair"]["accuracy"]  # type: ignore[index]
    benchmark_accuracy["metrics"] = [  # type: ignore[index]
        metric
        for metric in benchmark_accuracy["metrics"]  # type: ignore[index]
        if metric["name"] != "gold_recall"
    ]

    validator = _schema_validator()
    assert list(validator.iter_errors(operational))
    assert list(validator.iter_errors(benchmark))


@pytest.mark.parametrize("axis_status", ["pass", "limited"])
def test_draft_2020_schema_rejects_critical_miss_without_failed_axis(
    axis_status: str,
) -> None:
    report = _ready_report()
    accuracy = report["final_after_auto_repair"]["accuracy"]  # type: ignore[index]
    accuracy["status"] = axis_status  # type: ignore[index]
    accuracy["critical_misses"] = [  # type: ignore[index]
        {
            "item_id": "claim-critical-001",
            "reason": "contradicted",
            "validation_layer": "L2",
            "evidence_refs": ["source://lib/example.c#L10-L20"],
        }
    ]
    if axis_status == "limited":
        report["delivery_status"] = "limited"

    validator = Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))
    assert list(validator.iter_errors(report))


@pytest.mark.parametrize("axis_status", ["pass", "limited"])
def test_draft_2020_schema_rejects_failed_layer_without_failed_axis(
    axis_status: str,
) -> None:
    report = _ready_report()
    accuracy = report["final_after_auto_repair"]["accuracy"]  # type: ignore[index]
    accuracy["status"] = axis_status  # type: ignore[index]
    accuracy["validation_layers"]["L2"]["status"] = "fail"  # type: ignore[index]
    if axis_status == "limited":
        report["delivery_status"] = "limited"

    validator = Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))
    assert list(validator.iter_errors(report))


def test_draft_2020_schema_rejects_hard_failure_with_ready_report() -> None:
    report = _ready_report()
    report["hard_failures"] = [
        {
            "code": "CRITICAL_CONTRADICTION",
            "message": "critical claim contradicted",
            "evidence_refs": ["claim://claim-001"],
            "unrecoverable": True,
        }
    ]

    validator = Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))
    assert list(validator.iter_errors(report))


def test_draft_2020_schema_rejects_limitation_with_ready_report() -> None:
    report = _ready_report()
    report["limitations"] = ["L3_NOT_RUN"]

    schema = json.loads(SCHEMA_PATH.read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    assert not list(validator.iter_errors(_ready_report()))
    assert list(validator.iter_errors(report))


def test_frozen_contract_models_cannot_be_mutated() -> None:
    report = _validate(deepcopy(_report()))

    with pytest.raises(ValidationError, match="frozen"):
        report.run_ref = "changed"  # type: ignore[misc]
