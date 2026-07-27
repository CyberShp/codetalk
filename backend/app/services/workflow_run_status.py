"""Pure V3 workflow run status reduction.

The workflow runner, event store, and API all use this module so a delivery
state cannot be guessed from legacy quality labels.  It deliberately has no
knowledge of workflow domains, providers, or artifacts.
"""

from __future__ import annotations

from typing import Final


EXECUTION_STATUSES: Final = frozenset({
    "queued", "running", "waiting_for_input", "completed", "failed", "cancelled", "timed_out",
})
ARTIFACT_VALIDATION_STATUSES: Final = frozenset({
    "not_requested", "not_started", "running", "passed", "failed",
})
GOVERNANCE_STATUSES: Final = frozenset({
    "not_requested", "running", "passed", "warning", "failed", "waived",
})
DELIVERY_STATUSES: Final = frozenset({"pending", "ready", "blocked"})


def derive_delivery_status(
    *,
    execution_status: str,
    artifact_validation_status: str,
    governance_status: str,
) -> str:
    """Return the only delivery state permitted by the target architecture."""
    _validate_axis_values(
        execution_status=execution_status,
        artifact_validation_status=artifact_validation_status,
        governance_status=governance_status,
    )
    if execution_status in {"queued", "running", "waiting_for_input"}:
        return "pending"
    if execution_status in {"failed", "cancelled", "timed_out"}:
        return "blocked"
    if artifact_validation_status in {"not_started", "running"}:
        return "pending"
    if artifact_validation_status == "failed":
        return "blocked"
    if governance_status == "running":
        return "pending"
    if governance_status == "failed":
        return "blocked"
    return "ready"


def validate_status_axes(
    *,
    execution_status: str,
    artifact_validation_status: str,
    governance_status: str,
    delivery_status: str,
) -> None:
    """Reject invalid values and combinations before they reach the projection."""
    _validate_axis_values(
        execution_status=execution_status,
        artifact_validation_status=artifact_validation_status,
        governance_status=governance_status,
    )
    if delivery_status not in DELIVERY_STATUSES:
        raise ValueError(f"invalid delivery status: {delivery_status}")
    expected = derive_delivery_status(
        execution_status=execution_status,
        artifact_validation_status=artifact_validation_status,
        governance_status=governance_status,
    )
    if delivery_status != expected:
        raise ValueError(
            "invalid workflow status combination: "
            f"delivery_status={delivery_status}, expected={expected}"
        )


def legacy_quality_status(
    *,
    execution_status: str,
    artifact_validation_status: str,
    governance_status: str,
) -> str:
    """Project V3 axes into the existing quality label without losing V3 data."""
    delivery_status = derive_delivery_status(
        execution_status=execution_status,
        artifact_validation_status=artifact_validation_status,
        governance_status=governance_status,
    )
    if delivery_status == "pending":
        return "pending"
    if delivery_status == "blocked":
        return "blocked"
    if governance_status == "warning":
        return "warning"
    if artifact_validation_status == "not_requested" and governance_status == "not_requested":
        return "not_checked"
    return "passed"


def legacy_delivery_status(*, delivery_status: str) -> str:
    """Keep old cockpit/API consumers readable while V3 exposes four axes."""
    if delivery_status == "ready":
        return "complete"
    if delivery_status == "pending":
        return "none"
    if delivery_status == "blocked":
        return "none"
    raise ValueError(f"invalid delivery status: {delivery_status}")


def _validate_axis_values(
    *,
    execution_status: str,
    artifact_validation_status: str,
    governance_status: str,
) -> None:
    if execution_status not in EXECUTION_STATUSES:
        raise ValueError(f"invalid execution status: {execution_status}")
    if artifact_validation_status not in ARTIFACT_VALIDATION_STATUSES:
        raise ValueError(f"invalid artifact validation status: {artifact_validation_status}")
    if governance_status not in GOVERNANCE_STATUSES:
        raise ValueError(f"invalid governance status: {governance_status}")
