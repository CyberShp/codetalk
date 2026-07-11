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
        "## 流程步骤\n\n1. 建立 TCP 连接。\n2. 协商登录参数与 CHAP。\n3. 进入会话或返回失败并清理。\n\n"
        "## SFMEA\n\n| failure_mode | cause | effect | detection | severity | occurrence | "
        "detection_score | RPN | score_explanation | mitigation | source_evidence | test_mapping |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| CHAP 绕过 | 认证分支错误 | 未授权访问 | 登录响应和日志 | 10 | 2 | 3 | 60 | "
        "安全影响高 | 增加拒绝用例 | `lib/iscsi/iscsi.c` | `test/iscsi_tgt/login.sh` |\n\n"
        "## 黑盒测试用例\n\n"
        f"{cases}\n\n"
        "## 未确认项\n\n跨平台 initiator 差异为 ai_suggested_unverified。"
    )

    audit = audit_test_activity_response(
        content=content,
        contract=contract,
        repo_path=str(repo),
    )

    assert audit["status"] == "deliverable", audit
    assert audit["score"] == 100


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
        "iscsi_unverified_cleanup_or_lock_defect",
    }


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
