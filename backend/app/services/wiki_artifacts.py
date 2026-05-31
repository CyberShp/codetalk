"""Deterministic wiki layout artifacts rendered by CodeTalk."""

from __future__ import annotations


def attach_codetalk_wiki_artifacts(
    *,
    page_title: str,
    file_paths: list[str],
    ai_content: str,
    max_files: int = 8,
) -> str:
    """Prepend stable source and diagram blocks to an AI-written wiki page."""

    files = [p.strip() for p in file_paths if p and p.strip()][:max_files]
    parts = [
        _render_source_details(files),
        _render_source_table(files),
        _render_page_graph(page_title, files),
        ai_content.strip(),
    ]
    return "\n\n".join(part for part in parts if part.strip()).strip()


def _render_source_details(file_paths: list[str]) -> str:
    lines = [
        "<details>",
        "<summary>CodeTalk source files</summary>",
        "",
    ]
    if file_paths:
        lines.extend(f"- `{path}`" for path in file_paths)
    else:
        lines.append("- No source files were bound to this page.")
    lines.extend(["", "</details>"])
    return "\n".join(lines)


def _render_source_table(file_paths: list[str]) -> str:
    lines = [
        "### CodeTalk Source Table",
        "",
        "| Source file | Role | Notes |",
        "| --- | --- | --- |",
    ]
    if file_paths:
        for idx, path in enumerate(file_paths, start=1):
            lines.append(
                f"| `{_cell(path)}` | Relevant file {idx} | Bound by DeepWiki structure selection |"
            )
    else:
        lines.append("| (none) | Missing source binding | Validate repository indexing |")
    return "\n".join(lines)


def _render_page_graph(page_title: str, file_paths: list[str]) -> str:
    title = _mermaid_label(page_title or "Wiki page")
    lines = [
        "### CodeTalk Page Graph",
        "",
        "```mermaid",
        "graph TD",
        f'  Page["{title}"]',
    ]
    if file_paths:
        for idx, path in enumerate(file_paths, start=1):
            node = f"F{idx}"
            lines.append(f'  {node}["{_mermaid_label(path)}"]')
            lines.append(f"  Page --> {node}")
    else:
        lines.append('  Missing["No bound files"]')
        lines.append("  Page --> Missing")
    lines.append("```")
    return "\n".join(lines)


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
