"""Lazy compatibility facade for the pre-V3 professional execution pipeline.

The implementations remain in their canonical legacy modules. Importing this
facade is domain-neutral; a canonical module is loaded only when a V1/V2 path
actually asks for one of its exported capabilities.
"""

from importlib import import_module
from typing import Any


_EXPORTS = {
    "_apply_quality_feedback_field_patches": ("app.services.ai_staged_execution", "_apply_quality_feedback_field_patches"),
    "_deterministic_quality_claim_repair": ("app.services.ai_staged_execution", "_deterministic_quality_claim_repair"),
    "_deterministic_schema_repair": ("app.services.ai_staged_execution", "_deterministic_schema_repair"),
    "build_profile_execution_evidence": ("app.services.ai_staged_execution", "build_profile_execution_evidence"),
    "build_source_evidence_pack": ("app.services.ai_staged_execution", "build_source_evidence_pack"),
    "build_staged_execution_plan": ("app.services.ai_staged_execution", "build_staged_execution_plan"),
    "execute_staged_builtin_plan": ("app.services.ai_staged_execution", "execute_staged_builtin_plan"),
    "materialize_final_deterministic_quality_repairs": ("app.services.ai_staged_execution", "materialize_final_deterministic_quality_repairs"),
    "materialize_source_evidence_pack": ("app.services.ai_staged_execution", "materialize_source_evidence_pack"),
    "normalize_materialized_sfmea_risk_contract": ("app.services.ai_staged_execution", "normalize_materialized_sfmea_risk_contract"),
    "refresh_deterministic_combined_report": ("app.services.ai_staged_execution", "refresh_deterministic_combined_report"),
    "enrich_external_agent_claim_bindings": ("app.services.artifact_contract_v3", "enrich_external_agent_claim_bindings"),
    "materialize_artifact_contract_v3_outputs": ("app.services.artifact_contract_v3", "materialize_artifact_contract_v3_outputs"),
    "materialize_claim_evidence_ledger": ("app.services.artifact_contract_v3", "materialize_claim_evidence_ledger"),
    "validate_artifact_contract_v3_outputs": ("app.services.artifact_contract_v3", "validate_artifact_contract_v3_outputs"),
    "default_artifact_contract_v3": ("app.services.artifact_contract_v3", "default_artifact_contract_v3"),
    "build_behavior_claim_audit_readiness": ("app.services.behavior_claim_validator", "build_behavior_claim_audit_readiness"),
    "materialize_behavior_claim_validation": ("app.services.behavior_claim_validator", "materialize_behavior_claim_validation"),
    "render_business_flow_markdown": ("app.services.flow_evidence", "render_business_flow_markdown"),
    "promote_regular_stage_caches": ("app.services.regular_stage_governance", "promote_regular_stage_caches"),
    "refresh_source_driven_delivery_governance": ("app.services.source_driven_test_design", "refresh_source_driven_delivery_governance"),
    "ARTIFACT_TEMPLATES": ("app.services.test_activity_contract", "ARTIFACT_TEMPLATES"),
    "_audit_json_artifact": ("app.services.test_activity_contract", "_audit_json_artifact"),
    "audit_test_activity_artifacts": ("app.services.test_activity_contract", "audit_test_activity_artifacts"),
    "black_box_case_delivery_quality_gaps": ("app.services.test_activity_contract", "black_box_case_delivery_quality_gaps"),
    "build_test_activity_contract": ("app.services.test_activity_contract", "build_test_activity_contract"),
    "refresh_test_activity_contract": ("app.services.test_activity_contract", "refresh_test_activity_contract"),
    "default_test_activity_stage_specs": ("app.services.test_activity_stage_specs", "default_test_activity_stage_specs"),
    "TestActivityStageProgressTracker": ("app.services.test_activity_stage_specs", "TestActivityStageProgressTracker"),
    "project_test_activity_stage_progress": ("app.services.test_activity_stage_specs", "project_test_activity_stage_progress"),
    "validate_test_activity_stage_contract": ("app.services.test_activity_stage_specs", "validate_test_activity_stage_contract"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    return getattr(import_module(module_name), attribute_name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
