import uuid
from datetime import datetime

from pydantic import BaseModel


class LLMConfigCreate(BaseModel):
    provider: str  # openai, anthropic, google, ollama, custom
    model_name: str
    is_default: bool = False


class LLMConfigResponse(BaseModel):
    id: uuid.UUID
    provider: str
    model_name: str
    is_default: bool
    created_at: datetime

    model_config = {"from_attributes": True}
