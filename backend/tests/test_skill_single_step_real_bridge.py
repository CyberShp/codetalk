from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.skill_single_step_bridge import (
    BRIDGE_MODE,
    build_single_step_compat_definition,
    build_single_step_skill_plan,
    install_skill_single_step_bridge,
)


def _skill_ir() -> dict:
    return {
        "skill_id": "skill.example",
        "topological_order": ["step.01", "step.02"],
        "inputs": [
            {
                "input_id": "input.source",
                "kind": "workspace",
                "required": True,
            }
        ],
        "steps": [
            {
                "step_id": "step.01",
                "title": "Inspect source scope",
                "instruction_path": "steps/01.md",
                "depends_on": [],
                "produces": ["artifact.scope"],
            },
            {
                "step_id": "step.02",
                "title": "Second step is intentionally deferred",
                "instruction_path": "steps/02.md",
                "depends_on": ["step.01"],
                "produces": ["artifact.second"],
            },
        ],
        "artifacts": [
            {
                "artifact_id": "artifact.scope",
                "path": "artifacts/scope.md",
                "producer_step_id": "step.01",
                "required": True,
            },
            {
                "artifact_id": "artifact.second",
                "path": "artifacts/second.md",
                "producer_step_id": "step.02",
                "required": True,
            },
        ],
        "core_rules": [
            {
                "rule_id": "rule.evidence",
                "instruction_path": "rules/evidence.md",
            }
        ],
    }


def _version(tmp_path):
    unpacked = tmp_path / "unpacked"
    (unpacked / "steps").mkdir(parents=True)
    (unpacked / "rules").mkdir(parents=True)
    (unpacked / "steps" / "01.md").write_text(
        "Inspect the repository and write a source-backed scope report.",
        encoding="utf-8",
    )
    (unpacked / "rules" / "evidence.md").write_text(
        "Every conclusion must cite source evidence.",
        encoding="utf-8",
    )
    return SimpleNamespace(
        skill_id="skill.example",
        version_id="version.example",
        unpacked_root=unpacked,
        version_root=tmp_path,
        ir_path=tmp_path / "ir.json",
    )


def test_bridge_compiles_only_first_step_as_real_agent(tmp_path) -> None:
    plan = build_single_step_skill_plan(_skill_ir())
    definition = build_single_step_compat_definition(_version(tmp_path), _skill_ir())

    assert plan["bridge_mode"] == BRIDGE_MODE
    assert plan["topological_order"] == ["step.01"]
    assert plan["nodes"] == [
        {
            "node_id": "step.01",
            "kind": "agent",
            "type": "agent_task",
            "handler_id": "agent",
            "depends_on": [],
            "failure_policy": "stop",
            "required_outputs": ["artifact.scope"],
        }
    ]
    assert definition["steps"][0]["type"] == "agent_task"
    assert definition["steps"][0]["provider"] == "opencode"
    assert definition["steps"][0]["required_artifacts"] == [
        "artifacts/scope.md"
    ]
    assert "production Agent run" in definition["steps"][0]["goal"]
    assert "Every conclusion must cite source evidence" in definition["steps"][0]["goal"]
    assert definition["declared_outputs"][0]["producer_step_id"] == "step.01"
    assert all(step.get("type") != "skill_step" for step in definition["steps"])


def test_bridge_installs_idempotently() -> None:
    module = SimpleNamespace()

    install_skill_single_step_bridge(module)
    first_plan = module._skill_plan
    install_skill_single_step_bridge(module)

    assert module._single_step_real_skill_bridge_installed is True
    assert module._skill_plan is first_plan
    assert module._skill_compat_definition is build_single_step_compat_definition


def test_bridge_fails_closed_without_required_artifact(tmp_path) -> None:
    skill_ir = _skill_ir()
    skill_ir["artifacts"][0]["required"] = False

    with pytest.raises(ValueError, match="does not declare a required artifact"):
        build_single_step_compat_definition(_version(tmp_path), skill_ir)


def test_single_step_run_prepares_agent_run_and_crosses_harness_boundary(
    tmp_path,
    monkeypatch,
) -> None:
    import json
    from dataclasses import asdict
    from pathlib import Path

    from app.api import workbench_v2_tasks as task_api
    from app.services.harness_facade import HarnessRunResult
    from app.services.workbench_task_run import (
        WorkbenchTaskRunPreparer,
        refresh_run_snapshot_v3,
    )
    from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner

    skill_ir = _skill_ir()
    version = _version(tmp_path)
    definition = build_single_step_compat_definition(version, skill_ir)
    plan = build_single_step_skill_plan(skill_ir)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "module.c").write_text(
        "int module_entry(void) { return 0; }\n",
        encoding="utf-8",
    )

    class FrozenWorkflowStore:
        def freeze_workflow_snapshot(self, workflow_id: str) -> dict:
            assert workflow_id == "skill.example"
            return json.loads(json.dumps(definition))

    prepared = WorkbenchTaskRunPreparer(
        artifact_root=tmp_path / "task_runs",
        workflow_store=FrozenWorkflowStore(),
    ).prepare(
        workflow_id="skill.example",
        workspace_id="workspace",
        repo_path=str(repo),
        inputs={"input.source": str(repo)},
        workflow_snapshot_override=definition,
    )
    assert len(prepared.agent_runs) == 1
    assert prepared.agent_runs[0]["step_id"] == "step.01"
    assert not str(prepared.agent_runs[0]["run_id"]).startswith("fake-")

    prepared.task_bundle["compiled_plan"] = plan
    prepared.task_bundle["effective_compiled_definition"] = definition
    task_api._write_run(prepared)
    refresh_run_snapshot_v3(prepared.artifact_dir)

    calls: list[str] = []
    agent_artifact_dir = Path(prepared.agent_runs[0]["artifact_dir"])

    class RealBoundaryFacade:
        def execute(self, session_id, **_kwargs):
            calls.append(str(session_id))
            target = agent_artifact_dir / "artifacts" / "scope.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# Real provider boundary\n", encoding="utf-8")
            return HarnessRunResult(
                session_id=str(session_id),
                status="completed",
                exit_code=0,
                started_at="2026-08-06T00:00:00Z",
                completed_at="2026-08-06T00:00:01Z",
                duration_ms=1000,
                artifacts=["artifacts/scope.md"],
            )

    monkeypatch.setattr(
        WorkbenchWorkflowRunner,
        "_prepare_provider_facade_for_step",
        lambda *_args, **_kwargs: (
            RealBoundaryFacade(),
            "opencode-real-session",
            [],
        ),
    )

    result = WorkbenchWorkflowRunner(tmp_path / "task_runs").execute_task_run(
        prepared.task_run_id
    )

    assert calls == ["opencode-real-session"]
    assert result.execution_status == "completed"
    assert result.step_results[0]["type"] == "agent_task"
    assert result.step_results[0]["status"] == "completed"
    assert "fake" not in json.dumps(asdict(result), ensure_ascii=False)
    assert (agent_artifact_dir / "artifacts" / "scope.md").is_file()
