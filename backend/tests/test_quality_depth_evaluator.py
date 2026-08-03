from __future__ import annotations

import hashlib
import http.server
import importlib
import json
import os
import shutil
import threading
import time
from copy import deepcopy
from fractions import Fraction
from itertools import pairwise
from pathlib import Path

import pytest
from pydantic import ValidationError

NODE_KINDS = (
    ("trigger", "trigger"),
    ("precondition", "precondition"),
    ("entry", "entry"),
    ("call", "call"),
    ("resource-acquire", "resource_acquisition"),
    ("resource-owner", "resource_ownership"),
    ("state-mutation", "state_mutation"),
    ("downstream-effect", "downstream_effect"),
    ("error-propagation", "error_propagation"),
    ("cleanup", "cleanup"),
    ("resource-release", "resource_release"),
    ("recovery", "recovery"),
    ("external-observation", "external_observation"),
    ("executable-oracle", "executable_oracle"),
)


def _depth():
    try:
        return importlib.import_module("app.services.quality_depth_evaluator")
    except ModuleNotFoundError:
        pytest.fail(
            "quality_depth_evaluator is not implemented; this is the expected P2C RED"
        )


def _chain(chain_id: str = "flow") -> dict[str, object]:
    nodes = [
        {
            "node_id": node_id,
            "kind": kind,
            "statement": f"The source establishes the {node_id} causal stage.",
            "critical": True,
        }
        for node_id, kind in NODE_KINDS
    ]
    edges = [
        {
            "edge_id": f"edge-{source}-{target}",
            "source_node_id": source,
            "target_node_id": target,
            "statement": f"The {source} stage leads to the {target} stage.",
            "critical": True,
        }
        for (source, _), (target, _) in pairwise(NODE_KINDS)
    ]
    return {
        "chain_id": chain_id,
        "nodes": nodes,
        "edges": edges,
        "disconfirming_checks": [
            {
                "check_id": "reject-alternative-cause",
                "statement": "The source-backed check rejects the alternative cause.",
                "critical": True,
            }
        ],
    }


def _replace_with_consecutive_edges(chain: dict[str, object]) -> None:
    chain["edges"] = [
        {
            "edge_id": f"edge-{source['node_id']}-{target['node_id']}",
            "source_node_id": source["node_id"],
            "target_node_id": target["node_id"],
            "statement": (
                f"The {source['node_id']} stage leads to the "
                f"{target['node_id']} stage."
            ),
            "critical": True,
        }
        for source, target in pairwise(chain["nodes"])
    ]


def _truth(
    *chains: dict[str, object], execution_tier: str = "S"
) -> dict[str, object]:
    truth = {
        "case_id": "depth-case-001",
        "execution_tier": execution_tier,
        "chains": list(chains or (_chain(),)),
    }
    truth["evidence_catalog_sha256"] = _catalog_digest(_catalog(truth))
    return truth


def _candidate_chain(
    truth_chain: dict[str, object],
    *,
    node_ids: set[str] | None = None,
    edge_ids: set[str] | None = None,
    check_ids: set[str] | None = None,
    narrative: str = "",
) -> dict[str, object]:
    truth_nodes = truth_chain["nodes"]
    truth_edges = truth_chain["edges"]
    truth_checks = truth_chain["disconfirming_checks"]
    selected_nodes = node_ids if node_ids is not None else {
        node["node_id"] for node in truth_nodes
    }
    selected_edges = edge_ids if edge_ids is not None else {
        edge["edge_id"] for edge in truth_edges
    }
    selected_checks = check_ids if check_ids is not None else {
        check["check_id"] for check in truth_checks
    }
    return {
        "chain_id": truth_chain["chain_id"],
        "nodes": [
            {
                "node_id": node["node_id"],
                "status": "closed",
                "evidence_refs": [
                    _evidence_ref(truth_chain["chain_id"], "node", node["node_id"])
                ],
            }
            for node in truth_nodes
            if node["node_id"] in selected_nodes
        ],
        "edges": [
            {
                "edge_id": edge["edge_id"],
                "status": "closed",
                "evidence_refs": [
                    _evidence_ref(truth_chain["chain_id"], "edge", edge["edge_id"])
                ],
            }
            for edge in truth_edges
            if edge["edge_id"] in selected_edges
        ],
        "disconfirming_checks": [
            {
                "check_id": check["check_id"],
                "status": "pass",
                "evidence_refs": [
                    _evidence_ref(
                        truth_chain["chain_id"], "check", check["check_id"]
                    )
                ],
            }
            for check in truth_checks
            if check["check_id"] in selected_checks
        ],
        "narrative": narrative,
    }


def _candidate(
    truth: dict[str, object],
    *chains: dict[str, object],
    l3: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "chains": list(
            chains
            or tuple(_candidate_chain(chain) for chain in truth["chains"])
        )
    }
    if l3 is not None:
        payload["l3"] = l3
    return payload


def _evidence_ref(chain_id: str, category: str, obligation_id: str) -> str:
    scheme = "test" if category == "check" else "source"
    return f"{scheme}://depth#{chain_id}:{category}:{obligation_id}"


def _oracle_ref(chain_id: str) -> str:
    return f"oracle://hardware-run#{chain_id}"


def _catalog(truth: dict[str, object]) -> dict[str, object]:
    bindings: list[dict[str, str]] = []
    for chain in truth["chains"]:
        for category, plural, id_field in (
            ("node", "nodes", "node_id"),
            ("edge", "edges", "edge_id"),
            ("check", "disconfirming_checks", "check_id"),
        ):
            for obligation in chain[plural]:
                obligation_id = obligation[id_field]
                bindings.append(
                    {
                        "evidence_ref": _evidence_ref(
                            chain["chain_id"], category, obligation_id
                        ),
                        "chain_id": chain["chain_id"],
                        "category": category,
                        "obligation_id": obligation_id,
                    }
                )
        bindings.append(
            {
                "evidence_ref": _oracle_ref(chain["chain_id"]),
                "chain_id": chain["chain_id"],
                "category": "l3",
                "obligation_id": "execution",
            }
        )
    return {"case_id": truth["case_id"], "bindings": bindings}


def _catalog_digest(catalog: dict[str, object]) -> str:
    canonical = {
        "case_id": catalog["case_id"],
        "bindings": sorted(
            (
                {
                    key: value
                    for key, value in binding.items()
                    if key != "evidence_group" or value != "default"
                }
                for binding in catalog["bindings"]
            ),
            key=lambda binding: (
                binding["chain_id"],
                binding["category"],
                binding["obligation_id"],
                binding.get("evidence_group", "default"),
                binding["evidence_ref"],
            ),
        ),
    }
    serialized = json.dumps(
        canonical,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _evaluate(depth, truth, candidate, catalog=None):
    catalog_payload = catalog or _catalog(truth)
    typed_catalog = (
        catalog_payload
        if isinstance(catalog_payload, depth.DepthEvidenceCatalog)
        else depth.DepthEvidenceCatalog.model_validate(catalog_payload)
    )
    return depth.evaluate_depth(truth, candidate, typed_catalog)


def _metric(result, name: str):
    return next(metric for metric in result.metrics if metric.name.value == name)


def _miss_ids(result) -> set[str]:
    return {miss.item_id for miss in result.critical_misses}


def test_complete_static_chain_passes_all_depth_metrics() -> None:
    depth = _depth()
    truth = _truth()

    result = _evaluate(depth, truth, _candidate(truth))

    assert result.status.value == "pass"
    assert result.numerator == result.denominator
    assert result.critical_misses == ()
    assert result.limitations == ()
    assert result.validation_layers.l0.status.value == "pass"
    assert result.validation_layers.l1.status.value == "pass"
    assert result.validation_layers.l2.status.value == "pass"
    assert result.validation_layers.l3.status.value == "not_applicable"
    assert {metric.name.value for metric in result.metrics} == {
        "minimum_critical_chain_closure",
        "average_chain_closure",
        "state_closure",
        "resource_lifecycle_closure",
        "error_recovery_closure",
        "disconfirming_checks",
    }
    assert all(metric.numerator == metric.denominator for metric in result.metrics)


def test_evaluate_requires_an_explicit_trusted_evidence_catalog() -> None:
    depth = _depth()
    truth = _truth()

    with pytest.raises(TypeError, match="catalog"):
        depth.evaluate_depth(truth, _candidate(truth))
    with pytest.raises(TypeError, match="typed.*catalog"):
        depth.evaluate_depth(truth, _candidate(truth), _catalog(truth))


def test_catalog_digest_is_canonical_and_owned_by_hidden_truth() -> None:
    depth = _depth()
    truth = _truth()
    catalog_payload = _catalog(truth)
    reversed_payload = deepcopy(catalog_payload)
    reversed_payload["bindings"].reverse()
    catalog = depth.DepthEvidenceCatalog.model_validate(catalog_payload)
    reversed_catalog = depth.DepthEvidenceCatalog.model_validate(reversed_payload)

    assert depth.depth_evidence_catalog_sha256(catalog) == (
        depth.depth_evidence_catalog_sha256(reversed_catalog)
    )
    assert depth.depth_evidence_catalog_sha256(catalog) == (
        truth["evidence_catalog_sha256"]
    )


def test_explicit_default_evidence_group_preserves_catalog_digest() -> None:
    depth = _depth()
    truth = _truth()
    omitted = depth.DepthEvidenceCatalog.model_validate(_catalog(truth))
    explicit_payload = deepcopy(_catalog(truth))
    for binding in explicit_payload["bindings"]:
        binding["evidence_group"] = "default"
    explicit = depth.DepthEvidenceCatalog.model_validate(explicit_payload)

    assert depth.serialize_depth_evidence_catalog(explicit) == (
        depth.serialize_depth_evidence_catalog(omitted)
    )
    assert depth.depth_evidence_catalog_sha256(explicit) == (
        depth.depth_evidence_catalog_sha256(omitted)
    )


@pytest.mark.parametrize("digest", ["a" * 63, "A" * 64, "g" * 64])
def test_truth_requires_a_canonical_catalog_sha256(digest: str) -> None:
    depth = _depth()
    truth = _truth()
    truth["evidence_catalog_sha256"] = digest

    with pytest.raises(ValidationError, match="evidence_catalog_sha256"):
        depth.DepthTruth.model_validate(truth)


@pytest.mark.parametrize("binding_category", ["node", "l3"])
def test_altered_catalog_is_rejected_against_unchanged_hidden_truth_digest(
    binding_category: str,
) -> None:
    depth = _depth()
    truth = _truth()
    altered_payload = _catalog(truth)
    binding = next(
        item
        for item in altered_payload["bindings"]
        if item["category"] == binding_category
    )
    scheme = "oracle" if binding_category == "l3" else "source"
    binding["evidence_ref"] = f"{scheme}://candidate-authored#replacement"
    altered_catalog = depth.DepthEvidenceCatalog.model_validate(altered_payload)

    with pytest.raises(ValueError, match="digest"):
        depth.evaluate_depth(truth, _candidate(truth), altered_catalog)


def test_catalog_case_identity_must_match_hidden_truth() -> None:
    depth = _depth()
    truth = _truth()
    catalog_payload = _catalog(truth)
    catalog_payload["case_id"] = "different-case"
    catalog = depth.DepthEvidenceCatalog.model_validate(catalog_payload)

    with pytest.raises(ValueError, match="case_id"):
        depth.evaluate_depth(truth, _candidate(truth), catalog)


@pytest.mark.parametrize("binding_category", ["node", "l3"])
def test_catalog_rejects_ref_reuse_across_distinct_obligations(
    binding_category: str,
) -> None:
    depth = _depth()
    truth = _truth(_chain("flow-a"), _chain("flow-b"))
    catalog_payload = _catalog(truth)
    bindings = [
        binding
        for binding in catalog_payload["bindings"]
        if binding["category"] == binding_category
    ]
    bindings[1]["evidence_ref"] = bindings[0]["evidence_ref"]

    with pytest.raises(ValidationError, match="evidence_ref.*distinct"):
        depth.DepthEvidenceCatalog.model_validate(catalog_payload)


def test_catalog_rejects_untrusted_evidence_ref_schemes() -> None:
    depth = _depth()
    truth = _truth()
    catalog_payload = _catalog(truth)
    catalog_payload["bindings"][0]["evidence_ref"] = "madeup://candidate#claim"

    with pytest.raises(ValidationError, match="scheme"):
        depth.DepthEvidenceCatalog.model_validate(catalog_payload)


@pytest.mark.parametrize(
    ("category", "obligation_id", "evidence_ref"),
    [
        ("node", "entry", "source://does-not-exist#entry"),
        ("node", "entry", _evidence_ref("flow", "node", "trigger")),
        (
            "edge",
            "edge-external-observation-executable-oracle",
            _evidence_ref("flow", "edge", "edge-trigger-precondition"),
        ),
        (
            "check",
            "reject-alternative-cause",
            _evidence_ref("flow", "node", "trigger"),
        ),
    ],
)
def test_static_evidence_must_be_trusted_for_the_exact_obligation(
    category: str,
    obligation_id: str,
    evidence_ref: str,
) -> None:
    depth = _depth()
    truth = _truth()
    candidate = _candidate(truth)
    plural = {
        "node": "nodes",
        "edge": "edges",
        "check": "disconfirming_checks",
    }[category]
    id_field = {"node": "node_id", "edge": "edge_id", "check": "check_id"}[
        category
    ]
    observation = next(
        item
        for item in candidate["chains"][0][plural]
        if item[id_field] == obligation_id
    )
    observation["evidence_refs"] = [evidence_ref]

    result = _evaluate(depth, truth, candidate)

    item_id = f"chain:flow/{category}:{obligation_id}"
    assert result.status.value == "fail"
    assert result.validation_layers.l1.status.value == "fail"
    assert result.validation_layers.l2.status.value == "fail"
    assert item_id in result.validation_layers.l1.critical_miss_ids
    assert item_id in result.validation_layers.l2.critical_miss_ids
    assert item_id in _miss_ids(result)


def test_static_obligation_requires_the_complete_catalog_evidence_set() -> None:
    depth = _depth()
    truth = _truth()
    catalog_payload = _catalog(truth)
    first = next(
        binding
        for binding in catalog_payload["bindings"]
        if binding["category"] == "node" and binding["obligation_id"] == "trigger"
    )
    second = deepcopy(first)
    second["evidence_ref"] = "source://depth#flow:node:trigger:second-range"
    catalog_payload["bindings"].append(second)
    truth["evidence_catalog_sha256"] = _catalog_digest(catalog_payload)

    result = _evaluate(depth, truth, _candidate(truth), catalog_payload)

    assert result.status.value == "fail"
    assert "chain:flow/node:trigger" in _miss_ids(result)
    assert result.validation_layers.l1.status.value == "fail"


def test_noncritical_untrusted_evidence_fails_without_critical_label() -> None:
    depth = _depth()
    chain = _chain()
    node = next(item for item in chain["nodes"] if item["node_id"] == "state-mutation")
    node["critical"] = False
    truth = _truth(chain)
    candidate = _candidate(truth)
    observed = next(
        item
        for item in candidate["chains"][0]["nodes"]
        if item["node_id"] == "state-mutation"
    )
    observed["evidence_refs"] = ["source://untrusted#L1-L1"]

    result = _evaluate(depth, truth, candidate)

    item_id = "chain:flow/node:state-mutation"
    assert result.status.value == "fail"
    assert result.validation_layers.l1.status.value == "fail"
    assert item_id not in result.validation_layers.l1.critical_miss_ids
    assert item_id not in result.validation_layers.l2.critical_miss_ids
    assert item_id not in _miss_ids(result)


def test_static_obligation_accepts_either_complete_evidence_group() -> None:
    depth = _depth()
    truth = _truth()
    catalog_payload = _catalog(truth)
    primary = next(
        binding
        for binding in catalog_payload["bindings"]
        if binding["category"] == "node" and binding["obligation_id"] == "trigger"
    )
    alternate = deepcopy(primary)
    alternate["evidence_ref"] = "source://depth#flow:node:trigger:alternate"
    alternate["evidence_group"] = "alternate-trigger"
    catalog_payload["bindings"].append(alternate)
    truth["evidence_catalog_sha256"] = _catalog_digest(catalog_payload)
    candidate = _candidate(truth)
    trigger = candidate["chains"][0]["nodes"][0]
    trigger["evidence_refs"] = [alternate["evidence_ref"]]

    result = _evaluate(depth, truth, candidate, catalog_payload)

    assert result.status.value == "pass"
    assert "chain:flow/node:trigger" not in _miss_ids(result)


def test_static_obligation_rejects_refs_mixed_across_evidence_groups() -> None:
    depth = _depth()
    truth = _truth()
    catalog_payload = _catalog(truth)
    primary = next(
        binding
        for binding in catalog_payload["bindings"]
        if binding["category"] == "node" and binding["obligation_id"] == "trigger"
    )
    primary_second = deepcopy(primary)
    primary_second["evidence_ref"] = "source://depth#flow:node:trigger:primary-2"
    alternate_first = deepcopy(primary)
    alternate_first["evidence_ref"] = "source://depth#flow:node:trigger:alternate-1"
    alternate_first["evidence_group"] = "alternate-trigger"
    alternate_second = deepcopy(alternate_first)
    alternate_second["evidence_ref"] = "source://depth#flow:node:trigger:alternate-2"
    catalog_payload["bindings"].extend(
        [primary_second, alternate_first, alternate_second]
    )
    truth["evidence_catalog_sha256"] = _catalog_digest(catalog_payload)
    candidate = _candidate(truth)
    trigger = candidate["chains"][0]["nodes"][0]
    trigger["evidence_refs"] = [
        primary["evidence_ref"],
        alternate_second["evidence_ref"],
    ]

    result = _evaluate(depth, truth, candidate, catalog_payload)

    assert result.status.value == "fail"
    assert "chain:flow/node:trigger" in _miss_ids(result)


def test_chain_stopping_before_mutation_fails_with_addressable_open_obligations() -> None:
    depth = _depth()
    chain = _chain()
    truth = _truth(chain)
    prefix_nodes = {node_id for node_id, _ in NODE_KINDS[:6]}
    prefix_edges = {
        edge["edge_id"]
        for edge in chain["edges"]
        if edge["source_node_id"] in prefix_nodes
        and edge["target_node_id"] in prefix_nodes
    }
    candidate_chain = _candidate_chain(
        chain,
        node_ids=prefix_nodes,
        edge_ids=prefix_edges,
    )

    result = _evaluate(depth, truth, _candidate(truth, candidate_chain))

    assert result.status.value == "fail"
    assert "chain:flow/node:state-mutation" in _miss_ids(result)
    assert "chain:flow/node:downstream-effect" in _miss_ids(result)
    assert "chain:flow/edge:edge-resource-owner-state-mutation" in _miss_ids(
        result
    )
    minimum = _metric(result, "minimum_critical_chain_closure")
    assert minimum.numerator < minimum.denominator
    assert set(minimum.miss_ids).issubset(_miss_ids(result))


def test_success_only_chain_cannot_hide_missing_error_cleanup_and_recovery() -> None:
    depth = _depth()
    chain = _chain()
    truth = _truth(chain)
    omitted = {"error-propagation", "cleanup", "recovery"}
    node_ids = {node_id for node_id, _ in NODE_KINDS} - omitted
    edge_ids = {
        edge["edge_id"]
        for edge in chain["edges"]
        if edge["source_node_id"] in node_ids
        and edge["target_node_id"] in node_ids
    }

    result = _evaluate(
        depth,
        truth,
        _candidate(
            truth,
            _candidate_chain(chain, node_ids=node_ids, edge_ids=edge_ids),
        ),
    )

    assert result.status.value == "fail"
    recovery = _metric(result, "error_recovery_closure")
    assert recovery.numerator < recovery.denominator
    assert {
        "chain:flow/node:error-propagation",
        "chain:flow/node:cleanup",
        "chain:flow/node:recovery",
    }.issubset(set(recovery.miss_ids))


def test_resource_acquisition_without_ownership_and_release_fails() -> None:
    depth = _depth()
    chain = _chain()
    truth = _truth(chain)
    node_ids = {
        node_id
        for node_id, _ in NODE_KINDS
        if node_id not in {"resource-owner", "resource-release"}
    }
    edge_ids = {
        edge["edge_id"]
        for edge in chain["edges"]
        if edge["source_node_id"] in node_ids
        and edge["target_node_id"] in node_ids
    }

    result = _evaluate(
        depth,
        truth,
        _candidate(
            truth,
            _candidate_chain(chain, node_ids=node_ids, edge_ids=edge_ids),
        ),
    )

    lifecycle = _metric(result, "resource_lifecycle_closure")
    assert result.status.value == "fail"
    assert lifecycle.numerator < lifecycle.denominator
    assert {
        "chain:flow/node:resource-owner",
        "chain:flow/node:resource-release",
    }.issubset(set(lifecycle.miss_ids))


def test_disconnected_oracle_fails_even_when_oracle_node_exists() -> None:
    depth = _depth()
    chain = _chain()
    truth = _truth(chain)
    oracle_edge_id = "edge-external-observation-executable-oracle"
    edge_ids = {
        edge["edge_id"]
        for edge in chain["edges"]
        if edge["edge_id"] != oracle_edge_id
    }

    result = _evaluate(
        depth,
        truth,
        _candidate(truth, _candidate_chain(chain, edge_ids=edge_ids)),
    )

    expected = f"chain:flow/edge:{oracle_edge_id}"
    assert result.status.value == "fail"
    assert expected in _miss_ids(result)
    assert expected in _metric(result, "minimum_critical_chain_closure").miss_ids


def test_long_narrative_does_not_replace_a_disconfirming_check() -> None:
    depth = _depth()
    chain = _chain()
    truth = _truth(chain)
    candidate_chain = _candidate_chain(
        chain,
        check_ids=set(),
        narrative="long causal analysis " * 5_000,
    )

    result = _evaluate(depth, truth, _candidate(truth, candidate_chain))

    check_id = "chain:flow/check:reject-alternative-cause"
    checks = _metric(result, "disconfirming_checks")
    assert result.status.value == "fail"
    assert (checks.numerator, checks.denominator) == (0, 1)
    assert checks.miss_ids == (check_id,)
    assert check_id in _miss_ids(result)


@pytest.mark.parametrize(
    ("category", "obligation_id"),
    [
        ("node", "state-mutation"),
        ("edge", "edge-external-observation-executable-oracle"),
        ("check", "reject-alternative-cause"),
    ],
)
def test_every_truth_obligation_gates_even_when_marked_noncritical(
    category: str,
    obligation_id: str,
) -> None:
    depth = _depth()
    chain = _chain()
    plural = {
        "node": "nodes",
        "edge": "edges",
        "check": "disconfirming_checks",
    }[category]
    id_field = {"node": "node_id", "edge": "edge_id", "check": "check_id"}[
        category
    ]
    obligation = next(item for item in chain[plural] if item[id_field] == obligation_id)
    obligation["critical"] = False
    truth = _truth(chain)
    candidate_kwargs: dict[str, set[str]] = {}
    selected_ids = {
        item[id_field] for item in chain[plural] if item[id_field] != obligation_id
    }
    candidate_kwargs[
        {"node": "node_ids", "edge": "edge_ids", "check": "check_ids"}[category]
    ] = selected_ids

    result = _evaluate(
        depth,
        truth,
        _candidate(truth, _candidate_chain(chain, **candidate_kwargs)),
    )

    item_id = f"chain:flow/{category}:{obligation_id}"
    minimum = _metric(result, "minimum_critical_chain_closure")
    assert result.status.value == "fail"
    assert item_id not in _miss_ids(result)
    assert item_id not in result.validation_layers.l2.critical_miss_ids
    assert item_id in minimum.miss_ids
    assert minimum.numerator < minimum.denominator


def test_weakest_critical_chain_gates_instead_of_being_averaged_away() -> None:
    depth = _depth()
    complete_chain = _chain("complete")
    shallow_chain = _chain("shallow")
    truth = _truth(complete_chain, shallow_chain)
    shallow_nodes = {node_id for node_id, _ in NODE_KINDS[:4]}
    shallow_edges = {
        edge["edge_id"]
        for edge in shallow_chain["edges"]
        if edge["source_node_id"] in shallow_nodes
        and edge["target_node_id"] in shallow_nodes
    }

    result = _evaluate(
        depth,
        truth,
        _candidate(
            truth,
            _candidate_chain(complete_chain),
            _candidate_chain(
                shallow_chain,
                node_ids=shallow_nodes,
                edge_ids=shallow_edges,
                check_ids=set(),
            ),
        ),
    )

    minimum = _metric(result, "minimum_critical_chain_closure")
    average = _metric(result, "average_chain_closure")
    expected_closed = len(shallow_nodes) + len(shallow_edges)
    expected_total = (
        len(shallow_chain["nodes"])
        + len(shallow_chain["edges"])
        + len(shallow_chain["disconfirming_checks"])
    )
    assert result.status.value == "fail"
    assert (minimum.numerator, minimum.denominator) == (
        expected_closed,
        expected_total,
    )
    assert average.numerator * minimum.denominator > (
        minimum.numerator * average.denominator
    )
    assert any(miss_id.startswith("chain:shallow/") for miss_id in _miss_ids(result))


def test_average_chain_closure_is_an_exact_reduced_fraction() -> None:
    depth = _depth()
    chain_a = _chain("flow-a")
    chain_b = _chain("flow-b")
    chain_b["disconfirming_checks"].append(
        {
            "check_id": "reject-second-cause",
            "statement": "The source-backed check rejects the second cause.",
            "critical": True,
        }
    )
    truth = _truth(chain_a, chain_b)
    candidate_a = _candidate_chain(chain_a, check_ids=set())
    candidate_b = _candidate_chain(chain_b, check_ids=set())

    result = _evaluate(
        depth,
        truth,
        _candidate(truth, candidate_a, candidate_b),
    )

    total_a = len(chain_a["nodes"]) + len(chain_a["edges"]) + 1
    total_b = len(chain_b["nodes"]) + len(chain_b["edges"]) + 2
    expected = (Fraction(total_a - 1, total_a) + Fraction(total_b - 2, total_b)) / 2
    average = _metric(result, "average_chain_closure")
    assert (average.numerator, average.denominator) == (
        expected.numerator,
        expected.denominator,
    )


def test_missing_tier_h_is_l3_not_run_and_limits_an_otherwise_closed_axis() -> None:
    depth = _depth()
    truth = _truth(execution_tier="H")

    result = _evaluate(depth, truth, _candidate(truth))

    assert result.status.value == "limited"
    assert result.numerator < result.denominator
    assert result.critical_misses == ()
    assert result.limitations == ("L3_NOT_RUN:TIER_H",)
    assert result.validation_layers.l3.status.value == "not_run"
    assert result.validation_layers.l3.numerator == 0
    assert result.validation_layers.l3.denominator == 1
    assert result.validation_layers.l3.limitations == ("L3_NOT_RUN:TIER_H",)


def test_environment_limitation_does_not_waive_a_static_oracle_obligation() -> None:
    depth = _depth()
    chain = _chain()
    truth = _truth(chain, execution_tier="H")
    node_ids = {
        node_id for node_id, _ in NODE_KINDS if node_id != "executable-oracle"
    }
    edge_ids = {
        edge["edge_id"]
        for edge in chain["edges"]
        if edge["source_node_id"] in node_ids
        and edge["target_node_id"] in node_ids
    }

    result = _evaluate(
        depth,
        truth,
        _candidate(
            truth,
            _candidate_chain(chain, node_ids=node_ids, edge_ids=edge_ids),
        ),
    )

    assert result.status.value == "fail"
    assert "chain:flow/node:executable-oracle" in _miss_ids(result)
    assert result.validation_layers.l3.status.value == "not_run"
    assert result.limitations == ("L3_NOT_RUN:TIER_H",)


def test_tier_h_pass_requires_explicit_evidence_for_every_critical_chain() -> None:
    depth = _depth()
    truth = _truth(_chain("flow-a"), _chain("flow-b"), execution_tier="H")
    shared_l3 = {
        "status": "pass",
        "chain_evidence": [
            {"chain_id": "flow-a", "evidence_refs": ["oracle://global-run"]},
            {"chain_id": "flow-b", "evidence_refs": ["oracle://global-run"]},
        ],
        "limitations": [],
    }

    with pytest.raises(ValidationError, match="distinct"):
        depth.DepthCandidate.model_validate(_candidate(truth, l3=shared_l3))

    wrong_binding_l3 = {
        "status": "pass",
        "chain_evidence": [
            {"chain_id": "flow-a", "evidence_refs": [_oracle_ref("flow-b")]},
            {"chain_id": "flow-b", "evidence_refs": [_oracle_ref("flow-a")]},
        ],
        "limitations": [],
    }
    unsupported = _evaluate(depth, truth, _candidate(truth, l3=wrong_binding_l3))

    assert unsupported.status.value == "fail"
    assert unsupported.validation_layers.l3.status.value == "fail"
    assert {
        "chain:flow-a/l3:execution",
        "chain:flow-b/l3:execution",
    }.issubset(_miss_ids(unsupported))

    supported_l3 = {
        "status": "pass",
        "chain_evidence": [
            {"chain_id": "flow-a", "evidence_refs": [_oracle_ref("flow-a")]},
            {"chain_id": "flow-b", "evidence_refs": [_oracle_ref("flow-b")]},
        ],
        "limitations": [],
    }
    supported = _evaluate(depth, truth, _candidate(truth, l3=supported_l3))

    assert supported.status.value == "pass"
    assert supported.validation_layers.l3.status.value == "pass"
    assert supported.validation_layers.l3.numerator == 2
    assert supported.validation_layers.l3.denominator == 2


def test_l3_pass_requires_the_complete_catalog_evidence_set_per_chain() -> None:
    depth = _depth()
    truth = _truth(execution_tier="E")
    catalog_payload = _catalog(truth)
    first = next(
        binding
        for binding in catalog_payload["bindings"]
        if binding["category"] == "l3"
    )
    second = deepcopy(first)
    second["evidence_ref"] = "oracle://second-run#flow"
    catalog_payload["bindings"].append(second)
    truth["evidence_catalog_sha256"] = _catalog_digest(catalog_payload)
    candidate = _candidate(
        truth,
        l3={
            "status": "pass",
            "chain_evidence": [
                {"chain_id": "flow", "evidence_refs": [first["evidence_ref"]]}
            ],
            "limitations": [],
        },
    )

    result = _evaluate(depth, truth, candidate, catalog_payload)

    assert result.status.value == "fail"
    assert result.validation_layers.l3.status.value == "fail"
    assert "chain:flow/l3:execution" in _miss_ids(result)


def test_l3_status_and_limitations_are_consistent_and_propagated() -> None:
    depth = _depth()
    truth = _truth(execution_tier="H")
    bound_pass = {
        "status": "pass",
        "chain_evidence": [
            {"chain_id": "flow", "evidence_refs": [_oracle_ref("flow")]}
        ],
        "limitations": ["LAB_UNAVAILABLE"],
    }
    with pytest.raises(ValidationError, match="pass.*limitations"):
        depth.DepthCandidate.model_validate(_candidate(truth, l3=bound_pass))

    not_run_without_reason = {
        "status": "not_run",
        "chain_evidence": [],
        "limitations": [],
    }
    with pytest.raises(ValidationError, match="not_run.*limitation"):
        depth.DepthCandidate.model_validate(
            _candidate(truth, l3=not_run_without_reason)
        )

    not_applicable_with_reason = {
        "status": "not_applicable",
        "chain_evidence": [],
        "limitations": ["UNUSED_LIMITATION"],
    }
    with pytest.raises(ValidationError, match="not_applicable.*limitations"):
        depth.DepthCandidate.model_validate(
            _candidate(_truth(), l3=not_applicable_with_reason)
        )

    explicit_not_run = deepcopy(not_run_without_reason)
    explicit_not_run["limitations"] = ["LAB_UNAVAILABLE"]
    result = _evaluate(depth, truth, _candidate(truth, l3=explicit_not_run))

    assert result.status.value == "limited"
    assert result.validation_layers.l3.status.value == "not_run"
    assert result.limitations == ("L3_NOT_RUN:TIER_H", "LAB_UNAVAILABLE")


@pytest.mark.skipif(os.name == "nt", reason="oracle sandbox fixtures require POSIX")
def test_evaluator_owned_allowlisted_oracle_runs_twice_with_immutable_hashes(
    tmp_path: Path,
) -> None:
    depth = _depth()
    source = tmp_path / "source"
    source.mkdir()
    fixture = source / "oracle-result.json"
    fixture_bytes = b'{"classification":"pass","observed":"safe"}\n'
    fixture.write_bytes(fixture_bytes)
    artifact_one = tmp_path / "run-one"
    artifact_two = tmp_path / "run-two"
    cat = shutil.which("cat")
    assert cat is not None
    contract = depth.DepthOracleCommandContract(
        command_id="fixture-cat-v1",
        argv=(cat, "{fixture}"),
    )
    result_sha256 = hashlib.sha256(fixture_bytes).hexdigest()
    evidence_ref = f"oracle://fixture-cat-v1#sha256={result_sha256}"
    plan = depth.DepthExecutionPlan.model_validate(
        {
            "schema_version": "quality-depth-execution-v1",
            "case_id": "depth-case-001",
            "execution_tier": "E",
            "policy": "allowlisted",
            "oracles": [
                {
                    "oracle_id": "safe-fixture",
                    "chain_id": "flow",
                    "command_id": contract.command_id,
                    "command_sha256": depth.depth_oracle_command_sha256(contract),
                    "fixture_path": "oracle-result.json",
                    "fixture_sha256": result_sha256,
                    "expected_result_sha256": result_sha256,
                    "evidence_ref": evidence_ref,
                    "timeout_seconds": 5,
                    "requirements": [],
                }
            ],
            "limitations": [],
        }
    )
    catalog = depth.DepthEvidenceCatalog.model_validate(
        {
            "case_id": "depth-case-001",
            "bindings": [
                {
                    "evidence_ref": evidence_ref,
                    "chain_id": "flow",
                    "category": "l3",
                    "obligation_id": "execution",
                }
            ],
        }
    )

    first = depth.execute_depth_execution_oracles(
        plan,
        catalog,
        source_dir=source,
        artifact_dir=artifact_one,
        deadline_monotonic=time.monotonic() + 10,
        command_allowlist={contract.command_id: contract},
    )
    second = depth.execute_depth_execution_oracles(
        plan,
        catalog,
        source_dir=source,
        artifact_dir=artifact_two,
        deadline_monotonic=time.monotonic() + 10,
        command_allowlist={contract.command_id: contract},
    )

    assert first.evidence.status.value == "pass"
    assert first.evidence == second.evidence
    assert first.audit["network"] == "disabled"
    assert first.audit["sandbox"]["status"] == "active"
    assert first.audit["runs"][0]["command_sha256"] == plan.oracles[0].command_sha256
    assert first.audit["runs"][0]["fixture_sha256"] == result_sha256
    assert first.audit["runs"][0]["result_sha256"] == result_sha256
    assert first.audit["runs"] == second.audit["runs"]


@pytest.mark.parametrize("tier", ["E", "H"])
def test_unavailable_execution_environment_is_explicit_and_never_full_pass(
    tmp_path: Path,
    tier: str,
) -> None:
    depth = _depth()
    source = tmp_path / "source"
    source.mkdir()
    plan = depth.DepthExecutionPlan.model_validate(
        {
            "schema_version": "quality-depth-execution-v1",
            "case_id": "depth-case-001",
            "execution_tier": tier,
            "policy": "unavailable",
            "oracles": [],
            "limitations": [f"ENVIRONMENT_UNAVAILABLE:TIER_{tier}"],
        }
    )
    catalog = depth.DepthEvidenceCatalog.model_validate(
        {
            "case_id": "depth-case-001",
            "bindings": [
                {
                    "evidence_ref": "oracle://unavailable#not-run",
                    "chain_id": "flow",
                    "category": "l3",
                    "obligation_id": "execution",
                }
            ],
        }
    )

    result = depth.execute_depth_execution_oracles(
        plan,
        catalog,
        source_dir=source,
        artifact_dir=tmp_path / "artifacts",
        deadline_monotonic=time.monotonic() + 10,
        command_allowlist={},
    )

    assert result.evidence.status.value == "not_run"
    assert f"L3_NOT_RUN:TIER_{tier}" in result.evidence.limitations
    assert f"ENVIRONMENT_UNAVAILABLE:TIER_{tier}" in result.evidence.limitations
    assert result.evidence.chain_evidence == ()


def test_unallowlisted_oracle_command_fails_closed_without_execution(
    tmp_path: Path,
) -> None:
    depth = _depth()
    source = tmp_path / "source"
    source.mkdir()
    fixture = source / "fixture.json"
    fixture.write_text("{}\n", encoding="utf-8")
    digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
    evidence_ref = f"oracle://unknown#sha256={digest}"
    plan = depth.DepthExecutionPlan.model_validate(
        {
            "schema_version": "quality-depth-execution-v1",
            "case_id": "depth-case-001",
            "execution_tier": "E",
            "policy": "allowlisted",
            "oracles": [
                {
                    "oracle_id": "unknown",
                    "chain_id": "flow",
                    "command_id": "not-in-contract",
                    "command_sha256": "0" * 64,
                    "fixture_path": "fixture.json",
                    "fixture_sha256": digest,
                    "expected_result_sha256": digest,
                    "evidence_ref": evidence_ref,
                    "timeout_seconds": 1,
                    "requirements": [],
                }
            ],
            "limitations": [],
        }
    )
    catalog = depth.DepthEvidenceCatalog.model_validate(
        {
            "case_id": "depth-case-001",
            "bindings": [
                {
                    "evidence_ref": evidence_ref,
                    "chain_id": "flow",
                    "category": "l3",
                    "obligation_id": "execution",
                }
            ],
        }
    )

    result = depth.execute_depth_execution_oracles(
        plan,
        catalog,
        source_dir=source,
        artifact_dir=tmp_path / "artifacts",
        deadline_monotonic=time.monotonic() + 10,
        command_allowlist={},
    )

    assert result.evidence.status.value == "not_run"
    assert "L3_NOT_RUN:COMMAND_NOT_ALLOWLISTED" in result.evidence.limitations
    assert not (tmp_path / "artifacts").exists()


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("curl") is None,
    reason="dynamic network denial requires POSIX curl",
)
def test_allowlisted_oracle_still_has_network_denied_by_os_sandbox(
    tmp_path: Path,
) -> None:
    depth = _depth()
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"network-reachable\n")

        def log_message(self, _format: str, *args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source = tmp_path / "source"
        source.mkdir()
        fixture = source / "curl.conf"
        fixture.write_text(
            f'url = "http://127.0.0.1:{server.server_port}/oracle"\nsilent\n',
            encoding="utf-8",
        )
        fixture_sha = hashlib.sha256(fixture.read_bytes()).hexdigest()
        expected_sha = hashlib.sha256(b"network-reachable\n").hexdigest()
        evidence_ref = f"oracle://network-probe#sha256={expected_sha}"
        contract = depth.DepthOracleCommandContract(
            command_id="network-probe-v1",
            argv=(shutil.which("curl"), "--config", "{fixture}"),
        )
        plan = depth.DepthExecutionPlan.model_validate(
            {
                "schema_version": "quality-depth-execution-v1",
                "case_id": "depth-case-001",
                "execution_tier": "E",
                "policy": "allowlisted",
                "oracles": [
                    {
                        "oracle_id": "network-probe",
                        "chain_id": "flow",
                        "command_id": contract.command_id,
                        "command_sha256": depth.depth_oracle_command_sha256(contract),
                        "fixture_path": fixture.name,
                        "fixture_sha256": fixture_sha,
                        "expected_result_sha256": expected_sha,
                        "evidence_ref": evidence_ref,
                        "timeout_seconds": 3,
                        "requirements": [],
                    }
                ],
                "limitations": [],
            }
        )
        catalog = depth.DepthEvidenceCatalog.model_validate(
            {
                "case_id": "depth-case-001",
                "bindings": [
                    {
                        "evidence_ref": evidence_ref,
                        "chain_id": "flow",
                        "category": "l3",
                        "obligation_id": "execution",
                    }
                ],
            }
        )

        result = depth.execute_depth_execution_oracles(
            plan,
            catalog,
            source_dir=source,
            artifact_dir=tmp_path / "network-artifacts",
            deadline_monotonic=time.monotonic() + 5,
            command_allowlist={contract.command_id: contract},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.evidence.status.value == "fail"
    assert result.audit["network"] == "disabled"
    assert result.audit["runs"][0]["status"] == "failed"
    assert requests == []


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("tail") is None,
    reason="deadline fixture requires POSIX tail",
)
def test_allowlisted_oracle_shares_absolute_deadline_and_leaves_no_process(
    tmp_path: Path,
) -> None:
    depth = _depth()
    source = tmp_path / "source"
    source.mkdir()
    fixture = source / "stream.log"
    fixture.write_text("waiting\n", encoding="utf-8")
    fixture_sha = hashlib.sha256(fixture.read_bytes()).hexdigest()
    expected_sha = hashlib.sha256(b"never-completes\n").hexdigest()
    evidence_ref = f"oracle://deadline#sha256={expected_sha}"
    contract = depth.DepthOracleCommandContract(
        command_id="deadline-tail-v1",
        argv=(shutil.which("tail"), "-f", "{fixture}"),
    )
    plan = depth.DepthExecutionPlan.model_validate(
        {
            "schema_version": "quality-depth-execution-v1",
            "case_id": "depth-case-001",
            "execution_tier": "E",
            "policy": "allowlisted",
            "oracles": [
                {
                    "oracle_id": "deadline",
                    "chain_id": "flow",
                    "command_id": contract.command_id,
                    "command_sha256": depth.depth_oracle_command_sha256(contract),
                    "fixture_path": fixture.name,
                    "fixture_sha256": fixture_sha,
                    "expected_result_sha256": expected_sha,
                    "evidence_ref": evidence_ref,
                    "timeout_seconds": 5,
                    "requirements": [],
                }
            ],
            "limitations": [],
        }
    )
    catalog = depth.DepthEvidenceCatalog.model_validate(
        {
            "case_id": "depth-case-001",
            "bindings": [
                {
                    "evidence_ref": evidence_ref,
                    "chain_id": "flow",
                    "category": "l3",
                    "obligation_id": "execution",
                }
            ],
        }
    )
    started = time.monotonic()

    result = depth.execute_depth_execution_oracles(
        plan,
        catalog,
        source_dir=source,
        artifact_dir=tmp_path / "deadline-artifacts",
        deadline_monotonic=time.monotonic() + 0.2,
        command_allowlist={contract.command_id: contract},
    )

    assert time.monotonic() - started < 1.5
    assert result.evidence.status.value == "not_run"
    assert "L3_NOT_RUN:DEADLINE_EXCEEDED" in result.evidence.limitations
    assert result.audit["runs"][0]["status"] == "timed_out"


def test_typed_and_json_dict_adapters_are_equivalent() -> None:
    depth = _depth()
    truth_payload = _truth()
    candidate_payload = _candidate(truth_payload)

    typed_truth = depth.DepthTruth.model_validate(truth_payload)
    typed_candidate = depth.DepthCandidate.model_validate(candidate_payload)

    typed_catalog = depth.DepthEvidenceCatalog.model_validate(_catalog(truth_payload))

    assert depth.evaluate_depth(
        typed_truth, typed_candidate, typed_catalog
    ) == depth.evaluate_depth(
        truth_payload, candidate_payload, typed_catalog
    )


def test_corpus_stage_node_kinds_are_supported_by_typed_adapter() -> None:
    depth = _depth()
    chain = _chain()
    replacements = {
        "precondition": "precondition_input",
        "call": "call_chain",
        "state-mutation": "state_resource_mutation",
        "cleanup": "cleanup_recovery",
    }
    for node in chain["nodes"]:
        if node["node_id"] in replacements:
            node["kind"] = replacements[node["node_id"]]
    truth = _truth(chain)

    result = _evaluate(depth, truth, _candidate(truth))

    assert result.status.value == "pass"
    assert _metric(result, "state_closure").denominator > 1
    assert _metric(result, "resource_lifecycle_closure").denominator > 1
    assert _metric(result, "error_recovery_closure").denominator > 1


@pytest.mark.parametrize(
    ("node_ids", "replacement_kind", "missing_kind_id"),
    [
        (("trigger",), "call", "trigger"),
        (("precondition",), "call", "precondition_or_input"),
        (("entry",), "call", "entry"),
        (("call",), "entry", "call_or_call_chain"),
        (("state-mutation",), "call", "state_or_resource_mutation"),
        (("downstream-effect",), "call", "downstream_effect"),
        (("error-propagation",), "call", "error_propagation"),
        (("cleanup", "recovery"), "call", "cleanup_or_recovery"),
        (("external-observation",), "call", "external_observation"),
        (("executable-oracle",), "call", "executable_oracle"),
        (("resource-acquire",), "call", "resource_acquisition"),
        (("resource-owner",), "call", "resource_ownership"),
        (("resource-release",), "call", "resource_release"),
    ],
)
def test_truth_requires_every_ac4_chain_stage_and_resource_lifecycle_kind(
    node_ids: tuple[str, ...],
    replacement_kind: str,
    missing_kind_id: str,
) -> None:
    depth = _depth()
    truth = _truth()
    for node in truth["chains"][0]["nodes"]:
        if node["node_id"] in node_ids:
            node["kind"] = replacement_kind

    with pytest.raises(ValidationError, match=missing_kind_id):
        depth.DepthTruth.model_validate(truth)


def test_truth_requires_at_least_one_disconfirming_check() -> None:
    depth = _depth()
    truth = _truth()
    truth["chains"][0]["disconfirming_checks"] = []

    with pytest.raises(ValidationError, match="disconfirming_check"):
        depth.DepthTruth.model_validate(truth)


@pytest.mark.parametrize(
    ("collection", "index"),
    [
        ("nodes", 0),
        ("edges", 0),
        ("disconfirming_checks", 0),
    ],
)
def test_truth_requires_independently_judgeable_statement_for_every_obligation(
    collection: str,
    index: int,
) -> None:
    depth = _depth()
    truth = _truth()
    del truth["chains"][0][collection][index]["statement"]

    with pytest.raises(ValidationError, match="statement"):
        depth.DepthTruth.model_validate(truth)


def test_truth_rejects_semantically_out_of_order_stages() -> None:
    depth = _depth()
    truth = _truth()
    nodes = truth["chains"][0]["nodes"]
    nodes[0]["kind"], nodes[1]["kind"] = nodes[1]["kind"], nodes[0]["kind"]

    with pytest.raises(ValidationError, match="stage order"):
        depth.DepthTruth.model_validate(truth)


def test_truth_requires_trigger_as_the_first_node_with_valid_catalog_digest() -> None:
    depth = _depth()
    chain = _chain()
    acquisition = next(
        node for node in chain["nodes"] if node["kind"] == "resource_acquisition"
    )
    chain["nodes"].remove(acquisition)
    chain["nodes"].insert(0, acquisition)
    _replace_with_consecutive_edges(chain)
    truth = _truth(chain)
    catalog = depth.DepthEvidenceCatalog.model_validate(_catalog(truth))
    assert depth.depth_evidence_catalog_sha256(catalog) == (
        truth["evidence_catalog_sha256"]
    )

    with pytest.raises(ValidationError, match="first node.*trigger"):
        depth.evaluate_depth(truth, _candidate(truth), catalog)


def test_truth_requires_oracle_as_the_last_node_with_valid_catalog_digest() -> None:
    depth = _depth()
    chain = _chain()
    cleanup = next(node for node in chain["nodes"] if node["kind"] == "cleanup")
    chain["nodes"].remove(cleanup)
    chain["nodes"].append(cleanup)
    _replace_with_consecutive_edges(chain)
    truth = _truth(chain)
    catalog = depth.DepthEvidenceCatalog.model_validate(_catalog(truth))
    assert depth.depth_evidence_catalog_sha256(catalog) == (
        truth["evidence_catalog_sha256"]
    )

    with pytest.raises(ValidationError, match="last node.*executable_oracle"):
        depth.evaluate_depth(truth, _candidate(truth), catalog)


@pytest.mark.parametrize(
    ("kind", "node_id"),
    [
        ("trigger", "duplicate-trigger"),
        ("executable_oracle", "duplicate-oracle"),
    ],
)
def test_truth_requires_exactly_one_trigger_and_oracle(
    kind: str,
    node_id: str,
) -> None:
    depth = _depth()
    chain = _chain()
    duplicate = {
        "node_id": node_id,
        "kind": kind,
        "statement": f"The source establishes the {node_id} causal stage.",
        "critical": True,
    }
    if kind == "trigger":
        chain["nodes"].insert(1, duplicate)
    else:
        chain["nodes"].insert(len(chain["nodes"]) - 1, duplicate)
    _replace_with_consecutive_edges(chain)
    truth = _truth(chain)
    catalog = depth.DepthEvidenceCatalog.model_validate(_catalog(truth))

    with pytest.raises(ValidationError, match=f"exactly one {kind}"):
        depth.evaluate_depth(truth, _candidate(truth), catalog)


def test_truth_rejects_out_of_order_resource_lifecycle() -> None:
    depth = _depth()
    truth = _truth()
    nodes = truth["chains"][0]["nodes"]
    acquire = next(node for node in nodes if node["node_id"] == "resource-acquire")
    release = next(node for node in nodes if node["node_id"] == "resource-release")
    acquire["kind"], release["kind"] = release["kind"], acquire["kind"]

    with pytest.raises(ValidationError, match="resource lifecycle order"):
        depth.DepthTruth.model_validate(truth)


def test_truth_rejects_backward_edges_in_an_ordered_chain() -> None:
    depth = _depth()
    truth = _truth()
    edge = truth["chains"][0]["edges"][0]
    edge["source_node_id"], edge["target_node_id"] = (
        edge["target_node_id"],
        edge["source_node_id"],
    )

    with pytest.raises(ValidationError, match="forward"):
        depth.DepthTruth.model_validate(truth)


def test_truth_rejects_a_disconnected_required_node() -> None:
    depth = _depth()
    truth = _truth()
    chain = truth["chains"][0]
    chain["edges"] = [
        edge
        for edge in chain["edges"]
        if edge["edge_id"]
        not in {"edge-trigger-precondition", "edge-precondition-entry"}
    ]
    chain["edges"].append(
        {
            "edge_id": "edge-trigger-entry",
            "source_node_id": "trigger",
            "target_node_id": "entry",
            "statement": "The trigger stage leads to the entry stage.",
            "critical": True,
        }
    )

    with pytest.raises(ValidationError, match="disconnected"):
        depth.DepthTruth.model_validate(truth)


def test_truth_rejects_shortcut_graph_without_each_consecutive_edge() -> None:
    depth = _depth()
    truth = _truth()
    chain = truth["chains"][0]
    chain["edges"] = [
        edge
        for edge in chain["edges"]
        if edge["edge_id"] != "edge-precondition-entry"
    ]
    chain["edges"].extend(
        [
            {
                "edge_id": "edge-trigger-entry-shortcut",
                "source_node_id": "trigger",
                "target_node_id": "entry",
                "statement": "The trigger stage leads to the entry stage.",
                "critical": True,
            },
            {
                "edge_id": "edge-precondition-oracle-shortcut",
                "source_node_id": "precondition",
                "target_node_id": "executable-oracle",
                "statement": (
                    "The precondition stage leads directly to the executable oracle."
                ),
                "critical": True,
            },
        ]
    )

    with pytest.raises(
        ValidationError,
        match="consecutive edge.*precondition.*entry",
    ):
        depth.DepthTruth.model_validate(truth)


def test_duplicate_candidate_obligations_cannot_inflate_closure() -> None:
    depth = _depth()
    truth = _truth()
    candidate = _candidate(truth)
    candidate["chains"][0]["nodes"].append(
        deepcopy(candidate["chains"][0]["nodes"][0])
    )

    with pytest.raises(ValidationError, match="duplicate node"):
        depth.DepthCandidate.model_validate(candidate)


@pytest.mark.parametrize("unknown_kind", ["chain", "obligation"])
def test_unknown_candidate_structure_fails_closed_at_l0(unknown_kind: str) -> None:
    depth = _depth()
    truth = _truth()
    candidate = _candidate(truth)
    if unknown_kind == "chain":
        candidate["chains"].append(
            {
                "chain_id": "unknown-flow",
                "nodes": [],
                "edges": [],
                "disconfirming_checks": [],
                "narrative": "",
            }
        )
        expected_id = "candidate/chain:unknown-flow"
    else:
        candidate["chains"][0]["nodes"].append(
            {
                "node_id": "unknown-node",
                "status": "closed",
                "evidence_refs": ["source://unknown"],
            }
        )
        expected_id = "candidate/chain:flow/node:unknown-node"

    result = _evaluate(depth, truth, candidate)

    assert result.status.value == "fail"
    assert result.validation_layers.l0.status.value == "fail"
    assert expected_id in result.validation_layers.l0.critical_miss_ids
    assert expected_id in _miss_ids(result)
