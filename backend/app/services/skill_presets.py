"""Built-in CodeTalk Skill presets for the Skill Center startup experience."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.skill_build_pipeline import SkillBuildPipeline
from app.services.skill_review import ReviewProvenance, SkillReviewService
from app.services.skill_store import SkillStore


logger = logging.getLogger(__name__)

CODETALK_PRESET_RELEASE = "v2.5"
CODETALK_PRESET_PACK_ID = "pack.codetalks-v2.5"
CODETALK_PRESET_PROJECT_ID = "skill_project_codetalks_v25_presets"
CODETALK_PRESET_SOURCE_ROOT = "skills/presets/codetalks-v2.5"
CODETALK_PRESET_SOURCE_ARCHIVE = "codetalks-fused-v2.4-zh.zip"
CODETALK_PRESET_SOURCE_ARCHIVE_SHA256 = "7369ef35d339bc554610754ceb385b78d15f94fc8e1e5435350c4ebcf2b27325"
CODETALK_PRESET_SOURCE_TREE_SHA256 = "4cd94b0bf7f389b30ed38890621a6d76ffc914c19ba2ff446a9cb4069282fa33"
CODETALK_PRESET_RESOURCE_ROOT = Path(__file__).resolve().parents[1] / "resources" / "skills" / "codetalks-v2.5"

CODETALK_PRESET_SOURCE_FILES: tuple[str, ...] = (
    "NOTICE.md",
    "README.md",
    "SKILL.md",
    "checklists/final-validation.md",
    "checklists/judge-checklist.md",
    "references/analysis-models.md",
    "references/codehub-mr-access.md",
    "references/coverage-usage.md",
    "references/evidence-consumption.md",
    "references/failure-guidewords.md",
    "references/markdown-narrative-first.md",
    "references/output-separation.md",
    "references/path-fidelity.md",
    "references/scenario-expansion-engine.md",
    "references/tool-routing.md",
    "references/worker-judge-protocol.md",
    "scripts/run_guard.py",
    "steps/01-intake-and-scope.md",
    "steps/02-evidence-consumption.md",
    "steps/03-breadth-inventory.md",
    "steps/04-flow-deep-analysis.md",
    "steps/05-scenario-expansion.md",
    "steps/06-sfmea-blackbox-translation.md",
    "steps/07-test-design.md",
    "steps/08-independent-judge.md",
    "steps/09-final-delivery.md",
    "templates/coverage-gate-template.md",
    "templates/开发给测试讲代码模板.md",
    "templates/流程讲解活文档模板.md",
    "templates/黑盒测试用例Markdown模板.md",
    "workflow-manifest.json",
    "workflows/custom.md",
    "workflows/issue-regression.md",
    "workflows/module-analysis.md",
    "workflows/root-cause.md",
    "workflows/special-risk.md",
    "运行产物中文命名对照.md",
)

CODETALK_PRESET_STEP_FILES: tuple[str, ...] = (
    "steps/01-intake-and-scope.md",
    "steps/02-evidence-consumption.md",
    "steps/03-breadth-inventory.md",
    "steps/04-flow-deep-analysis.md",
    "steps/05-scenario-expansion.md",
    "steps/06-sfmea-blackbox-translation.md",
    "steps/07-test-design.md",
    "steps/08-independent-judge.md",
    "steps/09-final-delivery.md",
)

_PLACEHOLDER_MARKERS = (
    "this file is part of the codetalk",
    "built-in scenarios for skill-first task creation",
    "print('codetalk skill run guard')",
)


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
            "preset_release": CODETALK_PRESET_RELEASE,
            "source_archive": CODETALK_PRESET_SOURCE_ARCHIVE,
            "source_archive_sha256": CODETALK_PRESET_SOURCE_ARCHIVE_SHA256,
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
        if any(
            version.project_id == CODETALK_PRESET_PROJECT_ID
            for version in store.list_versions(skill_id=scenario.skill_id)
        ):
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
                session_id=f"preset-seed/codetalks-v2.5/{scenario.scenario_id}",
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
        "preset_release": CODETALK_PRESET_RELEASE,
        "source_archive": CODETALK_PRESET_SOURCE_ARCHIVE,
        "source_archive_sha256": CODETALK_PRESET_SOURCE_ARCHIVE_SHA256,
        "source_tree_sha256": CODETALK_PRESET_SOURCE_TREE_SHA256,
    }


def write_codetalk_v24_source(root: Path) -> None:
    """Copy the pinned v2.4 source bytes into the mutable v2.5 seed area."""

    source_root = CODETALK_PRESET_RESOURCE_ROOT
    _validate_codetalk_preset_source(source_root)
    root = Path(root)
    if root.exists():
        _reject_symlinks(root)
        unexpected = set(_relative_files(root)) - set(CODETALK_PRESET_SOURCE_FILES)
        if unexpected:
            raise ValueError(f"Codetalk preset destination contains unexpected files: {sorted(unexpected)!r}")
    root.mkdir(parents=True, exist_ok=True)
    for relative_path in CODETALK_PRESET_SOURCE_FILES:
        source = source_root / relative_path
        destination = root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    _validate_codetalk_preset_source(root)


def _get_or_create_preset_project(store: SkillStore) -> Any:
    try:
        return store.get_project(CODETALK_PRESET_PROJECT_ID)
    except KeyError:
        return store.create_project(
            project_id=CODETALK_PRESET_PROJECT_ID,
            name="CodeTalk v2.5 Preset Skills",
            pack_id=CODETALK_PRESET_PACK_ID,
        )


def _validate_codetalk_preset_source(root: Path) -> None:
    files = _relative_files(root)
    if files != tuple(sorted(CODETALK_PRESET_SOURCE_FILES)):
        raise ValueError(f"Codetalk preset source inventory mismatch: {files!r}")

    for relative_path in files:
        path = root / relative_path
        if path.suffix not in {".md", ".py", ".json"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Codetalk preset source is not UTF-8: {relative_path}") from exc
        lowered = text.lower()
        if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
            raise ValueError(f"Codetalk preset source contains placeholder content: {relative_path}")

    try:
        manifest = json.loads((root / "workflow-manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Codetalk preset workflow-manifest.json is invalid") from exc
    steps = manifest.get("steps")
    if not isinstance(steps, list):
        raise ValueError("Codetalk preset manifest steps must be a list")
    step_files = tuple(step.get("file") for step in steps if isinstance(step, dict))
    if step_files != CODETALK_PRESET_STEP_FILES:
        raise ValueError(f"Codetalk preset manifest must declare the authoritative nine steps: {step_files!r}")
    for step_file in step_files:
        instruction = (root / step_file).read_text(encoding="utf-8")
        has_boundary = any(boundary in instruction for boundary in ("必须", "不得", "禁止", "只允许"))
        if "## 目标" not in instruction or len(instruction.strip()) < 150 or not has_boundary:
            raise ValueError(f"Codetalk preset step is not substantive: {step_file}")

    if len((root / "SKILL.md").read_text(encoding="utf-8")) < 5000:
        raise ValueError("Codetalk preset SKILL.md is not substantive")
    if len((root / "checklists" / "judge-checklist.md").read_text(encoding="utf-8")) < 500:
        raise ValueError("Codetalk preset Judge checklist is not substantive")
    if len((root / "scripts" / "run_guard.py").read_text(encoding="utf-8")) < 1000:
        raise ValueError("Codetalk preset run guard is not substantive")

    digest = _source_tree_digest(root)
    if digest != CODETALK_PRESET_SOURCE_TREE_SHA256:
        raise ValueError(f"Codetalk preset source digest mismatch: {digest}")


def _relative_files(root: Path) -> tuple[str, ...]:
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"Codetalk preset source directory is missing or unsafe: {root}")
    _reject_symlinks(root)
    return tuple(
        sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
        )
    )


def _reject_symlinks(root: Path) -> None:
    if root.is_symlink() or any(path.is_symlink() for path in root.rglob("*")):
        raise ValueError(f"Codetalk preset source cannot contain symlinks: {root}")


def _source_tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for relative_path in _relative_files(root):
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update((root / relative_path).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
