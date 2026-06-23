from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
    except OSError:
        return []
    if not root.exists() or not root.is_dir():
        return []
    artifacts: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved != root and root not in resolved.parents:
            continue
        try:
            data = resolved.read_bytes()
        except OSError:
            continue
        relative_path = resolved.relative_to(root).as_posix()
        item: dict[str, Any] = {
            "relative_path": relative_path,
            "path": str(resolved),
            "kind": workbench_artifact_kind(relative_path),
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        preview = artifact_preview(resolved, data)
        if preview:
            item["preview"] = preview
        artifacts.append(item)
    return artifacts


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
        if name == "provider_diagnostics.json":
            return "agent_turn_provider_diagnostics"
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
    if name == "input_snapshot.json":
        return "input_snapshot"
    if name == "input_context.json":
        return "input_context"
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
    if name == "agent_run.json":
        return "agent_run"
    if name == "execution_input.json":
        return "agent_execution_input"
    if name == "provider_diagnostics.json":
        return "agent_provider_diagnostics"
    if name == "agent_run_lifecycle.json":
        return "agent_run_lifecycle"
    if name == "failure_recovery.json":
        return "agent_failure_recovery"
    if name.endswith(".json"):
        return "json"
    if name.endswith((".md", ".txt", ".patch", ".diff", ".log")):
        return "text"
    return "artifact"


def artifact_preview(path: Path, data: bytes, *, max_chars: int = 1200) -> str:
    if path.suffix.lower() not in {".json", ".md", ".txt", ".patch", ".diff", ".log"}:
        return ""
    text = data[: max_chars * 4].decode("utf-8", errors="replace")
    return text[:max_chars]
