"""DeepWiki-Open HTTP client adapter (Sprint 2 rewrite).

Provides wiki generation (streaming and non-streaming), wiki export,
and health checking against the DeepWiki-Open API.
"""

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Wiki generation can take a very long time for large repos
_WIKI_TIMEOUT = 1800.0  # 30 minutes
_DEFAULT_TIMEOUT = 30.0

# Default prompt sent to DeepWiki for wiki generation
_DEFAULT_PROMPT = (
    "Analyze the entire repository and generate comprehensive documentation. "
    "Include: architecture overview, key components, data flow, "
    "and Mermaid diagrams where appropriate."
)


class DeepWikiClient:
    """HTTP client for the DeepWiki-Open wiki generation server."""

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = base_url or settings.deepwiki_api_url
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(_DEFAULT_TIMEOUT, connect=10),
                trust_env=False,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def health(self) -> bool:
        """Check if DeepWiki API is reachable and healthy.

        GET /health
        """
        try:
            resp = await self.client.get("/health")
            if resp.status_code < 400:
                return True
            logger.warning("DeepWikiClient: health check returned HTTP %d", resp.status_code)
            return False
        except Exception as exc:
            logger.warning("DeepWikiClient: health check failed: %s", exc)
            return False

    async def generate_wiki(
        self,
        repo_path: str,
        prompt: str | None = None,
    ) -> dict[str, Any]:
        """Generate wiki documentation (non-streaming, waits for full response).

        POST /chat/completions  {"repo_url": path, "messages": [...]}

        Returns the full response as a dict with at least a ``content`` key.
        """
        messages = [{"role": "user", "content": prompt or _DEFAULT_PROMPT}]
        payload: dict[str, Any] = {
            "repo_url": repo_path,
            "messages": messages,
        }

        try:
            resp = await self.client.post(
                "/chat/completions",
                json=payload,
                timeout=_WIKI_TIMEOUT,
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            logger.info("DeepWikiClient: wiki generated for %s", repo_path)
            return data
        except httpx.HTTPStatusError as exc:
            logger.error(
                "DeepWikiClient: generate_wiki HTTP %d: %s",
                exc.response.status_code,
                exc.response.text[:200],
            )
            raise
        except Exception as exc:
            logger.error("DeepWikiClient: generate_wiki failed: %s", exc)
            raise

    async def stream_wiki(
        self,
        repo_path: str,
        prompt: str | None = None,
    ) -> AsyncIterator[str]:
        """Generate wiki documentation via streaming.

        POST /chat/completions  {"repo_url": path, "messages": [...]}

        Yields text chunks as they arrive from the server.
        """
        messages = [{"role": "user", "content": prompt or _DEFAULT_PROMPT}]
        payload: dict[str, Any] = {
            "repo_url": repo_path,
            "messages": messages,
        }

        try:
            async with self.client.stream(
                "POST",
                "/chat/completions",
                json=payload,
                timeout=_WIKI_TIMEOUT,
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_text():
                    if chunk:
                        yield chunk
        except httpx.HTTPStatusError as exc:
            logger.error(
                "DeepWikiClient: stream_wiki HTTP %d: %s",
                exc.response.status_code,
                exc.response.text[:200],
            )
            raise
        except Exception as exc:
            logger.error("DeepWikiClient: stream_wiki failed: %s", exc)
            raise

    async def get_wiki_structure(self, repo_path: str) -> dict[str, Any]:
        """Export wiki as structured JSON.

        POST /export/wiki  {"repo_url": path}
        """
        payload: dict[str, Any] = {"repo_url": repo_path}

        try:
            resp = await self.client.post(
                "/export/wiki",
                json=payload,
                timeout=_WIKI_TIMEOUT,
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            logger.info("DeepWikiClient: wiki structure exported for %s", repo_path)
            return data
        except httpx.HTTPStatusError as exc:
            logger.error(
                "DeepWikiClient: get_wiki_structure HTTP %d: %s",
                exc.response.status_code,
                exc.response.text[:200],
            )
            raise
        except Exception as exc:
            logger.error("DeepWikiClient: get_wiki_structure failed: %s", exc)
            raise
