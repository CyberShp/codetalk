from datetime import datetime

from pydantic import BaseModel


class ConfigField(BaseModel):
    name: str
    label: str
    field_type: str  # "url", "secret", "text", "select"
    options: list[str] | None = None
    placeholder: str | None = None


class ConfigDomain(BaseModel):
    domain: str
    label: str
    fields: list[ConfigField]
    env_map: dict[str, str]


class ComponentContract(BaseModel):
    component: str
    label: str
    domains: list[ConfigDomain]


class ComponentConfigUpdate(BaseModel):
    config: dict[str, str]


class ComponentConfigResponse(BaseModel):
    component: str
    domain: str
    config: dict[str, str]
    applied_at: datetime | None
    updated_at: datetime


class ComponentHealth(BaseModel):
    component: str
    healthy: bool
    container_status: str | None = None
    version: str | None = None


class ComponentStatus(BaseModel):
    component: str
    label: str
    health: ComponentHealth
    domains: list[ComponentConfigResponse]


class ApplyResult(BaseModel):
    success: bool
    message: str
    override_preview: dict[str, str] | None = None


class RestartResult(BaseModel):
    success: bool
    message: str
