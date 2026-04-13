"""deepwiki-open adapter.

IRON LAW: analyze() may ONLY do:
  (a) HTTP calls to the deepwiki API
  (b) Response format conversion
No analysis logic (regex matching beyond format extraction, AST traversal, graph building).
"""

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


class DeepwikiAdapter(BaseToolAdapter):
    def __init__(self, base_url: str = "http://deepwiki:8001"):
        self.base_url = base_url
        self._client: httpx.AsyncClient | None = None
        self._file_tree: str = ""
        self._readme: str = ""

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=httpx.Timeout(300, connect=10)
            )
        return self._client

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
        if request.target_files:
            chat_payload["included_files"] = ",".join(request.target_files)

        full_content = ""
        async with self.client.stream(
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
