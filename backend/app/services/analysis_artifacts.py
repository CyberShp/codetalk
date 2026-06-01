"""Deterministic analysis artifacts for report follow-up.

These files are intentionally produced without another LLM pass.  They give
reports and follow-up chat a stable evidence surface: which analysis object is
backed by which cards, which functions expose failure signals, and which branch
conditions are visible in the snippets CodeTalk already collected.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

ARTIFACT_FILENAMES = {
    "claim_evidence_map": "claim_evidence_map.json",
    "function_failure_matrix": "function_failure_matrix.json",
    "branch_deep_dive": "branch_deep_dive.json",
}

_KEYWORDS = {
    "if",
    "for",
    "while",
    "switch",
    "case",
    "return",
    "sizeof",
    "catch",
    "except",
}

_PY_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(")
_JS_DEF_RE = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_]\w*)\s*\(")
_GO_DEF_RE = re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\(")
_C_DEF_RE = re.compile(
    r"^\s*(?:[A-Za-z_][\w:<>,~\s\*\[\]&]+\s+)?([A-Za-z_]\w*)\s*\([^;]*\)\s*(?:\{|$)"
)
_ASSIGN_RE = re.compile(r"\b(?:state|status|phase|mode|flags?)\b\s*(?:=|:=|\|=|&=)", re.I)
_BRANCH_RE = re.compile(
    r"\b(if|else\s+if|switch|case|default|catch|except|while|for)\b|return\s+-[A-Za-z0-9_]+",
    re.I,
)
_ERROR_RE = re.compile(
    r"\b(err|error|errno|fail|failed|exception|throw|panic|SSL_get_error|goto\s+fail|return\s+-|E[A-Z0-9_]{2,})\b",
    re.I,
)
_CLEANUP_RE = re.compile(
    r"\b(free|close|destroy|release|cleanup|defer|finally|unref|put|delete|dispose)\b",
    re.I,
)
_RESOURCE_RE = re.compile(
    r"\b(alloc|malloc|calloc|realloc|open|create|init|connect|read|write|send|recv|lock|unlock)\b",
    re.I,
)
_OBSERVABLE_RE = re.compile(
    r"\b(log|printf|warn|trace|metric|return|status|code|response|reply|disconnect|close)\b",
    re.I,
)
_PROPAGATION_RE = re.compile(
    r"\b(return|throw|raise|goto|propagate|complete|callback|reply|disconnect|abort)\b",
    re.I,
)
_ERROR_CONDITION_RE = re.compile(
    r"(<\s*0|<=\s*0|==\s*NULL|!=\s*0|!\s*[A-Za-z_]\w*|failed|fail|error|errno|timeout|SSL_get_error)",
    re.I,
)
_SILENT_SUCCESS_RE = re.compile(r"\breturn\s+0\s*;", re.I)


def build_analysis_artifact_bundle(
    *,
    task_id: str,
    analysis_unit_mapping: dict,
    evidence_cards: list[Any],
    analysis_units: list[dict] | None = None,
) -> dict[str, dict]:
    """Build all deterministic artifacts from already-collected evidence."""

    card_dicts = [_card_to_dict(card) for card in evidence_cards or []]
    return {
        "claim_evidence_map": build_claim_evidence_map(
            task_id=task_id,
            analysis_unit_mapping=analysis_unit_mapping or {},
            evidence_cards=card_dicts,
            analysis_units=analysis_units or [],
        ),
        "function_failure_matrix": build_function_failure_matrix(
            task_id=task_id,
            evidence_cards=card_dicts,
        ),
        "branch_deep_dive": build_branch_deep_dive(
            task_id=task_id,
            evidence_cards=card_dicts,
        ),
    }


def build_claim_evidence_map(
    *,
    task_id: str,
    analysis_unit_mapping: dict,
    evidence_cards: list[dict],
    analysis_units: list[dict],
) -> dict:
    cards_by_id = {str(card.get("card_id")): card for card in evidence_cards if card.get("card_id")}
    unit_by_id = {
        str(unit.get("unit_id") or unit.get("id")): unit
        for unit in (analysis_unit_mapping.get("units") or analysis_units or [])
        if unit.get("unit_id") or unit.get("id")
    }

    claims: list[dict] = []
    for obj in analysis_unit_mapping.get("objects") or []:
        card_ids = [str(cid) for cid in obj.get("evidence_card_ids") or [] if cid]
        cards = [cards_by_id[cid] for cid in card_ids if cid in cards_by_id]
        files = sorted({str(c.get("file_path")) for c in cards if c.get("file_path")})
        symbols = sorted({str(c.get("symbol")) for c in cards if c.get("symbol")})
        uncertainty = [str(w) for w in obj.get("warnings") or [] if w]
        needs_verification = any(c.get("needs_verification") for c in cards)
        if needs_verification:
            uncertainty.append("At least one evidence card is marked needs_verification.")
        if not card_ids:
            uncertainty.append("No direct evidence card was produced for this analysis object.")

        coverage_status = str(obj.get("coverage_status") or "")
        status = "supported" if card_ids else "gap"
        if coverage_status == "resolved_without_evidence_cards":
            status = "candidate_only"
        if needs_verification and status == "supported":
            status = "needs_verification"

        unit_id = obj.get("unit_id")
        unit = unit_by_id.get(str(unit_id)) if unit_id else None
        claims.append(
            {
                "claim_id": f"claim:{obj.get('object_id') or len(claims) + 1}",
                "claim": str(obj.get("text") or obj.get("unit_title") or "analysis object"),
                "status": status,
                "coverage_status": coverage_status or status,
                "unit_id": unit_id,
                "unit_title": obj.get("unit_title") or (unit or {}).get("title"),
                "evidence_card_ids": card_ids,
                "files": files,
                "symbols": symbols,
                "evidence": [_evidence_ref(card) for card in cards],
                "uncertainty": _dedupe(uncertainty),
            }
        )

    return {
        "version": "claim-evidence-map-v1",
        "task_id": task_id,
        "claim_count": len(claims),
        "supported_count": sum(1 for c in claims if c["status"] in {"supported", "needs_verification"}),
        "gap_count": sum(1 for c in claims if c["status"] == "gap"),
        "claims": claims,
    }


def build_function_failure_matrix(*, task_id: str, evidence_cards: list[dict]) -> dict:
    rows_by_key: dict[tuple[str, str], dict] = {}
    for card in evidence_cards:
        lines = _snippet_lines(card.get("snippet") or "")
        functions = _functions_for_card(card, lines)
        if not functions:
            functions = ["(unknown_function)"]

        signals = _extract_signals(lines)
        for function in functions:
            key = (str(card.get("file_path") or ""), function)
            row = rows_by_key.setdefault(
                key,
                {
                    "function": function,
                    "file_path": card.get("file_path"),
                    "source": card.get("source"),
                    "confidence": card.get("confidence") or "unknown",
                    "evidence_card_ids": [],
                    "branch_conditions": [],
                    "error_signals": [],
                    "cleanup_signals": [],
                    "resource_signals": [],
                    "state_transitions": [],
                    "observable_signals": [],
                    "propagation_signals": [],
                    "containment_gaps": [],
                    "risk": "unknown",
                    "gaps": [],
                    "next_actions": [],
                },
            )
            row["evidence_card_ids"] = _dedupe([*row["evidence_card_ids"], card.get("card_id")])
            for field in (
                "branch_conditions",
                "error_signals",
                "cleanup_signals",
                "resource_signals",
                "state_transitions",
                "observable_signals",
                "propagation_signals",
            ):
                row[field] = _dedupe([*row[field], *signals[field]])[:12]
            row["containment_gaps"] = _dedupe([
                *row["containment_gaps"],
                *_containment_gaps_for_signals(signals),
            ])
            if not lines:
                row["gaps"].append("No source snippet was available for this function.")
            if not signals["branch_conditions"]:
                row["gaps"].append("No explicit branch condition was visible in the collected snippet.")
            if not signals["error_signals"]:
                row["gaps"].append("No explicit error signal was visible in the collected snippet.")
            row["gaps"] = _dedupe(row["gaps"])
            row["risk"] = _risk_for_row(row)
            row["next_actions"] = _next_actions_for_row(row)

    rows = sorted(rows_by_key.values(), key=lambda r: (str(r.get("file_path") or ""), r["function"]))
    return {
        "version": "function-failure-matrix-v1",
        "task_id": task_id,
        "function_count": len(rows),
        "functions": rows,
    }


def build_branch_deep_dive(*, task_id: str, evidence_cards: list[dict]) -> dict:
    branches: list[dict] = []
    gaps: list[dict] = []
    for card in evidence_cards:
        lines = _snippet_lines(card.get("snippet") or "", with_numbers=True)
        functions = _functions_for_card(card, [line for _, line in lines]) or ["(unknown_function)"]
        card_branches = []
        for idx, (line_number, line) in enumerate(lines):
            if not _BRANCH_RE.search(line):
                continue
            condition = _extract_branch_condition(line)
            context = _branch_context(lines, line_number, idx)
            containment_gaps = _branch_containment_gaps(condition, context)
            item = {
                "branch_id": f"branch:{card.get('card_id')}:{line_number or len(branches) + 1}",
                "function": functions[0],
                "file_path": card.get("file_path"),
                "condition": condition,
                "line": line,
                "line_number": line_number,
                "category": _branch_category(line),
                "evidence_card_id": card.get("card_id"),
                "observation": _observation_hint(line),
                "test_trigger_hint": f"Force input/state to satisfy: {condition}",
                "propagation": _join_limited(_propagation_lines(context)),
                "containment_gaps": containment_gaps,
                "risk": "high" if containment_gaps else "medium" if _is_error_condition(condition) else "low",
                "uncertainty": [] if card.get("confidence") == "high" else ["Evidence confidence is not high."],
            }
            branches.append(item)
            card_branches.append(item)
        if not card_branches:
            gaps.append(
                {
                    "evidence_card_id": card.get("card_id"),
                    "file_path": card.get("file_path"),
                    "function": functions[0],
                    "gap": "No branch condition was visible in the collected snippet.",
                }
            )

    return {
        "version": "branch-deep-dive-v1",
        "task_id": task_id,
        "branch_count": len(branches),
        "branches": branches[:200],
        "gaps": gaps[:200],
    }


async def write_analysis_artifacts(
    *,
    output_dir: Path,
    task_id: str,
    analysis_unit_mapping: dict,
    evidence_cards: list[Any],
    analysis_units: list[dict] | None = None,
) -> list[Path]:
    """Write all artifact JSON files into a task output directory."""

    bundle = build_analysis_artifact_bundle(
        task_id=task_id,
        analysis_unit_mapping=analysis_unit_mapping,
        evidence_cards=evidence_cards,
        analysis_units=analysis_units or [],
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for key, filename in ARTIFACT_FILENAMES.items():
        path = output_dir / filename
        payload = json.dumps(bundle[key], ensure_ascii=False, indent=2)
        await asyncio.to_thread(path.write_text, payload, "utf-8")
        written.append(path)
    return written


def load_analysis_artifact_bundle(output_dir: Path) -> dict[str, dict]:
    """Load available artifact JSON files from an output directory."""

    bundle: dict[str, dict] = {}
    for key, filename in ARTIFACT_FILENAMES.items():
        path = output_dir / filename
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            bundle[key] = data
    return bundle


def format_artifacts_for_report_qa(
    bundle: dict[str, dict],
    query: str | None = None,
    *,
    max_claims: int = 8,
    max_functions: int = 8,
    max_branches: int = 8,
) -> str:
    """Render compact artifact context for report_qa prompts."""

    if not bundle:
        return ""
    query_terms = _query_terms(query)
    lines = ["## CODETALK_ANALYSIS_ARTIFACTS"]

    claim_map = bundle.get("claim_evidence_map") or {}
    claims = _rank_items(claim_map.get("claims") or [], query_terms)
    if claims:
        lines.extend(["", "### Claim Evidence Map"])
        for claim in claims[:max_claims]:
            lines.append(
                "- {claim_id} [{status}] {claim}; evidence={evidence}; files={files}; symbols={symbols}; uncertainty={uncertainty}".format(
                    claim_id=claim.get("claim_id"),
                    status=claim.get("status"),
                    claim=_one_line(claim.get("claim")),
                    evidence=", ".join(claim.get("evidence_card_ids") or []) or "none",
                    files=", ".join(claim.get("files") or []) or "none",
                    symbols=", ".join(claim.get("symbols") or []) or "none",
                    uncertainty=", ".join(claim.get("uncertainty") or []) or "none",
                )
            )

    matrix = bundle.get("function_failure_matrix") or {}
    functions = _rank_items(matrix.get("functions") or [], query_terms)
    if functions:
        lines.extend(["", "### Function Failure Matrix"])
        for row in functions[:max_functions]:
            lines.append(
                "- {file}::{fn}; risk={risk}; evidence={evidence}; branches={branches}; errors={errors}; cleanup={cleanup}; state={state}; propagation={propagation}; containment_gaps={containment}; gaps={gaps}".format(
                    file=row.get("file_path") or "(unknown_file)",
                    fn=row.get("function") or "(unknown_function)",
                    risk=row.get("risk") or "unknown",
                    evidence=", ".join(row.get("evidence_card_ids") or []) or "none",
                    branches=_join_limited(row.get("branch_conditions")),
                    errors=_join_limited(row.get("error_signals")),
                    cleanup=_join_limited(row.get("cleanup_signals")),
                    state=_join_limited(row.get("state_transitions")),
                    propagation=_join_limited(row.get("propagation_signals")),
                    containment=_join_limited(row.get("containment_gaps")),
                    gaps=_join_limited(row.get("gaps")),
                )
            )

    deep_dive = bundle.get("branch_deep_dive") or {}
    branches = _rank_items(deep_dive.get("branches") or [], query_terms)
    if branches:
        lines.extend(["", "### Branch Deep Dive"])
        for branch in branches[:max_branches]:
            lines.append(
                "- {file}::{fn}; risk={risk}; condition={condition}; evidence={evidence}; trigger={trigger}; observation={observation}; containment_gaps={containment}".format(
                    file=branch.get("file_path") or "(unknown_file)",
                    fn=branch.get("function") or "(unknown_function)",
                    risk=branch.get("risk") or "unknown",
                    condition=_one_line(branch.get("condition")),
                    evidence=branch.get("evidence_card_id") or "none",
                    trigger=_one_line(branch.get("test_trigger_hint")),
                    observation=_one_line(branch.get("observation")),
                    containment=_join_limited(branch.get("containment_gaps")),
                )
            )

    return "\n".join(lines).strip()


def _card_to_dict(card: Any) -> dict:
    if isinstance(card, dict):
        return dict(card)
    to_dict = getattr(card, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict())
    fields = (
        "card_id",
        "object_id",
        "title",
        "source",
        "confidence",
        "file_path",
        "symbol",
        "snippet",
        "notes",
        "needs_verification",
    )
    return {name: getattr(card, name, None) for name in fields}


def _evidence_ref(card: dict) -> dict:
    return {
        "card_id": card.get("card_id"),
        "title": card.get("title"),
        "source": card.get("source"),
        "confidence": card.get("confidence"),
        "file_path": card.get("file_path"),
        "symbol": card.get("symbol"),
        "needs_verification": bool(card.get("needs_verification")),
    }


def _functions_for_card(card: dict, lines: list[str]) -> list[str]:
    symbol = str(card.get("symbol") or "").strip()
    if symbol:
        return [symbol]
    found: list[str] = []
    for line in lines:
        for regex in (_PY_DEF_RE, _JS_DEF_RE, _GO_DEF_RE, _C_DEF_RE):
            match = regex.search(line)
            if not match:
                continue
            name = match.group(1)
            if name not in _KEYWORDS:
                found.append(name)
                break
    return _dedupe(found)[:4]


def _extract_signals(lines: list[str]) -> dict[str, list[str]]:
    signals = {
        "branch_conditions": [],
        "error_signals": [],
        "cleanup_signals": [],
        "resource_signals": [],
        "state_transitions": [],
        "observable_signals": [],
        "propagation_signals": [],
    }
    for line in lines:
        clean = _one_line(line)
        if not clean:
            continue
        if _BRANCH_RE.search(clean):
            signals["branch_conditions"].append(_extract_branch_condition(clean))
        if _ERROR_RE.search(clean) or _is_error_condition(clean):
            signals["error_signals"].append(clean)
        if _CLEANUP_RE.search(clean):
            signals["cleanup_signals"].append(clean)
        if _RESOURCE_RE.search(clean):
            signals["resource_signals"].append(clean)
        if _ASSIGN_RE.search(clean) or "->state" in clean or ".state" in clean:
            signals["state_transitions"].append(clean)
        if _OBSERVABLE_RE.search(clean):
            signals["observable_signals"].append(clean)
        if _PROPAGATION_RE.search(clean):
            signals["propagation_signals"].append(clean)
    return {key: _dedupe(value) for key, value in signals.items()}


def _snippet_lines(snippet: str, *, with_numbers: bool = False):
    out = []
    for raw in (snippet or "").splitlines():
        line_number, clean = _clean_line(raw)
        if not clean or clean.startswith("..."):
            continue
        if with_numbers:
            out.append((line_number, clean))
        else:
            out.append(clean)
    return out


def _clean_line(raw: str) -> tuple[int | None, str]:
    text = (raw or "").strip()
    match = re.match(r"^(\d+):\s*(.*)$", text)
    if match:
        return int(match.group(1)), match.group(2).strip()
    return None, text


def _extract_branch_condition(line: str) -> str:
    clean = _one_line(line)
    for keyword in ("if", "switch", "while", "for", "catch", "except"):
        match = re.search(rf"\b{keyword}\s*\(([^)]*)\)", clean, re.I)
        if match:
            return f"{keyword} ({match.group(1).strip()})"
    case_match = re.search(r"\b(case\s+[^:]+:|default\s*:)", clean, re.I)
    if case_match:
        return case_match.group(1)
    return clean


def _branch_category(line: str) -> str:
    clean = line.lower()
    if "return -" in clean or _ERROR_RE.search(line):
        return "error_or_negative_return"
    if "switch" in clean or "case " in clean:
        return "dispatch"
    if "while" in clean or "for " in clean:
        return "loop"
    return "condition"


def _observation_hint(line: str) -> str:
    clean = line.lower()
    if "return" in clean:
        return "Observe return code and caller propagation."
    if "log" in clean or "printf" in clean:
        return "Observe emitted log or trace message."
    if "close" in clean or "disconnect" in clean:
        return "Observe connection/resource state after the branch."
    return "Observe status, state transition, logs, and downstream behavior."


def _is_error_condition(text: str) -> bool:
    return _ERROR_CONDITION_RE.search(text or "") is not None


def _branch_context(
    lines: list[tuple[int | None, str]],
    line_number: int | None,
    fallback_idx: int,
) -> list[str]:
    if line_number is None:
        return [line for _number, line in lines[fallback_idx: fallback_idx + 10]]
    for idx, (number, _line) in enumerate(lines):
        if number == line_number:
            return [line for _number, line in lines[idx: idx + 10]]
    return []


def _propagation_lines(lines: list[str]) -> list[str]:
    return [
        _one_line(line)
        for line in lines
        if _PROPAGATION_RE.search(line or "")
    ]


def _containment_gaps_for_signals(signals: dict[str, list[str]]) -> list[str]:
    if not signals.get("error_signals"):
        return []
    gaps: list[str] = []
    if not signals.get("propagation_signals"):
        gaps.append("No explicit error propagation signal was visible near the error branch.")
    if not signals.get("cleanup_signals") and not signals.get("state_transitions"):
        gaps.append("No cleanup/resource release or state rollback was visible near the error branch.")
    if not signals.get("observable_signals"):
        gaps.append("No user-visible return/log/status observation was visible for the error branch.")
    if any(_SILENT_SUCCESS_RE.search(line) for line in signals.get("propagation_signals", [])):
        gaps.append("Potential silent success: an error-like branch returns success code 0.")
    return gaps


def _branch_containment_gaps(condition: str, context: list[str]) -> list[str]:
    if not _is_error_condition(condition):
        return []
    gaps: list[str] = []
    joined = "\n".join(context)
    if _SILENT_SUCCESS_RE.search(joined):
        gaps.append("Potential silent success: error-like branch returns success code 0.")
    if not _CLEANUP_RE.search(joined) and not _ASSIGN_RE.search(joined):
        gaps.append("No cleanup/resource release or state rollback was visible in the branch window.")
    if not any(_PROPAGATION_RE.search(line) for line in context):
        gaps.append("No explicit propagation/return/throw signal was visible in the branch window.")
    return _dedupe(gaps)


def _risk_for_row(row: dict) -> str:
    if row.get("containment_gaps"):
        return "high"
    if row.get("error_signals") or row.get("cleanup_signals"):
        return "medium"
    return "low"


def _next_actions_for_row(row: dict) -> list[str]:
    actions = []
    if row.get("branch_conditions"):
        actions.append("Design one black-box trigger per branch condition.")
    else:
        actions.append("Request a wider source window or CGC branch query for this function.")
    if row.get("error_signals"):
        actions.append("Map each error signal to expected return/log/state observations.")
    if row.get("cleanup_signals"):
        actions.append("Verify cleanup/resource release on failure and retry paths.")
    if row.get("containment_gaps"):
        actions.append("Trace caller-visible propagation and add a negative test for missing fallback.")
    return _dedupe(actions)


def _rank_items(items: list[dict], terms: list[str]) -> list[dict]:
    if not terms:
        return items

    def score(item: dict) -> int:
        blob = json.dumps(item, ensure_ascii=False).lower()
        term_hits = sum(1 for term in terms if term in blob)
        gap_bonus = 1 if str(item.get("status") or "").lower() == "gap" else 0
        return term_hits * 10 + gap_bonus

    return sorted(items, key=score, reverse=True)


def _query_terms(query: str | None) -> list[str]:
    raw = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", query or "")
    seen: set[str] = set()
    terms = []
    for term in raw:
        lowered = term.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        terms.append(lowered)
    return terms[:8]


def _join_limited(values: Any, limit: int = 3) -> str:
    items = [_one_line(v) for v in (values or []) if _one_line(v)]
    if not items:
        return "none"
    text = "; ".join(items[:limit])
    if len(items) > limit:
        text += "; ..."
    return text


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _dedupe(values: list[Any]) -> list:
    out = []
    seen = set()
    for value in values:
        if value is None:
            continue
        key = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out
