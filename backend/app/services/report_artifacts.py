"""Deterministic report artifacts rendered by CodeTalk.

The LLM is allowed to write narrative content.  Layout-heavy structures that
often break on small internal models (tables, diagrams, SFMEA grids) are
rendered here from the evidence objects the pipeline already owns.
"""

from __future__ import annotations

from typing import Any


def build_codetalk_section_artifacts(
    *,
    section: dict,
    analysis_units: list[dict],
    evidence_cards: list[Any],
    common_context: dict,
    max_rows: int = 8,
) -> str:
    """Return Markdown artifacts for one report section.

    Every section gets a compact evidence table.  Sections that declare
    ``requires_mermaid`` or ``requires_sfmea`` get deterministic graph/table
    blocks as well, so the LLM does not have to maintain fragile Markdown
    structures.
    """

    parts: list[str] = []

    evidence_rows = _evidence_rows(
        analysis_units,
        evidence_cards,
        common_context,
        max_rows=max_rows,
    )
    parts.append(_render_evidence_table(evidence_rows))

    if section.get("requires_mermaid"):
        parts.append(_render_mermaid_diagram(section, analysis_units, common_context))

    if section.get("requires_sfmea"):
        parts.append(_render_sfmea_table(analysis_units, common_context, max_rows=max_rows))

    return "\n\n".join(part for part in parts if part.strip()).strip()


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
        lines.append('  Start(["Start"])')
        for idx, label in enumerate(labels, start=1):
            node = f"N{idx}"
            lines.append(f'  {node}["{_mermaid_label(label)}"]')
            lines.append(f"  {previous} --> {node}")
            previous = node
        lines.append('  End(["Observable result"])')
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


def _mermaid_label(value: str) -> str:
    return (
        value.replace("\\", "/")
        .replace('"', "'")
        .replace("[", "(")
        .replace("]", ")")
        .replace("\n", " ")
    )[:80]
