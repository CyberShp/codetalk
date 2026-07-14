from __future__ import annotations

import asyncio
import hashlib
import json
import time

import pytest

from app.llm.base import LLMResponse
from app.services.ai_thread_artifacts import _validate_schema
from app.services.ai_staged_execution import (
    _stage_prompt,
    _stage_format_rules,
    build_source_analysis_context,
    build_source_evidence_pack,
    build_staged_execution_plan,
    execute_staged_builtin_plan,
    materialize_source_evidence_pack,
)
from app.services.workflow_presets import EVIDENCE_CARDS_SCHEMA


class _StageLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.max_tokens_by_stage: dict[str, list[int]] = {}
        self.calls_by_stage: dict[str, int] = {}

    async def complete(self, messages, max_tokens=4096, temperature=0.2):
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        stage = next(
            line.split(":", 1)[1].strip()
            for line in prompt.splitlines()
            if line.startswith("STAGE_ID:")
        )
        self.max_tokens_by_stage.setdefault(stage, []).append(max_tokens)
        self.calls_by_stage[stage] = self.calls_by_stage.get(stage, 0) + 1
        if stage == "source_analysis":
            content = "# 代码证据\n\n- `lib/iscsi/iscsi.c:100` login version check\n"
        elif stage == "business_flow":
            content = "# 业务流程\n\n## 外部触发\nlogin PDU\n## 流程步骤\n1. negotiate\n## 异常分支\ntimeout\n## 观测点\nlog\n"
        elif stage == "sfmea":
            content = json.dumps([
                {
                    "failure_mode": "login timeout",
                    "cause": "peer silent",
                    "effect": "session unavailable",
                    "detection": "timeout log",
                    "severity": 7,
                    "occurrence": 3,
                    "detection_score": 2,
                    "rpn": 42,
                    "score_explanation": "service unavailable",
                    "mitigation": "bounded retry",
                    "source_evidence": "lib/iscsi/iscsi.c:1262",
                    "test_mapping": "test/iscsi_tgt/login.sh",
                }
            ])
        elif stage == "black_box_cases":
            dimensions = [
                "normal_path",
                "invalid_input",
                "resource_pressure",
                "timeout",
                "reconnect",
                "concurrency",
                "recovery",
                "performance",
            ]
            content = json.dumps([
                {
                    "case_id": f"TC-{index:02d}",
                    "test_dimension": dimension,
                    "scenario_name": dimension,
                    "preconditions": "target running",
                    "steps": ["connect initiator"],
                    "expected_result": "observable result",
                    "observability": ["log"],
                    "failure_diagnostics": ["session state"],
                    "mapped_test_dir": "test/iscsi_tgt",
                    "source_or_test_evidence": ["lib/iscsi/iscsi.c:1262"],
                }
                for index, dimension in enumerate(dimensions, 1)
            ])
        else:
            content = "# 测试设计\n\n## 目标\niSCSI login\n## 输入\nPDU\n## 用例设计\n见黑盒用例\n## 覆盖矩阵\n八维\n## 剩余风险\n需实机\n"
        return LLMResponse(content=content, model="stage-test", usage={}, truncated=False)


def _contract() -> dict:
    required_outputs = [
        "business_flow.md",
        "sfmea.json",
        "black_box_cases.json",
        "test_design.md",
    ]
    required_fields = {
        "sfmea.json": ["failure_mode", "cause"],
        "black_box_cases.json": ["case_id", "test_dimension"],
    }
    return {
        "target": "iSCSI login",
        "required_outputs": required_outputs,
        "artifact_contract": {
            artifact: {
                "artifact": artifact,
                "schema": (
                    {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": required_fields.get(artifact, []),
                        },
                    }
                    if artifact.endswith(".json")
                    else None
                ),
            }
            for artifact in required_outputs
        },
    }


def _verified_source_context(*, excerpt_chars: int = 2200) -> dict:
    files = []
    for index, path in enumerate(
        [
            "lib/iscsi/iscsi.c",
            "test/iscsi_tgt/login.sh",
            "lib/iscsi/conn.c",
            "test/iscsi_tgt/common.sh",
            "include/spdk/iscsi_spec.h",
            "lib/iscsi/tgt_node.c",
            "doc/iscsi.md",
        ]
    ):
        excerpt = (
            f"int spdk_iscsi_login_{index}(void) {{\n"
            f"    return iscsi_login_step_{index};\n"
            "}\n"
            + ("validated evidence line\n" * 160)
        )[:excerpt_chars]
        files.append(
            {
                "file_path": path,
                "start_line": 100 + index,
                "end_line": 120 + index,
                "excerpt": excerpt,
                "symbols": [f"spdk_iscsi_login_{index}"],
                "matched_terms": ["iscsi", "login"],
                "sha256": hashlib.sha256(excerpt.encode()).hexdigest(),
                "status": "validated_source_file",
            }
        )
    return {
        "contract_version": 1,
        "repo_path": "/repo/spdk",
        "goal": "分析 iSCSI login",
        "analysis_targets": [{"value": "iSCSI login"}],
        "user_inputs": [{"input_id": "analysis_target", "value": "iSCSI login"}],
        "input_materials": {
            "materials": [
                {
                    "input_id": "requirements",
                    "sha256": "input-sha",
                    "text_preview": "登录、认证、异常恢复与并发约束",
                }
            ]
        },
        "source_context": {
            "status": "ready",
            "repo_revision": "abc123",
            "files": files,
        },
        "mcp": {
            "gitnexus_summary": "login call graph",
            "cgc_summary": "authentication branch",
        },
        "test_activity_guidance": {
            "quality_gates": {"large": "must not enter source analysis"},
            "black_box_boundary": {"large": "must not enter source analysis"},
        },
        "quality_retry": {"feedback": {"large": "must not enter source analysis"}},
        "unrelated_history": "x" * 100000,
    }
def test_plan_compiles_dependency_order_and_declared_outputs():
    plan = build_staged_execution_plan(
        contract=_contract(),
        original_user_request="第一行：完整 iSCSI login 测试设计\n第二行：必须保留",
    )

    assert plan["version"] == "ai-staged-execution-v1"
    assert [stage["id"] for stage in plan["stages"]] == [
        "source_analysis",
        "business_flow",
        "sfmea",
        "black_box_cases",
        "test_design",
    ]
    assert plan["stages"][2]["depends_on"] == ["source_analysis", "business_flow"]
    assert plan["stages"][2]["artifact"] == "sfmea.json"
    assert "第一行" in plan["original_user_request"]
    assert "第二行" in plan["original_user_request"]
    source_stage = plan["stages"][0]
    assert source_stage["max_tokens"] == 1600
    assert source_stage["output_limits"]["max_chinese_characters"] == 1200
    assert source_stage["output_limits"]["max_evidence_anchors"] == 12


def test_source_analysis_context_keeps_only_bounded_verified_inputs():
    compact = build_source_analysis_context(
        plan={
            "original_user_request": "分析 iSCSI login",
            "target": "iSCSI login",
        },
        staged_context=_verified_source_context(),
        max_files=6,
        excerpt_chars=1200,
        max_evidence_anchors=12,
    )

    assert compact["analysis_target"] == "分析 iSCSI login"
    assert compact["repo_revision"] == "abc123"
    assert len(compact["files"]) == 6
    assert all(len(item["excerpt"]) <= 1200 for item in compact["files"])
    assert compact["files"][0]["classification"] == "source"
    assert compact["files"][1]["classification"] == "test"
    serialized = json.dumps(compact, ensure_ascii=False)
    assert "quality_gates" not in serialized
    assert "black_box_boundary" not in serialized
    assert "quality_retry" not in serialized
    assert "unrelated_history" not in serialized
    assert len(serialized) < 12000


def test_source_analysis_context_preserves_validated_custom_test_classification():
    staged_context = _verified_source_context()
    staged_context["source_context"]["files"][0]["file_path"] = "qa/login_case.sh"
    staged_context["source_context"]["files"][0]["classification"] = "test"

    compact = build_source_analysis_context(
        plan={"original_user_request": "分析 login"},
        staged_context=staged_context,
    )

    assert compact["files"][0]["classification"] == "test"


def test_source_evidence_pack_materializes_three_verified_artifacts(tmp_path):
    compact = build_source_analysis_context(
        plan={"original_user_request": "分析 iSCSI login", "target": "iSCSI login"},
        staged_context=_verified_source_context(),
        max_files=6,
        excerpt_chars=1200,
        max_evidence_anchors=12,
    )
    pack = build_source_evidence_pack(compact)

    materialized = materialize_source_evidence_pack(pack, tmp_path)

    assert pack["version"] == "source-evidence-pack-v1"
    assert pack["quality_gate"]["status"] == "passed"
    assert materialized == {
        "source_analysis": tmp_path / "source_analysis.md",
        "source_scope": tmp_path / "source_scope.json",
        "evidence_cards": tmp_path / "evidence_cards.json",
    }
    cards = json.loads((tmp_path / "evidence_cards.json").read_text(encoding="utf-8"))
    assert len(cards) == 6
    assert all(card["sha256"] for card in cards)
    assert any(card["classification"] == "test" for card in cards)
    assert "spdk_iscsi_login_0" in (tmp_path / "source_analysis.md").read_text(
        encoding="utf-8"
    )


def test_symbol_free_verified_evidence_remains_deliverable_but_not_cacheable():
    compact = build_source_analysis_context(
        plan={"original_user_request": "分析 JSON 配置"},
        staged_context={
            "source_context": {
                "repo_path": "/repo/config",
                "repo_revision": "config123",
                "files": [
                    {
                        "file_path": "config/login.json",
                        "classification": "source",
                        "start_line": 1,
                        "end_line": 4,
                        "excerpt": '{"login": true}',
                        "symbols": [],
                        "matched_terms": ["login"],
                        "sha256": "a" * 64,
                        "status": "validated_source_file",
                    }
                ],
            }
        },
    )
    pack = build_source_evidence_pack(compact)

    assert pack["quality_gate"]["status"] == "limited"
    assert pack["quality_gate"]["missing_symbol_evidence_ids"] == ["SRC-01"]
    assert _validate_schema(pack["evidence_cards"], EVIDENCE_CARDS_SCHEMA) == []


def test_stage_prompt_injects_only_declared_dependency_artifacts(tmp_path):
    source = tmp_path / "source_analysis.md"
    flow = tmp_path / "business_flow.md"
    unrelated = tmp_path / "unrelated.md"
    source.write_text("source evidence", encoding="utf-8")
    flow.write_text("flow evidence", encoding="utf-8")
    unrelated.write_text("must not leak", encoding="utf-8")

    prompt = _stage_prompt(
        plan={"original_user_request": "analyze"},
        stage={
            "id": "sfmea",
            "artifact": "sfmea.json",
            "depends_on": ["source_analysis", "business_flow"],
        },
        context_prompt="workspace context",
        completed={
            "source_analysis": source,
            "business_flow": flow,
            "unrelated": unrelated,
        },
    )

    assert "source evidence" in prompt
    assert "flow evidence" in prompt
    assert "must not leak" not in prompt


def test_source_scope_and_evidence_precede_flow_and_feed_it():
    contract = _contract()
    contract["required_outputs"] = [
        "source_scope.json",
        "evidence_cards.json",
        "business_flow.md",
        "sfmea.json",
    ]
    contract["artifact_contract"].update({
        "source_scope.json": {"artifact": "source_scope.json", "schema": {"type": "object"}},
        "evidence_cards.json": {"artifact": "evidence_cards.json", "schema": {"type": "array", "minItems": 1}},
        "business_flow.md": {"artifact": "business_flow.md"},
    })

    plan = build_staged_execution_plan(
        contract=contract,
        original_user_request="先读源码，再输出范围、证据、流程和 SFMEA",
    )

    assert [stage["id"] for stage in plan["stages"]] == [
        "source_analysis",
        "source_scope",
        "evidence_cards",
        "business_flow",
        "sfmea",
    ]
    assert plan["stages"][3]["depends_on"] == ["source_analysis"]
    sfmea_stage = plan["stages"][4]
    assert sfmea_stage["output_limits"] == {
        "max_items": 10,
        "max_field_characters": 180,
    }


def test_black_box_stage_is_bounded_around_required_dimensions():
    plan = build_staged_execution_plan(
        contract=_contract(),
        original_user_request="生成覆盖八维的黑盒测试",
    )

    stage = next(item for item in plan["stages"] if item["id"] == "black_box_cases")
    assert stage["output_limits"] == {
        "max_items": 12,
        "max_field_characters": 180,
    }


def test_plan_does_not_collapse_multiple_source_facing_deliverables():
    contract = _contract()
    contract["required_outputs"] = [
        "project_structure.md",
        "module_map.md",
        "tester_code_understanding.md",
    ]
    contract["artifact_contract"] = {
        name: {"artifact": name} for name in contract["required_outputs"]
    }

    plan = build_staged_execution_plan(
        contract=contract,
        original_user_request="输出三个独立源码理解文件",
    )

    assert [stage["artifact"] for stage in plan["stages"] if not stage["support"]] == [
        "project_structure.md",
        "module_map.md",
        "tester_code_understanding.md",
    ]


def test_plan_uses_canonical_dependency_order_for_unordered_user_outputs():
    contract = _contract()
    contract["required_outputs"] = [
        "sfmea.json",
        "black_box_cases.json",
        "test_strategy.md",
        "test_design.md",
        "business_flow.md",
    ]

    plan = build_staged_execution_plan(
        contract=contract,
        original_user_request="先写 SFMEA，最后才提到流程，但执行仍须按依赖拓扑运行",
    )

    assert [stage["id"] for stage in plan["stages"]] == [
        "source_analysis",
        "business_flow",
        "sfmea",
        "black_box_cases",
        "test_strategy",
        "test_design",
    ]
    assert plan["stages"][2]["depends_on"] == ["source_analysis", "business_flow"]


def test_plan_keeps_multiple_artifacts_owned_by_the_same_stage():
    contract = _contract()
    contract["required_outputs"] = ["business_flow.md", "black_box_cases.json", "black_box_cases.md"]
    contract["artifact_contract"]["black_box_cases.md"] = {"artifact": "black_box_cases.md"}

    plan = build_staged_execution_plan(
        contract=contract,
        original_user_request="同时交付 JSON 和 Markdown 黑盒用例",
    )

    black_box_stages = [stage for stage in plan["stages"] if stage["id"].startswith("black_box_cases")]
    assert [stage["artifact"] for stage in black_box_stages] == [
        "black_box_cases.json",
        "black_box_cases.md",
    ]


def test_plan_expands_transitive_support_dependencies():
    contract = _contract()
    contract["required_outputs"] = ["risk_review.md"]
    contract["artifact_contract"] = {"risk_review.md": {"artifact": "risk_review.md"}}

    plan = build_staged_execution_plan(
        contract=contract,
        original_user_request="只点名风险复核也必须先完成流程和 SFMEA",
    )

    assert [stage["id"] for stage in plan["stages"]] == [
        "source_analysis",
        "business_flow",
        "sfmea",
        "risk_review",
    ]


@pytest.mark.asyncio
async def test_executor_writes_each_stage_and_preserves_original_request(tmp_path):
    llm = _StageLLM()
    original = "第一行：完整 iSCSI login 测试设计\n第二行：必须保留全部输入"
    plan = build_staged_execution_plan(
        contract=_contract(),
        original_user_request=original,
    )
    progress: list[dict] = []

    result = await execute_staged_builtin_plan(
        llm=llm,
        plan=plan,
        artifact_dir=tmp_path,
        context_prompt="SOURCE_CONTEXT: lib/iscsi/iscsi.c",
        source_analysis_context=_verified_source_context(),
        on_progress=progress.append,
        max_tokens=4096,
    )

    assert result["status"] == "completed"
    assert result["completed_stages"] == 5
    assert (tmp_path / "staged_execution_plan.json").exists()
    assert (tmp_path / "stages" / "source_analysis" / "stage_result.json").exists()
    for artifact in _contract()["required_outputs"]:
        assert (tmp_path / artifact).is_file()
        assert (tmp_path / artifact).stat().st_size > 0
    assert all(original in prompt for prompt in llm.prompts)
    source_prompt = next(prompt for prompt in llm.prompts if "STAGE_ID: source_analysis" in prompt)
    assert source_prompt.count(original) == 1
    assert "quality_gates" not in source_prompt
    assert "black_box_boundary" not in source_prompt
    assert progress[-1]["status"] == "completed"
    sfmea_prompt = next(prompt for prompt in llm.prompts if "STAGE_ID: sfmea" in prompt)
    assert "business_flow.md" in sfmea_prompt
    assert "lib/iscsi/iscsi.c:100" in sfmea_prompt
    assert "CURRENT_STAGE_ONLY" in sfmea_prompt
    assert "不要在当前响应中生成其他阶段" in sfmea_prompt
    flow_prompt = next(
        prompt for prompt in llm.prompts if "STAGE_ID: business_flow" in prompt
    )
    assert "## 外部触发" in flow_prompt
    assert "## 流程步骤" in flow_prompt
    assert "## 异常分支" in flow_prompt
    assert "## 观测点" in flow_prompt
    assert "至少引用一个真实源码路径和一个真实测试路径" in flow_prompt


def test_evidence_stage_requires_file_local_verbatim_symbols():
    rules = _stage_format_rules("evidence_cards", "evidence_cards.json")

    assert "每个 symbol 必须逐字出现在对应 file_path" in " ".join(rules)


def test_sfmea_stage_requires_executable_verification_in_mitigation():
    rules = _stage_format_rules("sfmea", "sfmea.json")

    text = " ".join(rules)
    assert "具体整改" in text
    assert "可执行的测试或监控验证动作" in text


@pytest.mark.asyncio
async def test_source_analysis_degrades_without_full_retry_while_json_stage_repairs(tmp_path):
    class FlakyLLM(_StageLLM):
        async def complete(self, messages, max_tokens=4096, temperature=0.2):
            prompt = messages[-1]["content"]
            stage = next(
                line.split(":", 1)[1].strip()
                for line in prompt.splitlines()
                if line.startswith("STAGE_ID:")
            )
            attempts = self.calls_by_stage.get(stage, 0)
            if stage == "source_analysis" and attempts == 0:
                self.calls_by_stage[stage] = 1
                raise RuntimeError("temporary provider unavailable")
            if stage == "sfmea" and attempts == 0:
                self.calls_by_stage[stage] = 1
                return LLMResponse(content="not-json", model="stage-test", usage={}, truncated=False)
            return await super().complete(messages, max_tokens=max_tokens, temperature=temperature)

    llm = FlakyLLM()
    plan = build_staged_execution_plan(contract=_contract(), original_user_request="retry")

    result = await execute_staged_builtin_plan(
        llm=llm,
        plan=plan,
        artifact_dir=tmp_path,
        context_prompt="SOURCE_CONTEXT",
        source_analysis_context=_verified_source_context(),
    )

    assert result["status"] == "completed"
    assert llm.calls_by_stage["source_analysis"] == 1
    assert llm.calls_by_stage["sfmea"] == 2
    source_result = json.loads(
        (tmp_path / "stages" / "source_analysis" / "stage_result.json").read_text(
            encoding="utf-8"
        )
    )
    assert source_result["attempt_count"] == 1
    assert source_result["full_retry_performed"] is False
    assert source_result["degraded"] is True
    assert source_result["degradation_reason"] == "temporary provider unavailable"
    assert source_result["finish_reason"] == "transport_error"
    sfmea_result = json.loads(
        (tmp_path / "stages" / "sfmea" / "stage_result.json").read_text(encoding="utf-8")
    )
    assert sfmea_result["attempts"] == 2


@pytest.mark.asyncio
async def test_source_analysis_is_bounded_and_truncated_attempts_are_diagnosable(tmp_path):
    class TruncatedLLM:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.max_tokens: list[int] = []

        async def complete(self, messages, max_tokens=4096, temperature=0.2):
            self.prompts.append(messages[-1]["content"])
            self.max_tokens.append(max_tokens)
            return LLMResponse(
                content="# 未完成的源码分析\n\n" + ("证据条目\n" * 100),
                model="truncated-test",
                usage={},
                truncated=True,
            )

    llm = TruncatedLLM()
    contract = _contract()
    contract["required_outputs"] = ["source_scope.json", "evidence_cards.json"]
    contract["artifact_contract"] = {
        "source_scope.json": {"artifact": "source_scope.json", "schema": {"type": "object"}},
        "evidence_cards.json": {"artifact": "evidence_cards.json", "schema": {"type": "array"}},
    }
    plan = build_staged_execution_plan(
        contract=contract,
        original_user_request="分析 iSCSI login",
    )

    result = await execute_staged_builtin_plan(
        llm=llm,
        plan=plan,
        artifact_dir=tmp_path,
        context_prompt="x" * 200000,
        source_analysis_context=_verified_source_context(),
        max_tokens=6000,
    )

    assert result["status"] == "completed"
    assert llm.max_tokens[0] == 1600
    assert llm.max_tokens.count(1600) == 1
    assert "最多 12 个证据锚点" in llm.prompts[0]
    assert "1200 个中文字符" in llm.prompts[0]
    assert "x" * 5000 not in llm.prompts[0]
    stage_dir = tmp_path / "stages" / "source_analysis"
    assert (stage_dir / "raw_output_attempt_1.txt").read_text(encoding="utf-8").startswith(
        "# 未完成的源码分析"
    )
    assert not (stage_dir / "raw_output_attempt_2.txt").exists()
    stage_result = json.loads((stage_dir / "stage_result.json").read_text(encoding="utf-8"))
    assert stage_result["attempt_count"] == 1
    assert stage_result["prompt_characters_before_compaction"] >= 200000
    assert stage_result["prompt_characters"] < 15000
    assert stage_result["prompt_estimated_tokens"] < 4000
    assert stage_result["finish_reason"] == "length"
    assert stage_result["full_retry_performed"] is False
    assert stage_result["degraded"] is True


@pytest.mark.asyncio
async def test_source_analysis_enhancement_is_trimmed_at_markdown_boundary(tmp_path):
    class LongLLM:
        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            return LLMResponse(
                content="\n\n".join(
                    f"### 排序 {index}\n\n- `SRC-01` 完整的证据归纳段落。"
                    for index in range(1, 80)
                ),
                model="long-test",
                usage={"completion_tokens": 1500},
                finish_reason="stop",
            )

    contract = _contract()
    contract["required_outputs"] = ["source_scope.json", "evidence_cards.json"]
    contract["artifact_contract"] = {
        "source_scope.json": {"artifact": "source_scope.json", "schema": {"type": "object"}},
        "evidence_cards.json": {"artifact": "evidence_cards.json", "schema": {"type": "array"}},
    }
    plan = build_staged_execution_plan(contract=contract, original_user_request="bounded")

    await execute_staged_builtin_plan(
        llm=LongLLM(),
        plan=plan,
        artifact_dir=tmp_path,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
        source_analysis_limits={"max_chinese_characters": 300},
    )

    report = (tmp_path / "source_analysis.md").read_text(encoding="utf-8")
    enhancement = report.split("## 模型排序、归纳与缺口标记", 1)[1]
    assert "模型增强内容已按阶段预算截断" in enhancement
    assert enhancement.rstrip().endswith("确定性证据不受影响。")
    assert len(enhancement) < 500


@pytest.mark.asyncio
async def test_source_analysis_uses_small_repair_only_for_format_error(tmp_path):
    class RepairLLM:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.max_tokens: list[int] = []

        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            prompt = messages[-1]["content"]
            self.prompts.append(prompt)
            self.max_tokens.append(max_tokens)
            if len(self.prompts) == 1:
                return LLMResponse(
                    content="```markdown\n- `SRC-01` 未闭合",
                    model="repair-test",
                    usage={"completion_tokens": 20},
                    finish_reason="stop",
                )
            return LLMResponse(
                content="- `SRC-01` 与目标直接相关；未验证内容列为缺口。",
                model="repair-test",
                usage={"completion_tokens": 30},
                finish_reason="stop",
            )

    llm = RepairLLM()
    contract = _contract()
    contract["required_outputs"] = ["source_scope.json", "evidence_cards.json"]
    contract["artifact_contract"] = {
        "source_scope.json": {"artifact": "source_scope.json", "schema": {"type": "object"}},
        "evidence_cards.json": {"artifact": "evidence_cards.json", "schema": {"type": "array"}},
    }
    plan = build_staged_execution_plan(contract=contract, original_user_request="repair")

    await execute_staged_builtin_plan(
        llm=llm,
        plan=plan,
        artifact_dir=tmp_path,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
    )

    assert len(llm.prompts) == 2
    assert llm.max_tokens == [1600, 500]
    assert "SOURCE_ANALYSIS_CONTEXT" in llm.prompts[0]
    assert "SOURCE_ANALYSIS_CONTEXT" not in llm.prompts[1]
    assert "未闭合 Markdown 代码围栏" in llm.prompts[1]
    stage_result = json.loads(
        (tmp_path / "stages" / "source_analysis" / "stage_result.json").read_text()
    )
    assert stage_result["attempt_count"] == 1
    assert stage_result["repair_attempt_count"] == 1
    assert stage_result["full_retry_performed"] is False
    assert stage_result["degraded"] is False
    assert stage_result["output_tokens"] == 50
    report = (tmp_path / "source_analysis.md").read_text(encoding="utf-8")
    assert "未验证内容列为缺口" in report
    assert "```markdown" not in report


@pytest.mark.asyncio
async def test_source_analysis_rejects_unverified_model_paths_without_repair(tmp_path):
    class HallucinatingLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            self.calls += 1
            return LLMResponse(
                content=(
                    "- `lib/invented.c:99` 中 `fake_login()` 负责恢复。\n"
                    "- `SRC-99` 支持该结论。"
                ),
                model="hallucination-test",
                usage={"completion_tokens": 30},
                finish_reason="stop",
            )

    llm = HallucinatingLLM()
    contract = _contract()
    contract["required_outputs"] = ["source_scope.json", "evidence_cards.json"]
    contract["artifact_contract"] = {
        "source_scope.json": {"artifact": "source_scope.json", "schema": {"type": "object"}},
        "evidence_cards.json": {"artifact": "evidence_cards.json", "schema": {"type": "array"}},
    }
    plan = build_staged_execution_plan(contract=contract, original_user_request="grounding")

    await execute_staged_builtin_plan(
        llm=llm,
        plan=plan,
        artifact_dir=tmp_path,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
    )

    result = json.loads(
        (tmp_path / "stages" / "source_analysis" / "stage_result.json").read_text()
    )
    assert llm.calls == 1
    assert result["repair_attempt_count"] == 0
    assert result["degraded"] is True
    assert result["finish_reason"] == "grounding_rejected"
    report = (tmp_path / "source_analysis.md").read_text(encoding="utf-8")
    assert "invented.c" not in report
    assert "fake_login" not in report


@pytest.mark.asyncio
async def test_source_analysis_skips_provider_after_total_budget_is_spent(
    tmp_path,
    monkeypatch,
):
    from app.services import ai_staged_execution as staged_module

    original = staged_module.build_source_analysis_context

    def slow_context(**kwargs):
        time.sleep(0.06)
        return original(**kwargs)

    class CountingLLM(_StageLLM):
        pass

    monkeypatch.setattr(staged_module, "build_source_analysis_context", slow_context)
    llm = CountingLLM()
    contract = _contract()
    contract["required_outputs"] = ["source_scope.json", "evidence_cards.json"]
    contract["artifact_contract"] = {
        "source_scope.json": {"artifact": "source_scope.json", "schema": {"type": "object"}},
        "evidence_cards.json": {"artifact": "evidence_cards.json", "schema": {"type": "array"}},
    }
    plan = build_staged_execution_plan(contract=contract, original_user_request="budget")

    await execute_staged_builtin_plan(
        llm=llm,
        plan=plan,
        artifact_dir=tmp_path,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
        source_analysis_limits={
            "context_timeout_seconds": 1,
            "total_timeout_seconds": 0.05,
        },
    )

    result = json.loads(
        (tmp_path / "stages" / "source_analysis" / "stage_result.json").read_text()
    )
    assert llm.calls_by_stage.get("source_analysis", 0) == 0
    assert result["attempt_count"] == 0
    assert result["degraded"] is True
    assert result["degradation_reason"] == "total_budget_exceeded_during_context"
    assert result["finish_reason"] == "budget_exceeded"
    assert (tmp_path / "source_analysis.md").is_file()


@pytest.mark.asyncio
async def test_source_analysis_timeout_cancels_provider_and_continues_with_evidence(tmp_path):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingLLM:
        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    contract = _contract()
    contract["required_outputs"] = ["source_scope.json", "evidence_cards.json"]
    contract["artifact_contract"] = {
        "source_scope.json": {"artifact": "source_scope.json", "schema": {"type": "object"}},
        "evidence_cards.json": {"artifact": "evidence_cards.json", "schema": {"type": "array"}},
    }
    plan = build_staged_execution_plan(contract=contract, original_user_request="timeout")

    result = await asyncio.wait_for(
        execute_staged_builtin_plan(
            llm=BlockingLLM(),
            plan=plan,
            artifact_dir=tmp_path,
            context_prompt="legacy context",
            source_analysis_context=_verified_source_context(),
            source_analysis_limits={
                "timeout_seconds": 0.05,
                "total_timeout_seconds": 0.2,
            },
        ),
        timeout=0.5,
    )

    assert started.is_set()
    assert cancelled.is_set()
    assert result["status"] == "completed"
    assert (tmp_path / "source_scope.json").is_file()
    assert (tmp_path / "evidence_cards.json").is_file()
    stage_result = json.loads(
        (tmp_path / "stages" / "source_analysis" / "stage_result.json").read_text()
    )
    assert stage_result["attempt_count"] == 1
    assert stage_result["degraded"] is True
    assert stage_result["degradation_reason"] == "provider_timeout"
    assert stage_result["provider_wait_ms"] >= 40


@pytest.mark.asyncio
async def test_source_analysis_cache_reuses_validated_pack_without_provider_call(tmp_path):
    class CountingLLM(_StageLLM):
        pass

    contract = _contract()
    contract["required_outputs"] = ["source_scope.json", "evidence_cards.json"]
    contract["artifact_contract"] = {
        "source_scope.json": {"artifact": "source_scope.json", "schema": {"type": "object"}},
        "evidence_cards.json": {"artifact": "evidence_cards.json", "schema": {"type": "array"}},
    }
    plan = build_staged_execution_plan(contract=contract, original_user_request="cache")
    plan["workflow_version"] = "workflow-v7"
    cache_dir = tmp_path / "cache"
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_llm = CountingLLM()
    second_llm = CountingLLM()

    await execute_staged_builtin_plan(
        llm=first_llm,
        plan=plan,
        artifact_dir=first_dir,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
        source_analysis_cache_dir=cache_dir,
    )
    reused_events: list[dict] = []
    await execute_staged_builtin_plan(
        llm=second_llm,
        plan=plan,
        artifact_dir=second_dir,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
        source_analysis_cache_dir=cache_dir,
        on_progress=reused_events.append,
    )

    assert first_llm.calls_by_stage["source_analysis"] == 1
    assert second_llm.calls_by_stage.get("source_analysis", 0) == 0
    assert any(event.get("event_type") == "stage_reused" for event in reused_events)
    stage_result = json.loads(
        (second_dir / "stages" / "source_analysis" / "stage_result.json").read_text()
    )
    assert stage_result["cache_status"] == "hit"
    assert stage_result["attempt_count"] == 0
    assert stage_result["duration_ms"] < 30000


@pytest.mark.asyncio
@pytest.mark.parametrize("tampered_artifact", ["source_analysis.md", "source_scope.json"])
async def test_source_analysis_cache_rejects_tampered_artifacts(
    tmp_path,
    tampered_artifact,
):
    contract = _contract()
    contract["required_outputs"] = ["source_scope.json", "evidence_cards.json"]
    contract["artifact_contract"] = {
        "source_scope.json": {"artifact": "source_scope.json", "schema": {"type": "object"}},
        "evidence_cards.json": {"artifact": "evidence_cards.json", "schema": {"type": "array"}},
    }
    plan = build_staged_execution_plan(contract=contract, original_user_request="cache-tamper")
    plan["workflow_version"] = "workflow-v8"
    cache_dir = tmp_path / "cache"
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_llm = _StageLLM()
    second_llm = _StageLLM()

    await execute_staged_builtin_plan(
        llm=first_llm,
        plan=plan,
        artifact_dir=first_dir,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
        source_analysis_cache_dir=cache_dir,
    )
    first_result = json.loads(
        (first_dir / "stages" / "source_analysis" / "stage_result.json").read_text()
    )
    entry = cache_dir / first_result["cache_key"]
    if tampered_artifact == "source_analysis.md":
        (entry / "source_analysis.md").write_text("tampered report", encoding="utf-8")
    else:
        scope = json.loads((entry / "source_scope.json").read_text())
        scope["repo"] = "/wrong/repo"
        (entry / "source_scope.json").write_text(json.dumps(scope), encoding="utf-8")

    await execute_staged_builtin_plan(
        llm=second_llm,
        plan=plan,
        artifact_dir=second_dir,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
        source_analysis_cache_dir=cache_dir,
    )

    second_result = json.loads(
        (second_dir / "stages" / "source_analysis" / "stage_result.json").read_text()
    )
    assert second_llm.calls_by_stage["source_analysis"] == 1
    assert second_result["cache_status"] == "miss"
    assert "tampered report" not in (second_dir / "source_analysis.md").read_text()


@pytest.mark.asyncio
async def test_ready_downstream_stages_execute_in_parallel(tmp_path):
    active = 0
    max_active = 0
    both_ready = asyncio.Event()

    class ParallelLLM(_StageLLM):
        async def complete(self, messages, max_tokens=4096, temperature=0.2):
            nonlocal active, max_active
            prompt = messages[-1]["content"]
            if "STAGE_ID: source_analysis" in prompt:
                return await super().complete(messages, max_tokens, temperature)
            active += 1
            max_active = max(max_active, active)
            if active == 2:
                both_ready.set()
            try:
                await asyncio.wait_for(both_ready.wait(), timeout=0.3)
                return await super().complete(messages, max_tokens, temperature)
            finally:
                active -= 1

    contract = _contract()
    contract["required_outputs"] = ["project_structure.md", "module_map.md"]
    contract["artifact_contract"] = {
        name: {"artifact": name} for name in contract["required_outputs"]
    }
    plan = build_staged_execution_plan(contract=contract, original_user_request="parallel")

    result = await execute_staged_builtin_plan(
        llm=ParallelLLM(),
        plan=plan,
        artifact_dir=tmp_path,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
    )

    assert result["status"] == "completed"
    assert max_active == 2


@pytest.mark.asyncio
async def test_executor_stops_between_stages_when_cancelled(tmp_path):
    llm = _StageLLM()
    plan = build_staged_execution_plan(contract=_contract(), original_user_request="cancel")

    async def cancelled() -> bool:
        return len(llm.prompts) >= 1

    with pytest.raises(RuntimeError, match="任务已取消"):
        await execute_staged_builtin_plan(
            llm=llm,
            plan=plan,
            artifact_dir=tmp_path,
            context_prompt="SOURCE_CONTEXT",
            is_cancelled=cancelled,
        )
    assert len(llm.prompts) == 1


@pytest.mark.asyncio
async def test_executor_interrupts_active_provider_when_cancelled(tmp_path):
    started = asyncio.Event()
    provider_cancelled = asyncio.Event()
    cancellation_requested = False

    class BlockingLLM:
        async def complete(self, messages, max_tokens=4096, temperature=0.2):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                provider_cancelled.set()
                raise

    async def cancelled() -> bool:
        return cancellation_requested

    plan = build_staged_execution_plan(
        contract=_contract(),
        original_user_request="cancel active provider",
    )
    task = asyncio.create_task(
        execute_staged_builtin_plan(
            llm=BlockingLLM(),
            plan=plan,
            artifact_dir=tmp_path,
            context_prompt="SOURCE_CONTEXT",
            is_cancelled=cancelled,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    cancellation_requested = True
    done, _ = await asyncio.wait({task}, timeout=0.5)
    completed_after_cancel = task in done
    if not task.done():
        task.cancel()
    with pytest.raises((RuntimeError, asyncio.CancelledError)):
        await task

    assert completed_after_cancel is True
    assert provider_cancelled.is_set()
