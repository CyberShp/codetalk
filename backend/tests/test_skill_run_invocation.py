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
    return SimpleNamespace(
        version_id="skill_version_1",
        skill_id="skill.example",
        content_digest="sha256:" + "1" * 64,
        source_zip_path=source_zip,
        ir_path=ir,
        validation_report_path=validation,
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
    assert payload["source_zip"]["ref"] == "source.zip"
    assert payload["source_zip"]["digest"].startswith("sha256:")
    assert payload["skill_ir"]["ref"] == "skill-ir.json"
    assert payload["skill_ir"]["digest"] == payload["skill_ir_digest"]
    assert payload["validation_report"]["ref"] == "validation.json"
    assert payload["validation_report"]["digest"].startswith("sha256:")
    assert payload["input_snapshot"]["ref"] == "skill_input_snapshot.json"
    assert json.loads((tmp_path / "run" / "skill_input_snapshot.json").read_text(encoding="utf-8")) == {
        "input.source": str(tmp_path)
    }
    assert payload["selected_delivery_ids"] == ["delivery.report"]
    assert payload["required_artifact_ids"] == ["artifact.report"]
    assert payload["judge"]["required"] is True


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
