def test_deep_artifact_contract_declares_deliverables_supporting_and_diagnostics():
    from app.services.artifact_contract_v3 import default_artifact_contract_v3

    contract = default_artifact_contract_v3(profile_id="deep")
    layers = {item["layer"] for item in contract["artifacts"]}
    required = {item["artifact"] for item in contract["artifacts"] if item["required"]}

    assert layers == {"deliverable", "supporting", "diagnostic"}
    assert {"完整分析报告.md", "风险点与SFMEA.md", "黑盒测试设计.md"} <= required


def test_rapid_contract_is_bounded_without_claiming_deep_deliverables():
    from app.services.artifact_contract_v3 import default_artifact_contract_v3

    contract = default_artifact_contract_v3(profile_id="rapid")
    report = next(item for item in contract["artifacts"] if item["artifact"] == "快速分析报告.md")
    deep_report = next(item for item in contract["artifacts"] if item["artifact"] == "完整分析报告.md")

    assert report["required"] is True
    assert deep_report["required"] is False
    assert contract["delivery_class"] == "bounded_analysis"


def test_rapid_contract_materializes_targeted_sfmea_and_black_box_deliverables(tmp_path):
    import json

    from app.services.artifact_contract_v3 import materialize_artifact_contract_v3_outputs

    (tmp_path / "sfmea.json").write_text(
        json.dumps([{"sfmea_id": "SFMEA-01", "failure_mode": "Login timeout"}]),
        encoding="utf-8",
    )
    (tmp_path / "black_box_cases.json").write_text(
        json.dumps([{"case_id": "BBC-01", "scenario_name": "Login timeout"}]),
        encoding="utf-8",
    )

    written = materialize_artifact_contract_v3_outputs(tmp_path, profile_id="rapid")

    assert {"风险点与SFMEA.md", "黑盒测试设计.md"} <= set(written)
    assert not (tmp_path / "完整分析报告.md").exists()


def test_claim_evidence_ledger_blocks_a_claim_with_a_fabricated_quote(tmp_path):
    import json

    from app.services.artifact_contract_v3 import materialize_claim_evidence_ledger

    (tmp_path / "evidence_cards.json").write_text(
        json.dumps([
            {
                "evidence_id": "EV-001",
                "file_path": "lib/iscsi/login.c",
                "symbols": ["login"],
                "excerpt": "return SPDK_SUCCESS;",
                "sha256": "a" * 64,
            }
        ]),
        encoding="utf-8",
    )
    (tmp_path / "sfmea.json").write_text(
        json.dumps([
            {
                "sfmea_id": "SFMEA-001",
                "technical_claims": [{
                    "claim_id": "C-001",
                    "type": "protocol_constant",
                    "statement": "Login returns SPDK_SUCCESS.",
                    "evidence": [{
                        "evidence_id": "EV-001",
                        "path": "lib/iscsi/login.c",
                        "symbol": "login",
                        "quote": "return SPDK_FAILURE;",
                    }],
                }],
            }
        ]),
        encoding="utf-8",
    )
    (tmp_path / "black_box_cases.json").write_text("[]", encoding="utf-8")

    ledger = materialize_claim_evidence_ledger(tmp_path)

    assert ledger["status"] == "blocked"
    assert ledger["summary"]["contradicted"] == 1
    assert ledger["claims"][0]["verification_status"] == "contradicted"
    assert (tmp_path / "claim_evidence_ledger.json").is_file()


def test_claim_evidence_ledger_carries_independent_behavior_verdicts(tmp_path):
    import json

    from app.services.artifact_contract_v3 import materialize_claim_evidence_ledger

    card = {
        "evidence_id": "EV-002",
        "file_path": "lib/iscsi/login.c",
        "symbols": ["login"],
        "excerpt": "return SPDK_SUCCESS;",
        "sha256": "b" * 64,
    }
    claim = {
        "claim_id": "C-002",
        "type": "source_behavior",
        "statement": "Login returns SPDK_SUCCESS.",
        "evidence": [{
            "evidence_id": "EV-002",
            "path": "lib/iscsi/login.c",
            "symbol": "login",
            "quote": "return SPDK_SUCCESS;",
        }],
    }
    (tmp_path / "evidence_cards.json").write_text(json.dumps([card]), encoding="utf-8")
    (tmp_path / "sfmea.json").write_text(
        json.dumps([{"sfmea_id": "SFMEA-002", "technical_claims": [claim]}]),
        encoding="utf-8",
    )
    (tmp_path / "black_box_cases.json").write_text("[]", encoding="utf-8")
    (tmp_path / "behavior_claim_validation.json").write_text(
        json.dumps({"claims": [{"claim_id": "C-002", "status": "contradicts", "binding": ""}]}),
        encoding="utf-8",
    )

    ledger = materialize_claim_evidence_ledger(tmp_path)

    assert ledger["claims"][0]["l1_status"] == "verified"
    assert ledger["claims"][0]["l2_status"] == "contradicts"
    assert ledger["claims"][0]["verification_status"] == "contradicted"


def test_claim_ledger_uses_l2_for_behavior_not_single_line_word_overlap(tmp_path):
    """A real source anchor proves provenance; L2 judges the broader behavior."""
    import json

    from app.services.artifact_contract_v3 import materialize_claim_evidence_ledger
    from app.services.test_activity_contract import _behavior_claim_binding

    quote = "spdk_poller_unregister(&conn->login_timer);"
    card = {
        "evidence_id": "SRC-TIMER",
        "file_path": "lib/iscsi/iscsi.c",
        "symbols": ["iscsi_pdu_payload_op_login"],
        "start_line": 2207,
        "end_line": 2247,
        "excerpt": quote,
        "sha256": "e" * 64,
    }
    evidence = [{
        "evidence_id": "SRC-TIMER",
        "path": "lib/iscsi/iscsi.c",
        "symbol": "iscsi_pdu_payload_op_login",
        "lines": "L2210",
        "quote": quote,
    }]
    claim = {
        "claim_id": "CLAIM-TIMER",
        "type": "behavior_assertion",
        "statement": "第一个登录负载到达后取消超时检查。",
        "evidence": evidence,
    }
    binding = _behavior_claim_binding(
        claim_id="CLAIM-TIMER",
        claim_type="behavior_assertion",
        statement=claim["statement"],
        evidence=[{**evidence[0], "sha256": card["sha256"]}],
    )
    (tmp_path / "evidence_cards.json").write_text(json.dumps([card]), encoding="utf-8")
    (tmp_path / "sfmea.json").write_text(
        json.dumps([{"sfmea_id": "SFMEA-TIMER", "technical_claims": [claim]}]),
        encoding="utf-8",
    )
    (tmp_path / "black_box_cases.json").write_text("[]", encoding="utf-8")
    (tmp_path / "behavior_claim_validation.json").write_text(
        json.dumps({"claims": [{
            "claim_id": "CLAIM-TIMER",
            "binding": binding,
            "status": "supports",
        }]}),
        encoding="utf-8",
    )

    ledger = materialize_claim_evidence_ledger(tmp_path)

    assert ledger["status"] == "passed"
    assert ledger["claims"][0]["l1_status"] == "verified"
    assert ledger["claims"][0]["l2_status"] == "supports"
    assert ledger["claims"][0]["verification_status"] == "verified"


def test_claim_ledger_resolves_line_qualified_evidence_ids_without_losing_l2_binding(tmp_path):
    import json

    from app.services.artifact_contract_v3 import materialize_claim_evidence_ledger
    from app.services.test_activity_contract import _behavior_claim_binding

    quote = "if (auth_method == NULL) {"
    card = {
        "evidence_id": "SRC-03",
        "file_path": "lib/iscsi/iscsi.c",
        "symbols": ["iscsi_op_login_rsp_handle_csg_bit"],
        "start_line": 1929,
        "end_line": 1947,
        "excerpt": quote,
        "sha256": "d" * 64,
    }
    evidence = [{
        "evidence_id": "SRC-03:L1943",
        "path": "lib/iscsi/iscsi.c",
        "symbol": "",
        "lines": "L1943",
        "quote": quote,
    }]
    claim = {
        "claim_id": "TC-03",
        "type": "source_code_behavior",
        "statement": "auth_method 为 NULL 时进入缺参错误处理。",
        "evidence": evidence,
    }
    binding = _behavior_claim_binding(
        claim_id=claim["claim_id"],
        claim_type=claim["type"],
        statement=claim["statement"],
        evidence=[{**evidence[0], "sha256": card["sha256"]}],
    )
    (tmp_path / "evidence_cards.json").write_text(json.dumps([card]), encoding="utf-8")
    (tmp_path / "sfmea.json").write_text(
        json.dumps([{"sfmea_id": "SFMEA-03", "technical_claims": [claim]}]),
        encoding="utf-8",
    )
    (tmp_path / "black_box_cases.json").write_text("[]", encoding="utf-8")
    (tmp_path / "behavior_claim_validation.json").write_text(
        json.dumps({"claims": [{
            "claim_id": "TC-03",
            "binding": binding,
            "status": "supports",
        }]}),
        encoding="utf-8",
    )

    ledger = materialize_claim_evidence_ledger(tmp_path)

    assert ledger["summary"] == {
        "total": 1,
        "verified": 1,
        "contradicted": 0,
        "insufficient": 0,
    }
    assert ledger["claims"][0]["l2_status"] == "supports"


def test_nested_agent_artifacts_materialize_the_v3_ledger_and_rapid_contract(tmp_path):
    """Real workflow steps write under agent_runs/<step>; contracts must find them."""
    import json

    from app.services.artifact_contract_v3 import (
        materialize_artifact_contract_v3_outputs,
        materialize_claim_evidence_ledger,
        validate_artifact_contract_v3_outputs,
    )

    agent_dir = tmp_path / "agent_runs" / "analyze"
    agent_dir.mkdir(parents=True)
    card = {
        "evidence_id": "EV-NESTED-001",
        "file_path": "lib/iscsi/login.c",
        "symbols": ["login"],
        "excerpt": "return SPDK_SUCCESS;",
        "sha256": "c" * 64,
    }
    claim = {
        "claim_id": "C-NESTED-001",
        "type": "source_behavior",
        "statement": "Login returns SPDK_SUCCESS.",
        "evidence": [{
            "evidence_id": "EV-NESTED-001",
            "path": "lib/iscsi/login.c",
            "symbol": "login",
            "quote": "return SPDK_SUCCESS;",
        }],
    }
    (agent_dir / "source_scope.json").write_text(
        json.dumps({"analysis_target": "iSCSI login", "files": ["lib/iscsi/login.c"]}),
        encoding="utf-8",
    )
    (agent_dir / "source_analysis.md").write_text(
        "# iSCSI login source analysis\n\nVerified source evidence is available below.",
        encoding="utf-8",
    )
    (agent_dir / "evidence_cards.json").write_text(json.dumps([card]), encoding="utf-8")
    (agent_dir / "sfmea.json").write_text(
        json.dumps([{"sfmea_id": "SFMEA-NESTED-001", "technical_claims": [claim]}]),
        encoding="utf-8",
    )
    (agent_dir / "black_box_cases.json").write_text("[]", encoding="utf-8")
    (tmp_path / "input_consumption.json").write_text("{}", encoding="utf-8")
    (tmp_path / "task_artifact_manifest.json").write_text("{}", encoding="utf-8")

    materialize_artifact_contract_v3_outputs(tmp_path, profile_id="rapid")
    ledger = materialize_claim_evidence_ledger(tmp_path)
    validation = validate_artifact_contract_v3_outputs(tmp_path, profile_id="rapid")

    assert ledger["summary"] == {
        "total": 1,
        "verified": 1,
        "contradicted": 0,
        "insufficient": 0,
    }
    assert validation["status"] == "passed"


def test_external_agent_evidence_ids_are_deterministically_bound_to_claims(tmp_path):
    import json

    from app.services.artifact_contract_v3 import enrich_external_agent_claim_bindings

    card = {
        "evidence_id": "E-1", "file_path": "lib/iscsi/login.c",
        "start_line": 7, "end_line": 7, "symbols": ["login"],
        "excerpt": "return SPDK_SUCCESS;",
    }
    (tmp_path / "evidence_cards.json").write_text(json.dumps([card]), encoding="utf-8")
    (tmp_path / "sfmea.json").write_text(json.dumps([{
        "sfmea_id": "RISK-1", "source_evidence": ["E-1:7"],
    }]), encoding="utf-8")
    (tmp_path / "black_box_cases.json").write_text(json.dumps([{
        "case_id": "CASE-1", "source_or_test_evidence": ["E-1"],
    }]), encoding="utf-8")

    assert enrich_external_agent_claim_bindings(tmp_path) == {
        "sfmea.json": 1, "black_box_cases.json": 1,
    }
    assert json.loads((tmp_path / "sfmea.json").read_text())[0]["technical_claims"][0]["evidence"][0]["path"] == "lib/iscsi/login.c"


def test_external_agent_legacy_evidence_id_is_rebound_to_canonical_card_by_file(tmp_path):
    import json

    from app.services.artifact_contract_v3 import enrich_external_agent_claim_bindings

    canonical_card = {
        "evidence_id": "SRC-01", "file_path": "lib/iscsi/login.c",
        "start_line": 7, "end_line": 7, "symbols": ["login"],
        "excerpt": "return SPDK_SUCCESS;",
    }
    (tmp_path / "evidence_cards.json").write_text(
        json.dumps([canonical_card]), encoding="utf-8"
    )
    (tmp_path / "sfmea.json").write_text(json.dumps([{
        "sfmea_id": "RISK-1", "file_path": "lib/iscsi/login.c",
        "source_evidence": ["AGENT-CARD:7"],
    }]), encoding="utf-8")
    (tmp_path / "black_box_cases.json").write_text(json.dumps([]), encoding="utf-8")

    assert enrich_external_agent_claim_bindings(tmp_path) == {"sfmea.json": 1}
    row = json.loads((tmp_path / "sfmea.json").read_text())[0]
    assert "SRC-01" in row["source_evidence"]
    evidence = row["technical_claims"][0]["evidence"][0]
    assert evidence["evidence_id"] == "SRC-01"
    assert evidence["quote"] == "return SPDK_SUCCESS;"


def test_external_agent_existing_claim_is_rebound_to_canonical_evidence(tmp_path):
    import json

    from app.services.artifact_contract_v3 import enrich_external_agent_claim_bindings

    canonical_card = {
        "evidence_id": "SRC-01", "file_path": "lib/iscsi/login.c",
        "start_line": 7, "end_line": 9, "symbols": ["login"],
        "excerpt": "if (invalid) { return SPDK_ERR; }",
    }
    (tmp_path / "evidence_cards.json").write_text(
        json.dumps([canonical_card]), encoding="utf-8"
    )
    (tmp_path / "sfmea.json").write_text(json.dumps([{
        "sfmea_id": "RISK-1",
        "source_evidence": ["AGENT-LOGIN:7-9"],
        "technical_claims": [{
            "claim_id": "CLAIM-1",
            "type": "implementation_fact",
            "statement": "登录路径会拒绝非法输入。",
            "evidence": [{
                "evidence_id": "AGENT-LOGIN",
                "path": "lib/iscsi/login.c",
                "lines": "7-9",
                "symbol": "login",
                "quote": "invented quote",
            }],
        }],
    }]), encoding="utf-8")
    (tmp_path / "black_box_cases.json").write_text("[]", encoding="utf-8")

    assert enrich_external_agent_claim_bindings(tmp_path) == {"sfmea.json": 1}

    claim = json.loads((tmp_path / "sfmea.json").read_text())[0]["technical_claims"][0]
    assert claim["statement"] == canonical_card["excerpt"]
    assert claim["semantic_statement"] == "登录路径会拒绝非法输入。"
    assert claim["evidence"] == [{
        "evidence_id": "SRC-01",
        "path": "lib/iscsi/login.c",
        "lines": "L7-L9",
        "symbol": "login",
        "quote": canonical_card["excerpt"],
    }]


def test_stage_progress_only_marks_artifacts_that_were_really_materialized(tmp_path):
    from app.services.test_activity_stage_specs import (
        project_test_activity_stage_progress,
    )

    (tmp_path / "agent_runs" / "analyze").mkdir(parents=True)
    (tmp_path / "input_snapshot.json").write_text("{}", encoding="utf-8")
    (tmp_path / "input_consumption.json").write_text("{}", encoding="utf-8")
    (tmp_path / "agent_runs" / "analyze" / "source_scope.json").write_text(
        "{}", encoding="utf-8"
    )

    progress = project_test_activity_stage_progress(
        artifact_dir=tmp_path,
        profile_id="deep",
    )
    stages = {item["stage_id"]: item for item in progress["stages"]}

    assert stages["input_scope"]["status"] == "completed"
    assert stages["source_evidence"]["status"] == "partial"
    assert stages["source_evidence"]["present_artifacts"] == ["source_scope.json"]
    assert stages["sfmea"]["status"] == "not_requested"


def test_live_stage_progress_tracks_running_stage_without_claiming_missing_artifacts(tmp_path):
    from app.services.test_activity_stage_specs import TestActivityStageProgressTracker

    tracker = TestActivityStageProgressTracker(tmp_path, profile_id="deep")
    tracker.update({"stage_id": "source_analysis", "status": "running"})

    progress = tracker.read()
    stages = {item["stage_id"]: item for item in progress["stages"]}

    assert stages["source_evidence"]["status"] == "running"
    assert stages["source_evidence"]["present_artifacts"] == []
    assert stages["sfmea"]["status"] == "pending"
    assert (tmp_path / "test_activity_stage_progress.json").is_file()


def test_deep_contract_materializes_named_deliverables_only_from_real_stage_outputs(tmp_path):
    import json

    from app.services.artifact_contract_v3 import materialize_artifact_contract_v3_outputs

    (tmp_path / "source_scope.json").write_text(
        json.dumps({"analysis_target": "iSCSI login", "files": ["lib/iscsi/login.c"]}),
        encoding="utf-8",
    )
    (tmp_path / "evidence_cards.json").write_text(
        json.dumps([{"file_path": "lib/iscsi/login.c", "symbols": ["login"]}]),
        encoding="utf-8",
    )
    (tmp_path / "flow_cards.json").write_text(
        json.dumps({"items": [{"title": "Login flow", "summary": "validate then respond"}]}),
        encoding="utf-8",
    )
    (tmp_path / "sfmea.json").write_text(
        json.dumps([{"failure_mode": "认证失败", "effect": "登录被拒绝"}]),
        encoding="utf-8",
    )
    (tmp_path / "black_box_cases.json").write_text(
        json.dumps([{"title": "错误凭据", "expected_result": "登录失败"}]),
        encoding="utf-8",
    )

    written = materialize_artifact_contract_v3_outputs(tmp_path, profile_id="deep")

    assert {"完整分析报告.md", "开发给测试讲代码.md", "流程状态资源与异常传播.md", "风险点与SFMEA.md", "黑盒测试设计.md"} <= set(written)
    assert "iSCSI login" in (tmp_path / "完整分析报告.md").read_text(encoding="utf-8")
    explanation = (tmp_path / "开发给测试讲代码.md").read_text(encoding="utf-8")
    for heading in (
        "1. 这里是干什么的",
        "2. 外部怎么触发",
        "3. 正常流程怎么走",
        "4. 分支怎么进入",
        "5. 状态怎么变化",
        "6. 资源怎么使用和释放",
        "7. 超时、重试、取消和恢复",
        "8. 并发和关键时序窗口",
        "9. 异常传播和潜伏故障",
        "10. 风险点",
        "11. 黑盒怎么测",
        "12. 源码追溯和未决项",
    ):
        assert heading in explanation
    assert "当前证据未直接覆盖" in explanation


def test_deep_flow_state_resource_delivery_includes_all_governed_ledgers(tmp_path):
    import json

    from app.services.artifact_contract_v3 import materialize_artifact_contract_v3_outputs

    (tmp_path / "source_scope.json").write_text(json.dumps({"analysis_target": "iSCSI login"}), encoding="utf-8")
    (tmp_path / "evidence_cards.json").write_text(json.dumps([{"evidence_id": "SRC-01", "file_path": "lib/iscsi/login.c"}]), encoding="utf-8")
    (tmp_path / "flow_cards.json").write_text(json.dumps({"items": [{"flow_id": "FLOW-LOGIN-01", "title": "登录流程", "abnormal_paths": ["认证失败"]}]}), encoding="utf-8")
    (tmp_path / "sfmea.json").write_text(json.dumps([{"sfmea_id": "SFMEA-01", "failure_mode": "认证失败"}]), encoding="utf-8")
    (tmp_path / "black_box_cases.json").write_text(json.dumps([{"case_id": "BB-01", "title": "错误凭据"}]), encoding="utf-8")
    (tmp_path / "branch_disposition.json").write_text(json.dumps({"items": [{"condition": "认证失败", "disposition": "need_verify"}]}), encoding="utf-8")
    (tmp_path / "state_transition_disposition.json").write_text(json.dumps({"items": [{"state": "LOGIN", "transitions": [{"text": "认证通过后进入 FULL_FEATURE"}]}]}), encoding="utf-8")
    (tmp_path / "resource_lifecycle_disposition.json").write_text(json.dumps({"items": [{"name": "连接资源", "allocation": "建立连接时申请", "normal_release": "注销时释放"}]}), encoding="utf-8")
    (tmp_path / "error_propagation_chains.json").write_text(json.dumps({"items": [{"trigger": "认证失败", "downstream_effect": "返回公开登录拒绝响应"}]}), encoding="utf-8")

    materialize_artifact_contract_v3_outputs(tmp_path, profile_id="deep")

    delivery = (tmp_path / "流程状态资源与异常传播.md").read_text(encoding="utf-8")
    for heading in ("分支与异常触发", "状态迁移", "资源生命周期与耗尽边界", "超时、重试与恢复", "异常传播与外部观测"):
        assert heading in delivery
    assert "认证通过后进入 FULL_FEATURE" in delivery
    assert "建立连接时申请" in delivery
    assert "返回公开登录拒绝响应" in delivery


def test_deep_contract_materializes_task_deliverables_from_nested_agent_stage_outputs(tmp_path):
    """Stage quality gates audit the task root before final task collection."""
    import json

    from app.services.artifact_contract_v3 import materialize_artifact_contract_v3_outputs

    stage_dir = tmp_path / "agent_runs" / "analyze"
    stage_dir.mkdir(parents=True)
    (stage_dir / "source_scope.json").write_text(
        json.dumps({"analysis_target": "iSCSI login"}), encoding="utf-8"
    )
    (stage_dir / "evidence_cards.json").write_text(
        json.dumps([{"evidence_id": "SRC-01", "file_path": "lib/iscsi/login.c"}]),
        encoding="utf-8",
    )
    (stage_dir / "flow_cards.json").write_text(
        json.dumps({"items": [{"flow_id": "FLOW-01", "title": "Login flow"}]}),
        encoding="utf-8",
    )
    (stage_dir / "sfmea.json").write_text(
        json.dumps([{"sfmea_id": "SFMEA-01", "failure_mode": "认证失败"}]),
        encoding="utf-8",
    )
    (stage_dir / "black_box_cases.json").write_text(
        json.dumps([{"case_id": "BB-01", "title": "错误凭据"}]), encoding="utf-8"
    )

    written = materialize_artifact_contract_v3_outputs(tmp_path, profile_id="deep")

    assert "完整分析报告.md" in written
    assert (tmp_path / "完整分析报告.md").is_file()
    assert (tmp_path / "artifact_alignment_audit.json").is_file()


def test_rapid_contract_materializes_flow_delivery_for_alignment_audit(tmp_path):
    import json

    from app.services.artifact_contract_v3 import materialize_artifact_contract_v3_outputs

    (tmp_path / "source_scope.json").write_text(
        json.dumps({"analysis_target": "iSCSI login"}), encoding="utf-8"
    )
    (tmp_path / "evidence_cards.json").write_text(
        json.dumps([{"evidence_id": "SRC-01", "file_path": "lib/iscsi/login.c"}]),
        encoding="utf-8",
    )
    (tmp_path / "flow_cards.json").write_text(
        json.dumps({"items": [{"flow_id": "FLOW-LOGIN-01", "title": "登录流程"}]}),
        encoding="utf-8",
    )
    (tmp_path / "sfmea.json").write_text(
        json.dumps([{"sfmea_id": "SFMEA-LOGIN-01", "failure_mode": "认证失败"}]),
        encoding="utf-8",
    )
    (tmp_path / "black_box_cases.json").write_text(
        json.dumps([{"case_id": "BB-ISC-01", "title": "错误 CHAP 凭据"}]),
        encoding="utf-8",
    )

    written = materialize_artifact_contract_v3_outputs(tmp_path, profile_id="rapid")
    audit = json.loads((tmp_path / "artifact_alignment_audit.json").read_text(encoding="utf-8"))

    assert "流程状态资源与异常传播.md" in written
    assert "[FLOW-LOGIN-01]" in (tmp_path / "流程状态资源与异常传播.md").read_text(encoding="utf-8")
    assert audit["status"] == "passed"


def test_rapid_contract_keeps_every_evidence_card_id_in_the_tester_report(tmp_path):
    """Rapid delivery must not silently truncate IDs required by its audit."""
    import json

    from app.services.artifact_contract_v3 import materialize_artifact_contract_v3_outputs

    (tmp_path / "source_scope.json").write_text(
        json.dumps({"analysis_target": "iSCSI login"}), encoding="utf-8"
    )
    cards = [
        {"evidence_id": f"FLOW-EDGE-{index:03d}", "file_path": "lib/iscsi/iscsi.c"}
        for index in range(1, 25)
    ]
    (tmp_path / "evidence_cards.json").write_text(json.dumps(cards), encoding="utf-8")

    materialize_artifact_contract_v3_outputs(tmp_path, profile_id="rapid")

    report = (tmp_path / "快速分析报告.md").read_text(encoding="utf-8")
    audit = json.loads((tmp_path / "artifact_alignment_audit.json").read_text(encoding="utf-8"))
    assert "FLOW-EDGE-024" in report
    assert audit["status"] == "passed"


def test_artifact_alignment_audit_keeps_structured_ids_and_hashes_in_markdown(tmp_path):
    import json

    from app.services.artifact_contract_v3 import (
        materialize_artifact_alignment_audit,
        materialize_artifact_contract_v3_outputs,
        validate_artifact_contract_v3_outputs,
    )

    (tmp_path / "source_scope.json").write_text(
        json.dumps({"analysis_target": "iSCSI login"}), encoding="utf-8"
    )
    (tmp_path / "evidence_cards.json").write_text(
        json.dumps([{
            "evidence_id": "SRC-09",
            "file_path": "test/iscsi_tgt/chap/chap_common.sh",
            "start_line": 82,
            "end_line": 99,
            "symbols": ["config_chap_credentials_for_target"],
        }]),
        encoding="utf-8",
    )
    (tmp_path / "flow_cards.json").write_text(
        json.dumps({"items": [{"id": "FLOW-LOGIN-01", "title": "登录流程"}]}),
        encoding="utf-8",
    )
    (tmp_path / "sfmea.json").write_text(
        json.dumps([{"sfmea_id": "SFMEA-LOGIN-01", "failure_mode": "认证失败"}]),
        encoding="utf-8",
    )
    (tmp_path / "black_box_cases.json").write_text(
        json.dumps([{"case_id": "BB-ISC-01", "title": "错误 CHAP 凭据"}]),
        encoding="utf-8",
    )

    materialize_artifact_contract_v3_outputs(tmp_path, profile_id="deep")
    audit = materialize_artifact_alignment_audit(tmp_path, profile_id="deep")

    assert audit["status"] == "passed"
    assert all(item["json_sha256"] and item["markdown_sha256"] for item in audit["pairs"])
    assert all(not item["missing_ids"] for item in audit["pairs"])
    assert "[SRC-09]" in (tmp_path / "完整分析报告.md").read_text(encoding="utf-8")
    assert "[FLOW-LOGIN-01]" in (tmp_path / "流程状态资源与异常传播.md").read_text(encoding="utf-8")
    assert "[BB-ISC-01]" in (tmp_path / "黑盒测试设计.md").read_text(encoding="utf-8")

    case_delivery = tmp_path / "黑盒测试设计.md"
    case_delivery.write_text(case_delivery.read_text(encoding="utf-8").replace("BB-ISC-01", ""), encoding="utf-8")
    failed_audit = materialize_artifact_alignment_audit(tmp_path, profile_id="deep")

    assert failed_audit["status"] == "blocked"
    assert failed_audit["pairs"][-1]["missing_ids"] == ["BB-ISC-01"]
    validation = validate_artifact_contract_v3_outputs(tmp_path, profile_id="deep")
    assert "artifact_alignment_audit.json" in validation["malformed_required"]


def test_deep_contract_keeps_sfmea_hypotheses_distinct_from_observed_defects(tmp_path):
    import json

    from app.services.artifact_contract_v3 import materialize_artifact_contract_v3_outputs

    (tmp_path / "sfmea.json").write_text(
        json.dumps(
            [
                {
                    "sfmea_id": "SFMEA-01",
                    "failure_mode": "登录超时后的连接清理延迟",
                    "risk_status": "test_hypothesis",
                    "evidence_interpretation": "源码仅证明状态切换；是否延迟回收须通过故障注入验证。",
                    "cause": "故障注入假设：poller 未及时处理 EXITING 状态。",
                    "effect": "潜在连接资源残留。",
                    "source_evidence": ["SRC-02"],
                    "technical_claims": [
                        {
                            "statement": "conn->state = ISCSI_CONN_STATE_EXITING;",
                            "evidence": [
                                {
                                    "evidence_id": "SRC-02:L153",
                                    "path": "lib/iscsi/conn.c",
                                    "lines": "L153",
                                    "quote": "conn->state = ISCSI_CONN_STATE_EXITING;",
                                }
                            ],
                        }
                    ],
                    "mitigation": "注入登录超时并观测连接数回落。",
                    "test_mapping": "使用公开 initiator 制造登录超时。",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    written = materialize_artifact_contract_v3_outputs(tmp_path, profile_id="deep")

    content = (tmp_path / "风险点与SFMEA.md").read_text(encoding="utf-8")
    assert "风险假设，待故障注入验证" in content
    assert "不是已观测缺陷" in content
    assert "conn->state = ISCSI_CONN_STATE_EXITING;" in content
    assert "lib/iscsi/conn.c:L153" in content
    assert "若该假设发生的潜在影响：潜在连接资源残留。" in content
    assert "SFMEA-01" in content
    assert "风险点与SFMEA.md" in written


def test_deep_contract_validation_blocks_missing_named_deliverables(tmp_path):
    from app.services.artifact_contract_v3 import (
        validate_artifact_contract_v3_outputs,
    )

    result = validate_artifact_contract_v3_outputs(tmp_path, profile_id="deep")

    assert result["status"] == "blocked"
    assert "完整分析报告.md" in result["missing_required"]


def test_deep_contract_validation_rejects_incomplete_developer_explanation(tmp_path):
    from app.services.artifact_contract_v3 import (
        default_artifact_contract_v3,
        validate_artifact_contract_v3_outputs,
    )

    for item in default_artifact_contract_v3(profile_id="deep")["artifacts"]:
        if item["required"]:
            (tmp_path / item["artifact"]).write_text(
                "{}" if item["format"] == "json" else "# placeholder\n",
                encoding="utf-8",
            )
    result = validate_artifact_contract_v3_outputs(tmp_path, profile_id="deep")

    assert result["status"] == "blocked"
    assert "开发给测试讲代码.md" in result["malformed_required"]
    assert "1. 这里是干什么的" in result["malformed_required"]["开发给测试讲代码.md"]
