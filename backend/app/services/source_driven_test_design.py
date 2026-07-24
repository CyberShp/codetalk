"""Deterministic source-driven black-box test design artifacts.

The LLM stages explain and rank verified evidence. This module owns the
coverage bookkeeping, traceability, delivery gate, and mind-map rendering so
that missing evidence cannot be hidden by fluent prose.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "source-driven-test-design-v2"
MINDMAP_SCHEMA_VERSION = "test-design-mindmap-v1"

ALLOWED_DISPOSITIONS = frozenset(
    {
        "retain",
        "merge_into",
        "covered_by_other",
        "not_testable",
        "not_applicable",
        "blocked",
        "need_verify",
    }
)

SOURCE_DRIVEN_V2_ARTIFACTS = (
    "entrypoints.json",
    "flows.json",
    "states.json",
    "resources.json",
    "model_applicability.json",
    "flow_cards.json",
    "developer_explanation_coverage.json",
    "branch_disposition.json",
    "state_transition_disposition.json",
    "resource_lifecycle_disposition.json",
    "error_propagation_chains.json",
    "evidence_consumption_ledger.json",
    "scenario_candidates.json",
    "risk_register.json",
    "blackbox_control_observation.json",
    "test_basis.json",
    "test_scenarios.json",
    "test_flows.json",
    "traceability_matrix.json",
    "judge_report.json",
)

MINDMAP_ARTIFACTS = (
    "test_design_mindmap.json",
    "test_design_mindmap.html",
    "test_design_mindmap.svg",
)

_RESOURCE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("cmd", re.compile(r"\b(?:cmd|command)(?:s|_pool|_count|_sn)?\b", re.I)),
    ("pdu", re.compile(r"\bpdu(?:s|_pool|_count)?\b", re.I)),
    ("queue_item", re.compile(r"\b(?:queue|qpair|request|task)(?:s|_depth|_count|_pool)?\b", re.I)),
    ("connection", re.compile(r"\b(?:conn|connection)(?:s|_count|_pool)?\b", re.I)),
    ("session", re.compile(r"\b(?:sess|session)(?:ions|_count|_pool)?\b", re.I)),
    ("reference", re.compile(r"\b(?:ref|refs|refcnt|ref_count|reference)\b", re.I)),
    ("bitmap", re.compile(r"\b(?:bitmap|bit_map|bitset)\b", re.I)),
    ("counter", re.compile(r"\b(?:counter|count|generation|gen|tag|sequence|seq)\b", re.I)),
    ("handle", re.compile(r"\b(?:handle|fd|descriptor)\b", re.I)),
    ("quota", re.compile(r"\b(?:quota|limit|maximum|max_)\w*\b", re.I)),
    ("memory", re.compile(r"\b(?:malloc|calloc|realloc|free|memory|buffer)\b", re.I)),
)

_FINITE_RESOURCE_KINDS = {
    "cmd",
    "pdu",
    "queue_item",
    "connection",
    "session",
    "bitmap",
    "handle",
    "quota",
}

_RESOURCE_LIFECYCLE_RE = re.compile(
    r"(?:^|[_\W])(?:alloc(?:ate)?|calloc|malloc|free|release|destroy|destruct|acquire|"
    r"reclaim|put|get|reserve|unreserve|enqueue|dequeue|pool)\w*",
    re.I,
)
_RESOURCE_CAPACITY_RE = re.compile(
    r"(?:^|[_\W])(?:max(?:imum)?|limit|capacity|quota|pool|depth|bitmap|available|"
    r"free_count|in_use|outstanding)\w*",
    re.I,
)
_RESOURCE_WRAP_RE = re.compile(
    r"(?:^|[_\W])(?:wrap(?:around)?|rollover|overflow|generation|gen(?:eration)?_id|"
    r"sequence|seq(?:uence)?_id|tag(?:_id)?)\w*",
    re.I,
)
_CONCURRENCY_RE = re.compile(
    r"\b(?:thread|mutex|lock|atomic|poller|concurrent|parallel|race|shared|"
    r"message|async|callback)\w*\b",
    re.I,
)
_PROTOCOL_RE = re.compile(
    r"\b(?:iscsi|nvme|tcp|tls|chap|auth|login|opcode|pdu|rpc|config|"
    r"protocol|request|response)\w*\b",
    re.I,
)

_SCENARIO_SOURCE_IDS = (
    "branch",
    "state",
    "resource",
    "numeric_boundary_and_wrap",
    "concurrency",
    "error_propagation",
    "protocol_requirement_security_configuration",
    "coverage_history",
)

_REQUIRED_NONEMPTY_ITEMS = (
    "entrypoints.json",
    "flows.json",
    "flow_cards.json",
    "evidence_consumption_ledger.json",
    "scenario_candidates.json",
    "risk_register.json",
    "blackbox_control_observation.json",
    "test_scenarios.json",
    "test_flows.json",
)

def build_source_driven_test_design(
    *,
    source_pack: dict[str, Any],
    flow_pack: dict[str, Any],
    flow_outline: dict[str, Any],
    sfmea: list[dict[str, Any]],
    black_box_cases: list[dict[str, Any]],
    fact_verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build every V2 governance artifact from already verified stage data."""

    evidence_index = _evidence_index(source_pack, flow_pack)
    entrypoints = _entrypoints_artifact(source_pack, flow_pack)
    flows = _flows_artifact(flow_outline)
    states = _states_artifact(flow_pack)
    resources = _resources_artifact(source_pack, flow_pack)
    applicability = _model_applicability_artifact(
        flow_pack=flow_pack,
        states=states,
        resources=resources,
        source_pack=source_pack,
    )
    flow_cards = _flow_cards_artifact(
        flows=flows,
        flow_outline=flow_outline,
        flow_pack=flow_pack,
    )
    branch_disposition = _branch_disposition_artifact(
        flow_pack=flow_pack,
        cases=black_box_cases,
    )
    state_disposition = _state_disposition_artifact(
        states=states,
        cases=black_box_cases,
    )
    resource_disposition = _resource_disposition_artifact(
        resources=resources,
        flow_pack=flow_pack,
        cases=black_box_cases,
    )
    error_chains = _error_propagation_artifact(flow_pack, black_box_cases)
    explanation_coverage = _developer_explanation_coverage_artifact(
        flow_cards=flow_cards,
        branch_disposition=branch_disposition,
        state_disposition=state_disposition,
        resource_disposition=resource_disposition,
        error_chains=error_chains,
    )
    consumption = _evidence_consumption_artifact(source_pack, flow_pack)
    scenario_candidates = _scenario_candidates_artifact(
        source_pack=source_pack,
        flow_pack=flow_pack,
        applicability=applicability,
        branches=branch_disposition,
        states=state_disposition,
        resources=resource_disposition,
        errors=error_chains,
    )
    risks = _risk_register_artifact(sfmea, scenario_candidates)
    controls = _blackbox_control_observation_artifact(black_box_cases)
    test_basis = _test_basis_artifact(
        source_pack=source_pack,
        consumption=consumption,
        risks=risks,
        candidates=scenario_candidates,
    )
    scenarios = _test_scenarios_artifact(black_box_cases, scenario_candidates)
    test_flows = _test_flows_artifact(scenarios)
    traceability = _traceability_artifact(
        evidence_index=evidence_index,
        flows=flows,
        branches=branch_disposition,
        states=state_disposition,
        resources=resource_disposition,
        errors=error_chains,
        risks=risks,
        scenarios=scenarios,
        cases=black_box_cases,
    )

    artifacts: dict[str, Any] = {
        "entrypoints.json": entrypoints,
        "flows.json": flows,
        "states.json": states,
        "resources.json": resources,
        "model_applicability.json": applicability,
        "flow_cards.json": flow_cards,
        "developer_explanation_coverage.json": explanation_coverage,
        "branch_disposition.json": branch_disposition,
        "state_transition_disposition.json": state_disposition,
        "resource_lifecycle_disposition.json": resource_disposition,
        "error_propagation_chains.json": error_chains,
        "evidence_consumption_ledger.json": consumption,
        "scenario_candidates.json": scenario_candidates,
        "risk_register.json": risks,
        "blackbox_control_observation.json": controls,
        "test_basis.json": test_basis,
        "test_scenarios.json": scenarios,
        "test_flows.json": test_flows,
        "traceability_matrix.json": traceability,
    }
    artifacts["judge_report.json"] = build_judge_report(
        artifacts=artifacts,
        fact_verification=fact_verification,
    )
    return artifacts


def build_judge_report(
    *,
    artifacts: dict[str, Any],
    fact_verification: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return an independent, fail-closed coverage verdict."""

    missing = [name for name in SOURCE_DRIVEN_V2_ARTIFACTS if name != "judge_report.json" and name not in artifacts]
    malformed = [
        name
        for name, payload in artifacts.items()
        if name.endswith(".json") and not isinstance(payload, (dict, list))
    ]
    empty_required = []
    for name in _REQUIRED_NONEMPTY_ITEMS:
        payload = artifacts.get(name)
        rows = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            empty_required.append(f"{name}:empty")
    traceability = artifacts.get("traceability_matrix.json")
    traceability = traceability if isinstance(traceability, dict) else {}
    if not isinstance(traceability.get("links"), list) or not traceability.get("links"):
        empty_required.append("traceability_matrix.json:links_empty")
    orphan_cases = _strings(traceability.get("orphan_case_ids"))
    high_risk_unmapped = _strings(traceability.get("high_risk_unmapped_ids"))
    unknown_risks = [
        f"unknown_risk_id:{value}"
        for value in _strings(traceability.get("unknown_risk_ids"))
    ]
    unresolved_evidence = [
        f"unresolved_evidence_ref:{value}"
        for link in traceability.get("links") or []
        if isinstance(link, dict)
        for value in _strings(link.get("unresolved_evidence_refs"))
    ]
    disposition_artifacts = (
        "branch_disposition.json",
        "state_transition_disposition.json",
        "resource_lifecycle_disposition.json",
    )
    undisposed: list[str] = []
    unresolved: list[str] = []
    for name in disposition_artifacts:
        payload = artifacts.get(name)
        rows = payload.get("items") if isinstance(payload, dict) else []
        for row in rows or []:
            if not isinstance(row, dict) or str(row.get("disposition") or "") not in ALLOWED_DISPOSITIONS:
                undisposed.append(f"{name}:{row.get('id') if isinstance(row, dict) else '?'}")
            elif str(row.get("disposition") or "") in {"blocked", "need_verify"}:
                unresolved.append(f"{name}:{row.get('id') or '?'}:{row.get('disposition')}")

    applicability = artifacts.get("model_applicability.json")
    applicability_rows = applicability.get("items") if isinstance(applicability, dict) else []
    applicability_by_id = {
        str(row.get("model") or ""): row
        for row in applicability_rows or []
        if isinstance(row, dict)
    }
    missing_models = [
        model for model in _SCENARIO_SOURCE_IDS if model not in applicability_by_id
    ]
    scenario_candidates = artifacts.get("scenario_candidates.json")
    scenario_sources = scenario_candidates.get("sources") if isinstance(scenario_candidates, dict) else []
    scenario_source_by_id = {
        str(row.get("source") or ""): row
        for row in scenario_sources or []
        if isinstance(row, dict)
    }
    incomplete_sources = []
    for model in _SCENARIO_SOURCE_IDS:
        applicability_row = applicability_by_id.get(model)
        source_row = scenario_source_by_id.get(model)
        if source_row is None:
            incomplete_sources.append(f"scenario_source:{model}:missing")
            continue
        if bool((applicability_row or {}).get("applicable")) and str(source_row.get("status") or "") != "expanded":
            incomplete_sources.append(f"scenario_source:{model}:{source_row.get('status') or 'empty'}")

    facts = dict(fact_verification or {})
    fact_total = max(0, int(facts.get("total") or 0))
    fact_verified = max(0, int(facts.get("verified") or 0))
    fact_contradicted = max(0, int(facts.get("contradicted") or 0))
    fact_insufficient = max(0, int(facts.get("insufficient") or 0))
    facts_checked = (
        fact_total > 0
        and str(facts.get("status") or "") != "not_checked"
        and (
            facts.get("behavior_validator_independent") is True
            or facts.get("behavior_validator_not_required") is True
        )
    )
    facts_score = round(fact_verified * 100 / fact_total) if facts_checked else None
    facts_status = (
        "not_checked"
        if not facts_checked
        else "blocked"
        if fact_contradicted or fact_insufficient or fact_verified < fact_total
        else "passed"
    )
    structure_issues = [
        *missing,
        *malformed,
        *empty_required,
        *[f"model_applicability:{model}:missing" for model in missing_models],
    ]
    coverage_issues = [
        *undisposed,
        *unresolved,
        *incomplete_sources,
        *orphan_cases,
        *high_risk_unmapped,
        *unknown_risks,
        *unresolved_evidence,
    ]
    executable_rows = artifacts.get("blackbox_control_observation.json")
    executable_rows = executable_rows.get("items") if isinstance(executable_rows, dict) else []
    unexecutable = [
        str(row.get("case_id") or row.get("id") or "unknown")
        for row in executable_rows or []
        if isinstance(row, dict) and str(row.get("status") or "") != "executable"
    ]
    if not executable_rows:
        unexecutable.append("blackbox_control_observation.json:empty")
    axes = {
        "structure": {
            "status": "blocked" if structure_issues else "passed",
            "score": max(0, 100 - 10 * len(structure_issues)),
            "issues": structure_issues,
        },
        "facts": {
            "status": facts_status,
            "score": facts_score,
            "total": fact_total,
            "verified": fact_verified,
            "contradicted": fact_contradicted,
            "insufficient": fact_insufficient,
        },
        "executability": {
            "status": "blocked" if unexecutable else "passed",
            "score": max(0, 100 - 10 * len(unexecutable)),
            "issues": unexecutable,
        },
        "coverage_disposition": {
            "status": "blocked" if coverage_issues else "passed",
            "score": max(0, 100 - 10 * len(coverage_issues)),
            "issues": coverage_issues,
        },
    }
    ready = all(axis["status"] == "passed" for axis in axes.values())
    status = "READY" if ready else "BLOCKED" if any(axis["status"] in {"blocked", "not_checked"} for axis in axes.values()) else "PARTIAL"
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "coverage_judge",
        "status": status,
        "ready": ready,
        "axes": axes,
        "blocking_reasons": [
            f"{name}:{axis['status']}"
            for name, axis in axes.items()
            if axis["status"] != "passed"
        ],
        "policy": {
            "facts_zero_is_ready": False,
            "silent_omission_allowed": False,
            "orphan_cases_allowed": False,
        },
    }


def refresh_source_driven_delivery_governance(
    artifact_dir: str | Path,
) -> dict[str, Any]:
    """Refresh the final judge after all independent validators have settled.

    The staged deterministic judge runs before the independent L2 behavior
    validator. Delivery decisions must therefore be rebuilt from the files on
    disk at the end of the lifecycle instead of trusting the earlier snapshot.
    """

    root = Path(artifact_dir)
    artifacts = {
        name: payload
        for name in SOURCE_DRIVEN_V2_ARTIFACTS
        if name != "judge_report.json"
        and (payload := _read_json_artifact(root / name)) is not None
    }
    fact_verification = _combined_final_fact_verification(root)
    judge = build_judge_report(
        artifacts=artifacts,
        fact_verification=fact_verification,
    )
    _write_json_artifact(root / "final_fact_verification.json", fact_verification)
    _write_json_artifact(root / "judge_report.json", judge)
    artifacts["judge_report.json"] = judge

    if (root / MINDMAP_ARTIFACTS[0]).is_file():
        mindmap = build_test_design_mindmap(artifacts)
        _write_json_artifact(root / MINDMAP_ARTIFACTS[0], mindmap)
        _atomic_write_text(
            root / MINDMAP_ARTIFACTS[1],
            render_test_design_mindmap_html(mindmap),
        )
        _atomic_write_text(
            root / MINDMAP_ARTIFACTS[2],
            render_test_design_mindmap_svg(mindmap),
        )
    return judge


def _combined_final_fact_verification(root: Path) -> dict[str, Any]:
    deterministic_claims: list[dict[str, Any]] = []
    deterministic = _read_json_artifact(root / "independent_fact_verification.json")
    if isinstance(deterministic, dict):
        for item in deterministic.get("claims") or []:
            if isinstance(item, dict):
                deterministic_claims.append(
                    {**item, "validation_layer": "L1_deterministic_binding"}
                )

    behavior = _read_json_artifact(root / "behavior_claim_validation.json")
    behavior_is_independent = bool(
        isinstance(behavior, dict)
        and str(behavior.get("status") or "") == "completed"
        and isinstance(behavior.get("validator"), dict)
        and behavior["validator"].get("independent") is True
    )
    behavior_not_required = bool(
        isinstance(behavior, dict)
        and str(behavior.get("status") or "") == "not_applicable"
        and deterministic_claims
        and all(
            str(item.get("type") or "") == "source_anchor"
            for item in deterministic_claims
        )
    )
    claims: list[dict[str, Any]] = []
    if behavior_is_independent:
        status_map = {
            "supports": "verified",
            "contradicts": "contradicted",
            "insufficient": "insufficient",
        }
        l2_by_identity: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for item in behavior.get("claims") or []:
            if not isinstance(item, dict):
                continue
            claim_id = str(item.get("claim_id") or "")
            if claim_id:
                identity = (claim_id, str(item.get("binding") or ""))
                l2_by_identity[identity].append(item)
        for item in deterministic_claims:
            claim_id = str(item.get("claim_id") or "")
            binding = str(item.get("binding") or "")
            l1_status = str(item.get("status") or "insufficient")
            claim_type = str(item.get("type") or "")
            if l1_status == "verified" and claim_type == "source_anchor":
                # A source anchor is a literal, SHA-bound local source quote.
                # It has no open-world behaviour to send to L2; requiring a
                # second verdict here converted valid provenance into a false
                # failure whenever the independent validator correctly chose
                # only behavioural claims.
                claims.append(
                    {
                        **item,
                        "binding_status": l1_status,
                        "behavior_status": "not_required",
                        "status": "verified",
                        "validation_layer": "L1_deterministic_source_anchor",
                    }
                )
                continue
            bucket = l2_by_identity.get((claim_id, binding), [])
            l2 = bucket.pop(0) if bucket else None
            if not bucket:
                l2_by_identity.pop((claim_id, binding), None)
            l2_status = (
                status_map.get(str(l2.get("status") or ""), "insufficient")
                if l2
                else "insufficient"
            )
            final_status = l1_status if l1_status != "verified" else l2_status
            claims.append(
                {
                    **item,
                    "binding_status": l1_status,
                    "behavior_status": l2_status,
                    "status": final_status,
                    "validation_layer": "L1_binding_and_L2_independent_behavior",
                    **({"behavior_verdict": l2} if l2 else {}),
                }
            )
        for bucket in l2_by_identity.values():
            for item in bucket:
                claims.append(
                    {
                        **item,
                        "status": status_map.get(
                            str(item.get("status") or ""), "insufficient"
                        ),
                        "validation_layer": "L2_independent_behavior",
                    }
                )
        if bool(behavior.get("truncated")):
            claims.append(
                {
                    "claim_id": "L2-TRUNCATED",
                    "status": "insufficient",
                    "validation_layer": "L2_independent_behavior",
                    "reason": (
                        f"独立行为核验仅覆盖 {int(behavior.get('requested_count') or 0)}/"
                        f"{int(behavior.get('candidate_count') or 0)} 条断言"
                    ),
                }
            )
    elif behavior_not_required:
        claims = [
            {
                **item,
                "binding_status": str(item.get("status") or "insufficient"),
                "behavior_status": "not_required",
                "status": str(item.get("status") or "insufficient"),
                "validation_layer": "L1_deterministic_source_anchor",
            }
            for item in deterministic_claims
        ]
    else:
        claims = [
            {
                **item,
                "binding_status": str(item.get("status") or "insufficient"),
                "status": "insufficient",
            }
            for item in deterministic_claims
        ]

    total = len(claims)
    verified = sum(item.get("status") == "verified" for item in claims)
    contradicted = sum(item.get("status") == "contradicted" for item in claims)
    insufficient = sum(item.get("status") == "insufficient" for item in claims)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "final_fact_verification",
        "status": (
            "not_checked"
            if not total
            else "passed"
            if (behavior_is_independent or behavior_not_required) and verified == total
            else "blocked"
        ),
        "total": total,
        "verified": verified,
        "contradicted": contradicted,
        "insufficient": insufficient,
        "pass_rate": round(verified * 100 / total) if total else None,
        "behavior_validator_independent": behavior_is_independent,
        "behavior_validator_not_required": behavior_not_required,
        "claims": claims,
    }


def _read_json_artifact(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json_artifact(path: Path, payload: Any) -> None:
    _atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
    )


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = handle.name
        os.replace(temporary_path, path)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)


def verify_technical_claims(
    *,
    source_pack: dict[str, Any],
    sfmea: list[dict[str, Any]],
    black_box_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    """Verify explicit claims against the exact local evidence excerpt.

    This is the deterministic L1 judge. It is deliberately independent from
    the generation prompt and fails closed when an evidence card is absent.
    """

    from app.services.test_activity_contract import _behavior_claim_binding

    evidence = {
        str(item.get("evidence_id") or ""): item
        for item in source_pack.get("evidence_cards") or []
        if isinstance(item, dict) and str(item.get("evidence_id") or "")
    }
    claims: list[dict[str, Any]] = []
    for artifact, rows in (("sfmea.json", sfmea), ("black_box_cases.json", black_box_cases)):
        for row_index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            for claim_index, claim in enumerate(row.get("technical_claims") or []):
                if not isinstance(claim, dict):
                    continue
                refs = [item for item in claim.get("evidence") or [] if isinstance(item, dict)]
                statuses: list[str] = []
                checks = []
                binding_evidence: list[dict[str, Any]] = []
                for ref in refs:
                    evidence_id = str(ref.get("evidence_id") or "")
                    card = _resolve_claim_evidence_card(
                        evidence=evidence,
                        evidence_id=evidence_id,
                        reference=ref,
                    )
                    expected_path = str(ref.get("path") or "")
                    quote = str(ref.get("quote") or "")
                    expected_symbol = str(ref.get("symbol") or "")
                    if card:
                        binding_evidence.append(
                            {
                                **ref,
                                "sha256": str(card.get("sha256") or ""),
                            }
                        )
                    if not card:
                        ref_status = "insufficient"
                    elif expected_path and expected_path != str(card.get("file_path") or ""):
                        ref_status = "contradicted"
                    elif not quote or quote not in str(card.get("excerpt") or ""):
                        ref_status = "contradicted"
                    elif expected_symbol and expected_symbol not in _strings(card.get("symbols")):
                        ref_status = "contradicted"
                    elif not _claim_statement_supported_by_quote(
                        statement=str(claim.get("statement") or ""),
                        quote=quote,
                        claim_type=str(claim.get("type") or "source_behavior"),
                    ):
                        ref_status = "insufficient"
                    else:
                        ref_status = "verified"
                    statuses.append(ref_status)
                    checks.append(
                        {
                            "evidence_id": evidence_id,
                            "path": expected_path,
                            "quote_sha256": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
                            "status": ref_status,
                        }
                    )
                status = (
                    "insufficient"
                    if not refs or "insufficient" in statuses
                    else "contradicted"
                    if "contradicted" in statuses
                    else "verified"
                )
                claim_id = str(
                    claim.get("claim_id") or f"{artifact}:{row_index}:{claim_index}"
                )
                claim_type = str(claim.get("type") or "source_behavior")
                statement = str(claim.get("statement") or "")
                claims.append(
                    {
                        "claim_id": claim_id,
                        "type": claim_type,
                        "statement": statement,
                        "binding": _behavior_claim_binding(
                            claim_id=claim_id,
                            claim_type=claim_type,
                            statement=statement,
                            evidence=binding_evidence,
                        ),
                        "artifact": artifact,
                        "row_index": row_index,
                        "status": status,
                        "evidence_checks": checks,
                    }
                )
    total = len(claims)
    verified = sum(item["status"] == "verified" for item in claims)
    contradicted = sum(item["status"] == "contradicted" for item in claims)
    insufficient = sum(item["status"] == "insufficient" for item in claims)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "independent_fact_verification",
        "status": "not_checked" if not total else "passed" if verified == total else "blocked",
        "total": total,
        "verified": verified,
        "contradicted": contradicted,
        "insufficient": insufficient,
        "pass_rate": round(verified * 100 / total) if total else None,
        "claims": claims,
    }


def _resolve_claim_evidence_card(
    *,
    evidence: dict[str, dict[str, Any]],
    evidence_id: str,
    reference: dict[str, Any],
) -> dict[str, Any] | None:
    """Resolve `CARD:L123` to its source card without weakening line binding.

    Generated artifacts retain the exact line as part of their reference ID,
    while deterministic evidence cards intentionally use a stable card ID.  A
    suffix is valid only when the referenced line is inside the card's verified
    range; the original reference ID is preserved for the L2 binding hash.
    """
    direct = evidence.get(evidence_id)
    if direct is not None:
        return direct
    match = re.fullmatch(r"(.+):L(\d+)", evidence_id)
    if not match:
        return None
    card = evidence.get(match.group(1))
    if card is None:
        return None
    line = int(match.group(2))
    start = _evidence_reference_line(str(card.get("start_line") or ""))
    end = _evidence_reference_line(str(card.get("end_line") or ""))
    declared = _evidence_reference_line(str(reference.get("lines") or ""))
    if declared and declared != line:
        return None
    if not start or not end or not (start <= line <= end):
        return None
    return card


def _evidence_reference_line(value: str) -> int:
    match = re.search(r"\d+", value)
    return int(match.group(0)) if match else 0


def _claim_statement_supported_by_quote(
    *,
    statement: str,
    quote: str,
    claim_type: str,
) -> bool:
    statement_tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|0x[0-9A-Fa-f]+|\d+", statement)
    }
    quote_tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|0x[0-9A-Fa-f]+|\d+", quote)
    }
    literal_tokens = {
        token.lower()
        for token in re.findall(r"0x[0-9A-Fa-f]+|\b\d+\b|\b[A-Z][A-Z0-9_]{2,}\b", statement)
    }
    if claim_type in {"protocol_constant", "field_offset", "macro_value"}:
        return bool(literal_tokens) and literal_tokens.issubset(quote_tokens)
    return bool(statement_tokens & quote_tokens)
def build_test_design_mindmap(artifacts: dict[str, Any]) -> dict[str, Any]:
    """Build a stable overview plus flow/resource drill-down graph."""

    judge = artifacts.get("judge_report.json")
    judge = judge if isinstance(judge, dict) else {"status": "BLOCKED", "ready": False}
    nodes: list[dict[str, Any]] = []

    def add(
        node_id: str,
        node_type: str,
        title: str,
        summary: str,
        *,
        parent_id: str | None,
        priority: str = "P2",
        status: str = "READY",
        evidence_refs: Iterable[str] = (),
        trace_refs: dict[str, list[str]] | None = None,
    ) -> None:
        nodes.append(
            {
                "id": node_id,
                "type": node_type,
                "title": _plain_text(title, 180),
                "summary": _plain_text(summary, 1000),
                "priority": priority,
                "status": status,
                "parent_id": parent_id,
                "children": [],
                "evidence_refs": _dedupe(evidence_refs),
                "trace_refs": trace_refs or {},
            }
        )

    overall_status = str(judge.get("status") or "BLOCKED")

    def governed_status(status: Any, *, fact_sensitive: bool = True) -> str:
        value = str(status or "PARTIAL").upper()
        if value == "BLOCKED":
            return value
        if fact_sensitive and overall_status != "READY" and value == "READY":
            return "PARTIAL"
        return value

    add(
        "overview",
        "overview",
        "测试设计概览",
        "源码证据驱动的流程、风险、资源与黑盒测试追溯图",
        parent_id=None,
        priority="P0",
        status=overall_status,
    )
    section_specs = (
        ("scope", "scope", "范围与证据", "已验证范围、输入材料与证据缺口"),
        ("entrypoints", "entrypoint", "外部入口", "可由外部请求、协议或配置触发的入口"),
        ("flows", "flow", "P0/P1 流程", "正常、异常、恢复与并发相关流程"),
        ("states", "state", "状态机", "状态对象和已发现转换"),
        ("resources", "resource", "共享资源", "容量、生命周期、不变量和恢复重申请"),
        ("risks", "risk", "高风险 SFMEA", "高 RPN 风险及其测试映射"),
        ("scenarios", "scenario", "黑盒场景", "外部控制、观测与诊断"),
        ("gaps", "gap", "缺口与阻塞", "证据不足、未处置项和质量门禁"),
    )
    for node_id, node_type, title, summary in section_specs:
        fact_sensitive = node_id not in {"scope", "entrypoints"}
        add(
            node_id,
            node_type,
            title,
            summary,
            parent_id="overview",
            priority="P1",
            status=(
                overall_status
                if node_id == "gaps"
                else governed_status("READY", fact_sensitive=fact_sensitive)
            ),
        )

    entrypoints = _artifact_items(artifacts, "entrypoints.json")
    for index, item in enumerate(entrypoints[:40], 1):
        evidence = _strings(item.get("evidence_refs"))
        add(
            f"entrypoint:{_stable_fragment(item.get('id') or index)}",
            "entrypoint",
            str(item.get("symbol") or item.get("title") or f"入口 {index}"),
            str(item.get("file_path") or ""),
            parent_id="entrypoints",
            priority=str(item.get("priority") or "P1"),
            status=str(item.get("status") or "READY"),
            evidence_refs=evidence,
            trace_refs={"flow_ids": _strings(item.get("flow_ids"))},
        )

    flows = _artifact_items(artifacts, "flow_cards.json")
    for index, item in enumerate(flows[:40], 1):
        flow_id = str(item.get("flow_id") or item.get("id") or f"flow-{index}")
        parent = f"flow:{_stable_fragment(flow_id)}"
        add(
            parent,
            "flow",
            str(item.get("title") or item.get("name") or flow_id),
            str(item.get("purpose") or item.get("summary") or ""),
            parent_id="flows",
            priority=str(item.get("priority") or "P1"),
            status=governed_status(item.get("status") or "PARTIAL"),
            evidence_refs=_strings(item.get("evidence_refs")),
            trace_refs={
                "flow_ids": [flow_id],
                "sfmea_ids": _strings(item.get("sfmea_ids")),
                "case_ids": _strings(item.get("case_ids")),
            },
        )
        drilldown = (
            ("trigger", "触发与用途", item.get("trigger")),
            ("normal", "正常路径", item.get("normal_path")),
            ("abnormal", "异常分支", item.get("abnormal_paths")),
            ("state", "状态变化", item.get("state_refs")),
            ("resource", "资源与边界", item.get("resource_refs")),
            ("concurrency", "并发窗口", item.get("concurrency_windows")),
            ("propagation", "异常传播", item.get("error_chain_refs")),
            ("observation", "外部观测", item.get("external_observations")),
        )
        for suffix, title, value in drilldown:
            values = _strings(value)
            if values:
                add(
                    f"{parent}:{suffix}",
                    suffix,
                    title,
                    "；".join(values),
                    parent_id=parent,
                    priority="P2",
                    status=governed_status("READY"),
                    trace_refs={"flow_ids": [flow_id]},
                )

    resources = _artifact_items(artifacts, "resource_lifecycle_disposition.json")
    for index, item in enumerate(resources[:50], 1):
        resource_id = str(item.get("resource_id") or item.get("id") or f"resource-{index}")
        summary = "；".join(
            _strings(
                [
                    item.get("allocation"),
                    item.get("ownership"),
                    item.get("normal_release"),
                    item.get("abnormal_release"),
                    item.get("timeout_release"),
                    item.get("exhaustion"),
                    item.get("reacquire_after_recovery"),
                    item.get("invariant"),
                    item.get("boundary_values"),
                ]
            )
        )
        add(
            f"resource:{_stable_fragment(resource_id)}",
            "resource",
            str(item.get("name") or item.get("kind") or resource_id),
            summary,
            parent_id="resources",
            priority=str(item.get("priority") or "P1"),
            status=governed_status(
                "READY"
                if str(item.get("disposition")) in {"retain", "covered_by_other", "merge_into"}
                else "PARTIAL"
            ),
            evidence_refs=_strings(item.get("evidence_refs")),
            trace_refs={"resource_ids": [resource_id], "case_ids": _strings(item.get("case_ids"))},
        )

    for index, item in enumerate(_artifact_items(artifacts, "risk_register.json")[:40], 1):
        risk_id = str(item.get("risk_id") or item.get("sfmea_id") or f"risk-{index}")
        add(
            f"risk:{_stable_fragment(risk_id)}",
            "risk",
            str(item.get("failure_mode") or risk_id),
            str(item.get("final_effect") or item.get("effect") or ""),
            parent_id="risks",
            priority="P0" if int(item.get("rpn") or 0) >= 200 else "P1",
            status=governed_status(
                "READY" if _strings(item.get("test_case_ids")) else "BLOCKED"
            ),
            evidence_refs=_strings(item.get("evidence_refs")),
            trace_refs={"sfmea_ids": [risk_id], "case_ids": _strings(item.get("test_case_ids"))},
        )

    for index, item in enumerate(_artifact_items(artifacts, "test_scenarios.json")[:80], 1):
        scenario_id = str(item.get("scenario_id") or f"scenario-{index}")
        add(
            f"scenario:{_stable_fragment(scenario_id)}",
            "scenario",
            str(item.get("title") or scenario_id),
            str(item.get("expected_result") or ""),
            parent_id="scenarios",
            priority=str(item.get("priority") or "P2"),
            status=governed_status(item.get("status") or "READY"),
            evidence_refs=_strings(item.get("evidence_refs")),
            trace_refs={"case_ids": _strings(item.get("case_ids")), "sfmea_ids": _strings(item.get("risk_ids"))},
        )

    blockers = _strings(judge.get("blocking_reasons"))
    if not blockers:
        blockers = ["没有阻塞项"]
    for index, reason in enumerate(blockers, 1):
        add(
            f"gap:{index:03d}",
            "gap",
            f"门禁 {index}",
            reason,
            parent_id="gaps",
            priority="P0",
            status="BLOCKED" if overall_status != "READY" else "READY",
        )

    children: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        if node["parent_id"]:
            children[str(node["parent_id"])].append(str(node["id"]))
    for node in nodes:
        node["children"] = children.get(str(node["id"]), [])
    document = {
        "schema_version": MINDMAP_SCHEMA_VERSION,
        "status": overall_status,
        "default_expand_depth": 2,
        "root_id": "overview",
        "nodes": nodes,
        "source_artifact": "judge_report.json",
    }
    canonical = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    document["generation_id"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return document


def render_test_design_mindmap_html(mindmap: dict[str, Any]) -> str:
    """Render a self-contained, offline, bounded interactive viewer."""

    payload = _json_for_html(_sanitize_embedded_payload(mindmap))
    generation_id = html.escape(str(mindmap.get("generation_id") or "missing"))
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>测试设计脑图</title><style>
:root{{--bg:#f5f7f9;--panel:#fff;--ink:#17212b;--muted:#687583;--line:#d9e0e7;--accent:#087f8c;--bad:#b42318}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
header{{position:sticky;top:0;z-index:2;display:flex;gap:12px;align-items:center;padding:12px 16px;background:var(--panel);border-bottom:1px solid var(--line)}}
header strong{{font-size:16px}}header input,header select{{height:34px;border:1px solid var(--line);background:#fff;padding:0 9px}}
header input{{min-width:240px}}main{{display:grid;grid-template-columns:minmax(460px,1fr) 340px;gap:12px;padding:12px;height:calc(100vh - 60px)}}
#tree{{overflow:auto;background:var(--panel);border:1px solid var(--line);padding:12px}}#detail{{overflow:auto;background:var(--panel);border:1px solid var(--line);padding:16px}}
.node{{margin:5px 0 5px calc(var(--depth)*18px);border-left:3px solid var(--accent);background:#f9fbfc;padding:7px 9px;cursor:pointer;max-width:900px}}
.node[data-status="BLOCKED"]{{border-color:var(--bad)}}.node b{{display:block}}.node small{{color:var(--muted)}}.hidden{{display:none}}button{{border:1px solid var(--line);background:#fff;height:34px;padding:0 10px;cursor:pointer}}
dl{{margin:0}}dt{{margin-top:14px;color:var(--muted)}}dd{{margin:3px 0;white-space:pre-wrap;overflow-wrap:anywhere}}@media(max-width:800px){{main{{grid-template-columns:1fr;height:auto}}#tree,#detail{{max-height:70vh}}header{{flex-wrap:wrap}}}}
</style></head><body data-mindmap-root data-generation="{generation_id}">
<header><strong>测试设计脑图</strong><input id="search" aria-label="搜索节点" placeholder="搜索节点"><select id="priority" aria-label="按优先级筛选"><option value="">全部优先级</option><option>P0</option><option>P1</option><option>P2</option></select><select id="type" aria-label="按节点类型筛选"><option value="">全部类型</option></select><select id="status" aria-label="按状态筛选"><option value="">全部状态</option><option>READY</option><option>PARTIAL</option><option>BLOCKED</option></select><button id="expand">展开全部</button><button id="collapse">折叠到两层</button></header>
<main><section id="tree" aria-label="脑图节点"></section><aside id="detail"><h2>节点详情</h2><p>点击左侧节点查看证据、SFMEA 与测试用例追溯。</p></aside></main>
<script type="application/json" id="mindmap-data">{payload}</script>
<script>(()=>{{const data=JSON.parse(document.getElementById('mindmap-data').textContent);const byId=new Map(data.nodes.map(n=>[n.id,n]));const tree=document.getElementById('tree');const detail=document.getElementById('detail');let maxDepth=data.default_expand_depth||2;const depth=id=>{{let d=0,n=byId.get(id);while(n&&n.parent_id){{d++;n=byId.get(n.parent_id)}}return d}};const esc=v=>String(v??'');const render=()=>{{const q=document.getElementById('search').value.toLowerCase();const p=document.getElementById('priority').value,t=document.getElementById('type').value,s=document.getElementById('status').value;tree.textContent='';data.nodes.forEach(n=>{{const d=depth(n.id);const match=(!q||(n.title+' '+n.summary).toLowerCase().includes(q))&&(!p||n.priority===p)&&(!t||n.type===t)&&(!s||n.status===s);if(!match||d>maxDepth)return;const el=document.createElement('article');el.className='node';el.style.setProperty('--depth',d);el.dataset.status=n.status;const b=document.createElement('b');b.textContent=n.title;const sm=document.createElement('small');sm.textContent=n.type+' · '+n.priority+' · '+n.status;el.append(b,sm);el.onclick=()=>{{detail.textContent='';const h=document.createElement('h2');h.textContent=n.title;detail.append(h);[['摘要',n.summary],['证据',n.evidence_refs],['追溯',JSON.stringify(n.trace_refs,null,2)]].forEach(([k,v])=>{{const dt=document.createElement('dt');dt.textContent=k;const dd=document.createElement('dd');dd.textContent=Array.isArray(v)?v.join('\n'):esc(v);const dl=document.createElement('dl');dl.append(dt,dd);detail.append(dl)}})}};tree.append(el)}})}};const types=[...new Set(data.nodes.map(n=>n.type))].sort();types.forEach(v=>{{const o=document.createElement('option');o.value=o.textContent=v;document.getElementById('type').append(o)}});['search','priority','type','status'].forEach(id=>document.getElementById(id).addEventListener(id==='search'?'input':'change',render));document.getElementById('expand').onclick=()=>{{maxDepth=99;render()}};document.getElementById('collapse').onclick=()=>{{maxDepth=data.default_expand_depth||2;render()}};render()}})();</script></body></html>"""


def render_test_design_mindmap_svg(mindmap: dict[str, Any]) -> str:
    """Render a deterministic review-friendly overview SVG."""

    nodes = [item for item in mindmap.get("nodes") or [] if isinstance(item, dict)]
    by_id = {str(item.get("id")): item for item in nodes}
    visible = nodes
    row_height = 46
    width = 1280
    height = max(180, 70 + row_height * len(visible))
    rows: list[str] = []
    for index, node in enumerate(visible):
        depth = _node_depth(node, by_id)
        x = 28 + depth * 68
        y = 52 + index * row_height
        node_width = max(260, width - x - 32)
        status = str(node.get("status") or "PARTIAL")
        stroke = "#b42318" if status == "BLOCKED" else "#087f8c" if status == "READY" else "#b7791f"
        title = html.escape(_plain_text(node.get("title"), 110))
        meta = html.escape(f"{node.get('type')} · {node.get('priority')} · {status}")
        rows.append(
            f'<g data-node-id="{html.escape(str(node.get("id") or ""))}">'
            f'<rect x="{x}" y="{y}" width="{node_width}" height="36" rx="4" fill="#ffffff" stroke="{stroke}"/>'
            f'<text x="{x + 12}" y="{y + 16}" font-size="13" font-weight="600" fill="#17212b">{title}</text>'
            f'<text x="{x + 12}" y="{y + 30}" font-size="10" fill="#687583">{meta}</text></g>'
        )
    generation_id = html.escape(str(mindmap.get("generation_id") or "missing"))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="测试设计脑图" data-schema="{MINDMAP_SCHEMA_VERSION}" data-generation="{generation_id}">'
        '<rect width="100%" height="100%" fill="#f5f7f9"/>'
        '<text x="28" y="30" font-family="system-ui,sans-serif" font-size="18" font-weight="700" fill="#17212b">测试设计脑图</text>'
        f'<g font-family="system-ui,sans-serif">{"".join(rows)}</g></svg>'
    )


def materialize_source_driven_artifacts(
    *,
    artifact_dir: Any,
    artifacts: dict[str, Any],
    include_mindmap: bool,
) -> list[str]:
    """Write governed artifacts atomically enough for the staged executor."""

    from pathlib import Path

    root = Path(artifact_dir)
    root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for name, payload in artifacts.items():
        path = root / name
        _write_json_artifact(path, payload)
        written.append(name)
    if include_mindmap:
        mindmap = build_test_design_mindmap(artifacts)
        _write_json_artifact(root / MINDMAP_ARTIFACTS[0], mindmap)
        _atomic_write_text(root / MINDMAP_ARTIFACTS[1], render_test_design_mindmap_html(mindmap))
        _atomic_write_text(root / MINDMAP_ARTIFACTS[2], render_test_design_mindmap_svg(mindmap))
        written.extend(MINDMAP_ARTIFACTS)
    return written


def _entrypoints_artifact(source_pack: dict[str, Any], flow_pack: dict[str, Any]) -> dict[str, Any]:
    items = []
    for index, row in enumerate(flow_pack.get("entry_points") or [], 1):
        if not isinstance(row, dict):
            continue
        items.append(
            {
                "id": str(row.get("evidence_id") or f"ENTRY-{index:03d}"),
                "symbol": str(row.get("symbol") or ""),
                "file_path": str(row.get("file_path") or ""),
                "start_line": int(row.get("start_line") or 0),
                "end_line": int(row.get("end_line") or 0),
                "trigger": "external_or_upstream_call",
                "priority": "P1",
                "status": "READY" if row.get("symbol") and row.get("file_path") else "PARTIAL",
                "evidence_refs": [str(row.get("evidence_id") or "")],
                "flow_ids": [],
            }
        )
    return _ledger("entrypoints", items, gaps=flow_pack.get("evidence_gaps"))


def _flows_artifact(flow_outline: dict[str, Any]) -> dict[str, Any]:
    items = []
    for index, row in enumerate(flow_outline.get("main_flows") or [], 1):
        if not isinstance(row, dict):
            continue
        steps = [dict(item) for item in row.get("steps") or [] if isinstance(item, dict)]
        refs = _dedupe(ref for step in steps for ref in _strings(step.get("evidence_ids")))
        items.append(
            {
                "id": str(row.get("id") or f"FLOW-{index:03d}"),
                "name": str(row.get("name") or row.get("root_symbol") or f"流程 {index}"),
                "root_symbol": str(row.get("root_symbol") or ""),
                "steps": steps,
                "priority": "P0" if index == 1 else "P1",
                "status": "READY" if steps and refs else "PARTIAL",
                "evidence_refs": refs,
            }
        )
    return _ledger("flows", items, gaps=flow_outline.get("evidence_gaps"))


def _states_artifact(flow_pack: dict[str, Any]) -> dict[str, Any]:
    transitions = [dict(item) for item in flow_pack.get("state_transitions") or [] if isinstance(item, dict)]
    items = []
    for index, row in enumerate(flow_pack.get("state_objects") or [], 1):
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "")
        linked = [item for item in transitions if symbol and symbol.lower() in json.dumps(item, ensure_ascii=False).lower()]
        items.append(
            {
                "id": str(row.get("evidence_id") or f"STATE-{index:03d}"),
                "name": symbol or f"状态对象 {index}",
                "file_path": str(row.get("file_path") or ""),
                "transitions": linked,
                "evidence_refs": _dedupe([row.get("evidence_id"), *[item.get("evidence_id") for item in linked]]),
                "status": "READY" if linked else "PARTIAL",
            }
        )
    return _ledger("states", items, gaps=[] if transitions else ["未发现有源码证据的状态转换"])


def _resources_artifact(source_pack: dict[str, Any], flow_pack: dict[str, Any]) -> dict[str, Any]:
    matched_rows: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)
    rows = [item for item in source_pack.get("evidence_cards") or [] if isinstance(item, dict)]
    rows.extend(item for key in ("cleanup_paths", "error_paths", "recovery_paths") for item in flow_pack.get(key) or [] if isinstance(item, dict))
    for row in rows:
        text = " ".join(
            [
                str(row.get("symbol") or ""),
                " ".join(_strings(row.get("symbols"))),
                " ".join(_strings(row.get("matched_terms"))),
                str(row.get("text") or ""),
                str(row.get("excerpt") or ""),
            ]
        )
        for kind, pattern in _RESOURCE_PATTERNS:
            if not pattern.search(text):
                continue
            matched_rows[kind].append((row, text))

    candidates: list[dict[str, Any]] = []
    for kind, matches in matched_rows.items():
        combined_text = "\n".join(text for _, text in matches)
        if not _RESOURCE_LIFECYCLE_RE.search(combined_text):
            continue
        capacity_supported = any(
            _RESOURCE_LIFECYCLE_RE.search(text) and _RESOURCE_CAPACITY_RE.search(text)
            for _, text in matches
        )
        wrap_supported = any(
            _RESOURCE_LIFECYCLE_RE.search(text) and _RESOURCE_WRAP_RE.search(text)
            for _, text in matches
        )
        candidates.append(
            {
                "id": f"RESOURCE-{kind.upper().replace('_', '-')}",
                "kind": kind,
                "name": _resource_label(kind),
                "evidence_refs": _dedupe(row.get("evidence_id") for row, _ in matches),
                "source_paths": _dedupe(row.get("file_path") for row, _ in matches),
                "capacity_model_applicable": (
                    kind in _FINITE_RESOURCE_KINDS
                    and capacity_supported
                ),
                "wraparound_applicable": wrap_supported,
            }
        )
    return _ledger("resources", candidates, gaps=[] if candidates else ["未从有界源码证据识别出资源对象"])


def _model_applicability_artifact(*, flow_pack: dict[str, Any], states: dict[str, Any], resources: dict[str, Any], source_pack: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps({"source": source_pack, "flow": flow_pack}, ensure_ascii=False).lower()
    materials, coverage, history = _source_input_materials(source_pack)
    finite_resource_found = any(
        bool(item.get("capacity_model_applicable"))
        for item in resources.get("items") or []
        if isinstance(item, dict)
    )
    checks = [
        ("branch", bool(flow_pack.get("conditions")), "存在条件分支证据"),
        ("state", bool(states.get("items")), "存在状态对象或转换证据"),
        ("resource", bool(resources.get("items")), "存在资源申请/释放相关证据"),
        (
            "numeric_boundary_and_wrap",
            finite_resource_found
            or any(
                bool(item.get("wraparound_applicable"))
                for item in resources.get("items") or []
                if isinstance(item, dict)
            ),
            "存在有限资源、容量、计数、Tag、Generation 或位图机制",
        ),
        ("concurrency", bool(_CONCURRENCY_RE.search(text)), "存在并发同步或调度机制"),
        ("error_propagation", bool(flow_pack.get("error_paths")), "存在异常路径证据"),
        ("protocol_requirement_security_configuration", bool(_PROTOCOL_RE.search(text)), "存在协议、认证或配置机制"),
        ("coverage_history", bool(coverage or history), "存在覆盖率或历史证据"),
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "model_applicability",
        "items": [
            {
                "model": name,
                "applicable": applicable,
                "status": "applicable" if applicable else "not_applicable",
                "reason": reason if applicable else f"当前证据未显示：{reason}",
            }
            for name, applicable, reason in checks
        ],
    }


def _flow_cards_artifact(*, flows: dict[str, Any], flow_outline: dict[str, Any], flow_pack: dict[str, Any]) -> dict[str, Any]:
    branches = [dict(item) for item in flow_pack.get("conditions") or [] if isinstance(item, dict)]
    errors = [dict(item) for item in flow_pack.get("error_paths") or [] if isinstance(item, dict)]
    items = []
    for row in flows.get("items") or []:
        if not isinstance(row, dict):
            continue
        flow_id = str(row.get("id") or "")
        steps = [dict(item) for item in row.get("steps") or [] if isinstance(item, dict)]
        flow_evidence = set(_strings(row.get("evidence_refs")))
        flow_symbols = {
            str(value)
            for value in [row.get("root_symbol")]
            if str(value or "")
        }
        for step in steps:
            flow_evidence.update(_strings(step.get("evidence_ids")))
            flow_symbols.update(
                str(value)
                for value in (step.get("from_symbol"), step.get("to_symbol"))
                if str(value or "")
            )
        related_branches = [
            item for item in branches
            if _row_related_to_flow(item, flow_evidence, flow_symbols)
        ]
        related_errors = [
            item for item in errors
            if _row_related_to_flow(item, flow_evidence, flow_symbols)
        ]
        related_states = [
            item for item in flow_pack.get("state_transitions") or []
            if isinstance(item, dict)
            and _row_related_to_flow(item, flow_evidence, flow_symbols)
        ]
        related_cleanups = [
            item for item in flow_pack.get("cleanup_paths") or []
            if isinstance(item, dict)
            and _row_related_to_flow(item, flow_evidence, flow_symbols)
        ]
        items.append(
            {
                "flow_id": flow_id,
                "title": str(row.get("name") or flow_id),
                "purpose": "向黑盒测试人员说明该入口到响应/状态更新的已验证实现路径",
                "trigger": str(row.get("root_symbol") or "外部请求"),
                "normal_path": [str(item.get("action") or "") for item in steps],
                "abnormal_paths": [str(item.get("text") or "") for item in related_errors[:12]],
                "branch_refs": _strings(item.get("evidence_id") for item in related_branches[:20]),
                "state_refs": _strings(item.get("evidence_id") for item in related_states[:20]),
                "resource_refs": _strings(item.get("evidence_id") for item in related_cleanups[:20]),
                "boundary_and_wrap": ["仅对 model_applicability.json 判定适用的容量、计数、Tag/Generation 机制展开"],
                "concurrency_windows": ["根据线程、锁、poller 或共享对象证据确定；证据不足时 need_verify"],
                "error_chain_refs": _strings(item.get("evidence_id") for item in related_errors[:20]),
                "external_observations": ["协议响应/返回码", "连接或会话状态", "日志与指标", "恢复后重新请求结果"],
                "evidence_refs": _strings(row.get("evidence_refs")),
                "priority": str(row.get("priority") or "P1"),
                "status": str(row.get("status") or "PARTIAL"),
                "sfmea_ids": [],
                "case_ids": [],
            }
        )
    return _ledger("flow_cards", items, gaps=flow_outline.get("evidence_gaps"))


def _row_related_to_flow(
    row: dict[str, Any],
    flow_evidence: set[str],
    flow_symbols: set[str],
) -> bool:
    row_refs = set(
        _strings(
            [row.get("id"), row.get("evidence_id"), *(_strings(row.get("evidence_refs")))]
        )
    )
    if row_refs & flow_evidence:
        return True
    row_symbols = {
        str(value)
        for value in (
            row.get("symbol"),
            row.get("from_symbol"),
            row.get("to_symbol"),
        )
        if str(value or "")
    }
    return bool(row_symbols & flow_symbols)


def _branch_disposition_artifact(*, flow_pack: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
    items = []
    for index, row in enumerate(flow_pack.get("conditions") or [], 1):
        if not isinstance(row, dict):
            continue
        evidence_id = str(row.get("evidence_id") or f"BRANCH-{index:03d}")
        case_ids = _mapped_case_ids(cases, [evidence_id])
        items.append(
            {
                "id": evidence_id,
                "condition": str(row.get("text") or ""),
                "disposition": "retain" if case_ids else "need_verify",
                "reason": "已有黑盒用例映射" if case_ids else "已发现测试相关分支，但尚缺独立场景映射",
                "covered_by": case_ids,
                "evidence_refs": [evidence_id],
            }
        )
    return _ledger("branch_disposition", items, gaps=[])


def _state_disposition_artifact(*, states: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
    items = []
    for row in states.get("items") or []:
        if not isinstance(row, dict):
            continue
        evidence = _strings(row.get("evidence_refs"))
        case_ids = _mapped_case_ids(cases, evidence)
        items.append(
            {
                "id": str(row.get("id") or ""),
                "state": str(row.get("name") or ""),
                "transitions": list(row.get("transitions") or []),
                "disposition": "retain" if case_ids else "need_verify",
                "reason": "已有状态转换场景" if case_ids else "状态对象已识别，转换覆盖需补充",
                "covered_by": case_ids,
                "evidence_refs": evidence,
            }
        )
    return _ledger("state_transition_disposition", items, gaps=[])


def _resource_disposition_artifact(*, resources: dict[str, Any], flow_pack: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
    cleanup_text = " ".join(str(item.get("text") or "") for item in flow_pack.get("cleanup_paths") or [] if isinstance(item, dict))
    error_text = " ".join(str(item.get("text") or "") for item in flow_pack.get("error_paths") or [] if isinstance(item, dict))
    recovery_text = " ".join(str(item.get("text") or "") for item in flow_pack.get("recovery_paths") or [] if isinstance(item, dict))
    items = []
    for row in resources.get("items") or []:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "resource")
        evidence = _strings(row.get("evidence_refs"))
        case_ids = _mapped_case_ids(cases, evidence)
        capacity = bool(row.get("capacity_model_applicable"))
        wrap = bool(row.get("wraparound_applicable"))
        items.append(
            {
                "id": str(row.get("id") or f"RESOURCE-{kind}"),
                "resource_id": str(row.get("id") or f"RESOURCE-{kind}"),
                "kind": kind,
                "name": str(row.get("name") or _resource_label(kind)),
                "allocation": "从已验证 get/alloc/create/open 路径确认；未定位时 need_verify",
                "ownership": "由连接、会话、请求或模块持有；具体字段须由证据回链确认",
                "normal_release": cleanup_text[:1000] or "need_verify",
                "abnormal_release": cleanup_text[:1000] if error_text else "need_verify",
                "timeout_release": cleanup_text[:1000] if re.search(r"timeout|timer", error_text, re.I) else "need_verify",
                "exhaustion": "达到有限资源容量 N 后申请失败必须可观测" if capacity else "not_applicable",
                "reacquire_after_recovery": recovery_text[:1000] or "need_verify",
                "invariant": "allocated = active + pending；释放后可用量恢复，Bitmap/计数不得漂移",
                "boundary_values": (
                    ["0", "1", "N-1", "N", "N+1", "2N-1", "2N", "2N+1"]
                    if capacity
                    else ["not_applicable"]
                ),
                "wraparound": "类型上限、回绕、Tag/Generation 重用" if wrap else "not_applicable",
                "capacity_model_applicable": capacity,
                "wraparound_applicable": wrap,
                "disposition": "retain" if case_ids else "need_verify",
                "reason": "已有资源场景映射" if case_ids else "资源机制已识别，生命周期闭合与测试映射待核验",
                "case_ids": case_ids,
                "evidence_refs": evidence,
                "priority": "P1",
            }
        )
    return _ledger("resource_lifecycle_disposition", items, gaps=[])


def _error_propagation_artifact(flow_pack: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
    items = []
    cleanups = _strings(item.get("evidence_id") for item in flow_pack.get("cleanup_paths") or [] if isinstance(item, dict))
    recoveries = _strings(item.get("evidence_id") for item in flow_pack.get("recovery_paths") or [] if isinstance(item, dict))
    for index, row in enumerate(flow_pack.get("error_paths") or [], 1):
        if not isinstance(row, dict):
            continue
        evidence_id = str(row.get("evidence_id") or f"ERROR-{index:03d}")
        items.append(
            {
                "id": f"ERROR-CHAIN-{index:03d}",
                "origin": str(row.get("symbol") or row.get("file_path") or "upstream"),
                "trigger": str(row.get("text") or "异常条件"),
                "upstream_effect": "输入、协议或依赖层异常",
                "local_effect": "本流程进入错误处理分支",
                "downstream_effect": "响应、状态、资源释放或恢复路径受影响",
                "external_observation": "返回码/协议响应、连接状态、日志、指标及后续请求结果",
                "cleanup_refs": cleanups,
                "recovery_refs": recoveries,
                "disposition": "retain" if _mapped_case_ids(cases, [evidence_id]) else "need_verify",
                "case_ids": _mapped_case_ids(cases, [evidence_id]),
                "evidence_refs": [evidence_id],
            }
        )
    return _ledger("error_propagation_chains", items, gaps=[])


def _developer_explanation_coverage_artifact(**ledgers: dict[str, Any]) -> dict[str, Any]:
    sections = []
    uncovered: list[str] = []
    for name, payload in ledgers.items():
        items = [item for item in payload.get("items") or [] if isinstance(item, dict)]
        unresolved = [str(item.get("id") or item.get("flow_id") or "unknown") for item in items if str(item.get("disposition") or item.get("status") or "") in {"need_verify", "blocked", "PARTIAL"}]
        sections.append({"section": name, "total": len(items), "unresolved": unresolved, "complete": not unresolved and bool(items)})
        uncovered.extend(f"{name}:{item}" for item in unresolved)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "developer_explanation_coverage",
        "status": "READY" if not uncovered else "PARTIAL",
        "sections": sections,
        "uncovered_items": uncovered,
        "silent_omissions": 0,
    }


def _evidence_consumption_artifact(source_pack: dict[str, Any], flow_pack: dict[str, Any]) -> dict[str, Any]:
    materials, coverage, history = _source_input_materials(source_pack)
    cards = [item for item in source_pack.get("evidence_cards") or [] if isinstance(item, dict)]
    provider_status = [item for item in flow_pack.get("provider_status") or [] if isinstance(item, dict)]
    by_provider = {str(item.get("provider") or "").lower(): item for item in provider_status}
    items = [
        {
            "source": "input_materials",
            "status": "used" if materials else "not_provided",
            "count": len(materials),
            "refs": _strings(item.get("input_id") or item.get("sha256") for item in materials),
        },
        {
            "source": "verified_source_evidence",
            "status": "used" if cards else "blocked",
            "count": len(cards),
            "refs": _strings(item.get("evidence_id") for item in cards),
        },
    ]
    for provider in ("gitnexus", "cgc"):
        state = by_provider.get(provider, {})
        status = str(state.get("status") or "unavailable")
        items.append({"source": provider, "status": status, "count": int(state.get("count") or 0), "reason": str(state.get("reason") or ("工具无可用产物" if status != "used" else "已消费摘要"))})
    items.extend(
        [
            {"source": "coverage", "status": "used" if coverage else "not_provided", "count": len(coverage) if isinstance(coverage, list) else int(bool(coverage))},
            {"source": "history", "status": "used" if history else "not_provided", "count": len(history) if isinstance(history, list) else int(bool(history))},
        ]
    )
    return {"schema_version": SCHEMA_VERSION, "kind": "evidence_consumption_ledger", "items": items}


def _source_input_materials(
    source_pack: dict[str, Any],
) -> tuple[list[dict[str, Any]], Any, Any]:
    payload = source_pack.get("input_materials")
    if isinstance(payload, dict):
        materials = [
            item for item in payload.get("materials") or [] if isinstance(item, dict)
        ]
        return materials, payload.get("coverage"), payload.get("history")
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)], None, None
    return [], None, None


def _scenario_candidates_artifact(**context: Any) -> dict[str, Any]:
    applicability = {
        str(item.get("model")): bool(item.get("applicable"))
        for item in (context["applicability"].get("items") or [])
        if isinstance(item, dict)
    }
    _, coverage, history = _source_input_materials(context["source_pack"])
    resource_rows = [
        item for item in context["resources"].get("items") or []
        if isinstance(item, dict)
    ]
    source_specs = (
        ("branch", "分支", context["branches"].get("items") or []),
        ("state", "状态", context["states"].get("items") or []),
        ("resource", "资源与不变量", resource_rows),
        (
            "numeric_boundary_and_wrap",
            "数值边界与翻转",
            [
                item for item in resource_rows
                if item.get("capacity_model_applicable") or item.get("wraparound_applicable")
            ],
        ),
        (
            "concurrency",
            "并发交错",
            [
                item for item in context["flow_pack"].get("call_edges") or []
                if isinstance(item, dict)
                and _CONCURRENCY_RE.search(json.dumps(item, ensure_ascii=False))
            ],
        ),
        ("error_propagation", "异常传播", context["errors"].get("items") or []),
        (
            "protocol_requirement_security_configuration",
            "协议/需求/安全/配置",
            [
                item for item in context["flow_pack"].get("entry_points") or []
                if isinstance(item, dict)
                and _PROTOCOL_RE.search(json.dumps(item, ensure_ascii=False))
            ],
        ),
        ("coverage_history", "覆盖率/历史证据", _structured_rows(coverage, history)),
    )
    source_status = []
    items = []
    for source_id, label, rows in source_specs:
        applicable = bool(applicability.get(source_id))
        source_status.append({"source": source_id, "label": label, "applicable": applicable, "status": "expanded" if applicable and rows else "need_verify" if applicable else "not_applicable"})
        if not applicable:
            continue
        for index, row in enumerate(rows[:24], 1):
            row = row if isinstance(row, dict) else {}
            refs = _dedupe([row.get("id"), row.get("evidence_id"), *(_strings(row.get("evidence_refs")))])
            candidate = {
                "candidate_id": f"SCN-{source_id.upper().replace('_', '-')}-{index:03d}",
                "source": source_id,
                "title": f"{label}场景 {index}",
                "mechanism": str(row.get("condition") or row.get("state") or row.get("name") or row.get("trigger") or row.get("text") or "待从证据下钻"),
                "priority": "P1",
                "status": "READY" if refs else "PARTIAL",
                "evidence_refs": refs,
                "black_box_boundary": "仅通过外部协议、API、配置、故障注入与可观测状态控制和判定",
            }
            if source_id == "numeric_boundary_and_wrap":
                capacity = row.get("boundary_values") or []
                wrap = row.get("wraparound")
                candidate["boundary_values"] = capacity
                candidate["wraparound"] = wrap
            items.append(candidate)
    return {"schema_version": SCHEMA_VERSION, "kind": "scenario_candidates", "sources": source_status, "items": items}


def _risk_register_artifact(sfmea: list[dict[str, Any]], candidates: dict[str, Any]) -> dict[str, Any]:
    candidate_rows = [item for item in candidates.get("items") or [] if isinstance(item, dict)]
    items = []
    for index, row in enumerate(sfmea, 1):
        if not isinstance(row, dict):
            continue
        risk_id = str(row.get("sfmea_id") or row.get("risk_id") or f"SFMEA-{index:03d}")
        test_ids = _strings(row.get("test_mapping") or row.get("test_case_ids"))
        items.append(
            {
                **dict(row),
                "risk_id": risk_id,
                "mechanism": str(row.get("mechanism") or row.get("cause") or "need_verify"),
                "trigger_condition": str(row.get("trigger_condition") or row.get("cause") or "need_verify"),
                "local_effect": str(row.get("local_effect") or row.get("effect") or "need_verify"),
                "upstream_effect": str(row.get("upstream_effect") or "上游异常可在当前阶段表现为合法外观，需沿传播链验证"),
                "downstream_effect": str(row.get("downstream_effect") or row.get("effect") or "need_verify"),
                "final_effect": str(row.get("final_effect") or row.get("effect") or "need_verify"),
                "latent": str(row.get("latent") or "需验证是否在容量耗尽、计数翻转或后续请求时才暴露"),
                "existing_controls": _strings(row.get("existing_controls") or row.get("detection")),
                "control_gaps": _strings(row.get("control_gaps") or "缺少项须通过黑盒观测和恢复测试关闭"),
                "recovery_verification": str(row.get("recovery_verification") or row.get("mitigation") or "need_verify"),
                "evidence_refs": _strings(row.get("source_evidence")),
                "scenario_candidate_ids": _matching_candidate_ids(row, candidate_rows),
                "test_case_ids": test_ids,
            }
        )
    return _ledger("risk_register", items, gaps=[])


def _blackbox_control_observation_artifact(cases: list[dict[str, Any]]) -> dict[str, Any]:
    items = []
    for index, row in enumerate(cases, 1):
        if not isinstance(row, dict):
            continue
        case_id = str(row.get("case_id") or f"CASE-{index:03d}")
        steps = _strings(row.get("steps"))
        observations = _strings(row.get("observability"))
        white_box = any(re.search(r"\b(?:call|invoke)\s+[A-Za-z_]\w*\s*\(|调用\s*[A-Za-z_]\w*\s*\(", step, re.I) for step in steps)
        status = "executable" if steps and observations and not white_box else "blocked"
        items.append(
            {
                "id": f"CONTROL-{index:03d}",
                "case_id": case_id,
                "controls": steps,
                "external_interfaces": ["protocol", "public API/RPC", "configuration", "fault injection"],
                "observations": observations,
                "expected_result": str(row.get("expected_result") or ""),
                "failure_diagnostics": _strings(row.get("failure_diagnostics")),
                "internal_trace_only": _strings(row.get("source_or_test_evidence")),
                "status": status,
                "blocking_reason": "" if status == "executable" else "缺少可执行外部步骤/观测点，或混入内部函数调用",
            }
        )
    return _ledger("blackbox_control_observation", items, gaps=[])


def _test_basis_artifact(*, source_pack: dict[str, Any], consumption: dict[str, Any], risks: dict[str, Any], candidates: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "test_basis",
        "analysis_target": str(source_pack.get("analysis_target") or ""),
        "repo_revision": str(source_pack.get("repo_revision") or ""),
        "basis": [
            {"type": "source_evidence", "refs": _strings(item.get("evidence_id") for item in source_pack.get("evidence_cards") or [] if isinstance(item, dict))},
            {"type": "evidence_consumption", "refs": _strings(item.get("source") for item in consumption.get("items") or [] if isinstance(item, dict) and item.get("status") == "used")},
            {"type": "risk_register", "refs": _strings(item.get("risk_id") for item in risks.get("items") or [] if isinstance(item, dict))},
            {"type": "scenario_candidates", "refs": _strings(item.get("candidate_id") for item in candidates.get("items") or [] if isinstance(item, dict))},
        ],
    }


def _test_scenarios_artifact(cases: list[dict[str, Any]], candidates: dict[str, Any]) -> dict[str, Any]:
    items = []
    candidate_rows = [item for item in candidates.get("items") or [] if isinstance(item, dict)]
    for index, row in enumerate(cases, 1):
        if not isinstance(row, dict):
            continue
        case_id = str(row.get("case_id") or f"CASE-{index:03d}")
        items.append(
            {
                "scenario_id": f"TEST-SCENARIO-{index:03d}",
                "title": str(row.get("scenario_name") or case_id),
                "dimension": str(row.get("test_dimension") or "未分类"),
                "priority": "P1",
                "status": "READY",
                "preconditions": _strings(row.get("preconditions")),
                "external_steps": _strings(row.get("steps")),
                "expected_result": str(row.get("expected_result") or ""),
                "observations": _strings(row.get("observability")),
                "diagnostics": _strings(row.get("failure_diagnostics")),
                "case_ids": [case_id],
                "risk_ids": _strings(row.get("risk_ids")),
                "candidate_ids": _matching_candidate_ids(row, candidate_rows),
                "evidence_refs": _strings(row.get("source_or_test_evidence")),
            }
        )
    return _ledger("test_scenarios", items, gaps=[])


def _structured_rows(*values: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for value in values:
        if isinstance(value, dict):
            nested = value.get("items")
            if isinstance(nested, list):
                rows.extend(item for item in nested if isinstance(item, dict))
            else:
                rows.append(value)
        elif isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    return rows


def _matching_candidate_ids(
    row: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> list[str]:
    row_refs = set(
        _strings(
            [
                *(_strings(row.get("source_evidence"))),
                *(_strings(row.get("source_or_test_evidence"))),
                *(_strings(row.get("evidence_refs"))),
            ]
        )
    )
    matches: list[str] = []
    for candidate in candidates:
        candidate_refs = set(_strings(candidate.get("evidence_refs")))
        if row_refs & candidate_refs:
            candidate_id = str(candidate.get("candidate_id") or "")
            if candidate_id:
                matches.append(candidate_id)
    return _dedupe(matches)


def _test_flows_artifact(scenarios: dict[str, Any]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in scenarios.get("items") or []:
        if isinstance(row, dict):
            grouped[str(row.get("dimension") or "未分类")].append(row)
    items = []
    for index, (dimension, rows) in enumerate(sorted(grouped.items()), 1):
        items.append(
            {
                "test_flow_id": f"TEST-FLOW-{index:03d}",
                "name": dimension,
                "ordered_scenario_ids": [str(row.get("scenario_id") or "") for row in rows],
                "setup": _dedupe(item for row in rows for item in _strings(row.get("preconditions"))),
                "observations": _dedupe(item for row in rows for item in _strings(row.get("observations"))),
                "rerun_rule": "失败时保留输入、日志、指标和协议抓包，修复后按同一顺序复跑",
            }
        )
    return _ledger("test_flows", items, gaps=[])


def _traceability_artifact(**context: Any) -> dict[str, Any]:
    cases = [row for row in context["cases"] if isinstance(row, dict)]
    risks = context["risks"].get("items") or []
    links = []
    known_evidence = set(context["evidence_index"])
    risk_by_case: dict[str, list[str]] = defaultdict(list)
    known_risk_ids: set[str] = set()
    high_risk_ids: set[str] = set()
    for risk in risks:
        if not isinstance(risk, dict):
            continue
        risk_id = str(risk.get("risk_id") or "")
        if not risk_id:
            continue
        known_risk_ids.add(risk_id)
        mapped = _strings(risk.get("test_case_ids"))
        if int(risk.get("rpn") or 0) >= 200:
            high_risk_ids.add(risk_id)
        for case_id in mapped:
            risk_by_case[case_id].append(risk_id)
    orphan_cases = []
    mapped_risk_ids: set[str] = set()
    unknown_risk_ids: set[str] = set()
    for row in cases:
        case_id = str(row.get("case_id") or "")
        evidence = _strings(row.get("source_or_test_evidence"))
        risk_ids = _dedupe([*risk_by_case.get(case_id, []), *_strings(row.get("risk_ids"))])
        mapped_risk_ids.update(item for item in risk_ids if item in known_risk_ids)
        unknown_risk_ids.update(item for item in risk_ids if item not in known_risk_ids)
        verified_evidence = [
            item
            for item in evidence
            if _is_verified_evidence_reference(item, context["evidence_index"])
        ]
        known_case_risks = [item for item in risk_ids if item in known_risk_ids]
        if not verified_evidence and not known_case_risks:
            orphan_cases.append(case_id)
        links.append(
            {
                "case_id": case_id,
                "risk_ids": risk_ids,
                "evidence_refs": evidence,
                "verified_evidence_refs": verified_evidence,
                "unresolved_evidence_refs": [
                    item
                    for item in evidence
                    if not _is_verified_evidence_reference(item, context["evidence_index"])
                ],
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "traceability_matrix",
        "links": links,
        "orphan_case_ids": orphan_cases,
        "high_risk_unmapped_ids": sorted(high_risk_ids - mapped_risk_ids),
        "unknown_risk_ids": sorted(unknown_risk_ids),
        "disposition_sources": [
            "branch_disposition.json",
            "state_transition_disposition.json",
            "resource_lifecycle_disposition.json",
            "error_propagation_chains.json",
        ],
    }


def _is_verified_evidence_reference(
    value: str,
    evidence: dict[str, dict[str, Any]],
) -> bool:
    """Accept a stable card ID, its verified line suffix, or its display form.

    A test case carries human-readable provenance such as
    ``lib/iscsi/iscsi.c (SRC-02:L1130)``.  The line-qualified identifier still
    needs to resolve against the immutable card range; treating it as a new
    ID made every otherwise-valid trace look unresolved.
    """
    text = str(value or "").strip()
    if text in evidence:
        return True
    candidates = re.findall(r"[A-Za-z][A-Za-z0-9_-]*:L\d+", text)
    return any(
        _resolve_claim_evidence_card(
            evidence=evidence,
            evidence_id=candidate,
            reference={},
        )
        is not None
        for candidate in candidates
    )


def _evidence_index(source_pack: dict[str, Any], flow_pack: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = [item for item in source_pack.get("evidence_cards") or [] if isinstance(item, dict)]
    for key in ("entry_points", "call_edges", "state_objects", "state_transitions", "conditions", "error_paths", "cleanup_paths", "recovery_paths", "related_tests"):
        rows.extend(item for item in flow_pack.get(key) or [] if isinstance(item, dict))
    return {str(item.get("evidence_id")): item for item in rows if str(item.get("evidence_id") or "")}


def _artifact_items(artifacts: dict[str, Any], name: str) -> list[dict[str, Any]]:
    payload = artifacts.get(name)
    return [item for item in (payload.get("items") if isinstance(payload, dict) else []) or [] if isinstance(item, dict)]


def _ledger(kind: str, items: list[dict[str, Any]], *, gaps: Any) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "status": "READY" if items and not _strings(gaps) else "PARTIAL",
        "items": items,
        "gaps": _strings(gaps),
        "item_count": len(items),
    }


def _mapped_case_ids(cases: list[dict[str, Any]], evidence_refs: list[str]) -> list[str]:
    wanted = set(_strings(evidence_refs))
    return _dedupe(
        str(row.get("case_id") or "")
        for row in cases
        if isinstance(row, dict) and wanted.intersection(_strings(row.get("source_or_test_evidence")))
    )


def _resource_label(kind: str) -> str:
    return {
        "cmd": "命令资源（cmd）",
        "pdu": "协议数据单元（PDU）",
        "queue_item": "队列/请求项",
        "connection": "连接",
        "session": "会话",
        "reference": "引用计数",
        "bitmap": "Bitmap/位图槽位",
        "counter": "计数器/Tag/Generation",
        "handle": "句柄/描述符",
        "quota": "配额/容量",
        "memory": "内存/缓冲区",
    }.get(kind, kind)


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        return [json.dumps(value, ensure_ascii=False, sort_keys=True)]
    if isinstance(value, Iterable):
        values = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                values.append(text)
        return values
    text = str(value).strip()
    return [text] if text else []


def _dedupe(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _plain_text(value: Any, limit: int) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", str(value or ""))
    text = re.sub(r"<\s*/?\s*(?:script|style|iframe|object|embed)[^>]*>", "[已移除标签]", text, flags=re.I)
    text = re.sub(r"\bon(?:error|load|click|mouseover)\s*=", "on-event=", text, flags=re.I)
    return text[:limit]


def _json_for_html(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return (
        text.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _sanitize_embedded_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            _plain_text(key, 160): _sanitize_embedded_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_embedded_payload(item) for item in value]
    if isinstance(value, str):
        return _plain_text(value, 20_000)
    return value


def _stable_fragment(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-.")
    if text:
        return text[:80]
    return hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()[:12]


def _node_depth(node: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> int:
    depth = 0
    current = node
    visited: set[str] = set()
    while current.get("parent_id"):
        parent_id = str(current.get("parent_id"))
        if parent_id in visited:
            break
        visited.add(parent_id)
        depth += 1
        current = by_id.get(parent_id, {})
    return depth
