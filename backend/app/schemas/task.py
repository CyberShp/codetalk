import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class TaskType(str, Enum):
    FULL_REPO = "full_repo"
    FILE_PATHS = "file_paths"
    MR_DIFF = "mr_diff"


class TaskCreate(BaseModel):
    repository_id: uuid.UUID
    task_type: TaskType
    tools: list[str]
    ai_enabled: bool = False
    target_spec: dict = {}


class TaskResponse(BaseModel):
    id: uuid.UUID
    repository_id: uuid.UUID
    repository_name: str | None = None
    task_type: str
    status: str
    tools: list[str]
    ai_enabled: bool
    progress: int
    error: str | None
    ai_summary: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ToolRunResponse(BaseModel):
    id: uuid.UUID
    tool_name: str
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    result: dict | None
    error: str | None

    model_config = {"from_attributes": True}


class TaskDetailResponse(TaskResponse):
    tool_runs: list[ToolRunResponse] = []
