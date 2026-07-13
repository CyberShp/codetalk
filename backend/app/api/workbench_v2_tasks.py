"""Task lifecycle and Run Attempt routes for Workbench V2."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.services.evidence_memory import EvidenceMemoryStore
from app.services.test_semantic_library import TestSemanticLibraryStore
from app.services.workbench_task_run import WorkbenchTaskRunPreparer, WorkbenchTaskRunStore
from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore
from app.services.workbench_task_store import WorkbenchTask, WorkbenchTaskStore
from app.services.workflow_dsl import WorkflowStore
from app.services.workflow_version_store import WorkflowVersionStore


router = APIRouter(prefix="/api/workbench/tasks", tags=["workbench-v2-tasks"])
_ATTEMPT_LOCK = threading.RLock()


class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=240)
    description: str = ""
    workspace_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    workflow_version_id: str = Field(min_length=1)
    lifecycle_status: str = "draft"
    input_values: dict[str, Any] = Field(default_factory=dict)
    execution_overrides: dict[str, Any] = Field(default_factory=dict)
    output_overrides: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class TaskUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    lifecycle_status: str | None = None
    input_values: dict[str, Any] | None = None
    execution_overrides: dict[str, Any] | None = None
    output_overrides: dict[str, Any] | None = None
    tags: list[str] | None = None


class TaskCloneRequest(BaseModel):
    name: str | None = None


class TaskRunCreateRequest(BaseModel):
    parent_task_run_id: str = ""


def task_store() -> WorkbenchTaskStore:
    return WorkbenchTaskStore(settings.data_path / "workbench" / "workflows.db")


def version_store() -> WorkflowVersionStore:
    return WorkflowVersionStore(settings.data_path / "workbench" / "workflows.db")


def task_run_store() -> WorkbenchTaskRunStore:
    return WorkbenchTaskRunStore(settings.data_path / "workbench" / "task_runs")


def _require_v2() -> None:
    if not settings.workbench_v2_enabled:
        raise HTTPException(status_code=404, detail="Workbench V2 is not enabled")


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
    page_size: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    _require_v2()
    candidates = task_store().list_tasks(
        q=q,
        lifecycle_status=lifecycle_status,
        workflow_id=workflow_id,
        workspace_id=workspace_id,
        updated_from=updated_from,
        updated_to=updated_to,
        include_archived=lifecycle_status == "archived",
        limit=500,
    )
    enriched = [_task_payload(task) for task in candidates]
    if execution_status:
        enriched = [
            item for item in enriched
            if str((item.get("latest_run") or {}).get("execution_status") or "not_started")
            == execution_status
        ]
    if quality_status:
        enriched = [
            item for item in enriched
            if str((item.get("latest_run") or {}).get("quality_status") or "not_evaluated")
            == quality_status
        ]
    total = len(enriched)
    start = (page - 1) * page_size
    return {"items": enriched[start:start + page_size], "total": total, "page": page, "page_size": page_size}


@router.post("", status_code=201)
async def create_task(payload: TaskCreateRequest) -> dict[str, Any]:
    _require_v2()
    version = _published_version(payload.workflow_id, payload.workflow_version_id)
    _workspace(payload.workspace_id)
    if payload.lifecycle_status == "ready":
        _validate_ready_inputs(version.compiled_definition or {}, payload.input_values)
    try:
        task = task_store().create_task(**payload.model_dump())
    except (sqlite3.IntegrityError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _task_payload(task)


@router.get("/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    _require_v2()
    task = _task(task_id)
    return {**_task_payload(task), "runs": [_run_summary(run) for run in _task_runs(task_id)]}


@router.patch("/{task_id}")
async def update_task(task_id: str, payload: TaskUpdateRequest) -> dict[str, Any]:
    _require_v2()
    current = _task(task_id)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("lifecycle_status") == "ready":
        version = _published_version(current.workflow_id, current.workflow_version_id)
        values = changes.get("input_values", current.input_values)
        _validate_ready_inputs(version.compiled_definition or {}, values)
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
    _task(task_id)
    return _task_payload(task_store().clone_task(task_id, name=payload.name))


@router.get("/{task_id}/runs")
async def list_task_attempts(task_id: str) -> dict[str, Any]:
    _require_v2()
    _task(task_id)
    return {"items": [_run_summary(run) for run in _task_runs(task_id)]}


@router.post("/{task_id}/runs", status_code=201)
async def create_task_attempt(task_id: str, payload: TaskRunCreateRequest) -> dict[str, Any]:
    _require_v2()
    task = _task(task_id)
    if task.lifecycle_status != "ready":
        raise HTTPException(status_code=409, detail="只有就绪任务可以启动运行")
    version = _published_version(task.workflow_id, task.workflow_version_id)
    workspace = _workspace(task.workspace_id)
    repo_path = Path(str(workspace["repo_path"])).expanduser().resolve()
    if not repo_path.is_dir():
        raise HTTPException(status_code=422, detail=f"工作空间源码目录不可用：{repo_path}")
    if not version.compiled_definition or not version.compiled_plan:
        raise HTTPException(status_code=422, detail="工作流发布版本没有可执行编译计划")

    with _ATTEMPT_LOCK:
        previous = _task_runs(task_id)
        attempt_number = max((run.attempt_number for run in previous), default=0) + 1
        parent_run_id = str(payload.parent_task_run_id or "")
        if parent_run_id and not any(run.task_run_id == parent_run_id for run in previous):
            raise HTTPException(status_code=422, detail="父运行不属于当前任务")
        resolved_inputs = dict(task.input_values)
        for definition in version.compiled_definition.get("inputs") or []:
            if str(definition.get("resolver") or "") == "workspace":
                resolved_inputs[str(definition["id"])] = str(repo_path)
        _validate_ready_inputs(version.compiled_definition, resolved_inputs)
        workflow_store = WorkflowStore(settings.data_path / "workbench" / "task_workflows.db")
        workflow_store.save_workflow(version.compiled_definition)
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
            )
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"任务输入不完整或无效：{exc}") from exc
        prepared.task_bundle["workflow_version_id"] = version.version_id
        prepared.task_bundle["compiled_plan"] = version.compiled_plan
        prepared.task_bundle["execution_overrides"] = task.execution_overrides
        prepared.task_bundle["output_overrides"] = task.output_overrides
        _write_run(prepared)
        task_store().update_task(task_id, last_run_id=prepared.task_run_id)
    return _run_summary(prepared)


def _task(task_id: str) -> WorkbenchTask:
    try:
        return task_store().get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}") from exc


def _published_version(workflow_id: str, version_id: str):
    try:
        version = version_store().get_version(version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="工作流版本不存在") from exc
    if version.workflow_id != workflow_id:
        raise HTTPException(status_code=422, detail="工作流版本与工作流不匹配")
    if version.state != "published":
        raise HTTPException(status_code=422, detail="普通任务只能选择已发布工作流版本")
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
        if str(item.get("resolver") or "") == "workspace":
            continue
        value = values.get(str(item.get("id") or ""))
        if value is None or (isinstance(value, str) and not value.strip()) or value == [] or value == {}:
            missing.append(str(item.get("label") or item.get("id") or "未命名输入"))
    if missing:
        raise HTTPException(status_code=422, detail=f"任务缺少必填输入：{'、'.join(missing)}")


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
    path = Path(run.artifact_dir) / "task_run.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(asdict(run), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
