"""Phase 7 reliability contracts for isolated V3 attempt artifacts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def _write_v3_task_run(
    artifact_root: Path,
    *,
    task_run_id: str,
    task_id: str,
) -> Path:
    """Create the persisted minimum required by the public task-run artifact API."""
    task_dir = artifact_root / task_run_id
    task_dir.mkdir(parents=True)
    payload = {
        "task_run_id": task_run_id,
        "task_id": task_id,
        "workflow_id": "wf_phase7_free_source_analysis",
        "workspace_id": "ws_phase7_reliability",
        "repo_path": str(task_dir),
        "artifact_dir": str(task_dir),
        "workflow_snapshot": {"compiled_contract_version": 3},
        "input_snapshot": {"analysis_target": task_id},
        "task_bundle": {
            "compiled_definition": {"compiled_contract_version": 3},
            "compiled_plan": {"compiled_contract_version": 3, "nodes": []},
        },
        "execution_status": "completed",
        "artifact_validation_status": "passed",
        "governance_status": "not_requested",
        "delivery_status": "ready",
        "agent_runs": [],
    }
    (task_dir / "task_run.json").write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return task_dir


def test_phase7_three_concurrent_v3_runs_keep_artifacts_and_events_isolated(
    tmp_path: Path,
) -> None:
    """Concurrent attempts share a root, never an artifact/event namespace."""
    from app.services.workbench_task_run import WorkbenchTaskRunStore
    from app.services.workbench_task_run_events import WorkbenchTaskRunEventStore

    artifact_root = tmp_path / "workbench_task_runs"
    run_ids = [f"task_run_phase7_isolated_{index}" for index in range(3)]
    barrier = threading.Barrier(len(run_ids))

    def write_run(index: int) -> tuple[str, bytes]:
        task_run_id = run_ids[index]
        marker = f"phase7-isolation-marker-{index}"
        task_dir = _write_v3_task_run(
            artifact_root,
            task_run_id=task_run_id,
            task_id=f"task_phase7_isolated_{index}",
        )
        report_bytes = f"# {marker}\n\nOnly this attempt owns this artifact.\n".encode(
            "utf-8"
        )
        barrier.wait(timeout=5)
        (task_dir / "report.md").write_bytes(report_bytes)
        events = WorkbenchTaskRunEventStore(artifact_root)
        events.append(task_run_id, "node_started", {"node_id": "analyze", "marker": marker})
        events.append(
            task_run_id,
            "artifact_created",
            {"artifact": "report.md", "marker": marker},
        )
        return task_run_id, report_bytes

    with ThreadPoolExecutor(max_workers=len(run_ids)) as executor:
        written = list(executor.map(write_run, range(len(run_ids))))

    run_store = WorkbenchTaskRunStore(artifact_root)
    event_store = WorkbenchTaskRunEventStore(artifact_root)
    assert {task_run_id for task_run_id, _ in written} == set(run_ids)

    for index, (task_run_id, report_bytes) in enumerate(written):
        task_run = run_store.load(task_run_id)
        task_dir = Path(task_run.artifact_dir)
        marker = f"phase7-isolation-marker-{index}"
        events = event_store.list_after(task_run_id, limit=10)

        assert task_run.task_id == f"task_phase7_isolated_{index}"
        assert task_dir == artifact_root / task_run_id
        assert (task_dir / "report.md").read_bytes() == report_bytes
        assert [event["event_id"] for event in events] == [1, 2]
        assert [event["task_run_id"] for event in events] == [task_run_id, task_run_id]
        assert {event["payload"]["marker"] for event in events} == {marker}
        assert all(
            f"phase7-isolation-marker-{other}" not in (task_dir / "report.md").read_text(
                encoding="utf-8"
            )
            for other in range(len(run_ids))
            if other != index
        )


def test_phase7_large_result_artifact_has_bounded_preview_and_exact_download(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Preview remains bounded while the explicit download preserves all result bytes."""
    from app.api import agent_workbench

    artifact_root = tmp_path / "workbench_task_runs"
    task_run_id = "task_run_phase7_large_results"
    task_dir = _write_v3_task_run(
        artifact_root,
        task_run_id=task_run_id,
        task_id="task_phase7_large_results",
    )
    payload = {
        "results": [
            {
                "result_id": f"result-{index:03d}",
                "summary": f"bounded preview fixture row {index}",
                "evidence": "x" * 80,
            }
            for index in range(101)
        ]
    }
    artifact_bytes = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    artifact_name = "report-results.json"
    (task_dir / artifact_name).write_bytes(artifact_bytes)
    monkeypatch.setattr(agent_workbench, "_task_runs_dir", lambda: artifact_root)

    preview = asyncio.run(
        agent_workbench.get_task_run_artifact_content(
            task_run_id,
            artifact_name,
            max_chars=512,
        )
    )
    manifest = asyncio.run(agent_workbench.list_task_run_artifacts(task_run_id))
    download = asyncio.run(
        agent_workbench.download_task_run_artifact(task_run_id, artifact_name)
    )
    manifest_entry = next(
        item for item in manifest["artifacts"] if item["relative_path"] == artifact_name
    )

    assert len(payload["results"]) == 101
    assert preview["truncated"] is True
    assert len(preview["content"]) <= 512
    assert preview["size_bytes"] == len(artifact_bytes)
    assert preview["sha256"] == hashlib.sha256(artifact_bytes).hexdigest()
    assert len(manifest_entry["preview"]) <= 1200
    assert download.body == artifact_bytes
    assert json.loads(download.body.decode("utf-8"))["results"] == payload["results"]


def test_phase7_frozen_node_timeouts_override_runtime_defaults(monkeypatch) -> None:
    """Published node policy, not mutable runtime defaults, owns V3 execution limits."""
    from app.services import workbench_task_run

    monkeypatch.setattr(
        workbench_task_run,
        "_agent_runtime_for_provider",
        lambda _provider: {
            "enabled": True,
            "timeout_seconds": 120,
            "prompt_transport": "codex_exec_json",
            "requires_network": False,
        },
    )

    limits = workbench_task_run._agent_task_runtime_limits(
        "agent-runtime:phase7-timeout-fixture",
        step={"timeout_sec": 30, "idle_timeout_sec": 2},
    )

    assert limits == {
        "timeout_seconds": 30,
        "idle_timeout_seconds": 2,
        "requires_network": False,
    }
