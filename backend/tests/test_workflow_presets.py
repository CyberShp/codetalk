def test_builtin_workflow_presets_are_valid_and_cover_core_scenarios():
    from app.services.workflow_dsl import audit_workflow_definition, validate_workflow_definition
    from app.services.workflow_presets import builtin_workflow_presets

    presets = builtin_workflow_presets()

    assert {
        "module_analysis",
        "resource_leak_hunt",
        "mr_blackbox_test",
        "patch_impact_review",
        "source_flow_sfmea_blackbox",
        "nvmf_connect_io_blackbox",
        "iscsi_login_session_blackbox",
        "bdev_io_reset_blackbox",
        "rpc_config_negative_blackbox",
        "reactor_thread_poller_blackbox",
        "nvmf_disconnect_reconnect_blackbox",
        "iscsi_auth_failure_blackbox",
        "bdev_failover_resource_blackbox",
        "blobstore_ftl_recovery_blackbox",
        "vhost_vfio_user_lifecycle_blackbox",
        "nvmf_tcp_tls_auth_blackbox",
        "bdev_qos_latency_blackbox",
        "jsonrpc_concurrency_idempotency_blackbox",
        "app_startup_shutdown_smoke_blackbox",
        "nvme_ctrlr_hotplug_reset_blackbox",
        "storage_capacity_enospc_recovery_blackbox",
        "nvmf_rdma_transport_blackbox",
        "iscsi_digest_multi_connection_blackbox",
        "bdev_hotremove_io_error_blackbox",
        "blobstore_metadata_powerfail_blackbox",
        "rpc_security_authz_blackbox",
        "fault_injection_timeout_recovery_blackbox",
        "concurrent_operations_stress_blackbox",
        "observability_diagnostics_blackbox",
        "config_compatibility_rollback_blackbox",
        "lvol_snapshot_clone_blackbox",
        "raid_degraded_rebuild_blackbox",
        "nvme_multipath_failover_blackbox",
        "env_hugepage_memory_blackbox",
        "spdk_cli_rpc_smoke_blackbox",
        "target_crash_restart_blackbox",
        "multi_client_isolation_blackbox",
        "queue_depth_backpressure_blackbox",
        "io_error_injection_retry_blackbox",
        "config_reload_persistence_blackbox",
        "long_running_resource_leak_blackbox",
        "basic_lifecycle_smoke_blackbox",
        "io_stress_performance_blackbox",
        "failure_recovery_soak_blackbox",
        "transport_network_partition_blackbox",
        "data_integrity_corruption_blackbox",
        "upgrade_compatibility_persistence_blackbox",
        "telemetry_metrics_regression_blackbox",
    }.issubset({item["id"] for item in presets})
    assert [item["id"] for item in presets[:5]] == [
        "module_analysis",
        "resource_leak_hunt",
        "mr_blackbox_test",
        "patch_impact_review",
        "source_flow_sfmea_blackbox",
    ]

    for preset in presets:
        workflow = validate_workflow_definition(preset["definition"])
        assert workflow.id == preset["definition"]["id"]
        assert workflow.steps
        assert workflow.outputs
        assert audit_workflow_definition(preset["definition"])["warnings"] == []

    mr_preset = next(item for item in presets if item["id"] == "mr_blackbox_test")
    assert any(
        item["id"] == "mr_link" and item["type"] == "mr_link" and "resolver" not in item
        for item in mr_preset["definition"]["inputs"]
    )
    assert any(item["id"] == "patch_diff" and item["type"] == "patch" for item in mr_preset["definition"]["inputs"])
    assert mr_preset["definition"]["steps"][0]["type"] == "local_mr_blackbox_test"
    assert "mr_snapshot.json" in mr_preset["definition"]["steps"][0]["required_artifacts"]
    assert "black_box_cases.json" in mr_preset["definition"]["steps"][0]["required_artifacts"]
    black_box_output = next(
        item
        for item in mr_preset["definition"]["outputs"]
        if item["id"] == "black_box_cases"
    )
    assert black_box_output["type"] == "test_cases"
    assert black_box_output["artifact"] == "black_box_cases.json"
    assert black_box_output["semantic_import"]["enabled"] is True

    risk_preset = next(item for item in presets if item["id"] == "resource_leak_hunt")
    risk_step = next(
        item
        for item in risk_preset["definition"]["steps"]
        if item["id"] == "hunt_risks"
    )
    assert risk_step["type"] == "local_resource_leak_hunt"
    risk_output = next(
        item
        for item in risk_preset["definition"]["outputs"]
        if item["id"] == "risk_findings"
    )
    assert risk_output["artifact"] == "risk_findings.json"
    assert risk_output["evidence_memory"]["enabled"] is True
    assert risk_output["evidence_memory"]["kind"] == "resource_risk_finding"
    assert risk_output["evidence_memory"]["path_field"] == "file_path"

    patch_preset = next(item for item in presets if item["id"] == "patch_impact_review")
    patch_step = next(
        item
        for item in patch_preset["definition"]["steps"]
        if item["id"] == "analyze_impact"
    )
    assert patch_step["type"] == "local_patch_impact_review"
    impact_output = next(
        item
        for item in patch_preset["definition"]["outputs"]
        if item["id"] == "impact_scope"
    )
    assert impact_output["artifact"] == "impact_scope.json"
    assert impact_output["evidence_memory"]["enabled"] is True
    assert impact_output["evidence_memory"]["kind"] == "patch_impact_scope"

    scenario_preset = next(item for item in presets if item["id"] == "nvmf_connect_io_blackbox")
    scenario_step = next(
        item
        for item in scenario_preset["definition"]["steps"]
        if item["id"] == "analyze_source_flow"
    )
    assert scenario_step["type"] == "local_source_flow_sfmea_blackbox"
    assert "lib/nvmf" in scenario_step["default_query"]
    assert "black_box_cases.json" in scenario_step["required_artifacts"]
    scenario_outputs = {
        item["id"]: item
        for item in scenario_preset["definition"]["outputs"]
    }
    assert scenario_outputs["source_scope"]["schema"]["type"] == "object"
    assert scenario_outputs["code_evidence"]["schema"]["type"] == "array"
    assert scenario_outputs["sfmea"]["schema"]["type"] == "array"
    assert scenario_outputs["black_box_cases"]["semantic_import"]["enabled"] is True


def test_restore_builtin_workflow_presets_refreshes_stale_builtin_definitions(tmp_path):
    from app.services.workflow_dsl import WorkflowStore, audit_workflow_definition
    from app.services.workflow_presets import restore_builtin_workflow_presets

    store = WorkflowStore(tmp_path / "workflows.db")
    store.save_workflow({
        "id": "module_analysis",
        "name": "Stale Module Analysis",
        "version": 1,
        "inputs": [],
        "steps": [{"id": "discover_scope", "type": "local_scope_discover"}],
        "outputs": [{"id": "scope", "type": "json", "from": "discover_scope"}],
    })
    store.save_workflow({
        "id": "custom_workflow",
        "name": "Custom Workflow",
        "version": 1,
        "inputs": [],
        "steps": [{"id": "render", "type": "report_render"}],
        "outputs": [{"id": "report", "type": "markdown", "from": "render"}],
    })

    stale = store.get_workflow("module_analysis")
    assert any(
        warning["code"] == "json_output_missing_schema"
        for warning in audit_workflow_definition(stale.raw)["warnings"]
    )

    restore_builtin_workflow_presets(store)

    assert [item.id for item in store.list_workflows()[:5]] == [
        "module_analysis",
        "resource_leak_hunt",
        "mr_blackbox_test",
        "patch_impact_review",
        "source_flow_sfmea_blackbox",
    ]

    restored = store.get_workflow("module_analysis")
    assert restored.name == "Module Analysis"
    assert audit_workflow_definition(restored.raw)["warnings"] == []
    assert store.get_workflow("custom_workflow").name == "Custom Workflow"
    ids = {item.id for item in store.list_workflows()}
    assert {
        "module_analysis",
        "resource_leak_hunt",
        "mr_blackbox_test",
        "patch_impact_review",
        "source_flow_sfmea_blackbox",
        "blobstore_ftl_recovery_blackbox",
        "vhost_vfio_user_lifecycle_blackbox",
        "nvmf_tcp_tls_auth_blackbox",
        "bdev_qos_latency_blackbox",
        "jsonrpc_concurrency_idempotency_blackbox",
        "app_startup_shutdown_smoke_blackbox",
        "nvme_ctrlr_hotplug_reset_blackbox",
        "storage_capacity_enospc_recovery_blackbox",
        "nvmf_rdma_transport_blackbox",
        "iscsi_digest_multi_connection_blackbox",
        "bdev_hotremove_io_error_blackbox",
        "blobstore_metadata_powerfail_blackbox",
        "rpc_security_authz_blackbox",
        "fault_injection_timeout_recovery_blackbox",
        "concurrent_operations_stress_blackbox",
        "observability_diagnostics_blackbox",
        "config_compatibility_rollback_blackbox",
        "lvol_snapshot_clone_blackbox",
        "raid_degraded_rebuild_blackbox",
        "nvme_multipath_failover_blackbox",
        "env_hugepage_memory_blackbox",
        "spdk_cli_rpc_smoke_blackbox",
        "target_crash_restart_blackbox",
        "multi_client_isolation_blackbox",
        "queue_depth_backpressure_blackbox",
        "io_error_injection_retry_blackbox",
        "config_reload_persistence_blackbox",
        "long_running_resource_leak_blackbox",
        "basic_lifecycle_smoke_blackbox",
        "io_stress_performance_blackbox",
        "failure_recovery_soak_blackbox",
        "transport_network_partition_blackbox",
        "data_integrity_corruption_blackbox",
        "upgrade_compatibility_persistence_blackbox",
        "telemetry_metrics_regression_blackbox",
        "custom_workflow",
    }.issubset(ids)


def test_workflow_preset_can_be_installed_into_store(tmp_path):
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workflow_presets import install_workflow_preset

    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = install_workflow_preset(store, "patch_impact_review")

    assert workflow.id == "patch_impact_review"
    assert store.get_workflow("patch_impact_review").name == "Patch Impact Review"


def test_workflow_definition_rejects_unsafe_artifact_paths():
    import pytest

    from app.services.workflow_dsl import WorkflowValidationError, validate_workflow_definition

    with pytest.raises(WorkflowValidationError, match="unsafe required artifact path"):
        validate_workflow_definition({
            "id": "unsafe_required_artifact",
            "name": "Unsafe required artifact",
            "steps": [
                {
                    "id": "agent",
                    "type": "agent_task",
                    "required_artifacts": ["../secret.json"],
                }
            ],
            "outputs": [],
        })


def test_workflow_definition_validates_input_schema_definition():
    import pytest

    from app.services.workflow_dsl import WorkflowValidationError, validate_workflow_definition

    workflow = validate_workflow_definition({
        "id": "schema_input_workflow",
        "name": "Schema input workflow",
        "inputs": [
            {
                "id": "target",
                "type": "free_text",
                "schema": {"type": "string", "minLength": 3},
            }
        ],
        "steps": [{"id": "render", "type": "report_render"}],
        "outputs": [{"id": "report", "type": "markdown", "from": "render"}],
    })

    assert workflow.inputs[0].raw["schema"]["type"] == "string"

    with pytest.raises(WorkflowValidationError, match="workflow input schema must be an object"):
        validate_workflow_definition({
            "id": "bad_input_schema",
            "name": "Bad input schema",
            "inputs": [{"id": "target", "type": "free_text", "schema": "string"}],
            "steps": [{"id": "render", "type": "report_render"}],
            "outputs": [],
        })

    with pytest.raises(WorkflowValidationError, match="unsafe required artifact path"):
        validate_workflow_definition({
            "id": "empty_required_artifact",
            "name": "Empty required artifact",
            "steps": [
                {
                    "id": "agent",
                    "type": "agent_task",
                    "required_artifacts": [""],
                }
            ],
            "outputs": [],
        })

    with pytest.raises(WorkflowValidationError, match="unsafe output artifact path"):
        validate_workflow_definition({
            "id": "unsafe_output_artifact",
            "name": "Unsafe output artifact",
            "steps": [{"id": "agent", "type": "agent_task"}],
            "outputs": [
                {
                    "id": "report",
                    "type": "markdown",
                    "from": "agent",
                    "artifact": "C:/outside/report.md",
                }
            ],
        })
