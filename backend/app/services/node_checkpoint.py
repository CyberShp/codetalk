"""Node checkpoint storage for Workbench V3 attempts."""

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
from typing import Any

from app.services.interprocess_file_lock import exclusive_file_lock


_NODE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_CHECKPOINT_CONFLICT_MESSAGE = "节点检查点与当前运行不一致，请重试。"


class CheckpointConflict(RuntimeError):
    """Raised when a repeat checkpoint key carries different committed data."""


class CheckpointValidationError(ValueError):
    """Raised when persisted checkpoint data is malformed."""


@dataclass(frozen=True)
class NodeCheckpoint:
    checkpoint_version: int
    task_id: str
    attempt_id: str
    node_id: str
    revision: int
    idempotency_key: str
    status: str
    input_hash: str
    output_artifact_hashes: dict[str, str]
    provider_session: dict[str, Any]
    result_snapshot: dict[str, Any] | None
    completed_at: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "checkpoint_version": self.checkpoint_version,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "node_id": self.node_id,
            "revision": self.revision,
            "idempotency_key": self.idempotency_key,
            "status": self.status,
            "input_hash": self.input_hash,
            "output_artifact_hashes": dict(self.output_artifact_hashes),
            "provider_session": dict(self.provider_session),
            "result_snapshot": _clean_result_snapshot(self.result_snapshot),
            "completed_at": self.completed_at,
        }


class NodeCheckpointStore:
    """Persist node completion records under an attempt artifact directory."""

    def __init__(self, attempt_dir: str | Path) -> None:
        self.attempt_dir = Path(attempt_dir)
        self.checkpoint_dir = self.attempt_dir / "checkpoints"

    def load(self, node_id: str) -> NodeCheckpoint | None:
        path = self._checkpoint_path(node_id)
        if not path.is_file():
            return None
        return _checkpoint_from_payload(_read_json(path))

    def load_reusable_seed(
        self,
        node_id: str,
        *,
        expected_idempotency_key: str,
    ) -> dict[str, Any] | None:
        """Return a completed node's immutable scheduler seed when it still matches."""
        checkpoint = self.load(node_id)
        if (
            checkpoint is None
            or checkpoint.status != "completed"
            or checkpoint.idempotency_key != str(expected_idempotency_key)
            or checkpoint.result_snapshot is None
        ):
            return None
        return _clean_result_snapshot(checkpoint.result_snapshot)

    def commit_completed(
        self,
        *,
        task_id: str,
        attempt_id: str,
        node_id: str,
        idempotency_key: str,
        input_hash: str,
        output_artifact_hashes: dict[str, str] | None = None,
        provider_session: dict[str, Any] | None = None,
        result_snapshot: dict[str, Any] | None = None,
    ) -> NodeCheckpoint:
        clean_task_id = str(task_id)
        clean_attempt_id = str(attempt_id)
        clean_node_id = _clean_node_id(node_id)
        clean_outputs = {
            str(path): str(digest)
            for path, digest in (output_artifact_hashes or {}).items()
        }
        clean_provider = {
            str(key): value
            for key, value in (provider_session or {}).items()
        }
        clean_result_snapshot = _clean_result_snapshot(result_snapshot)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        with _attempt_checkpoint_lock(self.checkpoint_dir):
            existing = self.load(clean_node_id)
            if existing and (
                existing.task_id != clean_task_id
                or existing.attempt_id != clean_attempt_id
                or existing.node_id != clean_node_id
            ):
                raise CheckpointConflict(_CHECKPOINT_CONFLICT_MESSAGE)
            if existing and existing.idempotency_key == str(idempotency_key):
                if (
                    existing.input_hash == str(input_hash)
                    and existing.output_artifact_hashes == clean_outputs
                    and existing.provider_session == clean_provider
                    and existing.result_snapshot == clean_result_snapshot
                    and existing.status == "completed"
                ):
                    return existing
                raise CheckpointConflict(_CHECKPOINT_CONFLICT_MESSAGE)

            checkpoint = NodeCheckpoint(
                checkpoint_version=1,
                task_id=clean_task_id,
                attempt_id=clean_attempt_id,
                node_id=clean_node_id,
                revision=(existing.revision + 1) if existing else 1,
                idempotency_key=str(idempotency_key),
                status="completed",
                input_hash=str(input_hash),
                output_artifact_hashes=clean_outputs,
                provider_session=clean_provider,
                result_snapshot=clean_result_snapshot,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            _write_json_atomic(
                self._checkpoint_path(clean_node_id),
                checkpoint.to_payload(),
            )
            return checkpoint

    def _checkpoint_path(self, node_id: str) -> Path:
        return self.checkpoint_dir / f"{_clean_node_id(node_id)}.json"


def _checkpoint_from_payload(payload: Any) -> NodeCheckpoint:
    if not isinstance(payload, dict):
        raise CheckpointValidationError("checkpoint payload must be an object")
    output_hashes = payload.get("output_artifact_hashes") or {}
    provider_session = payload.get("provider_session") or {}
    if not isinstance(output_hashes, dict) or not isinstance(provider_session, dict):
        raise CheckpointValidationError("checkpoint hashes/session must be objects")
    checkpoint = NodeCheckpoint(
        checkpoint_version=int(payload.get("checkpoint_version") or 0),
        task_id=str(payload.get("task_id") or ""),
        attempt_id=str(payload.get("attempt_id") or ""),
        node_id=_clean_node_id(str(payload.get("node_id") or "")),
        revision=int(payload.get("revision") or 0),
        idempotency_key=str(payload.get("idempotency_key") or ""),
        status=str(payload.get("status") or ""),
        input_hash=str(payload.get("input_hash") or ""),
        output_artifact_hashes={str(key): str(value) for key, value in output_hashes.items()},
        provider_session=dict(provider_session),
        result_snapshot=_clean_result_snapshot(payload.get("result_snapshot")),
        completed_at=str(payload.get("completed_at") or ""),
    )
    if checkpoint.checkpoint_version != 1:
        raise CheckpointValidationError("unsupported checkpoint_version")
    if checkpoint.revision < 1:
        raise CheckpointValidationError("checkpoint revision must be positive")
    if checkpoint.status != "completed":
        raise CheckpointValidationError("unsupported checkpoint status")
    if not checkpoint.task_id or not checkpoint.attempt_id or not checkpoint.idempotency_key:
        raise CheckpointValidationError("checkpoint identity fields are required")
    return checkpoint


def compute_node_idempotency_key(
    *,
    node_definition: Any,
    frozen_inputs: Any,
    upstream_artifact_hashes: dict[str, str],
) -> str:
    """Build a stable key from the immutable execution context of one node."""
    payload = {
        "node_definition": node_definition,
        "frozen_inputs": frozen_inputs,
        "upstream_artifact_hashes": upstream_artifact_hashes,
    }
    return "sha256:" + hashlib.sha256(
        _canonical_json_bytes(payload, field_name="idempotency inputs")
    ).hexdigest()


def _clean_result_snapshot(
    result_snapshot: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if result_snapshot is None:
        return None
    if not isinstance(result_snapshot, dict):
        raise CheckpointValidationError("result_snapshot must be a JSON object")
    return json.loads(
        _canonical_json_bytes(result_snapshot, field_name="result_snapshot")
    )


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
        raise CheckpointValidationError(
            f"{field_name} must be JSON-serializable"
        ) from error


def _clean_node_id(node_id: str) -> str:
    value = str(node_id or "").strip()
    if not value or not _NODE_ID_RE.fullmatch(value) or "/" in value or "\\" in value:
        raise CheckpointValidationError("invalid checkpoint node_id")
    return value


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@contextmanager
def _attempt_checkpoint_lock(checkpoint_dir: Path):
    with exclusive_file_lock(checkpoint_dir / ".lock"):
        yield


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
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
