from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
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


_SELECTED_WORKFLOW_PATH = "workflows/module-analysis.md"
_STEP_01_INSTRUCTION_PATH = "steps/01-intake-and-scope.md"
_STEP_02_INSTRUCTION_PATH = "steps/02-evidence-consumption.md"
_CORE_RULE_PATHS = (
    "references/path-fidelity.md",
    "references/evidence-consumption.md",
    "references/markdown-narrative-first.md",
)


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
    input_snapshot = {
        "input.source": str(tmp_path / "module-under-test"),
        "analysis.goal": "trace the module startup flow",
    }
    execution_profile = {
        "id": "rapid",
        "label": "速度型",
        "expected_duration_minutes": [8, 20],
    }
    task_run = PreparedWorkbenchTaskRun(
        task_run_id=run_id,
        workflow_id="skill.example",
        workspace_id="workspace",
        repo_path=str(tmp_path),
        artifact_dir=str(task_dir),
        workflow_snapshot=definition,
        input_snapshot=input_snapshot,
        task_bundle={
            "compiled_definition": definition,
            "compiled_plan": plan,
            "execution_profile": execution_profile,
        },
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
        ("input_snapshot", "input_snapshot.json", input_snapshot),
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
    source_root = task_dir / "frozen_skill" / "source"
    (source_root / "steps").mkdir(parents=True)
    (source_root / "workflows").mkdir()
    (source_root / "references").mkdir()
    (source_root / "SKILL.md").write_text(
        "# CodeTalks full analysis\nFollow the complete analysis method.\n",
        encoding="utf-8",
    )
    (source_root / _STEP_01_INSTRUCTION_PATH).write_text(
        "# Intake and scope\nRead the frozen task input before analysis.\n",
        encoding="utf-8",
    )
    (source_root / _STEP_02_INSTRUCTION_PATH).write_text(
        "# Evidence consumption\nConsume the verified predecessor artifacts.\n",
        encoding="utf-8",
    )
    (source_root / _SELECTED_WORKFLOW_PATH).write_text(
        "# Module analysis workflow\nFollow the declared step order.\n",
        encoding="utf-8",
    )
    for rule_path in _CORE_RULE_PATHS:
        (source_root / rule_path).write_text(
            f"# {Path(rule_path).stem}\nThis core rule is mandatory.\n",
            encoding="utf-8",
        )
    source_file_digests = [
        {
            "path": path.relative_to(source_root).as_posix(),
            "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(source_root.rglob("*"))
        if path.is_file()
    ]
    skill_ir_path = task_dir / "frozen_skill" / "skill-ir-v1.json"
    skill_ir_path.write_text(
        json.dumps(
            {
                "schema_version": "codetalk-skill-v1",
                "selected_workflow_path": _SELECTED_WORKFLOW_PATH,
                "source_file_digests": source_file_digests,
                "core_rules": [
                    {
                        "rule_id": f"rule.{Path(path).stem}",
                        "instruction_path": path,
                        "acknowledgement_required": True,
                    }
                    for path in _CORE_RULE_PATHS
                ],
                "steps": [
                    {
                        "step_id": "skill.step-01",
                        "instruction_path": _STEP_01_INSTRUCTION_PATH,
                    },
                    {
                        "step_id": "skill.step-02",
                        "instruction_path": _STEP_02_INSTRUCTION_PATH,
                    },
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (task_dir / "skill_input_snapshot.json").write_text(
        json.dumps(input_snapshot, sort_keys=True),
        encoding="utf-8",
    )
    return task_run


def _frozen_reference(task_run: PreparedWorkbenchTaskRun, relative_path: str) -> dict:
    path = Path(task_run.artifact_dir, relative_path)
    return {
        "ref": relative_path,
        "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
        "access_scope": "read",
    }


def _seal_skill_invocation(task_run: PreparedWorkbenchTaskRun) -> None:
    path = Path(task_run.artifact_dir, "skill_invocation.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["invocation_digest"] = "sha256:" + "0" * 64
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload["invocation_digest"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_executable_skill_invocation(
    task_run: PreparedWorkbenchTaskRun,
    *,
    judge: dict | None = None,
) -> dict:
    runtime = {
        "runtime_id": "agent-runtime:default-opencode",
        "requested_provider": "opencode",
        "timeout_budget": {
            "profile_id": "rapid",
            "idle_timeout_seconds": 300,
            "step_timeout_seconds": 1200,
            "agent_timeout_seconds": 1200,
            "overall_timeout_seconds": 1800,
        },
        "execution": {
            "runtime_config_id": "default-opencode",
            "provider_ref": "agent-runtime:default-opencode",
            "command": ["opencode"],
            "prompt_transport": "opencode_run_arg",
            "mcp_profile": "",
            "requires_network": False,
        },
    }
    payload = {
        "schema_version": "skill-run-invocation-v1",
        "invocation_id": "skill_invocation_test_runtime",
        "task_run_id": task_run.task_run_id,
        "artifact_root": "artifacts",
        "skill_ir": _frozen_reference(
            task_run, "frozen_skill/skill-ir-v1.json"
        ),
        "input_snapshot": _frozen_reference(
            task_run, "skill_input_snapshot.json"
        ),
        "judge": dict(judge or {}),
        "runtime": {
            "producer": runtime,
            "judge": runtime if judge is not None else None,
        },
    }
    path = Path(task_run.artifact_dir, "skill_invocation.json")
    path.write_text(json.dumps(payload), encoding="utf-8")
    _seal_skill_invocation(task_run)
    return json.loads(path.read_text(encoding="utf-8"))


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
    _seal_skill_invocation(task_run)

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
    def __init__(
        self,
        artifact_root: Path,
        *,
        after_execute: Callable[[], None] | None = None,
    ) -> None:
        self.artifact_root = artifact_root
        self.requests: list[HarnessRunRequest] = []
        self.resume_tokens: list[ProviderResumeToken] = []
        self.execution_timeouts: list[int] = []
        self.after_execute = after_execute

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

    def execute(
        self,
        session: ProviderSession,
        **kwargs: object,
    ) -> CliProviderRunResult:
        self.execution_timeouts.append(int(kwargs.get("timeout_sec") or 0))
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
            for relative_path in required:
                path = self.artifact_root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# real output\n", encoding="utf-8")
        if self.after_execute is not None:
            self.after_execute()
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


class _TimedOutProviderAdapter(_RecordingProviderAdapter):
    def execute(
        self,
        session: ProviderSession,
        **kwargs: object,
    ) -> CliProviderRunResult:
        self.execution_timeouts.append(int(kwargs.get("timeout_sec") or 0))
        return CliProviderRunResult(
            run_id=session.session_id,
            status="failed",
            exit_code=None,
            started_at="2026-08-10T00:00:00Z",
            completed_at="2026-08-10T00:30:00Z",
            duration_ms=1_800_000,
            timed_out=True,
            error="执行器超过安全运行上限（1800s）；token=provider-secret",
            provider_diagnostics={
                "output": "read 42 files\ntoken=provider-secret",
                "resume_token": {
                    "provider": "opencode",
                    "value": "opaque-resume-session",
                },
                "provider_session": {
                    "session_id": "opaque-upstream-session",
                    "event_type": "session_created",
                },
            },
        )

    def collect_artifacts(self, _: ProviderSession) -> list[ArtifactCandidate]:
        return []


def test_independent_review_step_uses_fresh_judge_session_and_node_scoped_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.skill_agent_adapter as skill_adapter_module
    from app.services.skill_agent_adapter import execute_skill_step

    task_run = _persist_skill_run(tmp_path)
    definition = task_run.task_bundle["compiled_definition"]
    definition["steps"] = [
        {
            "step_id": "skill.step-01",
            "instruction_path": _STEP_01_INSTRUCTION_PATH,
            "completion_gate": {"required_artifact_ids": ["artifact.first"]},
        },
        {
            "step_id": "skill.step-08",
            "instruction_path": _STEP_02_INSTRUCTION_PATH,
            "completion_gate": {
                "required_artifact_ids": [
                    "artifact.review",
                    "artifact.internal-judge-state",
                ],
            },
        },
        {
            "step_id": "skill.step-09",
            "instruction_path": _STEP_02_INSTRUCTION_PATH,
            "completion_gate": {"required_artifact_ids": ["artifact.final"]},
        },
    ]
    definition["artifacts"] = [
        {
            "artifact_id": "artifact.first",
            "path": "first.md",
            "producer_step_id": "skill.step-01",
            "required": True,
        },
        {
            "artifact_id": "artifact.review",
            "path": "review.md",
            "producer_step_id": "skill.step-08",
            "required": True,
        },
        {
            "artifact_id": "artifact.internal-judge-state",
            "path": "judge-state.json",
            "producer_step_id": "skill.step-08",
            "required": True,
        },
        {
            "artifact_id": "artifact.final",
            "path": "final.md",
            "producer_step_id": "skill.step-09",
            "required": True,
        },
    ]
    _persist_rewritten_definition(task_run)
    invocation = _write_executable_skill_invocation(
        task_run,
        judge={
            "required": True,
            "isolated_session": True,
            "artifact_ids": ["artifact.final"],
        },
    )
    artifact_root = Path(task_run.artifact_dir, "artifacts")
    artifact_root.mkdir()

    class EventAdapter(_RecordingProviderAdapter):
        def execute(self, session: ProviderSession, **kwargs: object) -> CliProviderRunResult:
            sink = kwargs.get("event_sink")
            if callable(sink):
                sink(
                    "activity",
                    {
                        "text": "independent progress",
                        "step_id": "spoofed.step",
                        "node_id": "spoofed.node",
                    },
                )
            result = super().execute(session, **kwargs)
            required = self.requests[-1].task_bundle["required_artifacts"]
            if "judge-state.json" in required:
                (self.artifact_root / "judge-state.json").write_text(
                    json.dumps(
                        {
                            "independent": True,
                            "checked_artifacts": ["first.md"],
                        }
                    ),
                    encoding="utf-8",
                )
            return result

    adapter = EventAdapter(artifact_root)
    monkeypatch.setattr(
        skill_adapter_module,
        "create_provider_adapter",
        lambda **_: adapter,
    )
    events: list[tuple[str, dict]] = []
    results: list[dict] = []
    for node_id in ("skill.step-01", "skill.step-08", "skill.step-09"):
        result = execute_skill_step(
            task_run=task_run,
            node={"node_id": node_id},
            invocation=invocation,
            resolved_inputs={},
            execution_profile={"id": "rapid"},
            prior_step_results=list(results),
            timeout_sec=1200,
            event_sink=lambda event_type, payload: events.append((event_type, payload)),
        )
        results.append(result)

    independent_run_id = (
        f"{task_run.task_run_id}_skill_independent_"
        f"{hashlib.sha256(b'skill.step-08').hexdigest()[:12]}"
    )
    assert [request.run_id for request in adapter.requests] == [
        f"{task_run.task_run_id}_skill_producer",
        independent_run_id,
        f"{task_run.task_run_id}_skill_producer",
    ]
    assert len(adapter.resume_tokens) == 1
    assert "Do not launch nested agents or subagents" in (
        adapter.requests[1].task_bundle["rendered_user_input"]
    )
    progress_events = [
        payload
        for _event_type, payload in events
        if payload.get("text") == "independent progress"
    ]
    assert [payload.get("step_id") for payload in progress_events] == [
        "skill.step-01",
        "skill.step-08",
        "skill.step-09",
    ]
    assert [payload.get("node_id") for payload in progress_events] == [
        "skill.step-01",
        "skill.step-08",
        "skill.step-09",
    ]

    invalid_adapter = _RecordingProviderAdapter(artifact_root)
    monkeypatch.setattr(
        skill_adapter_module,
        "create_provider_adapter",
        lambda **_: invalid_adapter,
    )
    invalid_review = execute_skill_step(
        task_run=task_run,
        node={"node_id": "skill.step-08"},
        invocation=invocation,
        resolved_inputs={},
        execution_profile={"id": "rapid"},
        prior_step_results=[results[0]],
        timeout_sec=1200,
    )
    assert invalid_review["status"] == "error"
    assert invalid_review["error"] == "skill_independent_judge_state_invalid"

    invocation_path = Path(task_run.artifact_dir, "skill_invocation.json")
    without_judge = json.loads(invocation_path.read_text(encoding="utf-8"))
    without_judge["runtime"]["judge"] = None
    invocation_path.write_text(json.dumps(without_judge), encoding="utf-8")
    _seal_skill_invocation(task_run)
    without_judge = json.loads(invocation_path.read_text(encoding="utf-8"))
    with pytest.raises(
        skill_adapter_module.SkillAgentAdapterError,
        match="skill_independent_judge_runtime_unavailable",
    ):
        execute_skill_step(
            task_run=task_run,
            node={"node_id": "skill.step-08"},
            invocation=without_judge,
            resolved_inputs={},
            execution_profile={"id": "rapid"},
            prior_step_results=[results[0]],
            timeout_sec=1200,
        )


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
            "instruction_path": _STEP_01_INSTRUCTION_PATH,
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
    oversized_source_text = "static void startup(void) {}\n" * 1000
    task_run.input_snapshot["analysis.payload"] = oversized_source_text
    task_run.task_bundle["execution_profile"] = {
        "id": "deep",
        "label": "深度型",
        "expected_duration_minutes": [40, 90],
    }
    task_run.task_bundle["compiled_plan"]["nodes"][0].update(
        {
            "input_ports": [
                {"id": "source_scope", "required": True},
                {"id": "analysis_payload", "required": True},
            ],
            "resolved_input_bindings": {
                "source_scope": {
                    "source_node_id": "input.source",
                    "source_input_id": "input.source",
                    "source_port_id": "value",
                },
                "analysis_payload": {
                    "source_node_id": "analysis.payload",
                    "source_input_id": "analysis.payload",
                    "source_port_id": "value",
                },
            },
        }
    )
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
                "skill_ir": _frozen_reference(
                    task_run, "frozen_skill/skill-ir-v1.json"
                ),
                "input_snapshot": _frozen_reference(
                    task_run, "skill_input_snapshot.json"
                ),
                "runtime": {
                    "producer": {
                        "runtime_id": "agent-runtime:default-opencode",
                        "requested_provider": "opencode",
                        "timeout_budget": {
                            "agent_timeout_seconds": 30,
                            "overall_timeout_seconds": 60,
                        },
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
    _seal_skill_invocation(task_run)

    result = WorkbenchWorkflowRunner(tmp_path).execute_task_run(task_run.task_run_id)

    assert result.execution_status == "completed", result.step_results
    assert len(adapter.requests) == 1
    request = adapter.requests[0]
    assert request.provider == "agent-runtime:default-opencode"
    assert request.command == ["opencode"]
    assert request.task_bundle["resolved_inputs"] == {
        "analysis_payload": {
            "kind": "string",
            "characters": len(oversized_source_text),
            "source": "frozen_input_snapshot",
        },
        "source_scope": task_run.input_snapshot["input.source"],
    }
    assert request.task_bundle["execution_profile"]["id"] == "deep"
    assert request.task_bundle["frozen_input_snapshot"]["ref"] == (
        "skill_input_snapshot.json"
    )
    assert request.task_bundle["selected_workflow_path"] == _SELECTED_WORKFLOW_PATH
    assert request.task_bundle["step_instruction_path"] == (
        _STEP_01_INSTRUCTION_PATH
    )
    assert request.task_bundle["skill_entrypoint"]["ref"] == "SKILL.md"
    assert [
        item["instruction_path"]
        for item in request.task_bundle["core_rules"]
    ] == list(_CORE_RULE_PATHS)
    prompt = request.task_bundle["rendered_user_input"]
    assert "skill.step-01" in prompt
    assert _SELECTED_WORKFLOW_PATH in prompt
    assert _STEP_01_INSTRUCTION_PATH in prompt
    assert "SKILL.md" in prompt
    for rule_path in _CORE_RULE_PATHS:
        assert rule_path in prompt
    assert "skill_input_snapshot.json" in prompt
    assert "deep" in prompt
    assert "report.md" in prompt
    assert "analysis_payload" in prompt
    assert oversized_source_text not in prompt
    assert oversized_source_text not in json.dumps(
        request.task_bundle,
        ensure_ascii=False,
    )
    assert Path(task_run.artifact_dir, "artifacts", "report.md").is_file()
    assert result.step_results[0]["agent_session_id"] == "real-provider-session-1"
    assert "fake-" not in json.dumps(result.step_results)


@pytest.mark.parametrize(
    ("relative_path", "mutation", "expected_error"),
    [
        (
            "frozen_skill/skill-ir-v1.json",
            '{"tampered": true}',
            "skill_ir_reference_digest_mismatch",
        ),
        (
            "skill_input_snapshot.json",
            '{"input.source": "tampered"}',
            "skill_input_snapshot_reference_digest_mismatch",
        ),
        (
            f"frozen_skill/source/{_STEP_01_INSTRUCTION_PATH}",
            "# tampered step instruction\n",
            "skill_source_digest_mismatch",
        ),
    ],
)
def test_skill_step_rejects_tampered_frozen_execution_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_path: str,
    mutation: str,
    expected_error: str,
) -> None:
    import app.services.skill_agent_adapter as skill_adapter_module

    task_run = _persist_skill_run(tmp_path)
    definition = task_run.task_bundle["compiled_definition"]
    definition["steps"][0].update(
        {
            "step_id": "skill.step-01",
            "instruction_path": _STEP_01_INSTRUCTION_PATH,
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
    Path(task_run.artifact_dir, "artifacts").mkdir()
    _write_executable_skill_invocation(task_run)
    Path(task_run.artifact_dir, relative_path).write_text(
        mutation,
        encoding="utf-8",
    )
    monkeypatch.setattr(
        skill_adapter_module,
        "create_provider_adapter",
        lambda **_: pytest.fail("tampered frozen inputs must fail before provider start"),
    )

    result = WorkbenchWorkflowRunner(tmp_path).execute_task_run(task_run.task_run_id)

    assert result.execution_status == "failed"
    assert result.step_results[0]["error"] == "节点执行失败，请重试。"
    assert result.step_results[0]["technical_diagnostics"]["error"] == expected_error


@pytest.mark.parametrize(
    "mutation",
    ["extra_file", "nested_symlink", "source_root_symlink"],
)
def test_skill_step_rejects_unlisted_frozen_source_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    import app.services.skill_agent_adapter as skill_adapter_module

    task_run = _persist_skill_run(tmp_path)
    definition = task_run.task_bundle["compiled_definition"]
    definition["steps"][0].update(
        {
            "step_id": "skill.step-01",
            "instruction_path": _STEP_01_INSTRUCTION_PATH,
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
    Path(task_run.artifact_dir, "artifacts").mkdir()
    _write_executable_skill_invocation(task_run)
    source_root = Path(task_run.artifact_dir, "frozen_skill", "source")
    if mutation == "extra_file":
        (source_root / "unlisted.md").write_text("unlisted\n", encoding="utf-8")
    elif mutation == "nested_symlink":
        extras = source_root / "extras"
        extras.mkdir()
        (extras / "escape.md").symlink_to(
            Path(task_run.artifact_dir, "skill_input_snapshot.json")
        )
    else:
        relocated_source = Path(task_run.artifact_dir, "relocated-skill-source")
        source_root.rename(relocated_source)
        source_root.symlink_to(relocated_source, target_is_directory=True)
    monkeypatch.setattr(
        skill_adapter_module,
        "create_provider_adapter",
        lambda **_: pytest.fail("unlisted frozen source must fail before provider start"),
    )

    result = WorkbenchWorkflowRunner(tmp_path).execute_task_run(task_run.task_run_id)

    assert result.execution_status == "failed"
    assert result.step_results[0]["error"] == "节点执行失败，请重试。"
    assert result.step_results[0]["technical_diagnostics"]["error"] == (
        "skill_source_digest_inventory_mismatch"
    )


def test_skill_step_timeout_preserves_redacted_provider_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.skill_agent_adapter as skill_adapter_module

    task_run = _persist_skill_run(tmp_path)
    definition = task_run.task_bundle["compiled_definition"]
    definition["steps"][0].update(
        {
            "step_id": "skill.step-01",
            "instruction_path": _STEP_01_INSTRUCTION_PATH,
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
    adapter = _TimedOutProviderAdapter(artifact_root)
    monkeypatch.setattr(
        skill_adapter_module,
        "create_provider_adapter",
        lambda **_: adapter,
    )
    Path(task_run.artifact_dir, "skill_invocation.json").write_text(
        json.dumps(
            {
                "schema_version": "skill-run-invocation-v1",
                "invocation_id": "skill_invocation_timed_out",
                "task_run_id": task_run.task_run_id,
                "artifact_root": "artifacts",
                "skill_ir": _frozen_reference(
                    task_run, "frozen_skill/skill-ir-v1.json"
                ),
                "input_snapshot": _frozen_reference(
                    task_run, "skill_input_snapshot.json"
                ),
                "runtime": {
                    "producer": {
                        "runtime_id": "agent-runtime:default-opencode",
                        "requested_provider": "opencode",
                        "timeout_budget": {
                            "profile_id": "rapid",
                            "idle_timeout_seconds": 300,
                            "step_timeout_seconds": 1200,
                            "agent_timeout_seconds": 1200,
                            "overall_timeout_seconds": 1800,
                        },
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
    _seal_skill_invocation(task_run)

    result = WorkbenchWorkflowRunner(tmp_path).execute_task_run(task_run.task_run_id)

    assert result.execution_status == "timed_out"
    step_result = result.step_results[0]
    assert step_result["status"] == "timed_out"
    assert step_result["timed_out"] is True
    assert step_result["timeout_kind"] == "agent"
    assert step_result["artifact_dir"] == str(artifact_root)
    assert step_result["agent_session_id"] == "real-provider-session-1"
    assert step_result["provider_session"] == {
        "session_id": "real-provider-session-1",
        "provider_session": {
            "session_id": "opaque-upstream-session",
            "event_type": "session_created",
        },
        "resume_session": {
            "provider": "opencode",
            "session_id": "opaque-resume-session",
        },
    }
    assert step_result["provider_diagnostics"]["output_tail"].startswith(
        "read 42 files"
    )
    assert step_result["technical_diagnostics"]["provider_status"] == "failed"
    serialized = json.dumps(step_result, ensure_ascii=False)
    assert "provider-secret" not in serialized
    assert "<redacted>" in serialized


def test_required_skill_judge_runs_in_a_separate_real_session_before_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.skill_agent_adapter as skill_adapter_module
    import app.services.workbench_workflow_runner as runner_module

    task_run = _persist_skill_run(tmp_path)
    definition = task_run.task_bundle["compiled_definition"]
    definition["steps"][0].update(
        {
            "step_id": "skill.step-01",
            "instruction_path": _STEP_01_INSTRUCTION_PATH,
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

    monotonic_clock = {"value": 100.0}
    monkeypatch.setattr(
        runner_module.time,
        "monotonic",
        lambda: monotonic_clock["value"],
    )
    adapters: list[_RecordingProviderAdapter] = []

    def create_adapter(**kwargs: object) -> _RecordingProviderAdapter:
        after_execute: Callable[[], None] | None = None
        if not adapters:
            after_execute = lambda: monotonic_clock.__setitem__("value", 115.0)
        adapter = _RecordingProviderAdapter(
            Path(str(kwargs["artifact_dir"])),
            after_execute=after_execute,
        )
        adapters.append(adapter)
        return adapter

    monkeypatch.setattr(skill_adapter_module, "create_provider_adapter", create_adapter)
    runtime = {
        "runtime_id": "agent-runtime:default-opencode",
        "requested_provider": "opencode",
        "timeout_budget": {
            "profile_id": "rapid",
            "idle_timeout_seconds": 7,
            "step_timeout_seconds": 22,
            "agent_timeout_seconds": 30,
            "overall_timeout_seconds": 30,
        },
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
                "skill_ir": _frozen_reference(
                    task_run, "frozen_skill/skill-ir-v1.json"
                ),
                "input_snapshot": _frozen_reference(
                    task_run, "skill_input_snapshot.json"
                ),
                "required_artifact_ids": ["artifact.report"],
                "judge": definition["judge"],
                "runtime": {"producer": runtime, "judge": runtime},
            }
        ),
        encoding="utf-8",
    )
    _seal_skill_invocation(task_run)

    events: list[tuple[str, dict[str, Any]]] = []
    result = WorkbenchWorkflowRunner(
        tmp_path,
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    ).execute_task_run(task_run.task_run_id)

    assert result.execution_status == "completed", result.step_results
    assert result.artifact_validation_status == "passed"
    assert result.governance_status == "passed"
    assert result.delivery_status == "ready"
    assert len(adapters) == 2
    assert adapters[0].requests[0].run_id != adapters[1].requests[0].run_id
    assert [adapter.requests[0].timeout_seconds for adapter in adapters] == [22, 15]
    assert [adapter.requests[0].idle_timeout_seconds for adapter in adapters] == [
        7,
        7,
    ]
    assert [adapter.execution_timeouts for adapter in adapters] == [[22], [15]]
    assert Path(task_run.artifact_dir, "skill_judge_report.json").is_file()
    assert result.outputs[0]["status"] == "ok"
    judge_finished_index = next(
        index
        for index, (event_type, payload) in enumerate(events)
        if event_type == "step_completed" and payload.get("step_id") == "skill.judge"
    )
    run_completed_indexes = [
        index
        for index, (event_type, _payload) in enumerate(events)
        if event_type == "run_completed"
    ]
    assert len(run_completed_indexes) == 1
    assert run_completed_indexes[0] > judge_finished_index


def test_skill_judge_provider_events_are_authoritatively_node_scoped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.skill_agent_adapter as skill_adapter_module

    task_run = _persist_skill_run(tmp_path)
    definition = task_run.task_bundle["compiled_definition"]
    definition["artifacts"] = [
        {
            "artifact_id": "artifact.report",
            "path": "report.md",
            "producer_step_id": "skill.step-01",
            "required": True,
        }
    ]
    judge = {
        "required": True,
        "isolated_session": True,
        "artifact_ids": ["artifact.report"],
    }
    definition["judge"] = judge
    _persist_rewritten_definition(task_run)
    artifact_root = Path(task_run.artifact_dir, "artifacts")
    artifact_root.mkdir()
    (artifact_root / "report.md").write_text("# evidence\n", encoding="utf-8")
    invocation = _write_executable_skill_invocation(task_run, judge=judge)

    class EventJudgeAdapter(_RecordingProviderAdapter):
        def execute(self, session: ProviderSession, **kwargs: object) -> CliProviderRunResult:
            sink = kwargs.get("event_sink")
            if callable(sink):
                sink(
                    "activity",
                    {
                        "text": "judge progress",
                        "step_id": "spoofed.step",
                        "node_id": "spoofed.node",
                    },
                )
            return super().execute(session, **kwargs)

    monkeypatch.setattr(
        skill_adapter_module,
        "create_provider_adapter",
        lambda **_: EventJudgeAdapter(Path(task_run.artifact_dir)),
    )
    events: list[tuple[str, dict[str, Any]]] = []

    result = skill_adapter_module.execute_skill_judge(
        task_run=task_run,
        invocation=invocation,
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )

    assert result["status"] == "completed"
    progress = [
        payload
        for _event_type, payload in events
        if payload.get("text") == "judge progress"
    ]
    assert len(progress) == 1
    assert progress[0]["step_id"] == "skill.judge"
    assert progress[0]["node_id"] == "skill.judge"


def test_skill_judge_timeout_preserves_typed_provider_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.skill_agent_adapter as skill_adapter_module

    task_run = _persist_skill_run(tmp_path)
    definition = task_run.task_bundle["compiled_definition"]
    definition["artifacts"] = [
        {
            "artifact_id": "artifact.report",
            "path": "report.md",
            "producer_step_id": "skill.step-01",
            "required": True,
        }
    ]
    judge = {
        "required": True,
        "isolated_session": True,
        "artifact_ids": ["artifact.report"],
    }
    definition["judge"] = judge
    _persist_rewritten_definition(task_run)
    artifact_root = Path(task_run.artifact_dir, "artifacts")
    artifact_root.mkdir()
    (artifact_root / "report.md").write_text("# evidence\n", encoding="utf-8")
    invocation = _write_executable_skill_invocation(task_run, judge=judge)
    monkeypatch.setattr(
        skill_adapter_module,
        "create_provider_adapter",
        lambda **_: _TimedOutProviderAdapter(Path(task_run.artifact_dir)),
    )

    result = skill_adapter_module.execute_skill_judge(
        task_run=task_run,
        invocation=invocation,
    )

    assert result["type"] == "skill_judge"
    assert result["status"] == "timed_out"
    assert result["governance_status"] == "failed"
    assert result["timeout_kind"] == "agent"
    assert result["provider_diagnostics"]["output_tail"].startswith("read 42 files")
    assert result["provider_session"]["resume_session"] == {
        "provider": "opencode",
        "session_id": "opaque-resume-session",
    }


def test_skill_judge_does_not_launch_after_total_attempt_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.skill_agent_adapter as skill_adapter_module

    task_run = _persist_skill_run(tmp_path)
    monkeypatch.setattr(
        skill_adapter_module,
        "execute_skill_judge",
        lambda **_: pytest.fail("expired Attempt must not launch the Judge"),
    )

    result = WorkbenchWorkflowRunner(tmp_path)._execute_v3_skill_judge_node(
        task_run=task_run,
        timeout_sec=0,
    )

    assert result == {
        "step_id": "skill.judge",
        "node_id": "skill.judge",
        "type": "skill_judge",
        "status": "timed_out",
        "error": "total_execution_timeout",
        "timed_out": True,
        "timeout_kind": "overall",
        "technical_diagnostics": {"error": "total_execution_timeout"},
        "artifact_dir": task_run.artifact_dir,
        "governance_status": "failed",
    }


def test_skill_judge_does_not_launch_after_attempt_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.skill_agent_adapter as skill_adapter_module

    task_run = _persist_skill_run(tmp_path)
    monkeypatch.setattr(
        skill_adapter_module,
        "execute_skill_judge",
        lambda **_: pytest.fail("cancelled Attempt must not launch the Judge"),
    )

    result = WorkbenchWorkflowRunner(
        tmp_path,
        is_cancelled=lambda: True,
    )._execute_v3_skill_judge_node(
        task_run=task_run,
        timeout_sec=10,
    )

    assert result["step_id"] == "skill.judge"
    assert result["node_id"] == "skill.judge"
    assert result["type"] == "skill_judge"
    assert result["status"] == "cancelled"
    assert result["governance_status"] == "failed"


def test_multiple_skill_steps_resume_one_producer_provider_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.skill_agent_adapter as skill_adapter_module
    import app.services.workbench_workflow_runner as runner_module

    task_run = _persist_skill_run(tmp_path)
    definition = task_run.task_bundle["compiled_definition"]
    definition["steps"] = [
        {
            "step_id": "skill.step-01",
            "instruction_path": _STEP_01_INSTRUCTION_PATH,
            "depends_on": [],
            "completion_gate": {"required_artifact_ids": ["artifact.first"]},
        },
        {
            "step_id": "skill.step-02",
            "instruction_path": _STEP_02_INSTRUCTION_PATH,
            "depends_on": ["skill.step-01"],
            "completion_gate": {"required_artifact_ids": ["artifact.second"]},
        },
        {
            "step_id": "skill.step-03",
            "instruction_path": _STEP_02_INSTRUCTION_PATH,
            "depends_on": ["skill.step-02"],
            "completion_gate": {"required_artifact_ids": ["artifact.third"]},
        },
    ]
    definition["artifacts"] = [
        {
            "artifact_id": "artifact.first",
            "path": "first.md",
            "producer_step_id": "skill.step-01",
            "required": True,
        },
        {
            "artifact_id": "artifact.second",
            "path": "second.md",
            "producer_step_id": "skill.step-02",
            "required": True,
        },
        {
            "artifact_id": "artifact.third",
            "path": "third.md",
            "producer_step_id": "skill.step-03",
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
        {
            "node_id": "skill.step-03",
            "type": "skill_step",
            "depends_on": ["skill.step-02"],
        },
    ]
    plan["topological_order"] = [
        "skill.step-01",
        "skill.step-02",
        "skill.step-03",
    ]
    _persist_rewritten_definition(task_run)
    artifact_root = Path(task_run.artifact_dir, "artifacts")
    artifact_root.mkdir()
    monotonic_clock = {"value": 100.0}
    monkeypatch.setattr(
        runner_module.time,
        "monotonic",
        lambda: monotonic_clock["value"],
    )
    adapter = _RecordingProviderAdapter(
        artifact_root,
        after_execute=lambda: monotonic_clock.__setitem__(
            "value", monotonic_clock["value"] + 11.0
        ),
    )
    monkeypatch.setattr(
        skill_adapter_module, "create_provider_adapter", lambda **_: adapter
    )
    runtime = {
        "runtime_id": "agent-runtime:default-opencode",
        "requested_provider": "opencode",
        "timeout_budget": {
            "agent_timeout_seconds": 30,
            "overall_timeout_seconds": 30,
        },
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
                "skill_ir": _frozen_reference(
                    task_run, "frozen_skill/skill-ir-v1.json"
                ),
                "input_snapshot": _frozen_reference(
                    task_run, "skill_input_snapshot.json"
                ),
                "runtime": {"producer": runtime, "judge": None},
            }
        ),
        encoding="utf-8",
    )
    _seal_skill_invocation(task_run)

    result = WorkbenchWorkflowRunner(tmp_path).execute_task_run(task_run.task_run_id)

    assert result.execution_status == "completed", result.step_results
    assert len(adapter.requests) == 3
    assert len(adapter.resume_tokens) == 2
    assert {token.value for token in adapter.resume_tokens} == {
        "opaque-provider-session-1"
    }
    assert adapter.execution_timeouts == [30, 19, 8]
    assert adapter.requests[0].task_bundle["predecessor_artifacts"] == []
    assert adapter.requests[1].task_bundle["predecessor_artifacts"] == [
        {
            "step_id": "skill.step-01",
            "artifact": "first.md",
            "path": str(artifact_root / "first.md"),
        }
    ]
    assert adapter.requests[2].task_bundle["predecessor_artifacts"] == [
        {
            "step_id": "skill.step-01",
            "artifact": "first.md",
            "path": str(artifact_root / "first.md"),
        },
        {
            "step_id": "skill.step-02",
            "artifact": "second.md",
            "path": str(artifact_root / "second.md"),
        },
    ]
    assert "Verified predecessor artifacts" in (
        adapter.requests[1].task_bundle["rendered_user_input"]
    )
    assert "first.md" in adapter.requests[1].task_bundle["rendered_user_input"]
    assert adapter.requests[1].timeout_seconds == 19
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
    input_path = task_dir / "input_snapshot.json"
    input_path.write_text(
        json.dumps(task_run.input_snapshot, sort_keys=True),
        encoding="utf-8",
    )
    (task_dir / "skill_input_snapshot.json").write_text(
        json.dumps(task_run.input_snapshot, sort_keys=True),
        encoding="utf-8",
    )
    snapshot["components"]["input_snapshot"]["sha256"] = hashlib.sha256(
        input_path.read_bytes()
    ).hexdigest()
    skill_ir_path = task_dir / "frozen_skill" / "skill-ir-v1.json"
    skill_ir = json.loads(skill_ir_path.read_text(encoding="utf-8"))
    skill_ir["steps"] = definition.get("steps") or []
    skill_ir["artifacts"] = definition.get("artifacts") or []
    skill_ir_path.write_text(json.dumps(skill_ir, sort_keys=True), encoding="utf-8")
    snapshot_path.write_text(json.dumps(snapshot, sort_keys=True), encoding="utf-8")
