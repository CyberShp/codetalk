"""Shared deepwiki payload builder for HTTP and WebSocket chat endpoints."""
from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from app.config import settings
from app.utils.repo_paths import to_tool_repo_path

# Defer SQLAlchemy-backed model imports to TYPE_CHECKING so that consumers
# which only need the runtime constants (DEFAULT_EXCLUDED_DIRS, ChatMessage)
# don't transitively pull in sqlalchemy. This file is also imported by
# wiki_orchestrator.py, which is in turn imported by deepwiki_pages.py — and
# the native-mode backend venv has no sqlalchemy installed, so eager imports
# would crash `app.main` at startup.
if TYPE_CHECKING:
    from app.models.llm_config import LLMConfig
    from app.models.repository import Repository


class ChatMessage(BaseModel):
    role: str
    content: str


DEFAULT_EXCLUDED_DIRS: list[str] = [
    "node_modules", ".git", "dist", "build", "__pycache__",
    ".next", "vendor", "coverage", ".nyc_output",
    ".venv", "venv", ".tox", "egg-info",
]


def build_deepwiki_payload(
    repo: Repository,
    messages: list[ChatMessage],
    llm_config: LLMConfig | None,
    *,
    file_path: str | None = None,
    included_files: list[str] | None = None,
    excluded_dirs: list[str] | None = None,
    deep_research: bool = False,
) -> dict:
    """Build deepwiki request payload."""
    repo_path = to_tool_repo_path(
        repo.local_path,
        host_base_path=settings.repos_base_path,
        tool_base_path=settings.tool_repos_base_path,
    )

    payload: dict = {
        "repo_url": repo_path,
        "type": "local",
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "language": "zh",
    }

    if file_path:
        payload["filePath"] = file_path
    if included_files:
        payload["included_files"] = "\n".join(included_files)

    # Smart file filtering — merge defaults with caller-supplied dirs
    effective_excluded = list(DEFAULT_EXCLUDED_DIRS)
    if excluded_dirs:
        effective_excluded.extend(d for d in excluded_dirs if d not in effective_excluded)
    payload["excluded_dirs"] = "\n".join(effective_excluded)

    # Deep research tag injection on the last user message
    if deep_research and payload["messages"]:
        last = payload["messages"][-1]
        if last["role"] == "user":
            last["content"] = f"[DEEP RESEARCH] {last['content']}"

    if llm_config:
        provider = llm_config.provider
        if provider == "custom":
            provider = "openai"
        payload["provider"] = provider
        payload["model"] = llm_config.model_name
    else:
        payload["provider"] = settings.deepwiki_provider

    return payload
