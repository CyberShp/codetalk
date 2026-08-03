"""Reproducible, truth-isolated F012 benchmark execution entry points."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol, Sequence

from app.services.quality_benchmark_corpus import (
    PROJECT_DOMAIN_TAGS,
    QualityBenchmarkRegistry,
    load_quality_case,
    load_quality_registry,
    resolve_quality_project,
)
from app.services.quality_benchmark_generator import generate_quality_benchmark_artifacts
from app.services.quality_benchmark_semantic_judge import (
    DEFAULT_JUDGE_MODEL,
    BehaviorClaimBatchSemanticJudge,
    SemanticJudgment,
)
from app.services.quality_depth_evaluator import (
    DepthEvidenceCatalog,
    DepthExecutionPlan,
    DepthOracleCommandContract,
    execute_depth_execution_oracles,
)
from app.services.quality_evaluation_contract import (
    BenchmarkIdentity,
    EvaluationScope,
    RepairSummary,
    serialize_quality_evaluation,
)
from app.services.quality_evaluator import (
    QualityEvaluationStore,
    aggregate_quality_evaluation,
    build_quality_report,
    evaluate_quality_snapshot,
)

REPORT_FILENAME = "quality_evaluation_report.json"
MANIFEST_FILENAME = "quality_evaluation_manifest.json"
HUMAN_REPORT_FILENAME = "quality_evaluation_report.md"
REQUIRED_VERSIONS = frozenset({"model", "codetalk", "evaluator"})
@dataclass(frozen=True)
class BenchmarkRunResult:
    report_path: Path
    manifest_path: Path
    report_sha256: str


SemanticVerdict = Literal["supports", "contradicts", "insufficient"]


class EvaluatorOwnedSemanticVerdictAdapter(Protocol):
    """Independent semantic oracle used only after generator output is frozen."""

    def claim_verdict(
        self, *, candidate: Mapping[str, Any], truth: Mapping[str, Any]
    ) -> SemanticVerdict: ...

    def breadth_verdict(
        self, *, candidate: Mapping[str, Any], truth: Mapping[str, Any]
    ) -> SemanticVerdict: ...

    def depth_verdict(
        self,
        *,
        candidate: Mapping[str, Any],
        truth: Mapping[str, Any],
        binding: Any,
    ) -> SemanticVerdict: ...


class ConservativeDeterministicSemanticVerdictAdapter:
    """Fail-closed default while Breadth/Depth truth lacks typed assertions."""

    def claim_verdict(
        self, *, candidate: Mapping[str, Any], truth: Mapping[str, Any]
    ) -> SemanticVerdict:
        return _claim_semantic_status(
            _semantic_statement(candidate),
            _semantic_statement(truth),
        )

    def breadth_verdict(
        self, *, candidate: Mapping[str, Any], truth: Mapping[str, Any]
    ) -> SemanticVerdict:
        return "insufficient"

    def depth_verdict(
        self,
        *,
        candidate: Mapping[str, Any],
        truth: Mapping[str, Any],
        binding: Any,
    ) -> SemanticVerdict:
        return "insufficient"


_DEFAULT_SEMANTIC_VERDICT_ADAPTER = ConservativeDeterministicSemanticVerdictAdapter()


class _SemanticJudgmentRecorder:
    def __init__(self) -> None:
        self._judgments: dict[str, SemanticJudgment] = {}

    @property
    def judgments(self) -> tuple[SemanticJudgment, ...]:
        return tuple(self._judgments.values())

    def claim_verdict(
        self, *, candidate: Mapping[str, Any], truth: Mapping[str, Any]
    ) -> SemanticVerdict:
        return self._record("accuracy", candidate, truth, None)

    def breadth_verdict(
        self, *, candidate: Mapping[str, Any], truth: Mapping[str, Any]
    ) -> SemanticVerdict:
        return self._record("breadth", candidate, truth, None)

    def depth_verdict(
        self,
        *,
        candidate: Mapping[str, Any],
        truth: Mapping[str, Any],
        binding: Any,
    ) -> SemanticVerdict:
        return self._record("depth", candidate, truth, binding)

    def _record(
        self,
        axis: Literal["accuracy", "breadth", "depth"],
        candidate: Mapping[str, Any],
        truth: Mapping[str, Any],
        binding: Any,
    ) -> SemanticVerdict:
        candidate_statement = _axis_candidate_statement(axis, candidate)
        oracle_statement = _axis_oracle_statement(axis, truth, binding)
        observed_evidence_refs = _axis_candidate_evidence_refs(candidate)
        required_evidence_refs = _axis_oracle_evidence_refs(axis, truth, binding)
        if (
            not candidate_statement
            or not oracle_statement
            or not observed_evidence_refs
            or not required_evidence_refs
        ):
            return "insufficient"
        judgment_id = _semantic_judgment_id(
            axis=axis,
            candidate_statement=candidate_statement,
            oracle_statement=oracle_statement,
            observed_evidence_refs=observed_evidence_refs,
            required_evidence_refs=required_evidence_refs,
        )
        self._judgments.setdefault(
            judgment_id,
            SemanticJudgment(
                judgment_id=judgment_id,
                axis=axis,
                candidate_statement=candidate_statement,
                oracle_statement=oracle_statement,
                observed_evidence_refs=observed_evidence_refs,
                required_evidence_refs=required_evidence_refs,
            ),
        )
        return "insufficient"


class _ResolvedBatchSemanticVerdictAdapter(_SemanticJudgmentRecorder):
    def __init__(self, verdicts: Mapping[str, SemanticVerdict]) -> None:
        super().__init__()
        self._verdicts = dict(verdicts)

    def _record(
        self,
        axis: Literal["accuracy", "breadth", "depth"],
        candidate: Mapping[str, Any],
        truth: Mapping[str, Any],
        binding: Any,
    ) -> SemanticVerdict:
        candidate_statement = _axis_candidate_statement(axis, candidate)
        oracle_statement = _axis_oracle_statement(axis, truth, binding)
        observed_evidence_refs = _axis_candidate_evidence_refs(candidate)
        required_evidence_refs = _axis_oracle_evidence_refs(axis, truth, binding)
        if (
            not candidate_statement
            or not oracle_statement
            or not observed_evidence_refs
            or not required_evidence_refs
        ):
            return "insufficient"
        return self._verdicts.get(
            _semantic_judgment_id(
                axis=axis,
                candidate_statement=candidate_statement,
                oracle_statement=oracle_statement,
                observed_evidence_refs=observed_evidence_refs,
                required_evidence_refs=required_evidence_refs,
            ),
            "insufficient",
        )


def build_benchmark_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run or persist an independent CodeTalk quality benchmark."
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--case")
    selection.add_argument("--domain")
    selection.add_argument("--all", action="store_true")
    parser.add_argument("--source-root")
    parser.add_argument("--registry", default="benchmarks/quality/registry.json")
    parser.add_argument("--run-artifacts", required=False)
    parser.add_argument("--model", default=os.environ.get("CODETALK_QUALITY_MODEL", "gpt-5.6-sol"))
    parser.add_argument(
        "--judge-model",
        default=os.environ.get("CODETALK_QUALITY_JUDGE_MODEL", DEFAULT_JUDGE_MODEL),
    )
    parser.add_argument("--mode", choices=("rapid", "deep"), default="rapid")
    parser.add_argument("--output", required=True)
    return parser


def parse_benchmark_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_benchmark_argument_parser().parse_args(argv)


def validate_version_manifest(versions: Mapping[str, str]) -> dict[str, str]:
    normalized = {
        str(key): str(value).strip() for key, value in versions.items() if str(value).strip()
    }
    if not REQUIRED_VERSIONS.issubset(normalized):
        missing = ", ".join(sorted(REQUIRED_VERSIONS - normalized.keys()))
        raise ValueError(f"versions are missing required metadata: {missing}")
    return normalized


def evaluate_artifact_snapshot(
    *,
    case_path: str | Path,
    registry: QualityBenchmarkRegistry,
    artifacts_dir: str | Path,
    semantic_verdict_adapter: EvaluatorOwnedSemanticVerdictAdapter | None = None,
    source_dir: str | Path | None = None,
    generator_model: str = "",
    judge_model: str = DEFAULT_JUDGE_MODEL,
    mode: str = "rapid",
    deadline_monotonic: float | None = None,
    semantic_judge: Any | None = None,
    semantic_audit_sink: list[dict[str, Any]] | None = None,
    execution_command_allowlist: Mapping[
        str, DepthOracleCommandContract
    ] | None = None,
    execution_audit_sink: list[dict[str, Any]] | None = None,
    execution_artifacts_dir: str | Path | None = None,
    snapshot_label: str = "snapshot",
) -> Any:
    """Load hash-verified evaluator truth and generator artifacts separately."""

    case_file = Path(case_path)
    case = load_quality_case(
        case_file,
        registry=registry,
        source_dir=source_dir,
    )
    truth_root = case_file.resolve().parent
    artifacts = Path(artifacts_dir).resolve()
    if not artifacts.is_dir():
        raise ValueError(f"artifact directory is unavailable: {artifacts}")
    if _paths_overlap(truth_root, artifacts):
        raise ValueError("truth and artifact directories must be isolated")

    gold_claims = _read_json(truth_root / case.truth_package.gold_claims.path)
    coverage_universe = _read_json(truth_root / case.truth_package.coverage_universe.path)
    depth_truth = _read_json(truth_root / case.truth_package.critical_chains.path)
    execution = _read_json(truth_root / case.truth_package.execution_oracles.path)
    if not isinstance(execution, Mapping):
        raise ValueError("execution_oracles must be a JSON object")
    catalog_payload = execution.get("evidence_catalog")
    if catalog_payload is None and {"case_id", "bindings"}.issubset(execution):
        catalog_payload = {
            "case_id": execution["case_id"],
            "bindings": execution["bindings"],
        }
    if not isinstance(catalog_payload, Mapping):
        raise ValueError("execution_oracles must contain evidence_catalog")
    catalog = DepthEvidenceCatalog.model_validate(catalog_payload)
    execution_run = _run_evaluator_owned_depth_oracle(
        execution=execution,
        depth_truth=depth_truth,
        catalog=catalog,
        source_dir=source_dir,
        deadline_monotonic=deadline_monotonic,
        command_allowlist=execution_command_allowlist or {},
        artifact_dir=execution_artifacts_dir,
    )
    if execution_audit_sink is not None:
        execution_audit_sink.append(dict(execution_run.audit))
    evaluator_l3 = execution_run.evidence.model_dump(mode="json")

    depth_candidate = _read_first_json(artifacts, ("quality_depth_candidate.json",))
    claim_ledger = _read_first_json(artifacts, ("claim_ledger.json", "claim-ledger.json"))
    evidence_cards = _optional_json(artifacts / "evidence_cards.json", [])
    accuracy_policy = _optional_json(artifacts / "quality_accuracy_policy.json", {})
    breadth_bundle = _optional_json(artifacts / "quality_breadth.json", {})
    scenario_candidates = _artifact_value(
        breadth_bundle, artifacts, "scenario_candidates", "scenario_candidates.json"
    )
    scenarios = _artifact_value(
        breadth_bundle, artifacts, "scenarios", "test_scenarios.json"
    )
    dispositions = _artifact_value(
        breadth_bundle, artifacts, "dispositions", "scenario_dispositions.json"
    )
    adapter = semantic_verdict_adapter
    if adapter is None:
        recorder = _SemanticJudgmentRecorder()
        semantic_diagnostics: list[dict[str, Any]] = []
        _align_claim_semantics_from_evidence(
            _mapping(claim_ledger, "claim_ledger"),
            _items(gold_claims),
            semantic_verdict_adapter=recorder,
            semantic_diagnostic_sink=semantic_diagnostics,
        )
        _align_breadth_evidence_refs(
            coverage_universe,
            scenario_candidates,
            scenarios,
            dispositions,
            semantic_verdict_adapter=recorder,
        )
        _align_depth_candidate_from_evidence(
            depth_truth,
            depth_candidate,
            catalog,
            semantic_verdict_adapter=recorder,
        )
        effective_deadline = (
            float(deadline_monotonic)
            if deadline_monotonic is not None
            else time.monotonic()
        )
        batch_judge = semantic_judge or BehaviorClaimBatchSemanticJudge(
            judge_model=judge_model
        )
        semantic_source_dir = (
            Path(source_dir).resolve()
            if source_dir is not None
            else Path(".__quality_source_unavailable__")
        )
        result = batch_judge.judge(
            judgments=recorder.judgments,
            source_dir=semantic_source_dir,
            generator_model=str(generator_model),
            judge_model=str(judge_model),
            mode=str(mode),
            deadline_monotonic=effective_deadline,
            snapshot_label=str(snapshot_label),
        )
        _append_semantic_result_audit(
            semantic_audit_sink,
            result=result,
            judgments=recorder.judgments,
            diagnostics=semantic_diagnostics,
            extras={"decision_role": "diagnostic_screening"},
        )
        resolved_verdicts = dict(result.verdicts)
        if recorder.judgments:
            adjudication = batch_judge.judge(
                judgments=recorder.judgments,
                source_dir=semantic_source_dir,
                generator_model=str(generator_model),
                judge_model=str(judge_model),
                mode="deep",
                deadline_monotonic=effective_deadline,
                snapshot_label=f"{snapshot_label}_high_effort_adjudication",
            )
            disagreements = _semantic_verdict_disagreements(
                result.verdicts,
                adjudication.verdicts,
            )
            resolved_verdicts = _authoritative_semantic_verdicts(
                result.verdicts,
                adjudication.verdicts,
            )
            _append_semantic_result_audit(
                semantic_audit_sink,
                result=adjudication,
                judgments=recorder.judgments,
                diagnostics=(),
                extras={
                    "decision_role": "authoritative_adjudication",
                    "screening_disagreement_count": len(disagreements),
                    "screening_disagreements": list(disagreements),
                    "verdict_trace": list(
                        _semantic_verdict_trace(
                            recorder.judgments,
                            result.verdicts,
                            adjudication.verdicts,
                            resolved_verdicts,
                        )
                    ),
                },
            )
        adapter = _ResolvedBatchSemanticVerdictAdapter(resolved_verdicts)
    aligned_claim_ledger = _align_claim_semantics_from_evidence(
        _mapping(claim_ledger, "claim_ledger"),
        _items(gold_claims),
        semantic_verdict_adapter=adapter,
    )
    aligned_candidates, aligned_scenarios, aligned_dispositions = (
        _align_breadth_evidence_refs(
            coverage_universe,
            scenario_candidates,
            scenarios,
            dispositions,
            semantic_verdict_adapter=adapter,
        )
    )
    aligned_depth_candidate = _align_depth_candidate_from_evidence(
        depth_truth,
        depth_candidate,
        catalog,
        semantic_verdict_adapter=adapter,
        evaluator_l3=evaluator_l3,
    )
    return evaluate_quality_snapshot(
        accuracy_inputs={
            "scope": EvaluationScope.INDEPENDENT_BENCHMARK,
            "claim_ledger": aligned_claim_ledger,
            "evidence_cards": _items(evidence_cards),
            "gold_claims": _items(gold_claims),
            **_mapping(accuracy_policy, "quality_accuracy_policy"),
        },
        breadth_inputs={
            "universe": coverage_universe,
            "scenario_candidates": aligned_candidates,
            "scenarios": aligned_scenarios,
            "dispositions": aligned_dispositions,
        },
        depth_inputs={
            "truth": depth_truth,
            "candidate": aligned_depth_candidate,
            "catalog": catalog,
        },
    )


def _semantic_axis_audit_metadata(
    *,
    judgments: Sequence[SemanticJudgment],
    result_status: str,
    diagnostics: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    axes = ("accuracy", "breadth", "depth")
    candidate_count_by_axis = {
        axis: sum(1 for item in judgments if item.axis == axis) for axis in axes
    }
    repair_status_by_axis = {
        axis: (
            "required"
            if any(
                item.get("axis") == axis and item.get("repairable") is True
                for item in diagnostics
            )
            else "not_required"
        )
        for axis in axes
    }
    return {
        "candidate_count_by_axis": candidate_count_by_axis,
        "status_by_axis": {
            axis: str(result_status) if count > 0 else "no_candidates"
            for axis, count in candidate_count_by_axis.items()
        },
        "repair_status_by_axis": repair_status_by_axis,
    }


def _append_semantic_result_audit(
    sink: list[dict[str, Any]] | None,
    *,
    result: Any,
    judgments: Sequence[SemanticJudgment],
    diagnostics: Sequence[Mapping[str, Any]],
    extras: Mapping[str, Any] | None = None,
) -> None:
    if sink is None:
        return
    sink.append(
        {
            **dict(result.metadata),
            **_semantic_axis_audit_metadata(
                judgments=judgments,
                result_status=str(result.metadata.get("status") or "unavailable"),
                diagnostics=diagnostics,
            ),
            "diagnostics": list(diagnostics),
            "limitations": list(result.limitations),
            **dict(extras or {}),
        }
    )


def _authoritative_semantic_verdicts(
    screening: Mapping[str, SemanticVerdict],
    adjudication: Mapping[str, SemanticVerdict],
) -> dict[str, SemanticVerdict]:
    resolved: dict[str, SemanticVerdict] = {}
    for judgment_id, raw_screening in screening.items():
        _validated_semantic_verdict(raw_screening)
        resolved[judgment_id] = _validated_semantic_verdict(
            adjudication.get(judgment_id, "insufficient")
        )
    return resolved


def _semantic_verdict_disagreements(
    screening: Mapping[str, SemanticVerdict],
    adjudication: Mapping[str, SemanticVerdict],
) -> tuple[dict[str, str], ...]:
    disagreements: list[dict[str, str]] = []
    for judgment_id, raw_screening in screening.items():
        screening_verdict = _validated_semantic_verdict(raw_screening)
        adjudication_verdict = _validated_semantic_verdict(
            adjudication.get(judgment_id, "insufficient")
        )
        if screening_verdict == adjudication_verdict:
            continue
        disagreements.append(
            {
                "judgment_id": judgment_id,
                "screening": screening_verdict,
                "adjudication": adjudication_verdict,
            }
        )
    return tuple(disagreements)


def _semantic_verdict_trace(
    judgments: Sequence[SemanticJudgment],
    screening: Mapping[str, SemanticVerdict],
    adjudication: Mapping[str, SemanticVerdict],
    resolved: Mapping[str, SemanticVerdict],
) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "judgment_id": judgment.judgment_id,
            "axis": judgment.axis,
            "screening": _validated_semantic_verdict(
                screening.get(judgment.judgment_id, "insufficient")
            ),
            "adjudication": _validated_semantic_verdict(
                adjudication.get(judgment.judgment_id, "insufficient")
            ),
            "resolved": _validated_semantic_verdict(
                resolved.get(judgment.judgment_id, "insufficient")
            ),
        }
        for judgment in judgments
    )


def _run_evaluator_owned_depth_oracle(
    *,
    execution: Mapping[str, Any],
    depth_truth: Any,
    catalog: DepthEvidenceCatalog,
    source_dir: str | Path | None,
    deadline_monotonic: float | None,
    command_allowlist: Mapping[str, DepthOracleCommandContract],
    artifact_dir: str | Path | None,
) -> Any:
    truth_payload = _mapping(depth_truth, "critical_chains")
    case_id = str(truth_payload.get("case_id") or catalog.case_id)
    tier = str(truth_payload.get("execution_tier") or "S")
    raw_plan = execution.get("execution_plan")
    if isinstance(raw_plan, Mapping):
        plan_payload = dict(raw_plan)
    elif tier == "S":
        plan_payload = {
            "schema_version": "quality-depth-execution-v1",
            "case_id": case_id,
            "execution_tier": "S",
            "policy": "disabled",
            "oracles": [],
            "limitations": [],
        }
    else:
        plan_payload = {
            "schema_version": "quality-depth-execution-v1",
            "case_id": case_id,
            "execution_tier": tier,
            "policy": "unavailable",
            "oracles": [],
            "limitations": ["EXECUTION_PLAN_UNAVAILABLE"],
        }
    if source_dir is None and tier != "S":
        plan_payload = {
            "schema_version": "quality-depth-execution-v1",
            "case_id": case_id,
            "execution_tier": tier,
            "policy": "unavailable",
            "oracles": [],
            "limitations": ["PINNED_SOURCE_UNAVAILABLE"],
        }
    plan = DepthExecutionPlan.model_validate(plan_payload)
    if plan.case_id != case_id or plan.execution_tier.value != tier:
        raise ValueError("depth execution plan identity differs from hidden truth")
    effective_deadline = (
        float(deadline_monotonic)
        if deadline_monotonic is not None
        else time.monotonic()
    )
    source = Path(source_dir).resolve() if source_dir is not None else Path.cwd()
    if artifact_dir is not None:
        return execute_depth_execution_oracles(
            plan,
            catalog,
            source_dir=source,
            artifact_dir=artifact_dir,
            deadline_monotonic=effective_deadline,
            command_allowlist=command_allowlist,
        )
    with tempfile.TemporaryDirectory(prefix="codetalk-depth-oracle-") as root:
        return execute_depth_execution_oracles(
            plan,
            catalog,
            source_dir=source,
            artifact_dir=Path(root) / "execution",
            deadline_monotonic=effective_deadline,
            command_allowlist=command_allowlist,
        )


def run_quality_benchmark_case(
    *,
    case_path: str | Path,
    registry: QualityBenchmarkRegistry,
    first_pass_artifacts: str | Path,
    final_artifacts: str | Path,
    output_dir: str | Path,
    run_ref: str,
    repair_summary: RepairSummary | Mapping[str, Any],
    versions: Mapping[str, str],
    source_root: str | Path | None = None,
    execution: Mapping[str, Any] | None = None,
    semantic_verdict_adapter: EvaluatorOwnedSemanticVerdictAdapter | None = None,
    generator_model: str | None = None,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    mode: str = "rapid",
    started_monotonic: float | None = None,
    deadline_monotonic: float | None = None,
    semantic_judge: Any | None = None,
    execution_command_allowlist: Mapping[
        str, DepthOracleCommandContract
    ] | None = None,
) -> BenchmarkRunResult:
    run_started = (
        float(started_monotonic)
        if started_monotonic is not None
        else time.monotonic()
    )
    deadline = (
        float(deadline_monotonic)
        if deadline_monotonic is not None
        else run_started + _quality_generation_timeout(mode)
    )
    if deadline <= run_started:
        deadline = run_started
    first_root = Path(first_pass_artifacts).resolve()
    final_root = Path(final_artifacts).resolve()
    output_root = Path(output_dir).resolve()
    truth_root = Path(case_path).resolve().parent
    if any(
        _paths_overlap(output_root, protected)
        for protected in (first_root, final_root, truth_root)
    ):
        raise ValueError("output directory must differ from artifact directories")
    if output_root.exists():
        raise FileExistsError(f"immutable benchmark output already exists: {output_root}")
    normalized_versions = validate_version_manifest(versions)
    case = load_quality_case(case_path, registry=registry)
    projects = {project.id: project for project in registry.projects}
    project = projects[case.project_id]
    source_dir: Path | None = None
    if source_root is not None:
        source_dir = resolve_quality_project(
            case.project_id,
            registry=registry,
            corpus_root=source_root,
        ).path

    semantic_audits: list[dict[str, Any]] = []
    execution_audits: list[dict[str, Any]] = []
    effective_generator_model = str(
        generator_model or normalized_versions.get("model") or ""
    )
    batch_judge = semantic_judge
    if semantic_verdict_adapter is None and batch_judge is None:
        batch_judge = BehaviorClaimBatchSemanticJudge(judge_model=judge_model)

    first_input_sha256 = _artifact_snapshot_input_sha256(first_root)
    first = evaluate_artifact_snapshot(
        case_path=case_path,
        registry=registry,
        artifacts_dir=first_root,
        semantic_verdict_adapter=semantic_verdict_adapter,
        source_dir=source_dir,
        generator_model=effective_generator_model,
        judge_model=judge_model,
        mode=mode,
        deadline_monotonic=deadline,
        semantic_judge=batch_judge,
        semantic_audit_sink=semantic_audits,
        execution_command_allowlist=execution_command_allowlist,
        execution_audit_sink=execution_audits,
        snapshot_label="first_pass",
    )
    stable_first_input_sha256 = _artifact_snapshot_input_sha256(first_root)
    final_input_sha256 = _artifact_snapshot_input_sha256(final_root)
    reused_first_evaluation = (
        first_input_sha256 == stable_first_input_sha256 == final_input_sha256
    )
    if reused_first_evaluation:
        final = first
    else:
        final = evaluate_artifact_snapshot(
            case_path=case_path,
            registry=registry,
            artifacts_dir=final_root,
            semantic_verdict_adapter=semantic_verdict_adapter,
            source_dir=source_dir,
            generator_model=effective_generator_model,
            judge_model=judge_model,
            mode=mode,
            deadline_monotonic=deadline,
            semantic_judge=batch_judge,
            semantic_audit_sink=semantic_audits,
            execution_command_allowlist=execution_command_allowlist,
            execution_audit_sink=execution_audits,
            snapshot_label="final_after_auto_repair",
        )
    limitations = list(
        dict.fromkeys(
            str(limitation)
            for audit in semantic_audits
            for limitation in audit.get("limitations") or []
            if str(limitation).strip()
        )
    )
    deadline_exceeded = time.monotonic() >= deadline
    hard_failures: list[dict[str, Any]] = []
    if deadline_exceeded:
        limitations.append("WHOLE_CHAIN_DEADLINE_EXCEEDED")
        hard_failures.append(_whole_chain_deadline_failure())
    report = build_quality_report(
        scope=EvaluationScope.INDEPENDENT_BENCHMARK,
        run_ref=run_ref,
        benchmark_identity=BenchmarkIdentity(
            case_id=case.case_id,
            source_revision=project.commit,
            truth_package_version=case.truth_package_version,
        ),
        first_pass=first,
        final_after_auto_repair=final,
        repair_summary=repair_summary,
        hard_failures=hard_failures,
        limitations=list(dict.fromkeys(limitations)),
    )
    report_bytes = serialize_quality_evaluation(report).encode("utf-8")
    human_report_bytes = _render_human_evaluation(report).encode("utf-8")
    report_digest = hashlib.sha256(report_bytes).hexdigest()
    execution_payload = dict(execution or {})
    legacy_wall_clock = execution_payload.pop("wall_clock_seconds", None)
    if legacy_wall_clock is not None:
        execution_payload.setdefault(
            "generation_wall_clock_seconds", float(legacy_wall_clock)
        )
    elapsed = max(0.0, time.monotonic() - run_started)
    deadline_exceeded = deadline_exceeded or time.monotonic() >= deadline
    work_sufficiency_diagnostic = execution_payload.get(
        "work_sufficiency_diagnostic"
    )
    if not isinstance(work_sufficiency_diagnostic, Mapping):
        work_sufficiency_diagnostic = {}
    work_sufficiency_status = str(
        work_sufficiency_diagnostic.get("status")
        or execution_payload.get("work_sufficiency")
        or ("pending_audit" if elapsed < 300 else "not_sampled")
    )
    execution_payload.update(
        {
            "profile": str(execution_payload.get("profile") or mode),
            "wall_clock_seconds": round(elapsed, 6),
            "budget_seconds": max(0.0, deadline - run_started),
            "deadline_exceeded": deadline_exceeded,
            "status": (
                "blocked"
                if deadline_exceeded or limitations
                else str(execution_payload.get("status") or "completed")
            ),
            "work_sufficiency": work_sufficiency_status,
            "work_sufficiency_diagnostic": dict(work_sufficiency_diagnostic),
        }
    )
    manifest = {
        "schema_version": "quality-evaluation-manifest-v1",
        "run_ref": run_ref,
        "case_id": case.case_id,
        "project_id": case.project_id,
        "source_revision": project.commit,
        "truth_package_version": case.truth_package_version,
        "versions": normalized_versions,
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "executable": Path(sys.executable).name,
        },
        "execution": execution_payload,
        "snapshot_evaluation": {
            "final_after_auto_repair": {
                "strategy": (
                    "reused_first_pass"
                    if reused_first_evaluation
                    else "independent_evaluation"
                ),
                "artifact_input_sha256": final_input_sha256,
            }
        },
        "semantic_judges": semantic_audits,
        "depth_execution_oracles": execution_audits,
        "report_sha256": report_digest,
        "human_report_sha256": hashlib.sha256(human_report_bytes).hexdigest(),
    }
    output_root.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(
        tempfile.mkdtemp(dir=output_root.parent, prefix=f".{output_root.name}.")
    )
    try:
        _atomic_write(staging_root / REPORT_FILENAME, report_bytes)
        _atomic_write(staging_root / HUMAN_REPORT_FILENAME, human_report_bytes)
        _atomic_write(
            staging_root / MANIFEST_FILENAME,
            (json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
        )
        if output_root.exists():
            raise FileExistsError(
                f"immutable benchmark output already exists: {output_root}"
            )
        _rename_directory_noreplace(staging_root, output_root)
    finally:
        if staging_root.exists():
            for child in staging_root.iterdir():
                child.unlink()
            staging_root.rmdir()
    report_path = output_root / REPORT_FILENAME
    manifest_path = output_root / MANIFEST_FILENAME
    return BenchmarkRunResult(report_path, manifest_path, report_digest)


def _whole_chain_deadline_failure() -> dict[str, Any]:
    return {
        "code": "WHOLE_CHAIN_DEADLINE_EXCEEDED",
        "message": "The benchmark exhausted its single absolute whole-chain deadline.",
        "evidence_refs": (),
        "unrecoverable": True,
    }


def _render_human_evaluation(report: Any) -> str:
    lines = [
        f"# Independent Quality Evaluation: {report.run_ref}",
        "",
        f"- Scope: `{report.scope.value}`",
        f"- Delivery: `{report.delivery_status.value}`",
        f"- Repair attempts: {report.repair_summary.attempt_count}",
        "",
        "## Accuracy / Breadth / Depth",
        "",
        "| Axis | First pass | Final | Status | Critical misses |",
        "|---|---:|---:|---|---:|",
    ]
    for axis_name in ("accuracy", "breadth", "depth"):
        first = getattr(report.first_pass, axis_name)
        final = getattr(report.final_after_auto_repair, axis_name)
        lines.append(
            f"| {axis_name.title()} | {first.numerator}/{first.denominator} | "
            f"{final.numerator}/{final.denominator} | `{final.status.value}` | "
            f"{len(final.critical_misses)} |"
        )
    lines.extend(["", "## Raw Metrics", ""])
    for axis_name in ("accuracy", "breadth", "depth"):
        final = getattr(report.final_after_auto_repair, axis_name)
        lines.append(f"### {axis_name.title()}")
        lines.append("")
        for metric in final.metrics:
            lines.append(
                f"- `{metric.name.value}`: {metric.numerator}/{metric.denominator}"
            )
        lines.append("")
    if report.limitations:
        lines.extend(["## Limitations", ""])
        lines.extend(f"- {item}" for item in report.limitations)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def run_precomputed_benchmark(
    *,
    run_artifacts: str | Path,
    output_root: str | Path,
) -> Path:
    """Persist an already independently-evaluated report without reopening truth."""

    artifact_path = Path(run_artifacts)
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid precomputed benchmark report: {artifact_path}") from exc
    report = aggregate_quality_evaluation(payload)
    return QualityEvaluationStore(output_root).write(report)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_benchmark_args(argv)
    run_artifacts = (
        Path(args.run_artifacts)
        if args.run_artifacts
        else Path(f"{args.output}.run-artifacts")
    )
    if run_artifacts.exists() and not args.run_artifacts:
        raise SystemExit(f"immutable generated run artifacts already exist: {run_artifacts}")
    if run_artifacts.is_file():
        raise SystemExit(
            "--run-artifacts must be a directory containing first/final snapshots"
        )
    if not args.source_root:
        raise SystemExit("--source-root is required for independent benchmark execution")

    registry_path = Path(args.registry)
    registry = load_quality_registry(registry_path)
    case_paths = _select_case_paths(
        registry_path=registry_path,
        registry=registry,
        case_selector=args.case,
        domain_selector=args.domain,
        select_all=bool(args.all),
    )
    multiple = len(case_paths) > 1
    for case_path in case_paths:
        case_started = time.monotonic()
        case_deadline = case_started + _quality_generation_timeout(str(args.mode))
        case_payload = _mapping(_read_json(case_path), "quality benchmark case")
        case_id = str(case_payload.get("case_id") or "")
        case_run_root = run_artifacts
        if multiple or (
            args.run_artifacts and not (run_artifacts / "first_pass").is_dir()
        ):
            case_run_root = run_artifacts / case_id
        if not args.run_artifacts:
            project_id = str(case_payload.get("project_id") or "")
            source_dir = resolve_quality_project(
                project_id,
                registry=registry,
                corpus_root=Path(args.source_root),
            )
            generate_quality_benchmark_artifacts(
                case_id=case_id,
                source_dir=source_dir.path,
                output_dir=case_run_root,
                model=str(args.model),
                mode=str(args.mode),
                timeout_seconds=max(
                    1, int(max(0.0, case_deadline - time.monotonic()))
                ),
                codetalk_revision=_current_codetalk_revision(),
                truth_paths=_quality_case_truth_paths(case_path, registry=registry),
                analysis_target=str(
                    case_payload.get("analysis_target") or case_id
                ),
                prepublication_gate=_benchmark_compound_claim_gate(
                    case_path=case_path,
                    registry=registry,
                ),
            )
        output_root = Path(args.output) / case_id if multiple else Path(args.output)
        result = run_quality_benchmark_case(
            case_path=case_path,
            registry=registry,
            first_pass_artifacts=case_run_root / "first_pass",
            final_artifacts=case_run_root / "final_after_auto_repair",
            output_dir=output_root,
            run_ref=case_id,
            repair_summary=_evaluation_repair_summary(
                _read_json(case_run_root / "repair_summary.json")
            ),
            versions=_mapping(
                _read_json(case_run_root / "versions.json"), "versions"
            ),
            source_root=Path(args.source_root),
            execution=_benchmark_execution_manifest(case_run_root),
            generator_model=str(args.model),
            judge_model=str(args.judge_model),
            mode=str(args.mode),
            started_monotonic=case_started,
            deadline_monotonic=case_deadline,
        )
        if args.run_artifacts:
            _publish_task_run_projection(
                report_path=result.report_path,
                task_run_dir=case_run_root,
                deadline_monotonic=case_deadline,
            )
        print(result.manifest_path)
    return 0


def _quality_generation_timeout(mode: str) -> int:
    return 5400 if mode == "deep" else 900


def _benchmark_compound_claim_gate(
    *, case_path: Path, registry: QualityBenchmarkRegistry
) -> Any:
    case_file = case_path.resolve()
    case = load_quality_case(case_file, registry=registry)
    gold_claims = _items(
        _read_json(case_file.parent / case.truth_package.gold_claims.path)
    )

    def gate(response_path: Path) -> dict[str, Any]:
        response = _mapping(_read_json(response_path), "benchmark response")
        diagnostics: list[dict[str, Any]] = []
        _align_claim_semantics_from_evidence(
            {"claims": response.get("claims") or []},
            gold_claims,
            semantic_diagnostic_sink=diagnostics,
        )
        issues = [
            {
                "code": "compound_claim_requires_split",
                "artifact": "benchmark_response.json",
                "field": "claims",
                "row_id": str(item.get("candidate_id") or ""),
                "operation": "split_candidate_statement",
                "repairable": True,
            }
            for item in diagnostics
            if item.get("code") == "compound_claim_requires_split"
        ]
        return {
            "status": "needs_rework" if issues else "completed",
            "issues": issues,
        }

    return gate


def _evaluation_repair_summary(value: Any) -> dict[str, Any]:
    payload = _mapping(value, "repair_summary")
    return RepairSummary.model_validate(
        {
            "attempt_count": payload.get("attempt_count"),
            "elapsed_seconds": payload.get("elapsed_seconds"),
            "terminal_block_reason": payload.get("terminal_block_reason"),
        }
    ).model_dump(mode="json")


def _quality_case_truth_paths(
    case_path: Path, *, registry: QualityBenchmarkRegistry
) -> tuple[Path, ...]:
    case = load_quality_case(case_path, registry=registry)
    package = case.truth_package
    return tuple(
        case_path.resolve().parent / artifact.path
        for artifact in (
            package.gold_claims,
            package.coverage_universe,
            package.critical_chains,
            package.execution_oracles,
        )
    )


def _benchmark_execution_manifest(case_run_root: Path) -> dict[str, Any] | None:
    payload = _optional_json(case_run_root / "generation_manifest.json", None)
    if not isinstance(payload, Mapping):
        return None
    elapsed = payload.get("elapsed_seconds")
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)):
        raise ValueError("generation manifest requires elapsed_seconds")
    work_sufficiency = payload.get("work_sufficiency")
    if not isinstance(work_sufficiency, Mapping):
        raise ValueError("generation manifest requires work_sufficiency diagnostic")
    workbench_status = str(payload.get("workbench_status") or "").strip().lower()
    if workbench_status not in {"completed", "completed_empty", "needs_review"}:
        raise ValueError("generation manifest contains a non-deliverable Workbench status")
    artifact_manifest = _mapping(
        _read_json(case_run_root / "artifact_hash_manifest.json"),
        "generator artifact hash manifest",
    )
    artifact_root_sha256 = str(artifact_manifest.get("root_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", artifact_root_sha256):
        raise ValueError("generator artifact hash manifest requires root_sha256")
    return {
        "profile": str(payload.get("mode") or "rapid"),
        "generation_wall_clock_seconds": float(elapsed),
        "cache_reuse": bool(payload.get("cache_reused", False)),
        "workbench_status": workbench_status,
        "work_sufficiency": str(work_sufficiency.get("status") or "pending_audit"),
        "work_sufficiency_diagnostic": dict(work_sufficiency),
        "generator_artifact_root_sha256": artifact_root_sha256,
    }


def _current_codetalk_revision() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).parents[3],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("CodeTalk revision is unavailable for benchmark identity")
    revision = completed.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise RuntimeError("CodeTalk benchmark revision must be an immutable commit")
    return revision


def _select_case_paths(
    *,
    registry_path: Path,
    registry: QualityBenchmarkRegistry,
    case_selector: str | None,
    domain_selector: str | None,
    select_all: bool,
) -> list[Path]:
    projects_root = (registry_path.resolve().parent / "projects").resolve()
    discovered = sorted(path.resolve() for path in projects_root.glob("*/*/case.json"))
    if case_selector:
        direct = Path(case_selector)
        if direct.is_file():
            resolved_direct = direct.resolve()
            if resolved_direct not in discovered:
                raise ValueError("benchmark selector must identify a registered case")
            return [resolved_direct]
        matches = [
            path
            for path in discovered
            if str(_mapping(_read_json(path), "quality benchmark case").get("case_id"))
            == case_selector
        ]
    elif domain_selector:
        normalized_domain = domain_selector.strip().lower()
        project_ids = {
            project.id
            for project in registry.projects
            if normalized_domain in PROJECT_DOMAIN_TAGS.get(project.id, frozenset())
        }
        matches = [path for path in discovered if path.parent.parent.name in project_ids]
    elif select_all:
        matches = discovered
    else:
        matches = []
    if not matches:
        raise ValueError("benchmark selector did not match any cases")
    return matches


def _publish_task_run_projection(
    *,
    report_path: Path,
    task_run_dir: Path,
    deadline_monotonic: float | None = None,
) -> Path:
    """Publish a contract-valid report with evaluator-only identifiers erased."""

    payload = _read_json(report_path)
    if (
        deadline_monotonic is not None
        and time.monotonic() >= float(deadline_monotonic)
        and isinstance(payload, dict)
    ):
        failures = [
            dict(item)
            for item in payload.get("hard_failures") or []
            if isinstance(item, Mapping)
        ]
        if not any(
            str(item.get("code") or "") == "WHOLE_CHAIN_DEADLINE_EXCEEDED"
            for item in failures
        ):
            failures.append(_whole_chain_deadline_failure())
        payload["hard_failures"] = failures
        payload["limitations"] = list(
            dict.fromkeys(
                [
                    *[str(item) for item in payload.get("limitations") or []],
                    "WHOLE_CHAIN_DEADLINE_EXCEEDED",
                ]
            )
        )
        payload["delivery_status"] = "not_ready"
    projection = _redact_truth_derived_fields(payload)
    validated = aggregate_quality_evaluation(_mapping(projection, "quality report"))
    destination = task_run_dir / REPORT_FILENAME
    if destination.exists():
        raise FileExistsError(f"immutable task-run quality report already exists: {destination}")
    _atomic_write_noreplace(
        destination, serialize_quality_evaluation(validated).encode("utf-8")
    )
    return destination


def _redact_truth_derived_fields(value: Any) -> Any:
    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        for key, nested in value.items():
            public_key = str(key)
            if public_key == "critical_misses" and isinstance(nested, list):
                projected[public_key] = [
                    {
                        "item_id": f"public-critical-obligation-{index}",
                        "reason": "critical obligation is not satisfied",
                        "validation_layer": str(item.get("validation_layer") or "L2"),
                        "evidence_refs": [],
                    }
                    for index, item in enumerate(nested, start=1)
                    if isinstance(item, Mapping)
                ]
            elif public_key in {"miss_ids", "critical_miss_ids", "evidence_refs"}:
                projected[public_key] = []
            else:
                projected[public_key] = _redact_truth_derived_fields(nested)
        return projected
    if isinstance(value, list):
        return [_redact_truth_derived_fields(item) for item in value]
    return value


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON artifact: {path}") from exc


def _optional_json(path: Path, default: Any) -> Any:
    return _read_json(path) if path.is_file() else default


def _read_first_json(root: Path, names: Sequence[str]) -> Any:
    for name in names:
        path = root / name
        if path.is_file():
            return _read_json(path)
    raise ValueError(f"missing required artifact: {' or '.join(names)}")


def _artifact_snapshot_input_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"artifact snapshot contains a symlink: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return dict(value)


def _items(value: Any) -> Any:
    if isinstance(value, Mapping) and isinstance(value.get("items"), list):
        return value["items"]
    if isinstance(value, Mapping) and isinstance(value.get("claims"), list):
        return value["claims"]
    if isinstance(value, list):
        return value
    raise ValueError("expected a JSON array or an object with items")


_SOURCE_LINE_REF = re.compile(
    r"^(?:source|test)://(?P<path>[^#]+)#(?:[^:]+:)?L(?P<start>[0-9]+)-L(?P<end>[0-9]+)(?::[^#]+)?$"
)


def _normalized_evidence_ref(value: Any) -> tuple[str, int, int] | None:
    if isinstance(value, Mapping):
        path = str(value.get("path") or "").strip().lstrip("/")
        try:
            start = int(value.get("start_line"))
            end = int(value.get("end_line"))
        except (TypeError, ValueError):
            return None
        return (path, start, end) if path and start > 0 and end >= start else None
    match = _SOURCE_LINE_REF.fullmatch(str(value).strip())
    if not match:
        return None
    return (
        match.group("path").lstrip("/"),
        int(match.group("start")),
        int(match.group("end")),
    )


def _evidence_match_key(value: Any) -> tuple[Any, ...] | None:
    normalized = _normalized_evidence_ref(value)
    if normalized is not None:
        return ("range", *normalized)
    if isinstance(value, str) and value.strip():
        return ("opaque", value.strip())
    return None


def _bounded_evidence_match(
    observed: tuple[Any, ...], required: tuple[Any, ...]
) -> bool:
    if observed[0] != "range" or required[0] != "range":
        return observed == required
    _, observed_path, observed_start, observed_end = observed
    _, required_path, required_start, required_end = required
    if observed_path != required_path:
        return False
    observed_length = observed_end - observed_start + 1
    required_length = required_end - required_start + 1
    if observed_start <= required_start and observed_end >= required_end:
        return (
            observed_length <= required_length * 2
            and observed_length - required_length <= 20
        )
    if required_start <= observed_start and required_end >= observed_end:
        return observed_length * 2 >= required_length
    return False


def _complete_evidence_match(
    observed: tuple[Any, ...], required: tuple[Any, ...]
) -> bool:
    if observed[0] != "range" or required[0] != "range":
        return observed == required
    _, observed_path, observed_start, observed_end = observed
    _, required_path, required_start, required_end = required
    if observed_path != required_path:
        return False
    observed_length = observed_end - observed_start + 1
    required_length = required_end - required_start + 1
    return (
        observed_start <= required_start
        and observed_end >= required_end
        and observed_length - required_length <= 40
    )


def _has_bijective_complete_evidence_match(
    observed: frozenset[tuple[Any, ...]],
    required: set[tuple[Any, ...]],
) -> bool:
    if not required or len(observed) != len(required):
        return False
    observed_items = tuple(observed)
    compatible_indices = sorted(
        (
            tuple(
                index
                for index, candidate in enumerate(observed_items)
                if _complete_evidence_match(candidate, obligation)
            )
            for obligation in required
        ),
        key=len,
    )
    if any(not indices for indices in compatible_indices):
        return False

    def assign(position: int, used: frozenset[int]) -> bool:
        if position == len(compatible_indices):
            return True
        return any(
            index not in used and assign(position + 1, used | {index})
            for index in compatible_indices[position]
        )

    return assign(0, frozenset())


def _required_evidence_satisfied(
    required: set[tuple[Any, ...]], observed: set[tuple[Any, ...]]
) -> bool:
    return bool(required) and all(
        any(_bounded_evidence_match(candidate, obligation) for candidate in observed)
        for obligation in required
    )


def _align_claim_semantics_from_evidence(
    claim_ledger: Mapping[str, Any],
    gold_claims: Sequence[Mapping[str, Any]],
    *,
    semantic_verdict_adapter: EvaluatorOwnedSemanticVerdictAdapter | None = None,
    semantic_diagnostic_sink: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    adapter = semantic_verdict_adapter or _DEFAULT_SEMANTIC_VERDICT_ADAPTER
    aligned = json.loads(json.dumps(claim_ledger))
    for claim in aligned.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        # Candidate-authored validation status is never an evaluator oracle.
        claim["l2_status"] = "insufficient"
        claim["semantic_key"] = _unmatched_claim_semantic_key(claim)
        candidate_refs = {
            normalized
            for raw_ref in claim.get("evidence_refs") or []
            for normalized in [_normalized_evidence_ref(raw_ref)]
            if normalized is not None
        }
        matches = {
            str(gold.get("semantic_key") or ""): gold
            for gold in gold_claims
            if str(gold.get("semantic_key") or "")
            and _claim_evidence_requirement_satisfied(gold, candidate_refs)
        }
        exclusive_matches: dict[str, Mapping[str, Any]] = {}
        for candidate_ref in candidate_refs:
            observed_key = ("range", *candidate_ref)
            owners = {
                str(gold.get("semantic_key") or ""): gold
                for gold in gold_claims
                if str(gold.get("semantic_key") or "")
                and any(
                    _bounded_evidence_match(observed_key, required_key)
                    for raw_ref in gold.get("evidence_refs") or []
                    for required_key in [_evidence_match_key(raw_ref)]
                    if required_key is not None
                )
            }
            if len(owners) == 1:
                semantic_key, owner = next(iter(owners.items()))
                if semantic_key in matches:
                    exclusive_matches[semantic_key] = owner
        if len(exclusive_matches) > 1:
            _append_compound_claim_diagnostic(
                semantic_diagnostic_sink,
                claim=claim,
                matched_obligation_count=len(exclusive_matches),
            )
            continue
        factual_truth = {
            "statement": _semantic_statement(claim),
            "evidence_refs": list(_axis_candidate_evidence_refs(claim)),
        }
        claim["l2_status"] = _validated_semantic_verdict(
            adapter.claim_verdict(candidate=claim, truth=factual_truth)
        )
        candidates = exclusive_matches or matches
        verdicts = [
            (
                semantic_key,
                gold,
                _validated_semantic_verdict(
                    adapter.claim_verdict(candidate=claim, truth=gold)
                ),
            )
            for semantic_key, gold in candidates.items()
        ]
        supported = [entry for entry in verdicts if entry[2] == "supports"]
        contradicted = [entry for entry in verdicts if entry[2] == "contradicts"]
        if len(supported) > 1:
            claim["l2_status"] = "insufficient"
            _append_compound_claim_diagnostic(
                semantic_diagnostic_sink,
                claim=claim,
                matched_obligation_count=len(supported),
            )
        elif len(supported) == 1 and not contradicted:
            semantic_key, _, _ = supported[0]
            claim["semantic_key"] = semantic_key
        elif len(contradicted) == 1 and not supported:
            semantic_key, _, _ = contradicted[0]
            claim["semantic_key"] = semantic_key
            claim["l2_status"] = "contradicts"
        elif contradicted:
            claim["l2_status"] = "contradicts"
    return aligned


def _append_compound_claim_diagnostic(
    sink: list[dict[str, Any]] | None,
    *,
    claim: Mapping[str, Any],
    matched_obligation_count: int,
) -> None:
    if sink is None:
        return
    sink.append(
        {
            "axis": "accuracy",
            "code": "compound_claim_requires_split",
            "candidate_id": str(claim.get("claim_id") or ""),
            "matched_obligation_count": matched_obligation_count,
            "repairable": True,
            "repair": {
                "artifact": "claim_ledger.json",
                "operation": "split_candidate_statement",
            },
        }
    )


def _unmatched_claim_semantic_key(claim: Mapping[str, Any]) -> str:
    payload = {
        "claim_id": str(claim.get("claim_id") or ""),
        "statement": _semantic_statement(claim),
        "evidence_refs": list(_axis_candidate_evidence_refs(claim)),
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"candidate-unmatched-{digest}"


def _claim_evidence_requirement_satisfied(
    gold: Mapping[str, Any], candidate_refs: set[tuple[str, int, int]]
) -> bool:
    gold_refs = {
        normalized
        for raw_ref in gold.get("evidence_refs") or []
        for normalized in [_normalized_evidence_ref(raw_ref)]
        if normalized is not None
    }
    if not gold_refs:
        return False
    policy = gold.get("evidence_policy")
    if policy is None:
        policy = gold.get("evidence_requirement")
    mode = str(policy or "all").strip().lower() if not isinstance(policy, Mapping) else str(
        policy.get("mode") or "all"
    ).strip().lower()
    if mode == "all":
        return _required_evidence_satisfied(
            {("range", *item) for item in gold_refs},
            {("range", *item) for item in candidate_refs},
        )
    if mode != "groups" or not isinstance(policy, Mapping):
        return False
    raw_groups = policy.get("groups")
    if not isinstance(raw_groups, list) or not raw_groups:
        return False
    groups: list[set[tuple[str, int, int]]] = []
    for raw_group in raw_groups:
        if not isinstance(raw_group, list) or not raw_group:
            return False
        group = {
            normalized
            for raw_ref in raw_group
            for normalized in [_normalized_evidence_ref(raw_ref)]
            if normalized is not None
        }
        if not group:
            return False
        groups.append(group)
    observed = {("range", *item) for item in candidate_refs}
    return all(
        any(
            _bounded_evidence_match(candidate, ("range", *obligation))
            for candidate in observed
            for obligation in group
        )
        for group in groups
    )


_STRICT_NEGATIONS = frozenset({"cannot", "never", "no", "not", "without"})
_SEMANTIC_ANTONYMS = (
    (frozenset({"append", "add", "insert"}), frozenset({"remove", "delete", "erase"})),
    (frozenset({"accept", "allow", "permit"}), frozenset({"deny", "reject"})),
    (frozenset({"enable", "enabled"}), frozenset({"disable", "disabled"})),
    (frozenset({"reachable"}), frozenset({"unreachable"})),
    (frozenset({"success", "successful"}), frozenset({"fail", "failed", "failure"})),
)
_SEMANTIC_ARTICLES = frozenset({"a", "an", "the"})
_PASSIVE_AUXILIARIES = frozenset({"am", "are", "been", "being", "is", "was", "were"})
_CONTROLLED_INFLECTIONS = {
    "added": "add",
    "adds": "add",
    "allocated": "allocate",
    "allocates": "allocate",
    "appended": "append",
    "appends": "append",
    "called": "call",
    "calls": "call",
    "checked": "check",
    "checks": "check",
    "completed": "complete",
    "completes": "complete",
    "completing": "complete",
    "deleted": "delete",
    "deletes": "delete",
    "drained": "drain",
    "drains": "drain",
    "erased": "erase",
    "erases": "erase",
    "evicted": "evict",
    "evicts": "evict",
    "failed": "fail",
    "fails": "fail",
    "incremented": "increment",
    "increments": "increment",
    "inserted": "insert",
    "inserts": "insert",
    "marked": "mark",
    "marks": "mark",
    "moved": "move",
    "moves": "move",
    "recorded": "record",
    "records": "record",
    "rejected": "reject",
    "rejects": "reject",
    "released": "release",
    "releases": "release",
    "removed": "remove",
    "removes": "remove",
    "reserved": "reserve",
    "reserves": "reserve",
    "resumed": "resume",
    "resumes": "resume",
    "returned": "return",
    "returns": "return",
    "selected": "select",
    "selects": "select",
    "stored": "store",
    "stores": "store",
}


def _axis_candidate_statement(
    axis: Literal["accuracy", "breadth", "depth"],
    payload: Mapping[str, Any],
) -> str:
    if axis == "accuracy":
        return _semantic_statement(payload)
    return str(payload.get("narrative") or payload.get("statement") or "").strip()


def _axis_oracle_statement(
    axis: Literal["accuracy", "breadth", "depth"],
    payload: Mapping[str, Any],
    binding: Any,
) -> str:
    explicit = str(
        payload.get("claim")
        or payload.get("statement")
        or payload.get("narrative")
        or payload.get("description")
        or ""
    ).strip()
    if explicit:
        if axis == "depth":
            source_node_id = str(payload.get("source_node_id") or "").strip()
            target_node_id = str(payload.get("target_node_id") or "").strip()
            if source_node_id or target_node_id:
                endpoints = "; ".join(
                    value
                    for value in (
                        f"source_node_id={source_node_id}" if source_node_id else "",
                        f"target_node_id={target_node_id}" if target_node_id else "",
                    )
                    if value
                )
                return f"{explicit} [{endpoints}]"
        return explicit
    if axis == "breadth":
        return ""
    if axis == "depth":
        fields = [
            str(payload.get(key) or "").strip()
            for key in (
                "kind",
                "node_id",
                "edge_id",
                "check_id",
                "source",
                "target",
                "from",
                "to",
                "condition",
                "expected",
            )
            if str(payload.get(key) or "").strip()
        ]
        if fields:
            return "Close the source-backed causal obligation: " + "; ".join(fields)
        bindings = _depth_binding_sequence(binding)
        if bindings:
            return (
                "Close the source-backed causal obligation "
                + str(getattr(bindings[0], "obligation_id", "")).strip()
            )
    return ""


def _axis_oracle_evidence_refs(
    axis: Literal["accuracy", "breadth", "depth"],
    truth: Mapping[str, Any],
    binding: Any,
) -> tuple[str, ...]:
    if axis == "depth":
        values = [
            str(getattr(item, "evidence_ref", "") or "").strip()
            for item in _depth_binding_sequence(binding)
        ]
    else:
        values = [str(item).strip() for item in truth.get("evidence_refs") or []]
    return tuple(dict.fromkeys(value for value in values if value))


def _axis_candidate_evidence_refs(candidate: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for raw_ref in candidate.get("evidence_refs") or []:
        if isinstance(raw_ref, str) and raw_ref.strip():
            values.append(raw_ref.strip())
            continue
        normalized = _normalized_evidence_ref(raw_ref)
        if normalized is None:
            continue
        path, start_line, end_line = normalized
        values.append(f"source://{path}#L{start_line}-L{end_line}")
    return tuple(dict.fromkeys(values))


def _depth_binding_sequence(binding: Any) -> tuple[Any, ...]:
    if isinstance(binding, (tuple, list)):
        return tuple(binding)
    return (binding,) if binding is not None else ()


def _semantic_judgment_id(
    *,
    axis: str,
    candidate_statement: str,
    oracle_statement: str,
    observed_evidence_refs: Sequence[str],
    required_evidence_refs: Sequence[str],
) -> str:
    payload = {
        "axis": axis,
        "candidate_statement": candidate_statement,
        "oracle_statement": oracle_statement,
        "observed_evidence_refs": sorted(set(observed_evidence_refs)),
        "required_evidence_refs": sorted(set(required_evidence_refs)),
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"{axis}-{digest}"


def _semantic_statement(payload: Mapping[str, Any]) -> str:
    return str(payload.get("claim") or payload.get("statement") or "").strip()


def _claim_semantic_status(candidate: str, gold: str) -> str:
    """Accept exact or mechanically provable voice/inflection equivalence."""

    candidate_tokens = _semantic_tokens(candidate)
    gold_tokens = _semantic_tokens(gold)
    if not candidate_tokens or not gold_tokens:
        return "insufficient"
    if _semantic_variants(candidate_tokens).intersection(_semantic_variants(gold_tokens)):
        return "supports"
    candidate_set = set(candidate_tokens)
    gold_set = set(gold_tokens)
    if candidate_set.intersection(_STRICT_NEGATIONS) - gold_set:
        return "contradicts"
    for positive, negative in _SEMANTIC_ANTONYMS:
        gold_positive = bool(gold_set.intersection(positive))
        gold_negative = bool(gold_set.intersection(negative))
        candidate_positive = bool(candidate_set.intersection(positive))
        candidate_negative = bool(candidate_set.intersection(negative))
        if gold_positive != gold_negative and (
            (gold_positive and candidate_negative and not candidate_positive)
            or (gold_negative and candidate_positive and not candidate_negative)
        ):
            return "contradicts"
    return "insufficient"


def _semantic_variants(tokens: tuple[str, ...]) -> frozenset[tuple[str, ...]]:
    canonical = tuple(
        _CONTROLLED_INFLECTIONS.get(token, token)
        for token in tokens
        if token not in _SEMANTIC_ARTICLES
    )
    variants = {canonical}
    for auxiliary_index, token in enumerate(canonical):
        if token not in _PASSIVE_AUXILIARIES or auxiliary_index + 1 >= len(canonical):
            continue
        try:
            by_index = canonical.index("by", auxiliary_index + 2)
        except ValueError:
            continue
        object_tokens = canonical[:auxiliary_index]
        verb = canonical[auxiliary_index + 1]
        predicate_tail = canonical[auxiliary_index + 2:by_index]
        subject_tokens = canonical[by_index + 1:]
        if object_tokens and subject_tokens and verb in set(_CONTROLLED_INFLECTIONS.values()):
            variants.add(subject_tokens + (verb,) + object_tokens + predicate_tail)
    return frozenset(variants)


def _semantic_tokens(value: str) -> tuple[str, ...]:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return tuple(
        token
        for raw in re.findall(r"-?[A-Za-z0-9_]+", normalized.lower())
        for token in raw.replace("_", " ").split()
        if token
    )


def _validated_semantic_verdict(value: Any) -> SemanticVerdict:
    if value not in {"supports", "contradicts", "insufficient"}:
        raise ValueError("semantic verdict adapter returned an invalid verdict")
    return value


def _align_breadth_evidence_refs(
    universe: Any,
    *artifacts: Any,
    semantic_verdict_adapter: EvaluatorOwnedSemanticVerdictAdapter | None = None,
) -> tuple[Any, ...]:
    adapter = semantic_verdict_adapter or _DEFAULT_SEMANTIC_VERDICT_ADAPTER
    universe_items = _items(universe)

    def align(value: Any) -> Any:
        copied = json.loads(json.dumps(value))

        def visit(current: Any) -> None:
            if isinstance(current, dict):
                hidden_ids = {
                    str(item.get("item_id") or "") for item in universe_items
                }
                for field in (
                    "coverage_item_ids",
                    "universe_item_ids",
                    "obligation_ids",
                    "coverage_target_ids",
                ):
                    if field in current:
                        current[field] = [
                            item_id
                            for item_id in current.get(field) or []
                            if str(item_id) not in hidden_ids
                        ]
                for field in (
                    "item_id",
                    "coverage_item_id",
                    "universe_item_id",
                    "resource_id",
                    "flow_id",
                ):
                    if str(current.get(field) or "") in hidden_ids:
                        current[field] = f"public-untrusted-{field}"
                refs = current.get("evidence_refs")
                if isinstance(refs, list):
                    observed_keys = {
                        match_key
                        for ref in refs
                        for match_key in [_evidence_match_key(ref)]
                        if match_key is not None
                    }
                    candidates = {
                        str(item.get("item_id") or ""): item
                        for item in universe_items
                        if isinstance(item, Mapping)
                        if str(item.get("item_id") or "")
                        if _breadth_evidence_requirement_satisfied(
                            item, observed_keys
                        )
                    }
                    supported = [
                        item
                        for item in candidates.values()
                        if _validated_semantic_verdict(
                            adapter.breadth_verdict(candidate=current, truth=item)
                        )
                        == "supports"
                    ]
                    current["evidence_refs"] = list(
                        dict.fromkeys(
                            str(raw_ref)
                            for item in supported
                            for raw_ref in item.get("evidence_refs") or []
                            if _evidence_match_key(raw_ref) is not None
                        )
                    )
                for nested in current.values():
                    visit(nested)
            elif isinstance(current, list):
                for nested in current:
                    visit(nested)

        visit(copied)
        return copied

    return tuple(align(artifact) for artifact in artifacts)


def _breadth_evidence_requirement_satisfied(
    truth: Mapping[str, Any], observed_keys: set[tuple[Any, ...]]
) -> bool:
    required = {
        match_key
        for raw_ref in truth.get("evidence_refs") or []
        for match_key in [_evidence_match_key(raw_ref)]
        if match_key is not None
    }
    return bool(required) and all(
        any(
            _complete_evidence_match(candidate, obligation)
            for candidate in observed_keys
        )
        for obligation in required
    )


def _align_depth_candidate_from_evidence(
    truth: Any,
    candidate: Any,
    catalog: DepthEvidenceCatalog,
    *,
    semantic_verdict_adapter: EvaluatorOwnedSemanticVerdictAdapter | None = None,
    evaluator_l3: Mapping[str, Any] | None = None,
) -> Any:
    adapter = semantic_verdict_adapter or _DEFAULT_SEMANTIC_VERDICT_ADAPTER
    if not isinstance(candidate, Mapping):
        return candidate
    truth_chains = {
        str(chain.get("chain_id") or ""): chain
        for chain in _mapping(truth, "critical_chains").get("chains") or []
        if isinstance(chain, Mapping)
    }
    truth_obligations: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for chain_id, truth_chain in truth_chains.items():
        for category, field, id_field in (
            ("node", "nodes", "node_id"),
            ("edge", "edges", "edge_id"),
            ("check", "disconfirming_checks", "check_id"),
        ):
            for obligation in truth_chain.get(field) or []:
                if isinstance(obligation, Mapping):
                    obligation_id = str(obligation.get(id_field) or "")
                    if obligation_id:
                        truth_obligations[(chain_id, category, obligation_id)] = obligation

    public_observations: dict[str, list[Mapping[str, Any]]] = {
        "node": [],
        "edge": [],
        "check": [],
    }
    for chain in candidate.get("chains") or []:
        if not isinstance(chain, Mapping):
            continue
        for category, field, id_field in (
            ("node", "nodes", "node_id"),
            ("edge", "edges", "edge_id"),
            ("check", "disconfirming_checks", "check_id"),
        ):
            for observed in chain.get(field) or []:
                if not isinstance(observed, Mapping):
                    continue
                public_observations[category].append(observed)

    bindings_by_category: dict[
        str, dict[tuple[str, str, str], list[Any]]
    ] = {"node": {}, "edge": {}, "check": {}}
    for binding in catalog.bindings:
        category = binding.category.value
        key = (binding.chain_id, category, binding.obligation_id)
        if category in bindings_by_category and key in truth_obligations:
            bindings_by_category[category].setdefault(key, []).append(binding)

    matched: dict[
        tuple[str, str, str], tuple[Mapping[str, Any], tuple[Any, ...]]
    ] = {}
    for category, observations in public_observations.items():
        for observed in observations:
            raw_observed_refs = tuple(observed.get("evidence_refs") or ())
            observed_ref_keys = tuple(
                _evidence_match_key(raw_ref) for raw_ref in raw_observed_refs
            )
            if any(match_key is None for match_key in observed_ref_keys):
                continue
            observed_refs = frozenset(observed_ref_keys)
            if len(observed_refs) != len(raw_observed_refs):
                continue
            potential: dict[tuple[str, str, str], tuple[Any, ...]] = {}
            for key, bindings in bindings_by_category[category].items():
                binding_groups: dict[str, list[Any]] = {}
                for binding in bindings:
                    binding_groups.setdefault(binding.evidence_group, []).append(
                        binding
                    )
                for group_id in sorted(binding_groups):
                    group = tuple(binding_groups[group_id])
                    required_refs = {
                        match_key
                        for binding in group
                        for match_key in [
                            _evidence_match_key(binding.evidence_ref)
                        ]
                        if match_key is not None
                    }
                    if _has_bijective_complete_evidence_match(
                        observed_refs,
                        required_refs,
                    ):
                        potential[key] = group
                        break
            supported = []
            for key, bindings in potential.items():
                verdict = adapter.depth_verdict(
                    candidate=observed,
                    truth=truth_obligations[key],
                    binding=bindings,
                )
                if _validated_semantic_verdict(verdict) == "supports":
                    supported.append((key, bindings))
            for key, bindings in supported:
                existing = matched.get(key)
                success_statuses = (
                    frozenset({"closed", "pass"})
                    if category == "check"
                    else frozenset({"closed"})
                )
                observed_success = (
                    str(observed.get("status") or "closed").strip().lower()
                    in success_statuses
                )
                existing_success = existing is not None and (
                    str(existing[0].get("status") or "closed").strip().lower()
                    in success_statuses
                )
                if existing is None or (observed_success and not existing_success):
                    matched[key] = (observed, bindings)

    aligned_chains: list[dict[str, Any]] = []
    for chain_id, truth_chain in truth_chains.items():
        aligned: dict[str, Any] = {
            "chain_id": chain_id,
            "nodes": [],
            "edges": [],
            "disconfirming_checks": [],
            "narrative": "evaluator-aligned from public source evidence",
        }
        for category, field, id_field in (
            ("node", "nodes", "node_id"),
            ("edge", "edges", "edge_id"),
            ("check", "disconfirming_checks", "check_id"),
        ):
            truth_ids = [
                str(item.get(id_field) or "")
                for item in truth_chain.get(field) or []
                if isinstance(item, Mapping)
            ]
            for obligation_id in truth_ids:
                match = matched.get((chain_id, category, obligation_id))
                if match is None:
                    continue
                observed, bindings = match
                status = str(observed.get("status") or "closed")
                aligned[field].append(
                    {
                        id_field: obligation_id,
                        "status": "pass" if category == "check" and status == "closed" else status,
                        "evidence_refs": [
                            binding.evidence_ref for binding in bindings
                        ],
                    }
                )
        aligned_chains.append(aligned)
    result = {"chains": aligned_chains}
    if evaluator_l3 is not None:
        result["l3"] = dict(evaluator_l3)
    return result


def _artifact_value(bundle: Any, root: Path, key: str, filename: str) -> Any:
    if isinstance(bundle, Mapping) and key in bundle:
        return bundle[key]
    return _optional_json(root / filename, [])


def _atomic_write(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _atomic_write_noreplace(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_name, path)
        except FileExistsError as exc:
            raise FileExistsError(
                f"immutable task-run quality report already exists: {path}"
            ) from exc
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _rename_directory_noreplace(source: Path, destination: Path) -> None:
    """Atomically publish a complete directory without replacing a target."""

    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        rename_exclusive = libc.renamex_np
        rename_exclusive.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename_exclusive.restype = ctypes.c_int
        result = rename_exclusive(
            os.fsencode(source), os.fsencode(destination), 0x00000004
        )
        if result == 0:
            return
        error = ctypes.get_errno()
        if error in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(
                f"immutable benchmark output already exists: {destination}"
            )
        raise OSError(error, os.strerror(error), str(destination))
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        rename_exclusive = getattr(libc, "renameat2", None)
        if rename_exclusive is not None:
            rename_exclusive.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            rename_exclusive.restype = ctypes.c_int
            result = rename_exclusive(
                -100, os.fsencode(source), -100, os.fsencode(destination), 1
            )
            if result == 0:
                return
            error = ctypes.get_errno()
            if error in {errno.EEXIST, errno.ENOTEMPTY}:
                raise FileExistsError(
                    f"immutable benchmark output already exists: {destination}"
                )
            raise OSError(error, os.strerror(error), str(destination))

    raise OSError(
        errno.ENOTSUP,
        "atomic no-replace directory publication is unsupported on this platform",
        str(destination),
    )


def _paths_overlap(left: Path, right: Path) -> bool:
    left = left.resolve()
    right = right.resolve()
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


if __name__ == "__main__":
    raise SystemExit(main())
