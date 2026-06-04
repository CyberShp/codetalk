"""Deterministic report artifacts rendered by CodeTalk.

The LLM is allowed to write narrative content.  Layout-heavy structures that
often break on small internal models (tables, diagrams, SFMEA grids) are
rendered here from the evidence objects the pipeline already owns.
"""

from __future__ import annotations

from typing import Any

from app.services.analysis_artifacts import build_analysis_artifact_bundle


def build_codetalk_section_artifacts(
    *,
    section: dict,
    analysis_units: list[dict],
    evidence_cards: list[Any],
    common_context: dict,
    max_rows: int = 6,
) -> str:
    """Return Markdown artifacts for one report section.

    Every section gets a collapsed, compact evidence snapshot.  Sections that
    declare ``requires_mermaid`` or ``requires_sfmea`` get deterministic
    graph/table blocks as well, so the LLM does not have to maintain fragile
    Markdown structures.
    """

    parts: list[str] = []

    evidence_rows = _evidence_rows(
        analysis_units,
        evidence_cards,
        common_context,
        max_rows=max_rows,
    )
    parts.append(_wrap_details(
        "Evidence snapshot",
        _render_evidence_table(evidence_rows),
    ))

    if section.get("requires_mermaid"):
        parts.append(_render_mermaid_diagram(section, analysis_units, common_context))

    if section.get("requires_sfmea"):
        parts.append(_render_sfmea_table(analysis_units, common_context, max_rows=max_rows))

    return "\n\n".join(part for part in parts if part.strip()).strip()


def build_codetalk_report_artifact_appendix(
    *,
    task_id: str,
    analysis_units: list[dict],
    evidence_cards: list[Any],
    common_context: dict,
    max_rows: int = 12,
) -> str:
    """Return report-level traceability artifacts.

    This is the report contract counterpart to the JSON artifacts written into
    the task output directory.  It makes the final Markdown report useful even
    before the user opens follow-up chat or inspects JSON files.
    """

    mapping = _analysis_unit_mapping_for_report(
        task_id=task_id,
        analysis_units=analysis_units,
        evidence_cards=evidence_cards,
        common_context=common_context,
    )
    bundle = build_analysis_artifact_bundle(
        task_id=task_id,
        analysis_unit_mapping=mapping,
        evidence_cards=evidence_cards,
        analysis_units=analysis_units,
    )
    parts = [
        "## 90 CodeTalk Traceability Artifacts",
        "> Deterministic appendix rendered by CodeTalk from collected evidence cards. "
        "Use it as the claim/evidence contract; anything marked `gap` or "
        "`needs_verification` requires another source read or tool rerun.",
        "",
        _render_claim_evidence_map(bundle.get("claim_evidence_map") or {}, max_rows=max_rows),
        "",
        _render_function_failure_matrix(bundle.get("function_failure_matrix") or {}, max_rows=max_rows),
        "",
        _render_branch_deep_dive(bundle.get("branch_deep_dive") or {}, max_rows=max_rows),
    ]
    appendix = "\n".join(part for part in parts if str(part).strip()).strip()
    return _wrap_details("Detailed traceability appendix", appendix)


def _evidence_rows(
    analysis_units: list[dict],
    evidence_cards: list[Any],
    common_context: dict,
    *,
    max_rows: int,
) -> list[tuple[str, str, str, str]]:
    cards: list[Any] = []
    for unit in analysis_units or []:
        cards.extend(unit.get("cards") or [])
    cards.extend(evidence_cards or [])

    rows: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for card in cards:
        title = _attr(card, "title") or _attr(card, "symbol") or "evidence"
        source = _attr(card, "source") or "unknown"
        file_path = _attr(card, "file_path") or ""
        symbol = _attr(card, "symbol") or ""
        confidence = _attr(card, "confidence") or "unknown"
        file_symbol = file_path if not symbol else f"{file_path}::{symbol}" if file_path else symbol
        key = (str(title), str(source), str(file_symbol))
        if key in seen:
            continue
        seen.add(key)
        rows.append((
            _cell(str(title)),
            _cell(str(source)),
            _cell(str(file_symbol or "(not bound)")),
            _cell(str(confidence)),
        ))
        if len(rows) >= max_rows:
            break
    if not rows:
        for label in _unit_labels(analysis_units, common_context)[:max_rows]:
            rows.append((
                _cell(label),
                "codetalk",
                "(not bound)",
                "unknown",
            ))
    return rows


def _analysis_unit_mapping_for_report(
    *,
    task_id: str,
    analysis_units: list[dict],
    evidence_cards: list[Any],
    common_context: dict,
) -> dict:
    cards_by_object: dict[str, list[Any]] = {}
    cards_by_id: dict[str, Any] = {}
    all_cards: list[Any] = []
    for unit in analysis_units or []:
        all_cards.extend(unit.get("cards") or [])
    all_cards.extend(evidence_cards or [])
    for card in all_cards:
        card_id = str(_attr(card, "card_id") or "").strip()
        if card_id:
            cards_by_id.setdefault(card_id, card)
        object_id = str(_attr(card, "object_id") or "").strip()
        if object_id:
            cards_by_object.setdefault(object_id, []).append(card)

    units: list[dict] = []
    unit_by_object: dict[str, dict] = {}
    for idx, unit in enumerate(analysis_units or [], start=1):
        unit_id = str(unit.get("id") or unit.get("unit_id") or f"unit_{idx}")
        unit_cards = unit.get("cards") or []
        evidence_ids = _dedupe(
            [str(_attr(card, "card_id")) for card in unit_cards if _attr(card, "card_id")]
        )
        files = sorted({
            str(_attr(card, "file_path"))
            for card in unit_cards
            if _attr(card, "file_path")
        })
        entry = {
            "unit_id": unit_id,
            "title": unit.get("title") or unit_id,
            "object_ids": list(unit.get("object_ids") or []),
            "object_texts": list(unit.get("object_texts") or []),
            "evidence_card_ids": evidence_ids,
            "files": files,
        }
        units.append(entry)
        for object_id in entry["object_ids"]:
            unit_by_object[str(object_id)] = entry

    objects = []
    analysis_objects = common_context.get("analysis_objects") or []
    for idx, obj in enumerate(analysis_objects, start=1):
        object_id = str(obj.get("id") or obj.get("object_id") or f"obj_{idx}")
        direct_cards = cards_by_object.get(object_id, [])
        direct_ids = _dedupe(
            [str(_attr(card, "card_id")) for card in direct_cards if _attr(card, "card_id")]
        )
        unit = unit_by_object.get(object_id)
        if unit and not direct_ids:
            direct_ids = list(unit.get("evidence_card_ids") or [])
        warnings = []
        if not direct_ids:
            warnings.append("No direct evidence card was produced for this analysis object.")
        objects.append({
            "object_id": object_id,
            "text": obj.get("text") or obj.get("title") or object_id,
            "kind": obj.get("kind"),
            "priority": obj.get("priority"),
            "coverage_status": "direct_evidence" if direct_ids else "unresolved",
            "unit_id": unit.get("unit_id") if unit else None,
            "unit_title": unit.get("title") if unit else None,
            "candidate_count": len(direct_ids),
            "candidates": [],
            "evidence_card_ids": direct_ids,
            "warnings": warnings,
        })

    return {
        "version": "analysis-unit-mapping-report-v1",
        "task_id": task_id,
        "plan_object_count": len(objects),
        "unit_count": len(units),
        "objects": objects,
        "units": units,
    }


def _render_claim_evidence_map(claim_map: dict, *, max_rows: int) -> str:
    lines = [
        "### CodeTalk Claim-Evidence Map",
        "",
        "| Claim | Status | Unit | Evidence cards | Files/Symbols | Uncertainty |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    claims = claim_map.get("claims") or []
    if not claims:
        lines.append("| (no claim objects) | gap | (none) | (none) | (none) | No analysis objects were captured. |")
        return "\n".join(lines)
    for claim in claims[:max_rows]:
        file_symbol = _join_cell([*(claim.get("files") or []), *(claim.get("symbols") or [])])
        lines.append(
            "| {claim_id}: {claim} | {status} | {unit} | {evidence} | {file_symbol} | {uncertainty} |".format(
                claim_id=_cell(str(claim.get("claim_id") or "")),
                claim=_cell(str(claim.get("claim") or "")),
                status=_cell(str(claim.get("status") or "")),
                unit=_cell(str(claim.get("unit_title") or claim.get("unit_id") or "(none)")),
                evidence=_cell(_join_cell(claim.get("evidence_card_ids") or [])),
                file_symbol=_cell(file_symbol),
                uncertainty=_cell(_join_cell(claim.get("uncertainty") or [])),
            )
        )
    return "\n".join(lines)


def _render_function_failure_matrix(matrix: dict, *, max_rows: int) -> str:
    lines = [
        "### CodeTalk Function Failure Matrix",
        "",
        "| Function | Risk | Branch/Error signals | State/Cleanup/Propagation | Evidence | Gaps / next actions |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    rows = matrix.get("functions") or []
    if not rows:
        lines.append("| (no function evidence) | unknown | none | none | none | Need source snippets or CGC function context. |")
        return "\n".join(lines)
    for row in rows[:max_rows]:
        function_ref = f"{row.get('file_path') or '(unknown_file)'}::{row.get('function') or '(unknown_function)'}"
        branch_error = _join_cell([
            *_limit(row.get("branch_conditions")),
            *_limit(row.get("error_signals")),
        ])
        state_cleanup = _join_cell([
            *_limit(row.get("state_transitions")),
            *_limit(row.get("cleanup_signals")),
            *_limit(row.get("propagation_signals")),
        ])
        gaps_next = _join_cell([
            *_limit(row.get("containment_gaps"), 2),
            *_limit(row.get("gaps"), 2),
            *_limit(row.get("next_actions"), 2),
        ])
        lines.append(
            f"| {_cell(function_ref)} | {_cell(str(row.get('risk') or 'unknown'))} | {_cell(branch_error)} | {_cell(state_cleanup)} | "
            f"{_cell(_join_cell(row.get('evidence_card_ids') or []))} | {_cell(gaps_next)} |"
        )
    return "\n".join(lines)


def _render_branch_deep_dive(deep_dive: dict, *, max_rows: int) -> str:
    lines = [
        "### CodeTalk Branch Deep Dive",
        "",
        "| Function | Risk | Condition | Observation / containment | Evidence | Test trigger hint |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    branches = deep_dive.get("branches") or []
    if not branches:
        lines.append("| (no branch evidence) | unknown | none | none | none | Request a wider source window or CGC branch query. |")
        return "\n".join(lines)
    for branch in branches[:max_rows]:
        function_ref = f"{branch.get('file_path') or '(unknown_file)'}::{branch.get('function') or '(unknown_function)'}"
        lines.append(
            "| {fn} | {risk} | {condition} | {observation} | {evidence} | {trigger} |".format(
                fn=_cell(function_ref),
                risk=_cell(str(branch.get("risk") or "unknown")),
                condition=_cell(str(branch.get("condition") or "")),
                observation=_cell(_join_cell([
                    str(branch.get("observation") or ""),
                    *_limit(branch.get("containment_gaps"), 2),
                ])),
                evidence=_cell(str(branch.get("evidence_card_id") or "")),
                trigger=_cell(str(branch.get("test_trigger_hint") or "")),
            )
        )
    return "\n".join(lines)


def _render_evidence_table(rows: list[tuple[str, str, str, str]]) -> str:
    lines = [
        "### CodeTalk Evidence Table",
        "",
        "| Evidence | Source | File/Symbol | Confidence |",
        "| --- | --- | --- | --- |",
    ]
    for title, source, file_symbol, confidence in rows:
        lines.append(f"| {title} | {source} | {file_symbol} | {confidence} |")
    return "\n".join(lines)


def _wrap_details(summary: str, body: str) -> str:
    if not body.strip():
        return ""
    return "\n".join([
        "<details>",
        f"<summary>{_cell(summary)}</summary>",
        "",
        body.strip(),
        "",
        "</details>",
    ])


def _render_mermaid_diagram(
    section: dict,
    analysis_units: list[dict],
    common_context: dict,
) -> str:
    labels = _unit_labels(analysis_units, common_context)
    heading = str(section.get("heading") or "").lower()
    lines = ["### CodeTalk Diagram", "", "```mermaid"]
    if "flow" in heading or "流程" in heading:
        lines.append("flowchart TD")
        previous = "Start"
        lines.append('  Start(["开始"])')
        for idx, label in enumerate(labels, start=1):
            node = f"N{idx}"
            lines.append(f'  {node}["{_mermaid_label(label)}"]')
            lines.append(f"  {previous} --> {node}")
            previous = node
        lines.append('  End(["可观测结果"])')
        lines.append(f"  {previous} --> End")
    else:
        lines.append("graph TD")
        lines.append('  Root["Analysis scope"]')
        for idx, label in enumerate(labels, start=1):
            node = f"N{idx}"
            lines.append(f'  {node}["{_mermaid_label(label)}"]')
            lines.append(f"  Root --> {node}")
    lines.append("```")
    return "\n".join(lines)


def _render_sfmea_table(
    analysis_units: list[dict],
    common_context: dict,
    *,
    max_rows: int,
) -> str:
    labels = _unit_labels(analysis_units, common_context)[:max_rows]
    lines = [
        "### CodeTalk SFMEA Grid",
        "",
        "| Function/flow | Failure mode | Trigger | Injection point | Propagation | Impact | Observable signal | Severity | Probability | Detectability | Suggested test |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for label in labels:
        item = _cell(label)
        lines.append(
            f"| {item} | Evidence drift or missing branch | External input/config/state variation | Boundary/API/log observable | Downstream behavior may diverge | Incorrect result or hidden failure | Log/status/result mismatch | Medium | Unknown | Medium | Reproduce trigger and compare with CodeTalk evidence table |"
        )
    return "\n".join(lines)


def _unit_labels(analysis_units: list[dict], common_context: dict) -> list[str]:
    labels: list[str] = []
    for unit in analysis_units or []:
        title = str(unit.get("title") or "").strip()
        if title:
            labels.append(title)
    if not labels:
        for obj in common_context.get("analysis_objects") or []:
            text = str(obj.get("text") or "").strip()
            if text:
                labels.append(text)
    return labels[:8] or ["Analysis scope"]


def _attr(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _join_cell(values: list[Any]) -> str:
    items = [str(v).replace("\n", " ").strip() for v in values if str(v).strip()]
    return "; ".join(items) if items else "none"


def _limit(values: Any, count: int = 3) -> list[Any]:
    return list(values or [])[:count]


def _dedupe(values: list[Any]) -> list[Any]:
    out = []
    seen = set()
    for value in values:
        key = str(value)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _mermaid_label(value: str) -> str:
    return (
        value.replace("\\", "/")
        .replace('"', "'")
        .replace("[", "(")
        .replace("]", ")")
        .replace("\n", " ")
    )[:80]
