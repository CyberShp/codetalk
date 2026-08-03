from __future__ import annotations

import ast
import hashlib
import importlib
import json
from pathlib import Path

import pytest

from app.services.quality_evaluation_contract import (
    AxisStatus,
    EvaluationScope,
    LayerStatus,
    MetricName,
)

MODULE_PATH = (
    Path(__file__).parents[1] / "app" / "services" / "quality_accuracy_evaluator.py"
)


def _accuracy():
    try:
        return importlib.import_module("app.services.quality_accuracy_evaluator")
    except ModuleNotFoundError:
        pytest.fail(
            "quality_accuracy_evaluator is not implemented; this is the expected P2A RED"
        )


def _card(
    evidence_id: str,
    *,
    path: str = "lib/storage.c",
    start_line: int = 10,
    end_line: int = 20,
    excerpt: str | None = None,
) -> dict[str, object]:
    card: dict[str, object] = {
        "evidence_id": evidence_id,
        "path": path,
        "start_line": start_line,
        "end_line": end_line,
    }
    if excerpt is not None:
        card["excerpt"] = excerpt
    return card


def _claim(
    claim_id: str,
    semantic_key: str,
    *,
    critical: bool = False,
    l1_status: str = "verified",
    l2_status: str = "supports",
    evidence_id: str | None = None,
    path: str = "lib/storage.c",
    start_line: int = 12,
    end_line: int = 14,
) -> dict[str, object]:
    evidence_id = evidence_id or f"EV-{claim_id}"
    return {
        "claim_id": claim_id,
        "semantic_key": semantic_key,
        "critical": critical,
        "l1_status": l1_status,
        "l2_status": l2_status,
        "verification_status": (
            "contradicted"
            if l2_status in {"contradicts", "contradicted"}
            else "verified"
            if l1_status == "verified" and l2_status in {"supports", "verified"}
            else "insufficient"
        ),
        "evidence_refs": [
            {
                "evidence_id": evidence_id,
                "path": path,
                "start_line": start_line,
                "end_line": end_line,
            }
        ],
    }


def _gold(
    gold_id: str,
    semantic_key: str,
    *,
    critical: bool = False,
    applicability: str = "applicable",
    applicability_evidence_refs: list[str] | None = None,
) -> dict[str, object]:
    return {
        "gold_id": gold_id,
        "semantic_key": semantic_key,
        "critical": critical,
        "applicability": applicability,
        "applicability_evidence_refs": (
            applicability_evidence_refs
            if applicability_evidence_refs is not None
            else [f"truth://{gold_id}/applicability"]
        ),
    }


def _l3(status: str = "pass") -> dict[str, object]:
    return {
        "status": status,
        "numerator": 1 if status == "pass" else 0,
        "denominator": 1,
        "critical_miss_ids": [],
        "evidence_refs": ["oracle://accuracy"] if status == "pass" else [],
        "limitations": ["L3_NOT_RUN"] if status == "not_run" else [],
    }


def _evaluate(
    claims: list[dict[str, object]],
    *,
    scope: EvaluationScope = EvaluationScope.INDEPENDENT_BENCHMARK,
    gold_claims: list[dict[str, object]] | None = None,
    evidence_cards: list[dict[str, object]] | None = None,
    l3_validation: dict[str, object] | None = None,
    semantic_key_resolver=None,
    critical_semantic_keys: list[str] | None = None,
    critical_claim_bindings: list[str] | None = None,
    evidence_quote_bindings: dict[str, dict[str, str]] | None = None,
):
    policy = {}
    if semantic_key_resolver is not None:
        policy["semantic_key_resolver"] = semantic_key_resolver
    if critical_semantic_keys is not None:
        policy["critical_semantic_keys"] = critical_semantic_keys
    if critical_claim_bindings is not None:
        policy["critical_claim_bindings"] = critical_claim_bindings
    if evidence_quote_bindings is not None:
        policy["evidence_quote_bindings"] = evidence_quote_bindings
    return _accuracy().evaluate_accuracy(
        scope=scope,
        claim_ledger={
            "kind": "claim_evidence_ledger",
            "schema_version": "claim-evidence-ledger-v3",
            "claims": claims,
        },
        evidence_cards=(
            evidence_cards
            if evidence_cards is not None
            else [
                _card(str(claim["evidence_refs"][0]["evidence_id"]))  # type: ignore[index]
                for claim in claims
            ]
        ),
        gold_claims=gold_claims,
        l3_validation=l3_validation if l3_validation is not None else _l3(),
        **policy,
    )


def _metric(result, name: MetricName):
    return next(metric for metric in result.metrics if metric.name is name)


def test_all_claims_can_be_supported_while_critical_gold_omission_fails_recall() -> (
    None
):
    result = _evaluate(
        [_claim("C-RESET", "reset-path")],
        gold_claims=[
            _gold("G-RESET", "reset-path"),
            _gold("G-CLEANUP", "cleanup-path", critical=True),
        ],
    )

    precision = _metric(result, MetricName.CLAIM_PRECISION)
    recall = _metric(result, MetricName.GOLD_RECALL)
    assert (precision.numerator, precision.denominator, precision.miss_ids) == (
        1,
        1,
        (),
    )
    assert (recall.numerator, recall.denominator) == (1, 2)
    assert recall.miss_ids == ("omitted:G-CLEANUP",)
    assert result.status is AxisStatus.FAIL
    assert [miss.item_id for miss in result.critical_misses] == ["gold:G-CLEANUP"]


def test_right_file_with_line_range_outside_verified_card_is_unsupported() -> None:
    result = _evaluate(
        [_claim("C-LINES", "line-bound", start_line=31, end_line=32)],
        gold_claims=[_gold("G-LINES", "line-bound")],
        evidence_cards=[_card("EV-C-LINES", start_line=10, end_line=20)],
    )

    precision = _metric(result, MetricName.CLAIM_PRECISION)
    assert precision.numerator == 0
    assert precision.miss_ids == ("unsupported:C-LINES",)
    assert result.validation_layers.l1.status is LayerStatus.FAIL
    assert result.status is AxisStatus.FAIL


def test_semantic_contradiction_is_not_mistaken_for_a_cited_supported_claim() -> None:
    result = _evaluate(
        [_claim("C-RETURN", "return-status", l2_status="contradicts")],
        gold_claims=[_gold("G-RETURN", "return-status")],
    )

    assert _metric(result, MetricName.CLAIM_PRECISION).miss_ids == (
        "contradicted:C-RETURN",
    )
    assert _metric(result, MetricName.GOLD_RECALL).miss_ids == (
        "contradicted:G-RETURN",
    )
    assert result.validation_layers.l2.status is LayerStatus.FAIL


def test_canonical_semantic_key_resolver_deduplicates_only_gold_obligations() -> None:
    claims = [
        _claim("C-A", "cleanup-on-error", evidence_id="EV-A"),
        _claim("C-B", "error-releases-resource", evidence_id="EV-B"),
        _claim("C-C", "timeout-retries", evidence_id="EV-C"),
    ]

    def canonical(key: str) -> str:
        return {
            "cleanup-on-error": "cleanup",
            "error-releases-resource": "cleanup",
        }.get(key, key)

    result = _evaluate(
        claims,
        gold_claims=[
            _gold("G-CLEANUP", "cleanup-on-error"),
            _gold("G-TIMEOUT", "timeout-retries"),
        ],
        semantic_key_resolver=canonical,
    )

    precision = _metric(result, MetricName.CLAIM_PRECISION)
    recall = _metric(result, MetricName.GOLD_RECALL)
    assert (precision.numerator, precision.denominator) == (3, 3)
    assert (recall.numerator, recall.denominator) == (2, 2)
    assert result.status is AxisStatus.PASS


@pytest.mark.parametrize(
    (
        "failing_claim",
        "expected_precision_miss",
        "expected_l1",
        "expected_claim_reason",
    ),
    [
        (
            _claim(
                "C-UNSUPPORTED",
                "shared-fact-key",
                l1_status="insufficient",
                l2_status="not_checked",
                evidence_id="EV-UNSUPPORTED",
            ),
            "unsupported:C-UNSUPPORTED",
            (LayerStatus.FAIL, 1, 2),
            "critical_unsupported_evidence",
        ),
        (
            _claim(
                "C-CONTRADICTED",
                "shared-fact-key",
                l2_status="contradicts",
                evidence_id="EV-CONTRADICTED",
            ),
            "contradicted:C-CONTRADICTED",
            (LayerStatus.PASS, 2, 2),
            "critical_contradiction",
        ),
    ],
)
def test_precision_counts_each_emitted_fact_when_semantic_key_is_shared(
    failing_claim: dict[str, object],
    expected_precision_miss: str,
    expected_l1: tuple[LayerStatus, int, int],
    expected_claim_reason: str,
) -> None:
    supported_claim = _claim(
        "C-SUPPORTED",
        "shared-fact-key",
        evidence_id="EV-SUPPORTED",
    )
    result = _evaluate(
        [supported_claim, failing_claim],
        gold_claims=[_gold("G-SHARED", "shared-fact-key", critical=True)],
    )

    precision = _metric(result, MetricName.CLAIM_PRECISION)
    recall = _metric(result, MetricName.GOLD_RECALL)
    assert (precision.numerator, precision.denominator, precision.miss_ids) == (
        1,
        2,
        (expected_precision_miss,),
    )
    assert (recall.numerator, recall.denominator) == (0, 1)
    assert result.validation_layers.l1.status is expected_l1[0]
    assert (
        result.validation_layers.l1.numerator,
        result.validation_layers.l1.denominator,
    ) == expected_l1[1:]
    assert (
        result.validation_layers.l2.numerator,
        result.validation_layers.l2.denominator,
    ) == (1, 3)
    assert [
        (miss.item_id, miss.reason) for miss in result.critical_misses
    ] == [
        (f"claim:{failing_claim['claim_id']}", expected_claim_reason),
        ("gold:G-SHARED", "critical_gold_contradicted")
        if expected_precision_miss.startswith("contradicted:")
        else ("gold:G-SHARED", "critical_gold_omitted"),
    ]
    assert result.status is AxisStatus.FAIL


def test_non_transitive_pairwise_relation_cannot_create_equivalence_inflation() -> None:
    claims = [
        _claim("C-A", "A", evidence_id="EV-A"),
        _claim("C-B", "B", evidence_id="EV-B"),
        _claim("C-C", "C", evidence_id="EV-C"),
    ]

    def canonical(key: str) -> str:
        return {"A": "AB", "B": "AB", "C": "C"}[key]

    result = _evaluate(
        claims,
        gold_claims=[_gold("G-AB", "A"), _gold("G-C", "C")],
        semantic_key_resolver=canonical,
    )

    precision = _metric(result, MetricName.CLAIM_PRECISION)
    recall = _metric(result, MetricName.GOLD_RECALL)
    assert (precision.numerator, precision.denominator) == (3, 3)
    assert (recall.numerator, recall.denominator) == (2, 2)


@pytest.mark.parametrize("second_applicability", ["applicable", "not_applicable"])
def test_distinct_gold_ids_cannot_share_a_canonical_semantic_key(
    second_applicability: str,
) -> None:
    def canonical(key: str) -> str:
        return {"A": "AB", "B": "AB"}.get(key, key)

    with pytest.raises(
        _accuracy().AccuracyInputError,
        match=r"gold canonical key collision.*AB.*G-A.*G-B",
    ):
        _evaluate(
            [_claim("C-A", "A")],
            gold_claims=[
                _gold("G-A", "A"),
                _gold(
                    "G-B",
                    "B",
                    applicability=second_applicability,
                    applicability_evidence_refs=["truth://G-B/applicability"],
                ),
            ],
            semantic_key_resolver=canonical,
        )


def test_default_semantic_matching_is_exact_key_not_substring_or_token_overlap() -> (
    None
):
    result = _evaluate(
        [_claim("C-RESET", "reset")],
        gold_claims=[_gold("G-RESET-TIMEOUT", "reset-timeout")],
    )

    assert _metric(result, MetricName.GOLD_RECALL).miss_ids == (
        "omitted:G-RESET-TIMEOUT",
    )


def test_evidenced_non_applicable_gold_is_excluded_from_denominator() -> None:
    result = _evaluate(
        [_claim("C-RESET", "reset-path")],
        gold_claims=[
            _gold("G-RESET", "reset-path"),
            _gold(
                "G-RDMA",
                "rdma-cleanup",
                critical=True,
                applicability="not_applicable",
                applicability_evidence_refs=["truth://platform/no-rdma"],
            ),
        ],
    )

    recall = _metric(result, MetricName.GOLD_RECALL)
    assert (recall.numerator, recall.denominator, recall.miss_ids) == (1, 1, ())
    assert "truth://platform/no-rdma" in result.evidence_refs
    assert result.status is AxisStatus.PASS


def test_non_applicable_gold_without_evidence_fails_closed_at_l0() -> None:
    result = _evaluate(
        [_claim("C-RESET", "reset-path")],
        gold_claims=[
            _gold("G-RESET", "reset-path"),
            _gold(
                "G-RDMA",
                "rdma-cleanup",
                applicability="not_applicable",
                applicability_evidence_refs=[],
            ),
        ],
    )

    assert result.status is AxisStatus.FAIL
    assert result.validation_layers.l0.status is LayerStatus.FAIL
    assert [miss.item_id for miss in result.critical_misses] == [
        "input:gold:G-RDMA:missing_applicability_evidence"
    ]


def test_l3_unavailable_is_limited_and_never_passes() -> None:
    result = _evaluate(
        [_claim("C-RESET", "reset-path")],
        gold_claims=[_gold("G-RESET", "reset-path")],
        l3_validation=_l3("not_run"),
    )

    assert result.status is AxisStatus.LIMITED
    assert result.validation_layers.l3.status is LayerStatus.NOT_RUN
    assert result.limitations == ("L3_NOT_RUN",)


def test_critical_contradiction_hard_fails_even_with_high_precision() -> None:
    claims = [
        _claim(f"C-{index:03d}", f"fact-{index:03d}", evidence_id=f"EV-{index:03d}")
        for index in range(99)
    ]
    claims.append(
        _claim(
            "C-CRITICAL",
            "critical-cleanup",
            critical=True,
            l2_status="contradicts",
            evidence_id="EV-CRITICAL",
        )
    )
    gold_claims = [
        _gold(f"G-{index:03d}", f"fact-{index:03d}") for index in range(99)
    ] + [_gold("G-CRITICAL", "critical-cleanup", critical=True)]

    result = _evaluate(claims, gold_claims=gold_claims)

    assert _metric(result, MetricName.CLAIM_PRECISION).numerator == 99
    assert _metric(result, MetricName.CLAIM_PRECISION).denominator == 100
    assert result.status is AxisStatus.FAIL
    assert {miss.item_id for miss in result.critical_misses} == {
        "claim:C-CRITICAL",
        "gold:G-CRITICAL",
    }
    assert "claim://C-CRITICAL" in result.validation_layers.l2.evidence_refs


def test_operational_scope_emits_only_claim_precision_and_rejects_gold_truth() -> None:
    result = _evaluate(
        [_claim("C-RESET", "reset-path")],
        scope=EvaluationScope.OPERATIONAL,
        gold_claims=None,
    )

    assert [metric.name for metric in result.metrics] == [MetricName.CLAIM_PRECISION]

    with pytest.raises(
        _accuracy().AccuracyInputError,
        match="operational scope forbids gold claims",
    ):
        _evaluate(
            [_claim("C-RESET", "reset-path")],
            scope=EvaluationScope.OPERATIONAL,
            gold_claims=[_gold("G-RESET", "reset-path")],
        )


def test_benchmark_requires_nonempty_applicable_gold_denominator() -> None:
    with pytest.raises(
        _accuracy().AccuracyInputError,
        match="non-empty gold claims",
    ):
        _evaluate([_claim("C-RESET", "reset-path")], gold_claims=[])

    with pytest.raises(
        _accuracy().AccuracyInputError,
        match="at least one applicable gold claim",
    ):
        _evaluate(
            [_claim("C-RESET", "reset-path")],
            gold_claims=[
                _gold(
                    "G-RDMA",
                    "rdma-path",
                    applicability="not_applicable",
                    applicability_evidence_refs=["truth://platform/no-rdma"],
                )
            ],
        )


def _v3_claim(
    *,
    claim_id: str = "C-V3",
    binding: str = "binding-v3",
    l2_status: str = "supports",
    evidence_id: str = "EV-V3:L12",
    path: str = "lib/storage.c",
    quote: str = "return 0;",
) -> dict[str, object]:
    claim = _claim(claim_id, "ignored-ledger-self-key", l2_status=l2_status)
    claim.pop("evidence_refs")
    claim.pop("semantic_key")
    claim["binding"] = binding
    claim["evidence_checks"] = [
        {
            "evidence_id": evidence_id,
            "path": path,
            "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
            "status": "verified",
        }
    ]
    return claim


def _v3_quote_bindings(
    claim: dict[str, object],
    quote: str,
) -> dict[str, dict[str, str]]:
    identity = f"{claim['claim_id']}@{claim['binding']}"
    evidence_id = claim["evidence_checks"][0]["evidence_id"]  # type: ignore[index]
    return {identity: {str(evidence_id): quote}}


def test_materialized_multiline_v3_ledger_requires_raw_quote_binding(tmp_path) -> None:
    from app.services.artifact_contract_v3 import materialize_claim_evidence_ledger
    from app.services.test_activity_contract import _behavior_claim_binding

    quote = "return SPDK_SUCCESS;"
    card = {
        "evidence_id": "EV-MULTI",
        "file_path": "lib/storage.c",
        "start_line": 40,
        "end_line": 42,
        "symbols": ["submit_io"],
        "excerpt": f"prepare_io();\n{quote}\ncleanup_io();",
        "sha256": "a" * 64,
    }
    evidence = {
        "evidence_id": "EV-MULTI:L41",
        "path": "lib/storage.c",
        "symbol": "submit_io",
        "lines": "L41",
        "quote": quote,
    }
    claim = {
        "claim_id": "C-MULTI",
        "type": "source_behavior",
        "statement": "submit_io returns success on the normal path.",
        "evidence": [evidence],
    }
    binding = _behavior_claim_binding(
        claim_id=claim["claim_id"],
        claim_type=claim["type"],
        statement=claim["statement"],
        evidence=[{**evidence, "sha256": card["sha256"]}],
    )
    (tmp_path / "evidence_cards.json").write_text(json.dumps([card]))
    (tmp_path / "sfmea.json").write_text(
        json.dumps([{"sfmea_id": "R-1", "technical_claims": [claim]}])
    )
    (tmp_path / "black_box_cases.json").write_text("[]")
    (tmp_path / "behavior_claim_validation.json").write_text(
        json.dumps(
            {
                "claims": [
                    {
                        "claim_id": "C-MULTI",
                        "binding": binding,
                        "status": "supports",
                    }
                ]
            }
        )
    )
    ledger = materialize_claim_evidence_ledger(tmp_path)
    identity = f"C-MULTI@{binding}"

    without_binding = _evaluate(
        ledger["claims"],
        scope=EvaluationScope.OPERATIONAL,
        gold_claims=None,
        evidence_cards=[card],
    )
    with_binding = _evaluate(
        ledger["claims"],
        scope=EvaluationScope.OPERATIONAL,
        gold_claims=None,
        evidence_cards=[card],
        evidence_quote_bindings={identity: {"EV-MULTI:L41": quote}},
    )

    assert _metric(without_binding, MetricName.CLAIM_PRECISION).numerator == 0
    assert _metric(with_binding, MetricName.CLAIM_PRECISION).numerator == 1
    assert "source://lib/storage.c#EV-MULTI:L41" in with_binding.evidence_refs


def test_unqualified_v3_card_id_derives_only_a_unique_exact_quote_line() -> None:
    quote = "target_call();"
    claim = _v3_claim(evidence_id="EV-V3", quote=quote)
    identity = "C-V3@binding-v3"
    bindings = {identity: {"EV-V3": quote}}

    unique = _evaluate(
        [claim],
        scope=EvaluationScope.OPERATIONAL,
        gold_claims=None,
        evidence_cards=[
            _card(
                "EV-V3",
                start_line=70,
                end_line=72,
                excerpt=f"before();\n{quote}\nafter();",
            )
        ],
        evidence_quote_bindings=bindings,
    )
    ambiguous = _evaluate(
        [claim],
        scope=EvaluationScope.OPERATIONAL,
        gold_claims=None,
        evidence_cards=[
            _card(
                "EV-V3",
                start_line=70,
                end_line=71,
                excerpt=f"{quote}\n{quote}",
            )
        ],
        evidence_quote_bindings=bindings,
    )
    absent = _evaluate(
        [claim],
        scope=EvaluationScope.OPERATIONAL,
        gold_claims=None,
        evidence_cards=[_card("EV-V3", excerpt="different_call();")],
        evidence_quote_bindings=bindings,
    )
    with pytest.raises(
        _accuracy().AccuracyInputError,
        match="unknown evidence",
    ):
        _evaluate(
            [claim],
            scope=EvaluationScope.OPERATIONAL,
            gold_claims=None,
            evidence_cards=[_card("EV-V3", excerpt=quote)],
            evidence_quote_bindings={identity: {"EV-OTHER": quote}},
        )

    assert _metric(unique, MetricName.CLAIM_PRECISION).numerator == 1
    assert "source://lib/storage.c#EV-V3:L71" in unique.evidence_refs
    assert _metric(ambiguous, MetricName.CLAIM_PRECISION).numerator == 0
    assert _metric(absent, MetricName.CLAIM_PRECISION).numerator == 0


def test_existing_v3_evidence_check_must_bind_to_real_card_content_and_bounds() -> None:
    claim = _v3_claim()
    quote = "return 0;"

    result = _evaluate(
        [claim],
        gold_claims=[_gold("G-V3", "binding-v3")],
        evidence_cards=[_card("EV-V3", excerpt=f"line_10;\nline_11;\n{quote}")],
        evidence_quote_bindings=_v3_quote_bindings(claim, quote),
    )

    assert _metric(result, MetricName.CLAIM_PRECISION).numerator == 1
    assert "source://lib/storage.c#EV-V3:L12" in result.evidence_refs


@pytest.mark.parametrize(
    ("cards", "evidence_id", "path"),
    [
        ([], "EV-V3:L12", "lib/storage.c"),
        (
            [_card("EV-V3", excerpt="line_10;\nline_11;\nreturn 1;")],
            "EV-V3:L12",
            "lib/storage.c",
        ),
        (
            [_card("EV-V3", excerpt="line_10;\nline_11;\nreturn 0;")],
            "EV-V3:L99",
            "lib/storage.c",
        ),
        (
            [_card("EV-V3", excerpt="line_10;\nline_11;\nreturn 0;")],
            "EV-V3:L12",
            "lib/wrong.c",
        ),
    ],
)
def test_v3_verified_status_cannot_bypass_card_verification(
    cards: list[dict[str, object]],
    evidence_id: str,
    path: str,
) -> None:
    claim = _v3_claim(evidence_id=evidence_id, path=path)
    result = _evaluate(
        [claim],
        gold_claims=[_gold("G-V3", "binding-v3")],
        evidence_cards=cards,
        evidence_quote_bindings=_v3_quote_bindings(claim, "return 0;"),
    )

    assert _metric(result, MetricName.CLAIM_PRECISION).miss_ids == (
        "unsupported:C-V3@binding-v3",
    )
    assert result.validation_layers.l1.status is LayerStatus.FAIL


def test_v3_identity_and_miss_ids_include_claim_id_and_binding() -> None:
    claims = [
        _v3_claim(binding="binding-a", quote="return 0;"),
        _v3_claim(
            binding="binding-b",
            quote="return 1;",
            evidence_id="EV-V3-B:L13",
        ),
    ]
    cards = [
        _card("EV-V3", excerpt="line_10;\nline_11;\nreturn 0;"),
        _card("EV-V3-B", excerpt="not the claimed quote"),
    ]
    quote_bindings = {
        **_v3_quote_bindings(claims[0], "return 0;"),
        **_v3_quote_bindings(claims[1], "return 1;"),
    }

    result = _evaluate(
        claims,
        scope=EvaluationScope.OPERATIONAL,
        gold_claims=None,
        evidence_cards=cards,
        evidence_quote_bindings=quote_bindings,
    )

    precision = _metric(result, MetricName.CLAIM_PRECISION)
    assert precision.denominator == 2
    assert precision.miss_ids == ("unsupported:C-V3@binding-b",)


def test_operational_criticality_comes_only_from_external_policy() -> None:
    claim = _v3_claim(l2_status="contradicts")
    claim["critical"] = True
    quote = "return 0;"
    cards = [_card("EV-V3", excerpt=f"line_10;\nline_11;\n{quote}")]
    quote_bindings = _v3_quote_bindings(claim, quote)

    ordinary = _evaluate(
        [claim],
        scope=EvaluationScope.OPERATIONAL,
        gold_claims=None,
        evidence_cards=cards,
        evidence_quote_bindings=quote_bindings,
    )
    critical = _evaluate(
        [claim],
        scope=EvaluationScope.OPERATIONAL,
        gold_claims=None,
        evidence_cards=cards,
        critical_claim_bindings=["binding-v3"],
        evidence_quote_bindings=quote_bindings,
    )
    critical_by_semantic_key = _evaluate(
        [claim],
        scope=EvaluationScope.OPERATIONAL,
        gold_claims=None,
        evidence_cards=cards,
        critical_semantic_keys=["binding-v3"],
        evidence_quote_bindings=quote_bindings,
    )

    assert ordinary.status is AxisStatus.FAIL
    assert ordinary.critical_misses == ()
    assert [miss.item_id for miss in critical.critical_misses] == [
        "claim:C-V3@binding-v3"
    ]
    assert critical.critical_misses[0].reason == "critical_contradiction"
    assert critical_by_semantic_key.critical_misses == critical.critical_misses


def test_module_has_no_generator_prompt_model_or_consensus_imports() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }

    assert imports == {
        "__future__",
        "collections.abc",
        "dataclasses",
        "hashlib",
        "re",
        "typing",
        "app.services.quality_evaluation_contract",
    }
