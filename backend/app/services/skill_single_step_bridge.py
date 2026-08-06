"""Temporary real-provider bridge for the first executable Skill step.

This module deliberately reuses the existing Workbench ``agent_task`` preparation
and Harness execution path.  It does not add a second runner.  The bridge is
bounded to one Skill step so the real provider boundary can be qualified before
multi-step continuation and Judge orchestration are enabled.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import ModuleType
from typing import Any

logger = logging.getLogger(__name__)

BRIDGE_MODE = "single_step_real_agent_v1"
DEFAULT_PROVIDER = "opencode"


class SkillSingleStepBridgeError(ValueError):
    """Raised when a Skill cannot be projected onto the pilot runtime."""


def install_skill_single_step_bridge(task_api_module: ModuleType) -> None:
    """Install the bounded real-Agent compiler into the Skill task API.

    ``workbench_v2_tasks`` keeps the public API and persistence authority.  Only
    its two Skill-to-V3 projection functions are replaced.  New Run Attempts
    therefore continue to use ``WorkbenchTaskRunPreparer`` and
    ``WorkbenchWorkflowRunner`` without a parallel execution implementation.
    """

    if getattr(task_api_module, "_single_step_real_skill_bridge_installed", False):
        return
    task_api_module._skill_plan = build_single_step_skill_plan
    task_api_module._skill_compat_definition = build_single_step_compat_definition
    task_api_module._single_step_real_skill_bridge_installed = True
    logger.warning(
        "Installed bounded Skill real-Agent bridge: mode=%s provider=%s",
        BRIDGE_MODE,
        DEFAULT_PROVIDER,
    )


def build_single_step_skill_plan(skill_ir: dict[str, Any]) -> dict[str, Any]:
    """Compile exactly the first declared Skill step as a real Agent node."""

    step = _first_skill_step(skill_ir)
    step_id = _step_id(step)
    outputs = _declared_outputs_for_step(skill_ir, step_id)
    return {
        "compiled_contract_version": 3,
        "plan_version": 1,
        "skill_id": str(skill_ir.get("skill_id") or ""),
        "bridge_mode": BRIDGE_MODE,
        "topological_order": [step_id],
        "nodes": [
            {
                "node_id": step_id,
                "kind": "agent",
                "type": "agent_task",
                "handler_id": "agent",
                "depends_on": [],
                "failure_policy": "stop",
                "required_outputs": [
                    str(output["output_id"])
                    for output in outputs
                    if output.get("required")
                ],
            }
        ],
    }


def build_single_step_compat_definition(
    version: Any,
    skill_ir: dict[str, Any],
) -> dict[str, Any]:
    """Project the first Skill step into the existing V3 ``agent_task`` schema."""

    step = _first_skill_step(skill_ir)
    step_id = _step_id(step)
    outputs = _declared_outputs_for_step(skill_ir, step_id)
    required_artifacts = [
        str(output["artifact"])
        for output in outputs
        if output.get("required") and str(output.get("artifact") or "")
    ]
    if not required_artifacts:
        raise SkillSingleStepBridgeError(
            f"pilot Skill step {step_id!r} does not declare a required artifact"
        )

    instruction_path = str(step.get("instruction_path") or "").strip()
    instruction_body = _read_skill_source(version, instruction_path)
    core_rule_bodies = _core_rule_bodies(version, skill_ir)
    goal = _build_agent_goal(
        skill_id=str(skill_ir.get("skill_id") or ""),
        step=step,
        instruction_path=instruction_path,
        instruction_body=instruction_body,
        core_rule_bodies=core_rule_bodies,
        required_artifacts=required_artifacts,
    )
    agent_step = {
        "id": step_id,
        "step_id": step_id,
        "type": "agent_task",
        "title": str(step.get("title") or step_id),
        "goal": goal,
        "provider": DEFAULT_PROVIDER,
        "required_artifacts": required_artifacts,
        "provider_capabilities_required": [],
        "skill_instructions": [
            {
                "id": f"{step_id}.instruction",
                "path": instruction_path,
                "body": instruction_body,
            }
        ],
        "bridge_mode": BRIDGE_MODE,
    }
    return {
        "id": str(getattr(version, "skill_id", "") or skill_ir.get("skill_id") or ""),
        "version_id": str(getattr(version, "version_id", "") or ""),
        "name": str(getattr(version, "skill_id", "") or skill_ir.get("skill_id") or ""),
        "description": "Bounded first-step real Agent qualification path.",
        "compiled_contract_version": 3,
        "validation_profile": "none",
        "bridge_mode": BRIDGE_MODE,
        "inputs": _normalized_inputs(skill_ir),
        "declared_outputs": outputs,
        "outputs": outputs,
        "validators": [],
        "steps": [agent_step],
    }


def _first_skill_step(skill_ir: dict[str, Any]) -> dict[str, Any]:
    steps = [item for item in skill_ir.get("steps") or [] if isinstance(item, dict)]
    if not steps:
        raise SkillSingleStepBridgeError("Skill IR has no executable steps")
    by_id = {_step_id(step): step for step in steps if _step_id(step)}
    for candidate in skill_ir.get("topological_order") or []:
        step = by_id.get(str(candidate))
        if step is not None:
            return step
    return steps[0]


def _step_id(step: dict[str, Any]) -> str:
    value = str(step.get("step_id") or step.get("id") or "").strip()
    if not value:
        raise SkillSingleStepBridgeError("Skill step is missing step_id")
    return value


def _declared_outputs_for_step(
    skill_ir: dict[str, Any],
    step_id: str,
) -> list[dict[str, Any]]:
    artifacts = {
        str(item.get("artifact_id") or item.get("id") or ""): item
        for item in skill_ir.get("artifacts") or []
        if isinstance(item, dict)
    }
    step = next(
        (
            item
            for item in skill_ir.get("steps") or []
            if isinstance(item, dict) and _step_id(item) == step_id
        ),
        {},
    )
    produced_ids = [str(item) for item in step.get("produces") or [] if str(item)]
    outputs: list[dict[str, Any]] = []
    for artifact_id in produced_ids:
        artifact = artifacts.get(artifact_id)
        if not isinstance(artifact, dict):
            raise SkillSingleStepBridgeError(
                f"Skill step {step_id!r} references unknown artifact {artifact_id!r}"
            )
        path = str(artifact.get("path") or "").strip()
        if not path:
            raise SkillSingleStepBridgeError(
                f"Skill artifact {artifact_id!r} has no path"
            )
        outputs.append(
            {
                "id": artifact_id,
                "output_id": artifact_id,
                "artifact": path,
                "path": path,
                "type": "file",
                "required": bool(artifact.get("required", True)),
                "producer_step_id": step_id,
                "from": step_id,
                "visibility": str(artifact.get("visibility") or "internal"),
            }
        )
    return outputs


def _normalized_inputs(skill_ir: dict[str, Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in skill_ir.get("inputs") or []:
        if not isinstance(item, dict):
            continue
        input_id = str(item.get("id") or item.get("input_id") or "").strip()
        if not input_id:
            continue
        kind = str(item.get("kind") or item.get("type") or "text").strip()
        input_type = "directory" if kind == "workspace" else kind
        normalized.append(
            {
                **item,
                "id": input_id,
                "input_id": input_id,
                "type": input_type,
                "kind": kind,
                "resolver": "workspace" if kind == "workspace" else str(item.get("resolver") or ""),
            }
        )
    return normalized


def _read_skill_source(version: Any, relative_path: str) -> str:
    if not relative_path:
        raise SkillSingleStepBridgeError("pilot Skill step has no instruction_path")
    roots = [
        Path(str(getattr(version, "unpacked_root", "") or "")),
        Path(str(getattr(version, "version_root", "") or "")),
        Path(str(getattr(version, "ir_path", "") or "")).parent,
    ]
    for root in roots:
        if not str(root) or str(root) == ".":
            continue
        candidate = root / relative_path
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root.resolve(strict=True))
        except (FileNotFoundError, ValueError):
            continue
        if resolved.is_file():
            return resolved.read_text(encoding="utf-8")
    raise SkillSingleStepBridgeError(
        f"cannot read Skill instruction from published version: {relative_path}"
    )


def _core_rule_bodies(version: Any, skill_ir: dict[str, Any]) -> list[tuple[str, str]]:
    rules: list[tuple[str, str]] = []
    for item in skill_ir.get("core_rules") or []:
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("rule_id") or item.get("id") or "core-rule")
        path = str(item.get("instruction_path") or "").strip()
        if not path:
            continue
        rules.append((rule_id, _read_skill_source(version, path)))
    return rules


def _build_agent_goal(
    *,
    skill_id: str,
    step: dict[str, Any],
    instruction_path: str,
    instruction_body: str,
    core_rule_bodies: list[tuple[str, str]],
    required_artifacts: list[str],
) -> str:
    rules = "\n\n".join(
        f"## Core rule: {rule_id}\n{body.strip()}"
        for rule_id, body in core_rule_bodies
    )
    artifacts = "\n".join(f"- {path}" for path in required_artifacts)
    return (
        f"Execute the first real qualification step for Skill `{skill_id}`.\n"
        "This is a production Agent run, not a lifecycle simulation. Read the "
        "workspace source, follow the frozen instructions, and create every "
        "required artifact under the Agent artifact directory. Do not report "
        "completion until the files exist.\n\n"
        f"# Step\n{str(step.get('title') or _step_id(step))}\n\n"
        f"# Instruction source\n{instruction_path}\n\n"
        f"{instruction_body.strip()}\n\n"
        f"{rules}\n\n"
        f"# Required artifacts\n{artifacts}\n"
    ).strip()
