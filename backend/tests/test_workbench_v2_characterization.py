import json

import pytest


def _legacy_workflow(*, name: str = "Legacy analysis") -> dict:
    return {
        "id": "legacy_analysis",
        "name": name,
        "version": 1,
        "inputs": [{"id": "target", "type": "free_text", "required": True}],
        "steps": [{"id": "analyze", "type": "agent_task", "goal": "analyze source"}],
        "outputs": [{"id": "report", "type": "markdown", "from": "analyze"}],
    }


def test_versioned_workbench_rejects_the_legacy_rollback_switch(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("WORKBENCH_V2_ENABLED", raising=False)
    assert Settings(_env_file=None).workbench_v2_enabled is True

    monkeypatch.setenv("WORKBENCH_V2_ENABLED", "false")
    with pytest.raises(ValueError, match="Input should be True"):
        Settings(_env_file=None)


def test_legacy_workflow_store_keeps_mutable_definition_and_detached_snapshot(tmp_path):
    from app.services.workflow_dsl import WorkflowStore

    store = WorkflowStore(tmp_path / "workflows.db")
    created = store.save_workflow(_legacy_workflow())
    frozen = store.freeze_workflow_snapshot(created.id)
    store.save_workflow(_legacy_workflow(name="Updated legacy analysis"))

    assert frozen["name"] == "Legacy analysis"
    assert store.get_workflow(created.id).name == "Updated legacy analysis"
    assert [item.id for item in store.list_workflows()] == [created.id]


def test_legacy_task_run_list_events_and_restart_recovery_are_stable(tmp_path):
    from app.services.workbench_task_run import WorkbenchTaskRunStore
    from app.services.workbench_task_run_events import (
        WorkbenchTaskRunEventStore,
        reconcile_interrupted_task_runs,
    )

    root = tmp_path / "task_runs"
    run_dir = root / "task_run_legacy"
    run_dir.mkdir(parents=True)
    task_payload = {
        "task_run_id": "task_run_legacy",
        "workflow_id": "legacy_analysis",
        "workspace_id": "ws-legacy",
        "repo_path": "/repo",
        "artifact_dir": str(run_dir),
        "workflow_snapshot": _legacy_workflow(),
        "input_snapshot": {"target": "nvmf"},
        "task_bundle": {"workflow_contract": {"outputs": []}},
        "agent_runs": [],
        "created_at": "2026-07-13T00:00:00+00:00",
        "status": "running",
        "runtime": {"status": "running"},
    }
    (run_dir / "task_run.json").write_text(
        json.dumps(task_payload), encoding="utf-8"
    )

    run_store = WorkbenchTaskRunStore(root)
    assert run_store.load("task_run_legacy").workflow_id == "legacy_analysis"
    assert [item.task_run_id for item in run_store.list(workspace_id="ws-legacy")] == [
        "task_run_legacy"
    ]

    event_store = WorkbenchTaskRunEventStore(root)
    first = event_store.append("task_run_legacy", "step_started", {"step_id": "analyze"})
    assert first["event_id"] == 1
    assert first["event_kind"] == "status"

    result = reconcile_interrupted_task_runs(root)
    assert result["interrupted_count"] == 1
    assert event_store.current_status("task_run_legacy") == "interrupted"
    assert [item["event_id"] for item in event_store.list_after("task_run_legacy")] == [1, 2]
    assert event_store.list_after("task_run_legacy", after_id=1)[0]["event_kind"] == "error"


def test_legacy_semantic_import_and_fts_search_contract_is_stable(tmp_path):
    from app.services.test_semantic_library import TestSemanticLibraryStore

    store = TestSemanticLibraryStore(tmp_path / "semantic.db")
    imported = store.import_cases({
        "source_ref": "legacy-cases.json",
        "defaults": {"module": "lib/nvmf", "test_level": "black_box"},
        "cases": [{
            "case_id": "TC_LEGACY_NVMF_001",
            "feature": "NVMe-oF",
            "scenario": "controller reconnect after timeout",
            "terms": ["controller", "reconnect", "timeout"],
            "status": "active",
        }],
    })

    assert imported["imported_count"] == 1
    results = store.retrieve(query="reconnect timeout", module="lib/nvmf")
    assert [item.case_id for item in results] == ["TC_LEGACY_NVMF_001"]
    assert results[0].source_ref == "legacy-cases.json"
