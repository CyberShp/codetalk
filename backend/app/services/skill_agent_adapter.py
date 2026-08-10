"""Execute frozen Skill steps through the production Agent Harness seam."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.services.external_agent_discovery import redact_agent_diagnostic_text
from app.services.harness_facade import AgentHarnessFacade, HarnessRunRequest
from app.services.provider_adapters.contracts import (
    ProviderResumeToken,
    ProviderUnsupported,
)
from app.services.provider_adapters.registry import create_provider_adapter


class SkillAgentAdapterError(RuntimeError):
    """A frozen Skill step could not execute through a real provider."""


def execute_skill_step(
    *,
    task_run: Any,
    node: dict[str, Any],
    invocation: dict[str, Any],
    resolved_inputs: dict[str, Any],
    execution_profile: dict[str, Any],
    prior_step_results: list[dict[str, Any]],
    timeout_sec: int = 0,
    event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    node_id = str(node.get("node_id") or "")
    _validate_invocation_digest(invocation)
    producer = _producer_runtime(invocation)
    task_dir = Path(str(task_run.artifact_dir)).expanduser().resolve()
    artifact_root = _bounded_artifact_root(task_dir, invocation)
    definition = _compiled_definition(task_run)
    skill_ir = _frozen_skill_ir(task_dir, invocation)
    _validate_frozen_source_inventory(task_dir, skill_ir)
    step = next(
        (
            dict(item)
            for item in skill_ir.get("steps") or []
            if isinstance(item, dict)
            and str(item.get("step_id") or item.get("id") or "") == node_id
        ),
        {},
    )
    if not step:
        raise SkillAgentAdapterError("skill_step_instruction_missing")
    required_artifacts = _required_artifact_paths(skill_ir, step)
    if not required_artifacts:
        raise SkillAgentAdapterError("skill_step_artifact_contract_missing")
    independent_review = _is_independent_review_step(step)
    step_runtime = (
        _independent_judge_runtime(invocation)
        if independent_review
        else producer
    )
    execution = step_runtime["execution"]
    frozen_input_snapshot = _frozen_input_snapshot(task_dir, invocation)
    selected_workflow_path = str(skill_ir.get("selected_workflow_path") or "")
    selected_workflow = _bounded_skill_source_path(
        task_dir,
        selected_workflow_path,
        error="skill_selected_workflow_missing",
    )
    instruction_path = str(step.get("instruction_path") or "")
    instruction = _bounded_skill_source_path(
        task_dir,
        instruction_path,
        error="skill_step_instruction_missing",
    )
    skill_entrypoint = _bounded_skill_source_path(
        task_dir,
        "SKILL.md",
        error="skill_entrypoint_missing",
    )
    core_rules = _bounded_core_rules(task_dir, skill_ir)
    profile = dict(execution_profile or {})
    timeout_budget = (
        step_runtime.get("timeout_budget")
        if isinstance(step_runtime.get("timeout_budget"), dict)
        else {}
    )
    if not str(profile.get("id") or "") and str(
        timeout_budget.get("profile_id") or ""
    ):
        profile["id"] = str(timeout_budget["profile_id"])
    predecessor_artifacts = _verified_predecessor_artifacts(
        prior_step_results,
        artifact_root=artifact_root,
    )
    effective_timeout_seconds = _effective_step_timeout_seconds(
        timeout_budget,
        remaining_timeout_seconds=timeout_sec,
    )
    hard_timeout_kind = _hard_timeout_kind(
        timeout_budget,
        remaining_timeout_seconds=timeout_sec,
    )
    idle_timeout_seconds = _positive_float(
        timeout_budget.get("idle_timeout_seconds")
    )
    adapter = create_provider_adapter(
        provider=str(execution.get("provider_ref") or ""),
        prompt_transport=str(execution.get("prompt_transport") or ""),
        artifact_dir=artifact_root,
    )
    if adapter is None:
        raise SkillAgentAdapterError("skill_agent_adapter_unavailable")
    request = HarnessRunRequest(
        provider=str(execution.get("provider_ref") or ""),
        command=[str(item) for item in execution.get("command") or []],
        cwd=str(task_run.repo_path),
        workflow_snapshot=definition,
        task_bundle={
            "rendered_user_input": _render_step_prompt(
                artifact_root=artifact_root,
                node_id=node_id,
                step=step,
                selected_workflow=selected_workflow,
                selected_workflow_path=selected_workflow_path,
                instruction=instruction,
                instruction_path=instruction_path,
                skill_entrypoint=skill_entrypoint,
                core_rules=core_rules,
                frozen_input_snapshot=frozen_input_snapshot,
                resolved_inputs=resolved_inputs,
                execution_profile=profile,
                predecessor_artifacts=predecessor_artifacts,
                required_artifacts=required_artifacts,
                timeout_seconds=effective_timeout_seconds,
                independent_review=independent_review,
            ),
            "required_artifacts": required_artifacts,
            "skill_invocation": invocation,
            "provider_snapshot": _provider_snapshot(execution),
            "resolved_inputs": _request_resolved_inputs(resolved_inputs),
            "execution_profile": profile,
            "frozen_input_snapshot": frozen_input_snapshot,
            "selected_workflow_path": selected_workflow_path,
            "step_instruction_path": instruction_path,
            "skill_entrypoint": {
                "ref": "SKILL.md",
                "path": str(skill_entrypoint),
            },
            "core_rules": core_rules,
            "predecessor_artifacts": predecessor_artifacts,
            "execution_role": (
                "independent_judge" if independent_review else "producer"
            ),
        },
        mcp_profile=str(execution.get("mcp_profile") or ""),
        prompt_transport=str(execution.get("prompt_transport") or ""),
        timeout_seconds=effective_timeout_seconds,
        idle_timeout_seconds=idle_timeout_seconds,
        requires_network=bool(execution.get("requires_network", True)),
        run_id=(
            f"{task_run.task_run_id}_skill_independent_"
            f"{hashlib.sha256(node_id.encode('utf-8')).hexdigest()[:12]}"
            if independent_review
            else f"{task_run.task_run_id}_skill_producer"
        ),
    )
    facade = AgentHarnessFacade(artifact_root, adapter=adapter)
    session = facade.prepare(request)
    scoped_event_sink = _step_scoped_event_sink(event_sink, node_id=node_id)
    resume_token = None if independent_review else _load_producer_resume_token(task_dir)
    if resume_token is None:
        result = facade.execute(
            session,
            timeout_sec=request.timeout_seconds or 0,
            idle_timeout_sec=request.idle_timeout_seconds,
            is_cancelled=is_cancelled,
            event_sink=scoped_event_sink,
        )
    else:
        result = facade.resume(
            session,
            resume_token,
            timeout_sec=request.timeout_seconds or 0,
            idle_timeout_sec=request.idle_timeout_seconds,
            is_cancelled=is_cancelled,
            event_sink=scoped_event_sink,
        )
    if isinstance(result, ProviderUnsupported):
        raise SkillAgentAdapterError(result.code or "skill_agent_operation_unsupported")
    if str(result.status or "") != "completed":
        return _provider_failure_step_result(
            node_id=node_id,
            artifact_root=artifact_root,
            required_artifacts=required_artifacts,
            result=result,
            hard_timeout_kind=hard_timeout_kind,
        )
    if not independent_review:
        _persist_producer_resume_token(task_dir, result.provider_diagnostics)
    missing = [
        path for path in required_artifacts if not (artifact_root / path).is_file()
    ]
    if missing:
        return _provider_failure_step_result(
            node_id=node_id,
            artifact_root=artifact_root,
            required_artifacts=required_artifacts,
            result=result,
            error="skill_step_required_artifacts_missing:" + ",".join(missing),
            hard_timeout_kind=hard_timeout_kind,
        )
    if independent_review and not _independent_review_state_is_valid(
        skill_ir,
        artifact_root=artifact_root,
    ):
        return _provider_failure_step_result(
            node_id=node_id,
            artifact_root=artifact_root,
            required_artifacts=required_artifacts,
            result=result,
            error="skill_independent_judge_state_invalid",
            hard_timeout_kind=hard_timeout_kind,
        )
    return {
        "step_id": node_id,
        "node_id": node_id,
        "type": "skill_step",
        "status": "completed",
        "artifact_dir": str(artifact_root),
        "agent_session_id": str(result.session_id),
        "duration_ms": int(result.duration_ms),
        "required_artifacts": required_artifacts,
        "artifacts": list(result.artifacts),
        "provider_diagnostics": dict(result.provider_diagnostics),
        "execution_role": (
            "independent_judge" if independent_review else "producer"
        ),
    }


def execute_skill_judge(
    *,
    task_run: Any,
    invocation: dict[str, Any],
    timeout_sec: int = 0,
    event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    _validate_invocation_digest(invocation)
    runtime = invocation.get("runtime")
    judge_runtime = runtime.get("judge") if isinstance(runtime, dict) else None
    execution = (
        judge_runtime.get("execution") if isinstance(judge_runtime, dict) else None
    )
    if not isinstance(execution, dict):
        raise SkillAgentAdapterError("skill_judge_runtime_unavailable")
    task_dir = Path(str(task_run.artifact_dir)).expanduser().resolve()
    skill_ir = _frozen_skill_ir(task_dir, invocation)
    _frozen_input_snapshot(task_dir, invocation)
    _validate_frozen_source_inventory(task_dir, skill_ir)
    definition = _compiled_definition(task_run)
    judge = invocation.get("judge") if isinstance(invocation.get("judge"), dict) else {}
    artifact_ids = [str(item) for item in judge.get("artifact_ids") or [] if str(item)]
    artifact_paths = _artifact_paths_by_id(definition, artifact_ids)
    if len(artifact_paths) != len(artifact_ids):
        raise SkillAgentAdapterError("skill_judge_artifact_contract_missing")
    artifact_root = _bounded_artifact_root(task_dir, invocation)
    missing = [path for path in artifact_paths if not (artifact_root / path).is_file()]
    if missing:
        raise SkillAgentAdapterError(
            "skill_judge_required_artifacts_missing:" + ",".join(missing)
        )
    adapter = create_provider_adapter(
        provider=str(execution.get("provider_ref") or ""),
        prompt_transport=str(execution.get("prompt_transport") or ""),
        artifact_dir=task_dir,
    )
    if adapter is None:
        raise SkillAgentAdapterError("skill_judge_adapter_unavailable")
    judge_timeout_budget = (
        judge_runtime.get("timeout_budget")
        if isinstance(judge_runtime.get("timeout_budget"), dict)
        else {}
    )
    judge_timeout_seconds = _effective_step_timeout_seconds(
        judge_timeout_budget,
        remaining_timeout_seconds=timeout_sec,
    )
    hard_timeout_kind = _hard_timeout_kind(
        judge_timeout_budget,
        remaining_timeout_seconds=timeout_sec,
    )
    judge_idle_timeout_seconds = _positive_float(
        judge_timeout_budget.get("idle_timeout_seconds")
    )
    request = HarnessRunRequest(
        provider=str(execution.get("provider_ref") or ""),
        command=[str(item) for item in execution.get("command") or []],
        cwd=str(task_run.repo_path),
        workflow_snapshot=definition,
        task_bundle={
            "rendered_user_input": _render_judge_prompt(
                task_dir=task_dir,
                artifact_root=artifact_root,
                artifact_ids=artifact_ids,
                artifact_paths=artifact_paths,
            ),
            "required_artifacts": ["skill_judge_report.json"],
            "skill_invocation": invocation,
            "provider_snapshot": _provider_snapshot(execution),
        },
        mcp_profile=str(execution.get("mcp_profile") or ""),
        prompt_transport=str(execution.get("prompt_transport") or ""),
        timeout_seconds=judge_timeout_seconds,
        idle_timeout_seconds=judge_idle_timeout_seconds,
        requires_network=bool(execution.get("requires_network", True)),
        run_id=f"{task_run.task_run_id}_skill_judge",
    )
    facade = AgentHarnessFacade(task_dir, adapter=adapter)
    session = facade.prepare(request)
    result = facade.execute(
        session,
        timeout_sec=request.timeout_seconds or 0,
        idle_timeout_sec=request.idle_timeout_seconds,
        is_cancelled=is_cancelled,
        event_sink=_step_scoped_event_sink(event_sink, node_id="skill.judge"),
    )
    if isinstance(result, ProviderUnsupported):
        raise SkillAgentAdapterError(result.code or "skill_judge_operation_unsupported")
    if str(result.status or "") != "completed":
        return _provider_failure_step_result(
            node_id="skill.judge",
            artifact_root=task_dir,
            required_artifacts=artifact_paths,
            result=result,
            result_type="skill_judge",
            governance_status="failed",
            hard_timeout_kind=hard_timeout_kind,
        )
    report_path = task_dir / "skill_judge_report.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SkillAgentAdapterError("skill_judge_report_invalid") from exc
    if not isinstance(report, dict):
        raise SkillAgentAdapterError("skill_judge_report_invalid")
    checked_ids = {
        str(item) for item in report.get("checked_artifact_ids") or [] if str(item)
    }
    if (
        report.get("ready") is not True
        or str(report.get("status") or "") not in {"READY", "READY_WITH_WARNINGS"}
        or not set(artifact_ids).issubset(checked_ids)
    ):
        raise SkillAgentAdapterError("skill_judge_rejected")
    return {
        "step_id": "skill.judge",
        "node_id": "skill.judge",
        "type": "skill_judge",
        "status": "completed",
        "artifact_dir": str(task_dir),
        "agent_session_id": str(result.session_id),
        "duration_ms": int(result.duration_ms),
        "artifacts": list(result.artifacts),
        "governance_status": "passed",
    }


def _producer_runtime(invocation: dict[str, Any]) -> dict[str, Any]:
    runtime = invocation.get("runtime")
    producer = runtime.get("producer") if isinstance(runtime, dict) else None
    execution = producer.get("execution") if isinstance(producer, dict) else None
    if not isinstance(producer, dict) or not isinstance(execution, dict):
        raise SkillAgentAdapterError("skill_agent_runtime_unavailable")
    if not execution.get("provider_ref") or not execution.get("command"):
        raise SkillAgentAdapterError("skill_agent_runtime_unavailable")
    return producer


def _independent_judge_runtime(invocation: dict[str, Any]) -> dict[str, Any]:
    runtime = invocation.get("runtime")
    judge = runtime.get("judge") if isinstance(runtime, dict) else None
    execution = judge.get("execution") if isinstance(judge, dict) else None
    if not isinstance(judge, dict) or not isinstance(execution, dict):
        raise SkillAgentAdapterError("skill_independent_judge_runtime_unavailable")
    if not execution.get("provider_ref") or not execution.get("command"):
        raise SkillAgentAdapterError("skill_independent_judge_runtime_unavailable")
    return judge


def _is_independent_review_step(step: dict[str, Any]) -> bool:
    gate = step.get("completion_gate")
    required = (
        gate.get("required_artifact_ids") if isinstance(gate, dict) else []
    )
    produced = step.get("produces") if isinstance(step.get("produces"), list) else []
    return "artifact.internal-judge-state" in {
        str(item) for item in [*(required or []), *produced]
    }


def _independent_review_state_is_valid(
    skill_ir: dict[str, Any],
    *,
    artifact_root: Path,
) -> bool:
    paths = _artifact_paths_by_id(
        skill_ir,
        ["artifact.internal-judge-state"],
    )
    if len(paths) != 1:
        return False
    relative = Path(paths[0])
    if relative.is_absolute() or ".." in relative.parts:
        return False
    try:
        root = artifact_root.resolve(strict=True)
        candidate = root / relative
        if candidate.is_symlink():
            return False
        path = candidate.resolve(strict=True)
        path.relative_to(root)
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or payload.get("independent") is not True:
        return False
    checked = payload.get("checked_artifacts")
    return isinstance(checked, list) and any(str(item or "") for item in checked)


def _step_scoped_event_sink(
    event_sink: Callable[[str, dict[str, Any]], None] | None,
    *,
    node_id: str,
) -> Callable[[str, dict[str, Any]], None] | None:
    if event_sink is None:
        return None

    def emit(event_type: str, payload: dict[str, Any]) -> None:
        scoped = dict(payload)
        scoped["step_id"] = node_id
        scoped["node_id"] = node_id
        event_sink(event_type, scoped)

    return emit


def _bounded_artifact_root(task_dir: Path, invocation: dict[str, Any]) -> Path:
    relative = Path(str(invocation.get("artifact_root") or "artifacts"))
    if relative.is_absolute() or ".." in relative.parts:
        raise SkillAgentAdapterError("skill_artifact_root_unbounded")
    root = (task_dir / relative).resolve()
    if not root.is_relative_to(task_dir):
        raise SkillAgentAdapterError("skill_artifact_root_unbounded")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _compiled_definition(task_run: Any) -> dict[str, Any]:
    task_bundle = task_run.task_bundle if isinstance(task_run.task_bundle, dict) else {}
    definition = task_bundle.get("effective_compiled_definition")
    if not isinstance(definition, dict):
        definition = task_bundle.get("compiled_definition")
    if not isinstance(definition, dict):
        definition = task_run.workflow_snapshot
    return dict(definition) if isinstance(definition, dict) else {}


def _validate_invocation_digest(invocation: dict[str, Any]) -> None:
    expected = str(invocation.get("invocation_digest") or "")
    if not expected.startswith("sha256:"):
        raise SkillAgentAdapterError("skill_invocation_digest_missing")
    unsigned = json.loads(json.dumps(invocation, ensure_ascii=False))
    unsigned["invocation_digest"] = "sha256:" + "0" * 64
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    actual = "sha256:" + hashlib.sha256(canonical).hexdigest()
    if not hmac.compare_digest(expected, actual):
        raise SkillAgentAdapterError("skill_invocation_digest_mismatch")


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _frozen_skill_ir(
    task_dir: Path,
    invocation: dict[str, Any],
) -> dict[str, Any]:
    path = _bounded_task_reference(
        task_dir,
        invocation.get("skill_ir"),
        error="skill_ir_reference_invalid",
        digest_error="skill_ir_reference_digest_mismatch",
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SkillAgentAdapterError("skill_ir_reference_invalid") from exc
    if not isinstance(payload, dict):
        raise SkillAgentAdapterError("skill_ir_reference_invalid")
    return payload


def _frozen_input_snapshot(
    task_dir: Path,
    invocation: dict[str, Any],
) -> dict[str, Any]:
    reference = invocation.get("input_snapshot")
    path = _bounded_task_reference(
        task_dir,
        reference,
        error="skill_input_snapshot_reference_invalid",
        digest_error="skill_input_snapshot_reference_digest_mismatch",
    )
    return {
        "ref": str(reference.get("ref") or ""),
        "digest": str(reference.get("digest") or ""),
        "access_scope": str(reference.get("access_scope") or "read"),
        "path": str(path),
    }


def _bounded_task_reference(
    task_dir: Path,
    reference: Any,
    *,
    error: str,
    digest_error: str,
) -> Path:
    relative_text = (
        str(reference.get("ref") or "") if isinstance(reference, dict) else ""
    )
    relative = Path(relative_text)
    if not relative_text or relative.is_absolute() or ".." in relative.parts:
        raise SkillAgentAdapterError(error)
    candidate = task_dir / relative
    try:
        if candidate.is_symlink():
            raise SkillAgentAdapterError(error)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(task_dir)
    except (OSError, ValueError) as exc:
        raise SkillAgentAdapterError(error) from exc
    if not resolved.is_file():
        raise SkillAgentAdapterError(error)
    expected_digest = (
        str(reference.get("digest") or "") if isinstance(reference, dict) else ""
    )
    if not expected_digest.startswith("sha256:"):
        raise SkillAgentAdapterError(digest_error)
    try:
        actual_digest = _sha256_path(resolved)
    except OSError as exc:
        raise SkillAgentAdapterError(digest_error) from exc
    if not hmac.compare_digest(expected_digest, actual_digest):
        raise SkillAgentAdapterError(digest_error)
    return resolved


def _validate_frozen_source_inventory(
    task_dir: Path,
    skill_ir: dict[str, Any],
) -> None:
    entries = skill_ir.get("source_file_digests")
    if not isinstance(entries, list) or not entries:
        raise SkillAgentAdapterError("skill_source_digest_inventory_missing")
    frozen_root = task_dir / "frozen_skill"
    source_root = frozen_root / "source"
    try:
        if frozen_root.is_symlink() or source_root.is_symlink():
            raise SkillAgentAdapterError("skill_source_digest_inventory_mismatch")
        resolved_frozen_root = frozen_root.resolve(strict=True)
        resolved_frozen_root.relative_to(task_dir)
        resolved_source_root = source_root.resolve(strict=True)
        resolved_source_root.relative_to(resolved_frozen_root)
    except (OSError, ValueError) as exc:
        raise SkillAgentAdapterError(
            "skill_source_digest_inventory_mismatch"
        ) from exc
    seen: set[str] = set()
    declared_directories: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise SkillAgentAdapterError("skill_source_digest_inventory_invalid")
        relative_text = str(entry.get("path") or "")
        expected_digest = str(entry.get("digest") or "")
        if relative_text in seen or not expected_digest.startswith("sha256:"):
            raise SkillAgentAdapterError("skill_source_digest_inventory_invalid")
        seen.add(relative_text)
        relative = Path(relative_text)
        if not relative_text or relative.is_absolute() or ".." in relative.parts:
            raise SkillAgentAdapterError("skill_source_digest_inventory_invalid")
        parent = relative.parent
        while parent != Path("."):
            declared_directories.add(parent.as_posix())
            parent = parent.parent
        candidate = resolved_source_root / relative
        try:
            if candidate.is_symlink():
                raise SkillAgentAdapterError("skill_source_digest_mismatch")
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(resolved_source_root)
        except (OSError, ValueError) as exc:
            raise SkillAgentAdapterError("skill_source_digest_mismatch") from exc
        if not resolved.is_file() or not hmac.compare_digest(
            expected_digest,
            _sha256_path(resolved),
        ):
            raise SkillAgentAdapterError("skill_source_digest_mismatch")

    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    for current_root, directory_names, file_names in os.walk(
        resolved_source_root,
        topdown=True,
        followlinks=False,
    ):
        current = Path(current_root)
        for name in directory_names:
            path = current / name
            if path.is_symlink():
                raise SkillAgentAdapterError(
                    "skill_source_digest_inventory_mismatch"
                )
            actual_directories.add(path.relative_to(resolved_source_root).as_posix())
        for name in file_names:
            path = current / name
            if path.is_symlink() or not path.is_file():
                raise SkillAgentAdapterError(
                    "skill_source_digest_inventory_mismatch"
                )
            actual_files.add(path.relative_to(resolved_source_root).as_posix())
    if actual_files != seen or actual_directories != declared_directories:
        raise SkillAgentAdapterError("skill_source_digest_inventory_mismatch")


def _bounded_skill_source_path(
    task_dir: Path,
    relative_text: str,
    *,
    error: str,
) -> Path:
    relative = Path(relative_text)
    if not relative_text or relative.is_absolute() or ".." in relative.parts:
        raise SkillAgentAdapterError(error)
    try:
        source_root = (task_dir / "frozen_skill" / "source").resolve(strict=True)
        candidate = source_root / relative
        if candidate.is_symlink():
            raise SkillAgentAdapterError(error)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(source_root)
    except (OSError, ValueError) as exc:
        raise SkillAgentAdapterError(error) from exc
    if not resolved.is_file():
        raise SkillAgentAdapterError(error)
    return resolved


def _bounded_core_rules(
    task_dir: Path,
    skill_ir: dict[str, Any],
) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for item in skill_ir.get("core_rules") or []:
        if not isinstance(item, dict):
            continue
        instruction_path = str(item.get("instruction_path") or "")
        path = _bounded_skill_source_path(
            task_dir,
            instruction_path,
            error="skill_core_rule_missing",
        )
        rules.append(
            {
                "rule_id": str(item.get("rule_id") or ""),
                "instruction_path": instruction_path,
                "path": str(path),
                "acknowledgement_required": bool(
                    item.get("acknowledgement_required", False)
                ),
            }
        )
    return rules


def _verified_predecessor_artifacts(
    prior_step_results: list[dict[str, Any]],
    *,
    artifact_root: Path,
) -> list[dict[str, str]]:
    shared_root = artifact_root.resolve()
    artifacts: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for result in prior_step_results:
        if str(result.get("status") or "") != "completed":
            continue
        step_id = str(result.get("step_id") or result.get("node_id") or "")
        prior_root_text = str(result.get("artifact_dir") or "")
        if not step_id or not prior_root_text:
            continue
        try:
            prior_root = Path(prior_root_text).expanduser().resolve(strict=True)
        except OSError:
            continue
        if prior_root != shared_root:
            continue
        for value in result.get("artifacts") or []:
            artifact = str(value or "")
            relative = Path(artifact)
            if not artifact or relative.is_absolute() or ".." in relative.parts:
                continue
            candidate = shared_root / relative
            try:
                if candidate.is_symlink():
                    continue
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(shared_root)
            except (OSError, ValueError):
                continue
            if not resolved.is_file() or (step_id, artifact) in seen:
                continue
            seen.add((step_id, artifact))
            artifacts.append(
                {
                    "step_id": step_id,
                    "artifact": artifact,
                    "path": str(resolved),
                }
            )
    return artifacts


def _effective_step_timeout_seconds(
    timeout_budget: dict[str, Any],
    *,
    remaining_timeout_seconds: int,
) -> int:
    remaining = _positive_int(remaining_timeout_seconds)
    step_limit = _positive_int(timeout_budget.get("step_timeout_seconds"))
    if step_limit <= 0:
        step_limit = _positive_int(timeout_budget.get("agent_timeout_seconds"))
    if remaining > 0 and step_limit > 0:
        return min(remaining, step_limit)
    if remaining > 0:
        return remaining
    if step_limit > 0:
        return step_limit
    return _positive_int(timeout_budget.get("overall_timeout_seconds"))


def _hard_timeout_kind(
    timeout_budget: dict[str, Any],
    *,
    remaining_timeout_seconds: int,
) -> str:
    """Identify which hard budget selected the provider process deadline.

    An Attempt deadline wins ties so one terminal timeout has a stable owner.
    """

    remaining = _positive_int(remaining_timeout_seconds)
    step_limit = _positive_int(timeout_budget.get("step_timeout_seconds"))
    if step_limit <= 0:
        step_limit = _positive_int(timeout_budget.get("agent_timeout_seconds"))
    if remaining > 0 and (step_limit <= 0 or remaining <= step_limit):
        return "overall"
    return "agent"


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _required_artifact_paths(
    definition: dict[str, Any], step: dict[str, Any]
) -> list[str]:
    gate = step.get("completion_gate") if isinstance(step.get("completion_gate"), dict) else {}
    required_ids = {
        str(item) for item in gate.get("required_artifact_ids") or [] if str(item)
    }
    paths = [
        str(item.get("path") or "")
        for item in definition.get("artifacts") or []
        if isinstance(item, dict)
        and str(item.get("artifact_id") or "") in required_ids
        and str(item.get("path") or "")
    ]
    return sorted(set(paths))


def _artifact_paths_by_id(
    definition: dict[str, Any], artifact_ids: list[str]
) -> list[str]:
    paths_by_id = {
        str(item.get("artifact_id") or ""): str(item.get("path") or "")
        for item in definition.get("artifacts") or []
        if isinstance(item, dict)
        and str(item.get("artifact_id") or "")
        and str(item.get("path") or "")
    }
    return [paths_by_id[item] for item in artifact_ids if item in paths_by_id]


def _provider_snapshot(execution: dict[str, Any]) -> dict[str, Any]:
    provider_ref = str(execution.get("provider_ref") or "")
    return {
        "providers": {
            provider_ref: {
                "provider": provider_ref,
                "env_hints": {
                    str(key): "<redacted>"
                    for key in execution.get("environment_keys") or []
                    if str(key)
                },
            }
        }
    }


def _provider_failure_step_result(
    *,
    node_id: str,
    artifact_root: Path,
    required_artifacts: list[str],
    result: Any,
    error: str = "",
    result_type: str = "skill_step",
    governance_status: str = "",
    hard_timeout_kind: str = "agent",
) -> dict[str, Any]:
    raw_error = str(error or result.error or result.status or "skill_agent_failed")
    redacted_error = redact_agent_diagnostic_text(raw_error)[:4000]
    timed_out = bool(result.timed_out)
    provider_status = str(result.status or "failed")
    status = (
        "cancelled"
        if provider_status == "cancelled"
        else "timed_out"
        if timed_out
        else "error"
    )
    timeout_kind = (
        _provider_timeout_kind(raw_error, hard_timeout_kind=hard_timeout_kind)
        if timed_out
        else ""
    )
    diagnostics = _provider_diagnostic_summary(result.provider_diagnostics)
    provider_session: dict[str, Any] = {
        "session_id": str(result.session_id),
    }
    upstream_session = diagnostics.get("provider_session")
    if isinstance(upstream_session, dict) and upstream_session:
        provider_session["provider_session"] = upstream_session
    resume_session = diagnostics.get("resume_session")
    if isinstance(resume_session, dict) and resume_session:
        provider_session["resume_session"] = resume_session
    projected = {
        "step_id": node_id,
        "node_id": node_id,
        "type": result_type,
        "status": status,
        "error": redacted_error,
        "artifact_dir": str(artifact_root),
        "agent_session_id": str(result.session_id),
        "provider_session": provider_session,
        "duration_ms": int(result.duration_ms),
        "timed_out": timed_out,
        "timeout_kind": timeout_kind,
        "required_artifacts": required_artifacts,
        "artifacts": list(result.artifacts),
        "provider_diagnostics": diagnostics,
        "technical_diagnostics": {
            "error": f"{result_type}_lifecycle_failed",
            "provider_status": provider_status,
            "provider_error": redacted_error,
            "timed_out": timed_out,
            "timeout_kind": timeout_kind,
            "diagnostic_summary": diagnostics,
        },
    }
    if governance_status:
        projected["governance_status"] = governance_status
    return projected


def _provider_timeout_kind(error: str, *, hard_timeout_kind: str) -> str:
    lowered = error.lower()
    if "没有输出或进度" in error or "idle" in lowered or "activity" in lowered:
        return "idle"
    if "overall" in lowered or "total_execution_timeout" in lowered:
        return "overall"
    return hard_timeout_kind if hard_timeout_kind in {"agent", "overall"} else "agent"


def _provider_diagnostic_summary(value: Any) -> dict[str, Any]:
    diagnostics = value if isinstance(value, dict) else {}
    summary: dict[str, Any] = {}
    output = diagnostics.get("output")
    if output not in (None, ""):
        summary["output_tail"] = redact_agent_diagnostic_text(str(output))[-4000:]
    provider_session = diagnostics.get("provider_session")
    if isinstance(provider_session, dict):
        summary["provider_session"] = _redacted_diagnostic_value(provider_session)
    resume_token = diagnostics.get("resume_token")
    if isinstance(resume_token, dict):
        provider = redact_agent_diagnostic_text(
            str(resume_token.get("provider") or "")
        )[:200]
        session_id = redact_agent_diagnostic_text(
            str(resume_token.get("value") or "")
        )[:1000]
        if provider and session_id:
            summary["resume_session"] = {
                "provider": provider,
                "session_id": session_id,
            }
    details = {
        str(key): _redacted_diagnostic_value(item)
        for key, item in diagnostics.items()
        if str(key) not in {"output", "provider_session", "resume_token"}
    }
    if details:
        summary["details"] = details
    return summary


def _redacted_diagnostic_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        return "<truncated>"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:20]:
            key_text = str(key)
            normalized = key_text.lower().replace("-", "_")
            if any(
                marker in normalized
                for marker in ("secret", "token", "password", "credential", "api_key")
            ):
                result[key_text] = "<redacted>"
            else:
                result[key_text] = _redacted_diagnostic_value(
                    item,
                    depth=depth + 1,
                )
        return result
    if isinstance(value, (list, tuple)):
        return [
            _redacted_diagnostic_value(item, depth=depth + 1)
            for item in list(value)[:20]
        ]
    if isinstance(value, str):
        return redact_agent_diagnostic_text(value)[:2000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_agent_diagnostic_text(str(value))[:2000]


def _load_producer_resume_token(task_dir: Path) -> ProviderResumeToken | None:
    path = task_dir / "skill_producer_session.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    provider = str(payload.get("provider") or "") if isinstance(payload, dict) else ""
    value = str(payload.get("value") or "") if isinstance(payload, dict) else ""
    return ProviderResumeToken(provider=provider, value=value) if provider and value else None


def _persist_producer_resume_token(
    task_dir: Path, provider_diagnostics: dict[str, Any]
) -> None:
    token = provider_diagnostics.get("resume_token")
    if not isinstance(token, dict):
        return
    provider = str(token.get("provider") or "")
    value = str(token.get("value") or "")
    if not provider or not value:
        return
    path = task_dir / "skill_producer_session.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"provider": provider, "value": value}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _render_step_prompt(
    *,
    artifact_root: Path,
    node_id: str,
    step: dict[str, Any],
    selected_workflow: Path,
    selected_workflow_path: str,
    instruction: Path,
    instruction_path: str,
    skill_entrypoint: Path,
    core_rules: list[dict[str, Any]],
    frozen_input_snapshot: dict[str, Any],
    resolved_inputs: dict[str, Any],
    execution_profile: dict[str, Any],
    predecessor_artifacts: list[dict[str, str]],
    required_artifacts: list[str],
    timeout_seconds: int,
    independent_review: bool,
) -> str:
    required = "\n".join(f"- {path}" for path in required_artifacts)
    input_summary = "\n".join(
        f"- {key}: {_prompt_value_summary(value)}"
        for key, value in sorted(resolved_inputs.items())
    ) or "- none"
    predecessors = "\n".join(
        f"- {item['step_id']}: {item['path']}"
        for item in predecessor_artifacts
    ) or "- none"
    required_rules = "\n".join(
        f"- {item['rule_id']}: {item['instruction_path']} ({item['path']})"
        for item in core_rules
    ) or "- none"
    profile_id = str(execution_profile.get("id") or "unspecified")
    snapshot_ref = str(frozen_input_snapshot.get("ref") or "")
    snapshot_digest = str(frozen_input_snapshot.get("digest") or "")
    independent_contract = (
        "This is a fresh independent Judge session. Review the verified predecessor "
        "artifacts directly and form your own conclusions. Do not continue or trust "
        "the producer's conclusions. Do not launch nested agents or subagents; CodeTalk "
        "already provides the required session isolation.\n"
        if independent_review
        else ""
    )
    return (
        "Execute the frozen CodeTalk Skill step below against the current source workspace.\n"
        f"{independent_contract}"
        f"Step ID: {node_id}\n"
        f"Step title: {step.get('title') or node_id!s}\n"
        f"Execution profile: {profile_id}\n"
        f"Remaining step timeout: {timeout_seconds}s\n"
        f"Frozen input snapshot: {frozen_input_snapshot['path']} "
        f"(ref: {snapshot_ref}; digest: {snapshot_digest})\n"
        f"Frozen Skill entrypoint: SKILL.md ({skill_entrypoint})\n"
        f"Selected workflow: {selected_workflow_path} ({selected_workflow})\n"
        f"Step instruction: {instruction_path} ({instruction})\n"
        "Mandatory core rules:\n"
        f"{required_rules}\n"
        f"Artifact root: {artifact_root}\n"
        "Resolved input summary (read full values from the frozen snapshot; source text is not copied here):\n"
        f"{input_summary}\n"
        "Verified predecessor artifacts:\n"
        f"{predecessors}\n"
        "Read SKILL.md, the selected workflow, the step instruction, every mandatory "
        "core rule, and the input snapshot before acting; follow all of them. "
        "Preserve verified artifacts from earlier steps. "
        "Write every required artifact below the artifact root using exactly these relative paths:\n"
        f"{required}\n"
        "Do not report completion until every required file exists and contains substantive evidence."
    )


def _prompt_value_summary(value: Any) -> str:
    if isinstance(value, str):
        redacted = redact_agent_diagnostic_text(value)
        if len(redacted) <= 240:
            return redacted
        return f"<string {len(value)} characters; read frozen snapshot>"
    if isinstance(value, dict):
        keys = ", ".join(sorted(str(key) for key in value)[:12])
        return f"<object {len(value)} keys{': ' + keys if keys else ''}>"
    if isinstance(value, (list, tuple)):
        return f"<array {len(value)} items>"
    if value is None or isinstance(value, (bool, int, float)):
        return str(value)
    return f"<{type(value).__name__}; read frozen snapshot>"


def _request_resolved_inputs(resolved_inputs: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): _request_input_value(value)
        for key, value in sorted(resolved_inputs.items())
    }


def _request_input_value(value: Any) -> Any:
    if isinstance(value, str):
        redacted = redact_agent_diagnostic_text(value)
        if len(redacted) <= 240:
            return redacted
        return {
            "kind": "string",
            "characters": len(value),
            "source": "frozen_input_snapshot",
        }
    if isinstance(value, dict):
        return {
            "kind": "object",
            "keys": sorted(str(key) for key in value)[:20],
            "source": "frozen_input_snapshot",
        }
    if isinstance(value, (list, tuple)):
        return {
            "kind": "array",
            "items": len(value),
            "source": "frozen_input_snapshot",
        }
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return {
        "kind": type(value).__name__,
        "source": "frozen_input_snapshot",
    }


def _render_judge_prompt(
    *,
    task_dir: Path,
    artifact_root: Path,
    artifact_ids: list[str],
    artifact_paths: list[str],
) -> str:
    pairs = "\n".join(
        f"- {artifact_id}: {path}"
        for artifact_id, path in zip(artifact_ids, artifact_paths, strict=False)
    )
    return (
        "Act as the independent CodeTalk Skill Judge. Do not continue the producer's "
        "conversation and do not modify producer artifacts.\n"
        f"Frozen invocation: {task_dir / 'skill_invocation.json'}\n"
        f"Frozen Skill source: {task_dir / 'frozen_skill' / 'source'}\n"
        f"Producer artifact root: {artifact_root}\n"
        "Inspect every required artifact below for completeness, evidence quality, and "
        "consistency with the frozen Skill contract:\n"
        f"{pairs}\n"
        f"Write exactly one JSON object to {task_dir / 'skill_judge_report.json'} with "
        "fields status, ready, warnings, checked_artifact_ids, and summary. status must "
        "be READY or READY_WITH_WARNINGS only when every required artifact passes; "
        "checked_artifact_ids must list every inspected artifact ID."
    )
