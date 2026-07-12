import hashlib
import json
import sys
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


def test_agent_run_harness_executes_cli_with_task_bundle_and_audit_events(tmp_path):
    from app.services.agent_run_harness import AgentRunHarness

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
    assert execution_input["env_hints"] == {
        "CODETALK_AGENT_READONLY": "1",
        "CODETALK_REPO_PATH": str(tmp_path),
        "CODETALK_AGENT_ARTIFACT_DIR": str(artifact_dir),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_KEY_0": "core.excludesFile",
        "GIT_CONFIG_VALUE_0": "/dev/null",
        "TEMP": str(runtime_tmp_dir),
        "TMP": str(runtime_tmp_dir),
        "TMPDIR": str(runtime_tmp_dir),
    }
    assert execution_input["stdin_json_sha256"]
    events = (artifact_dir / "runtime_events.jsonl").read_text(encoding="utf-8")
    assert "agent_execution_input_prepared" in events
    assert "agent_run_started" in events
    assert "agent_run_completed" in events


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


def test_codex_runtime_home_links_static_inputs_and_keeps_state_in_artifacts(
    tmp_path, monkeypatch
):
    from app.services.agent_run_harness import _prepare_isolated_codex_home

    real_home = tmp_path / "real-codex-home"
    real_home.mkdir()
    for name in ("auth.json", "config.toml", "models_cache.json"):
        (real_home / name).write_text(name, encoding="utf-8")
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
    assert set(read_targets) == {
        (real_home / "auth.json").resolve(),
        (real_home / "config.toml").resolve(),
        (real_home / "models_cache.json").resolve(),
        (real_home / "skills").resolve(),
    }
    assert (runtime_home / "auth.json").is_symlink()
    assert (runtime_home / "config.toml").is_symlink()
    assert (runtime_home / "models_cache.json").is_symlink()
    assert (runtime_home / "skills").is_symlink()


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
