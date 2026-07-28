"""Explicit storage-test professional governance generator."""

from __future__ import annotations

import json

from app.services.governance_plugins.contracts import (
    GeneratedGovernanceArtifact,
    GovernancePluginExecution,
    GovernancePluginRequest,
    ValidationIssue,
    ValidationResult,
)


_OUTPUT_PORT_KEY_ROLES = {
    "contract": "contract",
    "storage_test_contract": "contract",
    "test_activity_contract": "contract",
    "sfmea": "sfmea",
    "risk_register": "sfmea",
    "black_box_cases": "black_box_cases",
    "external_cases": "black_box_cases",
}


class StorageTestDesignPlugin:
    def execute(self, request: GovernancePluginRequest) -> GovernancePluginExecution:
        declared = {
            output.artifact_id: output for output in request.declared_outputs
        }
        roles: dict[str, str] = {}
        for artifact_id in request.requested_output_ids:
            declaration = declared[artifact_id]
            role = _output_role(
                producer_port_key=declaration.producer_port_key,
                producer_port_id=declaration.producer_port_id,
            )
            if role is None:
                return _failure(
                    code="storage_test_design_generation_unavailable",
                    message="声明输出没有可用的存储测试专业生成角色。",
                    artifact_id=artifact_id,
                )
            if role in roles.values():
                return _failure(
                    code="storage_test_design_output_role_ambiguous",
                    message=(
                        "同一专业角色只能绑定一个声明输出，"
                        "不能复制为多个交付件。"
                    ),
                    artifact_id=artifact_id,
                )
            roles[artifact_id] = role

        professional_roles = tuple(
            role for role in roles.values() if role in {"sfmea", "black_box_cases"}
        )
        professional_payloads: dict[str, list[dict]] = {}
        if professional_roles:
            from app.services.governance_plugins.storage_professional_generation import (
                StorageProfessionalGenerationError,
                generate_storage_professional_payloads,
            )

            try:
                professional_payloads = generate_storage_professional_payloads(
                    inputs=request.inputs,
                    roles=professional_roles,
                    node_id=request.node_id,
                    artifact_id=next(
                        artifact_id
                        for artifact_id, role in roles.items()
                        if role in professional_roles
                    ),
                )
            except StorageProfessionalGenerationError as exc:
                return _failure(
                    code=exc.code,
                    message=exc.message,
                    artifact_id=exc.artifact_id,
                    details=exc.details,
                )

        produced: list[GeneratedGovernanceArtifact] = []
        for artifact_id in request.requested_output_ids:
            declaration = declared[artifact_id]
            role = roles[artifact_id]
            if role == "contract":
                payload = _contract_payload(request=request, path=declaration.path)
            else:
                payload = professional_payloads[role]
            produced.append(
                GeneratedGovernanceArtifact(
                    artifact_id=artifact_id,
                    content=json.dumps(
                        payload,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    ),
                    media_type="application/json",
                    metadata={"professional_role": role},
                )
            )
        return GovernancePluginExecution(
            status="passed",
            produced_artifacts=tuple(produced),
        )


def _output_role(*, producer_port_key: str, producer_port_id: str) -> str | None:
    # New contracts freeze business semantics in producer_port_key. The port ID
    # fallback preserves legacy requests whose stable IDs were themselves keys.
    semantic_key = producer_port_key.strip() or producer_port_id.strip()
    normalized_key = semantic_key.lower().replace("-", "_")
    return _OUTPUT_PORT_KEY_ROLES.get(normalized_key)


def _contract_payload(*, request: GovernancePluginRequest, path: str) -> dict:
    # The large professional rule module remains cold until this handler is
    # explicitly invoked by a compiled governance node.
    from app.services.test_activity_contract import build_test_activity_contract

    payload = build_test_activity_contract(
        target=str(request.inputs.get("target") or ""),
        repo_path=str(request.inputs.get("repo_path") or ""),
        workflow_outputs=[{"artifact": path, "required": True}],
        user_requirements=str(request.inputs.get("user_requirements") or ""),
    )
    payload["required_outputs"] = [path]
    payload["artifact_contract"] = {
        path: {
            "preview": "json",
            "schema": {"type": "object"},
        }
    }
    return payload


def create_plugin() -> StorageTestDesignPlugin:
    return StorageTestDesignPlugin()


def _failure(
    *,
    code: str,
    message: str,
    artifact_id: str = "",
    details: dict | None = None,
) -> GovernancePluginExecution:
    issue = ValidationIssue(
        code=code,
        message=message,
        artifact_id=artifact_id,
        details=dict(details or {}),
    )
    return GovernancePluginExecution(
        status="failed",
        validation=ValidationResult(status="failed", issues=(issue,)),
        message=message,
        error_code=code,
    )
