from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from app.services.quality_baseline import (
    BaselineError,
    EvaluationCodeIdentity,
    build_baseline_summary,
    evaluate_release_policy,
    freeze_threshold_policy,
    load_immutable_evaluation,
    render_human_baseline,
    serialize_baseline_data,
)
from app.services.quality_benchmark_corpus import (
    QualityBaselineCaseIdentity,
    QualityCorpusError,
    load_quality_baseline_corpus,
)

REPO_ROOT = Path(__file__).parents[2]
REGISTRY_PATH = REPO_ROOT / "benchmarks" / "quality" / "registry.json"
CORPUS = load_quality_baseline_corpus(REGISTRY_PATH)
FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "quality_evaluation"
    / "valid_independent_benchmark.json"
)
TEST_IDENTITY = EvaluationCodeIdentity(
    repository_root=REPO_ROOT,
    codetalk_revision="1" * 40,
    evaluator_version="quality-evaluation-v1",
    evaluator_sha256="2" * 64,
)
VERSIONS = {
    "codetalk": TEST_IDENTITY.codetalk_revision,
    "evaluator": TEST_IDENTITY.evaluator_version,
    "model": "model-1",
}


def _write_run(
    root: Path,
    *,
    case: QualityBaselineCaseIdentity,
    ratio: float = 1.0,
    profile: str | None = "rapid",
    wall_seconds: float | None = 600.0,
    work_sufficiency: str | None = "not_sampled",
    versions: dict[str, str] | None = None,
    critical_axis: str | None = None,
    run_ref: str | None = None,
) -> Path:
    payload = json.loads(FIXTURE.read_text())
    payload["run_ref"] = run_ref or f"run-{case.case_id}"
    payload["benchmark_identity"] = {
        "case_id": case.case_id,
        "source_revision": case.source_revision,
        "truth_package_version": case.truth_package_version,
    }
    for phase in ("first_pass", "final_after_auto_repair"):
        for axis in ("accuracy", "breadth", "depth"):
            for metric in payload[phase][axis]["metrics"]:
                metric["denominator"] = 100
                metric["numerator"] = int(ratio * 100)
                metric["miss_ids"] = []
            payload[phase][axis]["denominator"] = 100
            payload[phase][axis]["numerator"] = int(ratio * 100)
        if critical_axis is not None:
            payload[phase][critical_axis]["status"] = "fail"
            payload[phase][critical_axis]["critical_misses"] = [
                {
                    "item_id": "critical-1",
                    "reason": "required causal obligation is open",
                    "validation_layer": "L2",
                    "evidence_refs": [],
                }
            ]
    if critical_axis is not None:
        payload["delivery_status"] = "not_ready"

    run_dir = root / case.case_id
    run_dir.mkdir(parents=True)
    report_bytes = (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    human_bytes = f"# Evaluation {case.case_id}\n".encode()
    (run_dir / "quality_evaluation_report.json").write_bytes(report_bytes)
    (run_dir / "quality_evaluation_report.md").write_bytes(human_bytes)
    manifest: dict[str, object] = {
        "schema_version": "quality-evaluation-manifest-v1",
        "run_ref": payload["run_ref"],
        "case_id": case.case_id,
        "project_id": case.project_id,
        "source_revision": case.source_revision,
        "truth_package_version": case.truth_package_version,
        "versions": versions or VERSIONS,
        "environment": {"platform": "test"},
        "report_sha256": hashlib.sha256(report_bytes).hexdigest(),
        "human_report_sha256": hashlib.sha256(human_bytes).hexdigest(),
    }
    if profile is not None:
        diagnostic = {
            "status": "sufficient",
            "cache_reused": False,
            "axis_evidence": {
                "claims": 3,
                "breadth_candidates": 3,
                "breadth_scenarios": 2,
                "depth_nodes": 2,
                "depth_edges": 1,
                "disconfirming_checks": 1,
                "distinct_evidence_refs": 3,
                "provider_invocation_recorded": True,
            },
            "reasons": [],
        }
        manifest["execution"] = {
            "profile": profile,
            "wall_clock_seconds": wall_seconds,
            "generation_wall_clock_seconds": wall_seconds,
            "cache_reuse": False,
            "work_sufficiency": work_sufficiency,
            "work_sufficiency_diagnostic": diagnostic,
            "generator_artifact_root_sha256": "3" * 64,
            "generator_source_tree": case.source_tree,
        }
    (run_dir / "quality_evaluation_manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    )
    return run_dir


def _work_disposition(
    run_ref: str, run_dir: Path, *, sufficient: bool = True
) -> dict[str, object]:
    manifest = json.loads((run_dir / "quality_evaluation_manifest.json").read_text())
    execution = manifest["execution"]
    diagnostic_sha256 = hashlib.sha256(
        json.dumps(
            execution["work_sufficiency_diagnostic"],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "disposition": "sufficient" if sufficient else "insufficient",
        "rationale": "Independent artifact review closed the required work trace.",
        "case_id": manifest["case_id"],
        "report_sha256": manifest["report_sha256"],
        "generator_artifact_root_sha256": execution[
            "generator_artifact_root_sha256"
        ],
        "work_sufficiency_diagnostic_sha256": diagnostic_sha256,
        "cache_reuse": execution["cache_reuse"],
        "author_ids": ["thread:019fc390-ac0a-7ae0-b1d0-769aa3bee986"],
        "reviewer": {
            "reviewer_id": "agent:019fc9a2-4cad-7943-a79d-e3bfe21e0f14",
            "role": "runtime-security-auditor",
            "independent": True,
            "reviewed_at": "2026-08-03T10:00:00Z",
        },
        "evidence_refs": [
            f"artifact-sha256://{manifest['report_sha256']}",
            "artifact-sha256://" + execution["generator_artifact_root_sha256"],
            f"artifact-sha256://{diagnostic_sha256}",
        ],
    }


def _twelve_runs(
    root: Path,
    *,
    versions: dict[str, str] | None = None,
    rapid_limit_override: float | None = None,
    profile_override: str | None = None,
) -> tuple[list[Path], dict[str, object]]:
    runs: list[Path] = []
    work_audit: dict[str, object] = {}
    for index, case in enumerate(CORPUS.cases):
        profile = profile_override or ("rapid" if index % 2 == 0 else "deep")
        wall = 240.0 if index == 0 else (800.0 if profile == "rapid" else 5000.0)
        if rapid_limit_override is not None and profile == "rapid":
            wall = rapid_limit_override
        run_ref = f"core-{case.case_id}"
        run = _write_run(
                root,
                case=case,
                profile=profile,
                wall_seconds=wall,
                work_sufficiency="pending_audit" if wall < 300 else "not_sampled",
                versions=versions,
                run_ref=run_ref,
            )
        runs.append(run)
        if wall < 300:
            work_audit[run_ref] = _work_disposition(run_ref, run)
    return runs, work_audit


def _audit() -> dict[str, object]:
    reviewer_pairs = {
        "false_passes": (
            ("agent:019fc9c3-6cd9-75c2-9cc1-af1d0aa00ea8", "accuracy-auditor"),
            ("agent:019fc9a2-4cad-7943-a79d-e3bfe21e0f14", "runtime-security-auditor"),
        ),
        "false_failures": (
            ("agent:019fc9c3-6cd9-75c2-9cc1-af1d0aa00ea8", "accuracy-auditor"),
            ("agent:019fc9c3-6e3c-7f70-949b-21833175b36c", "breadth-auditor"),
        ),
        "missing_denominators": (
            ("agent:019fc9c3-6e3c-7f70-949b-21833175b36c", "breadth-auditor"),
            ("agent:019fc9c3-6f91-7a22-9875-caab929e744b", "depth-auditor"),
        ),
        "unstable_evaluator": (
            ("agent:019fc9c3-6f91-7a22-9875-caab929e744b", "depth-auditor"),
            ("agent:019fc9a2-4cad-7943-a79d-e3bfe21e0f14", "runtime-security-auditor"),
        ),
    }
    result = {
        name: {
            "status": "approved",
            "threshold_rationale": (
                f"The {name} mutation set and raw distributions support these thresholds."
            ),
            "evidence_refs": [f"test-evidence://calibration/{name}"],
            "reviewers": [
                {
                    "reviewer_id": reviewer_pairs[name][0][0],
                    "role": reviewer_pairs[name][0][1],
                    "independent": True,
                    "decision": "approve",
                    "reviewed_at": "2026-08-03T10:00:00Z",
                    "evidence_refs": [f"test-review://a/{name}"],
                },
                {
                    "reviewer_id": reviewer_pairs[name][1][0],
                    "role": reviewer_pairs[name][1][1],
                    "independent": True,
                    "decision": "approve",
                    "reviewed_at": "2026-08-03T10:05:00+00:00",
                    "evidence_refs": [f"test-review://b/{name}"],
                },
            ],
            "items": [],
        }
        for name in (
            "false_passes",
            "false_failures",
            "missing_denominators",
            "unstable_evaluator",
        )
    }
    result["author_ids"] = ["thread:019fc390-ac0a-7ae0-b1d0-769aa3bee986"]
    return result


def _thresholds(value: float = 1.0) -> dict[str, dict[str, float]]:
    return {
        "accuracy": {"claim_precision": value, "gold_recall": value},
        "breadth": {
            "discovery_recall": value,
            "critical_coverage": value,
            "scenario_realization": value,
            "disposition_completeness": value,
        },
        "depth": {
            "minimum_critical_chain_closure": value,
            "average_chain_closure": value,
            "state_closure": value,
            "resource_lifecycle_closure": value,
            "error_recovery_closure": value,
            "disconfirming_checks": value,
        },
    }


def _summary(tmp_path: Path) -> dict[str, object]:
    runs, work_audit = _twelve_runs(tmp_path)
    return build_baseline_summary(
        runs,
        corpus=CORPUS,
        evaluation_identity=TEST_IDENTITY,
        work_sufficiency_audit=work_audit,
    )


def test_formal_corpus_identity_binds_registry_case_and_truth_hashes() -> None:
    assert len(CORPUS.cases) == 12
    assert {case.domain for case in CORPUS.cases} == {
        "storage",
        "bmc",
        "kv-cache",
        "rdma-roce",
    }
    assert CORPUS.registry_sha256 == hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()
    assert all(len(case.case_sha256) == 64 for case in CORPUS.cases)
    assert all(len(case.truth_sha256) == 4 for case in CORPUS.cases)


def test_baseline_summary_preserves_per_domain_l3_status_and_limitations(
    tmp_path: Path,
) -> None:
    summary = _summary(tmp_path)

    l3 = summary["validation_layers"]["L3"]
    assert set(l3["domains"]) == {"storage", "bmc", "kv-cache", "rdma-roce"}
    for domain in l3["domains"].values():
        for axis in ("accuracy", "breadth", "depth"):
            final = domain[axis]["final"]
            assert sum(final["status_counts"].values()) > 0
            assert final["samples"]
            assert all("limitations" in sample for sample in final["samples"])


def test_formal_corpus_rejects_a_symlinked_registry(tmp_path: Path) -> None:
    linked = tmp_path / "registry.json"
    linked.symlink_to(REGISTRY_PATH)

    with pytest.raises(QualityCorpusError, match="non-symlink"):
        load_quality_baseline_corpus(linked)


def test_load_rejects_report_hash_case_and_evaluator_identity_mismatch(
    tmp_path: Path,
) -> None:
    case = CORPUS.cases[0]
    run = _write_run(tmp_path, case=case)
    manifest_path = run / "quality_evaluation_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["report_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(BaselineError, match="report hash"):
        load_immutable_evaluation(run, expected_case=case, expected_identity=TEST_IDENTITY)

    manifest["report_sha256"] = hashlib.sha256(
        (run / "quality_evaluation_report.json").read_bytes()
    ).hexdigest()
    manifest["versions"]["evaluator"] = "other"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(BaselineError, match="evaluator"):
        load_immutable_evaluation(run, expected_case=case, expected_identity=TEST_IDENTITY)


def test_summary_emits_authoritative_identity_distributions_and_timing(
    tmp_path: Path,
) -> None:
    runs, work_audit = _twelve_runs(tmp_path)

    summary = build_baseline_summary(
        runs,
        corpus=CORPUS,
        evaluation_identity=TEST_IDENTITY,
        work_sufficiency_audit=work_audit,
    )

    assert summary["coverage"] == {"expected": 12, "observed": 12, "missing_case_ids": []}
    assert summary["identity"]["corpus"]["registry_sha256"] == CORPUS.registry_sha256
    assert summary["identity"]["corpus"]["corpus_sha256"] == CORPUS.corpus_sha256
    assert summary["identity"]["evaluation"] == TEST_IDENTITY.as_dict()
    assert summary["domains"]["storage"]["accuracy"]["claim_precision"]["first"]["count"] == 4
    assert summary["timing"]["rapid"]["p100_seconds"] == 800.0
    assert summary["timing"]["deep"]["p100_seconds"] == 5000.0
    assert summary["timing"]["work_sufficiency_gate"] == "pass"
    under_five = summary["timing"]["under_five_minute_samples"]
    assert len(under_five) == 1
    assert under_five[0]["independent_disposition"]["gate"] == "pass"
    assert "aggregate" not in json.dumps(summary).lower()
    assert render_human_baseline(summary)["domains"][0]["domain"] == "bmc"


def test_baseline_summary_requires_execution_for_every_case(tmp_path: Path) -> None:
    runs, work_audit = _twelve_runs(tmp_path)
    manifest_path = runs[-1] / "quality_evaluation_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.pop("execution")
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(BaselineError, match="execution"):
        build_baseline_summary(
            runs,
            corpus=CORPUS,
            evaluation_identity=TEST_IDENTITY,
            work_sufficiency_audit=work_audit,
        )


def test_under_five_minute_run_without_independent_disposition_blocks_release(
    tmp_path: Path,
) -> None:
    runs, _ = _twelve_runs(tmp_path)
    summary = build_baseline_summary(
        runs,
        corpus=CORPUS,
        evaluation_identity=TEST_IDENTITY,
        work_sufficiency_audit={},
    )
    policy = freeze_threshold_policy(
        summary, thresholds=_thresholds(), calibration_audit=_audit()
    )

    result = evaluate_release_policy(summary, policy)

    assert summary["timing"]["work_sufficiency_gate"] == "fail"
    assert result["timing"]["work_sufficiency"] == "fail"
    assert result["release_gate"] == "fail"


def test_under_five_minute_generic_attestation_cannot_replace_bound_artifacts(
    tmp_path: Path,
) -> None:
    runs, _ = _twelve_runs(tmp_path)
    run_ref = f"core-{CORPUS.cases[0].case_id}"
    weak = {
        run_ref: {
            "disposition": "sufficient",
            "rationale": "Looks complete.",
            "reviewer": {
                "reviewer_id": "artifact-author",
                "independent": True,
                "reviewed_at": "2026-08-03T10:00:00Z",
            },
            "evidence_refs": ["probe://generic"],
        }
    }

    summary = build_baseline_summary(
        runs,
        corpus=CORPUS,
        evaluation_identity=TEST_IDENTITY,
        work_sufficiency_audit=weak,
    )

    disposition = summary["timing"]["under_five_minute_samples"][0][
        "independent_disposition"
    ]
    assert disposition["gate"] == "fail"
    assert "report_sha256" in disposition["reason"]


def test_under_five_cached_reuse_accepts_matching_reuse_diagnostic(
    tmp_path: Path,
) -> None:
    runs, _ = _twelve_runs(tmp_path)
    run = runs[0]
    manifest_path = run / "quality_evaluation_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["execution"]["cache_reuse"] = True
    manifest["execution"]["generator_response_sha256"] = "4" * 64
    manifest["execution"]["work_sufficiency"] = "reused"
    manifest["execution"]["work_sufficiency_diagnostic"] = {
        "status": "reused",
        "cache_reused": True,
        "reuse_source_sha256": "4" * 64,
        "reasons": [],
    }
    manifest_path.write_text(json.dumps(manifest))
    run_ref = str(manifest["run_ref"])

    summary = build_baseline_summary(
        runs,
        corpus=CORPUS,
        evaluation_identity=TEST_IDENTITY,
        work_sufficiency_audit={run_ref: _work_disposition(run_ref, run)},
    )

    disposition = summary["timing"]["under_five_minute_samples"][0][
        "independent_disposition"
    ]
    assert disposition["gate"] == "pass"
    assert disposition["cache_reuse"] is True


def test_under_five_cached_reuse_rejects_unbound_or_cold_reuse_hash(
    tmp_path: Path,
) -> None:
    runs, _ = _twelve_runs(tmp_path)
    run = runs[0]
    manifest_path = run / "quality_evaluation_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    diagnostic = manifest["execution"]["work_sufficiency_diagnostic"]
    diagnostic["reuse_source_sha256"] = "4" * 64
    manifest_path.write_text(json.dumps(manifest))
    run_ref = str(manifest["run_ref"])

    cold = build_baseline_summary(
        runs,
        corpus=CORPUS,
        evaluation_identity=TEST_IDENTITY,
        work_sufficiency_audit={run_ref: _work_disposition(run_ref, run)},
    )
    cold_disposition = cold["timing"]["under_five_minute_samples"][0][
        "independent_disposition"
    ]
    assert cold_disposition["gate"] == "fail"
    assert "cold" in cold_disposition["reason"]

    manifest["execution"]["cache_reuse"] = True
    manifest["execution"]["work_sufficiency"] = "reused"
    diagnostic.update({"status": "reused", "cache_reused": True})
    manifest["execution"]["generator_response_sha256"] = "5" * 64
    manifest_path.write_text(json.dumps(manifest))
    cached = build_baseline_summary(
        runs,
        corpus=CORPUS,
        evaluation_identity=TEST_IDENTITY,
        work_sufficiency_audit={run_ref: _work_disposition(run_ref, run)},
    )
    cached_disposition = cached["timing"]["under_five_minute_samples"][0][
        "independent_disposition"
    ]
    assert cached_disposition["gate"] == "fail"
    assert "generator response" in cached_disposition["reason"]


def test_invalid_metric_denominator_is_rejected_at_load(tmp_path: Path) -> None:
    case = CORPUS.cases[0]
    run = _write_run(tmp_path, case=case)
    report_path = run / "quality_evaluation_report.json"
    report = json.loads(report_path.read_text())
    report["first_pass"]["accuracy"]["metrics"][0]["denominator"] = 0
    report_bytes = (
        json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    report_path.write_bytes(report_bytes)
    manifest_path = run / "quality_evaluation_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["report_sha256"] = hashlib.sha256(report_bytes).hexdigest()
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(BaselineError, match="invalid quality evaluation report"):
        load_immutable_evaluation(run, expected_case=case, expected_identity=TEST_IDENTITY)


def test_summary_retains_status_and_critical_misses_as_independent_gates(
    tmp_path: Path,
) -> None:
    runs, work_audit = _twelve_runs(tmp_path)
    failed_case = CORPUS.cases[0]
    for path in runs:
        if path.name == failed_case.case_id:
            for child in path.iterdir():
                child.unlink()
            path.rmdir()
            runs.remove(path)
            break
    runs.append(
        _write_run(
            tmp_path,
            case=failed_case,
            profile="rapid",
            wall_seconds=800,
            critical_axis="depth",
            run_ref=f"core-{failed_case.case_id}",
        )
    )

    summary = build_baseline_summary(
        runs,
        corpus=CORPUS,
        evaluation_identity=TEST_IDENTITY,
        work_sufficiency_audit=work_audit,
    )

    assert summary["critical_failures"]["depth"] == 1
    assert summary["final_outcomes"][failed_case.case_id] == {
        "delivery_status": "not_ready",
        "axes": {"accuracy": "limited", "breadth": "limited", "depth": "fail"},
    }


def test_threshold_freeze_requires_complete_formal_corpus(tmp_path: Path) -> None:
    runs, work_audit = _twelve_runs(tmp_path)
    summary = build_baseline_summary(
        runs[:-1],
        corpus=CORPUS,
        evaluation_identity=TEST_IDENTITY,
        work_sufficiency_audit=work_audit,
    )
    with pytest.raises(BaselineError, match="complete 12-case corpus"):
        freeze_threshold_policy(
            summary, thresholds=_thresholds(), calibration_audit=_audit()
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("one-reviewer", "two independent reviewers"),
        ("not-independent", "declare independence"),
        ("no-evidence", "evidence references"),
        ("bad-timestamp", "ISO-8601"),
    ],
)
def test_threshold_freeze_requires_evidenced_joint_independent_approval(
    tmp_path: Path, mutation: str, message: str
) -> None:
    summary = _summary(tmp_path)
    audit = _audit()
    category = audit["false_passes"]
    if mutation == "one-reviewer":
        category["reviewers"] = category["reviewers"][:1]
    elif mutation == "not-independent":
        category["reviewers"][0]["independent"] = False
    elif mutation == "no-evidence":
        category["evidence_refs"] = []
    else:
        category["reviewers"][0]["reviewed_at"] = "yesterday"

    with pytest.raises(BaselineError, match=message):
        freeze_threshold_policy(
            summary, thresholds=_thresholds(), calibration_audit=audit
        )


def test_calibration_reviewer_cannot_be_an_author(tmp_path: Path) -> None:
    summary = _summary(tmp_path)
    audit = _audit()
    audit["author_ids"] = ["agent:019fc9c3-6cd9-75c2-9cc1-af1d0aa00ea8"]

    with pytest.raises(BaselineError, match="reviewer.*author"):
        freeze_threshold_policy(
            summary, thresholds=_thresholds(), calibration_audit=audit
        )


def test_threshold_freeze_requires_final_finding_dispositions(tmp_path: Path) -> None:
    summary = _summary(tmp_path)
    audit = _audit()
    audit["false_failures"]["items"] = [
        {
            "id": "finding-1",
            "disposition": "open",
            "rationale": "Still open.",
            "evidence_refs": ["test-evidence://finding-1"],
        }
    ]
    with pytest.raises(BaselineError, match="unresolved"):
        freeze_threshold_policy(
            summary, thresholds=_thresholds(), calibration_audit=audit
        )


def test_false_pass_finding_cannot_be_accepted_as_a_limitation(tmp_path: Path) -> None:
    summary = _summary(tmp_path)
    audit = _audit()
    audit["false_passes"]["items"] = [
        {
            "id": "known-false-pass",
            "disposition": "accepted_limitation",
            "rationale": "Known false positives are not release-safe.",
            "evidence_refs": ["test-evidence://known-false-pass"],
        }
    ]

    with pytest.raises(BaselineError, match="false_passes.*resolved"):
        freeze_threshold_policy(
            summary, thresholds=_thresholds(), calibration_audit=audit
        )


def test_thresholds_must_be_deterministically_derived_from_final_distributions(
    tmp_path: Path,
) -> None:
    summary = _summary(tmp_path)

    with pytest.raises(BaselineError, match="derived final distributions"):
        freeze_threshold_policy(
            summary, thresholds=_thresholds(0.8), calibration_audit=_audit()
        )


def test_threshold_policy_schema_versions_calibration_boundary_contract(
    tmp_path: Path,
) -> None:
    summary = _summary(tmp_path)

    policy = freeze_threshold_policy(
        summary, thresholds=_thresholds(), calibration_audit=_audit()
    )

    assert policy["schema_version"] == "quality-threshold-policy-v3"
    assert policy["threshold_derivation"]["schema_version"] == (
        "quality-threshold-derivation-v2"
    )
    assert policy["calibration_gate"] == "pass"
    assert len(policy["review_authority_sha256"]) == 64


def test_calibration_rejects_self_declared_reviewer_identity_and_role(
    tmp_path: Path,
) -> None:
    summary = _summary(tmp_path)
    audit = _audit()
    audit["false_passes"]["reviewers"][0].update(
        {
            "reviewer_id": "declared-other-author",
            "role": "implementation-author",
        }
    )

    with pytest.raises(BaselineError, match="review authority"):
        freeze_threshold_policy(
            summary, thresholds=_thresholds(), calibration_audit=audit
        )


def test_observed_minimum_cannot_self_approve_without_mutation_separation(
    tmp_path: Path,
) -> None:
    runs = [
        _write_run(
            tmp_path,
            case=case,
            ratio=0.0 if index == 0 else 1.0,
            profile="rapid",
            wall_seconds=800.0,
            run_ref=f"core-{case.case_id}",
        )
        for index, case in enumerate(CORPUS.cases)
    ]
    summary = build_baseline_summary(
        runs,
        corpus=CORPUS,
        evaluation_identity=TEST_IDENTITY,
        work_sufficiency_audit={},
    )
    policy = freeze_threshold_policy(
        summary, thresholds=_thresholds(1.0), calibration_audit=_audit()
    )

    result = evaluate_release_policy(summary, policy)

    assert policy["calibration_gate"] == "pass"
    assert result["calibration_gate"] == "pass"
    assert result["release_gate"] == "fail"
    metric = policy["threshold_derivation"]["metrics"]["accuracy"][
        "claim_precision"
    ]
    assert metric["status"] == "calibrated"
    assert metric["observed_final_minimum"] == 0.0
    assert metric["unacceptable_maximum"] < metric["acceptable_minimum"]
    assert metric["mutation_replay"]["mutated_axis_status"] == "fail"


def test_core_release_allows_unobserved_deep_profile(tmp_path: Path) -> None:
    runs = [
        _write_run(
            tmp_path,
            case=case,
            profile="rapid",
            wall_seconds=800.0,
            run_ref=f"core-{case.case_id}",
        )
        for case in CORPUS.cases
    ]
    summary = build_baseline_summary(
        runs,
        corpus=CORPUS,
        evaluation_identity=TEST_IDENTITY,
        work_sufficiency_audit={},
    )
    policy = freeze_threshold_policy(
        summary, thresholds=_thresholds(), calibration_audit=_audit()
    )

    result = evaluate_release_policy(summary, policy)

    assert result["timing"] == {
        "rapid": "pass",
        "deep": "not_run",
        "work_sufficiency": "pass",
    }
    assert result["release_gate"] == "pass"


def test_release_policy_gates_metrics_status_delivery_and_critical_independently(
    tmp_path: Path,
) -> None:
    summary = _summary(tmp_path)
    policy = freeze_threshold_policy(
        summary, thresholds=_thresholds(), calibration_audit=_audit()
    )
    degraded = copy.deepcopy(summary)
    degraded["domains"]["storage"]["breadth"]["critical_coverage"]["final"]["minimum"] = 0.7
    degraded["critical_failures"]["depth"] = 1
    case_id = CORPUS.cases[0].case_id
    degraded["final_outcomes"][case_id]["axes"]["accuracy"] = "fail"
    degraded["final_outcomes"][case_id]["delivery_status"] = "not_ready"

    result = evaluate_release_policy(degraded, policy)

    assert result["axes"]["accuracy"]["status_gate"] == "fail"
    assert result["axes"]["breadth"]["metric_gate"] == "fail"
    assert result["axes"]["depth"]["critical_gate"] == "fail"
    assert result["delivery_gate"] == "fail"
    assert result["release_gate"] == "fail"
    assert "score" not in json.dumps(result).lower()


def test_failed_final_axis_never_passes_with_derived_thresholds(tmp_path: Path) -> None:
    summary = _summary(tmp_path)
    policy = freeze_threshold_policy(
        summary, thresholds=_thresholds(), calibration_audit=_audit()
    )
    failed = copy.deepcopy(summary)
    for outcome in failed["final_outcomes"].values():
        outcome["delivery_status"] = "not_ready"
        outcome["axes"] = {axis: "fail" for axis in ("accuracy", "breadth", "depth")}

    result = evaluate_release_policy(failed, policy)

    assert result["release_gate"] == "fail"
    assert all(
        result["axes"][axis]["status_gate"] == "fail"
        for axis in ("accuracy", "breadth", "depth")
    )
    assert result["delivery_gate"] == "fail"


def test_policy_rejects_combined_or_incomplete_thresholds(tmp_path: Path) -> None:
    summary = _summary(tmp_path)
    thresholds = _thresholds()
    thresholds["aggregate"] = {"weighted": 0.8}
    with pytest.raises(BaselineError, match="three independent axes"):
        freeze_threshold_policy(
            summary, thresholds=thresholds, calibration_audit=_audit()
        )


def test_machine_output_serialization_is_canonical() -> None:
    assert serialize_baseline_data({"z": 1, "a": {"y": 2, "b": 3}}) == (
        '{"a":{"b":3,"y":2},"z":1}\n'
    )
