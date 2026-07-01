"""Built-in editable workflow presets for the Agent Workbench."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.workflow_dsl import WorkflowDefinition, WorkflowStore, validate_workflow_definition


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
                    {"id": "scope", "type": "json", "from": "discover_scope"},
                    {"id": "evidence_cards", "type": "json", "from": "discover_scope", "artifact": "evidence_cards.json"},
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
                    {"id": "evidence_cards", "type": "json", "from": "hunt_risks", "artifact": "evidence_cards.json"},
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
                    {"id": "source_scope", "type": "json", "from": "analyze_source_flow", "artifact": "source_scope.json"},
                    {"id": "code_evidence", "type": "json", "from": "analyze_source_flow", "artifact": "evidence_cards.json"},
                    {"id": "flow_map", "type": "markdown", "from": "analyze_source_flow", "artifact": "flow_map.md"},
                    {"id": "sfmea", "type": "json", "from": "analyze_source_flow", "artifact": "sfmea.json"},
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
        {
            "id": "mr_blackbox_test",
            "name": "MR Black-box Test Design",
            "description": "Let the Agent CLI fetch MR context through its MCP credentials, then validate artifacts and produce black-box test cases.",
            "definition": {
                "id": "mr_blackbox_test",
                "name": "MR Black-box Test Design",
                "version": 1,
                "inputs": [
                    {"id": "mr_link", "type": "mr_link", "required": False, "resolver": "agent_mcp", "role": "merge request URL"},
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
                    {"id": "mr_scope", "type": "json", "from": "collect_mr", "artifact": "mr_snapshot.json"},
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
