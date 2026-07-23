import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest


def test_json_row_quality_issues_include_stable_row_id(tmp_path):
    from app.services.test_activity_contract import _audit_json_artifact

    rows = [
        {
            "sfmea_id": "SFMEA-007",
            "failure_mode": "待验证：当前片段未显示失败后的状态恢复",
            "cause": "当前上下文未提供完整错误路径",
            "effect": "可能影响后续运行",
            "detection": "检查运行状态",
            "severity": 5,
            "occurrence": 3,
            "detection_score": 4,
            "rpn": 60,
            "mitigation": "继续分析",
            "source_evidence": ["lib/nvme/fabrics.c::connect"],
            "test_mapping": "test/nvme",
        }
    ]

    issues = _audit_json_artifact(
        artifact="sfmea.json",
        payload=rows,
        spec={"required_fields": []},
        repo=tmp_path,
    )

    indexed = [issue for issue in issues if issue.get("index") == 1]
    assert indexed
    assert {issue.get("row_id") for issue in indexed} == {"SFMEA-007"}


def test_black_box_professional_safety_and_latency_issues_bind_to_case_id(tmp_path):
    from app.services.test_activity_contract import _audit_json_artifact

    rows = [
        {
            "case_id": "BB-HAZARD-01",
            "test_dimension": "recovery",
            "scenario_name": "多连接回归",
            "steps": "运行 test/iscsi_tgt/multiconnection/multiconnection.sh。",
            "expected_result": "多连接均完成。",
            "observability": "脚本退出码和 target 日志。",
            "source_evidence": ["test/iscsi_tgt/multiconnection/multiconnection.sh"],
        },
        {
            "case_id": "BB-PERF-01",
            "test_dimension": "performance",
            "scenario_name": "Login latency p95",
            "steps": "连续发起 Login 并记录延迟。",
            "expected_result": "P95 < 10ms。",
            "observability": "pcap 时间戳。",
            "source_evidence": ["test/iscsi_tgt/iscsi_tgt.sh"],
        },
    ]

    issues = _audit_json_artifact(
        artifact="black_box_cases.json",
        payload=rows,
        spec={"required_fields": []},
        repo=tmp_path,
    )

    bindings = {(issue["code"], issue.get("row_id")) for issue in issues}
    assert ("unsafe_hazardous_test_mapping", "BB-HAZARD-01") in bindings
    assert ("ungrounded_performance_threshold", "BB-PERF-01") in bindings


def test_black_box_relative_latency_threshold_requires_statistical_basis_per_case(tmp_path):
    from app.services.test_activity_contract import _audit_json_artifact

    issues = _audit_json_artifact(
        artifact="black_box_cases.json",
        payload=[
            {
                "case_id": "BB-PERF-02",
                "test_dimension": "performance",
                "scenario_name": "Login relative regression",
                "steps": "采集登录时延。",
                "expected_result": "相对退化不得超过 10%。",
                "observability": "pcap。",
                "source_evidence": ["test/iscsi_tgt/iscsi_tgt.sh"],
            }
        ],
        spec={"required_fields": []},
        repo=tmp_path,
    )

    assert any(
        issue["code"] == "missing_performance_statistical_basis"
        and issue.get("row_id") == "BB-PERF-02"
        for issue in issues
    ), issues


@pytest.mark.parametrize(
    "failure_mode,cause",
    [
        (
            "读取 none 后释放临时字符串并置 NULL，后续不会使用该指针",
            "该分支跳过 setter，不存在 use-after-free",
        ),
        (
            "是否存在中间资源泄漏需用故障注入验证",
            "当前上下文只显示 cleanup 属性",
        ),
        (
            "给定源码不支持该路径存在函数内资源泄漏",
            "分配失败前没有需要释放的资源",
        ),
    ],
)
def test_sfmea_safety_or_unproven_row_is_not_a_scored_failure_mode(
    tmp_path, failure_mode, cause
):
    from app.services.test_activity_contract import _audit_json_artifact

    row = {
        "sfmea_id": "SFMEA-002",
        "failure_mode": failure_mode,
        "cause": cause,
        "effect": "没有已证实的失效影响",
        "detection": "运行回归测试",
        "severity": 2,
        "occurrence": 2,
        "detection_score": 2,
        "rpn": 8,
        "mitigation": "增加回归测试并检查资源计数",
        "source_evidence": ["lib/nvme/fabrics.c::connect"],
        "test_mapping": "test/nvme",
    }

    issues = _audit_json_artifact(
        artifact="sfmea.json",
        payload=[row],
        spec={"required_fields": []},
        repo=tmp_path,
    )

    assert any(issue["code"] == "non_risk_sfmea_row" for issue in issues)


@pytest.mark.parametrize(
    "failure_mode,cause",
    [
        (
            "构建时未启用 TLS 功能，命令返回不支持错误",
            "编译配置未选择 TLS 后端",
        ),
        (
            "配置字段为 NULL 时保持原值，不会覆盖现有配置",
            "setter 按接口约定跳过空字段",
        ),
        (
            "底层连接失败后错误码直接向上传播",
            "调用链保留原始负错误码",
        ),
        (
            "测试只覆盖正常路径，缺少异常用例",
            "现有测试目录没有对应场景",
        ),
    ],
)
def test_sfmea_rejects_normal_behavior_and_test_coverage_gaps(
    tmp_path, failure_mode, cause
):
    from app.services.test_activity_contract import _audit_json_artifact

    row = {
        "sfmea_id": "SFMEA-010",
        "failure_mode": failure_mode,
        "cause": cause,
        "effect": "没有已证明的产品失效影响",
        "detection": "运行回归测试",
        "severity": 2,
        "occurrence": 2,
        "detection_score": 2,
        "rpn": 8,
        "mitigation": "整改: 保持当前错误处理。验证: 增加回归测试并检查退出码。",
        "source_evidence": ["libnvme/src/nvme/fabrics.c:1-2"],
        "test_mapping": "libnvme/test",
    }

    issues = _audit_json_artifact(
        artifact="sfmea.json",
        payload=[row],
        spec={"required_fields": []},
        repo=tmp_path,
    )

    assert any(issue["code"] == "non_risk_sfmea_row" for issue in issues)


def test_sfmea_error_not_propagated_remains_a_scored_failure_mode(tmp_path):
    from app.services.test_activity_contract import _audit_json_artifact

    row = {
        "sfmea_id": "SFMEA-001",
        "failure_mode": "registry 清理失败仅记录 WARN，不向上传播",
        "cause": "函数返回 void，调用方不会收到该清理错误",
        "effect": "旧 ownership registry 条目可能保留",
        "detection": "检查 registry 条目和 WARN 日志",
        "severity": 6,
        "occurrence": 3,
        "detection_score": 4,
        "rpn": 72,
        "mitigation": (
            "整改: 返回清理错误并由调用方执行补偿。"
            "验证: 注入清理失败并确认错误可观察"
        ),
        "source_evidence": ["libnvme/src/nvme/fabrics.c:1407-1421"],
        "test_mapping": "注入 registry 删除失败",
    }

    issues = _audit_json_artifact(
        artifact="sfmea.json",
        payload=[row],
        spec={"required_fields": []},
        repo=tmp_path,
    )

    assert not any(issue["code"] == "non_risk_sfmea_row" for issue in issues)


@pytest.mark.parametrize(
    "failure_mode",
    [
        "A malformed DH-HMAC-CHAP secret is written verbatim to an error log.",
        "The compatibility retry is unreachable after a negative mapped error.",
        "An odd-length secret can be accepted as valid key material.",
    ],
)
def test_sfmea_accepts_source_backed_english_failure_modes(failure_mode):
    from app.services.test_activity_contract import sfmea_failure_mode_is_risk

    assert sfmea_failure_mode_is_risk(failure_mode)


@pytest.mark.parametrize(
    "failure_mode",
    [
        "A configured discovery controller is not created, yet the task returns success.",
        "A TLS key id with trailing garbage is accepted into topology.",
    ],
)
def test_sfmea_accepts_english_creation_and_parse_failure_modes(failure_mode):
    from app.services.test_activity_contract import sfmea_failure_mode_is_risk

    assert sfmea_failure_mode_is_risk(failure_mode)


def test_sfmea_mitigation_accepts_change_as_a_concrete_remediation():
    from app.services.test_activity_contract import sfmea_mitigation_quality_gaps

    assert sfmea_mitigation_quality_gaps(
        "Production remediation: change the error branch to return its original status. "
        "Verification: execute the negative test and assert the status in logs."
    ) == []


@pytest.mark.parametrize(
    "failure_mode",
    [
        "构建配置差异可能导致同一 hmac 参数行为不同：非 OpenSSL 构建拒绝非 NONE HMAC。",
        "若 host_key 为 NULL，函数不会更新字段，现有控制器字段保持不变；不存在 free(NULL) 路径。",
        "非法 key_len 时函数返回错误且不设置 raw_secret；源码不支持 encoded_key 故障。",
        "该测试只覆盖 32 字节 discovery log；当前测试覆盖不足。",
        "分配失败时函数返回错误且不使用 args；当前源码已按此处理。",
    ],
)
def test_sfmea_failure_mode_classifier_rejects_attempt3_normal_rows(failure_mode):
    from app.services.test_activity_contract import sfmea_failure_mode_is_risk

    assert sfmea_failure_mode_is_risk(failure_mode) is False


@pytest.mark.parametrize(
    "failure_mode",
    [
        "registry 清理失败仅记录 WARN，错误不向上传播，旧条目残留",
        "重复连接竞态使新 controller 泄漏并留下悬空 path",
        "长稳态运行后 16 位资源计数翻转，错误接受已耗尽的槽位",
    ],
)
def test_sfmea_failure_mode_classifier_keeps_real_product_hazards(failure_mode):
    from app.services.test_activity_contract import sfmea_failure_mode_is_risk

    assert sfmea_failure_mode_is_risk(failure_mode) is True


@pytest.mark.parametrize(
    "mitigation",
    [
        (
            "整改: 若发现泄漏，在对应错误路径前添加资源释放逻辑。"
            "验证: 对主要错误返回点做故障注入并检查资源计数"
        ),
        (
            "整改: 使用循环写入直到累计写满 len，短写时返回错误。"
            "验证: 注入短写和磁盘空间不足，确认错误可观测"
        ),
        (
            "整改: 保持 -1 作为不传 tos 的哨兵值并增加边界保护。"
            "验证: 运行单元测试检查参数字符串不含 tos=-1"
        ),
        (
            "整改: 在 save_discovery_log 中校验 write 返回值是否等于 len，"
            "短写时返回错误。验证: 注入短写并检查错误可观测"
        ),
    ],
)
def test_sfmea_mitigation_accepts_explicit_chinese_remediation_and_validation(
    mitigation,
):
    from app.services.test_activity_contract import sfmea_mitigation_quality_gaps

    assert sfmea_mitigation_quality_gaps(mitigation) == []


def test_sfmea_mitigation_keeps_remediation_prefixed_addition_when_it_also_mentions_a_test():
    from app.services.test_activity_contract import sfmea_mitigation_quality_gaps

    mitigation = (
        "整改: 添加更具体的 status_detail 并保留现有兼容行为。"
        "验证: 发送不含 AuthMethod 的 Login Request，确认响应 status_detail=0x07。"
    )

    assert sfmea_mitigation_quality_gaps(mitigation) == []


def test_combined_evidence_paths_does_not_treat_a_mentioned_basename_as_a_second_repo_path():
    from app.services.test_activity_contract import _combined_response_evidence_paths

    paths = _combined_response_evidence_paths(
        "证据 `lib/iscsi/conn.c:147-158`。"
        "若超时未关闭，请检查 login_timer registration in conn.c。"
    )

    assert paths == ["lib/iscsi/conn.c"]


def test_combined_sfmea_accepts_a_shared_one_to_ten_scale_definition():
    from app.services.test_activity_contract import _audit_combined_professional_completeness

    issues = _audit_combined_professional_completeness(
        "评分采用 1-10。Severity 表示业务影响；Occurrence 表示发生可能性；"
        "Detection 表示失效发生前难以发现的程度。RPN=S×O×D；RPN≥200 优先处理。",
        {},
        include_execution=False,
        include_consistency=False,
    )

    assert not any(issue["code"] == "missing_sfmea_scoring_scale" for issue in issues)


def test_source_anchor_claim_is_l1_verified_only_when_statement_matches_quote():
    from app.services.test_activity_contract import _deterministic_claim_semantics

    evidence = [{"quote": "c = libnvme_lookup_ctrl(s, &f.ctrl_params, NULL);"}]

    assert _deterministic_claim_semantics(
        claim_type="source_anchor",
        statement="c = libnvme_lookup_ctrl(s, &f.ctrl_params, NULL);",
        evidence=evidence,
    ) == ("supported", "")
    status, _ = _deterministic_claim_semantics(
        claim_type="source_anchor",
        statement="lookup performs network I/O",
        evidence=evidence,
    )
    assert status == "contradicted"


def test_professional_marker_findings_are_lint_but_harness_failures_are_l3():
    from app.services.test_activity_contract import _partition_combined_professional_issues

    structure, lint, executable = _partition_combined_professional_issues(
        [
            {"code": "missing_iscsi_professional_scenarios"},
            {"code": "missing_extended_chap_negative_scenarios"},
            {"code": "non_executable_raw_pdu_harness"},
            {"code": "sfmea_not_sorted_by_rpn"},
        ]
    )

    assert [item["code"] for item in lint] == [
        "missing_iscsi_professional_scenarios",
        "missing_extended_chap_negative_scenarios",
    ]
    assert [item["code"] for item in executable] == [
        "non_executable_raw_pdu_harness"
    ]
    assert [item["code"] for item in structure] == ["sfmea_not_sorted_by_rpn"]


def test_test_activity_contract_covers_storage_testing_profiles_and_templates():
    from app.services.test_activity_contract import (
        ARTIFACT_TEMPLATES,
        PROFILE_REGISTRY,
        build_test_activity_contract,
    )

    assert {
        "iscsi_login",
        "nvmeof_transport",
        "security_tls",
        "tcp_network",
        "bdev_io",
        "rpc_config",
        "reactor_thread_poller",
        "persistence_recovery",
        "performance_regression",
        "resource_lifecycle",
        "concurrency_race",
        "observability_diagnostics",
    }.issubset(PROFILE_REGISTRY)
    assert {
        "project_structure.md",
        "source_reading_plan.md",
        "module_map.md",
        "business_flow.md",
        "flow_map.md",
        "tester_code_understanding.md",
        "sfmea.json",
        "black_box_cases.json",
        "black_box_cases.md",
        "test_strategy.md",
        "test_design.md",
        "coverage_gap_report.md",
        "risk_review.md",
        "execution_checklist.md",
    }.issubset(ARTIFACT_TEMPLATES)

    contract = build_test_activity_contract(
        target="iSCSI login CHAP digest",
        repo_path="/Volumes/Media/dpdk/spdk",
        workflow_outputs=[
            {"id": "sfmea", "artifact": "sfmea.json", "type": "json"},
            {"id": "black_box_cases", "artifact": "black_box_cases.json", "type": "json"},
        ],
        user_requirements="输出 SFMEA 和黑盒测试用例，覆盖认证失败和 session reset。",
    )

    assert contract["target"] == "iSCSI login CHAP digest"
    assert "iscsi_login" in contract["domain_profiles"]
    assert "fragmented C-bit parameter assembly" in contract["domain_requirements"]["iscsi_login"]["required_scenarios"]
    assert (
        "mutual CHAP with valid challenge encoding but a mismatched mutual-secret oracle"
        in contract["domain_requirements"]["iscsi_login"]["required_scenarios"]
    )
    assert "half-open session before and after the first Login PDU" in contract["domain_requirements"]["iscsi_login"]["failure_modes"]
    assert contract["project_profile"]["project"] == "spdk"
    assert "lib/iscsi" in contract["project_profile"]["source_roots"]
    assert "test/iscsi_tgt" in contract["project_profile"]["test_roots"]
    assert {
        "test/iscsi_tgt/chap/chap_discovery.sh",
        "test/iscsi_tgt/login_redirection/login_redirection.sh",
        "test/unit/lib/iscsi/iscsi.c/iscsi_ut.c",
    }.issubset(contract["project_profile"]["validated_test_mappings"])
    assert contract["evidence_policy"]["source_first"] is True
    assert contract["evidence_policy"]["prefer_artifacts"] == ["GitNexus", "CGC"]
    assert contract["black_box_boundary"]["forbidden_internal_steps"]
    assert contract["quality_gates"]["required_black_box_dimensions"] == [
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
    assert set(contract["artifact_contract"]["sfmea.json"]["required_fields"]) >= {
        "failure_mode",
        "cause",
        "effect",
        "detection",
        "severity",
        "occurrence",
        "detection_score",
        "rpn",
        "score_explanation",
        "mitigation",
        "source_evidence",
        "test_mapping",
    }
    assert set(contract["artifact_contract"]["black_box_cases.json"]["required_fields"]) >= {
        "case_id",
        "preconditions",
        "steps",
        "expected_result",
        "observability",
        "failure_diagnostics",
        "mapped_test_dir",
        "source_or_test_evidence",
    }
    assert any(
        item["source"] == "domain_test_profile" and item["profile_id"] == "iscsi_login"
        for item in contract["focus_rationale"]
    )


def test_refresh_test_activity_contract_upgrades_declared_artifacts_without_losing_domain_rules():
    from app.services.test_activity_contract import refresh_test_activity_contract

    stale = {
        "domain_profiles": ["security_tls"],
        "required_outputs": ["business_flow.md", "sfmea.json"],
        "artifact_contract": {
            "business_flow.md": {"sections": ["old"]},
            "sfmea.json": {
                "required_fields": ["failure_mode"],
                "min_sfmea_rows": 12,
            },
        },
    }

    refreshed = refresh_test_activity_contract(
        stale,
        declared_artifacts=["flow_map.md", "sfmea.json", "evidence_cards.json"],
    )

    assert refreshed["domain_profiles"] == ["security_tls"]
    assert refreshed["required_outputs"] == ["flow_map.md", "sfmea.json"]
    assert refreshed["artifact_contract"]["flow_map.md"]["sections"] == [
        "外部触发",
        "流程步骤",
        "异常分支",
        "观测点",
    ]
    assert "mitigation" in refreshed["artifact_contract"]["sfmea.json"]["field_rules"]
    assert refreshed["artifact_contract"]["sfmea.json"]["min_sfmea_rows"] == 12
    assert refreshed["quality_gates"]["require_independent_behavior_validation"] is True


@pytest.mark.parametrize("verb", ["add", "validate", "require", "keep", "configure", "emit"])
def test_sfmea_actionable_mitigation_accepts_explicit_english_production_controls(verb):
    from app.services.test_activity_contract import sfmea_mitigation_is_actionable

    mitigation = (
        f"Production/config control: {verb} a preflight guard that rejects invalid credentials. "
        "Verification: run the negative attach test and assert the rejection log."
    )

    assert sfmea_mitigation_is_actionable(mitigation) is True


def test_combined_response_audits_declared_flow_map_like_business_flow():
    from app.services.test_activity_contract import audit_test_activity_response

    audit = audit_test_activity_response(
        content="# flow_map\n只有一句结论。" + "补充" * 400,
        contract={"required_outputs": ["flow_map.md"]},
    )

    assert any(
        issue["code"] == "missing_combined_business_flow"
        and issue["artifact"] == "flow_map.md"
        for issue in audit["issues"]
    )


def test_test_activity_contract_uses_declared_flow_artifact_and_actionable_sfmea_rules():
    from app.services.test_activity_contract import build_test_activity_contract

    contract = build_test_activity_contract(
        target="NVMe/TCP TLS 流程与 SFMEA",
        workflow_outputs=[
            {"id": "flow", "artifact": "flow_map.md", "type": "markdown"},
            {"id": "sfmea", "artifact": "sfmea.json", "type": "json"},
        ],
    )

    assert "flow_map.md" in contract["required_outputs"]
    assert "business_flow.md" not in contract["required_outputs"]
    assert contract["artifact_contract"]["flow_map.md"]["sections"] == [
        "外部触发",
        "流程步骤",
        "异常分支",
        "观测点",
    ]
    assert contract["artifact_contract"]["sfmea.json"]["field_rules"]["mitigation"] == (
        "每条 mitigation 必须同时包含具体整改动作，以及可执行的测试、监控或日志验证动作。"
    )


def test_test_design_mindmap_uses_its_own_contract_instead_of_plain_test_design():
    from app.services.test_activity_contract import build_test_activity_contract

    contract = build_test_activity_contract(
        target="NVMe/TCP 测试设计脑图",
        workflow_outputs=[
            {
                "id": "mindmap",
                "artifact": "test_design_mindmap.md",
                "type": "markdown",
            }
        ],
    )

    assert "test_design_mindmap.md" in contract["required_outputs"]
    assert "test_design.md" not in contract["required_outputs"]
    assert contract["artifact_contract"]["test_design_mindmap.md"]["required_terms"] == [
        "目标",
        "输入",
        "源码证据",
        "业务流程",
        "SFMEA",
        "黑盒用例",
        "观测点",
        "剩余风险",
    ]


def test_test_design_mindmap_audit_accepts_mermaid_branches(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_artifacts,
        build_test_activity_contract,
    )

    contract = build_test_activity_contract(
        target="NVMe/TCP 测试设计脑图",
        workflow_outputs=[
            {
                "id": "mindmap",
                "artifact": "test_design_mindmap.md",
                "type": "markdown",
            }
        ],
    )
    repo = tmp_path / "nvme-cli"
    (repo / "libnvme/src/nvme").mkdir(parents=True)
    (repo / "libnvme/test/ioctl").mkdir(parents=True)
    (repo / "libnvme/src/nvme/fabrics.c").write_text("int connect_ctrl(void);\n")
    (repo / "libnvme/test/ioctl/discovery.c").write_text("int test_discovery(void);\n")
    (tmp_path / "test_design_mindmap.md").write_text(
        """# NVMe/TCP 测试设计脑图

```mermaid
mindmap
  root((NVMe/TCP))
    目标
    输入
    源码证据
      libnvme/src/nvme/fabrics.c
      libnvme/test/ioctl/discovery.c
    业务流程
    SFMEA
    黑盒用例
    观测点
    剩余风险
```
""",
        encoding="utf-8",
    )

    audit = audit_test_activity_artifacts(
        artifact_dir=tmp_path,
        contract=contract,
        repo_path=str(repo),
    )

    assert audit["status"] == "deliverable"
    assert audit["issues"] == []


def test_sfmea_audit_rejects_non_risks_and_absence_of_evidence_claims(tmp_path):
    from app.services.test_activity_contract import _audit_json_artifact

    rows = [
        {
            "sfmea_id": "SFMEA-001",
            "failure_mode": "当前源码不支持该 failure mode",
            "cause": "修复模型确认原结论错误",
        },
        {
            "sfmea_id": "SFMEA-002",
            "failure_mode": "连接超时后发生 fd 泄漏",
            "cause": "当前片段未显示 fd 清理",
        },
        {
            "sfmea_id": "SFMEA-003",
            "failure_mode": "被测产品连接流程会崩溃",
            "cause": "测试 helper 未检查 NULL",
            "source_evidence": ["test/connect.c::test_connect"],
        },
    ]

    issues = _audit_json_artifact(
        artifact="sfmea.json",
        payload=rows,
        spec={},
        repo=tmp_path,
    )
    codes = {issue["code"] for issue in issues}

    assert "non_risk_sfmea_row" in codes
    assert "absence_of_evidence_as_defect" in codes
    assert "test_harness_risk_as_product_risk" in codes


def test_prepare_workbench_task_run_builds_test_activity_contract_for_executor(tmp_path):
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer
    from app.services.workflow_dsl import WorkflowStore

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text(
        "int spdk_iscsi_login(void) { return 0; }\n",
        encoding="utf-8",
    )
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "iscsi_contract_workflow",
        "name": "iSCSI contract workflow",
        "version": 1,
        "inputs": [{"id": "analysis_object", "type": "free_text", "role": "分析目标"}],
        "steps": [
            {
                "id": "design",
                "type": "agent_task",
                "provider": "builtin-llm",
                "skills": ["sfmea", "black-box-test-design"],
                "required_artifacts": ["sfmea.json", "black_box_cases.json"],
            }
        ],
        "outputs": [
            {"id": "sfmea", "type": "json", "from": "design", "artifact": "sfmea.json"},
            {
                "id": "black_box_cases",
                "type": "json",
                "from": "design",
                "artifact": "black_box_cases.json",
            },
        ],
    })

    task_run = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="iscsi_contract_workflow",
        workspace_id="ws-iscsi-contract",
        repo_path=str(repo),
        inputs={"analysis_object": "iSCSI login CHAP digest session reset"},
    )

    contract = task_run.task_bundle["test_activity_contract"]
    assert "iscsi_login" in contract["domain_profiles"]
    assert task_run.task_bundle["workflow_contract"]["test_activity_contract"] == contract
    step_bundle = json.loads(
        (
            Path(task_run.artifact_dir)
            / "agent_runs"
            / "design"
            / "task_bundle.json"
        ).read_text(encoding="utf-8")
    )
    output_contract = json.loads(
        (
            Path(task_run.artifact_dir)
            / "agent_runs"
            / "design"
            / "agent_output_contract.json"
        ).read_text(encoding="utf-8")
    )
    assert step_bundle["execution_contract"]["test_activity_contract"] == contract
    assert output_contract["test_activity_contract"] == contract
    assert (Path(task_run.artifact_dir) / "test_activity_contract.json").exists()


def test_test_activity_quality_audit_flags_shallow_or_graybox_artifacts(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_artifacts,
        build_test_activity_contract,
    )

    artifact_dir = tmp_path / "run"
    artifact_dir.mkdir()
    contract = build_test_activity_contract(
        target="nvme tcp tls",
        repo_path=str(tmp_path),
        workflow_outputs=[
            {"id": "sfmea", "artifact": "sfmea.json", "type": "json"},
            {"id": "black_box_cases", "artifact": "black_box_cases.json", "type": "json"},
        ],
        user_requirements="输出测试设计",
    )
    (artifact_dir / "sfmea.json").write_text(
        json.dumps([
            {
                "failure_mode": "TLS 握手失败",
                "cause": "证书错误",
                "effect": "连接失败",
                "severity": 8,
            }
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    (artifact_dir / "black_box_cases.json").write_text(
        json.dumps([
            {
                "case_id": "bb-001",
                "steps": ["call nvmf_tls_init() directly"],
                "expected_result": "失败",
            }
        ], ensure_ascii=False),
        encoding="utf-8",
    )

    audit = audit_test_activity_artifacts(
        artifact_dir=artifact_dir,
        contract=contract,
        repo_path=str(tmp_path),
    )

    assert audit["status"] == "needs_rework"
    assert audit["deliverable"] is False
    assert audit["score"] < 80
    assert any(issue["code"] == "missing_sfmea_fields" for issue in audit["issues"])
    assert any(issue["code"] == "black_box_boundary_violation" for issue in audit["issues"])
    assert audit["recommendations"][0].startswith("补齐")


def test_black_box_quality_rejects_chinese_internal_call_and_return_code_only():
    from app.services.test_activity_contract import (
        _black_box_boundary_violation,
        _black_box_expected_result_is_observable,
    )

    case = {
        "steps": ["调用 libnvmf_create_raw_secret(ctx, secret, 16, &raw, &length)"],
        "expected_result": "返回 -EINVAL",
    }

    assert _black_box_boundary_violation(case) is True
    assert _black_box_expected_result_is_observable(case["expected_result"]) is False


@pytest.mark.parametrize(
    "step",
    [
        "执行测试程序，调用 libnvmf_import_tls_key_versioned",
        "测试程序传入 NULL hostnqn 后直接调用 libnvmf_gen_dhchap_key",
        "invoke libnvme_ctrl_set_dhchap_host_key with a NULL value",
    ],
)
def test_black_box_boundary_rejects_direct_library_api_harness_steps(step):
    from app.services.test_activity_contract import _black_box_boundary_violation

    assert _black_box_boundary_violation({"steps": [step]}) is True


def test_black_box_delivery_gate_rejects_source_mapping_and_unit_test_fallback(
    tmp_path,
):
    from app.services.test_activity_contract import _audit_json_artifact

    repo = tmp_path / "nvme-cli"
    (repo / "libnvme" / "src" / "nvme").mkdir(parents=True)
    (repo / "libnvme" / "test" / "ioctl").mkdir(parents=True)
    (repo / "libnvme" / "test" / "ioctl" / "discovery.c").write_text(
        "int main(void) { return 0; }\n",
        encoding="utf-8",
    )
    case = {
        "case_id": "BB-10",
        "test_dimension": "boundary",
        "scenario_name": "discovery record boundary",
        "preconditions": ["discovery controller is reachable"],
        "steps": [
            "run nvme discover; if injection is unavailable, convert this to a unit test candidate"
        ],
        "expected_result": "the command exits with a visible status",
        "observability": ["exit code and stderr"],
        "failure_diagnostics": ["capture command output"],
        "mapped_test_dir": "libnvme/src/nvme/",
        "source_or_test_evidence": ["libnvme/test/ioctl/discovery.c"],
    }

    issues = _audit_json_artifact(
        artifact="black_box_cases.json",
        payload=[case],
        spec={"required_fields": list(case)},
        repo=repo,
    )

    assert any(
        issue["code"] == "missing_test_directory_mapping" for issue in issues
    )
    assert any(issue["code"] == "black_box_boundary_violation" for issue in issues)


def test_black_box_delivery_gate_accepts_existing_test_directory(tmp_path):
    from app.services.test_activity_contract import black_box_case_delivery_quality_gaps

    repo = tmp_path / "nvme-cli"
    (repo / "libnvme" / "test").mkdir(parents=True)
    case = {
        "steps": [
            "run nvme discover and record the public command return value"
        ],
        "expected_result": (
            "记录公开 CLI 返回值；不要用内部 mock 冒充黑盒结果"
        ),
        "mapped_test_dir": "libnvme/test/",
    }

    assert black_box_case_delivery_quality_gaps(case, repo_path=str(repo)) == []


def test_black_box_delivery_gate_accepts_multiple_existing_test_mappings(tmp_path):
    from app.services.test_activity_contract import black_box_case_delivery_quality_gaps

    repo = tmp_path / "nvme-cli"
    first = repo / "libnvme" / "test" / "config-api.c"
    second = repo / "libnvme" / "test" / "psk.c"
    first.parent.mkdir(parents=True)
    first.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    second.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    case = {
        "steps": ["run nvme discover and record the public command result"],
        "expected_result": "the command exit code and stderr are recorded",
        "mapped_test_dir": "libnvme/test/config-api.c; libnvme/test/psk.c",
    }

    assert black_box_case_delivery_quality_gaps(case, repo_path=str(repo)) == []


def test_explicit_claim_accepts_base_evidence_card_id_with_exact_quote(tmp_path):
    import hashlib

    from app.services.test_activity_contract import (
        _audit_explicit_technical_claims,
        _verified_evidence_files,
    )

    repo = tmp_path / "nvme-cli"
    source = repo / "libnvme" / "src" / "nvme" / "fabrics.c"
    source.parent.mkdir(parents=True)
    source.write_text(
        "static int discover(void)\n"
        "{\n"
        "\treturn 0;\n"
        "}\n",
        encoding="utf-8",
    )
    root = tmp_path / "artifacts"
    root.mkdir()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    (root / "evidence_cards.json").write_text(
        json.dumps(
            [
                {
                    "evidence_id": "EV-FAB-001",
                    "file_path": "libnvme/src/nvme/fabrics.c",
                    "start_line": 1,
                    "end_line": 4,
                    "excerpt": "static int discover(void)\n{\n\treturn 0;\n}",
                    "sha256": digest,
                }
            ]
        ),
        encoding="utf-8",
    )
    verified_files = _verified_evidence_files(root=root, repo=repo)
    rows = [
        {
            "sfmea_id": "SFMEA-001",
            "source_evidence": ["EV-FAB-001"],
            "technical_claims": [
                {
                    "claim_id": "TC-001",
                    "type": "source_anchor",
                    "statement": "return 0;",
                    "evidence": [
                        {
                            "evidence_id": "EV-FAB-001",
                            "path": "libnvme/src/nvme/fabrics.c",
                            "symbol": "discover",
                            "lines": "L3",
                            "quote": "return 0;",
                        }
                    ],
                }
            ],
        }
    ]

    claims, issues = _audit_explicit_technical_claims(
        artifact="sfmea.json",
        rows=rows,
        verified_files=verified_files,
    )

    assert issues == []
    assert claims[0]["status"] == "verified"
    assert claims[0]["evidence"][0]["evidence_id_matches"] is True


def test_black_box_quality_accepts_measurable_performance_oracle():
    from app.services.test_activity_contract import (
        _black_box_expected_result_is_observable,
    )

    assert _black_box_expected_result_is_observable(
        "real 时间不超过基线的 2 倍"
    ) is True


def test_performance_oracle_requires_reproducible_basis():
    from app.services.test_activity_contract import (
        black_box_oracle_basis_quality_gaps,
    )

    case = {
        "test_dimension": "performance",
        "expected_result": "P95 耗时不得超过基线的 2 倍",
    }

    assert black_box_oracle_basis_quality_gaps(case) == [
        "missing_oracle_basis",
        "missing_performance_sampling_plan",
    ]

    case["oracle_basis"] = (
        "在同一内核、目标配置和网络环境预热 5 次后重复 30 次，"
        "记录 P50/P95；阈值取未改动 commit 的 P95 基线与方差。"
    )
    assert black_box_oracle_basis_quality_gaps(case) == []


def test_resource_and_timeout_oracles_require_source_or_configuration_basis():
    from app.services.test_activity_contract import (
        black_box_oracle_basis_quality_gaps,
    )

    assert black_box_oracle_basis_quality_gaps({
        "test_dimension": "resource_wraparound",
        "expected_result": "65536 次操作后计数器不得翻转为 0",
        "oracle_basis": "循环执行并观察计数",
    }) == ["oracle_basis_not_traceable"]
    assert black_box_oracle_basis_quality_gaps({
        "test_dimension": "timeout",
        "expected_result": "5 秒内返回超时错误",
        "oracle_basis": "来自命令行 --timeout=5 配置及对应帮助文本证据",
    }) == []


def test_black_box_contract_requires_storage_lifecycle_dimensions():
    from app.services.test_activity_contract import BLACK_BOX_REQUIRED_DIMENSIONS

    assert BLACK_BOX_REQUIRED_DIMENSIONS == [
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


def test_cross_artifact_audit_rejects_stale_sfmea_and_case_ids_in_mindmap(tmp_path):
    from app.services.test_activity_contract import _audit_cross_artifact_references

    (tmp_path / "sfmea.json").write_text(
        json.dumps([{"sfmea_id": "SFMEA-001"}]), encoding="utf-8"
    )
    (tmp_path / "black_box_cases.json").write_text(
        json.dumps([{"case_id": "BB-001"}]), encoding="utf-8"
    )
    (tmp_path / "test_design_mindmap.md").write_text(
        "```mermaid\nmindmap\n root\n  SFMEA-001\n  SFMEA-007\n  BB-001\n  BB-099\n```\n",
        encoding="utf-8",
    )

    issues = _audit_cross_artifact_references(
        root=tmp_path,
        declared_artifacts={
            "sfmea.json",
            "black_box_cases.json",
            "test_design_mindmap.md",
        },
    )

    assert issues == [{
        "code": "stale_cross_artifact_reference",
        "artifact": "test_design_mindmap.md",
        "message": "test_design_mindmap.md 引用了当前交付件中不存在的条目: BB-099, SFMEA-007",
        "references": ["BB-099", "SFMEA-007"],
    }]


def test_strategy_rejects_complete_coverage_claim_when_gaps_remain(tmp_path):
    from app.services.test_activity_contract import _audit_markdown_artifact

    issues = _audit_markdown_artifact(
        artifact="test_strategy.md",
        content=(
            "# 测试策略\n已完整覆盖认证、重连和资源回收。\n"
            "## 证据缺口\nTLS 异常传播仍待补证据，长稳态尚未覆盖。\n"
        ),
        spec={},
        repo=tmp_path,
    )

    assert any(
        issue["code"] == "unsupported_complete_coverage_claim"
        for issue in issues
    )


def test_structured_evidence_paths_ignore_human_annotations():
    from app.services.test_activity_contract import _strict_evidence_path_strings

    paths = _strict_evidence_path_strings({
        "source_or_test_evidence": [
            "test/iscsi_tgt/chap/chap_discovery.sh (CHAP 发现登录)",
            "lib/iscsi/iscsi.c::iscsi_auth_params",
        ]
    })

    assert paths == [
        "test/iscsi_tgt/chap/chap_discovery.sh",
        "lib/iscsi/iscsi.c",
    ]


def test_markdown_sections_accept_content_organized_under_nested_headings(tmp_path):
    from app.services.test_activity_contract import _audit_markdown_artifact

    repo = tmp_path / "spdk"
    source = repo / "lib" / "iscsi" / "iscsi.c"
    test = repo / "test" / "iscsi_tgt" / "login.sh"
    source.parent.mkdir(parents=True)
    test.parent.mkdir(parents=True)
    source.write_text("int login(void);\n", encoding="utf-8")
    test.write_text("#!/bin/sh\n", encoding="utf-8")
    content = """# Flow
## 外部触发
- TCP login request
## 流程步骤
### 1. 接收登录
处理 `lib/iscsi/iscsi.c`。
## 异常分支
### A. 认证失败
执行 `test/iscsi_tgt/login.sh`。
## 观测点
### 日志
检查 Login Response。
"""

    issues = _audit_markdown_artifact(
        artifact="flow_map.md",
        content=content,
        spec={"sections": ["外部触发", "流程步骤", "异常分支", "观测点"]},
        repo=repo,
    )

    assert "empty_markdown_sections" not in {item["code"] for item in issues}


def test_markdown_sections_accept_descriptive_heading_suffixes(tmp_path):
    from app.services.test_activity_contract import _audit_markdown_artifact

    content = """# Flow
## 外部触发
CLI request.
## 流程步骤（主流程表）
One step.
## 异常分支
One branch.
## 观测点与证据引用
Check exit status and logs.
"""

    issues = _audit_markdown_artifact(
        artifact="flow_map.md",
        content=content,
        spec={"sections": ["外部触发", "流程步骤", "异常分支", "观测点"]},
        repo=tmp_path,
    )

    assert "missing_markdown_sections" not in {item["code"] for item in issues}


def test_markdown_evidence_accepts_top_level_source_and_nested_test_directories(
    tmp_path,
):
    from app.services.test_activity_contract import _audit_markdown_artifact

    repo = tmp_path / "nvme-cli"
    source = repo / "fabrics.c"
    nested_source = repo / "libnvme" / "src" / "nvme" / "fabrics.c"
    nested_test = repo / "libnvme" / "test" / "psk.c"
    source.parent.mkdir(parents=True)
    nested_source.parent.mkdir(parents=True)
    nested_test.parent.mkdir(parents=True)
    source.write_text("int fabrics_discovery(void);\n", encoding="utf-8")
    nested_source.write_text("int nvmf_connect(void);\n", encoding="utf-8")
    nested_test.write_text("int psk_test(void);\n", encoding="utf-8")
    content = """# Flow
## 外部触发
`fabrics.c`
## 流程步骤
`libnvme/src/nvme/fabrics.c`
## 异常分支
`libnvme/test/psk.c`
## 观测点
检查连接状态。
"""

    issues = _audit_markdown_artifact(
        artifact="flow_map.md",
        content=content,
        spec={"sections": ["外部触发", "流程步骤", "异常分支", "观测点"]},
        repo=repo,
    )

    codes = {item["code"] for item in issues}
    assert "missing_source_evidence" not in codes
    assert "missing_test_evidence" not in codes


def test_module_analysis_quality_audit_rejects_shallow_markdown(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_artifacts,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    artifact_dir = tmp_path / "run"
    artifact_dir.mkdir()
    (artifact_dir / "module_analysis.md").write_text("done\n", encoding="utf-8")
    contract = build_test_activity_contract(
        target="iSCSI login",
        repo_path=str(repo),
        workflow_outputs=[
            {
                "id": "report",
                "artifact": "module_analysis.md",
                "type": "markdown",
            }
        ],
        user_requirements="分析主流程、异常恢复和测试证据",
    )

    audit = audit_test_activity_artifacts(
        artifact_dir=artifact_dir,
        contract=contract,
        repo_path=str(repo),
    )

    assert audit["status"] == "needs_rework"
    assert audit["deliverable"] is False
    codes = {issue["code"] for issue in audit["issues"]}
    assert "missing_markdown_sections" in codes
    assert "missing_source_evidence" in codes
    assert "missing_test_evidence" in codes


def test_combined_report_quality_rejects_too_few_sfmea_and_black_box_cases(tmp_path):
    from app.services.test_activity_contract import audit_test_activity_artifacts

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    for name in ("iscsi.c", "conn.c", "tgt_node.c"):
        (repo / "lib" / "iscsi" / name).write_text("int login(void);\n", encoding="utf-8")
    for name in ("login.sh", "chap.sh"):
        (repo / "test" / "iscsi_tgt" / name).write_text("#!/bin/sh\n", encoding="utf-8")
    artifact_dir = tmp_path / "run"
    artifact_dir.mkdir()
    report = """# 报告
## 分析范围与证据缺口
范围与缺口。
## 关键源码证据
`lib/iscsi/iscsi.c` `lib/iscsi/conn.c` `lib/iscsi/tgt_node.c`
`test/iscsi_tgt/login.sh` `test/iscsi_tgt/chap.sh`
## 主流程与异常/恢复流程
主流程、认证失败和恢复流程。
## SFMEA
| ID | failure mode | cause | effect | detection | S | O | D | RPN | mitigation | evidence |
|---|---|---|---|---|---:|---:|---:|---:|---|---|
| F1 | fail | cause | effect | log | 8 | 3 | 4 | 96 | test | lib/iscsi/iscsi.c |
## 黑盒测试用例
### BB01 登录失败
前置条件、外部步骤、预期结果、观测点、失败诊断、test/iscsi_tgt/login.sh。
"""
    (artifact_dir / "report.md").write_text(report, encoding="utf-8")
    contract = {
        "artifact_contract": {
            "report.md": {
                "sections": [
                    "分析范围与证据缺口",
                    "关键源码证据",
                    "主流程与异常/恢复流程",
                    "SFMEA",
                    "黑盒测试用例",
                ],
                "min_sfmea_rows": 12,
                "min_black_box_cases": 12,
                "min_source_paths": 3,
                "min_test_paths": 2,
            }
        }
    }

    audit = audit_test_activity_artifacts(
        artifact_dir=artifact_dir,
        contract=contract,
        repo_path=str(repo),
    )

    codes = {issue["code"] for issue in audit["issues"]}
    assert "insufficient_sfmea_rows" in codes
    assert "insufficient_black_box_cases" in codes


def test_combined_report_output_uses_declared_filename_without_expanding_nested_artifacts():
    from app.services.test_activity_contract import build_test_activity_contract

    contract = build_test_activity_contract(
        target="SPDK iSCSI login 流程、SFMEA、黑盒测试用例",
        repo_path="/repo/spdk",
        workflow_outputs=[
            {
                "id": "report",
                "type": "combined_test_report",
                "artifact": "report.md",
            }
        ],
    )

    assert contract["required_outputs"] == ["report.md"]
    assert contract["artifact_contract"]["report.md"] == {
        "artifact": "report.md",
        "preview": "markdown",
        "required_fields": [],
        "sections": [
            "分析范围与证据缺口",
            "关键源码证据",
            "主流程与异常/恢复流程",
            "SFMEA",
            "黑盒测试用例",
        ],
        "quality_checks": [
            "required_fields_present",
            "source_or_test_evidence_present",
            "black_box_boundary_respected",
        ],
        "download_filename": "report.md",
        "min_sfmea_rows": 12,
        "min_black_box_cases": 12,
        "min_source_paths": 6,
        "min_test_paths": 4,
    }


def test_combined_report_quality_counts_numeric_sfmea_row_ids(tmp_path):
    from app.services.test_activity_contract import _audit_markdown_artifact

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text("int login;\n", encoding="utf-8")
    (repo / "test" / "iscsi_tgt" / "login.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    content = """# 报告
## SFMEA
| # | Failure Mode | S | O | D | RPN |
|---|---|---:|---:|---:|---:|
| 1 | login fail | 8 | 3 | 4 | 96 |
`lib/iscsi/iscsi.c` `test/iscsi_tgt/login.sh`
"""
    issues = _audit_markdown_artifact(
        artifact="report.md",
        content=content,
        spec={"sections": ["SFMEA"], "min_sfmea_rows": 1},
        repo=repo,
    )
    assert "insufficient_sfmea_rows" not in {item["code"] for item in issues}


def test_combined_report_quality_counts_fmea_prefixed_row_ids(tmp_path):
    from app.services.test_activity_contract import _audit_markdown_artifact

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text("int login;\n", encoding="utf-8")
    (repo / "test" / "iscsi_tgt" / "login.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    content = """# 报告
## SFMEA
| 编号 | 故障模式 | S | O | D | RPN |
|---|---|---:|---:|---:|---:|
| FMEA-01 | login fail | 8 | 3 | 4 | 96 |
`lib/iscsi/iscsi.c` `test/iscsi_tgt/login.sh`
"""
    issues = _audit_markdown_artifact(
        artifact="report.md",
        content=content,
        spec={"sections": ["SFMEA"], "min_sfmea_rows": 1},
        repo=repo,
    )
    assert "insufficient_sfmea_rows" not in {item["code"] for item in issues}


def test_combined_report_quality_counts_numbered_subsections_only_inside_black_box_section(tmp_path):
    from app.services.test_activity_contract import _audit_markdown_artifact

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text("int login;\n", encoding="utf-8")
    (repo / "test" / "iscsi_tgt" / "login.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    content = """# 报告
## 4. SFMEA
### 4.1 这不是黑盒用例
风险说明。
## 5. 黑盒测试用例
### 5.1 正常登录
前置条件、外部步骤、预期结果、观测点与失败诊断。
### 5.2 认证失败
前置条件、外部步骤、预期结果、观测点与失败诊断。
`lib/iscsi/iscsi.c` `test/iscsi_tgt/login.sh`
"""
    issues = _audit_markdown_artifact(
        artifact="report.md",
        content=content,
        spec={"sections": ["黑盒测试用例"], "min_black_box_cases": 2},
        repo=repo,
    )
    assert "insufficient_black_box_cases" not in {item["code"] for item in issues}


def test_combined_report_quality_counts_bbc_table_case_ids(tmp_path):
    from app.services.test_activity_contract import _audit_markdown_artifact

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text("int login;\n", encoding="utf-8")
    (repo / "test" / "iscsi_tgt" / "login.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    rows = "\n".join(
        f"| BBC-{index:03d} | 场景 {index} | 前置 | 外部步骤 | 预期 | 观测 | 诊断 | test/iscsi_tgt/login.sh |"
        for index in range(1, 13)
    )
    content = f"""# 报告
## 5. 黑盒测试用例
| ID | 场景 | 前置条件 | 外部步骤 | 预期结果 | 观测点 | 失败诊断 | 真实测试目录映射 |
|---|---|---|---|---|---|---|---|
{rows}
`lib/iscsi/iscsi.c` `test/iscsi_tgt/login.sh`
"""

    issues = _audit_markdown_artifact(
        artifact="report.md",
        content=content,
        spec={"sections": ["黑盒测试用例"], "min_black_box_cases": 12},
        repo=repo,
    )

    assert "insufficient_black_box_cases" not in {item["code"] for item in issues}


def test_combined_report_quality_counts_short_b_table_case_ids(tmp_path):
    from app.services.test_activity_contract import _audit_markdown_artifact

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text("int login;\n", encoding="utf-8")
    (repo / "test" / "iscsi_tgt" / "login.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    rows = "\n".join(
        f"| B{index} | 场景 {index} | 前置 | 外部步骤 | 预期 | 观测 | 诊断 | test/iscsi_tgt/login.sh |"
        for index in range(1, 13)
    )
    content = f"""# 报告
## 6. 黑盒测试用例
| ID | 场景 | 前置条件 | 外部步骤 | 预期结果 | 观测点 | 失败诊断 | 真实测试目录映射 |
|---|---|---|---|---|---|---|---|
{rows}
`lib/iscsi/iscsi.c` `test/iscsi_tgt/login.sh`
"""

    issues = _audit_markdown_artifact(
        artifact="report.md",
        content=content,
        spec={"sections": ["黑盒测试用例"], "min_black_box_cases": 12},
        repo=repo,
    )

    assert "insufficient_black_box_cases" not in {item["code"] for item in issues}


def test_combined_report_quality_ignores_shell_comments_inside_fenced_blocks(tmp_path):
    from app.services.test_activity_contract import _audit_markdown_artifact

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text("int login;\n", encoding="utf-8")
    (repo / "test" / "iscsi_tgt" / "login.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    rows = "\n".join(
        f"| B{index} | 场景 {index} | 前置 | 外部步骤 | 预期 | 观测 | 诊断 | test/iscsi_tgt/login.sh |"
        for index in range(1, 13)
    )
    content = f"""# 报告
## 6. 黑盒测试用例
```bash
sudo tcpdump -w /tmp/login.pcap &
# run the case here
```
| ID | 场景 | 前置条件 | 外部步骤 | 预期结果 | 观测点 | 失败诊断 | 真实测试目录映射 |
|---|---|---|---|---|---|---|---|
{rows}
`lib/iscsi/iscsi.c` `test/iscsi_tgt/login.sh`
## 7. 附录
后续内容。
"""

    issues = _audit_markdown_artifact(
        artifact="report.md",
        content=content,
        spec={"sections": ["黑盒测试用例"], "min_black_box_cases": 12},
        repo=repo,
    )

    assert "insufficient_black_box_cases" not in {item["code"] for item in issues}


@pytest.mark.parametrize("fenced", [False, True])
def test_combined_report_quality_requires_unique_unfenced_black_box_case_ids(
    tmp_path, fenced
):
    from app.services.test_activity_contract import _audit_markdown_artifact

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text("int login;\n", encoding="utf-8")
    (repo / "test" / "iscsi_tgt" / "login.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    if fenced:
        rows = "\n".join(
            f"| B{index} | 场景 | 前置 | 外部步骤 | 预期 | 观测 | 诊断 | test/iscsi_tgt/login.sh |"
            for index in range(1, 13)
        )
        rows = f"```markdown\n{rows}\n```"
    else:
        rows = "\n".join(
            "| B1 | 重复场景 | 前置 | 外部步骤 | 预期 | 观测 | 诊断 | test/iscsi_tgt/login.sh |"
            for _ in range(12)
        )
    content = f"""# 报告
## 黑盒测试用例
| ID | 场景 | 前置条件 | 外部步骤 | 预期结果 | 观测点 | 失败诊断 | 真实测试目录映射 |
|---|---|---|---|---|---|---|---|
{rows}
`lib/iscsi/iscsi.c` `test/iscsi_tgt/login.sh`
"""

    issues = _audit_markdown_artifact(
        artifact="report.md",
        content=content,
        spec={"sections": ["黑盒测试用例"], "min_black_box_cases": 12},
        repo=repo,
    )

    assert "insufficient_black_box_cases" in {item["code"] for item in issues}


def test_combined_report_quality_deduplicates_heading_and_table_case_ids(tmp_path):
    from app.services.test_activity_contract import _audit_markdown_artifact

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text("int login;\n", encoding="utf-8")
    (repo / "test" / "iscsi_tgt" / "login.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    repeated = "\n".join(
        f"### B-{index} 场景 {index}\n"
        f"| B{index} | 场景 {index} | 前置 | 外部步骤 | 预期 | 观测 | 诊断 | test/iscsi_tgt/login.sh |"
        for index in range(1, 7)
    )
    content = f"""# 报告
## 黑盒测试用例
{repeated}
`lib/iscsi/iscsi.c` `test/iscsi_tgt/login.sh`
"""

    issues = _audit_markdown_artifact(
        artifact="report.md",
        content=content,
        spec={"sections": ["黑盒测试用例"], "min_black_box_cases": 12},
        repo=repo,
    )

    assert "insufficient_black_box_cases" in {item["code"] for item in issues}


def test_combined_report_quality_respects_four_backtick_fences(tmp_path):
    from app.services.test_activity_contract import _audit_markdown_artifact

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text("int login;\n", encoding="utf-8")
    (repo / "test" / "iscsi_tgt" / "login.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    fenced_rows = "\n".join(
        f"| B{index} | 示例 | 前置 | 外部步骤 | 预期 | 观测 | 诊断 | test/iscsi_tgt/login.sh |"
        for index in range(1, 13)
    )
    content = f"""# 报告
## 黑盒测试用例
````markdown
```text
嵌套的三反引号示例
{fenced_rows}
```
````
`lib/iscsi/iscsi.c` `test/iscsi_tgt/login.sh`
"""

    issues = _audit_markdown_artifact(
        artifact="report.md",
        content=content,
        spec={"sections": ["黑盒测试用例"], "min_black_box_cases": 12},
        repo=repo,
    )

    assert "insufficient_black_box_cases" in {item["code"] for item in issues}


def test_combined_report_quality_ignores_indented_code_block_tables(tmp_path):
    from app.services.test_activity_contract import _audit_markdown_artifact

    repo = tmp_path / "spdk"
    repo.mkdir()
    rows = "\n".join(
        f"    | B{index} | 示例 | 前置 | 外部步骤 | 预期 | 观测 | 诊断 | test/iscsi_tgt/login.sh |"
        for index in range(1, 13)
    )
    content = f"""# 报告
## 黑盒测试用例
{rows}
"""

    issues = _audit_markdown_artifact(
        artifact="report.md",
        content=content,
        spec={"sections": ["黑盒测试用例"], "min_black_box_cases": 12},
        repo=repo,
    )

    assert "insufficient_black_box_cases" in {item["code"] for item in issues}


def test_combined_report_quality_ignores_space_tab_indented_code_block_tables(tmp_path):
    from app.services.test_activity_contract import _audit_markdown_artifact

    repo = tmp_path / "spdk"
    repo.mkdir()
    rows = "\n".join(
        f" \t| B{index} | 示例 | 前置 | 外部步骤 | 预期 | 观测 | 诊断 | test/iscsi_tgt/login.sh |"
        for index in range(1, 13)
    )
    content = f"""# 报告
## 黑盒测试用例
{rows}
"""

    issues = _audit_markdown_artifact(
        artifact="report.md",
        content=content,
        spec={"sections": ["黑盒测试用例"], "min_black_box_cases": 12},
        repo=repo,
    )

    assert "insufficient_black_box_cases" in {item["code"] for item in issues}


def test_combined_report_quality_does_not_treat_escaped_pipes_as_table_cells(tmp_path):
    from app.services.test_activity_contract import _audit_markdown_artifact

    repo = tmp_path / "spdk"
    repo.mkdir()
    rows = "\n".join(
        f"| B{index} | 场景 \\| 前置 \\| 外部步骤 \\| 预期 \\| 观测 \\| 诊断 |"
        for index in range(1, 13)
    )
    content = f"""# 报告
## 黑盒测试用例
{rows}
"""

    issues = _audit_markdown_artifact(
        artifact="report.md",
        content=content,
        spec={"sections": ["黑盒测试用例"], "min_black_box_cases": 12},
        repo=repo,
    )

    assert "insufficient_black_box_cases" in {item["code"] for item in issues}


def test_markdown_table_cells_preserves_pipe_inside_multi_backtick_code_span():
    from app.services.test_activity_contract import _markdown_table_cells

    cells = _markdown_table_cells("| B1 | `` `x|y` `` | expected |")

    assert cells == ["B1", "`` `x|y` ``", "expected"]


def test_markdown_table_cells_treats_unclosed_backticks_as_plain_text():
    from app.services.test_activity_contract import _markdown_table_cells

    cells = _markdown_table_cells("| B1 | `unfinished | expected | evidence |")

    assert cells == ["B1", "`unfinished", "expected", "evidence"]


def test_markdown_table_cells_ignores_escaped_backtick_as_closing_delimiter():
    from app.services.test_activity_contract import _markdown_table_cells

    cells = _markdown_table_cells(r"| B1 | `unfinished \` | expected | evidence |")

    assert cells == ["B1", "`unfinished `", "expected", "evidence"]


def test_markdown_section_normalization_accepts_descriptive_parenthetical_suffix():
    from app.services.test_activity_contract import _normalized_markdown_heading

    assert _normalized_markdown_heading("主流程 (Connect 到首个 I/O)") == "主流程"
    assert _normalized_markdown_heading("异常与恢复路径（网络与控制器）") == "异常与恢复路径"
    assert _normalized_markdown_heading("7. 黑盒测试用例矩阵") == "黑盒测试用例"


def test_combined_black_box_case_blocks_accepts_short_b_headings():
    from app.services.test_activity_contract import _combined_black_box_case_blocks

    blocks = _combined_black_box_case_blocks(
        "## 黑盒测试用例\n#### B15. MCS 容量边界\n- 预期结果：拒绝第二连接。\n"
    )

    assert blocks == [("B15. MCS 容量边界", "\n- 预期结果：拒绝第二连接。\n")]


def test_raw_pdu_capabilities_read_cid_from_login_bhs_bytes_20_to_21():
    import ast

    from app.services.test_activity_contract import _raw_pdu_ast_capabilities

    tree = ast.parse(
        "def build_login(cid):\n"
        "    bhs = bytearray(48)\n"
        "    bhs[20:22] = cid.to_bytes(2, 'big')\n"
        "    return bhs\n"
        "def build_login_with_struct(cid, tsih):\n"
        "    bhs = bytearray(48)\n"
        "    struct.pack_into('!H', bhs, 14, tsih)\n"
        "    struct.pack_into('!H', bhs, 20, cid)\n"
        "    return bhs\n"
        "def capture_tsih(response):\n"
        "    tsih = struct.unpack_from('!H', response, 14)[0]\n"
        "    return tsih\n"
    )

    capabilities = _raw_pdu_ast_capabilities([tree])
    assert "distinct_cid_input" in capabilities
    assert "nonzero_tsih_input" in capabilities
    assert "response_tsih_capture" in capabilities


def test_raw_pdu_capabilities_follow_wrapped_roundtrip_and_response_dictionary():
    import ast

    from app.services.test_activity_contract import _raw_pdu_ast_capabilities

    tree = ast.parse(
        "def recv_exact(sock, n):\n"
        "    return sock.recv(n)\n"
        "def recv_pdu(sock):\n"
        "    return recv_exact(sock, 48), b''\n"
        "def send_login(sock, request):\n"
        "    sock.sendall(request)\n"
        "    return recv_pdu(sock)\n"
        "def describe_rsp(bhs):\n"
        "    return {'tsih': int.from_bytes(bhs[14:16], 'big'), "
        "'status_class': bhs[36], 'status_detail': bhs[37]}\n"
        "def assert_status(rsp, expected_class, expected_detail):\n"
        "    assert rsp['status_class'] == expected_class\n"
        "    assert rsp['status_detail'] == expected_detail\n"
        "def connect(args):\n"
        "    return socket.create_connection((args.host, args.port))\n"
        "def run_steps(sock, steps):\n"
        "    for request in steps:\n"
        "        send_login(sock, request)\n"
        "def run_mcs(args, request):\n"
        "    with connect(args) as first:\n"
        "        rsp1 = describe_rsp(send_login(first, request)[0])\n"
        "        first_tsih = rsp1['tsih']\n"
        "        with connect(args) as second:\n"
        "            rsp2 = describe_rsp(send_login(second, request)[0])\n"
        "            assert_status(rsp2, 2, 6)\n"
    )

    capabilities = _raw_pdu_ast_capabilities([tree])

    assert {
        "response_tsih_capture",
        "login_response_status_oracle",
        "dual_socket_lifecycle",
        "multi_pdu_login",
    }.issubset(capabilities)


def test_combined_consistency_accepts_mcs_case_backed_by_capable_embedded_harness():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = r'''
## 黑盒测试用例
### TC17 MCS MaxConnections 容量边界
- 前置条件：首连接保持登录成功并记录返回 TSIH。
- 外部步骤：执行 `python3 /tmp/raw.py --mcs --mcs-second-cid 1 --expect-status-class 2 --expect-status-detail 6`。
- 预期结果：Status-Class=2，Status-Detail=6。

```python
import socket
def recv_exact(sock, n): return sock.recv(n)
def recv_pdu(sock): return recv_exact(sock, 48), b''
def send_login(sock, request):
    sock.sendall(request)
    return recv_pdu(sock)
def describe_rsp(bhs):
    return {'tsih': int.from_bytes(bhs[14:16], 'big'), 'status_class': bhs[36], 'status_detail': bhs[37]}
def assert_status(rsp, expected_class, expected_detail):
    assert rsp['status_class'] == expected_class
    assert rsp['status_detail'] == expected_detail
def connect(args): return socket.create_connection((args.host, args.port))
def run_mcs(args, request):
    with connect(args) as first:
        rsp1 = describe_rsp(send_login(first, request)[0])
        first_tsih = rsp1['tsih']
        second_request = bytearray(request)
        second_request[14:16] = first_tsih.to_bytes(2, 'big')
        second_request[20:22] = args.mcs_second_cid.to_bytes(2, 'big')
        with connect(args) as second:
            rsp2 = describe_rsp(send_login(second, second_request)[0])
            assert_status(rsp2, 2, 6)
def main():
    run_mcs(args, request)
```
'''

    issues = _audit_combined_report_consistency(content)

    assert not any(issue["code"] == "missing_mcs_capable_client" for issue in issues), issues


def test_raw_pdu_audit_rejects_capabilities_split_across_unreachable_dead_code():
    from app.services.test_activity_contract import _audit_raw_pdu_scenario_capabilities

    content = r'''
### TC17 MCS MaxConnections 容量边界
- 外部步骤：执行 `python3 raw.py --mcs`，使用相同 TSIH 和不同 CID。

```python
import socket
def dead_capture(bhs):
    return {'tsih': int.from_bytes(bhs[14:16], 'big')}
def dead_build(request, first_tsih, second_cid):
    request[14:16] = first_tsih.to_bytes(2, 'big')
    request[20:22] = second_cid.to_bytes(2, 'big')
def dead_status(rsp):
    assert rsp['status_class'] == 2
    assert rsp['status_detail'] == 6
def dead_mcs(args):
    with socket.create_connection((args.host, args.port)) as first:
        with socket.create_connection((args.host, args.port)) as second:
            pass
def main():
    print('does not execute the MCS helpers')
```
'''

    issues = _audit_raw_pdu_scenario_capabilities(content)

    assert any(
        issue["code"] == "raw_pdu_harness_missing_scenario_capability"
        for issue in issues
    ), issues


def test_raw_pdu_audit_does_not_treat_lambda_body_as_module_entrypoint():
    from app.services.test_activity_contract import _audit_raw_pdu_scenario_capabilities

    content = r'''
### TC17 MCS MaxConnections 容量边界
- 外部步骤：执行 `python3 raw.py --mcs`，使用相同 TSIH 和不同 CID。

```python
import socket
def dead_mcs(args, request, first_tsih, second_cid, rsp):
    request[14:16] = first_tsih.to_bytes(2, 'big')
    request[20:22] = second_cid.to_bytes(2, 'big')
    captured = {'tsih': int.from_bytes(rsp[14:16], 'big')}
    assert rsp['status_class'] == 2
    assert rsp['status_detail'] == 6
    with socket.create_connection((args.host, args.port)) as first:
        with socket.create_connection((args.host, args.port)) as second:
            return captured
unused = lambda: dead_mcs(args, request, first_tsih, second_cid, rsp)
```
'''

    issues = _audit_raw_pdu_scenario_capabilities(content)

    assert any(
        issue["code"] == "raw_pdu_harness_missing_scenario_capability"
        for issue in issues
    ), issues


def test_raw_pdu_audit_does_not_follow_lambda_inside_main_call_graph():
    from app.services.test_activity_contract import _audit_raw_pdu_scenario_capabilities

    content = r'''
### TC17 MCS MaxConnections 容量边界
- 外部步骤：执行 `python3 raw.py --mcs`，使用相同 TSIH 和不同 CID。

```python
import socket
def dead_mcs(args, request, first_tsih, second_cid, rsp):
    request[14:16] = first_tsih.to_bytes(2, 'big')
    request[20:22] = second_cid.to_bytes(2, 'big')
    captured = {'tsih': int.from_bytes(rsp[14:16], 'big')}
    assert rsp['status_class'] == 2
    assert rsp['status_detail'] == 6
    with socket.create_connection((args.host, args.port)) as first:
        with socket.create_connection((args.host, args.port)) as second:
            return captured
def main():
    unused = lambda: dead_mcs(args, request, first_tsih, second_cid, rsp)
main()
```
'''

    issues = _audit_raw_pdu_scenario_capabilities(content)

    assert any(
        issue["code"] == "raw_pdu_harness_missing_scenario_capability"
        for issue in issues
    ), issues


def test_raw_pdu_audit_follows_called_nested_function_inside_main():
    from app.services.test_activity_contract import _audit_raw_pdu_scenario_capabilities

    content = r'''
### TC17 MCS MaxConnections 容量边界
- 外部步骤：执行 `python3 raw.py --mcs`，使用相同 TSIH 和不同 CID。

```python
import socket
def main():
    def run_mcs(args, request, rsp):
        tsih = int.from_bytes(rsp[14:16], 'big')
        request[14:16] = tsih.to_bytes(2, 'big')
        request[20:22] = args.mcs_second_cid.to_bytes(2, 'big')
        assert rsp['status_class'] == 2
        assert rsp['status_detail'] == 6
        with socket.create_connection((args.host, args.port)) as first:
            first.sendall(request)
            first.recv(48)
            with socket.create_connection((args.host, args.port)) as second:
                second.sendall(request)
                second.recv(48)
    run_mcs(args, request, rsp)
main()
```
'''

    issues = _audit_raw_pdu_scenario_capabilities(content)

    assert not any(
        issue["code"] == "raw_pdu_harness_missing_scenario_capability"
        for issue in issues
    ), issues


def test_raw_pdu_audit_does_not_resolve_shadowed_local_call_to_dead_global():
    from app.services.test_activity_contract import _audit_raw_pdu_scenario_capabilities

    content = r'''
### TC17 MCS MaxConnections 容量边界
- 外部步骤：执行 `python3 raw.py --mcs`，使用相同 TSIH 和不同 CID。

```python
import socket
def run_mcs(args, request, rsp):
    tsih = int.from_bytes(rsp[14:16], 'big')
    request[14:16] = tsih.to_bytes(2, 'big')
    request[20:22] = args.mcs_second_cid.to_bytes(2, 'big')
    assert rsp['status_class'] == 2
    assert rsp['status_detail'] == 6
    with socket.create_connection((args.host, args.port)) as first:
        first.sendall(request)
        first.recv(48)
        with socket.create_connection((args.host, args.port)) as second:
            second.sendall(request)
            second.recv(48)
def main():
    def run_mcs(args, request, rsp):
        print('local implementation has no MCS behavior')
    run_mcs(args, request, rsp)
main()
```
'''

    issues = _audit_raw_pdu_scenario_capabilities(content)

    assert any(
        issue["code"] == "raw_pdu_harness_missing_scenario_capability"
        for issue in issues
    ), issues


def test_raw_pdu_audit_respects_shadowing_inside_reachable_nested_wrapper():
    from app.services.test_activity_contract import _audit_raw_pdu_scenario_capabilities

    content = r'''
### TC17 MCS MaxConnections 容量边界
- 外部步骤：执行 `python3 raw.py --mcs`，使用相同 TSIH 和不同 CID。

```python
import socket
def main():
    def run_mcs(args, request, rsp):
        tsih = int.from_bytes(rsp[14:16], 'big')
        request[14:16] = tsih.to_bytes(2, 'big')
        request[20:22] = args.mcs_second_cid.to_bytes(2, 'big')
        assert rsp['status_class'] == 2
        assert rsp['status_detail'] == 6
        with socket.create_connection((args.host, args.port)) as first:
            first.sendall(request)
            first.recv(48)
            with socket.create_connection((args.host, args.port)) as second:
                second.sendall(request)
                second.recv(48)
    def wrapper():
        def run_mcs(args, request, rsp):
            print('shadowing local implementation has no MCS behavior')
        run_mcs(args, request, rsp)
    wrapper()
main()
```
'''

    issues = _audit_raw_pdu_scenario_capabilities(content)

    assert any(
        issue["code"] == "raw_pdu_harness_missing_scenario_capability"
        for issue in issues
    ), issues


def test_raw_pdu_audit_respects_rebinding_of_nested_function_before_call():
    from app.services.test_activity_contract import _audit_raw_pdu_scenario_capabilities

    content = r'''
### TC17 MCS MaxConnections 容量边界
- 外部步骤：执行 `python3 raw.py --mcs`，使用相同 TSIH 和不同 CID。

```python
import socket
def main():
    def run_mcs(args, request, rsp):
        tsih = int.from_bytes(rsp[14:16], 'big')
        request[14:16] = tsih.to_bytes(2, 'big')
        request[20:22] = args.mcs_second_cid.to_bytes(2, 'big')
        assert rsp['status_class'] == 2
        assert rsp['status_detail'] == 6
        with socket.create_connection((args.host, args.port)) as first:
            first.sendall(request)
            first.recv(48)
            with socket.create_connection((args.host, args.port)) as second:
                second.sendall(request)
                second.recv(48)
    run_mcs = lambda args, request, rsp: print('replacement has no MCS behavior')
    run_mcs(args, request, rsp)
main()
```
'''

    issues = _audit_raw_pdu_scenario_capabilities(content)

    assert any(
        issue["code"] == "raw_pdu_harness_missing_scenario_capability"
        for issue in issues
    ), issues


def test_raw_pdu_audit_keeps_nested_function_called_before_later_rebinding():
    from app.services.test_activity_contract import _audit_raw_pdu_scenario_capabilities

    content = r'''
### TC17 MCS MaxConnections 容量边界
- 外部步骤：执行 `python3 raw.py --mcs`，使用相同 TSIH 和不同 CID。

```python
import socket
def main():
    def run_mcs(args, request, rsp):
        tsih = int.from_bytes(rsp[14:16], 'big')
        request[14:16] = tsih.to_bytes(2, 'big')
        request[20:22] = args.mcs_second_cid.to_bytes(2, 'big')
        assert rsp['status_class'] == 2
        assert rsp['status_detail'] == 6
        with socket.create_connection((args.host, args.port)) as first:
            first.sendall(request)
            first.recv(48)
            with socket.create_connection((args.host, args.port)) as second:
                second.sendall(request)
                second.recv(48)
    run_mcs(args, request, rsp)
    run_mcs = lambda args, request, rsp: print('later replacement')
main()
```
'''

    issues = _audit_raw_pdu_scenario_capabilities(content)

    assert not any(
        issue["code"] == "raw_pdu_harness_missing_scenario_capability"
        for issue in issues
    ), issues


def test_raw_pdu_audit_respects_import_rebinding_of_nested_function():
    from app.services.test_activity_contract import _audit_raw_pdu_scenario_capabilities

    content = r'''
### TC17 MCS MaxConnections 容量边界
- 外部步骤：执行 `python3 raw.py --mcs`，使用相同 TSIH 和不同 CID。

```python
import socket
def main():
    def run_mcs(args, request, rsp):
        tsih = int.from_bytes(rsp[14:16], 'big')
        request[14:16] = tsih.to_bytes(2, 'big')
        request[20:22] = args.mcs_second_cid.to_bytes(2, 'big')
        assert rsp['status_class'] == 2
        assert rsp['status_detail'] == 6
        with socket.create_connection((args.host, args.port)) as first:
            first.sendall(request)
            first.recv(48)
            with socket.create_connection((args.host, args.port)) as second:
                second.sendall(request)
                second.recv(48)
    from builtins import print as run_mcs
    run_mcs(args, request, rsp)
main()
```
'''

    issues = _audit_raw_pdu_scenario_capabilities(content)

    assert any(
        issue["code"] == "raw_pdu_harness_missing_scenario_capability"
        for issue in issues
    ), issues


def test_raw_pdu_audit_respects_match_capture_rebinding_of_nested_function():
    from app.services.test_activity_contract import _audit_raw_pdu_scenario_capabilities

    content = r'''
### TC17 MCS MaxConnections 容量边界
- 外部步骤：执行 `python3 raw.py --mcs`，使用相同 TSIH 和不同 CID。

```python
import socket
def main():
    def run_mcs(args, request, rsp):
        tsih = int.from_bytes(rsp[14:16], 'big')
        request[14:16] = tsih.to_bytes(2, 'big')
        request[20:22] = args.mcs_second_cid.to_bytes(2, 'big')
        assert rsp['status_class'] == 2
        assert rsp['status_detail'] == 6
        with socket.create_connection((args.host, args.port)) as first:
            first.sendall(request)
            first.recv(48)
            with socket.create_connection((args.host, args.port)) as second:
                second.sendall(request)
                second.recv(48)
    match replacement:
        case run_mcs:
            run_mcs(args, request, rsp)
main()
```
'''

    issues = _audit_raw_pdu_scenario_capabilities(content)

    assert any(
        issue["code"] == "raw_pdu_harness_missing_scenario_capability"
        for issue in issues
    ), issues


def test_raw_pdu_audit_respects_match_capture_in_nested_wrapper():
    from app.services.test_activity_contract import _audit_raw_pdu_scenario_capabilities

    content = r'''
### TC17 MCS MaxConnections 容量边界
- 外部步骤：执行 `python3 raw.py --mcs`，使用相同 TSIH 和不同 CID。

```python
import socket
def main():
    def run_mcs(args, request, rsp):
        tsih = int.from_bytes(rsp[14:16], 'big')
        request[14:16] = tsih.to_bytes(2, 'big')
        request[20:22] = args.mcs_second_cid.to_bytes(2, 'big')
        assert rsp['status_class'] == 2
        assert rsp['status_detail'] == 6
        with socket.create_connection((args.host, args.port)) as first:
            first.sendall(request)
            first.recv(48)
            with socket.create_connection((args.host, args.port)) as second:
                second.sendall(request)
                second.recv(48)
    def wrapper():
        match replacement:
            case run_mcs:
                run_mcs(args, request, rsp)
    wrapper()
main()
```
'''

    issues = _audit_raw_pdu_scenario_capabilities(content)

    assert any(
        issue["code"] == "raw_pdu_harness_missing_scenario_capability"
        for issue in issues
    ), issues


def test_raw_pdu_audit_does_not_treat_comprehension_target_as_wrapper_binding():
    from app.services.test_activity_contract import _audit_raw_pdu_scenario_capabilities

    content = r'''
### TC17 MCS MaxConnections 容量边界
- 外部步骤：执行 `python3 raw.py --mcs`，使用相同 TSIH 和不同 CID。

```python
import socket
def main():
    def run_mcs(args, request, rsp):
        tsih = int.from_bytes(rsp[14:16], 'big')
        request[14:16] = tsih.to_bytes(2, 'big')
        request[20:22] = args.mcs_second_cid.to_bytes(2, 'big')
        assert rsp['status_class'] == 2
        assert rsp['status_detail'] == 6
        with socket.create_connection((args.host, args.port)) as first:
            first.sendall(request)
            first.recv(48)
            with socket.create_connection((args.host, args.port)) as second:
                second.sendall(request)
                second.recv(48)
    def wrapper():
        names = [run_mcs for run_mcs in ('local-only',)]
        run_mcs(args, request, rsp)
        return names
    wrapper()
main()
```
'''

    issues = _audit_raw_pdu_scenario_capabilities(content)

    assert not any(
        issue["code"] == "raw_pdu_harness_missing_scenario_capability"
        for issue in issues
    ), issues


def test_module_analysis_quality_audit_rejects_combined_heading_and_path_traversal(
    tmp_path,
):
    from app.services.test_activity_contract import (
        audit_test_activity_artifacts,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    outside = tmp_path / "outside"
    (outside / "source.c").parent.mkdir(parents=True, exist_ok=True)
    (outside / "source.c").write_text("int outside;\n", encoding="utf-8")
    (outside / "case.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    artifact_dir = tmp_path / "run"
    artifact_dir.mkdir()
    (artifact_dir / "module_analysis.md").write_text(
        "## 分析范围 模块边界 关键入口与调用链 主流程 异常与恢复路径 "
        "源码与测试证据 测试关注点 证据缺口\n"
        "伪造的合并章节。lib/../../outside/source.c test/../../outside/case.sh\n",
        encoding="utf-8",
    )
    contract = build_test_activity_contract(
        target="iSCSI login",
        repo_path=str(repo),
        workflow_outputs=[
            {"id": "report", "artifact": "module_analysis.md", "type": "markdown"}
        ],
    )

    audit = audit_test_activity_artifacts(
        artifact_dir=artifact_dir,
        contract=contract,
        repo_path=str(repo),
    )

    codes = [issue["code"] for issue in audit["issues"]]
    assert audit["status"] == "needs_rework"
    assert "missing_markdown_sections" in codes
    assert codes.count("evidence_path_not_found") == 2


@pytest.mark.parametrize(
    "artifact",
    [
        "project_structure.md",
        "source_reading_plan.md",
        "module_map.md",
        "business_flow.md",
        "tester_code_understanding.md",
        "black_box_cases.md",
        "test_strategy.md",
        "test_design.md",
        "coverage_gap_report.md",
        "risk_review.md",
        "execution_checklist.md",
    ],
)
def test_all_markdown_test_activity_outputs_reject_shallow_content(tmp_path, artifact):
    from app.services.test_activity_contract import (
        audit_test_activity_artifacts,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    artifact_dir = tmp_path / "run"
    artifact_dir.mkdir()
    (artifact_dir / artifact).write_text("done\n", encoding="utf-8")
    contract = build_test_activity_contract(
        target="iSCSI login test activity",
        repo_path=str(repo),
        workflow_outputs=[{"id": "report", "artifact": artifact, "type": "markdown"}],
    )

    audit = audit_test_activity_artifacts(
        artifact_dir=artifact_dir,
        contract=contract,
        repo_path=str(repo),
    )

    codes = {issue["code"] for issue in audit["issues"]}
    assert audit["status"] == "needs_rework"
    assert "missing_markdown_sections" in codes
    assert "missing_source_evidence" in codes
    assert "missing_test_evidence" in codes


@pytest.mark.parametrize("artifact", ["sfmea.json", "black_box_cases.json"])
def test_structured_test_activity_outputs_reject_empty_lists(tmp_path, artifact):
    from app.services.test_activity_contract import (
        audit_test_activity_artifacts,
        build_test_activity_contract,
    )

    artifact_dir = tmp_path / "run"
    artifact_dir.mkdir()
    (artifact_dir / artifact).write_text("[]\n", encoding="utf-8")
    contract = build_test_activity_contract(
        target="iSCSI login",
        repo_path=str(tmp_path),
        workflow_outputs=[{"id": "result", "artifact": artifact, "type": "json"}],
    )

    audit = audit_test_activity_artifacts(
        artifact_dir=artifact_dir,
        contract=contract,
        repo_path=str(tmp_path),
    )

    assert audit["status"] == "needs_rework"
    assert any(issue["code"] == "empty_json_items" for issue in audit["issues"])


def test_sfmea_audit_rejects_invalid_scores_and_rpn(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_artifacts,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text("int login;\n", encoding="utf-8")
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    artifact_dir = tmp_path / "run"
    artifact_dir.mkdir()
    (artifact_dir / "sfmea.json").write_text(
        json.dumps(
            [
                {
                    "failure_mode": "login bypass",
                    "cause": "bad auth state",
                    "effect": "unauthorized session",
                    "detection": "login response and target log",
                    "severity": 11,
                    "occurrence": 2,
                    "detection_score": 3,
                    "rpn": 12,
                    "score_explanation": "security impact",
                    "mitigation": "add negative CHAP test",
                    "source_evidence": "lib/iscsi/iscsi.c",
                    "test_mapping": "test/iscsi_tgt",
                }
            ]
        ),
        encoding="utf-8",
    )
    contract = build_test_activity_contract(
        target="iSCSI login SFMEA",
        repo_path=str(repo),
        workflow_outputs=[{"id": "sfmea", "artifact": "sfmea.json", "type": "json"}],
    )

    audit = audit_test_activity_artifacts(
        artifact_dir=artifact_dir,
        contract=contract,
        repo_path=str(repo),
    )

    codes = {issue["code"] for issue in audit["issues"]}
    assert "sfmea_score_out_of_range" in codes
    assert "sfmea_rpn_mismatch" in codes


def test_sfmea_audit_requires_test_or_monitor_verification_in_mitigation(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_artifacts,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text("int login;\n", encoding="utf-8")
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    base_row = {
        "failure_mode": "login state is not released",
        "cause": "error path skips cleanup",
        "effect": "later login attempts fail",
        "detection": "connection state and target log",
        "severity": 7,
        "occurrence": 3,
        "detection_score": 2,
        "rpn": 42,
        "score_explanation": "S=7 unavailable; O=3 bounded; D=2 visible",
        "source_evidence": "lib/iscsi/iscsi.c",
        "test_mapping": "test/iscsi_tgt",
    }
    artifact_dir = tmp_path / "run"
    artifact_dir.mkdir()
    (artifact_dir / "sfmea.json").write_text(
        json.dumps(
            [
                {**base_row, "mitigation": "在错误路径释放连接并重置状态位"},
                {
                    **base_row,
                    "failure_mode": "login timeout is not bounded",
                    "mitigation": "增加超时清理，并用黑盒重连用例检查连接状态和告警日志",
                },
                {
                    **base_row,
                    "failure_mode": "login error is not observable",
                    "mitigation": "增加日志和监控告警",
                },
            ]
        ),
        encoding="utf-8",
    )
    contract = build_test_activity_contract(
        target="iSCSI login SFMEA",
        repo_path=str(repo),
        workflow_outputs=[{"id": "sfmea", "artifact": "sfmea.json", "type": "json"}],
    )

    audit = audit_test_activity_artifacts(
        artifact_dir=artifact_dir,
        contract=contract,
        repo_path=str(repo),
    )

    mitigation_issues = [
        issue for issue in audit["issues"] if issue["code"] == "non_actionable_mitigation"
    ]
    assert [issue["index"] for issue in mitigation_issues] == [1, 3]
    assert "验证动作" in mitigation_issues[0]["message"]
    assert "具体整改" in mitigation_issues[1]["message"]


@pytest.mark.parametrize(
    "mitigation",
    [
        "close the stale connection and add a reconnect test",
        "add input validation and a regression test",
        "增加参数校验，并添加边界测试",
        "Add timeout cleanup and regression tests",
        "Add timeout cleanup plus regression tests and monitor metrics",
        "新增超时清理及回归测试并监控日志",
        "Prevent session creation for invalid CHAP credentials; add negative tests",
        "Block invalid transitions and monitor error metrics",
        "Monitor logs and reset stale state",
        "Record metrics, then block invalid transitions",
        "监控日志并清理残留状态",
        "Add regression tests and reset stale state",
        "Run retry tests then block invalid transitions",
        "新增回归测试并清理残留状态",
        "执行重试测试然后限制重试次数",
        "Add regression tests, reset stale state",
        "Run retry tests, block invalid transitions",
        "新增回归测试，清理残留状态",
        "执行重试测试，限制重试次数",
        "在登录状态机中增加状态断言并返回错误；新增集成测试验证拒绝路径",
        "在参数解析路径增加长度上限保护；新增模糊测试验证超长参数处理",
        "Reset stale state and assert reconnect succeeds",
        "Limit retries and check the connection state",
        "限制重试次数并检查连接状态",
        "防止创建未授权会话并添加负向测试",
        "阻止非法状态迁移并监控日志",
        "启用 HeaderDigest/DataDigest，并添加 digest 错误计数监控",
        "确保 target 配置正确；添加 target 存在性检查；返回明确错误",
        "严格校验 CSG；返回明确错误；添加协议状态机监控",
        "free(host_key) 后立即置 host_key = NULL；添加单元测试验证后续访问不崩溃",
        "调用 libnvme_strerror 后检查返回值，若为 NULL 则使用默认字符串；构造异常返回并验证日志",
        "Track skipped invalid records and report a partial-success status; add a black-box regression test",
        "Capture disconnect failures and surface a warning; add cleanup assertions to BB-011",
    ],
)
def test_sfmea_mitigation_accepts_remediation_plus_verification(mitigation):
    from app.services.test_activity_contract import sfmea_mitigation_is_actionable

    assert sfmea_mitigation_is_actionable(mitigation) is True


@pytest.mark.parametrize(
    "mitigation",
    [
        "run retry test and monitor logs",
        "add reset/recovery black-box tests and inspect metrics",
        "新增重试测试并监控日志",
        "Add tests for normal path, timeout, reconnect/reset, concurrency, recovery, and monitor logs",
        "test cleanup and monitor logs",
        "测试重试并监控日志",
        "执行重试场景并监控日志",
        "monitor retry metrics and logs",
        "alert on reset failures and monitor logs",
        "监控重试指标和日志",
        "记录清理失败日志并配置告警",
        "Inspect retry metrics and logs",
        "Check reset metrics and logs",
        "Measure cleanup metrics and alert on failures",
        "Assert retry succeeds",
        "Validate reset behavior",
        "断言重试成功",
        "校验重置状态",
        "添加重试测试并监控日志",
        "确保测试通过并监控日志",
    ],
)
def test_sfmea_mitigation_rejects_test_scenario_without_product_remediation(mitigation):
    from app.services.test_activity_contract import sfmea_mitigation_is_actionable

    assert sfmea_mitigation_is_actionable(mitigation) is False


def test_local_source_flow_sfmea_generator_emits_remediation_and_verification():
    from app.services.test_activity_contract import sfmea_mitigation_is_actionable
    from app.services.workbench_workflow_runner import _source_flow_sfmea_item

    item = _source_flow_sfmea_item(
        task_run=None,
        file_path="lib/nvmf/ctrlr.c",
        evidence_card={"symbols": ["spdk_nvmf_ctrlr_connect"], "line_count": 20},
        index=1,
    )

    assert "Enforce bounded state transitions" in item["mitigation"]
    assert sfmea_mitigation_is_actionable(item["mitigation"]) is True


def test_black_box_audit_rejects_duplicate_cases(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_artifacts,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text("int login;\n", encoding="utf-8")
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    row = {
        "case_id": "TC-01",
        "scenario_name": "invalid CHAP login",
        "preconditions": "target requires CHAP",
        "steps": ["connect with invalid credentials"],
        "expected_result": "login is rejected",
        "observability": "login response and target log",
        "failure_diagnostics": "inspect target authentication configuration",
        "mapped_test_dir": "test/iscsi_tgt",
        "source_or_test_evidence": "lib/iscsi/iscsi.c",
    }
    artifact_dir = tmp_path / "run"
    artifact_dir.mkdir()
    (artifact_dir / "black_box_cases.json").write_text(
        json.dumps([row, row]),
        encoding="utf-8",
    )
    contract = build_test_activity_contract(
        target="iSCSI login black box cases",
        repo_path=str(repo),
        workflow_outputs=[
            {"id": "cases", "artifact": "black_box_cases.json", "type": "json"}
        ],
    )

    audit = audit_test_activity_artifacts(
        artifact_dir=artifact_dir,
        contract=contract,
        repo_path=str(repo),
    )

    assert any(issue["code"] == "duplicate_black_box_case" for issue in audit["issues"])


def test_black_box_audit_rejects_missing_required_test_dimensions(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_artifacts,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text("int login;\n", encoding="utf-8")
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    artifact_dir = tmp_path / "run"
    artifact_dir.mkdir()
    (artifact_dir / "black_box_cases.json").write_text(
        json.dumps(
            [
                {
                    "case_id": "TC-01",
                    "test_dimension": "normal_path",
                    "scenario_name": "valid CHAP login",
                    "preconditions": "target requires CHAP",
                    "steps": ["connect with valid credentials"],
                    "expected_result": "login succeeds",
                    "observability": "login response and target log",
                    "failure_diagnostics": "inspect target authentication configuration",
                    "mapped_test_dir": "test/iscsi_tgt",
                    "source_or_test_evidence": "lib/iscsi/iscsi.c",
                }
            ]
        ),
        encoding="utf-8",
    )
    contract = build_test_activity_contract(
        target="iSCSI login black box cases",
        repo_path=str(repo),
        workflow_outputs=[
            {"id": "cases", "artifact": "black_box_cases.json", "type": "json"}
        ],
    )

    audit = audit_test_activity_artifacts(
        artifact_dir=artifact_dir,
        contract=contract,
        repo_path=str(repo),
    )

    issue = next(
        item for item in audit["issues"] if item["code"] == "missing_black_box_dimensions"
    )
    assert set(issue["dimensions"]) == {
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
    }


def test_workbench_runner_marks_low_quality_test_activity_outputs_needs_rework(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
    from app.services.workflow_dsl import WorkflowStore

    script = tmp_path / "shallow_agent.py"
    script.write_text(
        "import json, os, pathlib\n"
        "root = pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'])\n"
        "(root / 'sfmea.json').write_text(json.dumps([{'failure_mode':'login failed','cause':'bad auth','effect':'cannot connect','severity':8}]), encoding='utf-8')\n"
        "(root / 'black_box_cases.json').write_text(json.dumps([{'case_id':'bb-1','steps':['call spdk_iscsi_login() directly'],'expected_result':'failed'}]), encoding='utf-8')\n"
        "print('done')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "shallow-agent", "command": f"python {script}"}
    ])
    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "shallow_test_design",
        "name": "Shallow test design",
        "version": 1,
        "inputs": [{"id": "analysis_object", "type": "free_text"}],
        "steps": [
            {
                "id": "design",
                "type": "agent_task",
                "provider": "shallow-agent",
                "required_artifacts": ["sfmea.json", "black_box_cases.json"],
            }
        ],
        "outputs": [
            {"id": "sfmea", "type": "json", "from": "design", "artifact": "sfmea.json"},
            {
                "id": "black_box_cases",
                "type": "json",
                "from": "design",
                "artifact": "black_box_cases.json",
            },
        ],
    })
    task_run = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="shallow_test_design",
        workspace_id="ws-low-quality",
        repo_path=str(repo),
        inputs={"analysis_object": "iSCSI login"},
    )

    result = WorkbenchWorkflowRunner(tmp_path / "task_runs").execute_task_run(
        task_run.task_run_id,
        timeout_sec=10,
    )

    assert result.status == "quality_blocked"
    assert result.test_activity_quality["status"] == "needs_rework"
    assert result.test_activity_quality["deliverable"] is False
    audit_path = Path(task_run.artifact_dir) / "test_activity_quality_audit.json"
    assert audit_path.exists()
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert any(issue["code"] == "black_box_boundary_violation" for issue in audit["issues"])


def test_combined_test_activity_response_requires_complete_sections_and_evidence(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text("int login_probe;\n", encoding="utf-8")
    contract = build_test_activity_contract(
        target="iSCSI login 完整流程、SFMEA、黑盒测试用例和测试设计",
        repo_path=str(repo),
        user_requirements="输出完整可下载测试设计文件",
    )

    audit = audit_test_activity_response(
        content="## 结论\n\n已完成。",
        contract=contract,
        repo_path=str(repo),
    )

    assert audit["status"] == "needs_rework"
    assert audit["deliverable"] is False
    codes = {issue["code"] for issue in audit["issues"]}
    assert "response_too_short" in codes
    assert "missing_combined_sfmea" in codes
    assert "missing_combined_black_box_dimensions" in codes
    assert "missing_combined_source_evidence" in codes
    assert "missing_specific_test_evidence" in codes


def test_combined_test_activity_response_accepts_complete_contract_shape(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text("int login_probe;\n", encoding="utf-8")
    (repo / "test" / "iscsi_tgt" / "login.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    contract = build_test_activity_contract(
        target="iSCSI login 完整流程、SFMEA、黑盒测试用例和测试设计",
        repo_path=str(repo),
        user_requirements="输出完整可下载测试设计文件",
    )
    dimensions = [
        "Normal Path",
        "Invalid Input",
        "Resource Pressure",
        "Timeout",
        "Reconnect",
        "Concurrency",
        "Recovery",
        "Performance",
    ]
    cases = "\n\n".join(
        f"### TC-{index:02d} {dimension}\n前置条件：target 已启动。\n步骤：从 initiator 发起登录。\n"
        "预期结果：返回明确状态。\n观测点：initiator 结果和 SPDK 日志。\n"
        "失败诊断线索：关联 session 日志。\n证据：`test/iscsi_tgt/login.sh`。"
        for index, dimension in enumerate(dimensions, start=1)
    )
    content = (
        "## 测试设计目标\n\n验证 iSCSI login 协商、认证、异常和恢复。\n\n"
        "## 输入与范围\n\n输入为 initiator 参数、CHAP 凭据与网络故障。\n\n"
        "## 代码证据\n\n- `lib/iscsi/iscsi.c`: 登录状态处理。\n"
        "- `test/iscsi_tgt/login.sh`: 现有登录测试入口。\n\n"
        "源码版本 Git revision: 97af299e3c76368219f0cddcc710fafd57edcc1c。\n\n"
        "## 流程步骤\n\n1. 建立 TCP 连接。\n2. 协商登录参数与 CHAP。\n3. 进入会话或返回失败并清理。\n\n"
        "专业场景覆盖：T+C 非法组合、非法 NSG、Unsupported Version、未知合法 key 返回 "
        "NotUnderstood、Target not found/removed、Authorization Failure、Redirect。Discovery Login "
        "进入 Full Feature 后再发送 SendTargets。首 payload 后 login_timer 注销且未重新注册。\n\n"
        "CHAP 负向矩阵独立覆盖：错误 CHAP_R、未知 CHAP 用户、CHAP 参数顺序错误、"
        "Mutual CHAP 缺失 challenge，以及 Target 要求 Mutual CHAP 但 Initiator 未提供。\n"
        "扩展矩阵覆盖不支持的 CHAP_A 算法、缺少 CHAP_R、CHAP_R hex 编码格式错误、"
        "Mutual CHAP 用户或 secret 缺失、Initiator 请求 Mutual CHAP 但 Target 未启用，"
        "以及 Mutual CHAP challenge 合法编码但语义错误。\n"
        "C-bit 分片覆盖 C=1 时 key/value 跨 PDU，最后用 C=0 收尾。\n"
        "抓包命令：tcpdump -i lo -w login.pcap tcp port 3260；"
        "解析断言：tshark -r login.pcap -Y iscsi -T fields -e iscsi.login_transit。\n\n"
            "执行入口：python3 raw_pdu_harness.py。\n"
            "```python\nimport socket, struct, hashlib\n"
            "bhs = bytearray(48)  # Login BHS opcode\n"
            "bhs[5:8] = (0).to_bytes(3, 'big')\n"
            "isid = b'123456'; cid = 1; itt = 2; cmdsn = 3\n"
            "chap_digest = hashlib.md5(b'challenge').digest()\n"
            "sock = socket.socket(); sock.sendall(bhs); response = sock.recv(48)\n"
            "dlen = int.from_bytes(response[5:8], 'big')\n```\n\n"
        "## SFMEA\n\n| failure_mode | cause | effect | detection | severity | occurrence | "
        "detection_score | RPN | score_explanation | mitigation | source_evidence | test_mapping |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| CHAP 绕过 | 认证分支错误 | 未授权访问 | 登录响应和日志 | 10 | 2 | 3 | 60 | "
        "安全影响高 | 增加拒绝用例 | `lib/iscsi/iscsi.c` | `test/iscsi_tgt/login.sh` |\n\n"
        "评分标尺：Severity 1-10 按影响，Occurrence 1-10 按频率，Detection 1-10 按难度；"
        "RPN >= 60 为高风险并优先执行。\n\n"
        "Occurrence 依据历史缺陷、协议登录流量分布和本轮测试统计，每次发布更新样本统计。\n\n"
        "## 黑盒测试用例\n\n"
        f"{cases}\n\n"
        "## 未确认项\n\n跨平台 initiator 差异为 ai_suggested_unverified。"
    )

    audit = audit_test_activity_response(
        content=content,
        contract=contract,
        repo_path=str(repo),
    )

    assert audit["status"] == "deliverable", audit["issues"]
    assert audit["score"] == 100


def test_combined_business_flow_accepts_named_end_to_end_flow_sections(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text("int login_probe;\n", encoding="utf-8")
    (repo / "test" / "iscsi_tgt" / "login.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    contract = build_test_activity_contract(
        target="iSCSI login 业务流程",
        repo_path=str(repo),
        workflow_outputs=[
            {"id": "flow", "artifact": "business_flow.md", "type": "markdown"}
        ],
    )
    content = (
        "## 端到端流程\n\n"
        "### 流程 A：无认证登录\n"
        "Initiator 发送 Login Request，Target 协商参数并进入 Full Feature Phase。\n\n"
        "### 流程 B：CHAP 登录\n"
        "Target 返回 challenge，Initiator 响应后完成认证与状态迁移。\n\n"
        "### 流程 C：授权失败与清理\n"
        "Target 返回 Authorization Failure，连接进入失败清理路径。\n\n"
        "## 异常与恢复\n\n断线后重连，核对 session 恢复和资源释放。\n\n"
        "## 证据\n\n`lib/iscsi/iscsi.c` 与 `test/iscsi_tgt/login.sh`。\n"
        + ("观测 Login 响应、连接状态、日志和会话恢复结果。" * 30)
    )

    audit = audit_test_activity_response(
        content=content,
        contract=contract,
        repo_path=str(repo),
    )

    assert not any(
        issue["code"] == "missing_combined_business_flow"
        for issue in audit["issues"]
    ), audit


def test_iscsi_professional_constraints_reject_known_protocol_contradictions(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 认证协商 SFMEA 和黑盒测试设计",
        repo_path=str(repo),
        user_requirements="输出完整可下载测试设计文件",
    )

    audit = audit_test_activity_response(
        content=(
            "## 结论\n\n"
            "`iscsi_op_login_response` 是认证和参数协商的核心函数。\n"
            "Authorization Failure 使用 Status-Class: 0x03。\n"
            "接收 Login Request 的入口是 `iscsi_op_login_rsp_handle_csg_bit`。\n"
            "Initiator 发送 Text Request 进行登录参数协商。\n"
            "连接清理由 `iscsi_param_free` 和 `spdk_startup` 完成。\n"
            "`iscsi_negotiate_chap_param` 执行 CHAP 认证。\n"
            "Parameter Error 使用 Status-Detail 0x05。\n"
            "参数协商失败使用 Status-Detail 0x02。"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    assert contract["professional_constraints"]
    conflicts = [
        issue for issue in audit["issues"]
        if issue["code"] == "professional_fact_conflict"
    ]
    assert len(conflicts) == 8
    assert all(issue["evidence"] for issue in conflicts)


def test_iscsi_professional_constraints_reject_run41_source_fact_errors(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="完整 iSCSI Login 源码分析、SFMEA 和黑盒测试设计",
        repo_path=str(repo),
        workflow_outputs=[
            {
                "id": "report",
                "type": "combined_test_report",
                "artifact": "report.md",
            }
        ],
    )

    audit = audit_test_activity_response(
        content=(
            "T+C 同时置位或 NSG=2 时返回 status-class 0x02、status-detail 0x0b。\n"
            "Unsupported Version 通过修改 Login BHS bytes 40-41 构造。\n"
            "Initiator 先通过 CHAP，再由 target 检查 ACL；失败日志为 auth failed。\n"
            "完整 key/value 跨 C=1 PDU 分片并以 C=0 收尾时，SPDK 无法重组并返回 MISSING_PARMS。\n"
            "同一 Login PDU 中重复 key 时使用最后一次出现的值并继续成功登录。\n"
            "CHAP_N、CHAP_I 和 CHAP_R 都必须使用 base64 编码；非法 CHAP_R 记录 base64 decode failed。\n"
            "CHAP_R 长度由 ISCSI_CHAP_MAX_SECRET_LEN 限制。\n"
            "抓取 Login Response：tshark -r login.pcap -Y 'iscsi.opcode==0x03' -T fields。\n"
            "iscsi_get_connections 的 login_phase 为 security_negotiation 或 operational_negotiation。\n"
            "test/app/fuzz/iscsi_fuzz/iscsi_fuzz.c 可覆盖随机 Login opcode。\n"
            "首个 Login PDU 处理后 timer 注销，因此 target 不会发送 Login Response。\n"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    constraint_ids = {
        issue["constraint_id"]
        for issue in audit["issues"]
        if issue["code"] == "professional_fact_conflict"
    }
    assert {
        "iscsi_invalid_login_request_detail",
        "iscsi_login_version_offsets",
        "iscsi_acl_precedes_chap_configuration",
        "iscsi_c_bit_parameter_reassembly",
        "iscsi_duplicate_key_rejected",
        "iscsi_chap_wire_encoding",
        "iscsi_chap_response_validation",
        "iscsi_login_response_opcode",
        "iscsi_rpc_login_phase_values",
        "iscsi_fuzzer_skips_login_opcode",
        "iscsi_first_payload_still_gets_response",
    }.issubset(constraint_ids), audit


def test_iscsi_professional_constraints_do_not_exempt_sfmea_fact_fields(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="完整 iSCSI Login 源码分析、SFMEA 和黑盒测试设计",
        repo_path=str(repo),
        workflow_outputs=[
            {"id": "report", "type": "combined_test_report", "artifact": "report.md"}
        ],
    )
    audit = audit_test_activity_response(
        content=(
            '{"failure_mode":"Login Response 过滤使用 '
            'iscsi.opcode==0x03 并读取 iscsi.login_status"}'
        ),
        contract=contract,
        repo_path=str(repo),
    )

    assert any(
        issue.get("constraint_id") == "iscsi_login_response_opcode"
        for issue in audit["issues"]
    ), audit


def test_iscsi_response_opcode_claim_accepts_request_capture_and_response_display_filter(
    tmp_path,
):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="完整 iSCSI Login 源码分析、SFMEA 和黑盒测试设计",
        repo_path=str(repo),
    )
    audit = audit_test_activity_response(
        content=(
            "SPDK returns Login Response with status Initiator Error. "
            "tcpdump -w login.pcap -i any 'port 3260 and iscsi.opcode==0x03'; "
            "tshark -r login.pcap -Y 'iscsi.opcode==0x23 and iscsi.login.statusclass' "
            "-T fields -e iscsi.login.statusclass."
        ),
        contract=contract,
        repo_path=str(repo),
    )

    assert not any(
        issue.get("constraint_id") == "iscsi_login_response_opcode"
        for issue in audit["issues"]
    ), audit


def test_iscsi_claim_validator_rejects_protocol_field_in_tcpdump_capture_filter(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="完整 iSCSI Login 黑盒测试设计",
        repo_path=str(repo),
    )
    audit = audit_test_activity_response(
        content="tcpdump -w login.pcap -i any 'port 3260 and iscsi.opcode==0x03'",
        contract=contract,
        repo_path=str(repo),
    )

    assert any(
        issue.get("code") == "invalid_capture_filter"
        and issue.get("claim_type") == "command_executability"
        for issue in audit["issues"]
    ), audit


def test_iscsi_claim_validator_keeps_tcpdump_and_tshark_inline_commands_separate(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="完整 iSCSI Login 黑盒测试设计",
        repo_path=str(repo),
    )
    audit = audit_test_activity_response(
        content=(
            "通过 `tcpdump -w login.pcap -i any port 3260` 捕获流量，再用 "
            "`tshark -r login.pcap -Y \"iscsi.opcode == 0x23\" "
            "-T fields -e iscsi.status_class` 解析响应。"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    assert not any(issue.get("code") == "invalid_capture_filter" for issue in audit["issues"]), audit


def test_iscsi_typed_behavior_claims_accept_post_login_sendtargets_and_negative_target_address(
    tmp_path,
):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="完整 iSCSI Discovery Login 流程分析",
        repo_path=str(repo),
    )
    audit = audit_test_activity_response(
        content=(
            "Discovery Login 成功进入 Full Feature Phase 后，initiator 才发送 Text Request "
            "请求 SendTargets。Discovery Login 的成功响应本身不会包含 TargetAddress；"
            "TargetAddress 只在后续 SendTargets Text Response 中返回。"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    assert not any(
        issue.get("constraint_id") in {
            "iscsi_login_negotiation_transport",
            "iscsi_discovery_target_address",
        }
        for issue in audit["issues"]
    ), audit


def test_iscsi_typed_behavior_claim_accepts_run46_post_login_sendtargets_wording(
    tmp_path,
):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="完整 iSCSI Discovery Login 流程分析",
        repo_path=str(repo),
    )
    audit = audit_test_activity_response(
        content=(
            "根据设计期望，initiator 在发现阶段应随后发送 Text Request（opcode 0x04）"
            "并携带 SendTargets=All 参数。这一行为属于登录完成后的标准协议交互，"
            "不属于 Login PDU 本身。"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    assert not any(
        issue.get("constraint_id") == "iscsi_login_negotiation_transport"
        for issue in audit["issues"]
    ), audit


def test_iscsi_professional_constraints_reject_real_run41_report_wording(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="完整 iSCSI Login 源码分析、SFMEA 和黑盒测试设计",
        repo_path=str(repo),
        workflow_outputs=[
            {"id": "report", "type": "combined_test_report", "artifact": "report.md"}
        ],
    )
    content = """
| SFMEA-001 | T+C both set in Login Request | SPDK returns Login Response with Initiator Error (0x02) and Invalid Login Request (0x0b) |
| SFMEA-002 | Invalid NSG in Login Request | SPDK returns status 0x02/0x0b |
### TC-07 Unsupported Version
- 操作步骤：Send Login PDU with version field (bytes 40-41) = 0x0001.
### TC-08 Authorization Failure
- 操作步骤：Complete CHAP authentication; Target will check initiator name against ACL after auth.
- 观测点：SPDK log: 'auth failed' in iscsi_op_login_check_target.
| SFMEA-005 | C=1 across PDU fragments ending with C=0 | SPDK fails to assemble complete parameter set and returns MISSING_PARMS |
### TC-11 Unknown CHAP user
- 操作步骤：Send CHAP_N with username 'nonexistent_user' (base64 encoded).
### TC-14 Invalid CHAP_R
- 预期结果：SPDK log 'base64 decode failed'.
### TC-21 CHAP_R length
- 失败诊断：check ISCSI_CHAP_MAX_SECRET_LEN enforcement for CHAP_R.
tshark -r /tmp/iscsi-login.pcap -Y iscsi.opcode==0x03 -T fields -e iscsi.login_status
test/app/fuzz/iscsi_fuzz/iscsi_fuzz.c may trigger random Login Request mutations.
### TC-04 首 payload 后 timer 注销
- 预期结果：timer 注销后无 Login Response 发出。
"""

    audit = audit_test_activity_response(
        content=content,
        contract=contract,
        repo_path=str(repo),
    )

    constraint_ids = {
        issue["constraint_id"]
        for issue in audit["issues"]
        if issue["code"] == "professional_fact_conflict"
    }
    assert {
        "iscsi_invalid_login_request_detail",
        "iscsi_login_version_offsets",
        "iscsi_acl_precedes_chap_configuration",
        "iscsi_c_bit_parameter_reassembly",
        "iscsi_chap_wire_encoding",
        "iscsi_chap_response_validation",
        "iscsi_login_response_opcode",
        "iscsi_fuzzer_skips_login_opcode",
        "iscsi_first_payload_still_gets_response",
    }.issubset(constraint_ids), audit


def test_iscsi_professional_constraints_accept_explicit_fact_corrections(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 认证协商 SFMEA 和黑盒测试设计",
        repo_path=str(repo),
        user_requirements="输出完整可下载测试设计文件",
    )

    audit = audit_test_activity_response(
        content=(
            "## 已核验事实\n\n"
            "`iscsi_op_login_response` 只发送响应，不是认证或参数协商核心。\n"
            "认证失败和授权失败都使用 Initiator Error class `0x02`，不是 Target Error `0x03`。\n"
            "认证失败和授权失败均属 Initiator Error `0x02`；Target Error 是 `0x03`。\n"
            "Login PDU 数据段完成参数协商，不需要 Text Request。\n"
            "连接清理由 `lib/iscsi/conn.c` 析构路径负责，`iscsi_param_free` 只释放参数链。\n"
            "`spdk_startup` 不负责 Login、认证或连接清理。\n"
            "`iscsi_negotiate_chap_param` 不执行 CHAP 认证，实际校验由 `iscsi_auth_params` 完成。\n"
            "| CHAP 策略设置 | `iscsi_negotiate_chap_param` | 1532-1543 | 根据 `disable_chap`/`require_chap` 设置 `AuthMethod`。不执行实际认证。 |\n"
            "Status-Detail 0x05 是 Unsupported Version，不是参数错误。\n"
            "Unsupported Version 的 status_detail = `0x05`，不能泛化为参数错误。\n"
            "Status-Detail `0x02` 表示 Authorization Failure，不是参数协商失败。"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    conflicts = [
        issue for issue in audit["issues"]
        if issue["code"] == "professional_fact_conflict"
    ]
    assert conflicts == []


def test_iscsi_status_class_constraint_does_not_confuse_status_detail_03(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 授权失败测试设计",
        repo_path=str(repo),
    )

    audit = audit_test_activity_response(
        content=(
            "Authorization Failure 使用 status_class=0x02, status_detail=0x02。\n"
            "Target 不存在导致授权失败：status_class=0x02, status_detail=0x03。"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    assert not any(
        issue.get("constraint_id") == "iscsi_login_status_class"
        for issue in audit["issues"]
    ), audit


def test_iscsi_professional_constraints_reject_unverified_defect_assertions(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login SFMEA",
        repo_path=str(repo),
        user_requirements="输出完整测试设计",
    )

    audit = audit_test_activity_response(
        content=(
            "## SFMEA\n\n"
            "`iscsi_parse_params` 未对超长 key 或 value 进行边界检查，导致缓冲区溢出。\n"
            "`iscsi_negotiate_params` 写入响应数据段时发生缓冲区溢出。\n"
            "登录失败路径中 `_iscsi_conn_destruct` 未被调用，造成连接泄漏。\n"
            "多个 Initiator 访问共享数据 `g_iscsi` 未加锁，必然产生资源竞争。"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    conflicts = [
        issue for issue in audit["issues"]
        if issue["code"] == "professional_fact_conflict"
    ]
    assert {issue["constraint_id"] for issue in conflicts} == {
        "iscsi_param_bounds_checked",
        "iscsi_negotiate_params_bounds_checked",
        "iscsi_unverified_cleanup_or_lock_defect",
    }


def test_iscsi_professional_constraints_accept_explicitly_unverified_lock_hypothesis(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login SFMEA",
        repo_path=str(repo),
    )

    audit = audit_test_activity_response(
        content=(
            "| failure_mode | mitigation |\n"
            "| Shared g_iscsi 配置无锁保护 | 待验证：需核对 RPC 修改路径的锁保护，"
            "当前不能断言存在竞争缺陷 |"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    assert not any(
        issue.get("constraint_id") == "iscsi_unverified_cleanup_or_lock_defect"
        for issue in audit["issues"]
    ), audit


def test_iscsi_professional_constraints_accept_disconnected_cleanup_evidence_gap(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login SFMEA",
        repo_path=str(repo),
    )

    audit = audit_test_activity_response(
        content=(
            "连接生命周期的清理由 `_iscsi_conn_destruct` 负责。\n"
            "证据缺口：`_iscsi_conn_destruct` 未出现在已验证调用分量中，"
            "因此不能证明登录失败到析构的完整调用路径。\n"
            "缺口：认证失败后的连接级清理（如 `_iscsi_conn_destruct`）路径"
            "未在当前验证范围内被直接调用。"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    assert not any(
        issue.get("constraint_id") == "iscsi_unverified_cleanup_or_lock_defect"
        for issue in audit["issues"]
    ), audit


def test_iscsi_cleanup_gate_accepts_current_scope_not_directly_called_wording(tmp_path):
    from app.services.test_activity_contract import (
        _audit_professional_constraints,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login SFMEA",
        repo_path=str(repo),
    )
    issues = _audit_professional_constraints(
        "缺口：认证失败后的连接级清理（如 `_iscsi_conn_destruct`）路径"
        "未在当前验证范围内被直接调用。",
        contract,
    )

    assert not any(
        issue.get("constraint_id") == "iscsi_unverified_cleanup_or_lock_defect"
        for issue in issues
    ), issues


def test_iscsi_professional_constraints_reject_state_machine_timeout_and_discovery_claims(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login CHAP 状态机、超时和 Discovery 黑盒测试",
        repo_path=str(repo),
    )

    audit = audit_test_activity_response(
        content=(
            "CHAP challenge/response 在 Operational Negotiation 阶段由 iscsi_auth_params 完成。\n"
            "CSG=1 是 SecurityNegotiation，CSG=0 是 OperationalNegotiation。\n"
            "Initiator 可以发送 CSG=3 的 Login Request 进入 Full Feature Phase。\n"
            "未知但格式正确的 key 会导致参数解析失败并断开连接。\n"
            "首个 Login PDU 后如果客户端停滞，30 秒登录定时器会触发清理。\n"
            "Discovery Login 成功响应必定返回 TargetAddress。\n"
            "黑盒观测点：调用 iscsi_get_active_conns 获取当前连接数。\n"
            "最终 Login Response 使用 CSG=3, NSG=3, T=1。"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    constraint_ids = {
        issue["constraint_id"]
        for issue in audit["issues"]
        if issue["code"] == "professional_fact_conflict"
    }
    assert constraint_ids == {
        "iscsi_chap_security_stage",
        "iscsi_csg_values",
        "iscsi_full_feature_request_rejected",
        "iscsi_unknown_key_not_understood",
        "iscsi_login_timer_after_first_pdu",
        "iscsi_discovery_target_address",
        "iscsi_internal_observer_boundary",
        "iscsi_login_response_stage_bits",
    }


def test_iscsi_professional_constraints_reject_chap_flags_reject_code_and_rpc_state(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login CHAP 与异常响应测试设计",
        repo_path=str(repo),
    )

    audit = audit_test_activity_response(
        content=(
            "CHAP 第一轮 Login Request 使用 T=0，Target Login Response 使用 T=1 并同时包含挑战和响应。\n"
            "CHAP 认证失败的 Login Response 仍设置 T=1、CSG=0、NSG=1。\n"
            "超长 Login PDU 返回 Reject Protocol Error (0x05)。\n"
            "scripts/rpc.py iscsi_get_connections 显示连接状态为 Full Feature Phase。\n"
            "Discovery TargetAddress 由 iscsi_op_login_set_params 决定。"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    constraint_ids = {
        issue["constraint_id"]
        for issue in audit["issues"]
        if issue["code"] == "professional_fact_conflict"
    }
    assert {
        "iscsi_chap_request_response_flags",
        "iscsi_login_error_flags_cleared",
        "iscsi_reject_protocol_error_reason",
        "iscsi_rpc_connection_state",
        "iscsi_discovery_target_info_symbol",
    }.issubset(constraint_ids), audit


def test_iscsi_constraints_reject_multiline_chap_timer_and_unknown_key_conflation(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整测试设计",
        repo_path=str(repo),
    )
    audit = audit_test_activity_response(
        content=(
            "Initiator 发送 Login Request:\n- CSG: 0\n- NSG: 1\n- T: 0\n"
            "Target 处理认证与参数。\nTarget 发送 Login Response:\n- CSG: 0\n- NSG: 1\n- T: 1\n"
            "第二个 Login Request:\n- CSG: 1\n- NSG: 3\n- T: 0\n"
            "最终 Login Response:\n- CSG: 1\n- NSG: 3\n- T: 1\n"
            "参数协商失败：Initiator 发送无效、未知或重复参数，Target 返回错误，连接无法建立。\n"
            "登录定时器超时：Initiator 发送首个 Login PDU 后停滞，login_timer 触发清理并断开连接。\n"
            "步骤：发送 T=0 请求后等待超过 30 秒，预期 Target 主动关闭连接。\n"
            "Discovery Login:\n- target=NULL，不追加 TargetAlias。\n"
            "Target 发送 Login Response:\n- TargetAddress=10.0.0.1:3260。"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    constraint_ids = {
        issue["constraint_id"]
        for issue in audit["issues"]
        if issue["code"] == "professional_fact_conflict"
    }
    assert {
        "iscsi_chap_request_response_flags",
        "iscsi_unknown_key_not_understood",
        "iscsi_login_timer_after_first_pdu",
        "iscsi_discovery_target_address",
    }.issubset(constraint_ids), audit


def test_iscsi_contract_uses_real_discovery_symbol_and_accepts_verified_flags(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login CHAP 与 Discovery 测试设计",
        repo_path=str(repo),
    )
    serialized_contract = json.dumps(contract, ensure_ascii=False)
    assert "iscsi_op_login_set_target_info" in serialized_contract
    discovery_constraints = [
        item
        for item in contract["professional_constraints"]
        if item["id"] in {"iscsi_discovery_target_address", "iscsi_discovery_target_info_symbol"}
    ]
    assert discovery_constraints
    assert all(
        item["evidence"] == ["lib/iscsi/iscsi.c::iscsi_op_login_set_target_info"]
        for item in discovery_constraints
    )

    audit = audit_test_activity_response(
        content=(
            "CHAP 第一轮 Login Request T=0，Login Response 继承 T=0；Target 只返回 CHAP_I/CHAP_C。\n"
            "Initiator 后续发送 CHAP_N/CHAP_R；最终迁移请求与成功响应均为 T=1、CSG=1、NSG=3。\n"
            "认证失败响应清除 T/CSG/NSG。Reject Protocol Error reason 是 0x04。\n"
            "iscsi_get_connections 的外部字段为 state=running 与 login_phase=full_feature_phase。\n"
            "Discovery session 由 iscsi_op_login_set_target_info 处理且 Login Response 不追加 TargetAddress。"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    blocked = {
        "iscsi_chap_request_response_flags",
        "iscsi_login_error_flags_cleared",
        "iscsi_reject_protocol_error_reason",
        "iscsi_rpc_connection_state",
        "iscsi_discovery_target_info_symbol",
    }
    assert not any(
        issue.get("constraint_id") in blocked for issue in audit["issues"]
    ), audit


def test_iscsi_constraints_accept_skip_security_and_internal_observer_warnings(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 状态与观测测试",
        repo_path=str(repo),
    )
    audit = audit_test_activity_response(
        content=(
            "CSG=1 时 CHAP 要求但未认证：Initiator 跳过 Security Negotiation 直接到 "
            "Operational，Target 会拒绝。\n"
            "首次 Login 当前 CSG=0、NSG=1、T=1，从 Security 迁移到 Operational Negotiation。\n"
            "iscsi_get_active_conns 是非公开内部函数，黑盒测试不应调用；"
            "应使用 RPC iscsi_get_connections、日志和 initiator 结果。\n"
            "Discovery Login 完成进入 Full Feature 后，Initiator 再发送 Text Request (SendTargets)。\n"
            "iscsi_param_free 位于 lib/iscsi/param.c，仅释放参数链表，不负责连接清理。"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    assert not any(
        issue.get("constraint_id") in {
            "iscsi_csg_values",
            "iscsi_internal_observer_boundary",
            "iscsi_login_negotiation_transport",
            "iscsi_connection_cleanup_role",
        }
        for issue in audit["issues"]
    ), audit


def test_iscsi_constraints_accept_negative_discovery_risk_and_internal_boundary(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login Discovery 灰盒测试设计",
        repo_path=str(repo),
    )
    audit = audit_test_activity_response(
        content=(
            "### SFMEA\n\n"
            "failure_mode: Discovery Login 返回 TargetAddress\n"
            "cause: Target 错误地返回了 TargetAddress。\n"
            "detection: 检查响应是否包含 TargetAddress。\n"
            "mitigation: 代码逻辑已保证 iscsi_op_login_set_target_info 仅在 target != NULL 时追加。\n\n"
            "| 内部观测函数 | iscsi_get_active_conns | 内部 C 函数，非公开 RPC 接口。"
            "黑盒测试不应直接调用，应使用 RPC iscsi_get_connections。 |\n"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    assert not any(
        issue.get("constraint_id") in {
            "iscsi_discovery_target_address",
            "iscsi_internal_observer_boundary",
        }
        for issue in audit["issues"]
    ), audit


def test_iscsi_constraints_accept_discovery_response_should_not_include_target_address(
    tmp_path,
):
    from app.services.test_activity_contract import (
        _audit_professional_constraints,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login Discovery 流程分析",
        repo_path=str(repo),
    )

    issues = _audit_professional_constraints(
        "成功的 Discovery Login Response 中不应包含 TargetAddress key。",
        contract,
    )

    assert not any(
        issue.get("constraint_id") == "iscsi_discovery_target_address"
        for issue in issues
    ), issues


def test_complete_iscsi_audit_accepts_named_mutual_user_or_secret_missing_scenario(
    tmp_path,
):
    from app.services.test_activity_contract import (
        _audit_combined_professional_completeness,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整流程 SFMEA 黑盒测试设计",
        repo_path=str(repo),
        workflow_outputs=[
            {"artifact": "report.md", "type": "combined_test_report"},
        ],
    )
    content = (
        '"failure_mode":"Mutual 用户或 secret 缺失",'
        '"cause":"Initiator 未提供 CHAP_N 或 CHAP_R",'
        '"detection":"检查 Login 数据段",'
        '"mitigation":"补充 raw-PDU 用例"'
    )

    issues = _audit_combined_professional_completeness(content, contract)
    missing = next(
        (
            issue
            for issue in issues
            if issue.get("code") == "missing_extended_chap_negative_scenarios"
        ),
        {},
    )

    assert "Mutual 用户或 secret 缺失" not in (missing.get("scenarios") or [])


def test_combined_business_flow_accepts_structured_stage_bullets(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整测试设计",
        repo_path=str(repo),
        workflow_outputs=[
            {"id": "flow", "artifact": "business_flow.md", "type": "markdown"}
        ],
    )
    content = (
        "## 端到端流程：CHAP 认证登录\n\n"
        "#### 外部触发\nInitiator 发送 Login Request。\n\n"
        "#### 流程步骤\n"
        "* **验证请求头**：校验数据段长度并分配响应。\n"
        "* **接收请求负载**：解析参数并进入登录状态机。\n"
        "* **完成 CHAP 认证**：验证 challenge/response。\n"
        "* **协商操作参数**：迁移到 Full Feature Phase。\n"
        "* **发送响应**：失败时清除阶段位并清理连接。\n\n"
        "#### 异常分支\n认证失败返回明确状态，连接进入清理路径。\n\n"
        "#### 恢复与观测\n更正凭据后重新登录，观察 RPC、日志和 initiator 结果。\n"
        + ("源码证据与测试映射。" * 80)
    )
    audit = audit_test_activity_response(
        content=content,
        contract=contract,
        repo_path=str(repo),
    )

    assert not any(
        issue["code"] == "missing_combined_business_flow"
        for issue in audit["issues"]
    ), audit


def test_iscsi_gate_rejects_c_flag_clear_unknown_key_and_wrong_test_mappings(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整灰盒测试设计",
        repo_path=str(repo),
    )
    audit = audit_test_activity_response(
        content=(
            "错误 Login Response 会清除 T/C/CSG/NSG。\n"
            "无法识别的合法参数与重复参数都会返回 0x0200 并使登录失败。\n"
            "100 个 Initiator 同时登录同一 Target，映射 test/iscsi_tgt/multiconnection/multiconnection.sh。\n"
            "模拟网络故障并自动重连，映射 test/iscsi_tgt/login_redirection/login_redirection.sh。\n"
            "使用 test/iscsi_tgt/calsoft/calsoft.py 测量 Login P99 延迟。\n"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    constraint_ids = {
        issue.get("constraint_id")
        for issue in audit["issues"]
        if issue["code"] == "professional_fact_conflict"
    }
    assert {
        "iscsi_login_error_c_flag_preserved",
        "iscsi_unknown_key_not_understood",
        "iscsi_multiconnection_mapping_scope",
        "iscsi_redirection_mapping_scope",
        "iscsi_calsoft_mapping_scope",
    }.issubset(constraint_ids), audit


def test_iscsi_full_design_requires_professional_scenarios_observer_revision_and_safety(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整流程 SFMEA 黑盒测试设计",
        repo_path=str(repo),
    )
    assert contract["quality_gates"]["must_record_source_revision"] is True
    assert contract["quality_gates"]["protocol_claims_require_wire_observer"] is True
    assert contract["quality_gates"]["hazardous_test_mapping_requires_safety"] is True
    assert contract["quality_gates"]["numeric_performance_threshold_requires_baseline"] is True
    content = (
        "## 流程步骤\n1. 建立连接。\n2. CHAP。\n3. 进入 Full Feature。\n"
        "## 异常与恢复\n认证失败后重试。\n"
        "## SFMEA\nfailure_mode cause effect detection severity occurrence detection_score RPN "
        "score_explanation mitigation source_evidence test_mapping\n"
        "| 风险 | 原因 | 后果 | 检测 | 8 | 2 | 3 | 48 | 解释 | 缓解 | lib/iscsi/iscsi.c | test/iscsi_tgt/chap/chap_discovery.sh |\n"
        "| 风险2 | 原因 | 后果 | 检测 | 7 | 2 | 3 | 42 | 解释 | 缓解 | lib/iscsi/param.c | test/iscsi_tgt/digests/digests.sh |\n"
        "## 黑盒测试用例\nnormal_path invalid_input resource_pressure timeout reconnect concurrency recovery performance\n"
        "前置条件 步骤 预期结果 观测点 失败诊断线索。\n"
        "Discovery Login 不返回 TargetAddress。CHAP 失败应返回 0x0201，最终响应为 T=1 CSG=1 NSG=3。\n"
        "性能门槛为平均 <10ms、P99 <50ms。映射 test/iscsi_tgt/multiconnection/multiconnection.sh。\n"
        "## 覆盖与剩余风险\n待验证。\n"
        + ("测试设计证据。" * 120)
    )
    audit = audit_test_activity_response(
        content=content,
        contract=contract,
        repo_path=str(repo),
    )

    issue_codes = {issue["code"] for issue in audit["issues"]}
    assert {
        "missing_iscsi_professional_scenarios",
        "missing_protocol_wire_observer",
        "missing_source_revision",
        "unsafe_hazardous_test_mapping",
        "ungrounded_performance_threshold",
    }.issubset(issue_codes), audit


def test_combined_response_accepts_specific_test_paths_inside_json_strings(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "test" / "iscsi_tgt" / "chap").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text("int login;\n", encoding="utf-8")
    (repo / "test" / "iscsi_tgt" / "chap" / "chap_discovery.sh").write_text(
        "#!/bin/sh\n", encoding="utf-8"
    )
    contract = build_test_activity_contract(
        target="iSCSI Login SFMEA 黑盒测试用例",
        repo_path=str(repo),
    )
    content = (
        'source_evidence: "lib/iscsi/iscsi.c"\n'
        'test_mapping: "test/iscsi_tgt/chap/chap_discovery.sh"\n'
        + ("sfmea failure_mode cause effect detection severity occurrence detection_score rpn "
           "score_explanation mitigation source_evidence test_mapping。\n" * 20)
        + "黑盒测试用例 normal_path invalid_input resource_pressure timeout reconnect concurrency recovery performance。\n"
        + "前置条件、步骤、预期结果、观测点、失败诊断线索。\n"
    )
    audit = audit_test_activity_response(
        content=content,
        contract=contract,
        repo_path=str(repo),
    )

    assert not any(
        issue["code"] in {"missing_combined_source_evidence", "missing_specific_test_evidence"}
        for issue in audit["issues"]
    ), audit


def test_iscsi_gate_rejects_fixed_final_csg_and_false_existing_test_mappings(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整灰盒测试设计",
        repo_path=str(repo),
    )
    audit = audit_test_activity_response(
        content=(
            "最终成功 Login Response 必须且只能为 T=1、CSG=1、NSG=3。\n"
            "test/iscsi_tgt/perf/iscsi_target.sh 直接采集 Login p50/p95/p99。\n"
            "test/iscsi_tgt/multiconnection/multiconnection.sh 覆盖多 Initiator 登录。\n"
            "test/iscsi_tgt/reset/reset.sh 覆盖 logout 后 relogin 恢复。\n"
            "test/unit/lib/iscsi/iscsi.c/iscsi_ut.c 已断言 Target Removed、"
            "Authorization Failure 和所有错误响应 flags。\n"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    constraint_ids = {
        issue.get("constraint_id")
        for issue in audit["issues"]
        if issue["code"] == "professional_fact_conflict"
    }
    assert {
        "iscsi_final_login_stage_alternatives",
        "iscsi_perf_scripts_not_login_latency",
        "iscsi_multiconnection_mapping_scope",
        "iscsi_reset_mapping_scope",
        "iscsi_unit_coverage_scope",
    }.issubset(constraint_ids), audit


def test_iscsi_gate_accepts_explicitly_corrected_mapping_and_boundary_statements(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 灰盒测试设计",
        repo_path=str(repo),
    )
    audit = audit_test_activity_response(
        content=(
            "合法未知 key 返回 NotUnderstood；超长、重复或格式非法参数另按解析错误处理。\n"
            "Discovery session 不应声称 Login Response 必定包含 TargetAddress。\n"
            "Protocol Error=0x04；若抓包看到 0x05，说明断言错误，因为 0x05 是 Command Not Supported。\n"
            "黑盒只用公开 RPC，不调用 iscsi_get_active_conns() 等内部 C 函数。\n"
                "login_redirection.sh 是受控 RPC redirect/logout，不是网络故障自动重连。\n"
                '"expected_result": "Redirect 是受控 RPC redirect/logout，不是网络故障自动重连"\n'
            "calsoft.py 不是登录延迟基准，只保留为协议一致性测试参考。\n"
            '"cause": "把未知合法 key 误当解析失败或直接断开"\n'
            '"cause": "把 SendTargets 写成 Login 前置步骤，或声称 Discovery Login 必定返回 TargetAddress"\n'
            '"cause": "使用 calsoft.py 作为登录延迟基准"\n'
            '"mitigation": "login_redirection.sh 只映射受控 Redirect，不映射网络故障 reconnect"\n'
            '"effect": "中途停滞时，不能确认 30 秒 login_timer 会清理连接"\n'
            '"failure_diagnostics": "若 Protocol Error 为 0x05，断言错误"\n'
            '"failure_diagnostics": "不得把 0x05 写成 Parameter Error"\n'
            'raise AssertionError("error Login Response must clear T/CSG/NSG")\n'
            '"expected_result": "CSG=3 Login Request 不是合法进入 Full Feature 的方式"\n'
            '"expected_result": "Reject reason=0x04 Protocol Error；0x05 是 Command Not Supported"\n'
            "最终 CSG 回显当前阶段，不能把 CSG=1 写成唯一合法终态。\n"
            "`CSG` 回显当前阶段，最终成功不能固定 `CSG=1`。\n"
                '"expected_result": "不得把 multiconnection.sh 解释为同一 Target 100 Initiator 覆盖"\n'
                "multiconnection.sh 不能映射为同一 Target 100 Initiator 或多 CID。\n"
                '"expected_result": "不得解释为同一 Target 100 Initiator 或同一 Initiator 多 CID 覆盖", "mapped_test_dir": "test/iscsi_tgt/multiconnection/multiconnection.sh"\n'
                '"preconditions": "target 允许 CHAP=None 或已配置可通过认证；进入 Operational Negotiation"\n'
                "iscsi_auth_params 只在 CSG=0 Security Negotiation 分支执行；CSG=1 Operational Negotiation 只检查认证完成。\n"
                '"expected_result": "Operational Negotiation 不承载 CHAP challenge/response"\n'
            '"score_explanation": "calsoft.py 是一致性入口，不采集 Login 延迟分位数"\n'
            '"source_evidence": "perf/iscsi_target.sh、perf/iscsi_initiator.sh 和 calsoft.py；这些脚本均不得作为 Login latency test_mapping"\n'
            "Login latency 不使用 perf/iscsi_target.sh、perf/iscsi_initiator.sh 或 calsoft.py 作为性能映射。\n"
                "reset/reset.sh 在持续 fio 中执行 sg_reset，不是 logout/relogin。\n"
                '"expected_result": "reset.sh 不被宣称覆盖 logout/relogin", "mapped_test_dir": "test/iscsi_tgt/reset/reset.sh"\n'
            "iscsi_ut.c 已覆盖部分入口，但未完整断言 Target Removed 和 Authorization Failure 状态码。\n"
            "Discovery connection 可能短暂存在，不能要求 RPC 必定看见。\n"
            "Target Removed 标为 ai_suggested_unverified，不得计入发布通过项。\n"
            '"expected_result": "不得笼统声称跨 PDU 重复 key 一律失败"\n'
            "Target Removed 若只能竞态触发则标注 ai_suggested_unverified 且不计入发布通过。\n"
            '"scenario_name": "受控 reset 与独立 logout/relogin 恢复",\n'
            '"expected_result": "独立执行 logout/relogin；reset.sh 不被宣称覆盖 logout/relogin",\n'
            '"mapped_test_dir": "test/iscsi_tgt/reset/reset.sh；ai_suggested_unverified: add relogin case"\n'
            '"failure_mode": "Target Removed 与 Authorization Failure 状态码错误",\n'
            '"score_explanation": "iscsi_ut.c 只断言部分返回值，不能声称完整覆盖",\n'
            '"test_mapping": "test/unit/lib/iscsi/iscsi.c/iscsi_ut.c；ai_suggested_unverified: add assertions"\n'
        ),
        contract=contract,
        repo_path=str(repo),
    )

    false_positive_ids = {
        issue.get("constraint_id")
        for issue in audit["issues"]
        if issue["code"] == "professional_fact_conflict"
    }
    assert not {
        "iscsi_unknown_key_not_understood",
        "iscsi_discovery_target_address",
        "iscsi_reject_protocol_error_reason",
        "iscsi_internal_observer_boundary",
        "iscsi_redirection_mapping_scope",
        "iscsi_calsoft_mapping_scope",
        "iscsi_perf_scripts_not_login_latency",
        "iscsi_login_timer_after_first_pdu",
        "iscsi_final_login_stage_alternatives",
        "iscsi_multiconnection_mapping_scope",
        "iscsi_reset_mapping_scope",
        "iscsi_unit_coverage_scope",
        "iscsi_reset_relogin_mapping",
        "iscsi_unit_assertion_mapping",
        "iscsi_discovery_rpc_ephemeral",
        "iscsi_target_removed_release_evidence",
        "iscsi_login_status_detail_05",
        "iscsi_login_error_c_flag_preserved",
        "iscsi_full_feature_request_rejected",
            "iscsi_duplicate_key_scope",
            "iscsi_chap_security_stage",
            "iscsi_csg_values",
    }.intersection(false_positive_ids), audit


def test_iscsi_gate_accepts_verbose_unknown_key_not_understood_correction(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 灰盒测试设计",
        repo_path=str(repo),
    )
    audit = audit_test_activity_response(
        content=(
            "参数协商中的未知合法 key：当 iscsi_negotiate_params 处理 Login Request "
            "中的参数时，对于格式合法但 target 未定义的 key，SPDK 会在响应 PDU 的"
            "数据段中返回该 key 并标记 NotUnderstood，而不是导致连接断开或整个 Login 失败。"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    assert not any(
        issue.get("code") == "professional_fact_conflict"
        and issue.get("constraint_id") == "iscsi_unknown_key_not_understood"
        for issue in audit["issues"]
    ), audit


def test_iscsi_full_design_requires_chap_negatives_executable_wire_checks_and_sfmea_scale(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整流程 SFMEA 黑盒测试设计",
        repo_path=str(repo),
    )
    content = (
        "Git revision 97af299e3c76368219f0cddcc710fafd57edcc1c。\n"
        "覆盖 T+C 非法组合、非法 NSG、Unsupported Version、合法未知 key=NotUnderstood、"
        "Target Not Found、Target Removed、Authorization Failure、Redirect、Discovery 后 SendTargets，"
        "并验证首 payload 后 login_timer 注销且未重新注册。\n"
        "最终迁移既允许 T=1 CSG=0 NSG=3，也允许 T=1 CSG=1 NSG=3。\n"
        "通过 tcpdump 保存 pcap，并用原始 PDU 解析器观察字段。\n"
        "multiconnection.sh 仅可在 allowlist 中的隔离测试设备运行并确认数据销毁风险。\n"
        "## SFMEA\nfailure_mode cause effect detection severity occurrence detection_score RPN "
        "score_explanation mitigation source_evidence test_mapping\n"
        "## 黑盒测试用例\nnormal_path invalid_input resource_pressure timeout reconnect concurrency recovery performance\n"
        "前置条件 步骤 预期结果 观测点 失败诊断线索。\n"
        + ("源码证据与测试映射。" * 120)
    )
    audit = audit_test_activity_response(
        content=content,
        contract=contract,
        repo_path=str(repo),
    )

    issue_codes = {issue["code"] for issue in audit["issues"]}
    assert {
        "missing_chap_negative_scenarios",
        "non_executable_protocol_observer",
        "missing_c_bit_fragmentation_case",
        "missing_sfmea_scoring_scale",
    }.issubset(issue_codes), audit


def test_iscsi_c_bit_case_spanning_json_fields_is_not_false_missing(tmp_path):
    from app.services.test_activity_contract import (
        _audit_combined_professional_completeness,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整流程 SFMEA 黑盒测试设计",
        repo_path=str(repo),
    )
    content = (
        "C-bit fragmented Login Request reassembly。"
        "步骤：发送 first Login PDU，C=1，数据段在 key/value 边界截断；"
        "发送 second PDU，C=0，剩余参数收尾。"
    )

    issues = _audit_combined_professional_completeness(content, contract)

    assert "missing_c_bit_fragmentation_case" not in {
        issue["code"] for issue in issues
    }


def test_iscsi_hazardous_mapping_accepts_isolated_test_disk_with_data_loss_notice(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整流程 SFMEA 黑盒测试设计",
        repo_path=str(repo),
    )

    audit = audit_test_activity_response(
        content=(
            "Git revision 97af299e3c76368219f0cddcc710fafd57edcc1c。\n"
            "test/iscsi_tgt/multiconnection/multiconnection.sh 仅允许在隔离测试盘运行，"
            "该脚本会执行随机写，测试数据可销毁且不得指向宿主裸盘。"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    assert not any(
        issue["code"] == "unsafe_hazardous_test_mapping"
        for issue in audit["issues"]
    ), audit


def test_iscsi_release_gate_accepts_explicit_chap_matrix_and_discrete_sfmea_scale(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整流程 SFMEA 黑盒测试设计",
        repo_path=str(repo),
    )
    content = (
        "## 评分标尺\n"
        "Severity：1 无影响；3 可重试；5 兼容失败；7 状态机失效；9 批量失败；10 未授权访问。\n"
        "Occurrence：1 仅理论输入；3 专用时序；5 常见错误配置；7 压力中易出现；10 高频必现。\n"
        "Detection：1 自动化稳定断言；3 单元覆盖；5 日志与 pcap；7 raw-PDU harness；10 不可稳定复验。\n"
        "RPN = Severity * Occurrence * Detection。\n"
        "风险分层：>=160 为 SFMEA 一级风险并优先执行，100-159 为二级风险。\n"
        "Mutual CHAP 缺少 challenge；Mutual CHAP 时发起方缺少 CHAP_I 或 CHAP_N，均独立覆盖。\n"
        "initiator 请求 mutual 但 target 未启用 mutual，运行 initiator-mutual-target-forbids。\n"
        "Mutual CHAP target digest oracle 不匹配，正确 secret 必须匹配且错误 secret 必须不匹配。\n"
        "不支持 CHAP_A、缺少 CHAP_R、CHAP_R hex/base64 编码错误分别执行。\n"
        "错误 CHAP_R、未知 CHAP 用户、CHAP 参数顺序错误分别执行。\n"
        "Target 要求 Mutual CHAP 但 Initiator 未提供，执行独立 raw-PDU case。\n"
        "target 要求 mutual 但 initiator 未提供，执行 target-requires-mutual-initiator-omits。\n"
        "Target Removed 若只能竞态触发，标注 ai_suggested_unverified 且不计发布通过。\n"
        + ("完整流程、源码证据和原子测试映射。" * 120)
    )
    audit = audit_test_activity_response(
        content=content,
        contract=contract,
        repo_path=str(repo),
    )

    issue_codes = {issue["code"] for issue in audit["issues"]}
    constraint_ids = {
        issue.get("constraint_id")
        for issue in audit["issues"]
        if issue["code"] == "professional_fact_conflict"
    }
    assert "missing_extended_chap_negative_scenarios" not in issue_codes, audit
    assert "missing_sfmea_scoring_scale" not in issue_codes, audit
    assert "iscsi_target_removed_release_evidence" not in constraint_ids, audit


def test_iscsi_extended_chap_gate_treats_missing_chap_n_as_missing_mutual_user(tmp_path):
    from app.services.test_activity_contract import (
        _audit_combined_professional_completeness,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整流程 SFMEA 黑盒测试设计",
        repo_path=str(repo),
    )
    content = (
        "不支持的 CHAP_A 算法。缺少 CHAP_R。CHAP_R hex 编码格式错误。"
        "Initiator 请求 mutual CHAP 但 Target 未启用。"
        "Mutual challenge 合法编码但语义错误。"
        "Mutual CHAP 时发起方缺少 CHAP_I 或 CHAP_N。"
    )

    issues = _audit_combined_professional_completeness(content, contract)
    extended = [
        issue
        for issue in issues
        if issue.get("code") == "missing_extended_chap_negative_scenarios"
    ]

    assert not any(
        "Mutual 用户或 secret 缺失" in (issue.get("scenarios") or [])
        for issue in extended
    ), extended


def test_iscsi_completeness_accepts_report_english_chap_and_timer_equivalents(tmp_path):
    from app.services.test_activity_contract import (
        _audit_combined_professional_completeness,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整流程 SFMEA 黑盒测试设计",
        repo_path=str(repo),
    )
    content = (
        "iscsi_pdu_payload_op_login calls spdk_poller_unregister to cancel the login timer "
        "when the first payload is processed. "
        "Unsupported CHAP_A algorithm is rejected. Missing CHAP_R is rejected. "
        "CHAP_R encoding format error (non-base64 characters in response). "
        "Mutual CHAP user or secret is not configured on target. "
        "Initiator requests mutual but target does not allow mutual. "
        "Mutual challenge correctly encoded but semantic mismatch. "
        "Target requires Mutual CHAP but Initiator does not provide any CHAP at all."
    )

    issues = _audit_combined_professional_completeness(content, contract)
    missing = {
        scenario
        for issue in issues
        if issue.get("code")
        in {
            "missing_iscsi_professional_scenarios",
            "missing_chap_negative_scenarios",
            "missing_extended_chap_negative_scenarios",
        }
        for scenario in issue.get("scenarios") or []
    }

    assert "首 payload 后 timer 注销" not in missing
    assert "CHAP_R 编码格式错误" not in missing
    assert "Mutual 用户或 secret 缺失" not in missing
    assert "Initiator 请求 Mutual 但 Target 禁止" not in missing
    assert "Mutual challenge 合法编码但语义错误" not in missing
    assert "Target 要求 Mutual 但 Initiator 未提供" not in missing


def test_iscsi_completeness_recognizes_run46_disabled_login_timer_scenario(tmp_path):
    from app.services.test_activity_contract import (
        _audit_combined_professional_completeness,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整流程 SFMEA 黑盒测试设计",
        repo_path=str(repo),
    )
    content = """
## 黑盒测试用例
### BB-16 Login stall after first PDU (login_timer disabled)
- 预期结果：Behavior unverified: login_timer disabled after first PDU, so no 30s timeout.
- 观测点：After >30s, check /proc/net/tcp for persisted target port connection (resource leak oracle).
"""

    issues = _audit_combined_professional_completeness(content, contract)
    missing = {
        scenario
        for issue in issues
        if issue.get("code") == "missing_iscsi_professional_scenarios"
        for scenario in issue.get("scenarios") or []
    }

    assert "首 payload 后 timer 注销" not in missing


def test_iscsi_gate_rejects_non_executable_or_semantically_false_blackbox_mappings(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整灰盒黑盒测试设计",
        repo_path=str(repo),
    )
    audit = audit_test_activity_response(
        content=(
            '"scenario_name": "多 initiator/多 target 并发登录",\n'
            '"mapped_test_dir": "test/iscsi_tgt/multiconnection/multiconnection.sh"\n'
            '"scenario_name": "logout 后 relogin 恢复",\n'
            '"mapped_test_dir": "test/iscsi_tgt/reset/reset.sh"\n'
            '"failure_mode": "Target Removed 与 Authorization Failure flags",\n'
            '"test_mapping": "test/unit/lib/iscsi/iscsi.c/iscsi_ut.c"\n'
            "Discovery Login 成功后公开 RPC 必定可见 discovery connection。\n"
            "每个成功 Login Response 都只显示 running/full_feature_phase。\n"
            "重复 key 一律按解析错误处理。\n"
            "Target Removed 标为 ai_suggested_unverified，但作为发布通过项。\n"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    constraint_ids = {
        issue.get("constraint_id")
        for issue in audit["issues"]
        if issue["code"] == "professional_fact_conflict"
    }
    assert {
        "iscsi_multiconnection_scenario_semantics",
        "iscsi_reset_relogin_mapping",
        "iscsi_unit_assertion_mapping",
        "iscsi_discovery_rpc_ephemeral",
        "iscsi_successful_login_phase_observation",
        "iscsi_duplicate_key_scope",
        "iscsi_target_removed_release_evidence",
    }.issubset(constraint_ids), audit


def test_iscsi_release_gate_rejects_shallow_raw_pdu_chap_and_statistical_design(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整可执行流程 SFMEA 黑盒测试设计",
        repo_path=str(repo),
    )
    audit = audit_test_activity_response(
        content=(
            "Git revision 97af299e3c76368219f0cddcc710fafd57edcc1c。\n"
            "覆盖 T+C、非法 NSG、Unsupported Version、NotUnderstood、Target Not Found/Removed、"
            "Authorization Failure、Redirect、Discovery SendTargets、login_timer 注销未重注册。\n"
            "CHAP 负向覆盖错误 CHAP_R、未知用户、顺序错误、Mutual CHAP 错误 challenge、"
            "Target 要求 Mutual 但 Initiator 未提供。\n"
            "C=1 时 key/value 跨 PDU 分片，C=0 收尾。\n"
            "tcpdump -i lo -w login.pcap tcp port 3260；"
            "tshark -r login.pcap -Y iscsi -T fields -e iscsi.login_transit。\n"
            "准备外部 raw PDU 工具发送报文。\n"
            '"scenario_name": "T+C、非法 NSG、Unsupported Version、C-bit 分片",\n'
            '"mapped_test_dir": "test/fuzz/autofuzz_iscsi.sh；test/iscsi_tgt/calsoft/calsoft.py"\n'
            '"scenario_name": "Target 要求 Mutual CHAP 但 Initiator 未提供",\n'
            '"mapped_test_dir": "test/iscsi_tgt/chap/chap_mutual_not_set.sh"\n'
            "P95 相对退化超过 20% 判失败；已有同硬件同样本量 baseline。\n"
            "Severity/Occurrence/Detection 均为 1-10；RPN >= 160 定义为 P0 并优先执行。\n"
            "SFMEA occurrence 由分析人员估计。\n"
            + ("测试设计证据。" * 120)
        ),
        contract=contract,
        repo_path=str(repo),
    )

    issue_codes = {issue["code"] for issue in audit["issues"]}
    constraint_ids = {
        issue.get("constraint_id")
        for issue in audit["issues"]
        if issue["code"] == "professional_fact_conflict"
    }
    assert {
        "non_executable_raw_pdu_harness",
        "missing_extended_chap_negative_scenarios",
        "missing_performance_statistical_basis",
        "missing_sfmea_occurrence_basis",
        "non_atomic_blackbox_case",
    }.issubset(issue_codes), audit
    assert {
        "iscsi_mutual_not_set_mapping_direction",
        "iscsi_fuzz_calsoft_semantic_mapping",
        "sfmea_rpn_not_defect_priority",
    }.issubset(constraint_ids), audit


def test_iscsi_release_gate_rejects_syntactically_invalid_python_harness(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整流程 SFMEA raw-PDU 黑盒测试设计",
        repo_path=str(repo),
        workflow_outputs=[
            {"artifact": "business_flow.md"},
            {"artifact": "sfmea.json"},
            {"artifact": "black_box_cases.json"},
        ],
    )
    content = (
        "```python\n"
        "import socket, struct, hashlib\n"
        "def run():\n"
        "bhs = bytearray(48)\n"
        "struct.pack_into('!I', bhs, 16, itt)\n"
        "isid = cid = itt = cmdsn = 1\n"
        "sock = socket.create_connection(('127.0.0.1', 3260))\n"
        "sock.sendall(bhs)\n"
        "data = sock.recv(48)\n"
        "digest = hashlib.md5(data).digest()\n"
        "```\n"
        "```python\nvalue = 1\n```\n"
        + ("raw PDU 可执行测试说明、源码证据与用例映射。" * 100)
    )
    audit = audit_test_activity_response(
        content=content,
        contract=contract,
        repo_path=str(repo),
    )

    assert any(
        issue["code"] == "non_executable_raw_pdu_harness"
        and "Python 语法" in issue["message"]
        for issue in audit["issues"]
    ), audit


def test_iscsi_release_gate_rejects_python_harness_with_invalid_wire_semantics(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整流程 SFMEA raw-PDU 黑盒测试设计",
        repo_path=str(repo),
        workflow_outputs=[
            {"artifact": "business_flow.md"},
            {"artifact": "sfmea.json"},
            {"artifact": "black_box_cases.json"},
        ],
    )
    content = (
        "```python\n"
        "import socket, struct, hashlib\n"
        "def pack_dsl(bhs, length):\n"
        "    bhs[4] = length >> 16\n"
        "    bhs[5] = length >> 8\n"
        "    bhs[6] = length\n"
        "def recv_pdu(sock):\n"
        "    bhs = sock.recv(48)\n"
        "    return sock.recv(dlen)\n"
        "def expect_auth_fail(response):\n"
        "    if response != 2:\n"
        "        raise AssertionError('wrong status')\n"
        "    raise AssertionError('always fails')\n"
        "def case_bad_chap_r(sock):\n"
        "    digest = hashlib.md5(bytes([chap_i]) + challenge).digest()\n"
        "    sock.sendall(struct.pack('!I', len(digest)))\n"
        "    return sock.recv(48)\n"
        "```\n"
        + ("raw PDU 可执行测试说明、源码证据与用例映射。" * 100)
    )
    audit = audit_test_activity_response(
        content=content,
        contract=contract,
        repo_path=str(repo),
    )

    messages = [
        issue["message"]
        for issue in audit["issues"]
        if issue["code"] == "non_executable_raw_pdu_harness"
    ]
    assert any("DataSegmentLength" in message for message in messages), audit
    assert any("未定义名称" in message for message in messages), audit
    assert any("无条件抛出" in message for message in messages), audit


def test_raw_pdu_static_analysis_accepts_nested_locals_and_mutual_helper():
    import ast

    from app.services.test_activity_contract import _raw_pdu_python_semantic_errors

    source = """
import socket

def pack_dsl(bhs, length):
    bhs[5:8] = length.to_bytes(3, "big")

def unpack_dsl(response):
    return int.from_bytes(response[5:8], "big")

def complete_chap_response(sess, args, include_mutual=False):
    items = [("CHAP_I", "7"), ("CHAP_C", "0x0102")] if include_mutual else []
    return sess.send(items)

def case_initiator_mutual_target_forbids(sess, args):
    return complete_chap_response(sess, args, include_mutual=True)

def case_mutual_semantic_wrong_challenge(sess, args):
    wrong_mutual_secret = args.mutual_secret + "_wrong"
    response = sess.send([wrong_mutual_secret])
    actual = decode_chap_value(response["CHAP_R"])
    expected_correct = decode_chap_value(chap_digest(args.mutual_secret))
    expected_wrong = decode_chap_value(chap_digest(wrong_mutual_secret))
    if actual != expected_correct or actual == expected_wrong:
        raise AssertionError("mutual oracle mismatch")
    return actual

def decode_chap_value(value):
    return value.casefold().encode()

def chap_digest(secret):
    return secret

def expect_full_feature(response):
    NSG_FULL_FEATURE = 3
    if response.get("status_class") != 0 or response.get("t") != 1 or response.get("nsg") != NSG_FULL_FEATURE:
        raise AssertionError("not full feature")

def case_operational_multi_round(sess, args):
    response = sess.send([])
    expect_full_feature(response)
    return response

def run_self_test():
    class DummyArgs:
        mutual_secret = "secret"
    try:
        values = [x for x in range(2)]
    except ValueError as exc:
        return DummyArgs(), exc
    return values
"""

    assert _raw_pdu_python_semantic_errors(source, ast.parse(source)) == []


def test_raw_pdu_static_analysis_accepts_nested_class_method_scope():
    import ast

    from app.services.test_activity_contract import _undefined_python_function_names

    source = """
def run_self_test():
    class RelatedConnection:
        pass

    class Session:
        def clone_connection(self, cid=None, tsih=None):
            assert cid == 1
            assert tsih == 0x1234
            return RelatedConnection()

    return Session().clone_connection(cid=1, tsih=0x1234)
"""

    assert _undefined_python_function_names(ast.parse(source)) == []


def test_raw_pdu_static_analysis_rejects_mutual_and_full_feature_false_passes():
    import ast

    from app.services.test_activity_contract import _raw_pdu_python_semantic_errors

    source = '''
def pack_dsl(bhs, length):
    bhs[5:8] = length.to_bytes(3, "big")

def unpack_dsl(response):
    return int.from_bytes(response[5:8], "big")

def chap_digest(chap_i, secret, challenge):
    return secret

def expect_auth_fail(response):
    return response

def expect_success_or_continue(response):
    return response

def case_mutual_semantic_wrong_challenge(sess, args):
    response = sess.send([])
    if response.get("status_class") != 0:
        expect_auth_fail(response)
        return
    actual = response.get("CHAP_R")
    expected_wrong = chap_digest(7, args.mutual_secret + "_wrong", b"challenge")
    if actual == expected_wrong:
        raise AssertionError("negative oracle did not detect mismatch")

def case_operational_multi_round(sess, args):
    response1 = sess.send([])
    expect_success_or_continue(response1)
    response2 = sess.send([])
    expect_success_or_continue(response2)
'''

    errors = _raw_pdu_python_semantic_errors(source, ast.parse(source))

    assert any("Mutual CHAP oracle" in error for error in errors), errors
    assert any("Full Feature" in error for error in errors), errors


def test_raw_pdu_static_analysis_rejects_self_test_that_swallows_its_failure_sentinel():
    import ast

    from app.services.test_activity_contract import _raw_pdu_python_semantic_errors

    source = '''
def pack_dsl(bhs, length):
    bhs[5:8] = length.to_bytes(3, "big")

def unpack_dsl(response):
    return int.from_bytes(response[5:8], "big")

def run_self_test():
    for bad in ({"status_class": 0},):
        try:
            expect_mutual_chap_oracle(bad)
            raise AssertionError("bad mutual response passed")
        except AssertionError:
            pass
'''

    errors = _raw_pdu_python_semantic_errors(source, ast.parse(source))

    assert any("自检失败哨兵" in error for error in errors), errors


def test_raw_pdu_static_analysis_accepts_mutual_oracle_delegated_to_local_helper():
    import ast

    from app.services.test_activity_contract import _raw_pdu_python_semantic_errors

    source = '''
def pack_dsl(bhs, length):
    bhs[5:8] = length.to_bytes(3, "big")

def unpack_dsl(response):
    return int.from_bytes(response[5:8], "big")

def decode_chap_value(value):
    return bytes.fromhex(value.removeprefix("0x"))

def chap_digest_bytes(chap_i, secret, challenge):
    return bytes([chap_i]) + secret.encode() + challenge

def expect_mutual_chap_oracle(response, chap_i, challenge, args):
    actual = decode_chap_value(response["CHAP_R"])
    expected_good = chap_digest_bytes(chap_i, args.target_mutual_secret, challenge)
    expected_bad = chap_digest_bytes(chap_i, args.wrong_mutual_secret, challenge)
    if response["CHAP_N"] != args.mutual_user or actual != expected_good or actual == expected_bad:
        raise AssertionError("mutual oracle mismatch")

def case_mutual_semantic_wrong_challenge(sess, args):
    response, chap_i, challenge = sess.send(args.wrong_mutual_secret)
    expect_mutual_chap_oracle(response, chap_i, challenge, args)

def expect_full_feature(response):
    if response.get("status_class") != 0 or response.get("t") != 1 or response.get("nsg") != 3:
        raise AssertionError("not full feature")

def case_operational_multi_round(sess, args):
    response = sess.send([])
    expect_full_feature(response)
'''

    assert _raw_pdu_python_semantic_errors(source, ast.parse(source)) == []


def test_professional_constraints_accept_explicit_corrections_in_json_artifacts(tmp_path):
    from app.services.test_activity_contract import (
        _audit_professional_constraints,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整灰盒测试设计",
        repo_path=str(repo),
    )
    content = (
        "避免把 Operational Negotiation 写成 CHAP 执行阶段；CSG=0 执行 CHAP，"
        "CSG=1 只校验认证状态。\n"
        "Mutual CHAP oracle 改为 bytes 比较，多轮 Operational Negotiation 最终轮改用 expect_full_feature 断言。\n"
        '{"failure_mode":"CSG=3 Login Request 被误认为合法迁移",'
        '"cause":"把 Full Feature Phase 请求写成合法登录迁移",'
        '"mitigation":"CSG=3 必须返回 Initiator Error"}\n'
        '{"failure_mode":"multiconnection.sh 被过度映射",'
        '"cause":"错误解释成同一 Target 100 Initiator、多 CID",'
        '"mitigation":"不得过度映射 multiconnection.sh"}\n'
        '"expected_result":"受控 Redirect 不得解释为网络故障自动重连",'
        '"mapped_test_dir":"test/iscsi_tgt/login_redirection/login_redirection.sh"\n'
        '"expected_result":"reset 场景不宣称覆盖 logout/relogin",'
        '"mapped_test_dir":"test/iscsi_tgt/reset/reset.sh"\n'
        '"scenario_name":"Discovery Login 不强制 TargetAddress",'
        '"expected_result":"Discovery Login 不要求包含 TargetAddress"\n'
        '"scenario_name":"logout 后 relogin 独立用例",'
        '"mapped_test_dir":"ai_suggested_unverified: add logout-relogin test；reset/reset.sh 不覆盖"\n'
        "结论：CSG=1 不再承载 CHAP，最终 Operational Negotiation 使用独立 expect_full_feature。\n"
        "test/iscsi_tgt/multiconnection/multiconnection.sh 仅作单 initiator、多 target/connection 参考，"
        "不证明同一 Target 100 Initiator 或多 CID。\n"
        '"failure_diagnostics":"multiconnection.sh 仅参考，不证明同 Target 100 initiator",'
        '"mapped_test_dir":"ai_suggested_unverified: add；test/iscsi_tgt/multiconnection/multiconnection.sh 仅参考"\n'
        "reset/reset.sh 不再映射 logout/relogin。\n"
    )

    issues = _audit_professional_constraints(content, contract)
    issue_ids = {issue.get("constraint_id") for issue in issues}

    assert not {
        "iscsi_chap_security_stage",
        "iscsi_csg_values",
        "iscsi_full_feature_request_rejected",
        "iscsi_multiconnection_mapping_scope",
        "iscsi_redirection_mapping_scope",
        "iscsi_reset_mapping_scope",
        "iscsi_discovery_target_address",
        "iscsi_reset_relogin_mapping",
    }.intersection(issue_ids), issues


def test_professional_constraints_reject_incorrect_session_or_test_mapping_claims(tmp_path):
    from app.services.test_activity_contract import (
        _audit_professional_constraints,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整灰盒测试设计",
        repo_path=str(repo),
    )
    content = (
        "CID 冲突必须返回 Too Many Connections status-detail=0x06。\n"
        "TSIH session reinstatement 复用返回的非零 TSIH，并换一个 CID 建立第二连接。\n"
        "CmdSN/ExpStatSN 异常必须返回 Initiator Error。\n"
        "未知 CHAP 用户由 test/iscsi_tgt/chap/chap_discovery.sh:23-32 覆盖。\n"
        "test/iscsi_tgt/rpc_config/rpc_config.py:107-127 验证 RPC state=running/login_phase=full_feature_phase。\n"
        "test/iscsi_tgt/reset/reset.sh:47-68 证明 fio I/O 可恢复。\n"
    )

    issues = _audit_professional_constraints(content, contract)
    issue_ids = {issue.get("constraint_id") for issue in issues}

    assert {
        "iscsi_duplicate_cid_not_too_many_connections",
        "iscsi_tsih_reinstatement_scope",
        "iscsi_cmdsn_expstatsn_rejection_unverified",
        "iscsi_unknown_user_test_mapping_scope",
        "iscsi_rpc_config_mapping_scope",
        "iscsi_reset_io_recovery_mapping_scope",
    }.issubset(issue_ids), issues


def test_professional_constraints_accept_corrected_session_and_mapping_boundaries(tmp_path):
    from app.services.test_activity_contract import (
        _audit_professional_constraints,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整灰盒测试设计",
        repo_path=str(repo),
    )
    content = (
        "重复 CID 不能直接映射为 Too Many Connections；0x06 由 MaxConnections 容量触发。\n"
        "Session reinstatement 使用同一 ISID 且 TSIH=0；非零 TSIH 是向现有 session 追加连接。\n"
        "源码没有证明异常 CmdSN/ExpStatSN 会返回 Initiator Error，因此先记录响应而不预设拒绝。\n"
        "chap_discovery.sh 不覆盖未知 CHAP_N 用户，unknown-user 只标 ai_suggested_unverified。\n"
        "rpc_config.py 不断言 state/login_phase 或 wire T/NSG；这些需要独立 pcap/RPC 断言。\n"
        "reset/reset.sh 只检查 target/fio 进程存活，不证明 I/O 成功恢复。\n"
        "multiconnection.sh 不映射同一 Target 多 initiator 或多 CID。\n"
        '"expected_result":"记录 CmdSN/ExpStatSN 实际响应；无源码/规范证据前不作为发布判定"\n'
        '"mitigation":"unknown-user 仅映射 harness，不映射 chap_discovery.sh"\n'
        "rpc_config.py 只映射公开连接字段，不作为 wire T/NSG/CSG 或 state/login_phase 断言证据。\n"
        "I/O reset 后恢复成功当前不由 reset.sh 证明。\n"
        "login_redirection.sh 覆盖 redirect/logout，不代表网络故障自动重连。\n"
        '"source_or_test_evidence":"perf/iscsi_target.sh: fio I/O only; calsoft.py conformance only"\n'
        "reset/reset.sh 不证明 I/O 成功恢复或 logout/relogin。\n"
    )

    issues = _audit_professional_constraints(content, contract)
    issue_ids = {issue.get("constraint_id") for issue in issues}

    assert not {
        "iscsi_duplicate_cid_not_too_many_connections",
        "iscsi_tsih_reinstatement_scope",
        "iscsi_cmdsn_expstatsn_rejection_unverified",
        "iscsi_unknown_user_test_mapping_scope",
        "iscsi_rpc_config_mapping_scope",
        "iscsi_reset_io_recovery_mapping_scope",
    }.intersection(issue_ids), issues


def test_professional_constraints_accept_inline_diagnostic_corrections(tmp_path):
    from app.services.test_activity_contract import (
        _audit_professional_constraints,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整灰盒测试设计",
        repo_path=str(repo),
    )
    content = (
        "| B03 | 非法 NSG | 发送 T=1、CSG=0、NSG=2 | "
        "status_class 0x02、detail 0x00；T/CSG/NSG 清除 | "
        "若 detail 0x0b，说明测试期望误用规范值，需按当前实现核验 |\n"
        "| B04 | Unsupported Version | 设置 BHS byte 3 version_min=1，"
        "byte 2 version_max=0 | detail 0x05 | "
        "若改了 bytes 40-41，则是 harness 错误，非版本字段 |\n"
        "| B01 | 正常登录 | RPC 观察 state=running 和 login_phase | "
        "test/iscsi_tgt/rpc_config/rpc_config.py 部分覆盖公开连接字段，"
        "不直接断言 state/login_phase 或 wire 状态 |\n"
    )

    issues = _audit_professional_constraints(content, contract)
    issue_ids = {issue.get("constraint_id") for issue in issues}

    assert not {
        "iscsi_invalid_login_request_detail",
        "iscsi_login_version_offsets",
        "iscsi_rpc_config_mapping_scope",
    }.intersection(issue_ids), issues


def test_iscsi_login_request_gate_allows_stage_handler_to_read_request_flags(tmp_path):
    from app.services.test_activity_contract import (
        _audit_professional_constraints,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="完整 iSCSI Login 测试设计",
        repo_path=str(repo),
    )
    correct = (
        "Login Request 的头部入口是 iscsi_pdu_hdr_op_login，负载入口是 "
        "iscsi_pdu_payload_op_login。响应构建阶段，"
        "iscsi_op_login_rsp_handle_csg_bit 根据收到的 Login Request 中的 CSG 位处理阶段分支。\n"
        "| FMEA-14 | Full Feature Phase 的 Login Request 不被拒绝 | "
        "iscsi_op_login_rsp_handle_csg_bit 返回错误，但需检查实际返回处理 | 6 | "
        "确保处理正确，发送 Login Request | "
        "lib/iscsi/iscsi.c::iscsi_op_login_rsp_handle_csg_bit |\n"
    )
    incorrect = "iscsi_op_login_rsp_handle_csg_bit 直接接收 Login Request 并作为入口。"

    correct_issues = _audit_professional_constraints(correct, contract)
    incorrect_issues = _audit_professional_constraints(incorrect, contract)

    assert not any(
        issue.get("constraint_id") == "iscsi_login_request_entry"
        for issue in correct_issues
    ), correct_issues
    assert any(
        issue.get("constraint_id") == "iscsi_login_request_entry"
        for issue in incorrect_issues
    ), incorrect_issues


def test_iscsi_c_flag_gate_accepts_explicit_preserved_flag_wording(tmp_path):
    from app.services.test_activity_contract import (
        _audit_professional_constraints,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="完整 iSCSI Login 测试设计",
        repo_path=str(repo),
    )

    issues = _audit_professional_constraints(
        "Login request with T=1 and C=1 is rejected. Error response path clears "
        "T/CSG/NSG flags but C flag may remain set.",
        contract,
    )

    assert not any(
        issue.get("constraint_id") == "iscsi_login_error_c_flag_preserved"
        for issue in issues
    ), issues


def test_iscsi_gate_accepts_adjacent_stage_alternatives_and_equivalent_unsupported_chap_wording(
    tmp_path,
):
    from app.services.test_activity_contract import (
        _audit_combined_professional_completeness,
        _audit_professional_constraints,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="完整 iSCSI Login 测试设计",
        repo_path=str(repo),
    )
    content = (
        "Security Negotiation Phase（CSG=0）与 Operational Negotiation Phase（CSG=1）的参数协商。\n"
        "成功响应显示 CSG=0、NSG=1 或 NSG=3、T=1（若为最终阶段）。\n"
        "若请求中 T=1 且 NSG=3，则响应 T=1、NSG=3，进入 Full Feature Phase。\n"
        "两阶段登录：Security Negotiation（CSG=0）到 Operational Negotiation（CSG=1）。"
        "最终成功响应为 CSG=1、NSG=3、T=1。\n"
        "单阶段登录：Security Negotiation 直接完成所有协商。"
        "最终成功响应为 CSG=0、NSG=3、T=1。\n"
        "Session Reinstatement（会话重建）：使用与已有 session 相同的 ISID 但 TSIH=0 发起新 Login。\n"
        "携带已有 session 的非零 TSIH 以及新的 CID，是向现有 session 追加连接。\n"
        "Discovery Login Response 中不会收到 TargetAddress，因为 discovery session 没有 target。\n"
        "未知 CHAP_N 用户需新增 raw-PDU 用例；chap_discovery.sh 未覆盖该场景。\n"
        '[{"failure_mode":"CHAP算法不匹配",'
        '"cause":"Initiator 在 CHAP_A 中选择 SHA，但 SPDK 仅支持 MD5",'
        '"mitigation":"发送 CHAP_A=5 并断言认证失败"}]\n'
    )

    constraints = _audit_professional_constraints(content, contract)
    constraint_ids = {issue.get("constraint_id") for issue in constraints}
    assert not {
        "iscsi_chap_request_response_flags",
        "iscsi_csg_values",
        "iscsi_final_login_stage_alternatives",
        "iscsi_tsih_reinstatement_scope",
        "iscsi_discovery_target_address",
        "iscsi_unknown_user_test_mapping_scope",
    }.intersection(constraint_ids), constraints

    completeness = _audit_combined_professional_completeness(content, contract)
    assert not any(
        issue.get("code") == "missing_extended_chap_negative_scenarios"
        and "不支持的 CHAP_A 算法" in (issue.get("scenarios") or [])
        for issue in completeness
    ), completeness


def test_iscsi_gate_accepts_run49_operational_then_security_stage_wording(tmp_path):
    from app.services.test_activity_contract import (
        _audit_professional_constraints,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="完整 iSCSI Login 测试设计",
        repo_path=str(repo),
    )
    content = (
        "在操作协商阶段（CSG=1）或安全协商阶段（CSG=0），"
        "Initiator 可以携带格式合法但未实现的 key。"
    )

    issues = _audit_professional_constraints(content, contract)

    assert not any(
        issue.get("constraint_id") == "iscsi_csg_values"
        for issue in issues
    ), issues


def test_combined_sfmea_requires_descending_rpn_order():
    from app.services.test_activity_contract import _audit_combined_sfmea_order

    content = '''
```json
[
  {"failure_mode":"A","rpn":120},
  {"failure_mode":"B","rpn":240},
  {"failure_mode":"C","rpn":80}
]
```
'''

    issues = _audit_combined_sfmea_order(content)

    assert issues and issues[0]["code"] == "sfmea_not_sorted_by_rpn"


def test_complete_delivery_rejects_malformed_json_and_undefined_profiles():
    from app.services.test_activity_contract import _audit_combined_execution_contract

    content = '''
| `P1_DISCOVERY_CHAP` | Discovery | commands |

```json
[{"case_id":"BB_001","preconditions":["P0_NO_CHAP"]}]
```

```json
[{"failure_mode":"broken","rpn":120]
```
'''

    issues = _audit_combined_execution_contract(content)
    codes = {issue["code"] for issue in issues}

    assert "invalid_fenced_json" in codes
    assert "undefined_execution_profile" in codes


def test_complete_delivery_rejects_incomplete_chap_profiles_and_capacity_setup():
    from app.services.test_activity_contract import _audit_combined_execution_contract

    content = '''
| `P0_NO_CHAP` | no auth | target only |
| `P1_DISCOVERY_CHAP` | discovery chap | auth only |
| `P2_DISCOVERY_MUTUAL` | discovery mutual | auth only |
| `P3_NORMAL_CHAP` | normal chap | iscsi_target_node_set_auth -g 1 |
| `P4_NORMAL_MUTUAL` | mutual | iscsi_target_node_set_auth -g 2 -m |

The mcs-capacity-limit case uses MaxConnections and --max-connections-probe.
'''

    issues = _audit_combined_execution_contract(content)
    codes = {issue["code"] for issue in issues}

    assert "incomplete_execution_profile" in codes
    assert "missing_max_connections_target_setup" in codes


def test_complete_delivery_requires_executable_discovery_credentials_and_mcs_case_oracle():
    from app.services.test_activity_contract import _audit_combined_execution_contract

    content = '''
| Profile | Purpose | Discovery | Normal | Commands |
|---|---|---:|---:|---|
| `P1_DISCOVERY_CHAP` | discovery chap | yes | no | --wait-for-rpc; iscsi_set_options; framework_start_init; iscsi_create_auth_group; iscsi_auth_group_add_secret; iscsi_set_discovery_auth; configure discovery username/password; run discovery; clean credentials |
| `P2_DISCOVERY_MUTUAL` | discovery mutual | yes | no | --wait-for-rpc; iscsi_set_options; framework_start_init; iscsi_create_auth_group; iscsi_auth_group_add_secret; iscsi_set_discovery_auth; configure mutual discovery username/password; run discovery; clean credentials |

```json
[
  {
    "case_id":"BB_032",
    "scenario_name":"MCS append connection",
    "steps":["open a second connection with the returned TSIH"],
    "expected_result":"second connection enters Full Feature",
    "observability":"pcap"
  }
]
```
'''

    issues = _audit_combined_execution_contract(content)
    codes = {issue["code"] for issue in issues}

    assert "non_executable_discovery_credentials" in codes
    assert "incomplete_mcs_black_box_oracle" in codes


def test_complete_delivery_rejects_broken_profile_markdown_separator():
    from app.services.test_activity_contract import _audit_combined_execution_contract

    content = '''
| Profile | Purpose | Discovery | Normal | Commands |
:|---:|---|
| `P0_NO_CHAP` | baseline | no | no | commands |
'''

    issues = _audit_combined_execution_contract(content)

    assert any(issue["code"] == "invalid_profile_table" for issue in issues), issues


def test_markdown_audit_rejects_truncated_table_row(tmp_path):
    from app.services.test_activity_contract import _audit_markdown_artifact

    content = '''
## 主流程与异常/恢复流程

| 步骤 | 外部行为 | 源码证据 | 证据 ID |
|:---:|---|---|---|
| 7.1 | 首连接登录成功 | `lib/iscsi/iscsi.c` | SRC-01 |
| 7.2 | Initiator 使用相同 TSIH 发起第二连接 | `lib/iscsi/iscsi.c` | SRC-02 |
| 7.3 | `append_iscsi_sess` 检查容量 | `lib/iscsi

## SFMEA

| ID | Failure mode |
|---|---|
| FMEA-01 | capacity |
'''

    issues = _audit_markdown_artifact(
        artifact="report.md",
        content=content,
        spec={"sections": ["主流程与异常/恢复流程", "SFMEA"]},
        repo=tmp_path,
    )

    assert any(issue["code"] == "malformed_markdown_table" for issue in issues), issues


def test_markdown_audit_does_not_treat_explicit_evidence_gap_as_claimed_path(tmp_path):
    from app.services.test_activity_contract import _audit_markdown_artifact

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "test").mkdir()
    (repo / "src" / "connect.c").write_text("int connect_target;\n", encoding="utf-8")
    (repo / "test" / "connect.c").write_text("int test_connect;\n", encoding="utf-8")
    content = (
        "## 证据\n"
        "已验证源码 `src/connect.c`，测试证据 `test/connect.c`。\n"
        "## 证据缺口\n"
        "TLS PSK 失败清理证据：待补（psk.c 不在当前证据白名单内）。\n"
    )

    issues = _audit_markdown_artifact(
        artifact="test_design_mindmap.md",
        content=content,
        spec={"sections": ["证据", "证据缺口"]},
        repo=repo,
    )

    assert not any(
        issue["code"] == "evidence_path_not_found" and "psk.c" in issue["message"]
        for issue in issues
    ), issues


def test_markdown_audit_still_rejects_unqualified_missing_evidence_path(tmp_path):
    from app.services.test_activity_contract import _audit_markdown_artifact

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "test").mkdir()
    (repo / "src" / "connect.c").write_text("int connect_target;\n", encoding="utf-8")
    (repo / "test" / "connect.c").write_text("int test_connect;\n", encoding="utf-8")

    issues = _audit_markdown_artifact(
        artifact="test_design_mindmap.md",
        content=(
            "源码 `src/connect.c`，测试 `test/connect.c`，TLS 证据 `psk.c`。\n"
        ),
        spec={},
        repo=repo,
    )

    assert any(
        issue["code"] == "evidence_path_not_found" and "psk.c" in issue["message"]
        for issue in issues
    ), issues


def test_raw_pdu_static_analysis_rejects_reinstatement_that_closes_old_session_first():
    import ast

    from app.services.test_activity_contract import _raw_pdu_python_semantic_errors

    source = '''
def pack_dsl(bhs, length):
    bhs[5:8] = length.to_bytes(3, "big")

def unpack_dsl(response):
    return int.from_bytes(response[5:8], "big")

def case_session_reinstatement(sess, args):
    first = sess.send_login([])
    sess.close()
    replacement = sess.related(isid=sess.isid, tsih=0)
    return replacement.send_login([])
'''

    errors = _raw_pdu_python_semantic_errors(source, ast.parse(source))

    assert any("reinstatement" in error and "旧连接" in error for error in errors), errors


def test_raw_pdu_static_analysis_rejects_mcs_append_without_dual_connection_oracle():
    import ast

    from app.services.test_activity_contract import _raw_pdu_python_semantic_errors

    source = '''
def pack_dsl(bhs, length):
    bhs[5:8] = length.to_bytes(3, "big")

def unpack_dsl(response):
    return int.from_bytes(response[5:8], "big")

def case_mcs_append_connection(sess, args):
    first = sess.send_login([])
    tsih = first.get("tsih")
    second = sess.related(tsih=tsih, cid=sess.cid + 1)
    response = second.send_login([])
    expect_full_feature(response)
'''

    errors = _raw_pdu_python_semantic_errors(source, ast.parse(source))

    assert any("MCS append" in error and "同时存活" in error for error in errors), errors


def test_complete_delivery_rejects_harness_mapping_without_registered_case():
    from app.services.test_activity_contract import _audit_combined_execution_contract

    content = '''
```python
CASES = {"known-case": run_known_case}
```
```json
[{"case_id":"BB_001","mapped_test_dir":"ai_suggested_unverified: add Raw-PDU Harness positive-chap"}]
```
'''

    issues = _audit_combined_execution_contract(content)

    assert any(issue["code"] == "harness_case_not_registered" for issue in issues), issues


def test_protocol_observer_accepts_multiline_tshark_command(tmp_path):
    from app.services.test_activity_contract import (
        _audit_combined_professional_completeness,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整流程 SFMEA 黑盒测试设计",
        repo_path=str(repo),
    )
    content = (
        "T+C 非法组合、非法 NSG、Unsupported Version、NotUnderstood、Target Not Found、"
        "Target Removed、Authorization Failure、Redirect、SendTargets、login_timer 注销未重新注册。\n"
        "tcpdump -i eth0 -s 0 -w /tmp/iscsi-login.pcap 'tcp port 3260'\n"
        "tshark -r /tmp/iscsi-login.pcap \\\n"
        "  -Y 'iscsi.opcode == 0x23' \\\n"
        "  -T fields -e iscsi.login_transit -e iscsi.login_nsg\n"
    )

    issues = _audit_combined_professional_completeness(content, contract)

    assert not any(issue["code"] == "non_executable_protocol_observer" for issue in issues), issues


def test_iscsi_constraints_reject_unconfirmed_raw_device_placeholder(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login IO 黑盒测试",
        repo_path=str(repo),
    )
    audit = audit_test_activity_response(
        content=(
            "前置条件：iSCSI 设备已出现。步骤：fio --filename=/dev/sdX --rw=read --size=1M。"
            "观测：/dev/sdX 可读写。"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    assert any(
        issue.get("constraint_id") == "black_box_raw_device_identity"
        for issue in audit["issues"]
    ), audit


def test_black_box_audit_rejects_destructive_raw_device_write(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_artifacts,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text("int login;\n", encoding="utf-8")
    (repo / "test" / "iscsi_tgt" / "login.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    case = {
        "case_id": "unsafe-raw-write",
        "test_dimension": "normal_path",
        "scenario_name": "验证登录后 IO",
        "preconditions": ["已连接裸设备"],
        "steps": ["执行 dd if=/dev/zero of=/dev/sdX bs=512 count=1"],
        "expected_result": "写入成功",
        "observability": ["dd 退出码"],
        "failure_diagnostics": ["检查日志"],
        "mapped_test_dir": "test/iscsi_tgt/login.sh",
        "source_or_test_evidence": "lib/iscsi/iscsi.c",
    }
    (artifact_dir / "black_box_cases.json").write_text(json.dumps([case]), encoding="utf-8")
    contract = build_test_activity_contract(
        target="iSCSI Login 黑盒测试",
        repo_path=str(repo),
        workflow_outputs=[{"artifact": "black_box_cases.json"}],
    )

    audit = audit_test_activity_artifacts(
        artifact_dir=artifact_dir,
        contract=contract,
        repo_path=str(repo),
    )

    assert any(issue["code"] == "unsafe_destructive_test_step" for issue in audit["issues"])


def test_iscsi_professional_constraints_accept_verified_state_machine_facts(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login CHAP 状态机、超时和 Discovery 黑盒测试",
        repo_path=str(repo),
    )
    audit = audit_test_activity_response(
        content=(
            "CHAP challenge/response 只在 CSG=0 Security Negotiation 由 iscsi_auth_params 执行；"
            "CSG=1 是 Operational Negotiation，CSG=3 Login Request 会被拒绝并返回 Initiator Error；"
            "CSG=1 跳过 Security Negotiation 且 require_chap 时会被拒绝；"
            "最终 Login Response 使用 CSG=1、NSG=3、T=1。\n"
            "未知但格式合法的 key 返回 NotUnderstood，不应笼统写成解析失败。\n"
            "iscsi_pdu_payload_op_login 在首个 Login payload 注销 login_timer，且多阶段登录未重新注册；"
            "因此首 PDU 后的 30 秒清理不受当前定时器保证，必须标为待验证风险。\n"
            "Discovery session 没有 target，响应不会追加 TargetAddress。\n"
            "iscsi_get_active_conns 是内部函数，黑盒不直接调用，改用日志、RPC 和 initiator 结果。\n"
            "IO 使用明确创建并已确认的隔离测试设备，不对宿主裸盘执行 dd。"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    assert not any(issue["code"] == "professional_fact_conflict" for issue in audit["issues"]), audit


def test_explicit_iscsi_subject_does_not_expand_generic_test_dimensions_into_unrelated_profiles(tmp_path):
    from app.services.test_activity_contract import build_test_activity_contract

    contract = build_test_activity_contract(
        target=(
            "基于 SPDK iSCSI Login 输出代码流程、SFMEA 和黑盒用例，覆盖 resource_pressure、"
            "timeout、reconnect、concurrency、recovery、performance 和 IO 观测。"
        ),
        repo_path=str(tmp_path),
    )

    assert contract["domain_profiles"] == ["iscsi_login"]
    assert contract["project_profile"]["source_roots"] == ["lib/iscsi", "lib/iscsi/iscsi.c"]


def test_combined_response_accepts_complete_fenced_json_sfmea(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text("int login;\n", encoding="utf-8")
    (repo / "test" / "iscsi_tgt" / "login.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    contract = build_test_activity_contract(
        target="iSCSI Login SFMEA",
        repo_path=str(repo),
        workflow_outputs=[{"artifact": "sfmea.json"}],
        user_requirements="输出 SFMEA",
    )
    rows = [
        {
            "failure_mode": f"failure-{index}", "cause": "cause", "effect": "effect",
            "detection": "log", "severity": 7, "occurrence": 2, "detection_score": 3,
            "rpn": 42, "score_explanation": "scored", "mitigation": "test",
            "source_evidence": "lib/iscsi/iscsi.c", "test_mapping": "test/iscsi_tgt/login.sh",
        }
        for index in range(2)
    ]

    audit = audit_test_activity_response(
        content=(
            "## SFMEA\n\n```json\n" + json.dumps(rows, ensure_ascii=False) + "\n```\n\n"
            "## 流程\n\n1. 输入。\n2. 执行。\n3. 观测。\n"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    assert not any(issue["code"] == "missing_combined_sfmea" for issue in audit["issues"])


def test_combined_response_ignores_glob_when_concrete_evidence_exists(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "test" / "iscsi_tgt" / "chap").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text("int login_probe;\n", encoding="utf-8")
    (repo / "test" / "iscsi_tgt" / "chap" / "chap.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    contract = build_test_activity_contract(
        target="iSCSI Login 测试设计",
        repo_path=str(repo),
        user_requirements="输出可下载测试设计文件",
    )

    audit = audit_test_activity_response(
        content=(
            "## 代码证据\n\n"
            "实现见 `lib/iscsi/iscsi.c`，具体回归入口见 "
            "`test/iscsi_tgt/chap/chap.sh`；同类脚本可概括为 "
            "`test/iscsi_tgt/chap/*.sh`。"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    assert not any(
        issue["code"] == "evidence_path_not_found"
        for issue in audit["issues"]
    )


def test_combined_response_allows_labeled_unverified_proposed_test_path(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text("int login_probe;\n", encoding="utf-8")
    contract = build_test_activity_contract(
        target="iSCSI Login 性能测试设计",
        repo_path=str(repo),
    )

    audit = audit_test_activity_response(
        content=(
            "源码证据：`lib/iscsi/iscsi.c`。\n"
            "test_mapping: ai_suggested_unverified: add "
            "test/iscsi_tgt/login_latency/login_latency_harness.sh\n"
            "已删除 test/iscsi_tgt/login_latency/obsolete_harness.sh 作为证据或 test_mapping 的引用。\n"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    assert not any(
        issue["code"] == "evidence_path_not_found"
        and (
            "login_latency_harness.sh" in issue["message"]
            or "obsolete_harness.sh" in issue["message"]
        )
        for issue in audit["issues"]
    ), audit


def test_professional_constraint_does_not_join_separate_bullets(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 测试设计",
        repo_path=str(repo),
    )

    audit = audit_test_activity_response(
        content=(
            "- 授权失败：`0x02/0x02` authorization failure。\n"
            "- 无资源：`0x03/0x02` target error/no resources。"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    assert not any(
        issue["code"] == "professional_fact_conflict"
        for issue in audit["issues"]
    )


def test_runtime_generated_observation_path_is_not_repository_evidence(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    (repo / "test" / "iscsi_tgt" / "perf").mkdir(parents=True)
    (repo / "test" / "iscsi_tgt" / "perf" / "iscsi_initiator.sh").write_text(
        "iscsi_fio_results=perf_output/iscsi_fio.json\n",
        encoding="utf-8",
    )
    contract = build_test_activity_contract(
        target="iSCSI Login 性能测试设计",
        repo_path=str(repo),
    )

    audit = audit_test_activity_response(
        content=(
            "具体测试映射：`test/iscsi_tgt/perf/iscsi_initiator.sh`。\n"
            "运行后观测产物：`test/iscsi_tgt/perf/perf_output/iscsi_fio.json`。"
        ),
        contract=contract,
        repo_path=str(repo),
    )

    assert not any(
        issue["code"] == "evidence_path_not_found"
        for issue in audit["issues"]
    )


def test_repo_path_exists_accepts_declared_test_binary_build_target(tmp_path):
    from app.services.test_activity_contract import (
        _repo_path_exists,
        audit_test_activity_response,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    target_dir = repo / "test" / "nvme" / "connect_stress"
    target_dir.mkdir(parents=True)
    (target_dir / "connect_stress.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    (target_dir / "Makefile").write_text("SPDK_ROOT_DIR := ../../..\n", encoding="utf-8")

    assert _repo_path_exists(repo, "test/nvme/connect_stress/connect_stress") is True
    assert _repo_path_exists(repo, "test/nvme/connect_stress/imaginary_binary") is False
    audit = audit_test_activity_response(
        content=(
            "源码证据：`test/nvme/connect_stress/connect_stress.c`。\n"
            "外部运行目标：`test/nvme/connect_stress/connect_stress`。"
        ),
        contract=build_test_activity_contract(
            target="NVMe connect stress 测试设计",
            repo_path=str(repo),
        ),
        repo_path=str(repo),
    )
    assert not any(
        issue["code"] == "evidence_path_not_found"
        and "test/nvme/connect_stress/connect_stress" in issue["message"]
        for issue in audit["issues"]
    )


def test_workbench_runner_classifies_unhelpful_agent_greeting(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
    from app.services.workflow_dsl import WorkflowStore

    script = tmp_path / "hello_agent.py"
    script.write_text("print('你好，有什么需要帮助？')\n", encoding="utf-8")
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "hello-agent", "command": f"python {script}"}
    ])
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "hello_agent_workflow",
        "name": "Hello agent workflow",
        "version": 1,
        "inputs": [{"id": "analysis_object", "type": "free_text"}],
        "steps": [
            {
                "id": "design",
                "type": "agent_task",
                "provider": "hello-agent",
                "required_artifacts": ["sfmea.json"],
            }
        ],
        "outputs": [{"id": "sfmea", "type": "json", "from": "design", "artifact": "sfmea.json"}],
    })
    task_run = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="hello_agent_workflow",
        workspace_id="ws-hello",
        repo_path=str(tmp_path),
        inputs={"analysis_object": "iSCSI login SFMEA"},
    )

    result = WorkbenchWorkflowRunner(tmp_path / "task_runs").execute_task_run(
        task_run.task_run_id,
        timeout_sec=10,
    )

    recovery = result.step_results[0]["failure_recovery"]
    assert recovery["failure_kind"] == "agent_unhelpful_output"
    assert recovery["user_message"] == "Agent 只返回了问候语，没有完成测试活动任务。"
    assert recovery["recommended_actions"][0] == (
        "从失败节点自动重试或切换执行器；CodeTalk 会保留完整任务契约并要求直接生成交付件。"
    )


def test_workbench_runner_classifies_agent_stopped_after_source_search(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.workbench_task_run import WorkbenchTaskRunPreparer
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
    from app.services.workflow_dsl import WorkflowStore

    script = tmp_path / "source_only_agent.py"
    script.write_text(
        "print('我已读取 lib/iscsi/iscsi.c 和 test/iscsi_tgt，接下来需要分析登录流程。')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "external_agent_custom_providers", [
        {"id": "source-only-agent", "command": f"python {script}"}
    ])
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow({
        "id": "source_only_workflow",
        "name": "Source only workflow",
        "version": 1,
        "inputs": [{"id": "analysis_object", "type": "free_text"}],
        "steps": [
            {
                "id": "design",
                "type": "agent_task",
                "provider": "source-only-agent",
                "required_artifacts": ["black_box_cases.json"],
            }
        ],
        "outputs": [
            {
                "id": "black_box_cases",
                "type": "json",
                "from": "design",
                "artifact": "black_box_cases.json",
            }
        ],
    })
    task_run = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="source_only_workflow",
        workspace_id="ws-source-only",
        repo_path=str(tmp_path),
        inputs={"analysis_object": "iSCSI login 黑盒测试用例"},
    )

    result = WorkbenchWorkflowRunner(tmp_path / "task_runs").execute_task_run(
        task_run.task_run_id,
        timeout_sec=10,
    )

    recovery = result.step_results[0]["failure_recovery"]
    assert recovery["failure_kind"] == "agent_stopped_after_source_search"
    assert recovery["user_message"] == "Agent 查了源码后提前停止，没有生成要求的交付件。"
    assert recovery["recommended_actions"][0] == (
        "从失败节点续跑，要求 Agent 复用已读源码并直接输出缺失的交付件。"
    )


def test_ai_thread_prompts_include_test_activity_contract_for_testing_work():
    from app.services.ai_conversations import _build_agent_prompt, _build_prompt

    conversation = {
        "id": "conv-1",
        "title": "SPDK",
        "workspace_id": "ws-spdk",
        "scope_type": "workspace",
        "scope_id": "ws-spdk",
        "initial_context": {"repo_path": "/Volumes/Media/dpdk/spdk"},
    }
    user_message = "针对 iSCSI login 输出 SFMEA 和黑盒测试用例"
    builtin_prompt = _build_prompt(
        conversation=conversation,
        messages=[],
        references=[],
        user_message=user_message,
    )[0]["content"]
    agent_prompt = _build_agent_prompt(
        conversation=conversation,
        messages=[],
        references=[],
        user_message=user_message,
        runtime={"id": "claude", "name": "Claude"},
        repo_path="/Volumes/Media/dpdk/spdk",
    )

    for prompt in (builtin_prompt, agent_prompt):
        assert "TEST_ACTIVITY_CONTRACT" in prompt
        assert "iscsi_login" in prompt
        assert "sfmea.json" in prompt
        assert "black_box_cases" in prompt
        assert "ai_suggested_unverified" in prompt
        assert "不能自由决定交付件骨架" in prompt
        assert "test_mapping 必须指向具体存在的测试文件" in prompt
        assert "不能只写测试目录" in prompt
        assert "FINAL_FACT_CHECK" in prompt
        assert "iscsi_negotiate_params 使用 alloc_len" in prompt
        assert "首个 Login payload 开始处理时注销 login_timer" in prompt
        assert "未知但格式合法的登录参数通常在协商响应中返回 NotUnderstood" in prompt
        assert "fragmented C-bit parameter assembly" in prompt
    assert agent_prompt.rfind("FINAL_FACT_CHECK") < agent_prompt.rfind("用户问题：")


def test_builtin_prompt_rehydrates_artifact_and_deduplicates_current_request(tmp_path, monkeypatch):
    from app.services import ai_conversations

    artifact_path = tmp_path / "conv-1" / "run-old.md"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text(
        "# 旧产物\n\n## 黑盒测试用例\nFULL_BUILTIN_ARTIFACT_MARKER\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ai_conversations,
        "ai_thread_artifact_path",
        lambda conversation_id, run_id: artifact_path,
    )
    current_request = "重新生成完整可下载的 iSCSI Login 测试设计"
    messages = [
        {"role": "user", "content": "生成 iSCSI Login 测试设计"},
        {
            "role": "assistant",
            "content": "已生成结构化产物，请下载完整产物。",
            "conversation_id": "conv-1",
            "run_id": "run-old",
            "actions": [{"id": "download_run_artifact"}],
        },
        {"role": "user", "content": current_request},
    ]

    prompt = ai_conversations._build_prompt(
        conversation={
            "id": "conv-1",
            "title": "SPDK",
            "workspace_id": "ws-spdk",
            "scope_type": "workspace",
            "scope_id": "ws-spdk",
            "initial_context": {"repo_path": "/Volumes/Media/dpdk/spdk"},
        },
        messages=messages,
        references=[],
        user_message=current_request,
    )

    assert sum(item["content"] == current_request for item in prompt) == 1
    assert "历史助手完整下载产物" in prompt[2]["content"]
    assert "FULL_BUILTIN_ARTIFACT_MARKER" in prompt[2]["content"]
    assert "本轮必须重新输出完整正文" in prompt[0]["content"]


@pytest.mark.asyncio
async def test_ai_thread_delivery_adds_test_activity_task_card_action(tmp_path, monkeypatch):
    from app.services import ai_conversations

    monkeypatch.setattr(
        ai_conversations,
        "ai_thread_artifact_path",
        lambda conversation_id, run_id: tmp_path / conversation_id / f"{run_id}.md",
    )
    content, actions = await ai_conversations._prepare_assistant_delivery(
        run_id="run_123",
        conversation={
            "id": "conv-test-activity",
            "title": "SPDK 测试设计",
            "workspace_id": "ws-spdk",
            "initial_context": {"repo_path": "/Volumes/Media/dpdk/spdk"},
        },
        content="请针对 iSCSI login 输出 SFMEA 和黑盒测试用例。",
        user_message="针对 iSCSI login 输出 SFMEA 和黑盒测试用例",
        force_artifact=True,
    )

    task_card = next(action for action in actions if action.get("id") == "test_activity_task_card")
    assert "下载完整产物" in json.dumps(actions, ensure_ascii=False)
    assert "完整测试设计" in content
    assert task_card["kind"] == "test_activity"
    assert task_card["label"] == "测试活动任务卡"
    assert task_card["workflow_template_id"] == "source_flow_sfmea_blackbox"
    assert task_card["workspace_id"] == "ws-spdk"
    assert task_card["target"] == "针对 iSCSI login 输出 SFMEA 和黑盒测试用例"
    assert "iscsi_login" in task_card["domain_profiles"]
    assert "sfmea.json" in task_card["recommended_outputs"]
    assert "black_box_cases.json" in task_card["recommended_outputs"]
    assert task_card["evidence_policy"]["source_first"] is True
    assert task_card["test_activity_contract"]["target"] == "针对 iSCSI login 输出 SFMEA 和黑盒测试用例"
    assert "iscsi_login" in task_card["test_activity_contract"]["domain_profiles"]
    assert task_card["test_activity_contract"]["project_profile"]["project"] == "spdk"
    assert task_card["test_activity_contract"]["evidence_policy"]["prefer_artifacts"] == ["GitNexus", "CGC"]
    assert task_card["artifact_contract"]["sfmea.json"]["preview"] == "table"
    assert "score_explanation" in task_card["artifact_contract"]["sfmea.json"]["required_fields"]
    assert "source_or_test_evidence" in task_card["artifact_contract"]["black_box_cases.json"]["required_fields"]
    parsed_href = urlparse(task_card["href"])
    query = parse_qs(parsed_href.query)
    assert parsed_href.path == "/workbench"
    assert query["workflow"] == ["source_flow_sfmea_blackbox"]
    assert query["workspace_id"] == ["ws-spdk"]
    assert query["target"] == ["针对 iSCSI login 输出 SFMEA 和黑盒测试用例"]
    assert query["outputs"] == ["sfmea.json,black_box_cases.json"]


def test_markdown_audit_blocks_missing_required_evidence_terms_and_forbidden_paths(
    tmp_path,
):
    from app.services.test_activity_contract import audit_test_activity_artifacts

    repo = tmp_path / "repo"
    artifact_dir = tmp_path / "artifacts"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "test" / "nvmf").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text("login\n", encoding="utf-8")
    (repo / "test" / "nvmf" / "digest.sh").write_text("digest\n", encoding="utf-8")
    artifact_dir.mkdir()
    (artifact_dir / "report.md").write_text(
        "# Report\n\nlib/iscsi/iscsi.c\n\ntest/nvmf/digest.sh\n\n默认 60s 后超时。\n",
        encoding="utf-8",
    )
    contract = {
        "quality_gates": {"min_score": 80},
        "artifact_contract": {
            "report.md": {
                "required_evidence_terms": ["iscsi_auth_params", "ISCSI_LOGIN_TIMEOUT"],
                "forbidden_evidence_path_prefixes": ["test/nvmf/"],
                "forbidden_claim_terms": ["默认 60s"],
            }
        },
    }

    audit = audit_test_activity_artifacts(
        artifact_dir=artifact_dir,
        contract=contract,
        repo_path=str(repo),
    )

    assert audit["deliverable"] is False
    assert {item["code"] for item in audit["issues"]}.issuperset(
        {
            "missing_required_evidence_terms",
            "forbidden_evidence_path",
            "forbidden_claim_term",
        }
    )


def test_combined_markdown_artifact_audit_surfaces_legacy_professional_fact_lint(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_artifacts,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    artifact_dir = tmp_path / "artifacts"
    (repo / "lib" / "iscsi").mkdir(parents=True)
    (repo / "test" / "iscsi_tgt").mkdir(parents=True)
    (repo / "lib" / "iscsi" / "iscsi.c").write_text("login_timer\n", encoding="utf-8")
    (repo / "test" / "iscsi_tgt" / "login.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    artifact_dir.mkdir()
    (artifact_dir / "report.md").write_text(
        "# iSCSI Login 测试分析报告\n\n"
        "## 分析范围与证据缺口\nlib/iscsi/iscsi.c 与 test/iscsi_tgt/login.sh。\n\n"
        "## 关键源码证据\n多阶段 CHAP 停滞会在 30 秒后由 login_timer 断开连接。\n\n"
        "## 主流程与异常/恢复流程\n1. 接收 Login PDU。\n\n"
        "## SFMEA\n| ID | 失效模式 | 原因 | 影响 | 检测 | S | O | D | RPN | 缓解 | 证据 | 测试映射 |\n"
        "|---|---|---|---|---|---:|---:|---:|---:|---|---|---|\n"
        + "\n".join(
            f"| SFMEA-{index:02d} | 登录停滞 | 丢包 | 连接占用 | 监控 | 8 | 4 | 4 | 128 | 补充测试 | lib/iscsi/iscsi.c | test/iscsi_tgt/login.sh |"
            for index in range(1, 13)
        )
        + "\n\n## 黑盒测试用例\n"
        + "\n".join(
            f"### BB-{index:02d}\n前置条件：目标端运行。步骤：发送请求。预期结果：返回响应。观测点：抓包。失败诊断：检查日志。"
            for index in range(1, 13)
        ),
        encoding="utf-8",
    )
    contract = build_test_activity_contract(
        target="iSCSI Login 完整流程 SFMEA 黑盒测试设计",
        repo_path=str(repo),
        workflow_outputs=[
            {"artifact": "report.md", "type": "combined_test_report"},
        ],
    )

    audit = audit_test_activity_artifacts(
        artifact_dir=artifact_dir,
        contract=contract,
        repo_path=str(repo),
    )

    assert any(
        issue.get("code") == "professional_fact_conflict"
        and issue.get("constraint_id") == "iscsi_login_timer_after_first_pdu"
        for issue in audit["lint_warnings"]
    ), audit
    assert not any(
        issue.get("code") == "missing_iscsi_professional_scenarios"
        for issue in audit["issues"]
    ), audit
    assert any(
        issue.get("code") == "missing_iscsi_professional_scenarios"
        for issue in audit["lint_warnings"]
    ), audit


def test_combined_report_routes_nested_black_box_conflict_to_structured_artifact(tmp_path):
    from app.services.test_activity_contract import (
        _audit_professional_constraints,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="iSCSI Login 完整流程 SFMEA 黑盒测试设计",
        repo_path=str(repo),
    )
    content = """
# iSCSI Login 测试分析报告

## 黑盒测试用例

### BB-NEW01 未知合法 key=NotUnderstood

- 预期结果：target 应返回 NotUnderstood，不拒绝登录；最终登录成功进入 Operational Negotiation（CSG=1）。
"""

    issues = _audit_professional_constraints(
        content,
        contract,
        source_artifact="report.md",
        infer_structured_section=True,
    )

    issue = next(
        item
        for item in issues
        if item.get("constraint_id") == "iscsi_final_login_stage_alternatives"
    )
    assert issue["artifact"] == "black_box_cases.json"
    assert issue["section_heading"] == "BB-NEW01 未知合法 key=NotUnderstood"
    assert "最终登录成功进入 Operational Negotiation（CSG=1）" in issue["conflicting_excerpt"]


def test_combined_iscsi_report_rejects_normal_login_with_auth_failure_expected_result():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## 黑盒测试用例
### TC-01 Normal login without authentication
- 前置条件：Target 未启用认证。
- 操作步骤：使用合法 initiator 登录。
- 预期结果：Target fails to decode and returns Authentication Failure (0x0201).
- 观测点：tcpdump 与 target 日志。
"""

    issues = _audit_combined_report_consistency(content)

    assert any(issue["code"] == "black_box_expected_result_contradiction" for issue in issues)


def test_combined_iscsi_report_rejects_first_pdu_timer_claim_even_with_global_correction():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## 主流程与异常/恢复流程
源码证据表明首个 payload 处理时 login_timer 已注销，当前实现未重新注册。

## 黑盒测试用例
### TC-17 Login timeout after first PDU
- 前置条件：Target running；Initiator sends initial Login Request but then stops communication
- 操作步骤：Send Login Request with C=0 (no continuation) but do not send any more data；Wait 30 seconds (ISCSI_LOGIN_TIMEOUT)
- 预期结果：Target closes the connection after 30 seconds; login_timer fires.
- 观测点：tcpdump FIN/RST。
"""

    issues = _audit_combined_report_consistency(content)

    assert any(issue["code"] == "black_box_evidence_contradiction" for issue in issues)


def test_combined_iscsi_report_rejects_chinese_first_pdu_timer_oracle_from_real_output():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## 主流程与异常/恢复流程
源码证据表明 iscsi_pdu_payload_op_login 在首个 payload 处理时注销 login_timer，当前实现未重新注册。

## 黑盒测试用例
### BB-13 登录过程中 initiator 停滞，超时 30 秒后连接关闭
- 前置条件：自定义脚本发送第一个 Login PDU 后不继续发送。
- 操作步骤：发送一个 Login PDU；等待 35 秒（超过 30 秒定时器）。
- 预期结果：35 秒后，target 主动关闭 TCP 连接（收到 FIN 或 RST）。
- 证据：lib/iscsi/iscsi.c:2218（spdk_poller_unregister 在首 PDU 后取消定时器）。
"""

    issues = _audit_combined_report_consistency(content)

    assert any(
        issue["code"] == "black_box_evidence_contradiction"
        and issue.get("constraint_id") == "iscsi_login_timer_after_first_pdu"
        for issue in issues
    ), issues


def test_combined_iscsi_report_rejects_non_executable_mcs_client_and_wrong_test_mapping():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## 黑盒测试用例
### BB-18 MCS 容量超限
- 前置条件：scripts/rpc.py iscsi_set_options -c 1；首连接保持在线并记录非零 TSIH。
- 操作步骤：使用同一 initiator 不同 CID 建立第二个连接：iscsiadm -m node --login -T TARGET_IQN --cid 1。
- 预期结果：返回 Too Many Connections (0x06)。
- 测试映射：test/iscsi_tgt/multiconnection/multiconnection.sh（危险；仅使用隔离测试盘并提示数据销毁风险）。
"""

    issues = _audit_combined_report_consistency(content)
    codes = {issue["code"] for issue in issues}

    assert "non_executable_mcs_client" in codes, issues
    assert "black_box_test_mapping_contradiction" in codes, issues


def test_combined_iscsi_report_rejects_natural_language_iscsiadm_cid_from_real_output():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## 黑盒测试用例
### TC-22 超过 MaxConnections 时拒绝额外连接
- 前置条件：启动 target 前执行 scripts/rpc.py iscsi_set_options -c 1；建立一个正常登录会话；然后尝试使用相同 TSIH 但不同 CID 登录
- 操作步骤：第一个连接使用 iscsiadm 正常登录（CID=0）；第二个连接使用 iscsiadm 指定 CID=1 同一 Target；观察第二个连接是否被拒绝
- 预期结果：第二个连接应被返回 Too Many Connections (status-detail=0x06)
- 测试映射：test/iscsi_tgt/multiconnection/multiconnection.sh（仅限隔离测试盘，数据销毁风险）
"""

    issues = _audit_combined_report_consistency(content)
    codes = {issue["code"] for issue in issues}

    assert "non_executable_mcs_client" in codes, issues
    assert "black_box_test_mapping_contradiction" in codes, issues


def test_combined_iscsi_report_rejects_mcs_case_without_capable_client_from_run33():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## 黑盒测试用例
### TC-16 MCS 超限 (MaxConnectionsPerSession=1)
- 前置条件：在 target 启动前执行 scripts/rpc.py iscsi_set_options -c 1；已有一条连接进入 Full Feature Phase（TSIH 已知）
- 操作步骤：使用相同 ISID、不同 CID 发起第二条登录；等待 Login Response
- 预期结果：第二条登录被拒绝，status=0x06 (Too Many Connections)
- 测试映射：test/iscsi_tgt/multiconnection/multiconnection.sh（仅限隔离盘，提示数据销毁风险）
"""

    issues = _audit_combined_report_consistency(content)
    codes = {issue["code"] for issue in issues}

    assert "missing_mcs_capable_client" in codes, issues
    assert "black_box_test_mapping_contradiction" in codes, issues


def test_combined_iscsi_report_accepts_capable_mcs_raw_pdu_client():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## 黑盒测试用例
### TC-16 MCS 超限 (MaxConnectionsPerSession=1)
- 前置条件：首连接保持在线，记录服务端返回的非零 TSIH 与 CID=0。
- 操作步骤：运行可执行 Python raw-PDU harness，在保持旧 socket 在线的同时复用相同 ISID/非零 TSIH，以 CID=1 发送第二个 Login Request；harness 通过 socket.sendall/recv 解析 Login Response。
- 预期结果：第二条登录被拒绝，status=0x06；旧 socket 继续可用。
- 观测点：iscsi_get_connections 仅保留旧 CID；pcap 显示同一 TSIH 的新 CID 被拒绝。
- 测试映射：需要新增 mcs_raw_pdu.py；multiconnection.sh 不覆盖同一 session 的 MCS，仅作环境搭建参考。
"""

    issues = _audit_combined_report_consistency(content)

    assert not any(
        issue.get("constraint_id") in {
            "iscsi_multiconnection_client_capability",
            "iscsi_multiconnection_mapping_scope",
        }
        for issue in issues
    ), issues


def test_combined_iscsi_report_accepts_run48_recorded_tsih_mcs_harness():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## 黑盒测试用例
### BB-017 MCS 容量超限触发 Too Many Connections
- 测试维度：resource_pressure
- 前置条件：target 启动前执行：scripts/rpc.py iscsi_set_options -c 1 (MaxConnections=1)；提供 raw_iscsi_harness.py（见附录），支持设置 ISID/CID/TSIH 和 socket sendall/recv；仅限隔离测试盘，有数据销毁风险
- 操作步骤：使用 harness 发送首 Login PDU：ISID=0x0001, CID=0x0001, TSIH=0x0000，接收成功响应，记录 TSIH；保持首连接 socket 在线，创建新 socket，发送第二 Login PDU：ISID=0x0001, CID=0x0002, TSIH=<记录值>；接收第二 Login Response，检查 opcode=0x23, status_class=0x02, status_detail=0x06
- 预期结果：第二 Login Response opcode=0x23, status_class=0x02 (Initiator Error), status_detail=0x06 (Too Many Connections)
- 观测点：抓包：sudo tcpdump -i any -w mcs_exceed.pcap port 3260；tshark -r mcs_exceed.pcap -Y 'iscsi.opcode==0x23 and iscsi.status_class==2 and iscsi.status_detail==6'
- 失败诊断：若第二连接成功，检查 iscsi_set_options 是否生效；若首连接被关闭，检查 TSIH 传递是否正确
- 测试映射：lib/iscsi/iscsi.c
"""

    issues = _audit_combined_report_consistency(content)

    assert not any(
        issue.get("constraint_id") == "iscsi_multiconnection_client_capability"
        for issue in issues
    ), issues


def test_combined_iscsi_report_accepts_run49_explicit_nonzero_tsih_mcs_harness():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## 黑盒测试用例
### TC-01 Exceed MaxConnectionsPerSession (limit=1)
- 前置条件：SPDK target 启动前执行 scripts/rpc.py iscsi_set_options -c 1；raw-PDU harness 可用（见报告附录）；首连接已建立: 使用 ISID_A, CID=1, TSIH=100（通过 harness 登录成功）
- 操作步骤：使用 raw-PDU harness 创建第二个 TCP socket；构造 Login Request PDU，ISID=ISID_A, CID=2, TSIH=100；通过 harness 发送该 PDU并保持第一个 socket 在线；接收 Login Response 并断言 Status-Detail=0x06
- 预期结果：响应 opcode=0x23，Status-Class=0x02，Status-Detail=0x06（Too Many Connections）
- 测试映射：待新增: raw-PDU harness（参见报告附录）

### TC-02 Multiple connections per session (same TSIH, different CID)
- 前置条件：首连接已建立: 使用 ISID_A, CID=1, TSIH=100；raw-PDU harness 可用，保持首 socket 在线
- 操作步骤：使用 raw-PDU harness 创建第二个 TCP socket；构造 Login Request PDU，ISID=ISID_A, CID=2, TSIH=100；通过 harness 发送，接收响应
- 预期结果：响应 opcode=0x23，Status-Class=0x00，Status-Detail=0x00
- 测试映射：待新增: raw-PDU harness（参见报告附录）
"""

    issues = _audit_combined_report_consistency(content)

    assert not any(
        issue.get("constraint_id") == "iscsi_multiconnection_client_capability"
        for issue in issues
    ), issues


def test_combined_iscsi_report_rejects_run49_generic_harness_for_mcs_claim():
    from app.services.test_activity_contract import _audit_combined_professional_completeness

    content = r'''
## 黑盒测试用例
### TC-02 Multiple connections per session (same TSIH, different CID)
- 前置条件：首连接已建立，记录服务端返回的非零 TSIH，保持首 socket 在线。
- 操作步骤：复用相同 ISID 和 TSIH，以不同 CID 创建第二个连接并发送 Login Request。
- 预期结果：接收 Login Response，断言 Status-Class=0x00、Status-Detail=0x00。
- 测试映射：待新增 raw-PDU harness。

```python
import socket

def build_login_pdu(data: bytes, *, isid: bytes, cid: int, itt: int, cmdsn: int) -> bytes:
    bhs = bytearray(48)
    bhs[0] = 0x03
    bhs[1] = 0x87
    bhs[5:8] = len(data).to_bytes(3, "big")
    bhs[8:14] = isid
    bhs[14:16] = (0).to_bytes(2, "big")
    bhs[20:24] = itt.to_bytes(4, "big")
    bhs[24:26] = cid.to_bytes(2, "big")
    bhs[28:32] = cmdsn.to_bytes(4, "big")
    return bytes(bhs) + data

def recv_pdu(sock):
    bhs = sock.recv(48)
    data_segment_length = int.from_bytes(bhs[5:8], "big")
    return bhs, sock.recv(data_segment_length)

def run(host, port):
    request = build_login_pdu(b"AuthMethod=None\x00", isid=b"ABCDEF", cid=1, itt=2, cmdsn=3)
    with socket.create_connection((host, port)) as sock:
        sock.sendall(request)
        return recv_pdu(sock)
```
'''
    contract = {
        "domain_profiles": ["iscsi_login"],
        "target": "完整 iSCSI Login 流程、SFMEA 与黑盒测试设计",
        "required_outputs": ["business_flow.md", "sfmea.json", "black_box_cases.json"],
        "artifact_contract": {},
    }

    issues = _audit_combined_professional_completeness(content, contract)

    capability_issue = next(
        issue
        for issue in issues
        if issue["code"] == "raw_pdu_harness_missing_scenario_capability"
    )
    assert "MCS" in capability_issue["message"]
    assert set(capability_issue["missing_capabilities"]) >= {
        "nonzero_tsih_input",
        "dual_socket_lifecycle",
        "login_response_status_oracle",
    }


def test_combined_iscsi_report_rejects_fixed_flags_and_version_harness_for_claimed_cases():
    from app.services.test_activity_contract import _audit_raw_pdu_scenario_capabilities

    content = r'''
## 黑盒测试用例
### TC-10 T+C 非法组合与 C-bit 分片
- 操作步骤：先发送 C=1 的 Login Request，再发送结束分片；另发 T=1,C=1 非法组合。
- 预期结果：解析 Login Response 并断言拒绝状态。

### TC-11 Unsupported Version
- 操作步骤：设置 version_max=0xff、version_min=0xfe 发送 Login Request。
- 预期结果：解析 Login Response 并断言 Unsupported Version。

```python
import socket

def build_login_pdu(data: bytes) -> bytes:
    bhs = bytearray(48)
    bhs[0] = 0x03
    bhs[1] = 0x87
    bhs[2] = 0
    bhs[3] = 0
    bhs[5:8] = len(data).to_bytes(3, "big")
    return bytes(bhs) + data

def run(host, port):
    request = build_login_pdu(b"AuthMethod=None\x00")
    with socket.create_connection((host, port)) as sock:
        sock.sendall(request)
        return sock.recv(48)
```
'''

    issues = _audit_raw_pdu_scenario_capabilities(content)

    capability_issue = next(
        issue
        for issue in issues
        if issue["code"] == "raw_pdu_harness_missing_scenario_capability"
    )
    assert set(capability_issue["missing_capabilities"]) >= {
        "mutable_login_flags",
        "multi_pdu_login",
        "version_range_input",
        "login_response_status_oracle",
    }


def test_fact_ledger_contradicts_supported_iscsi_version_reported_as_unsupported(tmp_path):
    from app.services.test_activity_contract import audit_test_activity_artifacts
    from app.services.workflow_presets import SFMEA_SCHEMA

    repo = tmp_path / "repo"
    header = repo / "include" / "spdk" / "iscsi_spec.h"
    header.parent.mkdir(parents=True)
    header.write_text("#define ISCSI_VERSION 0x00\n", encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "evidence_cards.json").write_text(
        json.dumps(
            [
                {
                    "evidence_id": "SRC-001",
                    "file_path": "include/spdk/iscsi_spec.h",
                    "start_line": 1,
                    "end_line": 1,
                    "excerpt": "#define ISCSI_VERSION 0x00",
                    "sha256": hashlib.sha256(header.read_bytes()).hexdigest(),
                    "symbols": ["ISCSI_VERSION"],
                }
            ]
        ),
        encoding="utf-8",
    )
    (artifacts / "sfmea.json").write_text(
        json.dumps(
            [
                {
                    "sfmea_id": "SFMEA-003",
                    "failure_mode": "Unsupported version: version_max=0, version_min=0",
                    "cause": "Initiator sends both version fields as zero.",
                    "effect": "Target rejects the login as unsupported version.",
                    "detection": "Login Response Status-Detail=0x05.",
                    "severity": 6,
                    "occurrence": 2,
                    "detection_score": 2,
                    "rpn": 24,
                    "mitigation": "Send a supported version range.",
                    "source_evidence": [
                        "include/spdk/iscsi_spec.h::ISCSI_VERSION"
                    ],
                    "test_mapping": "待新增 raw-PDU harness",
                }
            ]
        ),
        encoding="utf-8",
    )
    contract = {
        "artifact_contract": {"sfmea.json": {"schema": SFMEA_SCHEMA}},
        "quality_gates": {
            "min_score": 80,
            "require_independent_behavior_validation": True,
        },
    }

    result = audit_test_activity_artifacts(
        artifact_dir=artifacts,
        contract=contract,
        repo_path=str(repo),
    )

    contradiction = next(
        issue for issue in result["issues"] if issue["code"] == "source_claim_contradicted"
    )
    assert contradiction["claim_id"] == "SFMEA-003:protocol_version_range"
    assert contradiction["source_truth"] == "ISCSI_VERSION=0x00"
    assert result["fact_verification"] == {
        "total": 2,
        "verified": 0,
        "contradicted": 1,
        "insufficient": 1,
        "pass_rate": 0,
    }


def test_fact_ledger_rejects_exact_log_literal_missing_from_verified_source(tmp_path):
    from app.services.test_activity_contract import audit_test_activity_artifacts
    from app.services.workflow_presets import SFMEA_SCHEMA

    repo = tmp_path / "repo"
    source = repo / "lib" / "iscsi" / "conn.c"
    source.parent.mkdir(parents=True)
    source.write_text('SPDK_ERRLOG("auth failed\\n");\n', encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "evidence_cards.json").write_text(
        json.dumps(
            [
                {
                    "evidence_id": "SRC-001",
                    "file_path": "lib/iscsi/conn.c",
                    "start_line": 1,
                    "end_line": 1,
                    "excerpt": 'SPDK_ERRLOG("auth failed\\n");',
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "symbols": ["login_timeout"],
                }
            ]
        ),
        encoding="utf-8",
    )
    row = {
        "sfmea_id": "SFMEA-005",
        "failure_mode": "Login timeout",
        "cause": "Initiator stops responding.",
        "effect": "Connection closes.",
        "detection": "SPDK log contains 'login timed out'.",
        "severity": 6,
        "occurrence": 2,
        "detection_score": 2,
        "rpn": 24,
        "mitigation": "Observe timeout handling.",
        "source_evidence": ["lib/iscsi/conn.c::login_timeout"],
        "test_mapping": "待新增 timeout test",
    }
    (artifacts / "sfmea.json").write_text(json.dumps([row]), encoding="utf-8")
    contract = {
        "artifact_contract": {"sfmea.json": {"schema": SFMEA_SCHEMA}},
        "quality_gates": {
            "min_score": 80,
            "require_independent_behavior_validation": True,
        },
    }

    result = audit_test_activity_artifacts(
        artifact_dir=artifacts,
        contract=contract,
        repo_path=str(repo),
    )

    issue = next(
        issue for issue in result["issues"] if issue["code"] == "source_claim_contradicted"
    )
    assert issue["claim_id"] == "SFMEA-005:log_literal:1"
    assert issue["claimed_literal"] == "login timed out"
    assert issue["row_id"] == "SFMEA-005"


def test_fact_ledger_resolves_exact_log_literal_from_verified_evidence_anchor(tmp_path):
    from app.services.test_activity_contract import audit_test_activity_artifacts
    from app.services.workflow_presets import SFMEA_SCHEMA

    repo = tmp_path / "repo"
    source = repo / "lib" / "nvme" / "fabrics.c"
    source.parent.mkdir(parents=True)
    source.write_text(
        'libnvme_msg(ctx, WARN, "registry update failed: %s\\n", err);\n',
        encoding="utf-8",
    )
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "evidence_cards.json").write_text(
        json.dumps(
            [
                {
                    "evidence_id": "SRC-01",
                    "file_path": "lib/nvme/fabrics.c",
                    "start_line": 1,
                    "end_line": 1,
                    "excerpt": source.read_text(encoding="utf-8").strip(),
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "symbols": ["libnvme_msg"],
                }
            ]
        ),
        encoding="utf-8",
    )
    row = {
        "sfmea_id": "SFMEA-006",
        "failure_mode": "若 registry 更新失败仅记录告警，调用链可能继续推进",
        "cause": "更新函数没有向调用方返回错误",
        "effect": "控制器状态与 registry 状态可能不一致",
        "detection": "日志原文包含 'registry update failed'。",
        "severity": 6,
        "occurrence": 2,
        "detection_score": 3,
        "rpn": 36,
        "score_explanation": "S6 O2 D3",
        "mitigation": "整改: 返回错误并回滚。验证: 注入 registry 写入失败并检查返回码。",
        "source_evidence": ["SRC-01:L1"],
        "test_mapping": "注入 registry 更新失败",
    }
    (artifacts / "sfmea.json").write_text(json.dumps([row]), encoding="utf-8")
    contract = {
        "artifact_contract": {"sfmea.json": {"schema": SFMEA_SCHEMA}},
        "quality_gates": {"min_score": 0},
    }

    result = audit_test_activity_artifacts(
        artifact_dir=artifacts,
        contract=contract,
        repo_path=str(repo),
    )

    log_claim = next(
        claim for claim in result["fact_claims"]
        if claim.get("type") == "log_literal"
    )
    assert log_claim["status"] == "verified"
    assert not any(
        issue.get("claim_id") == "SFMEA-006:log_literal:1"
        for issue in result["issues"]
    )


def test_fact_ledger_only_extracts_explicit_local_log_claims(tmp_path):
    from app.services.test_activity_contract import audit_test_activity_artifacts
    from app.services.workflow_presets import SFMEA_SCHEMA

    repo = tmp_path / "repo"
    source = repo / "lib" / "iscsi" / "iscsi.c"
    source.parent.mkdir(parents=True)
    source.write_text(
        'SPDK_ERRLOG("auth failed (name %.64s)\\n", name);\n',
        encoding="utf-8",
    )
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "evidence_cards.json").write_text(
        json.dumps(
            [
                {
                    "evidence_id": "SRC-001",
                    "file_path": "lib/iscsi/iscsi.c",
                    "start_line": 1,
                    "end_line": 1,
                    "excerpt": 'SPDK_ERRLOG("auth failed (name %.64s)\\n", name);',
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "symbols": ["iscsi_auth_params"],
                }
            ]
        ),
        encoding="utf-8",
    )
    row = {
        "sfmea_id": "SFMEA-006",
        "failure_mode": "Authentication failure",
        "cause": "Unknown account",
        "effect": "Login rejected",
        "detection": (
            "SPDK log shows 'auth failed (name ...)' (exact format string "
            "'auth failed (name %.64s)' at iscsi.c); 'unknown user' is not logged. "
            "No explicit SPDK log for 'login timeout' (待验证). "
            "Capture with tshark filter 'iscsi.status_class==0x02'."
        ),
        "severity": 6,
        "occurrence": 2,
        "detection_score": 2,
        "rpn": 24,
        "mitigation": "Verify the response and source-backed log.",
        "source_evidence": ["lib/iscsi/iscsi.c::iscsi_auth_params"],
        "test_mapping": "待新增 authentication test",
    }
    (artifacts / "sfmea.json").write_text(json.dumps([row]), encoding="utf-8")
    contract = {
        "artifact_contract": {"sfmea.json": {"schema": SFMEA_SCHEMA}},
        "quality_gates": {"min_score": 80},
    }

    result = audit_test_activity_artifacts(
        artifact_dir=artifacts,
        contract=contract,
        repo_path=str(repo),
    )

    assert result["fact_verification"] == {
        "total": 1,
        "verified": 1,
        "contradicted": 0,
        "insufficient": 0,
        "pass_rate": 100,
    }
    log_claim = next(
        claim for claim in result["fact_claims"]
        if claim.get("type") == "log_literal"
    )
    assert log_claim["statement"].endswith("auth failed (name %.64s)")


def test_fact_ledger_verifies_structured_claim_quotes_against_hashed_source(tmp_path):
    from app.services.test_activity_contract import audit_test_activity_artifacts
    from app.services.workflow_presets import SFMEA_SCHEMA

    repo = tmp_path / "repo"
    source = repo / "include" / "spdk" / "iscsi_spec.h"
    source.parent.mkdir(parents=True)
    source.write_text(
        "#define ISCSI_OP_LOGIN_RSP 0x23\n#define ISCSI_LOGIN_AUTHENT_FAIL 0x01\n",
        encoding="utf-8",
    )
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "evidence_cards.json").write_text(
        json.dumps(
            [
                {
                    "evidence_id": "SRC-001",
                    "file_path": "include/spdk/iscsi_spec.h",
                    "start_line": 1,
                    "end_line": 2,
                    "excerpt": source.read_text(encoding="utf-8"),
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "symbols": ["ISCSI_OP_LOGIN_RSP", "ISCSI_LOGIN_AUTHENT_FAIL"],
                }
            ]
        ),
        encoding="utf-8",
    )
    base_row = {
        "failure_mode": "Authentication failure",
        "cause": "Bad credentials",
        "effect": "Login rejected",
        "detection": "Inspect Login Response",
        "severity": 6,
        "occurrence": 2,
        "detection_score": 2,
        "rpn": 24,
        "mitigation": "Verify the response",
        "source_evidence": ["include/spdk/iscsi_spec.h::ISCSI_OP_LOGIN_RSP"],
        "test_mapping": "test/iscsi_tgt",
    }
    rows = [
        {
            **base_row,
            "sfmea_id": "SFMEA-001",
            "technical_claims": [
                {
                    "claim_id": "C-001",
                    "type": "protocol_constant",
                    "statement": "Login Response opcode is 0x23",
                    "evidence": [
                        {
                            "evidence_id": "SRC-001:L1",
                            "path": "include/spdk/iscsi_spec.h",
                            "symbol": "ISCSI_OP_LOGIN_RSP",
                            "lines": "L1-L1",
                            "quote": "#define ISCSI_OP_LOGIN_RSP 0x23",
                        }
                    ],
                }
            ],
        },
        {
            **base_row,
            "sfmea_id": "SFMEA-002",
            "technical_claims": [
                {
                    "claim_id": "C-002",
                    "type": "protocol_constant",
                    "statement": "Authentication Failure is 0x02",
                    "evidence": [
                        {
                            "evidence_id": "SRC-001:L2",
                            "path": "include/spdk/iscsi_spec.h",
                            "symbol": "ISCSI_LOGIN_AUTHENT_FAIL",
                            "lines": "L2-L2",
                            "quote": "#define ISCSI_LOGIN_AUTHENT_FAIL 0x02",
                        }
                    ],
                }
            ],
        },
        {
            **base_row,
            "sfmea_id": "SFMEA-003",
            "technical_claims": [
                {
                    "claim_id": "C-003",
                    "type": "protocol_constant",
                    "statement": "Login Response opcode is 0x23",
                    "evidence": [
                        {
                            "evidence_id": "UNKNOWN:L1",
                            "path": "include/spdk/iscsi_spec.h",
                            "symbol": "ISCSI_OP_LOGIN_RSP",
                            "lines": "L1",
                            "quote": "#define ISCSI_OP_LOGIN_RSP 0x23",
                        }
                    ],
                }
            ],
        },
        {
            **base_row,
            "sfmea_id": "SFMEA-004",
            "technical_claims": [
                {
                    "claim_id": "C-004",
                    "type": "protocol_constant",
                    "statement": "Login Response opcode is 0x24",
                    "evidence": [
                        {
                            "evidence_id": "SRC-001:L1",
                            "path": "include/spdk/iscsi_spec.h",
                            "symbol": "ISCSI_OP_LOGIN_RSP",
                            "lines": "L1-L1",
                            "quote": "#define ISCSI_OP_LOGIN_RSP 0x23",
                        }
                    ],
                }
            ],
        },
    ]
    (artifacts / "sfmea.json").write_text(json.dumps(rows), encoding="utf-8")
    contract = {
        "artifact_contract": {"sfmea.json": {"schema": SFMEA_SCHEMA}},
        "quality_gates": {"min_score": 80},
    }

    result = audit_test_activity_artifacts(
        artifact_dir=artifacts,
        contract=contract,
        repo_path=str(repo),
    )

    claims = {claim["claim_id"]: claim for claim in result["fact_claims"]}
    assert claims["C-001"]["status"] == "verified"
    assert claims["C-001"]["evidence"][0]["sha256"] == hashlib.sha256(
        source.read_bytes()
    ).hexdigest()
    assert claims["C-002"]["status"] == "contradicted"
    assert claims["C-003"]["status"] == "contradicted"
    assert claims["C-004"]["status"] == "contradicted"
    issue = next(
        issue
        for issue in result["issues"]
        if issue.get("claim_id") == "C-002"
    )
    assert issue["code"] == "source_claim_contradicted"
    assert issue["validation_layer"] == "L1_deterministic"


def test_source_behavior_claim_requires_bound_l2_validation(tmp_path):
    from app.services.test_activity_contract import audit_test_activity_artifacts
    from app.services.workflow_presets import SFMEA_SCHEMA

    repo = tmp_path / "repo"
    source = repo / "lib" / "iscsi" / "iscsi.c"
    source.parent.mkdir(parents=True)
    source.write_text(
        "int iscsi_auth_params(void *params)\n{\n\tif (params == NULL) { return -1; }\n}\n",
        encoding="utf-8",
    )
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    (artifacts / "evidence_cards.json").write_text(
        json.dumps(
            [
                {
                    "evidence_id": "SRC-001",
                    "file_path": "lib/iscsi/iscsi.c",
                    "start_line": 1,
                    "end_line": 4,
                    "excerpt": source.read_text(encoding="utf-8"),
                    "sha256": source_sha,
                    "symbols": ["iscsi_auth_params"],
                }
            ]
        ),
        encoding="utf-8",
    )
    row = {
        "sfmea_id": "SFMEA-001",
        "failure_mode": "NULL input is dereferenced",
        "cause": "iscsi_auth_params has no NULL guard",
        "effect": "Target crashes",
        "detection": "Observe a crash",
        "severity": 9,
        "occurrence": 2,
        "detection_score": 3,
        "rpn": 54,
        "mitigation": "Add a NULL guard",
        "source_evidence": ["lib/iscsi/iscsi.c::iscsi_auth_params"],
        "test_mapping": "new negative test",
        "technical_claims": [
            {
                "claim_id": "C-BEHAVIOR-001",
                "type": "source_behavior",
                "statement": "iscsi_auth_params has no NULL guard",
                "evidence": [
                    {
                        "evidence_id": "SRC-001:L3",
                        "path": "lib/iscsi/iscsi.c",
                        "symbol": "iscsi_auth_params",
                        "lines": "L3",
                        "quote": "\tif (params == NULL) { return -1; }",
                    }
                ],
            }
        ],
    }
    (artifacts / "sfmea.json").write_text(json.dumps([row]), encoding="utf-8")
    contract = {
        "artifact_contract": {"sfmea.json": {"schema": SFMEA_SCHEMA}},
        "quality_gates": {
            "min_score": 80,
            "require_independent_behavior_validation": True,
        },
    }

    result = audit_test_activity_artifacts(
        artifact_dir=artifacts,
        contract=contract,
        repo_path=str(repo),
    )

    explicit = next(
        claim for claim in result["fact_claims"]
        if claim["claim_id"] == "C-BEHAVIOR-001"
    )
    assert explicit["status"] == "insufficient"
    assert explicit["semantic_validation"] == "requires_l2"
    explicit_issue = next(
        issue for issue in result["issues"]
        if issue.get("claim_id") == "C-BEHAVIOR-001"
    )
    assert explicit_issue["row_id"] == "SFMEA-001"
    row_claim = next(
        claim for claim in result["fact_claims"]
        if claim["claim_id"] == "ROW:sfmea.json:SFMEA-001"
    )
    assert row_claim["status"] == "insufficient"
    assert row_claim["type"] == "row_source_claim_coverage"
    assert "C-BEHAVIOR-001" in row_claim["statement"]
    row_issue = next(
        issue for issue in result["issues"]
        if issue.get("claim_id") == "ROW:sfmea.json:SFMEA-001"
    )
    assert row_issue["row_id"] == "SFMEA-001"
    assert result["quality_axes"]["facts"]["status"] == "blocked"
    assert result["deliverable"] is False


def test_source_behavior_claim_accepts_only_digest_bound_independent_l2_verdict(tmp_path):
    from app.services.test_activity_contract import (
        _behavior_claim_binding,
        audit_test_activity_artifacts,
        build_behavior_claim_validation_request,
    )
    from app.services.workflow_presets import SFMEA_SCHEMA

    repo = tmp_path / "repo"
    source = repo / "lib" / "iscsi" / "iscsi.c"
    source.parent.mkdir(parents=True)
    source.write_text(
        "int iscsi_auth_params(void *params)\n{\n\tif (params == NULL) { return -1; }\n}\n",
        encoding="utf-8",
    )
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    evidence = {
        "evidence_id": "SRC-001:L3",
        "path": "lib/iscsi/iscsi.c",
        "symbol": "iscsi_auth_params",
        "lines": "L3",
        "quote": "if (params == NULL) { return -1; }",
    }
    (artifacts / "evidence_cards.json").write_text(
        json.dumps(
            [
                {
                    "evidence_id": "SRC-001",
                    "file_path": "lib/iscsi/iscsi.c",
                    "start_line": 1,
                    "end_line": 4,
                    "excerpt": source.read_text(encoding="utf-8"),
                    "sha256": source_sha,
                    "symbols": ["iscsi_auth_params"],
                }
            ]
        ),
        encoding="utf-8",
    )
    statement = "iscsi_auth_params rejects NULL input before dereference"
    row = {
        "sfmea_id": "SFMEA-001",
        "failure_mode": "NULL input",
        "cause": "Caller passes NULL",
        "effect": "Function rejects the request",
        "detection": "Return code is -1",
        "severity": 3,
        "occurrence": 2,
        "detection_score": 2,
        "rpn": 12,
        "mitigation": "Retain the guard",
        "source_evidence": ["lib/iscsi/iscsi.c::iscsi_auth_params"],
        "test_mapping": "new negative test",
        "technical_claims": [
            {
                "claim_id": "C-BEHAVIOR-001",
                "type": "source_behavior",
                "statement": statement,
                "evidence": [evidence],
            }
        ],
    }
    (artifacts / "sfmea.json").write_text(json.dumps([row]), encoding="utf-8")
    request = build_behavior_claim_validation_request(
        artifact_dir=artifacts,
        repo_path=repo,
    )
    assert {claim["claim_id"] for claim in request["claims"]} == {
        "C-BEHAVIOR-001",
    }
    limited_request = build_behavior_claim_validation_request(
        artifact_dir=artifacts,
        repo_path=repo,
        max_claims=1,
    )
    # Only explicit technical claims are sent to the independent L2 auditor;
    # aggregate row claims are derived after those verdicts return.
    assert limited_request["candidate_count"] == 1
    assert limited_request["requested_count"] == 1
    assert limited_request["truncated"] is False
    assert "if (params == NULL)" in request["contexts"][0]["content"]
    checked_evidence = [{**evidence, "sha256": source_sha}]
    binding = _behavior_claim_binding(
        claim_id="C-BEHAVIOR-001",
        claim_type="source_behavior",
        statement=statement,
        evidence=checked_evidence,
    )
    (artifacts / "behavior_claim_validation.json").write_text(
        json.dumps(
            {
                "kind": "behavior_claim_validation",
                "schema_version": 1,
                "validator": {
                    "provider": "codex",
                    "model": "gpt-5.6-sol",
                    "independent": True,
                },
                "claims": [
                    {
                        "claim_id": "C-BEHAVIOR-001",
                        "binding": binding,
                        "status": "supports",
                        "reason": "The referenced guard returns before dereference.",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    contract = {
        "artifact_contract": {"sfmea.json": {"schema": SFMEA_SCHEMA}},
        "quality_gates": {
            "min_score": 80,
            "require_independent_behavior_validation": True,
        },
    }

    result = audit_test_activity_artifacts(
        artifact_dir=artifacts,
        contract=contract,
        repo_path=str(repo),
    )

    explicit = next(
        claim for claim in result["fact_claims"]
        if claim["claim_id"] == "C-BEHAVIOR-001"
    )
    assert explicit["status"] == "verified"
    assert explicit["validation_layer"] == "L2_independent_behavior"
    row_claim = next(
        claim
        for claim in result["fact_claims"]
        if claim["claim_id"] == "ROW:sfmea.json:SFMEA-001"
    )
    assert row_claim["status"] == "verified"
    assert row_claim["validation_layer"] == "aggregate_source_claims"
    assert not [
        issue
        for issue in result["issues"]
        if issue.get("claim_id") == row_claim["claim_id"]
    ]

    validation = json.loads(
        (artifacts / "behavior_claim_validation.json").read_text(encoding="utf-8")
    )
    validation["claims"][0]["binding"] = "stale-binding"
    (artifacts / "behavior_claim_validation.json").write_text(
        json.dumps(validation), encoding="utf-8"
    )
    stale = audit_test_activity_artifacts(
        artifact_dir=artifacts,
        contract=contract,
        repo_path=str(repo),
    )
    stale_claim = next(
        claim for claim in stale["fact_claims"]
        if claim["claim_id"] == "C-BEHAVIOR-001"
    )
    assert stale_claim["status"] == "insufficient"


def test_behavior_validation_context_honors_plain_line_ranges(tmp_path):
    """Evidence cards persist ranges as `123-126`, not only `L123-L126`."""
    from app.services.test_activity_contract import build_behavior_claim_validation_request

    repo = tmp_path / "repo"
    source = repo / "src" / "connect.c"
    source.parent.mkdir(parents=True)
    lines = [f"/* filler {index} */" for index in range(1, 180)]
    lines[0] = "/* connect_target is declared below */"
    lines[119] = "int connect_target(void) {"
    lines[120] = "    return connect_real_target();"
    lines[121] = "}"
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    (artifacts / "evidence_cards.json").write_text(
        json.dumps(
            [
                {
                    "evidence_id": "SRC-001",
                    "file_path": "src/connect.c",
                    "start_line": 120,
                    "end_line": 122,
                    "excerpt": "\n".join(lines[119:122]),
                    "sha256": source_sha,
                    "symbols": ["connect_target"],
                }
            ]
        ),
        encoding="utf-8",
    )
    (artifacts / "sfmea.json").write_text(
        json.dumps(
            [
                {
                    "sfmea_id": "SFMEA-001",
                    "failure_mode": "target connect is rejected",
                    "cause": "connect call returns an error",
                    "effect": "controller is not created",
                    "detection": "public CLI exit status",
                    "severity": 6,
                    "occurrence": 2,
                    "detection_score": 2,
                    "rpn": 24,
                    "mitigation": "Return a clear error and add a negative test",
                    "source_evidence": ["src/connect.c::connect_target"],
                    "test_mapping": "new negative test",
                    "technical_claims": [
                        {
                            "claim_id": "C-PLAIN-RANGE",
                            "type": "source_behavior",
                            "statement": "connect_target delegates to connect_real_target",
                            "evidence": [
                                {
                                    "evidence_id": "SRC-001:L121",
                                    "path": "src/connect.c",
                                    "symbol": "connect_target",
                                    "lines": "120-122",
                                    "quote": "return connect_real_target();",
                                }
                            ],
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    request = build_behavior_claim_validation_request(
        artifact_dir=artifacts,
        repo_path=repo,
    )

    claim = next(
        item for item in request["claims"] if item["claim_id"] == "C-PLAIN-RANGE"
    )
    context = next(
        item
        for item in request["contexts"]
        if item["context_id"] == claim["context_ids"][0]
    )
    assert context["start_line"] <= 120 <= context["end_line"]
    assert "000121:     return connect_real_target();" in context["content"]
    assert claim["evidence_bindings"] == [
        {
            "path": "src/connect.c",
            "symbol": "connect_target",
            "lines": "120-122",
            "quote": "return connect_real_target();",
        }
    ]


def test_behavior_validation_excludes_black_box_test_contract_from_source_entailment(
    tmp_path,
):
    from app.services.test_activity_contract import build_behavior_claim_validation_request

    repo = tmp_path / "repo"
    source = repo / "src" / "connect.c"
    source.parent.mkdir(parents=True)
    source.write_text("int connect_target(void) { return 0; }\n", encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "evidence_cards.json").write_text(
        json.dumps(
            [
                {
                    "evidence_id": "SRC-001",
                    "file_path": "src/connect.c",
                    "start_line": 1,
                    "end_line": 1,
                    "excerpt": source.read_text(encoding="utf-8"),
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "symbols": ["connect_target"],
                }
            ]
        ),
        encoding="utf-8",
    )
    (artifacts / "black_box_cases.json").write_text(
        json.dumps(
            [
                {
                    "case_id": "BB-001",
                    "test_dimension": "timeout",
                    "scenario_name": "target unreachable timeout",
                    "preconditions": ["target address is unreachable"],
                    "steps": ["run the public connect command"],
                    "expected_result": "command fails without creating a controller",
                    "oracle_basis": "public CLI contract and the referenced source error path",
                    "observability": ["exit code", "controller list"],
                    "source_or_test_evidence": ["src/connect.c::connect_target"],
                }
            ]
        ),
        encoding="utf-8",
    )

    request = build_behavior_claim_validation_request(
        artifact_dir=artifacts,
        repo_path=repo,
    )

    assert request["claims"] == []


def test_sfmea_json_requires_twelve_risk_rows(tmp_path):
    from app.services.test_activity_contract import (
        _audit_json_artifact,
        build_test_activity_contract,
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    contract = build_test_activity_contract(
        target="NVMe-oF TCP connect SFMEA",
        repo_path=str(repo),
        workflow_outputs=[
            {
                "artifact": "sfmea.json",
                "type": "json",
                "min_sfmea_rows": 12,
            }
        ],
    )
    spec = contract["artifact_contract"]["sfmea.json"]

    issues = _audit_json_artifact(
        artifact="sfmea.json",
        payload=[{"sfmea_id": "SFMEA-001"}],
        spec=spec,
        repo=repo,
    )

    assert spec["min_sfmea_rows"] == 12
    assert any(issue["code"] == "insufficient_sfmea_rows" for issue in issues)


def test_structured_claim_evidence_must_belong_to_row_evidence(tmp_path):
    from app.services.test_activity_contract import audit_test_activity_artifacts

    repo = tmp_path / "repo"
    source_a = repo / "src" / "connect.c"
    source_b = repo / "test" / "cleanup.c"
    source_a.parent.mkdir(parents=True)
    source_b.parent.mkdir(parents=True)
    source_a.write_text("int connect_target(void) { return 0; }\n", encoding="utf-8")
    source_b.write_text("int cleanup_target(void) { return 0; }\n", encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    cards = []
    for evidence_id, path, source, symbol in (
        ("SRC-001", "src/connect.c", source_a, "connect_target"),
        ("SRC-002", "test/cleanup.c", source_b, "cleanup_target"),
    ):
        cards.append({
            "evidence_id": evidence_id,
            "file_path": path,
            "start_line": 1,
            "end_line": 1,
            "excerpt": source.read_text(encoding="utf-8"),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "symbols": [symbol],
        })
    (artifacts / "evidence_cards.json").write_text(json.dumps(cards), encoding="utf-8")
    (artifacts / "black_box_cases.json").write_text(
        json.dumps([{
            "case_id": "BB-001",
            "test_dimension": "resource_cleanup",
            "scenario_name": "cleanup",
            "preconditions": ["connected"],
            "steps": ["run public disconnect command"],
            "expected_result": "command exits 0 and controller disappears",
            "observability": ["exit code", "controller list"],
            "failure_diagnostics": ["stderr"],
            "mapped_test_dir": "test",
            "source_or_test_evidence": ["test/cleanup.c:1"],
            "technical_claims": [{
                "claim_id": "TC-001",
                "type": "behavior",
                "statement": "connect returns success",
                "evidence": [{
                    "evidence_id": "SRC-001:L1",
                    "path": "src/connect.c",
                    "lines": "L1",
                    "quote": "int connect_target(void) { return 0; }",
                    "symbol": "connect_target",
                }],
            }],
        }]),
        encoding="utf-8",
    )
    contract = {
        "artifact_contract": {
            "black_box_cases.json": {
                "required_fields": [],
                "schema": {"type": "array"},
            }
        }
    }

    result = audit_test_activity_artifacts(
        artifact_dir=artifacts,
        contract=contract,
        repo_path=str(repo),
    )

    assert any(
        issue["code"] == "claim_evidence_not_declared_for_row"
        for issue in result["issues"]
    )


def test_markdown_evidence_anchor_must_match_evidence_card_range(tmp_path):
    from app.services.test_activity_contract import audit_test_activity_artifacts

    repo = tmp_path / "repo"
    source = repo / "src" / "connect.c"
    source.parent.mkdir(parents=True)
    source.write_text("\n".join(f"line {index}" for index in range(1, 31)) + "\n")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "evidence_cards.json").write_text(
        json.dumps([{
            "evidence_id": "SRC-001",
            "file_path": "src/connect.c",
            "start_line": 10,
            "end_line": 20,
            "excerpt": "line 10",
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "symbols": [],
        }]),
        encoding="utf-8",
    )
    (artifacts / "flow_map.md").write_text(
        "# Flow\nEvidence: SRC-001:L99-L101\n",
        encoding="utf-8",
    )

    result = audit_test_activity_artifacts(
        artifact_dir=artifacts,
        contract={"artifact_contract": {"flow_map.md": {"sections": []}}},
        repo_path=str(repo),
    )

    assert any(
        issue["code"] == "evidence_anchor_out_of_range"
        for issue in result["issues"]
    )


def test_disconnected_flow_cannot_pass_as_complete_delivery(tmp_path):
    from app.services.test_activity_contract import _audit_markdown_artifact

    issues = _audit_markdown_artifact(
        artifact="flow_map.md",
        content=(
            "# 流程\n"
            "当前调用图包含 12 个互不连通的调用分量，不能证明单一端到端业务顺序。\n"
        ),
        spec={"sections": []},
        repo=tmp_path,
    )

    assert any(issue["code"] == "disconnected_flow_evidence" for issue in issues)


def test_evidence_path_classification_ignores_markdown_line_anchor():
    from app.services.test_activity_contract import _evidence_path_classification

    assert _evidence_path_classification(
        "libnvme/src/nvme/fabrics.h:L300-308"
    ) == "source"
    assert _evidence_path_classification(
        "libnvme/test/ioctl/discovery.c:40-54"
    ) == "test"


def test_bound_behavior_validation_skips_same_id_with_stale_binding():
    from app.services.test_activity_contract import _bound_behavior_validation_status

    status, reason = _bound_behavior_validation_status(
        validation={
            "validator": {"independent": True},
            "claims": [
                {
                    "claim_id": "TC-04",
                    "binding": "blackbox-binding",
                    "status": "supports",
                    "reason": "different artifact",
                },
                {
                    "claim_id": "TC-04",
                    "binding": "sfmea-binding",
                    "status": "supports",
                    "reason": "matching SFMEA claim",
                },
            ],
        },
        claim_id="TC-04",
        binding="sfmea-binding",
    )

    assert status == "supports"
    assert reason == "matching SFMEA claim"


def test_bound_behavior_validation_details_exposes_a_scoped_field_patch():
    from app.services.test_activity_contract import _bound_behavior_validation_details

    status, reason, field_patch = _bound_behavior_validation_details(
        validation={
            "validator": {"independent": True},
            "claims": [
                {
                    "claim_id": "ROW:sfmea.json:SFMEA-04",
                    "binding": "binding-04",
                    "status": "contradicts",
                    "reason": "detection log is absent",
                    "field_patch": {
                        "detection": "仅观测状态响应。",
                    },
                }
            ],
        },
        claim_id="ROW:sfmea.json:SFMEA-04",
        binding="binding-04",
    )

    assert status == "contradicts"
    assert reason == "detection log is absent"
    assert field_patch == {"detection": "仅观测状态响应。"}


def test_staged_raw_pdu_report_requires_loopback_runtime_evidence(tmp_path):
    from app.services.test_activity_contract import _audit_raw_pdu_runtime_evidence

    (tmp_path / "staged_execution_result.json").write_text(
        json.dumps({"status": "completed"}),
        encoding="utf-8",
    )
    content = "## 附录：raw-PDU harness\n```python\nimport socket\n```"

    issues = _audit_raw_pdu_runtime_evidence(root=tmp_path, content=content)
    assert [issue["code"] for issue in issues] == [
        "raw_pdu_runtime_validation_failed"
    ]

    (tmp_path / "raw_pdu_harness_validation.json").write_text(
        json.dumps(
            {
                "status": "passed",
                "checks": [
                    "tcp_connect",
                    "first_pdu_sendall",
                    "login_response_recv",
                    "status_oracle",
                ],
            }
        ),
        encoding="utf-8",
    )
    assert _audit_raw_pdu_runtime_evidence(root=tmp_path, content=content) == []


def test_combined_iscsi_report_does_not_treat_independent_login_storm_as_mcs():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## 黑盒测试用例
### TC-03 Burst login storm (100 concurrent logins)
- 前置条件：SPDK target 运行，MaxConnectionsPerSession 设为足够大（如100）；raw-PDU harness 或并发脚本可用
- 操作步骤：同时打开100个TCP socket；每个 socket 使用不同 ISID、CID=1、TSIH=0 发送独立 Login Request；收集所有响应
- 预期结果：独立 session 的登录成功或按全局资源上限失败，target 不崩溃
- 测试映射：test/iscsi_tgt/multiconnection/multiconnection.sh 仅作多 target 环境参考，不证明同一 session MCS
"""

    issues = _audit_combined_report_consistency(content)

    assert not any(
        issue.get("constraint_id") in {
            "iscsi_multiconnection_client_capability",
            "iscsi_multiconnection_mapping_scope",
        }
        for issue in issues
    ), issues


def test_mcs_contract_accepts_zero_padded_decimal_identifiers_without_crashing():
    from app.services.test_activity_contract import _is_mcs_case_contract

    content = """
### MaxConnectionsPerSession capacity
首连接使用 TSIH=0001, CID=0001；第二连接复用 TSIH=0001, CID=0002。
"""

    assert _is_mcs_case_contract(content) is True


def test_combined_iscsi_report_accepts_explicit_non_multiconnection_mapping_prefix():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## 黑盒测试用例
### BB-016 MCS 容量限制（MaxConnectionsPerSession=1）
- 前置条件：target 启动前执行 scripts/rpc.py iscsi_set_options -c 1；首连接保持在线并记录非零 TSIH。
- 操作步骤：使用可执行 Python raw-PDU harness，复用相同 ISID/非零 TSIH，以新 CID 发送第二个 Login Request。
- 预期结果：第二条登录被拒绝，Status-Detail=0x06；旧 socket 继续可用。
- 测试映射：raw-PDU harness（附录 raw_pdu_harness.py）；非 multiconnection.sh。
"""

    issues = _audit_combined_report_consistency(content)

    assert not any(
        issue.get("constraint_id") == "iscsi_multiconnection_mapping_scope"
        for issue in issues
    ), issues


def test_combined_iscsi_report_rejects_mid_login_timer_claim_in_sfmea_row():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## 主流程与异常/恢复流程
源码证据表明首个 payload 处理时 login_timer 已注销，当前实现未重新注册。

## SFMEA
| ID | Failure mode | Cause | Effect | Detection | S | O | D | RPN | Mitigation | Evidence |
|---|---|---|---|---|---:|---:|---:|---:|---|---|
| FMEA-17 | 登录超时：在 ISCSI_LOGIN_TIMEOUT (30s) 内未完成登录 | Initiator 在中间阶段停止响应 | login_timeout 回调设置 EXITING，连接关闭 | 观察 TCP 断开 | 5 | 3 | 6 | 90 | 发送一个 Login Request 后暂停 31 秒，观察连接关闭 | lib/iscsi/conn.c:login_timeout |
"""

    issues = _audit_combined_report_consistency(content)

    assert any(
        issue["code"] == "sfmea_evidence_contradiction"
        and issue.get("constraint_id") == "iscsi_login_timer_after_first_pdu"
        for issue in issues
    )


def test_combined_iscsi_report_keeps_network_mapping_disclaimer_but_rejects_real_timer_claim():
    from app.services.test_activity_contract import (
        _audit_combined_report_consistency,
        _audit_professional_constraints,
        build_test_activity_contract,
    )

    content = """
## SFMEA
| ID | Failure mode | Cause | Effect | Detection | S | O | D | RPN | Mitigation | Evidence |
|---|---|---|---|---|---:|---:|---:|---:|---|---|
| FMEA-02 | Initiator sends first Login PDU but then stops; login timer expires after 30 seconds | Network issue or initiator crash | Connection enters ISCSI_CONN_STATE_EXITING; cleanup triggered | observe TCP RST after 30s | 4 | 5 | 2 | 40 | Ensure timer fires and cleanup completes | 待新增 network fault test; 不可用 login_redirection.sh (只测 RPC 重定向, 非网络中断) |
"""
    contract = build_test_activity_contract(
        target="iSCSI Login SFMEA",
        repo_path="/tmp/spdk",
    )

    professional_issues = _audit_professional_constraints(content, contract)
    consistency_issues = _audit_combined_report_consistency(content)

    assert not any(
        issue.get("constraint_id") == "iscsi_redirection_mapping_scope"
        for issue in professional_issues
    ), professional_issues
    assert any(
        issue["code"] == "sfmea_evidence_contradiction"
        and issue.get("constraint_id") == "iscsi_login_timer_after_first_pdu"
        for issue in consistency_issues
    ), consistency_issues


def test_combined_iscsi_report_rejects_first_pdu_delay_close_oracle_from_run33():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## 主流程与异常/恢复流程
首个 Login PDU 的 payload 处理时，登录定时器由 spdk_poller_unregister 注销。

## SFMEA
| ID | Failure mode | Cause | Effect | Detection | S | O | D | RPN | Mitigation | Evidence |
|---|---|---|---|---|---:|---:|---:|---:|---|---|
| FMEA-20 | Login 超时（超过 ISCSI_LOGIN_TIMEOUT 30 秒） | 网络延迟或发起者停滞 | login_timeout 设 EXITING，连接断开 | 日志或连接状态 | 8 | 6 | 3 | 144 | 确认 timer 注销后不再重启 | 待新增 raw-PDU harness：发送首个 Login PDU 后延迟 35 秒再发第二个，预期连接被关闭 |
"""

    issues = _audit_combined_report_consistency(content)

    assert any(
        issue["code"] == "sfmea_evidence_contradiction"
        and issue.get("constraint_id") == "iscsi_login_timer_after_first_pdu"
        for issue in issues
    ), issues


def test_combined_iscsi_report_rejects_ambiguous_tsih_zero_oracle():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## 黑盒测试用例
### TC-02 Session Reinstatement (TSIH=0) - same ISID
- 前置条件：已存在相同 ISID 的 Normal session。
- 操作步骤：发送 TSIH=0 的 Login Request。
- 预期结果：Target 应拒绝请求并返回 Initiator Error，或认为这是 reinstatement（视策略而定）。
- 观测点：抓包检查 Status-Class 与 Status-Detail。
"""

    issues = _audit_combined_report_consistency(content)

    assert any(
        issue["code"] == "black_box_expected_result_ambiguous"
        and issue.get("constraint_id") == "iscsi_tsih_reinstatement_scope"
        for issue in issues
    )


def test_combined_iscsi_report_accepts_explicit_first_pdu_timer_defect_oracle():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## 黑盒测试用例
### TC-01 Login timeout after first PDU hang
- 前置条件：login_timer deregistered after first payload, 30s timeout may not trigger - potential defect; resource oracle required.
- 操作步骤：发送首个 Login PDU 后停滞 31 秒。
- 预期结果：Current SPDK may leave connection half-open after 30s because login_timer is deregistered. Design expects TCP RST. This case detects the potential defect and resource leak.
- 观测点：RPC 连接计数、TCP socket 与进程 RSS。
"""

    issues = _audit_combined_report_consistency(content)

    assert not any(issue["code"] == "black_box_evidence_contradiction" for issue in issues)


def test_combined_iscsi_report_accepts_pending_timer_behavior_with_residue_oracle():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## 黑盒测试用例
### TC-01 Login timeout after sending first PDU then idle 35s
- 前置条件：发送第一个 Login PDU 后保持静默 35 秒。
- 预期结果：根据源码证据，login_timer 在首 PDU 处理后即被注销，30 秒定时器不会触发超时关闭。实际行为待验证：连接可能残留；若连接被清理则存在其他超时机制（待确认）。
- 观测点：pcap 中 35 秒内无 FIN/RST；RPC 连接计数可能不为零（资源残留）；记录 socket 和进程 RSS。
"""

    issues = _audit_combined_report_consistency(content)

    assert not any(
        issue["code"] == "black_box_evidence_contradiction"
        and issue.get("constraint_id") == "iscsi_login_timer_after_first_pdu"
        for issue in issues
    ), issues


def test_combined_iscsi_report_accepts_run46_unverified_no_timeout_claim():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## 黑盒测试用例
### BB-16 Login stall after first PDU (login_timer disabled)
- 前置条件：Raw-PDU harness sends only first Login PDU.
- 操作步骤：Open TCP connection, send first Login PDU, then wait 60 seconds.
- 预期结果：Behavior unverified: login_timer disabled after first PDU, so no 30s timeout. Connection may hang (待验证). Resource leak possible (socket fd not closed).
- 观测点：Target log does not show 'login timeout'; after >30s check /proc/net/tcp for a persisted connection (resource leak oracle).
"""

    issues = _audit_combined_report_consistency(content)

    assert not any(
        issue.get("code") == "black_box_evidence_contradiction"
        and issue.get("constraint_id") == "iscsi_login_timer_after_first_pdu"
        for issue in issues
    ), issues


def test_combined_iscsi_report_rejects_discovery_login_target_address_oracle():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## 黑盒测试用例
### TC-05 Discovery login returns TargetAddresses
- 前置条件：Target running in discovery mode.
- 操作步骤：Run iscsiadm -m discovery -t sendtargets -p 127.0.0.1:3260.
- 预期结果：Discovery login succeeds; response contains TargetAddress= keys.
- 观测点：Wire pcap shows Login Response with Status=0x00 and TargetAddress in data segment.
"""

    issues = _audit_combined_report_consistency(content)

    assert any(
        issue["code"] == "black_box_evidence_contradiction"
        and issue.get("constraint_id") == "iscsi_discovery_target_address"
        for issue in issues
    )


def test_combined_iscsi_report_accepts_target_address_in_followup_sendtargets_response():
    from app.services.test_activity_contract import (
        _audit_combined_report_consistency,
        _audit_professional_constraints,
        build_test_activity_contract,
    )

    content = """
## 黑盒测试用例
### TC-01 Discovery 登录成功（TargetAddress 在 SendTargets 期出现）
- 操作步骤：执行 iscsiadm -m discovery -t sendtargets -p <target_ip>:3260；捕获 discovery login 和后续 SendTargets 的 tcpdump；检查 SendTargets Text Response 中的 TargetAddress
- 预期结果：Discovery Login Response 成功（Status=0x00），SendTargets 响应包含 TargetAddress 和 TargetName
- 观测点：tcpdump 显示 Login Response Status=0x00；SendTargets Text Response 包含 TargetAddress=... 和 TargetName=...
"""
    contract = build_test_activity_contract(
        target="iSCSI Login 完整测试设计",
        repo_path="/tmp/spdk",
    )

    professional = _audit_professional_constraints(content, contract)
    consistency = _audit_combined_report_consistency(content)

    assert not any(
        issue.get("constraint_id") == "iscsi_discovery_target_address"
        for issue in [*professional, *consistency]
    ), [*professional, *consistency]


def test_professional_gate_accepts_different_cid_rejected_by_explicit_capacity_limit():
    from app.services.test_activity_contract import (
        _audit_professional_constraints,
        build_test_activity_contract,
    )

    content = """
### TC-03 超过 MaxConnectionsPerSession 容量（raw-PDU harness）
- 前置条件：target 启动前执行 scripts/rpc.py iscsi_set_options -c 1；raw-PDU harness（附录）可用；tcpdump 捕获
- 操作步骤：使用 harness 发送第一个 Login Request（ISID=new，CID=1）获得 TSIH；保持第一个连接的 TCP socket 在线；使用相同 TSIH、不同 CID（如 CID=2）发送第二个 Login Request；断言第二个 Login Response 的 Status=0x03 detail=0x06
- 预期结果：第二个 Login Response 返回 Too Many Connections（Status=0x03 detail=0x06），连接被拒绝
- 观测点：tcpdump 显示第二条 Login Response status=0x03 detail=0x06；harness recv 得到明确拒绝
"""
    contract = build_test_activity_contract(
        target="iSCSI Login 完整测试设计",
        repo_path="/tmp/spdk",
    )

    issues = _audit_professional_constraints(content, contract)

    assert not any(
        issue.get("constraint_id") == "iscsi_duplicate_cid_not_too_many_connections"
        for issue in issues
    ), issues


def test_combined_iscsi_report_rejects_unmeasured_absolute_login_latency_oracle():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## 黑盒测试用例
### TC-03 Login latency p50/p95/p99
- 操作步骤：Repeat 10 times and compute p50/p95/p99 from pcap timestamps.
- 预期结果：Login latency typical < 10 ms in isolation. Report a baseline for future regression.
- 观测点：tcpdump timestamps.
"""

    issues = _audit_combined_report_consistency(content)

    assert any(issue["code"] == "ungrounded_performance_threshold" for issue in issues)


def test_combined_iscsi_report_rejects_first_pdu_timeout_despite_unrelated_pending_marker():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## 黑盒测试用例
### TC-23 Login timeout after first Login PDU
- 前置条件：Target running；日志中的 timeout 原因仍待验证。
- 操作步骤：发送第一个 Login PDU 后停止发送，等待 35 秒。
- 预期结果：30 秒后 target 主动关闭连接，客户端收到 FIN 或 RST。
- 观测点：tcpdump 与连接计数。
"""

    issues = _audit_combined_report_consistency(content)

    assert any(
        issue["code"] == "black_box_evidence_contradiction"
        and issue.get("constraint_id") == "iscsi_login_timer_after_first_pdu"
        for issue in issues
    ), issues


def test_combined_iscsi_report_rejects_chap_challenge_stall_timeout_oracle():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## 黑盒测试用例
### TC-24 CHAP challenge stall timeout
- 前置条件：Target 已启用 CHAP。
- 操作步骤：发起登录，收到 Target CHAP challenge 后停止发送响应，等待 35 秒。
- 预期结果：30 秒后 target 主动关闭连接，客户端收到 FIN 或 RST。
- 观测点：pcap 与 RPC 连接计数。
"""

    issues = _audit_combined_report_consistency(content)

    assert any(
        issue["code"] == "black_box_evidence_contradiction"
        and issue.get("constraint_id") == "iscsi_login_timer_after_first_pdu"
        for issue in issues
    ), issues


def test_professional_gate_accepts_explicit_perf_script_non_login_disclaimer():
    from app.services.test_activity_contract import (
        _audit_professional_constraints,
        build_test_activity_contract,
    )

    content = """
### TC-05 Login latency baseline
- 测试映射：test/iscsi_tgt/perf/iscsi_target.sh 仅运行 fio I/O，not login latency；
  登录时延使用独立 pcap 计时器并在首次运行建立基线。
"""
    contract = build_test_activity_contract(
        target="iSCSI Login 完整测试设计",
        repo_path="/tmp/spdk",
    )

    issues = _audit_professional_constraints(content, contract)

    assert not any(
        issue.get("constraint_id") == "iscsi_perf_scripts_not_login_latency"
        for issue in issues
    ), issues


def test_professional_gate_accepts_chinese_io_only_perf_script_disclaimer():
    from app.services.test_activity_contract import (
        _audit_professional_constraints,
        build_test_activity_contract,
    )

    content = """
### BB-024 单次登录平均延迟测量
- 测试映射：独立登录延迟 harness + tcpdump；待补正式测试用例。
- 证据：test/iscsi_tgt/perf/iscsi_initiator.sh（仅 I/O，不覆盖登录延迟）。
"""
    contract = build_test_activity_contract(
        target="iSCSI Login 完整测试设计",
        repo_path="/tmp/spdk",
    )

    issues = _audit_professional_constraints(content, contract)

    assert not any(
        issue.get("constraint_id") == "iscsi_perf_scripts_not_login_latency"
        for issue in issues
    ), issues


def test_combined_iscsi_report_keeps_unmeasured_latency_blocked_with_disclaimer():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## 黑盒测试用例
### TC-05 Login latency p50/p95
- 操作步骤：连续登录 10 次并计算分位数。
- 预期结果：全部登录时延 < 5000ms，p50 < 500ms（待验证基线）。
- 测试映射：iscsi_target.sh 仅运行 fio I/O，not login latency。
"""

    issues = _audit_combined_report_consistency(content)

    assert any(issue["code"] == "ungrounded_performance_threshold" for issue in issues), issues


def test_combined_iscsi_report_rejects_raw_pdu_cli_options_missing_from_parser():
    from app.services.test_activity_contract import _audit_combined_professional_completeness

    content = """
## 黑盒测试用例
运行 `python3 raw_pdu.py --host 127.0.0.1 --port 3260 --tsih 1 --cid 2 --login-req login.bin --target-iqn iqn.test`。

```python
import argparse
import socket
import struct
import hashlib

parser = argparse.ArgumentParser()
parser.add_argument("--host", required=True)
parser.add_argument("--port", type=int, required=True)
args = parser.parse_args()
```
"""
    contract = {
        "domain_profiles": ["iscsi_login"],
        "target": "iSCSI Login 完整测试设计",
        "required_outputs": ["business_flow.md", "sfmea.json", "black_box_cases.json"],
        "artifact_contract": {},
    }

    issues = _audit_combined_professional_completeness(content, contract)

    assert any(
        issue["code"] == "non_executable_raw_pdu_harness"
        and "--tsih" in str(issue.get("message") or "")
        for issue in issues
    ), issues


def test_combined_iscsi_report_accepts_raw_pdu_cli_options_declared_by_parser():
    from app.services.test_activity_contract import _raw_pdu_cli_contract_errors

    content = """
运行 `python3 raw_pdu.py --host 127.0.0.1 --port 3260 --tsih 1 --cid 2`。

```python
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--host", required=True)
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--tsih", type=int, required=True)
parser.add_argument("--cid", type=int, required=True)
args = parser.parse_args()
```
"""

    assert _raw_pdu_cli_contract_errors(content) == []


def test_combined_iscsi_report_rejects_quality_repair_meta_language():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## 主流程与异常/恢复流程
#### 领域事实修正：Session Reinstatement 与连接追加
错误描述修正：之前版本混淆了两条路径。
#### 新增专业必测场景：首 Payload 后 Timer 注销
请在上述流程叙述的“外部触发与入口”段末尾补充以下情节：
"""

    issues = _audit_combined_report_consistency(content)

    assert any(issue["code"] == "quality_repair_meta_language" for issue in issues)


def test_combined_iscsi_report_rejects_duplicate_sfmea_semantics():
    from app.services.test_activity_contract import _audit_combined_report_consistency

    content = """
## SFMEA
| ID | Failure mode | Cause | Effect | Detection | S | O | D | RPN | Mitigation | Evidence |
|---|---|---|---|---|---:|---:|---:|---:|---|---|
| FMEA-01 | Mutual challenge 合法编码但语义错误 | wrong value | bypass | pcap | 9 | 2 | 4 | 72 | reject | lib/iscsi/iscsi.c |
| FMEA-09 | Mutual CHAP challenge correctly encoded but uses a wrong value | replay | bypass | pcap | 9 | 2 | 4 | 72 | reject | lib/iscsi/iscsi.c |
| FMEA-05 | Unsupported CHAP_A algorithm proposed by initiator | SHA1 | fail | pcap | 7 | 2 | 6 | 84 | reject | lib/iscsi/iscsi.c |
| FMEA-18 | CHAP algorithm mismatch uses a non-MD5 algorithm | SHA1 | fail | pcap | 7 | 3 | 6 | 126 | reject | lib/iscsi/iscsi.c |
"""

    issues = _audit_combined_report_consistency(content)

    duplicates = [issue for issue in issues if issue["code"] == "duplicate_sfmea_risk"]
    assert len(duplicates) == 2


def test_combined_report_attributes_sfmea_fact_conflict_to_sfmea_artifact(tmp_path):
    from app.services.test_activity_contract import (
        audit_test_activity_artifacts,
        build_test_activity_contract,
    )

    repo = tmp_path / "spdk"
    repo.mkdir()
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (artifact_dir / "report.md").write_text(
        "# iSCSI Login 报告\n\n"
        "## 分析范围与证据缺口\n待补证据。\n\n"
        "## 关键源码证据\n待补证据。\n\n"
        "## 主流程与异常/恢复流程\n1. 接收请求。\n2. 协商。\n3. 返回响应。\n\n"
        "## SFMEA\n"
        "| ID | failure mode | cause | effect | detection | S | O | D | RPN | mitigation | evidence |\n"
        "|---|---|---|---|---|---:|---:|---:|---:|---|---|\n"
        "| FMEA-18 | 非法阶段迁移 | CSG=0 可直接到 NSG=3，但规范要求先进入 Operational Negotiation | 会话异常 | 抓包 | 8 | 2 | 6 | 96 | 拒绝请求 | lib/iscsi/iscsi.c |\n\n"
        "## 黑盒测试用例\n待补。\n",
        encoding="utf-8",
    )
    contract = build_test_activity_contract(
        target="iSCSI Login 完整流程 SFMEA 黑盒测试设计",
        repo_path=str(repo),
        workflow_outputs=[
            {"artifact": "report.md", "type": "combined_test_report"},
        ],
    )

    audit = audit_test_activity_artifacts(
        artifact_dir=artifact_dir,
        contract=contract,
        repo_path=str(repo),
    )
    conflict = next(
        issue
        for issue in audit["lint_warnings"]
        if issue.get("constraint_id") == "iscsi_csg_values"
    )

    assert conflict["artifact"] == "sfmea.json"


def test_quality_audit_separates_legacy_professional_lint_from_verified_facts(tmp_path):
    from app.services.test_activity_contract import audit_test_activity_artifacts

    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    repo = tmp_path / "repo"
    (repo / "lib").mkdir(parents=True)
    (repo / "test").mkdir()
    (repo / "lib" / "protocol.c").write_text("int protocol_value;\n", encoding="utf-8")
    (repo / "test" / "protocol.sh").write_text("# smoke\n", encoding="utf-8")
    (artifact_dir / "report.md").write_text(
        "# Report\n\nThe generated report claims WRONG_PROTOCOL_VALUE.\n\n"
        "Evidence: lib/protocol.c and test/protocol.sh.\n",
        encoding="utf-8",
    )
    contract = {
        "artifact_contract": {
            "report.md": {
                "min_chars": 10,
                "sections": [],
            }
        },
        "professional_constraints": [
            {
                "id": "legacy_regex_fact",
                "assertion": "The protocol value must come from source evidence.",
                "conflict_patterns": ["WRONG_PROTOCOL_VALUE"],
                "correction_patterns": [],
            }
        ],
        "quality_gates": {"min_score": 80},
    }

    result = audit_test_activity_artifacts(
        artifact_dir=artifact_dir,
        contract=contract,
        repo_path=str(repo),
    )

    assert not any(issue["code"] == "professional_fact_conflict" for issue in result["issues"])
    assert any(
        issue["code"] == "professional_fact_conflict"
        for issue in result["lint_warnings"]
    )
    assert result["quality_axes"]["structure"]["status"] == "passed"
    assert result["quality_axes"]["facts"]["status"] == "not_checked"
    assert result["quality_axes"]["executability"]["status"] == "not_checked"


@pytest.mark.parametrize(
    ("constraint_id", "statement"),
    [
        (
            "iscsi_chap_security_stage",
            "CSG=1 仅检查认证状态，不应在 Operational Negotiation 中发送 CHAP challenge/response。",
        ),
        (
            "iscsi_unknown_user_test_mapping_scope",
            "不要把 chap_discovery.sh 当成未知用户覆盖；它覆盖缺少或配置正确凭据路径。",
        ),
        (
            "iscsi_redirection_mapping_scope",
            "Redirect 流程被误当作网络中断自动恢复；test/iscsi_tgt/login_redirection/login_redirection.sh 仅验证受控 RPC redirect。",
        ),
        (
            "iscsi_redirection_mapping_scope",
            "Redirect 流程被误当作网络中断自动恢复；现有脚本仅验证受控 RPC redirect 与连接计数；test/iscsi_tgt/login_redirection/login_redirection.sh。",
        ),
        (
            "iscsi_calsoft_mapping_scope",
            "不要使用 calsoft.py 或 perf FIO 脚本推导 login latency。",
        ),
        (
            "iscsi_login_error_c_flag_preserved",
            "非 success Login Response 清除 T、CSG、NSG；源码未在该分支清除 C bit，因此不能写成清除 T/C/CSG/NSG。",
        ),
        (
            "iscsi_csg_values",
            "CSG 0/1/3 分别为 Security Negotiation、Operational Negotiation、Full Feature Phase。",
        ),
        (
            "iscsi_unknown_key_not_understood",
            "未知但格式合法参数通常由协商层处理，不能笼统当成 parse failure。",
        ),
        (
            "iscsi_invalid_login_request_detail",
            "非法 NSG=2 被误报 0x0b；raw-PDU 必须断言 detail 0x00。",
        ),
        (
            "iscsi_login_version_offsets",
            "BHS byte 2/3 是版本字段；修改 byte 3，避免修改 payload bytes 40-41。",
        ),
        (
            "iscsi_fuzzer_skips_login_opcode",
            "iscsi_fuzz.c 的 seed 不是随机非法 Login Request 覆盖证明。",
        ),
        (
            "iscsi_unknown_user_test_mapping_scope",
            "Unknown CHAP_N 被现有测试误认为覆盖；禁止映射到 chap_discovery.sh 通过项。",
        ),
        (
            "iscsi_redirection_mapping_scope",
            "Redirect 被误当网络故障恢复；使用 login_redirection.sh 仅证明受控 RPC redirect。",
        ),
        (
            "iscsi_perf_scripts_not_login_latency",
            "不从 fio/perf 脚本外推 login latency；iscsi_target.sh 仅为 I/O 性能范围。",
        ),
        (
            "iscsi_multiconnection_mapping_scope",
            "multiconnection.sh 仅作危险隔离环境参考，不证明同一 session 非零 TSIH 下追加不同 CID。",
        ),
        (
            "iscsi_login_version_offsets",
            "BHS byte 2/3 版本字段解析回归；raw-PDU 修改 byte 3；新增负向黑盒并避免修改 payload bytes 40-41。",
        ),
        (
            "iscsi_unknown_user_test_mapping_scope",
            "Unknown CHAP_N 被现有测试误认为覆盖；chap_discovery.sh 只测正确凭据；新增用例，禁止映射到 chap_discovery.sh 通过项。",
        ),
        (
            "iscsi_redirection_mapping_scope",
            "login_redirection.sh 验证受控 RPC redirect，但不证明网络故障自动重连。",
        ),
        (
            "iscsi_login_version_offsets",
            "不要修改 payload bytes 40-41 来模拟版本；版本字段在 BHS byte 2/3。",
        ),
        (
            "iscsi_full_feature_request_rejected",
            "保持 CSG 白名单：收到 CSG=3 的 Login Request 作为非法请求处理，不把它当作进入 full feature 的合法迁移。",
        ),
        (
            "iscsi_invalid_login_request_detail",
            "T=1 时 NSG=2 不进入阶段迁移。执行 TC05，断言 detail 0x00；若出现 0x0b，标记为测试期望错误而非实现事实。",
        ),
        (
            "iscsi_redirection_mapping_scope",
            "运行手册把受控 redirect、logout/relogin、网络断开恢复分为三类，不复用 redirect 结果证明网络故障恢复。login_redirection.sh 仅记录 redirect 覆盖。",
        ),
    ],
)
def test_professional_lint_accepts_explicit_mapping_corrections(
    constraint_id, statement
):
    from app.services.test_activity_contract import (
        _matches_professional_correction,
        build_test_activity_contract,
    )

    contract = build_test_activity_contract(
        target="SPDK iSCSI Login 测试设计",
        repo_path="/tmp/spdk",
    )
    constraint = next(
        item
        for item in contract["professional_constraints"]
        if item["id"] == constraint_id
    )
    constraint = {**constraint, "correction_patterns": []}

    assert _matches_professional_correction(statement, constraint)


def test_quality_audit_reports_fact_contradiction_on_fact_axis(tmp_path):
    from app.services.test_activity_contract import audit_test_activity_artifacts
    from app.services.workflow_presets import SFMEA_SCHEMA

    repo = tmp_path / "repo"
    header = repo / "include" / "spdk" / "iscsi_spec.h"
    header.parent.mkdir(parents=True)
    header.write_text("#define ISCSI_VERSION 0x00\n", encoding="utf-8")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (artifact_dir / "evidence_cards.json").write_text(
        json.dumps(
            [
                {
                    "evidence_id": "SRC-001",
                    "file_path": "include/spdk/iscsi_spec.h",
                    "start_line": 1,
                    "end_line": 1,
                    "excerpt": "#define ISCSI_VERSION 0x00",
                    "sha256": hashlib.sha256(header.read_bytes()).hexdigest(),
                    "symbols": ["ISCSI_VERSION"],
                }
            ]
        ),
        encoding="utf-8",
    )
    (artifact_dir / "sfmea.json").write_text(
        json.dumps(
            [
                {
                    "sfmea_id": "SFMEA-FACT-1",
                    "failure_mode": "Unsupported version: version_max=0, version_min=0",
                    "cause": "Both version fields are zero.",
                    "effect": "Target rejects the login as unsupported version.",
                    "detection": "Observe a failed Login Response.",
                    "severity": 6,
                    "occurrence": 2,
                    "detection_score": 2,
                    "rpn": 24,
                    "mitigation": "Use a supported version range.",
                    "source_evidence": ["include/spdk/iscsi_spec.h::ISCSI_VERSION"],
                    "test_mapping": "raw-PDU harness",
                }
            ]
        ),
        encoding="utf-8",
    )

    result = audit_test_activity_artifacts(
        artifact_dir=artifact_dir,
        contract={
            "artifact_contract": {"sfmea.json": {"schema": SFMEA_SCHEMA}},
            "quality_gates": {"min_score": 80},
        },
        repo_path=str(repo),
    )

    facts = result["quality_axes"]["facts"]
    assert facts["status"] == "blocked"
    assert facts["verified"] == 0
    assert facts["contradicted"] == 1
    assert facts["pass_rate"] == 0
    assert result["deliverable"] is False


def test_fact_ledger_reads_workflow_artifacts_from_agent_run_directory(tmp_path):
    from app.services.test_activity_contract import audit_test_activity_artifacts
    from app.services.workflow_presets import SFMEA_SCHEMA

    repo = tmp_path / "repo"
    header = repo / "include" / "spdk" / "iscsi_spec.h"
    header.parent.mkdir(parents=True)
    header.write_text("#define ISCSI_VERSION 0x00\n", encoding="utf-8")
    task_root = tmp_path / "task"
    artifact_dir = task_root / "agent_runs" / "analyze"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "evidence_cards.json").write_text(
        json.dumps(
            [
                {
                    "evidence_id": "SRC-001",
                    "file_path": "include/spdk/iscsi_spec.h",
                    "start_line": 1,
                    "end_line": 1,
                    "excerpt": "#define ISCSI_VERSION 0x00",
                    "sha256": hashlib.sha256(header.read_bytes()).hexdigest(),
                    "symbols": ["ISCSI_VERSION"],
                }
            ]
        ),
        encoding="utf-8",
    )
    (artifact_dir / "sfmea.json").write_text(
        json.dumps(
            [
                {
                    "sfmea_id": "SFMEA-NESTED-1",
                    "failure_mode": "Unsupported version: version_max=0, version_min=0",
                    "cause": "Both version fields are zero.",
                    "effect": "Target rejects the login as unsupported version.",
                    "detection": "Observe Login Response.",
                    "severity": 6,
                    "occurrence": 2,
                    "detection_score": 2,
                    "rpn": 24,
                    "mitigation": "Use a supported version range.",
                    "source_evidence": ["include/spdk/iscsi_spec.h::ISCSI_VERSION"],
                    "test_mapping": "raw-PDU harness",
                }
            ]
        ),
        encoding="utf-8",
    )

    result = audit_test_activity_artifacts(
        artifact_dir=task_root,
        contract={
            "artifact_contract": {"sfmea.json": {"schema": SFMEA_SCHEMA}},
            "quality_gates": {"min_score": 80},
        },
        repo_path=str(repo),
    )

    assert result["fact_verification"]["total"] == 1
    assert result["fact_verification"]["contradicted"] == 1
