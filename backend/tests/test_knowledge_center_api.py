"""API contract tests for the standalone F011 knowledge center surface."""

from contextlib import asynccontextmanager
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def knowledge_client(tmp_path, monkeypatch):
    from app.api import knowledge_center
    from app.config import settings
    from app.services.knowledge_store import KnowledgeStore

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "runtime"))
    store = KnowledgeStore(tmp_path / "knowledge.sqlite3")
    source = store.register_source(
        source_kind="paste",
        source_identity="paste:seed",
        content=b"CmdSN availability stays at zero after recovery.",
        scope="project",
        workspace_identity="codehub.example/storage/array",
        locators=[{"kind": "line", "start": 1, "end": 1, "excerpt": "CmdSN"}],
    )
    incident = store.create_incident(
        title="CmdSN recovery stalls",
        summary="Advertised resources do not recover after pressure is removed.",
        scope="project",
        workspace_identity="codehub.example/storage/array",
        source_snapshot_ids=[source["source_snapshot_id"]],
        terms=["iSCSI", "CmdSN"],
    )
    pattern = store.create_pattern(
        name="Protocol progress after resource pressure",
        content="Check sequence progress and externally advertised availability.",
        scope="project",
        workspace_identity="codehub.example/storage/array",
        applicability=["resource exhaustion"],
        exclusions=["explicit recovery reset"],
    )

    @asynccontextmanager
    async def lifespan(app):
        yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(knowledge_center.router)
    app.dependency_overrides[knowledge_center.get_knowledge_store] = lambda: store
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, store, incident, pattern


async def test_knowledge_center_lists_searches_and_returns_provenance_details(
    knowledge_client,
):
    client, _, incident, pattern = knowledge_client

    incidents = await client.get(
        "/api/knowledge-center/incidents",
        params={"query": "CmdSN", "workspace_identity": "codehub.example/storage/array"},
    )
    assert incidents.status_code == 200
    assert incidents.json()[0]["incident_id"] == incident["incident_id"]

    detail = await client.get(
        f"/api/knowledge-center/incidents/{incident['incident_id']}"
    )
    assert detail.status_code == 200
    assert detail.json()["provenance"][0]["locators"][0]["kind"] == "line"

    patterns = await client.get(
        "/api/knowledge-center/patterns", params={"query": "sequence"}
    )
    assert patterns.status_code == 200
    assert patterns.json()[0]["pattern_id"] == pattern["pattern_id"]


async def test_pattern_version_restore_review_and_lifecycle_are_reversible(
    knowledge_client,
):
    client, _, _, pattern = knowledge_client
    pattern_id = pattern["pattern_id"]

    created_version = await client.post(
        f"/api/knowledge-center/patterns/{pattern_id}/versions",
        json={"content": "Check protocol progress and shared queue ownership."},
    )
    assert created_version.status_code == 201
    version_id = created_version.json()["pattern_version_id"]

    reviewed = await client.post(
        f"/api/knowledge-center/patterns/{pattern_id}/review",
        json={"review_state": "confirmed"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["review_state"] == "confirmed"

    deprecated = await client.post(
        f"/api/knowledge-center/patterns/{pattern_id}/lifecycle",
        json={"lifecycle_state": "deprecated"},
    )
    assert deprecated.status_code == 200
    assert deprecated.json()["lifecycle_state"] == "deprecated"

    restored = await client.post(
        f"/api/knowledge-center/patterns/{pattern_id}/restore/{version_id}"
    )
    assert restored.status_code == 200
    assert restored.json()["active_version_id"] == version_id


async def test_imports_are_deterministic_retryable_and_keep_optional_mr_explicit(
    knowledge_client,
):
    client, _, _, _ = knowledge_client

    no_mr = await client.post(
        "/api/knowledge-center/imports/paste",
        json={
            "text": "DTOE login window can block a shared receive queue.",
            "scope": "personal_global",
        },
    )
    assert no_mr.status_code == 201
    no_mr_payload = no_mr.json()
    assert no_mr_payload["codehub_request"] is None
    assert no_mr_payload["extraction"]["status"] == "pending_agent_enrichment"
    assert {stage["stage"] for stage in no_mr_payload["job"]["stages"]} >= {
        "deterministic_parse",
        "agent_enrichment",
    }

    with_mr = await client.post(
        "/api/knowledge-center/imports/paste",
        json={
            "text": "MR-backed incident context",
            "scope": "project",
            "workspace_identity": "codehub.example/storage/array",
            "mr_url": "https://codehub.example/storage/array/-/merge_requests/42",
        },
    )
    assert with_mr.status_code == 201
    request = with_mr.json()["codehub_request"]
    assert request["mr_url"].endswith("/42")
    assert request["search_enabled"] is False
    assert request["max_reference_hops"] == 1

    job_id = no_mr_payload["job"]["job_id"]
    job_detail = await client.get(f"/api/knowledge-center/import-jobs/{job_id}")
    assert job_detail.status_code == 200
    assert job_detail.json()["sources"][0]["filename"] == "paste.txt"
    jobs = await client.get("/api/knowledge-center/import-jobs")
    assert jobs.status_code == 200
    assert any(job["job_id"] == job_id for job in jobs.json())

    retry = await client.post(
        f"/api/knowledge-center/import-jobs/{job_id}/retry",
        json={"stage": "agent_enrichment"},
    )
    assert retry.status_code == 200
    assert retry.json()["stages"][-1]["status"] == "pending"


async def test_batch_upload_and_feedback_are_available_without_fake_extraction(
    knowledge_client,
):
    client, _, _, _ = knowledge_client

    uploaded = await client.post(
        "/api/knowledge-center/imports/files",
        data={"scope": "personal_global"},
        files=[
            ("files", ("incident-a.txt", b"resource pressure", "text/plain")),
            ("files", ("incident-b.txt", b"login window", "text/plain")),
        ],
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["job"]["source_count"] == 2
    assert uploaded.json()["extraction"]["status"] == "pending_agent_enrichment"

    feedback = await client.post(
        "/api/knowledge-center/feedback",
        json={
            "subject_type": "pattern",
            "subject_id": "pat_example",
            "outcome": "irrelevant",
            "note": "same term, different lifecycle",
        },
    )
    assert feedback.status_code == 201
    assert feedback.json()["outcome"] == "irrelevant"


async def test_agent_enrichment_prepares_real_workbench_run_without_unsupplied_mr(
    knowledge_client,
    monkeypatch,
):
    from app.api import knowledge_center
    from app.config import settings

    client, _, _, _ = knowledge_client
    monkeypatch.setattr(
        knowledge_center,
        "_execute_agent_enrichment",
        lambda _job_id, _task_run_id, _store: None,
    )
    imported = await client.post(
        "/api/knowledge-center/imports/paste",
        json={"text": "CmdSN recovery history", "scope": "personal_global"},
    )
    job_id = imported.json()["job"]["job_id"]

    started = await client.post(
        f"/api/knowledge-center/import-jobs/{job_id}/agent-enrichment",
        json={"provider": "claude-code"},
    )

    assert started.status_code == 202
    assert started.json()["codehub_request"] is None
    task_run_id = started.json()["task_run_id"]
    bundle = (
        settings.data_path
        / "workbench"
        / "task_runs"
        / task_run_id
        / "task_bundle.json"
    ).read_text(encoding="utf-8")
    assert '"agent_mcp_requests": []' in bundle
    assert "source_files" in bundle


async def test_agent_enrichment_passes_only_explicit_mr_to_agent_mcp(
    knowledge_client,
    monkeypatch,
):
    from app.api import knowledge_center
    from app.config import settings

    client, _, _, _ = knowledge_client
    monkeypatch.setattr(
        knowledge_center,
        "_execute_agent_enrichment",
        lambda _job_id, _task_run_id, _store: None,
    )
    mr_url = "https://codehub.example/storage/array/-/merge_requests/42"
    imported = await client.post(
        "/api/knowledge-center/imports/paste",
        json={
            "text": "MR-backed incident history",
            "scope": "project",
            "workspace_identity": "codehub.example/storage/array",
            "mr_url": mr_url,
        },
    )

    started = await client.post(
        f"/api/knowledge-center/import-jobs/{imported.json()['job']['job_id']}/agent-enrichment",
        json={"provider": "claude-code"},
    )

    assert started.status_code == 202
    task_run_id = started.json()["task_run_id"]
    bundle = json.loads(
        (
            settings.data_path
            / "workbench"
            / "task_runs"
            / task_run_id
            / "task_bundle.json"
        ).read_text(encoding="utf-8")
    )
    assert [item["value"] for item in bundle["agent_mcp_requests"]] == [mr_url]
    assert bundle["agent_mcp_requests"][0]["mcp_profiles"] == ["codehub-readonly"]


async def test_agent_enrichment_persists_unreviewed_incidents_and_patterns(
    knowledge_client,
    monkeypatch,
):
    from app.api import knowledge_center
    from app.config import settings

    client, store, _, _ = knowledge_client
    imported = await client.post(
        "/api/knowledge-center/imports/paste",
        json={"text": "shared queue blocks login", "scope": "personal_global"},
    )
    job_id = imported.json()["job"]["job_id"]
    task_run_id = f"task_run_enrichment_{job_id}"
    agent_root = (
        settings.data_path
        / "workbench"
        / "task_runs"
        / task_run_id
        / "agent_runs"
        / "enrich"
    )
    agent_root.mkdir(parents=True, exist_ok=True)
    (agent_root / "incidents.json").write_text(
        json.dumps({
            "incidents": [{
                "title": "DTOE login queue stall",
                "summary": "Data before login completion occupied a shared queue.",
                "terms": ["DTOE", "login"],
                "source_indexes": [0],
            }]
        }),
        encoding="utf-8",
    )
    (agent_root / "patterns.json").write_text(
        json.dumps({
            "patterns": [{
                "name": "Transition window and shared queue",
                "content": "Check transition rejection cleanup and queue ownership.",
                "terms": ["shared queue"],
                "applicability": ["login transition"],
                "exclusions": ["dedicated queue"],
                "incident_indexes": [0],
            }]
        }),
        encoding="utf-8",
    )

    class FakeRunner:
        def __init__(self, _root):
            pass

        def execute_task_run(self, _task_run_id, stop_on_error=True):
            assert stop_on_error is True
            return SimpleNamespace(status="completed")

    monkeypatch.setattr(knowledge_center, "WorkbenchWorkflowRunner", FakeRunner)
    knowledge_center._execute_agent_enrichment(job_id, task_run_id, store)

    job = store.get_import_job(job_id)
    assert job["status"] == "completed"
    created_pattern = store.list_patterns(query="Transition window")[0]
    assert created_pattern["review_state"] == "unreviewed"
    assert store.list_pattern_incidents(created_pattern["pattern_id"])[0]["title"] == (
        "DTOE login queue stall"
    )


async def test_project_import_without_identity_returns_actionable_validation_error(
    knowledge_client,
):
    client, _, _, _ = knowledge_client

    response = await client.post(
        "/api/knowledge-center/imports/paste",
        json={"text": "missing project identity", "scope": "project"},
    )

    assert response.status_code == 422
    assert "workspace_identity" in response.json()["detail"]
