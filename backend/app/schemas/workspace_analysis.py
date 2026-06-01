"""Pydantic schemas for workspace analysis task plan & scope preview.

These schemas back the new analysis modal introduced by
WORKSPACE_GITNEXUS_ANALYSIS_TASK_REDESIGN.md.  The contract is:

* The user submits an :class:`AnalysisPlan` (analysis objects + focus +
  report templates + optional guidance + LLM limits).
* The backend resolves it into a bounded :class:`ScopePreview` and stores
  both on the shadow task before the analysis pipeline runs.
* The pipeline always uses the plan/preview pair to drive fan-out;
  GitNexus communities are only ever evidence, never the unit count.

If a field is omitted on the wire, the conservative default applies.  All
caps default to "safe for an 8K-output / 192K-context model".  Callers
are free to raise them, but the backend enforces sane upper bounds
(see :func:`AnalysisPlan.normalize`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Plan inputs
# ---------------------------------------------------------------------------


class AnalysisObject(BaseModel):
    """A single user-defined analysis target (one row of the editor)."""

    id: str
    text: str = Field(min_length=1, max_length=2000)
    kind: Literal["topic", "module", "flow", "file", "function", "mixed"] = "topic"
    priority: Literal["high", "medium", "low"] = "medium"
    path_hints: list[str] = Field(default_factory=list, max_length=16)


class FocusOptions(BaseModel):
    """Checkbox grid of focus directions.

    Defaults follow §5.4 of the spec: test/runtime-behaviour first,
    security off by default.
    """

    key_flows: bool = True
    exception_branches: bool = True
    exception_propagation: bool = True
    boundary_values: bool = True
    long_running_flip: bool = True
    state_machine: bool = True
    resource_cleanup: bool = True
    concurrency: bool = True
    observability: bool = True
    sfmea: bool = True
    cpp_implicit_logic: bool = True
    security_risk: bool = False


class ReportSpec(BaseModel):
    """One report template entry on the plan."""

    id: str
    title: str
    enabled: bool = True
    template_id: str
    custom: bool = False
    audience: str | None = None
    questions: list[str] = Field(default_factory=list)
    output_format: str | None = None
    max_sections: int | None = None
    max_length_chars: int | None = None


class LLMLimits(BaseModel):
    """Hard caps applied during scope resolution and analysis fan-out."""

    max_evidence_cards: int = Field(default=48, ge=1, le=256)
    max_files_per_object: int = Field(default=12, ge=1, le=64)
    max_functions_per_object: int = Field(default=30, ge=1, le=128)
    max_communities_per_object: int = Field(default=8, ge=1, le=32)
    max_cards_per_report_section: int = Field(default=12, ge=1, le=64)
    max_output_chars_per_section: int = Field(default=1200, ge=200, le=8000)
    retry_empty_output: int = Field(default=1, ge=0, le=3)
    max_analysis_units: int = Field(default=16, ge=1, le=64)


class AnalysisPlan(BaseModel):
    """Top-level submission object for the analysis modal."""

    version: Literal["workspace-analysis-plan-v1"] = "workspace-analysis-plan-v1"
    analysis_objects: list[AnalysisObject] = Field(default_factory=list)
    focus: FocusOptions = Field(default_factory=FocusOptions)
    reports: list[ReportSpec] = Field(default_factory=list)
    user_guidance: str = ""
    llm_limits: LLMLimits = Field(default_factory=LLMLimits)

    @model_validator(mode="after")
    def _strip_strings(self) -> "AnalysisPlan":
        # Trim text content so blank lines from the textarea don't survive
        for obj in self.analysis_objects:
            obj.text = obj.text.strip()
            obj.path_hints = [hint.strip() for hint in obj.path_hints if hint.strip()]
        self.user_guidance = self.user_guidance.strip()
        return self

    def enabled_reports(self) -> list[ReportSpec]:
        return [r for r in self.reports if r.enabled]


# ---------------------------------------------------------------------------
# Scope preview
# ---------------------------------------------------------------------------


class ScopeCandidate(BaseModel):
    """A single file or symbol resolved as evidence for an analysis object."""

    path: str | None = None
    symbol: str | None = None
    source: Literal["gitnexus", "repo_search", "material", "manual"]
    confidence: Literal["high", "medium", "low"]
    reason: str
    role: Literal["primary", "related", "external"] | None = None


class ResolvedAnalysisObject(BaseModel):
    object_id: str
    text: str
    candidate_files: list[ScopeCandidate] = Field(default_factory=list)
    candidate_symbols: list[ScopeCandidate] = Field(default_factory=list)
    related_communities: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ScopePreview(BaseModel):
    workspace_id: str
    resolved_objects: list[ResolvedAnalysisObject] = Field(default_factory=list)
    estimated_analysis_units: int = 0
    estimated_evidence_cards: int = 0
    warnings: list[str] = Field(default_factory=list)
    gitnexus_available: bool = True


# ---------------------------------------------------------------------------
# Default plan factory
# ---------------------------------------------------------------------------


# Canonical report templates per §5.5 — keep IDs stable; the report
# generator maps these to file names.
DEFAULT_REPORT_TEMPLATES: list[dict] = [
    {
        "id": "project_structure",
        "title": "项目结构初步理解",
        "template_id": "project_structure",
    },
    {
        "id": "module_map",
        "title": "模块地图",
        "template_id": "module_map",
    },
    {
        "id": "source_reading",
        "title": "源码定向阅读记录",
        "template_id": "source_reading",
    },
    {
        "id": "business_flow",
        "title": "关键业务流程分析",
        "template_id": "business_flow",
    },
    {
        "id": "gitnexus_reliability",
        "title": "GitNexus 结果可信度评估",
        "template_id": "gitnexus_reliability",
    },
    {
        "id": "test_design",
        "title": "测试视角代码理解",
        "template_id": "test_design",
    },
]


_DEFAULT_OBJECT_EXAMPLES: list[str] = [
    "示例：核心业务的主流程登录/初始化",
    "示例：错误处理与重试路径",
    "示例：长时间运行的状态机与资源清理",
]


def build_default_plan(
    *,
    has_requirements: bool = False,
    seed_examples: bool = True,
) -> AnalysisPlan:
    """Construct the default plan returned by GET …/analysis/default-plan."""

    reports: list[ReportSpec] = [
        ReportSpec(
            id=t["id"],
            title=t["title"],
            template_id=t["template_id"],
            enabled=True,
            custom=False,
        )
        for t in DEFAULT_REPORT_TEMPLATES
    ]

    if has_requirements:
        # codetalks 愿景下，只要工作空间有活跃的需求/设计材料，需求-设计-代码追踪
        # 就是验证“材料是否真正进入测试场景”的核心报告，应默认启用（而非可选）。
        # 这样有材料时默认生成 7 份报告；无材料时此报告不追加。
        reports.append(
            ReportSpec(
                id="requirements_traceability",
                title="需求-设计-代码追踪",
                template_id="requirements_traceability",
                enabled=True,
                custom=False,
            )
        )

    objects: list[AnalysisObject] = []
    if seed_examples:
        for idx, text in enumerate(_DEFAULT_OBJECT_EXAMPLES, start=1):
            objects.append(
                AnalysisObject(
                    id=f"obj_default_{idx}",
                    text=text,
                    kind="topic",
                    priority="medium",
                )
            )

    return AnalysisPlan(
        analysis_objects=objects,
        focus=FocusOptions(),
        reports=reports,
        user_guidance="",
        llm_limits=LLMLimits(),
    )
