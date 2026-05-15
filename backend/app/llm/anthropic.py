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
        force_direct: bool = False,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model

        verify = ssl_cert_path if ssl_cert_path else True
        if force_direct:
            self._client = httpx.AsyncClient(
                transport=httpx.AsyncHTTPTransport(verify=verify),
                trust_env=False,
                timeout=httpx.Timeout(300, connect=15),
            )
        elif proxy_url:
            self._client = httpx.AsyncClient(
                proxy=proxy_url,
                verify=verify,
                trust_env=False,
                timeout=httpx.Timeout(300, connect=15),
            )
        else:
            # system proxy mode: trust_env=True (default) lets httpx read env vars
            self._client = httpx.AsyncClient(
                verify=verify,
                timeout=httpx.Timeout(300, connect=15),
            )

    async def complete(
        self,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> LLMResponse:
        headers = {
            "x-api-key": self._api_key,
            "Authorization": f"Bearer {self._api_key}",
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

    async def health_check(self) -> tuple[bool, str]:
        """Check Anthropic endpoint reachability with a minimal request.

        Returns (success, message) where success distinguishes reachable+authenticated
        from reachable-but-rejected or unreachable.
        """
        try:
            headers = {
                "x-api-key": self._api_key,
                "Authorization": f"Bearer {self._api_key}",
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
                url, headers=headers, json=payload, timeout=60
            )
            if resp.status_code < 400:
                return True, "连接成功"
            if resp.status_code < 500:
                return False, f"服务可达，但认证或接口失败 (HTTP {resp.status_code})"
            return False, f"服务端错误 (HTTP {resp.status_code})"
        except Exception as exc:
            logger.warning("Anthropic health check failed: %s", exc)
            return False, f"连接失败: {exc}"

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
