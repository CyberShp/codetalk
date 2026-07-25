"""Persistent AI investigation thread APIs."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.llm.factory import (
    create_behavior_claim_audit_llm_client,
    create_llm_client_from_active,
)
from app.services.ai_conversations import (
    AI_SCOPE_TYPES,
    AIConversationStore,
    ai_thread_artifact_path,
    ai_thread_delivery_dir,
    build_context_references,
    _is_independent_task_review_request,
    maybe_await,
    run_agent_generation,
    run_generation,
    sanitize_ai_thread_artifact_file,
)
from app.services.ai_thread_artifacts import (
    ArtifactContractError,
    build_ai_thread_delivery_zip,
    read_validated_ai_thread_artifact,
)
from app.services.ai_run_snapshots import build_ai_run_snapshot
from app.services.ai_workbench_links import AIWorkbenchLinkStore
from app.services.agent_runtimes import AgentRuntimeStore
from app.services.agent_run_coordinator import agent_run_coordinator
from app.services.external_agent_discovery import redact_agent_diagnostic_text
from app.services.workflow_presets import (
    active_builtin_workflow_presets,
    reserved_builtin_workflow_ids,
)
from app.services.workflow_version_store import workflow_header_status
from app.services.workflow_version_store import WorkflowVersionStore
from app.services.workbench_task_run import WorkbenchTaskRunStore
from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore
from app.services.workbench_task_store import WorkbenchTaskStore

router = APIRouter(prefix="/api/ai/conversations", tags=["ai-conversations"])
_ACTIVE_BUILTIN_WORKFLOW_IDS = frozenset(
    str(preset["definition"]["id"])
    for preset in active_builtin_workflow_presets()
)
_RESERVED_BUILTIN_WORKFLOW_IDS = reserved_builtin_workflow_ids()
@dataclass
class _OperationLockState:
    lock: asyncio.Lock
    users: int = 0


_TASK_DRAFT_LOCKS: dict[str, _OperationLockState] = {}
_TASK_RUN_THREAD_LOCKS: dict[str, _OperationLockState] = {}


@asynccontextmanager
async def _operation_lock(
    registry: dict[str, _OperationLockState],
    key: str,
) -> AsyncIterator[None]:
    state = registry.get(key)
    if state is None:
        state = _OperationLockState(lock=asyncio.Lock())
        registry[key] = state
    state.users += 1
    try:
        async with state.lock:
            yield
    finally:
        state.users -= 1
        if state.users == 0 and registry.get(key) is state:
            registry.pop(key, None)


class CreateConversationRequest(BaseModel):
    scope_type: str = Field(pattern="^[a-z_]+$")
    scope_id: str = Field(min_length=1, max_length=500)
    workspace_id: str | None = Field(default=None, max_length=500)
    memory_namespace: str | None = Field(default=None, max_length=500)
    runtime_type: str = Field(default="builtin_llm", pattern="^(builtin_llm|agent_runtime)$")
    agent_runtime_id: str | None = Field(default=None, max_length=200)
    title: str = Field(default="AI 调查线程", max_length=200)
    initial_context: dict[str, Any] | None = None


class CreateMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=12000)


class UpdateConversationRequest(BaseModel):
    runtime_type: str = Field(pattern="^(builtin_llm|agent_runtime)$")
    agent_runtime_id: str | None = Field(default=None, max_length=200)


class CreateTaskDraftRequest(BaseModel):
    """User intent only; executable workflow truth is loaded server-side."""

    model_config = ConfigDict(extra="ignore")

    source_message_id: str | None = Field(default=None, max_length=200)
    source_ai_run_id: str | None = Field(default=None, max_length=200)
    workflow_id: str | None = Field(default=None, max_length=200)
    workflow_version_id: str | None = Field(default=None, max_length=200)
    mode: str = Field(default="draft", pattern="^draft$")


def _store() -> AIConversationStore:
    return AIConversationStore()


def _selected_workflow_unavailability(initial_context: Any) -> tuple[int, str] | None:
    if not isinstance(initial_context, dict):
        return None
    workflow_id = str(initial_context.get("selected_workflow_id") or "").strip()
    if (
        workflow_id in _RESERVED_BUILTIN_WORKFLOW_IDS
        and workflow_id not in _ACTIVE_BUILTIN_WORKFLOW_IDS
    ):
        return (
            410,
            "该内置工作流已下线，仅保留历史线程记录；请选择当前发布工作流。",
        )
    if workflow_header_status(
        settings.data_path / "workbench" / "workflows.db",
        workflow_id,
    ) == "archived":
        return (
            409,
            "该自建工作流已归档，仅保留历史线程记录；请恢复工作流或选择其他工作流。",
        )
    return None


def _require_selected_workflow_available(initial_context: Any) -> None:
    unavailable = _selected_workflow_unavailability(initial_context)
    if unavailable:
        raise HTTPException(status_code=unavailable[0], detail=unavailable[1])


def _freeze_selected_workflow_binding(initial_context: Any) -> dict[str, Any] | None:
    if not isinstance(initial_context, dict):
        return initial_context
    context = dict(initial_context)
    workflow_id = str(context.get("selected_workflow_id") or "").strip()
    if not workflow_id:
        return context
    store = WorkflowVersionStore(settings.data_path / "workbench" / "workflows.db")
    try:
        header = store.get_workflow(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="所选工作流不存在或尚未发布") from exc
    version_id = str(
        context.get("selected_workflow_version_id") or header.published_version_id or ""
    ).strip()
    if not version_id:
        raise HTTPException(status_code=409, detail="所选工作流尚无已发布版本")
    try:
        version = store.get_version(version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="所选工作流版本不存在") from exc
    if version.workflow_id != workflow_id:
        raise HTTPException(status_code=422, detail="工作流版本与工作流不匹配")
    if version.state != "published":
        raise HTTPException(status_code=409, detail="AI 线程只能绑定已发布工作流版本")
    outputs = [
        {
            key: item[key]
            for key in ("id", "label", "type", "artifact", "required")
            if key in item
        }
        for item in (version.compiled_definition or {}).get("outputs") or []
        if isinstance(item, dict)
    ]
    context.update(
        {
            "selected_workflow_id": workflow_id,
            "selected_workflow_version_id": version_id,
            "selected_workflow_name": header.name,
            "workflow_binding_snapshot": {
                "recorded": True,
                "status": "recorded",
                "workflow_id": workflow_id,
                "workflow_version_id": version_id,
                "workflow_name": header.name,
                "version_number": version.version_number,
                "mode": "constraint_answer",
                "label": "已固定工作流版本",
                "outputs": outputs,
            },
        }
    )
    return context


def _redact_payload(value: Any) -> Any:
    if isinstance(value, str):
        return redact_agent_diagnostic_text(value)
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact_payload(item) for key, item in value.items()}
    return value


def _require_task_workspace(workspace_id: str) -> dict[str, str]:
    if not workspace_id or workspace_id == "global":
        raise HTTPException(status_code=422, detail="请先为 AI 线程选择工作空间")
    try:
        with sqlite3.connect(settings.sqlite_db) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT id, name, repo_path FROM workspaces WHERE id = ?",
                (workspace_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail="工作空间存储暂不可用") from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"工作空间不存在：{workspace_id}")
    return {key: str(row[key] or "") for key in ("id", "name", "repo_path")}


def _is_workspace_task_input(item: dict[str, Any]) -> bool:
    return str(item.get("resolver") or "") == "workspace" or (
        str(item.get("id") or "") == "repo_path"
        and str(item.get("type") or "") == "directory"
    )


def _task_draft_missing_inputs(definition: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in definition.get("inputs") or []:
        if not isinstance(item, dict) or not item.get("required") or _is_workspace_task_input(item):
            continue
        result.append(
            {
                "id": str(item.get("id") or ""),
                "label": str(item.get("label") or item.get("id") or "未命名输入"),
                "type": str(item.get("type") or "free_text"),
            }
        )
    return result


async def _require_enabled_agent_runtime(runtime_id: str | None) -> dict[str, Any]:
    value = str(runtime_id or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="Agent 执行器不能为空")
    try:
        runtime = await AgentRuntimeStore().get_runtime(value)
    except KeyError:
        raise HTTPException(status_code=404, detail="Agent runtime not found")
    if not runtime.get("enabled", True):
        raise HTTPException(status_code=400, detail="Agent 执行器已停用，请切换到可用执行器后再继续")
    return runtime


def kick_conversation_queue(conversation_id: str) -> None:
    """Idempotently ask the queue to claim and execute its next run."""

    async def _job() -> None:
        store = _store()
        run = await store.claim_next_queued_run(conversation_id)
        if run is None:
            return
        run_id = str(run["id"])
        conversation = await store.get_conversation(conversation_id)
        try:
            unavailable = _selected_workflow_unavailability(
                conversation.get("initial_context")
            )
            if unavailable:
                await store.fail_run(run_id, unavailable[1])
                return
            run_runtime_type = str(run.get("runtime_type") or "unknown")
            runtime_type = (
                run_runtime_type
                if run_runtime_type != "unknown"
                else str(conversation.get("runtime_type") or "builtin_llm")
            )
            if runtime_type == "agent_runtime":
                runtime_id = str(
                    run.get("agent_runtime_id")
                    or conversation.get("agent_runtime_id")
                    or ""
                )
                runtime = await store.get_runtime_execution_snapshot(run_id)
                if not runtime:
                    try:
                        runtime = await AgentRuntimeStore().get_runtime(runtime_id)
                    except Exception as exc:
                        await store.fail_run(run_id, f"Agent 执行器不可用：{exc}")
                        return
                runtime_snapshot = run.get("runtime_snapshot")
                snapshot_provider = (
                    str(runtime_snapshot.get("provider") or "")
                    if isinstance(runtime_snapshot, dict)
                    else ""
                )
                provider = _agent_capacity_provider(
                    snapshot_provider
                    or str(runtime.get("provider") or runtime.get("id") or "agent")
                )

                async def on_capacity_queued(queue_status: dict[str, Any]) -> None:
                    await store.update_run_metrics(run_id, **queue_status)
                    await store.append_event(
                        run_id=run_id,
                        conversation_id=conversation_id,
                        event_type="status",
                        payload={"status": "capacity_queued", **queue_status},
                    )

                async with agent_run_coordinator().slot(
                    provider,
                    on_queued=on_capacity_queued,
                ) as capacity:
                    await store.update_run_metrics(
                        run_id,
                        active_process_count=capacity["active_process_count"],
                        global_queue_position=0,
                        provider_queue_position=0,
                        queued_reason="",
                        provider=provider,
                    )
                    current = await store.get_run(run_id)
                    if current.get("status") != "running":
                        return
                    await run_agent_generation(store=store, run_id=run_id, runtime=runtime)
                return
            try:
                llm = await _builtin_llm_for_run(
                    store=store,
                    run=run,
                    conversation=conversation,
                )
            except Exception as exc:
                await store.fail_run(run_id, f"LLM 不可用：{exc}")
                return
            await run_generation(store=store, run_id=run_id, llm=llm)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await store.fail_run(run_id, f"执行器启动失败：{exc}")
        finally:
            next_run = await store.next_queued_run(conversation_id)
            if next_run:
                kick_conversation_queue(conversation_id)

    asyncio.create_task(_job())


async def _builtin_llm_for_run(
    *,
    store: AIConversationStore,
    run: dict[str, Any],
    conversation: dict[str, Any],
) -> Any:
    """Choose the frozen independent auditor for linked deliverable reviews."""
    messages = await store.list_messages(str(conversation.get("id") or ""))
    input_id = str(run.get("input_message_id") or "")
    user_message = next(
        (
            str(message.get("content") or "")
            for message in messages
            if str(message.get("id") or "") == input_id
            and str(message.get("role") or "") == "user"
        ),
        "",
    )
    if _is_independent_task_review_request(conversation, user_message):
        audit = await maybe_await(create_behavior_claim_audit_llm_client())
        if audit is None:
            raise ValueError("未配置独立质量复核模型，不能用生成模型代替复核")
        return audit[0]
    return await maybe_await(create_llm_client_from_active())


def schedule_conversation_run(run_id: str) -> None:
    """Compatibility wrapper for callers that still hold a run id."""

    async def _resolve_and_kick() -> None:
        try:
            run = await _store().get_run(run_id)
        except KeyError:
            return
        kick_conversation_queue(str(run["conversation_id"]))

    asyncio.create_task(_resolve_and_kick())


def _agent_capacity_provider(value: str) -> str:
    normalized = str(value or "agent").strip().lower()
    for provider in ("codex", "claude", "opencode", "nga"):
        if provider in normalized:
            return provider
    return normalized.removeprefix("agent-runtime:") or "agent"


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(body: CreateConversationRequest) -> dict[str, Any]:
    if body.scope_type not in AI_SCOPE_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported scope_type: {body.scope_type}")
    if body.runtime_type == "agent_runtime":
        await _require_enabled_agent_runtime(body.agent_runtime_id)
    _require_selected_workflow_available(body.initial_context)
    initial_context = _freeze_selected_workflow_binding(body.initial_context)
    return await _store().create_conversation(
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        workspace_id=body.workspace_id,
        memory_namespace=body.memory_namespace,
        runtime_type=body.runtime_type,
        agent_runtime_id=body.agent_runtime_id,
        title=body.title,
        initial_context=initial_context,
    )


@router.get("")
async def list_conversations(
    scope_type: str | None = None,
    scope_id: str | None = None,
    workspace_id: str | None = None,
    memory_namespace: str | None = None,
    status: str | None = None,
    include_internal: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    items = await _store().list_conversations(
        scope_type=scope_type,
        scope_id=scope_id,
        workspace_id=workspace_id,
        memory_namespace=memory_namespace,
        status=status,
        include_internal=include_internal,
        limit=limit,
    )
    return {"items": items}


@router.post(
    "/from-task-run/{task_run_id}",
    status_code=status.HTTP_201_CREATED,
)
async def open_or_create_task_run_conversation(
    task_run_id: str,
    response: Response,
) -> dict[str, Any]:
    async with _operation_lock(_TASK_RUN_THREAD_LOCKS, task_run_id):
        return await _open_or_create_task_run_conversation_locked(task_run_id, response)


async def _open_or_create_task_run_conversation_locked(
    task_run_id: str,
    response: Response,
) -> dict[str, Any]:
    links = AIWorkbenchLinkStore()
    for link in await links.list_links(
        task_run_id=task_run_id,
        relation_type="run_discussed_by_ai",
    ):
        try:
            conversation = await _store().get_conversation(str(link["conversation_id"]))
        except KeyError:
            continue
        response.status_code = status.HTTP_200_OK
        return {"conversation": conversation, "created": False, "link": link}

    try:
        run = WorkbenchTaskRunStore(
            settings.data_path / "workbench" / "task_runs"
        ).load(task_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="运行记录不存在") from exc
    if not run.task_id:
        raise HTTPException(status_code=409, detail="该历史运行没有关联 V2 任务")
    tasks = WorkbenchTaskStore(settings.data_path / "workbench" / "workflows.db")
    try:
        task = tasks.get_task(run.task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="关联任务不存在") from exc
    versions = WorkflowVersionStore(settings.data_path / "workbench" / "workflows.db")
    try:
        header = versions.get_workflow(task.workflow_id)
        version = versions.get_version(task.workflow_version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="关联工作流版本不存在") from exc

    execution_status = str(run.execution_status or "prepared")
    quality_status = str(run.quality_status or "not_checked")
    delivery_status = str(run.delivery_status or "none")
    frozen_workflow_name = str(run.workflow_snapshot.get("name") or task.workflow_id)
    run_events = WorkbenchTaskRunEventStore(
        settings.data_path / "workbench" / "task_runs"
    ).list_before(run.task_run_id, limit=500)
    current_node = ""
    for event in reversed(run_events):
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        node_id = str(payload.get("node_id") or "").strip()
        if node_id:
            current_node = node_id
            break
    context = {
        "task_id": task.task_id,
        "task_run_id": run.task_run_id,
        "attempt_number": run.attempt_number,
        "workflow_id": task.workflow_id,
        "workflow_version_id": task.workflow_version_id,
        "workflow_name": frozen_workflow_name,
        "workflow_version_number": version.version_number,
        "execution_status": execution_status,
        "quality_status": quality_status,
        "delivery_status": delivery_status,
        "artifact_manifest_ref": {
            "kind": "task_run_artifact_manifest",
            "task_run_id": run.task_run_id,
            "href": f"/api/workbench/task-runs/{quote(run.task_run_id, safe='')}/artifacts",
        },
        "failure_summary": (
            "本次运行未完成，请结合公开事件和交付件继续分析。"
            if execution_status in {"failed", "error", "interrupted"}
            else ""
        ),
        "current_node": current_node,
        "parent_task_run_id": run.parent_task_run_id,
        "selected_workflow_id": task.workflow_id,
        "selected_workflow_version_id": task.workflow_version_id,
        "selected_workflow_name": frozen_workflow_name,
        "workflow_binding_snapshot": {
            "recorded": True,
            "status": "recorded",
            "workflow_id": task.workflow_id,
            "workflow_version_id": task.workflow_version_id,
            "workflow_name": frozen_workflow_name,
            "version_number": version.version_number,
            "mode": "task_run_review",
            "label": "关联任务运行",
        },
    }
    conversation = await _store().create_conversation(
        scope_type="workbench_task_run",
        scope_id=run.task_run_id,
        workspace_id=task.workspace_id,
        memory_namespace=f"workspace:{task.workspace_id}",
        title=f"{task.name} · Attempt {run.attempt_number or 1}",
        initial_context=context,
    )
    link = await links.create_link(
        conversation_id=conversation["id"],
        task_id=task.task_id,
        task_run_id=run.task_run_id,
        relation_type="run_discussed_by_ai",
        metadata={
            "workflow_id": task.workflow_id,
            "workflow_version_id": task.workflow_version_id,
            "attempt_number": run.attempt_number,
        },
    )
    return {"conversation": conversation, "created": True, "link": link}


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: str) -> dict[str, Any]:
    try:
        conversation = await _store().get_conversation(conversation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="AI conversation not found")
    conversation["latest_run"] = await _store().latest_run(conversation_id)
    return conversation


@router.patch("/{conversation_id}")
async def update_conversation(conversation_id: str, body: UpdateConversationRequest) -> dict[str, Any]:
    store = _store()
    try:
        if body.runtime_type == "agent_runtime":
            await _require_enabled_agent_runtime(body.agent_runtime_id)
        conversation = await store.update_conversation_runtime(
            conversation_id,
            runtime_type=body.runtime_type,
            agent_runtime_id=body.agent_runtime_id,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="AI conversation not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    conversation["latest_run"] = await store.latest_run(conversation_id)
    return conversation


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_conversation(conversation_id: str) -> Response:
    store = _store()
    try:
        await store.delete_conversation(conversation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="AI conversation not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{conversation_id}/messages")
async def list_messages(conversation_id: str) -> dict[str, Any]:
    try:
        await _store().get_conversation(conversation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="AI conversation not found")
    return {"items": _redact_payload(await _store().list_messages(conversation_id))}


@router.get("/{conversation_id}/runs")
async def list_conversation_runs(
    conversation_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    include_timeline: bool = Query(default=False),
    timeline_limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    try:
        await _store().get_conversation(conversation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="AI conversation not found")
    return {
        "items": _redact_payload(
            await _store().list_runs(
                conversation_id,
                limit=limit,
                include_timeline=include_timeline,
                timeline_limit=timeline_limit,
            )
        )
    }


@router.get("/{conversation_id}/events")
async def list_events(
    conversation_id: str,
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    run_id: str | None = Query(default=None, max_length=200),
    process_only: bool = Query(default=False),
) -> dict[str, Any]:
    store = _store()
    try:
        await store.get_conversation(conversation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="AI conversation not found")
    if run_id:
        try:
            run = await store.get_run(run_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="AI run not found")
        if run["conversation_id"] != conversation_id:
            raise HTTPException(status_code=404, detail="AI run not found")
        events = await store.list_events_for_run(
            conversation_id,
            run_id,
            limit=limit,
            process_only=process_only,
        )
    else:
        events = await store.list_events_after(conversation_id, cursor=cursor, limit=limit)
    return {"items": _redact_payload(events)}


@router.post("/{conversation_id}/messages", status_code=status.HTTP_202_ACCEPTED)
async def create_message(conversation_id: str, body: CreateMessageRequest) -> dict[str, Any]:
    store = _store()
    try:
        conversation = await store.get_conversation(conversation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="AI conversation not found")
    _require_selected_workflow_available(conversation.get("initial_context"))
    runtime = None
    session_mode = "fresh"
    if conversation.get("runtime_type") == "agent_runtime":
        runtime = await _require_enabled_agent_runtime(conversation.get("agent_runtime_id"))
        previous_session = await store.get_agent_runtime_session(
            conversation_id=conversation_id,
            agent_runtime_id=str(conversation.get("agent_runtime_id") or ""),
        )
        session_mode = "resume" if previous_session else "fresh"
    refs = await build_context_references(
        conversation=conversation,
        user_message=body.content,
    )
    run_snapshot = build_ai_run_snapshot(
        conversation=conversation,
        runtime=runtime,
        references=[item.to_dict() for item in refs],
        session_mode=session_mode,
    )
    try:
        result = await store.create_user_message_and_run(
            conversation_id=conversation_id,
            content=body.content,
            references=refs,
            run_snapshot=run_snapshot,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    kick_conversation_queue(conversation_id)
    return _redact_payload(result)


@router.post("/{conversation_id}/task-drafts", status_code=status.HTTP_201_CREATED)
async def create_task_draft(
    conversation_id: str,
    body: CreateTaskDraftRequest,
    response: Response,
) -> dict[str, Any]:
    """Create a V2 Task draft pinned to one published workflow version."""

    async with _operation_lock(_TASK_DRAFT_LOCKS, conversation_id):
        return await _create_task_draft_locked(conversation_id, body, response)


async def _create_task_draft_locked(
    conversation_id: str,
    body: CreateTaskDraftRequest,
    response: Response,
) -> dict[str, Any]:

    store = _store()
    try:
        conversation = await store.get_conversation(conversation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="AI conversation not found")

    source_message = None
    if body.source_message_id:
        try:
            source_message = await store.get_message(body.source_message_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="来源消息不存在")
        if source_message.get("conversation_id") != conversation_id:
            raise HTTPException(status_code=422, detail="来源消息不属于当前线程")

    source_run = None
    if body.source_ai_run_id:
        try:
            source_run = await store.get_run(body.source_ai_run_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="来源 AI 运行不存在")
        if source_run.get("conversation_id") != conversation_id:
            raise HTTPException(status_code=422, detail="来源 AI 运行不属于当前线程")
    if (source_message is None) != (source_run is None):
        raise HTTPException(status_code=422, detail="来源消息与来源 AI 运行必须同时提供")
    if source_message is not None and source_run is not None:
        message_run_id = str(source_message.get("run_id") or "").strip()
        run_message_id = str(source_run.get("input_message_id") or "").strip()
        source_role = str(source_message.get("role") or "").strip()
        if (
            message_run_id != str(source_run.get("id") or "")
            or (
                source_role != "assistant"
                and run_message_id != str(source_message.get("id") or "")
            )
        ):
            raise HTTPException(status_code=422, detail="来源消息与来源 AI 运行不对应")

    initial_context = conversation.get("initial_context")
    context = initial_context if isinstance(initial_context, dict) else {}
    workflow_id = str(body.workflow_id or context.get("selected_workflow_id") or "").strip()
    requested_version_id = str(
        body.workflow_version_id
        or context.get("selected_workflow_version_id")
        or ""
    ).strip()
    if not workflow_id:
        raise HTTPException(status_code=422, detail="请先选择已发布工作流版本")
    _require_selected_workflow_available({"selected_workflow_id": workflow_id})

    versions = WorkflowVersionStore(settings.data_path / "workbench" / "workflows.db")
    try:
        header = versions.get_workflow(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="工作流或版本不存在") from exc
    version_id = requested_version_id or str(header.published_version_id or "")
    if not version_id:
        raise HTTPException(status_code=409, detail="所选工作流尚无已发布版本")
    try:
        version = versions.get_version(version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="工作流或版本不存在") from exc
    if version.workflow_id != workflow_id:
        raise HTTPException(status_code=422, detail="工作流版本与工作流不匹配")
    if version.state != "published":
        raise HTTPException(status_code=409, detail="任务草稿只能绑定已发布工作流版本")
    if (
        workflow_id in _RESERVED_BUILTIN_WORKFLOW_IDS
        and version_id != str(header.published_version_id or "")
    ):
        raise HTTPException(
            status_code=409,
            detail="内置工作流版本已更新，请刷新页面并选择最新发布版本",
        )
    if not version.compiled_definition or not version.compiled_plan:
        raise HTTPException(status_code=409, detail="已发布工作流缺少服务端编译计划")

    workspace_id = str(conversation.get("workspace_id") or "").strip()
    workspace = _require_task_workspace(workspace_id)
    task_store = WorkbenchTaskStore(settings.data_path / "workbench" / "workflows.db")
    link_store = AIWorkbenchLinkStore()
    for existing_link in await link_store.list_links(
        conversation_id=conversation_id,
        relation_type="task_created_from_ai",
    ):
        metadata = (
            existing_link.get("metadata")
            if isinstance(existing_link.get("metadata"), dict)
            else {}
        )
        if (
            str(existing_link.get("message_id") or "") == str(body.source_message_id or "")
            and str(existing_link.get("ai_run_id") or "") == str(body.source_ai_run_id or "")
            and str(metadata.get("workflow_id") or "") == workflow_id
            and str(metadata.get("workflow_version_id") or "") == version_id
        ):
            try:
                existing_task = task_store.get_task(
                    str(existing_link.get("task_id") or "")
                )
            except KeyError:
                continue
            response.status_code = status.HTTP_200_OK
            missing_inputs = _task_draft_missing_inputs(version.compiled_definition)
            return {
                "task": {
                    **asdict(existing_task),
                    "workspace_name": workspace["name"],
                    "workflow_name": header.name,
                },
                "next_required_step": 3 if missing_inputs else 4,
                "missing_inputs": missing_inputs,
            }
    description_parts = []
    if source_message:
        description_parts.append(str(source_message.get("content") or "").strip())
    description_parts.append(f"来源 AI 线程：{conversation_id}")
    task = task_store.create_task(
        name=str(conversation.get("title") or header.name or "AI 任务")[:240],
        description="\n\n".join(part for part in description_parts if part),
        workspace_id=workspace["id"],
        workflow_id=workflow_id,
        workflow_version_id=version_id,
        lifecycle_status="draft",
        tags=["ai-thread"],
    )
    missing_inputs = _task_draft_missing_inputs(version.compiled_definition)
    await link_store.create_link(
        conversation_id=conversation_id,
        message_id=body.source_message_id,
        ai_run_id=body.source_ai_run_id,
        task_id=task.task_id,
        relation_type="task_created_from_ai",
        metadata={
            "workflow_id": workflow_id,
            "workflow_version_id": version_id,
            "workspace_id": workspace_id,
        },
    )
    return {
        "task": {**asdict(task), "workspace_name": workspace["name"], "workflow_name": header.name},
        "next_required_step": 3 if missing_inputs else 4,
        "missing_inputs": missing_inputs,
    }


@router.post(
    "/{conversation_id}/runs/{source_run_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_failed_run(conversation_id: str, source_run_id: str) -> dict[str, Any]:
    store = _store()
    try:
        conversation = await store.get_conversation(conversation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="AI conversation not found")
    _require_selected_workflow_available(conversation.get("initial_context"))
    if conversation.get("runtime_type") == "agent_runtime":
        await _require_enabled_agent_runtime(conversation.get("agent_runtime_id"))
    try:
        result = await store.retry_failed_run(
            conversation_id=conversation_id,
            source_run_id=source_run_id,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="AI run not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    kick_conversation_queue(conversation_id)
    return _redact_payload(result)


@router.get("/{conversation_id}/stream")
async def stream_events(
    conversation_id: str,
    cursor: int = Query(default=0, ge=0),
) -> StreamingResponse:
    store = _store()
    try:
        await store.get_conversation(conversation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="AI conversation not found")

    async def _events():
        current = cursor
        idle_ticks = 0
        while True:
            events = await store.list_events_after(conversation_id, cursor=current)
            for event in events:
                current = max(current, int(event["event_id"]))
                yield f"data: {json.dumps(_redact_payload(event), ensure_ascii=False)}\n\n"
            latest = await store.latest_run(conversation_id)
            if not latest or latest["status"] not in {"queued", "running"}:
                break
            if not events:
                idle_ticks += 1
                if idle_ticks > 120:
                    break
                await asyncio.sleep(0.5)
            else:
                idle_ticks = 0

    return StreamingResponse(_events(), media_type="text/event-stream")


@router.get("/{conversation_id}/runs/{run_id}/artifact")
async def download_run_artifact(conversation_id: str, run_id: str) -> FileResponse:
    store = _store()
    try:
        await store.get_conversation(conversation_id)
        run = await store.get_run(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="AI conversation or run not found")
    if run["conversation_id"] != conversation_id:
        raise HTTPException(status_code=404, detail="AI conversation or run not found")
    path = ai_thread_artifact_path(conversation_id, run_id)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="AI run artifact not found")
    artifact_text = sanitize_ai_thread_artifact_file(path)
    if artifact_text is not None:
        return Response(
            content=artifact_text,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{conversation_id}-{run_id}-assistant-output.md"'
                )
            },
        )
    return FileResponse(
        path,
        media_type="text/markdown; charset=utf-8",
        filename=f"{conversation_id}-{run_id}-assistant-output.md",
    )


async def _require_conversation_run(
    conversation_id: str,
    run_id: str,
) -> dict[str, Any]:
    store = _store()
    try:
        await store.get_conversation(conversation_id)
        run = await store.get_run(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="AI conversation or run not found")
    if run["conversation_id"] != conversation_id:
        raise HTTPException(status_code=404, detail="AI conversation or run not found")
    return run


def _read_delivery_manifest(conversation_id: str, run_id: str) -> dict[str, Any]:
    path = ai_thread_delivery_dir(conversation_id, run_id) / "artifact_manifest.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="AI run artifact manifest not found")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"AI run artifact manifest is invalid: {exc}")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="AI run artifact manifest is invalid")
    return payload


@router.get("/{conversation_id}/runs/{run_id}/artifacts/manifest")
async def get_run_artifact_manifest(conversation_id: str, run_id: str) -> dict[str, Any]:
    await _require_conversation_run(conversation_id, run_id)
    return _redact_payload(_read_delivery_manifest(conversation_id, run_id))


@router.get("/{conversation_id}/runs/{run_id}/artifacts/content/{artifact_path:path}")
async def download_run_artifact_file(
    conversation_id: str,
    run_id: str,
    artifact_path: str,
) -> Response:
    await _require_conversation_run(conversation_id, run_id)
    manifest = _read_delivery_manifest(conversation_id, run_id)
    accepted = {
        str(item.get("relative_path") or ""): item
        for item in manifest.get("artifacts") or []
        if isinstance(item, dict) and item.get("validation_status") == "accepted"
    }
    if manifest.get("status") != "accepted" or artifact_path not in accepted:
        raise HTTPException(status_code=404, detail="AI run artifact not found or not accepted")
    root = ai_thread_delivery_dir(conversation_id, run_id)
    try:
        path, data = read_validated_ai_thread_artifact(root, manifest, artifact_path)
    except ArtifactContractError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    item = accepted[artifact_path]
    return Response(
        content=data,
        media_type=str(item.get("media_type") or "application/octet-stream"),
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(path.name)}"
        },
    )


@router.get("/{conversation_id}/runs/{run_id}/artifacts.zip")
async def download_run_artifacts_zip(conversation_id: str, run_id: str) -> Response:
    await _require_conversation_run(conversation_id, run_id)
    root = ai_thread_delivery_dir(conversation_id, run_id)
    manifest = _read_delivery_manifest(conversation_id, run_id)
    try:
        payload = build_ai_thread_delivery_zip(root, manifest)
    except ArtifactContractError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{run_id}-deliverables.zip"'
        },
    )


@router.post("/{conversation_id}/cancel")
async def cancel_conversation_run(conversation_id: str) -> dict[str, Any]:
    store = _store()
    try:
        await store.get_conversation(conversation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="AI conversation not found")
    run = await store.cancel_run(conversation_id)
    return {"run": run}
