from __future__ import annotations

from app.services.skill_presets import CODETALK_PRESET_REVISION, write_codetalk_v24_source


def test_builtin_preset_steps_are_real_bounded_instructions(tmp_path) -> None:
    root = tmp_path / "preset"
    write_codetalk_v24_source(root)

    skill_text = (root / "SKILL.md").read_text(encoding="utf-8")
    assert f"Preset revision: {CODETALK_PRESET_REVISION}" in skill_text

    for index in range(1, 10):
        text = (root / "steps" / f"{index:02d}-step.md").read_text(encoding="utf-8")
        assert "This file is part of the CodeTalk v2.4 preset Skill source." not in text
        assert len(text.strip()) > 100

    step01 = (root / "steps" / "01-step.md").read_text(encoding="utf-8")
    assert "只完成本轮分析范围确认" in step01
    assert "扫描整个仓库" in step01
    assert "仅生成 `活文档/01-范围与任务契约.md`" in step01

    step09 = (root / "steps" / "09-step.md").read_text(encoding="utf-8")
    assert "只基于已完成并审查过的活文档" in step09
    assert "禁止重新进行全仓源码探索" in step09
