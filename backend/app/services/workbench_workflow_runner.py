"""Execute prepared Agent Workbench workflow task runs."""

from __future__ import annotations

import json
import hashlib
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.agent_run_harness import (
    AgentRunHarness,
    ArtifactValidationHarness,
)
from app.services.workbench_artifact_manifest import write_task_artifact_manifest
from app.services.workbench_task_run import WorkbenchTaskRunStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class WorkbenchWorkflowExecutionResult:
    task_run_id: str
    status: str
    started_at: str
    completed_at: str
    context_discovery_decision: dict[str, Any] = field(default_factory=dict)
    audit_summary: dict[str, Any] = field(default_factory=dict)
    rerun_plan: dict[str, Any] = field(default_factory=dict)
    step_results: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[dict[str, Any]] = field(default_factory=list)


class WorkbenchWorkflowRunner:
    """Runs the executable steps of a previously prepared workbench task."""

    def __init__(self, artifact_root: str | Path) -> None:
        self.artifact_root = Path(artifact_root)
        self.store = WorkbenchTaskRunStore(self.artifact_root)

    def execute_task_run(
        self,
        task_run_id: str,
        *,
        timeout_sec: int = 90,
        stop_on_error: bool = True,
    ) -> WorkbenchWorkflowExecutionResult:
        task_run = self.store.load(task_run_id)
        started_at = _now()
        step_results: list[dict[str, Any]] = []
        agent_runs_by_step = {
            str(item.get("step_id") or ""): item
            for item in task_run.agent_runs
            if isinstance(item, dict)
        }

        for step in task_run.workflow_snapshot.get("steps") or []:
            if not isinstance(step, dict):
                continue
            step_id = str(step.get("id") or "")
            step_type = str(step.get("type") or "")
            if step_type != "agent_task":
                step_result = self._execute_builtin_step(
                    task_run=task_run,
                    step=step,
                    prior_step_results=step_results,
                )
                step_results.append(step_result)
                if stop_on_error and step_result.get("status") in {"error", "invalid"}:
                    break
                continue

            agent_run = agent_runs_by_step.get(step_id)
            if not agent_run:
                step_results.append({
                    "step_id": step_id,
                    "type": step_type,
                    "status": "error",
                    "error": "missing_agent_run",
                })
                if stop_on_error:
                    break
                continue

            step_result = self._execute_agent_step(
                task_run_id=task_run.task_run_id,
                step=step,
                agent_run=agent_run,
                prior_step_results=step_results,
                timeout_sec=timeout_sec,
            )
            step_results.append(step_result)
            if stop_on_error and step_result.get("status") != "completed":
                break

        outputs = self._collect_workflow_outputs(
            workflow_snapshot=task_run.workflow_snapshot,
            step_results=step_results,
        )
        status = _overall_status(step_results)
        if status == "completed" and any(
            item.get("status") in {"missing", "invalid"} for item in outputs
        ):
            status = "invalid"
        result = WorkbenchWorkflowExecutionResult(
            task_run_id=task_run.task_run_id,
            status=status,
            started_at=started_at,
            completed_at=_now(),
            context_discovery_decision=dict(
                task_run.task_bundle.get("context_discovery_decision") or {}
            ),
            audit_summary=_workflow_execution_audit_summary(
                step_results=step_results,
            ),
            rerun_plan=_workflow_rerun_plan(
                task_run=task_run,
                status=status,
                step_results=step_results,
                outputs=outputs,
            ),
            step_results=step_results,
            outputs=outputs,
        )
        self._write_execution_artifact(task_run.task_run_id, result)
        return result

    def _execute_agent_step(
        self,
        *,
        task_run_id: str,
        step: dict[str, Any],
        agent_run: dict[str, Any],
        prior_step_results: list[dict[str, Any]],
        timeout_sec: int,
    ) -> dict[str, Any]:
        step_id = str(step.get("id") or agent_run.get("step_id") or "")
        artifact_dir = Path(str(agent_run.get("artifact_dir") or ""))
        run_payload = _read_json(artifact_dir / "agent_run.json")
        run_id = str((run_payload or {}).get("run_id") or agent_run.get("run_id") or "")
        if not run_id:
            return {
                "step_id": step_id,
                "type": "agent_task",
                "status": "error",
                "error": "missing_run_id",
            }

        _inject_prior_step_context(
            artifact_dir=artifact_dir,
            prior_step_results=prior_step_results,
        )
        execution = AgentRunHarness(artifact_dir).execute_run(
            run_id,
            timeout_sec=timeout_sec,
        )
        executions = [asdict(execution)]
        turn_artifacts = [_snapshot_agent_turn_artifacts(artifact_dir, turn_id="turn_1")]
        source_slice_requests = _agent_source_slice_requests(artifact_dir)
        injected_source_slices: list[dict[str, Any]] = []
        source_slice_warnings: list[str] = []
        if source_slice_requests:
            injected_source_slices, source_slice_warnings = _materialize_requested_source_slices(
                repo_path=str((run_payload or {}).get("cwd") or ""),
                requests=source_slice_requests,
            )
            _write_json(artifact_dir / "source_slices.json", injected_source_slices)
            _inject_requested_source_slices(
                artifact_dir=artifact_dir,
                source_slices=injected_source_slices,
                warnings=source_slice_warnings,
            )
            _set_agent_turn_id(artifact_dir=artifact_dir, turn_id="turn_2")
            execution = AgentRunHarness(artifact_dir).execute_run(
                run_id,
                timeout_sec=timeout_sec,
            )
            executions.append(asdict(execution))
            turn_artifacts.append(_snapshot_agent_turn_artifacts(artifact_dir, turn_id="turn_2"))
        required_artifacts = [
            str(item)
            for item in (
                step.get("required_artifacts")
                or agent_run.get("required_artifacts")
                or []
            )
        ]
        validation = _validate_step_artifacts(artifact_dir, required_artifacts)
        status = (
            "completed"
            if execution.status == "completed" and validation.status == "ok"
            else "invalid"
            if validation.status != "ok"
            else execution.status
        )
        step_payload = {
            "step_id": step_id,
            "type": "agent_task",
            "status": status,
            "provider": agent_run.get("provider") or (run_payload or {}).get("provider") or "",
            "provider_diagnostics": _provider_diagnostics_summary(artifact_dir),
            "artifact_dir": str(artifact_dir),
            "execution": asdict(execution),
            "executions": executions,
            "turn_count": len(executions),
            "turn_artifacts": turn_artifacts,
            "source_slice_requests": source_slice_requests,
            "injected_source_slices": injected_source_slices,
            "source_slice_warnings": source_slice_warnings,
            "validation": asdict(validation),
            "required_artifacts": required_artifacts,
        }
        failure_recovery = _failure_recovery_summary(
            artifact_dir=artifact_dir,
            execution=asdict(execution),
            validation=asdict(validation),
        )
        if failure_recovery:
            step_payload["failure_recovery"] = failure_recovery
            _write_json(artifact_dir / "failure_recovery.json", failure_recovery)
        lifecycle = _agent_run_lifecycle_summary(
            step_id=step_id,
            status=status,
            artifact_dir=artifact_dir,
            executions=executions,
            turn_artifacts=turn_artifacts,
            validation=asdict(validation),
            required_artifacts=required_artifacts,
            source_slice_requests=source_slice_requests,
            injected_source_slices=injected_source_slices,
            failure_recovery=failure_recovery,
        )
        step_payload["lifecycle"] = lifecycle
        _write_json(artifact_dir / "agent_run_lifecycle.json", lifecycle)
        return step_payload

    def _execute_builtin_step(
        self,
        *,
        task_run: Any,
        step: dict[str, Any],
        prior_step_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        step_id = str(step.get("id") or "")
        step_type = str(step.get("type") or "")
        artifact_dir = (
            self.artifact_root
            / _safe_segment(task_run.task_run_id)
            / "steps"
            / _safe_segment(step_id)
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)

        context_bundle = task_run.task_bundle.get("context_bundle") or {}
        if step_type == "semantic_retrieve":
            payload = {
                "step_id": step_id,
                "type": step_type,
                "query": context_bundle.get("query") or "",
                "semantic_cases": context_bundle.get("semantic_cases") or [],
                "count": len(context_bundle.get("semantic_cases") or []),
            }
            artifact_path = artifact_dir / f"{step_id}.json"
            _write_json(artifact_path, payload)
            return _builtin_step_result(
                step_id,
                step_type,
                artifact_dir,
                artifact_path,
                payload["count"],
            )

        if step_type == "memory_retrieve":
            payload = {
                "step_id": step_id,
                "type": step_type,
                "query": context_bundle.get("query") or "",
                "evidence": context_bundle.get("evidence") or [],
                "count": len(context_bundle.get("evidence") or []),
            }
            artifact_path = artifact_dir / f"{step_id}.json"
            _write_json(artifact_path, payload)
            return _builtin_step_result(
                step_id,
                step_type,
                artifact_dir,
                artifact_path,
                payload["count"],
            )

        if step_type == "evidence_validate":
            payload = _evidence_validation_payload(
                task_run=task_run,
                step_id=step_id,
                prior_step_results=prior_step_results,
            )
            artifact_path = artifact_dir / f"{step_id}.json"
            _write_json(artifact_path, payload)
            _write_json(artifact_dir / "evidence_validation.json", payload)
            return _builtin_step_result(
                step_id,
                step_type,
                artifact_dir,
                artifact_path,
                payload.get("accepted_count", 0),
            )

        if step_type == "report_render":
            written = _render_report_artifacts(
                artifact_dir=artifact_dir,
                step=step,
                workflow_snapshot=task_run.workflow_snapshot,
                task_run=task_run,
                prior_step_results=prior_step_results,
            )
            return {
                "step_id": step_id,
                "type": step_type,
                "status": "completed",
                "artifact_dir": str(artifact_dir),
                "artifacts": written,
                "count": len(written),
            }

        if step_type == "diff_parse":
            payload = _diff_parse_payload(task_run.input_snapshot)
            parse_path = artifact_dir / f"{step_id}.json"
            changed_files_path = artifact_dir / "changed_files.json"
            summary_path = artifact_dir / "diff_summary.json"
            _write_json(parse_path, payload)
            _write_json(changed_files_path, payload["changed_files"])
            _write_json(summary_path, payload["summary"])
            return {
                "step_id": step_id,
                "type": step_type,
                "status": "completed",
                "artifact_dir": str(artifact_dir),
                "artifact": parse_path.name,
                "artifacts": [
                    parse_path.name,
                    changed_files_path.name,
                    summary_path.name,
                ],
                "count": len(payload["changed_files"]),
            }

        if step_type == "coverage_parse":
            payload = _coverage_parse_payload(task_run.input_snapshot)
            parse_path = artifact_dir / f"{step_id}.json"
            summary_path = artifact_dir / "coverage_summary.json"
            uncovered_path = artifact_dir / "uncovered_functions.json"
            _write_json(parse_path, payload)
            _write_json(summary_path, payload["summary"])
            _write_json(uncovered_path, payload["uncovered_functions"])
            return {
                "step_id": step_id,
                "type": step_type,
                "status": "completed",
                "artifact_dir": str(artifact_dir),
                "artifact": parse_path.name,
                "artifacts": [
                    parse_path.name,
                    summary_path.name,
                    uncovered_path.name,
                ],
                "count": len(payload["uncovered_functions"]),
            }

        if step_type in {"file_ingest", "artifact_export"}:
            payload = {
                "step_id": step_id,
                "type": step_type,
                "status": "completed",
                "inputs": task_run.input_snapshot,
                "message": "Built-in step captured prepared input snapshot for downstream Agent steps.",
            }
            artifact_path = artifact_dir / f"{step_id}.json"
            _write_json(artifact_path, payload)
            return _builtin_step_result(
                step_id,
                step_type,
                artifact_dir,
                artifact_path,
                len(task_run.input_snapshot),
            )

        payload = {
            "step_id": step_id,
            "type": step_type,
            "status": "skipped",
            "reason": "step type is not executable by Workbench runner",
        }
        artifact_path = artifact_dir / f"{step_id}.json"
        _write_json(artifact_path, payload)
        return {
            "step_id": step_id,
            "type": step_type,
            "status": "skipped",
            "artifact_dir": str(artifact_dir),
            "artifact": str(artifact_path),
            "reason": payload["reason"],
        }

    def _collect_workflow_outputs(
        self,
        *,
        workflow_snapshot: dict[str, Any],
        step_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        outputs: list[dict[str, Any]] = []
        steps_by_id = {
            str(item.get("step_id") or ""): item
            for item in step_results
            if isinstance(item, dict)
        }
        for output in workflow_snapshot.get("outputs") or []:
            if not isinstance(output, dict):
                continue
            output_id = str(output.get("id") or "").strip()
            output_type = str(output.get("type") or "").strip()
            source_step = str(output.get("from") or output.get("source") or "").strip()
            artifact_name = str(output.get("artifact") or output.get("path") or "").strip()
            item: dict[str, Any] = {
                "id": output_id,
                "type": output_type,
                "from": source_step,
                "artifact": artifact_name,
                "status": "unresolved",
            }
            step_result = steps_by_id.get(source_step) if source_step else None
            if step_result is None and not source_step:
                inferred = _infer_output_step(steps_by_id, artifact_name)
                if inferred is not None:
                    source_step, step_result = inferred
                    item["from"] = source_step
            if not step_result:
                item.update({
                    "status": "missing",
                    "reason": "source step was not declared or executed",
                })
                outputs.append(item)
                continue
            artifact_dir = Path(str(step_result.get("artifact_dir") or ""))
            if not artifact_name:
                artifact_name = _infer_output_artifact_name(
                    output=output,
                    step_result=step_result,
                )
                item["artifact"] = artifact_name
            if not artifact_name:
                item["reason"] = "output artifact is not declared"
                outputs.append(item)
                continue
            artifact_path = _resolve_artifact_path(artifact_dir, artifact_name)
            if artifact_path is None:
                item.update({
                    "status": "invalid",
                    "reason": "artifact path is unsafe",
                })
                outputs.append(item)
                continue
            if not artifact_path.exists() or not artifact_path.is_file():
                item.update({
                    "status": "missing",
                    "reason": "artifact file was not produced",
                    "path": str(artifact_path),
                })
                outputs.append(item)
                continue
            data = artifact_path.read_bytes()
            schema_errors = _validate_output_schema(
                output=output,
                data=data,
                artifact_path=artifact_path,
            )
            if schema_errors:
                item.update({
                    "status": "invalid",
                    "reason": "schema_validation_failed",
                    "path": str(artifact_path),
                    "schema_errors": schema_errors,
                })
                outputs.append(item)
                continue
            item.update({
                "status": "ok",
                "path": str(artifact_path),
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
                "preview": _preview_bytes(data),
            })
            outputs.append(item)
        return outputs

    def _write_execution_artifact(
        self,
        task_run_id: str,
        result: WorkbenchWorkflowExecutionResult,
    ) -> None:
        task_dir = self.artifact_root / _safe_segment(task_run_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        payload = asdict(result)
        (task_dir / "workflow_execution.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (task_dir / "workflow_outputs.json").write_text(
            json.dumps(
                {
                    "task_run_id": result.task_run_id,
                    "status": result.status,
                    "outputs": result.outputs,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        (task_dir / "task_rerun_plan.json").write_text(
            json.dumps(result.rerun_plan, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        write_task_artifact_manifest(task_dir, task_run_id=result.task_run_id)


def _validate_output_schema(
    *,
    output: dict[str, Any],
    data: bytes,
    artifact_path: Path,
) -> list[str]:
    schema = output.get("schema") or output.get("json_schema")
    if not isinstance(schema, dict):
        return []
    if artifact_path.suffix.lower() != ".json":
        return ["schema validation requires a JSON artifact"]
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"invalid JSON: {exc}"]
    return _validate_json_schema_fragment(payload, schema)


def _validate_json_schema_fragment(payload: Any, schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_type = str(schema.get("type") or "").strip()
    if expected_type:
        type_error = _json_type_error(payload, expected_type)
        if type_error:
            errors.append(type_error)
            return errors
    if isinstance(payload, dict):
        for field in schema.get("required") or []:
            field_name = str(field)
            if field_name not in payload:
                errors.append(f"missing required field: {field_name}")
        properties = schema.get("properties") or {}
        if isinstance(properties, dict):
            for field_name, property_schema in properties.items():
                if field_name not in payload or not isinstance(property_schema, dict):
                    continue
                property_type = str(property_schema.get("type") or "").strip()
                if property_type:
                    type_error = _json_type_error(
                        payload[field_name],
                        property_type,
                        path=str(field_name),
                    )
                    if type_error:
                        errors.append(type_error)
    return errors


def _json_type_error(value: Any, expected_type: str, *, path: str = "$") -> str:
    validators = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    validator = validators.get(expected_type)
    if validator is None or validator(value):
        return ""
    return f"{path} expected {expected_type}"


def _validate_step_artifacts(
    artifact_dir: Path,
    required_artifacts: list[str],
):
    validator = ArtifactValidationHarness(artifact_dir)
    required = {str(item) for item in required_artifacts}
    if {"mr_snapshot.json", "diff.patch", "changed_files.json"}.issubset(required):
        return validator.validate_mr_artifacts(required_artifacts=required_artifacts)
    return validator.validate_required_artifacts(required_artifacts=required_artifacts)


SOURCE_EXTENSIONS = frozenset({
    ".c", ".h", ".cc", ".cpp", ".hpp", ".py", ".go", ".rs", ".java",
    ".ts", ".tsx", ".js", ".jsx",
})


def _agent_source_slice_requests(artifact_dir: Path) -> list[dict[str, Any]]:
    payload = _read_json(artifact_dir / "source_slice_requests.json")
    if payload is None:
        payload = _read_json(artifact_dir / "source_slices_request.json")
    if isinstance(payload, dict):
        raw_items = payload.get("need_source_slices") or payload.get("source_slices") or []
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []
    requests: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        file_path = str(item.get("file_path") or item.get("path") or "").strip()
        if not file_path:
            continue
        requests.append({
            "file_path": file_path.replace("\\", "/"),
            "start_line": _positive_int(item.get("start_line"), default=1),
            "end_line": _positive_int(item.get("end_line"), default=0),
            "symbol": str(item.get("symbol") or ""),
            "reason": str(item.get("reason") or "agent requested source slice"),
        })
    return requests[:24]


def _materialize_requested_source_slices(
    *,
    repo_path: str,
    requests: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    repo = Path(repo_path)
    slices: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        root = repo.resolve()
    except OSError:
        return [], ["repo_path could not be resolved"]
    for request in requests:
        file_path = str(request.get("file_path") or "")
        resolved = _resolve_repo_source_path(root, file_path)
        if resolved is None:
            warnings.append(f"{file_path}: rejected_source_path")
            continue
        try:
            data = resolved.read_bytes()
            text = data.decode("utf-8", errors="replace")
        except OSError:
            warnings.append(f"{file_path}: read_failed")
            continue
        lines = text.splitlines()
        if not lines:
            start_line = 1
            end_line = 1
            excerpt = ""
        else:
            start_line = max(1, int(request.get("start_line") or 1))
            requested_end = int(request.get("end_line") or 0)
            end_line = requested_end if requested_end >= start_line else start_line + 119
            end_line = min(len(lines), end_line)
            excerpt = "\n".join(lines[start_line - 1:end_line])
        slices.append({
            "file_path": resolved.relative_to(root).as_posix(),
            "start_line": start_line,
            "end_line": end_line,
            "symbol": str(request.get("symbol") or ""),
            "reason": str(request.get("reason") or ""),
            "sha256": hashlib.sha256(data).hexdigest(),
            "excerpt": excerpt,
        })
    return slices, warnings


def _resolve_repo_source_path(root: Path, file_path: str) -> Path | None:
    candidate = Path(file_path)
    if candidate.is_absolute():
        path = candidate
    else:
        path = root / candidate
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if resolved == root or root not in resolved.parents:
        return None
    if not resolved.exists() or not resolved.is_file():
        return None
    if resolved.suffix.lower() not in SOURCE_EXTENSIONS:
        return None
    return resolved


def _inject_requested_source_slices(
    *,
    artifact_dir: Path,
    source_slices: list[dict[str, Any]],
    warnings: list[str],
) -> None:
    bundle_path = artifact_dir / "task_bundle.json"
    bundle = _read_json(bundle_path)
    if not isinstance(bundle, dict):
        return
    bundle["requested_source_slices"] = source_slices
    bundle["source_slice_request_warnings"] = warnings
    _write_json(bundle_path, bundle)


def _set_agent_turn_id(*, artifact_dir: Path, turn_id: str) -> None:
    run_path = artifact_dir / "agent_run.json"
    payload = _read_json(run_path)
    if not isinstance(payload, dict):
        return
    payload["turn_id"] = turn_id
    _write_json(run_path, payload)


def _snapshot_agent_turn_artifacts(artifact_dir: Path, *, turn_id: str) -> str:
    safe_turn_id = _safe_segment(turn_id)
    turn_dir = artifact_dir / "turns" / safe_turn_id
    turn_dir.mkdir(parents=True, exist_ok=True)
    for filename in (
        "agent_run.json",
        "task_bundle.json",
        "workflow_snapshot.json",
        "provider_diagnostics.json",
        "execution_input.json",
        "execution_result.json",
        "raw_output.txt",
        "source_slice_requests.json",
        "source_slices.json",
    ):
        source = artifact_dir / filename
        if source.exists() and source.is_file():
            shutil.copy2(source, turn_dir / filename)
    return f"turns/{safe_turn_id}"


def _provider_diagnostics_summary(artifact_dir: Path) -> dict[str, Any]:
    payload = _read_json(artifact_dir / "provider_diagnostics.json")
    execution_input = _read_json(artifact_dir / "execution_input.json")
    if not isinstance(payload, dict):
        return {
            "artifact": "provider_diagnostics.json",
            "status": "missing",
            "health_status": "unknown",
        }
    diagnostics = payload.get("diagnostics")
    if not isinstance(diagnostics, dict):
        diagnostics = {}
    health = payload.get("health")
    if not isinstance(health, dict):
        health = {}
    summary = {
        "artifact": "provider_diagnostics.json",
        "provider": str(payload.get("provider") or ""),
        "status": str(payload.get("status") or ""),
        "owner": str(payload.get("owner") or ""),
        "agent_owned": bool(payload.get("agent_owned", False)),
        "codetalk_callable": bool(payload.get("codetalk_callable", False)),
        "health_status": str(health.get("status") or "unknown"),
        "launch_kind": str(health.get("launch_kind") or ""),
        "used_fallback": bool(health.get("used_fallback", False)),
        "startup_probe_endpoint": str(diagnostics.get("startup_probe_endpoint") or ""),
        "prompt_transport": str(
            diagnostics.get("startup_probe_transport")
            or diagnostics.get("prompt_transport")
            or ""
        ),
        "mcp_credentials_owner": str(diagnostics.get("mcp_credentials_owner") or ""),
    }
    if isinstance(execution_input, dict):
        summary.update(_command_resolution_summary(execution_input.get("command_resolution")))
    return summary


def _failure_recovery_summary(
    *,
    artifact_dir: Path,
    execution: dict[str, Any],
    validation: dict[str, Any],
) -> dict[str, Any]:
    execution_status = str(execution.get("status") or "")
    validation_status = str(validation.get("status") or "")
    if execution_status == "completed" and validation_status == "ok":
        return {}
    if execution.get("timed_out"):
        failure_kind = "agent_timeout"
    elif execution_status and execution_status != "completed":
        failure_kind = "agent_error"
    elif validation_status and validation_status != "ok":
        failure_kind = "artifact_validation_failed"
    else:
        failure_kind = "unknown"
    missing_artifacts = [
        str(item.get("artifact") or "")
        for item in validation.get("rejected_artifact_details") or []
        if isinstance(item, dict)
        and item.get("reason") == "missing_required_artifact"
        and str(item.get("artifact") or "")
    ]
    actions = ["inspect raw_output.txt and execution_result.json"]
    if failure_kind == "agent_timeout":
        actions.append("increase timeout or narrow the Agent task scope before rerun")
    else:
        actions.append(
            "rerun the step after fixing provider command, MCP credentials, or agent prompt"
        )
    if validation_status != "ok":
        actions.append("do not materialize outputs until required artifacts validate")
    provider_diagnostics = _provider_failure_diagnostics_summary(artifact_dir)
    if provider_diagnostics.get("health_status") in {"unavailable", "configuration_error", "error"}:
        endpoint = str(provider_diagnostics.get("startup_probe_endpoint") or "").strip()
        if endpoint:
            actions.append(f"run startup probe {endpoint} to verify backend launch context")
    return {
        "failure_kind": failure_kind,
        "retryable": failure_kind in {"agent_error", "agent_timeout", "artifact_validation_failed"},
        "raw_output_artifact": "raw_output.txt" if (artifact_dir / "raw_output.txt").exists() else "",
        "execution_result_artifact": (
            "execution_result.json" if (artifact_dir / "execution_result.json").exists() else ""
        ),
        "validation_status": validation_status,
        "missing_artifacts": missing_artifacts,
        "suggested_actions": actions,
        "provider_diagnostics": provider_diagnostics,
    }


def _provider_failure_diagnostics_summary(artifact_dir: Path) -> dict[str, Any]:
    payload = _read_json(artifact_dir / "provider_diagnostics.json")
    execution_input = _read_json(artifact_dir / "execution_input.json")
    if not isinstance(payload, dict):
        return {}
    diagnostics = payload.get("diagnostics")
    if not isinstance(diagnostics, dict):
        diagnostics = {}
    health = payload.get("health")
    if not isinstance(health, dict):
        health = {}
    attempts = [
        _provider_attempt_failure_summary(item)
        for item in health.get("attempts") or []
        if isinstance(item, dict)
    ]
    summary: dict[str, Any] = {
        "artifact": "provider_diagnostics.json",
        "provider": str(payload.get("provider") or ""),
        "status": str(payload.get("status") or ""),
        "health_status": str(health.get("status") or "unknown"),
        "health_reason": str(health.get("reason") or ""),
        "configured_command_text": str(diagnostics.get("configured_command_text") or ""),
        "fallback_command_texts": [
            str(item)
            for item in diagnostics.get("fallback_command_texts") or []
            if str(item).strip()
        ],
        "startup_probe_endpoint": str(diagnostics.get("startup_probe_endpoint") or ""),
        "prompt_transport": str(
            diagnostics.get("startup_probe_transport")
            or diagnostics.get("prompt_transport")
            or ""
        ),
        "mcp_credentials_owner": str(diagnostics.get("mcp_credentials_owner") or ""),
        "attempts": attempts,
    }
    if isinstance(execution_input, dict):
        summary.update(_command_resolution_summary(execution_input.get("command_resolution")))
        process_command = execution_input.get("process_command")
        if isinstance(process_command, list):
            summary["process_command"] = [
                _redact_failure_diagnostic_text(str(item))
                for item in process_command
            ]
        launch_command = execution_input.get("launch_command")
        if isinstance(launch_command, list):
            summary["launch_command"] = [
                _redact_failure_diagnostic_text(str(item))
                for item in launch_command
            ]
    filtered = {
        key: value
        for key, value in summary.items()
        if _nonempty_diagnostic_value(value)
    }
    return _redact_failure_diagnostics(filtered)


def _provider_attempt_failure_summary(item: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "command",
        "status",
        "reason",
        "executable",
        "path",
        "launch_kind",
        "config_hint",
        "profile_config_path",
        "run_status",
        "run_message",
        "probe_status",
        "probe_message",
    )
    return {
        key: _redact_failure_diagnostics(value)
        for key in keys
        if _nonempty_diagnostic_value(value := item.get(key))
    }


def _redact_failure_diagnostics(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _redact_failure_diagnostics(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_failure_diagnostics(item) for item in value]
    if isinstance(value, str):
        return _redact_failure_diagnostic_text(value)
    return value


def _redact_failure_diagnostic_text(value: str) -> str:
    try:
        from app.services.external_agent_discovery import redact_agent_diagnostic_text

        return redact_agent_diagnostic_text(value)
    except Exception:
        return value


def _nonempty_diagnostic_value(value: Any) -> bool:
    return value is not None and value != "" and value != []


def _agent_run_lifecycle_summary(
    *,
    step_id: str,
    status: str,
    artifact_dir: Path,
    executions: list[dict[str, Any]],
    turn_artifacts: list[str],
    validation: dict[str, Any],
    required_artifacts: list[str],
    source_slice_requests: list[dict[str, Any]],
    injected_source_slices: list[dict[str, Any]],
    failure_recovery: dict[str, Any],
) -> dict[str, Any]:
    stages: list[dict[str, Any]] = [
        {
            "stage": "prepared",
            "status": "ok",
            "artifacts": [
                item for item in (
                    "agent_run.json",
                    "task_bundle.json",
                    "workflow_snapshot.json",
                )
                if (artifact_dir / item).exists()
            ],
        }
    ]
    for index, execution in enumerate(executions):
        turn_id = str(execution.get("turn_id") or f"turn_{index + 1}")
        if not execution.get("turn_id"):
            turn_id = _turn_id_from_artifact_path(turn_artifacts[index] if index < len(turn_artifacts) else "")
        turn_artifact_dir = turn_artifacts[index] if index < len(turn_artifacts) else ""
        stage = {
            "stage": "turn",
            "turn_id": turn_id or f"turn_{index + 1}",
            "status": str(execution.get("status") or ""),
            "execution_status": str(execution.get("status") or ""),
            "exit_code": execution.get("exit_code"),
            "timed_out": bool(execution.get("timed_out", False)),
            "duration_ms": int(execution.get("duration_ms") or 0),
            "artifact_dir": turn_artifact_dir,
            "artifacts": _existing_relative_artifacts(
                artifact_dir,
                [
                    f"{turn_artifact_dir}/provider_diagnostics.json",
                    f"{turn_artifact_dir}/execution_input.json",
                    f"{turn_artifact_dir}/execution_result.json",
                    f"{turn_artifact_dir}/raw_output.txt",
                ],
            ),
        }
        stages.append(stage)
    if source_slice_requests or injected_source_slices:
        stages.append({
            "stage": "source_slice_context",
            "status": "ok" if injected_source_slices else "requested",
            "requested_count": len(source_slice_requests),
            "injected_count": len(injected_source_slices),
            "artifacts": _existing_relative_artifacts(
                artifact_dir,
                ["source_slice_requests.json", "source_slices.json"],
            ),
        })
    stages.append({
        "stage": "artifact_validation",
        "status": str(validation.get("status") or ""),
        "validation_status": str(validation.get("status") or ""),
        "provenance_status": str(validation.get("provenance_status") or ""),
        "accepted_count": len(validation.get("accepted_artifacts") or []),
        "rejected_count": len(validation.get("rejected_artifacts") or []),
        "artifacts": [
            str(item.get("artifact") or "")
            for item in validation.get("accepted_artifact_details") or []
            if isinstance(item, dict) and str(item.get("artifact") or "")
        ],
    })
    if failure_recovery:
        stages.append({
            "stage": "failure_recovery",
            "status": "ready" if failure_recovery.get("retryable") else "recorded",
            "failure_kind": str(failure_recovery.get("failure_kind") or ""),
            "artifact": "failure_recovery.json",
        })
    payload: dict[str, Any] = {
        "step_id": step_id,
        "status": status,
        "turn_count": len(executions),
        "required_artifacts": required_artifacts,
        "accepted_artifacts": [str(item) for item in validation.get("accepted_artifacts") or []],
        "rejected_artifacts": [
            item for item in validation.get("rejected_artifacts") or []
            if isinstance(item, dict)
        ],
        "source_slice_request_count": len(source_slice_requests),
        "injected_source_slice_count": len(injected_source_slices),
        "stages": stages,
    }
    if failure_recovery:
        payload["failure_kind"] = str(failure_recovery.get("failure_kind") or "")
        payload["failure_recovery_artifact"] = "failure_recovery.json"
    return payload


def _workflow_execution_audit_summary(
    *,
    step_results: list[dict[str, Any]],
) -> dict[str, Any]:
    agent_lifecycle_artifacts: list[str] = []
    failure_kinds: list[str] = []
    missing_artifacts: list[str] = []
    for step in step_results:
        if not isinstance(step, dict):
            continue
        artifact_dir = Path(str(step.get("artifact_dir") or ""))
        lifecycle = step.get("lifecycle")
        if isinstance(lifecycle, dict) and artifact_dir:
            lifecycle_path = artifact_dir / "agent_run_lifecycle.json"
            if lifecycle_path.exists():
                agent_lifecycle_artifacts.append(
                    f"agent_runs/{_safe_segment(str(step.get('step_id') or 'step'))}/agent_run_lifecycle.json"
                )
        recovery = step.get("failure_recovery")
        if isinstance(recovery, dict):
            failure_kind = str(recovery.get("failure_kind") or "")
            if failure_kind and failure_kind not in failure_kinds:
                failure_kinds.append(failure_kind)
            for artifact in recovery.get("missing_artifacts") or []:
                text = str(artifact or "")
                if text and text not in missing_artifacts:
                    missing_artifacts.append(text)
    return {
        "step_count": len(step_results),
        "agent_step_count": sum(1 for step in step_results if step.get("type") == "agent_task"),
        "completed_steps": sum(1 for step in step_results if step.get("status") == "completed"),
        "invalid_steps": sum(1 for step in step_results if step.get("status") == "invalid"),
        "error_steps": sum(1 for step in step_results if step.get("status") == "error"),
        "agent_lifecycle_artifacts": agent_lifecycle_artifacts,
        "failure_kinds": failure_kinds,
        "missing_artifacts": missing_artifacts,
    }


def _workflow_rerun_plan(
    *,
    task_run: Any,
    status: str,
    step_results: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    blocked_outputs = [
        {
            "id": str(output.get("id") or ""),
            "status": str(output.get("status") or ""),
            "from": str(output.get("from") or ""),
            "artifact": str(output.get("artifact") or ""),
            "reason": str(output.get("reason") or ""),
        }
        for output in outputs
        if isinstance(output, dict) and output.get("status") in {"missing", "invalid"}
    ]
    steps: list[dict[str, Any]] = []
    for step in step_results:
        if not isinstance(step, dict):
            continue
        step_status = str(step.get("status") or "")
        validation = step.get("validation") if isinstance(step.get("validation"), dict) else {}
        recovery = (
            step.get("failure_recovery")
            if isinstance(step.get("failure_recovery"), dict)
            else {}
        )
        if step_status == "completed" and not recovery:
            continue
        step_type = str(step.get("type") or "")
        failure_kind = str(recovery.get("failure_kind") or "")
        if not failure_kind and validation.get("status") not in {"", "ok", None}:
            failure_kind = "artifact_validation_failed"
        if not failure_kind and step_status:
            failure_kind = step_status
        artifact_dir = Path(str(step.get("artifact_dir") or ""))
        step_id = str(step.get("step_id") or "")
        item: dict[str, Any] = {
            "step_id": step_id,
            "type": step_type,
            "status": step_status,
            "recommended_action": (
                "rerun_agent_step"
                if step_type == "agent_task"
                else "rerun_workflow_from_step"
            ),
            "failure_kind": failure_kind,
            "retryable": bool(recovery.get("retryable", step_status != "completed")),
            "required_artifacts": [str(value) for value in step.get("required_artifacts") or []],
            "missing_artifacts": [
                str(value)
                for value in (
                    recovery.get("missing_artifacts")
                    or validation.get("missing_artifacts")
                    or []
                )
            ],
            "overwrite_risk_artifacts": _rerun_overwrite_risk_artifacts(step_type),
        }
        if artifact_dir:
            if (artifact_dir / "failure_recovery.json").exists():
                item["failure_recovery_artifact"] = (
                    f"agent_runs/{_safe_segment(step_id or 'step')}/failure_recovery.json"
                    if step_type == "agent_task"
                    else "failure_recovery.json"
                )
            if (artifact_dir / "agent_run_lifecycle.json").exists():
                item["lifecycle_artifact"] = (
                    f"agent_runs/{_safe_segment(step_id or 'step')}/agent_run_lifecycle.json"
                    if step_type == "agent_task"
                    else "agent_run_lifecycle.json"
                )
        steps.append(item)
    return {
        "task_run_id": str(getattr(task_run, "task_run_id", "")),
        "workflow_id": str(getattr(task_run, "workflow_id", "")),
        "workspace_id": str(getattr(task_run, "workspace_id", "")),
        "repo_path": str(getattr(task_run, "repo_path", "")),
        "status": "clean" if status == "completed" and not blocked_outputs else "needs_rerun",
        "preserve_inputs": True,
        "reuse_task_bundle": True,
        "created_at": _now(),
        "steps": steps,
        "blocked_outputs": blocked_outputs,
    }


def _rerun_overwrite_risk_artifacts(step_type: str) -> list[str]:
    if step_type == "agent_task":
        return [
            "raw_output.txt",
            "execution_result.json",
            "provider_diagnostics.json",
            "agent_run_lifecycle.json",
        ]
    return []


def _turn_id_from_artifact_path(value: str) -> str:
    text = str(value or "").replace("\\", "/").rstrip("/")
    return text.rsplit("/", 1)[-1] if text else ""


def _existing_relative_artifacts(artifact_dir: Path, relative_paths: list[str]) -> list[str]:
    existing: list[str] = []
    for item in relative_paths:
        rel = str(item or "").replace("\\", "/")
        if rel and (artifact_dir / rel).exists():
            existing.append(rel)
    return existing


def _command_resolution_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    summary: dict[str, Any] = {
        "command_resolution_source": str(value.get("source") or ""),
    }
    if "reason" in value:
        summary["command_resolution_reason"] = str(value.get("reason") or "")
    if "used_fallback" in value:
        summary["command_resolution_used_fallback"] = bool(value.get("used_fallback", False))
    if "launch_kind" in value:
        summary["command_resolution_launch_kind"] = str(value.get("launch_kind") or "")
    return {key: item for key, item in summary.items() if item not in {"", None}}


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _inject_prior_step_context(
    *,
    artifact_dir: Path,
    prior_step_results: list[dict[str, Any]],
) -> None:
    bundle_path = artifact_dir / "task_bundle.json"
    bundle = _read_json(bundle_path)
    if not isinstance(bundle, dict):
        return
    bundle["prior_step_results"] = prior_step_results
    bundle["workflow_step_artifacts"] = _workflow_step_artifact_map(prior_step_results)
    _write_json(bundle_path, bundle)


def _workflow_step_artifact_map(
    prior_step_results: list[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    artifact_map: dict[str, dict[str, str]] = {}
    for result in prior_step_results:
        step_id = str(result.get("step_id") or "").strip()
        artifact_dir = Path(str(result.get("artifact_dir") or ""))
        if not step_id or not artifact_dir:
            continue
        step_artifacts: dict[str, str] = {}
        for artifact in result.get("artifacts") or []:
            artifact_name = str(artifact or "").strip()
            artifact_path = _resolve_artifact_path(artifact_dir, artifact_name)
            if artifact_path is None:
                continue
            key = _artifact_context_key(artifact_name)
            step_artifacts[key] = str(artifact_path)
        if step_artifacts:
            artifact_map[step_id] = step_artifacts
    return artifact_map


def _artifact_context_key(artifact_name: str) -> str:
    path = Path(artifact_name)
    stem = "".join(char if char.isalnum() else "_" for char in path.stem.lower()).strip("_")
    suffix = path.suffix.lower().lstrip(".")
    return f"{stem}_{suffix}" if suffix else stem


def _overall_status(step_results: list[dict[str, Any]]) -> str:
    actionable = [
        item for item in step_results
        if item.get("status") != "skipped"
    ]
    if not actionable:
        return "skipped"
    if all(item.get("status") == "completed" for item in actionable):
        return "completed"
    if any(item.get("status") == "error" for item in actionable):
        return "error"
    return "invalid"


def _diff_parse_payload(input_snapshot: dict[str, Any]) -> dict[str, Any]:
    patch_inputs = _patch_input_payloads(input_snapshot)
    changed_files: list[dict[str, str]] = []
    warnings: list[str] = []
    for item in patch_inputs:
        text = _read_text_from_input_payload(item)
        if not text:
            warnings.append(f"{item.get('input_id') or item.get('filename') or 'patch'}: empty diff text")
            continue
        changed_files.extend(_changed_files_from_unified_diff(text))
    unique_changed = _dedupe_changed_files(changed_files)
    return {
        "kind": "diff_parse",
        "inputs": patch_inputs,
        "changed_files": unique_changed,
        "summary": {
            "changed_files_count": len(unique_changed),
            "paths": [item["path"] for item in unique_changed],
            "warnings": warnings,
        },
    }


def _patch_input_payloads(input_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for input_id, value in input_snapshot.items():
        if not isinstance(value, dict):
            continue
        if value.get("kind") == "file_set":
            for file_item in value.get("files") or []:
                if isinstance(file_item, dict) and _is_patch_like_file(file_item):
                    payloads.append(dict(file_item))
            continue
        if _is_patch_like_file(value):
            payload = dict(value)
            payload.setdefault("input_id", str(input_id))
            payloads.append(payload)
    return payloads


def _is_patch_like_file(payload: dict[str, Any]) -> bool:
    suffix = str(payload.get("suffix") or "").lower()
    filename = str(payload.get("filename") or "").lower()
    return suffix in {".patch", ".diff"} or filename.endswith((".patch", ".diff"))


def _read_text_from_input_payload(payload: dict[str, Any]) -> str:
    for key in ("parsed_text_path", "copied_path", "original_path"):
        path_text = str(payload.get(key) or "")
        if not path_text:
            continue
        try:
            path = Path(path_text)
            if path.exists() and path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return ""


def _changed_files_from_unified_diff(diff_text: str) -> list[dict[str, str]]:
    changed: list[dict[str, str]] = []
    for line in diff_text.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        old_path = _clean_diff_path(parts[-2])
        new_path = _clean_diff_path(parts[-1])
        path = new_path or old_path
        if not path:
            continue
        changed.append({
            "path": path,
            "old_path": old_path or path,
            "status": _diff_file_status(old_path, new_path),
        })
    return changed


def _clean_diff_path(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if text in {"/dev/null", "dev/null"}:
        return ""
    if text.startswith("a/") or text.startswith("b/"):
        return text[2:]
    return text


def _diff_file_status(old_path: str, new_path: str) -> str:
    if old_path and new_path and old_path != new_path:
        return "renamed"
    if old_path and new_path:
        return "modified"
    if new_path:
        return "added"
    return "deleted"


def _dedupe_changed_files(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str]] = set()
    result: list[dict[str, str]] = []
    for item in items:
        key = (
            str(item.get("path") or ""),
            str(item.get("old_path") or ""),
            str(item.get("status") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _coverage_parse_payload(input_snapshot: dict[str, Any]) -> dict[str, Any]:
    coverage_inputs = _coverage_input_payloads(input_snapshot)
    files: list[dict[str, Any]] = []
    uncovered_functions: list[dict[str, Any]] = []
    warnings: list[str] = []
    for item in coverage_inputs:
        text = _read_text_from_input_payload(item)
        if not text:
            warnings.append(f"{item.get('input_id') or item.get('filename') or 'coverage'}: empty coverage text")
            continue
        parsed = _parse_lcov(text)
        files.extend(parsed["files"])
        uncovered_functions.extend(parsed["uncovered_functions"])
    summary = _coverage_summary(files, uncovered_functions, warnings)
    return {
        "kind": "coverage_parse",
        "inputs": coverage_inputs,
        "files": files,
        "uncovered_functions": uncovered_functions,
        "summary": summary,
    }


def _coverage_input_payloads(input_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for input_id, value in input_snapshot.items():
        if not isinstance(value, dict):
            continue
        if value.get("kind") == "file_set":
            for file_item in value.get("files") or []:
                if isinstance(file_item, dict) and _is_coverage_like_file(file_item):
                    payloads.append(dict(file_item))
            continue
        if _is_coverage_like_file(value):
            payload = dict(value)
            payload.setdefault("input_id", str(input_id))
            payloads.append(payload)
    return payloads


def _is_coverage_like_file(payload: dict[str, Any]) -> bool:
    suffix = str(payload.get("suffix") or "").lower()
    filename = str(payload.get("filename") or "").lower()
    return suffix in {".lcov", ".info"} or "coverage" in filename


def _parse_lcov(text: str) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    uncovered: list[dict[str, Any]] = []
    current_file = ""
    function_lines: dict[str, int] = {}
    function_hits: dict[str, int] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("SF:"):
            current_file = line[3:].replace("\\", "/")
            function_lines = {}
            function_hits = {}
            continue
        if line.startswith("FN:"):
            payload = line[3:]
            line_text, _, function_name = payload.partition(",")
            if function_name:
                function_lines[function_name] = _safe_int(line_text)
            continue
        if line.startswith("FNDA:"):
            payload = line[5:]
            hit_text, _, function_name = payload.partition(",")
            if function_name:
                function_hits[function_name] = _safe_int(hit_text)
            continue
        if line == "end_of_record":
            if current_file:
                file_uncovered: list[dict[str, Any]] = []
                for function_name, line_start in function_lines.items():
                    hit_count = function_hits.get(function_name, 0)
                    if hit_count == 0:
                        item = {
                            "file_path": current_file,
                            "function_name": function_name,
                            "line_start": line_start,
                            "hit_count": hit_count,
                        }
                        file_uncovered.append(item)
                        uncovered.append(item)
                files.append({
                    "file_path": current_file,
                    "function_count": len(function_lines),
                    "covered_function_count": sum(
                        1 for function_name in function_lines
                        if function_hits.get(function_name, 0) > 0
                    ),
                    "uncovered_function_count": len(file_uncovered),
                })
            current_file = ""
            function_lines = {}
            function_hits = {}
    return {"files": files, "uncovered_functions": uncovered}


def _coverage_summary(
    files: list[dict[str, Any]],
    uncovered_functions: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    function_count = sum(int(item.get("function_count") or 0) for item in files)
    covered_count = sum(int(item.get("covered_function_count") or 0) for item in files)
    return {
        "files_count": len(files),
        "function_count": function_count,
        "covered_function_count": covered_count,
        "uncovered_function_count": len(uncovered_functions),
        "function_coverage_percent": (
            round(covered_count * 100 / function_count, 2)
            if function_count
            else 0.0
        ),
        "warnings": warnings,
    }


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _resolve_artifact_path(artifact_dir: Path, artifact_name: str) -> Path | None:
    if not artifact_name:
        return None
    candidate = Path(artifact_name)
    if candidate.is_absolute():
        return None
    if any(part in {"", ".", ".."} for part in candidate.parts):
        return None
    try:
        root = artifact_dir.resolve()
        path = (root / candidate).resolve()
    except OSError:
        return None
    if path == root or root not in path.parents:
        return None
    return path


def _infer_output_step(
    steps_by_id: dict[str, dict[str, Any]],
    artifact_name: str,
) -> tuple[str, dict[str, Any]] | None:
    matches: list[tuple[str, dict[str, Any]]] = []
    agent_steps: list[tuple[str, dict[str, Any]]] = []
    for step_id, step_result in steps_by_id.items():
        if step_result.get("type") != "agent_task":
            continue
        agent_steps.append((step_id, step_result))
        artifact_dir = Path(str(step_result.get("artifact_dir") or ""))
        artifact_path = _resolve_artifact_path(artifact_dir, artifact_name)
        if artifact_path is not None and artifact_path.exists() and artifact_path.is_file():
            matches.append((step_id, step_result))
    if len(matches) == 1:
        return matches[0]
    if len(agent_steps) == 1:
        return agent_steps[0]
    return None


def _infer_output_artifact_name(
    *,
    output: dict[str, Any],
    step_result: dict[str, Any],
) -> str:
    output_id = str(output.get("id") or "").strip()
    output_type = str(output.get("type") or "").strip().lower()
    step_id = str(step_result.get("step_id") or "").strip()
    candidate_artifacts = [
        str(item)
        for item in (
            list(step_result.get("artifacts") or [])
            + list(step_result.get("required_artifacts") or [])
        )
        if str(item).strip()
    ]
    exact_candidates = _matching_artifact_candidates(
        output_id=output_id,
        output_type=output_type,
        artifacts=candidate_artifacts,
    )
    if exact_candidates:
        return exact_candidates[0]
    if output_id:
        for ext in _output_extensions(output_type):
            return f"{output_id}{ext}"
    if step_id:
        for ext in _output_extensions(output_type):
            return f"{step_id}{ext}"
    return ""


def _matching_artifact_candidates(
    *,
    output_id: str,
    output_type: str,
    artifacts: list[str],
) -> list[str]:
    compatible = [
        artifact
        for artifact in _dedupe_strings(artifacts)
        if _artifact_extension_matches_output_type(artifact, output_type)
    ]
    if not compatible:
        return []
    if output_id:
        normalized_output = _artifact_match_key(output_id)
        matches = [
            artifact
            for artifact in compatible
            if normalized_output
            and normalized_output in _artifact_match_key(Path(artifact).stem)
        ]
        if matches:
            return matches
        exact_name = [
            artifact
            for artifact in compatible
            if _artifact_match_key(Path(artifact).stem) == normalized_output
        ]
        if exact_name:
            return exact_name
    if len(compatible) == 1:
        return compatible
    return []


def _artifact_extension_matches_output_type(artifact: str, output_type: str) -> bool:
    suffix = Path(artifact).suffix.lower()
    return suffix in _output_extensions(output_type)


def _artifact_match_key(value: str) -> str:
    return "".join(char for char in str(value).lower() if char.isalnum())


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _output_extensions(output_type: str) -> list[str]:
    if output_type in {"markdown", "md", "report"}:
        return [".md"]
    if output_type in {"json", "scope_report", "test_cases"}:
        return [".json"]
    if output_type in {"patch", "diff"}:
        return [".patch", ".diff"]
    if output_type in {"text", "txt", "log"}:
        return [".txt"]
    return [".json"]


def _builtin_step_result(
    step_id: str,
    step_type: str,
    artifact_dir: Path,
    artifact_path: Path,
    count: int,
) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "type": step_type,
        "status": "completed",
        "artifact_dir": str(artifact_dir),
        "artifact": artifact_path.name,
        "artifacts": [artifact_path.name],
        "count": count,
    }


def _evidence_validation_payload(
    *,
    task_run: Any,
    step_id: str,
    prior_step_results: list[dict[str, Any]],
) -> dict[str, Any]:
    accepted = []
    rejected = []
    accepted_details = []
    rejected_details = []
    warnings = []
    for result in prior_step_results:
        validation = result.get("validation")
        if not isinstance(validation, dict):
            continue
        source_step_id = str(result.get("step_id") or "")
        artifact_dir = Path(str(result.get("artifact_dir") or ""))
        accepted_artifacts = [str(item) for item in validation.get("accepted_artifacts") or []]
        rejected_artifacts = [
            item for item in validation.get("rejected_artifacts") or []
            if isinstance(item, dict)
        ]
        accepted.extend(accepted_artifacts)
        rejected.extend(rejected_artifacts)
        warnings.extend(validation.get("warnings") or [])
        for artifact in accepted_artifacts:
            detail = _accepted_artifact_detail(
                artifact_dir=artifact_dir,
                artifact=artifact,
                source_step_id=source_step_id,
            )
            if detail:
                accepted_details.append(detail)
        for item in rejected_artifacts:
            rejected_details.append({
                **item,
                "source_step_id": source_step_id,
            })
    context_bundle = task_run.task_bundle.get("context_bundle") or {}
    payload = {
        "step_id": step_id,
        "status": "completed",
        "task_run_id": task_run.task_run_id,
        "workspace_id": task_run.workspace_id,
        "accepted_artifacts": accepted,
        "rejected_artifacts": rejected,
        "accepted_artifact_details": accepted_details,
        "rejected_artifact_details": rejected_details,
        "warnings": warnings,
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "context_evidence_count": len(context_bundle.get("evidence") or []),
        "semantic_case_count": len(context_bundle.get("semantic_cases") or []),
    }
    return payload


def _accepted_artifact_detail(
    *,
    artifact_dir: Path,
    artifact: str,
    source_step_id: str,
) -> dict[str, Any] | None:
    path = _resolve_artifact_path(artifact_dir, artifact)
    if path is None or not path.exists() or not path.is_file():
        return None
    data = path.read_bytes()
    return {
        "artifact": artifact,
        "source_step_id": source_step_id,
        "path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }


def _render_report_artifacts(
    *,
    artifact_dir: Path,
    step: dict[str, Any],
    workflow_snapshot: dict[str, Any],
    task_run: Any,
    prior_step_results: list[dict[str, Any]],
) -> list[str]:
    step_id = str(step.get("id") or "")
    outputs = [
        output for output in workflow_snapshot.get("outputs") or []
        if isinstance(output, dict)
        and str(output.get("from") or output.get("source") or "") == step_id
    ]
    if not outputs:
        outputs = [{"id": "report", "type": "markdown", "from": step_id}]
    written: list[str] = []
    content = _render_report_content(
        task_run=task_run,
        prior_step_results=prior_step_results,
    )
    for output in outputs:
        output_id = str(output.get("id") or "report").strip() or "report"
        artifact_name = str(output.get("artifact") or output.get("path") or "").strip()
        if not artifact_name:
            output_type = str(output.get("type") or "").lower()
            ext = (
                ".md"
                if output_type in {"markdown", "md", ""}
                else _output_extensions(output_type)[0]
            )
            artifact_name = f"{output_id}{ext}"
        artifact_path = _resolve_artifact_path(artifact_dir, artifact_name)
        if artifact_path is None:
            continue
        if artifact_path.suffix.lower() == ".json":
            _write_json(artifact_path, {
                "task_run_id": task_run.task_run_id,
                "workflow_id": task_run.workflow_id,
                "content": content,
            })
        else:
            artifact_path.write_text(content, encoding="utf-8")
        written.append(artifact_path.name)
    return written


def _render_report_content(
    *,
    task_run: Any,
    prior_step_results: list[dict[str, Any]],
) -> str:
    context_bundle = task_run.task_bundle.get("context_bundle") or {}
    lines = [
        f"# {task_run.workflow_id} report",
        "",
        f"- Task run: `{task_run.task_run_id}`",
        f"- Workspace: `{task_run.workspace_id}`",
        f"- Repo: `{task_run.repo_path}`",
        f"- Query: {context_bundle.get('query') or ''}",
        "",
        "## Workflow Steps",
    ]
    for result in prior_step_results:
        lines.append(
            f"- `{result.get('step_id')}` {result.get('type')}: {result.get('status')}"
        )
    evidence = context_bundle.get("evidence") or []
    semantics = context_bundle.get("semantic_cases") or []
    if evidence:
        lines.extend(["", "## Evidence Memory"])
        for item in evidence[:12]:
            subject = item.get("subject_key") or item.get("path") or ""
            reason = item.get("reason") or item.get("text") or ""
            lines.append(
                f"- {item.get('kind') or 'evidence'} `{subject}`: {reason}"
            )
    validation_payloads = _report_validation_payloads(prior_step_results)
    if validation_payloads:
        lines.extend(["", "## Artifact Validation"])
        for payload in validation_payloads:
            step_id = payload.get("step_id") or "evidence_validate"
            accepted = payload.get("accepted_artifact_details") or []
            rejected = payload.get("rejected_artifact_details") or []
            lines.append(
                f"- `{step_id}` accepted {len(accepted)}, rejected {len(rejected)}"
            )
            for item in accepted[:24]:
                artifact = item.get("artifact") or ""
                source_step_id = item.get("source_step_id") or ""
                sha256 = item.get("sha256") or ""
                size_bytes = item.get("size_bytes")
                lines.append(
                    "- accepted "
                    f"`{artifact}` from `{source_step_id}` "
                    f"sha256 `{sha256}` size {size_bytes}"
                )
            for item in rejected[:24]:
                artifact = item.get("artifact") or item.get("path") or ""
                source_step_id = item.get("source_step_id") or ""
                reason = item.get("reason") or item.get("error") or "rejected"
                lines.append(
                    f"- rejected `{artifact}` from `{source_step_id}`: {reason}"
                )
    source_slice_lines = _report_source_slice_lines(evidence)
    if source_slice_lines:
        lines.extend(["", "## Source Slices"])
        lines.extend(source_slice_lines)
    if semantics:
        lines.extend(["", "## Semantic Cases"])
        for item in semantics[:12]:
            terms = ", ".join(item.get("terms") or [])
            lines.append(
                f"- {item.get('case_id')}: {item.get('scenario') or ''} ({terms})"
            )
    return "\n".join(lines).strip() + "\n"


def _report_validation_payloads(
    prior_step_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for result in prior_step_results:
        if result.get("type") != "evidence_validate":
            continue
        artifact_dir_text = str(result.get("artifact_dir") or "")
        if not artifact_dir_text:
            continue
        artifact_dir = Path(artifact_dir_text)
        candidates = [artifact_dir / "evidence_validation.json"]
        artifact = str(result.get("artifact") or "")
        if artifact:
            candidates.append(artifact_dir / artifact)
        for path in candidates:
            payload = _read_json(path)
            if isinstance(payload, dict):
                payloads.append(payload)
                break
    return payloads


def _report_source_slice_lines(evidence: list[Any]) -> list[str]:
    lines: list[str] = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        subject = item.get("subject_key") or item.get("path") or ""
        slices = item.get("source_slices") or []
        if not isinstance(slices, list):
            continue
        for source_slice in slices:
            if not isinstance(source_slice, dict):
                continue
            file_path = source_slice.get("file_path") or ""
            start_line = source_slice.get("start_line")
            end_line = source_slice.get("end_line")
            sha256 = source_slice.get("sha256") or ""
            reason = source_slice.get("reason") or source_slice.get("symbol") or subject
            if not file_path or start_line is None or end_line is None:
                continue
            lines.append(
                f"- `{file_path}:{start_line}-{end_line}` "
                f"sha256 `{sha256}`: {reason}"
            )
            if len(lines) >= 24:
                return lines
    return lines


def _preview_bytes(data: bytes, *, max_chars: int = 4000) -> str:
    text = data[: max_chars * 4].decode("utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars]
    return text


def _safe_segment(value: str) -> str:
    text = str(value or "").strip()
    if not text or "/" in text or "\\" in text or ".." in text:
        raise KeyError(value)
    return text


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
