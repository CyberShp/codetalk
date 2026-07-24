import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


def test_evidence_memory_search_anchor_and_recent(tmp_path):
    from app.services.evidence_memory import EvidenceMemoryStore

    db_path = tmp_path / "evidence_memory.db"
    repo = tmp_path / "repo"
    source = repo / "nof" / "nvmf_tcp" / "transport" / "tls" / "tls.c"
    source.parent.mkdir(parents=True)
    source.write_text("int nvmf_tcp_tls_handshake(void) { return 0; }\n", encoding="utf-8")

    store = EvidenceMemoryStore(db_path)
    store.initialize()
    run_id = store.record_analysis_run(
        workspace_id="ws-nvme",
        repo_path=str(repo),
        object_text="nvme-tcp-tls",
        workflow_id="module_review",
        status="completed",
    )
    evidence_id = store.upsert_evidence_item(
        run_id=run_id,
        workspace_id="ws-nvme",
        kind="source_file",
        subject_key="nof/nvmf_tcp/transport/tls/tls.c",
        status="verified_local",
        source="ccr-code",
        path="nof/nvmf_tcp/transport/tls/tls.c",
        reason="Agent found source and CodeTalk validated the path.",
        confidence=0.92,
        text="nvme tcp tls nvmf_tcp transport tls source file",
    )
    slice_id = store.add_source_slice(
        evidence_id=evidence_id,
        file_path="nof/nvmf_tcp/transport/tls/tls.c",
        start_line=1,
        end_line=1,
        excerpt=source.read_text(encoding="utf-8"),
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    )

    results = store.search_analysis_memory("nvme tls", workspace_id="ws-nvme")
    assert [item.subject_key for item in results] == ["nof/nvmf_tcp/transport/tls/tls.c"]
    assert results[0].status == "verified_local"

    anchored = store.resolve_evidence_anchor("nof/nvmf_tcp/transport/tls/tls.c")
    assert anchored and anchored[0].evidence_id == evidence_id
    assert store.get_source_slice(slice_id).sha256 == hashlib.sha256(source.read_bytes()).hexdigest()

    recent = store.list_recent_analysis(workspace_id="ws-nvme")
    assert recent[0]["run_id"] == run_id
    assert recent[0]["object_text"] == "nvme-tcp-tls"


def test_workflow_dsl_accepts_agent_mcp_and_rejects_arbitrary_shell_steps():
    from app.services.workflow_dsl import WorkflowValidationError, validate_workflow_definition

    workflow = validate_workflow_definition({
        "id": "custom_mr_blackbox",
        "name": "MR black-box test design",
        "version": 1,
        "inputs": [
            {
                "id": "mr_link",
                "type": "external_link",
                "role": "merge_request",
                "resolver": "agent_mcp",
                "required": True,
            }
        ],
        "steps": [
            {
                "id": "collect_mr",
                "type": "agent_task",
                "goal": "mr_context_collect",
                "provider": "auto",
                "mcp_profile": "codehub-readonly",
                "required_artifacts": ["mr_snapshot.json", "diff.patch", "changed_files.json"],
            },
            {
                "id": "render",
                "type": "report_render",
                "template": "mr_test_report.md",
            },
        ],
        "outputs": [
            {"id": "report", "type": "markdown", "from": "{{steps.render.output}}"}
        ],
    })

    assert workflow.steps[0].mcp_profile == "codehub-readonly"
    assert workflow.inputs[0].resolver == "agent_mcp"

    bad = {
        "id": "unsafe",
        "name": "unsafe",
        "version": 1,
        "inputs": [],
        "steps": [{"id": "run_shell", "type": "powershell", "command": "Remove-Item *"}],
        "outputs": [],
    }
    with pytest.raises(WorkflowValidationError, match="unsupported workflow step type"):
        validate_workflow_definition(bad)


def test_workflow_dsl_rejects_duplicate_ids_and_missing_output_step():
    from app.services.workflow_dsl import WorkflowValidationError, validate_workflow_definition

    duplicate_input = {
        "id": "bad_inputs",
        "name": "Bad inputs",
        "version": 1,
        "inputs": [
            {"id": "module", "type": "free_text"},
            {"id": "module", "type": "file"},
        ],
        "steps": [{"id": "discover", "type": "agent_task"}],
        "outputs": [{"id": "scope", "type": "json", "from": "discover"}],
    }
    with pytest.raises(WorkflowValidationError, match="duplicate workflow input id: module"):
        validate_workflow_definition(duplicate_input)

    duplicate_output = {
        "id": "bad_outputs",
        "name": "Bad outputs",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [{"id": "discover", "type": "agent_task"}],
        "outputs": [
            {"id": "scope", "type": "json", "from": "discover"},
            {"id": "scope", "type": "markdown", "from": "discover"},
        ],
    }
    with pytest.raises(WorkflowValidationError, match="duplicate workflow output id: scope"):
        validate_workflow_definition(duplicate_output)

    missing_step = {
        "id": "bad_output_source",
        "name": "Bad output source",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [{"id": "discover", "type": "agent_task"}],
        "outputs": [{"id": "scope", "type": "json", "from": "missing_step"}],
    }
    with pytest.raises(WorkflowValidationError, match="unknown workflow output source step: missing_step"):
        validate_workflow_definition(missing_step)

    templated_source = validate_workflow_definition({
        "id": "templated_output_source",
        "name": "Templated output source",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [{"id": "discover", "type": "agent_task"}],
        "outputs": [{"id": "scope", "type": "json", "from": "{{steps.discover.output}}"}],
    })
    assert templated_source.outputs[0].source == "{{steps.discover.output}}"


def test_workflow_validation_rejects_canvas_contract_that_is_not_executable():
    from app.services.workflow_dsl import WorkflowValidationError, validate_workflow_definition

    with pytest.raises(
        WorkflowValidationError,
        match="canvas input node requirements_doc is missing from workflow inputs",
    ):
        validate_workflow_definition({
            "id": "fake_canvas",
            "name": "Fake Canvas",
            "version": 1,
            "inputs": [],
            "steps": [{"id": "agent_collect", "type": "agent_task"}],
            "outputs": [],
            "ui": {
                "layout": {
                    "nodes": [
                        {
                            "id": "input-node",
                            "kind": "input",
                            "source": "canvas",
                            "config": {"id": "requirements_doc", "type": "file"},
                        },
                        {
                            "id": "agent-node",
                            "kind": "agent",
                            "source": "canvas",
                            "config": {"id": "agent_collect"},
                        },
                    ],
                    "edges": [
                        {"id": "edge-1", "source": "input-node", "target": "agent-node"}
                    ],
                }
            },
        })


def test_workflow_validation_rejects_canvas_agent_edge_missing_dsl_dependency():
    from app.services.workflow_dsl import WorkflowValidationError, validate_workflow_definition

    with pytest.raises(
        WorkflowValidationError,
        match="canvas edge agent_a -> agent_b is missing from step dependencies",
    ):
        validate_workflow_definition({
            "id": "fake_dependency",
            "name": "Fake Dependency",
            "version": 1,
            "inputs": [],
            "steps": [
                {"id": "agent_a", "type": "agent_task"},
                {"id": "agent_b", "type": "agent_task"},
            ],
            "outputs": [],
            "ui": {
                "layout": {
                    "nodes": [
                        {"id": "node-a", "kind": "agent", "source": "canvas", "config": {"id": "agent_a"}},
                        {"id": "node-b", "kind": "agent", "source": "canvas", "config": {"id": "agent_b"}},
                    ],
                    "edges": [{"id": "edge-agent", "source": "node-a", "target": "node-b"}],
                }
            },
        })


def test_workflow_dsl_validates_user_defined_output_schema():
    from app.services.workflow_dsl import WorkflowValidationError, validate_workflow_definition

    valid = validate_workflow_definition({
        "id": "schema_workflow",
        "name": "Schema workflow",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [{"id": "discover", "type": "agent_task"}],
        "outputs": [
            {
                "id": "scope",
                "type": "json",
                "from": "discover",
                "artifact": "source_scope.json",
                "schema": {
                    "type": "object",
                    "required": ["files"],
                    "properties": {
                        "files": {"type": "array"},
                        "module": {"type": "string"},
                    },
                },
            }
        ],
    })
    assert valid.outputs[0].raw["schema"]["required"] == ["files"]

    invalid_type = dict(valid.raw)
    invalid_type["outputs"] = [
        {
            "id": "scope",
            "type": "json",
            "from": "discover",
            "schema": {"type": "map"},
        }
    ]
    with pytest.raises(WorkflowValidationError, match="unsupported schema type"):
        validate_workflow_definition(invalid_type)

    invalid_required = dict(valid.raw)
    invalid_required["outputs"] = [
        {
            "id": "scope",
            "type": "json",
            "from": "discover",
            "schema": {"type": "object", "required": ["files", 3]},
        }
    ]
    with pytest.raises(WorkflowValidationError, match="schema required must be a list of strings"):
        validate_workflow_definition(invalid_required)

    markdown_schema = dict(valid.raw)
    markdown_schema["outputs"] = [
        {
            "id": "report",
            "type": "markdown",
            "from": "discover",
            "schema": {"type": "object"},
        }
    ]
    with pytest.raises(WorkflowValidationError, match="schema requires json output type"):
        validate_workflow_definition(markdown_schema)


def test_workflow_dsl_validates_and_audits_semantic_output_import():
    from app.services.workflow_dsl import (
        WorkflowValidationError,
        audit_workflow_definition,
        validate_workflow_definition,
    )

    workflow = validate_workflow_definition({
        "id": "semantic_output_workflow",
        "name": "Semantic output workflow",
        "version": 1,
        "steps": [{"id": "design", "type": "agent_task"}],
        "outputs": [
            {
                "id": "black_box_cases",
                "type": "test_cases",
                "from": "design",
                "artifact": "black_box_cases.json",
                "semantic_import": {
                    "enabled": True,
                    "defaults": {"module": "nvmf_tcp/transport/tls"},
                },
            }
        ],
    })

    assert workflow.outputs[0].raw["semantic_import"]["defaults"]["module"] == (
        "nvmf_tcp/transport/tls"
    )

    bad_defaults = dict(workflow.raw)
    bad_defaults["outputs"] = [
        {
            "id": "black_box_cases",
            "type": "test_cases",
            "from": "design",
            "semantic_import": {"defaults": ["not", "an", "object"]},
        }
    ]
    with pytest.raises(WorkflowValidationError, match="semantic_import defaults must be an object"):
        validate_workflow_definition(bad_defaults)

    report_workflow = dict(workflow.raw)
    report_workflow["outputs"] = [
        {
            "id": "report",
            "type": "markdown",
            "from": "design",
            "artifact": "report.md",
            "semantic_import": True,
        }
    ]
    audit = audit_workflow_definition(report_workflow)

    assert audit["status"] == "warning"
    assert any(
        item["code"] == "semantic_import_on_non_test_cases_output"
        for item in audit["warnings"]
    )


def test_workflow_dsl_validates_and_audits_evidence_memory_mapping():
    import pytest

    from app.services.workflow_dsl import (
        WorkflowValidationError,
        audit_workflow_definition,
        validate_workflow_definition,
    )

    workflow = validate_workflow_definition({
        "id": "evidence_memory_output_workflow",
        "name": "Evidence memory output workflow",
        "version": 1,
        "steps": [{"id": "hunt", "type": "agent_task"}],
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
    })

    assert workflow.outputs[0].raw["evidence_memory"]["path_field"] == "file_path"

    bad_text_fields = dict(workflow.raw)
    bad_text_fields["outputs"] = [
        {
            "id": "risk_findings",
            "type": "json",
            "from": "hunt",
            "evidence_memory": {"text_fields": ["summary", 3]},
        }
    ]
    with pytest.raises(WorkflowValidationError, match="evidence_memory text_fields"):
        validate_workflow_definition(bad_text_fields)

    bad_enabled = dict(workflow.raw)
    bad_enabled["outputs"] = [
        {
            "id": "risk_findings",
            "type": "json",
            "from": "hunt",
            "evidence_memory": {"enabled": "yes"},
        }
    ]
    with pytest.raises(WorkflowValidationError, match="evidence_memory enabled"):
        validate_workflow_definition(bad_enabled)

    report_workflow = dict(workflow.raw)
    report_workflow["outputs"] = [
        {
            "id": "report",
            "type": "markdown",
            "from": "hunt",
            "artifact": "report.md",
            "evidence_memory": True,
        }
    ]
    audit = audit_workflow_definition(report_workflow)

    assert audit["status"] == "warning"
    assert any(
        item["code"] == "evidence_memory_on_non_json_output"
        for item in audit["warnings"]
    )


def test_workflow_store_persists_and_freezes_custom_workflow(tmp_path):
    from app.services.workflow_dsl import WorkflowStore

    store = WorkflowStore(tmp_path / "workflows.db")
    workflow_payload = {
        "id": "custom_patch_impact",
        "name": "Patch impact review",
        "version": 3,
        "inputs": [{"id": "patch_plan", "type": "file", "required": True}],
        "steps": [{"id": "analyze", "type": "agent_task", "goal": "patch_impact_review"}],
        "outputs": [{"id": "report", "type": "markdown", "from": "{{steps.analyze.output}}"}],
    }

    saved = store.save_workflow(workflow_payload)
    loaded = store.get_workflow("custom_patch_impact")
    snapshot = store.freeze_workflow_snapshot("custom_patch_impact")

    assert saved.version == 3
    assert loaded.name == "Patch impact review"
    assert snapshot["id"] == "custom_patch_impact"
    assert snapshot["version"] == 3
    assert [item.id for item in store.list_workflows()] == ["custom_patch_impact"]


def test_agent_run_harness_records_run_and_validates_agent_side_mr_artifacts(tmp_path):
    from app.services.agent_run_harness import AgentRunHarness, ArtifactValidationHarness

    artifact_dir = tmp_path / "task-artifacts"
    diff_text = "diff --git a/src/tls.c b/src/tls.c\n--- a/src/tls.c\n+++ b/src/tls.c\n@@ -1 +1 @@\n-old\n+new\n"
    diff_sha = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
    workflow_snapshot = {
        "id": "mr_test_design",
        "version": 1,
        "steps": [{"id": "collect_mr", "type": "agent_task"}],
    }
    task_bundle = {
        "task_id": "task-1",
        "input": {"mr_link": "https://codehub.local/project/merge_requests/1"},
        "required_artifacts": ["mr_snapshot.json", "diff.patch", "changed_files.json"],
    }

    harness = AgentRunHarness(artifact_dir)
    run = harness.create_run(
        provider="ccr-code",
        command=["ccr", "code"],
        cwd=str(tmp_path),
        workflow_snapshot=workflow_snapshot,
        task_bundle=task_bundle,
        mcp_profile="codehub-readonly",
    )
    harness.record_raw_output(run.run_id, stdout="agent stdout", stderr="token=secret-value")

    (artifact_dir / "mr_snapshot.json").write_text(
        json.dumps({
            "source": "agent_mcp",
            "mcp_profile": "codehub-readonly",
            "mr_url": task_bundle["input"]["mr_link"],
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

    validation = ArtifactValidationHarness(artifact_dir).validate_mr_artifacts(
        required_artifacts=task_bundle["required_artifacts"]
    )

    assert validation.status == "ok"
    assert validation.provenance_status == "agent_mcp_provenance"
    assert validation.accepted_artifacts == ["mr_snapshot.json", "diff.patch", "changed_files.json"]
    assert "secret-value" not in (artifact_dir / "raw_output.txt").read_text(encoding="utf-8")


def test_artifact_validation_rejects_windows_absolute_required_artifacts(tmp_path):
    from app.services.agent_run_harness import ArtifactValidationHarness

    artifact_dir = tmp_path / "task-artifacts"
    artifact_dir.mkdir()

    validation = ArtifactValidationHarness(artifact_dir).validate_required_artifacts(
        required_artifacts=["C:/outside/secret.json", "nested/../escape.json"]
    )

    assert validation.status == "invalid"
    assert validation.accepted_artifacts == []
    assert validation.rejected_artifacts == [
        {"artifact": "C:/outside/secret.json", "reason": "invalid_artifact_path"},
        {"artifact": "nested/../escape.json", "reason": "invalid_artifact_path"},
    ]
    assert all(item["path"] == "" for item in validation.rejected_artifact_details)


def test_mr_artifact_validation_rejects_unsafe_required_artifacts_before_reading(tmp_path):
    from app.services.agent_run_harness import ArtifactValidationHarness

    artifact_dir = tmp_path / "task-artifacts"
    artifact_dir.mkdir()

    validation = ArtifactValidationHarness(artifact_dir).validate_mr_artifacts(
        required_artifacts=["C:/outside/mr_snapshot.json", "../diff.patch"]
    )

    assert validation.status == "invalid"
    assert validation.provenance_status == "unverified_agent_claim"
    assert validation.accepted_artifacts == []
    assert validation.rejected_artifacts == [
        {"artifact": "C:/outside/mr_snapshot.json", "reason": "invalid_artifact_path"},
        {"artifact": "../diff.patch", "reason": "invalid_artifact_path"},
    ]


def test_agent_run_harness_executes_cli_with_task_bundle_and_audit_events(
    tmp_path,
    monkeypatch,
):
    from app.config import settings
    from app.services.agent_run_harness import AgentRunHarness

    runtime_temp_dir = tmp_path / "runtime-temp"
    monkeypatch.setattr(settings, "runtime_temp_dir", str(runtime_temp_dir))
    artifact_dir = tmp_path / "agent-run"
    output_file = artifact_dir / "agent_seen.json"
    script = (
        "import json, os, pathlib, sys; "
        "payload=json.load(sys.stdin); "
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({"
        "'readonly': os.environ.get('CODETALK_AGENT_READONLY'), "
        "'repo': os.environ.get('CODETALK_REPO_PATH'), "
        "'bundle_id': payload['task_bundle']['task_id']"
        "}), encoding='utf-8'); "
        "print('agent finished token=secret-value')"
    )
    harness = AgentRunHarness(artifact_dir)
    run = harness.create_run(
        run_id="agent_run_exec",
        provider="local-python",
        command=["python", "-c", script, str(output_file)],
        cwd=str(tmp_path),
        workflow_snapshot={"id": "wf"},
        task_bundle={
            "task_id": "task-42",
            "context_discovery_decision": {
                "fast-context": {
                    "requested_by_agent_instructions": True,
                    "codetalk_callable": False,
                    "agent_owned_possible": True,
                    "fallback_path": ["local_search", "agent_cli"],
                    "warnings": ["fast-context requested by AGENTS.md but backend MCP bridge is unavailable"],
                }
            },
        },
        mcp_profile="",
    )

    executed = harness.execute_run(run.run_id, timeout_sec=10)

    assert executed.status == "completed"
    assert executed.exit_code == 0
    assert output_file.exists()
    seen = json.loads(output_file.read_text(encoding="utf-8"))
    assert seen == {
        "readonly": "1",
        "repo": str(tmp_path),
        "bundle_id": "task-42",
    }
    assert "secret-value" not in (artifact_dir / "raw_output.txt").read_text(encoding="utf-8")
    execution_input = json.loads((artifact_dir / "execution_input.json").read_text(encoding="utf-8"))
    assert execution_input["stdin"]["task_bundle"]["task_id"] == "task-42"
    assert execution_input["command"] == ["python", "-c", script, str(output_file)]
    assert execution_input["launch_command"][1:] == execution_input["command"][1:]
    assert execution_input["process_command"][1:] == execution_input["command"][1:]
    assert execution_input["prompt_transport"] == "stdin"
    invocation = json.loads((artifact_dir / "agent_invocation.json").read_text(encoding="utf-8"))
    assert invocation["source"] == "workflow"
    assert invocation["run_id"] == "agent_run_exec"
    assert invocation["runtime"]["provider"] == "local-python"
    assert invocation["runtime"]["command"][:2] == ["python", "-c"]
    assert "secret-value" not in json.dumps(invocation["runtime"]["command"], ensure_ascii=False)
    assert invocation["cwd"] == str(tmp_path)
    assert invocation["repo_path"] == str(tmp_path)
    assert invocation["workflow"]["id"] == "wf"
    assert invocation["task_bundle"]["task_id"] == "task-42"
    assert invocation["execution_contract"]["runtime_type"] == "agent_runtime"
    assert invocation["execution_contract"]["typed_events"] == [
        "answer",
        "thinking",
        "diagnostic",
        "status",
        "tool_use",
        "tool_result",
        "artifact",
        "error",
        "done",
    ]
    assert invocation["artifact_contract"]["run_id"] == "agent_run_exec"
    symbol_rules = invocation["artifact_contract"]["evidence_rules"]["evidence_card_symbol_validation"]
    assert "exact filename" in symbol_rules["shell_files"]
    assert "empty symbols list" in symbol_rules["metadata_files"]
    assert "agent_invocation.json" in invocation["artifact_contract"]["audit_artifacts"]
    assert invocation["mcp_profile"] == ""
    assert invocation["skills"] == []
    assert invocation["session"] == execution_input["session_policy"]
    assert invocation["prompt"]["stdin_json_sha256"] == execution_input["stdin_json_sha256"]
    assert invocation["prompt"]["redacted"] is True
    assert invocation["artifact_dir"] == str(artifact_dir)
    from app.services.workbench_artifact_manifest import (
        workbench_artifact_audience,
        workbench_artifact_kind,
    )

    assert workbench_artifact_kind("agent_runs/collect/agent_invocation.json") == "agent_invocation"
    assert workbench_artifact_audience("agent_runs/collect/agent_invocation.json") == "diagnostic"
    assert execution_input["cwd"] == str(tmp_path)
    assert execution_input["timeout_sec"] == 10
    assert execution_input["turn_id"] == "turn_1"
    assert execution_input["task_bundle_sha256"]
    assert execution_input["workflow_snapshot_sha256"]
    assert execution_input["context_discovery_decision_summary"] == {
        "fast-context": {
            "requested_by_agent_instructions": True,
            "codetalk_callable": False,
            "agent_owned_possible": True,
            "fallback_path": ["local_search", "agent_cli"],
            "warnings": ["fast-context requested by AGENTS.md but backend MCP bridge is unavailable"],
        }
    }
    assert execution_input["stdin"]["context_discovery_decision_summary"] == (
        execution_input["context_discovery_decision_summary"]
    )
    run_payload = json.loads((artifact_dir / "agent_run.json").read_text(encoding="utf-8"))
    assert run_payload["turn_id"] == "turn_1"
    events = [
        json.loads(line)
        for line in (artifact_dir / "runtime_events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert all(event.get("turn_id") == "turn_1" for event in events)
    runtime_tmp_dir = Path(execution_input["env_hints"]["TEMP"])
    assert runtime_tmp_dir.parent == artifact_dir
    assert runtime_tmp_dir.name.startswith(".runtime-tmp-")
    assert not runtime_tmp_dir.exists()
    assert not list(artifact_dir.glob(".runtime-codex-home-*"))
    assert execution_input["env_hints"] == {
        "CODETALK_AGENT_READONLY": "1",
            "CODETALK_REPO_PATH": str(tmp_path),
            "CODETALK_AGENT_ARTIFACT_DIR": str(artifact_dir),
            "CODETALK_TEMP_DIR": str(runtime_tmp_dir),
            "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_KEY_0": "core.excludesFile",
        "GIT_CONFIG_VALUE_0": "/dev/null",
        "TEMP": str(runtime_tmp_dir),
        "TMP": str(runtime_tmp_dir),
        "TMPDIR": str(runtime_tmp_dir),
        "TMPPREFIX": str(runtime_tmp_dir / "zsh"),
    }
    assert execution_input["stdin_json_sha256"]
    events = (artifact_dir / "runtime_events.jsonl").read_text(encoding="utf-8")
    assert "agent_execution_input_prepared" in events
    assert "agent_run_started" in events
    assert "agent_run_completed" in events
    assert not list(runtime_temp_dir.glob("codetalk-agent-probe-*"))


def test_agent_run_harness_removes_codex_runtime_home_after_execution(tmp_path):
    from app.services.agent_run_harness import AgentRunHarness

    artifact_dir = tmp_path / "codex-agent-run"
    script = "import json, sys; json.load(sys.stdin); print('audit complete')"
    harness = AgentRunHarness(artifact_dir)
    run = harness.create_run(
        run_id="agent_run_codex_cleanup",
        provider="agent-runtime:default-codex",
        command=[sys.executable, "-c", script],
        cwd=str(tmp_path),
        workflow_snapshot={"id": "wf"},
        task_bundle={"task_id": "task-codex-cleanup"},
    )

    executed = harness.execute_run(run.run_id, timeout_sec=10)

    assert executed.status == "completed"
    assert not list(artifact_dir.glob(".runtime-tmp-*"))
    assert not list(artifact_dir.glob(".runtime-codex-home-*"))


def test_workflow_harness_projects_managed_claude_oauth_without_exposing_keychain(
    monkeypatch,
    tmp_path,
):
    from app.services import agent_cli_bridge, agent_run_harness

    class SecurityResult:
        returncode = 0
        stdout = '{"claudeAiOauth":{"accessToken":"workflow-oauth-token","refreshToken":"do-not-pass"}}'

    monkeypatch.setattr(agent_cli_bridge.sys, "platform", "darwin")
    monkeypatch.setattr(
        agent_cli_bridge.shutil,
        "which",
        lambda command: {
            "security": "/usr/bin/security",
            "claude": "/usr/local/bin/claude",
        }.get(command),
    )
    monkeypatch.setattr(
        agent_cli_bridge.subprocess,
        "run",
        lambda *_args, **_kwargs: SecurityResult(),
    )
    monkeypatch.setattr(
        agent_run_harness,
        "_base_agent_process_env_for_harness",
        lambda **_kwargs: {"PATH": "/usr/bin"},
        raising=False,
    )

    env = agent_run_harness._agent_process_env_for_harness(
        provider="agent-runtime:default-claude-code",
        repo_path="/repo",
        artifact_dir=tmp_path / "artifacts",
        command=["/usr/local/bin/claude"],
        prompt_transport="claude_print_arg",
    )

    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "workflow-oauth-token"
    assert "do-not-pass" not in json.dumps(env)


def test_workflow_harness_does_not_project_claude_oauth_to_custom_wrapper(
    monkeypatch,
    tmp_path,
):
    from app.services import agent_cli_bridge, agent_run_harness

    monkeypatch.setattr(agent_cli_bridge.sys, "platform", "darwin")
    monkeypatch.setattr(
        agent_cli_bridge.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("security must not run")
        ),
    )
    monkeypatch.setattr(
        agent_run_harness,
        "_base_agent_process_env_for_harness",
        lambda **_kwargs: {"PATH": "/usr/bin"},
        raising=False,
    )

    env = agent_run_harness._agent_process_env_for_harness(
        provider="agent-runtime:custom-claude-wrapper",
        repo_path="/repo",
        artifact_dir=tmp_path / "artifacts",
        command=["/tmp/custom-claude-wrapper"],
        prompt_transport="claude_print_arg",
    )

    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env


def test_workflow_harness_adds_vetted_macos_analysis_tools_to_agent_path(
    monkeypatch,
):
    """An isolated service PATH must still expose CodeTalk-approved local tools."""
    from app.services import agent_run_harness

    monkeypatch.setattr(
        agent_run_harness,
        "_vetted_analysis_tool_bin_paths",
        lambda **_kwargs: ["/opt/homebrew/bin", "/usr/bin"],
        raising=False,
    )

    environment = agent_run_harness._prepend_vetted_analysis_tool_paths(
        {"PATH": "/usr/bin:/bin"},
        platform_name="darwin",
    )

    assert environment["PATH"].split(os.pathsep) == [
        "/opt/homebrew/bin",
        "/usr/bin",
        "/bin",
    ]


def test_agent_run_harness_refreshes_output_contract_for_rerun(tmp_path):
    from app.services.agent_run_harness import AgentRunHarness

    artifact_dir = tmp_path / "agent-rerun"
    harness = AgentRunHarness(artifact_dir)
    run = harness.create_run(
        run_id="agent_rerun_contract",
        provider="local-python",
        command=["python", "-c", "import json, sys; json.load(sys.stdin)"],
        cwd=str(tmp_path),
        workflow_snapshot={"id": "source_flow"},
        task_bundle={
            "task_id": "task-rerun",
            "retry_validation_feedback": {
                "rejected_count": 1,
                "rejected_artifact_details": [{"symbol": "conceptual label"}],
                "instruction": "必须修正被拒绝证据。",
            },
            "retry_quality_feedback": {
                "score": 42,
                "issue_count": 1,
                "issues": [{"code": "non_actionable_mitigation"}],
                "instruction": "必须逐项修正质量问题。",
            },
        },
    )
    (artifact_dir / "agent_output_contract.json").write_text(
        '{"contract_version": 0, "evidence_rules": {}}',
        encoding="utf-8",
    )

    executed = harness.execute_run(run.run_id, timeout_sec=10)

    assert executed.status == "completed"
    execution_input = json.loads(
        (artifact_dir / "execution_input.json").read_text(encoding="utf-8")
    )
    contract = execution_input["stdin"]["agent_output_contract"]
    assert contract["contract_version"] == 1
    assert "exact filename" in contract["evidence_rules"]["evidence_card_symbol_validation"]["shell_files"]
    assert contract["retry_validation_feedback"]["rejected_count"] == 1
    assert contract["retry_quality_feedback"]["score"] == 42


def test_quality_retry_prompt_bundle_preserves_user_input_and_omits_redundant_discovery_context():
    from app.services.agent_run_harness import _task_bundle_for_agent_prompt

    bundle = {
        "inputs": {"analysis_object": "line one\nline two\n用户输入不能丢"},
        "input_materials": {"read_order": ["requirements.md"]},
        "goal": "fix rejected deliverables",
        "retry_quality_feedback": {
            "affected_artifacts": ["sfmea.json"],
            "protected_artifacts": ["evidence_cards.json"],
        },
        "context_bundle": {"large": "x" * 1000},
        "local_source_context": {"large": "x" * 1000},
        "source_read_chain": [{"large": "x" * 1000}],
        "evidence_consumption_trajectory": [{"large": "x" * 1000}],
        "provider_snapshot": {"large": "x" * 1000},
        "workflow_contract": {"large": "x" * 1000},
    }

    prompt_bundle = _task_bundle_for_agent_prompt(bundle)

    assert prompt_bundle["inputs"] == bundle["inputs"]
    assert prompt_bundle["input_materials"] == bundle["input_materials"]
    assert prompt_bundle["retry_quality_feedback"] == bundle["retry_quality_feedback"]
    assert "context_bundle" not in prompt_bundle
    assert "local_source_context" not in prompt_bundle
    assert "workflow_contract" not in prompt_bundle
    assert set(prompt_bundle["quality_retry_context_omissions"]) >= {
        "context_bundle",
        "local_source_context",
        "workflow_contract",
    }


def test_initial_agent_prompt_compacts_duplicate_context_without_losing_user_text():
    from app.services.agent_run_harness import (
        _artifact_contract_reference,
        _execution_contract_for_agent_prompt,
        _output_contract_for_agent_prompt,
        _task_bundle_for_agent_prompt,
    )

    user_text = "第一行\n第二行\n用户输入必须逐字保留"
    task_bundle = {
        "task_id": "task-compact",
        "goal": user_text,
        "inputs": {"analysis_target": user_text},
        "input_materials": {
            "materials": [{"path": "design.md", "user_note": user_text}]
        },
        "local_source_context": {"large": "x" * 300_000},
        "context_bundle": {"large": "x" * 300_000},
        "execution_contract": {"large": "x" * 300_000},
        "test_activity_contract": {"large": "x" * 300_000},
    }
    execution_contract = {
        "goal": user_text,
        "repo_path": "/repo",
        "user_inputs": [{"value": user_text}],
        "execution_rules": {
            "path_resolution": {
                "source_reads": "Use $CODETALK_REPO_PATH/<repo-relative-path>.",
            }
        },
        "source_context": {"files": [{"excerpt": "source evidence"}]},
        "test_activity_contract": {"duplicate": "x" * 300_000},
    }
    output_contract = {
        "contract_version": 1,
        "required_artifacts": ["report.md"],
        "execution_contract": execution_contract,
        "test_activity_contract": {"duplicate": "x" * 300_000},
    }

    compact_bundle = _task_bundle_for_agent_prompt(task_bundle)
    compact_execution = _execution_contract_for_agent_prompt(execution_contract)
    compact_output = _output_contract_for_agent_prompt(output_contract)
    artifact_reference = _artifact_contract_reference(
        compact_output,
        artifact_dir="/artifacts",
    )
    payload = json.dumps(
        {
            "task_bundle": compact_bundle,
            "execution_contract": compact_execution,
            "agent_output_contract": compact_output,
            "artifact_contract": artifact_reference,
        },
        ensure_ascii=False,
    )

    assert compact_bundle["goal"] == user_text
    assert compact_bundle["inputs"]["analysis_target"] == user_text
    assert compact_bundle["input_materials"]["materials"][0]["user_note"] == user_text
    assert compact_execution["user_inputs"][0]["value"] == user_text
    assert compact_execution["execution_rules"]["path_resolution"]["source_reads"] == (
        "Use $CODETALK_REPO_PATH/<repo-relative-path>."
    )
    assert len(payload) < 20_000
    assert "local_source_context" not in compact_bundle
    assert "test_activity_contract" not in compact_execution
    assert "execution_contract" not in compact_output
    assert artifact_reference["required_artifacts"] == ["report.md"]


def test_agent_prompt_limits_unknown_extension_context_but_preserves_user_text(
    monkeypatch,
):
    import app.services.agent_run_harness as harness_module

    user_text = "用户输入的每一个字都必须保留\n第二行"
    monkeypatch.setattr(
        harness_module,
        "_AGENT_PROMPT_EXTENSION_BUDGET_CHARACTERS",
        1024,
        raising=False,
    )
    task_bundle = {
        "task_id": "task-extension-budget",
        "goal": user_text,
        "inputs": {"analysis_target": user_text},
        "input_materials": {"design_doc": {"user_note": user_text}},
        "plugin_context": {"diagnostics": "x" * 5000},
    }

    prompt_bundle = harness_module._task_bundle_for_agent_prompt(task_bundle)

    assert prompt_bundle["goal"] == user_text
    assert prompt_bundle["inputs"]["analysis_target"] == user_text
    assert prompt_bundle["input_materials"]["design_doc"]["user_note"] == user_text
    assert "plugin_context" not in prompt_bundle
    assert "plugin_context" in prompt_bundle["context_omissions"]


def test_agent_prompt_budget_counts_key_names_and_bounds_omission_metadata(monkeypatch):
    import app.services.agent_run_harness as harness_module

    monkeypatch.setattr(
        harness_module,
        "_AGENT_PROMPT_EXTENSION_BUDGET_CHARACTERS",
        256,
    )
    enormous_key = "diagnostics-" + ("k" * 5000)
    task_bundle = {
        "goal": "保留用户目标",
        enormous_key: "ok",
        **{f"extra-{index}": "x" * 300 for index in range(100)},
    }

    prompt_bundle = harness_module._task_bundle_for_agent_prompt(task_bundle)
    serialized = json.dumps(prompt_bundle, ensure_ascii=False)

    assert prompt_bundle["goal"] == "保留用户目标"
    assert enormous_key not in prompt_bundle
    assert enormous_key not in serialized
    assert prompt_bundle["context_omission_count"] == 101
    assert len(prompt_bundle["context_omissions"]) <= 64
    assert len(serialized) < 10_000


def test_workflow_command_resolution_uses_windows_pathext(monkeypatch):
    from app.services import agent_run_harness

    monkeypatch.setattr(agent_run_harness.os, "name", "nt")
    monkeypatch.setattr(
        agent_run_harness.shutil,
        "which",
        lambda command: r"C:\Users\tester\AppData\Roaming\npm\opencode.cmd"
        if command == "opencode"
        else None,
    )

    assert agent_run_harness._resolve_local_process_command(["opencode", "run"]) == [
        r"C:\Users\tester\AppData\Roaming\npm\opencode.cmd",
        "run",
    ]


def test_idle_timeout_observes_output_without_newlines(tmp_path):
    from app.services.agent_run_harness import _run_cancellable_subprocess

    script = (
        "import sys,time\n"
        "for _ in range(8):\n"
        " sys.stdout.write('.')\n"
        " sys.stdout.flush()\n"
        " time.sleep(0.1)\n"
    )
    result = _run_cancellable_subprocess(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        input_bytes=None,
        timeout=5,
        idle_timeout=0.25,
        env=dict(os.environ),
    )

    assert result.timed_out is False
    assert result.exit_code == 0
    assert result.stdout == "........"


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group semantics")
def test_workflow_subprocess_cleans_descendant_after_parent_exits(tmp_path):
    from app.services.agent_run_harness import _run_cancellable_subprocess

    child_pid_file = tmp_path / "workflow-orphan.pid"
    child_script = (
        "import os,signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"open({str(child_pid_file)!r}, 'w').write(str(os.getpid())); "
        "time.sleep(60)"
    )
    parent_script = (
        "import subprocess,sys; "
        f"subprocess.Popen([sys.executable, '-c', {child_script!r}], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
        "stderr=subprocess.DEVNULL, close_fds=True)"
    )
    child_pid = 0
    try:
        result = _run_cancellable_subprocess(
            [sys.executable, "-c", parent_script],
            cwd=str(tmp_path),
            input_bytes=None,
            timeout=5,
            idle_timeout=2,
            env=dict(os.environ),
        )
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))

        assert result.exit_code == 0
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = subprocess.run(
                ["ps", "-o", "stat=", "-p", str(child_pid)],
                check=False,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if not state or state.startswith("Z"):
                break
            time.sleep(0.02)
        else:
            pytest.fail("workflow descendant survived after parent exited")
    finally:
        if child_pid:
            try:
                os.kill(child_pid, 9)
            except ProcessLookupError:
                pass


def test_workflow_agent_process_output_compacts_codex_command_updates():
    from app.services.agent_run_harness import _public_agent_process_output

    stream_state: dict[object, object] = {}
    updated = json.dumps(
        {
            "type": "item.updated",
            "item": {
                "type": "command_execution",
                "command": "rg tls lib/nvmf",
                "aggregated_output": "\n".join(f"source {line}" for line in range(300)),
            },
        }
    )
    completed = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "rg tls lib/nvmf",
                "status": "completed",
                "exit_code": 0,
                "aggregated_output": "\n".join(f"source {line}" for line in range(300)),
            },
        }
    )

    assert _public_agent_process_output("stdout", updated, stream_state=stream_state) == ""
    visible = _public_agent_process_output("stdout", completed, stream_state=stream_state)
    assert "command: rg tls lib/nvmf" in visible
    assert "294 lines omitted" in visible
    assert "source 150" not in visible


def test_workflow_agent_process_output_hides_known_codex_runtime_noise():
    from app.services.agent_run_harness import _public_agent_process_output

    stream_state: dict[object, object] = {}
    skill_warning = (
        "2026-07-13T01:23:42Z ERROR codex_core_skills::loader: "
        "failed to read skills symlink dir /repo/.codex/skills/tdd: "
        "Operation not permitted (os error 1)"
    )
    cache_warning = (
        "2026-07-13T01:23:42Z WARN failed to load models cache from "
        "/tmp/runtime-codex-home/models_cache.json: Operation not permitted"
    )

    assert _public_agent_process_output(
        "stderr", skill_warning, stream_state=stream_state
    ) == ""
    assert _public_agent_process_output(
        "stderr", cache_warning, stream_state=stream_state
    ) == ""
    assert "request timed out" in _public_agent_process_output(
        "stderr", "ERROR: Reconnecting... request timed out", stream_state=stream_state
    )


def test_agent_run_harness_preserves_cancelled_transport_attempt(tmp_path):
    import time

    from app.services.agent_run_harness import AgentRunHarness

    artifact_dir = tmp_path / "agent-run-cancelled"
    harness = AgentRunHarness(artifact_dir)
    run = harness.create_run(
        run_id="agent_run_cancelled",
        provider="local-python",
        command=["python", "-c", "import time; time.sleep(10)"],
        cwd=str(tmp_path),
        workflow_snapshot={"id": "wf"},
        task_bundle={"task_id": "task-cancelled"},
    )
    started = time.monotonic()

    executed = harness.execute_run(
        run.run_id,
        timeout_sec=10,
        is_cancelled=lambda: time.monotonic() - started > 0.1,
    )

    execution_input = json.loads(
        (artifact_dir / "execution_input.json").read_text(encoding="utf-8")
    )
    assert executed.status == "cancelled"
    assert execution_input["transport_attempts"][0]["status"] == "cancelled"


def test_codex_disables_inner_sandbox_only_inside_active_outer_sandbox():
    from pathlib import Path

    from app.services.agent_run_harness import (
        _finalize_invocation_candidates_for_sandbox,
        _prefer_native_macos_git_path,
        _task_run_read_roots,
    )

    command = ["/Users/dev/.local/bin/codex", "exec", "--json"]

    finalized = _finalize_invocation_candidates_for_sandbox(
        [(command, b"prompt", "codex_exec_json", "configured")],
        sandbox_active=True,
    )
    assert finalized == [(
        [
        "/Users/dev/.local/bin/codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--json",
        ],
        b"prompt",
        "codex_exec_json",
        "configured",
    )]
    assert _finalize_invocation_candidates_for_sandbox(
        [(command, b"", "codex_exec_json", "")], sandbox_active=False
    )[0][0] == command
    assert _finalize_invocation_candidates_for_sandbox(
        [(["python", "fake_codex.py", "exec", "--json"], b"", "stdin", "")],
        sandbox_active=True,
    )[0][0] == ["python", "fake_codex.py", "exec", "--json"]
    assert _task_run_read_roots(
        Path("/tmp/task_runs/task-1/agent_runs/analyze_module")
    ) == [Path("/tmp/task_runs/task-1").resolve()]
    assert _task_run_read_roots(Path("/tmp/unrelated")) == []
    native_git = Path("/native/toolchain/bin/git")
    assert _prefer_native_macos_git_path(
        {"PATH": "/usr/bin:/bin"},
        platform_name="darwin",
        native_git=native_git,
        exists=lambda path: path == native_git,
    )["PATH"] == "/native/toolchain/bin:/usr/bin:/bin"


def test_agent_run_harness_decodes_noisy_gbk_output(tmp_path):
    from app.services.agent_run_harness import AgentRunHarness

    artifact_dir = tmp_path / "agent-run-gbk"
    script = (
        "import sys; "
        "sys.stdout.write('\\x1b[32m47%\\n12/100\\n'); "
        "sys.stdout.buffer.write(bytes([0x80, 0x81, 0x8D, 0x90, 0x9D]) + b'\\n'); "
        "sys.stdout.flush(); "
        "sys.stdout.write('\\r\\x1b[2K⠋ 12\\r\\x1b[2K⠙ 47\\r\\x1b[2K'); "
        "sys.stdout.flush(); "
        "sys.stdout.buffer.write('源码证据：连接失败\\n'.encode('gbk')); "
        "sys.stdout.write('FINAL_NOISE_CLEAN_ANSWER: 已完成源码分析。\\n'); "
        "sys.stdout.write('\\x1b[0m'); "
        "sys.stdout.flush()"
    )
    harness = AgentRunHarness(artifact_dir)
    run = harness.create_run(
        run_id="agent_run_gbk",
        provider="local-python",
        command=["python", "-c", script],
        cwd=str(tmp_path),
        workflow_snapshot={"id": "wf"},
        task_bundle={"task_id": "task-gbk"},
    )

    executed = harness.execute_run(run.run_id, timeout_sec=10)

    assert executed.status == "completed"
    raw_output = (artifact_dir / "raw_output.txt").read_text(encoding="utf-8")
    execution_input = json.loads((artifact_dir / "execution_input.json").read_text(encoding="utf-8"))
    first_attempt = execution_input["transport_attempts"][0]
    assert "源码证据：连接失败" in raw_output
    assert "FINAL_NOISE_CLEAN_ANSWER: 已完成源码分析。" in raw_output
    assert "源码证据：连接失败" in first_attempt["stdout_excerpt"]
    assert "47%" not in raw_output
    assert "12/100" not in raw_output
    assert "�" not in raw_output


def test_agent_run_harness_injects_custom_provider_env_without_persisting_secret(
    tmp_path, monkeypatch
):
    from app.config import settings
    from app.services.agent_run_harness import AgentRunHarness

    artifact_dir = tmp_path / "agent-run-env"
    output_file = artifact_dir / "agent_env.json"
    script = (
        "import json, os, pathlib, sys; "
        "json.load(sys.stdin); "
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({"
        "'profile': os.environ.get('CORP_AGENT_PROFILE'), "
        "'token': os.environ.get('CORP_AGENT_TOKEN')"
        "}), encoding='utf-8')"
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {
            "id": "corp-agent",
            "command": "python",
            "prompt_transport": "stdin",
            "env_hints": {
                "CORP_AGENT_PROFILE": "innernet",
                "CORP_AGENT_TOKEN": "token=raw-secret-value",
            },
        }
    ])
    harness = AgentRunHarness(artifact_dir)
    run = harness.create_run(
        run_id="agent_run_env",
        provider="corp-agent",
        command=["python", "-c", script, str(output_file)],
        cwd=str(tmp_path),
        workflow_snapshot={"id": "wf"},
        task_bundle={"task_id": "task-env"},
    )

    executed = harness.execute_run(run.run_id, timeout_sec=10)

    assert executed.status == "completed"
    seen = json.loads(output_file.read_text(encoding="utf-8"))
    assert seen == {
        "profile": "innernet",
        "token": "token=raw-secret-value",
    }
    execution_input_text = (artifact_dir / "execution_input.json").read_text(encoding="utf-8")
    assert "raw-secret-value" not in execution_input_text
    execution_input = json.loads(execution_input_text)
    assert execution_input["env_hints"]["CORP_AGENT_PROFILE"] == "innernet"
    assert execution_input["env_hints"]["CORP_AGENT_TOKEN"] == "<redacted>"


def test_agent_run_harness_uses_provider_prompt_transport_for_argv_last(
    tmp_path, monkeypatch
):
    from app.config import settings
    from app.services.agent_run_harness import AgentRunHarness

    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {
            "id": "local-argv-agent",
            "command": "python",
            "prompt_transport": "argv_last",
        }
    ])
    artifact_dir = tmp_path / "agent-run-argv"
    output_file = artifact_dir / "agent_seen.json"
    script = (
        "import json, pathlib, sys; "
        "payload=json.loads(sys.argv[2]); "
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({"
        "'bundle_id': payload['task_bundle']['task_id'], "
        "'stdin_empty': sys.stdin.read() == ''"
        "}), encoding='utf-8')"
    )
    harness = AgentRunHarness(artifact_dir)
    run = harness.create_run(
        run_id="agent_run_argv",
        provider="local-argv-agent",
        command=["python", "-c", script, str(output_file)],
        cwd=str(tmp_path),
        workflow_snapshot={"id": "wf"},
        task_bundle={"task_id": "task-argv"},
    )

    executed = harness.execute_run(run.run_id, timeout_sec=10)

    assert executed.status == "completed"
    seen = json.loads(output_file.read_text(encoding="utf-8"))
    assert seen == {"bundle_id": "task-argv", "stdin_empty": True}
    execution_input = json.loads((artifact_dir / "execution_input.json").read_text(encoding="utf-8"))
    assert execution_input["command"] == ["python", "-c", script, str(output_file)]
    assert execution_input["process_command"][1:-1] == execution_input["command"][1:]
    assert json.loads(execution_input["process_command"][-1])["task_bundle"]["task_id"] == "task-argv"
    assert execution_input["prompt_transport"] == "argv"


def test_codex_runtime_home_copies_skills_and_keeps_state_in_artifacts(
    tmp_path, monkeypatch
):
    from app.services.agent_run_harness import _prepare_isolated_codex_home

    real_home = tmp_path / "real-codex-home"
    real_home.mkdir()
    (real_home / "auth.json").write_text("auth.json", encoding="utf-8")
    (real_home / "config.toml").write_text(
        '\n'.join([
            'model = "gpt-5.5"',
            'model_reasoning_effort = "high"',
            'network_access = "enabled"',
            '[plugins."browser@openai-bundled"]',
            'enabled = true',
            '[marketplaces.openai-bundled]',
            'source = "/host/private/plugins"',
            '[mcp_servers.node_repl]',
            'command = "/host/private/node_repl"',
        ]),
        encoding="utf-8",
    )
    (real_home / "models_cache.json").write_text("models_cache.json", encoding="utf-8")
    (real_home / "skills").mkdir()
    artifact_dir = tmp_path / "agent-run"
    artifact_dir.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(real_home))

    runtime_home, read_targets = _prepare_isolated_codex_home(
        provider="agent-runtime:default-codex",
        command=["/usr/local/bin/codex", "exec"],
        artifact_dir=artifact_dir,
    )

    assert runtime_home.parent == artifact_dir.resolve()
    assert runtime_home.name.startswith(".runtime-codex-home-")
    assert set(read_targets) == {(real_home / "auth.json").resolve()}
    assert (runtime_home / "auth.json").is_symlink()
    assert not (runtime_home / "config.toml").is_symlink()
    isolated_config = (runtime_home / "config.toml").read_text(encoding="utf-8")
    assert 'model = "gpt-5.5"' in isolated_config
    assert 'model_reasoning_effort = "high"' in isolated_config
    assert 'network_access = "enabled"' in isolated_config
    assert "plugins." not in isolated_config
    assert "marketplaces." not in isolated_config
    assert "mcp_servers." not in isolated_config
    assert "/host/private" not in isolated_config
    assert not (runtime_home / "models_cache.json").exists()
    assert (runtime_home / "skills").is_dir()
    assert not (runtime_home / "skills").is_symlink()


def test_codex_runtime_home_rejects_nested_skill_symlinks(tmp_path, monkeypatch):
    from app.services.agent_run_harness import _prepare_isolated_codex_home
    from app.services.agent_sandbox import AgentSandboxError

    real_home = tmp_path / "real-codex-home"
    skills = real_home / "skills" / "linked-skill"
    skills.mkdir(parents=True)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("must not be copied", encoding="utf-8")
    (skills / "secret.txt").symlink_to(outside)
    artifact_dir = tmp_path / "agent-run"
    artifact_dir.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(real_home))

    with pytest.raises(AgentSandboxError, match="符号链接"):
        _prepare_isolated_codex_home(
            provider="agent-runtime:default-codex",
            command=["codex", "exec"],
            artifact_dir=artifact_dir,
        )

    assert not any(
        candidate.read_text(encoding="utf-8") == "must not be copied"
        for candidate in artifact_dir.rglob("secret.txt")
        if candidate.is_file() and not candidate.is_symlink()
    )


def test_codex_runtime_home_makes_copied_skills_owner_writable(tmp_path, monkeypatch):
    from app.services.agent_run_harness import _prepare_isolated_codex_home

    real_home = tmp_path / "real-codex-home"
    skill_dir = real_home / "skills" / "readonly-skill"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("readonly source", encoding="utf-8")
    skill_file.chmod(0o444)
    skill_dir.chmod(0o555)
    (real_home / "skills").chmod(0o555)
    artifact_dir = tmp_path / "agent-run"
    artifact_dir.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(real_home))

    runtime_home, _ = _prepare_isolated_codex_home(
        provider="agent-runtime:default-codex",
        command=["codex", "exec"],
        artifact_dir=artifact_dir,
    )

    copied_dir = runtime_home / "skills" / "readonly-skill"
    copied_file = copied_dir / "SKILL.md"
    copied_file.write_text("updated", encoding="utf-8")
    created_file = copied_dir / "created.txt"
    created_file.write_text("created", encoding="utf-8")
    copied_file.unlink()

    assert created_file.read_text(encoding="utf-8") == "created"
    assert not copied_file.exists()


def test_codex_runtime_home_copies_from_open_fd_when_source_is_swapped(
    tmp_path, monkeypatch
):
    import shutil

    from app.services.agent_run_harness import _prepare_isolated_codex_home

    real_home = tmp_path / "real-codex-home"
    skill_dir = real_home / "skills" / "safe-skill"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("safe source", encoding="utf-8")
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("outside secret", encoding="utf-8")
    outside.chmod(0o644)
    artifact_dir = tmp_path / "agent-run"
    artifact_dir.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(real_home))
    original_copyfile = shutil.copyfile

    def swap_source_before_copy(source, target, *args, **kwargs):
        if Path(source) == skill_file:
            skill_file.unlink()
            skill_file.symlink_to(outside)
        return original_copyfile(source, target, *args, **kwargs)

    monkeypatch.setattr(shutil, "copyfile", swap_source_before_copy)

    runtime_home, _ = _prepare_isolated_codex_home(
        provider="agent-runtime:default-codex",
        command=["codex", "exec"],
        artifact_dir=artifact_dir,
    )

    copied = runtime_home / "skills" / "safe-skill" / "SKILL.md"
    assert not copied.is_symlink()
    assert copied.read_text(encoding="utf-8") == "safe source"
    assert outside.read_text(encoding="utf-8") == "outside secret"
    assert outside.stat().st_mode & 0o777 == 0o644


def test_codex_runtime_home_limits_skill_directory_entries(tmp_path, monkeypatch):
    from app.services.agent_run_harness import _prepare_isolated_codex_home
    from app.services.agent_sandbox import AgentSandboxError
    import app.services.agent_sandbox as sandbox_module

    real_home = tmp_path / "real-codex-home"
    skills = real_home / "skills"
    skills.mkdir(parents=True)
    for index in range(3):
        (skills / f"empty-{index}").mkdir()
    artifact_dir = tmp_path / "agent-run"
    artifact_dir.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(real_home))
    monkeypatch.setattr(sandbox_module, "_CODEX_SKILLS_MAX_ENTRIES", 2, raising=False)

    with pytest.raises(AgentSandboxError, match="目录项"):
        _prepare_isolated_codex_home(
            provider="agent-runtime:default-codex",
            command=["codex", "exec"],
            artifact_dir=artifact_dir,
        )


def test_codex_runtime_home_stops_scanning_at_entry_limit(tmp_path, monkeypatch):
    from app.services.agent_run_harness import _prepare_isolated_codex_home
    from app.services.agent_sandbox import AgentSandboxError
    import app.services.agent_sandbox as sandbox_module

    real_home = tmp_path / "real-codex-home"
    skills = real_home / "skills"
    skills.mkdir(parents=True)
    for index in range(10):
        (skills / f"empty-{index}").mkdir()
    artifact_dir = tmp_path / "agent-run"
    artifact_dir.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(real_home))
    monkeypatch.setattr(sandbox_module, "_CODEX_SKILLS_MAX_ENTRIES", 2)
    original_scandir = sandbox_module.os.scandir
    yielded = 0

    class CountingScandir:
        def __init__(self, source):
            self._inner = original_scandir(source)
            self._count_entries = isinstance(source, int)

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *args):
            return self._inner.__exit__(*args)

        def __iter__(self):
            return self

        def __next__(self):
            nonlocal yielded
            entry = next(self._inner)
            if self._count_entries:
                yielded += 1
            return entry

    monkeypatch.setattr(sandbox_module.os, "scandir", CountingScandir)

    with pytest.raises(AgentSandboxError, match="目录项"):
        _prepare_isolated_codex_home(
            provider="agent-runtime:default-codex",
            command=["codex", "exec"],
            artifact_dir=artifact_dir,
        )

    assert yielded == 3


def test_codex_runtime_home_skips_skill_copy_without_secure_descriptor_walk(
    tmp_path, monkeypatch
):
    from app.services.agent_run_harness import _prepare_isolated_codex_home
    import app.services.agent_sandbox as sandbox_module

    real_home = tmp_path / "real-codex-home"
    skill_dir = real_home / "skills" / "unsafe-platform-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("must not be copied", encoding="utf-8")
    artifact_dir = tmp_path / "agent-run"
    artifact_dir.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(real_home))
    monkeypatch.setattr(sandbox_module.os, "supports_dir_fd", set())

    runtime_home, _ = _prepare_isolated_codex_home(
        provider="agent-runtime:default-codex",
        command=["codex", "exec"],
        artifact_dir=artifact_dir,
    )

    assert (runtime_home / "skills").is_dir()
    assert list((runtime_home / "skills").iterdir()) == []


def test_codex_runtime_home_does_not_reuse_workspace_controlled_symlink(
    tmp_path, monkeypatch
):
    from app.services.agent_run_harness import _prepare_isolated_codex_home

    real_home = tmp_path / "real-codex-home"
    real_home.mkdir()
    (real_home / "auth.json").write_text("{}", encoding="utf-8")
    artifact_dir = tmp_path / "agent-run"
    artifact_dir.mkdir()
    host_target = tmp_path / "must-stay-read-only"
    host_target.mkdir()
    planted_home = artifact_dir / ".runtime-codex-home-planted"
    planted_home.mkdir()
    (planted_home / "sessions").symlink_to(host_target, target_is_directory=True)
    monkeypatch.setenv("CODEX_HOME", str(real_home))

    runtime_home, _ = _prepare_isolated_codex_home(
        provider="agent-runtime:default-codex",
        command=["codex", "exec"],
        artifact_dir=artifact_dir,
    )

    assert runtime_home != planted_home.resolve()
    assert not (runtime_home / "sessions").is_symlink()
    assert (runtime_home / "sessions").resolve() != host_target.resolve()


def test_codex_runtime_home_copies_static_inputs_when_symlinks_are_unavailable(
    tmp_path, monkeypatch
):
    from pathlib import Path

    from app.services.agent_run_harness import _prepare_isolated_codex_home

    real_home = tmp_path / "real-codex-home"
    real_home.mkdir()
    (real_home / "auth.json").write_text("auth", encoding="utf-8")
    (real_home / "skills").mkdir()
    (real_home / "skills" / "SKILL.md").write_text("skill", encoding="utf-8")
    artifact_dir = tmp_path / "agent-run"
    artifact_dir.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(real_home))

    def reject_symlink(*_args, **_kwargs):
        raise OSError("symlink privilege unavailable")

    monkeypatch.setattr(Path, "symlink_to", reject_symlink)
    runtime_home, _ = _prepare_isolated_codex_home(
        provider="agent-runtime:default-codex",
        command=["codex", "exec"],
        artifact_dir=artifact_dir,
    )

    assert (runtime_home / "auth.json").read_text(encoding="utf-8") == "auth"
    assert not (runtime_home / "auth.json").is_symlink()
    assert (runtime_home / "skills" / "SKILL.md").read_text(encoding="utf-8") == "skill"


def test_runtime_tmp_directory_is_unique_and_ignores_planted_symlinks(tmp_path):
    from app.services.agent_run_harness import _prepare_isolated_runtime_tmp

    artifact_dir = tmp_path / "agent-run"
    artifact_dir.mkdir()
    host_target = tmp_path / "host-target"
    host_target.mkdir()
    planted = artifact_dir / ".runtime-tmp-planted"
    planted.symlink_to(host_target, target_is_directory=True)

    first = _prepare_isolated_runtime_tmp(artifact_dir)
    second = _prepare_isolated_runtime_tmp(artifact_dir)

    assert first != second
    assert first.parent == artifact_dir.resolve()
    assert second.parent == artifact_dir.resolve()
    assert not first.is_symlink()
    assert not second.is_symlink()
    assert first.resolve() != host_target.resolve()
    assert second.resolve() != host_target.resolve()


def test_agent_run_harness_uses_prompt_file_for_large_argv_payload(
    tmp_path, monkeypatch
):
    from app.config import settings
    from app.services.agent_run_harness import AgentRunHarness

    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {
            "id": "local-large-argv-agent",
            "command": "python",
            "prompt_transport": "argv_last",
        }
    ])
    artifact_dir = tmp_path / "agent-run-large-argv"
    output_file = artifact_dir / "agent_seen.json"
    script = (
        "import json, os, pathlib, sys; "
        "payload=json.loads(pathlib.Path(os.environ['CODETALK_AGENT_PROMPT_FILE']).read_text()); "
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({"
        "'bundle_id': payload['task_bundle']['task_id'], "
        "'argv_count': len(sys.argv), "
        "'blob_len': len(payload['task_bundle']['large_context']), "
        "'bootstrap': sys.argv[-1]"
        "}), encoding='utf-8')"
    )
    harness = AgentRunHarness(artifact_dir)
    run = harness.create_run(
        run_id="agent_run_large_argv",
        provider="local-large-argv-agent",
        command=["python", "-c", script, str(output_file)],
        cwd=str(tmp_path),
        workflow_snapshot={"id": "wf"},
        task_bundle={"task_id": "task-large-argv", "large_context": "x" * 70000},
    )

    executed = harness.execute_run(run.run_id, timeout_sec=10)

    assert executed.status == "completed"
    seen = json.loads(output_file.read_text(encoding="utf-8"))
    assert seen == {
        "bundle_id": "task-large-argv",
        "argv_count": 3,
        "blob_len": 70000,
        "bootstrap": (
            "CodeTalk 的完整用户任务过长，已写入环境变量 CODETALK_AGENT_PROMPT_FILE 指向的 UTF-8 文件。"
            "必须先完整读取该文件，并把文件全部内容作为本轮唯一用户任务执行；不要只回复这条引导。"
        ),
    }
    execution_input = json.loads((artifact_dir / "execution_input.json").read_text(encoding="utf-8"))
    assert execution_input["prompt_transport"] == "argv"
    assert execution_input["prompt_transport_reason"] == "large_payload_prompt_file"
    prompt_file = Path(execution_input["env_hints"]["CODETALK_AGENT_PROMPT_FILE"])
    assert prompt_file.parent.parent == artifact_dir
    assert prompt_file.parent.name.startswith(".runtime-tmp-")
    assert execution_input["command"] == ["python", "-c", script, str(output_file)]
    assert execution_input["process_command"][1:-1] == execution_input["command"][1:]


@pytest.mark.parametrize(
    ("provider", "prompt_transport", "expected_tokens"),
    [
        ("claude-code", "claude_print_arg", {"-p", "--output-format", "stream-json"}),
        ("opencode", "opencode_run_arg", {"run", "--format", "json"}),
    ],
)
def test_agent_run_harness_large_managed_prompt_keeps_cli_mode_and_uses_prompt_file(
    tmp_path,
    monkeypatch,
    provider,
    prompt_transport,
    expected_tokens,
):
    from app.config import settings
    from app.services.agent_run_harness import AgentRunHarness

    run_dir = tmp_path / f"run-{provider}"
    capture = run_dir / f"{provider}-capture.json"
    shim = tmp_path / f"{provider}-shim.py"
    shim.write_text(
        "import json, os, pathlib, sys\n"
        "prompt_file = pathlib.Path(os.environ['CODETALK_AGENT_PROMPT_FILE'])\n"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({\n"
        "  'argv': sys.argv[2:],\n"
        "  'prompt': prompt_file.read_text(encoding='utf-8'),\n"
        "}, ensure_ascii=False), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        settings,
        "external_agent_custom_providers",
        [{"id": provider, "command": sys.executable, "prompt_transport": prompt_transport}],
    )
    large_context = "完整用户输入" * 12000
    harness = AgentRunHarness(run_dir)
    run = harness.create_run(
        run_id=f"large-{provider}",
        provider=provider,
        command=[sys.executable, str(shim), str(capture)],
        cwd=str(tmp_path),
        workflow_snapshot={"id": "wf"},
        task_bundle={"task_id": "task-large-managed", "large_context": large_context},
        prompt_transport=prompt_transport,
    )

    executed = harness.execute_run(run.run_id, timeout_sec=10)

    assert executed.status == "completed"
    seen = json.loads(capture.read_text(encoding="utf-8"))
    assert expected_tokens.issubset(set(seen["argv"]))
    assert "CODETALK_AGENT_PROMPT_FILE" in " ".join(seen["argv"])
    assert large_context in seen["prompt"]
    assert large_context not in " ".join(seen["argv"])
    execution_input = json.loads(
        (harness.artifact_dir / "execution_input.json").read_text(encoding="utf-8")
    )
    assert execution_input["prompt_transport_reason"] == "large_payload_prompt_file"


def test_agent_run_harness_executes_provider_health_fallback_command(
    tmp_path, monkeypatch
):
    from app.config import settings
    from app.services.agent_run_harness import AgentRunHarness

    artifact_dir = tmp_path / "agent-run-fallback"
    output_file = artifact_dir / "agent_seen.json"
    script = (
        "import json, pathlib, sys; "
        "payload=json.load(sys.stdin); "
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({"
        "'bundle_id': payload['task_bundle']['task_id']"
        "}), encoding='utf-8')"
    )
    fallback_command = f'"{sys.executable}" -c "{script}" "{output_file}"'
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {
            "id": "local-fallback-agent",
            "command": "missing-codetalk-agent-command",
            "fallback_commands": [fallback_command],
            "prompt_transport": "stdin",
        }
    ])
    task_bundle = {
        "task_id": "task-fallback",
        "provider_snapshot": {
            "providers": {
                "local-fallback-agent": {
                    "status": "configured",
                    "owner": "agent_cli",
                    "agent_owned": True,
                    "diagnostics": {
                        "configured_command_text": "missing-codetalk-agent-command",
                        "fallback_command_texts": [fallback_command],
                        "prompt_transport": "stdin",
                    },
                }
            }
        },
    }
    harness = AgentRunHarness(artifact_dir)
    run = harness.create_run(
        run_id="agent_run_fallback",
        provider="local-fallback-agent",
        command=["missing-codetalk-agent-command"],
        cwd=str(tmp_path),
        workflow_snapshot={"id": "wf"},
        task_bundle=task_bundle,
    )

    executed = harness.execute_run(run.run_id, timeout_sec=10)

    assert executed.status == "completed"
    seen = json.loads(output_file.read_text(encoding="utf-8"))
    assert seen == {"bundle_id": "task-fallback"}
    execution_input = json.loads((artifact_dir / "execution_input.json").read_text(encoding="utf-8"))
    assert execution_input["command"] == ["missing-codetalk-agent-command"]
    assert execution_input["command_resolution"]["source"] == "provider_health"
    assert execution_input["command_resolution"]["used_fallback"] is True
    assert execution_input["command_resolution"]["health_attempt_count"] == 2
    assert (
        execution_input["command_resolution"]["active_attempt_resolution"]["method"]
        == "configured_path"
    )
    assert execution_input["command_resolution"]["active_attempt_resolution"]["command"] == (
        fallback_command
    )
    assert execution_input["launch_command"][1:] == ["-c", script, str(output_file)]
