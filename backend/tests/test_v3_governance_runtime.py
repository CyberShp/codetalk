from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.workbench_task_run import PreparedWorkbenchTaskRun
from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner
from app.services.workflow_handler_dispatcher import WorkflowHandlerResult


def _dispatch_professional_artifact(
    tmp_path: Path,
    *,
    handler_id: str,
    producer_port_key: str,
    artifact_path: str,
    payload: list[dict],
):
    from app.services.workflow_handler_dispatcher import (
        WorkflowHandlerDispatcher,
        WorkflowHandlerRequest,
    )

    source_root = tmp_path / "repo"
    source_root.mkdir(exist_ok=True)
    artifact_root = tmp_path / f"external-{handler_id}"
    destination = artifact_root / artifact_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload), encoding="utf-8")
    return WorkflowHandlerDispatcher().dispatch(
        WorkflowHandlerRequest(
            handler_id=handler_id,
            handler_version=1,
            node_id=f"validate-{handler_id}",
            node_kind="validator",
            task_artifact_dir=tmp_path / "task",
            source_root=source_root,
            declared_outputs=(
                {
                    "output_id": "external-result",
                    "artifact": artifact_path,
                    "media_type": "application/json",
                    "producer_step_id": "external-agent",
                    "producer_port_id": "physical-port-7f3a",
                    "producer_port_key": producer_port_key,
                },
            ),
            required_output_ids=("external-result",),
            artifact_roots_by_output_id={"external-result": artifact_root},
        )
    )


def _canonical_professional_issue_codes(
    tmp_path: Path,
    *,
    artifact_name: str,
    payload: list[dict],
) -> list[str]:
    from app.services.test_activity_contract import (
        ARTIFACT_TEMPLATES,
        _audit_json_artifact,
    )

    return [
        str(issue["code"])
        for issue in _audit_json_artifact(
            artifact=artifact_name,
            payload=payload,
            spec=ARTIFACT_TEMPLATES[artifact_name],
            repo=tmp_path / "repo",
        )
    ]


def _task_run(
    tmp_path: Path,
    *,
    declared_outputs: list[dict],
    plan_nodes: list[dict],
    workflow_steps: list[dict] | None = None,
    agent_runs: list[dict] | None = None,
    input_snapshot: dict | None = None,
    profile: str = "artifact_only",
) -> PreparedWorkbenchTaskRun:
    run_id = "v3-governance-run"
    task_dir = tmp_path / run_id
    task_dir.mkdir(parents=True, exist_ok=True)
    definition = {
        "compiled_contract_version": 3,
        "validation_profile": profile,
        "declared_outputs": declared_outputs,
        "outputs": declared_outputs,
    }
    plan = {
        "compiled_contract_version": 3,
        "nodes": plan_nodes,
        "topological_order": [node["node_id"] for node in plan_nodes],
    }
    task_run = PreparedWorkbenchTaskRun(
        task_run_id=run_id,
        workflow_id="wf-v3-governance",
        workspace_id="workspace",
        repo_path=str(tmp_path / "repo"),
        artifact_dir=str(task_dir),
        workflow_snapshot={"steps": workflow_steps or []},
        input_snapshot=input_snapshot or {},
        task_bundle={"compiled_definition": definition, "compiled_plan": plan},
        agent_runs=agent_runs or [],
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
                "agent_runs": task_run.agent_runs,
            }
        ),
        encoding="utf-8",
    )
    (task_dir / "compiled_definition.json").write_text(
        json.dumps(definition, sort_keys=True), encoding="utf-8"
    )
    (task_dir / "compiled_plan.json").write_text(
        json.dumps(plan, sort_keys=True), encoding="utf-8"
    )
    components = {}
    for component_id, name in (
        ("v3_runtime_contract", "compiled_definition.json"),
        ("execution_plan", "compiled_plan.json"),
    ):
        path = task_dir / name
        components[component_id] = {
            "path": name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    (task_dir / "run_snapshot_v3.json").write_text(
        json.dumps({
            "schema_version": 3,
            "snapshot_kind": "codetalk_run_snapshot",
            "components": components,
        }, sort_keys=True),
        encoding="utf-8",
    )
    return task_run


def _agent_task(
    tmp_path: Path,
    *,
    output: dict,
    validators: list[dict],
    profile: str = "artifact_only",
) -> tuple[PreparedWorkbenchTaskRun, Path]:
    agent_dir = tmp_path / "v3-governance-run" / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    nodes = [
        {
            "node_id": "agent",
            "kind": "agent",
            "type": "agent_task",
            "handler_id": "agent",
            "handler_version": 1,
            "required_outputs": [output["output_id"]],
        },
        *validators,
    ]
    task_run = _task_run(
        tmp_path,
        declared_outputs=[output],
        plan_nodes=nodes,
        workflow_steps=[{"id": "agent", "type": "agent_task"}],
        agent_runs=[{"step_id": "agent", "artifact_dir": str(agent_dir)}],
        profile=profile,
    )
    return task_run, agent_dir


def _complete_agent(
    runner: WorkbenchWorkflowRunner,
    artifact_dir: Path,
) -> None:
    runner._execute_agent_step = lambda **_: {
        "step_id": "agent",
        "type": "agent_task",
        "status": "completed",
        "artifact_dir": str(artifact_dir),
    }


class _PolicyDispatcher:
    def __init__(self, *, failed_node_id: str, failed_kind: str) -> None:
        self.failed_node_id = failed_node_id
        self.failed_kind = failed_kind
        self.calls: list[str] = []

    def dispatch(self, request):
        self.calls.append(request.node_id)
        if request.node_id == self.failed_node_id:
            artifact_dir = ""
            if self.failed_kind == "governance":
                artifact_root = (
                    request.task_artifact_dir
                    / "governance_runs"
                    / request.node_id
                )
                artifact_root.mkdir(parents=True, exist_ok=True)
                (artifact_root / "contract.json").write_text(
                    '{"partial": true}',
                    encoding="utf-8",
                )
                artifact_dir = str(artifact_root)
            return WorkflowHandlerResult(
                handler_id=request.handler_id,
                handler_version=request.handler_version,
                node_id=request.node_id,
                node_kind=request.node_kind,
                axis=(
                    "governance"
                    if self.failed_kind == "governance"
                    else "artifact_validation"
                ),
                status="failed",
                governance_status=(
                    "failed" if self.failed_kind == "governance" else ""
                ),
                error_code="policy_test_failure",
                artifact_dir=artifact_dir,
            )
        return WorkflowHandlerResult(
            handler_id=request.handler_id,
            handler_version=request.handler_version,
            node_id=request.node_id,
            node_kind=request.node_kind,
            axis="artifact_validation",
            status="passed",
            validated_output_ids=request.required_output_ids,
        )


@pytest.mark.parametrize(
    ("failure_policy", "stop_on_error"),
    [("stop", False), (None, True)],
)
def test_v3_governance_failure_uses_scheduler_stop_policy_without_artifact_leakage(
    tmp_path: Path,
    failure_policy: str | None,
    stop_on_error: bool,
) -> None:
    (tmp_path / "repo").mkdir()
    output = {
        "output_id": "contract",
        "artifact": "contract.json",
        "required": True,
        "producer_step_id": "gate",
        "producer_port_id": "contract",
        "schema": None,
    }
    gate = {
        "node_id": "gate",
        "kind": "governance",
        "handler_id": "storage_test_design",
        "handler_version": 1,
        "required_outputs": ["contract"],
    }
    if failure_policy is not None:
        gate["failure_policy"] = failure_policy
    plan_nodes = [
        gate,
        {
            "node_id": "independent",
            "kind": "agent",
            "type": "agent_task",
            "handler_id": "agent",
            "handler_version": 1,
            "depends_on": [],
            "failure_policy": "stop",
        },
        {
            "node_id": "dependent",
            "kind": "agent",
            "type": "agent_task",
            "handler_id": "agent",
            "handler_version": 1,
            "depends_on": ["gate"],
            "failure_policy": "stop",
        },
        {
            "node_id": "exists",
            "kind": "validator",
            "handler_id": "artifact_exists",
            "handler_version": 1,
            "depends_on": ["gate"],
            "failure_policy": "stop",
            "required_outputs": ["contract"],
        },
    ]
    task_run = _task_run(
        tmp_path,
        declared_outputs=[output],
        plan_nodes=plan_nodes,
        workflow_steps=[
            {"id": "independent", "type": "agent_task"},
            {"id": "dependent", "type": "agent_task"},
        ],
        agent_runs=[
            {"step_id": "independent", "artifact_dir": str(tmp_path / "independent")},
            {"step_id": "dependent", "artifact_dir": str(tmp_path / "dependent")},
        ],
        profile="storage_test_design",
    )
    dispatcher = _PolicyDispatcher(failed_node_id="gate", failed_kind="governance")
    events: list[tuple[str, dict]] = []
    executed_agents: list[str] = []
    runner = WorkbenchWorkflowRunner(
        tmp_path,
        handler_dispatcher=dispatcher,
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )
    runner._execute_agent_step = lambda **kwargs: (
        executed_agents.append(kwargs["step"]["id"])
        or {
            "step_id": kwargs["step"]["id"],
            "type": "agent_task",
            "status": "completed",
            "artifact_dir": kwargs["agent_run"]["artifact_dir"],
        }
    )

    result = runner.execute_task_run(
        task_run.task_run_id,
        stop_on_error=stop_on_error,
    )

    assert dispatcher.calls == ["gate"]
    assert executed_agents == []
    assert [item["status"] for item in result.step_results] == [
        "failed",
        "blocked",
        "blocked",
        "blocked",
    ]
    assert result.execution_status == "failed"
    assert result.artifact_validation_status == "not_started"
    assert result.governance_status == "failed"
    assert result.delivery_status == "blocked"
    assert result.outputs == [
        {
            "id": "contract",
            "output_id": "contract",
            "artifact": "contract.json",
            "from": "gate",
            "required": True,
            "status": "unvalidated",
            "reason": "declared producer did not complete successfully",
        }
    ]
    event_types = [event_type for event_type, _payload in events]
    assert event_types.index("node_failed") < event_types.index("node_blocked")
    assert event_types[-2:] == ["run_completed", "v3_status_updated"]


def test_v3_validator_continue_policy_runs_independent_branch_and_blocks_dependent(
    tmp_path: Path,
) -> None:
    (tmp_path / "repo").mkdir()
    producer_dir = tmp_path / "producer"
    independent_dir = tmp_path / "independent"
    dependent_dir = tmp_path / "dependent"
    for directory in (producer_dir, independent_dir, dependent_dir):
        directory.mkdir()
    (producer_dir / "report.md").write_text("# report\n", encoding="utf-8")
    output = {
        "output_id": "report",
        "artifact": "report.md",
        "required": True,
        "producer_step_id": "producer",
        "producer_port_id": "report",
        "schema": None,
    }
    task_run = _task_run(
        tmp_path,
        declared_outputs=[output],
        plan_nodes=[
            {
                "node_id": "producer",
                "kind": "agent",
                "type": "agent_task",
                "handler_id": "agent",
                "handler_version": 1,
                "failure_policy": "stop",
                "required_outputs": ["report"],
            },
            {
                "node_id": "check",
                "kind": "validator",
                "handler_id": "artifact_exists",
                "handler_version": 1,
                "depends_on": ["producer"],
                "failure_policy": "continue_independent",
                "required_outputs": ["report"],
            },
            {
                "node_id": "independent",
                "kind": "agent",
                "type": "agent_task",
                "handler_id": "agent",
                "handler_version": 1,
                "depends_on": [],
                "failure_policy": "stop",
            },
            {
                "node_id": "dependent",
                "kind": "agent",
                "type": "agent_task",
                "handler_id": "agent",
                "handler_version": 1,
                "depends_on": ["check"],
                "failure_policy": "stop",
            },
        ],
        workflow_steps=[
            {"id": "producer", "type": "agent_task"},
            {"id": "independent", "type": "agent_task"},
            {"id": "dependent", "type": "agent_task"},
        ],
        agent_runs=[
            {"step_id": "producer", "artifact_dir": str(producer_dir)},
            {"step_id": "independent", "artifact_dir": str(independent_dir)},
            {"step_id": "dependent", "artifact_dir": str(dependent_dir)},
        ],
    )
    dispatcher = _PolicyDispatcher(failed_node_id="check", failed_kind="validator")
    events: list[tuple[str, dict]] = []
    executed_agents: list[str] = []
    runner = WorkbenchWorkflowRunner(
        tmp_path,
        handler_dispatcher=dispatcher,
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )

    def execute_agent(**kwargs):
        step_id = kwargs["step"]["id"]
        executed_agents.append(step_id)
        return {
            "step_id": step_id,
            "type": "agent_task",
            "status": "completed",
            "artifact_dir": kwargs["agent_run"]["artifact_dir"],
        }

    runner._execute_agent_step = execute_agent

    result = runner.execute_task_run(task_run.task_run_id)

    assert dispatcher.calls == ["check"]
    assert executed_agents == ["producer", "independent"]
    assert [item["status"] for item in result.step_results] == [
        "completed",
        "failed",
        "completed",
        "blocked",
    ]
    assert result.execution_status == "failed"
    assert result.artifact_validation_status == "failed"
    assert result.governance_status == "not_requested"
    assert result.delivery_status == "blocked"
    event_types = [event_type for event_type, _payload in events]
    assert event_types.index("node_failed") < event_types.index("node_blocked")
    assert event_types[-2:] == ["run_completed", "v3_status_updated"]


def test_completed_execution_with_failed_governance_blocks_downstream_validation_and_delivery(
    tmp_path: Path,
) -> None:
    (tmp_path / "repo").mkdir()
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "source-evidence.json").write_text("[]\n", encoding="utf-8")
    outputs = [
        {
            "output_id": "source_evidence",
            "artifact": "source-evidence.json",
            "required": True,
            "producer_step_id": "agent",
            "producer_port_id": "source_evidence",
            "schema": {"type": "array"},
        },
        {
            "output_id": "sfmea",
            "artifact": "sfmea.json",
            "required": True,
            "producer_step_id": "governance",
            "producer_port_id": "sfmea",
            "schema": {"type": "array"},
        },
    ]
    task_run = _task_run(
        tmp_path,
        declared_outputs=outputs,
        plan_nodes=[
            {
                "node_id": "agent",
                "kind": "agent",
                "type": "agent_task",
                "handler_id": "agent",
                "handler_version": 1,
                "failure_policy": "stop",
                "required_outputs": ["source_evidence"],
            },
            {
                "node_id": "governance",
                "kind": "governance",
                "handler_id": "storage_test_design",
                "handler_version": 1,
                "depends_on": ["agent"],
                "failure_policy": "stop",
                "required_outputs": ["sfmea"],
            },
            {
                "node_id": "exists",
                "kind": "validator",
                "handler_id": "artifact_exists",
                "handler_version": 1,
                "depends_on": ["agent", "governance"],
                "failure_policy": "stop",
                "required_outputs": ["source_evidence", "sfmea"],
            },
        ],
        workflow_steps=[{"id": "agent", "type": "agent_task"}],
        agent_runs=[{"step_id": "agent", "artifact_dir": str(agent_dir)}],
        profile="storage_test_design",
    )
    dispatcher = _PolicyDispatcher(
        failed_node_id="governance",
        failed_kind="governance",
    )
    runner = WorkbenchWorkflowRunner(tmp_path, handler_dispatcher=dispatcher)
    runner._execute_agent_step = lambda **kwargs: {
        "step_id": "agent",
        "type": "agent_task",
        "status": "completed",
        "artifact_dir": str(agent_dir),
    }

    result = runner.execute_task_run(task_run.task_run_id)

    assert [item["status"] for item in result.step_results] == [
        "completed",
        "failed",
        "blocked",
    ]
    assert result.execution_status == "completed"
    assert result.governance_status == "failed"
    assert result.artifact_validation_status == "failed"
    assert result.delivery_status == "blocked"


def test_configured_validation_is_not_started_after_provider_failure(
    tmp_path: Path,
) -> None:
    output = {
        "output_id": "report",
        "artifact": "report.md",
        "required": True,
        "producer_step_id": "agent",
        "schema": None,
    }
    task_run, _agent_dir = _agent_task(
        tmp_path,
        output=output,
        validators=[
            {
                "node_id": "exists",
                "kind": "validator",
                "handler_id": "artifact_exists",
                "handler_version": 1,
                "required_outputs": ["report"],
            },
        ],
    )
    runner = WorkbenchWorkflowRunner(tmp_path)
    runner._execute_agent_step = lambda **_: {
        "step_id": "agent",
        "type": "agent_task",
        "status": "error",
        "error": "provider_failed",
    }

    result = runner.execute_task_run(task_run.task_run_id)

    assert result.execution_status == "failed"
    assert result.artifact_validation_status == "not_started"
    assert result.governance_status == "not_requested"
    assert result.delivery_status == "blocked"


def test_artifact_only_v3_does_not_load_professional_legacy_modules() -> None:
    code = r'''
import json
import hashlib
import sys
import tempfile
from pathlib import Path

from app.services.workbench_workflow_runner import WorkbenchWorkflowRunner

forbidden = (
    "app.services.artifact_contract_v3",
    "app.services.test_activity_contract",
    "app.services.test_activity_stage_specs",
    "app.services.source_driven_test_design",
)
root = Path(tempfile.mkdtemp(prefix="codetalk-v3-cold-load-"))
run_id = "artifact-only-v3"
task_dir = root / run_id
agent_dir = task_dir / "agent"
agent_dir.mkdir(parents=True)
(agent_dir / "report.md").write_text("# Report\n", encoding="utf-8")
declared_output = {
    "output_id": "report",
    "artifact": "report.md",
    "required": True,
    "producer_step_id": "agent",
    "producer_port_id": "report",
    "schema": None,
}
payload = {
    "task_run_id": run_id,
    "workflow_id": "wf-v3-cold-load",
    "workspace_id": "workspace",
    "repo_path": str(root),
    "artifact_dir": str(task_dir),
    "workflow_snapshot": {"steps": [{"id": "agent", "type": "agent_task"}]},
    "input_snapshot": {},
    "task_bundle": {
        "compiled_definition": {
            "compiled_contract_version": 3,
            "validation_profile": "artifact_only",
            "declared_outputs": [declared_output],
            "outputs": [declared_output],
        },
        "compiled_plan": {
            "compiled_contract_version": 3,
            "nodes": [
                {
                    "node_id": "agent",
                    "kind": "agent",
                    "type": "agent_task",
                    "handler_id": "agent",
                    "handler_version": 1,
                    "required_outputs": ["report"],
                },
                {
                    "node_id": "exists",
                    "kind": "validator",
                    "handler_id": "artifact_exists",
                    "handler_version": 1,
                    "depends_on": ["agent"],
                    "required_outputs": ["report"],
                },
            ],
            "topological_order": ["agent", "exists"],
        },
    },
    "agent_runs": [{"step_id": "agent", "artifact_dir": str(agent_dir)}],
}
(task_dir / "task_run.json").write_text(json.dumps(payload), encoding="utf-8")
definition = payload["task_bundle"]["compiled_definition"]
plan = payload["task_bundle"]["compiled_plan"]
(task_dir / "compiled_definition.json").write_text(json.dumps(definition, sort_keys=True), encoding="utf-8")
(task_dir / "compiled_plan.json").write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")
components = {}
for component_id, name in (
    ("v3_runtime_contract", "compiled_definition.json"),
    ("execution_plan", "compiled_plan.json"),
):
    path = task_dir / name
    components[component_id] = {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
(task_dir / "run_snapshot_v3.json").write_text(json.dumps({
    "schema_version": 3,
    "snapshot_kind": "codetalk_run_snapshot",
    "components": components,
}, sort_keys=True), encoding="utf-8")
runner = WorkbenchWorkflowRunner(root)
runner._execute_agent_step = lambda **_: {
    "step_id": "agent",
    "type": "agent_task",
    "status": "completed",
    "artifact_dir": str(agent_dir),
}
result = runner.execute_task_run(run_id)
assert result.execution_status == "completed"
assert result.artifact_validation_status == "passed"
loaded = sorted(name for name in forbidden if name in sys.modules)
assert loaded == [], loaded
'''

    subprocess.run([sys.executable, "-c", code], check=True)


def test_v3_runner_executes_explicit_source_evidence_validator(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "sample.c"
    source.write_text("int answer(void) {\n    return 42;\n}\n", encoding="utf-8")
    output = {
        "output_id": "evidence",
        "artifact": "evidence.json",
        "required": True,
        "producer_step_id": "agent",
        "schema": None,
    }
    task_run, agent_dir = _agent_task(
        tmp_path,
        output=output,
        validators=[
            {
                "node_id": "exists",
                "kind": "validator",
                "handler_id": "artifact_exists",
                "handler_version": 1,
                "required_outputs": ["evidence"],
            },
            {
                "node_id": "source-check",
                "kind": "validator",
                "handler_id": "source_evidence",
                "handler_version": 1,
                "required_outputs": ["evidence"],
            },
        ],
    )
    (agent_dir / "evidence.json").write_text(
        json.dumps(
            [
                {
                    "file_path": "sample.c",
                    "start_line": 1,
                    "end_line": 2,
                    "excerpt": "int answer(void) {\n    return 42;",
                    "symbols": ["answer"],
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }
            ]
        ),
        encoding="utf-8",
    )
    events: list[tuple[str, dict]] = []
    runner = WorkbenchWorkflowRunner(
        tmp_path,
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )
    _complete_agent(runner, agent_dir)

    result = runner.execute_task_run(task_run.task_run_id)

    assert result.execution_status == "completed"
    assert result.artifact_validation_status == "passed"
    assert result.governance_status == "not_requested"
    source_result = next(
        item for item in result.step_results if item["step_id"] == "source-check"
    )
    assert source_result["handler_id"] == "source_evidence"
    assert source_result["handler_version"] == 1
    assert source_result["validated_output_ids"] == ["evidence"]
    assert any(
        event_type == "step_started"
        and payload.get("handler_id") == "source_evidence"
        for event_type, payload in events
    )


def test_artifact_only_does_not_load_professional_plugin_modules(tmp_path: Path) -> None:
    professional_modules = {
        "app.services.governance_plugins.storage_test_design",
        "app.services.governance_plugins.sfmea",
        "app.services.governance_plugins.black_box",
        "app.services.governance_plugins.independent_review",
    }
    for module_name in professional_modules:
        sys.modules.pop(module_name, None)
    output = {
        "output_id": "report",
        "artifact": "report.md",
        "required": True,
        "producer_step_id": "agent",
        "schema": None,
    }
    task_run, agent_dir = _agent_task(
        tmp_path,
        output=output,
        validators=[
            {
                "node_id": "exists",
                "kind": "validator",
                "handler_id": "artifact_exists",
                "handler_version": 1,
                "required_outputs": ["report"],
            }
        ],
    )
    (agent_dir / "report.md").write_text("# report\n", encoding="utf-8")
    runner = WorkbenchWorkflowRunner(tmp_path)
    _complete_agent(runner, agent_dir)

    result = runner.execute_task_run(task_run.task_run_id)

    assert result.delivery_status == "ready"
    assert professional_modules.isdisjoint(sys.modules)


def test_professional_validator_boundary_import_keeps_legacy_domain_cold() -> None:
    code = r'''
import sys

import app.services.governance_plugins._legacy_validation
import app.services.workflow_handler_dispatcher

forbidden = (
    "app.services.ai_staged_execution",
    "app.services.artifact_contract_v3",
    "app.services.test_activity_contract",
    "app.services.test_activity_stage_specs",
    "app.services.source_driven_test_design",
)
loaded = sorted(name for name in forbidden if name in sys.modules)
assert loaded == [], loaded
'''

    subprocess.run([sys.executable, "-c", code], check=True)


def test_dispatcher_sfmea_validator_matches_frozen_template_for_custom_filename(
    tmp_path: Path,
) -> None:
    source = tmp_path / "repo" / "lib" / "storage.c"
    source.parent.mkdir(parents=True)
    source.write_text("int submit(void) { return 0; }\n", encoding="utf-8")
    (tmp_path / "repo" / "test" / "storage").mkdir(parents=True)
    payload = [
        {
            "sfmea_id": "SFMEA-EXT-001",
            "failure_mode": "public request retains stale state",
            # Intentionally missing the canonical required field: cause.
            "effect": "the next public request returns an error status",
            "detection": "observe status, logs, counters, and cleanup state",
            "severity": 8,
            "occurrence": 3,
            "detection_score": 4,
            "rpn": 96,
            "score_explanation": "externally visible state with runtime validation pending",
            "mitigation": "repair cleanup and run a public retry test while monitoring counters",
            "source_evidence": ["lib/storage.c"],
            "test_mapping": "test/storage",
        }
    ]
    expected_codes = _canonical_professional_issue_codes(
        tmp_path,
        artifact_name="sfmea.json",
        payload=payload,
    )

    result = _dispatch_professional_artifact(
        tmp_path,
        handler_id="sfmea",
        producer_port_key="sfmea",
        artifact_path="deliveries/risk-register-v7.json",
        payload=payload,
    )

    assert "missing_sfmea_fields" in expected_codes
    assert [issue["code"] for issue in result.issues] == expected_codes
    assert result.status == "failed"


def test_dispatcher_black_box_validator_matches_frozen_dimensions_for_custom_filename(
    tmp_path: Path,
) -> None:
    source = tmp_path / "repo" / "lib" / "storage.c"
    source.parent.mkdir(parents=True)
    source.write_text("int submit(void) { return 0; }\n", encoding="utf-8")
    (tmp_path / "repo" / "test" / "storage").mkdir(parents=True)
    payload = [
        {
            "case_id": "BB-EXT-001",
            "test_dimension": "normal_path",
            "scenario_name": "supported request succeeds",
            "preconditions": ["target is ready and initial state is recorded"],
            "steps": [
                "start the target through its supported public command",
                "submit one valid request through the public client",
                "collect response, exit status, logs, metrics, and final state",
            ],
            "expected_result": "exit status is 0 and the public completion counter increases by one",
            "observability": ["client exit status", "public completion counter"],
            "failure_diagnostics": ["retain client output and target logs with timestamps"],
            "mapped_test_dir": "test/storage",
            "source_or_test_evidence": ["lib/storage.c"],
        }
    ]
    expected_codes = _canonical_professional_issue_codes(
        tmp_path,
        artifact_name="black_box_cases.json",
        payload=payload,
    )

    result = _dispatch_professional_artifact(
        tmp_path,
        handler_id="black_box",
        producer_port_key="black_box_cases",
        artifact_path="deliveries/external-test-matrix.json",
        payload=payload,
    )

    assert "missing_black_box_dimensions" in expected_codes
    assert [issue["code"] for issue in result.issues] == expected_codes
    assert result.status == "failed"


def test_dispatcher_professional_validators_accept_compliant_custom_filenames(
    tmp_path: Path,
) -> None:
    import hashlib

    from app.services.governance_plugins.storage_professional_generation import (
        generate_storage_professional_payloads,
    )

    source = tmp_path / "repo" / "lib" / "storage.c"
    source.parent.mkdir(parents=True)
    source.write_text(
        "int storage_submit(int value) {\n"
        "    if (value < 0) return -1;\n"
        "    return value;\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "repo" / "test" / "storage").mkdir(parents=True)
    payloads = generate_storage_professional_payloads(
        inputs={
            "target": "storage public request lifecycle",
            "repo_path": str(tmp_path / "repo"),
            "source_evidence": [
                {
                    "file_path": "lib/storage.c",
                    "start_line": 1,
                    "end_line": 4,
                    "excerpt": source.read_text(encoding="utf-8").rstrip(),
                    "symbols": ["storage_submit"],
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }
            ],
        },
        roles=("sfmea", "black_box_cases"),
        node_id="design",
        artifact_id="professional-delivery",
    )

    sfmea_result = _dispatch_professional_artifact(
        tmp_path,
        handler_id="sfmea",
        producer_port_key="sfmea",
        artifact_path="deliveries/risk-register.release.json",
        payload=payloads["sfmea"],
    )
    black_box_result = _dispatch_professional_artifact(
        tmp_path,
        handler_id="black_box",
        producer_port_key="black_box_cases",
        artifact_path="deliveries/test-matrix.release.json",
        payload=payloads["black_box_cases"],
    )

    assert sfmea_result.status == "passed"
    assert sfmea_result.validated_output_ids == ("external-result",)
    assert black_box_result.status == "passed"
    assert black_box_result.validated_output_ids == ("external-result",)


@pytest.mark.parametrize(
    ("blocking", "governance_status", "delivery_status"),
    [(True, "failed", "blocked"), (False, "warning", "ready")],
)
def test_governance_failure_changes_governance_axis_not_provider_execution(
    tmp_path: Path,
    blocking: bool,
    governance_status: str,
    delivery_status: str,
) -> None:
    output = {
        "output_id": "sfmea",
        "artifact": "sfmea.json",
        "required": True,
        "producer_step_id": "agent",
        "schema": None,
    }
    task_run, agent_dir = _agent_task(
        tmp_path,
        output=output,
        validators=[
            {
                "node_id": "exists",
                "kind": "validator",
                "handler_id": "artifact_exists",
                "handler_version": 1,
                "required_outputs": ["sfmea"],
            },
            {
                "node_id": "sfmea-check",
                "kind": "validator",
                "handler_id": "sfmea",
                "handler_version": 1,
                "required_outputs": ["sfmea"],
                "blocking": blocking,
            },
        ],
        profile="storage_test_design",
    )
    (agent_dir / "sfmea.json").write_text("{}", encoding="utf-8")
    events: list[tuple[str, dict]] = []
    runner = WorkbenchWorkflowRunner(
        tmp_path,
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )
    _complete_agent(runner, agent_dir)

    result = runner.execute_task_run(task_run.task_run_id)

    assert result.execution_status == "completed"
    assert result.artifact_validation_status == "passed"
    assert result.governance_status == governance_status
    assert result.delivery_status == delivery_status
    governance_result = next(
        item for item in result.step_results if item["step_id"] == "sfmea-check"
    )
    assert governance_result["provider_failed"] is False
    assert governance_result["handler_id"] == "sfmea"
    assert governance_result["handler_version"] == 1
    assert any(
        event_type == "step_failed"
        and payload.get("handler_id") == "sfmea"
        and payload.get("handler_version") == 1
        for event_type, payload in events
    )


def test_governance_generator_materializes_only_exact_declared_output(
    tmp_path: Path,
) -> None:
    (tmp_path / "repo").mkdir()
    output = {
        "output_id": "contract",
        "artifact": "nested/contract.json",
        "required": True,
        "producer_step_id": "design",
        "producer_port_id": "contract",
        "schema": None,
    }
    task_run = _task_run(
        tmp_path,
        declared_outputs=[output],
        plan_nodes=[
            {
                "node_id": "design",
                "kind": "governance",
                "handler_id": "storage_test_design",
                "handler_version": 1,
                "required_outputs": ["contract"],
                "resolved_input_bindings": {
                    "target": {
                        "source_node_id": "target-input",
                        "source_input_id": "target",
                        "source_port_id": "value",
                    }
                },
            },
            {
                "node_id": "exists",
                "kind": "validator",
                "handler_id": "artifact_exists",
                "handler_version": 1,
                "required_outputs": ["contract"],
            },
        ],
        input_snapshot={"target": "explicit storage test design"},
        profile="storage_test_design",
    )

    result = WorkbenchWorkflowRunner(tmp_path).execute_task_run(task_run.task_run_id)

    assert result.execution_status == "completed"
    assert result.governance_status == "passed"
    assert result.artifact_validation_status == "passed"
    generated = next(item for item in result.step_results if item["step_id"] == "design")
    output_root = Path(generated["artifact_dir"])
    assert [path.relative_to(output_root).as_posix() for path in output_root.rglob("*") if path.is_file()] == [
        "nested/contract.json"
    ]
    assert json.loads((output_root / "nested/contract.json").read_text(encoding="utf-8"))


def test_governance_generator_resolves_explicit_upstream_agent_port(
    tmp_path: Path,
) -> None:
    (tmp_path / "repo").mkdir()
    agent_dir = tmp_path / "v3-governance-run" / "agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "report.md").write_text("# report\n", encoding="utf-8")
    output = {
        "output_id": "contract",
        "artifact": "contract.json",
        "required": True,
        "producer_step_id": "design",
        "producer_port_id": "contract",
        "schema": None,
    }
    task_run = _task_run(
        tmp_path,
        declared_outputs=[output],
        plan_nodes=[
            {
                "node_id": "agent",
                "kind": "agent",
                "type": "agent_task",
                "handler_id": "agent",
                "handler_version": 1,
                "output_ports": [{"id": "report", "type": "artifact"}],
                "required_outputs": [],
            },
            {
                "node_id": "design",
                "kind": "governance",
                "handler_id": "storage_test_design",
                "handler_version": 1,
                "depends_on": ["agent"],
                "required_outputs": ["contract"],
                "resolved_input_bindings": {
                    "source": {
                        "source_node_id": "agent",
                        "source_port_id": "report",
                    }
                },
            },
        ],
        workflow_steps=[{"id": "agent", "type": "agent_task"}],
        agent_runs=[{"step_id": "agent", "artifact_dir": str(agent_dir)}],
        profile="storage_test_design",
    )
    runner = WorkbenchWorkflowRunner(tmp_path)
    _complete_agent(runner, agent_dir)

    result = runner.execute_task_run(task_run.task_run_id)

    assert result.execution_status == "completed"
    assert result.governance_status == "passed"
    governance_result = next(item for item in result.step_results if item["step_id"] == "design")
    assert governance_result["status"] == "completed"


def test_professional_governance_consumes_declared_agent_evidence_and_delivers(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "storage.c"
    source.write_text(
        "int storage_submit(int value) {\n"
        "    if (value < 0) {\n"
        "        return -1;\n"
        "    }\n"
        "    return value;\n"
        "}\n",
        encoding="utf-8",
    )
    agent_dir = tmp_path / "v3-governance-run" / "agent"
    agent_dir.mkdir(parents=True)
    evidence = [
        {
            "file_path": "storage.c",
            "start_line": 1,
            "end_line": 6,
            "excerpt": source.read_text(encoding="utf-8").rstrip(),
            "symbols": ["storage_submit"],
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
    ]
    (agent_dir / "evidence_cards.json").write_text(
        json.dumps(evidence),
        encoding="utf-8",
    )
    declared_outputs = [
        {
            "output_id": "evidence",
            "artifact": "evidence_cards.json",
            "media_type": "application/json",
            "required": True,
            "producer_step_id": "agent",
            "producer_port_id": "evidence",
            "schema": {"type": "array"},
        },
        {
            "output_id": "sfmea",
            "artifact": "sfmea.json",
            "media_type": "application/json",
            "required": True,
            "producer_step_id": "design",
            "producer_port_id": "sfmea",
            "schema": {"type": "array"},
        },
        {
            "output_id": "black_box_cases",
            "artifact": "black_box_cases.json",
            "media_type": "application/json",
            "required": True,
            "producer_step_id": "design",
            "producer_port_id": "black_box_cases",
            "schema": {"type": "array"},
        },
    ]
    plan_nodes = [
        {
            "node_id": "agent",
            "kind": "agent",
            "type": "agent_task",
            "handler_id": "agent",
            "handler_version": 1,
            "output_ports": [{"id": "evidence", "type": "artifact"}],
            "required_outputs": ["evidence"],
        },
        {
            "node_id": "design",
            "kind": "governance",
            "handler_id": "storage_test_design",
            "handler_version": 1,
            "depends_on": ["agent"],
            "resolved_input_bindings": {
                "source_evidence": {
                    "source_node_id": "agent",
                    "source_port_id": "evidence",
                },
                "target": {
                    "source_node_id": "target-input",
                    "source_input_id": "target",
                    "source_port_id": "value",
                },
            },
            "output_ports": [
                {"id": "sfmea", "type": "artifact"},
                {"id": "black_box_cases", "type": "artifact"},
            ],
            "required_outputs": ["sfmea", "black_box_cases"],
        },
        {
            "node_id": "exists",
            "kind": "validator",
            "handler_id": "artifact_exists",
            "handler_version": 1,
            "depends_on": ["design"],
            "required_outputs": ["evidence", "sfmea", "black_box_cases"],
        },
        {
            "node_id": "sfmea-check",
            "kind": "validator",
            "handler_id": "sfmea",
            "handler_version": 1,
            "depends_on": ["design"],
            "required_outputs": ["sfmea"],
        },
        {
            "node_id": "black-box-check",
            "kind": "validator",
            "handler_id": "black_box",
            "handler_version": 1,
            "depends_on": ["design"],
            "required_outputs": ["black_box_cases"],
        },
    ]
    task_run = _task_run(
        tmp_path,
        declared_outputs=declared_outputs,
        plan_nodes=plan_nodes,
        workflow_steps=[{"id": "agent", "type": "agent_task"}],
        agent_runs=[{"step_id": "agent", "artifact_dir": str(agent_dir)}],
        input_snapshot={"target": "storage submit error and recovery paths"},
        profile="storage_test_design",
    )
    runner = WorkbenchWorkflowRunner(tmp_path)
    _complete_agent(runner, agent_dir)

    result = runner.execute_task_run(task_run.task_run_id)

    assert result.execution_status == "completed"
    assert result.artifact_validation_status == "passed"
    assert result.governance_status == "passed"
    assert result.delivery_status == "ready"
    design = next(item for item in result.step_results if item["step_id"] == "design")
    generated_root = Path(design["artifact_dir"])
    sfmea = json.loads((generated_root / "sfmea.json").read_text(encoding="utf-8"))
    cases = json.loads(
        (generated_root / "black_box_cases.json").read_text(encoding="utf-8")
    )
    assert sfmea and cases
    assert sfmea != cases
    assert sfmea[0]["evidence"]["sha256"] == evidence[0]["sha256"]
    assert {item["test_dimension"] for item in cases} >= {
        "normal_path",
        "invalid_input",
        "resource_pressure",
        "timeout",
        "reconnect",
        "concurrency",
        "recovery",
        "performance",
        "long_steady_state",
        "resource_wraparound",
        "resource_cleanup",
        "upstream_error_propagation",
    }


def test_professional_governance_rejects_invalid_bound_json_without_provider_failure(
    tmp_path: Path,
) -> None:
    (tmp_path / "repo").mkdir()
    agent_dir = tmp_path / "v3-governance-run" / "agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "evidence_cards.json").write_text("{not-json", encoding="utf-8")
    declared_outputs = [
        {
            "output_id": "evidence",
            "artifact": "evidence_cards.json",
            "media_type": "application/json",
            "required": True,
            "producer_step_id": "agent",
            "producer_port_id": "evidence",
            "schema": None,
        },
        {
            "output_id": "sfmea",
            "artifact": "sfmea.json",
            "media_type": "application/json",
            "required": True,
            "producer_step_id": "design",
            "producer_port_id": "sfmea",
            "schema": None,
        },
    ]
    task_run = _task_run(
        tmp_path,
        declared_outputs=declared_outputs,
        plan_nodes=[
            {
                "node_id": "agent",
                "kind": "agent",
                "type": "agent_task",
                "handler_id": "agent",
                "handler_version": 1,
                "output_ports": [{"id": "evidence", "type": "artifact"}],
                "required_outputs": ["evidence"],
            },
            {
                "node_id": "design",
                "kind": "governance",
                "handler_id": "storage_test_design",
                "handler_version": 1,
                "depends_on": ["agent"],
                "resolved_input_bindings": {
                    "source_evidence": {
                        "source_node_id": "agent",
                        "source_port_id": "evidence",
                    }
                },
                "output_ports": [{"id": "sfmea", "type": "artifact"}],
                "required_outputs": ["sfmea"],
            },
        ],
        workflow_steps=[{"id": "agent", "type": "agent_task"}],
        agent_runs=[{"step_id": "agent", "artifact_dir": str(agent_dir)}],
        profile="storage_test_design",
    )
    runner = WorkbenchWorkflowRunner(tmp_path)
    _complete_agent(runner, agent_dir)

    result = runner.execute_task_run(task_run.task_run_id)

    assert result.execution_status == "completed"
    assert result.artifact_validation_status == "not_requested"
    assert result.governance_status == "failed"
    assert result.delivery_status == "blocked"
    governance_result = next(
        item for item in result.step_results if item["step_id"] == "design"
    )
    assert governance_result["error"] == "governance_input_json_invalid"
    assert governance_result["provider_failed"] is False


@pytest.mark.parametrize(
    ("artifact", "requested_output", "error_code"),
    [
        ("../escaped.json", "contract", "unsafe_governance_output_path"),
        ("contract.json", "undeclared", "undeclared_governance_output_edge"),
    ],
)
def test_governance_generator_rejects_unsafe_or_undeclared_output(
    tmp_path: Path,
    artifact: str,
    requested_output: str,
    error_code: str,
) -> None:
    (tmp_path / "repo").mkdir()
    output = {
        "output_id": "contract",
        "artifact": artifact,
        "required": True,
        "producer_step_id": "design",
        "producer_port_id": "contract",
        "schema": None,
    }
    task_run = _task_run(
        tmp_path,
        declared_outputs=[output],
        plan_nodes=[
            {
                "node_id": "design",
                "kind": "governance",
                "handler_id": "storage_test_design",
                "handler_version": 1,
                "required_outputs": [requested_output],
            }
        ],
        profile="storage_test_design",
    )

    result = WorkbenchWorkflowRunner(tmp_path).execute_task_run(task_run.task_run_id)

    assert result.execution_status == "completed"
    assert result.governance_status == "failed"
    assert result.delivery_status == "blocked"
    governance_result = result.step_results[0]
    assert governance_result["error"] == error_code
    assert not (tmp_path / "escaped.json").exists()


def test_handler_capability_snapshot_composes_existing_runtime_registries() -> None:
    from app.services.workflow_handler_registry import (
        workflow_handler_capability_snapshot,
    )

    handlers = workflow_handler_capability_snapshot()["handlers"]

    assert handlers["source_evidence"] == {"versions": [1], "kind": "validator"}
    assert handlers["storage_test_design"] == {
        "versions": [1],
        "kind": "governance",
        "input_ports": [
            {
                "key": "source_evidence",
                "label": "源码证据",
                "type": "artifact",
                "required": True,
                "collection": False,
            }
        ],
        "output_ports": [
            {
                "key": "sfmea",
                "label": "SFMEA 风险清单",
                "type": "artifact",
                "required": True,
                "collection": False,
            },
            {
                "key": "black_box_cases",
                "label": "黑盒测试用例",
                "type": "artifact",
                "required": True,
                "collection": False,
            },
        ],
    }
    assert handlers["sfmea"] == {"versions": [1], "kind": "validator"}
