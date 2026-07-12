from __future__ import annotations

import asyncio
import json

import pytest

from app.llm.base import LLMResponse
from app.services.ai_staged_execution import (
    build_staged_execution_plan,
    execute_staged_builtin_plan,
)


class _StageLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.calls_by_stage: dict[str, int] = {}

    async def complete(self, messages, max_tokens=4096, temperature=0.2):
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        stage = next(
            line.split(":", 1)[1].strip()
            for line in prompt.splitlines()
            if line.startswith("STAGE_ID:")
        )
        self.calls_by_stage[stage] = self.calls_by_stage.get(stage, 0) + 1
        if stage == "source_analysis":
            content = "# 代码证据\n\n- `lib/iscsi/iscsi.c:1262` login version check\n"
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
    assert progress[-1]["status"] == "completed"
    sfmea_prompt = next(prompt for prompt in llm.prompts if "STAGE_ID: sfmea" in prompt)
    assert "business_flow.md" in sfmea_prompt
    assert "lib/iscsi/iscsi.c:1262" in sfmea_prompt


@pytest.mark.asyncio
async def test_executor_retries_transient_provider_error_and_invalid_json(tmp_path):
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
    )

    assert result["status"] == "completed"
    assert llm.calls_by_stage["source_analysis"] == 2
    assert llm.calls_by_stage["sfmea"] == 2
    sfmea_result = json.loads(
        (tmp_path / "stages" / "sfmea" / "stage_result.json").read_text(encoding="utf-8")
    )
    assert sfmea_result["attempts"] == 2


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
