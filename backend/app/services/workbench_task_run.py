"""Prepare reproducible workbench task runs from workflow definitions."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.agent_run_harness import AgentRunHarness
from app.services.external_agent_discovery import (
    external_agent_provider_spec,
    split_agent_command,
)
from app.services.workbench_input_ingest import ingest_workbench_inputs
from app.services.workflow_dsl import WorkflowStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class PreparedWorkbenchTaskRun:
    task_run_id: str
    workflow_id: str
    workspace_id: str
    repo_path: str
    artifact_dir: str
    workflow_snapshot: dict[str, Any]
    input_snapshot: dict[str, Any]
    task_bundle: dict[str, Any]
    agent_runs: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=_now)


class WorkbenchTaskRunPreparer:
    """Freezes workflow/input state and creates Agent run envelopes."""

    def __init__(self, *, artifact_root: str | Path, workflow_store: WorkflowStore) -> None:
        self.artifact_root = Path(artifact_root)
        self.workflow_store = workflow_store

    def prepare(
        self,
        *,
        workflow_id: str,
        workspace_id: str,
        repo_path: str,
        inputs: dict[str, Any],
        provider_override: str | None = None,
    ) -> PreparedWorkbenchTaskRun:
        workflow_snapshot = self.workflow_store.freeze_workflow_snapshot(workflow_id)
        task_run_id = _new_id("task_run")
        artifact_dir = self.artifact_root / task_run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)

        required_artifacts_by_step = {
            str(step.get("id")): [str(item) for item in step.get("required_artifacts") or []]
            for step in workflow_snapshot.get("steps") or []
            if isinstance(step, dict) and step.get("type") == "agent_task"
        }
        input_snapshot = ingest_workbench_inputs(
            input_definitions=[
                item for item in workflow_snapshot.get("inputs") or []
                if isinstance(item, dict)
            ],
            inputs=dict(inputs or {}),
            artifact_dir=artifact_dir,
        )
        task_bundle = {
            "task_run_id": task_run_id,
            "workflow_id": workflow_id,
            "workspace_id": workspace_id,
            "repo_path": repo_path,
            "inputs": input_snapshot,
            "required_artifacts_by_step": required_artifacts_by_step,
            "created_at": _now(),
        }

        agent_runs: list[dict[str, Any]] = []
        for step in workflow_snapshot.get("steps") or []:
            if not isinstance(step, dict) or step.get("type") != "agent_task":
                continue
            step_id = str(step.get("id") or f"step_{len(agent_runs) + 1}")
            provider = str(provider_override or step.get("provider") or "claude-code")
            spec = external_agent_provider_spec(provider)
            command = split_agent_command(spec.command) if spec and spec.command else [provider]
            step_bundle = {
                **task_bundle,
                "step_id": step_id,
                "goal": step.get("goal") or "",
                "required_artifacts": required_artifacts_by_step.get(step_id, []),
                "mcp_profile": step.get("mcp_profile") or "",
            }
            agent_run = AgentRunHarness(artifact_dir / "agent_runs" / step_id).create_run(
                provider=provider,
                command=command,
                cwd=repo_path,
                workflow_snapshot=workflow_snapshot,
                task_bundle=step_bundle,
                mcp_profile=str(step.get("mcp_profile") or ""),
                run_id=f"{task_run_id}_{step_id}",
            )
            agent_runs.append({
                "step_id": step_id,
                "run_id": agent_run.run_id,
                "provider": provider,
                "artifact_dir": agent_run.artifact_dir,
                "mcp_profile": agent_run.mcp_profile,
                "required_artifacts": required_artifacts_by_step.get(step_id, []),
            })

        result = PreparedWorkbenchTaskRun(
            task_run_id=task_run_id,
            workflow_id=workflow_id,
            workspace_id=workspace_id,
            repo_path=repo_path,
            artifact_dir=str(artifact_dir),
            workflow_snapshot=workflow_snapshot,
            input_snapshot=input_snapshot,
            task_bundle=task_bundle,
            agent_runs=agent_runs,
        )
        _write_json(artifact_dir / "task_run.json", asdict(result))
        _write_json(artifact_dir / "workflow_snapshot.json", workflow_snapshot)
        _write_json(artifact_dir / "input_snapshot.json", input_snapshot)
        _write_json(artifact_dir / "task_bundle.json", task_bundle)
        return result


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
