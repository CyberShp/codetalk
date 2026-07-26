"""Task lifecycle and Run Attempt routes for Workbench V2."""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.services.evidence_memory import EvidenceMemoryStore
from app.services.ai_workbench_links import AIWorkbenchLinkStore
from app.services.test_semantic_library import TestSemanticLibraryStore
from app.services.workbench_task_run import (
    WorkbenchTaskRunPreparer,
    WorkbenchTaskRunStore,
    refresh_run_snapshot_v3,
    resolve_execution_profile,
)
from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore
from app.services.workbench_task_store import WorkbenchTask, WorkbenchTaskStore
from app.services.workbench_task_compile import TaskConfigurationError, compile_task_configuration
from app.services.workflow_dsl import WorkflowStore
from app.services.workflow_presets import (
    active_builtin_workflow_presets,
    reserved_builtin_workflow_ids,
)
from app.services.workflow_version_store import WorkflowVersionStore, workflow_header_status


router = APIRouter(prefix="/api/workbench/tasks", tags=["workbench-v2-tasks"])
_ATTEMPT_LOCK = threading.RLock()
_BUILTIN_WORKFLOW_IDS = reserved_builtin_workflow_ids()
_ACTIVE_BUILTIN_WORKFLOW_IDS = frozenset(
    str(preset["definition"]["id"])
    for preset in active_builtin_workflow_presets()
)


class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=240)
    description: str = ""
    workspace_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    workflow_version_id: str = Field(min_length=1)
    lifecycle_status: str = "draft"
    execution_profile_id: str = ""
    input_values: dict[str, Any] = Field(default_factory=dict)
    execution_overrides: dict[str, Any] = Field(default_factory=dict)
    output_overrides: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class TaskUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    lifecycle_status: str | None = None
    execution_profile_id: str | None = None
    input_values: dict[str, Any] | None = None
    execution_overrides: dict[str, Any] | None = None
    output_overrides: dict[str, Any] | None = None
    tags: list[str] | None = None


class TaskCloneRequest(BaseModel):
    name: str | None = None


class TaskRunCreateRequest(BaseModel):
    parent_task_run_id: str = ""
    execution_profile_id: str = ""


def task_store() -> WorkbenchTaskStore:
    return WorkbenchTaskStore(settings.data_path / "workbench" / "workflows.db")


def version_store() -> WorkflowVersionStore:
    return WorkflowVersionStore(settings.data_path / "workbench" / "workflows.db")


def task_run_store() -> WorkbenchTaskRunStore:
    return WorkbenchTaskRunStore(settings.data_path / "workbench" / "task_runs")


def _require_v2() -> None:
    # Kept as a compatibility seam for callers of the versioned API. The
    # versioned Workbench is now the only runtime and cannot be disabled.
    return None


@router.get("/history/runs")
async def list_legacy_task_runs(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    _require_v2()
    items = [run for run in task_run_store().list(limit=limit) if not run.task_id]
    return {"items": [{**_run_summary(run), "legacy": True} for run in items]}


@router.get("")
async def list_tasks(
    q: str = "",
    lifecycle_status: str = "",
    execution_status: str = "",
    quality_status: str = "",
    workflow_id: str = "",
    workspace_id: str = "",
    updated_from: str = "",
    updated_to: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    _require_v2()
    store = task_store()
    filters = dict(
        q=q,
        lifecycle_status=lifecycle_status,
        workflow_id=workflow_id,
        workspace_id=workspace_id,
        updated_from=updated_from,
        updated_to=updated_to,
        include_archived=lifecycle_status == "archived",
    )
    start = (page - 1) * page_size
    if not execution_status and not quality_status:
        candidates = store.list_tasks(**filters, limit=page_size, offset=start)
        return {
            "items": [_task_payload(task) for task in candidates],
            "total": store.count_tasks(**filters),
            "page": page,
            "page_size": page_size,
        }

    enriched: list[dict[str, Any]] = []
    offset = 0
    while True:
        candidates = store.list_tasks(**filters, limit=500, offset=offset)
        if not candidates:
            break
        enriched.extend(_task_payload(task) for task in candidates)
        offset += len(candidates)
        if len(candidates) < 500:
            break
    if execution_status:
        enriched = [
            item for item in enriched
            if str((item.get("latest_run") or {}).get("execution_status") or "not_started")
            == execution_status
        ]
    if quality_status:
        enriched = [
            item for item in enriched
            if str((item.get("latest_run") or {}).get("quality_status") or "not_checked")
            == quality_status
        ]
    total = len(enriched)
    return {"items": enriched[start:start + page_size], "total": total, "page": page, "page_size": page_size}


@router.post("", status_code=201)
async def create_task(payload: TaskCreateRequest) -> dict[str, Any]:
    _require_v2()
    _require_workflow_available_for_new_task(payload.workflow_id)
    version = _published_version(
        payload.workflow_id,
        payload.workflow_version_id,
        require_current_builtin=True,
    )
    _workspace(payload.workspace_id)
    input_values = _without_workspace_input_values(
        version.compiled_definition or {}, payload.input_values
    )
    if payload.lifecycle_status == "ready":
        _validate_ready_inputs(version.compiled_definition or {}, input_values)
    _effective_configuration_payload(
        version=version,
        execution_overrides=payload.execution_overrides,
        output_overrides=payload.output_overrides,
    )
    _validate_execution_profile(version.compiled_definition or {}, payload.execution_profile_id)
    try:
        task_payload = payload.model_dump()
        task_payload["input_values"] = input_values
        task = task_store().create_task(**task_payload)
    except (sqlite3.IntegrityError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _task_payload(task)


@router.get("/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    _require_v2()
    task = _task(task_id)
    version = _published_version(task.workflow_id, task.workflow_version_id)
    origins = await AIWorkbenchLinkStore().list_links(task_id=task_id)
    return {
        **_task_payload(task),
        "runs": [_run_summary(run) for run in _task_runs(task_id)],
        "workflow_version": {
            "version_id": version.version_id,
            "version_number": version.version_number,
            "compiled_definition": version.compiled_definition,
            "compiled_plan": version.compiled_plan,
        },
        "ai_origins": [
            {
                "conversation_id": item["conversation_id"],
                "message_id": item["message_id"],
                "ai_run_id": item["ai_run_id"],
                "task_run_id": item["task_run_id"],
                "relation_type": item["relation_type"],
                "created_at": item["created_at"],
            }
            for item in origins
        ],
    }


@router.patch("/{task_id}")
async def update_task(task_id: str, payload: TaskUpdateRequest) -> dict[str, Any]:
    _require_v2()
    current = _task(task_id)
    changes = payload.model_dump(exclude_unset=True)
    version = None
    if "input_values" in changes:
        version = _published_version(current.workflow_id, current.workflow_version_id)
        changes["input_values"] = _without_workspace_input_values(
            version.compiled_definition or {}, changes.get("input_values") or {}
        )
    if changes.get("lifecycle_status") == "ready":
        version = version or _published_version(
            current.workflow_id, current.workflow_version_id
        )
        values = changes.get("input_values", current.input_values)
        _validate_ready_inputs(version.compiled_definition or {}, values)
    if "execution_profile_id" in changes:
        version = version or _published_version(
            current.workflow_id, current.workflow_version_id
        )
        _validate_execution_profile(
            version.compiled_definition or {}, changes.get("execution_profile_id") or ""
        )
    if any(key in changes for key in {"execution_overrides", "output_overrides"}):
        version = _published_version(current.workflow_id, current.workflow_version_id)
        _effective_configuration_payload(
            version=version,
            execution_overrides=changes.get("execution_overrides", current.execution_overrides),
            output_overrides=changes.get("output_overrides", current.output_overrides),
        )
    try:
        return _task_payload(task_store().update_task(task_id, **changes))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{task_id}/archive")
async def archive_task(task_id: str) -> dict[str, Any]:
    _require_v2()
    task = _task(task_id)
    if task.last_run_id:
        status = WorkbenchTaskRunEventStore(
            settings.data_path / "workbench" / "task_runs"
        ).current_status(task.last_run_id)
        if status in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="任务正在运行，不能归档；请先取消当前运行")
    return _task_payload(task_store().archive_task(task_id))


@router.post("/{task_id}/clone", status_code=201)
async def clone_task(task_id: str, payload: TaskCloneRequest) -> dict[str, Any]:
    _require_v2()
    source = _task(task_id)
    _require_workflow_available_for_new_task(source.workflow_id)
    _published_version(
        source.workflow_id,
        source.workflow_version_id,
        require_current_builtin=True,
    )
    return _task_payload(task_store().clone_task(task_id, name=payload.name))


@router.get("/{task_id}/runs")
async def list_task_attempts(task_id: str) -> dict[str, Any]:
    _require_v2()
    _task(task_id)
    return {"items": [_run_summary(run) for run in _task_runs(task_id)]}


@router.post("/{task_id}/compile")
async def compile_task(task_id: str) -> dict[str, Any]:
    _require_v2()
    task = _task(task_id)
    version = _published_version(task.workflow_id, task.workflow_version_id)
    _validate_ready_inputs(version.compiled_definition or {}, task.input_values)
    effective = _effective_configuration_payload(
        version=version,
        execution_overrides=task.execution_overrides,
        output_overrides=task.output_overrides,
    )
    return {
        **effective,
        "validation": {"valid": True, "errors": [], "warnings": []},
    }


@router.post("/{task_id}/runs", status_code=201)
async def create_task_attempt(task_id: str, payload: TaskRunCreateRequest) -> dict[str, Any]:
    _require_v2()
    task = _task(task_id)
    if task.lifecycle_status != "ready":
        raise HTTPException(status_code=409, detail="只有就绪任务可以启动运行")
    workspace = _workspace(task.workspace_id)

    with _ATTEMPT_LOCK:
        previous = _task_runs(task_id)
        attempt_number = max((run.attempt_number for run in previous), default=0) + 1
        parent_run_id = str(payload.parent_task_run_id or "")
        parent_run = next(
            (run for run in previous if run.task_run_id == parent_run_id),
            None,
        )
        if parent_run_id and parent_run is None:
            raise HTTPException(status_code=422, detail="父运行不属于当前任务")

        if parent_run is not None:
            effective_definition = dict(
                parent_run.task_bundle.get("effective_compiled_definition")
                or parent_run.workflow_snapshot
            )
            effective_plan = dict(parent_run.task_bundle.get("compiled_plan") or {})
            resolved_inputs = _inputs_from_parent_snapshot(parent_run.input_snapshot)
            repo_path = Path(str(parent_run.repo_path)).expanduser().resolve()
            workflow_version_id = str(parent_run.task_bundle.get("workflow_version_id") or "")
            execution_overrides = dict(parent_run.task_bundle.get("execution_overrides") or {})
            output_overrides = dict(parent_run.task_bundle.get("output_overrides") or {})
            retry_seed_results, retry_failed_node_ids = _retry_seed_results_from_parent(
                parent_run,
                effective_plan,
            )
            frozen_profile_id = str(
                (parent_run.task_bundle.get("execution_profile") or {}).get("id") or ""
            )
            requested_profile_id = str(payload.execution_profile_id or "").strip()
            if requested_profile_id and requested_profile_id != frozen_profile_id:
                raise HTTPException(
                    status_code=422,
                    detail="重试必须沿用父运行的执行档位；请创建新的运行以切换档位",
                )
            execution_profile_id = frozen_profile_id
        else:
            version = _published_version(task.workflow_id, task.workflow_version_id)
            effective = _effective_configuration_payload(
                version=version,
                execution_overrides=task.execution_overrides,
                output_overrides=task.output_overrides,
            )
            effective_definition = effective["compiled_definition"]
            effective_plan = effective["compiled_plan"]
            resolved_inputs = dict(task.input_values)
            repo_path = Path(str(workspace["repo_path"])).expanduser().resolve()
            workflow_version_id = version.version_id
            execution_overrides = task.execution_overrides
            output_overrides = task.output_overrides
            retry_seed_results = {}
            retry_failed_node_ids = []
            execution_profile_id = str(
                payload.execution_profile_id or task.execution_profile_id or ""
            ).strip()
        if not repo_path.is_dir():
            raise HTTPException(status_code=422, detail=f"工作空间源码目录不可用：{repo_path}")
        for definition in effective_definition.get("inputs") or []:
            if _is_workspace_input_definition(definition):
                resolved_inputs[str(definition["id"])] = str(repo_path)
        _validate_ready_inputs(effective_definition, resolved_inputs)
        workflow_store = WorkflowStore(settings.data_path / "workbench" / "task_workflows.db")
        workflow_store.save_workflow(effective_definition)
        try:
            prepared = WorkbenchTaskRunPreparer(
                artifact_root=settings.data_path / "workbench" / "task_runs",
                workflow_store=workflow_store,
                evidence_memory=EvidenceMemoryStore(settings.data_path / "workbench" / "evidence_memory.db"),
                semantic_library=TestSemanticLibraryStore(settings.data_path / "workbench" / "test_semantics.db"),
            ).prepare(
                workflow_id=task.workflow_id,
                workspace_id=task.workspace_id,
                repo_path=str(repo_path),
                inputs=resolved_inputs,
                task_id=task.task_id,
                attempt_number=attempt_number,
                parent_task_run_id=parent_run_id,
                execution_profile_id=execution_profile_id,
            )
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"任务输入不完整或无效：{exc}") from exc
        prepared.task_bundle["workflow_version_id"] = workflow_version_id
        prepared.task_bundle["compiled_plan"] = effective_plan
        prepared.task_bundle["effective_compiled_definition"] = effective_definition
        prepared.task_bundle["execution_overrides"] = execution_overrides
        prepared.task_bundle["output_overrides"] = output_overrides
        if parent_run is not None:
            prepared.task_bundle["retry_source"] = {
                "task_run_id": parent_run.task_run_id,
                "mode": "from_failed_node" if retry_failed_node_ids else "frozen_attempt",
                "failed_node_ids": retry_failed_node_ids,
            }
            prepared.task_bundle["retry_seed_results"] = retry_seed_results
            _seed_quality_retry_from_parent(parent_run=parent_run, prepared=prepared)
        _write_run(prepared)
        refresh_run_snapshot_v3(prepared.artifact_dir)
        task_store().update_task(task_id, last_run_id=prepared.task_run_id)
    return _run_summary(prepared)


def _inputs_from_parent_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Recover immutable inputs using the parent's copied files when available."""
    recovered: dict[str, Any] = {}
    for input_id, value in snapshot.items():
        if not isinstance(value, dict):
            recovered[input_id] = value
            continue
        if value.get("kind") == "file":
            path = str(value.get("copied_path") or value.get("original_path") or "")
            if not path:
                raise HTTPException(status_code=409, detail=f"父运行输入文件不可复用：{input_id}")
            recovered[input_id] = path
            continue
        if value.get("kind") == "file_set":
            paths = [
                str(item.get("copied_path") or item.get("original_path") or "")
                for item in value.get("files") or []
                if isinstance(item, dict)
            ]
            if not paths or any(not item for item in paths):
                raise HTTPException(status_code=409, detail=f"父运行文件集合不可复用：{input_id}")
            recovered[input_id] = paths
            continue
        recovered[input_id] = value
    return recovered


def _retry_seed_results_from_parent(
    parent_run: Any,
    compiled_plan: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    execution_path = Path(str(parent_run.artifact_dir)) / "workflow_execution.json"
    try:
        execution = json.loads(execution_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}, []
    if not isinstance(execution, dict):
        return {}, []
    step_results = [
        item for item in execution.get("step_results") or []
        if isinstance(item, dict) and str(item.get("step_id") or "")
    ]
    success_statuses = {"completed", "completed_empty", "needs_review", "succeeded", "success"}
    failure_statuses = {"error", "failed", "failure", "invalid", "interrupted"}
    failed_nodes = {
        str(item.get("step_id") or "")
        for item in step_results
        if str(item.get("status") or "").lower() in failure_statuses
    }
    failed_nodes.update(
        str(item.get("from") or "")
        for item in execution.get("outputs") or []
        if isinstance(item, dict)
        and str(item.get("status") or "").lower() in {"missing", "invalid", "failed"}
        and str(item.get("from") or "")
    )
    plan_nodes = {
        str(item.get("node_id") or ""): item
        for item in compiled_plan.get("nodes") or []
        if isinstance(item, dict) and str(item.get("node_id") or "")
    }
    revalidate_quality_only = (
        not failed_nodes
        and str(getattr(parent_run, "quality_status", "") or "") == "blocked"
    )
    if not failed_nodes and not revalidate_quality_only:
        return {}, []
    impacted = set(failed_nodes)
    changed = True
    while changed:
        changed = False
        for node_id, node in plan_nodes.items():
            dependencies = {str(item) for item in node.get("depends_on") or [] if str(item)}
            if node_id not in impacted and dependencies & impacted:
                impacted.add(node_id)
                changed = True

    seeds: dict[str, dict[str, Any]] = {}
    for item in step_results:
        step_id = str(item.get("step_id") or "")
        if (
            step_id not in plan_nodes
            or str(item.get("status") or "").lower() not in success_statuses
        ):
            continue
        if revalidate_quality_only:
            # Final acceptance failures do not invalidate a successfully generated
            # deliverable. Reuse the expensive Agent node and rerun deterministic
            # validators/renderers against a child-local copy of its artifacts.
            if str(plan_nodes[step_id].get("type") or "") != "agent_task":
                continue
        elif step_id in impacted:
            continue
        reused = dict(item)
        reused["node_id"] = step_id
        reused["reused_from_task_run_id"] = str(parent_run.task_run_id)
        validated = reused.get("validated_outputs")
        if not isinstance(validated, dict):
            validated = {
                key: reused[key]
                for key in (
                    "artifact",
                    "artifacts",
                    "artifact_dir",
                    "count",
                    "validation",
                    "accepted_artifact_details",
                )
                if reused.get(key) not in (None, "", [], {})
            }
        reused["validated_outputs"] = validated
        seeds[step_id] = reused
    ordered_failures = [
        str(item)
        for item in compiled_plan.get("topological_order") or []
        if str(item) in failed_nodes
    ]
    return seeds, ordered_failures or sorted(failed_nodes)


def _seed_quality_retry_from_parent(*, parent_run: Any, prepared: Any) -> None:
    if str(getattr(parent_run, "quality_status", "") or "") != "blocked":
        return

    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner

    audit = WorkbenchWorkflowRunner(settings.data_path / "workbench" / "task_runs").audit_test_activity_quality(
        task_run=parent_run
    )
    parent_artifact_dir = str(getattr(parent_run, "artifact_dir", "") or "").strip()
    parent_root = (
        Path(parent_artifact_dir)
        if parent_artifact_dir
        else settings.data_path / "workbench" / "task_runs" / str(parent_run.task_run_id)
    )
    try:
        acceptance = json.loads(
            (parent_root / "task_acceptance_audit.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        acceptance = {}
    acceptance_checks = acceptance.get("checks") or [] if isinstance(acceptance, dict) else []
    acceptance_failures = [
        dict(check)
        for check in acceptance_checks
        if isinstance(check, dict)
        and str(check.get("status") or "") not in {"ok", "pass", "passed", "completed"}
    ]
    if acceptance_failures:
        # The staged audit does not include all final artifact executability
        # checks. A blocked final acceptance must force a generator rerun even
        # if its earlier staged audit happened to be green.
        issues = [
            dict(item)
            for item in audit.get("issues") or []
            if isinstance(item, dict)
        ]
        known_artifacts = {Path(str(item.get("artifact") or "")).name for item in issues}
        for failure in acceptance_failures:
            relative_path = str(failure.get("relative_path") or "").strip()
            if not relative_path or Path(relative_path).name in known_artifacts:
                continue
            issues.append({
                "artifact": relative_path,
                "code": str(failure.get("reason") or failure.get("id") or "acceptance_failed"),
                "message": str(failure.get("description") or "最终验收未通过").strip(),
                "acceptance_failure": True,
                "invalid_cases": [
                    dict(item)
                    for item in failure.get("invalid_cases") or []
                    if isinstance(item, dict)
                ][:50],
            })
            known_artifacts.add(Path(relative_path).name)
        audit = {
            **audit,
            "status": "needs_rework",
            "deliverable": False,
            "issue_count": len(issues),
            "issues": issues,
            "acceptance_failures": acceptance_failures,
        }
    child_root = Path(str(prepared.artifact_dir)).resolve()
    (child_root / "test_activity_quality_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    parent_agents = {
        str(item.get("step_id") or ""): item
        for item in getattr(parent_run, "agent_runs", []) or []
        if isinstance(item, dict) and str(item.get("step_id") or "")
    }
    issue_artifacts = {
        Path(str(item.get("artifact") or "")).name
        for item in audit.get("issues") or []
        if isinstance(item, dict) and str(item.get("artifact") or "").strip()
    }
    child_agents = [
        item
        for item in getattr(prepared, "agent_runs", []) or []
        if isinstance(item, dict) and str(item.get("step_id") or "")
    ]
    audit_status = str(audit.get("status") or "")
    copied, copied_support, path_replacements = _copy_parent_agent_artifacts(
        parent_agents=parent_agents,
        child_agents=child_agents,
        # A quality repair may rerun only one downstream artifact, but the
        # immutable stage contract still needs the already-verified support
        # evidence from the parent attempt.  Seed it into the child as a
        # read-only baseline; affected delivery artifacts remain eligible for
        # replacement by the repair stage below.
        include_support=True,
    )
    if audit_status not in {"needs_rework", "invalid"}:
        retry_seed_results = prepared.task_bundle.get("retry_seed_results")
        if isinstance(retry_seed_results, dict):
            prepared.task_bundle["retry_seed_results"] = _replace_path_prefixes(
                retry_seed_results,
                path_replacements,
            )
        prepared.task_bundle["retry_source"] = {
            "task_run_id": str(parent_run.task_run_id),
            "mode": "quality_revalidation",
            "failed_node_ids": [],
        }
        prepared.task_bundle["quality_revalidation_seed"] = {
            "audit_status": audit_status,
            "issue_count": int(audit.get("issue_count") or 0),
            "copied_artifacts": copied,
            "copied_support_files": copied_support,
        }
        return

    affected_agent_ids = [
        str(item.get("step_id") or "")
        for item in child_agents
        if issue_artifacts
        and issue_artifacts.intersection(
            Path(str(artifact)).name for artifact in item.get("required_artifacts") or []
        )
    ]
    if not affected_agent_ids:
        # A quality retry must execute a generator. Old artifacts are copied only
        # as a repair draft; reusing the completed Agent node would skip repair.
        affected_agent_ids = [str(item.get("step_id") or "") for item in child_agents]

    retry_seed_results = prepared.task_bundle.get("retry_seed_results")
    if isinstance(retry_seed_results, dict):
        for step_id in affected_agent_ids:
            retry_seed_results.pop(step_id, None)
    prepared.task_bundle["retry_source"] = {
        "task_run_id": str(parent_run.task_run_id),
        "mode": "quality_repair",
        "failed_node_ids": affected_agent_ids,
    }
    prepared.task_bundle["quality_retry_seed"] = {
        "audit_status": str(audit.get("status") or ""),
        "issue_count": int(audit.get("issue_count") or 0),
        "copied_artifacts": copied,
        "copied_support_files": copied_support,
    }


def _copy_parent_agent_artifacts(
    *,
    parent_agents: dict[str, dict[str, Any]],
    child_agents: list[dict[str, Any]],
    include_support: bool = False,
) -> tuple[list[str], list[str], dict[str, str]]:
    copied: list[str] = []
    copied_support: list[str] = []
    path_replacements: dict[str, str] = {}
    for child_agent in child_agents:
        step_id = str(child_agent.get("step_id") or "")
        parent_agent = parent_agents.get(step_id)
        if not isinstance(parent_agent, dict):
            continue
        source_root = Path(str(parent_agent.get("artifact_dir") or "")).resolve()
        destination_root = Path(str(child_agent.get("artifact_dir") or "")).resolve()
        path_replacements[str(source_root)] = str(destination_root)
        for artifact in child_agent.get("required_artifacts") or []:
            relative = Path(str(artifact or ""))
            if not str(relative) or relative.is_absolute() or ".." in relative.parts:
                continue
            source = (source_root / relative).resolve()
            destination = (destination_root / relative).resolve()
            if source_root not in source.parents or destination_root not in destination.parents:
                continue
            if not source.is_file():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied.append(f"{step_id}:{relative.as_posix()}")
        if include_support and source_root.is_dir():
            for source in sorted(path for path in source_root.rglob("*") if path.is_file()):
                try:
                    relative = source.relative_to(source_root)
                except ValueError:
                    continue
                destination = (destination_root / relative).resolve()
                if destination_root not in destination.parents or destination.exists():
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                copied_support.append(f"{step_id}:{relative.as_posix()}")
    return copied, copied_support, path_replacements


def _replace_path_prefixes(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _replace_path_prefixes(item, replacements)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_path_prefixes(item, replacements) for item in value]
    if isinstance(value, str):
        for source, destination in sorted(
            replacements.items(),
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            if value == source or value.startswith(f"{source}/"):
                return f"{destination}{value[len(source):]}"
    return value


def _task(task_id: str) -> WorkbenchTask:
    try:
        return task_store().get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}") from exc


def _require_workflow_available_for_new_task(workflow_id: str) -> None:
    if (
        workflow_id in _BUILTIN_WORKFLOW_IDS
        and workflow_id not in _ACTIVE_BUILTIN_WORKFLOW_IDS
    ):
        raise HTTPException(
            status_code=409,
            detail="该内置工作流已下线，仅保留历史任务与运行记录；请选择当前发布工作流。",
        )
    if workflow_header_status(
        settings.data_path / "workbench" / "workflows.db",
        workflow_id,
    ) == "archived":
        raise HTTPException(
            status_code=409,
            detail="该自建工作流已归档，仅保留历史任务与运行记录；请恢复工作流或选择其他工作流。",
        )


def _published_version(
    workflow_id: str,
    version_id: str,
    *,
    require_current_builtin: bool = False,
):
    store = version_store()
    try:
        version = store.get_version(version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="工作流版本不存在") from exc
    if version.workflow_id != workflow_id:
        raise HTTPException(status_code=422, detail="工作流版本与工作流不匹配")
    if version.state != "published":
        raise HTTPException(status_code=422, detail="普通任务只能选择已发布工作流版本")
    if require_current_builtin and workflow_id in _BUILTIN_WORKFLOW_IDS:
        try:
            current_version_id = store.get_workflow(workflow_id).published_version_id
        except KeyError as exc:
            raise HTTPException(status_code=409, detail="内置工作流尚未准备好，请刷新后重试") from exc
        if version_id != current_version_id:
            raise HTTPException(
                status_code=409,
                detail="内置工作流版本已更新，请刷新页面并选择最新发布版本",
            )
    return version


def _workspace(workspace_id: str) -> dict[str, str]:
    try:
        with sqlite3.connect(settings.sqlite_db) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT id, name, repo_path FROM workspaces WHERE id = ?", (workspace_id,)
            ).fetchone()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail="工作空间存储暂不可用") from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"工作空间不存在：{workspace_id}")
    return {key: str(row[key] or "") for key in ("id", "name", "repo_path")}


def _validate_ready_inputs(definition: dict[str, Any], values: dict[str, Any]) -> None:
    missing = []
    for item in definition.get("inputs") or []:
        if not isinstance(item, dict) or not item.get("required"):
            continue
        if _is_workspace_input_definition(item):
            continue
        value = values.get(str(item.get("id") or ""))
        if value is None or (isinstance(value, str) and not value.strip()) or value == [] or value == {}:
            missing.append(str(item.get("label") or item.get("id") or "未命名输入"))
    if missing:
        raise HTTPException(status_code=422, detail=f"任务缺少必填输入：{'、'.join(missing)}")


def _validate_execution_profile(definition: dict[str, Any], profile_id: str) -> None:
    selected = str(profile_id or "").strip()
    if not selected:
        return
    try:
        # Keep validation exactly aligned with the run preparer. Older
        # published versions intentionally fall back to compatibility profiles.
        resolve_execution_profile(definition, execution_profile_id=selected)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _is_workspace_input_definition(item: dict[str, Any]) -> bool:
    if str(item.get("resolver") or "") == "workspace":
        return True
    return (
        str(item.get("id") or "") == "repo_path"
        and str(item.get("type") or "") == "directory"
    )


def _without_workspace_input_values(
    definition: dict[str, Any], values: dict[str, Any]
) -> dict[str, Any]:
    reserved_ids = {
        str(item.get("id") or "")
        for item in definition.get("inputs") or []
        if isinstance(item, dict) and _is_workspace_input_definition(item)
    }
    return {key: value for key, value in values.items() if key not in reserved_ids}


def _effective_configuration_payload(*, version: Any, execution_overrides: dict[str, Any], output_overrides: dict[str, Any]) -> dict[str, Any]:
    if not version.compiled_definition or not version.compiled_plan:
        raise HTTPException(status_code=422, detail="工作流发布版本没有可执行编译计划")
    try:
        return compile_task_configuration(
            compiled_definition=version.compiled_definition,
            compiled_plan=version.compiled_plan,
            execution_overrides=execution_overrides,
            output_overrides=output_overrides,
        )
    except TaskConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _task_runs(task_id: str) -> list[Any]:
    return task_run_store().list(task_id=task_id, limit=500)


def _task_payload(task: WorkbenchTask) -> dict[str, Any]:
    payload = asdict(task)
    runs = _task_runs(task.task_id)
    payload["latest_run"] = _run_summary(runs[0]) if runs else None
    try:
        payload["workspace_name"] = _workspace(task.workspace_id)["name"]
    except HTTPException:
        payload["workspace_name"] = "工作空间不可用"
    try:
        payload["workflow_name"] = version_store().get_workflow(task.workflow_id).name
    except KeyError:
        payload["workflow_name"] = "工作流不可用"
    return payload


def _run_summary(run: Any) -> dict[str, Any]:
    return {
        "task_run_id": run.task_run_id,
        "task_id": run.task_id,
        "attempt_number": run.attempt_number,
        "parent_task_run_id": run.parent_task_run_id,
        "workflow_id": run.workflow_id,
        "workspace_id": run.workspace_id,
        "execution_status": run.execution_status,
        "quality_status": run.quality_status,
        "delivery_status": run.delivery_status,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "created_at": run.created_at,
    }


def _write_run(run: Any) -> None:
    artifact_dir = Path(run.artifact_dir)
    path = artifact_dir / "task_run.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(asdict(run), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
    bundle_path = artifact_dir / "task_bundle.json"
    bundle_temporary = bundle_path.with_suffix(".json.tmp")
    bundle_temporary.write_text(
        json.dumps(run.task_bundle, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    bundle_temporary.replace(bundle_path)
