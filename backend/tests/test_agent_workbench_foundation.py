import hashlib
import json
import sys

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
    assert execution_input["env_hints"] == {
        "CODETALK_AGENT_READONLY": "1",
        "CODETALK_REPO_PATH": str(tmp_path),
        "CODETALK_AGENT_ARTIFACT_DIR": str(artifact_dir),
    }
    assert execution_input["stdin_json_sha256"]
    events = (artifact_dir / "runtime_events.jsonl").read_text(encoding="utf-8")
    assert "agent_execution_input_prepared" in events
    assert "agent_run_started" in events
    assert "agent_run_completed" in events


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
    assert execution_input["launch_command"][1:] == ["-c", script, str(output_file)]
