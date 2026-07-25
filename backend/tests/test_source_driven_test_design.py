from __future__ import annotations

import json


def _source_pack() -> dict:
    return {
        "analysis_target": "SPDK iSCSI Login",
        "repo_revision": "abc123",
        "source_scope": {"repo": "/repo/spdk", "evidence_gaps": []},
        "evidence_cards": [
            {
                "evidence_id": "SRC-001",
                "classification": "source",
                "file_path": "lib/iscsi/iscsi.c",
                "start_line": 100,
                "end_line": 180,
                "symbols": ["iscsi_pdu_payload_op_login"],
                "matched_terms": ["login", "conn->state", "cmd_sn", "max_cmds"],
                "excerpt": "if (conn->state == EXITING) return;\nif (active_cmds >= max_cmds) return;\ncmd = iscsi_get_pdu(conn);\n",
                "sha256": "a" * 64,
            },
            {
                "evidence_id": "TEST-001",
                "classification": "test",
                "file_path": "test/iscsi_tgt/chap/chap.sh",
                "start_line": 1,
                "end_line": 40,
                "symbols": ["run_test"],
                "matched_terms": ["CHAP", "login"],
                "excerpt": "iscsiadm --mode discovery\n",
                "sha256": "b" * 64,
            },
        ],
        "input_materials": {
            "materials": [
                {"input_id": "design_doc", "sha256": "doc-sha", "summary": "CHAP 设计约束"}
            ]
        },
        "mcp": {
            "gitnexus_summary": "login call graph",
            "cgc_summary": "state and branch summary",
        },
    }


def _flow_pack() -> dict:
    return {
        "provider_status": [
            {"provider": "gitnexus", "status": "used"},
            {"provider": "cgc", "status": "used"},
        ],
        "entry_points": [
            {
                "evidence_id": "FLOW-ENTRY-001",
                "file_path": "lib/iscsi/iscsi.c",
                "symbol": "iscsi_pdu_payload_op_login",
                "start_line": 100,
                "end_line": 120,
            }
        ],
        "call_edges": [
            {
                "evidence_id": "FLOW-EDGE-001",
                "file_path": "lib/iscsi/iscsi.c",
                "from_symbol": "iscsi_pdu_payload_op_login",
                "to_symbol": "iscsi_op_login_response",
                "start_line": 130,
                "end_line": 130,
            }
        ],
        "state_objects": [
            {
                "evidence_id": "FLOW-STATE-001",
                "file_path": "lib/iscsi/iscsi.c",
                "symbol": "conn_state",
                "start_line": 110,
                "end_line": 110,
            }
        ],
        "state_transitions": [
            {
                "evidence_id": "FLOW-TRANSITION-001",
                "file_path": "lib/iscsi/iscsi.c",
                "symbol": "iscsi_pdu_payload_op_login",
                "text": "conn->state = ISCSI_CONN_STATE_RUNNING",
                "start_line": 150,
                "end_line": 150,
            }
        ],
        "conditions": [
            {
                "evidence_id": "FLOW-COND-001",
                "file_path": "lib/iscsi/iscsi.c",
                "symbol": "iscsi_pdu_payload_op_login",
                "text": "if (conn->state == EXITING)",
                "start_line": 112,
                "end_line": 112,
            }
        ],
        "error_paths": [
            {
                "evidence_id": "FLOW-ERROR-001",
                "file_path": "lib/iscsi/iscsi.c",
                "symbol": "iscsi_pdu_payload_op_login",
                "text": "login failed",
                "start_line": 160,
                "end_line": 160,
            }
        ],
        "cleanup_paths": [
            {
                "evidence_id": "FLOW-CLEANUP-001",
                "file_path": "lib/iscsi/conn.c",
                "symbol": "_iscsi_conn_destruct",
                "text": "free pending pdu",
                "start_line": 300,
                "end_line": 300,
            }
        ],
        "recovery_paths": [],
        "related_tests": [
            {
                "evidence_id": "FLOW-TEST-001",
                "file_path": "test/iscsi_tgt/chap/chap.sh",
                "symbol": "run_test",
                "start_line": 1,
                "end_line": 40,
            }
        ],
        "evidence_gaps": [],
    }


def _outline() -> dict:
    return {
        "main_flows": [
            {
                "id": "main-flow-01",
                "name": "iSCSI Login",
                "root_symbol": "iscsi_pdu_payload_op_login",
                "steps": [
                    {
                        "step": 1,
                        "action": "处理 Login PDU",
                        "from_symbol": "iscsi_pdu_payload_op_login",
                        "to_symbol": "iscsi_op_login_response",
                        "evidence_ids": ["FLOW-EDGE-001"],
                    }
                ],
            }
        ],
        "evidence_gaps": [],
    }


def _sfmea() -> list[dict]:
    return [
        {
            "sfmea_id": "SFMEA-001",
            "failure_mode": "PDU/cmd 资源未释放",
            "cause": "异常 Login 路径遗漏释放",
            "effect": "长时间运行后连接拒绝",
            "detection": "监控可用 cmd/PDU 数量和登录失败率",
            "severity": 9,
            "occurrence": 4,
            "detection_score": 6,
            "rpn": 216,
            "mitigation": "执行 N、2N 容量与恢复后重申请测试",
            "source_evidence": ["FLOW-CLEANUP-001"],
            "test_mapping": "CASE-001",
        }
    ]


def _cases() -> list[dict]:
    return [
        {
            "case_id": "CASE-001",
            "test_dimension": "资源耗尽与恢复",
            "scenario_name": "达到 cmd/PDU 容量后释放并重新登录",
            "preconditions": ["启动 iSCSI target 并配置隔离测试盘"],
            "steps": ["通过 iSCSI Initiator 并发建立 N 个登录请求", "释放连接后再次登录"],
            "expected_result": "容量内登录成功，释放后新登录恢复成功",
            "observability": ["登录响应", "连接状态", "资源指标"],
            "failure_diagnostics": ["target 日志", "连接统计"],
            "mapped_test_dir": "test/iscsi_tgt",
            "source_or_test_evidence": ["FLOW-CLEANUP-001", "FLOW-TEST-001"],
            "risk_ids": ["SFMEA-001"],
        }
    ]


def _ready_judge_artifacts() -> dict:
    from app.services.source_driven_test_design import build_source_driven_test_design

    artifacts = build_source_driven_test_design(
        source_pack=_source_pack(),
        flow_pack=_flow_pack(),
        flow_outline=_outline(),
        sfmea=_sfmea(),
        black_box_cases=_cases(),
    )
    for disposition_name in (
        "branch_disposition.json",
        "state_transition_disposition.json",
        "resource_lifecycle_disposition.json",
    ):
        for row in artifacts[disposition_name]["items"]:
            row["disposition"] = "retain"
    for row in artifacts["model_applicability.json"]["items"]:
        row["applicable"] = False
        row["status"] = "not_applicable"
    for row in artifacts["scenario_candidates.json"]["sources"]:
        row["applicable"] = False
        row["status"] = "not_applicable"
    return artifacts


def test_v2_builder_materializes_complete_inventory_dispositions_and_traceability():
    from app.services.source_driven_test_design import build_source_driven_test_design

    artifacts = build_source_driven_test_design(
        source_pack=_source_pack(),
        flow_pack=_flow_pack(),
        flow_outline=_outline(),
        sfmea=_sfmea(),
        black_box_cases=_cases(),
    )

    required = {
        "entrypoints.json",
        "flows.json",
        "states.json",
        "resources.json",
        "model_applicability.json",
        "flow_cards.json",
        "developer_explanation_coverage.json",
        "branch_disposition.json",
        "state_transition_disposition.json",
        "resource_lifecycle_disposition.json",
        "error_propagation_chains.json",
        "evidence_consumption_ledger.json",
        "scenario_candidates.json",
        "risk_register.json",
        "blackbox_control_observation.json",
        "test_basis.json",
        "test_scenarios.json",
        "test_flows.json",
        "traceability_matrix.json",
        "judge_report.json",
    }
    assert required.issubset(artifacts)
    assert artifacts["entrypoints.json"]["items"][0]["symbol"] == "iscsi_pdu_payload_op_login"
    assert artifacts["branch_disposition.json"]["items"][0]["disposition"] in {
        "retain", "merge_into", "covered_by_other", "not_testable", "not_applicable", "blocked", "need_verify"
    }
    resource_text = json.dumps(artifacts["resources.json"], ensure_ascii=False).lower()
    assert "cmd" in resource_text or "pdu" in resource_text
    lifecycle_text = json.dumps(artifacts["resource_lifecycle_disposition.json"], ensure_ascii=False)
    assert "2N" in lifecycle_text
    applicability = {
        item["model"]: item["applicable"]
        for item in artifacts["model_applicability.json"]["items"]
    }
    assert applicability["numeric_boundary_and_wrap"] is True
    assert artifacts["traceability_matrix.json"]["orphan_case_ids"] == []
    assert artifacts["traceability_matrix.json"]["high_risk_unmapped_ids"] == []
    assert artifacts["traceability_matrix.json"]["links"][0]["risk_ids"] == ["SFMEA-001"]


def test_judge_never_reports_ready_or_fact_score_100_when_facts_are_not_checked():
    from app.services.source_driven_test_design import build_judge_report

    report = build_judge_report(
        artifacts={"traceability_matrix.json": {"orphan_case_ids": []}},
        fact_verification={"status": "not_checked", "total": 0, "verified": 0},
    )

    assert report["status"] == "BLOCKED"
    assert report["axes"]["facts"]["status"] == "not_checked"
    assert report["axes"]["facts"]["score"] is None
    assert report["ready"] is False


def test_judge_blocks_empty_ledgers_unresolved_dispositions_and_incomplete_eight_sources():
    from app.services.source_driven_test_design import (
        SOURCE_DRIVEN_V2_ARTIFACTS,
        build_judge_report,
    )

    artifacts = {
        name: {"schema_version": "source-driven-test-design-v2", "items": []}
        for name in SOURCE_DRIVEN_V2_ARTIFACTS
        if name != "judge_report.json"
    }
    artifacts["model_applicability.json"] = {
        "items": [{"model": "resource", "applicable": True, "status": "applicable"}]
    }
    artifacts["resource_lifecycle_disposition.json"] = {
        "items": [{"id": "R-1", "disposition": "need_verify"}]
    }
    artifacts["traceability_matrix.json"] = {
        "links": [],
        "orphan_case_ids": [],
        "high_risk_unmapped_ids": [],
    }

    report = build_judge_report(
        artifacts=artifacts,
        fact_verification={
            "status": "passed",
            "total": 1,
            "verified": 1,
            "behavior_validator_independent": True,
        },
    )

    assert report["status"] == "BLOCKED"
    assert report["ready"] is False
    assert report["axes"]["structure"]["status"] == "blocked"
    assert report["axes"]["coverage_disposition"]["status"] == "blocked"


def test_judge_blocks_unknown_risks_and_unresolved_evidence_references():
    from app.services.source_driven_test_design import build_judge_report

    artifacts = _ready_judge_artifacts()
    artifacts["traceability_matrix.json"]["unknown_risk_ids"] = ["FAKE-RISK"]
    artifacts["traceability_matrix.json"]["links"][0]["unresolved_evidence_refs"] = [
        "FAKE-EVIDENCE"
    ]

    report = build_judge_report(
        artifacts=artifacts,
        fact_verification={
            "status": "passed",
            "total": 1,
            "verified": 1,
            "behavior_validator_independent": True,
        },
    )

    assert report["ready"] is False
    issues = report["axes"]["coverage_disposition"]["issues"]
    assert "unknown_risk_id:FAKE-RISK" in issues
    assert "unresolved_evidence_ref:FAKE-EVIDENCE" in issues


def test_judge_keeps_explicit_coverage_verification_work_as_warning():
    from app.services.source_driven_test_design import build_judge_report

    artifacts = _ready_judge_artifacts()
    artifacts["branch_disposition.json"]["items"][0]["disposition"] = "need_verify"

    report = build_judge_report(
        artifacts=artifacts,
        fact_verification={
            "status": "passed",
            "total": 1,
            "verified": 1,
            "behavior_validator_independent": True,
        },
    )

    assert report["ready"] is True
    assert report["status"] == "READY_WITH_WARNINGS"
    coverage = report["axes"]["coverage_disposition"]
    assert coverage["status"] == "warning"
    assert "branch_disposition.json:FLOW-COND-001:need_verify" in coverage["warnings"]


def test_traceability_resolves_line_qualified_and_display_evidence_references():
    from app.services.source_driven_test_design import build_source_driven_test_design

    cases = _cases()
    cases[0]["source_or_test_evidence"] = [
        "SRC-001:L101",
        "lib/iscsi/iscsi.c (SRC-001:L101)",
    ]
    artifacts = build_source_driven_test_design(
        source_pack=_source_pack(),
        flow_pack=_flow_pack(),
        flow_outline=_outline(),
        sfmea=_sfmea(),
        black_box_cases=cases,
    )

    link = artifacts["traceability_matrix.json"]["links"][0]
    assert link["verified_evidence_refs"] == cases[0]["source_or_test_evidence"]
    assert link["unresolved_evidence_refs"] == []


def test_traceability_resolves_human_readable_path_ranges_only_inside_verified_card():
    from app.services.source_driven_test_design import build_source_driven_test_design

    cases = _cases()
    cases[0]["source_or_test_evidence"] = [
        "lib/iscsi/iscsi.c:101-120",
        "test/iscsi_tgt/chap/chap.sh:1-40",
    ]
    artifacts = build_source_driven_test_design(
        source_pack=_source_pack(),
        flow_pack=_flow_pack(),
        flow_outline=_outline(),
        sfmea=_sfmea(),
        black_box_cases=cases,
    )

    link = artifacts["traceability_matrix.json"]["links"][0]
    assert link["verified_evidence_refs"] == cases[0]["source_or_test_evidence"]
    assert link["unresolved_evidence_refs"] == []

    cases[0]["source_or_test_evidence"] = ["lib/iscsi/iscsi.c:99-120"]
    invalid = build_source_driven_test_design(
        source_pack=_source_pack(),
        flow_pack=_flow_pack(),
        flow_outline=_outline(),
        sfmea=_sfmea(),
        black_box_cases=cases,
    )
    assert invalid["traceability_matrix.json"]["links"][0]["unresolved_evidence_refs"] == [
        "lib/iscsi/iscsi.c:99-120"
    ]


def test_traceability_accepts_display_path_only_when_same_case_has_verified_claim():
    from app.services.source_driven_test_design import build_source_driven_test_design

    cases = _cases()
    cases[0]["risk_ids"] = []
    cases[0]["source_or_test_evidence"] = ["lib/iscsi/iscsi.c"]
    cases[0]["technical_claims"] = [{
        "claim_id": "TC-001",
        "type": "source_anchor",
        "statement": "if (conn->state == EXITING) return;",
        "evidence": [{
            "evidence_id": "SRC-001:L101",
            "path": "lib/iscsi/iscsi.c",
            "lines": "L101",
            "quote": "if (conn->state == EXITING) return;",
            "symbol": "iscsi_pdu_payload_op_login",
        }],
    }]

    artifacts = build_source_driven_test_design(
        source_pack=_source_pack(),
        flow_pack=_flow_pack(),
        flow_outline=_outline(),
        sfmea=_sfmea(),
        black_box_cases=cases,
    )

    link = artifacts["traceability_matrix.json"]["links"][0]
    assert link["verified_evidence_refs"] == ["lib/iscsi/iscsi.c", "SRC-001:L101"]
    assert link["unresolved_evidence_refs"] == []
    assert artifacts["traceability_matrix.json"]["orphan_case_ids"] == []

    cases[0]["technical_claims"][0]["evidence"][0]["quote"] = "fabricated quote"
    invalid = build_source_driven_test_design(
        source_pack=_source_pack(),
        flow_pack=_flow_pack(),
        flow_outline=_outline(),
        sfmea=_sfmea(),
        black_box_cases=cases,
    )
    assert invalid["traceability_matrix.json"]["links"][0]["unresolved_evidence_refs"] == [
        "lib/iscsi/iscsi.c"
    ]


def test_traceability_accepts_card_display_label_only_with_same_card_claim():
    from app.services.source_driven_test_design import build_source_driven_test_design

    cases = _cases()
    cases[0]["risk_ids"] = []
    cases[0]["source_or_test_evidence"] = ["SRC-001:ISCSI_LOGIN_ACCEPT=0x00"]
    cases[0]["technical_claims"] = [{
        "claim_id": "TC-001",
        "type": "source_anchor",
        "statement": "if (conn->state == EXITING) return;",
        "evidence": [{
            "evidence_id": "SRC-001:L101",
            "path": "lib/iscsi/iscsi.c",
            "lines": "L101",
            "quote": "if (conn->state == EXITING) return;",
            "symbol": "iscsi_pdu_payload_op_login",
        }],
    }]

    artifacts = build_source_driven_test_design(
        source_pack=_source_pack(),
        flow_pack=_flow_pack(),
        flow_outline=_outline(),
        sfmea=_sfmea(),
        black_box_cases=cases,
    )

    link = artifacts["traceability_matrix.json"]["links"][0]
    assert link["unresolved_evidence_refs"] == []

    cases[0]["technical_claims"][0]["evidence"][0]["evidence_id"] = "SRC-OTHER:L101"
    invalid = build_source_driven_test_design(
        source_pack=_source_pack(),
        flow_pack=_flow_pack(),
        flow_outline=_outline(),
        sfmea=_sfmea(),
        black_box_cases=cases,
    )
    assert invalid["traceability_matrix.json"]["links"][0]["unresolved_evidence_refs"] == [
        "SRC-001:ISCSI_LOGIN_ACCEPT=0x00"
    ]


def test_delivery_refresh_rebuilds_traceability_from_normalized_final_artifacts(tmp_path: Path):
    from app.services.source_driven_test_design import (
        build_source_driven_test_design,
        refresh_source_driven_delivery_governance,
    )

    cases = _cases()
    cases[0]["source_or_test_evidence"] = ["lib/iscsi/iscsi.c"]
    cases[0]["technical_claims"] = [{
        "claim_id": "TC-001", "type": "source_anchor",
        "statement": "if (conn->state == EXITING) return;",
        "evidence": [{"evidence_id": "SRC-001:L101", "path": "lib/iscsi/iscsi.c", "lines": "L101", "quote": "if (conn->state == EXITING) return;", "symbol": "iscsi_pdu_payload_op_login"}],
    }]
    artifacts = build_source_driven_test_design(
        source_pack=_source_pack(), flow_pack=_flow_pack(), flow_outline=_outline(),
        sfmea=_sfmea(), black_box_cases=cases,
    )
    for name, payload in artifacts.items():
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "black_box_cases.json").write_text(json.dumps(cases), encoding="utf-8")
    (tmp_path / "evidence_cards.json").write_text(json.dumps(_source_pack()["evidence_cards"]), encoding="utf-8")
    judge = refresh_source_driven_delivery_governance(tmp_path)
    assert judge["axes"]["coverage_disposition"]["status"] != "blocked"


def test_delivery_refresh_discards_internal_sfmea_tombstones(tmp_path: Path):
    from app.services.source_driven_test_design import (
        refresh_source_driven_delivery_governance,
    )

    artifacts = _ready_judge_artifacts()
    for name, payload in artifacts.items():
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")
    sfmea_path = tmp_path / "sfmea.json"
    rows = _sfmea()
    rows.append({"sfmea_id": "SFMEA-DELETE", "_delete": True})
    sfmea_path.write_text(json.dumps(rows), encoding="utf-8")

    refresh_source_driven_delivery_governance(tmp_path)

    final_rows = json.loads(sfmea_path.read_text(encoding="utf-8"))
    assert all(row.get("_delete") is not True for row in final_rows)


def test_final_fact_verification_accepts_l1_verified_source_anchor_without_l2(tmp_path: Path):
    from app.services.source_driven_test_design import refresh_source_driven_delivery_governance

    artifacts = _ready_judge_artifacts()
    for name, payload in artifacts.items():
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "independent_fact_verification.json").write_text(
        json.dumps({
            "claims": [{
                "claim_id": "SRC-CLAIM-1",
                "type": "source_anchor",
                "status": "verified",
            }],
        }),
        encoding="utf-8",
    )
    (tmp_path / "behavior_claim_validation.json").write_text(
        json.dumps({
            "status": "completed",
            "validator": {"independent": True},
            "claims": [],
        }),
        encoding="utf-8",
    )

    refreshed = refresh_source_driven_delivery_governance(tmp_path)

    assert refreshed["axes"]["facts"]["status"] == "passed"
    final = json.loads((tmp_path / "final_fact_verification.json").read_text())
    assert final["claims"][0]["status"] == "verified"
    assert final["claims"][0]["behavior_status"] == "not_required"


def test_generic_request_connection_words_do_not_enable_finite_resource_boundaries():
    from app.services.source_driven_test_design import build_source_driven_test_design

    source = _source_pack()
    source["evidence_cards"] = [{
        "evidence_id": "SRC-GENERIC",
        "classification": "source",
        "file_path": "lib/example.c",
        "symbols": ["handle_request"],
        "matched_terms": ["request", "task", "connection", "count"],
        "excerpt": "int handle_request(struct connection *conn) { return conn != 0; }",
        "sha256": "c" * 64,
    }]
    flow = {**_flow_pack(), "cleanup_paths": [], "error_paths": [], "recovery_paths": []}

    artifacts = build_source_driven_test_design(
        source_pack=source,
        flow_pack=flow,
        flow_outline=_outline(),
        sfmea=_sfmea(),
        black_box_cases=_cases(),
    )

    assert artifacts["resources.json"]["items"] == []
    numeric = next(
        item for item in artifacts["model_applicability.json"]["items"]
        if item["model"] == "numeric_boundary_and_wrap"
    )
    assert numeric["applicable"] is False


def test_flow_and_scenario_links_only_use_related_evidence():
    from app.services.source_driven_test_design import build_source_driven_test_design

    flow = _flow_pack()
    flow["error_paths"].append({
        "evidence_id": "FLOW-ERROR-UNRELATED",
        "file_path": "lib/nvmf/tcp.c",
        "symbol": "nvmf_tcp_qpair_destroy",
        "text": "unrelated transport failure",
    })
    flow["call_edges"].append({
        "evidence_id": "FLOW-EDGE-UNRELATED",
        "file_path": "lib/blob/blobstore.c",
        "from_symbol": "blob_open",
        "to_symbol": "blob_close",
    })

    artifacts = build_source_driven_test_design(
        source_pack=_source_pack(),
        flow_pack=flow,
        flow_outline=_outline(),
        sfmea=_sfmea(),
        black_box_cases=_cases(),
    )

    flow_card = artifacts["flow_cards.json"]["items"][0]
    assert "FLOW-ERROR-UNRELATED" not in flow_card["error_chain_refs"]
    concurrency = [
        item for item in artifacts["scenario_candidates.json"]["items"]
        if item["source"] == "concurrency"
    ]
    assert concurrency == []
    risk = artifacts["risk_register.json"]["items"][0]
    assert all(
        candidate_id.startswith(("SCN-RESOURCE-", "SCN-NUMERIC-BOUNDARY-AND-WRAP-"))
        for candidate_id in risk["scenario_candidate_ids"]
    )


def test_flow_card_without_related_error_evidence_is_partial_not_ready():
    from app.services.source_driven_test_design import build_source_driven_test_design

    flow = _flow_pack()
    flow["error_paths"] = []
    artifacts = build_source_driven_test_design(
        source_pack=_source_pack(),
        flow_pack=flow,
        flow_outline=_outline(),
        sfmea=_sfmea(),
        black_box_cases=_cases(),
    )

    card = artifacts["flow_cards.json"]["items"][0]
    assert card["status"] == "PARTIAL"
    assert any("异常路径" in gap for gap in artifacts["flow_cards.json"]["gaps"])


def test_flow_card_links_verified_success_error_callback_companions_in_same_file():
    from app.services.source_driven_test_design import build_source_driven_test_design

    flow = _flow_pack()
    flow["call_edges"][0]["to_symbol"] = "iscsi_conn_login_pdu_success_complete"
    flow["error_paths"][0]["symbol"] = "iscsi_conn_login_pdu_err_complete"
    flow["error_paths"][0]["text"] = "Login response uses error completion callback"

    artifacts = build_source_driven_test_design(
        source_pack=_source_pack(),
        flow_pack=flow,
        flow_outline=_outline(),
        sfmea=_sfmea(),
        black_box_cases=_cases(),
    )

    card = artifacts["flow_cards.json"]["items"][0]
    assert card["status"] == "READY"
    assert card["error_chain_refs"] == ["FLOW-ERROR-001"]
    assert card["abnormal_paths"] == ["Login response uses error completion callback"]


def test_final_fact_verification_never_passes_without_independent_l2(tmp_path):
    from app.services.source_driven_test_design import _combined_final_fact_verification

    (tmp_path / "independent_fact_verification.json").write_text(
        json.dumps({
            "status": "passed",
            "claims": [{"claim_id": "C-1", "status": "verified"}],
        }),
        encoding="utf-8",
    )
    result = _combined_final_fact_verification(tmp_path)

    assert result["status"] == "blocked"
    assert result["behavior_validator_independent"] is False
    assert result["insufficient"] >= 1


def test_final_fact_verification_accepts_l1_only_source_anchors_when_l2_is_not_applicable(tmp_path):
    from app.services.source_driven_test_design import _combined_final_fact_verification

    (tmp_path / "independent_fact_verification.json").write_text(
        json.dumps({
            "status": "passed",
            "claims": [{
                "claim_id": "SRC-1",
                "type": "source_anchor",
                "status": "verified",
            }],
        }),
        encoding="utf-8",
    )
    (tmp_path / "behavior_claim_validation.json").write_text(
        json.dumps({
            "status": "not_applicable",
            "validator": {"independent": False},
            "candidate_count": 0,
            "requested_count": 0,
            "claims": [],
        }),
        encoding="utf-8",
    )

    result = _combined_final_fact_verification(tmp_path)

    assert result["status"] == "passed"
    assert result["behavior_validator_not_required"] is True
    assert result["verified"] == 1
    assert result["claims"][0]["status"] == "verified"


def test_final_fact_verification_rebuilds_l1_from_repaired_delivery_bytes(tmp_path):
    """A stale pre-repair snapshot must not poison a repaired delivery."""
    from app.services.source_driven_test_design import _combined_final_fact_verification

    (tmp_path / "independent_fact_verification.json").write_text(
        json.dumps({
            "status": "passed",
            "claims": [{
                "claim_id": "STALE-REMOVED-CLAIM",
                "type": "source",
                "status": "verified",
            }],
        }),
        encoding="utf-8",
    )
    (tmp_path / "evidence_cards.json").write_text(
        json.dumps([{
            "evidence_id": "SRC-REPAIRED",
            "file_path": "lib/iscsi/iscsi.c",
            "start_line": 10,
            "end_line": 10,
            "excerpt": "return 0;",
            "sha256": "a" * 64,
        }]),
        encoding="utf-8",
    )
    (tmp_path / "sfmea.json").write_text(
        json.dumps([{
            "sfmea_id": "SFMEA-REPAIRED",
            "technical_claims": [{
                "claim_id": "CURRENT-ANCHOR",
                "type": "source_anchor",
                "statement": "return 0;",
                "evidence": [{
                    "evidence_id": "SRC-REPAIRED",
                    "path": "lib/iscsi/iscsi.c",
                    "quote": "return 0;",
                }],
            }],
        }]),
        encoding="utf-8",
    )
    (tmp_path / "black_box_cases.json").write_text("[]", encoding="utf-8")
    (tmp_path / "behavior_claim_validation.json").write_text(
        json.dumps({
            "status": "not_applicable",
            "validator": {"independent": False},
            "candidate_count": 0,
            "requested_count": 0,
            "claims": [],
        }),
        encoding="utf-8",
    )

    result = _combined_final_fact_verification(tmp_path)

    assert result["status"] == "passed"
    assert result["verified"] == 1
    assert [item["claim_id"] for item in result["claims"]] == ["CURRENT-ANCHOR"]


def test_refresh_source_delivery_governance_resolves_agent_owned_artifacts(tmp_path):
    from app.services.source_driven_test_design import refresh_source_driven_delivery_governance

    agent_dir = tmp_path / "agent_runs" / "analyze"
    agent_dir.mkdir(parents=True)
    (agent_dir / "evidence_cards.json").write_text("[]", encoding="utf-8")
    (agent_dir / "sfmea.json").write_text("[]", encoding="utf-8")
    (agent_dir / "black_box_cases.json").write_text("[]", encoding="utf-8")

    refresh_source_driven_delivery_governance(tmp_path)

    assert (agent_dir / "judge_report.json").is_file()
    assert not (tmp_path / "judge_report.json").exists()


def test_refresh_source_delivery_governance_rebuilds_risk_ledger_after_sfmea_id_normalization(tmp_path):
    from app.services.source_driven_test_design import refresh_source_driven_delivery_governance

    (tmp_path / "sfmea.json").write_text(
        json.dumps([{
            "sfmea_id": "SFMEA-001",
            "failure_mode": "Login timeout cleanup risk",
            "source_evidence": ["SRC-1"],
            "test_mapping": ["BB-1"],
        }]),
        encoding="utf-8",
    )
    (tmp_path / "scenario_candidates.json").write_text(
        json.dumps({"items": []}), encoding="utf-8"
    )
    # This mimics an old pre-normalization ledger written while the model used
    # SFMEA-01.  The final delivery refresh must not retain that stale ID.
    (tmp_path / "risk_register.json").write_text(
        json.dumps({"items": [{"risk_id": "SFMEA-01"}]}), encoding="utf-8"
    )
    (tmp_path / "evidence_cards.json").write_text(
        json.dumps([{
            "evidence_id": "SRC-1",
            "file_path": "lib/iscsi/conn.c",
            "start_line": 10,
            "end_line": 10,
            "excerpt": "return 0;",
            "sha256": "a" * 64,
        }]),
        encoding="utf-8",
    )
    (tmp_path / "black_box_cases.json").write_text(
        json.dumps([{
            "case_id": "BB-1",
            "risk_ids": ["SFMEA-001"],
            "evidence_refs": ["SRC-1"],
        }]),
        encoding="utf-8",
    )

    refresh_source_driven_delivery_governance(tmp_path)

    risk_register = json.loads((tmp_path / "risk_register.json").read_text())
    traceability = json.loads((tmp_path / "traceability_matrix.json").read_text())
    assert risk_register["items"][0]["risk_id"] == "SFMEA-001"
    assert traceability["unknown_risk_ids"] == []


def test_final_fact_verification_preserves_l1_conflicts_and_unreviewed_tail(tmp_path):
    from app.services.source_driven_test_design import _combined_final_fact_verification

    (tmp_path / "independent_fact_verification.json").write_text(
        json.dumps({
            "claims": [
                {"claim_id": "C-1", "status": "contradicted"},
                {"claim_id": "C-2", "status": "verified"},
            ]
        }),
        encoding="utf-8",
    )
    (tmp_path / "behavior_claim_validation.json").write_text(
        json.dumps({
            "status": "completed",
            "validator": {"independent": True},
            "claims": [{"claim_id": "C-1", "status": "supports"}],
        }),
        encoding="utf-8",
    )

    result = _combined_final_fact_verification(tmp_path)

    by_id = {item["claim_id"]: item for item in result["claims"]}
    assert by_id["C-1"]["status"] == "contradicted"
    assert by_id["C-2"]["status"] == "insufficient"
    assert result["status"] == "blocked"


def test_final_fact_verification_matches_duplicate_claim_ids_by_binding(tmp_path):
    from app.services.source_driven_test_design import _combined_final_fact_verification

    (tmp_path / "independent_fact_verification.json").write_text(
        json.dumps({
            "claims": [
                {"claim_id": "DUP", "binding": "binding-a", "status": "verified"},
                {"claim_id": "DUP", "binding": "binding-b", "status": "verified"},
            ]
        }),
        encoding="utf-8",
    )
    (tmp_path / "behavior_claim_validation.json").write_text(
        json.dumps({
            "status": "completed",
            "validator": {"independent": True},
            "claims": [
                {"claim_id": "DUP", "binding": "binding-b", "status": "supports"},
                {"claim_id": "DUP", "binding": "binding-a", "status": "supports"},
            ],
        }),
        encoding="utf-8",
    )

    result = _combined_final_fact_verification(tmp_path)

    assert [(item["binding"], item["status"]) for item in result["claims"]] == [
        ("binding-a", "verified"),
        ("binding-b", "verified"),
    ]
    assert result["total"] == 2
    assert result["status"] == "passed"


def test_candidate_mapping_requires_shared_verified_evidence():
    from app.services.source_driven_test_design import _matching_candidate_ids

    candidates = [{
        "candidate_id": "SCN-1",
        "title": "Login request error",
        "mechanism": "request login error",
        "evidence_refs": ["SRC-OTHER"],
    }]
    row = {
        "failure_mode": "Login request error",
        "source_evidence": ["SRC-REAL"],
    }

    assert _matching_candidate_ids(row, candidates) == []


def test_plain_unsigned_integer_does_not_enable_capacity_or_wraparound():
    from app.services.source_driven_test_design import build_source_driven_test_design

    source = _source_pack()
    source["evidence_cards"] = [{
        "evidence_id": "SRC-UINT",
        "classification": "source",
        "file_path": "lib/example.c",
        "symbols": ["connection_create"],
        "matched_terms": ["connection", "uint32_t"],
        "excerpt": "struct connection *connection_create(uint32_t state);",
        "sha256": "d" * 64,
    }]
    flow = {**_flow_pack(), "cleanup_paths": [{
        "evidence_id": "CLEAN-UINT",
        "file_path": "lib/example.c",
        "symbol": "connection_destroy",
        "text": "connection_destroy(conn)",
    }]}

    artifacts = build_source_driven_test_design(
        source_pack=source,
        flow_pack=flow,
        flow_outline=_outline(),
        sfmea=_sfmea(),
        black_box_cases=_cases(),
    )

    connection = next(
        item for item in artifacts["resources.json"]["items"]
        if item["kind"] == "connection"
    )
    assert connection["capacity_model_applicable"] is False
    assert connection["wraparound_applicable"] is False


def test_final_behavior_validation_refreshes_judge_and_existing_mindmap(tmp_path):
    from app.services.source_driven_test_design import (
        MINDMAP_ARTIFACTS,
        build_source_driven_test_design,
        build_test_design_mindmap,
        refresh_source_driven_delivery_governance,
    )

    artifacts = build_source_driven_test_design(
        source_pack=_source_pack(),
        flow_pack=_flow_pack(),
        flow_outline=_outline(),
        sfmea=_sfmea(),
        black_box_cases=_cases(),
        fact_verification={"status": "not_checked", "total": 0},
    )
    for disposition_name in (
        "branch_disposition.json",
        "state_transition_disposition.json",
        "resource_lifecycle_disposition.json",
    ):
        for row in artifacts[disposition_name]["items"]:
            row["disposition"] = "retain"
            row["covered_by"] = ["CASE-001"]
    for name, payload in artifacts.items():
        (tmp_path / name).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    (tmp_path / "independent_fact_verification.json").write_text(
        json.dumps({"status": "not_checked", "total": 0, "claims": []}),
        encoding="utf-8",
    )
    initial_mindmap = build_test_design_mindmap(artifacts)
    (tmp_path / MINDMAP_ARTIFACTS[0]).write_text(
        json.dumps(initial_mindmap, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / MINDMAP_ARTIFACTS[1]).write_text("stale html", encoding="utf-8")
    (tmp_path / MINDMAP_ARTIFACTS[2]).write_text("stale svg", encoding="utf-8")
    (tmp_path / "behavior_claim_validation.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "validator": {"independent": True},
                "claims": [
                    {
                        "claim_id": "ROW:sfmea.json:SFMEA-001",
                        "status": "supports",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    refreshed = refresh_source_driven_delivery_governance(tmp_path)

    assert refreshed["ready"] is True
    assert refreshed["axes"]["facts"]["total"] == 1
    assert refreshed["axes"]["facts"]["verified"] == 1
    mindmap = json.loads((tmp_path / MINDMAP_ARTIFACTS[0]).read_text(encoding="utf-8"))
    assert mindmap["status"] == "READY"
    assert "data-mindmap-root" in (tmp_path / MINDMAP_ARTIFACTS[1]).read_text(encoding="utf-8")
    assert "test-design-mindmap-v1" in (tmp_path / MINDMAP_ARTIFACTS[2]).read_text(encoding="utf-8")


def test_final_behavior_validation_is_fail_closed_when_validator_is_not_independent(tmp_path):
    from app.services.source_driven_test_design import (
        build_source_driven_test_design,
        refresh_source_driven_delivery_governance,
    )

    artifacts = build_source_driven_test_design(
        source_pack=_source_pack(),
        flow_pack=_flow_pack(),
        flow_outline=_outline(),
        sfmea=_sfmea(),
        black_box_cases=_cases(),
        fact_verification={"status": "not_checked", "total": 0},
    )
    for name, payload in artifacts.items():
        (tmp_path / name).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    (tmp_path / "behavior_claim_validation.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "validator": {"independent": False},
                "claims": [{"claim_id": "C-1", "status": "supports"}],
            }
        ),
        encoding="utf-8",
    )

    refreshed = refresh_source_driven_delivery_governance(tmp_path)

    assert refreshed["ready"] is False
    assert refreshed["axes"]["facts"]["status"] == "not_checked"
    assert refreshed["axes"]["facts"]["score"] is None


def test_independent_fact_verifier_requires_exact_verified_source_quote():
    from app.services.source_driven_test_design import verify_technical_claims

    good = _sfmea()
    good[0]["technical_claims"] = [{
        "claim_id": "CLAIM-001",
        "type": "source_behavior",
        "statement": "conn 进入 EXITING 状态会提前返回",
        "evidence": [{
            "evidence_id": "SRC-001",
            "path": "lib/iscsi/iscsi.c",
            "quote": "if (conn->state == EXITING) return;",
        }],
    }]
    bad = _cases()
    bad[0]["technical_claims"] = [{
        "claim_id": "CLAIM-002",
        "type": "protocol_constant",
        "statement": "不存在的常量",
        "evidence": [{
            "evidence_id": "SRC-001",
            "path": "lib/iscsi/iscsi.c",
            "quote": "#define LOGIN_OPCODE 0x99",
        }],
    }]

    result = verify_technical_claims(
        source_pack=_source_pack(),
        sfmea=good,
        black_box_cases=bad,
    )

    assert result["status"] == "blocked"
    assert result["total"] == 2
    assert result["verified"] == 1
    assert result["contradicted"] == 1
    assert {item["status"] for item in result["claims"]} == {"verified", "contradicted"}


def test_mindmap_renderer_produces_single_source_json_offline_html_and_safe_svg():
    from app.services.source_driven_test_design import (
        build_source_driven_test_design,
        build_test_design_mindmap,
        render_test_design_mindmap_html,
        render_test_design_mindmap_svg,
    )

    artifacts = build_source_driven_test_design(
        source_pack=_source_pack(),
        flow_pack=_flow_pack(),
        flow_outline=_outline(),
        sfmea=_sfmea(),
        black_box_cases=_cases(),
    )
    mindmap = build_test_design_mindmap(artifacts)
    html = render_test_design_mindmap_html(mindmap)
    svg = render_test_design_mindmap_svg(mindmap)

    assert mindmap["schema_version"] == "test-design-mindmap-v1"
    assert mindmap["default_expand_depth"] == 2
    assert all(node.get("id") and node.get("type") and "trace_refs" in node for node in mindmap["nodes"])
    assert "搜索节点" in html and "data-mindmap-root" in html
    assert "http://" not in html and "https://" not in html
    assert "<svg" in svg and "test-design-mindmap-v1" in svg
    assert svg.count("data-node-id=") == len(mindmap["nodes"])
    assert mindmap["generation_id"] in html
    assert mindmap["generation_id"] in svg


def test_mindmap_does_not_mark_fact_sensitive_nodes_ready_when_judge_is_blocked():
    from app.services.source_driven_test_design import (
        build_source_driven_test_design,
        build_test_design_mindmap,
    )

    artifacts = build_source_driven_test_design(
        source_pack=_source_pack(),
        flow_pack=_flow_pack(),
        flow_outline=_outline(),
        sfmea=_sfmea(),
        black_box_cases=_cases(),
    )
    mindmap = build_test_design_mindmap(artifacts)

    assert mindmap["status"] == "BLOCKED"
    sensitive = {
        node["id"]: node["status"]
        for node in mindmap["nodes"]
        if node["id"].startswith(("risk:", "scenario:", "resource:", "flow:"))
    }
    assert sensitive
    assert "READY" not in sensitive.values()


def test_mindmap_renderer_escapes_untrusted_titles_and_summaries():
    from app.services.source_driven_test_design import render_test_design_mindmap_html

    payload = {
        "schema_version": "test-design-mindmap-v1",
        "status": "PARTIAL",
        "default_expand_depth": 2,
        "nodes": [
            {
                "id": "root",
                "type": "overview",
                "title": "<script>alert(1)</script>",
                "summary": "</script><img src=x onerror=alert(1)>",
                "priority": "P1",
                "status": "PARTIAL",
                "parent_id": None,
                "children": [],
                "evidence_refs": [],
                "trace_refs": {},
            }
        ],
    }

    html = render_test_design_mindmap_html(payload)
    assert "<script>alert(1)</script>" not in html
    assert "onerror=alert" not in html
    assert "已移除标签" in html
