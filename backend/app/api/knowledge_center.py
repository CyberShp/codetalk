"""Standalone local API for the F011 test knowledge center."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.services.knowledge_ingest import ParsedKnowledgeSource, parse_bytes, parse_paste
from app.services.knowledge_policy import (
    build_codehub_request,
    resolve_knowledge_scope,
    validate_codehub_response,
)
from app.services.knowledge_store import KnowledgeStore
from app.services.workbench_task_run import WorkbenchTaskRunPreparer
from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
from app.services.workflow_dsl import WorkflowStore


router = APIRouter(prefix="/api/knowledge-center", tags=["Knowledge center"])


class KnowledgeBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IncidentCreate(KnowledgeBaseModel):
    title: str
    summary: str
    scope: Literal["project", "personal_global"] = "personal_global"
    workspace_identity: str = ""
    source_snapshot_ids: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)


class PatternCreate(KnowledgeBaseModel):
    name: str
    content: str
    scope: Literal["project", "personal_global"] = "personal_global"
    workspace_identity: str = ""
    terms: list[str] = Field(default_factory=list)
    applicability: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)


class PatternVersionCreate(KnowledgeBaseModel):
    content: str
    terms: list[str] | None = None
    applicability: list[str] | None = None
    exclusions: list[str] | None = None


class PatternReview(KnowledgeBaseModel):
    review_state: Literal["unreviewed", "confirmed", "rejected"]


class PatternLifecycle(KnowledgeBaseModel):
    lifecycle_state: Literal["active", "superseded", "deprecated"]


class ImportText(KnowledgeBaseModel):
    text: str
    filename: str = "paste.txt"
    scope: Literal["project", "personal_global"] = "personal_global"
    workspace_identity: str = ""
    workspace_remotes: list[str] = Field(default_factory=list)
    mr_project_identity: str = ""
    mr_url: str | None = None
    keep_links: bool = False


class RetryImportStage(KnowledgeBaseModel):
    stage: str


class FeedbackCreate(KnowledgeBaseModel):
    subject_type: Literal["incident", "pattern", "import_job", "retrieval"]
    subject_id: str
    outcome: Literal["useful", "irrelevant", "confirmed", "ruled_out"]
    workspace_identity: str = ""
    note: str = ""


class AgentEnrichmentRequest(KnowledgeBaseModel):
    provider: str = "claude-code"


def get_knowledge_store() -> KnowledgeStore:
    return KnowledgeStore(settings.data_path / "knowledge" / "knowledge.sqlite3")


@router.get("/incidents")
def list_incidents(
    query: str = "",
    scope: Literal["project", "personal_global"] | None = None,
    workspace_identity: str | None = None,
    limit: int = 50,
    store: KnowledgeStore = Depends(get_knowledge_store),
) -> list[dict[str, Any]]:
    return store.list_incidents(
        query=query, scope=scope, workspace_identity=workspace_identity, limit=limit
    )


@router.post("/incidents", status_code=status.HTTP_201_CREATED)
def create_incident(
    payload: IncidentCreate,
    store: KnowledgeStore = Depends(get_knowledge_store),
) -> dict[str, Any]:
    try:
        return store.create_incident(**payload.model_dump())
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/incidents/{incident_id}")
def get_incident(
    incident_id: str,
    store: KnowledgeStore = Depends(get_knowledge_store),
) -> dict[str, Any]:
    try:
        result = store.get_incident(incident_id)
        result["provenance"] = store.get_incident_provenance(incident_id)
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown incident: {incident_id}") from exc


@router.get("/patterns")
def list_patterns(
    query: str = "",
    scope: Literal["project", "personal_global"] | None = None,
    workspace_identity: str | None = None,
    limit: int = 50,
    store: KnowledgeStore = Depends(get_knowledge_store),
) -> list[dict[str, Any]]:
    return store.list_patterns(
        query=query, scope=scope, workspace_identity=workspace_identity, limit=limit
    )


@router.post("/patterns", status_code=status.HTTP_201_CREATED)
def create_pattern(
    payload: PatternCreate,
    store: KnowledgeStore = Depends(get_knowledge_store),
) -> dict[str, Any]:
    try:
        return store.create_pattern(**payload.model_dump())
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/patterns/{pattern_id}")
def get_pattern(
    pattern_id: str,
    store: KnowledgeStore = Depends(get_knowledge_store),
) -> dict[str, Any]:
    try:
        result = store.get_pattern(pattern_id)
        result["versions"] = store.list_pattern_versions(pattern_id)
        result["incidents"] = store.list_pattern_incidents(pattern_id)
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown pattern: {pattern_id}") from exc


@router.get("/patterns/{pattern_id}/versions")
def list_pattern_versions(
    pattern_id: str,
    store: KnowledgeStore = Depends(get_knowledge_store),
) -> list[dict[str, Any]]:
    try:
        return store.list_pattern_versions(pattern_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown pattern: {pattern_id}") from exc


@router.post("/patterns/{pattern_id}/versions", status_code=status.HTTP_201_CREATED)
def add_pattern_version(
    pattern_id: str,
    payload: PatternVersionCreate,
    store: KnowledgeStore = Depends(get_knowledge_store),
) -> dict[str, Any]:
    try:
        return store.add_pattern_version(pattern_id, **payload.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown pattern: {pattern_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/patterns/{pattern_id}/restore/{pattern_version_id}")
def restore_pattern_version(
    pattern_id: str,
    pattern_version_id: str,
    store: KnowledgeStore = Depends(get_knowledge_store),
) -> dict[str, Any]:
    try:
        return store.restore_pattern_version(pattern_id, pattern_version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown pattern version: {pattern_version_id}") from exc


@router.post("/patterns/{pattern_id}/review")
def review_pattern(
    pattern_id: str,
    payload: PatternReview,
    store: KnowledgeStore = Depends(get_knowledge_store),
) -> dict[str, Any]:
    return _update_pattern_state(store, pattern_id, review_state=payload.review_state)


@router.post("/patterns/{pattern_id}/lifecycle")
def update_pattern_lifecycle(
    pattern_id: str,
    payload: PatternLifecycle,
    store: KnowledgeStore = Depends(get_knowledge_store),
) -> dict[str, Any]:
    return _update_pattern_state(store, pattern_id, lifecycle_state=payload.lifecycle_state)


@router.get("/import-jobs")
def list_import_jobs(
    limit: int = 50,
    store: KnowledgeStore = Depends(get_knowledge_store),
) -> list[dict[str, Any]]:
    return store.list_import_jobs(limit=limit)


@router.get("/import-jobs/{job_id}")
def get_import_job(
    job_id: str,
    store: KnowledgeStore = Depends(get_knowledge_store),
) -> dict[str, Any]:
    try:
        return {
            **store.get_import_job(job_id),
            "sources": store.list_import_sources(job_id),
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown import job: {job_id}") from exc


@router.post("/import-jobs/{job_id}/retry")
def retry_import_stage(
    job_id: str,
    payload: RetryImportStage,
    store: KnowledgeStore = Depends(get_knowledge_store),
) -> dict[str, Any]:
    try:
        store.retry_import_stage(job_id, payload.stage)
        return store.get_import_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown import job: {job_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/import-jobs/{job_id}/agent-enrichment", status_code=status.HTTP_202_ACCEPTED)
async def start_agent_enrichment(
    job_id: str,
    payload: AgentEnrichmentRequest,
    store: KnowledgeStore = Depends(get_knowledge_store),
) -> dict[str, Any]:
    try:
        job = store.get_import_job(job_id)
        sources = store.list_import_sources(job_id, include_content=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown import job: {job_id}") from exc
    if not sources:
        raise HTTPException(status_code=422, detail="import job has no registered sources")
    failed_sources = [item["filename"] for item in sources if item["parse_status"] != "parsed"]
    if failed_sources:
        raise HTTPException(
            status_code=422,
            detail=f"source parsing must succeed before Agent enrichment: {', '.join(failed_sources)}",
        )
    context = store.get_import_context(job_id)
    source_paths = _materialize_import_sources(job_id, sources)
    workflow = _agent_enrichment_workflow(
        job_id=job_id,
        provider=payload.provider,
        has_mr=bool(context.get("codehub_request")),
    )
    workflow_store = WorkflowStore(settings.data_path / "workbench" / "workflows.db")
    workflow_store.save_workflow(workflow)
    inputs: dict[str, Any] = {"source_files": [str(path) for path in source_paths]}
    codehub_request = context.get("codehub_request")
    if isinstance(codehub_request, dict):
        inputs["mr_link"] = str(codehub_request.get("mr_url") or "")
    try:
        prepared = WorkbenchTaskRunPreparer(
            artifact_root=settings.data_path / "workbench" / "task_runs",
            workflow_store=workflow_store,
        ).prepare(
            workflow_id=workflow["id"],
            workspace_id=str(job.get("workspace_identity") or "knowledge-personal"),
            repo_path=str(settings.data_path.resolve()),
            inputs=inputs,
        )
    except (KeyError, FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    store.start_import_stage(job_id, "agent_enrichment")
    store.update_import_status(job_id, "agent_enrichment_running")
    store.set_import_context(
        job_id,
        {**context, "task_run_id": prepared.task_run_id, "provider": payload.provider},
    )
    asyncio.create_task(
        asyncio.to_thread(
            _execute_agent_enrichment,
            job_id,
            prepared.task_run_id,
            store,
        )
    )
    return {
        "job_id": job_id,
        "status": "agent_enrichment_running",
        "task_run_id": prepared.task_run_id,
        "provider": payload.provider,
        "codehub_request": codehub_request,
    }


@router.post("/imports/paste", status_code=status.HTTP_201_CREATED)
def import_paste(
    payload: ImportText,
    store: KnowledgeStore = Depends(get_knowledge_store),
) -> dict[str, Any]:
    parsed = parse_paste(payload.text, keep_links=payload.keep_links)
    return _create_import_job(
        store,
        [(payload.filename, payload.text.encode("utf-8"), parsed)],
        scope=payload.scope,
        workspace_identity=payload.workspace_identity,
        workspace_remotes=payload.workspace_remotes,
        mr_project_identity=payload.mr_project_identity,
        mr_url=payload.mr_url,
    )


@router.post("/imports/files", status_code=status.HTTP_201_CREATED)
async def import_files(
    files: list[UploadFile] = File(...),
    scope: Literal["project", "personal_global"] = Form("personal_global"),
    workspace_identity: str = Form(""),
    workspace_remotes: str = Form(""),
    mr_project_identity: str = Form(""),
    mr_url: str = Form(""),
    keep_links: bool = Form(False),
    store: KnowledgeStore = Depends(get_knowledge_store),
) -> dict[str, Any]:
    if not files:
        raise HTTPException(status_code=422, detail="at least one file is required")
    parsed_files: list[tuple[str, bytes, ParsedKnowledgeSource]] = []
    for upload in files:
        content = await upload.read()
        filename = upload.filename or "upload.bin"
        parsed_files.append(
            (filename, content, parse_bytes(content, filename=filename, keep_links=keep_links))
        )
    remotes = [item.strip() for item in workspace_remotes.splitlines() if item.strip()]
    return _create_import_job(
        store,
        parsed_files,
        scope=scope,
        workspace_identity=workspace_identity,
        workspace_remotes=remotes,
        mr_project_identity=mr_project_identity,
        mr_url=mr_url or None,
    )


@router.post("/feedback", status_code=status.HTTP_201_CREATED)
def record_feedback(
    payload: FeedbackCreate,
    store: KnowledgeStore = Depends(get_knowledge_store),
) -> dict[str, Any]:
    return store.record_feedback(**payload.model_dump())


def _update_pattern_state(
    store: KnowledgeStore,
    pattern_id: str,
    **states: str,
) -> dict[str, Any]:
    try:
        return store.update_pattern_states(pattern_id, **states)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown pattern: {pattern_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _create_import_job(
    store: KnowledgeStore,
    sources: list[tuple[str, bytes, ParsedKnowledgeSource]],
    *,
    scope: str,
    workspace_identity: str,
    workspace_remotes: list[str],
    mr_project_identity: str,
    mr_url: str | None,
) -> dict[str, Any]:
    resolved_scope, resolved_identity, scope_reason = _resolve_import_scope(
        scope=scope,
        workspace_identity=workspace_identity,
        workspace_remotes=workspace_remotes,
        mr_project_identity=mr_project_identity,
    )
    try:
        job = store.create_import_job(
            source_count=len(sources), scope=resolved_scope, workspace_identity=resolved_identity
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job_id = str(job["job_id"])
    codehub_request = build_codehub_request(mr_url)
    store.set_import_context(
        job_id,
        {
            "codehub_request": codehub_request,
            "scope_reason": scope_reason,
        },
    )
    store.start_import_stage(job_id, "source_registration")
    source_records: list[dict[str, Any]] = []
    for filename, content, parsed in sources:
        source_kind = _source_kind(filename)
        source = store.register_source(
            source_kind=source_kind,
            source_identity=f"{source_kind}:{filename}",
            content=content,
            scope=resolved_scope,
            workspace_identity=resolved_identity,
            project_identity=resolved_identity,
            locators=parsed.locators,
        )
        source_records.append(
            {
                **source,
                "filename": filename,
                "parser": parsed.parser,
                "parse_status": parsed.status,
                "parse_error": parsed.error,
            }
        )
        store.attach_import_source(
            job_id,
            str(source["source_snapshot_id"]),
            filename=filename,
            parser=parsed.parser,
            parse_status=parsed.status,
            parse_error=parsed.error,
        )
    store.complete_import_stage(job_id, "source_registration", processed_count=len(sources))
    store.start_import_stage(job_id, "deterministic_parse")
    parse_errors = [
        record["parse_error"]
        for record in source_records
        if record["parse_status"] not in {"parsed"}
    ]
    if parse_errors:
        store.fail_import_stage(job_id, "deterministic_parse", "; ".join(parse_errors))
    else:
        store.complete_import_stage(job_id, "deterministic_parse", processed_count=len(sources))
    store.retry_import_stage(job_id, "agent_enrichment")
    store.update_import_status(job_id, "awaiting_agent_enrichment")
    return {
        "job": store.get_import_job(job_id),
        "sources": source_records,
        "scope": {
            "scope": resolved_scope,
            "workspace_identity": resolved_identity,
            "reason": scope_reason,
        },
        "codehub_request": codehub_request,
        "extraction": {
            "status": "pending_agent_enrichment",
            "job_id": job_id,
            "action": "Agent extraction",
            "agent_execution": "not_started",
        },
    }


def _resolve_import_scope(
    *,
    scope: str,
    workspace_identity: str,
    workspace_remotes: list[str],
    mr_project_identity: str,
) -> tuple[str, str, str]:
    if mr_project_identity:
        return resolve_knowledge_scope(
            workspace_remotes=workspace_remotes,
            mr_project_identity=mr_project_identity,
        )
    if scope == "project":
        return "project", workspace_identity, "explicit_workspace_selection"
    return "personal_global", "", "explicit_personal_global_selection"


def _source_kind(filename: str) -> str:
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "paste"
    return suffix if suffix in {"docx", "pdf", "xlsx", "txt", "md", "log", "csv"} else "file"


def _materialize_import_sources(
    job_id: str,
    sources: list[dict[str, Any]],
) -> list[Path]:
    root = settings.data_path / "knowledge" / "import_jobs" / job_id / "sources"
    root.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index, source in enumerate(sources, start=1):
        filename = re.sub(r"[^A-Za-z0-9._-]+", "_", str(source["filename"])).strip("._")
        if not filename:
            filename = f"source-{index}.txt"
        target = root / f"{index:03d}-{filename}"
        target.write_bytes(bytes(source["content"]))
        paths.append(target)
    return paths


def _agent_enrichment_workflow(
    *,
    job_id: str,
    provider: str,
    has_mr: bool,
) -> dict[str, Any]:
    inputs: list[dict[str, Any]] = [
        {
            "id": "source_files",
            "type": "file_set",
            "required": True,
            "label": "历史材料",
            "missing_guidance": "导入任务没有可供 Agent 提炼的来源文件。",
        }
    ]
    required_artifacts = ["incidents.json", "patterns.json"]
    if has_mr:
        inputs.append({
            "id": "mr_link",
            "type": "mr_link",
            "required": True,
            "resolver": "agent_mcp",
            "label": "MR 链接",
        })
        required_artifacts.append("source_manifest.json")
    goal = (
        "Extract historical test incidents and reusable experience patterns from the supplied "
        "source files. Preserve uncertainty, applicability, exclusions, and source indexes. "
        "Write incidents.json as {incidents:[{title,summary,terms,source_indexes}]}; write "
        "patterns.json as {patterns:[{name,content,terms,applicability,exclusions,incident_indexes}]}. "
        "Do not label historical content as a current defect."
    )
    if has_mr:
        goal += (
            " Read only the explicitly supplied MR through CodeHub MCP, follow at most one direct "
            "reference, perform no keyword search, and write source_manifest.json with a sources "
            "array containing source_url, parent_url, hop, and operation for every returned source."
        )
    return {
        "id": f"knowledge_enrichment_{job_id.replace('-', '_')}",
        "name": "Knowledge import Agent enrichment",
        "version": 1,
        "inputs": inputs,
        "steps": [{
            "id": "enrich",
            "type": "agent_task",
            "goal": goal,
            "provider": str(provider),
            "mcp_profile": "codehub-readonly" if has_mr else "",
            "required_artifacts": required_artifacts,
        }],
        "outputs": [
            {"id": "incidents", "type": "json", "from": "enrich", "artifact": "incidents.json"},
            {"id": "patterns", "type": "json", "from": "enrich", "artifact": "patterns.json"},
            *(
                [{"id": "source_manifest", "type": "json", "from": "enrich", "artifact": "source_manifest.json"}]
                if has_mr
                else []
            ),
        ],
    }


def _execute_agent_enrichment(
    job_id: str,
    task_run_id: str,
    store: KnowledgeStore,
) -> None:
    task_root = settings.data_path / "workbench" / "task_runs" / task_run_id
    try:
        execution = WorkbenchWorkflowRunner(
            settings.data_path / "workbench" / "task_runs"
        ).execute_task_run(task_run_id, stop_on_error=True)
        if execution.status != "completed":
            raise ValueError(f"Agent enrichment workflow ended with {execution.status}")
        agent_root = task_root / "agent_runs" / "enrich"
        incidents_payload = _read_json_object(agent_root / "incidents.json")
        patterns_payload = _read_json_object(agent_root / "patterns.json")
        incidents = incidents_payload.get("incidents")
        patterns = patterns_payload.get("patterns")
        if not isinstance(incidents, list) or not isinstance(patterns, list):
            raise ValueError("Agent enrichment artifacts require incidents and patterns lists")
        context = store.get_import_context(job_id)
        codehub_request = context.get("codehub_request")
        if isinstance(codehub_request, dict):
            manifest_payload = _read_json_object(agent_root / "source_manifest.json")
            manifest = manifest_payload.get("sources")
            if not isinstance(manifest, list):
                raise ValueError("source_manifest.json requires a sources list")
            validate_codehub_response(codehub_request, manifest, manifest)
        job = store.get_import_job(job_id)
        sources = store.list_import_sources(job_id)
        source_ids = [str(item["source_snapshot_id"]) for item in sources]
        created_incidents: list[dict[str, Any]] = []
        for item in incidents:
            if not isinstance(item, dict):
                raise ValueError("incident extraction item must be an object")
            selected_sources = _selected_source_ids(item.get("source_indexes"), source_ids)
            created_incidents.append(store.create_incident(
                title=str(item.get("title") or "").strip(),
                summary=str(item.get("summary") or "").strip(),
                scope=str(job["scope"]),
                workspace_identity=str(job.get("workspace_identity") or ""),
                source_snapshot_ids=selected_sources,
                terms=_string_list(item.get("terms")),
            ))
        created_patterns: list[dict[str, Any]] = []
        for item in patterns:
            if not isinstance(item, dict):
                raise ValueError("pattern extraction item must be an object")
            pattern = store.create_pattern(
                name=str(item.get("name") or "").strip(),
                content=str(item.get("content") or "").strip(),
                scope=str(job["scope"]),
                workspace_identity=str(job.get("workspace_identity") or ""),
                terms=_string_list(item.get("terms")),
                applicability=_string_list(item.get("applicability")),
                exclusions=_string_list(item.get("exclusions")),
            )
            created_patterns.append(pattern)
            for index in _index_list(item.get("incident_indexes"), len(created_incidents)):
                incident = created_incidents[index]
                store.link_incident_pattern(
                    str(incident["incident_id"]),
                    str(pattern["pattern_id"]),
                    str(pattern["active_version_id"]),
                )
        result = {
            "job_id": job_id,
            "task_run_id": task_run_id,
            "incident_ids": [item["incident_id"] for item in created_incidents],
            "pattern_ids": [item["pattern_id"] for item in created_patterns],
            "status": "completed",
        }
        (task_root / "knowledge_enrichment_result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        store.complete_import_stage(
            job_id,
            "agent_enrichment",
            processed_count=len(created_incidents) + len(created_patterns),
        )
        store.update_import_status(job_id, "completed")
    except Exception as exc:
        store.fail_import_stage(job_id, "agent_enrichment", str(exc))
        store.update_import_status(job_id, "failed")


def _read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _index_list(value: Any, size: int) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item < size:
            raise ValueError("incident index is outside the imported incident list")
        if item not in result:
            result.append(item)
    return result


def _selected_source_ids(value: Any, source_ids: list[str]) -> list[str]:
    indexes = _index_list(value, len(source_ids))
    return [source_ids[index] for index in indexes] if indexes else list(source_ids)
