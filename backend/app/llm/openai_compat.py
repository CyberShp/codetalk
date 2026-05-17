"""OpenAI-compatible Chat Completions API client.

Works with any endpoint that implements the /v1/chat/completions interface
(OpenAI, vLLM, Ollama, LM Studio, Together AI, etc.).
"""

import logging
import time

import httpx

from app.llm.base import BaseLLMClient, LLMResponse, async_retry

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
        t0 = time.monotonic()
        result = await async_retry(
            self._do_complete, messages, max_tokens, temperature,
            max_retries=3,
        )
        await self._write_debug_snapshot(messages, result, (time.monotonic() - t0) * 1000)
        return result

    async def _do_complete(
        self,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        """Execute a single OpenAI-compatible chat completion (called via retry)."""
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
        finish_reasons = [c.get("finish_reason", "unknown") for c in choices]
        logger.debug("OpenAI-compat response: %d choices, finish_reasons=%s", len(choices), finish_reasons)

        content = choices[0]["message"]["content"] if choices else ""

        if not content or len(content.strip()) < 10:
            logger.warning(
                "OpenAI-compat returned empty/too-short content (len=%d, model=%s)",
                len(content),
                self._model,
            )
            raise ValueError(
                f"LLM returned empty or too-short response "
                f"(len={len(content.strip())}, model={self._model})"
            )

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

    async def health_check(self) -> tuple[bool, str]:
        """Check endpoint reachability via /v1/models.

        Returns (success, message) with diagnostic detail.
        """
        try:
            headers = {"Authorization": f"Bearer {self._api_key}"}
            url = f"{self._base_url}/v1/models"
            resp = await self._client.get(url, headers=headers, timeout=60)
            if resp.status_code < 400:
                return True, "连接成功"
            if resp.status_code < 500:
                return False, f"服务可达，但认证或接口失败 (HTTP {resp.status_code})"
            return False, f"服务端错误 (HTTP {resp.status_code})"
        except Exception as exc:
            logger.warning("OpenAI-compat health check failed: %s", exc)
            return False, f"连接失败: {exc}"

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
