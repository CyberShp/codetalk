from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path


def _contains_key(value: object, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        return any(str(key) in forbidden for key in value) or any(
            _contains_key(item, forbidden) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_key(item, forbidden) for item in value)
    return False


def test_generic_invocation_contract_accepts_only_rendered_input_outputs_and_provider() -> None:
    from app.services.agent_invocation_contract import build_agent_invocation_contract

    assert list(inspect.signature(build_agent_invocation_contract).parameters) == [
        "rendered_input",
        "declared_outputs",
        "provider_config",
    ]

    rendered_input = " first line\n\nlast line "
    contract = build_agent_invocation_contract(
        rendered_input=rendered_input,
        declared_outputs=[
            {
                "output_id": "report",
                "artifact": "report.md",
                "required": True,
            }
        ],
        provider_config={"provider": "local", "artifact_dir": "/artifacts"},
    )

    assert contract == {
        "contract_version": 2,
        "rendered_input": rendered_input,
        "declared_outputs": [
            {
                "output_id": "report",
                "artifact": "report.md",
                "required": True,
            }
        ],
        "provider_config": {
            "provider": "local",
            "artifact_dir": "/artifacts",
        },
    }


def test_generic_invocation_contract_preserves_resolved_multiline_inputs_verbatim() -> None:
    from app.services.agent_run_harness import (
        AgentRunRecord,
        _generic_agent_invocation_contract,
    )

    analysis_target = "  first line\n\nsecond line  "
    contract = _generic_agent_invocation_contract(
        run=AgentRunRecord(
            run_id="run-1",
            turn_id="turn-1",
            provider="codex",
            command=["codex"],
            cwd="/repo",
            artifact_dir="/artifacts",
        ),
        task_bundle={
            "resolved_inputs": {
                "analysis_target": analysis_target,
                "design_doc": {"path": "/inputs/design.md"},
            },
            "required_artifacts": ["report.md"],
        },
    )

    rendered = json.loads(contract["rendered_input"])
    assert rendered["resolved_inputs"]["analysis_target"] == analysis_target
    assert rendered["resolved_inputs"]["design_doc"] == {
        "path": "/inputs/design.md"
    }


def test_v3_harness_prompt_and_manifests_are_domain_neutral(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.services.agent_sandbox import AgentSandboxLaunch
    from app.services.agent_run_harness import AgentRunHarness

    artifact_dir = tmp_path / "agent"
    captured_path = tmp_path / "captured.json"
    rendered_input = " preserve me\n\nverbatim "
    declared_outputs = [
        {
            "output_id": "report",
            "artifact": "report.md",
            "required": True,
            "schema": None,
        }
    ]
    script = (
        "import json, os, pathlib, sys; "
        "payload=json.load(sys.stdin); "
        "pathlib.Path(sys.argv[1]).write_text(json.dumps(payload), encoding='utf-8'); "
        "pathlib.Path(os.environ['CODETALK_AGENT_ARTIFACT_DIR'], 'report.md').write_text('ok', encoding='utf-8')"
    )
    monkeypatch.setattr(
        "app.services.agent_run_harness.prepare_agent_sandbox",
        lambda **_kwargs: AgentSandboxLaunch(
            status="test-bypass",
            wrapper=[],
            message="contract test",
            audit={"status": "test-bypass"},
        ),
    )
    harness = AgentRunHarness(artifact_dir)
    run = harness.create_run(
        provider="local-python",
        command=[sys.executable, "-c", script, str(captured_path)],
        cwd=str(tmp_path),
        workflow_snapshot={"id": "wf-v3", "compiled_contract_version": 3},
        task_bundle={
            "compiled_contract_version": 3,
            "rendered_user_input": rendered_input,
            "declared_outputs": declared_outputs,
            "required_artifacts": ["report.md"],
            "test_activity_contract": {"target": "must not leak"},
            "black_box_generation_policy": {"rule": "must not leak"},
            "execution_contract": {
                "outputs": {
                    "declared_outputs": declared_outputs,
                    "required_artifacts": ["report.md"],
                },
                "test_activity_contract": {"target": "must not leak"},
            },
        },
        prompt_transport="stdin",
        requires_network=False,
    )
    result = harness.execute_run(run.run_id, timeout_sec=10)

    assert result.status == "completed"
    invocation = json.loads(
        (artifact_dir / "agent_invocation.json").read_text(encoding="utf-8")
    )
    prompt = json.loads(captured_path.read_text(encoding="utf-8"))
    assert prompt["rendered_input"] == rendered_input
    assert set(prompt) == {
        "contract_version",
        "rendered_input",
        "declared_outputs",
        "provider_config",
    }
    assert prompt["declared_outputs"] == declared_outputs
    assert invocation["invocation_contract"] == prompt

    output_contract = json.loads(
        (artifact_dir / "agent_output_contract.json").read_text(encoding="utf-8")
    )
    capability = json.loads(
        (artifact_dir / "capability_manifest.json").read_text(encoding="utf-8")
    )
    forbidden = {
        "test_activity_contract",
        "black_box_generation_policy",
        "test_activity_writing_protocol",
    }
    assert not _contains_key(invocation, forbidden)
    assert not _contains_key(output_contract, forbidden)
    assert capability["outputs"] == {
        "required_artifacts": ["report.md"],
        "declared_output_count": 1,
        "expected_schema_count": 0,
        "artifact_dir": str(artifact_dir),
    }


def test_legacy_workbench_helper_preserves_specialist_contract_fields(
    tmp_path: Path,
) -> None:
    from app.services.agent_run_harness import AgentRunHarness
    from app.services.legacy_workbench_harness_contract import (
        build_legacy_workbench_harness_contract,
    )

    assert callable(build_legacy_workbench_harness_contract)
    artifact_dir = tmp_path / "legacy-agent"
    activity_contract = {
        "target": "legacy target",
        "required_outputs": ["sfmea.json", "black_box_cases.json"],
    }
    generation_policy = {"authority_rule": "legacy rule"}
    AgentRunHarness(artifact_dir).create_run(
        provider="local-python",
        command=[sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        workflow_snapshot={"id": "wf-v2", "schema_version": 2},
        task_bundle={
            "compiled_contract_version": 2,
            "required_artifacts": ["sfmea.json", "black_box_cases.json"],
            "test_activity_contract": activity_contract,
            "black_box_generation_policy": generation_policy,
            "execution_contract": {"test_activity_contract": activity_contract},
        },
        requires_network=False,
    )

    output_contract = json.loads(
        (artifact_dir / "agent_output_contract.json").read_text(encoding="utf-8")
    )
    invocation = json.loads(
        (artifact_dir / "agent_invocation.json").read_text(encoding="utf-8")
    )
    assert output_contract["test_activity_contract"] == activity_contract
    assert output_contract["black_box_generation_policy"] == generation_policy
    assert "test_activity_writing_protocol" in output_contract["evidence_rules"]
    assert invocation["test_activity_contract"] == activity_contract
