"""Persistent AI investigation thread APIs."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from app.llm.factory import create_llm_client_from_active
from app.services.ai_conversations import (
    AI_SCOPE_TYPES,
    AIConversationStore,
    ai_thread_artifact_path,
    build_context_references,
    maybe_await,
    run_agent_generation,
    run_generation,
)
from app.services.agent_runtimes import AgentRuntimeStore
from app.services.external_agent_discovery import redact_agent_diagnostic_text

router = APIRouter(prefix="/api/ai/conversations", tags=["ai-conversations"])


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


def _store() -> AIConversationStore:
    return AIConversationStore()


def _redact_payload(value: Any) -> Any:
    if isinstance(value, str):
        return redact_agent_diagnostic_text(value)
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact_payload(item) for key, item in value.items()}
    return value


def schedule_conversation_run(run_id: str) -> None:
    async def _job() -> None:
        store = _store()
        run = await store.get_run(run_id)
        conversation = await store.get_conversation(run["conversation_id"])
        if conversation.get("runtime_type") == "agent_runtime":
            runtime_id = str(conversation.get("agent_runtime_id") or "")
            try:
                runtime = await AgentRuntimeStore().get_runtime(runtime_id)
            except Exception as exc:
                await store.fail_run(run_id, f"Agent 执行器不可用：{exc}")
                return
            if not runtime.get("enabled", True):
                await store.fail_run(run_id, "Agent 执行器已停用")
                return
            await run_agent_generation(store=store, run_id=run_id, runtime=runtime)
            return
        try:
            llm = await maybe_await(create_llm_client_from_active())
        except Exception as exc:
            await store.fail_run(run_id, f"LLM 不可用：{exc}")
            return
        await run_generation(store=store, run_id=run_id, llm=llm)

    asyncio.create_task(_job())


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(body: CreateConversationRequest) -> dict[str, Any]:
    if body.scope_type not in AI_SCOPE_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported scope_type: {body.scope_type}")
    return await _store().create_conversation(
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        workspace_id=body.workspace_id,
        memory_namespace=body.memory_namespace,
        runtime_type=body.runtime_type,
        agent_runtime_id=body.agent_runtime_id,
        title=body.title,
        initial_context=body.initial_context,
    )


@router.get("")
async def list_conversations(
    scope_type: str | None = None,
    scope_id: str | None = None,
    workspace_id: str | None = None,
    memory_namespace: str | None = None,
    status: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    items = await _store().list_conversations(
        scope_type=scope_type,
        scope_id=scope_id,
        workspace_id=workspace_id,
        memory_namespace=memory_namespace,
        status=status,
        limit=limit,
    )
    return {"items": items}


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


@router.post("/{conversation_id}/messages", status_code=status.HTTP_202_ACCEPTED)
async def create_message(conversation_id: str, body: CreateMessageRequest) -> dict[str, Any]:
    store = _store()
    try:
        conversation = await store.get_conversation(conversation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="AI conversation not found")
    latest = await store.latest_run(conversation_id)
    if latest and latest["status"] in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="当前线程仍在生成中")
    refs = await build_context_references(
        conversation=conversation,
        user_message=body.content,
    )
    result = await store.create_user_message_and_run(
        conversation_id=conversation_id,
        content=body.content,
        references=refs,
    )
    schedule_conversation_run(result["run"]["id"])
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
    return FileResponse(
        path,
        media_type="text/markdown; charset=utf-8",
        filename=f"{conversation_id}-{run_id}-assistant-output.md",
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
