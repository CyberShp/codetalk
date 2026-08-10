from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


SCHEMA_DIR = Path(__file__).parents[1] / "app" / "schemas" / "skills"


def _schema_document(name: str) -> dict:
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def _validator(name: str) -> Draft202012Validator:
    resources = [Resource.from_contents(json.loads(path.read_text(encoding="utf-8"))) for path in SCHEMA_DIR.glob("*.schema.json")]
    registry = Registry().with_resources((resource.id(), resource) for resource in resources)
    return Draft202012Validator(_schema_document(name), registry=registry, format_checker=FormatChecker())


def _version(tmp_path):
    source_zip = tmp_path / "source.zip"
    ir = tmp_path / "skill-ir.json"
    validation = tmp_path / "validation.json"
    source_zip.write_bytes(b"zip")
    ir.write_text(
        json.dumps(
            {
                "schema_version": "skill-ir-v1",
                "skill_id": "skill.example",
                "content_digest": "sha256:" + "1" * 64,
                "required_agent_capabilities": ["tools", "artifact_collection"],
                "inputs": [{"input_id": "input.source", "label": "Source", "kind": "workspace", "required": True}],
                "steps": [
                    {
                        "step_id": "step.collect",
                        "title": "Collect",
                        "instruction_path": "steps/collect.md",
                        "depends_on": [],
                        "produces": ["artifact.report"],
                        "completion_gate": {"required_artifact_ids": ["artifact.report"]},
                    }
                ],
                "artifacts": [
                    {
                        "artifact_id": "artifact.report",
                        "path": "report.md",
                        "producer_step_id": "step.collect",
                        "required": True,
                        "visibility": "delivery",
                    }
                ],
                "deliveries": [{"delivery_id": "delivery.report", "label": "Report", "artifact_ids": ["artifact.report"]}],
                "scripts": [],
                "core_rules": [{"rule_id": "rule.safe", "instruction_path": "rules/safe.md", "acknowledgement_required": True}],
                "judge": {"required": True, "isolated_session": True, "artifact_ids": ["artifact.report"]},
                "topological_order": ["step.collect"],
                "source_file_digests": [{"path": "skill.json", "digest": "sha256:" + "2" * 64}],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    validation.write_text("{}", encoding="utf-8")
    unpacked_root = tmp_path / "source"
    (unpacked_root / "steps").mkdir(parents=True)
    (unpacked_root / "steps" / "collect.md").write_text(
        "# Collect real evidence\n", encoding="utf-8"
    )
    return SimpleNamespace(
        version_id="skill_version_1",
        skill_id="skill.example",
        content_digest="sha256:" + "1" * 64,
        source_zip_path=source_zip,
        ir_path=ir,
        validation_report_path=validation,
        unpacked_root=unpacked_root,
    )


def test_freeze_skill_run_invocation_writes_immutable_execution_record(tmp_path):
    from app.services.skill_run_invocation import freeze_skill_run_invocation

    invocation = freeze_skill_run_invocation(
        version=_version(tmp_path),
        task_run_id="task_run_1",
        task_id="task_1",
        artifact_root=tmp_path / "run",
        inputs={"input.source": str(tmp_path)},
        selected_deliveries=["delivery.report"],
        expected_content_digest="sha256:" + "1" * 64,
    )

    payload = json.loads((tmp_path / "run" / "skill_invocation.json").read_text(encoding="utf-8"))
    errors = list(_validator("skill-run-invocation-v1").iter_errors(payload))
    assert errors == []
    assert payload["invocation_id"] == invocation.invocation_id
    assert payload["schema_version"] == "skill-run-invocation-v1"
    assert payload["invocation_digest"] == invocation.invocation_digest
    assert payload["task_run_id"] == "task_run_1"
    assert payload["task_id"] == "task_1"
    assert payload["skill_id"] == "skill.example"
    assert payload["skill_version_id"] == "skill_version_1"
    assert payload["skill_content_digest"] == "sha256:" + "1" * 64
    assert payload["source_zip"]["ref"] == "frozen_skill/source-package.zip"
    assert payload["source_zip"]["digest"].startswith("sha256:")
    assert payload["skill_ir"]["ref"] == "frozen_skill/skill-ir-v1.json"
    assert payload["skill_ir"]["digest"] == payload["skill_ir_digest"]
    assert payload["validation_report"]["ref"] == (
        "frozen_skill/validation-report.json"
    )
    assert payload["validation_report"]["digest"].startswith("sha256:")
    assert payload["input_snapshot"]["ref"] == "skill_input_snapshot.json"
    assert json.loads((tmp_path / "run" / "skill_input_snapshot.json").read_text(encoding="utf-8")) == {
        "input.source": str(tmp_path)
    }
    assert payload["selected_delivery_ids"] == ["delivery.report"]
    assert payload["required_artifact_ids"] == ["artifact.report"]
    assert payload["judge"]["required"] is True


def test_freeze_skill_run_invocation_copies_immutable_skill_inputs_into_attempt(
    tmp_path,
):
    from app.services.skill_run_invocation import freeze_skill_run_invocation

    version = _version(tmp_path)
    run_root = tmp_path / "run-frozen"
    invocation = freeze_skill_run_invocation(
        version=version,
        task_run_id="task_run_frozen",
        task_id="task_frozen",
        artifact_root=run_root,
        inputs={"input.source": str(tmp_path)},
        expected_content_digest="sha256:" + "1" * 64,
    )

    frozen_root = run_root / "frozen_skill"
    assert (frozen_root / "source" / "steps" / "collect.md").read_text(
        encoding="utf-8"
    ) == "# Collect real evidence\n"
    for reference in (
        invocation.source_zip,
        invocation.skill_ir,
        invocation.validation_report,
    ):
        frozen_path = run_root / reference["ref"]
        assert frozen_path.is_file()
        assert frozen_path.is_relative_to(frozen_root)

    (version.unpacked_root / "steps" / "collect.md").write_text(
        "# mutated published source\n", encoding="utf-8"
    )
    assert (frozen_root / "source" / "steps" / "collect.md").read_text(
        encoding="utf-8"
    ) == "# Collect real evidence\n"


def test_freeze_skill_run_invocation_rejects_digest_or_artifact_drift(tmp_path):
    from app.services.skill_run_invocation import (
        SkillRunInvocationError,
        freeze_skill_run_invocation,
    )

    version = _version(tmp_path)
    with pytest.raises(SkillRunInvocationError, match="content digest"):
        freeze_skill_run_invocation(
            version=version,
            task_run_id="task_run_1",
            task_id="task_1",
            artifact_root=tmp_path / "run",
            inputs={},
            expected_content_digest="sha256:" + "2" * 64,
        )

    version.source_zip_path.unlink()
    with pytest.raises(SkillRunInvocationError, match="source_zip_path"):
        freeze_skill_run_invocation(
            version=version,
            task_run_id="task_run_1",
            task_id="task_1",
            artifact_root=tmp_path / "run",
            inputs={},
            expected_content_digest="sha256:" + "1" * 64,
        )


def test_freeze_skill_run_invocation_records_real_agent_runtime_without_fake_preflight(
    tmp_path,
):
    from app.services.skill_run_invocation import freeze_skill_run_invocation

    invocation = freeze_skill_run_invocation(
        version=_version(tmp_path),
        task_run_id="task_run_real",
        task_id="task_real",
        artifact_root=tmp_path / "run-real",
        inputs={"input.source": str(tmp_path)},
        expected_content_digest="sha256:" + "1" * 64,
        agent_runtime={
            "id": "default-opencode",
            "provider": "opencode",
            "command": "/usr/local/bin/opencode",
            "args": ["--model", "deepseek/deepseek-v4-flash"],
            "prompt_transport": "opencode_run_arg",
            "mcp_profile": "",
            "requires_network": True,
            "env": {"DEEPSEEK_API_KEY": "secret-never-freeze"},
            "timeout_seconds": 900,
        },
    )

    producer = invocation.runtime["producer"]
    assert producer["runtime_id"] == "agent-runtime:default-opencode"
    assert producer["requested_provider"] == "opencode"
    assert producer["preflight_receipt"]["status"] == "pending"
    assert producer["execution"] == {
        "runtime_config_id": "default-opencode",
        "provider_ref": "agent-runtime:default-opencode",
        "command": [
            "/usr/local/bin/opencode",
            "--model",
            "deepseek/deepseek-v4-flash",
        ],
        "prompt_transport": "opencode_run_arg",
        "mcp_profile": "",
        "requires_network": True,
        "environment_keys": ["DEEPSEEK_API_KEY"],
    }
    assert "secret-never-freeze" not in json.dumps(invocation.runtime)
    assert producer["observed_runtime_version"] == "unknown"


@pytest.mark.parametrize(
    ("profile_id", "idle_timeout", "step_timeout", "overall_timeout"),
    [
        ("rapid", 300, 1200, 1800),
        ("deep", 600, 5400, 7200),
    ],
)
def test_freeze_skill_run_invocation_records_independent_profile_timeout_budgets(
    tmp_path,
    profile_id,
    idle_timeout,
    step_timeout,
    overall_timeout,
):
    from app.services.skill_run_invocation import freeze_skill_run_invocation

    invocation = freeze_skill_run_invocation(
        version=_version(tmp_path),
        task_run_id=f"task_run_{profile_id}",
        task_id=f"task_{profile_id}",
        artifact_root=tmp_path / f"run-{profile_id}",
        inputs={"input.source": str(tmp_path)},
        expected_content_digest="sha256:" + "1" * 64,
        execution_profile_id=profile_id,
    )

    budget = invocation.runtime["producer"]["timeout_budget"]
    assert budget["profile_id"] == profile_id
    assert budget["idle_timeout_seconds"] == idle_timeout
    assert budget["step_timeout_seconds"] == step_timeout
    assert budget["overall_timeout_seconds"] == overall_timeout
    assert budget["agent_timeout_seconds"] == step_timeout

    payload = json.loads(
        (tmp_path / f"run-{profile_id}" / "skill_invocation.json").read_text(
            encoding="utf-8"
        )
    )
    assert list(_validator("skill-run-invocation-v1").iter_errors(payload)) == []
