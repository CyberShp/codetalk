"""Deterministic baseline calibration and historical replay policy for F012."""

from __future__ import annotations

import hashlib
import json
import re
import statistics
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.services.quality_benchmark_corpus import (
    QualityBaselineCaseIdentity,
    QualityBaselineCorpusIdentity,
)
from app.services.quality_calibration_mutations import (
    build_quality_calibration_mutation_matrix,
)
from app.services.quality_evaluation_contract import (
    EVALUATOR_VERSION,
    MetricName,
    QualityEvaluationReport,
    validate_quality_evaluation,
)

REPORT_FILENAME = "quality_evaluation_report.json"
HUMAN_REPORT_FILENAME = "quality_evaluation_report.md"
MANIFEST_FILENAME = "quality_evaluation_manifest.json"
MANIFEST_SCHEMA_VERSION = "quality-evaluation-manifest-v1"
SUMMARY_SCHEMA_VERSION = "quality-baseline-summary-v3"
POLICY_SCHEMA_VERSION = "quality-threshold-policy-v3"
BUNDLE_SCHEMA_VERSION = "quality-baseline-bundle-v2"
AXES = ("accuracy", "breadth", "depth")
REQUIRED_DOMAINS = frozenset({"storage", "bmc", "kv-cache", "rdma-roce"})
EVALUATOR_SOURCE_PATHS = (
    "backend/app/services/quality_calibration_mutations.py",
    "backend/app/services/quality_evaluation_contract.py",
    "backend/app/services/quality_accuracy_evaluator.py",
    "backend/app/services/quality_breadth_evaluator.py",
    "backend/app/services/quality_depth_evaluator.py",
    "backend/app/services/quality_evaluator.py",
    "backend/app/services/quality_benchmark_runner.py",
    "backend/app/services/quality_benchmark_semantic_judge.py",
    "backend/app/services/behavior_claim_validator.py",
)
REQUIRED_METRICS = {
    "accuracy": frozenset(
        {MetricName.CLAIM_PRECISION.value, MetricName.GOLD_RECALL.value}
    ),
    "breadth": frozenset(
        {
            MetricName.DISCOVERY_RECALL.value,
            MetricName.CRITICAL_COVERAGE.value,
            MetricName.SCENARIO_REALIZATION.value,
            MetricName.DISPOSITION_COMPLETENESS.value,
        }
    ),
    "depth": frozenset(
        {
            MetricName.MINIMUM_CRITICAL_CHAIN_CLOSURE.value,
            MetricName.AVERAGE_CHAIN_CLOSURE.value,
            MetricName.STATE_CLOSURE.value,
            MetricName.RESOURCE_LIFECYCLE_CLOSURE.value,
            MetricName.ERROR_RECOVERY_CLOSURE.value,
            MetricName.DISCONFIRMING_CHECKS.value,
        }
    ),
}
CALIBRATION_CATEGORIES = (
    "false_passes",
    "false_failures",
    "missing_denominators",
    "unstable_evaluator",
)
REVIEW_AUTHORITY_SCHEMA_VERSION = "quality-review-authority-v1"
REVIEW_AUTHORITY_RELATIVE_PATH = Path("benchmarks/quality/reviewer_authority.json")
EVALUATION_IDENTITY_PATHS = (
    *EVALUATOR_SOURCE_PATHS,
    REVIEW_AUTHORITY_RELATIVE_PATH.as_posix(),
)
REVIEW_ASSIGNMENTS = frozenset(
    {
        *(f"calibration:{category}" for category in CALIBRATION_CATEGORIES),
        "work_sufficiency",
        "final_vision",
    }
)
FINAL_DISPOSITIONS = frozenset({"resolved", "accepted_limitation"})
TIMING_LIMITS_SECONDS = {"rapid": 900.0, "deep": 5400.0}


class BaselineError(ValueError):
    """Raised when immutable evidence or calibration policy is invalid."""


@dataclass(frozen=True)
class EvaluationCodeIdentity:
    """Identity derived from a clean CodeTalk commit and evaluator bytes."""

    repository_root: Path
    codetalk_revision: str
    evaluator_version: str
    evaluator_sha256: str

    def as_dict(self) -> dict[str, str]:
        return {
            "codetalk_revision": self.codetalk_revision,
            "evaluator_version": self.evaluator_version,
            "evaluator_sha256": self.evaluator_sha256,
        }


@dataclass(frozen=True)
class LoadedEvaluation:
    directory: Path
    report: QualityEvaluationReport
    manifest: dict[str, Any]


@dataclass(frozen=True)
class RapidDeepComparison:
    """Paired comparison produced only from verified immutable reports."""

    payload: dict[str, Any]


def _load_review_authority(repository_root: Path) -> dict[str, Any]:
    path = repository_root / REVIEW_AUTHORITY_RELATIVE_PATH
    try:
        data = path.read_bytes()
        payload = json.loads(data)
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineError("review authority is unavailable or invalid") from exc
    normalized = _validated_review_authority(payload)
    normalized["sha256"] = hashlib.sha256(data).hexdigest()
    return normalized


def _validated_review_authority(value: Any) -> dict[str, Any]:
    authority = _mapping(value, "review authority")
    if authority.get("schema_version") != REVIEW_AUTHORITY_SCHEMA_VERSION:
        raise BaselineError("unsupported review authority schema")
    author_ids = sorted(
        set(_nonempty_string_list(authority.get("author_ids"), "review authority authors"))
    )
    reviewers = authority.get("reviewers")
    if not isinstance(reviewers, (list, dict)) or not reviewers:
        raise BaselineError("review authority requires assigned reviewers")
    reviewer_values = reviewers.values() if isinstance(reviewers, dict) else reviewers
    normalized_reviewers: dict[str, dict[str, Any]] = {}
    for raw in reviewer_values:
        reviewer = _mapping(raw, "review authority reviewer")
        reviewer_id = _required_string(reviewer, "reviewer_id")
        role = _required_string(reviewer, "role")
        assignments = sorted(
            set(
                _nonempty_string_list(
                    reviewer.get("assignments"), "review authority assignments"
                )
            )
        )
        if reviewer_id in normalized_reviewers:
            raise BaselineError("review authority reviewer IDs must be unique")
        if reviewer_id in author_ids:
            raise BaselineError("review authority reviewer cannot be an author")
        if not set(assignments).issubset(REVIEW_ASSIGNMENTS):
            raise BaselineError("review authority contains an unknown assignment")
        normalized_reviewers[reviewer_id] = {
            "reviewer_id": reviewer_id,
            "role": role,
            "assignments": assignments,
        }
    for category in CALIBRATION_CATEGORIES:
        assignment = f"calibration:{category}"
        if sum(
            assignment in reviewer["assignments"]
            for reviewer in normalized_reviewers.values()
        ) < 2:
            raise BaselineError(
                f"review authority requires two reviewers for {assignment}"
            )
    if not any(
        "work_sufficiency" in reviewer["assignments"]
        for reviewer in normalized_reviewers.values()
    ):
        raise BaselineError("review authority requires a work-sufficiency reviewer")
    result = {
        "schema_version": REVIEW_AUTHORITY_SCHEMA_VERSION,
        "author_ids": author_ids,
        "reviewers": dict(sorted(normalized_reviewers.items())),
    }
    sha256 = authority.get("sha256")
    if sha256 is not None:
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise BaselineError("review authority sha256 is invalid")
        result["sha256"] = sha256
    return result


def _require_authorized_reviewer(
    review_authority: Mapping[str, Any],
    *,
    reviewer_id: str,
    role: str,
    assignment: str,
) -> None:
    reviewers = _mapping(review_authority.get("reviewers"), "review authority reviewers")
    reviewer = reviewers.get(reviewer_id)
    if not isinstance(reviewer, Mapping):
        raise BaselineError("reviewer is absent from the frozen review authority")
    if reviewer.get("role") != role:
        raise BaselineError("reviewer role does not match the frozen review authority")
    assignments = reviewer.get("assignments")
    if not isinstance(assignments, list) or assignment not in assignments:
        raise BaselineError("reviewer assignment does not match the frozen review authority")


def load_clean_evaluation_identity(
    repository_root: str | Path,
) -> EvaluationCodeIdentity:
    """Bind a baseline to a clean Git commit and the evaluator implementation."""

    try:
        root = Path(repository_root).resolve(strict=True)
    except OSError as exc:
        raise BaselineError(
            f"CodeTalk repository is unavailable: {repository_root}"
        ) from exc
    if not root.is_dir():
        raise BaselineError(f"CodeTalk repository is not a directory: {root}")
    top_level = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    if top_level != root:
        raise BaselineError("repository_root must be the CodeTalk Git top level")
    revision = _git(root, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise BaselineError("CodeTalk revision must be an immutable commit SHA")
    dirty = _git(root, "status", "--porcelain", "--untracked-files=all")
    if dirty:
        raise BaselineError("baseline freezing requires a clean CodeTalk worktree")

    digest = hashlib.sha256()
    for relative in EVALUATION_IDENTITY_PATHS:
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise BaselineError(f"evaluator source is missing or unsafe: {relative}")
        content = path.read_bytes()
        committed = subprocess.run(
            ["git", "-C", str(root), "show", f"HEAD:{relative}"],
            check=False,
            capture_output=True,
            timeout=15,
        )
        if committed.returncode != 0 or committed.stdout != content:
            raise BaselineError(
                f"evaluation identity file must match the clean revision: {relative}"
            )
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return EvaluationCodeIdentity(
        repository_root=root,
        codetalk_revision=revision,
        evaluator_version=EVALUATOR_VERSION,
        evaluator_sha256=digest.hexdigest(),
    )


def load_immutable_evaluation(
    run_directory: str | Path,
    *,
    expected_case: QualityBaselineCaseIdentity | Mapping[str, Any] | None = None,
    expected_identity: EvaluationCodeIdentity | None = None,
    require_execution: bool = False,
) -> LoadedEvaluation:
    """Load one report/manifest pair and close every declared identity chain."""

    directory = Path(run_directory).resolve()
    report_path = directory / REPORT_FILENAME
    manifest_path = directory / MANIFEST_FILENAME
    for path in (report_path, manifest_path):
        if not path.is_file() or path.is_symlink():
            raise BaselineError(f"immutable evaluation file is missing or unsafe: {path}")

    report_bytes = report_path.read_bytes()
    manifest = _read_mapping(manifest_path, "quality evaluation manifest")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise BaselineError("unsupported quality evaluation manifest schema")
    expected_digest = _required_string(manifest, "report_sha256")
    actual_digest = hashlib.sha256(report_bytes).hexdigest()
    if expected_digest != actual_digest:
        raise BaselineError("report hash does not match immutable manifest")

    try:
        report_payload = json.loads(report_bytes)
        report = validate_quality_evaluation(report_payload)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise BaselineError(f"invalid quality evaluation report: {exc}") from exc
    if report.benchmark_identity is None:
        raise BaselineError("baseline requires independent benchmark identity")

    identity_pairs = {
        "run_ref": (manifest.get("run_ref"), report.run_ref),
        "case_id": (manifest.get("case_id"), report.benchmark_identity.case_id),
        "source_revision": (
            manifest.get("source_revision"),
            report.benchmark_identity.source_revision,
        ),
        "truth_package_version": (
            manifest.get("truth_package_version"),
            report.benchmark_identity.truth_package_version,
        ),
    }
    for label, (manifest_value, report_value) in identity_pairs.items():
        if manifest_value != report_value:
            raise BaselineError(f"{label} identity mismatch between report and manifest")

    versions = _versions(manifest)
    if expected_identity is not None:
        if versions["codetalk"] != expected_identity.codetalk_revision:
            raise BaselineError("codetalk identity does not match the clean repository")
        if versions["evaluator"] != expected_identity.evaluator_version:
            raise BaselineError("evaluator identity does not match the clean repository")
    if expected_case is not None:
        _validate_expected_case(manifest, expected_case)
    _validate_execution(manifest.get("execution"), required=require_execution)
    return LoadedEvaluation(directory=directory, report=report, manifest=manifest)


def build_baseline_summary(
    run_directories: Sequence[str | Path],
    *,
    corpus: QualityBaselineCorpusIdentity,
    evaluation_identity: EvaluationCodeIdentity,
    work_sufficiency_audit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build per-project and per-domain distributions without combining axes."""

    review_authority = _load_review_authority(evaluation_identity.repository_root)
    expected_cases = corpus.case_map
    expected_ids = set(expected_cases)
    loaded: list[LoadedEvaluation] = []
    observed_ids: set[str] = set()
    models: set[str] = set()
    for run_directory in run_directories:
        preliminary = load_immutable_evaluation(
            run_directory,
            expected_identity=evaluation_identity,
            require_execution=True,
        )
        case_id = str(preliminary.manifest["case_id"])
        if case_id not in expected_cases:
            raise BaselineError(f"case_id is outside the formal corpus: {case_id}")
        if case_id in observed_ids:
            raise BaselineError(f"duplicate case_id in baseline: {case_id}")
        _validate_expected_case(preliminary.manifest, expected_cases[case_id])
        loaded.append(preliminary)
        observed_ids.add(case_id)
        models.add(_versions(preliminary.manifest)["model"])
    if len(models) > 1:
        raise BaselineError("default baseline requires one model identity")

    project_points: dict[str, Any] = {}
    domain_points: dict[str, Any] = {}
    critical_failures = {axis: 0 for axis in AXES}
    final_outcomes: dict[str, Any] = {}
    for item in sorted(loaded, key=lambda value: str(value.manifest["case_id"])):
        case_id = str(item.manifest["case_id"])
        case = expected_cases[case_id]
        _append_report_points(project_points, case.project_id, case_id, item.report)
        _append_report_points(domain_points, case.domain, case_id, item.report)
        final_outcomes[case_id] = {
            "delivery_status": item.report.delivery_status.value,
            "axes": {
                axis: getattr(item.report.final_after_auto_repair, axis).status.value
                for axis in AXES
            },
        }
        for axis in AXES:
            result = getattr(item.report.final_after_auto_repair, axis)
            critical_failures[axis] += len(result.critical_misses)

    calibration_mutations = build_quality_calibration_mutation_matrix()
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "identity": {
            "corpus": corpus.as_dict(),
            "evaluation": evaluation_identity.as_dict(),
            "model": next(iter(models), None),
        },
        "review_authority": review_authority,
        "calibration_mutations": calibration_mutations,
        "coverage": {
            "expected": len(expected_ids),
            "observed": len(observed_ids),
            "missing_case_ids": sorted(expected_ids - observed_ids),
        },
        "projects": _finalize_groups(project_points),
        "domains": _finalize_groups(domain_points),
        "validation_layers": {
            "L3": _validation_layer_summary(
                loaded,
                expected_cases,
                layer="l3",
            )
        },
        "critical_failures": critical_failures,
        "final_outcomes": final_outcomes,
        "timing": _timing_summary(
            loaded,
            work_sufficiency_audit or {},
            review_authority=review_authority,
        ),
    }


def freeze_threshold_policy(
    summary: Mapping[str, Any],
    *,
    thresholds: Mapping[str, Mapping[str, float]],
    calibration_audit: Mapping[str, Any],
) -> dict[str, Any]:
    """Freeze independent thresholds only after complete, evidenced approval."""

    coverage = _mapping(summary.get("coverage"), "baseline coverage")
    if (
        coverage.get("expected") != 12
        or coverage.get("observed") != 12
        or coverage.get("missing_case_ids") != []
    ):
        raise BaselineError("threshold freeze requires the complete 12-case corpus")
    projects = _mapping(summary.get("projects"), "project distributions")
    if len(projects) != 12:
        raise BaselineError("threshold freeze requires 12 distinct benchmark projects")
    domains = _mapping(summary.get("domains"), "domain distributions")
    if set(domains) != REQUIRED_DOMAINS:
        raise BaselineError(
            "threshold freeze requires storage, bmc, kv-cache, and rdma-roce domains"
        )
    if set(thresholds) != set(AXES):
        raise BaselineError("thresholds must contain exactly three independent axes")
    normalized: dict[str, dict[str, float]] = {}
    for axis in AXES:
        axis_thresholds = thresholds[axis]
        if set(axis_thresholds) != REQUIRED_METRICS[axis]:
            raise BaselineError(f"{axis} thresholds must cover every raw metric")
        normalized[axis] = {}
        for metric, value in sorted(axis_thresholds.items()):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise BaselineError(f"{axis}.{metric} threshold must be numeric")
            if not 0.0 <= float(value) <= 1.0:
                raise BaselineError(f"{axis}.{metric} threshold must be in [0, 1]")
            normalized[axis][metric] = float(value)

    derived_thresholds, threshold_derivation = _derive_thresholds(summary)
    if normalized != derived_thresholds:
        raise BaselineError(
            "thresholds must equal the deterministically derived final distributions"
        )

    review_authority = _validated_review_authority(
        summary.get("review_authority")
    )
    calibration_author_ids = set(
        _nonempty_string_list(
            calibration_audit.get("author_ids"), "calibration author_ids"
        )
    )
    authority_authors = set(review_authority["author_ids"])
    authority_reviewer_ids = set(review_authority["reviewers"])
    if calibration_author_ids & authority_reviewer_ids:
        raise BaselineError("calibration reviewer cannot be an author")
    if calibration_author_ids != authority_authors:
        raise BaselineError(
            "calibration authors do not match the frozen review authority"
        )
    retained_audit = {
        category: _validate_calibration_review(
            category,
            calibration_audit.get(category),
            author_ids=calibration_author_ids,
            review_authority=review_authority,
        )
        for category in CALIBRATION_CATEGORIES
    }
    retained_audit["author_ids"] = sorted(calibration_author_ids)
    identity = _mapping(summary.get("identity"), "baseline identity")
    corpus_identity = _mapping(identity.get("corpus"), "corpus identity")
    evaluation = _mapping(identity.get("evaluation"), "evaluation identity")
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "corpus_sha256": _required_string(corpus_identity, "corpus_sha256"),
        "registry_sha256": _required_string(corpus_identity, "registry_sha256"),
        "codetalk_revision": _required_string(evaluation, "codetalk_revision"),
        "evaluator_version": _required_string(evaluation, "evaluator_version"),
        "evaluator_sha256": _required_string(evaluation, "evaluator_sha256"),
        "review_authority_sha256": review_authority["sha256"],
        "thresholds": normalized,
        "threshold_derivation": threshold_derivation,
        "calibration_gate": threshold_derivation["calibration_gate"],
        "critical_failure_gate": {axis: {"maximum": 0} for axis in AXES},
        "timing_limits_seconds": dict(TIMING_LIMITS_SECONDS),
        "calibration_audit": retained_audit,
    }


def evaluate_release_policy(
    summary: Mapping[str, Any], policy: Mapping[str, Any]
) -> dict[str, Any]:
    """Evaluate status, raw metrics, critical misses, and timing independently."""

    if policy.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise BaselineError("unsupported threshold policy schema")
    identity = _mapping(summary.get("identity"), "baseline identity")
    corpus = _mapping(identity.get("corpus"), "corpus identity")
    evaluation = _mapping(identity.get("evaluation"), "evaluation identity")
    identity_pairs = {
        "corpus": (corpus.get("corpus_sha256"), policy.get("corpus_sha256")),
        "registry": (corpus.get("registry_sha256"), policy.get("registry_sha256")),
        "codetalk": (
            evaluation.get("codetalk_revision"),
            policy.get("codetalk_revision"),
        ),
        "evaluator version": (
            evaluation.get("evaluator_version"),
            policy.get("evaluator_version"),
        ),
        "evaluator bytes": (
            evaluation.get("evaluator_sha256"),
            policy.get("evaluator_sha256"),
        ),
    }
    for label, (observed, expected) in identity_pairs.items():
        if observed != expected:
            raise BaselineError(
                f"release {label} identity does not match threshold policy"
            )

    domains = _mapping(summary.get("domains"), "domain distributions")
    failures = _mapping(summary.get("critical_failures"), "critical failures")
    outcomes = _mapping(summary.get("final_outcomes"), "final outcomes")
    corpus_cases = _mapping(corpus.get("cases"), "corpus cases")
    if set(outcomes) != set(corpus_cases):
        raise BaselineError("final outcomes must cover the formal 12-case corpus")
    policy_thresholds = _mapping(policy.get("thresholds"), "policy thresholds")
    calibration_gate = policy.get("calibration_gate")
    if calibration_gate not in {"pass", "fail"}:
        raise BaselineError("threshold policy calibration_gate is invalid")
    axis_results: dict[str, Any] = {}
    for axis in AXES:
        metric_results: dict[str, Any] = {}
        for metric, threshold in sorted(
            _mapping(policy_thresholds.get(axis), f"{axis} policy").items()
        ):
            observed: list[float] = []
            for domain_name, domain in sorted(domains.items()):
                axis_data = _mapping(_mapping(domain, domain_name).get(axis), axis)
                metric_data = _mapping(axis_data.get(metric), metric)
                final = _mapping(metric_data.get("final"), f"{metric} final distribution")
                minimum = final.get("minimum")
                if not isinstance(minimum, (int, float)) or isinstance(minimum, bool):
                    raise BaselineError(f"missing final denominator for {axis}.{metric}")
                observed.append(float(minimum))
            observed_minimum = min(observed) if observed else None
            gate = (
                "pass"
                if observed_minimum is not None
                and observed_minimum >= float(threshold)
                else "fail"
            )
            metric_results[metric] = {
                "gate": gate,
                "minimum": observed_minimum,
                "threshold": float(threshold),
            }
        metric_gate = (
            "pass"
            if metric_results
            and all(item["gate"] == "pass" for item in metric_results.values())
            else "fail"
        )
        critical_count = failures.get(axis)
        if not isinstance(critical_count, int) or isinstance(critical_count, bool):
            raise BaselineError(f"invalid {axis} critical failure count")
        critical_gate = "pass" if critical_count == 0 else "fail"
        statuses = []
        for case_id, outcome_value in sorted(outcomes.items()):
            outcome = _mapping(outcome_value, f"{case_id} final outcome")
            axes = _mapping(outcome.get("axes"), f"{case_id} axes")
            status = axes.get(axis)
            if status not in {"pass", "limited", "fail"}:
                raise BaselineError(f"invalid final {axis} status for {case_id}")
            statuses.append(status)
        status_gate = "fail" if "fail" in statuses else "pass"
        axis_results[axis] = {
            "gate": (
                "pass"
                if metric_gate == critical_gate == status_gate == "pass"
                else "fail"
            ),
            "metric_gate": metric_gate,
            "critical_gate": critical_gate,
            "status_gate": status_gate,
            "critical_failure_count": critical_count,
            "final_statuses": {
                status: statuses.count(status) for status in ("pass", "limited", "fail")
            },
            "metrics": metric_results,
        }

    delivery_statuses = []
    for case_id, outcome_value in sorted(outcomes.items()):
        outcome = _mapping(outcome_value, f"{case_id} final outcome")
        status = outcome.get("delivery_status")
        if status not in {"ready", "limited", "not_ready"}:
            raise BaselineError(f"invalid delivery status for {case_id}")
        delivery_statuses.append(status)
    delivery_gate = "fail" if "not_ready" in delivery_statuses else "pass"

    timing = _mapping(summary.get("timing"), "timing summary")
    timing_gate = {
        profile: _mapping(timing.get(profile), profile).get("gate", "not_run")
        for profile in ("rapid", "deep")
    }
    timing_gate["work_sufficiency"] = timing.get(
        "work_sufficiency_gate", "fail"
    )
    observed_profile_gates = [timing_gate[profile] for profile in ("rapid", "deep")]
    profile_timing_gate = (
        "pass"
        if "pass" in observed_profile_gates and "fail" not in observed_profile_gates
        else "fail"
    )
    release_gate = (
        "pass"
        if all(item["gate"] == "pass" for item in axis_results.values())
        and calibration_gate == "pass"
        and delivery_gate == "pass"
        and profile_timing_gate == "pass"
        and timing_gate["work_sufficiency"] == "pass"
        else "fail"
    )
    return {
        "axes": axis_results,
        "calibration_gate": calibration_gate,
        "delivery_gate": delivery_gate,
        "delivery_statuses": {
            status: delivery_statuses.count(status)
            for status in ("ready", "limited", "not_ready")
        },
        "timing": timing_gate,
        "release_gate": release_gate,
    }


def compare_historical_replay(
    current_run_directories: Sequence[str | Path],
    previous_baseline_directory: str | Path | Sequence[str | Path] | None,
) -> dict[str, Any]:
    """Compare only against a hash-verified, previously accepted frozen bundle."""

    if previous_baseline_directory is None:
        return {
            "status": "not_run",
            "reason": "previous_baseline_unavailable",
            "regressions": [],
        }
    if not isinstance(previous_baseline_directory, (str, Path)):
        return {
            "status": "not_run",
            "reason": "previous_baseline_not_proven_accepted",
            "regressions": [],
        }
    accepted = _accepted_bundle_runs(previous_baseline_directory)
    if accepted is None:
        return {
            "status": "not_run",
            "reason": "previous_baseline_not_accepted",
            "regressions": [],
        }
    previous_run_directories, previous_bundle_manifest = accepted
    current = _load_by_case(current_run_directories, "current")
    previous = _load_by_case(previous_run_directories, "previous")
    if set(current) != set(previous):
        raise BaselineError("historical replay case_id set mismatch")

    regressions: list[dict[str, Any]] = []
    for case_id in sorted(current):
        current_item = current[case_id]
        previous_item = previous[case_id]
        comparisons = {
            "project_id": (
                current_item.manifest.get("project_id"),
                previous_item.manifest.get("project_id"),
            ),
            "source_revision": (
                current_item.manifest.get("source_revision"),
                previous_item.manifest.get("source_revision"),
            ),
            "truth_package_version": (
                current_item.manifest.get("truth_package_version"),
                previous_item.manifest.get("truth_package_version"),
            ),
            "evaluator": (
                _versions(current_item.manifest).get("evaluator"),
                _versions(previous_item.manifest).get("evaluator"),
            ),
        }
        for label, (current_value, previous_value) in comparisons.items():
            if current_value != previous_value:
                raise BaselineError(
                    f"historical replay {label} mismatch for case {case_id}"
                )
        for phase in ("first_pass", "final_after_auto_repair"):
            current_snapshot = getattr(current_item.report, phase)
            previous_snapshot = getattr(previous_item.report, phase)
            for axis in AXES:
                current_axis = getattr(current_snapshot, axis)
                previous_axis = getattr(previous_snapshot, axis)
                current_metrics = {
                    metric.name.value: metric for metric in current_axis.metrics
                }
                previous_metrics = {
                    metric.name.value: metric for metric in previous_axis.metrics
                }
                if set(current_metrics) != set(previous_metrics):
                    raise BaselineError(
                        f"historical replay metric set mismatch for {case_id} {axis}"
                    )
                for metric in sorted(current_metrics):
                    current_ratio = _ratio(
                        current_metrics[metric].numerator,
                        current_metrics[metric].denominator,
                    )
                    previous_ratio = _ratio(
                        previous_metrics[metric].numerator,
                        previous_metrics[metric].denominator,
                    )
                    if current_ratio < previous_ratio:
                        regressions.append(
                            {
                                "case_id": case_id,
                                "phase": phase,
                                "axis": axis,
                                "metric": metric,
                                "previous": previous_ratio,
                                "current": current_ratio,
                                "delta": _round(current_ratio - previous_ratio),
                            }
                        )
                current_critical = len(current_axis.critical_misses)
                previous_critical = len(previous_axis.critical_misses)
                if current_critical > previous_critical:
                    regressions.append(
                        {
                            "case_id": case_id,
                            "phase": phase,
                            "axis": axis,
                            "kind": "critical_failure",
                            "previous": previous_critical,
                            "current": current_critical,
                            "delta": current_critical - previous_critical,
                        }
                    )
    current_versions = _uniform_versions(current.values(), "current")
    previous_versions = _uniform_versions(previous.values(), "previous")
    bundle_identity = _mapping(
        previous_bundle_manifest.get("evaluation_identity"),
        "previous bundle evaluation identity",
    )
    return {
        "status": "compared",
        "truth_oracle": "hidden_truth_package",
        "identity": {
            "previous": {
                "codetalk_revision": previous_versions["codetalk"],
                "model": previous_versions["model"],
                "evaluator": previous_versions["evaluator"],
                "evaluator_sha256": bundle_identity.get("evaluator_sha256"),
            },
            "current": {
                "codetalk_revision": current_versions["codetalk"],
                "model": current_versions["model"],
                "evaluator": current_versions["evaluator"],
            },
        },
        "regressions": regressions,
    }


def compare_rapid_deep_runs(
    rapid_run_directories: Sequence[str | Path],
    deep_run_directories: Sequence[str | Path],
    *,
    corpus: QualityBaselineCorpusIdentity,
    evaluation_identity: EvaluationCodeIdentity,
    work_sufficiency_audit: Mapping[str, Any] | None = None,
) -> RapidDeepComparison:
    """Compute a stratified comparison from same-case immutable report pairs."""

    review_authority = _load_review_authority(evaluation_identity.repository_root)
    rapid = _load_by_case(
        rapid_run_directories,
        "rapid",
        corpus=corpus,
        evaluation_identity=evaluation_identity,
        require_execution=True,
    )
    deep = _load_by_case(
        deep_run_directories,
        "deep",
        corpus=corpus,
        evaluation_identity=evaluation_identity,
        require_execution=True,
    )
    if not rapid or set(rapid) != set(deep):
        raise BaselineError("rapid/deep comparison requires same-case paired runs")
    case_map = corpus.case_map
    domains = {case_map[case_id].domain for case_id in rapid}
    if domains != REQUIRED_DOMAINS:
        raise BaselineError(
            "rapid/deep comparison must be stratified across all four domains"
        )

    audit = work_sufficiency_audit or {}
    pairs: list[dict[str, Any]] = []
    for case_id in sorted(rapid):
        rapid_item = rapid[case_id]
        deep_item = deep[case_id]
        rapid_execution = _mapping(
            rapid_item.manifest.get("execution"), "rapid execution"
        )
        deep_execution = _mapping(
            deep_item.manifest.get("execution"), "deep execution"
        )
        if rapid_execution.get("profile") != "rapid":
            raise BaselineError(f"rapid pair has non-rapid profile for {case_id}")
        if deep_execution.get("profile") != "deep":
            raise BaselineError(f"deep pair has non-deep profile for {case_id}")
        rapid_versions = _versions(rapid_item.manifest)
        deep_versions = _versions(deep_item.manifest)
        if rapid_versions != deep_versions:
            raise BaselineError(f"rapid/deep version identity mismatch for {case_id}")
        deltas: dict[str, Any] = {}
        for axis in AXES:
            rapid_axis = getattr(rapid_item.report.final_after_auto_repair, axis)
            deep_axis = getattr(deep_item.report.final_after_auto_repair, axis)
            rapid_metrics = {item.name.value: item for item in rapid_axis.metrics}
            deep_metrics = {item.name.value: item for item in deep_axis.metrics}
            if set(rapid_metrics) != set(deep_metrics):
                raise BaselineError(
                    f"rapid/deep metric set mismatch for {case_id} {axis}"
                )
            deltas[axis] = {
                metric: _round(
                    _ratio(
                        deep_metrics[metric].numerator,
                        deep_metrics[metric].denominator,
                    )
                    - _ratio(
                        rapid_metrics[metric].numerator,
                        rapid_metrics[metric].denominator,
                    )
                )
                for metric in sorted(rapid_metrics)
            }
        pairs.append(
            {
                "case_id": case_id,
                "domain": case_map[case_id].domain,
                "identity": {
                    "project_id": rapid_item.manifest["project_id"],
                    "source_revision": rapid_item.manifest["source_revision"],
                    "truth_package_version": rapid_item.manifest[
                        "truth_package_version"
                    ],
                    "versions": rapid_versions,
                },
                "rapid": {
                    "run_ref": rapid_item.manifest["run_ref"],
                    "report_sha256": rapid_item.manifest["report_sha256"],
                    "wall_clock_seconds": rapid_execution["wall_clock_seconds"],
                },
                "deep": {
                    "run_ref": deep_item.manifest["run_ref"],
                    "report_sha256": deep_item.manifest["report_sha256"],
                    "wall_clock_seconds": deep_execution["wall_clock_seconds"],
                },
                "final_metric_delta_deep_minus_rapid": deltas,
            }
        )

    timing = _timing_summary(
        [*rapid.values(), *deep.values()],
        audit,
        review_authority=review_authority,
    )
    payload = {
        "status": "complete",
        "evidence_kind": "paired_immutable_reports",
        "case_ids": sorted(rapid),
        "domains": sorted(domains),
        "timing": timing,
        "pairs": pairs,
    }
    payload["comparison_sha256"] = _canonical_sha256(payload)
    return RapidDeepComparison(payload=payload)


def build_regression_matrix(
    *,
    release_gate: Mapping[str, Any],
    historical_replay: Mapping[str, Any],
    rapid_deep_comparison: RapidDeepComparison | None,
    alternative_model_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Record model cells as regression samples, never as a truth vote."""

    alternative = (
        dict(alternative_model_result)
        if alternative_model_result is not None
        else {"status": "not_run", "reason": "alternative_model_unavailable"}
    )
    rapid_deep = (
        dict(rapid_deep_comparison.payload)
        if rapid_deep_comparison is not None
        else {"status": "not_run", "reason": "paired_rapid_deep_evidence_unavailable"}
    )
    rapid_timing = rapid_deep.get("timing")
    paired_gate = False
    if isinstance(rapid_timing, Mapping):
        paired_gate = (
            _mapping(rapid_timing.get("rapid"), "rapid timing").get("gate")
            == "pass"
            and _mapping(rapid_timing.get("deep"), "deep timing").get("gate")
            == "pass"
            and rapid_timing.get("work_sufficiency_gate") == "pass"
        )
    default = {
        "status": "complete",
        "release_gate": dict(release_gate),
        "historical_replay": dict(historical_replay),
    }
    return {
        "truth_oracle": "hidden_truth_package",
        "default_model": default,
        "alternative_model": alternative,
        "rapid_vs_deep": rapid_deep,
        "core_baseline_blocked": (
            release_gate.get("release_gate") != "pass"
            or rapid_deep.get("status") != "complete"
            or not paired_gate
        ),
    }


def render_human_baseline(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Return deterministic presentation data without duplicating evaluation logic."""

    domains = _mapping(summary.get("domains"), "domain distributions")
    rows = []
    for domain, axis_data in sorted(domains.items()):
        rows.append(
            {
                "domain": domain,
                "axes": [
                    {
                        "axis": axis,
                        "metrics": [
                            {
                                "metric": metric,
                                "first": values["first"],
                                "final": values["final"],
                            }
                            for metric, values in sorted(axis_data[axis].items())
                        ],
                    }
                    for axis in AXES
                ],
            }
        )
    return {
        "schema_version": "quality-baseline-human-v2",
        "coverage": dict(_mapping(summary.get("coverage"), "coverage")),
        "domains": rows,
        "timing": dict(_mapping(summary.get("timing"), "timing")),
    }


def serialize_baseline_data(value: Mapping[str, Any]) -> str:
    """Serialize machine-readable baseline data to canonical JSON bytes."""

    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ) + "\n"


def _append_report_points(
    groups: dict[str, Any],
    group_name: str,
    case_id: str,
    report: QualityEvaluationReport,
) -> None:
    group = groups.setdefault(group_name, {})
    for axis in AXES:
        axis_data = group.setdefault(axis, {})
        for phase in ("first_pass", "final_after_auto_repair"):
            result = getattr(getattr(report, phase), axis)
            for metric in result.metrics:
                metric_data = axis_data.setdefault(
                    metric.name.value, {"first": [], "final": []}
                )
                phase_name = "first" if phase == "first_pass" else "final"
                metric_data[phase_name].append(
                    {
                        "case_id": case_id,
                        "numerator": metric.numerator,
                        "denominator": metric.denominator,
                        "ratio": _ratio(metric.numerator, metric.denominator),
                    }
                )


def _finalize_groups(groups: Mapping[str, Any]) -> dict[str, Any]:
    finalized: dict[str, Any] = {}
    for group_name, axes in sorted(groups.items()):
        finalized[group_name] = {}
        for axis in AXES:
            finalized[group_name][axis] = {}
            for metric, phases in sorted(axes.get(axis, {}).items()):
                finalized[group_name][axis][metric] = {
                    phase: _distribution(points)
                    for phase, points in sorted(phases.items())
                }
    return finalized


def _validation_layer_summary(
    loaded: Sequence[LoadedEvaluation],
    cases: Mapping[str, QualityBaselineCaseIdentity],
    *,
    layer: str,
) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {"projects": {}, "domains": {}}
    for item in loaded:
        case_id = str(item.manifest["case_id"])
        case = cases[case_id]
        for grouping, group_name in (
            ("projects", case.project_id),
            ("domains", case.domain),
        ):
            group = grouped[grouping].setdefault(group_name, {})
            for axis in AXES:
                axis_data = group.setdefault(axis, {"first": [], "final": []})
                for phase, phase_name in (
                    ("first_pass", "first"),
                    ("final_after_auto_repair", "final"),
                ):
                    result = getattr(getattr(item.report, phase), axis)
                    outcome = getattr(result.validation_layers, layer)
                    axis_data[phase_name].append(
                        {
                            "case_id": case_id,
                            "status": outcome.status.value,
                            "numerator": outcome.numerator,
                            "denominator": outcome.denominator,
                            "limitations": sorted(outcome.limitations),
                        }
                    )

    result: dict[str, Any] = {}
    for grouping, groups in grouped.items():
        result[grouping] = {}
        for group_name, axes in sorted(groups.items()):
            result[grouping][group_name] = {}
            for axis, phases in sorted(axes.items()):
                result[grouping][group_name][axis] = {}
                for phase, samples in sorted(phases.items()):
                    ordered = sorted(samples, key=lambda sample: sample["case_id"])
                    statuses: dict[str, int] = {}
                    limitations: dict[str, int] = {}
                    for sample in ordered:
                        statuses[sample["status"]] = statuses.get(sample["status"], 0) + 1
                        for limitation in sample["limitations"]:
                            limitations[limitation] = limitations.get(limitation, 0) + 1
                    result[grouping][group_name][axis][phase] = {
                        "status_counts": dict(sorted(statuses.items())),
                        "limitation_counts": dict(sorted(limitations.items())),
                        "numerator_total": sum(sample["numerator"] for sample in ordered),
                        "denominator_total": sum(sample["denominator"] for sample in ordered),
                        "samples": ordered,
                    }
    return result


def _derive_thresholds(
    summary: Mapping[str, Any],
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    domains = _mapping(summary.get("domains"), "domain distributions")
    if set(domains) != REQUIRED_DOMAINS:
        raise BaselineError("threshold derivation requires every formal domain")
    expected_mutations = build_quality_calibration_mutation_matrix()
    observed_mutations = _mapping(
        summary.get("calibration_mutations"), "calibration mutation matrix"
    )
    if observed_mutations != expected_mutations:
        raise BaselineError(
            "calibration mutation matrix does not match the current evaluator"
        )
    mutation_axes = _mapping(
        observed_mutations.get("mutations"), "calibration mutations"
    )
    thresholds: dict[str, dict[str, float]] = {}
    metrics: dict[str, dict[str, Any]] = {}
    for axis in AXES:
        thresholds[axis] = {}
        metrics[axis] = {}
        for metric in sorted(REQUIRED_METRICS[axis]):
            minima: dict[str, float] = {}
            for domain_name, domain_value in sorted(domains.items()):
                domain = _mapping(domain_value, domain_name)
                axis_data = _mapping(domain.get(axis), f"{domain_name}.{axis}")
                metric_data = _mapping(
                    axis_data.get(metric), f"{domain_name}.{axis}.{metric}"
                )
                final = _mapping(metric_data.get("final"), "final distribution")
                minimum = final.get("minimum")
                if isinstance(minimum, bool) or not isinstance(minimum, (int, float)):
                    raise BaselineError(
                        f"threshold derivation requires {domain_name}.{axis}.{metric}"
                    )
                minima[domain_name] = _round(float(minimum))
            axis_mutations = _mapping(
                mutation_axes.get(axis), f"{axis} calibration mutations"
            )
            mutation = _mapping(
                axis_mutations.get(metric), f"{axis}.{metric} calibration mutation"
            )
            baseline = _mapping(mutation.get("baseline"), "mutation baseline")
            mutated = _mapping(mutation.get("mutated"), "mutation result")
            threshold = 1.0
            unacceptable_maximum = float(mutated.get("ratio", 1.0))
            status = (
                "calibrated"
                if baseline.get("ratio") == threshold
                and unacceptable_maximum < threshold
                and mutation.get("mutated_axis_status") == "fail"
                and mutation.get("expected_axis_status") == "fail"
                else "not_calibratable"
            )
            thresholds[axis][metric] = threshold
            metrics[axis][metric] = {
                "domain_final_minima": minima,
                "observed_final_minimum": min(minima.values()),
                "mutation_replay": dict(mutation),
                "acceptable_minimum": threshold,
                "unacceptable_maximum": unacceptable_maximum,
                "status": status,
                "threshold": threshold,
            }
    calibration_gate = (
        "pass"
        if all(
            metric["status"] == "calibrated"
            for axis_metrics in metrics.values()
            for metric in axis_metrics.values()
        )
        else "fail"
    )
    return thresholds, {
        "schema_version": "quality-threshold-derivation-v2",
        "algorithm": "actual-evaluator-one-obligation-mutation-v1",
        "summary_sha256": _canonical_sha256(summary),
        "mutation_matrix_sha256": observed_mutations["matrix_sha256"],
        "calibration_gate": calibration_gate,
        "metrics": metrics,
    }


def _distribution(points: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    samples = sorted((dict(point) for point in points), key=lambda item: item["case_id"])
    ratios = [float(item["ratio"]) for item in samples]
    return {
        "count": len(samples),
        "minimum": min(ratios),
        "maximum": max(ratios),
        "mean": _round(statistics.fmean(ratios)),
        "p50": _round(statistics.median(ratios)),
        "p100": max(ratios),
        "samples": samples,
    }


def _timing_summary(
    loaded: Sequence[LoadedEvaluation],
    work_sufficiency_audit: Mapping[str, Any],
    *,
    review_authority: Mapping[str, Any],
) -> dict[str, Any]:
    profiles: dict[str, list[tuple[str, str, float]]] = {"rapid": [], "deep": []}
    under_five: list[dict[str, Any]] = []
    work_gate = "pass"
    for item in loaded:
        execution = _mapping(item.manifest.get("execution"), "execution")
        profile = str(execution["profile"])
        wall = float(execution["wall_clock_seconds"])
        case_id = str(item.manifest["case_id"])
        run_ref = str(item.manifest["run_ref"])
        profiles[profile].append((case_id, run_ref, wall))
        if wall < 300.0:
            disposition = _work_sufficiency_disposition(
                run_ref,
                work_sufficiency_audit.get(run_ref),
                case_id=case_id,
                report_sha256=_required_string(item.manifest, "report_sha256"),
                execution=execution,
                review_authority=review_authority,
            )
            if disposition["gate"] != "pass":
                work_gate = "fail"
            under_five.append(
                {
                    "case_id": case_id,
                    "run_ref": run_ref,
                    "cache_reuse": bool(execution["cache_reuse"]),
                    "wall_clock_seconds": wall,
                    "generator_marker": execution.get("work_sufficiency"),
                    "independent_disposition": disposition,
                }
            )
    result: dict[str, Any] = {}
    for profile in ("rapid", "deep"):
        samples = sorted(profiles[profile])
        if not samples:
            result[profile] = {
                "gate": "not_run",
                "reason": "wall_clock_profile_unavailable",
                "sample_count": 0,
                "p100_seconds": None,
                "limit_seconds": TIMING_LIMITS_SECONDS[profile],
            }
            continue
        p100 = max(wall for _, _, wall in samples)
        result[profile] = {
            "gate": (
                "pass" if p100 <= TIMING_LIMITS_SECONDS[profile] else "fail"
            ),
            "sample_count": len(samples),
            "p100_seconds": p100,
            "limit_seconds": TIMING_LIMITS_SECONDS[profile],
            "samples": [
                {
                    "case_id": case_id,
                    "run_ref": run_ref,
                    "wall_clock_seconds": wall,
                }
                for case_id, run_ref, wall in samples
            ],
        }
    result["under_five_minute_samples"] = sorted(
        under_five, key=lambda item: (item["case_id"], item["run_ref"])
    )
    result["work_sufficiency_gate"] = work_gate
    return result


def _work_sufficiency_disposition(
    run_ref: str,
    value: Any,
    *,
    case_id: str,
    report_sha256: str,
    execution: Mapping[str, Any],
    review_authority: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {
            "gate": "fail",
            "reason": "independent_work_sufficiency_disposition_missing",
        }
    try:
        disposition = _required_string(value, "disposition")
        rationale = _required_string(value, "rationale")
        observed_report_sha256 = _required_string(value, "report_sha256")
        if observed_report_sha256 != report_sha256:
            raise BaselineError("work sufficiency report_sha256 does not match the run")
        observed_case_id = _required_string(value, "case_id")
        if observed_case_id != case_id:
            raise BaselineError("work sufficiency case_id does not match the run")
        generator_root = _required_string(
            execution, "generator_artifact_root_sha256"
        )
        observed_generator_root = _required_string(
            value, "generator_artifact_root_sha256"
        )
        if observed_generator_root != generator_root:
            raise BaselineError(
                "work sufficiency generator_artifact_root_sha256 does not match the run"
            )
        diagnostic = _mapping(
            execution.get("work_sufficiency_diagnostic"),
            "work sufficiency diagnostic",
        )
        diagnostic_sha256 = _canonical_sha256(diagnostic)
        if _required_string(value, "work_sufficiency_diagnostic_sha256") != diagnostic_sha256:
            raise BaselineError(
                "work sufficiency diagnostic hash does not match the run"
            )
        observed_cache_reuse = value.get("cache_reuse")
        if not isinstance(observed_cache_reuse, bool) or observed_cache_reuse != bool(
            execution.get("cache_reuse")
        ):
            raise BaselineError("work sufficiency cache_reuse does not match the run")
        diagnostic_cache_reused = diagnostic.get("cache_reused")
        if (
            not isinstance(diagnostic_cache_reused, bool)
            or diagnostic_cache_reused != observed_cache_reuse
        ):
            raise BaselineError(
                "work sufficiency diagnostic cache state does not match the run"
            )
        if observed_cache_reuse:
            if diagnostic.get("status") != "reused":
                raise BaselineError(
                    "cached work sufficiency diagnostic must have reused status"
                )
            reuse_source_sha256 = _required_string(
                diagnostic, "reuse_source_sha256"
            )
            if not re.fullmatch(r"[0-9a-f]{64}", reuse_source_sha256):
                raise BaselineError(
                    "cached work sufficiency diagnostic requires reuse source hash"
                )
            generator_response_sha256 = _required_string(
                execution, "generator_response_sha256"
            )
            if reuse_source_sha256 != generator_response_sha256:
                raise BaselineError(
                    "cached reuse source does not match the retained generator response"
                )
        else:
            if "reuse_source_sha256" in diagnostic:
                raise BaselineError(
                    "cold generator diagnostic cannot claim a reuse source"
                )
            if diagnostic.get("status") != "sufficient":
                raise BaselineError(
                    "cold generator work sufficiency diagnostic is not sufficient"
                )
        reviewer = _mapping(value.get("reviewer"), "work sufficiency reviewer")
        reviewer_id = _required_string(reviewer, "reviewer_id")
        reviewer_role = _required_string(reviewer, "role")
        author_ids = set(
            _nonempty_string_list(value.get("author_ids"), "work sufficiency author_ids")
        )
        authority_authors = set(review_authority["author_ids"])
        if author_ids != authority_authors:
            raise BaselineError(
                "work sufficiency authors do not match the frozen review authority"
            )
        if reviewer_id in author_ids:
            raise BaselineError("work sufficiency reviewer cannot be an author")
        _require_authorized_reviewer(
            review_authority,
            reviewer_id=reviewer_id,
            role=reviewer_role,
            assignment="work_sufficiency",
        )
        if reviewer.get("independent") is not True:
            raise BaselineError("work sufficiency reviewer must declare independence")
        reviewed_at = _validated_timestamp(reviewer.get("reviewed_at"))
        evidence_refs = _nonempty_string_list(
            value.get("evidence_refs"), "work sufficiency evidence_refs"
        )
        required_refs = {
            f"artifact-sha256://{report_sha256}",
            f"artifact-sha256://{generator_root}",
            f"artifact-sha256://{diagnostic_sha256}",
        }
        if not required_refs.issubset(evidence_refs):
            raise BaselineError(
                "work sufficiency evidence_refs must bind report, generator, and diagnostic hashes"
            )
        if disposition not in {"sufficient", "insufficient"}:
            raise BaselineError("work sufficiency disposition is invalid")
    except BaselineError as exc:
        return {"gate": "fail", "reason": str(exc)}
    return {
        "gate": "pass" if disposition == "sufficient" else "fail",
        "disposition": disposition,
        "rationale": rationale,
        "case_id": case_id,
        "report_sha256": report_sha256,
        "generator_artifact_root_sha256": generator_root,
        "work_sufficiency_diagnostic_sha256": diagnostic_sha256,
        "cache_reuse": observed_cache_reuse,
        "author_ids": sorted(author_ids),
        "reviewer": {
            "reviewer_id": reviewer_id,
            "role": reviewer_role,
            "independent": True,
            "reviewed_at": reviewed_at,
        },
        "evidence_refs": evidence_refs,
        "run_ref": run_ref,
    }


def _validate_calibration_review(
    category: str,
    value: Any,
    *,
    author_ids: set[str],
    review_authority: Mapping[str, Any],
) -> dict[str, Any]:
    review = _mapping(value, category)
    if review.get("status") != "approved":
        raise BaselineError(f"{category} calibration audit was not approved")
    threshold_rationale = _required_string(review, "threshold_rationale")
    category_evidence = _nonempty_string_list(
        review.get("evidence_refs"), f"{category} evidence_refs"
    )
    reviewers = review.get("reviewers")
    if not isinstance(reviewers, list) or len(reviewers) < 2:
        raise BaselineError(
            f"{category} requires at least two independent reviewers"
        )
    retained_reviewers: list[dict[str, Any]] = []
    reviewer_ids: set[str] = set()
    for item in reviewers:
        reviewer = _mapping(item, f"{category} reviewer")
        reviewer_id = _required_string(reviewer, "reviewer_id")
        reviewer_role = _required_string(reviewer, "role")
        if reviewer_id in reviewer_ids:
            raise BaselineError(f"{category} requires distinct independent reviewers")
        if reviewer_id in author_ids:
            raise BaselineError(f"{category} reviewer cannot be an author")
        _require_authorized_reviewer(
            review_authority,
            reviewer_id=reviewer_id,
            role=reviewer_role,
            assignment=f"calibration:{category}",
        )
        if reviewer.get("independent") is not True:
            raise BaselineError(f"{category} reviewer must declare independence")
        if reviewer.get("decision") != "approve":
            raise BaselineError(f"{category} requires joint reviewer approval")
        retained_reviewers.append(
            {
                "reviewer_id": reviewer_id,
                "role": reviewer_role,
                "independent": True,
                "decision": "approve",
                "reviewed_at": _validated_timestamp(reviewer.get("reviewed_at")),
                "evidence_refs": _nonempty_string_list(
                    reviewer.get("evidence_refs"),
                    f"{category} reviewer evidence_refs",
                ),
            }
        )
        reviewer_ids.add(reviewer_id)
    items = review.get("items")
    if not isinstance(items, list):
        raise BaselineError(f"{category} calibration items must be a list")
    retained_items: list[dict[str, Any]] = []
    finding_ids: set[str] = set()
    for item in items:
        finding = dict(_mapping(item, f"{category} finding"))
        finding_id = _required_string(finding, "id")
        if finding_id in finding_ids:
            raise BaselineError(f"{category} contains duplicate finding ids")
        if finding.get("disposition") not in FINAL_DISPOSITIONS:
            raise BaselineError(f"{category} contains an unresolved finding")
        if (
            category in {"false_passes", "missing_denominators", "unstable_evaluator"}
            and finding.get("disposition") != "resolved"
        ):
            raise BaselineError(f"{category} findings must be resolved")
        _required_string(finding, "rationale")
        _nonempty_string_list(
            finding.get("evidence_refs"), f"{category} finding evidence_refs"
        )
        retained_items.append(finding)
        finding_ids.add(finding_id)
    return {
        "status": "approved",
        "threshold_rationale": threshold_rationale,
        "evidence_refs": category_evidence,
        "reviewers": sorted(retained_reviewers, key=lambda item: item["reviewer_id"]),
        "items": sorted(
            retained_items, key=lambda item: json.dumps(item, sort_keys=True)
        ),
    }


def _accepted_bundle_runs(
    baseline_directory: str | Path,
) -> tuple[list[Path], dict[str, Any]] | None:
    root = Path(baseline_directory).resolve()
    manifest_path = root / "baseline_manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise BaselineError("previous baseline manifest is missing or unsafe")
    manifest = _read_mapping(manifest_path, "previous baseline manifest")
    if manifest.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        raise BaselineError("unsupported previous baseline bundle schema")
    expected_hashes = _mapping(
        manifest.get("artifact_sha256"), "previous baseline artifact hashes"
    )
    actual_paths = {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if set(actual_paths) != set(expected_hashes):
        raise BaselineError("previous baseline artifact set does not match manifest")
    for relative, path in actual_paths.items():
        if path.is_symlink():
            raise BaselineError("previous baseline contains a symlink")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hashes[relative]:
            raise BaselineError(f"previous baseline artifact hash mismatch: {relative}")
    release = _read_mapping(root / "release_gate.json", "previous release gate")
    regression = _read_mapping(
        root / "regression_matrix.json", "previous regression matrix"
    )
    if (
        release.get("release_gate") != "pass"
        or regression.get("core_baseline_blocked") is not False
        or manifest.get("bundle_status") != "passed"
    ):
        return None
    run_directories = sorted(root.glob("runs/*/evaluation"))
    if not run_directories:
        raise BaselineError("previous baseline contains no retained evaluation runs")
    return run_directories, manifest


def _load_by_case(
    directories: Sequence[str | Path],
    label: str,
    *,
    corpus: QualityBaselineCorpusIdentity | None = None,
    evaluation_identity: EvaluationCodeIdentity | None = None,
    require_execution: bool = False,
) -> dict[str, LoadedEvaluation]:
    result: dict[str, LoadedEvaluation] = {}
    case_map = corpus.case_map if corpus is not None else {}
    for directory in directories:
        item = load_immutable_evaluation(
            directory,
            expected_identity=evaluation_identity,
            require_execution=require_execution,
        )
        case_id = str(item.manifest["case_id"])
        if corpus is not None:
            if case_id not in case_map:
                raise BaselineError(f"{label} case is outside formal corpus: {case_id}")
            _validate_expected_case(item.manifest, case_map[case_id])
        if case_id in result:
            raise BaselineError(f"duplicate {label} case_id: {case_id}")
        result[case_id] = item
    return result


def _validate_execution(value: Any, *, required: bool) -> None:
    if value is None:
        if required:
            raise BaselineError("every baseline run requires execution evidence")
        return
    execution = _mapping(value, "execution")
    if execution.get("profile") not in {"rapid", "deep"}:
        raise BaselineError("execution profile must be rapid or deep")
    wall = execution.get("wall_clock_seconds")
    if (
        isinstance(wall, bool)
        or not isinstance(wall, (int, float))
        or wall <= 0
    ):
        raise BaselineError("execution wall_clock_seconds must be positive")
    if "cache_reuse" not in execution or not isinstance(
        execution.get("cache_reuse"), bool
    ):
        raise BaselineError("execution cache_reuse must be boolean")
    work_sufficiency = execution.get("work_sufficiency")
    if work_sufficiency is not None and (
        not isinstance(work_sufficiency, str) or not work_sufficiency.strip()
    ):
        raise BaselineError("execution work_sufficiency must be a non-empty string or null")


def _validate_expected_case(
    manifest: Mapping[str, Any],
    expected_case: QualityBaselineCaseIdentity | Mapping[str, Any],
) -> None:
    for name in (
        "case_id",
        "project_id",
        "source_revision",
        "truth_package_version",
    ):
        expected = (
            getattr(expected_case, name)
            if isinstance(expected_case, QualityBaselineCaseIdentity)
            else expected_case.get(name)
        )
        if manifest.get(name) != expected:
            raise BaselineError(f"{name} identity does not match corpus declaration")


def _versions(manifest: Mapping[str, Any]) -> dict[str, str]:
    versions = _mapping(manifest.get("versions"), "version manifest")
    required = {"model", "codetalk", "evaluator"}
    if set(versions) != required:
        raise BaselineError(
            "version manifest requires exactly model, codetalk, and evaluator"
        )
    if any(not isinstance(value, str) or not value.strip() for value in versions.values()):
        raise BaselineError("version identities must be non-empty strings")
    return {name: str(versions[name]) for name in sorted(required)}


def _uniform_versions(
    loaded: Sequence[LoadedEvaluation] | Any, label: str
) -> dict[str, str]:
    values = {_canonical_sha256(_versions(item.manifest)): _versions(item.manifest) for item in loaded}
    if len(values) != 1:
        raise BaselineError(f"{label} historical runs require uniform versions")
    return next(iter(values.values()))


def _read_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BaselineError(f"invalid {label}: {exc}") from exc
    return dict(_mapping(value, label))


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BaselineError(f"{label} must be an object")
    return value


def _required_string(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise BaselineError(f"{key} must be a non-empty string")
    return result.strip()


def _nonempty_string_list(value: Any, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise BaselineError(f"{label} must contain evidence references")
    return sorted({item.strip() for item in value})


def _validated_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BaselineError("reviewed_at must be an ISO-8601 timestamp")
    candidate = value.strip()
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BaselineError("reviewed_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise BaselineError("reviewed_at must include a timezone")
    return candidate


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        raise BaselineError("missing denominator in evaluation metric")
    return _round(numerator / denominator)


def _round(value: float) -> float:
    return round(value, 12)


def _git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BaselineError(
            f"cannot read CodeTalk Git identity: {' '.join(arguments)}"
        ) from exc
    if completed.returncode != 0:
        raise BaselineError(
            f"cannot read CodeTalk Git identity: {' '.join(arguments)}"
        )
    return completed.stdout.strip()
