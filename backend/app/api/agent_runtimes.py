"""Agent runtime settings APIs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator

from app.services.agent_cli_bridge import probe_agent_runtime
from app.services.agent_runtimes import (
    COMPLETION_MODES,
    OUTPUT_MODES,
    PROMPT_TRANSPORTS,
    SESSION_PERSISTENCE_MODES,
    WORKING_DIR_MODES,
    AgentRuntimeStore,
)

router = APIRouter(prefix="/api/settings/agent-runtimes", tags=["agent-runtimes"])


class AgentRuntimeBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    command: str = Field(min_length=1, max_length=500)
    args: list[str] = Field(default_factory=list)
    prompt_transport: str = "stdin"
    output_mode: str = "plain"
    working_dir_mode: str = "project"
    fixed_working_dir: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    health_command: str = ""
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    completion_mode: str = "process_exit"
    idle_complete_seconds: int = Field(default=5, ge=1, le=300)
    sentinel_text: str = ""
    session_persistence: str = "none"
    resume_args: list[str] = Field(default_factory=list)
    enabled: bool = True

    @field_validator("prompt_transport")
    @classmethod
    def _valid_transport(cls, value: str) -> str:
        if value not in PROMPT_TRANSPORTS:
            raise ValueError(f"unsupported prompt_transport: {value}")
        return value

    @field_validator("output_mode")
    @classmethod
    def _valid_output_mode(cls, value: str) -> str:
        if value not in OUTPUT_MODES:
            raise ValueError(f"unsupported output_mode: {value}")
        return value

    @field_validator("working_dir_mode")
    @classmethod
    def _valid_working_dir_mode(cls, value: str) -> str:
        if value not in WORKING_DIR_MODES:
            raise ValueError(f"unsupported working_dir_mode: {value}")
        return value

    @field_validator("completion_mode")
    @classmethod
    def _valid_completion_mode(cls, value: str) -> str:
        if value not in COMPLETION_MODES:
            raise ValueError(f"unsupported completion_mode: {value}")
        return value

    @field_validator("session_persistence")
    @classmethod
    def _valid_session_persistence(cls, value: str) -> str:
        if value not in SESSION_PERSISTENCE_MODES:
            raise ValueError(f"unsupported session_persistence: {value}")
        return value


class AgentRuntimeCreate(AgentRuntimeBase):
    pass


class AgentRuntimeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    command: str | None = Field(default=None, min_length=1, max_length=500)
    args: list[str] | None = None
    prompt_transport: str | None = None
    output_mode: str | None = None
    working_dir_mode: str | None = None
    fixed_working_dir: str | None = None
    env: dict[str, str] | None = None
    health_command: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=3600)
    completion_mode: str | None = None
    idle_complete_seconds: int | None = Field(default=None, ge=1, le=300)
    sentinel_text: str | None = None
    session_persistence: str | None = None
    resume_args: list[str] | None = None
    enabled: bool | None = None


class AgentRuntimeResponse(AgentRuntimeBase):
    id: str
    created_at: str
    updated_at: str


def _redact_runtime_response(runtime: dict[str, Any]) -> dict[str, Any]:
    body = dict(runtime)
    body["env"] = {str(key): "<redacted>" for key in (runtime.get("env") or {})}
    return body


def _store() -> AgentRuntimeStore:
    return AgentRuntimeStore()


@router.get("")
async def list_agent_runtimes(enabled: bool | None = Query(default=None)) -> dict[str, Any]:
    return {"items": [_redact_runtime_response(item) for item in await _store().list_runtimes(enabled=enabled)]}


@router.post("", response_model=AgentRuntimeResponse, status_code=status.HTTP_201_CREATED)
async def create_agent_runtime(body: AgentRuntimeCreate) -> dict[str, Any]:
    try:
        return _redact_runtime_response(await _store().create_runtime(body.model_dump()))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/{runtime_id}", response_model=AgentRuntimeResponse)
async def get_agent_runtime(runtime_id: str) -> dict[str, Any]:
    try:
        return _redact_runtime_response(await _store().get_runtime(runtime_id))
    except KeyError:
        raise HTTPException(status_code=404, detail="Agent runtime not found")


@router.put("/{runtime_id}", response_model=AgentRuntimeResponse)
async def update_agent_runtime(runtime_id: str, body: AgentRuntimeUpdate) -> dict[str, Any]:
    try:
        return _redact_runtime_response(
            await _store().update_runtime(runtime_id, body.model_dump(exclude_none=True))
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Agent runtime not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.delete("/{runtime_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_agent_runtime(runtime_id: str):
    try:
        await _store().delete_runtime(runtime_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Agent runtime not found")


@router.post("/{runtime_id}/probe")
async def probe_runtime(runtime_id: str) -> dict[str, Any]:
    try:
        runtime = await _store().get_runtime(runtime_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Agent runtime not found")
    return await probe_agent_runtime(runtime)
