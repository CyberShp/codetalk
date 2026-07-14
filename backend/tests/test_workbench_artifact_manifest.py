from pathlib import Path

from app.services.workbench_artifact_manifest import artifact_preview_with_redaction_status
from app.services.workbench_artifact_manifest import build_task_artifact_manifest


def test_artifact_preview_redacts_before_truncating_secret_boundary(tmp_path):
    secret = "boundaryPreviewSecretLeakValue1234567890"
    artifact = tmp_path / "diagnostics.log"
    artifact.write_text(
        ("x" * 1170) + f"\nAuthorization: Bearer {secret}\n",
        encoding="utf-8",
    )

    preview, redacted = artifact_preview_with_redaction_status(
        Path(artifact),
        artifact.read_bytes(),
        max_chars=1200,
    )

    assert redacted is True
    assert secret not in preview
    assert "boundary" not in preview
    assert "<redacted>" in preview


def test_artifact_manifest_marks_deliverables_and_diagnostics(tmp_path):
    task_dir = tmp_path / "task"
    (task_dir / "steps" / "discover_scope").mkdir(parents=True)
    (task_dir / "steps" / "render_report").mkdir(parents=True)
    (task_dir / "steps" / "discover_scope" / "source_scope.json").write_text(
        '{"files":[]}', encoding="utf-8"
    )
    (task_dir / "steps" / "render_report" / "report.md").write_text(
        "# Report\n", encoding="utf-8"
    )
    (task_dir / "task_bundle.json").write_text("{}", encoding="utf-8")
    (task_dir / "provider_snapshot.json").write_text("{}", encoding="utf-8")
    (task_dir / "inputs").mkdir()
    (task_dir / "inputs" / "requirements.md").write_text("req", encoding="utf-8")

    artifacts = {
        item["relative_path"]: item
        for item in build_task_artifact_manifest(task_dir)
    }

    assert artifacts["steps/discover_scope/source_scope.json"]["audience"] == "deliverable"
    assert artifacts["steps/render_report/report.md"]["audience"] == "deliverable"
    assert artifacts["task_bundle.json"]["audience"] == "diagnostic"
    assert artifacts["provider_snapshot.json"]["audience"] == "diagnostic"
    assert artifacts["inputs/requirements.md"]["audience"] == "input"


def test_artifact_manifest_marks_custom_declared_workflow_output_as_deliverable(tmp_path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "result.json").write_text('{"status":"ok"}', encoding="utf-8")
    (task_dir / "workflow_outputs.json").write_text(
        '{"outputs":[{"id":"custom_result","artifact":"result.json",'
        '"path":"result.json","status":"ok"}]}',
        encoding="utf-8",
    )

    artifacts = {
        item["relative_path"]: item
        for item in build_task_artifact_manifest(task_dir)
    }

    assert artifacts["result.json"]["audience"] == "deliverable"
    assert artifacts["workflow_outputs.json"]["audience"] == "diagnostic"


def test_artifact_manifest_ignores_agent_runtime_cache_directories(tmp_path):
    task_dir = tmp_path / "task"
    agent_dir = task_dir / "agent_runs" / "analyze"
    (agent_dir / "node-compile-cache" / "v1").mkdir(parents=True)
    (agent_dir / ".runtime-tmp-abc123" / "cache").mkdir(parents=True)
    (agent_dir / "node-compile-cache" / "v1" / "blob").write_bytes(b"cache")
    (agent_dir / ".runtime-tmp-abc123" / "cache" / "blob").write_bytes(b"cache")
    (agent_dir / ".runtime-codex-home-abc123").mkdir()
    (agent_dir / ".runtime-codex-home-abc123" / "state_5.sqlite").write_bytes(b"state")
    (agent_dir / ".tmp-report").mkdir()
    (agent_dir / ".tmp-report" / "result.md").write_text("# result", encoding="utf-8")
    (agent_dir / "module_analysis.md").write_text("# report", encoding="utf-8")

    paths = {
        item["relative_path"] for item in build_task_artifact_manifest(task_dir)
    }

    assert paths == {
        "agent_runs/analyze/module_analysis.md",
        "agent_runs/analyze/.tmp-report/result.md",
    }
