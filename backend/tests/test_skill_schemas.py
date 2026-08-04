"""Offline contract tests for the F014 JSON Schema documents."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from unicodedata import normalize

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


ROOT = Path(__file__).parents[1]
SCHEMA_DIR = ROOT / "app" / "schemas" / "skills"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "skills" / "contracts"


def _document(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _strict_document(path: Path) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        document: dict[str, Any] = {}
        for key, value in pairs:
            if key in document:
                raise ValueError(f"duplicate JSON key {key!r} in {path.name}")
            document[key] = value
        return document

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_keys)


def _registry() -> Registry:
    resources = [Resource.from_contents(_document(path)) for path in SCHEMA_DIR.glob("*.schema.json")]
    return Registry().with_resources((resource.id(), resource) for resource in resources)


def _validator(name: str) -> Draft202012Validator:
    schema = _document(SCHEMA_DIR / f"{name}.schema.json")
    return Draft202012Validator(schema, registry=_registry(), format_checker=FormatChecker())


def _set(document: dict[str, Any], path: list[str | int], value: Any) -> None:
    target: Any = document
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = value


def _delete(document: dict[str, Any], path: list[str | int]) -> None:
    target: Any = document
    for segment in path[:-1]:
        target = target[segment]
    del target[path[-1]]


def _mutated_document(fixture: dict[str, Any]) -> dict[str, Any]:
    document = copy.deepcopy(_document(FIXTURE_DIR / "positive" / fixture["source"]))
    mutation = fixture["mutation"]
    if mutation["operation"] == "set":
        _set(document, mutation["path"], mutation["value"])
    elif mutation["operation"] == "delete":
        _delete(document, mutation["path"])
    else:
        raise AssertionError(f"unsupported mutation: {mutation['operation']}")
    return document


@pytest.mark.parametrize(
    "schema_name",
    [
        "codetalk-skill-v1",
        "codetalk-skill-pack-v1",
        "skill-ir-v1",
        "skill-review-v1",
        "skill-run-invocation-v1",
        "agent-capability-report-v1",
    ],
)
def test_positive_contract_fixtures_validate_offline(schema_name: str) -> None:
    assert list(_validator(schema_name).iter_errors(_document(FIXTURE_DIR / "positive" / f"{schema_name}.json"))) == []


@pytest.mark.parametrize("case", sorted((FIXTURE_DIR / "negative").glob("*.json")))
def test_negative_contract_fixtures_fail_at_declared_path(case: Path) -> None:
    fixture = _document(case)
    document = _mutated_document(fixture)
    errors = list(_validator(fixture["schema"]).iter_errors(document))
    paths = [list(error.absolute_path) for error in errors]
    assert paths, case.name
    assert {tuple(path) for path in paths} == {tuple(fixture["expected_error_path"])}, (case.name, paths)


@pytest.mark.parametrize("case", sorted((FIXTURE_DIR / "negative").glob("*.json")))
def test_negative_contract_fixture_is_a_single_positive_mutation(case: Path) -> None:
    fixture = _document(case)
    assert set(fixture) == {"schema", "source", "mutation", "expected_error_path"}
    assert fixture["source"] == f"{fixture['schema']}.json"
    assert (FIXTURE_DIR / "positive" / fixture["source"]).is_file()
    assert fixture["mutation"]["operation"] in {"set", "delete"}
    assert fixture["mutation"]["path"]
    if fixture["mutation"]["operation"] == "set":
        assert set(fixture["mutation"]) == {"operation", "path", "value"}
    else:
        assert set(fixture["mutation"]) == {"operation", "path"}
    assert _mutated_document(fixture) != _document(FIXTURE_DIR / "positive" / fixture["source"])


def test_schema_ids_and_references_are_offline_resolvable() -> None:
    registry = _registry()
    for path in SCHEMA_DIR.glob("*.schema.json"):
        schema = _document(path)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"].startswith("https://codetalk.local/schemas/skills/")
        Draft202012Validator.check_schema(schema)
        assert registry.contents(schema["$id"]) == schema


def test_schema_documents_have_no_duplicate_json_keys() -> None:
    for path in SCHEMA_DIR.glob("*.schema.json"):
        assert _strict_document(path)


@pytest.mark.parametrize(
    ("schema_name", "path"),
    [
        ("skill-run-invocation-v1", ["runtime", "producer", "declared_context_window_tokens"]),
        ("skill-run-invocation-v1", ["runtime", "producer", "requested_max_output_tokens"]),
        ("skill-run-invocation-v1", ["runtime", "judge", "declared_context_window_tokens"]),
        ("skill-run-invocation-v1", ["runtime", "judge", "requested_max_output_tokens"]),
        ("agent-capability-report-v1", ["declared_context_window_tokens"]),
        ("agent-capability-report-v1", ["requested_max_output_tokens"]),
        ("skill-review-v1", ["review_evidence", "declared_context_window_tokens"]),
        ("skill-review-v1", ["review_evidence", "requested_max_output_tokens"]),
    ],
)
def test_deepseek_v4_flash_limits_are_schema_enforced(schema_name: str, path: list[str]) -> None:
    document = _document(FIXTURE_DIR / "positive" / f"{schema_name}.json")
    _set(document, path, 199999 if path[-1] == "declared_context_window_tokens" else 4097)
    assert list(_validator(schema_name).iter_errors(document))


@pytest.mark.parametrize("schema_name", ["skill-run-invocation-v1", "agent-capability-report-v1", "skill-review-v1"])
def test_non_deepseek_models_are_not_forced_to_deepseek_limits(schema_name: str) -> None:
    document = _document(FIXTURE_DIR / "positive" / f"{schema_name}.json")
    if schema_name == "skill-run-invocation-v1":
        targets = [document["runtime"]["producer"], document["runtime"]["judge"]]
    elif schema_name == "skill-review-v1":
        targets = [document["review_evidence"]]
    else:
        targets = [document]
    for target in targets:
        target["requested_model"] = "anthropic/claude-sonnet"
        target["declared_context_window_tokens"] = 100000
        target["requested_max_output_tokens"] = 8192
    assert list(_validator(schema_name).iter_errors(document)) == []


@pytest.mark.parametrize("unsafe_path", ["../escape.md", "/absolute.md", "C:/escape.md", "\\\\host\\share", "dir\\file.md", "bad\u0000path"])
def test_skill_paths_reject_escape_and_platform_specific_forms(unsafe_path: str) -> None:
    document = _document(FIXTURE_DIR / "positive" / "codetalk-skill-v1.json")
    document["steps"][0]["instruction_path"] = unsafe_path
    assert list(_validator("codetalk-skill-v1").iter_errors(document))


def test_skill_paths_accept_utf8_relative_paths() -> None:
    document = _document(FIXTURE_DIR / "positive" / "codetalk-skill-v1.json")
    assert list(_validator("codetalk-skill-v1").iter_errors(document)) == []


def test_schema_reserves_cross_reference_validation_for_compiler() -> None:
    document = _document(FIXTURE_DIR / "positive" / "codetalk-skill-v1.json")
    document["steps"][0]["depends_on"] = ["step.not_present"]
    document["steps"][1]["step_id"] = document["steps"][0]["step_id"]
    assert list(_validator("codetalk-skill-v1").iter_errors(document)) == []


def test_schema_preserves_nfc_and_nfd_collision_for_compiler_boundary() -> None:
    document = _document(FIXTURE_DIR / "positive" / "codetalk-skill-v1.json")
    nfc_path = "caf\u00e9/out.md"
    nfd_path = "cafe\u0301/out.md"
    assert nfc_path != nfd_path
    assert normalize("NFC", nfc_path) == normalize("NFC", nfd_path)
    document["artifacts"][0]["path"] = nfc_path
    document["artifacts"].append({
        "artifact_id": "artifact.other",
        "path": nfd_path,
        "producer_step_id": "step.collect",
        "required": True,
        "visibility": "internal",
    })
    assert list(_validator("codetalk-skill-v1").iter_errors(document)) == []


def test_invocation_requires_isolated_judge_for_required_judge() -> None:
    document = _document(FIXTURE_DIR / "positive" / "skill-run-invocation-v1.json")
    document["judge"]["isolated_session"] = False
    assert list(_validator("skill-run-invocation-v1").iter_errors(document))


@pytest.mark.parametrize("schema_name", ["codetalk-skill-v1", "skill-ir-v1"])
def test_required_agent_capabilities_are_closed_to_runtime_capability_vocabulary(schema_name: str) -> None:
    document = _document(FIXTURE_DIR / "positive" / f"{schema_name}.json")
    document["required_agent_capabilities"].append("misspelled_capability")
    assert list(_validator(schema_name).iter_errors(document))


@pytest.mark.parametrize("field", ["findings", "proposed_patches"])
def test_review_findings_and_proposed_patches_require_structured_fail_closed_items(field: str) -> None:
    document = _document(FIXTURE_DIR / "positive" / "skill-review-v1.json")
    document["review_evidence"][field] = ["unstructured review text"]
    assert list(_validator("skill-review-v1").iter_errors(document))


def test_review_fixture_exercises_structured_finding_and_patch_contracts() -> None:
    document = _document(FIXTURE_DIR / "positive" / "skill-review-v1.json")
    assert document["review_evidence"]["findings"]
    assert document["review_evidence"]["proposed_patches"]
    assert list(_validator("skill-review-v1").iter_errors(document)) == []


@pytest.mark.parametrize(
    ("session_name", "scope"),
    [
        ("producer", "frozen_inputs_and_artifacts_only"),
        ("judge", "own_session"),
    ],
)
def test_invocation_session_role_constrains_conversation_scope(session_name: str, scope: str) -> None:
    document = _document(FIXTURE_DIR / "positive" / "skill-run-invocation-v1.json")
    document["sessions"][session_name]["conversation_scope"] = scope
    assert list(_validator("skill-run-invocation-v1").iter_errors(document))


def test_required_judge_requires_a_non_null_judge_session() -> None:
    document = _document(FIXTURE_DIR / "positive" / "skill-run-invocation-v1.json")
    document["sessions"]["judge"] = None
    assert list(_validator("skill-run-invocation-v1").iter_errors(document))


def test_optional_judge_can_run_when_both_preflights_pass() -> None:
    document = copy.deepcopy(_document(FIXTURE_DIR / "positive" / "skill-run-invocation-v1.json"))
    document["judge"]["required"] = False

    assert isinstance(document["runtime"]["producer"], dict)
    assert isinstance(document["runtime"]["judge"], dict)
    assert document["runtime"]["producer"]["preflight_receipt"]["status"] == "passed"
    assert document["runtime"]["judge"]["preflight_receipt"]["status"] == "passed"
    assert isinstance(document["sessions"]["producer"], dict)
    assert isinstance(document["sessions"]["judge"], dict)
    assert list(_validator("skill-run-invocation-v1").iter_errors(document)) == []


def test_optional_executed_judge_requires_isolated_session() -> None:
    document = copy.deepcopy(_document(FIXTURE_DIR / "positive" / "skill-run-invocation-v1.json"))
    document["judge"]["required"] = False
    validator = _validator("skill-run-invocation-v1")
    assert list(validator.iter_errors(document)) == []

    document["judge"]["isolated_session"] = False
    paths = [list(error.absolute_path) for error in validator.iter_errors(document)]
    assert paths == [["judge", "isolated_session"]]


def test_optional_executed_judge_requires_artifacts() -> None:
    document = copy.deepcopy(_document(FIXTURE_DIR / "positive" / "skill-run-invocation-v1.json"))
    document["judge"]["required"] = False
    validator = _validator("skill-run-invocation-v1")
    assert list(validator.iter_errors(document)) == []

    document["judge"]["artifact_ids"] = []
    paths = [list(error.absolute_path) for error in validator.iter_errors(document)]
    assert paths == [["judge", "artifact_ids"]]


def test_terminal_ir_fixture_carries_all_execution_contract_groups() -> None:
    document = _document(FIXTURE_DIR / "positive" / "skill-ir-v1.json")
    assert set(document).issuperset({
        "inputs",
        "steps",
        "topological_order",
        "artifacts",
        "deliveries",
        "scripts",
        "core_rules",
        "judge",
        "source_file_digests",
    })
    assert document["inputs"]
    assert document["steps"]
    assert document["topological_order"]
    assert document["artifacts"]
    assert document["deliveries"]
    assert document["scripts"]
    assert document["core_rules"]
    assert document["source_file_digests"]
    assert document["core_rules"][0]["acknowledgement_required"] is True
    assert document["judge"]["required"] is True
    assert document["judge"]["isolated_session"] is True
    assert all(step["completion_gate"] for step in document["steps"])
    script = document["scripts"][0]
    assert set(script).issuperset({
        "working_directory",
        "timeout_seconds",
        "allowed_exit_codes",
        "log_artifact_ids",
        "write_scope",
    })


def test_invocation_sessions_freeze_requested_and_effective_model_provenance() -> None:
    document = _document(FIXTURE_DIR / "positive" / "skill-run-invocation-v1.json")
    for runtime in document["runtime"].values():
        assert runtime["requested_model"]
        assert runtime["effective_model"]
    assert list(_validator("skill-run-invocation-v1").iter_errors(document)) == []


@pytest.mark.parametrize("schema_name", ["skill-run-invocation-v1", "agent-capability-report-v1", "skill-review-v1"])
def test_deepseek_constraints_follow_requested_not_effective_model(schema_name: str) -> None:
    document = _document(FIXTURE_DIR / "positive" / f"{schema_name}.json")
    if schema_name == "skill-run-invocation-v1":
        targets = [document["runtime"]["producer"], document["runtime"]["judge"]]
    elif schema_name == "skill-review-v1":
        targets = [document["review_evidence"]]
    else:
        targets = [document]
    for target in targets:
        target["requested_model"] = "example/other-model"
        target["effective_model"] = "deepseek/deepseek-v4-flash"
        target["declared_context_window_tokens"] = 100000
        target["requested_max_output_tokens"] = 8192
    assert list(_validator(schema_name).iter_errors(document)) == []


def test_invocation_runtime_envelopes_are_complete_and_sessions_only_hold_identity() -> None:
    document = _document(FIXTURE_DIR / "positive" / "skill-run-invocation-v1.json")
    required_runtime_fields = {
        "runtime_id",
        "requested_provider",
        "effective_provider",
        "requested_model",
        "effective_model",
        "observed_runtime_version",
        "requested_capabilities",
        "declared_context_window_tokens",
        "requested_max_output_tokens",
        "timeout_budget",
        "capability_report_id",
        "capability_report_digest",
        "preflight_receipt",
    }
    required_timeout_fields = {
        "queue_timeout_seconds",
        "agent_timeout_seconds",
        "script_timeout_seconds",
        "validation_timeout_seconds",
        "overall_timeout_seconds",
    }
    for role in ("producer", "judge"):
        runtime = document["runtime"][role]
        assert set(runtime).issuperset(required_runtime_fields)
        assert runtime["requested_capabilities"]
        assert set(runtime["timeout_budget"]) == required_timeout_fields
        assert runtime["preflight_receipt"]["status"] == "passed"
    for role in ("producer", "judge"):
        assert set(document["sessions"][role]) == {
            "agent_session_id",
            "role",
            "runtime_id",
            "conversation_scope",
        }


@pytest.mark.parametrize(
    ("path", "expected_path"),
    [
        (["runtime", "producer", "requested_capabilities"], ["runtime", "producer"]),
        (["runtime", "producer", "effective_provider"], ["runtime", "producer"]),
        (["runtime", "producer", "timeout_budget", "queue_timeout_seconds"], ["runtime", "producer", "timeout_budget"]),
        (["runtime", "producer", "timeout_budget", "agent_timeout_seconds"], ["runtime", "producer", "timeout_budget"]),
        (["runtime", "producer", "timeout_budget", "script_timeout_seconds"], ["runtime", "producer", "timeout_budget"]),
        (["runtime", "producer", "timeout_budget", "validation_timeout_seconds"], ["runtime", "producer", "timeout_budget"]),
        (["runtime", "producer", "timeout_budget", "overall_timeout_seconds"], ["runtime", "producer", "timeout_budget"]),
        (["runtime", "judge"], ["runtime"]),
        (["runtime", "judge", "requested_capabilities"], ["runtime", "judge"]),
        (["runtime", "judge", "effective_provider"], ["runtime", "judge"]),
        (["runtime", "judge", "requested_model"], ["runtime", "judge"]),
        (["runtime", "judge", "declared_context_window_tokens"], ["runtime", "judge"]),
        (["runtime", "judge", "timeout_budget"], ["runtime", "judge"]),
        (["runtime", "judge", "capability_report_id"], ["runtime", "judge"]),
        (["runtime", "judge", "preflight_receipt"], ["runtime", "judge"]),
    ],
)
def test_invocation_requires_complete_role_runtime_envelopes(path: list[str], expected_path: list[str]) -> None:
    document = _document(FIXTURE_DIR / "positive" / "skill-run-invocation-v1.json")
    _delete(document, path)
    paths = [list(error.absolute_path) for error in _validator("skill-run-invocation-v1").iter_errors(document)]
    assert expected_path in paths


def test_invocation_requires_role_specific_preflight_session_gating() -> None:
    document = _document(FIXTURE_DIR / "positive" / "skill-run-invocation-v1.json")
    document["runtime"]["producer"]["preflight_receipt"]["status"] = "failed"
    paths = [list(error.absolute_path) for error in _validator("skill-run-invocation-v1").iter_errors(document)]
    assert ["sessions"] in paths

    document = _document(FIXTURE_DIR / "positive" / "skill-run-invocation-v1.json")
    document["runtime"]["judge"]["preflight_receipt"]["status"] = "failed"
    paths = [list(error.absolute_path) for error in _validator("skill-run-invocation-v1").iter_errors(document)]
    assert ["sessions", "judge"] in paths


def test_failed_producer_preflight_allows_only_null_sessions() -> None:
    document = _document(FIXTURE_DIR / "positive" / "skill-run-invocation-v1.json")
    document["runtime"]["producer"]["preflight_receipt"]["status"] = "failed"
    document["sessions"] = {"producer": None, "judge": None}
    assert list(_validator("skill-run-invocation-v1").iter_errors(document)) == []


def test_judge_session_requires_both_role_preflights_to_pass() -> None:
    document = _document(FIXTURE_DIR / "positive" / "skill-run-invocation-v1.json")
    document["runtime"]["judge"]["preflight_receipt"]["status"] = "failed"
    document["sessions"]["judge"] = None
    assert list(_validator("skill-run-invocation-v1").iter_errors(document)) == []


@pytest.mark.parametrize("runtime_role", ["producer", "judge"])
def test_deepseek_v4_flash_limits_are_independently_driven_by_each_runtime_request(runtime_role: str) -> None:
    document = _document(FIXTURE_DIR / "positive" / "skill-run-invocation-v1.json")
    document["runtime"][runtime_role]["requested_max_output_tokens"] = 4097
    paths = [list(error.absolute_path) for error in _validator("skill-run-invocation-v1").iter_errors(document)]
    assert ["runtime", runtime_role, "requested_max_output_tokens"] in paths


@pytest.mark.parametrize("runtime_role", ["producer", "judge"])
def test_non_deepseek_runtime_does_not_relax_the_other_role_deepseek_limit(runtime_role: str) -> None:
    document = _document(FIXTURE_DIR / "positive" / "skill-run-invocation-v1.json")
    runtime = document["runtime"][runtime_role]
    runtime["requested_model"] = "anthropic/claude-sonnet"
    runtime["declared_context_window_tokens"] = 100000
    runtime["requested_max_output_tokens"] = 8192
    assert list(_validator("skill-run-invocation-v1").iter_errors(document)) == []


def test_review_evidence_requires_timestamp_and_actionable_structured_findings() -> None:
    document = _document(FIXTURE_DIR / "positive" / "skill-review-v1.json")
    evidence = document["review_evidence"]
    assert evidence["reviewed_at"]
    finding = evidence["findings"][0]
    assert finding["locations"] == [{"path": "scripts/run_guard.py", "field": "write_scope"}]
    assert finding["reason"]
    assert finding["impact"]
    assert finding["recommendation"]


@pytest.mark.parametrize(
    ("schema_name", "collection"),
    [
        ("codetalk-skill-v1", "inputs"),
        ("codetalk-skill-v1", "steps"),
        ("codetalk-skill-v1", "artifacts"),
        ("codetalk-skill-v1", "deliveries"),
        ("codetalk-skill-v1", "core_rules"),
        ("skill-ir-v1", "inputs"),
        ("skill-ir-v1", "steps"),
        ("skill-ir-v1", "artifacts"),
        ("skill-ir-v1", "deliveries"),
        ("skill-ir-v1", "core_rules"),
        ("skill-ir-v1", "topological_order"),
        ("skill-ir-v1", "source_file_digests"),
    ],
)
def test_skill_and_terminal_ir_require_nonempty_execution_groups(schema_name: str, collection: str) -> None:
    document = _document(FIXTURE_DIR / "positive" / f"{schema_name}.json")
    document[collection] = []
    paths = [list(error.absolute_path) for error in _validator(schema_name).iter_errors(document)]
    assert [collection] in paths
