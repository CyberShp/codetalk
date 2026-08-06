from __future__ import annotations

import json
from pathlib import Path

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
    assert len(ir["deliveries"]) == 8
    assert evidence["provider"] == "deepseek"
    assert evidence["requested_model"] == "deepseek-v4-flash"
    assert evidence["declared_context_window_tokens"] == 200000
    assert evidence["requested_max_output_tokens"] == 4096


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
