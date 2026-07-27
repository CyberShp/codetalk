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
    _canonicalize_verified_repo_path_mentions,
    _sanitize_structured_repo_path_mentions,
    _normalize_black_box_dimension_contract,
    _normalize_black_box_delivery_contract,
    _normalize_black_box_source_anchor_claims,
    _normalize_black_box_oracle_contract,
    _materialize_missing_sfmea_source_anchor_claims,
    _normalize_sfmea_source_anchor_claims,
    _normalize_sfmea_risk_contract,
    _apply_regular_stage_output_limits,
    _apply_quality_feedback_field_patches,
    _apply_sfmea_nonrisk_deletion_tombstones,
    _business_flow_deterministic_base,
    _compact_execution_input_contract,
    _deterministic_quality_claim_repair,
    _deterministic_schema_repair,
    _deep_exploration_stage_prompt,
    _execute_source_driven_deterministic_stage,
    _finalize_combined_markdown_report,
    _regular_stage_prompt,
    _json_array_continuation_prompt,
    _merge_json_array_patch,
    _missing_quality_repair_row_ids,
    _existing_quality_stage_result,
    build_profile_execution_evidence,
    _quality_repair_row_ids,
    _quality_repair_may_reassign_black_box_dimensions,
    _quality_repair_allows_new_items,
    _quality_feedback_for_artifact,
    _quality_repair_prompt_seed,
    _quality_repair_evidence_cards,
    _ISCSI_RAW_PDU_APPENDIX,
    _extract_business_flow_narrative,
    _ensure_stable_stage_row_ids,
    _render_deterministic_combined_report,
    _select_regular_stage_llm,
    _select_bounded_source_context_files,
    _source_analysis_prompt_context,
    _source_enclosing_c_function,
    _salvage_truncated_json_array,
    _is_valid_json_artifact_seed,
    _render_stage_artifact,
    _stage_prompt,
    _stage_format_rules,
    StagedExecutionCancelled,
    build_source_analysis_context,
    build_source_evidence_pack,
    build_staged_execution_plan,
    execute_staged_builtin_plan,
    materialize_source_evidence_pack,
    materialize_final_deterministic_quality_repairs,
)

from app.services.workflow_presets import (
    BLACK_BOX_CASES_SCHEMA,
    EVIDENCE_CARDS_SCHEMA,
    SFMEA_SCHEMA,
)
from app.services.flow_evidence import (
    FLOW_EVIDENCE_VERSION,
    _definition_symbol,
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


def test_black_box_stage_capacity_covers_every_required_dimension():
    from app.services.ai_staged_execution import _stage_execution_limits
    from app.services.test_activity_contract import BLACK_BOX_REQUIRED_DIMENSIONS

    limits = _stage_execution_limits("black_box_cases")

    assert limits["output_limits"]["max_items"] >= len(BLACK_BOX_REQUIRED_DIMENSIONS)
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


def test_structured_quality_repair_routes_to_primary_reasoner(monkeypatch):
    class Client:
        def __init__(self, model: str) -> None:
            self._model = model

    reasoner = Client("deepseek-reasoner")
    fast = Client("deepseek-chat")
    monkeypatch.setattr(
        "app.services.ai_staged_execution.settings.regular_stage_structured_fast_model_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.ai_staged_execution.settings.regular_stage_quality_repair_use_primary_model",
        True,
    )

    assert (
        _select_regular_stage_llm(
            reasoner,
            fast,
            "sfmea.json",
            quality_repair=True,
        )
        is reasoner
    )


def test_structured_quality_repair_routes_to_independent_repair_model(monkeypatch):
    class Client:
        def __init__(self, model: str) -> None:
            self._model = model

    flash = Client("deepseek-v4-flash")
    source_fast = Client("deepseek-v4-flash")
    pro = Client("deepseek-v4-pro")
    monkeypatch.setattr(
        "app.services.ai_staged_execution.settings.regular_stage_quality_repair_use_primary_model",
        True,
    )

    assert (
        _select_regular_stage_llm(
            flash,
            source_fast,
            "sfmea.json",
            quality_repair=True,
            quality_repair_llm=pro,
        )
        is pro
    )


def test_risk_bearing_artifacts_use_primary_author_then_independent_validation():
    class Client:
        def __init__(self, model: str) -> None:
            self._model = model

    flash = Client("deepseek-v4-flash")
    pro = Client("deepseek-v4-pro")

    assert _select_regular_stage_llm(
        flash, flash, "sfmea.json", quality_repair_llm=pro
    ) is flash
    assert _select_regular_stage_llm(
        flash, flash, "black_box_cases.json", quality_repair_llm=pro
    ) is flash
    assert _select_regular_stage_llm(
        flash, flash, "business_flow.md", quality_repair_llm=pro
    ) is flash


def test_quality_repair_patch_reports_every_omitted_requested_row():
    patch = [
        {"sfmea_id": "SFMEA-06", "failure_mode": "corrected"},
        {"sfmea_id": "SFMEA-09", "failure_mode": "corrected"},
    ]

    assert _missing_quality_repair_row_ids(
        patch,
        {"SFMEA-06", "SFMEA-07", "SFMEA-08", "SFMEA-09"},
    ) == {"SFMEA-07", "SFMEA-08"}


def test_quality_feedback_field_patch_overrides_model_repair_for_bound_row():
    rendered = [
        {
            "sfmea_id": "SFMEA-04",
            "failure_mode": "empty AuthMethod mismatch",
            "detection": "model repeated a nonexistent log",
        },
        {
            "sfmea_id": "SFMEA-05",
            "failure_mode": "accepted row",
            "detection": "unchanged",
        },
    ]
    feedback = {
        "issues": [
            {
                "artifact": "sfmea.json",
                "row_id": "SFMEA-04",
                "code": "behavior_claim_contradicted",
                "field_patch": {
                    "detection": "通过登录响应状态观测，不声称存在日志。",
                    "sfmea_id": "MUST-NOT-CHANGE",
                },
            }
        ]
    }

    patched = _apply_quality_feedback_field_patches(
        rendered,
        artifact="sfmea.json",
        quality_feedback=feedback,
    )

    assert patched[0]["sfmea_id"] == "SFMEA-04"
    assert patched[0]["detection"] == "通过登录响应状态观测，不声称存在日志。"
    assert patched[1] == rendered[1]


def test_quality_repair_prompt_seed_applies_independent_field_patch_first():
    seed = json.dumps(
        [
            {
                "sfmea_id": "SFMEA-001",
                "failure_mode": "注册表更新失败会导致连接失败",
                "test_mapping": "验证连接返回失败",
            }
        ],
        ensure_ascii=False,
    )
    feedback = {
        "issues": [
            {
                "artifact": "sfmea.json",
                "row_id": "SFMEA-001",
                "code": "behavior_claim_contradicted",
                "field_patch": {
                    "test_mapping": "验证注册表失败仅记录告警，连接仍返回实例。"
                },
            }
        ]
    }

    prompt_seed = json.loads(
        _quality_repair_prompt_seed(
            current_artifact_seed=seed,
            artifact="sfmea.json",
            quality_feedback=feedback,
        )
    )

    assert prompt_seed[0]["test_mapping"] == (
        "验证注册表失败仅记录告警，连接仍返回实例。"
    )


def test_quality_repair_prompt_seed_selects_row_referenced_only_by_one_based_index():
    seed = json.dumps(
        [
            {"sfmea_id": "SFMEA-001", "failure_mode": "正常拒绝"},
            {"sfmea_id": "SFMEA-002", "failure_mode": "已验证风险"},
        ],
        ensure_ascii=False,
    )
    feedback = {
        "issues": [
            {
                "artifact": "sfmea.json",
                "code": "non_risk_sfmea_row",
                "index": 1,
            }
        ]
    }

    prompt_seed = json.loads(
        _quality_repair_prompt_seed(
            current_artifact_seed=seed,
            artifact="sfmea.json",
            quality_feedback=feedback,
        )
    )

    assert prompt_seed == [{"sfmea_id": "SFMEA-001", "failure_mode": "正常拒绝"}]


def test_sfmea_nonrisk_tombstone_resolves_one_based_issue_index():
    result = _apply_sfmea_nonrisk_deletion_tombstones(
        [],
        quality_feedback={
            "issues": [
                {
                    "artifact": "sfmea.json",
                    "code": "non_risk_sfmea_row",
                    "index": 1,
                }
            ]
        },
        base_items=[
            {"sfmea_id": "SFMEA-001", "failure_mode": "正常拒绝"},
            {"sfmea_id": "SFMEA-002", "failure_mode": "已验证风险"},
        ],
    )

    assert result == [{"sfmea_id": "SFMEA-001", "_delete": True}]


def test_sfmea_normalizer_replaces_test_only_or_guard_inversion_with_product_risk_candidate():
    rendered = [
        {
            "sfmea_id": "SFMEA-09",
            "failure_mode": "登录参数更新在非 Full Feature 阶段执行",
            "cause": "风险假设：若 full_feature 标志未正确设置，参数更新可能被跳过",
            "mechanism": "风险假设：检查 full_feature 状态",
            "technical_claims": [
                {
                    "statement": "if (conn->full_feature) {",
                    "evidence": [{"evidence_id": "SRC-01:L1119", "path": "lib/iscsi/iscsi.c", "quote": "if (conn->full_feature) {"}],
                }
            ],
        },
        {
            "sfmea_id": "SFMEA-11",
            "failure_mode": "登录请求 PDU 分配失败未处理",
            "cause": "测试 helper 假设",
            "mechanism": "风险假设：fuzz helper",
            "technical_claims": [
                {
                    "statement": "req_pdu = iscsi_get_pdu(conn);",
                    "evidence": [{"evidence_id": "SRC-TEST:L531", "path": "test/app/fuzz/iscsi.c", "quote": "req_pdu = iscsi_get_pdu(conn);"}],
                }
            ],
        },
        {
            "sfmea_id": "SFMEA-12",
            "failure_mode": "连接销毁时 socket 关闭顺序不当",
            "cause": "风险假设：若 socket 未先关闭，可能处理残留数据",
            "mechanism": "风险假设：清理连接",
            "technical_claims": [
                {
                    "statement": "iscsi_poll_group_remove_conn(conn->pg, conn);",
                    "evidence": [{"evidence_id": "SRC-06:L630", "path": "lib/iscsi/conn.c", "quote": "iscsi_poll_group_remove_conn(conn->pg, conn);"}],
                }
            ],
        },
        {
            "sfmea_id": "SFMEA-13",
            "failure_mode": "连接析构时未注销所有定时器",
            "cause": "风险假设：注销未注册定时器可能导致内存泄漏",
            "mechanism": "风险假设：注销 logout_request_timer",
            "technical_claims": [
                {
                    "statement": "spdk_poller_unregister(&conn->logout_request_timer);",
                    "evidence": [{"evidence_id": "SRC-06:L633", "path": "lib/iscsi/conn.c", "quote": "spdk_poller_unregister(&conn->logout_request_timer);"}],
                }
            ],
        },
        {
            "sfmea_id": "SFMEA-14",
            "failure_mode": "超过 MaxConnections 限制后仍接受新连接",
            "cause": "风险假设：检查与连接添加不是原子操作",
            "mechanism": "风险假设：并发登录绕过连接数限制",
            "technical_claims": [
                {
                    "statement": "if (sess->connections >= sess->MaxConnections) {",
                    "evidence": [{"evidence_id": "SRC-10:L707", "path": "lib/iscsi/iscsi.c", "quote": "if (sess->connections >= sess->MaxConnections) {"}],
                }
            ],
        },
        {
            "sfmea_id": "SFMEA-15",
            "failure_mode": "连接添加时未校验会话归属",
            "cause": "风险假设：会话归属校验失败后仍继续添加连接",
            "mechanism": "风险假设：连接添加到错误会话",
            "technical_claims": [
                {
                    "statement": "return ISCSI_LOGIN_CONN_ADD_FAIL;",
                    "evidence": [{"evidence_id": "SRC-10:L704", "path": "lib/iscsi/iscsi.c", "quote": "return ISCSI_LOGIN_CONN_ADD_FAIL;"}],
                }
            ],
        },
    ]
    product_catalog = [
        {
            "evidence_id": "SRC-01:L1119",
            "path": "lib/iscsi/iscsi.c",
            "symbol": "",
            "lines": "L1119",
            "quote": "if (conn->full_feature) {",
        },
        {
            "evidence_id": "SRC-10:L711",
            "path": "lib/iscsi/iscsi.c",
            "symbol": "",
            "lines": "L711",
            "quote": "return ISCSI_LOGIN_TOO_MANY_CONNECTIONS;",
        },
        {
            "evidence_id": "SRC-10:L707",
            "path": "lib/iscsi/iscsi.c",
            "symbol": "",
            "lines": "L707",
            "quote": "if (sess->connections >= sess->MaxConnections) {",
        },
        {
            "evidence_id": "SRC-10:L704",
            "path": "lib/iscsi/iscsi.c",
            "symbol": "",
            "lines": "L704",
            "quote": "return ISCSI_LOGIN_CONN_ADD_FAIL;",
        },
        {
            "evidence_id": "SRC-06:L630",
            "path": "lib/iscsi/conn.c",
            "symbol": "",
            "lines": "L630",
            "quote": "iscsi_poll_group_remove_conn(conn->pg, conn);",
        },
        {
            "evidence_id": "SRC-06:L633",
            "path": "lib/iscsi/conn.c",
            "symbol": "",
            "lines": "L633",
            "quote": "spdk_poller_unregister(&conn->logout_request_timer);",
        },
    ]

    normalized, changed = _normalize_sfmea_risk_contract(
        rendered,
        product_claim_catalog=product_catalog,
    )

    assert "SFMEA-09:source_risk_candidate" in changed
    assert "SFMEA-11:source_risk_candidate" in changed
    assert "SFMEA-12:source_risk_candidate" in changed
    assert "SFMEA-13:source_risk_candidate" in changed
    assert "SFMEA-14:source_risk_candidate" in changed
    assert "SFMEA-15:source_risk_candidate" in changed
    by_id = {row["sfmea_id"]: row for row in normalized}
    assert "参数更新在非 Full Feature 阶段执行" not in by_id["SFMEA-09"]["failure_mode"]
    assert "错误接受" in by_id["SFMEA-11"]["failure_mode"]
    assert by_id["SFMEA-11"]["technical_claims"][0]["evidence"][0]["path"] == "lib/iscsi/iscsi.c"
    assert "残留" in by_id["SFMEA-12"]["failure_mode"]
    assert "并发" in by_id["SFMEA-13"]["failure_mode"]
    # SFMEA-14 and SFMEA-11 normalize to the same verified MaxConnections
    # hypothesis. Delivery de-duplicates semantic twins before publication.
    assert "SFMEA-14" not in by_id
    assert "错误返回" in by_id["SFMEA-15"]["failure_mode"]
    assert all(row["risk_status"] == "test_hypothesis" for row in normalized)


def test_sfmea_normalizer_removes_unbound_exact_log_claim_from_detection():
    rendered = [
        {
            "sfmea_id": "SFMEA-01",
            "failure_mode": "登录超时导致会话不可用",
            "detection": "检查 SPDK 日志原文“Connection is already exited”",
            "technical_claims": [
                {
                    "statement": "conn->state = ISCSI_CONN_STATE_EXITING;",
                    "evidence": [
                        {
                            "evidence_id": "SRC-01:L153",
                            "path": "lib/iscsi/conn.c",
                            "quote": "conn->state = ISCSI_CONN_STATE_EXITING;",
                        }
                    ],
                }
            ],
        }
    ]

    normalized, changed = _normalize_sfmea_risk_contract(rendered)

    assert "$[0].detection:unbound_exact_log" in changed
    assert "Connection is already exited" not in normalized[0]["detection"]
    assert "公开 initiator" in normalized[0]["detection"]
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
                    "sfmea_id": "SFMEA-001",
                    "failure_mode": "login timeout",
                    "mechanism": "the bounded login timer expires while the peer is silent",
                    "trigger_condition": "the peer sends no valid Login continuation before timeout",
                    "cause": "peer silent",
                    "effect": "session unavailable",
                    "local_effect": "the pending Login exchange stops",
                    "upstream_effect": "the initiator receives no usable session",
                    "downstream_effect": "I/O setup cannot begin",
                    "final_effect": "the target session remains unavailable",
                    "latent": "not latent; externally visible at Login timeout",
                    "detection": "timeout log",
                    "existing_controls": "bounded Login timer and error response path",
                    "control_gaps": "timer expiry needs externally observable regression coverage",
                    "severity": 7,
                    "occurrence": 3,
                    "detection_score": 2,
                    "rpn": 42,
                    "score_explanation": "service unavailable",
                    "mitigation": "bounded retry",
                    "recovery_verification": "retry Login after the timed-out connection is closed",
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
                    "risk_ids": ["SFMEA-001"],
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
        "breadth_inventory",
        "developer_explanation",
        "scenario_expansion",
        "business_flow",
        "sfmea",
        "black_box_cases",
        "test_design",
    ]
    stages = {stage["id"]: stage for stage in plan["stages"]}
    assert stages["breadth_inventory"]["depends_on"] == ["flow_outline"]
    assert stages["developer_explanation"]["depends_on"] == ["breadth_inventory"]
    assert stages["scenario_expansion"]["depends_on"] == ["breadth_inventory"]
    assert stages["sfmea"]["depends_on"] == ["source_analysis", "flow_outline"]
    assert stages["sfmea"]["artifact"] == "sfmea.json"
    assert stages["business_flow"]["depends_on"] == ["flow_outline"]
    assert "第一行" in plan["original_user_request"]
    assert "第二行" in plan["original_user_request"]
    source_stage = plan["stages"][0]
    assert source_stage["max_tokens"] == 1600
    assert source_stage["output_limits"]["max_chinese_characters"] == 1200
    assert source_stage["output_limits"]["max_evidence_anchors"] == 12


@pytest.mark.asyncio
async def test_rapid_test_activity_materializes_required_stage_contract_artifacts(tmp_path):
    plan = build_staged_execution_plan(
        contract=_contract(),
        original_user_request="分析 iSCSI login 并输出可执行测试设计",
        execution_profile={"id": "rapid"},
    )

    result = await execute_staged_builtin_plan(
        llm=_StageLLM(),
        plan=plan,
        artifact_dir=tmp_path,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
    )

    assert result["status"] == "completed"
    assert all((tmp_path / name).is_file() for name in (
        "entrypoints.json",
        "flows.json",
        "flow_cards.json",
        "scenario_candidates.json",
    ))


def test_deep_profile_plan_materializes_parallel_exploration_branches():
    """Deep runs must schedule bounded analysis work, not merely label artifacts."""
    plan = build_staged_execution_plan(
        contract=_contract(),
        original_user_request="完整分析 iSCSI login 的资源、异常、并发与恢复测试设计",
        execution_profile={
            "id": "deep",
            "label": "深度型",
            "delivery_class": "full_test_delivery",
            "max_subagents": 4,
        },
    )

    stages = {stage["id"]: stage for stage in plan["stages"]}
    branch_ids = {
        "deep_entry_paths",
        "deep_state_and_resources",
        "deep_failures_and_recovery",
        "deep_concurrency_and_boundaries",
    }

    assert plan["execution_profile"]["id"] == "deep"
    assert plan["execution_profile"]["applied_subagent_count"] == 4
    assert branch_ids.issubset(stages)
    assert all(stages[branch_id]["depends_on"] == ["flow_outline"] for branch_id in branch_ids)
    assert all(stages[branch_id]["support"] is True for branch_id in branch_ids)
    assert branch_ids.issubset(stages["business_flow"]["depends_on"])
    assert branch_ids.issubset(stages["sfmea"]["depends_on"])
    assert branch_ids.issubset(stages["black_box_cases"]["depends_on"])
    # Deep mode gains breadth from independently scoped branches, not from
    # silently defeating the V3 source-evidence/output budget per branch.
    assert all(stages[branch_id]["max_tokens"] <= 1600 for branch_id in branch_ids)
    assert all(
        stages[branch_id]["output_limits"]["max_evidence_anchors"] <= 12
        for branch_id in branch_ids
    )
    assert stages["source_analysis"]["max_tokens"] <= 1600
    assert stages["source_analysis"]["output_limits"]["max_evidence_anchors"] <= 12
    assert plan["execution_profile"]["source_analysis_limits"]["max_files"] <= 6
    assert plan["execution_profile"]["source_analysis_limits"]["max_evidence_anchors"] <= 12
    assert (
        plan["execution_profile"]["source_analysis_limits"]["max_tokens"]
        == stages["source_analysis"]["max_tokens"]
    )
    assert (
        plan["execution_profile"]["source_analysis_limits"]["max_chinese_characters"]
        == stages["source_analysis"]["output_limits"]["max_chinese_characters"]
    )


def test_plan_does_not_duplicate_source_evidence_pack_as_generic_artifact_stage():
    contract = _contract()
    contract["required_outputs"] = ["source_analysis.md", *contract["required_outputs"]]
    contract["artifact_contract"]["source_analysis.md"] = {
        "artifact": "source_analysis.md",
    }

    plan = build_staged_execution_plan(
        contract=contract,
        original_user_request="仅对已验证证据做源码分析摘要",
        execution_profile={"id": "deep", "max_subagents": 4},
    )

    source_stages = [
        stage for stage in plan["stages"] if stage.get("artifact") == "source_analysis.md"
    ]
    assert [stage["id"] for stage in source_stages] == ["source_analysis"]
    assert source_stages[0]["max_tokens"] == 1600


def test_deep_exploration_prompt_routes_evidence_by_branch_responsibility():
    """A deep branch must not receive the same first six cards as every peer."""
    source_pack = {
        "repo_revision": "abc123",
        "evidence_cards": [
            {
                "evidence_id": "SRC-01",
                "file_path": "lib/iscsi/iscsi.c",
                "classification": "source",
                "start_line": 100,
                "end_line": 110,
                "symbols": ["iscsi_auth_params"],
                "matched_terms": ["authentication"],
                "excerpt": "authentication parameter parsing",
            },
            {
                "evidence_id": "SRC-02",
                "file_path": "lib/iscsi/conn.c",
                "classification": "source",
                "start_line": 200,
                "end_line": 210,
                "symbols": ["login_timeout"],
                "matched_terms": ["login timeout"],
                "excerpt": "login timeout cleanup",
            },
            {
                "evidence_id": "SRC-03",
                "file_path": "lib/iscsi/iscsi.c",
                "classification": "source",
                "start_line": 700,
                "end_line": 714,
                "symbols": ["append_iscsi_sess"],
                "matched_terms": ["MaxConnections", "tsih"],
                "excerpt": "if (sess->connections >= sess->MaxConnections)",
            },
            {
                "evidence_id": "SRC-04",
                "file_path": "lib/iscsi/iscsi.c",
                "classification": "source",
                "start_line": 130,
                "end_line": 135,
                "symbols": ["iscsi_conn_login_complete"],
                "matched_terms": ["login completion"],
                "excerpt": "login completion callback",
            },
            {
                "evidence_id": "SRC-05",
                "file_path": "lib/iscsi/conn.c",
                "classification": "source",
                "start_line": 300,
                "end_line": 305,
                "symbols": ["iscsi_conn_destruct"],
                "matched_terms": ["connection cleanup"],
                "excerpt": "connection cleanup",
            },
            {
                "evidence_id": "SRC-06",
                "file_path": "test/iscsi_tgt/login.sh",
                "classification": "test",
                "start_line": 10,
                "end_line": 20,
                "symbols": ["login_test"],
                "matched_terms": ["login"],
                "excerpt": "basic login test",
            },
            {
                "evidence_id": "SRC-07",
                "file_path": "lib/iscsi/iscsi.c",
                "classification": "source",
                "start_line": 4720,
                "end_line": 4730,
                "symbols": ["iscsi_pdu_payload_read"],
                "matched_terms": ["data digest error"],
                "excerpt": "data digest error",
            },
            {
                "evidence_id": "SRC-08",
                "file_path": "lib/iscsi/iscsi.c",
                "classification": "source",
                "start_line": 703,
                "end_line": 708,
                "symbols": ["append_iscsi_sess"],
                "matched_terms": ["TODO: need a mutex"],
                "excerpt": "TODO: need a mutex around session append",
            },
        ],
    }
    plan = {"original_user_request": "分析 iSCSI Login 的 MCS、TSIH 和 Digest 错误"}
    prompt = _deep_exploration_stage_prompt(
        plan=plan,
        stage={
            "id": "deep_failures_and_recovery",
            "artifact": "deep_exploration/failures.md",
            "purpose": "异常传播、超时、取消、断连与恢复探索",
            "output_limits": {"max_chinese_characters": 1800},
        },
        source_pack=source_pack,
        outline={},
    )

    assert "SRC-07" in prompt
    assert "data digest error" in prompt
    assert "SRC-03" in prompt
    assert "至少引用两个不同的 routed evidence_id" in prompt


def test_deep_profile_requires_governed_source_driven_stage_chain_for_basic_report():
    """A combined report must not silently bypass the nine-stage activity contract."""
    plan = build_staged_execution_plan(
        contract={
            "target": "iSCSI login",
            "required_outputs": ["report.md"],
            "artifact_contract": {"report.md": {"artifact": "report.md"}},
        },
        original_user_request="分析 iSCSI login 并输出测试报告",
        execution_profile={"id": "deep", "max_subagents": 2},
    )

    stages = {stage["id"]: stage for stage in plan["stages"]}

    assert plan["required_outputs"] == ["report.md"]
    assert {
        "breadth_inventory",
        "developer_explanation",
        "scenario_expansion",
        "test_design_governance",
        "coverage_judge",
    }.issubset(stages)
    assert stages["breadth_inventory"]["artifact"] == "entrypoints.json"
    assert stages["scenario_expansion"]["artifact"] == "scenario_candidates.json"
    assert stages["coverage_judge"]["artifact"] == "judge_report.json"
    assert stages["sfmea"]["support"] is True
    assert stages["black_box_cases"]["support"] is True
    assert stages["coverage_judge"]["support"] is True


def test_quality_reuse_rejects_partial_multi_artifact_stage(tmp_path):
    artifact = tmp_path / "entrypoints.json"
    artifact.write_text("{}", encoding="utf-8")

    reused = _existing_quality_stage_result(
        plan={"quality_retry_feedback": {"issues": []}},
        artifact_dir=tmp_path,
        stage_dir=tmp_path / "stages" / "breadth_inventory",
        stage={
            "id": "breadth_inventory",
            "artifact": "entrypoints.json",
            "produces_artifacts": ["entrypoints.json", "flows.json"],
        },
    )

    assert reused is None


def test_quality_reuse_preserves_prior_provider_metrics_for_auditable_depth(tmp_path):
    artifact = tmp_path / "black_box_cases.json"
    artifact.write_text("[]", encoding="utf-8")
    stage_dir = tmp_path / "stages" / "black_box_cases"
    stage_dir.mkdir(parents=True)
    (stage_dir / "stage_result.json").write_text(
        json.dumps(
            {
                "stage_id": "black_box_cases",
                "status": "completed",
                "attempt_count": 1,
                "provider_call_count": 1,
                "provider_wait_ms": 3210.5,
                "output_tokens": 456,
                "model": "deepseek-v4-pro",
                "finish_reason": "stop",
            }
        ),
        encoding="utf-8",
    )

    reused = _existing_quality_stage_result(
        plan={"quality_retry_feedback": {"issues": []}},
        artifact_dir=tmp_path,
        stage_dir=stage_dir,
        stage={"id": "black_box_cases", "artifact": "black_box_cases.json"},
    )

    assert reused is not None
    assert reused["reused"] is True
    assert reused["provider_call_count"] == 1
    assert reused["output_tokens"] == 456
    assert reused["prior_execution_metrics"]["provider_wait_ms"] == 3210.5


def test_deep_profile_evidence_rejects_reuse_without_prior_branch_provider_work(tmp_path):
    for stage_id in ("deep_entry_paths", "deep_state_and_resources", "black_box_cases"):
        stage_dir = tmp_path / "stages" / stage_id
        stage_dir.mkdir(parents=True)
        (stage_dir / "stage_result.json").write_text(
            json.dumps({"stage_id": stage_id, "status": "completed", "reused": True}),
            encoding="utf-8",
        )

    evidence = build_profile_execution_evidence(
        artifact_dir=tmp_path,
        execution_profile={"id": "deep", "applied_subagent_count": 2},
    )

    assert evidence["status"] == "blocked"
    assert evidence["missing_branch_provider_work"] == [
        "deep_entry_paths",
        "deep_state_and_resources",
    ]
    assert evidence["missing_delivery_provider_work"] is True


def test_sfmea_missing_technical_claim_is_materialized_from_its_exact_source_reference():
    rendered = [
        {
            "sfmea_id": "SFMEA-010",
            "source_evidence": ["lib/iscsi/conn.c:147-158"],
            "technical_claims": [],
        }
    ]
    catalog = [
        {
            "evidence_id": "SRC-03:L153",
            "path": "lib/iscsi/conn.c",
            "lines": "L153",
            "quote": "conn->state = ISCSI_CONN_STATE_EXITING;",
            "symbol": "login_timeout",
        }
    ]

    normalized = _materialize_missing_sfmea_source_anchor_claims(rendered, catalog)

    claim = normalized[0]["technical_claims"][0]
    assert claim["claim_id"] == "TC-SFMEA-010-SOURCE"
    assert claim["statement"] == "conn->state = ISCSI_CONN_STATE_EXITING;"
    assert claim["evidence"][0]["evidence_id"] == "SRC-03:L153"


def test_deep_profile_evidence_does_not_report_a_deadline_failure_when_work_passed(tmp_path):
    for stage_id in ("deep_entry_paths", "deep_state_and_resources", "black_box_cases"):
        stage_dir = tmp_path / "stages" / stage_id
        stage_dir.mkdir(parents=True)
        (stage_dir / "stage_result.json").write_text(
            json.dumps(
                {"stage_id": stage_id, "status": "completed", "provider_call_count": 1}
            ),
            encoding="utf-8",
        )

    evidence = build_profile_execution_evidence(
        artifact_dir=tmp_path,
        execution_profile={"id": "deep", "applied_subagent_count": 2},
    )

    assert evidence["status"] == "passed"
    assert evidence["reason"] != "workflow_deadline_exceeded"
    assert "真实模型工作" in evidence["reason"]


def test_deep_profile_evidence_rejects_branch_without_its_routed_citations(tmp_path):
    (tmp_path / "staged_execution_plan.json").write_text(
        json.dumps(
            {
                "original_user_request": "分析 iSCSI Login 的 MCS、TSIH 和 Digest 错误",
                "execution_profile": {"id": "deep", "applied_subagent_count": 2},
            }
        ),
        encoding="utf-8",
    )
    source_stage = tmp_path / "stages" / "source_analysis"
    source_stage.mkdir(parents=True)
    (source_stage / "source_evidence_pack.json").write_text(
        json.dumps(
            {
                "evidence_cards": [
                    {
                        "evidence_id": "SRC-01",
                        "file_path": "lib/iscsi/iscsi.c",
                        "symbols": ["iscsi_auth_params"],
                        "excerpt": "authentication",
                    },
                    {
                        "evidence_id": "SRC-02",
                        "file_path": "lib/iscsi/iscsi.c",
                        "symbols": ["append_iscsi_sess"],
                        "matched_terms": ["MaxConnections", "tsih"],
                        "excerpt": "MaxConnections tsih",
                    },
                    {
                        "evidence_id": "SRC-03",
                        "file_path": "lib/iscsi/iscsi.c",
                        "symbols": ["iscsi_pdu_payload_read"],
                        "matched_terms": ["data digest error"],
                        "excerpt": "data digest error",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    for stage_id, citations in {
        "deep_entry_paths": "SRC-01 SRC-02",
        # This branch gets Digest evidence but only cites unrelated Login cards.
        "deep_state_and_resources": "SRC-01",
        "black_box_cases": "SRC-01 SRC-02",
    }.items():
        stage_dir = tmp_path / "stages" / stage_id
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "stage_result.json").write_text(
            json.dumps(
                {
                    "stage_id": stage_id,
                    "status": "completed",
                    "provider_call_count": 1,
                }
            ),
            encoding="utf-8",
        )
        (stage_dir / "raw_output.txt").write_text(citations, encoding="utf-8")

    evidence = build_profile_execution_evidence(
        artifact_dir=tmp_path,
        execution_profile={"id": "deep", "applied_subagent_count": 2},
    )

    assert evidence["status"] == "blocked"
    assert evidence["under_evidenced_branches"] == ["deep_state_and_resources"]


def test_deep_profile_evidence_accepts_a_routed_file_and_line_reference(tmp_path):
    (tmp_path / "staged_execution_plan.json").write_text(
        json.dumps({"original_user_request": "分析 iSCSI Login"}), encoding="utf-8"
    )
    source_stage = tmp_path / "stages" / "source_analysis"
    source_stage.mkdir(parents=True)
    (source_stage / "source_evidence_pack.json").write_text(
        json.dumps({"evidence_cards": [{
            "evidence_id": "SRC-01",
            "file_path": "lib/iscsi/iscsi.c",
            "start_line": 1114,
            "end_line": 1132,
            "symbols": ["iscsi_conn_login_pdu_err_complete"],
            "matched_terms": ["login"],
            "excerpt": "login error completion",
        }, {
            "evidence_id": "SRC-02",
            "file_path": "test/iscsi_tgt/chap/chap_common.sh",
            "start_line": 82,
            "end_line": 99,
            "symbols": ["config_chap_credentials_for_target"],
            "matched_terms": ["chap"],
            "excerpt": "chap setup",
        }]}),
        encoding="utf-8",
    )
    for stage_id in ("deep_entry_paths", "deep_state_and_resources", "black_box_cases"):
        stage_dir = tmp_path / "stages" / stage_id
        stage_dir.mkdir(parents=True)
        (stage_dir / "stage_result.json").write_text(
            json.dumps({"stage_id": stage_id, "status": "completed", "provider_call_count": 1}),
            encoding="utf-8",
        )
        citations = "SRC-01 SRC-02" if stage_id != "deep_entry_paths" else (
            "lib/iscsi/iscsi.c:1130\ntest/iscsi_tgt/chap/chap_common.sh:82-99"
        )
        (stage_dir / "raw_output.txt").write_text(citations, encoding="utf-8")

    evidence = build_profile_execution_evidence(
        artifact_dir=tmp_path,
        execution_profile={"id": "deep", "applied_subagent_count": 2},
    )

    assert evidence["status"] == "passed"
    assert evidence["branch_citation_requirements"]["deep_entry_paths"]["cited_evidence_ids"] == ["SRC-01", "SRC-02"]


def test_deep_profile_evidence_reads_materialized_branch_artifact_when_raw_is_absent(tmp_path):
    (tmp_path / "staged_execution_plan.json").write_text(json.dumps({"original_user_request": "iSCSI Login"}), encoding="utf-8")
    source = tmp_path / "stages" / "source_analysis"
    source.mkdir(parents=True)
    (source / "source_evidence_pack.json").write_text(json.dumps({"evidence_cards": [
        {"evidence_id": "SRC-01", "file_path": "lib/iscsi/iscsi.c", "start_line": 10, "end_line": 20, "excerpt": "login"},
        {"evidence_id": "SRC-02", "file_path": "test/iscsi_tgt/login.sh", "start_line": 1, "end_line": 5, "excerpt": "login"},
    ]}), encoding="utf-8")
    for stage_id in ("deep_entry_paths", "deep_state_and_resources", "black_box_cases"):
        stage_dir = tmp_path / "stages" / stage_id
        stage_dir.mkdir(parents=True)
        artifact = "deep_exploration/entry_paths.md" if stage_id == "deep_entry_paths" else f"{stage_id}.md"
        output = tmp_path / artifact
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("SRC-01 SRC-02", encoding="utf-8")
        (stage_dir / "stage_result.json").write_text(json.dumps({"stage_id": stage_id, "status": "completed", "provider_call_count": 1, "artifact": artifact}), encoding="utf-8")
    evidence = build_profile_execution_evidence(artifact_dir=tmp_path, execution_profile={"id": "deep", "applied_subagent_count": 2})
    assert evidence["status"] == "passed"


def test_quality_reuse_always_rebuilds_derived_judge(tmp_path):
    (tmp_path / "judge_report.json").write_text("{}", encoding="utf-8")
    reused = _existing_quality_stage_result(
        plan={"quality_retry_feedback": {"issues": []}}, artifact_dir=tmp_path,
        stage_dir=tmp_path / "stages" / "coverage_judge",
        stage={"id": "coverage_judge", "artifact": "judge_report.json"},
    )
    assert reused is None


@pytest.mark.asyncio
async def test_deep_profile_executes_exploration_branches_before_delivery_stages(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("app.services.ai_staged_execution.settings.llm_max_concurrency", 4)
    llm = _StageLLM()
    plan = build_staged_execution_plan(
        contract=_contract(),
        original_user_request="完整分析 iSCSI login 的资源、异常、并发与恢复测试设计",
        execution_profile={"id": "deep", "max_subagents": 4},
    )

    result = await execute_staged_builtin_plan(
        llm=llm,
        plan=plan,
        artifact_dir=tmp_path,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
    )

    branch_ids = (
        "deep_entry_paths",
        "deep_state_and_resources",
        "deep_failures_and_recovery",
        "deep_concurrency_and_boundaries",
    )
    assert result["status"] == "completed"
    assert all(llm.calls_by_stage.get(branch_id) == 1 for branch_id in branch_ids)
    assert all(
        (tmp_path / "deep_exploration" / filename).is_file()
        for filename in (
            "entry_paths.md",
            "state_and_resources.md",
            "failures_and_recovery.md",
            "concurrency_and_boundaries.md",
        )
    )
    assert json.loads(
        (tmp_path / "stages" / "deep_entry_paths" / "stage_result.json").read_text()
    )["subagent_role"] == "deep_entry_paths"


@pytest.mark.asyncio
async def test_truncated_deep_support_branch_is_preserved_without_blocking_delivery(tmp_path):
    class TruncatedDeepBranchLLM(_StageLLM):
        async def complete(self, messages, max_tokens=4096, temperature=0.2):
            response = await super().complete(messages, max_tokens, temperature)
            stage = next(
                line.split(":", 1)[1].strip()
                for line in messages[-1]["content"].splitlines()
                if line.startswith("STAGE_ID:")
            )
            if stage == "deep_concurrency_and_boundaries":
                return LLMResponse(
                    content="# 并发探索\n\n- SRC-01: 已保留的并发风险摘要",
                    model="stage-test",
                    usage={},
                    truncated=True,
                    finish_reason="length",
                )
            return response

    plan = build_staged_execution_plan(
        contract=_contract(),
        original_user_request="完整分析 iSCSI login 的资源、异常、并发与恢复测试设计",
        execution_profile={"id": "deep", "max_subagents": 4},
    )
    result = await execute_staged_builtin_plan(
        llm=TruncatedDeepBranchLLM(),
        plan=plan,
        artifact_dir=tmp_path,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
    )

    branch_result = json.loads(
        (
            tmp_path
            / "stages"
            / "deep_concurrency_and_boundaries"
            / "stage_result.json"
        ).read_text()
    )
    assert result["status"] == "completed"
    assert branch_result["status"] == "completed"
    assert branch_result["degraded"] is True
    assert branch_result["finish_reason"] == "truncated_support_preserved"
    assert (tmp_path / "sfmea.json").is_file()
    assert "达到输出上限" in (
        tmp_path / "deep_exploration" / "concurrency_and_boundaries.md"
    ).read_text()


def test_deep_exploration_branch_uses_bounded_markdown_context():
    plan = build_staged_execution_plan(
        contract=_contract(),
        original_user_request="分析 iSCSI login 的资源生命周期与耗尽条件",
        execution_profile={"id": "deep", "max_subagents": 4},
    )
    stage = next(
        item for item in plan["stages"]
        if item["id"] == "deep_state_and_resources"
    )
    source_pack = {
        "repo_revision": "abc123",
        "evidence_cards": [
            {
                "evidence_id": f"SRC-{index:02d}",
                "file_path": f"lib/iscsi/file_{index}.c",
                "classification": "source",
                "start_line": index,
                "end_line": index + 1,
                "symbols": [f"iscsi_symbol_{index}"],
                "excerpt": "verified source line\n" * 400,
            }
            for index in range(12)
        ],
    }

    prompt = _regular_stage_prompt(
        plan=plan,
        stage=stage,
        source_pack=source_pack,
        flow_pack={"entry_points": [{"symbol": "iscsi_login"}] * 100},
        outline={"steps": [{"summary": "login"}] * 100},
        completed={},
    )

    assert stage["max_tokens"] <= 1600
    assert stage["output_limits"]["max_chinese_characters"] <= 1800
    assert len(prompt) < 16_000
    assert "必须直接以 Markdown 标题或列表开始，不得使用 JSON" in prompt
    assert "SRC-09" not in prompt


def test_source_driven_v2_plan_groups_ledgers_and_mindmap_without_extra_model_calls():
    from app.services.source_driven_test_design import (
        MINDMAP_ARTIFACTS,
        SOURCE_DRIVEN_V2_ARTIFACTS,
    )

    required = [
        "source_scope.json",
        "evidence_cards.json",
        "flow_map.md",
        "sfmea.json",
        "black_box_cases.json",
        *SOURCE_DRIVEN_V2_ARTIFACTS,
        *MINDMAP_ARTIFACTS,
    ]
    plan = build_staged_execution_plan(
        contract={
            "target": "SPDK iSCSI Login",
            "required_outputs": required,
            "artifact_contract": {name: {"artifact": name} for name in required},
        },
        original_user_request="分析 SPDK iSCSI Login 并输出测试设计脑图",
    )

    stage_ids = [stage["id"] for stage in plan["stages"]]
    assert stage_ids == [
        "source_analysis",
        "source_scope",
        "evidence_cards",
        "flow_evidence_pack",
        "flow_outline",
        "breadth_inventory",
        "developer_explanation",
        "scenario_expansion",
        "business_flow",
        "sfmea",
        "black_box_cases",
        "test_design_governance",
        "coverage_judge",
        "test_design_mindmap",
    ]
    grouped = {stage["id"]: stage for stage in plan["stages"]}
    assert set(grouped["breadth_inventory"]["produces_artifacts"]) == {
        "entrypoints.json", "flows.json", "states.json", "resources.json", "model_applicability.json"
    }
    assert grouped["sfmea"]["depends_on"] == ["source_analysis", "flow_outline"]
    assert grouped["black_box_cases"]["depends_on"] == [
        "source_analysis", "flow_outline", "sfmea", "scenario_expansion"
    ]
    assert grouped["coverage_judge"]["depends_on"] == ["test_design_governance"]
    assert grouped["test_design_mindmap"]["depends_on"] == ["coverage_judge"]
    assert all(grouped[name].get("deterministic") for name in (
        "breadth_inventory", "developer_explanation", "scenario_expansion",
        "test_design_governance", "coverage_judge", "test_design_mindmap",
    ))


def test_stage_artifact_writers_use_atomic_replace(tmp_path, monkeypatch):
    import app.services.ai_staged_execution as staged

    replacements: list[tuple[str, str]] = []
    real_replace = staged.os.replace

    def recording_replace(source, destination):
        replacements.append((str(source), str(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(staged.os, "replace", recording_replace)
    json_path = tmp_path / "judge_report.json"
    text_path = tmp_path / "test_design_mindmap.svg"

    staged._write_json(json_path, {"status": "BLOCKED"})
    staged._write_text(text_path, "<svg />")

    assert json_path.read_text(encoding="utf-8").startswith("{")
    assert text_path.read_text(encoding="utf-8") == "<svg />"
    assert [destination for _, destination in replacements] == [
        str(json_path),
        str(text_path),
    ]


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


def test_flow_edges_are_promoted_into_the_l1_source_evidence_ledger():
    from app.services.ai_staged_execution import _merge_verified_flow_edges_into_source_pack

    merged = _merge_verified_flow_edges_into_source_pack(
        {
            "evidence_cards": [{
                "evidence_id": "SRC-01",
                "file_path": "lib/iscsi/iscsi.c",
                "start_line": 100,
                "end_line": 102,
                "excerpt": "int login(void);",
                "sha256": "a" * 64,
            }],
        },
        {
            "call_edges": [{
                "evidence_id": "FLOW-EDGE-001",
                "file_path": "lib/iscsi/iscsi.c",
                "start_line": 1889,
                "end_line": 1889,
                "matched_text": "rc = iscsi_op_login_session_discovery_chap(conn);",
                "from_symbol": "iscsi_op_login_phase_none",
                "to_symbol": "iscsi_op_login_session_discovery_chap",
                "sha256": "b" * 64,
            }],
        },
    )

    edge = next(item for item in merged["evidence_cards"] if item["evidence_id"] == "FLOW-EDGE-001")
    assert edge["excerpt"] == "rc = iscsi_op_login_session_discovery_chap(conn);"
    assert edge["start_line"] == 1889
    assert edge["sha256"] == "b" * 64
    assert edge["kind"] == "source"
    assert edge["source"] == "flow-evidence-pack"
    assert edge["reason"]


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
    assert "必须包含一个 source_anchor" in prompt


def test_verified_claim_catalog_keeps_single_card_enclosing_symbol_on_internal_line():
    catalog = _build_verified_claim_catalog({
        "evidence_cards": [{
            "evidence_id": "SRC-02",
            "file_path": "lib/iscsi/param.c",
            "classification": "source",
            "start_line": 319,
            "end_line": 321,
            "symbols": ["iscsi_parse_params"],
            "excerpt": "if (cbit_enabled) {\n\treturn -1;\n}",
            "sha256": "b" * 64,
        }],
    })

    assert catalog[0]["symbol"] == "iscsi_parse_params"
    assert catalog[0]["quote"] == "if (cbit_enabled) {"


def test_verified_claim_catalog_excludes_incomplete_declaration_fragments():
    """A split C declaration is context, never a model-selectable fact."""
    catalog = _build_verified_claim_catalog({
        "evidence_cards": [{
            "evidence_id": "SRC-03",
            "file_path": "lib/iscsi/conn.c",
            "classification": "source",
            "start_line": 160,
            "end_line": 168,
            "symbols": ["iscsi_conn_start", "iscsi_conn_construct"],
            "excerpt": (
                "static void\n"
                "iscsi_conn_start(void *ctx)\n"
                "{\n"
                "\tstruct spdk_iscsi_conn *conn = ctx;\n"
                "\tiscsi_poll_group_add_conn(conn->pg, conn);\n"
                "\tconn->login_timer = SPDK_POLLER_REGISTER(login_timeout, conn, 30);\n"
                "}\n"
            ),
            "sha256": "c" * 64,
        }],
    })

    quotes = {item["quote"] for item in catalog}
    assert "static void" not in quotes
    assert "struct spdk_iscsi_conn *conn = ctx;" not in quotes
    assert "iscsi_conn_start(void *ctx)" in quotes
    assert "conn->login_timer = SPDK_POLLER_REGISTER(login_timeout, conn, 30);" in quotes


def test_black_box_prompt_exposes_only_materialized_sfmea_risk_ids(tmp_path):
    sfmea = tmp_path / "sfmea.json"
    sfmea.write_text(
        json.dumps(
            [
                {
                    "sfmea_id": "SFMEA-01",
                    "failure_mode": "登录超时后的资源清理未完成",
                    "source_evidence": ["lib/iscsi/conn.c:iscsi_conn_stop"],
                },
                {
                    "sfmea_id": "SFMEA-02",
                    "failure_mode": "Digest 错误后的连接恢复失败",
                    "source_evidence": ["lib/iscsi/iscsi.c:iscsi_op_login_rsp_init"],
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    prompt = _regular_stage_prompt(
        plan={"original_user_request": "分析 iSCSI login"},
        stage={
            "id": "black_box_cases",
            "artifact": "black_box_cases.json",
            "purpose": "黑盒用例",
            "depends_on": ["sfmea"],
            "output_contract": {"schema": {"type": "array"}},
        },
        source_pack={"evidence_cards": []},
        flow_pack={},
        outline={},
        completed={"sfmea": sfmea},
    )

    assert "SFMEA_RISK_LEDGER" in prompt
    assert '"sfmea_id": "SFMEA-01"' in prompt
    assert '"sfmea_id": "SFMEA-02"' in prompt
    assert "SFMEA-RISK-01" not in prompt
    assert "risk_ids 只能引用 SFMEA_RISK_LEDGER 中逐字列出的 sfmea_id" in prompt


def test_sfmea_prompt_excludes_test_only_claim_anchors(tmp_path):
    source_pack = {
        "repo_revision": "abc123",
        "evidence_cards": [
            {
                "evidence_id": "SRC-01",
                "file_path": "lib/iscsi/iscsi.c",
                "classification": "source",
                "start_line": 100,
                "end_line": 100,
                "symbols": ["iscsi_login"],
                "excerpt": "return ISCSI_LOGIN_TOO_MANY_CONNECTIONS;",
                "sha256": "a" * 64,
            },
            {
                "evidence_id": "TEST-01",
                "file_path": "test/app/fuzz/iscsi_fuzz/iscsi_fuzz.c",
                "classification": "test",
                "start_line": 20,
                "end_line": 20,
                "symbols": ["fuzz_login"],
                "excerpt": "request = calloc(1, sizeof(*request));",
                "sha256": "b" * 64,
            },
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

    catalog_text = prompt.split("VERIFIED_CLAIM_EVIDENCE_CATALOG:", 1)[1]
    assert "SRC-01:L100" in catalog_text
    assert "TEST-01:L20" not in catalog_text
    assert "test/tests/fuzz/harness" in prompt


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
    assert canonicalized[0]["technical_claims"][0]["statement"] == "Login accept is zero."
    # A non-canonical behavior assertion must not survive as a stale claim
    # that later poisons the quality repair loop.
    assert canonicalized[1]["technical_claims"] == []


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
    assert "严禁新增任何 ID" not in prompt
    assert "必须返回完整的修复后顶层值" not in prompt
    assert "必须输出至少 12 个" not in prompt


def test_sfmea_non_risk_repair_prompt_allows_verified_row_deletion():
    prompt = _regular_stage_prompt(
        plan={
            "original_user_request": "分析资源生命周期",
            "quality_retry_feedback": {
                "affected_artifacts": ["sfmea.json"],
                "issues": [
                    {
                        "artifact": "sfmea.json",
                        "row_id": "SFMEA-02",
                        "code": "non_risk_sfmea_row",
                        "message": "该行描述的是正常释放行为",
                    }
                ],
            },
        },
        stage={
            "id": "sfmea",
            "artifact": "sfmea.json",
            "purpose": "SFMEA",
            "depends_on": [],
            "output_contract": {"schema": {"type": "array", "minItems": 1}},
        },
        source_pack={"evidence_cards": []},
        flow_pack={},
        outline={},
        completed={},
        current_artifact_seed='[{"sfmea_id":"SFMEA-02","failure_mode":"正常释放"}]',
    )

    assert '"_delete": true' in prompt
    assert "不得为了数量补造" in prompt
    assert "不得把正常行为包装成风险" in prompt


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
    assert "严禁新增任何 ID" in prompt
    assert "系统会拒绝所有越界新增行" in prompt
    assert len(prompt) < 30_000


def test_quality_repair_prompt_stays_small_with_production_sized_source_pack():
    failed_ids = {"SRC-03", "SRC-05", "SRC-06", "SRC-09", "SRC-12"}
    previous = json.dumps(
        [
            {
                "case_id": f"BBC-{index:02d}",
                "scenario_name": f"failed scenario {index}",
                "expected_result": "old contradicted conclusion " + ("x" * 900),
                "technical_claims": [
                    {
                        "claim_id": f"TC-BBC-{index:02d}",
                        "evidence": [{"evidence_id": f"SRC-{index:02d}"}],
                    }
                ],
            }
            for index in (3, 5, 6, 9, 12)
        ],
        ensure_ascii=False,
        indent=2,
    )
    source_pack = {
        "repo_revision": "abc123",
        "evidence_cards": [
            {
                "evidence_id": f"SRC-{index:02d}",
                "file_path": f"lib/iscsi/source_{index:02d}.c",
                "start_line": index * 10,
                "end_line": index * 10 + 40,
                "excerpt": (
                    f"RELEVANT_SOURCE_{index:02d}_" if f"SRC-{index:02d}" in failed_ids
                    else f"UNRELATED_SOURCE_{index:02d}_"
                )
                + ("source " * 900),
                "symbols": [f"symbol_{index:02d}"],
                "sha256": f"{index:064x}",
            }
            for index in range(1, 45)
        ],
        "verified_literals": [
            {
                "name": f"LITERAL_{index:02d}",
                "value": str(index),
                "evidence_id": f"SRC-{index:02d}",
            }
            for index in range(1, 45)
        ],
    }
    feedback = {
        "affected_artifacts": ["black_box_cases.json"],
        "issues": [
            {
                "artifact": "black_box_cases.json",
                "code": "behavior_claim_contradicted",
                "row_id": f"BBC-{index:02d}",
                "claim_id": f"ROW:black_box_cases.json:BBC-{index:02d}",
                "message": f"AUDIT_TRUTH_{index:02d}: remove the contradicted behavior",
                "evidence": [{"evidence_id": f"SRC-{index:02d}"}],
            }
            for index in (3, 5, 6, 9, 12)
        ],
    }
    plan = {
        "original_user_request": "完整分析 iSCSI login",
        "quality_retry_feedback": feedback,
        "source_bound_domain_fact_candidates": [
            {
                "id": f"fact_{index:02d}",
                "assertion": "domain fact " + ("detail " * 180),
                "evidence": [f"lib/iscsi/source_{index:02d}.c::symbol_{index:02d}"],
            }
            for index in range(1, 45)
        ],
    }

    prompt = _regular_stage_prompt(
        plan=plan,
        stage={
            "id": "black_box_cases",
            "artifact": "black_box_cases.json",
            "purpose": "black-box cases",
            "depends_on": [],
            "output_contract": {
                "schema": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": [
                            "case_id",
                            "scenario_name",
                            "steps",
                            "expected_result",
                        ],
                    },
                }
            },
        },
        source_pack=source_pack,
        flow_pack={},
        outline={},
        completed={},
        current_artifact_seed=previous,
    )

    assert len(prompt) < 35_000
    assert "RELEVANT_SOURCE_03" in prompt
    assert "RELEVANT_SOURCE_12" in prompt
    assert "UNRELATED_SOURCE_01" not in prompt
    assert "fact_01" not in prompt
    assert prompt.index("AUDIT_TRUTH_03") < prompt.index("CURRENT_ARTIFACT_TO_REPAIR")


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


def test_quality_repair_row_only_patch_rejects_unrequested_new_rows():
    previous = [
        {"sfmea_id": "SFMEA-01", "failure_mode": "old one"},
        {"sfmea_id": "SFMEA-02", "failure_mode": "old two"},
    ]
    patch = [
        {"sfmea_id": "SFMEA-02", "failure_mode": "corrected two"},
        {"sfmea_id": "SFMEA-13", "failure_mode": "unrequested new row"},
    ]

    merged = _merge_json_array_patch(
        previous,
        patch,
        allowed_existing_row_ids={"SFMEA-02"},
        allow_new_items=False,
    )

    assert merged == [
        {"sfmea_id": "SFMEA-01", "failure_mode": "old one"},
        {"sfmea_id": "SFMEA-02", "failure_mode": "corrected two"},
    ]


def test_quality_repair_patch_preserves_black_box_dimension_contract():
    previous = [
        {
            "case_id": "BB-12",
            "test_dimension": "upstream_error_propagation",
            "scenario_name": "upstream error reaches the CLI",
        }
    ]
    model_patch = [
        {
            "case_id": "BB-12",
            "test_dimension": "discovery_log_error_handling",
            "scenario_name": "corrected externally observable error flow",
        }
    ]

    merged = _merge_json_array_patch(
        previous,
        model_patch,
        allowed_existing_row_ids={"BB-12"},
        allow_new_items=False,
        immutable_fields={"test_dimension"},
    )

    assert merged == [
        {
            "case_id": "BB-12",
            "test_dimension": "upstream_error_propagation",
            "scenario_name": "corrected externally observable error flow",
        }
    ]


def test_black_box_oracle_contract_adds_traceable_basis_without_inventing_thresholds():
    rows = [
        {
            "case_id": "BB-03",
            "test_dimension": "resource_pressure",
            "oracle_basis": "observe partial failures",
        },
        {
            "case_id": "BB-08",
            "test_dimension": "performance",
            "oracle_basis": "same-environment baseline",
        },
    ]

    normalized, fields = _normalize_black_box_oracle_contract(rows)

    assert "环境配置" in normalized[0]["oracle_basis"]
    assert "不得预设固定数值" in normalized[0]["oracle_basis"]
    assert "预热" in normalized[1]["oracle_basis"]
    assert "至少 30 次" in normalized[1]["oracle_basis"]
    assert "P50/P95" in normalized[1]["oracle_basis"]
    assert "方差" in normalized[1]["oracle_basis"]
    assert fields == ["$[0].oracle_basis", "$[1].oracle_basis"]


def test_black_box_oracle_contract_removes_unregistered_fixed_thresholds():
    rows = [
        {
            "case_id": "BB-08",
            "test_dimension": "performance",
            "oracle_basis": "Threshold is 50% for P50 and 100% for P95",
        },
        {
            "case_id": "BB-09",
            "test_dimension": "long_steady_state",
            "oracle_basis": "24-hour steady state with stable RSS",
        },
        {
            "case_id": "BB-10",
            "test_dimension": "resource_wraparound",
            "oracle_basis": "scanf overflow wraparound is implementation-defined",
        },
    ]

    normalized, fields = _normalize_black_box_oracle_contract(rows)

    assert "50%" not in normalized[0]["oracle_basis"]
    assert "100%" not in normalized[0]["oracle_basis"]
    assert "24-hour" not in normalized[1]["oracle_basis"]
    assert "implementation-defined" not in normalized[2]["oracle_basis"]
    assert "已登记 SLO" in normalized[0]["oracle_basis"]
    assert "运行前登记" in normalized[1]["oracle_basis"]
    assert "环境能力阻塞" in normalized[2]["oracle_basis"]
    assert fields == [
        "$[0].oracle_basis",
        "$[1].oracle_basis",
        "$[2].oracle_basis",
    ]


def test_black_box_oracle_contract_replaces_unregistered_absolute_latency_expectation():
    rows = [
        {
            "case_id": "BB-08",
            "test_dimension": "performance",
            "expected_result": "P50 延迟 < 10ms，P95 延迟 < 20ms（待环境确认）",
            "oracle_basis": "待同环境基线建立后确定阈值。",
        }
    ]

    normalized, fields = _normalize_black_box_oracle_contract(rows)

    assert "< 10ms" not in normalized[0]["expected_result"]
    assert "不预设绝对通过阈值" in normalized[0]["expected_result"]
    assert "$[0].expected_result" in fields


def test_black_box_dimension_contract_keeps_atomic_duplicate_dimensions():
    rows = [
        {"case_id": "BB-01", "test_dimension": "normal_path"},
        {"case_id": "BB-02", "test_dimension": "invalid_input"},
        {"case_id": "BB-X", "test_dimension": "discovery_log_error_handling"},
        {"case_id": "BB-02B", "test_dimension": "invalid_input"},
    ]
    stage = {
        "output_contract": {
            "required_dimensions": ["normal_path", "invalid_input"],
        }
    }

    normalized, fields = _normalize_black_box_dimension_contract(rows, stage)

    assert [item["case_id"] for item in normalized] == ["BB-01", "BB-02", "BB-02B"]
    assert fields == ["$[2].test_dimension:noncontract_removed"]


def test_black_box_dimension_contract_keeps_gate_required_additional_case():
    rows = [
        {"case_id": "BB-01", "test_dimension": "invalid_input"},
        {"case_id": "BB-CBIT", "test_dimension": "invalid_input"},
    ]
    stage = {
        "output_contract": {"required_dimensions": ["invalid_input"]}
    }

    normalized, fields = _normalize_black_box_dimension_contract(
        rows,
        stage,
        preserve_additional_cases=True,
    )

    assert [item["case_id"] for item in normalized] == ["BB-01", "BB-CBIT"]
    assert fields == []


def test_initial_black_box_generation_keeps_duplicates_for_semantic_repair():
    rows = [
        {"case_id": "BB-01", "test_dimension": "normal_path"},
        {"case_id": "BB-02", "test_dimension": "invalid_input"},
        {"case_id": "BB-03", "test_dimension": "invalid_input"},
    ]
    stage = {
        "output_contract": {
            "required_dimensions": ["normal_path", "invalid_input", "timeout"],
        }
    }

    normalized, fields = _normalize_black_box_dimension_contract(
        rows,
        stage,
        preserve_additional_cases=True,
    )

    assert [item["case_id"] for item in normalized] == ["BB-01", "BB-02", "BB-03"]
    assert fields == []


def test_missing_black_box_dimensions_allows_reassigning_duplicate_case_ids():
    assert _quality_repair_may_reassign_black_box_dimensions(
        {
            "issues": [
                {
                    "code": "missing_black_box_dimensions",
                    "artifact": "black_box_cases.json",
                    "dimensions": ["performance"],
                }
            ]
        }
    )
    assert not _quality_repair_may_reassign_black_box_dimensions(
        {"issues": [{"code": "black_box_boundary_violation"}]}
    )


def test_professional_coverage_feedback_routes_to_additive_black_box_repair():
    feedback = {
        "affected_artifacts": ["black_box_cases.json"],
        "issues": [
            {
                "artifact": "完整分析报告.md",
                "code": "professional_coverage_incomplete",
                "scenarios": ["错误 CHAP_R", "未知 CHAP 用户"],
            }
        ],
    }

    scoped = _quality_feedback_for_artifact(feedback, "black_box_cases.json")

    assert scoped["issues"] == [{
        "artifact": "black_box_cases.json",
        "source_artifact": "完整分析报告.md",
        "code": "professional_coverage_incomplete",
        "scenarios": ["错误 CHAP_R", "未知 CHAP 用户"],
    }]
    assert _quality_repair_may_reassign_black_box_dimensions(scoped)
    assert _quality_repair_allows_new_items(
        artifact="black_box_cases.json", quality_feedback=scoped
    )


def test_quality_repair_materializes_missing_c_bit_fragmentation_case():
    repaired, fields = _deterministic_quality_claim_repair(
        [{"case_id": "BC-01", "risk_ids": ["SFMEA-01"], "technical_claims": [], "source_or_test_evidence": ["lib/iscsi/param.c"]}],
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{"artifact": "black_box_cases.json", "code": "missing_c_bit_fragmentation_case"}]},
    )
    assert len(repaired) == 2
    assert repaired[-1]["case_id"] == "BBC-CBIT-FRAGMENT"
    assert "C=1" in " ".join(repaired[-1]["steps"])
    assert "C=0" in " ".join(repaired[-1]["steps"])
    assert fields == ["$[+].c_bit_fragmentation_case"]


def test_quality_repair_does_not_inherit_unrelated_claims_for_c_bit_case():
    """A generated protocol scenario must never borrow another row's proof."""
    inherited_claim = {
        "claim_id": "TC-CHAP",
        "type": "source_anchor",
        "statement": "iscsi_auth_params(conn);",
        "evidence": [{
            "evidence_id": "SRC-CHAP:L773",
            "path": "lib/iscsi/iscsi.c",
            "lines": "L773",
            "quote": "iscsi_auth_params(conn);",
            "symbol": "iscsi_auth_params",
        }],
    }
    repaired, _ = _deterministic_quality_claim_repair(
        [{
            "case_id": "BB-01",
            "risk_ids": [],
            "source_or_test_evidence": ["SRC-CHAP:L773"],
            "technical_claims": [inherited_claim],
        }],
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "missing_c_bit_fragmentation_case",
        }]},
    )

    cbit_case = repaired[-1]
    assert cbit_case["case_id"] == "BBC-CBIT-FRAGMENT"
    assert cbit_case["technical_claims"] == []
    assert cbit_case["source_or_test_evidence"] == []
    assert "ai_suggested_unverified" in cbit_case["mapped_test_dir"]


def test_quality_repair_binds_c_bit_case_to_its_own_verified_anchor():
    repaired, _ = _deterministic_quality_claim_repair(
        [{"case_id": "BB-01", "risk_ids": [], "technical_claims": []}],
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "missing_c_bit_fragmentation_case",
        }]},
        evidence_cards=[{
            "evidence_id": "SRC-CBIT",
            "file_path": "lib/iscsi/iscsi.c",
            "start_line": 1298,
            "end_line": 1302,
            "excerpt": "rc = iscsi_parse_params(params, pdu->data,\n\t\t\tpdu->data_segment_len, ISCSI_BHS_LOGIN_GET_CBIT(reqh->flags),\n\t\t\t&conn->partial_text_parameter);",
            "symbols": ["iscsi_op_login_store_incoming_params"],
            "sha256": "a" * 64,
        }],
    )

    claim = repaired[-1]["technical_claims"][0]
    assert claim["evidence"][0]["evidence_id"] == "SRC-CBIT:L1299"
    assert "CBIT" in claim["statement"]


def test_quality_repair_maps_generated_c_bit_case_to_matching_sfmea_ledger_item():
    repaired, _ = _deterministic_quality_claim_repair(
        [{"case_id": "BB-01", "risk_ids": [], "technical_claims": []}],
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{"artifact": "black_box_cases.json", "code": "missing_c_bit_fragmentation_case"}]},
        sfmea_risk_ledger=[
            {"sfmea_id": "SFMEA-001", "failure_mode": "Login 超时后连接未关闭"},
            {"sfmea_id": "SFMEA-003", "failure_mode": "C-bit 参数跨 PDU 分片时 partial_text_parameter 未正确续接"},
        ],
    )

    assert repaired[-1]["risk_ids"] == ["SFMEA-003"]


def test_quality_repair_materializes_c_bit_case_when_existing_ledger_has_no_matching_risk():
    """A required protocol case must not disappear behind an unrelated SFMEA ledger."""
    repaired, fields = _deterministic_quality_claim_repair(
        [{"case_id": "BB-01", "risk_ids": ["SFMEA-001"], "technical_claims": []}],
        artifact="black_box_cases.json",
        quality_feedback={
            "issues": [
                {
                    "artifact": "black_box_cases.json",
                    "code": "missing_c_bit_fragmentation_case",
                }
            ]
        },
        sfmea_risk_ledger=[
            {"sfmea_id": "SFMEA-001", "failure_mode": "Login timeout cleanup"},
        ],
    )

    assert repaired[-1]["case_id"] == "BBC-CBIT-FRAGMENT"
    assert repaired[-1]["risk_ids"] == []
    assert fields == ["$[+].c_bit_fragmentation_case"]


def test_quality_repair_combines_oracle_normalization_with_c_bit_materialization():
    repaired, fields = _deterministic_quality_claim_repair(
        [
            {
                "case_id": "BB-09",
                "test_dimension": "long_steady_state",
                "oracle_basis": "待验证假设：运行前登记观测项",
                "risk_ids": ["SFMEA-09"],
                "technical_claims": [],
            }
        ],
        artifact="black_box_cases.json",
        quality_feedback={
            "issues": [
                {
                    "artifact": "black_box_cases.json",
                    "code": "oracle_basis_not_traceable",
                    "row_id": "BB-09",
                },
                {
                    "artifact": "black_box_cases.json",
                    "code": "missing_c_bit_fragmentation_case",
                },
            ]
        },
    )

    assert "同环境基线" in repaired[0]["oracle_basis"]
    assert repaired[-1]["case_id"] == "BBC-CBIT-FRAGMENT"
    assert "$[0].oracle_basis" in fields
    assert "$[+].c_bit_fragmentation_case" in fields


def test_quality_repair_adds_sampling_plan_when_performance_audit_requires_it():
    repaired, fields = _deterministic_quality_claim_repair(
        [{
            "case_id": "BB-PERF-01",
            "test_dimension": "performance",
            "oracle_basis": "判据来源：同环境基线。",
            "risk_ids": ["SFMEA-PERF-01"],
            "technical_claims": [],
        }],
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "missing_performance_sampling_plan",
            "row_id": "BB-PERF-01",
        }]},
    )

    assert "预热" in repaired[0]["oracle_basis"]
    assert "P50/P95" in repaired[0]["oracle_basis"]
    assert fields == ["$[0].oracle_basis"]


def test_quality_repair_materializes_c_bit_and_mcs_cases_together():
    repaired, fields = _deterministic_quality_claim_repair(
        [{"case_id": "BB-01", "risk_ids": ["SFMEA-01"], "technical_claims": []}],
        artifact="black_box_cases.json",
        quality_feedback={
            "issues": [
                {"artifact": "black_box_cases.json", "code": "missing_c_bit_fragmentation_case"},
                {"artifact": "test_design.md", "code": "missing_max_connections_target_setup"},
            ]
        },
    )

    assert {row["case_id"] for row in repaired} == {
        "BB-01",
        "BBC-CBIT-FRAGMENT",
        "BBC-MCS-CAPACITY",
    }
    assert "$[+].c_bit_fragmentation_case" in fields
    assert "$[+].mcs_target_setup_case" in fields


def test_deterministic_c_bit_repair_does_not_skip_required_risk_mapping():
    repaired, fields = _deterministic_quality_claim_repair(
        [{"case_id": "BB-01", "risk_ids": [], "technical_claims": []}],
        artifact="black_box_cases.json",
        quality_feedback={
            "issues": [
                {
                    "artifact": "black_box_cases.json",
                    "code": "missing_c_bit_fragmentation_case",
                },
                {
                    "artifact": "black_box_cases.json",
                    "code": "risk_case_missing_sfmea_mapping",
                    "row_id": "BB-01",
                },
            ]
        },
    )

    assert repaired == [{"case_id": "BB-01", "risk_ids": [], "technical_claims": []}]
    assert fields == []


def test_high_risk_mapping_repair_can_patch_any_existing_black_box_case():
    row_ids = _quality_repair_row_ids(
        artifact="black_box_cases.json",
        quality_feedback={
            "issues": [
                {
                    "artifact": "black_box_cases.json",
                    "code": "high_risk_sfmea_unmapped",
                    "unmapped_risk_ids": ["SFMEA-04"],
                }
            ]
        },
        base_items=[
            {"case_id": "BB-01", "risk_ids": []},
            {"case_id": "BB-02", "risk_ids": ["SFMEA-02"]},
        ],
    )

    assert row_ids == {"BB-01", "BB-02"}


def test_quality_repair_patch_can_delete_a_disproved_sfmea_row():
    previous = [
        {"sfmea_id": "SFMEA-01", "failure_mode": "verified risk"},
        {"sfmea_id": "SFMEA-02", "failure_mode": "disproved risk"},
    ]
    patch = [{"sfmea_id": "SFMEA-02", "_delete": True}]

    merged = _merge_json_array_patch(
        previous,
        patch,
        allowed_existing_row_ids={"SFMEA-02"},
        allow_new_items=False,
    )

    assert merged == [
        {"sfmea_id": "SFMEA-01", "failure_mode": "verified risk"},
    ]


def test_non_risk_sfmea_feedback_forces_deletion_tombstone():
    previous = [
        {"sfmea_id": "SFMEA-01", "failure_mode": "verified risk"},
        {"sfmea_id": "SFMEA-02", "failure_mode": "disproved risk"},
    ]
    model_patch = [
        {
            "sfmea_id": "SFMEA-02",
            "failure_mode": "safe behavior repackaged as a risk",
        }
    ]
    feedback = {
        "issues": [
            {
                "artifact": "sfmea.json",
                "row_id": "SFMEA-02",
                "code": "non_risk_sfmea_row",
            }
        ]
    }

    patch = _apply_sfmea_nonrisk_deletion_tombstones(
        model_patch,
        quality_feedback=feedback,
        base_items=previous,
    )

    assert patch == [{"sfmea_id": "SFMEA-02", "_delete": True}]


def test_sfmea_delete_instruction_field_patch_forces_deletion_tombstone():
    previous = [
        {"sfmea_id": "SFMEA-03", "failure_mode": "unsupported resource leak"},
    ]
    feedback = {
        "issues": [
            {
                "artifact": "sfmea.json",
                "row_id": "SFMEA-03",
                "code": "behavior_claim_contradicted",
                "field_patch": {
                    "failure_mode": (
                        "删除该 SFMEA 行：当前源码显示该分支属于正常保护行为，"
                        "不应作为失效模式。"
                    )
                },
            }
        ]
    }

    patch = _apply_sfmea_nonrisk_deletion_tombstones(
        [
            {
                "sfmea_id": "SFMEA-03",
                "failure_mode": "删除该 SFMEA 行：该路径属于正常保护行为。",
            }
        ],
        quality_feedback=feedback,
        base_items=previous,
    )

    assert patch == [{"sfmea_id": "SFMEA-03", "_delete": True}]


def test_sfmea_insufficient_claim_preserves_risk_unless_feedback_explicitly_marks_nonrisk():
    previous = [
        {"sfmea_id": "SFMEA-03", "failure_mode": "safe path presented as risk"},
        {"sfmea_id": "SFMEA-04", "failure_mode": "unchecked sscanf result"},
    ]
    feedback = {
        "issues": [
            {
                "artifact": "sfmea.json",
                "row_id": "SFMEA-03",
                "code": "behavior_claim_insufficient",
                "field_patch": {
                    "failure_mode": "删除该 SFMEA 行：当前源码显示这是正常保护路径。",
                },
            },
            {
                "artifact": "sfmea.json",
                "row_id": "SFMEA-04",
                "code": "behavior_claim_insufficient",
                "field_patch": {},
            },
            {
                "artifact": "sfmea.json",
                "row_id": "SFMEA-04",
                "code": "source_claim_insufficient",
            },
        ]
    }

    patch = _apply_sfmea_nonrisk_deletion_tombstones(
        [
            {"sfmea_id": "SFMEA-03", "failure_mode": "safe path presented as risk"},
            {"sfmea_id": "SFMEA-04", "failure_mode": "unchecked sscanf result"},
        ],
        quality_feedback=feedback,
        base_items=previous,
    )

    assert patch == [
        {"sfmea_id": "SFMEA-04", "failure_mode": "unchecked sscanf result"},
        {"sfmea_id": "SFMEA-03", "_delete": True},
    ]


def test_contradicted_sfmea_claim_forces_deletion_instead_of_rewording():
    previous = [
        {"sfmea_id": "SFMEA-01", "failure_mode": "verified risk"},
        {"sfmea_id": "SFMEA-02", "failure_mode": "unsupported risk"},
    ]
    feedback = {
        "issues": [
            {
                "artifact": "sfmea.json",
                "claim_id": "SFMEA-02:behavior:1",
                "code": "behavior_claim_contradicted",
            }
        ]
    }

    patch = _apply_sfmea_nonrisk_deletion_tombstones(
        [{"sfmea_id": "SFMEA-02", "failure_mode": "reworded guess"}],
        quality_feedback=feedback,
        base_items=previous,
    )

    assert patch == [{"sfmea_id": "SFMEA-02", "_delete": True}]


def test_contradicted_sfmea_claim_keeps_independently_corrected_real_risk():
    previous = [{"sfmea_id": "SFMEA-07", "failure_mode": "overstated risk"}]
    corrected = {
        "sfmea_id": "SFMEA-07",
        "failure_mode": "reconnect_delay 缺少用户态范围校验",
    }
    feedback = {
        "issues": [
            {
                "artifact": "sfmea.json",
                "row_id": "SFMEA-07",
                "code": "behavior_claim_contradicted",
                "field_patch": {
                    "failure_mode": "reconnect_delay 缺少用户态范围校验",
                    "effect": "越界值会继续传递给内核，最终行为需要黑盒验证。",
                    "mitigation": "增加边界校验，并执行越界输入回归测试。",
                },
            }
        ]
    }

    patch = _apply_sfmea_nonrisk_deletion_tombstones(
        [corrected],
        quality_feedback=feedback,
        base_items=previous,
    )

    assert patch == [corrected]


def test_contradicted_sfmea_claim_detection_only_patch_still_deletes_row():
    previous = [{"sfmea_id": "SFMEA-08", "failure_mode": "unsupported risk"}]
    feedback = {
        "issues": [
            {
                "artifact": "sfmea.json",
                "row_id": "SFMEA-08",
                "code": "behavior_claim_contradicted",
                "field_patch": {
                    "detection": "观察控制器状态和错误日志。",
                },
            }
        ]
    }

    patch = _apply_sfmea_nonrisk_deletion_tombstones(
        [{"sfmea_id": "SFMEA-08", "failure_mode": "reworded guess"}],
        quality_feedback=feedback,
        base_items=previous,
    )

    assert patch == [{"sfmea_id": "SFMEA-08", "_delete": True}]


def test_source_function_detection_does_not_treat_else_if_as_function():
    source = (
        "int handle_state(int state) {\n"
        "    if (state == 1) {\n"
        "        return 1;\n"
        "    } else if (state == 2) {\n"
        "        return 2;\n"
        "    }\n"
        "    return 0;\n"
        "}\n"
    )

    assert _source_enclosing_c_function(source, anchor_line=5) == "handle_state"


def test_contradicted_sfmea_claim_deletes_explicit_safe_path_correction():
    previous = [{"sfmea_id": "SFMEA-02", "failure_mode": "use after free"}]
    feedback = {
        "issues": [
            {
                "artifact": "sfmea.json",
                "row_id": "SFMEA-02",
                "code": "behavior_claim_contradicted",
                "field_patch": {
                    "failure_mode": "当前源码未表现出悬空指针使用",
                    "effect": "未从给定源码发现 use-after-free。",
                    "mitigation": "无需添加置空语句；该语句已存在。",
                },
            }
        ]
    }

    patch = _apply_sfmea_nonrisk_deletion_tombstones(
        [{"sfmea_id": "SFMEA-02", "failure_mode": "safe path reworded"}],
        quality_feedback=feedback,
        base_items=previous,
    )

    assert patch == [{"sfmea_id": "SFMEA-02", "_delete": True}]


def test_row_source_claim_contradiction_uses_the_same_sfmea_tombstone_path():
    previous = [{"sfmea_id": "SFMEA-03", "failure_mode": "invented leak"}]
    feedback = {
        "issues": [{
            "artifact": "sfmea.json",
            "row_id": "SFMEA-03",
            "code": "row_source_claim_contradicted",
        }]
    }

    patch = _apply_sfmea_nonrisk_deletion_tombstones(
        [{"sfmea_id": "SFMEA-03", "failure_mode": "still invented"}],
        quality_feedback=feedback,
        base_items=previous,
    )

    assert patch == [{"sfmea_id": "SFMEA-03", "_delete": True}]


def test_quality_repair_row_ids_extracts_row_from_derived_claim_id():
    feedback = {
        "issues": [
            {
                "artifact": "sfmea.json",
                "code": "source_claim_contradicted",
                "claim_id": "SFMEA-09:log_literal:1",
            },
            {
                "artifact": "sfmea.json",
                "code": "behavior_claim_insufficient",
                "claim_id": "ROW:sfmea.json:SFMEA-12",
            },
        ]
    }

    assert _quality_repair_row_ids(
        artifact="sfmea.json",
        quality_feedback=feedback,
    ) == {"SFMEA-09", "SFMEA-12"}


def test_black_box_behavior_claim_is_preserved_for_independent_review():
    rows = [
        {
            "case_id": "BB-009",
            "technical_claims": [
                {
                    "claim_id": "TC-BB-009",
                    "type": "behavior",
                    "statement": "lookup latency depends on the network",
                    "evidence": [
                        {
                            "evidence_id": "SRC-06:L186",
                            "path": "libnvme/test/tree-fabrics.c",
                            "lines": "L186",
                            "quote": "c = libnvme_lookup_ctrl(s, &f.ctrl_params, NULL);",
                        }
                    ],
                }
            ],
        }
    ]

    normalized = _normalize_black_box_source_anchor_claims(rows)

    claim = normalized[0]["technical_claims"][0]
    assert claim["type"] == "behavior"
    assert claim["statement"] == "lookup latency depends on the network"


def test_sfmea_literal_source_claim_is_normalized_to_l1_source_anchor():
    rows = [{
        "sfmea_id": "SFMEA-12",
        "technical_claims": [{
            "claim_id": "TC-12",
            "type": "source",
            "statement": "spdk_sock_close(&conn->sock);",
            "evidence": [{
                "evidence_id": "SRC-06:L631",
                "path": "lib/iscsi/conn.c",
                "lines": "L631",
                "quote": "spdk_sock_close(&conn->sock);",
            }],
        }],
    }]

    normalized = _normalize_sfmea_source_anchor_claims(rows)

    assert normalized[0]["technical_claims"][0]["type"] == "source_anchor"


def test_sfmea_interpreted_source_claim_is_preserved_as_behavior_assertion():
    rows = [{
        "technical_claims": [{
            "type": "source",
            "statement": "socket close proves all transfer tasks are safe",
            "evidence": [{"quote": "spdk_sock_close(&conn->sock);"}],
        }],
    }]

    normalized = _normalize_sfmea_source_anchor_claims(rows)

    claim = normalized[0]["technical_claims"][0]
    assert claim["type"] == "behavior_assertion"
    assert claim["statement"] == "socket close proves all transfer tasks are safe"


def test_sfmea_row_behavior_assertion_is_materialized_as_a_bound_l2_claim():
    from app.services.ai_staged_execution import (
        _materialize_sfmea_row_behavior_assertions,
    )

    rows = [{
        "sfmea_id": "SFMEA-13",
        "behavior_assertion": {
            "assertion": "未协商到支持的 CHAP 算法时，目标记录错误并进入错误返回路径。",
            "evidence_id": "SRC-01:L831",
            "path": "lib/iscsi/iscsi.c",
            "lines": "L831",
            "quote": "if (new_val == NULL) {",
        },
        "technical_claims": [{
            "claim_id": "TC-13",
            "type": "source_anchor",
            "statement": "if (new_val == NULL) {",
            "evidence": [{
                "evidence_id": "SRC-01:L831",
                "path": "lib/iscsi/iscsi.c",
                "lines": "L831",
                "quote": "if (new_val == NULL) {",
            }],
        }],
    }]

    materialized = _materialize_sfmea_row_behavior_assertions(rows)

    claim = materialized[0]["technical_claims"][1]
    assert claim["type"] == "behavior_assertion"
    assert claim["statement"] == "未协商到支持的 CHAP 算法时，目标记录错误并进入错误返回路径。"
    assert claim["evidence"][0]["evidence_id"] == "SRC-01:L831"


def test_final_materialized_sfmea_contract_rewrites_cleanup_order_to_hypothesis(tmp_path):
    from app.services.ai_staged_execution import normalize_materialized_sfmea_risk_contract

    (tmp_path / "evidence_cards.json").write_text(json.dumps([{
        "evidence_id": "SRC-06:L631",
        "file_path": "lib/iscsi/conn.c",
        "start_line": 631,
        "end_line": 631,
        "excerpt": "spdk_sock_close(&conn->sock);",
        "symbols": ["_iscsi_conn_destruct"],
        "sha256": "digest",
        "classification": "source",
    }]), encoding="utf-8")
    (tmp_path / "source_scope.json").write_text("{}", encoding="utf-8")
    (tmp_path / "sfmea.json").write_text(json.dumps([{
        "sfmea_id": "SFMEA-12",
        "failure_mode": "连接析构中 socket 关闭后仍访问",
        "cause": "风险假设：若 socket 关闭后仍有数据传输任务，可能导致访问已关闭的 socket",
        "mechanism": "风险假设：若先调用 spdk_sock_close 再调用清理任务，后者可能访问 socket",
        "technical_claims": [{
            "claim_id": "TC-12",
            "type": "source",
            "statement": "spdk_sock_close(&conn->sock);",
            "evidence": [{"evidence_id": "SRC-06:L631", "path": "lib/iscsi/conn.c", "quote": "spdk_sock_close(&conn->sock);"}],
        }],
    }]), encoding="utf-8")

    fields = normalize_materialized_sfmea_risk_contract(
        artifact_dir=tmp_path,
        plan={"original_user_request": "iSCSI login cleanup"},
    )

    row = json.loads((tmp_path / "sfmea.json").read_text())[0]
    assert any("source_risk_candidate" in field for field in fields)
    assert row["risk_status"] == "test_hypothesis"
    assert "故障注入" in row["cause"]
    assert row["technical_claims"][0]["type"] == "source_anchor"


def test_final_materialized_sfmea_contract_canonicalizes_claim_to_bound_quote(tmp_path):
    from app.services.ai_staged_execution import normalize_materialized_sfmea_risk_contract

    (tmp_path / "evidence_cards.json").write_text(json.dumps([{
        "evidence_id": "SRC-03",
        "file_path": "lib/iscsi/iscsi.c",
        "start_line": 10,
        "end_line": 14,
        "excerpt": "if (conn == NULL) {\n\treturn -1;\n}",
        "symbols": ["iscsi_auth_params"],
        "sha256": "digest",
        "classification": "source",
    }]), encoding="utf-8")
    (tmp_path / "source_scope.json").write_text("{}", encoding="utf-8")
    (tmp_path / "sfmea.json").write_text(json.dumps([{
        "sfmea_id": "SFMEA-03",
        "risk_status": "test_hypothesis",
        "technical_claims": [{
            "claim_id": "TC-03",
            "type": "current_behavior",
            "statement": "iscsi_auth_params 对空指针返回 -1。",
            "evidence": [{
                "evidence_id": "SRC-03:L10",
                "path": "lib/iscsi/iscsi.c",
                "lines": "L10",
                "quote": "if (conn == NULL) {",
            }],
        }],
    }]), encoding="utf-8")

    normalize_materialized_sfmea_risk_contract(
        artifact_dir=tmp_path,
        plan={"original_user_request": "iSCSI login"},
    )

    claim = json.loads((tmp_path / "sfmea.json").read_text())[0]["technical_claims"][0]
    assert claim["type"] == "behavior_assertion"
    assert claim["statement"] == "iscsi_auth_params 对空指针返回 -1。"


def test_final_materialized_sfmea_contract_binds_declared_source_evidence_without_model_claim(tmp_path):
    from app.services.ai_staged_execution import normalize_materialized_sfmea_risk_contract

    (tmp_path / "evidence_cards.json").write_text(json.dumps([{
        "evidence_id": "SRC-07",
        "file_path": "lib/iscsi/iscsi.c",
        "start_line": 1288,
        "end_line": 1304,
        "excerpt": "if (rc < 0) {\n\tiscsi_param_free(*params);\n}",
        "symbols": ["iscsi_op_login_store_incoming_params"],
        "sha256": "digest",
        "classification": "source",
    }]), encoding="utf-8")
    (tmp_path / "source_scope.json").write_text("{}", encoding="utf-8")
    (tmp_path / "stages" / "source_analysis").mkdir(parents=True)
    (tmp_path / "stages" / "source_analysis" / "source_evidence_pack.json").write_text(
        json.dumps({"evidence_cards": [{
            "evidence_id": "SRC-EARLY",
            "file_path": "lib/iscsi/tgt_node.c",
            "start_line": 10,
            "end_line": 10,
            "excerpt": "return 0;",
            "symbols": [],
            "sha256": "other-digest",
            "classification": "source",
        }]}),
        encoding="utf-8",
    )
    (tmp_path / "sfmea.json").write_text(json.dumps([{
        "sfmea_id": "SFMEA-07",
        "risk_status": "test_hypothesis",
        "source_evidence": ["lib/iscsi/iscsi.c:1288-1304"],
    }]), encoding="utf-8")

    normalize_materialized_sfmea_risk_contract(
        artifact_dir=tmp_path,
        plan={"original_user_request": "iSCSI login"},
    )

    claim = json.loads((tmp_path / "sfmea.json").read_text())[0]["technical_claims"][0]
    assert claim["type"] == "source_anchor"
    assert claim["statement"] == "if (rc < 0) {"
    assert claim["evidence"][0]["evidence_id"] == "SRC-07:L1288"


def test_final_materialized_sfmea_contract_binds_unique_declared_source_symbol_without_model_claim(tmp_path):
    from app.services.ai_staged_execution import normalize_materialized_sfmea_risk_contract

    (tmp_path / "evidence_cards.json").write_text(json.dumps([{
        "evidence_id": "SRC-08",
        "file_path": "lib/iscsi/iscsi.c",
        "start_line": 2238,
        "end_line": 2238,
        "excerpt": "return iscsi_op_login_session_normal(conn, rsp_pdu, params);",
        "symbols": ["iscsi_op_login_session_normal"],
        "sha256": "digest",
        "classification": "source",
    }]), encoding="utf-8")
    (tmp_path / "source_scope.json").write_text("{}", encoding="utf-8")
    (tmp_path / "sfmea.json").write_text(json.dumps([{
        "sfmea_id": "SFMEA-08",
        "risk_status": "test_hypothesis",
        "source_evidence": ["lib/iscsi/iscsi.c:iscsi_op_login_session_normal"],
    }]), encoding="utf-8")

    normalize_materialized_sfmea_risk_contract(
        artifact_dir=tmp_path,
        plan={"original_user_request": "iSCSI login"},
    )

    claim = json.loads((tmp_path / "sfmea.json").read_text())[0]["technical_claims"][0]
    assert claim["type"] == "source_anchor"
    assert claim["statement"] == "return iscsi_op_login_session_normal(conn, rsp_pdu, params);"
    assert claim["evidence"][0]["evidence_id"] == "SRC-08:L2238"


def test_final_materialized_sfmea_contract_uses_declared_range_end_to_disambiguate_flow_cards(tmp_path):
    from app.services.ai_staged_execution import normalize_materialized_sfmea_risk_contract

    (tmp_path / "evidence_cards.json").write_text(json.dumps([
        {"evidence_id": "FLOW-EDGE-009", "file_path": "lib/iscsi/iscsi.c", "start_line": 1864, "end_line": 1864, "excerpt": "rc = iscsi_op_login_initialize_port(conn);", "symbols": [], "sha256": "a", "classification": "source"},
        {"evidence_id": "FLOW-EDGE-010", "file_path": "lib/iscsi/iscsi.c", "start_line": 1870, "end_line": 1870, "excerpt": "rc = iscsi_op_login_session_type(conn, rsp_pdu, &session_type, params);", "symbols": [], "sha256": "b", "classification": "source"},
    ]), encoding="utf-8")
    (tmp_path / "source_scope.json").write_text("{}", encoding="utf-8")
    (tmp_path / "sfmea.json").write_text(json.dumps([{
        "sfmea_id": "SFMEA-11", "risk_status": "test_hypothesis",
        "source_evidence": ["lib/iscsi/iscsi.c:1864-1870"],
    }]), encoding="utf-8")

    normalize_materialized_sfmea_risk_contract(artifact_dir=tmp_path, plan={})

    claim = json.loads((tmp_path / "sfmea.json").read_text())[0]["technical_claims"][0]
    assert claim["evidence"][0]["evidence_id"] == "FLOW-EDGE-010:L1870"


def test_final_materialized_sfmea_contract_removes_deletion_tombstones(tmp_path):
    from app.services.ai_staged_execution import normalize_materialized_sfmea_risk_contract

    (tmp_path / "evidence_cards.json").write_text("[]", encoding="utf-8")
    (tmp_path / "source_scope.json").write_text("{}", encoding="utf-8")
    (tmp_path / "sfmea.json").write_text(json.dumps([
        {"sfmea_id": "SFMEA-01", "failure_mode": "retained risk"},
        {"sfmea_id": "SFMEA-02", "_delete": True},
    ]), encoding="utf-8")

    normalize_materialized_sfmea_risk_contract(
        artifact_dir=tmp_path,
        plan={"original_user_request": "iSCSI login"},
    )

    rows = json.loads((tmp_path / "sfmea.json").read_text(encoding="utf-8"))
    assert [row["sfmea_id"] for row in rows] == ["SFMEA-01"]


def test_final_materialized_sfmea_contract_refills_declared_floor_after_tombstone(tmp_path):
    from app.services.ai_staged_execution import normalize_materialized_sfmea_risk_contract

    (tmp_path / "evidence_cards.json").write_text(json.dumps([{
        "evidence_id": "SRC-01",
        "file_path": "lib/iscsi/iscsi.c",
        "start_line": 10,
        "end_line": 10,
        "excerpt": "if (conn == NULL) {",
        "symbols": ["iscsi_login"],
        "sha256": "digest",
        "classification": "source",
    }]), encoding="utf-8")
    (tmp_path / "source_scope.json").write_text("{}", encoding="utf-8")
    (tmp_path / "sfmea.json").write_text(json.dumps([
        {"sfmea_id": "SFMEA-01", "failure_mode": "retained risk"},
        {"sfmea_id": "SFMEA-02", "_delete": True},
    ]), encoding="utf-8")

    normalize_materialized_sfmea_risk_contract(
        artifact_dir=tmp_path,
        plan={"stages": [
            {
                "id": "sfmea",
                "artifact": "sfmea.json",
                "output_contract": {"schema": {"minItems": 1}},
            },
            {
                "id": "report",
                "artifact": "report.md",
                "output_contract": {"min_sfmea_rows": 2},
            },
        ]},
    )

    rows = json.loads((tmp_path / "sfmea.json").read_text(encoding="utf-8"))
    assert len(rows) == 2
    assert all(row.get("_delete") is not True for row in rows)
    assert rows[-1]["risk_status"] == "test_hypothesis"
    assert rows[-1]["technical_claims"][0]["evidence"][0]["evidence_id"] == "SRC-01:L10"
    assert rows[-1]["failure_mode"] in rows[-1]["mitigation"]


def test_final_materialized_sfmea_contract_removes_exact_delivery_duplicate_before_floor(tmp_path):
    from app.services.ai_staged_execution import normalize_materialized_sfmea_risk_contract

    (tmp_path / "evidence_cards.json").write_text(json.dumps([
        {
            "evidence_id": "SRC-01",
            "file_path": "lib/iscsi/iscsi.c",
            "start_line": 10,
            "end_line": 10,
            "excerpt": "if (conn == NULL) {",
            "symbols": ["iscsi_login"],
            "sha256": "digest-1",
            "classification": "source",
        },
        {
            "evidence_id": "SRC-02",
            "file_path": "lib/iscsi/conn.c",
            "start_line": 20,
            "end_line": 20,
            "excerpt": "spdk_sock_close(&conn->sock);",
            "symbols": ["iscsi_conn_close"],
            "sha256": "digest-2",
            "classification": "source",
        },
    ]), encoding="utf-8")
    (tmp_path / "source_scope.json").write_text("{}", encoding="utf-8")
    duplicate = {
        "failure_mode": "登录输入校验失败导致会话不可用",
        "cause": "故障注入假设：输入不合法",
        "effect": "发起端无法建立会话",
        "detection": "观察协议响应和连接状态",
        "mitigation": "整改: 固化输入校验。验证: 注入非法输入并观察协议响应。",
        "severity": 6,
        "occurrence": 2,
        "detection_score": 7,
        "rpn": 84,
        "risk_status": "test_hypothesis",
        "technical_claims": [{"type": "source_anchor", "statement": "if (conn == NULL) {", "evidence": [{
            "evidence_id": "SRC-01:L10", "path": "lib/iscsi/iscsi.c", "lines": "L10", "quote": "if (conn == NULL) {",
        }]}],
        "source_evidence": ["SRC-01:L10"],
    }
    rows = [{**duplicate, "sfmea_id": "SFMEA-01"}, {**duplicate, "sfmea_id": "SFMEA-02"}]
    (tmp_path / "sfmea.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    fields = normalize_materialized_sfmea_risk_contract(
        artifact_dir=tmp_path,
        plan={"stages": [{"id": "sfmea", "artifact": "sfmea.json", "output_contract": {"schema": {"minItems": 2}}}]},
    )

    normalized = json.loads((tmp_path / "sfmea.json").read_text(encoding="utf-8"))
    assert any("SFMEA-02:duplicate_removed" == field for field in fields)
    assert len(normalized) == 2
    assert normalized[0]["failure_mode"] == duplicate["failure_mode"]
    assert normalized[1]["failure_mode"] != duplicate["failure_mode"]


def test_final_materialized_sfmea_contract_uses_agent_staged_plan_from_task_root(tmp_path):
    from app.services.ai_staged_execution import normalize_materialized_sfmea_risk_contract

    agent_dir = tmp_path / "agent_runs" / "analyze"
    agent_dir.mkdir(parents=True)
    (agent_dir / "evidence_cards.json").write_text(json.dumps([{
        "evidence_id": "SRC-01",
        "file_path": "lib/iscsi/iscsi.c",
        "start_line": 10,
        "end_line": 10,
        "excerpt": "if (conn == NULL) {",
        "symbols": ["iscsi_login"],
        "sha256": "digest",
        "classification": "source",
    }]), encoding="utf-8")
    (agent_dir / "source_scope.json").write_text("{}", encoding="utf-8")
    (agent_dir / "sfmea.json").write_text(json.dumps([
        {"sfmea_id": "SFMEA-01", "failure_mode": "retained risk"},
        {"sfmea_id": "SFMEA-02", "_delete": True},
    ]), encoding="utf-8")
    (agent_dir / "staged_execution_plan.json").write_text(json.dumps({"stages": [{
        "id": "sfmea",
        "artifact": "sfmea.json",
        "output_contract": {"schema": {"minItems": 2}},
    }]}), encoding="utf-8")

    normalize_materialized_sfmea_risk_contract(artifact_dir=tmp_path, plan={})

    rows = json.loads((agent_dir / "sfmea.json").read_text(encoding="utf-8"))
    assert len(rows) == 2
    assert all(row.get("_delete") is not True for row in rows)


def test_normalize_black_box_delivery_contract_replaces_source_mapping_and_unit_fallback():
    rendered, fields = _normalize_black_box_delivery_contract(
        [
            {
                "case_id": "BB-10",
                "scenario_name": "discovery boundary",
                "steps": [
                    "若无法通过外部接口注入边界值，将该场景改为单元测试候选"
                ],
                "mapped_test_dir": "libnvme/src/nvme/",
            }
        ]
    )

    assert rendered[0]["mapped_test_dir"].startswith("ai_suggested_unverified:")
    assert "单元测试" not in rendered[0]["steps"][0]
    assert "环境能力阻塞" in rendered[0]["steps"][0]
    assert fields == ["$[0].mapped_test_dir", "$[0].steps[0]"]


def test_normalize_black_box_delivery_contract_removes_private_state_from_observation():
    rendered, fields = _normalize_black_box_delivery_contract(
        [
            {
                "case_id": "BB-11",
                "scenario_name": "login state",
                "steps": ["使用 initiator 发起登录"],
                "observability": ["通过 RPC 检查 conn->state=FULL_FEATURE"],
                "failure_diagnostics": ["记录 conn->state 的变化"],
                "mapped_test_dir": "test/iscsi_tgt",
            }
        ]
    )

    assert rendered[0]["observability"] == [
        "通过公开 CLI/RPC、目标日志、协议响应或 TCP 会话状态观察结果；不依赖内部结构字段。"
    ]
    assert rendered[0]["failure_diagnostics"] == [
        "通过公开 CLI/RPC、目标日志、协议响应或 TCP 会话状态观察结果；不依赖内部结构字段。"
    ]
    assert fields == ["$[0].observability[0]", "$[0].failure_diagnostics[0]"]


def test_normalize_sfmea_risk_contract_marks_unsupported_defect_language_as_hypothesis():
    rendered, fields = _normalize_sfmea_risk_contract(
        [
            {
                "sfmea_id": "SFMEA-01",
                "failure_mode": "连接槽位未正确清理",
                "mechanism": "登录路径维护会话连接计数",
                "cause": "错误路径未递减连接计数",
                "source_evidence": ["lib/iscsi/iscsi.c:721-722"],
                "technical_claims": [
                    {
                        "statement": "sess->connections++;",
                        "evidence": [{"path": "lib/iscsi/iscsi.c", "quote": "sess->connections++;"}],
                    }
                ],
            }
        ]
    )

    row = rendered[0]
    assert row["risk_status"] == "test_hypothesis"
    assert row["mechanism"].startswith("风险假设：若")
    assert row["cause"].startswith("故障注入假设：若")
    assert "故障注入风险假设" in row["evidence_interpretation"]
    for expected in (
        "$[0].risk_status",
        "$[0].evidence_interpretation",
        "$[0].mechanism",
        "$[0].cause",
        "$[0].effect:risk_hypothesis_default",
    ):
        assert expected in fields


def test_normalize_sfmea_risk_contract_fills_only_missing_effect_chain_as_hypothesis():
    rendered, fields = _normalize_sfmea_risk_contract(
        [
            {
                "sfmea_id": "SFMEA-STRUCTURE-01",
                "failure_mode": "登录阶段的异常时序导致会话状态不一致",
                "mechanism": "风险假设：异常时序可能使状态迁移交错。",
                "cause": "故障注入假设：若回调与断连交错，则状态迁移可能偏离。",
                "local_effect": "已有局部影响说明。",
            }
        ]
    )

    assert rendered[0]["local_effect"] == "已有局部影响说明。"
    assert rendered[0]["effect"].startswith("风险假设：登录阶段的异常时序")
    assert rendered[0]["downstream_effect"]
    assert rendered[0]["final_effect"]
    assert "$[0].effect:risk_hypothesis_default" in fields
    assert "$[0].local_effect:risk_hypothesis_default" not in fields


def test_normalize_sfmea_risk_contract_marks_unmeasured_hypothesis_rpn_provisional():
    rendered, fields = _normalize_sfmea_risk_contract(
        [
            {
                "sfmea_id": "SFMEA-PROVISIONAL-01",
                "risk_status": "test_hypothesis",
                "occurrence": 3,
                "score_explanation": "Occurrence=3（需特定时序，待采样）。",
            }
        ]
    )

    assert rendered[0]["occurrence_basis"] == "专家工程评审先验；无实测数据，低置信度，待采样校准。"
    assert rendered[0]["rpn_status"] == "provisional"
    assert "专家工程评审先验" in rendered[0]["score_explanation"]
    assert "$[0].occurrence_basis:provisional_expert_prior" in fields


def test_sfmea_contract_fills_declared_minimum_with_distinct_source_hypotheses():
    from app.services.ai_staged_execution import _complete_minimum_sfmea_hypotheses

    rows = [
        {"sfmea_id": f"SFMEA-{index:02d}", "failure_mode": f"已有风险 {index}"}
        for index in range(1, 12)
    ]
    catalog = [
        {"evidence_id": "SRC-1", "path": "lib/iscsi/iscsi.c", "quote": "if (conn->state >= ISCSI_CONN_STATE_EXITING) {"},
        {"evidence_id": "SRC-2", "path": "lib/iscsi/iscsi.c", "quote": "spdk_poller_unregister(&conn->logout_request_timer);"},
    ]

    completed, fields = _complete_minimum_sfmea_hypotheses(
        rows,
        minimum_items=12,
        product_claim_catalog=catalog,
    )

    assert len(completed) == 12
    assert completed[-1]["rpn_status"] == "provisional"
    assert completed[-1]["technical_claims"][0]["evidence"][0]["evidence_id"]
    assert fields == ["$[11]:deterministic_source_risk_floor"]


def test_materialized_sfmea_normalizer_resolves_agent_owned_artifact_from_task_root(tmp_path):
    from app.services.ai_staged_execution import normalize_materialized_sfmea_risk_contract

    agent_dir = tmp_path / "agent_runs" / "analyze"
    agent_dir.mkdir(parents=True)
    (agent_dir / "sfmea.json").write_text(
        json.dumps([
            {
                "sfmea_id": "SFMEA-PROVISIONAL-02",
                "risk_status": "test_hypothesis",
                "occurrence": 2,
                "score_explanation": "Occurrence=2（待采样）。",
            }
        ]),
        encoding="utf-8",
    )

    fields = normalize_materialized_sfmea_risk_contract(
        artifact_dir=tmp_path,
        plan={},
    )

    persisted = json.loads((agent_dir / "sfmea.json").read_text(encoding="utf-8"))
    assert "$[0].occurrence_basis:provisional_expert_prior" in fields
    assert persisted[0]["rpn_status"] == "provisional"


def test_normalize_sfmea_risk_contract_replaces_unbound_source_labels_with_claim_anchors():
    rendered, fields = _normalize_sfmea_risk_contract(
        [{
            "sfmea_id": "SFMEA-ANCHOR-01",
            "failure_mode": "登录异常路径风险",
            "mechanism": "连接状态在异常时序中可能交错",
            "cause": "上游连接关闭与回调完成交错",
            "source_evidence": [
                "lib/iscsi/conn.c:login_timeout",
                "lib/iscsi/iscsi.c:iscsi_conn_login_pdu_success_complete",
            ],
            "technical_claims": [{
                "type": "source_anchor",
                "statement": "conn->state = ISCSI_CONN_STATE_EXITING;",
                "evidence": [{
                    "evidence_id": "SRC-02:L153",
                    "path": "lib/iscsi/conn.c",
                    "lines": "L153",
                    "quote": "conn->state = ISCSI_CONN_STATE_EXITING;",
                }],
            }],
        }]
    )

    assert rendered[0]["source_evidence"] == ["SRC-02:L153"]
    assert "$[0].source_evidence" in fields


def test_normalize_sfmea_replaces_bare_source_path_with_verified_hypothesis():
    rendered, fields = _normalize_sfmea_risk_contract(
        [{
            "sfmea_id": "SFMEA-BARE-PATH-01",
            "failure_mode": "并发登录导致会话状态不一致",
            "mechanism": "风险假设：多个连接同时登录时状态更新未加锁。",
            "cause": "风险假设：并发时序可能绕过预期状态收敛。",
            "source_evidence": ["lib/iscsi/iscsi.c"],
            "technical_claims": [],
        }],
        product_claim_catalog=[{
            "evidence_id": "SRC-22:L1674",
            "path": "lib/iscsi/iscsi.c",
            "symbol": "iscsi_op_login_session_normal",
            "lines": "L1674",
            "quote": "rc = iscsi_op_login_check_session(conn, sess);",
        }],
    )

    row = rendered[0]
    assert "SFMEA-BARE-PATH-01:source_risk_candidate" in fields
    assert row["risk_status"] == "test_hypothesis"
    assert row["technical_claims"][0]["evidence"][0]["evidence_id"] == "SRC-22:L1674"
    assert row["source_evidence"] == ["SRC-22:L1674"]
    assert "已验证源码锚点" in row["evidence_interpretation"]


def test_normalize_sfmea_turns_guarded_full_feature_into_a_timing_hypothesis():
    rendered, fields = _normalize_sfmea_risk_contract(
        [
            {
                "sfmea_id": "SFMEA-09",
                "failure_mode": "登录参数更新时未校验 full_feature 状态",
                "mechanism": "仅在 full_feature 为真时更新参数",
                "cause": "full_feature 为假时遗漏参数更新",
                "technical_claims": [
                    {
                        "statement": "if (conn->full_feature) {",
                        "evidence": [
                            {
                                "path": "lib/iscsi/iscsi.c",
                                "quote": "if (conn->full_feature) {",
                            }
                        ],
                    }
                ],
            }
        ]
    )

    row = rendered[0]
    assert row["risk_status"] == "test_hypothesis"
    assert row["failure_mode"] == "登录错误处理的阶段时序异常可能导致参数状态不一致"
    assert "full_feature 状态切换交错" in row["trigger_condition"]
    assert "guarded_full_feature_hypothesis" in fields[-1]


def test_normalize_sfmea_turns_guarded_connection_state_into_a_concurrency_hypothesis():
    rendered, fields = _normalize_sfmea_risk_contract(
        [
            {
                "sfmea_id": "SFMEA-06",
                "failure_mode": "登录成功回调中状态检查不充分导致重复处理",
                "mechanism": "检查 state >= EXITING，但未检查其他中间状态",
                "cause": "错误路径未检查状态",
                "technical_claims": [
                    {
                        "statement": "if (conn->state >= ISCSI_CONN_STATE_EXITING) {",
                        "evidence": [
                            {
                                "path": "lib/iscsi/iscsi.c",
                                "quote": "if (conn->state >= ISCSI_CONN_STATE_EXITING) {",
                            }
                        ],
                    }
                ],
            }
        ]
    )

    row = rendered[0]
    assert row["failure_mode"] == "登录成功回调与连接退出并发时状态转换竞态导致重复处理"
    assert "并发故障注入" in row["trigger_condition"]
    assert "guarded_connection_state_hypothesis" in fields[-1]


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


def test_quality_repair_claim_catalog_includes_context_for_a_stale_evidence_id():
    evidence_cards = [
        {
            "evidence_id": "SRC-06",
            "file_path": "libnvme/test/ioctl/ana.c",
            "start_line": 255,
            "end_line": 257,
            "excerpt": "static void test_long_log(void)\n{\n}",
            "symbols": ["test_long_log"],
        },
        {
            "evidence_id": "SRC-15",
            "file_path": "libnvme/src/nvme/crypto.c",
            "start_line": 1004,
            "end_line": 1006,
            "excerpt": (
                "__libnvme_public int libnvmf_set_keyring(\n"
                "\t\tstruct libnvme_global_ctx *ctx, long key_id)\n"
                "{"
            ),
            "symbols": ["libnvmf_set_keyring"],
        },
    ]
    previous = json.dumps(
        [
            {
                "case_id": "BB-11",
                "scenario_name": "TLS keyring cleanup",
                "technical_claims": [
                    {
                        "claim_id": "TC-BB-11",
                        "evidence": [{"evidence_id": "SRC-06:L165"}],
                    }
                ],
            }
        ]
    )
    feedback = {
        "affected_artifacts": ["black_box_cases.json"],
        "issues": [
            {
                "artifact": "black_box_cases.json",
                "row_id": "BB-11",
                "code": "source_claim_contradicted",
                "message": (
                    "Use libnvme/src/nvme/crypto.c and libnvmf_set_keyring "
                    "instead of the stale source anchor"
                ),
            }
        ],
    }

    prompt = _regular_stage_prompt(
        plan={"original_user_request": "analyze TLS", "quality_retry_feedback": feedback},
        stage={
            "id": "black_box_cases",
            "artifact": "black_box_cases.json",
            "purpose": "black-box cases",
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

    assert "SRC-15:L1004" in catalog_section
    assert "libnvmf_set_keyring" in catalog_section


def test_canonical_claim_evidence_recovers_stale_id_from_verified_path_and_quote():
    rows = [
        {
            "case_id": "BB-03",
            "technical_claims": [
                {
                    "claim_id": "TC-BB-03",
                    "evidence": [
                        {
                            "evidence_id": "SRC-13:L1004",
                            "path": "libnvme/src/nvme/crypto.c",
                            "lines": "L1004",
                            "quote": "__libnvme_public int libnvmf_set_keyring(",
                            "symbol": "libnvmf_set_keyring",
                        }
                    ],
                }
            ],
        }
    ]
    catalog = [
        {
            "evidence_id": "SRC-15:L1004",
            "path": "libnvme/src/nvme/crypto.c",
            "lines": "L1004",
            "quote": "__libnvme_public int libnvmf_set_keyring(",
            "symbol": "libnvmf_set_keyring",
        }
    ]

    normalized = _canonicalize_technical_claim_evidence(rows, catalog)

    assert normalized[0]["technical_claims"][0]["evidence"] == catalog


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
        "breadth_inventory",
        "developer_explanation",
        "scenario_expansion",
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
    assert sfmea_claim_schema["maxItems"] == 2
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


def test_explicit_sfmea_and_black_box_outputs_inherit_combined_report_minimums():
    contract = {
        "target": "iSCSI login",
        "required_outputs": ["sfmea.json", "black_box_cases.json", "report.md"],
        "artifact_contract": {
            "sfmea.json": {"artifact": "sfmea.json", "schema": SFMEA_SCHEMA},
            "black_box_cases.json": {
                "artifact": "black_box_cases.json",
                "schema": BLACK_BOX_CASES_SCHEMA,
            },
            "report.md": {
                "artifact": "report.md",
                "min_sfmea_rows": 12,
                "min_black_box_cases": 12,
            },
        },
    }

    plan = build_staged_execution_plan(
        contract=contract,
        original_user_request="生成完整 iSCSI login 测试报告",
    )
    sfmea = next(stage for stage in plan["stages"] if stage["id"] == "sfmea")
    cases = next(
        stage for stage in plan["stages"] if stage["id"] == "black_box_cases"
    )

    assert sfmea["output_contract"]["schema"]["minItems"] == 12
    assert cases["output_contract"]["schema"]["minItems"] == 12
    assert sfmea["output_limits"]["max_items"] >= 12
    assert cases["output_limits"]["max_items"] >= 12


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
    assert result["harness_validation"]["transport"] == "synthetic_loopback"
    assert result["harness_validation"]["validation_scope"] == "synthetic_harness_self_test"
    assert result["harness_validation"]["target_kind"] == "simulated_loopback"
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


def test_refresh_deterministic_combined_report_rebuilds_from_current_json_artifacts(tmp_path):
    from app.services.ai_staged_execution import refresh_deterministic_combined_report

    source_stage = tmp_path / "stages" / "source_analysis"
    source_stage.mkdir(parents=True)
    (source_stage / "source_evidence_pack.json").write_text(
        json.dumps(
            {
                "repo_revision": "abc1234",
                "evidence_cards": [
                    {
                        "file_path": "lib/iscsi/iscsi.c",
                        "start_line": 10,
                        "end_line": 12,
                        "symbols": ["login"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "business_flow.md").write_text("登录主流程。\n", encoding="utf-8")
    (tmp_path / "sfmea.json").write_text(
        json.dumps(
            [{"sfmea_id": "SFMEA-02", "failure_mode": "保留风险"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "black_box_cases.json").write_text(
        json.dumps(
            [{"case_id": "BB-02", "scenario_name": "保留用例"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    refreshed = refresh_deterministic_combined_report(
        artifact_dir=tmp_path,
        plan={"target": "iSCSI 登录测试"},
        output_contract={"min_sfmea_rows": 1, "min_black_box_cases": 1},
    )

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert refreshed["artifact"] == "report.md"
    assert "SFMEA-02" in report
    assert "BB-02" in report
    assert "SFMEA-01" not in report


def test_refresh_combined_report_uses_task_owned_cards_when_cached_sidecar_is_missing(tmp_path):
    from app.services.ai_staged_execution import refresh_deterministic_combined_report

    (tmp_path / "evidence_cards.json").write_text(
        json.dumps([{
            "file_path": "lib/iscsi/conn.c",
            "start_line": 625,
            "end_line": 645,
            "symbols": ["_iscsi_conn_destruct"],
            "sha256": "verified-digest",
        }]),
        encoding="utf-8",
    )
    (tmp_path / "source_scope.json").write_text(json.dumps({"scope_id": "current"}), encoding="utf-8")
    (tmp_path / "business_flow.md").write_text("连接清理流程。\n", encoding="utf-8")
    (tmp_path / "sfmea.json").write_text(json.dumps([{"sfmea_id": "SFMEA-01", "failure_mode": "清理风险"}]), encoding="utf-8")
    (tmp_path / "black_box_cases.json").write_text(json.dumps([{"case_id": "BB-01", "scenario_name": "清理验证"}]), encoding="utf-8")

    refresh_deterministic_combined_report(
        artifact_dir=tmp_path,
        plan={"target": "连接清理测试", "repo_revision": "abc1234"},
        output_contract={"min_sfmea_rows": 1, "min_black_box_cases": 1},
    )

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "本报告基于 1 张" in report
    assert "lib/iscsi/conn.c:625-645" in report


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


def test_combined_report_keeps_black_box_technical_claims_visible_to_testers():
    from app.services.ai_staged_execution import _render_deterministic_combined_report

    report = _render_deterministic_combined_report(
        plan={"original_user_request": "完整分析 iSCSI login"},
        source_pack={"repo_revision": "abc123", "evidence_cards": []},
        business_flow="主流程。",
        sfmea=[],
        black_box_cases=[
            {
                "case_id": "BB-009",
                "scenario_name": "首 payload 后 timer 注销",
                "technical_claims": [
                    {
                        "claim_id": "CLAIM-TIMER-001",
                        "statement": "SPDK 在首个 Login payload 中注销 login_timer。",
                    }
                ],
            }
        ],
    )

    assert "源码断言：SPDK 在首个 Login payload 中注销 login_timer。" in report


def test_quality_repair_declares_existing_claim_evidence_on_its_black_box_row():
    from app.services.ai_staged_execution import _deterministic_quality_claim_repair

    repaired, fields = _deterministic_quality_claim_repair(
        [
            {
                "case_id": "BB-037",
                "source_or_test_evidence": ["SRC-06:L82"],
                "technical_claims": [
                    {
                        "claim_id": "CLAIM-MUT-NOPROV-BEH",
                        "type": "behavior_assertion",
                        "evidence": [
                            {
                                "evidence_id": "SRC-03:L526",
                                "path": "include/spdk/iscsi_spec.h",
                            }
                        ],
                    }
                ],
            }
        ],
        artifact="black_box_cases.json",
        quality_feedback={
            "issues": [
                {
                    "artifact": "black_box_cases.json",
                    "code": "claim_evidence_not_declared_for_row",
                    "claim_id": "CLAIM-MUT-NOPROV-BEH",
                }
            ]
        },
        evidence_cards=[
            {
                "evidence_id": "SRC-03",
                "file_path": "include/spdk/iscsi_spec.h",
                "start_line": 500,
                "end_line": 540,
            }
        ],
    )

    assert repaired[0]["source_or_test_evidence"] == ["SRC-06:L82", "SRC-03:L526"]
    assert "$[0].source_or_test_evidence" in fields


def test_quality_repair_removes_a_claim_that_independent_validation_cannot_support():
    """Never relabel an arbitrary card as proof for a rejected behaviour claim."""
    repaired, fields = _deterministic_quality_claim_repair(
        [
            {
                "case_id": "BB-013",
                "scenario_name": "CHAP 参数顺序错误",
                "source_or_test_evidence": ["SRC-LOGIN"],
                "technical_claims": [
                    {
                        "claim_id": "CLAIM-BB-013-B",
                        "type": "code_behavior",
                        "statement": "参数顺序错误会被 target 拒绝。",
                        "evidence": [{"evidence_id": "SRC-LOGIN"}],
                    },
                    {
                        "claim_id": "CLAIM-BB-013-A",
                        "type": "source_anchor",
                        "statement": "literal verified source quote",
                        "evidence": [{"evidence_id": "SRC-LOGIN"}],
                    },
                ],
            }
        ],
        artifact="black_box_cases.json",
        quality_feedback={
            "issues": [
                {
                    "artifact": "black_box_cases.json",
                    "code": "source_claim_insufficient",
                    "claim_id": "CLAIM-BB-013-B",
                }
            ]
        },
        evidence_cards=[
            {
                "evidence_id": "SRC-LOGIN",
                "file_path": "lib/iscsi/iscsi.c",
                "start_line": 100,
                "end_line": 110,
                "excerpt": "iscsi_parse_params(conn, pdu);",
            }
        ],
    )

    assert [claim["claim_id"] for claim in repaired[0]["technical_claims"]] == [
        "CLAIM-BB-013-A"
    ]
    assert "$[0].technical_claims[CLAIM-BB-013-B]._remove_unsupported" in fields


def test_quality_repair_adds_a_bounded_chap_order_hypothesis_when_deep_coverage_requires_it():
    from app.services.ai_staged_execution import _deterministic_quality_claim_repair

    repaired, fields = _deterministic_quality_claim_repair(
        [],
        artifact="black_box_cases.json",
        quality_feedback={
            "issues": [
                {
                    "artifact": "black_box_cases.json",
                    "code": "professional_coverage_incomplete",
                    "scenarios": ["CHAP 参数顺序错误"],
                }
            ]
        },
        evidence_cards=[
            {
                "evidence_id": "SRC-06",
                "file_path": "test/iscsi_tgt/chap/chap_common.sh",
                "start_line": 82,
                "end_line": 99,
                "excerpt": "function config_chap_credentials_for_target() {",
                "symbols": ["config_chap_credentials_for_target"],
            }
        ],
    )

    assert repaired[0]["case_id"] == "BBC-CHAP-ORDER"
    assert repaired[0]["scenario_name"] == "CHAP 参数顺序错误"
    assert repaired[0]["technical_claims"] == []
    assert repaired[0]["source_or_test_evidence"] == ["SRC-06"]
    assert "待验证" in repaired[0]["expected_result"]
    assert "$[+].chap_parameter_order_case" in fields


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


def test_deterministic_combined_report_closes_unbalanced_flow_fence_before_delivery_sections():
    from app.services.ai_staged_execution import _render_deterministic_combined_report
    from app.services.test_activity_contract import _markdown_heading_matches

    report = _render_deterministic_combined_report(
        plan={"original_user_request": "iSCSI login 测试分析"},
        source_pack={"repo_revision": "abc123", "evidence_cards": []},
        business_flow="流程摘要。\n```text\n模型未闭合的流程图",
        sfmea=[{"sfmea_id": "SFMEA-01", "failure_mode": "登录超时后连接未关闭"}],
        black_box_cases=[{"case_id": "BB-01", "scenario_name": "登录超时"}],
    )

    visible_headings = {match.group(1) for match in _markdown_heading_matches(report)}
    assert "SFMEA" in visible_headings
    assert "黑盒测试用例" in visible_headings


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


def test_combined_report_finalizer_splits_file_line_cells_for_markdown_table():
    content = """## 分析范围与证据缺口

| 状态对象 | 文件 | 行号 | 说明 |
|----------|------|------|------|
| `spdk_iscsi_conn` | `lib/iscsi/conn.c:150` | 连接结构体 |

## SFMEA
| ID | 故障模式 |
|---|---|
| SFMEA-01 | 登录失败 |

## 黑盒测试用例
| ID | 场景 |
|---|---|
| BB-01 | 合法登录 |
"""
    finalized, _ = _finalize_combined_markdown_report(
        content=content,
        source_pack={"evidence_cards": [{"file_path": "lib/iscsi/conn.c", "sha256": "a" * 64}]},
        output_contract={"min_sfmea_rows": 1, "min_black_box_cases": 1},
    )

    assert "| `spdk_iscsi_conn` | `lib/iscsi/conn.c` | 150 | 连接结构体 |" in finalized


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


def test_invalid_json_artifact_cannot_be_reused_as_quality_repair_seed():
    assert _is_valid_json_artifact_seed(
        '[{"steps":["nvme connect --dhchap-secret <redacted>"abc"]}]',
        "black_box_cases.json",
    ) is False
    assert _is_valid_json_artifact_seed(
        '[{"steps":["nvme connect --dhchap-secret \\\"<redacted>\\\""]}]',
        "black_box_cases.json",
    ) is True


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


def test_deterministic_schema_repair_lifts_flat_source_anchor_into_claim():
    payload = [{
        "case_id": "BB-01",
        "technical_claims": [{
            "evidence_id": "SRC-01:L42",
            "path": "lib/iscsi/iscsi.c",
            "quote": "iscsi_conn_login_pdu_success_complete(void *arg)",
            "lines": "L42",
            "symbol": "iscsi_conn_login_pdu_success_complete",
        }],
    }]
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "technical_claims": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["claim_id", "type", "statement", "evidence"],
                        "properties": {
                            "claim_id": {"type": "string"},
                            "type": {"type": "string"},
                            "statement": {"type": "string"},
                            "evidence": {"type": "array"},
                        },
                    },
                },
            },
        },
    }

    repaired, fields = _deterministic_schema_repair(payload, schema)

    claim = repaired[0]["technical_claims"][0]
    assert claim["type"] == "source_anchor"
    assert claim["statement"] == claim["quote"]
    assert claim["evidence"] == [{
        "evidence_id": "SRC-01:L42",
        "path": "lib/iscsi/iscsi.c",
        "quote": "iscsi_conn_login_pdu_success_complete(void *arg)",
        "lines": "L42",
        "symbol": "iscsi_conn_login_pdu_success_complete",
    }]
    assert "$[0].technical_claims[0].evidence" in fields


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


def test_deterministic_quality_claim_repair_materializes_audited_iscsi_vague_steps():
    payload = [
        {
            "case_id": "BB-09",
            "scenario_name": "长时间保持 iSCSI 登录状态",
            "test_dimension": "long_steady_state",
            "source_or_test_evidence": ["lib/iscsi/conn.c"],
            "steps": ["保持会话空闲 1 小时", "执行 IO 操作"],
        }
    ]

    repaired, fields = _deterministic_quality_claim_repair(
        payload,
        artifact="black_box_cases.json",
        quality_feedback={
            "issues": [
                {
                    "artifact": "black_box_cases.json",
                    "code": "black_box_case_quality_failed",
                    "invalid_cases": [
                        {"case_id": "BB-09", "reasons": ["vague_steps"]}
                    ],
                }
            ]
        },
    )

    assert fields == ["$[0].steps"]
    assert "iscsiadm -m session" in repaired[0]["steps"][0]
    assert "fio" in repaired[0]["steps"][-1]
    from app.services.test_activity_contract import black_box_steps_are_actionable

    assert black_box_steps_are_actionable(repaired[0]["steps"])


def test_deterministic_quality_repair_removes_audited_duplicate_black_box_case():
    payload = [
        {
            "case_id": "BB-KEEP",
            "scenario_name": "非法 Login 请求",
            "steps": ["发送非法 Login Request 并记录响应"],
            "expected_result": "请求被拒绝并记录响应。",
        },
        {
            "case_id": "BB-DUPLICATE",
            "scenario_name": "重复的非法 Login 请求",
            "steps": ["发送非法 Login Request 并记录响应"],
            "expected_result": "请求被拒绝并记录响应。",
        },
    ]

    repaired, fields = _deterministic_quality_claim_repair(
        payload,
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "black_box_case_quality_failed",
            "invalid_cases": [{
                "case_id": "BB-DUPLICATE",
                "reasons": ["duplicate_black_box_case"],
            }],
        }]},
    )

    assert [row["case_id"] for row in repaired] == ["BB-KEEP"]
    assert fields == ["BB-DUPLICATE._delete_duplicate"]


def test_deterministic_quality_claim_repair_replaces_calsoft_semantic_mapping():
    payload = [{
        "case_id": "BC-12",
        "scenario_name": "Login Response 中 T 和 C 位同时设置时的错误传播",
        "mapped_test_dir": "test/iscsi_tgt/calsoft/calsoft.py",
        "expected_result": "target 返回 Initiator Error",
        "steps": ["使用协议测试工具发送构造的 Login Response PDU"],
    }]

    repaired, fields = _deterministic_quality_claim_repair(
        payload,
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_fuzz_calsoft_semantic_mapping",
            "row_id": "BC-12",
        }]},
    )

    assert "$[0].mapped_test_dir" in fields
    assert "ai_suggested_unverified" in repaired[0]["mapped_test_dir"]
    assert "raw-PDU harness" in repaired[0]["expected_result"]
    assert "不得把 calsoft" in repaired[0]["expected_result"].lower()


def test_deterministic_quality_claim_repair_corrects_sfmea_c_bit_claim():
    payload = [{
        "sfmea_id": "SFMEA-010",
        "failure_mode": "Login 响应中错误标志未正确清除",
        "cause": "错误分支清除 T/C/CSG/NSG",
    }]
    repaired, fields = _deterministic_quality_claim_repair(
        payload,
        artifact="sfmea.json",
        quality_feedback={"issues": [{
            "artifact": "sfmea.json",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_login_error_c_flag_preserved",
            "row_id": "SFMEA-010",
        }]},
    )
    assert "$[0].failure_mode" in fields
    assert "但不清除 C bit" in repaired[0]["cause"]
    assert "raw-PDU harness" in repaired[0]["detection"]


def test_deterministic_quality_claim_repair_routes_report_c_bit_feedback_to_matching_sfmea_row():
    payload = [
        {
            "sfmea_id": "SFMEA-006",
            "failure_mode": "错误 Login Response 的标志位处理与协议语义不一致",
            "cause": "错误响应分支应清除 T、CSG 和 NSG；C bit 必须按请求与协议语义单独判读。",
        },
        {
            "sfmea_id": "SFMEA-007",
            "failure_mode": "普通资源耗尽",
            "cause": "无关条目",
        },
    ]

    repaired, fields = _deterministic_quality_claim_repair(
        payload,
        artifact="sfmea.json",
        quality_feedback={"issues": [{
            "artifact": "report.md",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_login_error_c_flag_preserved",
        }]},
    )

    assert "$[0].cause" in fields
    assert "但不清除 C bit" in repaired[0]["cause"]
    assert repaired[1]["cause"] == "无关条目"


def test_deterministic_quality_claim_repair_uses_audited_excerpt_row_id_for_sfmea_facts():
    payload = [
        {
            "sfmea_id": "SFMEA-006",
            "failure_mode": "CHAP 认证失败后未清除安全上下文",
            "cause": "错误分支会清除 T/C/CSG/NSG",
        },
        {
            "sfmea_id": "SFMEA-012",
            "failure_mode": "登录定时器在首个 PDU 后注销，后续停滞无超时保护",
            "cause": "首个 Login PDU 后停滞必由 30 秒登录定时器清理",
            "detection": "发送首个 Login PDU 后停滞 60 秒，确认连接被关闭",
        },
    ]
    feedback = {
        "issues": [
            {
                "artifact": "sfmea.json",
                "code": "professional_fact_conflict",
                "constraint_id": "iscsi_login_error_c_flag_preserved",
                "conflicting_excerpt": "| SFMEA-006 | CHAP 认证失败后未清除安全上下文 |",
            },
            {
                "artifact": "sfmea.json",
                "code": "professional_fact_conflict",
                "constraint_id": "iscsi_login_timer_after_first_pdu",
                "conflicting_excerpt": "| SFMEA-012 | 登录定时器在首个 PDU 后注销 |",
            },
        ]
    }

    repaired, fields = _deterministic_quality_claim_repair(
        payload,
        artifact="sfmea.json",
        quality_feedback=feedback,
    )

    assert "$[0].cause" in fields
    assert "但不清除 C bit" in repaired[0]["cause"]
    assert "$[1].cause" in fields
    assert "当前多阶段登录不重新注册该定时器" in repaired[1]["cause"]
    assert "不把 30 秒登录定时器清理作为预期" in repaired[1]["detection"]


def test_deterministic_quality_claim_repair_corrects_unknown_key_and_multiconnection_sfmea():
    payload = [
        {
            "sfmea_id": "SFMEA-008",
            "failure_mode": "未知参数键未按规范返回错误",
            "cause": "未知键解析失败并断开连接",
            "detection": "检查目标是否关闭连接",
            "mitigation": "修复解析失败路径",
        },
        {
            "sfmea_id": "SFMEA-012",
            "failure_mode": "并发登录时资源竞争导致连接状态不一致",
            "test_mapping": "test/iscsi_tgt/multiconnection/multiconnection.sh",
        },
    ]
    feedback = {
        "issues": [
            {
                "artifact": "sfmea.json",
                "code": "professional_fact_conflict",
                "constraint_id": "iscsi_unknown_key_not_understood",
                "conflicting_excerpt": "| SFMEA-008 | 未知参数键未按规范返回错误 |",
            },
            {
                "artifact": "sfmea.json",
                "code": "professional_fact_conflict",
                "constraint_id": "iscsi_multiconnection_mapping_scope",
                "conflicting_excerpt": "| SFMEA-012 | 并发登录时资源竞争导致连接状态不一致 |",
            },
        ]
    }

    repaired, fields = _deterministic_quality_claim_repair(
        payload,
        artifact="sfmea.json",
        quality_feedback=feedback,
    )

    assert "$[0].expected_result" not in fields
    assert "$[0].cause" in fields
    assert "NotUnderstood" in repaired[0]["cause"]
    assert "格式非法" in repaired[0]["detection"]
    assert "$[1].test_mapping" in fields
    assert repaired[1]["test_mapping"].startswith("ai_suggested_unverified:")


def test_deterministic_quality_claim_repair_corrects_black_box_flags_and_thresholds():
    payload = [
        {
            "case_id": "BC-07",
            "scenario_name": "CHAP authentication failure followed by successful re-login",
            "expected_result": "认证失败响应保留 T=1 并进入阶段迁移。",
            "observability": ["抓取 Login Response"],
        },
        {
            "case_id": "BC-PERF-01",
            "scenario_name": "Login performance baseline",
            "expected_result": "P99 Login latency must be below 50 ms.",
            "oracle_basis": "固定 50 ms 阈值",
        },
    ]
    repaired, fields = _deterministic_quality_claim_repair(
        payload,
        artifact="black_box_cases.json",
        quality_feedback={"issues": [
            {"artifact": "black_box_cases.json", "code": "professional_fact_conflict", "constraint_id": "iscsi_login_error_flags_cleared", "row_id": "BC-07"},
            {"artifact": "black_box_cases.json", "code": "ungrounded_performance_threshold", "row_id": "BC-PERF-01"},
        ]},
    )
    assert "$[0].expected_result" in fields
    assert "清除 T、CSG、NSG" in repaired[0]["expected_result"]
    assert "$[1].expected_result" in fields
    assert "相对退化门槛" in repaired[1]["expected_result"]
    assert "50 ms" not in repaired[1]["expected_result"]


def test_deterministic_quality_claim_repair_resolves_black_box_flag_audit_section_heading():
    payload = [{
        "case_id": "BB-002",
        "scenario_name": "T+C 非法组合",
        "expected_result": "错误响应会清除 T/C/CSG/NSG。",
        "observability": ["抓取 Login Response"],
        "failure_diagnostics": ["检查 flags"],
    }]

    repaired, fields = _deterministic_quality_claim_repair(
        payload,
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_login_error_c_flag_preserved",
            "section_heading": "BB-002 T+C 非法组合",
        }]},
    )

    assert "$[0].expected_result" in fields
    assert "清除 T、CSG、NSG" in repaired[0]["expected_result"]
    assert "C bit" in " ".join(repaired[0]["observability"])
    assert "T/C/CSG/NSG" not in " ".join(repaired[0]["failure_diagnostics"])


def test_deterministic_quality_claim_repair_routes_report_unit_mapping_feedback_to_sfmea():
    payload = [
        {
            "sfmea_id": "SFMEA-012",
            "failure_mode": "参数解析失败后参数对象未释放",
            "test_mapping": "test/unit/lib/iscsi/iscsi.c/iscsi_ut.c",
        },
        {
            "sfmea_id": "SFMEA-013",
            "failure_mode": "普通错误",
            "test_mapping": "test/iscsi_tgt/chap/chap_common.sh",
        },
    ]

    repaired, fields = _deterministic_quality_claim_repair(
        payload,
        artifact="sfmea.json",
        quality_feedback={"issues": [{
            "artifact": "report.md",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_unit_coverage_scope",
        }]},
    )

    assert "$[0].test_mapping" in fields
    assert "iscsi_ut.c" not in repaired[0]["test_mapping"]
    assert repaired[1]["test_mapping"] == "test/iscsi_tgt/chap/chap_common.sh"


def test_deterministic_quality_claim_repair_removes_report_only_missing_sfmea_test_path():
    payload = [{
        "sfmea_id": "SFMEA-001",
        "failure_mode": "登录超时后连接未完全释放资源",
        "test_mapping": "test/iscsi_tgt/login_timeout/login_timeout.sh (待创建)",
    }]

    repaired, fields = _deterministic_quality_claim_repair(
        payload,
        artifact="sfmea.json",
        quality_feedback={"issues": [{
            "artifact": "report.md",
            "code": "evidence_path_not_found",
            "evidence_path": "test/iscsi_tgt/login_timeout/login_timeout.sh",
        }]},
    )

    assert "$[0].test_mapping" in fields
    assert "login_timeout/login_timeout.sh" not in repaired[0]["test_mapping"]
    assert "ai_suggested_unverified" in repaired[0]["test_mapping"]


def test_deterministic_quality_claim_repair_routes_report_status_detail_feedback_to_sfmea():
    payload = [{
        "sfmea_id": "SFMEA-007",
        "failure_mode": "Unsupported Version 未返回正确状态码",
        "cause": "版本检查失败时使用通用 Initiator Error 而非特定 0x05",
    }]

    repaired, fields = _deterministic_quality_claim_repair(
        payload,
        artifact="sfmea.json",
        quality_feedback={"issues": [{
            "artifact": "report.md",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_login_status_detail_05",
        }]},
    )

    assert "$[0].cause" in fields
    assert "ISCSI_LOGIN_UNSUPPORTED_VERSION (0x05)" in repaired[0]["cause"]


def test_deterministic_quality_claim_repair_routes_report_rpc_mapping_feedback_to_sfmea():
    payload = [{
        "sfmea_id": "SFMEA-007",
        "failure_mode": "不支持的版本号未正确拒绝",
        "trigger_condition": "发送不支持版本的 Login Request",
        "test_mapping": "test/iscsi_tgt/rpc_config/rpc_config.py（需扩展版本测试）",
    }]

    repaired, fields = _deterministic_quality_claim_repair(
        payload,
        artifact="sfmea.json",
        quality_feedback={"issues": [{
            "artifact": "report.md",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_rpc_config_mapping_scope",
        }]},
    )

    assert "$[0].test_mapping" in fields
    assert "rpc_config.py" not in repaired[0]["test_mapping"]
    assert "raw-PDU Login harness" in repaired[0]["test_mapping"]


def test_deterministic_quality_claim_repair_removes_direct_duplicate_case_indices():
    payload = [
        {"case_id": "BC-01", "scenario_name": "保留"},
        {"case_id": "BC-02", "scenario_name": "重复"},
        {"case_id": "BC-03", "scenario_name": "重复"},
    ]
    repaired, fields = _deterministic_quality_claim_repair(
        payload,
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "duplicate_black_box_case",
            "index": 3,
        }]},
    )

    assert [row["case_id"] for row in repaired] == ["BC-01", "BC-02"]
    assert fields == ["BC-03._delete_duplicate"]


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


def test_deterministic_quality_repair_unmaps_same_target_concurrency_from_multiconnection():
    payload = [
        {
            "case_id": "BC-05",
            "scenario_name": "同一 target 的并发 Login 请求均成功",
            "steps": ["同时发起 4 个 iscsiadm --login 到同一 target"],
            "mapped_test_dir": "test/iscsi_tgt/multiconnection/multiconnection.sh",
        }
    ]
    feedback = {
        "issues": [{
            "artifact": "black_box_cases.json",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_multiconnection_mapping_scope",
            "row_id": "BC-05",
        }]
    }

    repaired, fields = _deterministic_quality_claim_repair(
        payload,
        artifact="black_box_cases.json",
        quality_feedback=feedback,
    )

    assert "ai_suggested_unverified" in repaired[0]["mapped_test_dir"]
    assert "multiconnection.sh 仅作环境搭建参考" in repaired[0]["mapped_test_dir"]
    assert repaired[0]["steps"] == payload[0]["steps"]
    assert fields == ["$[0].mapped_test_dir"]


def test_final_quality_repair_materializes_professional_mapping_fix(tmp_path):
    import json

    from app.services.ai_staged_execution import (
        materialize_final_deterministic_quality_repairs,
    )

    artifact = tmp_path / "agent_runs" / "analyze" / "black_box_cases.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps([{
        "case_id": "BC-05",
        "scenario_name": "同一 target 的并发 Login 请求均成功",
        "mapped_test_dir": "test/iscsi_tgt/multiconnection/multiconnection.sh",
    }]), encoding="utf-8")

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_multiconnection_mapping_scope",
            "row_id": "BC-05",
        }]},
    )

    assert changed == {"black_box_cases.json": ["$[0].mapped_test_dir"]}
    repaired = json.loads(artifact.read_text(encoding="utf-8"))
    assert "ai_suggested_unverified" in repaired[0]["mapped_test_dir"]


def test_final_quality_repair_rebuilds_nested_business_flow_from_verified_outline(tmp_path):
    from app.services.ai_staged_execution import (
        materialize_final_deterministic_quality_repairs,
    )

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    (artifacts / "business_flow.md").write_text(
        "# 关键业务流程分析\n\n错误响应清除 T/C/CSG/NSG。\n",
        encoding="utf-8",
    )
    (artifacts / "flow_outline.json").write_text(json.dumps({
        "analysis_target": "iSCSI Login",
        "repo_revision": "abc123",
        "actors": [],
        "main_flow": [],
        "branches": [],
        "states": [],
        "related_tests": [],
        "evidence": [],
    }), encoding="utf-8")

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "business_flow.md",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_login_error_c_flag_preserved",
        }]},
    )

    assert changed == {"business_flow.md": ["render_verified_flow_outline"]}
    rendered = (artifacts / "business_flow.md").read_text(encoding="utf-8")
    assert "确定性 Flow Outline renderer" in rendered
    assert "清除 T/C/CSG/NSG" not in rendered


def test_final_quality_repair_corrects_nested_module_map_c_bit_claim(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    module_map = artifacts / "module_map.md"
    module_map.write_text(
        "# 模块映射\n\n错误响应 flags 清除 T/C/CSG/NSG。\n",
        encoding="utf-8",
    )

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "module_map.md",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_login_error_c_flag_preserved",
        }]},
    )

    assert changed == {"module_map.md": ["iscsi_login_error_c_flag_preserved"]}
    rendered = module_map.read_text(encoding="utf-8")
    assert "清除 T/C/CSG/NSG" not in rendered
    assert "C bit 按请求与协议语义单独判读" in rendered


def test_final_quality_repair_corrects_chinese_delimited_module_map_c_bit_claim(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    module_map = artifacts / "module_map.md"
    module_map.write_text(
        "错误 Login Response 清除 T、C、CSG 和 NSG。\n",
        encoding="utf-8",
    )

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "module_map.md",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_login_error_c_flag_preserved",
        }]},
    )

    assert changed == {"module_map.md": ["iscsi_login_error_c_flag_preserved"]}
    rendered = module_map.read_text(encoding="utf-8")
    assert "清除 T、C、CSG 和 NSG" not in rendered
    assert "C bit 按请求与协议语义单独判读" in rendered


def test_final_quality_repair_corrects_c_bit_table_heading_variant(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    module_map = artifacts / "module_map.md"
    module_map.write_text(
        "| 错误响应 flags 清除 | 现有单元测试未覆盖错误响应 T/C/CSG/NSG 清除逻辑 | 新增测试 |\n",
        encoding="utf-8",
    )

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "module_map.md",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_login_error_c_flag_preserved",
        }]},
    )

    assert changed == {"module_map.md": ["iscsi_login_error_c_flag_preserved"]}
    rendered = module_map.read_text(encoding="utf-8")
    assert "错误响应 flags 清除" not in rendered
    assert "C bit 按请求与协议语义单独判读" in rendered


def test_final_quality_repair_corrects_nested_module_map_chap_role(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    module_map = artifacts / "module_map.md"
    module_map.write_text(
        "| **CHAP 协商** | `iscsi_negotiate_chap_param` | `lib/iscsi/iscsi.c:1559` | 执行 CHAP 认证协商 | `SRC-01:L1559` |\n",
        encoding="utf-8",
    )

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "module_map.md",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_chap_execution_role",
        }]},
    )

    assert changed == {"module_map.md": ["iscsi_chap_execution_role"]}
    rendered = module_map.read_text(encoding="utf-8")
    assert "执行 CHAP 认证协商" not in rendered
    assert "实际 CHAP challenge/response 校验由 `iscsi_auth_params` 路径执行" in rendered


def test_final_quality_repair_corrects_variant_chap_module_map_row(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    module_map = artifacts / "module_map.md"
    module_map.write_text(
        "| CHAP 协商 | `lib/iscsi/iscsi.c` | `iscsi_negotiate_chap_param` | 执行 CHAP 认证协商 |\n",
        encoding="utf-8",
    )

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "module_map.md",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_chap_execution_role",
        }]},
    )

    assert changed == {"module_map.md": ["iscsi_chap_execution_role"]}
    rendered = module_map.read_text(encoding="utf-8")
    assert "执行 CHAP 认证协商" not in rendered
    assert "iscsi_auth_params" in rendered


def test_final_quality_repair_renames_module_map_dependency_section(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    module_map = artifacts / "module_map.md"
    module_map.write_text(
        "## 核心函数调用链\n\n`iscsi_parse_params` 解析输入。\n",
        encoding="utf-8",
    )

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "module_map.md",
            "code": "missing_markdown_sections",
            "sections": ["依赖"],
        }]},
    )

    assert changed == {"module_map.md": ["dependency_section_heading"]}
    assert "## 依赖与调用链" in module_map.read_text(encoding="utf-8")


def test_final_quality_repair_renames_module_map_dependency_graph_section(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    module_map = artifacts / "module_map.md"
    module_map.write_text(
        "## 核心函数依赖图\n\n`iscsi_parse_params` 解析输入。\n",
        encoding="utf-8",
    )

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "module_map.md",
            "code": "missing_markdown_sections",
            "sections": ["依赖"],
        }]},
    )

    assert changed == {"module_map.md": ["dependency_section_heading"]}
    assert "## 依赖与调用链" in module_map.read_text(encoding="utf-8")


def test_final_quality_repair_removes_nested_module_map_path_from_audit_message(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    module_map = artifacts / "module_map.md"
    module_map.write_text("# 模块映射\n\n依赖 `lib/not-real/`。\n", encoding="utf-8")

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "module_map.md",
            "code": "evidence_path_not_found",
            "message": "证据路径不存在: lib/not-real/",
        }]},
    )

    assert changed == {"module_map.md": ["evidence_path_not_found"]}
    assert "lib/not-real/" not in module_map.read_text(encoding="utf-8")


def test_final_quality_repair_rebuilds_legacy_flow_map_alias_from_verified_outline(tmp_path):
    from app.services.ai_staged_execution import (
        materialize_final_deterministic_quality_repairs,
    )

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    (artifacts / "flow_map.md").write_text(
        "# 业务流程\n\n初始状态为 ISCSI_SECURITY_NEGOTIATION。\n",
        encoding="utf-8",
    )
    (artifacts / "flow_outline.json").write_text(json.dumps({
        "analysis_target": "iSCSI Login",
        "repo_revision": "abc123",
        "entry_points": [],
        "steps": [],
        "error_flows": [],
        "cleanup_flows": [],
        "recovery_flows": [],
        "state_objects": [],
        "state_transitions": [],
        "related_tests": [],
        "evidence": [],
    }), encoding="utf-8")

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "business_flow.md",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_rpc_login_phase_values",
        }]},
    )

    assert changed == {"flow_map.md": ["render_verified_flow_outline"]}
    rendered = (artifacts / "flow_map.md").read_text(encoding="utf-8")
    assert "确定性 Flow Outline renderer" in rendered
    assert "ISCSI_SECURITY_NEGOTIATION" not in rendered


def test_final_quality_repair_removes_calsoft_latency_mapping_from_test_strategy(tmp_path):
    from app.services.ai_staged_execution import (
        materialize_final_deterministic_quality_repairs,
    )

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    strategy = artifacts / "test_strategy.md"
    strategy.write_text(
        "| BB-PERF-008 | Login 延迟基线 | performance | test/iscsi_tgt/calsoft/calsoft.py | - |\n",
        encoding="utf-8",
    )

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "test_strategy.md",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_calsoft_mapping_scope",
        }]},
    )

    assert changed == {"test_strategy.md": ["iscsi_calsoft_mapping_scope"]}
    rendered = strategy.read_text(encoding="utf-8")
    assert "calsoft.py" not in rendered
    assert "独立 Login 延迟计时与抓包 harness" in rendered


def test_final_quality_repair_normalizes_calsoft_latency_mapping_before_audit(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    strategy = artifacts / "test_strategy.md"
    strategy.write_text(
        "| L4 - 性能测试 | 登录延迟与吞吐 | 黑盒 | calsoft.py, 自定义脚本 |\n",
        encoding="utf-8",
    )

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": []},
    )

    assert changed == {"test_strategy.md": ["iscsi_calsoft_mapping_scope"]}
    rendered = strategy.read_text(encoding="utf-8")
    assert "calsoft.py" not in rendered
    assert "独立 Login 延迟计时与抓包 harness" in rendered


def test_final_quality_repair_normalizes_rpc_config_wire_mapping_before_audit(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    (artifacts / "black_box_cases.json").write_text(
        json.dumps([{
            "case_id": "BC-12",
            "scenario_name": "Login with T=1 and C=1 simultaneously is rejected",
            "mapped_test_dir": "test/iscsi_tgt/rpc_config/rpc_config.py",
            "steps": ["send Login PDU with CSG=0 and T=1"],
        }]),
        encoding="utf-8",
    )
    strategy = artifacts / "test_strategy.md"
    strategy.write_text(
        "| SFMEA-005 | T=1 和 C=1 同时设置 | 黑盒测试 BC-12 | `test/iscsi_tgt/rpc_config/rpc_config.py` |\n",
        encoding="utf-8",
    )

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": []},
    )

    cases = json.loads((artifacts / "black_box_cases.json").read_text(encoding="utf-8"))
    assert "rpc_config.py" not in cases[0]["mapped_test_dir"]
    assert "raw-PDU Login wire" in cases[0]["mapped_test_dir"]
    rendered = strategy.read_text(encoding="utf-8")
    assert "rpc_config.py" not in rendered
    assert "raw-PDU Login wire" in rendered
    assert "black_box_cases.json" in changed
    assert "test_strategy.md" in changed


def test_final_quality_repair_rebinds_claim_path_to_verified_evidence_card(tmp_path):
    from app.services.ai_staged_execution import (
        materialize_final_deterministic_quality_repairs,
    )

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    (artifacts / "evidence_cards.json").write_text(json.dumps([{
        "evidence_id": "SRC-06",
        "file_path": "test/unit/lib/iscsi/iscsi.c/iscsi_ut.c",
        "start_line": 174,
        "end_line": 174,
        "excerpt": "static void op_login_session_normal_test(void)",
        "sha256": "verified",
    }]), encoding="utf-8")
    target = artifacts / "black_box_cases.json"
    target.write_text(json.dumps([{
        "case_id": "BB-RECONNECT-001",
        "technical_claims": [{
            "claim_id": "TC-RECONNECT-001",
            "evidence": [{
                "evidence_id": "SRC-06:L174",
                "path": "lib/iscsi/iscsi.c/iscsi_ut.c",
            }],
        }],
    }]), encoding="utf-8")

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "row_id": "BB-RECONNECT-001",
            "claim_id": "TC-RECONNECT-001",
            "code": "source_claim_insufficient",
        }]},
    )

    repaired = json.loads(target.read_text(encoding="utf-8"))
    assert changed == {
        "black_box_cases.json": [
            "$[0].technical_claims[0].evidence[0].path",
            "$[0].source_or_test_evidence",
        ]
    }
    assert repaired[0]["technical_claims"][0]["evidence"][0]["path"] == (
        "test/unit/lib/iscsi/iscsi.c/iscsi_ut.c"
    )


def test_final_quality_repair_replaces_contradicted_claim_with_verified_anchor(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    (artifacts / "evidence_cards.json").write_text(json.dumps([{
        "evidence_id": "SRC-07",
        "file_path": "lib/iscsi/iscsi.c",
        "start_line": 1889,
        "end_line": 1889,
        "excerpt": "rc = iscsi_op_login_session_discovery_chap(conn);",
        "symbols": ["iscsi_op_login_phase_none"],
        "sha256": "verified",
    }]), encoding="utf-8")
    target = artifacts / "black_box_cases.json"
    target.write_text(json.dumps([{
        "case_id": "BC-03",
        "technical_claims": [{
            "claim_id": "TC-03",
            "type": "source_code_behavior",
            "statement": "invented behavior",
            "evidence": [{"evidence_id": "SRC-07:L1889", "quote": "invented"}],
        }],
    }]), encoding="utf-8")

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "row_id": "BC-03",
            "claim_id": "TC-03",
            "code": "source_claim_contradicted",
        }]},
    )

    repaired = json.loads(target.read_text(encoding="utf-8"))
    claim = repaired[0]["technical_claims"][0]
    assert changed == {
        "black_box_cases.json": [
            "$[0].technical_claims[0]",
            "$[0].source_or_test_evidence",
        ]
    }
    assert claim["type"] == "source_anchor"
    assert claim["statement"] == "rc = iscsi_op_login_session_discovery_chap(conn);"
    assert claim["evidence"][0]["path"] == "lib/iscsi/iscsi.c"


def test_final_quality_repair_removes_audited_missing_module_map_path(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    module_map = artifacts / "module_map.md"
    module_map.write_text("CHAP (`lib/iscsi/chap.c`)\n", encoding="utf-8")

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "module_map.md",
            "code": "evidence_path_not_found",
            "evidence_path": "lib/iscsi/chap.c",
        }]},
    )

    assert changed == {"module_map.md": ["evidence_path_not_found"]}
    assert "lib/iscsi/chap.c" not in module_map.read_text(encoding="utf-8")


def test_final_quality_repair_removes_audited_missing_flow_map_path(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    flow_map = artifacts / "flow_map.md"
    flow_map.write_text(
        "CHAP 协商细节需要进一步分析 `iscsi_chap.c` 中的实现。\n",
        encoding="utf-8",
    )

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "flow_map.md",
            "code": "evidence_path_not_found",
            "evidence_path": "iscsi_chap.c",
        }]},
    )

    assert changed == {"flow_map.md": ["evidence_path_not_found"]}
    repaired = flow_map.read_text(encoding="utf-8")
    assert "iscsi_chap.c" not in repaired
    assert "待确认实现文件" in repaired


def test_final_quality_repair_replaces_audited_missing_test_strategy_path_with_explicit_harness_gap(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    target = artifacts / "test_strategy.md"
    target.write_text(
        "认证失败映射：`test/iscsi_tgt/chap/chap_auth_failure.sh`。\n",
        encoding="utf-8",
    )

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "test_strategy.md",
            "code": "evidence_path_not_found",
            "evidence_path": "test/iscsi_tgt/chap/chap_auth_failure.sh",
        }]},
    )

    repaired = target.read_text(encoding="utf-8")
    assert changed == {"test_strategy.md": ["evidence_path_not_found"]}
    assert "chap_auth_failure.sh" not in repaired
    assert "ai_suggested_unverified: 需新增外部可执行测试 harness" in repaired


def test_final_quality_repair_removes_unverified_login_fuzzer_and_unit_coverage_mapping(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    target = artifacts / "test_strategy.md"
    target.write_text(
        "| 非法输入 | 随机 Login Request | `test/app/fuzz/iscsi_fuzz/iscsi_fuzz.c` |\n"
        "| 错误响应 flags | 已覆盖 | `test/unit/lib/iscsi/iscsi.c/iscsi_ut.c` |\n",
        encoding="utf-8",
    )

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [
            {
                "artifact": "test_strategy.md",
                "code": "professional_fact_conflict",
                "constraint_id": "iscsi_fuzzer_skips_login_opcode",
            },
            {
                "artifact": "test_strategy.md",
                "code": "professional_fact_conflict",
                "constraint_id": "iscsi_unit_coverage_scope",
            },
        ]},
    )

    repaired = target.read_text(encoding="utf-8")
    assert "明确跳过 LOGIN opcode" in repaired
    assert "已覆盖" not in repaired
    assert changed["test_strategy.md"] == [
        "iscsi_fuzzer_skips_login_opcode",
        "iscsi_unit_coverage_scope",
    ]


def test_final_quality_repair_expands_iscsi_csg_nsg_transition_semantics(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    target = artifacts / "test_strategy.md"
    target.write_text(
        "| 协议阶段 | Security Negotiation (CSG=0) → Operational Negotiation (NSG=1) 转换 |\n",
        encoding="utf-8",
    )

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "test_strategy.md",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_csg_values",
        }]},
    )

    assert changed == {"test_strategy.md": ["iscsi_csg_transition_semantics"]}
    repaired = target.read_text(encoding="utf-8")
    assert "CSG=0，NSG=1" in repaired
    assert "后续操作协商请求（CSG=1）" in repaired


def test_final_quality_repair_corrects_csg_nsg_table_state_shorthand(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    target = artifacts / "test_strategy.md"
    target.write_text(
        "| 关键状态转换 | CSG=0 (Security Negotiation) → NSG=1 (Operational Negotiation) 或 NSG=3 (Full Feature Phase) |\n",
        encoding="utf-8",
    )

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "test_strategy.md",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_csg_values",
        }]},
    )

    repaired = target.read_text(encoding="utf-8")
    assert "CSG=0（安全协商）" in repaired
    assert "后续请求 CSG=1" in repaired
    assert "NSG=3" not in repaired
    assert changed == {"test_strategy.md": ["iscsi_csg_transition_semantics"]}


def test_final_quality_repair_removes_login_fuzzer_coverage_from_strategy(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    target = artifacts / "test_strategy.md"
    target.write_text(
        "| 随机 PDU | 使用 iscsi_fuzz 发送随机 Login Request | SRC-06:L523-L541 |\n",
        encoding="utf-8",
    )

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "test_strategy.md",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_fuzzer_skips_login_opcode",
        }]},
    )

    repaired = target.read_text(encoding="utf-8")
    assert "明确跳过 LOGIN opcode" in repaired
    assert "发送随机 Login Request" not in repaired
    assert changed["test_strategy.md"] == ["iscsi_fuzzer_skips_login_opcode"]


def test_final_quality_repair_corrects_module_map_login_phase_labels(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    target = artifacts / "module_map.md"
    target.write_text(
        "Login Response (CSG=0, NSG=1, T=0)\n"
        "Login Request (CSG=1, T=0) -> 安全协商继续\n",
        encoding="utf-8",
    )

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [
            {
                "artifact": "module_map.md",
                "code": "professional_fact_conflict",
                "constraint_id": "iscsi_chap_request_response_flags",
            },
            {
                "artifact": "module_map.md",
                "code": "professional_fact_conflict",
                "constraint_id": "iscsi_csg_values",
            },
        ]},
    )

    repaired = target.read_text(encoding="utf-8")
    assert "NSG 不作为迁移字段" in repaired
    assert "CSG=1, T=0) -> 操作协商继续" in repaired
    assert changed["module_map.md"] == [
        "iscsi_login_phase_flag_semantics",
        "iscsi_csg_transition_semantics",
    ]


def test_final_quality_repair_narrows_iscsi_unit_coverage_scope(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    module_map = artifacts / "module_map.md"
    module_map.write_text(
        "| 错误响应 flags 验证 | 无单元测试覆盖 | `test/unit/lib/iscsi/iscsi.c/iscsi_ut.c` |\n",
        encoding="utf-8",
    )

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "module_map.md",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_unit_coverage_scope",
        }]},
    )

    assert changed == {"module_map.md": ["iscsi_unit_coverage_scope"]}
    assert "iscsi_ut.c" not in module_map.read_text(encoding="utf-8")


def test_final_quality_repair_prevents_test_strategy_from_claiming_unverified_iscsi_unit_coverage(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    target = artifacts / "test_strategy.md"
    target.write_text(
        "| 错误响应 flags 清除 | 单元测试未覆盖 | 新增 test/unit/lib/iscsi/iscsi.c/iscsi_ut.c 测试 |\n"
        "**禁止声明“完整覆盖”**，直至缺口通过执行验证。\n",
        encoding="utf-8",
    )

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": []},
    )

    repaired = target.read_text(encoding="utf-8")
    assert changed == {"test_strategy.md": ["iscsi_unit_coverage_scope"]}
    assert "iscsi_ut.c" not in repaired
    assert "需新增专用单元测试并逐项记录断言" in repaired
    assert "禁止声明“完整覆盖”" in repaired


def test_final_quality_repair_prevents_multiconnection_script_from_claiming_generic_concurrent_login(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    target = artifacts / "test_strategy.md"
    target.write_text(
        "| 并发 Login | 同一 ISID 并发登录 | test/iscsi_tgt/multiconnection/multiconnection.sh |\n",
        encoding="utf-8",
    )

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": []},
    )

    repaired = target.read_text(encoding="utf-8")
    assert changed == {"test_strategy.md": ["iscsi_multiconnection_mapping_scope"]}
    assert "需新增同一 Target 并发 Login harness" in repaired
    assert "不证明通用并发登录覆盖" in repaired


def test_final_quality_repair_corrects_login_error_c_bit_statement(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    target = artifacts / "black_box_cases.json"
    target.write_text(json.dumps([{
        "case_id": "BC-02",
        "expected_result": "T/C/CSG/NSG bits cleared",
    }]), encoding="utf-8")

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_login_error_c_flag_preserved",
            "row_id": "BC-02",
        }]},
    )

    repaired = json.loads(target.read_text(encoding="utf-8"))[0]
    assert "不会清除 C bit" in repaired["expected_result"]
    assert changed["black_box_cases.json"]


def test_final_quality_repair_corrects_login_timer_claim_in_black_box_case(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    target = artifacts / "black_box_cases.json"
    target.write_text(json.dumps([{
        "case_id": "BC-04",
        "expected_result": "首个 Login PDU 后停滞时，30 秒 login_timer 必然断开连接。",
    }]), encoding="utf-8")

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_login_timer_after_first_pdu",
            "row_id": "BC-04",
        }]},
    )

    repaired = json.loads(target.read_text(encoding="utf-8"))[0]
    assert "不把 30 秒 login_timer 清理作为预期" in repaired["expected_result"]
    assert changed["black_box_cases.json"]


def test_final_quality_repair_adds_recovery_and_timeout_black_box_dimensions(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    target = artifacts / "black_box_cases.json"
    target.write_text(json.dumps([{
        "case_id": "BC-01",
        "test_dimension": "normal_path",
        "technical_claims": [],
    }]), encoding="utf-8")

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "missing_black_box_dimensions",
            "dimensions": ["recovery", "timeout"],
        }]},
    )

    rows = json.loads(target.read_text(encoding="utf-8"))
    assert {row["test_dimension"] for row in rows} >= {"recovery", "timeout"}
    assert changed["black_box_cases.json"]


def test_final_quality_repair_does_not_attach_an_unrelated_source_anchor_to_black_box_rows(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    (artifacts / "evidence_cards.json").write_text(json.dumps([{
        "evidence_id": "SRC-01",
        "file_path": "lib/iscsi/iscsi.c",
        "start_line": 1889,
        "end_line": 1889,
        "excerpt": "rc = iscsi_op_login_session_discovery_chap(conn);",
        "symbols": ["iscsi_op_login_phase_none"],
        "sha256": "verified",
    }]), encoding="utf-8")
    target = artifacts / "black_box_cases.json"
    target.write_text(json.dumps([{
        "case_id": "BC-01",
        "scenario_name": "公开 initiator 登录",
        "steps": ["使用公开 initiator 发起登录"],
    }]), encoding="utf-8")

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "row_source_claim_insufficient",
            "row_id": "BC-01",
        }]},
    )

    repaired = json.loads(target.read_text(encoding="utf-8"))[0]
    assert changed == {}
    assert "technical_claims" not in repaired
    assert repaired["scenario_name"] == "公开 initiator 登录"


def test_final_quality_repair_rewrites_indexed_black_box_boundary_violation(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    target = artifacts / "black_box_cases.json"
    target.write_text(json.dumps([{
        "case_id": "BC-10",
        "steps": ["检查 conn->params_text 是否被正确清理"],
    }]), encoding="utf-8")

    materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "black_box_boundary_violation",
            "index": 1,
        }]},
    )

    row = json.loads(target.read_text(encoding="utf-8"))[0]
    assert "conn->" not in " ".join(row["steps"])
    assert "lib/" not in " ".join(row["failure_diagnostics"])


def test_final_quality_repair_normalizes_malformed_markdown_table_column_count(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    target = artifacts / "module_map.md"
    target.write_text(
        "| 缺口描述 | 建议测试路径 |\n"
        "| --- | --- |\n"
        "| 对应失败语义 | 现有证据不足 | 需新增专用测试 |\n",
        encoding="utf-8",
    )

    materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "module_map.md",
            "code": "malformed_markdown_table",
            "lines": [3],
        }]},
    )

    assert target.read_text(encoding="utf-8").splitlines()[2] == (
        "| 对应失败语义 | 现有证据不足; 需新增专用测试 |"
    )


def test_final_quality_repair_normalizes_test_strategy_table_before_audit_feedback(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    target = artifacts / "test_strategy.md"
    target.write_text(
        "| 场景 | 现有映射 | 缺口 |\n"
        "| --- | --- | --- |\n"
        "| 并发登录 | 无 | 需新增 | 附加说明 |\n",
        encoding="utf-8",
    )

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": []},
    )

    assert changed == {"test_strategy.md": ["markdown_table_column_count"]}
    assert target.read_text(encoding="utf-8").splitlines()[2] == (
        "| 并发登录 | 无 | 需新增; 附加说明 |"
    )


def test_final_quality_repair_keeps_concurrent_login_mapping_in_three_column_table(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    target = artifacts / "test_strategy.md"
    target.write_text(
        "| 测试目录 | 文件路径 | 覆盖内容 |\n"
        "| --- | --- | --- |\n"
        "| 并发 Login | test/iscsi_tgt/multiconnection/multiconnection.sh | 并发 Login 覆盖 |\n",
        encoding="utf-8",
    )

    materialize_final_deterministic_quality_repairs(tmp_path, quality_feedback={"issues": []})

    assert target.read_text(encoding="utf-8").splitlines()[2] == (
        "| 并发 Login | 会话隔离与资源收敛 | 黑盒；"
        "ai_suggested_unverified: 需新增同一 Target 并发 Login harness；"
        "multiconnection.sh 仅作多个 Target/连接环境参考，不证明通用并发登录覆盖 |"
    )


def test_final_quality_repair_preserves_case_index_column_count_after_concurrency_rewrite(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    target = artifacts / "test_strategy.md"
    target.write_text(
        "| 用例 ID | 场景 | 测试维度 | 映射测试目录 | 优先级 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| BC-03 | 同一 Target 并发 Login | concurrent | "
        "test/iscsi_tgt/multiconnection/multiconnection.sh | P1 |\n",
        encoding="utf-8",
    )

    materialize_final_deterministic_quality_repairs(tmp_path, quality_feedback={"issues": []})

    cells = [cell.strip() for cell in target.read_text(encoding="utf-8").splitlines()[2].strip("|").split("|")]
    assert len(cells) == 5
    assert "需新增同一 Target 并发 Login harness" in " | ".join(cells)


def test_final_quality_repair_rebuilds_agent_report_from_repaired_protocol_facts(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    stage_dir = artifacts / "stages" / "source_analysis"
    stage_dir.mkdir(parents=True)
    evidence_cards = [{
        "evidence_id": "SRC-01",
        "file_path": "lib/iscsi/iscsi.c",
        "start_line": 100,
        "end_line": 100,
        "excerpt": "iscsi login source anchor",
        "symbols": ["iscsi_login"],
    }]
    (stage_dir / "source_evidence_pack.json").write_text(json.dumps({
        "repo_revision": "test-revision",
        "evidence_cards": evidence_cards,
    }), encoding="utf-8")
    (artifacts / "evidence_cards.json").write_text(json.dumps(evidence_cards), encoding="utf-8")
    (artifacts / "business_flow.md").write_text("# 流程\n\n已验证流程。\n", encoding="utf-8")
    (artifacts / "staged_execution_plan.json").write_text(json.dumps({
        "original_user_request": "分析 iSCSI login",
        "repo_revision": "test-revision",
    }), encoding="utf-8")
    (artifacts / "sfmea.json").write_text(json.dumps([{
        "sfmea_id": "SFMEA-01",
        "failure_mode": "Login 错误响应标志位错误",
        "cause": "错误响应固定 CSG=1 和 NSG=3",
        "effect": "协商语义错误",
        "detection": "抓包",
        "severity": 6,
        "occurrence": 3,
        "detection_score": 4,
        "rpn": 72,
        "mitigation": "清除全部 flags",
    }, {
        "sfmea_id": "SFMEA-02",
        "failure_mode": "未知键处理错误",
        "cause": "未知 key 直接解析失败",
        "effect": "协商失败",
        "detection": "抓包",
        "severity": 4,
        "occurrence": 2,
        "detection_score": 4,
        "rpn": 32,
        "mitigation": "拒绝未知 key",
    }]), encoding="utf-8")
    (artifacts / "black_box_cases.json").write_text(json.dumps([{
        "case_id": "BB-01",
        "scenario_name": "CHAP Login flags",
        "steps": ["CHAP 首轮"],
        "expected_result": "CSG=1",
        "observability": [],
        "failure_diagnostics": [],
    }, {
        "case_id": "BB-02",
        "scenario_name": "unknown key",
        "steps": ["发送未知 key"],
        "expected_result": "解析失败并断开",
        "observability": [],
        "failure_diagnostics": [],
    }]), encoding="utf-8")
    report = artifacts / "report.md"
    report.write_text("# 模型报告\n\n未知 key 解析失败并断开。\n", encoding="utf-8")

    changed = materialize_final_deterministic_quality_repairs(tmp_path, quality_feedback={"issues": [{
        "artifact": "report.md",
        "code": "professional_fact_conflict",
        "constraint_id": "iscsi_chap_request_response_flags",
    }, {
        "artifact": "report.md",
        "code": "professional_fact_conflict",
        "constraint_id": "iscsi_unknown_key_not_understood",
    }]})

    rendered = report.read_text(encoding="utf-8")
    assert "NotUnderstood" in rendered
    assert "CSG 按协商路径记录为 0 或 1" in rendered
    assert "render_repaired_structured_delivery" in changed["report.md"]


def test_final_quality_repair_removes_report_only_missing_source_path(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    report = artifacts / "report.md"
    report.write_text(
        "## 证据缺口\n\n需要从 `lib/iscsi/chap.c` 获取 CHAP 流程。\n",
        encoding="utf-8",
    )
    (artifacts / "staged_execution_plan.json").write_text("{}", encoding="utf-8")

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "report.md",
            "code": "evidence_path_not_found",
            "message": "证据路径不存在: lib/iscsi/chap.c",
        }]},
    )

    assert changed == {"report.md": ["render_repaired_structured_delivery"]}
    assert "lib/iscsi/chap.c" not in report.read_text(encoding="utf-8")


def test_black_box_stage_fills_missing_technical_claims_from_verified_cards():
    from app.services.ai_staged_execution import _materialize_missing_black_box_technical_claims

    rows, fields = _materialize_missing_black_box_technical_claims(
        [{
            "case_id": "BB-01",
            "scenario_name": "公开 Login 验证",
            "technical_claims": [],
        }],
        evidence_cards=[{
            "evidence_id": "SRC-01",
            "file_path": "lib/iscsi/iscsi.c",
            "start_line": 100,
            "end_line": 101,
            "excerpt": "iscsi login verified anchor",
            "symbols": ["iscsi_login"],
        }],
    )

    assert fields == ["$[0].technical_claims[0]"]
    assert rows[0]["technical_claims"][0]["type"] == "source_anchor"
    assert rows[0]["technical_claims"][0]["evidence"][0] == {
        "evidence_id": "SRC-01",
        "path": "lib/iscsi/iscsi.c",
        "lines": "L100-L101",
        "quote": "iscsi login verified anchor",
        "symbol": "iscsi_login",
    }


def test_black_box_stage_prefers_the_row_declared_evidence_card_for_anchor():
    from app.services.ai_staged_execution import _materialize_missing_black_box_technical_claims

    rows, _ = _materialize_missing_black_box_technical_claims(
        [{
            "case_id": "BB-02",
            "source_or_test_evidence": ["SRC-02:L40-L41"],
            "technical_claims": [],
        }],
        evidence_cards=[
            {
                "evidence_id": "SRC-01",
                "file_path": "lib/iscsi/first.c",
                "start_line": 10,
                "end_line": 10,
                "excerpt": "first verified anchor",
                "symbols": ["first"],
            },
            {
                "evidence_id": "SRC-02",
                "file_path": "lib/iscsi/second.c",
                "start_line": 40,
                "end_line": 41,
                "excerpt": "second verified anchor",
                "symbols": ["second"],
            },
        ],
    )

    assert rows[0]["technical_claims"][0]["evidence"][0]["evidence_id"] == "SRC-02"
    assert rows[0]["technical_claims"][0]["evidence"][0]["path"] == "lib/iscsi/second.c"


def test_final_quality_repair_restores_source_anchor_after_removing_bad_black_box_claim(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    (tmp_path / "evidence_cards.json").write_text(json.dumps([{
        "evidence_id": "SRC-02",
        "file_path": "lib/iscsi/login.c",
        "start_line": 40,
        "end_line": 40,
        "excerpt": "return ISCSI_LOGIN_AUTHENT_FAIL;",
        "symbols": ["login_error"],
    }]), encoding="utf-8")
    (tmp_path / "black_box_cases.json").write_text(json.dumps([{
        "case_id": "BB-02",
        "source_or_test_evidence": ["SRC-02:L40"],
        "technical_claims": [],
    }]), encoding="utf-8")

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "row_source_claim_insufficient",
            "row_id": "BB-02",
        }]},
    )

    rows = json.loads((tmp_path / "black_box_cases.json").read_text(encoding="utf-8"))
    assert "black_box_cases.json" in changed
    assert rows[0]["technical_claims"][0]["evidence"][0]["evidence_id"] == "SRC-02"


def test_deterministic_quality_repair_removes_unbound_raw_device_placeholder():
    from app.services.ai_staged_execution import _deterministic_quality_claim_repair

    rows, fields = _deterministic_quality_claim_repair(
        [{
            "case_id": "BC-001",
            "scenario_name": "iSCSI Login 后 I/O 验证",
            "preconditions": ["Login 成功"],
            "expected_result": "出现 /dev/sdX 后执行 I/O",
            "observability": ["lsblk 显示 /dev/sdX"],
        }],
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{
            "artifact": "report.md",
            "code": "professional_fact_conflict",
            "constraint_id": "black_box_raw_device_identity",
            "row_id": "BC-001",
        }]},
    )

    rendered = json.dumps(rows, ensure_ascii=False)
    assert "/dev/sdX" not in rendered
    assert "by-path" in rendered
    assert "序列号" in rendered
    assert "$[0].preconditions" in fields


def test_final_quality_repair_materializes_missing_strategy_execution_order_and_test_evidence(tmp_path):
    from app.services.ai_staged_execution import materialize_final_deterministic_quality_repairs

    artifacts = tmp_path / "agent_runs" / "analyze"
    artifacts.mkdir(parents=True)
    (artifacts / "evidence_cards.json").write_text(json.dumps([{
        "evidence_id": "TEST-01",
        "file_path": "test/iscsi_tgt/chap/chap_common.sh",
        "classification": "test",
        "start_line": 10,
        "end_line": 12,
        "excerpt": "run_test login_chap",
        "sha256": "verified",
    }]), encoding="utf-8")
    target = artifacts / "test_strategy.md"
    target.write_text("# 测试策略\n\n## 范围\n仅保留已有内容。\n", encoding="utf-8")

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "test_strategy.md",
            "code": "missing_markdown_sections",
            "sections": ["执行顺序"],
        }, {
            "artifact": "test_strategy.md",
            "code": "missing_test_evidence",
        }]},
    )

    repaired = target.read_text(encoding="utf-8")
    assert changed["test_strategy.md"] == ["required_sections_and_source_evidence"]
    assert "## 执行顺序" in repaired
    assert "test/iscsi_tgt/chap/chap_common.sh:L10" in repaired


def test_black_box_normalization_promotes_exact_declared_source_line_to_l1_claim():
    from app.services.ai_staged_execution import _normalize_black_box_source_anchor_claims

    rows = [{
        "case_id": "BC-01",
        "source_or_test_evidence": ["lib/iscsi/iscsi.c:1889-1889"],
    }]
    catalog = [{
        "evidence_id": "FLOW-EDGE-008:L1889",
        "path": "lib/iscsi/iscsi.c",
        "lines": "L1889",
        "quote": "rc = iscsi_op_login_session_discovery_chap(conn);",
        "symbol": "iscsi_op_login_phase_none",
    }]

    normalized = _normalize_black_box_source_anchor_claims(rows, catalog)

    claim = normalized[0]["technical_claims"][0]
    assert claim["type"] == "source_anchor"
    assert claim["statement"] == "rc = iscsi_op_login_session_discovery_chap(conn);"
    assert claim["evidence"][0]["evidence_id"] == "FLOW-EDGE-008:L1889"


def test_black_box_normalization_declares_all_retained_claim_evidence():
    rows = [{
        "case_id": "BB-10",
        "source_or_test_evidence": ["lib/iscsi/conn.c:167"],
        "technical_claims": [
            {
                "claim_id": "C-1",
                "type": "source_anchor",
                "statement": "conn->login_timer = register(...);",
                "evidence": [{
                    "evidence_id": "SRC-01:L167",
                    "path": "lib/iscsi/conn.c",
                    "lines": "L167",
                    "quote": "conn->login_timer = register(...);",
                }],
            },
            {
                "claim_id": "C-2",
                "type": "behavior_assertion",
                "statement": "timeout uses the declared protocol constant.",
                "evidence": [{
                    "evidence_id": "SRC-02:L526",
                    "path": "include/spdk/iscsi_spec.h",
                    "lines": "L526",
                    "quote": "#define ISCSI_LOGIN_AUTHENT_FAIL 0x01",
                }],
            },
        ],
    }]

    normalized = _normalize_black_box_source_anchor_claims(rows, [])

    assert any(
        "include/spdk/iscsi_spec.h" in reference
        for reference in normalized[0]["source_or_test_evidence"]
    )


def test_final_quality_repair_materializes_sfmea_deletions_without_tombstones(tmp_path):
    from app.services.ai_staged_execution import (
        materialize_final_deterministic_quality_repairs,
    )

    artifact = tmp_path / "sfmea.json"
    artifact.write_text(json.dumps([
        {"sfmea_id": "SFMEA-001", "failure_mode": "保留风险"},
        {"sfmea_id": "SFMEA-002", "failure_mode": "已证伪风险"},
    ]), encoding="utf-8")

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "sfmea.json",
            "code": "row_source_claim_contradicted",
            "row_id": "SFMEA-002",
        }]},
    )

    assert changed == {"sfmea.json": ["SFMEA-002._delete"]}
    assert json.loads(artifact.read_text(encoding="utf-8")) == [
        {"sfmea_id": "SFMEA-001", "failure_mode": "保留风险"},
    ]


def test_deterministic_stage_repair_preserves_prior_provider_metrics():
    from app.services.ai_staged_execution import (
        _preserve_provider_metrics_for_deterministic_repair,
    )

    result = _preserve_provider_metrics_for_deterministic_repair(
        {
            "stage_id": "sfmea",
            "model": "deterministic",
            "attempt_count": 0,
            "provider_wait_ms": 0.0,
            "output_tokens": 0,
            "total_duration_ms": 5.0,
            "duration_ms": 5.0,
            "finish_reason": "deterministic_claim_repair",
        },
        prior_result={
            "model": "deepseek-v4-pro",
            "attempts": 1,
            "attempt_count": 1,
            "provider_call_count": 1,
            "prompt_characters": 19635,
            "prompt_estimated_tokens": 4908,
            "provider_wait_ms": 4905.3,
            "output_tokens": 321,
            "total_duration_ms": 4964.0,
            "duration_ms": 4964.0,
            "finish_reason": "stop",
        },
        repair_duration_ms=5.0,
    )

    assert result["model"] == "deepseek-v4-pro"
    assert result["attempt_count"] == 1
    assert result["provider_wait_ms"] == 4905.3
    assert result["output_tokens"] == 321
    assert result["prompt_estimated_tokens"] == 4908
    assert result["total_duration_ms"] == 4969.0
    assert result["finish_reason"] == "deterministic_claim_repair"
    assert result["provider_finish_reason"] == "stop"
    assert result["repair_model"] == "deterministic"


def test_deterministic_quality_claim_repair_materializes_missing_mcs_target_setup_case():
    payload = [
        {
            "case_id": "BB-001",
            "scenario_name": "Normal login",
            "technical_claims": [],
            "source_or_test_evidence": ["lib/iscsi/iscsi.c"],
        }
    ]

    repaired, fields = _deterministic_quality_claim_repair(
        payload,
        artifact="black_box_cases.json",
        quality_feedback={
            "issues": [
                {
                    "artifact": "test_design.md",
                    "code": "missing_max_connections_target_setup",
                }
            ]
        },
    )

    assert len(repaired) == 2
    case = repaired[-1]
    assert case["case_id"] == "BBC-MCS-CAPACITY"
    assert "scripts/rpc.py iscsi_set_options -c 1" in " ".join(case["preconditions"])
    assert "non-zero TSIH" in " ".join(case["steps"])
    assert case["mapped_test_dir"].startswith("ai_suggested_unverified:")
    assert "；" not in case["mapped_test_dir"]
    assert "判据来源" in case["oracle_basis"]
    assert "隔离测试设备" in " ".join(case["failure_diagnostics"])
    assert "数据销毁风险" in " ".join(case["failure_diagnostics"])
    assert "$[+].mcs_target_setup_case" in fields
    report = _render_deterministic_combined_report(
        plan={"original_user_request": "SPDK iSCSI Login 测试设计"},
        source_pack={"repo_revision": "abc123", "evidence_cards": []},
        business_flow="Login request -> response",
        sfmea=[],
        black_box_cases=repaired,
    )
    from app.services.test_activity_contract import _audit_combined_execution_contract

    assert not any(
        issue["code"] == "missing_max_connections_target_setup"
        for issue in _audit_combined_execution_contract(report)
    )


def test_deterministic_quality_repair_handles_supported_findings_in_mixed_batch():
    """An unrelated audit finding must not suppress a safe MCS repair."""
    payload = [
        {
            "case_id": "BB-001",
            "scenario_name": "Normal login",
            "technical_claims": [],
            "source_or_test_evidence": ["lib/iscsi/iscsi.c"],
        }
    ]

    repaired, fields = _deterministic_quality_claim_repair(
        payload,
        artifact="black_box_cases.json",
        quality_feedback={
            "issues": [
                {
                    "artifact": "test_design.md",
                    "code": "missing_max_connections_target_setup",
                },
                {
                    "artifact": "black_box_cases.json",
                    "code": "black_box_rpc_observability_ambiguous",
                },
            ]
        },
    )

    assert len(repaired) == 2
    assert "scripts/rpc.py iscsi_set_options -c 1" in " ".join(
        repaired[-1]["preconditions"]
    )
    assert "$[+].mcs_target_setup_case" in fields


def test_deterministic_quality_repair_names_public_rpc_field_for_full_feature():
    repaired, fields = _deterministic_quality_claim_repair(
        [
            {
                "case_id": "BB-001",
                "observability": [
                    "target 端 show_connections RPC 返回 conn_state 为 full_feature_phase"
                ],
            }
        ],
        artifact="black_box_cases.json",
        quality_feedback={
            "issues": [
                {
                    "artifact": "black_box_cases.json",
                    "code": "black_box_rpc_observability_ambiguous",
                }
            ]
        },
    )

    assert repaired[0]["observability"] == [
        "执行 scripts/rpc.py iscsi_get_connections，确认 connections[].login_phase=full_feature_phase"
    ]
    assert fields == ["$[0].observability"]


def test_deterministic_quality_repair_names_public_rpc_field_without_full_feature_hint():
    repaired, fields = _deterministic_quality_claim_repair(
        [{
            "case_id": "BB-010",
            "observability": [
                "scripts/rpc.py iscsi_get_connections shows connection established"
            ],
        }],
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "black_box_rpc_observability_ambiguous",
        }]},
    )

    assert repaired[0]["observability"] == [
        "执行 scripts/rpc.py iscsi_get_connections，确认 connections[].login_phase=full_feature_phase"
    ]
    assert fields == ["$[0].observability"]


def test_deterministic_quality_repair_marks_hazardous_mapping_as_isolated():
    repaired, fields = _deterministic_quality_claim_repair(
        [
            {
                "case_id": "BB-001",
                "mapped_test_dir": "test/iscsi_tgt/multiconnection/multiconnection.sh",
                "preconditions": ["SPDK target is running"],
                "failure_diagnostics": ["retain target logs"],
            }
        ],
        artifact="black_box_cases.json",
        quality_feedback={
            "issues": [
                {
                    "artifact": "black_box_cases.json",
                    "code": "unsafe_hazardous_test_mapping",
                    "row_id": "BB-001",
                }
            ]
        },
    )

    assert "隔离测试设备" in " ".join(repaired[0]["preconditions"])
    assert "数据销毁风险" in " ".join(repaired[0]["failure_diagnostics"])
    assert "$[0].preconditions" in fields
    assert "$[0].failure_diagnostics" in fields


def test_deterministic_quality_repair_materializes_resource_pressure_from_ledger():
    repaired, fields = _deterministic_quality_claim_repair(
        [{"case_id": "BB-001", "risk_ids": [], "technical_claims": []}],
        artifact="black_box_cases.json",
        quality_feedback={
            "issues": [
                {
                    "artifact": "black_box_cases.json",
                    "code": "missing_black_box_dimensions",
                    "dimensions": ["resource_pressure"],
                }
            ]
        },
        sfmea_risk_ledger=[
            {
                "sfmea_id": "SFMEA-001",
                "failure_mode": "Login 超时后连接未释放，残留资源",
            }
        ],
    )

    case = repaired[-1]
    assert case["test_dimension"] == "resource_pressure"
    assert case["risk_ids"] == ["SFMEA-001"]
    assert "$[+].mcs_target_setup_case" in fields


def test_deterministic_quality_repair_materializes_resource_wraparound_case():
    repaired, fields = _deterministic_quality_claim_repair(
        [{"case_id": "BB-001", "risk_ids": [], "technical_claims": []}],
        artifact="black_box_cases.json",
        quality_feedback={
            "issues": [
                {
                    "artifact": "black_box_cases.json",
                    "code": "missing_black_box_dimensions",
                    "dimensions": ["resource_wraparound"],
                }
            ]
        },
        sfmea_risk_ledger=[
            {
                "sfmea_id": "SFMEA-001",
                "failure_mode": "Login 失败后连接资源未释放",
            }
        ],
    )

    case = repaired[-1]
    assert case["test_dimension"] == "resource_wraparound"
    assert case["risk_ids"] == ["SFMEA-001"]
    assert "raw-PDU harness" in " ".join(case["steps"])
    assert "$[+].resource_wraparound_case" in fields


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

    assert 'bhs[16:20] = itt.to_bytes(4, "big")' in report
    assert 'bhs[20:22] = cid.to_bytes(2, "big")' in report
    assert 'bhs[24:28] = cmdsn.to_bytes(4, "big")' in report
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
    assert "命令行选项" in rules
    assert "不得编造性能阈值" in rules
    assert "不得使用‘可能成功或失败’" in rules


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


def test_business_flow_markdown_escapes_source_pipes_inside_tables():
    report = render_business_flow_markdown(
        {
            "analysis_target": "iSCSI login",
            "repo_revision": "abc123",
            "entry_points": [],
            "steps": [],
            "error_flows": [{
                "text": "if ((!disable && !require) || auth_failed)",
                "evidence_id": "ERR-001",
            }],
            "cleanup_flows": [],
            "recovery_flows": [],
            "state_objects": [],
            "state_transitions": [],
            "evidence_gaps": [],
        }
    )

    assert "if ((!disable && !require) \\|\\| auth_failed)" in report


def test_business_flow_markdown_renders_related_test_evidence():
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
            "related_tests": [{
                "evidence_id": "FLOW-TEST-001",
                "file_path": "test/unit/lib/iscsi/iscsi.c/iscsi_ut.c",
                "symbol": "op_login_session_normal_test",
                "start_line": 173,
                "end_line": 191,
            }],
            "evidence_gaps": [],
        }
    )

    assert "## 关联测试证据" in report
    assert "test/unit/lib/iscsi/iscsi.c/iscsi_ut.c" in report
    assert "FLOW-TEST-001" in report


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


def test_flow_outline_selects_verified_normal_path_and_keeps_error_as_supporting_branch():
    outline = build_flow_outline(
        {
            "analysis_target": "iSCSI login: timeout and recovery coverage",
            "repo_revision": "abc123",
            "entry_points": [
                {"evidence_id": "ENTRY-START", "symbol": "iscsi_conn_start", "details": {"classification": "source"}},
                {"evidence_id": "ENTRY-TIMEOUT", "symbol": "login_timeout", "details": {"classification": "source"}},
            ],
            "call_edges": [
                {"evidence_id": "EDGE-01", "from_symbol": "iscsi_conn_start", "to_symbol": "iscsi_conn_sock_cb"},
                {"evidence_id": "EDGE-02", "from_symbol": "iscsi_conn_sock_cb", "to_symbol": "iscsi_handle_incoming_pdus"},
                {"evidence_id": "EDGE-03", "from_symbol": "iscsi_handle_incoming_pdus", "to_symbol": "iscsi_read_pdu"},
                {"evidence_id": "EDGE-04", "from_symbol": "iscsi_read_pdu", "to_symbol": "iscsi_pdu_payload_handle"},
                {"evidence_id": "EDGE-05", "from_symbol": "iscsi_pdu_payload_handle", "to_symbol": "iscsi_pdu_payload_op_login"},
                {"evidence_id": "EDGE-06", "from_symbol": "iscsi_pdu_payload_op_login", "to_symbol": "iscsi_conn_login_pdu_success_complete", "relation": "callback_reference"},
                {"evidence_id": "EDGE-TIMEOUT", "from_symbol": "login_timeout", "to_symbol": "spdk_poller_unregister"},
            ],
        }
    )

    assert len(outline["main_flows"]) == 1
    assert outline["main_flows"][0]["root_symbol"] == "iscsi_conn_start"
    assert outline["main_flows"][0]["steps"][-1]["to_symbol"] == "iscsi_conn_login_pdu_success_complete"
    assert outline["main_flows"][0]["steps"][-1]["action"] == (
        "iscsi_pdu_payload_op_login 传入回调 iscsi_conn_login_pdu_success_complete"
    )
    assert outline["supporting_components"][0]["root_symbol"] == "login_timeout"
    assert not any("不能证明单一端到端" in gap for gap in outline["evidence_gaps"])


def test_flow_outline_selects_verified_target_slice_when_completion_callback_is_not_visible():
    outline = build_flow_outline(
        {
            "analysis_target": "iSCSI Login C-bit 参数重组，从接收 PDU 到参数解析",
            "repo_revision": "abc123",
            "entry_points": [{"evidence_id": "ENTRY", "symbol": "iscsi_conn_start"}],
            "call_edges": [
                {"evidence_id": "EDGE-01", "from_symbol": "iscsi_conn_start", "to_symbol": "iscsi_handle_incoming_pdus"},
                {"evidence_id": "EDGE-02", "from_symbol": "iscsi_handle_incoming_pdus", "to_symbol": "iscsi_read_pdu"},
                {"evidence_id": "EDGE-03", "from_symbol": "iscsi_read_pdu", "to_symbol": "iscsi_pdu_payload_handle"},
                {"evidence_id": "EDGE-04", "from_symbol": "iscsi_pdu_payload_handle", "to_symbol": "iscsi_pdu_payload_op_login"},
                {"evidence_id": "EDGE-05", "from_symbol": "iscsi_pdu_payload_op_login", "to_symbol": "iscsi_op_login_store_incoming_params"},
                {"evidence_id": "EDGE-06", "from_symbol": "iscsi_op_login_store_incoming_params", "to_symbol": "iscsi_parse_params"},
            ],
        }
    )

    assert len(outline["main_flows"]) == 1
    assert "目标范围" in outline["main_flows"][0]["name"]
    assert outline["main_flows"][0]["steps"][-1]["to_symbol"] == "iscsi_parse_params"
    assert not any("不能证明单一端到端" in gap for gap in outline["evidence_gaps"])


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
    assert outline["evidence_gaps"] == []
    assert outline["scope_exclusions"] == [{
        "kind": "unreachable_call_edges",
        "count": 1,
        "reason": "不属于已验证入口可达分量的调用边",
    }]


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


def test_source_analysis_prompt_projection_keeps_delivery_evidence_outside_prompt():
    context = {
        "files": [
            {
                "file_path": f"lib/iscsi/source_{index}.c",
                "classification": "source",
                "matched_terms": ["login"],
                "symbols": [f"source_{index}"],
            }
            for index in range(6)
        ]
        + [
            {
                "file_path": f"test/iscsi/login_{index}.sh",
                "classification": "test",
                "matched_terms": ["login"],
                "symbols": [f"test_{index}"],
            }
            for index in range(4)
        ]
    }

    prompt_context = _source_analysis_prompt_context(context, max_files=6)

    assert len(context["files"]) == 10
    assert len(prompt_context["files"]) == 6
    assert prompt_context["prompt_projection"] == {
        "file_count": 6,
        "full_evidence_file_count": 10,
        "reason": "source_analysis_prompt_budget",
    }


def test_source_analysis_context_keeps_anchor_window_when_function_exceeds_budget(tmp_path):
    source = tmp_path / "lib" / "iscsi" / "login.c"
    source.parent.mkdir(parents=True)
    body = ["static int login_handler(void)", "{"]
    body.extend(f"\tint filler_{index} = {index};" for index in range(120))
    body.extend([
        "\tconn->login_timer = NULL;",
        "\treturn send_login_response(conn);",
        "}",
    ])
    source_text = "\n".join(body) + "\n"
    source.write_text(source_text, encoding="utf-8")
    timer_line = next(
        index + 1 for index, line in enumerate(body) if "login_timer = NULL" in line
    )
    context = {
        "repo_path": str(tmp_path),
        "source_context": {
            "repo_path": str(tmp_path),
            "files": [{
                "file_path": "lib/iscsi/login.c",
                "classification": "source",
                "start_line": timer_line,
                "end_line": timer_line,
                "excerpt": "conn->login_timer = NULL;",
                "symbols": ["login_handler"],
                "matched_terms": ["login"],
                "sha256": hashlib.sha256(source_text.encode()).hexdigest(),
            }],
        },
    }

    compact = build_source_analysis_context(
        plan={"original_user_request": "分析 iSCSI login", "target": "iSCSI login"},
        staged_context=context,
        max_files=1,
        excerpt_chars=300,
        max_evidence_anchors=1,
    )

    excerpt = compact["files"][0]["excerpt"]
    assert len(excerpt) <= 300
    assert "conn->login_timer = NULL;" in excerpt
    assert "send_login_response(conn);" in excerpt


def test_source_analysis_context_adds_verified_anchors_within_selected_files(
    tmp_path,
):
    source = tmp_path / "libnvme" / "src" / "nvme" / "fabrics.c"
    source.parent.mkdir(parents=True)
    source_text = (
        "static int connect_ctrl(void) { return submit_connect(); }\n"
        + "\n" * 80
        + "static int load_tls_psk(void) { return keyring_search(); }\n"
        + "\n" * 80
        + "static void rollback_ctrl(void) { release_controller(); }\n"
    )
    source.write_text(source_text, encoding="utf-8")
    test_file = tmp_path / "libnvme" / "test" / "fabrics.c"
    test_file.parent.mkdir(parents=True)
    test_text = "int test_reconnect(void) { return verify_recovery(); }\n"
    test_file.write_text(test_text, encoding="utf-8")
    staged_context = {
        "repo_path": str(tmp_path),
        "source_context": {
            "repo_path": str(tmp_path),
            "repo_revision": "fixture",
            "tokens": [
                "connect",
                "tls",
                "psk",
                "keyring",
                "rollback",
                "controller",
                "reconnect",
            ],
            "files": [
                {
                    "file_path": "libnvme/src/nvme/fabrics.c",
                    "classification": "source",
                    "start_line": 1,
                    "end_line": 1,
                    "excerpt": source_text.splitlines()[0],
                    "symbols": ["connect_ctrl"],
                    "matched_terms": ["connect", "tls", "psk", "keyring", "rollback"],
                    "sha256": hashlib.sha256(source_text.encode()).hexdigest(),
                    "status": "validated_source_file",
                },
                {
                    "file_path": "libnvme/test/fabrics.c",
                    "classification": "test",
                    "start_line": 1,
                    "end_line": 1,
                    "excerpt": test_text.strip(),
                    "symbols": ["test_reconnect"],
                    "matched_terms": ["reconnect"],
                    "sha256": hashlib.sha256(test_text.encode()).hexdigest(),
                    "status": "validated_source_file",
                },
            ],
        },
    }

    compact = build_source_analysis_context(
        plan={"original_user_request": "分析 connect TLS PSK rollback reconnect"},
        staged_context=staged_context,
        max_files=2,
        excerpt_chars=500,
        max_evidence_anchors=4,
    )

    assert len({item["file_path"] for item in compact["files"]}) == 2
    assert len(compact["files"]) == 4
    assert any("load_tls_psk" in item["excerpt"] for item in compact["files"])
    assert any("rollback_ctrl" in item["excerpt"] for item in compact["files"])
    assert all(item["sha256"] for item in compact["files"])


def test_source_analysis_context_prefers_error_branch_over_help_text(tmp_path):
    source = tmp_path / "libnvme" / "src" / "nvme" / "fabrics.c"
    source.parent.mkdir(parents=True)
    source_text = "\n".join(
        [
            "static int connect_ctrl(void) { return submit_connect(); }",
            *([""] * 20),
            'const char *help = "reconnect delay option";',
            *([""] * 50),
            "static int reconnect_ctrl(int fd) {",
            "    int ret = write(fd, \"reconnect\", 9);",
            "    if (ret != 9) {",
            "        close(fd);",
            "        return -EIO;",
            "    }",
            "    return 0;",
            "}",
        ]
    )
    source.write_text(source_text, encoding="utf-8")
    staged_context = {
        "repo_path": str(tmp_path),
        "source_context": {
            "repo_path": str(tmp_path),
            "repo_revision": "fixture",
            "tokens": ["reconnect"],
            "files": [
                {
                    "file_path": "libnvme/src/nvme/fabrics.c",
                    "classification": "source",
                    "start_line": 1,
                    "end_line": 1,
                    "excerpt": source_text.splitlines()[0],
                    "symbols": ["connect_ctrl"],
                    "matched_terms": ["reconnect"],
                    "sha256": hashlib.sha256(source_text.encode()).hexdigest(),
                    "status": "validated_source_file",
                }
            ],
        },
    }

    compact = build_source_analysis_context(
        plan={"original_user_request": "分析 reconnect 错误恢复"},
        staged_context=staged_context,
        max_files=1,
        excerpt_chars=500,
        max_evidence_anchors=2,
    )

    assert len(compact["files"]) == 2
    assert "if (ret != 9)" in compact["files"][1]["excerpt"]
    assert "close(fd)" in compact["files"][1]["excerpt"]


def test_compact_source_selector_upgrades_low_score_test_evidence():
    candidates = [
        {
            "file_path": "src/fabrics.c",
            "classification": "source",
            "score": 100,
            "matched_terms": ["connect", "tls"],
            "symbols": ["connect_ctrl"],
        },
        {
            "file_path": "test/rare.c",
            "classification": "test",
            "score": 1,
            "matched_terms": ["rare"],
            "symbols": ["test_rare"],
        },
        {
            "file_path": "test/psk.c",
            "classification": "test",
            "score": 20,
            "matched_terms": ["tls"],
            "symbols": ["test_psk"],
        },
    ]

    selected = _select_bounded_source_context_files(
        candidates,
        limit=2,
        min_source_files=1,
        min_test_files=1,
        coverage_tokens=["connect", "tls", "rare"],
    )

    assert {item["file_path"] for item in selected} == {
        "src/fabrics.c",
        "test/psk.c",
    }


def test_source_analysis_context_prefers_risk_branch_over_term_rich_declaration(
    tmp_path,
):
    source = tmp_path / "fabrics.c"
    source_text = "\n".join(
        [
            "static int seed_entry(void) { return 0; }",
            *( [""] * 20 ),
            "static int describe_tls_keyring_reconnect_timeout(void)",
            "{",
            "    return 0;",
            "}",
            *( [""] * 15 ),
            "static int write_zeroes(int fd)",
            "{",
            "    int retry = read(fd, NULL, 0);",
            "    if (retry < 0) {",
            "        close(fd);",
            "        return -EIO;",
            "    }",
            "    return retry;",
            "}",
            *( [""] * 30 ),
            "static int reconnect_ctrl(int fd)",
            "{",
            '    int ret = write(fd, "reconnect", 9);',
            "    if (ret != 9) {",
            "        close(fd);",
            "        return -EIO;",
            "    }",
            "    return 0;",
            "}",
        ]
    )
    source.write_text(source_text, encoding="utf-8")
    staged_context = {
        "repo_path": str(tmp_path),
        "source_context": {
            "repo_path": str(tmp_path),
            "repo_revision": "fixture",
            "tokens": ["tls", "keyring", "reconnect", "timeout", "retry"],
            "files": [
                {
                    "file_path": "fabrics.c",
                    "classification": "source",
                    "start_line": 1,
                    "end_line": 1,
                    "excerpt": source_text.splitlines()[0],
                    "symbols": ["seed_entry"],
                    "matched_terms": ["seed"],
                    "sha256": hashlib.sha256(source_text.encode()).hexdigest(),
                    "status": "validated_source_file",
                }
            ],
        },
    }

    compact = build_source_analysis_context(
        plan={"original_user_request": "分析 TLS keyring reconnect timeout retry"},
        staged_context=staged_context,
        max_files=1,
        excerpt_chars=500,
        max_evidence_anchors=2,
    )

    assert compact["files"][1]["symbols"] == ["reconnect_ctrl"]
    assert "if (ret != 9)" in compact["files"][1]["excerpt"]
    assert "close(fd)" in compact["files"][1]["excerpt"]


def test_source_analysis_expansion_prefers_complex_core_discovery_function(
    tmp_path,
):
    source = tmp_path / "fabrics.c"
    source_text = "\n".join(
        [
            "static int seed(void) { return 0; }",
            *( [""] * 20 ),
            "static int libnvmf_discovery_nbft(void)",
            "{",
            "    int ret = discovery_from_table();",
            "    if (ret < 0) return ret;",
            "    if (ret == 1) return retry_discovery();",
            "    if (ret == 2) return cleanup_discovery();",
            "    if (ret == 3) return reconnect_discovery();",
            "    if (ret == 4) return fail_discovery();",
            "    return ret;",
            "}",
            *( [""] * 20 ),
            "static int _nvmf_discovery(void)",
            "{",
            "    int ret = read_discovery_log();",
            "    if (ret < 0)",
            "        return ret;",
            "    for (int i = 0; i < 8; i++) {",
            "        ret = nvmf_connect_disc_entry(i);",
            "        if (ret < 0) {",
            "            cleanup_controller(i);",
            "            continue;",
            "        }",
            "    }",
            "    return 0;",
            "}",
        ]
    )
    source.write_text(source_text, encoding="utf-8")
    staged_context = {
        "repo_path": str(tmp_path),
        "source_context": {
            "repo_path": str(tmp_path),
            "repo_revision": "fixture",
            "tokens": ["discovery", "connect", "cleanup"],
            "files": [
                {
                    "file_path": "fabrics.c",
                    "classification": "source",
                    "start_line": 1,
                    "end_line": 1,
                    "excerpt": source_text.splitlines()[0],
                    "symbols": ["seed"],
                    "matched_terms": [],
                    "sha256": hashlib.sha256(source_text.encode()).hexdigest(),
                    "status": "validated_source_file",
                }
            ],
        },
    }

    compact = build_source_analysis_context(
        plan={"original_user_request": "分析 discovery connect cleanup"},
        staged_context=staged_context,
        max_files=1,
        excerpt_chars=800,
        max_evidence_anchors=2,
        min_test_files=0,
    )

    assert compact["files"][1]["symbols"][0] == "_nvmf_discovery"


def test_source_symbol_relevance_uses_identifier_boundaries():
    from app.services.ai_staged_execution import _source_symbol_matches_token

    assert _source_symbol_matches_token("nvmf_connect_disc_entry", "connect")
    assert _source_symbol_matches_token("_nvmf_discovery", "discovery")
    assert not _source_symbol_matches_token("derive_retained_key", "ret")
    assert not _source_symbol_matches_token("getrandom_bytes", "err")


def test_source_analysis_expansion_follows_calls_between_selected_functions(
    tmp_path,
):
    source = tmp_path / "fabrics.c"
    source_text = "\n".join(
        [
            "static int seed(void) { return 0; }",
            *( [""] * 10 ),
            "static int nvmf_connect_disc_entry(int entry)",
            "{",
            "    if (entry < 0) return -1;",
            "    return connect_entry(entry);",
            "}",
            *( [""] * 10 ),
            "static int nvmf_update_tls_concat(void)",
            "{",
            "    if (tls_keyring_error()) return cleanup_tls();",
            "    if (tls_keyring_error()) return retry_tls_connect();",
            "    if (tls_keyring_error()) return cleanup_tls();",
            "    if (tls_keyring_error()) return retry_tls_connect();",
            "    return retry_tls_connect();",
            "}",
            *( [""] * 10 ),
            "static int _nvmf_discovery(void)",
            "{",
            "    int child = nvmf_connect_disc_entry(1);",
            "    if (child < 0) continue_discovery();",
            "    return child;",
            "}",
        ]
    )
    source.write_text(source_text, encoding="utf-8")
    staged_context = {
        "repo_path": str(tmp_path),
        "source_context": {
            "repo_path": str(tmp_path),
            "repo_revision": "fixture",
            "tokens": [
                "nvmf", "discovery", "connect", "child", "tls",
                "keyring", "error", "cleanup", "retry",
            ],
            "files": [
                {
                    "file_path": "fabrics.c",
                    "classification": "source",
                    "start_line": 1,
                    "end_line": 1,
                    "excerpt": source_text.splitlines()[0],
                    "symbols": ["seed"],
                    "matched_terms": [],
                    "sha256": hashlib.sha256(source_text.encode()).hexdigest(),
                    "status": "validated_source_file",
                    "content_match_count": 20,
                }
            ],
        },
    }

    compact = build_source_analysis_context(
        plan={"original_user_request": "分析 discovery 子任务 connect 和 TLS"},
        staged_context=staged_context,
        max_files=1,
        excerpt_chars=800,
        max_evidence_anchors=3,
        min_test_files=0,
    )

    assert [item["symbols"][0] for item in compact["files"][1:]] == [
        "_nvmf_discovery",
        "nvmf_connect_disc_entry",
    ]


def test_source_analysis_context_additional_slice_keeps_enclosing_multiline_c_symbol(
    tmp_path,
):
    source = tmp_path / "libnvme" / "src" / "nvme" / "fabrics.c"
    source.parent.mkdir(parents=True)
    source_text = "\n".join(
        [
            "static int seed_entry(void) { return 0; }",
            *( [""] * 30 ),
            "static int libnvme_add_ctrl(struct libnvmf_context *fctx,",
            "        struct libnvme_host *host, struct libnvme_ctrl *ctrl)",
            "{",
            "    int err;",
            "retry:",
            "    err = libnvmf_add_ctrl(host, ctrl);",
            "    if (err && fctx->retry(err))",
            "        goto retry;",
            "    return err;",
            "}",
        ]
    )
    source.write_text(source_text, encoding="utf-8")
    staged_context = {
        "repo_path": str(tmp_path),
        "source_context": {
            "repo_path": str(tmp_path),
            "repo_revision": "fixture",
            "tokens": ["retry"],
            "files": [
                {
                    "file_path": "libnvme/src/nvme/fabrics.c",
                    "classification": "source",
                    "start_line": 1,
                    "end_line": 1,
                    "excerpt": source_text.splitlines()[0],
                    "symbols": ["seed_entry"],
                    "matched_terms": ["retry"],
                    "sha256": hashlib.sha256(source_text.encode()).hexdigest(),
                    "status": "validated_source_file",
                }
            ],
        },
    }

    compact = build_source_analysis_context(
        plan={"original_user_request": "分析 retry 错误恢复"},
        staged_context=staged_context,
        max_files=1,
        excerpt_chars=500,
        max_evidence_anchors=2,
    )

    assert compact["files"][1]["symbols"] == ["libnvme_add_ctrl"]


def test_source_symbol_detection_accepts_token_on_multiline_signature():
    source_text = "\n".join(
        [
            "static bool hook_decide_retry(struct context *ctx, int err,",
            "        void *user_data)",
            "{",
            "    return err == -EAGAIN;",
            "}",
        ]
    )

    assert (
        _source_enclosing_c_function(source_text, anchor_line=1)
        == "hook_decide_retry"
    )


def test_source_analysis_context_completes_small_c_function_instead_of_cutting_branch(
    tmp_path,
):
    source = tmp_path / "libnvme" / "src" / "nvme" / "tree-fabrics.c"
    source.parent.mkdir(parents=True)
    source_text = "\n".join(
        [
            "static void read_dhchap(struct ctrl *ctrl)",
            "{",
            "    char *ctrl_key;",
            "",
            '    ctrl_key = get_attr(ctrl, "dhchap_ctrl_secret");',
            '    if (ctrl_key && !strcmp(ctrl_key, "none")) {',
            "        free(ctrl_key);",
            "        ctrl_key = NULL;",
            "    }",
            "    if (ctrl_key)",
            "        set_key(ctrl, ctrl_key);",
            "}",
        ]
    )
    source.write_text(source_text, encoding="utf-8")
    staged_context = {
        "repo_path": str(tmp_path),
        "source_context": {
            "repo_path": str(tmp_path),
            "repo_revision": "fixture",
            "tokens": ["dhchap", "ctrl_key"],
            "files": [
                {
                    "file_path": "libnvme/src/nvme/tree-fabrics.c",
                    "classification": "source",
                    "start_line": 1,
                    "end_line": 7,
                    "excerpt": "\n".join(source_text.splitlines()[:7]),
                    "symbols": ["read_dhchap"],
                    "matched_terms": ["dhchap", "ctrl_key"],
                    "sha256": hashlib.sha256(source_text.encode()).hexdigest(),
                    "status": "validated_source_file",
                }
            ],
        },
    }

    compact = build_source_analysis_context(
        plan={"original_user_request": "分析 dhchap ctrl_key 生命周期"},
        staged_context=staged_context,
        max_files=1,
        excerpt_chars=800,
        max_evidence_anchors=1,
    )

    assert compact["files"][0]["start_line"] == 1
    assert compact["files"][0]["end_line"] == 12
    assert "ctrl_key = NULL;" in compact["files"][0]["excerpt"]
    assert "set_key(ctrl, ctrl_key);" in compact["files"][0]["excerpt"]


def test_source_analysis_context_preserves_protocol_semantic_anchors_after_token_cap(
    tmp_path,
):
    subsystem = tmp_path / "lib" / "iscsi" / "iscsi_subsystem.c"
    subsystem.parent.mkdir(parents=True)
    subsystem_text = "\n".join(
        [
            "static int inspect_tsih(uint16_t tsih)",
            "{",
            "    return tsih == 0;",
            "}",
            *([""] * 24),
            "static void allocate_session_tsih(struct session *sess, int index)",
            "{",
            "    /* tsih 0 is reserved, so start tsih values at 1. */",
            "    sess->tsih = index + 1;",
            "}",
        ]
    )
    subsystem.write_text(subsystem_text, encoding="utf-8")
    login = tmp_path / "lib" / "iscsi" / "iscsi.c"
    login_text = "\n".join(
        [
            "static int inspect_login_target(const char *target)",
            "{",
            "    return target == NULL;",
            "}",
            *([""] * 24),
            "static void iscsi_op_login_rsp_init(struct response *rsph)",
            "{",
            "    rsph->status_detail = ISCSI_LOGIN_TARGET_REMOVED;",
            "}",
        ]
    )
    login.write_text(login_text, encoding="utf-8")
    test_file = tmp_path / "test" / "iscsi" / "login.c"
    test_file.parent.mkdir(parents=True)
    test_text = "int test_login(void) { return 0; }\n"
    test_file.write_text(test_text, encoding="utf-8")
    noise = [f"noise_{index}" for index in range(32)]
    staged_context = {
        "repo_path": str(tmp_path),
        "source_context": {
            "repo_path": str(tmp_path),
            "repo_revision": "fixture",
            "tokens": [*noise, "target", "login", "tsih"],
            "files": [
                {
                    "file_path": "lib/iscsi/iscsi_subsystem.c",
                    "classification": "source",
                    "start_line": 1,
                    "end_line": 4,
                    "excerpt": "\n".join(subsystem_text.splitlines()[:4]),
                    "symbols": ["inspect_tsih"],
                    "matched_terms": ["tsih"],
                    "score": 100,
                    "sha256": hashlib.sha256(subsystem_text.encode()).hexdigest(),
                    "status": "validated_source_file",
                },
                {
                    "file_path": "lib/iscsi/iscsi.c",
                    "classification": "source",
                    "start_line": 1,
                    "end_line": 4,
                    "excerpt": "\n".join(login_text.splitlines()[:4]),
                    "symbols": ["inspect_login_target"],
                    "matched_terms": ["target", "login"],
                    "score": 90,
                    "sha256": hashlib.sha256(login_text.encode()).hexdigest(),
                    "status": "validated_source_file",
                },
                {
                    "file_path": "test/iscsi/login.c",
                    "classification": "test",
                    "start_line": 1,
                    "end_line": 1,
                    "excerpt": test_text.strip(),
                    "symbols": ["test_login"],
                    "matched_terms": ["login"],
                    "score": 1,
                    "sha256": hashlib.sha256(test_text.encode()).hexdigest(),
                    "status": "validated_source_file",
                },
            ],
        },
    }

    compact = build_source_analysis_context(
        plan={"original_user_request": "分析 iSCSI login target TSIH 恢复"},
        staged_context=staged_context,
        max_files=3,
        excerpt_chars=500,
        max_evidence_anchors=5,
    )

    excerpts = "\n".join(str(item["excerpt"]) for item in compact["files"])
    assert "sess->tsih = index + 1;" in excerpts
    assert "ISCSI_LOGIN_TARGET_REMOVED" in excerpts


def test_source_analysis_context_keeps_login_cbit_parameter_assembly_anchor(
    tmp_path,
):
    source = tmp_path / "lib" / "iscsi" / "iscsi.c"
    source.parent.mkdir(parents=True)
    noisy_login_functions = []
    for index in range(6):
        noisy_login_functions.extend(
            [
                f"static int iscsi_login_noise_{index}(int rc)",
                "{",
                "    if (rc < 0) {",
                "        return -EINVAL;",
                "    }",
                "    return rc;",
                "}",
                "",
            ]
        )
    source_text = "\n".join(
        [
            "static int iscsi_login_response(void)",
            "{",
            "    return 0;",
            "}",
            "",
            *noisy_login_functions,
            "static int iscsi_op_login_store_incoming_params(struct request *req)",
            "{",
            "    return iscsi_parse_params(&req->params, req->data, req->length,",
            "        ISCSI_BHS_LOGIN_GET_CBIT(req->flags), &req->partial_parameter);",
            "}",
        ]
    )
    source.write_text(source_text, encoding="utf-8")
    staged_context = {
        "repo_path": str(tmp_path),
        "source_context": {
            "repo_path": str(tmp_path),
            "repo_revision": "fixture",
            "tokens": ["login"],
            "files": [
                {
                    "file_path": "lib/iscsi/iscsi.c",
                    "classification": "source",
                    "start_line": 1,
                    "end_line": 4,
                    "excerpt": "\n".join(source_text.splitlines()[:4]),
                    "symbols": ["iscsi_login_response"],
                    "matched_terms": ["login"],
                    "sha256": hashlib.sha256(source_text.encode()).hexdigest(),
                    "status": "validated_source_file",
                }
            ],
        },
    }

    compact = build_source_analysis_context(
        plan={"original_user_request": "分析 iSCSI Login 参数协商"},
        staged_context=staged_context,
        max_files=1,
        excerpt_chars=500,
        max_evidence_anchors=2,
    )

    excerpts = "\n".join(str(item["excerpt"]) for item in compact["files"])
    assert "ISCSI_BHS_LOGIN_GET_CBIT" in excerpts
    assert "iscsi_parse_params" in excerpts


def test_source_analysis_context_reserves_cbit_anchor_amid_login_helper_noise(tmp_path):
    """C-bit evidence must survive a compact iSCSI Login evidence budget."""
    source = tmp_path / "lib" / "iscsi" / "iscsi.c"
    source.parent.mkdir(parents=True)
    helpers = []
    for name in ("target", "timeout", "response", "session", "digest", "auth"):
        helpers.extend([
            f"static int iscsi_login_{name}(int rc)",
            "{",
            "    if (rc < 0) return rc;",
            "    return 0;",
            "}",
            "",
        ])
    source_text = "\n".join([
        "static int iscsi_login_entry(void) { return 0; }",
        "",
        *helpers,
        "static int iscsi_op_login_store_incoming_params(struct request *req)",
        "{",
        "    return iscsi_parse_params(&req->params, req->data, req->length,",
        "        ISCSI_BHS_LOGIN_GET_CBIT(req->flags), &req->partial_parameter);",
        "}",
    ])
    source.write_text(source_text, encoding="utf-8")
    staged_context = {
        "repo_path": str(tmp_path),
        "source_context": {
            "repo_path": str(tmp_path),
            "repo_revision": "fixture",
            "tokens": ["login", "target", "timeout", "digest", "auth"],
            "files": [{
                "file_path": "lib/iscsi/iscsi.c",
                "classification": "source",
                "start_line": 1,
                "end_line": 1,
                "excerpt": "static int iscsi_login_entry(void) { return 0; }",
                "symbols": ["iscsi_login_entry"],
                "matched_terms": ["login"],
                "sha256": hashlib.sha256(source_text.encode()).hexdigest(),
                "status": "validated_source_file",
            }],
        },
    }

    compact = build_source_analysis_context(
        plan={"original_user_request": "分析 iSCSI Login 认证和超时"},
        staged_context=staged_context,
        max_files=1,
        excerpt_chars=500,
        max_evidence_anchors=3,
    )

    excerpts = "\n".join(str(item["excerpt"]) for item in compact["files"])
    assert "ISCSI_BHS_LOGIN_GET_CBIT" in excerpts


def test_source_analysis_context_reserves_iscsi_login_semantic_anchors(tmp_path):
    source = tmp_path / "lib" / "iscsi" / "iscsi.c"
    source.parent.mkdir(parents=True)
    source_text = "\n".join([
        "static int iscsi_login_entry(void) { return 0; }",
        "",
        *([""] * 20),
        "static int iscsi_auth_params(struct conn *conn)",
        "{",
        "    return conn != NULL ? 0 : -1;",
        "}",
        "",
        *([""] * 20),
        "static int iscsi_op_login_phase_none(struct conn *conn)",
        "{",
        "    return iscsi_auth_params(conn);",
        "}",
        "",
        *([""] * 20),
        "static void iscsi_op_login_response(struct conn *conn)",
        "{",
        "    conn->status = 0;",
        "}",
        "",
        *([""] * 20),
        "static int iscsi_op_login_rsp_handle_csg_bit(struct conn *conn)",
        "{",
        "    return iscsi_op_login_phase_none(conn);",
        "}",
        "",
        *([""] * 20),
        "static int iscsi_op_login_store_incoming_params(struct conn *conn)",
        "{",
        "    return iscsi_parse_params(&conn->params, NULL, 0,",
        "        ISCSI_BHS_LOGIN_GET_CBIT(conn->flags), &conn->partial_parameter);",
        "}",
        "",
        *( [""] * 20 ),
        "static void iscsi_pdu_payload_op_login(struct conn *conn)",
        "{",
        *(f"    int filler_{index} = {index};" for index in range(80)),
        "    spdk_poller_unregister(&conn->login_timer);",
        "    iscsi_op_login_response(conn);",
        "}",
    ])
    source.write_text(source_text, encoding="utf-8")
    staged_context = {
        "repo_path": str(tmp_path),
        "source_context": {
            "repo_path": str(tmp_path),
            "repo_revision": "fixture",
            "tokens": ["iscsi", "login"],
            "files": [{
                "file_path": "lib/iscsi/iscsi.c",
                "classification": "source",
                "start_line": 1,
                "end_line": 1,
                "excerpt": "static int iscsi_login_entry(void) { return 0; }",
                "symbols": ["iscsi_login_entry"],
                "matched_terms": ["iscsi", "login"],
                "sha256": hashlib.sha256(source_text.encode()).hexdigest(),
                "status": "validated_source_file",
            }],
        },
    }

    compact = build_source_analysis_context(
        plan={"original_user_request": "完整 iSCSI Login CHAP 状态机分析"},
        staged_context=staged_context,
        max_files=1,
        excerpt_chars=500,
        max_evidence_anchors=7,
    )

    symbols = {
        symbol
        for item in compact["files"]
        for symbol in item.get("symbols") or []
    }
    assert {
        "iscsi_auth_params",
        "iscsi_op_login_phase_none",
        "iscsi_op_login_response",
        "iscsi_op_login_rsp_handle_csg_bit",
        "iscsi_pdu_payload_op_login",
    } <= symbols
    payload_excerpt = next(
        str(item.get("excerpt") or "")
        for item in compact["files"]
        if "iscsi_pdu_payload_op_login" in (item.get("symbols") or [])
    )
    assert "spdk_poller_unregister(&conn->login_timer);" in payload_excerpt


def test_source_analysis_context_does_not_fill_anchor_budget_with_help_text(
    tmp_path,
):
    source = tmp_path / "fabrics.c"
    source_text = "\n".join(
        [
            "static int connect_ctrl(void) { return submit_connect(); }",
            *( [""] * 20 ),
            'static const char *help = "tls keyring reconnect timeout option";',
            *( [""] * 30 ),
            "static int reconnect_ctrl(int fd)",
            "{",
            "    if (write(fd, \"retry\", 5) < 0) {",
            "        close(fd);",
            "        return -EIO;",
            "    }",
            "    return 0;",
            "}",
        ]
    )
    source.write_text(source_text, encoding="utf-8")
    staged_context = {
        "repo_path": str(tmp_path),
        "source_context": {
            "repo_path": str(tmp_path),
            "repo_revision": "fixture",
            "tokens": ["tls", "keyring", "reconnect", "timeout", "retry"],
            "files": [
                {
                    "file_path": "fabrics.c",
                    "classification": "source",
                    "start_line": 1,
                    "end_line": 1,
                    "excerpt": source_text.splitlines()[0],
                    "symbols": ["connect_ctrl"],
                    "matched_terms": ["connect"],
                    "sha256": hashlib.sha256(source_text.encode()).hexdigest(),
                    "status": "validated_source_file",
                }
            ],
        },
    }

    compact = build_source_analysis_context(
        plan={"original_user_request": "分析 TLS keyring reconnect timeout retry"},
        staged_context=staged_context,
        max_files=1,
        excerpt_chars=500,
        max_evidence_anchors=2,
    )

    assert len(compact["files"]) == 2
    assert compact["files"][1]["symbols"] == ["reconnect_ctrl"]
    assert "close(fd)" in compact["files"][1]["excerpt"]
    assert "const char *help" not in compact["files"][1]["excerpt"]


def test_markdown_canonicalizes_unique_verified_source_basename():
    content = (
        "`tree-fabrics.c:308-325` 读取密钥；"
        "`libnvme/src/nvme/tree-fabrics.c:319` 已是完整路径；"
        "`tree-fabrics.c:181` 是测试证据；"
        "`fabrics.c:10` 存在重名，不应猜测。"
    )
    source_pack = {
        "evidence_cards": [
            {
                "file_path": "libnvme/src/nvme/tree-fabrics.c",
                "start_line": 308,
                "end_line": 325,
            },
            {
                "file_path": "libnvme/test/tree-fabrics.c",
                "start_line": 178,
                "end_line": 195,
            },
            {"file_path": "libnvme/src/nvme/fabrics.c"},
            {"file_path": "fabrics.c"},
        ]
    }

    normalized = _canonicalize_verified_repo_path_mentions(content, source_pack)

    assert "`libnvme/src/nvme/tree-fabrics.c:308-325`" in normalized
    assert normalized.count("libnvme/src/nvme/tree-fabrics.c") == 2
    assert "`libnvme/test/tree-fabrics.c:181`" in normalized
    assert "`fabrics.c:10`" in normalized


def test_structured_output_removes_unverified_bare_source_filename():
    result = _sanitize_structured_repo_path_mentions(
        {"failure_diagnostics": ["check iscsi_conn.c then lib/iscsi/iscsi.c"]},
        {"evidence_cards": [{"file_path": "lib/iscsi/iscsi.c"}]},
    )
    assert result["failure_diagnostics"] == ["check 已验证源码片段 then lib/iscsi/iscsi.c"]


def test_black_box_anchor_is_declared_as_row_evidence():
    rows = [{
        "source_or_test_evidence": ["test/iscsi_tgt/login.sh (TEST-01)"],
        "technical_claims": [{
            "claim_id": "TC-1",
            "evidence": [{"evidence_id": "SRC-01:L10", "path": "lib/iscsi/iscsi.c", "quote": "return 0;"}],
        }],
    }]
    result = _normalize_black_box_source_anchor_claims(rows)
    assert result[0]["technical_claims"][0]["type"] == "source_anchor"
    assert "lib/iscsi/iscsi.c (SRC-01:L10)" in result[0]["source_or_test_evidence"]


def test_black_box_delivery_contract_replaces_unbound_symbol_references_with_claim_anchors():
    rendered, fields = _normalize_black_box_delivery_contract(
        [{
            "case_id": "BB-ANCHOR-01",
            "source_or_test_evidence": [
                "lib/iscsi/iscsi.c:iscsi_conn_login_pdu_success_complete",
                "include/spdk/iscsi_spec.h:ISCSI_LOGIN_ACCEPT",
            ],
            "technical_claims": [{
                "type": "source_anchor",
                "evidence": [{
                    "evidence_id": "SRC-03:L1125",
                    "path": "lib/iscsi/iscsi.c",
                    "lines": "L1125",
                    "quote": "iscsi_conn_login_pdu_success_complete(void *arg)",
                }],
            }],
        }]
    )

    assert rendered[0]["source_or_test_evidence"] == ["SRC-03:L1125"]
    assert fields == ["$[0].source_or_test_evidence"]


def test_technical_claim_normalization_preserves_behavior_claim_beside_l1_anchor():
    from app.services.ai_staged_execution import (
        _canonicalize_technical_claim_evidence,
        _normalize_black_box_source_anchor_claims,
    )

    anchor = {
        "evidence_id": "SRC-01:L10",
        "path": "lib/iscsi/iscsi.c",
        "lines": "L10",
        "symbol": "iscsi_auth_params",
        "quote": "return iscsi_auth_params(conn);",
    }
    rows = [{
        "case_id": "BB-01",
        "source_or_test_evidence": ["SRC-01:L10"],
        "technical_claims": [
            {
                "claim_id": "TC-BB-01-SOURCE",
                "type": "source_anchor",
                "statement": anchor["quote"],
                "evidence": [anchor],
            },
            {
                "claim_id": "TC-BB-01-BEHAVIOR",
                "type": "behavior_assertion",
                "statement": "认证失败会返回可观察的 Login Response。",
                "evidence": [anchor],
            },
        ],
    }]

    rendered = _canonicalize_technical_claim_evidence(rows, [anchor])
    rendered = _normalize_black_box_source_anchor_claims(rendered, [anchor])

    assert [claim["type"] for claim in rendered[0]["technical_claims"]] == [
        "source_anchor",
        "behavior_assertion",
    ]
    assert rendered[0]["technical_claims"][1]["statement"] == (
        "认证失败会返回可观察的 Login Response。"
    )


def test_markdown_canonicalizes_stale_prefix_to_unique_verified_repo_path():
    content = "`src/fabrics.c:1567-1585` handles connect cleanup."
    source_pack = {
        "evidence_cards": [
            {
                "file_path": "libnvme/src/nvme/fabrics.c",
                "start_line": 1567,
                "end_line": 1585,
            }
        ]
    }

    normalized = _canonicalize_verified_repo_path_mentions(content, source_pack)

    assert "`libnvme/src/nvme/fabrics.c:1567-1585`" in normalized
    assert "`src/fabrics.c" not in normalized


def test_markdown_canonicalizes_stale_prefix_to_closest_verified_repo_path():
    content = "分析目标提到 src/fabrics.c，源码入口仍以实际仓库为准。"
    source_pack = {
        "evidence_cards": [
            {"file_path": "fabrics.c", "start_line": 664, "end_line": 681},
            {
                "file_path": "libnvme/src/nvme/fabrics.c",
                "start_line": 1567,
                "end_line": 1585,
            },
        ]
    }

    normalized = _canonicalize_verified_repo_path_mentions(content, source_pack)

    assert "src/fabrics.c" not in normalized
    assert "分析目标提到 fabrics.c" in normalized


def test_markdown_replaces_invalid_prefixed_path_with_unique_verified_path_when_line_is_unverified():
    content = "参考 lib/iscsi/iscsi.c/iscsi_ut.c:173-191 的 Login 单元测试。"
    source_pack = {
        "evidence_cards": [{
            "file_path": "test/unit/lib/iscsi/iscsi.c/iscsi_ut.c",
            "start_line": 174,
            "end_line": 220,
        }],
    }

    normalized = _canonicalize_verified_repo_path_mentions(content, source_pack)

    assert "参考 lib/iscsi/iscsi.c/iscsi_ut.c" not in normalized
    assert "test/unit/lib/iscsi/iscsi.c/iscsi_ut.c" in normalized
    assert ":173-191" not in normalized
    assert "行号未验证" in normalized


def test_markdown_keeps_test_path_when_a_parent_directory_ends_with_c_extension():
    content = "`test/unit/lib/iscsi/iscsi.c/iscsi_ut.c` 是已验证单元测试。"
    source_pack = {
        "evidence_cards": [
            {"file_path": "lib/iscsi/iscsi.c", "start_line": 1551, "end_line": 1560},
            {
                "file_path": "test/unit/lib/iscsi/iscsi.c/iscsi_ut.c",
                "start_line": 174,
                "end_line": 220,
            },
        ],
    }

    normalized = _canonicalize_verified_repo_path_mentions(content, source_pack)

    assert "test/unit/lib/iscsi/iscsi.c/iscsi_ut.c" in normalized
    assert "`lib/iscsi/iscsi.c/iscsi_ut.c`" not in normalized


def test_sfmea_generation_rules_forbid_normal_behavior_padding_and_evidence_drift():
    rules = "\n".join(_stage_format_rules("sfmea", "sfmea.json"))

    assert "正常拒绝" in rules
    assert "安全释放" in rules
    assert "同一路径和函数" in rules
    assert "不得拆成多条" in rules


def test_black_box_generation_rules_keep_dimensions_and_external_boundaries():
    rules = "\n".join(_stage_format_rules("black_box_cases", "black_box_cases.json"))

    assert "保持既有 case_id" in rules
    assert "禁止 mock/调用 libnvme 或 libnvmf 内部函数" in rules
    assert "upstream_error_propagation" in rules
    assert "CLI 退出码" in rules


def test_supporting_markdown_rules_forbid_unverified_coverage_and_interface_claims():
    from app.services.ai_staged_execution import _stage_format_rules

    module_rules = "\n".join(_stage_format_rules("module_map", "module_map.md"))
    flow_rules = "\n".join(_stage_format_rules("business_flow", "flow_map.md"))
    strategy_rules = "\n".join(_stage_format_rules("test_strategy", "test_strategy.md"))

    assert "evidence_id" in module_rules
    assert "内核接口" in module_rules
    assert "每个流程步骤" in flow_rules
    assert "完整覆盖" in strategy_rules
    assert "环境版本" in strategy_rules


def test_test_strategy_runs_after_risk_and_case_design():
    from app.services.ai_staged_execution import build_staged_execution_plan

    contract = _contract()
    contract["required_outputs"].append("test_strategy.md")
    contract["artifact_contract"]["test_strategy.md"] = {
        "artifact": "test_strategy.md"
    }

    plan = build_staged_execution_plan(
        contract=contract,
        original_user_request="输出完整测试策略",
    )
    stage = next(item for item in plan["stages"] if item["artifact"] == "test_strategy.md")

    assert stage["depends_on"] == [
        "source_analysis",
        "flow_outline",
        "sfmea",
        "black_box_cases",
    ]


def test_sfmea_rows_receive_stable_ids_before_quality_repair():
    rows = [
        {"failure_mode": "risk-a"},
        {"sfmea_id": "SFMEA-009", "failure_mode": "risk-b"},
        {"failure_mode": "risk-c"},
    ]

    normalized, fields = _ensure_stable_stage_row_ids(rows, "sfmea")

    assert [item["sfmea_id"] for item in normalized] == [
        "SFMEA-001",
        "SFMEA-009",
        "SFMEA-003",
    ]
    assert fields == ["$[0].sfmea_id", "$[2].sfmea_id"]


def test_source_context_compaction_prefers_implementation_symbols_over_headers():
    from app.services.ai_staged_execution import _select_bounded_source_context_files

    selected = _select_bounded_source_context_files(
        [
            {
                "file_path": "libnvme/src/nvme/fabrics.c",
                "classification": "source",
                "symbols": ["connect_ctrl"],
                "matched_terms": ["connect"],
                "score": 80,
            },
            {
                "file_path": "libnvme/src/nvme/fabrics.h",
                "classification": "source",
                "symbols": [],
                "matched_terms": ["auth", "timeout"],
                "score": 100,
            },
            {
                "file_path": "libnvme/src/nvme/tree-fabrics.c",
                "classification": "source",
                "symbols": ["read_dhchap"],
                "matched_terms": ["dhchap", "tls"],
                "score": 60,
            },
            {
                "file_path": "libnvme/test/fabrics.c",
                "classification": "test",
                "symbols": ["test_connect"],
                "matched_terms": ["connect"],
                "score": 20,
            },
        ],
        limit=3,
        min_source_files=2,
        min_test_files=1,
        coverage_tokens=["connect", "auth", "timeout", "dhchap", "tls"],
    )

    paths = {item["file_path"] for item in selected}
    assert "libnvme/src/nvme/fabrics.c" in paths
    assert "libnvme/src/nvme/tree-fabrics.c" in paths
    assert "libnvme/test/fabrics.c" in paths
    assert "libnvme/src/nvme/fabrics.h" not in paths


def test_source_context_keeps_core_library_connect_implementation_for_broad_target():
    from app.services.ai_staged_execution import _select_bounded_source_context_files

    candidates = [
        {
            "file_path": "libnvme/src/nvme/fabrics.h",
            "classification": "source",
            "symbols": ["libnvmf_get_default_trsvcid"],
            "matched_terms": ["discovery", "connect", "fabrics", "nvmf", "retry"],
            "score": 44,
        },
        {
            "file_path": "fabrics.c",
            "classification": "source",
            "symbols": ["fabrics_discovery"],
            "matched_terms": ["discovery", "controller", "connect", "sysfs", "fabrics", "nvmf", "cleanup"],
            "score": 40,
        },
        {
            "file_path": "libnvme/src/nvme/config-ini.c",
            "classification": "source",
            "symbols": ["libnvmf_key_lookup"],
            "matched_terms": ["controller", "chap", "keyring", "nvmf", "dhchap"],
            "score": 32,
        },
        {
            "file_path": "libnvme/src/nvme/crypto.c",
            "classification": "source",
            "symbols": ["libnvmf_gen_dhchap_key"],
            "matched_terms": ["hmac", "chap", "nvmf", "dhchap"],
            "score": 32,
        },
        {
            "file_path": "libnvme/src/nvme/fabrics.c",
            "classification": "source",
            "symbols": ["libnvmf_connect_ctrl"],
            "matched_terms": ["connect", "nvmf", "cleanup"],
            "score": 32,
        },
        {
            "file_path": "libnvme/src/nvme/tree.c",
            "classification": "source",
            "symbols": ["libnvme_ctrl_get_reconnect_count"],
            "matched_terms": ["cleanup", "reconnect"],
            "score": 20,
        },
        {
            "file_path": "libnvme/src/nvme/tree-fabrics.c",
            "classification": "source",
            "symbols": ["libnvmf_read_sysfs_dhchap"],
            "matched_terms": ["chap", "sysfs", "nvmf", "dhchap"],
            "score": 36,
        },
    ]

    selected = _select_bounded_source_context_files(
        candidates,
        limit=6,
        min_source_files=6,
        min_test_files=0,
        coverage_tokens=[
            "discovery", "controller", "connect", "hmac", "chap", "tls",
            "keyring", "sysfs", "nvmf", "dhchap", "cleanup", "reconnect",
        ],
    )

    assert "libnvme/src/nvme/fabrics.c" in {
        item["file_path"] for item in selected
    }


def test_source_context_prefers_core_behavior_over_term_rich_formatter():
    from app.services.ai_staged_execution import _select_bounded_source_context_files

    candidates = [
        {
            "file_path": "nvme-print-binary.c",
            "classification": "source",
            "symbols": ["binary_discovery_log"],
            "matched_terms": ["discovery", "over", "log", "page", "nvmf"],
            "score": 20,
            "content_match_count": 12,
            "behavior_score": 0,
        },
        {
            "file_path": "fabrics.c",
            "classification": "source",
            "symbols": ["fabrics_discovery"],
            "matched_terms": ["discovery", "controller", "connect", "fabrics", "cleanup", "nvmf"],
            "score": 36,
            "content_match_count": 182,
            "behavior_score": 4,
        },
        {
            "file_path": "libnvme/src/nvme/crypto.c",
            "classification": "source",
            "symbols": ["libnvmf_gen_dhchap_key"],
            "matched_terms": ["hmac", "chap", "dhchap", "nvmf"],
            "score": 48,
            "content_match_count": 435,
            "behavior_score": 5,
        },
        {
            "file_path": "libnvme/src/nvme/fabrics.c",
            "classification": "source",
            "symbols": ["libnvmf_connect_ctrl"],
            "matched_terms": ["connect", "cleanup", "nvmf"],
            "score": 40,
            "content_match_count": 500,
            "behavior_score": 8,
        },
        {
            "file_path": "libnvme/src/nvme/tree.c",
            "classification": "source",
            "symbols": ["libnvme_ctrl_get_reconnect_count"],
            "matched_terms": ["reconnect", "cleanup", "long"],
            "score": 40,
            "content_match_count": 63,
            "behavior_score": 3,
        },
        {
            "file_path": "libnvme/test/ioctl/discovery.c",
            "classification": "test",
            "symbols": ["fetch_discovery_log"],
            "matched_terms": ["discovery", "log", "nvmf"],
            "score": 28,
            "content_match_count": 35,
            "behavior_score": 4,
        },
        {
            "file_path": "libnvme/test/tree-fabrics.c",
            "classification": "test",
            "symbols": ["tcp_ctrl_lookup"],
            "matched_terms": ["tcp", "nvmf"],
            "score": 20,
            "content_match_count": 40,
            "behavior_score": 3,
        },
        {
            "file_path": "libnvme/test/ioctl/logs.c",
            "classification": "test",
            "symbols": ["test_get_log_discovery"],
            "matched_terms": ["discovery", "log", "page"],
            "score": 20,
            "content_match_count": 25,
            "behavior_score": 2,
        },
    ]

    selected = _select_bounded_source_context_files(
        candidates,
        limit=6,
        min_source_files=1,
        min_test_files=3,
        coverage_tokens=[
            "tcp", "discovery", "controller", "connect", "hmac", "chap",
            "tls", "psk", "keyring", "fabrics", "dhchap", "over", "log",
            "page", "reconnect", "resource", "cleanup", "long",
        ],
    )

    paths = {item["file_path"] for item in selected}
    assert "libnvme/src/nvme/fabrics.c" in paths
    assert "nvme-print-binary.c" not in paths


def test_source_scope_counts_unique_files_separately_from_evidence_anchors():
    from app.services.ai_staged_execution import build_source_evidence_pack

    context = {
        "analysis_target": "connect and reconnect",
        "repo_path": "/repo",
        "repo_revision": "abc",
        "files": [
            {
                "evidence_id": "SRC-01",
                "file_path": "src/fabrics.c",
                "classification": "source",
                "start_line": 10,
                "end_line": 20,
                "excerpt": "connect",
                "symbols": ["connect_ctrl"],
                "matched_terms": ["connect"],
                "sha256": "a" * 64,
            },
            {
                "evidence_id": "SRC-02",
                "file_path": "src/fabrics.c",
                "classification": "source",
                "start_line": 30,
                "end_line": 40,
                "excerpt": "reconnect",
                "symbols": ["reconnect_ctrl"],
                "matched_terms": ["reconnect"],
                "sha256": "a" * 64,
            },
        ],
    }

    scope = build_source_evidence_pack(context)["source_scope"]

    assert scope["files"] == ["src/fabrics.c"]
    assert scope["source_files"] == ["src/fabrics.c"]
    assert scope["file_count"] == 1
    assert scope["evidence_anchor_count"] == 2


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


def test_flow_evidence_pack_classifies_returned_error_and_negative_rc_branch(tmp_path):
    """C error constants and negative return guards must remain visible to flow gates."""
    source = (
        "int login_params(void) {\n"
        "    int rc = parse_params();\n"
        "    if (rc < 0) {\n"
        "        return SPDK_ISCSI_LOGIN_ERROR_PARAMETER;\n"
        "    }\n"
        "    return 0;\n"
        "}\n"
    )
    source_pack = {
        "analysis_target": "iSCSI login parameter failure",
        "source_scope": {},
        "evidence_cards": [{
            "evidence_id": "SRC-01",
            "classification": "source",
            "file_path": "lib/iscsi/login.c",
            "start_line": 1,
            "end_line": 7,
            "excerpt": source,
            "symbols": ["login_params"],
            "sha256": hashlib.sha256(source.encode()).hexdigest(),
        }],
    }

    pack = build_flow_evidence_pack(source_pack)

    error_text = [str(item.get("text") or "") for item in pack["error_paths"]]
    assert "if (rc < 0) {" in error_text
    assert "return SPDK_ISCSI_LOGIN_ERROR_PARAMETER;" in error_text


def test_flow_evidence_pack_does_not_misclassify_names_or_macros_as_error_paths():
    source = (
        "#define ISCSI_LOGIN_TIMEOUT 30\n"
        "static int login_timeout(void *arg) {\n"
        "    return 0;\n"
        "}\n"
    )
    source_pack = {
        "analysis_target": "login timeout",
        "source_scope": {},
        "evidence_cards": [{
            "evidence_id": "SRC-01",
            "classification": "source",
            "file_path": "lib/iscsi/login.c",
            "start_line": 1,
            "end_line": 4,
            "excerpt": source,
            "symbols": ["login_timeout"],
            "sha256": hashlib.sha256(source.encode()).hexdigest(),
        }],
    }

    assert build_flow_evidence_pack(source_pack)["error_paths"] == []


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


def test_flow_evidence_expands_reverse_callers_and_outlines_from_verified_ingress(tmp_path):
    repo = tmp_path / "repo-reverse-flow"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    source = (
        "int login_complete(void) { return 0; }\n"
        "int payload_login(void) { return login_complete(); }\n"
        "int read_pdu(void) { return payload_login(); }\n"
        "int incoming_pdus(void) { return read_pdu(); }\n"
        "int portal_accept(void) { return incoming_pdus(); }\n"
    )
    (repo / "lib" / "iscsi" / "login.c").write_text(source, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=CodeTalk Test", "-c", "user.email=codetalk@example.invalid", "commit", "-qm", "fixture"],
        cwd=repo,
        check=True,
    )
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    source_pack = {
        "analysis_target": "iSCSI Login",
        "repo_revision": revision,
        "source_scope": {"repo": str(repo), "source_files": ["lib/iscsi/login.c"]},
        "evidence_cards": [{
            "evidence_id": "SRC-01",
            "file_path": "lib/iscsi/login.c",
            "classification": "source",
            "start_line": 1,
            "end_line": 1,
            "excerpt": "int login_complete(void) { return 0; }\n",
            "symbols": ["login_complete"],
            "sha256": hashlib.sha256(source.encode()).hexdigest(),
        }],
    }

    pack = build_flow_evidence_pack(source_pack, repo_path=str(repo), max_files=2)
    outline = build_flow_outline(pack)

    assert {
        (edge["from_symbol"], edge["to_symbol"])
        for edge in pack["call_edges"]
    }.issuperset({
        ("payload_login", "login_complete"),
        ("read_pdu", "payload_login"),
        ("incoming_pdus", "read_pdu"),
        ("portal_accept", "incoming_pdus"),
    })
    assert outline["main_flows"][0]["root_symbol"] == "portal_accept"
    assert [step["to_symbol"] for step in outline["main_flows"][0]["steps"]] == [
        "incoming_pdus", "read_pdu", "payload_login", "login_complete"
    ]


def test_flow_evidence_prioritizes_a_verified_chain_over_unrelated_card_symbols(tmp_path):
    """A crowded evidence pack must not starve the target's call chain."""
    repo = tmp_path / "repo-priority-flow"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    source = (
        "static int\nlogin_complete(void) { return 0; }\n"
        "static int\npayload_login(void) { return login_complete(); }\n"
        "static int\nread_pdu(void) { return payload_login(); }\n"
        "static int\nincoming_pdus(void) { return read_pdu(); }\n"
        "static int\nportal_accept(void) { return incoming_pdus(); }\n"
        "int unrelated_01(void) { return 0; }\n"
        "int unrelated_02(void) { return 0; }\n"
        "int unrelated_03(void) { return 0; }\n"
        "int unrelated_04(void) { return 0; }\n"
        "int unrelated_05(void) { return 0; }\n"
        "int unrelated_06(void) { return 0; }\n"
        "int unrelated_07(void) { return 0; }\n"
        "int unrelated_08(void) { return 0; }\n"
        "int unrelated_09(void) { return 0; }\n"
        "int unrelated_10(void) { return 0; }\n"
        "int unrelated_11(void) { return 0; }\n"
        "int unrelated_12(void) { return 0; }\n"
        "int unrelated_13(void) { return 0; }\n"
        "int unrelated_14(void) { return 0; }\n"
        "int unrelated_15(void) { return 0; }\n"
        "int unrelated_16(void) { return 0; }\n"
    )
    path = repo / "lib" / "iscsi" / "login.c"
    path.write_text(source, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=CodeTalk Test", "-c", "user.email=codetalk@example.invalid", "commit", "-qm", "fixture"],
        cwd=repo,
        check=True,
    )
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    noise_symbols = [f"unrelated_{index:02d}" for index in range(1, 17)]
    source_pack = {
        "analysis_target": "iSCSI Login",
        "repo_revision": revision,
        "source_scope": {"repo": str(repo), "source_files": ["lib/iscsi/login.c"]},
        "evidence_cards": [
            {
                "evidence_id": "SRC-NOISE",
                "file_path": "lib/iscsi/login.c",
                "classification": "source",
                "start_line": 11,
                "end_line": 26,
                "excerpt": "\n".join(source.splitlines()[10:]),
                "symbols": noise_symbols,
                "sha256": hashlib.sha256(source.encode()).hexdigest(),
            },
            {
                "evidence_id": "SRC-LOGIN",
                "file_path": "lib/iscsi/login.c",
                "classification": "source",
                "start_line": 1,
                "end_line": 2,
                "excerpt": "static int\nlogin_complete(void) { return 0; }\n",
                "symbols": ["login_complete"],
                "sha256": hashlib.sha256(source.encode()).hexdigest(),
            },
        ],
    }

    pack = build_flow_evidence_pack(source_pack, repo_path=str(repo), max_files=4)

    assert {
        (edge["from_symbol"], edge["to_symbol"])
        for edge in pack["call_edges"]
    }.issuperset({
        ("payload_login", "login_complete"),
        ("read_pdu", "payload_login"),
        ("incoming_pdus", "read_pdu"),
        ("portal_accept", "incoming_pdus"),
    })


def test_flow_evidence_preserves_a_verified_edge_when_the_callee_is_already_seeded(tmp_path):
    """Queue de-duplication must not discard a separately verified call edge."""
    repo = tmp_path / "repo-seeded-edge"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    noisy_callers = "".join(
        f"static int\nnoise_{index:03d}(void) {{ return store_params(); }}\n"
        for index in range(90)
    )
    source = noisy_callers + (
        "static int\n"
        "store_params(void) { return 0; }\n"
        "static int\n"
        "payload_login(void) { return store_params(); }\n"
    )
    path = repo / "lib" / "iscsi" / "login.c"
    path.write_text(source, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=CodeTalk Test", "-c", "user.email=codetalk@example.invalid", "commit", "-qm", "fixture"],
        cwd=repo,
        check=True,
    )
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    source_pack = {
        "analysis_target": "iSCSI payload_login to store_params flow",
        "repo_revision": revision,
        "source_scope": {"repo": str(repo), "source_files": ["lib/iscsi/login.c"]},
        "evidence_cards": [
            {
                "evidence_id": "SRC-PAYLOAD",
                "file_path": "lib/iscsi/login.c",
                "classification": "source",
                "start_line": 183,
                "end_line": 184,
                "excerpt": "static int\npayload_login(void) {\n",
                "symbols": ["payload_login"],
                "sha256": hashlib.sha256(source.encode()).hexdigest(),
            },
            {
                "evidence_id": "SRC-STORE",
                "file_path": "lib/iscsi/login.c",
                "classification": "source",
                "start_line": 181,
                "end_line": 182,
                "excerpt": "static int\nstore_params(void) { return 0; }\n",
                "symbols": ["store_params"],
                "sha256": hashlib.sha256(source.encode()).hexdigest(),
            },
        ],
    }

    pack = build_flow_evidence_pack(source_pack, repo_path=str(repo), max_files=2)

    assert ("payload_login", "store_params") in {
        (edge["from_symbol"], edge["to_symbol"])
        for edge in pack["call_edges"]
    }


def test_flow_symbol_parser_accepts_split_c_definitions_but_rejects_control_macros():
    assert _definition_symbol("static int\niscsi_login(void)\n{") == "iscsi_login"
    assert _definition_symbol("TAILQ_FOREACH(item, &items, link) {") == ""


def test_flow_evidence_version_changes_when_its_verified_edge_semantics_change():
    """A corrected deterministic artifact must not reuse the prior cache entry."""
    assert FLOW_EVIDENCE_VERSION == "flow-evidence-pack-v6"


def test_flow_evidence_tracks_verified_callback_references_without_call_syntax(tmp_path):
    repo = tmp_path / "repo-callback-flow"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    source = (
        "static void\nlogin_complete(void) { }\n"
        "static int\nresponse(void (*callback)(void)) { callback(); return 0; }\n"
        "static int\npayload_login(void) { return response(login_complete); }\n"
        "static int\nincoming_pdus(void) { return payload_login(); }\n"
    )
    path = repo / "lib" / "iscsi" / "login.c"
    path.write_text(source, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=CodeTalk Test", "-c", "user.email=codetalk@example.invalid", "commit", "-qm", "fixture"],
        cwd=repo,
        check=True,
    )
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    pack = build_flow_evidence_pack(
        {
            "analysis_target": "iSCSI login",
            "repo_revision": revision,
            "source_scope": {"repo": str(repo), "source_files": ["lib/iscsi/login.c"]},
            "evidence_cards": [{
                "evidence_id": "SRC-LOGIN",
                "file_path": "lib/iscsi/login.c",
                "classification": "source",
                "start_line": 1,
                "end_line": 2,
                "excerpt": "static void\nlogin_complete(void) { }\n",
                "symbols": ["login_complete"],
                "sha256": hashlib.sha256(source.encode()).hexdigest(),
            }],
        },
        repo_path=str(repo),
        max_files=2,
    )

    callback_edge = next(
        edge for edge in pack["call_edges"]
        if edge.get("from_symbol") == "payload_login"
        and edge.get("to_symbol") == "login_complete"
    )
    assert callback_edge["relation"] == "callback_reference"


def test_flow_evidence_discovers_callback_argument_from_the_active_function(tmp_path):
    """The completion callback need not be preselected as an evidence card."""
    repo = tmp_path / "repo-callback-argument"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    source = (
        "static void\nlogin_complete(void) { }\n"
        "static int\nresponse(void (*callback)(void)) { callback(); return 0; }\n"
        "static int\npayload_login(void) { return response(login_complete); }\n"
    )
    path = repo / "lib" / "iscsi" / "login.c"
    path.write_text(source, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=CodeTalk Test", "-c", "user.email=codetalk@example.invalid", "commit", "-qm", "fixture"],
        cwd=repo,
        check=True,
    )
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    pack = build_flow_evidence_pack(
        {
            "analysis_target": "iSCSI login",
            "repo_revision": revision,
            "source_scope": {"repo": str(repo), "source_files": ["lib/iscsi/login.c"]},
            "evidence_cards": [{
                "evidence_id": "SRC-PAYLOAD",
                "file_path": "lib/iscsi/login.c",
                "classification": "source",
                "start_line": 5,
                "end_line": 5,
                "excerpt": "payload_login(void) { return response(login_complete); }",
                "symbols": ["payload_login"],
                "sha256": hashlib.sha256(source.encode()).hexdigest(),
            }],
        },
        repo_path=str(repo),
        max_files=2,
    )

    assert any(
        edge.get("from_symbol") == "payload_login"
        and edge.get("to_symbol") == "login_complete"
        and edge.get("relation") == "callback_reference"
        for edge in pack["call_edges"]
    )


def test_flow_outline_does_not_merge_test_helper_edges_into_product_main_path():
    outline = build_flow_outline(
        {
            "analysis_target": "iSCSI login",
            "entry_points": [{
                "evidence_id": "ENTRY",
                "file_path": "lib/iscsi/login.c",
                "symbol": "login_start",
            }],
            "call_edges": [
                {
                    "evidence_id": "EDGE-01",
                    "file_path": "lib/iscsi/login.c",
                    "from_symbol": "login_start",
                    "to_symbol": "login_dispatch",
                },
                {
                    "evidence_id": "EDGE-02",
                    "file_path": "lib/iscsi/login.c",
                    "from_symbol": "login_dispatch",
                    "to_symbol": "login_success",
                },
                {
                    "evidence_id": "EDGE-TEST",
                    "file_path": "test/iscsi_tgt/login_helper.c",
                    "from_symbol": "login_dispatch",
                    "to_symbol": "fixture_helper",
                },
            ],
        }
    )

    assert [step["to_symbol"] for step in outline["main_flows"][0]["steps"]] == [
        "login_dispatch",
        "login_success",
    ]


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


@pytest.mark.asyncio
async def test_source_driven_v2_stages_materialize_complete_governed_bundle(tmp_path):
    compact = build_source_analysis_context(
        plan={"original_user_request": "分析 iSCSI login", "target": "iSCSI login"},
        staged_context=_verified_source_context(),
        max_files=6,
        excerpt_chars=1200,
        max_evidence_anchors=12,
    )
    source_pack = build_source_evidence_pack(compact)
    source_stage = tmp_path / "stages" / "source_analysis"
    source_stage.mkdir(parents=True)
    (source_stage / "source_evidence_pack.json").write_text(
        json.dumps(source_pack), encoding="utf-8"
    )
    flow_pack = build_flow_evidence_pack(source_pack)
    (tmp_path / "flow_evidence_pack.json").write_text(
        json.dumps(flow_pack), encoding="utf-8"
    )
    (tmp_path / "flow_outline.json").write_text(
        json.dumps(build_flow_outline(flow_pack)), encoding="utf-8"
    )
    (tmp_path / "sfmea.json").write_text("[]", encoding="utf-8")
    (tmp_path / "black_box_cases.json").write_text("[]", encoding="utf-8")

    results = []
    for stage_id in (
        "breadth_inventory",
        "developer_explanation",
        "scenario_expansion",
        "test_design_governance",
        "coverage_judge",
        "test_design_mindmap",
    ):
        stage_dir = tmp_path / "stages" / stage_id
        stage_dir.mkdir(parents=True)
        results.append(
            await _execute_source_driven_deterministic_stage(
                plan={"original_user_request": "分析 iSCSI login"},
                stage={"id": stage_id},
                stage_dir=stage_dir,
                artifact_dir=tmp_path,
                is_cancelled=None,
                on_progress=None,
            )
        )

    assert all(result["provider_call_count"] == 0 for result in results)
    assert json.loads((tmp_path / "judge_report.json").read_text())["status"] == "BLOCKED"
    assert (tmp_path / "test_design_mindmap.json").is_file()
    assert "data-mindmap-root" in (tmp_path / "test_design_mindmap.html").read_text()
    assert "test-design-mindmap-v1" in (tmp_path / "test_design_mindmap.svg").read_text()


def test_source_evidence_pack_replaces_retry_seed_with_current_canonical_pack(tmp_path):
    stale_cards = [
        {
            "evidence_id": "SRC-01",
            "file_path": "stale.c",
            "start_line": 1,
            "end_line": 1,
            "excerpt": "int stale(void);",
            "symbols": ["stale"],
            "sha256": "0" * 64,
        }
    ]
    (tmp_path / "source_scope.json").write_text(
        json.dumps({"files": ["stale.c"]}), encoding="utf-8"
    )
    (tmp_path / "evidence_cards.json").write_text(
        json.dumps(stale_cards), encoding="utf-8"
    )
    pack = build_source_evidence_pack(
        {
            "analysis_target": "NVMe fabrics connect",
            "repo_revision": "abc123",
            "files": [
                {
                    "evidence_id": "SRC-12",
                    "file_path": "libnvme/test/ioctl/logs.c",
                    "classification": "test",
                    "start_line": 849,
                    "end_line": 849,
                    "excerpt": "static void test_get_log_discovery(void)",
                    "symbols": ["test_get_log_discovery"],
                    "matched_terms": ["discovery"],
                    "sha256": "a" * 64,
                }
            ],
        }
    )

    materialize_source_evidence_pack(pack, tmp_path)

    assert json.loads((tmp_path / "source_scope.json").read_text(encoding="utf-8")) == pack[
        "source_scope"
    ]
    assert json.loads((tmp_path / "evidence_cards.json").read_text(encoding="utf-8")) == pack[
        "evidence_cards"
    ]


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
        "breadth_inventory",
        "developer_explanation",
        "scenario_expansion",
        "business_flow",
        "sfmea",
    ]
    stages = {stage["id"]: stage for stage in plan["stages"]}
    assert stages["breadth_inventory"]["depends_on"] == ["flow_outline"]
    sfmea_stage = stages["sfmea"]
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


def test_test_design_mindmap_consumes_evidence_flow_risk_and_cases():
    contract = _contract()
    contract["required_outputs"].append("test_design_mindmap.md")
    contract["artifact_contract"]["test_design_mindmap.md"] = {
        "artifact": "test_design_mindmap.md"
    }

    plan = build_staged_execution_plan(
        contract=contract,
        original_user_request="输出测试设计脑图",
    )

    stage = next(
        item for item in plan["stages"] if item["artifact"] == "test_design_mindmap.md"
    )
    assert stage["id"] == "test_design_mindmap"
    assert stage["depends_on"] == [
        "source_analysis",
        "flow_outline",
        "sfmea",
        "black_box_cases",
    ]


def test_compact_source_context_keeps_rare_target_term_evidence():
    from app.services.ai_staged_execution import _select_bounded_source_context_files

    common = [
        {
            "file_path": f"src/fabrics_{index}.c",
            "classification": "source",
            "score": 100 - index,
            "matched_terms": ["fabrics", "connect", "discovery"],
        }
        for index in range(6)
    ]
    psk_test = {
        "file_path": "test/psk.c",
        "classification": "test",
        "score": 20,
        "matched_terms": ["hmac", "chap", "tls", "psk"],
    }
    common_test = {
        "file_path": "test/fabrics.c",
        "classification": "test",
        "score": 40,
        "matched_terms": ["fabrics", "connect", "discovery"],
    }
    rare_source = {
        "file_path": "src/nvme.c",
        "classification": "source",
        "score": 80,
        "matched_terms": ["hmac", "chap", "tls", "psk"],
    }

    selected = _select_bounded_source_context_files(
        [*common, rare_source, common_test, psk_test],
        limit=4,
        min_source_files=1,
        min_test_files=1,
        coverage_tokens=["fabrics", "connect", "discovery", "hmac", "chap", "tls", "psk"],
    )

    assert "test/psk.c" in [item["file_path"] for item in selected]


def test_test_design_plan_requires_three_test_evidence_files():
    from app.services.ai_staged_execution import _source_evidence_minimums

    plan = build_staged_execution_plan(
        contract=_contract(),
        original_user_request="基于源码生成 SFMEA 和黑盒测试",
    )

    assert _source_evidence_minimums(plan) == (1, 3)


def test_sfmea_prompt_separates_verified_behavior_from_hypothetical_failure():
    from app.services.ai_staged_execution import _stage_format_rules

    rules = "\n".join(_stage_format_rules("sfmea", "sfmea.json"))

    assert "technical_claims.statement" in rules
    assert "假设性失效" in rules
    assert "不得把风险假设写成已存在的代码缺陷" in rules
    assert "未显示" in rules
    assert "声明文件" in rules
    assert "测试辅助代码" in rules
    assert "不得重复" in rules


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
        "breadth_inventory",
        "developer_explanation",
        "scenario_expansion",
        "business_flow",
        "sfmea",
        "black_box_cases",
        "test_strategy",
        "test_design",
    ]
    stages = {stage["id"]: stage for stage in plan["stages"]}
    assert stages["sfmea"]["depends_on"] == ["source_analysis", "flow_outline"]
    assert stages["black_box_cases"]["depends_on"] == [
        "source_analysis", "flow_outline", "sfmea", "scenario_expansion"
    ]


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
    assert result["completed_stages"] == 10
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
    assert stage_result["provider_call_count"] == 1
    assert stage_result["degraded"] is True
    assert stage_result["degradation_reason"] == "provider_timeout"
    assert stage_result["provider_wait_ms"] >= 40
    assert stage_result["total_duration_ms"] >= stage_result["provider_wait_ms"]


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
    assert any(
        event.get("event_type") == "stage_reused"
        and event.get("reuse_source") == "cross_run_cache"
        for event in reused_events
    )
    stage_result = json.loads(
        (second_dir / "stages" / "source_analysis" / "stage_result.json").read_text()
    )
    assert stage_result["cache_status"] == "hit"
    assert stage_result["attempt_count"] == 0
    assert stage_result["provider_call_count"] == 0
    assert stage_result["duration_ms"] < 30000
    assert stage_result["total_duration_ms"] == stage_result["duration_ms"]


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
async def test_provider_truncated_json_array_uses_reconstruction_budget(tmp_path):
    class RepairLLM(_StageLLM):
        def __init__(self) -> None:
            super().__init__()
            self.repair_max_tokens = 0

        async def complete_once(self, messages, max_tokens=4096, temperature=0.2):
            prompt = messages[-1]["content"]
            if "STAGE_ID: source_analysis" in prompt:
                return await super().complete(messages, max_tokens, temperature)
            if "SMALL_FORMAT_REPAIR" in prompt:
                self.repair_max_tokens = max_tokens
                return LLMResponse(
                    content=json.dumps(
                        [{"failure_mode": "timeout", "cause": "peer silent"}]
                    ),
                    model="repair",
                    usage={},
                    truncated=False,
                )
            return LLMResponse(
                content='[{"failure_mode":"timeout"',
                model="full",
                usage={},
                truncated=True,
                finish_reason="length",
            )

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

    result = await execute_staged_builtin_plan(
        llm=llm,
        plan=build_staged_execution_plan(
            contract=contract,
            original_user_request="reconstruct a truncated json row",
        ),
        artifact_dir=tmp_path,
        context_prompt="legacy",
        source_analysis_context=_verified_source_context(),
    )

    assert result["status"] == "completed"
    assert llm.repair_max_tokens >= 2400


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


def test_reused_source_analysis_rematerializes_its_canonical_evidence_pack(tmp_path):
    from app.services.ai_staged_execution import _existing_quality_stage_result

    artifact_dir = tmp_path / "run"
    stage_dir = artifact_dir / "stages" / "source_analysis"
    stage_dir.mkdir(parents=True)
    (artifact_dir / "source_analysis.md").write_text("# source\n", encoding="utf-8")
    canonical_pack = {
        "source_scope": {"scope_id": "current"},
        "evidence_cards": [
            {
                "evidence_id": "SRC-15",
                "file_path": "libnvme/src/nvme/crypto.c",
                "start_line": 1004,
                "end_line": 1004,
                "excerpt": "int libnvmf_set_keyring(void);",
                "symbols": ["libnvmf_set_keyring"],
                "sha256": "digest",
            }
        ],
    }
    (stage_dir / "source_evidence_pack.json").write_text(
        json.dumps(canonical_pack),
        encoding="utf-8",
    )
    (artifact_dir / "evidence_cards.json").write_text(
        json.dumps([{"evidence_id": "SRC-01", "file_path": "stale.c"}]),
        encoding="utf-8",
    )
    (artifact_dir / "source_scope.json").write_text(
        json.dumps({"scope_id": "stale"}),
        encoding="utf-8",
    )

    reused = _existing_quality_stage_result(
        plan={
            "quality_retry_feedback": {"issue_count": 1},
            "cache_bypass_artifacts": ["black_box_cases.json"],
        },
        artifact_dir=artifact_dir,
        stage_dir=stage_dir,
        stage={"id": "source_analysis", "artifact": "source_analysis.md"},
    )

    assert reused is not None
    assert json.loads((artifact_dir / "source_scope.json").read_text()) == {
        "scope_id": "current"
    }
    assert json.loads((artifact_dir / "evidence_cards.json").read_text()) == canonical_pack[
        "evidence_cards"
    ]


def test_reused_source_analysis_rebuilds_missing_sidecar_from_task_owned_cards(tmp_path):
    from app.services.ai_staged_execution import _existing_quality_stage_result

    artifact_dir = tmp_path / "run"
    stage_dir = artifact_dir / "stages" / "source_analysis"
    stage_dir.mkdir(parents=True)
    (artifact_dir / "source_analysis.md").write_text("# source\n", encoding="utf-8")
    cards = [{
        "evidence_id": "SRC-01",
        "file_path": "lib/iscsi/conn.c",
        "start_line": 625,
        "end_line": 645,
        "excerpt": "spdk_sock_close(&conn->sock);",
        "symbols": ["_iscsi_conn_destruct"],
        "sha256": "digest",
    }]
    (artifact_dir / "evidence_cards.json").write_text(json.dumps(cards), encoding="utf-8")
    (artifact_dir / "source_scope.json").write_text(json.dumps({"scope_id": "current"}), encoding="utf-8")

    reused = _existing_quality_stage_result(
        plan={"quality_retry_feedback": {"issue_count": 1}, "original_user_request": "cleanup"},
        artifact_dir=artifact_dir,
        stage_dir=stage_dir,
        stage={"id": "source_analysis", "artifact": "source_analysis.md"},
    )

    assert reused is not None
    sidecar = json.loads((stage_dir / "source_evidence_pack.json").read_text())
    assert sidecar["evidence_cards"] == cards


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
    reused_source_pack = json.loads(
        (tmp_path / "second" / "stages" / "source_analysis" / "source_evidence_pack.json").read_text()
    )
    assert reused_flow["entry_point_count"] == len(reused_pack.get("entry_points") or [])
    assert reused_flow["call_edge_count"] == len(reused_pack.get("call_edges") or [])
    assert reused_flow["test_reference_count"] == len(reused_pack.get("related_tests") or [])
    expected_reused_edge_ids = {
        str(edge.get("evidence_id") or "")
        for edge in reused_pack.get("call_edges") or []
        if isinstance(edge, dict)
        and edge.get("evidence_id")
        and edge.get("file_path")
        and edge.get("matched_text")
        and edge.get("start_line")
        and edge.get("sha256")
    }
    assert {
        card["evidence_id"]
        for card in reused_source_pack["evidence_cards"]
        if str(card.get("evidence_id") or "").startswith("FLOW-EDGE-")
    } == expected_reused_edge_ids
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


def test_deterministic_quality_repair_uses_existing_recovery_verification_for_mitigation():
    repaired, fields = _deterministic_quality_claim_repair(
        [{"sfmea_id": "SFMEA-012", "mitigation": "整改: 根据解析返回值设置对应的 status_detail。", "recovery_verification": "故障注入 iscsi_parse_params 返回错误，并断言 Login Response 的 status_detail。"}],
        artifact="sfmea.json",
        quality_feedback={"issues": [{"artifact": "sfmea.json", "code": "non_actionable_mitigation", "row_id": "SFMEA-012", "gaps": ["missing_verification_action"]}]},
    )
    assert fields == ["$[0].mitigation"]
    assert "验证: 故障注入 iscsi_parse_params 返回错误" in repaired[0]["mitigation"]


def test_deterministic_quality_repair_distinguishes_duplicate_sfmea_mitigations():
    shared = "整改: 明确异常路径的状态、资源和错误传播契约。验证: 注入触发条件并确认协议响应、连接状态和资源指标一致。"
    repaired, fields = _deterministic_quality_claim_repair(
        [
            {"sfmea_id": "SFMEA-002", "failure_mode": "连接清理失败后资源残留", "mitigation": shared},
            {"sfmea_id": "SFMEA-003", "failure_mode": "登录回调与连接退出竞态导致重复处理", "mitigation": shared},
            {"sfmea_id": "SFMEA-007", "failure_mode": "登录阶段切换交错导致参数状态异常", "mitigation": shared},
        ],
        artifact="sfmea.json",
        quality_feedback={"issues": [{
            "artifact": "sfmea.json",
            "code": "duplicate_generic_sfmea_mitigation",
            "row_ids": ["SFMEA-002", "SFMEA-003", "SFMEA-007"],
        }]},
    )

    mitigations = [row["mitigation"] for row in repaired]
    assert fields == ["$[0].mitigation", "$[1].mitigation", "$[2].mitigation"]
    assert len(set(mitigations)) == 3
    assert "资源计数回到基线" in mitigations[0]
    assert "只产生一次外部响应" in mitigations[1]
    assert "阶段字段" in mitigations[2]


def test_deterministic_quality_repair_restores_missing_upstream_error_dimension():
    repaired, fields = _deterministic_quality_claim_repair(
        [{
            "case_id": "BB-01",
            "test_dimension": "normal_path",
            "risk_ids": ["SFMEA-001"],
            "technical_claims": [{"claim_id": "TC-001"}],
        }],
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "missing_black_box_dimensions",
            "dimensions": ["upstream_error_propagation"],
        }]},
    )

    restored = repaired[-1]
    assert fields == ["$[+].upstream_error_propagation_case"]
    assert restored["test_dimension"] == "upstream_error_propagation"
    assert restored["technical_claims"] == [{"claim_id": "TC-001"}]
    assert "错误传播" in restored["scenario_name"]
    assert isinstance(restored["failure_diagnostics"], list)


def test_deterministic_quality_repair_restores_missing_resource_cleanup_dimension():
    repaired, fields = _deterministic_quality_claim_repair(
        [{
            "case_id": "BB-01",
            "test_dimension": "normal_path",
            "risk_ids": ["SFMEA-001"],
            "technical_claims": [{"claim_id": "TC-001"}],
        }],
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "missing_black_box_dimensions",
            "dimensions": ["resource_cleanup"],
        }]},
    )

    restored = repaired[-1]
    assert fields == ["$[+].resource_cleanup_case"]
    assert restored["test_dimension"] == "resource_cleanup"
    assert restored["technical_claims"] == [{"claim_id": "TC-001"}]
    assert "清理" in restored["scenario_name"]
    assert "基线" in restored["expected_result"]


def test_first_pass_black_box_output_materializes_missing_contract_dimension():
    from app.services.ai_staged_execution import _materialize_missing_black_box_dimensions

    repaired, fields = _materialize_missing_black_box_dimensions(
        [{
            "case_id": "BB-01",
            "test_dimension": "normal_path",
            "risk_ids": ["SFMEA-001"],
            "technical_claims": [{"claim_id": "TC-001"}],
        }],
        stage={"output_contract": {"required_dimensions": ["normal_path", "resource_cleanup"]}},
        sfmea_risk_ledger=[],
        evidence_cards=[],
    )

    assert fields == ["$[+].resource_cleanup_case"]
    assert {row["test_dimension"] for row in repaired} == {"normal_path", "resource_cleanup"}


def test_missing_black_box_contract_dimensions_include_reconnect_performance_and_steady_state():
    from app.services.ai_staged_execution import _materialize_missing_black_box_dimensions

    repaired, fields = _materialize_missing_black_box_dimensions(
        [{
            "case_id": "BB-01",
            "test_dimension": "normal_path",
            "risk_ids": ["SFMEA-001"],
            "technical_claims": [{"claim_id": "TC-001"}],
        }],
        stage={"output_contract": {"required_dimensions": [
            "normal_path", "reconnect", "performance", "long_steady_state",
        ]}},
        sfmea_risk_ledger=[],
        evidence_cards=[],
    )

    by_dimension = {row["test_dimension"]: row for row in repaired}
    assert {"reconnect", "performance", "long_steady_state"} <= set(by_dimension)
    for dimension in ("reconnect", "performance", "long_steady_state"):
        assert by_dimension[dimension]["observability"]
        assert by_dimension[dimension]["oracle_basis"]
        assert "状态" in by_dimension[dimension]["expected_result"] or "日志" in by_dimension[dimension]["expected_result"]
    assert "$[+].reconnect_case" in fields
    assert "$[+].performance_case" in fields
    assert "$[+].long_steady_state_case" in fields


def test_black_box_output_limit_never_drops_declared_required_dimensions():
    from app.services.ai_staged_execution import _apply_regular_stage_output_limits

    rows = [
        {"case_id": f"BB-{index}", "test_dimension": f"dimension-{index}"}
        for index in range(12)
    ]
    result = _apply_regular_stage_output_limits(
        rows,
        {
            "output_contract": {
                "required_dimensions": [f"dimension-{index}" for index in range(12)],
            },
            "output_limits": {"max_items": 8},
        },
    )

    assert len(result) == 12


def test_black_box_output_limit_never_drops_schema_declared_atomic_cases():
    from app.services.ai_staged_execution import _apply_regular_stage_output_limits

    rows = [
        {"case_id": f"BB-{index}", "test_dimension": "invalid_input"}
        for index in range(27)
    ]
    result = _apply_regular_stage_output_limits(
        rows,
        {
            "output_contract": {
                "schema": {"type": "array", "minItems": 27},
                "required_dimensions": ["invalid_input"],
            },
            "output_limits": {"max_items": 12},
        },
    )

    assert len(result) == 27


def test_deep_iscsi_plan_budget_allows_atomic_profile_scenarios():
    contract = {
        "target": "完整 iSCSI Login 测试设计",
        "domain_profiles": ["iscsi_login"],
        "domain_requirements": {
            "iscsi_login": {
                "required_scenarios": [f"场景-{index}" for index in range(15)],
            }
        },
        "required_outputs": ["report.md"],
        "artifact_contract": {
            "report.md": {
                "artifact": "report.md",
                "min_sfmea_rows": 12,
                "min_black_box_cases": 12,
            }
        },
    }

    plan = build_staged_execution_plan(
        contract=contract,
        original_user_request="完整 iSCSI Login 测试设计",
        execution_profile={"id": "deep"},
    )
    cases = next(stage for stage in plan["stages"] if stage["id"] == "black_box_cases")

    assert cases["output_contract"]["schema"]["minItems"] == 27
    assert cases["output_limits"]["max_items"] == 27
    assert cases["continue_on_length"] is True


def test_deep_iscsi_plan_uses_atomic_matrix_over_generic_profile_summary():
    contract = {
        "target": "完整 iSCSI Login 测试设计",
        "domain_profiles": ["iscsi_login"],
        "domain_requirements": {
            "iscsi_login": {
                "required_scenarios": ["宽泛场景"],
                "required_atomic_scenarios": [f"原子场景-{index}" for index in range(20)],
            }
        },
        "required_outputs": ["report.md"],
        "artifact_contract": {
            "report.md": {
                "artifact": "report.md",
                "min_sfmea_rows": 1,
                "min_black_box_cases": 1,
            }
        },
    }

    plan = build_staged_execution_plan(
        contract=contract,
        original_user_request="完整 iSCSI Login 测试设计",
        execution_profile={"id": "deep"},
    )
    cases = next(stage for stage in plan["stages"] if stage["id"] == "black_box_cases")

    assert cases["output_contract"]["schema"]["minItems"] == 32
    assert cases["output_limits"]["max_items"] == 32
    assert cases["output_contract"]["required_atomic_scenarios"] == [
        f"原子场景-{index}" for index in range(20)
    ]


def test_sfmea_prompt_bounds_large_dependency_and_evidence_context(tmp_path):
    source_pack = {
        "repo_revision": "abc123",
        "evidence_cards": [
            {
                "evidence_id": f"SRC-{index:02d}",
                "file_path": f"lib/iscsi/file_{index}.c",
                "start_line": 10,
                "end_line": 20,
                "symbols": [f"symbol_{index}"],
                "classification": "source",
                "excerpt": ("if (error) { return -1; }\n" * 120),
            }
            for index in range(32)
        ],
    }
    dependency = tmp_path / "deep-note.md"
    dependency.write_text("deep evidence\n" * 3000, encoding="utf-8")
    prompt = _regular_stage_prompt(
        plan={"original_user_request": "完整 iSCSI Login 测试设计"},
        stage={
            "id": "sfmea",
            "artifact": "sfmea.json",
            "depends_on": ["deep_entry_paths"],
            "output_contract": {"artifact": "sfmea.json", "schema": SFMEA_SCHEMA},
        },
        source_pack=source_pack,
        flow_pack={},
        outline={},
        completed={"deep_entry_paths": dependency},
    )

    assert len(prompt) < 40_000
    assert '"truncated": true' in prompt
    assert "SRC-00" in prompt


def test_deterministic_quality_repair_makes_targeted_black_box_result_observable():
    repaired, fields = _deterministic_quality_claim_repair(
        [{
            "case_id": "BB-LOGIN-008",
            "expected_result": "第二次登录成功，参数协商正确",
            "observability": ["initiator 登录输出"],
        }],
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "black_box_expected_result_ambiguous",
            "row_id": "BB-LOGIN-008",
        }]},
    )

    assert "Login Response" in repaired[0]["expected_result"]
    assert "$[0].expected_result" in fields


def test_deterministic_quality_repair_tombstones_contradicted_sfmea_without_rewriting_fact():
    repaired, fields = _deterministic_quality_claim_repair(
        [{"sfmea_id": "SFMEA-003", "failure_mode": "保留 NSG 未被拒绝"}],
        artifact="sfmea.json",
        quality_feedback={"issues": [{
            "artifact": "sfmea.json",
            "code": "row_source_claim_contradicted",
            "row_id": "SFMEA-003",
            "message": "源码已显式拒绝保留 NSG。",
        }]},
    )

    assert repaired == [{"sfmea_id": "SFMEA-003", "_delete": True}]
    assert fields == ["SFMEA-003._delete"]


def test_deterministic_schema_repair_normalizes_reused_black_box_diagnostics():
    from app.services.ai_staged_execution import _deterministic_schema_repair

    payload = [{"failure_diagnostics": "保留请求、响应和 target 日志。"}]
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "failure_diagnostics": {
                    "type": "array",
                    "items": {"type": "string"},
                }
            },
        },
    }

    repaired, fields = _deterministic_schema_repair(payload, schema)
    assert repaired[0]["failure_diagnostics"] == ["保留请求、响应和 target 日志。"]
    assert fields == ["$[0].failure_diagnostics"]


def test_quality_stage_reuse_normalizes_json_before_marking_completed(tmp_path):
    from app.services.ai_staged_execution import _existing_quality_stage_result

    artifact = tmp_path / "black_box_cases.json"
    artifact.write_text(
        json.dumps([{"failure_diagnostics": "保留请求和响应。"}], ensure_ascii=False),
        encoding="utf-8",
    )
    stage_dir = tmp_path / "stages" / "black_box_cases"
    stage_dir.mkdir(parents=True)
    result = _existing_quality_stage_result(
        plan={"quality_retry_feedback": {"affected_artifacts": []}},
        artifact_dir=tmp_path,
        stage_dir=stage_dir,
        stage={
            "id": "black_box_cases",
            "artifact": "black_box_cases.json",
            "output_contract": {
                "schema": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "failure_diagnostics": {
                                "type": "array",
                                "items": {"type": "string"},
                            }
                        },
                    },
                }
            },
        },
    )

    assert result is not None
    assert json.loads(artifact.read_text(encoding="utf-8"))[0]["failure_diagnostics"] == ["保留请求和响应。"]


def test_deterministic_quality_repair_does_not_override_source_backed_discovery_target_address_claim():
    from app.services.ai_staged_execution import _deterministic_quality_claim_repair

    repaired, fields = _deterministic_quality_claim_repair(
        [{
            "case_id": "BB-DISCOVERY-01",
            "scenario_name": "Discovery Login Response 包含 TargetAddress",
            "expected_result": "Login Response 数据段包含 TargetAddress=ip:port",
            "observability": ["Login Response 包含 TargetAddress"],
            "failure_diagnostics": ["检查 TargetAddress 格式"],
            "mapped_test_dir": "test/iscsi_tgt/chap/chap_discovery.sh",
        }],
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_discovery_target_address",
        }]},
    )

    case = repaired[0]
    assert case["scenario_name"] == "Discovery Login Response 包含 TargetAddress"
    assert case["expected_result"] == "Login Response 数据段包含 TargetAddress=ip:port"
    assert case["mapped_test_dir"] == "test/iscsi_tgt/chap/chap_discovery.sh"
    assert fields == []


def test_deterministic_quality_repair_corrects_unknown_key_contract_from_report_feedback():
    repaired, fields = _deterministic_quality_claim_repair(
        [{
            "case_id": "BC-UNKNOWN-01",
            "scenario_name": "Login with Unknown Key",
            "expected_result": "未知 key 被当作解析失败并断开连接",
            "observability": ["target log reports parse failure"],
            "failure_diagnostics": ["确认 Login 失败"],
        }],
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{
            # The combined Markdown report is the surface that caught this,
            # but the editable source of truth is the structured case.
            "artifact": "test_design.md",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_unknown_key_not_understood",
        }]},
    )

    case = repaired[0]
    assert "NotUnderstood" in case["expected_result"]
    assert "不得笼统断言" in case["expected_result"]
    assert "$[0].expected_result" in fields


def test_deterministic_quality_repair_corrects_consistency_finding_in_sfmea():
    repaired, fields = _deterministic_quality_claim_repair(
        [{
            "sfmea_id": "SFMEA-006",
            "failure_mode": "多阶段登录停滞无超时保护",
            "cause": "首个 Login PDU 后停止响应，30 秒 login_timer 会关闭连接",
            "detection": "等待 30 秒后确认连接关闭",
            "mitigation": "确认 timer 正常触发",
        }],
        artifact="sfmea.json",
        quality_feedback={"issues": [{
            "artifact": "sfmea.json",
            "code": "sfmea_evidence_contradiction",
            "constraint_id": "iscsi_login_timer_after_first_pdu",
            "risk_id": "SFMEA-006",
        }]},
    )

    row = repaired[0]
    assert "已注销" in row["failure_mode"]
    assert "不把 30 秒登录定时器清理作为预期" in row["detection"]
    assert "$[0].failure_mode" in fields


def test_deterministic_quality_repair_replaces_unrelated_iscsi_test_mappings_with_explicit_harness_gaps(tmp_path):
    from app.services.test_activity_contract import (
        _audit_professional_constraints,
        build_test_activity_contract,
    )

    repaired, fields = _deterministic_quality_claim_repair(
        [
            {
                "case_id": "BB-LOGIN-007",
                "scenario_name": "登录过程中断连后资源正确释放",
                "expected_result": "重新登录成功，target 无状态残留",
                "mapped_test_dir": "test/iscsi_tgt/reset/reset.sh",
            },
            {
                "case_id": "BB-LOGIN-008",
                "scenario_name": "登录延迟基准测试",
                "expected_result": "P95 登录延迟稳定",
                "mapped_test_dir": "test/iscsi_tgt/calsoft/calsoft.py",
            },
        ],
        artifact="black_box_cases.json",
        quality_feedback={"issues": [
            {
                "artifact": "black_box_cases.json",
                "code": "professional_fact_conflict",
                "constraint_id": "iscsi_reset_mapping_scope",
                "row_id": "BB-LOGIN-007",
            },
            {
                "artifact": "black_box_cases.json",
                "code": "professional_fact_conflict",
                "constraint_id": "iscsi_calsoft_mapping_scope",
                "row_id": "BB-LOGIN-008",
            },
        ]},
    )

    reset_case, latency_case = repaired
    assert "ai_suggested_unverified" in reset_case["mapped_test_dir"]
    assert "不覆盖 logout/relogin" in reset_case["mapped_test_dir"]
    assert "ai_suggested_unverified" in latency_case["mapped_test_dir"]
    assert "不得作为 Login 延迟基线" in latency_case["mapped_test_dir"]
    assert "$[0].mapped_test_dir" in fields
    assert "$[1].mapped_test_dir" in fields

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整灰盒测试设计",
        repo_path=str(repo),
    )
    issues = _audit_professional_constraints(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in repaired),
        contract,
    )
    constraint_ids = {issue.get("constraint_id") for issue in issues}
    assert "iscsi_reset_mapping_scope" not in constraint_ids
    assert "iscsi_calsoft_mapping_scope" not in constraint_ids


def test_deterministic_quality_repair_fixes_calsoft_latency_without_row_id():
    repaired, fields = _deterministic_quality_claim_repair(
        [{
            "case_id": "BB-PERF-001",
            "scenario_name": "Login Latency Baseline",
            "expected_result": "报告 Login P50/P95",
            "mapped_test_dir": "test/iscsi_tgt/calsoft/calsoft.py",
            "steps": ["measure login latency"],
        }],
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_calsoft_mapping_scope",
        }]},
    )

    assert "calsoft" not in repaired[0]["mapped_test_dir"].lower()
    assert "协议一致性套件" in repaired[0]["expected_result"]
    assert "$[0].mapped_test_dir" in fields


def test_deterministic_quality_repair_fixes_declared_mitigation_and_mapping_gaps():
    sfmea, sfmea_fields = _deterministic_quality_claim_repair(
        [{"sfmea_id": "SFMEA-03", "failure_mode": "解析失败后资源未收敛"}],
        artifact="sfmea.json",
        quality_feedback={"issues": [{
            "artifact": "sfmea.json",
            "code": "non_actionable_mitigation",
            "row_id": "SFMEA-03",
        }]},
    )
    cases, case_fields = _deterministic_quality_claim_repair(
        [{
            "case_id": "BB-CONCURRENCY-006",
            "mapped_test_dir": "test/iscsi_tgt/multiconnection/multiconnection.sh",
            "expected_result": "Both initiators successfully log in",
        }],
        artifact="black_box_cases.json",
        quality_feedback={"issues": [
            {
                "artifact": "black_box_cases.json",
                "code": "missing_test_directory_mapping",
                "row_id": "BB-CONCURRENCY-006",
            },
            {
                "artifact": "black_box_cases.json",
                "code": "professional_fact_conflict",
                "constraint_id": "iscsi_multiconnection_scenario_semantics",
                "row_id": "BB-CONCURRENCY-006",
            },
        ]},
    )

    assert "整改:" in sfmea[0]["mitigation"] and "验证:" in sfmea[0]["mitigation"]
    assert "$[0].mitigation" in sfmea_fields
    assert "multiconnection" not in cases[0]["mapped_test_dir"]
    assert "；" not in cases[0]["mapped_test_dir"]
    assert "不得将并发成功预设" in cases[0]["expected_result"]
    assert "$[0].mapped_test_dir" in case_fields


def test_deterministic_quality_repair_separates_duplicate_cid_from_mcs_capacity():
    repaired, fields = _deterministic_quality_claim_repair(
        [{
            "sfmea_id": "SFMEA-DUP-CID",
            "failure_mode": "重复 ISID/CID 连接未正确拒绝",
            "cause": "重复 CID 会触发 Too Many Connections (0x06)",
            "detection": "相同 ISID/CID 的第二个连接返回 Too Many Connections",
            "mitigation": "重复 CID 时返回 Too Many Connections",
        }],
        artifact="sfmea.json",
        quality_feedback={"issues": [{
            "artifact": "test_design.md",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_duplicate_cid_not_too_many_connections",
        }]},
    )

    row = repaired[0]
    assert "MaxConnections" in row["failure_mode"]
    assert "重复 CID" not in row["failure_mode"]
    assert "$[0].failure_mode" in fields


def test_deterministic_quality_repair_routes_audit_scenarios_to_rows_and_fixes_mcs_and_timer_contracts():
    payload = [
        {
            "case_id": "BC-03",
            "test_dimension": "resource_pressure",
            "scenario_name": "Reject additional connection when MaxConnectionsPerSession=1",
            "preconditions": ["first connection exists"],
            "steps": ["open a second connection"],
            "expected_result": "second connection is rejected",
            "observability": ["target log"],
            "failure_diagnostics": ["check connection"],
            "mapped_test_dir": "test/iscsi_tgt/multiconnection/multiconnection.sh",
        },
        {
            "case_id": "BC-04",
            "test_dimension": "timeout",
            "scenario_name": "Login hangs after first request; target should close connection after 30s",
            "preconditions": ["target running"],
            "steps": ["send first Login PDU then wait"],
            "expected_result": "after 30 seconds target closes the connection",
            "observability": ["tcp state"],
            "failure_diagnostics": ["connection did not close"],
        },
    ]
    feedback = {
        "issues": [
            {
                "artifact": "black_box_cases.json",
                "code": "missing_mcs_capable_client",
                "constraint_id": "iscsi_multiconnection_client_capability",
                "scenario": "TC-03 Reject additional connection when MaxConnectionsPerSession=1",
            },
            {
                "artifact": "black_box_cases.json",
                "code": "black_box_evidence_contradiction",
                "constraint_id": "iscsi_login_timer_after_first_pdu",
                "scenario": "TC-04 Login hangs after first request; target should close connection after 30s",
            },
        ]
    }

    repaired, fields = _deterministic_quality_claim_repair(
        payload,
        artifact="black_box_cases.json",
        quality_feedback=feedback,
    )

    mcs_case, timer_case = repaired
    assert "raw-PDU" in " ".join(mcs_case["preconditions"])
    assert "non-zero TSIH" in " ".join(mcs_case["steps"])
    assert "CID=2" in " ".join(mcs_case["steps"])
    assert "--scenario mcs" in " ".join(mcs_case["steps"])
    assert "support/iscsi_login_raw_pdu.py" in mcs_case["mapped_test_dir"]
    assert "当前实现" in timer_case["expected_result"]
    assert "不把 30 秒 login_timer 清理作为预期" in timer_case["expected_result"]
    assert "资源残留" in " ".join(timer_case["observability"])
    assert "$[0].steps" in fields
    assert "$[1].expected_result" in fields

    from app.services.test_activity_contract import _audit_combined_report_consistency

    report = "\n\n".join(
        "\n".join([
            f"### {row['scenario_name']}",
            "- 前置条件：" + "；".join(row.get("preconditions") or []),
            "- 操作步骤：" + "；".join(row.get("steps") or []),
            "- 预期结果：" + str(row.get("expected_result") or ""),
            "- 观测点：" + "；".join(row.get("observability") or []),
            "- 失败诊断：" + "；".join(row.get("failure_diagnostics") or []),
            "- 测试映射：" + str(row.get("mapped_test_dir") or ""),
        ])
        for row in repaired
    )
    constraint_ids = {
        issue.get("constraint_id")
        for issue in _audit_combined_report_consistency(report)
    }
    assert "iscsi_login_timer_after_first_pdu" not in constraint_ids
    assert "iscsi_multiconnection_client_capability" not in constraint_ids


def test_quality_repair_row_ids_resolve_tc_prefix_to_persisted_bc_case_id():
    row_ids = _quality_repair_row_ids(
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "missing_mcs_capable_client",
            "scenario": "TC-03 Reject additional connection when MaxConnectionsPerSession=1",
        }]},
        base_items=[{
            "case_id": "BC-03",
            "scenario_name": "Reject additional connection when MaxConnectionsPerSession=1",
        }],
    )

    assert row_ids == {"BC-03"}


def test_raw_pdu_harness_self_test_resolves_relative_artifact_dir(monkeypatch, tmp_path):
    from app.services.ai_staged_execution import _materialize_and_validate_raw_pdu_harness

    monkeypatch.chdir(tmp_path)
    result = _materialize_and_validate_raw_pdu_harness(Path("relative-run"))

    assert result["status"] == "passed"
    assert (tmp_path / "relative-run" / "support" / "iscsi_login_raw_pdu.py").is_file()


def test_deterministic_quality_repair_does_not_present_capacity_status_as_duplicate_cid_oracle():
    repaired, fields = _deterministic_quality_claim_repair(
        [{
            "case_id": "BC-DUP-CID",
            "scenario_name": "重复 CID 被拒绝",
            "steps": ["复用首连接 CID 发送第二个 Login Request"],
            "expected_result": "返回 Too Many Connections (0x06)",
            "failure_diagnostics": ["期望 0x0105"],
        }],
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_duplicate_cid_not_too_many_connections",
            "row_id": "BC-DUP-CID",
        }]},
    )

    row = repaired[0]
    assert "Too Many Connections" not in row["expected_result"]
    assert "实际响应判读" in row["expected_result"]
    assert "不同 CID" in row["failure_diagnostics"][1]
    assert "$[0].expected_result" in fields


def test_deterministic_quality_repair_does_not_label_login_detail_05_as_parameter_error():
    repaired, fields = _deterministic_quality_claim_repair(
        [{
            "case_id": "BC-PARAM-05",
            "scenario_name": "参数解析失败",
            "expected_result": "Login Response 返回 Parameter Error (0x05)",
        }],
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_login_status_detail_05",
            "row_id": "BC-PARAM-05",
        }]},
    )

    row = repaired[0]
    assert "Parameter Error" not in row["expected_result"]
    assert "不预设" in row["expected_result"]
    assert "0x05" not in row["expected_result"]
    assert "$[0].expected_result" in fields


def test_deterministic_quality_repair_keeps_chap_response_csg_path_dependent():
    repaired, fields = _deterministic_quality_claim_repair(
        [{
            "case_id": "BC-CHAP-01",
            "scenario_name": "CHAP Login",
            "steps": [
                "Login Request CSG=0, NSG=1, T=0; Login Response CSG=0, T=0",
                "Login Request CSG=1, NSG=3, T=1; Login Response CSG=1, NSG=3, T=1",
            ],
        }],
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_chap_request_response_flags",
        }]},
    )

    steps = " ".join(repaired[0]["steps"])
    assert "CSG=0 或 CSG=1" in steps
    assert "响应继承请求" in steps
    assert "$[0].steps" in fields


def test_deterministic_quality_repair_keeps_final_login_csg_path_dependent():
    repaired, fields = _deterministic_quality_claim_repair(
        [{
            "case_id": "BB-NORMAL-001",
            "scenario_name": "Discovery Login",
            "steps": ["final response CSG=1, NSG=3, T=1"],
            "expected_result": "final response CSG=1, NSG=3, T=1",
        }],
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_final_login_stage_alternatives",
            "row_id": "BB-NORMAL-001",
        }]},
    )

    steps = " ".join(repaired[0]["steps"])
    assert "CSG=0 与 CSG=1" in steps
    assert "T=1、NSG=3" in repaired[0]["expected_result"]
    assert "$[0].expected_result" in fields


def test_deterministic_quality_repair_corrects_final_login_response_stage_bits():
    repaired, fields = _deterministic_quality_claim_repair(
        [{
            "case_id": "BB-01",
            "scenario_name": "正常安全协商登录",
            "expected_result": "Login Response 最终 T=1、CSG=3、NSG=3",
        }],
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_login_response_stage_bits",
            "row_id": "BB-01",
        }]},
    )

    assert "CSG=3" not in repaired[0]["expected_result"]
    assert "CSG=0 和 CSG=1" in repaired[0]["expected_result"]
    assert "T=1、NSG=3" in repaired[0]["expected_result"]
    assert "$[0].expected_result" in fields


def test_deterministic_quality_repair_resolves_rendered_headings_and_repairs_iscsi_cases():
    """Task-level audits name rendered headings, not always stable JSON ids."""
    repaired, fields = _deterministic_quality_claim_repair(
        [
            {
                "case_id": "BB-01",
                "scenario_name": "正常登录进入 Full Feature Phase",
                "steps": ["最终响应 CSG=3、NSG=3、T=1"],
                "expected_result": "Login Response 最终 CSG=3、NSG=3、T=1",
            },
            {
                "case_id": "BB-07",
                "scenario_name": "Authorization Failure",
                "steps": ["CHAP_N、CHAP_I、CHAP_R 全部使用 base64 编码"],
                "expected_result": "返回 Authorization Failure",
            },
            {
                "case_id": "BB-23",
                "scenario_name": "登录超时后连接关闭",
                "expected_result": "首个 Login PDU 后 30 秒 login_timer 必然关闭连接",
            },
        ],
        artifact="black_box_cases.json",
        quality_feedback={"issues": [
            {
                "artifact": "black_box_cases.json",
                "code": "professional_fact_conflict",
                "constraint_id": "iscsi_login_response_stage_bits",
                "section_heading": "BB-01 正常登录进入 Full Feature Phase",
            },
            {
                "artifact": "black_box_cases.json",
                "code": "professional_fact_conflict",
                "constraint_id": "iscsi_chap_wire_encoding",
                "section_heading": "BB-07 Authorization Failure",
            },
            {
                "artifact": "black_box_cases.json",
                "code": "black_box_evidence_contradiction",
                "constraint_id": "iscsi_login_timer_after_first_pdu",
                "scenario": "BB-23 登录超时后连接关闭",
            },
        ]},
    )

    by_id = {row["case_id"]: row for row in repaired}
    assert "CSG=3" not in by_id["BB-01"]["expected_result"]
    assert "CSG=3" not in " ".join(by_id["BB-01"]["observability"])
    assert "CSG!=3" not in " ".join(by_id["BB-01"]["failure_diagnostics"])
    chap = " ".join(by_id["BB-07"]["steps"])
    assert "CHAP_N 为普通用户名字符串" in chap
    assert "CHAP_I 为十进制标识符" in chap
    assert "CHAP_R/CHAP_C" in chap
    assert "不会保证首个 Login PDU 后 30 秒" in by_id["BB-23"]["expected_result"]
    assert "资源残留" in by_id["BB-23"]["expected_result"]
    assert "$[0].expected_result" in fields
    assert "$[1].steps" in fields
    assert "$[2].expected_result" in fields


def test_final_quality_repair_corrects_first_login_pdu_timer_strategy(tmp_path):
    strategy_path = tmp_path / "test_strategy.md"
    strategy_path.write_text(
        "| H-01 | 首个 Login PDU 后登录定时器注销，后续停滞无超时清理 |\\n"
        "| G-01 | 首个 Login PDU 后登录定时器注销行为未在测试中覆盖 |\\n"
        "| T-01 | 首个 Login PDU 后停滞超时测试 |\\n",
        encoding="utf-8",
    )

    changed = materialize_final_deterministic_quality_repairs(
        tmp_path,
        quality_feedback={"issues": [{
            "artifact": "test_strategy.md",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_login_timer_after_first_pdu",
        }]},
    )

    repaired = strategy_path.read_text(encoding="utf-8")
    assert "后续停滞无超时清理" not in repaired
    assert "不把 30 秒登录定时器清理作为预期" in repaired
    assert changed["test_strategy.md"] == ["iscsi_login_timer_after_first_pdu"]


def test_deterministic_quality_repair_replaces_fuzz_mapping_with_black_box_harness():
    repaired, fields = _deterministic_quality_claim_repair(
        [{
            "case_id": "BC-VERSION-01",
            "scenario_name": "Unsupported Version",
            "mapped_test_dir": "test/app/fuzz/iscsi_fuzz/iscsi_fuzz.c",
            "steps": ["发送带非法版本字段的 Login Request PDU"],
        }],
        artifact="black_box_cases.json",
        quality_feedback={"issues": [{
            "artifact": "black_box_cases.json",
            "code": "black_box_boundary_violation",
            "row_id": "BC-VERSION-01",
        }]},
    )

    case = repaired[0]
    assert case["mapped_test_dir"].startswith("ai_suggested_unverified:")
    assert "$[0].mapped_test_dir" in fields


def test_deterministic_quality_repair_replaces_login_fuzzer_mapping_in_sfmea(tmp_path):
    from app.services.ai_staged_execution import _deterministic_quality_claim_repair
    from app.services.test_activity_contract import (
        _audit_professional_constraints,
        build_test_activity_contract,
    )

    repaired, fields = _deterministic_quality_claim_repair(
        [{
            "sfmea_id": "SFMEA-LOGIN-FUZZ",
            "failure_mode": "并发登录请求导致状态覆盖",
            "test_mapping": "test/app/fuzz/iscsi_fuzz/iscsi_fuzz.c（需扩展并发登录场景）",
            "source_evidence": ["SRC-02:L1301"],
        }],
        artifact="sfmea.json",
        quality_feedback={"issues": [{
            "artifact": "sfmea.json",
            "code": "professional_fact_conflict",
            "constraint_id": "iscsi_fuzzer_skips_login_opcode",
            "row_id": "SFMEA-LOGIN-FUZZ",
        }]},
    )

    row = repaired[0]
    assert row["test_mapping"].startswith("ai_suggested_unverified:")
    assert "明确跳过 LOGIN opcode" in row["test_mapping"]
    assert "$[0].test_mapping" in fields

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(target="iSCSI Login 测试设计", repo_path=str(repo))
    issues = _audit_professional_constraints(
        json.dumps(row, ensure_ascii=False),
        contract,
    )
    assert "iscsi_fuzzer_skips_login_opcode" not in {
        issue.get("constraint_id") for issue in issues
    }


def test_sfmea_floor_uses_distinct_verified_symbols_after_tombstones():
    from app.services.ai_staged_execution import _complete_minimum_sfmea_hypotheses

    rows = [{"sfmea_id": f"SFMEA-{index:02d}", "failure_mode": "登录异常路径处理错误导致会话状态异常"}
            for index in range(1, 9)]
    catalog = [
        {
            "evidence_id": f"SRC-{index}",
            "path": "lib/iscsi/iscsi.c",
            "symbol": f"iscsi_login_handler_{index}",
            "lines": f"L{index}",
            "quote": "unclassified source anchor",
        }
        for index in range(1, 5)
    ]

    completed, fields = _complete_minimum_sfmea_hypotheses(
        rows,
        minimum_items=12,
        product_claim_catalog=catalog,
    )

    assert len(completed) == 12
    assert len(fields) == 4
    assert len({row["failure_mode"] for row in completed}) == 5
