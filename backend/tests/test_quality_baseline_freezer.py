from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from app.services.quality_baseline import (
    EVALUATOR_SOURCE_PATHS,
    BaselineError,
    compare_historical_replay,
    load_clean_evaluation_identity,
)
from app.services.quality_baseline_freezer import (
    freeze_baseline_output,
    freeze_blocked_baseline_output,
    main,
)
from app.services.quality_benchmark_corpus import QualityCorpusError
from tests.test_quality_baseline_policy import (
    CORPUS,
    REGISTRY_PATH,
    _audit,
    _thresholds,
    _twelve_runs,
    _work_disposition,
    _write_run,
)


def _run_git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _clean_repository(root: Path) -> tuple[Path, dict[str, str]]:
    root.mkdir(parents=True)
    for index, relative in enumerate(EVALUATOR_SOURCE_PATHS):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# test evaluator source {index}\n", encoding="utf-8")
    shutil.copytree(
        REGISTRY_PATH.parent,
        root / "benchmarks" / "quality",
    )
    _run_git(root, "init", "-q")
    _run_git(root, "config", "user.name", "Quality Test")
    _run_git(root, "config", "user.email", "quality-test@example.invalid")
    _run_git(root, "add", ".")
    _run_git(root, "-c", "commit.gpgsign=false", "commit", "-qm", "test identity")
    identity = load_clean_evaluation_identity(root)
    return root, {
        "model": "model-1",
        "codetalk": identity.codetalk_revision,
        "evaluator": identity.evaluator_version,
    }


def _write_generator(root: Path, run: Path) -> Path:
    manifest = json.loads((run / "quality_evaluation_manifest.json").read_text())
    report = json.loads((run / "quality_evaluation_report.json").read_text())
    generator = root / str(manifest["case_id"])
    response_bytes = (
        json.dumps(
            {"case_id": manifest["case_id"], "source": "retained-generator-response"},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    response_sha256 = hashlib.sha256(response_bytes).hexdigest()
    first = generator / "first_pass"
    final = generator / "final_after_auto_repair"
    first.mkdir(parents=True)
    final.mkdir()
    (generator / "benchmark_response.json").write_bytes(response_bytes)
    (first / "candidate.json").write_text(
        json.dumps({"phase": "first", "case_id": manifest["case_id"]}) + "\n"
    )
    (final / "candidate.json").write_text(
        json.dumps({"phase": "final", "case_id": manifest["case_id"]}) + "\n"
    )
    (generator / "repair_summary.json").write_text(
        json.dumps(report["repair_summary"], sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    (generator / "versions.json").write_text(
        json.dumps(manifest["versions"], sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    execution = manifest["execution"]
    (generator / "generation_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "quality-benchmark-generation-v1",
                "case_id": manifest["case_id"],
                "mode": execution["profile"],
                "model": manifest["versions"]["model"],
                "codetalk_revision": manifest["versions"]["codetalk"],
                "source_tree": next(
                    case.source_tree
                    for case in CORPUS.cases
                    if case.case_id == manifest["case_id"]
                ),
                "elapsed_seconds": execution["generation_wall_clock_seconds"],
                "response_sha256": response_sha256,
                "artifact_hash_manifest": "artifact_hash_manifest.json",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    (generator / "workbench_audit.json").write_text(
        json.dumps(
            {
                "schema_version": "quality-benchmark-workbench-audit-v1",
                "task_artifact_hashes": {
                    "benchmark_response.json": response_sha256
                },
            }
        )
        + "\n"
    )
    manifest["execution"]["generator_response_sha256"] = response_sha256
    root_sha = _rewrite_generator_hash_manifest(generator)
    manifest["execution"]["generator_artifact_root_sha256"] = root_sha
    (run / "quality_evaluation_manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    )
    return generator


def _rewrite_generator_hash_manifest(
    generator: Path, *, legacy: bool = False
) -> str:
    artifacts: dict[str, dict[str, object]] = {}
    paths = (
        [
            path
            for root_name in ("first_pass", "final_after_auto_repair")
            for path in (generator / root_name).rglob("*")
        ]
        if legacy
        else list(generator.rglob("*"))
    )
    for path in sorted(paths):
        relative = path.relative_to(generator).as_posix()
        if not path.is_file() or relative == "artifact_hash_manifest.json":
            continue
        data = path.read_bytes()
        artifacts[relative] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }
    canonical = json.dumps(
        artifacts, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode()
    root_sha = hashlib.sha256(canonical).hexdigest()
    (generator / "artifact_hash_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "quality-benchmark-artifact-hashes-v1",
                "artifacts": artifacts,
                "root_sha256": root_sha,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    return root_sha


def _generators(root: Path, runs: list[Path]) -> list[Path]:
    return [_write_generator(root, run) for run in runs]


def _paired_evidence(
    root: Path, versions: dict[str, str]
) -> tuple[list[Path], list[Path], list[Path], list[Path]]:
    cases = [
        next(case for case in CORPUS.cases if case.domain == domain)
        for domain in ("storage", "bmc", "kv-cache", "rdma-roce")
    ]
    rapid_runs = [
        _write_run(
            root / "rapid-runs",
            case=case,
            profile="rapid",
            wall_seconds=600.0,
            versions=versions,
            run_ref=f"rapid-{case.case_id}",
        )
        for case in cases
    ]
    deep_runs = [
        _write_run(
            root / "deep-runs",
            case=case,
            profile="deep",
            wall_seconds=1200.0,
            versions=versions,
            run_ref=f"deep-{case.case_id}",
        )
        for case in cases
    ]
    return (
        rapid_runs,
        _generators(root / "rapid-generators", rapid_runs),
        deep_runs,
        _generators(root / "deep-generators", deep_runs),
    )


def _evidence_fixture(
    tmp_path: Path,
    *,
    rapid_limit_override: float | None = None,
    core_profile: str | None = None,
) -> dict[str, object]:
    repository, versions = _clean_repository(tmp_path / "repository")
    evidence = tmp_path / "evidence"
    runs, work_audit = _twelve_runs(
        evidence / "core-runs",
        versions=versions,
        rapid_limit_override=rapid_limit_override,
        profile_override=core_profile,
    )
    generators = _generators(evidence / "core-generators", runs)
    for run in runs:
        manifest = json.loads((run / "quality_evaluation_manifest.json").read_text())
        if float(manifest["execution"]["wall_clock_seconds"]) < 300.0:
            run_ref = str(manifest["run_ref"])
            work_audit[run_ref] = _work_disposition(run_ref, run)
    rapid_runs, rapid_generators, deep_runs, deep_generators = _paired_evidence(
        evidence, versions
    )
    review_evidence = evidence / "independent-review.md"
    review_evidence.write_text(
        "Independent R1-R4 calibration review evidence.\n", encoding="utf-8"
    )
    return {
        "repository": repository,
        "registry": repository / "benchmarks" / "quality" / "registry.json",
        "versions": versions,
        "evidence_root": evidence,
        "runs": runs,
        "generators": generators,
        "rapid_runs": rapid_runs,
        "rapid_generators": rapid_generators,
        "deep_runs": deep_runs,
        "deep_generators": deep_generators,
        "work_audit": work_audit,
        "review_evidence": [review_evidence],
    }


def _bound_audit(fixture: dict[str, object]) -> dict[str, object]:
    evidence_path = Path(fixture["review_evidence"][0])
    ref = (
        "bundle-review-evidence://sha256/"
        + hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    )
    audit = json.loads(json.dumps(_audit()))

    def bind(value: object) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if key == "evidence_refs" and isinstance(nested, list):
                    value[key] = [ref]
                else:
                    bind(nested)
        elif isinstance(value, list):
            for nested in value:
                bind(nested)

    bind(audit)
    return audit


def _freeze(
    fixture: dict[str, object],
    output: Path,
    *,
    registry_path: Path | None = None,
) -> Path:
    return freeze_baseline_output(
        run_directories=fixture["runs"],
        generator_directories=fixture["generators"],
        registry_path=registry_path or fixture["registry"],
        repository_root=fixture["repository"],
        thresholds=_thresholds(),
        calibration_audit=_bound_audit(fixture),
        review_evidence_files=fixture["review_evidence"],
        work_sufficiency_audit=fixture["work_audit"],
        rapid_run_directories=fixture["rapid_runs"],
        rapid_generator_directories=fixture["rapid_generators"],
        deep_run_directories=fixture["deep_runs"],
        deep_generator_directories=fixture["deep_generators"],
        output_directory=output,
    )


def _blocked_fixture(tmp_path: Path) -> dict[str, object]:
    fixture = _evidence_fixture(tmp_path)
    blocked_case_ids = {
        "mooncake-store-put-commit-readiness-recovery-001",
        "spdk-concurrent-bdev-reset-001",
    }
    runs = [
        Path(path)
        for path in fixture["runs"]
        if Path(path).name not in blocked_case_ids
    ]
    generators = [
        Path(path)
        for path in fixture["generators"]
        if Path(path).name not in blocked_case_ids
    ]
    failures: list[Path] = []
    for case_id in sorted(blocked_case_ids):
        case = CORPUS.case_map[case_id]
        failure = tmp_path / "evidence" / "generation-failures" / case_id
        failure.mkdir(parents=True)
        payload = {
            "schema_version": "quality-benchmark-generator-v1",
            "case_id": case_id,
            "mode": "rapid",
            "model": fixture["versions"]["model"],
            "codetalk_revision": fixture["versions"]["codetalk"],
            "source_tree": case.source_tree,
            "elapsed_seconds": 266.0,
            "timeout_seconds": 899,
            "status": "quality_blocked",
            "failure_code": "workbench_quality_blocked",
            "truth_inputs": [],
        }
        (failure / "generation_failure.json").write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        repair_projection = {
            "attempted_count": 1,
            "accepted_count": 0,
            "last_accepted_attempt": 0,
            "stopped_reason": "no_quality_progress",
            "remaining_seconds": 600.0,
            "outcomes": [
                {
                    "attempt": 1,
                    "accepted": False,
                    "status_before": "needs_rework",
                    "status_after": "needs_rework",
                    "issues_before": 1,
                    "issues_after": 1,
                }
            ],
        }
        raw_repair = {
            "enabled": True,
            "attempt_count": 1,
            "attempts": [
                {
                    "attempt": 1,
                    "accepted": False,
                    "status_before": "needs_rework",
                    "status_after": "needs_rework",
                    "issues_before": 1,
                    "issues_after": 1,
                }
            ],
            "total_budget_seconds": 900.0,
            "remaining_seconds": 600.0,
            "stopped_reason": "no_quality_progress",
        }
        raw_repair_bytes = (
            json.dumps(raw_repair, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        raw_repair_sha256 = hashlib.sha256(raw_repair_bytes).hexdigest()
        (failure / "workbench_audit.json").write_text(
            json.dumps(
                {
                    "schema_version": "quality-benchmark-workbench-audit-v1",
                    "workbench_status": "quality_blocked",
                    "repair_attempt_count": 1,
                    "accepted_response_attempt": 0,
                    "repair_audit": repair_projection,
                    "task_artifact_hashes": {
                        "quality_repair_result.json": raw_repair_sha256,
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        (failure / "repair_trace.json").write_text(
            json.dumps(
                {
                    "schema_version": "quality-benchmark-repair-trace-v1",
                    "source_sha256": raw_repair_sha256,
                    "projection": repair_projection,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        (failure / "repair_trace_source.json").write_bytes(raw_repair_bytes)
        _rewrite_generator_hash_manifest(failure)
        failures.append(failure)
    fixture["runs"] = runs
    fixture["generators"] = generators
    fixture["failures"] = failures
    return fixture


def _freeze_blocked(fixture: dict[str, object], output: Path) -> Path:
    return freeze_blocked_baseline_output(
        run_directories=fixture["runs"],
        generator_directories=fixture["generators"],
        failure_directories=fixture["failures"],
        registry_path=fixture["registry"],
        repository_root=fixture["repository"],
        review_evidence_files=fixture["review_evidence"],
        work_sufficiency_audit=fixture["work_audit"],
        rapid_run_directories=fixture["rapid_runs"],
        rapid_generator_directories=fixture["rapid_generators"],
        deep_run_directories=fixture["deep_runs"],
        deep_generator_directories=fixture["deep_generators"],
        output_directory=output,
    )


def test_blocked_freezer_retains_complete_observation_without_freezing_thresholds(
    tmp_path: Path,
) -> None:
    fixture = _blocked_fixture(tmp_path)

    output = _freeze_blocked(fixture, tmp_path / "blocked-baseline")

    observation = json.loads((output / "baseline_observation.json").read_text())
    assert observation["coverage"] == {
        "expected": 12,
        "attempted": 12,
        "evaluated": 10,
        "generation_failed": 2,
        "missing_attempt_case_ids": [],
        "missing_evaluation_case_ids": [
            "mooncake-store-put-commit-readiness-recovery-001",
            "spdk-concurrent-bdev-reset-001",
        ],
    }
    assert set(observation["generation_failures"]) == {
        "mooncake-store-put-commit-readiness-recovery-001",
        "spdk-concurrent-bdev-reset-001",
    }
    assert not (output / "threshold_policy.json").exists()
    policy = json.loads((output / "threshold_freeze_status.json").read_text())
    assert policy == {
        "schema_version": "quality-threshold-policy-not-frozen-v1",
        "status": "not_frozen",
        "reason": "complete_evaluable_corpus_unavailable",
        "evaluated_case_count": 10,
        "expected_case_count": 12,
        "missing_evaluation_case_ids": [
            "mooncake-store-put-commit-readiness-recovery-001",
            "spdk-concurrent-bdev-reset-001",
        ],
    }
    release = json.loads((output / "release_gate.json").read_text())
    assert release["release_gate"] == "fail"
    assert release["release_status"] == "blocked"
    assert release["block_reasons"] == [
        "generation_failures_present",
        "thresholds_not_frozen",
    ]
    assert len(list((output / "runs").glob("*/evaluation/quality_evaluation_report.json"))) == 10
    assert len(
        list(
            (output / "generation_failures").glob(
                "*/generator/generation_failure.json"
            )
        )
    ) == 2
    manifest = json.loads((output / "baseline_manifest.json").read_text())
    assert manifest["bundle_status"] == "blocked"
    assert set(manifest["source_generation_failure_sha256"]) == set(
        observation["generation_failures"]
    )
    freezer_identity = manifest["freezer_identity"]
    assert len(freezer_identity["implementation_sha256"]) == 64
    for relative, digest in freezer_identity["source_sha256"].items():
        retained = output / "freezer_implementation" / relative
        assert hashlib.sha256(retained.read_bytes()).hexdigest() == digest
    assert observation["freezer_identity"] == freezer_identity
    regression = json.loads((output / "regression_matrix.json").read_text())
    assert regression["core_baseline_blocked"] is True


def test_blocked_freezer_rejects_tampered_generation_failure(tmp_path: Path) -> None:
    fixture = _blocked_fixture(tmp_path)
    failure = Path(fixture["failures"][0])
    payload = json.loads((failure / "generation_failure.json").read_text())
    payload["source_tree"] = "0" * 40
    (failure / "generation_failure.json").write_text(json.dumps(payload))

    with pytest.raises(BaselineError, match="generation failure artifact hash mismatch"):
        _freeze_blocked(fixture, tmp_path / "tampered-blocked-baseline")


def test_blocked_freezer_rejects_missing_case_observation(tmp_path: Path) -> None:
    fixture = _blocked_fixture(tmp_path)
    fixture["failures"] = fixture["failures"][:-1]

    with pytest.raises(BaselineError, match="complete 12-case observation coverage"):
        _freeze_blocked(fixture, tmp_path / "incomplete-blocked-baseline")


def test_blocked_freezer_requires_audit_for_quality_blocked_failure(
    tmp_path: Path,
) -> None:
    fixture = _blocked_fixture(tmp_path)
    failure = Path(fixture["failures"][0])
    (failure / "workbench_audit.json").unlink()
    _rewrite_generator_hash_manifest(failure)

    with pytest.raises(BaselineError, match="quality-blocked failure requires"):
        _freeze_blocked(fixture, tmp_path / "missing-workbench-audit")


def test_blocked_freezer_discloses_legacy_missing_repair_attempt_audit(
    tmp_path: Path,
) -> None:
    fixture = _blocked_fixture(tmp_path)
    failure = Path(fixture["failures"][0])
    workbench_path = failure / "workbench_audit.json"
    workbench = json.loads(workbench_path.read_text())
    del workbench["repair_audit"]
    workbench_path.write_text(json.dumps(workbench))
    (failure / "repair_trace.json").unlink()
    (failure / "repair_trace_source.json").unlink()
    _rewrite_generator_hash_manifest(failure)

    output = _freeze_blocked(fixture, tmp_path / "legacy-repair-audit")

    release = json.loads((output / "release_gate.json").read_text())
    assert "repair_attempt_audit_unavailable" in release["block_reasons"]
    observation = json.loads((output / "baseline_observation.json").read_text())
    assert observation["generation_failures"][failure.name][
        "repair_attempt_audit_status"
    ] == "unavailable"


def test_blocked_freezer_reprojects_workbench_audit_without_unknown_secrets(
    tmp_path: Path,
) -> None:
    fixture = _blocked_fixture(tmp_path)
    failure = Path(fixture["failures"][0])
    workbench_path = failure / "workbench_audit.json"
    workbench = json.loads(workbench_path.read_text())
    workbench["runtime_credentials"] = {"token": "R4_SECRET_PROBE"}
    workbench_path.write_text(json.dumps(workbench))
    _rewrite_generator_hash_manifest(failure)

    output = _freeze_blocked(fixture, tmp_path / "sanitized-workbench-audit")

    published = b"\n".join(
        path.read_bytes() for path in output.rglob("*") if path.is_file()
    )
    assert b"R4_SECRET_PROBE" not in published
    retained = json.loads(
        (
            output
            / "generation_failures"
            / failure.name
            / "generator"
            / "workbench_audit.json"
        ).read_text()
    )
    assert "runtime_credentials" not in retained


def test_blocked_freezer_rejects_inconsistent_repair_attempt_audit(
    tmp_path: Path,
) -> None:
    fixture = _blocked_fixture(tmp_path)
    failure = Path(fixture["failures"][0])
    workbench_path = failure / "workbench_audit.json"
    workbench = json.loads(workbench_path.read_text())
    workbench["repair_audit"]["accepted_count"] = 99
    workbench_path.write_text(json.dumps(workbench))
    _rewrite_generator_hash_manifest(failure)

    with pytest.raises(BaselineError, match="repair attempt audit is inconsistent"):
        _freeze_blocked(fixture, tmp_path / "inconsistent-repair-audit")


def test_blocked_freezer_rejects_forged_repair_trace_source_hash(
    tmp_path: Path,
) -> None:
    fixture = _blocked_fixture(tmp_path)
    failure = Path(fixture["failures"][0])
    forged_sha256 = "0" * 64

    trace_path = failure / "repair_trace.json"
    trace = json.loads(trace_path.read_text())
    trace["source_sha256"] = forged_sha256
    trace_path.write_text(json.dumps(trace))

    workbench_path = failure / "workbench_audit.json"
    workbench = json.loads(workbench_path.read_text())
    workbench["task_artifact_hashes"]["quality_repair_result.json"] = forged_sha256
    workbench_path.write_text(json.dumps(workbench))
    _rewrite_generator_hash_manifest(failure)

    with pytest.raises(BaselineError, match="repair trace source hash mismatch"):
        _freeze_blocked(fixture, tmp_path / "forged-repair-source-hash")


def test_blocked_freezer_rejects_secret_value_in_repair_trace_source(
    tmp_path: Path,
) -> None:
    fixture = _blocked_fixture(tmp_path)
    failure = Path(fixture["failures"][0])
    source_path = failure / "repair_trace_source.json"
    source = json.loads(source_path.read_text())
    source["attempts"][0]["candidate_status"] = (
        "Bearer AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    )
    source_bytes = (json.dumps(source) + "\n").encode()
    source_path.write_bytes(source_bytes)
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()

    trace_path = failure / "repair_trace.json"
    trace = json.loads(trace_path.read_text())
    trace["source_sha256"] = source_sha256
    trace_path.write_text(json.dumps(trace))

    workbench_path = failure / "workbench_audit.json"
    workbench = json.loads(workbench_path.read_text())
    workbench["task_artifact_hashes"]["quality_repair_result.json"] = source_sha256
    workbench_path.write_text(json.dumps(workbench))
    _rewrite_generator_hash_manifest(failure)

    with pytest.raises(BaselineError, match="repair trace source contains sensitive"):
        _freeze_blocked(fixture, tmp_path / "secret-repair-source-value")


@pytest.mark.parametrize("status", ["cancelled", "invalid", "failed"])
def test_blocked_freezer_accepts_generator_terminal_status_contract(
    tmp_path: Path, status: str
) -> None:
    fixture = _blocked_fixture(tmp_path)
    failure = Path(fixture["failures"][0])
    failure_path = failure / "generation_failure.json"
    payload = json.loads(failure_path.read_text())
    payload["status"] = status
    payload["failure_code"] = f"workbench_{status}"
    failure_path.write_text(json.dumps(payload))
    _rewrite_generator_hash_manifest(failure)

    output = _freeze_blocked(fixture, tmp_path / f"terminal-{status}")

    assert output.is_dir()
    retained = output / "generation_failures" / failure.name / "generator"
    assert not (retained / "workbench_audit.json").exists()
    assert not (retained / "repair_trace.json").exists()
    assert not (retained / "repair_trace_source.json").exists()


@pytest.mark.parametrize(
    ("failure_code", "status"),
    [
        ("postprocess_worker_failed", "error"),
        ("postprocess_worker_termination_failed", "error"),
        ("candidate_secret_material_detected", "invalid"),
    ],
)
def test_blocked_freezer_accepts_postprocess_terminal_code_contract(
    tmp_path: Path, failure_code: str, status: str
) -> None:
    fixture = _blocked_fixture(tmp_path)
    failure = Path(fixture["failures"][0])
    failure_path = failure / "generation_failure.json"
    payload = json.loads(failure_path.read_text())
    payload["status"] = status
    payload["failure_code"] = failure_code
    failure_path.write_text(json.dumps(payload))
    _rewrite_generator_hash_manifest(failure)

    output = _freeze_blocked(fixture, tmp_path / failure_code)

    assert output.is_dir()


def test_blocked_freezer_rejects_orphan_repair_trace_source(tmp_path: Path) -> None:
    fixture = _blocked_fixture(tmp_path)
    failure = Path(fixture["failures"][0])
    (failure / "repair_trace.json").unlink()
    _rewrite_generator_hash_manifest(failure)

    with pytest.raises(BaselineError, match="repair trace source is orphaned"):
        _freeze_blocked(fixture, tmp_path / "orphan-repair-source")


def test_blocked_freezer_projects_read_only_failure_source_via_writable_staging(
    tmp_path: Path,
) -> None:
    fixture = _blocked_fixture(tmp_path)
    for failure in fixture["failures"]:
        for path in failure.rglob("*"):
            path.chmod(0o555 if path.is_dir() else 0o444)
        failure.chmod(0o555)

    output = _freeze_blocked(fixture, tmp_path / "read-only-failures")

    assert output.is_dir()
    assert all(path.stat().st_mode & 0o222 == 0 for path in output.rglob("*"))


def test_blocked_freezer_rejects_unknown_generation_failure_fields(
    tmp_path: Path,
) -> None:
    fixture = _blocked_fixture(tmp_path)
    failure = Path(fixture["failures"][0])
    failure_path = failure / "generation_failure.json"
    payload = json.loads(failure_path.read_text())
    payload["runtime_credentials"] = {"token": "R4_FAILURE_SECRET"}
    payload["truth_shadow"] = ["hidden-truth-probe"]
    failure_path.write_text(json.dumps(payload))
    _rewrite_generator_hash_manifest(failure)

    with pytest.raises(BaselineError, match="unknown fields"):
        _freeze_blocked(fixture, tmp_path / "unknown-failure-fields")


def test_blocked_freezer_rejects_status_failure_code_mismatch(
    tmp_path: Path,
) -> None:
    fixture = _blocked_fixture(tmp_path)
    failure = Path(fixture["failures"][0])
    failure_path = failure / "generation_failure.json"
    payload = json.loads(failure_path.read_text())
    payload["status"] = "error"
    payload["failure_code"] = "workbench_quality_blocked"
    failure_path.write_text(json.dumps(payload))
    (failure / "workbench_audit.json").unlink()
    (failure / "repair_trace.json").unlink()
    _rewrite_generator_hash_manifest(failure)

    with pytest.raises(BaselineError, match="status/failure_code mismatch"):
        _freeze_blocked(fixture, tmp_path / "mismatched-failure-status")


def test_freezer_publishes_complete_read_only_self_contained_bundle(
    tmp_path: Path,
) -> None:
    fixture = _evidence_fixture(tmp_path)
    output = _freeze(fixture, tmp_path / "baseline")

    assert output == (tmp_path / "baseline").resolve()
    assert len(list((output / "runs").glob("*/evaluation/quality_evaluation_report.json"))) == 12
    assert len(list((output / "runs").glob("*/generator/first_pass/candidate.json"))) == 12
    assert len(list((output / "comparisons" / "rapid-deep").glob("*/rapid/evaluation/quality_evaluation_report.json"))) == 4
    assert len(list((output / "comparisons" / "rapid-deep").glob("*/deep/generator/final_after_auto_repair/candidate.json"))) == 4

    manifest = json.loads((output / "baseline_manifest.json").read_text())
    assert manifest["bundle_status"] == "passed"
    freezer_identity = manifest["freezer_identity"]
    assert len(freezer_identity["implementation_sha256"]) == 64
    for relative, digest in freezer_identity["source_sha256"].items():
        retained = output / "freezer_implementation" / relative
        assert hashlib.sha256(retained.read_bytes()).hexdigest() == digest
    assert manifest["registry_sha256"] == CORPUS.registry_sha256
    assert manifest["corpus_sha256"] == CORPUS.corpus_sha256
    assert set(manifest["case_identities"]) == {case.case_id for case in CORPUS.cases}
    actual_hashes = {
        path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "baseline_manifest.json"
    }
    assert manifest["artifact_sha256"] == actual_hashes
    retained_reviews = list((output / "review_evidence").iterdir())
    assert len(retained_reviews) == 1
    assert hashlib.sha256(retained_reviews[0].read_bytes()).hexdigest() == (
        retained_reviews[0].name
    )
    assert set(manifest["source_run_sha256"]) == {case.case_id for case in CORPUS.cases}
    encoded = "\n".join(
        path.read_text()
        for path in output.rglob("*")
        if path.is_file()
    ).lower()
    assert "overall_score" not in encoded
    assert "weighted_score" not in encoded
    assert "aggregate_score" not in encoded
    if os.name != "nt":
        assert all(path.stat().st_mode & 0o222 == 0 for path in output.rglob("*"))
        assert output.stat().st_mode & 0o222 == 0


def test_clean_identity_rejects_ignored_untracked_reviewer_authority(
    tmp_path: Path,
) -> None:
    fixture = _evidence_fixture(tmp_path)
    repository = Path(fixture["repository"])
    relative = "benchmarks/quality/reviewer_authority.json"
    _run_git(repository, "rm", "--cached", relative)
    (repository / ".gitignore").write_text(relative + "\n", encoding="utf-8")
    _run_git(repository, "add", ".gitignore")
    _run_git(
        repository,
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "ignore reviewer authority",
    )
    assert _run_git(repository, "status", "--porcelain") == ""

    with pytest.raises(BaselineError, match="evaluation identity file"):
        load_clean_evaluation_identity(repository)


def test_freezer_rejects_calibration_evidence_absent_from_bundle(
    tmp_path: Path,
) -> None:
    fixture = _evidence_fixture(tmp_path)
    audit = _bound_audit(fixture)
    audit["false_passes"]["evidence_refs"] = [
        "bundle-review-evidence://sha256/" + "f" * 64
    ]

    with pytest.raises(BaselineError, match="absent from the frozen bundle"):
        freeze_baseline_output(
            run_directories=fixture["runs"],
            generator_directories=fixture["generators"],
            registry_path=fixture["registry"],
            repository_root=fixture["repository"],
            thresholds=_thresholds(),
            calibration_audit=audit,
            review_evidence_files=fixture["review_evidence"],
            work_sufficiency_audit=fixture["work_audit"],
            rapid_run_directories=fixture["rapid_runs"],
            rapid_generator_directories=fixture["rapid_generators"],
            deep_run_directories=fixture["deep_runs"],
            deep_generator_directories=fixture["deep_generators"],
            output_directory=tmp_path / "unbound-review",
        )


def test_freezer_compares_generator_elapsed_to_generation_phase_time(
    tmp_path: Path,
) -> None:
    fixture = _evidence_fixture(tmp_path)
    run = Path(fixture["runs"][0])
    generator = Path(fixture["generators"][0])
    manifest_path = run / "quality_evaluation_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    whole_chain_seconds = manifest["execution"]["wall_clock_seconds"]
    generation_seconds = whole_chain_seconds - 90.0
    manifest["execution"]["generation_wall_clock_seconds"] = generation_seconds

    generation_path = generator / "generation_manifest.json"
    generation = json.loads(generation_path.read_text())
    generation["elapsed_seconds"] = generation_seconds
    generation_path.write_text(json.dumps(generation) + "\n")
    manifest["execution"]["generator_artifact_root_sha256"] = (
        _rewrite_generator_hash_manifest(generator)
    )
    manifest_path.write_text(json.dumps(manifest) + "\n")

    output = _freeze(fixture, tmp_path / "phase-timing")

    assert output.is_dir()


@pytest.mark.parametrize("tamper_response_authority", [False, True])
def test_freezer_binds_cached_reuse_to_retained_response_authority(
    tmp_path: Path, tamper_response_authority: bool
) -> None:
    fixture = _evidence_fixture(tmp_path)
    run = Path(fixture["runs"][0])
    generator = Path(fixture["generators"][0])
    response_sha256 = hashlib.sha256(
        (generator / "benchmark_response.json").read_bytes()
    ).hexdigest()
    generation_path = generator / "generation_manifest.json"
    generation = json.loads(generation_path.read_text())
    generation["cache_reused"] = True
    generation["response_sha256"] = response_sha256
    generation["work_sufficiency"] = {
        "status": "reused",
        "cache_reused": True,
        "reuse_source_sha256": response_sha256,
        "reasons": [],
    }
    generation_path.write_text(json.dumps(generation))
    workbench_path = generator / "workbench_audit.json"
    workbench = json.loads(workbench_path.read_text())
    workbench["task_artifact_hashes"] = {
        "benchmark_response.json": (
            "8" * 64 if tamper_response_authority else response_sha256
        )
    }
    workbench_path.write_text(json.dumps(workbench))
    root_sha256 = _rewrite_generator_hash_manifest(generator)

    manifest_path = run / "quality_evaluation_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    execution = manifest["execution"]
    execution["cache_reuse"] = True
    execution["work_sufficiency"] = "reused"
    execution["work_sufficiency_diagnostic"] = generation["work_sufficiency"]
    execution["generator_response_sha256"] = response_sha256
    execution["generator_artifact_root_sha256"] = root_sha256
    manifest_path.write_text(json.dumps(manifest))
    fixture["work_audit"][manifest["run_ref"]] = _work_disposition(
        manifest["run_ref"], run
    )

    if tamper_response_authority:
        with pytest.raises(BaselineError, match="retained workbench evidence"):
            _freeze(fixture, tmp_path / "baseline")
    else:
        output = _freeze(fixture, tmp_path / "baseline")
        assert output.is_dir()


def test_freezer_rejects_coherently_rewritten_response_hash_declarations(
    tmp_path: Path,
) -> None:
    fixture = _evidence_fixture(tmp_path)
    run = Path(fixture["runs"][0])
    generator = Path(fixture["generators"][0])
    forged_sha256 = "7" * 64

    generation_path = generator / "generation_manifest.json"
    generation = json.loads(generation_path.read_text())
    generation["cache_reused"] = True
    generation["response_sha256"] = forged_sha256
    generation["work_sufficiency"] = {
        "status": "reused",
        "cache_reused": True,
        "reuse_source_sha256": forged_sha256,
        "reasons": [],
    }
    generation_path.write_text(json.dumps(generation))
    workbench_path = generator / "workbench_audit.json"
    workbench = json.loads(workbench_path.read_text())
    workbench["task_artifact_hashes"]["benchmark_response.json"] = forged_sha256
    workbench_path.write_text(json.dumps(workbench))
    root_sha256 = _rewrite_generator_hash_manifest(generator)

    manifest_path = run / "quality_evaluation_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["execution"]["cache_reuse"] = True
    manifest["execution"]["work_sufficiency"] = "reused"
    manifest["execution"]["work_sufficiency_diagnostic"] = generation[
        "work_sufficiency"
    ]
    manifest["execution"]["generator_response_sha256"] = forged_sha256
    manifest["execution"]["generator_artifact_root_sha256"] = root_sha256
    manifest_path.write_text(json.dumps(manifest))
    fixture["work_audit"][manifest["run_ref"]] = _work_disposition(
        manifest["run_ref"], run
    )

    with pytest.raises(BaselineError, match="retained response bytes"):
        _freeze(fixture, tmp_path / "baseline")


def test_freezer_rejects_generator_candidate_tamper(tmp_path: Path) -> None:
    fixture = _evidence_fixture(tmp_path)
    generator = Path(fixture["generators"][0])
    (generator / "first_pass" / "candidate.json").write_text("tampered\n")

    with pytest.raises(BaselineError, match="generator artifact hash mismatch"):
        _freeze(fixture, tmp_path / "tampered")


def test_freezer_rejects_recomputed_generator_manifest_without_evaluation_anchor(
    tmp_path: Path,
) -> None:
    fixture = _evidence_fixture(tmp_path)
    generator = Path(fixture["generators"][0])
    (generator / "first_pass" / "candidate.json").write_text("tampered\n")
    _rewrite_generator_hash_manifest(generator)

    with pytest.raises(BaselineError, match="evaluation artifact root authority mismatch"):
        _freeze(fixture, tmp_path / "recomputed")


def test_freezer_rejects_unmanifested_nested_same_name_file(tmp_path: Path) -> None:
    fixture = _evidence_fixture(tmp_path)
    generator = Path(fixture["generators"][0])
    (generator / "first_pass" / "artifact_hash_manifest.json").write_text(
        '{"candidate":"unmanifested"}\n'
    )

    with pytest.raises(BaselineError, match="generator artifact set"):
        _freeze(fixture, tmp_path / "nested-control-name")


def test_freezer_rejects_current_evaluation_anchor_mismatch(tmp_path: Path) -> None:
    fixture = _evidence_fixture(tmp_path)
    run = Path(fixture["runs"][0])
    manifest_path = run / "quality_evaluation_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["execution"]["generator_artifact_root_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest) + "\n")

    with pytest.raises(BaselineError, match="evaluation artifact root authority mismatch"):
        _freeze(fixture, tmp_path / "wrong-authority")


@pytest.mark.parametrize("valid", [True, False], ids=("valid", "invalid-root"))
def test_freezer_validates_non_circular_legacy_generator_anchor(
    tmp_path: Path, valid: bool
) -> None:
    fixture = _evidence_fixture(tmp_path)
    generator = Path(fixture["generators"][0])
    root_sha = _rewrite_generator_hash_manifest(generator, legacy=True)
    generation_path = generator / "generation_manifest.json"
    generation = json.loads(generation_path.read_text())
    generation.pop("artifact_hash_manifest")
    generation["artifact_root_sha256"] = root_sha if valid else "0" * 64
    generation_path.write_text(json.dumps(generation) + "\n")

    if valid:
        assert _freeze(fixture, tmp_path / "legacy-valid").is_dir()
    else:
        with pytest.raises(BaselineError, match="legacy generator artifact root mismatch"):
            _freeze(fixture, tmp_path / "legacy-invalid")


@pytest.mark.parametrize("anchor_mode", ["both", "neither"])
def test_freezer_rejects_ambiguous_generator_anchor_contract(
    tmp_path: Path, anchor_mode: str
) -> None:
    fixture = _evidence_fixture(tmp_path)
    generator = Path(fixture["generators"][0])
    generation_path = generator / "generation_manifest.json"
    generation = json.loads(generation_path.read_text())
    if anchor_mode == "both":
        generation["artifact_root_sha256"] = "0" * 64
    else:
        generation.pop("artifact_hash_manifest")
    generation_path.write_text(json.dumps(generation) + "\n")

    with pytest.raises(BaselineError, match="anchor contract is ambiguous"):
        _freeze(fixture, tmp_path / f"anchor-{anchor_mode}")


def test_bundle_remains_replayable_after_external_evidence_is_deleted(
    tmp_path: Path,
) -> None:
    fixture = _evidence_fixture(tmp_path)
    output = _freeze(fixture, tmp_path / "baseline")
    shutil.rmtree(fixture["evidence_root"])

    retained = sorted((output / "runs").glob("*/evaluation"))
    result = compare_historical_replay(retained, output)

    assert len(retained) == 12
    assert result["status"] == "compared"
    assert result["regressions"] == []


def test_freezer_reads_only_staged_copy_after_source_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.quality_baseline_freezer as freezer

    fixture = _evidence_fixture(tmp_path)
    original = freezer._copy_evaluation_once

    def copy_then_mutate(source: Path, destination: Path) -> None:
        original(source, destination)
        (source / "quality_evaluation_report.json").write_text('{"tampered":true}\n')

    monkeypatch.setattr(freezer, "_copy_evaluation_once", copy_then_mutate)

    output = _freeze(fixture, tmp_path / "baseline")

    assert json.loads((output / "release_gate.json").read_text())["release_gate"] == "pass"
    assert json.loads((output / "baseline_manifest.json").read_text())["bundle_status"] == "passed"


def test_freezer_refuses_partial_corpus_and_existing_destination(tmp_path: Path) -> None:
    fixture = _evidence_fixture(tmp_path)
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        _freeze(fixture, existing)

    with pytest.raises(BaselineError, match="complete 12-case corpus"):
        freeze_baseline_output(
            run_directories=fixture["runs"][:-1],
            generator_directories=fixture["generators"][:-1],
            registry_path=fixture["registry"],
            repository_root=fixture["repository"],
            thresholds=_thresholds(),
            calibration_audit=_bound_audit(fixture),
            review_evidence_files=fixture["review_evidence"],
            work_sufficiency_audit=fixture["work_audit"],
            rapid_run_directories=fixture["rapid_runs"],
            rapid_generator_directories=fixture["rapid_generators"],
            deep_run_directories=fixture["deep_runs"],
            deep_generator_directories=fixture["deep_generators"],
            output_directory=tmp_path / "partial",
        )
    assert not (tmp_path / "partial").exists()


def test_freezer_rejects_caller_corpus_mapping_and_synthetic_registry(
    tmp_path: Path,
) -> None:
    assert "corpus_cases" not in inspect.signature(freeze_baseline_output).parameters
    fixture = _evidence_fixture(tmp_path)
    repository = fixture["repository"]
    registry_path = fixture["registry"]
    registry = json.loads(registry_path.read_text())
    registry["projects"][0]["id"] = "project-00"
    registry_path.write_text(json.dumps(registry))
    _run_git(repository, "add", "benchmarks/quality/registry.json")
    _run_git(
        repository,
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "synthetic registry mutation",
    )

    with pytest.raises(QualityCorpusError, match="domain authority"):
        _freeze(fixture, tmp_path / "synthetic-output", registry_path=registry_path)
    assert not (tmp_path / "synthetic-output").exists()


def test_freezer_rejects_registry_outside_the_clean_codetalk_revision(
    tmp_path: Path,
) -> None:
    fixture = _evidence_fixture(tmp_path)
    copied = tmp_path / "external-quality"
    shutil.copytree(REGISTRY_PATH.parent, copied)

    with pytest.raises(BaselineError, match="formal registry"):
        _freeze(
            fixture,
            tmp_path / "external-output",
            registry_path=copied / "registry.json",
        )


def test_freezer_requires_clean_codetalk_and_binds_evaluator_bytes(tmp_path: Path) -> None:
    fixture = _evidence_fixture(tmp_path)
    repository = fixture["repository"]
    identity = load_clean_evaluation_identity(repository)
    assert len(identity.evaluator_sha256) == 64
    (repository / EVALUATOR_SOURCE_PATHS[0]).write_text("# dirty mutation\n")

    with pytest.raises(BaselineError, match="clean CodeTalk worktree"):
        _freeze(fixture, tmp_path / "dirty-output")


def _write_cli_inputs(tmp_path: Path, fixture: dict[str, object]) -> dict[str, Path]:
    values = {
        "thresholds.json": _thresholds(),
        "audit.json": _bound_audit(fixture),
        "work.json": fixture["work_audit"],
    }
    paths: dict[str, Path] = {}
    for name, value in values.items():
        path = tmp_path / name
        path.write_text(json.dumps(value), encoding="utf-8")
        paths[name] = path
    return paths


def _cli_args(
    fixture: dict[str, object], inputs: dict[str, Path], output: Path
) -> list[str]:
    return [
        "--runs-root", str(Path(fixture["runs"][0]).parent),
        "--run-artifacts-root", str(Path(fixture["generators"][0]).parent),
        "--rapid-runs-root", str(Path(fixture["rapid_runs"][0]).parent),
        "--rapid-run-artifacts-root", str(Path(fixture["rapid_generators"][0]).parent),
        "--deep-runs-root", str(Path(fixture["deep_runs"][0]).parent),
        "--deep-run-artifacts-root", str(Path(fixture["deep_generators"][0]).parent),
        "--registry", str(fixture["registry"]),
        "--repository-root", str(fixture["repository"]),
        "--thresholds", str(inputs["thresholds.json"]),
        "--calibration-audit", str(inputs["audit.json"]),
        "--review-evidence", str(Path(fixture["review_evidence"][0])),
        "--work-sufficiency-audit", str(inputs["work.json"]),
        "--output", str(output),
    ]


def test_freezer_cli_discovers_evidence_and_returns_zero_for_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = _evidence_fixture(tmp_path)
    inputs = _write_cli_inputs(tmp_path, fixture)
    output = tmp_path / "published"

    assert main(_cli_args(fixture, inputs, output)) == 0
    assert capsys.readouterr().out.strip() == str(output.resolve())
    matrix = json.loads((output / "regression_matrix.json").read_text())
    assert matrix["rapid_vs_deep"]["evidence_kind"] == "paired_immutable_reports"
    assert matrix["core_baseline_blocked"] is False


def test_freezer_keeps_all_rapid_core_and_paired_profile_gates_independent(
    tmp_path: Path,
) -> None:
    fixture = _evidence_fixture(tmp_path, core_profile="rapid")
    output = _freeze(fixture, tmp_path / "baseline")

    release = json.loads((output / "release_gate.json").read_text())
    matrix = json.loads((output / "regression_matrix.json").read_text())

    assert release["timing"] == {
        "rapid": "pass",
        "deep": "not_run",
        "work_sufficiency": "pass",
    }
    assert release["release_gate"] == "pass"
    assert matrix["rapid_vs_deep"]["status"] == "complete"
    assert matrix["rapid_vs_deep"]["timing"]["rapid"]["gate"] == "pass"
    assert matrix["rapid_vs_deep"]["timing"]["deep"]["gate"] == "pass"
    assert matrix["core_baseline_blocked"] is False


def test_blocked_release_is_atomically_published_but_cli_returns_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = _evidence_fixture(tmp_path, rapid_limit_override=901.0)
    inputs = _write_cli_inputs(tmp_path, fixture)
    output = tmp_path / "blocked"

    assert main(_cli_args(fixture, inputs, output)) == 2
    assert capsys.readouterr().out.strip() == str(output.resolve())
    assert json.loads((output / "release_gate.json").read_text())["release_gate"] == "fail"
    assert json.loads((output / "regression_matrix.json").read_text())["core_baseline_blocked"] is True
    assert json.loads((output / "baseline_manifest.json").read_text())["bundle_status"] == "blocked"
    assert output.is_dir()


def test_cli_publishes_generation_blocked_observation_and_returns_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = _blocked_fixture(tmp_path)
    inputs = _write_cli_inputs(tmp_path, fixture)
    runs_root = tmp_path / "blocked-cli-runs"
    generators_root = tmp_path / "blocked-cli-generators"
    for run in fixture["runs"]:
        shutil.copytree(run, runs_root / Path(run).name)
    for generator in fixture["generators"]:
        shutil.copytree(generator, generators_root / Path(generator).name)
    for failure in fixture["failures"]:
        shutil.copytree(failure, generators_root / Path(failure).name)
    output = tmp_path / "blocked-cli-output"
    args = [
        "--publish-blocked-on-generation-failure",
        "--runs-root", str(runs_root),
        "--run-artifacts-root", str(generators_root),
        "--registry", str(fixture["registry"]),
        "--repository-root", str(fixture["repository"]),
        "--review-evidence", str(Path(fixture["review_evidence"][0])),
        "--work-sufficiency-audit", str(inputs["work.json"]),
        "--output", str(output),
    ]

    assert main(args) == 2
    assert capsys.readouterr().out.strip() == str(output.resolve())
    assert json.loads((output / "release_gate.json").read_text())[
        "release_status"
    ] == "blocked"
    assert not (output / "threshold_policy.json").exists()
    assert json.loads((output / "regression_matrix.json").read_text())[
        "rapid_vs_deep"
    ]["status"] == "not_run"


def test_cli_has_no_caller_corpus_versions_or_rapid_status_inputs() -> None:
    with pytest.raises(SystemExit):
        main(
            [
                "--corpus-cases", "synthetic.json",
                "--versions", "caller.json",
                "--rapid-deep-result", "status.json",
            ]
        )
