"""RED contract for F014 Task 5 filesystem-authoritative Skill drafts."""

from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path
from types import ModuleType

import pytest


def _store_module() -> ModuleType:
    try:
        return importlib.import_module("app.services.skill_store")
    except ModuleNotFoundError as exc:
        if exc.name == "app.services.skill_store":
            pytest.fail("RED: app.services.skill_store has not been implemented")
        raise


def _write_minimal_source(root: Path) -> None:
    (root / "workflows").mkdir(parents=True)
    (root / "steps").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    (root / "workflow-manifest.json").write_text(
        json.dumps(
            {
                "version": "2.4",
                "required_core_rules": {
                    "path-fidelity": "references/path-fidelity.md",
                    "evidence-consumption": "references/evidence-consumption.md",
                    "narrative-first": "references/narrative-first.md",
                },
                "evidence_allowed_status": ["parsed"],
                "coverage_allowed_outcomes": ["analyzed"],
                "flow_required_headings": ["## flow"],
                "flow_key_narrative_headings": ["## flow"],
                "steps": [
                    {
                        "id": "01",
                        "file": "steps/01.md",
                        "required": ["活文档/01.md"],
                        "markdown_min_chars": 1,
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "workflows" / "custom.md").write_text("# custom\n", encoding="utf-8")
    (root / "steps" / "01.md").write_text("# step\n", encoding="utf-8")
    (root / "scripts" / "run_guard.py").write_text("print('ok')\n", encoding="utf-8")
    for path in ["path-fidelity.md", "evidence-consumption.md", "narrative-first.md"]:
        (root / "references" / path).write_text(f"# {path}\n", encoding="utf-8")


def _paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    return tmp_path / "codetalk.db", tmp_path / "data", tmp_path / "source"


def test_create_draft_copies_source_files_to_filesystem_and_stores_only_metadata(tmp_path: Path) -> None:
    module = _store_module()
    db_path, data_dir, source = _paths(tmp_path)
    _write_minimal_source(source)
    store = module.SkillStore(db_path=db_path, data_dir=data_dir)

    project = store.create_project(name="Codetalks Pack", pack_id="pack.codetalks")
    draft = store.create_draft_from_source(
        project_id=project.project_id,
        source_root=source,
        source_scenario_id="custom",
        skill_id="skill.codetalks-custom",
    )

    assert draft.project_id == project.project_id
    assert draft.source_scenario_id == "custom"
    assert draft.filesystem_path == data_dir / "skills" / "drafts" / draft.draft_id / "source"
    assert (draft.filesystem_path / "workflow-manifest.json").is_file()
    assert (draft.filesystem_path / "workflows" / "custom.md").read_text(encoding="utf-8") == "# custom\n"

    with sqlite3.connect(db_path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(skill_drafts)").fetchall()}
        row = db.execute("SELECT * FROM skill_drafts WHERE draft_id = ?", (draft.draft_id,)).fetchone()

    assert row is not None
    assert "source_json" not in columns
    assert "ir_json" not in columns
    assert "content_json" not in columns


def test_rescan_draft_reflects_external_filesystem_edits_without_database_update(tmp_path: Path) -> None:
    module = _store_module()
    db_path, data_dir, source = _paths(tmp_path)
    _write_minimal_source(source)
    store = module.SkillStore(db_path=db_path, data_dir=data_dir)
    project = store.create_project(name="Codetalks Pack")
    draft = store.create_draft_from_source(
        project_id=project.project_id,
        source_root=source,
        source_scenario_id="custom",
        skill_id="skill.codetalks-custom",
    )

    before = store.rescan_draft(draft.draft_id)
    (draft.filesystem_path / "workflows" / "custom.md").write_text("# externally edited\n", encoding="utf-8")
    after = store.rescan_draft(draft.draft_id)

    assert before.file_digests != after.file_digests
    assert after.file_digests["workflows/custom.md"].startswith("sha256:")
    assert after.file_count == before.file_count
    assert store.get_draft(draft.draft_id).updated_at == draft.updated_at


def test_create_draft_rejects_source_roots_outside_filesystem_and_missing_projects(tmp_path: Path) -> None:
    module = _store_module()
    db_path, data_dir, source = _paths(tmp_path)
    _write_minimal_source(source)
    store = module.SkillStore(db_path=db_path, data_dir=data_dir)

    with pytest.raises(KeyError):
        store.create_draft_from_source(
            project_id="missing",
            source_root=source,
            source_scenario_id="custom",
            skill_id="skill.codetalks-custom",
        )

    project = store.create_project(name="Codetalks Pack")
    symlink = tmp_path / "source-link"
    symlink.symlink_to(source, target_is_directory=True)

    with pytest.raises(module.SkillStoreError) as caught:
        store.create_draft_from_source(
            project_id=project.project_id,
            source_root=symlink,
            source_scenario_id="custom",
            skill_id="skill.codetalks-custom",
        )

    assert caught.value.code == "unsafe_source_root"


def test_record_review_retains_audit_metadata_without_mutating_draft_source(tmp_path: Path) -> None:
    module = _store_module()
    db_path, data_dir, source = _paths(tmp_path)
    _write_minimal_source(source)
    store = module.SkillStore(db_path=db_path, data_dir=data_dir)
    project = store.create_project(name="Codetalks Pack")
    draft = store.create_draft_from_source(
        project_id=project.project_id,
        source_root=source,
        source_scenario_id="custom",
        skill_id="skill.codetalks-custom",
    )
    build = store.register_build(
        draft_id=draft.draft_id,
        build_id="build-review-red",
        status="built",
        content_digest="sha256:" + "a" * 64,
        zip_digest="sha256:" + "b" * 64,
    )
    record_path = data_dir / "skills" / "builds" / build.build_id / "reviews" / "review.full" / "skill-review-v1.json"
    review_record = {
        "schema_version": "skill-review-v1",
        "review_id": "review.full",
        "skill_id": draft.skill_id,
        "content_digest": build.content_digest,
        "review_evidence_digest": "sha256:" + "c" * 64,
        "review_evidence": {
            "purpose": "full release review",
            "session_id": "session-review-1",
            "review_kind": "full",
            "reviewed_at": "2026-08-04T00:00:00Z",
            "provider": "deepseek",
            "requested_model": "deepseek-v4-flash",
            "effective_model": "deepseek-v4-flash",
            "response_model": "deepseek-v4-flash",
            "declared_context_window_tokens": 200000,
            "requested_max_output_tokens": 4096,
            "decision": "approved",
            "findings": [],
            "proposed_patches": [],
        },
    }
    before = (draft.filesystem_path / "workflows" / "custom.md").read_text(encoding="utf-8")

    review = store.record_review(build_id=build.build_id, review_record=review_record, record_path=record_path)

    assert review.review_id == "review.full"
    assert review.review_kind == "full"
    assert review.decision == "approved"
    assert review.record_path == record_path
    assert json.loads(record_path.read_text(encoding="utf-8")) == review_record
    assert store.required_full_review_for_build(build.build_id).review_id == review.review_id
    assert (draft.filesystem_path / "workflows" / "custom.md").read_text(encoding="utf-8") == before

    with sqlite3.connect(db_path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(skill_reviews)").fetchall()}
    assert {"build_id", "review_kind", "decision", "review_evidence_digest", "record_path"} <= columns
