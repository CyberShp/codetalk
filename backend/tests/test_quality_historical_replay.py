from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from app.services.quality_baseline import (
    BUNDLE_SCHEMA_VERSION,
    BaselineError,
    build_regression_matrix,
    compare_historical_replay,
    compare_rapid_deep_runs,
)
from tests.test_quality_baseline_policy import (
    CORPUS,
    TEST_IDENTITY,
    VERSIONS,
    _write_run,
)


def _accepted_bundle(
    root: Path,
    runs: list[Path],
    *,
    accepted: bool = True,
) -> Path:
    root.mkdir(parents=True)
    for run in runs:
        destination = root / "runs" / run.name / "evaluation"
        destination.parent.mkdir(parents=True)
        shutil.copytree(run, destination)
    (root / "release_gate.json").write_text(
        json.dumps({"release_gate": "pass" if accepted else "fail"}) + "\n"
    )
    (root / "regression_matrix.json").write_text(
        json.dumps({"core_baseline_blocked": not accepted}) + "\n"
    )
    hashes = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
    manifest = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "bundle_status": "passed" if accepted else "blocked",
        "artifact_sha256": hashes,
        "evaluation_identity": TEST_IDENTITY.as_dict(),
    }
    (root / "baseline_manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    )
    return root


def test_previous_baseline_unavailable_unproven_or_unaccepted_is_not_run(
    tmp_path: Path,
) -> None:
    assert compare_historical_replay([], None) == {
        "status": "not_run",
        "reason": "previous_baseline_unavailable",
        "regressions": [],
    }
    assert compare_historical_replay([], []) == {
        "status": "not_run",
        "reason": "previous_baseline_not_proven_accepted",
        "regressions": [],
    }
    previous = _write_run(tmp_path / "runs", case=CORPUS.cases[0])
    bundle = _accepted_bundle(tmp_path / "blocked", [previous], accepted=False)
    assert compare_historical_replay([], bundle) == {
        "status": "not_run",
        "reason": "previous_baseline_not_accepted",
        "regressions": [],
    }


def test_history_requires_same_case_source_truth_and_evaluator(tmp_path: Path) -> None:
    case = CORPUS.cases[0]
    previous = _write_run(tmp_path / "previous-runs", case=case)
    bundle = _accepted_bundle(tmp_path / "previous-bundle", [previous])
    changed_source = replace(case, source_revision="f" * 40)
    current = _write_run(
        tmp_path / "current-runs",
        case=changed_source,
        versions={**VERSIONS, "codetalk": "3" * 40},
    )

    with pytest.raises(BaselineError, match="source_revision"):
        compare_historical_replay([current], bundle)


def test_history_detects_each_axis_metric_regression_and_retains_identities(
    tmp_path: Path,
) -> None:
    case = CORPUS.cases[0]
    previous = _write_run(tmp_path / "previous-runs", case=case, ratio=0.9)
    bundle = _accepted_bundle(tmp_path / "previous-bundle", [previous])
    current = _write_run(
        tmp_path / "current-runs",
        case=case,
        ratio=0.8,
        versions={
            **VERSIONS,
            "codetalk": "3" * 40,
            "model": "model-2",
        },
    )

    result = compare_historical_replay([current], bundle)

    assert result["status"] == "compared"
    assert {item["axis"] for item in result["regressions"]} == {
        "accuracy",
        "breadth",
        "depth",
    }
    assert all(item["current"] == 0.8 for item in result["regressions"])
    assert result["identity"]["previous"]["codetalk_revision"] == VERSIONS["codetalk"]
    assert result["identity"]["previous"]["model"] == "model-1"
    assert result["identity"]["current"]["codetalk_revision"] == "3" * 40
    assert result["identity"]["current"]["model"] == "model-2"
    serialized = json.dumps(result).lower()
    assert "vote" not in serialized
    assert "consensus" not in serialized


def test_history_rejects_evaluator_change_as_non_comparable(tmp_path: Path) -> None:
    case = CORPUS.cases[0]
    previous = _write_run(tmp_path / "previous-runs", case=case)
    bundle = _accepted_bundle(tmp_path / "previous-bundle", [previous])
    current = _write_run(
        tmp_path / "current-runs",
        case=case,
        versions={**VERSIONS, "evaluator": "quality-evaluation-v2"},
    )
    with pytest.raises(BaselineError, match="evaluator"):
        compare_historical_replay([current], bundle)


def test_history_detects_new_critical_failure_even_when_ratios_do_not_change(
    tmp_path: Path,
) -> None:
    case = CORPUS.cases[0]
    previous = _write_run(tmp_path / "previous-runs", case=case)
    bundle = _accepted_bundle(tmp_path / "previous-bundle", [previous])
    current = _write_run(
        tmp_path / "current-runs",
        case=case,
        critical_axis="depth",
        versions={**VERSIONS, "codetalk": "3" * 40},
    )

    result = compare_historical_replay([current], bundle)

    critical = [
        item
        for item in result["regressions"]
        if item.get("kind") == "critical_failure"
    ]
    assert [(item["phase"], item["axis"]) for item in critical] == [
        ("first_pass", "depth"),
        ("final_after_auto_repair", "depth"),
    ]


def _paired_runs(
    tmp_path: Path,
    *,
    deep_seconds: float = 1200.0,
    deep_versions: dict[str, str] | None = None,
) -> tuple[list[Path], list[Path]]:
    selected = [
        next(case for case in CORPUS.cases if case.domain == domain)
        for domain in ("storage", "bmc", "kv-cache", "rdma-roce")
    ]
    rapid = [
        _write_run(
            tmp_path / "rapid",
            case=case,
            profile="rapid",
            wall_seconds=600.0,
            run_ref=f"rapid-{case.case_id}",
        )
        for case in selected
    ]
    deep = [
        _write_run(
            tmp_path / "deep",
            case=case,
            profile="deep",
            wall_seconds=deep_seconds,
            versions=deep_versions,
            run_ref=f"deep-{case.case_id}",
        )
        for case in selected
    ]
    return rapid, deep


def test_rapid_deep_comparison_is_computed_from_stratified_same_case_pairs(
    tmp_path: Path,
) -> None:
    rapid, deep = _paired_runs(tmp_path)

    comparison = compare_rapid_deep_runs(
        rapid,
        deep,
        corpus=CORPUS,
        evaluation_identity=TEST_IDENTITY,
    )
    matrix = build_regression_matrix(
        release_gate={"release_gate": "pass"},
        historical_replay={"status": "not_run"},
        rapid_deep_comparison=comparison,
    )

    assert comparison.payload["evidence_kind"] == "paired_immutable_reports"
    assert comparison.payload["domains"] == ["bmc", "kv-cache", "rdma-roce", "storage"]
    assert len(comparison.payload["pairs"]) == 4
    assert all(len(pair["rapid"]["report_sha256"]) == 64 for pair in comparison.payload["pairs"])
    assert matrix["rapid_vs_deep"]["status"] == "complete"
    assert matrix["alternative_model"] == {
        "status": "not_run",
        "reason": "alternative_model_unavailable",
    }
    assert matrix["core_baseline_blocked"] is False


def test_regression_matrix_cannot_accept_caller_asserted_status_json() -> None:
    with pytest.raises(TypeError):
        build_regression_matrix(
            default_model_result={"status": "complete"},
            rapid_deep_result={
                "status": "complete",
                "case_ids": ["does-not-exist"],
            },
        )


def test_rapid_deep_pairing_rejects_identity_and_case_set_mismatch(
    tmp_path: Path,
) -> None:
    rapid, deep = _paired_runs(
        tmp_path,
        deep_versions={**VERSIONS, "model": "other-model"},
    )
    with pytest.raises(BaselineError, match="version identity"):
        compare_rapid_deep_runs(
            rapid,
            deep,
            corpus=CORPUS,
            evaluation_identity=TEST_IDENTITY,
        )

    rapid, deep = _paired_runs(tmp_path / "case-set")
    with pytest.raises(BaselineError, match="same-case paired"):
        compare_rapid_deep_runs(
            rapid[:-1],
            deep,
            corpus=CORPUS,
            evaluation_identity=TEST_IDENTITY,
        )


def test_rapid_deep_timing_failure_blocks_matrix(tmp_path: Path) -> None:
    rapid, deep = _paired_runs(tmp_path, deep_seconds=5401.0)
    comparison = compare_rapid_deep_runs(
        rapid,
        deep,
        corpus=CORPUS,
        evaluation_identity=TEST_IDENTITY,
    )

    matrix = build_regression_matrix(
        release_gate={"release_gate": "pass"},
        historical_replay={"status": "not_run"},
        rapid_deep_comparison=comparison,
    )

    assert comparison.payload["timing"]["deep"]["gate"] == "fail"
    assert matrix["core_baseline_blocked"] is True


def test_tampered_previous_bundle_hash_is_rejected(tmp_path: Path) -> None:
    previous = _write_run(tmp_path / "previous-runs", case=CORPUS.cases[0])
    bundle = _accepted_bundle(tmp_path / "previous-bundle", [previous])
    (bundle / "release_gate.json").write_text('{"release_gate":"fail"}\n')

    with pytest.raises(BaselineError, match="hash mismatch"):
        compare_historical_replay([previous], bundle)
