import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class SourceType(str, Enum):
    GIT_URL = "git_url"
    LOCAL_PATH = "local_path"
    ZIP_UPLOAD = "zip_upload"


class RepositoryCreate(BaseModel):
    name: str
    source_type: SourceType
    source_uri: str
    branch: str = "main"


class RepositoryResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    source_type: str
    source_uri: str
    local_path: str | None
    branch: str
    last_indexed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
