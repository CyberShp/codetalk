"""Shared capacity coordinator for local Agent CLI processes."""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from app.config import settings


QueueCallback = Callable[[dict[str, Any]], Any | Awaitable[Any]]
logger = logging.getLogger(__name__)


@dataclass
class _Waiter:
    token: str
    provider: str
    future: asyncio.Future[None]
    on_queued: QueueCallback | None = None
    last_queue_status: dict[str, Any] | None = None
    granted: bool = False


class AgentRunCoordinator:
    """Bound global and per-provider Agent processes without busy polling."""

    def __init__(
        self,
        *,
        max_global_agent_processes: int,
        max_processes_per_provider: int,
        provider_limits: dict[str, int] | None = None,
    ) -> None:
        self.max_global = max(1, int(max_global_agent_processes))
        self.max_per_provider = max(1, int(max_processes_per_provider))
        self.provider_limits = {
            str(key).strip().lower(): max(1, int(value))
            for key, value in (provider_limits or {}).items()
            if str(key).strip()
        }
        self._lock = asyncio.Lock()
        self._active_by_provider: dict[str, int] = {}
        self._waiters: list[_Waiter] = []

    @asynccontextmanager
    async def slot(
        self,
        provider: str,
        *,
        on_queued: QueueCallback | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        normalized = str(provider or "agent").strip().lower() or "agent"
        acquired = False
        waiter: _Waiter | None = None
        queue_status: dict[str, Any] | None = None
        async with self._lock:
            provider_already_waiting = any(
                waiter.provider == normalized for waiter in self._waiters
            )
            if not provider_already_waiting and self._can_start(normalized):
                self._activate(normalized)
                acquired = True
            else:
                waiter = _Waiter(
                    token=f"agent_slot_{uuid.uuid4().hex}",
                    provider=normalized,
                    future=asyncio.get_running_loop().create_future(),
                    on_queued=on_queued,
                )
                self._waiters.append(waiter)
                queue_status = self._queue_status(waiter)
                waiter.last_queue_status = queue_status
        try:
            if queue_status is not None and on_queued is not None:
                callback_result = on_queued(queue_status)
                if inspect.isawaitable(callback_result):
                    await callback_result
            if waiter is not None:
                await waiter.future
                acquired = True
            yield await self.snapshot()
        finally:
            if acquired:
                await self._release(normalized)
            elif waiter is not None:
                await self._cancel_waiter(waiter)

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            return {
                "active_process_count": sum(self._active_by_provider.values()),
                "active_by_provider": dict(self._active_by_provider),
                "queued_process_count": len(self._waiters),
                "max_global_agent_processes": self.max_global,
                "max_processes_per_provider": self.max_per_provider,
            }

    def _provider_limit(self, provider: str) -> int:
        return self.provider_limits.get(provider, self.max_per_provider)

    def _can_start(self, provider: str) -> bool:
        return (
            sum(self._active_by_provider.values()) < self.max_global
            and self._active_by_provider.get(provider, 0) < self._provider_limit(provider)
        )

    def _activate(self, provider: str) -> None:
        self._active_by_provider[provider] = self._active_by_provider.get(provider, 0) + 1

    def _deactivate(self, provider: str) -> None:
        active = self._active_by_provider.get(provider, 0)
        if active <= 1:
            self._active_by_provider.pop(provider, None)
        else:
            self._active_by_provider[provider] = active - 1

    def _queue_status(self, waiter: _Waiter) -> dict[str, Any]:
        global_position = self._waiters.index(waiter) + 1
        provider_position = sum(
            1
            for candidate in self._waiters[:global_position]
            if candidate.provider == waiter.provider
        )
        label = _provider_label(waiter.provider)
        return {
            "active_process_count": sum(self._active_by_provider.values()),
            "global_queue_position": global_position,
            "provider_queue_position": provider_position,
            "queued_reason": f"等待 {label} 执行槽位，前方 {provider_position - 1} 个任务。",
            "provider": waiter.provider,
        }

    async def _release(self, provider: str) -> None:
        async with self._lock:
            self._deactivate(provider)
            self._wake_eligible_waiters()
            updates = self._queued_updates()
        await self._notify_queue_updates(updates)

    async def _cancel_waiter(self, waiter: _Waiter) -> None:
        async with self._lock:
            if waiter in self._waiters:
                self._waiters.remove(waiter)
                waiter.future.cancel()
            elif waiter.granted:
                waiter.granted = False
                self._deactivate(waiter.provider)
            self._wake_eligible_waiters()
            updates = self._queued_updates()
        await self._notify_queue_updates(updates)

    def _queued_updates(self) -> list[tuple[QueueCallback, dict[str, Any]]]:
        updates: list[tuple[QueueCallback, dict[str, Any]]] = []
        for waiter in self._waiters:
            if waiter.on_queued is None:
                continue
            queue_status = self._queue_status(waiter)
            if queue_status == waiter.last_queue_status:
                continue
            waiter.last_queue_status = queue_status
            updates.append((waiter.on_queued, queue_status))
        return updates

    async def _notify_queue_updates(
        self,
        updates: list[tuple[QueueCallback, dict[str, Any]]],
    ) -> None:
        for callback, queue_status in updates:
            try:
                callback_result = callback(queue_status)
                if inspect.isawaitable(callback_result):
                    await callback_result
            except Exception:
                logger.exception("Failed to persist refreshed Agent queue status")

    def _wake_eligible_waiters(self) -> None:
        for waiter in list(self._waiters):
            if not self._can_start(waiter.provider):
                continue
            self._waiters.remove(waiter)
            self._activate(waiter.provider)
            waiter.granted = True
            if not waiter.future.done():
                waiter.future.set_result(None)


def _provider_label(provider: str) -> str:
    return {
        "codex": "Codex",
        "claude": "Claude",
        "claude-code": "Claude",
        "opencode": "OpenCode",
        "nga": "NGA",
    }.get(provider, provider or "Agent")


_default_coordinator: AgentRunCoordinator | None = None
_default_signature: tuple[int, int, tuple[tuple[str, int], ...]] | None = None


def agent_run_coordinator() -> AgentRunCoordinator:
    global _default_coordinator, _default_signature
    provider_limits = {
        str(key): int(value)
        for key, value in settings.agent_provider_process_limits.items()
    }
    signature = (
        settings.max_global_agent_processes,
        settings.max_processes_per_provider,
        tuple(sorted(provider_limits.items())),
    )
    if _default_coordinator is None or _default_signature != signature:
        _default_coordinator = AgentRunCoordinator(
            max_global_agent_processes=signature[0],
            max_processes_per_provider=signature[1],
            provider_limits=provider_limits,
        )
        _default_signature = signature
    return _default_coordinator
