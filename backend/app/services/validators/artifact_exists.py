"""Read-only checks for declared artifact files."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .common import inspect_regular_file, result_from_issues, selected_declarations
from .contracts import ValidationIssue, ValidationResult


def validate_artifact_exists(
    *,
    artifact_root: Path,
    declared_outputs: Iterable[object],
    required_output_ids: Iterable[str],
    node_id: str = "",
    **_unused: object,
) -> ValidationResult:
    validator_id = "artifact_exists"
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
    for declaration in selected:
        path, issue = inspect_regular_file(
            root=artifact_root,
            relative_path=declaration.artifact,
            output_id=declaration.output_id,
            node_id=node_id,
        )
        if issue is not None:
            issues.append(issue)
            continue
        assert path is not None
        try:
            if path.stat().st_size == 0:
                issues.append(
                    ValidationIssue(
                        code="artifact_empty",
                        message="声明产物为空。",
                        node_id=node_id,
                        output_id=declaration.output_id,
                        path=declaration.artifact,
                    )
                )
        except OSError as exc:
            issues.append(
                ValidationIssue(
                    code="artifact_unreadable",
                    message="声明产物不可访问。",
                    node_id=node_id,
                    output_id=declaration.output_id,
                    path=declaration.artifact,
                    details={"error_type": type(exc).__name__},
                )
            )
    return result_from_issues(
        validator_id=validator_id,
        issues=issues,
        validated_output_ids=tuple(item.output_id for item in selected),
    )
