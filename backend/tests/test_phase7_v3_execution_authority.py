"""Phase 7 execution authority and rollback policy regression contracts."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


pytestmark = pytest.mark.asyncio


def _write_task_run(
    root: Path,
    *,
    task_run_id: str,
    compiled_contract_version: int | None,
    status: str = "completed",
) -> Path:
    task_dir = root / task_run_id
    task_dir.mkdir(parents=True)
    workflow_snapshot = {
        "id": "phase7-execution-authority",
        "steps": [{"id": "analyze", "type": "agent_task"}],
    }
    task_bundle: dict[str, object] = {}
    if compiled_contract_version is not None:
        workflow_snapshot["compiled_contract_version"] = compiled_contract_version
        task_bundle = {
            "compiled_definition": {
                "compiled_contract_version": compiled_contract_version,
            },
            "compiled_plan": {
                "compiled_contract_version": compiled_contract_version,
                "nodes": [],
            },
        }
    payload = {
        "task_run_id": task_run_id,
        "workflow_id": "phase7-execution-authority",
        "workspace_id": "phase7-execution-authority",
        "repo_path": str(task_dir),
        "artifact_dir": str(task_dir),
        "workflow_snapshot": workflow_snapshot,
        "input_snapshot": {},
        "task_bundle": task_bundle,
        "execution_status": status,
        "status": status,
        "agent_runs": [
            {
                "step_id": "analyze",
                "run_id": f"{task_run_id}_analyze",
                "artifact_dir": str(task_dir / "agent_runs" / "analyze"),
            }
        ],
    }
    (task_dir / "task_run.json").write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    if compiled_contract_version is not None:
        compiled_definition = task_bundle["compiled_definition"]
        definition_path = task_dir / "compiled_definition.json"
        definition_path.write_text(
            json.dumps(compiled_definition, sort_keys=True),
            encoding="utf-8",
        )
        (task_dir / "run_snapshot_v3.json").write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "snapshot_kind": "codetalk_run_snapshot",
                    "execution_contract": {
                        "compiled_contract_version": compiled_contract_version,
                    },
                    "components": {
                        "v3_runtime_contract": {
                            "path": "compiled_definition.json",
                            "sha256": hashlib.sha256(
                                definition_path.read_bytes()
                            ).hexdigest(),
                        }
                    },
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        (task_dir / "agent_execution_descriptors.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "agent_runs": [
                        {
                            "step_id": "analyze",
                            "run_id": f"{task_run_id}_analyze",
                        }
                    ],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    agent_dir = task_dir / "agent_runs" / "analyze"
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent_run.json").write_text(
        json.dumps(
            {
                "run_id": f"{task_run_id}_analyze",
                "status": "prepared",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return task_dir


def _tree_fingerprint(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize(
    "filename",
    [
        "task_run.json",
        "workflow_snapshot.json",
        "task_bundle.json",
        "compiled_definition.json",
        "compiled_plan.json",
        "agent_execution_descriptors.json",
        "run_snapshot_v3.json",
    ],
)
async def test_corrupt_frozen_attempt_marker_never_downgrades_to_legacy(
    tmp_path: Path,
    filename: str,
) -> None:
    from app.services.workflow_migration_policy import is_v3_attempt_candidate

    task_dir = tmp_path / "corrupt-attempt"
    task_dir.mkdir()
    (task_dir / filename).write_text("{tampered", encoding="utf-8")

    assert is_v3_attempt_candidate(task_dir) is True


@pytest.fixture
async def phase7_execution_client(tmp_path: Path, monkeypatch):
    from app.api import agent_workbench
    from app.config import settings

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "sqlite_db", str(data_dir / "codetalk.sqlite3"))
    monkeypatch.setattr(settings, "workflow_hitl_enabled", True)
    app = FastAPI()
    app.include_router(agent_workbench.router)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client, data_dir / "workbench" / "task_runs"


async def test_v3_task_agent_execute_is_rejected_before_facade_execution(
    phase7_execution_client,
    monkeypatch,
) -> None:
    from app.api import agent_workbench
    from app.config import settings

    client, task_runs_root = phase7_execution_client
    monkeypatch.setattr(settings, "workflow_v3_writes_enabled", True)
    task_run_id = "task_run_v3_direct_agent_blocked"
    task_dir = _write_task_run(
        task_runs_root,
        task_run_id=task_run_id,
        compiled_contract_version=3,
    )
    before = _tree_fingerprint(task_dir)

    def unexpected_facade_execution(*_args, **_kwargs):
        raise AssertionError("V3 task agents must execute only through the DAG scheduler")

    monkeypatch.setattr(
        agent_workbench.AgentHarnessFacade,
        "execute",
        unexpected_facade_execution,
    )

    response = await client.post(
        f"/api/workbench/task-runs/{task_run_id}/agent-runs/analyze/execute",
        json={"timeout_sec": 30},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "workflow_v3_scheduler_authority",
        "message": "V3 任务只能通过工作流调度器执行；不能单独执行任务内的 Agent 节点。",
    }
    assert _tree_fingerprint(task_dir) == before


async def test_partial_v3_attempt_cannot_fall_back_to_direct_agent_execution(
    phase7_execution_client,
    monkeypatch,
) -> None:
    from app.api import agent_workbench
    from app.config import settings

    client, task_runs_root = phase7_execution_client
    monkeypatch.setattr(settings, "workflow_v3_writes_enabled", True)
    task_run_id = "task_run_partial_v3_direct_agent_blocked"
    task_dir = _write_task_run(
        task_runs_root,
        task_run_id=task_run_id,
        compiled_contract_version=3,
    )
    payload = json.loads((task_dir / "task_run.json").read_text(encoding="utf-8"))
    payload["workflow_snapshot"].pop("compiled_contract_version")
    payload["task_bundle"] = {}
    (task_dir / "task_run.json").write_text(
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )
    (task_dir / "run_snapshot_v3.json").write_text("{tampered", encoding="utf-8")
    (task_dir / "compiled_definition.json").unlink()
    (task_dir / "agent_execution_descriptors.json").unlink()
    before = _tree_fingerprint(task_dir)

    def unexpected_facade_execution(*_args, **_kwargs):
        raise AssertionError("partial V3 attempts must fail closed")

    monkeypatch.setattr(
        agent_workbench.AgentHarnessFacade,
        "execute",
        unexpected_facade_execution,
    )

    response = await client.post(
        f"/api/workbench/task-runs/{task_run_id}/agent-runs/analyze/execute",
        json={"timeout_sec": 30},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "workflow_v3_scheduler_authority"
    assert _tree_fingerprint(task_dir) == before


async def test_corrupt_snapshot_only_v3_rollback_rejects_workflow_execution(
    phase7_execution_client,
    monkeypatch,
) -> None:
    from app.config import settings

    client, task_runs_root = phase7_execution_client
    monkeypatch.setattr(settings, "workflow_v3_writes_enabled", False)
    task_run_id = "task_run_snapshot_only_v3_read_only_execute"
    task_dir = _write_task_run(
        task_runs_root,
        task_run_id=task_run_id,
        compiled_contract_version=3,
        status="queued",
    )
    payload = json.loads((task_dir / "task_run.json").read_text(encoding="utf-8"))
    payload["workflow_snapshot"].pop("compiled_contract_version")
    payload["task_bundle"] = {}
    (task_dir / "task_run.json").write_text(
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )
    (task_dir / "run_snapshot_v3.json").write_text("{tampered", encoding="utf-8")
    (task_dir / "compiled_definition.json").unlink()
    (task_dir / "agent_execution_descriptors.json").unlink()
    before = _tree_fingerprint(task_dir)

    response = await client.post(
        f"/api/workbench/task-runs/{task_run_id}/execute",
        json={"timeout_sec": 30},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "workflow_v3_read_only"
    assert _tree_fingerprint(task_dir) == before


@pytest.mark.parametrize(
    ("suffix", "payload"),
    [
        ("/validate-mr-artifacts", {"required_artifacts": []}),
        (
            "/materialize-evidence",
            {"required_artifacts": [], "object_text": "must not materialize"},
        ),
    ],
)
async def test_v3_task_agent_post_execution_actions_stay_scheduler_owned(
    phase7_execution_client,
    monkeypatch,
    suffix: str,
    payload: dict[str, object],
) -> None:
    from app.config import settings

    client, task_runs_root = phase7_execution_client
    monkeypatch.setattr(settings, "workflow_v3_writes_enabled", True)
    task_run_id = "task_run_v3_agent_action_blocked"
    task_dir = _write_task_run(
        task_runs_root,
        task_run_id=task_run_id,
        compiled_contract_version=3,
    )
    before = _tree_fingerprint(task_dir)

    response = await client.post(
        f"/api/workbench/task-runs/{task_run_id}/agent-runs/analyze{suffix}",
        json=payload,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "workflow_v3_scheduler_authority"
    assert _tree_fingerprint(task_dir) == before


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/execute", {"timeout_sec": 30}),
        ("/rerun-plan/execute", {"timeout_sec": 30}),
        (
            "/approvals/release-approval/decision",
            {
                "decision": "approve",
                "actor": "phase7-reviewer",
                "reason": "must remain read-only",
                "decided_at": "2026-07-29T08:00:00Z",
            },
        ),
    ],
)
async def test_v3_rollback_rejects_existing_attempt_resume_paths_without_mutation(
    phase7_execution_client,
    monkeypatch,
    path: str,
    payload: dict[str, object],
) -> None:
    from app.config import settings

    client, task_runs_root = phase7_execution_client
    monkeypatch.setattr(settings, "workflow_v3_writes_enabled", False)
    task_run_id = "task_run_v3_read_only_existing"
    task_dir = _write_task_run(
        task_runs_root,
        task_run_id=task_run_id,
        compiled_contract_version=3,
    )
    before = _tree_fingerprint(task_dir)

    response = await client.post(
        f"/api/workbench/task-runs/{task_run_id}{path}",
        json=payload,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "workflow_v3_read_only",
        "message": "V3 工作流当前处于只读回滚模式；历史工作流、任务和产物仍可查看与下载。",
    }
    assert _tree_fingerprint(task_dir) == before


async def test_v3_rollback_rejects_cancel_without_mutating_running_attempt(
    phase7_execution_client,
    monkeypatch,
) -> None:
    from app.config import settings

    client, task_runs_root = phase7_execution_client
    monkeypatch.setattr(settings, "workflow_v3_writes_enabled", False)
    task_run_id = "task_run_v3_read_only_cancel"
    task_dir = _write_task_run(
        task_runs_root,
        task_run_id=task_run_id,
        compiled_contract_version=3,
        status="running",
    )
    before = _tree_fingerprint(task_dir)

    response = await client.post(
        f"/api/workbench/task-runs/{task_run_id}/cancel",
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "workflow_v3_read_only"
    assert _tree_fingerprint(task_dir) == before


async def test_partial_v3_rollback_rejects_acceptance_audit_without_mutation(
    phase7_execution_client,
    monkeypatch,
) -> None:
    from app.config import settings

    client, task_runs_root = phase7_execution_client
    monkeypatch.setattr(settings, "workflow_v3_writes_enabled", False)
    task_run_id = "task_run_partial_v3_read_only_acceptance_audit"
    task_dir = _write_task_run(
        task_runs_root,
        task_run_id=task_run_id,
        compiled_contract_version=3,
    )
    payload = json.loads((task_dir / "task_run.json").read_text(encoding="utf-8"))
    payload["workflow_snapshot"].pop("compiled_contract_version")
    payload["task_bundle"] = {}
    (task_dir / "task_run.json").write_text(
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )
    (task_dir / "run_snapshot_v3.json").write_text("{tampered", encoding="utf-8")
    (task_dir / "compiled_definition.json").unlink()
    before = _tree_fingerprint(task_dir)

    response = await client.post(
        f"/api/workbench/task-runs/{task_run_id}/acceptance-audit",
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "workflow_v3_read_only",
        "message": "V3 工作流当前处于只读回滚模式；历史工作流、任务和产物仍可查看与下载。",
    }
    assert _tree_fingerprint(task_dir) == before


async def test_partial_v3_acceptance_audit_stays_on_v3_no_op_path(
    phase7_execution_client,
    monkeypatch,
) -> None:
    from app.api import agent_workbench
    from app.config import settings

    client, task_runs_root = phase7_execution_client
    monkeypatch.setattr(settings, "workflow_v3_writes_enabled", True)
    task_run_id = "task_run_partial_v3_acceptance_audit_no_op"
    task_dir = _write_task_run(
        task_runs_root,
        task_run_id=task_run_id,
        compiled_contract_version=3,
    )
    payload = json.loads((task_dir / "task_run.json").read_text(encoding="utf-8"))
    payload["workflow_snapshot"].pop("compiled_contract_version")
    payload["task_bundle"] = {}
    (task_dir / "task_run.json").write_text(
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )
    (task_dir / "run_snapshot_v3.json").write_text("{tampered", encoding="utf-8")
    (task_dir / "compiled_definition.json").unlink()
    before = _tree_fingerprint(task_dir)

    def unexpected_legacy_audit(*_args, **_kwargs):
        raise AssertionError("partial V3 attempts must not run the legacy audit")

    monkeypatch.setattr(
        agent_workbench.WorkbenchWorkflowRunner,
        "audit_test_activity_quality",
        unexpected_legacy_audit,
    )

    response = await client.post(
        f"/api/workbench/task-runs/{task_run_id}/acceptance-audit",
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "not_applicable",
        "reason": "frozen_contract_uses_validation_profile",
        "compiled_contract_version": 3,
    }
    assert _tree_fingerprint(task_dir) == before


async def test_v3_rollback_get_projects_frozen_outcomes_without_backfill_writes(
    phase7_execution_client,
    monkeypatch,
) -> None:
    from app.config import settings

    client, task_runs_root = phase7_execution_client
    monkeypatch.setattr(settings, "workflow_v3_writes_enabled", False)
    task_run_id = "task_run_v3_read_only_get"
    task_dir = _write_task_run(
        task_runs_root,
        task_run_id=task_run_id,
        compiled_contract_version=3,
        status="queued",
    )
    (task_dir / "workflow_execution.json").write_text(
        json.dumps(
            {
                "compiled_contract_version": 3,
                "execution_status": "completed",
                "artifact_validation_status": "passed",
                "governance_status": "not_requested",
                "delivery_status": "ready",
                "quality_status": "passed",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    before = _tree_fingerprint(task_dir)

    response = await client.get(f"/api/workbench/task-runs/{task_run_id}")

    assert response.status_code == 200
    assert response.json()["execution_status"] == "completed"
    assert response.json()["delivery_status"] == "ready"
    assert _tree_fingerprint(task_dir) == before


async def test_partial_v3_rollback_get_projects_legacy_outcomes_without_writes(
    phase7_execution_client,
    monkeypatch,
) -> None:
    from app.config import settings

    client, task_runs_root = phase7_execution_client
    monkeypatch.setattr(settings, "workflow_v3_writes_enabled", False)
    task_run_id = "task_run_partial_v3_read_only_get"
    task_dir = _write_task_run(
        task_runs_root,
        task_run_id=task_run_id,
        compiled_contract_version=3,
        status="completed",
    )
    payload = json.loads((task_dir / "task_run.json").read_text(encoding="utf-8"))
    payload["workflow_snapshot"].pop("compiled_contract_version")
    payload["task_bundle"] = {}
    payload["quality_status"] = "not_checked"
    payload["delivery_status"] = "none"
    (task_dir / "task_run.json").write_text(
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )
    (task_dir / "run_snapshot_v3.json").write_text("{tampered", encoding="utf-8")
    (task_dir / "compiled_definition.json").unlink()
    (task_dir / "workflow_execution.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "outputs": [{"path": "report.md", "status": "completed"}],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    before = _tree_fingerprint(task_dir)

    response = await client.get(f"/api/workbench/task-runs/{task_run_id}")

    assert response.status_code == 200
    assert response.json()["task_run_id"] == task_run_id
    assert _tree_fingerprint(task_dir) == before


async def test_v3_rollback_does_not_disable_legacy_task_run_compatibility(
    phase7_execution_client,
    monkeypatch,
) -> None:
    from app.config import settings

    client, task_runs_root = phase7_execution_client
    monkeypatch.setattr(settings, "workflow_v3_writes_enabled", False)
    task_run_id = "task_run_legacy_still_readable_and_runnable"
    task_dir = _write_task_run(
        task_runs_root,
        task_run_id=task_run_id,
        compiled_contract_version=None,
        status="queued",
    )
    (task_dir / "agent_execution_descriptors.json").write_text(
        json.dumps({"schema_version": 1, "agent_runs": []}, sort_keys=True),
        encoding="utf-8",
    )
    (task_dir / "run_snapshot_v3.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "snapshot_kind": "codetalk_run_snapshot",
                "execution_contract": {"compiled_contract_version": None},
                "components": {
                    "stage_specs": {"path": "stage_specs.json", "sha256": "legacy"},
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    response = await client.post(
        f"/api/workbench/task-runs/{task_run_id}/execute",
        json={"timeout_sec": 30},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/materialize-outputs", None),
        ("/semantic-cases/import-outputs", {}),
    ],
)
async def test_v3_rollback_rejects_post_run_mutations_without_side_effects(
    phase7_execution_client,
    monkeypatch,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    from app.config import settings

    client, task_runs_root = phase7_execution_client
    monkeypatch.setattr(settings, "workflow_v3_writes_enabled", False)
    task_run_id = "task_run_v3_read_only_post_run"
    task_dir = _write_task_run(
        task_runs_root,
        task_run_id=task_run_id,
        compiled_contract_version=3,
    )
    before = _tree_fingerprint(task_dir)

    response = await client.post(
        f"/api/workbench/task-runs/{task_run_id}{path}",
        json=payload,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "workflow_v3_read_only"
    assert _tree_fingerprint(task_dir) == before


@pytest.mark.parametrize("path", ["/rerun-plan", "/rerun-plan/validation"])
async def test_v3_rollback_rerun_reads_do_not_materialize_plan_files(
    phase7_execution_client,
    monkeypatch,
    path: str,
) -> None:
    from app.config import settings

    client, task_runs_root = phase7_execution_client
    monkeypatch.setattr(settings, "workflow_v3_writes_enabled", False)
    task_run_id = "task_run_v3_read_only_rerun_read"
    task_dir = _write_task_run(
        task_runs_root,
        task_run_id=task_run_id,
        compiled_contract_version=3,
    )
    before = _tree_fingerprint(task_dir)

    response = await client.get(
        f"/api/workbench/task-runs/{task_run_id}{path}",
    )

    assert response.status_code == 200
    assert _tree_fingerprint(task_dir) == before


async def test_v3_rollback_blocks_startup_recovery_without_mutating_attempt(
    phase7_execution_client,
    monkeypatch,
) -> None:
    from app.config import settings
    from app.services.workflow_startup_recovery import reconcile_v3_startup_recovery

    _client, task_runs_root = phase7_execution_client
    monkeypatch.setattr(settings, "workflow_v3_writes_enabled", False)
    task_run_id = "task_run_v3_read_only_startup"
    task_dir = _write_task_run(
        task_runs_root,
        task_run_id=task_run_id,
        compiled_contract_version=3,
        status="running",
    )
    before = _tree_fingerprint(task_dir)

    decisions = reconcile_v3_startup_recovery(task_runs_root)

    assert [(item.task_run_id, item.action) for item in decisions] == [
        (task_run_id, "read_only")
    ]
    assert _tree_fingerprint(task_dir) == before


async def test_v3_rollback_rechecks_policy_before_background_execution(
    phase7_execution_client,
    monkeypatch,
) -> None:
    from app.api import agent_workbench
    from app.config import settings

    _client, task_runs_root = phase7_execution_client
    monkeypatch.setattr(settings, "workflow_v3_writes_enabled", False)
    task_run_id = "task_run_v3_read_only_background"
    task_dir = _write_task_run(
        task_runs_root,
        task_run_id=task_run_id,
        compiled_contract_version=3,
        status="queued",
    )
    before = _tree_fingerprint(task_dir)

    async def unexpected_preflight(_task_run_id: str):
        raise AssertionError("read-only V3 execution must stop before preflight")

    monkeypatch.setattr(
        agent_workbench,
        "_preflight_task_run_agent_runtimes",
        unexpected_preflight,
    )

    await agent_workbench._execute_task_run_background(
        task_run_id=task_run_id,
        payload=agent_workbench.TaskRunExecuteRequest(timeout_sec=30),
    )
    await asyncio.sleep(0)

    assert _tree_fingerprint(task_dir) == before


async def test_v3_rollback_does_not_schedule_recovered_attempt(
    phase7_execution_client,
    monkeypatch,
) -> None:
    from app.api import agent_workbench
    from app.config import settings

    _client, task_runs_root = phase7_execution_client
    monkeypatch.setattr(settings, "workflow_v3_writes_enabled", False)
    task_run_id = "task_run_v3_read_only_recovery_schedule"
    task_dir = _write_task_run(
        task_runs_root,
        task_run_id=task_run_id,
        compiled_contract_version=3,
        status="running",
    )
    before = _tree_fingerprint(task_dir)

    assert agent_workbench.schedule_recovered_v3_task_run(task_run_id) is False
    assert _tree_fingerprint(task_dir) == before
