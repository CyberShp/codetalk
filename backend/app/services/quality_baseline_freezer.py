"""Atomic publication of a reviewed, self-contained F012 baseline bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.services.quality_baseline import (
    BUNDLE_SCHEMA_VERSION,
    HUMAN_REPORT_FILENAME,
    MANIFEST_FILENAME,
    REPORT_FILENAME,
    BaselineError,
    EvaluationCodeIdentity,
    build_baseline_summary,
    build_regression_matrix,
    compare_historical_replay,
    compare_rapid_deep_runs,
    evaluate_release_policy,
    freeze_threshold_policy,
    load_clean_evaluation_identity,
    load_immutable_evaluation,
    serialize_baseline_data,
)
from app.services.quality_benchmark_corpus import (
    QualityBaselineCorpusIdentity,
    load_quality_baseline_corpus,
)
from app.services.quality_benchmark_runner import _rename_directory_noreplace

GENERATOR_REQUIRED_FILES = (
    "benchmark_response.json",
    "repair_summary.json",
    "versions.json",
    "generation_manifest.json",
    "artifact_hash_manifest.json",
)
GENERATOR_REQUIRED_DIRECTORIES = ("first_pass", "final_after_auto_repair")
GENERATOR_OPTIONAL_FILES = ("workbench_audit.json",)
FREEZER_IMPLEMENTATION_PATHS = (
    "backend/app/services/quality_baseline_freezer.py",
    "backend/app/services/quality_baseline.py",
    "backend/app/services/quality_benchmark_corpus.py",
    "backend/app/services/quality_benchmark_runner.py",
)
_REPAIR_TRACE_SECRET_VALUE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----",
        r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}",
        r"\b(?:sk-(?:proj-)?|gh[pousr]_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{16,}",
        r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
        r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|credential)\b\s*[:=]\s*[\"']?[A-Za-z0-9._~+/=-]{12,}",
    )
)


def freeze_blocked_baseline_output(
    *,
    run_directories: Sequence[str | Path],
    generator_directories: Sequence[str | Path],
    failure_directories: Sequence[str | Path],
    registry_path: str | Path,
    repository_root: str | Path,
    review_evidence_files: Sequence[str | Path],
    work_sufficiency_audit: Mapping[str, Any],
    rapid_run_directories: Sequence[str | Path],
    rapid_generator_directories: Sequence[str | Path],
    deep_run_directories: Sequence[str | Path],
    deep_generator_directories: Sequence[str | Path],
    output_directory: str | Path,
) -> Path:
    """Freeze complete observations when generation blocks before evaluation."""

    output = Path(output_directory).resolve()
    if output.exists():
        raise FileExistsError(f"immutable baseline output already exists: {output}")
    evaluation_identity = load_clean_evaluation_identity(repository_root)
    formal_registry = evaluation_identity.repository_root / "benchmarks/quality/registry.json"
    try:
        requested_registry = Path(registry_path).resolve(strict=True)
        resolved_formal_registry = formal_registry.resolve(strict=True)
    except OSError as exc:
        raise BaselineError("the formal registry is unavailable") from exc
    if requested_registry != resolved_formal_registry:
        raise BaselineError(
            "registry must be the formal registry in the clean CodeTalk revision"
        )
    corpus = load_quality_baseline_corpus(resolved_formal_registry)
    _require_tracked_corpus(evaluation_identity, corpus)

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=output.parent, prefix=f".{output.name}."))
    try:
        freezer_identity = _stage_freezer_implementation(staging)
        if review_evidence_files:
            _stage_review_evidence(staging, review_evidence_files)
        core = _stage_evidence_group(
            staging,
            group="core",
            run_directories=run_directories,
            generator_directories=generator_directories,
            corpus=corpus,
            evaluation_identity=evaluation_identity,
        )
        failures = _stage_generation_failures(
            staging,
            failure_directories=failure_directories,
            corpus=corpus,
            evaluation_identity=evaluation_identity,
        )
        evaluated_ids = {case_id for case_id, _ in core}
        failure_ids = {case_id for case_id, _, _ in failures}
        if evaluated_ids & failure_ids:
            raise BaselineError("case cannot be both evaluated and generation-blocked")
        expected_ids = set(corpus.case_map)
        observed_ids = evaluated_ids | failure_ids
        if observed_ids != expected_ids:
            raise BaselineError(
                "blocked baseline requires complete 12-case observation coverage"
            )

        comparison_inputs = (
            rapid_run_directories,
            rapid_generator_directories,
            deep_run_directories,
            deep_generator_directories,
        )
        if any(comparison_inputs) and not all(comparison_inputs):
            raise BaselineError(
                "blocked rapid/deep comparison requires all four evidence sets"
            )
        if all(comparison_inputs):
            rapid = _stage_evidence_group(
                staging,
                group="rapid",
                run_directories=rapid_run_directories,
                generator_directories=rapid_generator_directories,
                corpus=corpus,
                evaluation_identity=evaluation_identity,
            )
            deep = _stage_evidence_group(
                staging,
                group="deep",
                run_directories=deep_run_directories,
                generator_directories=deep_generator_directories,
                corpus=corpus,
                evaluation_identity=evaluation_identity,
            )
        else:
            rapid = []
            deep = []
        core_runs = _publish_staged_core(staging, core)
        rapid_runs = (
            _publish_staged_comparison(staging, rapid, "rapid") if rapid else []
        )
        deep_runs = (
            _publish_staged_comparison(staging, deep, "deep") if deep else []
        )
        published_failures = _publish_staged_failures(staging, failures)
        shutil.rmtree(staging / ".incoming")

        summary = build_baseline_summary(
            core_runs,
            corpus=corpus,
            evaluation_identity=evaluation_identity,
            work_sufficiency_audit=work_sufficiency_audit,
        )
        models = {
            str(payload["model"])
            for payload in published_failures.values()
        }
        summary_model = str(summary["identity"].get("model") or "")
        if models != {summary_model}:
            raise BaselineError(
                "generation failure model identity does not match evaluated runs"
            )
        comparison = (
            compare_rapid_deep_runs(
                rapid_runs,
                deep_runs,
                corpus=corpus,
                evaluation_identity=evaluation_identity,
                work_sufficiency_audit=work_sufficiency_audit,
            )
            if rapid_runs
            else None
        )
        coverage = {
            "expected": len(expected_ids),
            "attempted": len(observed_ids),
            "evaluated": len(evaluated_ids),
            "generation_failed": len(failure_ids),
            "missing_attempt_case_ids": sorted(expected_ids - observed_ids),
            "missing_evaluation_case_ids": sorted(expected_ids - evaluated_ids),
        }
        observation = {
            "schema_version": "quality-baseline-observation-v1",
            "identity": summary["identity"],
            "freezer_identity": freezer_identity,
            "coverage": coverage,
            "generation_failures": published_failures,
            "evaluated_summary_ref": "baseline_summary.json",
            "rapid_deep_comparison": (
                comparison.payload
                if comparison is not None
                else {
                    "status": "not_run",
                    "reason": "paired_rapid_deep_evidence_unavailable",
                }
            ),
        }
        threshold_status = {
            "schema_version": "quality-threshold-policy-not-frozen-v1",
            "status": "not_frozen",
            "reason": "complete_evaluable_corpus_unavailable",
            "evaluated_case_count": len(evaluated_ids),
            "expected_case_count": len(expected_ids),
            "missing_evaluation_case_ids": sorted(expected_ids - evaluated_ids),
        }
        block_reasons = [
            "generation_failures_present",
            "thresholds_not_frozen",
        ]
        if any(
            payload.get("repair_attempt_audit_status") == "unavailable"
            for payload in published_failures.values()
        ):
            block_reasons.append("repair_attempt_audit_unavailable")
        release_gate = {
            "schema_version": "quality-release-gate-blocked-v1",
            "release_gate": "fail",
            "release_status": "blocked",
            "block_reasons": block_reasons,
            "axis_gates": {
                axis: "not_evaluated_for_complete_corpus"
                for axis in ("accuracy", "breadth", "depth")
            },
            "generation_failure_case_ids": sorted(failure_ids),
        }
        regression = build_regression_matrix(
            release_gate=release_gate,
            historical_replay={
                "status": "not_run",
                "reason": "blocked_core_baseline",
            },
            rapid_deep_comparison=comparison,
        )
        artifacts: dict[str, bytes] = {
            "baseline_observation.json": serialize_baseline_data(observation).encode(),
            "baseline_summary.json": serialize_baseline_data(summary).encode(),
            "threshold_freeze_status.json": serialize_baseline_data(
                threshold_status
            ).encode(),
            "release_gate.json": serialize_baseline_data(release_gate).encode(),
            "regression_matrix.json": serialize_baseline_data(regression).encode(),
            "environment_manifest.json": serialize_baseline_data(
                _blocked_environment_manifest(
                    core_runs, published_failures, evaluation_identity
                )
            ).encode(),
            "baseline_report.md": _render_blocked_baseline_markdown(
                observation, release_gate
            ).encode(),
        }
        for name, payload in artifacts.items():
            (staging / name).write_bytes(payload)
        manifest = {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "bundle_status": "blocked",
            "artifact_sha256": _tree_hashes(
                staging, exclude={"baseline_manifest.json"}
            ),
            "source_run_sha256": _source_run_hashes(core_runs),
            "source_generation_failure_sha256": _source_failure_hashes(
                staging / "generation_failures"
            ),
            "registry_sha256": corpus.registry_sha256,
            "corpus_sha256": corpus.corpus_sha256,
            "case_identities": {
                case.case_id: case.as_dict() for case in corpus.cases
            },
            "evaluation_identity": evaluation_identity.as_dict(),
            "freezer_identity": freezer_identity,
            "model": summary_model,
        }
        (staging / "baseline_manifest.json").write_text(
            serialize_baseline_data(manifest), encoding="utf-8"
        )
        _make_tree_read_only(staging)
        _rename_directory_noreplace(staging, output)
        output.chmod(0o555)
    finally:
        if staging.exists():
            _make_tree_writable(staging)
            shutil.rmtree(staging)
    return output


def _stage_freezer_implementation(staging: Path) -> dict[str, Any]:
    repository_root = Path(__file__).resolve().parents[3]
    destination = staging / "freezer_implementation"
    digest = hashlib.sha256()
    source_sha256: dict[str, str] = {}
    clean_revision = True
    for relative in FREEZER_IMPLEMENTATION_PATHS:
        source = repository_root / relative
        if not source.is_file() or source.is_symlink():
            raise BaselineError(f"freezer implementation source is unsafe: {relative}")
        data = source.read_bytes()
        source_sha256[relative] = hashlib.sha256(data).hexdigest()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        committed = subprocess.run(
            ["git", "-C", str(repository_root), "show", f"HEAD:{relative}"],
            check=False,
            capture_output=True,
            timeout=15,
        )
        if committed.returncode != 0 or committed.stdout != data:
            clean_revision = False
    revision = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout.strip()
    return {
        "schema_version": "quality-baseline-freezer-identity-v1",
        "repository_revision": revision,
        "clean_revision": clean_revision,
        "implementation_sha256": digest.hexdigest(),
        "source_sha256": dict(sorted(source_sha256.items())),
    }


def freeze_baseline_output(
    *,
    run_directories: Sequence[str | Path],
    generator_directories: Sequence[str | Path],
    registry_path: str | Path,
    repository_root: str | Path,
    thresholds: Mapping[str, Mapping[str, float]],
    calibration_audit: Mapping[str, Any],
    review_evidence_files: Sequence[str | Path],
    work_sufficiency_audit: Mapping[str, Any],
    rapid_run_directories: Sequence[str | Path],
    rapid_generator_directories: Sequence[str | Path],
    deep_run_directories: Sequence[str | Path],
    deep_generator_directories: Sequence[str | Path],
    output_directory: str | Path,
    previous_baseline_directory: str | Path | None = None,
) -> Path:
    """Copy once, validate from staging, and atomically publish pass/block evidence."""

    output = Path(output_directory).resolve()
    if output.exists():
        raise FileExistsError(f"immutable baseline output already exists: {output}")
    evaluation_identity = load_clean_evaluation_identity(repository_root)
    formal_registry = evaluation_identity.repository_root / "benchmarks/quality/registry.json"
    try:
        requested_registry = Path(registry_path).resolve(strict=True)
        resolved_formal_registry = formal_registry.resolve(strict=True)
    except OSError as exc:
        raise BaselineError("the formal registry is unavailable") from exc
    if requested_registry != resolved_formal_registry:
        raise BaselineError(
            "registry must be the formal registry in the clean CodeTalk revision"
        )
    corpus = load_quality_baseline_corpus(resolved_formal_registry)
    _require_tracked_corpus(evaluation_identity, corpus)

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=output.parent, prefix=f".{output.name}."))
    try:
        freezer_identity = _stage_freezer_implementation(staging)
        review_evidence_refs = _stage_review_evidence(
            staging, review_evidence_files
        )
        _require_bound_calibration_evidence(
            calibration_audit, review_evidence_refs
        )
        core = _stage_evidence_group(
            staging,
            group="core",
            run_directories=run_directories,
            generator_directories=generator_directories,
            corpus=corpus,
            evaluation_identity=evaluation_identity,
        )
        rapid = _stage_evidence_group(
            staging,
            group="rapid",
            run_directories=rapid_run_directories,
            generator_directories=rapid_generator_directories,
            corpus=corpus,
            evaluation_identity=evaluation_identity,
        )
        deep = _stage_evidence_group(
            staging,
            group="deep",
            run_directories=deep_run_directories,
            generator_directories=deep_generator_directories,
            corpus=corpus,
            evaluation_identity=evaluation_identity,
        )

        core_runs = _publish_staged_core(staging, core)
        rapid_runs = _publish_staged_comparison(staging, rapid, "rapid")
        deep_runs = _publish_staged_comparison(staging, deep, "deep")
        shutil.rmtree(staging / ".incoming")
        summary = build_baseline_summary(
            core_runs,
            corpus=corpus,
            evaluation_identity=evaluation_identity,
            work_sufficiency_audit=work_sufficiency_audit,
        )
        policy = freeze_threshold_policy(
            summary,
            thresholds=thresholds,
            calibration_audit=calibration_audit,
        )
        release_gate = evaluate_release_policy(summary, policy)
        rapid_deep = compare_rapid_deep_runs(
            rapid_runs,
            deep_runs,
            corpus=corpus,
            evaluation_identity=evaluation_identity,
            work_sufficiency_audit=work_sufficiency_audit,
        )

        previous_staged: Path | None = None
        if previous_baseline_directory is not None:
            previous_staged = staging / ".previous-baseline"
            _copy_tree_once(
                Path(previous_baseline_directory), previous_staged, label="previous baseline"
            )
        history = compare_historical_replay(core_runs, previous_staged)
        regression = build_regression_matrix(
            release_gate=release_gate,
            historical_replay=history,
            rapid_deep_comparison=rapid_deep,
        )
        if previous_staged is not None:
            _make_tree_writable(previous_staged)
            shutil.rmtree(previous_staged)

        environment = _environment_manifest(core_runs, evaluation_identity)
        artifacts: dict[str, bytes] = {
            "baseline_summary.json": serialize_baseline_data(summary).encode(),
            "threshold_policy.json": serialize_baseline_data(policy).encode(),
            "calibration_anomalies.json": serialize_baseline_data(
                policy["calibration_audit"]
            ).encode(),
            "release_gate.json": serialize_baseline_data(release_gate).encode(),
            "regression_matrix.json": serialize_baseline_data(regression).encode(),
            "environment_manifest.json": serialize_baseline_data(environment).encode(),
            "baseline_report.md": _render_baseline_markdown(
                summary, release_gate, regression
            ).encode(),
        }
        for name, payload in artifacts.items():
            (staging / name).write_bytes(payload)

        bundle_status = (
            "passed"
            if release_gate["release_gate"] == "pass"
            and regression["core_baseline_blocked"] is False
            else "blocked"
        )
        source_hashes = _source_run_hashes(core_runs)
        artifact_hashes = _tree_hashes(staging, exclude={"baseline_manifest.json"})
        manifest = {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "bundle_status": bundle_status,
            "artifact_sha256": artifact_hashes,
            "source_run_sha256": source_hashes,
            "registry_sha256": corpus.registry_sha256,
            "corpus_sha256": corpus.corpus_sha256,
            "case_identities": {
                case.case_id: case.as_dict() for case in corpus.cases
            },
            "evaluation_identity": evaluation_identity.as_dict(),
            "freezer_identity": freezer_identity,
            "model": summary["identity"]["model"],
        }
        (staging / "baseline_manifest.json").write_text(
            serialize_baseline_data(manifest), encoding="utf-8"
        )
        _make_tree_read_only(staging)
        _rename_directory_noreplace(staging, output)
        output.chmod(0o555)
    finally:
        if staging.exists():
            _make_tree_writable(staging)
            shutil.rmtree(staging)
    return output


def _stage_review_evidence(
    staging: Path,
    evidence_files: Sequence[str | Path],
) -> frozenset[str]:
    if not evidence_files:
        raise BaselineError("review evidence set must not be empty")
    destination = staging / "review_evidence"
    destination.mkdir()
    retained: set[str] = set()
    for source_value in evidence_files:
        source = Path(source_value)
        if source.is_symlink():
            raise BaselineError(f"review evidence must not be a symlink: {source}")
        try:
            resolved = source.resolve(strict=True)
        except OSError as exc:
            raise BaselineError(f"review evidence is unavailable: {source}") from exc
        if not resolved.is_file():
            raise BaselineError(f"review evidence is not a file: {resolved}")
        payload = resolved.read_bytes()
        if not payload:
            raise BaselineError(f"review evidence must not be empty: {resolved}")
        digest = hashlib.sha256(payload).hexdigest()
        ref = f"bundle-review-evidence://sha256/{digest}"
        target = destination / digest
        if target.exists():
            if target.read_bytes() != payload:
                raise BaselineError("review evidence SHA-256 collision")
        else:
            target.write_bytes(payload)
        retained.add(ref)
    return frozenset(retained)


def _require_bound_calibration_evidence(
    calibration_audit: Mapping[str, Any],
    retained_refs: frozenset[str],
) -> None:
    referenced: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if key == "evidence_refs" and isinstance(nested, list):
                    referenced.update(item for item in nested if isinstance(item, str))
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(calibration_audit)
    if not referenced:
        raise BaselineError("calibration audit does not reference review evidence")
    unbound = sorted(referenced - retained_refs)
    if unbound:
        raise BaselineError(
            "calibration audit references evidence absent from the frozen bundle: "
            + ", ".join(unbound)
        )


def _stage_evidence_group(
    staging: Path,
    *,
    group: str,
    run_directories: Sequence[str | Path],
    generator_directories: Sequence[str | Path],
    corpus: QualityBaselineCorpusIdentity,
    evaluation_identity: EvaluationCodeIdentity,
) -> list[tuple[str, Path]]:
    if len(run_directories) != len(generator_directories):
        raise BaselineError(f"{group} evaluation/generator counts do not match")
    if not run_directories:
        raise BaselineError(f"{group} evidence set must not be empty")
    incoming = staging / ".incoming" / group
    incoming.mkdir(parents=True, exist_ok=True)
    result: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for index, (run_directory, generator_directory) in enumerate(
        zip(run_directories, generator_directories, strict=True)
    ):
        pair_root = incoming / f"{index:03d}"
        evaluation = pair_root / "evaluation"
        generator = pair_root / "generator"
        _copy_evaluation_once(Path(run_directory), evaluation)
        _copy_generator_once(Path(generator_directory), generator)
        loaded = load_immutable_evaluation(
            evaluation,
            expected_identity=evaluation_identity,
            require_execution=True,
        )
        case_id = str(loaded.manifest["case_id"])
        if case_id in seen:
            raise BaselineError(f"duplicate {group} case_id: {case_id}")
        expected_case = corpus.case_map.get(case_id)
        if expected_case is None:
            raise BaselineError(f"{group} case is outside formal corpus: {case_id}")
        load_immutable_evaluation(
            evaluation,
            expected_case=expected_case,
            expected_identity=evaluation_identity,
            require_execution=True,
        )
        _validate_generator_evidence(generator, loaded, expected_case=expected_case)
        result.append((case_id, pair_root))
        seen.add(case_id)
    return result


def _stage_generation_failures(
    staging: Path,
    *,
    failure_directories: Sequence[str | Path],
    corpus: QualityBaselineCorpusIdentity,
    evaluation_identity: EvaluationCodeIdentity,
) -> list[tuple[str, Path, dict[str, Any]]]:
    if not failure_directories:
        raise BaselineError("blocked baseline requires generation failure evidence")
    incoming = staging / ".incoming" / "generation_failures"
    incoming.mkdir(parents=True, exist_ok=True)
    result: list[tuple[str, Path, dict[str, Any]]] = []
    seen: set[str] = set()
    for index, source in enumerate(failure_directories):
        pair_root = incoming / f"{index:03d}"
        generator = pair_root / "generator"
        _copy_tree_once(Path(source), generator, label="generation failure")
        _make_tree_writable(generator)
        payload = _validate_generation_failure_evidence(
            generator,
            corpus=corpus,
            evaluation_identity=evaluation_identity,
        )
        case_id = str(payload["case_id"])
        if case_id in seen:
            raise BaselineError(f"duplicate generation failure case_id: {case_id}")
        seen.add(case_id)
        result.append((case_id, pair_root, payload))
    return result


def _validate_generation_failure_evidence(
    generator: Path,
    *,
    corpus: QualityBaselineCorpusIdentity,
    evaluation_identity: EvaluationCodeIdentity,
) -> dict[str, Any]:
    allowed = {
        "generation_failure.json",
        "workbench_audit.json",
        "repair_trace.json",
        "repair_trace_source.json",
        "artifact_hash_manifest.json",
    }
    actual = {
        path.relative_to(generator).as_posix()
        for path in generator.rglob("*")
        if path.is_file()
    }
    if not {"generation_failure.json", "artifact_hash_manifest.json"}.issubset(actual):
        raise BaselineError("generation failure evidence is incomplete")
    if not actual.issubset(allowed):
        raise BaselineError("generation failure evidence contains unexpected files")
    source_artifact_root_sha256 = _validate_artifact_hash_tree(
        generator, label="generation failure"
    )

    payload = _read_json_mapping(
        generator / "generation_failure.json", "generation failure"
    )
    expected_fields = {
        "schema_version",
        "case_id",
        "mode",
        "model",
        "codetalk_revision",
        "source_tree",
        "elapsed_seconds",
        "timeout_seconds",
        "status",
        "failure_code",
        "truth_inputs",
    }
    if set(payload) != expected_fields:
        raise BaselineError("generation failure contains unknown fields")
    if payload.get("schema_version") != "quality-benchmark-generator-v1":
        raise BaselineError("unsupported generation failure schema")
    case_id = str(payload.get("case_id") or "")
    expected_case = corpus.case_map.get(case_id)
    if expected_case is None:
        raise BaselineError("generation failure case is outside formal corpus")
    checks = {
        "codetalk revision": (
            payload.get("codetalk_revision"),
            evaluation_identity.codetalk_revision,
        ),
        "source tree": (payload.get("source_tree"), expected_case.source_tree),
    }
    for label, (observed, expected) in checks.items():
        if observed != expected:
            raise BaselineError(f"generation failure {label} mismatch")
    if payload.get("truth_inputs") != []:
        raise BaselineError("generation failure evidence must remain truth-isolated")
    if payload.get("mode") not in {"rapid", "deep"}:
        raise BaselineError("generation failure mode is invalid")
    if payload.get("status") not in {
        "quality_blocked",
        "timed_out",
        "cancelled",
        "invalid",
        "error",
        "failed",
    }:
        raise BaselineError("generation failure status is invalid")
    repair_attempt_audit_status = "not_applicable"
    if payload.get("status") == "quality_blocked":
        workbench_path = generator / "workbench_audit.json"
        if not workbench_path.is_file():
            raise BaselineError(
                "quality-blocked failure requires retained workbench audit"
            )
        workbench = _project_blocked_workbench_audit(
            _read_json_mapping(workbench_path, "workbench audit")
        )
        trace_path = generator / "repair_trace.json"
        if trace_path.is_file():
            trace = _read_json_mapping(trace_path, "repair trace")
            if set(trace) != {"schema_version", "source_sha256", "projection"}:
                raise BaselineError("repair trace contains unknown fields")
            if trace.get("schema_version") != "quality-benchmark-repair-trace-v1":
                raise BaselineError("repair trace schema is invalid")
            source_sha256 = trace.get("source_sha256")
            trace_source_path = generator / "repair_trace_source.json"
            if not trace_source_path.is_file():
                raise BaselineError("repair trace source is unavailable")
            trace_source_bytes = trace_source_path.read_bytes()
            if (
                source_sha256
                != workbench["task_artifact_hashes"].get(
                    "quality_repair_result.json"
                )
                or source_sha256
                != hashlib.sha256(trace_source_bytes).hexdigest()
            ):
                raise BaselineError("repair trace source hash mismatch")
            trace_source = _read_json_mapping(
                trace_source_path, "repair trace source"
            )
            source_projection = _project_repair_result_source(trace_source)
            trace_projection = _project_repair_attempt_audit(
                trace.get("projection")
            )
            if (
                trace_projection != workbench["repair_audit"]
                or source_projection != trace_projection
            ):
                raise BaselineError("repair trace projection mismatch")
            trace["projection"] = trace_projection
            trace_path.write_text(serialize_baseline_data(trace), encoding="utf-8")
        else:
            if (generator / "repair_trace_source.json").exists():
                raise BaselineError("repair trace source is orphaned")
            if workbench["repair_audit"].get("status") == "complete":
                workbench["repair_audit"] = {
                    "status": "unavailable",
                    "reason": "canonical_trace_missing",
                }
        workbench_path.write_text(
            serialize_baseline_data(workbench), encoding="utf-8"
        )
        _rewrite_staged_artifact_hash_manifest(generator)
        repair_audit = workbench["repair_audit"]
        repair_attempt_audit_status = (
            "complete"
            if isinstance(repair_audit, Mapping)
            and repair_audit.get("status") == "complete"
            else "unavailable"
        )
    else:
        for name in (
            "workbench_audit.json",
            "repair_trace.json",
            "repair_trace_source.json",
        ):
            path = generator / name
            if path.exists():
                path.unlink()
        _rewrite_staged_artifact_hash_manifest(generator)
    failure_code = str(payload.get("failure_code") or "")
    if not failure_code:
        raise BaselineError("generation failure code is unavailable")
    expected_status_by_code = {
        "workbench_quality_blocked": "quality_blocked",
        "work_sufficiency_incomplete": "quality_blocked",
        "evaluator_repair_exhausted": "quality_blocked",
        "absolute_deadline_exceeded": "timed_out",
        "workbench_timed_out": "timed_out",
        "workbench_cancelled": "cancelled",
        "invalid_workbench_response": "invalid",
        "workbench_invalid": "invalid",
        "workbench_error": "error",
        "workbench_execution_failed": "error",
        "postprocess_worker_failed": "error",
        "postprocess_worker_termination_failed": "error",
        "candidate_materialization_failed": "error",
        "candidate_secret_material_detected": "invalid",
        "workbench_failed": "failed",
    }
    if expected_status_by_code.get(failure_code) != payload.get("status"):
        raise BaselineError("generation failure status/failure_code mismatch")
    if not str(payload.get("model") or ""):
        raise BaselineError("generation failure model identity is unavailable")
    for field in ("elapsed_seconds", "timeout_seconds"):
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise BaselineError(f"generation failure {field} is invalid")
    return {
        **payload,
        "repair_attempt_audit_status": repair_attempt_audit_status,
        "source_artifact_root_sha256": source_artifact_root_sha256,
    }


def _project_blocked_workbench_audit(value: Mapping[str, Any]) -> dict[str, Any]:
    if (
        value.get("schema_version") != "quality-benchmark-workbench-audit-v1"
        or value.get("workbench_status") != "quality_blocked"
    ):
        raise BaselineError("quality-blocked workbench audit is invalid")
    task_run_id = str(value.get("task_run_id") or "")
    if task_run_id and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", task_run_id):
        raise BaselineError("quality-blocked task_run_id is invalid")
    result: dict[str, Any] = {
        "schema_version": "quality-benchmark-workbench-audit-v1",
        "task_run_id": task_run_id,
        "workbench_status": "quality_blocked",
        "terminal_blocked": True,
        "work_sufficiency": _project_work_sufficiency_audit(
            value.get("work_sufficiency")
        ),
        "task_artifact_hashes": _project_sha256_mapping(
            value.get("task_artifact_hashes"),
            allowed_keys={
                "task_run.json",
                "execution.json",
                "benchmark_runtime.json",
                "benchmark_response.json",
                "sandbox_policy.json",
                "quality_repair_result.json",
            },
        ),
        "first_provenance": _project_workbench_provenance(
            value.get("first_provenance")
        ),
        "final_provenance": _project_workbench_provenance(
            value.get("final_provenance")
        ),
    }
    repair_attempt_count = value.get("repair_attempt_count")
    if isinstance(repair_attempt_count, bool) or not isinstance(
        repair_attempt_count, int
    ) or repair_attempt_count < 0:
        repair_attempt_count = 0
    repair_audit = _project_repair_attempt_audit(value.get("repair_audit"))
    if (
        repair_audit.get("status") == "complete"
        and repair_attempt_count != repair_audit["attempted_count"]
    ):
        raise BaselineError("repair attempt audit is inconsistent")
    accepted_response_attempt = value.get("accepted_response_attempt")
    if isinstance(accepted_response_attempt, bool) or not isinstance(
        accepted_response_attempt, int
    ) or accepted_response_attempt < 0:
        accepted_response_attempt = 0
    if (
        repair_audit.get("status") == "complete"
        and accepted_response_attempt > repair_audit["attempted_count"]
    ):
        raise BaselineError("repair attempt audit is inconsistent")
    result["repair_attempt_count"] = repair_attempt_count
    result["accepted_response_attempt"] = accepted_response_attempt
    result["repair_audit"] = repair_audit
    return result


def _project_repair_attempt_audit(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {
            "status": "unavailable",
            "reason": "legacy_projection_missing",
        }
    try:
        attempted_count = int(value.get("attempted_count"))
        accepted_count = int(value.get("accepted_count"))
        last_accepted_attempt = int(value.get("last_accepted_attempt") or 0)
        remaining_seconds = float(value.get("remaining_seconds") or 0.0)
    except (TypeError, ValueError) as exc:
        raise BaselineError("repair attempt audit is invalid") from exc
    outcomes_value = value.get("outcomes")
    if (
        attempted_count < 0
        or accepted_count < 0
        or remaining_seconds < 0
        or not isinstance(outcomes_value, list)
    ):
        raise BaselineError("repair attempt audit is invalid")
    outcomes: list[dict[str, Any]] = []
    for raw in outcomes_value:
        if not isinstance(raw, Mapping):
            raise BaselineError("repair attempt audit is invalid")
        try:
            outcome = {
                "attempt": int(raw.get("attempt")),
                "accepted": raw.get("accepted") is True,
                "status_before": _audit_token(raw.get("status_before")),
                "status_after": _audit_token(raw.get("status_after")),
                "issues_before": int(raw.get("issues_before") or 0),
                "issues_after": int(raw.get("issues_after") or 0),
            }
        except (TypeError, ValueError) as exc:
            raise BaselineError("repair attempt audit is invalid") from exc
        if (
            outcome["attempt"] < 1
            or outcome["issues_before"] < 0
            or outcome["issues_after"] < 0
        ):
            raise BaselineError("repair attempt audit is invalid")
        outcomes.append(outcome)
    accepted_attempts = [
        item["attempt"] for item in outcomes if item["accepted"]
    ]
    if (
        attempted_count != len(outcomes)
        or accepted_count != len(accepted_attempts)
        or last_accepted_attempt != max(accepted_attempts, default=0)
        or [item["attempt"] for item in outcomes]
        != list(range(1, attempted_count + 1))
    ):
        raise BaselineError("repair attempt audit is inconsistent")
    return {
        "status": "complete",
        "attempted_count": attempted_count,
        "accepted_count": accepted_count,
        "last_accepted_attempt": last_accepted_attempt,
        "stopped_reason": _audit_token(value.get("stopped_reason")),
        "remaining_seconds": remaining_seconds,
        "outcomes": outcomes,
    }


def _project_repair_result_source(value: Mapping[str, Any]) -> dict[str, Any]:
    expected_fields = {
        "enabled",
        "attempt_count",
        "attempts",
        "total_budget_seconds",
        "remaining_seconds",
        "stopped_reason",
    }
    if set(value) != expected_fields:
        raise BaselineError("repair trace source contains unknown fields")
    attempts = value.get("attempts")
    if not isinstance(attempts, list):
        raise BaselineError("repair trace source is invalid")
    allowed_attempt_fields = {
        "attempt",
        "duration_ms",
        "status_before",
        "issues_before",
        "status_after",
        "issues_after",
        "accepted",
        "candidate_status",
        "candidate_score",
        "candidate_issues",
        "affected_artifacts",
        "salvaged_rows",
        "materialized_field_patches",
        "contradiction_tombstones",
        "deterministic_repairs",
        "refreshed_reports",
        "field_patch_rounds",
        "final_status",
        "final_issues",
    }
    outcomes: list[dict[str, Any]] = []
    for raw in attempts:
        if not isinstance(raw, Mapping) or not set(raw).issubset(
            allowed_attempt_fields
        ):
            raise BaselineError("repair trace source contains unknown fields")
        _reject_sensitive_repair_trace_keys(raw)
        outcomes.append(
            {
                "attempt": raw.get("attempt"),
                "accepted": raw.get("accepted") is True,
                "status_before": raw.get("status_before"),
                "status_after": raw.get("status_after"),
                "issues_before": raw.get("issues_before"),
                "issues_after": raw.get("issues_after"),
            }
        )
    accepted_attempts = [
        int(item["attempt"])
        for item in outcomes
        if item["accepted"] and item.get("attempt") is not None
    ]
    projected = {
        "attempted_count": value.get("attempt_count"),
        "accepted_count": len(accepted_attempts),
        "last_accepted_attempt": max(accepted_attempts, default=0),
        "stopped_reason": value.get("stopped_reason"),
        "remaining_seconds": value.get("remaining_seconds"),
        "outcomes": outcomes,
    }
    return _project_repair_attempt_audit(projected)


def _reject_sensitive_repair_trace_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if re.search(
                r"(?i)(?:token|secret|password|credential|authorization)",
                str(key),
            ):
                raise BaselineError("repair trace source contains sensitive fields")
            _reject_sensitive_repair_trace_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_sensitive_repair_trace_keys(nested)
    elif isinstance(value, str) and any(
        pattern.search(value) for pattern in _REPAIR_TRACE_SECRET_VALUE_PATTERNS
    ):
        raise BaselineError("repair trace source contains sensitive values")


def _project_work_sufficiency_audit(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key in ("status", "profile"):
        if key in value:
            result[key] = _audit_token(value.get(key))
    for key in ("auto_continue", "cache_reused", "provider_invocation_recorded"):
        if isinstance(value.get(key), bool):
            result[key] = value[key]
    for key in (
        "elapsed_seconds",
        "remaining_seconds",
        "minimum_remaining_seconds",
    ):
        raw = value.get(key)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw >= 0:
            result[key] = raw
    reasons = value.get("reasons")
    if isinstance(reasons, list):
        result["reasons"] = [_audit_token(item) for item in reasons]
    for key in ("axis_evidence", "minimums"):
        nested = value.get(key)
        if not isinstance(nested, Mapping):
            continue
        projected: dict[str, int | bool] = {}
        for nested_key, nested_value in nested.items():
            name = _audit_token(nested_key)
            if isinstance(nested_value, bool):
                projected[name] = nested_value
            elif isinstance(nested_value, int) and nested_value >= 0:
                projected[name] = nested_value
            else:
                raise BaselineError("work sufficiency audit is invalid")
        result[key] = dict(sorted(projected.items()))
    return result


def _project_sha256_mapping(
    value: Any, *, allowed_keys: set[str]
) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str] = {}
    for key in sorted(set(value) & allowed_keys):
        digest = value[key]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise BaselineError("workbench artifact hash is invalid")
        result[key] = digest
    return result


def _project_workbench_provenance(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    attempt = value.get("attempt")
    if isinstance(attempt, int) and not isinstance(attempt, bool) and attempt >= 0:
        result["attempt"] = attempt
    event = value.get("event")
    if event is not None:
        result["event"] = _audit_token(event)
    for key in ("response_sha256", "workflow_outputs_sha256", "quality_audit_sha256"):
        digest = value.get(key)
        if digest is None:
            continue
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise BaselineError("workbench provenance hash is invalid")
        result[key] = digest
    return result


def _audit_token(value: Any) -> str:
    text = str(value or "")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{0,160}", text):
        raise BaselineError("workbench audit token is invalid")
    return text


def _rewrite_staged_artifact_hash_manifest(root: Path) -> str:
    artifacts: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if not path.is_file() or relative == "artifact_hash_manifest.json":
            continue
        data = path.read_bytes()
        artifacts[relative] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }
    canonical = json.dumps(
        artifacts, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    root_digest = hashlib.sha256(canonical).hexdigest()
    (root / "artifact_hash_manifest.json").write_text(
        serialize_baseline_data(
            {
                "schema_version": "quality-benchmark-artifact-hashes-v1",
                "artifacts": artifacts,
                "root_sha256": root_digest,
            }
        ),
        encoding="utf-8",
    )
    return root_digest


def _validate_artifact_hash_tree(root: Path, *, label: str) -> str:
    manifest = _read_json_mapping(
        root / "artifact_hash_manifest.json", f"{label} artifact hash manifest"
    )
    artifacts = _mapping(manifest.get("artifacts"), f"{label} artifact hashes")
    actual_files = {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file()
        and path.relative_to(root).as_posix() != "artifact_hash_manifest.json"
    }
    if set(actual_files) != set(artifacts):
        raise BaselineError(f"{label} artifact set does not match hash manifest")
    retained: dict[str, dict[str, Any]] = {}
    for relative, path in sorted(actual_files.items()):
        data = path.read_bytes()
        descriptor = _mapping(artifacts[relative], f"{label} hash {relative}")
        digest = hashlib.sha256(data).hexdigest()
        if descriptor.get("sha256") != digest or descriptor.get("size_bytes") != len(data):
            raise BaselineError(f"{label} artifact hash mismatch: {relative}")
        retained[relative] = {"sha256": digest, "size_bytes": len(data)}
    canonical = json.dumps(
        retained, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    root_digest = hashlib.sha256(canonical).hexdigest()
    if manifest.get("root_sha256") != root_digest:
        raise BaselineError(f"{label} artifact root hash mismatch")
    return root_digest


def _publish_staged_failures(
    staging: Path,
    failures: Sequence[tuple[str, Path, dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    root = staging / "generation_failures"
    root.mkdir()
    published: dict[str, dict[str, Any]] = {}
    for case_id, pair_root, payload in sorted(failures):
        destination = root / case_id
        if destination.exists():
            raise BaselineError(f"duplicate staged generation failure: {case_id}")
        pair_root.rename(destination)
        generator = destination / "generator"
        published[case_id] = {
            **payload,
            "artifact_root_sha256": _validate_artifact_hash_tree(
                generator, label="generation failure"
            ),
        }
    return published


def _require_tracked_corpus(
    identity: EvaluationCodeIdentity, corpus: QualityBaselineCorpusIdentity
) -> None:
    relative_paths = [
        "benchmarks/quality/registry.json",
        "benchmarks/quality/reviewer_authority.json",
    ]
    for case in corpus.cases:
        case_root = Path("benchmarks/quality/projects") / case.project_id / case.case_id
        relative_paths.append((case_root / "case.json").as_posix())
        relative_paths.extend(
            (case_root / truth_path).as_posix()
            for _, truth_path, _ in case.truth_sha256
        )
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(identity.repository_root),
            "ls-files",
            "--error-unmatch",
            "--",
            *relative_paths,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if completed.returncode != 0:
        raise BaselineError(
            "formal registry, reviewer authority, case descriptors, and truth files must be tracked by the clean CodeTalk revision"
        )


def _publish_staged_core(
    staging: Path, pairs: Sequence[tuple[str, Path]]
) -> list[Path]:
    runs_root = staging / "runs"
    runs_root.mkdir()
    result: list[Path] = []
    for case_id, pair_root in sorted(pairs):
        destination = runs_root / case_id
        if destination.exists():
            raise BaselineError(f"duplicate staged core case_id: {case_id}")
        pair_root.rename(destination)
        result.append(destination / "evaluation")
    return result


def _publish_staged_comparison(
    staging: Path,
    pairs: Sequence[tuple[str, Path]],
    profile: str,
) -> list[Path]:
    root = staging / "comparisons" / "rapid-deep"
    root.mkdir(parents=True, exist_ok=True)
    result: list[Path] = []
    for case_id, pair_root in sorted(pairs):
        destination = root / case_id / profile
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise BaselineError(f"duplicate {profile} comparison case_id: {case_id}")
        pair_root.rename(destination)
        result.append(destination / "evaluation")
    return result


def _copy_evaluation_once(source: Path, destination: Path) -> None:
    resolved = _safe_source_directory(source, "evaluation run")
    destination.mkdir(parents=True)
    for name in (REPORT_FILENAME, HUMAN_REPORT_FILENAME, MANIFEST_FILENAME):
        path = resolved / name
        if not path.is_file() or path.is_symlink():
            raise BaselineError(f"evaluation evidence is missing or unsafe: {path}")
        shutil.copy2(path, destination / name, follow_symlinks=False)


def _copy_generator_once(source: Path, destination: Path) -> None:
    resolved = _safe_source_directory(source, "generator run")
    destination.mkdir(parents=True)
    for name in GENERATOR_REQUIRED_DIRECTORIES:
        path = resolved / name
        _reject_symlinks(path, label=f"generator {name}")
        if not path.is_dir():
            raise BaselineError(f"generator evidence directory is missing: {path}")
        shutil.copytree(path, destination / name, symlinks=False)
    for name in GENERATOR_REQUIRED_FILES:
        path = resolved / name
        if not path.is_file() or path.is_symlink():
            raise BaselineError(f"generator evidence file is missing or unsafe: {path}")
        shutil.copy2(path, destination / name, follow_symlinks=False)
    for name in GENERATOR_OPTIONAL_FILES:
        path = resolved / name
        if path.exists():
            if not path.is_file() or path.is_symlink():
                raise BaselineError(f"generator evidence file is unsafe: {path}")
            shutil.copy2(path, destination / name, follow_symlinks=False)


def _validate_generator_evidence(
    generator: Path, evaluation: Any, *, expected_case: Any
) -> None:
    versions = _read_json_mapping(generator / "versions.json", "generator versions")
    manifest_versions = _mapping(evaluation.manifest.get("versions"), "versions")
    if versions != dict(manifest_versions):
        raise BaselineError("generator/evaluation version identity mismatch")

    generation = _read_json_mapping(
        generator / "generation_manifest.json", "generation manifest"
    )
    execution = _mapping(evaluation.manifest.get("execution"), "execution")
    checks = {
        "case_id": (generation.get("case_id"), evaluation.manifest.get("case_id")),
        "mode": (generation.get("mode"), execution.get("profile")),
        "model": (generation.get("model"), versions.get("model")),
        "codetalk_revision": (
            generation.get("codetalk_revision"),
            versions.get("codetalk"),
        ),
        "source_tree": (
            generation.get("source_tree"),
            expected_case.source_tree,
        ),
        "evaluation source_tree": (
            execution.get("generator_source_tree"),
            expected_case.source_tree,
        ),
    }
    for label, (observed, expected) in checks.items():
        if observed != expected:
            raise BaselineError(f"generator/evaluation {label} mismatch")
    elapsed = generation.get("elapsed_seconds")
    generation_wall = execution.get("generation_wall_clock_seconds")
    if (
        isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or isinstance(generation_wall, bool)
        or not isinstance(generation_wall, (int, float))
        or abs(float(elapsed) - float(generation_wall)) > 0.001
    ):
        raise BaselineError("generation elapsed time does not match execution evidence")

    repair = _read_json_mapping(generator / "repair_summary.json", "repair summary")
    expected_repair = evaluation.report.repair_summary.model_dump(mode="json")
    if repair != expected_repair:
        raise BaselineError("generator/evaluation repair summary mismatch")

    response_sha256 = str(generation.get("response_sha256") or "")
    retained_response_sha256 = hashlib.sha256(
        (generator / "benchmark_response.json").read_bytes()
    ).hexdigest()
    if retained_response_sha256 != response_sha256:
        raise BaselineError(
            "generator response hash does not match retained response bytes"
        )
    if execution.get("generator_response_sha256") != response_sha256:
        raise BaselineError("evaluation response authority mismatch")

    if generation.get("cache_reused") is True:
        workbench_audit = _read_json_mapping(
            generator / "workbench_audit.json", "workbench audit"
        )
        task_hashes = _mapping(
            workbench_audit.get("task_artifact_hashes"),
            "workbench task artifact hashes",
        )
        if task_hashes.get("benchmark_response.json") != response_sha256:
            raise BaselineError(
                "cached generator response is not bound to retained workbench evidence"
            )
        diagnostic = _mapping(
            generation.get("work_sufficiency"), "generator work sufficiency"
        )
        if diagnostic.get("reuse_source_sha256") != response_sha256:
            raise BaselineError(
                "cached reuse source does not match the retained generator response"
            )
    hash_manifest = _read_json_mapping(
        generator / "artifact_hash_manifest.json", "generator artifact hash manifest"
    )
    artifacts = _mapping(hash_manifest.get("artifacts"), "generator artifact hashes")
    manifest_ref = generation.get("artifact_hash_manifest")
    legacy_root = generation.get("artifact_root_sha256")
    current_contract = manifest_ref is not None
    legacy_contract = legacy_root is not None
    if current_contract == legacy_contract:
        raise BaselineError("generator artifact anchor contract is ambiguous")
    if current_contract:
        if manifest_ref != "artifact_hash_manifest.json":
            raise BaselineError("generator artifact manifest reference is invalid")
        actual_files = {
            path.relative_to(generator).as_posix(): path
            for path in generator.rglob("*")
            if path.is_file()
            and path.relative_to(generator).as_posix()
            != "artifact_hash_manifest.json"
        }
    else:
        actual_files = {
            path.relative_to(generator).as_posix(): path
            for root_name in GENERATOR_REQUIRED_DIRECTORIES
            for path in (generator / root_name).rglob("*")
            if path.is_file()
        }
    if set(actual_files) != set(artifacts):
        raise BaselineError("generator artifact set does not match hash manifest")
    retained: dict[str, dict[str, Any]] = {}
    for relative, path in sorted(actual_files.items()):
        data = path.read_bytes()
        descriptor = _mapping(artifacts[relative], f"generator hash {relative}")
        digest = hashlib.sha256(data).hexdigest()
        if descriptor.get("sha256") != digest or descriptor.get("size_bytes") != len(data):
            raise BaselineError(f"generator artifact hash mismatch: {relative}")
        retained[relative] = {"sha256": digest, "size_bytes": len(data)}
    canonical = json.dumps(
        retained, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    root_digest = hashlib.sha256(canonical).hexdigest()
    if hash_manifest.get("root_sha256") != root_digest:
        raise BaselineError("generator artifact root hash mismatch")
    if current_contract:
        if execution.get("generator_artifact_root_sha256") != root_digest:
            raise BaselineError("evaluation artifact root authority mismatch")
    elif legacy_root != root_digest:
        raise BaselineError("legacy generator artifact root mismatch")

    human_path = evaluation.directory / HUMAN_REPORT_FILENAME
    if hashlib.sha256(human_path.read_bytes()).hexdigest() != evaluation.manifest.get(
        "human_report_sha256"
    ):
        raise BaselineError("human evaluation report hash mismatch")


def _copy_tree_once(source: Path, destination: Path, *, label: str) -> None:
    resolved = _safe_source_directory(source, label)
    _reject_symlinks(resolved, label=label)
    shutil.copytree(resolved, destination, symlinks=False)


def _safe_source_directory(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise BaselineError(f"{label} must not be a symlink: {path}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise BaselineError(f"{label} is unavailable: {path}") from exc
    if not resolved.is_dir():
        raise BaselineError(f"{label} is not a directory: {resolved}")
    return resolved


def _reject_symlinks(root: Path, *, label: str) -> None:
    if root.is_symlink():
        raise BaselineError(f"{label} contains a symlink: {root}")
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.is_symlink():
            raise BaselineError(f"{label} contains a symlink: {path}")


def _source_run_hashes(
    run_directories: Sequence[str | Path],
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for directory in run_directories:
        root = Path(directory)
        manifest_path = root / MANIFEST_FILENAME
        report_path = root / REPORT_FILENAME
        human_path = root / HUMAN_REPORT_FILENAME
        manifest = _read_json_mapping(manifest_path, "quality evaluation manifest")
        case_id = str(manifest.get("case_id", ""))
        if not case_id or case_id in result:
            raise BaselineError("source run manifests require unique case_id values")
        result[case_id] = {
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            "human_report_sha256": hashlib.sha256(human_path.read_bytes()).hexdigest(),
        }
    return dict(sorted(result.items()))


def _environment_manifest(
    run_directories: Sequence[str | Path], identity: EvaluationCodeIdentity
) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    models: set[str] = set()
    for directory in run_directories:
        root = Path(directory)
        payload = _read_json_mapping(root / MANIFEST_FILENAME, "evaluation manifest")
        versions = _mapping(payload.get("versions"), "versions")
        models.add(str(versions.get("model")))
        runs.append(
            {
                "case_id": payload.get("case_id"),
                "run_ref": payload.get("run_ref"),
                "environment": payload.get("environment"),
                "execution": payload.get("execution"),
            }
        )
    return {
        "schema_version": "quality-baseline-environment-v2",
        "evaluation_identity": identity.as_dict(),
        "model": next(iter(models)) if len(models) == 1 else None,
        "runs": sorted(runs, key=lambda item: str(item["case_id"])),
    }


def _blocked_environment_manifest(
    run_directories: Sequence[str | Path],
    generation_failures: Mapping[str, Mapping[str, Any]],
    identity: EvaluationCodeIdentity,
) -> dict[str, Any]:
    result = _environment_manifest(run_directories, identity)
    result["schema_version"] = "quality-baseline-environment-v3"
    result["generation_failures"] = [
        {
            "case_id": case_id,
            "mode": payload.get("mode"),
            "model": payload.get("model"),
            "elapsed_seconds": payload.get("elapsed_seconds"),
            "timeout_seconds": payload.get("timeout_seconds"),
            "status": payload.get("status"),
            "failure_code": payload.get("failure_code"),
        }
        for case_id, payload in sorted(generation_failures.items())
    ]
    return result


def _source_failure_hashes(root: Path) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for case_root in sorted(root.iterdir()):
        generator = case_root / "generator"
        failure_path = generator / "generation_failure.json"
        manifest_path = generator / "artifact_hash_manifest.json"
        result[case_root.name] = {
            "generation_failure_sha256": hashlib.sha256(
                failure_path.read_bytes()
            ).hexdigest(),
            "artifact_hash_manifest_sha256": hashlib.sha256(
                manifest_path.read_bytes()
            ).hexdigest(),
        }
    return result


def _render_blocked_baseline_markdown(
    observation: Mapping[str, Any], release_gate: Mapping[str, Any]
) -> str:
    coverage = _mapping(observation.get("coverage"), "blocked baseline coverage")
    failures = _mapping(
        observation.get("generation_failures"), "generation failures"
    )
    lines = [
        "# F012 Independent Quality Baseline Observation",
        "",
        "Bundle status: blocked",
        (
            "Observation coverage: "
            f"{coverage['attempted']}/{coverage['expected']} "
            f"({coverage['evaluated']} evaluated, "
            f"{coverage['generation_failed']} generation-blocked)"
        ),
        "Threshold policy: not frozen",
        "",
        "## Generation failures",
        "",
    ]
    for case_id, payload in sorted(failures.items()):
        lines.append(
            f"- `{case_id}`: `{payload.get('status')}` / "
            f"`{payload.get('failure_code')}`"
        )
    lines.extend(["", "## Release gate", ""])
    for reason in release_gate.get("block_reasons") or []:
        lines.append(f"- `{reason}`")
    lines.append("")
    return "\n".join(lines)


def _render_baseline_markdown(
    summary: Mapping[str, Any],
    release_gate: Mapping[str, Any],
    regression: Mapping[str, Any],
) -> str:
    coverage = summary["coverage"]
    lines = [
        "# F012 Independent Quality Baseline",
        "",
        f"Bundle status: {'blocked' if regression['core_baseline_blocked'] else 'passed'}",
        f"Corpus coverage: {coverage['observed']}/{coverage['expected']}",
        "",
        "## Per-domain final distributions",
        "",
    ]
    for domain, axes in sorted(summary["domains"].items()):
        lines.extend([f"### {domain}", ""])
        for axis, metrics in sorted(axes.items()):
            lines.append(f"#### {axis.title()}")
            lines.append("")
            lines.append("| Metric | Min | Mean | P50 | P100 |")
            lines.append("|---|---:|---:|---:|---:|")
            for metric, phases in sorted(metrics.items()):
                final = phases["final"]
                lines.append(
                    f"| `{metric}` | {final['minimum']:.3f} | {final['mean']:.3f} | "
                    f"{final['p50']:.3f} | {final['p100']:.3f} |"
                )
            lines.append("")
    lines.extend(["## Independent release gates", ""])
    for axis, result in sorted(release_gate["axes"].items()):
        lines.append(f"- {axis.title()}: `{result['gate']}`")
    lines.extend(
        [
            f"- Delivery: `{release_gate['delivery_gate']}`",
            "",
            "## Timing",
            "",
            f"- Rapid: `{summary['timing']['rapid']['gate']}`",
            f"- Deep: `{summary['timing']['deep']['gate']}`",
            f"- Under-five-minute work sufficiency: `{summary['timing']['work_sufficiency_gate']}`",
            "",
        ]
    )
    return "\n".join(lines)


def _tree_hashes(root: Path, *, exclude: set[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in exclude:
            continue
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _make_tree_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    # macOS renamex_np requires the staging directory itself to remain writable.
    root.chmod(0o700)


def _make_tree_writable(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        try:
            if not path.is_symlink():
                path.chmod(0o700 if path.is_dir() else 0o600)
        except OSError:
            pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Freeze a reviewed F012 quality baseline bundle."
    )
    parser.add_argument("--runs-root", required=True, type=Path)
    parser.add_argument("--run-artifacts-root", required=True, type=Path)
    parser.add_argument("--rapid-runs-root", type=Path)
    parser.add_argument("--rapid-run-artifacts-root", type=Path)
    parser.add_argument("--deep-runs-root", type=Path)
    parser.add_argument("--deep-run-artifacts-root", type=Path)
    parser.add_argument(
        "--publish-blocked-on-generation-failure",
        action="store_true",
        help="Freeze complete attempted coverage when terminal generation failures exist.",
    )
    parser.add_argument(
        "--registry", default=Path("benchmarks/quality/registry.json"), type=Path
    )
    parser.add_argument(
        "--repository-root", default=Path(__file__).resolve().parents[3], type=Path
    )
    parser.add_argument("--thresholds", type=Path)
    parser.add_argument("--calibration-audit", type=Path)
    parser.add_argument(
        "--review-evidence", required=True, action="append", type=Path
    )
    parser.add_argument("--work-sufficiency-audit", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--previous-baseline", type=Path)
    args = parser.parse_args(argv)

    work_sufficiency_audit = _read_json_mapping(
        args.work_sufficiency_audit, "work sufficiency audit"
    )
    comparison_roots = (
        args.rapid_runs_root,
        args.rapid_run_artifacts_root,
        args.deep_runs_root,
        args.deep_run_artifacts_root,
    )
    if args.publish_blocked_on_generation_failure:
        if any(comparison_roots) and not all(comparison_roots):
            parser.error(
                "blocked rapid/deep comparison requires all four evidence roots"
            )
        if all(comparison_roots):
            rapid_runs, rapid_generators = _discover_evidence_pairs(
                args.rapid_runs_root, args.rapid_run_artifacts_root
            )
            deep_runs, deep_generators = _discover_evidence_pairs(
                args.deep_runs_root, args.deep_run_artifacts_root
            )
        else:
            rapid_runs, rapid_generators, deep_runs, deep_generators = [], [], [], []
        core_runs, core_generators, failures = _discover_observation_evidence(
            args.runs_root, args.run_artifacts_root
        )
        output = freeze_blocked_baseline_output(
            run_directories=core_runs,
            generator_directories=core_generators,
            failure_directories=failures,
            registry_path=args.registry,
            repository_root=args.repository_root,
            review_evidence_files=args.review_evidence,
            work_sufficiency_audit=work_sufficiency_audit,
            rapid_run_directories=rapid_runs,
            rapid_generator_directories=rapid_generators,
            deep_run_directories=deep_runs,
            deep_generator_directories=deep_generators,
            output_directory=args.output,
        )
    else:
        if (
            args.thresholds is None
            or args.calibration_audit is None
            or not all(comparison_roots)
        ):
            parser.error(
                "threshold, calibration, and rapid/deep evidence roots are "
                "required for a complete baseline"
            )
        rapid_runs, rapid_generators = _discover_evidence_pairs(
            args.rapid_runs_root, args.rapid_run_artifacts_root
        )
        deep_runs, deep_generators = _discover_evidence_pairs(
            args.deep_runs_root, args.deep_run_artifacts_root
        )
        core_runs, core_generators = _discover_evidence_pairs(
            args.runs_root, args.run_artifacts_root
        )
        output = freeze_baseline_output(
            run_directories=core_runs,
            generator_directories=core_generators,
            registry_path=args.registry,
            repository_root=args.repository_root,
            thresholds=_read_json_mapping(args.thresholds, "thresholds"),
            calibration_audit=_read_json_mapping(
                args.calibration_audit, "calibration audit"
            ),
            review_evidence_files=args.review_evidence,
            work_sufficiency_audit=work_sufficiency_audit,
            rapid_run_directories=rapid_runs,
            rapid_generator_directories=rapid_generators,
            deep_run_directories=deep_runs,
            deep_generator_directories=deep_generators,
            output_directory=args.output,
            previous_baseline_directory=args.previous_baseline,
        )
    print(output)
    if args.publish_blocked_on_generation_failure:
        return 2
    release = _read_json_mapping(output / "release_gate.json", "release gate")
    regression = _read_json_mapping(
        output / "regression_matrix.json", "regression matrix"
    )
    return (
        0
        if release.get("release_gate") == "pass"
        and regression.get("core_baseline_blocked") is False
        else 2
    )


def _discover_evidence_pairs(
    runs_root: Path, generator_root: Path
) -> tuple[list[Path], list[Path]]:
    runs = _discover_run_directories(runs_root)
    generators = _discover_generator_directories(generator_root)
    run_map = {_case_id_from_evaluation(path): path for path in runs}
    generator_map = {_case_id_from_generation(path): path for path in generators}
    if len(run_map) != len(runs) or len(generator_map) != len(generators):
        raise BaselineError("evidence roots contain duplicate case_id values")
    if set(run_map) != set(generator_map):
        raise BaselineError("evaluation/generator evidence case sets do not match")
    case_ids = sorted(run_map)
    return (
        [run_map[case_id] for case_id in case_ids],
        [generator_map[case_id] for case_id in case_ids],
    )


def _discover_observation_evidence(
    runs_root: Path, generator_root: Path
) -> tuple[list[Path], list[Path], list[Path]]:
    runs = _discover_run_directories(runs_root)
    resolved_generators = _safe_source_directory(generator_root, "generator root")
    successes: list[Path] = []
    failures: list[Path] = []
    for path in sorted(resolved_generators.iterdir()):
        if not path.is_dir() or path.is_symlink():
            continue
        has_success = (path / "generation_manifest.json").is_file()
        has_failure = (path / "generation_failure.json").is_file()
        if has_success == has_failure:
            raise BaselineError(
                "generator observation directory must contain exactly one terminal manifest"
            )
        (successes if has_success else failures).append(path)
    if not failures:
        raise BaselineError("blocked baseline requires generation failure evidence")
    run_map = {_case_id_from_evaluation(path): path for path in runs}
    success_map = {_case_id_from_generation(path): path for path in successes}
    failure_map = {
        str(
            _read_json_mapping(
                path / "generation_failure.json", "generation failure"
            ).get("case_id", "")
        ): path
        for path in failures
    }
    if (
        len(run_map) != len(runs)
        or len(success_map) != len(successes)
        or len(failure_map) != len(failures)
    ):
        raise BaselineError("observation roots contain duplicate case_id values")
    if set(run_map) != set(success_map):
        raise BaselineError("evaluation/successful generator case sets do not match")
    if set(success_map) & set(failure_map):
        raise BaselineError("case cannot contain both success and failure evidence")
    case_ids = sorted(run_map)
    return (
        [run_map[case_id] for case_id in case_ids],
        [success_map[case_id] for case_id in case_ids],
        [failure_map[case_id] for case_id in sorted(failure_map)],
    )


def _discover_run_directories(root: Path) -> list[Path]:
    resolved = _safe_source_directory(root, "evaluation root")
    directories = sorted(
        path
        for path in resolved.iterdir()
        if path.is_dir()
        and not path.is_symlink()
        and (path / REPORT_FILENAME).is_file()
        and (path / MANIFEST_FILENAME).is_file()
    )
    if not directories:
        raise BaselineError(f"no quality evaluation runs found under {resolved}")
    return directories


def _discover_generator_directories(root: Path) -> list[Path]:
    resolved = _safe_source_directory(root, "generator root")
    directories = sorted(
        path
        for path in resolved.iterdir()
        if path.is_dir()
        and not path.is_symlink()
        and (path / "generation_manifest.json").is_file()
    )
    if not directories:
        raise BaselineError(f"no generator runs found under {resolved}")
    return directories


def _case_id_from_evaluation(path: Path) -> str:
    return str(
        _read_json_mapping(path / MANIFEST_FILENAME, "evaluation manifest").get(
            "case_id", ""
        )
    )


def _case_id_from_generation(path: Path) -> str:
    return str(
        _read_json_mapping(path / "generation_manifest.json", "generation manifest").get(
            "case_id", ""
        )
    )


def _read_json_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BaselineError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise BaselineError(f"{label} must be an object")
    return value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BaselineError(f"{label} must be an object")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
