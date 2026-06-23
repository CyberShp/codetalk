"""Prepare reproducible workbench task runs from workflow definitions."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.agent_run_harness import AgentRunHarness
from app.services.evidence_memory import EvidenceMemoryStore
from app.services.external_agent_discovery import (
    check_provider_health,
    external_agent_provider_capabilities,
    external_agent_provider_spec,
    redact_agent_diagnostic_text,
    split_agent_command,
)
from app.services.test_semantic_library import TestSemanticLibraryStore
from app.services.workbench_artifact_manifest import write_task_artifact_manifest
from app.services.workbench_input_ingest import ingest_workbench_inputs
from app.services.workflow_dsl import WorkflowStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class PreparedWorkbenchTaskRun:
    task_run_id: str
    workflow_id: str
    workspace_id: str
    repo_path: str
    artifact_dir: str
    workflow_snapshot: dict[str, Any]
    input_snapshot: dict[str, Any]
    task_bundle: dict[str, Any]
    agent_runs: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=_now)


class WorkbenchTaskRunPreparer:
    """Freezes workflow/input state and creates Agent run envelopes."""

    def __init__(
        self,
        *,
        artifact_root: str | Path,
        workflow_store: WorkflowStore,
        evidence_memory: EvidenceMemoryStore | None = None,
        semantic_library: TestSemanticLibraryStore | None = None,
    ) -> None:
        self.artifact_root = Path(artifact_root)
        self.workflow_store = workflow_store
        self.evidence_memory = evidence_memory
        self.semantic_library = semantic_library

    def prepare(
        self,
        *,
        workflow_id: str,
        workspace_id: str,
        repo_path: str,
        inputs: dict[str, Any],
        provider_override: str | None = None,
    ) -> PreparedWorkbenchTaskRun:
        workflow_snapshot = self.workflow_store.freeze_workflow_snapshot(workflow_id)
        task_run_id = _new_id("task_run")
        artifact_dir = self.artifact_root / task_run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)

        required_artifacts_by_step = {
            str(step.get("id")): [str(item) for item in step.get("required_artifacts") or []]
            for step in workflow_snapshot.get("steps") or []
            if isinstance(step, dict) and step.get("type") == "agent_task"
        }
        output_schemas_by_step = build_output_schemas_by_step(workflow_snapshot)
        semantic_import_outputs_by_step = build_semantic_import_outputs_by_step(
            workflow_snapshot
        )
        input_snapshot = ingest_workbench_inputs(
            input_definitions=[
                item for item in workflow_snapshot.get("inputs") or []
                if isinstance(item, dict)
            ],
            inputs=dict(inputs or {}),
            artifact_dir=artifact_dir,
        )
        context_bundle = build_workbench_context_bundle(
            workspace_id=workspace_id,
            repo_path=repo_path,
            input_snapshot=input_snapshot,
            evidence_memory=self.evidence_memory,
            semantic_library=self.semantic_library,
        )
        agent_instructions = collect_agent_instructions(
            repo_path=repo_path,
            input_snapshot=input_snapshot,
        )
        input_context = build_input_context(input_snapshot)
        provider_snapshot = build_agent_provider_snapshot(
            workflow_snapshot=workflow_snapshot,
            provider_override=provider_override,
        )
        provider_readiness = build_provider_readiness_report(
            repo_path=repo_path,
            provider_snapshot=provider_snapshot,
            deployment_evidence=[
                item for item in context_bundle.get("deployment_evidence") or []
                if isinstance(item, dict)
            ],
        )
        workflow_contract = build_workflow_contract(
            workflow_snapshot=workflow_snapshot,
            provider_snapshot=provider_snapshot,
        )
        agent_mcp_requests = build_agent_mcp_requests(
            workflow_snapshot=workflow_snapshot,
            input_snapshot=input_snapshot,
            workflow_contract=workflow_contract,
        )
        context_discovery_decision = build_context_discovery_decision(
            agent_instructions=agent_instructions,
            provider_snapshot=provider_snapshot,
        )
        context_artifacts = build_context_artifact_payloads(
            context_bundle=context_bundle,
            context_discovery_decision=context_discovery_decision,
            evidence_memory_configured=self.evidence_memory is not None,
            semantic_library_configured=self.semantic_library is not None,
        )
        black_box_generation_policy = build_black_box_generation_policy(
            context_bundle=context_bundle,
        )
        task_bundle = {
            "task_run_id": task_run_id,
            "workflow_id": workflow_id,
            "workspace_id": workspace_id,
            "repo_path": repo_path,
            "inputs": input_snapshot,
            "input_context": input_context,
            "workflow_contract": workflow_contract,
            "agent_mcp_requests": agent_mcp_requests,
            "agent_instructions": agent_instructions,
            "provider_snapshot": provider_snapshot,
            "provider_readiness": provider_readiness,
            "context_discovery_decision": context_discovery_decision,
            "context_bundle": context_bundle,
            "memory_retrieval": context_artifacts["memory_retrieval"],
            "source_read_chain": context_artifacts["source_read_chain"],
            "evidence_consumption_trajectory": context_artifacts["evidence_consumption_trajectory"],
            "degraded_retrieval": context_artifacts["degraded_retrieval"],
            "black_box_generation_policy": black_box_generation_policy,
            "required_artifacts_by_step": required_artifacts_by_step,
            "output_schemas_by_step": output_schemas_by_step,
            "semantic_import_outputs_by_step": semantic_import_outputs_by_step,
            "created_at": _now(),
        }

        agent_runs: list[dict[str, Any]] = []
        for step in workflow_snapshot.get("steps") or []:
            if not isinstance(step, dict) or step.get("type") != "agent_task":
                continue
            step_id = str(step.get("id") or f"step_{len(agent_runs) + 1}")
            provider = str(provider_override or step.get("provider") or "claude-code")
            spec = external_agent_provider_spec(provider)
            command = split_agent_command(spec.command) if spec and spec.command else [provider]
            step_bundle = {
                **task_bundle,
                "step_id": step_id,
                "goal": step.get("goal") or "",
                "required_artifacts": required_artifacts_by_step.get(step_id, []),
                "expected_output_schemas": output_schemas_by_step.get(step_id, []),
                "expected_semantic_outputs": semantic_import_outputs_by_step.get(step_id, []),
                "mcp_profile": step.get("mcp_profile") or "",
            }
            agent_run = AgentRunHarness(artifact_dir / "agent_runs" / step_id).create_run(
                provider=provider,
                command=command,
                cwd=repo_path,
                workflow_snapshot=workflow_snapshot,
                task_bundle=step_bundle,
                mcp_profile=str(step.get("mcp_profile") or ""),
                run_id=f"{task_run_id}_{step_id}",
            )
            agent_runs.append({
                "step_id": step_id,
                "run_id": agent_run.run_id,
                "provider": provider,
                "artifact_dir": agent_run.artifact_dir,
                "mcp_profile": agent_run.mcp_profile,
                "required_artifacts": required_artifacts_by_step.get(step_id, []),
            })

        result = PreparedWorkbenchTaskRun(
            task_run_id=task_run_id,
            workflow_id=workflow_id,
            workspace_id=workspace_id,
            repo_path=repo_path,
            artifact_dir=str(artifact_dir),
            workflow_snapshot=workflow_snapshot,
            input_snapshot=input_snapshot,
            task_bundle=task_bundle,
            agent_runs=agent_runs,
        )
        _write_json(artifact_dir / "task_run.json", asdict(result))
        _write_json(artifact_dir / "workflow_snapshot.json", workflow_snapshot)
        _write_json(artifact_dir / "workflow_contract.json", workflow_contract)
        _write_json(artifact_dir / "agent_mcp_requests.json", agent_mcp_requests)
        _write_json(artifact_dir / "input_snapshot.json", input_snapshot)
        _write_json(artifact_dir / "input_context.json", input_context)
        _write_json(artifact_dir / "agent_instructions.json", agent_instructions)
        _write_json(artifact_dir / "provider_snapshot.json", provider_snapshot)
        _write_json(artifact_dir / "provider_readiness.json", provider_readiness)
        _write_json(artifact_dir / "context_discovery_decision.json", context_discovery_decision)
        _write_json(artifact_dir / "context_bundle.json", context_bundle)
        _write_json(artifact_dir / "output_schemas_by_step.json", output_schemas_by_step)
        _write_json(
            artifact_dir / "semantic_import_outputs_by_step.json",
            semantic_import_outputs_by_step,
        )
        _write_json(artifact_dir / "memory_retrieval.json", context_artifacts["memory_retrieval"])
        _write_json(artifact_dir / "source_read_chain.json", context_artifacts["source_read_chain"])
        _write_json(
            artifact_dir / "evidence_consumption_trajectory.json",
            context_artifacts["evidence_consumption_trajectory"],
        )
        _write_json(artifact_dir / "degraded_retrieval.json", context_artifacts["degraded_retrieval"])
        _write_json(artifact_dir / "black_box_generation_policy.json", black_box_generation_policy)
        _write_json(artifact_dir / "task_bundle.json", task_bundle)
        write_task_artifact_manifest(artifact_dir, task_run_id=task_run_id)
        return result


class WorkbenchTaskRunStore:
    """Loads prepared task-run artifacts back into the Workbench."""

    def __init__(self, artifact_root: str | Path) -> None:
        self.artifact_root = Path(artifact_root)

    def load(self, task_run_id: str) -> PreparedWorkbenchTaskRun:
        task_run_dir = self.artifact_root / _safe_segment(task_run_id)
        payload = _read_json(task_run_dir / "task_run.json")
        if not isinstance(payload, dict):
            raise KeyError(task_run_id)
        return _prepared_task_run_from_payload(payload)

    def list(
        self,
        *,
        workspace_id: str | None = None,
        limit: int = 50,
    ) -> list[PreparedWorkbenchTaskRun]:
        if not self.artifact_root.exists():
            return []
        runs: list[PreparedWorkbenchTaskRun] = []
        for path in self.artifact_root.iterdir():
            if not path.is_dir():
                continue
            payload = _read_json(path / "task_run.json")
            if not isinstance(payload, dict):
                continue
            if workspace_id and payload.get("workspace_id") != workspace_id:
                continue
            try:
                runs.append(_prepared_task_run_from_payload(payload))
            except (KeyError, TypeError, ValueError):
                continue
        runs.sort(key=lambda item: item.created_at, reverse=True)
        return runs[: max(1, int(limit))]


def build_workbench_context_bundle(
    *,
    workspace_id: str,
    repo_path: str = "",
    input_snapshot: dict[str, Any],
    evidence_memory: EvidenceMemoryStore | None = None,
    semantic_library: TestSemanticLibraryStore | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    query = _context_query_from_inputs(input_snapshot)
    evidence = []
    deployment_evidence = []
    semantic_cases = []
    if query and evidence_memory is not None:
        evidence = [
            _evidence_item_payload(
                item,
                source_slices=evidence_memory.list_source_slices(item.evidence_id),
                repo_path=repo_path,
            )
            for item in evidence_memory.search_analysis_memory(
                query,
                workspace_id=workspace_id,
                limit=limit,
            )
        ]
        deployment_evidence = [
            _evidence_item_payload(item, source_slices=[], repo_path=repo_path)
            for item in evidence_memory.list_evidence_items(
                workspace_id="codetalk-deployment",
                kinds=("deployment_probe", "provider_task_probe"),
                sources=("deployment_probe",),
                limit=limit,
            )
        ]
    if query and semantic_library is not None:
        semantic_cases = [
            _semantic_case_payload(item)
            for item in semantic_library.retrieve(
                query=query,
                limit=limit,
            )
        ]
    return {
        "query": query,
        "evidence": evidence,
        "deployment_evidence": deployment_evidence,
        "semantic_cases": semantic_cases,
        "limits": {
            "evidence": limit,
            "semantic_cases": limit,
        },
    }


def build_input_context(input_snapshot: dict[str, Any], *, preview_chars: int = 4000) -> dict[str, Any]:
    inputs: list[dict[str, Any]] = []
    for input_id, value in input_snapshot.items():
        if not isinstance(value, dict):
            continue
        kind = str(value.get("kind") or "")
        if kind == "file":
            inputs.append(
                _input_context_file(
                    input_id=str(input_id),
                    payload=value,
                    preview_chars=preview_chars,
                )
            )
        elif kind == "file_set":
            files = [
                _input_context_file(
                    input_id=str(file_payload.get("input_id") or f"{input_id}_{index + 1}"),
                    payload=file_payload,
                    preview_chars=preview_chars,
                )
                for index, file_payload in enumerate(value.get("files") or [])
                if isinstance(file_payload, dict)
            ]
            inputs.append({
                "input_id": str(input_id),
                "kind": "file_set",
                "count": int(value.get("count") or len(files)),
                "manifest_path": str(value.get("manifest_path") or ""),
                "files": files,
            })
    return {
        "inputs": inputs,
        "file_count": sum(
            len(item.get("files") or [item])
            for item in inputs
            if isinstance(item, dict)
        ),
        "preview_chars_per_file": preview_chars,
    }


def _input_context_file(
    *,
    input_id: str,
    payload: dict[str, Any],
    preview_chars: int,
) -> dict[str, Any]:
    parsed_text_path = str(payload.get("parsed_text_path") or "")
    chunks_path = str(payload.get("chunks_path") or "")
    chunks = _read_json(Path(chunks_path)) if chunks_path else None
    chunk_count = len(chunks) if isinstance(chunks, list) else 0
    return {
        "input_id": input_id,
        "kind": "file",
        "filename": str(payload.get("filename") or ""),
        "suffix": str(payload.get("suffix") or ""),
        "size_bytes": int(payload.get("size_bytes") or 0),
        "sha256": str(payload.get("sha256") or ""),
        "original_path": str(payload.get("original_path") or ""),
        "copied_path": str(payload.get("copied_path") or ""),
        "parsed_text_path": parsed_text_path,
        "chunks_path": chunks_path,
        "chunk_count": chunk_count,
        "text_preview": _read_text_preview(parsed_text_path, preview_chars),
        "text_truncated": _text_file_exceeds(parsed_text_path, preview_chars),
        "parse_warnings": [str(item) for item in payload.get("parse_warnings") or []],
    }


def _read_text_preview(path_text: str, max_chars: int) -> str:
    if not path_text:
        return ""
    try:
        return Path(path_text).read_text(encoding="utf-8", errors="replace")[:max_chars]
    except OSError:
        return ""


def _text_file_exceeds(path_text: str, max_chars: int) -> bool:
    if not path_text:
        return False
    try:
        return len(Path(path_text).read_text(encoding="utf-8", errors="replace")) > max_chars
    except OSError:
        return False


def build_output_schemas_by_step(workflow_snapshot: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    schemas: dict[str, list[dict[str, Any]]] = {}
    for output in workflow_snapshot.get("outputs") or []:
        if not isinstance(output, dict):
            continue
        schema = output.get("schema") or output.get("json_schema")
        if not isinstance(schema, dict):
            continue
        source_step = str(output.get("from") or output.get("source") or "").strip()
        if not source_step:
            continue
        schemas.setdefault(source_step, []).append({
            "output_id": str(output.get("id") or ""),
            "artifact": str(output.get("artifact") or output.get("path") or ""),
            "type": str(output.get("type") or ""),
            "schema": dict(schema),
        })
    return schemas


def build_semantic_import_outputs_by_step(
    workflow_snapshot: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    outputs: dict[str, list[dict[str, Any]]] = {}
    for output in workflow_snapshot.get("outputs") or []:
        if not isinstance(output, dict):
            continue
        semantic_import = output.get("semantic_import")
        if semantic_import is True:
            semantic_payload: dict[str, Any] = {"enabled": True}
        elif isinstance(semantic_import, dict):
            if semantic_import.get("enabled", True) is False:
                continue
            semantic_payload = {
                "enabled": True,
                **dict(semantic_import),
            }
        else:
            continue
        source_step = str(output.get("from") or output.get("source") or "").strip()
        if not source_step:
            continue
        outputs.setdefault(source_step, []).append({
            "output_id": str(output.get("id") or ""),
            "artifact": str(output.get("artifact") or output.get("path") or ""),
            "type": str(output.get("type") or ""),
            "semantic_import": semantic_payload,
        })
    return outputs


def build_workflow_contract(
    *,
    workflow_snapshot: dict[str, Any],
    provider_snapshot: dict[str, Any],
) -> dict[str, Any]:
    steps = provider_snapshot.get("steps") or {}
    providers = provider_snapshot.get("providers") or {}
    agent_steps = [
        _workflow_contract_agent_step(
            step,
            provider_payload=_workflow_contract_provider_payload(
                step,
                providers=providers,
                steps=steps,
            ),
            step_payload=steps.get(str(step.get("id") or ""), {}) if isinstance(steps, dict) else {},
        )
        for step in workflow_snapshot.get("steps") or []
        if isinstance(step, dict) and step.get("type") == "agent_task"
    ]
    return {
        "workflow_id": str(workflow_snapshot.get("id") or ""),
        "workflow_name": str(workflow_snapshot.get("name") or ""),
        "version": workflow_snapshot.get("version", 1),
        "inputs": [
            _workflow_contract_input(item)
            for item in workflow_snapshot.get("inputs") or []
            if isinstance(item, dict)
        ],
        "agent_mcp_inputs": _workflow_contract_agent_mcp_inputs(
            workflow_snapshot=workflow_snapshot,
            agent_steps=agent_steps,
        ),
        "agent_steps": agent_steps,
        "outputs": [
            _workflow_contract_output(item)
            for item in workflow_snapshot.get("outputs") or []
            if isinstance(item, dict)
        ],
    }


def _workflow_contract_input(item: dict[str, Any]) -> dict[str, Any]:
    resolver = str(item.get("resolver") or "")
    schema = item.get("schema") or item.get("json_schema")
    schema_required = []
    schema_type = ""
    if isinstance(schema, dict):
        schema_required = [str(value) for value in schema.get("required") or []]
        schema_type = str(schema.get("type") or "")
    payload = {
        "id": str(item.get("id") or ""),
        "type": str(item.get("type") or ""),
        "required": bool(item.get("required", False)),
        "role": str(item.get("role") or ""),
        "resolver": resolver,
        "agent_owned": resolver == "agent_mcp",
    }
    if isinstance(schema, dict):
        payload["has_schema"] = True
        payload["schema_type"] = schema_type
        payload["schema_required"] = schema_required
        payload["schema"] = dict(schema)
    return payload


def _workflow_contract_agent_mcp_inputs(
    *,
    workflow_snapshot: dict[str, Any],
    agent_steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    mcp_steps = [
        step for step in agent_steps
        if bool(step.get("agent_owned_mcp"))
    ]
    if not mcp_steps:
        return []
    requests: list[dict[str, Any]] = []
    for item in workflow_snapshot.get("inputs") or []:
        if not isinstance(item, dict) or str(item.get("resolver") or "") != "agent_mcp":
            continue
        required_artifacts_by_step = {
            str(step.get("id") or ""): [str(value) for value in step.get("required_artifacts") or []]
            for step in mcp_steps
            if str(step.get("id") or "")
        }
        requests.append({
            "input_id": str(item.get("id") or ""),
            "input_type": str(item.get("type") or ""),
            "role": str(item.get("role") or ""),
            "resolver": "agent_mcp",
            "credential_owner": "agent_cli",
            "codetalk_fetch_allowed": False,
            "agent_step_ids": [str(step.get("id") or "") for step in mcp_steps if str(step.get("id") or "")],
            "mcp_profiles": _unique_strings(
                str(step.get("mcp_profile") or "")
                for step in mcp_steps
                if str(step.get("mcp_profile") or "")
            ),
            "required_artifacts_by_step": required_artifacts_by_step,
            "validation_rule": (
                "Agent CLI must fetch this input through its own MCP credentials and return "
                "required artifacts; CodeTalk validates artifacts instead of fetching the remote resource."
            ),
        })
    return requests


def build_agent_mcp_requests(
    *,
    workflow_snapshot: dict[str, Any],
    input_snapshot: dict[str, Any],
    workflow_contract: dict[str, Any],
) -> list[dict[str, Any]]:
    del workflow_snapshot
    requests: list[dict[str, Any]] = []
    for contract in workflow_contract.get("agent_mcp_inputs") or []:
        if not isinstance(contract, dict):
            continue
        input_id = str(contract.get("input_id") or "")
        if not input_id or input_id not in input_snapshot:
            continue
        required_artifacts_by_step = {
            str(step_id): [str(value) for value in values or []]
            for step_id, values in (contract.get("required_artifacts_by_step") or {}).items()
        }
        required_artifacts = _unique_strings(
            artifact
            for values in required_artifacts_by_step.values()
            for artifact in values
        )
        requests.append({
            "input_id": input_id,
            "input_type": str(contract.get("input_type") or ""),
            "value": input_snapshot[input_id],
            "resolver": "agent_mcp",
            "credential_owner": "agent_cli",
            "codetalk_fetch_allowed": False,
            "agent_step_ids": [str(value) for value in contract.get("agent_step_ids") or []],
            "mcp_profiles": [str(value) for value in contract.get("mcp_profiles") or []],
            "required_artifacts_by_step": required_artifacts_by_step,
            "artifact_validation": {
                "strategy": "required_artifacts",
                "codetalk_remote_fetch": False,
                "required_artifacts": required_artifacts,
            },
        })
    return requests


def _workflow_contract_provider_payload(
    step: dict[str, Any],
    *,
    providers: Any,
    steps: Any,
) -> Any:
    if not isinstance(providers, dict):
        return {}
    step_id = str(step.get("id") or "")
    step_payload = steps.get(step_id, {}) if isinstance(steps, dict) else {}
    provider = (
        str(step_payload.get("provider") or "")
        if isinstance(step_payload, dict)
        else ""
    )
    if not provider:
        provider = str(step.get("provider") or "claude-code")
    return providers.get(provider, {})


def _workflow_contract_agent_step(
    step: dict[str, Any],
    *,
    provider_payload: Any,
    step_payload: Any,
) -> dict[str, Any]:
    provider = str(
        (step_payload or {}).get("provider")
        if isinstance(step_payload, dict)
        else step.get("provider") or "claude-code"
    )
    capabilities = (
        provider_payload.get("capabilities")
        if isinstance(provider_payload, dict) and isinstance(provider_payload.get("capabilities"), dict)
        else {}
    )
    mcp_profile = str(step.get("mcp_profile") or "")
    supports_mcp = bool(capabilities.get("supports_mcp"))
    return {
        "id": str(step.get("id") or ""),
        "provider": provider,
        "mcp_profile": mcp_profile,
        "goal": str(step.get("goal") or ""),
        "required_artifacts": [str(item) for item in step.get("required_artifacts") or []],
        "prompt_transport": str(capabilities.get("prompt_transport") or ""),
        "supports_mcp": supports_mcp,
        "mcp_profiles": list(capabilities.get("mcp_profiles") or []),
        "agent_owned_mcp": bool(mcp_profile or supports_mcp),
    }


def _workflow_contract_output(item: dict[str, Any]) -> dict[str, Any]:
    schema = item.get("schema") or item.get("json_schema")
    schema_required = []
    schema_type = ""
    if isinstance(schema, dict):
        schema_required = [str(value) for value in schema.get("required") or []]
        schema_type = str(schema.get("type") or "")
    payload = {
        "id": str(item.get("id") or ""),
        "type": str(item.get("type") or ""),
        "from": str(item.get("from") or item.get("source") or ""),
        "artifact": str(item.get("artifact") or item.get("path") or ""),
        "has_schema": isinstance(schema, dict),
        "schema_type": schema_type,
        "schema_required": schema_required,
    }
    semantic_import = item.get("semantic_import")
    if semantic_import is True:
        payload["semantic_import"] = {"enabled": True}
    elif isinstance(semantic_import, dict) and semantic_import.get("enabled", True) is not False:
        payload["semantic_import"] = {"enabled": True, **dict(semantic_import)}
    return payload


def _unique_strings(values: Any) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value)
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique


def build_agent_provider_snapshot(
    *,
    workflow_snapshot: dict[str, Any],
    provider_override: str | None = None,
) -> dict[str, Any]:
    providers: dict[str, dict[str, Any]] = {}
    steps: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for step in workflow_snapshot.get("steps") or []:
        if not isinstance(step, dict) or step.get("type") != "agent_task":
            continue
        step_id = str(step.get("id") or f"step_{len(steps) + 1}")
        provider = str(provider_override or step.get("provider") or "claude-code")
        steps[step_id] = {
            "provider": provider,
            "mcp_profile": str(step.get("mcp_profile") or ""),
            "provider_override": bool(provider_override),
        }
        if provider in providers:
            continue
        spec = external_agent_provider_spec(provider)
        if spec is None:
            providers[provider] = {
                "provider": provider,
                "status": "unknown_provider",
                "owner": "agent_cli",
                "codetalk_callable": False,
                "agent_owned": True,
                "command": [provider],
                "fallback_commands": [],
                "capabilities": {},
                "prompt_transport": "",
                "credential_boundary": (
                    "Provider is not configured; CodeTalk cannot launch it or validate its capability claims."
                ),
                "diagnostics": _unknown_agent_cli_provider_diagnostics(provider),
            }
            warnings.append(f"{provider}: provider is not configured")
            continue
        providers[provider] = {
            "provider": provider,
            "status": "configured" if spec.command else "missing_command",
            "owner": "agent_cli",
            "codetalk_callable": False,
            "agent_owned": True,
            "display_name": spec.display_name or provider,
            "command": split_agent_command(spec.command) if spec.command else [],
            "fallback_commands": [
                split_agent_command(command)
                for command in spec.fallback_commands
                if command
            ],
            "readonly_args": list(spec.readonly_args),
            "command_hint_env": spec.command_hint_env,
            "prompt_transport": spec.prompt_transport,
            "capabilities": external_agent_provider_capabilities(provider),
            "credential_boundary": (
                "Agent CLI owns its own MCP credentials and remote access; CodeTalk only "
                "passes task bundles and validates returned artifacts."
            ),
            "diagnostics": build_agent_cli_provider_diagnostics(provider, spec),
        }
        if not spec.command:
            warnings.append(f"{provider}: command is not configured")
    return {
        "created_at": _now(),
        "providers": providers,
        "codetalk_providers": build_codetalk_provider_snapshot(),
        "steps": steps,
        "warnings": warnings,
    }


def build_provider_readiness_report(
    *,
    repo_path: str,
    provider_snapshot: dict[str, Any],
    deployment_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    repo = _repo_readiness(repo_path)
    codetalk_providers = {
        provider: _codetalk_readiness_payload(provider, payload, repo_path=repo_path)
        for provider, payload in (provider_snapshot.get("codetalk_providers") or {}).items()
        if isinstance(payload, dict)
    }
    deployment_by_provider = _deployment_evidence_by_provider(deployment_evidence or [])
    agent_cli_providers = {
        provider: _agent_cli_readiness_payload(
            provider,
            payload,
            deployment_evidence=deployment_by_provider.get(provider),
        )
        for provider, payload in (provider_snapshot.get("providers") or {}).items()
        if isinstance(payload, dict)
    }
    blocking_reasons: list[str] = []
    warnings: list[str] = []
    if repo["status"] != "available":
        blocking_reasons.append("repo_path_missing")
    for provider, payload in codetalk_providers.items():
        if payload.get("status") in {"missing_config", "unavailable", "error"}:
            warnings.append(f"codetalk_provider_unavailable:{provider}")
    for provider, payload in agent_cli_providers.items():
        if payload.get("status") in {"unavailable", "missing_command", "unknown_provider", "error"}:
            warnings.append(f"agent_cli_unavailable:{provider}")
        if payload.get("deployment_evidence_conflict"):
            warnings.append(f"agent_cli_conflicts_with_deployment_probe:{provider}")
    summary_status = "blocked" if blocking_reasons else "degraded" if warnings else "ready"
    return {
        "created_at": _now(),
        "repo": repo,
        "codetalk_providers": codetalk_providers,
        "agent_cli_providers": agent_cli_providers,
        "summary": {
            "status": summary_status,
            "blocking_reasons": blocking_reasons,
            "warnings": warnings,
            "non_blocking_policy": (
                "Unavailable GitNexus, CGC, fast-context, or Agent CLI providers are recorded "
                "as degraded; CodeTalk continues with any remaining local, memory, semantic, "
                "and validated artifact paths."
            ),
        },
    }


def _repo_readiness(repo_path: str) -> dict[str, Any]:
    path = Path(str(repo_path or ""))
    exists = bool(repo_path and path.exists())
    is_dir = exists and path.is_dir()
    git_dir = path / ".git" if is_dir else Path()
    return {
        "path": str(repo_path or ""),
        "status": "available" if is_dir else "missing",
        "exists": exists,
        "is_dir": is_dir,
        "git_metadata_present": bool(is_dir and git_dir.exists()),
        "local_search_available": is_dir,
        "reason": "" if is_dir else "repo path does not exist or is not a directory",
    }


def _codetalk_readiness_payload(
    provider: str,
    payload: dict[str, Any],
    *,
    repo_path: str,
) -> dict[str, Any]:
    diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    return {
        "provider": provider,
        "owner": str(payload.get("owner") or "codetalk"),
        "status": str(payload.get("status") or "unknown"),
        "codetalk_callable": bool(payload.get("codetalk_callable", False)),
        "non_blocking": bool(payload.get("non_blocking", True)),
        "startup_probe_endpoint": str(diagnostics.get("startup_probe_endpoint") or ""),
        "health_endpoint": str(diagnostics.get("health_endpoint") or ""),
        "repo_path": repo_path,
        "unavailable_behavior": str(payload.get("unavailable_behavior") or ""),
        "next_check": _codetalk_provider_next_check(provider, diagnostics),
    }


def _codetalk_provider_next_check(provider: str, diagnostics: dict[str, Any]) -> str:
    endpoint = str(diagnostics.get("startup_probe_endpoint") or "")
    if endpoint:
        return f"POST {endpoint}?repo_path=<repo_path>"
    if provider == "local-search":
        return "Verify repo.path exists and is readable by the CodeTalk backend process."
    return "No startup probe is available for this provider."


def _agent_cli_readiness_payload(
    provider: str,
    payload: dict[str, Any],
    *,
    deployment_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    resolution = (
        diagnostics.get("command_resolution")
        if isinstance(diagnostics.get("command_resolution"), dict)
        else {}
    )
    status = str(resolution.get("status") or payload.get("status") or "unknown")
    readiness = {
        "provider": provider,
        "owner": "agent_cli",
        "status": status,
        "configured_command": str(resolution.get("configured_command") or ""),
        "command": str(resolution.get("command") or ""),
        "used_fallback": bool(resolution.get("used_fallback", False)),
        "reason": str(resolution.get("reason") or ""),
        "attempt_count": int(resolution.get("attempt_count") or 0),
        "startup_probe_endpoint": str(diagnostics.get("startup_probe_endpoint") or ""),
        "health_endpoint": str(diagnostics.get("health_endpoint") or ""),
        "manual_probe_command": str(diagnostics.get("manual_probe_command") or ""),
        "credential_boundary": str(payload.get("credential_boundary") or ""),
        "unavailable_behavior": str(payload.get("unavailable_behavior") or ""),
    }
    if deployment_evidence:
        readiness["deployment_evidence"] = deployment_evidence
        readiness["deployment_evidence_conflict"] = _deployment_evidence_conflicts_with_status(
            status=status,
            deployment_evidence=deployment_evidence,
        )
    return readiness


def _deployment_evidence_by_provider(
    deployment_evidence: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in deployment_evidence:
        if not isinstance(item, dict):
            continue
        provider = _provider_from_deployment_evidence(item)
        if not provider or provider in result:
            continue
        result[provider] = _deployment_evidence_summary(item)
    return result


def _provider_from_deployment_evidence(item: dict[str, Any]) -> str:
    provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
    provider = str(provenance.get("provider") or item.get("symbol") or "").strip()
    if provider:
        return provider
    subject_key = str(item.get("subject_key") or "").strip()
    if ":" in subject_key:
        return subject_key.split(":", 1)[0].strip()
    return ""


def _deployment_evidence_summary(item: dict[str, Any]) -> dict[str, Any]:
    provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
    return {
        "provider": _provider_from_deployment_evidence(item),
        "evidence_id": str(item.get("evidence_id") or ""),
        "kind": str(item.get("kind") or ""),
        "subject_key": str(item.get("subject_key") or ""),
        "evidence_status": str(item.get("status") or ""),
        "evidence_source": str(item.get("source") or ""),
        "probe_id": str(provenance.get("probe_id") or ""),
        "task_probe_status": str(provenance.get("task_probe_status") or ""),
        "path": str(item.get("path") or ""),
        "reason": str(item.get("reason") or ""),
    }


def _deployment_evidence_conflicts_with_status(
    *,
    status: str,
    deployment_evidence: dict[str, Any],
) -> bool:
    if status not in {"unavailable", "missing_command", "unknown_provider", "error"}:
        return False
    return (
        deployment_evidence.get("task_probe_status") == "ready"
        or deployment_evidence.get("evidence_status") == "accepted"
    )


def build_codetalk_provider_snapshot() -> dict[str, dict[str, Any]]:
    providers = [
        _codetalk_provider_snapshot_item(
            provider="local-search",
            display_name="Local repo search",
            owner="codetalk_builtin",
            status="available",
            capabilities={
                "provider": "local-search",
                "supports_mcp": False,
                "mcp_profiles": [],
                "supports_artifact_export": False,
                "supports_json_output": True,
                "prompt_transport": "none",
                "supports_source_discovery": True,
                "supports_call_graph": False,
                "supports_source_slices": True,
                "supports_black_box_terms": False,
            },
            unavailable_behavior="Always available when the repository path is readable.",
        ),
        _codetalk_provider_snapshot_item(
            provider="gitnexus",
            display_name="GitNexus",
            owner="codetalk_index",
            status="configured" if getattr(settings, "gitnexus_base_url", "") else "missing_config",
            capabilities={
                "provider": "gitnexus",
                "supports_mcp": False,
                "mcp_profiles": [],
                "supports_artifact_export": False,
                "supports_json_output": True,
                "prompt_transport": "http",
                "supports_source_discovery": True,
                "supports_call_graph": True,
                "supports_source_slices": False,
                "supports_black_box_terms": False,
            },
            unavailable_behavior="CodeTalk records GitNexus as unavailable and continues with local search, CGC, memory, and Agent CLI providers.",
        ),
        _codetalk_provider_snapshot_item(
            provider="cgc",
            display_name="CGC",
            owner="codetalk_index",
            status="configured" if getattr(settings, "cgc_base_url", "") else "missing_config",
            capabilities={
                "provider": "cgc",
                "supports_mcp": False,
                "mcp_profiles": [],
                "supports_artifact_export": False,
                "supports_json_output": True,
                "prompt_transport": "http_or_cli",
                "supports_source_discovery": True,
                "supports_call_graph": True,
                "supports_source_slices": False,
                "supports_black_box_terms": False,
            },
            unavailable_behavior="CodeTalk records CGC as unavailable and continues with local search, GitNexus, memory, and Agent CLI providers.",
        ),
        _codetalk_provider_snapshot_item(
            provider="evidence-memory",
            display_name="Evidence Memory",
            owner="codetalk_memory",
            status="available",
            capabilities={
                "provider": "evidence-memory",
                "supports_mcp": False,
                "mcp_profiles": [],
                "supports_artifact_export": False,
                "supports_json_output": True,
                "prompt_transport": "none",
                "supports_source_discovery": True,
                "supports_call_graph": False,
                "supports_source_slices": True,
                "supports_black_box_terms": False,
            },
            unavailable_behavior="If no memory facts exist, CodeTalk continues with live discovery providers.",
        ),
        _codetalk_provider_snapshot_item(
            provider="semantic-library",
            display_name="Semantic Test Library",
            owner="codetalk_memory",
            status="available",
            capabilities={
                "provider": "semantic-library",
                "supports_mcp": False,
                "mcp_profiles": [],
                "supports_artifact_export": False,
                "supports_json_output": True,
                "prompt_transport": "none",
                "supports_source_discovery": False,
                "supports_call_graph": False,
                "supports_source_slices": False,
                "supports_black_box_terms": True,
            },
            unavailable_behavior="If no semantic cases match, black-box generation falls back to validated entries and source evidence.",
        ),
    ]
    return {item["provider"]: item for item in providers}


def _codetalk_provider_snapshot_item(
    *,
    provider: str,
    display_name: str,
    owner: str,
    status: str,
    capabilities: dict[str, Any],
    unavailable_behavior: str,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "display_name": display_name,
        "owner": owner,
        "status": status,
        "non_blocking": True,
        "codetalk_callable": status in {"available", "configured"},
        "agent_owned": False,
        "command": [],
        "fallback_commands": [],
        "readonly_args": [],
        "command_hint_env": "",
        "capabilities": capabilities,
        "credential_boundary": "CodeTalk owns this provider and validates any materialized evidence locally.",
        "unavailable_behavior": unavailable_behavior,
        "diagnostics": _codetalk_provider_diagnostics(
            provider=provider,
            owner=owner,
            status=status,
            codetalk_callable=status in {"available", "configured"},
        ),
    }


def build_agent_cli_provider_diagnostics(provider: str, spec: Any) -> dict[str, Any]:
    """Return side-effect-free launch diagnostics for Agent-owned providers."""
    command_text = str(getattr(spec, "command", "") or "").strip()
    fallback_texts = [
        str(command).strip()
        for command in getattr(spec, "fallback_commands", []) or []
        if str(command).strip()
    ]
    prompt_transport = str(getattr(spec, "prompt_transport", "") or "auto").strip() or "auto"
    command_hint_env = str(getattr(spec, "command_hint_env", "") or "").strip()
    manual_probe = (
        f"POST /api/tools/{provider}/startup-probe with repo_path, then verify the "
        f"same backend shell can launch: {command_text or provider}"
    )
    hints = [
        (
            "PowerShell profile, PATH, and service account environment may differ from "
            "an interactive terminal; verify the backend process can resolve the command."
        ),
        (
            "For CCR/Claude Code Router, prefer configuring claude_code_command as "
            "`ccr code` and run the startup probe to validate non-interactive launch mode."
        ),
        (
            "MCP credentials belong to the Agent CLI process. CodeTalk passes task "
            "bundles and validates artifacts; it does not fetch protected MR data itself."
        ),
    ]
    if command_hint_env:
        hints.append(f"Override this provider with {command_hint_env} when backend PATH differs.")
    diagnostics = {
        "health_endpoint": f"/api/tools/{provider}/health",
        "startup_probe_endpoint": f"/api/tools/{provider}/startup-probe",
        "configured_command_text": command_text,
        "fallback_command_texts": fallback_texts,
        "prompt_transport": prompt_transport,
        "startup_probe_transport": prompt_transport,
        "manual_probe_command": manual_probe,
        "probe_recipe": _agent_cli_probe_recipe(
            provider=provider,
            command_text=command_text,
            fallback_texts=fallback_texts,
            command_hint_env=command_hint_env,
        ),
        "mcp_credentials_owner": "agent_cli",
        "codetalk_validation_role": (
            "CodeTalk treats Agent output as candidate evidence until local artifact, "
            "path, schema, or source-slice validation accepts it."
        ),
        "troubleshooting": hints,
    }
    command_resolution = _agent_cli_command_resolution(provider, command_text, fallback_texts)
    if command_resolution:
        diagnostics["command_resolution"] = command_resolution
    return diagnostics


def _agent_cli_probe_recipe(
    *,
    provider: str,
    command_text: str,
    fallback_texts: list[str],
    command_hint_env: str,
) -> dict[str, Any]:
    env_name = command_hint_env or f"{provider.upper().replace('-', '_')}_COMMAND"
    environment_checks = ["PATH"]
    if provider == "claude-code":
        environment_checks.extend(["CCR_CONFIG_PATH", "CLAUDE_CODE_CONFIG_PATH"])
    return {
        "startup_probe_http": f"POST /api/tools/{provider}/startup-probe?repo_path=<repo_path>",
        "backend_command": command_text or provider,
        "fallback_commands": list(fallback_texts),
        "command_env": env_name,
        "command_env_example": f"{env_name}={command_text or provider}",
        "environment_checks": environment_checks,
        "notes": [
            "Run the startup probe from the CodeTalk UI first; it uses the backend process environment.",
            "If your terminal works but CodeTalk does not, configure the full executable path with command_env.",
            "For Agent-owned MCP, keep credentials in the Agent CLI environment; CodeTalk only passes task bundles.",
        ],
    }


def _agent_cli_command_resolution(
    provider: str,
    command_text: str,
    fallback_texts: list[str],
) -> dict[str, Any]:
    if not command_text:
        return {
            "status": "missing_command",
            "reason": "provider command is not configured",
            "used_fallback": False,
            "attempt_count": 0,
            "attempts": [],
        }
    try:
        health = check_provider_health(provider, command_text, fallback_commands=fallback_texts)
    except Exception as exc:
        return {
            "status": "error",
            "reason": redact_agent_diagnostic_text(str(exc)),
            "used_fallback": False,
            "attempt_count": 0,
            "attempts": [],
        }
    if not isinstance(health, dict):
        return {}
    attempts = [
        _agent_cli_command_resolution_attempt(item)
        for item in health.get("attempts") or []
        if isinstance(item, dict)
    ]
    return {
        "status": str(health.get("status") or ""),
        "configured_command": redact_agent_diagnostic_text(
            str(health.get("configured_command") or command_text)
        ),
        "command": redact_agent_diagnostic_text(str(health.get("command") or "")),
        "path": redact_agent_diagnostic_text(str(health.get("path") or "")),
        "launch_kind": str(health.get("launch_kind") or ""),
        "used_fallback": bool(health.get("used_fallback", False)),
        "reason": redact_agent_diagnostic_text(str(health.get("reason") or "")),
        "attempt_count": len(attempts),
        "attempts": attempts,
        "diagnostic": _agent_cli_command_resolution_diagnostic(health.get("diagnostic")),
    }


def _agent_cli_command_resolution_attempt(item: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "command",
        "status",
        "reason",
        "executable",
        "argv",
        "configured_argv",
        "path",
        "launch_kind",
        "config_hint",
        "profile_config_path",
        "shell_path",
        "diagnostic",
    )
    result: dict[str, Any] = {}
    for key in keys:
        value = item.get(key)
        if value is None:
            continue
        if key in {"argv", "configured_argv"} and isinstance(value, list):
            result[key] = [
                redact_agent_diagnostic_text(str(part))
                for part in value
            ]
            continue
        if key == "diagnostic" and isinstance(value, dict):
            result[key] = _agent_cli_command_resolution_diagnostic(value)
            continue
        result[key] = redact_agent_diagnostic_text(str(value)) if isinstance(value, str) else value
    return result


def _agent_cli_command_resolution_diagnostic(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key in (
        "cwd",
        "summary",
        "command_hint_env",
        "command_hint",
        "path_entry_count",
        "path_entries",
        "checked_common_dirs",
    ):
        item = value.get(key)
        if item is None:
            continue
        if isinstance(item, str):
            result[key] = redact_agent_diagnostic_text(item)
        elif isinstance(item, list):
            result[key] = [
                redact_agent_diagnostic_text(str(part))
                for part in item
            ]
        elif isinstance(item, (int, float, bool)):
            result[key] = item
        else:
            result[key] = redact_agent_diagnostic_text(str(item))
    return result


def _unknown_agent_cli_provider_diagnostics(provider: str) -> dict[str, Any]:
    return {
        "health_endpoint": f"/api/tools/{provider}/health",
        "startup_probe_endpoint": f"/api/tools/{provider}/startup-probe",
        "configured_command_text": "",
        "fallback_command_texts": [],
        "prompt_transport": "",
        "startup_probe_transport": "",
        "manual_probe_command": f"Configure external_agent_custom_providers for {provider}.",
        "probe_recipe": {
            "startup_probe_http": f"POST /api/tools/{provider}/startup-probe?repo_path=<repo_path>",
            "backend_command": provider,
            "fallback_commands": [],
            "command_env": "EXTERNAL_AGENT_CUSTOM_PROVIDERS",
            "command_env_example": (
                f'EXTERNAL_AGENT_CUSTOM_PROVIDERS=[{{"id":"{provider}",'
                f'"command":"{provider} run","prompt_transport":"stdin"}}]'
            ),
            "environment_checks": ["PATH"],
            "notes": [
                "Provider is referenced by a workflow but missing from CodeTalk settings.",
                "Add command and prompt_transport before running the startup probe.",
            ],
        },
        "mcp_credentials_owner": "agent_cli",
        "codetalk_validation_role": "No Agent output can be trusted until the provider is configured and artifacts validate.",
        "troubleshooting": [
            "Provider is referenced by a workflow but missing from CodeTalk settings.",
            "Add it to external_agent_custom_providers with command and prompt_transport.",
        ],
    }


def _codetalk_provider_diagnostics(
    *,
    provider: str,
    owner: str,
    status: str,
    codetalk_callable: bool,
) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "owner": owner,
        "status": status,
        "codetalk_callable": codetalk_callable,
        "health_endpoint": "",
        "startup_probe_endpoint": "",
        "credential_boundary": "CodeTalk owns this provider and uses backend credentials/configuration.",
        "troubleshooting": [],
    }
    if provider in {"gitnexus", "cgc"}:
        diagnostics["health_endpoint"] = f"/api/tools/{provider}/health"
        diagnostics["startup_probe_endpoint"] = f"/api/tools/{provider}/startup-probe"
        diagnostics["troubleshooting"] = [
            f"Run the startup probe for {provider} with repo_path before trusting graph/index coverage.",
            "Unavailable index providers are non-blocking; local search and Agent CLI discovery continue.",
        ]
    elif provider == "local-search":
        diagnostics["troubleshooting"] = [
            "Requires only a readable repo_path; failures usually mean the task repository path is wrong."
        ]
    elif provider == "evidence-memory":
        diagnostics["troubleshooting"] = [
            "No matches means no prior validated facts, not an infrastructure failure."
        ]
    elif provider == "semantic-library":
        diagnostics["troubleshooting"] = [
            "No matches means imported test semantics do not cover this module or feature yet."
        ]
    return diagnostics


def build_context_discovery_decision(
    *,
    agent_instructions: dict[str, Any],
    provider_snapshot: dict[str, Any],
) -> dict[str, Any]:
    requested_files = _instruction_files_requesting_fast_context(agent_instructions)
    codetalk_callable = bool(
        getattr(settings, "context_discovery_enabled", True)
        and getattr(settings, "fast_context_enabled", True)
        and getattr(settings, "fast_context_backend_bridge_enabled", False)
    )
    providers = provider_snapshot.get("providers") or {}
    steps = provider_snapshot.get("steps") or {}
    agent_mcp_providers = [
        provider
        for provider, payload in providers.items()
        if isinstance(payload, dict)
        and bool((payload.get("capabilities") or {}).get("supports_mcp"))
    ]
    agent_steps_with_mcp_profile = [
        step_id
        for step_id, payload in steps.items()
        if isinstance(payload, dict) and str(payload.get("mcp_profile") or "").strip()
    ]
    warnings: list[str] = []
    if requested_files and not codetalk_callable:
        if not getattr(settings, "context_discovery_enabled", True):
            warnings.append("fast-context requested by AGENTS.md but context discovery is disabled")
        elif not getattr(settings, "fast_context_enabled", True):
            warnings.append("fast-context requested by AGENTS.md but provider is disabled")
        else:
            warnings.append("fast-context requested by AGENTS.md but backend MCP bridge is unavailable")
    if requested_files and not agent_mcp_providers and not agent_steps_with_mcp_profile:
        warnings.append("no Agent CLI step advertises MCP support or an MCP profile")
    return {
        "fast-context": {
            "requested_by_agent_instructions": bool(requested_files),
            "requested_by_files": requested_files,
            "codetalk_provider": "fast-context",
            "codetalk_callable": codetalk_callable,
            "codetalk_settings": {
                "context_discovery_enabled": bool(getattr(settings, "context_discovery_enabled", True)),
                "fast_context_enabled": bool(getattr(settings, "fast_context_enabled", True)),
                "fast_context_backend_bridge_enabled": bool(
                    getattr(settings, "fast_context_backend_bridge_enabled", False)
                ),
            },
            "fallback_path": [
                "local_search",
                "gitnexus",
                "cgc",
                "agent_cli",
            ],
            "agent_cli_mcp_possible": bool(agent_mcp_providers or agent_steps_with_mcp_profile),
            "agent_cli_mcp_providers": agent_mcp_providers,
            "agent_cli_mcp_steps": agent_steps_with_mcp_profile,
            "agent_cli_credential_boundary": (
                "Agent CLI may use its own MCP credentials; CodeTalk validates only returned artifacts."
            ),
            "warnings": warnings,
        }
    }


def build_context_artifact_payloads(
    *,
    context_bundle: dict[str, Any],
    context_discovery_decision: dict[str, Any],
    evidence_memory_configured: bool,
    semantic_library_configured: bool,
) -> dict[str, Any]:
    query = str(context_bundle.get("query") or "")
    evidence = [
        item for item in context_bundle.get("evidence") or []
        if isinstance(item, dict)
    ]
    deployment_evidence = [
        item for item in context_bundle.get("deployment_evidence") or []
        if isinstance(item, dict)
    ]
    semantic_cases = [
        item for item in context_bundle.get("semantic_cases") or []
        if isinstance(item, dict)
    ]
    memory_retrieval = {
        "provider": "evidence-memory",
        "query": query,
        "retrieved_count": len(evidence),
        "deployment_retrieved_count": len(deployment_evidence),
        "limit": (context_bundle.get("limits") or {}).get("evidence"),
        "authority_rule": (
            "retrieval is navigation only; source evidence requires validated source_slices"
        ),
        "items": [
            {
                "evidence_id": item.get("evidence_id") or "",
                "kind": item.get("kind") or "",
                "subject_key": item.get("subject_key") or "",
                "status": item.get("status") or "",
                "source": item.get("source") or "",
                "source_read_status": item.get("source_read_status") or "no_source_slices",
                "usable_as_source_evidence": bool(item.get("usable_as_source_evidence")),
                "source_slice_count": len(item.get("source_slices") or []),
                "reuse_reason": _memory_reuse_reason(item),
                "source_slice_refs": _source_slice_refs(item.get("source_slices") or []),
            }
            for item in evidence
        ],
        "deployment_items": [
            {
                "evidence_id": item.get("evidence_id") or "",
                "kind": item.get("kind") or "",
                "subject_key": item.get("subject_key") or "",
                "status": item.get("status") or "",
                "source": item.get("source") or "",
                "symbol": item.get("symbol") or "",
                "path": item.get("path") or "",
                "reuse_reason": _deployment_memory_reuse_reason(item),
                "provenance": item.get("provenance") or {},
            }
            for item in deployment_evidence
        ],
    }
    reads: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for item in evidence:
        evidence_id = str(item.get("evidence_id") or "")
        events.append({
            "event": "memory_retrieved",
            "provider": "evidence-memory",
            "evidence_id": evidence_id,
            "subject_key": item.get("subject_key") or "",
            "usable_as_source_evidence": bool(item.get("usable_as_source_evidence")),
            "reuse_reason": _memory_reuse_reason(item),
        })
        for source_slice in item.get("source_slices") or []:
            if not isinstance(source_slice, dict):
                continue
            verified = source_slice.get("integrity_status") == "verified_current"
            read = {
                "event": "source_slice_attached" if verified else "source_slice_stale",
                "evidence_id": evidence_id,
                "slice_id": source_slice.get("slice_id") or "",
                "file_path": source_slice.get("file_path") or "",
                "start_line": source_slice.get("start_line"),
                "end_line": source_slice.get("end_line"),
                "sha256": source_slice.get("sha256") or "",
                "current_sha256": source_slice.get("current_sha256") or "",
                "status": "validated_source_slice" if verified else "stale_source_slice",
                "validation_error": source_slice.get("validation_error") or "",
                "excerpt_chars": len(str(source_slice.get("excerpt") or "")),
            }
            reads.append(read)
            events.append(read)
    for item in deployment_evidence:
        events.append({
            "event": "deployment_evidence_retrieved",
            "provider": "evidence-memory",
            "evidence_id": item.get("evidence_id") or "",
            "kind": item.get("kind") or "",
            "subject_key": item.get("subject_key") or "",
            "status": item.get("status") or "",
            "source": item.get("source") or "",
            "reuse_reason": _deployment_memory_reuse_reason(item),
        })
    for item in semantic_cases:
        events.append({
            "event": "semantic_case_retrieved",
            "provider": "semantic-library",
            "semantic_id": item.get("semantic_id") or "",
            "case_id": item.get("case_id") or "",
            "terms": item.get("terms") or [],
            "reuse_reason": "query matched semantic library case; use terms to align black-box wording",
        })
    source_read_chain = {
        "query": query,
        "reads": reads,
        "read_count": len(reads),
        "rejected": [],
        "authority_rule": "only validated_source_slice reads may support source evidence",
    }
    evidence_consumption_trajectory = {
        "query": query,
        "task_phase": "prepare",
        "scoring_policy": "navigation_only_not_authority",
        "events": events,
    }
    degraded_retrieval = {
        "query": query,
        "non_blocking": True,
        "degraded": _degraded_retrieval_items(
            context_bundle=context_bundle,
            context_discovery_decision=context_discovery_decision,
            evidence_memory_configured=evidence_memory_configured,
            semantic_library_configured=semantic_library_configured,
        ),
    }
    return {
        "memory_retrieval": memory_retrieval,
        "source_read_chain": source_read_chain,
        "evidence_consumption_trajectory": evidence_consumption_trajectory,
        "degraded_retrieval": degraded_retrieval,
    }


def build_black_box_generation_policy(*, context_bundle: dict[str, Any]) -> dict[str, Any]:
    semantic_cases = [
        item for item in context_bundle.get("semantic_cases") or []
        if isinstance(item, dict)
    ]
    semantic_terms: list[dict[str, Any]] = []
    for item in semantic_cases:
        terms = [str(term) for term in item.get("terms") or [] if str(term)]
        if not terms:
            continue
        semantic_terms.append({
            "case_id": str(item.get("case_id") or ""),
            "feature": str(item.get("feature") or ""),
            "module": str(item.get("module") or ""),
            "terms": terms,
            "test_level": str(item.get("test_level") or ""),
            "reuse_rule": "terminology_only_not_source_truth",
        })
    return {
        "provider": "semantic-library",
        "query": str(context_bundle.get("query") or ""),
        "semantic_terms": semantic_terms,
        "semantic_case_count": len(semantic_cases),
        "semantic_term_count": sum(len(item["terms"]) for item in semantic_terms),
        "authority_rule": (
            "semantic-library matches may shape black-box wording but cannot prove source behavior or entry reachability"
        ),
        "allowed_uses": [
            "black_box_case_wording",
            "test_taxonomy_alignment",
            "observable_assertion_style",
        ],
        "must_not_use_semantics_as": [
            "source_evidence",
            "entry_verification",
            "artifact_validation",
        ],
    }


def _memory_reuse_reason(item: dict[str, Any]) -> str:
    if bool(item.get("usable_as_source_evidence")):
        return (
            "query matched prior evidence; source slices are attached and may be used as source evidence"
        )
    if item.get("source_slices"):
        return "query matched prior evidence; navigation only because source slices are stale or unverified"
    return "query matched prior evidence; navigation only because no source slices are attached"


def _deployment_memory_reuse_reason(item: dict[str, Any]) -> str:
    return (
        "deployment evidence describes Agent provider readiness; "
        "use for routing and diagnostics only"
    )


def _source_slice_refs(source_slices: list[Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for source_slice in source_slices:
        if not isinstance(source_slice, dict):
            continue
        refs.append({
            "slice_id": source_slice.get("slice_id") or "",
            "file_path": source_slice.get("file_path") or "",
            "start_line": source_slice.get("start_line"),
            "end_line": source_slice.get("end_line"),
            "sha256": source_slice.get("sha256") or "",
        })
    return refs


def _degraded_retrieval_items(
    *,
    context_bundle: dict[str, Any],
    context_discovery_decision: dict[str, Any],
    evidence_memory_configured: bool,
    semantic_library_configured: bool,
) -> list[dict[str, Any]]:
    degraded: list[dict[str, Any]] = []
    fast_context = context_discovery_decision.get("fast-context") or {}
    if (
        isinstance(fast_context, dict)
        and fast_context.get("requested_by_agent_instructions")
        and not fast_context.get("codetalk_callable")
    ):
        settings_snapshot = fast_context.get("codetalk_settings") or {}
        if not settings_snapshot.get("context_discovery_enabled", True):
            reason = "context_discovery_disabled"
        elif not settings_snapshot.get("fast_context_enabled", True):
            reason = "provider_disabled"
        else:
            reason = "backend_mcp_bridge_unavailable"
        degraded.append({
            "provider": "fast-context",
            "reason": reason,
            "fallback_path": fast_context.get("fallback_path") or [],
            "warnings": fast_context.get("warnings") or [],
        })
    evidence = context_bundle.get("evidence") or []
    semantic_cases = context_bundle.get("semantic_cases") or []
    if not evidence_memory_configured:
        degraded.append({
            "provider": "evidence-memory",
            "reason": "store_not_configured",
            "fallback_path": ["local_search", "gitnexus", "cgc", "agent_cli"],
        })
    elif not evidence:
        degraded.append({
            "provider": "evidence-memory",
            "reason": "no_matching_evidence",
            "fallback_path": ["local_search", "gitnexus", "cgc", "agent_cli"],
        })
    if not semantic_library_configured:
        degraded.append({
            "provider": "semantic-library",
            "reason": "store_not_configured",
            "fallback_path": ["validated_entries", "source_evidence", "agent_cli"],
        })
    elif not semantic_cases:
        degraded.append({
            "provider": "semantic-library",
            "reason": "no_matching_cases",
            "fallback_path": ["validated_entries", "source_evidence", "agent_cli"],
        })
    return degraded


def _instruction_files_requesting_fast_context(agent_instructions: dict[str, Any]) -> list[str]:
    requested: list[str] = []
    for item in agent_instructions.get("files") or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").lower()
        if "fast-context" not in content and "fast_context" not in content:
            continue
        relative_path = str(item.get("relative_path") or item.get("path") or "").strip()
        if relative_path:
            requested.append(relative_path)
    return requested


def collect_agent_instructions(
    *,
    repo_path: str | Path,
    input_snapshot: dict[str, Any],
    max_chars_per_file: int = 24000,
) -> dict[str, Any]:
    repo_root = Path(repo_path)
    files: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        root = repo_root.resolve()
    except OSError:
        return {
            "files": files,
            "warnings": ["repo_path could not be resolved"],
            "policy": _agent_instruction_policy(),
        }
    if not root.exists() or not root.is_dir():
        return {
            "files": files,
            "warnings": ["repo_path is not an existing directory"],
            "policy": _agent_instruction_policy(),
        }

    seen: set[Path] = set()
    for candidate in _agent_instruction_candidates(root, input_snapshot):
        try:
            path = candidate.resolve()
        except OSError:
            continue
        if path in seen or not _is_within(path, root):
            continue
        seen.add(path)
        if not path.exists() or not path.is_file():
            continue
        data = path.read_bytes()
        content = data.decode("utf-8", errors="replace")
        truncated = len(content) > max_chars_per_file
        files.append({
            "relative_path": path.relative_to(root).as_posix(),
            "path": str(path),
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
            "content": content[:max_chars_per_file],
            "truncated": truncated,
        })
    return {
        "files": files,
        "warnings": warnings,
        "policy": _agent_instruction_policy(),
    }


def _agent_instruction_candidates(root: Path, input_snapshot: dict[str, Any]) -> list[Path]:
    candidates = [root / "AGENTS.md"]
    for hint in _input_path_hints(input_snapshot):
        path = Path(hint)
        if path.is_absolute():
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if not _is_within(resolved, root):
                continue
            relative = resolved.relative_to(root)
        else:
            relative = path
        if any(part in {"", ".", ".."} for part in relative.parts):
            continue
        current = root
        for part in relative.parts[:-1]:
            current = current / part
            candidates.append(current / "AGENTS.md")
        if len(relative.parts) and (root / relative).is_dir():
            candidates.append(root / relative / "AGENTS.md")
    return candidates


def _input_path_hints(input_snapshot: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    for value in input_snapshot.values():
        if isinstance(value, str):
            if _looks_like_path(value):
                hints.append(value)
        elif isinstance(value, dict):
            for key in ("value", "path", "original_path", "copied_path", "filename"):
                item = value.get(key)
                if item and _looks_like_path(str(item)):
                    hints.append(str(item))
        elif isinstance(value, (list, tuple)):
            for item in value:
                text = str(item)
                if _looks_like_path(text):
                    hints.append(text)
    return hints


def _looks_like_path(value: str) -> bool:
    text = value.strip()
    return bool(text) and ("/" in text or "\\" in text)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _agent_instruction_policy() -> dict[str, Any]:
    return {
        "scope": "task",
        "source": "repo_AGENTS_md",
        "preferred_code_locator": "fast-context",
        "fast_context_required": False,
        "unavailable_provider_behavior": "record warning and continue",
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _prepared_task_run_from_payload(payload: dict[str, Any]) -> PreparedWorkbenchTaskRun:
    return PreparedWorkbenchTaskRun(
        task_run_id=str(payload["task_run_id"]),
        workflow_id=str(payload["workflow_id"]),
        workspace_id=str(payload["workspace_id"]),
        repo_path=str(payload["repo_path"]),
        artifact_dir=str(payload["artifact_dir"]),
        workflow_snapshot=dict(payload.get("workflow_snapshot") or {}),
        input_snapshot=dict(payload.get("input_snapshot") or {}),
        task_bundle=dict(payload.get("task_bundle") or {}),
        agent_runs=[
            dict(item) for item in payload.get("agent_runs") or []
            if isinstance(item, dict)
        ],
        created_at=str(payload.get("created_at") or ""),
    )


def _safe_segment(value: str) -> str:
    text = str(value or "").strip()
    if not text or "/" in text or "\\" in text or ".." in text:
        raise KeyError(value)
    return text


def _context_query_from_inputs(input_snapshot: dict[str, Any]) -> str:
    parts: list[str] = []
    for value in input_snapshot.values():
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for key in ("value", "text", "filename", "original_path", "path"):
                if value.get(key):
                    parts.append(str(value[key]))
            parsed_text_path = value.get("parsed_text_path")
            if parsed_text_path:
                parsed = _read_text_prefix(Path(str(parsed_text_path)), max_chars=4000)
                if parsed:
                    parts.append(parsed)
        elif isinstance(value, (list, tuple)):
            parts.extend(str(item) for item in value if str(item))
    query = " ".join(part.strip() for part in parts if part and part.strip())
    return " ".join(query.split())[:8000]


def _read_text_prefix(path: Path, *, max_chars: int) -> str:
    try:
        if not path.exists() or not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except OSError:
        return ""


def _evidence_item_payload(
    item: Any,
    *,
    source_slices: list[Any] | None = None,
    repo_path: str = "",
) -> dict[str, Any]:
    source_slice_payloads = [
        _source_slice_payload(source_slice, repo_path=repo_path)
        for source_slice in (source_slices or [])
    ]
    verified_source_slices = [
        source_slice for source_slice in source_slice_payloads
        if source_slice.get("integrity_status") == "verified_current"
    ]
    payload = {
        "evidence_id": item.evidence_id,
        "run_id": item.run_id,
        "kind": item.kind,
        "subject_key": item.subject_key,
        "status": item.status,
        "source": item.source,
        "path": item.path,
        "symbol": item.symbol,
        "reason": item.reason,
        "confidence": item.confidence,
        "text": item.text,
        "provenance": item.provenance or {},
        "source_read_status": _source_read_status(source_slice_payloads),
        "usable_as_source_evidence": bool(verified_source_slices),
    }
    if source_slice_payloads:
        payload["source_slices"] = source_slice_payloads
    return payload


def _source_slice_payload(item: Any, *, repo_path: str = "") -> dict[str, Any]:
    integrity = _source_slice_integrity(
        repo_path=repo_path,
        file_path=str(item.file_path),
        expected_sha256=str(item.sha256),
    )
    return {
        "slice_id": item.slice_id,
        "evidence_id": item.evidence_id,
        "file_path": item.file_path,
        "start_line": item.start_line,
        "end_line": item.end_line,
        "sha256": item.sha256,
        "integrity_status": integrity["status"],
        "current_sha256": integrity["current_sha256"],
        "validation_error": integrity["validation_error"],
        "excerpt": item.excerpt,
        "created_at": item.created_at,
    }


def _source_read_status(source_slices: list[dict[str, Any]]) -> str:
    if not source_slices:
        return "no_source_slices"
    if any(item.get("integrity_status") == "verified_current" for item in source_slices):
        return "source_slices_attached"
    return "source_slices_stale"


def _source_slice_integrity(
    *,
    repo_path: str,
    file_path: str,
    expected_sha256: str,
) -> dict[str, str]:
    if not repo_path:
        return {
            "status": "repo_path_missing",
            "current_sha256": "",
            "validation_error": "repo_path_missing",
        }
    repo = Path(repo_path)
    try:
        repo_resolved = repo.resolve()
    except OSError:
        return {
            "status": "repo_unavailable",
            "current_sha256": "",
            "validation_error": "repo_unavailable",
        }
    candidate = Path(str(file_path or "").replace("\\", "/"))
    if candidate.is_absolute():
        path = candidate
    else:
        path = repo_resolved / candidate
    try:
        resolved = path.resolve()
    except OSError:
        return {
            "status": "file_missing",
            "current_sha256": "",
            "validation_error": "file_missing",
        }
    try:
        resolved.relative_to(repo_resolved)
    except ValueError:
        return {
            "status": "outside_repo",
            "current_sha256": "",
            "validation_error": "outside_repo",
        }
    if not resolved.exists() or not resolved.is_file():
        return {
            "status": "file_missing",
            "current_sha256": "",
            "validation_error": "file_missing",
        }
    try:
        current_sha = hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError:
        return {
            "status": "read_failed",
            "current_sha256": "",
            "validation_error": "read_failed",
        }
    if expected_sha256 and current_sha == expected_sha256:
        return {
            "status": "verified_current",
            "current_sha256": current_sha,
            "validation_error": "",
        }
    return {
        "status": "hash_mismatch",
        "current_sha256": current_sha,
        "validation_error": "hash_mismatch",
    }


def _semantic_case_payload(item: Any) -> dict[str, Any]:
    return {
        "semantic_id": item.semantic_id,
        "case_id": item.case_id,
        "feature": item.feature,
        "module": item.module,
        "scenario": item.scenario,
        "preconditions": list(item.preconditions),
        "actions": list(item.actions),
        "expected": list(item.expected),
        "test_level": item.test_level,
        "interface": item.interface,
        "terms": list(item.terms),
        "assertion_style": item.assertion_style,
        "tags": list(item.tags),
        "source_ref": item.source_ref,
        "status": item.status,
    }
