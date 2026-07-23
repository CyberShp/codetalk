"""Built-in editable workflow presets for the Agent Workbench."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.source_driven_test_design import (
    MINDMAP_ARTIFACTS,
    SOURCE_DRIVEN_V2_ARTIFACTS,
)
from app.services.workflow_dsl import WorkflowDefinition, WorkflowStore, validate_workflow_definition


SOURCE_SCOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["scope_id", "query", "repo", "discovery", "files", "entry_points"],
    "properties": {
        "scope_id": {"type": "string"},
        "query": {"type": "string"},
        "repo": {"type": "string"},
        "discovery": {
            "type": "object",
            "required": ["provider", "method", "file_count"],
            "properties": {
                "provider": {"type": "string"},
                "method": {"type": "string"},
                "file_count": {"type": "integer"},
            },
            "additionalProperties": True,
        },
        "files": {"type": "array", "items": {"type": "string"}},
        "entry_points": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["file_path", "symbol", "reason"],
                "properties": {
                    "file_path": {"type": "string"},
                    "symbol": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "additionalProperties": True,
            },
        },
    },
    "additionalProperties": True,
}


EVIDENCE_CARDS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "minItems": 1,
    "items": {
        "type": "object",
        "required": [
            "evidence_id",
            "kind",
            "file_path",
            "start_line",
            "end_line",
            "excerpt",
            "symbols",
            "reason",
            "sha256",
            "source",
        ],
        "properties": {
            "evidence_id": {"type": "string"},
            "kind": {"type": "string"},
            "file_path": {"type": "string"},
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
            "excerpt": {"type": "string", "minLength": 1},
            "symbols": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "reason": {"type": "string"},
            "sha256": {"type": "string"},
            "line_count": {"type": "integer"},
            "source": {"type": "string"},
        },
        "additionalProperties": True,
    },
}


TECHNICAL_CLAIMS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "minItems": 1,
    "maxItems": 1,
    "items": {
        "type": "object",
        "required": ["claim_id", "type", "statement", "evidence"],
        "properties": {
            "claim_id": {"type": "string", "minLength": 1},
            "type": {"type": "string", "minLength": 1},
            "statement": {"type": "string", "minLength": 1},
            "evidence": {
                "type": "array",
                "minItems": 1,
                "maxItems": 1,
                "items": {
                    "type": "object",
                    "required": ["evidence_id", "path", "quote"],
                    "properties": {
                        "evidence_id": {"type": "string", "minLength": 1},
                        "path": {"type": "string", "minLength": 1},
                        "symbol": {"type": "string"},
                        "lines": {"type": "string"},
                        "quote": {"type": "string", "minLength": 1},
                    },
                    "additionalProperties": True,
                },
            },
        },
        "additionalProperties": True,
    },
}


SFMEA_SCHEMA: dict[str, Any] = {
    "type": "array",
    "minItems": 1,
    "items": {
        "type": "object",
        "required": [
            "sfmea_id",
            "failure_mode",
            "mechanism",
            "trigger_condition",
            "cause",
            "effect",
            "local_effect",
            "upstream_effect",
            "downstream_effect",
            "final_effect",
            "latent",
            "detection",
            "existing_controls",
            "control_gaps",
            "severity",
            "occurrence",
            "detection_score",
            "rpn",
            "score_explanation",
            "mitigation",
            "recovery_verification",
            "source_evidence",
            "test_mapping",
        ],
        "properties": {
            "sfmea_id": {"type": "string"},
            "module": {"type": "string"},
            "file_path": {"type": "string"},
            "failure_mode": {"type": "string"},
            "mechanism": {"type": "string", "minLength": 1},
            "trigger_condition": {"type": "string", "minLength": 1},
            "cause": {"type": "string"},
            "effect": {"type": "string"},
            "local_effect": {"type": "string", "minLength": 1},
            "upstream_effect": {"type": "string", "minLength": 1},
            "downstream_effect": {"type": "string", "minLength": 1},
            "final_effect": {"type": "string", "minLength": 1},
            "latent": {"type": "string", "minLength": 1},
            "detection": {"type": "string"},
            "existing_controls": {"type": "string", "minLength": 1},
            "control_gaps": {"type": "string", "minLength": 1},
            "severity": {"type": "integer"},
            "occurrence": {"type": "integer"},
            "detection_score": {"type": "integer"},
            "rpn": {"type": "integer"},
            "score_explanation": {"type": "string"},
            "mitigation": {"type": "string"},
            "recovery_verification": {"type": "string", "minLength": 1},
            "source_evidence": {"type": "array", "items": {"type": "string"}},
            "test_mapping": {"type": "string"},
            "evidence": {"type": "object"},
            "technical_claims": TECHNICAL_CLAIMS_SCHEMA,
        },
        "additionalProperties": True,
    },
}


BLACK_BOX_CASES_SCHEMA: dict[str, Any] = {
    "type": "array",
    "minItems": 1,
    "items": {
        "type": "object",
        "required": [
            "case_id",
            "risk_ids",
            "test_dimension",
            "scenario_name",
            "preconditions",
            "steps",
            "expected_result",
            "observability",
            "failure_diagnostics",
            "mapped_test_dir",
            "source_or_test_evidence",
        ],
        "properties": {
            "case_id": {"type": "string"},
            "risk_ids": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "minLength": 1},
            },
            "test_dimension": {"type": "string"},
            "scenario_name": {"type": "string"},
            "preconditions": {"type": "array", "items": {"type": "string"}},
            "steps": {"type": "array", "items": {"type": "string"}},
            "expected_result": {"type": "string"},
            "oracle_basis": {"type": "string"},
            "observability": {"type": "array", "items": {"type": "string"}},
            "failure_diagnostics": {"type": "array", "items": {"type": "string"}},
            "mapped_test_dir": {"type": "string"},
            "source_or_test_evidence": {"type": "array", "items": {"type": "string"}},
            "technical_claims": TECHNICAL_CLAIMS_SCHEMA,
        },
        "additionalProperties": True,
    },
}


RISK_FINDINGS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["finding_id", "file_path", "risk", "summary", "source"],
        "properties": {
            "finding_id": {"type": "string"},
            "file_path": {"type": "string"},
            "function": {"type": "string"},
            "resource": {"type": "string"},
            "risk_pattern": {"type": "string"},
            "risk": {"type": "string"},
            "summary": {"type": "string"},
            "severity": {"type": "string"},
            "confidence": {"type": "string"},
            "source": {"type": "string"},
        },
        "additionalProperties": True,
    },
}


MR_SNAPSHOT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["kind", "source", "status", "summary"],
    "properties": {
        "kind": {"type": "string"},
        "source": {"type": "string"},
        "status": {"type": "string"},
        "mr_link": {"type": "string"},
        "repo": {"type": "string"},
        "changed_files_count": {"type": "integer"},
        "changed_files": {"type": "array"},
        "summary": {"type": "string"},
    },
    "additionalProperties": True,
}


IMPACT_SCOPE_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["impact_id", "file_path", "summary", "impact", "risk", "source"],
        "properties": {
            "impact_id": {"type": "string"},
            "file_path": {"type": "string"},
            "symbol": {"type": "string"},
            "status": {"type": "string"},
            "module": {"type": "string"},
            "summary": {"type": "string"},
            "impact": {"type": "string"},
            "risk": {"type": "string"},
            "test_scope": {"type": "string"},
            "source": {"type": "string"},
            "evidence": {"type": "object"},
        },
        "additionalProperties": True,
    },
}


LEGACY_SOURCE_FLOW_REQUIRED_ARTIFACTS = [
    "source_scope.json",
    "evidence_cards.json",
    "flow_map.md",
    "sfmea.json",
    "black_box_cases.json",
]

SOURCE_FLOW_REQUIRED_ARTIFACTS = [
    *LEGACY_SOURCE_FLOW_REQUIRED_ARTIFACTS,
    *SOURCE_DRIVEN_V2_ARTIFACTS,
]

TEST_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["scope", "risks", "activities", "entry_criteria", "exit_criteria"],
    "properties": {
        "scope": {"type": "array"},
        "risks": {"type": "array"},
        "activities": {"type": "array"},
        "entry_criteria": {"type": "array"},
        "exit_criteria": {"type": "array"},
    },
    "additionalProperties": True,
}

EXECUTION_MATRIX_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["batches"],
    "properties": {
        "batches": {"type": "array"},
        "environments": {"type": "array"},
        "observability": {"type": "array"},
        "rerun_policy": {"type": "array"},
    },
    "additionalProperties": True,
}

COVERAGE_GAP_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["gaps", "recommendations"],
    "properties": {
        "gaps": {"type": "array"},
        "recommendations": {"type": "array"},
        "source_evidence": {"type": "array"},
    },
    "additionalProperties": True,
}

ORIGINAL_CORE_WORKFLOW_PRESET_IDS = (
    "module_analysis",
    "resource_leak_hunt",
    "mr_blackbox_test",
    "patch_impact_review",
)

CORE_WORKFLOW_PRESET_IDS = (
    *ORIGINAL_CORE_WORKFLOW_PRESET_IDS,
    "source_flow_sfmea_blackbox",
    "testing_activity_orchestration",
    "basic_source_report_codex",
    "basic_source_design_report_builtin",
)

COMMON_TEST_SCENARIO_PRESET_IDS = (
    "nvmf_connect_io_blackbox",
    "iscsi_login_session_blackbox",
    "bdev_io_reset_blackbox",
    "rpc_config_negative_blackbox",
    "reactor_thread_poller_blackbox",
    "nvmf_disconnect_reconnect_blackbox",
    "iscsi_auth_failure_blackbox",
    "bdev_failover_resource_blackbox",
    "blobstore_ftl_recovery_blackbox",
    "vhost_vfio_user_lifecycle_blackbox",
    "nvmf_tcp_tls_auth_blackbox",
    "bdev_qos_latency_blackbox",
    "jsonrpc_concurrency_idempotency_blackbox",
    "app_startup_shutdown_smoke_blackbox",
    "nvme_ctrlr_hotplug_reset_blackbox",
    "storage_capacity_enospc_recovery_blackbox",
    "nvmf_rdma_transport_blackbox",
    "iscsi_digest_multi_connection_blackbox",
    "bdev_hotremove_io_error_blackbox",
    "blobstore_metadata_powerfail_blackbox",
    "rpc_security_authz_blackbox",
    "fault_injection_timeout_recovery_blackbox",
    "concurrent_operations_stress_blackbox",
    "observability_diagnostics_blackbox",
    "config_compatibility_rollback_blackbox",
    "lvol_snapshot_clone_blackbox",
    "raid_degraded_rebuild_blackbox",
    "nvme_multipath_failover_blackbox",
    "env_hugepage_memory_blackbox",
    "spdk_cli_rpc_smoke_blackbox",
    "target_crash_restart_blackbox",
    "multi_client_isolation_blackbox",
    "queue_depth_backpressure_blackbox",
    "io_error_injection_retry_blackbox",
    "config_reload_persistence_blackbox",
    "long_running_resource_leak_blackbox",
    "basic_lifecycle_smoke_blackbox",
    "io_stress_performance_blackbox",
    "failure_recovery_soak_blackbox",
    "transport_network_partition_blackbox",
    "data_integrity_corruption_blackbox",
    "upgrade_compatibility_persistence_blackbox",
    "telemetry_metrics_regression_blackbox",
    "nvmf_subsystem_namespace_acl_blackbox",
    "iscsi_lun_resize_hotplug_blackbox",
    "bdev_crypto_integrity_blackbox",
    "scheduler_qos_fairness_blackbox",
    "backup_restore_integrity_blackbox",
    "nvme_discovery_log_blackbox",
    "iscsi_portal_failover_blackbox",
    "bdev_zone_append_blackbox",
    "jsonrpc_partial_rollback_blackbox",
    "vfio_user_hotplug_reconnect_blackbox",
    "lvol_thin_snapshot_blackbox",
    "api_contract_negative_blackbox",
    "state_persistence_restart_blackbox",
    "concurrency_isolation_race_blackbox",
    "performance_capacity_regression_blackbox",
    "security_access_control_blackbox",
)

ACTIVE_BUILTIN_WORKFLOW_PRESET_IDS = (
    "source_flow_sfmea_blackbox",
    "basic_source_report_codex",
    "basic_source_design_report_builtin",
)

BUILTIN_WORKFLOW_PRESET_ALIASES = {
    "basic_source_report_claude": "basic_source_report_codex",
}


def canonical_builtin_workflow_preset_id(preset_id: str) -> str:
    value = str(preset_id or "")
    return BUILTIN_WORKFLOW_PRESET_ALIASES.get(value, value)


_BASIC_ISCSI_REPORT_GOAL = (
    "针对 SPDK iSCSI login 进行源码证据驱动的测试分析。必须先读取工作空间源码，"
    "优先检查已有 GitNexus/CGC 产物，并核验真实文件、符号、行号和现有测试目录；"
    "如果提供开发设计文档，还必须逐条吸收其中的设计约束、外部行为与未决问题。"
    "必须交付 report.md，以及任务输出契约列出的 source_analysis.md、source_scope.json、"
    "evidence_cards.json、flow_cards.json、sfmea.json 和 black_box_cases.json；终端输出仅用于进度。"
    "除运行时已由 CodeTalk 创建的诊断文件外，不得自行创建 artifact manifest、claim ledger、"
    "额外报告或未声明 JSON；完成这七份交付件的结构自检后必须结束，不得通过新增文件重复自检。"
    "report.md 至少包含：分析范围与证据缺口、"
    "关键源码证据、主流程与异常/恢复流程、SFMEA、可由测试人员直接执行的黑盒测试用例。"
    "SFMEA 必须包含 failure mode、cause、effect、detection、severity、occurrence、"
    "detection score、RPN、mitigation 和证据映射；黑盒用例必须包含前置条件、外部步骤、"
    "预期结果、观测点、失败诊断和真实测试目录映射，不得把内部函数调用写成测试步骤。"
    "现有测试文件只能证明其实际覆盖的场景；若协议位、非法 PDU、MCS 或异常时序需要新增 "
    "raw-PDU harness，必须输出受控 harness 设计契约：隔离前置条件、输入类别、预期外部结果、"
    "观测点、清理步骤和人工批准要求，不得声称该用例已可直接执行。"
    "报告必须记录 Git revision/commit。完整场景矩阵必须分别覆盖 T+C 非法组合、非法 NSG、"
    "Unsupported Version、Authorization Failure、C=1 跨 PDU 分片后以 C=0 收尾，以及 CHAP "
    "错误 CHAP_R、未知用户、参数顺序、算法、缺失/错误编码和 mutual CHAP 双向配置负向场景。"
    "凡声明协议位或状态码预期，必须给出证据来源、可观测字段和待执行的验证方法。凡需要 raw-PDU，"
    "仅描述其受控执行所需的 harness 能力与验收条件；可执行流量构造只能在被明确批准的后续测试活动中生成。"
    "MCS 容量用例必须在 target 启动前使用 iscsi_set_options -c 配置 MaxConnectionsPerSession，"
    "保留首连接并使用相同 TSIH/不同 CID；映射 multiconnection.sh 时必须声明仅限隔离测试盘并"
    "提示数据销毁风险。SFMEA 前必须定义 Severity/Occurrence/Detection 的 1-10 评分标尺、"
    "RPN 优先级阈值，并说明 Occurrence 来自缺陷历史、登录流量分布或测试统计；没有数据时"
    "必须标明待采样而不是伪造发生率。"
    "质量下限：至少引用 6 个可核验源码文件和 4 个可核验测试文件，至少输出 12 条不同的 "
    "SFMEA 风险项与 12 条原子化黑盒用例；任一数量不足都不得宣称完成。"
    "必须把实现事实、设计期望和证据缺口明确分开；协议状态码、超时数值、日志原文、"
    "性能阈值和连接关闭行为只有在证据片段直接支持时才能作为事实，否则必须标为待验证。"
    "最终报告不得把设计文档中的期望反写成已实现事实，也不得引用 iSCSI 范围外的测试路径。"
)


_BASIC_ISCSI_EVIDENCE_HINTS = [
    {"path": "lib/iscsi/iscsi.c", "term": "iscsi_auth_params", "label": "CHAP negotiation"},
    {"path": "lib/iscsi/iscsi.c", "term": "selected algorithm is 5 (MD5)", "label": "CHAP algorithm selection"},
    {"path": "lib/iscsi/iscsi.c", "term": "compare MD5 digest", "label": "CHAP response verification"},
    {"path": "lib/iscsi/iscsi.c", "term": "Initiator wants to use mutual CHAP", "label": "mutual CHAP rejection"},
    {"path": "lib/iscsi/iscsi.c", "term": "required mutual CHAP", "label": "mutual CHAP requirement"},
    {"path": "lib/iscsi/iscsi.c", "term": "iscsi_conn_login_pdu_err_complete", "label": "login error completion"},
    {"path": "lib/iscsi/iscsi.c", "term": "iscsi_op_login_rsp_handle_csg_bit", "label": "login stage transition"},
    {"path": "lib/iscsi/iscsi.c", "term": "iscsi_pdu_payload_op_login", "label": "login payload entry"},
    {"path": "lib/iscsi/iscsi.c", "term": "spdk_poller_unregister(&conn->login_timer)", "label": "login timer cancellation"},
    {"path": "lib/iscsi/iscsi.c", "term": "rsph->status_detail = ISCSI_LOGIN_AUTHENT_FAIL", "label": "authentication failure response"},
    {"path": "lib/iscsi/iscsi.c", "term": "iscsi_op_login_response", "label": "login response"},
    {"path": "lib/iscsi/conn.c", "term": "login_timeout", "label": "login timer callback"},
    {"path": "lib/iscsi/iscsi.h", "term": "ISCSI_LOGIN_TIMEOUT", "label": "login timeout constant"},
    {"path": "include/spdk/iscsi_spec.h", "term": "ISCSI_LOGIN_AUTHENT_FAIL", "label": "wire authentication failure code"},
    {"path": "lib/iscsi/tgt_node.c", "term": "iscsi_check_chap_params", "label": "CHAP configuration validation"},
    {"path": "lib/iscsi/iscsi_subsystem.c", "term": "iscsi_check_chap_params", "label": "CHAP option validation"},
    {"path": "test/app/fuzz/iscsi_fuzz/iscsi_fuzz.c", "term": "fuzz_iscsi_send_login_request", "label": "login wire-format test seed"},
    {"path": "test/iscsi_tgt/chap/chap_mutual_not_set.sh", "term": "configuring initiator with biderectional authentication", "label": "mutual CHAP negative test"},
    {"path": "test/iscsi_tgt/chap/chap_common.sh", "term": "config_chap_credentials_for_target", "label": "CHAP test fixture"},
    {"path": "test/iscsi_tgt/digests/digests.sh", "term": "HeaderDigest", "label": "digest negotiation tests"},
    {"path": "test/iscsi_tgt/multiconnection/multiconnection.sh", "term": "CONNECTION_NUMBER", "label": "multi-connection test"},
    {"path": "test/iscsi_tgt/rpc_config/rpc_config.py", "term": "mutual_chap", "label": "RPC configuration test"},
    {"path": "test/iscsi_tgt/login_redirection/login_redirection.sh", "term": "redirect", "label": "login redirection test"},
    {"path": "test/iscsi_tgt/common.sh", "term": "waitforiscsidevices", "label": "external login fixture"},
    {"path": "lib/iscsi/conn.c", "term": "login_timer = SPDK_POLLER_REGISTER", "label": "login timer registration"},
    {"path": "lib/iscsi/conn.c", "term": "_iscsi_conn_destruct", "label": "connection cleanup chain"},
    {"path": "lib/iscsi/iscsi_subsystem.c", "term": "conn->state == ISCSI_CONN_STATE_EXITING", "label": "poll-group destruction trigger"},
    {"path": "lib/iscsi/iscsi.c", "term": "this PDU should be sent without digest", "label": "login response digest exception"},
    {"path": "lib/iscsi/iscsi.c", "term": "append_iscsi_sess", "label": "existing-session MCS path"},
    {"path": "lib/iscsi/iscsi.c", "term": "sess->connections >= sess->MaxConnections", "label": "MCS capacity boundary"},
    {"path": "lib/iscsi/iscsi.c", "term": "TODO: need a mutex", "label": "MCS synchronization gap"},
    {"path": "lib/iscsi/param.c", "term": "iscsi_copy_param2var", "label": "post-login digest activation"},
    {"path": "lib/iscsi/iscsi.c", "term": "data digest error", "label": "full-feature data digest failure"},
    {"path": "lib/iscsi/iscsi.c", "term": "header digest error", "label": "full-feature header digest failure"},
    {"path": "test/app/fuzz/iscsi_fuzz/iscsi_fuzz.c", "term": "LOGIN and LOGOUT opcodes are ignored here", "label": "fuzzer login coverage boundary"},
    {"path": "include/spdk/iscsi_spec.h", "term": "ISCSI_LOGIN_UNSUPPORTED_VERSION", "label": "unsupported version status detail"},
    {"path": "include/spdk/iscsi_spec.h", "term": "ISCSI_LOGIN_AUTHORIZATION_FAIL", "label": "authorization failure status detail"},
    {"path": "lib/iscsi/iscsi.c", "term": "Set T/CSG/NSG to reserved if login error", "label": "error response flag clearing"},
    {"path": "lib/iscsi/iscsi.c", "term": "case ISCSI_FULL_FEATURE_PHASE", "label": "full-feature login request rejection"},
    {"path": "scripts/rpc.py", "term": "--max-connections-per-session", "label": "MCS target startup configuration"},
    {"path": "lib/iscsi/param.c", "term": "iscsi_parse_params", "label": "C-bit parameter reassembly"},
    {"path": "lib/iscsi/iscsi.c", "term": "iscsi_op_login_session_normal", "label": "normal session and reinstatement semantics"},
    {"path": "lib/iscsi/conn.c", "term": "iscsi_conn_info_json", "label": "public connection RPC fields"},
    {"path": "lib/iscsi/param.c", "term": "iscsi_parse_param", "label": "bounded parameter parsing"},
]


def _basic_report_preset(*, include_design: bool, provider: str) -> dict[str, Any]:
    preset_id = (
        "basic_source_design_report_builtin"
        if include_design
        else "basic_source_report_codex"
    )
    name = (
        "基础源码 + 设计文档报告（内置模型）"
        if include_design
        else "基础源码报告（Codex CLI）"
    )
    description = (
        "以已建工作空间和一份设计文档为输入，由内置模型生成流程、SFMEA 与黑盒用例报告。"
        if include_design
        else "仅以已建工作空间为输入，由 Codex CLI 生成流程、SFMEA 与黑盒用例报告。"
    )
    inputs: list[dict[str, Any]] = [
        {
            "id": "repo_path",
            "label": "源码工作空间",
            "type": "directory",
            "required": True,
            "resolver": "workspace",
            "role": "SPDK 源码工作空间",
        }
    ]
    input_ports: list[dict[str, Any]] = [
        {"id": "repo_path", "type": "directory", "required": True}
    ]
    if include_design:
        inputs.append(
            {
                "id": "design_doc",
                "label": "开发设计文档",
                "type": "file",
                "required": True,
                "resolver": "local",
                "role": "iSCSI login 设计约束与外部行为",
            }
        )
        input_ports.append(
            {"id": "design_doc", "type": "file", "required": True}
        )
    execution_subject = "builtin_llm" if provider == "builtin-llm" else "agent"
    execution_label = {
        "builtin-llm": "内置模型",
        "codex": "Codex CLI",
        "claude-code": "Claude Code",
    }.get(provider, provider)
    return {
        "id": preset_id,
        "name": name,
        "description": description,
        "definition": {
            "id": preset_id,
            "name": name,
            "description": description,
            "version": 1,
            "artifact_contract_version": "v3",
            "execution_subject": execution_subject,
            "execution_label": execution_label,
            "user_message": (
                f"{execution_label} 将读取源码"
                f"{'与开发设计文档' if include_design else ''}并生成一份可下载报告。"
            ),
            "inputs": inputs,
            "steps": [
                {
                    "id": "analyze",
                    "type": "agent_task",
                    "provider": provider,
                    **({"execution_mode": "staged"} if provider == "builtin-llm" else {}),
                    "skills": [
                        "source-evidence-first",
                        "storage-flow-analysis",
                        "sfmea",
                        "black-box-test-design",
                        "artifact-contract",
                    ],
                    "input_ports": input_ports,
                    "report_sections": ["流程", "SFMEA", "黑盒测试用例"],
                    "source_context_limit": 44,
                    "source_context_min_test_files": 8,
                    "source_analysis_max_files": 44,
                    "source_analysis_max_evidence_anchors": 44,
                    "source_context_search_roots": [
                        "lib/iscsi",
                        "include/spdk",
                        "test/iscsi_tgt",
                        "test/app/fuzz/iscsi_fuzz",
                    ],
                    "source_evidence_hints": _BASIC_ISCSI_EVIDENCE_HINTS,
                    "goal": _BASIC_ISCSI_REPORT_GOAL,
                    "required_artifacts": [
                        "source_analysis.md",
                        "source_scope.json",
                        "evidence_cards.json",
                        "flow_cards.json",
                        "sfmea.json",
                        "black_box_cases.json",
                    ],
                    "timeout_sec": 1200,
                    "idle_timeout_sec": 300,
                }
            ],
            "outputs": [
                {"id": "source_scope", "label": "源码范围", "type": "json", "from": "analyze", "artifact": "source_scope.json", "schema": SOURCE_SCOPE_SCHEMA},
                {"id": "evidence_cards", "label": "代码证据", "type": "json", "from": "analyze", "artifact": "evidence_cards.json", "schema": EVIDENCE_CARDS_SCHEMA},
                {"id": "flow_cards", "label": "流程卡片", "type": "json", "from": "analyze", "artifact": "flow_cards.json", "schema": {"type": "object", "required": ["items"], "properties": {"items": {"type": "array"}}}},
                {"id": "sfmea", "label": "SFMEA", "type": "json", "from": "analyze", "artifact": "sfmea.json", "schema": SFMEA_SCHEMA},
                {"id": "black_box_cases", "label": "黑盒测试用例", "type": "test_cases", "from": "analyze", "artifact": "black_box_cases.json", "schema": BLACK_BOX_CASES_SCHEMA},
                {
                    "id": "report",
                    "label": "分析报告",
                    "type": "combined_test_report",
                    "from": "analyze",
                    "artifact": "report.md",
                    "min_sfmea_rows": 12,
                    "min_black_box_cases": 12,
                    "required_evidence_terms": [
                        "iscsi_auth_params",
                        "iscsi_conn_login_pdu_err_complete",
                        "iscsi_pdu_payload_op_login",
                        "ISCSI_LOGIN_AUTHENT_FAIL",
                        "ISCSI_LOGIN_TIMEOUT",
                        "test/iscsi_tgt/chap/chap_mutual_not_set.sh",
                        "test/iscsi_tgt/multiconnection/multiconnection.sh",
                    ],
                    "forbidden_evidence_path_prefixes": ["test/nvmf/"],
                    "forbidden_claim_terms": [
                        "默认 60s",
                        "[待验证] 60s",
                        "iscsi_check_chap_params 未使用常量时间比较",
                        "AuthMethod 为 NULL 时跳过认证",
                        "switch 缺少 ISCSI_FULL_FEATURE_PHASE",
                        "MaxConnections 已达上限时返回的成功",
                        "登录错误响应中 C-bit 未置位",
                        "Digest 校验失败后未释放",
                    ],
                }
            ],
        },
    }


def _source_flow_outputs(tag: str) -> list[dict[str, Any]]:
    return [
        {
            "id": "source_scope",
            "type": "json",
            "from": "analyze_source_flow",
            "artifact": "source_scope.json",
            "schema": SOURCE_SCOPE_SCHEMA,
        },
        {
            "id": "code_evidence",
            "type": "json",
            "from": "analyze_source_flow",
            "artifact": "evidence_cards.json",
            "schema": EVIDENCE_CARDS_SCHEMA,
        },
        {
            "id": "flow_map",
            "type": "markdown",
            "from": "analyze_source_flow",
            "artifact": "flow_map.md",
        },
        {
            "id": "sfmea",
            "type": "json",
            "from": "analyze_source_flow",
            "artifact": "sfmea.json",
            "schema": SFMEA_SCHEMA,
        },
        {
            "id": "black_box_cases",
            "type": "test_cases",
            "from": "analyze_source_flow",
            "artifact": "black_box_cases.json",
            "semantic_import": {
                "enabled": True,
                "defaults": {
                    "test_level": "black_box",
                    "tags": [tag],
                },
            },
        },
        {"id": "report", "type": "markdown", "from": "render_report"},
    ]


def _source_flow_scenario_preset(
    *,
    preset_id: str,
    name: str,
    description: str,
    default_query: str,
) -> dict[str, Any]:
    return {
        "id": preset_id,
        "name": name,
        "description": description,
        "definition": {
            "id": preset_id,
            "name": name,
            "version": 1,
            "inputs": [
                {
                    "id": "analysis_object",
                    "type": "free_text",
                    "required": False,
                    "role": "optional override for the preset scenario scope",
                },
                {"id": "repo_path", "type": "directory", "required": True, "resolver": "local"},
                {"id": "requirements_doc", "type": "file", "required": False, "role": "requirements"},
                {"id": "design_doc", "type": "file", "required": False, "role": "design"},
                {"id": "coverage_report", "type": "coverage_report", "required": False, "role": "coverage context"},
                {"id": "semantic_library_ref", "type": "semantic_library_ref", "required": False, "role": "test terminology"},
            ],
            "steps": [
                {
                    "id": "analyze_source_flow",
                    "type": "local_source_flow_sfmea_blackbox",
                    "goal": (
                        "Run a scenario-focused source evidence, flow, SFMEA, and black-box "
                        "test generation chain. Check GitNexus/CGC artifacts first when present."
                    ),
                    "default_query": default_query,
                    "required_artifacts": LEGACY_SOURCE_FLOW_REQUIRED_ARTIFACTS,
                },
                {"id": "validate_evidence", "type": "evidence_validate"},
                {"id": "render_report", "type": "report_render"},
            ],
            "outputs": _source_flow_outputs(preset_id),
        },
    }


def builtin_workflow_presets() -> list[dict[str, Any]]:
    """Return every historical built-in preset for compatibility and migrations."""

    presets = [
        {
            "id": "module_analysis",
            "name": "Module Analysis",
            "description": (
                "Collect local source evidence first, then let the selected Agent produce an "
                "evidence-backed module analysis report with flows, abnormal paths, and test focus."
            ),
            "definition": {
                "id": "module_analysis",
                "name": "Module Analysis",
                "version": 1,
                "execution_subject": "agent",
                "execution_label": "智能体源码分析",
                "user_message": "先收集本地源码证据，再由所选执行器完成深度模块分析。",
                "inputs": [
                    {"id": "analysis_object", "type": "free_text", "required": True, "role": "module or feature name"},
                    {"id": "repo_path", "type": "directory", "required": True, "resolver": "local"},
                    {"id": "requirements_doc", "type": "file", "required": False, "role": "requirements"},
                    {"id": "design_doc", "type": "file", "required": False, "role": "design"},
                ],
                "steps": [
                    {
                        "id": "discover_scope",
                        "type": "local_scope_discover",
                        "goal": "Discover source files, symbols, entry points, and evidence for the requested module from the local repository.",
                        "required_artifacts": ["source_scope.json", "evidence_cards.json"],
                    },
                    {
                        "id": "analyze_module",
                        "type": "agent_task",
                        "provider": "claude-code",
                        "mcp_profile": "codehub-mcp",
                        "skills": [
                            "source-evidence-first",
                            "module-analysis",
                            "business-flow-mapping",
                            "storage-test-analysis",
                            "artifact-contract",
                        ],
                        "skill_instructions": [
                            {
                                "id": "module-analysis",
                                "label": "模块分析",
                                "source": "codetalk_builtin",
                                "prompt_hint": (
                                    "生成 module_analysis.md，必须包含分析范围、模块边界、关键入口与调用链、"
                                    "主流程、异常与恢复路径、源码与测试证据、测试关注点和证据缺口。"
                                ),
                            },
                            {
                                "id": "source-evidence-first",
                                "label": "源码证据优先",
                                "source": "codetalk_builtin",
                                "prompt_hint": (
                                    "先读取工作区源码、GitNexus/CGC 产物和 discover_scope 的证据卡；"
                                    "每个关键判断引用真实文件、符号或测试目录，不得把推测写成事实。"
                                ),
                            },
                            {
                                "id": "storage-test-analysis",
                                "label": "存储测试视角",
                                "source": "codetalk_builtin",
                                "prompt_hint": (
                                    "结合用户指定模块识别协议状态、资源生命周期、超时、并发、恢复、"
                                    "性能和可观测性风险；测试关注点必须由源码和现有测试证据支撑。"
                                ),
                            },
                        ],
                        "goal": (
                            "Read the complete user input and workspace source. Consume the local scope "
                            "and evidence artifacts from the previous step, then use GitNexus/CGC when "
                            "available to produce module_analysis.md. The report must explain module "
                            "boundaries, concrete entry points and call paths, main and abnormal/recovery "
                            "flows, existing test evidence, test-focused risks, and explicit evidence gaps. "
                            "Do not modify the repository; terminal output is progress only."
                        ),
                        "required_artifacts": ["module_analysis.md"],
                    },
                    {"id": "validate_evidence", "type": "evidence_validate"},
                ],
                "outputs": [
                    {
                        "id": "scope",
                        "type": "json",
                        "from": "discover_scope",
                        "schema": SOURCE_SCOPE_SCHEMA,
                    },
                    {
                        "id": "evidence_cards",
                        "type": "json",
                        "from": "discover_scope",
                        "artifact": "evidence_cards.json",
                        "schema": EVIDENCE_CARDS_SCHEMA,
                    },
                    {
                        "id": "report",
                        "type": "markdown",
                        "from": "analyze_module",
                        "artifact": "module_analysis.md",
                    },
                ],
            },
        },
        {
            "id": "resource_leak_hunt",
            "name": "Resource Leak and Error Branch Hunt",
            "description": "Find resource leaks, cleanup gaps, and abnormal branch risks without requiring the heavy module template.",
            "definition": {
                "id": "resource_leak_hunt",
                "name": "Resource Leak and Error Branch Hunt",
                "version": 1,
                "inputs": [
                    {"id": "target_scope", "type": "free_text", "required": True, "role": "module, file, or function scope"},
                    {"id": "risk_pattern", "type": "enum", "required": False, "role": "leak, cleanup, exception branch, lifetime"},
                    {"id": "repo_path", "type": "directory", "required": True, "resolver": "local"},
                ],
                "steps": [
                    {
                        "id": "hunt_risks",
                        "type": "local_resource_leak_hunt",
                        "goal": "Find resource acquisition/release pairs, abnormal exits, missing cleanup, and evidence-backed test hooks from the local repository.",
                        "required_artifacts": ["risk_findings.json", "evidence_cards.json", "test_hooks.json"],
                    },
                    {"id": "validate_evidence", "type": "evidence_validate"},
                    {"id": "render_report", "type": "report_render"},
                ],
                "outputs": [
                    {
                        "id": "risk_findings",
                        "type": "json",
                        "from": "hunt_risks",
                        "artifact": "risk_findings.json",
                        "schema": RISK_FINDINGS_SCHEMA,
                        "evidence_memory": {
                            "enabled": True,
                            "kind": "resource_risk_finding",
                            "subject_key_field": "finding_id",
                            "path_field": "file_path",
                            "symbol_field": "function",
                            "status": "candidate_output",
                            "text_fields": ["summary", "risk", "resource", "function"],
                        },
                    },
                    {
                        "id": "evidence_cards",
                        "type": "json",
                        "from": "hunt_risks",
                        "artifact": "evidence_cards.json",
                        "schema": EVIDENCE_CARDS_SCHEMA,
                    },
                    {"id": "report", "type": "markdown", "from": "render_report"},
                ],
            },
        },
        {
            "id": "mr_blackbox_test",
            "name": "MR Black-box Test Design",
            "description": "Let the Agent CLI fetch MR context through its MCP credentials, then validate artifacts and produce black-box test cases.",
            "definition": {
                "id": "mr_blackbox_test",
                "name": "MR Black-box Test Design",
                "version": 1,
                "inputs": [
                    {"id": "mr_link", "type": "mr_link", "required": False, "role": "merge request URL"},
                    {"id": "patch_diff", "type": "patch", "required": False, "role": "local patch diff"},
                    {"id": "repo_path", "type": "directory", "required": False, "resolver": "local"},
                    {"id": "design_doc", "type": "file", "required": False, "role": "design context"},
                    {"id": "coverage_report", "type": "coverage_report", "required": False, "role": "coverage context"},
                    {"id": "semantic_library_ref", "type": "semantic_library_ref", "required": False, "role": "test terminology"},
                ],
                "steps": [
                    {
                        "id": "collect_mr",
                        "type": "local_mr_blackbox_test",
                        "goal": "Collect MR or local patch context and produce black-box cases without editing files.",
                        "required_artifacts": ["mr_snapshot.json", "diff.patch", "changed_files.json", "black_box_cases.json"],
                    },
                    {"id": "semantic_retrieve", "type": "semantic_retrieve"},
                    {"id": "validate_mr_evidence", "type": "evidence_validate"},
                    {"id": "render_blackbox_cases", "type": "report_render"},
                ],
                "outputs": [
                    {
                        "id": "mr_scope",
                        "type": "json",
                        "from": "collect_mr",
                        "artifact": "mr_snapshot.json",
                        "schema": MR_SNAPSHOT_SCHEMA,
                    },
                    {
                        "id": "black_box_cases",
                        "type": "test_cases",
                        "from": "collect_mr",
                        "artifact": "black_box_cases.json",
                        "semantic_import": {
                            "enabled": True,
                            "defaults": {
                                "test_level": "black_box",
                                "tags": ["mr_blackbox_test"],
                            },
                        },
                    },
                ],
            },
        },
        {
            "id": "patch_impact_review",
            "name": "Patch Impact Review",
            "description": "Analyze a patch plan or diff, explain before/after flow changes, impact range, and test recommendations.",
            "definition": {
                "id": "patch_impact_review",
                "name": "Patch Impact Review",
                "version": 1,
                "inputs": [
                    {"id": "patch_plan", "type": "file", "required": False, "role": "patch plan"},
                    {"id": "patch_diff", "type": "patch", "required": False, "role": "patch diff"},
                    {"id": "repo_path", "type": "directory", "required": True, "resolver": "local"},
                ],
                "steps": [
                    {"id": "parse_patch", "type": "diff_parse"},
                    {
                        "id": "analyze_impact",
                        "type": "local_patch_impact_review",
                        "goal": "Explain pre/post flow changes, affected files/symbols, compatibility risks, and test scope from local diff and source evidence.",
                        "required_artifacts": ["impact_scope.json", "flow_delta.json", "test_recommendations.json"],
                    },
                    {"id": "validate_evidence", "type": "evidence_validate"},
                    {"id": "render_report", "type": "report_render"},
                ],
                "outputs": [
                    {
                        "id": "impact_scope",
                        "type": "json",
                        "from": "analyze_impact",
                        "artifact": "impact_scope.json",
                        "schema": IMPACT_SCOPE_SCHEMA,
                        "evidence_memory": {
                            "enabled": True,
                            "kind": "patch_impact_scope",
                            "subject_key_field": "impact_id",
                            "path_field": "file_path",
                            "symbol_field": "symbol",
                            "status": "candidate_output",
                            "text_fields": ["summary", "flow_delta", "impact", "risk", "test_scope"],
                        },
                    },
                    {"id": "report", "type": "markdown", "from": "render_report"},
                ],
            },
        },
        {
            "id": "source_flow_sfmea_blackbox",
            "name": "代码分析 -> 流程 -> SFMEA -> 黑盒用例",
            "description": (
                "基于真实源码证据完成代码分析、流程梳理、SFMEA 和可执行黑盒用例；"
                "优先消费可用的 GitNexus 与 CGC 产物。"
            ),
            "definition": {
                "id": "source_flow_sfmea_blackbox",
                "name": "代码分析 -> 流程 -> SFMEA -> 黑盒用例",
                "description": (
                    "基于真实源码证据完成代码分析、流程梳理、SFMEA 和可执行黑盒用例；"
                    "优先消费可用的 GitNexus 与 CGC 产物。"
                ),
                "version": 2,
                "execution_subject": "builtin_llm",
                "execution_label": "内置模型分阶段分析",
                "user_message": "内置模型将按源码证据、流程、SFMEA 和黑盒用例分阶段生成并校验交付件。",
                "inputs": [
                    {"id": "analysis_object", "label": "分析对象", "type": "free_text", "required": True, "role": "要分析的模块、特性或业务流程"},
                    {"id": "repo_path", "label": "源码工作空间", "type": "directory", "required": True, "resolver": "local", "role": "由已选择的工作空间自动注入"},
                    {"id": "requirements_doc", "label": "需求文档", "type": "file", "required": False, "role": "需求与外部行为约束"},
                    {"id": "design_doc", "label": "开发设计文档", "type": "file", "required": False, "role": "设计机制与异常约束"},
                    {"id": "coverage_report", "label": "覆盖率报告", "type": "coverage_report", "required": False, "role": "覆盖现状与补测线索"},
                    {"id": "semantic_library_ref", "label": "语义库参考", "type": "semantic_library_ref", "required": False, "role": "测试术语或历史用例参考"},
                ],
                "steps": [
                    {
                        "id": "analyze_source_flow",
                        "label": "源码驱动测试分析",
                        "type": "agent_task",
                        "provider": "builtin-llm",
                        "execution_mode": "staged",
                        "mcp_profile": "codehub-mcp",
                        "skills": [
                            "source-evidence-first",
                            "storage-flow-analysis",
                            "sfmea",
                            "black-box-test-design",
                            "artifact-contract",
                        ],
                        "source_context_limit": 44,
                        "source_context_min_test_files": 6,
                        "source_analysis_max_files": 6,
                        "source_analysis_max_evidence_anchors": 12,
                        "source_analysis_min_test_files": 3,
                        "goal": (
                            "先检查可用的 GitNexus 和 CGC 产物，再读取本地源码与测试证据；"
                            "First check GitNexus and CGC artifacts when available, then read local source "
                            "evidence to produce code evidence, externally observable flow steps, SFMEA, "
                            "and black-box test cases. Every evidence card must contain a SHA256-verified "
                            "file_path, start_line, end_line, and a verbatim contiguous excerpt from that "
                            "range. Every technical claim must reference the base evidence_id and use an "
                            "exact source quote within that card; 禁止省略号、拼接或改写 quote，必须逐字可核验。 "
                            "SFMEA only includes actual failure behavior or plausible defect paths: 禁止把正常保护逻辑、"
                            "预期参数拒绝或测试覆盖缺口当作失效模式。Each mitigation must include both a "
                            "production/configuration/operational remediation and an executable test or "
                            "monitoring verification. mapped_test_dir must be one existing repository test "
                            "path, multiple existing paths separated by semicolons, or an explicit "
                            "ai_suggested_unverified marker. Performance cases must state warmup, repeated "
                            "sampling, P50/P95, and a source/config/spec/same-environment baseline. Markdown "
                            "evidence must use full repository-relative paths instead of bare filenames. A "
                            "technical claim may state only what its exact quote directly establishes; do not "
                            "upgrade call ordering, return handling, cleanup ordering, or a guard branch into "
                            "a defect unless the verified evidence directly establishes the adverse effect. "
                            "Put uncertain or externally testable concerns in a clearly labeled hypothesis or "
                            "remaining-risk section, never in a scored SFMEA failure mode or a technical claim. "
                            "flow_map.md must name at least one existing repository test path as its test "
                            "mapping, and test_strategy.md must name at least one existing repository source "
                            "path as its source anchor."
                        ),
                        "required_artifacts": [
                            *SOURCE_FLOW_REQUIRED_ARTIFACTS,
                            "module_map.md",
                            "test_strategy.md",
                        ],
                    },
                    {
                        "id": "validate_evidence",
                        "label": "源码证据校验",
                        "type": "evidence_validate",
                        "goal": "逐项校验文件、符号、行号和引用片段，阻止无效源码证据进入交付件。",
                    },
                    {
                        "id": "render_report",
                        "label": "汇总报告生成",
                        "type": "report_render",
                        "goal": "汇总已通过门禁的代码证据、流程、SFMEA、黑盒用例和可选测试设计脑图。",
                    },
                ],
                "outputs": [
                    {
                        "id": "source_scope",
                        "label": "源码范围",
                        "type": "json",
                        "from": "analyze_source_flow",
                        "artifact": "source_scope.json",
                        "schema": SOURCE_SCOPE_SCHEMA,
                    },
                    {
                        "id": "code_evidence",
                        "label": "代码证据卡",
                        "type": "json",
                        "from": "analyze_source_flow",
                        "artifact": "evidence_cards.json",
                        "schema": EVIDENCE_CARDS_SCHEMA,
                    },
                    {
                        "id": "module_map",
                        "label": "模块地图",
                        "type": "markdown",
                        "from": "analyze_source_flow",
                        "artifact": "module_map.md",
                    },
                    {"id": "flow_map", "label": "业务流程图谱", "type": "markdown", "from": "analyze_source_flow", "artifact": "flow_map.md"},
                    {
                        "id": "sfmea",
                        "label": "SFMEA 风险表",
                        "type": "json",
                        "from": "analyze_source_flow",
                        "artifact": "sfmea.json",
                        "min_sfmea_rows": 12,
                        "schema": SFMEA_SCHEMA,
                    },
                    {
                        "id": "black_box_cases",
                        "label": "黑盒测试用例",
                        "type": "test_cases",
                        "from": "analyze_source_flow",
                        "artifact": "black_box_cases.json",
                        "min_black_box_cases": 12,
                        "semantic_import": {
                            "enabled": True,
                            "defaults": {
                                "test_level": "black_box",
                                "tags": ["source_flow_sfmea_blackbox"],
                            },
                        },
                        "schema": BLACK_BOX_CASES_SCHEMA,
                    },
                    {
                        "id": "test_strategy",
                        "label": "测试策略",
                        "type": "markdown",
                        "from": "analyze_source_flow",
                        "artifact": "test_strategy.md",
                    },
                    {
                        "id": "test_design_mindmap",
                        "label": "测试设计脑图",
                        "type": "test_design_mindmap",
                        "from": "analyze_source_flow",
                        "artifact": MINDMAP_ARTIFACTS[0],
                        "companion_artifacts": list(MINDMAP_ARTIFACTS[1:]),
                        "required": False,
                        "default_enabled": False,
                    },
                    {"id": "report", "label": "汇总报告", "type": "markdown", "from": "render_report"},
                ],
            },
        },
        {
            "id": "testing_activity_orchestration",
            "name": "Testing Activity Orchestration",
            "description": (
                "Plan and orchestrate testing work beyond test design: strategy, scope, "
                "environment readiness, execution matrix, coverage gaps, defect triage, "
                "regression scope, performance/reliability activities, and release readiness."
            ),
            "definition": {
                "id": "testing_activity_orchestration",
                "name": "Testing Activity Orchestration",
                "version": 1,
                "inputs": [
                    {"id": "test_goal", "type": "free_text", "required": True, "role": "testing objective or release risk"},
                    {"id": "repo_path", "type": "directory", "required": True, "resolver": "local"},
                    {"id": "requirements_doc", "type": "file", "required": False, "role": "requirements or acceptance criteria"},
                    {"id": "coverage_report", "type": "coverage_report", "required": False, "role": "coverage context"},
                    {"id": "defect_report", "type": "file", "required": False, "role": "known defects or failure summary"},
                    {"id": "environment_notes", "type": "long_text", "required": False, "role": "test environment, constraints, resources"},
                    {"id": "semantic_library_ref", "type": "semantic_library_ref", "required": False, "role": "testing terminology"},
                ],
                "steps": [
                    {
                        "id": "plan_testing_activity",
                        "type": "agent_task",
                        "provider": "claude-code",
                        "mcp_profile": "",
                        "skills": [
                            "source-evidence-first",
                            "test-strategy-planning",
                            "black-box-test-design",
                            "coverage-gap-analysis",
                            "test-execution-orchestration",
                            "defect-triage-regression",
                            "performance-reliability-testing",
                            "artifact-contract",
                        ],
                        "skill_instructions": [
                            {
                                "id": "test-strategy-planning",
                                "label": "测试策略与计划",
                                "source": "codetalk_builtin",
                                "prompt_hint": "输出测试策略、范围、风险优先级、准入/准出标准、资源/环境依赖、里程碑和未决问题。",
                            },
                            {
                                "id": "black-box-test-design",
                                "label": "黑盒测试设计",
                                "source": "codetalk_builtin",
                                "prompt_hint": (
                                    "输出可执行黑盒用例，只描述外部输入、操作、预期结果、观测点和失败诊断，"
                                    "并把每条用例映射到真实源码或测试目录证据。"
                                ),
                            },
                            {
                                "id": "coverage-gap-analysis",
                                "label": "覆盖率与缺口分析",
                                "source": "codetalk_builtin",
                                "prompt_hint": "结合覆盖率文件、源码入口和现有测试目录，标出覆盖缺口、补充测试建议和证据映射。",
                            },
                            {
                                "id": "test-execution-orchestration",
                                "label": "测试执行编排",
                                "source": "codetalk_builtin",
                                "prompt_hint": "输出可执行测试矩阵，包含环境、前置条件、批次顺序、并发/长跑安排、观测指标、失败诊断和复跑规则。",
                            },
                            {
                                "id": "defect-triage-regression",
                                "label": "缺陷分诊与回归",
                                "source": "codetalk_builtin",
                                "prompt_hint": "输出缺陷分级、复现线索、影响范围、回归测试范围、阻塞/放行建议和需要补充的证据。",
                            },
                            {
                                "id": "performance-reliability-testing",
                                "label": "性能与可靠性测试",
                                "source": "codetalk_builtin",
                                "prompt_hint": "输出性能/可靠性测试计划，包含基线、负载模型、指标、故障注入、soak、退化阈值和诊断数据。",
                            },
                        ],
                        "goal": (
                            "First inspect workspace source, GitNexus/CGC artifacts, input files, "
                            "coverage, semantic cases, and defect evidence unless the user explicitly "
                            "excludes source-based analysis. Produce testing activity artifacts for "
                            "strategy, plan, black-box cases, execution, coverage gaps, defect triage, "
                            "regression, performance/reliability, and release readiness. Terminal output "
                            "is only progress; required artifacts are authoritative."
                        ),
                        "required_artifacts": [
                            "test_strategy.md",
                            "test_plan.json",
                            "execution_matrix.json",
                            "coverage_gap_report.json",
                            "defect_triage.md",
                            "release_readiness.md",
                            "black_box_cases.json",
                        ],
                    },
                    {"id": "semantic_retrieve", "type": "semantic_retrieve"},
                    {"id": "validate_evidence", "type": "evidence_validate"},
                    {"id": "render_report", "type": "report_render"},
                ],
                "outputs": [
                    {"id": "test_strategy", "type": "markdown", "from": "plan_testing_activity", "artifact": "test_strategy.md"},
                    {
                        "id": "test_plan",
                        "type": "json",
                        "from": "plan_testing_activity",
                        "artifact": "test_plan.json",
                        "schema": TEST_PLAN_SCHEMA,
                    },
                    {
                        "id": "execution_matrix",
                        "type": "json",
                        "from": "plan_testing_activity",
                        "artifact": "execution_matrix.json",
                        "schema": EXECUTION_MATRIX_SCHEMA,
                    },
                    {
                        "id": "coverage_gap_report",
                        "type": "json",
                        "from": "plan_testing_activity",
                        "artifact": "coverage_gap_report.json",
                        "schema": COVERAGE_GAP_REPORT_SCHEMA,
                    },
                    {"id": "defect_triage", "type": "markdown", "from": "plan_testing_activity", "artifact": "defect_triage.md"},
                    {"id": "release_readiness", "type": "markdown", "from": "plan_testing_activity", "artifact": "release_readiness.md"},
                    {
                        "id": "black_box_cases",
                        "type": "test_cases",
                        "from": "plan_testing_activity",
                        "artifact": "black_box_cases.json",
                        "semantic_import": {
                            "enabled": True,
                            "defaults": {
                                "test_level": "black_box",
                                "tags": ["testing_activity_orchestration"],
                            },
                        },
                    },
                    {"id": "report", "type": "markdown", "from": "render_report"},
                ],
            },
        },
        _basic_report_preset(include_design=False, provider="codex"),
        _basic_report_preset(include_design=True, provider="builtin-llm"),
        _source_flow_scenario_preset(
            preset_id="nvmf_connect_io_blackbox",
            name="NVMe-oF Connect / IO Black-box Scenario",
            description=(
                "Analyze SPDK NVMe-oF connect, authentication, queue setup, IO submit, "
                "disconnect/reconnect, timeout, and reset behavior for source-backed SFMEA "
                "and black-box cases."
            ),
            default_query=(
                "lib/nvmf test/nvmf NVMe-oF connect authentication queue setup IO submit "
                "disconnect reconnect timeout controller reset"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="iscsi_login_session_blackbox",
            name="iSCSI Login / Session Black-box Scenario",
            description=(
                "Analyze SPDK iSCSI login, CHAP, digest, multi-connection, session reset, "
                "redirect, and initiator disconnect behavior for SFMEA and black-box cases."
            ),
            default_query=(
                "lib/iscsi test/iscsi_tgt iSCSI login CHAP digest multi-connection session "
                "reset redirect initiator disconnect authentication failure"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="bdev_io_reset_blackbox",
            name="bdev IO / Reset Black-box Scenario",
            description=(
                "Analyze SPDK bdev open, submit, complete, error returns, pending reset, "
                "IO drain, reconnect, failover, and resource pressure behavior."
            ),
            default_query=(
                "lib/bdev module/bdev test/bdev bdev open submit complete error return "
                "pending reset IO drain reconnect failover resource pressure"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="rpc_config_negative_blackbox",
            name="RPC / Config Negative Black-box Scenario",
            description=(
                "Analyze public RPC/config flows for invalid parameters, repeated calls, "
                "ordering errors, partial success, rollback, idempotency, and diagnostics."
            ),
            default_query=(
                "rpc config app test/json_config invalid parameter repeated call ordering "
                "partial success rollback idempotency diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="reactor_thread_poller_blackbox",
            name="Reactor / Thread / Poller Black-box Scenario",
            description=(
                "Analyze reactor, thread, message passing, poller scheduling, blocking pollers, "
                "long task dispatch, concurrency, recovery, and performance degradation."
            ),
            default_query=(
                "lib/thread lib/event lib/scheduler test/thread reactor thread message poller "
                "scheduling blocking long task concurrency recovery performance degradation"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="nvmf_disconnect_reconnect_blackbox",
            name="NVMe-oF Disconnect / Reconnect Black-box Scenario",
            description=(
                "Analyze SPDK NVMe-oF timeout, disconnect, reconnect, keep-alive, controller "
                "reset, qpair teardown, and recovery behavior for source-backed SFMEA and "
                "black-box cases."
            ),
            default_query=(
                "lib/nvmf test/nvmf NVMe-oF keep alive timeout disconnect reconnect "
                "controller reset qpair teardown transport error recovery"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="iscsi_auth_failure_blackbox",
            name="iSCSI Auth Failure / Reset Black-box Scenario",
            description=(
                "Analyze SPDK iSCSI CHAP/authentication failure, redirect, digest mismatch, "
                "session reset, logout, initiator disconnect, and recovery diagnostics."
            ),
            default_query=(
                "lib/iscsi test/iscsi_tgt iSCSI CHAP authentication failure digest mismatch "
                "redirect session reset logout initiator disconnect recovery diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="bdev_failover_resource_blackbox",
            name="bdev Failover / Resource Pressure Black-box Scenario",
            description=(
                "Analyze SPDK bdev failover, reconnect, resource exhaustion, no-memory paths, "
                "I/O drain, reset ordering, and public error reporting."
            ),
            default_query=(
                "lib/bdev module/bdev test/bdev bdev failover reconnect resource exhaustion "
                "no memory IO drain reset ordering public error reporting"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="blobstore_ftl_recovery_blackbox",
            name="Blobstore / FTL Recovery Black-box Scenario",
            description=(
                "Analyze SPDK blobstore and FTL metadata recovery, ENOSPC, abnormal shutdown, "
                "super block consistency, relocation, and restart recovery behavior."
            ),
            default_query=(
                "lib/blob lib/ftl module/bdev/ftl test/blobfs test/ftl blobstore FTL "
                "metadata recovery ENOSPC abnormal shutdown super block consistency restart"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="vhost_vfio_user_lifecycle_blackbox",
            name="vhost / vfio-user Lifecycle Black-box Scenario",
            description=(
                "Analyze SPDK vhost and vfio-user device lifecycle, queue configuration, "
                "guest attach/detach, socket cleanup, reset, and error recovery behavior."
            ),
            default_query=(
                "lib/vhost lib/vfio_user test/vhost test/vfio_user vhost vfio-user device "
                "lifecycle queue configuration guest attach detach socket cleanup reset recovery"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="nvmf_tcp_tls_auth_blackbox",
            name="NVMe/TCP TLS / Authentication Black-box Scenario",
            description=(
                "Analyze SPDK NVMe/TCP TLS and authentication setup, certificate/key mismatch, "
                "secure connection negotiation, fallback denial, reconnect, and public diagnostics."
            ),
            default_query=(
                "lib/nvmf test/nvmf NVMe TCP TLS authentication certificate key mismatch "
                "secure connection negotiation fallback denial reconnect diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="bdev_qos_latency_blackbox",
            name="bdev QoS / Latency Degradation Black-box Scenario",
            description=(
                "Analyze SPDK bdev QoS, rate limiting, queue depth pressure, latency spikes, "
                "timeout reporting, fairness, and recovery under sustained IO load."
            ),
            default_query=(
                "lib/bdev module/bdev test/bdev bdev QoS rate limit queue depth latency "
                "timeout fairness sustained IO load performance degradation recovery"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="jsonrpc_concurrency_idempotency_blackbox",
            name="JSON-RPC Concurrency / Idempotency Black-box Scenario",
            description=(
                "Analyze SPDK public JSON-RPC concurrency, repeated create/delete calls, "
                "idempotency, partial success, ordering races, rollback, and observable errors."
            ),
            default_query=(
                "rpc app test/json_config scripts/rpc.py JSON-RPC concurrency repeated "
                "create delete idempotency partial success ordering race rollback observable error"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="app_startup_shutdown_smoke_blackbox",
            name="App Startup / Shutdown Smoke Black-box Scenario",
            description=(
                "Analyze SPDK application startup, configuration load, RPC readiness, signal "
                "handling, graceful shutdown, restart, and externally visible diagnostics."
            ),
            default_query=(
                "app lib/event scripts/rpc.py test/app test/json_config SPDK application startup "
                "configuration load RPC readiness signal graceful shutdown restart diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="nvme_ctrlr_hotplug_reset_blackbox",
            name="NVMe Controller Hotplug / Reset Black-box Scenario",
            description=(
                "Analyze SPDK NVMe controller attach, identify, reset, timeout, hotremove, "
                "namespace change, reconnect, and public error reporting behavior."
            ),
            default_query=(
                "lib/nvme test/nvme nvme controller attach identify reset timeout hotremove "
                "namespace change reconnect public error reporting"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="storage_capacity_enospc_recovery_blackbox",
            name="Storage Capacity / ENOSPC Recovery Black-box Scenario",
            description=(
                "Analyze capacity pressure, ENOSPC, allocation failure, metadata persistence, "
                "partial write, retry, cleanup, and recovery behavior across SPDK storage layers."
            ),
            default_query=(
                "lib/bdev lib/blob lib/ftl test/bdev test/blobfs capacity pressure ENOSPC "
                "allocation failure metadata persistence partial write retry cleanup recovery"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="nvmf_rdma_transport_blackbox",
            name="NVMe/RDMA Transport Black-box Scenario",
            description=(
                "Analyze NVMe/RDMA connection setup, queue pairs, RDMA CM events, memory "
                "registration, disconnect, retry, error recovery, and public diagnostics."
            ),
            default_query=(
                "lib/nvmf test/nvmf NVMe RDMA transport queue pair RDMA CM event memory "
                "registration disconnect retry error recovery public diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="iscsi_digest_multi_connection_blackbox",
            name="iSCSI Digest / Multi-connection Black-box Scenario",
            description=(
                "Analyze iSCSI header/data digest, multi-connection sessions, connection "
                "migration, digest failure, recovery, and external log/status signals."
            ),
            default_query=(
                "lib/iscsi test/iscsi_tgt iSCSI header digest data digest multi connection "
                "session connection migration digest failure recovery external log status"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="bdev_hotremove_io_error_blackbox",
            name="bdev Hotremove / IO Error Black-box Scenario",
            description=(
                "Analyze bdev hotremove, underlying device loss, IO error reporting, reset, "
                "drain, retry, and externally visible state transitions."
            ),
            default_query=(
                "lib/bdev module/bdev test/bdev bdev hotremove underlying device loss IO "
                "error reporting reset drain retry observable state transition"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="blobstore_metadata_powerfail_blackbox",
            name="Blobstore Metadata / Power-fail Recovery Black-box Scenario",
            description=(
                "Analyze blobstore metadata updates, abnormal shutdown, power-fail restart, "
                "super block and cluster consistency, partial writes, and recovery validation."
            ),
            default_query=(
                "lib/blob test/blobfs blobstore metadata update abnormal shutdown power fail "
                "restart super block cluster consistency partial write recovery validation"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="rpc_security_authz_blackbox",
            name="RPC Security / Authorization Black-box Scenario",
            description=(
                "Analyze RPC exposure, authentication and authorization boundaries, invalid "
                "commands, sensitive parameters, failure audit, replay, and user-visible errors."
            ),
            default_query=(
                "scripts/rpc.py lib/event test/json_config RPC exposure authentication "
                "authorization invalid command sensitive parameter failure audit replay user visible error"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="fault_injection_timeout_recovery_blackbox",
            name="Fault Injection / Timeout Recovery Black-box Scenario",
            description=(
                "Analyze externally triggered fault injection, transport errors, timeout handling, "
                "process restart, retry behavior, cleanup, and recovery diagnostics across storage workflows."
            ),
            default_query=(
                "test/common test/nvmf test/bdev test/json_config lib/nvmf lib/bdev lib/thread "
                "fault injection timeout transport error retry cleanup process restart recovery diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="concurrent_operations_stress_blackbox",
            name="Concurrent Operations / Stress Black-box Scenario",
            description=(
                "Analyze concurrent public operations, create/delete races, connect/disconnect while IO runs, "
                "queue pressure, idempotency, ordering, and externally observable stress failures."
            ),
            default_query=(
                "test/nvmf test/bdev test/json_config lib/nvmf lib/bdev lib/thread rpc concurrency "
                "stress create delete race connect disconnect IO queue pressure idempotency ordering"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="observability_diagnostics_blackbox",
            name="Observability / Diagnostics Black-box Scenario",
            description=(
                "Analyze logs, counters, public status commands, diagnostic artifacts, warning paths, "
                "and failure triage signals that a black-box tester can observe without reading internals."
            ),
            default_query=(
                "lib/log lib/event scripts/rpc.py test/json_config test/common diagnostics logs counters "
                "status command warning failure triage observable metrics artifact"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="config_compatibility_rollback_blackbox",
            name="Config Compatibility / Rollback Black-box Scenario",
            description=(
                "Analyze configuration compatibility, invalid or mixed-version config input, partial apply, "
                "rollback, restart persistence, idempotency, and user-visible diagnostics."
            ),
            default_query=(
                "scripts/rpc.py test/json_config test/app lib/event app config compatibility invalid "
                "mixed version partial apply rollback restart persistence idempotency diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="lvol_snapshot_clone_blackbox",
            name="Logical Volume Snapshot / Clone Black-box Scenario",
            description=(
                "Analyze SPDK lvol create/delete, snapshot, clone, resize, thin provision, "
                "metadata persistence, ENOSPC, and recovery behavior."
            ),
            default_query=(
                "module/bdev/lvol lib/blob test/lvol scripts/rpc.py logical volume lvol "
                "snapshot clone resize thin provision metadata persistence ENOSPC recovery"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="raid_degraded_rebuild_blackbox",
            name="RAID Degraded / Rebuild Black-box Scenario",
            description=(
                "Analyze SPDK RAID create/start/stop, member failure, degraded mode, rebuild, "
                "I/O continuity, resync progress, and external diagnostics."
            ),
            default_query=(
                "module/bdev/raid test/bdev scripts/rpc.py RAID create start stop member "
                "failure degraded rebuild IO continuity resync progress diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="nvme_multipath_failover_blackbox",
            name="NVMe Multipath / Failover Black-box Scenario",
            description=(
                "Analyze NVMe multipath attach, path loss, ANA state changes, failover, reconnect, "
                "I/O continuity, timeout handling, and public status signals."
            ),
            default_query=(
                "lib/nvme module/bdev/nvme test/nvme test/bdev NVMe multipath path loss "
                "ANA failover reconnect IO continuity timeout public status"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="env_hugepage_memory_blackbox",
            name="Environment / Hugepage Memory Black-box Scenario",
            description=(
                "Analyze SPDK environment initialization, hugepage allocation, memory pressure, "
                "invalid launch parameters, cleanup, restart, and observable diagnostics."
            ),
            default_query=(
                "lib/env_dpdk lib/env_ocf test/env app SPDK environment initialization "
                "hugepage memory allocation pressure invalid parameter cleanup restart diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="spdk_cli_rpc_smoke_blackbox",
            name="SPDK CLI / RPC Smoke Black-box Scenario",
            description=(
                "Analyze SPDK public CLI and RPC smoke paths, target startup readiness, "
                "basic create/list/delete operations, invalid commands, and diagnostic output."
            ),
            default_query=(
                "scripts/rpc.py scripts/spdkcli.py test/json_config test/app app lib/event "
                "CLI RPC smoke startup readiness create list delete invalid command diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="target_crash_restart_blackbox",
            name="Target Crash / Restart Recovery Black-box Scenario",
            description=(
                "Analyze target process crash, signal termination, restart readiness, reconnect, "
                "state cleanup, in-flight IO visibility, and operator diagnostics."
            ),
            default_query=(
                "app lib/event lib/thread test/app test/nvmf test/iscsi_tgt target process crash "
                "signal termination restart readiness reconnect state cleanup inflight IO diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="multi_client_isolation_blackbox",
            name="Multi-client Isolation Black-box Scenario",
            description=(
                "Analyze multi-initiator or multi-client isolation, namespace visibility, access "
                "boundaries, shared resource pressure, and cross-session leakage symptoms."
            ),
            default_query=(
                "lib/nvmf lib/iscsi module/bdev test/nvmf test/iscsi_tgt multi client initiator "
                "namespace isolation access boundary session leakage shared resource pressure"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="queue_depth_backpressure_blackbox",
            name="Queue Depth / Backpressure Black-box Scenario",
            description=(
                "Analyze queue depth limits, outstanding IO saturation, backpressure behavior, "
                "latency spikes, timeout reporting, throttling, and recovery after pressure is removed."
            ),
            default_query=(
                "lib/bdev lib/nvmf lib/iscsi lib/thread test/bdev queue depth outstanding IO "
                "saturation backpressure latency timeout throttling recovery pressure removed"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="io_error_injection_retry_blackbox",
            name="IO Error Injection / Retry Black-box Scenario",
            description=(
                "Analyze externally visible IO error injection, retry, partial completion, "
                "transport failures, fail-fast behavior, and post-error data-path recovery."
            ),
            default_query=(
                "lib/bdev lib/nvmf lib/iscsi test/bdev test/nvmf IO error injection retry "
                "partial completion transport failure fail fast data path recovery diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="config_reload_persistence_blackbox",
            name="Config Reload / Persistence Black-box Scenario",
            description=(
                "Analyze config reload, saved configuration persistence, restart restore, "
                "partial apply, rollback, duplicate commands, and external state verification."
            ),
            default_query=(
                "app scripts/rpc.py test/json_config config reload saved configuration persistence "
                "restart restore partial apply rollback duplicate command external state verification"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="long_running_resource_leak_blackbox",
            name="Long-running Resource Leak Black-box Scenario",
            description=(
                "Analyze long-running create/delete, connect/disconnect, sustained IO, resource "
                "growth, cleanup, metrics, logs, and soak-test failure diagnostics."
            ),
            default_query=(
                "lib/bdev lib/nvmf lib/iscsi lib/thread test/common long running soak create "
                "delete connect disconnect sustained IO resource leak cleanup metrics logs diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="basic_lifecycle_smoke_blackbox",
            name="Basic Lifecycle Smoke Black-box Scenario",
            description=(
                "Analyze common create, list, update, delete, restart, and cleanup flows that "
                "black-box testers run before deeper storage validation."
            ),
            default_query=(
                "scripts/rpc.py test/json_config test/app test/bdev app lib/event basic lifecycle "
                "smoke create list update delete restart cleanup readiness diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="io_stress_performance_blackbox",
            name="I/O Stress / Performance Baseline Black-box Scenario",
            description=(
                "Analyze sustained I/O, mixed read/write load, queue depth pressure, latency "
                "regression, throughput baseline, and externally visible degradation signals."
            ),
            default_query=(
                "lib/bdev module/bdev test/bdev test/nvmf scripts/perf.py fio IO stress "
                "performance latency throughput queue depth mixed read write regression baseline"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="failure_recovery_soak_blackbox",
            name="Failure Recovery / Soak Black-box Scenario",
            description=(
                "Analyze long-running reliability scenarios with restart, disconnect, reconnect, "
                "resource pressure, cleanup, and recovery evidence visible to operators."
            ),
            default_query=(
                "test/common test/nvmf test/bdev lib/thread lib/bdev lib/nvmf soak reliability "
                "restart disconnect reconnect resource pressure cleanup recovery long running"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="transport_network_partition_blackbox",
            name="Transport Network Partition Black-box Scenario",
            description=(
                "Analyze transport-level packet loss, network partition, reconnect, timeout, "
                "keep-alive, IO continuity, and externally visible recovery behavior."
            ),
            default_query=(
                "lib/nvmf test/nvmf lib/iscsi test/iscsi_tgt transport packet loss network "
                "partition reconnect timeout keep alive IO continuity recovery diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="data_integrity_corruption_blackbox",
            name="Data Integrity / Corruption Black-box Scenario",
            description=(
                "Analyze externally observable data integrity checks, checksum or digest mismatch, "
                "partial write, read-after-write validation, metadata corruption, and recovery signals."
            ),
            default_query=(
                "lib/bdev lib/blob lib/iscsi lib/nvmf test/bdev test/blobfs data integrity "
                "checksum digest mismatch partial write read after write metadata corruption recovery"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="upgrade_compatibility_persistence_blackbox",
            name="Upgrade Compatibility / Persistence Black-box Scenario",
            description=(
                "Analyze upgrade, downgrade, restart persistence, saved configuration compatibility, "
                "metadata versioning, rollback, and user-visible migration diagnostics."
            ),
            default_query=(
                "app lib/event lib/blob lib/ftl scripts/rpc.py test/json_config upgrade downgrade "
                "restart persistence saved configuration compatibility metadata version rollback migration"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="telemetry_metrics_regression_blackbox",
            name="Telemetry / Metrics Regression Black-box Scenario",
            description=(
                "Analyze telemetry, counters, logs, status commands, metric regressions, alertability, "
                "and failure triage signals available to black-box storage testers."
            ),
            default_query=(
                "lib/trace lib/log lib/event scripts/rpc.py test/common telemetry counters logs "
                "status metrics regression alert diagnostics failure triage observable"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="nvmf_subsystem_namespace_acl_blackbox",
            name="NVMe-oF Subsystem / Namespace ACL Black-box Scenario",
            description=(
                "Analyze subsystem and namespace lifecycle, host allow-list changes, ANA visibility, "
                "namespace attach/detach, reconnect, and externally visible access-denial behavior."
            ),
            default_query=(
                "lib/nvmf test/nvmf scripts/rpc.py subsystem namespace host allow list ACL ANA "
                "attach detach reconnect access denied visibility diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="iscsi_lun_resize_hotplug_blackbox",
            name="iSCSI LUN Resize / Hotplug Black-box Scenario",
            description=(
                "Analyze iSCSI target LUN add/remove, resize, hotplug visibility, initiator rescan, "
                "active IO behavior, session recovery, and public diagnostics."
            ),
            default_query=(
                "lib/iscsi test/iscsi_tgt scripts/rpc.py iSCSI LUN add remove resize hotplug "
                "initiator rescan active IO session recovery diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="bdev_crypto_integrity_blackbox",
            name="bdev Crypto / Integrity Black-box Scenario",
            description=(
                "Analyze crypto or integrity bdev configuration, key mismatch, data verification, "
                "invalid parameters, failure reporting, performance impact, and recovery."
            ),
            default_query=(
                "module/bdev/crypto module/bdev test/bdev scripts/rpc.py bdev crypto integrity "
                "key mismatch data verification invalid parameter failure reporting performance recovery"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="scheduler_qos_fairness_blackbox",
            name="Scheduler QoS / Fairness Black-box Scenario",
            description=(
                "Analyze scheduler, poller, reactor, queue depth, QoS, fairness, starvation, "
                "latency regression, and externally observable recovery under competing workloads."
            ),
            default_query=(
                "lib/scheduler lib/thread lib/event test/scheduler test/thread scheduler poller reactor "
                "QoS fairness starvation latency regression competing workloads recovery"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="backup_restore_integrity_blackbox",
            name="Backup / Restore Integrity Black-box Scenario",
            description=(
                "Analyze export/import, save/restore, snapshot-like backup flows, checksum validation, "
                "partial restore, corrupted input, restart persistence, and operator diagnostics."
            ),
            default_query=(
                "scripts/rpc.py test/json_config lib/blob lib/bdev backup restore export import save "
                "checksum validation partial restore corrupted input restart persistence diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="nvme_discovery_log_blackbox",
            name="NVMe Discovery / Log Black-box Scenario",
            description=(
                "Analyze discovery log pages, identify/controller data, log retrieval, changed "
                "subsystem visibility, malformed requests, transport loss, and externally visible diagnostics."
            ),
            default_query=(
                "lib/nvme lib/nvmf test/nvme test/nvmf scripts/rpc.py discovery log page identify "
                "controller data subsystem visibility malformed request transport loss diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="iscsi_portal_failover_blackbox",
            name="iSCSI Portal / Failover Black-box Scenario",
            description=(
                "Analyze portal group changes, target discovery, failover, reconnect, stale sessions, "
                "network partition behavior, and operator-visible recovery signals."
            ),
            default_query=(
                "lib/iscsi test/iscsi_tgt scripts/rpc.py portal group discovery failover reconnect "
                "stale session network partition recovery diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="bdev_zone_append_blackbox",
            name="bdev Zone Append Black-box Scenario",
            description=(
                "Analyze zoned bdev write pointer handling, zone append, reset/open/finish, boundary "
                "errors, concurrent writers, capacity pressure, and observable completion behavior."
            ),
            default_query=(
                "lib/bdev module/bdev test/bdev zone append write pointer reset open finish boundary "
                "concurrent writer capacity pressure completion error diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="jsonrpc_partial_rollback_blackbox",
            name="JSON-RPC Partial Failure / Rollback Black-box Scenario",
            description=(
                "Analyze multi-step RPC changes, duplicate calls, partial success, rollback or cleanup, "
                "idempotency, invalid ordering, and client-visible error payloads."
            ),
            default_query=(
                "lib/rpc scripts/rpc.py test/json_config test/rpc JSON-RPC multi step partial success "
                "rollback cleanup idempotency duplicate invalid ordering error payload"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="vfio_user_hotplug_reconnect_blackbox",
            name="vfio-user Hotplug / Reconnect Black-box Scenario",
            description=(
                "Analyze vfio-user device hotplug, guest detach, reconnect, queue reconfiguration, "
                "socket loss, lifecycle recovery, and user-visible state transitions."
            ),
            default_query=(
                "lib/vfio_user lib/vhost test/vfio_user test/vhost vfio-user hotplug guest detach "
                "reconnect queue reconfiguration socket loss lifecycle recovery state transition"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="lvol_thin_snapshot_blackbox",
            name="lvol Thin Provisioning / Snapshot Black-box Scenario",
            description=(
                "Analyze thin provisioning, snapshot and clone lifecycle, ENOSPC handling, delete order, "
                "metadata persistence, restart recovery, and externally observable integrity checks."
            ),
            default_query=(
                "lib/lvol test/lvol scripts/rpc.py thin provisioning snapshot clone ENOSPC delete order "
                "metadata persistence restart recovery integrity check diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="api_contract_negative_blackbox",
            name="API Contract Negative Black-box Scenario",
            description=(
                "Analyze public API/RPC contract behavior for malformed input, unknown fields, "
                "version mismatch, duplicate requests, error payload stability, and operator diagnostics."
            ),
            default_query=(
                "api rpc cli scripts malformed input unknown field version mismatch duplicate request "
                "error payload compatibility diagnostics negative test"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="state_persistence_restart_blackbox",
            name="State Persistence / Restart Black-box Scenario",
            description=(
                "Analyze saved configuration, restart recovery, partially applied state, rollback, "
                "idempotent replay, stale state cleanup, and externally observable consistency checks."
            ),
            default_query=(
                "state persistence restart saved config replay rollback partial apply stale state cleanup "
                "idempotent recovery consistency diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="concurrency_isolation_race_blackbox",
            name="Concurrency / Isolation Race Black-box Scenario",
            description=(
                "Analyze concurrent create/delete/update, multi-client isolation, ordering races, "
                "shared resource pressure, starvation, and externally visible state leaks."
            ),
            default_query=(
                "concurrency isolation race multi client create delete update ordering shared resource "
                "starvation state leak stress diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="performance_capacity_regression_blackbox",
            name="Performance / Capacity Regression Black-box Scenario",
            description=(
                "Analyze throughput, latency, queue depth, capacity limits, backpressure, slow "
                "operation diagnostics, degradation thresholds, and recovery after pressure is removed."
            ),
            default_query=(
                "performance capacity regression throughput latency queue depth backpressure slow operation "
                "limit degradation recovery metrics diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="security_access_control_blackbox",
            name="Security / Access Control Black-box Scenario",
            description=(
                "Analyze authentication, authorization, tenant or host isolation, denied operations, "
                "sensitive data exposure, audit logs, replay attempts, and public failure responses."
            ),
            default_query=(
                "security access control authentication authorization isolation denied operation sensitive "
                "data exposure audit log replay failure response diagnostics"
            ),
        ),
    ]
    for preset in presets:
        preset_id = str(preset["id"])
        if preset_id in CORE_WORKFLOW_PRESET_IDS:
            preset["group"] = "core"
        elif preset_id in COMMON_TEST_SCENARIO_PRESET_IDS:
            preset["group"] = "common_test_scenario"
        validate_workflow_definition(preset["definition"])
    preset_ids = [str(preset["id"]) for preset in presets]
    if preset_ids[: len(ORIGINAL_CORE_WORKFLOW_PRESET_IDS)] != list(ORIGINAL_CORE_WORKFLOW_PRESET_IDS):
        raise AssertionError("original core workflow presets must stay first and complete")
    if preset_ids[: len(CORE_WORKFLOW_PRESET_IDS)] != list(CORE_WORKFLOW_PRESET_IDS):
        raise AssertionError("core workflow presets must stay first and complete")
    missing_scenarios = set(COMMON_TEST_SCENARIO_PRESET_IDS).difference(preset_ids)
    if missing_scenarios:
        raise AssertionError(f"missing common test scenario presets: {sorted(missing_scenarios)}")
    return deepcopy(presets)


def active_builtin_workflow_presets() -> list[dict[str, Any]]:
    """Return the intentionally small preset catalog exposed by the release UI."""

    by_id = {str(preset["id"]): preset for preset in builtin_workflow_presets()}
    return [deepcopy(by_id[preset_id]) for preset_id in ACTIVE_BUILTIN_WORKFLOW_PRESET_IDS]


def reserved_builtin_workflow_ids() -> frozenset[str]:
    """Return active and retired official ids so custom workflows cannot shadow them."""

    return frozenset(
        {
            *(str(preset["id"]) for preset in builtin_workflow_presets()),
            *BUILTIN_WORKFLOW_PRESET_ALIASES,
        }
    )


def get_workflow_preset(preset_id: str) -> dict[str, Any]:
    preset_id = canonical_builtin_workflow_preset_id(preset_id)
    for preset in builtin_workflow_presets():
        if preset["id"] == preset_id:
            return preset
    raise KeyError(preset_id)


def install_workflow_preset(store: WorkflowStore, preset_id: str) -> WorkflowDefinition:
    preset = get_workflow_preset(preset_id)
    return store.save_workflow(deepcopy(preset["definition"]))


def restore_builtin_workflow_presets(store: WorkflowStore) -> list[WorkflowDefinition]:
    """Install active release presets while preserving custom and retired definitions."""

    restored: list[WorkflowDefinition] = []
    presets = active_builtin_workflow_presets()
    for preset in reversed(presets):
        store.save_workflow(deepcopy(preset["definition"]))
    for preset in presets:
        restored.append(store.get_workflow(str(preset["definition"]["id"])))
    return restored
