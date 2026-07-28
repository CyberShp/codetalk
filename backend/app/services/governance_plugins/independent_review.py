"""Read-only independent review of explicitly declared storage artifacts."""

from __future__ import annotations

import asyncio
import copy
import inspect
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Coroutine

from app.services.governance_plugins.contracts import (
    GovernancePluginExecution,
    GovernancePluginRequest,
    ValidationIssue,
    ValidationResult,
)


class IndependentReviewPlugin:
    def execute(self, request: GovernancePluginRequest) -> GovernancePluginExecution:
        from app.services.behavior_claim_validator import (
            build_behavior_claim_validation_request,
            materialize_behavior_claim_validation,
        )
        from app.services.test_activity_contract import audit_test_activity_artifacts

        root = Path(request.artifact_dir).resolve(strict=True)
        declarations = {
            output.artifact_id: output for output in request.declared_outputs
        }
        required_declarations = [
            declarations[artifact_id] for artifact_id in request.required_output_ids
        ]
        contract = _contract_for_required_outputs(
            dict(request.inputs.get("contract") or {}),
            [output.path for output in required_declarations],
        )
        with tempfile.TemporaryDirectory(
            prefix=".independent-review-",
            dir=root,
        ) as snapshot_dir:
            snapshot = Path(snapshot_dir)
            for declaration in required_declarations:
                _copy_regular_bound_artifact(
                    root=root,
                    snapshot=snapshot,
                    relative_path=declaration.path,
                )
            repo_path = str(request.inputs.get("repo_path") or "")
            review_request = build_behavior_claim_validation_request(
                artifact_dir=snapshot,
                repo_path=repo_path,
            )
            if review_request.get("claims"):
                _run_async_blocking(
                    materialize_behavior_claim_validation(
                        artifact_dir=snapshot,
                        repo_path=repo_path,
                        generator_identity=_configured_review_generator_identity(request),
                        request=review_request,
                        builtin_audit_loader=_load_configured_audit,
                    )
                )
                quality_gates = contract.get("quality_gates")
                quality_gates = (
                    copy.deepcopy(quality_gates)
                    if isinstance(quality_gates, dict)
                    else {}
                )
                quality_gates["require_independent_behavior_validation"] = True
                contract["quality_gates"] = quality_gates
            audit = audit_test_activity_artifacts(
                artifact_dir=snapshot,
                contract=contract,
                repo_path=repo_path,
            )
        raw_issues = list(audit.get("issues") or [])
        issues = tuple(
            ValidationIssue(
                code=str(item.get("code") or "independent_review_failed"),
                message=str(item.get("message") or "独立审查未通过。"),
                artifact_id=str(item.get("artifact") or ""),
                details={
                    key: value
                    for key, value in item.items()
                    if key not in {"code", "message", "artifact"}
                },
            )
            for item in raw_issues
            if isinstance(item, dict)
        )
        status = "passed" if bool(audit.get("deliverable")) else "failed"
        return GovernancePluginExecution(
            status=status,
            validation=ValidationResult(status=status, issues=issues),
            error_code="independent_review_failed" if status == "failed" else "",
        )


def create_plugin() -> IndependentReviewPlugin:
    return IndependentReviewPlugin()


def _configured_review_generator_identity(request: GovernancePluginRequest) -> str:
    identity = str(request.inputs.get("generator_identity") or "").strip()
    if identity.startswith("builtin-llm:"):
        return identity
    return f"builtin-llm:{identity or 'unknown'}"


async def _load_configured_audit() -> Any:
    from app.llm.factory import create_behavior_claim_audit_llm_client

    loaded = create_behavior_claim_audit_llm_client()
    configured = await loaded if inspect.isawaitable(loaded) else loaded
    if configured is None:
        raise RuntimeError("未配置独立质量核验模型")
    return configured


def _run_async_blocking(coroutine: Coroutine[Any, Any, Any]) -> Any:
    """Run the existing async reviewer from sync plugin dispatch."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)

    result: list[Any] = []
    failure: list[BaseException] = []

    def run() -> None:
        try:
            result.append(asyncio.run(coroutine))
        except BaseException as exc:  # Re-raise on the dispatching thread.
            failure.append(exc)

    worker = threading.Thread(target=run, name="independent-review", daemon=True)
    worker.start()
    worker.join()
    if failure:
        raise failure[0]
    return result[0]


def _contract_for_required_outputs(
    contract: dict,
    required_paths: list[str],
) -> dict:
    filtered = copy.deepcopy(contract)
    artifact_contract = contract.get("artifact_contract")
    artifact_contract = artifact_contract if isinstance(artifact_contract, dict) else {}
    filtered["artifact_contract"] = {
        path: copy.deepcopy(artifact_contract.get(path) or {})
        for path in required_paths
    }
    filtered["required_outputs"] = list(required_paths)
    return filtered


def _copy_regular_bound_artifact(
    *,
    root: Path,
    snapshot: Path,
    relative_path: str,
) -> None:
    source = root / relative_path
    if source.is_symlink() or not source.is_file():
        return
    try:
        resolved = source.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return
    destination = snapshot / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(resolved, destination, follow_symlinks=False)
