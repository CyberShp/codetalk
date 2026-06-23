import json
import hashlib
from pathlib import Path


def test_prepare_workbench_task_run_freezes_workflow_and_creates_agent_run(tmp_path):
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer

    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "mr_test_design",
        "name": "MR test design",
        "version": 2,
        "inputs": [{"id": "mr_link", "type": "external_link", "resolver": "agent_mcp"}],
        "steps": [
            {
                "id": "collect_mr",
                "type": "agent_task",
                "goal": "mr_context_collect",
                "provider": "claude-code",
                "mcp_profile": "codehub-readonly",
                "required_artifacts": ["mr_snapshot.json", "diff.patch", "changed_files.json"],
            },
            {"id": "render", "type": "report_render"},
        ],
        "outputs": [{"id": "report", "type": "markdown"}],
    })

    result = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="mr_test_design",
        workspace_id="ws1",
        repo_path="E:/repo",
        inputs={"mr_link": "https://codehub.local/project/merge_requests/1"},
        provider_override=None,
    )

    assert result.workflow_snapshot["version"] == 2
    assert result.task_bundle["inputs"]["mr_link"] == "https://codehub.local/project/merge_requests/1"
    assert result.agent_runs[0]["step_id"] == "collect_mr"
    assert result.agent_runs[0]["mcp_profile"] == "codehub-readonly"

    root = Path(result.artifact_dir)
    assert (root / "task_run.json").exists()
    assert (root / "workflow_snapshot.json").exists()
    assert (root / "input_snapshot.json").exists()
    bundle = json.loads((root / "task_bundle.json").read_text(encoding="utf-8"))
    assert bundle["required_artifacts_by_step"]["collect_mr"] == [
        "mr_snapshot.json",
        "diff.patch",
        "changed_files.json",
    ]
    assert (root / "agent_runs" / "collect_mr" / "agent_run.json").exists()


def test_prepare_workbench_task_run_ingests_file_inputs(tmp_path):
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer

    patch_plan = tmp_path / "patch-plan.md"
    patch_plan.write_text("# Patch plan\n\nChange TLS handshake timeout.\n", encoding="utf-8")
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "patch_impact_review",
        "name": "Patch impact",
        "version": 1,
        "inputs": [{"id": "patch_plan", "type": "file", "required": True}],
        "steps": [{"id": "analyze", "type": "agent_task", "goal": "patch_impact_review"}],
        "outputs": [{"id": "report", "type": "markdown"}],
    })

    result = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="patch_impact_review",
        workspace_id="ws1",
        repo_path="E:/repo",
        inputs={"patch_plan": {"path": str(patch_plan)}},
        provider_override="claude-code",
    )

    file_info = result.input_snapshot["patch_plan"]
    assert file_info["kind"] == "file"
    assert file_info["sha256"] == hashlib.sha256(patch_plan.read_bytes()).hexdigest()
    assert Path(file_info["copied_path"]).exists()
    assert Path(file_info["parsed_text_path"]).read_text(encoding="utf-8").startswith("# Patch plan")
    assert Path(file_info["chunks_path"]).exists()
    input_context = result.task_bundle["input_context"]
    assert input_context["inputs"][0]["input_id"] == "patch_plan"
    assert input_context["inputs"][0]["kind"] == "file"
    assert input_context["inputs"][0]["filename"] == "patch-plan.md"
    assert input_context["inputs"][0]["text_preview"].startswith("# Patch plan")
    assert input_context["inputs"][0]["chunk_count"] == 1
    assert input_context["inputs"][0]["chunks_path"] == file_info["chunks_path"]
    step_bundle = json.loads(
        Path(result.artifact_dir, "agent_runs", "analyze", "task_bundle.json").read_text(
            encoding="utf-8"
        )
    )
    assert step_bundle["input_context"]["inputs"][0]["input_id"] == "patch_plan"
    assert Path(result.artifact_dir, "input_context.json").exists()


def test_prepare_workbench_task_run_validates_required_inputs(tmp_path):
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer

    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "required_input_workflow",
        "name": "Required input workflow",
        "version": 1,
        "inputs": [{"id": "target_scope", "type": "free_text", "required": True}],
        "steps": [{"id": "render", "type": "report_render"}],
        "outputs": [{"id": "report", "type": "markdown"}],
    })

    try:
        WorkbenchTaskRunPreparer(
            artifact_root=tmp_path / "task_runs",
            workflow_store=workflow_store,
        ).prepare(
            workflow_id="required_input_workflow",
            workspace_id="ws1",
            repo_path=str(tmp_path),
            inputs={},
        )
    except ValueError as exc:
        assert "required input target_scope is missing" in str(exc)
    else:
        raise AssertionError("missing required input should fail task preparation")


def test_prepare_workbench_task_run_ingests_file_set_inputs(tmp_path):
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer

    req = tmp_path / "requirements.md"
    design = tmp_path / "design.md"
    req.write_text("# Requirements\n\nTLS must fail closed.\n", encoding="utf-8")
    design.write_text("# Design\n\nHandshake cleanup path.\n", encoding="utf-8")
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "file_set_workflow",
        "name": "File set workflow",
        "version": 1,
        "inputs": [{"id": "docs", "type": "file_set", "required": True}],
        "steps": [{"id": "render", "type": "report_render"}],
        "outputs": [{"id": "report", "type": "markdown"}],
    })

    result = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="file_set_workflow",
        workspace_id="ws1",
        repo_path=str(tmp_path),
        inputs={"docs": [{"path": str(req)}, {"path": str(design)}]},
    )

    docs = result.input_snapshot["docs"]
    assert docs["kind"] == "file_set"
    assert docs["count"] == 2
    assert [item["filename"] for item in docs["files"]] == [
        "requirements.md",
        "design.md",
    ]
    assert Path(docs["manifest_path"]).exists()
    assert "TLS must fail closed" in Path(docs["files"][0]["parsed_text_path"]).read_text(
        encoding="utf-8"
    )
    input_context = result.task_bundle["input_context"]
    assert input_context["inputs"][0]["input_id"] == "docs"
    assert input_context["inputs"][0]["kind"] == "file_set"
    assert input_context["inputs"][0]["count"] == 2
    assert input_context["inputs"][0]["files"][0]["filename"] == "requirements.md"
    assert "TLS must fail closed" in input_context["inputs"][0]["files"][0]["text_preview"]


def test_prepare_workbench_task_run_injects_evidence_and_semantic_context(tmp_path):
    from app.services.evidence_memory import EvidenceMemoryStore
    from app.services.test_semantic_library import TestSemanticLibraryStore
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer

    memory = EvidenceMemoryStore(tmp_path / "memory.db")
    memory.record_analysis_run(
        run_id="run-prev",
        workspace_id="ws1",
        repo_path="E:/repo",
        object_text="nvme tcp tls",
        workflow_id="module_analysis",
        status="completed",
    )
    evidence_id = memory.upsert_evidence_item(
        run_id="run-prev",
        workspace_id="ws1",
        kind="changed_file",
        subject_key="nof/nvmf_tcp/transport/tls/tls.c",
        status="agent_mcp_verified",
        source="claude-code",
        path="nof/nvmf_tcp/transport/tls/tls.c",
        reason="validated TLS source",
        text="nvme tcp tls handshake cleanup",
    )
    memory.add_source_slice(
        evidence_id=evidence_id,
        file_path="nof/nvmf_tcp/transport/tls/tls.c",
        start_line=10,
        end_line=18,
        sha256="abc123",
        excerpt="int nvmf_tcp_tls_handshake(void) { return -EINVAL; }",
    )
    semantics = TestSemanticLibraryStore(tmp_path / "semantics.db")
    semantics.upsert_case({
        "case_id": "TC_TLS_HANDSHAKE_FAIL",
        "feature": "NVMe TCP TLS",
        "module": "nvmf_tcp",
        "scenario": "TLS handshake fails and connection is released",
        "terms": ["TLS negotiation", "connection release"],
        "tags": ["black_box", "resource_cleanup"],
        "test_level": "black_box",
    })
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "mr_blackbox_test",
        "name": "MR black-box",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [{"id": "design", "type": "agent_task", "goal": "black-box test design"}],
        "outputs": [{"id": "cases", "type": "markdown"}],
    })

    result = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
        evidence_memory=memory,
        semantic_library=semantics,
    ).prepare(
        workflow_id="mr_blackbox_test",
        workspace_id="ws1",
        repo_path="E:/repo",
        inputs={"module": "nvme tcp tls"},
    )

    context_bundle = result.task_bundle["context_bundle"]
    assert context_bundle["query"] == "nvme tcp tls"
    assert context_bundle["evidence"][0]["subject_key"] == "nof/nvmf_tcp/transport/tls/tls.c"
    assert context_bundle["evidence"][0]["source_read_status"] == "source_slices_attached"
    assert context_bundle["evidence"][0]["usable_as_source_evidence"] is True
    assert context_bundle["evidence"][0]["source_slices"][0]["file_path"] == (
        "nof/nvmf_tcp/transport/tls/tls.c"
    )
    assert context_bundle["evidence"][0]["source_slices"][0]["start_line"] == 10
    assert "nvmf_tcp_tls_handshake" in context_bundle["evidence"][0]["source_slices"][0]["excerpt"]
    assert context_bundle["semantic_cases"][0]["case_id"] == "TC_TLS_HANDSHAKE_FAIL"
    assert Path(result.artifact_dir, "context_bundle.json").exists()
    step_bundle = json.loads(
        Path(result.artifact_dir, "agent_runs", "design", "task_bundle.json").read_text(encoding="utf-8")
    )
    assert step_bundle["context_bundle"]["semantic_cases"][0]["terms"] == [
        "TLS negotiation",
        "connection release",
    ]
    assert step_bundle["context_bundle"]["evidence"][0]["source_slices"][0]["sha256"] == "abc123"
    memory_retrieval = json.loads(
        Path(result.artifact_dir, "memory_retrieval.json").read_text(encoding="utf-8")
    )
    assert memory_retrieval["provider"] == "evidence-memory"
    assert memory_retrieval["retrieved_count"] == 1
    assert memory_retrieval["items"][0]["source_slice_count"] == 1
    assert memory_retrieval["items"][0]["reuse_reason"] == (
        "query matched prior evidence; source slices are attached and may be used as source evidence"
    )
    assert memory_retrieval["items"][0]["source_slice_refs"] == [
        {
            "slice_id": memory_retrieval["items"][0]["source_slice_refs"][0]["slice_id"],
            "file_path": "nof/nvmf_tcp/transport/tls/tls.c",
            "start_line": 10,
            "end_line": 18,
            "sha256": "abc123",
        }
    ]
    source_read_chain = json.loads(
        Path(result.artifact_dir, "source_read_chain.json").read_text(encoding="utf-8")
    )
    assert source_read_chain["reads"][0]["file_path"] == "nof/nvmf_tcp/transport/tls/tls.c"
    assert source_read_chain["reads"][0]["sha256"] == "abc123"
    trajectory = json.loads(
        Path(result.artifact_dir, "evidence_consumption_trajectory.json").read_text(encoding="utf-8")
    )
    assert trajectory["scoring_policy"] == "navigation_only_not_authority"
    assert trajectory["events"][0]["reuse_reason"] == (
        "query matched prior evidence; source slices are attached and may be used as source evidence"
    )
    semantic_event = next(
        item for item in trajectory["events"]
        if item["event"] == "semantic_case_retrieved"
    )
    assert semantic_event["reuse_reason"] == (
        "query matched semantic library case; use terms to align black-box wording"
    )
    assert [event["event"] for event in trajectory["events"]] == [
        "memory_retrieved",
        "source_slice_attached",
        "semantic_case_retrieved",
    ]


def test_prepare_workbench_task_run_records_degraded_retrieval_artifact(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer

    monkeypatch.setattr(settings, "context_discovery_enabled", True)
    monkeypatch.setattr(settings, "fast_context_enabled", True)
    monkeypatch.setattr(settings, "fast_context_backend_bridge_enabled", False)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text(
        "Prefer mcp__fast-context__fast_context_search before local grep.\n",
        encoding="utf-8",
    )
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "degraded_context_workflow",
        "name": "Degraded context workflow",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [{"id": "discover", "type": "agent_task"}],
        "outputs": [{"id": "report", "type": "markdown"}],
    })

    result = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="degraded_context_workflow",
        workspace_id="ws-degraded",
        repo_path=str(repo),
        inputs={"module": "nvme tcp tls"},
    )

    degraded = json.loads(
        Path(result.artifact_dir, "degraded_retrieval.json").read_text(encoding="utf-8")
    )
    reasons = {item["provider"]: item["reason"] for item in degraded["degraded"]}
    assert reasons["fast-context"] == "backend_mcp_bridge_unavailable"
    assert reasons["evidence-memory"] == "store_not_configured"
    assert reasons["semantic-library"] == "store_not_configured"
    step_bundle = json.loads(
        Path(result.artifact_dir, "agent_runs", "discover", "task_bundle.json").read_text(encoding="utf-8")
    )
    assert step_bundle["degraded_retrieval"]["degraded"][0]["provider"] == "fast-context"


def test_prepare_workbench_task_run_embeds_repo_agent_instructions(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer

    monkeypatch.setattr(settings, "context_discovery_enabled", True)
    monkeypatch.setattr(settings, "fast_context_enabled", True)
    monkeypatch.setattr(settings, "fast_context_backend_bridge_enabled", False)
    repo = tmp_path / "repo"
    target_dir = repo / "lib" / "thread"
    target_dir.mkdir(parents=True)
    (repo / "AGENTS.md").write_text(
        "# Repo instructions\n\nPrefer fast-context before grep.\n",
        encoding="utf-8",
    )
    (target_dir / "AGENTS.md").write_text(
        "# Thread instructions\n\nUse GitNexus process context.\n",
        encoding="utf-8",
    )
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "module_review",
        "name": "Module review",
        "version": 1,
        "inputs": [{"id": "module_path", "type": "free_text"}],
        "steps": [{"id": "discover", "type": "agent_task"}],
        "outputs": [{"id": "report", "type": "markdown"}],
    })

    result = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="module_review",
        workspace_id="ws1",
        repo_path=str(repo),
        inputs={"module_path": "lib/thread/thread.c"},
    )

    instructions = result.task_bundle["agent_instructions"]
    assert [item["relative_path"] for item in instructions["files"]] == [
        "AGENTS.md",
        "lib/thread/AGENTS.md",
    ]
    assert instructions["files"][0]["sha256"] == hashlib.sha256(
        (repo / "AGENTS.md").read_bytes()
    ).hexdigest()
    assert "fast-context" in instructions["files"][0]["content"]
    root_payload = json.loads(
        Path(result.artifact_dir, "agent_instructions.json").read_text(encoding="utf-8")
    )
    assert root_payload["files"][1]["relative_path"] == "lib/thread/AGENTS.md"
    step_bundle = json.loads(
        Path(result.artifact_dir, "agent_runs", "discover", "task_bundle.json").read_text(encoding="utf-8")
    )
    assert step_bundle["agent_instructions"]["files"][0]["relative_path"] == "AGENTS.md"
    decision = result.task_bundle["context_discovery_decision"]["fast-context"]
    assert decision["requested_by_agent_instructions"] is True
    assert decision["codetalk_callable"] is bool(
        settings.context_discovery_enabled
        and settings.fast_context_enabled
        and settings.fast_context_backend_bridge_enabled
    )
    assert decision["fallback_path"] == [
        "local_search",
        "gitnexus",
        "cgc",
        "agent_cli",
    ]
    assert "bridge" in " ".join(decision["warnings"]).lower()
    persisted_decision = json.loads(
        Path(result.artifact_dir, "context_discovery_decision.json").read_text(encoding="utf-8")
    )
    assert persisted_decision["fast-context"]["requested_by_files"] == ["AGENTS.md"]
    assert (
        step_bundle["context_discovery_decision"]["fast-context"]["codetalk_callable"]
        is decision["codetalk_callable"]
    )


def test_prepare_workbench_task_run_embeds_agent_provider_snapshot(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer

    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {
            "id": "corp-agent",
            "command": "corp-agent run --json",
            "fallback_commands": ["corp-agent --legacy"],
            "supports_mcp": True,
            "mcp_profiles": ["codehub-readonly"],
            "supports_artifact_export": True,
            "supports_json_output": True,
        }
    ])
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "provider_snapshot_workflow",
        "name": "Provider snapshot workflow",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [
            {"id": "known", "type": "agent_task", "provider": "corp-agent"},
            {"id": "unknown", "type": "agent_task", "provider": "missing-agent"},
        ],
        "outputs": [{"id": "report", "type": "markdown"}],
    })

    result = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="provider_snapshot_workflow",
        workspace_id="ws1",
        repo_path=str(tmp_path),
        inputs={"module": "nvme-tcp-tls"},
    )

    snapshot = result.task_bundle["provider_snapshot"]
    known = snapshot["providers"]["corp-agent"]
    assert known["status"] == "configured"
    assert known["command"] == ["corp-agent", "run", "--json"]
    assert known["fallback_commands"] == [["corp-agent", "--legacy"]]
    assert known["capabilities"]["supports_mcp"] is True
    assert known["agent_owned"] is True
    assert known["codetalk_callable"] is False
    assert known["diagnostics"]["health_endpoint"] == "/api/tools/corp-agent/health"
    assert known["diagnostics"]["startup_probe_endpoint"] == "/api/tools/corp-agent/startup-probe"
    assert known["diagnostics"]["configured_command_text"] == "corp-agent run --json"
    assert known["diagnostics"]["fallback_command_texts"] == ["corp-agent --legacy"]
    assert known["diagnostics"]["mcp_credentials_owner"] == "agent_cli"
    assert snapshot["steps"]["known"]["provider"] == "corp-agent"
    assert snapshot["providers"]["missing-agent"]["status"] == "unknown_provider"
    assert snapshot["providers"]["missing-agent"]["diagnostics"]["manual_probe_command"]
    assert snapshot["codetalk_providers"]["local-search"]["codetalk_callable"] is True
    assert snapshot["codetalk_providers"]["local-search"]["capabilities"]["supports_source_slices"] is True
    assert snapshot["codetalk_providers"]["gitnexus"]["owner"] == "codetalk_index"
    assert snapshot["codetalk_providers"]["gitnexus"]["diagnostics"]["startup_probe_endpoint"] == (
        "/api/tools/gitnexus/startup-probe"
    )
    assert snapshot["codetalk_providers"]["cgc"]["capabilities"]["supports_call_graph"] is True
    assert snapshot["codetalk_providers"]["evidence-memory"]["owner"] == "codetalk_memory"
    assert snapshot["codetalk_providers"]["semantic-library"]["capabilities"]["supports_black_box_terms"] is True
    assert "missing-agent" in snapshot["warnings"][0]
    persisted = json.loads(
        Path(result.artifact_dir, "provider_snapshot.json").read_text(encoding="utf-8")
    )
    assert persisted["steps"]["unknown"]["provider"] == "missing-agent"
    assert persisted["codetalk_providers"]["local-search"]["status"] == "available"
    step_bundle = json.loads(
        Path(result.artifact_dir, "agent_runs", "known", "task_bundle.json").read_text(encoding="utf-8")
    )
    assert step_bundle["provider_snapshot"]["providers"]["corp-agent"]["status"] == "configured"
    assert step_bundle["provider_snapshot"]["providers"]["corp-agent"]["diagnostics"]["manual_probe_command"]
    assert step_bundle["provider_snapshot"]["codetalk_providers"]["gitnexus"]["owner"] == "codetalk_index"


def test_agent_execution_persists_provider_diagnostics_snapshot(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner

    script_path = tmp_path / "agent_echo_diagnostics.py"
    script_path.write_text(
        "import json, os, pathlib, sys\n"
        "payload=json.loads(sys.stdin.read())\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "(root/'result.json').write_text(json.dumps(payload['provider_diagnostics']), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {
            "id": "corp-agent",
            "command": f"python {script_path}",
            "fallback_commands": ["corp-agent --legacy"],
            "prompt_transport": "stdin",
            "supports_mcp": True,
            "mcp_profiles": ["codehub-readonly"],
        }
    ])

    def fake_health(provider, command, fallback_commands=None):
        return {
            "provider": provider,
            "status": "available",
            "configured_command": command,
            "command": command,
            "argv": ["python", str(script_path)],
            "path": str(script_path),
            "launch_kind": "exec",
            "used_fallback": False,
            "attempts": [
                {
                    "command": command,
                    "status": "available",
                    "launch_kind": "exec",
                    "path": str(script_path),
                }
            ],
        }

    monkeypatch.setattr(
        "app.services.external_agent_discovery.check_provider_health",
        fake_health,
    )
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "provider_diagnostics_execution",
        "name": "Provider diagnostics execution",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [
            {
                "id": "discover",
                "type": "agent_task",
                "provider": "corp-agent",
                "required_artifacts": ["result.json"],
            }
        ],
        "outputs": [{"id": "result", "type": "json", "artifact": "result.json"}],
    })
    artifact_root = tmp_path / "task_runs"
    prepared = WorkbenchTaskRunPreparer(
        artifact_root=artifact_root,
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="provider_diagnostics_execution",
        workspace_id="ws1",
        repo_path=str(tmp_path),
        inputs={"module": "nvme-tcp-tls"},
    )

    executed = WorkbenchWorkflowRunner(artifact_root).execute_task_run(
        prepared.task_run_id,
        timeout_sec=10,
    )

    assert executed.status == "completed"
    artifact_dir = Path(prepared.artifact_dir, "agent_runs", "discover")
    provider_diagnostics = json.loads(
        (artifact_dir / "provider_diagnostics.json").read_text(encoding="utf-8")
    )
    assert provider_diagnostics["provider"] == "corp-agent"
    assert provider_diagnostics["diagnostics"]["startup_probe_endpoint"] == (
        "/api/tools/corp-agent/startup-probe"
    )
    assert provider_diagnostics["diagnostics"]["mcp_credentials_owner"] == "agent_cli"
    assert provider_diagnostics["health"]["status"] == "available"
    assert provider_diagnostics["health"]["configured_command"].startswith("python ")
    assert provider_diagnostics["health"]["attempts"][0]["status"] == "available"
    step_result = executed.step_results[0]
    assert step_result["provider_diagnostics"]["provider"] == "corp-agent"
    assert step_result["provider_diagnostics"]["health_status"] == "available"
    assert step_result["provider_diagnostics"]["startup_probe_endpoint"] == (
        "/api/tools/corp-agent/startup-probe"
    )
    assert step_result["provider_diagnostics"]["artifact"] == "provider_diagnostics.json"
    execution_input = json.loads(
        (artifact_dir / "execution_input.json").read_text(encoding="utf-8")
    )
    assert execution_input["provider_diagnostics"]["provider"] == "corp-agent"
    assert execution_input["provider_diagnostics"]["health"]["launch_kind"] == "exec"
    agent_seen = json.loads((artifact_dir / "result.json").read_text(encoding="utf-8"))
    assert agent_seen["diagnostics"]["startup_probe_transport"] == "stdin"
    assert agent_seen["health"]["status"] == "available"
    turn_snapshot = json.loads(
        (artifact_dir / "turns" / "turn_1" / "provider_diagnostics.json").read_text(
            encoding="utf-8"
        )
    )
    assert turn_snapshot["diagnostics"]["configured_command_text"].startswith("python ")


def test_agent_execution_provider_health_snapshot_redacts_secrets(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner

    script_path = tmp_path / "agent_write_result.py"
    script_path.write_text(
        "import json, os, pathlib, sys\n"
        "json.loads(sys.stdin.read())\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "(root/'result.json').write_text('{\"ok\": true}', encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "secret-agent", "command": f"python {script_path}"}
    ])

    def fake_health(provider, command, fallback_commands=None):
        return {
            "provider": provider,
            "status": "unavailable",
            "reason": "spawn failed token=super-secret-token",
            "attempts": [
                {
                    "command": command,
                    "status": "unavailable",
                    "config_hint": "api_key=sk-test-secret",
                }
            ],
        }

    monkeypatch.setattr(
        "app.services.external_agent_discovery.check_provider_health",
        fake_health,
    )
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "provider_health_redaction",
        "name": "Provider health redaction",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [
            {
                "id": "discover",
                "type": "agent_task",
                "provider": "secret-agent",
                "required_artifacts": ["result.json"],
            }
        ],
        "outputs": [{"id": "result", "type": "json", "artifact": "result.json"}],
    })
    artifact_root = tmp_path / "task_runs"
    prepared = WorkbenchTaskRunPreparer(
        artifact_root=artifact_root,
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="provider_health_redaction",
        workspace_id="ws1",
        repo_path=str(tmp_path),
        inputs={"module": "nvme-tcp-tls"},
    )

    executed = WorkbenchWorkflowRunner(artifact_root).execute_task_run(
        prepared.task_run_id,
        timeout_sec=10,
    )

    assert executed.status == "completed"
    text = Path(
        prepared.artifact_dir,
        "agent_runs",
        "discover",
        "provider_diagnostics.json",
    ).read_text(encoding="utf-8")
    assert "super-secret-token" not in text
    assert "sk-test-secret" not in text
    assert "<redacted>" in text


def test_workbench_task_run_store_loads_and_lists_prepared_runs(tmp_path):
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workbench_task_run import (
        WorkbenchTaskRunPreparer,
        WorkbenchTaskRunStore,
    )

    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "module_review",
        "name": "Module review",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [{"id": "discover", "type": "agent_task"}],
        "outputs": [{"id": "report", "type": "markdown"}],
    })
    root = tmp_path / "task_runs"
    first = WorkbenchTaskRunPreparer(
        artifact_root=root,
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="module_review",
        workspace_id="ws1",
        repo_path="E:/repo",
        inputs={"module": "nvme-tcp-tls"},
    )
    second = WorkbenchTaskRunPreparer(
        artifact_root=root,
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="module_review",
        workspace_id="ws2",
        repo_path="E:/repo",
        inputs={"module": "bdev"},
    )

    store = WorkbenchTaskRunStore(root)

    assert store.load(first.task_run_id).task_run_id == first.task_run_id
    assert [item.task_run_id for item in store.list(limit=10)] == [
        second.task_run_id,
        first.task_run_id,
    ]
    assert [item.task_run_id for item in store.list(workspace_id="ws1")] == [
        first.task_run_id,
    ]


def test_workbench_workflow_runner_executes_agent_steps_and_validates_artifacts(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
    from app.services.workflow_dsl import WorkflowStore

    script_path = tmp_path / "agent_collect_mr.py"
    script_path.write_text(
        "import hashlib, json, os, pathlib\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "diff='diff --git a/src/tls.c b/src/tls.c\\n--- a/src/tls.c\\n+++ b/src/tls.c\\n'\n"
        "sha=hashlib.sha256(diff.encode()).hexdigest()\n"
        "(root/'diff.patch').write_text(diff, encoding='utf-8')\n"
        "(root/'changed_files.json').write_text(json.dumps([{'path':'src/tls.c','status':'modified'}]), encoding='utf-8')\n"
        "(root/'report.md').write_text('# TLS report\\n\\nready', encoding='utf-8')\n"
        "(root/'mr_snapshot.json').write_text(json.dumps({"
        "'source':'agent_mcp','mcp_profile':'codehub-readonly','mr_url':'https://codehub.local/p/merge_requests/1',"
        "'project':'p','mr_id':'1','title':'TLS','source_branch':'feature','target_branch':'main',"
        "'base_commit':'base','head_commit':'head','diff_sha256':sha,'changed_files_count':1"
        "}), encoding='utf-8')\n"
        "print('ok token=secret-value')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "mr_test_design",
        "name": "MR test design",
        "version": 1,
        "inputs": [{"id": "mr_link", "type": "mr_link", "resolver": "agent_mcp"}],
        "steps": [
            {
                "id": "collect_mr",
                "type": "agent_task",
                "provider": "local-python",
                "mcp_profile": "codehub-readonly",
                "required_artifacts": ["mr_snapshot.json", "diff.patch", "changed_files.json"],
            },
            {"id": "render", "type": "report_render"},
        ],
        "outputs": [{"id": "report", "type": "markdown", "from": "collect_mr", "artifact": "report.md"}],
    })
    task_run = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="mr_test_design",
        workspace_id="ws-runner",
        repo_path=str(tmp_path),
        inputs={"mr_link": "https://codehub.local/p/merge_requests/1"},
    )

    result = WorkbenchWorkflowRunner(tmp_path / "task_runs").execute_task_run(
        task_run.task_run_id,
        timeout_sec=10,
    )

    assert result.status == "completed"
    assert result.task_run_id == task_run.task_run_id
    assert result.step_results[0]["step_id"] == "collect_mr"
    assert result.step_results[0]["execution"]["status"] == "completed"
    assert result.step_results[0]["validation"]["status"] == "ok"
    accepted_details = result.step_results[0]["validation"]["accepted_artifact_details"]
    assert {item["artifact"] for item in accepted_details} == {
        "mr_snapshot.json",
        "diff.patch",
        "changed_files.json",
    }
    assert all(item["sha256"] and item["size_bytes"] > 0 for item in accepted_details)
    assert all(Path(item["path"]).is_file() for item in accepted_details)
    assert result.outputs[0]["id"] == "report"
    assert result.outputs[0]["status"] == "ok"
    assert result.outputs[0]["from"] == "collect_mr"
    assert result.outputs[0]["artifact"] == "report.md"
    assert result.outputs[0]["sha256"] == hashlib.sha256(
        Path(result.outputs[0]["path"]).read_bytes()
    ).hexdigest()
    root = Path(task_run.artifact_dir)
    assert (root / "workflow_execution.json").exists()
    workflow_outputs = json.loads((root / "workflow_outputs.json").read_text(encoding="utf-8"))
    assert workflow_outputs["outputs"][0]["id"] == "report"
    assert "secret-value" not in (
        root / "agent_runs" / "collect_mr" / "raw_output.txt"
    ).read_text(encoding="utf-8")


def test_workbench_workflow_runner_rejects_missing_required_agent_artifact(
    tmp_path,
    monkeypatch,
):
    from app.config import settings
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
    from app.services.workflow_dsl import WorkflowStore

    script_path = tmp_path / "agent_missing_artifact.py"
    script_path.write_text(
        "import os, pathlib\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "(root/'source_scope.json').write_text('{\"files\":[]}', encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "missing_artifact_workflow",
        "name": "Missing artifact workflow",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [
            {
                "id": "discover",
                "type": "agent_task",
                "provider": "local-python",
                "required_artifacts": ["source_scope.json", "evidence_cards.json"],
            },
            {"id": "render", "type": "report_render"},
        ],
        "outputs": [{"id": "report", "type": "markdown", "from": "render"}],
    })
    task_run = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="missing_artifact_workflow",
        workspace_id="ws-missing-artifact",
        repo_path=str(tmp_path),
        inputs={"module": "nvme tcp tls"},
    )

    result = WorkbenchWorkflowRunner(tmp_path / "task_runs").execute_task_run(
        task_run.task_run_id,
        timeout_sec=10,
    )

    assert result.status == "invalid"
    assert result.step_results[0]["status"] == "invalid"
    validation = result.step_results[0]["validation"]
    assert validation["accepted_artifact_details"][0]["artifact"] == "source_scope.json"
    rejected = validation["rejected_artifact_details"]
    assert rejected == [
        {
            "artifact": "evidence_cards.json",
            "reason": "missing_required_artifact",
            "path": str(
                Path(task_run.artifact_dir)
                / "agent_runs"
                / "discover"
                / "evidence_cards.json"
            ),
        }
    ]


def test_workbench_workflow_runner_records_agent_failure_recovery(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
    from app.services.workflow_dsl import WorkflowStore

    script_path = tmp_path / "agent_fail.py"
    script_path.write_text(
        "import sys\n"
        "print('partial stdout before failure')\n"
        "print('fatal diagnostic', file=sys.stderr)\n"
        "sys.exit(7)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "agent_failure_recovery",
        "name": "Agent failure recovery",
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
    })
    task_run = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="agent_failure_recovery",
        workspace_id="ws-failure",
        repo_path=str(tmp_path),
        inputs={"module": "nvme tcp tls"},
    )

    result = WorkbenchWorkflowRunner(tmp_path / "task_runs").execute_task_run(
        task_run.task_run_id,
        timeout_sec=10,
    )

    step = result.step_results[0]
    assert step["status"] == "invalid"
    assert step["execution"]["status"] == "error"
    assert step["execution"]["exit_code"] == 7
    assert step["failure_recovery"] == {
        "failure_kind": "agent_error",
        "retryable": True,
        "raw_output_artifact": "raw_output.txt",
        "execution_result_artifact": "execution_result.json",
        "validation_status": "invalid",
        "missing_artifacts": ["source_scope.json"],
        "suggested_actions": [
            "inspect raw_output.txt and execution_result.json",
            "rerun the step after fixing provider command, MCP credentials, or agent prompt",
            "do not materialize outputs until required artifacts validate",
        ],
    }


def test_workbench_workflow_runner_enforces_user_output_schema(
    tmp_path,
    monkeypatch,
):
    from app.config import settings
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
    from app.services.workflow_dsl import WorkflowStore

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
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "schema_enforced_workflow",
        "name": "Schema enforced workflow",
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
    })
    task_run = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="schema_enforced_workflow",
        workspace_id="ws-schema",
        repo_path=str(tmp_path),
        inputs={"module": "nvme tcp tls"},
    )

    result = WorkbenchWorkflowRunner(tmp_path / "task_runs").execute_task_run(
        task_run.task_run_id,
        timeout_sec=10,
    )

    assert result.status == "invalid"
    assert result.outputs[0]["status"] == "invalid"
    assert result.outputs[0]["reason"] == "schema_validation_failed"
    assert "missing required field: files" in result.outputs[0]["schema_errors"]


def test_prepare_workbench_task_run_includes_output_schemas_in_agent_bundle(
    tmp_path,
):
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer
    from app.services.workflow_dsl import WorkflowStore

    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "schema_bundle_workflow",
        "name": "Schema bundle workflow",
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
    })

    result = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="schema_bundle_workflow",
        workspace_id="ws-schema-bundle",
        repo_path=str(tmp_path),
        inputs={"module": "nvme tcp tls"},
    )

    assert result.task_bundle["output_schemas_by_step"]["discover"][0] == {
        "output_id": "scope",
        "artifact": "source_scope.json",
        "type": "json",
        "schema": {"type": "object", "required": ["files"]},
    }
    step_bundle = json.loads(
        Path(result.artifact_dir, "agent_runs", "discover", "task_bundle.json").read_text(
            encoding="utf-8"
        )
    )
    assert step_bundle["output_schemas_by_step"]["discover"][0]["schema"]["required"] == [
        "files"
    ]


def test_prepare_workbench_task_run_writes_workflow_contract_artifact(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer
    from app.services.workflow_dsl import WorkflowStore

    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {
            "id": "corp-agent",
            "command": "corp-agent run --json",
            "supports_mcp": True,
            "mcp_profiles": ["codehub-readonly"],
            "supports_artifact_export": True,
            "supports_json_output": True,
        }
    ])

    repo = tmp_path / "repo"
    repo.mkdir()
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "contract_workflow",
        "name": "Contract workflow",
        "version": 1,
        "inputs": [
            {
                "id": "mr_link",
                "type": "mr_link",
                "required": True,
                "resolver": "agent_mcp",
                "role": "merge request URL",
            },
            {"id": "design_doc", "type": "file", "required": False, "role": "design"},
        ],
        "steps": [
            {
                "id": "collect_mr",
                "type": "agent_task",
                "provider": "corp-agent",
                "mcp_profile": "codehub-readonly",
                "goal": "Collect MR context through Agent MCP.",
                "required_artifacts": ["mr_snapshot.json", "changed_files.json"],
            }
        ],
        "outputs": [
            {
                "id": "mr_scope",
                "type": "json",
                "from": "collect_mr",
                "artifact": "mr_snapshot.json",
                "schema": {
                    "type": "object",
                    "required": ["mr_url", "changed_files_count"],
                    "properties": {
                        "mr_url": {"type": "string"},
                        "changed_files_count": {"type": "integer"},
                    },
                },
            }
        ],
    })

    result = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="contract_workflow",
        workspace_id="ws-contract",
        repo_path=str(repo),
        inputs={"mr_link": "https://codehub.local/project/merge_requests/7"},
    )

    contract = result.task_bundle["workflow_contract"]
    assert contract["workflow_id"] == "contract_workflow"
    assert contract["inputs"][0] == {
        "id": "mr_link",
        "type": "mr_link",
        "required": True,
        "role": "merge request URL",
        "resolver": "agent_mcp",
        "agent_owned": True,
    }
    assert contract["agent_steps"][0]["provider"] == "corp-agent"
    assert contract["agent_steps"][0]["mcp_profile"] == "codehub-readonly"
    assert contract["agent_steps"][0]["agent_owned_mcp"] is True
    assert contract["outputs"][0]["schema_required"] == ["mr_url", "changed_files_count"]
    assert contract["outputs"][0]["has_schema"] is True
    persisted = json.loads(
        Path(result.artifact_dir, "workflow_contract.json").read_text(encoding="utf-8")
    )
    assert persisted == contract
    step_bundle = json.loads(
        Path(result.artifact_dir, "agent_runs", "collect_mr", "task_bundle.json").read_text(
            encoding="utf-8"
        )
    )
    assert step_bundle["workflow_contract"]["agent_steps"][0]["agent_owned_mcp"] is True


def test_workbench_workflow_runner_infers_output_from_required_agent_artifact(
    tmp_path,
    monkeypatch,
):
    from app.config import settings
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
    from app.services.workflow_dsl import WorkflowStore

    script_path = tmp_path / "agent_scope.py"
    script_path.write_text(
        "import json, os, pathlib\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "(root/'source_scope.json').write_text(json.dumps({'scope':'tls'}), encoding='utf-8')\n"
        "(root/'evidence_cards.json').write_text(json.dumps([]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "module_analysis_like",
        "name": "Module analysis like",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [
            {
                "id": "discover_scope",
                "type": "agent_task",
                "provider": "local-python",
                "required_artifacts": ["source_scope.json", "evidence_cards.json"],
            }
        ],
        "outputs": [{"id": "scope", "type": "json", "from": "discover_scope"}],
    })
    task_run = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="module_analysis_like",
        workspace_id="ws-output-infer",
        repo_path=str(tmp_path),
        inputs={"module": "nvme tcp tls"},
    )

    result = WorkbenchWorkflowRunner(tmp_path / "task_runs").execute_task_run(
        task_run.task_run_id,
        timeout_sec=10,
    )

    assert result.status == "completed"
    assert result.outputs[0]["status"] == "ok"
    assert result.outputs[0]["artifact"] == "source_scope.json"


def test_workbench_workflow_runner_infers_output_from_builtin_step_artifact(tmp_path):
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
    from app.services.workflow_dsl import WorkflowStore

    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "builtin_output_infer",
        "name": "Builtin output infer",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [{"id": "validate_mr_evidence", "type": "evidence_validate"}],
        "outputs": [{"id": "mr_scope", "type": "json", "from": "validate_mr_evidence"}],
    })
    task_run = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="builtin_output_infer",
        workspace_id="ws-builtin-output-infer",
        repo_path=str(tmp_path),
        inputs={"module": "nvme tcp tls"},
    )

    result = WorkbenchWorkflowRunner(tmp_path / "task_runs").execute_task_run(
        task_run.task_run_id,
        timeout_sec=10,
    )

    assert result.status == "completed"
    assert result.outputs[0]["status"] == "ok"
    assert result.outputs[0]["artifact"] == "validate_mr_evidence.json"


def test_workbench_workflow_runner_injects_prior_step_artifacts_into_agent_task(
    tmp_path,
    monkeypatch,
):
    from app.config import settings
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
    from app.services.workflow_dsl import WorkflowStore

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
    script_path = tmp_path / "agent_prior.py"
    script_path.write_text(
        "import json, pathlib, sys\n"
        "payload=json.loads(sys.stdin.read())\n"
        "bundle=payload['task_bundle']\n"
        "root=pathlib.Path(payload['artifact_dir'])\n"
        "(root/'agent_seen.json').write_text(json.dumps({"
        "'prior': bundle.get('prior_step_results'),"
        "'artifacts': bundle.get('workflow_step_artifacts')"
        "}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "patch_prior_context",
        "name": "Patch prior context",
        "version": 1,
        "inputs": [{"id": "patch_diff", "type": "patch", "required": True}],
        "steps": [
            {"id": "parse_patch", "type": "diff_parse"},
            {
                "id": "analyze",
                "type": "agent_task",
                "provider": "local-python",
                "required_artifacts": ["agent_seen.json"],
            },
        ],
        "outputs": [{"id": "agent_seen", "type": "json", "from": "analyze"}],
    })
    task_run = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="patch_prior_context",
        workspace_id="ws-prior-artifacts",
        repo_path=str(tmp_path),
        inputs={"patch_diff": {"path": str(patch_file)}},
    )

    result = WorkbenchWorkflowRunner(tmp_path / "task_runs").execute_task_run(
        task_run.task_run_id,
        timeout_sec=10,
    )

    assert result.status == "completed"
    parse_result = result.step_results[0]
    assert parse_result["step_id"] == "parse_patch"
    assert "changed_files.json" in parse_result["artifacts"]
    seen = json.loads(
        Path(
            result.step_results[1]["artifact_dir"],
            "agent_seen.json",
        ).read_text(encoding="utf-8")
    )
    assert seen["prior"][0]["step_id"] == "parse_patch"
    parse_artifacts = seen["artifacts"]["parse_patch"]
    assert parse_artifacts["changed_files_json"].endswith("changed_files.json")
    changed = json.loads(Path(parse_artifacts["changed_files_json"]).read_text(encoding="utf-8"))
    assert changed == [{"path": "src/tls.c", "old_path": "src/tls.c", "status": "modified"}]


def test_workbench_workflow_runner_runs_second_agent_turn_for_source_slice_requests(
    tmp_path,
    monkeypatch,
):
    from app.config import settings
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
    from app.services.workflow_dsl import WorkflowStore

    source = tmp_path / "src" / "tls.c"
    source.parent.mkdir()
    source.write_text(
        "int nvmf_tcp_tls_handshake(void) {\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    script_path = tmp_path / "agent_slice_turns.py"
    script_path.write_text(
        "import json, pathlib, sys\n"
        "payload=json.loads(sys.stdin.read())\n"
        "bundle=payload['task_bundle']\n"
        "root=pathlib.Path(payload['artifact_dir'])\n"
        "slices=bundle.get('requested_source_slices') or []\n"
        "if not slices:\n"
        "    (root/'source_slice_requests.json').write_text(json.dumps({"
        "'need_source_slices':[{'file_path':'src/tls.c','start_line':1,'end_line':3,"
        "'reason':'need handshake implementation'}]}"
        "), encoding='utf-8')\n"
        "else:\n"
        "    (root/'source_scope.json').write_text(json.dumps({"
        "'files':[{'path':slices[0]['file_path'],'sha256':slices[0]['sha256']}],"
        "'excerpt':slices[0]['excerpt']"
        "}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "source_slice_turns",
        "name": "Source slice turns",
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
                "id": "source_scope",
                "type": "json",
                "from": "discover",
                "artifact": "source_scope.json",
            }
        ],
    })
    task_run = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="source_slice_turns",
        workspace_id="ws-source-slice-turns",
        repo_path=str(tmp_path),
        inputs={"module": "nvme tcp tls"},
    )

    result = WorkbenchWorkflowRunner(tmp_path / "task_runs").execute_task_run(
        task_run.task_run_id,
        timeout_sec=10,
    )

    assert result.status == "completed"
    step = result.step_results[0]
    assert step["turn_count"] == 2
    assert step["source_slice_requests"][0]["file_path"] == "src/tls.c"
    assert step["injected_source_slices"][0]["file_path"] == "src/tls.c"
    assert "nvmf_tcp_tls_handshake" in step["injected_source_slices"][0]["excerpt"]
    artifact_dir = Path(step["artifact_dir"])
    source_slices = json.loads((artifact_dir / "source_slices.json").read_text(encoding="utf-8"))
    assert source_slices[0]["sha256"]
    source_scope = json.loads((artifact_dir / "source_scope.json").read_text(encoding="utf-8"))
    assert source_scope["files"][0]["path"] == "src/tls.c"
    assert result.outputs[0]["status"] == "ok"
    turn_1 = artifact_dir / "turns" / "turn_1"
    turn_2 = artifact_dir / "turns" / "turn_2"
    assert json.loads((turn_1 / "execution_input.json").read_text(encoding="utf-8"))[
        "turn_id"
    ] == "turn_1"
    assert json.loads((turn_2 / "execution_input.json").read_text(encoding="utf-8"))[
        "turn_id"
    ] == "turn_2"
    assert not json.loads((turn_1 / "task_bundle.json").read_text(encoding="utf-8")).get(
        "requested_source_slices"
    )
    assert json.loads((turn_2 / "task_bundle.json").read_text(encoding="utf-8"))[
        "requested_source_slices"
    ][0]["file_path"] == "src/tls.c"
    assert (turn_1 / "raw_output.txt").exists()
    assert (turn_2 / "raw_output.txt").exists()
    assert (turn_1 / "execution_result.json").exists()
    assert (turn_2 / "execution_result.json").exists()
    assert step["turn_artifacts"] == [
        "turns/turn_1",
        "turns/turn_2",
    ]


def test_workbench_workflow_runner_parses_coverage_before_agent_task(
    tmp_path,
    monkeypatch,
):
    from app.config import settings
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
    from app.services.workflow_dsl import WorkflowStore

    coverage_file = tmp_path / "coverage.info"
    coverage_file.write_text(
        "TN:\n"
        "SF:src/tls.c\n"
        "FN:10,nvmf_tcp_tls_handshake\n"
        "FNDA:0,nvmf_tcp_tls_handshake\n"
        "FN:30,nvmf_tcp_tls_cleanup\n"
        "FNDA:3,nvmf_tcp_tls_cleanup\n"
        "FNF:2\n"
        "FNH:1\n"
        "end_of_record\n",
        encoding="utf-8",
    )
    script_path = tmp_path / "agent_coverage.py"
    script_path.write_text(
        "import json, pathlib, sys\n"
        "payload=json.loads(sys.stdin.read())\n"
        "bundle=payload['task_bundle']\n"
        "root=pathlib.Path(payload['artifact_dir'])\n"
        "(root/'agent_seen_coverage.json').write_text(json.dumps("
        "bundle.get('workflow_step_artifacts')"
        "), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "coverage_prior_context",
        "name": "Coverage prior context",
        "version": 1,
        "inputs": [{"id": "coverage_report", "type": "coverage_report", "required": True}],
        "steps": [
            {"id": "parse_coverage", "type": "coverage_parse"},
            {
                "id": "design",
                "type": "agent_task",
                "provider": "local-python",
                "required_artifacts": ["agent_seen_coverage.json"],
            },
        ],
        "outputs": [{"id": "agent_seen_coverage", "type": "json", "from": "design"}],
    })
    task_run = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="coverage_prior_context",
        workspace_id="ws-coverage-prior",
        repo_path=str(tmp_path),
        inputs={"coverage_report": {"path": str(coverage_file)}},
    )

    result = WorkbenchWorkflowRunner(tmp_path / "task_runs").execute_task_run(
        task_run.task_run_id,
        timeout_sec=10,
    )

    assert result.status == "completed"
    parse_result = result.step_results[0]
    assert "coverage_summary.json" in parse_result["artifacts"]
    assert "uncovered_functions.json" in parse_result["artifacts"]
    artifacts = json.loads(
        Path(
            result.step_results[1]["artifact_dir"],
            "agent_seen_coverage.json",
        ).read_text(encoding="utf-8")
    )
    coverage_artifacts = artifacts["parse_coverage"]
    uncovered = json.loads(
        Path(coverage_artifacts["uncovered_functions_json"]).read_text(encoding="utf-8")
    )
    assert uncovered == [
        {
            "file_path": "src/tls.c",
            "function_name": "nvmf_tcp_tls_handshake",
            "line_start": 10,
            "hit_count": 0,
        }
    ]


def test_workbench_evidence_validate_records_artifact_hashes(
    tmp_path,
    monkeypatch,
):
    from app.config import settings
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
    from app.services.workflow_dsl import WorkflowStore

    script_path = tmp_path / "agent_scope.py"
    script_path.write_text(
        "import json, os, pathlib\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "(root/'source_scope.json').write_text(json.dumps({'files':['src/tls.c']}), encoding='utf-8')\n"
        "(root/'evidence_cards.json').write_text(json.dumps([{'path':'src/tls.c'}]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "evidence_hash_audit",
        "name": "Evidence hash audit",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [
            {
                "id": "discover",
                "type": "agent_task",
                "provider": "local-python",
                "required_artifacts": ["source_scope.json", "evidence_cards.json"],
            },
            {"id": "validate_evidence", "type": "evidence_validate"},
        ],
        "outputs": [{"id": "validation", "type": "json", "from": "validate_evidence"}],
    })
    task_run = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="evidence_hash_audit",
        workspace_id="ws-evidence-hash",
        repo_path=str(tmp_path),
        inputs={"module": "nvme tcp tls"},
    )

    result = WorkbenchWorkflowRunner(tmp_path / "task_runs").execute_task_run(
        task_run.task_run_id,
        timeout_sec=10,
    )

    assert result.status == "completed"
    validation_path = (
        Path(task_run.artifact_dir)
        / "steps"
        / "validate_evidence"
        / "evidence_validation.json"
    )
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    details = validation["accepted_artifact_details"]
    assert {item["artifact"] for item in details} == {
        "source_scope.json",
        "evidence_cards.json",
    }
    assert {item["source_step_id"] for item in details} == {"discover"}
    assert all(item["sha256"] and item["size_bytes"] > 0 for item in details)
    assert all(Path(item["path"]).is_file() for item in details)


def test_workbench_report_render_includes_validation_hashes_and_source_slices(
    tmp_path,
    monkeypatch,
):
    from app.config import settings
    from app.services.evidence_memory import EvidenceMemoryStore
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
    from app.services.workflow_dsl import WorkflowStore

    memory = EvidenceMemoryStore(tmp_path / "memory.db")
    memory.record_analysis_run(
        run_id="run-prev",
        workspace_id="ws-report-audit",
        repo_path=str(tmp_path),
        object_text="nvme tcp tls",
        workflow_id="module_analysis",
        status="completed",
    )
    evidence_id = memory.upsert_evidence_item(
        run_id="run-prev",
        workspace_id="ws-report-audit",
        kind="source_file",
        subject_key="nof/nvmf_tcp/transport/tls/tls.c",
        status="verified_local",
        source="external_agent",
        path="nof/nvmf_tcp/transport/tls/tls.c",
        reason="validated TLS source",
        text="nvme tcp tls handshake cleanup",
    )
    memory.add_source_slice(
        evidence_id=evidence_id,
        file_path="nof/nvmf_tcp/transport/tls/tls.c",
        start_line=10,
        end_line=18,
        sha256="sliceabc123456",
        excerpt="int nvmf_tcp_tls_handshake(void) { return -EINVAL; }",
    )
    script_path = tmp_path / "agent_scope.py"
    script_path.write_text(
        "import json, os, pathlib\n"
        "root=pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "(root/'source_scope.json').write_text(json.dumps({'files':['src/tls.c']}), encoding='utf-8')\n"
        "(root/'evidence_cards.json').write_text(json.dumps([{'path':'src/tls.c'}]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "local-python", "command": f"python {script_path}"}
    ])
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "report_audit_workflow",
        "name": "Report audit workflow",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [
            {
                "id": "discover",
                "type": "agent_task",
                "provider": "local-python",
                "required_artifacts": ["source_scope.json", "evidence_cards.json"],
            },
            {"id": "validate_evidence", "type": "evidence_validate"},
            {"id": "render_report", "type": "report_render"},
        ],
        "outputs": [{"id": "report", "type": "markdown", "from": "render_report"}],
    })
    task_run = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
        evidence_memory=memory,
    ).prepare(
        workflow_id="report_audit_workflow",
        workspace_id="ws-report-audit",
        repo_path=str(tmp_path),
        inputs={"module": "nvme tcp tls"},
    )

    result = WorkbenchWorkflowRunner(tmp_path / "task_runs").execute_task_run(
        task_run.task_run_id,
        timeout_sec=10,
    )

    assert result.status == "completed"
    report = (
        Path(task_run.artifact_dir)
        / "steps"
        / "render_report"
        / "report.md"
    ).read_text(encoding="utf-8")
    assert "## Artifact Validation" in report
    assert "source_scope.json" in report
    assert "evidence_cards.json" in report
    assert "sha256" in report
    assert "## Source Slices" in report
    assert "nof/nvmf_tcp/transport/tls/tls.c:10-18" in report
    assert "sliceabc123456" in report


def test_workbench_workflow_runner_executes_builtin_context_and_report_steps(tmp_path):
    from app.services.evidence_memory import EvidenceMemoryStore
    from app.services.test_semantic_library import TestSemanticLibraryStore
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
    from app.services.workflow_dsl import WorkflowStore

    (tmp_path / "AGENTS.md").write_text(
        "Prefer fast-context before local grep.\n",
        encoding="utf-8",
    )
    memory = EvidenceMemoryStore(tmp_path / "memory.db")
    memory.record_analysis_run(
        run_id="run-prev",
        workspace_id="ws-runner-builtins",
        repo_path=str(tmp_path),
        object_text="nvme tcp tls",
        workflow_id="module_analysis",
        status="completed",
    )
    memory.upsert_evidence_item(
        run_id="run-prev",
        workspace_id="ws-runner-builtins",
        kind="source_file",
        subject_key="nof/nvmf_tcp/transport/tls/tls.c",
        status="verified_local",
        source="external_agent",
        path="nof/nvmf_tcp/transport/tls/tls.c",
        reason="validated TLS source",
        text="nvme tcp tls handshake cleanup",
    )
    semantics = TestSemanticLibraryStore(tmp_path / "semantics.db")
    semantics.upsert_case({
        "case_id": "TC_TLS_HANDSHAKE_FAIL",
        "feature": "NVMe TCP TLS",
        "module": "nvmf_tcp",
        "scenario": "TLS handshake fails and connection is released",
        "terms": ["TLS negotiation", "connection release"],
        "test_level": "black_box",
    })
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "builtin_steps_workflow",
        "name": "Builtin steps workflow",
        "version": 1,
        "inputs": [{"id": "module", "type": "free_text"}],
        "steps": [
            {"id": "semantic_lookup", "type": "semantic_retrieve"},
            {"id": "memory_lookup", "type": "memory_retrieve"},
            {"id": "validate_evidence", "type": "evidence_validate"},
            {"id": "render_report", "type": "report_render"},
        ],
        "outputs": [
            {"id": "report", "type": "markdown", "from": "render_report"},
            {"id": "semantic_lookup", "type": "json", "from": "semantic_lookup"},
            {"id": "memory_lookup", "type": "json", "from": "memory_lookup"},
        ],
    })
    task_run = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
        evidence_memory=memory,
        semantic_library=semantics,
    ).prepare(
        workflow_id="builtin_steps_workflow",
        workspace_id="ws-runner-builtins",
        repo_path=str(tmp_path),
        inputs={"module": "nvme tcp tls"},
    )

    result = WorkbenchWorkflowRunner(tmp_path / "task_runs").execute_task_run(
        task_run.task_run_id,
        timeout_sec=10,
    )

    assert result.status == "completed"
    assert [item["status"] for item in result.step_results] == [
        "completed",
        "completed",
        "completed",
        "completed",
    ]
    root = Path(task_run.artifact_dir)
    semantic_artifact = root / "steps" / "semantic_lookup" / "semantic_lookup.json"
    memory_artifact = root / "steps" / "memory_lookup" / "memory_lookup.json"
    report_artifact = root / "steps" / "render_report" / "report.md"
    assert "TC_TLS_HANDSHAKE_FAIL" in semantic_artifact.read_text(encoding="utf-8")
    assert "nof/nvmf_tcp/transport/tls/tls.c" in memory_artifact.read_text(encoding="utf-8")
    assert "TC_TLS_HANDSHAKE_FAIL" in report_artifact.read_text(encoding="utf-8")
    output_status = {item["id"]: item["status"] for item in result.outputs}
    assert output_status == {
        "report": "ok",
        "semantic_lookup": "ok",
        "memory_lookup": "ok",
    }
    execution = json.loads((root / "workflow_execution.json").read_text(encoding="utf-8"))
    assert execution["context_discovery_decision"]["fast-context"]["requested_by_agent_instructions"] is True
    assert execution["context_discovery_decision"]["fast-context"]["fallback_path"][-1] == "agent_cli"
