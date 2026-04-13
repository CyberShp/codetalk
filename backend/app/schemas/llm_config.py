import uuid
from datetime import datetime

from pydantic import BaseModel


class LLMConfigCreate(BaseModel):
    provider: str  # openai, anthropic, google, ollama, custom
    model_name: str
    api_key: str | None = None
    base_url: str | None = None
    proxy_mode: str = "system"  # "system" | "direct"
    is_default: bool = False


class LLMConfigResponse(BaseModel):
    id: uuid.UUID
    provider: str
    model_name: str
    has_api_key: bool
    base_url: str | None
    proxy_mode: str
    is_default: bool
    created_at: datetime

    model_config = {"from_attributes": True}
