from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.knowledge_replay import (
    HistoricalReplayRunner,
    load_replay_fixtures,
)
from app.services.knowledge_store import KnowledgeStore


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "knowledge_replay"


def test_three_historical_cases_load_with_source_provenance():
    cases = load_replay_fixtures(FIXTURE_DIR)

    assert [case.case_id for case in cases] == [
        "iscsi-cmdsn-recovery",
        "iscsi-dtoe-login-window",
        "iscsi-lock-cross-file-release",
    ]
    assert all(case.source.identity.startswith("fixture://") for case in cases)
    assert all(case.source.locators for case in cases)
    assert all(case.query for case in cases)


def test_history_only_is_an_investigation_lead_and_current_support_is_a_candidate(tmp_path):
    runner = HistoricalReplayRunner(KnowledgeStore(tmp_path / "knowledge.sqlite3"))
    case = load_replay_fixtures(FIXTURE_DIR)[0]

    history_only = runner.replay_case(case, current_evidence=[])
    current_supported = runner.replay_case(case, current_evidence=["current cmdsn trace"])

    assert history_only["decision"]["status"] == "investigation_lead"
    assert current_supported["decision"]["status"] == "candidate_finding"
    assert history_only["source"]["source_snapshot_id"]
    assert history_only["decision"]["status"] != "confirmed_finding"


@pytest.mark.parametrize("case_index", [0, 1, 2])
def test_confirmed_requires_current_evidence_and_nonempty_disconfirming_checks(tmp_path, case_index):
    runner = HistoricalReplayRunner(KnowledgeStore(tmp_path / f"knowledge-{case_index}.sqlite3"))
    case = load_replay_fixtures(FIXTURE_DIR)[case_index]

    without_checks = runner.replay_case(
        case,
        requested_status="confirmed_finding",
        current_evidence=[f"current evidence for {case.case_id}"],
        disconfirming_checks=[],
    )
    with_checks = runner.replay_case(
        case,
        requested_status="confirmed_finding",
        current_evidence=[f"current evidence for {case.case_id}"],
        disconfirming_checks=[
            {"check": "independent lifecycle check", "status": "completed", "result": "counter-check passed"}
        ],
    )

    assert without_checks["decision"]["status"] == "candidate_finding"
    assert with_checks["decision"]["status"] == "confirmed_finding"


def test_fallback_current_evidence_can_rule_out_a_historical_lead(tmp_path):
    runner = HistoricalReplayRunner(KnowledgeStore(tmp_path / "knowledge.sqlite3"))
    case = load_replay_fixtures(FIXTURE_DIR)[2]

    report = runner.replay_case(
        case,
        requested_status="ruled_out",
        current_disproof_evidence=["cross-file caller invokes iscsi_unlock after the operation"],
    )

    assert report["decision"]["status"] == "ruled_out"
    assert report["decision"]["status"] != "confirmed_finding"


def test_replay_report_is_machine_readable_and_metrics_do_not_claim_historical_truth(tmp_path):
    runner = HistoricalReplayRunner(KnowledgeStore(tmp_path / "knowledge.sqlite3"))
    report = runner.run(FIXTURE_DIR)

    json.dumps(report, ensure_ascii=False)
    assert report["schema_version"] == 1
    assert len(report["scenarios"]) == 3
    assert 0 <= report["metrics"]["retrieval_usefulness"] <= 1
    assert 0 <= report["metrics"]["conclusion_precision"] <= 1
    assert report["metrics"]["conclusion_precision"] == 1
    assert report["conclusion_scope"] == "authority_transition_consistency"
    assert all(scenario["classification"] == "historical_only" for scenario in report["scenarios"])
    assert all(scenario["source"]["identity"].startswith("fixture://") for scenario in report["scenarios"])
    assert all(scenario["source"]["kind"] for scenario in report["scenarios"])
    assert all(
        trial["evidence_mode"] == "synthetic_policy_probe"
        for scenario in report["scenarios"]
        for trial in scenario["trials"]
    )
    assert all(scenario["source"]["locators"] for scenario in report["scenarios"])
    assert all("confirmed_finding" not in scenario["historical_record"]["content"] for scenario in report["scenarios"])


def test_replay_report_can_be_written_as_json(tmp_path):
    runner = HistoricalReplayRunner(KnowledgeStore(tmp_path / "knowledge.sqlite3"))
    output = tmp_path / "replay-report.json"

    report = runner.write_report(FIXTURE_DIR, output)

    assert json.loads(output.read_text(encoding="utf-8")) == report
    assert output.stat().st_size > 0
