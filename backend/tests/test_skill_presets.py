from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.services import skill_presets
from app.services.skill_build_pipeline import SkillBuildPipeline
from app.services.skill_presets import (
    CODETALK_PRESET_SCENARIOS,
    ensure_codetalk_skill_presets,
    write_codetalk_v24_source,
)
from app.services.skill_review import ReviewProvenance, SkillReviewService
from app.services.skill_store import SkillStore


def _store(tmp_path: Path) -> SkillStore:
    return SkillStore(db_path=tmp_path / "skills.db", data_dir=tmp_path / "data")


def _source_tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(
        (item for item in root.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def test_codetalk_preset_source_matches_the_pinned_authoritative_archive(tmp_path: Path) -> None:
    source = tmp_path / "source"
    expected_inventory = json.loads(
        (
            Path(__file__).parent
            / "fixtures"
            / "skills"
            / "codetalks-v2.4"
            / "source-inventory.json"
        ).read_text(encoding="utf-8")
    )

    write_codetalk_v24_source(source)

    files = sorted(
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.is_file()
    )
    assert files == sorted(expected_inventory["files"])
    assert _source_tree_digest(source) == "4cd94b0bf7f389b30ed38890621a6d76ffc914c19ba2ff446a9cb4069282fa33"
    assert (
        getattr(skill_presets, "CODETALK_PRESET_SOURCE_ARCHIVE_SHA256", None)
        == expected_inventory["archive_sha256"]
    )

    manifest = json.loads((source / "workflow-manifest.json").read_text(encoding="utf-8"))
    assert [step["file"] for step in manifest["steps"]] == [
        "steps/01-intake-and-scope.md",
        "steps/02-evidence-consumption.md",
        "steps/03-breadth-inventory.md",
        "steps/04-flow-deep-analysis.md",
        "steps/05-scenario-expansion.md",
        "steps/06-sfmea-blackbox-translation.md",
        "steps/07-test-design.md",
        "steps/08-independent-judge.md",
        "steps/09-final-delivery.md",
    ]
    for step in manifest["steps"]:
        instruction = (source / step["file"]).read_text(encoding="utf-8")
        assert "## 目标" in instruction
        assert len(instruction.strip()) >= 150
        assert any(boundary in instruction for boundary in ("必须", "不得", "禁止", "只允许"))
        assert "This file is part of the CodeTalk" not in instruction


def test_codetalk_preset_source_rejects_placeholder_instructions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource = tmp_path / "resource"
    write_codetalk_v24_source(resource)
    manifest = json.loads((resource / "workflow-manifest.json").read_text(encoding="utf-8"))
    (resource / manifest["steps"][0]["file"]).write_text(
        "# placeholder\n\nThis file is part of the CodeTalk v2.4 preset Skill source.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(skill_presets, "CODETALK_PRESET_RESOURCE_ROOT", resource, raising=False)

    with pytest.raises(ValueError, match="placeholder"):
        write_codetalk_v24_source(tmp_path / "target")


def test_codetalk_presets_seed_five_published_versions_idempotently(tmp_path: Path) -> None:
    store = _store(tmp_path)

    first = ensure_codetalk_skill_presets(store)
    second = ensure_codetalk_skill_presets(store)

    assert first["scenario_count"] == 5
    assert len(first["created"]) == 5
    assert second["created"] == []
    versions = store.list_versions()
    seeded_skill_ids = {scenario.skill_id for scenario in CODETALK_PRESET_SCENARIOS}
    assert {version.skill_id for version in versions if version.skill_id in seeded_skill_ids} == seeded_skill_ids
    assert sum(1 for version in versions if version.skill_id in seeded_skill_ids) == 5

    module = next(version for version in versions if version.skill_id == "skill.codetalks-module-full-analysis")
    ir = json.loads(module.ir_path.read_text(encoding="utf-8"))
    review_records = json.loads(module.review_records_path.read_text(encoding="utf-8"))
    evidence = review_records[0]["review_evidence"]
    assert len(ir["steps"]) == 9
    assert ir["steps"][0]["instruction_path"] == "steps/01-intake-and-scope.md"
    assert len(ir["source_file_digests"]) == 37
    assert len(ir["deliveries"]) == 8
    assert evidence["provider"] == "deepseek"
    assert evidence["requested_model"] == "deepseek-v4-flash"
    assert evidence["declared_context_window_tokens"] == 200000
    assert evidence["requested_max_output_tokens"] == 4096


def test_codetalk_presets_publish_a_new_release_without_mutating_historical_versions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    historical_source = tmp_path / "historical-source"
    write_codetalk_v24_source(historical_source)
    (historical_source / "references" / "tool-routing.md").write_text(
        "# Historical v2.4 preset\n\nThis released source must remain immutable.\n",
        encoding="utf-8",
    )
    historical_project = store.create_project(
        project_id="skill_project_codetalks_v24_presets",
        name="CodeTalk v2.4 Preset Skills",
        pack_id="pack.codetalks-v2.4",
    )
    historical_draft = store.create_draft_from_source(
        project_id=historical_project.project_id,
        source_root=historical_source,
        source_scenario_id="module-analysis",
        skill_id="skill.codetalks-module-full-analysis",
    )
    historical_version = _publish(store, historical_draft.draft_id, session="preset/history/v2.4")
    historical_bytes = (historical_version.unpacked_root / "references" / "tool-routing.md").read_bytes()

    first = ensure_codetalk_skill_presets(store)
    second = ensure_codetalk_skill_presets(store)

    assert len(first["created"]) == 5
    assert second["created"] == []
    module_versions = store.list_versions(skill_id="skill.codetalks-module-full-analysis")
    assert len(module_versions) == 2
    current_version = next(
        version
        for version in module_versions
        if version.project_id == "skill_project_codetalks_v25_presets"
    )
    assert current_version.content_digest != historical_version.content_digest
    assert store.get_version(historical_version.version_id).content_digest == historical_version.content_digest
    assert (historical_version.unpacked_root / "references" / "tool-routing.md").read_bytes() == historical_bytes
    assert first["source_archive_sha256"] == "7369ef35d339bc554610754ceb385b78d15f94fc8e1e5435350c4ebcf2b27325"


def test_skill_modification_depths_produce_new_reviewed_versions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    source = tmp_path / "source"
    write_codetalk_v24_source(source)
    project = store.create_project(name="Modification depth", pack_id="pack.codetalks-depth")

    light = store.create_draft_from_source(
        project_id=project.project_id,
        source_root=source,
        source_scenario_id="custom",
        skill_id="skill.depth-light",
    )
    light_first = _publish(store, light.draft_id, session="depth/light/base")
    (light.filesystem_path / "references" / "tool-routing.md").write_text("# tool routing\n\nLight wording change.\n", encoding="utf-8")
    light_second = _publish(store, light.draft_id, session="depth/light/changed")
    first_ir = json.loads(light_first.ir_path.read_text(encoding="utf-8"))
    second_ir = json.loads(light_second.ir_path.read_text(encoding="utf-8"))
    assert light_first.content_digest != light_second.content_digest
    assert len(first_ir["steps"]) == len(second_ir["steps"])

    medium = store.create_draft_from_source(
        project_id=project.project_id,
        source_root=source,
        source_scenario_id="custom",
        skill_id="skill.depth-medium",
    )
    manifest_path = medium.filesystem_path / "workflow-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["steps"][0]["required"].append("活文档/20-修改深度验证.md")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    medium_version = _publish(store, medium.draft_id, session="depth/medium")
    medium_ir = json.loads(medium_version.ir_path.read_text(encoding="utf-8"))
    assert any(artifact["path"] == "活文档/20-修改深度验证.md" for artifact in medium_ir["artifacts"])

    heavy = store.create_draft_from_source(
        project_id=project.project_id,
        source_root=source,
        source_scenario_id="special-risk",
        skill_id="skill.depth-heavy-special-risk",
    )
    heavy_version = _publish(store, heavy.draft_id, session="depth/heavy")
    heavy_ir = json.loads(heavy_version.ir_path.read_text(encoding="utf-8"))
    assert heavy_version.skill_id == "skill.depth-heavy-special-risk"
    assert heavy_ir["selected_workflow_path"] == "workflows/special-risk.md"
    assert heavy_version.content_digest != medium_version.content_digest


def _publish(store: SkillStore, draft_id: str, *, session: str):
    build = SkillBuildPipeline(store).build_candidate(draft_id)
    SkillReviewService(store).review_build(
        build.build_id,
        scope="full",
        provenance=ReviewProvenance(
            purpose="modification-depth release review",
            session_id=session,
            provider="deepseek",
            requested_model="deepseek-v4-flash",
            effective_model="deepseek-v4-flash",
            response_model="deepseek-v4-flash",
            declared_context_window_tokens=200000,
            requested_max_output_tokens=4096,
        ),
    )
    return SkillBuildPipeline(store).publish_build(build.build_id)
