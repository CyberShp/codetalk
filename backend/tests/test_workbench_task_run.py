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
