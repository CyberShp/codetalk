"""Built-in CodeTalk Skill presets for the Skill Center startup experience."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.skill_build_pipeline import SkillBuildPipeline
from app.services.skill_review import ReviewProvenance, SkillReviewService
from app.services.skill_store import SkillStore


logger = logging.getLogger(__name__)

CODETALK_PRESET_PACK_ID = "pack.codetalks-v2.4"
CODETALK_PRESET_PROJECT_ID = "skill_project_codetalks_v24_presets"
CODETALK_PRESET_SOURCE_ROOT = "skills/presets/codetalks-v2.4"


@dataclass(frozen=True)
class CodeTalkPresetScenario:
    scenario_id: str
    skill_id: str
    label: str
    description: str


CODETALK_PRESET_SCENARIOS: tuple[CodeTalkPresetScenario, ...] = (
    CodeTalkPresetScenario(
        scenario_id="custom",
        skill_id="skill.codetalks-custom",
        label="自定义讲解",
        description="面向自由输入的代码讲解与测试设计。",
    ),
    CodeTalkPresetScenario(
        scenario_id="issue-regression",
        skill_id="skill.codetalks-issue-regression",
        label="Issue 回归",
        description="从缺陷或 MR 链接出发构造回归分析与黑盒用例。",
    ),
    CodeTalkPresetScenario(
        scenario_id="module-analysis",
        skill_id="skill.codetalks-module-full-analysis",
        label="模块全量分析",
        description="保留 9 个步骤、37 个必需产物和 8 个正式交付。",
    ),
    CodeTalkPresetScenario(
        scenario_id="root-cause",
        skill_id="skill.codetalks-root-cause",
        label="根因定位",
        description="围绕异常链和状态转换做根因解释。",
    ),
    CodeTalkPresetScenario(
        scenario_id="special-risk",
        skill_id="skill.codetalks-special-risk",
        label="专项风险",
        description="聚焦高风险路径、边界条件和专项验证。",
    ),
)


def codetalk_preset_source_root(data_dir: str | Path) -> Path:
    return Path(data_dir) / CODETALK_PRESET_SOURCE_ROOT


def codetalk_preset_payload(data_dir: str | Path) -> list[dict[str, str]]:
    source_root = codetalk_preset_source_root(data_dir)
    write_codetalk_v24_source(source_root)
    return [
        {
            "scenario_id": scenario.scenario_id,
            "skill_id": scenario.skill_id,
            "label": scenario.label,
            "description": scenario.description,
            "source_root": str(source_root),
        }
        for scenario in CODETALK_PRESET_SCENARIOS
    ]


def ensure_codetalk_skill_presets(store: SkillStore) -> dict[str, Any]:
    """Publish the five built-in CodeTalk scenarios if they are absent.

    The seeding path uses the same public Store -> Build -> Review -> Publish
    services as user-created Skills. It is intentionally idempotent per
    ``skill_id`` so backend restarts do not produce duplicate versions.
    """

    store.initialize_and_migrate()
    source_root = codetalk_preset_source_root(store.data_dir)
    write_codetalk_v24_source(source_root)
    project = _get_or_create_preset_project(store)
    created: list[str] = []
    existing: list[str] = []
    pipeline = SkillBuildPipeline(store)
    reviewer = SkillReviewService(store)
    for scenario in CODETALK_PRESET_SCENARIOS:
        if store.list_versions(skill_id=scenario.skill_id):
            existing.append(scenario.skill_id)
            continue
        draft = store.create_draft_from_source(
            project_id=project.project_id,
            source_root=source_root,
            source_scenario_id=scenario.scenario_id,
            skill_id=scenario.skill_id,
        )
        build = pipeline.build_candidate(draft.draft_id)
        reviewer.review_build(
            build.build_id,
            scope="full",
            provenance=ReviewProvenance(
                purpose=f"built-in CodeTalk preset seed: {scenario.scenario_id}",
                session_id=f"preset-seed/codetalks-v2.4/{scenario.scenario_id}",
                provider="deepseek",
                requested_model="deepseek-v4-flash",
                effective_model="deepseek-v4-flash",
                response_model="deepseek-v4-flash",
                declared_context_window_tokens=200000,
                requested_max_output_tokens=4096,
            ),
        )
        version = pipeline.publish_build(build.build_id)
        created.append(getattr(version, "version_id", scenario.skill_id))
    if created:
        logger.info("Seeded CodeTalk Skill presets: %s", created)
    return {
        "source_root": str(source_root),
        "created": created,
        "existing": existing,
        "scenario_count": len(CODETALK_PRESET_SCENARIOS),
    }


def write_codetalk_v24_source(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    manifest = _v24_manifest()
    _write_text(root / "workflow-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    for scenario in CODETALK_PRESET_SCENARIOS:
        _write_text(
            root / "workflows" / f"{scenario.scenario_id}.md",
            f"# {scenario.label}\n\n{scenario.description}\n",
        )
    for path in [
        "SKILL.md",
        "scripts/run_guard.py",
        "checklists/judge-checklist.md",
        "references/tool-routing.md",
        "templates/开发给测试讲代码模板.md",
        *manifest["required_core_rules"].values(),
        *(step["file"] for step in manifest["steps"]),
    ]:
        _write_text(root / path, _default_source_content(path))


def _get_or_create_preset_project(store: SkillStore) -> Any:
    try:
        return store.get_project(CODETALK_PRESET_PROJECT_ID)
    except KeyError:
        return store.create_project(
            project_id=CODETALK_PRESET_PROJECT_ID,
            name="CodeTalk v2.4 Preset Skills",
            pack_id=CODETALK_PRESET_PACK_ID,
        )


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    path.write_text(content, encoding="utf-8")


def _default_source_content(path: str) -> str:
    if path == "SKILL.md":
        return "# CodeTalk v2.4 Preset Pack\n\nBuilt-in scenarios for Skill-first task creation.\n"
    if path == "scripts/run_guard.py":
        return "print('codetalk skill run guard')\n"
    return f"# {path}\n\nThis file is part of the CodeTalk v2.4 preset Skill source.\n"


def _v24_manifest() -> dict[str, Any]:
    required_by_step = [
        ["活文档/01-范围与任务契约.md"],
        ["活文档/02-输入材料消费记录.md", "内部索引/运行计划.json", "内部索引/输入材料索引.json", "活文档/覆盖门禁/步骤02-覆盖门禁.md"],
        ["活文档/03-入口清单与说明.md", "活文档/04-流程清单与说明.md", "活文档/05-状态清单与说明.md", "活文档/06-资源清单与说明.md", "活文档/07-分析模型适用性.md", "活文档/覆盖门禁/步骤03-覆盖门禁.md"],
        ["活文档/08-分支处置与解释.md", "活文档/09-状态转换处置与解释.md", "活文档/10-资源生命周期处置与解释.md", "活文档/11-异常传播链与解释.md", "活文档/12-开发讲解覆盖台账.md", "活文档/覆盖门禁/步骤04-覆盖门禁.md"],
        ["活文档/13-场景候选池与推导说明.md", "活文档/14-风险点清单与因果说明.md", "活文档/覆盖门禁/步骤05-覆盖门禁.md"],
        ["活文档/15-SFMEA分析.md", "活文档/16-黑盒控制与观测映射.md", "活文档/17-测试设计依据.md", "活文档/覆盖门禁/步骤06-覆盖门禁.md"],
        ["活文档/18-测试追溯矩阵.md", "活文档/覆盖门禁/步骤07-覆盖门禁.md"],
        ["活文档/19-独立审查报告.md", "活文档/覆盖门禁/最终覆盖门禁.md", "内部索引/独立审查状态.json"],
        ["正式输出/开发给测试讲代码.md", "正式输出/流程分支状态资源与异常传播.md", "正式输出/风险点与SFMEA.md", "正式输出/黑盒测试场景.md", "正式输出/黑盒测试流程.md", "正式输出/黑盒测试用例.md", "正式输出/覆盖审计与分析限制.md", "正式输出/完整分析报告.md"],
    ]
    steps: list[dict[str, Any]] = []
    for index, required in enumerate(required_by_step, start=1):
        step_id = f"{index:02d}"
        step: dict[str, Any] = {
            "id": step_id,
            "file": f"steps/{step_id}-step.md",
            "required": required,
            "markdown_min_chars": 600 + index,
        }
        if index == 4:
            step["requires_glob"] = ["活文档/流程讲解/流程-*.md"]
            step["flow_narrative_validation"] = True
        steps.append(step)
    return {
        "version": "2.4",
        "required_core_rules": {
            "path-fidelity": "references/path-fidelity.md",
            "evidence-consumption": "references/evidence-consumption.md",
            "narrative-first": "references/markdown-narrative-first.md",
        },
        "evidence_allowed_status": ["parsed", "partially_parsed", "blocked", "out_of_scope", "unreadable"],
        "coverage_allowed_outcomes": ["analyzed", "covered_by_other", "not_applicable", "blocked", "need_verify", "truncated"],
        "flow_required_headings": ["## 一、这里是干什么的", "## 二、外部怎么触发"],
        "flow_key_narrative_headings": ["## 一、这里是干什么的"],
        "steps": steps,
    }
