"""Deterministic serial DAG scheduler for compiled Workbench plans."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


SUCCESS_STATUSES = frozenset({"completed", "completed_empty", "needs_review", "succeeded", "success"})
WAITING_STATUSES = frozenset({"waiting_for_input"})
_NODE_FAILURE_MESSAGE = "节点执行失败，请重试。"


@dataclass(frozen=True)
class WorkflowScheduleResult:
    status: str
    ordered_results: list[dict[str, Any]] = field(default_factory=list)
    results_by_node: dict[str, dict[str, Any]] = field(default_factory=dict)


class WorkflowDagScheduler:
    def __init__(
        self,
        *,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._event_sink = event_sink

    def run(
        self,
        plan: dict[str, Any],
        *,
        execute_node: Callable[[dict[str, Any], dict[str, dict[str, Any]]], dict[str, Any]],
        seed_results: dict[str, dict[str, Any]] | None = None,
    ) -> WorkflowScheduleResult:
        nodes = {
            str(item.get("node_id") or ""): dict(item)
            for item in plan.get("nodes") or []
            if isinstance(item, dict) and str(item.get("node_id") or "")
        }
        order = [str(item) for item in plan.get("topological_order") or []]
        if set(order) != set(nodes) or len(order) != len(nodes):
            raise ValueError("compiled plan topological_order does not match its nodes")
        if int(plan.get("max_parallelism") or 1) != 1:
            raise ValueError("Workbench V2 scheduler currently supports max_parallelism = 1")
        seen: set[str] = set()
        for node_id in order:
            dependencies = set(_dependencies(nodes[node_id]))
            if not dependencies <= seen:
                raise ValueError(f"compiled plan order violates dependencies for node {node_id}")
            seen.add(node_id)

        ordered_results: list[dict[str, Any]] = []
        results: dict[str, dict[str, Any]] = {}
        reusable = {
            str(node_id): dict(result)
            for node_id, result in (seed_results or {}).items()
            if str(node_id) in nodes
            and isinstance(result, dict)
            and str(result.get("status") or "") in SUCCESS_STATUSES
        }
        stop_remaining = False
        stop_source = ""
        for node_id in order:
            node = nodes[node_id]
            self._emit("node_queued", {"node_id": node_id})
            if node_id in reusable:
                reused = reusable[node_id]
                reused.setdefault("node_id", node_id)
                reused.setdefault("type", str(node.get("type") or ""))
                reused.setdefault("validated_outputs", {})
                reused.setdefault("direct_dependencies", {})
                results[node_id] = reused
                ordered_results.append(reused)
                self._emit(
                    "node_reused",
                    {
                        "node_id": node_id,
                        "source_task_run_id": str(reused.get("reused_from_task_run_id") or ""),
                    },
                )
                continue
            dependencies = _dependencies(node)
            blocked_by = [
                dependency
                for dependency in dependencies
                if str(results.get(dependency, {}).get("status") or "") not in SUCCESS_STATUSES
            ]
            if stop_remaining or blocked_by:
                waiting_blockers = [
                    dependency
                    for dependency in blocked_by
                    if str(results.get(dependency, {}).get("status") or "")
                    in WAITING_STATUSES
                ]
                blocked = {
                    "node_id": node_id,
                    "type": str(node.get("type") or ""),
                    "status": "blocked",
                    "blocked_by": blocked_by or ([stop_source] if stop_source else []),
                    "reason": (
                        "upstream_waiting"
                        if waiting_blockers
                        else "upstream_failed"
                        if blocked_by
                        else "run_paused"
                        if stop_source
                        and str(results.get(stop_source, {}).get("status") or "")
                        in WAITING_STATUSES
                        else "run_stopped_after_failure"
                    ),
                    "validated_outputs": {},
                    "direct_dependencies": {},
                }
                results[node_id] = blocked
                ordered_results.append(blocked)
                self._emit("node_blocked", dict(blocked))
                continue
            direct_outputs = {
                dependency: dict(results[dependency].get("validated_outputs") or {})
                for dependency in dependencies
            }
            self._emit("node_started", {"node_id": node_id, "depends_on": dependencies})
            try:
                result = dict(execute_node(node, direct_outputs) or {})
            except Exception as exc:
                result = {
                    "node_id": node_id,
                    "type": str(node.get("type") or ""),
                    "status": "failed",
                    "technical_diagnostics": {
                        "exception_type": type(exc).__name__,
                        "message": str(exc),
                    },
                    "validated_outputs": {},
                }
            result.setdefault("node_id", node_id)
            result.setdefault("type", str(node.get("type") or ""))
            result.setdefault("status", "failed")
            result.setdefault("validated_outputs", {})
            result["direct_dependencies"] = direct_outputs
            results[node_id] = result
            ordered_results.append(result)
            if str(result["status"]) in SUCCESS_STATUSES:
                self._emit("node_completed", dict(result))
            elif str(result["status"]) in WAITING_STATUSES:
                self._emit("node_waiting", dict(result))
                stop_remaining = True
                stop_source = node_id
            else:
                _normalize_failure_result(result)
                self._emit("node_failed", _public_failure_event_payload(result))
                if str(node.get("failure_policy") or "stop") == "stop":
                    stop_remaining = True
                    stop_source = node_id
        status = (
            "waiting_for_input"
            if any(
                str(item.get("status") or "") in WAITING_STATUSES
                for item in ordered_results
            )
            else "succeeded"
            if ordered_results
            and all(
                str(item.get("status") or "") in SUCCESS_STATUSES
                for item in ordered_results
            )
            else "succeeded"
            if not ordered_results
            else "failed"
        )
        self._emit("run_completed", {"status": status})
        return WorkflowScheduleResult(
            status=status,
            ordered_results=ordered_results,
            results_by_node=results,
        )

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._event_sink is not None:
            self._event_sink(event_type, payload)


def _dependencies(node: dict[str, Any]) -> list[str]:
    return sorted({str(item) for item in node.get("depends_on") or [] if str(item)})


def _normalize_failure_result(result: dict[str, Any]) -> None:
    diagnostics = result.get("technical_diagnostics")
    clean_diagnostics = dict(diagnostics) if isinstance(diagnostics, dict) else {}
    raw_error = result.get("error")
    if raw_error not in (None, ""):
        clean_diagnostics.setdefault("error", raw_error)
    if clean_diagnostics:
        result["technical_diagnostics"] = clean_diagnostics
    result["error"] = _NODE_FAILURE_MESSAGE


def _public_failure_event_payload(result: dict[str, Any]) -> dict[str, Any]:
    payload = dict(result)
    payload.pop("technical_diagnostics", None)
    return payload
