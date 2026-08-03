from __future__ import annotations

from app.services.quality_calibration_mutations import (
    build_quality_calibration_mutation_matrix,
)


def test_every_release_metric_has_real_fail_closed_mutation_replay() -> None:
    matrix = build_quality_calibration_mutation_matrix()

    assert len(matrix["matrix_sha256"]) == 64
    assert {axis: set(metrics) for axis, metrics in matrix["mutations"].items()} == {
        "accuracy": {"claim_precision", "gold_recall"},
        "breadth": {
            "discovery_recall",
            "critical_coverage",
            "scenario_realization",
            "disposition_completeness",
        },
        "depth": {
            "minimum_critical_chain_closure",
            "average_chain_closure",
            "state_closure",
            "resource_lifecycle_closure",
            "error_recovery_closure",
            "disconfirming_checks",
        },
    }
    for metrics in matrix["mutations"].values():
        for mutation in metrics.values():
            assert mutation["baseline"]["ratio"] == 1.0
            assert mutation["mutated"]["ratio"] < 1.0
            assert mutation["mutated_axis_status"] == "fail"
