"""OpenAI-compatible Chat Completions API client.

Works with any endpoint that implements the /v1/chat/completions interface
(OpenAI, vLLM, Ollama, LM Studio, Together AI, etc.).
"""

import logging

import httpx

from app.llm.base import BaseLLMClient, LLMResponse

logger = logging.getLogger(__name__)


class OpenAICompatClient(BaseLLMClient):
    """Client for any OpenAI-compatible chat completions endpoint."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        proxy_url: str | None = None,
        ssl_cert_path: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model

        transport_kwargs: dict = {}
        if ssl_cert_path:
            transport_kwargs["verify"] = ssl_cert_path

        proxy = proxy_url if proxy_url else None
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(120, connect=15),
            proxy=proxy,
            **transport_kwargs,
        )

    async def complete(
        self,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> LLMResponse:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }

        url = f"{self._base_url}/v1/chat/completions"
        logger.info(
            "OpenAI-compat API call: model=%s, max_tokens=%d",
            self._model,
            max_tokens,
        )

        resp = await self._client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

        choices = data.get("choices", [])
        content = choices[0]["message"]["content"] if choices else ""

        raw_usage = data.get("usage", {})
        usage = {
            "prompt_tokens": raw_usage.get("prompt_tokens", 0),
            "completion_tokens": raw_usage.get("completion_tokens", 0),
            "total_tokens": raw_usage.get("total_tokens", 0),
        }

        return LLMResponse(
            content=content,
            model=data.get("model", self._model),
            usage=usage,
        )

    async def health_check(self) -> bool:
        """Check endpoint reachability via /v1/models."""
        try:
            headers = {"Authorization": f"Bearer {self._api_key}"}
            url = f"{self._base_url}/v1/models"
            resp = await self._client.get(url, headers=headers, timeout=10)
            return resp.status_code == 200
        except Exception as exc:
            logger.warning("OpenAI-compat health check failed: %s", exc)
            return False

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
