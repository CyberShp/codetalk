"""Execute prepared Agent Workbench workflow task runs."""

from __future__ import annotations

import json
import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.agent_run_harness import (
    AgentRunHarness,
    ArtifactValidationHarness,
)
from app.services.workbench_task_run import WorkbenchTaskRunStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class WorkbenchWorkflowExecutionResult:
    task_run_id: str
    status: str
    started_at: str
    completed_at: str
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
                step_results.append({
                    "step_id": step_id,
                    "type": step_type,
                    "status": "skipped",
                    "reason": "step type is not executable by Agent Run Harness",
                })
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

        execution = AgentRunHarness(artifact_dir).execute_run(
            run_id,
            timeout_sec=timeout_sec,
        )
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
        return {
            "step_id": step_id,
            "type": "agent_task",
            "status": status,
            "provider": agent_run.get("provider") or (run_payload or {}).get("provider") or "",
            "artifact_dir": str(artifact_dir),
            "execution": asdict(execution),
            "validation": asdict(validation),
            "required_artifacts": required_artifacts,
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
            if not artifact_name:
                item["reason"] = "output artifact is not declared"
                outputs.append(item)
                continue
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


def _validate_step_artifacts(
    artifact_dir: Path,
    required_artifacts: list[str],
):
    validator = ArtifactValidationHarness(artifact_dir)
    required = {str(item) for item in required_artifacts}
    if {"mr_snapshot.json", "diff.patch", "changed_files.json"}.issubset(required):
        return validator.validate_mr_artifacts(required_artifacts=required_artifacts)
    return validator.validate_required_artifacts(required_artifacts=required_artifacts)


def _overall_status(step_results: list[dict[str, Any]]) -> str:
    executed = [item for item in step_results if item.get("type") == "agent_task"]
    if not executed:
        return "skipped"
    if all(item.get("status") == "completed" for item in executed):
        return "completed"
    if any(item.get("status") == "error" for item in executed):
        return "error"
    return "invalid"


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
