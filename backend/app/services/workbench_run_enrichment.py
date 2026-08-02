"""Optional product enrichments applied after the Phase 2 run is prepared."""

from __future__ import annotations

import json
import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any

from app.services.artifact_profiles import (
    ArtifactProfileStore,
    apply_artifact_profile_to_task_bundle,
    write_output_contract_snapshot,
)
from app.services.evidence_memory import EvidenceMemoryStore
from app.services.knowledge_policy import canonical_repository_identity
from app.services.knowledge_retrieval import (
    EvidenceMemoryKnowledgeProvider,
    FederatedKnowledgeRetriever,
    SemanticLibraryKnowledgeProvider,
    WorkspaceMaterialKnowledgeProvider,
)
from app.services.knowledge_store import KnowledgeStore
from app.services.test_semantic_library import TestSemanticLibraryStore
from app.services.workbench_deliverables import build_task_run_deliverables


def enrich_prepared_task_run(
    prepared: Any,
    *,
    artifact_profile_store: ArtifactProfileStore,
    knowledge_store: KnowledgeStore,
    evidence_memory: EvidenceMemoryStore | None,
    semantic_library: TestSemanticLibraryStore | None,
    material_db_path: str | Path,
    selected_artifact_profile_id: str = "",
    feature_tags: list[str] | None = None,
    parent_artifact_profile: dict[str, Any] | None = None,
) -> None:
    """Attach optional contracts without changing Phase 2 execution authority."""

    task_dir = Path(str(prepared.artifact_dir))
    profile_resolution = _resolve_artifact_profile(
        workflow_snapshot=prepared.workflow_snapshot,
        workspace_id=str(prepared.workspace_id),
        store=artifact_profile_store,
        selected_profile_id=selected_artifact_profile_id,
        feature_tags=feature_tags or [],
        parent_artifact_profile=parent_artifact_profile,
    )
    output_contract = write_output_contract_snapshot(
        task_dir,
        task_run_id=str(prepared.task_run_id),
        resolution=profile_resolution,
    )

    context_bundle = prepared.task_bundle.get("context_bundle")
    query = (
        str(context_bundle.get("query") or "")
        if isinstance(context_bundle, dict)
        else ""
    )
    knowledge_retrieval = build_workbench_knowledge_retrieval(
        workflow_snapshot=prepared.workflow_snapshot,
        query=query,
        repo_path=str(prepared.repo_path),
        workspace_id=str(prepared.workspace_id),
        knowledge_store=knowledge_store,
        evidence_memory=evidence_memory,
        semantic_library=semantic_library,
        material_db_path=material_db_path,
    )

    prepared.task_bundle.clear()
    prepared.task_bundle.update(
        apply_artifact_profile_to_task_bundle(
            {
                **_read_json_object(task_dir / "task_bundle.json"),
                "knowledge_retrieval": knowledge_retrieval,
            },
            output_contract,
        )
    )
    _write_json(task_dir / "knowledge_retrieval.json", knowledge_retrieval)
    for agent_run in prepared.agent_runs:
        agent_dir = Path(str(agent_run.get("artifact_dir") or ""))
        if not agent_dir.is_dir():
            continue
        agent_bundle = _read_json_object(agent_dir / "task_bundle.json")
        agent_bundle["artifact_profile"] = dict(output_contract)
        agent_bundle["knowledge_retrieval"] = dict(knowledge_retrieval)
        _write_json(agent_dir / "task_bundle.json", agent_bundle)


def build_builtin_artifact_profile(
    workflow_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Translate frozen workflow outputs into the default deliverable profile."""

    artifacts: list[dict[str, Any]] = []
    seen_filenames: set[str] = set()
    for index, output in enumerate(workflow_snapshot.get("outputs") or []):
        if not isinstance(output, dict):
            continue
        output_id = str(output.get("id") or f"output_{index + 1}").strip()
        artifact_id = re.sub(r"[^a-z0-9_-]+", "_", output_id.casefold()).strip("_")
        if not artifact_id or not artifact_id[0].isalpha():
            artifact_id = f"output_{index + 1}"
        artifact_format, suffix = _builtin_artifact_format(
            str(output.get("type") or "json").strip().casefold()
        )
        filename = str(
            output.get("artifact")
            or output.get("filename")
            or f"{artifact_id}{suffix}"
        ).strip()
        if filename.casefold() in seen_filenames:
            filename = f"{artifact_id}_{index + 1}{suffix}"
        seen_filenames.add(filename.casefold())
        artifact: dict[str, Any] = {
            "id": artifact_id,
            "filename": filename,
            "format": artifact_format,
            "required": bool(output.get("required", True)),
        }
        if isinstance(output.get("schema"), dict):
            artifact["schema"] = dict(output["schema"])
        artifacts.append(artifact)
    if not artifacts:
        artifacts.append(
            {
                "id": "report",
                "filename": "report.md",
                "format": "markdown",
                "required": True,
            }
        )
    workflow_id = str(workflow_snapshot.get("id") or "workflow")
    return {
        "id": f"builtin_{workflow_id}",
        "version": int(workflow_snapshot.get("version") or 1),
        "name": f"{workflow_snapshot.get('name') or workflow_id} default outputs",
        "description": "Generated from the frozen workflow output definition.",
        "scope": {"workflow_id": workflow_id},
        "artifacts": artifacts,
    }


def build_workbench_knowledge_retrieval(
    *,
    workflow_snapshot: dict[str, Any],
    query: str,
    repo_path: str,
    workspace_id: str,
    knowledge_store: KnowledgeStore | None,
    evidence_memory: EvidenceMemoryStore | None,
    semantic_library: TestSemanticLibraryStore | None,
    material_db_path: str | Path,
) -> dict[str, Any]:
    policy = workflow_snapshot.get("knowledge_policy")
    if not isinstance(policy, dict):
        return _empty_retrieval("disabled", policy=None)
    normalized_policy = {
        "sources": [str(item) for item in policy.get("sources") or []],
        "scopes": [str(item) for item in policy.get("scopes") or []],
        "mode": str(policy.get("mode") or "preflight"),
        "max_results": int(policy.get("max_results") or 12),
        "allow_followup": bool(policy.get("allow_followup", False)),
    }
    workspace_identity = _repository_identity(repo_path)
    if not query.strip():
        return _empty_retrieval(
            "skipped_empty_query",
            policy=normalized_policy,
            workspace_identity=workspace_identity,
        )
    if knowledge_store is None:
        result = _empty_retrieval(
            "degraded_unavailable",
            policy=normalized_policy,
            workspace_identity=workspace_identity,
        )
        result["provider_statuses"] = [
            {"provider": "experience_patterns", "status": "not_configured"}
        ]
        return result
    if normalized_policy["mode"] == "on_demand":
        result = _empty_retrieval(
            "ready_on_demand",
            policy=normalized_policy,
            workspace_identity=workspace_identity,
        )
        result["query"] = ""
        return result

    selected_sources = set(normalized_policy["sources"])
    providers = []
    unavailable: list[dict[str, str]] = []
    if "semantic_cases" in selected_sources:
        if semantic_library is None:
            unavailable.append({"provider": "semantic_cases", "status": "not_configured"})
        else:
            providers.append(SemanticLibraryKnowledgeProvider(semantic_library))
    if "evidence_memory" in selected_sources:
        if evidence_memory is None:
            unavailable.append({"provider": "evidence_memory", "status": "not_configured"})
        else:
            providers.append(
                EvidenceMemoryKnowledgeProvider(evidence_memory, workspace_id=workspace_id)
            )
    if "materials" in selected_sources:
        database = Path(material_db_path)
        if database.is_file():
            providers.append(
                WorkspaceMaterialKnowledgeProvider(database, workspace_id=workspace_id)
            )
        else:
            unavailable.append({"provider": "materials", "status": "not_configured"})

    retrieval = FederatedKnowledgeRetriever(
        knowledge_store,
        legacy_providers=providers,
    ).retrieve(
        query,
        workspace_identity=workspace_identity or None,
        max_results=normalized_policy["max_results"],
        scopes=normalized_policy["scopes"],
        include_experience="experience_patterns" in selected_sources,
    )
    return {
        "status": "ready",
        "policy": normalized_policy,
        "workspace_identity": workspace_identity,
        "query": query,
        "records": [
            {
                **dict(record),
                "authority": "investigation_lead",
                "usable_as_current_evidence": False,
            }
            for record in retrieval.records
        ],
        "fts_candidate_count": retrieval.fts_candidate_count,
        "embedding_status": retrieval.embedding_status,
        "provider_statuses": [*retrieval.provider_statuses, *unavailable],
    }


def knowledge_followup_requests(
    artifact_dir: str | Path,
    trusted_task_bundle: dict[str, Any],
) -> list[dict[str, str]]:
    """Read bounded Agent requests only when the frozen policy permits them."""

    retrieval = trusted_task_bundle.get("knowledge_retrieval")
    policy = retrieval.get("policy") if isinstance(retrieval, dict) else None
    if not isinstance(policy, dict) or not bool(policy.get("allow_followup", False)):
        return []
    payload = _read_json_value(Path(artifact_dir) / "knowledge_followup_requests.json")
    raw_items = payload.get("queries") if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list):
        return []
    requests: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        query = str(item.get("query") or "").strip()[:500]
        key = query.casefold()
        if not query or key in seen:
            continue
        seen.add(key)
        requests.append(
            {
                "query": query,
                "reason": str(
                    item.get("reason") or "agent requested historical context"
                ).strip()[:500],
            }
        )
    return requests[:3]


def materialize_requested_knowledge(
    *,
    requests: list[dict[str, str]],
    task_bundle: dict[str, Any],
    workflow_snapshot: dict[str, Any],
    knowledge_store: KnowledgeStore,
    evidence_memory: EvidenceMemoryStore | None,
    semantic_library: TestSemanticLibraryStore | None,
    material_db_path: str | Path,
) -> dict[str, Any]:
    """Resolve Agent follow-up queries under the frozen workflow policy."""

    followup_workflow = dict(workflow_snapshot)
    policy = (
        dict(workflow_snapshot.get("knowledge_policy"))
        if isinstance(workflow_snapshot.get("knowledge_policy"), dict)
        else {}
    )
    policy["mode"] = "preflight"
    policy["allow_followup"] = False
    followup_workflow["knowledge_policy"] = policy
    retrievals: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    seen_records: set[str] = set()
    for request in requests:
        retrieval = build_workbench_knowledge_retrieval(
            workflow_snapshot=followup_workflow,
            query=str(request.get("query") or ""),
            repo_path=str(task_bundle.get("repo_path") or ""),
            workspace_id=str(task_bundle.get("workspace_id") or ""),
            knowledge_store=knowledge_store,
            evidence_memory=evidence_memory,
            semantic_library=semantic_library,
            material_db_path=material_db_path,
        )
        retrievals.append(
            {
                "query": str(request.get("query") or ""),
                "reason": str(request.get("reason") or ""),
                **retrieval,
            }
        )
        for record in retrieval.get("records") or []:
            if not isinstance(record, dict):
                continue
            record_key = str(record.get("record_id") or "") or hashlib.sha256(
                json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            if record_key in seen_records:
                continue
            seen_records.add(record_key)
            records.append(dict(record))
    return {
        "status": "ready" if records else "ready_empty",
        "requests": requests,
        "retrievals": retrievals,
        "records": records,
        "authority": "history_remains_investigation_lead",
        "warnings": [],
    }


def inject_requested_knowledge(
    artifact_dir: str | Path,
    retrieval: dict[str, Any],
) -> None:
    bundle_path = Path(artifact_dir) / "task_bundle.json"
    bundle = _read_json_object(bundle_path)
    if not bundle:
        return
    bundle["requested_knowledge"] = dict(retrieval)
    _write_json(bundle_path, bundle)


def build_knowledge_usage(
    retrieval: dict[str, Any],
    step_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    injected_ids = sorted(
        {
            str(item.get("record_id") or "")
            for item in retrieval.get("records") or []
            if isinstance(item, dict) and item.get("record_id")
        }
    )
    injected = set(injected_ids)
    reported: set[str] = set()
    unrecognized: set[str] = set()
    reports: list[dict[str, Any]] = []
    for report in step_reports:
        if not isinstance(report, dict):
            continue
        raw_ids = report.get("used_record_ids") or report.get("record_ids") or []
        ids = [str(item) for item in raw_ids if str(item)] if isinstance(raw_ids, list) else []
        reported.update(item for item in ids if item in injected)
        unrecognized.update(item for item in ids if item not in injected)
        reports.append(dict(report))
    return {
        "status": "reported" if reported else "not_reported",
        "retrieval_status": str(retrieval.get("status") or "disabled"),
        "injected_record_ids": injected_ids,
        "reported_used_record_ids": sorted(reported),
        "unrecognized_record_ids": sorted(unrecognized),
        "authority": "history_remains_investigation_lead",
        "step_reports": reports,
    }


def finalize_enriched_task_run(
    task_run: Any,
    execution: dict[str, Any],
) -> dict[str, Any]:
    """Persist optional audit and delivery artifacts after Phase2 finalization."""

    task_dir = Path(str(task_run.artifact_dir))
    retrieval_path = task_dir / "knowledge_retrieval.json"
    output_contract_path = task_dir / "output_contract.json"
    _assert_frozen_component_matches(
        task_dir,
        component_id="knowledge_retrieval",
        relative_path="knowledge_retrieval.json",
    )
    _assert_frozen_component_matches(
        task_dir,
        component_id="output_contract",
        relative_path="output_contract.json",
    )
    if retrieval_path.is_file():
        retrieval = _merged_knowledge_retrieval(
            _read_json_object(retrieval_path),
            execution.get("step_results") or [],
        )
        reports: list[dict[str, Any]] = []
        for step in execution.get("step_results") or []:
            if not isinstance(step, dict):
                continue
            step_id = str(step.get("step_id") or "")
            if not step_id:
                continue
            report = _read_json_object(task_dir / "steps" / step_id / "knowledge_usage.json")
            if not report:
                report = _read_json_object(
                    task_dir / "agent_runs" / step_id / "knowledge_usage.json"
                )
            if report:
                reports.append({"step_id": step_id, **report})
        _write_json(task_dir / "knowledge_usage.json", build_knowledge_usage(retrieval, reports))

    successful = str(execution.get("status") or "") in {
        "completed",
        "completed_empty",
        "needs_review",
        "partial",
    }
    if not output_contract_path.is_file() or not successful:
        return {}
    deliverables = build_task_run_deliverables(task_run)
    summary = {
        "task_run_id": str(task_run.task_run_id),
        "artifact_count": deliverables["artifact_count"],
        "bundle_size_bytes": deliverables["bundle_size_bytes"],
        "bundle_sha256": deliverables["bundle_sha256"],
        "validation": deliverables["validation"],
    }
    _write_json(task_dir / "deliverable_bundle.json", summary)
    return deliverables


def _assert_frozen_component_matches(
    task_dir: Path,
    *,
    component_id: str,
    relative_path: str,
) -> None:
    snapshot = _read_json_object(task_dir / "run_snapshot_v3.json")
    components = snapshot.get("components") if isinstance(snapshot, dict) else None
    descriptor = components.get(component_id) if isinstance(components, dict) else None
    if not isinstance(descriptor, dict):
        return
    if str(descriptor.get("path") or "") != relative_path:
        raise RuntimeError(f"invalid frozen enrichment component: {component_id}")
    expected_sha256 = str(descriptor.get("sha256") or "").lower()
    path = task_dir / relative_path
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"missing frozen enrichment component: {component_id}") from exc
    if len(expected_sha256) != 64 or hashlib.sha256(data).hexdigest() != expected_sha256:
        raise RuntimeError(f"changed frozen enrichment component: {component_id}")


def _merged_knowledge_retrieval(
    retrieval: dict[str, Any],
    step_results: list[dict[str, Any]],
) -> dict[str, Any]:
    merged = dict(retrieval)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    sources = [merged.get("records") or []]
    sources.extend(
        step.get("injected_knowledge", {}).get("records") or []
        for step in step_results
        if isinstance(step, dict) and isinstance(step.get("injected_knowledge"), dict)
    )
    for source_records in sources:
        for record in source_records:
            if not isinstance(record, dict):
                continue
            key = str(record.get("record_id") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            records.append(dict(record))
    merged["records"] = records
    if records and merged.get("status") == "ready_on_demand":
        merged["status"] = "ready_followup"
    return merged


def _resolve_artifact_profile(
    *,
    workflow_snapshot: dict[str, Any],
    workspace_id: str,
    store: ArtifactProfileStore,
    selected_profile_id: str,
    feature_tags: list[str],
    parent_artifact_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    if isinstance(parent_artifact_profile, dict):
        return {
            "source": "parent_attempt",
            "profile": {
                "id": str(parent_artifact_profile.get("profile_id") or ""),
                "version": int(parent_artifact_profile.get("profile_version") or 0),
                "name": str(parent_artifact_profile.get("name") or ""),
                "description": str(parent_artifact_profile.get("description") or ""),
                "artifacts": [
                    dict(item)
                    for item in parent_artifact_profile.get("artifacts") or []
                    if isinstance(item, dict)
                ],
            },
        }
    return store.resolve_profile(
        selected_profile_id=selected_profile_id,
        workspace_id=workspace_id,
        feature_tags=feature_tags,
        builtin_profile=build_builtin_artifact_profile(workflow_snapshot),
    )


def _repository_identity(repo_path: str) -> str:
    repo = Path(str(repo_path or "")).expanduser()
    if not repo.is_dir():
        return ""
    try:
        completed = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return canonical_repository_identity(completed.stdout.strip())


def _builtin_artifact_format(output_type: str) -> tuple[str, str]:
    if output_type in {"markdown", "md", "report"}:
        return "markdown", ".md"
    if output_type == "csv":
        return "csv", ".csv"
    if output_type == "xlsx":
        return "xlsx", ".xlsx"
    if output_type in {"text", "txt"}:
        return "text", ".txt"
    return "json", ".json"


def _empty_retrieval(
    status: str,
    *,
    policy: dict[str, Any] | None,
    workspace_identity: str = "",
) -> dict[str, Any]:
    return {
        "status": status,
        "policy": policy,
        "workspace_identity": workspace_identity,
        "records": [],
        "provider_statuses": [],
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_json_value(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
