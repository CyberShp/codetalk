"""RED contract for F014 Task 5 deterministic Skill build candidates."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import sqlite3
from pathlib import Path
from types import ModuleType, SimpleNamespace
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


def _review_module() -> ModuleType:
    try:
        return importlib.import_module("app.services.skill_review")
    except ModuleNotFoundError as exc:
        if exc.name == "app.services.skill_review":
            pytest.fail("RED: app.services.skill_review has not been implemented")
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


def _full_review_evidence(
    *,
    decision: str,
    findings: list[dict[str, object]] | None = None,
    proposed_patches: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "purpose": "full release review",
        "session_id": "review-session/f014-task6",
        "review_kind": "full",
        "reviewed_paths": ["__FULL_CANDIDATE__"],
        "reviewed_at": "2026-08-05T00:00:00Z",
        "provider": "deepseek",
        "requested_model": "deepseek-v4-flash",
        "effective_model": "deepseek-v4-flash",
        "response_model": "deepseek-v4-flash",
        "declared_context_window_tokens": 200000,
        "requested_max_output_tokens": 4096,
        "decision": decision,
        "findings": findings or [],
        "proposed_patches": proposed_patches or [],
    }


def _record_review(store, build_id: str, evidence: dict[str, object]):
    review_module = _review_module()
    build = store.get_build(build_id)
    paths = evidence.get("reviewed_paths")
    if paths == ["__FULL_CANDIDATE__"]:
        paths = sorted(path.relative_to(build.unpacked_root).as_posix() for path in build.unpacked_root.rglob("*") if path.is_file())
        evidence = {**evidence, "reviewed_paths": paths}
    evidence = {
        **evidence,
        "reviewed_file_digests": [
            {"path": path, "digest": _file_digest(build.unpacked_root / str(path))}
            for path in sorted(str(path) for path in paths or [])
        ],
    }
    return review_module.SkillReviewService(store).record_review(
        build_id=build_id,
        review_evidence=evidence,
    )


def _file_digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _allow_tmp_cleanup(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts)):
        if path.is_dir():
            path.chmod(0o755)
        elif path.is_file():
            path.chmod(0o644)
    root.chmod(0o755)


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


def test_publish_build_requires_an_explicit_approved_full_review(tmp_path: Path) -> None:
    pipeline_module = _pipeline_module()
    store, draft = _draft(tmp_path)
    pipeline = pipeline_module.SkillBuildPipeline(store)
    build = pipeline.build_candidate(draft.draft_id)

    with pytest.raises(pipeline_module.SkillPublicationError) as missing_review:
        pipeline.publish_build(build.build_id)

    assert missing_review.value.code == "full_review_required"
    assert build.version_id is None
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0] == 0

    _record_review(
        store,
        build.build_id,
        {
            **_full_review_evidence(decision="approved"),
            "review_kind": "incremental",
            "reviewed_paths": ["workflow-manifest.json"],
        },
    )
    with pytest.raises(pipeline_module.SkillPublicationError) as incremental_review:
        pipeline.publish_build(build.build_id)

    assert incremental_review.value.code == "full_review_required"
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0] == 0


def test_publish_build_rejects_candidate_bytes_changed_after_full_review(tmp_path: Path) -> None:
    pipeline_module = _pipeline_module()
    store, draft = _draft(tmp_path)
    pipeline = pipeline_module.SkillBuildPipeline(store)
    build = pipeline.build_candidate(draft.draft_id)
    _record_review(store, build.build_id, _full_review_evidence(decision="approved"))
    (build.unpacked_root / "references" / "tool-routing.md").write_text("# mutated after review\n", encoding="utf-8")

    with pytest.raises(pipeline_module.SkillPublicationError) as caught:
        pipeline.publish_build(build.build_id)

    assert caught.value.code == "candidate_digest_mismatch"
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0] == 0


def test_publish_build_rejects_review_record_changed_after_digest_was_recorded(tmp_path: Path) -> None:
    pipeline_module = _pipeline_module()
    store, draft = _draft(tmp_path)
    pipeline = pipeline_module.SkillBuildPipeline(store)
    build = pipeline.build_candidate(draft.draft_id)
    review = _record_review(store, build.build_id, _full_review_evidence(decision="approved"))
    record = json.loads(review.record_path.read_text(encoding="utf-8"))
    record["review_evidence"]["decision"] = "changes_requested"
    review.record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(pipeline_module.SkillPublicationError) as caught:
        pipeline.publish_build(build.build_id)

    assert caught.value.code == "review_evidence_digest_mismatch"
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0] == 0


def test_publish_build_rejects_ir_changed_without_recomputed_embedded_digest(tmp_path: Path) -> None:
    pipeline_module = _pipeline_module()
    store, draft = _draft(tmp_path)
    pipeline = pipeline_module.SkillBuildPipeline(store)
    build = pipeline.build_candidate(draft.draft_id)
    _record_review(store, build.build_id, _full_review_evidence(decision="approved"))
    ir = json.loads(build.ir_path.read_text(encoding="utf-8"))
    ir["skill_id"] = "skill.tampered"
    build.ir_path.write_text(json.dumps(ir, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(pipeline_module.SkillPublicationError) as caught:
        pipeline.publish_build(build.build_id)

    assert caught.value.code == "ir_digest_mismatch"
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0] == 0


def test_explicit_publish_creates_immutable_version_with_linked_content_and_review_evidence(
    tmp_path: Path,
) -> None:
    pipeline_module = _pipeline_module()
    store, draft = _draft(tmp_path)
    pipeline = pipeline_module.SkillBuildPipeline(store)
    build = pipeline.build_candidate(draft.draft_id)
    review = _record_review(store, build.build_id, _full_review_evidence(decision="approved"))

    published = pipeline.publish_build(build.build_id)

    assert published.version_id
    assert published.content_digest == build.content_digest
    assert published.review_evidence_digest == review.review_evidence_digest
    assert published.source_zip_path.is_file()
    assert published.unpacked_root.is_dir()
    assert published.ir_path.is_file()
    assert published.validation_report_path.is_file()
    assert published.review_records_path.is_file()
    assert published.manifest_path.is_file()

    manifest = json.loads(published.manifest_path.read_text(encoding="utf-8"))
    assert manifest["version_id"] == published.version_id
    assert manifest["content_digest"] == build.content_digest
    assert manifest["review_evidence_digest"] == review.review_evidence_digest
    assert manifest["review_records"] == [review.review_id]
    assert manifest["artifacts"] == {
        "source_package": "source-package.zip",
        "source": "source",
        "ir": "ir/skill-ir-v1.json",
        "validation_report": "validation/validation-report.json",
        "review_records": "reviews/skill-reviews.json",
    }

    # A Version is a release snapshot, not another view of mutable candidate bytes.
    candidate_source = build.unpacked_root / "references" / "tool-routing.md"
    release_source = published.unpacked_root / "references" / "tool-routing.md"
    candidate_source.write_text("# changed after publication\n", encoding="utf-8")
    assert release_source.read_text(encoding="utf-8") == "# references/tool-routing.md\n"

    with sqlite3.connect(store.db_path) as db:
        version_count = db.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0]
    assert version_count == 1
    assert store.get_build(build.build_id).version_id == published.version_id

    with pytest.raises(PermissionError):
        release_source.write_text("# forbidden release mutation\n", encoding="utf-8")

    again = pipeline.publish_build(build.build_id)
    assert again.version_id == published.version_id
    assert sorted(path.name for path in (store.data_dir / "skills" / "versions").iterdir()) == [
        published.version_id
    ]
    _allow_tmp_cleanup(published.version_root)


def test_publish_build_cleans_version_files_when_database_registration_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline_module = _pipeline_module()
    store, draft = _draft(tmp_path)
    pipeline = pipeline_module.SkillBuildPipeline(store)
    build = pipeline.build_candidate(draft.draft_id)
    _record_review(store, build.build_id, _full_review_evidence(decision="approved"))

    def fail_registration(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(store, "record_published_version", fail_registration)

    with pytest.raises(RuntimeError, match="database unavailable"):
        pipeline.publish_build(build.build_id)

    versions_root = store.data_dir / "skills" / "versions"
    assert not versions_root.exists() or not list(versions_root.iterdir())
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0] == 0
    assert store.get_build(build.build_id).version_id is None


def test_acknowledged_high_risk_ai_finding_is_retained_without_becoming_a_hidden_release_blocker(
    tmp_path: Path,
) -> None:
    pipeline_module = _pipeline_module()
    store, draft = _draft(tmp_path)
    pipeline = pipeline_module.SkillBuildPipeline(store)
    build = pipeline.build_candidate(draft.draft_id)
    finding = {
        "finding_id": "finding.high-risk-contradiction",
        "severity": "p1",
        "summary": "AI detected a semantic contradiction in the guidance.",
        "locations": [{"path": "references/tool-routing.md", "field": "content"}],
        "reason": "The candidate describes incompatible tool-routing behavior.",
        "impact": "A future run could select an unsafe analysis route.",
        "recommendation": "Resolve or explicitly acknowledge the release risk.",
        "disposition": "acknowledged",
        "acknowledgement": {
            "policy_ref": "risk-register/F014-001",
            "rationale": "The release owner accepts this known, visible risk.",
        },
    }
    review = _record_review(
        store,
        build.build_id,
        _full_review_evidence(decision="acknowledged", findings=[finding]),
    )

    published = pipeline.publish_build(build.build_id)

    records = json.loads(published.review_records_path.read_text(encoding="utf-8"))
    assert records[0]["review_id"] == review.review_id
    assert records[0]["content_digest"] == build.content_digest
    assert records[0]["review_evidence_digest"] == review.review_evidence_digest
    assert records[0]["review_evidence"]["decision"] == "acknowledged"
    assert set(records[0]["review_evidence"]["reviewed_paths"])
    assert records[0]["review_evidence"]["findings"][0]["severity"] == "p1"
    assert records[0]["review_evidence"]["findings"][0]["disposition"] == "acknowledged"
    _allow_tmp_cleanup(published.version_root)


def test_publish_build_rejects_open_review_findings_even_when_review_row_claims_approved(
    tmp_path: Path,
) -> None:
    pipeline_module = _pipeline_module()
    store, draft = _draft(tmp_path)
    pipeline = pipeline_module.SkillBuildPipeline(store)
    build = pipeline.build_candidate(draft.draft_id)
    finding = {
        "finding_id": "finding.open-p1",
        "severity": "p1",
        "summary": "Open release blocker",
        "locations": [{"path": "references/tool-routing.md", "field": "content"}],
        "reason": "The candidate still has an unresolved blocker.",
        "impact": "Publication would hide unresolved review risk.",
        "recommendation": "Resolve or explicitly acknowledge the finding.",
        "disposition": "open",
    }
    evidence = _full_review_evidence(decision="approved", findings=[finding])
    build = store.get_build(build.build_id)
    paths = sorted(path.relative_to(build.unpacked_root).as_posix() for path in build.unpacked_root.rglob("*") if path.is_file())
    evidence = {
        **evidence,
        "reviewed_paths": paths,
        "reviewed_file_digests": [
            {"path": path, "digest": _file_digest(build.unpacked_root / str(path))}
            for path in paths
        ],
    }
    review_record = {
        "schema_version": "skill-review-v1",
        "review_id": "review_bypassed_open_finding",
        "skill_id": draft.skill_id,
        "content_digest": build.content_digest,
        "review_evidence_digest": pipeline_module._json_digest(evidence),
        "review_evidence": evidence,
    }
    record_path = Path(build.build_root) / "reviews" / "review_bypassed_open_finding" / "skill-review-v1.json"
    store.record_review(build_id=build.build_id, review_record=review_record, record_path=record_path)

    with pytest.raises(pipeline_module.SkillPublicationError) as caught:
        pipeline.publish_build(build.build_id)

    assert caught.value.code == "review_findings_unresolved"


def test_release_review_records_include_explicit_patch_decisions(tmp_path: Path) -> None:
    pipeline_module = _pipeline_module()
    store, draft = _draft(tmp_path)
    pipeline = pipeline_module.SkillBuildPipeline(store)
    build = pipeline.build_candidate(draft.draft_id)
    finding = {
        "finding_id": "finding.high-risk-contradiction",
        "severity": "p1",
        "summary": "AI detected a semantic contradiction in the guidance.",
        "locations": [{"path": "references/tool-routing.md", "field": "content"}],
        "reason": "The candidate describes incompatible tool-routing behavior.",
        "impact": "A future run could select an unsafe analysis route.",
        "recommendation": "Resolve or explicitly acknowledge the release risk.",
        "disposition": "acknowledged",
        "acknowledgement": {
            "policy_ref": "risk-register/F014-001",
            "rationale": "The release owner accepts this known, visible risk.",
        },
    }
    patch = {
        "patch_id": "patch.high-risk-contradiction",
        "finding_ids": ["finding.high-risk-contradiction"],
        "summary": "Clarify routing behavior",
        "target_path": "references/tool-routing.md",
        "base_digest": "sha256:" + "c" * 64,
        "unified_diff": "--- a/references/tool-routing.md\n+++ b/references/tool-routing.md\n@@ -1 +1 @@\n-old\n+new\n",
    }
    review = _record_review(
        store,
        build.build_id,
        _full_review_evidence(decision="acknowledged", findings=[finding], proposed_patches=[patch]),
    )
    store.record_patch_decision(
        SimpleNamespace(
            review_id=review.review_id,
            patch_id="patch.high-risk-contradiction",
            decision="reject",
            proposal_state="rejected",
            actor="human.reviewer",
            decided_at="2026-08-05T01:00:00Z",
        )
    )

    published = pipeline.publish_build(build.build_id)

    records = json.loads(published.review_records_path.read_text(encoding="utf-8"))
    assert records[0]["patch_decisions"] == [
        {
            "review_id": review.review_id,
            "patch_id": "patch.high-risk-contradiction",
            "decision": "reject",
            "proposal_state": "rejected",
            "actor": "human.reviewer",
            "decided_at": "2026-08-05T01:00:00Z",
        }
    ]
    _allow_tmp_cleanup(published.version_root)


def test_publish_recovery_rejects_orphan_version_with_tampered_review_audit(tmp_path: Path) -> None:
    pipeline_module = _pipeline_module()
    store, draft = _draft(tmp_path)
    pipeline = pipeline_module.SkillBuildPipeline(store)
    build = pipeline.build_candidate(draft.draft_id)
    review = _record_review(store, build.build_id, _full_review_evidence(decision="approved"))
    published = pipeline.publish_build(build.build_id)
    version_id = published.version_id
    _allow_tmp_cleanup(published.version_root)
    store.delete_version_metadata(version_id=version_id, build_id=build.build_id)

    published.review_records_path.write_text("[]\n", encoding="utf-8")
    manifest = json.loads(published.manifest_path.read_text(encoding="utf-8"))
    manifest["review_records"] = []
    published.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(pipeline_module.SkillPublicationError):
        pipeline.publish_build(build.build_id)

    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0] == 0
    assert store.get_build(build.build_id).version_id is None
    assert json.loads(review.record_path.read_text(encoding="utf-8"))["review_id"] == review.review_id


def test_publish_reclaims_stale_owner_lock_and_recovers_orphan_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pipeline_module = _pipeline_module()
    store, draft = _draft(tmp_path)
    pipeline = pipeline_module.SkillBuildPipeline(store)
    build = pipeline.build_candidate(draft.draft_id)
    _record_review(store, build.build_id, _full_review_evidence(decision="approved"))
    published = pipeline.publish_build(build.build_id)
    version_id = published.version_id
    _allow_tmp_cleanup(published.version_root)
    store.delete_version_metadata(version_id=version_id, build_id=build.build_id)

    lock_root = store.data_dir / "skills" / "versions" / f".{version_id}.lock"
    lock_root.mkdir(parents=True, exist_ok=False)
    (lock_root / "owner.json").write_text(
        json.dumps(
            {
                "pid": 0,
                "created_at_epoch": 1,
                "build_id": build.build_id,
                "version_id": version_id,
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    monotonic_values = iter([0.0, 31.0])
    monkeypatch.setattr(pipeline_module.time, "monotonic", lambda: next(monotonic_values, 31.0))
    monkeypatch.setattr(pipeline_module.time, "sleep", lambda _seconds: None)

    recovered = pipeline.publish_build(build.build_id)

    assert recovered.version_id == version_id
    assert not lock_root.exists()
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0] == 1
    assert store.get_build(build.build_id).version_id == version_id
    _allow_tmp_cleanup(published.version_root)


def test_publish_does_not_reclaim_active_owner_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline_module = _pipeline_module()
    store, draft = _draft(tmp_path)
    pipeline = pipeline_module.SkillBuildPipeline(store)
    build = pipeline.build_candidate(draft.draft_id)
    _record_review(store, build.build_id, _full_review_evidence(decision="approved"))

    version_id = f"skill_version_{build.build_id}"
    lock_root = store.data_dir / "skills" / "versions" / f".{version_id}.lock"
    lock_root.mkdir(parents=True, exist_ok=False)
    (lock_root / "owner.json").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "created_at_epoch": 1,
                "build_id": build.build_id,
                "version_id": version_id,
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    monotonic_values = iter([0.0, 31.0])
    monkeypatch.setattr(pipeline_module.time, "time", lambda: 2_000_000_001)
    monkeypatch.setattr(pipeline_module.time, "monotonic", lambda: next(monotonic_values, 31.0))
    monkeypatch.setattr(pipeline_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(pipeline_module.SkillPublicationError) as caught:
        pipeline.publish_build(build.build_id)

    assert caught.value.code == "publish_lock_timeout"
    assert lock_root.exists()
    assert (lock_root / "owner.json").exists()
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0] == 0
    pipeline_module._remove_tree(lock_root)


def test_publish_reclaim_does_not_delete_recreated_owner_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline_module = _pipeline_module()
    store, draft = _draft(tmp_path)
    pipeline = pipeline_module.SkillBuildPipeline(store)
    build = pipeline.build_candidate(draft.draft_id)
    _record_review(store, build.build_id, _full_review_evidence(decision="approved"))

    version_id = f"skill_version_{build.build_id}"
    lock_root = store.data_dir / "skills" / "versions" / f".{version_id}.lock"
    lock_root.mkdir(parents=True, exist_ok=False)
    owner_path = lock_root / "owner.json"
    stale_owner = {
        "pid": 0,
        "created_at_epoch": 1,
        "build_id": build.build_id,
        "version_id": version_id,
    }
    fresh_owner = {
        "pid": os.getpid(),
        "created_at_epoch": 2_000_000_000,
        "build_id": "other-build",
        "version_id": version_id,
    }
    owner_path.write_text(json.dumps(stale_owner), encoding="utf-8")
    original_reader = pipeline_module._read_publish_lock_owner_bytes
    read_count = 0

    def racing_reader(path: Path) -> bytes | None:
        nonlocal read_count
        read_count += 1
        if read_count == 2:
            owner_path.write_text(json.dumps(fresh_owner), encoding="utf-8")
        return original_reader(path)

    monotonic_values = iter([0.0, 31.0])
    monkeypatch.setattr(pipeline_module, "_read_publish_lock_owner_bytes", racing_reader)
    monkeypatch.setattr(pipeline_module.time, "time", lambda: 2_000_000_001)
    monkeypatch.setattr(pipeline_module.time, "monotonic", lambda: next(monotonic_values, 31.0))
    monkeypatch.setattr(pipeline_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(pipeline_module.SkillPublicationError) as caught:
        pipeline.publish_build(build.build_id)

    assert caught.value.code == "publish_lock_timeout"
    assert json.loads(owner_path.read_text(encoding="utf-8")) == fresh_owner
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM skill_versions").fetchone()[0] == 0
    pipeline_module._remove_tree(lock_root)
