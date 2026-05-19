"""Abstract base class for LLM clients."""

import asyncio
import hashlib
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

# ContextVar carrying the active task_id — set by AnalysisPipeline.run(),
# consumed by _write_debug_snapshot.  None outside a pipeline context.
current_task_id: ContextVar[str | None] = ContextVar("current_task_id", default=None)

_RETRYABLE_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.TimeoutException,
    httpx.RemoteProtocolError,
)

_DEFAULT_BACKOFF_SECONDS = (1, 2, 4)

# Truncation limits: keep snapshots small while still useful for debugging.
_SNAPSHOT_MSG_CHARS = 500
_SNAPSHOT_RESP_CHARS = 2000


async def async_retry(
    fn: Callable[..., Awaitable[T]],
    *args: object,
    max_retries: int = 3,
    backoff_seconds: tuple[int, ...] = _DEFAULT_BACKOFF_SECONDS,
    **kwargs: object,
) -> T:
    """Retry an async callable with exponential backoff.

    Retries on network errors (ConnectError, TimeoutException) and HTTP 5xx
    responses (HTTPStatusError with status >= 500).  After all retries are
    exhausted the original exception is re-raised.
    """
    last_exc: BaseException | None = None
    for attempt in range(max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429:
                last_exc = exc
                if attempt < max_retries:
                    retry_after = exc.response.headers.get("Retry-After")
                    try:
                        delay: float = float(retry_after) if retry_after else 0.0
                    except (ValueError, TypeError):
                        delay = 0.0
                    if delay <= 0:
                        delay = float(
                            backoff_seconds[min(attempt, len(backoff_seconds) - 1)] * 2
                        )
                    logger.warning(
                        "Rate limited (429), retrying in %.1fs (attempt %d/%d)",
                        delay,
                        attempt + 1,
                        max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue
            elif status < 500:
                raise  # other 4xx errors are not retryable
            last_exc = exc
        except _RETRYABLE_EXCEPTIONS as exc:
            last_exc = exc

        if attempt < max_retries:
            delay = backoff_seconds[min(attempt, len(backoff_seconds) - 1)]
            logger.warning(
                "Retry %d/%d after %.1fs — %s: %s",
                attempt + 1,
                max_retries,
                delay,
                type(last_exc).__name__,
                last_exc,
            )
            await asyncio.sleep(delay)

    raise last_exc  # type: ignore[misc]


@dataclass(frozen=True)
class LLMResponse:
    """Immutable response from an LLM call."""

    content: str
    model: str
    usage: dict  # {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int}
    truncated: bool = False


class BaseLLMClient(ABC):
    """Base class that all LLM clients must implement."""

    @abstractmethod
    async def complete(
        self,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> LLMResponse:
        """Send messages to the LLM and return a response."""
        ...

    async def stream_complete(
        self,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> AsyncIterator[str]:
        """Stream completion tokens. Default: fallback to complete() and yield once."""
        resp = await self.complete(messages, max_tokens, temperature)
        yield resp.content

    async def stream_complete_collected(
        self,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float = 0.3,
        max_retries: int = 3,
    ) -> str:
        """Stream with retry — collect all chunks and return the full text."""
        last_exc: BaseException | None = None
        for attempt in range(max_retries + 1):
            try:
                content = ""
                async for chunk in self.stream_complete(
                    messages, max_tokens, temperature,
                ):
                    content += chunk
                return content
            except (*_RETRYABLE_EXCEPTIONS, httpx.HTTPStatusError) as exc:
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                    raise
                last_exc = exc
                if attempt < max_retries:
                    delay = _DEFAULT_BACKOFF_SECONDS[
                        min(attempt, len(_DEFAULT_BACKOFF_SECONDS) - 1)
                    ]
                    logger.warning(
                        "Stream retry %d/%d after %.1fs — %s: %s",
                        attempt + 1,
                        max_retries,
                        delay,
                        type(exc).__name__,
                        exc,
                    )
                    await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]

    @abstractmethod
    async def health_check(self) -> tuple[bool, str]:
        """Check LLM endpoint reachability.

        Returns (success, message) for diagnostic feedback.
        """
        ...

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Rough token estimate. Uses tiktoken if available, else chars/4."""
        try:
            import tiktoken  # type: ignore[import-untyped]
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            return len(text) // 4

    async def _write_debug_snapshot(
        self,
        messages: list[dict],
        response: LLMResponse,
        duration_ms: float,
    ) -> None:
        """Write a truncated I/O snapshot to outputs/{task_id}/debug/.

        No-ops when current_task_id is not set (outside pipeline context).
        Write failures are logged as warnings and never propagate.
        Uses asyncio.to_thread so file I/O doesn't block the event loop.
        """
        task_id = current_task_id.get()
        if task_id is None:
            return

        from app.config import settings  # lazy to avoid circular imports at module load

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")[:17]
        content_hash = hashlib.sha256(response.content.encode()).hexdigest()
        model_safe = response.model.replace("/", "_").replace(":", "_")[:30]
        filename = f"{ts}_{model_safe}_{content_hash[:8]}.json"

        messages_str = json.dumps(messages, ensure_ascii=False)
        snapshot = {
            "timestamp": ts,
            "model": response.model,
            "messages": (
                messages_str[:_SNAPSHOT_MSG_CHARS]
                + ("…" if len(messages_str) > _SNAPSHOT_MSG_CHARS else "")
            ),
            "messages_total_chars": len(messages_str),
            "response_content": (
                response.content[:_SNAPSHOT_RESP_CHARS]
                + ("…" if len(response.content) > _SNAPSHOT_RESP_CHARS else "")
            ),
            "response_total_chars": len(response.content),
            "usage": response.usage,
            "duration_ms": round(duration_ms, 1),
        }

        def _write() -> None:
            debug_dir = settings.outputs_path / task_id / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            (debug_dir / filename).write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        try:
            await asyncio.to_thread(_write)
        except Exception as exc:
            logger.warning("LLM debug snapshot write failed: %s", exc)
