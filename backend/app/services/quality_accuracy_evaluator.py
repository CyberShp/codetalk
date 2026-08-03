"""Deterministic factual precision and hidden-gold recall evaluation."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.services.quality_evaluation_contract import (
    AxisResult,
    AxisStatus,
    CriticalMiss,
    EvaluationScope,
    LayerStatus,
    MetricName,
    RatioMetric,
    ValidationLayer,
    ValidationLayerOutcome,
    ValidationLayers,
)


class AccuracyInputError(ValueError):
    """Raised when evaluator-only truth crosses a scope boundary."""


class SemanticKeyResolver(Protocol):
    def __call__(self, semantic_key: str, /) -> str: ...


@dataclass(frozen=True)
class _EvidenceBound:
    evidence_id: str
    path: str
    start_line: int
    end_line: int
    excerpt: str


@dataclass(frozen=True)
class _EvidenceCheck:
    evidence_id: str
    path: str
    quote_sha256: str
    status: str


@dataclass(frozen=True)
class _Claim:
    claim_id: str
    binding: str
    semantic_key: str
    l1_status: str
    l2_status: str
    direct_evidence: tuple[_EvidenceBound, ...]
    evidence_checks: tuple[_EvidenceCheck, ...]

    @property
    def identity(self) -> str:
        return f"{self.claim_id}@{self.binding}" if self.binding else self.claim_id

    @property
    def contradicted(self) -> bool:
        return self.l2_status in {"contradicts", "contradicted"}

    @property
    def semantically_supported(self) -> bool:
        return self.l2_status in {"supports", "supported", "verified"}


@dataclass(frozen=True)
class _GoldClaim:
    gold_id: str
    semantic_key: str
    critical: bool
    applicable: bool
    applicability_evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class _ClaimAssessment:
    claim: _Claim
    l1_supported: bool

    @property
    def supported(self) -> bool:
        return self.l1_supported and self.claim.semantically_supported


@dataclass(frozen=True)
class _ClaimGroup:
    canonical_key: str
    assessments: tuple[_ClaimAssessment, ...]

    @property
    def claims(self) -> tuple[_Claim, ...]:
        return tuple(assessment.claim for assessment in self.assessments)

    @property
    def l1_supported(self) -> bool:
        return all(assessment.l1_supported for assessment in self.assessments)

    @property
    def supported(self) -> bool:
        return all(assessment.supported for assessment in self.assessments)


@dataclass(frozen=True)
class _GoldGroup:
    canonical_key: str
    claims: tuple[_GoldClaim, ...]
    matched: bool
    contradicted: bool


@dataclass(frozen=True)
class _InputIssue:
    item_id: str
    reason: str


def identity_semantic_key(semantic_key: str) -> str:
    """Default semantic policy: use the stable key without inference."""

    return semantic_key


def evaluate_accuracy(
    *,
    scope: EvaluationScope | str,
    claim_ledger: Mapping[str, Any],
    evidence_cards: Sequence[Mapping[str, Any]] = (),
    gold_claims: Sequence[Mapping[str, Any]] | None = None,
    l3_validation: Mapping[str, Any] | None = None,
    semantic_key_resolver: SemanticKeyResolver | None = None,
    critical_semantic_keys: Sequence[str] = (),
    critical_claim_bindings: Sequence[str] = (),
    evidence_quote_bindings: Mapping[str, Mapping[str, str]] | None = None,
) -> AxisResult:
    """Evaluate emitted factual claims without generator or model dependencies."""

    normalized_scope = _scope(scope)
    if normalized_scope is EvaluationScope.OPERATIONAL and gold_claims is not None:
        raise AccuracyInputError("operational scope forbids gold claims")
    if (
        normalized_scope is EvaluationScope.INDEPENDENT_BENCHMARK
        and gold_claims is None
    ):
        raise AccuracyInputError("independent_benchmark scope requires gold claims")
    if normalized_scope is EvaluationScope.INDEPENDENT_BENCHMARK and not gold_claims:
        raise AccuracyInputError(
            "independent_benchmark scope requires non-empty gold claims"
        )

    resolver = semantic_key_resolver or identity_semantic_key
    cards = _adapt_evidence_cards(evidence_cards)
    claims = _adapt_claim_ledger(claim_ledger)
    quote_bindings = _adapt_evidence_quote_bindings(
        evidence_quote_bindings,
        claims=claims,
    )
    gold, input_issues = _adapt_gold_claims(gold_claims or ())
    _require_unique_gold_canonical_keys(gold, resolver)

    applicable_gold = tuple(item for item in gold if item.applicable)
    excluded_gold = tuple(item for item in gold if not item.applicable)
    if (
        normalized_scope is EvaluationScope.INDEPENDENT_BENCHMARK
        and not applicable_gold
    ):
        raise AccuracyInputError(
            "independent_benchmark scope requires at least one applicable gold claim"
        )
    claim_groups = _claim_groups(claims, cards, quote_bindings, resolver)
    gold_groups = _gold_groups(applicable_gold, claim_groups, resolver)
    policy_semantic_keys = frozenset(
        _canonical_key(resolver, key)
        for key in _policy_strings(
            critical_semantic_keys,
            field="critical_semantic_keys",
        )
    )
    policy_bindings = frozenset(
        _policy_strings(
            critical_claim_bindings,
            field="critical_claim_bindings",
        )
    )
    critical_gold_keys = frozenset(
        group.canonical_key
        for group in gold_groups
        if any(claim.critical for claim in group.claims)
    )

    precision = _claim_precision(claim_groups)
    metrics = [precision]
    if normalized_scope is EvaluationScope.INDEPENDENT_BENCHMARK:
        metrics.append(_gold_recall(gold_groups))

    l0 = _l0_outcome(input_issues, claim_ledger)
    l1 = _l1_outcome(
        claim_groups,
        critical_semantic_keys=policy_semantic_keys | critical_gold_keys,
        critical_claim_bindings=policy_bindings,
        cards=cards,
        quote_bindings=quote_bindings,
    )
    l2 = _l2_outcome(
        claim_groups,
        gold_groups,
        critical_semantic_keys=policy_semantic_keys | critical_gold_keys,
        critical_claim_bindings=policy_bindings,
    )
    l3 = _l3_outcome(l3_validation)
    limitations = _dedupe((*l3.limitations,))
    critical_misses = _critical_misses(
        input_issues=input_issues,
        claim_groups=claim_groups,
        gold_groups=gold_groups,
        l3=l3,
        critical_semantic_keys=policy_semantic_keys | critical_gold_keys,
        critical_claim_bindings=policy_bindings,
        cards=cards,
        quote_bindings=quote_bindings,
    )

    layers = ValidationLayers(L0=l0, L1=l1, L2=l2, L3=l3)
    layer_values = (l0, l1, l2, l3)
    if any(layer.status is LayerStatus.FAIL for layer in layer_values):
        status = AxisStatus.FAIL
    elif limitations or l3.status is LayerStatus.NOT_RUN:
        status = AxisStatus.LIMITED
    else:
        status = AxisStatus.PASS

    evidence_refs = _dedupe(
        (
            *l0.evidence_refs,
            *l1.evidence_refs,
            *l2.evidence_refs,
            *l3.evidence_refs,
            *(
                ref
                for item in excluded_gold
                for ref in item.applicability_evidence_refs
            ),
        )
    )
    return AxisResult(
        status=status,
        numerator=precision.numerator,
        denominator=precision.denominator,
        critical_misses=critical_misses,
        evidence_refs=evidence_refs,
        limitations=limitations,
        validation_layers=layers,
        metrics=tuple(metrics),
    )


def _scope(value: EvaluationScope | str) -> EvaluationScope:
    if isinstance(value, EvaluationScope):
        return value
    try:
        return EvaluationScope(value)
    except ValueError as exc:
        raise AccuracyInputError(f"unknown evaluation scope: {value}") from exc


def _adapt_evidence_cards(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, _EvidenceBound]:
    cards: dict[str, _EvidenceBound] = {}
    for row in rows:
        evidence_id = _required_string(row, "evidence_id")
        if evidence_id in cards:
            raise AccuracyInputError(f"duplicate evidence_id: {evidence_id}")
        path = _required_string(row, "path", fallback="file_path")
        start_line = _required_integer(row, "start_line", fallback="line_start")
        end_line = _required_integer(row, "end_line", fallback="line_end")
        if start_line <= 0 or end_line < start_line:
            raise AccuracyInputError(f"invalid evidence bounds: {evidence_id}")
        cards[evidence_id] = _EvidenceBound(
            evidence_id=evidence_id,
            path=path,
            start_line=start_line,
            end_line=end_line,
            excerpt=(row.get("excerpt") if isinstance(row.get("excerpt"), str) else ""),
        )
    return cards


def _adapt_claim_ledger(claim_ledger: Mapping[str, Any]) -> tuple[_Claim, ...]:
    raw_claims = claim_ledger.get("claims")
    if not _is_mapping_sequence(raw_claims):
        raise AccuracyInputError("claim ledger claims must be a list of objects")
    claims: list[_Claim] = []
    seen_identities: set[tuple[str, str]] = set()
    for raw in raw_claims:
        claim_id = _required_string(raw, "claim_id")
        binding = _optional_string(raw.get("binding"))
        identity = (claim_id, binding)
        if identity in seen_identities:
            raise AccuracyInputError(f"duplicate claim identity: {claim_id}@{binding}")
        seen_identities.add(identity)
        semantic_key = _optional_string(raw.get("semantic_key"))
        semantic_key = semantic_key or binding or claim_id
        direct_evidence = _adapt_direct_evidence(raw.get("evidence_refs"))
        pre_resolved = _adapt_pre_resolved_evidence(raw.get("evidence_checks"))
        claims.append(
            _Claim(
                claim_id=claim_id,
                binding=binding,
                semantic_key=semantic_key,
                l1_status=_optional_string(raw.get("l1_status")) or "insufficient",
                l2_status=_optional_string(raw.get("l2_status")) or "not_checked",
                direct_evidence=direct_evidence,
                evidence_checks=pre_resolved,
            )
        )
    return tuple(sorted(claims, key=lambda item: (item.claim_id, item.binding)))


def _adapt_evidence_quote_bindings(
    value: Mapping[str, Mapping[str, str]] | None,
    *,
    claims: tuple[_Claim, ...],
) -> dict[tuple[str, str], str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise AccuracyInputError("evidence_quote_bindings must be a mapping")
    claims_by_identity = {claim.identity: claim for claim in claims}
    result: dict[tuple[str, str], str] = {}
    for identity, evidence in value.items():
        if (
            not isinstance(identity, str)
            or not identity
            or identity != identity.strip()
        ):
            raise AccuracyInputError(
                "evidence_quote_bindings identities must be exact non-empty strings"
            )
        claim = claims_by_identity.get(identity)
        if claim is None:
            raise AccuracyInputError(
                f"evidence_quote_bindings contains unknown claim identity: {identity}"
            )
        if not isinstance(evidence, Mapping):
            raise AccuracyInputError(
                f"evidence_quote_bindings[{identity}] must be a mapping"
            )
        known_evidence_ids = {check.evidence_id for check in claim.evidence_checks}
        for evidence_id, quote in evidence.items():
            if (
                not isinstance(evidence_id, str)
                or not evidence_id
                or evidence_id != evidence_id.strip()
                or evidence_id not in known_evidence_ids
            ):
                raise AccuracyInputError(
                    f"evidence_quote_bindings contains unknown evidence for {identity}"
                )
            if not isinstance(quote, str) or not quote:
                raise AccuracyInputError(
                    f"evidence_quote_bindings quote must be a non-empty string: {identity}"
                )
            result[(identity, evidence_id)] = quote
    return result


def _adapt_direct_evidence(value: Any) -> tuple[_EvidenceBound, ...]:
    if value is None:
        return ()
    if not _is_mapping_sequence(value):
        return ()
    refs: list[_EvidenceBound] = []
    for raw in value:
        try:
            evidence_id = _required_string(raw, "evidence_id")
            path = _required_string(raw, "path", fallback="file_path")
            start_line = _required_integer(raw, "start_line", fallback="line_start")
            end_line = _required_integer(raw, "end_line", fallback="line_end")
        except AccuracyInputError:
            return ()
        refs.append(
            _EvidenceBound(
                evidence_id=evidence_id,
                path=path,
                start_line=start_line,
                end_line=end_line,
                excerpt="",
            )
        )
    return tuple(refs)


def _adapt_pre_resolved_evidence(value: Any) -> tuple[_EvidenceCheck, ...]:
    if not _is_mapping_sequence(value):
        return ()
    checks: list[_EvidenceCheck] = []
    for raw in value:
        checks.append(
            _EvidenceCheck(
                evidence_id=_optional_string(raw.get("evidence_id")),
                path=_optional_string(raw.get("path")),
                quote_sha256=_optional_string(raw.get("quote_sha256")),
                status=_optional_string(raw.get("status")),
            )
        )
    return tuple(checks)


def _adapt_gold_claims(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[tuple[_GoldClaim, ...], tuple[_InputIssue, ...]]:
    claims: list[_GoldClaim] = []
    issues: list[_InputIssue] = []
    seen_ids: set[str] = set()
    for raw in rows:
        gold_id = _required_string(raw, "gold_id", fallback="claim_id")
        if gold_id in seen_ids:
            raise AccuracyInputError(f"duplicate gold_id: {gold_id}")
        seen_ids.add(gold_id)
        semantic_key = _required_string(raw, "semantic_key")
        applicability = _optional_string(raw.get("applicability")) or "applicable"
        if applicability not in {"applicable", "not_applicable"}:
            raise AccuracyInputError(
                f"invalid gold applicability for {gold_id}: {applicability}"
            )
        evidence_refs = _string_tuple(raw.get("applicability_evidence_refs"))
        if applicability == "not_applicable" and not evidence_refs:
            issues.append(
                _InputIssue(
                    item_id=f"input:gold:{gold_id}:missing_applicability_evidence",
                    reason="non_applicable_gold_requires_explicit_evidence",
                )
            )
        claims.append(
            _GoldClaim(
                gold_id=gold_id,
                semantic_key=semantic_key,
                critical=raw.get("critical") is True,
                applicable=applicability == "applicable",
                applicability_evidence_refs=evidence_refs,
            )
        )
    return (
        tuple(sorted(claims, key=lambda item: item.gold_id)),
        tuple(sorted(issues, key=lambda item: item.item_id)),
    )


def _claim_groups(
    claims: tuple[_Claim, ...],
    cards: Mapping[str, _EvidenceBound],
    quote_bindings: Mapping[tuple[str, str], str],
    resolver: SemanticKeyResolver,
) -> tuple[_ClaimGroup, ...]:
    grouped: dict[str, list[_Claim]] = {}
    for claim in claims:
        grouped.setdefault(_canonical_key(resolver, claim.semantic_key), []).append(
            claim
        )
    result: list[_ClaimGroup] = []
    for canonical_key in sorted(grouped):
        group = tuple(grouped[canonical_key])
        assessments = tuple(
            _ClaimAssessment(
                claim=item,
                l1_supported=_claim_has_exact_evidence(item, cards, quote_bindings),
            )
            for item in group
        )
        result.append(
            _ClaimGroup(
                canonical_key=canonical_key,
                assessments=assessments,
            )
        )
    return tuple(result)


def _gold_groups(
    gold: tuple[_GoldClaim, ...],
    claim_groups: tuple[_ClaimGroup, ...],
    resolver: SemanticKeyResolver,
) -> tuple[_GoldGroup, ...]:
    grouped: dict[str, list[_GoldClaim]] = {}
    for claim in gold:
        grouped.setdefault(_canonical_key(resolver, claim.semantic_key), []).append(
            claim
        )
    emitted_by_key = {group.canonical_key: group for group in claim_groups}
    result: list[_GoldGroup] = []
    for canonical_key in sorted(grouped):
        group = tuple(grouped[canonical_key])
        emitted = emitted_by_key.get(canonical_key)
        result.append(
            _GoldGroup(
                canonical_key=canonical_key,
                claims=group,
                matched=bool(
                    emitted
                    and any(
                        assessment.supported
                        for assessment in emitted.assessments
                    )
                ),
                contradicted=bool(
                    emitted and any(claim.contradicted for claim in emitted.claims)
                ),
            )
        )
    return tuple(result)


def _require_unique_gold_canonical_keys(
    gold: tuple[_GoldClaim, ...],
    resolver: SemanticKeyResolver,
) -> None:
    owners: dict[str, str] = {}
    for claim in gold:
        canonical_key = _canonical_key(resolver, claim.semantic_key)
        existing_id = owners.get(canonical_key)
        if existing_id is not None:
            raise AccuracyInputError(
                "gold canonical key collision "
                f"for {canonical_key}: {existing_id}, {claim.gold_id}"
            )
        owners[canonical_key] = claim.gold_id


def _canonical_key(resolver: SemanticKeyResolver, semantic_key: str) -> str:
    try:
        first = resolver(semantic_key)
        second = resolver(semantic_key)
    except Exception as exc:
        raise AccuracyInputError("semantic key resolver failed") from exc
    if not isinstance(first, str) or not first.strip():
        raise AccuracyInputError("semantic key resolver returned an empty key")
    if first != second:
        raise AccuracyInputError("semantic key resolver is non-deterministic")
    return first.strip()


def _claim_has_exact_evidence(
    claim: _Claim,
    cards: Mapping[str, _EvidenceBound],
    quote_bindings: Mapping[tuple[str, str], str],
) -> bool:
    if claim.l1_status != "verified":
        return False
    has_evidence = bool(claim.direct_evidence or claim.evidence_checks)
    direct_valid = not claim.direct_evidence or all(
        (card := cards.get(ref.evidence_id)) is not None
        and ref.path == card.path
        and card.start_line <= ref.start_line <= ref.end_line <= card.end_line
        for ref in claim.direct_evidence
    )
    checks_valid = not claim.evidence_checks or all(
        _resolve_evidence_check(
            claim,
            check,
            cards,
            quote_bindings,
        )
        is not None
        for check in claim.evidence_checks
    )
    return has_evidence and direct_valid and checks_valid


def _resolve_evidence_check(
    claim: _Claim,
    check: _EvidenceCheck,
    cards: Mapping[str, _EvidenceBound],
    quote_bindings: Mapping[tuple[str, str], str],
) -> str | None:
    if check.status != "verified":
        return None
    line_match = re.fullmatch(r"(.+):L([1-9][0-9]*)", check.evidence_id)
    base_id = line_match.group(1) if line_match else check.evidence_id
    declared_line = int(line_match.group(2)) if line_match else None
    card = cards.get(base_id)
    quote = quote_bindings.get((claim.identity, check.evidence_id))
    if (
        card is None
        or not check.path
        or check.path != card.path
        or not card.excerpt
        or not quote
        or not re.fullmatch(r"[0-9a-f]{64}", check.quote_sha256)
    ):
        return None
    if hashlib.sha256(quote.encode("utf-8")).hexdigest() != check.quote_sha256:
        return None
    offset = card.excerpt.find(quote)
    if offset < 0 or card.excerpt.find(quote, offset + 1) >= 0:
        return None
    prefix = card.excerpt[:offset].replace("\r\n", "\n").replace("\r", "\n")
    normalized_quote = quote.replace("\r\n", "\n").replace("\r", "\n")
    derived_line = card.start_line + prefix.count("\n")
    derived_end_line = derived_line + normalized_quote.count("\n")
    if not (card.start_line <= derived_line <= derived_end_line <= card.end_line):
        return None
    if declared_line is not None and declared_line != derived_line:
        return None
    return f"source://{card.path}#{base_id}:L{derived_line}"


def _claim_precision(groups: tuple[_ClaimGroup, ...]) -> RatioMetric:
    if not groups:
        return RatioMetric(
            name=MetricName.CLAIM_PRECISION,
            numerator=0,
            denominator=1,
            miss_ids=("unsupported:NO_EMITTED_FACTUAL_CLAIMS",),
        )
    assessments = tuple(
        assessment for group in groups for assessment in group.assessments
    )
    supported = sum(assessment.supported for assessment in assessments)
    misses = _dedupe(
        tuple(
            f"contradicted:{assessment.claim.identity}"
            if assessment.claim.contradicted
            else f"unsupported:{assessment.claim.identity}"
            for assessment in assessments
            if not assessment.supported
        )
    )
    return RatioMetric(
        name=MetricName.CLAIM_PRECISION,
        numerator=supported,
        denominator=len(assessments),
        miss_ids=misses,
    )


def _gold_recall(groups: tuple[_GoldGroup, ...]) -> RatioMetric:
    if not groups:
        raise AccuracyInputError(
            "independent_benchmark scope requires a valid gold denominator"
        )
    misses = _dedupe(
        tuple(
            f"{'contradicted' if group.contradicted else 'omitted'}:{claim.gold_id}"
            for group in groups
            if not group.matched
            for claim in group.claims
        )
    )
    return RatioMetric(
        name=MetricName.GOLD_RECALL,
        numerator=sum(group.matched for group in groups),
        denominator=len(groups),
        miss_ids=misses,
    )


def _l0_outcome(
    issues: tuple[_InputIssue, ...],
    claim_ledger: Mapping[str, Any],
) -> ValidationLayerOutcome:
    passed = not issues
    return ValidationLayerOutcome(
        status=LayerStatus.PASS if passed else LayerStatus.FAIL,
        numerator=1 if passed else 0,
        denominator=1,
        critical_miss_ids=tuple(issue.item_id for issue in issues),
        evidence_refs=(
            f"artifact://{_optional_string(claim_ledger.get('schema_version')) or 'claim-ledger'}",
        ),
        limitations=(),
    )


def _l1_outcome(
    groups: tuple[_ClaimGroup, ...],
    *,
    critical_semantic_keys: frozenset[str],
    critical_claim_bindings: frozenset[str],
    cards: Mapping[str, _EvidenceBound],
    quote_bindings: Mapping[tuple[str, str], str],
) -> ValidationLayerOutcome:
    denominator = max(1, sum(len(group.assessments) for group in groups))
    numerator = sum(
        assessment.l1_supported
        for group in groups
        for assessment in group.assessments
    )
    critical = tuple(
        f"claim:{assessment.claim.identity}"
        for group in groups
        for assessment in group.assessments
        if not assessment.l1_supported
        if _claim_is_critical(
            assessment.claim,
            group,
            critical_semantic_keys=critical_semantic_keys,
            critical_claim_bindings=critical_claim_bindings,
        )
    )
    return ValidationLayerOutcome(
        status=LayerStatus.PASS if numerator == denominator else LayerStatus.FAIL,
        numerator=numerator,
        denominator=denominator,
        critical_miss_ids=_dedupe(critical),
        evidence_refs=_dedupe(
            ref
            for group in groups
            for claim in group.claims
            for ref in _claim_evidence_refs(claim, cards, quote_bindings)
        ),
        limitations=(),
    )


def _l2_outcome(
    claim_groups: tuple[_ClaimGroup, ...],
    gold_groups: tuple[_GoldGroup, ...],
    *,
    critical_semantic_keys: frozenset[str],
    critical_claim_bindings: frozenset[str],
) -> ValidationLayerOutcome:
    denominator = (
        sum(len(group.assessments) for group in claim_groups) + len(gold_groups)
    )
    denominator = max(1, denominator)
    numerator = sum(
        assessment.supported
        for group in claim_groups
        for assessment in group.assessments
    ) + sum(group.matched for group in gold_groups)
    critical_claims = (
        f"claim:{assessment.claim.identity}"
        for group in claim_groups
        for assessment in group.assessments
        if not assessment.supported
        if _claim_is_critical(
            assessment.claim,
            group,
            critical_semantic_keys=critical_semantic_keys,
            critical_claim_bindings=critical_claim_bindings,
        )
    )
    critical_gold = (
        f"gold:{claim.gold_id}"
        for group in gold_groups
        if not group.matched
        for claim in group.claims
        if claim.critical
    )
    return ValidationLayerOutcome(
        status=LayerStatus.PASS if numerator == denominator else LayerStatus.FAIL,
        numerator=numerator,
        denominator=denominator,
        critical_miss_ids=_dedupe((*critical_claims, *critical_gold)),
        evidence_refs=_dedupe(
            (
                *(
                    f"claim://{claim.identity}"
                    for group in claim_groups
                    for claim in group.claims
                ),
                *(
                    f"gold://{claim.gold_id}"
                    for group in gold_groups
                    for claim in group.claims
                ),
            )
        ),
        limitations=(),
    )


def _claim_is_critical(
    claim: _Claim,
    group: _ClaimGroup,
    *,
    critical_semantic_keys: frozenset[str],
    critical_claim_bindings: frozenset[str],
) -> bool:
    return group.canonical_key in critical_semantic_keys or bool(
        claim.binding and claim.binding in critical_claim_bindings
    )


def _l3_outcome(value: Mapping[str, Any] | None) -> ValidationLayerOutcome:
    if value is None:
        return ValidationLayerOutcome(
            status=LayerStatus.NOT_RUN,
            numerator=0,
            denominator=1,
            critical_miss_ids=(),
            evidence_refs=(),
            limitations=("L3_NOT_RUN",),
        )
    try:
        status = LayerStatus(_required_string(value, "status"))
        numerator = _required_integer(value, "numerator")
        denominator = _required_integer(value, "denominator")
    except ValueError as exc:
        raise AccuracyInputError("invalid L3 validation") from exc
    limitations = _string_tuple(value.get("limitations"))
    if status is LayerStatus.NOT_RUN and "L3_NOT_RUN" not in limitations:
        limitations = (*limitations, "L3_NOT_RUN")
    try:
        return ValidationLayerOutcome(
            status=status,
            numerator=numerator,
            denominator=denominator,
            critical_miss_ids=_string_tuple(value.get("critical_miss_ids")),
            evidence_refs=_string_tuple(value.get("evidence_refs")),
            limitations=limitations,
        )
    except ValueError as exc:
        raise AccuracyInputError("invalid L3 validation") from exc


def _critical_misses(
    *,
    input_issues: tuple[_InputIssue, ...],
    claim_groups: tuple[_ClaimGroup, ...],
    gold_groups: tuple[_GoldGroup, ...],
    l3: ValidationLayerOutcome,
    critical_semantic_keys: frozenset[str],
    critical_claim_bindings: frozenset[str],
    cards: Mapping[str, _EvidenceBound],
    quote_bindings: Mapping[tuple[str, str], str],
) -> tuple[CriticalMiss, ...]:
    misses: list[CriticalMiss] = [
        CriticalMiss(
            item_id=issue.item_id,
            reason=issue.reason,
            validation_layer=ValidationLayer.L0,
            evidence_refs=(),
        )
        for issue in input_issues
    ]
    for group in claim_groups:
        for assessment in group.assessments:
            if assessment.supported:
                continue
            claim = assessment.claim
            if not _claim_is_critical(
                claim,
                group,
                critical_semantic_keys=critical_semantic_keys,
                critical_claim_bindings=critical_claim_bindings,
            ):
                continue
            if claim.contradicted:
                reason = "critical_contradiction"
                layer = ValidationLayer.L2
            elif not assessment.l1_supported:
                reason = "critical_unsupported_evidence"
                layer = ValidationLayer.L1
            else:
                reason = "critical_unsupported_claim"
                layer = ValidationLayer.L2
            misses.append(
                CriticalMiss(
                    item_id=f"claim:{claim.identity}",
                    reason=reason,
                    validation_layer=layer,
                    evidence_refs=_dedupe(
                        (
                            *_claim_evidence_refs(claim, cards, quote_bindings),
                            f"claim://{claim.identity}",
                        )
                    ),
                )
            )
    for group in gold_groups:
        if group.matched:
            continue
        for claim in group.claims:
            if claim.critical:
                misses.append(
                    CriticalMiss(
                        item_id=f"gold:{claim.gold_id}",
                        reason=(
                            "critical_gold_contradicted"
                            if group.contradicted
                            else "critical_gold_omitted"
                        ),
                        validation_layer=ValidationLayer.L2,
                        evidence_refs=(f"gold://{claim.gold_id}",),
                    )
                )
    for item_id in l3.critical_miss_ids:
        misses.append(
            CriticalMiss(
                item_id=f"oracle:{item_id}",
                reason="critical_execution_oracle_failure",
                validation_layer=ValidationLayer.L3,
                evidence_refs=l3.evidence_refs,
            )
        )
    by_id = {miss.item_id: miss for miss in misses}
    return tuple(by_id[item_id] for item_id in sorted(by_id))


def _claim_evidence_refs(
    claim: _Claim,
    cards: Mapping[str, _EvidenceBound],
    quote_bindings: Mapping[tuple[str, str], str],
) -> tuple[str, ...]:
    direct = tuple(
        f"source://{ref.path}#L{ref.start_line}-L{ref.end_line}"
        for ref in claim.direct_evidence
    )
    checked = tuple(
        resolved
        for check in claim.evidence_checks
        if (
            resolved := _resolve_evidence_check(
                claim,
                check,
                cards,
                quote_bindings,
            )
        )
        is not None
    )
    return _dedupe((*direct, *checked))


def _policy_strings(value: Sequence[str], *, field: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise AccuracyInputError(f"{field} must be a sequence of strings")
    normalized = tuple(
        item.strip() for item in value if isinstance(item, str) and item.strip()
    )
    if len(normalized) != len(value):
        raise AccuracyInputError(f"{field} must contain only non-empty strings")
    return _dedupe(normalized)


def _required_string(
    value: Mapping[str, Any],
    key: str,
    *,
    fallback: str | None = None,
) -> str:
    result = _optional_string(value.get(key))
    if not result and fallback:
        result = _optional_string(value.get(fallback))
    if not result:
        raise AccuracyInputError(f"missing non-empty string: {key}")
    return result


def _optional_string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _required_integer(
    value: Mapping[str, Any],
    key: str,
    *,
    fallback: str | None = None,
) -> int:
    result = value.get(key)
    if result is None and fallback:
        result = value.get(fallback)
    if isinstance(result, bool) or not isinstance(result, int):
        raise AccuracyInputError(f"missing integer: {key}")
    return result


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return _dedupe(
        item.strip() for item in value if isinstance(item, str) and item.strip()
    )


def _is_mapping_sequence(value: Any) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and all(isinstance(item, Mapping) for item in value)
    )


def _dedupe(values: Sequence[str] | Any) -> tuple[str, ...]:
    return tuple(sorted(set(values)))
