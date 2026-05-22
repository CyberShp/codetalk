"""OpenAI-compatible embeddings client.

Calls /v1/embeddings — works with OpenAI, Ollama, vLLM, Together AI, etc.
"""

import logging

import httpx

from app.llm.base import async_retry

logger = logging.getLogger(__name__)

_BATCH_SIZE = 64


class EmbeddingClient:
    """Stateless embedding client for OpenAI-compatible /v1/embeddings endpoints."""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(120, connect=15),
            limits=httpx.Limits(keepalive_expiry=30),
        )

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts, returning one embedding vector per text.

        Automatically batches if len(texts) > _BATCH_SIZE.
        """
        if not texts:
            return []

        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            result = await async_retry(self._do_embed, batch, max_retries=2)
            all_embeddings.extend(result)

        return all_embeddings

    async def _do_embed(self, texts: list[str]) -> list[list[float]]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        payload = {"input": texts, "model": self._model}
        url = f"{self._base_url}/v1/embeddings"

        logger.info("Embedding %d texts via %s (model=%s)", len(texts), url, self._model)
        resp = await self._client.post(url, headers=headers, json=payload)
        resp.raise_for_status()

        data = resp.json()
        items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
        return [item["embedding"] for item in items]

    async def close(self) -> None:
        await self._client.aclose()
