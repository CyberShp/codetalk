"""Execute frozen Skill steps through the production Agent Harness seam."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.services.agent_runtimes import get_agent_runtime_sync
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
    event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    node_id = str(node.get("node_id") or "")
    producer = _producer_runtime(invocation)
    execution = producer["execution"]
    task_dir = Path(str(task_run.artifact_dir)).expanduser().resolve()
    artifact_root = _bounded_artifact_root(task_dir, invocation)
    definition = _compiled_definition(task_run)
    step = next(
        (
            dict(item)
            for item in definition.get("steps") or []
            if isinstance(item, dict)
            and str(item.get("step_id") or item.get("id") or "") == node_id
        ),
        {},
    )
    required_artifacts = _required_artifact_paths(definition, step)
    if not required_artifacts:
        raise SkillAgentAdapterError("skill_step_artifact_contract_missing")

    adapter = create_provider_adapter(
        provider=str(execution.get("provider_ref") or ""),
        prompt_transport=str(execution.get("prompt_transport") or ""),
        artifact_dir=artifact_root,
    )
    request = HarnessRunRequest(
        provider=str(execution.get("provider_ref") or ""),
        command=[str(item) for item in execution.get("command") or []],
        cwd=str(task_run.repo_path),
        workflow_snapshot=definition,
        task_bundle={
            "rendered_user_input": _render_step_prompt(
                task_dir=task_dir,
                artifact_root=artifact_root,
                node_id=node_id,
                step=step,
                required_artifacts=required_artifacts,
            ),
            "required_artifacts": required_artifacts,
            "skill_invocation": invocation,
            "provider_snapshot": _provider_snapshot(execution),
        },
        mcp_profile=str(execution.get("mcp_profile") or ""),
        prompt_transport=str(execution.get("prompt_transport") or ""),
        timeout_seconds=int(
            (producer.get("timeout_budget") or {}).get("agent_timeout_seconds") or 0
        ),
        requires_network=bool(execution.get("requires_network", True)),
        run_id=f"{task_run.task_run_id}_skill_producer",
    )
    facade = AgentHarnessFacade(artifact_root, adapter=adapter)
    session = facade.prepare(request)
    _apply_configured_runtime(
        session,
        execution,
        hard_timeout_seconds=request.timeout_seconds or 1800,
    )

    resume_token = _load_producer_resume_token(task_dir)
    if resume_token is None:
        result = facade.execute(
            session,
            timeout_sec=0,
            is_cancelled=is_cancelled,
            event_sink=event_sink,
        )
    else:
        result = facade.resume(
            session,
            resume_token,
            timeout_sec=0,
            is_cancelled=is_cancelled,
            event_sink=event_sink,
        )
    if isinstance(result, ProviderUnsupported):
        raise SkillAgentAdapterError(result.code or "skill_agent_operation_unsupported")
    if str(result.status or "") != "completed":
        raise SkillAgentAdapterError(str(result.error or result.status or "skill_agent_failed"))

    _persist_producer_resume_token(task_dir, result.provider_diagnostics)
    missing = [path for path in required_artifacts if not (artifact_root / path).is_file()]
    if missing:
        raise SkillAgentAdapterError(
            "skill_step_required_artifacts_missing:" + ",".join(missing)
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
    }


def execute_skill_judge(
    *,
    task_run: Any,
    invocation: dict[str, Any],
    event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    runtime = invocation.get("runtime")
    judge_runtime = runtime.get("judge") if isinstance(runtime, dict) else None
    execution = judge_runtime.get("execution") if isinstance(judge_runtime, dict) else None
    if not isinstance(execution, dict):
        raise SkillAgentAdapterError("skill_judge_runtime_unavailable")

    task_dir = Path(str(task_run.artifact_dir)).expanduser().resolve()
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
        timeout_seconds=int(
            (judge_runtime.get("timeout_budget") or {}).get("agent_timeout_seconds") or 0
        ),
        requires_network=bool(execution.get("requires_network", True)),
        run_id=f"{task_run.task_run_id}_skill_judge",
    )
    facade = AgentHarnessFacade(task_dir, adapter=adapter)
    session = facade.prepare(request)
    _apply_configured_runtime(
        session,
        execution,
        hard_timeout_seconds=request.timeout_seconds or 900,
    )
    result = facade.execute(
        session,
        timeout_sec=0,
        is_cancelled=is_cancelled,
        event_sink=event_sink,
    )
    if isinstance(result, ProviderUnsupported):
        raise SkillAgentAdapterError(result.code or "skill_judge_operation_unsupported")
    if str(result.status or "") != "completed":
        raise SkillAgentAdapterError(str(result.error or result.status or "skill_judge_failed"))

    report_path = task_dir / "skill_judge_report.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SkillAgentAdapterError("skill_judge_report_invalid") from exc
    if not isinstance(report, dict):
        raise SkillAgentAdapterError("skill_judge_report_invalid")
    checked_ids = {str(item) for item in report.get("checked_artifact_ids") or [] if str(item)}
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


def _apply_configured_runtime(
    session: Any,
    execution: dict[str, Any],
    *,
    hard_timeout_seconds: int,
) -> None:
    """Restore the selected Agent Runtime semantics lost by the generic Harness adapter."""

    runtime_id = str(execution.get("runtime_config_id") or "").strip()
    configured = get_agent_runtime_sync(runtime_id) if runtime_id else None
    metadata = getattr(session, "metadata", None)
    runtime = metadata.get("runtime") if isinstance(metadata, dict) else None
    if not isinstance(configured, dict) or not isinstance(runtime, dict):
        return

    runtime["output_mode"] = str(configured.get("output_mode") or "auto")
    runtime["completion_mode"] = str(configured.get("completion_mode") or "process_exit")
    runtime["idle_complete_seconds"] = max(
        1, int(configured.get("idle_complete_seconds") or 5)
    )
    runtime["sentinel_text"] = str(configured.get("sentinel_text") or "")
    runtime["session_persistence"] = str(
        configured.get("session_persistence") or "none"
    )
    runtime["resume_args"] = [str(item) for item in configured.get("resume_args") or []]

    hard_timeout = max(1, int(hard_timeout_seconds or 1))
    configured_timeout = max(1, int(configured.get("timeout_seconds") or hard_timeout))
    runtime["timeout_seconds"] = min(configured_timeout, hard_timeout)
    runtime["activity_timeout_seconds"] = min(configured_timeout, hard_timeout)
    runtime["total_timeout_seconds"] = hard_timeout


def _producer_runtime(invocation: dict[str, Any]) -> dict[str, Any]:
    runtime = invocation.get("runtime")
    producer = runtime.get("producer") if isinstance(runtime, dict) else None
    execution = producer.get("execution") if isinstance(producer, dict) else None
    if not isinstance(producer, dict) or not isinstance(execution, dict):
        raise SkillAgentAdapterError("skill_agent_runtime_unavailable")
    if not execution.get("provider_ref") or not execution.get("command"):
        raise SkillAgentAdapterError("skill_agent_runtime_unavailable")
    return producer


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


def _required_artifact_paths(
    definition: dict[str, Any], step: dict[str, Any]
) -> list[str]:
    gate = step.get("completion_gate") if isinstance(step.get("completion_gate"), dict) else {}
    required_ids = {str(item) for item in gate.get("required_artifact_ids") or [] if str(item)}
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
    task_dir: Path,
    artifact_root: Path,
    node_id: str,
    step: dict[str, Any],
    required_artifacts: list[str],
) -> str:
    source_root = task_dir / "frozen_skill" / "source"
    instruction_path = str(step.get("instruction_path") or "")
    instruction = source_root / instruction_path if instruction_path else None
    input_snapshot_path = task_dir / "skill_input_snapshot.json"
    try:
        input_snapshot = input_snapshot_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SkillAgentAdapterError("skill_input_snapshot_unavailable") from exc
    if instruction is not None:
        try:
            instruction_text = instruction.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SkillAgentAdapterError("skill_step_instruction_unavailable") from exc
        if not instruction_text:
            raise SkillAgentAdapterError("skill_step_instruction_unavailable")
    else:
        instruction_text = "(embedded contract only)"
    required = "\n".join(f"- {path}" for path in required_artifacts)
    return (
        "Execute only the current frozen CodeTalk Skill step against the current source workspace.\n"
        f"Step ID: {node_id}\n"
        f"Step title: {step.get('title') or node_id!s}\n"
        f"Frozen Skill source: {source_root}\n"
        f"Artifact root: {artifact_root}\n\n"
        "Task input snapshot:\n"
        f"{input_snapshot or '{}'}\n\n"
        "Current step instruction:\n"
        f"{instruction_text}\n\n"
        "Execution boundary:\n"
        "- Work only on this step; do not execute or precompute later steps.\n"
        "- Keep source exploration inside the scope declared by the task input.\n"
        "- If this step defines scope or a task contract, inspect only enough source to verify that boundary; do not perform a full-repository analysis.\n"
        "- Preserve artifacts from earlier steps.\n"
        "Write every required artifact below the artifact root using exactly these relative paths:\n"
        f"{required}\n"
        "Stop this step as soon as every required file exists and contains substantive evidence."
    )


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
