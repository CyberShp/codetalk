"""Anthropic Messages API client."""

import logging

import httpx

from app.llm.base import BaseLLMClient, LLMResponse

logger = logging.getLogger(__name__)

_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicClient(BaseLLMClient):
    """Client for the Anthropic Messages API (Claude models)."""

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
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        payload = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }

        url = f"{self._base_url}/v1/messages"
        logger.info("Anthropic API call: model=%s, max_tokens=%d", self._model, max_tokens)

        resp = await self._client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

        content = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                content += block["text"]

        raw_usage = data.get("usage", {})
        usage = {
            "prompt_tokens": raw_usage.get("input_tokens", 0),
            "completion_tokens": raw_usage.get("output_tokens", 0),
            "total_tokens": (
                raw_usage.get("input_tokens", 0) + raw_usage.get("output_tokens", 0)
            ),
        }

        return LLMResponse(
            content=content,
            model=data.get("model", self._model),
            usage=usage,
        )

    async def health_check(self) -> bool:
        """Check Anthropic endpoint reachability with a minimal request."""
        try:
            headers = {
                "x-api-key": self._api_key,
                "anthropic-version": _ANTHROPIC_VERSION,
                "content-type": "application/json",
            }
            payload = {
                "model": self._model,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "hi"}],
            }
            url = f"{self._base_url}/v1/messages"
            resp = await self._client.post(
                url, headers=headers, json=payload, timeout=15
            )
            return resp.status_code == 200
        except Exception as exc:
            logger.warning("Anthropic health check failed: %s", exc)
            return False

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
