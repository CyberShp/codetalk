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


async def test_workbench_provider_capabilities_matrix_api(workbench_client, monkeypatch):
    monkeypatch.setattr(settings, "claude_code_command", "ccr code")
    monkeypatch.setattr(settings, "claude_code_fallback_commands", ["claude"])
    monkeypatch.setattr(settings, "claude_code_mcp_profiles", ["codehub-readonly"])
    monkeypatch.setattr(settings, "opencode_command", "opencode")
    monkeypatch.setattr(settings, "fast_context_enabled", True)
    monkeypatch.setattr(settings, "fast_context_backend_bridge_enabled", False)
    monkeypatch.setattr(
        settings,
        "external_agent_custom_providers",
        [
            {
                "id": "corp-agent",
                "command": "corp-agent run",
                "supports_mcp": True,
                "mcp_profiles": ["codehub"],
                "prompt_transport": "stdin",
            }
        ],
    )

    resp = await workbench_client.get("/api/workbench/provider-capabilities")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    by_id = {item["provider"]: item for item in body["providers"]}

    assert by_id["claude-code"]["owner"] == "agent_cli"
    assert by_id["claude-code"]["command"] == ["ccr", "code"]
    assert by_id["claude-code"]["fallback_commands"] == [["claude"]]
    assert by_id["claude-code"]["capabilities"]["supports_mcp"] is True
    assert by_id["claude-code"]["capabilities"]["mcp_profiles"] == ["codehub-readonly"]
    assert by_id["claude-code"]["diagnostics"]["health_endpoint"] == "/api/tools/claude-code/health"
    assert by_id["claude-code"]["diagnostics"]["startup_probe_endpoint"] == "/api/tools/claude-code/startup-probe"
    assert by_id["claude-code"]["diagnostics"]["configured_command_text"] == "ccr code"
    assert by_id["claude-code"]["diagnostics"]["fallback_command_texts"] == ["claude"]
    assert by_id["claude-code"]["diagnostics"]["prompt_transport"] == "claude_print_arg"
    assert by_id["claude-code"]["diagnostics"]["startup_probe_transport"] == "claude_print_arg"
    assert by_id["claude-code"]["diagnostics"]["mcp_credentials_owner"] == "agent_cli"
    recipe = by_id["claude-code"]["diagnostics"]["probe_recipe"]
    assert recipe["startup_probe_http"] == "POST /api/tools/claude-code/startup-probe?repo_path=<repo_path>"
    assert recipe["backend_command"] == "ccr code"
    assert recipe["fallback_commands"] == ["claude"]
    assert recipe["command_env"] == "CLAUDE_CODE_COMMAND"
    assert "CCR_CONFIG_PATH" in recipe["environment_checks"]
    assert "PowerShell profile" in by_id["claude-code"]["diagnostics"]["troubleshooting"][0]
    assert "ccr code" in by_id["claude-code"]["diagnostics"]["manual_probe_command"]

    assert by_id["corp-agent"]["owner"] == "agent_cli"
    assert by_id["corp-agent"]["command"] == ["corp-agent", "run"]
    assert by_id["corp-agent"]["capabilities"]["prompt_transport"] == "stdin"
    assert by_id["corp-agent"]["diagnostics"]["startup_probe_transport"] == "stdin"
    assert by_id["corp-agent"]["diagnostics"]["health_endpoint"] == "/api/tools/corp-agent/health"

    assert by_id["fast-context"]["owner"] == "codetalk_mcp_bridge"
    assert by_id["fast-context"]["status"] == "bridge_disabled"
    assert by_id["fast-context"]["non_blocking"] is True
    assert "continues" in by_id["fast-context"]["unavailable_behavior"]
    assert by_id["fast-context"]["diagnostics"]["codetalk_callable"] is False
    assert "Agent CLIs may still call their own MCP" in by_id["fast-context"]["diagnostics"]["credential_boundary"]

    assert by_id["local-search"]["owner"] == "codetalk_builtin"
    assert by_id["local-search"]["status"] == "available"
    assert by_id["local-search"]["codetalk_callable"] is True
    assert by_id["local-search"]["agent_owned"] is False

    assert by_id["gitnexus"]["owner"] == "codetalk_index"
    assert by_id["gitnexus"]["non_blocking"] is True
    assert by_id["gitnexus"]["capabilities"]["supports_source_discovery"] is True
    assert by_id["gitnexus"]["diagnostics"]["startup_probe_endpoint"] == "/api/tools/gitnexus/startup-probe"

    assert by_id["cgc"]["owner"] == "codetalk_index"
    assert by_id["cgc"]["capabilities"]["supports_call_graph"] is True

    assert by_id["evidence-memory"]["owner"] == "codetalk_memory"
    assert by_id["evidence-memory"]["capabilities"]["supports_source_slices"] is True

    assert by_id["semantic-library"]["owner"] == "codetalk_memory"


async def test_workbench_provider_capabilities_include_agent_launch_resolution(
    workbench_client,
    monkeypatch,
):
    monkeypatch.setattr(settings, "claude_code_command", "ccr code")
    monkeypatch.setattr(settings, "claude_code_fallback_commands", ["claude"])
    monkeypatch.setattr(settings, "opencode_command", "")
    monkeypatch.setattr(settings, "external_agent_custom_providers", [])

    def fake_health(provider, command, fallback_commands=None):
        assert provider == "claude-code"
        assert command == "ccr code"
        assert fallback_commands == ["claude"]
        return {
            "provider": provider,
            "status": "available",
            "configured_command": "claude",
            "command": "C:/tools/claude.cmd -p --output-format json",
            "path": "C:/tools/claude.cmd",
            "launch_kind": "exec",
            "used_fallback": True,
            "reason": "primary command unavailable; using fallback: claude",
            "attempts": [
                {
                    "command": "ccr code",
                    "status": "unavailable",
                    "reason": "command not found: ccr",
                    "launch_kind": "exec",
                },
                {
                    "command": "claude",
                    "status": "available",
                    "path": "C:/tools/claude.cmd",
                    "launch_kind": "exec",
                },
            ],
        }

    monkeypatch.setattr(
        "app.services.workbench_task_run.check_provider_health",
        fake_health,
        raising=False,
    )

    resp = await workbench_client.get("/api/workbench/provider-capabilities")

    assert resp.status_code == 200
    by_id = {item["provider"]: item for item in resp.json()["providers"]}
    resolution = by_id["claude-code"]["diagnostics"]["command_resolution"]
    assert resolution["status"] == "available"
    assert resolution["configured_command"] == "claude"
    assert resolution["used_fallback"] is True
    assert resolution["launch_kind"] == "exec"
    assert resolution["reason"] == "primary command unavailable; using fallback: claude"
    assert resolution["attempt_count"] == 2
    assert resolution["attempts"][0]["reason"] == "command not found: ccr"
    assert by_id["semantic-library"]["capabilities"]["supports_black_box_terms"] is True


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


async def test_workbench_workflow_response_includes_soft_audit_warnings(workbench_client):
    response = await workbench_client.post(
        "/api/workbench/workflows",
        json={
            "id": "thin_custom_workflow",
            "name": "Thin custom workflow",
            "version": 1,
            "inputs": [{"id": "mr_link", "type": "mr_link", "resolver": "agent_mcp"}],
            "steps": [{"id": "ask_agent", "type": "agent_task", "provider": "claude-code"}],
            "outputs": [{"id": "result", "type": "json", "from": "ask_agent"}],
        },
    )

    assert response.status_code == 201
    audit = response.json()["audit"]
    warning_codes = {item["code"] for item in audit["warnings"]}
    assert "agent_task_missing_required_artifacts" in warning_codes
    assert "json_output_missing_schema" in warning_codes
    assert "agent_mcp_input_without_mcp_step" in warning_codes


async def test_workbench_input_file_upload_api_returns_prepare_payload(workbench_client):
    resp = await workbench_client.post(
        "/api/workbench/input-files/upload",
        files={"file": ("requirements.md", b"# Requirements\nTLS must fail closed\n", "text/markdown")},
        data={"input_id": "requirements_doc"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "workbench_input_upload"
    assert body["input_id"] == "requirements_doc"
    assert body["filename"] == "requirements.md"
    assert body["size"] == len(b"# Requirements\nTLS must fail closed\n")
    assert body["sha256"]
    assert body["input_payload"] == {"path": body["path"]}
    assert Path(body["path"]).exists()
    assert Path(body["path"]).read_text(encoding="utf-8").startswith("# Requirements")


async def test_workbench_semantic_library_bulk_import_api(workbench_client):
    imported = await workbench_client.post(
        "/api/workbench/semantic-cases/import",
        json={
            "source_ref": "feature_cases/nvmf_tls.json",
            "defaults": {
                "feature": "NVMe TCP TLS",
                "module": "nvmf_tcp/transport/tls",
                "test_level": "black_box",
            },
            "cases": [
                {
                    "case_id": "TC_TLS_BULK_001",
                    "scenario": "TLS certificate is rejected",
                    "terms": ["certificate", "handshake"],
                },
                {"scenario": "missing id"},
            ],
        },
    )

    assert imported.status_code == 201
    body = imported.json()
    assert body["imported_count"] == 1
    assert body["rejected_count"] == 1
    assert body["imported"][0]["case_id"] == "TC_TLS_BULK_001"
    assert body["rejected"][0]["reason"] == "case_id is required"

    search = await workbench_client.get(
        "/api/workbench/semantic-cases/search",
        params={
            "q": "certificate handshake",
            "module": "nvmf_tcp/transport/tls",
            "test_level": "black_box",
        },
    )
    assert [item["case_id"] for item in search.json()["items"]] == ["TC_TLS_BULK_001"]
    assert search.json()["items"][0]["source_ref"] == "feature_cases/nvmf_tls.json"


async def test_workbench_semantic_library_file_import_api(workbench_client):
    imported = await workbench_client.post(
        "/api/workbench/semantic-cases/import-file",
        data={
            "defaults_json": json.dumps({
                "feature": "NVMe TCP TLS",
                "module": "nvmf_tcp/transport/tls",
                "test_level": "black_box",
            }),
        },
        files={
            "file": (
                "tls_cases.csv",
                "case_id,scenario,terms\nTC_TLS_UPLOAD,TLS upload import,tls;upload\n",
                "text/csv",
            )
        },
    )

    assert imported.status_code == 201
    body = imported.json()
    assert body["source_ref"] == "tls_cases.csv"
    assert body["imported_count"] == 1
    assert body["imported"][0]["case_id"] == "TC_TLS_UPLOAD"

    search = await workbench_client.get(
        "/api/workbench/semantic-cases/search",
        params={
            "q": "upload",
            "module": "nvmf_tcp/transport/tls",
            "test_level": "black_box",
        },
    )
    assert search.status_code == 200
    assert search.json()["items"][0]["case_id"] == "TC_TLS_UPLOAD"


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
    assert body["provider_diagnostics"]["provider"] == "local-python"
    assert body["provider_diagnostics"]["health_status"]
    assert body["provider_diagnostics"]["artifact"] == "provider_diagnostics.json"
    assert body["provider_diagnostics"]["command_resolution_source"] == "configured_command"
    assert body["provider_diagnostics"]["command_resolution_reason"] == "ad_hoc_command_preserved"
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
    body = executed.json()
    assert body["status"] == "completed"
    assert body["provider_diagnostics"]["provider"] == "local-python"
    assert body["provider_diagnostics"]["health_status"]
    assert body["provider_diagnostics"]["command_resolution_source"] == "configured_command"
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
    assert body["step_results"][0]["provider_diagnostics"]["provider"] == "local-python"
    assert body["step_results"][0]["provider_diagnostics"]["health_status"]
    assert (
        body["step_results"][0]["provider_diagnostics"]["command_resolution_source"]
        == "provider_health"
    )
    assert body["step_results"][0]["validation"]["accepted_artifact_details"][0]["artifact"] == (
        "result.json"
    )
    assert body["step_results"][0]["validation"]["accepted_artifact_details"][0]["sha256"]
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


async def test_workbench_task_run_materialize_workflow_outputs_api(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    from app.config import settings

    script_path = tmp_path / "agent_write_cases.py"
    script_path.write_text(
        "import pathlib, os\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "(root/'cases.md').write_text('TLS negotiation black-box case', encoding='utf-8')\n"
        "print('cases ready')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow = {
        "id": "output_memory_workflow",
        "name": "Output memory workflow",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [
            {
                "id": "design",
                "type": "agent_task",
                "provider": "local-python",
                "required_artifacts": ["cases.md"],
            }
        ],
        "outputs": [{"id": "cases", "type": "markdown", "from": "design", "artifact": "cases.md"}],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201
    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "output_memory_workflow",
            "workspace_id": "ws-output-memory",
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
    assert executed.json()["outputs"][0]["status"] == "ok"

    materialized = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/materialize-outputs"
    )

    assert materialized.status_code == 200
    body = materialized.json()
    assert body["status"] == "ok"
    assert body["evidence_count"] == 1
    materialization_artifact = (
        Path(prepared.json()["artifact_dir"]) / "workflow_output_materialization.json"
    )
    assert materialization_artifact.exists()
    materialization = json.loads(materialization_artifact.read_text(encoding="utf-8"))
    assert materialization["task_run_id"] == task_run_id
    assert materialization["workflow_outputs_artifact"]["output_count"] == 1
    assert materialization["workflow_outputs_artifact"]["sha256"]
    assert materialization["evidence_ids"] == body["evidence_ids"]
    artifacts = await workbench_client.get(f"/api/workbench/task-runs/{task_run_id}/artifacts")
    assert artifacts.status_code == 200
    paths = {item["relative_path"]: item for item in artifacts.json()["artifacts"]}
    assert (
        paths["workflow_output_materialization.json"]["kind"]
        == "workflow_output_materialization"
    )
    search = await workbench_client.get(
        "/api/workbench/memory/search",
        params={"q": "TLS negotiation", "workspace_id": "ws-output-memory"},
    )
    assert search.status_code == 200
    item = search.json()["items"][0]
    assert item["kind"] == "workflow_output"
    assert item["subject_key"].endswith("/cases")


async def test_workbench_materialize_workflow_outputs_preserves_rejection_details(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    from app.config import settings

    script_path = tmp_path / "agent_bad_output.py"
    script_path.write_text(
        "import json, pathlib, os\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "(root/'scope.json').write_text(json.dumps({'wrong': []}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow = {
        "id": "output_rejection_details",
        "name": "Output rejection details",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [
            {
                "id": "discover",
                "type": "agent_task",
                "provider": "local-python",
                "required_artifacts": ["scope.json"],
            }
        ],
        "outputs": [
            {
                "id": "scope",
                "type": "json",
                "from": "discover",
                "artifact": "scope.json",
                "schema": {"type": "object", "required": ["files"]},
            }
        ],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201
    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "output_rejection_details",
            "workspace_id": "ws-output-rejected",
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
    assert executed.json()["outputs"][0]["status"] == "invalid"

    materialized = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/materialize-outputs"
    )

    assert materialized.status_code == 200
    body = materialized.json()
    assert body["status"] == "partial"
    assert body["evidence_count"] == 0
    assert body["rejected_outputs"] == [
        {
            "output": "scope",
            "reason": "output_not_ok",
            "output_status": "invalid",
            "output_reason": "schema_validation_failed",
            "artifact": "scope.json",
            "path": executed.json()["outputs"][0]["path"],
            "from": "discover",
            "schema_errors": ["missing required field: files"],
        }
    ]
    materialization = json.loads(
        (Path(prepared.json()["artifact_dir"]) / "workflow_output_materialization.json")
        .read_text(encoding="utf-8")
    )
    assert materialization["rejected_outputs"] == body["rejected_outputs"]


async def test_workbench_materialize_changed_files_output_as_structured_memory(
    workbench_client,
    tmp_path,
):
    patch_file = tmp_path / "tls.patch"
    patch_file.write_text(
        "diff --git a/src/tls.c b/src/tls.c\n"
        "--- a/src/tls.c\n"
        "+++ b/src/tls.c\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n",
        encoding="utf-8",
    )
    workflow = {
        "id": "changed_files_memory_workflow",
        "name": "Changed files memory workflow",
        "version": 1,
        "inputs": [{"id": "patch_diff", "type": "patch", "required": True}],
        "steps": [{"id": "parse_patch", "type": "diff_parse"}],
        "outputs": [
            {
                "id": "changed_files",
                "type": "json",
                "from": "parse_patch",
                "artifact": "changed_files.json",
            }
        ],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201
    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "changed_files_memory_workflow",
            "workspace_id": "ws-changed-files-memory",
            "repo_path": str(tmp_path),
            "inputs": {"patch_diff": {"path": str(patch_file)}},
        },
    )
    task_run_id = prepared.json()["task_run_id"]
    executed = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/execute",
        json={"timeout_sec": 10},
    )
    assert executed.status_code == 200
    assert executed.json()["outputs"][0]["status"] == "ok"

    materialized = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/materialize-outputs"
    )

    assert materialized.status_code == 200
    body = materialized.json()
    assert body["status"] == "ok"
    assert body["evidence_count"] == 2
    search = await workbench_client.get(
        "/api/workbench/memory/search",
        params={"q": "src/tls.c", "workspace_id": "ws-changed-files-memory"},
    )
    assert search.status_code == 200
    items = search.json()["items"]
    changed = [item for item in items if item["kind"] == "changed_file"]
    assert changed
    assert changed[0]["subject_key"] == "src/tls.c"
    assert changed[0]["provenance"]["output_id"] == "changed_files"


async def test_workbench_materialize_rejects_changed_files_without_repo_or_patch_evidence(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    from app.config import settings

    script_path = tmp_path / "agent_changed_files.py"
    script_path.write_text(
        "import json, os, pathlib\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "diff='diff --git a/src/tls.c b/src/tls.c\\n--- a/src/tls.c\\n+++ b/src/tls.c\\n'\n"
        "(root/'diff.patch').write_text(diff, encoding='utf-8')\n"
        "(root/'changed_files.json').write_text(json.dumps([\n"
        "  {'path':'src/tls.c','status':'modified'},\n"
        "  {'path':'src/not_in_patch.c','status':'modified'}\n"
        "]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow = {
        "id": "changed_files_validation_workflow",
        "name": "Changed files validation workflow",
        "version": 1,
        "inputs": [{"id": "mr_link", "type": "mr_link", "resolver": "agent_mcp"}],
        "steps": [
            {
                "id": "collect",
                "type": "agent_task",
                "provider": "local-python",
                "required_artifacts": ["diff.patch", "changed_files.json"],
            }
        ],
        "outputs": [
            {
                "id": "changed_files",
                "type": "json",
                "from": "collect",
                "artifact": "changed_files.json",
            }
        ],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201
    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "changed_files_validation_workflow",
            "workspace_id": "ws-changed-files-validated",
            "repo_path": str(tmp_path),
            "inputs": {"mr_link": "https://codehub.local/project/merge_requests/1"},
        },
    )
    task_run_id = prepared.json()["task_run_id"]
    executed = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/execute",
        json={"timeout_sec": 10},
    )
    assert executed.status_code == 200
    assert executed.json()["status"] == "completed"

    materialized = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/materialize-outputs"
    )

    assert materialized.status_code == 200
    body = materialized.json()
    assert body["status"] == "partial"
    assert any(
        item["reason"] == "changed_file_not_in_repo_or_patch_snapshot"
        and item["path"] == "src/not_in_patch.c"
        for item in body["rejected_outputs"]
    )
    search = await workbench_client.get(
        "/api/workbench/memory/search",
        params={"q": "src/tls.c", "workspace_id": "ws-changed-files-validated"},
    )
    changed = [item for item in search.json()["items"] if item["kind"] == "changed_file"]
    assert [item["subject_key"] for item in changed] == ["src/tls.c"]


async def test_workbench_materialize_uncovered_functions_output_as_structured_memory(
    workbench_client,
    tmp_path,
):
    coverage_file = tmp_path / "coverage.info"
    coverage_file.write_text(
        "TN:\n"
        "SF:nof/nvmf_tcp/transport/tls/tls.c\n"
        "FN:42,nvmf_tcp_tls_handshake\n"
        "FNDA:0,nvmf_tcp_tls_handshake\n"
        "FN:88,nvmf_tcp_tls_cleanup\n"
        "FNDA:4,nvmf_tcp_tls_cleanup\n"
        "end_of_record\n",
        encoding="utf-8",
    )
    workflow = {
        "id": "coverage_gap_memory_workflow",
        "name": "Coverage gap memory workflow",
        "version": 1,
        "inputs": [{"id": "coverage_report", "type": "coverage_report", "required": True}],
        "steps": [{"id": "parse_coverage", "type": "coverage_parse"}],
        "outputs": [
            {
                "id": "uncovered_functions",
                "type": "json",
                "from": "parse_coverage",
                "artifact": "uncovered_functions.json",
            }
        ],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201
    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "coverage_gap_memory_workflow",
            "workspace_id": "ws-coverage-gap-memory",
            "repo_path": str(tmp_path),
            "inputs": {"coverage_report": {"path": str(coverage_file)}},
        },
    )
    task_run_id = prepared.json()["task_run_id"]
    executed = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/execute",
        json={"timeout_sec": 10},
    )
    assert executed.status_code == 200
    assert executed.json()["outputs"][0]["status"] == "ok"

    materialized = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/materialize-outputs"
    )

    assert materialized.status_code == 200
    body = materialized.json()
    assert body["status"] == "ok"
    assert body["evidence_count"] == 2
    search = await workbench_client.get(
        "/api/workbench/memory/search",
        params={"q": "nvmf_tcp_tls_handshake", "workspace_id": "ws-coverage-gap-memory"},
    )
    assert search.status_code == 200
    items = search.json()["items"]
    gaps = [item for item in items if item["kind"] == "coverage_gap"]
    assert gaps
    assert gaps[0]["subject_key"] == "nof/nvmf_tcp/transport/tls/tls.c:nvmf_tcp_tls_handshake"
    assert gaps[0]["symbol"] == "nvmf_tcp_tls_handshake"
    assert gaps[0]["provenance"]["line_start"] == 42


async def test_workbench_materialize_source_scope_output_as_structured_memory(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    from app.config import settings

    source_file = tmp_path / "nof" / "nvmf_tcp" / "transport" / "tls" / "tls.c"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("int nvmf_tcp_tls_handshake(void) { return 0; }\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("not source evidence\n", encoding="utf-8")
    script_path = tmp_path / "agent_scope.py"
    script_path.write_text(
        "import json, os, pathlib\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "payload={\n"
        "  'files': [\n"
        "    {'path':'nof/nvmf_tcp/transport/tls/tls.c','reason':'TLS transport source',"
        "     'symbols':[{'name':'nvmf_tcp_tls_handshake','line_start':1}]},\n"
        "    {'path':'missing/tls.c','reason':'agent guessed path'},\n"
        "    {'path':'README.md','reason':'not source'}\n"
        "  ]\n"
        "}\n"
        "(root/'source_scope.json').write_text(json.dumps(payload), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow = {
        "id": "source_scope_memory_workflow",
        "name": "Source scope memory workflow",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text", "required": True}],
        "steps": [
            {
                "id": "discover",
                "type": "agent_task",
                "provider": "local-python",
                "required_artifacts": ["source_scope.json"],
            }
        ],
        "outputs": [
            {
                "id": "source_scope",
                "type": "json",
                "from": "discover",
                "artifact": "source_scope.json",
            }
        ],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201
    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "source_scope_memory_workflow",
            "workspace_id": "ws-source-scope-memory",
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
    assert executed.json()["outputs"][0]["status"] == "ok"

    materialized = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/materialize-outputs"
    )

    assert materialized.status_code == 200
    body = materialized.json()
    assert body["status"] == "ok"
    assert body["evidence_count"] == 3
    source_search = await workbench_client.get(
        "/api/workbench/memory/search",
        params={"q": "TLS transport source", "workspace_id": "ws-source-scope-memory"},
    )
    assert source_search.status_code == 200
    source_items = source_search.json()["items"]
    source_files = [item for item in source_items if item["kind"] == "source_file"]
    assert source_files
    assert source_files[0]["subject_key"] == "nof/nvmf_tcp/transport/tls/tls.c"
    assert source_files[0]["status"] == "verified_output"
    slices = await workbench_client.get(
        f"/api/workbench/memory/evidence/{source_files[0]['evidence_id']}/source-slices"
    )
    assert slices.status_code == 200
    slice_items = slices.json()["items"]
    assert slice_items
    assert slice_items[0]["file_path"] == "nof/nvmf_tcp/transport/tls/tls.c"
    assert slice_items[0]["start_line"] == 1
    assert "nvmf_tcp_tls_handshake" in slice_items[0]["excerpt"]
    assert slice_items[0]["sha256"]

    symbol_search = await workbench_client.get(
        "/api/workbench/memory/search",
        params={"q": "nvmf_tcp_tls_handshake", "workspace_id": "ws-source-scope-memory"},
    )
    assert symbol_search.status_code == 200
    symbol_items = symbol_search.json()["items"]
    symbols = [item for item in symbol_items if item["kind"] == "symbol"]
    assert symbols
    assert symbols[0]["path"] == "nof/nvmf_tcp/transport/tls/tls.c"
    assert symbols[0]["symbol"] == "nvmf_tcp_tls_handshake"
    rejected = [item for item in symbol_items if item["path"] == "README.md"]
    assert rejected == []


async def test_workbench_materialize_evidence_cards_output_as_structured_memory(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    from app.config import settings

    source_file = tmp_path / "src" / "tls.c"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("int nvmf_tcp_tls_cleanup(void) { return 0; }\n", encoding="utf-8")
    script_path = tmp_path / "agent_cards.py"
    script_path.write_text(
        "import json, os, pathlib\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "cards=[\n"
        "  {'card_id':'card_tls_cleanup','path':'src/tls.c','symbol':'nvmf_tcp_tls_cleanup',"
        "   'reason':'cleanup evidence','excerpt':'cleanup releases resources'},\n"
        "  {'card_id':'card_missing','path':'src/missing.c','symbol':'missing_symbol',"
        "   'reason':'bad evidence'}\n"
        "]\n"
        "(root/'evidence_cards.json').write_text(json.dumps(cards), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow = {
        "id": "evidence_cards_memory_workflow",
        "name": "Evidence cards memory workflow",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text", "required": True}],
        "steps": [
            {
                "id": "discover",
                "type": "agent_task",
                "provider": "local-python",
                "required_artifacts": ["evidence_cards.json"],
            }
        ],
        "outputs": [
            {
                "id": "evidence_cards",
                "type": "json",
                "from": "discover",
                "artifact": "evidence_cards.json",
            }
        ],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201
    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "evidence_cards_memory_workflow",
            "workspace_id": "ws-evidence-cards-memory",
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
    assert executed.json()["outputs"][0]["status"] == "ok"

    materialized = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/materialize-outputs"
    )

    assert materialized.status_code == 200
    body = materialized.json()
    assert body["status"] == "ok"
    assert body["evidence_count"] == 2
    search = await workbench_client.get(
        "/api/workbench/memory/search",
        params={"q": "cleanup releases resources", "workspace_id": "ws-evidence-cards-memory"},
    )
    assert search.status_code == 200
    cards = [item for item in search.json()["items"] if item["kind"] == "evidence_card"]
    assert cards
    assert cards[0]["subject_key"] == "card_tls_cleanup"
    assert cards[0]["path"] == "src/tls.c"
    assert cards[0]["symbol"] == "nvmf_tcp_tls_cleanup"


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


async def test_workbench_prepare_task_run_api_rejects_missing_required_input(workbench_client, tmp_path):
    workflow = {
        "id": "required_api_workflow",
        "name": "Required API workflow",
        "version": 1,
        "inputs": [{"id": "target_scope", "type": "free_text", "required": True}],
        "steps": [{"id": "render", "type": "report_render"}],
        "outputs": [{"id": "report", "type": "markdown"}],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201

    resp = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "required_api_workflow",
            "workspace_id": "ws-required",
            "repo_path": str(tmp_path),
            "inputs": {},
        },
    )

    assert resp.status_code == 422
    assert "required input target_scope is missing" in resp.json()["detail"]


async def test_workbench_task_run_artifacts_api_lists_audit_files(workbench_client, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("Prefer fast-context first.\n", encoding="utf-8")
    patch_plan = tmp_path / "patch-plan.md"
    patch_plan.write_text("# Patch plan\nUpdate TLS cleanup.\n", encoding="utf-8")
    workflow = {
        "id": "artifact_audit_workflow",
        "name": "Artifact audit workflow",
        "version": 1,
        "inputs": [
            {"id": "module", "type": "free_text"},
            {"id": "patch_plan", "type": "file"},
        ],
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
            "inputs": {
                "module": "lib/thread/thread.c",
                "patch_plan": {"path": str(patch_plan)},
            },
        },
    )
    task_run_id = prepared.json()["task_run_id"]

    artifacts = await workbench_client.get(f"/api/workbench/task-runs/{task_run_id}/artifacts")

    assert artifacts.status_code == 200
    body = artifacts.json()
    paths = {item["relative_path"]: item for item in body["artifacts"]}
    assert paths["task_bundle.json"]["sha256"]
    assert paths["input_snapshot.json"]["kind"] == "input_snapshot"
    assert paths["input_context.json"]["kind"] == "input_context"
    assert paths["inputs/patch_plan/file_metadata.json"]["kind"] == "input_file_metadata"
    assert paths["inputs/patch_plan/parsed_text.txt"]["kind"] == "input_parsed_text"
    assert paths["inputs/patch_plan/chunks.json"]["kind"] == "input_chunks"
    assert paths["inputs/patch_plan/original/patch-plan.md"]["kind"] == "input_original_file"
    assert paths["agent_instructions.json"]["kind"] == "agent_instructions"
    assert paths["workflow_contract.json"]["kind"] == "workflow_contract"
    assert paths["agent_mcp_requests.json"]["kind"] == "agent_mcp_requests"
    assert paths["context_discovery_decision.json"]["kind"] == "context_discovery_decision"
    assert paths["output_schemas_by_step.json"]["kind"] == "output_schemas"
    assert paths["memory_retrieval.json"]["kind"] == "memory_retrieval"
    assert paths["source_read_chain.json"]["kind"] == "source_read_chain"
    assert paths["evidence_consumption_trajectory.json"]["kind"] == (
        "evidence_consumption_trajectory"
    )
    assert paths["degraded_retrieval.json"]["kind"] == "degraded_retrieval"
    assert paths["agent_runs/discover/task_bundle.json"]["kind"] == "agent_task_bundle"


async def test_workbench_task_run_artifact_content_api_is_safe(workbench_client, tmp_path):
    workflow = {
        "id": "artifact_content_workflow",
        "name": "Artifact content workflow",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [{"id": "discover", "type": "agent_task", "provider": "claude-code"}],
        "outputs": [{"id": "report", "type": "markdown"}],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201
    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "artifact_content_workflow",
            "workspace_id": "ws-artifact-content",
            "repo_path": str(tmp_path),
            "inputs": {"module": "nvme-tcp-tls"},
        },
    )
    task_run_id = prepared.json()["task_run_id"]

    content = await workbench_client.get(
        f"/api/workbench/task-runs/{task_run_id}/artifacts/content/task_bundle.json"
    )

    assert content.status_code == 200
    body = content.json()
    assert body["relative_path"] == "task_bundle.json"
    assert body["kind"] == "task_bundle"
    assert body["sha256"]
    assert body["truncated"] is False
    assert "artifact_content_workflow" in body["content"]

    escaped = await workbench_client.get(
        f"/api/workbench/task-runs/{task_run_id}/artifacts/content/%2E%2E/outside.txt"
    )
    assert escaped.status_code == 400


async def test_workbench_task_run_artifacts_api_labels_agent_execution_input(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    from app.config import settings

    script_path = tmp_path / "agent_noop.py"
    script_path.write_text(
        "import json, sys\n"
        "json.load(sys.stdin)\n"
        "print('done')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow = {
        "id": "execution_input_audit_workflow",
        "name": "Execution input audit workflow",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [
            {"id": "discover", "type": "agent_task", "provider": "local-python"},
            {"id": "validate_evidence", "type": "evidence_validate"},
        ],
        "outputs": [{"id": "report", "type": "markdown"}],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201
    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "execution_input_audit_workflow",
            "workspace_id": "ws-execution-input",
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

    artifacts = await workbench_client.get(f"/api/workbench/task-runs/{task_run_id}/artifacts")

    paths = {item["relative_path"]: item for item in artifacts.json()["artifacts"]}
    execution_input = paths["agent_runs/discover/execution_input.json"]
    assert execution_input["kind"] == "agent_execution_input"
    assert "CODETALK_AGENT_READONLY" in execution_input["preview"]
    provider_diagnostics = paths["agent_runs/discover/provider_diagnostics.json"]
    assert provider_diagnostics["kind"] == "agent_provider_diagnostics"
    provider_diagnostics_content = await workbench_client.get(
        f"/api/workbench/task-runs/{task_run_id}/artifacts/content/agent_runs/discover/provider_diagnostics.json"
    )
    assert provider_diagnostics_content.status_code == 200
    assert "startup_probe_endpoint" in provider_diagnostics_content.json()["content"]
    assert (
        paths["steps/validate_evidence/evidence_validation.json"]["kind"]
        == "evidence_validation"
    )


async def test_workbench_task_run_artifacts_api_labels_failure_recovery(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    from app.config import settings

    script_path = tmp_path / "agent_fail.py"
    script_path.write_text(
        "import sys\n"
        "print('agent failed')\n"
        "sys.exit(3)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow = {
        "id": "failure_recovery_artifact_workflow",
        "name": "Failure recovery artifact workflow",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [
            {
                "id": "discover",
                "type": "agent_task",
                "provider": "local-python",
                "required_artifacts": ["source_scope.json"],
            }
        ],
        "outputs": [{"id": "scope", "type": "json", "from": "discover", "artifact": "source_scope.json"}],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201
    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "failure_recovery_artifact_workflow",
            "workspace_id": "ws-failure-recovery",
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
    assert executed.json()["step_results"][0]["failure_recovery"]["failure_kind"] == "agent_error"

    artifacts = await workbench_client.get(f"/api/workbench/task-runs/{task_run_id}/artifacts")

    paths = {item["relative_path"]: item for item in artifacts.json()["artifacts"]}
    recovery = paths["agent_runs/discover/failure_recovery.json"]
    assert recovery["kind"] == "agent_failure_recovery"
    lifecycle = paths["agent_runs/discover/agent_run_lifecycle.json"]
    assert lifecycle["kind"] == "agent_run_lifecycle"
    rerun_plan = paths["task_rerun_plan.json"]
    assert rerun_plan["kind"] == "task_rerun_plan"
    assert "agent_error" in lifecycle["preview"]
    assert "agent_error" in recovery["preview"]
    assert "needs_rerun" in rerun_plan["preview"]
    content = await workbench_client.get(
        f"/api/workbench/task-runs/{task_run_id}/artifacts/content/agent_runs/discover/failure_recovery.json"
    )
    assert content.status_code == 200
    assert content.json()["kind"] == "agent_failure_recovery"
    assert "source_scope.json" in content.json()["content"]
    lifecycle_content = await workbench_client.get(
        f"/api/workbench/task-runs/{task_run_id}/artifacts/content/agent_runs/discover/agent_run_lifecycle.json"
    )
    assert lifecycle_content.status_code == 200
    assert lifecycle_content.json()["kind"] == "agent_run_lifecycle"
    assert "failure_recovery" in lifecycle_content.json()["content"]
    rerun_content = await workbench_client.get(
        f"/api/workbench/task-runs/{task_run_id}/artifacts/content/task_rerun_plan.json"
    )
    assert rerun_content.status_code == 200
    assert rerun_content.json()["kind"] == "task_rerun_plan"
    assert "source_scope.json" in rerun_content.json()["content"]
    rerun_plan_response = await workbench_client.get(
        f"/api/workbench/task-runs/{task_run_id}/rerun-plan"
    )
    assert rerun_plan_response.status_code == 200
    rerun_plan = rerun_plan_response.json()
    assert rerun_plan["task_run_id"] == task_run_id
    assert rerun_plan["status"] == "needs_rerun"
    assert rerun_plan["steps"][0]["recommended_action"] == "rerun_agent_step"
    assert rerun_plan["steps"][0]["missing_artifacts"] == ["source_scope.json"]
    validation_response = await workbench_client.get(
        f"/api/workbench/task-runs/{task_run_id}/rerun-plan/validation"
    )
    assert validation_response.status_code == 200
    validation = validation_response.json()
    assert validation["task_run_id"] == task_run_id
    assert validation["status"] == "ready"
    assert validation["can_rerun"] is True
    assert validation["plan_status"] == "needs_rerun"
    assert {item["id"]: item["status"] for item in validation["checks"]} == {
        "task_run": "ok",
        "input_snapshot": "ok",
        "task_bundle": "ok",
        "workflow_snapshot": "ok",
        "repo_path": "ok",
    }
    assert validation["steps"][0]["step_id"] == "discover"
    assert validation["steps"][0]["status"] == "ready"
    assert validation["steps"][0]["artifact_dir_exists"] is True
    assert validation["steps"][0]["overwrite_risk_artifacts"][0] == {
        "artifact": "raw_output.txt",
        "exists": True,
    }
    rerun_response = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/rerun-plan/execute",
        json={"timeout_sec": 10},
    )
    assert rerun_response.status_code == 200
    rerun = rerun_response.json()
    assert rerun["status"] == "executed"
    assert rerun["validation_before"]["status"] == "ready"
    assert rerun["execution"]["task_run_id"] == task_run_id
    assert rerun["execution"]["status"] == "invalid"
    assert rerun["execution"]["step_results"][0]["failure_recovery"]["failure_kind"] == "agent_error"
    assert rerun["validation_after"]["status"] == "ready"


async def test_workbench_task_run_artifacts_api_labels_agent_turn_snapshots(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    from app.config import settings

    source = tmp_path / "src" / "tls.c"
    source.parent.mkdir()
    source.write_text("int nvmf_tcp_tls_handshake(void) { return 0; }\n", encoding="utf-8")
    script_path = tmp_path / "agent_turns.py"
    script_path.write_text(
        "import json, pathlib, sys\n"
        "payload=json.loads(sys.stdin.read())\n"
        "bundle=payload['task_bundle']\n"
        "root=pathlib.Path(payload['artifact_dir'])\n"
        "if not bundle.get('requested_source_slices'):\n"
        "    (root/'source_slice_requests.json').write_text(json.dumps({"
        "'need_source_slices':[{'file_path':'src/tls.c'}]}"
        "), encoding='utf-8')\n"
        "else:\n"
        "    (root/'source_scope.json').write_text(json.dumps({'files':[{'path':'src/tls.c'}]}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow = {
        "id": "turn_snapshot_audit_workflow",
        "name": "Turn snapshot audit workflow",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [
            {
                "id": "discover",
                "type": "agent_task",
                "provider": "local-python",
                "required_artifacts": ["source_scope.json"],
            }
        ],
        "outputs": [{"id": "scope", "type": "json", "from": "discover", "artifact": "source_scope.json"}],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201
    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "turn_snapshot_audit_workflow",
            "workspace_id": "ws-turn-snapshots",
            "repo_path": str(tmp_path),
            "inputs": {"module": "nvme tcp tls"},
        },
    )
    task_run_id = prepared.json()["task_run_id"]
    executed = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/execute",
        json={"timeout_sec": 10},
    )
    assert executed.status_code == 200

    artifacts = await workbench_client.get(f"/api/workbench/task-runs/{task_run_id}/artifacts")

    paths = {item["relative_path"]: item for item in artifacts.json()["artifacts"]}
    assert (
        paths["agent_runs/discover/turns/turn_1/execution_input.json"]["kind"]
        == "agent_turn_execution_input"
    )
    assert (
        paths["agent_runs/discover/turns/turn_1/provider_diagnostics.json"]["kind"]
        == "agent_turn_provider_diagnostics"
    )
    assert paths["agent_runs/discover/turns/turn_1/raw_output.txt"]["kind"] == "agent_turn_raw_output"
    assert paths["agent_runs/discover/turns/turn_2/task_bundle.json"]["kind"] == "agent_turn_task_bundle"
    assert paths["agent_runs/discover/turns/turn_2/source_slices.json"]["kind"] == "agent_turn_source_slices"
    content = await workbench_client.get(
        f"/api/workbench/task-runs/{task_run_id}/artifacts/content/"
        "agent_runs/discover/turns/turn_2/task_bundle.json"
    )
    assert content.status_code == 200
    assert content.json()["kind"] == "agent_turn_task_bundle"
    assert "requested_source_slices" in content.json()["content"]


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
