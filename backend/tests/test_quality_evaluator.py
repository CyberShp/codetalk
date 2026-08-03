from __future__ import annotations

import importlib

import pytest

from app.services.quality_evaluation_contract import (
    AxisResult,
    AxisStatus,
    BenchmarkIdentity,
    EvaluationScope,
    EvaluationSnapshot,
    LayerStatus,
    MetricName,
    RatioMetric,
    RepairSummary,
    ValidationLayerOutcome,
    ValidationLayers,
)


def _quality():
    try:
        return importlib.import_module("app.services.quality_evaluator")
    except ModuleNotFoundError:
        pytest.fail("quality_evaluator is not implemented; expected P3 RED")


def _layer(status: LayerStatus = LayerStatus.PASS) -> ValidationLayerOutcome:
    return ValidationLayerOutcome(
        status=status,
        numerator=1 if status is LayerStatus.PASS else 0,
        denominator=1,
        critical_miss_ids=(),
        evidence_refs=("source://fixture#L1",) if status is LayerStatus.PASS else (),
        limitations=("L3_NOT_RUN",) if status is LayerStatus.NOT_RUN else (),
    )


def _axis(name: str, status: AxisStatus = AxisStatus.PASS) -> AxisResult:
    required = {
        "accuracy": [MetricName.CLAIM_PRECISION, MetricName.GOLD_RECALL],
        "breadth": [
            MetricName.DISCOVERY_RECALL,
            MetricName.CRITICAL_COVERAGE,
            MetricName.SCENARIO_REALIZATION,
            MetricName.DISPOSITION_COMPLETENESS,
        ],
        "depth": [
            MetricName.MINIMUM_CRITICAL_CHAIN_CLOSURE,
            MetricName.AVERAGE_CHAIN_CLOSURE,
            MetricName.STATE_CLOSURE,
            MetricName.RESOURCE_LIFECYCLE_CLOSURE,
            MetricName.ERROR_RECOVERY_CLOSURE,
            MetricName.DISCONFIRMING_CHECKS,
        ],
    }
    l3 = _layer(LayerStatus.NOT_RUN if status is AxisStatus.LIMITED else LayerStatus.PASS)
    return AxisResult(
        status=status,
        numerator=0 if status is AxisStatus.FAIL else 1,
        denominator=1,
        critical_misses=(),
        evidence_refs=(f"artifact://{name}.json",),
        limitations=("L3_NOT_RUN",) if status is AxisStatus.LIMITED else (),
        validation_layers=ValidationLayers(
            L0=_layer(), L1=_layer(), L2=_layer(), L3=l3
        ),
        metrics=tuple(
            RatioMetric(
                name=metric,
                numerator=0 if status is AxisStatus.FAIL else 1,
                denominator=1,
                miss_ids=(f"{name}:open",) if status is AxisStatus.FAIL else (),
            )
            for metric in required[name]
        ),
    )


def _snapshot(*, depth: AxisStatus = AxisStatus.PASS) -> EvaluationSnapshot:
    return EvaluationSnapshot(
        accuracy=_axis("accuracy"),
        breadth=_axis("breadth"),
        depth=_axis("depth", depth),
    )


def test_build_report_uses_a_conjunctive_gate_without_aggregate_score() -> None:
    report = _quality().build_quality_report(
        scope=EvaluationScope.INDEPENDENT_BENCHMARK,
        run_ref="run-1",
        benchmark_identity=BenchmarkIdentity(
            case_id="case-1", source_revision="a" * 40, truth_package_version="1"
        ),
        first_pass=_snapshot(depth=AxisStatus.FAIL),
        final_after_auto_repair=_snapshot(depth=AxisStatus.FAIL),
        repair_summary=RepairSummary(
            attempt_count=0, elapsed_seconds=0, terminal_block_reason=None
        ),
    )

    assert report.delivery_status.value == "not_ready"
    payload = report.model_dump(mode="json")
    assert not {"overall_score", "weighted_score", "aggregate_score"} & payload.keys()
    assert report.final_after_auto_repair.accuracy.status is AxisStatus.PASS
    assert report.final_after_auto_repair.breadth.status is AxisStatus.PASS
    assert report.final_after_auto_repair.depth.status is AxisStatus.FAIL


def test_report_preserves_first_and_repaired_snapshots_without_recalculation() -> None:
    first = _snapshot(depth=AxisStatus.FAIL)
    final = _snapshot(depth=AxisStatus.LIMITED)
    report = _quality().build_quality_report(
        scope="independent_benchmark",
        run_ref="run-2",
        benchmark_identity={
            "case_id": "case-2",
            "source_revision": "b" * 40,
            "truth_package_version": "1",
        },
        first_pass=first,
        final_after_auto_repair=final,
        repair_summary={
            "attempt_count": 1,
            "elapsed_seconds": 2.5,
            "terminal_block_reason": None,
        },
    )

    assert report.first_pass == first
    assert report.final_after_auto_repair == final
    assert report.delivery_status.value == "limited"
    assert report.limitations == ("L3_NOT_RUN",)


def test_axis_evaluator_is_called_once_per_axis_and_returns_snapshot(monkeypatch) -> None:
    module = _quality()
    calls: list[str] = []
    monkeypatch.setattr(module, "evaluate_accuracy", lambda **_: calls.append("accuracy") or _axis("accuracy"))
    monkeypatch.setattr(module, "evaluate_breadth", lambda *_, **__: calls.append("breadth") or _axis("breadth"))
    monkeypatch.setattr(module, "evaluate_depth", lambda *_, **__: calls.append("depth") or _axis("depth"))

    snapshot = module.evaluate_quality_snapshot(
        accuracy_inputs={},
        breadth_inputs={"universe": []},
        depth_inputs={"truth": {}, "candidate": {}, "catalog": object()},
    )

    assert calls == ["accuracy", "breadth", "depth"]
    assert snapshot == _snapshot()


def test_synthetic_checkpoint_keeps_exact_open_depth_obligation_and_repairs_only_depth() -> None:
    module = _quality()
    first = _snapshot(depth=AxisStatus.FAIL)
    repaired = _snapshot(depth=AxisStatus.PASS)

    blocked = module.build_quality_report(
        scope=EvaluationScope.INDEPENDENT_BENCHMARK,
        run_ref="checkpoint-first",
        benchmark_identity={
            "case_id": "synthetic-depth-gap",
            "source_revision": "e" * 40,
            "truth_package_version": "1",
        },
        first_pass=first,
        final_after_auto_repair=first,
        repair_summary={
            "attempt_count": 0,
            "elapsed_seconds": 0,
            "terminal_block_reason": None,
        },
    )
    final = module.build_quality_report(
        scope=EvaluationScope.INDEPENDENT_BENCHMARK,
        run_ref="checkpoint-repaired",
        benchmark_identity={
            "case_id": "synthetic-depth-gap",
            "source_revision": "e" * 40,
            "truth_package_version": "1",
        },
        first_pass=first,
        final_after_auto_repair=repaired,
        repair_summary={
            "attempt_count": 1,
            "elapsed_seconds": 1.5,
            "terminal_block_reason": None,
        },
    )

    open_depth = next(
        metric
        for metric in blocked.final_after_auto_repair.depth.metrics
        if metric.name is MetricName.MINIMUM_CRITICAL_CHAIN_CLOSURE
    )
    assert blocked.delivery_status.value == "not_ready"
    assert open_depth.miss_ids == ("depth:open",)
    assert final.delivery_status.value == "ready"
    assert final.first_pass.accuracy == final.final_after_auto_repair.accuracy
    assert final.first_pass.breadth == final.final_after_auto_repair.breadth
    assert final.first_pass.depth != final.final_after_auto_repair.depth
    assert "aggregate_score" not in final.model_dump(mode="json")


def test_store_is_immutable_for_the_same_run_reference(tmp_path) -> None:
    module = _quality()
    report = module.build_quality_report(
        scope=EvaluationScope.INDEPENDENT_BENCHMARK,
        run_ref="immutable-run",
        benchmark_identity={
            "case_id": "immutable-case",
            "source_revision": "f" * 40,
            "truth_package_version": "1",
        },
        first_pass=_snapshot(),
        final_after_auto_repair=_snapshot(),
        repair_summary={
            "attempt_count": 0,
            "elapsed_seconds": 0,
            "terminal_block_reason": None,
        },
    )
    store = module.QualityEvaluationStore(tmp_path)
    store.write(report)

    with pytest.raises(module.QualityEvaluationStoreError, match="already exists"):
        store.write(report)
