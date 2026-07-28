"""Local child-session records scoped to one workflow node attempt."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable

from app.services.interprocess_file_lock import exclusive_file_lock


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_SESSION_ID_RE = re.compile(r"^child_[a-f0-9]{32}$")
_UNSET = object()


class ChildSessionConflict(RuntimeError):
    """Raised when a stable child-session key is reused with different metadata."""


class ChildSessionNotFoundError(LookupError):
    """Raised when a child session does not belong to this parent node attempt."""


class ChildSessionValidationError(ValueError):
    """Raised for malformed local child-session data or unsafe output paths."""


@dataclass(frozen=True)
class ChildSession:
    """Checkpoint-safe metadata for one child execution under a parent node."""

    session_id: str
    parent_attempt_id: str
    parent_node_id: str
    provider: str
    input_summary: Any
    status: str
    artifact_dir: str

    def to_snapshot(self) -> dict[str, Any]:
        """Return JSON-only state suitable for the parent node checkpoint."""
        return {
            "session_id": self.session_id,
            "parent_attempt_id": self.parent_attempt_id,
            "parent_node_id": self.parent_node_id,
            "provider": self.provider,
            "input_summary": _json_clone(self.input_summary, field_name="input_summary"),
            "status": self.status,
            "artifact_dir": self.artifact_dir,
        }


@dataclass(frozen=True)
class ChildSessionClaim:
    """One durable disposition for a deterministic child-session key."""

    disposition: str
    reason: str
    session: ChildSession
    snapshot: dict[str, Any]
    output: Any | None


class ChildSessionStore:
    """Persist child sessions below one parent node in an existing attempt directory."""

    def __init__(
        self,
        attempt_dir: str | Path,
        *,
        parent_attempt_id: str,
        parent_node_id: str,
    ) -> None:
        self.attempt_dir = Path(attempt_dir)
        self.parent_attempt_id = _clean_identifier(
            parent_attempt_id,
            field_name="parent_attempt_id",
        )
        self.parent_node_id = _clean_identifier(
            parent_node_id,
            field_name="parent_node_id",
        )
        self.parent_node_dir = self.attempt_dir / "nodes" / self.parent_node_id
        self.session_root = self.parent_node_dir / "child_sessions"

    def create(
        self,
        *,
        session_key: str,
        provider: str,
        input_summary: Any,
        status: str = "queued",
    ) -> ChildSession:
        """Create or load a deterministic child session for one parent-local key."""
        clean_key = _clean_session_key(session_key)
        clean_provider = _clean_text(provider, field_name="provider")
        clean_status = _clean_text(status, field_name="status")
        clean_summary = _json_clone(input_summary, field_name="input_summary")
        session_id = _child_session_id(
            parent_attempt_id=self.parent_attempt_id,
            parent_node_id=self.parent_node_id,
            session_key=clean_key,
        )
        with self._locked_session(session_id):
            return self._create_or_load_locked(
                session_id=session_id,
                session_key=clean_key,
                provider=clean_provider,
                input_summary=clean_summary,
                status=clean_status,
            )

    def claim_or_inspect(
        self,
        *,
        session_key: str,
        provider: str,
        input_summary: Any,
    ) -> ChildSessionClaim:
        """Atomically claim a never-started session or inspect its durable result.

        A deterministic key may execute once. Completed sessions are reusable only
        when the completion persisted its output; every other prior state is
        reported without permitting another execution.
        """
        clean_key = _clean_session_key(session_key)
        clean_provider = _clean_text(provider, field_name="provider")
        clean_summary = _json_clone(input_summary, field_name="input_summary")
        session_id = _child_session_id(
            parent_attempt_id=self.parent_attempt_id,
            parent_node_id=self.parent_node_id,
            session_key=clean_key,
        )
        with self._locked_session(session_id):
            if not self._metadata_path(session_id).is_file():
                session = self._create_or_load_locked(
                    session_id=session_id,
                    session_key=clean_key,
                    provider=clean_provider,
                    input_summary=clean_summary,
                    status="running",
                )
                return self._claim_result(
                    "claimed",
                    "new_session_claimed",
                    session,
                    None,
                )

            session, existing_key, output, has_output = self._load_record_with_key(
                session_id
            )
            self._require_matching_metadata(
                session=session,
                session_key=existing_key,
                expected_key=clean_key,
                provider=clean_provider,
                input_summary=clean_summary,
            )
            if session.status == "queued":
                session = self._update_status_locked(session, existing_key, "running")
                return self._claim_result(
                    "claimed",
                    "queued_session_claimed",
                    session,
                    None,
                )
            if session.status == "completed" and has_output:
                return self._claim_result(
                    "completed",
                    "completed_session_reusable",
                    session,
                    output,
                )
            if session.status == "completed":
                return self._claim_result(
                    "indeterminate",
                    "completed_output_missing",
                    session,
                    None,
                )
            if session.status in {"running", "waiting_for_input"}:
                return self._claim_result(
                    "in_progress",
                    "prior_session_in_progress",
                    session,
                    None,
                )
            if session.status in {"failed", "cancelled", "timed_out"}:
                return self._claim_result(
                    "failed",
                    "prior_session_failed",
                    session,
                    None,
                )
            return self._claim_result(
                "indeterminate",
                "unknown_prior_status",
                session,
                None,
            )

    def load(self, session_id: str) -> ChildSession:
        """Load a child session only from this store's parent node and attempt."""
        session, _ = self._load_with_key(session_id)
        return session

    def snapshot(self, session_id: str) -> dict[str, Any]:
        """Return a parent-checkpoint-serializable child-session snapshot."""
        return self.load(session_id).to_snapshot()

    def update_status(
        self,
        session_id: str,
        status: str,
        *,
        output: Any = _UNSET,
    ) -> ChildSession:
        """Atomically update only the child session's generic execution status."""
        clean_session_id = _clean_session_id(session_id)
        with self._locked_session(clean_session_id):
            session, session_key, existing_output, has_output = self._load_record_with_key(
                clean_session_id
            )
            return self._update_status_locked(
                session,
                session_key,
                status,
                output=output if output is not _UNSET else (
                    existing_output if has_output else _UNSET
                ),
            )

    def complete(self, session_id: str, output: Any) -> ChildSession:
        """Persist a reusable child result with its terminal completed state."""
        return self.update_status(session_id, "completed", output=output)

    def _update_status_locked(
        self,
        session: ChildSession,
        session_key: str,
        status: str,
        *,
        output: Any = _UNSET,
    ) -> ChildSession:
        updated = ChildSession(
            session_id=session.session_id,
            parent_attempt_id=session.parent_attempt_id,
            parent_node_id=session.parent_node_id,
            provider=session.provider,
            input_summary=session.input_summary,
            status=_clean_text(status, field_name="status"),
            artifact_dir=session.artifact_dir,
        )
        self._write_session(updated, session_key, output=output)
        return updated

    def append_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one durable, session-local event without changing parent state."""
        session = self.load(session_id)
        if payload is not None and not isinstance(payload, dict):
            raise ChildSessionValidationError("event payload must be a JSON object")
        event = {
            "event_type": _clean_text(event_type, field_name="event_type"),
            "session_id": session.session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": _json_clone(payload or {}, field_name="event payload"),
        }
        _append_jsonl(self._event_path(session.session_id), event)
        return event

    def collect_declared_outputs(
        self,
        session_id: str,
        declared_outputs: Iterable[str],
    ) -> dict[str, str]:
        """Collect existing, declared regular files without traversing session bounds."""
        session = self.load(session_id)
        artifact_dir = self._artifact_dir(session.session_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        collected: dict[str, str] = {}
        for declared_output in declared_outputs:
            relative_path = _clean_declared_output(declared_output)
            candidate = artifact_dir.joinpath(*PurePosixPath(relative_path).parts)
            _reject_symlink_path(artifact_dir, candidate)
            if not candidate.exists():
                continue
            if not candidate.is_file():
                raise ChildSessionValidationError(
                    "declared output must be a regular file"
                )
            collected[relative_path] = (
                session.artifact_dir + "/" + relative_path
            )
        return collected

    def _create_or_load_locked(
        self,
        *,
        session_id: str,
        session_key: str,
        provider: str,
        input_summary: Any,
        status: str,
    ) -> ChildSession:
        if self._metadata_path(session_id).is_file():
            existing, existing_key, _, _ = self._load_record_with_key(session_id)
            self._require_matching_metadata(
                session=existing,
                session_key=existing_key,
                expected_key=session_key,
                provider=provider,
                input_summary=input_summary,
            )
            return existing
        artifact_dir = self._artifact_dir(session_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        session = ChildSession(
            session_id=session_id,
            parent_attempt_id=self.parent_attempt_id,
            parent_node_id=self.parent_node_id,
            provider=provider,
            input_summary=input_summary,
            status=status,
            artifact_dir=self._relative_to_attempt(artifact_dir),
        )
        self._write_session(session, session_key)
        return session

    def _require_matching_metadata(
        self,
        *,
        session: ChildSession,
        session_key: str,
        expected_key: str,
        provider: str,
        input_summary: Any,
    ) -> None:
        if (
            session_key != expected_key
            or session.provider != provider
            or session.input_summary != input_summary
        ):
            raise ChildSessionConflict(
                "child session key is already bound to different metadata"
            )

    def _claim_result(
        self,
        disposition: str,
        reason: str,
        session: ChildSession,
        output: Any | None,
    ) -> ChildSessionClaim:
        return ChildSessionClaim(
            disposition=disposition,
            reason=reason,
            session=session,
            snapshot=session.to_snapshot(),
            output=(
                _json_clone(output, field_name="output")
                if output is not None
                else None
            ),
        )

    def _write_session(
        self,
        session: ChildSession,
        session_key: str,
        *,
        output: Any = _UNSET,
    ) -> None:
        payload = {
            "child_session_version": 1,
            "session_key": session_key,
            **session.to_snapshot(),
        }
        if output is not _UNSET:
            payload["output"] = _json_clone(output, field_name="output")
        _write_json_atomic(self._metadata_path(session.session_id), payload)

    def _load_with_key(self, session_id: str) -> tuple[ChildSession, str]:
        session, session_key, _, _ = self._load_record_with_key(session_id)
        return session, session_key

    def _load_record_with_key(
        self, session_id: str
    ) -> tuple[ChildSession, str, Any | None, bool]:
        clean_session_id = _clean_session_id(session_id)
        metadata_path = self._metadata_path(clean_session_id)
        if not metadata_path.is_file():
            raise ChildSessionNotFoundError("child session was not found")
        payload = _read_json(metadata_path)
        if not isinstance(payload, dict) or payload.get("child_session_version") != 1:
            raise ChildSessionValidationError("unsupported child session metadata")
        session_key = _clean_session_key(payload.get("session_key"))
        session = _session_from_snapshot(payload)
        expected_id = _child_session_id(
            parent_attempt_id=self.parent_attempt_id,
            parent_node_id=self.parent_node_id,
            session_key=session_key,
        )
        if (
            session.session_id != clean_session_id
            or session.session_id != expected_id
            or session.parent_attempt_id != self.parent_attempt_id
            or session.parent_node_id != self.parent_node_id
            or session.artifact_dir != self._relative_to_attempt(
                self._artifact_dir(clean_session_id)
            )
        ):
            raise ChildSessionValidationError("child session is outside its parent scope")
        has_output = "output" in payload
        output = (
            _json_clone(payload["output"], field_name="output") if has_output else None
        )
        return session, session_key, output, has_output

    def _session_dir(self, session_id: str) -> Path:
        return self.session_root / _clean_session_id(session_id)

    def _metadata_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "session.json"

    def _event_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "events.jsonl"

    def _artifact_dir(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "artifacts"

    def _lock_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / ".claim.lock"

    @contextmanager
    def _locked_session(self, session_id: str):
        with _child_session_lock(self._lock_path(session_id)):
            yield

    def _relative_to_attempt(self, path: Path) -> str:
        return path.relative_to(self.attempt_dir).as_posix()


def _session_from_snapshot(payload: dict[str, Any]) -> ChildSession:
    return ChildSession(
        session_id=_clean_session_id(payload.get("session_id")),
        parent_attempt_id=_clean_identifier(
            payload.get("parent_attempt_id"), field_name="parent_attempt_id"
        ),
        parent_node_id=_clean_identifier(
            payload.get("parent_node_id"), field_name="parent_node_id"
        ),
        provider=_clean_text(payload.get("provider"), field_name="provider"),
        input_summary=_json_clone(payload.get("input_summary"), field_name="input_summary"),
        status=_clean_text(payload.get("status"), field_name="status"),
        artifact_dir=_clean_artifact_dir(payload.get("artifact_dir")),
    )


def _child_session_id(
    *, parent_attempt_id: str, parent_node_id: str, session_key: str
) -> str:
    digest = hashlib.sha256(
        _canonical_json_bytes(
            {
                "parent_attempt_id": parent_attempt_id,
                "parent_node_id": parent_node_id,
                "session_key": session_key,
            },
            field_name="child session identity",
        )
    ).hexdigest()
    return "child_" + digest[:32]


def _clean_identifier(value: Any, *, field_name: str) -> str:
    cleaned = str(value or "").strip()
    if (
        not cleaned
        or cleaned in {".", ".."}
        or not _IDENTIFIER_RE.fullmatch(cleaned)
    ):
        raise ChildSessionValidationError(f"invalid {field_name}")
    return cleaned


def _clean_session_id(value: Any) -> str:
    cleaned = str(value or "").strip()
    if not _SESSION_ID_RE.fullmatch(cleaned):
        raise ChildSessionValidationError("invalid child session_id")
    return cleaned


def _clean_session_key(value: Any) -> str:
    return _clean_text(value, field_name="session_key")


def _clean_text(value: Any, *, field_name: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ChildSessionValidationError(f"{field_name} is required")
    return cleaned


def _clean_artifact_dir(value: Any) -> str:
    cleaned = _clean_declared_output(value)
    if not cleaned.endswith("/artifacts"):
        raise ChildSessionValidationError("invalid child artifact_dir")
    return cleaned


def _clean_declared_output(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ChildSessionValidationError("invalid declared output")
    cleaned = value.strip()
    posix_path = PurePosixPath(cleaned)
    windows_path = PureWindowsPath(cleaned)
    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or "\\" in cleaned
        or any(part in {"", ".", ".."} for part in posix_path.parts)
    ):
        raise ChildSessionValidationError("invalid declared output path")
    return posix_path.as_posix()


def _reject_symlink_path(root: Path, candidate: Path) -> None:
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise ChildSessionValidationError("declared output escapes artifact directory") from error
    current = root
    if current.is_symlink():
        raise ChildSessionValidationError("child artifact directory cannot be a symlink")
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise ChildSessionValidationError("declared output cannot be a symlink")


def _json_clone(payload: Any, *, field_name: str) -> Any:
    return json.loads(_canonical_json_bytes(payload, field_name=field_name))


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
        raise ChildSessionValidationError(
            f"{field_name} must be JSON-serializable"
        ) from error


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ChildSessionValidationError("invalid child session metadata") from error


@contextmanager
def _child_session_lock(lock_path: Path):
    """Serialize one deterministic session across threads and processes."""
    with exclusive_file_lock(lock_path):
        yield


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    content = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f"{path.name}.tmp-", dir=str(path.parent), text=True
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


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    encoded = _canonical_json_bytes(payload, field_name="event") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        offset = 0
        while offset < len(encoded):
            offset += os.write(fd, encoded[offset:])
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
