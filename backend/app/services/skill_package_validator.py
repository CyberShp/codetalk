"""Deterministic cross-reference validation for F014 Skill documents."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from app.services.skill_package_paths import SkillPackagePathError, validate_member_name


SCHEMA_DIR = Path(__file__).parents[1] / "schemas" / "skills"


@dataclass(frozen=True)
class SkillPackageValidationIssue:
    code: str
    path: str
    message: str


@dataclass(frozen=True)
class SkillPackageValidationResult:
    ok: bool
    issues: tuple[SkillPackageValidationIssue, ...]


class SkillPackageValidationError(ValueError):
    def __init__(self, issues: Iterable[SkillPackageValidationIssue]) -> None:
        self.issues = tuple(issues)
        message = "skill package validation failed"
        if self.issues:
            first = self.issues[0]
            message = f"{message}: {first.code} at {first.path}"
        super().__init__(message)


def _issue(code: str, path: str, message: str) -> SkillPackageValidationResult:
    return SkillPackageValidationResult(False, (SkillPackageValidationIssue(code, path, message),))


def _ok() -> SkillPackageValidationResult:
    return SkillPackageValidationResult(True, ())


def _document(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _registry() -> Registry:
    resources = [Resource.from_contents(_document(path)) for path in SCHEMA_DIR.glob("*.schema.json")]
    return Registry().with_resources((resource.id(), resource) for resource in resources)


def _schema_validator(name: str) -> Draft202012Validator:
    schema = _document(SCHEMA_DIR / f"{name}.schema.json")
    return Draft202012Validator(schema, registry=_registry(), format_checker=FormatChecker())


def _canonical_path_key(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def _index_by_id(items: list[dict[str, Any]], key: str, field_path: str) -> tuple[dict[str, int], SkillPackageValidationResult | None]:
    seen: dict[str, int] = {}
    for index, item in enumerate(items):
        item_id = item[key]
        if item_id in seen:
            return seen, _issue("duplicate_id", f"{field_path}[{index}].{key}", f"duplicate id {item_id!r}")
        seen[item_id] = index
    return seen, None


def _topological_order(document: dict[str, Any]) -> list[str] | None:
    step_ids = [step["step_id"] for step in document["steps"]]
    dependencies = {step["step_id"]: list(step["depends_on"]) for step in document["steps"]}
    ordered: list[str] = []
    temporary: set[str] = set()
    permanent: set[str] = set()

    def visit(step_id: str) -> bool:
        if step_id in permanent:
            return True
        if step_id in temporary:
            return False
        temporary.add(step_id)
        for dependency in dependencies[step_id]:
            if not visit(dependency):
                return False
        temporary.remove(step_id)
        permanent.add(step_id)
        ordered.append(step_id)
        return True

    for step_id in step_ids:
        if not visit(step_id):
            return None
    return ordered


def _safe_path(path: str) -> bool:
    try:
        validate_member_name(path)
    except SkillPackagePathError:
        return False
    return True


def _safe_glob_path(path: str) -> bool:
    try:
        validate_member_name(path.replace("*", "STAR"))
    except SkillPackagePathError:
        return False
    return True


def _schema_error_sort_key(error: Any) -> tuple[int, list[Any]]:
    priority = {
        "schema_version": 0,
        "skill_id": 1,
        "name": 2,
        "required_agent_capabilities": 3,
        "inputs": 4,
        "steps": 5,
        "artifacts": 6,
        "deliveries": 7,
        "scripts": 8,
        "core_rules": 9,
        "judge": 10,
    }
    path = list(error.absolute_path)
    return (priority.get(path[0], 99) if path else 99, path)


def _referenced_source_paths(document: dict[str, Any]) -> list[tuple[str, str]]:
    paths: list[tuple[str, str]] = []
    for index, step in enumerate(document["steps"]):
        paths.append((f"steps[{index}].instruction_path", step["instruction_path"]))
    for index, script in enumerate(document["scripts"]):
        paths.append((f"scripts[{index}].path", script["path"]))
        paths.append((f"scripts[{index}].working_directory", script["working_directory"]))
        for scope_index, scope in enumerate(script["write_scope"]):
            paths.append((f"scripts[{index}].write_scope[{scope_index}]", scope))
    for index, rule in enumerate(document["core_rules"]):
        paths.append((f"core_rules[{index}].instruction_path", rule["instruction_path"]))
    return paths


def _path_fields(document: dict[str, Any]) -> list[tuple[str, str]]:
    paths = _referenced_source_paths(document)
    if document.get("selected_workflow_path"):
        paths.append(("selected_workflow_path", document["selected_workflow_path"]))
    for index, artifact in enumerate(document["artifacts"]):
        paths.append((f"artifacts[{index}].path", artifact["path"]))
    return paths


def _glob_path_fields(document: dict[str, Any]) -> list[tuple[str, str]]:
    paths: list[tuple[str, str]] = []
    for step_index, step in enumerate(document["steps"]):
        for glob_index, glob_path in enumerate(step["completion_gate"].get("requires_glob", [])):
            paths.append((f"steps[{step_index}].completion_gate.requires_glob[{glob_index}]", glob_path))
    return paths


def _has_symlink_component(root: Path, relative_path: str) -> bool:
    current = root
    for segment in Path(relative_path).parts:
        current = current / segment
        if current.is_symlink():
            return True
    return False


def _source_file_issue(root: Path, relative_path: str, path_field: str) -> SkillPackageValidationResult | None:
    if not _safe_path(relative_path):
        return _issue("unsafe_path", path_field, f"unsafe path {relative_path!r}")
    if _has_symlink_component(root, relative_path):
        return _issue("unsafe_path", path_field, f"source path uses a symlink: {relative_path!r}")
    candidate = root / relative_path
    try:
        candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (FileNotFoundError, ValueError):
        if candidate.exists():
            return _issue("unsafe_path", path_field, f"source path escapes root: {relative_path!r}")
    if not candidate.is_file():
        return _issue("missing_source_file", path_field, relative_path)
    return None


def _path_segments(path: str) -> tuple[str, ...]:
    return tuple(_canonical_path_key(segment) for segment in path.split("/"))


def _artifact_path_issue(document: dict[str, Any]) -> SkillPackageValidationResult | None:
    seen: dict[str, int] = {}
    segments_by_index: list[tuple[str, ...]] = []
    for index, artifact in enumerate(document["artifacts"]):
        path = artifact["path"]
        key = _canonical_path_key(path)
        if key in seen:
            return _issue("duplicate_artifact_path", f"artifacts[{index}].path", path)
        seen[key] = index
        segments = _path_segments(path)
        for existing_index, existing_segments in enumerate(segments_by_index):
            shorter, longer = sorted((existing_segments, segments), key=len)
            if len(shorter) < len(longer) and longer[: len(shorter)] == shorter:
                return _issue("artifact_path_prefix_conflict", f"artifacts[{index}].path", path)
        segments_by_index.append(segments)
    return None


def validate_skill_document(
    document: dict[str, Any],
    *,
    source_root: str | Path | None = None,
    source_path: str | Path | None = None,
) -> SkillPackageValidationResult:
    """Return a stable single-root-cause validation result for a Skill V1 document."""

    schema_errors = sorted(_schema_validator("codetalk-skill-v1").iter_errors(document), key=_schema_error_sort_key)
    if schema_errors:
        error = schema_errors[0]
        path = ".".join(str(segment) for segment in error.absolute_path) or "$"
        return _issue("schema_error", path, error.message)

    indexes: dict[str, dict[str, int]] = {}
    for collection, key in (
        ("inputs", "input_id"),
        ("steps", "step_id"),
        ("artifacts", "artifact_id"),
        ("deliveries", "delivery_id"),
        ("scripts", "script_id"),
        ("core_rules", "rule_id"),
    ):
        index, result = _index_by_id(document[collection], key, collection)
        if result is not None:
            return result
        indexes[collection] = index

    canonical_paths: dict[str, str] = {}
    for path_field, path in _path_fields(document):
        if path == "." and path_field.endswith(".working_directory"):
            pass
        elif not _safe_path(path):
            return _issue("unsafe_path", path_field, f"unsafe path {path!r}")
        key = _canonical_path_key(path)
        existing = canonical_paths.get(key)
        if existing is not None and existing != path:
            return _issue("canonical_path_collision", path_field, f"path aliases {existing!r}")
        canonical_paths[key] = path

    for path_field, path in _glob_path_fields(document):
        if not _safe_glob_path(path):
            return _issue("unsafe_path", path_field, f"unsafe glob path {path!r}")

    artifact_path_issue = _artifact_path_issue(document)
    if artifact_path_issue is not None:
        return artifact_path_issue

    step_ids = indexes["steps"]
    artifact_ids = indexes["artifacts"]
    script_ids = indexes["scripts"]
    artifacts_by_id = {artifact["artifact_id"]: artifact for artifact in document["artifacts"]}
    step_index_by_id = indexes["steps"]
    produced_by: dict[str, str] = {}

    for step_index, step in enumerate(document["steps"]):
        for dependency_index, dependency in enumerate(step["depends_on"]):
            if dependency not in step_ids:
                return _issue("unknown_step_dependency", f"steps[{step_index}].depends_on[{dependency_index}]", dependency)
        if step.get("script_id") and step["script_id"] not in script_ids:
            return _issue("unknown_script", f"steps[{step_index}].script_id", step["script_id"])
        produced = set(step["produces"])
        for artifact_index, artifact_id in enumerate(step["produces"]):
            if artifact_id not in artifact_ids:
                return _issue("unknown_produced_artifact", f"steps[{step_index}].produces[{artifact_index}]", artifact_id)
            existing_producer = produced_by.get(artifact_id)
            if existing_producer is not None and existing_producer != step["step_id"]:
                return _issue("multiple_artifact_producers", f"steps[{step_index}].produces[{artifact_index}]", artifact_id)
            produced_by[artifact_id] = step["step_id"]
        for artifact_index, artifact_id in enumerate(step["completion_gate"]["required_artifact_ids"]):
            if artifact_id not in artifact_ids:
                return _issue("unknown_completion_artifact", f"steps[{step_index}].completion_gate.required_artifact_ids[{artifact_index}]", artifact_id)
            if artifact_id not in produced:
                return _issue("completion_gate_not_produced", f"steps[{step_index}].completion_gate.required_artifact_ids[{artifact_index}]", artifact_id)
            if not artifacts_by_id[artifact_id]["required"]:
                return _issue("optional_artifact_in_gate", f"steps[{step_index}].completion_gate.required_artifact_ids[{artifact_index}]", artifact_id)

    if _topological_order(document) is None:
        return _issue("dependency_cycle", "steps", "step dependency graph contains a cycle")

    step_produces = {step["step_id"]: set(step["produces"]) for step in document["steps"]}
    for artifact_index, artifact in enumerate(document["artifacts"]):
        producer = artifact["producer_step_id"]
        artifact_id = artifact["artifact_id"]
        if producer not in step_ids:
            return _issue("unknown_artifact_producer", f"artifacts[{artifact_index}].producer_step_id", producer)
        if artifact_id not in step_produces[producer]:
            return _issue("undeclared_artifact_producer", f"artifacts[{artifact_index}].producer_step_id", producer)
        producer_step = document["steps"][step_index_by_id[producer]]
        if artifact["required"] and artifact_id not in producer_step["completion_gate"]["required_artifact_ids"]:
            return _issue("required_artifact_missing_from_gate", f"artifacts[{artifact_index}].artifact_id", artifact_id)

    delivered_artifacts: set[str] = set()
    for delivery_index, delivery in enumerate(document["deliveries"]):
        for artifact_index, artifact_id in enumerate(delivery["artifact_ids"]):
            if artifact_id not in artifact_ids:
                return _issue("unknown_delivery_artifact", f"deliveries[{delivery_index}].artifact_ids[{artifact_index}]", artifact_id)
            if artifacts_by_id[artifact_id]["visibility"] != "delivery":
                return _issue("delivery_artifact_not_visible", f"deliveries[{delivery_index}].artifact_ids[{artifact_index}]", artifact_id)
            delivered_artifacts.add(artifact_id)

    for artifact_index, artifact in enumerate(document["artifacts"]):
        if artifact["visibility"] == "delivery" and artifact["artifact_id"] not in delivered_artifacts:
            return _issue("unconsumed_delivery_artifact", f"artifacts[{artifact_index}].artifact_id", artifact["artifact_id"])

    for script_index, script in enumerate(document["scripts"]):
        for artifact_index, artifact_id in enumerate(script["log_artifact_ids"]):
            if artifact_id not in artifact_ids:
                return _issue("unknown_script_log_artifact", f"scripts[{script_index}].log_artifact_ids[{artifact_index}]", artifact_id)

    for artifact_index, artifact_id in enumerate(document["judge"]["artifact_ids"]):
        if artifact_id not in artifact_ids:
            return _issue("unknown_judge_artifact", f"judge.artifact_ids[{artifact_index}]", artifact_id)

    if source_root is not None:
        root = Path(source_root)
        source_checks = [(path, value) for path, value in _referenced_source_paths(document) if ".working_directory" not in path and ".write_scope" not in path]
        if document.get("selected_workflow_path"):
            source_checks.append(("selected_workflow_path", document["selected_workflow_path"]))
        if source_path is not None:
            try:
                relative_source_path = Path(source_path).relative_to(root).as_posix()
            except ValueError:
                return _issue("unsafe_path", "source_path", f"source path is outside source root: {source_path}")
            source_checks.insert(0, ("source_path", relative_source_path))
        for path_field, relative_path in source_checks:
            source_issue = _source_file_issue(root, relative_path, path_field)
            if source_issue is not None:
                return source_issue

    return _ok()


def topological_order(document: dict[str, Any]) -> list[str]:
    order = _topological_order(document)
    if order is None:
        raise SkillPackageValidationError((_issue("dependency_cycle", "steps", "step dependency graph contains a cycle").issues[0],))
    return order
