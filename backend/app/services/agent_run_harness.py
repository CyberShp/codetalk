"""Agent run and artifact validation harness for CodeTalk workflows."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json_sha256(payload: Any) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


_MAX_ARG_PROMPT_BYTES = 24000


def _default_agent_session_policy() -> dict[str, Any]:
    return {
        "external_session_mode": "disposable_process",
        "resume_supported": False,
        "resume_source": "none",
        "continuity_owner": "codetalk_task_bundle",
        "memory_sources": [
            "task_bundle",
            "evidence_memory",
            "source_slices",
            "validated_artifacts",
        ],
        "raw_output_reuse": "never_without_validation",
        "context_overflow_strategy": "source_slice_request_turn",
    }


@dataclass(frozen=True)
class AgentRunRecord:
    run_id: str
    turn_id: str
    provider: str
    command: list[str]
    cwd: str
    artifact_dir: str
    mcp_profile: str = ""
    session_policy: dict[str, Any] = field(default_factory=_default_agent_session_policy)
    status: str = "created"
    created_at: str = field(default_factory=_now)


@dataclass(frozen=True)
class ArtifactValidationResult:
    status: str
    provenance_status: str
    accepted_artifacts: list[str] = field(default_factory=list)
    rejected_artifacts: list[dict[str, str]] = field(default_factory=list)
    accepted_artifact_details: list[dict[str, Any]] = field(default_factory=list)
    rejected_artifact_details: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AgentRunExecutionResult:
    run_id: str
    status: str
    exit_code: int | None
    started_at: str
    completed_at: str
    duration_ms: int
    timed_out: bool = False
    error: str = ""
    provider_diagnostics: dict[str, Any] = field(default_factory=dict)


class AgentRunHarness:
    """Writes the reproducible envelope around an external Agent CLI run."""

    def __init__(self, artifact_dir: str | Path) -> None:
        self.artifact_dir = Path(artifact_dir)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

    def create_run(
        self,
        *,
        provider: str,
        command: list[str],
        cwd: str,
        workflow_snapshot: dict[str, Any],
        task_bundle: dict[str, Any],
        mcp_profile: str = "",
        run_id: str | None = None,
        turn_id: str = "turn_1",
    ) -> AgentRunRecord:
        run = AgentRunRecord(
            run_id=run_id or _new_id("agent_run"),
            turn_id=turn_id or "turn_1",
            provider=provider,
            command=[str(part) for part in command],
            cwd=cwd,
            artifact_dir=str(self.artifact_dir),
            mcp_profile=mcp_profile,
        )
        self._write_json("agent_run.json", asdict(run))
        self._write_json("task_bundle.json", task_bundle)
        self._write_json("workflow_snapshot.json", workflow_snapshot)
        return run

    def record_raw_output(self, run_id: str, *, stdout: str, stderr: str = "") -> None:
        run_payload = self._read_json_file("agent_run.json")
        turn_id = (
            str(run_payload.get("turn_id") or "turn_1")
            if isinstance(run_payload, dict)
            else "turn_1"
        )
        payload = "\n".join(part for part in [stdout, stderr] if part)
        self._write_text("raw_output.txt", _redact(payload))
        self._write_json(
            "runtime_events.jsonl",
            {
                "event": "raw_output_recorded",
                "run_id": run_id,
                "turn_id": turn_id,
                "created_at": _now(),
            },
            append_jsonl=True,
        )

    def execute_run(self, run_id: str, *, timeout_sec: int = 90) -> AgentRunExecutionResult:
        run_payload = self._read_json_file("agent_run.json")
        if not isinstance(run_payload, dict) or run_payload.get("run_id") != run_id:
            raise ValueError(f"unknown agent run: {run_id}")
        command = [str(part) for part in run_payload.get("command") or []]
        if not command:
            raise ValueError("agent run command is empty")
        cwd = str(run_payload.get("cwd") or "")
        if not cwd:
            raise ValueError("agent run cwd is empty")

        task_bundle = self._read_json_file("task_bundle.json")
        workflow_snapshot = self._read_json_file("workflow_snapshot.json")
        turn_id = str(run_payload.get("turn_id") or "turn_1")
        context_discovery_decision_summary = _context_discovery_decision_summary(
            task_bundle if isinstance(task_bundle, dict) else {}
        )
        provider_diagnostics = _provider_diagnostics_snapshot(
            run_payload=run_payload,
            task_bundle=task_bundle if isinstance(task_bundle, dict) else {},
        )
        session_policy = (
            run_payload.get("session_policy")
            if isinstance(run_payload.get("session_policy"), dict)
            else _default_agent_session_policy()
        )
        self._write_json("provider_diagnostics.json", provider_diagnostics)
        stdin_payload_obj = {
            "run_id": run_id,
            "turn_id": turn_id,
            "provider": run_payload.get("provider") or "",
            "mcp_profile": run_payload.get("mcp_profile") or "",
            "session_policy": session_policy,
            "workflow_snapshot": workflow_snapshot if isinstance(workflow_snapshot, dict) else {},
            "task_bundle": task_bundle if isinstance(task_bundle, dict) else {},
            "context_discovery_decision_summary": context_discovery_decision_summary,
            "provider_diagnostics": provider_diagnostics,
            "artifact_dir": str(self.artifact_dir),
        }
        stdin_payload = json.dumps(stdin_payload_obj, ensure_ascii=False)
        task_bundle_sha256 = _json_sha256(task_bundle if isinstance(task_bundle, dict) else {})
        workflow_snapshot_sha256 = _json_sha256(
            workflow_snapshot if isinstance(workflow_snapshot, dict) else {}
        )
        env_hints = {
            "CODETALK_AGENT_READONLY": "1",
            "CODETALK_REPO_PATH": cwd,
            "CODETALK_AGENT_ARTIFACT_DIR": str(self.artifact_dir),
        }
        launch_command, command_resolution = _launch_command_from_provider_health(
            command,
            provider_diagnostics,
        )
        process_command, stdin_payload_bytes, prompt_transport = _agent_process_invocation_for_harness(
            provider=str(run_payload.get("provider") or ""),
            command=launch_command,
            prompt=stdin_payload,
        )
        prompt_transport_reason = ""
        if (
            prompt_transport != "stdin"
            and len(stdin_payload.encode("utf-8")) > _MAX_ARG_PROMPT_BYTES
        ):
            process_command = list(launch_command)
            stdin_payload_bytes = stdin_payload.encode("utf-8")
            prompt_transport = "stdin"
            prompt_transport_reason = "large_payload_forced_stdin"
        self._write_json(
            "execution_input.json",
            {
                "run_id": run_id,
                "turn_id": turn_id,
                "provider": run_payload.get("provider") or "",
                "command": command,
                "launch_command": launch_command,
                "command_resolution": command_resolution,
                "process_command": process_command,
                "prompt_transport": prompt_transport,
                "prompt_transport_reason": prompt_transport_reason,
                "cwd": cwd,
                "timeout_sec": max(1, int(timeout_sec)),
                "mcp_profile": run_payload.get("mcp_profile") or "",
                "session_policy": session_policy,
                "env_hints": env_hints,
                "task_bundle_sha256": task_bundle_sha256,
                "workflow_snapshot_sha256": workflow_snapshot_sha256,
                "context_discovery_decision_summary": context_discovery_decision_summary,
                "provider_diagnostics": provider_diagnostics,
                "stdin": stdin_payload_obj,
                "stdin_json_sha256": hashlib.sha256(
                    stdin_payload.encode("utf-8")
                ).hexdigest(),
            },
        )

        started_at = _now()
        self._write_json(
            "runtime_events.jsonl",
            {
                "event": "agent_execution_input_prepared",
                "run_id": run_id,
                "turn_id": turn_id,
                "artifact": "execution_input.json",
                "task_bundle_sha256": task_bundle_sha256,
                "workflow_snapshot_sha256": workflow_snapshot_sha256,
                "context_discovery_decision_summary": context_discovery_decision_summary,
                "provider_diagnostics_artifact": "provider_diagnostics.json",
                "created_at": started_at,
            },
            append_jsonl=True,
        )
        self._write_json(
            "runtime_events.jsonl",
            {
                "event": "agent_run_started",
                "run_id": run_id,
                "turn_id": turn_id,
                "command": command,
                "launch_command": launch_command,
                "command_resolution": command_resolution,
                "process_command": process_command,
                "prompt_transport": prompt_transport,
                "prompt_transport_reason": prompt_transport_reason,
                "created_at": started_at,
            },
            append_jsonl=True,
        )
        started = datetime.now(timezone.utc)
        env = _agent_process_env_for_harness(
            provider=str(run_payload.get("provider") or ""),
            repo_path=cwd,
        )
        env.update(env_hints)

        exit_code: int | None = None
        stdout = ""
        stderr = ""
        timed_out = False
        error = ""
        try:
            completed = subprocess.run(
                process_command,
                cwd=cwd,
                input=stdin_payload_bytes,
                capture_output=True,
                timeout=max(1, int(timeout_sec)),
                env=env,
                check=False,
            )
            exit_code = completed.returncode
            stdout = _decode_subprocess_text(completed.stdout)
            stderr = _decode_subprocess_text(completed.stderr)
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = _decode_subprocess_text(exc.stdout)
            stderr = _decode_subprocess_text(exc.stderr)
            error = f"agent run timed out after {timeout_sec}s"
        except OSError as exc:
            error = str(exc)

        completed_at = _now()
        duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        status = "timeout" if timed_out else "completed" if exit_code == 0 else "error"
        self.record_raw_output(run_id, stdout=stdout, stderr=stderr)
        result = AgentRunExecutionResult(
            run_id=run_id,
            status=status,
            exit_code=exit_code,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            timed_out=timed_out,
            error=error,
            provider_diagnostics={
                **_provider_diagnostics_result_summary(provider_diagnostics),
                **_command_resolution_result_summary(command_resolution),
            },
        )
        self._write_json("execution_result.json", asdict(result))
        self._write_json(
            "runtime_events.jsonl",
            {
                "event": "agent_run_completed",
                "run_id": run_id,
                "turn_id": turn_id,
                "status": status,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "error": error,
                "created_at": completed_at,
            },
            append_jsonl=True,
        )
        self._write_json("agent_run.json", {**run_payload, "status": status})
        return result

    def _write_json(self, filename: str, payload: Any, *, append_jsonl: bool = False) -> None:
        path = self.artifact_dir / filename
        if append_jsonl:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
            return
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def _write_text(self, filename: str, content: str) -> None:
        (self.artifact_dir / filename).write_text(content, encoding="utf-8")

    def _read_json_file(self, filename: str) -> Any:
        try:
            return json.loads((self.artifact_dir / filename).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None


class ArtifactValidationHarness:
    """Validates Agent-produced artifacts before they become evidence."""

    def __init__(self, artifact_dir: str | Path) -> None:
        self.artifact_dir = Path(artifact_dir)

    def validate_required_artifacts(self, *, required_artifacts: list[str]) -> ArtifactValidationResult:
        accepted: list[str] = []
        rejected: list[dict[str, str]] = []
        accepted_details: list[dict[str, Any]] = []
        rejected_details: list[dict[str, str]] = []
        for artifact in required_artifacts:
            safe_artifact = _safe_required_artifact(artifact)
            if not safe_artifact:
                item = {"artifact": artifact, "reason": "invalid_artifact_path", "path": ""}
                rejected.append({"artifact": artifact, "reason": item["reason"]})
                rejected_details.append(item)
                continue
            path = self.artifact_dir / safe_artifact
            if not path.exists():
                item = {
                    "artifact": artifact,
                    "reason": "missing_required_artifact",
                    "path": str(path),
                }
                rejected.append({"artifact": artifact, "reason": item["reason"]})
                rejected_details.append(item)
            elif path.is_dir():
                item = {
                    "artifact": artifact,
                    "reason": "artifact_is_directory",
                    "path": str(path),
                }
                rejected.append({"artifact": artifact, "reason": item["reason"]})
                rejected_details.append(item)
            else:
                accepted.append(safe_artifact)
                accepted_details.append(_artifact_detail(path, artifact=safe_artifact))
        return ArtifactValidationResult(
            status="invalid" if rejected else "ok",
            provenance_status="agent_artifact_present" if not rejected else "unverified_agent_claim",
            accepted_artifacts=accepted,
            rejected_artifacts=rejected,
            accepted_artifact_details=accepted_details,
            rejected_artifact_details=rejected_details,
        )

    def validate_mr_artifacts(self, *, required_artifacts: list[str]) -> ArtifactValidationResult:
        accepted: list[str] = []
        rejected: list[dict[str, str]] = []
        accepted_details: list[dict[str, Any]] = []
        rejected_details: list[dict[str, str]] = []
        warnings: list[str] = []

        for artifact in required_artifacts:
            safe_artifact = _safe_required_artifact(artifact)
            if not safe_artifact:
                item = {"artifact": artifact, "reason": "invalid_artifact_path", "path": ""}
                rejected.append({"artifact": artifact, "reason": item["reason"]})
                rejected_details.append(item)
                continue
            path = self.artifact_dir / safe_artifact
            if not path.exists():
                item = {
                    "artifact": artifact,
                    "reason": "missing_required_artifact",
                    "path": str(path),
                }
                rejected.append({"artifact": artifact, "reason": item["reason"]})
                rejected_details.append(item)
            elif path.is_dir():
                item = {
                    "artifact": artifact,
                    "reason": "artifact_is_directory",
                    "path": str(path),
                }
                rejected.append({"artifact": item["artifact"], "reason": item["reason"]})
                rejected_details.append(item)
            else:
                accepted.append(safe_artifact)
                accepted_details.append(_artifact_detail(path, artifact=safe_artifact))
        if rejected:
            return ArtifactValidationResult(
                status="invalid",
                provenance_status="unverified_agent_claim",
                accepted_artifacts=accepted,
                rejected_artifacts=rejected,
                accepted_artifact_details=accepted_details,
                rejected_artifact_details=rejected_details,
            )

        snapshot = self._read_json("mr_snapshot.json")
        diff_text = (self.artifact_dir / "diff.patch").read_text(encoding="utf-8")
        changed_files = self._read_json("changed_files.json")
        if not isinstance(snapshot, dict):
            item = {
                "artifact": "mr_snapshot.json",
                "reason": "invalid_json_object",
                "path": str(self.artifact_dir / "mr_snapshot.json"),
            }
            rejected.append({"artifact": item["artifact"], "reason": item["reason"]})
            rejected_details.append(item)
        if not isinstance(changed_files, list):
            item = {
                "artifact": "changed_files.json",
                "reason": "invalid_json_array",
                "path": str(self.artifact_dir / "changed_files.json"),
            }
            rejected.append({"artifact": item["artifact"], "reason": item["reason"]})
            rejected_details.append(item)

        for field_name in (
            "source", "mcp_profile", "mr_url", "project", "mr_id", "title",
            "source_branch", "target_branch", "base_commit", "head_commit",
            "diff_sha256", "changed_files_count",
        ):
            if isinstance(snapshot, dict) and snapshot.get(field_name) in {None, ""}:
                item = {
                    "artifact": "mr_snapshot.json",
                    "reason": f"missing_{field_name}",
                    "path": str(self.artifact_dir / "mr_snapshot.json"),
                }
                rejected.append({"artifact": item["artifact"], "reason": item["reason"]})
                rejected_details.append(item)

        if isinstance(snapshot, dict):
            actual_sha = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
            if snapshot.get("diff_sha256") != actual_sha:
                item = {
                    "artifact": "diff.patch",
                    "reason": "diff_sha256_mismatch",
                    "path": str(self.artifact_dir / "diff.patch"),
                }
                rejected.append({"artifact": item["artifact"], "reason": item["reason"]})
                rejected_details.append(item)

        if isinstance(changed_files, list):
            diff_paths = _paths_from_unified_diff(diff_text)
            for item in changed_files:
                item_path = str((item or {}).get("path") or "").replace("\\", "/")
                if item_path and item_path not in diff_paths:
                    warnings.append(f"changed file not present in diff: {item_path}")

        return ArtifactValidationResult(
            status="invalid" if rejected else "ok",
            provenance_status="agent_mcp_provenance" if not rejected else "unverified_agent_claim",
            accepted_artifacts=accepted,
            rejected_artifacts=rejected,
            accepted_artifact_details=accepted_details,
            rejected_artifact_details=rejected_details,
            warnings=warnings,
        )

    def _read_json(self, filename: str) -> Any:
        try:
            return json.loads((self.artifact_dir / filename).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None


def _paths_from_unified_diff(diff_text: str) -> set[str]:
    paths: set[str] = set()
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            for candidate in parts[-2:]:
                cleaned = re.sub(r"^[ab]/", "", candidate).replace("\\", "/")
                if cleaned:
                    paths.add(cleaned)
        elif line.startswith(("--- a/", "+++ b/")):
            paths.add(line[6:].replace("\\", "/"))
    return paths


def _artifact_detail(path: Path, *, artifact: str) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "artifact": artifact,
        "path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }


def _safe_required_artifact(artifact: Any) -> str:
    text = str(artifact or "").strip().replace("\\", "/")
    if not text:
        return ""
    posix = PurePosixPath(text)
    windows = PureWindowsPath(text)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or windows.root:
        return ""
    if any(part in {"", ".", ".."} for part in posix.parts):
        return ""
    return posix.as_posix()


def _context_discovery_decision_summary(task_bundle: dict[str, Any]) -> dict[str, Any]:
    decision = task_bundle.get("context_discovery_decision")
    if not isinstance(decision, dict):
        return {}
    summary: dict[str, Any] = {}
    for provider, payload in decision.items():
        if not isinstance(provider, str) or not isinstance(payload, dict):
            continue
        item: dict[str, Any] = {}
        for key in (
            "requested_by_agent_instructions",
            "codetalk_callable",
            "agent_owned_possible",
            "fallback_path",
            "warnings",
        ):
            if key in payload:
                item[key] = payload[key]
        if item:
            summary[provider] = item
    return summary


def _agent_process_invocation_for_harness(
    *,
    provider: str,
    command: list[str],
    prompt: str,
) -> tuple[list[str], bytes, str]:
    """Reuse external-agent prompt transport rules for Workbench task runs."""
    try:
        from app.services.external_agent_discovery import _agent_process_invocation

        return _agent_process_invocation(provider, command, prompt)
    except Exception:
        return list(command), prompt.encode("utf-8"), "stdin"


def _agent_process_env_for_harness(*, provider: str, repo_path: str) -> dict[str, str]:
    """Use the same environment hints as source discovery, including CCR config."""
    try:
        from app.services.external_agent_discovery import _agent_process_env

        return _agent_process_env(provider, repo_path)
    except Exception:
        return os.environ.copy()


def _launch_command_from_provider_health(
    configured_command: list[str],
    provider_diagnostics: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    health = provider_diagnostics.get("health")
    if not isinstance(health, dict) or health.get("status") != "available":
        return list(configured_command), {"source": "configured_command"}
    argv = health.get("argv")
    if not isinstance(argv, list) or not argv:
        return list(configured_command), {"source": "configured_command", "reason": "health_argv_missing"}
    launch_kind = str(health.get("launch_kind") or "")
    should_use_health_argv = (
        bool(provider_diagnostics.get("provider_snapshot_present"))
        or bool(health.get("used_fallback", False))
        or launch_kind in {"powershell", "powershell-profile", "powershell-script"}
    )
    if not should_use_health_argv:
        return list(configured_command), {
            "source": "configured_command",
            "health_status": "available",
            "reason": "ad_hoc_command_preserved",
        }
    launch_command = [str(part) for part in argv]
    return launch_command, {
        "source": "provider_health",
        "used_fallback": bool(health.get("used_fallback", False)),
        "launch_kind": launch_kind,
        "configured_command": str(health.get("configured_command") or ""),
        "path": str(health.get("path") or ""),
    }


def _provider_diagnostics_snapshot(
    *,
    run_payload: dict[str, Any],
    task_bundle: dict[str, Any],
) -> dict[str, Any]:
    provider = str(run_payload.get("provider") or "").strip()
    snapshot = task_bundle.get("provider_snapshot")
    provider_info: dict[str, Any] = {}
    if isinstance(snapshot, dict):
        providers = snapshot.get("providers")
        if isinstance(providers, dict):
            raw_provider = providers.get(provider)
            if isinstance(raw_provider, dict):
                provider_info = raw_provider
    diagnostics = provider_info.get("diagnostics") if isinstance(provider_info, dict) else {}
    if not isinstance(diagnostics, dict):
        diagnostics = {}
    health = _agent_provider_health_snapshot(
        provider=provider,
        command=str(diagnostics.get("configured_command_text") or " ".join(
            str(part) for part in run_payload.get("command") or []
        )).strip(),
        fallback_commands=[
            str(command).strip()
            for command in diagnostics.get("fallback_command_texts") or []
            if str(command).strip()
        ],
    )
    return {
        "provider": provider,
        "status": str(provider_info.get("status") or "unknown") if provider_info else "unknown",
        "provider_snapshot_present": bool(provider_info),
        "owner": str(provider_info.get("owner") or "agent_cli") if provider_info else "agent_cli",
        "agent_owned": bool(provider_info.get("agent_owned", True)) if provider_info else True,
        "codetalk_callable": bool(provider_info.get("codetalk_callable", False)) if provider_info else False,
        "command": [str(part) for part in run_payload.get("command") or []],
        "cwd": str(run_payload.get("cwd") or ""),
        "mcp_profile": str(run_payload.get("mcp_profile") or ""),
        "diagnostics": diagnostics,
        "health": health,
        "credential_boundary": str(provider_info.get("credential_boundary") or "") if provider_info else "",
        "unavailable_behavior": str(provider_info.get("unavailable_behavior") or "") if provider_info else "",
    }


def _agent_provider_health_snapshot(
    *,
    provider: str,
    command: str,
    fallback_commands: list[str],
) -> dict[str, Any]:
    if not provider:
        return {"status": "unknown", "reason": "missing provider"}
    try:
        from app.services.external_agent_discovery import (
            check_provider_health,
            redact_agent_diagnostic_text,
        )

        health = check_provider_health(
            provider,
            command,
            fallback_commands=fallback_commands,
        )
        return _redact_diagnostic_payload(health, redact_agent_diagnostic_text)
    except Exception as exc:
        return {
            "status": "error",
            "reason": _redact(str(exc)),
        }


def _redact_diagnostic_payload(payload: Any, redactor: Any) -> Any:
    if isinstance(payload, dict):
        return {
            str(key): _redact_diagnostic_payload(value, redactor)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_redact_diagnostic_payload(item, redactor) for item in payload]
    if isinstance(payload, str):
        return redactor(payload)
    return payload


def _provider_diagnostics_result_summary(payload: dict[str, Any]) -> dict[str, Any]:
    diagnostics = payload.get("diagnostics")
    if not isinstance(diagnostics, dict):
        diagnostics = {}
    health = payload.get("health")
    if not isinstance(health, dict):
        health = {}
    return {
        "artifact": "provider_diagnostics.json",
        "provider": str(payload.get("provider") or ""),
        "status": str(payload.get("status") or ""),
        "owner": str(payload.get("owner") or ""),
        "agent_owned": bool(payload.get("agent_owned", False)),
        "codetalk_callable": bool(payload.get("codetalk_callable", False)),
        "health_status": str(health.get("status") or "unknown"),
        "launch_kind": str(health.get("launch_kind") or ""),
        "used_fallback": bool(health.get("used_fallback", False)),
        "startup_probe_endpoint": str(diagnostics.get("startup_probe_endpoint") or ""),
        "prompt_transport": str(
            diagnostics.get("startup_probe_transport")
            or diagnostics.get("prompt_transport")
            or ""
        ),
        "mcp_credentials_owner": str(diagnostics.get("mcp_credentials_owner") or ""),
    }


def _command_resolution_result_summary(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    summary: dict[str, Any] = {
        "command_resolution_source": str(payload.get("source") or ""),
    }
    if "reason" in payload:
        summary["command_resolution_reason"] = str(payload.get("reason") or "")
    if "used_fallback" in payload:
        summary["command_resolution_used_fallback"] = bool(payload.get("used_fallback", False))
    if "launch_kind" in payload:
        summary["command_resolution_launch_kind"] = str(payload.get("launch_kind") or "")
    return {key: value for key, value in summary.items() if value not in {"", None}}


_SECRET_RE = re.compile(
    r"(?i)\b(api[-_]?key|token|access[-_]?token|secret|password)\s*=\s*[^\s]+"
)
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")


def _redact(text: str) -> str:
    value = _SECRET_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text or "")
    return _BEARER_RE.sub(r"\1<redacted>", value)


def _decode_subprocess_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
