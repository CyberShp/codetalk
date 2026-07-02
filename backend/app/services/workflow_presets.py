"""Built-in editable workflow presets for the Agent Workbench."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.workflow_dsl import WorkflowDefinition, WorkflowStore, validate_workflow_definition


SOURCE_SCOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["scope_id", "query", "repo", "discovery", "files", "entry_points"],
    "properties": {
        "scope_id": {"type": "string"},
        "query": {"type": "string"},
        "repo": {"type": "string"},
        "discovery": {
            "type": "object",
            "required": ["provider", "method", "file_count"],
            "properties": {
                "provider": {"type": "string"},
                "method": {"type": "string"},
                "file_count": {"type": "integer"},
            },
            "additionalProperties": True,
        },
        "files": {"type": "array", "items": {"type": "string"}},
        "entry_points": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["file_path", "symbol", "reason"],
                "properties": {
                    "file_path": {"type": "string"},
                    "symbol": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "additionalProperties": True,
            },
        },
    },
    "additionalProperties": True,
}


EVIDENCE_CARDS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["evidence_id", "kind", "file_path", "symbols", "reason", "source"],
        "properties": {
            "evidence_id": {"type": "string"},
            "kind": {"type": "string"},
            "file_path": {"type": "string"},
            "symbols": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"},
            "sha256": {"type": "string"},
            "line_count": {"type": "integer"},
            "source": {"type": "string"},
        },
        "additionalProperties": True,
    },
}


SFMEA_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "required": [
            "failure_mode",
            "cause",
            "effect",
            "detection",
            "severity",
            "occurrence",
            "detection_score",
            "rpn",
            "mitigation",
        ],
        "properties": {
            "sfmea_id": {"type": "string"},
            "module": {"type": "string"},
            "file_path": {"type": "string"},
            "failure_mode": {"type": "string"},
            "cause": {"type": "string"},
            "effect": {"type": "string"},
            "detection": {"type": "string"},
            "severity": {"type": "integer"},
            "occurrence": {"type": "integer"},
            "detection_score": {"type": "integer"},
            "rpn": {"type": "integer"},
            "score_explanation": {"type": "string"},
            "mitigation": {"type": "string"},
            "evidence": {"type": "object"},
        },
        "additionalProperties": True,
    },
}


RISK_FINDINGS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["finding_id", "file_path", "risk", "summary", "source"],
        "properties": {
            "finding_id": {"type": "string"},
            "file_path": {"type": "string"},
            "function": {"type": "string"},
            "resource": {"type": "string"},
            "risk_pattern": {"type": "string"},
            "risk": {"type": "string"},
            "summary": {"type": "string"},
            "severity": {"type": "string"},
            "confidence": {"type": "string"},
            "source": {"type": "string"},
        },
        "additionalProperties": True,
    },
}


MR_SNAPSHOT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["kind", "source", "status", "summary"],
    "properties": {
        "kind": {"type": "string"},
        "source": {"type": "string"},
        "status": {"type": "string"},
        "mr_link": {"type": "string"},
        "repo": {"type": "string"},
        "changed_files_count": {"type": "integer"},
        "changed_files": {"type": "array"},
        "summary": {"type": "string"},
    },
    "additionalProperties": True,
}


IMPACT_SCOPE_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["impact_id", "file_path", "summary", "impact", "risk", "source"],
        "properties": {
            "impact_id": {"type": "string"},
            "file_path": {"type": "string"},
            "symbol": {"type": "string"},
            "status": {"type": "string"},
            "module": {"type": "string"},
            "summary": {"type": "string"},
            "impact": {"type": "string"},
            "risk": {"type": "string"},
            "test_scope": {"type": "string"},
            "source": {"type": "string"},
            "evidence": {"type": "object"},
        },
        "additionalProperties": True,
    },
}


SOURCE_FLOW_REQUIRED_ARTIFACTS = [
    "source_scope.json",
    "evidence_cards.json",
    "flow_map.md",
    "sfmea.json",
    "black_box_cases.json",
]


def _source_flow_outputs(tag: str) -> list[dict[str, Any]]:
    return [
        {
            "id": "source_scope",
            "type": "json",
            "from": "analyze_source_flow",
            "artifact": "source_scope.json",
            "schema": SOURCE_SCOPE_SCHEMA,
        },
        {
            "id": "code_evidence",
            "type": "json",
            "from": "analyze_source_flow",
            "artifact": "evidence_cards.json",
            "schema": EVIDENCE_CARDS_SCHEMA,
        },
        {
            "id": "flow_map",
            "type": "markdown",
            "from": "analyze_source_flow",
            "artifact": "flow_map.md",
        },
        {
            "id": "sfmea",
            "type": "json",
            "from": "analyze_source_flow",
            "artifact": "sfmea.json",
            "schema": SFMEA_SCHEMA,
        },
        {
            "id": "black_box_cases",
            "type": "test_cases",
            "from": "analyze_source_flow",
            "artifact": "black_box_cases.json",
            "semantic_import": {
                "enabled": True,
                "defaults": {
                    "test_level": "black_box",
                    "tags": [tag],
                },
            },
        },
        {"id": "report", "type": "markdown", "from": "render_report"},
    ]


def _source_flow_scenario_preset(
    *,
    preset_id: str,
    name: str,
    description: str,
    default_query: str,
) -> dict[str, Any]:
    return {
        "id": preset_id,
        "name": name,
        "description": description,
        "definition": {
            "id": preset_id,
            "name": name,
            "version": 1,
            "inputs": [
                {
                    "id": "analysis_object",
                    "type": "free_text",
                    "required": False,
                    "role": "optional override for the preset scenario scope",
                },
                {"id": "repo_path", "type": "directory", "required": True, "resolver": "local"},
                {"id": "requirements_doc", "type": "file", "required": False, "role": "requirements"},
                {"id": "design_doc", "type": "file", "required": False, "role": "design"},
                {"id": "coverage_report", "type": "coverage_report", "required": False, "role": "coverage context"},
                {"id": "semantic_library_ref", "type": "semantic_library_ref", "required": False, "role": "test terminology"},
            ],
            "steps": [
                {
                    "id": "analyze_source_flow",
                    "type": "local_source_flow_sfmea_blackbox",
                    "goal": (
                        "Run a scenario-focused source evidence, flow, SFMEA, and black-box "
                        "test generation chain. Check GitNexus/CGC artifacts first when present."
                    ),
                    "default_query": default_query,
                    "required_artifacts": SOURCE_FLOW_REQUIRED_ARTIFACTS,
                },
                {"id": "validate_evidence", "type": "evidence_validate"},
                {"id": "render_report", "type": "report_render"},
            ],
            "outputs": _source_flow_outputs(preset_id),
        },
    }


def builtin_workflow_presets() -> list[dict[str, Any]]:
    """Return versioned workflow presets users can install and customize."""

    presets = [
        {
            "id": "module_analysis",
            "name": "Module Analysis",
            "description": "Analyze a module, discover source scope, collect evidence, and render a structured report.",
            "definition": {
                "id": "module_analysis",
                "name": "Module Analysis",
                "version": 1,
                "inputs": [
                    {"id": "analysis_object", "type": "free_text", "required": True, "role": "module or feature name"},
                    {"id": "repo_path", "type": "directory", "required": True, "resolver": "local"},
                    {"id": "requirements_doc", "type": "file", "required": False, "role": "requirements"},
                    {"id": "design_doc", "type": "file", "required": False, "role": "design"},
                ],
                "steps": [
                    {
                        "id": "discover_scope",
                        "type": "local_scope_discover",
                        "goal": "Discover source files, symbols, entry points, and evidence for the requested module from the local repository.",
                        "required_artifacts": ["source_scope.json", "evidence_cards.json"],
                    },
                    {"id": "validate_evidence", "type": "evidence_validate"},
                    {"id": "render_report", "type": "report_render"},
                ],
                "outputs": [
                    {
                        "id": "scope",
                        "type": "json",
                        "from": "discover_scope",
                        "schema": SOURCE_SCOPE_SCHEMA,
                    },
                    {
                        "id": "evidence_cards",
                        "type": "json",
                        "from": "discover_scope",
                        "artifact": "evidence_cards.json",
                        "schema": EVIDENCE_CARDS_SCHEMA,
                    },
                    {"id": "report", "type": "markdown", "from": "render_report"},
                ],
            },
        },
        {
            "id": "resource_leak_hunt",
            "name": "Resource Leak and Error Branch Hunt",
            "description": "Find resource leaks, cleanup gaps, and abnormal branch risks without requiring the heavy module template.",
            "definition": {
                "id": "resource_leak_hunt",
                "name": "Resource Leak and Error Branch Hunt",
                "version": 1,
                "inputs": [
                    {"id": "target_scope", "type": "free_text", "required": True, "role": "module, file, or function scope"},
                    {"id": "risk_pattern", "type": "enum", "required": False, "role": "leak, cleanup, exception branch, lifetime"},
                    {"id": "repo_path", "type": "directory", "required": True, "resolver": "local"},
                ],
                "steps": [
                    {
                        "id": "hunt_risks",
                        "type": "local_resource_leak_hunt",
                        "goal": "Find resource acquisition/release pairs, abnormal exits, missing cleanup, and evidence-backed test hooks from the local repository.",
                        "required_artifacts": ["risk_findings.json", "evidence_cards.json", "test_hooks.json"],
                    },
                    {"id": "validate_evidence", "type": "evidence_validate"},
                    {"id": "render_report", "type": "report_render"},
                ],
                "outputs": [
                    {
                        "id": "risk_findings",
                        "type": "json",
                        "from": "hunt_risks",
                        "artifact": "risk_findings.json",
                        "schema": RISK_FINDINGS_SCHEMA,
                        "evidence_memory": {
                            "enabled": True,
                            "kind": "resource_risk_finding",
                            "subject_key_field": "finding_id",
                            "path_field": "file_path",
                            "symbol_field": "function",
                            "status": "candidate_output",
                            "text_fields": ["summary", "risk", "resource", "function"],
                        },
                    },
                    {
                        "id": "evidence_cards",
                        "type": "json",
                        "from": "hunt_risks",
                        "artifact": "evidence_cards.json",
                        "schema": EVIDENCE_CARDS_SCHEMA,
                    },
                    {"id": "report", "type": "markdown", "from": "render_report"},
                ],
            },
        },
        {
            "id": "mr_blackbox_test",
            "name": "MR Black-box Test Design",
            "description": "Let the Agent CLI fetch MR context through its MCP credentials, then validate artifacts and produce black-box test cases.",
            "definition": {
                "id": "mr_blackbox_test",
                "name": "MR Black-box Test Design",
                "version": 1,
                "inputs": [
                    {"id": "mr_link", "type": "mr_link", "required": False, "role": "merge request URL"},
                    {"id": "patch_diff", "type": "patch", "required": False, "role": "local patch diff"},
                    {"id": "repo_path", "type": "directory", "required": False, "resolver": "local"},
                    {"id": "design_doc", "type": "file", "required": False, "role": "design context"},
                    {"id": "coverage_report", "type": "coverage_report", "required": False, "role": "coverage context"},
                    {"id": "semantic_library_ref", "type": "semantic_library_ref", "required": False, "role": "test terminology"},
                ],
                "steps": [
                    {
                        "id": "collect_mr",
                        "type": "local_mr_blackbox_test",
                        "goal": "Collect MR or local patch context and produce black-box cases without editing files.",
                        "required_artifacts": ["mr_snapshot.json", "diff.patch", "changed_files.json", "black_box_cases.json"],
                    },
                    {"id": "semantic_retrieve", "type": "semantic_retrieve"},
                    {"id": "validate_mr_evidence", "type": "evidence_validate"},
                    {"id": "render_blackbox_cases", "type": "report_render"},
                ],
                "outputs": [
                    {
                        "id": "mr_scope",
                        "type": "json",
                        "from": "collect_mr",
                        "artifact": "mr_snapshot.json",
                        "schema": MR_SNAPSHOT_SCHEMA,
                    },
                    {
                        "id": "black_box_cases",
                        "type": "test_cases",
                        "from": "collect_mr",
                        "artifact": "black_box_cases.json",
                        "semantic_import": {
                            "enabled": True,
                            "defaults": {
                                "test_level": "black_box",
                                "tags": ["mr_blackbox_test"],
                            },
                        },
                    },
                ],
            },
        },
        {
            "id": "patch_impact_review",
            "name": "Patch Impact Review",
            "description": "Analyze a patch plan or diff, explain before/after flow changes, impact range, and test recommendations.",
            "definition": {
                "id": "patch_impact_review",
                "name": "Patch Impact Review",
                "version": 1,
                "inputs": [
                    {"id": "patch_plan", "type": "file", "required": False, "role": "patch plan"},
                    {"id": "patch_diff", "type": "patch", "required": False, "role": "patch diff"},
                    {"id": "repo_path", "type": "directory", "required": True, "resolver": "local"},
                ],
                "steps": [
                    {"id": "parse_patch", "type": "diff_parse"},
                    {
                        "id": "analyze_impact",
                        "type": "local_patch_impact_review",
                        "goal": "Explain pre/post flow changes, affected files/symbols, compatibility risks, and test scope from local diff and source evidence.",
                        "required_artifacts": ["impact_scope.json", "flow_delta.json", "test_recommendations.json"],
                    },
                    {"id": "validate_evidence", "type": "evidence_validate"},
                    {"id": "render_report", "type": "report_render"},
                ],
                "outputs": [
                    {
                        "id": "impact_scope",
                        "type": "json",
                        "from": "analyze_impact",
                        "artifact": "impact_scope.json",
                        "schema": IMPACT_SCOPE_SCHEMA,
                        "evidence_memory": {
                            "enabled": True,
                            "kind": "patch_impact_scope",
                            "subject_key_field": "impact_id",
                            "path_field": "file_path",
                            "symbol_field": "symbol",
                            "status": "candidate_output",
                            "text_fields": ["summary", "flow_delta", "impact", "risk", "test_scope"],
                        },
                    },
                    {"id": "report", "type": "markdown", "from": "render_report"},
                ],
            },
        },
        {
            "id": "source_flow_sfmea_blackbox",
            "name": "Code Analysis -> Flow -> SFMEA -> Black-box Cases",
            "description": (
                "Run the workspace-report style chain: source-backed code analysis, flow mapping, "
                "SFMEA, and externally executable black-box test cases. GitNexus and CGC artifacts "
                "are treated as first-priority evidence when present."
            ),
            "definition": {
                "id": "source_flow_sfmea_blackbox",
                "name": "Code Analysis -> Flow -> SFMEA -> Black-box Cases",
                "version": 1,
                "inputs": [
                    {"id": "analysis_object", "type": "free_text", "required": True, "role": "module, feature, or flow under test"},
                    {"id": "repo_path", "type": "directory", "required": True, "resolver": "local"},
                    {"id": "requirements_doc", "type": "file", "required": False, "role": "requirements"},
                    {"id": "design_doc", "type": "file", "required": False, "role": "design"},
                    {"id": "coverage_report", "type": "coverage_report", "required": False, "role": "coverage context"},
                    {"id": "semantic_library_ref", "type": "semantic_library_ref", "required": False, "role": "test terminology"},
                ],
                "steps": [
                    {
                        "id": "analyze_source_flow",
                        "type": "local_source_flow_sfmea_blackbox",
                        "goal": (
                            "First check GitNexus and CGC artifacts when available, then read local source "
                            "evidence to produce code evidence, externally observable flow steps, SFMEA, "
                            "and black-box test cases."
                        ),
                        "required_artifacts": [
                            "source_scope.json",
                            "evidence_cards.json",
                            "flow_map.md",
                            "sfmea.json",
                            "black_box_cases.json",
                        ],
                    },
                    {"id": "validate_evidence", "type": "evidence_validate"},
                    {"id": "render_report", "type": "report_render"},
                ],
                "outputs": [
                    {
                        "id": "source_scope",
                        "type": "json",
                        "from": "analyze_source_flow",
                        "artifact": "source_scope.json",
                        "schema": SOURCE_SCOPE_SCHEMA,
                    },
                    {
                        "id": "code_evidence",
                        "type": "json",
                        "from": "analyze_source_flow",
                        "artifact": "evidence_cards.json",
                        "schema": EVIDENCE_CARDS_SCHEMA,
                    },
                    {"id": "flow_map", "type": "markdown", "from": "analyze_source_flow", "artifact": "flow_map.md"},
                    {
                        "id": "sfmea",
                        "type": "json",
                        "from": "analyze_source_flow",
                        "artifact": "sfmea.json",
                        "schema": SFMEA_SCHEMA,
                    },
                    {
                        "id": "black_box_cases",
                        "type": "test_cases",
                        "from": "analyze_source_flow",
                        "artifact": "black_box_cases.json",
                        "semantic_import": {
                            "enabled": True,
                            "defaults": {
                                "test_level": "black_box",
                                "tags": ["source_flow_sfmea_blackbox"],
                            },
                        },
                    },
                    {"id": "report", "type": "markdown", "from": "render_report"},
                ],
            },
        },
        _source_flow_scenario_preset(
            preset_id="nvmf_connect_io_blackbox",
            name="NVMe-oF Connect / IO Black-box Scenario",
            description=(
                "Analyze SPDK NVMe-oF connect, authentication, queue setup, IO submit, "
                "disconnect/reconnect, timeout, and reset behavior for source-backed SFMEA "
                "and black-box cases."
            ),
            default_query=(
                "lib/nvmf test/nvmf NVMe-oF connect authentication queue setup IO submit "
                "disconnect reconnect timeout controller reset"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="iscsi_login_session_blackbox",
            name="iSCSI Login / Session Black-box Scenario",
            description=(
                "Analyze SPDK iSCSI login, CHAP, digest, multi-connection, session reset, "
                "redirect, and initiator disconnect behavior for SFMEA and black-box cases."
            ),
            default_query=(
                "lib/iscsi test/iscsi_tgt iSCSI login CHAP digest multi-connection session "
                "reset redirect initiator disconnect authentication failure"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="bdev_io_reset_blackbox",
            name="bdev IO / Reset Black-box Scenario",
            description=(
                "Analyze SPDK bdev open, submit, complete, error returns, pending reset, "
                "IO drain, reconnect, failover, and resource pressure behavior."
            ),
            default_query=(
                "lib/bdev module/bdev test/bdev bdev open submit complete error return "
                "pending reset IO drain reconnect failover resource pressure"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="rpc_config_negative_blackbox",
            name="RPC / Config Negative Black-box Scenario",
            description=(
                "Analyze public RPC/config flows for invalid parameters, repeated calls, "
                "ordering errors, partial success, rollback, idempotency, and diagnostics."
            ),
            default_query=(
                "rpc config app test/json_config invalid parameter repeated call ordering "
                "partial success rollback idempotency diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="reactor_thread_poller_blackbox",
            name="Reactor / Thread / Poller Black-box Scenario",
            description=(
                "Analyze reactor, thread, message passing, poller scheduling, blocking pollers, "
                "long task dispatch, concurrency, recovery, and performance degradation."
            ),
            default_query=(
                "lib/thread lib/event lib/scheduler test/thread reactor thread message poller "
                "scheduling blocking long task concurrency recovery performance degradation"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="nvmf_disconnect_reconnect_blackbox",
            name="NVMe-oF Disconnect / Reconnect Black-box Scenario",
            description=(
                "Analyze SPDK NVMe-oF timeout, disconnect, reconnect, keep-alive, controller "
                "reset, qpair teardown, and recovery behavior for source-backed SFMEA and "
                "black-box cases."
            ),
            default_query=(
                "lib/nvmf test/nvmf NVMe-oF keep alive timeout disconnect reconnect "
                "controller reset qpair teardown transport error recovery"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="iscsi_auth_failure_blackbox",
            name="iSCSI Auth Failure / Reset Black-box Scenario",
            description=(
                "Analyze SPDK iSCSI CHAP/authentication failure, redirect, digest mismatch, "
                "session reset, logout, initiator disconnect, and recovery diagnostics."
            ),
            default_query=(
                "lib/iscsi test/iscsi_tgt iSCSI CHAP authentication failure digest mismatch "
                "redirect session reset logout initiator disconnect recovery diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="bdev_failover_resource_blackbox",
            name="bdev Failover / Resource Pressure Black-box Scenario",
            description=(
                "Analyze SPDK bdev failover, reconnect, resource exhaustion, no-memory paths, "
                "I/O drain, reset ordering, and public error reporting."
            ),
            default_query=(
                "lib/bdev module/bdev test/bdev bdev failover reconnect resource exhaustion "
                "no memory IO drain reset ordering public error reporting"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="blobstore_ftl_recovery_blackbox",
            name="Blobstore / FTL Recovery Black-box Scenario",
            description=(
                "Analyze SPDK blobstore and FTL metadata recovery, ENOSPC, abnormal shutdown, "
                "super block consistency, relocation, and restart recovery behavior."
            ),
            default_query=(
                "lib/blob lib/ftl module/bdev/ftl test/blobfs test/ftl blobstore FTL "
                "metadata recovery ENOSPC abnormal shutdown super block consistency restart"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="vhost_vfio_user_lifecycle_blackbox",
            name="vhost / vfio-user Lifecycle Black-box Scenario",
            description=(
                "Analyze SPDK vhost and vfio-user device lifecycle, queue configuration, "
                "guest attach/detach, socket cleanup, reset, and error recovery behavior."
            ),
            default_query=(
                "lib/vhost lib/vfio_user test/vhost test/vfio_user vhost vfio-user device "
                "lifecycle queue configuration guest attach detach socket cleanup reset recovery"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="nvmf_tcp_tls_auth_blackbox",
            name="NVMe/TCP TLS / Authentication Black-box Scenario",
            description=(
                "Analyze SPDK NVMe/TCP TLS and authentication setup, certificate/key mismatch, "
                "secure connection negotiation, fallback denial, reconnect, and public diagnostics."
            ),
            default_query=(
                "lib/nvmf test/nvmf NVMe TCP TLS authentication certificate key mismatch "
                "secure connection negotiation fallback denial reconnect diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="bdev_qos_latency_blackbox",
            name="bdev QoS / Latency Degradation Black-box Scenario",
            description=(
                "Analyze SPDK bdev QoS, rate limiting, queue depth pressure, latency spikes, "
                "timeout reporting, fairness, and recovery under sustained IO load."
            ),
            default_query=(
                "lib/bdev module/bdev test/bdev bdev QoS rate limit queue depth latency "
                "timeout fairness sustained IO load performance degradation recovery"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="jsonrpc_concurrency_idempotency_blackbox",
            name="JSON-RPC Concurrency / Idempotency Black-box Scenario",
            description=(
                "Analyze SPDK public JSON-RPC concurrency, repeated create/delete calls, "
                "idempotency, partial success, ordering races, rollback, and observable errors."
            ),
            default_query=(
                "rpc app test/json_config scripts/rpc.py JSON-RPC concurrency repeated "
                "create delete idempotency partial success ordering race rollback observable error"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="app_startup_shutdown_smoke_blackbox",
            name="App Startup / Shutdown Smoke Black-box Scenario",
            description=(
                "Analyze SPDK application startup, configuration load, RPC readiness, signal "
                "handling, graceful shutdown, restart, and externally visible diagnostics."
            ),
            default_query=(
                "app lib/event scripts/rpc.py test/app test/json_config SPDK application startup "
                "configuration load RPC readiness signal graceful shutdown restart diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="nvme_ctrlr_hotplug_reset_blackbox",
            name="NVMe Controller Hotplug / Reset Black-box Scenario",
            description=(
                "Analyze SPDK NVMe controller attach, identify, reset, timeout, hotremove, "
                "namespace change, reconnect, and public error reporting behavior."
            ),
            default_query=(
                "lib/nvme test/nvme nvme controller attach identify reset timeout hotremove "
                "namespace change reconnect public error reporting"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="storage_capacity_enospc_recovery_blackbox",
            name="Storage Capacity / ENOSPC Recovery Black-box Scenario",
            description=(
                "Analyze capacity pressure, ENOSPC, allocation failure, metadata persistence, "
                "partial write, retry, cleanup, and recovery behavior across SPDK storage layers."
            ),
            default_query=(
                "lib/bdev lib/blob lib/ftl test/bdev test/blobfs capacity pressure ENOSPC "
                "allocation failure metadata persistence partial write retry cleanup recovery"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="nvmf_rdma_transport_blackbox",
            name="NVMe/RDMA Transport Black-box Scenario",
            description=(
                "Analyze NVMe/RDMA connection setup, queue pairs, RDMA CM events, memory "
                "registration, disconnect, retry, error recovery, and public diagnostics."
            ),
            default_query=(
                "lib/nvmf test/nvmf NVMe RDMA transport queue pair RDMA CM event memory "
                "registration disconnect retry error recovery public diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="iscsi_digest_multi_connection_blackbox",
            name="iSCSI Digest / Multi-connection Black-box Scenario",
            description=(
                "Analyze iSCSI header/data digest, multi-connection sessions, connection "
                "migration, digest failure, recovery, and external log/status signals."
            ),
            default_query=(
                "lib/iscsi test/iscsi_tgt iSCSI header digest data digest multi connection "
                "session connection migration digest failure recovery external log status"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="bdev_hotremove_io_error_blackbox",
            name="bdev Hotremove / IO Error Black-box Scenario",
            description=(
                "Analyze bdev hotremove, underlying device loss, IO error reporting, reset, "
                "drain, retry, and externally visible state transitions."
            ),
            default_query=(
                "lib/bdev module/bdev test/bdev bdev hotremove underlying device loss IO "
                "error reporting reset drain retry observable state transition"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="blobstore_metadata_powerfail_blackbox",
            name="Blobstore Metadata / Power-fail Recovery Black-box Scenario",
            description=(
                "Analyze blobstore metadata updates, abnormal shutdown, power-fail restart, "
                "super block and cluster consistency, partial writes, and recovery validation."
            ),
            default_query=(
                "lib/blob test/blobfs blobstore metadata update abnormal shutdown power fail "
                "restart super block cluster consistency partial write recovery validation"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="rpc_security_authz_blackbox",
            name="RPC Security / Authorization Black-box Scenario",
            description=(
                "Analyze RPC exposure, authentication and authorization boundaries, invalid "
                "commands, sensitive parameters, failure audit, replay, and user-visible errors."
            ),
            default_query=(
                "scripts/rpc.py lib/event test/json_config RPC exposure authentication "
                "authorization invalid command sensitive parameter failure audit replay user visible error"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="fault_injection_timeout_recovery_blackbox",
            name="Fault Injection / Timeout Recovery Black-box Scenario",
            description=(
                "Analyze externally triggered fault injection, transport errors, timeout handling, "
                "process restart, retry behavior, cleanup, and recovery diagnostics across storage workflows."
            ),
            default_query=(
                "test/common test/nvmf test/bdev test/json_config lib/nvmf lib/bdev lib/thread "
                "fault injection timeout transport error retry cleanup process restart recovery diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="concurrent_operations_stress_blackbox",
            name="Concurrent Operations / Stress Black-box Scenario",
            description=(
                "Analyze concurrent public operations, create/delete races, connect/disconnect while IO runs, "
                "queue pressure, idempotency, ordering, and externally observable stress failures."
            ),
            default_query=(
                "test/nvmf test/bdev test/json_config lib/nvmf lib/bdev lib/thread rpc concurrency "
                "stress create delete race connect disconnect IO queue pressure idempotency ordering"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="observability_diagnostics_blackbox",
            name="Observability / Diagnostics Black-box Scenario",
            description=(
                "Analyze logs, counters, public status commands, diagnostic artifacts, warning paths, "
                "and failure triage signals that a black-box tester can observe without reading internals."
            ),
            default_query=(
                "lib/log lib/event scripts/rpc.py test/json_config test/common diagnostics logs counters "
                "status command warning failure triage observable metrics artifact"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="config_compatibility_rollback_blackbox",
            name="Config Compatibility / Rollback Black-box Scenario",
            description=(
                "Analyze configuration compatibility, invalid or mixed-version config input, partial apply, "
                "rollback, restart persistence, idempotency, and user-visible diagnostics."
            ),
            default_query=(
                "scripts/rpc.py test/json_config test/app lib/event app config compatibility invalid "
                "mixed version partial apply rollback restart persistence idempotency diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="lvol_snapshot_clone_blackbox",
            name="Logical Volume Snapshot / Clone Black-box Scenario",
            description=(
                "Analyze SPDK lvol create/delete, snapshot, clone, resize, thin provision, "
                "metadata persistence, ENOSPC, and recovery behavior."
            ),
            default_query=(
                "module/bdev/lvol lib/blob test/lvol scripts/rpc.py logical volume lvol "
                "snapshot clone resize thin provision metadata persistence ENOSPC recovery"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="raid_degraded_rebuild_blackbox",
            name="RAID Degraded / Rebuild Black-box Scenario",
            description=(
                "Analyze SPDK RAID create/start/stop, member failure, degraded mode, rebuild, "
                "I/O continuity, resync progress, and external diagnostics."
            ),
            default_query=(
                "module/bdev/raid test/bdev scripts/rpc.py RAID create start stop member "
                "failure degraded rebuild IO continuity resync progress diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="nvme_multipath_failover_blackbox",
            name="NVMe Multipath / Failover Black-box Scenario",
            description=(
                "Analyze NVMe multipath attach, path loss, ANA state changes, failover, reconnect, "
                "I/O continuity, timeout handling, and public status signals."
            ),
            default_query=(
                "lib/nvme module/bdev/nvme test/nvme test/bdev NVMe multipath path loss "
                "ANA failover reconnect IO continuity timeout public status"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="env_hugepage_memory_blackbox",
            name="Environment / Hugepage Memory Black-box Scenario",
            description=(
                "Analyze SPDK environment initialization, hugepage allocation, memory pressure, "
                "invalid launch parameters, cleanup, restart, and observable diagnostics."
            ),
            default_query=(
                "lib/env_dpdk lib/env_ocf test/env app SPDK environment initialization "
                "hugepage memory allocation pressure invalid parameter cleanup restart diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="spdk_cli_rpc_smoke_blackbox",
            name="SPDK CLI / RPC Smoke Black-box Scenario",
            description=(
                "Analyze SPDK public CLI and RPC smoke paths, target startup readiness, "
                "basic create/list/delete operations, invalid commands, and diagnostic output."
            ),
            default_query=(
                "scripts/rpc.py scripts/spdkcli.py test/json_config test/app app lib/event "
                "CLI RPC smoke startup readiness create list delete invalid command diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="basic_lifecycle_smoke_blackbox",
            name="Basic Lifecycle Smoke Black-box Scenario",
            description=(
                "Analyze common create, list, update, delete, restart, and cleanup flows that "
                "black-box testers run before deeper storage validation."
            ),
            default_query=(
                "scripts/rpc.py test/json_config test/app test/bdev app lib/event basic lifecycle "
                "smoke create list update delete restart cleanup readiness diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="io_stress_performance_blackbox",
            name="I/O Stress / Performance Baseline Black-box Scenario",
            description=(
                "Analyze sustained I/O, mixed read/write load, queue depth pressure, latency "
                "regression, throughput baseline, and externally visible degradation signals."
            ),
            default_query=(
                "lib/bdev module/bdev test/bdev test/nvmf scripts/perf.py fio IO stress "
                "performance latency throughput queue depth mixed read write regression baseline"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="failure_recovery_soak_blackbox",
            name="Failure Recovery / Soak Black-box Scenario",
            description=(
                "Analyze long-running reliability scenarios with restart, disconnect, reconnect, "
                "resource pressure, cleanup, and recovery evidence visible to operators."
            ),
            default_query=(
                "test/common test/nvmf test/bdev lib/thread lib/bdev lib/nvmf soak reliability "
                "restart disconnect reconnect resource pressure cleanup recovery long running"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="transport_network_partition_blackbox",
            name="Transport Network Partition Black-box Scenario",
            description=(
                "Analyze transport-level packet loss, network partition, reconnect, timeout, "
                "keep-alive, IO continuity, and externally visible recovery behavior."
            ),
            default_query=(
                "lib/nvmf test/nvmf lib/iscsi test/iscsi_tgt transport packet loss network "
                "partition reconnect timeout keep alive IO continuity recovery diagnostics"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="data_integrity_corruption_blackbox",
            name="Data Integrity / Corruption Black-box Scenario",
            description=(
                "Analyze externally observable data integrity checks, checksum or digest mismatch, "
                "partial write, read-after-write validation, metadata corruption, and recovery signals."
            ),
            default_query=(
                "lib/bdev lib/blob lib/iscsi lib/nvmf test/bdev test/blobfs data integrity "
                "checksum digest mismatch partial write read after write metadata corruption recovery"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="upgrade_compatibility_persistence_blackbox",
            name="Upgrade Compatibility / Persistence Black-box Scenario",
            description=(
                "Analyze upgrade, downgrade, restart persistence, saved configuration compatibility, "
                "metadata versioning, rollback, and user-visible migration diagnostics."
            ),
            default_query=(
                "app lib/event lib/blob lib/ftl scripts/rpc.py test/json_config upgrade downgrade "
                "restart persistence saved configuration compatibility metadata version rollback migration"
            ),
        ),
        _source_flow_scenario_preset(
            preset_id="telemetry_metrics_regression_blackbox",
            name="Telemetry / Metrics Regression Black-box Scenario",
            description=(
                "Analyze telemetry, counters, logs, status commands, metric regressions, alertability, "
                "and failure triage signals available to black-box storage testers."
            ),
            default_query=(
                "lib/trace lib/log lib/event scripts/rpc.py test/common telemetry counters logs "
                "status metrics regression alert diagnostics failure triage observable"
            ),
        ),
    ]
    for preset in presets:
        validate_workflow_definition(preset["definition"])
    return deepcopy(presets)


def get_workflow_preset(preset_id: str) -> dict[str, Any]:
    for preset in builtin_workflow_presets():
        if preset["id"] == preset_id:
            return preset
    raise KeyError(preset_id)


def install_workflow_preset(store: WorkflowStore, preset_id: str) -> WorkflowDefinition:
    preset = get_workflow_preset(preset_id)
    return store.save_workflow(deepcopy(preset["definition"]))


def restore_builtin_workflow_presets(store: WorkflowStore) -> list[WorkflowDefinition]:
    """Install or refresh built-in workflows while preserving custom workflow ids."""

    restored: list[WorkflowDefinition] = []
    presets = builtin_workflow_presets()
    for preset in reversed(presets):
        store.save_workflow(deepcopy(preset["definition"]))
    for preset in presets:
        restored.append(store.get_workflow(str(preset["definition"]["id"])))
    return restored
