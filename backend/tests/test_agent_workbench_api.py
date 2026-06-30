from contextlib import asynccontextmanager
import hashlib
import json
import sys
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


async def test_workbench_workflow_draft_audit_api_reports_warnings_and_invalid(
    workbench_client,
):
    warning_workflow = {
        "id": "draft_warning",
        "name": "Draft warning",
        "version": 1,
        "inputs": [{"id": "mr_link", "type": "mr_link", "resolver": "agent_mcp"}],
        "steps": [{"id": "collect", "type": "agent_task", "provider": "claude-code"}],
        "outputs": [{"id": "findings", "type": "json", "from": "collect"}],
    }

    warning_resp = await workbench_client.post(
        "/api/workbench/workflows/audit-draft",
        json=warning_workflow,
    )

    assert warning_resp.status_code == 200
    warning_body = warning_resp.json()
    assert warning_body["status"] == "warning"
    assert warning_body["valid"] is True
    codes = {item["code"] for item in warning_body["warnings"]}
    assert "agent_task_missing_required_artifacts" in codes
    assert "json_output_missing_schema" in codes
    assert "agent_mcp_input_without_mcp_step" in codes

    invalid_resp = await workbench_client.post(
        "/api/workbench/workflows/audit-draft",
        json={
            "id": "bad",
            "name": "Bad",
            "steps": [{"id": "collect", "type": "unsupported_step"}],
        },
    )

    assert invalid_resp.status_code == 200
    invalid_body = invalid_resp.json()
    assert invalid_body["status"] == "invalid"
    assert invalid_body["valid"] is False
    assert "unsupported workflow step type" in invalid_body["error"]


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


async def test_workbench_workflow_capabilities_api_documents_custom_workflows(workbench_client):
    resp = await workbench_client.get("/api/workbench/workflow-capabilities")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "file" in body["input_types"]
    assert "file_set" in body["input_types"]
    assert "coverage_report" in body["input_types"]
    assert "mr_link" in body["input_types"]
    assert "agent_mcp" in body["input_resolvers"]
    assert "agent_task" in body["step_types"]
    assert "semantic_retrieve" in body["step_types"]
    assert "json" in body["output_types"]
    assert body["input_features"]["json_schema_validation"] is True
    assert body["output_features"]["json_schema_validation"] is True
    assert body["output_features"]["workflow_output_materialization"] is True
    assert body["agent_cli_features"]["agent_owned_mcp_credentials"] is True
    assert "jsonl" in body["semantic_library_import_formats"]
    assert body["artifact_contract"]["required_artifacts"] == "validated locally before outputs are accepted"


async def test_workbench_core_workflow_readiness_api_covers_builtin_scenarios(workbench_client):
    resp = await workbench_client.get("/api/workbench/core-workflow-readiness")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["summary"]["workflow_count"] == 4
    assert body["summary"]["missing_required"] == 0
    by_id = {item["id"]: item for item in body["workflows"]}
    assert set(by_id) == {
        "module_analysis",
        "resource_leak_hunt",
        "mr_blackbox_test",
        "patch_impact_review",
    }
    assert by_id["module_analysis"]["scenario"] == "module_analysis"
    assert by_id["resource_leak_hunt"]["scenario"] == "risk_hunt"
    assert by_id["mr_blackbox_test"]["agent_mcp_required"] is False
    assert by_id["mr_blackbox_test"]["required_artifacts"] == [
        "mr_snapshot.json",
        "diff.patch",
        "changed_files.json",
        "black_box_cases.json",
    ]
    assert by_id["mr_blackbox_test"]["agent_step_count"] == 0
    assert by_id["mr_blackbox_test"]["builtin_steps"] == [
        "collect_mr",
        "semantic_retrieve",
        "validate_mr_evidence",
        "render_blackbox_cases",
    ]
    assert by_id["module_analysis"]["agent_step_count"] == 0
    assert by_id["module_analysis"]["builtin_steps"] == [
        "discover_scope",
        "validate_evidence",
        "render_report",
    ]
    assert by_id["resource_leak_hunt"]["agent_step_count"] == 0
    assert by_id["resource_leak_hunt"]["builtin_steps"] == [
        "hunt_risks",
        "validate_evidence",
        "render_report",
    ]
    assert by_id["patch_impact_review"]["agent_step_count"] == 0
    assert by_id["patch_impact_review"]["builtin_steps"] == [
        "parse_patch",
        "analyze_impact",
        "validate_evidence",
        "render_report",
    ]
    for item in by_id.values():
        assert item["status"] == "ready"
        assert item["output_count"] >= 2
        assert not item["missing_required"]


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


async def test_workbench_provider_capabilities_loads_persisted_agent_settings(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    import aiosqlite
    from app.database import _MIGRATIONS, _SCHEMA

    db_path = tmp_path / "settings.db"
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                await db.execute(stmt)
            except aiosqlite.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        await db.executemany(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            [
                ("claude_code_command", "ccr code"),
                ("claude_code_mcp_profiles", json.dumps(["codehub-readonly"])),
                (
                    "external_agent_custom_providers",
                    json.dumps([
                        {
                            "id": "persisted-agent",
                            "command": "persisted-agent run --json",
                            "prompt_transport": "stdin",
                            "supports_mcp": True,
                            "mcp_profiles": ["codehub-mcp"],
                        }
                    ]),
                ),
            ],
        )
        await db.commit()

    monkeypatch.setattr(settings, "sqlite_db", str(db_path))
    monkeypatch.setattr(settings, "claude_code_command", "claude")
    monkeypatch.setattr(settings, "claude_code_mcp_profiles", [])
    monkeypatch.setattr(settings, "external_agent_custom_providers", [])

    resp = await workbench_client.get("/api/workbench/provider-capabilities")

    assert resp.status_code == 200
    by_id = {item["provider"]: item for item in resp.json()["providers"]}
    assert by_id["claude-code"]["diagnostics"]["configured_command_text"] == "ccr code"
    assert by_id["claude-code"]["capabilities"]["mcp_profiles"] == ["codehub-readonly"]
    assert by_id["persisted-agent"]["command"] == ["persisted-agent", "run", "--json"]
    assert by_id["persisted-agent"]["capabilities"]["supports_mcp"] is True


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
                    "executable": "ccr",
                    "argv": ["ccr", "code"],
                    "configured_argv": ["ccr", "code"],
                    "launch_kind": "exec",
                    "resolution": {
                        "command": "ccr code",
                        "executable": "ccr",
                        "configured_argv": ["ccr", "code"],
                        "platform": "Windows",
                        "method": "not_found",
                        "which": "",
                        "where_exe": "C:/Windows/System32/where.exe",
                        "where_returncode": 1,
                        "where_stdout": [],
                        "where_stderr": "INFO: Could not find files for the given pattern(s).",
                        "common_dir_path": "",
                        "powershell_get_command": "",
                    },
                    "diagnostic": {
                        "summary": "cwd: E:/codetalk; PATH entries: C:/missing",
                        "cwd": "E:/codetalk",
                        "path_entries": ["C:/missing"],
                        "path_entry_count": 1,
                        "checked_common_dirs": ["C:/Users/me/AppData/Roaming/npm"],
                    },
                },
                {
                    "command": "claude",
                    "status": "available",
                    "executable": "claude",
                    "argv": ["C:/tools/claude.cmd", "-p", "--output-format", "json"],
                    "configured_argv": ["C:/tools/claude.cmd"],
                    "path": "C:/tools/claude.cmd",
                    "launch_kind": "exec",
                },
            ],
            "diagnostic": {
                "summary": "cwd: E:/codetalk; PATH entries: C:/missing",
                "cwd": "E:/codetalk",
                "path_entries": ["C:/missing"],
                "path_entry_count": 1,
                "command_hint_env": "CLAUDE_CODE_COMMAND",
                "command_hint": "set CLAUDE_CODE_COMMAND to C:/tools/claude.cmd",
            },
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
    assert resolution["attempts"][0]["executable"] == "ccr"
    assert resolution["attempts"][0]["configured_argv"] == ["ccr", "code"]
    assert resolution["attempts"][0]["resolution"]["where_exe"] == "C:/Windows/System32/where.exe"
    assert resolution["attempts"][0]["resolution"]["where_returncode"] == 1
    assert resolution["attempts"][0]["resolution"]["method"] == "not_found"
    assert resolution["attempts"][0]["diagnostic"]["path_entries"] == ["C:/missing"]
    assert resolution["diagnostic"]["command_hint_env"] == "CLAUDE_CODE_COMMAND"
    assert by_id["semantic-library"]["capabilities"]["supports_black_box_terms"] is True


async def test_workbench_system_audit_api_reports_control_plane_readiness(workbench_client):
    resp = await workbench_client.get("/api/workbench/system-audit")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["summary"]["missing_required"] == 0
    checks = {item["id"]: item for item in body["checks"]}
    assert checks["workbench_data_dir"]["status"] == "ok"
    assert checks["workflow_presets"]["status"] == "ok"
    assert {
        "module_analysis",
        "resource_leak_hunt",
        "mr_blackbox_test",
        "patch_impact_review",
    }.issubset(set(checks["workflow_presets"]["details"]["available"]))
    assert checks["provider_capability_matrix"]["details"]["provider_count"] >= 4
    assert checks["codetalk_index_provider_readiness"]["severity"] == "recommended"
    assert set(checks["codetalk_index_provider_readiness"]["details"]["ready_provider_ids"]) >= {
        "gitnexus",
        "cgc",
    }
    assert checks["agent_cli_provider_registry"]["status"] == "ok"
    assert checks["task_acceptance_audit_api"]["details"]["endpoint"] == (
        "POST /api/workbench/task-runs/{task_run_id}/acceptance-audit"
    )
    assert checks["external_agent_sandbox"]["severity"] == "recommended"
    assert checks["external_agent_sandbox"]["status"] == "missing"
    assert "external_agent_sandbox" in {
        item["id"] for item in body["missing_recommended"]
    }


async def test_workbench_system_audit_reports_degraded_when_no_agent_cli_launches(
    workbench_client,
    monkeypatch,
):
    monkeypatch.setattr(settings, "claude_code_command", "ccr code")
    monkeypatch.setattr(settings, "opencode_command", "opencode")
    monkeypatch.setattr(
        settings,
        "external_agent_custom_providers",
        [{"id": "corp-agent", "command": "corp-agent run", "prompt_transport": "stdin"}],
    )

    def fake_health(provider, command, fallback_commands=None):
        executable = str(command).split()[0] if command else ""
        return {
            "provider": provider,
            "status": "unavailable",
            "reason": f"command not found: {executable}",
            "attempts": [
                {
                    "command": command,
                    "status": "unavailable",
                    "reason": f"command not found: {executable}",
                    "executable": executable,
                    "configured_argv": str(command).split(),
                }
            ],
            "diagnostic": {
                "summary": "cwd: E:/codetalk; PATH entries: C:/missing",
                "command_hint_env": f"{provider.upper().replace('-', '_')}_COMMAND",
                "command_hint": f"set {provider.upper().replace('-', '_')}_COMMAND to a full CLI path",
            },
        }

    monkeypatch.setattr(
        "app.services.workbench_task_run.check_provider_health",
        fake_health,
        raising=False,
    )

    resp = await workbench_client.get("/api/workbench/system-audit")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["runtime_status"] == "degraded"
    checks = {item["id"]: item for item in body["checks"]}
    readiness = checks["agent_cli_launch_readiness"]
    assert readiness["status"] == "missing"
    assert readiness["severity"] == "recommended"
    assert readiness["details"]["available_provider_count"] == 0
    assert set(readiness["details"]["failed_provider_ids"]) == {
        "claude-code",
        "opencode",
        "corp-agent",
    }
    assert "CLAUDE_CODE_COMMAND" in readiness["details"]["recommended_actions"][0]
    assert body["missing_recommended"][0]["id"] == "agent_cli_launch_readiness"


async def test_workbench_system_audit_reports_index_provider_configuration_gaps(
    workbench_client,
    monkeypatch,
):
    monkeypatch.setattr(settings, "gitnexus_base_url", "")
    monkeypatch.setattr(settings, "cgc_base_url", "")

    resp = await workbench_client.get("/api/workbench/system-audit")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["runtime_status"] == "degraded"
    checks = {item["id"]: item for item in body["checks"]}
    index_readiness = checks["codetalk_index_provider_readiness"]
    assert index_readiness["status"] == "missing"
    assert index_readiness["severity"] == "recommended"
    assert index_readiness["details"]["ready_provider_count"] == 0
    assert set(index_readiness["details"]["failed_provider_ids"]) == {"gitnexus", "cgc"}
    assert "startup probe" in index_readiness["details"]["notes"][1]
    assert "fallback remains non-blocking" in index_readiness["details"]["recommended_actions"][0]


async def test_workbench_deployment_probe_runs_agent_cli_startup_checks(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "claude_code_command", "ccr code")
    monkeypatch.setattr(settings, "opencode_command", "opencode")
    monkeypatch.setattr(settings, "external_agent_custom_providers", [])

    async def fake_probe(provider, repo_path=None):
        if provider == "claude-code":
            return {
                "provider": provider,
                "healthy": True,
                "status": "ok",
                "message": "startup_probe_ok",
                "health": {"status": "available", "used_fallback": False},
            }
        return {
            "provider": provider,
            "healthy": False,
            "status": "unavailable",
            "message": "command not found: opencode",
            "health": {"status": "unavailable", "reason": "command not found: opencode"},
        }

    monkeypatch.setattr(
        "app.api.agent_workbench.probe_external_agent_startup",
        fake_probe,
        raising=False,
    )

    resp = await workbench_client.post(
        "/api/workbench/deployment-probe",
        json={"repo_path": str(tmp_path)},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["summary"]["provider_count"] == 2
    assert body["summary"]["healthy_count"] == 1
    assert body["summary"]["failed_count"] == 1
    by_id = {item["provider"]: item for item in body["providers"]}
    assert by_id["claude-code"]["status"] == "ok"
    assert by_id["opencode"]["status"] == "unavailable"
    assert body["evidence_count"] == 3
    artifact_path = Path(body["artifact"]["path"])
    assert artifact_path.exists()
    latest_path = artifact_path.parent / "deployment_probe_latest.json"
    assert latest_path.exists()
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    assert latest["probe_id"] == body["probe_id"]
    assert latest["summary"]["failed_count"] == 1


async def test_workbench_deployment_probe_can_run_task_contract_probe(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    script = tmp_path / "deployment_probe_agent.py"
    script.write_text(
        "\n".join([
            "import json",
            "import os",
            "import pathlib",
            "import sys",
            "",
            "payload = json.loads(sys.stdin.read() or '{}')",
            "artifact_dir = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
            "(artifact_dir / 'agent_task_probe.json').write_text(json.dumps({",
            "    'status': 'ok',",
            "    'provider': payload.get('provider'),",
            "    'task_bundle_keys': sorted((payload.get('task_bundle') or {}).keys()),",
            "}), encoding='utf-8')",
            "print(json.dumps({'status': 'ok'}))",
            "",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        settings,
        "external_agent_custom_providers",
        [
            {
                "id": "deployment-agent",
                "command": f'"{sys.executable}" "{script}"',
                "prompt_transport": "stdin",
            }
        ],
    )

    async def fake_probe(provider, repo_path=None):
        return {
            "provider": provider,
            "healthy": True,
            "status": "ok",
            "message": "startup_probe_ok",
            "health": {"status": "available"},
        }

    monkeypatch.setattr(
        "app.api.agent_workbench.probe_external_agent_startup",
        fake_probe,
        raising=False,
    )

    resp = await workbench_client.post(
        "/api/workbench/deployment-probe",
        json={
            "repo_path": str(tmp_path),
            "providers": ["deployment-agent"],
            "task_contract_probe": True,
            "timeout_sec": 15,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["summary"]["task_contract_probe"] is True
    assert body["summary"]["task_ready_count"] == 1
    assert body["summary"]["task_failed_count"] == 0
    provider = body["providers"][0]
    assert provider["provider"] == "deployment-agent"
    assert provider["task_probe"]["status"] == "ready"
    assert provider["task_probe"]["summary"]["task_contract_status"] == "ok"
    assert Path(provider["task_probe"]["artifact"]["path"]).exists()
    assert body["evidence_count"] == 3
    assert len(body["evidence_ids"]) == 3
    latest = json.loads(Path(body["artifact"]["latest_path"]).read_text(encoding="utf-8"))
    assert latest["providers"][0]["task_probe"]["task_run_id"] == provider["task_probe"]["task_run_id"]
    assert latest["evidence_ids"] == body["evidence_ids"]

    memory = await workbench_client.get(
        "/api/workbench/memory/search",
        params={
            "q": "provider_task_probe deployment-agent",
            "workspace_id": "codetalk-deployment",
        },
    )
    assert memory.status_code == 200
    items = memory.json()["items"]
    assert any(item["kind"] == "provider_task_probe" for item in items)
    provider_evidence = next(item for item in items if item["kind"] == "provider_task_probe")
    assert provider_evidence["status"] == "accepted"
    assert provider_evidence["source"] == "deployment_probe"
    assert provider_evidence["provenance"]["provider"] == "deployment-agent"
    assert provider_evidence["provenance"]["task_probe_status"] == "ready"

    startup_memory = await workbench_client.get(
        "/api/workbench/memory/search",
        params={
            "q": "provider_startup_probe deployment-agent",
            "workspace_id": "codetalk-deployment",
        },
    )
    assert startup_memory.status_code == 200
    startup_items = startup_memory.json()["items"]
    assert any(item["kind"] == "provider_startup_probe" for item in startup_items)
    startup_evidence = next(
        item for item in startup_items if item["kind"] == "provider_startup_probe"
    )
    assert startup_evidence["status"] == "accepted"
    assert startup_evidence["source"] == "deployment_probe"
    assert startup_evidence["provenance"]["provider"] == "deployment-agent"


async def test_workbench_system_audit_uses_latest_deployment_task_probe(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    script = tmp_path / "audit_probe_agent.py"
    script.write_text(
        "\n".join([
            "import json",
            "import os",
            "import pathlib",
            "import sys",
            "",
            "payload = json.loads(sys.stdin.read() or '{}')",
            "artifact_dir = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
            "(artifact_dir / 'agent_task_probe.json').write_text(json.dumps({",
            "    'status': 'ok',",
            "    'provider': payload.get('provider'),",
            "}), encoding='utf-8')",
            "print(json.dumps({'status': 'ok'}))",
            "",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        settings,
        "external_agent_custom_providers",
        [
            {
                "id": "audit-agent",
                "command": f'"{sys.executable}" "{script}"',
                "prompt_transport": "stdin",
            }
        ],
    )

    async def fake_probe(provider, repo_path=None):
        return {
            "provider": provider,
            "healthy": True,
            "status": "ok",
            "message": "startup_probe_ok",
            "health": {"status": "available"},
        }

    monkeypatch.setattr(
        "app.api.agent_workbench.probe_external_agent_startup",
        fake_probe,
        raising=False,
    )

    probe = await workbench_client.post(
        "/api/workbench/deployment-probe",
        json={
            "repo_path": str(tmp_path),
            "providers": ["audit-agent"],
            "task_contract_probe": True,
            "timeout_sec": 15,
        },
    )
    assert probe.status_code == 200

    audit = await workbench_client.get("/api/workbench/system-audit")

    assert audit.status_code == 200
    checks = {item["id"]: item for item in audit.json()["checks"]}
    latest = checks["latest_deployment_task_probe"]
    assert latest["status"] == "ok"
    assert latest["details"]["probe_id"] == probe.json()["probe_id"]
    assert latest["details"]["task_ready_count"] == 1
    assert latest["details"]["task_failed_count"] == 0
    assert latest["details"]["artifact_path"] == probe.json()["artifact"]["latest_path"]


async def test_workbench_task_smoke_e2e_runs_prepare_execute_acceptance(
    workbench_client,
    tmp_path,
):
    resp = await workbench_client.post(
        "/api/workbench/task-runs/smoke-e2e",
        json={"repo_path": str(tmp_path)},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["workflow_id"] == "codetalk_smoke_e2e"
    assert body["execution"]["status"] == "completed"
    assert body["acceptance_audit"]["status"] == "ready"
    assert body["acceptance_audit"]["summary"]["missing_required"] == 0
    assert body["task_run"]["task_run_id"]
    artifact_path = Path(body["artifact"]["path"])
    assert artifact_path.exists()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["task_run_id"] == body["task_run"]["task_run_id"]
    assert payload["acceptance_audit"]["status"] == "ready"
    task_dir = Path(body["task_run"]["artifact_dir"])
    assert (task_dir / "workflow_execution.json").exists()
    assert (task_dir / "task_acceptance_audit.json").exists()
    assert (task_dir / "agent_runs" / "discover_scope" / "source_scope.json").exists()


async def test_workbench_provider_task_probe_executes_configured_provider_contract(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    script = tmp_path / "probe_agent.py"
    script.write_text(
        "\n".join([
            "import json",
            "import os",
            "import pathlib",
            "import sys",
            "",
            "payload = json.loads(sys.stdin.read() or '{}')",
            "artifact_dir = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])",
            "probe = {",
            "    'status': 'ok',",
            "    'provider': payload.get('provider'),",
            "    'run_id': payload.get('run_id'),",
            "    'has_task_bundle': bool(payload.get('task_bundle')),",
            "}",
            "(artifact_dir / 'agent_task_probe.json').write_text(json.dumps(probe), encoding='utf-8')",
            "print(json.dumps({'status': 'ok', 'probe': 'agent_task_contract'}))",
            "",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        settings,
        "external_agent_custom_providers",
        [
            {
                "id": "probe-agent",
                "command": f'"{sys.executable}" "{script}"',
                "prompt_transport": "stdin",
            }
        ],
    )

    resp = await workbench_client.post(
        "/api/workbench/provider-task-probe",
        json={
            "provider": "probe-agent",
            "repo_path": str(tmp_path),
            "timeout_sec": 15,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "probe-agent"
    assert body["status"] == "ready"
    assert body["execution"]["status"] == "completed"
    assert body["acceptance_audit"]["status"] == "ready"
    assert body["contract"]["required_artifacts"] == ["agent_task_probe.json"]
    assert body["summary"]["missing_required"] == 0
    assert body["summary"]["execution_status"] == "completed"
    assert body["summary"]["task_contract_status"] == "ok"
    task_dir = Path(body["task_run"]["artifact_dir"])
    probe_artifact = task_dir / "agent_runs" / "agent_task_probe" / "agent_task_probe.json"
    assert probe_artifact.exists()
    payload = json.loads(probe_artifact.read_text(encoding="utf-8"))
    assert payload["provider"] == "probe-agent"
    assert payload["has_task_bundle"] is True
    assert Path(body["artifact"]["path"]).exists()
    persisted = json.loads(Path(body["artifact"]["path"]).read_text(encoding="utf-8"))
    assert persisted["task_run_id"] == body["task_run_id"]


async def test_workbench_provider_task_probe_rejects_unknown_provider(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "external_agent_custom_providers", [])

    resp = await workbench_client.post(
        "/api/workbench/provider-task-probe",
        json={"provider": "missing-agent", "repo_path": str(tmp_path)},
    )

    assert resp.status_code == 422
    assert "Unknown provider" in resp.text


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


async def test_agent_mr_artifact_validation_rejects_directory(tmp_path):
    from app.services.agent_run_harness import ArtifactValidationHarness

    artifact_dir = tmp_path / "agent"
    artifact_dir.mkdir()
    (artifact_dir / "mr_snapshot.json").mkdir()
    (artifact_dir / "diff.patch").write_text("", encoding="utf-8")
    (artifact_dir / "changed_files.json").write_text("[]", encoding="utf-8")

    result = ArtifactValidationHarness(artifact_dir).validate_mr_artifacts(
        required_artifacts=["mr_snapshot.json", "diff.patch", "changed_files.json"],
    )

    assert result.status == "invalid"
    assert {
        "artifact": "mr_snapshot.json",
        "reason": "artifact_is_directory",
    } in result.rejected_artifacts
    assert "mr_snapshot.json" not in result.accepted_artifacts


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


async def test_workbench_task_scoped_agent_run_retries_stdin_when_prompt_arg_fails(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    from app.services.agent_run_harness import AgentRunHarness

    task_run_id = "task_run_transport_fallback"
    step_id = "discover"
    artifact_dir = (
        tmp_path
        / "data"
        / "workbench"
        / "task_runs"
        / task_run_id
        / "agent_runs"
        / step_id
    )
    attempts_file = artifact_dir / "attempts.jsonl"
    script = (
        "import json, pathlib, sys; "
        "path=pathlib.Path(sys.argv[1]); "
        "payload=sys.stdin.read(); "
        "path.parent.mkdir(parents=True, exist_ok=True); "
        "existing=path.read_text(encoding='utf-8') if path.exists() else ''; "
        "path.write_text(existing+json.dumps({'argv': sys.argv[2:], 'stdin_has_run_id': 'run_id' in payload})+'\\n', encoding='utf-8'); "
        "bad='-p' in sys.argv[2:]; "
        "print('stdin transport ok' if not bad else '', end=''); "
        "print('unknown option: -p', file=sys.stderr) if bad else None; "
        "raise SystemExit(2 if bad else 0)"
    )
    AgentRunHarness(artifact_dir).create_run(
        run_id=f"{task_run_id}_{step_id}",
        provider="claude-code",
        command=["python", "-c", script, str(attempts_file), "-p"],
        cwd=str(tmp_path),
        workflow_snapshot={"id": "wf"},
        task_bundle={"task_id": task_run_id},
    )
    monkeypatch.setattr(
        "app.services.external_agent_discovery.settings.external_agent_custom_providers",
        [],
    )

    executed = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/agent-runs/{step_id}/execute",
        json={"timeout_sec": 10},
    )

    assert executed.status_code == 200
    body = executed.json()
    assert body["status"] == "completed"
    attempts = [
        json.loads(line)
        for line in attempts_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(attempts) == 2
    assert "-p" in attempts[0]["argv"]
    assert "-p" not in attempts[1]["argv"]
    assert attempts[0]["stdin_has_run_id"] is False
    assert attempts[1]["stdin_has_run_id"] is True
    execution_input = json.loads((artifact_dir / "execution_input.json").read_text(encoding="utf-8"))
    assert execution_input["prompt_transport"] == "stdin"
    assert execution_input["prompt_transport_reason"] == "transport_fallback_from_argv"
    assert execution_input["transport_attempts"][0]["prompt_transport"] == "argv"
    assert execution_input["transport_attempts"][0]["status"] == "error"
    assert execution_input["transport_attempts"][1]["prompt_transport"] == "stdin"
    assert execution_input["transport_attempts"][1]["prompt_transport_reason"] == "transport_fallback_from_argv"
    replay_plan = json.loads((artifact_dir / "agent_replay_plan.json").read_text(encoding="utf-8"))
    assert replay_plan["prompt_transport"] == "stdin"
    assert replay_plan["prompt_transport_reason"] == "transport_fallback_from_argv"
    assert replay_plan["transport_attempts"][0]["status"] == "error"
    assert replay_plan["transport_attempts"][1]["prompt_transport"] == "stdin"


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
    assert executed.json()["acceptance_audit"]["status"] == "ready"
    assert executed.json()["acceptance_audit"]["summary"]["missing_required"] == 0
    auto_materialized = executed.json()["evidence_materialization"]
    assert auto_materialized["status"] == "ok"
    assert auto_materialized["evidence_count"] == 1
    search_after_execute = await workbench_client.get(
        "/api/workbench/memory/search",
        params={"q": "TLS negotiation", "workspace_id": "ws-output-memory"},
    )
    assert search_after_execute.status_code == 200
    assert any(
        item["kind"] == "workflow_output"
        for item in search_after_execute.json()["items"]
    )
    auto_materialization_artifact = (
        Path(prepared.json()["artifact_dir"]) / "workflow_output_materialization.json"
    )
    assert auto_materialization_artifact.exists()
    acceptance_artifact = Path(prepared.json()["artifact_dir"]) / "task_acceptance_audit.json"
    assert acceptance_artifact.exists()
    acceptance_payload = json.loads(acceptance_artifact.read_text(encoding="utf-8"))
    assert acceptance_payload["task_run_id"] == task_run_id

    materialized = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/materialize-outputs"
    )

    assert materialized.status_code == 200
    body = materialized.json()
    assert body["status"] == "ok"
    assert body["evidence_count"] == 1
    assert body["materialized_evidence"][0]["output_id"] == "cases"
    assert body["materialization_audit"]["summary"]["declared_output_count"] == 1
    assert body["materialization_audit"]["summary"]["materialized_output_count"] == 1
    assert body["materialization_audit"]["outputs"][0]["output_id"] == "cases"
    assert body["materialization_audit"]["outputs"][0]["materialization_status"] == "accepted"
    assert body["materialization_audit"]["outputs"][0]["materialized_count"] == 1
    materialization_artifact = (
        Path(prepared.json()["artifact_dir"]) / "workflow_output_materialization.json"
    )
    assert materialization_artifact.exists()
    materialization = json.loads(materialization_artifact.read_text(encoding="utf-8"))
    assert materialization["task_run_id"] == task_run_id
    assert materialization["workflow_outputs_artifact"]["output_count"] == 1
    assert materialization["workflow_outputs_artifact"]["sha256"]
    assert materialization["evidence_ids"] == body["evidence_ids"]
    assert materialization["materialization_audit"] == body["materialization_audit"]
    artifacts = await workbench_client.get(f"/api/workbench/task-runs/{task_run_id}/artifacts")
    assert artifacts.status_code == 200
    paths = {item["relative_path"]: item for item in artifacts.json()["artifacts"]}
    assert (
        paths["workflow_output_materialization.json"]["kind"]
        == "workflow_output_materialization"
    )
    manifest = json.loads(
        (Path(prepared.json()["artifact_dir"]) / "task_artifact_manifest.json")
        .read_text(encoding="utf-8")
    )
    manifest_paths = {item["relative_path"]: item for item in manifest["artifacts"]}
    assert (
        manifest_paths["workflow_output_materialization.json"]["kind"]
        == "workflow_output_materialization"
    )
    assert (
        manifest_paths["task_acceptance_audit.json"]["kind"]
        == "task_acceptance_audit"
    )
    assert manifest_paths["workflow_output_materialization.json"]["sha256"] == hashlib.sha256(
        materialization_artifact.read_bytes()
    ).hexdigest()
    search = await workbench_client.get(
        "/api/workbench/memory/search",
        params={"q": "TLS negotiation", "workspace_id": "ws-output-memory"},
    )
    assert search.status_code == 200
    item = search.json()["items"][0]
    assert item["kind"] == "workflow_output"
    assert item["subject_key"].endswith("/cases")
    assert item["provenance"]["output_id"] == "cases"
    assert item["provenance"]["output_status"] == "ok"
    assert item["provenance"]["schema_status"] == "not_declared"
    assert item["provenance"]["agent_output_contract"]["artifact"] == (
        "agent_runs/design/agent_output_contract.json"
    )
    assert item["provenance"]["agent_output_contract"]["sha256"]
    assert item["provenance"]["agent_run"]["artifact"] == "agent_runs/design/agent_run.json"
    assert item["provenance"]["agent_run"]["sha256"]
    assert item["provenance"]["agent_execution_input"]["artifact"] == (
        "agent_runs/design/execution_input.json"
    )
    assert item["provenance"]["agent_execution_input"]["sha256"]
    assert item["provenance"]["agent_execution_result"]["artifact"] == (
        "agent_runs/design/execution_result.json"
    )
    assert item["provenance"]["agent_replay_plan"]["artifact"] == (
        "agent_runs/design/agent_replay_plan.json"
    )
    assert item["provenance"]["agent_replay_plan"]["sha256"]
    assert item["provenance"]["workflow_outputs_artifact"]["sha256"] == (
        materialization["workflow_outputs_artifact"]["sha256"]
    )


async def test_workbench_task_run_run_api_prepares_executes_and_audits(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    from app.config import settings

    script_path = tmp_path / "agent_one_click.py"
    script_path.write_text(
        "import pathlib, os\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "(root/'cases.md').write_text('TLS one-click black-box case', encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow = {
        "id": "one_click_run_workflow",
        "name": "One click run workflow",
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

    response = await workbench_client.post(
        "/api/workbench/task-runs/run",
        json={
            "workflow_id": "one_click_run_workflow",
            "workspace_id": "ws-one-click-run",
            "repo_path": str(tmp_path),
            "inputs": {"module": "nvme-tcp-tls"},
            "timeout_sec": 10,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed"
    assert body["task_run"]["task_run_id"] == body["task_run_id"]
    assert body["execution"]["status"] == "completed"
    assert body["execution"]["outputs"][0]["status"] == "ok"
    assert body["evidence_materialization"]["status"] == "ok"
    assert body["evidence_materialization"]["evidence_count"] == 1
    assert body["acceptance_audit"]["status"] == "ready"
    task_dir = Path(body["task_run"]["artifact_dir"])
    assert (task_dir / "workflow_execution.json").exists()
    assert (task_dir / "workflow_output_materialization.json").exists()
    assert (task_dir / "task_acceptance_audit.json").exists()
    assert (task_dir / "task_artifact_manifest.json").exists()
    search = await workbench_client.get(
        "/api/workbench/memory/search",
        params={"q": "one-click", "workspace_id": "ws-one-click-run"},
    )
    assert search.status_code == 200
    assert any(item["kind"] == "workflow_output" for item in search.json()["items"])


async def test_builtin_mr_blackbox_run_produces_executable_black_box_case_contract(
    workbench_client,
    tmp_path,
):
    repo = tmp_path / "spdk-like"
    source_file = repo / "lib" / "nvmf" / "tcp.c"
    source_file.parent.mkdir(parents=True)
    source_file.write_text(
        "int nvmf_tcp_qpair_init(void) { return 0; }\n",
        encoding="utf-8",
    )
    test_dir = repo / "test" / "nvmf"
    test_dir.mkdir(parents=True)
    (test_dir / "nvmf.sh").write_text("# public nvmf smoke harness\n", encoding="utf-8")
    diff_text = (
        "diff --git a/lib/nvmf/tcp.c b/lib/nvmf/tcp.c\n"
        "--- a/lib/nvmf/tcp.c\n"
        "+++ b/lib/nvmf/tcp.c\n"
        "@@ -1 +1 @@\n"
        "-int nvmf_tcp_qpair_init(void) { return 0; }\n"
        "+int nvmf_tcp_qpair_init(void) { return -1; }\n"
    )

    installed = await workbench_client.post(
        "/api/workbench/workflow-presets/mr_blackbox_test/install"
    )
    assert installed.status_code == 201

    response = await workbench_client.post(
        "/api/workbench/task-runs/run",
        json={
            "workflow_id": "mr_blackbox_test",
            "workspace_id": "ws-mr-blackbox-contract",
            "repo_path": str(repo),
            "inputs": {
                "patch_diff": diff_text,
                "repo_path": str(repo),
            },
            "timeout_sec": 10,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed"
    assert body["execution"]["outputs"][1]["id"] == "black_box_cases"
    assert body["execution"]["outputs"][1]["status"] == "ok"
    assert body["semantic_output_import"]["status"] == "ok"
    assert body["semantic_output_import"]["imported_count"] == 1
    assert body["acceptance_audit"]["status"] == "ready"

    task_dir = Path(body["task_run"]["artifact_dir"])
    cases_path = task_dir / "steps" / "collect_mr" / "black_box_cases.json"
    assert cases_path.exists()
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    assert len(cases) == 1
    case = cases[0]
    assert case["case_type"] == "black_box_ready"
    assert case["file_path"] == "lib/nvmf/tcp.c"
    assert case["module"] == "nvmf"
    for field in (
        "scenario",
        "preconditions",
        "inputs",
        "steps",
        "expected",
        "observable_signals",
        "diagnostics",
    ):
        assert case[field]
    assert "test/nvmf" in json.dumps(case["preconditions"] + case["diagnostics"])
    assert "public workflow" in case["inputs"] or "host connection" in case["inputs"]
    assert all("nvmf_tcp_qpair_init" not in step for step in case["steps"])
    assert any("RPC" in signal or "log" in signal for signal in case["observable_signals"])
    assert case["trace"]["changed_file"]["path"] == "lib/nvmf/tcp.c"

    materialized = task_dir / "semantic_output_import.json"
    assert materialized.exists()
    imported = json.loads(materialized.read_text(encoding="utf-8"))
    assert imported["result"]["source_ref"] == f"task_run:{body['task_run_id']}:black_box_cases"


async def test_workbench_task_run_run_auto_imports_declared_semantic_outputs(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    from app.config import settings

    script_path = tmp_path / "agent_write_semantic_cases.py"
    script_path.write_text(
        "import json, pathlib, os\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "(root/'black_box_cases.json').write_text(json.dumps([\n"
        "  {\n"
        "    'title': 'TLS handshake uses existing failure wording',\n"
        "    'entry_kind': 'rpc',\n"
        "    'inputs': 'connect with expired certificate',\n"
        "    'steps': ['start TLS listener', 'connect with expired certificate'],\n"
        "    'expected': ['handshake rejected', 'standard failure reason is reported']\n"
        "  }\n"
        "]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow = {
        "id": "auto_semantic_output_workflow",
        "name": "Auto semantic output workflow",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [
            {
                "id": "design",
                "type": "agent_task",
                "provider": "local-python",
                "required_artifacts": ["black_box_cases.json"],
            }
        ],
        "outputs": [
            {
                "id": "black_box_cases",
                "type": "test_cases",
                "from": "design",
                "artifact": "black_box_cases.json",
                "semantic_import": {
                    "enabled": True,
                    "defaults": {
                        "feature": "NVMe TCP TLS",
                        "module": "nvmf_tcp/transport/tls",
                        "terms": ["expired-cert"],
                    },
                },
            }
        ],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201

    response = await workbench_client.post(
        "/api/workbench/task-runs/run",
        json={
            "workflow_id": "auto_semantic_output_workflow",
            "workspace_id": "ws-auto-semantic-output",
            "repo_path": str(tmp_path),
            "inputs": {"module": "nvmf_tcp/transport/tls"},
            "timeout_sec": 10,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["semantic_output_import"]["status"] == "ok"
    assert body["semantic_output_import"]["imported_count"] == 1
    task_dir = Path(body["task_run"]["artifact_dir"])
    artifact = task_dir / "semantic_output_import.json"
    assert artifact.exists()
    artifact_payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert artifact_payload["mode"] == "auto"
    assert artifact_payload["result"]["source_ref"] == (
        f"task_run:{body['task_run_id']}:black_box_cases"
    )

    search = await workbench_client.get(
        "/api/workbench/semantic-cases/search",
        params={
            "q": "expired-cert failure wording",
            "module": "nvmf_tcp/transport/tls",
            "test_level": "black_box",
        },
    )
    assert search.status_code == 200
    assert search.json()["items"][0]["scenario"] == (
        "TLS handshake uses existing failure wording"
    )


async def test_workbench_imports_black_box_workflow_output_into_semantic_library(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    from app.config import settings

    script_path = tmp_path / "agent_write_black_box_cases.py"
    script_path.write_text(
        "import json, pathlib, os\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "(root/'black_box_cases.json').write_text(json.dumps([\n"
        "  {\n"
        "    'title': 'TLS handshake rejects expired certificate',\n"
        "    'entry_kind': 'rpc',\n"
        "    'preconditions': 'NVMe TCP subsystem has TLS enabled',\n"
        "    'inputs': 'connect with an expired certificate',\n"
        "    'steps': ['start listener', 'connect host with expired certificate'],\n"
        "    'expected': 'connection is rejected and failure is logged',\n"
        "    'observable_signals': ['RPC reports failure', 'TLS alert is logged']\n"
        "  }\n"
        "]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow = {
        "id": "semantic_feedback_workflow",
        "name": "Semantic feedback workflow",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [
            {
                "id": "design",
                "type": "agent_task",
                "provider": "local-python",
                "required_artifacts": ["black_box_cases.json"],
            }
        ],
        "outputs": [
            {
                "id": "black_box_cases",
                "type": "test_cases",
                "from": "design",
                "artifact": "black_box_cases.json",
            }
        ],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201
    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "semantic_feedback_workflow",
            "workspace_id": "ws-semantic-feedback",
            "repo_path": str(tmp_path),
            "inputs": {"module": "nvmf_tcp/transport/tls"},
        },
    )
    task_run_id = prepared.json()["task_run_id"]
    executed = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/execute",
        json={"timeout_sec": 10},
    )
    assert executed.status_code == 200
    assert executed.json()["outputs"][0]["status"] == "ok"

    imported = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/semantic-cases/import-outputs",
        json={"output_ids": ["black_box_cases"]},
    )

    assert imported.status_code == 201
    body = imported.json()
    assert body["imported_count"] == 1
    assert body["rejected_count"] == 0
    assert body["imported"][0]["case_id"].startswith(f"{task_run_id}_black_box_cases_")
    assert body["source_ref"] == f"task_run:{task_run_id}:black_box_cases"
    artifact = Path(prepared.json()["artifact_dir"]) / "semantic_output_import.json"
    assert artifact.exists()
    artifact_payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert artifact_payload["result"]["imported_count"] == 1

    search = await workbench_client.get(
        "/api/workbench/semantic-cases/search",
        params={
            "q": "expired certificate",
            "module": "nvmf_tcp/transport/tls",
            "test_level": "black_box",
        },
    )
    assert search.status_code == 200
    case = search.json()["items"][0]
    assert case["scenario"] == "TLS handshake rejects expired certificate"
    assert case["interface"] == "rpc"
    assert "generated_from_task_output" in case["tags"]


async def test_workbench_materialize_outputs_auto_imports_declared_semantic_outputs(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    from app.config import settings

    script_path = tmp_path / "agent_write_materialize_semantic_cases.py"
    script_path.write_text(
        "import json, pathlib, os\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "(root/'black_box_cases.json').write_text(json.dumps([\n"
        "  {\n"
        "    'title': 'TLS listener reports configured alert text',\n"
        "    'entry_kind': 'cli',\n"
        "    'inputs': 'start listener with expired peer certificate',\n"
        "    'steps': ['configure TLS listener', 'connect expired certificate peer'],\n"
        "    'expected': ['connection rejected', 'configured alert text is visible']\n"
        "  }\n"
        "]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow = {
        "id": "manual_materialize_semantic_output",
        "name": "Manual materialize semantic output",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [
            {
                "id": "design",
                "type": "agent_task",
                "provider": "local-python",
                "required_artifacts": ["black_box_cases.json"],
            }
        ],
        "outputs": [
            {
                "id": "black_box_cases",
                "type": "test_cases",
                "from": "design",
                "artifact": "black_box_cases.json",
                "semantic_import": {
                    "enabled": True,
                    "defaults": {
                        "module": "nvmf_tcp/transport/tls",
                        "terms": ["alert-text"],
                    },
                },
            }
        ],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201
    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "manual_materialize_semantic_output",
            "workspace_id": "ws-manual-materialize-semantic",
            "repo_path": str(tmp_path),
            "inputs": {"module": "nvmf_tcp/transport/tls"},
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
        f"/api/workbench/task-runs/{task_run_id}/materialize-outputs",
    )

    assert materialized.status_code == 200
    body = materialized.json()
    assert body["semantic_output_import"]["status"] == "ok"
    assert body["semantic_output_import"]["imported_count"] == 1
    artifact = Path(prepared.json()["artifact_dir"]) / "semantic_output_import.json"
    assert json.loads(artifact.read_text(encoding="utf-8"))["mode"] == "auto"

    search = await workbench_client.get(
        "/api/workbench/semantic-cases/search",
        params={
            "q": "alert-text",
            "module": "nvmf_tcp/transport/tls",
            "test_level": "black_box",
        },
    )
    assert search.status_code == 200
    assert search.json()["items"][0]["scenario"] == (
        "TLS listener reports configured alert text"
    )


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
    assert body["materialization_audit"]["outputs"][0]["output_id"] == "scope"
    assert body["materialization_audit"]["outputs"][0]["materialization_status"] == "rejected"
    assert body["materialization_audit"]["outputs"][0]["rejection_reasons"] == ["output_not_ok"]
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


async def test_workbench_materialize_rejects_output_path_outside_task_artifacts(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    from app.config import settings

    script_path = tmp_path / "agent_scope.py"
    script_path.write_text(
        "import json, pathlib, os\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "(root/'scope.json').write_text(json.dumps({'files': []}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow = {
        "id": "outside_output_rejection",
        "name": "Outside output rejection",
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
            }
        ],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201
    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "outside_output_rejection",
            "workspace_id": "ws-outside-output",
            "repo_path": str(tmp_path),
            "inputs": {"module": "nvme-tcp-tls"},
        },
    )
    assert prepared.status_code == 201
    task_run_id = prepared.json()["task_run_id"]
    executed = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/execute",
        json={"timeout_sec": 10},
    )
    assert executed.status_code == 200
    assert executed.json()["outputs"][0]["status"] == "ok"

    outside = tmp_path / "outside_scope.json"
    outside.write_text(json.dumps({"files": [{"path": "outside.c"}]}), encoding="utf-8")
    outside_sha = hashlib.sha256(outside.read_bytes()).hexdigest()
    workflow_outputs_path = Path(prepared.json()["artifact_dir"]) / "workflow_outputs.json"
    workflow_outputs = json.loads(workflow_outputs_path.read_text(encoding="utf-8"))
    workflow_outputs["outputs"][0]["path"] = str(outside)
    workflow_outputs["outputs"][0]["sha256"] = outside_sha
    workflow_outputs_path.write_text(
        json.dumps(workflow_outputs, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

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
            "reason": "output_path_outside_task_artifacts",
            "path": str(outside),
        }
    ]
    search = await workbench_client.get(
        "/api/workbench/memory/search",
        params={"q": "outside.c", "workspace_id": "ws-outside-output"},
    )
    assert search.status_code == 200
    assert search.json()["items"] == []


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


async def test_workbench_materialize_custom_json_output_with_evidence_mapping(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    from app.config import settings

    script_path = tmp_path / "agent_findings.py"
    script_path.write_text(
        "import json, os, pathlib\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "findings=[{\n"
        "  'finding_id':'leak_tls_cleanup',\n"
        "  'file_path':'src/tls.c',\n"
        "  'function':'nvmf_tcp_tls_cleanup',\n"
        "  'resource':'bio',\n"
        "  'summary':'missing release on error branch'\n"
        "}]\n"
        "(root/'resource_leaks.json').write_text(json.dumps(findings), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow = {
        "id": "custom_finding_memory_workflow",
        "name": "Custom finding memory workflow",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text", "required": True}],
        "steps": [
            {
                "id": "scan",
                "type": "agent_task",
                "provider": "local-python",
                "required_artifacts": ["resource_leaks.json"],
            }
        ],
        "outputs": [
            {
                "id": "resource_leaks",
                "type": "json",
                "from": "scan",
                "artifact": "resource_leaks.json",
                "schema": {"type": "array"},
                "evidence_memory": {
                    "enabled": True,
                    "kind": "resource_leak_finding",
                    "subject_key_field": "finding_id",
                    "path_field": "file_path",
                    "symbol_field": "function",
                    "status": "candidate_output",
                    "text_fields": ["summary", "resource", "function"],
                },
            }
        ],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201
    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "custom_finding_memory_workflow",
            "workspace_id": "ws-custom-finding-memory",
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
    materialization = json.loads(
        (Path(prepared.json()["artifact_dir"]) / "workflow_output_materialization.json")
        .read_text(encoding="utf-8")
    )
    audit = materialization["materialization_audit"]
    assert audit["summary"]["declared_output_count"] == 1
    assert audit["summary"]["evidence_memory_declared_count"] == 1
    assert audit["summary"]["materialized_output_count"] == 1
    assert audit["summary"]["rejected_output_count"] == 0
    assert audit["outputs"] == [
        {
            "artifact": "resource_leaks.json",
            "declared_type": "json",
            "evidence_memory_declared": True,
            "evidence_memory_mapping": {
                "enabled": True,
                "kind": "resource_leak_finding",
                "path_field": "file_path",
                "status": "candidate_output",
                "subject_key_field": "finding_id",
                "symbol_field": "function",
                "text_fields": ["summary", "resource", "function"],
            },
            "from": "scan",
            "materialization_status": "accepted",
            "materialized_count": 2,
            "materialized_evidence_ids": [
                item["evidence_id"]
                for item in materialization["materialized_evidence"]
                if item["output_id"] == "resource_leaks"
            ],
            "output_id": "resource_leaks",
            "produced_status": "ok",
            "rejected_count": 0,
            "rejection_reasons": [],
        }
    ]
    search = await workbench_client.get(
        "/api/workbench/memory/search",
        params={"q": "missing release", "workspace_id": "ws-custom-finding-memory"},
    )
    assert search.status_code == 200
    findings = [
        item for item in search.json()["items"]
        if item["kind"] == "resource_leak_finding"
    ]
    assert findings
    assert findings[0]["subject_key"] == "leak_tls_cleanup"
    assert findings[0]["path"] == "src/tls.c"
    assert findings[0]["symbol"] == "nvmf_tcp_tls_cleanup"
    assert findings[0]["status"] == "candidate_output"
    assert findings[0]["provenance"]["output_id"] == "resource_leaks"
    assert findings[0]["provenance"]["workflow_output_evidence_id"]
    assert findings[0]["provenance"]["item"]["resource"] == "bio"


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
    assert body["status"] == "partial"
    assert body["evidence_count"] == 2
    assert {
        "output": "uncovered_functions",
        "path": "nof/nvmf_tcp/transport/tls/tls.c",
        "function_name": "nvmf_tcp_tls_handshake",
        "reason": "coverage_source_path_not_verified",
    } in body["rejected_outputs"]
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
    assert gaps[0]["status"] == "needs_source_validation"
    assert gaps[0]["provenance"]["line_start"] == 42
    assert gaps[0]["provenance"]["source_verified"] is False


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
    assert body["status"] == "partial"
    assert body["evidence_count"] == 3
    assert {
        "output": "source_scope",
        "reason": "source_scope_path_not_verified",
        "path": "missing/tls.c",
    } in body["rejected_outputs"]
    assert {
        "output": "source_scope",
        "reason": "source_scope_path_not_verified",
        "path": "README.md",
    } in body["rejected_outputs"]
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
    assert source_files[0]["provenance"]["agent_replay_plan"]["artifact"] == (
        "agent_runs/discover/agent_replay_plan.json"
    )
    assert source_files[0]["provenance"]["agent_execution_input"]["sha256"]
    assert source_files[0]["provenance"]["workflow_output_evidence_id"]
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
    assert body["status"] == "partial"
    assert body["evidence_count"] == 2
    assert {
        "output": "evidence_cards",
        "reason": "evidence_card_path_not_verified",
        "path": "src/missing.c",
        "card_id": "card_missing",
    } in body["rejected_outputs"]
    materialization = json.loads(
        (Path(prepared.json()["artifact_dir"]) / "workflow_output_materialization.json")
        .read_text(encoding="utf-8")
    )
    materialized_evidence = materialization["materialized_evidence"]
    assert [item["kind"] for item in materialized_evidence] == [
        "workflow_output",
        "evidence_card",
    ]
    assert materialized_evidence[0]["output_id"] == "evidence_cards"
    assert materialized_evidence[1]["subject_key"] == "card_tls_cleanup"
    assert materialized_evidence[1]["source_step_id"] == "discover"
    assert materialized_evidence[1]["path"] == "src/tls.c"
    assert materialized_evidence[1]["symbol"] == "nvmf_tcp_tls_cleanup"
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


async def test_workbench_agent_cli_workflow_materializes_auditable_memory_end_to_end(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    from app.config import settings

    source_file = tmp_path / "nof" / "nvmf_tcp" / "transport" / "tls" / "tls.c"
    source_file.parent.mkdir(parents=True)
    source_file.write_text(
        "int nvmf_tcp_tls_handshake(void) { return 0; }\n"
        "int nvmf_tcp_tls_cleanup(void) { return 0; }\n",
        encoding="utf-8",
    )
    script_path = tmp_path / "agent_full_workflow.py"
    script_path.write_text(
        "import json, os, pathlib, sys\n"
        "json.loads(sys.stdin.read())\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "source='nof/nvmf_tcp/transport/tls/tls.c'\n"
        "(root/'source_scope.json').write_text(json.dumps({\n"
        "  'files':[{'path':source,'reason':'validated TLS source scope',"
        "  'symbols':[{'name':'nvmf_tcp_tls_handshake','line_start':1}]}]\n"
        "}), encoding='utf-8')\n"
        "(root/'evidence_cards.json').write_text(json.dumps([\n"
        "  {'card_id':'tls_cleanup_card','path':source,'symbol':'nvmf_tcp_tls_cleanup',"
        "   'reason':'cleanup evidence','excerpt':'cleanup is externally testable'}\n"
        "]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}", "prompt_transport": "stdin"}
    ])
    workflow = {
        "id": "agent_cli_full_memory_workflow",
        "name": "Agent CLI full memory workflow",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text", "required": True}],
        "steps": [
            {
                "id": "discover",
                "type": "agent_task",
                "provider": "local-python",
                "goal": "Discover NVMe TCP TLS source scope and evidence cards.",
                "required_artifacts": ["source_scope.json", "evidence_cards.json"],
            },
            {"id": "validate_evidence", "type": "evidence_validate"},
            {"id": "render_report", "type": "report_render"},
        ],
        "outputs": [
            {
                "id": "source_scope",
                "type": "json",
                "from": "discover",
                "artifact": "source_scope.json",
            },
            {
                "id": "evidence_cards",
                "type": "json",
                "from": "discover",
                "artifact": "evidence_cards.json",
            },
            {"id": "report", "type": "markdown", "from": "render_report"},
        ],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201
    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "agent_cli_full_memory_workflow",
            "workspace_id": "ws-agent-cli-full",
            "repo_path": str(tmp_path),
            "inputs": {"module": "nvme-tcp-tls"},
        },
    )
    assert prepared.status_code == 201
    task_run_id = prepared.json()["task_run_id"]

    executed = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/execute",
        json={"timeout_sec": 10},
    )
    assert executed.status_code == 200
    execution = executed.json()
    assert execution["status"] == "completed"
    assert [output["status"] for output in execution["outputs"]] == ["ok", "ok", "ok"]
    assert execution["audit_summary"]["missing_artifacts"] == []

    materialized = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/materialize-outputs"
    )
    assert materialized.status_code == 200
    materialized_body = materialized.json()
    assert materialized_body["status"] == "ok"
    assert materialized_body["evidence_count"] >= 6

    artifacts = await workbench_client.get(f"/api/workbench/task-runs/{task_run_id}/artifacts")
    assert artifacts.status_code == 200
    artifact_paths = {item["relative_path"]: item for item in artifacts.json()["artifacts"]}
    assert artifact_paths["task_artifact_manifest.json"]["kind"] == "task_artifact_manifest"
    assert artifact_paths["workflow_output_materialization.json"]["kind"] == "workflow_output_materialization"
    assert artifact_paths["agent_runs/discover/agent_run_lifecycle.json"]["kind"] == "agent_run_lifecycle"

    source_search = await workbench_client.get(
        "/api/workbench/memory/search",
        params={"q": "validated TLS source scope", "workspace_id": "ws-agent-cli-full"},
    )
    assert source_search.status_code == 200
    source_items = source_search.json()["items"]
    source_files = [item for item in source_items if item["kind"] == "source_file"]
    assert source_files
    assert source_files[0]["subject_key"] == "nof/nvmf_tcp/transport/tls/tls.c"
    slices = await workbench_client.get(
        f"/api/workbench/memory/evidence/{source_files[0]['evidence_id']}/source-slices"
    )
    assert slices.status_code == 200
    assert "nvmf_tcp_tls_handshake" in slices.json()["items"][0]["excerpt"]

    card_search = await workbench_client.get(
        "/api/workbench/memory/search",
        params={"q": "cleanup is externally testable", "workspace_id": "ws-agent-cli-full"},
    )
    assert card_search.status_code == 200
    cards = [item for item in card_search.json()["items"] if item["kind"] == "evidence_card"]
    assert cards
    assert cards[0]["subject_key"] == "tls_cleanup_card"


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


async def test_workbench_prepare_task_run_api_lazily_materializes_rerun_plan(
    workbench_client,
    tmp_path,
):
    workflow = {
        "id": "prepare_only_rerun_plan",
        "name": "Prepare-only rerun plan",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text", "required": True}],
        "steps": [{"id": "render", "type": "report_render"}],
        "outputs": [{"id": "report", "type": "markdown"}],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201
    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "prepare_only_rerun_plan",
            "workspace_id": "ws-prepare-rerun",
            "repo_path": str(tmp_path),
            "inputs": {"module": "lib/nvmf"},
        },
    )
    assert prepared.status_code == 201
    task_run_id = prepared.json()["task_run_id"]
    task_dir = Path(prepared.json()["artifact_dir"])
    rerun_plan_path = task_dir / "task_rerun_plan.json"
    assert not rerun_plan_path.exists()

    rerun_plan_response = await workbench_client.get(
        f"/api/workbench/task-runs/{task_run_id}/rerun-plan"
    )

    assert rerun_plan_response.status_code == 200
    rerun_plan = rerun_plan_response.json()
    assert rerun_plan["task_run_id"] == task_run_id
    assert rerun_plan["status"] == "needs_rerun"
    assert rerun_plan["preserve_inputs"] is True
    assert rerun_plan["reuse_task_bundle"] is True
    assert rerun_plan["steps"] == []
    assert rerun_plan_path.exists()
    validation = await workbench_client.get(
        f"/api/workbench/task-runs/{task_run_id}/rerun-plan/validation"
    )
    assert validation.status_code == 200
    assert validation.json()["can_rerun"] is True


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
    assert paths["provider_readiness.json"]["kind"] == "provider_readiness"
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
    task_dir = Path(prepared.json()["artifact_dir"])
    secret = "sk-artifact-secret-value"
    (task_dir / "diagnostics.log").write_text(
        f"provider failed --api-key {secret}; token={secret}; Authorization: Bearer {secret}",
        encoding="utf-8",
    )
    text_diagnostics = {
        "diagnostics.yaml": f"token: {secret}\nstatus: failed\n",
        "diagnostics.html": f"<pre>Authorization: Bearer {secret}</pre>",
        "diagnostics.jsonl": f'{{"api_key":"{secret}","status":"failed"}}\n',
        "diagnostics.ndjson": f'{{"access_token":"{secret}","status":"failed"}}\n',
        "diagnostics.xml": f"<diagnostic password=\"{secret}\" />",
        "diagnostics.csv": f"name,secret\nagent,{secret}\n",
    }
    for filename, payload in text_diagnostics.items():
        (task_dir / filename).write_text(payload, encoding="utf-8")

    content = await workbench_client.get(
        f"/api/workbench/task-runs/{task_run_id}/artifacts/content/task_bundle.json"
    )

    assert content.status_code == 200
    body = content.json()
    assert body["relative_path"] == "task_bundle.json"
    assert body["kind"] == "task_bundle"
    assert body["sha256"]
    assert body["truncated"] is False
    assert body["content_redacted"] is False
    assert "artifact_content_workflow" in body["content"]

    escaped = await workbench_client.get(
        f"/api/workbench/task-runs/{task_run_id}/artifacts/content/%2E%2E/outside.txt"
    )
    assert escaped.status_code == 400

    artifacts = await workbench_client.get(f"/api/workbench/task-runs/{task_run_id}/artifacts")
    diagnostics = next(
        item for item in artifacts.json()["artifacts"] if item["relative_path"] == "diagnostics.log"
    )
    assert secret not in diagnostics["preview"]
    assert "<redacted>" in diagnostics["preview"]
    assert diagnostics["preview_redacted"] is True

    diagnostic_content = await workbench_client.get(
        f"/api/workbench/task-runs/{task_run_id}/artifacts/content/diagnostics.log"
    )
    assert diagnostic_content.status_code == 200
    diagnostic_body = diagnostic_content.json()
    assert secret not in diagnostic_body["content"]
    assert "<redacted>" in diagnostic_body["content"]
    assert diagnostic_body["content_redacted"] is True
    assert (task_dir / "diagnostics.log").read_text(encoding="utf-8").count(secret) == 3

    artifacts_by_path = {
        item["relative_path"]: item for item in (await workbench_client.get(
            f"/api/workbench/task-runs/{task_run_id}/artifacts"
        )).json()["artifacts"]
    }
    for relative_path in text_diagnostics:
        manifest_item = artifacts_by_path[relative_path]
        assert secret not in manifest_item["preview"]
        assert "<redacted>" in manifest_item["preview"]
        assert manifest_item["preview_redacted"] is True

        content_response = await workbench_client.get(
            f"/api/workbench/task-runs/{task_run_id}/artifacts/content/{relative_path}"
        )
        assert content_response.status_code == 200
        content_body = content_response.json()
        assert content_body["is_text"] is True
        assert content_body["content_redacted"] is True
        assert secret not in content_body["content"]
        assert "<redacted>" in content_body["content"]


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
    replay_plan = paths["agent_runs/discover/agent_replay_plan.json"]
    assert replay_plan["kind"] == "agent_replay_plan"
    replay_plan_content = await workbench_client.get(
        f"/api/workbench/task-runs/{task_run_id}/artifacts/content/agent_runs/discover/agent_replay_plan.json"
    )
    assert replay_plan_content.status_code == 200
    assert "readonly_env_required" in replay_plan_content.json()["content"]
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
    assert {
        item["artifact"]: item["status"]
        for item in validation["steps"][0]["replay_artifacts"]
    } == {
        "agent_run.json": "ok",
        "task_bundle.json": "ok",
        "workflow_snapshot.json": "ok",
        "agent_output_contract.json": "ok",
        "execution_input.json": "ok",
        "agent_replay_plan.json": "ok",
    }
    (Path(prepared.json()["artifact_dir"]) / "agent_runs" / "discover" / "agent_replay_plan.json").unlink()
    blocked_validation_response = await workbench_client.get(
        f"/api/workbench/task-runs/{task_run_id}/rerun-plan/validation"
    )
    assert blocked_validation_response.status_code == 200
    blocked_validation = blocked_validation_response.json()
    assert blocked_validation["status"] == "blocked"
    assert blocked_validation["can_rerun"] is False
    assert blocked_validation["steps"][0]["status"] == "blocked"
    assert blocked_validation["steps"][0]["reason"] == "agent replay artifact is missing"
    missing_replay = {
        item["artifact"]: item
        for item in blocked_validation["steps"][0]["replay_artifacts"]
    }
    assert missing_replay["agent_replay_plan.json"]["status"] == "blocked"
    assert missing_replay["agent_replay_plan.json"]["reason"] == "required replay artifact is missing"

    await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/execute",
        json={"timeout_sec": 10},
    )
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
    assert rerun["evidence_materialization"]["status"] == "partial"
    assert rerun["evidence_materialization"]["evidence_count"] == 0
    assert rerun["acceptance_audit"]["status"] == "incomplete"
    assert rerun["acceptance_audit"]["summary"]["missing_required"] > 0
    assert rerun["validation_after"]["status"] == "ready"
    artifacts_after_rerun = await workbench_client.get(
        f"/api/workbench/task-runs/{task_run_id}/artifacts"
    )
    paths_after_rerun = {
        item["relative_path"]: item for item in artifacts_after_rerun.json()["artifacts"]
    }
    assert paths_after_rerun["task_rerun_execution.json"]["kind"] == "task_rerun_execution"
    assert paths_after_rerun["task_rerun_history.json"]["kind"] == "task_rerun_history"
    assert paths_after_rerun["workflow_output_materialization.json"]["kind"] == "workflow_output_materialization"
    assert paths_after_rerun["task_acceptance_audit.json"]["kind"] == "task_acceptance_audit"
    rerun_manifest = json.loads(
        (Path(prepared.json()["artifact_dir"]) / "task_artifact_manifest.json")
        .read_text(encoding="utf-8")
    )
    rerun_manifest_paths = {
        item["relative_path"]: item for item in rerun_manifest["artifacts"]
    }
    assert rerun_manifest_paths["task_rerun_execution.json"]["kind"] == "task_rerun_execution"
    assert rerun_manifest_paths["task_rerun_history.json"]["kind"] == "task_rerun_history"
    assert (
        rerun_manifest_paths["workflow_output_materialization.json"]["kind"]
        == "workflow_output_materialization"
    )
    assert rerun_manifest_paths["task_acceptance_audit.json"]["kind"] == "task_acceptance_audit"
    rerun_execution_content = await workbench_client.get(
        f"/api/workbench/task-runs/{task_run_id}/artifacts/content/task_rerun_execution.json"
    )
    assert rerun_execution_content.status_code == 200
    assert rerun_execution_content.json()["kind"] == "task_rerun_execution"
    assert "validation_before" in rerun_execution_content.json()["content"]
    assert "evidence_materialization" in rerun_execution_content.json()["content"]
    assert "acceptance_audit" in rerun_execution_content.json()["content"]
    assert "agent_error" in rerun_execution_content.json()["content"]
    history_response = await workbench_client.get(
        f"/api/workbench/task-runs/{task_run_id}/rerun-plan/history"
    )
    assert history_response.status_code == 200
    history = history_response.json()
    assert history["task_run_id"] == task_run_id
    assert history["count"] == 1
    assert history["records"][0]["rerun_id"].startswith(f"{task_run_id}_rerun_")
    assert history["records"][0]["sequence"] == 1
    assert history["records"][0]["status"] == "executed"
    assert history["records"][0]["artifact"]["path"] == (
        f"task_reruns/{task_run_id}_rerun_1/task_rerun_execution.json"
    )
    assert history["records"][0]["execution"]["status"] == "invalid"
    assert history["records"][0]["evidence_materialization"]["status"] == "partial"
    assert history["records"][0]["acceptance_audit"]["status"] == "incomplete"
    first_rerun_artifact = Path(prepared.json()["artifact_dir"]) / history["records"][0]["artifact"]["path"]
    assert first_rerun_artifact.exists()

    second_rerun_response = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/rerun-plan/execute",
        json={"timeout_sec": 10},
    )
    assert second_rerun_response.status_code == 200
    second_history_response = await workbench_client.get(
        f"/api/workbench/task-runs/{task_run_id}/rerun-plan/history"
    )
    second_history = second_history_response.json()
    assert second_history["count"] == 2
    assert second_history["records"][1]["sequence"] == 2
    assert second_history["records"][1]["artifact"]["path"] == (
        f"task_reruns/{task_run_id}_rerun_2/task_rerun_execution.json"
    )
    assert second_history["records"][1]["artifact"]["path"] != history["records"][0]["artifact"]["path"]
    second_rerun_artifact = Path(prepared.json()["artifact_dir"]) / second_history["records"][1]["artifact"]["path"]
    assert second_rerun_artifact.exists()


async def test_workbench_task_run_acceptance_audit_api_records_required_evidence(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    from app.config import settings

    script_path = tmp_path / "agent_ok.py"
    script_path.write_text(
        "import json, os, pathlib, sys\n"
        "json.load(sys.stdin)\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "(root/'source_scope.json').write_text(json.dumps({'files': []}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow = {
        "id": "acceptance_audit_workflow",
        "name": "Acceptance audit workflow",
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
        "outputs": [
            {
                "id": "scope",
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
            "workflow_id": "acceptance_audit_workflow",
            "workspace_id": "ws-acceptance",
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

    response = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/acceptance-audit"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["task_run_id"] == task_run_id
    assert body["status"] == "ready"
    assert body["summary"]["required_checks"] >= 10
    assert body["summary"]["missing_required"] == 0
    checks = {item["id"]: item for item in body["checks"]}
    assert checks["task_bundle"]["status"] == "ok"
    assert checks["provider_readiness"]["status"] == "ok"
    assert checks["black_box_generation_policy"]["status"] == "ok"
    assert checks["provider_readiness_agent:local-python"]["status"] == "ok"
    assert checks["agent_run:discover"]["status"] == "ok"
    assert checks["agent_agent_replay_plan:discover"]["status"] == "ok"
    assert checks["agent_agent_replay_plan:discover"]["severity"] == "required"
    assert checks["agent_stdin_redaction:discover:execution_input"]["status"] == "ok"
    assert checks["agent_stdin_redaction:discover:execution_input"]["stdin_redacted"] is True
    assert checks["agent_stdin_redaction:discover:execution_input"]["stdin_json_sha256"]
    assert checks["agent_required_artifact:discover:source_scope.json"]["status"] == "ok"
    assert checks["workflow_output:scope"]["status"] == "ok"
    assert checks["workflow_execution"]["status"] == "ok"
    assert checks["task_artifact_manifest"]["status"] == "ok"
    artifact = Path(prepared.json()["artifact_dir"]) / "task_acceptance_audit.json"
    assert artifact.exists()
    assert json.loads(artifact.read_text(encoding="utf-8"))["status"] == "ready"
    artifacts = await workbench_client.get(f"/api/workbench/task-runs/{task_run_id}/artifacts")
    paths = {item["relative_path"]: item for item in artifacts.json()["artifacts"]}
    assert paths["task_acceptance_audit.json"]["kind"] == "task_acceptance_audit"
    audit_content = await workbench_client.get(
        f"/api/workbench/task-runs/{task_run_id}/artifacts/content/task_acceptance_audit.json"
    )
    assert audit_content.status_code == 200
    assert audit_content.json()["kind"] == "task_acceptance_audit"
    assert '"status": "ready"' in audit_content.json()["content"]


async def test_workbench_task_run_acceptance_audit_reports_missing_black_box_policy(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    from app.config import settings

    script_path = tmp_path / "agent_ok.py"
    script_path.write_text(
        "import json, os, pathlib, sys\n"
        "json.load(sys.stdin)\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "(root/'source_scope.json').write_text(json.dumps({'files': []}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow = {
        "id": "missing_black_box_policy_workflow",
        "name": "Missing black-box policy workflow",
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
        "outputs": [
            {
                "id": "scope",
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
            "workflow_id": "missing_black_box_policy_workflow",
            "workspace_id": "ws-missing-policy",
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
    (Path(prepared.json()["artifact_dir"]) / "black_box_generation_policy.json").unlink()

    response = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/acceptance-audit"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "incomplete"
    checks = {item["id"]: item for item in body["checks"]}
    assert checks["black_box_generation_policy"]["status"] == "missing"
    assert checks["black_box_generation_policy"]["reason"] == "artifact_missing"


async def test_workbench_task_run_acceptance_audit_records_semantic_import_artifact(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    from app.config import settings

    script_path = tmp_path / "agent_semantic_import_audit.py"
    script_path.write_text(
        "import json, os, pathlib, sys\n"
        "json.load(sys.stdin)\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "(root/'black_box_cases.json').write_text(json.dumps([\n"
        "  {'title':'TLS audit semantic case','steps':['connect'],"
        "'expected':['observable failure']}\n"
        "]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow = {
        "id": "acceptance_semantic_import_workflow",
        "name": "Acceptance semantic import workflow",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [
            {
                "id": "design",
                "type": "agent_task",
                "provider": "local-python",
                "required_artifacts": ["black_box_cases.json"],
            }
        ],
        "outputs": [
            {
                "id": "black_box_cases",
                "type": "test_cases",
                "from": "design",
                "artifact": "black_box_cases.json",
                "semantic_import": {"enabled": True},
            }
        ],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201
    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "acceptance_semantic_import_workflow",
            "workspace_id": "ws-acceptance-semantic",
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

    response = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/acceptance-audit"
    )

    assert response.status_code == 200
    body = response.json()
    checks = {item["id"]: item for item in body["checks"]}
    assert checks["semantic_import_outputs"]["status"] == "ok"
    assert checks["semantic_import_outputs"]["severity"] == "required"
    assert checks["semantic_output_import"]["status"] == "ok"
    assert checks["semantic_output_import"]["severity"] == "required"
    assert body["summary"]["missing_required"] == 0
    artifacts = await workbench_client.get(f"/api/workbench/task-runs/{task_run_id}/artifacts")
    paths = {item["relative_path"]: item for item in artifacts.json()["artifacts"]}
    assert (
        paths["semantic_import_outputs_by_step.json"]["kind"]
        == "semantic_import_outputs"
    )
    assert paths["semantic_output_import.json"]["kind"] == "semantic_output_import"


async def test_workbench_task_run_acceptance_audit_requires_declared_evidence_mapping(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    from app.config import settings

    script_path = tmp_path / "agent_evidence_mapping_audit.py"
    script_path.write_text(
        "import json, os, pathlib, sys\n"
        "json.load(sys.stdin)\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "(root/'risk_findings.json').write_text(json.dumps([\n"
        "  {'finding_id':'risk_tls_cleanup','file_path':'src/tls.c',"
        "'function':'tls_cleanup','summary':'cleanup branch risk'}\n"
        "]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow = {
        "id": "acceptance_evidence_mapping_workflow",
        "name": "Acceptance evidence mapping workflow",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [
            {
                "id": "hunt",
                "type": "agent_task",
                "provider": "local-python",
                "required_artifacts": ["risk_findings.json"],
            }
        ],
        "outputs": [
            {
                "id": "risk_findings",
                "type": "json",
                "from": "hunt",
                "artifact": "risk_findings.json",
                "evidence_memory": {
                    "enabled": True,
                    "kind": "resource_risk_finding",
                    "subject_key_field": "finding_id",
                    "path_field": "file_path",
                    "symbol_field": "function",
                    "status": "candidate_output",
                    "text_fields": ["summary", "function"],
                },
            }
        ],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201
    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "acceptance_evidence_mapping_workflow",
            "workspace_id": "ws-acceptance-evidence-map",
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
    assert executed.json()["acceptance_audit"]["status"] == "ready"
    materialization = Path(prepared.json()["artifact_dir"]) / "workflow_output_materialization.json"
    assert materialization.exists()
    materialization.unlink()

    response = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/acceptance-audit"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "incomplete"
    checks = {item["id"]: item for item in body["checks"]}
    assert checks["workflow_output_materialization"]["status"] == "missing"
    assert checks["workflow_output_materialization"]["severity"] == "required"
    assert (
        checks["workflow_output_materialization"]["reason"]
        == "evidence_memory_declared_but_materialization_artifact_missing"
    )


async def test_workbench_task_run_acceptance_audit_reports_missing_agent_artifact(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    from app.config import settings

    script_path = tmp_path / "agent_missing.py"
    script_path.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow = {
        "id": "acceptance_missing_workflow",
        "name": "Acceptance missing workflow",
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
        "outputs": [
            {
                "id": "scope",
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
            "workflow_id": "acceptance_missing_workflow",
            "workspace_id": "ws-acceptance-missing",
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

    response = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/acceptance-audit"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "incomplete"
    checks = {item["id"]: item for item in body["checks"]}
    missing = checks["agent_required_artifact:discover:source_scope.json"]
    assert missing["status"] == "missing"
    assert missing["severity"] == "required"
    assert "agent_runs/discover/source_scope.json" in missing["relative_path"]
    assert checks["task_rerun_plan"]["status"] == "ok"


async def test_workbench_task_run_acceptance_audit_flags_unavailable_agent_provider(
    workbench_client,
    tmp_path,
):
    from app.services.evidence_memory import EvidenceMemoryStore

    data_dir = Path(settings.data_dir)
    memory = EvidenceMemoryStore(data_dir / "workbench" / "evidence_memory.db")
    memory.record_analysis_run(
        run_id="deployment_probe:acceptance-ready",
        workspace_id="codetalk-deployment",
        repo_path=str(tmp_path),
        object_text="deployment probe acceptance-ready",
        workflow_id="workbench_deployment_probe",
        status="healthy",
    )
    memory.upsert_evidence_item(
        run_id="deployment_probe:acceptance-ready",
        workspace_id="codetalk-deployment",
        kind="provider_task_probe",
        subject_key="missing-agent-cli:agent_task_probe",
        status="accepted",
        source="deployment_probe",
        symbol="missing-agent-cli",
        reason="provider_task_probe missing-agent-cli ready; contract ok",
        text="provider_task_probe missing-agent-cli ready deployment_probe task contract",
        provenance={
            "provider": "missing-agent-cli",
            "probe_id": "acceptance-ready",
            "task_probe_status": "ready",
        },
    )

    workflow = {
        "id": "acceptance_unknown_provider_workflow",
        "name": "Acceptance unknown provider workflow",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [
            {
                "id": "discover",
                "type": "agent_task",
                "provider": "missing-agent-cli",
                "required_artifacts": ["source_scope.json"],
            }
        ],
        "outputs": [
            {
                "id": "scope",
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
            "workflow_id": "acceptance_unknown_provider_workflow",
            "workspace_id": "ws-acceptance-provider",
            "repo_path": str(tmp_path),
            "inputs": {"module": "nvme-tcp-tls"},
        },
    )
    task_run_id = prepared.json()["task_run_id"]

    response = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/acceptance-audit"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "incomplete"
    checks = {item["id"]: item for item in body["checks"]}
    provider_check = checks["provider_readiness_agent:missing-agent-cli"]
    assert provider_check["status"] == "missing"
    assert provider_check["severity"] == "required"
    assert provider_check["provider_status"] == "unknown_provider"
    assert provider_check["startup_probe_endpoint"] == "/api/tools/missing-agent-cli/startup-probe"
    assert provider_check["deployment_evidence_conflict"] is True
    assert provider_check["deployment_task_probe_status"] == "ready"
    assert provider_check["deployment_probe_id"] == "acceptance-ready"


async def test_workbench_task_run_acceptance_audit_flags_invalid_workflow_output(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    from app.config import settings

    script_path = tmp_path / "agent_bad_schema.py"
    script_path.write_text(
        "import json, os, pathlib\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "(root/'source_scope.json').write_text(json.dumps({'wrong': []}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow = {
        "id": "acceptance_invalid_output_workflow",
        "name": "Acceptance invalid output workflow",
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
        "outputs": [
            {
                "id": "scope",
                "type": "json",
                "from": "discover",
                "artifact": "source_scope.json",
                "schema": {"type": "object", "required": ["files"]},
            }
        ],
    }
    assert (await workbench_client.post("/api/workbench/workflows", json=workflow)).status_code == 201
    prepared = await workbench_client.post(
        "/api/workbench/task-runs/prepare",
        json={
            "workflow_id": "acceptance_invalid_output_workflow",
            "workspace_id": "ws-acceptance-invalid-output",
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

    response = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/acceptance-audit"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "incomplete"
    checks = {item["id"]: item for item in body["checks"]}
    output_check = checks["workflow_output:scope"]
    assert output_check["status"] == "missing"
    assert output_check["output_status"] == "invalid"
    assert output_check["reason"] == "schema_validation_failed"
    assert "missing required field: files" in output_check["schema_errors"]


async def test_workbench_task_run_artifacts_api_labels_agent_turn_snapshots(
    workbench_client,
    tmp_path,
    monkeypatch,
):
    from app.config import settings

    source = tmp_path / "src" / "tls.c"
    source.parent.mkdir()
    source.write_text("int nvmf_tcp_tls_handshake(void) { return 0; }\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(
        "Prefer mcp__fast-context__fast_context_search before local grep.\n",
        encoding="utf-8",
    )
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
    assert (
        paths["agent_runs/discover/turns/turn_1/agent_replay_plan.json"]["kind"]
        == "agent_turn_replay_plan"
    )
    assert paths["agent_runs/discover/turns/turn_2/task_bundle.json"]["kind"] == "agent_turn_task_bundle"
    assert paths["agent_runs/discover/turns/turn_2/source_slices.json"]["kind"] == "agent_turn_source_slices"
    assert (
        paths["agent_runs/discover/turns/turn_2/agent_replay_plan.json"]["kind"]
        == "agent_turn_replay_plan"
    )
    assert paths["task_artifact_manifest.json"]["kind"] == "task_artifact_manifest"
    manifest_content = await workbench_client.get(
        f"/api/workbench/task-runs/{task_run_id}/artifacts/content/task_artifact_manifest.json"
    )
    assert manifest_content.status_code == 200
    assert manifest_content.json()["kind"] == "task_artifact_manifest"
    assert "agent_runs/discover/agent_run_lifecycle.json" in manifest_content.json()["content"]
    content = await workbench_client.get(
        f"/api/workbench/task-runs/{task_run_id}/artifacts/content/"
        "agent_runs/discover/turns/turn_2/task_bundle.json"
    )
    assert content.status_code == 200
    assert content.json()["kind"] == "agent_turn_task_bundle"
    assert "requested_source_slices" in content.json()["content"]
    acceptance = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/acceptance-audit"
    )
    assert acceptance.status_code == 200
    checks = {item["id"]: item for item in acceptance.json()["checks"]}
    assert checks["agent_turn_task_bundle:discover:turn_1"]["status"] == "ok"
    assert checks["agent_turn_execution_input:discover:turn_1"]["severity"] == "required"
    assert checks["agent_turn_stdin_redaction:discover:turn_1:execution_input"]["status"] == "ok"
    assert checks["agent_turn_stdin_redaction:discover:turn_2:execution_input"]["status"] == "ok"
    assert checks["agent_turn_agent_replay_plan:discover:turn_1"]["status"] == "ok"
    assert checks["agent_turn_raw_output:discover:turn_2"]["status"] == "ok"
    assert checks["agent_turn_agent_replay_plan:discover:turn_2"]["status"] == "ok"
    assert checks["agent_turn_provider_diagnostics:discover:turn_2"]["status"] == "ok"
    assert checks["agent_source_slice_requests:discover"]["status"] == "ok"
    assert checks["agent_source_slices:discover"]["status"] == "ok"
    assert checks["agent_turn_source_slice_requests:discover:turn_1"]["status"] == "ok"
    assert checks["agent_turn_source_slices:discover:turn_2"]["status"] == "ok"

    task_dir = Path(prepared.json()["artifact_dir"])
    turn_1_execution_input = (
        task_dir
        / "agent_runs"
        / "discover"
        / "turns"
        / "turn_1"
        / "execution_input.json"
    )
    execution_payload = json.loads(turn_1_execution_input.read_text(encoding="utf-8"))
    execution_payload.pop("agent_instruction_policy", None)
    turn_1_execution_input.write_text(
        json.dumps(execution_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    corrupted_acceptance = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/acceptance-audit"
    )
    assert corrupted_acceptance.status_code == 200
    corrupted_body = corrupted_acceptance.json()
    assert corrupted_body["status"] == "incomplete"
    corrupted_checks = {item["id"]: item for item in corrupted_body["checks"]}
    missing_policy = corrupted_checks[
        "agent_turn_instruction_policy:discover:turn_1:execution_input"
    ]
    assert missing_policy["status"] == "missing"
    assert missing_policy["reason"] == "agent_instruction_policy_missing"

    execution_payload["agent_instruction_policy"] = {"files": [], "file_count": 0}
    execution_payload.pop("stdin_redacted", None)
    turn_1_execution_input.write_text(
        json.dumps(execution_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    redaction_acceptance = await workbench_client.post(
        f"/api/workbench/task-runs/{task_run_id}/acceptance-audit"
    )
    assert redaction_acceptance.status_code == 200
    redaction_checks = {item["id"]: item for item in redaction_acceptance.json()["checks"]}
    missing_redaction = redaction_checks[
        "agent_turn_stdin_redaction:discover:turn_1:execution_input"
    ]
    assert missing_redaction["status"] == "missing"
    assert missing_redaction["reason"] == "stdin_redacted_flag_missing"


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
