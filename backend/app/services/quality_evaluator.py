"""Conjunctive report assembly and fail-closed report persistence for F012."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from app.services.quality_accuracy_evaluator import evaluate_accuracy
from app.services.quality_breadth_evaluator import evaluate_breadth
from app.services.quality_depth_evaluator import evaluate_depth
from app.services.quality_evaluation_contract import (
    SCHEMA_VERSION,
    BenchmarkIdentity,
    DeliveryStatus,
    EvaluationScope,
    EvaluationSnapshot,
    HardFailure,
    QualityEvaluationReport,
    RepairSummary,
    serialize_quality_evaluation,
    validate_quality_evaluation,
)


class QualityEvaluationStoreError(ValueError):
    """Raised when a stored quality evaluation cannot be trusted."""


def evaluate_quality_snapshot(
    *,
    accuracy_inputs: Mapping[str, Any],
    breadth_inputs: Mapping[str, Any],
    depth_inputs: Mapping[str, Any],
) -> EvaluationSnapshot:
    """Evaluate each independent axis exactly once."""

    return EvaluationSnapshot(
        accuracy=evaluate_accuracy(**dict(accuracy_inputs)),
        breadth=evaluate_breadth(**dict(breadth_inputs)),
        depth=evaluate_depth(**dict(depth_inputs)),
    )


def build_quality_report(
    *,
    scope: EvaluationScope | str,
    run_ref: str,
    benchmark_identity: BenchmarkIdentity | Mapping[str, Any] | None,
    first_pass: EvaluationSnapshot | Mapping[str, Any],
    final_after_auto_repair: EvaluationSnapshot | Mapping[str, Any],
    repair_summary: RepairSummary | Mapping[str, Any],
    hard_failures: tuple[HardFailure, ...] | list[Mapping[str, Any]] = (),
    limitations: tuple[str, ...] | list[str] = (),
) -> QualityEvaluationReport:
    """Build a report whose delivery decision is a strict three-axis gate."""

    normalized_first = _model(EvaluationSnapshot, first_pass)
    normalized_final = _model(EvaluationSnapshot, final_after_auto_repair)
    normalized_repair = _model(RepairSummary, repair_summary)
    normalized_identity = (
        None if benchmark_identity is None else _model(BenchmarkIdentity, benchmark_identity)
    )
    normalized_failures = tuple(
        item if isinstance(item, HardFailure) else HardFailure.model_validate(item)
        for item in hard_failures
    )
    normalized_limitations = _unique_strings(
        (*limitations, *_snapshot_limitations(normalized_final))
    )
    delivery_status = _derive_delivery_status(
        normalized_final,
        hard_failures=normalized_failures,
        limitations=normalized_limitations,
        terminal_block_reason=normalized_repair.terminal_block_reason,
    )
    return validate_quality_evaluation(
        QualityEvaluationReport(
            schema_version=SCHEMA_VERSION,
            scope=scope,
            run_ref=run_ref,
            benchmark_identity=normalized_identity,
            delivery_status=delivery_status,
            first_pass=normalized_first,
            final_after_auto_repair=normalized_final,
            repair_summary=normalized_repair,
            hard_failures=normalized_failures,
            limitations=normalized_limitations,
        )
    )


def _model(model_type: type[Any], value: Any) -> Any:
    if isinstance(value, model_type):
        return value
    return model_type.model_validate(value)


def _snapshot_limitations(snapshot: EvaluationSnapshot) -> tuple[str, ...]:
    return _unique_strings(
        limitation
        for axis in (snapshot.accuracy, snapshot.breadth, snapshot.depth)
        for limitation in axis.limitations
    )


def _unique_strings(values: Any) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value).strip()))


def _derive_delivery_status(
    snapshot: EvaluationSnapshot,
    *,
    hard_failures: tuple[HardFailure, ...],
    limitations: tuple[str, ...],
    terminal_block_reason: str | None,
) -> DeliveryStatus:
    statuses = (snapshot.accuracy.status.value, snapshot.breadth.status.value, snapshot.depth.status.value)
    if hard_failures or terminal_block_reason is not None or "fail" in statuses:
        return DeliveryStatus.NOT_READY
    if limitations or "limited" in statuses:
        return DeliveryStatus.LIMITED
    return DeliveryStatus.READY


def aggregate_quality_evaluation(
    report: QualityEvaluationReport | Mapping[str, Any],
) -> QualityEvaluationReport:
    """Validate a precomputed three-axis report without recomputing its axes.

    Delivery is intentionally derived by the frozen contract from the final
    Accuracy, Breadth, and Depth results.  This boundary must never add a score
    or weaken an axis failure into an aggregate outcome.
    """

    return validate_quality_evaluation(
        report if isinstance(report, QualityEvaluationReport) else dict(report)
    )


class QualityEvaluationStore:
    """Atomic, run-reference-addressable persistence for validated reports."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def write(self, report: QualityEvaluationReport | Mapping[str, Any]) -> Path:
        validated = aggregate_quality_evaluation(report)
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self.root / f"{_safe_run_ref(validated.run_ref)}.json"
        if destination.exists():
            raise QualityEvaluationStoreError(
                f"quality evaluation already exists: {validated.run_ref}"
            )
        serialized = serialize_quality_evaluation(validated)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.root,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            text=True,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary_name, destination)
            except FileExistsError as exc:
                raise QualityEvaluationStoreError(
                    f"quality evaluation already exists: {validated.run_ref}"
                ) from exc
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return destination

    def read(self, run_ref: str) -> QualityEvaluationReport:
        path = self.root / f"{_safe_run_ref(run_ref)}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise
        except (OSError, json.JSONDecodeError) as exc:
            raise QualityEvaluationStoreError(
                f"invalid stored quality evaluation: {run_ref}"
            ) from exc
        try:
            return aggregate_quality_evaluation(payload)
        except Exception as exc:
            raise QualityEvaluationStoreError(
                f"invalid stored quality evaluation: {run_ref}"
            ) from exc


def _safe_run_ref(run_ref: str) -> str:
    if not isinstance(run_ref, str) or not run_ref or run_ref != run_ref.strip():
        raise QualityEvaluationStoreError("run_ref must be a non-empty string")
    if any(character in run_ref for character in ("/", "\\", "\x00")) or run_ref in {
        ".",
        "..",
    }:
        raise QualityEvaluationStoreError("run_ref is not a safe report identifier")
    return run_ref
