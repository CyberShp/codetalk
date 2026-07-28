"""Domain-neutral runtime dispatch for explicit V3 workflow handlers."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Literal

from app.services.validators import DEFAULT_VALIDATOR_REGISTRY
from app.services.validators.common import inspect_regular_file


HandlerAxis = Literal["artifact_validation", "governance"]
_MAX_GOVERNANCE_INPUT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class _GovernanceInputError(Exception):
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowHandlerRequest:
    handler_id: str
    handler_version: int
    node_id: str
    node_kind: str
    task_artifact_dir: Path
    source_root: Path
    declared_outputs: tuple[dict[str, Any], ...] = ()
    required_output_ids: tuple[str, ...] = ()
    artifact_roots_by_output_id: dict[str, Path] = field(default_factory=dict)
    inputs: dict[str, Any] = field(default_factory=dict)
    blocking: bool = True


@dataclass(frozen=True)
class WorkflowHandlerResult:
    handler_id: str
    handler_version: int
    node_id: str
    node_kind: str
    axis: HandlerAxis
    status: Literal["passed", "failed"]
    governance_status: str = ""
    error_code: str = ""
    message: str = ""
    issues: tuple[dict[str, Any], ...] = ()
    validated_output_ids: tuple[str, ...] = ()
    produced_output_ids: tuple[str, ...] = ()
    artifact_dir: str = ""
    provider_failed: bool = False
    details: dict[str, Any] = field(default_factory=dict)


class WorkflowHandlerDispatcher:
    """Dispatch explicit handlers while keeping professional imports lazy."""

    def __init__(
        self,
        *,
        governance_registry_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._governance_registry_factory = governance_registry_factory
        self._governance_registry: Any = None

    def dispatch(self, request: WorkflowHandlerRequest) -> WorkflowHandlerResult:
        if (
            request.node_kind == "validator"
            and request.handler_id in DEFAULT_VALIDATOR_REGISTRY.ids()
        ):
            return self._dispatch_validator(request)
        return self._dispatch_governance(request)

    def _dispatch_validator(
        self,
        request: WorkflowHandlerRequest,
    ) -> WorkflowHandlerResult:
        if request.handler_version != 1:
            return self._failure(
                request,
                axis="artifact_validation",
                code="workflow_handler_version_unavailable",
                message="Validator handler version is not available.",
            )
        declarations = {
            str(item.get("output_id") or ""): item
            for item in request.declared_outputs
            if str(item.get("output_id") or "")
        }
        required = tuple(dict.fromkeys(request.required_output_ids))
        undeclared = [output_id for output_id in required if output_id not in declarations]
        if undeclared:
            return self._failure(
                request,
                axis="artifact_validation",
                code="undeclared_required_output",
                message="Validator requires an undeclared workflow output.",
                details={"undeclared_output_ids": sorted(undeclared)},
            )

        results = []
        selected_ids = required or ("",)
        for output_id in selected_ids:
            selected = [declarations[output_id]] if output_id else []
            artifact_root = request.artifact_roots_by_output_id.get(
                output_id,
                request.task_artifact_dir,
            )
            try:
                result = DEFAULT_VALIDATOR_REGISTRY.run(
                    request.handler_id,
                    artifact_root=artifact_root,
                    source_root=request.source_root,
                    declared_outputs=selected,
                    required_output_ids=[output_id] if output_id else [],
                    node_id=request.node_id,
                )
            except Exception as exc:
                return self._failure(
                    request,
                    axis="artifact_validation",
                    code="validator_handler_failed",
                    message=f"Validator handler failed: {type(exc).__name__}",
                )
            results.append(result)

        issues = tuple(asdict(issue) for result in results for issue in result.issues)
        status = (
            "failed"
            if issues or any(result.status == "failed" for result in results)
            else "passed"
        )
        return WorkflowHandlerResult(
            handler_id=request.handler_id,
            handler_version=request.handler_version,
            node_id=request.node_id,
            node_kind=request.node_kind,
            axis="artifact_validation",
            status=status,
            error_code=(issues[0]["code"] if issues else ""),
            issues=issues,
            validated_output_ids=required,
            details={
                "validator_results": [asdict(result) for result in results],
            },
        )

    def _dispatch_governance(
        self,
        request: WorkflowHandlerRequest,
    ) -> WorkflowHandlerResult:
        registry = self._get_governance_registry()
        descriptor = next(
            (
                item
                for item in registry.availability_snapshot()
                if item.get("handler_id") == request.handler_id
            ),
            None,
        )
        if not descriptor or request.handler_version != descriptor.get("handler_version"):
            return self._failure(
                request,
                axis="governance",
                code="governance_handler_unavailable",
                message="Governance handler or version is not available.",
                governance_status="failed" if request.blocking else "warning",
            )

        from app.services.governance_plugins.contracts import (
            DeclaredGovernanceOutput,
            GovernanceOutputEdge,
            GovernancePluginRequest,
        )

        declarations = tuple(
            DeclaredGovernanceOutput(
                artifact_id=str(item.get("output_id") or ""),
                path=str(item.get("artifact") or ""),
                producer_node_id=str(item.get("producer_step_id") or ""),
                producer_port_id=str(item.get("producer_port_id") or ""),
                producer_port_key=str(item.get("producer_port_key") or ""),
            )
            for item in request.declared_outputs
            if str(item.get("output_id") or "")
        )
        edges = tuple(
            GovernanceOutputEdge(
                edge_id=f"declared-output:{output.artifact_id}",
                source_node_id=output.producer_node_id,
                source_port_id=output.producer_port_id,
                target_artifact_id=output.artifact_id,
            )
            for output in declarations
            if output.producer_node_id and output.producer_port_id
        )
        request.task_artifact_dir.mkdir(parents=True, exist_ok=True)
        try:
            hydrated_inputs = _hydrate_governance_inputs(request.inputs)
        except _GovernanceInputError as exc:
            return self._failure(
                request,
                axis="governance",
                code=exc.code,
                message=exc.message,
                governance_status="failed" if request.blocking else "warning",
                details=exc.details,
            )
        with tempfile.TemporaryDirectory(
            prefix=".workflow-handler-",
            dir=request.task_artifact_dir,
        ) as workspace_name:
            workspace = Path(workspace_name)
            if request.node_kind == "validator":
                self._stage_governance_inputs(
                    workspace=workspace,
                    declarations=declarations,
                    required_output_ids=request.required_output_ids,
                    artifact_roots=request.artifact_roots_by_output_id,
                    node_id=request.node_id,
                )
            plugin_request = GovernancePluginRequest(
                handler_id=request.handler_id,
                node_id=request.node_id,
                node_kind=request.node_kind,
                artifact_dir=str(workspace),
                inputs={
                    "repo_path": str(request.source_root),
                    **hydrated_inputs,
                },
                required_output_ids=(
                    request.required_output_ids
                    if request.node_kind == "validator"
                    else ()
                ),
                requested_output_ids=(
                    request.required_output_ids
                    if request.node_kind == "governance"
                    else ()
                ),
                declared_outputs=declarations,
                output_edges=edges,
                blocking=request.blocking,
            )
            plugin_result = registry.invoke(plugin_request)

        if plugin_result.governance_status in {"failed", "warning"}:
            issues = tuple(
                asdict(issue)
                for issue in (plugin_result.validation.issues if plugin_result.validation else ())
            )
            return WorkflowHandlerResult(
                handler_id=request.handler_id,
                handler_version=request.handler_version,
                node_id=request.node_id,
                node_kind=request.node_kind,
                axis="governance",
                status="failed",
                governance_status=plugin_result.governance_status,
                error_code=plugin_result.error_code,
                message=plugin_result.message,
                issues=issues,
            )

        artifact_dir = ""
        produced_ids: tuple[str, ...] = ()
        if request.node_kind == "governance":
            materialized, error = self._materialize_governance_outputs(
                request=request,
                produced_artifacts=plugin_result.produced_artifacts,
            )
            if error is not None:
                return error
            artifact_dir, produced_ids = materialized
        return WorkflowHandlerResult(
            handler_id=request.handler_id,
            handler_version=request.handler_version,
            node_id=request.node_id,
            node_kind=request.node_kind,
            axis="governance",
            status="passed",
            governance_status="passed",
            validated_output_ids=(
                request.required_output_ids
                if request.node_kind == "validator"
                else ()
            ),
            produced_output_ids=produced_ids,
            artifact_dir=artifact_dir,
        )

    def _get_governance_registry(self) -> Any:
        if self._governance_registry is None:
            factory = self._governance_registry_factory
            if factory is None:
                from app.services.governance_plugins.registry import (
                    create_governance_plugin_registry,
                )

                factory = create_governance_plugin_registry
            self._governance_registry = factory()
        return self._governance_registry

    @staticmethod
    def _stage_governance_inputs(
        *,
        workspace: Path,
        declarations: tuple[Any, ...],
        required_output_ids: tuple[str, ...],
        artifact_roots: dict[str, Path],
        node_id: str,
    ) -> None:
        declared = {item.artifact_id: item for item in declarations}
        for output_id in required_output_ids:
            declaration = declared.get(output_id)
            root = artifact_roots.get(output_id)
            if declaration is None or root is None:
                continue
            source, issue = inspect_regular_file(
                root=root,
                relative_path=declaration.path,
                output_id=output_id,
                node_id=node_id,
            )
            if issue is not None or source is None:
                continue
            relative = _safe_relative_path(declaration.path)
            if relative is None:
                continue
            destination = workspace.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination, follow_symlinks=False)

    def _materialize_governance_outputs(
        self,
        *,
        request: WorkflowHandlerRequest,
        produced_artifacts: tuple[Any, ...],
    ) -> tuple[tuple[str, tuple[str, ...]], WorkflowHandlerResult | None]:
        declared = {
            str(item.get("output_id") or ""): item
            for item in request.declared_outputs
            if str(item.get("output_id") or "")
        }
        prepared: list[tuple[str, PurePosixPath, str]] = []
        for candidate in produced_artifacts:
            output_id = str(candidate.artifact_id)
            declaration = declared.get(output_id)
            relative = _safe_relative_path(
                str((declaration or {}).get("artifact") or "")
            )
            if declaration is None or output_id not in request.required_output_ids:
                return ("", ()), self._failure(
                    request,
                    axis="governance",
                    code="undeclared_governance_output",
                    message="Governance handler returned an undeclared output.",
                    governance_status="failed" if request.blocking else "warning",
                )
            if relative is None:
                return ("", ()), self._failure(
                    request,
                    axis="governance",
                    code="unsafe_governance_output_path",
                    message="Governance output path is unsafe.",
                    governance_status="failed" if request.blocking else "warning",
                )
            if not isinstance(candidate.content, str):
                return ("", ()), self._failure(
                    request,
                    axis="governance",
                    code="invalid_governance_output_content",
                    message="Governance output content must be text.",
                    governance_status="failed" if request.blocking else "warning",
                )
            declared_media_type = _normalized_media_type(
                str((declaration or {}).get("media_type") or "")
            )
            candidate_media_type = _normalized_media_type(
                str(candidate.media_type or "")
            )
            if (
                declared_media_type
                and candidate_media_type != declared_media_type
            ):
                return ("", ()), self._failure(
                    request,
                    axis="governance",
                    code="governance_output_media_type_mismatch",
                    message=(
                        "Governance output media type does not match the "
                        "declared workflow output."
                    ),
                    governance_status="failed" if request.blocking else "warning",
                    details={
                        "output_id": output_id,
                        "declared_media_type": declared_media_type,
                        "candidate_media_type": candidate_media_type,
                    },
                )
            prepared.append((output_id, relative, candidate.content))

        node_directory = _safe_node_directory(request.node_id)
        if node_directory is None:
            return ("", ()), self._failure(
                request,
                axis="governance",
                code="unsafe_governance_node_id",
                message="Governance node ID cannot address an output directory.",
                governance_status="failed" if request.blocking else "warning",
            )
        runs_root = request.task_artifact_dir / "governance_runs"
        if runs_root.is_symlink():
            return ("", ()), self._failure(
                request,
                axis="governance",
                code="unsafe_governance_output_path",
                message="Governance output root cannot be a symlink.",
                governance_status="failed" if request.blocking else "warning",
            )
        runs_root.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{node_directory}-", dir=runs_root))
        final = runs_root / node_directory
        try:
            for _output_id, relative, content in prepared:
                destination = temporary.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(content, encoding="utf-8")
            if final.is_symlink():
                return ("", ()), self._failure(
                    request,
                    axis="governance",
                    code="unsafe_governance_output_path",
                    message="Governance output directory cannot be a symlink.",
                    governance_status="failed" if request.blocking else "warning",
                )
            if final.exists():
                if not final.is_dir():
                    return ("", ()), self._failure(
                        request,
                        axis="governance",
                        code="unsafe_governance_output_path",
                        message="Governance output directory is not a directory.",
                        governance_status="failed" if request.blocking else "warning",
                    )
                shutil.rmtree(final)
            os.replace(temporary, final)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return (
            str(final),
            tuple(output_id for output_id, _relative, _content in prepared),
        ), None

    @staticmethod
    def _failure(
        request: WorkflowHandlerRequest,
        *,
        axis: HandlerAxis,
        code: str,
        message: str,
        governance_status: str = "",
        details: dict[str, Any] | None = None,
    ) -> WorkflowHandlerResult:
        return WorkflowHandlerResult(
            handler_id=request.handler_id,
            handler_version=request.handler_version,
            node_id=request.node_id,
            node_kind=request.node_kind,
            axis=axis,
            status="failed",
            governance_status=governance_status,
            error_code=code,
            message=message,
            details=dict(details or {}),
        )


def _safe_relative_path(raw_path: str) -> PurePosixPath | None:
    normalized = str(raw_path or "").replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or bool(PureWindowsPath(normalized).drive)
        or ".." in path.parts
        or not path.parts
        or normalized.endswith("/")
    ):
        return None
    return path


def _safe_node_directory(node_id: str) -> str | None:
    value = str(node_id or "")
    if not value or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in value):
        return None
    if value in {".", ".."}:
        return None
    return value


def _normalized_media_type(value: str) -> str:
    return str(value or "").split(";", 1)[0].strip().lower()


def _hydrate_governance_inputs(value: Any) -> Any:
    if isinstance(value, list):
        return [_hydrate_governance_inputs(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_hydrate_governance_inputs(item) for item in value)
    if not isinstance(value, dict):
        return value
    if value.get("__workflow_artifact_ref__") is True:
        return _read_governance_artifact_reference(value)
    return {
        str(key): _hydrate_governance_inputs(item)
        for key, item in value.items()
    }


def _read_governance_artifact_reference(reference: dict[str, Any]) -> Any:
    output_id = str(reference.get("output_id") or "")
    artifact = str(reference.get("artifact") or "")
    root = Path(str(reference.get("artifact_root") or ""))
    media_type = str(
        reference.get("media_type") or "application/octet-stream"
    ).split(";", 1)[0].strip().lower()
    source, issue = inspect_regular_file(
        root=root,
        relative_path=artifact,
        output_id=output_id,
        code_prefix="governance_input",
    )
    if issue is not None or source is None:
        raise _GovernanceInputError(
            code=(issue.code if issue is not None else "governance_input_unreadable"),
            message=(
                issue.message
                if issue is not None
                else "The bound workflow artifact cannot be read."
            ),
            details={
                "output_id": output_id,
                "artifact": artifact,
                **(issue.details if issue is not None else {}),
            },
        )
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise _GovernanceInputError(
            code="governance_input_unreadable",
            message="The bound workflow artifact cannot be inspected.",
            details={"output_id": output_id, "error_type": type(exc).__name__},
        ) from exc
    if size > _MAX_GOVERNANCE_INPUT_BYTES:
        raise _GovernanceInputError(
            code="governance_input_too_large",
            message="The bound workflow artifact exceeds the input size limit.",
            details={
                "output_id": output_id,
                "size_bytes": size,
                "max_bytes": _MAX_GOVERNANCE_INPUT_BYTES,
            },
        )
    if media_type == "application/json" or media_type.endswith("+json"):
        try:
            return json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _GovernanceInputError(
                code="governance_input_json_invalid",
                message="The bound JSON workflow artifact is invalid.",
                details={
                    "output_id": output_id,
                    "error_type": type(exc).__name__,
                },
            ) from exc
    if media_type.startswith("text/"):
        try:
            return source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise _GovernanceInputError(
                code="governance_input_text_invalid",
                message="The bound text workflow artifact is not valid UTF-8.",
                details={
                    "output_id": output_id,
                    "error_type": type(exc).__name__,
                },
            ) from exc
    raise _GovernanceInputError(
        code="governance_input_media_type_unsupported",
        message="The bound workflow artifact media type is not supported.",
        details={"output_id": output_id, "media_type": media_type},
    )
