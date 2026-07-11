"""Testing activity contracts, profiles, artifact templates, and quality audit."""

from __future__ import annotations

import ast
import builtins
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
        scenarios=[
            "login negotiation",
            "CHAP success/failure in Security Negotiation",
            "CHAP request/response rounds with T, CSG, and NSG assertions",
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
                "correction_patterns": [r"(?:未知|unknown).{0,100}(?:notunderstood|not understood)"],
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
                "id": "iscsi_discovery_target_address",
                "assertion": (
                    "iscsi_op_login_set_target_info 仅在 target != NULL 时向响应追加 TargetAddress；"
                    "Discovery session 没有 target，不能声称 Discovery Login 必定返回 TargetAddress。"
                ),
                "evidence": ["lib/iscsi/iscsi.c::iscsi_op_login_set_target_info"],
                "conflict_patterns": [
                    r"discovery login.{0,120}(?:必定|总是|always).{0,60}(?:返回|包含|return).{0,40}targetaddress",
                    r"discovery.{0,100}(?:成功响应|response).{0,100}(?:返回|包含).{0,40}targetaddress",
                    r"discovery login[\s\S]{0,700}(?:login response|登录响应)[\s\S]{0,320}targetaddress\s*=",
                ],
                "correction_patterns": [
                    r"discovery.{0,220}(?:不返回|不会返回|不追加|不包含|does not (?:return|append|include)).{0,30}targetaddress",
                    r"discovery.{0,180}(?:不应声称|不能声称|不得声称|not claim).{0,80}targetaddress",
                    r"(?:检查|验证|检测|check).{0,120}discovery.{0,120}(?:是否|有无|whether).{0,30}(?:包含|返回|include|return)?.{0,20}targetaddress",
                    r"discovery login.{0,100}(?:不强制|不要求|非必需|not require).{0,40}targetaddress",
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
                    r"(?:最终|final).{0,120}(?:必须|只能|固定|应|must|only|should|expected).{0,100}[\"'`]?csg[\"'`]?\s*[:=]\s*1",
                    r"(?:必须|只能|固定|应|must|only|should|expected).{0,100}(?:最终|final).{0,120}[\"'`]?csg[\"'`]?\s*[:=]\s*1",
                    r"(?:最终|final).{0,120}csg\s*[:=]\s*1.{0,100}(?:唯一|only)",
                ],
                "correction_patterns": [
                    r"csg\s*[:=]\s*0.{0,160}csg\s*[:=]\s*1.{0,100}(?:均|都|允许|合法|取决于|depending|either)",
                    r"(?:允许|合法|either).{0,100}csg\s*[:=]\s*[01].{0,160}csg\s*[:=]\s*[01]",
                    r"(?:不能|不得|不可|not).{0,60}(?:把|treat)?\s*[`\"']?csg[`\"']?\s*[:=]\s*1.{0,100}(?:唯一|only|固定)",
                    r"(?:最终|final).{0,80}(?:不能固定|不得固定|not fixed).{0,40}[`\"']?csg\s*[:=]\s*1[`\"']?",
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
                    r"chap_discovery\.sh.{0,160}(?:不覆盖|不发送|不能证明|不作为|not cover).{0,80}(?:未知|unknown|chap_n)",
                    r"chap_discovery\.sh.{0,120}(?:不|未|不得|不能).{0,30}(?:映射|发送|发|覆盖).{0,80}(?:未知|unknown|chap_n)",
                    r"(?:未知|unknown).{0,80}(?:ai_suggested_unverified|需要新增|待新增|正文.*harness).{0,200}chap_discovery\.sh.{0,80}(?:不覆盖|不作为)",
                    r"(?:未知|unknown|unknown-user).{0,100}(?:仅|只).{0,40}(?:映射|使用).{0,60}harness.{0,100}(?:不|未|不得|不能).{0,30}映射.{0,60}chap_discovery\.sh",
                    r"(?:未知|unknown).{0,120}(?:不得|不能|不再|不).{0,30}映射.{0,80}chap_discovery\.sh",
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
                    r"login_redirection\.sh.{0,180}(?:不覆盖|不能证明|不映射|不作为|仅供参考|需要新增|不是(?:网络故障|自动重连)|not cover|does not prove|not a network)",
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
                    r"iscsi_(?:target|initiator)\.sh.{0,180}(?:不采集|不测|不能测|fio\s*i/o|does not measure|not a login)",
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
                    r"iscsi_ut\.c.{0,220}(?:未完整断言|不能声称完整覆盖|只断言部分|partial).{0,160}(?:target removed|authorization failure|错误响应)",
                    r"iscsi_ut\.c.{0,360}(?:不能笼统声称|不得笼统声称|must not claim).{0,220}(?:target removed|authorization failure|错误响应)",
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
        "domain_requirements": {
            profile_id: {
                "required_scenarios": list(PROFILE_REGISTRY[profile_id].get("required_scenarios", [])),
                "failure_modes": list(PROFILE_REGISTRY[profile_id].get("failure_modes", [])),
                "black_box_observability": list(PROFILE_REGISTRY[profile_id].get("black_box_observability", [])),
                "graybox_evidence_points": list(PROFILE_REGISTRY[profile_id].get("graybox_evidence_points", [])),
            }
            for profile_id in domain_profiles
        },
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
            and not (repo / path).exists()
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
            and path.lower().startswith(("test/", "tests/"))
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
                "MCS 容量用例必须给出 target 启动期 iscsi_set_options -c 配置；客户端 probe 参数不能替代 MaxConnections 设置",
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
    return issues


def _audit_combined_professional_completeness(
    content: str,
    contract: dict[str, Any],
) -> list[dict[str, Any]]:
    profiles = {str(item) for item in contract.get("domain_profiles") or []}
    required_outputs = {str(item) for item in contract.get("required_outputs") or []}
    target = str(contract.get("target") or "")
    if (
        "iscsi_login" not in profiles
        or "完整" not in target
        or not {"business_flow.md", "sfmea.json", "black_box_cases.json"}.issubset(required_outputs)
    ):
        return []

    lower = content.lower()
    issues: list[dict[str, Any]] = []
    issues.extend(_audit_combined_sfmea_order(content))
    issues.extend(_audit_combined_execution_contract(content))
    scenario_markers = {
        "T+C 非法组合": (r"\bt\s*\+\s*c\b", r"t\s*=\s*1.{0,80}c\s*=\s*1"),
        "非法 NSG": (r"非法\s*nsg", r"invalid\s+nsg", r"reserved\s+nsg"),
        "Unsupported Version": (r"unsupported version", r"不支持.{0,20}版本"),
        "未知合法 key=NotUnderstood": (r"notunderstood", r"not understood"),
        "Target not found/removed": (r"target[_ ]not[_ ]found", r"target[_ ]removed", r"目标不存在", r"目标已删除"),
        "Authorization Failure": (r"authorization failure", r"授权失败"),
        "Redirect": (r"redirect", r"重定向"),
        "Discovery 后 SendTargets": (r"sendtargets",),
        "首 payload 后 timer 注销": (r"login_timer.{0,120}(?:注销|unregister|未重新注册|not re[- ]?armed)", r"(?:注销|unregister|未重新注册|not re[- ]?armed).{0,120}login_timer"),
    }
    missing = [
        label
        for label, patterns in scenario_markers.items()
        if not any(re.search(pattern, content, flags=re.IGNORECASE | re.DOTALL) for pattern in patterns)
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
            r"(?:target|目标端).{0,80}(?:要求|require).{0,30}mutual(?:\s*chap)?.{0,100}(?:initiator|发起端).{0,60}(?:未提供|缺失|missing|without)",
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
        "不支持的 CHAP_A 算法": (r"(?:不支持|unsupported).{0,40}chap_a", r"chap_a.{0,40}(?:不支持|unsupported)"),
        "缺少 CHAP_R": (r"(?:缺少|缺失|missing|absent).{0,40}chap_r", r"chap_r.{0,40}(?:缺少|缺失|missing|absent)"),
        "CHAP_R 编码格式错误": (r"chap_r.{0,80}(?:hex|base64|编码|encoding|格式).{0,50}(?:错误|无效|invalid|malformed)",),
        "Mutual 用户或 secret 缺失": (r"mutual\s*chap.{0,100}(?:用户|user|secret|密钥).{0,50}(?:缺少|缺失|missing|absent)",),
        "Initiator 请求 Mutual 但 Target 禁止": (
            r"(?:initiator|发起端).{0,80}(?:请求|request).{0,30}mutual(?:\s*chap)?.{0,100}(?:target|目标端).{0,70}(?:禁止|未启用|disable|not enabled)",
        ),
        "Mutual challenge 合法编码但语义错误": (
            r"mutual(?:\s*chap)?.{0,100}(?:challenge|chap_c).{0,100}(?:合法编码|valid encoding).{0,100}(?:语义错误|错误.{0,30}(?:secret|oracle)|oracle.{0,30}(?:不匹配|mismatch)|wrong value|mismatch)",
            r"mutual(?:\s*chap)?.{0,120}(?:target\s*)?(?:digest\s*)?oracle.{0,120}(?:误判|不匹配|mismatch|wrong)",
        ),
    }
    missing_extended_chap = [
        label
        for label, patterns in extended_chap_markers.items()
        if not any(re.search(pattern, content, flags=re.IGNORECASE | re.DOTALL) for pattern in patterns)
    ]
    if missing_extended_chap:
        issues.append(
            _issue(
                "missing_extended_chap_negative_scenarios",
                "sfmea.json",
                "完整 iSCSI Login 交付件缺少扩展 CHAP 安全负向场景: " + "、".join(missing_extended_chap),
                scenarios=missing_extended_chap,
            )
        )

    needs_raw_pdu_harness = bool(
        re.search(r"(?:raw\s*pdu|原始\s*pdu|\bt\s*\+\s*c\b|chap_r|c-bit|c\s*位)", lower)
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

    c_bit_case = bool(
        re.search(r"\bc\s*[:=]\s*1", lower)
        and re.search(r"(?:跨|分片|fragment|split).{0,80}(?:pdu|key|value|参数)", lower)
        and re.search(r"\bc\s*[:=]\s*0", lower)
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

    if "multiconnection.sh" in lower and not re.search(
        r"(?:null|malloc)\s*bdev|专用测试盘|隔离测试设备|数据销毁确认|允许列表|allowlist|disposable|isolated",
        lower,
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
    has_sfmea_scale = bool(
        re.search(rf"(?:severity|严重度).{{0,40}}{scale_range}", lower)
        and re.search(rf"(?:occurrence|发生度).{{0,40}}{scale_range}", lower)
        and re.search(rf"(?:detection|探测度|可探测度).{{0,40}}{scale_range}", lower)
        and re.search(r"(?:rpn)[\s\S]{0,240}(?:优先|priority|阈值|threshold|高风险)", lower)
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
    if not constraint.get("evaluate_hypothetical_mapping") and re.match(
        r'^\s*[\[{]?\s*["\']?(?:cause|failure_mode)["\']?\s*:',
        statement,
        flags=re.IGNORECASE,
    ):
        return True
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
        re.sub(r":L?\d+(?:-L?\d+)?$", "", path.rstrip(".,;，。；"), flags=re.IGNORECASE)
        for path in [*candidates, *_markdown_repo_paths(str(content or ""))]
        if not any(marker in path for marker in "*?[]")
    )


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
