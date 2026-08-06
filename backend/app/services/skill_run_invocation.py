"""Frozen Skill invocation records for Workbench Task runs."""

from __future__ import annotations

import json
import hashlib
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SkillRunInvocationError(ValueError):
    """Raised when a Skill invocation cannot be frozen safely."""


@dataclass(frozen=True)
class SkillRunInvocation:
    schema_version: str
    invocation_id: str
    invocation_digest: str
    task_run_id: str
    task_id: str
    skill_id: str
    skill_version_id: str
    skill_content_digest: str
    skill_ir_digest: str
    source_zip: dict[str, Any]
    skill_ir: dict[str, Any]
    validation_report: dict[str, Any]
    input_snapshot: dict[str, Any]
    declared_context_refs: list[dict[str, Any]]
    runtime: dict[str, Any]
    sessions: dict[str, Any]
    recovery_policy: dict[str, Any]
    selected_delivery_ids: list[str]
    required_artifact_ids: list[str]
    artifact_root: str
    judge: dict[str, Any]


def freeze_skill_run_invocation(
    *,
    version: Any,
    task_run_id: str,
    task_id: str,
    artifact_root: str | Path,
    inputs: dict[str, Any],
    skill_ir: dict[str, Any] | None = None,
    selected_deliveries: list[str] | tuple[str, ...] | None = None,
    expected_content_digest: str = "",
) -> SkillRunInvocation:
    """Persist the immutable Skill Version and run inputs before execution."""

    expected = str(expected_content_digest or "").strip()
    actual = str(getattr(version, "content_digest", "") or "").strip()
    if expected and actual != expected:
        raise SkillRunInvocationError("skill version content digest changed")
    source_zip = Path(getattr(version, "source_zip_path", ""))
    ir_path = Path(getattr(version, "ir_path", ""))
    validation_path = Path(getattr(version, "validation_report_path", ""))
    for label, path in {
        "source_zip_path": source_zip,
        "ir_path": ir_path,
        "validation_report_path": validation_path,
    }.items():
        if not path.is_file():
            raise SkillRunInvocationError(f"skill invocation missing {label}")
    ir = dict(skill_ir or _read_json(ir_path))
    judge = _judge_payload(ir)
    root = Path(artifact_root)
    root.mkdir(parents=True, exist_ok=True)
    input_snapshot_path = root / "skill_input_snapshot.json"
    input_payload = json.loads(json.dumps(dict(inputs or {}), ensure_ascii=False))
    input_snapshot_path.write_text(
        json.dumps(input_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    input_snapshot_digest = _sha256_path(input_snapshot_path)
    skill_ir_digest = _sha256_path(ir_path)
    selected = _selected_delivery_ids(ir, selected_deliveries)
    required_artifacts = _required_artifact_ids(ir, selected)
    runtime = {
        "producer": _runtime_envelope("producer", ["tools", "artifact_collection", "cancellation"], agent_timeout_seconds=1800),
        "judge": _runtime_envelope("judge", ["session_isolation", "artifact_collection", "cancellation"], agent_timeout_seconds=900)
        if judge.get("required") or judge.get("artifact_ids")
        else None,
    }
    sessions = {
        "producer": {
            "agent_session_id": f"producer session/{task_run_id}",
            "role": "producer",
            "runtime_id": runtime["producer"]["runtime_id"],
            "conversation_scope": "own_session",
        },
        "judge": {
            "agent_session_id": f"judge session/{task_run_id}",
            "role": "judge",
            "runtime_id": runtime["judge"]["runtime_id"],
            "conversation_scope": "frozen_inputs_and_artifacts_only",
        } if runtime["judge"] else None,
    }

    payload = {
        "schema_version": "skill-run-invocation-v1",
        "invocation_id": f"skill_invocation_{uuid.uuid4().hex}",
        "invocation_digest": "sha256:" + "0" * 64,
        "task_run_id": str(task_run_id),
        "task_id": str(task_id),
        "skill_id": str(getattr(version, "skill_id", "") or ir.get("skill_id") or ""),
        "skill_version_id": str(getattr(version, "version_id", "") or ""),
        "skill_content_digest": actual,
        "skill_ir_digest": skill_ir_digest,
        "source_zip": _artifact_reference(version, source_zip),
        "skill_ir": _artifact_reference(version, ir_path),
        "validation_report": _artifact_reference(version, validation_path),
        "input_snapshot": {
            "ref": "skill_input_snapshot.json",
            "digest": input_snapshot_digest,
            "access_scope": "read",
        },
        "declared_context_refs": [
            {
                "ref": "skill_input_snapshot.json",
                "digest": input_snapshot_digest,
                "access_scope": "read",
            }
        ],
        "runtime": runtime,
        "sessions": sessions,
        "recovery_policy": {"max_clean_session_replacements": 1},
        "selected_delivery_ids": selected,
        "required_artifact_ids": required_artifacts,
        "artifact_root": "artifacts",
        "judge": judge,
    }
    payload["invocation_digest"] = _json_digest(payload)
    invocation = SkillRunInvocation(**payload)
    temporary = root / "skill_invocation.json.tmp"
    temporary.write_text(
        json.dumps(asdict(invocation), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(root / "skill_invocation.json")
    return invocation


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SkillRunInvocationError(f"skill invocation invalid json object: {path}")
    return payload


def _sha256_path(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _json_digest(payload: dict[str, Any]) -> str:
    unsigned = dict(payload)
    unsigned["invocation_digest"] = "sha256:" + "0" * 64
    data = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _artifact_reference(version: Any, path: Path) -> dict[str, Any]:
    ref = path.name
    version_root = getattr(version, "version_root", None)
    if version_root:
        try:
            ref = path.relative_to(Path(version_root)).as_posix()
        except ValueError:
            ref = path.name
    return {
        "ref": ref,
        "digest": _sha256_path(path),
        "access_scope": "read",
    }


def _runtime_envelope(role: str, capabilities: list[str], *, agent_timeout_seconds: int) -> dict[str, Any]:
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    runtime_id = f"runtime/{role}/local"
    report_digest = "sha256:" + hashlib.sha256(f"{role}:{','.join(capabilities)}".encode("utf-8")).hexdigest()
    return {
        "runtime_id": runtime_id,
        "requested_provider": "opencode",
        "effective_provider": "opencode",
        "requested_model": "deepseek/deepseek-v4-flash",
        "effective_model": "deepseek/deepseek-v4-flash",
        "observed_runtime_version": "local-preflight",
        "requested_capabilities": capabilities,
        "declared_context_window_tokens": 200000,
        "requested_max_output_tokens": 4096,
        "timeout_budget": {
            "queue_timeout_seconds": 60,
            "agent_timeout_seconds": agent_timeout_seconds,
            "script_timeout_seconds": 300,
            "validation_timeout_seconds": 600,
            "overall_timeout_seconds": max(agent_timeout_seconds + 600, 1800),
        },
        "capability_report_id": f"capability {role}/local",
        "capability_report_digest": report_digest,
        "preflight_receipt": {
            "status": "passed",
            "timestamp": timestamp,
            "endpoint_class": "local-dev",
            "credential_ready": True,
        },
    }


def _judge_payload(ir: dict[str, Any]) -> dict[str, Any]:
    judge = ir.get("judge") if isinstance(ir.get("judge"), dict) else {}
    artifact_ids = [
        str(item)
        for item in judge.get("artifact_ids", [])
        if str(item)
    ]
    return {
        "required": bool(judge.get("required")),
        "isolated_session": bool(judge.get("isolated_session", True)),
        "artifact_ids": artifact_ids,
    }


def _selected_delivery_ids(ir: dict[str, Any], selected_deliveries: list[str] | tuple[str, ...] | None) -> list[str]:
    requested = [str(item) for item in selected_deliveries or [] if str(item)]
    if requested:
        return requested
    return [
        str(item.get("delivery_id"))
        for item in ir.get("deliveries", [])
        if isinstance(item, dict) and str(item.get("delivery_id") or "")
    ]


def _required_artifact_ids(ir: dict[str, Any], selected_delivery_ids: list[str]) -> list[str]:
    selected = set(selected_delivery_ids)
    artifacts: list[str] = []
    for delivery in ir.get("deliveries", []):
        if not isinstance(delivery, dict) or str(delivery.get("delivery_id")) not in selected:
            continue
        artifacts.extend(str(item) for item in delivery.get("artifact_ids", []) if str(item))
    if not artifacts:
        artifacts = [
            str(item.get("artifact_id"))
            for item in ir.get("artifacts", [])
            if isinstance(item, dict) and item.get("required") and str(item.get("artifact_id") or "")
        ]
    return sorted(set(artifacts))
