"""One process-local registry for CodeTalk-managed workflow tools.

The registry starts empty by design.  A Tool node is executable only after the
deployment explicitly registers a local :class:`ToolDefinition`; this module is
the single composition root for the dispatcher, its permissions, and the
capability metadata exposed to workflow authoring.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

from app.services.tool_dispatch import (
    ToolDefinition,
    ToolDispatcher,
    _schema_definition_problem,
)


# Deployments must explicitly populate this local registry during bootstrap.
# Keeping the default empty prevents the palette from advertising an execution
# surface that has no CodeTalk-managed implementation behind it.
MANAGED_TOOL_REGISTRY: dict[str, ToolDefinition] = {}

_MANIFEST_SUFFIX = ".json"
_MAX_MANIFEST_BYTES = 64 * 1024
_MANIFEST_FIELDS = frozenset({
    "tool_id",
    "implementation",
    "input_schema",
    "required_permissions",
})


class ManagedToolManifestError(ValueError):
    """A deployment manifest cannot safely produce a managed Tool definition."""


def _json_echo(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return a detached copy of the call arguments for local contract testing."""
    return deepcopy(arguments)


# Manifest files select only these process-local implementations.  In particular,
# an implementation value never becomes a command, import path, URL, or code.
BUILTIN_TOOL_IMPLEMENTATIONS: Mapping[str, Callable[[dict[str, Any]], Any]] = (
    MappingProxyType({"json_echo": _json_echo})
)


@dataclass(frozen=True)
class ManagedToolRuntime:
    """Immutable dispatcher composition for one workflow execution boundary."""

    dispatcher: ToolDispatcher
    granted_permissions: tuple[str, ...]
    tools_by_id: dict[str, ToolDefinition]

    @property
    def available(self) -> bool:
        return bool(self.tools_by_id)

    def handler_capability(self) -> dict[str, Any] | None:
        if not self.available:
            return None
        return {
            "versions": [1],
            "kind": "tool",
            "tool_ids": sorted(self.tools_by_id),
        }

    def validate_frozen_plan_nodes(
        self, nodes: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Fail closed before any node starts for unknown tools or permissions."""
        errors: list[dict[str, Any]] = []
        for node in nodes:
            if str(node.get("kind") or "") != "tool":
                continue
            node_id = str(node.get("node_id") or "")
            tool_id = str(node.get("tool_id") or "").strip()
            definition = self.tools_by_id.get(tool_id)
            if definition is None:
                errors.append({
                    "node_id": node_id,
                    "code": "tool_not_registered",
                    "message": "Frozen workflow references an unmanaged tool.",
                    "tool_id": tool_id,
                })
                continue
            requested_permissions = sorted({
                str(permission)
                for permission in node.get("required_permissions") or []
                if str(permission)
            })
            expected_permissions = sorted({
                str(permission) for permission in definition.required_permissions
            })
            if requested_permissions != expected_permissions:
                errors.append({
                    "node_id": node_id,
                    "code": "tool_permissions_invalid",
                    "message": (
                        "Frozen workflow tool permissions do not match the managed contract."
                    ),
                    "tool_id": tool_id,
                    "expected_permissions": expected_permissions,
                    "requested_permissions": requested_permissions,
                })
        return errors


def managed_tool_runtime(
    *, manifest_dir: Path | str | None = None
) -> ManagedToolRuntime:
    """Build a detached dispatcher from the registry or one manifest directory."""
    if manifest_dir is None:
        from app.config import settings

        configured_directory = settings.workflow_managed_tool_manifest_dir.strip()
        if configured_directory:
            manifest_dir = configured_directory
    tools_by_id = (
        _load_manifest_directory(manifest_dir)
        if manifest_dir is not None
        else _registry_tools()
    )
    granted_permissions = tuple(sorted({
        str(permission)
        for definition in tools_by_id.values()
        for permission in definition.required_permissions
    }))
    return ManagedToolRuntime(
        dispatcher=ToolDispatcher(tools_by_id.values()),
        granted_permissions=granted_permissions,
        tools_by_id=tools_by_id,
    )


def _registry_tools() -> dict[str, ToolDefinition]:
    tools_by_id: dict[str, ToolDefinition] = {}
    for registry_id, definition in MANAGED_TOOL_REGISTRY.items():
        if not isinstance(definition, ToolDefinition):
            raise TypeError("managed tool registry values must be ToolDefinition instances")
        tool_id = str(definition.tool_id).strip()
        if not tool_id or tool_id != str(registry_id).strip():
            raise ValueError("managed tool registry key must match ToolDefinition.tool_id")
        if tool_id in tools_by_id:
            raise ValueError(f"duplicate managed tool id: {tool_id}")
        tools_by_id[tool_id] = definition
    return tools_by_id


def _load_manifest_directory(manifest_dir: Path | str) -> dict[str, ToolDefinition]:
    try:
        directory = Path(manifest_dir).resolve(strict=True)
    except OSError as exc:
        raise ManagedToolManifestError("managed tool manifest directory is unavailable") from exc
    if not directory.is_dir():
        raise ManagedToolManifestError("managed tool manifest path must be a directory")

    try:
        manifest_paths = sorted(
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() == _MANIFEST_SUFFIX
        )
    except OSError as exc:
        raise ManagedToolManifestError("managed tool manifest directory cannot be read") from exc

    tools_by_id: dict[str, ToolDefinition] = {}
    for path in manifest_paths:
        if path.is_symlink():
            raise ManagedToolManifestError(f"managed tool manifest must not be a symlink: {path.name}")
        try:
            if path.stat().st_size > _MAX_MANIFEST_BYTES:
                raise ManagedToolManifestError(
                    f"managed tool manifest exceeds {_MAX_MANIFEST_BYTES} bytes: {path.name}"
                )
            payload = json.loads(path.read_text(encoding="utf-8"))
        except ManagedToolManifestError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManagedToolManifestError(
                f"managed tool manifest is not valid UTF-8 JSON: {path.name}"
            ) from exc
        definition = _definition_from_manifest(payload, source_name=path.name)
        if definition.tool_id in tools_by_id:
            raise ManagedToolManifestError(
                f"duplicate managed tool id: {definition.tool_id}"
            )
        tools_by_id[definition.tool_id] = definition
    return tools_by_id


def _definition_from_manifest(payload: Any, *, source_name: str) -> ToolDefinition:
    if not isinstance(payload, dict):
        raise ManagedToolManifestError(f"managed tool manifest must be an object: {source_name}")
    unknown_fields = sorted(set(payload) - _MANIFEST_FIELDS)
    missing_fields = sorted(_MANIFEST_FIELDS - set(payload))
    if unknown_fields or missing_fields:
        raise ManagedToolManifestError(
            f"managed tool manifest fields are invalid: {source_name}"
        )

    tool_id = _manifest_text(payload["tool_id"], field="tool_id", source_name=source_name)
    implementation = _manifest_text(
        payload["implementation"], field="implementation", source_name=source_name
    )
    handler = BUILTIN_TOOL_IMPLEMENTATIONS.get(implementation)
    if handler is None:
        raise ManagedToolManifestError(
            f"managed tool implementation is not allowed: {implementation}"
        )

    input_schema = payload["input_schema"]
    schema_problem = _schema_definition_problem(input_schema)
    if schema_problem or not isinstance(input_schema, dict) or input_schema.get("type") != "object":
        raise ManagedToolManifestError(
            f"managed tool input schema must be a valid object schema: {source_name}"
        )
    permissions = _manifest_permissions(
        payload["required_permissions"], source_name=source_name
    )
    return ToolDefinition(
        tool_id=tool_id,
        input_schema=deepcopy(input_schema),
        required_permissions=permissions,
        handler=handler,
    )


def _manifest_text(value: Any, *, field: str, source_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ManagedToolManifestError(
            f"managed tool {field} must be a non-empty trimmed string: {source_name}"
        )
    return value


def _manifest_permissions(value: Any, *, source_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ManagedToolManifestError(
            f"managed tool required_permissions must be an array: {source_name}"
        )
    permissions = tuple(
        _manifest_text(permission, field="required_permissions", source_name=source_name)
        for permission in value
    )
    if len(set(permissions)) != len(permissions):
        raise ManagedToolManifestError(
            f"managed tool required_permissions contains duplicates: {source_name}"
        )
    return permissions
