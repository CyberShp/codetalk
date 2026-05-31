"""OpenAI-compatible Chat Completions API client.

Works with any endpoint that implements the /v1/chat/completions interface
(OpenAI, vLLM, Ollama, LM Studio, Together AI, etc.).
"""

import json
import logging
import time
from collections.abc import AsyncIterator

import httpx

from app.llm.base import (
    BaseLLMClient,
    LLMResponse,
    async_retry,
    current_finish_reason,
)

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
        pool_limits = httpx.Limits(keepalive_expiry=30)
        if force_direct:
            self._client = httpx.AsyncClient(
                transport=httpx.AsyncHTTPTransport(verify=verify),
                trust_env=False,
                timeout=httpx.Timeout(300, connect=15),
                limits=pool_limits,
            )
        elif proxy_url:
            self._client = httpx.AsyncClient(
                proxy=proxy_url,
                verify=verify,
                trust_env=False,
                timeout=httpx.Timeout(300, connect=15),
                limits=pool_limits,
            )
        else:
            self._client = httpx.AsyncClient(
                verify=verify,
                timeout=httpx.Timeout(300, connect=15),
                limits=pool_limits,
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

    async def stream_complete(
        self,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> AsyncIterator[str]:
        """True streaming via OpenAI SSE — yields content deltas as they arrive."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
            "stream": True,
        }
        url = f"{self._base_url}/v1/chat/completions"
        logger.info("OpenAI-compat streaming call: model=%s", self._model)

        async with self._client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                # Try several shapes that internal OpenAI-compatible
                # providers use in the wild.  We yield whichever
                # non-empty string we find first.
                try:
                    choice = chunk["choices"][0]
                except (KeyError, IndexError):
                    continue
                # Record the provider's finish_reason so debug snapshots and the
                # report generator can tell a truncated ("length") generation
                # apart from a clean stop (P1).
                fr = choice.get("finish_reason")
                if fr:
                    current_finish_reason.set(str(fr))
                candidates = [
                    (choice.get("delta") or {}).get("content"),
                    (choice.get("delta") or {}).get("text"),
                    (choice.get("delta") or {}).get("reasoning_content"),
                    choice.get("text"),
                    choice.get("content"),
                    (choice.get("message") or {}).get("content"),
                ]
                # Accept the first non-empty string candidate, but tolerate
                # providers that return content as a list of segment dicts.
                delta: str = ""
                for cand in candidates:
                    if isinstance(cand, str) and cand:
                        delta = cand
                        break
                    if isinstance(cand, list):
                        parts = []
                        for seg in cand:
                            if isinstance(seg, dict):
                                txt = seg.get("text") or seg.get("content")
                                if txt:
                                    parts.append(str(txt))
                        if parts:
                            delta = "".join(parts)
                            break
                if delta:
                    yield delta

    async def _do_complete(
        self,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        """Execute a single OpenAI-compatible chat completion (called via retry)."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
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

        truncated = choices[0].get("finish_reason") == "length" if choices else False
        if truncated:
            logger.warning(
                "LLM response truncated (finish_reason=length), output may be incomplete"
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
            truncated=truncated,
        )

    async def health_check(self) -> tuple[bool, str]:
        """Check endpoint reachability via /v1/models.

        Returns (success, message) with diagnostic detail.
        """
        try:
            headers: dict[str, str] = {}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
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
