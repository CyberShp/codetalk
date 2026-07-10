"""Shared AgentInvocation contract helpers.

This module is intentionally small: AI threads and Workbench workflows still
own their execution paths, but the public invocation contract should not drift
between them.
"""

from __future__ import annotations

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
