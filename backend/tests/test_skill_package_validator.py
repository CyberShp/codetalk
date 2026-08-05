"""RED contract for F014 Task 4 deterministic Skill validation."""

from __future__ import annotations

import copy
import importlib
import json
from dataclasses import is_dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "skills" / "contracts" / "positive"


def _validator() -> ModuleType:
    try:
        return importlib.import_module("app.services.skill_package_validator")
    except ModuleNotFoundError as exc:
        if exc.name == "app.services.skill_package_validator":
            pytest.fail("RED: app.services.skill_package_validator has not been implemented")
        raise


def _skill_document() -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / "codetalk-skill-v1.json").read_text(encoding="utf-8"))


def _write_source_tree(root: Path, document: dict[str, Any]) -> Path:
    skill_path = root / "skill.json"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for path in {
        *(step["instruction_path"] for step in document["steps"]),
        *(script["path"] for script in document["scripts"]),
        *(rule["instruction_path"] for rule in document["core_rules"]),
    }:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {path}\n", encoding="utf-8")
    return skill_path


def _single_issue(document: dict[str, Any], tmp_path: Path) -> Any:
    validator = _validator()
    skill_path = _write_source_tree(tmp_path, document)
    result = validator.validate_skill_document(document, source_root=tmp_path, source_path=skill_path)
    assert is_dataclass(result) and getattr(type(result), "__dataclass_params__").frozen
    assert not result.ok
    assert len(result.issues) == 1
    return result.issues[0]


@pytest.mark.parametrize(
    ("mutator", "code", "path"),
    [
        (lambda doc: doc["steps"].__setitem__(1, {**doc["steps"][1], "step_id": "step.collect"}), "duplicate_id", "steps[1].step_id"),
        (lambda doc: doc["steps"][1]["depends_on"].__setitem__(0, "step.missing"), "unknown_step_dependency", "steps[1].depends_on[0]"),
        (lambda doc: doc["artifacts"][1].__setitem__("producer_step_id", "step.missing"), "unknown_artifact_producer", "artifacts[1].producer_step_id"),
        (lambda doc: doc["steps"][1].__setitem__("script_id", "script.missing"), "unknown_script", "steps[1].script_id"),
        (lambda doc: doc["steps"][1]["produces"].__setitem__(0, "artifact.missing"), "unknown_produced_artifact", "steps[1].produces[0]"),
        (lambda doc: doc["deliveries"][0]["artifact_ids"].__setitem__(0, "artifact.missing"), "unknown_delivery_artifact", "deliveries[0].artifact_ids[0]"),
        (lambda doc: doc["judge"]["artifact_ids"].__setitem__(0, "artifact.missing"), "unknown_judge_artifact", "judge.artifact_ids[0]"),
        (lambda doc: doc["steps"][1]["completion_gate"]["required_artifact_ids"].__setitem__(0, "artifact.raw"), "completion_gate_not_produced", "steps[1].completion_gate.required_artifact_ids[0]"),
        (lambda doc: doc["scripts"][0]["log_artifact_ids"].__setitem__(0, "artifact.missing"), "unknown_script_log_artifact", "scripts[0].log_artifact_ids[0]"),
    ],
)
def test_validator_reports_exact_reference_errors(mutator: Any, code: str, path: str, tmp_path: Path) -> None:
    document = _skill_document()
    mutator(document)

    issue = _single_issue(document, tmp_path)

    assert issue.code == code
    assert issue.path == path
    assert issue.message


def test_validator_rejects_dependency_cycles_with_exact_path(tmp_path: Path) -> None:
    document = _skill_document()
    document["steps"][0]["depends_on"] = ["step.analyze"]

    issue = _single_issue(document, tmp_path)

    assert issue.code == "dependency_cycle"
    assert issue.path == "steps"


def test_validator_rejects_source_file_paths_that_do_not_exist(tmp_path: Path) -> None:
    document = _skill_document()
    skill_path = _write_source_tree(tmp_path, document)
    (tmp_path / document["steps"][0]["instruction_path"]).unlink()
    validator = _validator()

    result = validator.validate_skill_document(document, source_root=tmp_path, source_path=skill_path)

    assert [(issue.code, issue.path) for issue in result.issues] == [("missing_source_file", "steps[0].instruction_path")]


def test_validator_rejects_source_path_that_is_not_safe(tmp_path: Path) -> None:
    document = _skill_document()
    unsafe_skill_path = _write_source_tree(tmp_path / "bad\\source", document)
    validator = _validator()

    result = validator.validate_skill_document(document, source_root=tmp_path, source_path=unsafe_skill_path)

    assert [(issue.code, issue.path) for issue in result.issues] == [("unsafe_path", "source_path")]


def test_validator_rejects_source_path_symlink_escape(tmp_path: Path) -> None:
    document = _skill_document()
    skill_path = _write_source_tree(tmp_path, document)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    outside_skill = outside / "skill.json"
    outside_skill.write_text(skill_path.read_text(encoding="utf-8"), encoding="utf-8")
    skill_path.unlink()
    skill_path.symlink_to(outside_skill)
    validator = _validator()

    result = validator.validate_skill_document(document, source_root=tmp_path, source_path=skill_path)

    assert [(issue.code, issue.path) for issue in result.issues] == [("unsafe_path", "source_path")]


def test_validator_rejects_referenced_source_file_symlink_escape(tmp_path: Path) -> None:
    document = _skill_document()
    skill_path = _write_source_tree(tmp_path, document)
    instruction_path = tmp_path / document["steps"][0]["instruction_path"]
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    outside_instruction = outside / "instruction.md"
    outside_instruction.write_text("# outside\n", encoding="utf-8")
    instruction_path.unlink()
    instruction_path.symlink_to(outside_instruction)
    validator = _validator()

    result = validator.validate_skill_document(document, source_root=tmp_path, source_path=skill_path)

    assert [(issue.code, issue.path) for issue in result.issues] == [("unsafe_path", "steps[0].instruction_path")]


def test_validator_rejects_artifact_produced_by_multiple_steps(tmp_path: Path) -> None:
    document = _skill_document()
    document["steps"][1]["produces"].append("artifact.raw")

    issue = _single_issue(document, tmp_path)

    assert issue.code == "multiple_artifact_producers"
    assert issue.path == "steps[1].produces[1]"


def test_validator_rejects_internal_artifact_in_delivery(tmp_path: Path) -> None:
    document = _skill_document()
    document["deliveries"][0]["artifact_ids"][0] = "artifact.raw"

    issue = _single_issue(document, tmp_path)

    assert issue.code == "delivery_artifact_not_visible"
    assert issue.path == "deliveries[0].artifact_ids[0]"


def test_validator_rejects_unconsumed_delivery_artifact(tmp_path: Path) -> None:
    document = _skill_document()
    document["artifacts"].append({
        "artifact_id": "artifact.extra_report",
        "path": "out/extra-report.md",
        "producer_step_id": "step.analyze",
        "required": True,
        "visibility": "delivery",
    })
    document["steps"][1]["produces"].append("artifact.extra_report")
    document["steps"][1]["completion_gate"]["required_artifact_ids"].append("artifact.extra_report")

    issue = _single_issue(document, tmp_path)

    assert issue.code == "unconsumed_delivery_artifact"
    assert issue.path == "artifacts[2].artifact_id"


def test_validator_rejects_required_artifact_missing_from_completion_gate(tmp_path: Path) -> None:
    document = _skill_document()
    document["steps"][1]["completion_gate"]["required_artifact_ids"].remove("artifact.report")

    issue = _single_issue(document, tmp_path)

    assert issue.code == "required_artifact_missing_from_gate"
    assert issue.path == "artifacts[1].artifact_id"


def test_validator_rejects_optional_artifact_in_completion_gate(tmp_path: Path) -> None:
    document = _skill_document()
    document["artifacts"][0]["required"] = False

    issue = _single_issue(document, tmp_path)

    assert issue.code == "optional_artifact_in_gate"
    assert issue.path == "steps[0].completion_gate.required_artifact_ids[0]"


def test_validator_rejects_duplicate_artifact_output_paths(tmp_path: Path) -> None:
    document = _skill_document()
    document["artifacts"][1]["path"] = document["artifacts"][0]["path"]

    issue = _single_issue(document, tmp_path)

    assert issue.code == "duplicate_artifact_path"
    assert issue.path == "artifacts[1].path"


def test_validator_rejects_artifact_output_path_prefix_conflicts(tmp_path: Path) -> None:
    document = _skill_document()
    document["artifacts"][0]["path"] = "out"
    document["artifacts"][1]["path"] = "out/report.md"

    issue = _single_issue(document, tmp_path)

    assert issue.code == "artifact_path_prefix_conflict"
    assert issue.path == "artifacts[1].path"


def test_validator_rejects_unsafe_selected_workflow_path(tmp_path: Path) -> None:
    document = _skill_document()
    document["selected_workflow_path"] = "./workflow.md"

    issue = _single_issue(document, tmp_path)

    assert issue.code == "unsafe_path"
    assert issue.path == "selected_workflow_path"


def test_validator_rejects_unsafe_completion_gate_glob_path(tmp_path: Path) -> None:
    document = _skill_document()
    document["steps"][0]["completion_gate"]["requires_glob"] = ["./out/*.md"]

    issue = _single_issue(document, tmp_path)

    assert issue.code == "unsafe_path"
    assert issue.path == "steps[0].completion_gate.requires_glob[0]"


def test_validator_rejects_nfc_casefold_path_collisions(tmp_path: Path) -> None:
    document = _skill_document()
    document["artifacts"][0]["path"] = "out/caf\u00e9.md"
    document["artifacts"][1]["path"] = "out/CAFE\u0301.md"

    issue = _single_issue(document, tmp_path)

    assert issue.code == "canonical_path_collision"
    assert issue.path == "artifacts[1].path"


def test_validator_accepts_the_positive_contract_fixture(tmp_path: Path) -> None:
    document = _skill_document()
    skill_path = _write_source_tree(tmp_path, document)
    validator = _validator()

    result = validator.validate_skill_document(copy.deepcopy(document), source_root=tmp_path, source_path=skill_path)

    assert result.ok
    assert result.issues == ()
