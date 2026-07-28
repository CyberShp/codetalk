"""Deterministic JSON parsing and schema checks for declared artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .common import inspect_regular_file, result_from_issues, selected_declarations
from .contracts import ValidationIssue, ValidationResult


def validate_json_schema(
    *,
    artifact_root: Path,
    declared_outputs: Iterable[object],
    required_output_ids: Iterable[str],
    node_id: str = "",
    **_unused: object,
) -> ValidationResult:
    validator_id = "json_schema"
    selected, error = selected_declarations(
        validator_id=validator_id,
        declared_outputs=declared_outputs,
        required_output_ids=required_output_ids,
        node_id=node_id,
    )
    if error is not None:
        return error
    assert selected is not None
    issues: list[ValidationIssue] = []
    for declaration in selected:
        if declaration.schema is None:
            issues.append(_issue("json_schema_missing", "声明输出没有 JSON Schema。", declaration.output_id, declaration.artifact))
            continue
        if not isinstance(declaration.schema, dict):
            issues.append(_issue("json_schema_invalid", "JSON Schema 必须是结构化对象。", declaration.output_id, declaration.artifact))
            continue
        schema_problem = _check_schema_shape(declaration.schema, path="$")
        if schema_problem:
            issues.append(_issue("json_schema_invalid", "JSON Schema 结构无效。", declaration.output_id, declaration.artifact, {"schema_error": schema_problem}))
            continue
        path, path_issue = inspect_regular_file(
            root=artifact_root,
            relative_path=declaration.artifact,
            output_id=declaration.output_id,
            node_id=node_id,
        )
        if path_issue is not None:
            issues.append(path_issue)
            continue
        assert path is not None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            issues.append(_issue("artifact_invalid_json", "声明产物不是有效 JSON。", declaration.output_id, declaration.artifact, {"error_type": type(exc).__name__}))
            continue
        mismatches = _validate_value(value, declaration.schema, path="$")
        if mismatches:
            issues.append(_issue("json_schema_mismatch", "声明产物不符合 JSON Schema。", declaration.output_id, declaration.artifact, {"mismatches": mismatches}))
    return result_from_issues(
        validator_id=validator_id,
        issues=issues,
        validated_output_ids=tuple(item.output_id for item in selected),
    )


def _issue(code: str, message: str, output_id: str, path: str, details: dict[str, Any] | None = None) -> ValidationIssue:
    return ValidationIssue(code=code, message=message, output_id=output_id, path=path, details=dict(details or {}))


def _check_schema_shape(schema: dict[str, Any], *, path: str) -> str:
    allowed_types = {"object", "array", "string", "integer", "number", "boolean", "null"}
    schema_type = schema.get("type")
    if schema_type is not None and schema_type not in allowed_types:
        return f"{path}.type is unsupported"
    if "required" in schema and not (
        isinstance(schema["required"], list)
        and all(isinstance(item, str) for item in schema["required"])
    ):
        return f"{path}.required must be a string array"
    for keyword in ("minLength", "maxLength", "minItems", "maxItems"):
        if keyword in schema and not (
            isinstance(schema[keyword], int)
            and not isinstance(schema[keyword], bool)
            and schema[keyword] >= 0
        ):
            return f"{path}.{keyword} must be a non-negative integer"
    for keyword in ("minimum", "maximum"):
        if keyword in schema and not (
            isinstance(schema[keyword], (int, float))
            and not isinstance(schema[keyword], bool)
        ):
            return f"{path}.{keyword} must be a number"
    if "enum" in schema and not (
        isinstance(schema["enum"], list) and bool(schema["enum"])
    ):
        return f"{path}.enum must be a non-empty array"
    additional = schema.get("additionalProperties", True)
    if not isinstance(additional, (bool, dict)):
        return f"{path}.additionalProperties must be a boolean or object"
    if isinstance(additional, dict):
        problem = _check_schema_shape(
            additional, path=f"{path}.additionalProperties"
        )
        if problem:
            return problem
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return f"{path}.properties must be an object"
    for key, child in properties.items():
        if not isinstance(child, dict):
            return f"{path}.properties.{key} must be an object"
        problem = _check_schema_shape(child, path=f"{path}.properties.{key}")
        if problem:
            return problem
    if "items" in schema:
        if not isinstance(schema["items"], dict):
            return f"{path}.items must be an object"
        problem = _check_schema_shape(schema["items"], path=f"{path}.items")
        if problem:
            return problem
    return ""


def _validate_value(value: object, schema: dict[str, Any], *, path: str) -> list[str]:
    problems: list[str] = []
    expected = schema.get("type")
    if expected and not _matches_type(value, expected):
        return [f"{path}: expected {expected}"]
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}: value is not in enum")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            problems.append(f"{path}: shorter than minLength")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            problems.append(f"{path}: longer than maxLength")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            problems.append(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            problems.append(f"{path}: above maximum")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            problems.append(f"{path}: fewer than minItems")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            problems.append(f"{path}: more than maxItems")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                problems.extend(_validate_value(item, item_schema, path=f"{path}[{index}]"))
    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                problems.append(f"{path}.{key}: required property missing")
        properties = schema.get("properties", {})
        for key, item in value.items():
            child = properties.get(key)
            if isinstance(child, dict):
                problems.extend(_validate_value(item, child, path=f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                problems.append(f"{path}.{key}: additional property rejected")
            elif isinstance(schema.get("additionalProperties"), dict):
                problems.extend(
                    _validate_value(
                        item,
                        schema["additionalProperties"],
                        path=f"{path}.{key}",
                    )
                )
    return problems


def _matches_type(value: object, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }[expected]
