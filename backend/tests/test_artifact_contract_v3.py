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
