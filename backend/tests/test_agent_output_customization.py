from app.services.agent_run_harness import AgentRunRecord, _agent_output_contract_payload


def test_phase2_agent_contract_carries_frozen_deliverable_and_knowledge_rules(tmp_path):
    run = AgentRunRecord(
        run_id="run-1",
        turn_id="turn-1",
        provider="codex",
        command=["codex"],
        cwd=str(tmp_path),
        artifact_dir=str(tmp_path / "artifacts"),
    )
    payload = _agent_output_contract_payload(
        run=run,
        workflow_snapshot={"compiled_contract_version": 3},
        task_bundle={
            "compiled_contract_version": 3,
            "declared_outputs": [
                {"id": "report", "artifact": "report.md", "required": True}
            ],
            "artifact_profile": {
                "profile_id": "apro_protocol",
                "profile_version": 4,
                "sha256": "profile-sha",
                "artifacts": [
                    {
                        "id": "review",
                        "filename": "protocol-review.md",
                        "format": "markdown",
                        "required": True,
                    }
                ],
            },
            "knowledge_retrieval": {
                "policy": {"allow_followup": True},
                "items": [],
            },
        },
    )

    assert payload["deliverable_profile"]["profile_id"] == "apro_protocol"
    assert payload["deliverable_profile"]["profile_version"] == 4
    assert payload["deliverable_profile"]["sha256"] == "profile-sha"
    assert payload["deliverable_profile"]["artifacts"][0]["filename"] == "protocol-review.md"
    assert payload["knowledge_followup_protocol"]["enabled"] is True
    assert payload["knowledge_followup_protocol"]["request_artifact"] == (
        "knowledge_followup_requests.json"
    )
    assert payload["knowledge_followup_protocol"]["max_queries_per_run"] == 3
    assert payload["knowledge_followup_protocol"]["direct_store_access"] is False
    assert "experience_lead" in payload["knowledge_followup_protocol"]["authority"]
    assert payload["knowledge_usage_report"] == {
        "artifact": "knowledge_usage.json",
        "required": True,
        "schema": {"used_record_ids": ["record-id"]},
        "rule": "Report only record ids actually used in the final answer; use an empty list when none were used.",
    }
