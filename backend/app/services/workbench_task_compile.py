"""Compile Task-level overrides onto an immutable Workflow Version snapshot."""

from __future__ import annotations

import copy
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any


class TaskConfigurationError(ValueError):
    pass


_EXECUTION_FIELDS = frozenset({
    "provider", "mcp_profiles", "skill_ids", "timeout_sec", "idle_timeout_sec",
    "failure_policy", "retry_policy",
})
_AGENT_RESOURCE_FIELDS = frozenset({"provider", "mcp_profiles", "skill_ids"})
_OUTPUT_TYPES = frozenset({
    "json", "markdown", "text", "patch", "diff", "test_cases", "scope_report",
    "test_design_mindmap",
})


def compile_task_configuration(
    *,
    compiled_definition: dict[str, Any],
    compiled_plan: dict[str, Any],
    execution_overrides: dict[str, Any],
    output_overrides: dict[str, Any],
) -> dict[str, Any]:
    definition = copy.deepcopy(compiled_definition)
    plan = copy.deepcopy(compiled_plan)
    steps = {str(item.get("id") or ""): item for item in definition.get("steps") or [] if isinstance(item, dict)}
    plan_nodes = {str(item.get("node_id") or ""): item for item in plan.get("nodes") or [] if isinstance(item, dict)}

    raw_nodes = execution_overrides.get("nodes") if isinstance(execution_overrides, dict) else {}
    if raw_nodes is None:
        raw_nodes = {}
    if not isinstance(raw_nodes, dict):
        raise TaskConfigurationError("execution_overrides.nodes 必须是对象")
    for step_id, fields in raw_nodes.items():
        if str(step_id) not in steps or not isinstance(fields, dict):
            raise TaskConfigurationError(f"任务覆盖引用未知执行节点：{step_id}")
        step = steps[str(step_id)]
        if str(step.get("type") or "") != "agent_task":
            invalid_resources = _AGENT_RESOURCE_FIELDS.intersection(fields)
            if invalid_resources:
                labels = "、".join(sorted(invalid_resources))
                raise TaskConfigurationError(
                    f"仅 Agent 节点可覆盖执行器资源；{step_id} 不能覆盖 {labels}"
                )
        for field, directive in fields.items():
            if field not in _EXECUTION_FIELDS:
                raise TaskConfigurationError(f"不支持的任务执行覆盖字段：{field}")
            if not isinstance(directive, dict) or directive.get("mode") not in {"inherit", "replace"}:
                raise TaskConfigurationError(f"{step_id}.{field} 必须显式指定 inherit 或 replace")
            if directive["mode"] == "inherit":
                continue
            if "value" not in directive:
                raise TaskConfigurationError(f"{step_id}.{field} replace 缺少 value")
            value = copy.deepcopy(directive["value"])
            _validate_execution_value(str(step_id), field, value)
            definition_field = "skills" if field == "skill_ids" else field
            steps[str(step_id)][definition_field] = value
            if field == "mcp_profiles":
                steps[str(step_id)]["mcp_profile"] = value[0] if isinstance(value, list) and len(value) == 1 else ""
            if str(step_id) in plan_nodes:
                plan_nodes[str(step_id)][field] = value

    originals = {
        str(item.get("id") or ""): item
        for item in definition.get("outputs") or []
        if isinstance(item, dict)
    }
    raw_output_changes = output_overrides.get("outputs") if isinstance(output_overrides, dict) else {}
    if raw_output_changes is None:
        raw_output_changes = {}
    if not isinstance(raw_output_changes, dict):
        raise TaskConfigurationError("output_overrides.outputs 必须是对象")
    effective_outputs: list[dict[str, Any]] = []
    artifact_renames: dict[tuple[str, str], str] = {}
    for output_id, original in originals.items():
        change = raw_output_changes.get(output_id) or {}
        if not isinstance(change, dict):
            raise TaskConfigurationError(f"输出覆盖必须是对象：{output_id}")
        if "enabled" in change and not isinstance(change["enabled"], bool):
            raise TaskConfigurationError(f"输出 enabled 必须是布尔值：{output_id}")
        enabled = change.get("enabled", original.get("default_enabled", True))
        if original.get("required") and not enabled:
            raise TaskConfigurationError(f"必需输出不能关闭：{output_id}")
        if not enabled:
            continue
        output = copy.deepcopy(original)
        if original.get("default_enabled") is False or "enabled" in change:
            output["enabled"] = True
        if "label" in change:
            output["label"] = str(change.get("label") or output_id).strip()
        if "artifact" in change:
            old_artifact = str(output.get("artifact") or "")
            new_artifact = _artifact(change.get("artifact"), output_id=output_id)
            output["artifact"] = new_artifact
            artifact_renames[(str(output.get("from") or ""), old_artifact)] = new_artifact
        effective_outputs.append(output)
    unknown_outputs = set(raw_output_changes) - set(originals)
    if unknown_outputs:
        raise TaskConfigurationError(f"输出覆盖引用未知输出：{', '.join(sorted(unknown_outputs))}")

    custom = output_overrides.get("custom_outputs") if isinstance(output_overrides, dict) else []
    if custom is None:
        custom = []
    if not isinstance(custom, list):
        raise TaskConfigurationError("custom_outputs 必须是数组")
    for raw in custom:
        if not isinstance(raw, dict):
            raise TaskConfigurationError("任务专用输出必须是对象")
        output_id = str(raw.get("id") or "").strip()
        source = str(raw.get("from") or "").strip()
        if not output_id or output_id in originals or any(item.get("id") == output_id for item in effective_outputs):
            raise TaskConfigurationError(f"任务专用输出 ID 无效或重复：{output_id}")
        if source not in steps:
            raise TaskConfigurationError(f"任务专用输出来源节点不存在：{source}")
        if str(steps[source].get("type") or "") != "agent_task":
            raise TaskConfigurationError(
                f"节点 {source} 不能生成任务专用文件；请选择 Agent 节点作为输出来源"
            )
        output_type = str(raw.get("type") or "").strip()
        if output_type not in _OUTPUT_TYPES:
            raise TaskConfigurationError(f"任务专用输出类型不支持：{output_id} ({output_type or 'empty'})")
        output = {
            "id": output_id,
            "label": str(raw.get("label") or output_id).strip(),
            "type": output_type,
            "from": source,
            "artifact": _artifact(raw.get("artifact"), output_id=output_id),
            "required": bool(raw.get("required", False)),
        }
        if raw.get("schema") is not None:
            if not isinstance(raw["schema"], dict):
                raise TaskConfigurationError(f"任务专用输出 Schema 必须是对象：{output_id}")
            output["schema"] = copy.deepcopy(raw["schema"])
        elif output_type == "json":
            raise TaskConfigurationError(f"JSON 任务专用输出缺少 Schema：{output_id}")
        if output_type == "test_design_mindmap":
            from app.services.source_driven_test_design import MINDMAP_ARTIFACTS

            output["artifact"] = MINDMAP_ARTIFACTS[0]
            output["companion_artifacts"] = list(MINDMAP_ARTIFACTS[1:])
        effective_outputs.append(output)

    for output in effective_outputs:
        if str(output.get("type") or "") != "test_design_mindmap":
            continue
        source_id = str(output.get("from") or "")
        source_step = steps.get(source_id) or {}
        provider = str(source_step.get("provider") or "builtin-llm")
        if (
            str(source_step.get("type") or "") != "agent_task"
            or str(source_step.get("execution_mode") or "") != "staged"
            or provider != "builtin-llm"
        ):
            raise TaskConfigurationError(
                "测试设计脑图只能连接到内置模型分阶段分析节点；"
                f"当前来源节点为 {source_id or '未指定'}"
            )

    artifacts: set[str] = set()
    for output in effective_outputs:
        # Migrated read-only workflows may expose terminal-only outputs. Keep
        # those runnable, while every new/custom file output remains strict.
        if not str(output.get("artifact") or "").strip():
            continue
        output_id = str(output.get("id") or "")
        declared = [output.get("artifact"), *(output.get("companion_artifacts") or [])]
        for raw_artifact in declared:
            artifact = _artifact(raw_artifact, output_id=output_id)
            if artifact in artifacts:
                raise TaskConfigurationError(f"artifact 文件名重复：{artifact}")
            artifacts.add(artifact)
    for step_id, step in steps.items():
        required = []
        for artifact in step.get("required_artifacts") or []:
            next_artifact = artifact_renames.get((step_id, str(artifact)), str(artifact))
            if next_artifact in artifacts or str(step.get("execution_mode") or "") == "staged":
                required.append(next_artifact)
        for output in effective_outputs:
            if output.get("from") != step_id:
                continue
            for artifact in [output.get("artifact"), *(output.get("companion_artifacts") or [])]:
                artifact = str(artifact or "").strip()
                if artifact and artifact not in required:
                    required.append(artifact)
        step["required_artifacts"] = required

    definition["outputs"] = effective_outputs
    for node in plan_nodes.values():
        node["output_contracts"] = [copy.deepcopy(item) for item in effective_outputs if item.get("from") == node.get("node_id")]
    return {"compiled_definition": definition, "compiled_plan": plan}


def _artifact(value: Any, *, output_id: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise TaskConfigurationError(f"输出 {output_id} 缺少 artifact 文件名")
    posix = PurePosixPath(text)
    windows = PureWindowsPath(text)
    if posix.is_absolute() or windows.is_absolute() or ".." in posix.parts or ".." in windows.parts or text.endswith(("/", "\\")):
        raise TaskConfigurationError(f"artifact 路径不安全：{text}")
    return text.replace("\\", "/")


def _validate_execution_value(step_id: str, field: str, value: Any) -> None:
    if field == "provider" and (not isinstance(value, str) or not value.strip()):
        raise TaskConfigurationError(f"{step_id}.provider 必须是非空执行器 ID")
    if field in {"mcp_profiles", "skill_ids"}:
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            raise TaskConfigurationError(f"{step_id}.{field} 必须是结构化字符串数组")
        if len(set(value)) != len(value):
            raise TaskConfigurationError(f"{step_id}.{field} 不能包含重复 ID")
    if field in {"timeout_sec", "idle_timeout_sec"} and (
        not isinstance(value, int) or isinstance(value, bool) or value <= 0
    ):
        raise TaskConfigurationError(f"{step_id}.{field} 必须是正整数")
    if field == "failure_policy" and value not in {"stop", "continue_independent"}:
        raise TaskConfigurationError(f"{step_id}.failure_policy 不受支持：{value}")
    if field == "retry_policy" and value != {"max_attempts": 1, "backoff_seconds": 0}:
        raise TaskConfigurationError(f"{step_id}.retry_policy 当前只支持一次立即执行")
