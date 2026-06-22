from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import settings

pytestmark = [pytest.mark.asyncio]


@asynccontextmanager
async def _no_lifespan(app: FastAPI):
    yield


@pytest.fixture
async def workbench_client(tmp_path, monkeypatch):
    from app.api import agent_workbench

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    app = FastAPI(lifespan=_no_lifespan)
    app.include_router(agent_workbench.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_workbench_workflow_crud_api(workbench_client):
    workflow = {
        "id": "custom_mr_blackbox",
        "name": "MR black-box workflow",
        "version": 1,
        "inputs": [{"id": "mr_link", "type": "external_link", "resolver": "agent_mcp"}],
        "steps": [{"id": "collect", "type": "agent_task", "mcp_profile": "codehub-readonly"}],
        "outputs": [{"id": "report", "type": "markdown"}],
    }

    created = await workbench_client.post("/api/workbench/workflows", json=workflow)
    assert created.status_code == 201
    assert created.json()["id"] == "custom_mr_blackbox"

    listed = await workbench_client.get("/api/workbench/workflows")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == ["custom_mr_blackbox"]

    loaded = await workbench_client.get("/api/workbench/workflows/custom_mr_blackbox")
    assert loaded.status_code == 200
    assert loaded.json()["steps"][0]["mcp_profile"] == "codehub-readonly"

    frozen = await workbench_client.get("/api/workbench/workflows/custom_mr_blackbox/snapshot")
    assert frozen.status_code == 200
    assert frozen.json()["version"] == 1


async def test_workbench_workflow_preset_api(workbench_client):
    presets = await workbench_client.get("/api/workbench/workflow-presets")
    assert presets.status_code == 200
    preset_ids = {item["id"] for item in presets.json()["items"]}
    assert "mr_blackbox_test" in preset_ids
    assert "resource_leak_hunt" in preset_ids

    installed = await workbench_client.post(
        "/api/workbench/workflow-presets/mr_blackbox_test/install"
    )
    assert installed.status_code == 201
    assert installed.json()["id"] == "mr_blackbox_test"

    listed = await workbench_client.get("/api/workbench/workflows")
    assert [item["id"] for item in listed.json()] == ["mr_blackbox_test"]


async def test_workbench_semantic_library_api(workbench_client):
    created = await workbench_client.post(
        "/api/workbench/semantic-cases",
        json={
            "case_id": "TC_TLS_001",
            "feature": "NVMe TCP TLS",
            "module": "nvmf_tcp/transport/tls",
            "scenario": "certificate rejected during handshake",
            "test_level": "black_box",
            "terms": ["certificate", "handshake"],
            "assertion_style": "status + log",
            "status": "active",
        },
    )
    assert created.status_code == 201
    assert created.json()["case_id"] == "TC_TLS_001"

    search = await workbench_client.get(
        "/api/workbench/semantic-cases/search",
        params={
            "q": "certificate handshake",
            "module": "nvmf_tcp/transport/tls",
            "test_level": "black_box",
        },
    )
    assert search.status_code == 200
    assert [item["case_id"] for item in search.json()["items"]] == ["TC_TLS_001"]


async def test_workbench_memory_api(workbench_client):
    run_resp = await workbench_client.post(
        "/api/workbench/memory/runs",
        json={
            "workspace_id": "ws1",
            "repo_path": "E:/repo",
            "object_text": "nvme-tcp-tls",
            "workflow_id": "module_review",
            "status": "completed",
        },
    )
    assert run_resp.status_code == 201
    run_id = run_resp.json()["run_id"]

    evidence_resp = await workbench_client.post(
        "/api/workbench/memory/evidence",
        json={
            "run_id": run_id,
            "workspace_id": "ws1",
            "kind": "source_file",
            "subject_key": "nof/nvmf_tcp/transport/tls/tls.c",
            "status": "verified_local",
            "source": "ccr-code",
            "path": "nof/nvmf_tcp/transport/tls/tls.c",
            "reason": "validated source",
            "text": "nvme tcp tls transport source",
        },
    )
    assert evidence_resp.status_code == 201

    search = await workbench_client.get(
        "/api/workbench/memory/search",
        params={"q": "nvme tls", "workspace_id": "ws1"},
    )
    assert search.status_code == 200
    assert search.json()["items"][0]["subject_key"] == "nof/nvmf_tcp/transport/tls/tls.c"

    recent = await workbench_client.get(
        "/api/workbench/memory/recent",
        params={"workspace_id": "ws1"},
    )
    assert recent.status_code == 200
    assert recent.json()["items"][0]["object_text"] == "nvme-tcp-tls"


async def test_workbench_agent_run_harness_api(workbench_client):
    create = await workbench_client.post(
        "/api/workbench/agent-runs",
        json={
            "provider": "ccr-code",
            "command": ["ccr", "code"],
            "cwd": "E:/repo",
            "mcp_profile": "codehub-readonly",
            "workflow_snapshot": {"id": "mr_test_design", "version": 1},
            "task_bundle": {
                "task_id": "task-1",
                "required_artifacts": ["mr_snapshot.json", "diff.patch", "changed_files.json"],
            },
        },
    )
    assert create.status_code == 201
    run = create.json()
    assert run["provider"] == "ccr-code"
    assert run["mcp_profile"] == "codehub-readonly"

    raw = await workbench_client.post(
        f"/api/workbench/agent-runs/{run['run_id']}/raw-output",
        json={"stdout": "ok", "stderr": "token=secret-value"},
    )
    assert raw.status_code == 200

    artifact_dir = run["artifact_dir"]
    diff_text = "diff --git a/src/tls.c b/src/tls.c\n--- a/src/tls.c\n+++ b/src/tls.c\n"
    diff_sha = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
    from pathlib import Path

    root = Path(artifact_dir)
    (root / "mr_snapshot.json").write_text(
        json.dumps({
            "source": "agent_mcp",
            "mcp_profile": "codehub-readonly",
            "mr_url": "https://codehub.local/project/merge_requests/1",
            "project": "project",
            "mr_id": "1",
            "title": "TLS change",
            "source_branch": "feature",
            "target_branch": "main",
            "base_commit": "base",
            "head_commit": "head",
            "diff_sha256": diff_sha,
            "changed_files_count": 1,
        }),
        encoding="utf-8",
    )
    (root / "diff.patch").write_text(diff_text, encoding="utf-8")
    (root / "changed_files.json").write_text(
        json.dumps([{"path": "src/tls.c", "status": "modified"}]),
        encoding="utf-8",
    )

    validation = await workbench_client.post(
        f"/api/workbench/agent-runs/{run['run_id']}/validate-mr-artifacts",
        json={"required_artifacts": ["mr_snapshot.json", "diff.patch", "changed_files.json"]},
    )
    assert validation.status_code == 200
    assert validation.json()["status"] == "ok"
    assert validation.json()["provenance_status"] == "agent_mcp_provenance"
    assert "secret-value" not in (root / "raw_output.txt").read_text(encoding="utf-8")


async def test_workbench_agent_run_execute_api(workbench_client, tmp_path):
    output_file = tmp_path / "agent-output.txt"
    script = (
        "import json, pathlib, sys; "
        "payload=json.load(sys.stdin); "
        "pathlib.Path(sys.argv[1]).write_text(payload['task_bundle']['task_id'], encoding='utf-8'); "
        "print('done token=secret-value')"
    )
    create = await workbench_client.post(
        "/api/workbench/agent-runs",
        json={
            "provider": "local-python",
            "command": ["python", "-c", script, str(output_file)],
            "cwd": str(tmp_path),
            "workflow_snapshot": {"id": "wf"},
            "task_bundle": {"task_id": "task-execute"},
        },
    )
    assert create.status_code == 201
    run = create.json()

    executed = await workbench_client.post(
        f"/api/workbench/agent-runs/{run['run_id']}/execute",
        json={"timeout_sec": 10},
    )

    assert executed.status_code == 200
    body = executed.json()
    assert body["status"] == "completed"
    assert body["exit_code"] == 0
    assert output_file.read_text(encoding="utf-8") == "task-execute"
    from pathlib import Path

    raw_output = Path(run["artifact_dir"]) / "raw_output.txt"
    assert "done" in raw_output.read_text(encoding="utf-8")
    assert "secret-value" not in raw_output.read_text(encoding="utf-8")


async def test_workbench_task_scoped_agent_run_execute_api(workbench_client, tmp_path):
    from app.services.agent_run_harness import AgentRunHarness

    task_run_id = "task_run_unit"
    step_id = "collect_mr"
    artifact_dir = (
        tmp_path
        / "data"
        / "workbench"
        / "task_runs"
        / task_run_id
        / "agent_runs"
        / step_id
    )
    output_file = artifact_dir / "task-agent-output.txt"
    script = (
        "import json, pathlib, sys; "
        "payload=json.load(sys.stdin); "
        "pathlib.Path(sys.argv[1]).write_text(payload['run_id'], encoding='utf-8'); "
        "print('task scoped run complete')"
    )
    AgentRunHarness(artifact_dir).create_run(
        run_id=f"{task_run_id}_{step_id}",
        provider="local-python",
        command=["python", "-c", script, str(output_file)],
        cwd=str(tmp_path),
        workflow_snapshot={"id": "wf"},
        task_bundle={"task_id": task_run_id},
    )

    executed = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/agent-runs/{step_id}/execute",
        json={"timeout_sec": 10},
    )

    assert executed.status_code == 200
    assert executed.json()["status"] == "completed"
    assert output_file.read_text(encoding="utf-8") == f"{task_run_id}_{step_id}"


async def test_workbench_task_scoped_agent_run_validate_mr_artifacts_api(workbench_client, tmp_path):
    from app.services.agent_run_harness import AgentRunHarness

    task_run_id = "task_run_validate"
    step_id = "collect_mr"
    artifact_dir = (
        tmp_path
        / "data"
        / "workbench"
        / "task_runs"
        / task_run_id
        / "agent_runs"
        / step_id
    )
    diff_text = "diff --git a/src/tls.c b/src/tls.c\n--- a/src/tls.c\n+++ b/src/tls.c\n"
    diff_sha = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
    AgentRunHarness(artifact_dir).create_run(
        run_id=f"{task_run_id}_{step_id}",
        provider="local-agent",
        command=["python", "-c", "print('noop')"],
        cwd=str(tmp_path),
        workflow_snapshot={"id": "wf"},
        task_bundle={"task_id": task_run_id},
    )
    (artifact_dir / "mr_snapshot.json").write_text(
        json.dumps({
            "source": "agent_mcp",
            "mcp_profile": "codehub-readonly",
            "mr_url": "https://codehub.local/project/merge_requests/1",
            "project": "project",
            "mr_id": "1",
            "title": "TLS change",
            "source_branch": "feature",
            "target_branch": "main",
            "base_commit": "base",
            "head_commit": "head",
            "diff_sha256": diff_sha,
            "changed_files_count": 1,
        }),
        encoding="utf-8",
    )
    (artifact_dir / "diff.patch").write_text(diff_text, encoding="utf-8")
    (artifact_dir / "changed_files.json").write_text(
        json.dumps([{"path": "src/tls.c", "status": "modified"}]),
        encoding="utf-8",
    )

    validation = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/agent-runs/{step_id}/validate-mr-artifacts",
        json={"required_artifacts": ["mr_snapshot.json", "diff.patch", "changed_files.json"]},
    )

    assert validation.status_code == 200
    assert validation.json()["status"] == "ok"
    assert validation.json()["provenance_status"] == "agent_mcp_provenance"


async def test_workbench_task_run_list_get_and_materialize_evidence_api(workbench_client, tmp_path):
    workflow = {
        "id": "mr_test_design",
        "name": "MR test design",
        "version": 1,
        "inputs": [{"id": "mr_link", "type": "external_link", "resolver": "agent_mcp"}],
        "steps": [
            {
                "id": "collect_mr",
                "type": "agent_task",
                "provider": "local-agent",
                "mcp_profile": "codehub-readonly",
                "required_artifacts": ["mr_snapshot.json", "diff.patch", "changed_files.json"],
            }
        ],
        "outputs": [{"id": "report", "type": "markdown"}],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201
    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "mr_test_design",
            "workspace_id": "ws-materialize",
            "repo_path": str(tmp_path),
            "inputs": {"mr_link": "https://codehub.local/project/merge_requests/1"},
        },
    )
    assert prepared.status_code == 201
    task_run = prepared.json()
    task_run_id = task_run["task_run_id"]
    step_id = "collect_mr"

    listed = await workbench_client.get(
        "/api/workbench/task-runs",
        params={"workspace_id": "ws-materialize"},
    )
    assert listed.status_code == 200
    assert listed.json()["items"][0]["task_run_id"] == task_run_id

    loaded = await workbench_client.get(f"/api/workbench/task-runs/{task_run_id}")
    assert loaded.status_code == 200
    assert loaded.json()["workflow_id"] == "mr_test_design"

    artifact_dir = Path(task_run["artifact_dir"]) / "agent_runs" / step_id
    diff_text = "diff --git a/src/tls.c b/src/tls.c\n--- a/src/tls.c\n+++ b/src/tls.c\n"
    diff_sha = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
    (artifact_dir / "mr_snapshot.json").write_text(
        json.dumps({
            "source": "agent_mcp",
            "mcp_profile": "codehub-readonly",
            "mr_url": "https://codehub.local/project/merge_requests/1",
            "project": "project",
            "mr_id": "1",
            "title": "TLS change",
            "source_branch": "feature",
            "target_branch": "main",
            "base_commit": "base",
            "head_commit": "head",
            "diff_sha256": diff_sha,
            "changed_files_count": 1,
        }),
        encoding="utf-8",
    )
    (artifact_dir / "diff.patch").write_text(diff_text, encoding="utf-8")
    (artifact_dir / "changed_files.json").write_text(
        json.dumps([{"path": "src/tls.c", "status": "modified"}]),
        encoding="utf-8",
    )

    materialized = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/agent-runs/{step_id}/materialize-evidence",
        json={
            "required_artifacts": ["mr_snapshot.json", "diff.patch", "changed_files.json"],
            "object_text": "MR 1 TLS change",
        },
    )

    assert materialized.status_code == 200
    body = materialized.json()
    assert body["status"] == "ok"
    assert body["evidence_count"] >= 3

    search = await workbench_client.get(
        "/api/workbench/memory/search",
        params={"q": "src/tls.c", "workspace_id": "ws-materialize"},
    )
    assert search.status_code == 200
    assert search.json()["items"][0]["kind"] == "changed_file"
    assert search.json()["items"][0]["subject_key"] == "src/tls.c"


async def test_workbench_task_run_execute_workflow_api(workbench_client, tmp_path, monkeypatch):
    from app.config import settings

    script_path = tmp_path / "agent_write_result.py"
    script_path.write_text(
        "import pathlib, os\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "(root/'result.json').write_text('{\"ok\": true}', encoding='utf-8')\n"
        "print('workflow done token=secret-value')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow = {
        "id": "simple_agent_workflow",
        "name": "Simple agent workflow",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [
            {
                "id": "discover",
                "type": "agent_task",
                "provider": "local-python",
                "required_artifacts": ["result.json"],
            }
        ],
        "outputs": [{"id": "result", "type": "json", "artifact": "result.json"}],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201
    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "simple_agent_workflow",
            "workspace_id": "ws-execute",
            "repo_path": str(tmp_path),
            "inputs": {"module": "nvme-tcp-tls"},
        },
    )
    task_run_id = prepared.json()["task_run_id"]

    executed = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/execute",
        json={"timeout_sec": 10},
    )

    assert executed.status_code == 200
    body = executed.json()
    assert body["status"] == "completed"
    assert body["step_results"][0]["step_id"] == "discover"
    assert body["step_results"][0]["validation"]["status"] == "ok"
    assert body["outputs"][0]["id"] == "result"
    assert body["outputs"][0]["status"] == "ok"
    assert body["outputs"][0]["from"] == "discover"
    assert body["outputs"][0]["artifact"] == "result.json"
    artifact_dir = Path(prepared.json()["artifact_dir"])
    assert (artifact_dir / "workflow_execution.json").exists()
    assert (artifact_dir / "workflow_outputs.json").exists()
    assert "secret-value" not in (
        artifact_dir / "agent_runs" / "discover" / "raw_output.txt"
    ).read_text(encoding="utf-8")


async def test_workbench_prepare_task_run_api(workbench_client):
    workflow = {
        "id": "mr_test_design",
        "name": "MR test design",
        "version": 1,
        "inputs": [{"id": "mr_link", "type": "external_link", "resolver": "agent_mcp"}],
        "steps": [
            {
                "id": "collect_mr",
                "type": "agent_task",
                "provider": "claude-code",
                "goal": "mr_context_collect",
                "mcp_profile": "codehub-readonly",
                "required_artifacts": ["mr_snapshot.json", "diff.patch", "changed_files.json"],
            }
        ],
        "outputs": [{"id": "report", "type": "markdown"}],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201

    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "mr_test_design",
            "workspace_id": "ws1",
            "repo_path": "E:/repo",
            "inputs": {"mr_link": "https://codehub.local/project/merge_requests/1"},
        },
    )

    assert prepared.status_code == 201
    body = prepared.json()
    assert body["workflow_snapshot"]["id"] == "mr_test_design"
    assert body["task_bundle"]["inputs"]["mr_link"].startswith("https://codehub.local/")
    assert body["agent_runs"][0]["step_id"] == "collect_mr"


async def test_workbench_task_run_artifacts_api_lists_audit_files(workbench_client, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("Prefer fast-context first.\n", encoding="utf-8")
    workflow = {
        "id": "artifact_audit_workflow",
        "name": "Artifact audit workflow",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [{"id": "discover", "type": "agent_task", "provider": "claude-code"}],
        "outputs": [{"id": "report", "type": "markdown"}],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201
    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "artifact_audit_workflow",
            "workspace_id": "ws-artifacts",
            "repo_path": str(repo),
            "inputs": {"module": "lib/thread/thread.c"},
        },
    )
    task_run_id = prepared.json()["task_run_id"]

    artifacts = await workbench_client.get(f"/api/workbench/task-runs/{task_run_id}/artifacts")

    assert artifacts.status_code == 200
    body = artifacts.json()
    paths = {item["relative_path"]: item for item in body["artifacts"]}
    assert paths["task_bundle.json"]["sha256"]
    assert paths["agent_instructions.json"]["kind"] == "agent_instructions"
    assert paths["agent_runs/discover/task_bundle.json"]["kind"] == "agent_task_bundle"


async def test_workbench_prepare_task_run_api_injects_memory_and_semantics(workbench_client, tmp_path):
    assert (await workbench_client.post(
        "/api/workbench/memory/runs",
        json={
            "run_id": "run-context-prev",
            "workspace_id": "ws-context",
            "repo_path": str(tmp_path),
            "object_text": "nvme tcp tls",
            "workflow_id": "module_analysis",
            "status": "completed",
        },
    )).status_code == 201
    assert (await workbench_client.post(
        "/api/workbench/memory/evidence",
        json={
            "run_id": "run-context-prev",
            "workspace_id": "ws-context",
            "kind": "source_file",
            "subject_key": "nof/nvmf_tcp/transport/tls/tls.c",
            "status": "verified_local",
            "source": "fast-context",
            "path": "nof/nvmf_tcp/transport/tls/tls.c",
            "reason": "validated source",
            "text": "nvme tcp tls handshake cleanup",
        },
    )).status_code == 201
    assert (await workbench_client.post(
        "/api/workbench/semantic-cases",
        json={
            "case_id": "TC_CONTEXT_TLS",
            "feature": "NVMe TCP TLS",
            "module": "nvmf_tcp",
            "scenario": "TLS handshake failure releases connection",
            "terms": ["TLS negotiation", "connection release"],
            "tags": ["black_box"],
            "test_level": "black_box",
        },
    )).status_code == 201
    workflow = {
        "id": "context_injected_workflow",
        "name": "Context injected workflow",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [{"id": "design", "type": "agent_task", "provider": "claude-code"}],
        "outputs": [{"id": "cases", "type": "markdown"}],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201

    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "context_injected_workflow",
            "workspace_id": "ws-context",
            "repo_path": str(tmp_path),
            "inputs": {"module": "nvme tcp tls"},
        },
    )

    assert prepared.status_code == 201
    context_bundle = prepared.json()["task_bundle"]["context_bundle"]
    assert context_bundle["evidence"][0]["subject_key"] == "nof/nvmf_tcp/transport/tls/tls.c"
    assert context_bundle["semantic_cases"][0]["case_id"] == "TC_CONTEXT_TLS"
