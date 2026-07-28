"""Stable, domain-neutral contracts returned by read-only validators."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    node_id: str = ""
    output_id: str = ""
    path: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationResult:
    """A validation outcome that is deliberately distinct from provider state."""

    validator_id: str
    status: Literal["passed", "failed"]
    issues: tuple[ValidationIssue, ...] = ()
    validated_output_ids: tuple[str, ...] = ()
    failure_kind: str = "validation_failed"
    provider_failed: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def passed(
        cls,
        *,
        validator_id: str,
        validated_output_ids: tuple[str, ...] = (),
        details: dict[str, Any] | None = None,
    ) -> "ValidationResult":
        return cls(
            validator_id=validator_id,
            status="passed",
            validated_output_ids=validated_output_ids,
            details=dict(details or {}),
        )

    @classmethod
    def failed(
        cls,
        *,
        validator_id: str,
        code: str,
        message: str,
        node_id: str = "",
        output_id: str = "",
        path: str = "",
        details: dict[str, Any] | None = None,
        validated_output_ids: tuple[str, ...] = (),
    ) -> "ValidationResult":
        issue = ValidationIssue(
            code=code,
            message=message,
            node_id=node_id,
            output_id=output_id,
            path=path,
            details=dict(details or {}),
        )
        return cls(
            validator_id=validator_id,
            status="failed",
            issues=(issue,),
            validated_output_ids=validated_output_ids,
        )


@dataclass(frozen=True)
class DeclaredOutput:
    output_id: str
    artifact: str
    required: bool
    schema: object = None

