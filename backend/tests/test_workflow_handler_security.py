from __future__ import annotations

from pathlib import Path

import pytest

from app.services.governance_plugins.contracts import (
    GeneratedGovernanceArtifact,
    GovernancePluginResult,
)
from app.services.workflow_handler_dispatcher import (
    WorkflowHandlerDispatcher,
    WorkflowHandlerRequest,
)


class _Registry:
    def __init__(self, result: GovernancePluginResult | None = None) -> None:
        self.result = result
        self.invoked = False

    def availability_snapshot(self) -> list[dict]:
        return [
            {
                "handler_id": "storage_test_design",
                "handler_version": 1,
                "node_kind": "governance",
                "available": True,
            }
        ]

    def invoke(self, _request):
        self.invoked = True
        if self.result is None:
            raise AssertionError("invalid bound input must fail before plugin invocation")
        return self.result


def _request(
    tmp_path: Path,
    *,
    reference: dict | None = None,
    media_type: str = "application/json",
) -> WorkflowHandlerRequest:
    task_dir = tmp_path / "task"
    task_dir.mkdir(exist_ok=True)
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return WorkflowHandlerRequest(
        handler_id="storage_test_design",
        handler_version=1,
        node_id="design",
        node_kind="governance",
        task_artifact_dir=task_dir,
        source_root=repo,
        declared_outputs=(
            {
                "output_id": "risk_report",
                "artifact": "risk-register.json",
                "media_type": media_type,
                "producer_step_id": "design",
                "producer_port_id": "port_sfmea",
            },
        ),
        required_output_ids=("risk_report",),
        inputs={"source_evidence": reference} if reference is not None else {},
    )


def _reference(root: Path, *, media_type: str) -> dict:
    return {
        "__workflow_artifact_ref__": True,
        "output_id": "evidence",
        "artifact_root": str(root),
        "artifact": "evidence.bin",
        "media_type": media_type,
    }


@pytest.mark.parametrize(
    ("fixture", "media_type", "error_code"),
    [
        ("too_large", "application/json", "governance_input_too_large"),
        ("non_utf8", "text/plain", "governance_input_text_invalid"),
        (
            "unsupported_media",
            "application/octet-stream",
            "governance_input_media_type_unsupported",
        ),
        ("symlink", "application/json", "governance_input_symlink_rejected"),
    ],
)
def test_bound_governance_artifact_fails_closed_before_plugin(
    tmp_path: Path,
    fixture: str,
    media_type: str,
    error_code: str,
) -> None:
    artifact_root = tmp_path / "upstream"
    artifact_root.mkdir()
    artifact = artifact_root / "evidence.bin"
    if fixture == "too_large":
        with artifact.open("wb") as stream:
            stream.truncate(16 * 1024 * 1024 + 1)
    elif fixture == "non_utf8":
        artifact.write_bytes(b"\xff\xfe")
    elif fixture == "unsupported_media":
        artifact.write_bytes(b"opaque")
    else:
        outside = tmp_path / "outside.json"
        outside.write_text("[]", encoding="utf-8")
        artifact.symlink_to(outside)
    registry = _Registry()
    dispatcher = WorkflowHandlerDispatcher(
        governance_registry_factory=lambda: registry
    )

    result = dispatcher.dispatch(
        _request(
            tmp_path,
            reference=_reference(artifact_root, media_type=media_type),
        )
    )

    assert result.status == "failed"
    assert result.axis == "governance"
    assert result.error_code == error_code
    assert result.provider_failed is False
    assert registry.invoked is False


def test_governance_candidate_media_type_must_match_declared_output(
    tmp_path: Path,
) -> None:
    plugin_result = GovernancePluginResult(
        handler_id="storage_test_design",
        handler_version=1,
        node_id="design",
        node_kind="governance",
        governance_status="passed",
        delivery_status="ready",
        produced_artifacts=(
            GeneratedGovernanceArtifact(
                artifact_id="risk_report",
                content="# wrong representation\n",
                media_type="text/markdown",
            ),
        ),
    )
    registry = _Registry(plugin_result)
    dispatcher = WorkflowHandlerDispatcher(
        governance_registry_factory=lambda: registry
    )

    result = dispatcher.dispatch(_request(tmp_path))

    assert registry.invoked is True
    assert result.status == "failed"
    assert result.error_code == "governance_output_media_type_mismatch"
    assert result.details == {
        "output_id": "risk_report",
        "declared_media_type": "application/json",
        "candidate_media_type": "text/markdown",
    }
    assert not (tmp_path / "task" / "governance_runs" / "design").exists()
