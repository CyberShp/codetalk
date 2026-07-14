"""Public, immutable execution snapshots for AI conversation runs."""

from __future__ import annotations

from typing import Any


def build_ai_run_snapshot(
    *,
    conversation: dict[str, Any],
    runtime: dict[str, Any] | None,
    references: list[dict[str, Any]],
    session_mode: str = "fresh",
) -> dict[str, Any]:
    context = conversation.get("initial_context")
    context = context if isinstance(context, dict) else {}
    workflow_binding = _workflow_binding_snapshot(context)
    scope_type = str(conversation.get("scope_type") or "")
    execution_mode = (
        "task_run_review"
        if scope_type == "workbench_task_run"
        else "workflow_constraint"
        if workflow_binding.get("workflow_id")
        else "free_qa"
    )
    runtime_type = str(conversation.get("runtime_type") or "builtin_llm")
    runtime_id = str(conversation.get("agent_runtime_id") or "").strip()
    runtime_snapshot = _runtime_snapshot(
        runtime_type=runtime_type,
        runtime_id=runtime_id,
        runtime=runtime,
        session_mode=session_mode,
    )
    mcp_profiles = _split_profiles(str((runtime or {}).get("mcp_profile") or ""))
    skills = [
        str(item).strip()
        for item in (runtime or {}).get("skills") or []
        if str(item).strip()
    ]
    source_types = sorted(
        {
            str(item.get("source_type") or "")
            for item in references
            if isinstance(item, dict) and str(item.get("source_type") or "")
        }
    )
    return {
        "execution_mode": execution_mode,
        "runtime_type": runtime_type,
        "agent_runtime_id": runtime_id or None,
        "runtime_snapshot": runtime_snapshot,
        "workflow_binding_snapshot": workflow_binding,
        "skills_snapshot": skills,
        "mcp_snapshot": mcp_profiles,
        "context_summary": {
            "scope_type": scope_type or "unknown",
            "workspace_id": str(conversation.get("workspace_id") or "global"),
            "reference_count": len(references),
            "source_types": source_types,
        },
        "artifact_contract": _artifact_contract(workflow_binding),
        "metrics": {},
    }


def _runtime_snapshot(
    *,
    runtime_type: str,
    runtime_id: str,
    runtime: dict[str, Any] | None,
    session_mode: str,
) -> dict[str, Any]:
    if runtime_type != "agent_runtime":
        return {
            "recorded": True,
            "id": "builtin-llm",
            "name": "内置模型",
            "provider": "builtin_llm",
            "prompt_transport": "api",
            "session_mode": "fresh",
        }
    payload = runtime or {}
    return {
        "recorded": True,
        "id": str(payload.get("id") or runtime_id),
        "name": str(payload.get("name") or runtime_id or "未命名 Agent"),
        "provider": _runtime_provider(payload, runtime_id),
        "prompt_transport": str(payload.get("prompt_transport") or "stdin"),
        "session_mode": "resume" if session_mode == "resume" else "fresh",
        "session_persistence": str(payload.get("session_persistence") or "none"),
    }


def _runtime_provider(runtime: dict[str, Any], runtime_id: str) -> str:
    explicit = str(runtime.get("provider") or "").strip()
    if explicit:
        return explicit
    value = str(runtime_id or runtime.get("id") or "").lower()
    for provider in ("codex", "claude", "opencode", "nga"):
        if provider in value:
            return provider
    return f"agent-runtime:{runtime_id}" if runtime_id else "agent-runtime"


def _workflow_binding_snapshot(context: dict[str, Any]) -> dict[str, Any]:
    existing = context.get("workflow_binding_snapshot")
    if isinstance(existing, dict) and existing:
        return dict(existing)
    workflow_id = str(context.get("selected_workflow_id") or "").strip()
    if not workflow_id:
        return {"recorded": False, "status": "unbound", "label": "未绑定工作流"}
    version_id = str(context.get("selected_workflow_version_id") or "").strip()
    return {
        "recorded": bool(version_id),
        "status": "recorded" if version_id else "legacy",
        "workflow_id": workflow_id,
        "workflow_version_id": version_id or "",
        "workflow_name": str(context.get("selected_workflow_name") or workflow_id),
        "mode": "constraint_answer",
        "label": "已固定工作流版本" if version_id else "旧绑定未记录版本",
    }


def _artifact_contract(workflow_binding: dict[str, Any]) -> dict[str, Any]:
    contract = workflow_binding.get("artifact_contract")
    if isinstance(contract, dict):
        return dict(contract)
    outputs = workflow_binding.get("outputs")
    if not isinstance(outputs, list):
        return {}
    return {
        "required_outputs": [
            str(item.get("artifact") or item.get("id") or "")
            for item in outputs
            if isinstance(item, dict)
            and bool(item.get("required", True))
            and str(item.get("artifact") or item.get("id") or "")
        ]
    }


def _split_profiles(value: str) -> list[str]:
    return list(
        dict.fromkeys(
            item.strip()
            for item in str(value or "").split("+")
            if item.strip()
        )
    )
