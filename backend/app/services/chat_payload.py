"""Shared deepwiki payload builder for HTTP and WebSocket chat endpoints."""

from app.api.chat import ChatMessage
from app.models.llm_config import LLMConfig
from app.models.repository import Repository
from app.utils.repo_paths import to_tool_repo_path
from app.config import settings

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
) -> tuple[dict, bool]:
    """Build deepwiki request payload.

    Returns (payload, trust_env).
    """
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

    proxy_mode = llm_config.proxy_mode if llm_config else "system"
    trust_env = proxy_mode != "direct"

    return payload, trust_env
