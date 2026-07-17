from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import subprocess
import time
from pathlib import Path

import pytest

from app.llm.base import LLMResponse, current_finish_reason
from app.services.ai_thread_artifacts import _validate_schema
from app.services.ai_staged_execution import (
    _complete_with_cancellation,
    _ProcessProviderCapacity,
    _business_flow_stage_prompt,
    _build_verified_claim_catalog,
    _canonicalize_technical_claim_evidence,
    _apply_regular_stage_output_limits,
    _business_flow_deterministic_base,
    _compact_execution_input_contract,
    _deterministic_quality_claim_repair,
    _deterministic_schema_repair,
    _finalize_combined_markdown_report,
    _regular_stage_prompt,
    _json_array_continuation_prompt,
    _merge_json_array_patch,
    _quality_repair_evidence_cards,
    _ISCSI_RAW_PDU_APPENDIX,
    _extract_business_flow_narrative,
    _render_deterministic_combined_report,
    _select_regular_stage_llm,
    _salvage_truncated_json_array,
    _render_stage_artifact,
    _stage_prompt,
    _stage_format_rules,
    StagedExecutionCancelled,
    build_source_analysis_context,
    build_source_evidence_pack,
    build_staged_execution_plan,
    execute_staged_builtin_plan,
    materialize_source_evidence_pack,
)
from app.services.workflow_presets import EVIDENCE_CARDS_SCHEMA
from app.services.flow_evidence import (
    build_business_flow_context,
    build_flow_evidence_pack,
    build_flow_outline,
    render_business_flow_markdown,
)
from app.services.regular_stage_governance import (
    promote_regular_stage_caches,
    restore_regular_stage_cache,
    stage_execution_policy,
    store_regular_stage_cache,
)
from app.services.workbench_task_run import build_local_source_context


def test_deterministic_raw_pdu_appendix_dispatches_supported_cli_scenarios():
    assert 'parser.add_argument("--host", "--target_ip", dest="host", required=True)' in _ISCSI_RAW_PDU_APPENDIX
    assert 'parser.add_argument("--scenario", default="basic")' in _ISCSI_RAW_PDU_APPENDIX
    assert 'if "fragment" in scenario' in _ISCSI_RAW_PDU_APPENDIX
    assert 'elif "version" in scenario' in _ISCSI_RAW_PDU_APPENDIX
    assert 'elif "mcs" in scenario' in _ISCSI_RAW_PDU_APPENDIX


def test_structured_json_stage_routes_reasoner_to_fast_auxiliary(monkeypatch):
    class Client:
        def __init__(self, model: str) -> None:
            self._model = model

    reasoner = Client("deepseek-reasoner")
    fast = Client("deepseek-chat")
    monkeypatch.setattr(
        "app.services.ai_staged_execution.settings.regular_stage_structured_fast_model_enabled",
        True,
    )

    assert _select_regular_stage_llm(reasoner, fast, "sfmea.json") is fast
    assert _select_regular_stage_llm(reasoner, fast, "business_flow.md") is reasoner
from app.services.workbench_workflow_runner import (
    _build_workbench_staged_plan,
    _expand_quality_blocked_artifacts,
    _quality_allows_cache_promotion,
)


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
            content = json.dumps(
                {
                    "ranked_evidence_ids": ["SRC-01", "SRC-02"],
                    "gap_evidence_ids": ["SRC-06"],
                }
            )
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
                    "source_evidence": ["lib/iscsi/iscsi.c:1262"],
                    "test_mapping": "test/iscsi_tgt/login.sh",
                    "technical_claims": [
                        {
                            "claim_id": "C-SFMEA-001",
                            "type": "source_behavior",
                            "statement": "The login path is implemented in the iSCSI source.",
                            "evidence": [
                                {
                                    "evidence_id": "SRC-01:L1",
                                    "path": "lib/iscsi/iscsi.c",
                                    "symbol": "spdk_iscsi_login_0",
                                    "lines": "L100-L120",
                                    "quote": "validated evidence line",
                                }
                            ],
                        }
                    ],
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
                    "preconditions": ["target running"],
                    "steps": ["connect initiator"],
                    "expected_result": "observable result",
                    "observability": ["log"],
                    "failure_diagnostics": ["session state"],
                    "mapped_test_dir": "test/iscsi_tgt",
                    "source_or_test_evidence": ["lib/iscsi/iscsi.c:1262"],
                    "technical_claims": [
                        {
                            "claim_id": f"C-TC-{index:02d}",
                            "type": "test_evidence",
                            "statement": "The scenario is grounded in the iSCSI source.",
                            "evidence": [
                                {
                                    "evidence_id": "SRC-01:L1",
                                    "path": "lib/iscsi/iscsi.c",
                                    "symbol": "spdk_iscsi_login_0",
                                    "lines": "L100-L120",
                                    "quote": "validated evidence line",
                                }
                            ],
                        }
                    ],
                }
                for index, dimension in enumerate(dimensions, 1)
            ])
        else:
            content = "# 测试设计\n\n## 目标\niSCSI login\n## 输入\nPDU\n## 用例设计\n见黑盒用例\n## 覆盖矩阵\n八维\n## 剩余风险\n需实机\n"
        return LLMResponse(content=content, model="stage-test", usage={}, truncated=False)


def test_provider_wait_message_does_not_report_a_fresh_call_as_stalled():
    from app.services.ai_staged_execution import _provider_wait_user_message

    assert _provider_wait_user_message(
        output_characters=0,
        elapsed_seconds=0.1,
        remaining_seconds=299,
        heartbeat_seconds=10,
    ) == "模型已提交，正在等待首段输出"
    assert _provider_wait_user_message(
        output_characters=0,
        elapsed_seconds=31,
        remaining_seconds=269,
        heartbeat_seconds=10,
    ) == "Provider 尚未返回首段输出，系统将在剩余 269 秒后停止"
    assert _provider_wait_user_message(
        output_characters=42,
        elapsed_seconds=31,
        remaining_seconds=269,
        heartbeat_seconds=10,
    ) == "模型仍在生成，后端心跳正常"


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
            f"    return iscsi_login_step_{index}();\n"
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
        "flow_evidence_pack",
        "flow_outline",
        "business_flow",
        "sfmea",
        "black_box_cases",
        "test_design",
    ]
    assert plan["stages"][4]["depends_on"] == ["source_analysis", "flow_outline"]
    assert plan["stages"][4]["artifact"] == "sfmea.json"
    assert plan["stages"][3]["depends_on"] == ["flow_outline"]
    assert "第一行" in plan["original_user_request"]
    assert "第二行" in plan["original_user_request"]
    source_stage = plan["stages"][0]
    assert source_stage["max_tokens"] == 1600
    assert source_stage["output_limits"]["max_chinese_characters"] == 1200
    assert source_stage["output_limits"]["max_evidence_anchors"] == 12


def test_business_flow_policy_is_single_attempt_streaming_and_hard_bounded():
    policy = stage_execution_policy(
        stage={"id": "business_flow", "max_tokens": 99999},
        global_max_tokens=99999,
        overrides={
            "provider_timeout_seconds": 9999,
            "total_timeout_seconds": 9999,
            "repair_timeout_seconds": 9999,
            "max_full_attempts": 99,
        },
    )

    assert 4000 <= policy.max_tokens <= 8000
    assert policy.max_full_attempts == 1
    assert policy.streaming is True
    assert policy.allow_degraded_output is True
    assert policy.provider_timeout_seconds <= 360
    assert policy.total_timeout_seconds <= 360
    assert policy.repair_timeout_seconds <= 60


def test_business_flow_context_deduplicates_and_bounds_verified_evidence():
    nodes = [
        {
            "evidence_id": f"FLOW-{index:03d}",
            "file_path": f"lib/iscsi/file_{index}.c",
            "symbol": f"iscsi_step_{index}",
            "start_line": index,
            "end_line": index + 3,
            "provider": "git-grep",
            "sha256": "a" * 64,
            "text": "verified branch " + ("detail " * 100),
        }
        for index in range(1, 31)
    ]
    source_pack = {
        "repo_revision": "abc123",
        "evidence_cards": [
            {
                "evidence_id": f"SRC-{index:02d}",
                "file_path": (
                    f"test/iscsi_tgt/test_{index}.sh"
                    if index == 20
                    else f"lib/iscsi/source_{index}.c"
                ),
                "classification": "test" if index == 20 else "source",
                "start_line": index,
                "end_line": index + 20,
                "symbols": [f"symbol_{index}"],
                "excerpt": "source excerpt " * 200,
                "sha256": "b" * 64,
            }
            for index in range(1, 21)
        ],
        "input_materials": [{"name": "requirements", "summary": "input " * 1000}],
    }
    flow_pack = {
        "entry_points": nodes,
        "call_edges": nodes,
        "state_objects": nodes,
        "state_transitions": nodes,
        "conditions": nodes,
        "error_paths": nodes,
        "cleanup_paths": nodes,
        "recovery_paths": nodes,
        "related_tests": nodes,
        "evidence_gaps": ["gap " * 500],
    }
    outline = {
        "actors": ["initiator", "target"],
        "entry_points": nodes,
        "main_flows": [{"steps": nodes}],
        "steps": nodes,
        "branches": nodes,
        "error_flows": nodes,
        "cleanup_flows": nodes,
        "recovery_flows": nodes,
        "state_objects": nodes,
        "state_transitions": nodes,
        "related_tests": nodes,
        "evidence_ids": [item["evidence_id"] for item in nodes],
        "evidence_gaps": ["gap " * 500],
    }

    compact = build_business_flow_context(
        plan={"original_user_request": "分析 iSCSI login"},
        source_pack=source_pack,
        flow_pack=flow_pack,
        outline=outline,
    )
    serialized = json.dumps(compact, ensure_ascii=False)

    assert len(serialized) < 25_000
    assert len(compact["flow_evidence_pack"]["entry_points"]) <= 8
    assert len(compact["flow_outline"]["steps"]) <= 16
    assert len(compact["verified_evidence_cards"]) <= 6
    assert any(
        card["classification"] == "test"
        for card in compact["verified_evidence_cards"]
    )


def test_sfmea_uses_compact_stage_context_instead_of_full_staged_context(tmp_path):
    source_pack = {
        "repo_revision": "abc123",
        "analysis_target": "iSCSI login",
        "evidence_cards": [
            {
                "evidence_id": f"SRC-{index:02d}",
                "file_path": f"lib/iscsi/source_{index}.c",
                "classification": "source",
                "start_line": index,
                "end_line": index + 5,
                "symbols": [f"iscsi_symbol_{index}"],
                "excerpt": "verified excerpt " * 80,
                "sha256": "a" * 64,
            }
            for index in range(1, 12)
        ],
        "input_materials": [{"name": "requirements", "summary": "CHAP recovery"}],
    }
    outline = {
        "evidence_ids": ["FLOW-ENTRY-001", "FLOW-EDGE-001"],
        "entry_points": [{"evidence_id": "FLOW-ENTRY-001", "symbol": "iscsi_login"}],
        "steps": [
            {
                "step": 1,
                "from_symbol": "iscsi_login",
                "to_symbol": "iscsi_authenticate",
                "evidence_ids": ["FLOW-EDGE-001"],
            }
        ],
        "evidence_gaps": [],
    }
    dependency = tmp_path / "source_analysis.md"
    dependency.write_text("full staged context marker " * 6000, encoding="utf-8")
    stage = {
        "id": "sfmea",
        "artifact": "sfmea.json",
        "purpose": "SFMEA",
        "depends_on": ["source_analysis", "flow_outline"],
        "output_contract": {
            "schema": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["failure_mode", "detection"],
                    "properties": {
                        "failure_mode": {"type": "string"},
                        "detection": {"type": "string"},
                    },
                },
            }
        },
        "output_limits": {"max_items": 10, "max_field_characters": 180},
    }

    prompt = _regular_stage_prompt(
        plan={
            "original_user_request": "第一行：分析 iSCSI login\n第二行：必须保留",
            "required_outputs": ["sfmea.json", "black_box_cases.json"],
        },
        stage=stage,
        source_pack=source_pack,
        flow_pack={},
        outline=outline,
        completed={"source_analysis": dependency},
    )

    assert len(prompt) < 30_000
    assert "第一行：分析 iSCSI login" in prompt
    assert "第二行：必须保留" in prompt
    assert '"detection"' in prompt
    assert "FLOW-EDGE-001" in prompt
    assert "full staged context marker" not in prompt


def test_regular_stage_prompt_exposes_canonical_claim_evidence_catalog(tmp_path):
    source_pack = {
        "repo_revision": "abc123",
        "evidence_cards": [
            {
                "evidence_id": "SRC-01",
                "file_path": "include/spdk/iscsi_spec.h",
                "classification": "source",
                "start_line": 518,
                "end_line": 519,
                "symbols": ["ISCSI_LOGIN_ACCEPT"],
                "excerpt": "#define ISCSI_LOGIN_ACCEPT\t0x00\n/* next line */",
                "sha256": "a" * 64,
            }
        ],
    }
    stage = {
        "id": "sfmea",
        "artifact": "sfmea.json",
        "purpose": "SFMEA",
        "depends_on": [],
        "output_contract": {"schema": {"type": "array"}},
    }

    prompt = _regular_stage_prompt(
        plan={"original_user_request": "分析 iSCSI login"},
        stage=stage,
        source_pack=source_pack,
        flow_pack={},
        outline={},
        completed={},
    )

    catalog = _build_verified_claim_catalog(source_pack)
    assert catalog == [
        {
            "evidence_id": "SRC-01:L518",
            "path": "include/spdk/iscsi_spec.h",
            "symbol": "ISCSI_LOGIN_ACCEPT",
            "lines": "L518",
            "quote": "#define ISCSI_LOGIN_ACCEPT\t0x00",
        }
    ]
    assert "VERIFIED_CLAIM_EVIDENCE_CATALOG" in prompt
    assert "SRC-01:L518" in prompt
    assert "只能逐字选择一个 evidence_id" in prompt


def test_verified_claim_catalog_keeps_later_constants_and_log_literals():
    excerpt = "\n".join(
        [f"#define LOGIN_STATUS_{index:02d}\t0x{index:02x}" for index in range(12)]
        + ['SPDK_ERRLOG("CHAP sequence error\\n");']
    )
    source_pack = {
        "evidence_cards": [
            {
                "evidence_id": "SRC-11",
                "file_path": "include/spdk/iscsi_spec.h",
                "start_line": 518,
                "symbols": ["LOGIN_STATUS_11"],
                "excerpt": excerpt,
            }
        ]
    }

    catalog = _build_verified_claim_catalog(source_pack)
    by_id = {item["evidence_id"]: item for item in catalog}

    assert by_id["SRC-11:L529"]["quote"] == "#define LOGIN_STATUS_11\t0x0b"
    assert by_id["SRC-11:L530"]["quote"] == 'SPDK_ERRLOG("CHAP sequence error\\n");'


def test_canonical_claim_evidence_is_materialized_from_evidence_id():
    rendered = [
        {
            "technical_claims": [
                {
                    "claim_id": "C-1",
                    "type": "protocol_constant",
                    "statement": "Login accept is zero.",
                    "evidence": [
                        {
                            "evidence_id": "SRC-01:L518",
                            "path": "invented.c",
                            "lines": "?",
                            "quote": "...",
                        }
                    ],
                }
            ]
        },
        {
            "technical_claims": [
                {
                    "claim_id": "C-2",
                    "type": "source_behavior",
                    "statement": "Unknown evidence remains invalid.",
                    "evidence": [{"evidence_id": "UNKNOWN", "quote": "fake"}],
                }
            ]
        },
    ]
    catalog = [
        {
            "evidence_id": "SRC-01:L518",
            "path": "include/spdk/iscsi_spec.h",
            "symbol": "ISCSI_LOGIN_ACCEPT",
            "lines": "L518",
            "quote": "#define ISCSI_LOGIN_ACCEPT\t0x00",
        }
    ]

    canonicalized = _canonicalize_technical_claim_evidence(rendered, catalog)

    assert canonicalized[0]["technical_claims"][0]["evidence"] == [catalog[0]]
    assert canonicalized[1]["technical_claims"][0]["evidence"] == [
        {"evidence_id": "UNKNOWN", "quote": "fake"}
    ]


def test_regular_stage_output_limit_is_enforced_in_code():
    rendered = [{"case_id": f"BB-{index:02d}"} for index in range(1, 25)]

    limited = _apply_regular_stage_output_limits(
        rendered,
        {"output_limits": {"max_items": 18}},
    )

    assert len(limited) == 18
    assert limited[-1]["case_id"] == "BB-18"


def test_json_array_continuation_receives_canonical_claim_evidence_catalog():
    prompt = _json_array_continuation_prompt(
        stage={
            "id": "sfmea",
            "artifact": "sfmea.json",
            "output_contract": {"schema": {"type": "array"}},
        },
        existing_items=[{"sfmea_id": "F-001", "failure_mode": "timeout"}],
        remaining_count=2,
        evidence_ids=["SRC-01"],
        claim_evidence_catalog=[
            {
                "evidence_id": "SRC-01:L518",
                "path": "include/spdk/iscsi_spec.h",
                "symbol": "ISCSI_LOGIN_ACCEPT",
                "lines": "L518",
                "quote": "#define ISCSI_LOGIN_ACCEPT\t0x00",
            }
        ],
    )

    assert "VERIFIED_CLAIM_EVIDENCE_CATALOG" in prompt
    assert "SRC-01:L518" in prompt
    assert "#define ISCSI_LOGIN_ACCEPT\\t0x00" in prompt
    assert "copy one complete catalog object verbatim" in prompt


def test_quality_repair_prompt_carries_previous_json_and_only_relevant_issues(tmp_path):
    previous = json.dumps(
        [{"failure_mode": "已验证风险", "cause": "已验证原因"}],
        ensure_ascii=False,
    )
    prompt = _regular_stage_prompt(
        plan={
            "original_user_request": "完整分析 iSCSI login",
            "quality_retry_feedback": {
                "affected_artifacts": ["sfmea.json", "black_box_cases.json"],
                "issues": [
                    {
                        "artifact": "sfmea.json",
                        "code": "missing_chap_negative_scenarios",
                        "message": "缺少 Mutual CHAP 负向风险",
                    },
                    {
                        "artifact": "black_box_cases.json",
                        "code": "unsafe_hazardous_test_mapping",
                        "message": "破坏性设备映射不安全",
                    },
                ],
                "instruction": "逐项修复",
            },
        },
        stage={
            "id": "sfmea",
            "artifact": "sfmea.json",
            "purpose": "SFMEA",
            "depends_on": [],
            "output_contract": {"schema": {"type": "array"}},
        },
        source_pack={},
        flow_pack={},
        outline={},
        completed={},
        current_artifact_seed=previous,
    )

    assert "CURRENT_ARTIFACT_TO_REPAIR" in prompt
    assert "已验证风险" in prompt
    assert "缺少 Mutual CHAP 负向风险" in prompt
    assert "破坏性设备映射不安全" not in prompt
    assert "保留未被门禁否定的条目" in prompt


def test_quality_repair_array_prompt_requests_an_incremental_patch():
    prompt = _regular_stage_prompt(
        plan={
            "original_user_request": "完整分析 iSCSI login",
            "quality_retry_feedback": {
                "affected_artifacts": ["black_box_cases.json"],
                "issues": [
                    {
                        "artifact": "black_box_cases.json",
                        "code": "missing_iscsi_professional_scenarios",
                        "message": "缺少专业必测场景",
                        "scenarios": ["Mutual CHAP challenge", "未知键 NotUnderstood"],
                    }
                ],
            },
        },
        stage={
            "id": "black_box_cases",
            "artifact": "black_box_cases.json",
            "purpose": "黑盒用例",
            "depends_on": [],
            "output_contract": {
                "schema": {"type": "array", "minItems": 12},
            },
        },
        source_pack={},
        flow_pack={},
        outline={},
        completed={},
        current_artifact_seed='[{"case_id":"BB-001"}]',
    )

    assert "仅返回需要新增或替换的独立条目数组" in prompt
    assert "系统会按稳定 ID 合并" in prompt
    assert "每个必测场景必须是一个独立条目" in prompt
    assert "场景名称必须逐字包含门禁给出的场景名" in prompt
    assert "必须返回完整的修复后顶层值" not in prompt
    assert "必须输出至少 12 个" not in prompt


def test_behavior_quality_repair_prompt_scopes_failed_rows_and_rejects_pending_relabel():
    previous = json.dumps(
        [
            {
                "case_id": "BBC-01",
                "scenario_name": "accepted row",
                "expected_result": "UNRELATED_ACCEPTED_ROW_MUST_NOT_ENTER_REPAIR_PROMPT",
            },
            {
                "case_id": "BBC-10",
                "scenario_name": "Mutual CHAP with wrong target response",
                "expected_result": "target rejects the impossible response",
                "technical_claims": [
                    {
                        "claim_id": "TC-BBC-10",
                        "evidence": [{"evidence_id": "SRC-44:L938"}],
                    }
                ],
            },
            {
                "case_id": "BBC-11",
                "scenario_name": "Login timeout after first PDU",
                "expected_result": "timer fires after 30 seconds",
                "technical_claims": [
                    {
                        "claim_id": "TC-BBC-11",
                        "evidence": [{"evidence_id": "SRC-43:L2228"}],
                    }
                ],
            },
        ],
        ensure_ascii=False,
        indent=2,
    )
    source_pack = {
        "evidence_cards": [
            {
                "evidence_id": f"SRC-{index:02d}",
                "file_path": f"lib/iscsi/source_{index:02d}.c",
                "start_line": index,
                "end_line": index + 1,
                "excerpt": (
                    "EXACT_FAILED_ROW_EVIDENCE"
                    if index in {43, 44}
                    else f"UNRELATED_EVIDENCE_{index:02d}_" + ("x" * 1200)
                ),
            }
            for index in range(1, 45)
        ]
    }
    feedback = {
        "affected_artifacts": ["black_box_cases.json"],
        "issues": [
            {
                "artifact": "black_box_cases.json",
                "code": "behavior_claim_contradicted",
                "claim_id": "ROW:black_box_cases.json:BBC-10",
                "message": "target does not validate an initiator-supplied target response",
                "evidence": [
                    {
                        "evidence_id": "SRC-44:L938",
                        "path": "lib/iscsi/source_44.c",
                        "quote": "target builds its own response",
                    }
                ],
            },
            {
                "artifact": "black_box_cases.json",
                "code": "behavior_claim_contradicted",
                "claim_id": "ROW:black_box_cases.json:BBC-11",
                "message": "the first login payload unregisters the timer",
                "evidence": [
                    {
                        "evidence_id": "SRC-43:L2228",
                        "path": "lib/iscsi/source_43.c",
                        "quote": "spdk_poller_unregister",
                    }
                ],
            },
        ],
    }

    prompt = _regular_stage_prompt(
        plan={
            "original_user_request": "完整分析 iSCSI login",
            "quality_retry_feedback": feedback,
        },
        stage={
            "id": "black_box_cases",
            "artifact": "black_box_cases.json",
            "purpose": "黑盒用例",
            "depends_on": [],
            "output_contract": {"schema": {"type": "array"}},
        },
        source_pack=source_pack,
        flow_pack={},
        outline={},
        completed={},
        current_artifact_seed=previous,
    )

    assert "BBC-10" in prompt
    assert "BBC-11" in prompt
    assert "UNRELATED_ACCEPTED_ROW_MUST_NOT_ENTER_REPAIR_PROMPT" not in prompt
    assert "EXACT_FAILED_ROW_EVIDENCE" in prompt
    assert "UNRELATED_EVIDENCE_01_" not in prompt
    assert "不能仅添加“待验证”" in prompt
    assert "场景前提本身与源码相反" in prompt
    assert len(prompt) < 30_000


def test_quality_repair_array_patch_preserves_previous_rows_fields_and_capacity():
    previous = [
        {
            "case_id": f"BBC-{index:02d}",
            "scenario_name": f"accepted scenario {index}",
            "expected_result": f"old result {index}",
            "observability": [f"metric-{index}"],
        }
        for index in range(1, 13)
    ]
    patch = [
        {
            "case_id": "BBC-10",
            "expected_result": "source-backed corrected result",
        },
        {
            "case_id": "BBC-13",
            "scenario_name": "unexpected new row",
            "expected_result": "must not evict an accepted row",
        },
    ]

    merged = _merge_json_array_patch(
        previous,
        patch,
        allowed_existing_row_ids={"BBC-10"},
    )
    limited = _apply_regular_stage_output_limits(
        merged,
        {"output_limits": {"max_items": 12}},
        minimum_items=len(merged),
    )

    assert [item["case_id"] for item in limited] == [
        f"BBC-{index:02d}" for index in range(1, 13)
    ] + ["BBC-13"]
    repaired = next(item for item in limited if item["case_id"] == "BBC-10")
    assert repaired["expected_result"] == "source-backed corrected result"
    assert repaired["scenario_name"] == "accepted scenario 10"
    assert repaired["observability"] == ["metric-10"]


def test_quality_repair_evidence_cards_combine_exact_and_contextual_matches():
    evidence_cards = [
        {
            "evidence_id": "SRC-44",
            "file_path": "lib/iscsi/auth.c",
            "symbols": ["iscsi_auth_params"],
        },
        {
            "evidence_id": "SRC-45",
            "file_path": "lib/iscsi/session.c",
            "symbols": ["append_iscsi_sess"],
        },
        {
            "evidence_id": "SRC-46",
            "file_path": "lib/iscsi/unrelated.c",
            "symbols": ["unrelated"],
        },
    ]

    selected = _quality_repair_evidence_cards(
        evidence_cards=evidence_cards,
        evidence_ids={"SRC-44:L938"},
        feedback_text=(
            "SRC-44 contradicts the claim; inspect lib/iscsi/session.c "
            "and append_iscsi_sess as supporting context"
        ).lower(),
    )

    assert [item["evidence_id"] for item in selected] == ["SRC-44", "SRC-45"]


def test_quality_repair_claim_catalog_filters_requested_evidence_before_global_limit():
    evidence_cards = []
    for index in range(1, 31):
        start_line = index * 100
        excerpt_lines = [f"int helper_{index}_{line} = {line};" for line in range(8)]
        if index == 30:
            excerpt_lines[7] = "int TARGET_LATE_CLAIM = 0x23;"
        evidence_cards.append(
            {
                "evidence_id": f"SRC-{index:02d}",
                "file_path": f"lib/iscsi/source_{index:02d}.c",
                "start_line": start_line,
                "end_line": start_line + 7,
                "excerpt": "\n".join(excerpt_lines),
                "symbols": [],
            }
        )
    target_line = 3007
    previous = json.dumps(
        [
            {
                "case_id": "BBC-30",
                "scenario_name": "late catalog evidence",
                "technical_claims": [
                    {
                        "claim_id": "TC-BBC-30",
                        "evidence": [{"evidence_id": f"SRC-30:L{target_line}"}],
                    }
                ],
            }
        ],
        ensure_ascii=False,
    )
    feedback = {
        "affected_artifacts": ["black_box_cases.json"],
        "issues": [
            {
                "artifact": "black_box_cases.json",
                "code": "behavior_claim_contradicted",
                "claim_id": "ROW:black_box_cases.json:BBC-30",
                "evidence": [{"evidence_id": f"SRC-30:L{target_line}"}],
            }
        ],
    }

    prompt = _regular_stage_prompt(
        plan={"original_user_request": "分析 iSCSI", "quality_retry_feedback": feedback},
        stage={
            "id": "black_box_cases",
            "artifact": "black_box_cases.json",
            "purpose": "黑盒用例",
            "depends_on": [],
            "output_contract": {"schema": {"type": "array"}},
        },
        source_pack={"evidence_cards": evidence_cards},
        flow_pack={},
        outline={},
        completed={},
        current_artifact_seed=previous,
    )
    catalog_section = prompt.split("VERIFIED_CLAIM_EVIDENCE_CATALOG:", 1)[1].split(
        "VERIFIED_REPO_PATH_ALLOWLIST:", 1
    )[0]

    assert f'"evidence_id": "SRC-30:L{target_line}"' in catalog_section
    assert "TARGET_LATE_CLAIM" in catalog_section


def test_requested_claim_catalog_keeps_exact_lines_across_large_cards():
    source_pack = {
        "evidence_cards": [
            {
                "evidence_id": "SRC-A",
                "file_path": "lib/iscsi/a.c",
                "start_line": 100,
                "end_line": 199,
                "excerpt": "\n".join(
                    f"int a_line_{index} = {index};" for index in range(100)
                ),
            },
            {
                "evidence_id": "SRC-B",
                "file_path": "lib/iscsi/b.c",
                "start_line": 200,
                "end_line": 299,
                "excerpt": "\n".join(
                    f"int b_line_{index} = {index};" for index in range(100)
                ),
            },
        ]
    }

    catalog = _build_verified_claim_catalog(
        source_pack,
        requested_evidence_ids={"SRC-A:L199", "SRC-B:L299"},
    )

    assert {item["evidence_id"] for item in catalog} == {
        "SRC-A:L199",
        "SRC-B:L299",
    }


def test_performance_quality_repair_prompt_requires_a_statistical_basis():
    prompt = _regular_stage_prompt(
        plan={
            "original_user_request": "分析登录性能",
            "quality_retry_feedback": {
                "affected_artifacts": ["black_box_cases.json"],
                "issues": [
                    {
                        "artifact": "black_box_cases.json",
                        "code": "missing_performance_statistical_basis",
                        "message": "相对性能阈值必须有统计依据",
                    }
                ],
            },
        },
        stage={
            "id": "black_box_cases",
            "artifact": "black_box_cases.json",
            "purpose": "黑盒用例",
            "depends_on": [],
            "output_contract": {"schema": {"type": "array"}},
        },
        source_pack={},
        flow_pack={},
        outline={},
        completed={},
        current_artifact_seed='[{"case_id":"BB-PERF"}]',
    )

    assert "标准差、方差、置信区间或历史波动" in prompt
    assert "禁止直接写固定百分比" in prompt


def test_json_array_salvage_skips_one_malformed_object_and_keeps_later_rows():
    malformed = """[
      {"case_id":"BB-01","steps":["ok"]},
      {"case_id":"BB-02","steps":["first"],"second"]},
      {"case_id":"BB-03","steps":["still valid"]}
    ]"""

    recovered = _salvage_truncated_json_array(malformed)

    assert [item["case_id"] for item in recovered] == ["BB-01", "BB-03"]


def test_quality_repair_prompt_uses_raw_artifact_and_omits_discovery_context():
    previous = json.dumps(
        [
            {
                "case_id": f"BB-{index:03d}",
                "test_dimension": "invalid_input",
                "scenario_name": f"场景 {index}",
                "preconditions": ["准备隔离测试环境"],
                "steps": ["执行外部操作"],
                "expected_result": "记录外部结果",
                "observability": ["抓包"],
                "failure_diagnostics": ["检查日志"],
                "mapped_test_dir": "test/iscsi_tgt/chap/chap_common.sh",
                "source_or_test_evidence": ["lib/iscsi/iscsi.c"],
            }
            for index in range(1, 25)
        ],
        ensure_ascii=False,
        indent=2,
    )
    source_pack = {
        "repo_revision": "abc1234",
        "evidence_cards": [
            {
                "evidence_id": f"SRC-{index:02d}",
                "file_path": "lib/iscsi/iscsi.c",
                "start_line": index,
                "end_line": index + 2,
                "excerpt": "verified-source-" + ("x" * 1400),
            }
            for index in range(1, 45)
        ],
        "verified_literals": [
            {"name": "ISCSI_LOGIN_TIMEOUT", "value": "30", "evidence_id": "SRC-01"}
        ],
    }
    prompt = _regular_stage_prompt(
        plan={
            "original_user_request": "完整分析 iSCSI login",
            "quality_retry_feedback": {
                "affected_artifacts": ["black_box_cases.json"],
                "issues": [
                    {
                        "artifact": "black_box_cases.json",
                        "code": "ungrounded_performance_threshold",
                        "message": "性能阈值必须改为同环境相对基线",
                    }
                ],
                "instruction": "逐项修复",
            },
        },
        stage={
            "id": "black_box_cases",
            "artifact": "black_box_cases.json",
            "purpose": "黑盒用例",
            "depends_on": ["flow_outline"],
            "output_contract": {"schema": {"type": "array", "minItems": 24}},
        },
        source_pack=source_pack,
        flow_pack={"marker": "huge-flow-marker", "edges": ["y" * 2000] * 40},
        outline={"marker": "huge-outline-marker", "steps": ["z" * 2000] * 20},
        completed={},
        current_artifact_seed=previous,
    )

    assert f"CURRENT_ARTIFACT_TO_REPAIR:\n{previous}" in prompt
    assert '"current_artifact_to_repair"' not in prompt
    assert "huge-flow-marker" not in prompt
    assert "huge-outline-marker" not in prompt
    assert len(prompt) < 70_000


def test_quality_repair_prompt_ends_with_mandatory_issue_checklist():
    prompt = _regular_stage_prompt(
        plan={
            "original_user_request": "完整分析 iSCSI login",
            "quality_retry_feedback": {
                "affected_artifacts": ["black_box_cases.json"],
                "issues": [
                    {
                        "artifact": "report.md",
                        "source_artifact": "assistant-output.md",
                        "code": "missing_iscsi_professional_scenarios",
                        "message": "缺少专业必测场景: Discovery 后 SendTargets",
                        "scenarios": ["Discovery 后 SendTargets"],
                    },
                    {
                        "artifact": "black_box_cases.json",
                        "source_artifact": "test_design.md",
                        "code": "missing_max_connections_target_setup",
                        "message": "必须使用 iscsi_set_options -c 2",
                    },
                ],
                "instruction": "逐项修复",
            },
        },
        stage={
            "id": "black_box_cases",
            "artifact": "black_box_cases.json",
            "purpose": "黑盒用例",
            "depends_on": [],
            "output_contract": {"schema": {"type": "array"}},
        },
        source_pack={},
        flow_pack={},
        outline={},
        completed={},
        current_artifact_seed='[{"case_id":"BB-001"}]',
    )

    checklist = prompt.rsplit("MANDATORY_QUALITY_REPAIR_CHECKLIST:", 1)[1]
    assert "missing_iscsi_professional_scenarios" in checklist
    assert "Discovery 后 SendTargets" in checklist
    assert "missing_max_connections_target_setup" in checklist
    assert "iscsi_set_options -c 2" in checklist


def test_workbench_plan_and_regular_prompt_preserve_named_execution_inputs(tmp_path):
    execution_contract = {
        "workflow": {"version": 7},
        "goal": "完成灰白盒测试设计并给出恢复约束",
        "analysis_targets": [
            {"id": "target", "label": "分析目标", "value": "iSCSI login\n保留第二行"}
        ],
        "user_inputs": [
            {"id": "target", "label": "分析目标", "value": "iSCSI login\n保留第二行"},
            {"id": "mr_link", "label": "MR 链接", "value": "https://codehub.local/mr/42"},
            {"id": "constraints", "label": "测试约束", "value": "必须覆盖并发重连与资源耗尽"},
        ],
        "input_materials": {
            "materials": [
                {"input_id": "requirements", "sha256": "abc", "summary": "需求材料摘要"}
            ]
        },
        "mcp": {
            "profile": "gitnexus+cgc",
            "availability": {"status": "available"},
            "requests": [{"input_id": "mr_link", "value": "https://codehub.local/mr/42"}],
        },
        "skills": {
            "ids": ["storage-test-design", "sfmea"],
            "instructions": ["按存储协议测试专业规则检查恢复路径"],
        },
        "test_activity_contract": {
            "target": "iSCSI login",
            "professional_constraints": [
                {
                    "id": "iscsi_recovery",
                    "assertion": "恢复路径必须区分连接清理与会话恢复",
                    "evidence": ["lib/iscsi/conn.c::_iscsi_conn_destruct"],
                    "conflict_patterns": ["LARGE_INTERNAL_REGEX_SENTINEL" * 2000],
                }
            ],
            "quality_gates": {"high_risk_requires_source_or_test_evidence": True},
            "artifact_contract": {
                f"output-{index}.json": {"schema": "UNRELATED_SCHEMA_SENTINEL" * 2000}
                for index in range(40)
            },
        },
    }
    plan = _build_workbench_staged_plan(
        run_id="run-input-contract",
        execution_contract=execution_contract,
        task_bundle={"context_bundle": {"query": "unused history " * 20_000}},
        output_contract={"expected_output_schemas": []},
        required_artifacts=["test_design.md"],
    )
    stage = next(item for item in plan["stages"] if item["artifact"] == "test_design.md")
    prompt = _regular_stage_prompt(
        plan=plan,
        stage=stage,
        source_pack={"repo_revision": "abc123", "evidence_cards": []},
        flow_pack={},
        outline={},
        completed={},
    )

    for expected in (
        "iSCSI login",
        "保留第二行",
        "https://codehub.local/mr/42",
        "必须覆盖并发重连与资源耗尽",
        "requirements",
        "需求材料摘要",
        "gitnexus+cgc",
            "storage-test-design",
            "按存储协议测试专业规则检查恢复路径",
            "iscsi_recovery",
            "high_risk_requires_source_or_test_evidence",
    ):
        assert expected in prompt
    assert "unused history" not in prompt
    assert "LARGE_INTERNAL_REGEX_SENTINEL" not in prompt
    assert "UNRELATED_SCHEMA_SENTINEL" not in prompt
    assert len(prompt) < 35_000


def test_workbench_plan_exposes_source_bound_domain_facts_without_lint_patterns():
    execution_contract = {
        "goal": "分析 iSCSI login",
        "test_activity_contract": {
            "professional_constraints": [
                {
                    "id": "iscsi_login_timer_after_first_pdu",
                    "assertion": (
                        "首个 Login payload 开始处理时注销 login_timer，"
                        "后续不能预设该定时器仍会触发。"
                    ),
                    "evidence": [
                        "lib/iscsi/iscsi.c::iscsi_pdu_payload_op_login"
                    ],
                    "conflict_patterns": ["REGEX_MUST_NOT_REACH_GENERATION"],
                    "correction_patterns": ["CORRECTION_REGEX_MUST_NOT_REACH_GENERATION"],
                }
            ],
        },
    }

    plan = _build_workbench_staged_plan(
        run_id="run-domain-facts",
        execution_contract=execution_contract,
        task_bundle={},
        output_contract={"expected_output_schemas": []},
        required_artifacts=["black_box_cases.json"],
    )

    assert plan["source_bound_domain_fact_candidates"] == [
        {
            "id": "iscsi_login_timer_after_first_pdu",
            "assertion": (
                "首个 Login payload 开始处理时注销 login_timer，"
                "后续不能预设该定时器仍会触发。"
            ),
            "evidence": [
                "lib/iscsi/iscsi.c::iscsi_pdu_payload_op_login"
            ],
        }
    ]
    serialized = json.dumps(
        plan["source_bound_domain_fact_candidates"], ensure_ascii=False
    )
    assert "REGEX_MUST_NOT_REACH_GENERATION" not in serialized
    assert "CORRECTION_REGEX_MUST_NOT_REACH_GENERATION" not in serialized


def test_regular_stage_prompt_includes_only_domain_facts_bound_to_verified_source():
    facts = [
        {
            "id": "verified_timer_fact",
            "assertion": "首个 Login payload 会注销 login_timer。",
            "evidence": ["lib/iscsi/iscsi.c::iscsi_pdu_payload_op_login"],
        },
        {
            "id": "missing_source_fact",
            "assertion": "这条断言没有当前证据支持。",
            "evidence": ["lib/iscsi/missing.c::missing_symbol"],
        },
    ]
    plan = {
        "original_user_request": "分析 iSCSI login",
        "source_bound_domain_fact_candidates": facts,
    }
    stage = {
        "id": "black_box_cases",
        "artifact": "black_box_cases.json",
        "purpose": "黑盒用例",
        "depends_on": [],
        "output_contract": {"schema": {"type": "array"}},
    }
    source_pack = {
        "evidence_cards": [
            {
                "file_path": "lib/iscsi/iscsi.c",
                "symbols": ["iscsi_pdu_payload_op_login"],
                "excerpt": "iscsi_pdu_payload_op_login unregisters login_timer",
            }
        ]
    }

    prompt = _regular_stage_prompt(
        plan=plan,
        stage=stage,
        source_pack=source_pack,
        flow_pack={},
        outline={},
        completed={},
        current_artifact_seed='[{"case_id":"BBC-11"}]',
    )

    assert "SOURCE_BOUND_DOMAIN_FACTS" in prompt
    assert "verified_timer_fact" in prompt
    assert "首个 Login payload 会注销 login_timer" in prompt
    assert "missing_source_fact" not in prompt
    assert "这条断言没有当前证据支持" not in prompt
    assert "已验证源码片段矛盾时以源码片段为准" in prompt


def test_workbench_staged_plan_preserves_combined_report_quality_contract():
    report_contract = {
        "sections": ["分析范围与证据缺口", "SFMEA", "黑盒测试用例"],
        "min_sfmea_rows": 12,
        "min_black_box_cases": 12,
        "min_source_paths": 6,
        "min_test_paths": 4,
    }

    plan = _build_workbench_staged_plan(
        run_id="run-combined-report",
        execution_contract={
            "goal": "生成完整测试报告",
            "test_activity_contract": {
                "artifact_contract": {"report.md": report_contract},
            },
        },
        task_bundle={},
        output_contract={"expected_output_schemas": []},
        required_artifacts=["report.md"],
    )

    stage = next(item for item in plan["stages"] if item["artifact"] == "report.md")
    assert stage["output_contract"] == {
        "artifact": "report.md",
        **report_contract,
    }
    assert stage["streaming"] is True
    assert stage["continue_on_length"] is True


def test_combined_report_support_stages_use_bounded_independent_budgets():
    report_contract = {
        "sections": ["分析范围与证据缺口", "SFMEA", "黑盒测试用例"],
        "min_sfmea_rows": 12,
        "min_black_box_cases": 12,
    }

    plan = _build_workbench_staged_plan(
        run_id="run-combined-report-budget",
        execution_contract={
            "goal": "生成完整测试报告",
            "test_activity_contract": {
                "artifact_contract": {"report.md": report_contract},
            },
        },
        task_bundle={},
        output_contract={"expected_output_schemas": []},
        required_artifacts=["report.md"],
    )

    sfmea = next(item for item in plan["stages"] if item["artifact"] == "sfmea.json")
    cases = next(
        item for item in plan["stages"] if item["artifact"] == "black_box_cases.json"
    )

    assert sfmea["max_tokens"] == 9000
    assert sfmea["output_limits"]["max_items"] == 12
    assert cases["max_tokens"] == 12000
    assert cases["output_limits"]["max_items"] == 12
    assert sfmea["max_tokens"] < cases["max_tokens"] <= 12000


def test_workbench_staged_plan_uses_goal_when_no_explicit_analysis_target():
    plan = _build_workbench_staged_plan(
        run_id="run-goal-fallback",
        execution_contract={
            "goal": "针对 SPDK iSCSI login 生成测试报告",
            "analysis_targets": [],
            "input_materials": {
                "materials": [
                    {
                        "input_id": "design_doc",
                        "summary": "设计文档必须覆盖 CHAP 与重连。",
                    }
                ]
            },
        },
        task_bundle={
            "context_bundle": {
                "query": "design.md /private/tmp/design.md 设计文档完整正文不应成为标题"
            }
        },
        output_contract={"expected_output_schemas": []},
        required_artifacts=["report.md"],
    )

    assert plan["original_user_request"] == "针对 SPDK iSCSI login 生成测试报告"
    assert plan["execution_input_contract"]["input_materials"]["materials"][0][
        "summary"
    ] == "设计文档必须覆盖 CHAP 与重连。"


def test_combined_report_plan_runs_flow_sfmea_and_black_box_before_report():
    contract = {
        "target": "iSCSI login",
        "required_outputs": ["report.md"],
        "artifact_contract": {
            "report.md": {
                "artifact": "report.md",
                "sections": ["主流程与异常/恢复流程", "SFMEA", "黑盒测试用例"],
                "min_sfmea_rows": 12,
                "min_black_box_cases": 12,
                "min_source_paths": 6,
                "min_test_paths": 4,
            }
        },
    }

    plan = build_staged_execution_plan(
        contract=contract,
        original_user_request="生成源码证据驱动的完整测试报告",
    )

    assert [stage["id"] for stage in plan["stages"]] == [
        "source_analysis",
        "flow_evidence_pack",
        "flow_outline",
        "business_flow",
        "sfmea",
        "black_box_cases",
        "artifact_1",
    ]
    final_stage = plan["stages"][-1]
    assert final_stage["depends_on"] == [
        "business_flow",
        "sfmea",
        "black_box_cases",
    ]
    assert final_stage["support"] is False
    sfmea_stage = next(stage for stage in plan["stages"] if stage["id"] == "sfmea")
    cases_stage = next(stage for stage in plan["stages"] if stage["id"] == "black_box_cases")
    flow_stage = next(stage for stage in plan["stages"] if stage["id"] == "business_flow")
    assert sfmea_stage["output_contract"]["schema"]["minItems"] == 12
    assert cases_stage["output_contract"]["schema"]["minItems"] == 12
    assert "technical_claims" in sfmea_stage["output_contract"]["schema"]["items"]["required"]
    assert "technical_claims" in cases_stage["output_contract"]["schema"]["items"]["required"]
    sfmea_claim_schema = sfmea_stage["output_contract"]["schema"]["items"]["properties"]["technical_claims"]
    assert sfmea_claim_schema["minItems"] == 1
    assert sfmea_claim_schema["maxItems"] == 1
    assert sfmea_claim_schema["items"]["required"] == [
        "claim_id",
        "type",
        "statement",
        "evidence",
    ]
    evidence_schema = sfmea_claim_schema["items"]["properties"]["evidence"]
    assert evidence_schema["maxItems"] == 1
    assert "evidence_id" in evidence_schema["items"]["required"]
    assert sfmea_stage["max_tokens"] == 9_000
    assert sfmea_stage["output_limits"]["max_items"] == 12
    assert cases_stage["max_tokens"] == 12_000
    assert cases_stage["output_limits"]["max_items"] == 12
    assert flow_stage["streaming"] is True
    assert flow_stage["continue_on_length"] is True
    assert flow_stage["max_continuations"] == 1


@pytest.mark.asyncio
async def test_combined_report_is_deterministically_rendered_from_validated_stage_artifacts(
    tmp_path,
):
    class NoReportRewriteLLM(_StageLLM):
        async def complete(self, messages, max_tokens=4096, temperature=0.2):
            prompt = messages[-1]["content"]
            assert "STAGE_ID: artifact_1" not in prompt
            return await super().complete(messages, max_tokens, temperature)

    contract = {
        "target": "完整 iSCSI login 测试分析",
        "required_outputs": ["report.md"],
        "artifact_contract": {
            "report.md": {
                "artifact": "report.md",
                "sections": [
                    "分析范围与证据缺口",
                    "关键源码证据",
                    "主流程与异常/恢复流程",
                    "SFMEA",
                    "黑盒测试用例",
                ],
                "min_sfmea_rows": 1,
                "min_black_box_cases": 8,
                "min_source_paths": 1,
                "min_test_paths": 1,
            }
        },
    }
    llm = NoReportRewriteLLM()

    execution = await execute_staged_builtin_plan(
        llm=llm,
        plan=build_staged_execution_plan(
            contract=contract,
            original_user_request="完整分析 iSCSI login",
        ),
        artifact_dir=tmp_path,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
    )

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    result = json.loads(
        (tmp_path / "stages" / "artifact_1" / "stage_result.json").read_text()
    )
    assert execution["status"] == "completed"
    assert result["attempt_count"] == 0
    assert result["provider_call_count"] == 0
    assert result["producer"] == "deterministic_combined_report"
    assert result["harness_validation"]["status"] == "passed"
    assert result["harness_validation"]["transport"] == "tcp_loopback"
    runtime_validation = json.loads(
        (tmp_path / "raw_pdu_harness_validation.json").read_text()
    )
    assert runtime_validation["status"] == "passed"
    assert "first_pdu_sendall" in runtime_validation["checks"]
    assert runtime_validation["interpreter"].endswith("python3")
    harness_source = (tmp_path / "support" / "iscsi_login_raw_pdu.py").read_text()
    assert "from __future__ import annotations" in harness_source
    assert "artifact_1" not in llm.calls_by_stage
    for heading in (
        "## 分析范围与证据缺口",
        "## 关键源码证据",
        "## 主流程与异常/恢复流程",
        "## SFMEA",
        "## 黑盒测试用例",
    ):
        assert heading in report
    assert report.count("### TC-") == 8
    assert "lib/iscsi/iscsi.c" in report


def test_combined_report_preserves_sfmea_ids_for_quality_repair_targeting():
    from app.services.ai_staged_execution import _render_deterministic_combined_report
    from app.services.test_activity_contract import _audit_combined_report_consistency

    sfmea = [
        {
            "sfmea_id": "SFMEA-20",
            "failure_mode": "Unsupported CHAP_A algorithm proposed by initiator",
            "cause": "SHA1 is not supported",
            "effect": "login rejected",
            "detection": "capture login response",
            "severity": 7,
            "occurrence": 2,
            "detection_score": 6,
            "rpn": 84,
            "mitigation": "reject unsupported algorithms",
            "source_evidence": ["lib/iscsi/iscsi.c:900-930"],
            "test_mapping": "test/iscsi_tgt/chap/chap_mutual_not_set.sh",
        },
        {
            "sfmea_id": "SFMEA-09",
            "failure_mode": "CHAP algorithm mismatch uses a non-MD5 algorithm",
            "cause": "initiator selects SHA1",
            "effect": "login rejected",
            "detection": "capture login response",
            "severity": 7,
            "occurrence": 3,
            "detection_score": 6,
            "rpn": 126,
            "mitigation": "use MD5",
            "source_evidence": ["lib/iscsi/iscsi.c:900-930"],
            "test_mapping": "test/iscsi_tgt/chap/chap_mutual_not_set.sh",
        },
    ]

    report = _render_deterministic_combined_report(
        plan={"original_user_request": "analyze iSCSI login"},
        source_pack={"repo_revision": "abc123", "evidence_cards": []},
        business_flow="主流程。",
        sfmea=sfmea,
        black_box_cases=[],
    )
    duplicates = [
        issue
        for issue in _audit_combined_report_consistency(report)
        if issue["code"] == "duplicate_sfmea_risk"
    ]

    assert "| SFMEA-20 | Unsupported CHAP_A algorithm" in report
    assert "| SFMEA-09 | CHAP algorithm mismatch" in report
    assert duplicates[0]["risk_ids"] == ["SFMEA-20", "SFMEA-09"]


def test_deterministic_iscsi_appendix_filters_login_responses_with_response_opcode():
    from app.services.ai_staged_execution import _render_deterministic_combined_report

    report = _render_deterministic_combined_report(
        plan={"original_user_request": "完整分析 iSCSI login"},
        source_pack={"repo_revision": "abc123", "evidence_cards": []},
        business_flow="主流程。",
        sfmea=[],
        black_box_cases=[],
    )

    appendix = report.split("## 附录：iSCSI raw-PDU 复验工具", 1)[1]
    assert "tshark -r /tmp/iscsi-login.pcap -Y iscsi.opcode==0x23" in appendix
    assert "tshark -r /tmp/iscsi-login.pcap -Y iscsi.opcode==0x03" not in appendix


@pytest.mark.asyncio
async def test_deterministic_combined_report_keeps_all_sections_when_flow_contains_markdown_fence(
    tmp_path,
):
    class FencedFlowLLM(_StageLLM):
        async def complete(self, messages, max_tokens=4096, temperature=0.2):
            prompt = messages[-1]["content"]
            if "STAGE_ID: business_flow" in prompt:
                self.prompts.append(prompt)
                self.max_tokens_by_stage.setdefault("business_flow", []).append(max_tokens)
                self.calls_by_stage["business_flow"] = (
                    self.calls_by_stage.get("business_flow", 0) + 1
                )
                return LLMResponse(
                    content=(
                        "```markdown\n### 模型叙述增强\n\n"
                        "登录请求进入状态机。\n```"
                    ),
                    model="fenced-flow",
                    usage={},
                    finish_reason="stop",
                )
            return await super().complete(messages, max_tokens, temperature)

    contract = {
        "target": "完整 iSCSI login 测试分析",
        "required_outputs": ["report.md"],
        "artifact_contract": {
            "report.md": {
                "artifact": "report.md",
                "sections": [
                    "分析范围与证据缺口",
                    "关键源码证据",
                    "主流程与异常/恢复流程",
                    "SFMEA",
                    "黑盒测试用例",
                ],
                "min_sfmea_rows": 1,
                "min_black_box_cases": 8,
                "min_source_paths": 1,
                "min_test_paths": 1,
            }
        },
    }
    llm = FencedFlowLLM()

    execution = await execute_staged_builtin_plan(
        llm=llm,
        plan=build_staged_execution_plan(
            contract=contract,
            original_user_request="完整分析 iSCSI login",
        ),
        artifact_dir=tmp_path,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
    )

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert execution["status"] == "completed"
    for heading in (
        "## 分析范围与证据缺口",
        "## 关键源码证据",
        "## 主流程与异常/恢复流程",
        "## SFMEA",
        "## 黑盒测试用例",
    ):
        assert heading in report
    assert report.count("### TC-") == 8
    assert "test/iscsi_tgt/login.sh" in report
    assert "```python" in report
    assert "bhs[5:8] = len(data).to_bytes(3, \"big\")" in report
    assert "int.from_bytes(bhs[5:8], \"big\")" in report


@pytest.mark.asyncio
async def test_combined_markdown_report_never_calls_truncating_report_provider(tmp_path):
    report_prompts: list[str] = []

    class LongReportLLM(_StageLLM):
        async def complete(self, messages, max_tokens=4096, temperature=0.2):
            prompt = messages[-1]["content"]
            if "STAGE_ID: artifact_1" not in prompt:
                return await super().complete(messages, max_tokens, temperature)
            report_prompts.append(prompt)
            if len(report_prompts) == 1:
                return LLMResponse(
                    content="# 报告\n\n## 分析范围与证据缺口\n第一段未完",
                    model="stage-test",
                    usage={},
                    truncated=True,
                    finish_reason="length",
                )
            return LLMResponse(
                content="，从原位置续写完成。\n\n## 黑盒测试用例\n### 5.1 正常登录\n完成。",
                model="stage-test",
                usage={},
                truncated=False,
                finish_reason="stop",
            )

    contract = {
        "target": "iSCSI login",
        "required_outputs": ["report.md"],
        "artifact_contract": {
            "report.md": {
                "artifact": "report.md",
                "preview": "markdown",
                "sections": ["分析范围与证据缺口", "黑盒测试用例"],
                "min_black_box_cases": 1,
            }
        },
    }
    execution = await execute_staged_builtin_plan(
        llm=LongReportLLM(),
        plan=build_staged_execution_plan(
            contract=contract,
            original_user_request="生成完整报告",
        ),
        artifact_dir=tmp_path,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
    )

    stage_result = json.loads(
        (tmp_path / "stages" / "artifact_1" / "stage_result.json").read_text()
    )
    report = (tmp_path / "report.md").read_text()
    assert execution["status"] == "completed"
    assert stage_result["attempt_count"] == 0
    assert stage_result["provider_call_count"] == 0
    assert stage_result["continuation_count"] == 0
    assert stage_result["finish_reason"] == "deterministic_materialization"
    assert report_prompts == []
    assert "## 黑盒测试用例" in report


def test_regular_streaming_stage_prompt_carries_partial_continuation():
    prompt = _regular_stage_prompt(
        plan={"original_user_request": "继续测试设计"},
        stage={
            "id": "test_design",
            "artifact": "test_design.md",
            "purpose": "测试设计",
            "depends_on": [],
        },
        source_pack={},
        flow_pack={},
        outline={},
        completed={},
        partial_seed="已完成范围与环境章节。",
    )

    assert "PARTIAL_OUTPUT_TO_CONTINUE" in prompt
    assert "已完成范围与环境章节" in prompt
    assert "禁止重复" in prompt


def test_combined_report_prompt_exposes_verified_path_allowlist():
    prompt = _regular_stage_prompt(
        plan={"original_user_request": "生成完整测试报告"},
        stage={
            "id": "artifact_1",
            "artifact": "report.md",
            "purpose": "组合报告",
            "depends_on": [],
            "output_contract": {
                "min_source_paths": 1,
                "min_test_paths": 1,
                "required_evidence_terms": ["ISCSI_LOGIN_TIMEOUT"],
                "forbidden_claim_terms": ["默认 60s"],
            },
        },
        source_pack={
            "evidence_cards": [
                {"file_path": "lib/iscsi/iscsi.c"},
                {"file_path": "test/iscsi_tgt/login.sh"},
            ],
            "verified_literals": [{
                "name": "ISCSI_LOGIN_TIMEOUT",
                "value": "30",
                "evidence_id": "SRC-01",
            }],
        },
        flow_pack={},
        outline={},
        completed={},
    )

    assert "VERIFIED_REPO_PATH_ALLOWLIST" in prompt
    assert "VERIFIED_LITERAL_FACTS" in prompt
    assert '"value": "30"' in prompt
    assert "交付件必须包含关键证据锚点: ISCSI_LOGIN_TIMEOUT" in prompt
    assert "禁止输出以下已知冲突结论: 默认 60s" in prompt
    assert "lib/iscsi/iscsi.c" in prompt
    assert "test/iscsi_tgt/login.sh" in prompt
    assert "任何源码或测试路径都必须逐字来自该白名单" in prompt


def test_black_box_stage_rules_require_executable_mcs_target_setup_command():
    rules = _stage_format_rules("black_box_cases", "black_box_cases.json")

    assert any("scripts/rpc.py iscsi_set_options -c 1" in rule for rule in rules)


def test_combined_report_finalizer_removes_unverified_paths_and_adds_verified_index():
    content = """我先规划一下任务，这些思考不应进入交付件。
```markdown
# 报告
## 分析范围与证据缺口
引用 `lib/iscsi/iscsi.c` 和虚构的 `lib/iscsi/session.c`。
## SFMEA
| 编号 | 故障模式 |
|---|---|
| FMEA-01 | 登录失败 |
## 黑盒测试用例
| 编号 | 前置条件 |
|---|---|
| TC-01 | target running |
```
"""
    source_pack = {
        "evidence_cards": [
            {
                "evidence_id": "SRC-01",
                "file_path": "lib/iscsi/iscsi.c",
                "classification": "source",
                "start_line": 10,
                "end_line": 20,
                "symbols": ["iscsi_login"],
                "sha256": "a" * 64,
            },
            {
                "evidence_id": "SRC-02",
                "file_path": "test/iscsi_tgt/login.sh",
                "classification": "test",
                "start_line": 1,
                "end_line": 12,
                "symbols": ["login_test"],
                "sha256": "b" * 64,
            },
        ]
    }

    finalized, removed = _finalize_combined_markdown_report(
        content=content,
        source_pack=source_pack,
        output_contract={"min_sfmea_rows": 1, "min_black_box_cases": 1},
    )

    assert "lib/iscsi/session.c" not in finalized
    assert "我先规划一下任务" not in finalized
    assert "```markdown" not in finalized
    assert finalized.startswith("# 报告")
    assert "session.c" in finalized
    assert "lib/iscsi/iscsi.c:10-20" in finalized
    assert "test/iscsi_tgt/login.sh:1-12" in finalized
    assert removed == ["lib/iscsi/session.c"]


def test_combined_report_finalizer_removes_reasoning_before_required_h2_section():
    content = """我们需要先思考输出合同、数量门禁和如何组织报告。

这是内部推演，不应该交付给用户。

## 1. 分析范围与证据缺口

引用 `lib/iscsi/iscsi.c`。

## 2. SFMEA

| ID | Failure Mode |
|---|---|
| FM-001 | login failure |

## 3. 黑盒测试用例

| ID | 场景 |
|---|---|
| BB-001 | login |
"""
    source_pack = {
        "evidence_cards": [{
            "file_path": "lib/iscsi/iscsi.c",
            "classification": "source",
            "start_line": 1,
            "end_line": 10,
            "sha256": "a" * 64,
        }]
    }

    finalized, _ = _finalize_combined_markdown_report(
        content=content,
        source_pack=source_pack,
        output_contract={
            "sections": ["分析范围与证据缺口", "SFMEA", "黑盒测试用例"],
            "min_sfmea_rows": 1,
            "min_black_box_cases": 1,
        },
    )

    assert finalized.startswith("# 测试分析报告\n\n## 1. 分析范围与证据缺口")
    assert "先思考输出合同" not in finalized
    assert "内部推演" not in finalized


def test_combined_report_finalizer_repairs_duplicated_markdown_table_prefix():
    content = """# 测试分析报告

## 业务流程

| 观测手段 | 可观测内容 | 相关证据 |
|----------|------------|----------|
| `iscsiadm -m session` | 会话状态 | 黑盒观测 |
| `test/iscsi_tgt/chap/chap_discovery.sh` 等脚本输出 | CH| `test/iscsi_tgt/chap/chap_discovery.sh` 等脚本输出 | CHAP 认证结果、Discovery 结果 | [FLOW-TEST-002] |

## SFMEA

| 编号 | 故障模式 |
|---|---|
| FMEA-01 | 登录失败 |

## 黑盒测试用例

| 编号 | 前置条件 |
|---|---|
| TC-01 | target running |
"""
    source_pack = {
        "evidence_cards": [{
            "file_path": "test/iscsi_tgt/chap/chap_discovery.sh",
            "classification": "test",
            "start_line": 1,
            "end_line": 20,
            "sha256": "a" * 64,
        }]
    }

    finalized, _ = _finalize_combined_markdown_report(
        content=content,
        source_pack=source_pack,
        output_contract={"min_sfmea_rows": 1, "min_black_box_cases": 1},
    )

    expected_row = (
        "| `test/iscsi_tgt/chap/chap_discovery.sh` 等脚本输出 "
        "| CHAP 认证结果、Discovery 结果 | [FLOW-TEST-002] |"
    )
    assert expected_row in finalized
    assert "| CH| `test/iscsi_tgt/chap/chap_discovery.sh`" not in finalized


def test_combined_report_finalizer_repairs_wrapped_markdown_table_cell():
    content = """# 测试分析报告

## SFMEA

| ID | Failure mode | Cause |
|---|---|---|
| SFMEA-016 | Duplicate key in same Login PDU rejected | Initiator sends 'SessionType=Normal
 SessionType=Discovery' and parser rejects it. |

## 黑盒测试用例

| 编号 | 前置条件 |
|---|---|
| TC-01 | target running |
"""

    finalized, _ = _finalize_combined_markdown_report(
        content=content,
        source_pack={"evidence_cards": []},
        output_contract={"min_sfmea_rows": 1, "min_black_box_cases": 1},
    )

    assert "SessionType=Normal<br>SessionType=Discovery" in finalized
    assert "\n SessionType=Discovery' and parser rejects it. |" not in finalized


def test_json_renderer_accepts_valid_json_with_an_unclosed_markdown_fence():
    rendered = _render_stage_artifact(
        '```json\n[{"case_id":"BB-001","test_dimension":"normal_path"}]',
        "black_box_cases.json",
    )

    assert rendered == [{"case_id": "BB-001", "test_dimension": "normal_path"}]


def test_deterministic_schema_repair_marks_missing_text_as_unverified():
    payload = [
        {
            "failure_mode": "CHAP failure",
            "cause": "credential mismatch",
            "source_evidence": ["SRC-01"],
        }
    ]
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["failure_mode", "cause", "detection", "severity"],
            "properties": {
                "failure_mode": {"type": "string"},
                "cause": {"type": "string"},
                "detection": {"type": "string"},
                "severity": {"type": "integer"},
            },
        },
    }

    repaired, fields = _deterministic_schema_repair(payload, schema)

    assert repaired[0]["failure_mode"] == "CHAP failure"
    assert repaired[0]["detection"].startswith("待验证：")
    assert "severity" not in repaired[0]
    assert fields == ["$[0].detection"]


def test_deterministic_schema_repair_does_not_invent_core_semantic_fields():
    payload = [{"cause": "credential mismatch"}]
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["failure_mode", "cause", "detection"],
            "properties": {
                "failure_mode": {"type": "string"},
                "cause": {"type": "string"},
                "detection": {"type": "string"},
            },
        },
    }

    repaired, fields = _deterministic_schema_repair(payload, schema)

    assert "failure_mode" not in repaired[0]
    assert repaired[0]["detection"].startswith("待验证：")
    assert fields == ["$[0].detection"]


def test_deterministic_schema_repair_wraps_string_for_string_array_field():
    payload = [
        {
            "case_id": "BB-01",
            "failure_diagnostics": "检查 target 日志与 pcap",
        }
    ]
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string"},
                "failure_diagnostics": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
    }

    repaired, fields = _deterministic_schema_repair(payload, schema)

    assert repaired[0]["failure_diagnostics"] == ["检查 target 日志与 pcap"]
    assert fields == ["$[0].failure_diagnostics"]


def test_deterministic_quality_claim_repair_normalizes_invalid_tcpdump_filter():
    payload = [
        {
            "failure_mode": "T+C invalid combination",
            "detection": (
                "tcpdump -w tc.pcap -i any 'port 3260 and iscsi.opcode==0x03'; "
                "tshark -r tc.pcap -Y 'iscsi.opcode==0x23'"
            ),
        }
    ]
    feedback = {
        "issues": [
            {
                "artifact": "sfmea.json",
                "code": "invalid_capture_filter",
                "claim_type": "command_executability",
            }
        ]
    }

    repaired, fields = _deterministic_quality_claim_repair(
        payload,
        artifact="sfmea.json",
        quality_feedback=feedback,
    )

    assert repaired[0]["detection"] == (
        "tcpdump -w tc.pcap -i any 'tcp port 3260'; "
        "tshark -r tc.pcap -Y 'iscsi.opcode==0x23'"
    )
    assert fields == ["$[0].detection"]


def test_deterministic_quality_claim_repair_enforces_mcs_raw_pdu_contract():
    payload = [
        {
            "case_id": "BB-025",
            "scenario_name": "MCS capacity",
            "preconditions": ["Initiator has iscsiadm and can create two connections"],
            "steps": [
                "Login second connection using raw-PDU harness or iscsiadm with same session identifier"
            ],
            "mapped_test_dir": (
                "test/iscsi_tgt/multiconnection/multiconnection.sh "
                "(warning: isolated test disk, data destruction risk)"
            ),
        }
    ]
    feedback = {
        "issues": [
            {
                "artifact": "black_box_cases.json",
                "code": "black_box_test_mapping_contradiction",
                "constraint_id": "iscsi_multiconnection_mapping_scope",
                "scenario": "BB-025 MCS capacity",
            }
        ]
    }

    repaired, fields = _deterministic_quality_claim_repair(
        payload,
        artifact="black_box_cases.json",
        quality_feedback=feedback,
    )

    assert repaired[0]["steps"] == [
        "Login second connection using raw-PDU harness with the same ISID, non-zero TSIH, "
        "a different CID, and the first socket kept online"
    ]
    assert "仅作环境搭建参考" in repaired[0]["mapped_test_dir"]
    assert "不覆盖同一 session 的 MCS" in repaired[0]["mapped_test_dir"]
    assert "$[0].steps" in fields
    assert "$[0].mapped_test_dir" in fields


def test_deterministic_iscsi_report_harness_supports_claimed_scenarios():
    from app.services.ai_staged_execution import _render_deterministic_combined_report
    from app.services.test_activity_contract import _audit_raw_pdu_scenario_capabilities

    report = _render_deterministic_combined_report(
        plan={
            "target": "SPDK iSCSI login",
            "original_user_request": "分析 SPDK iSCSI login",
        },
        source_pack={"repo_revision": "abc123", "evidence_cards": []},
        business_flow="Login request to response.",
        sfmea=[],
        black_box_cases=[
            {
                "case_id": "TC-01",
                "scenario_name": "T+C 非法组合与 C-bit 分片",
                "steps": "先发送 C=1 分片，再发送 C=0 收尾，并断言响应状态。",
            },
            {
                "case_id": "TC-02",
                "scenario_name": "Unsupported Version",
                "steps": "设置 version_max 与 version_min，断言拒绝状态。",
            },
            {
                "case_id": "TC-03",
                "scenario_name": "MCS same TSIH different CID",
                "steps": "保持首连接，复用响应 TSIH 建立第二连接并断言状态。",
            },
        ],
    )

    assert _audit_raw_pdu_scenario_capabilities(report) == []


def test_quality_repair_prompt_hides_gate_score_and_keeps_failed_claim_truth():
    from app.services.ai_staged_execution import _quality_feedback_for_artifact

    feedback = {
        "status": "needs_rework",
        "score": 37,
        "issue_count": 1,
        "quality_artifact": "quality_audit.json",
        "affected_artifacts": ["sfmea.json"],
        "issues": [
            {
                "code": "source_claim_contradicted",
                "artifact": "sfmea.json",
                "claim_id": "SFMEA-003:protocol_version_range",
                "message": "version 0..0 is supported",
                "source_truth": "ISCSI_VERSION=0x00",
            }
        ],
    }

    scoped = _quality_feedback_for_artifact(feedback, "sfmea.json")

    assert "score" not in scoped
    assert "status" not in scoped
    assert "quality_artifact" not in scoped
    assert scoped["issues"][0]["source_truth"] == "ISCSI_VERSION=0x00"


def test_deterministic_claim_repair_is_visible_in_materialized_report():
    payload = [
        {
            "case_id": "BB-025",
            "scenario_name": "MCS capacity",
            "preconditions": ["Initiator has iscsiadm and can create two connections"],
            "steps": [
                "Login second connection using raw-PDU harness or iscsiadm with same session identifier"
            ],
            "mapped_test_dir": "test/iscsi_tgt/multiconnection/multiconnection.sh",
        }
    ]
    feedback = {
        "issues": [
            {
                "artifact": "black_box_cases.json",
                "code": "black_box_test_mapping_contradiction",
                "constraint_id": "iscsi_multiconnection_mapping_scope",
                "scenario": "BB-025 MCS capacity",
            }
        ]
    }

    repaired, _ = _deterministic_quality_claim_repair(
        payload,
        artifact="black_box_cases.json",
        quality_feedback=feedback,
    )
    report = _render_deterministic_combined_report(
        plan={"original_user_request": "SPDK iSCSI Login 测试设计"},
        source_pack={"repo_revision": "abc123", "evidence_cards": []},
        business_flow="Login request -> response",
        sfmea=[],
        black_box_cases=repaired,
    )

    assert "仅作环境搭建参考" in report
    assert "不覆盖同一 session 的 MCS" in report
    assert "raw-PDU harness or iscsiadm" not in report


def test_black_box_stage_rules_name_every_required_dimension():
    rules = "\n".join(_stage_format_rules("black_box_cases", "black_box_cases.json"))

    for dimension in (
        "normal_path",
        "invalid_input",
        "resource_pressure",
        "timeout",
        "reconnect",
        "concurrency",
        "recovery",
        "performance",
    ):
        assert dimension in rules


def test_deterministic_business_flow_uses_quality_contract_section_names():
    report = render_business_flow_markdown(
        {
            "analysis_target": "iSCSI login",
            "repo_revision": "abc123",
            "entry_points": [],
            "steps": [],
            "error_flows": [],
            "cleanup_flows": [],
            "recovery_flows": [],
            "state_objects": [],
            "state_transitions": [],
            "evidence_gaps": [],
        }
    )

    assert "## 异常分支\n" in report
    assert "## 异常分支与恢复" not in report


def test_flow_outline_keeps_disconnected_call_components_separate():
    outline = build_flow_outline(
        {
            "analysis_target": "iSCSI login",
            "repo_revision": "abc123",
            "entry_points": [
                {"evidence_id": "ENTRY-A", "symbol": "login_start"},
                {"evidence_id": "ENTRY-X", "symbol": "rpc_config"},
            ],
            "call_edges": [
                {
                    "evidence_id": "EDGE-A",
                    "from_symbol": "login_start",
                    "to_symbol": "chap_negotiate",
                },
                {
                    "evidence_id": "EDGE-B",
                    "from_symbol": "chap_negotiate",
                    "to_symbol": "login_complete",
                },
                {
                    "evidence_id": "EDGE-X",
                    "from_symbol": "rpc_config",
                    "to_symbol": "json_decode",
                },
            ],
        }
    )

    assert len(outline["main_flows"]) == 2
    assert [step["to_symbol"] for step in outline["main_flows"][0]["steps"]] == [
        "chap_negotiate",
        "login_complete",
    ]
    assert [step["to_symbol"] for step in outline["main_flows"][1]["steps"]] == [
        "json_decode"
    ]
    assert any("单一端到端" in gap for gap in outline["evidence_gaps"])


def test_flow_outline_excludes_edges_not_reachable_from_verified_entries():
    outline = build_flow_outline(
        {
            "analysis_target": "iSCSI login",
            "repo_revision": "abc123",
            "entry_points": [
                {"evidence_id": "ENTRY-A", "symbol": "login_start"},
            ],
            "call_edges": [
                {
                    "evidence_id": "EDGE-A",
                    "from_symbol": "login_start",
                    "to_symbol": "chap_negotiate",
                },
                {
                    "evidence_id": "EDGE-X",
                    "from_symbol": "nvme_trace_rpc",
                    "to_symbol": "trace_register",
                },
            ],
        }
    )

    assert len(outline["main_flows"]) == 1
    assert [step["to_symbol"] for step in outline["steps"]] == ["chap_negotiate"]
    assert "nvme_trace_rpc" not in json.dumps(outline["main_flows"])
    assert any("不属于已验证入口可达分量" in gap for gap in outline["evidence_gaps"])


def test_flow_outline_does_not_promote_arbitrary_roots_when_entry_has_no_call_edge():
    outline = build_flow_outline(
        {
            "analysis_target": "iSCSI login",
            "repo_revision": "abc123",
            "entry_points": [{"evidence_id": "ENTRY-A", "symbol": "login_start"}],
            "call_edges": [
                {
                    "evidence_id": "EDGE-X",
                    "from_symbol": "unrelated_rpc",
                    "to_symbol": "trace_register",
                }
            ],
        }
    )

    assert len(outline["main_flows"]) == 1
    assert outline["main_flows"][0]["root_symbol"] == "login_start"
    assert "unrelated_rpc" not in json.dumps(outline["steps"])
    assert any("入口" in gap and "调用边" in gap for gap in outline["evidence_gaps"])


@pytest.mark.asyncio
async def test_provider_that_ignores_cancellation_cannot_break_stage_deadline(monkeypatch):
    monkeypatch.setattr(
        "app.services.ai_staged_execution.settings.regular_stage_cancel_grace_seconds",
        0.03,
    )
    release = asyncio.Event()

    class StubbornLLM:
        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue
            return LLMResponse(content="late", model="stubborn", usage={})

    started = time.monotonic()
    call = asyncio.create_task(
        _complete_with_cancellation(
            llm=StubbornLLM(),
            prompt="bounded",
            max_tokens=128,
            is_cancelled=None,
            timeout_seconds=0.02,
            single_attempt=True,
        )
    )
    done, _ = await asyncio.wait({call}, timeout=0.15)
    try:
        assert call in done, "the internal deadline must return without an outer wait_for"
        with pytest.raises(asyncio.TimeoutError):
            await call
        assert time.monotonic() - started < 0.15
    finally:
        release.set()
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_detached_provider_keeps_capacity_until_it_really_stops(monkeypatch):
    monkeypatch.setattr(
        "app.services.ai_staged_execution.settings.regular_stage_cancel_grace_seconds",
        0.01,
    )
    release = asyncio.Event()
    detached: list[asyncio.Task] = []
    capacity = _ProcessProviderCapacity(1)
    assert await capacity.acquire(0.1) is True

    class StubbornLLM:
        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue
            return LLMResponse(content="late", model="stubborn", usage={})

    with pytest.raises(asyncio.TimeoutError):
        await _complete_with_cancellation(
            llm=StubbornLLM(),
            prompt="bounded",
            max_tokens=128,
            is_cancelled=None,
            timeout_seconds=0.01,
            single_attempt=True,
            on_detached_task=detached.append,
        )
    capacity.release_after(detached)
    assert await capacity.acquire(0.03) is False
    release.set()
    await asyncio.sleep(0.02)
    assert await capacity.acquire(0.1) is True
    capacity.release()


@pytest.mark.asyncio
async def test_provider_capacity_wait_observes_task_cancellation_quickly():
    capacity = _ProcessProviderCapacity(1)
    assert await capacity.acquire(0.1) is True
    cancelled = False

    async def is_cancelled():
        return cancelled

    waiter = asyncio.create_task(capacity.acquire(0.35, is_cancelled=is_cancelled))
    await asyncio.sleep(0.02)
    cancelled = True
    started = time.monotonic()
    with pytest.raises(StagedExecutionCancelled):
        await waiter
    assert time.monotonic() - started < 0.12
    capacity.release()


def test_completed_stage_cache_requires_quality_promotion(tmp_path):
    cache_root = tmp_path / "cache"
    artifact_root = tmp_path / "run"
    stage_dir = artifact_root / "stages" / "sfmea"
    stage_dir.mkdir(parents=True)
    output = artifact_root / "sfmea.json"
    output.write_text("[]", encoding="utf-8")
    result = {
        "stage_id": "sfmea",
        "artifact": "sfmea.json",
        "status": "completed",
        "cache_key": "cache-key",
    }
    (stage_dir / "stage_result.json").write_text(json.dumps(result), encoding="utf-8")
    store_regular_stage_cache(
        cache_root=cache_root,
        cache_key="cache-key",
        artifact="sfmea.json",
        output_path=output,
        stage_result=result,
    )

    assert restore_regular_stage_cache(
        cache_root=cache_root,
        cache_key="cache-key",
        artifact="sfmea.json",
        output_path=tmp_path / "before.json",
    ) is None
    promoted = promote_regular_stage_caches(
        cache_root=cache_root,
        artifact_roots=[artifact_root],
        blocked_artifacts=set(),
    )
    assert promoted == ["sfmea.json"]
    assert restore_regular_stage_cache(
        cache_root=cache_root,
        cache_key="cache-key",
        artifact="sfmea.json",
        output_path=tmp_path / "after.json",
    ) is not None


def test_cache_promotion_rejects_a_replaced_candidate_with_same_key(tmp_path):
    cache_root = tmp_path / "cache"
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    for root, content in ((first_root, "first"), (second_root, "second")):
        (root / "stages" / "sfmea").mkdir(parents=True)
        output = root / "sfmea.json"
        output.write_text(content, encoding="utf-8")
        result = {
            "stage_id": "sfmea",
            "artifact": "sfmea.json",
            "status": "completed",
            "cache_key": "shared-key",
        }
        (root / "stages" / "sfmea" / "stage_result.json").write_text(
            json.dumps(result), encoding="utf-8"
        )
        store_regular_stage_cache(
            cache_root=cache_root,
            cache_key="shared-key",
            artifact="sfmea.json",
            output_path=output,
            stage_result=result,
        )

    promoted = promote_regular_stage_caches(
        cache_root=cache_root,
        artifact_roots=[first_root],
        blocked_artifacts=set(),
    )

    assert promoted == []
    assert restore_regular_stage_cache(
        cache_root=cache_root,
        cache_key="shared-key",
        artifact="sfmea.json",
        output_path=tmp_path / "restored.json",
    ) is None


def test_deliverable_quality_status_allows_cache_promotion():
    assert _quality_allows_cache_promotion("deliverable") is True
    assert _quality_allows_cache_promotion("invalid") is False


def test_quality_blocking_propagates_to_dependent_artifacts():
    blocked = _expand_quality_blocked_artifacts({"sfmea.json"})

    assert {
        "sfmea.json",
        "black_box_cases.json",
        "test_design.md",
        "risk_review.md",
    }.issubset(blocked)
    assert "business_flow.md" not in blocked


def test_concurrent_cache_writes_keep_one_valid_atomic_entry(tmp_path):
    cache_root = tmp_path / "cache"
    output = tmp_path / "business_flow.md"
    output.write_text("# verified flow\n", encoding="utf-8")
    result = {
        "stage_id": "business_flow",
        "artifact": "business_flow.md",
        "status": "completed",
        "cache_key": "shared-key",
    }

    def write_cache(_: int) -> None:
        store_regular_stage_cache(
            cache_root=cache_root,
            cache_key="shared-key",
            artifact="business_flow.md",
            output_path=output,
            stage_result=result,
            quality_status="verified",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write_cache, range(24)))

    restored = tmp_path / "restored.md"
    assert restore_regular_stage_cache(
        cache_root=cache_root,
        cache_key="shared-key",
        artifact="business_flow.md",
        output_path=restored,
    ) is not None
    assert restored.read_text(encoding="utf-8") == "# verified flow\n"
    assert not list(cache_root.glob(".*.tmp"))
    assert not list(cache_root.glob(".*.stale"))


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


def test_source_analysis_context_expands_to_combined_report_evidence_minimums():
    staged_context = _verified_source_context()
    staged_context["source_context"]["files"] = [
        {
            **staged_context["source_context"]["files"][index % 7],
            "file_path": (
                f"test/iscsi_tgt/case_{index}.sh"
                if index >= 6
                else f"lib/iscsi/source_{index}.c"
            ),
            "classification": "test" if index >= 6 else "source",
        }
        for index in range(10)
    ]
    plan = {
        "original_user_request": "分析 iSCSI login",
        "stages": [
            {
                "id": "artifact_1",
                "output_contract": {
                    "artifact": "report.md",
                    "min_source_paths": 6,
                    "min_test_paths": 4,
                },
            }
        ],
    }

    compact = build_source_analysis_context(
        plan=plan,
        staged_context=staged_context,
        max_files=6,
        excerpt_chars=1200,
        max_evidence_anchors=12,
    )

    assert len(compact["files"]) == 10
    assert sum(item["classification"] == "source" for item in compact["files"]) >= 6
    assert sum(item["classification"] == "test" for item in compact["files"]) >= 4


def test_business_flow_prompt_keeps_named_execution_inputs_and_tools():
    plan = build_staged_execution_plan(
        contract=_contract(),
        original_user_request="分析 NVMe TCP TLS 握手失败",
    )
    plan["execution_input_contract"] = {
        "goal": "分析 NVMe TCP TLS 握手失败",
        "user_inputs": [
            {"id": "mr_link", "name": "待分析 MR", "value": "https://git.example/mr/42"},
        ],
        "mcp": {"profiles": ["gitnexus", "cgc"]},
        "skills": {"selected": ["storage-test-design"]},
        "test_activity_contract": {
            "professional_constraints": [
                {"id": "tls-auth", "assertion": "必须覆盖双向认证失败", "evidence": ["TLS spec"]},
            ]
        },
    }

    prompt = _business_flow_stage_prompt(
        plan=plan,
        stage={"id": "business_flow", "artifact": "business_flow.md"},
        source_pack={"repo_revision": "abc", "evidence_cards": []},
        flow_pack={},
        outline={},
    )

    assert "待分析 MR" in prompt
    assert "https://git.example/mr/42" in prompt
    assert "gitnexus" in prompt and "cgc" in prompt
    assert "storage-test-design" in prompt
    assert "tls-auth" in prompt
    assert "必须覆盖双向认证失败" not in prompt


def test_business_flow_quality_repair_prompt_contains_feedback_and_previous_artifact():
    previous = "## 已有流程\n\n首个 Login payload 已注销 timer。"
    prompt = _business_flow_stage_prompt(
        plan={
            "original_user_request": "完整分析 iSCSI login",
            "quality_retry_feedback": {
                "affected_artifacts": ["report.md", "business_flow.md"],
                "issues": [
                    {
                        "artifact": "report.md",
                        "source_artifact": "assistant-output.md",
                        "code": "missing_iscsi_professional_scenarios",
                        "message": "缺少专业必测场景: Discovery 后 SendTargets",
                        "scenarios": ["Discovery 后 SendTargets"],
                    }
                ],
            },
        },
        stage={"id": "business_flow", "artifact": "business_flow.md"},
        source_pack={"repo_revision": "abc", "evidence_cards": []},
        flow_pack={},
        outline={},
        current_artifact_seed=previous,
    )

    assert "CURRENT_ARTIFACT_TO_REPAIR" in prompt
    assert previous in prompt
    assert "MANDATORY_QUALITY_REPAIR_CHECKLIST" in prompt
    assert "Discovery 后 SendTargets" in prompt
    assert "只返回面向用户的最终流程叙述" in prompt
    assert "不要描述旧版本" in prompt


def test_business_flow_quality_repair_rebuilds_the_deterministic_base():
    outline = {
        "version": "flow-outline-v1",
        "target": "iSCSI login",
        "main_flows": [],
        "branches": [],
        "failure_paths": [],
        "observability": [],
        "evidence_ids": [],
    }

    rebuilt = _business_flow_deterministic_base(
        outline=outline,
        existing_content="# 旧流程\n\n错误叙述，不应继续拼接。\n",
    )

    assert "旧流程" not in rebuilt
    assert "错误叙述" not in rebuilt
    assert "关键业务流程分析" in rebuilt


def test_business_flow_narrative_extractor_discards_model_meta_commentary():
    raw = (
        "我们被要求修复四个问题。先检查为什么审计失败，接下来写最终结果。\n\n"
        "```markdown\n"
        "# iSCSI Login 业务流程补充\n\n"
        "## Discovery 后 SendTargets\n\n"
        "外部发起方完成 Discovery login 后发送 SendTargets。\n"
        "```\n"
    )

    cleaned = _extract_business_flow_narrative(raw)

    assert cleaned.startswith("# iSCSI Login 业务流程补充")
    assert "我们被要求" not in cleaned
    assert "接下来写" not in cleaned
    assert "Discovery 后 SendTargets" in cleaned


def test_business_flow_narrative_extractor_truncates_appended_model_meta_commentary():
    raw = (
        "# iSCSI Login 业务流程补充\n\n"
        "## Discovery 后 SendTargets\n\n"
        "Discovery Login 不返回 TargetAddress；登录后发送 Text Request，"
        "TargetAddress 出现在 Text Response。"
        "我们被要求修复 business_flow.md，修复两个问题：\n\n"
        "当前业务流产物已经是一个半成品（PARTIAL_OUTPUT_TO_CONTINUE）。\n"
    )

    cleaned = _extract_business_flow_narrative(raw)

    assert cleaned.endswith("TargetAddress 出现在 Text Response。")
    assert "我们被要求" not in cleaned
    assert "PARTIAL_OUTPUT_TO_CONTINUE" not in cleaned


def test_compact_iscsi_contract_keeps_only_lint_identity_not_professional_assertions():
    from app.services.test_activity_contract import build_test_activity_contract

    contract = build_test_activity_contract(
        target="iSCSI Login 完整流程 SFMEA 黑盒测试设计",
        repo_path="/Volumes/Media/dpdk/spdk",
        workflow_outputs=[
            {"artifact": "report.md", "type": "combined_test_report"},
        ],
    )

    compact = _compact_execution_input_contract(
        {"goal": contract["target"], "test_activity_contract": contract}
    )
    serialized = json.dumps(compact, ensure_ascii=False)

    assert len(serialized) < 15000
    assert "iscsi_login_timer_after_first_pdu" in serialized
    assert "iscsi_login_error_flags_cleared" in serialized
    assert "iscsi_multiconnection_scenario_semantics" in serialized
    assert "iscsi_fuzz_calsoft_semantic_mapping" in serialized
    assert "首个 Login payload 开始处理时注销 login_timer" not in serialized
    assert '"role": "lint_only_not_generation_context"' in serialized


def test_compact_execution_contract_has_global_budget_without_dropping_input_identity():
    inputs = [
        {
            "id": f"input_{index:02d}",
            "name": f"输入材料 {index:02d}",
            "value": (f"material-{index}-" * 3000),
        }
        for index in range(64)
    ]
    constraints = [
        {
            "id": f"constraint-{index}",
            "assertion": f"专业约束 {index} " + ("detail " * 500),
            "evidence": [f"evidence-{index}"],
        }
        for index in range(12)
    ]

    compact = _compact_execution_input_contract(
        {
            "goal": "完整测试活动",
            "user_inputs": inputs,
            "test_activity_contract": {"professional_constraints": constraints},
        }
    )
    serialized = json.dumps(compact, ensure_ascii=False)

    assert len(serialized) < 60000
    assert all(item["id"] in serialized for item in inputs)
    assert "sha256" in serialized and "source_ref" in serialized
    assert "constraint-11" in serialized
    assert '"professional_constraints":' not in serialized
    assert "professional_constraint_catalog" in serialized

    more_inputs = [
        {"id": f"extra_{index:03d}", "name": f"额外输入 {index}", "value": "x" * 5000}
        for index in range(100)
    ]
    overflow_serialized = json.dumps(
        _compact_execution_input_contract({"user_inputs": more_inputs}),
        ensure_ascii=False,
    )
    assert len(overflow_serialized) < 60000
    assert all(item["id"] in overflow_serialized for item in more_inputs)

    named_contract = {
        "input_materials": {
            "read_order": [f"read-order-{index:03d}" for index in range(100)],
            "materials": [
                {
                    "input_id": f"material_{index:03d}",
                    "name": f"材料 {index}",
                    "content": "m" * 4000,
                }
                for index in range(40)
            ],
        },
        "mcp": {
            "profiles": [
                {"id": f"mcp_{index:03d}", "label": f"MCP {index}"}
                for index in range(100)
            ]
        },
        "skills": {
            "selected": [
                {"id": f"skill_{index:03d}", "label": f"Skill {index}"}
                for index in range(100)
            ]
        },
    }
    named_serialized = json.dumps(
        _compact_execution_input_contract(named_contract),
        ensure_ascii=False,
    )
    assert len(named_serialized) < 60000
    for identity in (
        "read-order-099",
        "material_039",
        "mcp_099",
        "skill_099",
    ):
        assert identity in named_serialized


def test_flow_evidence_pack_uses_revision_pinned_bounded_git_grep(tmp_path):
    repo = tmp_path / "repo"
    (repo / "lib").mkdir(parents=True)
    (repo / "test").mkdir()
    caller = "int caller(void) {\n    return spdk_iscsi_login();\n}\n"
    login = (
        "int spdk_iscsi_login(void) {\n"
        "    if (authenticate_login() != 0) {\n"
        "        return login_error();\n"
        "    }\n"
        "    return 0;\n"
        "}\n"
    )
    test_source = "void test_login(void) { spdk_iscsi_login(); }\n"
    (repo / "lib" / "caller.c").write_text(caller)
    (repo / "lib" / "false_caller.c").write_text(
        "int false_caller(void) {\n"
        "    // spdk_iscsi_login is documented here but is not called.\n"
        "    return 0;\n"
        "}\n"
    )
    (repo / "lib" / "login.c").write_text(login)
    (repo / "test" / "login_ut.c").write_text(test_source)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=CodeTalk Test",
            "-c",
            "user.email=codetalk@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=repo,
        check=True,
    )
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    source_pack = {
        "analysis_target": "iSCSI login",
        "repo_revision": revision,
        "source_scope": {
            "repo": str(repo),
            "source_files": ["lib/login.c"],
            "test_files": ["test/login_ut.c"],
            "evidence_gaps": [],
        },
        "evidence_cards": [
            {
                "evidence_id": "SRC-01",
                "file_path": "lib/login.c",
                "classification": "source",
                "start_line": 1,
                "end_line": 7,
                "excerpt": login,
                "symbols": ["spdk_iscsi_login"],
                "matched_terms": ["iscsi", "login"],
                "sha256": hashlib.sha256(login.encode()).hexdigest(),
                "source": "local-source-search",
            }
        ],
        "tool_summaries": {
            "gitnexus": "login call graph available",
            "cgc": "authentication branch available",
        },
    }

    pack = build_flow_evidence_pack(source_pack, repo_path=str(repo), max_files=6)

    assert pack["repo_revision_verified"] is True
    assert {item["provider"] for item in pack["provider_status"]} == {
        "gitnexus",
        "cgc",
        "git-grep",
    }
    assert any(
        edge.get("from_symbol") == "caller"
        and edge.get("to_symbol") == "spdk_iscsi_login"
        and edge.get("provider") == "git-grep"
        for edge in pack["call_edges"]
    )
    assert not any(
        edge.get("from_symbol") == "false_caller"
        and edge.get("to_symbol") == "spdk_iscsi_login"
        for edge in pack["call_edges"]
    )
    assert any(
        item.get("file_path") == "test/login_ut.c"
        and item.get("provider") == "git-grep"
        for item in pack["related_tests"]
    )
    for key in (
        "entry_points",
        "call_edges",
        "state_objects",
        "state_transitions",
        "conditions",
        "error_paths",
        "cleanup_paths",
        "recovery_paths",
        "related_tests",
    ):
        assert all(len(item.get("sha256") or "") == 64 for item in pack[key])


def test_flow_evidence_ignores_calls_inside_block_comments_and_strings(tmp_path):
    repo = tmp_path / "repo-comments"
    (repo / "lib").mkdir(parents=True)
    source = (
        "int login(void) {\n"
        "  /* fake_cleanup();\n"
        "     int fake_caller(void) { fake_reconnect(); } */\n"
        '  const char *message = "fake_auth()";\n'
        "  return real_auth();\n"
        "}\n"
        "int real_auth(void) { return 0; }\n"
    )
    (repo / "lib" / "login.c").write_text(source)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=CodeTalk Test", "-c", "user.email=codetalk@example.invalid", "commit", "-qm", "fixture"],
        cwd=repo,
        check=True,
    )
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    source_pack = {
        "analysis_target": "login",
        "repo_revision": revision,
        "source_scope": {"repo": str(repo), "source_files": ["lib/login.c"], "test_files": []},
        "evidence_cards": [{
            "evidence_id": "SRC-01",
            "file_path": "lib/login.c",
            "classification": "source",
            "start_line": 1,
            "end_line": 7,
            "excerpt": source,
            "symbols": ["login"],
            "sha256": hashlib.sha256(source.encode()).hexdigest(),
        }],
    }

    pack = build_flow_evidence_pack(source_pack, repo_path=str(repo), max_files=4)
    called = {str(item.get("to_symbol") or "") for item in pack["call_edges"]}

    assert "real_auth" in called
    assert "fake_cleanup" not in called
    assert "fake_reconnect" not in called
    assert "fake_auth" not in called
    assert not any(
        item.get("from_symbol") == "fake_caller" for item in pack["call_edges"]
    )


def test_flow_evidence_does_not_treat_adjacent_unlisted_definition_as_reverse_call():
    source_pack = {
        "analysis_target": "iSCSI CHAP construction",
        "repo_revision": "",
        "source_scope": {},
        "evidence_cards": [
            {
                "evidence_id": "SRC-001",
                "classification": "source",
                "file_path": "lib/iscsi/tgt_node.c",
                "start_line": 1026,
                "end_line": 1059,
                "sha256": "abc",
                "symbols": ["iscsi_check_chap_params"],
                "excerpt": """bool
iscsi_check_chap_params(bool disable)
{
    return !disable;
}

struct node *iscsi_tgt_node_construct(bool disable)
{
    if (!iscsi_check_chap_params(disable)) {
        return NULL;
    }
    return allocate_node();
}
""",
            }
        ],
    }

    pack = build_flow_evidence_pack(source_pack, max_files=1)
    edges = {
        (edge["from_symbol"], edge["to_symbol"])
        for edge in pack["call_edges"]
    }

    assert ("iscsi_tgt_node_construct", "iscsi_check_chap_params") in edges
    assert ("iscsi_check_chap_params", "iscsi_tgt_node_construct") not in edges


def test_flow_evidence_ignores_truncated_next_function_declaration():
    source_pack = {
        "analysis_target": "iSCSI CHAP construction",
        "repo_revision": "",
        "source_scope": {},
        "evidence_cards": [
            {
                "evidence_id": "SRC-001",
                "classification": "source",
                "file_path": "lib/iscsi/tgt_node.c",
                "start_line": 1026,
                "end_line": 1044,
                "sha256": "abc",
                "symbols": ["iscsi_check_chap_params"],
                "excerpt": """bool
iscsi_check_chap_params(bool disable)
{
    return !disable;
}

struct node *iscsi_tgt_node_construct(int target_index,
""",
            }
        ],
    }

    pack = build_flow_evidence_pack(source_pack, max_files=1)

    assert not any(
        edge["from_symbol"] == "iscsi_check_chap_params"
        and edge["to_symbol"] == "iscsi_tgt_node_construct"
        for edge in pack["call_edges"]
    )


def test_flow_discovery_prioritizes_verified_symbols_over_incidental_library_calls(tmp_path):
    repo = tmp_path / "repo-priority"
    (repo / "lib").mkdir(parents=True)
    noisy_calls = "\n".join(f"    helper_{index}();" for index in range(20))
    noisy_source = f"int entry(void) {{\n{noisy_calls}\n    return 0;\n}}\n"
    important_source = (
        "int important_check(void) { return 1; }\n"
        "int construct_target(\n"
        "        int first_argument,\n"
        "        int second_argument,\n"
        "        int third_argument,\n"
        "        int fourth_argument)\n"
        "{\n"
        "    return important_check();\n"
        "}\n"
    )
    (repo / "lib" / "noisy.c").write_text(noisy_source)
    (repo / "lib" / "important.c").write_text(important_source)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=CodeTalk Test",
            "-c",
            "user.email=codetalk@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=repo,
        check=True,
    )
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    source_pack = {
        "analysis_target": "important check",
        "repo_revision": revision,
        "source_scope": {"source_files": ["lib/noisy.c", "lib/important.c"]},
        "evidence_cards": [
            {
                "evidence_id": "SRC-001",
                "classification": "source",
                "file_path": "lib/noisy.c",
                "start_line": 1,
                "end_line": 23,
                "excerpt": noisy_source,
                "symbols": ["entry"],
                "sha256": hashlib.sha256(noisy_source.encode()).hexdigest(),
            },
            {
                "evidence_id": "SRC-002",
                "classification": "source",
                "file_path": "lib/important.c",
                "start_line": 1,
                "end_line": 1,
                "excerpt": "int important_check(void) { return 1; }\n",
                "symbols": ["important_check"],
                "sha256": hashlib.sha256(important_source.encode()).hexdigest(),
            },
        ],
    }

    pack = build_flow_evidence_pack(source_pack, repo_path=str(repo), max_files=2)

    assert any(
        edge["from_symbol"] == "construct_target"
        and edge["to_symbol"] == "important_check"
        for edge in pack["call_edges"]
    )


def test_flow_evidence_pack_bounds_git_grep_to_evidence_module(tmp_path):
    repo = tmp_path / "repo"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "lib" / "nvme").mkdir(parents=True)
    login = "int iscsi_login(void) { return authenticate_login(); }\n"
    (repo / "lib" / "iscsi" / "login.c").write_text(login)
    (repo / "lib" / "iscsi" / "caller.c").write_text(
        "int iscsi_accept(void) { return iscsi_login(); }\n"
    )
    (repo / "lib" / "nvme" / "trace.c").write_text(
        "int nvme_trace_rpc(void) { return iscsi_login(); }\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=CodeTalk Test",
            "-c",
            "user.email=codetalk@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=repo,
        check=True,
    )
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    source_pack = {
        "analysis_target": "iSCSI login",
        "repo_revision": revision,
        "source_scope": {
            "source_files": ["lib/iscsi/login.c"],
            "test_files": [],
            "evidence_gaps": [],
        },
        "evidence_cards": [
            {
                "evidence_id": "SRC-01",
                "file_path": "lib/iscsi/login.c",
                "classification": "source",
                "start_line": 1,
                "end_line": 1,
                "excerpt": login,
                "symbols": ["iscsi_login"],
                "matched_terms": ["iscsi", "login"],
                "sha256": hashlib.sha256(login.encode()).hexdigest(),
                "source": "local-source-search",
            }
        ],
    }

    pack = build_flow_evidence_pack(source_pack, repo_path=str(repo), max_files=6)

    callers = {
        edge.get("from_symbol")
        for edge in pack["call_edges"]
        if edge.get("to_symbol") == "iscsi_login"
    }
    assert "iscsi_accept" in callers
    assert "nvme_trace_rpc" not in callers


def test_local_source_context_ignores_generic_product_terms_and_keeps_test_symbols(tmp_path):
    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    (repo / "include" / "spdk").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "login.c").write_text(
        "int iscsi_login(void) { return iscsi_authenticate(); }\n"
    )
    (repo / "test" / "iscsi_tgt" / "login.sh").write_text(
        "login_recovery_test() {\n  run_iscsi_login --reconnect\n}\n"
    )
    (repo / "include" / "spdk" / "env_dpdk.h").write_text(
        "#define SPDK_DPDK_GENERIC 1\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=CodeTalk Test",
            "-c",
            "user.email=codetalk@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=repo,
        check=True,
    )

    context = build_local_source_context(
        repo_path=str(repo),
        query=(
            "基于 SPDK 源码分析 iSCSI login、认证与恢复，并输出流程、SFMEA、"
            "black box cases 和 code evidence"
        ),
        limit=6,
    )

    selected = {item["file_path"]: item for item in context["files"]}
    assert "lib/iscsi/login.c" in selected
    assert "test/iscsi_tgt/login.sh" in selected
    assert "include/spdk/env_dpdk.h" not in selected
    assert all(item["symbols"] for item in selected.values())
    assert "login_recovery_test" in selected["test/iscsi_tgt/login.sh"]["symbols"]


def test_source_analysis_context_preserves_validated_custom_test_classification():
    staged_context = _verified_source_context()
    staged_context["source_context"]["files"][0]["file_path"] = "qa/login_case.sh"
    staged_context["source_context"]["files"][0]["classification"] = "test"

    compact = build_source_analysis_context(
        plan={"original_user_request": "分析 login"},
        staged_context=staged_context,
    )

    assert compact["files"][0]["classification"] == "test"


def test_source_analysis_context_keeps_a_test_card_when_compacting_ranked_files():
    staged_context = _verified_source_context()
    files = staged_context["source_context"]["files"]
    for item in files:
        item["classification"] = "source"
        item["file_path"] = f"lib/iscsi/{Path(item['file_path']).name}"
    files[-1]["classification"] = "test"
    files[-1]["file_path"] = "test/iscsi_tgt/login.sh"

    compact = build_source_analysis_context(
        plan={"original_user_request": "分析 iSCSI login"},
        staged_context=staged_context,
        max_files=6,
    )

    assert len(compact["files"]) == 6
    assert any(item["classification"] == "test" for item in compact["files"])
    assert "最高相关证据中缺少测试目录文件" not in compact["evidence_gaps"]


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


def test_source_evidence_pack_extracts_verified_constant_literals():
    pack = build_source_evidence_pack({
        "analysis_target": "iSCSI login",
        "repo_revision": "abc123",
        "files": [
            {
                "evidence_id": "SRC-01",
                "file_path": "lib/iscsi/iscsi.h",
                "classification": "source",
                "start_line": 100,
                "end_line": 110,
                "excerpt": "#define ISCSI_LOGIN_TIMEOUT 30 /* seconds */\n",
                "symbols": ["ISCSI_LOGIN_TIMEOUT"],
                "sha256": "a" * 64,
            },
            {
                "evidence_id": "SRC-02",
                "file_path": "include/spdk/iscsi_spec.h",
                "classification": "source",
                "start_line": 520,
                "end_line": 530,
                "excerpt": "#define ISCSI_LOGIN_AUTHENT_FAIL 0x01\n",
                "symbols": ["ISCSI_LOGIN_AUTHENT_FAIL"],
                "sha256": "b" * 64,
            },
        ],
    })

    assert pack["verified_literals"] == [
        {
            "name": "ISCSI_LOGIN_TIMEOUT",
            "value": "30",
            "evidence_id": "SRC-01",
            "file_path": "lib/iscsi/iscsi.h",
            "line": 100,
        },
        {
            "name": "ISCSI_LOGIN_AUTHENT_FAIL",
            "value": "0x01",
            "evidence_id": "SRC-02",
            "file_path": "include/spdk/iscsi_spec.h",
            "line": 520,
        },
    ]


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
        "flow_evidence_pack",
        "flow_outline",
        "business_flow",
        "sfmea",
    ]
    assert plan["stages"][5]["depends_on"] == ["flow_outline"]
    sfmea_stage = plan["stages"][6]
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
        "flow_evidence_pack",
        "flow_outline",
        "business_flow",
        "sfmea",
        "black_box_cases",
        "test_strategy",
        "test_design",
    ]
    assert plan["stages"][4]["depends_on"] == ["source_analysis", "flow_outline"]


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
        original_user_request="只点名风险复核也必须先完成流程骨架和 SFMEA",
    )

    assert [stage["id"] for stage in plan["stages"]] == [
        "source_analysis",
        "flow_evidence_pack",
        "flow_outline",
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
    assert result["completed_stages"] == 7
    assert (tmp_path / "staged_execution_plan.json").exists()
    assert (tmp_path / "stages" / "source_analysis" / "stage_result.json").exists()
    for artifact in _contract()["required_outputs"]:
        assert (tmp_path / artifact).is_file()
        assert (tmp_path / artifact).stat().st_size > 0
    full_prompts = [prompt for prompt in llm.prompts if "SMALL_FORMAT_REPAIR" not in prompt]
    assert all("第一行：完整 iSCSI login 测试设计" in prompt for prompt in full_prompts)
    assert all("第二行：必须保留全部输入" in prompt for prompt in full_prompts)
    source_prompt = next(prompt for prompt in llm.prompts if "STAGE_ID: source_analysis" in prompt)
    assert source_prompt.count(original) == 1
    assert "quality_gates" not in source_prompt
    assert "black_box_boundary" not in source_prompt
    assert progress[-1]["status"] == "completed"
    sfmea_prompt = next(prompt for prompt in llm.prompts if "STAGE_ID: sfmea" in prompt)
    assert "flow_outline.json" in sfmea_prompt
    assert "lib/iscsi/iscsi.c:100" in sfmea_prompt
    assert "CURRENT_STAGE_ONLY" in sfmea_prompt
    assert "不要在当前响应中生成其他阶段" in sfmea_prompt
    flow_prompt = next(
        prompt for prompt in llm.prompts if "STAGE_ID: business_flow" in prompt
    )
    assert "BUSINESS_FLOW_CONTEXT" in flow_prompt
    assert "flow_evidence_pack" in flow_prompt
    assert "flow_outline" in flow_prompt
    assert "quality_gates" not in flow_prompt
    assert "black_box_boundary" not in flow_prompt


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
    assert sfmea_result["attempt_count"] == 1
    assert sfmea_result["repair_attempt_count"] == 1
    assert sfmea_result["full_retry_performed"] is False


@pytest.mark.asyncio
async def test_source_analysis_is_bounded_and_truncated_attempts_are_diagnosable(tmp_path):
    class TruncatedLLM:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.max_tokens: list[int] = []

        async def complete(self, messages, max_tokens=4096, temperature=0.2):
            self.prompts.append(messages[-1]["content"])
            self.max_tokens.append(max_tokens)
            if len(self.prompts) == 2:
                return LLMResponse(
                    content=json.dumps(
                        {
                            "ranked_evidence_ids": ["SRC-01", "SRC-02"],
                            "gap_evidence_ids": ["SRC-06"],
                        }
                    ),
                    model="repair-test",
                    usage={},
                    truncated=False,
                    finish_reason="stop",
                )
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
    assert llm.max_tokens[1] <= 600
    assert "最多 12 个证据锚点" in llm.prompts[0]
    assert "JSON 总长度不得超过 1200 字符" in llm.prompts[0]
    assert "x" * 5000 not in llm.prompts[0]
    stage_dir = tmp_path / "stages" / "source_analysis"
    assert (stage_dir / "raw_output_attempt_1.txt").read_text(encoding="utf-8").startswith(
        "# 未完成的源码分析"
    )
    assert not (stage_dir / "raw_output_attempt_2.txt").exists()
    assert (stage_dir / "raw_output_repair.txt").exists()
    stage_result = json.loads((stage_dir / "stage_result.json").read_text(encoding="utf-8"))
    assert stage_result["attempt_count"] == 1
    assert stage_result["prompt_characters_before_compaction"] >= 200000
    assert stage_result["prompt_characters"] < 15000
    assert stage_result["prompt_estimated_tokens"] < 4000
    assert stage_result["finish_reason"] == "repair_stop"
    assert stage_result["full_retry_performed"] is False
    assert stage_result["repair_attempt_count"] == 1
    assert stage_result["degraded"] is False
    assert "x" * 5000 not in llm.prompts[1]


@pytest.mark.asyncio
async def test_source_analysis_enhancement_renders_only_verified_ranking(tmp_path):
    class RankingLLM:
        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            return LLMResponse(
                content=json.dumps(
                    {
                        "ranked_evidence_ids": ["SRC-02", "SRC-01"],
                        "gap_evidence_ids": ["SRC-06"],
                    }
                ),
                model="ranking-test",
                usage={"completion_tokens": 40},
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
        llm=RankingLLM(),
        plan=plan,
        artifact_dir=tmp_path,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
    )

    report = (tmp_path / "source_analysis.md").read_text(encoding="utf-8")
    enhancement = report.split("## 模型排序、归纳与缺口标记", 1)[1]
    assert enhancement.index("SRC-02") < enhancement.index("SRC-01")
    assert "SRC-06" in enhancement
    assert "需要补充证据" in enhancement
    assert "ranked_evidence_ids" not in enhancement


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
                    content='{"ranked_evidence_ids": ["SRC-01"',
                    model="repair-test",
                    usage={"completion_tokens": 20},
                    finish_reason="stop",
                )
            return LLMResponse(
                content=json.dumps(
                    {
                        "ranked_evidence_ids": ["SRC-01"],
                        "gap_evidence_ids": ["SRC-02"],
                    }
                ),
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
    assert "JSON 未闭合或格式错误" in llm.prompts[1]
    stage_result = json.loads(
        (tmp_path / "stages" / "source_analysis" / "stage_result.json").read_text()
    )
    assert stage_result["attempt_count"] == 1
    assert stage_result["repair_attempt_count"] == 1
    assert stage_result["full_retry_performed"] is False
    assert stage_result["degraded"] is False
    assert stage_result["output_tokens"] == 50
    report = (tmp_path / "source_analysis.md").read_text(encoding="utf-8")
    assert "SRC-01" in report
    assert "SRC-02" in report
    assert "ranked_evidence_ids" not in report


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_partial_json",
    [
        '{"ranked_evidence_ids":["SRC-01"],"gap_evidence_ids":[],"note":"hello"',
        '{"ranked_evidence_ids":["SRC-01"],"gap_evidence_ids":[] trailing prose',
    ],
)
async def test_source_analysis_does_not_repair_semantic_contract_violations(
    tmp_path,
    invalid_partial_json,
):
    class SemanticViolationLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            self.calls += 1
            return LLMResponse(
                content=invalid_partial_json,
                model="semantic-violation-test",
                usage={"completion_tokens": 30},
                finish_reason="stop",
            )

    llm = SemanticViolationLLM()
    contract = _contract()
    contract["required_outputs"] = ["source_scope.json", "evidence_cards.json"]
    plan = build_staged_execution_plan(
        contract=contract,
        original_user_request="reject semantic repair",
    )

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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unverified_output",
    [
        "- `lib/invented.c:99` 中 `fake_login()` 负责恢复。",
        "lib/iscsi/iscsi.c line 999 调用 fake_login(conn)",
        "认证失败后系统必然重启控制器",
        '{"ranked_evidence_ids":["SRC-99"],"gap_evidence_ids":[]}',
        '{"ranked_evidence_ids":["SRC-01"],"note":"lib/invented.c"}',
    ],
)
async def test_source_analysis_rejects_unverified_model_paths_without_repair(
    tmp_path,
    unverified_output,
):
    class HallucinatingLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            self.calls += 1
            return LLMResponse(
                content=unverified_output,
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
    assert unverified_output not in report


@pytest.mark.asyncio
async def test_source_analysis_skips_provider_after_total_budget_is_spent(
    tmp_path,
    monkeypatch,
):
    from app.services import ai_staged_execution as staged_module

    original = staged_module.build_source_analysis_context

    def slow_context(**kwargs):
        time.sleep(0.5)
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
    assert result["duration_ms"] < 150
    assert (tmp_path / "source_analysis.md").is_file()


@pytest.mark.asyncio
async def test_source_analysis_context_timeout_uses_io_free_memory_fallback(
    tmp_path,
    monkeypatch,
):
    from app.services import ai_staged_execution as staged_module

    original = staged_module._project_source_analysis_context

    def blocked_projector(**kwargs):
        time.sleep(0.5)
        return original(**kwargs)

    monkeypatch.setattr(
        staged_module,
        "_project_source_analysis_context",
        blocked_projector,
    )
    llm = _StageLLM()
    contract = _contract()
    contract["required_outputs"] = ["source_scope.json", "evidence_cards.json"]
    plan = build_staged_execution_plan(
        contract=contract,
        original_user_request="hard context timeout",
    )

    started = time.monotonic()
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
    elapsed_ms = (time.monotonic() - started) * 1000

    result = json.loads(
        (tmp_path / "stages" / "source_analysis" / "stage_result.json").read_text()
    )
    assert elapsed_ms < 150
    assert llm.calls_by_stage.get("source_analysis", 0) == 0
    assert result["degradation_reason"] == "total_budget_exceeded_during_context"
    assert result["quality_gate"]["sha256_validated_count"] > 0


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
async def test_source_analysis_cache_reuses_validated_pack_without_provider_call(tmp_path, monkeypatch):
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

    class CacheMustNotWaitForCapacity:
        async def acquire(self, *args, **kwargs):
            raise AssertionError("cache hit must not wait for Provider capacity")

        def release_after(self, tasks):
            raise AssertionError("cache hit must not release unacquired capacity")

    monkeypatch.setattr(
        "app.services.ai_staged_execution._shared_provider_capacity",
        lambda: CacheMustNotWaitForCapacity(),
    )
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
async def test_source_analysis_cache_rejects_legacy_free_text_contract(tmp_path):
    from app.services.ai_staged_execution import _sha256_path

    contract = _contract()
    contract["required_outputs"] = ["source_scope.json", "evidence_cards.json"]
    plan = build_staged_execution_plan(contract=contract, original_user_request="cache-v3")
    plan["workflow_version"] = "workflow-cache-v3"
    cache_dir = tmp_path / "cache"
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    await execute_staged_builtin_plan(
        llm=_StageLLM(),
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
    legacy_report = "# old cache\n\nlib/invented.c:999 fake_login()\n"
    (entry / "source_analysis.md").write_text(legacy_report, encoding="utf-8")
    metadata_path = entry / "cache_metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["version"] = "source-analysis-cache-v2"
    metadata["artifact_sha256"]["source_analysis.md"] = _sha256_path(
        entry / "source_analysis.md"
    )
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    second_llm = _StageLLM()

    await execute_staged_builtin_plan(
        llm=second_llm,
        plan=plan,
        artifact_dir=second_dir,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
        source_analysis_cache_dir=cache_dir,
    )

    result = json.loads(
        (second_dir / "stages" / "source_analysis" / "stage_result.json").read_text()
    )
    assert result["cache_status"] == "miss"
    assert second_llm.calls_by_stage["source_analysis"] == 1
    assert legacy_report not in (second_dir / "source_analysis.md").read_text()


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
    third_dir = tmp_path / "third"
    first_llm = _StageLLM()
    second_llm = _StageLLM()
    third_llm = _StageLLM()

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

    await execute_staged_builtin_plan(
        llm=third_llm,
        plan=plan,
        artifact_dir=third_dir,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
        source_analysis_cache_dir=cache_dir,
    )
    third_result = json.loads(
        (third_dir / "stages" / "source_analysis" / "stage_result.json").read_text()
    )
    assert third_llm.calls_by_stage.get("source_analysis", 0) == 0
    assert third_result["cache_status"] == "hit"


@pytest.mark.asyncio
async def test_ready_downstream_stages_execute_in_parallel(tmp_path, monkeypatch):
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

    monkeypatch.setattr("app.services.ai_staged_execution.settings.llm_max_concurrency", 2)
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
async def test_regular_stage_provider_timeout_uses_partial_markdown_and_never_full_retries(
    tmp_path,
    monkeypatch,
):
    events: list[dict] = []
    monkeypatch.setattr(
        "app.services.ai_staged_execution.settings.regular_stage_heartbeat_seconds", 0.01
    )

    class SlowStreamingLLM:
        def __init__(self) -> None:
            self.complete_calls_by_stage: dict[str, int] = {}
            self.stream_calls = 0
            self.cancelled = asyncio.Event()

        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            prompt = messages[-1]["content"]
            stage = next(
                line.split(":", 1)[1].strip()
                for line in prompt.splitlines()
                if line.startswith("STAGE_ID:")
            )
            self.complete_calls_by_stage[stage] = self.complete_calls_by_stage.get(stage, 0) + 1
            if stage == "source_analysis":
                return LLMResponse(
                    content=json.dumps({"ranked_evidence_ids": ["SRC-01"], "gap_evidence_ids": []}),
                    model="source",
                    usage={},
                    truncated=False,
                )
            raise AssertionError("streaming markdown must not use complete_once")

        async def stream_complete(self, messages, max_tokens=4096, temperature=0.2):
            self.stream_calls += 1
            try:
                for character in (
                    "## 已生成主流程\n\n1. initiator 发送 login PDU。\n"
                    + ("流程证据持续生成。" * 30)
                ):
                    yield character
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    contract = _contract()
    contract["required_outputs"] = ["business_flow.md"]
    contract["artifact_contract"] = {"business_flow.md": {"artifact": "business_flow.md"}}
    plan = build_staged_execution_plan(contract=contract, original_user_request="bounded flow")
    llm = SlowStreamingLLM()

    result = await execute_staged_builtin_plan(
        llm=llm,
        plan=plan,
        artifact_dir=tmp_path,
        context_prompt="legacy context that must not be resent",
        source_analysis_context=_verified_source_context(),
        regular_stage_limits={
            "business_flow": {
                "provider_timeout_seconds": 0.05,
                "total_timeout_seconds": 0.1,
                "repair_timeout_seconds": 0.02,
            }
        },
        on_progress=events.append,
    )

    stage_result = json.loads(
        (tmp_path / "stages" / "business_flow" / "stage_result.json").read_text()
    )
    flow = (tmp_path / "business_flow.md").read_text()
    assert result["status"] == "partial"
    assert result["partial_stages"] == ["business_flow"]
    assert stage_result["status"] == "partial"
    assert stage_result["attempt_count"] == 1
    assert stage_result["full_retry_performed"] is False
    assert llm.stream_calls == 1
    assert llm.complete_calls_by_stage.get("business_flow", 0) == 0
    assert llm.cancelled.is_set()
    assert "已生成主流程" in flow
    assert any(event.get("event_type") == "stage_first_token" for event in events)
    assert any(event.get("event_type") == "stage_output_delta" for event in events)
    assert sum(event.get("event_type") == "stage_output_delta" for event in events) <= 10
    assert any(
        event.get("event_type") == "stage_heartbeat"
        and "remaining_seconds" in event
        for event in events
    )
    assert any(event.get("event_type") == "stage_output_checkpoint" for event in events)
    assert any(event.get("event_type") == "stage_timed_out" for event in events)
    assert any(
        event.get("event_type") == "stage_completed"
        and event.get("stage_id") == "business_flow"
        and event.get("status") == "partial"
        for event in events
    )


@pytest.mark.asyncio
async def test_truncated_json_stage_uses_small_repair_without_resending_full_context(tmp_path):
    class RepairLLM(_StageLLM):
        def __init__(self) -> None:
            super().__init__()
            self.once_prompts: list[str] = []

        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            prompt = messages[-1]["content"]
            self.once_prompts.append(prompt)
            if "STAGE_ID: source_analysis" in prompt:
                return await super().complete(messages, max_tokens, temperature)
            if "SMALL_FORMAT_REPAIR" in prompt:
                return LLMResponse(
                    content=json.dumps([{"failure_mode": "timeout", "cause": "peer silent"}]),
                    model="repair",
                    usage={},
                    truncated=False,
                )
            return LLMResponse(content='[{"failure_mode": "timeout"', model="full", usage={}, truncated=False)

    contract = _contract()
    contract["required_outputs"] = ["sfmea.json"]
    contract["artifact_contract"] = {
        "sfmea.json": {
            "artifact": "sfmea.json",
            "schema": {
                "type": "array",
                "items": {"type": "object", "required": ["failure_mode", "cause"]},
            },
        }
    }
    llm = RepairLLM()
    await execute_staged_builtin_plan(
        llm=llm,
        plan=build_staged_execution_plan(contract=contract, original_user_request="repair"),
        artifact_dir=tmp_path,
        context_prompt="FULL_CONTEXT_SENTINEL " + "x" * 5000,
        source_analysis_context=_verified_source_context(),
    )

    sfmea_result = json.loads(
        (tmp_path / "stages" / "sfmea" / "stage_result.json").read_text()
    )
    repair_prompts = [prompt for prompt in llm.once_prompts if "SMALL_FORMAT_REPAIR" in prompt]
    assert sfmea_result["attempt_count"] == 1
    assert sfmea_result["repair_attempt_count"] == 1
    assert sfmea_result["full_retry_performed"] is False
    assert len(repair_prompts) == 1
    assert "FULL_CONTEXT_SENTINEL" not in repair_prompts[0]
    assert len(repair_prompts[0]) < 4000


@pytest.mark.asyncio
async def test_truncated_json_array_keeps_closed_items_and_only_continues_missing_rows(
    tmp_path,
):
    class ContinuationLLM(_StageLLM):
        def __init__(self) -> None:
            super().__init__()
            self.once_prompts: list[str] = []
            self.token_budgets: list[int] = []

        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            prompt = messages[-1]["content"]
            self.once_prompts.append(prompt)
            self.token_budgets.append(max_tokens)
            if "STAGE_ID: source_analysis" in prompt:
                return await super().complete(messages, max_tokens, temperature)
            if "STAGE_ID: sfmea" in prompt:
                return await super().complete(messages, max_tokens, temperature)
            if "JSON_ARRAY_CONTINUATION" in prompt:
                assert "REMAINING_ITEM_COUNT: 1" in prompt
                assert '"case_id": "case-1"' not in prompt
                return LLMResponse(
                    content=json.dumps(
                        [
                            {
                                "case_id": "case-3",
                                "scenario_name": "reconnect",
                            }
                        ]
                    ),
                    model="continuation",
                    usage={},
                    truncated=False,
                )
            return LLMResponse(
                content=(
                    '[{"case_id":"case-1","scenario_name":"normal"},'
                    '{"case_id":"case-2","scenario_name":"timeout"},'
                    '{"case_id":"case-3"'
                ),
                model="full",
                usage={},
                truncated=True,
                finish_reason="length",
            )

    contract = _contract()
    contract["required_outputs"] = ["black_box_cases.json"]
    contract["artifact_contract"] = {
        "black_box_cases.json": {
            "artifact": "black_box_cases.json",
            "schema": {
                "type": "array",
                "minItems": 3,
                "items": {
                    "type": "object",
                    "required": ["case_id", "scenario_name"],
                },
            },
        }
    }
    llm = ContinuationLLM()

    result = await execute_staged_builtin_plan(
        llm=llm,
        plan=build_staged_execution_plan(
            contract=contract,
            original_user_request="continue only the missing rows",
        ),
        artifact_dir=tmp_path,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
    )

    cases = json.loads((tmp_path / "black_box_cases.json").read_text())
    stage_result = json.loads(
        (tmp_path / "stages" / "black_box_cases" / "stage_result.json").read_text()
    )
    continuation_prompts = [
        prompt for prompt in llm.once_prompts if "JSON_ARRAY_CONTINUATION" in prompt
    ]
    assert result["status"] == "completed"
    assert [item["case_id"] for item in cases] == ["case-1", "case-2", "case-3"]
    assert stage_result["attempt_count"] == 1
    assert stage_result["continuation_count"] == 1
    assert stage_result["provider_call_count"] == 2
    assert stage_result["full_retry_performed"] is False
    assert len(continuation_prompts) == 1
    assert len(continuation_prompts[0]) < 20000
    assert llm.token_budgets[-1] > 600


@pytest.mark.asyncio
async def test_json_array_continuation_uses_fast_auxiliary_model(tmp_path):
    class PrimaryReasoningLLM(_StageLLM):
        def __init__(self) -> None:
            super().__init__()
            self.continuation_prompts: list[str] = []

        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            prompt = messages[-1]["content"]
            if "JSON_ARRAY_CONTINUATION" in prompt:
                self.continuation_prompts.append(prompt)
                raise AssertionError("continuation must not use the reasoning model")
            if "STAGE_ID: source_analysis" in prompt:
                return await super().complete(messages, max_tokens, temperature)
            if "STAGE_ID: sfmea" in prompt:
                return await super().complete(messages, max_tokens, temperature)
            return LLMResponse(
                content=(
                    '[{"case_id":"case-1","scenario_name":"normal"},'
                    '{"case_id":"case-2","scenario_name":"timeout"'
                ),
                model="reasoning-model",
                usage={},
                truncated=True,
                finish_reason="length",
            )

    class FastAuxiliaryLLM(_StageLLM):
        def __init__(self) -> None:
            super().__init__()
            self.continuation_prompts: list[str] = []

        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            prompt = messages[-1]["content"]
            if "JSON_ARRAY_CONTINUATION" in prompt:
                self.continuation_prompts.append(prompt)
                return LLMResponse(
                    content='[{"case_id":"case-2","scenario_name":"timeout"}]',
                    model="fast-model",
                    usage={},
                    truncated=False,
                )
            return await super().complete(messages, max_tokens, temperature)

    contract = _contract()
    contract["required_outputs"] = ["black_box_cases.json"]
    contract["artifact_contract"] = {
        "black_box_cases.json": {
            "artifact": "black_box_cases.json",
            "schema": {
                "type": "array",
                "minItems": 2,
                "items": {
                    "type": "object",
                    "required": ["case_id", "scenario_name"],
                },
            },
        }
    }
    primary = PrimaryReasoningLLM()
    auxiliary = FastAuxiliaryLLM()

    await execute_staged_builtin_plan(
        llm=primary,
        source_analysis_llm=auxiliary,
        plan=build_staged_execution_plan(
            contract=contract,
            original_user_request="route continuation to fast model",
        ),
        artifact_dir=tmp_path,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
    )

    stage_result = json.loads(
        (tmp_path / "stages" / "black_box_cases" / "stage_result.json").read_text()
    )
    assert primary.continuation_prompts == []
    assert len(auxiliary.continuation_prompts) == 1
    assert stage_result["continuation_model"] == "fast-model"


@pytest.mark.asyncio
async def test_malformed_json_array_keeps_later_rows_and_only_replaces_damaged_row(
    tmp_path,
):
    class MalformedArrayLLM(_StageLLM):
        def __init__(self) -> None:
            super().__init__()
            self.once_prompts: list[str] = []

        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            prompt = messages[-1]["content"]
            self.once_prompts.append(prompt)
            if "STAGE_ID: source_analysis" in prompt:
                return await super().complete(messages, max_tokens, temperature)
            if "STAGE_ID: sfmea" in prompt:
                return await super().complete(messages, max_tokens, temperature)
            if "JSON_ARRAY_CONTINUATION" in prompt:
                assert "REMAINING_ITEM_COUNT: 1" in prompt
                return LLMResponse(
                    content=json.dumps(
                        [{"case_id": "case-2", "scenario_name": "invalid input"}]
                    ),
                    model="continuation",
                    usage={},
                    truncated=False,
                )
            return LLMResponse(
                content=(
                    '[{"case_id":"case-1","scenario_name":"normal"},'
                    '{"case_id":"case-2","steps":["first"],"broken"]},'
                    '{"case_id":"case-3","scenario_name":"timeout"}]'
                ),
                model="full",
                usage={},
                truncated=False,
                finish_reason="stop",
            )

    contract = _contract()
    contract["required_outputs"] = ["black_box_cases.json"]
    contract["artifact_contract"] = {
        "black_box_cases.json": {
            "artifact": "black_box_cases.json",
            "schema": {
                "type": "array",
                "minItems": 3,
                "items": {
                    "type": "object",
                    "required": ["case_id", "scenario_name"],
                },
            },
        }
    }
    llm = MalformedArrayLLM()

    result = await execute_staged_builtin_plan(
        llm=llm,
        plan=build_staged_execution_plan(
            contract=contract,
            original_user_request="replace only a malformed black-box row",
        ),
        artifact_dir=tmp_path,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
    )

    cases = json.loads((tmp_path / "black_box_cases.json").read_text())
    stage_result = json.loads(
        (tmp_path / "stages" / "black_box_cases" / "stage_result.json").read_text()
    )
    continuation_prompts = [
        prompt for prompt in llm.once_prompts if "JSON_ARRAY_CONTINUATION" in prompt
    ]
    assert result["status"] == "completed"
    assert [item["case_id"] for item in cases] == ["case-1", "case-3", "case-2"]
    assert stage_result["attempt_count"] == 1
    assert stage_result["continuation_count"] == 1
    assert stage_result["provider_call_count"] == 2
    assert stage_result["finish_reason"] == "json_array_salvage_stop"
    assert stage_result["full_retry_performed"] is False
    assert len(continuation_prompts) == 1


@pytest.mark.asyncio
async def test_truncated_quality_repair_preserves_previous_array_cardinality(tmp_path):
    class QualityRepairContinuationLLM(_StageLLM):
        def __init__(self) -> None:
            super().__init__()
            self.continuation_prompts: list[str] = []

        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            prompt = messages[-1]["content"]
            if "STAGE_ID: source_analysis" in prompt:
                return await super().complete(messages, max_tokens, temperature)
            if "STAGE_ID: sfmea" in prompt:
                return await super().complete(messages, max_tokens, temperature)
            if "JSON_ARRAY_CONTINUATION" in prompt:
                self.continuation_prompts.append(prompt)
                assert "REMAINING_ITEM_COUNT: 2" in prompt
                return LLMResponse(
                    content=json.dumps(
                        [
                            {
                                "case_id": "case-3",
                                "scenario_name": "must not overwrite accepted reconnect",
                            },
                            {"case_id": "case-5", "scenario_name": "mutual chap"},
                        ]
                    ),
                    model="continuation",
                    usage={},
                    truncated=False,
                )
            if "STAGE_ID: black_box_cases" in prompt:
                return LLMResponse(
                    content=(
                        '[{"case_id":"case-1","scenario_name":"normal"},'
                        '{"case_id":"case-2","scenario_name":"timeout"},'
                        '{"case_id":"case-3"'
                    ),
                    model="repair",
                    usage={},
                    truncated=True,
                    finish_reason="length",
                )
            return await super().complete(messages, max_tokens, temperature)

    previous = [
        {"case_id": f"case-{index}", "scenario_name": f"existing-{index}"}
        for index in range(1, 5)
    ]
    (tmp_path / "black_box_cases.json").write_text(
        json.dumps(previous), encoding="utf-8"
    )
    contract = _contract()
    contract["required_outputs"] = ["black_box_cases.json"]
    contract["artifact_contract"] = {
        "black_box_cases.json": {
            "artifact": "black_box_cases.json",
            "schema": {
                "type": "array",
                "minItems": 2,
                "items": {
                    "type": "object",
                    "required": ["case_id", "scenario_name"],
                },
            },
        }
    }
    plan = build_staged_execution_plan(
        contract=contract,
        original_user_request="repair without dropping accepted cases",
    )
    plan["quality_retry_feedback"] = {
        "affected_artifacts": ["black_box_cases.json"],
        "issues": [
            {
                "artifact": "black_box_cases.json",
                "code": "missing_mutual_chap_case",
                "message": "add mutual CHAP coverage",
            }
        ],
    }
    plan["cache_bypass_artifacts"] = ["black_box_cases.json"]
    llm = QualityRepairContinuationLLM()

    result = await execute_staged_builtin_plan(
        llm=llm,
        plan=plan,
        artifact_dir=tmp_path,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
    )

    cases = json.loads((tmp_path / "black_box_cases.json").read_text())
    stage_result = json.loads(
        (tmp_path / "stages" / "black_box_cases" / "stage_result.json").read_text()
    )
    assert result["status"] == "completed"
    assert [item["case_id"] for item in cases] == [
        "case-1",
        "case-2",
        "case-3",
        "case-4",
        "case-5",
    ]
    assert cases[2]["scenario_name"] == "existing-3"
    assert cases[3]["scenario_name"] == "existing-4"
    assert cases[4]["scenario_name"] == "mutual chap"
    assert stage_result["continuation_count"] == 1
    assert len(llm.continuation_prompts) == 1


@pytest.mark.asyncio
async def test_quality_repair_restores_accepted_rows_omitted_by_model(tmp_path):
    class OmissionRepairLLM(_StageLLM):
        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            prompt = messages[-1]["content"]
            if "STAGE_ID: source_analysis" in prompt:
                return await super().complete(messages, max_tokens, temperature)
            if "STAGE_ID: sfmea" in prompt:
                return await super().complete(messages, max_tokens, temperature)
            if "STAGE_ID: black_box_cases" in prompt:
                return LLMResponse(
                    content=json.dumps(
                        [
                            {"case_id": "case-1", "scenario_name": "normal-fixed"},
                            {"case_id": "case-2", "scenario_name": "timeout"},
                            {"case_id": "case-3", "scenario_name": "reconnect"},
                        ]
                    ),
                    model="repair",
                    usage={},
                    truncated=False,
                    finish_reason="stop",
                )
            return await super().complete(messages, max_tokens, temperature)

    previous = [
        {"case_id": f"case-{index}", "scenario_name": f"existing-{index}"}
        for index in range(1, 5)
    ]
    (tmp_path / "black_box_cases.json").write_text(
        json.dumps(previous), encoding="utf-8"
    )
    contract = _contract()
    contract["required_outputs"] = ["black_box_cases.json"]
    contract["artifact_contract"] = {
        "black_box_cases.json": {
            "artifact": "black_box_cases.json",
            "schema": {
                "type": "array",
                "minItems": 2,
                "items": {
                    "type": "object",
                    "required": ["case_id", "scenario_name"],
                },
            },
        }
    }
    plan = build_staged_execution_plan(
        contract=contract,
        original_user_request="repair one case without dropping accepted rows",
    )
    plan["quality_retry_feedback"] = {
        "affected_artifacts": ["black_box_cases.json"],
        "issues": [
                {
                    "artifact": "black_box_cases.json",
                    "code": "fix_case_one",
                    "claim_id": "ROW:black_box_cases.json:case-1",
                    "message": "fix case one",
                }
        ],
    }
    plan["cache_bypass_artifacts"] = ["black_box_cases.json"]

    await execute_staged_builtin_plan(
        llm=OmissionRepairLLM(),
        plan=plan,
        artifact_dir=tmp_path,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
    )

    cases = json.loads((tmp_path / "black_box_cases.json").read_text())
    assert [item["case_id"] for item in cases] == [
        "case-1",
        "case-2",
        "case-3",
        "case-4",
    ]
    assert cases[0]["scenario_name"] == "normal-fixed"
    assert cases[-1]["scenario_name"] == "existing-4"


def test_quality_patch_deduplicates_known_sfmea_semantic_categories():
    from app.services.ai_staged_execution import (
        _deduplicate_sfmea_semantic_categories,
    )

    rows = [
        {
            "sfmea_id": "FM-38",
            "failure_mode": "Mutual challenge 合法编码但语义错误",
        },
        {
            "sfmea_id": "FM-19",
            "failure_mode": "Mutual CHAP challenge correctly encoded but uses a wrong value",
        },
        {
            "sfmea_id": "FM-01",
            "failure_mode": "Login request uses an invalid NSG",
        },
    ]

    deduplicated = _deduplicate_sfmea_semantic_categories(rows)

    assert [row["sfmea_id"] for row in deduplicated] == ["FM-38", "FM-01"]


@pytest.mark.asyncio
async def test_truncated_json_array_can_finish_with_two_bounded_missing_only_continuations(
    tmp_path,
):
    class TwoChunkContinuationLLM(_StageLLM):
        def __init__(self) -> None:
            super().__init__()
            self.continuation_prompts: list[str] = []
            self.continuation_budgets: list[int] = []

        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            prompt = messages[-1]["content"]
            if "STAGE_ID: source_analysis" in prompt:
                return await super().complete(messages, max_tokens, temperature)
            if "STAGE_ID: sfmea" in prompt:
                return await super().complete(messages, max_tokens, temperature)
            if "JSON_ARRAY_CONTINUATION" in prompt:
                self.continuation_prompts.append(prompt)
                self.continuation_budgets.append(max_tokens)
                if len(self.continuation_prompts) == 1:
                    assert "REMAINING_ITEM_COUNT: 2" in prompt
                    return LLMResponse(
                        content=(
                            '[{"case_id":"case-3","scenario_name":"reconnect"},'
                            '{"case_id":"case-4","scenario_name":"concurrency"'
                        ),
                        model="continuation-1",
                        usage={},
                        truncated=True,
                        finish_reason="length",
                    )
                assert "REMAINING_ITEM_COUNT: 1" in prompt
                assert "case_id:case-3" in prompt
                return LLMResponse(
                    content=json.dumps(
                        [{"case_id": "case-4", "scenario_name": "concurrency"}]
                    ),
                    model="continuation-2",
                    usage={},
                    truncated=False,
                )
            return LLMResponse(
                content=(
                    '[{"case_id":"case-1","scenario_name":"normal"},'
                    '{"case_id":"case-2","scenario_name":"timeout"},'
                    '{"case_id":"case-3"'
                ),
                model="full",
                usage={},
                truncated=True,
                finish_reason="length",
            )

    contract = _contract()
    contract["required_outputs"] = ["black_box_cases.json"]
    contract["artifact_contract"] = {
        "black_box_cases.json": {
            "artifact": "black_box_cases.json",
            "schema": {
                "type": "array",
                "minItems": 4,
                "items": {
                    "type": "object",
                    "required": ["case_id", "scenario_name"],
                },
            },
        }
    }
    llm = TwoChunkContinuationLLM()

    result = await execute_staged_builtin_plan(
        llm=llm,
        plan=build_staged_execution_plan(
            contract=contract,
            original_user_request="continue only missing rows in bounded chunks",
        ),
        artifact_dir=tmp_path,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
    )

    cases = json.loads((tmp_path / "black_box_cases.json").read_text())
    stage_result = json.loads(
        (tmp_path / "stages" / "black_box_cases" / "stage_result.json").read_text()
    )
    assert result["status"] == "completed"
    assert [item["case_id"] for item in cases] == [
        "case-1",
        "case-2",
        "case-3",
        "case-4",
    ]
    assert stage_result["continuation_count"] == 2
    assert stage_result["provider_call_count"] == 3
    assert stage_result["full_retry_performed"] is False
    assert len(llm.continuation_prompts) == 2
    assert llm.continuation_budgets[0] >= 4000
    assert not (tmp_path / "stages" / "black_box_cases" / "repair_prompt.txt").exists()


@pytest.mark.asyncio
async def test_flow_outline_is_deterministic_and_unblocks_sfmea_before_narrative_finishes(
    tmp_path,
):
    narrative_started = asyncio.Event()
    sfmea_started = asyncio.Event()

    class DependencyLLM(_StageLLM):
        async def stream_complete(self, messages, max_tokens=4096, temperature=0.2):
            prompt = messages[-1]["content"]
            if "STAGE_ID: business_flow" in prompt:
                narrative_started.set()
                await asyncio.wait_for(sfmea_started.wait(), timeout=0.5)
                yield "## 模型补充\n流程补充完成。\n"
                return
            yield (await self.complete_once(messages, max_tokens, temperature)).content

        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            prompt = messages[-1]["content"]
            if "STAGE_ID: sfmea" in prompt:
                sfmea_started.set()
            return await super().complete(messages, max_tokens, temperature)

    result = await execute_staged_builtin_plan(
        llm=DependencyLLM(),
        plan=build_staged_execution_plan(contract=_contract(), original_user_request="dependency graph"),
        artifact_dir=tmp_path,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
    )

    outline = json.loads((tmp_path / "flow_outline.json").read_text())
    evidence_pack = json.loads((tmp_path / "flow_evidence_pack.json").read_text())
    assert result["status"] == "completed"
    assert narrative_started.is_set()
    assert sfmea_started.is_set()
    assert outline["main_flows"]
    assert outline["evidence_ids"]
    assert evidence_pack["entry_points"]
    assert evidence_pack["call_edges"]
    assert "sequenceDiagram" in (tmp_path / "business_flow.md").read_text()


@pytest.mark.asyncio
async def test_provider_capacity_is_shared_across_concurrent_plans(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.ai_staged_execution.settings.llm_max_concurrency", 1)
    active = 0
    maximum = 0

    class SharedProviderLLM(_StageLLM):
        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            return await super().complete(messages, max_tokens, temperature)

        async def stream_complete(self, messages, max_tokens=4096, temperature=0.2):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                await asyncio.sleep(0.03)
                yield "## 业务流程\n\n真实增量。\n"
            finally:
                active -= 1

    contract = {
        **_contract(),
        "required_outputs": ["business_flow.md"],
        "artifact_contract": {"business_flow.md": {"artifact": "business_flow.md"}},
    }
    llm = SharedProviderLLM()
    await asyncio.gather(
        execute_staged_builtin_plan(
            llm=llm,
            plan=build_staged_execution_plan(contract=contract, original_user_request="one"),
            artifact_dir=tmp_path / "one",
            context_prompt="legacy",
            source_analysis_context=_verified_source_context(),
        ),
        execute_staged_builtin_plan(
            llm=llm,
            plan=build_staged_execution_plan(contract=contract, original_user_request="two"),
            artifact_dir=tmp_path / "two",
            context_prompt="legacy",
            source_analysis_context=_verified_source_context(),
        ),
    )

    assert maximum == 1


def test_provider_capacity_is_process_global_across_event_loops(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.ai_staged_execution.settings.llm_max_concurrency", 1)
    active = 0
    maximum = 0
    lock = __import__("threading").Lock()

    class SharedProviderLLM(_StageLLM):
        async def stream_complete(self, messages, max_tokens=4096, temperature=0.2):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            try:
                await asyncio.sleep(0.06)
                yield "## 业务流程\n\n真实增量。\n"
            finally:
                with lock:
                    active -= 1

    contract = {
        **_contract(),
        "required_outputs": ["business_flow.md"],
        "artifact_contract": {"business_flow.md": {"artifact": "business_flow.md"}},
    }

    def run(index: int) -> None:
        asyncio.run(
            execute_staged_builtin_plan(
                llm=SharedProviderLLM(),
                plan=build_staged_execution_plan(
                    contract=contract,
                    original_user_request=f"thread-{index}",
                ),
                artifact_dir=tmp_path / str(index),
                context_prompt="legacy",
                source_analysis_context=_verified_source_context(),
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(run, range(2)))

    assert maximum == 1


@pytest.mark.asyncio
async def test_stream_finish_reason_and_every_public_delta_are_preserved(tmp_path):
    events: list[dict] = []
    streamed = "流程内容" * 700

    class TruncatedStreamLLM(_StageLLM):
        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            return await super().complete(messages, max_tokens, temperature)

        async def stream_complete(self, messages, max_tokens=4096, temperature=0.2):
            current_finish_reason.set("length")
            yield streamed

    contract = {
        **_contract(),
        "required_outputs": ["business_flow.md"],
        "artifact_contract": {"business_flow.md": {"artifact": "business_flow.md"}},
    }
    execution = await execute_staged_builtin_plan(
        llm=TruncatedStreamLLM(),
        plan=build_staged_execution_plan(contract=contract, original_user_request="truncated"),
        artifact_dir=tmp_path,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
        on_progress=events.append,
    )

    result = json.loads(
        (tmp_path / "stages" / "business_flow" / "stage_result.json").read_text()
    )
    deltas = "".join(
        str(event.get("delta") or "")
        for event in events
        if event.get("event_type") == "stage_output_delta"
    )
    assert result["finish_reason"] == "length"
    assert result["status"] == "partial"
    assert execution["status"] == "partial"
    assert execution["partial_stages"] == ["business_flow"]
    assert deltas == streamed


@pytest.mark.asyncio
async def test_business_flow_length_continuation_keeps_specialized_prompt(tmp_path):
    class ContinueBusinessFlowLLM(_StageLLM):
        def __init__(self) -> None:
            super().__init__()
            self.stream_prompts: list[str] = []

        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            return await super().complete(messages, max_tokens, temperature)

        async def stream_complete(self, messages, max_tokens=4096, temperature=0.2):
            prompt = messages[-1]["content"]
            self.stream_prompts.append(prompt)
            if len(self.stream_prompts) == 1:
                current_finish_reason.set("length")
                yield "### 主流程补充\n\n已完成登录入口与协商，"
                return
            current_finish_reason.set("stop")
            yield "继续补充异常清理与恢复步骤。\n"

    contract = {
        **_contract(),
        "required_outputs": ["business_flow.md"],
        "artifact_contract": {"business_flow.md": {"artifact": "business_flow.md"}},
    }
    plan = build_staged_execution_plan(
        contract=contract,
        original_user_request="continue the verified business flow",
    )
    flow_stage = next(stage for stage in plan["stages"] if stage["id"] == "business_flow")
    flow_stage.update({"continue_on_length": True, "max_continuations": 1})
    llm = ContinueBusinessFlowLLM()

    execution = await execute_staged_builtin_plan(
        llm=llm,
        plan=plan,
        artifact_dir=tmp_path,
        context_prompt="legacy context must not be reintroduced",
        source_analysis_context=_verified_source_context(),
    )

    stage_result = json.loads(
        (tmp_path / "stages" / "business_flow" / "stage_result.json").read_text()
    )
    assert execution["status"] == "completed"
    assert stage_result["status"] == "completed"
    assert stage_result["continuation_count"] == 1
    assert len(llm.stream_prompts) == 2
    assert "PURPOSE: 仅对已验证 Flow Outline 做公开叙述增强" in llm.stream_prompts[1]
    assert "BUSINESS_FLOW_CONTEXT:" in llm.stream_prompts[1]
    assert "PARTIAL_NARRATIVE_TO_CONTINUE:" in llm.stream_prompts[1]
    assert "legacy context must not be reintroduced" not in llm.stream_prompts[1]


def test_quality_repair_reuses_existing_unaffected_stage_artifact(tmp_path):
    from app.services.ai_staged_execution import _existing_quality_stage_result

    artifact_dir = tmp_path / "run"
    stage_dir = artifact_dir / "stages" / "business_flow"
    stage_dir.mkdir(parents=True)
    output = artifact_dir / "business_flow.md"
    output.write_text("# verified flow\n", encoding="utf-8")
    plan = {
        "quality_retry_feedback": {"issue_count": 2},
        "cache_bypass_artifacts": ["sfmea.json", "report.md"],
    }

    reused = _existing_quality_stage_result(
        plan=plan,
        artifact_dir=artifact_dir,
        stage_dir=stage_dir,
        stage={"id": "business_flow", "artifact": "business_flow.md"},
    )
    assert reused is not None
    assert reused["attempt_count"] == 0
    assert reused["reuse_source"] == "same_run_quality_accepted_artifact"

    assert _existing_quality_stage_result(
        plan=plan,
        artifact_dir=artifact_dir,
        stage_dir=artifact_dir / "stages" / "sfmea",
        stage={"id": "sfmea", "artifact": "sfmea.json"},
    ) is None
    assert _existing_quality_stage_result(
        plan={},
        artifact_dir=artifact_dir,
        stage_dir=stage_dir,
        stage={"id": "business_flow", "artifact": "business_flow.md"},
    ) is None


@pytest.mark.asyncio
async def test_regular_stage_cache_reuses_flow_and_invalidates_on_repo_revision(tmp_path):
    cache_dir = tmp_path / "cache"
    plan = build_staged_execution_plan(contract=_contract(), original_user_request="cache flow")
    plan["workflow_version"] = "workflow-flow-v1"
    first = _StageLLM()
    second = _StageLLM()
    changed = _StageLLM()

    await execute_staged_builtin_plan(
        llm=first,
        plan=plan,
        artifact_dir=tmp_path / "first",
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
        regular_stage_cache_dir=cache_dir,
    )
    promote_regular_stage_caches(
        cache_root=cache_dir,
        artifact_roots=[tmp_path / "first"],
        blocked_artifacts=set(),
    )
    reused_events: list[dict] = []
    await execute_staged_builtin_plan(
        llm=second,
        plan=plan,
        artifact_dir=tmp_path / "second",
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
        regular_stage_cache_dir=cache_dir,
        on_progress=reused_events.append,
    )
    changed_context = _verified_source_context()
    changed_context["source_context"]["repo_revision"] = "def456"
    await execute_staged_builtin_plan(
        llm=changed,
        plan=plan,
        artifact_dir=tmp_path / "changed",
        context_prompt="legacy",
        source_analysis_context=changed_context,
        regular_stage_cache_dir=cache_dir,
    )

    assert second.calls_by_stage.get("business_flow", 0) == 0
    assert any(
        event.get("event_type") == "stage_reused"
        and event.get("stage_id") in {"flow_evidence_pack", "flow_outline", "business_flow"}
        for event in reused_events
    )
    reused_flow = next(
        event
        for event in reused_events
        if event.get("event_type") == "stage_reused"
        and event.get("stage_id") == "flow_evidence_pack"
    )
    reused_pack = json.loads((tmp_path / "second" / "flow_evidence_pack.json").read_text())
    assert reused_flow["entry_point_count"] == len(reused_pack.get("entry_points") or [])
    assert reused_flow["call_edge_count"] == len(reused_pack.get("call_edges") or [])
    assert reused_flow["test_reference_count"] == len(reused_pack.get("related_tests") or [])
    assert changed.calls_by_stage.get("business_flow", 0) == 1


@pytest.mark.asyncio
async def test_retry_attempt_reuses_flow_support_and_continues_partial_business_flow(tmp_path):
    source_cache = tmp_path / "source-cache"
    regular_cache = tmp_path / "regular-cache"
    first_cancelled = asyncio.Event()

    class PartialFlowLLM(_StageLLM):
        _model = "same-real-model"

        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            return await super().complete(messages, max_tokens, temperature)

        async def stream_complete(self, messages, max_tokens=4096, temperature=0.2):
            try:
                yield "已完成登录入口与协商步骤。\n"
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                first_cancelled.set()
                raise

    class ContinueFlowLLM(_StageLLM):
        _model = "same-real-model"

        def __init__(self) -> None:
            super().__init__()
            self.stream_prompts: list[str] = []

        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            return await super().complete(messages, max_tokens, temperature)

        async def stream_complete(self, messages, max_tokens=4096, temperature=0.2):
            self.stream_prompts.append(messages[-1]["content"])
            yield "继续补充异常清理与恢复步骤。\n"

    plan = build_staged_execution_plan(
        contract={
            **_contract(),
            "required_outputs": ["business_flow.md"],
            "artifact_contract": {
                "business_flow.md": {"artifact": "business_flow.md"}
            },
        },
        original_user_request="partial resume",
    )
    await execute_staged_builtin_plan(
        llm=PartialFlowLLM(),
        plan=plan,
        artifact_dir=tmp_path / "first",
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
        source_analysis_cache_dir=source_cache,
        regular_stage_cache_dir=regular_cache,
        regular_stage_limits={
            "business_flow": {
                "provider_timeout_seconds": 0.04,
                "total_timeout_seconds": 0.08,
            }
        },
    )

    events: list[dict] = []
    continuation = ContinueFlowLLM()
    await execute_staged_builtin_plan(
        llm=continuation,
        plan=plan,
        artifact_dir=tmp_path / "second",
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
        source_analysis_cache_dir=source_cache,
        regular_stage_cache_dir=regular_cache,
        regular_stage_limits={
            "business_flow": {
                "provider_timeout_seconds": 0.04,
                "total_timeout_seconds": 0.08,
            }
        },
        on_progress=events.append,
    )

    result = json.loads(
        (tmp_path / "second" / "stages" / "business_flow" / "stage_result.json").read_text()
    )
    report = (tmp_path / "second" / "business_flow.md").read_text()
    assert first_cancelled.is_set()
    assert result["status"] == "completed"
    assert continuation.stream_prompts
    assert "PARTIAL_NARRATIVE_TO_CONTINUE" in continuation.stream_prompts[0]
    assert "已完成登录入口与协商步骤" in continuation.stream_prompts[0]
    assert "继续补充异常清理与恢复步骤" in report
    reused = {
        event.get("stage_id")
        for event in events
        if event.get("event_type") == "stage_reused"
    }
    assert {"flow_evidence_pack", "flow_outline", "business_flow"}.issubset(reused)


@pytest.mark.asyncio
async def test_same_provider_concurrency_is_limited_and_metrics_are_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.ai_staged_execution.settings.llm_max_concurrency", 1)
    active = 0
    max_active = 0

    class CapacityLLM(_StageLLM):
        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            nonlocal active, max_active
            prompt = messages[-1]["content"]
            if "STAGE_ID: source_analysis" in prompt:
                return await super().complete(messages, max_tokens, temperature)
            active += 1
            max_active = max(max_active, active)
            try:
                await asyncio.sleep(0.02)
                return await super().complete(messages, max_tokens, temperature)
            finally:
                active -= 1

    contract = _contract()
    contract["required_outputs"] = ["project_structure.md", "module_map.md"]
    contract["artifact_contract"] = {
        name: {"artifact": name} for name in contract["required_outputs"]
    }
    await execute_staged_builtin_plan(
        llm=CapacityLLM(),
        plan=build_staged_execution_plan(contract=contract, original_user_request="capacity"),
        artifact_dir=tmp_path,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
    )

    results = [
        json.loads((tmp_path / "stages" / stage / "stage_result.json").read_text())
        for stage in ("project_structure", "module_map")
    ]
    assert max_active == 1
    for result in results:
        assert result["queue_wait_ms"] >= 0
        assert result["provider_wait_ms"] >= 0
        assert result["generation_ms"] >= 0
        assert result["validation_ms"] >= 0
        assert result["repair_ms"] >= 0
        assert result["total_duration_ms"] >= result["provider_wait_ms"]


@pytest.mark.asyncio
async def test_source_analysis_uses_process_global_provider_capacity(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.ai_staged_execution.settings.llm_max_concurrency", 1)
    active = 0
    max_active = 0

    class SourceCapacityLLM(_StageLLM):
        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            try:
                await asyncio.sleep(0.04)
                return await super().complete(messages, max_tokens, temperature)
            finally:
                active -= 1

    plans = [
        build_staged_execution_plan(contract=_contract(), original_user_request=f"source-{index}")
        for index in range(2)
    ]
    await asyncio.gather(
        *[
            execute_staged_builtin_plan(
                llm=SourceCapacityLLM(),
                source_analysis_llm=SourceCapacityLLM(),
                plan=plan,
                artifact_dir=tmp_path / f"run-{index}",
                context_prompt="legacy",
                source_analysis_context=_verified_source_context(),
            )
            for index, plan in enumerate(plans)
        ]
    )

    assert max_active == 1


@pytest.mark.asyncio
async def test_executor_stops_between_stages_when_cancelled(tmp_path):
    llm = _StageLLM()
    contract = _contract()
    contract["required_outputs"] = ["risk_review.md"]
    contract["artifact_contract"] = {"risk_review.md": {"artifact": "risk_review.md"}}
    plan = build_staged_execution_plan(contract=contract, original_user_request="cancel")

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
