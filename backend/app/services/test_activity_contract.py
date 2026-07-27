"""Testing activity contracts, profiles, artifact templates, and quality audit."""

from __future__ import annotations

import ast
import copy
import builtins
import hashlib
import json
import re
from pathlib import Path
from typing import Any


# This is the generation contract for the complete iSCSI Login activity, not a
# best-effort keyword list.  The quality audit consumes the same labels below;
# keeping one canonical matrix prevents the planner from asking for a broad
# "CHAP negative" case while the delivery gate expects a distinct scenario.
COMPLETE_ISCSI_LOGIN_REQUIRED_ATOMIC_SCENARIOS = (
    "T+C 非法组合",
    "非法 NSG",
    "Unsupported Version",
    "未知合法 key=NotUnderstood",
    "Target not found/removed",
    "Authorization Failure",
    "Redirect",
    "Discovery 后 SendTargets",
    "首 payload 后 timer 注销",
    "错误 CHAP_R",
    "未知 CHAP 用户",
    "CHAP 参数顺序错误",
    "Mutual CHAP 缺失或错误 challenge",
    "Target 要求 Mutual 但 Initiator 未提供",
    "不支持的 CHAP_A 算法",
    "缺少 CHAP_R",
    "CHAP_R 编码格式错误",
    "Mutual 用户或 secret 缺失",
    "Initiator 请求 Mutual 但 Target 禁止",
    "Mutual challenge 合法编码但语义错误",
)


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
        scenarios=[
            "login negotiation",
            "CHAP success/failure in Security Negotiation",
            "CHAP request/response rounds with T, CSG, and NSG assertions",
            "mutual CHAP with valid challenge encoding but a mismatched mutual-secret oracle",
            "CSG 0/1/3, invalid NSG, T+C, and error-response flag clearing",
            "fragmented C-bit parameter assembly",
            "unknown, duplicate, and oversized keys",
            "Discovery Login without TargetAddress followed by SendTargets",
            "session reinstatement with duplicate ISID/CID",
            "digest mismatch",
            "session reset",
            "simultaneous login",
            "multi-connection recovery",
            "allocation failure recovery",
            "login latency baseline and degradation threshold",
        ],
        failure_modes=[
            "bad credentials",
            "redirect loop",
            "digest validation failure",
            "half-open session before and after the first Login PDU",
            "initiator disconnect",
        ],
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
                    r"iscsi_op_login_response.{0,80}(?:处理|整理|发送|write|send).{0,40}(?:响应|response)",
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
                    r"(?:authorization failure|授权失败).{0,80}status[- _]?class\s*[:=]?\s*0x03",
                    r"status[- _]?class\s*[:=]?\s*0x03.{0,80}(?:authorization failure|授权失败)",
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
                    r"(?:接收|入口|处理)[^\n|]{0,60}login request[^\n|]{0,100}iscsi_op_login_rsp_handle_csg_bit",
                    r"iscsi_op_login_rsp_handle_csg_bit[^\n|]{0,100}(?:接收|入口|处理)[^\n|]{0,60}login request",
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
                    r"(?:discovery login|发现登录).{0,180}(?:完成|成功|full feature).{0,220}(?:之后|后|then|after).{0,120}(?:text request|sendtargets)",
                    r"(?:text request|sendtargets).{0,180}(?:discovery login|发现登录).{0,120}(?:完成后|成功后|after)",
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
                    r"(?:iscsi_param_free|spdk_startup).{0,180}(?:不负责|并非|does not).{0,80}(?:连接清理|connection cleanup)",
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
                    r"(?:不得|不能|不可|不应|do not).{0,30}(?:把|将)?\s*(?:status[- ]?detail\s*[:=]?\s*)?0x05.{0,50}(?:写成|标成|视为|map to|treat as).{0,30}(?:parameter error|参数(?:协商)?错误|参数错误)",
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
                    r"(?:未.{0,40}(?:调用|执行)|未加锁|没有锁|无锁保护).{0,220}(?:假设|可能|潜在|待验证|需核验|尚未确认|不能断言|hypothes)",
                    r"(?:证据缺口|未连通|不能证明|无法证明).{0,220}_iscsi_conn_destruct.{0,160}未出现在已验证(?:调用)?(?:分量|调用链|证据)",
                    r"_iscsi_conn_destruct.{0,160}未出现在已验证(?:调用)?(?:分量|调用链|证据).{0,220}(?:证据缺口|未连通|不能证明|无法证明)",
                    r"(?:缺口|证据缺口).{0,180}_iscsi_conn_destruct.{0,120}未在.{0,50}(?:验证|证据)范围.{0,40}(?:直接)?(?:调用|执行)",
                ],
            },
            {
                "id": "iscsi_chap_security_stage",
                "assertion": (
                    "iscsi_auth_params 只在 CSG Security Negotiation (0) 分支执行 CHAP；"
                    "Operational Negotiation (1) 只校验认证已经完成，不能承载 CHAP challenge/response。"
                ),
                "evidence": ["lib/iscsi/iscsi.c::iscsi_op_login_rsp_handle_csg_bit"],
                "conflict_patterns": [
                    r"(?:chap|iscsi_auth_params).{0,120}(?:operational negotiation|操作协商).{0,100}(?:执行|完成|challenge|response|认证)",
                    r"(?:operational negotiation|操作协商).{0,120}(?:chap|iscsi_auth_params).{0,100}(?:执行|完成|challenge|response|认证)",
                ],
                "correction_patterns": [
                    r"(?:不在|不能|并非|not).{0,40}(?:operational negotiation|操作协商)",
                    r"csg\s*[:=]?\s*1.{0,80}(?:不应|不该|不得|不能).{0,40}(?:operational negotiation|操作协商).{0,60}(?:chap|challenge|response)",
                    r"csg\s*[:=]?\s*1.{0,50}(?:不再|不|不能|不得|不可).{0,30}(?:承载|执行|处理).{0,30}chap",
                    r"(?:security negotiation|安全协商).{0,80}(?:chap|iscsi_auth_params)",
                    r"(?:operational negotiation|操作协商).{0,120}(?:chap\s*=\s*none|已配置可通过认证|认证已完成|authentication already complete)",
                    r"(?:operational negotiation|操作协商).{0,80}(?:不承载|不执行|不能承载|does not).{0,60}(?:chap|challenge|response)",
                    r"iscsi_auth_params.{0,120}csg\s*[:=]?\s*0.{0,80}(?:security negotiation|安全协商).{0,160}csg\s*[:=]?\s*1.{0,80}(?:operational negotiation|操作协商).{0,80}(?:只检查|只校验|only checks)",
                    r"(?:避免|防止|不得|不能|不可|do not).{0,40}(?:把|将|treat)?.{0,80}(?:operational negotiation|操作协商).{0,80}(?:chap|iscsi_auth_params).{0,40}(?:执行|阶段|认证)",
                    r"mutual\s*chap.{0,160}(?:oracle|chap_n).{0,180}(?:operational negotiation|操作协商).{0,120}expect_full_feature",
                ],
            },
            {
                "id": "iscsi_chap_request_response_flags",
                "assertion": (
                    "Login Response 继承请求的 T/C/CSG，且只有请求 T=1 时才继承 NSG；"
                    "因此 CHAP 第一轮 T=0 请求的响应也必须 T=0。最终进入 Full Feature 的请求和"
                    "成功响应应为 T=1、NSG=3；当前阶段可按协商路径为 CSG=0 或 CSG=1，不能固定为 CSG=1。"
                ),
                "evidence": ["lib/iscsi/iscsi.c::iscsi_op_login_rsp_init"],
                "conflict_patterns": [
                    r"(?:chap.{0,80})?login request.{0,100}\bt\s*[:=]\s*0.{0,220}login response.{0,100}\bt\s*[:=]\s*1",
                    r"(?:chap.{0,80})?login response.{0,100}\bt\s*[:=]\s*1.{0,220}login request.{0,100}\bt\s*[:=]\s*0",
                    r"(?:第一轮|first round).{0,120}(?:请求|request).{0,80}\bt\s*[:=]\s*0.{0,180}(?:响应|response).{0,80}\bt\s*[:=]\s*1",
                    r"login request[\s\S]{0,260}csg\s*[:=]\s*0[\s\S]{0,260}\bt\s*[:=]\s*0[\s\S]{0,900}login response[\s\S]{0,260}\bt\s*[:=]\s*1",
                    r"(?:第二|second|final transition)[\s\S]{0,160}login request[\s\S]{0,320}\bt\s*[:=]\s*0[\s\S]{0,700}(?:最终|final)[\s\S]{0,100}login response[\s\S]{0,260}\bt\s*[:=]\s*1",
                ],
                "correction_patterns": [
                    r"(?:请求|request).{0,80}\bt\s*[:=]\s*0.{0,160}(?:响应|response).{0,80}(?:继承|保持|remains?|inherits?).{0,40}\bt\s*[:=]\s*0",
                    r"(?:响应|response).{0,100}\bt\s*[:=]\s*1.{0,40}(?:若|仅当|if|only when).{0,40}(?:最终|final)",
                ],
            },
            {
                "id": "iscsi_login_error_flags_cleared",
                "assertion": (
                    "任何非成功 Login Response 都会清除 T、CSG 和 NSG；认证失败响应不能保留 T=1"
                    "或阶段迁移位。"
                ),
                "evidence": ["lib/iscsi/iscsi.c::iscsi_op_login_response"],
                "conflict_patterns": [
                    r"(?:认证失败|authentication failure|auth(?:entication)? fail|login error).{0,180}(?:response|响应).{0,180}\bt\s*[:=]\s*1",
                    r"(?:error response|错误响应|失败响应).{0,160}(?:csg|nsg)\s*[:=]\s*[013]",
                ],
                "correction_patterns": [
                    r"(?:认证失败|authentication failure|error response|错误响应|失败响应).{0,220}(?:清除|清零|reserved|clear).{0,80}(?:t|csg|nsg)",
                    r"(?:认证失败|authentication failure|error response|错误响应|失败响应).{0,120}"
                    r"t\s*[:=]\s*1.{0,120}(?:不传播|does not propagate).{0,120}"
                    r"(?:error response|错误响应|失败响应).{0,120}t\s*[:=]\s*0",
                ],
            },
            {
                "id": "iscsi_login_error_c_flag_preserved",
                "assertion": (
                    "错误 Login Response 清除 T、CSG 和 NSG，但不会由该错误分支清除 C；"
                    "不能笼统写成清除 T/C/CSG/NSG。T=1 与 C=1 同时出现本身会被拒绝。"
                ),
                "evidence": ["lib/iscsi/iscsi.c::iscsi_op_login_response"],
                "conflict_patterns": [
                    r"(?:错误|失败|error|fail).{0,100}(?:login\s*)?(?:response|响应).{0,140}(?:清除|清零|clear).{0,100}(?:t\s*[/,、]\s*c(?!sg)|t/c(?!sg)/csg|t、c(?!sg)、csg|t\s+c(?!sg)\s+csg)",
                    r"(?:清除|清零|clear).{0,100}(?:t\s*[/,、]\s*c(?!sg)|t/c(?!sg)/csg|t、c(?!sg)、csg).{0,140}(?:错误|失败|error|fail).{0,80}(?:response|响应)",
                ],
                "correction_patterns": [
                    r"(?:清除|清零|clear).{0,80}(?:t|csg|nsg).{0,120}(?:不清除|保留|preserve|does not clear).{0,20}\bc\b",
                    r"(?:清除|清零|clear).{0,80}(?:t|csg|nsg).{0,120}\bc\b.{0,30}(?:may\s+remain|remains?|可能保留|仍保留)",
                ],
            },
            {
                "id": "iscsi_csg_values",
                "assertion": "Login CSG 取值 0=Security Negotiation、1=Operational Negotiation、3=Full Feature Phase。",
                "evidence": ["lib/iscsi/iscsi.h::ISCSI_SECURITY_NEGOTIATION_PHASE"],
                "conflict_patterns": [
                    r"csg\s*[:=]?\s*1.{0,50}(?:security\s*negotiation|安全协商)",
                    r"csg\s*[:=]?\s*0.{0,50}(?:operational\s*negotiation|操作协商)",
                ],
                "correction_patterns": [
                    r"csg\s*[:=]?\s*1.{0,140}(?:跳过|绕过|skip|bypass).{0,50}(?:security\s*negotiation|安全协商)",
                    r"csg\s*[:=]?\s*0.{0,50}(?:跳过|绕过|skip|bypass).{0,30}(?:operational\s*negotiation|操作协商)",
                    r"csg\s*[:=]?\s*0.{0,100}nsg\s*[:=]?\s*1.{0,100}(?:迁移|进入|transition).{0,50}(?:operational\s*negotiation|操作协商)",
                    r"csg\s*[:=]?\s*0.{0,40}(?:security\s*negotiation|安全协商).{0,120}csg\s*[:=]?\s*1.{0,40}(?:operational\s*negotiation|操作协商)",
                    r"(?:security\s*negotiation|安全协商).{0,40}csg\s*[:=]?\s*0.{0,120}(?:operational\s*negotiation|操作协商).{0,40}csg\s*[:=]?\s*1",
                    r"csg\s*[:=]?\s*0/1/3.{0,60}(?:语义|meaning).{0,120}(?:避免|防止|不得|不能).{0,100}(?:operational\s*negotiation|操作协商)",
                ],
            },
            {
                "id": "iscsi_full_feature_request_rejected",
                "assertion": "收到 CSG=3 Full Feature Phase 的 Login Request 会返回 Initiator Error，不是合法状态迁移。",
                "evidence": ["lib/iscsi/iscsi.c::iscsi_op_login_rsp_handle_csg_bit"],
                "conflict_patterns": [
                    r"(?:发送|send).{0,50}csg\s*[:=]?\s*3.{0,100}(?:进入|transition|成功|合法).{0,60}full feature",
                    r"csg\s*[:=]?\s*3.{0,100}(?:login request).{0,100}(?:进入|transition|成功|合法).{0,60}full feature",
                ],
                "correction_patterns": [
                    r"csg\s*[:=]?\s*3.{0,140}(?:拒绝|initiator error|invalid|不合法|不是合法)",
                ],
            },
            {
                "id": "iscsi_unknown_key_not_understood",
                "assertion": (
                    "未知但格式合法的登录参数通常在协商响应中返回 NotUnderstood；"
                    "不能笼统写成解析失败并断开连接，也不能把超长或格式非法的参数称为未知合法 key。"
                ),
                "evidence": ["lib/iscsi/param.c::iscsi_negotiate_params"],
                "conflict_patterns": [
                    r"(?:未知|unknown).{0,80}(?:key|参数).{0,100}(?:解析失败|parse failure|断开|disconnect|拒绝登录)",
                    r"(?:无效|invalid).{0,60}(?:未知|unknown).{0,120}(?:参数|key).{0,160}(?:返回.{0,40}错误|连接无法建立|login fail)",
                    r"(?:未知|unknown).{0,80}(?:参数|key).{0,160}(?:返回.{0,40}错误|连接无法建立|login fail)",
                    r"(?:无法识别|未识别|unrecognized).{0,60}(?:合法)?(?:参数|key).{0,140}(?:0x0?200|登录失败|login fail|拒绝)",
                    r"(?:无法识别|未识别|unrecognized).{0,80}(?:参数|key).{0,100}(?:重复|duplicate).{0,120}(?:都会|均|all).{0,80}(?:失败|fail|0x0?200)",
                ],
                "correction_patterns": [r"(?:未知|unknown).{0,240}(?:notunderstood|not understood)"],
            },
            {
                "id": "iscsi_login_timer_after_first_pdu",
                "assertion": (
                    "iscsi_pdu_payload_op_login 在首个 Login payload 开始处理时注销 login_timer，"
                    "当前实现未在多阶段登录中重新注册；因此不能声称首 PDU 后停滞必由 30 秒登录定时器清理。"
                ),
                "evidence": ["lib/iscsi/iscsi.c::iscsi_pdu_payload_op_login"],
                "conflict_patterns": [
                    r"(?:首个|first|收到).{0,80}login pdu.{0,120}(?:30\s*秒|30\s*seconds?).{0,100}(?:定时器|timer).{0,100}(?:清理|断开|触发)",
                    r"(?:中途|mid[- ]?login|多阶段|chap).{0,120}(?:停滞|stall|丢包|packet loss).{0,120}(?:30\s*秒|30\s*seconds?).{0,100}(?:清理|断开|timeout)",
                    r"(?:首个|first).{0,80}login pdu.{0,100}(?:停滞|stall).{0,120}(?:login_timer|登录定时器).{0,100}(?:触发|清理|断开|关闭|timeout)",
                    r"(?:login_timer|登录定时器).{0,100}(?:超时|timeout).{0,100}(?:清理|断开|关闭).{0,160}(?:首个|first).{0,80}login pdu",
                ],
                "correction_patterns": [
                    r"(?:注销|unregister|未重新注册|not re[- ]?armed).{0,160}(?:login_timer|登录定时器|timer)",
                    r"(?:待验证|不能声称|不保证|unsupported).{0,120}(?:30\s*秒|timer|定时器)",
                    r"(?:不能确认|无法确认|cannot confirm).{0,100}(?:30\s*秒|login_timer|timer).{0,100}(?:清理|cleanup)",
                ],
            },
            {
                "id": "iscsi_invalid_login_request_detail",
                "assertion": (
                    "SPDK 当前实现对 T 与 C 同时置位、以及保留 NSG 值等非法 Login Request "
                    "返回 Initiator Error class，并把 detail 设置为 ISCSI_LOGIN_INITIATOR_ERROR (0x00)；"
                    "不能把 RFC/规范中的 0x0b 直接写成当前实现的观测结果。"
                ),
                "evidence": [
                    "lib/iscsi/iscsi.c::iscsi_pdu_hdr_op_login",
                    "lib/iscsi/iscsi.c::iscsi_op_login_rsp_handle_csg_bit",
                    "include/spdk/iscsi_spec.h::ISCSI_LOGIN_INITIATOR_ERROR",
                ],
                "conflict_patterns": [
                    r"(?:t\s*\+\s*c|invalid\s+nsg|非法\s*nsg)[^\n]{0,260}`?0x0?b`?",
                    r"(?:t\s*\+\s*c|t\s*=\s*1.{0,30}c\s*=\s*1|invalid\s+nsg|非法\s*nsg|nsg\s*=\s*2).{0,220}(?:status[- _]?detail|detail|状态细节).{0,40}`?0x0?b`?",
                    r"`?0x0?b`?.{0,80}(?:status[- _]?detail|detail|状态细节).{0,220}(?:t\s*\+\s*c|invalid\s+nsg|非法\s*nsg|nsg\s*=\s*2)",
                ],
                "correction_patterns": [
                    r"(?:当前实现|spdk).{0,160}(?:detail|状态细节).{0,40}`?0x0?0`?.{0,120}(?:规范|rfc).{0,80}`?0x0?b`?",
                    r"(?:detail|状态细节).{0,30}`?0x0?0`?.{0,180}(?:若|if).{0,30}(?:detail|状态细节).{0,30}`?0x0?b`?.{0,120}(?:误用|规范|rfc|错误期望)",
                ],
            },
            {
                "id": "iscsi_login_version_offsets",
                "assertion": (
                    "Login Request 的 version_max/version_min 位于 BHS 字节 2 和 3；"
                    "字节 40 起是数据段，不能用 bytes 40-41 修改登录版本。"
                ),
                "evidence": ["include/spdk/iscsi_spec.h::spdk_iscsi_login_req"],
                "conflict_patterns": [
                    r"(?:unsupported version|version(?:_max|_min)?|版本)[^\n]{0,180}(?:bytes?|字节|offset)\s*40\s*[-~～至到]\s*41",
                    r"(?:version_max|version_min|unsupported version|版本).{0,160}(?:bytes?|字节|offset).{0,30}40\s*[-~～至到]\s*41",
                    r"(?:bytes?|字节|offset).{0,30}40\s*[-~～至到]\s*41.{0,160}(?:version_max|version_min|unsupported version|版本)",
                ],
                "correction_patterns": [
                    r"(?:version_max|version_min|版本).{0,100}(?:bytes?|字节|offset).{0,20}2\s*[-~～至到]\s*3",
                    r"(?:byte|字节)\s*3.{0,40}version_min.{0,80}(?:byte|字节)\s*2.{0,40}version_max.{0,180}(?:bytes?|字节)\s*40\s*[-~～至到]\s*41.{0,100}(?:错误|非版本|not version)",
                ],
            },
            {
                "id": "iscsi_acl_precedes_chap_configuration",
                "assertion": (
                    "SPDK 在 iscsi_op_login_check_target 检查 initiator/portal ACL 后，才进入 session "
                    "与 CHAP 配置路径；ACL 拒绝日志是 access denied，不能写成先 CHAP 后 ACL 或 auth failed。"
                ),
                "evidence": [
                    "lib/iscsi/iscsi.c::iscsi_op_login_check_target",
                    "lib/iscsi/iscsi.c::iscsi_pdu_hdr_op_login",
                ],
                "conflict_patterns": [
                    r"(?:chap|认证)[^\n]{0,180}(?:acl|access|授权)[^\n]{0,80}(?:after auth|认证后|之后)",
                    r"(?:acl|access|授权)[^\n]{0,80}(?:after auth|认证后|之后)",
                    r"(?:iscsi_op_login_check_target|acl)[^\n]{0,180}[`\"']auth failed",
                    r"(?:先|first).{0,60}(?:chap|认证).{0,100}(?:再|then|after).{0,80}(?:acl|access|授权)",
                    r"(?:chap|认证).{0,80}(?:之后|after).{0,80}(?:acl|access|授权)",
                    r"(?:acl|access denied|授权).{0,140}(?:日志|log).{0,40}[`\"']?auth failed",
                ],
                "correction_patterns": [
                    r"(?:acl|access).{0,80}(?:先于|before|在前).{0,80}(?:chap|认证)",
                    r"(?:acl|授权).{0,100}(?:拒绝|失败).{0,60}[`\"']?access denied",
                ],
            },
            {
                "id": "iscsi_c_bit_parameter_reassembly",
                "assertion": (
                    "参数解析器会保留 C=1 Login PDU 的不完整数据，并在后续 C=0 PDU 到达后重组；"
                    "只有最终仍缺少必需 key 等真实错误才会失败。"
                ),
                "evidence": [
                    "lib/iscsi/param.c::iscsi_parse_params",
                    "test/unit/lib/iscsi/param.c/param_ut.c",
                ],
                "conflict_patterns": [
                    r"c\s*=\s*1[^\n]{0,180}c\s*=\s*0[^\n]{0,220}(?:fails? to assemble|无法重组|不能重组|missing_parms)",
                    r"(?:完整|合法|complete|valid).{0,100}(?:key/value|参数).{0,120}(?:c\s*=\s*1).{0,180}(?:c\s*=\s*0).{0,160}(?:无法|不能|失败|missing_parms|fail).{0,60}(?:重组|reassembl|登录)?",
                    r"(?:c\s*=\s*1).{0,160}(?:c\s*=\s*0).{0,160}(?:spdk).{0,80}(?:无法|不能|不支持|fails?).{0,80}(?:重组|reassembl)",
                ],
                "correction_patterns": [
                    r"(?:支持|能够|can|will).{0,60}(?:重组|reassembl).{0,120}(?:c\s*=\s*1).{0,120}(?:c\s*=\s*0)",
                ],
            },
            {
                "id": "iscsi_duplicate_key_rejected",
                "assertion": (
                    "同一登录参数列表中的重复 key 会被 iscsi_parse_param 拒绝；"
                    "当前实现不是采用最后一次出现的值继续登录。"
                ),
                "evidence": [
                    "lib/iscsi/param.c::iscsi_parse_param",
                    "test/unit/lib/iscsi/param.c/param_ut.c",
                ],
                "conflict_patterns": [
                    r"(?:重复|duplicate).{0,80}(?:key|参数).{0,140}(?:最后一次|last(?: occurrence| value)?|后者).{0,80}(?:生效|wins?|使用|采用|继续|成功)",
                    r"(?:最后一次|last(?: occurrence| value)?|后者).{0,80}(?:生效|wins?|使用|采用).{0,140}(?:重复|duplicate).{0,60}(?:key|参数)",
                ],
                "correction_patterns": [
                    r"(?:重复|duplicate).{0,80}(?:key|参数).{0,80}(?:拒绝|reject|失败|error)",
                ],
            },
            {
                "id": "iscsi_chap_wire_encoding",
                "assertion": (
                    "CHAP_N 是普通用户名字符串，CHAP_I 是十进制标识符；"
                    "只有 CHAP_R/CHAP_C 支持带 0x/0b 前缀的编码，不能把 CHAP_N、CHAP_I、CHAP_R 全写成 base64。"
                ),
                "evidence": ["lib/iscsi/iscsi.c::iscsi_auth_params"],
                "conflict_patterns": [
                    r"chap_n[^\n]{0,180}(?:base64 encoded|base64\s*编码)",
                    r"chap_n.{0,40}chap_i.{0,40}chap_r.{0,80}(?:都|均|all|must).{0,40}base64",
                    r"(?:都|均|all|must).{0,40}(?:使用|编码|be).{0,30}base64.{0,100}chap_(?:n|i)",
                    r"chap_(?:n|i).{0,80}(?:必须|must|应).{0,30}(?:base64|hex|0x|0b)",
                ],
                "correction_patterns": [
                    r"chap_n.{0,60}(?:普通|plain).{0,40}(?:字符串|username).{0,120}chap_i.{0,60}(?:十进制|decimal)",
                ],
            },
            {
                "id": "iscsi_chap_response_validation",
                "assertion": (
                    "解码后的 CHAP_R 必须恰好为 16 字节；ISCSI_CHAP_MAX_SECRET_LEN 限制配置 secret，"
                    "不是 wire response 长度。格式错误日志是 response format error。"
                ),
                "evidence": ["lib/iscsi/iscsi.c::iscsi_auth_params"],
                "conflict_patterns": [
                    r"chap_r[^\n]{0,220}base64 decode failed",
                    r"base64 decode failed[^\n]{0,220}chap_r",
                    r"iscsi_chap_max_secret_len[^\n]{0,180}chap_r",
                    r"chap_r[^\n]{0,180}iscsi_chap_max_secret_len",
                    r"chap_r.{0,120}(?:长度|length|size).{0,100}iscsi_chap_max_secret_len",
                    r"iscsi_chap_max_secret_len.{0,100}(?:限制|limit).{0,100}chap_r",
                    r"(?:非法|malformed|invalid).{0,80}chap_r.{0,120}(?:日志|log|记录).{0,40}[`\"']?base64 decode failed",
                ],
                "correction_patterns": [
                    r"chap_r.{0,120}(?:恰好|exactly|必须).{0,20}16\s*(?:字节|bytes?)",
                    r"(?:response format error).{0,80}(?:chap_r|响应)",
                ],
            },
            {
                "id": "iscsi_login_response_opcode",
                "assertion": "Login Request opcode 是 0x03，Login Response opcode 是 0x23；抓包过滤响应必须使用 0x23。",
                "evidence": ["include/spdk/iscsi_spec.h::ISCSI_OP_LOGIN_RSP"],
                "conflict_patterns": [
                    r"iscsi\.opcode\s*==\s*0x0?3(?![0-9a-f])[^\n]{0,200}iscsi\.login_status",
                    r"(?:login response|登录响应|抓取.{0,30}响应).{0,160}iscsi\.opcode\s*==\s*0x0?3(?![0-9a-f])",
                    r"iscsi\.opcode\s*==\s*0x0?3(?![0-9a-f]).{0,160}(?:login response|登录响应)",
                ],
                "correction_patterns": [
                    r"(?:login response|登录响应).{0,100}(?:0x23|iscsi\.opcode\s*==\s*0x23)",
                ],
            },
            {
                "id": "iscsi_rpc_login_phase_values",
                "assertion": (
                    "iscsi_get_connections 的 login_phase 枚举字符串包含 _phase 后缀："
                    "security_negotiation_phase、operational_negotiation_phase、full_feature_phase。"
                ),
                "evidence": ["lib/iscsi/conn.c::iscsi_conn_info_json"],
                "conflict_patterns": [
                    # This contract concerns the public RPC value, not the
                    # internal conn->login_phase enum used by the state
                    # machine.  Keeping the RPC anchor avoids rejecting a
                    # source-accurate flow map that names internal constants.
                    r"(?:iscsi_get_connections|connections?\s*\[\s*\]).{0,180}login_phase.{0,120}(?:security_negotiation(?!_phase)|operational_negotiation(?!_phase))(?:[\s`\"',/]|$)",
                ],
                "correction_patterns": [
                    r"login_phase.{0,160}(?:security_negotiation_phase|operational_negotiation_phase)",
                ],
            },
            {
                "id": "iscsi_fuzzer_skips_login_opcode",
                "assertion": (
                    "test/app/fuzz/iscsi_fuzz/iscsi_fuzz.c 当前明确跳过 LOGIN opcode；"
                    "不能把该 fuzzer 映射为随机 Login Request 覆盖。"
                ),
                "evidence": ["test/app/fuzz/iscsi_fuzz/iscsi_fuzz.c"],
                "conflict_patterns": [
                    r"(?:iscsi_fuzz(?:\.c)?|现有\s*fuzz)[^\n]{0,180}(?:可能|may|might)[^\n]{0,40}(?:覆盖|触发|cover|trigger)",
                    r"iscsi_fuzz(?:\.c)?[^\n]{0,220}(?:may|might|可能|可)[^\n]{0,80}(?:trigger|cover|mutat|触发|覆盖)[^\n]{0,80}(?:login|登录)",
                    r"iscsi_fuzz(?:\.c)?.{0,180}(?:覆盖|cover|随机|mutat).{0,80}(?:login opcode|login request|登录)",
                    r"(?:login opcode|login request|登录).{0,100}(?:随机|mutat|覆盖|cover).{0,180}iscsi_fuzz(?:\.c)?",
                ],
                "correction_patterns": [
                    r"iscsi_fuzz(?:\.c)?.{0,160}(?:跳过|忽略|不处理|skip|ignore).{0,60}(?:login|登录)",
                    r"iscsi_fuzz(?:\.c)?[^\n]{0,140}(?:不针对|无此断言|不能证明|待补证据)",
                ],
            },
            {
                "id": "iscsi_first_payload_still_gets_response",
                "assertion": (
                    "注销 login_timer 不会阻止当前 Login PDU 的响应路径；首个 payload 仍会进入 "
                    "iscsi_op_login_response 并发送 Login Response。"
                ),
                "evidence": [
                    "lib/iscsi/iscsi.c::iscsi_pdu_payload_op_login",
                    "lib/iscsi/iscsi.c::iscsi_op_login_response",
                ],
                "conflict_patterns": [
                    r"(?:timer|定时器)[^\n]{0,100}(?:注销|unregister)[^\n]{0,160}(?:无|no|不会)[^\n]{0,60}(?:login )?response",
                    r"(?:首个|first).{0,80}(?:login )?(?:pdu|payload).{0,160}(?:timer|定时器).{0,100}(?:注销|unregister).{0,160}(?:不|不会|no).{0,40}(?:发送|send).{0,50}(?:login )?response",
                    r"(?:timer|定时器).{0,100}(?:注销|unregister).{0,180}(?:首个|first).{0,80}(?:pdu|payload).{0,120}(?:无响应|no response|不会发送)",
                ],
                "correction_patterns": [
                    r"(?:timer|定时器).{0,100}(?:注销|unregister).{0,180}(?:仍|still).{0,80}(?:发送|send).{0,60}(?:login )?response",
                ],
            },
            {
                "id": "iscsi_discovery_target_address",
                "assertion": (
                    "iscsi_op_login_set_target_info 在 target != NULL 且 session_type 为 "
                    "SESSION_TYPE_DISCOVERY 时向 Login Response 追加 TargetAddress；"
                    "不能声称 Discovery Login 不会追加 TargetAddress，也不能把该行为归因于不存在的 "
                    "iscsi_op_login_set_params。"
                ),
                "evidence": ["lib/iscsi/iscsi.c::iscsi_op_login_set_target_info"],
                "conflict_patterns": [
                    r"discovery.{0,220}(?:不返回|不会返回|不会收到|不追加|不包含|不应包含|不该包含|does not (?:return|append|include)|does not receive|should not include).{0,30}targetaddress",
                    r"discovery login.{0,100}(?:不强制|不要求|非必需|not require).{0,40}targetaddress",
                ],
                "correction_patterns": [
                    r"discovery.{0,120}(?:返回|包含|append|include|return).{0,40}targetaddress",
                    r"session_type.{0,100}(?:session_type_discovery|discovery).{0,140}targetaddress",
                ],
            },
            {
                "id": "iscsi_discovery_target_info_symbol",
                "assertion": (
                    "Discovery Login 的目标信息函数是 iscsi_op_login_set_target_info；"
                    "iscsi_op_login_set_params 不是当前 SPDK 源码中的符号。"
                ),
                "evidence": ["lib/iscsi/iscsi.c::iscsi_op_login_set_target_info"],
                "conflict_patterns": [r"\biscsi_op_login_set_params\b"],
                "correction_patterns": [
                    r"iscsi_op_login_set_params.{0,100}(?:不存在|错误|并非|不是).{0,100}iscsi_op_login_set_target_info",
                ],
            },
            {
                "id": "iscsi_reject_protocol_error_reason",
                "assertion": (
                    "Reject PDU 的 Protocol Error reason code 是 0x04；0x05 是 Command Not Supported。"
                ),
                "evidence": ["include/spdk/iscsi_spec.h::ISCSI_REASON_PROTOCOL_ERROR"],
                "conflict_patterns": [
                    r"(?:reject.{0,60})?protocol error.{0,80}\(?`?0x05`?\)?",
                    r"`?0x05`?.{0,80}(?:reject.{0,60})?protocol error",
                ],
                "correction_patterns": [
                    r"protocol error.{0,80}`?0x04`?.{0,100}(?:不是|而非|not).{0,40}`?0x05`?",
                    r"protocol error.{0,80}`?0x04`?.{0,160}`?0x05`?.{0,100}(?:错误|command not supported|断言)",
                    r"(?:reject(?:\s+reason)?\s*[:=]?\s*)?`?0x04`?.{0,50}protocol error.{0,100}`?0x05`?.{0,50}command not supported",
                    r"(?:若|if).{0,30}protocol error.{0,40}`?0x05`?.{0,80}(?:错误|wrong|断言)",
                    r"(?:reject\s*)?protocol error.{0,80}`?0x04`?",
                ],
            },
            {
                "id": "iscsi_rpc_connection_state",
                "assertion": (
                    "iscsi_get_connections 的公开字段是 state=running 和 "
                    "login_phase=full_feature_phase；不能声称 RPC 直接返回状态 Full Feature Phase。"
                ),
                "evidence": ["lib/iscsi/conn.c::iscsi_conn_info_json"],
                "conflict_patterns": [
                    r"iscsi_get_connections.{0,160}(?:显示|返回|reports?|shows?).{0,100}(?:状态|state).{0,40}(?:为|=|is)?\s*[\"'`]?full feature phase",
                ],
                "correction_patterns": [
                    r"iscsi_get_connections.{0,220}state\s*[:=]\s*[\"'`]?running.{0,120}login_phase\s*[:=]\s*[\"'`]?full_feature_phase",
                ],
            },
            {
                "id": "iscsi_internal_observer_boundary",
                "assertion": (
                    "iscsi_get_active_conns 是 target 内部 C 函数，不是黑盒测试者可直接调用的公开观测接口；"
                    "黑盒观测应使用进程状态、RPC、日志、initiator 结果或公开指标。"
                ),
                "evidence": ["lib/iscsi/conn.c::iscsi_get_active_conns"],
                "conflict_patterns": [
                    r"(?:黑盒|观测点|observer|observe).{0,100}(?:调用|call|invoke).{0,30}iscsi_get_active_conns",
                    r"iscsi_get_active_conns.{0,100}(?:外部|公开|rpc|黑盒).{0,80}(?:调用|接口|观测)",
                    r"(?:通过|using).{0,80}iscsi_get_active_conns.{0,80}(?:观测|observe|获取|query)",
                ],
                "correction_patterns": [
                    r"iscsi_get_active_conns.{0,140}(?:非公开|内部(?:\s*c\s*)?函数|internal).{0,140}(?:不应(?:直接)?调用|不直接调用|不能直接调用|not call|not invoke)",
                    r"(?:不调用|不应调用|不能调用|not call|not invoke).{0,80}iscsi_get_active_conns.{0,140}(?:内部(?:\s*c\s*)?函数|internal)",
                    r"(?:不调用|不应调用|不能调用|not call|not invoke).{0,80}iscsi_get_active_conns",
                    r"(?:不要调用|不得调用|do not call).{0,80}iscsi_get_active_conns",
                ],
            },
            {
                "id": "iscsi_login_response_stage_bits",
                "assertion": (
                    "成功 Login Response 的 CSG 回显当前阶段，NSG 表示下一阶段；"
                    "从 Security 或 Operational 迁移到 Full Feature 时可分别为 CSG=0/1、NSG=3、T=1，"
                    "而不是 CSG=3。"
                ),
                "evidence": ["lib/iscsi/iscsi.c::iscsi_op_login_rsp_init"],
                "conflict_patterns": [
                    r"(?:login response|登录响应|最终响应).{0,100}csg\s*[:=]?\s*3.{0,80}nsg\s*[:=]?\s*3",
                    r"csg\s*[:=]?\s*3.{0,80}nsg\s*[:=]?\s*3.{0,100}(?:login response|登录响应|最终响应)",
                ],
                "correction_patterns": [
                    r"(?:login response|登录响应|最终响应).{0,100}csg\s*[:=]?\s*1.{0,80}nsg\s*[:=]?\s*3",
                ],
            },
            {
                "id": "iscsi_final_login_stage_alternatives",
                "assertion": (
                    "最终成功 Login Response 必须为 T=1、NSG=3，但 CSG 回显当前阶段；"
                    "认证与参数协商路径允许 CSG=0 或 CSG=1，不能把 CSG=1 写成唯一合法终态。"
                ),
                "evidence": [
                    "lib/iscsi/iscsi.c::iscsi_op_login_rsp_handle_csg_bit",
                    "lib/iscsi/iscsi.c::iscsi_op_login_response",
                ],
                "conflict_patterns": [
                    r"(?:最终|final).{0,120}(?:必须|只能|固定|应当|应该|must|only|should|expected).{0,100}[\"'`]?csg[\"'`]?\s*[:=]\s*1",
                    r"(?:必须|只能|固定|应当|应该|must|only|should|expected).{0,100}(?:最终|final).{0,120}[\"'`]?csg[\"'`]?\s*[:=]\s*1",
                    r"(?:最终|final).{0,120}[\"'`]?csg[\"'`]?\s*[:=]\s*1",
                    r"(?:最终|final).{0,120}csg\s*[:=]\s*1.{0,100}(?:唯一|only)",
                ],
                "correction_patterns": [
                    r"csg\s*[:=]\s*0.{0,160}csg\s*[:=]\s*1.{0,100}(?:均|都|允许|合法|取决于|depending|either)",
                    r"csg.{0,100}csg\s*[:=]\s*0.{0,100}(?:或|and|/).{0,100}csg\s*[:=]\s*1",
                    r"(?:允许|合法|either).{0,100}csg\s*[:=]\s*[01].{0,160}csg\s*[:=]\s*[01]",
                    r"(?:不能|不得|不可|not).{0,60}(?:把|treat)?\s*[`\"']?csg[`\"']?\s*[:=]\s*1.{0,100}(?:唯一|only|固定)",
                    r"(?:最终|final).{0,80}(?:不能固定|不得固定|not fixed).{0,40}[`\"']?csg\s*[:=]\s*1[`\"']?",
                    r"(?:两阶段|two[- ]stage).{0,220}(?:最终|final).{0,80}csg\s*[:=]\s*1.{0,220}(?:单阶段|single[- ]stage).{0,220}(?:最终|final).{0,80}csg\s*[:=]\s*0",
                ],
            },
            {
                "id": "iscsi_multiconnection_mapping_scope",
                "assertion": (
                    "test/iscsi_tgt/multiconnection/multiconnection.sh 创建多个 Target 并执行批量登录，"
                    "不能直接证明同一 Target 的 100 Initiator、同一 Initiator 多 CID 或通用登录并发结论。"
                ),
                "evidence": ["test/iscsi_tgt/multiconnection/multiconnection.sh"],
                "conflict_patterns": [
                    r"(?:100\s*(?:个)?\s*initiator|多\s*initiator|multiple initiators?|同一\s*target|same target|同一\s*initiator|多\s*cid|multiple cid).{0,260}multiconnection\.sh",
                    r"multiconnection\.sh.{0,260}(?:100\s*(?:个)?\s*initiator|多\s*initiator|multiple initiators?|同一\s*target|same target|同一\s*initiator|多\s*cid|multiple cid)",
                ],
                "correction_patterns": [
                    r"multiconnection\.sh.{0,320}(?:不覆盖|不证明|不能证明|不作为|不能作为|不能(?:直接)?映射|不得解释|仅(?:供|作)?参考|需要新增|not cover|does not prove)",
                    r"multiconnection\.sh.{0,100}(?:不|未|没有|不得|不能).{0,30}(?:映射|证明|代表)",
                    r"multiconnection\.sh.{0,220}仅作.{0,100}参考.{0,120}(?:不证明|不能证明|不得解释)",
                    r"(?:若|如果|if).{0,30}(?:误映射|错误映射|wrongly map).{0,40}multiconnection\.sh.{0,100}(?:同一\s*target|多\s*cid|100\s*initiator).{0,60}(?:修正|纠正|correct)",
                    r"(?:不得|不能|不可|not).{0,60}(?:把|treat)?\s*multiconnection\.sh.{0,100}(?:解释|映射|证明).{0,100}(?:同一\s*target|多\s*cid|100\s*initiator)",
                    r"(?:不得|不能|不可|not).{0,40}(?:解释|映射|证明).{0,120}(?:同一\s*target|多\s*cid|100\s*initiator).{0,260}multiconnection\.sh",
                ],
            },
            {
                "id": "iscsi_duplicate_cid_not_too_many_connections",
                "assertion": (
                    "SPDK append_iscsi_sess 以 MaxConnections 容量决定 Too Many Connections (0x06)，"
                    "不能把重复 CID 本身写成必然触发 0x06 的条件。"
                ),
                "evidence": ["lib/iscsi/iscsi.c::append_iscsi_sess", "lib/iscsi/iscsi.h::DEFAULT_MAX_CONNECTIONS_PER_SESSION"],
                "conflict_patterns": [
                    r"(?:cid.{0,40}(?:冲突|重复|相同|duplicate|same)).{0,180}(?:too many connections|0x0?6|status[-_ ]detail.{0,20}(?:6|0x0?6))",
                    r"(?:too many connections|0x0?6).{0,180}(?:cid.{0,40}(?:冲突|重复|相同|duplicate|same))",
                ],
                "correction_patterns": [
                    r"(?:cid.{0,40}(?:冲突|重复|相同|duplicate|same)).{0,100}(?:不能|不得|不应|不会|not).{0,80}(?:too many connections|0x0?6)",
                    r"(?:too many connections|0x0?6).{0,120}(?:maxconnections|容量|连接上限|connection limit)",
                    r"(?:maxconnections|iscsi_set_options\s+-c\s+1|连接上限).{0,500}(?:不同|新|different|new).{0,30}cid.{0,220}(?:too many connections|0x0?6|detail\s*=\s*0x0?6)",
                ],
            },
            {
                "id": "iscsi_tsih_reinstatement_scope",
                "assertion": (
                    "同 ISID 的 session reinstatement 使用 TSIH=0 重新创建 session；"
                    "携带已有非零 TSIH 与新 CID 是向现有 Normal session 追加连接，不是 reinstatement。"
                ),
                "evidence": ["lib/iscsi/iscsi.c::iscsi_op_login_session_normal"],
                "conflict_patterns": [
                    r"(?:tsih.{0,20})?(?:session reinstatement|会话恢复|会话重建).{0,180}(?:复用|使用|携带|reuse).{0,60}(?:非零|已有|返回).{0,30}tsih",
                    r"case_tsih_reinstatement[\s\S]{0,900}tsih\s*=\s*(?:first|rsp|response).{0,500}cid\s*=.{0,40}(?:\+\s*1|new|different)",
                ],
                "correction_patterns": [
                    r"(?:session reinstatement|会话恢复|会话重建).{0,120}(?:tsih\s*[:=]?\s*0).{0,120}(?:同一|same).{0,30}isid",
                    r"(?:session reinstatement|会话恢复|会话重建).{0,120}(?:同一|same).{0,60}isid.{0,80}tsih\s*[:=]?\s*0",
                    r"(?:非零|已有|返回).{0,30}tsih.{0,120}(?:追加连接|append|mcs|不是.{0,30}(?:reinstatement|会话恢复|会话重建))",
                ],
            },
            {
                "id": "iscsi_cmdsn_expstatsn_rejection_unverified",
                "assertion": (
                    "当前 Login 路径读取 CmdSN/ExpStatSN，但没有证据表明任意异常值都返回 Initiator Error；"
                    "未验证场景必须记录实际响应，不能预设确定拒绝。"
                ),
                "evidence": ["lib/iscsi/iscsi.c::iscsi_op_login_rsp_init"],
                "conflict_patterns": [
                    r"cmdsn.{0,50}expstatsn.{0,180}(?:必须|应|expected|expect).{0,60}(?:initiator error|确定性拒绝|reject)",
                    r"(?:initiator error|确定性拒绝).{0,180}cmdsn.{0,50}expstatsn",
                    r"case_bad_cmdsn_expstatsn[\s\S]{0,500}expect_login_error",
                ],
                "correction_patterns": [
                    r"cmdsn.{0,50}expstatsn.{0,180}(?:没有|无|不能|不得|不预设|未验证|not).{0,100}(?:拒绝|initiator error|reject)",
                    r"cmdsn.{0,50}expstatsn.{0,220}(?:无|没有|缺少).{0,40}(?:源码|规范|证据).{0,100}(?:不作为|不能作为|不得作为).{0,40}(?:发布|通过|判定)",
                    r"(?:记录|采集|observe).{0,80}(?:实际)?响应.{0,120}(?:不预设|待验证|unverified)",
                ],
            },
            {
                "id": "iscsi_unknown_user_test_mapping_scope",
                "assertion": (
                    "chap_discovery.sh 验证缺少 initiator 凭据后再配置正确凭据，"
                    "不发送未知 CHAP_N，不能作为 unknown-user 的现有通过映射。"
                ),
                "evidence": ["test/iscsi_tgt/chap/chap_discovery.sh"],
                "conflict_patterns": [
                    r"(?:未知|不存在|unknown).{0,50}(?:chap.{0,20})?(?:用户|user|chap_n).{0,220}chap_discovery\.sh",
                    r"chap_discovery\.sh.{0,220}(?:未知|不存在|unknown).{0,50}(?:chap.{0,20})?(?:用户|user|chap_n)",
                ],
                "correction_patterns": [
                    r"(?:不要|不得|不能|不可).{0,20}(?:把|将).{0,20}chap_discovery\.sh.{0,30}(?:当成|作为|视为).{0,50}(?:未知|unknown|chap_n)",
                    r"chap_discovery\.sh.{0,160}(?:不覆盖|不发送|不能证明|不作为|not cover).{0,80}(?:未知|unknown|chap_n)",
                    r"chap_discovery\.sh.{0,120}(?:不|未|不得|不能).{0,30}(?:映射|发送|发|覆盖).{0,80}(?:未知|unknown|chap_n)",
                    r"(?:未知|unknown).{0,80}(?:ai_suggested_unverified|需要新增|待新增|正文.*harness).{0,200}chap_discovery\.sh.{0,80}(?:不覆盖|不作为)",
                    r"(?:未知|unknown|unknown-user).{0,100}(?:仅|只).{0,40}(?:映射|使用).{0,60}harness.{0,100}(?:不|未|不得|不能).{0,30}映射.{0,60}chap_discovery\.sh",
                    r"(?:未知|unknown).{0,120}(?:不得|不能|不再|不).{0,30}映射.{0,80}chap_discovery\.sh",
                    r"(?:未知|unknown).{0,100}(?:用户|user|chap_n).{0,160}chap_discovery\.sh.{0,100}(?:未覆盖|不覆盖|需新增|需要新增|not cover)",
                    r"ai_suggested_unverified.{0,100}(?:unknown|chap-user).{0,220}chap_discovery\.sh",
                ],
            },
            {
                "id": "iscsi_rpc_config_mapping_scope",
                "assertion": (
                    "rpc_config.py 检查公开配置、连接信息和 logout 后连接清空，"
                    "不直接断言 Login wire 的 T/NSG/CSG 或 state/login_phase。"
                ),
                "evidence": ["test/iscsi_tgt/rpc_config/rpc_config.py"],
                "conflict_patterns": [
                    r"rpc_config\.py.{0,260}(?:state\s*=\s*running|login_phase|full_feature_phase|t\s*=\s*1|nsg\s*=\s*3)",
                    r"(?:state\s*=\s*running|login_phase|full_feature_phase|t\s*=\s*1|nsg\s*=\s*3).{0,260}rpc_config\.py",
                ],
                "correction_patterns": [
                    r"rpc_config\.py.{0,160}(?:不|未|没有|不能|not).{0,80}(?:断言|验证|覆盖).{0,100}(?:state|login_phase|full_feature|t/?.{0,10}nsg)",
                    r"rpc_config\.py.{0,160}(?:只|仅).{0,40}映射.{0,120}(?:不作为|不能作为|不代表).{0,120}(?:wire|state|login_phase|full_feature|t/?.{0,10}nsg)",
                    r"(?:state|login_phase|full_feature|t/?.{0,10}nsg).{0,100}(?:需要|新增|独立).{0,100}(?:pcap|rpc|断言).{0,200}rpc_config\.py.{0,80}(?:不|未)",
                    r"(?:不把|不将|不得把|不能把).{0,30}rpc_config\.py.{0,80}(?:当作|作为).{0,80}(?:断言|证据|映射)",
                    r"rpc_config\.py.{0,100}(?:部分|仅|只).{0,60}(?:覆盖|映射).{0,80}(?:连接字段|公开连接字段|连接信息)",
                ],
            },
            {
                "id": "iscsi_reset_io_recovery_mapping_scope",
                "assertion": (
                    "reset.sh 在 fio 运行期间执行 sg_reset 并检查进程存活，随后允许 fio 非零退出；"
                    "它不能单独证明 I/O 成功恢复或完成。"
                ),
                "evidence": ["test/iscsi_tgt/reset/reset.sh"],
                "conflict_patterns": [
                    r"reset(?:/reset)?\.sh.{0,220}(?:i/o|io|fio).{0,80}(?:可恢复|成功恢复|恢复成功|完成|recovered|recovery succeeds)",
                    r"(?:i/o|io|fio).{0,80}(?:可恢复|成功恢复|恢复成功|完成).{0,220}reset(?:/reset)?\.sh",
                ],
                "correction_patterns": [
                    r"reset(?:/reset)?\.sh.{0,180}(?:只|仅).{0,80}(?:进程存活|process.{0,20}alive).{0,120}(?:不证明|不能证明|不保证).{0,80}(?:i/o|io|fio).{0,50}(?:恢复|成功|完成)",
                    r"reset(?:/reset)?\.sh.{0,180}(?:不证明|不能证明|不保证|does not prove).{0,100}(?:i/o|io|fio).{0,50}(?:恢复|成功|完成)",
                    r"(?:i/o|io|fio).{0,80}(?:恢复|成功|完成).{0,80}(?:当前|尚|仍).{0,40}(?:不由|不能由|未由).{0,50}reset(?:/reset)?\.sh.{0,40}(?:证明|覆盖)",
                    r"reset(?:/reset)?\.sh.{0,100}(?:只|仅).{0,30}映射.{0,50}(?:进程存活|process).{0,120}(?:i/o|io|fio).{0,50}(?:另列|待新增|需要新增)",
                ],
            },
            {
                "id": "iscsi_redirection_mapping_scope",
                "assertion": (
                    "login_redirection.sh 覆盖 RPC 重定向与 logout，不是网络中断后自动重连测试；"
                    "网络故障重连必须新增或映射到真实网络故障场景。"
                ),
                "evidence": ["test/iscsi_tgt/login_redirection/login_redirection.sh"],
                "conflict_patterns": [
                    r"(?:网络故障|网络中断|关闭端口|断网|network (?:fault|outage)|自动重连).{0,260}login_redirection\.sh",
                    r"login_redirection\.sh.{0,260}(?:网络故障|网络中断|关闭端口|断网|network (?:fault|outage)|自动重连)",
                ],
                "correction_patterns": [
                    r"redirect.{0,80}(?:被|遭)?(?:误当|误判|错误地?认为|错误地?视为).{0,80}(?:网络故障|网络中断|自动重连|自动恢复).{0,240}login_redirection\.sh.{0,100}(?:仅|只).{0,40}(?:验证|覆盖).{0,80}(?:受控\s*rpc|redirect)",
                    r"login_redirection\.sh.{0,180}(?:不覆盖|不能证明|不映射|不作为|仅供参考|需要新增|不是(?:网络故障|自动重连)|not cover|does not prove|not a network)",
                    r"(?:不可用|不用|不使用|不得使用|不能使用|do not use).{0,80}login_redirection\.sh.{0,180}(?:只测|仅测|不测|非|不是|not).{0,80}(?:网络中断|网络故障|自动重连|network fault|network outage)",
                    r"login_redirection\.sh.{0,500}(?:不得解释|不能解释|不可解释|不应解释).{0,80}(?:网络故障|自动重连)",
                    r"redirect.{0,100}(?:受控\s*rpc|controlled rpc).{0,80}(?:不是|不覆盖|not).{0,50}(?:网络故障|自动重连|network fault|automatic reconnect)",
                    r"login_redirection\.sh.{0,160}(?:不代表|不能代表|不等于).{0,80}(?:网络故障|自动重连)",
                    r"login_redirection\.sh.{0,120}(?:仅|只).{0,40}(?:映射|覆盖).{0,60}(?:受控)?\s*redirect.{0,120}(?:网络故障|自动重连).{0,50}(?:另建|待新增|需要新增)",
                    r"(?:不得|不能|不可|不是|不应|do not).{0,50}(?:解释|视为|映射|覆盖|treat).{0,100}(?:网络故障|自动重连|network fault|automatic reconnect).{0,240}login_redirection\.sh",
                ],
            },
            {
                "id": "iscsi_calsoft_mapping_scope",
                "assertion": (
                    "calsoft.py 是协议一致性套件入口，不测 Login latency；登录延迟基线需要独立计时工具和环境基线。"
                ),
                "evidence": ["test/iscsi_tgt/calsoft/calsoft.py"],
                "conflict_patterns": [
                    r"calsoft\.py.{0,180}(?:login|登录).{0,80}(?:latency|延迟|p99|性能)",
                    r"(?:login|登录).{0,80}(?:latency|延迟|p99|性能).{0,180}calsoft\.py",
                ],
                "correction_patterns": [
                    r"(?:不要|不得|不能|不可).{0,20}(?:使用|用).{0,40}calsoft\.py.{0,100}(?:推导|推出|证明|作为).{0,60}(?:login|登录).{0,40}(?:latency|延迟)",
                    r"calsoft\.py.{0,180}(?:不测|不覆盖|不作为|不是|仅.*一致性|does not measure|not a benchmark)",
                    r"(?:不能|不得|不可|not).{0,60}(?:从|use)?\s*`?calsoft\.py`?.{0,140}(?:推出|作为|derive|benchmark)",
                    r"calsoft\.py.{0,160}(?:不采集|不产生|does not collect).{0,80}(?:login|登录).{0,80}(?:延迟|latency|分位数)",
                    r"calsoft\.py.{0,100}(?:是|仅为|only).{0,80}(?:一致性|conformance|compatibility).{0,80}(?:入口|测试|suite)",
                    r"calsoft\.py.{0,180}(?:均)?(?:不得|不能|不可|not).{0,40}(?:作为|用作|be used).{0,80}(?:login|登录).{0,60}(?:latency|延迟|test_mapping)",
                    r"(?:不用|不使用|不得使用|不能使用|do not use).{0,220}calsoft\.py.{0,100}(?:作为|用作|性能映射|latency|benchmark)?",
                    r"(?:login|登录).{0,80}(?:latency|延迟|p50|p95|p99).{0,80}(?:不用|不使用|不得使用|不能使用|do not use).{0,220}calsoft\.py",
                    r"(?:不得映射|不能映射|不映射|do not map).{0,240}calsoft\.py",
                    r"test_mapping.{0,80}(?:不再使用|不得使用|不能使用|does not use).{0,240}calsoft\.py",
                    r"calsoft\.py.{0,80}(?:conformance only|一致性(?:套件)?(?:入口)?).{0,120}(?:ai_suggested_unverified|login-latency|latency harness)?",
                    r"ai_suggested_unverified.{0,100}(?:login-latency|latency|p50|p95|p99).{0,260}calsoft\.py",
                    r"calsoft\.py.{0,260}ai_suggested_unverified.{0,100}(?:login-latency|latency|p50|p95|p99)",
                ],
            },
            {
                "id": "iscsi_perf_scripts_not_login_latency",
                "assertion": (
                    "test/iscsi_tgt/perf/iscsi_target.sh 与 iscsi_initiator.sh 运行 fio I/O，"
                    "不采集 Login p50/p95/p99；登录延迟必须用独立计时与抓包方案，并明确安全设备。"
                ),
                "evidence": [
                    "test/iscsi_tgt/perf/iscsi_target.sh",
                    "test/iscsi_tgt/perf/iscsi_initiator.sh",
                ],
                "conflict_patterns": [
                    r"iscsi_(?:target|initiator)\.sh.{0,220}(?:login|登录).{0,100}(?:p50|p95|p99|latency|延迟)",
                    r"(?:login|登录).{0,100}(?:p50|p95|p99|latency|延迟).{0,220}iscsi_(?:target|initiator)\.sh",
                ],
                "correction_patterns": [
                    r"iscsi_(?:target|initiator)\.sh.{0,180}(?:不采集|不测|不能测|fio\s*i/o|does not measure|not a login|not login latency)",
                    r"iscsi_(?:target|initiator)\.sh.{0,80}(?:仅|只)\s*(?:fio\s*)?i/o.{0,80}(?:不覆盖|不采集|不测|不能测).{0,60}(?:login|登录).{0,60}(?:latency|延迟|p50|p95|p99)",
                    r"iscsi_(?:target|initiator)\.sh.{0,260}(?:均)?(?:不得|不能|不可|not).{0,40}(?:作为|用作|be used).{0,80}(?:login|登录).{0,60}(?:latency|延迟|test_mapping)",
                    r"(?:不用|不使用|不得使用|不能使用|do not use).{0,220}iscsi_(?:target|initiator)\.sh.{0,100}(?:作为|用作|性能映射|latency|benchmark)?",
                    r"(?:login|登录).{0,80}(?:latency|延迟|p50|p95|p99).{0,80}(?:不用|不使用|不得使用|不能使用|do not use).{0,220}iscsi_(?:target|initiator)\.sh",
                    r"(?:不得映射|不能映射|不映射|do not map).{0,240}iscsi_(?:target|initiator)\.sh",
                    r"test_mapping.{0,80}(?:不再使用|不得使用|不能使用|does not use).{0,240}iscsi_(?:target|initiator)\.sh",
                    r"iscsi_(?:target|initiator)\.sh.{0,100}(?:fio\s*i/o\s*only|仅.*fio|fio\s*i/o).{0,160}(?:ai_suggested_unverified|login-latency|latency harness)?",
                    r"ai_suggested_unverified.{0,100}(?:login-latency|latency|p50|p95|p99).{0,260}iscsi_(?:target|initiator)\.sh",
                    r"iscsi_(?:target|initiator)\.sh.{0,320}ai_suggested_unverified.{0,100}(?:login-latency|latency|p50|p95|p99)",
                ],
            },
            {
                "id": "iscsi_reset_mapping_scope",
                "assertion": (
                    "test/iscsi_tgt/reset/reset.sh 在持续 fio 中执行 sg_reset，不覆盖 logout/relogin；"
                    "会话重建必须使用独立可控用例。"
                ),
                "evidence": ["test/iscsi_tgt/reset/reset.sh"],
                "conflict_patterns": [
                    r"reset/reset\.sh.{0,220}(?:logout|relogin|重新登录|会话重建)",
                    r"(?:logout|relogin|重新登录|会话重建).{0,220}reset/reset\.sh",
                ],
                "correction_patterns": [
                    r"reset/reset\.sh.{0,180}(?:不覆盖|不能证明|仅.*sg_reset|持续\s*fio|does not cover)",
                    r"reset/reset\.sh.{0,100}(?:不再|不得|不能|不可).{0,40}(?:映射|解释|声称).{0,60}(?:logout|relogin)",
                    r"reset/reset\.sh.{0,160}(?:持续.{0,30}fio|sg_reset).{0,120}(?:不是|不覆盖|不实现).{0,80}(?:logout|relogin)",
                    r"reset(?:/reset\.sh|\.sh).{0,80}(?:不被宣称|不得声称|不能声称).{0,60}(?:覆盖|实现).{0,40}(?:logout|relogin)",
                    r"reset/reset\.sh.{0,160}ai_suggested_unverified.{0,80}(?:add|新增).{0,40}(?:logout|relogin)",
                    r"(?:不宣称|不声称|不覆盖|不得解释|不能解释|does not cover).{0,80}(?:logout|relogin|重新登录|会话重建).{0,240}reset(?:/reset\.sh|\.sh)",
                    r"reset/reset\.sh.{0,180}(?:不证明|不能证明|不覆盖).{0,160}(?:logout|relogin)",
                ],
            },
            {
                "id": "iscsi_unit_coverage_scope",
                "assertion": (
                    "现有 iscsi_ut.c 不能被笼统宣称已覆盖错误响应 flags、Target Removed、"
                    "Authorization Failure 或所有 Login 失败语义；每项必须指向具体测试函数和断言。"
                ),
                "evidence": ["test/unit/lib/iscsi/iscsi.c/iscsi_ut.c"],
                "conflict_patterns": [
                    r"iscsi_ut\.c.{0,220}(?:已|already|覆盖|断言|covers?).{0,180}(?:target removed|authorization failure|错误响应\s*flags|所有.*(?:错误|失败))",
                    r"(?:target removed|authorization failure|错误响应\s*flags|所有.*(?:错误|失败)).{0,220}iscsi_ut\.c",
                ],
                "correction_patterns": [
                    r"iscsi_ut\.c.{0,220}(?:未覆盖|没有断言|不能证明|需新增|does not cover|missing assertion)",
                    r"(?:无法确认|不能确认).{0,160}iscsi_ut\.c.{0,160}(?:覆盖|断言).{0,120}(?:错误响应\s*flags|target removed|authorization failure|所有.*(?:错误|失败))",
                    r"iscsi_ut\.c.{0,220}(?:未完整断言|不能声称完整覆盖|只断言部分|partial).{0,160}(?:target removed|authorization failure|错误响应)",
                    r"iscsi_ut\.c.{0,360}(?:不能笼统(?:声称|宣称)|不得笼统(?:声称|宣称)|must not claim).{0,220}(?:target removed|authorization failure|错误响应|所有.*(?:错误|失败))",
                ],
            },
            {
                "id": "iscsi_multiconnection_scenario_semantics",
                "assertion": (
                    "multiconnection.sh 是单 initiator、多个 target/connection 的危险写入脚本；"
                    "不得把它映射为多 initiator、同一 target 多 CID 或通用并发登录用例。"
                ),
                "evidence": ["test/iscsi_tgt/multiconnection/multiconnection.sh"],
                "evaluate_hypothetical_mapping": True,
                "conflict_patterns": [
                    r'"scenario_name"\s*:\s*"[^"]*(?:多\s*initiator|multiple initiators?)[^"]*"[\s\S]{0,600}"(?:mapped_test_dir|test_mapping)"\s*:\s*"[^"]*multiconnection\.sh',
                ],
                "correction_patterns": [],
            },
            {
                "id": "iscsi_reset_relogin_mapping",
                "assertion": (
                    "reset/reset.sh 在持续 fio 中执行 sg_reset，不实现 logout/relogin；"
                    "logout 后重新登录必须使用独立脚本或明确的新用例。"
                ),
                "evidence": ["test/iscsi_tgt/reset/reset.sh"],
                "evaluate_hypothetical_mapping": True,
                "conflict_patterns": [
                    r'"scenario_name"\s*:\s*"[^"]*(?:logout|relogin|重新登录|会话重建)[^"]*"[\s\S]{0,600}"(?:mapped_test_dir|test_mapping)"\s*:\s*"[^"]*reset/reset\.sh',
                ],
                "correction_patterns": [
                    r"(?:独立|separate).{0,120}(?:logout|relogin).{0,180}reset\.sh.{0,100}(?:不被宣称|不覆盖|不实现|does not cover)",
                    r"(?:ai_suggested_unverified|独立用例|separate).{0,220}(?:logout|relogin).{0,220}reset/reset\.sh.{0,60}(?:不覆盖|不实现|does not cover)",
                ],
            },
            {
                "id": "iscsi_unit_assertion_mapping",
                "assertion": (
                    "Target Removed、Authorization Failure 和错误响应 flags 不能直接映射为 iscsi_ut.c 已覆盖；"
                    "必须引用其中真实存在的具体测试函数与断言，否则标为待新增。"
                ),
                "evidence": ["test/unit/lib/iscsi/iscsi.c/iscsi_ut.c"],
                "evaluate_hypothetical_mapping": True,
                "conflict_patterns": [
                    r'"(?:failure_mode|scenario_name)"\s*:\s*"[^"]*(?:target removed|authorization failure|错误响应[^\"]*flags)[^"]*"[\s\S]{0,700}"(?:mapped_test_dir|test_mapping)"\s*:\s*"[^"]*iscsi_ut\.c',
                ],
                "correction_patterns": [
                    r"iscsi_ut\.c.{0,240}(?:只断言部分|不能声称完整覆盖|未完整断言)[\s\S]{0,520}(?:ai_suggested_unverified|add .*assert)",
                ],
            },
            {
                "id": "iscsi_discovery_rpc_ephemeral",
                "assertion": (
                    "iscsiadm SendTargets discovery 的连接可能在 RPC 轮询前结束；"
                    "不能把 iscsi_get_connections 必定看见 discovery connection 作为通过条件，wire pcap 才是可靠观测。"
                ),
                "evidence": ["lib/iscsi/conn.c::iscsi_conn_info_json"],
                "conflict_patterns": [
                    r"discovery.{0,220}(?:rpc|iscsi_get_connections).{0,100}(?:必定|一定|可见|visible|must see)",
                    r"(?:rpc|iscsi_get_connections).{0,100}(?:必定|一定|可见|visible|must see).{0,140}discovery",
                ],
                "correction_patterns": [
                    r"discovery.{0,140}(?:可能短暂|短暂存在|ephemeral).{0,120}(?:不能要求|不得要求|not require).{0,100}(?:rpc|iscsi_get_connections)",
                    r"(?:不要求|不能要求|不得要求|not require).{0,80}(?:rpc|iscsi_get_connections).{0,100}(?:短暂|discovery|ephemeral)",
                ],
            },
            {
                "id": "iscsi_successful_login_phase_observation",
                "assertion": (
                    "处理成功的中间 Login PDU 可处于 Security/Operational 阶段；"
                    "只有整个 Login 完成后才能断言 running/full_feature_phase，不能对每个成功响应作此断言。"
                ),
                "evidence": ["lib/iscsi/iscsi.c::iscsi_pdu_payload_op_login"],
                "conflict_patterns": [
                    r"(?:每个|所有|any|every).{0,80}(?:成功|successful).{0,80}(?:login response|login pdu).{0,140}(?:running|full_feature_phase)",
                    r"(?:成功连接|successful connection).{0,100}(?:只|only).{0,80}(?:running|full_feature_phase)",
                ],
                "correction_patterns": [],
            },
            {
                "id": "iscsi_duplicate_key_scope",
                "assertion": (
                    "重复 key 必须区分同一 PDU 内重复与跨 Login PDU 再协商；"
                    "后者不一定是解析错误，不能笼统声称所有重复 key 都失败。"
                ),
                "evidence": ["lib/iscsi/param.c::iscsi_negotiate_param_init"],
                "conflict_patterns": [
                    r"(?:重复|duplicate).{0,50}(?:key|参数).{0,80}(?:一律|全部|所有|always|all).{0,80}(?:解析错误|失败|fail|error)",
                    r"(?:一律|全部|所有|always|all).{0,50}(?:重复|duplicate).{0,50}(?:key|参数).{0,80}(?:解析错误|失败|fail|error)",
                ],
                "correction_patterns": [
                    r"(?:同一\s*pdu|same pdu).{0,160}(?:跨|across).{0,60}(?:pdu|login)",
                    r"(?:不得|不能|不可|不应|do not).{0,40}(?:笼统)?(?:声称|认为|treat).{0,80}(?:跨\s*pdu|across.{0,20}pdu).{0,80}(?:重复|duplicate).{0,30}(?:key|参数).{0,40}(?:一律|全部|always|all).{0,30}(?:失败|fail|error)",
                ],
            },
            {
                "id": "iscsi_target_removed_release_evidence",
                "assertion": (
                    "Target Removed 竞态若只标为 ai_suggested_unverified，就不能同时计为发布通过项；"
                    "必须给出可控删除时序和可复验观测，或明确排除在通过统计外。"
                ),
                "evidence": ["lib/iscsi/iscsi.c::iscsi_op_login_check_target"],
                "conflict_patterns": [
                    r"target removed.{0,120}ai_suggested_unverified.{0,140}(?:发布通过|计为通过|pass|validated)",
                ],
                "correction_patterns": [
                    r"target removed.{0,120}ai_suggested_unverified.{0,140}(?:不|不得|不能|不可|not).{0,30}(?:计(?:入)?|作为|count).{0,40}(?:发布通过|pass)",
                ],
            },
            {
                "id": "iscsi_mutual_not_set_mapping_direction",
                "assertion": (
                    "chap_mutual_not_set.sh 覆盖 initiator 请求 Mutual CHAP、但 target 未启用 mutual 的拒绝路径；"
                    "它不覆盖 target 要求 Mutual CHAP 而 initiator 未提供。"
                ),
                "evidence": ["test/iscsi_tgt/chap/chap_mutual_not_set.sh"],
                "evaluate_hypothetical_mapping": True,
                "conflict_patterns": [
                    r'"scenario_name"\s*:\s*"[^"]*(?:target|目标端).{0,80}(?:要求|require).{0,40}mutual\s*chap.{0,100}(?:initiator|发起端).{0,80}(?:未提供|缺失|missing|without)[^"]*"[\s\S]{0,700}"(?:mapped_test_dir|test_mapping)"\s*:\s*"[^"]*chap_mutual_not_set\.sh',
                ],
                "correction_patterns": [],
            },
            {
                "id": "iscsi_fuzz_calsoft_semantic_mapping",
                "assertion": (
                    "autofuzz_iscsi.sh 和 calsoft.py 不能作为未知 key=NotUnderstood、重复 key 或 C-bit 分片的确定性语义断言映射；"
                    "这些场景需要独立可复验 harness。"
                ),
                "evidence": [
                    "test/fuzz/autofuzz_iscsi.sh",
                    "test/iscsi_tgt/calsoft/calsoft.py",
                ],
                "evaluate_hypothetical_mapping": True,
                "conflict_patterns": [
                    r'"scenario_name"\s*:\s*"[^"]*(?:notunderstood|未知\s*key|重复\s*key|c-bit|c\s*位|分片)[^"]*"[\s\S]{0,700}"(?:mapped_test_dir|test_mapping)"\s*:\s*"[^"]*(?:autofuzz_iscsi\.sh|calsoft\.py)',
                ],
                "correction_patterns": [],
            },
            {
                "id": "sfmea_rpn_not_defect_priority",
                "assertion": (
                    "SFMEA RPN 风险分层不得直接命名为项目缺陷 P0/P1/P2；"
                    "应使用“SFMEA 一级/二级风险”等独立术语，避免与缺陷优先级混淆。"
                ),
                "evidence": [],
                "conflict_patterns": [
                    r"rpn.{0,80}(?:>=|≥|大于等于).{0,20}\d+.{0,80}(?:定义为|命名为|标为|is)\s*p[012]",
                ],
                "correction_patterns": [],
            },
            {
                "id": "black_box_raw_device_safety",
                "assertion": (
                    "黑盒 IO 用例不得直接写未确认的宿主裸设备（例如 dd of=/dev/sdX）；"
                    "必须使用明确创建的隔离测试设备、测试命名空间或可销毁镜像，并在前置条件中确认目标。"
                ),
                "evidence": ["test/iscsi_tgt/common.sh"],
                "conflict_patterns": [
                    r"\bdd\b.{0,180}\bof\s*=\s*/dev/(?:sd[a-z]|nvme\d+n\d+|vd[a-z]|xvd[a-z])\b",
                ],
                "correction_patterns": [
                    r"(?:专用测试盘|隔离测试设备|已确认设备|disposable test device|isolated test device|explicit device confirmation).{0,180}\bdd\b",
                    r"\bdd\b.{0,180}(?:专用测试盘|隔离测试设备|已确认设备|disposable test device|isolated test device|explicit device confirmation)",
                ],
            },
            {
                "id": "black_box_raw_device_identity",
                "assertion": (
                    "黑盒 IO 前置条件必须动态解析本次 iSCSI 会话对应的设备，并通过 by-path、"
                    "序列号或测试命名空间确认身份；不得直接把 /dev/sdX、/dev/nvmeXnY 等宿主裸设备占位符交给用户执行。"
                ),
                "evidence": ["test/iscsi_tgt/common.sh"],
                "conflict_patterns": [
                    r"/dev/(?:sd[xX]|nvme[xX]\w*|vd[xX]|xvd[xX])\b",
                    r"(?:--filename|filename\s*=|\bof\s*=)\s*/dev/(?:sd[a-z]|nvme\d+n\d+|vd[a-z]|xvd[a-z])\b",
                ],
                "correction_patterns": [
                    r"(?:动态解析|by-path|序列号|serial|隔离测试设备|专用测试设备|测试命名空间|disposable|isolated).{0,220}(?:/dev/|filename|设备)",
                    r"(?:/dev/|filename|设备).{0,220}(?:动态解析|by-path|序列号|serial|隔离测试设备|专用测试设备|测试命名空间|disposable|isolated)",
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
    "long_steady_state",
    "resource_wraparound",
    "resource_cleanup",
    "upstream_error_propagation",
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
    "flow_map.md": {"preview": "markdown", "sections": ["外部触发", "流程步骤", "异常分支", "观测点"], "required_fields": ["steps", "evidence"]},
    "tester_code_understanding.md": {"preview": "markdown", "sections": ["测试视角摘要", "可观测行为", "不可直接依赖的内部细节"], "required_fields": ["observable_behavior", "boundaries"]},
    "sfmea.json": {
        "preview": "table",
        "required_fields": ["failure_mode", "cause", "effect", "detection", "severity", "occurrence", "detection_score", "rpn", "score_explanation", "mitigation", "source_evidence", "test_mapping"],
        "field_rules": {
            "mitigation": "每条 mitigation 必须同时包含具体整改动作，以及可执行的测试、监控或日志验证动作。",
        },
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
    "test_design_mindmap.md": {
        "preview": "mermaid",
        "required_fields": ["target", "evidence", "flows", "risks", "cases"],
        "required_terms": [
            "目标",
            "输入",
            "源码证据",
            "业务流程",
            "SFMEA",
            "黑盒用例",
            "观测点",
            "剩余风险",
        ],
        "required_mermaid_diagram": "mindmap",
    },
    "coverage_gap_report.md": {"preview": "markdown", "sections": ["覆盖缺口", "入口", "补充建议"], "required_fields": ["gaps", "recommendations"]},
    "risk_review.md": {"preview": "markdown", "sections": ["高风险项", "证据", "建议"], "required_fields": ["risks", "evidence"]},
    "execution_checklist.md": {"preview": "markdown", "sections": ["前置检查", "执行步骤", "验收"], "required_fields": ["preflight", "steps", "acceptance"]},
    "combined_test_report.md": {
        "preview": "markdown",
        "sections": ["分析范围与证据缺口", "关键源码证据", "主流程与异常/恢复流程", "SFMEA", "黑盒测试用例"],
        "min_sfmea_rows": 12,
        "min_black_box_cases": 12,
        "min_source_paths": 6,
        "min_test_paths": 4,
    },
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
    declared_templates = _declared_output_templates(workflow_outputs or [])
    artifact_contract = {
        artifact: _artifact_contract_payload(
            artifact,
            declared_templates.get(artifact) or ARTIFACT_TEMPLATES[artifact],
        )
        for artifact in required_outputs
        if artifact in declared_templates or artifact in ARTIFACT_TEMPLATES
    }
    focus_rationale = _focus_rationale(
        domain_profiles=domain_profiles,
        project_profile=project_profile,
        user_requirements=user_requirements,
    )
    domain_requirements = {
        profile_id: {
            "required_scenarios": list(PROFILE_REGISTRY[profile_id].get("required_scenarios", [])),
            "failure_modes": list(PROFILE_REGISTRY[profile_id].get("failure_modes", [])),
            "black_box_observability": list(PROFILE_REGISTRY[profile_id].get("black_box_observability", [])),
            "graybox_evidence_points": list(PROFILE_REGISTRY[profile_id].get("graybox_evidence_points", [])),
        }
        for profile_id in domain_profiles
    }
    if "iscsi_login" in domain_profiles and "完整" in combined_text:
        domain_requirements["iscsi_login"]["required_atomic_scenarios"] = list(
            COMPLETE_ISCSI_LOGIN_REQUIRED_ATOMIC_SCENARIOS
        )
    return {
        "contract_version": 1,
        "target": target_text,
        "domain_profiles": domain_profiles,
        "project_profile": project_profile,
        "user_requirements": str(user_requirements or ""),
        "required_outputs": required_outputs,
        "focus_rationale": focus_rationale,
        "domain_requirements": domain_requirements,
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
            "require_independent_behavior_validation": True,
            "high_risk_requires_source_or_test_evidence": True,
            "black_box_cases_must_not_call_internal_functions": True,
            "missing_required_artifacts_block_delivery": True,
            "required_black_box_dimensions": list(BLACK_BOX_REQUIRED_DIMENSIONS),
            "must_record_source_revision": True,
            "protocol_claims_require_wire_observer": True,
            "protocol_observer_must_be_executable": True,
            "raw_pdu_cases_require_runnable_harness": True,
            "complete_iscsi_requires_chap_negative_matrix": True,
            "complete_iscsi_requires_extended_chap_negative_matrix": True,
            "complete_iscsi_requires_c_bit_fragmentation_case": True,
            "sfmea_requires_scoring_scale_and_priority": True,
            "sfmea_occurrence_requires_data_basis": True,
            "relative_performance_threshold_requires_variance_basis": True,
            "black_box_cases_must_be_atomic": True,
            "hazardous_test_mapping_requires_safety": True,
            "numeric_performance_threshold_requires_baseline": True,
        },
        "executor_requirements": {
            "must_receive_full_user_input": True,
            "must_read_workspace_source_unless_user_declines": True,
            "must_generate_declared_artifacts": True,
            "invalid_short_greeting_is_failure": True,
        },
        "artifact_contract": artifact_contract,
    }


def refresh_test_activity_contract(
    contract: dict[str, Any],
    *,
    declared_artifacts: list[str],
) -> dict[str, Any]:
    """Upgrade artifact rules for a saved task while preserving its domain analysis."""
    refreshed = dict(contract or {})
    existing_artifacts = (
        contract.get("artifact_contract")
        if isinstance(contract.get("artifact_contract"), dict)
        else {}
    )
    declared = _unique_strings(
        str(item).strip()
        for item in declared_artifacts
        if str(item).strip() in ARTIFACT_TEMPLATES
    )
    if not declared:
        return refreshed
    quality_gates = dict(refreshed.get("quality_gates") or {})
    quality_gates.setdefault("require_independent_behavior_validation", True)
    refreshed["quality_gates"] = quality_gates
    refreshed["required_outputs"] = declared
    artifact_contract: dict[str, dict[str, Any]] = {}
    preserved_policy_fields = (
        "min_sfmea_rows",
        "min_black_box_cases",
        "min_source_paths",
        "min_test_paths",
        "required_dimensions",
        "required_evidence_terms",
        "required_terms",
        "forbidden_evidence_path_prefixes",
        "forbidden_claim_terms",
    )
    for artifact in declared:
        payload = _artifact_contract_payload(artifact, ARTIFACT_TEMPLATES[artifact])
        existing = existing_artifacts.get(artifact)
        if isinstance(existing, dict):
            for key in preserved_policy_fields:
                if existing.get(key) not in (None, [], ""):
                    payload[key] = copy.deepcopy(existing[key])
        artifact_contract[artifact] = payload
    refreshed["artifact_contract"] = artifact_contract
    return refreshed


_PROFESSIONAL_MARKER_LINT_CODES = {
    "missing_iscsi_professional_scenarios",
    "missing_chap_negative_scenarios",
    "missing_extended_chap_negative_scenarios",
}
_PROFESSIONAL_EXECUTABILITY_CODES = {
    "non_executable_raw_pdu_harness",
    "missing_protocol_wire_observer",
    "non_executable_protocol_observer",
    "unsafe_hazardous_test_mapping",
    "non_executable_mcs_client",
    "missing_mcs_capable_client",
}
_PROFESSIONAL_COVERAGE_GROUP_COUNT = len(_PROFESSIONAL_MARKER_LINT_CODES)


def _professional_coverage_axis(
    lint_warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Expose incomplete domain coverage without treating it as a fact failure.

    The professional scenario checks are intentionally heuristics, so they do
    not replace L1/L2 fact gates.  They are nevertheless product-quality
    evidence: a rapid run may be downloadable with declared gaps, while a
    deep run must turn the same gaps into a delivery block at the profile
    layer.  Keeping this as its own axis prevents a structurally valid report
    from being represented as fully covered.
    """
    warnings = [
        issue
        for issue in lint_warnings
        if str(issue.get("code") or "") in _PROFESSIONAL_MARKER_LINT_CODES
    ]
    if not warnings:
        return {
            "status": "passed",
            "score": 100,
            "issue_count": 0,
            "missing_scenario_count": 0,
            "missing_scenarios": [],
            "declared_scope": "professional_scenario_coverage",
            "warnings": [],
        }
    codes = {str(issue.get("code") or "") for issue in warnings}
    scenarios: list[str] = []
    for issue in warnings:
        for raw_scenario in issue.get("scenarios") or []:
            scenario = str(raw_scenario).strip()
            if scenario and scenario not in scenarios:
                scenarios.append(scenario)
    covered_groups = max(0, _PROFESSIONAL_COVERAGE_GROUP_COUNT - len(codes))
    return {
        "status": "warning",
        "score": round(covered_groups * 100 / _PROFESSIONAL_COVERAGE_GROUP_COUNT),
        "issue_count": len(codes),
        "missing_scenario_count": len(scenarios),
        # The profile gate turns this advisory axis into a deep-delivery
        # repair contract.  Preserve the individual scenario names instead
        # of only their rendered prose, otherwise the repair stage cannot
        # tell which structured black-box cases it must add.
        "missing_scenarios": scenarios,
        "declared_scope": "professional_scenario_coverage",
        "warnings": [
            str(issue.get("message") or "存在待扩展的专业测试场景")
            for issue in warnings
        ],
    }


def _partition_combined_professional_issues(
    issues: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep marker heuristics out of fact/structure gates and route L3 failures."""
    structural: list[dict[str, Any]] = []
    lint: list[dict[str, Any]] = []
    executable: list[dict[str, Any]] = []
    for issue in issues:
        code = str(issue.get("code") or "")
        if code in _PROFESSIONAL_MARKER_LINT_CODES:
            lint.append(issue)
        elif code in _PROFESSIONAL_EXECUTABILITY_CODES:
            executable.append(issue)
        else:
            structural.append(issue)
    return structural, lint, executable


def audit_test_activity_artifacts(
    *,
    artifact_dir: str | Path,
    contract: dict[str, Any],
    repo_path: str = "",
) -> dict[str, Any]:
    root = Path(artifact_dir)
    repo = Path(str(repo_path or ""))
    structural_issues: list[dict[str, Any]] = []
    executable_issues: list[dict[str, Any]] = []
    lint_warnings: list[dict[str, Any]] = []
    structured_semantic_issues: list[dict[str, Any]] = []
    structured_semantic_claims: list[dict[str, Any]] = []
    execution_checks_applicable = False
    artifact_contract = contract.get("artifact_contract") or {}
    structured_black_box_declared = any(
        str(artifact).endswith(".json")
        and (
            "black_box" in Path(str(artifact)).stem.lower()
            or int((spec or {}).get("min_black_box_cases") or 0) > 0
        )
        for artifact, spec in artifact_contract.items()
        if isinstance(spec, dict)
    ) or (root / "black_box_cases.json").is_file()
    audited_json_artifacts: set[str] = set()
    quality_gates = contract.get("quality_gates") or {}
    behavior_validation_surface = any(
        "sfmea" in str(artifact).lower()
        or "black_box" in str(artifact).lower()
        or str((spec or {}).get("type") or "").lower()
        == "combined_test_report"
        for artifact, spec in artifact_contract.items()
        if isinstance(spec, dict)
    )
    require_behavior_validation = bool(
        quality_gates.get("require_independent_behavior_validation", False)
        and behavior_validation_surface
    )
    if require_behavior_validation:
        behavior_validation = _read_json(
            _artifact_path(root, "behavior_claim_validation.json")
        )
        validation = behavior_validation if isinstance(behavior_validation, dict) else {}
        validator = validation.get("validator")
        validator = validator if isinstance(validator, dict) else {}
        validation_status = str(validation.get("status") or "missing").strip().lower()
        independent = bool(validator.get("independent"))
        if validation_status != "completed" or not independent:
            reason = str(validation.get("reason") or "未生成可用的独立核验结论。")
            structural_issues.append(
                _issue(
                    "independent_behavior_validation_unavailable",
                    "behavior_claim_validation.json",
                    (
                        "工作流要求独立源码事实核验，但当前核验不可作为独立审计使用："
                        f"状态为 {validation_status or 'missing'}，原因：{reason}。"
                        "请配置与生成执行器不同的独立审计模型或 Agent 后重新运行。"
                    ),
                    severity="blocking",
                    validation_status=validation_status or "missing",
                    independent=independent,
                    recommended_action=(
                        "选择与生成执行器不同的独立质量核验模型或 Agent，然后从失败节点重试。"
                    ),
                )
            )

    def record_semantic_conflicts(
        *,
        artifact: str,
        content: str,
        row_id: str = "",
        infer_structured_section: bool = False,
    ) -> None:
        """Turn delivery-level professional conflicts into fact-gate claims.

        Markdown and JSON are both user-facing delivery surfaces. A correct
        summary must not mask a false test-path mapping or source statement in
        either authoritative representation.
        """
        if not content.strip():
            return
        row_token = re.sub(r"[^A-Za-z0-9_.-]+", "-", row_id).strip("-")
        for index, raw_issue in enumerate(
            _audit_professional_constraints(
                content,
                contract,
                source_artifact=artifact,
                # Only a combined report contains multiple delivery sections.
                # Infer its section so an SFMEA fact conflict is attributed to
                # the structured SFMEA output. Standalone Markdown keeps its
                # own artifact attribution.
                infer_structured_section=infer_structured_section,
            ),
            start=1,
        ):
            issue = dict(raw_issue)
            if row_id:
                issue["row_id"] = row_id
            constraint_id = str(issue.get("constraint_id") or "semantic-mapping")
            structured_semantic_issues.append(issue)
            structured_semantic_claims.append(
                {
                    "claim_id": (
                        f"SEM-{Path(artifact).stem.upper()}-{row_token or index}-{index:03d}"
                    ),
                    "type": (
                        "test_mapping_semantics"
                        if "mapping" in constraint_id
                        else "professional_semantics"
                    ),
                    "statement": str(issue.get("message") or "测试映射与已验证领域事实冲突"),
                    "status": "contradicted",
                    "source_artifact": artifact,
                    "constraint_id": constraint_id,
                    "evidence": list(issue.get("evidence") or []),
                }
            )

    def audit_structured_semantics(*, artifact: str, payload: Any) -> None:
        if not isinstance(payload, (dict, list)):
            return
        rows = payload if isinstance(payload, list) else [payload]
        row_id_key = "sfmea_id" if artifact == "sfmea.json" else "case_id"
        semantic_keys = (
            "failure_mode",
            "scenario_name",
            "expected_result",
            "test_mapping",
            "mapped_test_dir",
            "source_evidence",
            "source_or_test_evidence",
        )
        for row_index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                continue
            # Constraint patterns express a semantic relation such as
            # scenario -> mapped test. Rebuild it in a stable relation-first
            # order so parsing a JSON artifact cannot alter the verdict.
            ordered = {
                key: row[key]
                for key in semantic_keys
                if key in row and row[key] not in (None, "", [], {})
            }
            if ordered:
                record_semantic_conflicts(
                    artifact=artifact,
                    content=json.dumps(ordered, ensure_ascii=False),
                    row_id=str(row.get(row_id_key) or f"row-{row_index}").strip(),
                )

    if contract.get("audit_scope_required") and not artifact_contract:
        structural_issues.append(
            _issue(
                "empty_test_activity_audit_scope",
                "workflow",
                "工作流声明了测试活动交付件，但没有可审计的输出契约",
            )
        )
    for artifact, spec in artifact_contract.items():
        path = _artifact_path(root, artifact)
        if not path.exists():
            structural_issues.append(
                _issue("missing_required_artifact", artifact, f"缺少交付件 {artifact}")
            )
            continue
        if artifact.endswith(".json"):
            payload = _read_json(path)
            structural_issues.extend(
                _audit_json_artifact(artifact=artifact, payload=payload, spec=spec, repo=repo)
            )
            audit_structured_semantics(artifact=artifact, payload=payload)
            audited_json_artifacts.add(Path(str(artifact)).name)
        else:
            content = path.read_text(encoding="utf-8", errors="ignore").strip()
            if not content:
                structural_issues.append(
                    _issue("empty_artifact", artifact, f"{artifact} 内容为空")
                )
            elif artifact.endswith(".md"):
                structural_issues.extend(
                    _audit_markdown_artifact(
                        artifact=artifact,
                        content=content,
                        spec=spec,
                        repo=repo,
                    )
                )
                structural_issues.extend(
                    _audit_markdown_evidence_anchors(
                        artifact=artifact,
                        content=content,
                        root=root,
                    )
                )
                combined_report = _is_combined_test_report_spec(spec)
                record_semantic_conflicts(
                    artifact=artifact,
                    content=content,
                    infer_structured_section=combined_report,
                )
                if combined_report:
                    execution_checks_applicable = True
                    professional_issues = _audit_combined_professional_completeness(
                        content,
                        contract,
                        include_execution=False,
                        include_consistency=False,
                    )
                    routed_structure, routed_lint, routed_executable = (
                        _partition_combined_professional_issues(professional_issues)
                    )
                    # Structured black-box cases are the authoritative repair
                    # surface. The report remains a presentation artifact; its
                    # aggregate safety/performance finding has no case_id and
                    # therefore cannot drive a safe, targeted repair.
                    if structured_black_box_declared:
                        routed_executable = [
                            issue
                            for issue in routed_executable
                            if str(issue.get("code") or "")
                            != "unsafe_hazardous_test_mapping"
                        ]
                    structural_issues.extend(routed_structure)
                    lint_warnings.extend(routed_lint)
                    executable_issues.extend(routed_executable)
                    executable_issues.extend(_audit_combined_execution_contract(content))
                    executable_issues.extend(
                        _audit_raw_pdu_runtime_evidence(root=root, content=content)
                    )
                    consistency_issues = _audit_combined_report_consistency(content)
                    if structured_black_box_declared:
                        consistency_issues = [
                            issue
                            for issue in consistency_issues
                            if str(issue.get("code") or "")
                            not in {
                                "ungrounded_performance_threshold",
                                "missing_performance_statistical_basis",
                            }
                        ]
                    # A report-level consistency check can expose a direct
                    # contradiction between a user-facing SFMEA/black-box
                    # assertion and its verified source evidence.  Those are
                    # factual gate failures, not optional presentation lint.
                    # Route them through the same severity partition used by
                    # the completeness checks so a contradictory report can
                    # never receive a deliverable verdict merely because its
                    # JSON artifacts look structurally valid.
                    (
                        consistency_structure,
                        consistency_lint,
                        consistency_executable,
                    ) = _partition_combined_professional_issues(consistency_issues)
                    structural_issues.extend(consistency_structure)
                    lint_warnings.extend(consistency_lint)
                    executable_issues.extend(consistency_executable)
    # Staged workflows commonly expose one formal Markdown report while using
    # SFMEA, black-box JSON, and flow cards as the authoritative intermediate
    # fact ledger.  A report-only contract must not bypass a known disconnected
    # flow graph.
    # Those rows remain quality-critical even when they are not separate user
    # downloads; otherwise report-only contracts lose case-level repair IDs.
    for artifact in ("flow_cards.json", "sfmea.json", "black_box_cases.json"):
        # The Harness stores structured stage outputs under the producing node
        # (for example ``agent_runs/analyze``). Resolve them through the same
        # path policy as declared artifacts so report-only contracts cannot
        # bypass their quality gates simply because the files are nested.
        path = _artifact_path(root, artifact)
        if artifact in audited_json_artifacts or not path.is_file():
            continue
        payload = _read_json(path)
        structural_issues.extend(
            _audit_json_artifact(
                artifact=artifact,
                payload=payload,
                spec={},
                repo=repo,
            )
        )
        audit_structured_semantics(artifact=artifact, payload=payload)
    structural_issues.extend(_audit_cross_artifact_references(
        root=root,
        declared_artifacts={str(item) for item in artifact_contract},
    ))
    fact_claims, fact_issues = _audit_structured_fact_claims(
        root=root,
        repo=repo,
        require_behavior_validation=require_behavior_validation,
    )
    fact_claims.extend(structured_semantic_claims)
    fact_issues.extend(structured_semantic_issues)
    issues = [*structural_issues, *fact_issues, *executable_issues]
    structure_score = max(0, 100 - len(structural_issues) * 15)
    empty_scope = any(
        item.get("code") == "empty_test_activity_audit_scope"
        for item in structural_issues
    )
    if empty_scope:
        structure_score = 0
    fact_total = len(fact_claims)
    fact_verified = sum(claim.get("status") == "verified" for claim in fact_claims)
    fact_contradicted = sum(
        claim.get("status") == "contradicted" for claim in fact_claims
    )
    fact_insufficient = sum(
        claim.get("status") == "insufficient" for claim in fact_claims
    )
    fact_pass_rate = round(fact_verified * 100 / fact_total) if fact_total else 100
    executable_pass_rate = 0 if executable_issues else 100
    coverage_breadth = _professional_coverage_axis(lint_warnings)
    quality_axes = {
        "structure": {
            "status": "blocked" if structural_issues else "passed",
            "score": structure_score,
            "issue_count": len(structural_issues),
        },
        "facts": {
            "status": (
                "not_checked"
                if not fact_total
                else "blocked"
                if fact_contradicted or fact_insufficient
                else "passed"
            ),
            "total": fact_total,
            "verified": fact_verified,
            "contradicted": fact_contradicted,
            "insufficient": fact_insufficient,
            "pass_rate": fact_pass_rate,
        },
        "executability": {
            "status": (
                "not_checked"
                if not execution_checks_applicable
                else "blocked"
                if executable_issues
                else "passed"
            ),
            "issue_count": len(executable_issues),
            "pass_rate": executable_pass_rate if execution_checks_applicable else None,
        },
        "coverage_breadth": coverage_breadth,
    }
    score = min(
        structure_score,
        fact_pass_rate if fact_total else 100,
        executable_pass_rate if execution_checks_applicable else 100,
        int(coverage_breadth["score"]),
    )
    status = (
        "invalid"
        if empty_scope
        else "needs_rework"
        if issues
        else "warning"
        if coverage_breadth["status"] == "warning"
        else "deliverable"
    )
    recommendations = _recommendations_for_issues(issues)
    if coverage_breadth["status"] == "warning":
        recommendations.insert(
            0,
            "本次结果保留为受限覆盖交付；请补充覆盖广度轴列出的协议、认证或恢复场景，"
            "再用于完整测试设计评审。",
        )
    return {
        "kind": "test_activity_quality_audit",
        "status": status,
        "deliverable": status == "deliverable",
        "score": score,
        "issue_count": len(issues),
        "issues": issues,
        "lint_warning_count": len(lint_warnings),
        "lint_warnings": lint_warnings,
        "recommendations": recommendations,
        "fact_verification": {
            "total": fact_total,
            "verified": fact_verified,
            "contradicted": fact_contradicted,
            "insufficient": fact_insufficient,
            "pass_rate": fact_pass_rate,
        },
        "fact_claims": fact_claims,
        "quality_axes": quality_axes,
    }


def _audit_structured_fact_claims(
    *,
    root: Path,
    repo: Path,
    require_behavior_validation: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Route deterministic protocol claims to source-backed validators."""
    constants = _verified_c_constant_ledger(root=root, repo=repo)
    verified_files = _verified_evidence_files(root=root, repo=repo)
    behavior_validation = _read_json(
        _artifact_path(root, "behavior_claim_validation.json")
    )
    claims: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    sfmea = _read_json(_artifact_path(root, "sfmea.json"))
    structured_artifacts = {
        "sfmea.json": sfmea if isinstance(sfmea, list) else [],
        "black_box_cases.json": _read_json(_artifact_path(root, "black_box_cases.json")),
    }
    for artifact, rows in structured_artifacts.items():
        if not isinstance(rows, list):
            continue
        structured_claims, structured_issues = _audit_explicit_technical_claims(
            artifact=artifact,
            rows=rows,
            verified_files=verified_files,
            behavior_validation=(
                behavior_validation
                if isinstance(behavior_validation, dict)
                else {}
            ),
            require_behavior_validation=require_behavior_validation,
        )
        claims.extend(structured_claims)
        issues.extend(structured_issues)
        if require_behavior_validation:
            row_claims, row_issues = _audit_row_behavior_claims(
                artifact=artifact,
                rows=rows,
                verified_files=verified_files,
                explicit_claims=structured_claims,
                behavior_validation=(
                    behavior_validation
                    if isinstance(behavior_validation, dict)
                    else {}
                ),
            )
            claims.extend(row_claims)
            issues.extend(row_issues)
    if not isinstance(sfmea, list):
        return claims, issues

    iscsi_version = constants.get("ISCSI_VERSION")
    for index, row in enumerate(sfmea):
        if not isinstance(row, dict):
            continue
        row_id = str(
            row.get("sfmea_id") or row.get("risk_id") or row.get("id") or f"row-{index + 1}"
        )
        statement = " ".join(
            str(row.get(field) or "")
            for field in ("failure_mode", "cause", "effect", "detection")
        )
        version_values = _extract_iscsi_version_range(statement)
        describes_unsupported = bool(re.search(
            r"unsupported\s+version|不支持.{0,12}版本",
            statement,
            flags=re.IGNORECASE,
        ))
        if version_values is not None and iscsi_version is not None and describes_unsupported:
            version_max, version_min = version_values
            source_value = int(iscsi_version["integer_value"])
            supported = version_min <= source_value <= version_max
            claim_id = f"{row_id}:protocol_version_range"
            status = "contradicted" if supported else "verified"
            claim = {
                "claim_id": claim_id,
                "type": "protocol_version_range",
                "statement": (
                    f"version_max={version_max}, version_min={version_min} "
                    "会触发 Unsupported Version"
                ),
                "status": status,
                "source_truth": f"ISCSI_VERSION={iscsi_version['raw_value']}",
                "evidence": [
                    {
                        "path": iscsi_version["file_path"],
                        "line": iscsi_version["line"],
                        "symbol": "ISCSI_VERSION",
                        "quote": iscsi_version["source_line"],
                        "sha256": iscsi_version["sha256"],
                    }
                ],
            }
            claims.append(claim)
            if status == "contradicted":
                issues.append(
                    _issue(
                        "source_claim_contradicted",
                        "sfmea.json",
                        f"{row_id} 把版本范围 {version_min}..{version_max} 判为不支持，"
                        f"但已验证源码定义 {claim['source_truth']}，该范围包含当前支持版本。",
                        claim_id=claim_id,
                        claim_type="protocol_version_range",
                        source_truth=claim["source_truth"],
                        evidence=claim["evidence"],
                        validation_layer="L1_deterministic",
                    )
                )

        log_fields = " ".join(
            str(row.get(field) or "") for field in ("effect", "detection")
        )
        log_literals = _extract_exact_log_literals(log_fields)
        evidence_paths = _structured_source_evidence_paths(
            row.get("source_evidence"),
            verified_files=verified_files,
        )
        candidate_files = [
            verified_files[path]
            for path in evidence_paths
            if path in verified_files
        ]
        for literal_index, (literal, uncertain) in enumerate(log_literals, start=1):
            # A delivery may name an unverified log only as an observation gap.
            # That is deliberately not a claim that the source emits the literal,
            # so it belongs in the report but must not poison the fact ledger.
            if uncertain:
                continue
            matches = [
                metadata
                for metadata in candidate_files
                if literal in str(metadata.get("content") or "")
            ]
            log_status = "verified" if matches else "insufficient" if uncertain else "contradicted"
            log_claim_id = f"{row_id}:log_literal:{literal_index}"
            log_claim = {
                "claim_id": log_claim_id,
                "type": "log_literal",
                "statement": f"源码会输出日志原文: {literal}",
                "status": log_status,
                "evidence": [
                    {
                        "path": path,
                        "sha256": verified_files[path]["sha256"],
                    }
                    for path in evidence_paths
                    if path in verified_files
                ],
            }
            claims.append(log_claim)
            if log_status == "verified":
                continue
            code = (
                "source_claim_insufficient"
                if log_status == "insufficient"
                else "source_claim_contradicted"
            )
            issues.append(
                _issue(
                    code,
                    "sfmea.json",
                    (
                        f"{row_id} 给出日志原文“{literal}”，但该文本未出现在其已验证源码证据中；"
                        "请改为真实日志原文或明确标为待验证。"
                    ),
                    claim_id=log_claim_id,
                    claim_type="log_literal",
                    row_id=row_id,
                    claimed_literal=literal,
                    evidence=log_claim["evidence"],
                    validation_layer="L1_deterministic",
                )
            )
    return claims, issues


def _claim_lines_within_evidence_card(
    value: str,
    card: dict[str, Any],
) -> bool:
    normalized = str(value or "").strip()
    if not normalized:
        return True
    match = re.fullmatch(r"L?(\d+)(?:\s*-\s*L?(\d+))?", normalized)
    if not match:
        return False
    first_line = int(match.group(1))
    last_line = int(match.group(2) or first_line)
    return (
        int(card.get("start_line") or 0) <= first_line <= last_line
        <= int(card.get("end_line") or 0)
    )


def _audit_explicit_technical_claims(
    *,
    artifact: str,
    rows: list[Any],
    verified_files: dict[str, dict[str, Any]],
    behavior_validation: dict[str, Any] | None = None,
    require_behavior_validation: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    claims: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        row_id = str(
            row.get("sfmea_id")
            or row.get("case_id")
            or row.get("risk_id")
            or row.get("id")
            or f"row-{row_index}"
        ).strip()
        row_declared_paths = set(
            _structured_source_evidence_paths(
                [
                    *(row.get("source_evidence") or []),
                    *(row.get("source_or_test_evidence") or []),
                ],
                verified_files=verified_files,
            )
        )
        for claim_index, raw_claim in enumerate(row.get("technical_claims") or [], start=1):
            if not isinstance(raw_claim, dict):
                continue
            claim_id = str(raw_claim.get("claim_id") or f"{row_id}:claim:{claim_index}").strip()
            claim_type = str(raw_claim.get("type") or "technical_assertion").strip()
            statement = str(raw_claim.get("statement") or "").strip()
            checked_evidence: list[dict[str, Any]] = []
            valid_evidence: list[dict[str, Any]] = []
            has_verified_path = False
            for raw_evidence in raw_claim.get("evidence") or []:
                if not isinstance(raw_evidence, dict):
                    continue
                relative = str(raw_evidence.get("path") or "").replace("\\", "/").lstrip("/")
                if not relative or ".." in Path(relative).parts:
                    continue
                if row_declared_paths and relative not in row_declared_paths:
                    issues.append(
                        _issue(
                            "claim_evidence_not_declared_for_row",
                            artifact,
                            (
                                f"{row_id} 的技术断言 {claim_id} 使用了未在该条目证据集合中声明的路径: "
                                f"{relative}"
                            ),
                            row_id=row_id,
                            claim_id=claim_id,
                            evidence_path=relative,
                            declared_paths=sorted(row_declared_paths),
                        )
                    )
                metadata = verified_files.get(relative)
                if not metadata:
                    checked_evidence.append({"path": relative, "verified_path": False})
                    continue
                has_verified_path = True
                quote = str(raw_evidence.get("quote") or "").strip()
                symbol = str(raw_evidence.get("symbol") or "").strip()
                evidence_id = str(raw_evidence.get("evidence_id") or "").strip()
                content = str(metadata.get("content") or "")
                canonical_anchor = (
                    metadata.get("evidence_anchors", {}).get(evidence_id)
                    if evidence_id and isinstance(metadata.get("evidence_anchors"), dict)
                    else None
                )
                canonical_card = (
                    metadata.get("evidence_cards", {}).get(evidence_id)
                    if evidence_id and isinstance(metadata.get("evidence_cards"), dict)
                    else None
                )
                card_quote_matches = bool(
                    isinstance(canonical_card, dict)
                    and quote
                    and quote in str(canonical_card.get("excerpt") or "")
                    and _claim_lines_within_evidence_card(
                        str(raw_evidence.get("lines") or ""),
                        canonical_card,
                    )
                )
                evidence_id_matches = bool(
                    not evidence_id
                    or (
                        isinstance(canonical_anchor, dict)
                        and quote == str(canonical_anchor.get("quote") or "")
                    )
                    or card_quote_matches
                )
                quote_matches = bool(quote and quote in content and evidence_id_matches)
                symbol_matches = bool(not symbol or symbol in content)
                evidence = {
                    "evidence_id": evidence_id,
                    "path": relative,
                    "symbol": symbol,
                    "lines": str(raw_evidence.get("lines") or "").strip(),
                    "quote": quote,
                    "sha256": str(metadata.get("sha256") or ""),
                    "quote_matches": quote_matches,
                    "evidence_id_matches": evidence_id_matches,
                    "symbol_matches": symbol_matches,
                }
                checked_evidence.append(evidence)
                if quote_matches and symbol_matches:
                    valid_evidence.append(evidence)
            semantic_status, semantic_reason = _deterministic_claim_semantics(
                claim_type=claim_type,
                statement=statement,
                evidence=valid_evidence,
            )
            if semantic_status == "requires_l2" and not require_behavior_validation:
                semantic_status, semantic_reason = "supported", ""
            validation_layer = "L1_deterministic"
            if valid_evidence and semantic_status == "requires_l2":
                binding = _behavior_claim_binding(
                    claim_id=claim_id,
                    claim_type=claim_type,
                    statement=statement,
                    evidence=valid_evidence,
                )
                l2_status, semantic_reason = _bound_behavior_validation_status(
                    validation=behavior_validation or {},
                    claim_id=claim_id,
                    binding=binding,
                )
                status = (
                    "verified"
                    if l2_status == "supports"
                    else "contradicted"
                    if l2_status == "contradicts"
                    else "insufficient"
                )
                validation_layer = "L2_independent_behavior"
            elif valid_evidence:
                status = (
                    "verified"
                    if semantic_status == "supported"
                    else "contradicted"
                    if semantic_status == "contradicted"
                    else "insufficient"
                )
            else:
                status = "contradicted" if has_verified_path else "insufficient"
            claim = {
                "claim_id": claim_id,
                "type": claim_type,
                "statement": statement,
                "status": status,
                "artifact": artifact,
                "row_id": row_id,
                "evidence": checked_evidence,
                "semantic_validation": semantic_status,
                "validation_layer": validation_layer,
            }
            if validation_layer == "L2_independent_behavior":
                claim["binding"] = _behavior_claim_binding(
                    claim_id=claim_id,
                    claim_type=claim_type,
                    statement=statement,
                    evidence=valid_evidence,
                )
            claims.append(claim)
            if status == "verified":
                continue
            code = (
                "source_claim_contradicted"
                if status == "contradicted"
                else "source_claim_insufficient"
            )
            reason = (
                semantic_reason
                if valid_evidence and semantic_status != "supported"
                else "引用路径已通过 SHA256 校验，但 quote 或 symbol 与源码不一致"
                if status == "contradicted"
                else "没有指向已通过 SHA256 校验的源码证据"
            )
            issues.append(
                _issue(
                    code,
                    artifact,
                    f"{row_id} 的技术断言 {claim_id} 未通过事实核验：{reason}。",
                    claim_id=claim_id,
                    claim_type=claim_type,
                    statement=statement,
                    evidence=checked_evidence,
                    row_id=row_id,
                    validation_layer=validation_layer,
                )
            )
    return claims, issues


def _deterministic_claim_semantics(
    *,
    claim_type: str,
    statement: str,
    evidence: list[dict[str, Any]],
) -> tuple[str, str]:
    """Verify closed-world claim types against the exact source literal.

    A real quote proves provenance, not entailment. Constants are deterministic:
    when a claim states a numeric value, that value must equal the value in the
    referenced define/enum assignment. Open-world behaviour claims require an
    independent, digest-bound L2 verdict; provenance alone never proves them.
    """
    constant_evidence = next(
        (
            item
            for item in evidence
            if _source_constant_value(str(item.get("quote") or "")) is not None
        ),
        None,
    )
    normalized_type = str(claim_type or "").strip().lower()
    if normalized_type == "source_anchor":
        quotes = [str(item.get("quote") or "").strip() for item in evidence]
        if not quotes:
            return "insufficient", "source_anchor 没有引用已验证源码行"
        if str(statement or "").strip() in quotes:
            return "supported", ""
        return "contradicted", "source_anchor statement 必须逐字等于已验证源码 quote"
    if constant_evidence is None and normalized_type not in {
        "protocol_constant",
        "macro_value",
        "enum_value",
        "field_offset",
        "log_literal",
    }:
        return "requires_l2", "行为断言需要独立模型结合完整源码上下文核验"
    if constant_evidence is None:
        return "insufficient", "protocol_constant 没有引用可解析的源码常量定义"

    source_value = _source_constant_value(str(constant_evidence.get("quote") or ""))
    claimed_values = _numeric_claim_values(statement)
    if not claimed_values:
        return "supported", ""
    if source_value in claimed_values:
        return "supported", ""
    return (
        "contradicted",
        "断言中的常量值与已验证源码定义不一致"
        f"（源码={_format_constant_value(source_value)}，断言={', '.join(_format_constant_value(value) for value in claimed_values)}）",
    )


def _audit_row_behavior_claims(
    *,
    artifact: str,
    rows: list[Any],
    verified_files: dict[str, dict[str, Any]],
    explicit_claims: list[dict[str, Any]],
    behavior_validation: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Summarize source-claim coverage for each SFMEA/test-case row.

    A row also contains test hypotheses, expected observations and remediation
    proposals.  Those are deliberately evaluated by the structural and
    executability gates, not re-submitted as one giant source-entailment
    sentence.  The latter made a correct source fact fail whenever its proposed
    effect or test oracle was necessarily not present in the implementation.
    """
    claims: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    claims_by_row: dict[str, list[dict[str, Any]]] = {}
    for claim in explicit_claims:
        if not isinstance(claim, dict):
            continue
        claims_by_row.setdefault(str(claim.get("row_id") or ""), []).append(claim)
    for row_index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        row_id = str(
            row.get("sfmea_id")
            or row.get("case_id")
            or row.get("risk_id")
            or row.get("id")
            or f"row-{row_index}"
        ).strip()
        row_claims = claims_by_row.get(row_id, [])
        explicit_behavior_claims = [
            claim
            for claim in row_claims
            if str(claim.get("type") or "").strip().lower()
            not in {
                "source_anchor",
                "protocol_constant",
                "macro_value",
                "enum_value",
                "field_offset",
                "log_literal",
            }
        ]
        behavior_claims = list(explicit_behavior_claims)
        # A literal source anchor establishes provenance only.  It must not
        # become a substitute for the behaviour assertion represented by the
        # user-visible SFMEA/test-case fields.  Without at least one explicit
        # behaviour assertion the independent auditor has no bounded claim to
        # verify, and an arbitrary auto-bound card can otherwise launder a
        # false protocol outcome into a green quality score.
        requires_explicit_behavior_assertion = True
        row_behavior_claims: list[dict[str, Any]] = []
        evidence = _row_behavior_evidence(
            row=row,
            verified_files=verified_files,
            explicit_claims=row_claims,
        )
        claim_id = f"ROW:{artifact}:{row_id}"
        # Each visible SFMEA or black-box row can make a source-behaviour
        # assertion, even when it is labelled as a test hypothesis.  A
        # hypothesis may propose a test tool or a measurement that is not in
        # source, but it cannot use that label to assert an unsupported result
        # such as a protocol status, missing guard, or resource leak.  The L2
        # auditor distinguishes those two cases from the compact row payload.
        requires_row_behavior = True
        if requires_row_behavior and evidence:
            row_claim_type = (
                "sfmea_row_behavior"
                if artifact == "sfmea.json"
                else "black_box_case_behavior"
            )
            row_statement = _row_behavior_statement(artifact=artifact, row=row)
            row_binding = _behavior_claim_binding(
                claim_id=claim_id,
                claim_type=row_claim_type,
                statement=row_statement,
                evidence=evidence,
            )
            l2_status, l2_reason = _bound_behavior_validation_status(
                validation=behavior_validation,
                claim_id=claim_id,
                binding=row_binding,
            )
            row_behavior_claims.append(
                {
                    "claim_id": claim_id,
                    "type": row_claim_type,
                    "status": (
                        "verified"
                        if l2_status == "supports"
                        else "contradicted"
                        if l2_status == "contradicts"
                        else "insufficient"
                    ),
                    "reason": l2_reason,
                    "binding": row_binding,
                }
            )
        behavior_claims.extend(row_behavior_claims)
        all_claims = [*row_claims, *row_behavior_claims]
        failed_claims = [claim for claim in all_claims if claim.get("status") != "verified"]
        if not row_claims or not evidence:
            status = "insufficient"
            reason = "该条目没有可供事实核验的技术断言或已验证源码证据"
        elif requires_explicit_behavior_assertion and not explicit_behavior_claims:
            status = "insufficient"
            reason = "该条目只有来源锚点，缺少可独立核验的行为断言"
        elif requires_row_behavior and not behavior_claims:
            status = "insufficient"
            reason = "该条目只有 L1 来源锚点，缺少独立核验的行为断言"
        elif any(claim.get("status") == "contradicted" for claim in failed_claims):
            status = "contradicted"
            reason = "该条目包含与源码矛盾的技术断言"
        elif failed_claims:
            status = "insufficient"
            reason = "该条目包含证据不足的技术断言"
        else:
            status = "verified"
            reason = "该条目的技术断言均已通过源码事实核验"
        statement = json.dumps(
            {
                "source_claim_ids": [
                    str(claim.get("claim_id") or "") for claim in row_claims
                ],
                "source_claim_status": {
                    str(claim.get("claim_id") or ""): str(claim.get("status") or "")
                    for claim in row_claims
                },
                "behavior_claim_ids": [
                    str(claim.get("claim_id") or "") for claim in behavior_claims
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        claim = {
            "claim_id": claim_id,
            "type": "row_source_claim_coverage",
            "statement": statement,
            "status": status,
            "artifact": artifact,
            "row_id": row_id,
            "evidence": evidence,
            "validation_layer": "aggregate_source_claims",
        }
        claims.append(claim)
        if status == "verified":
            continue
        issues.append(
            _issue(
                (
                    "row_source_claim_contradicted"
                    if status == "contradicted"
                    else "row_source_claim_insufficient"
                ),
                artifact,
                f"{row_id} 的源码事实覆盖未通过：{reason}。",
                claim_id=claim_id,
                claim_type="row_source_claim_coverage",
                row_id=row_id,
                statement=statement,
                evidence=evidence,
                validation_layer="aggregate_source_claims",
            )
        )
    return claims, issues


def _row_behavior_statement(*, artifact: str, row: dict[str, Any]) -> str:
    fields = (
        (
            "risk_status",
            "failure_mode",
            "cause",
            "effect",
            "detection",
            "mitigation",
            "test_mapping",
            "evidence_interpretation",
            "mechanism",
        )
        if artifact == "sfmea.json"
        else (
            "case_type",
            "test_dimension",
            "scenario_name",
            "preconditions",
            "steps",
            "expected_result",
            "oracle_basis",
            "observability",
            "failure_diagnostics",
            "mapped_test_dir",
        )
    )
    payload = {field: row.get(field) for field in fields if field in row}
    if artifact == "black_box_cases.json":
        # External steps, oracles and observability describe a test contract;
        # they are not claims that the product already implements an RSS probe,
        # fio harness, or every measurement tool named by the tester.  Producers
        # may explicitly elevate another case type, but the safe default keeps
        # ordinary source-driven black-box designs in the hypothesis lane.
        payload["case_type"] = str(
            row.get("case_type") or "black_box_hypothesis"
        ).strip() or "black_box_hypothesis"
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _row_behavior_evidence(
    *,
    row: dict[str, Any],
    verified_files: dict[str, dict[str, Any]],
    explicit_claims: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def append(item: dict[str, Any]) -> None:
        if not isinstance(item, dict):
            return
        path = str(item.get("path") or "")
        quote = str(item.get("quote") or "")
        evidence_id = str(item.get("evidence_id") or "")
        key = (path, evidence_id, quote)
        if not path or key in seen:
            return
        seen.add(key)
        evidence.append(
            {
                "evidence_id": evidence_id,
                "path": path,
                "symbol": str(item.get("symbol") or ""),
                "lines": str(item.get("lines") or ""),
                "quote": quote,
                "sha256": str(item.get("sha256") or ""),
            }
        )

    for claim in explicit_claims:
        for item in claim.get("evidence") or []:
            if isinstance(item, dict) and item.get("quote_matches") is not False:
                append(item)
    raw_paths = [
        *(row.get("source_evidence") or []),
        *(row.get("source_or_test_evidence") or []),
    ]
    for path in _structured_source_evidence_paths(raw_paths):
        metadata = verified_files.get(path)
        if metadata:
            append({"path": path, "sha256": str(metadata.get("sha256") or "")})
    return evidence


def build_behavior_claim_validation_request(
    *,
    artifact_dir: str | Path,
    repo_path: str | Path,
    max_claims: int = 64,
    context_chars: int = 6000,
) -> dict[str, Any]:
    """Build a compact, source-backed request for the independent L2 auditor."""
    root = Path(artifact_dir)
    repo = Path(repo_path)
    verified_files = _verified_evidence_files(root=root, repo=repo)
    candidates: list[dict[str, Any]] = []
    for artifact in ("sfmea.json", "black_box_cases.json"):
        rows = _read_json(_artifact_path(root, artifact))
        if not isinstance(rows, list):
            continue
        explicit, _ = _audit_explicit_technical_claims(
            artifact=artifact,
            rows=rows,
            verified_files=verified_files,
            behavior_validation={},
            require_behavior_validation=True,
        )
        candidates.extend(
            claim
            for claim in explicit
            if claim.get("validation_layer") == "L2_independent_behavior"
        )
        # Exact source anchors establish only provenance.  The user-visible
        # SFMEA/case fields can still make a broader, false assertion about
        # behavior.  Submit that row-level assertion to L2 even when every
        # explicit claim has been normalized to a deterministic source anchor.
        claims_by_row: dict[str, list[dict[str, Any]]] = {}
        for claim in explicit:
            if isinstance(claim, dict):
                claims_by_row.setdefault(str(claim.get("row_id") or ""), []).append(claim)
        row_id_key = "sfmea_id" if artifact == "sfmea.json" else "case_id"
        row_claim_type = (
            "sfmea_row_behavior"
            if artifact == "sfmea.json"
            else "black_box_case_behavior"
        )
        for row_index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                continue
            row_id = str(row.get(row_id_key) or f"row-{row_index}").strip()
            if not row_id:
                continue
            # A black-box contract may name public tools and measurements that
            # are not implemented by the repository, but a source-bound row
            # still asserts the expected product behaviour.  Submit that
            # behaviour to L2; the auditor is instructed not to reject the
            # test tooling merely because it is external.
            if artifact == "black_box_cases.json" and not claims_by_row.get(row_id):
                continue
            evidence = _row_behavior_evidence(
                row=row,
                verified_files=verified_files,
                explicit_claims=claims_by_row.get(row_id, []),
            )
            if not evidence:
                continue
            statement = _row_behavior_statement(artifact=artifact, row=row)
            candidates.append(
                {
                    "claim_id": f"ROW:{artifact}:{row_id}",
                    "type": row_claim_type,
                    "artifact": artifact,
                    "row_id": row_id,
                    "statement": statement,
                    "evidence": evidence,
                    "binding": _behavior_claim_binding(
                        claim_id=f"ROW:{artifact}:{row_id}",
                        claim_type=row_claim_type,
                        statement=statement,
                        evidence=evidence,
                    ),
                    "validation_layer": "L2_independent_behavior",
                }
            )

    contexts: list[dict[str, Any]] = []
    context_index: dict[tuple[str, int, int, str], str] = {}
    request_claims: list[dict[str, Any]] = []
    claim_limit = max(1, int(max_claims))
    selected_candidates = candidates[:claim_limit]
    for claim in selected_candidates:
        context_ids: list[str] = []
        for evidence in claim.get("evidence") or []:
            if not isinstance(evidence, dict):
                continue
            source_context = _expanded_behavior_source_context(
                evidence=evidence,
                verified_files=verified_files,
                context_chars=max(1000, int(context_chars)),
            )
            if not source_context:
                continue
            key = (
                str(source_context["path"]),
                int(source_context["start_line"]),
                int(source_context["end_line"]),
                str(source_context["sha256"]),
            )
            context_id = context_index.get(key)
            if context_id is None:
                context_id = f"CTX-{len(contexts) + 1:03d}"
                context_index[key] = context_id
                contexts.append({"context_id": context_id, **source_context})
            if context_id not in context_ids:
                context_ids.append(context_id)
        request_claims.append(
            {
                "claim_id": str(claim.get("claim_id") or ""),
                "type": str(claim.get("type") or ""),
                "artifact": str(claim.get("artifact") or ""),
                "row_id": str(claim.get("row_id") or ""),
                "statement": str(claim.get("statement") or ""),
                "binding": str(claim.get("binding") or ""),
                "context_ids": context_ids,
                # Keep declaration ownership alongside the compact source window.
                # The window is intentionally bounded, so a static function's
                # signature can otherwise sit immediately above it.
                "evidence_bindings": [
                    {
                        "path": str(evidence.get("path") or ""),
                        "symbol": str(evidence.get("symbol") or ""),
                        "lines": str(evidence.get("lines") or ""),
                        "quote": str(evidence.get("quote") or ""),
                    }
                    for evidence in claim.get("evidence") or []
                    if isinstance(evidence, dict)
                ],
            }
        )
    payload = {
        "kind": "behavior_claim_validation_request",
        "schema_version": 2,
        "repo_path": str(repo.resolve()) if repo.exists() else str(repo),
        "claims": request_claims,
        "contexts": contexts,
        "candidate_count": len(candidates),
        "requested_count": len(request_claims),
        "truncated": len(candidates) > len(request_claims),
    }
    payload["request_sha256"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return payload


def _expanded_behavior_source_context(
    *,
    evidence: dict[str, Any],
    verified_files: dict[str, dict[str, Any]],
    context_chars: int,
) -> dict[str, Any] | None:
    path = str(evidence.get("path") or "")
    metadata = verified_files.get(path)
    if not metadata:
        return None
    content = str(metadata.get("content") or "")
    lines = content.splitlines()
    if not lines:
        return None
    # Evidence cards serialize ranges as either `L120-L122` or `120-122`.
    # Treat the first numeric line as the anchor; otherwise an L2 audit can
    # fall back to an unrelated earlier symbol declaration in the same file.
    line_match = re.search(r"\d+", str(evidence.get("lines") or ""))
    anchor = int(line_match.group(0)) if line_match else 0
    if anchor <= 0:
        symbol = str(evidence.get("symbol") or "").strip()
        quote = str(evidence.get("quote") or "").strip()
        needle = symbol or quote
        if needle:
            anchor = next(
                (index for index, line in enumerate(lines, start=1) if needle in line),
                1,
            )
        else:
            anchor = 1
    start = max(1, anchor - 45)
    end = min(len(lines), anchor + 75)
    numbered = "\n".join(
        f"{line_number:06d}: {lines[line_number - 1]}"
        for line_number in range(start, end + 1)
    )
    if len(numbered) > context_chars:
        numbered = numbered[:context_chars]
        kept_lines = numbered.count("\n") + 1
        end = min(end, start + kept_lines - 1)
    return {
        "path": path,
        "start_line": start,
        "end_line": end,
        "sha256": str(metadata.get("sha256") or ""),
        "content": numbered,
    }


def _behavior_claim_binding(
    *,
    claim_id: str,
    claim_type: str,
    statement: str,
    evidence: list[dict[str, Any]],
) -> str:
    """Bind an L2 verdict to the exact claim text and verified source bytes."""
    normalized_evidence = [
        {
            "evidence_id": str(item.get("evidence_id") or ""),
            "path": str(item.get("path") or ""),
            "symbol": str(item.get("symbol") or ""),
            "lines": str(item.get("lines") or ""),
            "quote": str(item.get("quote") or ""),
            "sha256": str(item.get("sha256") or ""),
        }
        for item in evidence
        if isinstance(item, dict)
    ]
    payload = {
        "claim_id": str(claim_id or "").strip(),
        "type": str(claim_type or "").strip(),
        "statement": str(statement or "").strip(),
        "evidence": normalized_evidence,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _bound_behavior_validation_status(
    *,
    validation: dict[str, Any],
    claim_id: str,
    binding: str,
) -> tuple[str, str]:
    status, reason, _ = _bound_behavior_validation_details(
        validation=validation,
        claim_id=claim_id,
        binding=binding,
    )
    return status, reason


def _bound_behavior_validation_details(
    *,
    validation: dict[str, Any],
    claim_id: str,
    binding: str,
) -> tuple[str, str, dict[str, Any]]:
    validator = (
        validation.get("validator")
        if isinstance(validation.get("validator"), dict)
        else {}
    )
    if not bool(validator.get("independent")):
        return "insufficient", "缺少独立行为审计器的核验结果", {}
    matched_claim_id = False
    for item in validation.get("claims") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("claim_id") or "").strip() != claim_id:
            continue
        matched_claim_id = True
        if str(item.get("binding") or "").strip() != binding:
            continue
        status = str(item.get("status") or "").strip().lower()
        reason = str(item.get("reason") or "").strip()
        if status in {"supports", "contradicts", "insufficient"}:
            field_patch = (
                dict(item.get("field_patch") or {})
                if isinstance(item.get("field_patch"), dict)
                else {}
            )
            return status, reason or f"独立行为审计结果：{status}", field_patch
        return "insufficient", "独立行为审计返回了未知状态", {}
    if matched_claim_id:
        return "insufficient", "行为审计结果与当前断言或源码证据不匹配", {}
    return "insufficient", "当前行为断言尚未经过独立模型核验", {}


def _source_constant_value(quote: str) -> int | None:
    match = re.match(
        r"^\s*(?:#\s*define\s+[A-Za-z_][A-Za-z0-9_]*|[A-Za-z_][A-Za-z0-9_]*\s*=)\s+"
        r"(0[xX][0-9A-Fa-f]+|\d+)\b",
        str(quote or ""),
    )
    if not match:
        return None
    return int(match.group(1), 0)


def _numeric_claim_values(statement: str) -> list[int]:
    values: list[int] = []
    for match in re.finditer(r"(?<![A-Za-z0-9_])(0[xX][0-9A-Fa-f]+|\d+)(?![A-Za-z0-9_])", statement):
        value = int(match.group(1), 0)
        if value not in values:
            values.append(value)
    return values


def _format_constant_value(value: int | None) -> str:
    return "unknown" if value is None else f"0x{value:X}"


def _verified_c_constant_ledger(
    *,
    root: Path,
    repo: Path,
) -> dict[str, dict[str, Any]]:
    allowed_files = _verified_evidence_files(root=root, repo=repo)

    ledger: dict[str, dict[str, Any]] = {}
    patterns = (
        re.compile(r"^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s+([^\s/]+)"),
        re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^,\s/]+)\s*,?"),
    )
    for relative, metadata in allowed_files.items():
        text = str(metadata.get("content") or "")
        for line_number, source_line in enumerate(text.splitlines(), start=1):
            for pattern in patterns:
                match = pattern.match(source_line)
                if not match:
                    continue
                raw_value = match.group(2).rstrip(";,)uUlL")
                integer_value = _parse_c_integer_literal(raw_value)
                if integer_value is None:
                    break
                ledger[match.group(1)] = {
                    "raw_value": raw_value,
                    "integer_value": integer_value,
                    "file_path": relative,
                    "line": line_number,
                    "source_line": source_line.strip(),
                    "sha256": metadata["sha256"],
                }
                break
    return ledger


def _verified_evidence_files(
    *,
    root: Path,
    repo: Path,
) -> dict[str, dict[str, Any]]:
    evidence_cards = _read_json(_artifact_path(root, "evidence_cards.json"))
    if not isinstance(evidence_cards, list) or not repo.is_dir():
        return {}
    allowed_files: dict[str, dict[str, Any]] = {}
    for card in evidence_cards:
        if not isinstance(card, dict):
            continue
        relative = str(card.get("file_path") or "").replace("\\", "/").lstrip("/")
        if not relative or ".." in Path(relative).parts:
            continue
        path = repo / relative
        if not path.is_file():
            continue
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        expected_digest = str(card.get("sha256") or "")
        if expected_digest and expected_digest != digest:
            continue
        content = raw.decode("utf-8", errors="replace")
        metadata = allowed_files.setdefault(
            relative,
            {
                "path": path,
                "sha256": digest,
                "content": content,
                "evidence_anchors": {},
                "evidence_cards": {},
            },
        )
        card_id = str(card.get("evidence_id") or "").strip()
        start_line = int(card.get("start_line") or 0)
        end_line = int(card.get("end_line") or 0)
        if card_id and start_line > 0 and end_line >= start_line:
            source_lines = content.splitlines()
            bounded_end = min(end_line, len(source_lines))
            source_excerpt = "\n".join(source_lines[start_line - 1 : bounded_end])
            declared_excerpt = str(card.get("excerpt") or "").strip("\n")
            # Flow discovery preserves the code tokens and line range but can
            # intentionally omit leading indentation from a one-line call
            # edge. Treat indentation-only differences as equivalent after
            # the file SHA256 and every referenced line have been verified.
            # Any token, order, or line-range difference still fails closed.
            normalized_declared = "\n".join(
                line.strip() for line in declared_excerpt.splitlines()
            )
            normalized_source = "\n".join(
                line.strip() for line in source_excerpt.strip("\n").splitlines()
            )
            if bounded_end != end_line or normalized_declared != normalized_source:
                continue
            metadata["evidence_cards"][card_id] = {
                "path": relative,
                "start_line": start_line,
                "end_line": end_line,
                "excerpt": source_excerpt,
            }
            anchors = metadata["evidence_anchors"]
            for line_number in range(start_line, bounded_end + 1):
                quote = source_lines[line_number - 1].strip()
                if not quote:
                    continue
                anchors[f"{card_id}:L{line_number}"] = {
                    "path": relative,
                    "lines": f"L{line_number}",
                    "quote": quote,
                }
    return allowed_files


def _structured_source_evidence_paths(
    value: Any,
    *,
    verified_files: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    paths: list[str] = []
    for item in value if isinstance(value, list) else []:
        text = str(item or "").strip().replace("\\", "/")
        match = re.match(r"^([^:]+(?:/[^:]+)+?)(?:::{1}|:\d|\s|$)", text)
        if match:
            paths.append(match.group(1).strip())
            continue
        if not verified_files:
            continue
        card_id = text.split(":L", 1)[0]
        for path, metadata in verified_files.items():
            anchors = (
                metadata.get("evidence_anchors")
                if isinstance(metadata.get("evidence_anchors"), dict)
                else {}
            )
            if text in anchors or any(
                str(anchor_id).startswith(f"{card_id}:L")
                for anchor_id in anchors
            ):
                paths.append(path)
    return list(dict.fromkeys(paths))


def _extract_exact_log_literals(text: str) -> list[tuple[str, bool]]:
    """Extract only literals locally asserted to be exact source log text.

    Detection fields often mix log guidance, packet filters, expected wire values,
    and prose abbreviations. Treating every quoted token in such a field as a log
    claim creates false contradictions and makes the repair loop optimize wording
    instead of facts.
    """
    literals: list[tuple[str, bool]] = []
    for match in re.finditer(r"(['\"])([^'\"\n]{4,160})\1", text):
        literal = match.group(2).strip()
        before = text[max(0, match.start() - 96) : match.start()]
        after = text[match.end() : min(len(text), match.end() + 64)]
        local_context = f"{before} {after}"
        asserted_as_log = bool(
            re.search(
                r"(?:\b(?:spdk\s+)?log\b|日志(?:原文)?|"
                r"(?:exact\s+)?format\s+string|格式(?:串|字符串))"
                r"[^'\"\n]{0,48}$",
                before,
                flags=re.IGNORECASE,
            )
        )
        negated = bool(
            re.search(
                r"^\s*(?:is|was|will\s+be)?\s*(?:not|never)\s+(?:logged|emitted|printed)"
                r"|^\s*(?:不会|未|不)(?:被)?(?:记录|输出|打印)",
                after,
                flags=re.IGNORECASE,
            )
            or re.search(
                r"(?:\bno\s+(?:explicit\s+)?(?:spdk\s+)?log\b|"
                r"\b(?:spdk\s+)?log\b[^'\"\n]{0,32}\bno\s+explicit\b|"
                r"(?:没有|无|未见)(?:明确|显式)?[^'\"\n]{0,20}日志)",
                before[-80:],
                flags=re.IGNORECASE,
            )
        )
        placeholder = bool(re.search(r"\.\.\.|…", literal))
        packet_filter = bool(
            re.search(r"\b(?:tshark|tcpdump|filter)\b", before[-48:], flags=re.IGNORECASE)
            or re.search(r"(?:==|&&|\|\|)", literal)
        )
        if (
            not literal
            or not asserted_as_log
            or negated
            or placeholder
            or packet_filter
            or re.fullmatch(r"0x[0-9a-fA-F]+", literal)
        ):
            continue
        # Only treat an uncertainty marker attached to this literal as a gap.
        # A separate speculative phrase elsewhere in the field must not make a
        # verified log literal disappear from the fact ledger.
        uncertainty_context = f"{before[-32:]} {after[:48]}"
        uncertain = bool(
            re.search(
                r"待验证|需核验|尚未确认|\bunverified\b",
                uncertainty_context,
                flags=re.IGNORECASE,
            )
        )
        literals.append((literal, uncertain))
    return literals


def _parse_c_integer_literal(value: str) -> int | None:
    normalized = str(value or "").strip().rstrip("uUlL")
    if not re.fullmatch(r"(?:0[xX][0-9a-fA-F]+|\d+)", normalized):
        return None
    return int(normalized, 16 if normalized.lower().startswith("0x") else 10)


def _extract_iscsi_version_range(statement: str) -> tuple[int, int] | None:
    maximum = re.search(
        r"version[_ -]?max\s*=\s*(0[xX][0-9a-fA-F]+|\d+)", statement, flags=re.IGNORECASE
    )
    minimum = re.search(
        r"version[_ -]?min\s*=\s*(0[xX][0-9a-fA-F]+|\d+)", statement, flags=re.IGNORECASE
    )
    if not maximum or not minimum:
        return None
    parsed_maximum = _parse_c_integer_literal(maximum.group(1))
    parsed_minimum = _parse_c_integer_literal(minimum.group(1))
    if parsed_maximum is None or parsed_minimum is None:
        return None
    return parsed_maximum, parsed_minimum


def _is_combined_test_report_spec(spec: dict[str, Any]) -> bool:
    sections = {str(item).strip() for item in spec.get("sections") or []}
    return {
        "主流程与异常/恢复流程",
        "SFMEA",
        "黑盒测试用例",
    }.issubset(sections)


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

    flow_artifact = (
        "flow_map.md"
        if "flow_map.md" in required_outputs
        else "business_flow.md"
    )
    if required_outputs & {"business_flow.md", "flow_map.md"}:
        numbered_steps = re.findall(r"(?m)^\s*(?:\d+[.)]|步骤\s*\d+)\s*", text)
        named_flows = re.findall(
            r"(?mi)^\s*#{2,6}\s*(?:流程|flow)\s*(?:[a-z]|\d+|[一二三四五六七八九十]+)\s*[:：.)、-]",
            text,
        )
        structured_flow_steps: list[str] = []
        for section in re.findall(
            r"(?mis)^\s*#{2,6}\s*[^\n]*(?:流程步骤|flow steps?)[^\n]*\n(.*?)(?=^\s*#{2,6}\s|\Z)",
            text,
        ):
            structured_flow_steps.extend(
                re.findall(r"(?m)^\s*(?:[*+-]\s+|\d+[.)]\s+)(?:\*\*)?\S+", section)
            )
        has_flow_marker = any(marker in lower for marker in ("流程", "flow", "状态迁移"))
        has_failure_or_recovery = any(
            marker in lower
            for marker in ("异常", "失败", "恢复", "清理", "error", "failure", "recovery")
        )
        if (
            not has_flow_marker
            or max(len(numbered_steps), len(named_flows), len(structured_flow_steps)) < 3
            or not has_failure_or_recovery
        ):
            issues.append(
                _issue(
                    "missing_combined_business_flow",
                    flow_artifact,
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
    proposed_paths = {
        path for path in evidence_paths if _is_labeled_unverified_proposal(text, path)
    }
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
            if path not in proposed_paths
            and not _repo_path_exists(repo, path)
            and not _looks_like_runtime_generated_path(path)
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
            if path not in proposed_paths
            and _evidence_path_classification(path) == "test"
            and Path(path).suffix
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
    issues.extend(_audit_combined_professional_completeness(text, contract))

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


def _audit_combined_sfmea_order(content: str) -> list[dict[str, Any]]:
    for block in re.findall(r"```json\s*(\[.*?\])\s*```", content, flags=re.IGNORECASE | re.DOTALL):
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, list) or not payload:
            continue
        rows = [row for row in payload if isinstance(row, dict) and "failure_mode" in row]
        if len(rows) < 2:
            continue
        rpns = [_integer_score(row.get("rpn")) for row in rows]
        if any(rpn is None for rpn in rpns):
            continue
        violations = [
            index + 1
            for index in range(len(rpns) - 1)
            if int(rpns[index + 1]) > int(rpns[index])
        ]
        if violations:
            return [
                _issue(
                    "sfmea_not_sorted_by_rpn",
                    "sfmea.json",
                    "SFMEA 必须按 RPN 降序排列，保证风险执行优先级与表格顺序一致",
                    positions=violations[:20],
                )
            ]
        return []
    return []


def _audit_combined_execution_contract(content: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    json_blocks = re.findall(r"```json\s*\n([\s\S]*?)```", content, flags=re.IGNORECASE)
    parsed_json_blocks: list[Any] = []
    for index, block in enumerate(json_blocks, start=1):
        try:
            parsed_json_blocks.append(json.loads(block))
        except json.JSONDecodeError as exc:
            issues.append(
                _issue(
                    "invalid_fenced_json",
                    "assistant-output.md",
                    f"第 {index} 个 JSON 交付块无法解析: 第 {exc.lineno} 行第 {exc.colno} 列 {exc.msg}",
                    block_index=index,
                    line=exc.lineno,
                    column=exc.colno,
                )
            )

    profile_refs: set[str] = set()
    for payload in parsed_json_blocks:
        if not isinstance(payload, list):
            continue
        for row in payload:
            if not isinstance(row, dict) or "case_id" not in row:
                continue
            profile_refs.update(
                re.findall(
                    r"\bP\d+_[A-Z][A-Z0-9_]+\b",
                    json.dumps(row, ensure_ascii=False),
                )
            )
    profile_definitions = set(
        re.findall(r"^\s*\|\s*`?(P\d+_[A-Z][A-Z0-9_]+)`?\s*\|", content, flags=re.MULTILINE)
    )
    undefined_profiles = sorted(profile_refs - profile_definitions)
    if undefined_profiles:
        issues.append(
            _issue(
                "undefined_execution_profile",
                "test_design.md",
                "测试用例引用了未定义的执行 profile: " + "、".join(undefined_profiles),
                profiles=undefined_profiles,
            )
        )

    incomplete_profiles: list[str] = []
    for profile in (
        "P1_DISCOVERY_CHAP",
        "P2_DISCOVERY_MUTUAL",
        "P3_NORMAL_CHAP",
        "P4_NORMAL_MUTUAL",
    ):
        if profile not in profile_definitions:
            continue
        row_match = re.search(rf"^\s*\|[^\n]*{re.escape(profile)}[^\n]*$", content, flags=re.MULTILINE)
        row = row_match.group(0) if row_match else ""
        auth_requirements = ("iscsi_create_auth_group", "iscsi_auth_group_add_secret")
        profile_requirements = (
            (
                "--wait-for-rpc",
                "iscsi_set_options",
                "framework_start_init",
                *auth_requirements,
                "iscsi_set_discovery_auth",
            )
            if profile.startswith("P1_") or profile.startswith("P2_")
            else (*auth_requirements, "iscsi_target_node_set_auth")
        )
        if not all(marker in row for marker in profile_requirements):
            incomplete_profiles.append(profile)
    if incomplete_profiles:
        issues.append(
            _issue(
                "incomplete_execution_profile",
                "test_design.md",
                "CHAP profile 必须自包含 auth group 创建、secret 配置和 target 绑定: "
                + "、".join(incomplete_profiles),
                profiles=incomplete_profiles,
            )
        )

    non_executable_discovery_profiles: list[str] = []
    for profile in ("P1_DISCOVERY_CHAP", "P2_DISCOVERY_MUTUAL"):
        if profile not in profile_definitions:
            continue
        row_match = re.search(rf"^\s*\|[^\n]*{re.escape(profile)}[^\n]*$", content, flags=re.MULTILINE)
        row = row_match.group(0) if row_match else ""
        uses_spdk_helpers = all(
            marker in row
            for marker in (
                "config_chap_credentials_for_initiator",
                "default_initiator_chap_credentials",
            )
        )
        uses_explicit_iscsid_commands = bool(
            re.search(r"sed\s+-i.{0,240}discovery\.sendtargets\.auth", row, flags=re.IGNORECASE)
            and re.search(r"(?:restart_iscsid|systemctl\s+restart\s+iscsid)", row, flags=re.IGNORECASE)
            and re.search(r"(?:restore|cleanup|清理|恢复)", row, flags=re.IGNORECASE)
        )
        runs_discovery = bool(re.search(r"iscsiadm\s+-m\s+discovery", row, flags=re.IGNORECASE))
        if not runs_discovery or not (uses_spdk_helpers or uses_explicit_iscsid_commands):
            non_executable_discovery_profiles.append(profile)
    if non_executable_discovery_profiles:
        issues.append(
            _issue(
                "non_executable_discovery_credentials",
                "test_design.md",
                "Discovery CHAP profile 必须给出可复制的 initiator 凭据配置、iscsid 生效、discovery 命令和凭据恢复步骤: "
                + "、".join(non_executable_discovery_profiles),
                profiles=non_executable_discovery_profiles,
            )
        )

    incomplete_mcs_cases: list[str] = []
    for payload in parsed_json_blocks:
        if not isinstance(payload, list):
            continue
        for row in payload:
            if not isinstance(row, dict) or "case_id" not in row:
                continue
            row_text = json.dumps(row, ensure_ascii=False)
            if not (
                str(row.get("case_id") or "").upper() == "BB_032"
                or re.search(r"MCS\s+append", row_text, flags=re.IGNORECASE)
            ):
                continue
            has_nonzero_tsih = bool(
                re.search(
                    r"(?:非零|nonzero|positive|>\s*0).{0,24}TSIH|TSIH.{0,24}(?:非零|nonzero|positive|>\s*0)",
                    row_text,
                    flags=re.IGNORECASE,
                )
            )
            keeps_old_socket = bool(
                re.search(
                    r"(?:旧|old).{0,32}(?:socket|连接).{0,64}(?:可用|存活|open|live|非\s*EOF|not\s+EOF)",
                    row_text,
                    flags=re.IGNORECASE,
                )
            )
            proves_rpc_pair = (
                "iscsi_get_connections" in row_text
                and bool(re.search(r"(?:同一|same).{0,24}TSIH", row_text, flags=re.IGNORECASE))
                and bool(re.search(r"(?:旧|old).{0,24}(?:新|new).{0,24}CID", row_text, flags=re.IGNORECASE))
            )
            if not (has_nonzero_tsih and keeps_old_socket and proves_rpc_pair):
                incomplete_mcs_cases.append(str(row.get("case_id") or "MCS append"))
    if incomplete_mcs_cases:
        issues.append(
            _issue(
                "incomplete_mcs_black_box_oracle",
                "black_box_cases.json",
                "MCS append 黑盒用例自身必须写明首个 TSIH 非零、旧 socket 保持可用、"
                "iscsi_get_connections 中同一 TSIH 下旧/新 CID 共存: "
                + "、".join(incomplete_mcs_cases),
                cases=incomplete_mcs_cases,
            )
        )

    lines = content.splitlines()
    for index, line in enumerate(lines[:-1]):
        if not re.match(r"^\s*\|\s*Profile\s*\|", line, flags=re.IGNORECASE):
            continue
        separator = lines[index + 1].strip()
        header_columns = [cell for cell in line.strip().strip("|").split("|")]
        separator_columns = [cell.strip() for cell in separator.strip().strip("|").split("|")]
        valid_separator = (
            separator.startswith("|")
            and separator.endswith("|")
            and len(separator_columns) == len(header_columns)
            and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator_columns)
        )
        if not valid_separator:
            issues.append(
                _issue(
                    "invalid_profile_table",
                    "test_design.md",
                    "Profile Markdown 表头分隔行无效或列数不一致，下载后无法正常渲染",
                )
            )
        break

    lower = content.lower()
    if ("mcs-capacity-limit" in lower or "maxconnections" in lower) and not re.search(
        r"iscsi_set_options[^\n]{0,160}(?:\s-c\s+\d+|max_connections_per_session)",
        content,
        flags=re.IGNORECASE,
    ):
        issues.append(
            _issue(
                "missing_max_connections_target_setup",
                "test_design.md",
                "MCS 容量用例必须在 target 启动前给出可执行命令 `scripts/rpc.py iscsi_set_options -c 1`；客户端 probe 参数或 `-c MaxConnectionsPerSession=1` 不能替代该启动期设置",
            )
        )

    registered_cases: set[str] = set()
    for block in re.findall(r"```python\s*\n([\s\S]*?)```", content, flags=re.IGNORECASE):
        try:
            tree = ast.parse(block)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(isinstance(target, ast.Name) and target.id == "CASES" for target in targets):
                continue
            value = node.value
            if isinstance(value, ast.Dict):
                registered_cases.update(
                    key.value for key in value.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)
                )
    claimed_cases: set[str] = set()
    for payload in parsed_json_blocks:
        if not isinstance(payload, list):
            continue
        for row in payload:
            if not isinstance(row, dict) or "case_id" not in row:
                continue
            mapping = str(row.get("mapped_test_dir") or "")
            claimed_cases.update(
                re.findall(r"Raw-PDU\s+Harness\s+([a-z][a-z0-9-]+)", mapping, flags=re.IGNORECASE)
            )
    missing_cases = sorted(case for case in claimed_cases if case not in registered_cases)
    if missing_cases:
        issues.append(
            _issue(
                "harness_case_not_registered",
                "black_box_cases.json",
                "黑盒用例声称由 Raw-PDU Harness 覆盖，但 CASES 未注册: " + "、".join(missing_cases),
                cases=missing_cases,
            )
        )
    issues.extend(_audit_raw_pdu_scenario_capabilities(content))
    return issues


def _audit_raw_pdu_runtime_evidence(*, root: Path, content: str) -> list[dict[str, Any]]:
    """Require runtime proof for deterministic staged raw-PDU deliverables."""
    if not (root / "staged_execution_result.json").is_file():
        return []
    if not re.search(
        r"raw[-_ ]?pdu|原始\s*pdu|\bt\s*\+\s*c\b|c-bit|c\s*位",
        content,
        flags=re.IGNORECASE,
    ):
        return []
    validation = _read_json(root / "raw_pdu_harness_validation.json")
    if (
        isinstance(validation, dict)
        and validation.get("status") == "passed"
        and validation.get("validation_scope") == "spdk_target_integration"
        and validation.get("target_kind") == "spdk_iscsi_target"
        and isinstance(validation.get("target_identity"), dict)
    ):
        checks = {str(item) for item in validation.get("checks") or []}
        required = {
            "tcp_connect",
            "first_pdu_sendall",
            "login_response_recv",
            "status_oracle",
        }
        if required.issubset(checks):
            return []
    return [
        _issue(
            "raw_pdu_runtime_validation_failed",
            "black_box_cases.json",
            "raw-PDU harness 缺少连接真实 SPDK iSCSI target 的验证证据；本地回环自检只能证明 harness 本身，不能标记为可执行。",
            validation_layer="L3_executable",
            validation=validation if isinstance(validation, dict) else {},
        )
    ]


def _audit_raw_pdu_scenario_capabilities(content: str) -> list[dict[str, Any]]:
    """Validate claimed raw-PDU scenarios against executable AST capabilities.

    Text identifies which scenario a report claims. Capability is established only
    by the embedded Python program's data flow and assertions, never by prose.
    """
    case_blocks = _combined_black_box_case_blocks(content)
    mcs_claims = [
        heading
        for heading, body in case_blocks
        if _is_mcs_case_contract(f"{heading}\n{body}")
    ]
    continuation_claims = [
        heading
        for heading, body in case_blocks
        if re.search(
            r"\bt\s*\+\s*c\b|c[- ]?bit|c\s*=\s*1|continuation|分片",
            f"{heading}\n{body}",
            flags=re.IGNORECASE,
        )
    ]
    version_claims = [
        heading
        for heading, body in case_blocks
        if re.search(
            r"unsupported\s+version|version[_ -]?(?:max|min)|不支持.{0,12}版本",
            f"{heading}\n{body}",
            flags=re.IGNORECASE,
        )
    ]
    if not (mcs_claims or continuation_claims or version_claims):
        return []

    trees = _embedded_python_trees(content)
    capability_labels = {
        "nonzero_tsih_input": "支持把首个响应中的非零 TSIH 写入第二个 Login Request",
        "response_tsih_capture": "从首个 Login Response 解析并保存 TSIH",
        "dual_socket_lifecycle": "在第二个连接完成判定前保持首个 socket 在线",
        "distinct_cid_input": "第二个连接使用可配置且不同的 CID",
        "login_response_status_oracle": "解析并断言 Login Response 的 Status-Class/Status-Detail",
        "mutable_login_flags": "允许场景设置 Login flags，而不是固定写死一个 flags 字节",
        "multi_pdu_login": "在同一连接上实际发送并接收多段 Login PDU",
        "version_range_input": "允许场景分别设置 Version-max 与 Version-min 字节",
    }
    required: set[str] = {"login_response_status_oracle"}
    if mcs_claims:
        required.update(
            {
                "nonzero_tsih_input",
                "response_tsih_capture",
                "dual_socket_lifecycle",
                "distinct_cid_input",
            }
        )
    if continuation_claims:
        required.update({"mutable_login_flags", "multi_pdu_login"})
    if version_claims:
        required.add("version_range_input")
    executable_capability_sets = _raw_pdu_executable_capability_sets(trees)
    capabilities = max(
        executable_capability_sets,
        key=lambda item: len(required & item),
        default=set(),
    )
    missing = sorted(required - capabilities)
    if not missing:
        return []
    scenarios = [*mcs_claims, *continuation_claims, *version_claims]
    scenario_label = "MCS" if mcs_claims and not (continuation_claims or version_claims) else "raw-PDU"
    return [
        _issue(
            "raw_pdu_harness_missing_scenario_capability",
            "black_box_cases.json",
            f"报告声明了 {scenario_label} 测试，但内嵌脚本无法执行对应场景，缺少: "
            + "、".join(capability_labels[key] for key in missing),
            scenarios=list(dict.fromkeys(scenarios)),
            missing_capabilities=missing,
            validation_layer="L3_executable",
        )
    ]


def _embedded_python_trees(content: str) -> list[ast.Module]:
    trees: list[ast.Module] = []
    for source in re.findall(r"```python\s*\n([\s\S]*?)```", content, flags=re.IGNORECASE):
        try:
            trees.append(ast.parse(source))
        except SyntaxError:
            continue
    return trees


def _raw_pdu_executable_capability_sets(trees: list[ast.Module]) -> list[set[str]]:
    capability_sets: list[set[str]] = []
    for tree in trees:
        functions = {
            node.name.lower(): node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if not functions:
            continue
        top_level_names = set(functions)
        calls = {
            name: _ast_direct_function_calls(
                function,
                top_level_names - _ast_local_bound_names(function),
            )
            for name, function in functions.items()
        }
        roots = _ast_module_entrypoint_calls(tree, set(functions))
        if "main" in functions:
            roots.add("main")
        if not roots:
            continue
        reachable = set(roots)
        pending = list(roots)
        while pending:
            current = pending.pop()
            for called in calls.get(current, set()):
                if called not in reachable:
                    reachable.add(called)
                    pending.append(called)
        executable_tree = ast.Module(
            body=[
                _ast_without_nested_dead_scopes(functions[name])
                for name in functions
                if name in reachable
            ],
            type_ignores=[],
        )
        capability_sets.append(_raw_pdu_ast_capabilities([executable_tree]))
    return capability_sets


def _ast_module_entrypoint_calls(tree: ast.Module, function_names: set[str]) -> set[str]:
    roots: set[str] = set()

    class EntrypointVisitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return

        def visit_Call(self, node: ast.Call) -> None:
            called = _ast_call_name(node)
            if called in function_names:
                roots.add(called)
            self.generic_visit(node)

    for statement in tree.body:
        EntrypointVisitor().visit(statement)
    return roots


def _ast_direct_function_calls(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    function_names: set[str],
) -> set[str]:
    calls: set[str] = set()

    class DirectCallVisitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return

        def visit_Call(self, node: ast.Call) -> None:
            called = _ast_call_name(node)
            if called in function_names:
                calls.add(called)
            self.generic_visit(node)

    visitor = DirectCallVisitor()
    for statement in function.body:
        visitor.visit(statement)
    return calls


def _ast_without_nested_dead_scopes(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    cloned = copy.deepcopy(function)

    nested_functions = _ast_immediate_nested_functions(cloned)
    nested_names = set(nested_functions)
    reachable = _ast_unshadowed_direct_nested_calls(cloned, nested_functions)
    pending = list(reachable)
    while pending:
        current = pending.pop()
        sibling_names = nested_names - _ast_local_bound_names(
            nested_functions[current]
        )
        for called in _ast_direct_function_calls(
            nested_functions[current], sibling_names
        ):
            if called not in reachable:
                reachable.add(called)
                pending.append(called)

    class DeadScopePruner(ast.NodeTransformer):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef | None:
            if node.name.lower() not in reachable:
                return None
            return _ast_without_nested_dead_scopes(node)

        def visit_AsyncFunctionDef(
            self, node: ast.AsyncFunctionDef
        ) -> ast.AsyncFunctionDef | None:
            if node.name.lower() not in reachable:
                return None
            return _ast_without_nested_dead_scopes(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return None

        def visit_Lambda(self, node: ast.Lambda) -> ast.Constant:
            return ast.copy_location(ast.Constant(value=None), node)

    pruner = DeadScopePruner()
    cloned.body = [
        transformed
        for statement in cloned.body
        if (transformed := pruner.visit(statement)) is not None
    ]
    return cloned


def _ast_immediate_nested_functions(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    nested: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}

    class NestedFunctionCollector(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            nested[node.name.lower()] = node

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            nested[node.name.lower()] = node

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return

    collector = NestedFunctionCollector()
    for statement in function.body:
        collector.visit(statement)
    return nested


def _ast_unshadowed_direct_nested_calls(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    nested_functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
) -> set[str]:
    nested_names = set(nested_functions)
    bindings: dict[str, list[tuple[int, int, str]]] = {
        name: [] for name in nested_names
    }
    calls: dict[str, list[tuple[int, int]]] = {name: [] for name in nested_names}

    class ScopeEventCollector(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            name = node.name.lower()
            if name in nested_names:
                bindings[name].append((node.lineno, node.col_offset, "function"))

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            name = node.name.lower()
            if name in nested_names:
                bindings[name].append((node.lineno, node.col_offset, "function"))

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            name = node.name.lower()
            if name in nested_names:
                bindings[name].append((node.lineno, node.col_offset, "other"))

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return

        def visit_Name(self, node: ast.Name) -> None:
            name = node.id.lower()
            if name in nested_names and isinstance(node.ctx, (ast.Store, ast.Del)):
                bindings[name].append((node.lineno, node.col_offset, "other"))

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                name = (alias.asname or alias.name.split(".", 1)[0]).lower()
                if name in nested_names:
                    bindings[name].append((node.lineno, node.col_offset, "other"))

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            for alias in node.names:
                name = (alias.asname or alias.name).lower()
                if name in nested_names:
                    bindings[name].append((node.lineno, node.col_offset, "other"))

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            if node.name and str(node.name).lower() in nested_names:
                bindings[str(node.name).lower()].append(
                    (node.lineno, node.col_offset, "other")
                )
            self.generic_visit(node)

        def visit_MatchAs(self, node: ast.MatchAs) -> None:
            if node.name and node.name.lower() in nested_names:
                bindings[node.name.lower()].append(
                    (node.lineno, node.col_offset, "other")
                )
            if node.pattern is not None:
                self.visit(node.pattern)

        def visit_MatchStar(self, node: ast.MatchStar) -> None:
            if node.name and node.name.lower() in nested_names:
                bindings[node.name.lower()].append(
                    (node.lineno, node.col_offset, "other")
                )

        def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
            if node.rest and node.rest.lower() in nested_names:
                bindings[node.rest.lower()].append(
                    (node.lineno, node.col_offset, "other")
                )
            for key in node.keys:
                self.visit(key)
            for pattern in node.patterns:
                self.visit(pattern)

        def visit_ListComp(self, node: ast.ListComp) -> None:
            self._visit_comprehension(node.generators, node.elt)

        def visit_SetComp(self, node: ast.SetComp) -> None:
            self._visit_comprehension(node.generators, node.elt)

        def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
            self._visit_comprehension(node.generators, node.elt)

        def visit_DictComp(self, node: ast.DictComp) -> None:
            self._visit_comprehension(node.generators, node.key, node.value)

        def _visit_comprehension(
            self,
            generators: list[ast.comprehension],
            *values: ast.AST,
        ) -> None:
            for generator in generators:
                self.visit(generator.iter)
                for condition in generator.ifs:
                    self.visit(condition)
            for value in values:
                self.visit(value)

        def visit_Call(self, node: ast.Call) -> None:
            name = _ast_call_name(node)
            if name in nested_names:
                calls[name].append((node.lineno, node.col_offset))
            self.generic_visit(node)

    collector = ScopeEventCollector()
    for statement in function.body:
        collector.visit(statement)

    reachable: set[str] = set()
    for name, call_positions in calls.items():
        events = sorted(bindings[name])
        expected_definition = nested_functions[name]
        expected_position = (
            expected_definition.lineno,
            expected_definition.col_offset,
        )
        for call_position in call_positions:
            preceding = [event for event in events if event[:2] < call_position]
            if not preceding:
                continue
            latest = preceding[-1]
            if latest[2] == "function" and latest[:2] == expected_position:
                reachable.add(name)
                break
    return reachable


def _ast_local_bound_names(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> set[str]:
    bound = {
        argument.arg.lower()
        for argument in (
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
        )
    }
    declared_external: set[str] = set()

    class LocalBindingCollector(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            bound.add(node.name.lower())

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            bound.add(node.name.lower())

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            bound.add(node.name.lower())

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return

        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, ast.Store):
                bound.add(node.id.lower())

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                bound.add((alias.asname or alias.name.split(".", 1)[0]).lower())

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            for alias in node.names:
                bound.add((alias.asname or alias.name).lower())

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            if node.name:
                bound.add(str(node.name).lower())
            self.generic_visit(node)

        def visit_MatchAs(self, node: ast.MatchAs) -> None:
            if node.name:
                bound.add(node.name.lower())
            if node.pattern is not None:
                self.visit(node.pattern)

        def visit_MatchStar(self, node: ast.MatchStar) -> None:
            if node.name:
                bound.add(node.name.lower())

        def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
            if node.rest:
                bound.add(node.rest.lower())
            for key in node.keys:
                self.visit(key)
            for pattern in node.patterns:
                self.visit(pattern)

        def visit_ListComp(self, node: ast.ListComp) -> None:
            self._visit_comprehension(node.generators, node.elt)

        def visit_SetComp(self, node: ast.SetComp) -> None:
            self._visit_comprehension(node.generators, node.elt)

        def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
            self._visit_comprehension(node.generators, node.elt)

        def visit_DictComp(self, node: ast.DictComp) -> None:
            self._visit_comprehension(node.generators, node.key, node.value)

        def _visit_comprehension(
            self,
            generators: list[ast.comprehension],
            *values: ast.AST,
        ) -> None:
            for generator in generators:
                self.visit(generator.iter)
                for condition in generator.ifs:
                    self.visit(condition)
            for value in values:
                self.visit(value)

        def visit_Global(self, node: ast.Global) -> None:
            declared_external.update(name.lower() for name in node.names)

        def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
            declared_external.update(name.lower() for name in node.names)

    collector = LocalBindingCollector()
    for statement in function.body:
        collector.visit(statement)
    return bound - declared_external


def _raw_pdu_ast_capabilities(trees: list[ast.Module]) -> set[str]:
    capabilities: set[str] = set()
    all_functions = [
        node
        for tree in trees
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    function_names = {function.name.lower() for function in all_functions}
    function_calls = {
        function.name.lower(): {
            called
            for call in ast.walk(function)
            if isinstance(call, ast.Call)
            and (called := _ast_call_name(call))
            and called in function_names
        }
        for function in all_functions
    }
    send_capable = {
        function.name.lower()
        for function in all_functions
        if any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr in {"send", "sendall"}
            for call in ast.walk(function)
        )
    }
    receive_capable = {
        function.name.lower()
        for function in all_functions
        if any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr in {"recv", "recv_into", "read", "readexactly"}
            for call in ast.walk(function)
        )
    }
    connection_factories = {
        function.name.lower()
        for function in all_functions
        if any(
            isinstance(call, ast.Call)
            and _ast_call_name(call) in {"create_connection", "open_connection"}
            for call in ast.walk(function)
        )
    }
    for reachable in (send_capable, receive_capable, connection_factories):
        changed = True
        while changed:
            changed = False
            for function_name, called_names in function_calls.items():
                if function_name not in reachable and called_names & reachable:
                    reachable.add(function_name)
                    changed = True
    for tree in trees:
        functions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for function in functions:
            parameters = {
                argument.arg.lower()
                for argument in (
                    *function.args.posonlyargs,
                    *function.args.args,
                    *function.args.kwonlyargs,
                )
            }
            function_name = function.name.lower()
            assignments = [
                node
                for node in ast.walk(function)
                if isinstance(node, (ast.Assign, ast.AnnAssign))
            ]
            for assignment in assignments:
                value = assignment.value
                targets = assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
                for target in targets:
                    byte_range = _ast_subscript_byte_range(target)
                    byte_index = _ast_subscript_index(target)
                    value_names = _ast_expression_names(value)
                    semantic_names = parameters | value_names
                    if byte_range == (14, 16) and (
                        any(
                            name == "tsih" or name.endswith("_tsih")
                            for name in semantic_names
                        )
                    ) and not _ast_expression_is_constant_zero(value):
                        capabilities.add("nonzero_tsih_input")
                    if byte_range == (20, 22) and (
                        any(
                            name == "cid" or name.endswith("_cid")
                            for name in semantic_names
                        )
                    ):
                        capabilities.add("distinct_cid_input")

                    assigned_names = _ast_assigned_target_names(target)
                    if "tsih" in assigned_names and _ast_expression_reads_byte_range(value, (14, 16)):
                        capabilities.add("response_tsih_capture")
                    if byte_index == 1 and value_names.intersection(
                        {"flags", "login_flags", "transit", "continue_bit", "csg", "nsg"}
                    ):
                        capabilities.add("mutable_login_flags")
                    if byte_index in {2, 3} and value_names.intersection(
                        {"version", "version_max", "version_min", "max_version", "min_version"}
                    ):
                        capabilities.add(f"version_byte_{byte_index}")

            for dictionary in (node for node in ast.walk(function) if isinstance(node, ast.Dict)):
                for key, value in zip(dictionary.keys, dictionary.values):
                    if (
                        isinstance(key, ast.Constant)
                        and str(key.value).lower() == "tsih"
                        and _ast_expression_reads_byte_range(value, (14, 16))
                    ):
                        capabilities.add("response_tsih_capture")

            for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
                if not (
                    isinstance(call.func, ast.Attribute)
                    and call.func.attr in {"pack_into", "unpack_from"}
                    and len(call.args) >= 3
                ):
                    continue
                offset = call.args[2]
                if not isinstance(offset, ast.Constant) or not isinstance(offset.value, int):
                    continue
                call_names = _ast_expression_names(call)
                if call.func.attr == "pack_into" and int(offset.value) == 14 and "tsih" in call_names:
                    capabilities.add("nonzero_tsih_input")
                if call.func.attr == "pack_into" and int(offset.value) == 20 and "cid" in call_names:
                    capabilities.add("distinct_cid_input")
                for assignment in assignments:
                    if assignment.value is None or call not in ast.walk(assignment.value):
                        continue
                    targets = (
                        assignment.targets
                        if isinstance(assignment, ast.Assign)
                        else [assignment.target]
                    )
                    assigned_names = {
                        name
                        for target in targets
                        for name in _ast_assigned_target_names(target)
                    }
                    if (
                        call.func.attr == "unpack_from"
                        and int(offset.value) == 14
                        and "tsih" in assigned_names
                    ):
                        capabilities.add("response_tsih_capture")

            if _ast_function_has_status_oracle(function):
                capabilities.add("login_response_status_oracle")
            if _ast_function_has_dual_socket_lifecycle(
                function,
                function_name=function_name,
                connection_factories=connection_factories,
            ):
                capabilities.add("dual_socket_lifecycle")
            send_calls = [
                node
                for node in ast.walk(function)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"send", "sendall"}
            ]
            if len(send_calls) >= 2:
                capabilities.add("multi_pdu_login")
            for loop in (
                node for node in ast.walk(function) if isinstance(node, (ast.For, ast.AsyncFor, ast.While))
            ):
                loop_calls = {
                    called
                    for call in ast.walk(loop)
                    if isinstance(call, ast.Call)
                    and (called := _ast_call_name(call))
                }
                if loop_calls & send_capable & receive_capable:
                    capabilities.add("multi_pdu_login")
    if {"version_byte_2", "version_byte_3"}.issubset(capabilities):
        capabilities.add("version_range_input")
    return capabilities


def _ast_subscript_byte_range(node: ast.AST) -> tuple[int, int] | None:
    if not isinstance(node, ast.Subscript) or not isinstance(node.slice, ast.Slice):
        return None
    lower = node.slice.lower
    upper = node.slice.upper
    if not (
        isinstance(lower, ast.Constant)
        and isinstance(lower.value, int)
        and isinstance(upper, ast.Constant)
        and isinstance(upper.value, int)
    ):
        return None
    return int(lower.value), int(upper.value)


def _ast_subscript_index(node: ast.AST) -> int | None:
    if not isinstance(node, ast.Subscript):
        return None
    index = node.slice
    if isinstance(index, ast.Constant) and isinstance(index.value, int):
        return int(index.value)
    return None


def _ast_expression_names(node: ast.AST) -> set[str]:
    names = {
        child.id.lower()
        for child in ast.walk(node)
        if isinstance(child, ast.Name)
    }
    names.update(
        child.attr.lower()
        for child in ast.walk(node)
        if isinstance(child, ast.Attribute)
    )
    return names


def _ast_call_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id.lower()
    if isinstance(call.func, ast.Attribute):
        return call.func.attr.lower()
    return ""


def _ast_expression_is_constant_zero(node: ast.AST) -> bool:
    names = [child for child in ast.walk(node) if isinstance(child, ast.Name)]
    numeric_values = [
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, int)
    ]
    return not names and bool(numeric_values) and all(value == 0 for value in numeric_values)


def _ast_assigned_target_names(node: ast.AST) -> set[str]:
    return {
        child.id.lower()
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
    }


def _ast_expression_reads_byte_range(node: ast.AST, expected: tuple[int, int]) -> bool:
    return any(
        _ast_subscript_byte_range(child) == expected
        for child in ast.walk(node)
        if isinstance(child, ast.Subscript)
    )


def _ast_function_has_status_oracle(
    function: ast.AST,
) -> bool:
    status_names = {"status_class", "status_detail", "statusclass", "statusdetail"}
    compared_names: set[str] = set()
    compared_indexes: set[int] = set()
    for compare in (node for node in ast.walk(function) if isinstance(node, (ast.Compare, ast.Assert))):
        compared_names.update(_ast_expression_names(compare))
        for subscript in (
            node for node in ast.walk(compare) if isinstance(node, ast.Subscript)
        ):
            if isinstance(subscript.slice, ast.Constant) and isinstance(subscript.slice.value, int):
                compared_indexes.add(int(subscript.slice.value))
            elif isinstance(subscript.slice, ast.Constant) and isinstance(subscript.slice.value, str):
                compared_names.add(subscript.slice.value.lower())
    return bool(status_names & compared_names) or {36, 37}.issubset(compared_indexes)


def _ast_function_has_dual_socket_lifecycle(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    function_name: str,
    connection_factories: set[str] | None = None,
) -> bool:
    if not any(marker in function_name for marker in ("mcs", "multi_connection", "append_connection")):
        return False
    connection_calls = 0
    known_factories = set(connection_factories or set())
    for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
        if isinstance(call.func, ast.Attribute) and call.func.attr in {
            "create_connection",
            "open_connection",
            "related",
        }:
            connection_calls += 1
        elif isinstance(call.func, ast.Name) and any(
            marker in call.func.id.lower()
            for marker in ("create_connection", "open_connection", "connect_login")
        ):
            connection_calls += 1
        elif _ast_call_name(call) in known_factories:
            connection_calls += 1
    if connection_calls < 2:
        return False

    close_lines = [
        call.lineno
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "close"
    ]
    status_oracle_lines = [
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, (ast.Compare, ast.Assert))
        and _ast_function_has_status_oracle(node)
    ]
    return not close_lines or not status_oracle_lines or min(close_lines) > min(status_oracle_lines)


def _audit_combined_professional_completeness(
    content: str,
    contract: dict[str, Any],
    *,
    include_execution: bool = True,
    include_consistency: bool = True,
) -> list[dict[str, Any]]:
    profiles = {str(item) for item in contract.get("domain_profiles") or []}
    required_outputs = {str(item) for item in contract.get("required_outputs") or []}
    target = str(contract.get("target") or "")
    has_combined_report = any(
        isinstance(spec, dict) and _is_combined_test_report_spec(spec)
        for spec in (contract.get("artifact_contract") or {}).values()
    )
    if (
        "iscsi_login" not in profiles
        or "完整" not in target
        or not (
            has_combined_report
            or {"business_flow.md", "sfmea.json", "black_box_cases.json"}.issubset(required_outputs)
        )
    ):
        return []

    lower = content.lower()
    issues: list[dict[str, Any]] = []
    issues.extend(_audit_combined_sfmea_order(content))
    if include_execution:
        issues.extend(_audit_combined_execution_contract(content))
    if include_consistency:
        issues.extend(_audit_combined_report_consistency(content))
    scenario_markers = {
        "T+C 非法组合": (r"\bt\s*\+\s*c\b", r"t\s*=\s*1.{0,80}c\s*=\s*1"),
        "非法 NSG": (r"非法\s*nsg", r"invalid\s+nsg", r"reserved\s+nsg"),
        "Unsupported Version": (r"unsupported version", r"不支持.{0,20}版本"),
        "未知合法 key=NotUnderstood": (r"notunderstood", r"not understood"),
        "Target not found/removed": (r"target[_ ]not[_ ]found", r"target[_ ]removed", r"目标不存在", r"目标已删除"),
        "Authorization Failure": (r"authorization failure", r"授权失败"),
        "Redirect": (r"redirect", r"重定向"),
        "Discovery 后 SendTargets": (r"sendtargets",),
        "首 payload 后 timer 注销": (
            r"login[_ ]timer.{0,120}(?:注销|unregister|未重新注册|not re[- ]?armed|cancel)",
            r"(?:注销|unregister|未重新注册|not re[- ]?armed|cancel).{0,120}login[_ ]timer",
            r"spdk_poller_unregister.{0,120}(?:登录定时器|login[_ ]timer)",
            r"(?:登录定时器|login[_ ]timer).{0,120}spdk_poller_unregister",
        ),
    }
    missing = [
        label
        for label, patterns in scenario_markers.items()
        if not _has_combined_iscsi_scenario(
            label=label,
            content=content,
            fallback_patterns=patterns,
        )
    ]
    if missing:
        issues.append(
            _issue(
                "missing_iscsi_professional_scenarios",
                "assistant-output.md",
                "完整 iSCSI Login 交付件缺少专业必测场景: " + "、".join(missing),
                scenarios=missing,
            )
        )

    chap_negative_markers = {
        "错误 CHAP_R": (r"(?:错误|无效|incorrect|invalid|wrong).{0,40}chap_r", r"chap_r.{0,40}(?:错误|无效|incorrect|invalid|wrong)"),
        "未知 CHAP 用户": (r"(?:未知|不存在|unknown).{0,40}(?:chap_)?(?:user|用户|chap_n)",),
        "CHAP 参数顺序错误": (r"chap.{0,60}(?:顺序|次序|order).{0,40}(?:错误|非法|invalid|wrong)",),
        "Mutual CHAP 缺失或错误 challenge": (r"mutual\s*chap.{0,100}(?:缺失|错误|无效|missing|wrong|invalid).{0,60}(?:challenge|chap_c|响应|response)",),
        "Target 要求 Mutual 但 Initiator 未提供": (
            r"(?:target|目标端).{0,80}(?:要求|require).{0,30}mutual(?:\s*chap)?.{0,100}(?:initiator|发起端).{0,60}(?:未提供|缺失|missing|without|does not provide|omits?)",
        ),
    }
    missing_chap = [
        label
        for label, patterns in chap_negative_markers.items()
        if not any(re.search(pattern, content, flags=re.IGNORECASE | re.DOTALL) for pattern in patterns)
    ]
    if missing_chap:
        issues.append(
            _issue(
                "missing_chap_negative_scenarios",
                "sfmea.json",
                "完整 iSCSI Login 交付件缺少独立 CHAP 负向场景: " + "、".join(missing_chap),
                scenarios=missing_chap,
            )
        )

    extended_chap_markers = {
        "不支持的 CHAP_A 算法": (
            r"(?:不支持|unsupported).{0,40}chap_a",
            r"chap_a.{0,40}(?:不支持|unsupported)",
            r"chap_a.{0,80}(?:sha|非\s*md5|算法不匹配|algorithm mismatch).{0,100}(?:仅支持|只支持|supports? only|md5)",
        ),
        "缺少 CHAP_R": (r"(?:缺少|缺失|missing|absent).{0,40}chap_r", r"chap_r.{0,40}(?:缺少|缺失|missing|absent)"),
        "CHAP_R 编码格式错误": (
            r"chap_r.{0,80}(?:hex|base64|编码|encoding|格式).{0,50}(?:错误|无效|error|incorrect|wrong|invalid|malformed)",
            r"chap_r.{0,80}(?:编码|encoding|格式|format).{0,40}(?:错误|无效|error|incorrect|wrong|invalid|malformed).{0,60}(?:hex|base64)?",
        ),
        "Mutual 用户或 secret 缺失": (
            r"mutual(?:\s*chap)?.{0,100}(?:用户|user|secret|密钥).{0,50}(?:缺少|缺失|missing|absent|not configured|not found|no matching)",
            r"mutual(?:\s*chap)?.{0,100}(?:缺少|缺失|missing|absent).{0,60}(?:用户|user|secret|密钥|chap_n|chap_r)",
            r"mutual(?:\s*chap)?.{0,100}(?:chap_n|chap_r).{0,50}(?:缺少|缺失|missing|absent|未提供|not provided)",
        ),
        "Initiator 请求 Mutual 但 Target 禁止": (
            r"(?:initiator|发起端).{0,80}(?:请求|request).{0,30}mutual(?:\s*chap)?.{0,100}(?:target|目标端).{0,70}(?:禁止|未启用|disable|not enabled|does not allow|not allowed|forbid)",
        ),
        "Mutual challenge 合法编码但语义错误": (
            r"mutual(?:\s*chap)?.{0,100}(?:challenge|chap_c).{0,100}(?:合法编码|valid encoding).{0,100}(?:语义错误|错误.{0,30}(?:secret|oracle)|oracle.{0,30}(?:不匹配|mismatch)|wrong value|mismatch)",
            r"mutual(?:\s*chap)?.{0,100}(?:challenge|chap_c).{0,100}(?:correctly\s+encoded|正确编码).{0,100}(?:semantic\s*mismatch|语义错误|semantically\s*(?:wrong|invalid))",
            r"mutual(?:\s*chap)?.{0,120}(?:chap_i|chap_c).{0,120}(?:encode|encoded).{0,40}valid.{0,100}(?:semantic\s*mismatch|semantically\s*(?:wrong|invalid))",
            r"mutual(?:\s*chap)?.{0,120}(?:target\s*)?(?:digest\s*)?oracle.{0,120}(?:误判|不匹配|mismatch|wrong)",
        ),
    }
    missing_extended_chap = [
        label
        for label, patterns in extended_chap_markers.items()
        if not any(re.search(pattern, content, flags=re.IGNORECASE | re.DOTALL) for pattern in patterns)
    ]
    if missing_extended_chap:
        remediation = ""
        if "Mutual challenge 合法编码但语义错误" in missing_extended_chap:
            remediation = (
                "；其中 Mutual challenge 语义负向场景必须使用可正常解码的 CHAP_C，"
                "再用与 target 配置不同的 mutual secret 计算期望 CHAP_R，"
                "以 initiator 拒绝错误响应作为 oracle"
            )
        issues.append(
            _issue(
                "missing_extended_chap_negative_scenarios",
                "sfmea.json",
                "完整 iSCSI Login 交付件缺少扩展 CHAP 安全负向场景: "
                + "、".join(missing_extended_chap)
                + remediation,
                scenarios=missing_extended_chap,
            )
        )

    needs_raw_pdu_harness = bool(
        re.search(r"(?:raw[-_ ]?pdu|原始\s*pdu|\bt\s*\+\s*c\b|chap_r|c-bit|c\s*位)", lower)
    )
    if needs_raw_pdu_harness:
        raw_harness_requirements = {
            "可执行入口": r"(?:python3?|uv\s+run\s+python)[^\n]{0,200}|```python[\s\S]{0,500}(?:import\s+socket|from\s+scapy)",
            "BHS 构造": r"(?:struct\.pack|bytearray|scapy).{0,200}(?:bhs|login|opcode)",
            "会话字段": r"isid.{0,240}cid.{0,240}itt.{0,240}cmdsn|cmdsn.{0,240}itt.{0,240}cid.{0,240}isid",
            "发送与接收": r"(?:sendall|sendto|socket\.send).{0,240}(?:recv|receive|socket\.recv)",
            "CHAP 摘要": r"(?:hashlib\.md5|openssl\s+(?:dgst\s+-md5|md5)|chap.{0,80}(?:digest|摘要).{0,80}(?:代码|command|命令))",
        }
        missing_raw = [
            label
            for label, pattern in raw_harness_requirements.items()
            if not re.search(pattern, content, flags=re.IGNORECASE | re.DOTALL)
        ]
        if missing_raw:
            issues.append(
                _issue(
                    "non_executable_raw_pdu_harness",
                    "black_box_cases.json",
                    "raw-PDU 用例不是可直接复验的 harness，缺少: " + "、".join(missing_raw),
                    fields=missing_raw,
                )
            )
        python_harnesses = re.findall(
            r"```python\s*\n([\s\S]*?)```",
            content,
            flags=re.IGNORECASE,
        )
        syntax_errors: list[str] = []
        for harness in python_harnesses:
            try:
                tree = ast.parse(harness)
            except SyntaxError as exc:
                location = f"第 {exc.lineno} 行" if exc.lineno else "未知行"
                syntax_errors.append(f"{location}: {exc.msg}")
                continue
            semantic_errors = _raw_pdu_python_semantic_errors(harness, tree)
            if semantic_errors:
                issues.append(
                    _issue(
                        "non_executable_raw_pdu_harness",
                        "black_box_cases.json",
                        "raw-PDU Python wire 语义或运行契约无效: "
                        + "；".join(semantic_errors[:8]),
                        errors=semantic_errors[:20],
                    )
                )
        if syntax_errors:
            issues.append(
                _issue(
                    "non_executable_raw_pdu_harness",
                    "black_box_cases.json",
                    "raw-PDU Python 语法无效，下载后无法直接运行: "
                    + "；".join(syntax_errors[:3]),
                    errors=syntax_errors[:10],
                )
            )
        cli_contract_errors = _raw_pdu_cli_contract_errors(content)
        if cli_contract_errors:
            issues.append(
                _issue(
                    "non_executable_raw_pdu_harness",
                    "black_box_cases.json",
                    "raw-PDU 运行命令与 Python 参数解析器不一致: "
                    + "；".join(cli_contract_errors[:8]),
                    errors=cli_contract_errors[:20],
                )
            )

    # The JSON delivery keeps a scenario title and its wire steps in separate
    # fields.  A short cross-field distance limit therefore produced a false
    # "missing" result for an otherwise executable C-bit reassembly case.
    c_bit_case = bool(
        re.search(r"\bc\s*[:=]\s*1", lower)
        and re.search(r"\bc\s*[:=]\s*0", lower)
        and re.search(r"(?:跨|分片|fragment|split|reassembly)", lower)
        and re.search(r"(?:pdu|key|value|参数)", lower)
    )
    if not c_bit_case:
        issues.append(
            _issue(
                "missing_c_bit_fragmentation_case",
                "black_box_cases.json",
                "缺少可执行的 Login C-bit 参数跨 PDU 分片用例：至少覆盖 C=1 中间分片、key/value 边界和 C=0 收尾",
            )
        )

    protocol_claimed = bool(
        re.search(r"(?:status[- _]?(?:class|detail)|\bt\s*=|\bcsg\s*=|\bnsg\s*=|0x0?20[0-9])", lower)
    )
    wire_observer = bool(
        re.search(r"(?:tcpdump|wireshark|pcap|抓包|原始\s*pdu|raw\s+pdu|pdu.{0,30}(?:解析器|parser))", lower)
    )
    if protocol_claimed and not wire_observer:
        issues.append(
            _issue(
                "missing_protocol_wire_observer",
                "black_box_cases.json",
                "用例声明了协议位或状态码预期，但没有提供抓包、原始 PDU 或字段解析器作为可执行观测手段",
            )
        )
    elif protocol_claimed:
        capture_command = bool(
            re.search(r"(?:tcpdump|dumpcap)[^\n]{0,240}(?:\s-w\s|--write)", lower)
        )
        decode_assertion = bool(
            re.search(
                r"(?:tshark|pdu.{0,30}(?:parser|解析器|解析脚本))[\s\S]{0,520}"
                r"(?:\s-y\s|\s-t\s|\s-e\s|filter|字段|assert|断言)",
                lower,
            )
        )
        if not capture_command or not decode_assertion:
            issues.append(
                _issue(
                    "non_executable_protocol_observer",
                    "black_box_cases.json",
                    "协议位/状态码用例必须给出可直接执行的抓包命令、PDU 字段解析/过滤命令及断言；只写“抓包观察”不能交付",
                )
            )

    if not re.search(r"(?:revision|commit|源码版本|git\s+rev)[^\n]{0,80}\b[0-9a-f]{7,40}\b", lower):
        issues.append(
            _issue(
                "missing_source_revision",
                "test_design.md",
                "完整源码分析交付件必须记录被分析仓库的 Git revision/commit",
            )
        )

    hazardous_mapping_isolated = bool(re.search(
        r"(?:null|malloc)\s*bdev|专用测试盘|隔离测试(?:设备|盘)|允许列表|allowlist|disposable|isolated",
        lower,
    ))
    hazardous_mapping_warns_data_loss = bool(re.search(
        r"数据(?:会|可|可能)?(?:被)?(?:销毁|覆盖)|数据销毁风险|随机写|破坏性|destructive|data loss",
        lower,
    ))
    if "multiconnection.sh" in lower and not (
        hazardous_mapping_isolated and hazardous_mapping_warns_data_loss
    ):
        issues.append(
            _issue(
                "unsafe_hazardous_test_mapping",
                "black_box_cases.json",
                "映射的 multiconnection.sh 会使用 NVMe/lvol 并执行随机写；必须限定隔离测试设备并提示数据销毁风险",
            )
        )

    has_numeric_threshold = bool(
        re.search(r"(?:<|≤|低于|不超过)\s*\d+(?:\.\d+)?\s*(?:ms|毫秒)", lower)
        or re.search(r"(?:p9[059]|平均|average)[^\n]{0,80}\d+(?:\.\d+)?\s*(?:ms|毫秒)", lower)
    )
    has_threshold_basis = bool(
        re.search(r"(?:环境基线|历史基线|同环境|硬件配置|样本量|baseline|hardware|sample size|相对退化|regression)", lower)
    )
    if has_numeric_threshold and not has_threshold_basis:
        issues.append(
            _issue(
                "ungrounded_performance_threshold",
                "black_box_cases.json",
                "登录延迟阈值缺少硬件、样本量和同环境基线；应使用相对退化门槛或明确基线来源",
            )
        )
    has_relative_percent_threshold = bool(
        re.search(r"(?:相对退化|relative regression|regression).{0,80}\d+(?:\.\d+)?\s*%", lower)
        or re.search(r"\d+(?:\.\d+)?\s*%.{0,80}(?:相对退化|relative regression|regression)", lower)
    )
    has_variance_basis = bool(
        re.search(r"(?:标准差|方差|置信区间|基线波动|bootstrap|stddev|variance|confidence interval)", lower)
    )
    if has_relative_percent_threshold and not has_variance_basis:
        issues.append(
            _issue(
                "missing_performance_statistical_basis",
                "black_box_cases.json",
                "相对性能阈值必须依据基线方差/标准差、置信区间或历史波动确定，不能直接固定百分比",
            )
        )
    scale_range = r"(?:1\s*[-~至]\s*10|1\.\.10|1\b.{0,320}\b10\b)"
    shared_scale = bool(re.search(rf"(?:评分|score).{{0,48}}{scale_range}", lower))
    has_sfmea_scale = bool(
        (
            shared_scale
            and re.search(r"(?:severity|严重度)", lower)
            and re.search(r"(?:occurrence|发生度)", lower)
            and re.search(r"(?:detection|探测度|可探测度)", lower)
        )
        or (
            re.search(rf"(?:severity|严重度).{{0,40}}{scale_range}", lower)
            and re.search(rf"(?:occurrence|发生度).{{0,40}}{scale_range}", lower)
            and re.search(rf"(?:detection|探测度|可探测度).{{0,40}}{scale_range}", lower)
        )
    ) and bool(
        re.search(r"(?:rpn)[\s\S]{0,240}(?:优先|priority|阈值|threshold|高风险)", lower)
    )
    if not has_sfmea_scale:
        issues.append(
            _issue(
                "missing_sfmea_scoring_scale",
                "sfmea.json",
                "SFMEA 必须定义 Severity/Occurrence/Detection 的 1-10 评分标尺，并按 RPN 阈值给出执行优先级",
            )
        )
    has_occurrence_basis = bool(
        re.search(r"(?:缺陷历史|历史缺陷|协议流量分布|登录流量|测试统计|样本统计|observed rate|defect history|traffic distribution|test statistics)", lower)
    )
    if not has_occurrence_basis:
        issues.append(
            _issue(
                "missing_sfmea_occurrence_basis",
                "sfmea.json",
                "SFMEA Occurrence 评分必须说明缺陷历史、协议/登录流量分布或测试统计依据；仅由分析人员估计不可复核",
            )
        )

    scenario_names = re.findall(
        r'"scenario_name"\s*:\s*"([^"]+)"',
        content,
        flags=re.IGNORECASE,
    )
    non_atomic = [
        name
        for name in scenario_names
        if len(re.findall(r"[、，,]|\s+与\s+|\s+and\s+", name, flags=re.IGNORECASE)) >= 3
    ]
    if non_atomic:
        issues.append(
            _issue(
                "non_atomic_blackbox_case",
                "black_box_cases.json",
                "黑盒用例合并了过多独立故障，无法单独记录和归因: " + "；".join(non_atomic[:5]),
                scenarios=non_atomic[:10],
            )
        )
    return issues


def _is_mcs_case_contract(case_text: str) -> bool:
    lower = str(case_text or "").lower()
    heading = lower.splitlines()[0] if lower.splitlines() else ""
    if re.search(r"\bmcs\b|multiple connections? per session|同一\s*session.{0,20}多连接", heading):
        return True
    tsih_values = re.findall(r"\btsih\s*=\s*(0x[0-9a-f]+|\d+)", lower)
    cid_values = re.findall(r"\bcid\s*=\s*(0x[0-9a-f]+|\d+)", lower)
    nonzero_tsih_reused = any(
        _parse_numeric_literal(value) != 0 and tsih_values.count(value) >= 2
        for value in set(tsih_values)
    )
    same_session_identity = nonzero_tsih_reused and len(set(cid_values)) >= 2
    explicit_same_session = bool(re.search(
        r"(?:same|相同|同一).{0,30}tsih.{0,60}(?:different|new|不同|新).{0,20}cid"
        r"|(?:same|同一).{0,30}session.{0,60}(?:different|new|不同|新).{0,20}cid",
        lower,
        flags=re.IGNORECASE | re.DOTALL,
    ))
    return bool("maxconnections" in lower and (same_session_identity or explicit_same_session))


def _has_mcs_capable_client_contract(case_text: str) -> bool:
    """Validate the MCS client as a conjunction of observable capabilities."""
    lower = str(case_text or "").lower()
    harness_named = any(
        marker in lower
        for marker in (
            "raw-pdu",
            "raw pdu",
            "raw_pdu",
            "raw_iscsi_harness",
            "libiscsi",
        )
    ) and any(marker in lower for marker in ("harness", "client", "客户端", "脚本"))
    socket_io = (
        "sendall" in lower and bool(re.search(r"\brecv\b|接收", lower))
    ) or (
        harness_named
        and bool(re.search(r"发送|\bsend\b", lower))
        and bool(re.search(r"接收|\breceive\b|\brecv\b", lower))
    )
    first_connection_kept = bool(re.search(
        r"(?:保持|保留|维持|keep|retain).{0,30}(?:首|第一|旧|first|existing).{0,30}"
        r"(?:连接|connection|socket).{0,20}(?:在线|打开|存活|online|open|alive)",
        lower,
        flags=re.IGNORECASE | re.DOTALL,
    ))
    tsih_recorded = bool(re.search(
        r"(?:记录|保存|capture|record).{0,30}tsih|tsih.{0,30}(?:记录|保存|captur|record)",
        lower,
        flags=re.IGNORECASE | re.DOTALL,
    ))
    tsih_reused = bool(re.search(
        r"tsih\s*=\s*<[^>]*(?:记录|record)[^>]*>"
        r"|(?:相同|同一|same|reuse|复用|非零|non[- ]?zero).{0,30}tsih"
        r"|tsih.{0,30}(?:相同|同一|same|reuse|复用|非零|non[- ]?zero)",
        lower,
        flags=re.IGNORECASE | re.DOTALL,
    ))
    raw_tsih_values = re.findall(r"\btsih\s*=\s*(0x[0-9a-f]+|\d+)", lower)
    explicit_nonzero_tsih_reuse = any(
        _parse_numeric_literal(value) != 0 and raw_tsih_values.count(value) >= 2
        for value in set(raw_tsih_values)
    )
    cid_values = {
        value.lower()
        for value in re.findall(r"\bcid\s*=\s*(0x[0-9a-f]+|\d+)", lower)
    }
    distinct_cid = len(cid_values) >= 2 or bool(re.search(
        r"(?:不同|新|different|new).{0,20}cid|cid.{0,20}(?:不同|新|different|new)",
        lower,
        flags=re.IGNORECASE | re.DOTALL,
    ))
    response_received = bool(re.search(
        r"(?:接收|解析|recv|receive|parse).{0,50}login response"
        r"|login response.{0,50}(?:接收|解析|recv|receive|parse)"
        r"|(?:接收|receive|recv).{0,12}(?:响应|response)",
        lower,
        flags=re.IGNORECASE | re.DOTALL,
    ))
    response_oracle = bool(re.search(
        r"(?:status|opcode|状态).{0,30}(?:=|0x|断言|检查|assert|拒绝|accept)"
        r"|(?:断言|检查|assert|拒绝|accept).{0,30}(?:status|opcode|状态)",
        lower,
        flags=re.IGNORECASE | re.DOTALL,
    ))
    return all((
        harness_named,
        socket_io,
        first_connection_kept,
        (tsih_recorded and tsih_reused) or explicit_nonzero_tsih_reuse,
        distinct_cid,
        response_received and response_oracle,
    ))


def _parse_numeric_literal(value: str) -> int:
    normalized = str(value or "").strip().lower()
    return int(normalized, 16 if normalized.startswith("0x") else 10)


def _audit_combined_report_consistency(content: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    mcs_harness_capabilities = {
        "nonzero_tsih_input",
        "response_tsih_capture",
        "dual_socket_lifecycle",
        "distinct_cid_input",
        "login_response_status_oracle",
    }
    has_embedded_mcs_harness = any(
        mcs_harness_capabilities.issubset(capabilities)
        for capabilities in _raw_pdu_executable_capability_sets(
            _embedded_python_trees(content)
        )
    )
    if re.search(
        r"(?:"
        r"请在上述[^\n]{0,160}(?:补充|替换|修改)"
        r"|错误描述修正\s*[:：]\s*之前版本"
        r"|修改后的产物\s*[:：]"
        r")",
        content,
        flags=re.IGNORECASE,
    ):
        issues.append(_issue(
            "quality_repair_meta_language",
            "business_flow.md",
            "交付件包含面向模型的修复说明或版本对比话术；必须改写为直接面向用户的最终流程事实。",
        ))

    for heading, body in _combined_black_box_case_blocks(content):
        case_text = f"{heading}\n{body}"
        lower = case_text.lower()
        expected = _combined_case_expected_result(body).lower()
        normal_case = bool(re.search(
            r"(?:normal|baseline|正常|基线).{0,80}(?:without authentication|no[- ]?chap|无认证|未启用认证)",
            lower,
            flags=re.IGNORECASE | re.DOTALL,
        ))
        auth_failure_expected = bool(re.search(
            r"(?:authentication failure|认证失败|fails? to decode|decode error|base64.{0,30}(?:失败|error))",
            expected,
            flags=re.IGNORECASE,
        ))
        if normal_case and auth_failure_expected:
            issues.append(_issue(
                "black_box_expected_result_contradiction",
                "black_box_cases.json",
                f"{heading} 是无认证正常路径，但预期结果却要求认证/解码失败；请按标题场景重写原子化预期。",
                scenario=heading,
            ))

        first_pdu_stall = bool(re.search(
            r"(?:"
            r"login.{0,80}(?:after|发送|收到).{0,30}(?:first|首个|第一个).{0,20}pdu"
            r"|(?:after|发送|收到|首个|第一个|first).{0,100}(?:first|首个|第一个)?\s*login\s*pdu"
            r"|(?:首个|第一个).{0,60}login\s*pdu"
            r"|(?:收到|receive[ds]?).{0,40}(?:target\s*)?chap\s+challenge.{0,100}(?:停止|停滞|stall|不再|without|未发送|不发送)"
            r")",
            lower,
            flags=re.IGNORECASE | re.DOTALL,
        ))
        timer_guarantee = _classify_login_timer_claim(expected) == "guaranteed"
        defect_qualified = bool(re.search(
            r"(?:当前(?:实现|缺陷).{0,100}(?:可能|不会|不能)|预计失败|预期失败|不能保证|不保证|"
            r"实际行为待验证|清理行为待验证|连接状态待验证|"
            r"current (?:implementation|defect|spdk).{0,100}(?:may|might|does not|will not)|"
            r"potential defect|expected to fail|not guaranteed)",
            case_text,
            flags=re.IGNORECASE | re.DOTALL,
        ))
        residue_oracle = bool(re.search(
            r"(?:half[- ]?open|半开|资源残留|资源泄漏|resource (?:residue|leak)|"
            r"(?:连接|connection|socket).{0,40}(?:计数|count|仍在|remains?)|(?:rpc|rss).{0,40}(?:计数|count|残留|leak))",
            case_text,
            flags=re.IGNORECASE | re.DOTALL,
        ))
        evidence_qualifier = defect_qualified and residue_oracle
        if first_pdu_stall and timer_guarantee and not evidence_qualifier:
            issues.append(_issue(
                "black_box_evidence_contradiction",
                "black_box_cases.json",
                f"{heading} 声称首个 Login PDU 后 30 秒定时器必然触发，但源码证据表明 login_timer 已注销且未重新注册；必须标为当前缺陷/待验证并给出资源残留 oracle。",
                scenario=heading,
                constraint_id="iscsi_login_timer_after_first_pdu",
            ))

        mcs_case = _is_mcs_case_contract(case_text)
        if mcs_case and re.search(
            r"iscsiadm[^\n]{0,240}(?:\s--cid(?:\s|=)|(?:指定|设置|set|with)\s*cid\s*=?\s*\d+)",
            case_text,
            flags=re.IGNORECASE,
        ):
            issues.append(_issue(
                "non_executable_mcs_client",
                "black_box_cases.json",
                f"{heading} 使用了 open-iscsi iscsiadm 不支持的 --cid 参数；同 session 多连接必须提供可运行的 libiscsi/raw-PDU 客户端，并显式复用非零 TSIH、保持旧 socket 在线。",
                scenario=heading,
                constraint_id="iscsi_multiconnection_client_capability",
            ))

        case_invokes_mcs_harness = bool(
            re.search(r"raw[-_ ]?pdu|raw_iscsi_harness|--mcs\b", case_text, re.IGNORECASE)
            and re.search(
                r"(?:expect[-_ ]?status|status[-_ ]?(?:class|detail)|"
                r"status-(?:class|detail)|状态(?:类|明细))",
                case_text,
                re.IGNORECASE,
            )
        )
        has_mcs_capable_client = _has_mcs_capable_client_contract(case_text) or (
            has_embedded_mcs_harness and case_invokes_mcs_harness
        )
        if mcs_case and not has_mcs_capable_client:
            issues.append(_issue(
                "missing_mcs_capable_client",
                "black_box_cases.json",
                f"{heading} 没有指定可控制非零 TSIH、不同 CID 并保持旧 socket 在线的可执行客户端；请提供 libiscsi/raw-PDU harness、运行命令和响应断言，iscsiadm 不能完成该场景。",
                scenario=heading,
                constraint_id="iscsi_multiconnection_client_capability",
            ))

        maps_multiconnection = "multiconnection.sh" in lower
        mapping_is_qualified = bool(re.search(
            r"(?:"
            r"multiconnection\.sh.{0,260}(?:不覆盖|不能证明|不能映射|仅作.{0,40}参考|需要新增|does not cover|does not prove|not map)"
            r"|(?:非|不是|不使用|不映射|not)\s*`?multiconnection\.sh`?"
            r")",
            case_text,
            flags=re.IGNORECASE | re.DOTALL,
        ))
        if mcs_case and maps_multiconnection and not mapping_is_qualified:
            issues.append(_issue(
                "black_box_test_mapping_contradiction",
                "black_box_cases.json",
                f"{heading} 把 multiconnection.sh 映射为同一 session 的 MCS 用例；该脚本实际创建多个 target 并分别登录，不能证明非零 TSIH 下追加不同 CID。",
                scenario=heading,
                constraint_id="iscsi_multiconnection_mapping_scope",
            ))

        tsih_zero_reinstatement = bool(
            re.search(r"tsih\s*[:=]?\s*0", case_text, flags=re.IGNORECASE)
            and re.search(
                r"(?:same|相同|同一|已有).{0,80}isid|isid.{0,80}(?:same|相同|同一|已有)",
                case_text,
                flags=re.IGNORECASE | re.DOTALL,
            )
            and re.search(r"reinstatement|会话恢复|会话重建", case_text, flags=re.IGNORECASE)
        )
        ambiguous_oracle = bool(re.search(
            r"(?:\bor\b|或者|或认为|二选一|视策略|取决于.{0,30}策略|待确认)",
            expected,
            flags=re.IGNORECASE,
        ))
        if tsih_zero_reinstatement and ambiguous_oracle:
            issues.append(_issue(
                "black_box_expected_result_ambiguous",
                "black_box_cases.json",
                f"{heading} 对同 ISID、TSIH=0 的 session reinstatement 给出互斥预期；黑盒用例必须使用源码支持的唯一 oracle，不能写成拒绝或恢复二选一。",
                scenario=heading,
                constraint_id="iscsi_tsih_reinstatement_scope",
            ))

        discovery_login_case = bool(re.search(
            r"discovery.{0,40}login|login.{0,40}discovery",
            lower,
            flags=re.IGNORECASE | re.DOTALL,
        ))
        if (
            discovery_login_case
            and bool(re.search(
                r"(?:does not|must not|should not|不应|不会|不返回|不包含).{0,40}targetaddress",
                expected,
                flags=re.IGNORECASE | re.DOTALL,
            ))
        ):
            issues.append(_issue(
                "black_box_evidence_contradiction",
                "black_box_cases.json",
                f"{heading} 声称 Discovery Login Response 不包含 TargetAddress；当前源码在 target 非空的 Discovery session 中向 Login Response 追加该字段。",
                scenario=heading,
                constraint_id="iscsi_discovery_target_address",
            ))

        login_latency_case = bool(re.search(
            r"(?:login|登录).{0,60}(?:latency|延迟|p50|p95|p99)|(?:latency|延迟|p50|p95|p99).{0,60}(?:login|登录)",
            case_text,
            flags=re.IGNORECASE | re.DOTALL,
        ))
        absolute_latency_oracle = bool(re.search(
            r"(?:<|<=|≤|低于|不超过)\s*\d+(?:\.\d+)?\s*(?:ms|毫秒)",
            expected,
            flags=re.IGNORECASE,
        ))
        measured_threshold_basis = bool(re.search(
            r"(?:历史基线|同环境基线|实测基线|基线测得|measured baseline|historical baseline|same[- ]environment baseline)"
            r".{0,120}(?:样本量|sample size|硬件|hardware|commit|revision)",
            case_text,
            flags=re.IGNORECASE | re.DOTALL,
        ))
        if login_latency_case and absolute_latency_oracle and not measured_threshold_basis:
            issues.append(_issue(
                "ungrounded_performance_threshold",
                "black_box_cases.json",
                f"{heading} 给出绝对登录延迟阈值，但没有同环境实测基线、样本量和硬件/版本来源；首次运行只能采样建基线，不能预设通过值。",
                scenario=heading,
            ))

    for risk_id, row_text in _combined_sfmea_full_rows(content):
        lower = row_text.lower()
        mid_login_stall = bool(re.search(
            r"(?:中间阶段|中途|多阶段|mid[- ]?login|after.{0,40}first.{0,20}login.{0,20}pdu|首个.{0,30}login.{0,20}pdu.{0,20}后)"
            r".{0,100}(?:停止响应|停滞|stall|无响应|暂停|延迟|等待|delay|wait)"
            r"|(?:send|sends|发送).{0,30}(?:first|首个|第一个).{0,20}login.{0,20}pdu.{0,60}(?:stop|stops|停止|停滞|无响应)",
            lower,
            flags=re.IGNORECASE | re.DOTALL,
        ))
        timer_closes_connection = bool(re.search(
            r"(?:"
            r"login[_ ]?timer.{0,120}(?:连接关闭|断开|close|退出|exiting)"
            r"|\d+\s*(?:秒|seconds?|s).{0,120}(?:连接关闭|断开|close|fin|rst)"
            r")",
            lower,
            flags=re.IGNORECASE | re.DOTALL,
        ))
        row_is_qualified = bool(re.search(
            r"(?:"
            r"(?:不会|不应|不能保证|不保证|may not|does not|will not).{0,80}(?:关闭|断开|close|fin|rst)"
            r"|(?:half[- ]?open|资源残留|资源泄漏|resource (?:residue|leak))"
            r"|(?:当前缺陷|待验证缺陷|potential defect|expected to fail)"
            r")",
            lower,
            flags=re.IGNORECASE | re.DOTALL,
        ))
        if mid_login_stall and timer_closes_connection and not row_is_qualified:
            issues.append(_issue(
                "sfmea_evidence_contradiction",
                "sfmea.json",
                f"{risk_id} 声称首个 Login payload 后的中途停滞会由 30 秒 login_timer 清理，但该 timer 已注销且未重新注册；必须改为资源残留风险和待验证 oracle。",
                risk_id=risk_id,
                constraint_id="iscsi_login_timer_after_first_pdu",
            ))

    sfmea_categories: dict[str, list[str]] = {}
    for risk_id, failure_mode in _combined_sfmea_rows(content):
        category = _sfmea_semantic_category(failure_mode)
        if category:
            sfmea_categories.setdefault(category, []).append(risk_id)
    for category, risk_ids in sfmea_categories.items():
        if len(risk_ids) < 2:
            continue
        issues.append(_issue(
            "duplicate_sfmea_risk",
            "sfmea.json",
            "SFMEA 存在语义重复风险，不能通过换词增加数量：" + "、".join(risk_ids),
            category=category,
            risk_ids=risk_ids,
        ))
    return issues


def _classify_login_timer_claim(expected_result: str) -> str:
    """Classify first-PDU timer semantics without losing negation polarity."""
    lower = str(expected_result or "").lower()
    denied_patterns = (
        r"\bno\s+\d+\s*(?:s|seconds?)\s+timeout\b",
        r"(?:login[_ ]?timer|登录定时器).{0,80}(?:disabled|unregistered|deregistered|注销|未重新注册)",
        r"(?:timeout|定时器).{0,60}(?:will not|does not|may not|might not|not guaranteed|不会|不能|不保证).{0,40}(?:trigger|fire|close|触发|关闭|断开)?",
        r"(?:will not|does not|may not|might not|not guaranteed|不会|不能|不保证).{0,80}(?:timeout|timer|定时器|触发|关闭|断开)",
        r"(?:half[- ]?open|may hang|might hang|可能残留|资源残留|resource leak).{0,120}(?:待验证|unverified|not closed|未关闭)?",
    )
    if any(
        re.search(pattern, lower, flags=re.IGNORECASE | re.DOTALL)
        for pattern in denied_patterns
    ):
        return "denied_or_uncertain"

    guaranteed = bool(
        re.search(
            r"(?:"
            r"login[_ ]?timer.{0,80}(?:fires?|触发)"
            r"|(?:\d+\s*(?:秒|seconds?|s)).{0,100}(?:主动)?"
            r"(?:关闭|断开|\bclose(?:d|s)?\b|\bfin\b|\brst\b)"
            r")",
            lower,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )
    return "guaranteed" if guaranteed else "unspecified"


def _sfmea_semantic_category(failure_mode: str) -> str:
    lower = str(failure_mode or "").lower()
    if (
        re.search(r"mutual(?:\s*chap)?", lower)
        and re.search(r"(?:challenge|chap_c)", lower)
        and re.search(r"(?:语义错误|错误值|wrong|mismatch|replay)", lower)
    ):
        return "mutual_challenge_semantic_mismatch"
    if (
        re.search(r"(?:chap_a|chap.{0,30}algorithm|chap.{0,20}算法)", lower)
        and re.search(
            r"(?:unsupported|不支持|mismatch|不匹配|non[- ]?md5|非\s*md5|sha)",
            lower,
        )
    ):
        return "unsupported_chap_algorithm"
    return ""


def _combined_black_box_case_blocks(content: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(
        r"(?im)^\s*#{2,6}\s+((?:B|BB|BBC|TC|CASE|用例)[-_ ]?\d+\b[^\n]*)$",
        content,
    ))
    blocks: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        blocks.append((match.group(1).strip(), content[match.end():end]))
    return blocks


def _combined_case_expected_result(body: str) -> str:
    match = re.search(
        r"(?im)^\s*(?:[-*]\s*)?(?:预期结果|expected result)\s*[:：]\s*(.+)$",
        body,
    )
    return str(match.group(1) if match else "").strip()


def _combined_sfmea_rows(content: str) -> list[tuple[str, str]]:
    return [
        (match.group(1).strip(), match.group(2).strip())
        for match in re.finditer(
            r"(?im)^\s*\|\s*((?:SFMEA|FMEA|FM|F)[-_ ]?\d+)\s*\|\s*([^|]+)\|",
            content,
        )
    ]


def _combined_sfmea_full_rows(content: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in content.splitlines():
        match = re.match(
            r"^\s*\|\s*((?:SFMEA|FMEA|FM|F)[-_ ]?\d+)\s*\|",
            line,
            flags=re.IGNORECASE,
        )
        if match:
            rows.append((match.group(1).strip(), line.strip()))
    return rows


def _raw_pdu_cli_contract_errors(content: str) -> list[str]:
    command_options: set[str] = set()
    for match in re.finditer(
        r"(?:python3?|uv\s+run\s+python)\s+[^\n`]*?(?:raw[-_]?pdu|pdu[-_]?raw)[^\n`]*?\.py(?P<args>[^\n`]*)",
        content,
        flags=re.IGNORECASE,
    ):
        command_options.update(
            option.lower()
            for option in re.findall(r"--[a-zA-Z][a-zA-Z0-9_-]*", match.group("args"))
        )
    if not command_options:
        return []

    declared_options: set[str] = set()
    for harness in re.findall(r"```python\s*\n([\s\S]*?)```", content, flags=re.IGNORECASE):
        try:
            tree = ast.parse(harness)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if not isinstance(function, ast.Attribute) or function.attr != "add_argument":
                continue
            for argument in node.args:
                if (
                    isinstance(argument, ast.Constant)
                    and isinstance(argument.value, str)
                    and argument.value.startswith("--")
                ):
                    declared_options.add(argument.value.lower())

    missing = sorted(command_options - declared_options)
    return [f"命令使用 {option}，但脚本未声明该参数" for option in missing]


def _raw_pdu_python_semantic_errors(source: str, tree: ast.Module) -> list[str]:
    errors: list[str] = []
    dsl_writer = bool(
        re.search(
            r"(?:bhs|buf|header)\s*\[\s*5\s*:\s*8\s*\]\s*=.{0,100}(?:to_bytes|pack)",
            source,
            flags=re.IGNORECASE | re.DOTALL,
        )
        or re.search(
            r"(?:bhs|buf|header)\s*\[\s*5\s*\].{0,180}"
            r"(?:bhs|buf|header)\s*\[\s*6\s*\].{0,180}"
            r"(?:bhs|buf|header)\s*\[\s*7\s*\]",
            source,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )
    if not dsl_writer:
        errors.append("DataSegmentLength 必须写入 BHS bytes 5-7，byte 4 是 TotalAHSLength")

    dsl_reader = bool(
        re.search(
            r"(?:int\.from_bytes\s*\(\s*(?:bhs|buf|header|response|rsp)\s*\[\s*5\s*:\s*8\s*\])",
            source,
            flags=re.IGNORECASE,
        )
        or re.search(
            r"(?:bhs|buf|header|response|rsp)\s*\[\s*5\s*\].{0,180}"
            r"(?:bhs|buf|header|response|rsp)\s*\[\s*6\s*\].{0,180}"
            r"(?:bhs|buf|header|response|rsp)\s*\[\s*7\s*\].{0,120}(?:dlen|data_segment|length)",
            source,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )
    if not dsl_reader:
        errors.append("接收路径必须从 BHS bytes 5-7 解析 DataSegmentLength")

    undefined = _undefined_python_function_names(tree)
    if undefined:
        errors.append("函数引用未定义名称: " + ", ".join(undefined[:12]))

    direct_raises = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith(("expect", "case_"))
        and any(isinstance(statement, ast.Raise) for statement in node.body)
    ]
    if direct_raises:
        errors.append("测试函数成功路径仍会无条件抛出异常: " + ", ".join(direct_raises[:8]))

    swallowed_self_test_sentinels: list[str] = []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) or "self_test" not in function.name:
            continue
        for try_node in (node for node in ast.walk(function) if isinstance(node, ast.Try)):
            sentinel_raises = [
                node
                for statement in try_node.body
                for node in ast.walk(statement)
                if isinstance(node, ast.Raise)
                and isinstance(node.exc, ast.Call)
                and isinstance(node.exc.func, ast.Name)
                and node.exc.func.id == "AssertionError"
            ]
            catches_and_discards_assertion = any(
                isinstance(handler.type, ast.Name)
                and handler.type.id == "AssertionError"
                and handler.body
                and all(isinstance(statement, ast.Pass) for statement in handler.body)
                for handler in try_node.handlers
            )
            if sentinel_raises and catches_and_discards_assertion:
                swallowed_self_test_sentinels.append(function.name)
                break
    if swallowed_self_test_sentinels:
        errors.append(
            "自检失败哨兵被同一 try/except AssertionError 吞掉: "
            + ", ".join(sorted(set(swallowed_self_test_sentinels)))
        )

    functions = {
        node.name: ast.get_source_segment(source, node) or ""
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    reinstatement_node = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in {"case_session_reinstatement", "case_tsih_reinstatement"}
        ),
        None,
    )
    if reinstatement_node is not None:
        close_lines = [
            call.lineno
            for call in ast.walk(reinstatement_node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "close"
        ]
        login_lines = [
            call.lineno
            for call in ast.walk(reinstatement_node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr in {"send_login", "login_none_to_full"}
        ]
        if close_lines and login_lines and min(close_lines) < max(login_lines):
            errors.append(
                "session reinstatement 必须保留旧连接直到同 ISID/TSIH=0 的新登录完成，不能先关闭旧连接"
            )
    mcs_append = functions.get("case_mcs_append_connection", "")
    if mcs_append:
        checks_nonzero_tsih = bool(
            re.search(
                r"(?:not\s+tsih|tsih\s*(?:==|is)\s*(?:0|none)|(?:get\s*\(\s*['\"]tsih['\"]\s*\)).{0,40}(?:==|is)\s*(?:0|none))",
                mcs_append,
                flags=re.IGNORECASE | re.DOTALL,
            )
        )
        proves_dual_connection = bool(
            re.search(
                r"(?:iscsi_get_connections|get_connections|rpc).{0,300}(?:cid|tsih).{0,300}(?:cid|tsih|connections)",
                mcs_append,
                flags=re.IGNORECASE | re.DOTALL,
            )
            or re.search(
                r"(?:old|base|original|sess).{0,100}(?:socket|sock|connection).{0,120}(?:alive|open|peek|eof|closed)",
                mcs_append,
                flags=re.IGNORECASE | re.DOTALL,
            )
        )
        if not checks_nonzero_tsih or not proves_dual_connection:
            errors.append(
                "MCS append 必须断言首个 TSIH 非零，并证明旧连接与新 CID 在同一 TSIH 下同时存活"
            )
    target_forbids = functions.get("case_initiator_mutual_target_forbids", "")
    mutual_helper = functions.get("complete_chap_response", "")
    target_forbids_contract = target_forbids + "\n" + mutual_helper
    if target_forbids and not (
        re.search(r"chap_i", target_forbids_contract, flags=re.IGNORECASE)
        and re.search(r"chap_c", target_forbids_contract, flags=re.IGNORECASE)
    ):
        errors.append("initiator 请求 mutual/target 禁止用例必须发送完整 CHAP_I 与 CHAP_C，不能退化为缺 challenge")
    semantic_wrong = functions.get("case_mutual_semantic_wrong_challenge", "")
    if semantic_wrong and not re.search(
        r"(?:wrong|bad|incorrect).{0,40}(?:mutual|secret)|mutual_secret\s*\+|mutual_secret\s*\.replace",
        semantic_wrong,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        errors.append("Mutual challenge 语义负向用例必须使用与 target 配置不同的 mutual secret 建立错误 oracle")
    if semantic_wrong:
        semantic_wrong_node = next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "case_mutual_semantic_wrong_challenge"
            ),
            None,
        )
        delegated_helpers = []
        if semantic_wrong_node is not None:
            delegated_helpers = [
                functions[call.func.id]
                for call in ast.walk(semantic_wrong_node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id in functions
            ]
        semantic_oracle_contract = "\n".join([semantic_wrong, *delegated_helpers])
        normalized_oracle = bool(
            re.search(
                r"decode_chap_value|casefold\s*\(|compare_digest\s*\(|bytes\.fromhex\s*\(",
                semantic_oracle_contract,
                flags=re.IGNORECASE,
            )
        )
        correct_oracle = bool(
            re.search(
                r"(?:expected|oracle).{0,24}(?:correct|right|target)|"
                r"(?:correct|right|target).{0,24}(?:secret|oracle)",
                semantic_oracle_contract,
                flags=re.IGNORECASE | re.DOTALL,
            )
        )
        wrong_oracle = bool(
            re.search(
                r"(?:expected|oracle).{0,24}(?:wrong|bad|incorrect)|"
                r"(?:wrong|bad|incorrect).{0,24}(?:secret|oracle)",
                semantic_oracle_contract,
                flags=re.IGNORECASE | re.DOTALL,
            )
        )
        accepts_auth_failure = "expect_auth_fail" in semantic_oracle_contract
        if accepts_auth_failure or not (normalized_oracle and correct_oracle and wrong_oracle):
            errors.append(
                "Mutual CHAP oracle 必须按解码后的 bytes 同时验证正确 secret 匹配、错误 secret 不匹配；"
                "Authentication Failure 不能作为该 oracle 用例通过"
            )

    operational = functions.get("case_operational_multi_round", "")
    if operational:
        full_feature_contract = operational + "\n" + functions.get("expect_full_feature", "")
        asserts_t = bool(
            re.search(
                r"(?:get\s*\(\s*['\"]t['\"]\s*\)|\[\s*['\"]t['\"]\s*\])"
                r"\s*(?:==|!=|in|not\s+in).{0,40}\b1\b",
                full_feature_contract,
                flags=re.IGNORECASE | re.DOTALL,
            )
        )
        asserts_nsg = bool(
            re.search(
                r"(?:get\s*\(\s*['\"]nsg['\"]\s*\)|\[\s*['\"]nsg['\"]\s*\])"
                r"\s*(?:==|!=|in|not\s+in).{0,40}(?:\b3\b|NSG_FULL_FEATURE)",
                full_feature_contract,
                flags=re.IGNORECASE | re.DOTALL,
            )
        )
        if not (asserts_t and asserts_nsg):
            errors.append(
                "Operational Negotiation 最终轮必须使用独立 Full Feature oracle 断言 T=1、NSG=3，"
                "不能只复用 success-or-continue"
            )
    return errors


def _undefined_python_function_names(tree: ast.Module) -> list[str]:
    module_defined: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module_defined.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                module_defined.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            module_defined.update(_assigned_python_names(node))

    allowed = module_defined | set(dir(builtins))
    parents: dict[ast.AST, ast.AST] = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }

    def scope_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[set[str], set[str]]:
        defined = {
            argument.arg
            for argument in [
                *function.args.posonlyargs,
                *function.args.args,
                *function.args.kwonlyargs,
            ]
        }
        if function.args.vararg:
            defined.add(function.args.vararg.arg)
        if function.args.kwarg:
            defined.add(function.args.kwarg.arg)
        loaded: set[str] = set()

        class ScopeVisitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                defined.add(node.name)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                defined.add(node.name)

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                defined.add(node.name)

            def visit_Lambda(self, node: ast.Lambda) -> None:
                # Lambda bodies have their own argument scope and are checked by Python at runtime.
                return

            def visit_Name(self, node: ast.Name) -> None:
                if isinstance(node.ctx, (ast.Store, ast.Del)):
                    defined.add(node.id)
                elif isinstance(node.ctx, ast.Load):
                    loaded.add(node.id)

            def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
                if isinstance(node.name, str):
                    defined.add(node.name)
                for statement in node.body:
                    self.visit(statement)

        visitor = ScopeVisitor()
        for statement in function.body:
            visitor.visit(statement)
        return defined, loaded

    scope_cache: dict[ast.AST, tuple[set[str], set[str]]] = {}

    def cached_scope_names(
        function: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> tuple[set[str], set[str]]:
        if function not in scope_cache:
            scope_cache[function] = scope_names(function)
        return scope_cache[function]

    missing: set[str] = set()
    for function in (
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        local_defined, loaded = cached_scope_names(function)
        lexical_defined: set[str] = set()
        ancestor = parents.get(function)
        while ancestor is not None and not isinstance(ancestor, ast.Module):
            if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)):
                ancestor_defined, _ = cached_scope_names(ancestor)
                lexical_defined.update(ancestor_defined)
            ancestor = parents.get(ancestor)
        missing.update(loaded - local_defined - lexical_defined - allowed)
    return sorted(missing)


def _assigned_python_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
            names.add(child.id)
    return names


def _audit_professional_constraints(
    content: str,
    contract: dict[str, Any],
    *,
    source_artifact: str = "assistant-output.md",
    infer_structured_section: bool = False,
) -> list[dict[str, Any]]:
    issues = _audit_typed_professional_claims(
        content,
        contract,
        source_artifact=source_artifact,
        infer_structured_section=infer_structured_section,
    )
    for constraint in contract.get("professional_constraints") or []:
        if not isinstance(constraint, dict):
            continue
        if str(constraint.get("id") or "") in {"iscsi_login_response_opcode"}:
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
                correction_context = _professional_correction_window(
                    content, conflict.start(), conflict.end()
                )
                if _matches_professional_correction(
                    statement, constraint
                ) or _matches_professional_correction(correction_context, constraint):
                    continue
                conflict_found = True
                break
            if not conflict_found:
                continue
            statement = _professional_statement_window(
                content, conflict.start(), conflict.end()
            ).strip()
            headings = _professional_heading_context(content, conflict.start())
            issues.append(
                _issue(
                    "professional_fact_conflict",
                    (
                        _professional_section_artifact(
                            content,
                            conflict.start(),
                            fallback=source_artifact,
                        )
                        if infer_structured_section
                        else source_artifact
                    ),
                    "交付件与已验证的领域事实冲突：" + str(constraint.get("assertion") or ""),
                    constraint_id=str(constraint.get("id") or ""),
                    evidence=[str(item) for item in constraint.get("evidence") or []],
                    conflicting_excerpt=statement[:800],
                    section_heading=(headings[-1] if headings else ""),
                )
            )
            break
    return issues


def _audit_typed_professional_claims(
    content: str,
    contract: dict[str, Any],
    *,
    source_artifact: str,
    infer_structured_section: bool,
) -> list[dict[str, Any]]:
    """Validate claims whose meaning depends on command role or assertion polarity."""
    constraint_by_id = {
        str(item.get("id") or ""): item
        for item in contract.get("professional_constraints") or []
        if isinstance(item, dict)
    }
    response_constraint = constraint_by_id.get("iscsi_login_response_opcode")
    if not isinstance(response_constraint, dict):
        return []

    issues: list[dict[str, Any]] = []

    def artifact_for(position: int) -> str:
        if not infer_structured_section:
            return source_artifact
        return _professional_section_artifact(
            content,
            position,
            fallback=source_artifact,
        )

    tcpdump_commands = list(
        re.finditer(
            r"(?im)\b(tcpdump\b[^`\n；;]*)",
            content,
        )
    )
    tcpdump_protocol_field_spans: list[tuple[int, int]] = []
    for command in tcpdump_commands:
        command_text = command.group(1).strip()
        if not re.search(r"\biscsi\.[a-z0-9_.]+", command_text, flags=re.IGNORECASE):
            continue
        tcpdump_protocol_field_spans.append((command.start(1), command.end(1)))
        issues.append(
            _issue(
                "invalid_capture_filter",
                artifact_for(command.start()),
                "tcpdump 使用 BPF capture filter，不能直接解析 iscsi.opcode 等协议字段；请仅按 TCP/端口抓包，再用 tshark -Y 解析 iSCSI 字段。",
                claim_type="command_executability",
                validator_layer="L3",
                command=command_text[:800],
            )
        )

    incorrect_response_claims: list[re.Match[str]] = []
    incorrect_response_claims.extend(
        re.finditer(
            r"(?im)tshark\b[^\n；;]*-Y\s+[^\n；;]*iscsi\.opcode\s*==\s*0x0?3(?![0-9a-f])"
            r"[^\n；;]*(?:iscsi\.login[_a-z.]*status|statusclass|statusdetail)",
            content,
        )
    )
    incorrect_response_claims.extend(
        re.finditer(
            r"(?i)(?:login response|登录响应|抓取.{0,30}响应).{0,180}"
            r"iscsi\.opcode\s*==\s*0x0?3(?![0-9a-f])",
            content,
        )
    )
    seen_positions: set[int] = set()
    for conflict in sorted(incorrect_response_claims, key=lambda item: item.start()):
        opcode_match = re.search(
            r"iscsi\.opcode\s*==\s*0x0?3(?![0-9a-f])",
            conflict.group(0),
            flags=re.IGNORECASE,
        )
        opcode_position = (
            conflict.start() + opcode_match.start()
            if opcode_match is not None
            else conflict.start()
        )
        if any(start <= opcode_position < end for start, end in tcpdump_protocol_field_spans):
            continue
        if conflict.start() in seen_positions:
            continue
        seen_positions.add(conflict.start())
        statement = _professional_statement_window(
            content,
            conflict.start(),
            conflict.end(),
        ).strip()
        issues.append(
            _issue(
                "professional_fact_conflict",
                artifact_for(conflict.start()),
                "交付件与已验证的领域事实冲突："
                + str(response_constraint.get("assertion") or ""),
                constraint_id="iscsi_login_response_opcode",
                claim_type="protocol_constant",
                validator_layer="L1",
                expected_value="0x23",
                observed_value="0x03",
                evidence=[
                    str(item) for item in response_constraint.get("evidence") or []
                ],
                conflicting_excerpt=statement[:800],
            )
        )
    return issues


def _professional_section_artifact(
    content: str,
    conflict_start: int,
    *,
    fallback: str,
) -> str:
    """Map a combined-report finding back to the structured stage that produced it."""
    for heading in reversed(_professional_heading_context(content, conflict_start)):
        lowered = heading.lower()
        if "sfmea" in lowered or re.search(r"\bfmea\b", lowered):
            return "sfmea.json"
        if "黑盒" in lowered or "black-box" in lowered or "black box" in lowered:
            return "black_box_cases.json"
        if "流程" in lowered or "flow" in lowered:
            return "business_flow.md"
    return fallback


def _professional_heading_context(content: str, conflict_start: int) -> list[str]:
    """Return Markdown headings before a finding, from document root to leaf."""
    headings = list(
        re.finditer(
            r"(?m)^\s*(#{1,6})\s+([^\n]+?)\s*$",
            content[: max(0, conflict_start)],
        )
    )
    if not headings:
        return []
    hierarchy: list[tuple[int, str]] = []
    for match in headings:
        level = len(match.group(1))
        while hierarchy and hierarchy[-1][0] >= level:
            hierarchy.pop()
        hierarchy.append((level, match.group(2).strip()))
    return [heading for _, heading in hierarchy]


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


def _professional_correction_window(content: str, start: int, end: int) -> str:
    """Include one neighboring line so nearby qualifiers can disambiguate a claim."""
    line_start = content.rfind("\n", 0, start) + 1
    previous_start = content.rfind("\n", 0, max(0, line_start - 1)) + 1
    line_end = content.find("\n", end)
    if line_end < 0:
        line_end = len(content)
    next_end = content.find("\n", min(len(content), line_end + 1))
    if next_end < 0:
        next_end = len(content)
    return content[previous_start:next_end]


def _matches_professional_correction(statement: str, constraint: dict[str, Any]) -> bool:
    constraint_id = str(constraint.get("id") or "")
    stable_correction_patterns = {
        "iscsi_chap_security_stage": (
            r"csg\s*[:=]?\s*1.{0,80}(?:不应|不该|不得|不能).{0,40}"
            r"(?:operational negotiation|操作协商).{0,60}(?:chap|challenge|response)"
        ),
        "iscsi_unknown_user_test_mapping_scope": (
            r"(?:"
            r"(?:不要|不得|不能|不可).{0,20}(?:把|将).{0,20}"
            r"`?chap_discovery\.sh`?.{0,30}(?:当成|作为|视为).{0,50}(?:未知|unknown|chap_n)"
            r"|(?:未知|unknown).{0,40}(?:chap_n|用户|user).{0,80}(?:误认为|误当).{0,40}覆盖"
            r".{0,320}(?:禁止|不得|不能|不).{0,20}映射.{0,40}(?:`?chap_discovery\.sh`?|到.{0,20}chap_discovery\.sh)"
            r")"
        ),
        "iscsi_redirection_mapping_scope": (
            r"(?:"
            r"redirect.{0,80}(?:被|遭)?(?:误当|误判|错误地?认为|错误地?视为)"
            r".{0,80}(?:网络故障|网络中断|自动重连|自动恢复)"
            r"(?=.{0,400}login_redirection\.sh)"
            r"(?=.{0,400}(?:仅|只).{0,40}(?:验证|覆盖|证明).{0,80}(?:受控\s*rpc|redirect))"
            r"|login_redirection\.sh.{0,100}(?:不证明|不能证明).{0,80}"
            r"(?:网络故障|网络中断|自动重连|自动恢复)"
            r"|(?:redirect|重定向).{0,120}(?:网络断开|网络故障).{0,80}"
            r"(?:分为|区分|分成).{0,80}(?:不复用|不能复用|不得复用).{0,60}"
            r"(?:redirect|重定向).{0,30}(?:结果|覆盖).{0,40}(?:证明|代替).{0,40}"
            r"(?:网络故障|网络断开|恢复)"
            r")"
        ),
        "iscsi_calsoft_mapping_scope": (
            r"(?:不要|不得|不能|不可).{0,20}(?:使用|用).{0,40}"
            r"`?calsoft\.py`?.{0,100}(?:推导|推出|证明|作为).{0,60}"
            r"(?:login|登录).{0,40}(?:latency|延迟)"
        ),
        "iscsi_login_error_c_flag_preserved": (
            r"(?:源码)?(?:未|不会|没有).{0,40}(?:清除|clear).{0,10}c(?:\s*bit)?"
            r".{0,100}(?:不能|不得|不可).{0,30}(?:写成|声称).{0,30}清除.{0,20}t/c/csg/nsg"
        ),
        "iscsi_login_error_flags_cleared": (
            r"(?:认证失败|authentication failure|error response|错误响应|失败响应).{0,120}"
            r"t\s*[:=]\s*1.{0,120}(?:不传播|does not propagate).{0,120}"
            r"(?:error response|错误响应|失败响应).{0,120}t\s*[:=]\s*0"
            r"|(?:error response|错误响应|失败响应).{0,160}"
            r"t\s*[:=]\s*0.{0,100}csg\s*[:=]\s*0.{0,100}nsg\s*[:=]\s*0"
        ),
        "iscsi_csg_values": (
            r"csg\s*0/1/3.{0,20}分别为.{0,40}security negotiation"
            r".{0,40}operational negotiation.{0,40}full feature phase"
        ),
        "iscsi_unknown_key_not_understood": (
            r"未知.{0,20}格式合法.{0,80}(?:不能|不得|不可).{0,30}(?:当成|写成).{0,30}parse failure"
        ),
        "iscsi_invalid_login_request_detail": (
            r"(?:"
            r"(?:误报|误写|不能写成).{0,30}0x0b.{0,100}(?:断言|实际|实现).{0,30}(?:detail\s*)?0x00"
            r"|(?:断言|预期|expect).{0,30}(?:detail\s*)?0x00.{0,120}"
            r"(?:若|如果|if).{0,30}0x0b.{0,80}(?:测试期望错误|不是实现事实|非实现事实)"
            r")"
        ),
        "iscsi_full_feature_request_rejected": (
            r"csg\s*[:=]?\s*3.{0,80}(?:非法|拒绝|invalid|reject).{0,100}"
            r"(?:不把|不能|不得|不可|not).{0,60}(?:进入|当作|视为|作为).{0,80}"
            r"(?:full feature|合法迁移|阶段迁移)"
        ),
        "iscsi_login_version_offsets": (
            r"(?:"
            r"(?:bhs\s*)?byte(?:s)?\s*2/3.{0,40}(?:版本|version).{0,320}"
            r"(?:避免|不能|不得|不可).{0,30}(?:payload\s*)?bytes?\s*40[-–]41"
            r"|(?:不要|避免|不能|不得|不可).{0,30}(?:payload\s*)?bytes?\s*40[-–]41"
            r".{0,100}(?:版本|version).{0,40}(?:bhs\s*)?byte(?:s)?\s*2/3"
            r")"
        ),
        "iscsi_fuzzer_skips_login_opcode": (
            r"iscsi_fuzz\.c.{0,80}(?:不是|不作为|不能作为).{0,80}(?:随机|非法).{0,40}login request.{0,40}(?:覆盖|证明)"
        ),
        "iscsi_perf_scripts_not_login_latency": (
            r"不从.{0,30}(?:fio|perf).{0,30}(?:外推|推导).{0,30}(?:login|登录).{0,20}(?:latency|延迟)"
        ),
        "iscsi_multiconnection_mapping_scope": (
            r"multiconnection\.sh.{0,80}(?:仅|只).{0,30}(?:作|作为).{0,60}参考"
            r".{0,80}(?:不证明|不能证明).{0,100}(?:非零\s*tsih|不同\s*cid|同一\s*session)"
        ),
        "iscsi_unit_coverage_scope": (
            r"iscsi_ut\.c.{0,360}(?:不能笼统(?:声称|宣称)|不得笼统(?:声称|宣称)|"
            r"(?:无法|不能)确认.{0,80}(?:覆盖|断言)|must not claim)"
            r".{0,220}(?:target removed|authorization failure|错误响应|所有.*(?:错误|失败))"
        ),
    }
    stable_pattern = stable_correction_patterns.get(constraint_id)
    if stable_pattern and re.search(
        stable_pattern,
        statement,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        return True
    if constraint_id == "iscsi_csg_values" and _has_correct_csg_stage_mapping(statement):
        return True
    if constraint_id == "iscsi_unit_coverage_scope":
        lowered = statement.lower()
        if (
            "iscsi_ut.c" in lowered
            and (
                "不能笼统" in statement
                or "不得笼统" in statement
                or ("无法确认" in statement or "不能确认" in statement)
                and any(term in lowered for term in ("覆盖", "断言"))
            )
            and any(term in lowered for term in ("target removed", "authorization failure", "错误响应", "login 失败"))
        ):
            return True
    if (
        constraint_id == "iscsi_login_negotiation_transport"
        and _is_post_login_text_request_claim(statement)
    ):
        return True
    if constraint_id == "iscsi_login_negotiation_transport" and re.search(
        r"(?:"
        r"(?:discovery\s+)?login.{0,100}(?:成功|完成|进入).{0,80}full feature phase.{0,40}(?:后|之后|then|after).{0,120}(?:text request|sendtargets)"
        r"|(?:text request|sendtargets).{0,160}(?:仅|只|only).{0,80}(?:登录成功后|full feature phase 后|after login|after full feature)"
        r")",
        statement,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        return True
    if constraint_id == "iscsi_login_negotiation_transport" and re.search(
        r"(?:不处理|不属于|并非).{0,80}(?:text request|sendtargets).{0,120}"
        r"(?:full feature phase|full feature|全功能阶段).{0,40}(?:后|之后|after)",
        statement,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        return True
    if constraint_id == "iscsi_discovery_target_address" and re.search(
        r"discovery\s+login.{0,160}(?:不会|不返回|不包含|不追加|does not|will not|must not)"
        r".{0,16}`?targetaddress",
        statement,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        return True
    if re.match(
        r'^\s*[\[{]?\s*["\']?(?:cause|failure_mode)["\']?\s*:',
        statement,
        flags=re.IGNORECASE,
    ) and re.search(
        r"(?:误当|误判|错误地?认为|错误地?声称|或声称|把.{0,120}(?:写成|当成|作为)|使用.{0,120}作为|"
        r"mistaken(?:ly)?|wrongly|incorrectly|treat.{0,80}as|use.{0,80}as)",
        statement,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        return True
    for pattern in constraint.get("correction_patterns") or []:
        try:
            if re.search(str(pattern), statement, flags=re.IGNORECASE | re.DOTALL):
                return True
        except re.error:
            continue
    return False


def _has_correct_csg_stage_mapping(statement: str) -> bool:
    text = str(statement or "")

    def mapped(stage_pattern: str, value: str) -> bool:
        same_clause = r"[^\n。！？；;，,]"
        return bool(
            re.search(
                rf"(?:{stage_pattern}){same_clause}{{0,30}}csg\s*[:=]?\s*{value}\b"
                rf"|csg\s*[:=]?\s*{value}\b{same_clause}{{0,30}}(?:{stage_pattern})",
                text,
                flags=re.IGNORECASE | re.DOTALL,
            )
        )

    return mapped(r"security\s*negotiation|安全协商", "0") and mapped(
        r"operational\s*negotiation|操作协商", "1"
    )


def _is_post_login_text_request_claim(statement: str) -> bool:
    lower = str(statement or "").lower()
    has_followup_request = "text request" in lower or "sendtargets" in lower
    has_post_login_scope = any(
        marker in lower
        for marker in (
            "登录完成后",
            "登录成功后",
            "login 完成后",
            "full feature phase 后",
            "进入 full feature phase 后",
            "after login",
            "after discovery login",
            "post-login",
        )
    )
    excludes_login_pdu = any(
        marker in lower
        for marker in (
            "不属于 login pdu",
            "不是 login pdu",
            "并非 login pdu",
            "outside the login pdu",
            "not part of the login pdu",
        )
    )
    # A module-scope exclusion such as "不包含 Text Request 处理（进入 Full
    # Feature Phase 后）" is the same semantic boundary. It is not claiming
    # that Text Request participates in Login; requiring one particular
    # English/Chinese negation phrase here made a correct scope statement look
    # like a protocol contradiction.
    excludes_login_pdu = excludes_login_pdu or (
        "不包含" in lower and "text request" in lower
    )
    return has_followup_request and has_post_login_scope and excludes_login_pdu


def _has_combined_iscsi_scenario(
    *,
    label: str,
    content: str,
    fallback_patterns: tuple[str, ...],
) -> bool:
    if label != "首 payload 后 timer 注销":
        return any(
            re.search(pattern, content, flags=re.IGNORECASE | re.DOTALL)
            for pattern in fallback_patterns
        )

    lower = str(content or "").lower()
    has_first_payload_scope = bool(
        re.search(
            r"(?:first|首个|第一个|首).{0,40}(?:login\s*)?pdu"
            r"|(?:first|首个|第一个|首).{0,40}(?:login\s*)?payload",
            lower,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )
    timer_is_disabled = (
        "login_timer disabled" in lower
        or "login timer disabled" in lower
        or bool(
            re.search(
                r"(?:"
                r"(?:login[_ ]timer|login timer|登录定时器).{0,80}"
                r"(?:注销|未重新注册|unregister|not re[- ]?armed|cancel|disabled)"
                r"|(?:注销|unregister|cancel|disabled).{0,80}"
                r"(?:login[_ ]timer|login timer|登录定时器)"
                r")",
                lower,
                flags=re.IGNORECASE | re.DOTALL,
            )
        )
    )
    return has_first_payload_scope and timer_is_disabled


def _combined_response_evidence_paths(content: str) -> list[str]:
    candidates = re.findall(
        r"`((?:app|lib|module|src|test|tests)/[^`\s:]+)(?::\d+(?:-\d+)?)?`",
        str(content or ""),
        flags=re.IGNORECASE,
    )
    normalized = _unique_strings(
        re.sub(r":L?\d+(?:-L?\d+)?$", "", path.rstrip(".,;，。；"), flags=re.IGNORECASE)
        for path in [*candidates, *_markdown_repo_paths(str(content or ""))]
        if not any(marker in path for marker in "*?[]")
    )
    # Narrative text may mention a basename (for example, "in conn.c") after
    # already citing `lib/iscsi/conn.c`.  Treat that prose echo as the same
    # evidence rather than a fictitious repository-root path.
    qualified_basenames = {Path(path).name for path in normalized if "/" in path}
    return [
        path
        for path in normalized
        if "/" in path or Path(path).name not in qualified_basenames
    ]


def _is_labeled_unverified_proposal(content: str, path: str) -> bool:
    for line in str(content or "").splitlines():
        if path not in line:
            continue
        lower = line.lower()
        if "ai_suggested_unverified" in lower and re.search(
            r"(?:\badd\b|新增|待新增|proposed|to be created)",
            lower,
            flags=re.IGNORECASE,
        ):
            return True
        if re.search(
            r"(?:待补(?:证据)?|证据缺口|尚未验证|未验证|无已验证证据|"
            r"不在[^\n]{0,40}(?:白名单|allowlist)|"
            r"evidence\s+gap|not\s+in[^\n]{0,40}allowlist)",
            lower,
            flags=re.IGNORECASE,
        ):
            return True
        if re.search(
            rf"(?:删除|移除|不再把|不再使用|removed?|do not use).{{0,120}}{re.escape(path.lower())}.{{0,160}}(?:证据|映射|test_mapping|引用|evidence|mapping)",
            lower,
            flags=re.IGNORECASE,
        ):
            return True
    return False


def _looks_like_runtime_generated_path(path: str) -> bool:
    candidate = Path(str(path or ""))
    generated_suffixes = {".csv", ".html", ".json", ".log", ".xml", ".xlsx"}
    generated_markers = {"artifact", "artifacts", "output", "outputs", "report", "reports", "result", "results"}
    return candidate.suffix.lower() in generated_suffixes and any(
        any(marker in part.lower() for marker in generated_markers)
        for part in candidate.parts[:-1]
    )


def _matched_profiles(text: str) -> list[str]:
    explicit_profile_terms = (
        ("iscsi_login", ("iscsi",)),
        ("nvmeof_transport", ("nvmeof", "nvme-o-f", "nvme-of", "nvmf")),
        ("bdev_io", ("bdev",)),
        ("rpc_config", ("jsonrpc", "json-rpc", "rpc config", "rpc/config")),
        ("reactor_thread_poller", ("reactor", "poller")),
        ("persistence_recovery", ("blobstore", "ftl")),
    )
    explicit = [
        profile_id
        for profile_id, terms in explicit_profile_terms
        if any(_term_matches(text, term) for term in terms)
    ]
    if "iscsi_login" in explicit and "bdev_io" in explicit and not re.search(
        r"(?:针对|分析|梳理|覆盖|设计)\s*(?:spdk\s*)?bdev\b|"
        r"\bbdev\s*(?:io|模块|子系统|路径|流程|sfmea|测试设计|black box|test design)",
        str(text or ""),
        flags=re.IGNORECASE,
    ):
        explicit.remove("bdev_io")
    if explicit:
        if _term_matches(text, "tls") and "security_tls" not in explicit:
            explicit.append("security_tls")
        if "nvmeof_transport" in explicit and any(
            _term_matches(text, term) for term in ("tcp", "network", "packet loss")
        ):
            explicit.append("tcp_network")
        return explicit

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
    declared_templates = _declared_output_templates(outputs)
    requested = list(declared_templates)
    if any(
        str(item.get("type") or "").strip().lower() == "combined_test_report"
        for item in outputs
        if isinstance(item, dict)
    ):
        return _unique_strings(requested)
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
            if artifact == "test_design.md" and "test_design_mindmap.md" in requested:
                continue
            if artifact == "black_box_cases.json" and any(
                item in requested for item in ("black_box_cases.json", "black_box_cases.md")
            ):
                continue
            if artifact == "business_flow.md" and any(
                item in requested for item in ("business_flow.md", "flow_map.md")
            ):
                continue
            requested.append(artifact)
    return _unique_strings(requested or ["business_flow.md", "sfmea.json", "black_box_cases.json"])


def _declared_output_templates(
    outputs: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    declared: dict[str, dict[str, Any]] = {}
    for item in outputs:
        if not isinstance(item, dict):
            continue
        artifact = str(item.get("artifact") or item.get("path") or "").strip()
        if not artifact:
            continue
        template: dict[str, Any] | None = None
        if artifact in ARTIFACT_TEMPLATES:
            template = dict(ARTIFACT_TEMPLATES[artifact])
        elif str(item.get("type") or "").strip().lower() == "combined_test_report":
            template = dict(ARTIFACT_TEMPLATES["combined_test_report.md"])
        if template is None:
            continue
        for key in (
            "min_sfmea_rows",
            "min_black_box_cases",
            "required_evidence_terms",
            "required_terms",
            "forbidden_evidence_path_prefixes",
            "forbidden_claim_terms",
        ):
            if key.startswith("min_") and isinstance(item.get(key), int):
                template[key] = max(1, int(item[key]))
            elif isinstance(item.get(key), list):
                template[key] = [
                    str(value) for value in item[key] if str(value).strip()
                ]
        declared[artifact] = template
    return declared


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
    if isinstance(template.get("field_rules"), dict):
        payload["field_rules"] = dict(template["field_rules"])
    for key in (
        "required_evidence_terms",
        "required_terms",
        "forbidden_evidence_path_prefixes",
        "forbidden_claim_terms",
    ):
        if isinstance(template.get(key), list):
            payload[key] = [
                str(value) for value in template[key] if str(value).strip()
            ]
    if str(template.get("required_mermaid_diagram") or "").strip():
        payload["required_mermaid_diagram"] = str(
            template["required_mermaid_diagram"]
        ).strip()
    for key in (
        "min_sfmea_rows",
        "min_black_box_cases",
        "min_source_paths",
        "min_test_paths",
    ):
        if template.get(key) is not None:
            payload[key] = int(template[key])
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
    # The deterministic mind-map publisher emits a graph document rather than
    # a tabular JSON artifact. Its stable payload is ``{nodes: [...]}``.
    if artifact == "test_design_mindmap.json" and isinstance(payload, dict):
        rows = payload.get("nodes")
    else:
        rows = payload if isinstance(payload, list) else payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return [_issue("json_shape_invalid", artifact, f"{artifact} 必须是数组或包含 items 数组")]
    if not rows:
        return [_issue("empty_json_items", artifact, f"{artifact} 没有任何可交付条目")]
    issues: list[dict[str, Any]] = []
    if artifact == "flow_cards.json" and isinstance(payload, dict):
        flow_gaps = "\n".join(
            str(item) for item in payload.get("gaps") or [] if str(item).strip()
        )
        # A deliberately scoped local-component delivery may only state the
        # verified component boundary.  It is not an end-to-end flow, but it
        # is a valid downgraded artifact when that scope is explicit to users.
        explicit_local_component_scope = (
            str(payload.get("status") or "").strip() == "local_component_analysis"
            and bool(re.search(r"(?:本次|仅).{0,20}局部分量分析|local[ _-]?component", flow_gaps, re.IGNORECASE))
        )
        if (
            str(payload.get("status") or "").strip().upper() == "PARTIAL"
            and not explicit_local_component_scope
        ):
            issues.append(
                _issue(
                    "flow_incomplete_for_delivery",
                    artifact,
                    "流程台账仍为 PARTIAL；正常流程之外的异常、状态、资源或传播路径尚缺真实证据，不能作为完整测试活动交付。",
                    gaps=list(payload.get("gaps") or []),
                )
            )
        if not explicit_local_component_scope and re.search(
            r"(?:不能证明|无法证明).{0,40}(?:端到端|完整|单一).{0,40}(?:流程|顺序|调用链)",
            flow_gaps,
            flags=re.IGNORECASE,
        ):
            issues.append(
                _issue(
                    "flow_evidence_not_connected",
                    artifact,
                    "流程证据仍是互不连通的分量，不能作为端到端业务流程交付；"
                    "需要补充入口到终态的已验证调用链，或明确将本次交付降级为局部分量分析",
                )
            )
        for index, flow in enumerate(rows, start=1):
            if not isinstance(flow, dict):
                continue
            normal_path = flow.get("normal_path")
            abnormal_paths = flow.get("abnormal_paths")
            if (
                str(flow.get("status") or "").upper() == "READY"
                and isinstance(normal_path, list)
                and normal_path
                and (not isinstance(abnormal_paths, list) or not abnormal_paths)
            ):
                issues.append(
                    _issue(
                        "flow_missing_abnormal_paths",
                        artifact,
                        f"flow_cards.json 第 {index} 个 READY 流程已声明正常路径，却没有任何异常路径；"
                        "应明确超时、拒绝、断连或其他已验证异常分支，或将状态降级为局部分析。",
                        index=index,
                        row_id=str(flow.get("flow_id") or ""),
                    )
                )
    minimum_rows = int(
        spec.get(
            "min_sfmea_rows" if artifact == "sfmea.json" else "min_black_box_cases"
        )
        or 0
    )
    if minimum_rows and len(rows) < minimum_rows:
        label = "SFMEA 风险项" if artifact == "sfmea.json" else "黑盒测试用例"
        issues.append(
            _issue(
                "insufficient_sfmea_rows"
                if artifact == "sfmea.json"
                else "insufficient_black_box_cases",
                artifact,
                f"{artifact} {label}不足: {len(rows)}/{minimum_rows}",
                actual=len(rows),
                required=minimum_rows,
            )
        )
    required_fields = [str(item) for item in spec.get("required_fields") or []]
    seen_case_ids: set[str] = set()
    seen_case_signatures: set[str] = set()
    observed_dimensions: set[str] = set()
    sfmea_mitigation_rows: dict[str, list[tuple[str, str]]] = {}
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            issues.append(_issue("json_item_invalid", artifact, f"{artifact} 第 {index} 项不是对象"))
            continue
        row_issue_start = len(issues)
        row_id = str(
            row.get("sfmea_id")
            or row.get("case_id")
            or row.get("risk_id")
            or row.get("id")
            or f"row-{index}"
        ).strip()
        missing = [
            field for field in required_fields
            if not _field_present(row, field)
        ]
        if missing:
            code = "missing_sfmea_fields" if artifact == "sfmea.json" else "missing_black_box_fields"
            issues.append(_issue(code, artifact, f"{artifact} 第 {index} 项缺少字段: {', '.join(missing)}", index=index, fields=missing))
        if artifact.startswith("black_box") and _black_box_boundary_violation(row):
            issues.append(_issue("black_box_boundary_violation", artifact, f"{artifact} 第 {index} 项混入内部函数调用或修改源码步骤", index=index))
        if artifact.startswith("black_box") and not _black_box_expected_result_is_observable(
            row.get("expected_result") or row.get("expected")
        ):
            issues.append(
                _issue(
                    "black_box_expected_result_ambiguous",
                    artifact,
                    f"{artifact} 第 {index} 项的预期结果缺少可观测状态、日志、指标或退出码语义",
                    index=index,
                )
            )
        if artifact.startswith("black_box") and _unsafe_destructive_test_step(row):
            issues.append(
                _issue(
                    "unsafe_destructive_test_step",
                    artifact,
                    f"{artifact} 第 {index} 项包含未隔离、未确认目标的破坏性设备写入",
                    index=index,
                )
            )
        if artifact == "sfmea.json":
            issues.extend(_audit_sfmea_scores(row, artifact=artifact, index=index))
            issues.extend(_audit_sfmea_mitigation(row, artifact=artifact, index=index))
            issues.extend(_audit_sfmea_occurrence_basis(row, artifact=artifact, index=index))
            mitigation = re.sub(r"\s+", " ", str(row.get("mitigation") or "").strip())
            if mitigation:
                sfmea_mitigation_rows.setdefault(mitigation, []).append(
                    (row_id, str(row.get("failure_mode") or "").strip())
                )
            risk_status = str(row.get("risk_status") or "").strip()
            risk_text = " ".join(
                str(row.get(field) or "")
                for field in ("failure_mode", "cause", "effect")
            )
            # A qualified test hypothesis is deliberately not an observed defect,
            # but it is still a scored risk candidate.  Do not turn its explicit
            # "待验证" wording into a deletion instruction for finalization.
            # A risk hypothesis is allowed to compare external error outcomes
            # (for example, "session not found" versus "connection add failed").
            # Do not let the generic absence/negation lint mistake that comparison
            # for a claim that the implementation has no error path.  The separate
            # qualification check below still rejects a bare or invented hypothesis.
            explicit_test_hypothesis = bool(
                risk_status == "test_hypothesis"
                and re.search(
                    r"(?:风险|故障注入|失效)假设\s*[:：]|待验证\s*[:：]",
                    " ".join(
                        (
                            risk_text,
                            str(row.get("mechanism") or ""),
                            str(row.get("evidence_interpretation") or ""),
                        )
                    ),
                    flags=re.IGNORECASE,
                )
            )
            if not explicit_test_hypothesis and (
                not sfmea_failure_mode_is_risk(row.get("failure_mode"))
                or re.search(
                r"(?:当前|该)?源码.{0,8}不支持.{0,20}(?:failure\s*mode|失效|风险)"
                r"|待验证\s*[:：]"
                r"|无法从.{0,40}(?:推导|证明|确认)"
                r"|(?:该|此)(?:路径|片段|上下文).{0,20}(?:未显示|未见).{0,30}(?:泄漏|缺陷|失效|风险)"
                r"|源码.{0,16}不支持.{0,40}(?:泄漏|缺陷|失效|风险|错误|EINVAL)"
                r"|是否存在.{0,40}(?:需|需要).{0,20}(?:验证|确认)"
                r"|不会.{0,12}使用该指针"
                r"|不会(?:发生|导致|造成|出现|触发|产生|访问).{0,16}(?:use-after-free|泄漏|崩溃|失效|缺陷|错误)"
                r"|(?:不存在|没有).{0,24}(?:use-after-free|泄漏|崩溃|失效|缺陷|错误|使用该指针)",
                risk_text,
                flags=re.IGNORECASE,
                )
            ):
                issues.append(
                    _issue(
                        "non_risk_sfmea_row",
                        artifact,
                        f"{artifact} 第 {index} 项描述的是被否定或待验证的假设，不是可评分失效模式",
                        index=index,
                        row_id=row_id,
                        risk_status=risk_status,
                    )
                )
            interpretation = str(row.get("evidence_interpretation") or "").strip()
            mechanism_text = str(row.get("mechanism") or "").strip()
            if risk_status == "test_hypothesis":
                hypothesis_text = " ".join((interpretation, mechanism_text, str(row.get("cause") or "")))
                if not re.search(
                    r"(?:风险|故障注入|失效)假设|(?:若|当).{0,80}(?:失效|未|错误|异常|超限|竞态|泄漏)",
                    hypothesis_text,
                    flags=re.IGNORECASE,
                ):
                    issues.append(
                        _issue(
                            "unqualified_sfmea_risk_hypothesis",
                            artifact,
                            f"{artifact} 第 {index} 项标记为风险假设，却没有说明需要通过故障注入验证的偏离条件",
                            index=index,
                            row_id=row_id,
                        )
                    )
            elif risk_status == "observed_defect":
                claims = row.get("technical_claims") or []
                if not isinstance(claims, list) or not claims:
                    issues.append(
                        _issue(
                            "observed_defect_without_direct_evidence",
                            artifact,
                            f"{artifact} 第 {index} 项声称已观测到缺陷，却没有提供直接技术断言证据",
                            index=index,
                            row_id=row_id,
                        )
                    )
            if re.search(
                r"(?:片段|上下文|声明).{0,12}(?:未显示|未见|没有显示|未提供).{0,30}(?:校验|检查|清理|释放|处理)",
                risk_text,
                flags=re.IGNORECASE,
            ):
                issues.append(
                    _issue(
                        "absence_of_evidence_as_defect",
                        artifact,
                        f"{artifact} 第 {index} 项把当前证据未覆盖误当成已存在缺陷",
                        index=index,
                    )
                )
            evidence_paths = _structured_source_evidence_paths(
                [
                    *(row.get("source_evidence") or []),
                    *(row.get("source_or_test_evidence") or []),
                ]
            )
            if (
                evidence_paths
                and all(_evidence_path_classification(path) == "test" for path in evidence_paths)
                and not re.search(r"测试|test\s+harness|test\s+helper", str(row.get("failure_mode") or ""), re.IGNORECASE)
            ):
                issues.append(
                    _issue(
                        "test_harness_risk_as_product_risk",
                        artifact,
                        f"{artifact} 第 {index} 项只有测试代码证据，却描述为被测产品风险",
                        index=index,
                    )
                )
        if artifact == "black_box_cases.json":
            dimension = str(row.get("test_dimension") or "").strip().lower()
            if dimension:
                observed_dimensions.add(dimension)
            case_text = "\n".join(
                str(row.get(field) or "")
                for field in (
                    "scenario_name",
                    "preconditions",
                    "steps",
                    "expected_result",
                    "oracle_basis",
                    "observability",
                    "failure_diagnostics",
                    "mapped_test_dir",
                )
            )
            lower_case_text = case_text.lower()
            if "multiconnection.sh" in lower_case_text:
                isolated_target = bool(re.search(
                    r"(?:null|malloc)\s*bdev|专用测试盘|隔离测试(?:设备|盘)|允许列表|allowlist|disposable|isolated",
                    lower_case_text,
                ))
                data_loss_warning = bool(re.search(
                    r"数据(?:会|可|可能)?(?:被)?(?:销毁|覆盖)|数据销毁风险|随机写|破坏性|destructive|data loss",
                    lower_case_text,
                ))
                if not (isolated_target and data_loss_warning):
                    issues.append(
                        _issue(
                            "unsafe_hazardous_test_mapping",
                            artifact,
                            f"{artifact} 第 {index} 项映射 multiconnection.sh，却没有同时限定隔离测试设备和提示数据销毁风险",
                            index=index,
                        )
                    )
            login_latency_case = bool(re.search(
                r"(?:login|登录).{0,60}(?:latency|延迟|p50|p95|p99)|(?:latency|延迟|p50|p95|p99).{0,60}(?:login|登录)",
                lower_case_text,
                flags=re.IGNORECASE | re.DOTALL,
            ))
            absolute_latency_oracle = bool(re.search(
                r"(?:<|<=|≤|低于|不超过)\s*\d+(?:\.\d+)?\s*(?:ms|毫秒)",
                str(row.get("expected_result") or row.get("expected") or ""),
                flags=re.IGNORECASE,
            ))
            measured_threshold_basis = bool(re.search(
                r"(?:历史基线|同环境基线|实测基线|基线测得|measured baseline|historical baseline|same[- ]environment baseline)"
                r".{0,120}(?:样本量|sample size|硬件|hardware|commit|revision)",
                lower_case_text,
                flags=re.IGNORECASE | re.DOTALL,
            ))
            if login_latency_case and absolute_latency_oracle and not measured_threshold_basis:
                issues.append(
                    _issue(
                        "ungrounded_performance_threshold",
                        artifact,
                        f"{artifact} 第 {index} 项给出绝对登录延迟阈值，却没有同环境实测基线、样本量和硬件/版本来源；首次运行只能采样建基线",
                        index=index,
                    )
                )
            relative_threshold = bool(re.search(
                r"(?:相对退化|relative regression|regression).{0,80}\d+(?:\.\d+)?\s*%"
                r"|\d+(?:\.\d+)?\s*%.{0,80}(?:相对退化|relative regression|regression)",
                lower_case_text,
                flags=re.IGNORECASE | re.DOTALL,
            ))
            statistical_basis = bool(re.search(
                r"(?:标准差|方差|置信区间|基线波动|bootstrap|stddev|variance|confidence interval)",
                lower_case_text,
                flags=re.IGNORECASE,
            ))
            if relative_threshold and not statistical_basis:
                issues.append(
                    _issue(
                        "missing_performance_statistical_basis",
                        artifact,
                        f"{artifact} 第 {index} 项的相对性能阈值缺少基线方差、标准差或置信区间依据",
                        index=index,
                    )
                )
            for gap in black_box_case_delivery_quality_gaps(
                row,
                repo_path=str(repo),
            ):
                if gap == "missing_test_directory_mapping":
                    issues.append(
                        _issue(
                            gap,
                            artifact,
                            f"{artifact} 第 {index} 项必须映射到仓库内真实测试路径，或明确标记为待新增测试",
                            index=index,
                        )
                    )
                elif gap == "white_box_boundary" and not any(
                    issue.get("code") == "black_box_boundary_violation"
                    and issue.get("index") == index
                    for issue in issues[row_issue_start:]
                ):
                    issues.append(
                        _issue(
                            "black_box_boundary_violation",
                            artifact,
                            f"{artifact} 第 {index} 项混入内部实现或单元测试操作，不是可交付黑盒步骤",
                            index=index,
                        )
                    )
            for gap in black_box_oracle_basis_quality_gaps(row):
                messages = {
                    "missing_oracle_basis": "缺少阈值或时长判据的来源说明",
                    "oracle_basis_not_traceable": "判据来源未映射到源码、配置、规范或环境基线",
                    "missing_performance_sampling_plan": (
                        "性能判据缺少预热、重复采样、P50 和 P95 统计计划"
                    ),
                }
                issues.append(_issue(
                    gap,
                    artifact,
                    f"{artifact} 第 {index} 项{messages[gap]}",
                    index=index,
                    test_dimension=dimension,
                ))
            for gap in black_box_observability_quality_gaps(row):
                issues.append(
                    _issue(
                        gap,
                        artifact,
                        f"{artifact} 第 {index} 项的 RPC 观测点没有声明可验证的公开字段语义；"
                        "请写出实际 RPC 方法和字段名，或改用协议响应、日志、连接结果等外部观测",
                        index=index,
                    )
                )
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
        for issue in issues[row_issue_start:]:
            if isinstance(issue, dict):
                issue.setdefault("row_id", row_id)
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
    if artifact == "sfmea.json":
        for mitigation, rows_with_mitigation in sfmea_mitigation_rows.items():
            distinct_failure_modes = {
                failure_mode for _, failure_mode in rows_with_mitigation if failure_mode
            }
            if len(rows_with_mitigation) < 2 or len(distinct_failure_modes) < 2:
                continue
            issues.append(
                _issue(
                    "duplicate_generic_sfmea_mitigation",
                    artifact,
                    "多个不同失效模式复用了同一 mitigation 模板；必须为每项给出可区分的整改与验证动作",
                    row_ids=[row_id for row_id, _ in rows_with_mitigation],
                    mitigation=mitigation,
                )
            )
    return issues


def _markdown_visible_line_mask(lines: list[str]) -> list[bool]:
    visible: list[bool] = []
    fence_character = ""
    fence_length = 0
    opening_pattern = re.compile(r"^\s{0,3}(?P<fence>`{3,}|~{3,})")
    for line in lines:
        text = line.rstrip("\r\n")
        if not fence_character:
            opening = opening_pattern.match(text)
            if opening is None:
                visible.append(True)
                continue
            fence = opening.group("fence")
            fence_character = fence[0]
            fence_length = len(fence)
            visible.append(False)
            continue
        visible.append(False)
        if re.fullmatch(
            rf"\s{{0,3}}{re.escape(fence_character)}{{{fence_length},}}\s*",
            text,
        ):
            fence_character = ""
            fence_length = 0
    return visible


def _markdown_heading_matches(content: str) -> list[re.Match[str]]:
    pattern = re.compile(
        r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$",
        flags=re.MULTILINE,
    )
    matches: list[re.Match[str]] = []
    lines = content.splitlines(keepends=True)
    visible = _markdown_visible_line_mask(lines)
    offset = 0
    for line, line_visible in zip(lines, visible):
        # A tab or four spaces starts an indented code block.  Source/test
        # excerpts commonly contain shell comments such as ``# Enable ...``;
        # they are not report headings and must not truncate a delivery
        # section during quality auditing.
        if line_visible and _markdown_indentation_columns(line) < 4:
            match = pattern.match(content, offset, offset + len(line))
            if match is not None:
                matches.append(match)
        offset += len(line)
    return matches


def _markdown_without_fenced_blocks(content: str) -> str:
    lines = content.splitlines(keepends=True)
    visible = _markdown_visible_line_mask(lines)
    visible_lines: list[str] = []
    for line, line_visible in zip(lines, visible):
        visible_lines.append(line if line_visible else ("\n" if line.endswith("\n") else ""))
    return "".join(visible_lines)


def _markdown_indentation_columns(line: str) -> int:
    columns = 0
    for character in str(line or ""):
        if character == " ":
            columns += 1
        elif character == "\t":
            columns += 4 - (columns % 4)
        else:
            break
    return columns


def _markdown_without_code_blocks(content: str) -> str:
    lines = content.splitlines(keepends=True)
    visible = _markdown_visible_line_mask(lines)
    visible_lines: list[str] = []
    for line, line_visible in zip(lines, visible):
        is_indented_code = _markdown_indentation_columns(line) >= 4
        visible_lines.append(
            line
            if line_visible and not is_indented_code
            else ("\n" if line.endswith("\n") else "")
        )
    return "".join(visible_lines)


def _markdown_table_cells(line: str) -> list[str]:
    text = line.rstrip("\r\n")
    if _markdown_indentation_columns(text) >= 4:
        return []
    text = text.lstrip(" \t")
    if not text.startswith("|"):
        return []
    cells: list[str] = []
    current: list[str] = []
    code_delimiter_length = 0
    escaped = False
    index = 0
    while index < len(text):
        character = text[index]
        if escaped:
            current.append(character)
            escaped = False
            index += 1
        elif character == "\\":
            escaped = True
            index += 1
        elif character == "`":
            run_end = index + 1
            while run_end < len(text) and text[run_end] == "`":
                run_end += 1
            run_length = run_end - index
            current.append(text[index:run_end])
            if code_delimiter_length == 0:
                if _has_unescaped_backtick_delimiter(
                    text,
                    start=run_end,
                    delimiter_length=run_length,
                ):
                    code_delimiter_length = run_length
            elif run_length == code_delimiter_length:
                code_delimiter_length = 0
            index = run_end
        elif character == "|" and code_delimiter_length == 0:
            cells.append("".join(current).strip())
            current = []
            index += 1
        else:
            current.append(character)
            index += 1
    if escaped:
        current.append("\\")
    cells.append("".join(current).strip())
    if cells and not cells[0]:
        cells.pop(0)
    if cells and not cells[-1]:
        cells.pop()
    return cells


def _has_unescaped_backtick_delimiter(
    text: str,
    *,
    start: int,
    delimiter_length: int,
) -> bool:
    index = start
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] != "`":
            index += 1
            continue
        run_end = index + 1
        while run_end < len(text) and text[run_end] == "`":
            run_end += 1
        if run_end - index == delimiter_length:
            return True
        index = run_end
    return False


def _canonical_black_box_case_id(value: str) -> str:
    return re.sub(r"[-_ ]", "", str(value or "").upper())


def _canonical_sfmea_id(value: str) -> str:
    return re.sub(r"[-_ ]", "", str(value or "").upper())


def _black_box_case_allows_empty_risk_ids(row: dict[str, Any]) -> bool:
    """Permit unlinked cases only for explicitly normal or correct-rejection paths.

    An empty risk link is not a generic escape hatch.  It is useful after a
    validator proves that a generated SFMEA entry actually described normal
    behaviour, but timeout, recovery, capacity and concurrency scenarios still
    need a real risk ledger entry.
    """
    dimension = str(row.get("test_dimension") or "").strip().lower()
    # The generated oracle template may mention recovery and generic source
    # state.  Those words do not turn an explicitly normal journey into a
    # product-risk claim, so classify normal/performance baselines from their
    # user-facing scenario contract before scanning diagnostic prose.
    scenario_contract = "\n".join(
        part
        for field in ("scenario_name", "steps", "expected_result")
        for part in _flatten_text(row.get(field))
    ).lower()
    if dimension == "normal_path" and re.search(
        r"正常(?:路径|流程|登录|请求|行为)|成功路径|full[ _-]?feature",
        scenario_contract,
        flags=re.IGNORECASE,
    ):
        return True
    if dimension == "performance":
        performance_risk_markers = (
            r"回归|regression|超时|timeout|耗尽|泄漏|资源不足|异常|失败|"
            r"恢复|recovery|重连|reconnect|崩溃|挂起|死锁|数据损坏|丢失"
        )
        if not re.search(performance_risk_markers, scenario_contract, flags=re.IGNORECASE):
            return True

    text = "\n".join(
        part
        for field in (
            "test_dimension",
            "scenario_name",
            "preconditions",
            "steps",
            "expected_result",
            "oracle_basis",
            "observability",
            "failure_diagnostics",
        )
        for part in _flatten_text(row.get(field))
    ).lower()
    risk_markers = (
        r"超时|timeout|重连|reconnect|恢复|recovery|并发|concurren|竞态|race|"
        r"耗尽|泄漏|资源不足|资源.*满|资源.*超限|容量.*超限|故障注入|异常传播|"
        r"崩溃|挂起|死锁|数据损坏|丢失|失效|翻转|wraparound"
    )
    if re.search(risk_markers, text, flags=re.IGNORECASE):
        return False
    return bool(
        re.search(
            r"正常(?:路径|流程|登录|请求|行为)|成功路径|"
            r"正确拒绝|预期拒绝|应当拒绝|非法(?:输入|请求).{0,40}(?:拒绝|返回(?:错误|失败))|"
            r"返回(?:参数错误|错误码|失败码)|拒绝(?:非法|无效)",
            text,
            flags=re.IGNORECASE,
        )
    )


def _audit_cross_artifact_references(
    *,
    root: Path,
    declared_artifacts: set[str],
) -> list[dict[str, Any]]:
    sfmea_ids: set[str] = set()
    black_box_ids: set[str] = set()
    sfmea_payload = _read_json(root / "sfmea.json")
    if isinstance(sfmea_payload, list):
        sfmea_ids = {
            _canonical_sfmea_id(str(row.get("sfmea_id") or ""))
            for row in sfmea_payload
            if isinstance(row, dict) and str(row.get("sfmea_id") or "").strip()
        }
    black_box_payload = _read_json(root / "black_box_cases.json")
    if isinstance(black_box_payload, list):
        black_box_ids = {
            _canonical_black_box_case_id(str(row.get("case_id") or ""))
            for row in black_box_payload
            if isinstance(row, dict) and str(row.get("case_id") or "").strip()
        }

    issues: list[dict[str, Any]] = []
    if isinstance(black_box_payload, list):
        for index, row in enumerate(black_box_payload, start=1):
            if not isinstance(row, dict):
                continue
            case_id = str(row.get("case_id") or f"row-{index}").strip()
            risk_ids = row.get("risk_ids")
            if not isinstance(risk_ids, list):
                continue
            risk_id_pairs = [
                (str(risk_id).strip(), _canonical_sfmea_id(risk_id))
                for risk_id in risk_ids
                if str(risk_id or "").strip()
            ]
            if not risk_id_pairs:
                if not _black_box_case_allows_empty_risk_ids(row):
                    issues.append(
                        _issue(
                            "risk_case_missing_sfmea_mapping",
                            "black_box_cases.json",
                            f"black_box_cases.json 第 {index} 项是风险/异常测试，却没有关联 SFMEA 风险项",
                            index=index,
                            row_id=case_id,
                        )
                    )
                continue
            stale_risk_ids = sorted(
                {
                    str(risk_id)
                    for risk_id, normalized in risk_id_pairs
                    if normalized not in sfmea_ids
                }
            )
            if stale_risk_ids:
                issues.append(
                    _issue(
                        "black_box_risk_id_not_found",
                        "black_box_cases.json",
                        f"black_box_cases.json 第 {index} 项引用了不存在的 SFMEA 风险项: {', '.join(stale_risk_ids)}",
                        index=index,
                        row_id=case_id,
                        risk_ids=stale_risk_ids,
                    )
                )
    if isinstance(sfmea_payload, list) and isinstance(black_box_payload, list):
        mapped_risk_ids = {
            normalized
            for row in black_box_payload
            if isinstance(row, dict)
            for risk_id in (row.get("risk_ids") or [])
            if str(risk_id or "").strip()
            for normalized in [_canonical_sfmea_id(str(risk_id))]
        }
        high_risk_ids = [
            str(row.get("sfmea_id") or "").strip()
            for row in sfmea_payload
            if isinstance(row, dict)
            and str(row.get("sfmea_id") or "").strip()
            and int(row.get("rpn") or 0) >= 200
        ]
        unmapped_high_risk_ids = [
            risk_id
            for risk_id in high_risk_ids
            if _canonical_sfmea_id(risk_id) not in mapped_risk_ids
        ]
        if unmapped_high_risk_ids:
            issues.append(
                _issue(
                    "high_risk_sfmea_unmapped",
                    "black_box_cases.json",
                    "以下高 RPN SFMEA 风险项没有关联到任何黑盒用例: "
                    + ", ".join(unmapped_high_risk_ids),
                    unmapped_risk_ids=unmapped_high_risk_ids,
                )
            )
    for artifact in (
        "test_design_mindmap.md",
        "test_design.md",
        "report.md",
    ):
        path = root / artifact
        if artifact not in declared_artifacts or not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        stale: list[str] = []
        for reference in re.findall(
            r"(?i)(?<![A-Z0-9])(?:SFMEA|BLACKBOX|BB)[-_ ]?\d+(?![A-Z0-9])",
            content,
        ):
            display = re.sub(r"\s+", "-", reference.upper())
            normalized = re.sub(r"[-_ ]", "", display)
            if normalized.startswith("SFMEA"):
                if sfmea_ids and normalized not in sfmea_ids:
                    stale.append(display)
            elif black_box_ids and normalized not in black_box_ids:
                stale.append(display)
        stale = sorted(set(stale))
        if stale:
            issues.append(_issue(
                "stale_cross_artifact_reference",
                artifact,
                f"{artifact} 引用了当前交付件中不存在的条目: {', '.join(stale)}",
                references=stale,
            ))
    return issues


def _audit_markdown_evidence_anchors(
    *,
    artifact: str,
    content: str,
    root: Path,
) -> list[dict[str, Any]]:
    cards = _read_json(_artifact_path(root, "evidence_cards.json"))
    if not isinstance(cards, list):
        return []
    ranges = {
        str(card.get("evidence_id") or "").strip(): (
            int(card.get("start_line") or 0),
            int(card.get("end_line") or 0),
            str(card.get("file_path") or ""),
        )
        for card in cards
        if isinstance(card, dict) and str(card.get("evidence_id") or "").strip()
    }
    issues: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for match in re.finditer(
        r"\b(SRC-\d+)\s*:\s*L(\d+)(?:\s*-\s*L?(\d+))?",
        content,
        flags=re.IGNORECASE,
    ):
        evidence_id = match.group(1).upper()
        first_line = int(match.group(2))
        last_line = int(match.group(3) or first_line)
        key = (evidence_id, first_line, last_line)
        if key in seen:
            continue
        seen.add(key)
        expected = ranges.get(evidence_id)
        if expected is None:
            issues.append(
                _issue(
                    "evidence_anchor_unknown",
                    artifact,
                    f"证据锚点不存在: {evidence_id}",
                    evidence_id=evidence_id,
                )
            )
            continue
        start_line, end_line, file_path = expected
        if (
            start_line <= 0
            or end_line < start_line
            or first_line < start_line
            or last_line > end_line
        ):
            issues.append(
                _issue(
                    "evidence_anchor_out_of_range",
                    artifact,
                    (
                        f"证据锚点 {evidence_id}:L{first_line}-L{last_line} 超出证据卡范围 "
                        f"L{start_line}-L{end_line} ({file_path})"
                    ),
                    evidence_id=evidence_id,
                    claimed_lines=f"L{first_line}-L{last_line}",
                    expected_lines=f"L{start_line}-L{end_line}",
                    file_path=file_path,
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
    if artifact in {"flow_map.md", "business_flow.md"} and re.search(
        r"(?:互不连通|不连通|disconnected).{0,80}(?:不能|无法|不足以|cannot|unable).{0,80}(?:端到端|业务顺序|end[- ]to[- ]end)",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        issues.append(
            _issue(
                "disconnected_flow_evidence",
                artifact,
                "调用证据尚未连成可核验的端到端流程，不能作为完整流程交付",
            )
        )
    coverage_claim = re.search(
        r"(?i)(?:完整覆盖|全量覆盖|fully\s+covered|complete\s+coverage)",
        content,
    )
    coverage_claim_is_negated = bool(
        coverage_claim
        and re.search(
            r"(?:禁止|不得|不能|不可|避免|未|不声明|不声称|不宣称)\s*(?:写|声称|宣称|声明|表示)?\s*[\"“']?\s*$",
            content[max(0, coverage_claim.start() - 28):coverage_claim.start()],
            flags=re.IGNORECASE,
        )
    )
    if artifact == "test_strategy.md" and coverage_claim and not coverage_claim_is_negated and re.search(
        r"(?i)(?:待补证据|证据缺口|尚未覆盖|未覆盖|remaining\s+gap|evidence\s+gap)",
        content,
    ):
        issues.append(_issue(
            "unsupported_complete_coverage_claim",
            artifact,
            "测试策略在仍有证据或覆盖缺口时声称完整覆盖",
        ))
    missing_terms = [
        str(term)
        for term in spec.get("required_terms") or []
        if str(term).strip() and str(term) not in content
    ]
    if missing_terms:
        issues.append(
            _issue(
                "missing_required_terms",
                artifact,
                f"{artifact} 缺少必要内容: {', '.join(missing_terms)}",
                terms=missing_terms,
            )
        )
    required_diagram = str(spec.get("required_mermaid_diagram") or "").strip()
    if required_diagram and not re.search(
        rf"```mermaid\s+{re.escape(required_diagram)}\b",
        content,
        flags=re.IGNORECASE,
    ):
        issues.append(
            _issue(
                "missing_mermaid_diagram",
                artifact,
                f"{artifact} 缺少 Mermaid {required_diagram} 图",
            )
        )
    heading_matches = _markdown_heading_matches(content)
    required_sections = [str(item) for item in spec.get("sections") or []]
    section_headings: dict[str, tuple[int, re.Match[str]]] = {}
    for index, match in enumerate(heading_matches):
        normalized = _normalized_markdown_heading(match.group(1))
        section = next(
            (
                required
                for required in required_sections
                if normalized == required
                or re.match(
                    rf"^{re.escape(required)}(?:\s|与|和|及|/|:|：|·)",
                    normalized,
                )
            ),
            None,
        )
        if section is not None and section not in section_headings:
            section_headings[section] = (index, match)
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
        current_level = len(match.group(0).lstrip()) - len(
            match.group(0).lstrip().lstrip("#")
        )
        end = len(content)
        for next_match in heading_matches[index + 1:]:
            next_level = len(next_match.group(0).lstrip()) - len(
                next_match.group(0).lstrip().lstrip("#")
            )
            if next_level <= current_level:
                end = next_match.start()
                break
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

    malformed_table_lines = _malformed_markdown_table_lines(content)
    if malformed_table_lines:
        issues.append(
            _issue(
                "malformed_markdown_table",
                artifact,
                f"{artifact} 包含未闭合或列数不一致的 Markdown 表格行: "
                + ", ".join(str(line) for line in malformed_table_lines),
                lines=malformed_table_lines,
            )
        )

    evidence_paths = _markdown_repo_paths(content)
    claimed_evidence_paths = [
        path
        for path in evidence_paths
        if not _is_labeled_unverified_proposal(content, path)
    ]
    required_evidence_terms = [
        str(value).strip()
        for value in spec.get("required_evidence_terms") or []
        if str(value).strip()
    ]
    missing_evidence_terms = [
        term for term in required_evidence_terms if term.lower() not in content.lower()
    ]
    if missing_evidence_terms:
        issues.append(
            _issue(
                "missing_required_evidence_terms",
                artifact,
                f"{artifact} 缺少关键证据锚点: {', '.join(missing_evidence_terms)}",
                terms=missing_evidence_terms,
            )
        )
    for forbidden_term in (
        str(value).strip()
        for value in spec.get("forbidden_claim_terms") or []
        if str(value).strip()
    ):
        if forbidden_term.lower() in content.lower():
            issues.append(
                _issue(
                    "forbidden_claim_term",
                    artifact,
                    f"{artifact} 包含与已验证证据冲突或无依据的结论: {forbidden_term}",
                    term=forbidden_term,
                )
            )
    forbidden_prefixes = [
        str(value).strip().replace("\\", "/")
        for value in spec.get("forbidden_evidence_path_prefixes") or []
        if str(value).strip()
    ]
    for evidence_path in claimed_evidence_paths:
        if any(evidence_path.startswith(prefix) for prefix in forbidden_prefixes):
            issues.append(
                _issue(
                    "forbidden_evidence_path",
                    artifact,
                    f"{artifact} 包含超出分析范围的证据路径: {evidence_path}",
                    path=evidence_path,
                )
            )
    existing_evidence_paths = [
        path for path in claimed_evidence_paths if _repo_path_exists(repo, path)
    ]
    source_paths = [
        path for path in existing_evidence_paths
        if _evidence_path_classification(path) == "source"
    ]
    test_paths = [
        path
        for path in existing_evidence_paths
        if _evidence_path_classification(path) == "test"
    ]
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
    min_source_paths = int(spec.get("min_source_paths") or 0)
    if min_source_paths and len(set(source_paths)) < min_source_paths:
        issues.append(_issue(
            "insufficient_source_evidence",
            artifact,
            f"{artifact} 可核验源码证据不足: {len(set(source_paths))}/{min_source_paths}",
        ))
    min_test_paths = int(spec.get("min_test_paths") or 0)
    if min_test_paths and len(set(test_paths)) < min_test_paths:
        issues.append(_issue(
            "insufficient_test_evidence",
            artifact,
            f"{artifact} 可核验测试证据不足: {len(set(test_paths))}/{min_test_paths}",
        ))
    min_sfmea_rows = int(spec.get("min_sfmea_rows") or 0)
    if min_sfmea_rows:
        sfmea_rows = len(re.findall(
            r"(?im)^\s*\|\s*(?:(?:F|FM|FMEA|SFMEA)[-_ ]?\d+|\d+)\s*\|",
            content,
        ))
        if sfmea_rows < min_sfmea_rows:
            issues.append(_issue(
                "insufficient_sfmea_rows",
                artifact,
                f"{artifact} SFMEA 风险项不足: {sfmea_rows}/{min_sfmea_rows}",
            ))
    min_black_box_cases = int(spec.get("min_black_box_cases") or 0)
    if min_black_box_cases:
        black_box_content = ""
        section_heading = section_headings.get("黑盒测试用例")
        if section_heading is not None:
            index, match = section_heading
            current_level = len(match.group(0).lstrip()) - len(
                match.group(0).lstrip().lstrip("#")
            )
            end = len(content)
            for next_match in heading_matches[index + 1:]:
                next_level = len(next_match.group(0).lstrip()) - len(
                    next_match.group(0).lstrip().lstrip("#")
                )
                if next_level <= current_level:
                    end = next_match.start()
                    break
            black_box_content = content[match.end():end]
        visible_black_box_content = _markdown_without_code_blocks(black_box_content)
        case_ids: set[str] = set()
        heading_pattern = re.compile(
            r"(?im)^\s*#{2,6}\s+(?:(?P<prefix>B|BB|BBC|TC|CASE|用例)"
            r"[-_ ]?(?P<number>\d+)\b|(?P<section>\d+(?:[.．]\d+)+)\s+\S)"
        )
        for case_match in heading_pattern.finditer(visible_black_box_content):
            if case_match.group("section"):
                case_ids.add(case_match.group("section").replace("．", "."))
            else:
                case_ids.add(_canonical_black_box_case_id(
                    f"{case_match.group('prefix')}{case_match.group('number')}"
                ))
        case_id_pattern = re.compile(
            r"^(?:B|BB|BBC|TC|CASE|用例)[-_ ]?\d+$",
            flags=re.IGNORECASE,
        )
        for line in visible_black_box_content.splitlines():
            cells = _markdown_table_cells(line)
            if len(cells) < 8 or not all(cells[:7]):
                continue
            if not case_id_pattern.fullmatch(cells[0]):
                continue
            case_ids.add(_canonical_black_box_case_id(cells[0]))
        black_box_cases = len(case_ids)
        if black_box_cases < min_black_box_cases:
            issues.append(_issue(
                "insufficient_black_box_cases",
                artifact,
                f"{artifact} 黑盒用例不足: {black_box_cases}/{min_black_box_cases}",
            ))
    for evidence in claimed_evidence_paths:
        if not _repo_path_exists(repo, evidence):
            issues.append(
                _issue(
                    "evidence_path_not_found",
                    artifact,
                    f"证据路径不存在: {evidence}",
                )
            )
    return issues


def _malformed_markdown_table_lines(content: str) -> list[int]:
    lines = str(content or "").splitlines()
    visible = _markdown_visible_line_mask(lines)

    malformed: list[int] = []
    for index, delimiter in enumerate(lines):
        if not visible[index] or not _is_markdown_table_delimiter(delimiter):
            continue
        header_index = index - 1
        while header_index >= 0 and not lines[header_index].strip():
            header_index -= 1
        if header_index < 0 or not visible[header_index]:
            continue
        header = lines[header_index].strip()
        header_cells = _markdown_table_cells(header)
        delimiter_cells = _markdown_table_cells(delimiter.strip())
        if len(header_cells) < 2:
            continue
        if len(delimiter_cells) != len(header_cells):
            malformed.append(index + 1)
        requires_outer_pipes = header.startswith("|") and header.endswith("|")
        row_index = index + 1
        while row_index < len(lines):
            row = lines[row_index].strip()
            if not visible[row_index]:
                break
            if not row or row.startswith("#") or row.startswith(("```", "~~~")):
                break
            if not row.startswith("|"):
                break
            cells = _markdown_table_cells(row)
            if (
                (requires_outer_pipes and not row.endswith("|"))
                or len(cells) != len(header_cells)
            ):
                malformed.append(row_index + 1)
            row_index += 1
    return sorted(set(malformed))


def _is_markdown_table_delimiter(line: str) -> bool:
    cells = _markdown_table_cells(str(line or "").strip())
    return len(cells) >= 2 and all(
        re.fullmatch(r":?-{3,}:?", cell.strip()) is not None for cell in cells
    )


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


def _audit_sfmea_occurrence_basis(
    row: dict[str, Any],
    *,
    artifact: str,
    index: int,
) -> list[dict[str, Any]]:
    """Do not present a guessed occurrence score as a measured likelihood."""
    if str(row.get("risk_status") or "").strip() != "test_hypothesis":
        return []
    explanation = str(row.get("score_explanation") or "")
    occurrence = _integer_score(row.get("occurrence") or row.get("occurrence_score"))
    data_basis = " ".join(
        str(row.get(field) or "")
        for field in ("occurrence_basis", "score_explanation", "evidence_interpretation")
    )
    has_measured_basis = bool(
        re.search(
            r"(?:缺陷历史|历史缺陷|协议流量分布|登录流量|测试统计|样本统计|"
            r"observed rate|defect history|traffic distribution|test statistics)",
            data_basis,
            flags=re.IGNORECASE,
        )
    )
    pending_sampling = bool(
        re.search(r"(?:待采样|待统计|pending sampling|to be sampled)", explanation, re.IGNORECASE)
    )
    provisional_expert_basis = bool(
        str(row.get("rpn_status") or "").strip().lower() == "provisional"
        and re.search(r"(?:专家(?:工程)?评审|expert(?:\s+engineering)?\s+review)", data_basis, re.IGNORECASE)
        and re.search(r"(?:低置信度|low\s+confidence)", data_basis, re.IGNORECASE)
    )
    if (
        occurrence is not None
        and pending_sampling
        and not has_measured_basis
        and not provisional_expert_basis
    ):
        return [
            _issue(
                "sfmea_occurrence_without_data_basis",
                artifact,
                f"{artifact} 第 {index} 项把待采样风险假设赋予 Occurrence={occurrence}，"
                "但没有缺陷历史、流量分布或测试统计依据；应先采样，或明确该项不参与可交付 RPN 排序",
                index=index,
                occurrence=occurrence,
            )
        ]
    return []


_SFMEA_REMEDIATION_ACTION_RE = re.compile(
    r"(?i)\b("
    r"fix|change|modify|release|reset|bound|limit|lock|retry|rollback|clean(?:up)?|reject|close|abort|"
    r"prevent|block|add|introduce|validate|require|keep|configure|emit|expose|ensure|"
    r"recover|restore|serializ|sanitize|enforce|implement|replace|refactor|"
    r"retain|buffer|input\s+validation|track|accumulate|capture|preserve|"
    r"surface|propagate|report|warn|combine"
    r")\b|"
    r"(修复|释放|重置|限制|加锁|重试|回滚|清理|拒绝|恢复|串行|净化|强制|"
    r"实现|替换|重构|初始化|清空|调用|使用|保持|避免|引用计数|持有.{0,12}引用|缓冲|关闭|中止|参数校验|输入校验|防止|阻止)"
    r"|(启用|严格校验|强制校验|返回.{0,12}(?:错误|失败)|添加.{0,20}(?:检查|校验)|"
    r"增加.{0,24}(?:检查|校验|断言|处理|保护|上限|清理|析构|回调)|"
    r"置.{0,24}(?:NULL|null|零|0)|"
    r"检查.{0,24}(?:返回值|返回结果).{0,24}(?:默认|替代|回退)|"
    r"检查.{0,24}(?:NULL|null|为空|有效性)|"
    r"确保.{0,24}(?:配置|状态|资源|连接|会话|参数).{0,20}(?:正确|有效|一致|释放|关闭))"
)
_SFMEA_VERIFICATION_ACTION_RE = re.compile(
    r"(?i)\b("
    r"tests?|cases?|coverage|monitor(?:ing)?|metrics?|logs?|alerts?|probes?|traces?|diagnos|"
    r"observable|validate|check|assert(?:ion)?s?"
    r")\b|"
    r"(测试|用例|覆盖|监控|指标|日志|告警|探针|追踪|诊断|验证|校验|检查|断言)"
)
_SFMEA_TEST_SCENARIO_ACTION_RE = re.compile(
    r"(?i)\b(?:retry|reset|recovery|reconnect|rollback|cleanup|abort|close)"
    r"(?=(?:\s*/\s*(?:retry|reset|recovery|reconnect|rollback|cleanup|abort|close))*"
    r"\s+(?:black[- ]box\s+|regression\s+|negative\s+)?(?:tests?|cases?|coverage)\b)|"
    r"(?:重试|重置|恢复|重连|回滚|清理|中止|关闭)"
    r"(?=(?:\s*[/、]\s*(?:重试|重置|恢复|重连|回滚|清理|中止|关闭))*"
    r"\s*(?:黑盒|回归|负向)?(?:测试|用例|覆盖))|"
    r"\b(?:tests?|cases?|scenarios?)\s+"
    r"(?:retry|reset|recovery|reconnect|rollback|cleanup|abort|close)\b|"
    r"\b(?:execute|run)\s+"
    r"(?:retry|reset|recovery|reconnect|rollback|cleanup|abort|close)"
    r"\s+(?:scenarios?|cases?|tests?)\b|"
    r"(?:测试|用例|场景|验证)\s*(?:重试|重置|恢复|重连|回滚|清理|中止|关闭)|"
    r"执行\s*(?:重试|重置|恢复|重连|回滚|清理|中止|关闭)\s*(?:场景|用例|测试)"
)
_SFMEA_TEST_ONLY_SEGMENT_RE = re.compile(
    r"(?i)^\s*(?:add|extend|run|execute|create|write)\b"
    r"(?:(?!\b(?:and|then|plus)\b).){0,80}\b(?:tests?|cases?|coverage)\b|"
    r"^\s*(?!整改\s*:)(?:新增|添加|运行|执行|编写|扩展)"
    r"(?:(?!并|且|同时|及).){0,40}(?:测试|用例|覆盖)"
)
_SFMEA_OBSERVATION_CLAUSE_RE = re.compile(
    r"(?i)^\s*(?:monitor|alert|log|record|observe|trace|probe|inspect|check|measure|"
    r"assert|validate)\b|"
    r"^\s*(?:监控|记录|告警|追踪|观察|采集|检查|测量|断言|校验)"
)
_SFMEA_BARE_TEST_DIMENSION_RE = re.compile(
    r"(?i)^\s*(?:normal\s+path|invalid\s+input|resource\s+pressure|timeout|"
    r"reconnect(?:\s*/\s*reset)?|reset|concurrency|recovery|"
    r"performance(?:\s+degradation)?)\s*$|"
    r"^\s*(?:正常路径|非法输入|资源压力|超时|重连(?:/重置)?|重置|并发|恢复|性能退化)\s*$"
)
_SFMEA_CONNECTOR_SPLIT_RE = re.compile(
    r"(?i)\s+\b(?:and|then|plus)\b\s+|(?:并且|同时|然后|并|且|及)"
)


def sfmea_mitigation_quality_gaps(mitigation: Any) -> list[str]:
    text = str(mitigation or "").strip()
    if not text:
        return ["missing_remediation_action", "missing_verification_action"]
    remediation_clauses = []
    for segment in re.split(r"[;；。\n]+", text):
        normalized_segment = segment.strip()
        if not normalized_segment:
            continue
        for logical_clause in _SFMEA_CONNECTOR_SPLIT_RE.split(normalized_segment):
            normalized_logical_clause = logical_clause.strip()
            if not normalized_logical_clause:
                continue
            for clause in re.split(r"[，,]+", normalized_logical_clause):
                normalized_clause = clause.strip()
                if (
                    not normalized_clause
                    or _SFMEA_TEST_ONLY_SEGMENT_RE.search(normalized_clause)
                    or _SFMEA_OBSERVATION_CLAUSE_RE.search(normalized_clause)
                    or _SFMEA_BARE_TEST_DIMENSION_RE.search(normalized_clause)
                ):
                    continue
                remediation_clauses.append(
                    _SFMEA_TEST_SCENARIO_ACTION_RE.sub("", normalized_clause)
                )
    remediation_text = " ".join(remediation_clauses)
    gaps: list[str] = []
    explicitly_labeled_remediation = bool(
        re.search(r"整改\s*:\s*(?!.*(?:仅|只)?(?:新增|添加|编写).{0,24}(?:测试|用例|覆盖))\S+", remediation_text)
    )
    if not _SFMEA_REMEDIATION_ACTION_RE.search(remediation_text) and not explicitly_labeled_remediation:
        gaps.append("missing_remediation_action")
    if not _SFMEA_VERIFICATION_ACTION_RE.search(text):
        gaps.append("missing_verification_action")
    return gaps


def sfmea_mitigation_is_actionable(mitigation: Any) -> bool:
    return not sfmea_mitigation_quality_gaps(mitigation)


_SFMEA_NON_FAILURE_MODE_RE = re.compile(
    r"(?i)(?:"
    r"(?:构建|编译|配置).{0,24}(?:差异|不同).{0,24}(?:行为|返回)|"
    r"(?:不会|不再).{0,24}(?:更新|覆盖|使用|访问).{0,40}(?:保持|不变|安全|不存在)|"
    r"(?:不存在|没有).{0,40}(?:路径|缺陷|泄漏|故障|风险)|"
    r"(?:返回|传播).{0,16}(?:错误|负错误码).{0,20}(?:且不|直接|原样)|"
    r"源码.{0,20}不支持.{0,40}(?:结论|故障|缺陷|风险)|"
    r"(?:该|当前|现有)?测试.{0,24}(?:只覆盖|覆盖不足|缺少|未覆盖)|"
    r"当前源码.{0,16}(?:已按此处理|正确处理)|"
    r"source.{0,20}(?:does not support|already handles)|"
    r"test.{0,20}(?:coverage gap|only covers)"
    r")"
)
_SFMEA_FAILURE_SIGNAL_RE = re.compile(
    r"(?i)\b(?:fail(?:ure|ed)?|timeout|leak|corrupt(?:ion)?|lost|stale|race|"
    r"deadlock|hang|incorrect|bypass|ignored|unpropagated|exhaust(?:ed|ion)?|"
    r"overflow|wraparound|double[- ]free|use[- ]after[- ]free|crash|downgrade|"
    r"expos(?:e|ed|ure)|disclos(?:e|ed|ure)|unreachable|unavailable|"
    r"accept(?:ed|s|ance)?|mismatch|verbatim|fails?|missing|absent|incomplete|"
    r"trailing|garbage|not\s+created)\b|"
    r"(?:失败|超时|泄漏|错误|异常|丢失|残留|竞态|死锁|阻塞|耗尽|翻转|溢出|越界|"
    r"空指针|双重(?:注销|释放)|绕过|未传播|不向上传播|未释放|未(?:被)?拒绝|未正确|未检查|未处理|"
    r"未清理|未清除|未.{0,8}关闭|静默丢弃|(?:静默)?丢弃|未(?:原子)?(?:增加|递增|更新)|重复释放|悬空|崩溃|降级|错误接受|错误拒绝|误报|未记录|不可追溯)"
    r"|(?:超限|耗尽|满载).{0,16}(?:仍|继续|再次).{0,16}(?:分配|创建|接受|建立)"
)


def sfmea_failure_mode_is_risk(failure_mode: Any) -> bool:
    text = str(failure_mode or "").strip()
    if not text or _SFMEA_NON_FAILURE_MODE_RE.search(text):
        return False
    return bool(_SFMEA_FAILURE_SIGNAL_RE.search(text))


def _audit_sfmea_mitigation(
    row: dict[str, Any],
    *,
    artifact: str,
    index: int,
) -> list[dict[str, Any]]:
    mitigation = str(row.get("mitigation") or "").strip()
    gaps = sfmea_mitigation_quality_gaps(mitigation)
    if not mitigation or not gaps:
        return []
    missing_labels = []
    if "missing_remediation_action" in gaps:
        missing_labels.append("具体整改")
    if "missing_verification_action" in gaps:
        missing_labels.append("可执行的测试或监控验证动作")
    return [
        _issue(
            "non_actionable_mitigation",
            artifact,
            f"{artifact} 第 {index} 项 mitigation 缺少{'和'.join(missing_labels)}",
            index=index,
            gaps=gaps,
        )
    ]


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
    text = re.sub(r"\s*[（(][^）)]*[）)]\s*$", "", text)
    text = re.sub(r"^(黑盒测试用例)(?:矩阵|清单|列表)$", r"\1", text)
    return text.strip().rstrip(":：")


def _markdown_repo_paths(content: str) -> list[str]:
    patterns = (
        re.compile(
            r"(?<![A-Za-z0-9_/])(?:[A-Za-z0-9_.+@%\-]+/)*"
            r"[A-Za-z0-9_.+@%\-]+\."
            r"(?:c|h|cc|cpp|hpp)"
            r"(?![A-Za-z0-9])"
            r"(?::L?\d+(?:-L?\d+)?)?"
        ),
        re.compile(
            r"(?<![A-Za-z0-9_/])(?:lib|test|tests|include|module|app)/"
            r"[A-Za-z0-9_.+@%/\-]+(?::L?\d+(?:-L?\d+)?)?"
        ),
    )
    return _unique_strings(
        match.group(0).rstrip(".,;:)]}`'")
        for pattern in patterns
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
    # A black-box instruction may explicitly prohibit an internal operation.
    # Do not turn “不得调用内部函数” into the very violation it prevents.
    text = re.sub(r"(?:不得|不要|禁止|不)\s*调用内部函数", "", text)
    return bool(
        re.search(
            r"\b(call|invoke)\s+[a-z0-9_]*\(|"
            r"\b(?:call|invoke)\s+(?:libnvmf|libnvme)[a-z0-9_]*\b|"
            r"调用\s*[a-z_][a-z0-9_]*\s*\(|"
            r"(?:调用|直接调用)\s*(?:libnvmf|libnvme)[a-z0-9_]*\b|"
            r"(?:通过|使用|由)\s*(?:spdk|iscsi|nvmf|bdev|libnvmf|libnvme)_[a-z0-9_]*\b|"
            r"直接调用|调用内部函数|修改源码|private struct|internal function",
            text,
        )
    )


_BLACK_BOX_DELIVERY_WHITE_BOX_RE = re.compile(
    r"(?i)("
    r"\b(?:mock|stub|patch|unit\s*test|internal\s+function|"
    r"direct\s+function|private\s+function)\b|"
    r"\b(?:invoke|call)\s+(?:an?\s+)?(?:internal|private)\s+(?:function|method)\b|"
    r"\b(?:invoke|call)\s+[a-z_][a-z0-9_]*\s*\(|"
    r"\b[a-z0-9_./-]+\.(?:c|cc|cpp|cxx|h|hpp):\d+\b|"
    r"\b[a-z_][a-z0-9_]*->[a-z_][a-z0-9_]*\b|"
    r"\b[a-z_][a-z0-9_]*::[a-z_][a-z0-9_]*\b|"
    r"调用\s*(?:内部|私有)?\s*(?:函数|方法)|"
    r"(?:调用|直接调用)\s*(?:libnvmf|libnvme)[a-z0-9_]*\b|"
    r"(?:内部|私有)(?:函数|方法|变量|状态|字段|调用栈)|"
    r"单元测试(?:候选|用例)?|(?:内部|私有)(?:函数|方法)?返回值|调用栈|"
    r"修改[^，。；;\n]*?(?:变量|状态|字段)|"
    r"进入[^，。；;\n]*?:\d+[^，。；;\n]*?分支"
    r")"
)

_BLACK_BOX_ACTIONABLE_STEP_RE = re.compile(
    r"(?i)\b("
    r"start|stop|restart|connect|disconnect|reconnect|send|request|upload|download|import|export|"
    r"configure|set|create|delete|run|execute|invoke\s+cli|open|close|interrupt|kill|timeout|"
    r"failover|reset|login|logout|read|write|submit|fio|rpc|curl|nvme|iscsi|spdk|target|initiator|"
    r"network|port|file|config|service|process|command"
    r")\b|"
    r"(启动|停止|重启|连接|断开|重连|发送|请求|上传|下载|导入|导出|配置|设置|创建|删除|运行命令|"
    r"打开|关闭|中断|终止|超时|故障切换|重置|登录|读|写|提交|网络|端口|文件|服务|进程|命令|"
    r"target|initiator|NVMe|iSCSI|RPC|fio|curl)"
)
_BLACK_BOX_VAGUE_STEP_RE = re.compile(
    r"(?i)^\s*(run\s+test|execute\s+test|verify|validate|observe|check|test|"
    r"执行测试|运行测试|验证功能|验证|观察结果|观察|检查|测试)\s*[。.!！]?\s*$"
)


def _is_explicit_unverified_test_mapping(value: str) -> bool:
    normalized = str(value or "").strip()
    marker = "ai_suggested_unverified"
    return normalized == marker or normalized.startswith((marker + ":", marker + "："))


def black_box_steps_are_actionable(steps: Any) -> bool:
    """Use the same step-quality rule during repair and final acceptance."""
    meaningful = [item.strip() for item in _flatten_text(steps) if item.strip()]
    if not meaningful:
        return False
    if any(_BLACK_BOX_ACTIONABLE_STEP_RE.search(item) for item in meaningful):
        return True
    return any(
        len(item) >= 20 and not _BLACK_BOX_VAGUE_STEP_RE.match(item)
        for item in meaningful
    )


def _test_mapping_values(value: Any) -> list[str]:
    # An explicit "needs a new test" declaration may explain why an existing
    # script is only a setup reference.  It is one mapping contract, not a
    # semicolon-delimited list whose explanatory path becomes a false mapping.
    if not isinstance(value, list) and _is_explicit_unverified_test_mapping(str(value or "")):
        return [str(value).strip()]
    raw_values = value if isinstance(value, list) else [value]
    return _unique_strings(
        part.strip()
        for raw in raw_values
        for part in re.split(r"[;；\n]+", str(raw or ""))
        if part.strip()
    )


def _is_verified_test_mapping(value: str, *, repo_path: str = "") -> bool:
    normalized = str(value or "").strip().replace("\\", "/").rstrip("/")
    normalized = re.sub(r":L?\d+(?:-L?\d+)?$", "", normalized)
    relative = Path(normalized)
    if not normalized or relative.is_absolute() or ".." in relative.parts:
        return False
    if not any(
        part.lower() in {"test", "tests", "spec", "specs"}
        for part in relative.parts
    ):
        return False
    if not repo_path:
        return False
    try:
        repo = Path(repo_path).expanduser().resolve()
        candidate = (repo / relative).resolve()
    except OSError:
        return False
    return (
        (candidate == repo or repo in candidate.parents)
        and candidate.exists()
        and (candidate.is_file() or candidate.is_dir())
    )


def black_box_case_delivery_quality_gaps(
    row: dict[str, Any],
    *,
    repo_path: str = "",
) -> list[str]:
    """Shared stage/final checks for a deliverable black-box test case."""
    mapping = (
        row.get("suggested_spdk_test_dir")
        or row.get("suggested_test_directory")
        or row.get("test_directory")
        or row.get("test_dir")
        or row.get("mapped_test_dir")
        or ""
    )
    mappings = _test_mapping_values(mapping)
    gaps: list[str] = []
    if not black_box_steps_are_actionable(row.get("steps")):
        gaps.append("vague_steps")
    if not mappings or not all(
        _is_verified_test_mapping(item, repo_path=repo_path)
        or _is_explicit_unverified_test_mapping(item)
        for item in mappings
    ):
        gaps.append("missing_test_directory_mapping")
    boundary_fields = (
        "title",
        "scenario",
        "scenario_name",
        "inputs",
        "steps",
        "expected",
        "expected_result",
        "preconditions",
        "observability",
        "diagnostics",
        "failure_diagnostics",
    )
    boundary_parts = [
        part
        for field in boundary_fields
        for part in _flatten_text(row.get(field))
        if part
    ]
    if any(
        _BLACK_BOX_DELIVERY_WHITE_BOX_RE.search(part)
        and not re.search(
            r"(?i)(?:\bnot\s+by\b|\bwithout\b|\bdo\s+not\b|\bmust\s+not\b|"
            r"不得|不要|禁止|不应)[^。；;\n]{0,100}"
            r"(?:internal|private|unit\s*test|内部|私有|单元测试|修改源码)",
            part,
        )
        and not re.search(r"(?:不得|不要|禁止|不)\s*调用内部函数", part)
        for part in boundary_parts
    ):
        gaps.append("white_box_boundary")
    return gaps


_BLACK_BOX_OBSERVABLE_RESULT_RE = re.compile(
    r"(?i)\b(?:log|metric|status|state|exit\s*code|error|timeout|reject|accept|"
    r"connect|disconnect|reconnect|response|message|event|alert|counter|latency|"
    r"throughput|duration|elapsed|runtime|baseline|percentile|stdout|stderr)\b|"
    r"(?:日志|指标|状态|退出码|错误|超时|拒绝|接受|连接|断开|重连|响应|消息|"
    r"事件|告警|计数|延迟|吞吐|耗时|用时|时间|基线|百分位|标准输出|标准错误)"
)


def black_box_expected_result_is_observable(value: Any) -> bool:
    text = " ".join(_flatten_text(value)).strip()
    if not text:
        return False
    if _BLACK_BOX_OBSERVABLE_RESULT_RE.search(text):
        return True
    return len(text) >= 18 and not re.fullmatch(
        r"(?i)(?:success|successful|ok|pass|passed|正常|成功|通过|返回\s*-?[A-Z0-9_]+)",
        text,
    )


_black_box_expected_result_is_observable = black_box_expected_result_is_observable


_BLACK_BOX_BASIS_REQUIRED_DIMENSIONS = {
    "resource_pressure",
    "timeout",
    "performance",
    "long_steady_state",
    "resource_wraparound",
}
_BLACK_BOX_TRACEABLE_BASIS_RE = re.compile(
    r"(?i)\b(?:source|code|macro|constant|config(?:uration)?|option|argument|"
    r"environment|baseline|spec(?:ification)?|limit|maximum|range|bit[- ]?width|"
    r"ulimit|commit|help|manpage)\b|"
    r"(?:源码|代码|宏|常量|配置|参数|选项|环境|基线|规范|上限|最大值|范围|位宽|"
    r"提交|帮助文本|手册|证据)"
)
_BLACK_BOX_PERFORMANCE_SAMPLE_RE = re.compile(
    r"(?i)(?=.*(?:warmup|preheat|预热))"
    r"(?=.*(?:repeat|iterations?|samples?|runs?|重复|样本|采样|运行))"
    r"(?=.*(?:p50|50th\s*percentile|中位数))"
    r"(?=.*(?:p95|95th\s*percentile))",
    flags=re.DOTALL,
)


def black_box_oracle_basis_quality_gaps(row: dict[str, Any]) -> list[str]:
    dimension = str(row.get("test_dimension") or "").strip().lower()
    if dimension not in _BLACK_BOX_BASIS_REQUIRED_DIMENSIONS:
        return []
    basis = " ".join(_flatten_text(row.get("oracle_basis"))).strip()
    gaps: list[str] = []
    if not basis:
        gaps.append("missing_oracle_basis")
    elif not _BLACK_BOX_TRACEABLE_BASIS_RE.search(basis):
        gaps.append("oracle_basis_not_traceable")
    if dimension == "performance" and not _BLACK_BOX_PERFORMANCE_SAMPLE_RE.search(basis):
        gaps.append("missing_performance_sampling_plan")
    return gaps


def black_box_observability_quality_gaps(row: dict[str, Any]) -> list[str]:
    """Reject abbreviated RPC state claims that cannot be verified externally.

    A public RPC is a valid black-box observation, but its method/field contract
    still has to be named.  In particular, ``full_feature`` is a protocol phase,
    not a self-describing RPC field value, so a case must state the actual public
    field (for SPDK iSCSI, for example ``login_phase=full_feature_phase``).
    """
    observations = " ".join(_flatten_text(row.get("observability")))
    lower = observations.lower()
    if not re.search(r"\brpc\b|RPC|远程过程调用", observations, re.IGNORECASE):
        return []
    if not re.search(r"full[_ -]?feature", lower, re.IGNORECASE):
        return []
    if re.search(r"login[_ -]?phase\s*(?:=|为)?\s*full[_ -]?feature[_ -]?phase", lower):
        return []
    return ["black_box_rpc_observability_ambiguous"]


def _unsafe_destructive_test_step(row: dict[str, Any]) -> bool:
    text = " ".join(
        _flatten_text(
            [
                row.get("preconditions"),
                row.get("steps"),
                row.get("expected_result"),
            ]
        )
    ).lower()
    raw_device_write = re.search(
        r"\bdd\b.{0,160}\bof\s*=\s*/dev/(?:sd[a-z]|nvme\d+n\d+|vd[a-z]|xvd[a-z])\b",
        text,
    )
    if not raw_device_write:
        return False
    return not re.search(
        r"(?:专用测试盘|隔离测试设备|已确认设备|disposable test device|isolated test device|explicit device confirmation)",
        text,
    )


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
    return _unique_strings(
        path
        for value in values
        for path in _markdown_repo_paths(value)
    )


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


def _evidence_path_classification(value: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/").lstrip("./")
    normalized = re.sub(r":L?\d+(?:-L?\d+)?$", "", normalized)
    parts = [part.lower() for part in normalized.split("/") if part]
    if any(part in {"test", "tests", "spec", "specs"} for part in parts[:-1]):
        return "test"
    if Path(normalized).suffix.lower() in {
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".hpp",
        ".go",
        ".java",
        ".js",
        ".jsx",
        ".py",
        ".rs",
        ".sh",
        ".ts",
        ".tsx",
    }:
        return "source"
    return ""


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
    if repo_root not in candidate.parents:
        return False
    if candidate.exists():
        return True
    return (
        not relative.suffix
        and bool(relative.parts)
        and relative.parts[0].lower() in {"test", "tests"}
        and candidate.with_suffix(".c").is_file()
        and (candidate.parent / "Makefile").is_file()
    )


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
