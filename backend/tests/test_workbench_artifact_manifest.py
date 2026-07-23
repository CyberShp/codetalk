from pathlib import Path
import errno

import pytest

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
    (task_dir / "provider_live_readiness.json").write_text("{}", encoding="utf-8")
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
    assert artifacts["provider_live_readiness.json"]["kind"] == "provider_live_readiness"
    assert artifacts["provider_live_readiness.json"]["audience"] == "diagnostic"
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


def test_artifact_manifest_marks_declared_mindmap_companions_as_deliverables(tmp_path):
    task_dir = tmp_path / "task"
    agent_dir = task_dir / "agent_runs" / "analyze"
    agent_dir.mkdir(parents=True)
    for name, content in {
        "test_design_mindmap.json": "{}",
        "test_design_mindmap.html": "<!doctype html>",
        "test_design_mindmap.svg": "<svg></svg>",
    }.items():
        (agent_dir / name).write_text(content, encoding="utf-8")
    (task_dir / "workflow_snapshot.json").write_text(
        '{"outputs":[{"id":"test_design_mindmap",'
        '"artifact":"test_design_mindmap.json",'
        '"companion_artifacts":["test_design_mindmap.html",'
        '"test_design_mindmap.svg"]}]}',
        encoding="utf-8",
    )

    artifacts = {
        item["relative_path"]: item
        for item in build_task_artifact_manifest(task_dir)
    }

    assert artifacts["agent_runs/analyze/test_design_mindmap.json"]["audience"] == "deliverable"
    assert artifacts["agent_runs/analyze/test_design_mindmap.html"]["audience"] == "deliverable"
    assert artifacts["agent_runs/analyze/test_design_mindmap.svg"]["audience"] == "deliverable"


def test_artifact_manifest_keeps_undeclared_stage_files_out_of_deliverables(tmp_path):
    task_dir = tmp_path / "task"
    agent_dir = task_dir / "agent_runs" / "analyze"
    agent_dir.mkdir(parents=True)
    (agent_dir / "source_scope.json").write_text('{"files":[]}', encoding="utf-8")
    (agent_dir / "evidence_cards.json").write_text("[]", encoding="utf-8")
    (agent_dir / "report.md").write_text("# Report\n", encoding="utf-8")
    (task_dir / "workflow_snapshot.json").write_text(
        '{"outputs":[{"id":"report","artifact":"report.md"}]}',
        encoding="utf-8",
    )

    artifacts = {
        item["relative_path"]: item
        for item in build_task_artifact_manifest(task_dir)
    }

    assert artifacts["agent_runs/analyze/report.md"]["audience"] == "deliverable"
    assert artifacts["agent_runs/analyze/source_scope.json"]["audience"] == "support"
    assert artifacts["agent_runs/analyze/evidence_cards.json"]["audience"] == "support"


def test_artifact_manifest_uses_frozen_v3_contract_layers(tmp_path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "完整分析报告.md").write_text("# 完整分析报告\n", encoding="utf-8")
    (task_dir / "evidence_cards.json").write_text("[]", encoding="utf-8")
    (task_dir / "provider_diagnostics.json").write_text("{}", encoding="utf-8")
    (task_dir / "artifact_contract_v3.json").write_text(
        """{
          "schema_version": "artifact-contract-v3",
          "artifacts": [
            {"artifact": "完整分析报告.md", "layer": "deliverable", "required": true, "downloadable": true},
            {"artifact": "evidence_cards.json", "layer": "supporting", "required": true, "downloadable": true},
            {"artifact": "provider_diagnostics.json", "layer": "diagnostic", "required": false, "downloadable": false}
          ]
        }""",
        encoding="utf-8",
    )

    artifacts = {
        item["relative_path"]: item
        for item in build_task_artifact_manifest(task_dir)
    }

    assert artifacts["完整分析报告.md"]["audience"] == "deliverable"
    assert artifacts["完整分析报告.md"]["layer"] == "deliverable"
    assert artifacts["完整分析报告.md"]["contract_required"] is True
    assert artifacts["evidence_cards.json"]["audience"] == "support"
    assert artifacts["evidence_cards.json"]["layer"] == "supporting"
    assert artifacts["provider_diagnostics.json"]["audience"] == "diagnostic"
    assert artifacts["provider_diagnostics.json"]["downloadable"] is False


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


def test_artifact_manifest_does_not_depend_on_recursive_rglob_during_runtime_cleanup(
    tmp_path, monkeypatch
):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "report.md").write_text("# report\n", encoding="utf-8")

    def disappearing_rglob(_self, _pattern):
        raise FileNotFoundError("runtime directory was removed during scan")

    monkeypatch.setattr(Path, "rglob", disappearing_rglob)

    artifacts = build_task_artifact_manifest(task_dir)

    assert [item["relative_path"] for item in artifacts] == ["report.md"]


def test_artifact_manifest_only_ignores_disappeared_directories(tmp_path, monkeypatch):
    import app.services.workbench_artifact_manifest as manifest_module

    task_dir = tmp_path / "task"
    task_dir.mkdir()

    def denied_walk(*_args, onerror=None, **_kwargs):
        assert onerror is not None
        onerror(PermissionError(errno.EACCES, "permission denied", str(task_dir / "locked")))
        return iter(())

    monkeypatch.setattr(manifest_module.os, "walk", denied_walk)

    with pytest.raises(PermissionError):
        build_task_artifact_manifest(task_dir)


def test_artifact_manifest_does_not_hide_file_permission_errors(tmp_path, monkeypatch):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    artifact = task_dir / "report.md"
    artifact.write_text("# report\n", encoding="utf-8")
    original_read_bytes = Path.read_bytes

    def denied_read(path):
        if path == artifact:
            raise PermissionError(errno.EACCES, "permission denied", str(path))
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", denied_read)

    with pytest.raises(PermissionError):
        build_task_artifact_manifest(task_dir)


@pytest.mark.parametrize(
    "declaration_name",
    ["workflow_outputs.json", "workflow_snapshot.json", "workflow_contract.json"],
)
def test_artifact_manifest_does_not_hide_declaration_permission_errors(
    tmp_path,
    monkeypatch,
    declaration_name,
):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    declaration = task_dir / declaration_name
    declaration.write_text('{"outputs":[]}', encoding="utf-8")
    original_read_text = Path.read_text

    def denied_read_text(path, *args, **kwargs):
        if path == declaration:
            raise PermissionError(errno.EACCES, "permission denied", str(path))
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied_read_text)

    with pytest.raises(PermissionError):
        build_task_artifact_manifest(task_dir)
