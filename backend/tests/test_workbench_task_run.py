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
        "outputs": [{"id": "report", "type": "markdown"}],
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
    root = Path(task_run.artifact_dir)
    assert (root / "workflow_execution.json").exists()
    assert "secret-value" not in (
        root / "agent_runs" / "collect_mr" / "raw_output.txt"
    ).read_text(encoding="utf-8")
