"""Abstract base class for LLM clients."""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

_RETRYABLE_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.TimeoutException,
)

_DEFAULT_BACKOFF_SECONDS = (1, 2, 4)


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
            if exc.response.status_code < 500:
                raise  # 4xx errors are not retryable
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

    @abstractmethod
    async def health_check(self) -> tuple[bool, str]:
        """Check LLM endpoint reachability.

        Returns (success, message) for diagnostic feedback.
        """
        ...
