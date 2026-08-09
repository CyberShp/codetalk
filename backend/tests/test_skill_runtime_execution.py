from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.harness_facade import HarnessRunRequest
from app.services.provider_adapters.cli_base import CliProviderRunResult
from app.services.provider_adapters.contracts import (
    ArtifactCandidate,
    CancelResult,
    ProviderCapabilities,
    ProviderResumeToken,
    ProviderSession,
)
from app.services.workbench_task_run import PreparedWorkbenchTaskRun
from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner


def _persist_skill_run(tmp_path: Path) -> PreparedWorkbenchTaskRun:
    run_id = "skill-runtime-run"
    task_dir = tmp_path / run_id
    task_dir.mkdir(parents=True)
    definition = {
        "compiled_contract_version": 3,
        "validation_profile": "none",
        "declared_outputs": [],
        "outputs": [],
        "steps": [{"id": "skill.step-01", "type": "skill_step"}],
    }
    plan = {
        "compiled_contract_version": 3,
        "nodes": [
            {"node_id": "skill.step-01", "type": "skill_step", "depends_on": []}
        ],
        "topological_order": ["skill.step-01"],
    }
    task_run = PreparedWorkbenchTaskRun(
        task_run_id=run_id,
        workflow_id="skill.example",
        workspace_id="workspace",
        repo_path=str(tmp_path),
        artifact_dir=str(task_dir),
        workflow_snapshot=definition,
        input_snapshot={},
        task_bundle={"compiled_definition": definition, "compiled_plan": plan},
        agent_runs=[],
    )
    (task_dir / "task_run.json").write_text(
        json.dumps(
            {
                "task_run_id": task_run.task_run_id,
                "workflow_id": task_run.workflow_id,
                "workspace_id": task_run.workspace_id,
                "repo_path": task_run.repo_path,
                "artifact_dir": task_run.artifact_dir,
                "workflow_snapshot": task_run.workflow_snapshot,
                "input_snapshot": task_run.input_snapshot,
                "task_bundle": task_run.task_bundle,
                "agent_runs": [],
            }
        ),
        encoding="utf-8",
    )
    components: dict[str, dict[str, str]] = {}
    for component_id, filename, payload in (
        ("v3_runtime_contract", "compiled_definition.json", definition),
        ("execution_plan", "compiled_plan.json", plan),
        ("input_snapshot", "input_snapshot.json", {}),
        ("agent_execution_descriptors", "agent_execution_descriptors.json", {"schema_version": 1, "agent_runs": []}),
    ):
        path = task_dir / filename
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        components[component_id] = {
            "path": filename,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    (task_dir / "run_snapshot_v3.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "snapshot_kind": "codetalk_run_snapshot",
                "components": components,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return task_run


def test_skill_run_fails_closed_without_real_agent_runtime(tmp_path: Path) -> None:
    task_run = _persist_skill_run(tmp_path)
    Path(task_run.artifact_dir, "skill_invocation.json").write_text(
        json.dumps(
            {
                "schema_version": "skill-run-invocation-v1",
                "invocation_id": "skill_invocation_without_runtime",
                "task_run_id": task_run.task_run_id,
                "artifact_root": "artifacts",
                "runtime": {"producer": None, "judge": None},
            }
        ),
        encoding="utf-8",
    )

    events: list[tuple[str, dict]] = []
    result = WorkbenchWorkflowRunner(
        tmp_path,
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    ).execute_task_run(task_run.task_run_id)

    assert result.execution_status == "failed"
    assert result.delivery_status == "blocked"
    assert result.step_results[0]["technical_diagnostics"]["error"] == (
        "skill_agent_runtime_unavailable"
    )
    assert "fake-skill_invocation" not in json.dumps(events)


def test_skill_run_rejects_declared_runtime_without_executable_snapshot(
    tmp_path: Path,
) -> None:
    task_run = _persist_skill_run(tmp_path)
    Path(task_run.artifact_dir, "skill_invocation.json").write_text(
        json.dumps(
            {
                "schema_version": "skill-run-invocation-v1",
                "invocation_id": "skill_invocation_placeholder_runtime",
                "task_run_id": task_run.task_run_id,
                "artifact_root": "artifacts",
                "runtime": {
                    "producer": {
                        "runtime_id": "runtime/producer/local",
                        "requested_provider": "opencode",
                    },
                    "judge": None,
                },
            }
        ),
        encoding="utf-8",
    )

    result = WorkbenchWorkflowRunner(tmp_path).execute_task_run(task_run.task_run_id)

    assert result.execution_status == "failed"
    assert result.step_results[0]["technical_diagnostics"]["error"] == (
        "skill_agent_runtime_unavailable"
    )


def test_task_runtime_selection_freezes_only_configured_enabled_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import workbench_v2_tasks

    runtime = {
        "id": "default-opencode",
        "provider": "opencode",
        "command": "opencode",
        "args": [],
        "prompt_transport": "opencode_run_arg",
        "enabled": True,
    }
    monkeypatch.setattr(
        workbench_v2_tasks,
        "get_agent_runtime_sync",
        lambda runtime_id: runtime if runtime_id == runtime["id"] else None,
    )
    task = SimpleNamespace(
        execution_overrides={"agent_runtime_id": "default-opencode"}
    )

    assert workbench_v2_tasks._selected_agent_runtime(task) == runtime

    task.execution_overrides = {"agent_runtime_id": "missing"}
    with pytest.raises(ValueError, match="Agent Runtime 不存在"):
        workbench_v2_tasks._selected_agent_runtime(task)

    task.execution_overrides = {}
    with pytest.raises(ValueError, match="请选择 Agent Runtime"):
        workbench_v2_tasks._selected_agent_runtime(task)


@pytest.mark.asyncio
async def test_agent_runtime_preflight_must_pass_before_attempt_freeze(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import workbench_v2_tasks

    runtime = {"id": "default-opencode", "command": "opencode"}
    monkeypatch.setattr(
        workbench_v2_tasks,
        "probe_agent_runtime",
        lambda _: _async_result({"success": True, "message": "ok"}),
    )

    receipt = await workbench_v2_tasks._agent_runtime_preflight(runtime)

    assert receipt["status"] == "passed"
    assert receipt["credential_ready"] is False
    assert "message" not in receipt

    monkeypatch.setattr(
        workbench_v2_tasks,
        "probe_agent_runtime",
        lambda _: _async_result({"success": False, "message": "auth failed"}),
    )
    with pytest.raises(ValueError, match="auth failed"):
        await workbench_v2_tasks._agent_runtime_preflight(runtime)


async def _async_result(value: object) -> object:
    return value


def test_skill_compat_contract_preserves_artifacts_judge_and_dependencies() -> None:
    from app.api.workbench_v2_tasks import _skill_compat_definition, _skill_plan

    version = SimpleNamespace(skill_id="skill.example")
    skill_ir = {
        "required_agent_capabilities": ["tools", "artifact_collection"],
        "inputs": [],
        "steps": [
            {"step_id": "step.collect", "depends_on": []},
            {"step_id": "step.report", "depends_on": ["step.collect"]},
        ],
        "artifacts": [
            {
                "artifact_id": "artifact.report",
                "path": "report.md",
                "producer_step_id": "step.report",
                "required": True,
            }
        ],
        "deliveries": [
            {
                "delivery_id": "delivery.report",
                "artifact_ids": ["artifact.report"],
            }
        ],
        "judge": {
            "required": True,
            "isolated_session": True,
            "artifact_ids": ["artifact.report"],
        },
        "topological_order": ["step.collect", "step.report"],
    }

    definition = _skill_compat_definition(version, skill_ir)
    plan = _skill_plan(skill_ir)

    assert definition["artifacts"] == skill_ir["artifacts"]
    assert definition["judge"] == skill_ir["judge"]
    assert definition["required_agent_capabilities"] == skill_ir[
        "required_agent_capabilities"
    ]
    assert plan["topological_order"] == ["step.collect", "step.report"]
    assert plan["nodes"][1]["depends_on"] == ["step.collect"]
    assert definition["declared_outputs"] == [
        {
            "output_id": "delivery.report",
            "artifact": "report.md",
            "producer_step_id": "step.report",
            "required": True,
        }
    ]


class _RecordingProviderAdapter:
    def __init__(self, artifact_root: Path) -> None:
        self.artifact_root = artifact_root
        self.requests: list[HarnessRunRequest] = []
        self.resume_tokens: list[ProviderResumeToken] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True,
            tool_call=True,
            session_resume=True,
            structured_output=False,
            mcp=True,
            skills=False,
            cancellation=True,
        )

    def prepare(self, request: HarnessRunRequest) -> ProviderSession:
        self.requests.append(request)
        return ProviderSession(
            session_id="real-provider-session-1",
            provider="opencode",
            requires_network=False,
            artifact_dir=str(self.artifact_root),
        )

    def execute(self, session: ProviderSession, **_: object) -> CliProviderRunResult:
        required = list(self.requests[-1].task_bundle["required_artifacts"])
        if required == ["skill_judge_report.json"]:
            (self.artifact_root / "skill_judge_report.json").write_text(
                json.dumps(
                    {
                        "status": "READY",
                        "ready": True,
                        "warnings": [],
                        "checked_artifact_ids": ["artifact.report"],
                    }
                ),
                encoding="utf-8",
            )
        else:
            (self.artifact_root / "report.md").write_text(
                "# real output\n", encoding="utf-8"
            )
        return CliProviderRunResult(
            run_id=session.session_id,
            status="completed",
            exit_code=0,
            started_at="2026-08-10T00:00:00Z",
            completed_at="2026-08-10T00:00:01Z",
            duration_ms=1000,
            artifacts=["report.md"],
            provider_diagnostics={
                "resume_token": {
                    "provider": "opencode",
                    "value": "opaque-provider-session-1",
                }
            },
        )

    def resume(
        self,
        session: ProviderSession,
        resume_from: ProviderResumeToken,
        **kwargs: object,
    ) -> CliProviderRunResult:
        self.resume_tokens.append(resume_from)
        return self.execute(session, **kwargs)

    def cancel(self, session: ProviderSession) -> CancelResult:
        return CancelResult(session_id=session.session_id, status="cancelled")

    def record_raw_output(self, *_: object, **__: object) -> None:
        return None

    def collect_artifacts(self, _: ProviderSession) -> list[ArtifactCandidate]:
        required = list(self.requests[-1].task_bundle["required_artifacts"])
        return [
            ArtifactCandidate(path=path, metadata={"provider": "opencode"})
            for path in required
        ]


def test_skill_step_executes_through_real_harness_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.skill_agent_adapter as skill_adapter_module

    task_run = _persist_skill_run(tmp_path)
    definition = task_run.task_bundle["compiled_definition"]
    definition["steps"][0].update(
        {
            "step_id": "skill.step-01",
            "instruction_path": "steps/01.md",
            "completion_gate": {"required_artifact_ids": ["artifact.report"]},
        }
    )
    definition["artifacts"] = [
        {
            "artifact_id": "artifact.report",
            "path": "report.md",
            "producer_step_id": "skill.step-01",
            "required": True,
        }
    ]
    _persist_rewritten_definition(task_run)
    artifact_root = Path(task_run.artifact_dir, "artifacts")
    artifact_root.mkdir()
    adapter = _RecordingProviderAdapter(artifact_root)
    monkeypatch.setattr(
        skill_adapter_module,
        "create_provider_adapter",
        lambda **_: adapter,
    )
    Path(task_run.artifact_dir, "skill_invocation.json").write_text(
        json.dumps(
            {
                "schema_version": "skill-run-invocation-v1",
                "invocation_id": "skill_invocation_real_runtime",
                "task_run_id": task_run.task_run_id,
                "artifact_root": "artifacts",
                "runtime": {
                    "producer": {
                        "runtime_id": "agent-runtime:default-opencode",
                        "requested_provider": "opencode",
                        "timeout_budget": {"agent_timeout_seconds": 30},
                        "execution": {
                            "runtime_config_id": "default-opencode",
                            "provider_ref": "agent-runtime:default-opencode",
                            "command": ["opencode"],
                            "prompt_transport": "opencode_run_arg",
                            "mcp_profile": "",
                            "requires_network": False,
                        },
                    },
                    "judge": None,
                },
            }
        ),
        encoding="utf-8",
    )

    result = WorkbenchWorkflowRunner(tmp_path).execute_task_run(task_run.task_run_id)

    assert result.execution_status == "completed", result.step_results
    assert len(adapter.requests) == 1
    request = adapter.requests[0]
    assert request.provider == "agent-runtime:default-opencode"
    assert request.command == ["opencode"]
    assert "skill.step-01" in request.task_bundle["rendered_user_input"]
    assert Path(task_run.artifact_dir, "artifacts", "report.md").is_file()
    assert result.step_results[0]["agent_session_id"] == "real-provider-session-1"
    assert "fake-" not in json.dumps(result.step_results)


def test_required_skill_judge_runs_in_a_separate_real_session_before_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.skill_agent_adapter as skill_adapter_module

    task_run = _persist_skill_run(tmp_path)
    definition = task_run.task_bundle["compiled_definition"]
    definition["steps"][0].update(
        {
            "step_id": "skill.step-01",
            "instruction_path": "steps/01.md",
            "completion_gate": {"required_artifact_ids": ["artifact.report"]},
        }
    )
    definition["artifacts"] = [
        {
            "artifact_id": "artifact.report",
            "path": "report.md",
            "producer_step_id": "skill.step-01",
            "required": True,
        }
    ]
    definition["declared_outputs"] = [
        {
            "output_id": "delivery.report",
            "artifact": "report.md",
            "producer_step_id": "skill.step-01",
            "required": True,
        }
    ]
    definition["judge"] = {
        "required": True,
        "isolated_session": True,
        "artifact_ids": ["artifact.report"],
    }
    task_run.task_bundle["skill_judge_required"] = True
    _persist_rewritten_definition(task_run)

    adapters: list[_RecordingProviderAdapter] = []

    def create_adapter(**kwargs: object) -> _RecordingProviderAdapter:
        adapter = _RecordingProviderAdapter(Path(str(kwargs["artifact_dir"])))
        adapters.append(adapter)
        return adapter

    monkeypatch.setattr(skill_adapter_module, "create_provider_adapter", create_adapter)
    runtime = {
        "runtime_id": "agent-runtime:default-opencode",
        "requested_provider": "opencode",
        "timeout_budget": {"agent_timeout_seconds": 30},
        "execution": {
            "runtime_config_id": "default-opencode",
            "provider_ref": "agent-runtime:default-opencode",
            "command": ["opencode"],
            "prompt_transport": "opencode_run_arg",
            "mcp_profile": "",
            "requires_network": False,
        },
    }
    Path(task_run.artifact_dir, "skill_invocation.json").write_text(
        json.dumps(
            {
                "schema_version": "skill-run-invocation-v1",
                "invocation_id": "skill_invocation_judged",
                "task_run_id": task_run.task_run_id,
                "artifact_root": "artifacts",
                "required_artifact_ids": ["artifact.report"],
                "judge": definition["judge"],
                "runtime": {"producer": runtime, "judge": runtime},
            }
        ),
        encoding="utf-8",
    )

    result = WorkbenchWorkflowRunner(tmp_path).execute_task_run(task_run.task_run_id)

    assert result.execution_status == "completed", result.step_results
    assert result.artifact_validation_status == "passed"
    assert result.governance_status == "passed"
    assert result.delivery_status == "ready"
    assert len(adapters) == 2
    assert adapters[0].requests[0].run_id != adapters[1].requests[0].run_id
    assert Path(task_run.artifact_dir, "skill_judge_report.json").is_file()
    assert result.outputs[0]["status"] == "ok"


def test_multiple_skill_steps_resume_one_producer_provider_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.skill_agent_adapter as skill_adapter_module

    task_run = _persist_skill_run(tmp_path)
    definition = task_run.task_bundle["compiled_definition"]
    definition["steps"] = [
        {
            "step_id": "skill.step-01",
            "instruction_path": "steps/01.md",
            "depends_on": [],
            "completion_gate": {"required_artifact_ids": ["artifact.first"]},
        },
        {
            "step_id": "skill.step-02",
            "instruction_path": "steps/02.md",
            "depends_on": ["skill.step-01"],
            "completion_gate": {"required_artifact_ids": ["artifact.second"]},
        },
    ]
    definition["artifacts"] = [
        {
            "artifact_id": "artifact.first",
            "path": "report.md",
            "producer_step_id": "skill.step-01",
            "required": True,
        },
        {
            "artifact_id": "artifact.second",
            "path": "report.md",
            "producer_step_id": "skill.step-02",
            "required": True,
        },
    ]
    plan = task_run.task_bundle["compiled_plan"]
    plan["nodes"] = [
        {"node_id": "skill.step-01", "type": "skill_step", "depends_on": []},
        {
            "node_id": "skill.step-02",
            "type": "skill_step",
            "depends_on": ["skill.step-01"],
        },
    ]
    plan["topological_order"] = ["skill.step-01", "skill.step-02"]
    _persist_rewritten_definition(task_run)
    artifact_root = Path(task_run.artifact_dir, "artifacts")
    artifact_root.mkdir()
    adapter = _RecordingProviderAdapter(artifact_root)
    monkeypatch.setattr(
        skill_adapter_module, "create_provider_adapter", lambda **_: adapter
    )
    runtime = {
        "runtime_id": "agent-runtime:default-opencode",
        "requested_provider": "opencode",
        "timeout_budget": {"agent_timeout_seconds": 30},
        "execution": {
            "runtime_config_id": "default-opencode",
            "provider_ref": "agent-runtime:default-opencode",
            "command": ["opencode"],
            "prompt_transport": "opencode_run_arg",
            "mcp_profile": "",
            "requires_network": False,
        },
    }
    Path(task_run.artifact_dir, "skill_invocation.json").write_text(
        json.dumps(
            {
                "schema_version": "skill-run-invocation-v1",
                "invocation_id": "skill_invocation_resumed",
                "task_run_id": task_run.task_run_id,
                "artifact_root": "artifacts",
                "runtime": {"producer": runtime, "judge": None},
            }
        ),
        encoding="utf-8",
    )

    result = WorkbenchWorkflowRunner(tmp_path).execute_task_run(task_run.task_run_id)

    assert result.execution_status == "completed", result.step_results
    assert len(adapter.requests) == 2
    assert len(adapter.resume_tokens) == 1
    assert adapter.resume_tokens[0].value == "opaque-provider-session-1"
    assert {
        item["agent_session_id"]
        for item in result.step_results
        if item["type"] == "skill_step"
    } == {"real-provider-session-1"}


def _persist_rewritten_definition(task_run: PreparedWorkbenchTaskRun) -> None:
    task_dir = Path(task_run.artifact_dir)
    definition = task_run.task_bundle["compiled_definition"]
    (task_dir / "task_run.json").write_text(
        json.dumps(
            {
                "task_run_id": task_run.task_run_id,
                "workflow_id": task_run.workflow_id,
                "workspace_id": task_run.workspace_id,
                "repo_path": task_run.repo_path,
                "artifact_dir": task_run.artifact_dir,
                "workflow_snapshot": definition,
                "input_snapshot": task_run.input_snapshot,
                "task_bundle": task_run.task_bundle,
                "agent_runs": [],
            }
        ),
        encoding="utf-8",
    )
    definition_path = task_dir / "compiled_definition.json"
    definition_path.write_text(json.dumps(definition, sort_keys=True), encoding="utf-8")
    snapshot_path = task_dir / "run_snapshot_v3.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["components"]["v3_runtime_contract"]["sha256"] = hashlib.sha256(
        definition_path.read_bytes()
    ).hexdigest()
    plan_path = task_dir / "compiled_plan.json"
    plan_path.write_text(
        json.dumps(task_run.task_bundle["compiled_plan"], sort_keys=True),
        encoding="utf-8",
    )
    snapshot["components"]["execution_plan"]["sha256"] = hashlib.sha256(
        plan_path.read_bytes()
    ).hexdigest()
    snapshot_path.write_text(json.dumps(snapshot, sort_keys=True), encoding="utf-8")
