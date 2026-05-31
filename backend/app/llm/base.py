"""Abstract base class for LLM clients."""

import asyncio
import hashlib
import json
import logging
import time
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

# ContextVar carrying the provider's finish_reason / stop_reason for the most
# recent streamed call in *this* task.  asyncio.gather runs each report section
# in its own task with an isolated context copy, so providers can set this from
# the SSE stream and _write_debug_snapshot can record it without cross-talk.
current_finish_reason: ContextVar[str | None] = ContextVar(
    "current_finish_reason", default=None
)

# Per-task count of genuinely truncated LLM generations (finish_reason=length).
# A ContextVar can't aggregate across the isolated contexts that asyncio.gather
# gives each report section, so we keep a process-global tally keyed by task_id
# (set via current_task_id).  The report generator resets it at the start of a
# run and reads it at the end to mark the run's health (Round 2/3 bug: truncated
# sections were still shipped as `completed`).
_task_truncations: dict[str, int] = {}


def note_truncation(task_id: str | None) -> None:
    if task_id:
        _task_truncations[task_id] = _task_truncations.get(task_id, 0) + 1


def forgive_truncation(task_id: str | None, count: int = 1) -> None:
    """Mark provider truncations as recovered after a later retry succeeds."""
    if not task_id or count <= 0:
        return
    remaining = max(0, _task_truncations.get(task_id, 0) - count)
    if remaining:
        _task_truncations[task_id] = remaining
    else:
        _task_truncations.pop(task_id, None)


def get_truncation_count(task_id: str | None) -> int:
    return _task_truncations.get(task_id, 0) if task_id else 0


def reset_truncation_count(task_id: str | None) -> None:
    if task_id:
        _task_truncations.pop(task_id, None)

_RETRYABLE_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.TimeoutException,
    httpx.RemoteProtocolError,
)


class LLMEmptyOutputError(RuntimeError):
    """Raised when an LLM stream completed but produced no usable content.

    The report generator catches this to mark a section as failed instead
    of silently writing an empty file.
    """

_DEFAULT_BACKOFF_SECONDS = (1, 2, 4)

# Truncation limits: keep snapshots small while still useful for debugging.
# NOTE: _SNAPSHOT_RESP_CHARS previously sat at 2000, which made *every* longer
# report section look "truncated" in debug JSON (trailing "…") even when the
# saved report was complete.  Raised to a realistic section size and the
# snapshot now explicitly flags when *it* did the truncating (P1).
_SNAPSHOT_MSG_CHARS = 500
_SNAPSHOT_RESP_CHARS = 12000


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
        *,
        min_chars: int = 0,
        retry_on_empty: bool = True,
    ) -> str:
        """Stream with retry — collect all chunks and return the full text.

        Empty / suspiciously-short outputs raise ``LLMEmptyOutputError`` so
        callers can mark the relevant report section as failed instead of
        silently writing a blank file.  When ``retry_on_empty`` is True we
        retry once (or up to ``max_retries``) before raising — providers
        occasionally drop the first delta when overloaded.
        """
        t0 = time.monotonic()
        last_exc: BaseException | None = None
        for attempt in range(max_retries + 1):
            try:
                content = ""
                chunk_count = 0
                # Reset before streaming so a stale value from a prior call in
                # this task can't be mis-attributed.
                current_finish_reason.set(None)
                async for chunk in self.stream_complete(
                    messages, max_tokens, temperature,
                ):
                    if chunk:
                        content += chunk
                        chunk_count += 1
                if (
                    retry_on_empty
                    and (chunk_count == 0 or len(content.strip()) < max(min_chars, 1))
                ):
                    raise LLMEmptyOutputError(
                        f"streaming produced empty/too-short output "
                        f"(chunks={chunk_count}, chars={len(content.strip())})"
                    )
                model_name = getattr(self, "_model", "streaming")
                await self._write_debug_snapshot(
                    messages,
                    LLMResponse(content=content, model=model_name, usage={}),
                    (time.monotonic() - t0) * 1000,
                )
                return content
            except LLMEmptyOutputError as exc:
                last_exc = exc
                if attempt < max_retries:
                    delay = _DEFAULT_BACKOFF_SECONDS[
                        min(attempt, len(_DEFAULT_BACKOFF_SECONDS) - 1)
                    ]
                    logger.warning(
                        "Stream returned empty output, retrying in %.1fs (attempt %d/%d)",
                        delay, attempt + 1, max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise
            except (*_RETRYABLE_EXCEPTIONS, httpx.HTTPStatusError) as exc:
                if isinstance(exc, httpx.HTTPStatusError):
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
                                    _DEFAULT_BACKOFF_SECONDS[
                                        min(attempt, len(_DEFAULT_BACKOFF_SECONDS) - 1)
                                    ] * 2
                                )
                            logger.warning(
                                "Stream rate limited (429), retrying in %.1fs (attempt %d/%d)",
                                delay,
                                attempt + 1,
                                max_retries,
                            )
                            await asyncio.sleep(delay)
                            continue
                    elif status < 500:
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
        snapshot_truncated = len(response.content) > _SNAPSHOT_RESP_CHARS
        finish_reason = current_finish_reason.get()
        llm_truncated = (finish_reason == "length") or bool(
            getattr(response, "truncated", False)
        )
        if llm_truncated:
            note_truncation(task_id)
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
                + ("…" if snapshot_truncated else "")
            ),
            "response_total_chars": len(response.content),
            # finish_reason from the provider (length == real LLM truncation).
            "finish_reason": finish_reason,
            # True when the provider itself reported a truncated generation.
            "llm_truncated": llm_truncated,
            # True when the trailing "…" above is the SNAPSHOT's own doing, not
            # the model's — so reviewers stop mistaking it for LLM truncation.
            "response_snapshot_truncated": snapshot_truncated,
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
