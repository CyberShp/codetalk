from __future__ import annotations

import errno
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.external_agent_discovery import redact_agent_diagnostic_text

TEXT_ARTIFACT_SUFFIXES = {
    ".csv",
    ".diff",
    ".htm",
    ".html",
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".ndjson",
    ".patch",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

_RUNTIME_CACHE_DIRECTORY_NAMES = {
    ".runtime-tmp",
    "node-compile-cache",
}


def _is_runtime_cache_directory(name: str) -> bool:
    return (
        name in _RUNTIME_CACHE_DIRECTORY_NAMES
        or name.startswith(".runtime-tmp-")
        or name == ".runtime-codex-home"
        or name.startswith(".runtime-codex-home-")
    )


def _iter_manifest_files(root: Path):
    def handle_walk_error(error: OSError) -> None:
        if error.errno == errno.ENOENT:
            return None
        raise error

    for current, directory_names, file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
        onerror=handle_walk_error,
    ):
        current_path = Path(current)
        directory_names[:] = sorted(
            name
            for name in directory_names
            if not _is_runtime_cache_directory(name)
            and not (current_path / name).is_symlink()
        )
        for name in sorted(file_names):
            yield current_path / name


def _ignore_disappeared_path(error: OSError) -> bool:
    if error.errno == errno.ENOENT:
        return True
    raise error


def write_task_artifact_manifest(task_dir: Path, *, task_run_id: str) -> dict[str, Any]:
    artifacts = [
        item
        for item in build_task_artifact_manifest(task_dir)
        if item.get("relative_path") != "task_artifact_manifest.json"
    ]
    payload = {
        "task_run_id": task_run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "task_artifact_manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return payload


def build_task_artifact_manifest(task_dir: Path) -> list[dict[str, Any]]:
    try:
        root = task_dir.resolve()
    except OSError as error:
        if _ignore_disappeared_path(error):
            return []
    if not root.exists() or not root.is_dir():
        return []
    declared_deliverables = _declared_workflow_deliverable_paths(root)
    artifacts: list[dict[str, Any]] = []
    for path in _iter_manifest_files(root):
        if not path.is_file():
            continue
        try:
            unresolved_relative = path.relative_to(root)
        except ValueError:
            continue
        if any(_is_runtime_cache_directory(part) for part in unresolved_relative.parts[:-1]):
            continue
        try:
            resolved = path.resolve()
        except OSError as error:
            if _ignore_disappeared_path(error):
                continue
        if resolved != root and root not in resolved.parents:
            continue
        try:
            data = resolved.read_bytes()
        except OSError as error:
            if _ignore_disappeared_path(error):
                continue
        relative_path = resolved.relative_to(root).as_posix()
        item: dict[str, Any] = {
            "relative_path": relative_path,
            "path": str(resolved),
            "kind": workbench_artifact_kind(relative_path),
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        item["audience"] = workbench_artifact_audience(
            relative_path,
            kind=str(item["kind"]),
            declared_deliverables=declared_deliverables,
        )
        preview, preview_redacted = artifact_preview_with_redaction_status(
            resolved,
            data,
            max_chars=3200 if relative_path.endswith("execution_input.json") else 1200,
        )
        if preview:
            item["preview"] = preview
            item["preview_redacted"] = preview_redacted
        artifacts.append(item)
    return artifacts


def _declared_workflow_deliverable_paths(task_dir: Path) -> set[str]:
    declared: set[str] = set()
    for filename in ("workflow_outputs.json", "workflow_snapshot.json", "workflow_contract.json"):
        source = task_dir / filename
        if not source.is_file():
            continue
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except OSError as error:
            if _ignore_disappeared_path(error):
                continue
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        outputs = payload.get("outputs")
        if not isinstance(outputs, list):
            continue
        for output in outputs:
            if not isinstance(output, dict):
                continue
            for key in ("path", "artifact"):
                normalized = _safe_relative_artifact_path(output.get(key))
                if normalized:
                    declared.add(normalized)
            for companion in output.get("companion_artifacts") or []:
                normalized = _safe_relative_artifact_path(companion)
                if normalized:
                    declared.add(normalized)
    return declared


def _safe_relative_artifact_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    if not text or text.startswith("/"):
        return ""
    parts = [part for part in text.split("/") if part not in {"", "."}]
    if not parts or ".." in parts:
        return ""
    return "/".join(parts)


DELIVERABLE_ARTIFACT_NAMES = {
    "black_box_cases.json",
    "evidence_cards.json",
    "flow_delta.json",
    "flow_map.md",
    "impact_scope.json",
    "module_analysis.md",
    "mr_snapshot.json",
    "report.md",
    "risk_findings.json",
    "sfmea.json",
    "source_scope.json",
    "test_hooks.json",
    "test_plan.json",
    "test_recommendations.json",
}

DIAGNOSTIC_ARTIFACT_KINDS = {
    "agent_execution_input",
    "agent_failure_recovery",
    "agent_failure_retry_context",
    "agent_instructions",
    "agent_invocation",
    "agent_mcp_requests",
    "agent_output_contract",
    "agent_provider_diagnostics",
    "agent_raw_output",
    "agent_replay_plan",
    "agent_run",
    "agent_run_lifecycle",
    "agent_task_bundle",
    "agent_turn_execution_input",
    "agent_turn_execution_result",
    "agent_turn_output_contract",
    "agent_turn_provider_diagnostics",
    "agent_turn_raw_output",
    "agent_turn_replay_plan",
    "agent_turn_run",
    "agent_turn_source_slice_requests",
    "agent_turn_source_slices",
    "agent_turn_task_bundle",
    "black_box_generation_policy",
    "capability_manifest",
    "context_bundle",
    "context_discovery_decision",
    "degraded_retrieval",
    "evidence_consumption_trajectory",
    "evidence_validation",
    "memory_retrieval",
    "output_schemas",
    "provider_readiness",
    "provider_snapshot",
    "sandbox_policy",
    "semantic_import_outputs",
    "source_read_chain",
    "task_acceptance_audit",
    "task_bundle",
    "task_rerun_execution",
    "task_rerun_history",
    "task_rerun_plan",
    "workflow_contract",
    "workflow_execution",
    "workflow_output_materialization",
    "workflow_outputs",
    "test_activity_quality_audit",
    "verified_fact_ledger",
}


def workbench_artifact_audience(
    relative_path: str,
    *,
    kind: str | None = None,
    declared_deliverables: set[str] | None = None,
) -> str:
    name = relative_path.rsplit("/", 1)[-1]
    normalized_kind = kind or workbench_artifact_kind(relative_path)
    if relative_path.startswith("inputs/") or normalized_kind.startswith("input_"):
        return "input"
    declared = declared_deliverables or set()
    if declared:
        declared_names = {path.rsplit("/", 1)[-1] for path in declared}
        if relative_path in declared or name in declared_names:
            return "deliverable"
        if normalized_kind in DIAGNOSTIC_ARTIFACT_KINDS:
            return "diagnostic"
        return "support"
    if normalized_kind in DIAGNOSTIC_ARTIFACT_KINDS:
        return "diagnostic"
    if name in DELIVERABLE_ARTIFACT_NAMES:
        return "deliverable"
    return "support"


def workbench_artifact_kind(relative_path: str) -> str:
    name = relative_path.rsplit("/", 1)[-1]
    parts = relative_path.split("/")
    if "/turns/" in relative_path:
        if name == "execution_input.json":
            return "agent_turn_execution_input"
        if name == "task_bundle.json":
            return "agent_turn_task_bundle"
        if name == "raw_output.txt":
            return "agent_turn_raw_output"
        if name == "execution_result.json":
            return "agent_turn_execution_result"
        if name == "agent_replay_plan.json":
            return "agent_turn_replay_plan"
        if name == "provider_diagnostics.json":
            return "agent_turn_provider_diagnostics"
        if name == "agent_output_contract.json":
            return "agent_turn_output_contract"
        if name == "source_slice_requests.json":
            return "agent_turn_source_slice_requests"
        if name == "source_slices.json":
            return "agent_turn_source_slices"
        if name == "agent_run.json":
            return "agent_turn_run"
    if relative_path.endswith("/task_bundle.json"):
        return "agent_task_bundle"
    if name == "task_bundle.json":
        return "task_bundle"
    if name == "agent_instructions.json":
        return "agent_instructions"
    if name == "provider_snapshot.json":
        return "provider_snapshot"
    if name == "provider_readiness.json":
        return "provider_readiness"
    if name == "sandbox_policy.json":
        return "sandbox_policy"
    if name == "input_snapshot.json":
        return "input_snapshot"
    if name == "input_context.json":
        return "input_context"
    if name == "input_materials.json":
        return "input_materials"
    if parts and parts[0] == "inputs":
        if name == "file_metadata.json":
            return "input_file_metadata"
        if name == "file_set_manifest.json":
            return "input_file_set_manifest"
        if name == "parsed_text.txt":
            return "input_parsed_text"
        if name == "chunks.json":
            return "input_chunks"
        if "original" in parts:
            return "input_original_file"
        return "input_artifact"
    if name == "workflow_contract.json":
        return "workflow_contract"
    if name == "agent_mcp_requests.json":
        return "agent_mcp_requests"
    if name == "context_discovery_decision.json":
        return "context_discovery_decision"
    if name == "context_bundle.json":
        return "context_bundle"
    if name == "output_schemas_by_step.json":
        return "output_schemas"
    if name == "semantic_import_outputs_by_step.json":
        return "semantic_import_outputs"
    if name == "black_box_generation_policy.json":
        return "black_box_generation_policy"
    if name == "memory_retrieval.json":
        return "memory_retrieval"
    if name == "source_read_chain.json":
        return "source_read_chain"
    if name == "evidence_consumption_trajectory.json":
        return "evidence_consumption_trajectory"
    if name == "degraded_retrieval.json":
        return "degraded_retrieval"
    if name == "workflow_outputs.json":
        return "workflow_outputs"
    if name == "workflow_output_materialization.json":
        return "workflow_output_materialization"
    if name == "semantic_output_import.json":
        return "semantic_output_import"
    if name == "workflow_execution.json":
        return "workflow_execution"
    if name == "task_artifact_manifest.json":
        return "task_artifact_manifest"
    if name == "task_acceptance_audit.json":
        return "task_acceptance_audit"
    if name == "test_activity_quality_audit.json":
        return "test_activity_quality_audit"
    if name == "verified_fact_ledger.json":
        return "verified_fact_ledger"
    if name == "task_rerun_plan.json":
        return "task_rerun_plan"
    if name == "task_rerun_execution.json":
        return "task_rerun_execution"
    if name == "task_rerun_history.json":
        return "task_rerun_history"
    if name == "evidence_validation.json":
        return "evidence_validation"
    if name == "raw_output.txt":
        return "agent_raw_output"
    if name == "agent_invocation.json":
        return "agent_invocation"
    if name == "capability_manifest.json":
        return "capability_manifest"
    if name == "agent_run.json":
        return "agent_run"
    if name == "execution_input.json":
        return "agent_execution_input"
    if name == "provider_diagnostics.json":
        return "agent_provider_diagnostics"
    if name == "agent_output_contract.json":
        return "agent_output_contract"
    if name == "agent_run_lifecycle.json":
        return "agent_run_lifecycle"
    if name == "agent_replay_plan.json":
        return "agent_replay_plan"
    if name == "failure_recovery.json":
        return "agent_failure_recovery"
    if name == "failure_retry_context.json":
        return "agent_failure_retry_context"
    if name.endswith(".json"):
        return "json"
    if name.endswith((".md", ".txt", ".patch", ".diff", ".log")):
        return "text"
    return "artifact"


def artifact_preview(path: Path, data: bytes, *, max_chars: int = 1200) -> str:
    return artifact_preview_with_redaction_status(path, data, max_chars=max_chars)[0]


def artifact_preview_with_redaction_status(path: Path, data: bytes, *, max_chars: int = 1200) -> tuple[str, bool]:
    if path.suffix.lower() not in TEXT_ARTIFACT_SUFFIXES:
        return "", False
    text = data.decode("utf-8", errors="replace")
    redacted = redact_agent_diagnostic_text(text)
    return truncate_redacted_text(redacted, max_chars), redacted != text


def truncate_redacted_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    marker = "<redacted>"
    for prefix_len in range(1, len(marker)):
        if truncated.endswith(marker[:prefix_len]):
            return truncated[: -prefix_len] + marker
    return truncated
