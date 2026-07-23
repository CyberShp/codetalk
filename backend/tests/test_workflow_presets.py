def test_release_workflow_presets_expose_full_and_two_basic_workflows():
    from app.services.workflow_presets import (
        active_builtin_workflow_presets,
        reserved_builtin_workflow_ids,
    )

    presets = active_builtin_workflow_presets()

    assert [item["id"] for item in presets] == [
        "source_flow_sfmea_blackbox",
        "basic_source_report_codex",
        "basic_source_design_report_builtin",
    ]
    assert "module_analysis" in reserved_builtin_workflow_ids()
    assert "source_flow_sfmea_blackbox" in reserved_builtin_workflow_ids()
    assert "basic_source_report_claude" in reserved_builtin_workflow_ids()


def test_source_flow_v2_keeps_release_presets_and_declares_optional_mindmap():
    from app.services.source_driven_test_design import (
        MINDMAP_ARTIFACTS,
        SOURCE_DRIVEN_V2_ARTIFACTS,
    )
    from app.services.workflow_presets import active_builtin_workflow_presets

    presets = active_builtin_workflow_presets()
    assert [item["id"] for item in presets] == [
        "source_flow_sfmea_blackbox",
        "basic_source_report_codex",
        "basic_source_design_report_builtin",
    ]
    source_flow = next(item for item in presets if item["id"] == "source_flow_sfmea_blackbox")["definition"]
    assert source_flow["name"] == "代码分析 -> 流程 -> SFMEA -> 黑盒用例"
    assert source_flow["description"].startswith("基于真实源码证据")
    assert source_flow["version"] == 2
    step = source_flow["steps"][0]
    assert set(SOURCE_DRIVEN_V2_ARTIFACTS).issubset(step["required_artifacts"])
    mindmap = next(item for item in source_flow["outputs"] if item["id"] == "test_design_mindmap")
    assert mindmap == {
        "id": "test_design_mindmap",
        "label": "测试设计脑图",
        "type": "test_design_mindmap",
        "from": "analyze_source_flow",
        "artifact": MINDMAP_ARTIFACTS[0],
        "companion_artifacts": list(MINDMAP_ARTIFACTS[1:]),
        "required": False,
        "default_enabled": False,
    }
    for preset_id in ("basic_source_report_codex", "basic_source_design_report_builtin"):
        definition = next(item for item in presets if item["id"] == preset_id)["definition"]
        assert definition["artifact_contract_version"] == "v3"
        assert {"source_scope.json", "evidence_cards.json", "flow_cards.json", "sfmea.json", "black_box_cases.json"} <= set(definition["steps"][0]["required_artifacts"])
        assert all(item.get("type") != "test_design_mindmap" for item in definition["outputs"])


def test_sfmea_schema_requires_source_mechanism_effect_chain_controls_and_recovery():
    from app.services.workflow_presets import SFMEA_SCHEMA

    required = set(SFMEA_SCHEMA["items"]["required"])
    assert {
        "sfmea_id",
        "mechanism",
        "trigger_condition",
        "local_effect",
        "upstream_effect",
        "downstream_effect",
        "final_effect",
        "latent",
        "existing_controls",
        "control_gaps",
        "score_explanation",
        "recovery_verification",
        "source_evidence",
        "test_mapping",
    } <= required


def test_black_box_case_schema_requires_sfmea_traceability_ids():
    from app.services.workflow_presets import BLACK_BOX_CASES_SCHEMA

    assert "risk_ids" in BLACK_BOX_CASES_SCHEMA["items"]["required"]
    assert BLACK_BOX_CASES_SCHEMA["items"]["properties"]["risk_ids"] == {
        "type": "array",
        "minItems": 1,
        "items": {"type": "string", "minLength": 1},
    }


def test_source_driven_preset_exposes_chinese_input_and_output_labels():
    from app.services.workflow_presets import get_workflow_preset

    definition = get_workflow_preset("source_flow_sfmea_blackbox")["definition"]
    inputs = {item["id"]: item for item in definition["inputs"]}
    outputs = {item["id"]: item for item in definition["outputs"]}

    assert inputs["analysis_object"]["label"] == "分析对象"
    assert inputs["design_doc"]["label"] == "开发设计文档"
    assert outputs["source_scope"]["label"] == "源码范围"
    assert outputs["black_box_cases"]["label"] == "黑盒测试用例"

    steps = {item["id"]: item for item in definition["steps"]}
    assert steps["analyze_source_flow"]["label"] == "源码驱动测试分析"
    assert steps["validate_evidence"]["label"] == "源码证据校验"
    assert steps["render_report"]["label"] == "汇总报告生成"
    assert "GitNexus" in steps["analyze_source_flow"]["goal"]
    assert "读取本地源码" in steps["analyze_source_flow"]["goal"]


def test_retired_claude_basic_preset_alias_resolves_to_codex_replacement():
    from app.services.workflow_presets import get_workflow_preset

    preset = get_workflow_preset("basic_source_report_claude")

    assert preset["id"] == "basic_source_report_codex"
    assert preset["definition"]["steps"][0]["provider"] == "codex"


def test_basic_report_workflow_presets_have_named_analysis_target_and_one_report_contract():
    from app.services.workflow_presets import active_builtin_workflow_presets

    by_id = {item["id"]: item for item in active_builtin_workflow_presets()}

    source_only = by_id["basic_source_report_codex"]["definition"]
    assert source_only["description"] == by_id["basic_source_report_codex"]["description"]
    assert source_only["execution_subject"] == "agent"
    assert source_only["execution_label"] == "Codex CLI"
    assert source_only["steps"][0]["provider"] == "codex"
    assert source_only["inputs"] == [
        {
            "id": "repo_path",
            "label": "源码工作空间",
            "type": "directory",
            "required": True,
            "resolver": "workspace",
            "role": "SPDK 源码工作空间",
        },
        {
            "id": "analysis_target",
            "label": "分析目标",
            "type": "long_text",
            "required": True,
            "resolver": "manual",
            "role": "用户逐字要求，定义分析范围、流程、异常、资源、并发与恢复重点",
        },
    ]
    source_step = source_only["steps"][0]
    assert source_step["type"] == "agent_task"
    assert source_step["provider"] == "codex"
    assert "mcp_profile" not in source_step
    assert {"source_scope.json", "evidence_cards.json", "flow_cards.json", "sfmea.json", "black_box_cases.json"} <= set(source_step["required_artifacts"])
    assert "用户填写的“分析目标”" in source_step["goal"]
    assert "不得声称该用例已可直接执行" in source_step["goal"]
    assert "受控 harness 设计契约" in source_step["goal"]
    assert "可执行流量构造只能在被明确批准的后续测试活动中生成" in source_step["goal"]
    assert "不得自行创建 artifact manifest、claim ledger、额外报告或未声明 JSON" in source_step["goal"]
    assert "$CODETALK_AGENT_ARTIFACT_DIR" in source_step["goal"]
    assert "Git revision" in source_step["goal"]
    assert "iscsi_set_options -c" in source_step["goal"]
    assert "Occurrence" in source_step["goal"]
    assert {"流程", "SFMEA", "黑盒测试用例"}.issubset(set(source_step["report_sections"]))
    assert source_step["source_context_limit"] >= 36
    assert source_step["source_context_min_test_files"] >= 6
    assert source_step["source_analysis_max_files"] == source_step["source_context_limit"]
    assert source_step["source_analysis_max_evidence_anchors"] == source_step["source_context_limit"]
    evidence_hints = source_step["source_evidence_hints"]
    assert {item["term"] for item in evidence_hints}.issuperset(
        {
            "iscsi_auth_params",
            "iscsi_conn_login_pdu_err_complete",
            "iscsi_op_login_rsp_handle_csg_bit",
            "iscsi_pdu_payload_op_login",
            "ISCSI_LOGIN_AUTHENT_FAIL",
            "ISCSI_LOGIN_TIMEOUT",
            "configuring initiator with biderectional authentication",
            "HeaderDigest",
            "fuzz_iscsi_send_login_request",
            "login_timer = SPDK_POLLER_REGISTER",
            "_iscsi_conn_destruct",
            "conn->state == ISCSI_CONN_STATE_EXITING",
            "this PDU should be sent without digest",
            "append_iscsi_sess",
            "sess->connections >= sess->MaxConnections",
            "TODO: need a mutex",
            "iscsi_copy_param2var",
            "data digest error",
            "header digest error",
            "LOGIN and LOGOUT opcodes are ignored here",
            "ISCSI_LOGIN_UNSUPPORTED_VERSION",
            "ISCSI_LOGIN_AUTHORIZATION_FAIL",
            "Set T/CSG/NSG to reserved if login error",
            "case ISCSI_FULL_FEATURE_PHASE",
            "--max-connections-per-session",
            "iscsi_parse_params",
            "iscsi_op_login_session_normal",
            "iscsi_conn_info_json",
            "iscsi_parse_param",
        }
    )
    assert all(not item["path"].startswith("test/nvmf/") for item in evidence_hints)
    assert {item["artifact"] for item in source_only["outputs"]} >= {"source_scope.json", "evidence_cards.json", "flow_cards.json", "sfmea.json", "black_box_cases.json", "report.md"}

    with_design = by_id["basic_source_design_report_builtin"]["definition"]
    assert with_design["description"] == by_id["basic_source_design_report_builtin"]["description"]
    assert with_design["execution_subject"] == "builtin_llm"
    assert with_design["execution_label"] == "内置模型"
    assert [item["id"] for item in with_design["inputs"]] == [
        "repo_path",
        "analysis_target",
        "design_doc",
    ]
    assert with_design["inputs"][2] == {
        "id": "design_doc",
        "label": "开发设计文档",
        "type": "file",
        "required": True,
        "resolver": "local",
        "role": "iSCSI login 设计约束与外部行为",
    }
    builtin_step = with_design["steps"][0]
    assert builtin_step["provider"] == "builtin-llm"
    assert builtin_step["execution_mode"] == "staged"
    assert "mcp_profile" not in builtin_step
    assert builtin_step["required_artifacts"] == source_step["required_artifacts"]
    assert builtin_step["input_ports"] == [
        {"id": "repo_path", "type": "directory", "required": True},
        {"id": "analysis_target", "type": "long_text", "required": True},
        {"id": "design_doc", "type": "file", "required": True},
    ]
    assert {item["artifact"] for item in with_design["outputs"]} >= {"source_scope.json", "evidence_cards.json", "flow_cards.json", "sfmea.json", "black_box_cases.json", "report.md"}


def test_source_flow_preset_requires_exact_source_evidence_and_first_pass_quality():
    from app.services.workflow_presets import get_workflow_preset

    definition = get_workflow_preset("source_flow_sfmea_blackbox")["definition"]
    step = definition["steps"][0]
    outputs = {item["id"]: item for item in definition["outputs"]}
    evidence_item = outputs["code_evidence"]["schema"]["items"]

    assert {
        "start_line",
        "end_line",
        "excerpt",
        "sha256",
    }.issubset(set(evidence_item["required"]))
    assert "逐字" in step["goal"]
    assert "禁止省略号" in step["goal"]
    assert "P50/P95" in step["goal"]
    assert "正常保护逻辑" in step["goal"]
    assert "mapped_test_dir" in step["goal"]


def test_builtin_workflow_presets_are_valid_and_cover_core_scenarios():
    from app.services.source_driven_test_design import MINDMAP_ARTIFACTS
    from app.services.workflow_dsl import audit_workflow_definition, validate_workflow_definition
    from app.services.workflow_presets import (
        COMMON_TEST_SCENARIO_PRESET_IDS,
        CORE_WORKFLOW_PRESET_IDS,
        ORIGINAL_CORE_WORKFLOW_PRESET_IDS,
        builtin_workflow_presets,
    )

    presets = builtin_workflow_presets()
    preset_ids = [item["id"] for item in presets]

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
        "nvmf_subsystem_namespace_acl_blackbox",
        "iscsi_lun_resize_hotplug_blackbox",
        "bdev_crypto_integrity_blackbox",
        "scheduler_qos_fairness_blackbox",
        "backup_restore_integrity_blackbox",
        "api_contract_negative_blackbox",
        "state_persistence_restart_blackbox",
        "concurrency_isolation_race_blackbox",
        "performance_capacity_regression_blackbox",
        "security_access_control_blackbox",
    }.issubset(set(preset_ids))
    assert ORIGINAL_CORE_WORKFLOW_PRESET_IDS == (
        "module_analysis",
        "resource_leak_hunt",
        "mr_blackbox_test",
        "patch_impact_review",
    )
    assert CORE_WORKFLOW_PRESET_IDS == (
        *ORIGINAL_CORE_WORKFLOW_PRESET_IDS,
        "source_flow_sfmea_blackbox",
        "testing_activity_orchestration",
        "basic_source_report_codex",
        "basic_source_design_report_builtin",
    )
    assert preset_ids[: len(ORIGINAL_CORE_WORKFLOW_PRESET_IDS)] == list(
        ORIGINAL_CORE_WORKFLOW_PRESET_IDS
    )
    assert preset_ids[: len(CORE_WORKFLOW_PRESET_IDS)] == list(CORE_WORKFLOW_PRESET_IDS)
    assert set(CORE_WORKFLOW_PRESET_IDS).isdisjoint(COMMON_TEST_SCENARIO_PRESET_IDS)
    assert set(COMMON_TEST_SCENARIO_PRESET_IDS).issubset(set(preset_ids))

    for preset in presets:
        if preset["id"] in CORE_WORKFLOW_PRESET_IDS:
            assert preset.get("group") == "core"
        elif preset["id"] in COMMON_TEST_SCENARIO_PRESET_IDS:
            assert preset.get("group") == "common_test_scenario"
        workflow = validate_workflow_definition(preset["definition"])
        assert workflow.id == preset["definition"]["id"]
        assert workflow.steps
        assert workflow.outputs
        assert audit_workflow_definition(preset["definition"])["warnings"] == []

    source_flow_preset = next(
        item for item in presets if item["id"] == "source_flow_sfmea_blackbox"
    )
    source_flow_step = source_flow_preset["definition"]["steps"][0]
    assert source_flow_step["type"] == "agent_task"
    assert source_flow_step["provider"] == "builtin-llm"
    assert source_flow_step["execution_mode"] == "staged"
    assert source_flow_step["mcp_profile"] == "codehub-mcp"
    assert source_flow_step["source_context_limit"] == 44
    assert source_flow_step["source_context_min_test_files"] == 6
    assert source_flow_step["source_analysis_max_files"] == 6
    assert source_flow_step["source_analysis_max_evidence_anchors"] == 12
    assert source_flow_step["source_analysis_min_test_files"] == 3
    assert "may state only what its exact quote directly establishes" in source_flow_step["goal"]
    assert "flow_map.md must name at least one existing repository test path" in source_flow_step["goal"]
    assert "test_strategy.md must name at least one existing repository source path" in source_flow_step["goal"]
    assert {
        "module_map.md",
        "test_strategy.md",
    }.issubset(set(source_flow_step["required_artifacts"]))
    assert {
        "source-evidence-first",
        "storage-flow-analysis",
        "sfmea",
        "black-box-test-design",
        "artifact-contract",
    }.issubset(set(source_flow_step["skills"]))
    source_flow_outputs = {
        item["id"]: item for item in source_flow_preset["definition"]["outputs"]
    }
    assert source_flow_outputs["code_evidence"]["schema"]["minItems"] == 1
    assert "minItems" not in (
        source_flow_outputs["code_evidence"]["schema"]["items"]["properties"]["symbols"]
    )
    assert source_flow_outputs["sfmea"]["schema"]["minItems"] == 1
    assert source_flow_outputs["sfmea"]["min_sfmea_rows"] == 12
    assert source_flow_outputs["black_box_cases"]["schema"]["minItems"] == 1
    assert source_flow_outputs["black_box_cases"]["min_black_box_cases"] == 12
    assert source_flow_outputs["module_map"]["artifact"] == "module_map.md"
    assert source_flow_outputs["test_strategy"]["artifact"] == "test_strategy.md"
    assert source_flow_outputs["test_design_mindmap"]["artifact"] == MINDMAP_ARTIFACTS[0]
    assert source_flow_outputs["test_design_mindmap"]["default_enabled"] is False
    sfmea_required = source_flow_outputs["sfmea"]["schema"]["items"]["required"]
    assert "source_evidence" in sfmea_required
    assert "test_mapping" in sfmea_required
    black_box_required = source_flow_outputs["black_box_cases"]["schema"]["items"]["required"]
    assert "scenario_name" in black_box_required

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

    module_preset = next(item for item in presets if item["id"] == "module_analysis")
    module_steps = {item["id"]: item for item in module_preset["definition"]["steps"]}
    assert module_steps["discover_scope"]["type"] == "local_scope_discover"
    assert module_steps["analyze_module"]["type"] == "agent_task"
    assert module_steps["analyze_module"]["provider"] == "claude-code"
    assert module_steps["analyze_module"]["mcp_profile"] == "codehub-mcp"
    assert "source-evidence-first" in module_steps["analyze_module"]["skills"]
    assert module_steps["analyze_module"]["required_artifacts"] == [
        "module_analysis.md"
    ]
    assert module_steps["validate_evidence"]["type"] == "evidence_validate"
    module_report = next(
        item
        for item in module_preset["definition"]["outputs"]
        if item["id"] == "report"
    )
    assert module_report["from"] == "analyze_module"
    assert module_report["artifact"] == "module_analysis.md"

    testing_activity_preset = next(
        item for item in presets if item["id"] == "testing_activity_orchestration"
    )
    testing_activity_step = next(
        item
        for item in testing_activity_preset["definition"]["steps"]
        if item["id"] == "plan_testing_activity"
    )
    assert "black-box-test-design" in testing_activity_step["skills"]
    assert "black_box_cases.json" in testing_activity_step["required_artifacts"]
    testing_activity_black_box_output = next(
        item
        for item in testing_activity_preset["definition"]["outputs"]
        if item["id"] == "black_box_cases"
    )
    assert testing_activity_black_box_output["type"] == "test_cases"
    assert testing_activity_black_box_output["artifact"] == "black_box_cases.json"
    assert testing_activity_black_box_output["semantic_import"]["enabled"] is True

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


def test_restore_builtin_workflow_presets_refreshes_active_release_definition(tmp_path):
    from app.services.workflow_dsl import WorkflowStore, audit_workflow_definition
    from app.services.workflow_presets import (
        restore_builtin_workflow_presets,
    )

    store = WorkflowStore(tmp_path / "workflows.db")
    store.save_workflow({
        "id": "source_flow_sfmea_blackbox",
        "name": "Stale Release Workflow",
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

    stale = store.get_workflow("source_flow_sfmea_blackbox")
    assert any(
        warning["code"] == "json_output_missing_schema"
        for warning in audit_workflow_definition(stale.raw)["warnings"]
    )

    restore_builtin_workflow_presets(store)

    restored = store.get_workflow("source_flow_sfmea_blackbox")
    assert restored.name == "代码分析 -> 流程 -> SFMEA -> 黑盒用例"
    assert audit_workflow_definition(restored.raw)["warnings"] == []
    assert store.get_workflow("custom_workflow").name == "Custom Workflow"
    ids = {item.id for item in store.list_workflows()}
    assert "source_flow_sfmea_blackbox" in ids
    assert "custom_workflow" in ids


def test_workflow_preset_can_be_installed_into_store(tmp_path):
    from app.services.workflow_dsl import WorkflowStore
    from app.services.workflow_presets import install_workflow_preset

    store = WorkflowStore(tmp_path / "workflows.db")
    workflow = install_workflow_preset(store, "patch_impact_review")

    assert workflow.id == "patch_impact_review"
    assert store.get_workflow("patch_impact_review").name == "Patch Impact Review"


def test_black_box_schema_exposes_oracle_basis_for_runtime_quality_gate():
    from app.services.workflow_presets import BLACK_BOX_CASES_SCHEMA

    properties = BLACK_BOX_CASES_SCHEMA["items"]["properties"]
    assert properties["oracle_basis"] == {"type": "string"}


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
                "type": "combined_test_report",
                    "from": "agent",
                    "artifact": "C:/outside/report.md",
                }
            ],
        })
