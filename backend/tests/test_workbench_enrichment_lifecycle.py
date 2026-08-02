import json
import sys
import zipfile
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace


def test_followup_requests_obey_frozen_policy_and_are_bounded(tmp_path: Path):
    from app.services.workbench_run_enrichment import knowledge_followup_requests

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "knowledge_followup_requests.json").write_text(
        json.dumps(
            {
                "queries": [
                    {"query": " iSCSI CmdSN recovery ", "reason": "first"},
                    {"query": "iscsi cmdsn recovery", "reason": "duplicate"},
                    {"query": "DTOE shared queue", "reason": "second"},
                    {"query": "login window", "reason": "third"},
                    {"query": "must be capped", "reason": "fourth"},
                ]
            }
        ),
        encoding="utf-8",
    )
    disabled = {"knowledge_retrieval": {"policy": {"allow_followup": False}}}
    enabled = {"knowledge_retrieval": {"policy": {"allow_followup": True}}}

    assert knowledge_followup_requests(agent_dir, disabled) == []
    requests = knowledge_followup_requests(agent_dir, enabled)
    assert [item["query"] for item in requests] == [
        "iSCSI CmdSN recovery",
        "DTOE shared queue",
        "login window",
    ]


def test_finalize_enriched_run_writes_usage_and_deliverable_bundle(tmp_path: Path):
    from app.services.workbench_run_enrichment import finalize_enriched_task_run

    task_dir = tmp_path / "task_run_1"
    agent_dir = task_dir / "agent_runs" / "analyze"
    agent_dir.mkdir(parents=True)
    (task_dir / "report.md").write_text("# Result\n", encoding="utf-8")
    (task_dir / "output_contract.json").write_text(
        json.dumps(
            {
                "profile_id": "apro_test",
                "profile_version": 1,
                "artifacts": [
                    {
                        "id": "report",
                        "filename": "report.md",
                        "format": "markdown",
                        "required": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (task_dir / "knowledge_retrieval.json").write_text(
        json.dumps(
            {
                "status": "ready_followup",
                "records": [
                    {"record_id": "pattern-1", "authority": "investigation_lead"}
                ],
            }
        ),
        encoding="utf-8",
    )
    (agent_dir / "knowledge_usage.json").write_text(
        json.dumps({"used_record_ids": ["pattern-1", "unknown"]}),
        encoding="utf-8",
    )
    execution = {
        "status": "completed",
        "outputs": [{"id": "report", "status": "ok", "path": "report.md"}],
        "step_results": [{"step_id": "analyze"}],
    }
    (task_dir / "workflow_execution.json").write_text(
        json.dumps(execution), encoding="utf-8"
    )
    task_run = SimpleNamespace(
        task_run_id="task_run_1",
        artifact_dir=str(task_dir),
        workflow_id="workflow-1",
        workflow_snapshot={"name": "Workflow 1"},
    )

    result = finalize_enriched_task_run(task_run, execution)

    usage = json.loads((task_dir / "knowledge_usage.json").read_text(encoding="utf-8"))
    assert usage["reported_used_record_ids"] == ["pattern-1"]
    assert usage["unrecognized_record_ids"] == ["unknown"]
    assert usage["authority"] == "history_remains_investigation_lead"
    assert Path(result["bundle_path"]).is_file()
    assert (task_dir / "deliverable_bundle.json").is_file()


def test_phase2_runner_executes_allowed_knowledge_followup_and_finalizes(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.artifact_profiles import ArtifactProfileStore
    from app.services.knowledge_store import KnowledgeStore
    from app.services.workbench_run_enrichment import enrich_prepared_task_run
    from app.services.workbench_task_run import (
        WorkbenchTaskRunPreparer,
        refresh_run_snapshot_v3,
    )
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
    from app.services.workflow_dsl import WorkflowStore

    knowledge_store = KnowledgeStore(tmp_path / "knowledge.sqlite3")
    pattern = knowledge_store.create_pattern(
        name="CmdSN recovery after over-limit",
        content="iSCSI CmdSN resource recovery may remain exhausted after over-limit.",
        scope="personal_global",
        terms=["iSCSI", "CmdSN", "resource recovery"],
    )
    profile_store = ArtifactProfileStore(tmp_path / "profiles.sqlite3")
    profile = profile_store.create_profile(
        {
            "name": "Custom incident delivery",
            "artifacts": [
                {
                    "id": "custom_summary",
                    "filename": "custom-summary.md",
                    "format": "markdown",
                    "required": True,
                    "schema": {"required_sections": ["Custom Summary"]},
                }
            ],
        }
    )
    script_path = tmp_path / "agent_knowledge_turns.py"
    script_path.write_text(
        "import json, pathlib, sys\n"
        "payload=json.loads(sys.stdin.read())\n"
        "bundle=payload['task_bundle']\n"
        "root=pathlib.Path(payload['artifact_dir'])\n"
        "followup=bundle.get('requested_knowledge') or {}\n"
        "if not followup.get('records'):\n"
        "    (root/'knowledge_followup_requests.json').write_text(json.dumps({"
        "'queries':[{'query':'iSCSI CmdSN resource recovery','reason':'check history'}]}"
        "), encoding='utf-8')\n"
        "else:\n"
        "    record=followup['records'][0]\n"
        "    (root/'report.json').write_text(json.dumps({'record_id':record['record_id']}), encoding='utf-8')\n"
        "    (root/'custom-summary.md').write_text('# Custom Summary\\n\\nVerified delivery.\\n', encoding='utf-8')\n"
        "    (root/'knowledge_usage.json').write_text(json.dumps({'used_record_ids':[record['record_id']]}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        settings,
        "external_agent_custom_providers",
        [{"id": "local-python", "command": f"{sys.executable} {script_path}"}],
    )
    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow(
        {
            "id": "knowledge_followup_turns",
            "name": "Knowledge follow-up turns",
            "version": 1,
            "inputs": [{"id": "analysis_object", "type": "free_text"}],
            "steps": [
                {
                    "id": "analyze",
                    "type": "agent_task",
                    "provider": "local-python",
                    "required_artifacts": ["report.json"],
                }
            ],
            "outputs": [
                {
                    "id": "report",
                    "type": "json",
                    "from": "analyze",
                    "artifact": "report.json",
                }
            ],
            "knowledge_policy": {
                "sources": ["experience_patterns"],
                "scopes": ["personal_global"],
                "mode": "on_demand",
                "max_results": 4,
                "allow_followup": True,
            },
        }
    )
    prepared = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="knowledge_followup_turns",
        workspace_id="ws-knowledge-followup",
        repo_path=str(tmp_path),
        inputs={"analysis_object": "investigate iSCSI recovery"},
    )
    enrich_prepared_task_run(
        prepared,
        artifact_profile_store=profile_store,
        knowledge_store=knowledge_store,
        evidence_memory=None,
        semantic_library=None,
        material_db_path=tmp_path / "materials.sqlite3",
        selected_artifact_profile_id=profile["id"],
    )
    task_dir = Path(prepared.artifact_dir)
    (task_dir / "task_run.json").write_text(
        json.dumps(asdict(prepared)), encoding="utf-8"
    )
    (task_dir / "task_bundle.json").write_text(
        json.dumps(prepared.task_bundle), encoding="utf-8"
    )
    refresh_run_snapshot_v3(task_dir)
    poisoned_bundle = dict(prepared.task_bundle)
    poisoned_bundle["artifact_profile"] = {
        "profile_id": "apro_poisoned",
        "artifacts": [{"id": "wrong", "filename": "wrong.md"}],
    }
    poisoned_bundle["knowledge_retrieval"] = {
        "policy": {"allow_followup": False},
        "records": [],
    }
    (task_dir / "task_bundle.json").write_text(
        json.dumps(poisoned_bundle), encoding="utf-8"
    )
    for agent_run in prepared.agent_runs:
        agent_bundle_path = Path(agent_run["artifact_dir"]) / "task_bundle.json"
        agent_bundle = json.loads(agent_bundle_path.read_text(encoding="utf-8"))
        agent_bundle["artifact_profile"] = poisoned_bundle["artifact_profile"]
        agent_bundle["knowledge_retrieval"] = poisoned_bundle["knowledge_retrieval"]
        agent_bundle_path.write_text(json.dumps(agent_bundle), encoding="utf-8")

    result = WorkbenchWorkflowRunner(
        tmp_path / "task_runs",
        knowledge_store=knowledge_store,
        material_db_path=tmp_path / "materials.sqlite3",
    ).execute_task_run(prepared.task_run_id, timeout_sec=10)

    assert result.status == "completed"
    step = result.step_results[0]
    assert step["turn_count"] == 2
    assert step["knowledge_followup_requests"][0]["query"] == (
        "iSCSI CmdSN resource recovery"
    )
    assert step["injected_knowledge"]["records"][0]["record_id"] == pattern["pattern_id"]
    assert step["injected_knowledge"]["records"][0]["usable_as_current_evidence"] is False
    usage = json.loads((task_dir / "knowledge_usage.json").read_text(encoding="utf-8"))
    assert usage["reported_used_record_ids"] == [pattern["pattern_id"]]
    custom_output = next(item for item in result.outputs if item["id"] == "custom_summary")
    assert custom_output["status"] == "ok"
    assert custom_output["artifact"] == "custom-summary.md"
    assert (task_dir / "deliverables.zip").is_file()
    assert (task_dir / "deliverable_bundle.json").is_file()
    with zipfile.ZipFile(task_dir / "deliverables.zip") as archive:
        assert "artifacts/custom-summary.md" in archive.namelist()


def test_enrichment_contracts_are_frozen_snapshot_components(tmp_path: Path):
    from app.services.workbench_task_run import (
        WorkbenchTaskRunPreparer,
        refresh_run_snapshot_v3,
        validate_run_snapshot_v3,
    )
    from app.services.workflow_dsl import WorkflowStore

    workflow_store = WorkflowStore(tmp_path / "workflows.db")
    workflow_store.save_workflow(
        {
            "id": "snapshot_enrichment",
            "name": "Snapshot enrichment",
            "version": 1,
            "inputs": [],
            "steps": [{"id": "capture", "type": "file_ingest"}],
            "outputs": [],
        }
    )
    prepared = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=workflow_store,
    ).prepare(
        workflow_id="snapshot_enrichment",
        workspace_id="ws-snapshot",
        repo_path=str(tmp_path),
        inputs={},
    )
    task_dir = Path(prepared.artifact_dir)
    (task_dir / "output_contract.json").write_text(
        json.dumps({"profile_id": "apro_frozen", "artifacts": []}),
        encoding="utf-8",
    )
    (task_dir / "knowledge_retrieval.json").write_text(
        json.dumps({"policy": {"allow_followup": False}, "records": []}),
        encoding="utf-8",
    )

    snapshot = refresh_run_snapshot_v3(task_dir)

    assert snapshot["components"]["output_contract"]["path"] == "output_contract.json"
    assert snapshot["components"]["knowledge_retrieval"]["path"] == (
        "knowledge_retrieval.json"
    )
    output_contract = (task_dir / "output_contract.json").read_text(encoding="utf-8")
    (task_dir / "output_contract.json").write_text("{}", encoding="utf-8")
    assert any("output_contract" in error for error in validate_run_snapshot_v3(task_dir))
    (task_dir / "output_contract.json").write_text(output_contract, encoding="utf-8")
    refresh_run_snapshot_v3(task_dir)
    (task_dir / "knowledge_retrieval.json").write_text("{}", encoding="utf-8")
    assert any("knowledge_retrieval" in error for error in validate_run_snapshot_v3(task_dir))
