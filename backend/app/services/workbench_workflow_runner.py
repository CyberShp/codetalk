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

        if step_type in {"diff_parse", "file_ingest", "coverage_parse", "artifact_export"}:
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
    required_artifacts = [
        str(item)
        for item in step_result.get("required_artifacts") or []
        if str(item).strip()
    ]
    for ext in _output_extensions(output_type):
        candidate = f"{output_id}{ext}"
        if candidate in required_artifacts:
            return candidate
    if output_id:
        for ext in _output_extensions(output_type):
            return f"{output_id}{ext}"
    if step_id:
        for ext in _output_extensions(output_type):
            return f"{step_id}{ext}"
    return ""


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
    validations = [
        item.get("validation")
        for item in prior_step_results
        if isinstance(item.get("validation"), dict)
    ]
    accepted = []
    rejected = []
    warnings = []
    for validation in validations:
        accepted.extend(validation.get("accepted_artifacts") or [])
        rejected.extend(validation.get("rejected_artifacts") or [])
        warnings.extend(validation.get("warnings") or [])
    context_bundle = task_run.task_bundle.get("context_bundle") or {}
    payload = {
        "step_id": step_id,
        "status": "completed",
        "task_run_id": task_run.task_run_id,
        "workspace_id": task_run.workspace_id,
        "accepted_artifacts": accepted,
        "rejected_artifacts": rejected,
        "warnings": warnings,
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "context_evidence_count": len(context_bundle.get("evidence") or []),
        "semantic_case_count": len(context_bundle.get("semantic_cases") or []),
    }
    return payload


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
    if semantics:
        lines.extend(["", "## Semantic Cases"])
        for item in semantics[:12]:
            terms = ", ".join(item.get("terms") or [])
            lines.append(
                f"- {item.get('case_id')}: {item.get('scenario') or ''} ({terms})"
            )
    return "\n".join(lines).strip() + "\n"


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
