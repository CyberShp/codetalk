"""Shared built-in Workbench skill definitions and prompt resolution."""

from __future__ import annotations

from typing import Any, Iterable


BUILTIN_WORKBENCH_SKILLS: tuple[dict[str, Any], ...] = (
    {
        "id": "source-evidence-first",
        "label": "源码证据优先",
        "source": "codetalk_builtin",
        "prompt_hint": "优先读取工作区源码、GitNexus 和 CGC 产物；所有关键结论必须引用真实文件、符号、行号和可复核摘录。",
    },
    {
        "id": "storage-flow-analysis",
        "label": "存储流程梳理",
        "source": "codetalk_builtin",
        "prompt_hint": "按入口、前置条件、关键状态、正常流程、异常传播、恢复路径、并发关系和外部可观测行为组织分析。",
    },
    {
        "id": "sfmea",
        "label": "SFMEA",
        "source": "codetalk_builtin",
        "prompt_hint": "只记录真实失效模式；每条包含 cause、effect、detection、S/O/D/RPN、具体可执行 mitigation、测试映射和源码证据。",
    },
    {
        "id": "black-box-test-design",
        "label": "黑盒测试设计",
        "source": "codetalk_builtin",
        "prompt_hint": "仅使用外部输入、公开命令和可观测结果；覆盖正常、异常、边界、恢复、并发、资源、性能、长稳态和故障传播。",
    },
    {
        "id": "test-strategy-planning",
        "label": "测试策略与计划",
        "source": "codetalk_builtin",
        "prompt_hint": "输出范围、风险优先级、环境与资源、准入准出、里程碑；性能计划包含预热、重复采样、P50/P95 和同环境基线。",
    },
    {
        "id": "coverage-gap-analysis",
        "label": "覆盖率与缺口分析",
        "source": "codetalk_builtin",
        "prompt_hint": "结合覆盖率、源码入口和真实测试目录，标出未覆盖路径、黑盒/灰盒边界和补充建议。",
    },
    {
        "id": "test-execution-orchestration",
        "label": "测试执行编排",
        "source": "codetalk_builtin",
        "prompt_hint": "生成环境、数据、批次、并发、长跑、观测、失败处置和复跑规则明确的执行矩阵。",
    },
    {
        "id": "defect-triage-regression",
        "label": "缺陷分诊与回归",
        "source": "codetalk_builtin",
        "prompt_hint": "输出缺陷等级、复现证据、影响面、回归范围、阻塞判断和仍需补充的证据。",
    },
    {
        "id": "performance-reliability-testing",
        "label": "性能与可靠性测试",
        "source": "codetalk_builtin",
        "prompt_hint": "覆盖基线、压力、故障注入、soak、资源耗尽与翻转；明确负载、阈值来源、预热、样本数、P50/P95 和诊断数据。",
    },
    {
        "id": "artifact-contract",
        "label": "产物契约",
        "source": "codetalk_builtin",
        "prompt_hint": "必须写入全部 required_artifacts 并满足 schema；终端回答不能替代文件，脑图必须包含有效的 Mermaid mindmap 代码块。",
    },
)


def resolve_workbench_skill_instructions(
    skill_ids: Iterable[Any],
    explicit_instructions: Iterable[Any] = (),
) -> list[dict[str, Any]]:
    """Resolve selected IDs into executable instructions, preserving overrides."""

    catalog = {str(item["id"]): dict(item) for item in BUILTIN_WORKBENCH_SKILLS}
    explicit = {
        str(item.get("id") or ""): dict(item)
        for item in explicit_instructions
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    selected = [str(value or "").strip() for value in skill_ids]
    selected = [value for value in selected if value]
    explicit_order = [
        str(item.get("id") or "").strip()
        for item in explicit_instructions
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    ordered_ids = [value for value in explicit_order if value in selected]
    ordered_ids.extend(value for value in selected if value not in ordered_ids)
    resolved: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in ordered_ids:
        skill_id = str(value or "").strip()
        if not skill_id or skill_id in seen:
            continue
        seen.add(skill_id)
        base = catalog.get(
            skill_id,
            {"id": skill_id, "label": skill_id, "source": "workflow"},
        )
        override = explicit.get(skill_id, {})
        merged = {**base, **override, "id": skill_id}
        body = str(merged.get("body") or "").strip()
        prompt_hint = str(merged.get("prompt_hint") or "").strip()
        if body and not prompt_hint:
            merged["prompt_hint"] = body
        elif prompt_hint and not body:
            merged["body"] = prompt_hint
        resolved.append(merged)
    return resolved
