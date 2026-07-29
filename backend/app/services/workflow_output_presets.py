"""Optional output content presets for canvas-authored workflows.

These presets are product defaults inspired by the external Codetalks skill
pack.  They are hints for selected outputs, not hidden workflow stages or
mandatory gates.
"""

from __future__ import annotations

from typing import Any


OUTPUT_CONTENT_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "id": "dev_to_test_flow_doc",
        "label": "开发给测试讲代码",
        "roles": ("flow_doc", "source_evidence", "report"),
        "description": "把源码实现翻译成测试人员可理解的流程讲解。",
        "prompt_hint": (
            "按做什么、谁触发、正常流程、分支进入、状态变化、资源、超时重试、"
            "并发窗口、异常传播、风险、黑盒怎么测、源码追溯组织内容。"
        ),
        "headings": (
            "这段代码/流程是干什么的",
            "外部入口和触发条件",
            "正常流程（外部动作 -> 内部推进 -> 外部结果）",
            "分支进入条件表",
            "状态变化",
            "资源申请、占用、释放和耗尽",
            "超时、重试、取消和恢复",
            "并发和关键时序窗口",
            "异常传播和潜伏故障",
            "风险点清单",
            "黑盒测试如何构造",
            "源码追溯和未决项",
        ),
        "source": "codetalk_builtin",
    },
    {
        "id": "branch_state_resource_exception_map",
        "label": "分支状态资源异常传播图谱",
        "roles": ("storage_flow", "flow_doc", "report"),
        "description": "梳理分支、状态、资源生命周期和异常传播链。",
        "prompt_hint": (
            "用外部可构造条件 -> 内部判断/状态机制 -> 外部可观察结果的映射组织；"
            "补充状态转换、资源申请释放/耗尽、异常源头和传播链。"
        ),
        "headings": (
            "入口与分支总览",
            "外部可构造条件映射",
            "状态转换与不变量",
            "资源生命周期与耗尽边界",
            "异常源头与传播链",
            "恢复路径与遗留风险",
        ),
        "source": "codetalk_builtin",
    },
    {
        "id": "sfmea_risk_causal_chain",
        "label": "风险点与 SFMEA 因果链",
        "roles": ("sfmea", "report"),
        "description": "从实现事实演绎 SFMEA 风险、传播和验证方法。",
        "prompt_hint": (
            "从分支、状态、资源、不变量、协议事务、并发、超时恢复等实现事实演绎风险；"
            "每个高风险项说明机制、传播路径、用户影响和黑盒验证方法。"
        ),
        "headings": (
            "风险来源与证据",
            "Failure Mode",
            "Cause / Effect / Detection",
            "S/O/D/RPN 评分依据",
            "Mitigation 与验证方法",
            "关联黑盒场景",
        ),
        "source": "codetalk_builtin",
    },
    {
        "id": "blackbox_scenario_flow_case_pack",
        "label": "黑盒场景-流程-用例包",
        "roles": ("black_box", "test_cases", "report"),
        "description": "生成可执行黑盒场景、流程和用例。",
        "prompt_hint": (
            "强调测试人员可执行的前置条件、外部操作、故障注入、观察点、独立 Oracle、"
            "后续验证和清理复原；不得要求修改内部代码或调用内部函数。"
        ),
        "headings": (
            "黑盒测试场景",
            "黑盒测试流程",
            "黑盒测试用例",
            "前置条件与数据准备",
            "外部操作与故障注入",
            "预期结果与独立 Oracle",
            "观察点、诊断线索与清理复原",
        ),
        "source": "codetalk_builtin",
    },
    {
        "id": "source_evidence_trace_summary",
        "label": "证据消费与源码追溯摘要",
        "roles": ("source_evidence", "report"),
        "description": "记录输入材料消费、源码追溯和未决证据缺口。",
        "prompt_hint": (
            "记录材料是否 parsed/blocked/out_of_scope、读取范围、覆盖率或 MR 等证据如何驱动场景，"
            "以及源码文件/函数/设计协议/日志/覆盖率缺口和未决项。"
        ),
        "headings": (
            "输入材料消费记录",
            "源码读取范围",
            "证据到结论的映射",
            "覆盖率/MR/日志等辅助证据",
            "阻塞、范围外和未决项",
        ),
        "source": "codetalk_builtin",
    },
    {
        "id": "coverage_audit_limits",
        "label": "覆盖审计与分析限制",
        "roles": ("source_evidence", "report", "independent_review"),
        "description": "在正式报告尾部说明覆盖结论、阻塞项和分析限制。",
        "prompt_hint": (
            "用分析项、Outcome、证据/工件、关联场景/用例、Missing Work 表达覆盖结论；"
            "说明阻塞、未验证、合并覆盖和截断范围。"
        ),
        "headings": (
            "覆盖审计结论",
            "分析项与 Outcome",
            "证据/工件与关联用例",
            "Missing Work",
            "阻塞、未验证与分析限制",
        ),
        "source": "codetalk_builtin",
    },
)


def output_content_preset_options() -> list[dict[str, str]]:
    return [
        {
            "value": str(preset["id"]),
            "label": str(preset["label"]),
        }
        for preset in OUTPUT_CONTENT_PRESETS
    ]


def selected_output_content_presets(values: Any) -> list[dict[str, Any]]:
    requested = [
        str(value).strip()
        for value in (values if isinstance(values, list | tuple | set) else [])
        if str(value).strip()
    ]
    if not requested:
        return []
    by_id = {str(preset["id"]): preset for preset in OUTPUT_CONTENT_PRESETS}
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for preset_id in requested:
        preset = by_id.get(preset_id)
        if preset is None or preset_id in seen:
            continue
        seen.add(preset_id)
        selected.append({
            "id": preset["id"],
            "label": preset["label"],
            "roles": list(preset["roles"]),
            "description": preset["description"],
            "prompt_hint": preset["prompt_hint"],
            "headings": list(preset["headings"]),
            "source": preset["source"],
        })
    return selected
