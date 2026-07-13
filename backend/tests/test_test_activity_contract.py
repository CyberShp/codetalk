import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest


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
            "sfmea.json": {"required_fields": ["failure_mode"]},
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


def test_markdown_section_normalization_accepts_descriptive_parenthetical_suffix():
    from app.services.test_activity_contract import _normalized_markdown_heading

    assert _normalized_markdown_heading("主流程 (Connect 到首个 I/O)") == "主流程"
    assert _normalized_markdown_heading("异常与恢复路径（网络与控制器）") == "异常与恢复路径"


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

    assert result.status == "needs_rework"
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
        "Mutual CHAP 缺少 challenge、mutual 用户或 secret 缺失均独立覆盖。\n"
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
