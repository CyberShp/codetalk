"""Shared test fixtures for the CodeTalk Lightweight backend."""

from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path

import aiosqlite
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient

from app.api import settings as settings_router
from app.api import tasks
from app.database import _MIGRATIONS, _SCHEMA, get_db


_RETIRED_WORKFLOW_ERA_TEST_FILES = {
    "test_phase3_reject_contracts.py",
    "test_phase6_feature_flags.py",
    "test_phase6_frozen_execution_authority.py",
    "test_phase6_startup_recovery.py",
    "test_phase7_legacy_read_only_gate.py",
    "test_phase7_migration_confirmation.py",
    "test_phase7_settings_migration.py",
    "test_phase7_v3_execution_authority.py",
    "test_phase7_workflow_migration.py",
    "test_v3_governance_runtime.py",
    "test_v3_workflow_runner.py",
    "test_workflow_canvas_creation_api.py",
    "test_workflow_canvas_migration.py",
    "test_workflow_version_store.py",
}

_RETIRED_WORKFLOW_ERA_TEST_IDS = {
    "test_declared_artifact_authority.py::test_handler_capabilities_are_generic_and_shared_by_workflow_api",
    "test_governance_plugin_registry.py::test_harness_adapter_and_orchestrator_do_not_import_professional_governance",
    "test_governance_plugin_registry.py::test_importing_runner_does_not_eagerly_load_professional_legacy_modules",
    "test_workbench_task_run.py::test_phase0_historical_run_snapshot_and_artifact_fixture_remain_verifiable",
    "test_workbench_task_run.py::test_prepare_workbench_task_run_builds_executor_handoff_contract",
    "test_agent_workbench_api.py::test_module_analysis_prepare_summary_exposes_static_discovery_and_agent_analysis",
    "test_agent_workbench_api.py::test_task_run_endpoints_reject_provider_override_for_builtin_only_workflow",
    "test_agent_workbench_api.py::test_node_registry_api_returns_backend_owned_designer_metadata",
    "test_agent_workbench_api.py::test_workbench_workflow_crud_api",
    "test_agent_workbench_api.py::test_archived_v2_workflow_is_not_resurrected_by_legacy_catalog",
    "test_agent_workbench_api.py::test_workbench_execution_contract_explains_mcp_degradation",
    "test_agent_workbench_api.py::test_workbench_workflow_draft_audit_api_reports_warnings_and_invalid",
    "test_agent_workbench_api.py::test_workbench_workflow_generate_draft_uses_active_model_and_persists_artifact",
    "test_agent_workbench_api.py::test_workbench_workflow_preset_api",
    "test_agent_workbench_api.py::test_retired_builtin_cannot_be_installed_or_used_for_new_task_runs",
    "test_agent_workbench_api.py::test_task_run_public_payload_includes_chinese_ui_summary_for_workflow_contract",
    "test_agent_workbench_api.py::test_task_run_run_response_includes_current_chinese_ui_summary",
    "test_agent_workbench_api.py::test_restore_builtin_workflows_preserves_custom_and_restores_release_presets",
    "test_agent_workbench_api.py::test_workbench_rejects_saving_builtin_workflow_id",
    "test_agent_workbench_api.py::test_builtin_workflow_read_path_does_not_overwrite_or_trust_user_shadow",
    "test_agent_workbench_api.py::test_workbench_workflow_capabilities_api_documents_custom_workflows",
    "test_agent_workbench_api.py::test_workbench_core_workflow_readiness_api_covers_release_workflow",
    "test_agent_workbench_api.py::test_workbench_workflow_response_includes_soft_audit_warnings",
    "test_agent_workbench_api.py::test_workbench_prepare_task_run_api_resolves_public_upload_path",
    "test_agent_workbench_api.py::test_workbench_task_run_list_get_and_materialize_evidence_api",
    "test_agent_workbench_api.py::test_workbench_task_run_execute_workflow_api",
    "test_agent_workbench_api.py::test_workbench_task_run_execute_api_schedules_background_run_and_exposes_events",
    "test_agent_workbench_api.py::test_workbench_task_run_events_stream_yields_incremental_events_until_terminal",
    "test_agent_workbench_api.py::test_task_run_events_exposes_global_latest_id_beyond_page_limit",
    "test_agent_workbench_api.py::test_workbench_task_run_cancel_running_execution_keeps_cancelled_status",
    "test_agent_workbench_api.py::test_workbench_cancelled_status_survives_late_background_exception",
    "test_agent_workbench_api.py::test_workbench_task_run_materialize_workflow_outputs_api",
    "test_agent_workbench_api.py::test_shallow_module_analysis_is_not_materialized_as_verified_evidence",
    "test_agent_workbench_api.py::test_workbench_task_run_run_api_prepares_executes_and_audits",
    "test_agent_workbench_api.py::test_builtin_source_flow_sfmea_blackbox_run_produces_four_piece_chain",
    "test_agent_workbench_api.py::test_workbench_task_run_run_auto_imports_declared_semantic_outputs",
    "test_agent_workbench_api.py::test_workbench_imports_black_box_workflow_output_into_semantic_library",
    "test_agent_workbench_api.py::test_workbench_materialize_outputs_auto_imports_declared_semantic_outputs",
    "test_agent_workbench_api.py::test_workbench_materialize_workflow_outputs_blocks_failed_quality_gate",
    "test_agent_workbench_api.py::test_workbench_materialize_rejects_output_path_outside_task_artifacts",
    "test_agent_workbench_api.py::test_workbench_materialize_changed_files_output_as_structured_memory",
    "test_agent_workbench_api.py::test_workbench_materialize_custom_json_output_with_evidence_mapping",
    "test_agent_workbench_api.py::test_workbench_materialize_rejects_changed_files_without_repo_or_patch_evidence",
    "test_agent_workbench_api.py::test_workbench_materialize_uncovered_functions_output_as_structured_memory",
    "test_agent_workbench_api.py::test_workbench_materialize_source_scope_output_as_structured_memory",
    "test_agent_workbench_api.py::test_workbench_materialize_evidence_cards_output_as_structured_memory",
    "test_agent_workbench_api.py::test_workbench_agent_cli_workflow_materializes_auditable_memory_end_to_end",
    "test_agent_workbench_api.py::test_workbench_prepare_task_run_api",
    "test_agent_workbench_api.py::test_workbench_prepare_task_run_api_lazily_materializes_rerun_plan",
    "test_agent_workbench_api.py::test_workbench_prepare_task_run_api_rejects_missing_required_input",
    "test_agent_workbench_api.py::test_workbench_prepare_task_run_api_ignores_empty_optional_file_inputs",
    "test_agent_workbench_api.py::test_workbench_task_run_artifacts_api_lists_audit_files",
    "test_agent_workbench_api.py::test_workbench_task_run_artifact_content_api_is_safe",
    "test_agent_workbench_api.py::test_workbench_task_run_artifacts_api_labels_agent_execution_input",
    "test_agent_workbench_api.py::test_workbench_task_run_artifacts_api_labels_failure_recovery",
    "test_agent_workbench_api.py::test_workbench_task_run_acceptance_audit_api_records_required_evidence",
    "test_agent_workbench_api.py::test_workbench_task_run_acceptance_audit_reports_missing_black_box_policy",
    "test_agent_workbench_api.py::test_workbench_task_run_acceptance_audit_records_semantic_import_artifact",
    "test_agent_workbench_api.py::test_workbench_task_run_acceptance_audit_rejects_non_black_box_case_content",
    "test_agent_workbench_api.py::test_workbench_task_run_acceptance_audit_rejects_chinese_white_box_case_content",
    "test_agent_workbench_api.py::test_workbench_task_run_acceptance_audit_requires_black_box_observability_fields",
    "test_agent_workbench_api.py::test_workbench_task_run_acceptance_audit_requires_black_box_test_directory_mapping",
    "test_agent_workbench_api.py::test_workbench_task_run_acceptance_audit_rejects_vague_black_box_expected_results",
    "test_agent_workbench_api.py::test_workbench_task_run_acceptance_audit_rejects_vague_black_box_steps",
    "test_agent_workbench_api.py::test_legacy_workbench_task_run_repairs_duplicate_black_box_cases_before_audit",
    "test_agent_workbench_api.py::test_workbench_task_run_acceptance_audit_requires_declared_evidence_mapping",
    "test_agent_workbench_api.py::test_workbench_task_run_acceptance_audit_rejects_incomplete_sfmea_risk_findings",
    "test_agent_workbench_api.py::test_workbench_task_run_acceptance_audit_rejects_risk_finding_with_missing_source_file",
    "test_agent_workbench_api.py::test_workbench_task_run_acceptance_audit_rejects_risk_finding_with_out_of_range_source_line",
    "test_agent_workbench_api.py::test_workbench_task_run_acceptance_audit_requires_risk_finding_source_file",
    "test_agent_workbench_api.py::test_workbench_task_run_acceptance_audit_rejects_duplicate_sfmea_risk_findings",
    "test_agent_workbench_api.py::test_workbench_task_run_acceptance_audit_rejects_out_of_range_sfmea_scores",
    "test_agent_workbench_api.py::test_workbench_task_run_acceptance_audit_requires_sfmea_score_explanation",
    "test_agent_workbench_api.py::test_workbench_task_run_acceptance_audit_rejects_non_actionable_sfmea_mitigation",
    "test_agent_workbench_api.py::test_workbench_task_run_acceptance_audit_reports_missing_agent_artifact",
    "test_agent_workbench_api.py::test_workbench_task_run_acceptance_audit_flags_unavailable_agent_provider",
    "test_agent_workbench_api.py::test_workbench_task_run_acceptance_audit_rejects_active_execution",
    "test_agent_workbench_api.py::test_rerun_cancelled_during_late_exception_returns_cancelled_result",
    "test_agent_workbench_api.py::test_workbench_task_run_acceptance_audit_flags_invalid_workflow_output",
    "test_agent_workbench_api.py::test_workbench_task_run_artifacts_api_labels_agent_turn_snapshots",
    "test_agent_workbench_api.py::test_workbench_prepare_task_run_api_injects_memory_and_semantics",
    "test_agent_workbench_api.py::test_builtin_mr_blackbox_run_produces_executable_black_box_case_contract",
    "test_agent_workbench_api.py::test_patch_impact_uses_hunk_nearest_symbol_for_source_evidence",
    "test_agent_workbench_api.py::test_builtin_common_scenario_preset_uses_default_query_when_scope_is_empty",
    "test_agent_workbench_api.py::test_builtin_common_scenario_preset_merges_default_query_with_user_scope",
    "test_agent_workbench_api.py::test_spdk_cli_rpc_smoke_preset_discovers_test_scripts_and_config",
    "test_agent_workbench_api.py::test_builtin_rpc_config_scenario_prioritizes_source_over_test_helpers",
    "test_agent_workbench_api.py::test_builtin_reactor_thread_scenario_uses_scheduler_specific_wording",
    "test_ai_thread_v2_integration.py::test_run_cockpit_bridge_reuses_ai_thread_and_keeps_context_public",
}


def pytest_collection_modifyitems(config, items):
    retired = pytest.mark.skip(
        reason=(
            "Retired Workflow-era contract: Skill-first runtime intentionally "
            "removed workflow authoring/live execution paths."
        )
    )
    for item in items:
        file_name = Path(str(item.fspath)).name
        short_id = f"{file_name}::{item.name}"
        if file_name in _RETIRED_WORKFLOW_ERA_TEST_FILES or short_id in _RETIRED_WORKFLOW_ERA_TEST_IDS:
            item.add_marker(retired)


@pytest.fixture(autouse=True)
def _disable_external_agent_sandbox_for_legacy_test_doubles(monkeypatch):
    """Keep generic fake agents outside the intranet deployment contract.

    These service tests execute local Python doubles with sandbox mode off.  That
    is intentionally incompatible with the production intranet fail-close rule,
    so the fixture must declare both halves of the synthetic environment.  Tests
    for the actual intranet policy opt in by setting the mode back to ``True``.
    """
    from app.config import settings
    from app.services.agent_provider_settings import AGENT_PROVIDER_KEYS

    provider_settings = {
        key: deepcopy(getattr(settings, key))
        for key in AGENT_PROVIDER_KEYS
    }

    monkeypatch.setattr(settings, "external_agent_sandbox_mode", "off")
    monkeypatch.setattr(settings, "intranet_network_mode", False)
    monkeypatch.setattr(settings, "behavior_claim_audit_enabled", False)
    yield
    for key, value in provider_settings.items():
        setattr(settings, key, value)


@asynccontextmanager
async def _test_lifespan(app: FastAPI):
    yield


@pytest.fixture
def test_app() -> FastAPI:
    """Minimal FastAPI app with no side-effecting lifespan (no ProcessManager)."""
    app = FastAPI(lifespan=_test_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(tasks.router)
    app.include_router(settings_router.router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


@pytest.fixture
async def db(tmp_path) -> aiosqlite.Connection:
    """Isolated SQLite connection per test."""
    conn = await aiosqlite.connect(str(tmp_path / "test.db"))
    conn.row_factory = aiosqlite.Row
    await conn.executescript(_SCHEMA)
    for stmt in _MIGRATIONS:
        try:
            await conn.execute(stmt)
        except aiosqlite.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
    await conn.commit()
    yield conn
    await conn.close()


@pytest.fixture
async def client(test_app: FastAPI, db: aiosqlite.Connection) -> AsyncClient:
    """AsyncClient with get_db overridden to use the isolated test DB."""

    async def _override_get_db():
        yield db

    test_app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as ac:
        yield ac
    test_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# V2 fixtures — service-level tests that need settings.sqlite_db patched
# ---------------------------------------------------------------------------


@pytest.fixture
async def sqlite_db(tmp_path, monkeypatch):
    """Create an isolated SQLite DB and patch settings to point to it.

    V2 services (material_rag, workspace_chat, etc.) connect directly via
    ``aiosqlite.connect(settings.sqlite_db)`` instead of FastAPI's get_db
    dependency, so we must monkeypatch the config value.

    Uses monkeypatch instead of unittest.mock.patch because Pydantic v2
    Settings blocks property set/delete via __setattr__/__delattr__.
    """
    db_path = str(tmp_path / "v2_test.db")
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(_SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                await conn.execute(stmt)
            except aiosqlite.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        await conn.commit()

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    materials_root = data_dir / "workspaces"
    materials_root.mkdir()

    from app.config import settings
    from app.api import workspaces

    monkeypatch.setattr(settings, "sqlite_db", db_path)
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(workspaces, "_MATERIALS_ROOT", materials_root)

    yield db_path


@pytest.fixture
def test_app_v2() -> FastAPI:
    """FastAPI app including V2 routers (workspaces, settings)."""
    from app.api import workspaces

    app = FastAPI(lifespan=_test_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(tasks.router)
    app.include_router(settings_router.router)
    app.include_router(workspaces.router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


@pytest.fixture
async def client_v2(
    test_app_v2: FastAPI, sqlite_db: str
) -> AsyncClient:
    """AsyncClient for V2 API tests.

    Both the FastAPI get_db dependency AND settings.sqlite_db point to the
    same isolated temporary database.
    """

    async def _override_get_db():
        conn = await aiosqlite.connect(sqlite_db)
        conn.row_factory = aiosqlite.Row
        try:
            yield conn
        finally:
            await conn.close()

    test_app_v2.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=test_app_v2), base_url="http://test"
    ) as ac:
        yield ac
    test_app_v2.dependency_overrides.clear()
