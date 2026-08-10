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
CODETALK_PRESET_REVISION = "2026-08-10-step-contract-v1"

_STEP_INSTRUCTIONS = {
    "steps/01-step.md": """# Step 01 - 范围与任务契约

只完成本轮分析范围确认，不做后续全量源码分析。

1. 读取任务输入，确认目标模块、分析目标、用户约束和输入材料。
2. 只查看足以确认边界的少量源码或目录；禁止为了“更完整”扫描整个仓库。
3. 明确 in-scope、out-of-scope、关键假设、未知项和后续步骤需要遵守的范围。
4. 仅生成 `活文档/01-范围与任务契约.md`。
5. 文件写完且内容足够支撑后续步骤后立即结束当前 Step，不预执行 Step 02 及之后的工作。
""",
    "steps/02-step.md": """# Step 02 - 输入材料与运行计划

只整理输入材料和后续分析计划，不展开深度源码分析。

1. 读取 Step 01 的范围契约和本次任务输入。
2. 记录每份输入材料的用途、可读状态和与目标范围的关系。
3. 形成后续步骤的有限运行计划与输入索引；不得扩展 Step 01 已冻结的范围。
4. 生成本 Step 声明的输入消费记录、运行计划、输入材料索引和覆盖门禁文件。
5. 所有必需文件写完后立即结束当前 Step。
""",
    "steps/03-step.md": """# Step 03 - 入口、流程、状态与资源发现

在已冻结范围内完成第一轮源码证据发现。

1. 只搜索 Step 01 声明的模块或目录。
2. 定位主要外部入口、核心流程、关键状态和资源对象，并记录真实仓库相对路径及符号证据。
3. 给出分析模型适用性和仍待验证的证据缺口。
4. 生成本 Step 声明的入口、流程、状态、资源、模型适用性和覆盖门禁文件。
5. 不提前做详细分支展开、SFMEA 或测试用例设计；必需文件完成后结束。
""",
    "steps/04-step.md": """# Step 04 - 分支、状态转换、资源生命周期与异常传播

只深化 Step 03 已识别出的关键流程，不重新做无边界入口发现。

1. 沿已有入口和流程证据展开重要条件分支、状态转换、资源申请/释放和异常传播链。
2. 对关键流程生成 `活文档/流程讲解/流程-*.md`，并引用真实代码路径和符号。
3. 生成本 Step 声明的分支、状态、资源、异常传播、覆盖台账和覆盖门禁文件。
4. 新发现仅在与既有流程直接相关时纳入；禁止扩展到无关模块。
5. 所有必需文件完成后结束当前 Step。
""",
    "steps/05-step.md": """# Step 05 - 场景候选与风险推导

基于已有代码证据推导测试场景和风险，不重新扫描源码。

1. 使用 Step 03/04 的入口、流程、状态、资源和异常传播证据。
2. 推导正常、异常、边界、并发、恢复和资源不足等候选场景。
3. 对每个风险说明触发条件、原因、影响和已有检测/恢复线索。
4. 生成本 Step 声明的场景候选池、风险清单和覆盖门禁文件。
5. 必需文件完成后结束当前 Step。
""",
    "steps/06-step.md": """# Step 06 - SFMEA 与黑盒测试设计依据

把既有风险转换成可验证的测试设计，不扩大源码范围。

1. 对 Step 05 风险形成 SFMEA，包含 failure mode、cause、effect、detection、S/O/D、RPN 和 mitigation。
2. 把内部机制映射为黑盒可控制输入和可观察日志、指标、状态或协议行为。
3. 说明测试设计依据及证据来源。
4. 生成本 Step 声明的 SFMEA、控制与观测映射、测试设计依据和覆盖门禁文件。
5. 必需文件完成后结束当前 Step。
""",
    "steps/07-step.md": """# Step 07 - 测试追溯

建立证据、风险与测试设计之间的追溯关系。

1. 将入口、流程、状态、资源、异常链和风险逐项映射到对应测试场景或验证方式。
2. 标记尚未覆盖、被其他项覆盖、不适用、阻塞或仍需确认的项目。
3. 生成测试追溯矩阵和本 Step 覆盖门禁文件。
4. 不重新执行前面步骤的全量分析；仅针对明确缺口做最小补证。
5. 必需文件完成后结束当前 Step。
""",
    "steps/08-step.md": """# Step 08 - 独立审查

审查已有产物，不重新从头执行分析。

1. 检查前序产物是否满足范围、证据、覆盖、追溯和一致性要求。
2. 对缺口给出明确状态和原因；只允许为确认具体缺口做最小源码复核。
3. 生成独立审查报告、最终覆盖门禁和 `内部索引/独立审查状态.json`。
4. 不生成正式交付件；该工作留给 Step 09。
5. 必需文件完成后结束当前 Step。
""",
    "steps/09-step.md": """# Step 09 - 正式交付

只基于已完成并审查过的活文档进行最终汇总。

1. 汇总前序产物形成 8 个声明的正式输出。
2. 保留代码证据、分析限制、风险、SFMEA、黑盒场景、流程和用例之间的一致性。
3. 除非为修正一个明确的证据缺口，否则禁止重新进行全仓源码探索。
4. 写完全部声明的正式输出后立即结束；不要继续追加未声明的分析任务。
""",
}


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
    """Publish each built-in scenario once per preset source revision."""

    store.initialize_and_migrate()
    source_root = codetalk_preset_source_root(store.data_dir)
    write_codetalk_v24_source(source_root)
    project = _get_or_create_preset_project(store)
    created: list[str] = []
    existing: list[str] = []
    pipeline = SkillBuildPipeline(store)
    reviewer = SkillReviewService(store)
    for scenario in CODETALK_PRESET_SCENARIOS:
        versions = store.list_versions(skill_id=scenario.skill_id)
        if any(_has_current_preset_revision(version) for version in versions):
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
                session_id=f"preset-seed/codetalks-v2.4/{scenario.scenario_id}/{CODETALK_PRESET_REVISION}",
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


def _has_current_preset_revision(version: Any) -> bool:
    root = Path(str(getattr(version, "unpacked_root", "") or ""))
    try:
        skill_text = (root / "SKILL.md").read_text(encoding="utf-8")
    except OSError:
        return False
    return f"Preset revision: {CODETALK_PRESET_REVISION}" in skill_text


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
        return (
            "# CodeTalk v2.4 Preset Pack\n\n"
            f"Preset revision: {CODETALK_PRESET_REVISION}\n\n"
            "Built-in scenarios for Skill-first task creation.\n"
        )
    if path == "scripts/run_guard.py":
        return "print('codetalk skill run guard')\n"
    if path in _STEP_INSTRUCTIONS:
        return _STEP_INSTRUCTIONS[path]
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
