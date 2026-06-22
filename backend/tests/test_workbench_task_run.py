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
    memory.upsert_evidence_item(
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
    assert context_bundle["semantic_cases"][0]["case_id"] == "TC_TLS_HANDSHAKE_FAIL"
    assert Path(result.artifact_dir, "context_bundle.json").exists()
    step_bundle = json.loads(
        Path(result.artifact_dir, "agent_runs", "design", "task_bundle.json").read_text(encoding="utf-8")
    )
    assert step_bundle["context_bundle"]["semantic_cases"][0]["terms"] == [
        "TLS negotiation",
        "connection release",
    ]


def test_prepare_workbench_task_run_embeds_repo_agent_instructions(tmp_path):
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer

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
    assert snapshot["steps"]["known"]["provider"] == "corp-agent"
    assert snapshot["providers"]["missing-agent"]["status"] == "unknown_provider"
    assert "missing-agent" in snapshot["warnings"][0]
    persisted = json.loads(
        Path(result.artifact_dir, "provider_snapshot.json").read_text(encoding="utf-8")
    )
    assert persisted["steps"]["unknown"]["provider"] == "missing-agent"
    step_bundle = json.loads(
        Path(result.artifact_dir, "agent_runs", "known", "task_bundle.json").read_text(encoding="utf-8")
    )
    assert step_bundle["provider_snapshot"]["providers"]["corp-agent"]["status"] == "configured"


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
