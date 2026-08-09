"""Frozen Skill invocation records for Workbench Task runs."""

from __future__ import annotations

import hashlib
import json
import shutil
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
    agent_runtime: dict[str, Any] | None = None,
    preflight_receipt: dict[str, Any] | None = None,
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
    frozen = _freeze_skill_inputs(
        root=root,
        source_zip=source_zip,
        source_root=Path(getattr(version, "unpacked_root", "")),
        ir_path=ir_path,
        validation_path=validation_path,
    )
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
        "producer": _runtime_envelope(
            "producer",
            ["tools", "artifact_collection", "cancellation"],
            agent_timeout_seconds=1800,
            agent_runtime=agent_runtime,
            preflight_receipt=preflight_receipt,
        ),
        "judge": _runtime_envelope(
            "judge",
            ["session_isolation", "artifact_collection", "cancellation"],
            agent_timeout_seconds=900,
            agent_runtime=agent_runtime,
            preflight_receipt=preflight_receipt,
        )
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
        "source_zip": _run_artifact_reference(root, frozen["source_zip"]),
        "skill_ir": _run_artifact_reference(root, frozen["skill_ir"]),
        "validation_report": _run_artifact_reference(
            root, frozen["validation_report"]
        ),
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


def _freeze_skill_inputs(
    *,
    root: Path,
    source_zip: Path,
    source_root: Path,
    ir_path: Path,
    validation_path: Path,
) -> dict[str, Path]:
    if not source_root.is_dir():
        raise SkillRunInvocationError("skill invocation missing unpacked_root")
    frozen_root = root / "frozen_skill"
    if frozen_root.exists():
        raise SkillRunInvocationError("skill invocation frozen inputs already exist")
    temporary = root / f".frozen_skill-{uuid.uuid4().hex}.tmp"
    try:
        temporary.mkdir(parents=False)
        shutil.copy2(source_zip, temporary / "source-package.zip")
        shutil.copy2(ir_path, temporary / "skill-ir-v1.json")
        shutil.copy2(validation_path, temporary / "validation-report.json")
        shutil.copytree(source_root, temporary / "source", symlinks=True)
        temporary.replace(frozen_root)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {
        "source_zip": frozen_root / "source-package.zip",
        "skill_ir": frozen_root / "skill-ir-v1.json",
        "validation_report": frozen_root / "validation-report.json",
    }


def _run_artifact_reference(root: Path, path: Path) -> dict[str, Any]:
    return {
        "ref": path.relative_to(root).as_posix(),
        "digest": _sha256_path(path),
        "access_scope": "read",
    }


def _runtime_envelope(
    role: str,
    capabilities: list[str],
    *,
    agent_timeout_seconds: int,
    agent_runtime: dict[str, Any] | None,
    preflight_receipt: dict[str, Any] | None,
) -> dict[str, Any]:
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    runtime_config = dict(agent_runtime or {})
    runtime_config_id = str(runtime_config.get("id") or "").strip()
    provider = str(runtime_config.get("provider") or "unconfigured").strip()
    runtime_id = (
        f"agent-runtime:{runtime_config_id}"
        if runtime_config_id
        else f"runtime/{role}/unconfigured"
    )
    report_digest = "sha256:" + hashlib.sha256(
        f"{role}:{','.join(capabilities)}".encode()
    ).hexdigest()
    envelope = {
        "runtime_id": runtime_id,
        "requested_provider": provider,
        "effective_provider": provider if runtime_config_id else "unknown",
        "requested_model": "deepseek/deepseek-v4-flash",
        "effective_model": "unknown",
        "observed_runtime_version": "unknown",
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
        "preflight_receipt": dict(preflight_receipt)
        if preflight_receipt
        else {
            "status": "pending",
            "timestamp": timestamp,
            "endpoint_class": "local-runtime",
            "credential_ready": False,
        },
    }
    command = str(runtime_config.get("command") or "").strip()
    if runtime_config_id and command:
        envelope["execution"] = {
            "runtime_config_id": runtime_config_id,
            "provider_ref": f"agent-runtime:{runtime_config_id}",
            "command": [command, *[str(item) for item in runtime_config.get("args") or []]],
            "prompt_transport": str(runtime_config.get("prompt_transport") or "stdin"),
            "mcp_profile": str(runtime_config.get("mcp_profile") or ""),
            "requires_network": bool(runtime_config.get("requires_network", True)),
            "environment_keys": sorted(
                str(key)
                for key in (runtime_config.get("env") or {})
                if str(key).strip()
            ),
        }
    return envelope


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
