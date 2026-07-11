"""Testing activity contracts, profiles, artifact templates, and quality audit."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _profile(
    *,
    name: str,
    aliases: list[str],
    scenarios: list[str],
    failure_modes: list[str],
    observability: list[str],
    graybox_evidence: list[str],
    source_entries: list[str],
    test_dirs: list[str],
    validated_test_mappings: list[str] | None = None,
    forbidden_internal_steps: list[str] | None = None,
    professional_constraints: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "aliases": aliases,
        "required_scenarios": scenarios,
        "failure_modes": failure_modes,
        "black_box_observability": observability,
        "graybox_evidence_points": graybox_evidence,
        "recommended_source_entries": source_entries,
        "recommended_test_dirs": test_dirs,
        "validated_test_mappings": validated_test_mappings or [],
        "professional_constraints": professional_constraints or [],
        "log_metric_rpc_observability": observability,
        "forbidden_internal_steps": forbidden_internal_steps
        or [
            "direct internal function invocation",
            "modify source code to trigger the scenario",
            "assert private struct fields as the external expected result",
        ],
    }


PROFILE_REGISTRY: dict[str, dict[str, Any]] = {
    "iscsi_login": _profile(
        name="iSCSI login/session",
        aliases=["iscsi", "login", "chap", "digest", "session reset"],
        scenarios=["login negotiation", "CHAP success/failure", "digest mismatch", "session reset", "multi-connection recovery"],
        failure_modes=["bad credentials", "redirect loop", "digest validation failure", "half-open session", "initiator disconnect"],
        observability=["initiator login result", "SPDK logs", "session state", "connection reset behavior", "test/iscsi_tgt output"],
        graybox_evidence=["login state machine", "CHAP decision point", "session cleanup path"],
        source_entries=["lib/iscsi", "lib/iscsi/iscsi.c"],
        test_dirs=["test/iscsi_tgt"],
        validated_test_mappings=[
            "test/iscsi_tgt/chap/chap_discovery.sh",
            "test/iscsi_tgt/chap/chap_mutual_not_set.sh",
            "test/iscsi_tgt/digests/digests.sh",
            "test/iscsi_tgt/login_redirection/login_redirection.sh",
            "test/iscsi_tgt/multiconnection/multiconnection.sh",
            "test/iscsi_tgt/rpc_config/rpc_config.py",
            "test/iscsi_tgt/calsoft/calsoft.py",
            "test/unit/lib/iscsi/iscsi.c/iscsi_ut.c",
        ],
        professional_constraints=[
            {
                "id": "iscsi_login_response_role",
                "assertion": (
                    "iscsi_op_login_response 负责整理并发送 Login Response；认证与阶段协商由 "
                    "iscsi_op_login_rsp_handle_csg_bit、iscsi_auth_params 和 "
                    "iscsi_op_login_rsp_handle 等路径完成。"
                ),
                "evidence": [
                    "lib/iscsi/iscsi.c::iscsi_op_login_response",
                    "lib/iscsi/iscsi.c::iscsi_op_login_rsp_handle_csg_bit",
                    "lib/iscsi/iscsi.c::iscsi_op_login_rsp_handle",
                ],
                "conflict_patterns": [
                    r"iscsi_op_login_response.{0,100}(?:认证|鉴权|auth|协商|negot).{0,40}(?:核心|处理|完成)",
                    r"iscsi_op_login_response.{0,60}(?:是|为).{0,30}(?:认证|鉴权|auth|协商|negot)",
                ],
                "correction_patterns": [
                    r"iscsi_op_login_response.{0,100}(?:不是|并非|不负责|does not).{0,60}(?:认证|鉴权|auth|协商|negot)",
                    r"(?:此函数|该函数|this function).{0,30}(?:不是|并非|不负责|does not).{0,60}(?:认证|鉴权|auth|协商|negot)",
                    r"iscsi_op_login_response.{0,180}(?:响应发送|send).{0,180}(?:认证|auth|协商|negot).{0,100}(?:负载|payload).{0,60}(?:处理|完成)",
                    r"(?:响应发送|send).{0,60}iscsi_op_login_response.{0,180}(?:认证|auth|协商|negot).{0,100}(?:负载|payload).{0,60}(?:处理|完成)",
                ],
            },
            {
                "id": "iscsi_login_status_class",
                "assertion": (
                    "Authentication Failure 和 Authorization Failure 都属于 Initiator Error "
                    "Status-Class 0x02；0x03 是 Target Error。"
                ),
                "evidence": ["include/spdk/iscsi_spec.h::ISCSI_CLASS_INITIATOR_ERROR"],
                "conflict_patterns": [
                    r"(?:authorization failure|授权失败).{0,80}(?:status[- ]?class\s*[:=]?\s*)?0x03",
                    r"(?:status[- ]?class\s*[:=]?\s*)?0x03.{0,80}(?:authorization failure|授权失败)",
                ],
                "correction_patterns": [
                    r"(?:authorization failure|授权失败).{0,80}`?0x02`?.{0,50}(?:不是|而非|not)\s*(?:target error\s*)?`?0x03`?",
                    r"(?:authorization failure|授权失败).{0,80}`?0x02`?.{0,50}target error.{0,20}`?0x03`?",
                ],
            },
            {
                "id": "iscsi_login_request_entry",
                "assertion": (
                    "Login Request 的头部与负载入口分别是 iscsi_pdu_hdr_op_login 和 "
                    "iscsi_pdu_payload_op_login；iscsi_op_login_rsp_handle_csg_bit 只处理响应阶段与认证状态。"
                ),
                "evidence": [
                    "lib/iscsi/iscsi.c::iscsi_pdu_hdr_op_login",
                    "lib/iscsi/iscsi.c::iscsi_pdu_payload_op_login",
                    "lib/iscsi/iscsi.c::iscsi_op_login_rsp_handle_csg_bit",
                ],
                "conflict_patterns": [
                    r"(?:接收|入口|处理).{0,60}login request.{0,100}iscsi_op_login_rsp_handle_csg_bit",
                    r"iscsi_op_login_rsp_handle_csg_bit.{0,100}(?:接收|入口|处理).{0,60}login request",
                ],
            },
            {
                "id": "iscsi_login_negotiation_transport",
                "assertion": (
                    "登录认证与参数协商使用 Login Request/Response 的数据段；不要把 Text Request "
                    "写成进入 Full Feature Phase 前的必需登录步骤。"
                ),
                "evidence": [
                    "lib/iscsi/iscsi.c::iscsi_pdu_payload_op_login",
                    "lib/iscsi/iscsi.c::iscsi_op_login_rsp_handle",
                ],
                "conflict_patterns": [
                    r"initiator.{0,50}(?:发送|send).{0,40}text request.{0,100}(?:登录|login|参数协商|negot)",
                    r"text request.{0,100}(?:登录参数协商|login negotiation|进入 full feature)",
                ],
                "correction_patterns": [
                    r"(?:不需要|无需|not required).{0,50}text request",
                    r"text request.{0,50}(?:不是|并非|不作为|is not).{0,80}(?:必需|required|登录|login)",
                ],
            },
            {
                "id": "iscsi_connection_cleanup_role",
                "assertion": (
                    "连接生命周期清理由 lib/iscsi/conn.c 的连接析构路径负责；iscsi_param_free "
                    "只释放参数链表，app/iscsi_tgt 的 spdk_startup 只是应用启动入口。"
                ),
                "evidence": [
                    "lib/iscsi/conn.c::_iscsi_conn_destruct",
                    "lib/iscsi/param.c::iscsi_param_free",
                    "app/iscsi_tgt/iscsi_tgt.c::spdk_startup",
                ],
                "conflict_patterns": [
                    r"(?:连接清理|connection cleanup).{0,30}(?:由|通过|by)\s*`?(?:iscsi_param_free|spdk_startup)",
                    r"(?:iscsi_param_free|spdk_startup).{0,100}(?:负责|完成|实现).{0,40}(?:连接清理|connection cleanup)",
                ],
                "correction_patterns": [
                    r"(?:iscsi_param_free|spdk_startup).{0,50}(?:不负责|并非|does not).{0,60}(?:连接清理|connection cleanup)",
                ],
            },
            {
                "id": "iscsi_chap_execution_role",
                "assertion": (
                    "iscsi_negotiate_chap_param 只根据配置设置 AuthMethod 策略；实际 CHAP "
                    "challenge/response 校验由 iscsi_auth_params 路径执行。"
                ),
                "evidence": [
                    "lib/iscsi/iscsi.c::iscsi_negotiate_chap_param",
                    "lib/iscsi/iscsi.c::iscsi_auth_params",
                ],
                "conflict_patterns": [
                    r"iscsi_negotiate_chap_param.{0,100}(?:执行|处理|完成|负责).{0,40}(?:chap\s*)?(?:认证|authentication)",
                    r"(?:chap\s*)?(?:认证|authentication).{0,100}(?:由|通过).{0,30}iscsi_negotiate_chap_param.{0,40}(?:执行|处理|完成)",
                ],
                "correction_patterns": [
                    r"iscsi_negotiate_chap_param.{0,160}(?:不执行|不负责|并非|does not).{0,50}(?:chap\s*)?(?:认证|authentication)",
                ],
            },
            {
                "id": "iscsi_login_status_detail_05",
                "assertion": (
                    "Login Status-Detail 0x05 表示 Unsupported Version；参数协商失败不能泛化标成 Parameter Error 0x05。"
                ),
                "evidence": ["include/spdk/iscsi_spec.h::ISCSI_LOGIN_UNSUPPORTED_VERSION"],
                "conflict_patterns": [
                    r"(?:parameter error|参数(?:协商)?错误|参数错误).{0,80}(?:status[- ]?detail\s*[:=]?\s*)?0x05",
                    r"(?:status[- ]?detail\s*[:=]?\s*)?0x05.{0,80}(?:parameter error|参数(?:协商)?错误|参数错误)",
                ],
                "correction_patterns": [
                    r"(?:status[- ]?detail\s*[:=]?\s*)?0x05.{0,80}(?:不是|并非|not).{0,30}(?:parameter error|参数(?:协商)?错误|参数错误)",
                    r"unsupported version.{0,80}(?:status[- ]?detail\s*[:=]?\s*)?`?0x05`?.{0,80}(?:不能泛化|不应泛化|must not generalize).{0,30}(?:parameter error|参数(?:协商)?错误|参数错误)",
                ],
            },
            {
                "id": "iscsi_login_status_detail_02",
                "assertion": (
                    "Initiator Error 的 Login Status-Detail 0x02 表示 Authorization Failure；"
                    "通用参数解析或协商失败不能标成 0x02。"
                ),
                "evidence": [
                    "include/spdk/iscsi_spec.h::ISCSI_LOGIN_AUTHORIZATION_FAIL",
                    "lib/iscsi/iscsi.c::iscsi_op_login_store_incoming_params",
                ],
                "conflict_patterns": [
                    r"(?:参数(?:解析|协商)?失败|parameter (?:parse|negotiation) (?:error|failure)).{0,100}(?:status[- ]?detail|detail)\s*[:=]?\s*`?0x02`?",
                    r"(?:status[- ]?detail|detail)\s*[:=]?\s*`?0x02`?.{0,100}(?:参数(?:解析|协商)?失败|parameter (?:parse|negotiation) (?:error|failure))",
                ],
                "correction_patterns": [
                    r"(?:status[- ]?detail\s*[:=]?\s*)?`?0x02`?.{0,80}(?:表示|是|means).{0,40}(?:authorization failure|授权失败).{0,100}(?:不是|并非|不能|不应|not).{0,50}(?:参数(?:解析|协商)?失败|parameter)",
                ],
            },
            {
                "id": "iscsi_param_bounds_checked",
                "assertion": (
                    "iscsi_parse_param 使用有界 strnlen、memchr 以及 key/value 长度上限；"
                    "不能把“缺少边界检查/必然越界”写成已确认缺陷。未经验证只能标成假设。"
                ),
                "evidence": ["lib/iscsi/param.c::iscsi_parse_param"],
                "conflict_patterns": [
                    r"iscsi_parse_param(?:s)?.{0,180}(?:未(?:做|进行|对).{0,40}(?:边界|长度)检查|写入越界|缓冲区溢出)",
                    r"(?:缓冲区溢出|写入越界).{0,180}iscsi_parse_param(?:s)?",
                ],
                "correction_patterns": [
                    r"(?:假设|若|如果|可能|潜在|待验证|需核验|尚未确认|hypothes).{0,180}(?:边界|越界|溢出)",
                ],
            },
            {
                "id": "iscsi_negotiate_params_bounds_checked",
                "assertion": (
                    "iscsi_negotiate_params 使用 alloc_len、剩余空间检查和有界 snprintf；"
                    "不能把响应数据段写越界描述成已确认缺陷。未经验证只能标成假设。"
                ),
                "evidence": ["lib/iscsi/param.c::iscsi_negotiate_params"],
                "conflict_patterns": [
                    r"iscsi_negotiate_params.{0,220}(?:响应数据段溢出|写入越界|buffer overflow|缓冲区溢出)",
                    r"(?:响应数据段溢出|写入越界|buffer overflow|缓冲区溢出).{0,220}iscsi_negotiate_params",
                ],
                "correction_patterns": [
                    r"(?:假设|若|如果|可能|潜在|待验证|需核验|尚未确认|hypothes).{0,220}(?:越界|溢出|overflow)",
                    r"iscsi_negotiate_params.{0,220}(?:alloc_len|剩余空间|snprintf).{0,160}(?:防止|避免|限制|有界|bounded)",
                ],
            },
            {
                "id": "iscsi_unverified_cleanup_or_lock_defect",
                "assertion": (
                    "SFMEA 可以分析清理或并发失效假设，但在没有具体错误路径证据时，"
                    "不能断言析构未调用或共享配置未加锁；必须明确标注为待验证假设。"
                ),
                "evidence": [
                    "lib/iscsi/conn.c::_iscsi_conn_destruct",
                    "lib/iscsi/iscsi.c::iscsi_op_login_session_discovery_chap",
                ],
                "conflict_patterns": [
                    r"_iscsi_conn_destruct.{0,160}未.{0,40}(?:调用|执行)",
                    r"(?:未.{0,40}(?:调用|执行)).{0,160}_iscsi_conn_destruct",
                    r"(?:g_iscsi|共享数据).{0,160}(?:未加锁|没有锁|无锁保护)",
                ],
                "correction_patterns": [
                    r"(?:假设|若|如果|可能|潜在|待验证|需核验|尚未确认|hypothes).{0,200}(?:未.{0,40}(?:调用|执行)|未加锁|没有锁|无锁保护)",
                ],
            },
        ],
    ),
    "nvmeof_transport": _profile(
        name="NVMe-oF transport/connect/IO",
        aliases=["nvme", "nvmeof", "nvmf", "nvme-o-f", "tcp", "transport", "connect", "queue"],
        scenarios=["connect", "authentication", "queue creation", "IO submit/complete", "disconnect/reconnect", "controller reset"],
        failure_modes=["connect timeout", "queue teardown leak", "controller reset race", "IO completion loss", "transport error propagation"],
        observability=["nvme connect status", "RPC result", "SPDK logs", "host-visible namespace state", "test/nvmf output"],
        graybox_evidence=["transport ops", "controller state", "request completion path"],
        source_entries=["lib/nvmf", "lib/nvmf/tcp.c"],
        test_dirs=["test/nvmf"],
    ),
    "security_tls": _profile(
        name="TLS/security handshake",
        aliases=["tls", "ssl", "certificate", "cert", "key", "psk", "auth"],
        scenarios=["valid certificate", "expired certificate", "wrong identity", "cipher mismatch", "credential rotation"],
        failure_modes=["handshake failure", "silent downgrade", "credential leak", "bad error reporting"],
        observability=["connection result", "TLS alert/log", "RPC/config status", "certificate file diagnostics"],
        graybox_evidence=["TLS config parsing", "auth handshake branch", "credential loading path"],
        source_entries=["lib/nvmf", "lib/sock", "lib/iscsi"],
        test_dirs=["test/nvmf", "test/iscsi_tgt"],
    ),
    "tcp_network": _profile(
        name="TCP/network disruption",
        aliases=["tcp", "network", "timeout", "reconnect", "disconnect", "packet loss"],
        scenarios=["timeout", "disconnect", "reconnect", "partial write", "address conflict"],
        failure_modes=["stuck connection", "retry storm", "resource leak", "wrong timeout surface"],
        observability=["socket status", "client-visible error", "logs", "metrics", "reconnect behavior"],
        graybox_evidence=["socket callbacks", "poller path", "transport error mapping"],
        source_entries=["lib/sock", "lib/nvmf", "lib/iscsi"],
        test_dirs=["test/nvmf", "test/iscsi_tgt"],
    ),
    "bdev_io": _profile(
        name="bdev IO lifecycle",
        aliases=["bdev", "block", "io", "submit", "complete", "reset", "failover"],
        scenarios=["open/close", "submit", "complete", "error return", "reset", "I/O drain"],
        failure_modes=["completion lost", "reset while pending", "double close", "wrong error propagation"],
        observability=["RPC status", "fio/bdev test output", "logs", "latency/IO counters"],
        graybox_evidence=["bdev descriptor", "I/O channel", "completion callback"],
        source_entries=["lib/bdev"],
        test_dirs=["test/bdev"],
    ),
    "rpc_config": _profile(
        name="RPC/config",
        aliases=["rpc", "jsonrpc", "config", "parameter", "duplicate"],
        scenarios=["invalid parameter", "duplicate call", "order error", "partial success rollback"],
        failure_modes=["stale config", "unclear error", "non-idempotent retry", "partial rollback failure"],
        observability=["RPC response", "config dump", "logs", "process state"],
        graybox_evidence=["RPC handler", "config object", "rollback path"],
        source_entries=["lib/rpc", "lib/jsonrpc"],
        test_dirs=["test/rpc"],
    ),
    "reactor_thread_poller": _profile(
        name="reactor/thread/poller",
        aliases=["reactor", "thread", "poller", "message", "scheduler"],
        scenarios=["cross-thread message", "poller blocking", "long task scheduling", "shutdown ordering"],
        failure_modes=["deadlock", "poller starvation", "message ordering bug", "shutdown hang"],
        observability=["thread logs", "latency", "task completion", "shutdown status"],
        graybox_evidence=["thread message queue", "poller registration", "reactor loop"],
        source_entries=["lib/thread", "lib/event"],
        test_dirs=["test/thread", "test/event"],
    ),
    "persistence_recovery": _profile(
        name="persistence/recovery",
        aliases=["blobstore", "ftl", "metadata", "persist", "recovery", "power loss"],
        scenarios=["metadata recovery", "space exhaustion", "unclean shutdown", "replay"],
        failure_modes=["metadata corruption", "lost allocation", "recovery hang", "wrong rollback"],
        observability=["mount result", "state after restart", "logs", "integrity check"],
        graybox_evidence=["metadata load", "superblock path", "replay path"],
        source_entries=["lib/blobstore", "lib/ftl"],
        test_dirs=["test/blobstore", "test/ftl"],
    ),
    "performance_regression": _profile(
        name="performance/regression",
        aliases=["performance", "latency", "throughput", "regression", "soak"],
        scenarios=["baseline throughput", "tail latency", "resource saturation", "long run"],
        failure_modes=["latency spike", "throughput drop", "memory growth", "CPU spin"],
        observability=["latency histogram", "throughput", "CPU/memory metrics", "logs"],
        graybox_evidence=["hot path", "poller cost", "queue depth behavior"],
        source_entries=["lib"],
        test_dirs=["test"],
    ),
    "resource_lifecycle": _profile(
        name="resource lifecycle",
        aliases=["resource", "leak", "cleanup", "free", "close", "teardown"],
        scenarios=["allocation failure", "partial init", "cleanup", "double close", "error path"],
        failure_modes=["leak", "use after free", "double free", "stale handle"],
        observability=["process memory", "logs", "repeat operation behavior", "sanitizer/test output"],
        graybox_evidence=["goto err path", "free/close pairing", "ownership transfer"],
        source_entries=["lib"],
        test_dirs=["test"],
    ),
    "concurrency_race": _profile(
        name="concurrency/race",
        aliases=["concurrency", "race", "parallel", "multi", "thread", "simultaneous"],
        scenarios=["parallel requests", "cancel during operation", "shutdown with in-flight IO", "repeated reconnect"],
        failure_modes=["race", "lost wakeup", "ordering violation", "deadlock"],
        observability=["operation outcome", "logs", "latency", "state convergence"],
        graybox_evidence=["lock boundary", "state transition", "thread handoff"],
        source_entries=["lib"],
        test_dirs=["test"],
    ),
    "observability_diagnostics": _profile(
        name="observability/diagnostics",
        aliases=["observability", "diagnostic", "log", "metric", "trace", "error message"],
        scenarios=["clear error", "log correlation", "metric update", "diagnostic package"],
        failure_modes=["misleading error", "missing log", "missing metric", "sensitive data exposure"],
        observability=["logs", "metrics", "RPC error", "diagnostic bundle"],
        graybox_evidence=["log branch", "error code mapping", "metric increment"],
        source_entries=["lib"],
        test_dirs=["test"],
    ),
}


SPDK_PROJECT_PROFILE: dict[str, Any] = {
    "project": "spdk",
    "modules": {
        "lib/nvmf": {"profiles": ["nvmeof_transport", "security_tls", "tcp_network"], "test_roots": ["test/nvmf"]},
        "lib/iscsi": {"profiles": ["iscsi_login", "security_tls", "tcp_network"], "test_roots": ["test/iscsi_tgt"]},
        "lib/bdev": {"profiles": ["bdev_io", "resource_lifecycle", "performance_regression"], "test_roots": ["test/bdev"]},
        "lib/blobstore": {"profiles": ["persistence_recovery", "resource_lifecycle"], "test_roots": ["test/blobstore"]},
        "lib/thread": {"profiles": ["reactor_thread_poller", "concurrency_race"], "test_roots": ["test/thread"]},
        "lib/event": {"profiles": ["reactor_thread_poller", "concurrency_race"], "test_roots": ["test/event"]},
        "lib/rpc": {"profiles": ["rpc_config", "observability_diagnostics"], "test_roots": ["test/rpc"]},
        "lib/jsonrpc": {"profiles": ["rpc_config", "observability_diagnostics"], "test_roots": ["test/rpc"]},
    },
}


BLACK_BOX_REQUIRED_DIMENSIONS = [
    "normal_path",
    "invalid_input",
    "resource_pressure",
    "timeout",
    "reconnect",
    "concurrency",
    "recovery",
    "performance",
]


ARTIFACT_TEMPLATES: dict[str, dict[str, Any]] = {
    "module_analysis.md": {
        "preview": "markdown",
        "sections": [
            "分析范围",
            "模块边界",
            "关键入口与调用链",
            "主流程",
            "异常与恢复路径",
            "源码与测试证据",
            "测试关注点",
            "证据缺口",
        ],
        "required_fields": ["module", "entry_points", "flows", "evidence", "test_mapping"],
    },
    "project_structure.md": {"preview": "markdown", "sections": ["项目结构", "测试相关目录", "入口说明"], "required_fields": ["source_roots", "test_roots"]},
    "source_reading_plan.md": {"preview": "markdown", "sections": ["阅读目标", "阅读顺序", "证据缺口"], "required_fields": ["target", "read_order", "evidence_policy"]},
    "module_map.md": {"preview": "markdown", "sections": ["模块边界", "入口", "依赖", "测试映射"], "required_fields": ["module", "entries", "test_mapping"]},
    "business_flow.md": {"preview": "markdown", "sections": ["外部触发", "流程步骤", "异常分支", "观测点"], "required_fields": ["steps", "evidence"]},
    "tester_code_understanding.md": {"preview": "markdown", "sections": ["测试视角摘要", "可观测行为", "不可直接依赖的内部细节"], "required_fields": ["observable_behavior", "boundaries"]},
    "sfmea.json": {
        "preview": "table",
        "required_fields": ["failure_mode", "cause", "effect", "detection", "severity", "occurrence", "detection_score", "rpn", "score_explanation", "mitigation", "source_evidence", "test_mapping"],
        "schema": {"type": "array"},
    },
    "black_box_cases.json": {
        "preview": "table",
        "required_fields": ["case_id", "test_dimension", "scenario_name", "preconditions", "steps", "expected_result", "observability", "failure_diagnostics", "mapped_test_dir", "source_or_test_evidence"],
        "required_dimensions": BLACK_BOX_REQUIRED_DIMENSIONS,
        "schema": {"type": "array"},
    },
    "black_box_cases.md": {"preview": "markdown", "sections": ["用例列表", "观测点", "诊断线索"], "required_fields": ["case_id", "steps", "expected_result"]},
    "test_strategy.md": {"preview": "markdown", "sections": ["范围", "风险", "分层策略", "执行顺序"], "required_fields": ["scope", "risks", "layers"]},
    "test_design.md": {"preview": "markdown", "sections": ["目标", "输入", "用例设计", "覆盖矩阵", "剩余风险"], "required_fields": ["target", "cases", "coverage"]},
    "coverage_gap_report.md": {"preview": "markdown", "sections": ["覆盖缺口", "入口", "补充建议"], "required_fields": ["gaps", "recommendations"]},
    "risk_review.md": {"preview": "markdown", "sections": ["高风险项", "证据", "建议"], "required_fields": ["risks", "evidence"]},
    "execution_checklist.md": {"preview": "markdown", "sections": ["前置检查", "执行步骤", "验收"], "required_fields": ["preflight", "steps", "acceptance"]},
}


def build_test_activity_contract(
    *,
    target: str,
    repo_path: str = "",
    workflow_outputs: list[dict[str, Any]] | None = None,
    user_requirements: str = "",
) -> dict[str, Any]:
    target_text = str(target or "").strip()
    combined_text = " ".join([target_text, str(user_requirements or "")]).strip()
    domain_profiles = _matched_profiles(combined_text)
    project_profile = _spdk_project_profile(repo_path=repo_path, target=combined_text, domain_profiles=domain_profiles)
    required_outputs = _requested_outputs(workflow_outputs or [], combined_text)
    artifact_contract = {
        artifact: _artifact_contract_payload(artifact, template)
        for artifact, template in ARTIFACT_TEMPLATES.items()
        if artifact in required_outputs
    }
    focus_rationale = _focus_rationale(
        domain_profiles=domain_profiles,
        project_profile=project_profile,
        user_requirements=user_requirements,
    )
    return {
        "contract_version": 1,
        "target": target_text,
        "domain_profiles": domain_profiles,
        "project_profile": project_profile,
        "user_requirements": str(user_requirements or ""),
        "required_outputs": required_outputs,
        "focus_rationale": focus_rationale,
        "professional_constraints": [
            dict(constraint)
            for profile_id in domain_profiles
            for constraint in PROFILE_REGISTRY[profile_id].get("professional_constraints", [])
        ],
        "evidence_policy": {
            "source_first": True,
            "prefer_artifacts": ["GitNexus", "CGC"],
            "must_cite_existing_source_or_test": True,
            "unverified_ai_suggestions_label": "ai_suggested_unverified",
        },
        "black_box_boundary": {
            "external_inputs_only": True,
            "forbidden_internal_steps": _unique_strings(
                item
                for profile_id in domain_profiles
                for item in PROFILE_REGISTRY[profile_id].get("forbidden_internal_steps", [])
            ),
        },
        "quality_gates": {
            "min_score": 80,
            "high_risk_requires_source_or_test_evidence": True,
            "black_box_cases_must_not_call_internal_functions": True,
            "missing_required_artifacts_block_delivery": True,
            "required_black_box_dimensions": list(BLACK_BOX_REQUIRED_DIMENSIONS),
        },
        "executor_requirements": {
            "must_receive_full_user_input": True,
            "must_read_workspace_source_unless_user_declines": True,
            "must_generate_declared_artifacts": True,
            "invalid_short_greeting_is_failure": True,
        },
        "artifact_contract": artifact_contract,
    }


def audit_test_activity_artifacts(
    *,
    artifact_dir: str | Path,
    contract: dict[str, Any],
    repo_path: str = "",
) -> dict[str, Any]:
    root = Path(artifact_dir)
    repo = Path(str(repo_path or ""))
    issues: list[dict[str, Any]] = []
    for artifact, spec in (contract.get("artifact_contract") or {}).items():
        path = _artifact_path(root, artifact)
        if not path.exists():
            issues.append(_issue("missing_required_artifact", artifact, f"缺少交付件 {artifact}"))
            continue
        if artifact.endswith(".json"):
            payload = _read_json(path)
            issues.extend(_audit_json_artifact(artifact=artifact, payload=payload, spec=spec, repo=repo))
        else:
            content = path.read_text(encoding="utf-8", errors="ignore").strip()
            if not content:
                issues.append(_issue("empty_artifact", artifact, f"{artifact} 内容为空"))
            elif artifact.endswith(".md"):
                issues.extend(
                    _audit_markdown_artifact(
                        artifact=artifact,
                        content=content,
                        spec=spec,
                        repo=repo,
                    )
                )
    score = max(0, 100 - len(issues) * 15)
    status = "deliverable" if score >= int((contract.get("quality_gates") or {}).get("min_score") or 80) and not issues else "needs_rework"
    return {
        "kind": "test_activity_quality_audit",
        "status": status,
        "deliverable": status == "deliverable",
        "score": score,
        "issue_count": len(issues),
        "issues": issues,
        "recommendations": _recommendations_for_issues(issues),
    }


def audit_test_activity_response(
    *,
    content: str,
    contract: dict[str, Any],
    repo_path: str = "",
) -> dict[str, Any]:
    """Audit a combined Markdown deliverable produced in an AI thread.

    Workbench executions write one file per declared artifact and use
    ``audit_test_activity_artifacts``. Built-in chat models cannot write those
    files directly, so their downloadable Markdown must satisfy the same
    contract before CodeTalk labels it complete.
    """

    text = str(content or "").strip()
    lower = text.lower()
    required_outputs = {
        str(item).strip()
        for item in contract.get("required_outputs") or []
        if str(item).strip()
    }
    issues: list[dict[str, Any]] = []
    min_chars = 1200 if len(required_outputs) >= 3 else 700
    if len(text) < min_chars:
        issues.append(
            _issue(
                "response_too_short",
                "assistant-output.md",
                f"组合交付件仅 {len(text)} 字符，无法覆盖声明的测试活动输出",
            )
        )

    if "business_flow.md" in required_outputs:
        numbered_steps = re.findall(r"(?m)^\s*(?:\d+[.)]|步骤\s*\d+)\s*", text)
        named_flows = re.findall(
            r"(?mi)^\s*#{2,6}\s*(?:流程|flow)\s*(?:[a-z]|\d+|[一二三四五六七八九十]+)\s*[:：.)、-]",
            text,
        )
        has_flow_marker = any(marker in lower for marker in ("流程", "flow", "状态迁移"))
        has_failure_or_recovery = any(
            marker in lower
            for marker in ("异常", "失败", "恢复", "清理", "error", "failure", "recovery")
        )
        if (
            not has_flow_marker
            or max(len(numbered_steps), len(named_flows)) < 3
            or not has_failure_or_recovery
        ):
            issues.append(
                _issue(
                    "missing_combined_business_flow",
                    "business_flow.md",
                    "组合交付件缺少至少 3 步的代码流程与异常/恢复说明",
                )
            )

    if "sfmea.json" in required_outputs:
        sfmea_fields = {
            "failure_mode": ("failure_mode", "failure mode", "失效模式"),
            "cause": ("cause", "原因"),
            "effect": ("effect", "影响", "后果"),
            "detection": ("detection", "探测", "检测"),
            "severity": ("severity", "严重度"),
            "occurrence": ("occurrence", "发生度"),
            "detection_score": ("detection_score", "探测度", "检测评分"),
            "rpn": ("rpn",),
            "mitigation": ("mitigation", "缓解", "改进措施"),
            "evidence": ("source_evidence", "源码证据", "证据"),
            "test_mapping": ("test_mapping", "测试映射", "测试目录"),
        }
        missing = [
            field
            for field, aliases in sfmea_fields.items()
            if not any(alias in lower for alias in aliases)
        ]
        has_sfmea_rows = (
            text.count("\n|") >= 3
            or len(re.findall(r"(?mi)^\s*[-*]\s+.*rpn", text)) >= 2
            or _combined_json_array_has_fields(text, {"failure_mode", "rpn", "source_evidence", "test_mapping"})
        )
        if "sfmea" not in lower or missing or not has_sfmea_rows:
            issues.append(
                _issue(
                    "missing_combined_sfmea",
                    "sfmea.json",
                    "组合交付件的 SFMEA 字段或风险条目不完整",
                    fields=missing,
                )
            )

    black_box_dimensions_complete = False
    if "black_box_cases.json" in required_outputs:
        field_aliases = (
            ("前置条件", "precondition"),
            ("步骤", "steps"),
            ("预期结果", "expected_result", "expected result"),
            ("观测点", "observability"),
            ("失败诊断", "failure_diagnostics", "diagnostic"),
        )
        missing_fields = [
            aliases[0]
            for aliases in field_aliases
            if not any(alias in lower for alias in aliases)
        ]
        dimension_aliases = {
            "normal_path": ("normal_path", "normal path", "正常路径", "正常场景"),
            "invalid_input": ("invalid_input", "invalid input", "非法输入", "无效输入"),
            "resource_pressure": ("resource_pressure", "resource pressure", "资源不足", "资源压力"),
            "timeout": ("timeout", "超时"),
            "reconnect": ("reconnect", "重连"),
            "concurrency": ("concurrency", "并发"),
            "recovery": ("recovery", "恢复"),
            "performance": ("performance", "性能"),
        }
        missing_dimensions = [
            dimension
            for dimension, aliases in dimension_aliases.items()
            if not any(alias in lower for alias in aliases)
        ]
        black_box_dimensions_complete = not missing_dimensions
        if missing_fields:
            issues.append(
                _issue(
                    "missing_combined_black_box_fields",
                    "black_box_cases.json",
                    "组合交付件的黑盒用例字段不完整",
                    fields=missing_fields,
                )
            )
        if missing_dimensions:
            issues.append(
                _issue(
                    "missing_combined_black_box_dimensions",
                    "black_box_cases.json",
                    "组合交付件缺少黑盒测试维度: " + ", ".join(missing_dimensions),
                    dimensions=missing_dimensions,
                )
            )

    if "test_design.md" in required_outputs:
        required_markers = {
            "target": ("目标", "target"),
            "input": ("输入", "input"),
            "cases": ("用例", "case"),
            "coverage": ("覆盖", "coverage"),
            "remaining_risk": (
                "剩余风险",
                "residual risk",
                "待验证",
                "已知限制",
                "ai_suggested_unverified",
            ),
        }
        missing = [
            field
            for field, aliases in required_markers.items()
            if not (field == "coverage" and black_box_dimensions_complete)
            and not any(alias in lower for alias in aliases)
        ]
        if missing:
            issues.append(
                _issue(
                    "missing_combined_test_design",
                    "test_design.md",
                    "组合交付件的测试设计章节不完整",
                    fields=missing,
                )
            )

    repo = Path(str(repo_path or "")).expanduser()
    evidence_paths = _combined_response_evidence_paths(text)
    if not evidence_paths:
        issues.append(
            _issue(
                "missing_combined_source_evidence",
                "assistant-output.md",
                "组合交付件没有引用工作区源码或测试目录证据",
            )
        )
    elif repo.is_dir():
        missing_paths = [
            path
            for path in evidence_paths
            if not (repo / path).exists() and not _looks_like_runtime_generated_path(path)
        ]
        for path in missing_paths:
            issues.append(
                _issue(
                    "evidence_path_not_found",
                    "assistant-output.md",
                    f"证据路径不存在: {path}",
                )
            )
    if required_outputs.intersection({"sfmea.json", "black_box_cases.json"}):
        specific_test_paths = [
            path
            for path in evidence_paths
            if path.lower().startswith(("test/", "tests/")) and Path(path).suffix
        ]
        if not specific_test_paths:
            issues.append(
                _issue(
                    "missing_specific_test_evidence",
                    "assistant-output.md",
                    "组合交付件只引用了测试目录，没有映射到具体测试脚本或用例文件",
                )
            )

    issues.extend(_audit_professional_constraints(text, contract))

    score = max(0, 100 - len(issues) * 15)
    status = "deliverable" if not issues and score >= int((contract.get("quality_gates") or {}).get("min_score") or 80) else "needs_rework"
    return {
        "kind": "test_activity_quality_audit",
        "source": "ai_thread_combined_markdown",
        "status": status,
        "deliverable": status == "deliverable",
        "score": score,
        "issue_count": len(issues),
        "issues": issues,
        "recommendations": _recommendations_for_issues(issues),
    }


def _combined_json_array_has_fields(content: str, required_fields: set[str]) -> bool:
    for block in re.findall(r"```json\s*(\[.*?\])\s*```", content, flags=re.IGNORECASE | re.DOTALL):
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, list) or len(payload) < 2:
            continue
        valid_items = [item for item in payload if isinstance(item, dict)]
        if len(valid_items) >= 2 and all(required_fields.issubset(item) for item in valid_items):
            return True
    return False


def _audit_professional_constraints(
    content: str,
    contract: dict[str, Any],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for constraint in contract.get("professional_constraints") or []:
        if not isinstance(constraint, dict):
            continue
        conflict_found = False
        for pattern in constraint.get("conflict_patterns") or []:
            try:
                conflicts = list(re.finditer(str(pattern), content, flags=re.IGNORECASE))
            except re.error:
                continue
            if not conflicts:
                continue
            for conflict in conflicts:
                statement = _professional_statement_window(content, conflict.start(), conflict.end())
                if _matches_professional_correction(statement, constraint):
                    continue
                conflict_found = True
                break
            if not conflict_found:
                continue
            issues.append(
                _issue(
                    "professional_fact_conflict",
                    "assistant-output.md",
                    "交付件与已验证的领域事实冲突：" + str(constraint.get("assertion") or ""),
                    constraint_id=str(constraint.get("id") or ""),
                    evidence=[str(item) for item in constraint.get("evidence") or []],
                )
            )
            break
    return issues


def _professional_statement_window(content: str, start: int, end: int) -> str:
    boundaries = "\n。！？"
    left = max((content.rfind(marker, 0, start) for marker in boundaries), default=-1) + 1
    right_candidates = [
        position
        for marker in boundaries
        if (position := content.find(marker, end)) >= 0
    ]
    right = min(right_candidates) + 1 if right_candidates else len(content)
    return content[left:right]


def _matches_professional_correction(statement: str, constraint: dict[str, Any]) -> bool:
    for pattern in constraint.get("correction_patterns") or []:
        try:
            if re.search(str(pattern), statement, flags=re.IGNORECASE | re.DOTALL):
                return True
        except re.error:
            continue
    return False


def _combined_response_evidence_paths(content: str) -> list[str]:
    candidates = re.findall(
        r"`((?:app|lib|module|src|test|tests)/[^`\s:]+)(?::\d+(?:-\d+)?)?`",
        str(content or ""),
        flags=re.IGNORECASE,
    )
    return _unique_strings(
        path.rstrip(".,;，。；")
        for path in candidates
        if not any(marker in path for marker in "*?[]")
    )


def _looks_like_runtime_generated_path(path: str) -> bool:
    candidate = Path(str(path or ""))
    generated_suffixes = {".csv", ".html", ".json", ".log", ".xml", ".xlsx"}
    generated_markers = {"artifact", "artifacts", "output", "outputs", "report", "reports", "result", "results"}
    return candidate.suffix.lower() in generated_suffixes and any(
        any(marker in part.lower() for marker in generated_markers)
        for part in candidate.parts[:-1]
    )


def _matched_profiles(text: str) -> list[str]:
    matched: list[str] = []
    for profile_id, profile in PROFILE_REGISTRY.items():
        aliases = [profile_id.replace("_", " "), *profile.get("aliases", [])]
        if any(_term_matches(text, alias) for alias in aliases):
            matched.append(profile_id)
    if not matched and _term_matches(text, "kv"):
        matched.extend(["bdev_io", "persistence_recovery", "performance_regression"])
    if "nvmeof_transport" in matched and _term_matches(text, "tls") and "security_tls" not in matched:
        matched.append("security_tls")
    if "nvmeof_transport" in matched and _term_matches(text, "tcp") and "tcp_network" not in matched:
        matched.append("tcp_network")
    return matched or ["observability_diagnostics"]


def _term_matches(text: str, term: str) -> bool:
    haystack = str(text or "").lower()
    needle = str(term or "").lower().strip()
    if not needle:
        return False
    if len(needle) <= 3 and needle.isalpha():
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack))
    return needle in haystack


def _spdk_project_profile(*, repo_path: str, target: str, domain_profiles: list[str]) -> dict[str, Any]:
    source_roots: list[str] = []
    test_roots: list[str] = []
    validated_test_mappings: list[str] = []
    related_profiles: list[str] = []
    for root, payload in SPDK_PROJECT_PROFILE["modules"].items():
        profiles = [str(item) for item in payload.get("profiles") or []]
        if root in target or any(profile in domain_profiles for profile in profiles):
            source_roots.append(root)
            test_roots.extend(str(item) for item in payload.get("test_roots") or [])
            related_profiles.extend(profiles)
    for profile_id in domain_profiles:
        profile = PROFILE_REGISTRY.get(profile_id, {})
        source_roots.extend(str(item) for item in profile.get("recommended_source_entries") or [])
        test_roots.extend(str(item) for item in profile.get("recommended_test_dirs") or [])
        validated_test_mappings.extend(str(item) for item in profile.get("validated_test_mappings") or [])
    return {
        "project": "spdk" if "spdk" in str(repo_path).lower() or source_roots else "generic",
        "source_roots": _unique_strings(source_roots),
        "test_roots": _unique_strings(test_roots),
        "validated_test_mappings": _unique_strings(validated_test_mappings),
        "related_profiles": _unique_strings(related_profiles),
    }


def _requested_outputs(outputs: list[dict[str, Any]], text: str) -> list[str]:
    requested = [
        str(item.get("artifact") or item.get("path") or "").strip()
        for item in outputs
        if isinstance(item, dict)
        and str(item.get("artifact") or item.get("path") or "").strip() in ARTIFACT_TEMPLATES
    ]
    lower = text.lower()
    keyword_map = {
        "sfmea": "sfmea.json",
        "黑盒": "black_box_cases.json",
        "测试用例": "black_box_cases.json",
        "测试策略": "test_strategy.md",
        "测试设计": "test_design.md",
        "流程": "business_flow.md",
        "模块": "module_map.md",
        "项目结构": "project_structure.md",
    }
    for keyword, artifact in keyword_map.items():
        if keyword in lower or keyword in text:
            if artifact == "black_box_cases.json" and any(
                item in requested for item in ("black_box_cases.json", "black_box_cases.md")
            ):
                continue
            requested.append(artifact)
    return _unique_strings(requested or ["business_flow.md", "sfmea.json", "black_box_cases.json"])


def _artifact_contract_payload(artifact: str, template: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "artifact": artifact,
        "preview": str(template.get("preview") or ""),
        "required_fields": [str(item) for item in template.get("required_fields") or []],
        "sections": [str(item) for item in template.get("sections") or []],
        "quality_checks": [
            "required_fields_present",
            "source_or_test_evidence_present",
            "black_box_boundary_respected",
        ],
        "download_filename": artifact,
    }
    if isinstance(template.get("schema"), dict):
        payload["schema"] = dict(template["schema"])
    if isinstance(template.get("required_dimensions"), list):
        payload["required_dimensions"] = [
            str(item) for item in template["required_dimensions"] if str(item).strip()
        ]
    return payload


def _focus_rationale(
    *,
    domain_profiles: list[str],
    project_profile: dict[str, Any],
    user_requirements: str,
) -> list[dict[str, Any]]:
    rationale = []
    if str(user_requirements or "").strip():
        rationale.append({"source": "user_explicit_requirement", "summary": str(user_requirements).strip()[:500]})
    for profile_id in domain_profiles:
        rationale.append({
            "source": "domain_test_profile",
            "profile_id": profile_id,
            "summary": PROFILE_REGISTRY[profile_id]["name"],
        })
    if project_profile.get("source_roots") or project_profile.get("test_roots"):
        rationale.append({
            "source": "project_source_and_test_layout",
            "source_roots": project_profile.get("source_roots") or [],
            "test_roots": project_profile.get("test_roots") or [],
        })
    rationale.append({"source": "team_policy", "summary": "源码优先、黑盒边界、低质量产物需补证据"})
    return rationale


def _audit_json_artifact(
    *,
    artifact: str,
    payload: Any,
    spec: dict[str, Any],
    repo: Path,
) -> list[dict[str, Any]]:
    if payload is None:
        return [_issue("invalid_json", artifact, f"{artifact} 不是有效 JSON")]
    rows = payload if isinstance(payload, list) else payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return [_issue("json_shape_invalid", artifact, f"{artifact} 必须是数组或包含 items 数组")]
    if not rows:
        return [_issue("empty_json_items", artifact, f"{artifact} 没有任何可交付条目")]
    issues: list[dict[str, Any]] = []
    required_fields = [str(item) for item in spec.get("required_fields") or []]
    seen_case_ids: set[str] = set()
    seen_case_signatures: set[str] = set()
    observed_dimensions: set[str] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            issues.append(_issue("json_item_invalid", artifact, f"{artifact} 第 {index} 项不是对象"))
            continue
        missing = [
            field for field in required_fields
            if not _field_present(row, field)
        ]
        if missing:
            code = "missing_sfmea_fields" if artifact == "sfmea.json" else "missing_black_box_fields"
            issues.append(_issue(code, artifact, f"{artifact} 第 {index} 项缺少字段: {', '.join(missing)}", index=index, fields=missing))
        if artifact.startswith("black_box") and _black_box_boundary_violation(row):
            issues.append(_issue("black_box_boundary_violation", artifact, f"{artifact} 第 {index} 项混入内部函数调用或修改源码步骤", index=index))
        if artifact == "sfmea.json":
            issues.extend(_audit_sfmea_scores(row, artifact=artifact, index=index))
        if artifact == "black_box_cases.json":
            dimension = str(row.get("test_dimension") or "").strip().lower()
            if dimension:
                observed_dimensions.add(dimension)
            duplicate_reason = _black_box_duplicate_reason(
                row,
                seen_case_ids=seen_case_ids,
                seen_case_signatures=seen_case_signatures,
            )
            if duplicate_reason:
                issues.append(
                    _issue(
                        "duplicate_black_box_case",
                        artifact,
                        f"{artifact} 第 {index} 项与已有用例重复: {duplicate_reason}",
                        index=index,
                    )
                )
        evidence_values = _evidence_strings(row)
        if artifact in {"sfmea.json", "black_box_cases.json"} and not evidence_values:
            issues.append(_issue("missing_source_or_test_evidence", artifact, f"{artifact} 第 {index} 项缺少源码或测试证据", index=index))
        for evidence in _strict_evidence_path_strings(row):
            if _looks_like_repo_path(evidence) and not _repo_path_exists(repo, evidence):
                issues.append(_issue("evidence_path_not_found", artifact, f"证据路径不存在: {evidence}", index=index))
    if artifact == "black_box_cases.json":
        required_dimensions = {
            str(item).strip().lower()
            for item in spec.get("required_dimensions") or []
            if str(item).strip()
        }
        missing_dimensions = sorted(required_dimensions - observed_dimensions)
        if missing_dimensions:
            issues.append(
                _issue(
                    "missing_black_box_dimensions",
                    artifact,
                    "black_box_cases.json 缺少测试维度: " + ", ".join(missing_dimensions),
                    dimensions=missing_dimensions,
                )
            )
    return issues


def _audit_markdown_artifact(
    *,
    artifact: str,
    content: str,
    spec: dict[str, Any],
    repo: Path,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    heading_matches = list(
        re.finditer(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", content, flags=re.MULTILINE)
    )
    required_sections = [str(item) for item in spec.get("sections") or []]
    section_headings: dict[str, tuple[int, re.Match[str]]] = {}
    for index, match in enumerate(heading_matches):
        normalized = _normalized_markdown_heading(match.group(1))
        if normalized in required_sections and normalized not in section_headings:
            section_headings[normalized] = (index, match)
    missing_sections = [
        section
        for section in required_sections
        if section not in section_headings
    ]
    if missing_sections:
        issues.append(
            _issue(
                "missing_markdown_sections",
                artifact,
                f"{artifact} 缺少章节: {', '.join(missing_sections)}",
                sections=missing_sections,
            )
        )

    empty_sections: list[str] = []
    for section in required_sections:
        section_heading = section_headings.get(section)
        if section_heading is None:
            continue
        index, match = section_heading
        end = heading_matches[index + 1].start() if index + 1 < len(heading_matches) else len(content)
        if not content[match.end():end].strip():
            empty_sections.append(section)
    if empty_sections:
        issues.append(
            _issue(
                "empty_markdown_sections",
                artifact,
                f"{artifact} 章节内容为空: {', '.join(empty_sections)}",
                sections=empty_sections,
            )
        )

    evidence_paths = _markdown_repo_paths(content)
    existing_evidence_paths = [
        path for path in evidence_paths if _repo_path_exists(repo, path)
    ]
    source_paths = [
        path for path in existing_evidence_paths
        if path.startswith(("lib/", "include/", "module/", "app/"))
    ]
    test_paths = [path for path in existing_evidence_paths if path.startswith("test/")]
    if not source_paths:
        issues.append(
            _issue(
                "missing_source_evidence",
                artifact,
                f"{artifact} 缺少可核验的源码路径证据",
            )
        )
    if not test_paths:
        issues.append(
            _issue(
                "missing_test_evidence",
                artifact,
                f"{artifact} 缺少可核验的测试目录或测试文件证据",
            )
        )
    for evidence in evidence_paths:
        if not _repo_path_exists(repo, evidence):
            issues.append(
                _issue(
                    "evidence_path_not_found",
                    artifact,
                    f"证据路径不存在: {evidence}",
                )
            )
    return issues


def _audit_sfmea_scores(
    row: dict[str, Any],
    *,
    artifact: str,
    index: int,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    scores: dict[str, int] = {}
    for field in ("severity", "occurrence", "detection_score"):
        value = row.get(field)
        if field == "severity" and _integer_score(value) is None:
            value = row.get("severity_score")
        if field == "occurrence" and _integer_score(value) is None:
            value = row.get("occurrence_score")
        score = _integer_score(value)
        if score is not None:
            scores[field] = score
        if score is None or not 1 <= score <= 10:
            issues.append(
                _issue(
                    "sfmea_score_out_of_range",
                    artifact,
                    f"{artifact} 第 {index} 项 {field} 必须是 1-10 的整数",
                    index=index,
                    field=field,
                )
            )
    rpn = _integer_score(row.get("rpn"))
    if len(scores) == 3 and rpn is not None:
        expected = scores["severity"] * scores["occurrence"] * scores["detection_score"]
        if rpn != expected:
            issues.append(
                _issue(
                    "sfmea_rpn_mismatch",
                    artifact,
                    f"{artifact} 第 {index} 项 RPN 应为 {expected}，实际为 {rpn}",
                    index=index,
                    expected_rpn=expected,
                    actual_rpn=rpn,
                )
            )
    return issues


def _integer_score(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = str(value or "").strip()
    return int(text) if re.fullmatch(r"-?\d+", text) else None


def _black_box_duplicate_reason(
    row: dict[str, Any],
    *,
    seen_case_ids: set[str],
    seen_case_signatures: set[str],
) -> str:
    case_id = re.sub(r"\s+", "", str(row.get("case_id") or "").lower())
    signature = "|".join(
        re.sub(r"\s+", "", text.lower())
        for field in ("scenario_name", "preconditions", "steps", "expected_result")
        for text in _flatten_text(row.get(field))
        if text.strip()
    )
    duplicate = ""
    if case_id and case_id in seen_case_ids:
        duplicate = f"case_id={row.get('case_id')}"
    elif signature and signature in seen_case_signatures:
        duplicate = "场景、前置条件、步骤和预期结果相同"
    if case_id:
        seen_case_ids.add(case_id)
    if signature:
        seen_case_signatures.add(signature)
    return duplicate


def _normalized_markdown_heading(value: str) -> str:
    text = str(value or "").strip().strip("`*_ ")
    text = re.sub(
        r"^(?:第?\s*\d+\s*[章节、.．:：-]?|[一二三四五六七八九十]+\s*[、.．])\s*",
        "",
        text,
    )
    return text.strip().rstrip(":：")


def _markdown_repo_paths(content: str) -> list[str]:
    pattern = re.compile(
        r"(?<![A-Za-z0-9_/])(?:lib|test|include|module|app)/"
        r"[A-Za-z0-9_.+@%/\-]+(?::L?\d+(?:-L?\d+)?)?"
    )
    return _unique_strings(
        match.group(0).rstrip(".,;:)]}`'")
        for match in pattern.finditer(content)
    )


def _black_box_boundary_violation(row: dict[str, Any]) -> bool:
    action_fields = [
        row.get("steps"),
        row.get("inputs"),
        row.get("operations"),
        row.get("test_steps"),
    ]
    text = " ".join(part for value in action_fields for part in _flatten_text(value)).lower()
    return bool(re.search(r"\b(call|invoke)\s+[a-z0-9_]*\(|直接调用|调用内部函数|修改源码|private struct|internal function", text))


def _field_present(row: dict[str, Any], field: str) -> bool:
    value = row.get(field)
    if value not in (None, "", []):
        return True
    aliases = {
        "severity": ["severity_score"],
        "occurrence": ["occurrence_score"],
        "source_evidence": ["file_path", "source_file"],
        "test_mapping": ["test_directory", "mapped_test_dir", "mitigation"],
        "source_or_test_evidence": ["file_path", "mapped_test_dir", "test_directory"],
        "observability": ["observable_signals"],
        "expected_result": ["expected"],
        "failure_diagnostics": ["diagnostics"],
    }
    return any(row.get(alias) not in (None, "", []) for alias in aliases.get(field, []))


def _evidence_strings(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("source_evidence", "test_mapping", "source_or_test_evidence", "mapped_test_dir", "file_path", "test_directory"):
        value = row.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value if str(item).strip())
        elif isinstance(value, dict):
            values.extend(str(item) for item in value.values() if str(item).strip())
    return values


def _strict_evidence_path_strings(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("source_evidence", "source_or_test_evidence", "file_path", "test_directory"):
        value = row.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value if str(item).strip())
        elif isinstance(value, dict):
            values.extend(str(item) for item in value.values() if str(item).strip())
    return values


def _flatten_text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [part for item in value.values() for part in _flatten_text(item)]
    if isinstance(value, list):
        return [part for item in value for part in _flatten_text(item)]
    return []


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _artifact_path(root: Path, artifact: str) -> Path:
    direct = root / artifact
    if direct.exists():
        return direct
    matches = sorted(root.glob(f"**/{artifact}"))
    return matches[0] if matches else direct


def _looks_like_repo_path(value: str) -> bool:
    text = str(value or "").strip()
    return bool(re.match(r"^(lib|test|include|module|app)/", text))


def _repo_path_exists(repo: Path, value: str) -> bool:
    if not repo.exists():
        return True
    relative = Path(value.split(":", 1)[0])
    if relative.is_absolute() or ".." in relative.parts:
        return False
    try:
        repo_root = repo.resolve()
        candidate = (repo_root / relative).resolve()
    except OSError:
        return False
    return repo_root in candidate.parents and candidate.exists()


def _issue(code: str, artifact: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "artifact": artifact, "message": message, **extra}


def _recommendations_for_issues(issues: list[dict[str, Any]]) -> list[str]:
    if not issues:
        return ["质量门禁已通过，可以交付。"]
    codes = {str(issue.get("code") or "") for issue in issues}
    recommendations: list[str] = []
    if any(code.startswith("missing_") for code in codes):
        recommendations.append("补齐缺失字段、源码证据和测试目录映射后重跑质量审计。")
    if "black_box_boundary_violation" in codes:
        recommendations.append("将黑盒步骤改为外部输入、操作、期望输出和观测点，不要要求调用内部函数或修改源码。")
    if "evidence_path_not_found" in codes:
        recommendations.append("重新检索 GitNexus/CGC 和本地源码，修正不存在的证据引用。")
    if "professional_fact_conflict" in codes:
        recommendations.append("按领域事实锚点重新核对源码和协议常量，修正冲突结论后再交付。")
    return recommendations or ["从低质量交付件重跑，要求执行器严格遵守 TestActivityContract。"]


def _unique_strings(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
