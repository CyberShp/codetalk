"""Durable, Attempt-local journal records for Tool actions.

The journal intentionally records decisions only. Callers decide whether to invoke a
tool after receiving an ``execute`` decision, then commit the resulting terminal
state through this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from app.services.interprocess_file_lock import exclusive_file_lock


_JOURNAL_VERSION = 1
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_ACTION_KEY_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ToolActionConflict(RuntimeError):
    """Raised when a journal action cannot make the requested state transition."""


class ToolActionValidationError(ValueError):
    """Raised when tool action data or a persisted record is invalid."""


@dataclass(frozen=True)
class ToolActionRecord:
    """One immutable journal revision for a Tool action."""

    journal_version: int
    task_id: str
    attempt_id: str
    node_id: str
    tool_id: str
    action_key: str
    frozen_arguments: dict[str, Any]
    status: Literal["prepared", "completed", "failed"]
    prepared_at: str
    output: Any = None
    error: dict[str, Any] | None = None
    completed_at: str = ""
    failed_at: str = ""

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "journal_version": self.journal_version,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "node_id": self.node_id,
            "tool_id": self.tool_id,
            "action_key": self.action_key,
            "frozen_arguments": _clean_arguments(self.frozen_arguments),
            "status": self.status,
            "prepared_at": self.prepared_at,
        }
        if self.status == "completed":
            payload["output"] = _clean_json_value(self.output, field_name="output")
            payload["completed_at"] = self.completed_at
        elif self.status == "failed":
            payload["error"] = _clean_error(self.error)
            payload["failed_at"] = self.failed_at
        return payload


@dataclass(frozen=True)
class ToolActionDecision:
    """The durable outcome a caller must honor before it can invoke a Tool."""

    disposition: Literal["execute", "completed", "failed", "indeterminate"]
    record: ToolActionRecord


@dataclass(frozen=True)
class ToolActionContext:
    """Stable parent identity for Tool calls emitted by one Agent node."""

    task_id: str
    attempt_id: str
    node_id: str


class ToolActionJournal:
    """Atomically persist Tool action state under one existing Attempt directory."""

    def __init__(self, attempt_dir: str | Path) -> None:
        self.attempt_dir = Path(attempt_dir)
        self.journal_dir = self.attempt_dir / "tool-actions"

    def begin(
        self,
        *,
        task_id: str,
        attempt_id: str,
        node_id: str,
        tool_id: str,
        frozen_arguments: dict[str, Any],
    ) -> ToolActionDecision:
        """Record preparation once, or return the prior durable outcome.

        A previously prepared record deliberately returns ``indeterminate``. The
        journal cannot distinguish a crash before a call from one during a call, so
        a later process must escalate rather than run the Tool a second time.
        """
        record = _prepared_record(
            task_id=task_id,
            attempt_id=attempt_id,
            node_id=node_id,
            tool_id=tool_id,
            frozen_arguments=frozen_arguments,
        )
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        with _attempt_journal_lock(self.journal_dir):
            existing = self._load_unlocked(record.action_key)
            if existing is None:
                _write_json_atomic(self._record_path(record.action_key), record.to_payload())
                return ToolActionDecision("execute", record)
            _assert_matching_action(existing, record)
            if existing.status == "completed":
                return ToolActionDecision("completed", existing)
            if existing.status == "failed":
                return ToolActionDecision("failed", existing)
            return ToolActionDecision("indeterminate", existing)

    def complete(self, prepared: ToolActionRecord, *, output: Any) -> ToolActionRecord:
        """Atomically commit one prepared action's immutable completed output."""
        clean_output = _clean_json_value(output, field_name="output")
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        with _attempt_journal_lock(self.journal_dir):
            current = self._required_current(prepared)
            if current.status == "completed":
                if current.output == clean_output:
                    return current
                raise ToolActionConflict("completed output cannot be overwritten")
            if current.status == "failed":
                raise ToolActionConflict("failed action cannot be completed")
            completed = ToolActionRecord(
                **{
                    **_record_identity(current),
                    "status": "completed",
                    "prepared_at": current.prepared_at,
                    "output": clean_output,
                    "completed_at": _now(),
                }
            )
            _write_json_atomic(
                self._record_path(current.action_key), completed.to_payload()
            )
            return completed

    def fail(
        self,
        prepared: ToolActionRecord,
        *,
        error: dict[str, Any],
    ) -> ToolActionRecord:
        """Atomically commit one prepared action's immutable structured failure."""
        clean_error = _clean_error(error)
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        with _attempt_journal_lock(self.journal_dir):
            current = self._required_current(prepared)
            if current.status == "failed":
                if current.error == clean_error:
                    return current
                raise ToolActionConflict("failed error cannot be overwritten")
            if current.status == "completed":
                raise ToolActionConflict("completed action cannot be failed")
            failed = ToolActionRecord(
                **{
                    **_record_identity(current),
                    "status": "failed",
                    "prepared_at": current.prepared_at,
                    "error": clean_error,
                    "failed_at": _now(),
                }
            )
            _write_json_atomic(self._record_path(current.action_key), failed.to_payload())
            return failed

    def load(self, action_key: str) -> ToolActionRecord | None:
        """Load and validate a persisted record without changing its state."""
        clean_key = _clean_action_key(action_key)
        if not self._record_path(clean_key).is_file():
            return None
        return self._load_unlocked(clean_key)

    def _required_current(self, supplied: ToolActionRecord) -> ToolActionRecord:
        _validate_record(supplied)
        current = self._load_unlocked(supplied.action_key)
        if current is None:
            raise ToolActionConflict("prepared action record does not exist")
        _assert_matching_action(current, supplied)
        return current

    def _load_unlocked(self, action_key: str) -> ToolActionRecord | None:
        path = self._record_path(action_key)
        if not path.is_file():
            return None
        return _record_from_payload(_read_json(path))

    def _record_path(self, action_key: str) -> Path:
        clean_key = _clean_action_key(action_key)
        return self.journal_dir / f"{clean_key.removeprefix('sha256:')}.json"


def compute_tool_action_key(
    *,
    attempt_id: str,
    node_id: str,
    tool_id: str,
    frozen_arguments: dict[str, Any],
) -> str:
    """Build the stable idempotency key for one frozen Tool action."""
    payload = {
        "attempt_id": _clean_identifier(attempt_id, field_name="attempt_id"),
        "node_id": _clean_identifier(node_id, field_name="node_id"),
        "tool_id": _clean_identifier(tool_id, field_name="tool_id"),
        "frozen_arguments": _clean_arguments(frozen_arguments),
    }
    return "sha256:" + hashlib.sha256(
        _canonical_json_bytes(payload, field_name="action identity")
    ).hexdigest()


def _prepared_record(
    *,
    task_id: str,
    attempt_id: str,
    node_id: str,
    tool_id: str,
    frozen_arguments: dict[str, Any],
) -> ToolActionRecord:
    clean_task_id = _clean_identifier(task_id, field_name="task_id")
    clean_attempt_id = _clean_identifier(attempt_id, field_name="attempt_id")
    clean_node_id = _clean_identifier(node_id, field_name="node_id")
    clean_tool_id = _clean_identifier(tool_id, field_name="tool_id")
    clean_arguments = _clean_arguments(frozen_arguments)
    return ToolActionRecord(
        journal_version=_JOURNAL_VERSION,
        task_id=clean_task_id,
        attempt_id=clean_attempt_id,
        node_id=clean_node_id,
        tool_id=clean_tool_id,
        action_key=compute_tool_action_key(
            attempt_id=clean_attempt_id,
            node_id=clean_node_id,
            tool_id=clean_tool_id,
            frozen_arguments=clean_arguments,
        ),
        frozen_arguments=clean_arguments,
        status="prepared",
        prepared_at=_now(),
    )


def _record_from_payload(payload: Any) -> ToolActionRecord:
    if not isinstance(payload, dict):
        raise ToolActionValidationError("tool action record must be an object")
    status = payload.get("status")
    expected_fields = {
        "journal_version",
        "task_id",
        "attempt_id",
        "node_id",
        "tool_id",
        "action_key",
        "frozen_arguments",
        "status",
        "prepared_at",
    }
    if status == "completed":
        expected_fields.update({"output", "completed_at"})
    elif status == "failed":
        expected_fields.update({"error", "failed_at"})
    elif status != "prepared":
        raise ToolActionValidationError("unsupported tool action status")
    if set(payload) != expected_fields:
        raise ToolActionValidationError("tool action record has an invalid schema")
    record = ToolActionRecord(
        journal_version=payload.get("journal_version"),
        task_id=payload.get("task_id"),
        attempt_id=payload.get("attempt_id"),
        node_id=payload.get("node_id"),
        tool_id=payload.get("tool_id"),
        action_key=payload.get("action_key"),
        frozen_arguments=payload.get("frozen_arguments"),
        status=status,
        prepared_at=payload.get("prepared_at"),
        output=payload.get("output"),
        error=payload.get("error"),
        completed_at=payload.get("completed_at", ""),
        failed_at=payload.get("failed_at", ""),
    )
    _validate_record(record)
    return record


def _validate_record(record: ToolActionRecord) -> None:
    if not isinstance(record, ToolActionRecord):
        raise ToolActionValidationError("tool action record has an invalid type")
    if record.journal_version != _JOURNAL_VERSION:
        raise ToolActionValidationError("unsupported tool action journal_version")
    task_id = _clean_identifier(record.task_id, field_name="task_id")
    attempt_id = _clean_identifier(record.attempt_id, field_name="attempt_id")
    node_id = _clean_identifier(record.node_id, field_name="node_id")
    tool_id = _clean_identifier(record.tool_id, field_name="tool_id")
    arguments = _clean_arguments(record.frozen_arguments)
    expected_key = compute_tool_action_key(
        attempt_id=attempt_id,
        node_id=node_id,
        tool_id=tool_id,
        frozen_arguments=arguments,
    )
    if record.action_key != expected_key:
        raise ToolActionValidationError("tool action action_key does not match its identity")
    if not isinstance(record.prepared_at, str) or not record.prepared_at:
        raise ToolActionValidationError("tool action prepared_at is required")
    if record.status == "prepared":
        if record.output is not None or record.error is not None:
            raise ToolActionValidationError("prepared tool action cannot have terminal data")
        if record.completed_at or record.failed_at:
            raise ToolActionValidationError("prepared tool action cannot have terminal timestamps")
        return
    if record.status == "completed":
        _clean_json_value(record.output, field_name="output")
        if record.error is not None or not isinstance(record.completed_at, str) or not record.completed_at:
            raise ToolActionValidationError("completed tool action requires output and timestamp")
        if record.failed_at:
            raise ToolActionValidationError("completed tool action cannot have failed_at")
        return
    if record.status == "failed":
        _clean_error(record.error)
        if record.output is not None or not isinstance(record.failed_at, str) or not record.failed_at:
            raise ToolActionValidationError("failed tool action requires error and timestamp")
        if record.completed_at:
            raise ToolActionValidationError("failed tool action cannot have completed_at")
        return
    raise ToolActionValidationError("unsupported tool action status")


def _assert_matching_action(
    current: ToolActionRecord,
    supplied: ToolActionRecord,
) -> None:
    if _record_identity(current) != _record_identity(supplied):
        raise ToolActionConflict("tool action ownership does not match the persisted record")


def _record_identity(record: ToolActionRecord) -> dict[str, Any]:
    return {
        "journal_version": record.journal_version,
        "task_id": record.task_id,
        "attempt_id": record.attempt_id,
        "node_id": record.node_id,
        "tool_id": record.tool_id,
        "action_key": record.action_key,
        "frozen_arguments": _clean_arguments(record.frozen_arguments),
    }


def _clean_identifier(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ToolActionValidationError(f"{field_name} must be a string")
    clean_value = value.strip()
    if not clean_value or not _IDENTIFIER_RE.fullmatch(clean_value):
        raise ToolActionValidationError(f"invalid {field_name}")
    return clean_value


def _clean_action_key(value: Any) -> str:
    if not isinstance(value, str) or not _ACTION_KEY_RE.fullmatch(value):
        raise ToolActionValidationError("invalid tool action key")
    return value


def _clean_arguments(arguments: Any) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ToolActionValidationError("frozen_arguments must be a JSON object")
    cleaned = _clean_json_value(arguments, field_name="frozen_arguments")
    if not isinstance(cleaned, dict):  # pragma: no cover - guarded above.
        raise ToolActionValidationError("frozen_arguments must be a JSON object")
    return cleaned


def _clean_error(error: Any) -> dict[str, Any]:
    if not isinstance(error, dict):
        raise ToolActionValidationError("error must be a JSON object")
    cleaned = _clean_json_value(error, field_name="error")
    if not isinstance(cleaned, dict):  # pragma: no cover - guarded above.
        raise ToolActionValidationError("error must be a JSON object")
    return cleaned


def _clean_json_value(value: Any, *, field_name: str) -> Any:
    return json.loads(_canonical_json_bytes(value, field_name=field_name))


def _canonical_json_bytes(payload: Any, *, field_name: str) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ToolActionValidationError(
            f"{field_name} must be JSON-serializable"
        ) from error


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ToolActionValidationError("tool action record cannot be read") from error


@contextmanager
def _attempt_journal_lock(journal_dir: Path):
    with exclusive_file_lock(journal_dir / ".lock"):
        yield


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    content = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f"{path.name}.tmp-",
        dir=str(path.parent),
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
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
