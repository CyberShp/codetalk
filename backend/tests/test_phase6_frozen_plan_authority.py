from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_frozen_plan_attempt(
    root: Path,
    *,
    plan_path: str = "compiled_plan.json",
) -> dict:
    plan = {
        "compiled_contract_version": 3,
        "nodes": [{"node_id": "agent"}],
        "topological_order": ["agent"],
    }
    _write_json(root / "compiled_plan.json", plan)
    _write_json(
        root / "run_snapshot_v3.json",
        {
            "schema_version": 3,
            "snapshot_kind": "codetalk_run_snapshot",
            "components": {
                "execution_plan": {
                    "path": plan_path,
                    "sha256": _sha256(root / "compiled_plan.json"),
                },
            },
        },
    )
    return plan


def test_load_frozen_compiled_plan_accepts_only_the_declared_normalized_plan(tmp_path: Path) -> None:
    from app.services.workbench_task_run import load_frozen_compiled_plan

    expected = _write_frozen_plan_attempt(tmp_path, plan_path=".\\compiled_plan.json")

    assert load_frozen_compiled_plan(tmp_path) == expected


@pytest.mark.parametrize(
    "fixture",
    [
        "missing_snapshot",
        "malformed_snapshot",
        "missing_execution_plan",
        "wrong_execution_plan_target",
        "stale_snapshot",
        "missing_plan",
        "tampered_plan",
        "malformed_plan",
    ],
)
def test_load_frozen_compiled_plan_rejects_invalid_authority_without_leaking_details(
    tmp_path: Path,
    fixture: str,
) -> None:
    from app.services.workbench_task_run import (
        FrozenCompiledPlanAuthorityError,
        load_frozen_compiled_plan,
    )

    if fixture == "malformed_snapshot":
        (tmp_path / "run_snapshot_v3.json").write_text("{", encoding="utf-8")
    else:
        _write_frozen_plan_attempt(tmp_path)
        snapshot_path = tmp_path / "run_snapshot_v3.json"
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if fixture == "missing_snapshot":
            snapshot_path.unlink()
        elif fixture == "missing_execution_plan":
            snapshot["components"] = {}
            _write_json(snapshot_path, snapshot)
        elif fixture == "wrong_execution_plan_target":
            _write_json(tmp_path / "other_plan.json", {"untrusted": True})
            snapshot["components"]["execution_plan"]["path"] = "other_plan.json"
            snapshot["components"]["execution_plan"]["sha256"] = _sha256(
                tmp_path / "other_plan.json"
            )
            _write_json(snapshot_path, snapshot)
        elif fixture == "stale_snapshot":
            snapshot["components"]["execution_plan"]["sha256"] = "0" * 64
            _write_json(snapshot_path, snapshot)
        elif fixture == "missing_plan":
            (tmp_path / "compiled_plan.json").unlink()
        elif fixture == "tampered_plan":
            _write_json(tmp_path / "compiled_plan.json", {"tampered": True})
        elif fixture == "malformed_plan":
            (tmp_path / "compiled_plan.json").write_text("[", encoding="utf-8")
            snapshot["components"]["execution_plan"]["sha256"] = _sha256(
                tmp_path / "compiled_plan.json"
            )
            _write_json(snapshot_path, snapshot)

    with pytest.raises(
        FrozenCompiledPlanAuthorityError,
        match="^Frozen compiled plan is unavailable or invalid\\.$",
    ) as error:
        load_frozen_compiled_plan(tmp_path)

    assert "compiled_plan.json" not in str(error.value)
    assert "run_snapshot_v3.json" not in str(error.value)


def test_approval_authorization_fails_closed_when_frozen_plan_hash_changes(
    tmp_path: Path,
) -> None:
    from app.api.agent_workbench import _task_run_human_approval_node

    plan = {
        "compiled_contract_version": 3,
        "nodes": [{
            "node_id": "release-approval",
            "kind": "human_approval",
            "handler_id": "human_approval",
        }],
        "topological_order": ["release-approval"],
    }
    _write_json(tmp_path / "compiled_plan.json", plan)
    _write_json(
        tmp_path / "run_snapshot_v3.json",
        {
            "schema_version": 3,
            "snapshot_kind": "codetalk_run_snapshot",
            "components": {
                "execution_plan": {
                    "path": "compiled_plan.json",
                    "sha256": _sha256(tmp_path / "compiled_plan.json"),
                },
            },
        },
    )
    task_run = SimpleNamespace(artifact_dir=str(tmp_path), task_bundle={})

    assert _task_run_human_approval_node(task_run, "release-approval") == plan["nodes"][0]

    _write_json(
        tmp_path / "compiled_plan.json",
        {
            **plan,
            "nodes": [{
                "node_id": "tampered-approval",
                "kind": "human_approval",
                "handler_id": "human_approval",
            }],
        },
    )

    assert _task_run_human_approval_node(task_run, "tampered-approval") is None
