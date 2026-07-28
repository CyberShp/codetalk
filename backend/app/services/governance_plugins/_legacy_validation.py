"""Lazy adapters around the current professional audit implementation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services import legacy_workflow_execution as legacy_execution
from app.services.governance_plugins.contracts import (
    GovernancePluginExecution,
    GovernancePluginRequest,
    ValidationIssue,
    ValidationResult,
)


def validate_json_artifact(
    request: GovernancePluginRequest,
    *,
    artifact_name: str,
) -> GovernancePluginExecution:
    declaration = next(
        output
        for output in request.declared_outputs
        if output.artifact_id in request.required_output_ids
    )
    path = Path(request.artifact_dir) / declaration.path
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = None
    canonical_spec = dict(legacy_execution.ARTIFACT_TEMPLATES[artifact_name])
    raw_issues = legacy_execution._audit_json_artifact(
        artifact=artifact_name,
        payload=payload,
        spec=canonical_spec,
        repo=Path(str(request.inputs.get("repo_path") or "")),
    )
    issues = tuple(_validation_issue(item, declaration.artifact_id) for item in raw_issues)
    status = "failed" if issues else "passed"
    return GovernancePluginExecution(
        status=status,
        validation=ValidationResult(status=status, issues=issues),
        error_code="governance_validation_failed" if issues else "",
    )


def _validation_issue(payload: dict[str, Any], artifact_id: str) -> ValidationIssue:
    return ValidationIssue(
        code=str(payload.get("code") or "professional_validation_failed"),
        message=str(payload.get("message") or "专业校验未通过。"),
        artifact_id=artifact_id,
        details={
            key: value
            for key, value in payload.items()
            if key not in {"code", "message", "artifact"}
        },
    )
