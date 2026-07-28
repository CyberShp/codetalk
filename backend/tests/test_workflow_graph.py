import copy

import pytest


def _capabilities() -> dict:
    return {
        "providers": {
            "codex": {"available": True, "mcp_profiles": ["gitnexus"]},
            "builtin-llm": {"available": True, "mcp_profiles": []},
        },
        "skills": ["source-evidence-first", "sfmea"],
    }


def _graph() -> dict:
    return {
        "schema_version": 2,
        "workflow_id": "source_flow",
        "name": "Source flow",
        "description": "Analyze source and render report",
        "nodes": [
            {
                "id": "report",
                "kind": "output",
                "label": "Report",
                "position": {"x": 900, "y": 100},
                "config": {
                    "output_id": "report",
                    "type": "markdown",
                    "artifact": "report.md",
                    "required": True,
                    "source_node_id": "render",
                    "source_port_id": "report",
                },
            },
            {
                "id": "render",
                "kind": "agent",
                "label": "Render",
                "position": {"x": 600, "y": 100},
                "config": {
                    "step_id": "render",
                    "goal": "render a source-backed report",
                    "provider": "builtin-llm",
                    "mcp_profiles": [],
                    "skill_ids": ["sfmea"],
                    "required_artifacts": ["report.md"],
                    "input_ports": [{"id": "analysis", "type": "markdown", "required": True}],
                    "output_ports": [{"id": "report", "type": "markdown"}],
                    "timeout_sec": 900,
                    "failure_policy": "stop",
                },
            },
            {
                "id": "repo",
                "kind": "input",
                "label": "Repository",
                "position": {"x": 0, "y": 100},
                "config": {
                    "contract_id": "repo_path",
                    "label": "Repository",
                    "type": "directory",
                    "required": True,
                    "resolver": "workspace",
                    "role": "source repository",
                },
            },
            {
                "id": "analyze",
                "kind": "agent",
                "label": "Analyze",
                "position": {"x": 300, "y": 100},
                "config": {
                    "step_id": "analyze",
                    "goal": "analyze source",
                    "provider": "codex",
                    "mcp_profiles": ["gitnexus"],
                    "skill_ids": ["source-evidence-first"],
                    "required_artifacts": [],
                    "input_ports": [{"id": "repo_path", "type": "directory", "required": True}],
                    "output_ports": [{"id": "analysis", "type": "markdown"}],
                    "timeout_sec": 900,
                    "failure_policy": "continue_independent",
                },
            },
        ],
        "edges": [
            {
                "id": "edge-render-report",
                "kind": "data",
                "source": {"node_id": "render", "port_id": "report"},
                "target": {"node_id": "report", "port_id": "value"},
            },
            {
                "id": "edge-repo-analyze",
                "kind": "data",
                "source": {"node_id": "repo", "port_id": "value"},
                "target": {"node_id": "analyze", "port_id": "repo_path"},
            },
            {
                "id": "edge-analyze-render",
                "kind": "data",
                "source": {"node_id": "analyze", "port_id": "analysis"},
                "target": {"node_id": "render", "port_id": "analysis"},
            },
        ],
        "settings": {"stop_on_error": True, "max_parallelism": 1},
    }


def _v3_graph(*, handler_id: str = "agent") -> dict:
    """Minimal V3 graph used only to verify graph-module schema dispatch."""
    return {
        "schema_version": 3,
        "workflow_id": "v3_source_report",
        "name": "V3 source report",
        "description": "",
        "nodes": [
            {
                "id": "input_repo",
                "kind": "input",
                "label": "Repository",
                "position": {"x": 0, "y": 0},
                "ports": {
                    "inputs": [],
                    "outputs": [{"id": "value", "type": "directory"}],
                },
                "config": {
                    "input_id": "repo_path",
                    "type": "directory",
                    "required": True,
                },
            },
            {
                "id": "agent_report",
                "kind": "agent",
                "label": "Write report",
                "position": {"x": 360, "y": 0},
                "ports": {
                    "inputs": [{"id": "repo", "type": "directory", "required": True}],
                    "outputs": [{"id": "report", "type": "artifact", "required": True}],
                },
                "config": {
                    "handler_id": handler_id,
                    "handler_version": 1,
                    "provider_ref": "provider_codex_default",
                    "provider_capabilities_required": ["streaming", "cancellation"],
                    "mcp_profiles": [],
                    "skill_ids": [],
                    "skill_instructions": [],
                    "goal": "Read the repository and produce the declared report.",
                    "prompt_template_version": 1,
                    "prompt_template": "{{node_goal}}\n{{bound_inputs}}\n{{output_contract}}",
                    "input_rendering": {
                        "preserve_user_text_verbatim": True,
                        "binding_order": ["repo"],
                    },
                    "timeout_sec": 1200,
                    "idle_timeout_sec": 180,
                    "retry_policy": {"max_attempts": 1, "backoff_seconds": 0},
                    "failure_policy": "stop",
                    "required_outputs": ["report"],
                },
            },
            {
                "id": "output_report",
                "kind": "output",
                "label": "Report",
                "position": {"x": 720, "y": 0},
                "ports": {
                    "inputs": [{"id": "value", "type": "artifact", "required": True}],
                    "outputs": [],
                },
                "config": {
                    "output_id": "report",
                    "artifact": "report.md",
                    "media_type": "text/markdown",
                    "required": True,
                    "producer_step_id": "agent_report",
                },
            },
        ],
        "edges": [
            {
                "id": "repo-to-agent",
                "kind": "data",
                "source": {"node_id": "input_repo", "port_id": "value"},
                "target": {"node_id": "agent_report", "port_id": "repo"},
            },
            {
                "id": "agent-to-report",
                "kind": "data",
                "source": {"node_id": "agent_report", "port_id": "report"},
                "target": {"node_id": "output_report", "port_id": "value"},
            },
        ],
        "settings": {
            "validation_profile": "artifact_only",
            "stop_on_error": True,
            "max_parallelism": 1,
        },
    }


def _v3_capabilities() -> dict:
    return {
        "handlers": {
            "agent": {"versions": [1]},
            "artifact_exists": {"versions": [1]},
        },
    }


def test_graph_validator_and_compiler_are_deterministic_for_shuffled_nodes():
    from app.services.workflow_graph import compile_workflow_graph, validate_workflow_graph

    graph = _graph()
    validation = validate_workflow_graph(graph, capabilities=_capabilities())
    assert validation == {"valid": True, "errors": [], "warnings": []}

    first = compile_workflow_graph(
        graph, capabilities=_capabilities(), workflow_version_id="wfv_1"
    )
    shuffled = copy.deepcopy(graph)
    shuffled["nodes"].reverse()
    shuffled["edges"].reverse()
    second = compile_workflow_graph(
        shuffled, capabilities=_capabilities(), workflow_version_id="wfv_1"
    )

    assert first == second
    assert first["compiled_plan"]["topological_order"] == ["analyze", "render"]
    assert [item["id"] for item in first["compiled_definition"]["steps"]] == [
        "analyze",
        "render",
    ]
    render = first["compiled_plan"]["nodes"][1]
    assert render["depends_on"] == ["analyze"]
    assert render["resolved_input_bindings"] == {
        "analysis": {"source_node_id": "analyze", "source_port_id": "analysis"}
    }


def test_schema_v2_keeps_the_existing_legacy_graph_compiler_contract():
    """Schema 2 must not be upgraded or interpreted through the V3 contract."""
    from app.services.workflow_graph import compile_workflow_graph, validate_workflow_graph

    graph = _graph()
    validation = validate_workflow_graph(graph, capabilities=_capabilities())
    compiled = compile_workflow_graph(
        graph,
        capabilities=_capabilities(),
        workflow_version_id="wfv_v2_legacy",
        workflow_version_number=7,
    )

    assert validation == {"valid": True, "errors": [], "warnings": []}
    assert compiled["compiled_definition"]["version"] == 7
    assert compiled["compiled_definition"]["inputs"][0]["id"] == "repo_path"
    assert compiled["compiled_plan"]["plan_version"] == 1
    assert "compiled_contract_version" not in compiled["compiled_definition"]
    assert "validation_profile" not in compiled["compiled_plan"]


def test_schema_v3_validation_delegates_to_the_v3_contract_draft_semantics():
    """V3 draft validation is authoritative in workflow_contract_v3, not V2 helpers."""
    from app.services.workflow_contract_v3 import validate_workflow_contract_v3
    from app.services.workflow_graph import validate_workflow_graph

    graph = _v3_graph()
    capabilities = _v3_capabilities()

    assert validate_workflow_graph(graph, capabilities=capabilities) == validate_workflow_contract_v3(
        graph,
        capabilities=capabilities,
        require_executable=False,
    )


def test_schema_v3_compile_delegates_to_the_v3_contract_executable_semantics():
    """V3 compilation must request executable handlers and retain V3's output shape."""
    from app.services.workflow_contract_v3 import compile_workflow_contract_v3
    from app.services.workflow_graph import compile_workflow_graph

    graph = _v3_graph()
    capabilities = _v3_capabilities()

    compiled = compile_workflow_graph(
        graph,
        capabilities=capabilities,
        workflow_version_id="wfv_v3_dispatch",
        workflow_version_number=4,
    )
    assert compiled == compile_workflow_contract_v3(
        graph,
        capabilities=capabilities,
        workflow_version_id="wfv_v3_dispatch",
        workflow_version_number=4,
        require_executable=True,
    )


def test_schema_v3_unregistered_handler_warns_for_draft_but_fails_compile():
    """A V3 draft can be saved, while a runnable workflow must fail closed."""
    from app.services.workflow_graph import (
        WorkflowGraphValidationError,
        compile_workflow_graph,
        validate_workflow_graph,
    )

    graph = _v3_graph(handler_id="handler_not_registered")
    capabilities = _v3_capabilities()

    validation = validate_workflow_graph(graph, capabilities=capabilities)
    assert validation["valid"] is True
    assert {item["code"] for item in validation["warnings"]} == {
        "handler_unavailable_draft"
    }

    with pytest.raises(WorkflowGraphValidationError) as exc_info:
        compile_workflow_graph(
            graph,
            capabilities=capabilities,
            workflow_version_id="wfv_v3_handler_missing",
        )
    assert {item["code"] for item in exc_info.value.validation["errors"]} == {
        "handler_unavailable"
    }


def test_unknown_graph_schema_fails_closed_without_v2_or_v3_inference():
    from app.services.workflow_graph import WorkflowGraphValidationError, compile_workflow_graph, validate_workflow_graph

    graph = _graph()
    graph["schema_version"] = 99

    validation = validate_workflow_graph(graph, capabilities=_capabilities())
    assert validation["valid"] is False
    assert validation["errors"] == [
        {
            "code": "schema_version_unsupported",
            "message": "Unsupported authoring graph schema_version: 99",
            "field": "schema_version",
        }
    ]
    with pytest.raises(WorkflowGraphValidationError):
        compile_workflow_graph(
            graph,
            capabilities=_capabilities(),
            workflow_version_id="wfv_unknown_schema",
        )


def test_node_registry_is_the_authoritative_graph_kind_and_ui_schema_source():
    from app.services.workflow_graph import SUPPORTED_NODE_KINDS
    from app.services.workflow_node_registry import node_registry_payload

    registry = node_registry_payload(schema_version=2)

    assert registry["schema_version"] == 1
    assert {item["kind"] for item in registry["nodes"]} == SUPPORTED_NODE_KINDS - {
        "tool",
        "human_approval",
        "subagent",
    }
    agent = next(item for item in registry["nodes"] if item["kind"] == "agent")
    assert agent["ui"]["palette_label"] == "智能体模块"
    assert agent["config_schema"]["input_ports"]["type"] == "port_list"
    assert agent["config_schema"]["output_ports"]["type"] == "port_list"
    assert agent["ui_schema"]["inspector"]["field_order"] == list(agent["config_schema"])
    assert agent["default_ports"]["input_ports"]
    assert agent["default_config"]["provider"] == "builtin-llm"


def test_registry_describes_editable_builtin_step_fields_without_frontend_kind_branches():
    from app.services.workflow_node_registry import node_registry_payload

    registry = node_registry_payload(schema_version=2)
    semantic = next(item for item in registry["nodes"] if item["kind"] == "semantic_retrieve")

    assert semantic["config_schema"]["step_id"]["type"] == "string"
    assert semantic["config_schema"]["timeout_sec"]["type"] == "integer"
    assert semantic["config_schema"]["failure_policy"]["type"] == "enum"


def test_graph_compiles_declared_execution_profiles_into_immutable_contracts():
    from app.services.workflow_graph import compile_workflow_graph, validate_workflow_graph

    graph = _graph()
    graph["settings"]["execution_profiles"] = [
        {
            "id": "rapid",
            "label": "速度型",
            "delivery_class": "bounded_analysis",
            "expected_duration_minutes": [10, 25],
            "max_subagents": 1,
            "stage_overrides": {"independent_judge": {"required": False}},
        },
        {
            "id": "deep",
            "label": "深度型",
            "delivery_class": "full_test_delivery",
            "expected_duration_minutes": [45, 90],
            "max_subagents": 4,
            "stage_overrides": {"independent_judge": {"required": True}},
        },
    ]
    graph["settings"]["default_execution_profile"] = "rapid"

    validation = validate_workflow_graph(graph, capabilities=_capabilities())
    assert validation == {"valid": True, "errors": [], "warnings": []}

    compiled = compile_workflow_graph(
        graph, capabilities=_capabilities(), workflow_version_id="wfv_profiles"
    )

    for contract in (compiled["compiled_definition"], compiled["compiled_plan"]):
        assert contract["default_execution_profile"] == "rapid"
        assert contract["execution_profiles"] == [
            {
                "id": "rapid",
                "label": "速度型",
                "delivery_class": "bounded_analysis",
                "expected_duration_minutes": [10, 25],
                "max_subagents": 1,
                "stage_overrides": {"independent_judge": {"required": False}},
            },
            {
                "id": "deep",
                "label": "深度型",
                "delivery_class": "full_test_delivery",
                "expected_duration_minutes": [45, 90],
                "max_subagents": 4,
                "stage_overrides": {"independent_judge": {"required": True}},
            },
        ]


def test_default_execution_profiles_match_the_product_time_windows():
    from app.services.workflow_graph import compile_workflow_graph

    compiled = compile_workflow_graph(
        _graph(), capabilities=_capabilities(), workflow_version_id="wfv_default_profiles"
    )

    assert compiled["compiled_definition"]["execution_profiles"] == [
        {
            "id": "rapid",
            "label": "速度型",
            "delivery_class": "bounded_analysis",
            "expected_duration_minutes": [8, 20],
            "max_subagents": 1,
            "stage_overrides": {"independent_judge": {"required": False}},
        },
        {
            "id": "deep",
            "label": "深度型",
            "delivery_class": "full_test_delivery",
            "expected_duration_minutes": [40, 90],
            "max_subagents": 4,
            "stage_overrides": {"independent_judge": {"required": True}},
        },
    ]


def test_graph_validator_reports_cycle_port_type_artifact_and_reachability_errors():
    from app.services.workflow_graph import validate_workflow_graph

    graph = _graph()
    graph["nodes"].append({
        "id": "orphan",
        "kind": "semantic_retrieve",
        "label": "Orphan",
        "position": {"x": 0, "y": 0},
        "config": {"input_ports": [], "output_ports": []},
    })
    graph["edges"].append({
        "id": "edge-cycle",
        "kind": "dependency",
        "source": {"node_id": "render", "port_id": "done"},
        "target": {"node_id": "analyze", "port_id": "start"},
    })
    graph["nodes"][3]["config"]["input_ports"][0]["type"] = "json"
    graph["nodes"][1]["config"]["required_artifacts"] = ["../escape.md"]

    validation = validate_workflow_graph(graph, capabilities=_capabilities())
    codes = {item["code"] for item in validation["errors"]}
    assert {"graph_cycle", "port_type_mismatch", "unsafe_artifact", "orphan_node"} <= codes


def test_graph_validator_reports_provider_mcp_skill_and_required_binding_contracts():
    from app.services.workflow_graph import validate_workflow_graph

    graph = _graph()
    analyze = next(item for item in graph["nodes"] if item["id"] == "analyze")
    analyze["config"].update({
        "goal": "",
        "provider": "missing-provider",
        "mcp_profiles": ["unknown-mcp"],
        "skill_ids": ["unknown-skill"],
    })
    graph["edges"] = [
        edge for edge in graph["edges"] if edge["id"] != "edge-repo-analyze"
    ]

    validation = validate_workflow_graph(graph, capabilities=_capabilities())
    error_codes = {item["code"] for item in validation["errors"]}
    warning_codes = {item["code"] for item in validation["warnings"]}
    assert {"required_input_unbound", "agent_goal_missing", "provider_unknown", "mcp_incompatible"} <= error_codes
    assert "skill_unknown" in warning_codes


def test_graph_accepts_directory_and_file_bound_to_distinct_agent_inputs():
    from app.services.workflow_graph import compile_workflow_graph, validate_workflow_graph

    graph = _graph()
    analyze = next(item for item in graph["nodes"] if item["id"] == "analyze")
    analyze["config"]["input_ports"] = [
        {"id": "repo_path", "type": "directory", "required": True},
        {"id": "design_doc", "type": "file", "required": False},
    ]
    graph["nodes"].append({
        "id": "design_doc",
        "kind": "input",
        "label": "开发设计文档",
        "position": {"x": 0, "y": 260},
        "config": {
            "contract_id": "design_doc",
            "label": "开发设计文档",
            "type": "file",
            "required": False,
            "resolver": "local",
            "role": "可选的开发设计文档",
        },
    })
    graph["edges"].append({
        "id": "edge-design-doc-analyze",
        "kind": "data",
        "source": {"node_id": "design_doc", "port_id": "value"},
        "target": {"node_id": "analyze", "port_id": "design_doc"},
    })

    assert validate_workflow_graph(graph, capabilities=_capabilities())["valid"] is True
    compiled = compile_workflow_graph(
        graph, capabilities=_capabilities(), workflow_version_id="wfv_dual_inputs"
    )
    analyze_plan = next(
        item for item in compiled["compiled_plan"]["nodes"] if item["node_id"] == "analyze"
    )
    assert analyze_plan["resolved_input_bindings"] == {
        "design_doc": {"source_node_id": "design_doc", "source_port_id": "value"},
        "repo_path": {"source_node_id": "repo_path", "source_port_id": "value"},
    }


def test_graph_rejects_multiple_data_edges_to_one_scalar_input():
    from app.services.workflow_graph import validate_workflow_graph

    graph = _graph()
    graph["nodes"].append({
        "id": "backup_repo",
        "kind": "input",
        "label": "备用源码工作区",
        "position": {"x": 0, "y": 260},
        "config": {
            "contract_id": "backup_repo",
            "label": "备用源码工作区",
            "type": "directory",
            "required": False,
            "resolver": "local",
        },
    })
    graph["edges"].append({
        "id": "edge-backup-repo-analyze",
        "kind": "data",
        "source": {"node_id": "backup_repo", "port_id": "value"},
        "target": {"node_id": "analyze", "port_id": "repo_path"},
    })

    validation = validate_workflow_graph(graph, capabilities=_capabilities())
    duplicate = next(
        item
        for item in validation["errors"]
        if item["code"] == "multiple_edges_to_single_input"
    )
    assert duplicate["node_id"] == "analyze"
    assert duplicate["field"] == "repo_path"
    assert duplicate["message"] == "该输入已绑定：analyze.repo_path"


def test_graph_preserves_every_binding_for_a_collection_input():
    from app.services.workflow_graph import compile_workflow_graph, validate_workflow_graph

    graph = _graph()
    analyze = next(item for item in graph["nodes"] if item["id"] == "analyze")
    analyze["config"]["input_ports"] = [
        {"id": "repo_paths", "type": "directory", "required": True, "collection": True}
    ]
    graph["edges"][1]["target"]["port_id"] = "repo_paths"
    graph["nodes"].append({
        "id": "backup_repo",
        "kind": "input",
        "label": "备用源码工作区",
        "position": {"x": 0, "y": 260},
        "config": {
            "contract_id": "backup_repo",
            "label": "备用源码工作区",
            "type": "directory",
            "required": False,
            "resolver": "local",
        },
    })
    graph["edges"].append({
        "id": "edge-backup-repo-analyze",
        "kind": "data",
        "source": {"node_id": "backup_repo", "port_id": "value"},
        "target": {"node_id": "analyze", "port_id": "repo_paths"},
    })

    assert validate_workflow_graph(graph, capabilities=_capabilities())["valid"] is True
    compiled = compile_workflow_graph(
        graph, capabilities=_capabilities(), workflow_version_id="wfv_collection"
    )
    analyze_plan = next(
        item for item in compiled["compiled_plan"]["nodes"] if item["node_id"] == "analyze"
    )
    assert analyze_plan["resolved_input_bindings"]["repo_paths"] == [
        {"source_node_id": "backup_repo", "source_port_id": "value"},
        {"source_node_id": "repo_path", "source_port_id": "value"},
    ]


def test_graph_rejects_empty_unsafe_and_duplicate_agent_input_port_ids():
    from app.services.workflow_graph import validate_workflow_graph

    graph = _graph()
    analyze = next(item for item in graph["nodes"] if item["id"] == "analyze")
    analyze["config"]["input_ports"] = [
        {"id": "repo_path", "type": "directory", "required": True},
        {"id": "repo_path", "type": "file", "required": False},
        {"id": "bad port", "type": "file", "required": False},
        {"id": "", "type": "file", "required": False},
    ]

    validation = validate_workflow_graph(graph, capabilities=_capabilities())
    codes = {item["code"] for item in validation["errors"]}
    assert {
        "duplicate_input_port_id",
        "input_port_id_invalid",
        "input_port_id_missing",
    } <= codes


def test_runner_resolves_every_collection_binding_in_compiled_order():
    from app.services.workbench_workflow_runner import _resolve_plan_node_inputs

    resolved = _resolve_plan_node_inputs(
        plan_node={
            "resolved_input_bindings": {
                "documents": [
                    {"source_node_id": "requirements", "source_port_id": "value"},
                    {"source_node_id": "design", "source_port_id": "value"},
                ]
            }
        },
        input_snapshot={"requirements": "req.md", "design": "design.md"},
        direct_dependency_outputs={},
    )

    assert resolved == {"documents": ["req.md", "design.md"]}


def test_legacy_compiler_preserves_historical_sequential_execution():
    from app.services.workflow_graph import compile_legacy_workflow

    legacy = {
        "id": "legacy",
        "name": "Legacy",
        "version": 4,
        "inputs": [],
        "steps": [
            {"id": "second-by-name", "type": "report_render"},
            {"id": "first-by-name", "type": "evidence_validate"},
        ],
        "outputs": [],
    }
    compiled = compile_legacy_workflow(legacy, workflow_version_id="legacy-v4")
    assert compiled["topological_order"] == ["second-by-name", "first-by-name"]
    assert compiled["nodes"][1]["depends_on"] == ["second-by-name"]


def test_graph_compiler_preserves_mindmap_companion_artifacts():
    from app.services.workflow_graph import compile_workflow_graph

    graph = _graph()
    report = next(node for node in graph["nodes"] if node["id"] == "report")
    report["config"].update(
        {
            "output_id": "test_design_mindmap",
            "label": "测试设计脑图",
            "type": "test_design_mindmap",
            "artifact": "test_design_mindmap.json",
            "companion_artifacts": [
                "test_design_mindmap.html",
                "test_design_mindmap.svg",
            ],
        }
    )
    render = next(node for node in graph["nodes"] if node["id"] == "render")
    render["config"]["output_ports"] = [
        {"id": "report", "type": "test_design_mindmap"}
    ]
    render["config"]["required_artifacts"] = ["test_design_mindmap.json"]

    compiled = compile_workflow_graph(
        graph,
        capabilities=_capabilities(),
        workflow_version_id="wfv-mindmap",
    )

    output = compiled["compiled_definition"]["outputs"][0]
    assert output["type"] == "test_design_mindmap"
    assert output["companion_artifacts"] == [
        "test_design_mindmap.html",
        "test_design_mindmap.svg",
    ]
