"""Durable, Attempt-local ownership lease for workflow execution."""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from app.services.interprocess_file_lock import exclusive_file_lock


_OWNER_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,256}$")


class ExecutionLeaseError(RuntimeError):
    """Base class for workflow execution lease failures."""


class ExecutionLeaseLost(ExecutionLeaseError):
    """Raised when a caller tries to renew a lease it no longer owns."""


class ExecutionLeaseValidationError(ExecutionLeaseError):
    """Raised when a persisted execution lease record is invalid."""


@dataclass(frozen=True)
class ExecutionLease:
    """The durable ownership record for one running workflow Attempt."""

    execution_lease_version: int
    attempt_id: str
    owner_token: str
    acquired_at: datetime
    heartbeat_at: datetime
    expires_at: datetime

    def to_payload(self) -> dict[str, str | int]:
        return {
            "execution_lease_version": self.execution_lease_version,
            "attempt_id": self.attempt_id,
            "owner_token": self.owner_token,
            "acquired_at": _timestamp(self.acquired_at),
            "heartbeat_at": _timestamp(self.heartbeat_at),
            "expires_at": _timestamp(self.expires_at),
        }


class WorkflowExecutionLeaseStore:
    """Serialize durable execution ownership for one Attempt artifact directory."""

    def __init__(self, attempt_dir: str | Path, *, attempt_id: str) -> None:
        self.attempt_dir = Path(attempt_dir)
        self.attempt_id = _required_text(attempt_id, "attempt_id")

    @property
    def lease_path(self) -> Path:
        return self.attempt_dir / "workflow_execution_lease.json"

    @property
    def lock_path(self) -> Path:
        return self.attempt_dir / ".workflow_execution_lease.lock"

    def acquire(
        self,
        *,
        ttl: timedelta,
        now: datetime | None = None,
    ) -> ExecutionLease | None:
        """Claim this Attempt when no unexpired owner record exists.

        A caller that sees ``None`` did not acquire execution ownership. A malformed
        record raises instead of being replaced, so a corrupted Attempt cannot start
        a second executor silently.
        """
        duration = _lease_duration(ttl)
        current_time = _current_time(now)
        with self._locked():
            existing = self._load_unlocked()
            if existing is not None and current_time < existing.expires_at:
                return None
            lease = ExecutionLease(
                execution_lease_version=1,
                attempt_id=self.attempt_id,
                owner_token=secrets.token_urlsafe(32),
                acquired_at=current_time,
                heartbeat_at=current_time,
                expires_at=current_time + duration,
            )
            _write_json_atomic(self.lease_path, lease.to_payload())
            return lease

    def heartbeat(
        self,
        lease: ExecutionLease,
        *,
        ttl: timedelta,
        now: datetime | None = None,
    ) -> ExecutionLease:
        """Extend the expiry of a still-owned, unexpired lease."""
        duration = _lease_duration(ttl)
        current_time = _current_time(now)
        with self._locked():
            existing = self._load_unlocked()
            self._require_current_owner(existing, lease)
            if current_time >= existing.expires_at:
                raise ExecutionLeaseLost("workflow execution lease has expired")
            if current_time < existing.heartbeat_at:
                raise ExecutionLeaseValidationError(
                    "heartbeat time must not precede the current heartbeat"
                )
            renewed = ExecutionLease(
                execution_lease_version=existing.execution_lease_version,
                attempt_id=existing.attempt_id,
                owner_token=existing.owner_token,
                acquired_at=existing.acquired_at,
                heartbeat_at=current_time,
                expires_at=current_time + duration,
            )
            _write_json_atomic(self.lease_path, renewed.to_payload())
            return renewed

    def release(self, lease: ExecutionLease) -> bool:
        """Release a lease only when its owner token still matches the record."""
        with self._locked():
            existing = self._load_unlocked()
            if existing is None:
                return False
            if (
                not isinstance(lease, ExecutionLease)
                or lease.attempt_id != self.attempt_id
                or lease.owner_token != existing.owner_token
            ):
                return False
            self.lease_path.unlink()
            _fsync_directory(self.attempt_dir)
            return True

    def load(self) -> ExecutionLease | None:
        """Read the durable Attempt-local ownership record, validating all fields."""
        with self._locked():
            return self._load_unlocked()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.attempt_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with exclusive_file_lock(self.lock_path):
            yield

    def _load_unlocked(self) -> ExecutionLease | None:
        if not self.lease_path.is_file():
            return None
        try:
            payload = json.loads(self.lease_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ExecutionLeaseValidationError(
                "workflow execution lease record is unreadable"
            ) from error
        return _lease_from_payload(payload, expected_attempt_id=self.attempt_id)

    def _require_current_owner(
        self,
        existing: ExecutionLease | None,
        lease: ExecutionLease,
    ) -> None:
        if (
            existing is None
            or not isinstance(lease, ExecutionLease)
            or lease.attempt_id != self.attempt_id
            or lease.owner_token != existing.owner_token
        ):
            raise ExecutionLeaseLost("workflow execution lease no longer owns this Attempt")


def _lease_from_payload(payload: Any, *, expected_attempt_id: str) -> ExecutionLease:
    if not isinstance(payload, dict):
        raise ExecutionLeaseValidationError("execution lease payload must be an object")
    lease = ExecutionLease(
        execution_lease_version=_positive_int(
            payload.get("execution_lease_version"),
            "execution_lease_version",
        ),
        attempt_id=_required_text(payload.get("attempt_id"), "attempt_id"),
        owner_token=_owner_token(payload.get("owner_token")),
        acquired_at=_parse_timestamp(payload.get("acquired_at"), "acquired_at"),
        heartbeat_at=_parse_timestamp(payload.get("heartbeat_at"), "heartbeat_at"),
        expires_at=_parse_timestamp(payload.get("expires_at"), "expires_at"),
    )
    if lease.execution_lease_version != 1:
        raise ExecutionLeaseValidationError("unsupported execution_lease_version")
    if lease.attempt_id != expected_attempt_id:
        raise ExecutionLeaseValidationError("execution lease attempt_id does not match")
    if lease.heartbeat_at < lease.acquired_at:
        raise ExecutionLeaseValidationError("heartbeat_at must not precede acquired_at")
    if lease.expires_at <= lease.heartbeat_at:
        raise ExecutionLeaseValidationError("expires_at must follow heartbeat_at")
    return lease


def _lease_duration(value: timedelta) -> timedelta:
    if not isinstance(value, timedelta):
        raise ExecutionLeaseValidationError("ttl must be a timedelta")
    seconds = value.total_seconds()
    if not math.isfinite(seconds) or seconds <= 0:
        raise ExecutionLeaseValidationError("ttl must be positive")
    return value


def _current_time(value: datetime | None) -> datetime:
    return _as_utc(value, "now") if value is not None else datetime.now(timezone.utc)


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ExecutionLeaseValidationError(f"{field_name} must be a positive integer")
    return value


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ExecutionLeaseValidationError(f"{field_name} must be a string")
    text = value.strip()
    if not text:
        raise ExecutionLeaseValidationError(f"{field_name} is required")
    return text


def _owner_token(value: object) -> str:
    token = _required_text(value, "owner_token")
    if not _OWNER_TOKEN_RE.fullmatch(token):
        raise ExecutionLeaseValidationError("owner_token has an invalid format")
    return token


def _parse_timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ExecutionLeaseValidationError(f"{field_name} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ExecutionLeaseValidationError(
            f"{field_name} must be an ISO timestamp"
        ) from error
    return _as_utc(parsed, field_name)


def _as_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ExecutionLeaseValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return _as_utc(value, "timestamp").isoformat()


def _write_json_atomic(path: Path, payload: dict[str, str | int]) -> None:
    content = json.dumps(payload, ensure_ascii=True, allow_nan=False, indent=2, sort_keys=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f"{path.name}.tmp-",
        dir=str(path.parent),
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
