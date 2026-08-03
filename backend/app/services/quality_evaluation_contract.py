"""Strict, immutable contract for operational and benchmark quality reports."""

from __future__ import annotations

import json
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveInt,
    StringConstraints,
    model_validator,
)

SCHEMA_VERSION = "quality-evaluation-v1"
EVALUATOR_VERSION = "quality-evaluation-v5"

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
CommitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
TruthPackageVersion = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]*$")]


class EvaluationScope(str, Enum):
    OPERATIONAL = "operational"
    INDEPENDENT_BENCHMARK = "independent_benchmark"


class AxisStatus(str, Enum):
    PASS = "pass"
    LIMITED = "limited"
    FAIL = "fail"


class DeliveryStatus(str, Enum):
    READY = "ready"
    LIMITED = "limited"
    NOT_READY = "not_ready"


class ValidationLayer(str, Enum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class LayerStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_RUN = "not_run"
    NOT_APPLICABLE = "not_applicable"


class MetricName(str, Enum):
    CLAIM_PRECISION = "claim_precision"
    GOLD_RECALL = "gold_recall"
    DISCOVERY_RECALL = "discovery_recall"
    CRITICAL_COVERAGE = "critical_coverage"
    SCENARIO_REALIZATION = "scenario_realization"
    DISPOSITION_COMPLETENESS = "disposition_completeness"
    MINIMUM_CRITICAL_CHAIN_CLOSURE = "minimum_critical_chain_closure"
    AVERAGE_CHAIN_CLOSURE = "average_chain_closure"
    STATE_CLOSURE = "state_closure"
    RESOURCE_LIFECYCLE_CLOSURE = "resource_lifecycle_closure"
    ERROR_RECOVERY_CLOSURE = "error_recovery_closure"
    DISCONFIRMING_CHECKS = "disconfirming_checks"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RatioMetric(ContractModel):
    name: MetricName = Field(strict=False)
    numerator: NonNegativeInt
    denominator: PositiveInt
    miss_ids: tuple[NonEmptyString, ...] = Field(strict=False)

    @model_validator(mode="after")
    def numerator_does_not_exceed_denominator(self) -> "RatioMetric":
        if self.numerator > self.denominator:
            raise ValueError("metric numerator cannot exceed denominator")
        return self


class ValidationLayerOutcome(ContractModel):
    status: LayerStatus = Field(strict=False)
    numerator: NonNegativeInt
    denominator: PositiveInt
    critical_miss_ids: tuple[NonEmptyString, ...] = Field(strict=False)
    evidence_refs: tuple[NonEmptyString, ...] = Field(strict=False)
    limitations: tuple[NonEmptyString, ...] = Field(strict=False)

    @model_validator(mode="after")
    def validate_counts_and_critical_status(self) -> "ValidationLayerOutcome":
        if self.numerator > self.denominator:
            raise ValueError("validation layer numerator cannot exceed denominator")
        if self.critical_miss_ids and self.status is not LayerStatus.FAIL:
            raise ValueError("critical_miss_ids require validation layer status fail")
        return self


class ValidationLayers(ContractModel):
    l0: ValidationLayerOutcome = Field(alias="L0")
    l1: ValidationLayerOutcome = Field(alias="L1")
    l2: ValidationLayerOutcome = Field(alias="L2")
    l3: ValidationLayerOutcome = Field(alias="L3")


class CriticalMiss(ContractModel):
    item_id: NonEmptyString
    reason: NonEmptyString
    validation_layer: ValidationLayer = Field(strict=False)
    evidence_refs: tuple[NonEmptyString, ...] = Field(strict=False)


class AxisResult(ContractModel):
    status: AxisStatus = Field(strict=False)
    numerator: NonNegativeInt
    denominator: PositiveInt
    critical_misses: tuple[CriticalMiss, ...] = Field(strict=False)
    evidence_refs: tuple[NonEmptyString, ...] = Field(strict=False)
    limitations: tuple[NonEmptyString, ...] = Field(strict=False)
    validation_layers: ValidationLayers
    metrics: tuple[RatioMetric, ...] = Field(strict=False)

    @model_validator(mode="after")
    def validate_axis_invariants(self) -> "AxisResult":
        if self.numerator > self.denominator:
            raise ValueError("axis numerator cannot exceed denominator")
        if self.critical_misses and self.status is not AxisStatus.FAIL:
            raise ValueError("critical_misses require axis status fail")
        layer_outcomes = (
            self.validation_layers.l0,
            self.validation_layers.l1,
            self.validation_layers.l2,
            self.validation_layers.l3,
        )
        if (
            any(layer.status is LayerStatus.FAIL for layer in layer_outcomes)
            and self.status is not AxisStatus.FAIL
        ):
            raise ValueError("a failed validation layer requires axis status fail")
        has_limitation = bool(self.limitations) or any(
            layer.status is LayerStatus.NOT_RUN or bool(layer.limitations)
            for layer in layer_outcomes
        )
        if has_limitation and self.status is AxisStatus.PASS:
            raise ValueError(
                "a not_run validation layer or limitation requires axis status limited or fail"
            )
        metric_names = [metric.name for metric in self.metrics]
        if len(metric_names) != len(set(metric_names)):
            raise ValueError("metric names must be unique within an axis")
        return self


class EvaluationSnapshot(ContractModel):
    accuracy: AxisResult
    breadth: AxisResult
    depth: AxisResult

    @model_validator(mode="after")
    def require_axis_metrics(self) -> "EvaluationSnapshot":
        required = {
            "accuracy": {MetricName.CLAIM_PRECISION},
            "breadth": {
                MetricName.DISCOVERY_RECALL,
                MetricName.CRITICAL_COVERAGE,
                MetricName.SCENARIO_REALIZATION,
                MetricName.DISPOSITION_COMPLETENESS,
            },
            "depth": {
                MetricName.MINIMUM_CRITICAL_CHAIN_CLOSURE,
                MetricName.AVERAGE_CHAIN_CLOSURE,
                MetricName.STATE_CLOSURE,
                MetricName.RESOURCE_LIFECYCLE_CLOSURE,
                MetricName.ERROR_RECOVERY_CLOSURE,
                MetricName.DISCONFIRMING_CHECKS,
            },
        }
        for axis_name, expected in required.items():
            actual = {metric.name for metric in getattr(self, axis_name).metrics}
            missing = sorted(metric.value for metric in expected - actual)
            if missing:
                raise ValueError(
                    f"{axis_name} is missing required metrics: {', '.join(missing)}"
                )
        return self


class BenchmarkIdentity(ContractModel):
    case_id: NonEmptyString
    source_revision: CommitSha
    truth_package_version: TruthPackageVersion


class RepairSummary(ContractModel):
    attempt_count: NonNegativeInt
    elapsed_seconds: NonNegativeFloat
    terminal_block_reason: NonEmptyString | None


class HardFailure(ContractModel):
    code: NonEmptyString
    message: NonEmptyString
    evidence_refs: tuple[NonEmptyString, ...] = Field(strict=False)
    unrecoverable: bool


class QualityEvaluationReport(ContractModel):
    schema_version: Literal[SCHEMA_VERSION]
    scope: EvaluationScope = Field(strict=False)
    run_ref: NonEmptyString
    benchmark_identity: BenchmarkIdentity | None
    delivery_status: DeliveryStatus = Field(strict=False)
    first_pass: EvaluationSnapshot
    final_after_auto_repair: EvaluationSnapshot
    repair_summary: RepairSummary
    hard_failures: tuple[HardFailure, ...] = Field(strict=False)
    limitations: tuple[NonEmptyString, ...] = Field(strict=False)

    @model_validator(mode="after")
    def validate_scope_and_delivery(self) -> "QualityEvaluationReport":
        snapshots = (self.first_pass, self.final_after_auto_repair)
        if self.scope is EvaluationScope.INDEPENDENT_BENCHMARK:
            if self.benchmark_identity is None:
                raise ValueError(
                    "benchmark_identity is required for independent_benchmark scope"
                )
            for snapshot in snapshots:
                if MetricName.GOLD_RECALL not in _metric_names(snapshot.accuracy):
                    raise ValueError(
                        "gold_recall is required for independent_benchmark accuracy"
                    )
        else:
            if self.benchmark_identity is not None:
                raise ValueError(
                    "benchmark_identity is forbidden for operational scope"
                )
            for snapshot in snapshots:
                if MetricName.GOLD_RECALL in _metric_names(snapshot.accuracy):
                    raise ValueError("gold_recall is forbidden for operational scope")

        expected_delivery = _delivery_status(
            self.final_after_auto_repair,
            hard_failures=self.hard_failures,
            limitations=self.limitations,
            terminal_block_reason=self.repair_summary.terminal_block_reason,
        )
        if self.delivery_status is not expected_delivery:
            raise ValueError(
                "delivery_status must be derived from final_after_auto_repair, "
                "hard_failures, and limitations"
            )
        if (
            self.repair_summary.terminal_block_reason is not None
            and self.delivery_status is not DeliveryStatus.NOT_READY
        ):
            raise ValueError(
                "terminal_block_reason requires delivery_status not_ready"
            )
        if (
            self.first_pass != self.final_after_auto_repair
            and self.repair_summary.attempt_count == 0
        ):
            raise ValueError(
                "attempt_count must be greater than zero when final_after_auto_repair differs from first_pass"
            )
        return self


def _metric_names(axis: AxisResult) -> set[MetricName]:
    return {metric.name for metric in axis.metrics}


def _delivery_status(
    snapshot: EvaluationSnapshot,
    *,
    hard_failures: tuple[HardFailure, ...],
    limitations: tuple[str, ...],
    terminal_block_reason: str | None,
) -> DeliveryStatus:
    statuses = (snapshot.accuracy.status, snapshot.breadth.status, snapshot.depth.status)
    if hard_failures or terminal_block_reason is not None or AxisStatus.FAIL in statuses:
        return DeliveryStatus.NOT_READY
    if limitations or AxisStatus.LIMITED in statuses:
        return DeliveryStatus.LIMITED
    return DeliveryStatus.READY


def validate_quality_evaluation(
    report: QualityEvaluationReport | dict[str, Any],
) -> QualityEvaluationReport:
    """Fail closed through the complete Pydantic runtime contract."""

    payload = (
        report.model_dump(mode="python", by_alias=True)
        if isinstance(report, QualityEvaluationReport)
        else report
    )
    return QualityEvaluationReport.model_validate(payload)


def serialize_quality_evaluation(
    report: QualityEvaluationReport | dict[str, Any],
) -> str:
    """Validate and serialize a report with stable keys and separators."""

    validated = validate_quality_evaluation(report)
    payload = validated.model_dump(mode="json", by_alias=True)
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"


def quality_evaluation_json_schema() -> dict[str, Any]:
    """Return the structural authoring/CI schema; Pydantic remains authoritative."""

    schema = QualityEvaluationReport.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["allOf"] = [
        _scope_schema_condition(
            scope=EvaluationScope.OPERATIONAL,
            benchmark_identity={"type": "null"},
            require_gold_recall=False,
        ),
        _scope_schema_condition(
            scope=EvaluationScope.INDEPENDENT_BENCHMARK,
            benchmark_identity={"not": {"type": "null"}},
            require_gold_recall=True,
        ),
    ]
    schema["x-codetalk-runtime-validation"] = {
        "authority": "pydantic",
        "public_validator": (
            "app.services.quality_evaluation_contract.validate_quality_evaluation"
        ),
        "invariants": [
            {
                "id": "numerator_lte_denominator",
                "reason": (
                    "Draft 2020-12 cannot compare sibling numerator and denominator values."
                ),
            },
            {
                "id": "snapshot_change_requires_positive_attempt_count",
                "reason": (
                    "Draft 2020-12 cannot compare nested snapshot equality with attempt_count."
                ),
            },
        ],
    }
    _add_cross_field_schema_rules(schema)
    return schema


def _add_cross_field_schema_rules(schema: dict[str, Any]) -> None:
    definitions = schema["$defs"]
    layer_schema = definitions["ValidationLayerOutcome"]
    layer_schema["allOf"] = [
        {
            "if": {
                "properties": {"critical_miss_ids": {"minItems": 1}},
                "required": ["critical_miss_ids"],
            },
            "then": {"properties": {"status": {"const": LayerStatus.FAIL.value}}},
        }
    ]

    axis_schema = definitions["AxisResult"]
    failed_layer = _validation_layers_condition(
        lambda: {
            "properties": {"status": {"const": LayerStatus.FAIL.value}},
            "required": ["status"],
        }
    )
    limited_layer = _validation_layers_condition(
        lambda: {
            "anyOf": [
                {
                    "properties": {"status": {"const": LayerStatus.NOT_RUN.value}},
                    "required": ["status"],
                },
                {
                    "properties": {"limitations": {"minItems": 1}},
                    "required": ["limitations"],
                },
            ]
        }
    )
    axis_schema["allOf"] = [
        {
            "if": {
                "properties": {"critical_misses": {"minItems": 1}},
                "required": ["critical_misses"],
            },
            "then": {"properties": {"status": {"const": AxisStatus.FAIL.value}}},
        },
        {
            "if": {
                "properties": {"validation_layers": failed_layer},
                "required": ["validation_layers"],
            },
            "then": {"properties": {"status": {"const": AxisStatus.FAIL.value}}},
        },
        {
            "if": {
                "anyOf": [
                    {
                        "properties": {"limitations": {"minItems": 1}},
                        "required": ["limitations"],
                    },
                    {
                        "properties": {"validation_layers": limited_layer},
                        "required": ["validation_layers"],
                    },
                ]
            },
            "then": {"properties": {"status": {"not": {"const": AxisStatus.PASS.value}}}},
        },
    ]

    snapshot_schema = definitions["EvaluationSnapshot"]
    snapshot_schema["allOf"] = [
        _axis_required_metrics_condition(
            axis="accuracy",
            metrics=(MetricName.CLAIM_PRECISION,),
        ),
        _axis_required_metrics_condition(
            axis="breadth",
            metrics=(
                MetricName.DISCOVERY_RECALL,
                MetricName.CRITICAL_COVERAGE,
                MetricName.SCENARIO_REALIZATION,
                MetricName.DISPOSITION_COMPLETENESS,
            ),
        ),
        _axis_required_metrics_condition(
            axis="depth",
            metrics=(
                MetricName.MINIMUM_CRITICAL_CHAIN_CLOSURE,
                MetricName.AVERAGE_CHAIN_CLOSURE,
                MetricName.STATE_CLOSURE,
                MetricName.RESOURCE_LIFECYCLE_CLOSURE,
                MetricName.ERROR_RECOVERY_CLOSURE,
                MetricName.DISCONFIRMING_CHECKS,
            ),
        ),
    ]

    failure_signal = {
        "anyOf": [
            _nonempty_array_predicate("hard_failures"),
            _terminal_block_reason_predicate(),
            _final_axis_status_predicate(AxisStatus.FAIL),
        ]
    }
    limited_signal = {
        "anyOf": [
            _nonempty_array_predicate("limitations"),
            _final_axis_status_predicate(AxisStatus.LIMITED),
        ]
    }
    schema["allOf"].extend(
        [
            _delivery_derivation_condition(
                predicate=failure_signal,
                status=DeliveryStatus.NOT_READY,
            ),
            _delivery_derivation_condition(
                predicate={
                    "allOf": [
                        {"not": failure_signal},
                        limited_signal,
                    ]
                },
                status=DeliveryStatus.LIMITED,
            ),
            _delivery_derivation_condition(
                predicate={
                    "allOf": [
                        {"not": failure_signal},
                        {"not": limited_signal},
                    ]
                },
                status=DeliveryStatus.READY,
            ),
        ]
    )


def _validation_layers_condition(layer_condition_factory: Any) -> dict[str, Any]:
    return {
        "anyOf": [
            {
                "properties": {layer: layer_condition_factory()},
                "required": [layer],
            }
            for layer in ("L0", "L1", "L2", "L3")
        ]
    }


def _axis_required_metrics_condition(
    *,
    axis: str,
    metrics: tuple[MetricName, ...],
) -> dict[str, Any]:
    return {
        "properties": {
            axis: {
                "properties": {
                    "metrics": {
                        "allOf": [
                            {
                                "contains": {
                                    "properties": {
                                        "name": {"const": metric.value}
                                    },
                                    "required": ["name"],
                                }
                            }
                            for metric in metrics
                        ]
                    }
                },
                "required": ["metrics"],
            }
        },
        "required": [axis],
    }


def _nonempty_array_predicate(field: str) -> dict[str, Any]:
    return {
        "properties": {field: {"minItems": 1}},
        "required": [field],
    }


def _terminal_block_reason_predicate() -> dict[str, Any]:
    return {
        "properties": {
            "repair_summary": {
                "properties": {"terminal_block_reason": {"type": "string"}},
                "required": ["terminal_block_reason"],
            }
        },
        "required": ["repair_summary"],
    }


def _final_axis_status_predicate(axis_status: AxisStatus) -> dict[str, Any]:
    axis_condition = {
        "anyOf": [
            {
                "properties": {
                    axis: {
                        "properties": {"status": {"const": axis_status.value}},
                        "required": ["status"],
                    }
                },
                "required": [axis],
            }
            for axis in ("accuracy", "breadth", "depth")
        ]
    }
    return {
        "properties": {"final_after_auto_repair": axis_condition},
        "required": ["final_after_auto_repair"],
    }


def _delivery_derivation_condition(
    *,
    predicate: dict[str, Any],
    status: DeliveryStatus,
) -> dict[str, Any]:
    return {
        "if": predicate,
        "then": {
            "properties": {"delivery_status": {"const": status.value}},
            "required": ["delivery_status"],
        },
    }


def _scope_schema_condition(
    *,
    scope: EvaluationScope,
    benchmark_identity: dict[str, Any],
    require_gold_recall: bool,
) -> dict[str, Any]:
    metric_match = {
        "properties": {"name": {"const": MetricName.GOLD_RECALL.value}},
        "required": ["name"],
    }
    metric_condition = (
        {"contains": metric_match}
        if require_gold_recall
        else {"not": {"contains": metric_match}}
    )
    snapshot_condition = {
        "properties": {
            "accuracy": {
                "properties": {"metrics": metric_condition},
                "required": ["metrics"],
            }
        },
        "required": ["accuracy"],
    }
    return {
        "if": {
            "properties": {"scope": {"const": scope.value}},
            "required": ["scope"],
        },
        "then": {
            "properties": {
                "benchmark_identity": benchmark_identity,
                "first_pass": snapshot_condition,
                "final_after_auto_repair": snapshot_condition,
            },
            "required": [
                "benchmark_identity",
                "first_pass",
                "final_after_auto_repair",
            ],
        },
    }
