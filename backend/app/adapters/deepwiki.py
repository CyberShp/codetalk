"""deepwiki-open adapter.

IRON LAW: analyze() may ONLY do:
  (a) HTTP calls to the deepwiki API
  (b) Response format conversion
No analysis logic (regex matching beyond format extraction, AST traversal, graph building).
"""

import logging
import re
from collections.abc import AsyncIterator

import httpx

from .base import (
    AnalysisRequest,
    BaseToolAdapter,
    ToolCapability,
    ToolHealth,
    UnifiedResult,
)

logger = logging.getLogger(__name__)


class DeepwikiAdapter(BaseToolAdapter):
    def __init__(self, base_url: str = "http://deepwiki:8001"):
        self.base_url = base_url
        self._client: httpx.AsyncClient | None = None
        self._file_tree: str = ""
        self._readme: str = ""

    def _get_client(self, proxy_mode: str = "system") -> httpx.AsyncClient:
        if self._client is not None and not self._client.is_closed:
            return self._client
        proxy = None if proxy_mode == "direct" else None
        kwargs: dict = {
            "base_url": self.base_url,
            "timeout": httpx.Timeout(300, connect=10),
        }
        if proxy_mode == "direct":
            kwargs["proxy"] = None
        self._client = httpx.AsyncClient(**kwargs)
        return self._client

    @property
    def client(self) -> httpx.AsyncClient:
        return self._get_client()

    def name(self) -> str:
        return "deepwiki"

    def capabilities(self) -> list[ToolCapability]:
        return [
            ToolCapability.DOCUMENTATION,
            ToolCapability.ARCHITECTURE_DIAGRAM,
            ToolCapability.KNOWLEDGE_GRAPH,
        ]

    async def health_check(self) -> ToolHealth:
        try:
            resp = await self.client.get("/health")
            data = resp.json()
            return ToolHealth(
                is_healthy=data.get("status") == "healthy",
                container_status="running",
            )
        except Exception as exc:
            return ToolHealth(
                is_healthy=False,
                container_status="error",
                last_check=str(exc),
            )

    async def prepare(self, request: AnalysisRequest) -> None:
        resp = await self.client.get(
            "/local_repo/structure", params={"path": request.repo_local_path}
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"deepwiki cannot access repo at {request.repo_local_path}: HTTP {resp.status_code}"
            )
        body = resp.json()
        self._file_tree = body.get("file_tree", "")
        self._readme = body.get("readme", "")

    async def analyze(self, request: AnalysisRequest) -> UnifiedResult:
        proxy_mode = request.options.get("proxy_mode", "system")
        http_client = self._get_client(proxy_mode)

        logger.info(
            "deepwiki analyze: provider=%s model=%s proxy=%s has_key=%s base_url=%s",
            request.options.get("provider", "(none)"),
            request.options.get("model", "(none)"),
            proxy_mode,
            bool(request.options.get("llm_api_key")),
            request.options.get("llm_base_url", "(default)"),
        )

        target_desc = "the entire repository"
        if request.target_files:
            target_desc = f"these files: {', '.join(request.target_files)}"

        prompt = (
            f"Analyze {target_desc} and generate comprehensive documentation. "
            "Include: architecture overview, key components, data flow, "
            "and Mermaid diagrams where appropriate."
        )

        chat_payload: dict = {
            "repo_url": request.repo_local_path,
            "messages": [{"role": "user", "content": prompt}],
        }

        if request.options.get("provider"):
            chat_payload["provider"] = request.options["provider"]
        if request.options.get("model"):
            chat_payload["model"] = request.options["model"]
        if request.options.get("language"):
            chat_payload["language"] = request.options["language"]
        if request.options.get("llm_api_key"):
            chat_payload["api_key"] = request.options["llm_api_key"]
        if request.target_files:
            chat_payload["included_files"] = ",".join(request.target_files)

        full_content = ""
        async with http_client.stream(
            "POST",
            "/chat/completions/stream",
            json=chat_payload,
            timeout=300,
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_text():
                full_content += chunk

        diagrams = _extract_mermaid_blocks(full_content)

        return UnifiedResult(
            tool_name="deepwiki",
            capability=ToolCapability.DOCUMENTATION,
            data={
                "documentation": full_content,
                "file_tree": self._file_tree,
            },
            raw_output=full_content,
            diagrams=diagrams,
            metadata={
                "provider": chat_payload.get("provider"),
                "model": chat_payload.get("model"),
            },
        )

    async def stream_logs(self, run_id: str) -> AsyncIterator[str]:
        yield "deepwiki: analysis started"
        yield "deepwiki: generating documentation via RAG..."
        yield "deepwiki: completed"

    async def cleanup(self, request: AnalysisRequest) -> None:
        self._file_tree = ""
        self._readme = ""


def _extract_mermaid_blocks(markdown: str) -> list[dict]:
    """Extract ```mermaid code blocks from markdown. Response format conversion only."""
    pattern = r"```mermaid\s*\n(.*?)\n```"
    blocks = re.findall(pattern, markdown, re.DOTALL)
    return [{"type": "mermaid", "content": block.strip()} for block in blocks]
