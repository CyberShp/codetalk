"""Lifecycle-safe built-in model adapter.

The original adapter intentionally lets a synchronous model callable continue in a
daemon thread after CodeTalk has timed out or rejected its result.  Provider staging
must therefore outlive the public execute() call and may only be removed after that
worker has stopped using it.
"""

from __future__ import annotations

import shutil
import threading
import uuid
from pathlib import Path

from app.services.harness_facade import HarnessRunRequest
from app.services.provider_adapters.builtin_model import (
    BuiltinModelAdapter as _BaseBuiltinModelAdapter,
    _SessionState,
)
from app.services.provider_adapters.contracts import ProviderSession


class LifecycleSafeBuiltinModelAdapter(_BaseBuiltinModelAdapter):
    """Keep provider staging alive until its owning worker reaches a safe point."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._cleanup_requested: set[int] = set()
        self._cleanup_completed: set[int] = set()
        self._worker_names: dict[int, str] = {}

    def prepare(self, request: HarnessRunRequest) -> ProviderSession:
        # A workflow run can prepare the same provider more than once during retry
        # or recovery.  The provider session identity must not reuse request.run_id,
        # otherwise the later epoch overwrites the earlier state in _sessions.
        run_prefix = str(request.run_id or "builtin").strip() or "builtin"
        session_id = f"{run_prefix}_{uuid.uuid4().hex}"
        staging_dir = (
            self.artifact_dir
            / ".builtin-model-staging"
            / uuid.uuid4().hex
        )
        # prepare() returns a usable ProviderSession.  Creating the directory here
        # also removes the race window before the runner seeds execution inputs.
        staging_dir.mkdir(parents=True, exist_ok=False)
        session = ProviderSession(
            session_id=session_id,
            provider=str(request.provider or "builtin"),
            requires_network=request.requires_network,
            artifact_dir=str(staging_dir),
            mcp_profile=request.mcp_profile,
            prompt_transport=request.prompt_transport,
        )
        state = _SessionState(request=request, staging_dir=staging_dir)
        with self._lock:
            self._sessions[session_id] = state
            self._worker_names[id(state)] = f"builtin-model-{session_id}"
        return session

    def finalize_artifacts(self, session: ProviderSession) -> None:
        """Request cleanup without deleting a directory an active worker still uses."""

        self._request_staging_cleanup(self._state_for(session))

    def _discard_epoch(self, state: _SessionState) -> None:
        self._request_staging_cleanup(state)

    def _discard_staging(self, state: _SessionState) -> None:
        self._request_staging_cleanup(state)

    def _request_staging_cleanup(self, state: _SessionState) -> None:
        state_key = id(state)
        current_name = threading.current_thread().name
        with self._lock:
            self._cleanup_requested.add(state_key)
            if state_key in self._cleanup_completed:
                return
            worker_name = self._worker_names.get(state_key, "")
            worker_alive = bool(worker_name) and any(
                thread.name == worker_name and thread.is_alive()
                for thread in threading.enumerate()
            )
            # Timeout/cancel/facade-finalize can arrive while the synchronous model
            # callable is still writing diagnostics.  Defer deletion to that worker's
            # finally path.  When invoked by the worker itself, all provider writes
            # have already returned and the staging epoch is safe to remove.
            if worker_alive and current_name != worker_name:
                return
            self._cleanup_completed.add(state_key)
        shutil.rmtree(state.staging_dir, ignore_errors=True)
