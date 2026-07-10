"""Shared AgentInvocation contract helpers.

This module is intentionally small: AI threads and Workbench workflows still
own their execution paths, but the public invocation contract should not drift
between them.
"""

from __future__ import annotations

from pathlib import Path, PureWindowsPath
from typing import Any


AGENT_INVOCATION_TYPED_EVENTS = (
    "answer",
    "thinking",
    "diagnostic",
    "status",
    "tool_use",
    "tool_result",
    "artifact",
    "error",
    "done",
)


def agent_invocation_typed_events() -> list[str]:
    return list(AGENT_INVOCATION_TYPED_EVENTS)


def build_agent_invocation_execution_contract(
    *,
    runtime_type: str = "agent_runtime",
    source_first: bool,
    cwd: str,
    repo_path: str,
    outputs: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contract = dict(extra or {})
    contract.update(
        {
            "runtime_type": runtime_type,
            "source_first": bool(source_first),
            "must_receive_full_user_input": True,
            "cwd": str(cwd or ""),
            "repo_path": str(repo_path or cwd or ""),
            "typed_events": agent_invocation_typed_events(),
        }
    )
    if outputs is not None and "outputs" not in contract:
        contract["outputs"] = outputs
    return contract


def agent_invocation_artifact_event_payload(
    manifest: dict[str, Any],
    *,
    artifact: str = "agent_invocation.json",
    content: str = "AgentInvocation 已准备",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the public artifact event payload shared by AI threads and workflows."""

    runtime = manifest.get("runtime") if isinstance(manifest.get("runtime"), dict) else {}
    session = manifest.get("session") if isinstance(manifest.get("session"), dict) else {}
    execution_contract = (
        manifest.get("execution_contract")
        if isinstance(manifest.get("execution_contract"), dict)
        else {}
    )
    test_activity_contract = (
        manifest.get("test_activity_contract")
        if isinstance(manifest.get("test_activity_contract"), dict)
        else {}
    )
    artifact_contract = (
        manifest.get("artifact_contract")
        if isinstance(manifest.get("artifact_contract"), dict)
        else {}
    )
    test_activity_contract = (
        manifest.get("test_activity_contract")
        if isinstance(manifest.get("test_activity_contract"), dict)
        else {}
    )
    repo_path = str(manifest.get("repo_path") or manifest.get("cwd") or "")
    payload: dict[str, Any] = {
        "artifact": artifact,
        "artifact_kind": "agent_invocation",
        "content": content,
        "runtime": {
            "id": str(runtime.get("id") or ""),
            "name": str(runtime.get("name") or ""),
            "provider": str(runtime.get("provider") or manifest.get("provider") or ""),
            "prompt_transport": str(runtime.get("prompt_transport") or ""),
            "output_mode": str(runtime.get("output_mode") or ""),
            "completion_mode": str(runtime.get("completion_mode") or ""),
            "working_dir_mode": str(runtime.get("working_dir_mode") or ""),
        },
        "cwd_label": _public_path_name(str(manifest.get("cwd") or repo_path)),
        "repo_label": _public_path_name(repo_path),
        "mcp_profile": str(manifest.get("mcp_profile") or ""),
        "skills": [str(item) for item in manifest.get("skills") or [] if str(item).strip()],
        "session": {
            "persistence": str(session.get("persistence") or ""),
            "mode": str(session.get("mode") or ""),
        },
        "execution_contract": _public_execution_contract_event_payload(execution_contract),
        "test_activity_contract": _public_test_activity_contract_event_payload(
            test_activity_contract
        ),
        "artifact_contract": _public_artifact_contract_event_payload(
            artifact_contract,
            required_outputs=test_activity_contract.get("required_outputs"),
        ),
    }
    if extra:
        payload.update(extra)
    return payload


def agent_invocation_capability_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Summarize what capabilities this invocation received and what is degraded."""

    runtime = manifest.get("runtime") if isinstance(manifest.get("runtime"), dict) else {}
    execution_contract = (
        manifest.get("execution_contract")
        if isinstance(manifest.get("execution_contract"), dict)
        else {}
    )
    task_bundle = (
        manifest.get("task_bundle")
        if isinstance(manifest.get("task_bundle"), dict)
        else {}
    )
    mcp_contract = execution_contract.get("mcp") if isinstance(execution_contract.get("mcp"), dict) else {}
    skills_contract = (
        execution_contract.get("skills")
        if isinstance(execution_contract.get("skills"), dict)
        else {}
    )
    outputs = (
        execution_contract.get("outputs")
        if isinstance(execution_contract.get("outputs"), dict)
        else {}
    )
    artifact_contract = (
        manifest.get("artifact_contract")
        if isinstance(manifest.get("artifact_contract"), dict)
        else {}
    )
    test_activity_contract = (
        manifest.get("test_activity_contract")
        if isinstance(manifest.get("test_activity_contract"), dict)
        else {}
    )
    capability_status = _capability_status(mcp_contract)
    return {
        "schema_version": 1,
        "source": str(manifest.get("source") or "agent_invocation"),
        "run_id": str(manifest.get("run_id") or ""),
        "turn_id": str(manifest.get("turn_id") or ""),
        "runtime": {
            "provider": str(runtime.get("provider") or manifest.get("provider") or ""),
            "name": str(runtime.get("name") or ""),
            "prompt_transport": str(runtime.get("prompt_transport") or ""),
            "cwd_label": _public_path_name(str(manifest.get("cwd") or "")),
            "repo_label": _public_path_name(str(manifest.get("repo_path") or manifest.get("cwd") or "")),
        },
        "input_contract": {
            "must_receive_full_user_input": bool(
                execution_contract.get("must_receive_full_user_input")
            ),
            "source_first": execution_contract.get("source_first"),
            "material_count": _safe_int(
                (task_bundle.get("input_materials") or {}).get("material_count")
                if isinstance(task_bundle.get("input_materials"), dict)
                else 0
            ),
            "user_input_count": len(_list_like(task_bundle.get("user_inputs"))),
        },
        "mcp": {
            "profile": str(manifest.get("mcp_profile") or mcp_contract.get("profile") or ""),
            "status": capability_status,
            "availability": mcp_contract.get("availability") if mcp_contract else {},
            "request_count": len(_list_like(mcp_contract.get("requests"))),
            "credential_owner": "agent_cli" if manifest.get("mcp_profile") else "",
            "degraded": capability_status in {"codetalk_prefetch", "direct_unverified", "unavailable"},
        },
        "skills": {
            "ids": _unique_string_list(
                manifest.get("skills")
                or skills_contract.get("ids")
                or task_bundle.get("skills")
                or []
            ),
            "instruction_count": len(_list_like(skills_contract.get("instructions"))),
            "rule": str(skills_contract.get("rule") or ""),
        },
        "outputs": {
            "required_artifacts": _unique_string_list(
                outputs.get("required_artifacts")
                or artifact_contract.get("required_artifacts")
                or artifact_contract.get("required_outputs")
                or test_activity_contract.get("required_outputs")
                or []
            ),
            "declared_output_count": len(_list_like(outputs.get("declared_outputs"))),
            "expected_schema_count": len(_list_like(outputs.get("expected_output_schemas"))),
            "artifact_dir": str(manifest.get("artifact_dir") or ""),
        },
        "typed_events": agent_invocation_typed_events(),
    }


def agent_invocation_capability_event_payload(
    manifest: dict[str, Any],
    *,
    artifact: str = "capability_manifest.json",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    capability = agent_invocation_capability_manifest(manifest)
    payload: dict[str, Any] = {
        "artifact": artifact,
        "artifact_kind": "capability_manifest",
        "content": "执行器能力边界已记录，MCP、skills、输入与输出目标可核验。",
        "related_artifacts": ["agent_invocation.json"],
        "runtime": capability["runtime"],
        "input_contract": capability["input_contract"],
        "mcp": capability["mcp"],
        "skills": capability["skills"],
        "outputs": capability["outputs"],
        "typed_events": capability["typed_events"],
    }
    if extra:
        payload.update(extra)
    return payload


def _public_execution_contract_event_payload(
    execution_contract: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "typed_events": execution_contract.get("typed_events") or [],
        "source_first": execution_contract.get("source_first"),
        "must_receive_full_user_input": bool(
            execution_contract.get("must_receive_full_user_input")
        ),
        "outputs": _public_execution_outputs_event_payload(execution_contract),
    }
    mcp = execution_contract.get("mcp")
    if isinstance(mcp, dict):
        payload["mcp"] = {
            "availability": mcp.get("availability"),
            "profiles": mcp.get("profiles"),
            "degraded": mcp.get("degraded"),
            "reason": str(mcp.get("reason") or ""),
        }
    return payload


def _capability_status(mcp_contract: dict[str, Any]) -> str:
    availability = (
        mcp_contract.get("availability")
        if isinstance(mcp_contract.get("availability"), dict)
        else {}
    )
    status = str(availability.get("status") or "").strip()
    if status:
        return status
    if not mcp_contract:
        return "not_declared"
    if not str(mcp_contract.get("profile") or "").strip():
        return "not_requested"
    return "unknown"


def _list_like(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _unique_string_list(value: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in _list_like(value):
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _public_execution_outputs_event_payload(execution_contract: dict[str, Any]) -> dict[str, Any]:
    outputs = (
        execution_contract.get("outputs")
        if isinstance(execution_contract.get("outputs"), dict)
        else {}
    )
    requested = [
        {
            "source": str(item.get("source") or ""),
            "items": [str(value) for value in item.get("items") or [] if str(value).strip()],
        }
        for item in outputs.get("user_requested_outputs") or []
        if isinstance(item, dict)
    ]
    return {"user_requested_outputs": requested} if requested else {}


def _public_test_activity_contract_event_payload(contract: dict[str, Any]) -> dict[str, Any]:
    if not contract:
        return {}
    return {
        "target": str(contract.get("target") or ""),
        "domain_profiles": [
            str(item) for item in contract.get("domain_profiles") or [] if str(item).strip()
        ],
        "required_outputs": [
            str(item) for item in contract.get("required_outputs") or [] if str(item).strip()
        ],
    }


def _public_artifact_contract_event_payload(
    contract: dict[str, Any],
    *,
    required_outputs: Any = None,
) -> dict[str, Any]:
    outputs = [
        str(item)
        for item in (
            contract.get("required_outputs")
            or contract.get("required_artifacts")
            or required_outputs
            or []
        )
        if str(item).strip()
    ]
    template_source = (
        contract.get("artifact_contract")
        if isinstance(contract.get("artifact_contract"), dict)
        else contract
    )
    if not contract and not outputs:
        return {}
    return {
        "required_outputs": outputs,
        "templates": sorted(
            str(key)
            for key in template_source.keys()
            if str(key).strip() and isinstance(template_source, dict)
        ),
        "artifact_dir_policy": str(contract.get("artifact_dir_policy") or ""),
        "download_delivery": bool(contract.get("download_delivery")),
    }


def _public_path_name(path: str) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    try:
        if "\\" in text:
            return PureWindowsPath(text).name or text
        return Path(text).expanduser().name or text
    except (OSError, RuntimeError):
        return text
