import json
from pathlib import Path

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
    assert contract["evidence_policy"]["source_first"] is True
    assert contract["evidence_policy"]["prefer_artifacts"] == ["GitNexus", "CGC"]
    assert contract["black_box_boundary"]["forbidden_internal_steps"]
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
    assert task_card["target"] == "针对 iSCSI login 输出 SFMEA 和黑盒测试用例"
    assert "iscsi_login" in task_card["domain_profiles"]
    assert "sfmea.json" in task_card["recommended_outputs"]
    assert "black_box_cases.json" in task_card["recommended_outputs"]
    assert task_card["evidence_policy"]["source_first"] is True
    assert task_card["href"] == "/workbench?workflow=source_flow_sfmea_blackbox"
