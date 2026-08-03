"""Deterministic evaluator mutation replay for threshold calibration."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from itertools import pairwise
from typing import Any

from app.services.quality_accuracy_evaluator import evaluate_accuracy
from app.services.quality_breadth_evaluator import evaluate_breadth
from app.services.quality_depth_evaluator import (
    DepthEvidenceCatalog,
    depth_evidence_catalog_sha256,
    evaluate_depth,
)
from app.services.quality_evaluation_contract import (
    EVALUATOR_VERSION,
    EvaluationScope,
    MetricName,
)

SCHEMA_VERSION = "quality-calibration-mutations-v1"


def build_quality_calibration_mutation_matrix() -> dict[str, Any]:
    """Run one real fail-closed evaluator mutation for every release metric."""

    mutations = {
        "accuracy": _accuracy_mutations(),
        "breadth": _breadth_mutations(),
        "depth": _depth_mutations(),
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "mutations": mutations,
    }
    payload["matrix_sha256"] = _sha256_json(payload)
    return payload


def _accuracy_mutations() -> dict[str, Any]:
    claims = [_claim("C1", "fact-1"), _claim("C2", "fact-2")]
    gold = [_gold("G1", "fact-1"), _gold("G2", "fact-2")]
    baseline = _accuracy_result(claims, gold)

    unsupported = deepcopy(claims)
    unsupported[1]["l2_status"] = "insufficient"
    unsupported[1]["verification_status"] = "insufficient"
    precision = _accuracy_result(unsupported, gold)
    recall = _accuracy_result(claims[:1], gold)
    return {
        MetricName.CLAIM_PRECISION.value: _record(
            "accuracy-unsupported-one-emitted-claim",
            baseline,
            precision,
            MetricName.CLAIM_PRECISION,
        ),
        MetricName.GOLD_RECALL.value: _record(
            "accuracy-omit-one-applicable-gold-claim",
            baseline,
            recall,
            MetricName.GOLD_RECALL,
        ),
    }


def _claim(claim_id: str, semantic_key: str) -> dict[str, Any]:
    evidence_id = f"EV-{claim_id}"
    return {
        "claim_id": claim_id,
        "semantic_key": semantic_key,
        "critical": True,
        "l1_status": "verified",
        "l2_status": "supports",
        "verification_status": "verified",
        "evidence_refs": [
            {
                "evidence_id": evidence_id,
                "path": "calibration.c",
                "start_line": 1,
                "end_line": 1,
            }
        ],
    }


def _gold(gold_id: str, semantic_key: str) -> dict[str, Any]:
    return {
        "gold_id": gold_id,
        "semantic_key": semantic_key,
        "critical": True,
        "applicability": "applicable",
        "applicability_evidence_refs": [f"truth://{gold_id}/applicable"],
    }


def _accuracy_result(claims: list[dict[str, Any]], gold: list[dict[str, Any]]):
    cards = [
        {
            "evidence_id": claim["evidence_refs"][0]["evidence_id"],
            "path": "calibration.c",
            "start_line": 1,
            "end_line": 1,
        }
        for claim in claims
    ]
    return evaluate_accuracy(
        scope=EvaluationScope.INDEPENDENT_BENCHMARK,
        claim_ledger={
            "kind": "claim_evidence_ledger",
            "schema_version": "claim-evidence-ledger-v3",
            "claims": claims,
        },
        evidence_cards=cards,
        gold_claims=gold,
        l3_validation={
            "status": "pass",
            "numerator": 1,
            "denominator": 1,
            "critical_miss_ids": [],
            "evidence_refs": ["oracle://calibration/accuracy"],
            "limitations": [],
        },
    )


def _breadth_mutations() -> dict[str, Any]:
    dimensions = (
        "entrypoints",
        "flows",
        "branches",
        "states",
        "resources",
        "boundaries",
        "concurrency",
        "errors",
    )
    universe = {
        "schema_version": "quality-breadth-universe-v1",
        "items": [
            {
                "item_id": f"B-{index}",
                "statement": f"Required {dimension} behavior is covered.",
                "dimension": dimension,
                "critical": True,
                "applicability": "required",
                "evidence_refs": [f"truth://B-{index}"],
                "applicability_evidence_refs": [],
            }
            for index, dimension in enumerate(dimensions, start=1)
        ]
        + [
            {
                "item_id": "B-9",
                "statement": "The optional protocol surface is not applicable.",
                "dimension": "protocol",
                "critical": False,
                "applicability": "conditional",
                "evidence_refs": ["truth://B-9"],
                "applicability_evidence_refs": ["truth://B-9/not-applicable"],
            }
        ],
    }
    candidates = {
        "kind": "scenario_candidates",
        "items": [
            {
                "candidate_id": f"C-{index}",
                "coverage_item_ids": [f"B-{index}"],
                "evidence_refs": [f"truth://B-{index}"],
            }
            for index in range(1, 10)
        ],
    }
    scenarios = {
        "kind": "test_scenarios",
        "items": [
            {
                "scenario_id": f"S-{index}",
                "candidate_ids": [f"C-{index}"],
                "coverage_item_ids": [f"B-{index}"],
                "status": "ready",
                "evidence_refs": [f"truth://B-{index}"],
            }
            for index in range(1, 9)
        ],
    }
    dispositions = [
        {
            "item_id": "B-9",
            "disposition": "not_applicable",
            "evidence_refs": ["truth://B-9/not-applicable"],
        }
    ]
    baseline = evaluate_breadth(
        universe,
        scenario_candidates=candidates,
        scenarios=scenarios,
        dispositions=dispositions,
    )

    def without(container: dict[str, Any], item_id: str) -> dict[str, Any]:
        result = deepcopy(container)
        result["items"] = [
            row
            for row in result["items"]
            if item_id not in row.get("coverage_item_ids", [])
        ]
        return result

    no_candidate = evaluate_breadth(
        universe,
        scenario_candidates=without(candidates, "B-1"),
        scenarios=without(scenarios, "B-1"),
        dispositions=dispositions,
    )
    no_critical = evaluate_breadth(
        universe,
        scenario_candidates=without(candidates, "B-2"),
        scenarios=without(scenarios, "B-2"),
        dispositions=dispositions,
    )
    no_scenario = evaluate_breadth(
        universe,
        scenario_candidates=candidates,
        scenarios=without(scenarios, "B-3"),
        dispositions=dispositions,
    )
    no_disposition = evaluate_breadth(
        universe,
        scenario_candidates=candidates,
        scenarios=scenarios,
        dispositions=[],
    )
    return {
        MetricName.DISCOVERY_RECALL.value: _record(
            "breadth-remove-one-discovered-candidate",
            baseline,
            no_candidate,
            MetricName.DISCOVERY_RECALL,
        ),
        MetricName.CRITICAL_COVERAGE.value: _record(
            "breadth-remove-one-critical-obligation",
            baseline,
            no_critical,
            MetricName.CRITICAL_COVERAGE,
        ),
        MetricName.SCENARIO_REALIZATION.value: _record(
            "breadth-remove-one-realized-scenario",
            baseline,
            no_scenario,
            MetricName.SCENARIO_REALIZATION,
        ),
        MetricName.DISPOSITION_COMPLETENESS.value: _record(
            "breadth-remove-one-required-disposition",
            baseline,
            no_disposition,
            MetricName.DISPOSITION_COMPLETENESS,
        ),
    }


_DEPTH_NODES = (
    ("trigger", "trigger"),
    ("precondition", "precondition"),
    ("entry", "entry"),
    ("call", "call"),
    ("resource-acquire", "resource_acquisition"),
    ("resource-owner", "resource_ownership"),
    ("state-mutation", "state_mutation"),
    ("downstream", "downstream_effect"),
    ("error", "error_propagation"),
    ("cleanup", "cleanup"),
    ("resource-release", "resource_release"),
    ("recovery", "recovery"),
    ("observation", "external_observation"),
    ("oracle", "executable_oracle"),
)


def _depth_mutations() -> dict[str, Any]:
    chain = {
        "chain_id": "calibration-chain",
        "nodes": [
            {
                "node_id": node_id,
                "kind": kind,
                "statement": f"The chain closes {node_id}.",
                "critical": True,
            }
            for node_id, kind in _DEPTH_NODES
        ],
        "edges": [
            {
                "edge_id": f"edge-{source}-{target}",
                "source_node_id": source,
                "target_node_id": target,
                "statement": f"{source} leads to {target}.",
                "critical": True,
            }
            for (source, _), (target, _) in pairwise(_DEPTH_NODES)
        ],
        "disconfirming_checks": [
            {
                "check_id": "reject-alternative",
                "statement": "The alternative explanation is rejected.",
                "critical": True,
            }
        ],
    }
    truth: dict[str, Any] = {
        "case_id": "calibration-depth",
        "execution_tier": "S",
        "chains": [chain],
    }
    catalog_payload = _depth_catalog(truth)
    catalog = DepthEvidenceCatalog.model_validate(catalog_payload)
    truth["evidence_catalog_sha256"] = depth_evidence_catalog_sha256(catalog)
    complete = _depth_candidate(chain)
    baseline = evaluate_depth(truth, {"chains": [complete]}, catalog)

    targets = {
        MetricName.MINIMUM_CRITICAL_CHAIN_CLOSURE: ("node", "call"),
        MetricName.AVERAGE_CHAIN_CLOSURE: ("node", "entry"),
        MetricName.STATE_CLOSURE: ("node", "state-mutation"),
        MetricName.RESOURCE_LIFECYCLE_CLOSURE: ("node", "resource-acquire"),
        MetricName.ERROR_RECOVERY_CLOSURE: ("node", "error"),
        MetricName.DISCONFIRMING_CHECKS: ("check", "reject-alternative"),
    }
    result: dict[str, Any] = {}
    for metric_name, (category, obligation_id) in targets.items():
        mutated = deepcopy(complete)
        field = "nodes" if category == "node" else "disconfirming_checks"
        id_field = "node_id" if category == "node" else "check_id"
        mutated[field] = [
            row for row in mutated[field] if row[id_field] != obligation_id
        ]
        evaluated = evaluate_depth(truth, {"chains": [mutated]}, catalog)
        result[metric_name.value] = _record(
            f"depth-remove-{category}-{obligation_id}",
            baseline,
            evaluated,
            metric_name,
        )
    return result


def _depth_ref(category: str, obligation_id: str) -> str:
    scheme = "test" if category == "check" else "source"
    return f"{scheme}://calibration#{category}:{obligation_id}"


def _depth_catalog(truth: dict[str, Any]) -> dict[str, Any]:
    bindings = []
    for chain in truth["chains"]:
        for category, field, id_field in (
            ("node", "nodes", "node_id"),
            ("edge", "edges", "edge_id"),
            ("check", "disconfirming_checks", "check_id"),
        ):
            for obligation in chain[field]:
                obligation_id = obligation[id_field]
                bindings.append(
                    {
                        "evidence_ref": _depth_ref(category, obligation_id),
                        "chain_id": chain["chain_id"],
                        "category": category,
                        "obligation_id": obligation_id,
                    }
                )
        bindings.append(
            {
                "evidence_ref": "oracle://calibration#execution",
                "chain_id": chain["chain_id"],
                "category": "l3",
                "obligation_id": "execution",
            }
        )
    return {"case_id": truth["case_id"], "bindings": bindings}


def _depth_candidate(chain: dict[str, Any]) -> dict[str, Any]:
    return {
        "chain_id": chain["chain_id"],
        "nodes": [
            {
                "node_id": item["node_id"],
                "status": "closed",
                "evidence_refs": [_depth_ref("node", item["node_id"])],
            }
            for item in chain["nodes"]
        ],
        "edges": [
            {
                "edge_id": item["edge_id"],
                "status": "closed",
                "evidence_refs": [_depth_ref("edge", item["edge_id"])],
            }
            for item in chain["edges"]
        ],
        "disconfirming_checks": [
            {
                "check_id": item["check_id"],
                "status": "pass",
                "evidence_refs": [_depth_ref("check", item["check_id"])],
            }
            for item in chain["disconfirming_checks"]
        ],
    }


def _record(mutation_id: str, baseline: Any, mutated: Any, metric_name: MetricName) -> dict[str, Any]:
    return {
        "mutation_id": mutation_id,
        "baseline": _metric_result(baseline, metric_name),
        "mutated": _metric_result(mutated, metric_name),
        "mutated_axis_status": mutated.status.value,
        "expected_axis_status": "fail",
    }


def _metric_result(result: Any, metric_name: MetricName) -> dict[str, Any]:
    metric = next(item for item in result.metrics if item.name is metric_name)
    return {
        "numerator": metric.numerator,
        "denominator": metric.denominator,
        "ratio": round(metric.numerator / metric.denominator, 12),
        "miss_ids": list(metric.miss_ids),
    }


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
