from __future__ import annotations

import json
from pathlib import Path


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_store_load_reconciles_human_labels_to_authoritative_task_and_step_paths(
    tmp_path,
):
    # Importing the registry installs the production load/list reconciliation seam.
    import app.services.provider_adapters.registry  # noqa: F401
    from app.services.workbench_task_run import WorkbenchTaskRunStore

    store_root = tmp_path / "task_runs"
    task_run_id = "task_run_0123456789abcdef0123456789abcdef"
    task_root = store_root / task_run_id
    bad_task_root = store_root / "工作流节点"
    bad_agent_root = bad_task_root / "工作流节点" / "源码驱动测试分析"

    _write_json(
        bad_agent_root / "agent_run.json",
        {
            "run_id": "legacy-run",
            "provider": "builtin-llm",
            "artifact_dir": str(bad_agent_root),
        },
    )
    _write_json(
        bad_agent_root / "task_bundle.json",
        {
            "task_run_id": task_run_id,
            "step_id": "源码驱动测试分析",
        },
    )
    (bad_agent_root / "legacy_input.txt").write_text("preserve", encoding="utf-8")

    payload = {
        "task_run_id": task_run_id,
        "workflow_id": "source_flow_sfmea_blackbox",
        "workspace_id": "workspace-1",
        "repo_path": str(tmp_path / "repo"),
        "artifact_dir": str(bad_task_root),
        "workflow_snapshot": {
            "id": "source_flow_sfmea_blackbox",
            "compiled_contract_version": 3,
            "steps": [
                {
                    "id": "analyze_source_flow",
                    "label": "源码驱动测试分析",
                    "type": "agent_task",
                    "provider": "builtin-llm",
                }
            ],
        },
        "input_snapshot": {},
        "task_bundle": {"compiled_contract_version": 3},
        "agent_runs": [
            {
                "step_id": "源码驱动测试分析",
                "run_id": "legacy-run",
                "provider": "builtin-llm",
                "artifact_dir": str(bad_agent_root),
            }
        ],
    }
    _write_json(task_root / "task_run.json", payload)
    _write_json(
        task_root / "agent_execution_descriptors.json",
        {"schema_version": 1, "agent_runs": payload["agent_runs"]},
    )

    loaded = WorkbenchTaskRunStore(store_root).load(task_run_id)

    expected_agent_root = task_root / "agent_runs" / "analyze_source_flow"
    assert Path(loaded.artifact_dir) == task_root.resolve()
    assert loaded.agent_runs == [
        {
            "step_id": "analyze_source_flow",
            "run_id": "legacy-run",
            "provider": "builtin-llm",
            "artifact_dir": str(expected_agent_root.resolve()),
        }
    ]
    assert (expected_agent_root / "legacy_input.txt").read_text(encoding="utf-8") == "preserve"

    corrected_task = json.loads((task_root / "task_run.json").read_text(encoding="utf-8"))
    assert corrected_task["artifact_dir"] == str(task_root.resolve())
    assert corrected_task["agent_runs"][0]["step_id"] == "analyze_source_flow"
    assert corrected_task["agent_runs"][0]["artifact_dir"] == str(
        expected_agent_root.resolve()
    )

    corrected_agent = json.loads(
        (expected_agent_root / "agent_run.json").read_text(encoding="utf-8")
    )
    assert corrected_agent["artifact_dir"] == str(expected_agent_root.resolve())
    corrected_bundle = json.loads(
        (expected_agent_root / "task_bundle.json").read_text(encoding="utf-8")
    )
    assert corrected_bundle["step_id"] == "analyze_source_flow"

    reconciliation = json.loads(
        (task_root / "artifact_path_reconciliation.json").read_text(encoding="utf-8")
    )
    assert reconciliation["authority"] == "task_run_json_parent_and_frozen_step_id"
    assert len(reconciliation["changes"]) == 2


def test_store_load_keeps_already_canonical_paths_stable(tmp_path):
    import app.services.provider_adapters.registry  # noqa: F401
    from app.services.workbench_task_run import WorkbenchTaskRunStore

    store_root = tmp_path / "task_runs"
    task_run_id = "task_run_fedcba9876543210fedcba9876543210"
    task_root = store_root / task_run_id
    agent_root = task_root / "agent_runs" / "analyze_source_flow"
    _write_json(agent_root / "agent_run.json", {"run_id": "run", "artifact_dir": str(agent_root)})
    _write_json(agent_root / "task_bundle.json", {"step_id": "analyze_source_flow"})
    _write_json(
        task_root / "task_run.json",
        {
            "task_run_id": task_run_id,
            "workflow_id": "workflow",
            "workspace_id": "workspace",
            "repo_path": str(tmp_path),
            "artifact_dir": str(task_root),
            "workflow_snapshot": {
                "compiled_contract_version": 3,
                "steps": [{"id": "analyze_source_flow", "type": "agent_task"}],
            },
            "input_snapshot": {},
            "task_bundle": {"compiled_contract_version": 3},
            "agent_runs": [
                {
                    "step_id": "analyze_source_flow",
                    "run_id": "run",
                    "artifact_dir": str(agent_root),
                }
            ],
        },
    )

    loaded = WorkbenchTaskRunStore(store_root).load(task_run_id)

    assert Path(loaded.artifact_dir) == task_root.resolve()
    assert Path(loaded.agent_runs[0]["artifact_dir"]) == agent_root.resolve()
    assert not (task_root / "artifact_path_reconciliation.json").exists()
