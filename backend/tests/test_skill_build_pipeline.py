"""RED contract for F014 Task 5 deterministic Skill build candidates."""

from __future__ import annotations

import copy
import importlib
import json
import sqlite3
from pathlib import Path
from types import ModuleType
from zipfile import ZipFile

import pytest

from test_skill_ir_compiler import _v24_manifest


def _store_module() -> ModuleType:
    try:
        return importlib.import_module("app.services.skill_store")
    except ModuleNotFoundError as exc:
        if exc.name == "app.services.skill_store":
            pytest.fail("RED: app.services.skill_store has not been implemented")
        raise


def _pipeline_module() -> ModuleType:
    try:
        return importlib.import_module("app.services.skill_build_pipeline")
    except ModuleNotFoundError as exc:
        if exc.name == "app.services.skill_build_pipeline":
            pytest.fail("RED: app.services.skill_build_pipeline has not been implemented")
        raise


def _write_v24_source(root: Path) -> None:
    manifest = _v24_manifest()
    root.mkdir(parents=True)
    (root / "workflow-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "workflows").mkdir(parents=True)
    for scenario in ["custom", "issue-regression", "module-analysis", "root-cause", "special-risk"]:
        (root / "workflows" / f"{scenario}.md").write_text(f"# {scenario}\n", encoding="utf-8")
    for path in [
        "SKILL.md",
        "scripts/run_guard.py",
        "checklists/judge-checklist.md",
        "references/tool-routing.md",
        "templates/开发给测试讲代码模板.md",
        *manifest["required_core_rules"].values(),
        *(step["file"] for step in manifest["steps"]),
    ]:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {path}\n", encoding="utf-8")


def _draft(tmp_path: Path):
    store_module = _store_module()
    db_path = tmp_path / "codetalk.db"
    data_dir = tmp_path / "data"
    source = tmp_path / "source"
    _write_v24_source(source)
    store = store_module.SkillStore(db_path=db_path, data_dir=data_dir)
    project = store.create_project(name="Codetalks Pack", pack_id="pack.codetalks")
    draft = store.create_draft_from_source(
        project_id=project.project_id,
        source_root=source,
        source_scenario_id="module-analysis",
        skill_id="skill.codetalks-module-full-analysis",
    )
    return store, draft


def test_build_candidate_writes_atomic_zip_unpacked_ir_validation_and_digest_map(tmp_path: Path) -> None:
    pipeline_module = _pipeline_module()
    store, draft = _draft(tmp_path)
    pipeline = pipeline_module.SkillBuildPipeline(store)

    build = pipeline.build_candidate(draft.draft_id)

    assert build.draft_id == draft.draft_id
    assert build.status == "built"
    assert build.version_id is None
    assert build.content_digest.startswith("sha256:")
    assert build.zip_path.is_file()
    assert build.unpacked_root.is_dir()
    assert build.ir_path.is_file()
    assert build.validation_report_path.is_file()
    assert build.file_digest_map_path.is_file()
    assert build.manifest_path.is_file()

    ir = json.loads(build.ir_path.read_text(encoding="utf-8"))
    validation = json.loads(build.validation_report_path.read_text(encoding="utf-8"))
    digest_map = json.loads(build.file_digest_map_path.read_text(encoding="utf-8"))
    manifest = json.loads(build.manifest_path.read_text(encoding="utf-8"))

    assert validation == {"ok": True, "issues": []}
    assert ir["skill_id"] == "skill.codetalks-module-full-analysis"
    assert len([artifact for artifact in ir["artifacts"] if artifact["required"]]) == 37
    assert digest_map["files"]["workflow-manifest.json"].startswith("sha256:")
    assert manifest["content_digest"] == build.content_digest
    assert manifest["review_required"] is True
    assert manifest["version_id"] is None
    assert manifest["ir_content_digest"] == ir["content_digest"]
    assert build.content_digest != ir["content_digest"]

    with ZipFile(build.zip_path) as archive:
        assert archive.namelist() == sorted(archive.namelist())
        assert "source/workflow-manifest.json" in archive.namelist()
        assert "ir/skill-ir-v1.json" in archive.namelist()
        assert "validation/validation-report.json" in archive.namelist()
        assert "manifest.json" in archive.namelist()

    with sqlite3.connect(store.db_path) as db:
        version_count = db.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0]
    assert version_count == 0


def test_build_candidate_is_deterministic_for_identical_source_bytes_and_changes_after_external_edit(tmp_path: Path) -> None:
    pipeline_module = _pipeline_module()
    store, draft = _draft(tmp_path)
    pipeline = pipeline_module.SkillBuildPipeline(store)

    first = pipeline.build_candidate(draft.draft_id)
    second = pipeline.build_candidate(draft.draft_id)
    assert first.content_digest == second.content_digest
    assert first.zip_digest == second.zip_digest

    (draft.filesystem_path / "references" / "tool-routing.md").write_text("# changed routing\n", encoding="utf-8")
    third = pipeline.build_candidate(draft.draft_id)

    assert third.content_digest != first.content_digest
    assert third.zip_digest != first.zip_digest


def test_build_candidate_records_validation_failure_without_publishing_or_partial_candidate(tmp_path: Path) -> None:
    pipeline_module = _pipeline_module()
    store, draft = _draft(tmp_path)
    manifest_path = draft.filesystem_path / "workflow-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["steps"][0]["id"] = "bad/id"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pipeline = pipeline_module.SkillBuildPipeline(store)

    with pytest.raises(pipeline_module.SkillBuildError) as caught:
        pipeline.build_candidate(draft.draft_id)

    assert caught.value.code == "validation_failed"
    assert caught.value.build_id
    failed = store.get_build(caught.value.build_id)
    report = json.loads(failed.validation_report_path.read_text(encoding="utf-8"))
    assert failed.status == "failed"
    assert failed.version_id is None
    assert report["ok"] is False
    assert report["issues"][0]["code"]
    assert not failed.zip_path.exists()

    with sqlite3.connect(store.db_path) as db:
        version_count = db.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0]
    assert version_count == 0


def test_build_candidate_rejects_externally_added_draft_symlink_without_copying_target(tmp_path: Path) -> None:
    pipeline_module = _pipeline_module()
    store, draft = _draft(tmp_path)
    outside = tmp_path / "outside-secret.md"
    outside.write_text("secret\n", encoding="utf-8")
    (draft.filesystem_path / "references" / "leak.md").symlink_to(outside)
    pipeline = pipeline_module.SkillBuildPipeline(store)

    with pytest.raises(pipeline_module.SkillBuildError) as caught:
        pipeline.build_candidate(draft.draft_id)

    assert caught.value.code == "validation_failed"
    failed = store.get_build(caught.value.build_id)
    report = json.loads(failed.validation_report_path.read_text(encoding="utf-8"))
    assert failed.status == "failed"
    assert report["issues"][0]["code"] == "unsafe_path"
    assert not failed.zip_path.exists()


def test_build_candidate_records_unexpected_artifact_failure_without_partial_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline_module = _pipeline_module()
    store, draft = _draft(tmp_path)
    pipeline = pipeline_module.SkillBuildPipeline(store)

    def fail_zip(*args, **kwargs):
        raise RuntimeError("zip failure")

    monkeypatch.setattr(pipeline_module, "_write_deterministic_zip", fail_zip)

    with pytest.raises(pipeline_module.SkillBuildError) as caught:
        pipeline.build_candidate(draft.draft_id)

    assert caught.value.code == "build_failed"
    failed = store.get_build(caught.value.build_id)
    report = json.loads(failed.validation_report_path.read_text(encoding="utf-8"))
    assert failed.status == "failed"
    assert report["ok"] is False
    assert report["issues"][0]["code"] == "build_failed"
    assert not failed.zip_path.exists()
    assert not list((store.data_dir / "skills" / "builds").glob(".*.tmp-*"))

    with sqlite3.connect(store.db_path) as db:
        version_count = db.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0]
    assert version_count == 0
