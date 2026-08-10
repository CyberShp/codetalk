from __future__ import annotations

import json

import pytest

from app.services.skill_agent_adapter import SkillAgentAdapterError, _render_step_prompt


def test_step_prompt_embeds_frozen_input_and_instruction(tmp_path) -> None:
    task_dir = tmp_path / "run"
    source_root = task_dir / "frozen_skill" / "source"
    instruction = source_root / "steps" / "01-step.md"
    instruction.parent.mkdir(parents=True)
    instruction.write_text(
        "# Step 01\n\n只确认 lib/nvmf 的分析范围，不执行后续全量分析。\n",
        encoding="utf-8",
    )
    (task_dir / "skill_input_snapshot.json").write_text(
        json.dumps(
            {"analysis_target": "lib/nvmf", "constraint": "只分析 TLS 建链"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    artifact_root = task_dir / "artifacts"
    artifact_root.mkdir()

    prompt = _render_step_prompt(
        task_dir=task_dir,
        artifact_root=artifact_root,
        node_id="step-01",
        step={"title": "Codetalks step 01", "instruction_path": "steps/01-step.md"},
        required_artifacts=["活文档/01-范围与任务契约.md"],
    )

    assert "lib/nvmf" in prompt
    assert "只分析 TLS 建链" in prompt
    assert "只确认 lib/nvmf 的分析范围" in prompt
    assert "do not execute or precompute later steps" in prompt
    assert "do not perform a full-repository analysis" in prompt
    assert "活文档/01-范围与任务契约.md" in prompt


def test_step_prompt_fails_fast_when_instruction_is_missing(tmp_path) -> None:
    task_dir = tmp_path / "run"
    (task_dir / "frozen_skill" / "source").mkdir(parents=True)
    (task_dir / "skill_input_snapshot.json").write_text("{}", encoding="utf-8")
    artifact_root = task_dir / "artifacts"
    artifact_root.mkdir()

    with pytest.raises(SkillAgentAdapterError, match="skill_step_instruction_unavailable"):
        _render_step_prompt(
            task_dir=task_dir,
            artifact_root=artifact_root,
            node_id="step-01",
            step={"instruction_path": "steps/01-step.md"},
            required_artifacts=["活文档/01-范围与任务契约.md"],
        )
