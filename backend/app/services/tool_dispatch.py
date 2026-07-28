"""Controlled, local Tool Call dispatch for Harness orchestration."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal


ToolHandler = Callable[[dict[str, Any]], Any]
_SCHEMA_TYPES = frozenset({
    "object", "array", "string", "integer", "number", "boolean", "null",
})


@dataclass(frozen=True)
class ToolDefinition:
    """A CodeTalk-managed local tool and its execution contract."""

    tool_id: str
    input_schema: dict[str, Any]
    required_permissions: tuple[str, ...] = ()
    handler: ToolHandler = field(default=lambda _arguments: None, repr=False, compare=False)


@dataclass(frozen=True)
class ToolCallRequest:
    """Provider-supplied call data; task state is intentionally not part of it."""

    tool_id: str
    arguments: dict[str, Any]
    granted_permissions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolCallError:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCallResult:
    tool_id: str
    status: Literal["completed", "failed"]
    output: Any = None
    error: ToolCallError | None = None


class ToolDispatcher:
    """Validate and run registered local tools without owning task state."""

    def __init__(self, tools: Iterable[ToolDefinition] = ()) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        for tool in tools:
            if not isinstance(tool, ToolDefinition):
                raise TypeError("tools must contain ToolDefinition instances")
            tool_id = str(tool.tool_id).strip()
            if not tool_id:
                raise ValueError("tool_id is required")
            if tool_id in self._tools:
                raise ValueError(f"duplicate tool_id: {tool_id}")
            self._tools[tool_id] = tool

    def dispatch(self, request: ToolCallRequest) -> ToolCallResult:
        """Run a call only after registry, schema, and permission checks pass."""
        tool_id = str(request.tool_id).strip()
        tool = self._tools.get(tool_id)
        if tool is None:
            return self._failure(
                tool_id,
                "tool_not_found",
                "Requested tool is not registered.",
            )

        if not isinstance(request.arguments, dict):
            return self._failure(
                tool_id,
                "invalid_arguments",
                "Tool arguments must be an object.",
                {"errors": ["$: expected object"]},
            )

        schema_error = _schema_definition_problem(tool.input_schema)
        if schema_error:
            return self._failure(
                tool_id,
                "invalid_tool_schema",
                "Registered tool input schema is invalid.",
                {"error": schema_error},
            )

        errors = _validate_schema(request.arguments, tool.input_schema)
        if errors:
            return self._failure(
                tool_id,
                "invalid_arguments",
                "Tool arguments do not match the registered schema.",
                {"errors": errors},
            )

        granted = {str(permission) for permission in request.granted_permissions}
        missing = sorted(set(tool.required_permissions) - granted)
        if missing:
            return self._failure(
                tool_id,
                "permission_denied",
                "Call lacks required tool permissions.",
                {"missing_permissions": missing},
            )

        try:
            output = tool.handler(deepcopy(request.arguments))
        except Exception as exc:
            return self._failure(
                tool_id,
                "tool_execution_failed",
                "Local tool handler failed.",
                {"error_type": type(exc).__name__},
            )
        return ToolCallResult(tool_id=tool_id, status="completed", output=output)

    @staticmethod
    def _failure(
        tool_id: str,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> ToolCallResult:
        return ToolCallResult(
            tool_id=tool_id,
            status="failed",
            error=ToolCallError(code=code, message=message, details=dict(details or {})),
        )


def _schema_definition_problem(schema: Any, *, path: str = "$") -> str:
    if not isinstance(schema, dict):
        return f"{path} must be an object"
    schema_type = schema.get("type")
    if schema_type is not None and schema_type not in _SCHEMA_TYPES:
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
    if "enum" in schema and not isinstance(schema["enum"], list):
        return f"{path}.enum must be an array"
    additional = schema.get("additionalProperties", True)
    if not isinstance(additional, (bool, dict)):
        return f"{path}.additionalProperties must be a boolean or object"
    if isinstance(additional, dict):
        problem = _schema_definition_problem(
            additional, path=f"{path}.additionalProperties"
        )
        if problem:
            return problem
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return f"{path}.properties must be an object"
    for name, child in properties.items():
        if not isinstance(child, dict):
            return f"{path}.properties.{name} must be an object"
        problem = _schema_definition_problem(child, path=f"{path}.properties.{name}")
        if problem:
            return problem
    if "items" in schema:
        if not isinstance(schema["items"], dict):
            return f"{path}.items must be an object"
        return _schema_definition_problem(schema["items"], path=f"{path}.items")
    return ""


def _validate_schema(value: Any, schema: dict[str, Any], *, path: str = "$") -> list[str]:
    expected = schema.get("type")
    if expected and not _matches_type(value, expected):
        return [f"{path}: expected {expected}"]

    errors: list[str] = []
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value is not in enum")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than minItems")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than maxItems")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(_validate_schema(item, item_schema, path=f"{path}[{index}]"))
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}.{name}: required property missing")
        properties = schema.get("properties", {})
        for name, item in value.items():
            child = properties.get(name)
            if isinstance(child, dict):
                errors.extend(_validate_schema(item, child, path=f"{path}.{name}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{name}: additional property rejected")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors.extend(
                    _validate_schema(item, schema["additionalProperties"], path=f"{path}.{name}")
                )
    return errors


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }[expected]
