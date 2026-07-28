"""Registry and contract guard for explicitly selected governance plugins."""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from app.services.governance_plugins.contracts import (
    GovernancePlugin,
    GovernancePluginDescriptor,
    GovernancePluginExecution,
    GovernancePluginRequest,
    GovernancePluginResult,
    ValidationIssue,
    ValidationResult,
)


PluginFactory = Callable[[], GovernancePlugin]


@dataclass
class _Registration:
    descriptor: GovernancePluginDescriptor
    factory: PluginFactory | None = None
    loader: str = ""
    instance: GovernancePlugin | None = None


_PROFILE_HANDLERS: dict[str, tuple[str, ...]] = {
    "none": (),
    "artifact_only": (),
    "schema": (),
    "source_evidence": (),
    "storage_test_design": ("sfmea", "black_box"),
    "formal_release": (
        "sfmea",
        "black_box",
        "independent_review",
    ),
}


class GovernancePluginRegistry:
    """Own handler metadata without importing professional modules eagerly."""

    def __init__(self) -> None:
        self._registrations: dict[str, _Registration] = {}

    def register(
        self,
        descriptor: GovernancePluginDescriptor,
        *,
        factory: PluginFactory | None = None,
        loader: str = "",
    ) -> None:
        handler_id = descriptor.handler_id.strip()
        if not handler_id:
            raise ValueError("handler_id is required")
        if handler_id in self._registrations:
            raise ValueError(f"governance handler already registered: {handler_id}")
        if (factory is None) == (not loader):
            raise ValueError("register exactly one factory or lazy loader")
        self._registrations[handler_id] = _Registration(
            descriptor=descriptor,
            factory=factory,
            loader=loader,
        )

    def availability_snapshot(self) -> list[dict[str, Any]]:
        """Return compiler-safe metadata without resolving a handler module."""
        return [
            {
                "handler_id": item.descriptor.handler_id,
                "handler_version": item.descriptor.handler_version,
                "node_kind": item.descriptor.node_kind,
                "available": self._registration_available(item),
                "capabilities": sorted(item.descriptor.capabilities),
                **(
                    {
                        "input_ports": [
                            {
                                "key": port.key,
                                "label": port.label,
                                "type": port.port_type,
                                "required": port.required,
                                "collection": port.collection,
                            }
                            for port in item.descriptor.input_ports
                        ]
                    }
                    if item.descriptor.input_ports
                    else {}
                ),
                **(
                    {
                        "output_ports": [
                            {
                                "key": port.key,
                                "label": port.label,
                                "type": port.port_type,
                                "required": port.required,
                                "collection": port.collection,
                            }
                            for port in item.descriptor.output_ports
                        ]
                    }
                    if item.descriptor.output_ports
                    else {}
                ),
            }
            for item in sorted(
                self._registrations.values(),
                key=lambda registration: registration.descriptor.handler_id,
            )
        ]

    @staticmethod
    def _registration_available(registration: _Registration) -> bool:
        if registration.factory is not None:
            return True
        module_name, separator, attribute = registration.loader.partition(":")
        if not separator or not module_name or not attribute:
            return False
        try:
            return importlib.util.find_spec(module_name) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            return False

    def explicit_handlers_for_profile(self, profile_id: str) -> tuple[str, ...]:
        return _PROFILE_HANDLERS.get(str(profile_id or "").strip(), ())

    def loaded_handler_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                handler_id
                for handler_id, registration in self._registrations.items()
                if registration.instance is not None
            )
        )

    def invoke(self, request: GovernancePluginRequest) -> GovernancePluginResult:
        registration = self._registrations.get(request.handler_id)
        if registration is None:
            return self._failure(
                request,
                descriptor=None,
                code="governance_handler_unavailable",
                message=f"治理处理器未注册：{request.handler_id}",
            )
        descriptor = registration.descriptor
        if request.node_kind != descriptor.node_kind:
            return self._failure(
                request,
                descriptor=descriptor,
                code="governance_handler_kind_mismatch",
                message="节点类型与治理处理器类型不一致。",
            )
        boundary_error = self._validate_request_boundary(request, descriptor)
        if boundary_error is not None:
            code, message = boundary_error
            return self._failure(
                request,
                descriptor=descriptor,
                code=code,
                message=message,
            )
        try:
            plugin = self._load(registration)
            execution = plugin.execute(request)
        except Exception as exc:  # Plugin exceptions must not become Provider failures.
            return self._failure(
                request,
                descriptor=descriptor,
                code="governance_plugin_failed",
                message=f"治理插件执行失败：{type(exc).__name__}",
            )
        execution_error = self._validate_execution(request, descriptor, execution)
        if execution_error is not None:
            code, message = execution_error
            return self._failure(
                request,
                descriptor=descriptor,
                code=code,
                message=message,
            )
        if execution.status != "passed" or (
            execution.validation is not None
            and execution.validation.status != "passed"
        ):
            return self._failure(
                request,
                descriptor=descriptor,
                code=execution.error_code or "governance_validation_failed",
                message=execution.message or "治理校验未通过。",
                validation=execution.validation,
            )
        return GovernancePluginResult(
            handler_id=descriptor.handler_id,
            handler_version=descriptor.handler_version,
            node_id=request.node_id,
            node_kind=descriptor.node_kind,
            governance_status="passed",
            delivery_status="ready",
            validation=execution.validation,
            produced_artifacts=execution.produced_artifacts,
            message=execution.message,
        )

    def _load(self, registration: _Registration) -> GovernancePlugin:
        if registration.instance is not None:
            return registration.instance
        factory = registration.factory
        if factory is None:
            module_name, separator, attribute = registration.loader.partition(":")
            if not separator or not module_name or not attribute:
                raise ValueError("invalid governance plugin loader")
            factory = getattr(importlib.import_module(module_name), attribute)
        registration.instance = factory()
        return registration.instance

    def _validate_request_boundary(
        self,
        request: GovernancePluginRequest,
        descriptor: GovernancePluginDescriptor,
    ) -> tuple[str, str] | None:
        declared_by_id: dict[str, list[Any]] = {}
        for output in request.declared_outputs:
            declared_by_id.setdefault(output.artifact_id, []).append(output)
            path = PurePosixPath(output.path.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts:
                return "unsafe_governance_output_path", "治理输出路径必须位于交付目录内。"

        if descriptor.node_kind == "validator":
            if request.requested_output_ids or any(
                edge.source_node_id == request.node_id for edge in request.output_edges
            ):
                return "validator_output_forbidden", "只读校验节点不能声明生成输出。"
            if len(request.required_output_ids) != len(
                set(request.required_output_ids)
            ):
                return (
                    "validator_required_output_binding_invalid",
                    "专业校验节点的交付件绑定不能重复。",
                )
            if (
                "single_declared_output" in descriptor.capabilities
                and len(request.required_output_ids) != 1
            ):
                return (
                    "validator_required_output_binding_invalid",
                    "该专业校验节点必须且只能绑定一个声明交付件。",
                )
            missing = [
                output_id
                for output_id in request.required_output_ids
                if len(declared_by_id.get(output_id, ())) != 1
            ]
            if missing:
                return (
                    "validator_requires_undeclared_output",
                    "校验节点只能读取工作流已声明的交付件。",
                )
            return None

        if not request.requested_output_ids:
            return "governance_output_not_requested", "生成型治理节点必须声明输出。"
        requested_output_ids = set(request.requested_output_ids)
        if len(requested_output_ids) != len(request.requested_output_ids):
            return (
                "duplicate_governance_output_request",
                "生成型治理节点的声明输出不能重复。",
            )
        if any(
            edge.source_node_id == request.node_id
            and edge.target_artifact_id not in requested_output_ids
            for edge in request.output_edges
        ):
            return (
                "undeclared_governance_output_edge",
                "生成型治理节点不能连接未声明为本节点输出的交付件。",
            )
        for artifact_id in request.requested_output_ids:
            declarations = declared_by_id.get(artifact_id, [])
            if len(declarations) != 1:
                return (
                    "undeclared_governance_output",
                    "生成型治理输出必须唯一且预先声明。",
                )
            declaration = declarations[0]
            if declaration.producer_node_id != request.node_id:
                return (
                    "governance_output_producer_mismatch",
                    "声明交付件的 producer 与治理节点不一致。",
                )
            edges = [
                edge
                for edge in request.output_edges
                if edge.target_artifact_id == artifact_id
            ]
            if not edges:
                return (
                    "unconnected_governance_output",
                    "生成型治理输出必须通过画布连线连接声明交付件。",
                )
            if len(edges) != 1:
                return (
                    "multiple_producers_for_governance_output",
                    "声明交付件只能有一个 producer。",
                )
            edge = edges[0]
            if (
                edge.source_node_id != request.node_id
                or edge.source_port_id != declaration.producer_port_id
            ):
                return (
                    "governance_output_producer_mismatch",
                    "治理输出连线与声明 producer 端口不一致。",
                )
        return None

    def _validate_execution(
        self,
        request: GovernancePluginRequest,
        descriptor: GovernancePluginDescriptor,
        execution: GovernancePluginExecution,
    ) -> tuple[str, str] | None:
        if descriptor.node_kind == "validator" and execution.produced_artifacts:
            return "validator_generated_artifact", "只读校验插件无权增加交付件。"
        if descriptor.node_kind == "governance":
            if execution.status != "passed":
                if execution.produced_artifacts:
                    return (
                        "failed_governance_generated_artifact",
                        "失败的治理插件不能返回候选交付件。",
                    )
                return None
            produced_ids = [item.artifact_id for item in execution.produced_artifacts]
            if len(produced_ids) != len(set(produced_ids)):
                return "duplicate_governance_artifact", "治理插件重复生成同一交付件。"
            if set(produced_ids) != set(request.requested_output_ids):
                return (
                    "governance_output_set_mismatch",
                    "治理插件生成结果必须与节点声明输出完全一致。",
                )
        return None

    def _failure(
        self,
        request: GovernancePluginRequest,
        *,
        descriptor: GovernancePluginDescriptor | None,
        code: str,
        message: str,
        validation: ValidationResult | None = None,
    ) -> GovernancePluginResult:
        issue = ValidationIssue(code=code, message=message)
        normalized_validation = validation or ValidationResult(
            status="failed",
            issues=(issue,),
        )
        return GovernancePluginResult(
            handler_id=(descriptor.handler_id if descriptor else request.handler_id),
            handler_version=(descriptor.handler_version if descriptor else 0),
            node_id=request.node_id,
            node_kind=(descriptor.node_kind if descriptor else request.node_kind),
            governance_status="failed" if request.blocking else "warning",
            delivery_status="blocked" if request.blocking else "ready",
            validation=normalized_validation,
            message=message,
            error_code=code,
        )


def create_governance_plugin_registry() -> GovernancePluginRegistry:
    from app.services.governance_plugins.contracts import GovernancePortDescriptor

    registry = GovernancePluginRegistry()
    registrations = (
        (
            GovernancePluginDescriptor(
                handler_id="storage_test_design",
                handler_version=1,
                node_kind="governance",
                capabilities=("declared_artifact_generation", "storage_test_design"),
                input_ports=(
                    GovernancePortDescriptor(
                        key="source_evidence",
                        label="源码证据",
                        port_type="artifact",
                        required=True,
                    ),
                ),
                output_ports=(
                    GovernancePortDescriptor(
                        key="sfmea",
                        label="SFMEA 风险清单",
                        port_type="artifact",
                        required=True,
                    ),
                    GovernancePortDescriptor(
                        key="black_box_cases",
                        label="黑盒测试用例",
                        port_type="artifact",
                        required=True,
                    ),
                ),
            ),
            "app.services.governance_plugins.storage_test_design:create_plugin",
        ),
        (
            GovernancePluginDescriptor(
                handler_id="sfmea",
                handler_version=1,
                node_kind="validator",
                capabilities=(
                    "read_only",
                    "sfmea_validation",
                    "single_declared_output",
                ),
            ),
            "app.services.governance_plugins.sfmea:create_plugin",
        ),
        (
            GovernancePluginDescriptor(
                handler_id="black_box",
                handler_version=1,
                node_kind="validator",
                capabilities=(
                    "read_only",
                    "black_box_validation",
                    "single_declared_output",
                ),
            ),
            "app.services.governance_plugins.black_box:create_plugin",
        ),
        (
            GovernancePluginDescriptor(
                handler_id="independent_review",
                handler_version=1,
                node_kind="validator",
                capabilities=("independent_review", "read_only"),
            ),
            "app.services.governance_plugins.independent_review:create_plugin",
        ),
    )
    for descriptor, loader in registrations:
        registry.register(descriptor, loader=loader)
    return registry


def governance_handler_availability_snapshot() -> list[dict[str, Any]]:
    """Stable compiler entry point; discovery never imports handler modules."""
    return create_governance_plugin_registry().availability_snapshot()
