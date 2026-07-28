"""Read-only truth checks for structured source evidence cards."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

from .common import inspect_regular_file, result_from_issues, selected_declarations
from .contracts import ValidationIssue, ValidationResult


def validate_source_evidence(
    *,
    artifact_root: Path,
    source_root: Path,
    declared_outputs: Iterable[object],
    required_output_ids: Iterable[str],
    node_id: str = "",
    **_unused: object,
) -> ValidationResult:
    validator_id = "source_evidence"
    selected, error = selected_declarations(
        validator_id=validator_id,
        declared_outputs=declared_outputs,
        required_output_ids=required_output_ids,
        node_id=node_id,
    )
    if error is not None:
        return error
    assert selected is not None
    issues: list[ValidationIssue] = []
    verified_count = 0
    for declaration in selected:
        artifact, artifact_issue = inspect_regular_file(
            root=artifact_root,
            relative_path=declaration.artifact,
            output_id=declaration.output_id,
            node_id=node_id,
        )
        if artifact_issue is not None:
            issues.append(artifact_issue)
            continue
        assert artifact is not None
        try:
            cards = json.loads(artifact.read_text(encoding="utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            issues.append(ValidationIssue(code="artifact_invalid_json", message="源码证据产物不是有效 JSON。", output_id=declaration.output_id, path=declaration.artifact, details={"error_type": type(exc).__name__}))
            continue
        if not isinstance(cards, list) or not cards or not all(isinstance(card, dict) for card in cards):
            issues.append(ValidationIssue(code="source_evidence_invalid_structure", message="源码证据必须是非空的结构化卡片数组。", output_id=declaration.output_id, path=declaration.artifact))
            continue
        for index, card in enumerate(cards):
            issue = _validate_card(
                card,
                index=index,
                output_id=declaration.output_id,
                source_root=source_root,
                node_id=node_id,
            )
            if issue is not None:
                issues.append(issue)
            else:
                verified_count += 1
    return result_from_issues(
        validator_id=validator_id,
        issues=issues,
        validated_output_ids=tuple(item.output_id for item in selected),
        details={"verified_evidence_count": verified_count},
    )


def _validate_card(
    card: dict,
    *,
    index: int,
    output_id: str,
    source_root: Path,
    node_id: str,
) -> ValidationIssue | None:
    file_path = card.get("file_path")
    start = card.get("start_line")
    end = card.get("end_line")
    excerpt = card.get("excerpt")
    symbols = card.get("symbols")
    digest = card.get("sha256")
    if not isinstance(file_path, str) or not isinstance(start, int) or isinstance(start, bool) or not isinstance(end, int) or isinstance(end, bool) or not isinstance(excerpt, str) or not isinstance(symbols, list) or not symbols or not all(isinstance(item, str) and item for item in symbols) or not isinstance(digest, str) or not digest:
        return _card_issue("source_evidence_invalid_card", "源码证据卡字段不完整或类型错误。", output_id, str(file_path or ""), index)
    source, path_issue = inspect_regular_file(
        root=source_root,
        relative_path=file_path,
        output_id=output_id,
        code_prefix="source",
        node_id=node_id,
    )
    if path_issue is not None:
        return ValidationIssue(
            code=path_issue.code,
            message=path_issue.message,
            node_id=node_id,
            output_id=output_id,
            path=file_path,
            details={"card_index": index},
        )
    assert source is not None
    try:
        data = source.read_bytes()
        text = data.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        return _card_issue("source_unreadable", "源码文件无法按 UTF-8 读取。", output_id, file_path, index, {"error_type": type(exc).__name__})
    lines = text.splitlines()
    if start < 1 or end < start or end > len(lines):
        return _card_issue("source_line_range_invalid", "源码证据行号超出文件范围。", output_id, file_path, index, {"line_count": len(lines), "start_line": start, "end_line": end})
    selected = "\n".join(lines[start - 1 : end])
    if excerpt.rstrip("\n") != selected:
        return _card_issue("source_excerpt_mismatch", "源码证据片段与指定行号不一致。", output_id, file_path, index)
    missing_symbols = sorted(symbol for symbol in symbols if symbol not in selected)
    if missing_symbols:
        return _card_issue("source_symbol_missing", "源码证据中的符号未出现在指定片段。", output_id, file_path, index, {"missing_symbols": missing_symbols})
    actual_digest = hashlib.sha256(data).hexdigest()
    if digest.lower() != actual_digest:
        return _card_issue("source_sha256_mismatch", "源码证据摘要与文件内容不一致。", output_id, file_path, index, {"actual_sha256": actual_digest})
    return None


def _card_issue(code: str, message: str, output_id: str, path: str, index: int, details: dict | None = None) -> ValidationIssue:
    payload = {"card_index": index}
    payload.update(details or {})
    return ValidationIssue(code=code, message=message, output_id=output_id, path=path, details=payload)
