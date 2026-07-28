"""Compile-time contracts for explicit Validator and Governance nodes."""

from __future__ import annotations

import copy


def _capabilities() -> dict:
    return {
        "handlers": {
            "agent": {"versions": [1], "kind": "agent"},
            "artifact_exists": {"versions": [1], "kind": "validator"},
            "json_schema": {"versions": [1], "kind": "validator"},
            "source_evidence": {"versions": [1], "kind": "validator"},
            "storage_test_design": {
                "versions": [1],
                "kind": "governance",
                "input_ports": [{
                    "key": "source_evidence", "label": "源码证据",
                    "type": "artifact", "required": True, "collection": False,
                }],
                "output_ports": [
                    {
                        "key": "sfmea", "label": "SFMEA 风险清单",
                        "type": "artifact", "required": True, "collection": False,
                    },
                    {
                        "key": "black_box_cases", "label": "黑盒测试用例",
                        "type": "artifact", "required": True, "collection": False,
                    },
                ],
            },
            "independent_review": {"versions": [1], "kind": "validator"},
        }
    }


def _base_graph() -> dict:
    return {
        "schema_version": 3,
        "workflow_id": "governance-contract",
        "name": "Explicit governance contract",
        "settings": {"validation_profile": "none"},
        "nodes": [
            {
                "id": "repo",
                "kind": "input",
                "label": "Repository",
                "ports": {"inputs": [], "outputs": [{"id": "value", "type": "directory"}]},
                "config": {"input_id": "repo", "type": "directory", "required": True},
            },
            {
                "id": "analyze",
                "kind": "agent",
                "label": "Analyze",
                "ports": {
                    "inputs": [{"id": "repo_path", "type": "directory", "required": True}],
                    "outputs": [
                        {"id": "report", "type": "artifact", "required": True},
                        {"id": "verified_evidence", "type": "artifact", "required": True},
                    ],
                },
                "config": {
                    "handler_id": "agent",
                    "handler_version": 1,
                    "provider_ref": "builtin-llm",
                    "goal": "Analyze only the declared repository input.",
                    "prompt_template": "{{bound_inputs}}",
                },
            },
            {
                "id": "report-output",
                "kind": "output",
                "label": "Report",
                "ports": {"inputs": [{"id": "value", "type": "artifact", "required": True}], "outputs": []},
                "config": {
                    "output_id": "report",
                    "artifact": "report.md",
                    "media_type": "text/markdown",
                    "required": True,
                },
            },
        ],
        "edges": [
            {
                "id": "repo-analyze",
                "kind": "data",
                "source": {"node_id": "repo", "port_id": "value"},
                "target": {"node_id": "analyze", "port_id": "repo_path"},
            },
            {
                "id": "analyze-report",
                "kind": "data",
                "source": {"node_id": "analyze", "port_id": "report"},
                "target": {"node_id": "report-output", "port_id": "value"},
            },
        ],
    }


def _with_governance() -> dict:
    graph = _base_graph()
    graph["nodes"].extend([
        {
            "id": "design-tests",
            "kind": "governance",
            "label": "Storage test design",
            "ports": {
                "inputs": [{
                    "id": "evidence_input_01j", "binding_key": "source_evidence",
                    "type": "artifact", "required": True, "collection": False,
                }],
                "outputs": [
                    {
                        "id": "sfmea", "binding_key": "sfmea",
                        "type": "artifact", "required": True, "collection": False,
                    },
                    {
                        "id": "black_box_cases", "binding_key": "black_box_cases",
                        "type": "artifact", "required": True, "collection": False,
                    },
                ],
            },
            "config": {
                "handler_id": "storage_test_design",
                "handler_version": 1,
                "failure_policy": "stop",
            },
        },
        {
            "id": "sfmea-output",
            "kind": "output",
            "label": "SFMEA",
            "ports": {"inputs": [{"id": "value", "type": "artifact", "required": True}], "outputs": []},
            "config": {
                "output_id": "sfmea",
                "artifact": "sfmea.json",
                "media_type": "application/json",
                "schema": {"type": "array"},
                "required": True,
            },
        },
        {
            "id": "black-box-output",
            "kind": "output",
            "label": "Black-box cases",
            "ports": {"inputs": [{"id": "value", "type": "artifact", "required": True}], "outputs": []},
            "config": {
                "output_id": "black_box_cases",
                "artifact": "black_box_cases.json",
                "media_type": "application/json",
                "schema": {"type": "array"},
                "required": True,
            },
        },
    ])
    graph["edges"].extend([
        {
            "id": "analyze-governance",
            "kind": "data",
            "source": {"node_id": "analyze", "port_id": "verified_evidence"},
            "target": {"node_id": "design-tests", "port_id": "evidence_input_01j"},
        },
        {
            "id": "governance-sfmea",
            "kind": "data",
            "source": {"node_id": "design-tests", "port_id": "sfmea"},
            "target": {"node_id": "sfmea-output", "port_id": "value"},
        },
        {
            "id": "governance-black-box",
            "kind": "data",
            "source": {"node_id": "design-tests", "port_id": "black_box_cases"},
            "target": {"node_id": "black-box-output", "port_id": "value"},
        },
    ])
    return graph


def test_explicit_validator_requires_at_least_one_declared_output_binding():
    from app.services.workflow_contract_v3 import compile_workflow_contract_v3

    graph = _base_graph()
    graph["nodes"].append({
        "id": "noop-validator",
        "kind": "validator",
        "label": "No-op validator",
        "ports": {"inputs": [], "outputs": []},
        "config": {
            "handler_id": "artifact_exists",
            "handler_version": 1,
            "required_outputs": [],
            "blocking": True,
        },
    })

    compiled = compile_workflow_contract_v3(
        graph,
        capabilities=_capabilities(),
        workflow_version_id="wfv-noop-validator",
    )

    assert compiled["compiled_definition"] is None
    issue = next(
        item for item in compiled["validation_result"]["errors"]
        if item["code"] == "validator_required_outputs_empty"
    )
    assert issue["node_id"] == "noop-validator"
    assert issue["field"] == "required_outputs"
    assert "至少选择一个已声明交付件" in issue["message"]


def test_explicit_validator_is_read_only_and_freezes_declared_output_dependencies():
    from app.services.workflow_contract_v3 import compile_workflow_contract_v3

    graph = _base_graph()
    graph["nodes"].append({
        "id": "validate-report",
        "kind": "validator",
        "label": "Validate report",
        "ports": {"inputs": [], "outputs": []},
        "config": {
            "handler_id": "artifact_exists",
            "handler_version": 1,
            "required_outputs": ["report"],
            "blocking": True,
        },
    })

    compiled = compile_workflow_contract_v3(
        graph,
        capabilities=_capabilities(),
        workflow_version_id="wfv-validator",
    )

    assert compiled["validation_result"]["valid"] is True
    validator = next(
        node for node in compiled["compiled_plan"]["nodes"]
        if node["node_id"] == "validate-report"
    )
    assert {
        "kind": validator["kind"],
        "handler_id": validator["handler_id"],
        "handler_version": validator["handler_version"],
        "depends_on": validator["depends_on"],
        "required_outputs": validator["required_outputs"],
        "input_ports": validator["input_ports"],
        "output_ports": validator["output_ports"],
        "blocking": validator["blocking"],
    } == {
        "kind": "validator",
        "handler_id": "artifact_exists",
        "handler_version": 1,
        "depends_on": ["analyze"],
        "required_outputs": ["report"],
        "input_ports": [],
        "output_ports": [],
        "blocking": True,
    }


def test_validator_cannot_declare_generated_output_ports():
    from app.services.workflow_contract_v3 import validate_workflow_contract_v3

    graph = _base_graph()
    graph["nodes"].append({
        "id": "invalid-validator",
        "kind": "validator",
        "ports": {"inputs": [], "outputs": [{"id": "review", "type": "artifact"}]},
        "config": {
            "handler_id": "artifact_exists",
            "handler_version": 1,
            "required_outputs": ["report"],
        },
    })

    result = validate_workflow_contract_v3(
        graph, capabilities=_capabilities(), require_executable=True
    )

    assert any(
        issue["code"] == "validator_output_ports_forbidden"
        and issue.get("node_id") == "invalid-validator"
        for issue in result["errors"]
    )


def test_governance_output_is_declared_connected_and_frozen_as_unique_producer():
    from app.services.workflow_contract_v3 import compile_workflow_contract_v3

    compiled = compile_workflow_contract_v3(
        _with_governance(),
        capabilities=_capabilities(),
        workflow_version_id="wfv-governance",
    )

    assert compiled["validation_result"]["valid"] is True
    definition = compiled["compiled_definition"]
    declared_sfmea = next(
        output for output in definition["declared_outputs"] if output["output_id"] == "sfmea"
    )
    assert declared_sfmea["producer_step_id"] == "design-tests"
    assert declared_sfmea["producer_port_id"] == "sfmea"
    assert declared_sfmea["producer_port_key"] == "sfmea"
    governance = next(
        node for node in compiled["compiled_plan"]["nodes"]
        if node["node_id"] == "design-tests"
    )
    assert governance["kind"] == "governance"
    assert governance["handler_id"] == "storage_test_design"
    assert governance["handler_version"] == 1
    assert governance["depends_on"] == ["analyze"]
    assert governance["resolved_input_bindings"] == {
        "evidence_input_01j": {
            "source_node_id": "analyze",
            "source_port_id": "verified_evidence",
        }
    }
    assert governance["required_outputs"] == ["black_box_cases", "sfmea"]
    assert governance["output_ports"] == [
        {
            "id": "sfmea", "binding_key": "sfmea", "type": "artifact",
            "required": True, "collection": False,
        },
        {
            "id": "black_box_cases", "binding_key": "black_box_cases",
            "type": "artifact", "required": True, "collection": False,
        },
    ]
    assert governance["blocking"] is True


def test_governance_freezes_semantic_port_key_independent_of_ids_and_filename():
    from app.services.workflow_contract_v3 import compile_workflow_contract_v3

    graph = _with_governance()
    governance = next(
        node for node in graph["nodes"] if node["id"] == "design-tests"
    )
    governance["ports"]["outputs"][0]["id"] = "port_01j_random"
    output = next(node for node in graph["nodes"] if node["id"] == "sfmea-output")
    output["config"].update({
        "output_id": "output_01j_random",
        "artifact": "risk-register.json",
    })
    edge = next(edge for edge in graph["edges"] if edge["id"] == "governance-sfmea")
    edge["source"]["port_id"] = "port_01j_random"

    compiled = compile_workflow_contract_v3(
        graph,
        capabilities=_capabilities(),
        workflow_version_id="wfv-governance-semantic-port",
    )

    assert compiled["validation_result"]["valid"] is True
    declared = next(
        item
        for item in compiled["compiled_definition"]["declared_outputs"]
        if item["output_id"] == "output_01j_random"
    )
    assert declared["artifact"] == "risk-register.json"
    assert declared["producer_port_id"] == "port_01j_random"
    assert declared["producer_port_key"] == "sfmea"


def test_storage_test_design_generated_professional_ports_require_json_array_contract_by_semantic_key():
    from app.services.workflow_contract_v3 import compile_workflow_contract_v3

    graph = _with_governance()
    governance = next(
        node for node in graph["nodes"] if node["id"] == "design-tests"
    )
    governance["ports"]["outputs"] = [
        {
            "id": "random-sfmea-port",
            "binding_key": "sfmea",
            "type": "artifact",
            "required": True,
            "collection": False,
        },
        {
            "id": "random-black-box-port",
            "binding_key": "black_box_cases",
            "type": "artifact",
            "required": True,
            "collection": False,
        },
    ]
    sfmea_output = next(node for node in graph["nodes"] if node["id"] == "sfmea-output")
    sfmea_output["config"].update({
        "output_id": "risk_register_custom",
        "artifact": "risk-register.custom",
        "media_type": "text/markdown",
        "schema": {"type": "array"},
    })
    black_box_output = next(
        node for node in graph["nodes"] if node["id"] == "black-box-output"
    )
    black_box_output["label"] = "Custom black box matrix"
    black_box_output["config"].update({
        "output_id": "black_box_custom",
        "artifact": "custom-matrix.data",
        "media_type": "application/json",
        "schema": {"type": "object"},
    })
    graph["edges"] = [
        edge
        for edge in graph["edges"]
        if edge["id"] not in {"governance-sfmea", "governance-black-box"}
    ]
    graph["edges"].extend([
        {
            "id": "governance-sfmea-custom",
            "kind": "data",
            "source": {"node_id": "design-tests", "port_id": "random-sfmea-port"},
            "target": {"node_id": "sfmea-output", "port_id": "value"},
        },
        {
            "id": "governance-black-box-custom",
            "kind": "data",
            "source": {"node_id": "design-tests", "port_id": "random-black-box-port"},
            "target": {"node_id": "black-box-output", "port_id": "value"},
        },
    ])

    compiled = compile_workflow_contract_v3(
        graph,
        capabilities=_capabilities(),
        workflow_version_id="wfv-governance-professional-output-contract",
    )

    assert compiled["compiled_definition"] is None
    issues = compiled["validation_result"]["errors"]
    sfmea_issue = next(
        item for item in issues
        if item["code"] == "validator_output_media_type_incompatible"
        and item.get("handler_id") == "sfmea"
    )
    black_box_issue = next(
        item for item in issues
        if item["code"] == "professional_output_schema_incompatible"
        and item.get("handler_id") == "black_box_cases"
    )
    assert sfmea_issue["output_id"] == "risk_register_custom"
    assert sfmea_issue["node_id"] == "sfmea-output"
    assert black_box_issue["output_id"] == "black_box_custom"
    assert black_box_issue["node_id"] == "black-box-output"
    assert "交付件" in sfmea_issue["message"]
    assert "JSON 数组" in black_box_issue["message"]


def test_storage_test_design_generated_professional_ports_accept_custom_filenames_with_json_array_contract():
    from app.services.workflow_contract_v3 import compile_workflow_contract_v3

    graph = _with_governance()
    governance = next(
        node for node in graph["nodes"] if node["id"] == "design-tests"
    )
    governance["ports"]["outputs"][0]["id"] = "stable-generated-port"
    output = next(node for node in graph["nodes"] if node["id"] == "sfmea-output")
    output["config"].update({
        "output_id": "custom_professional_result",
        "artifact": "risk-register.custom-name",
        "media_type": "application/json",
        "schema": {"type": "array"},
    })
    edge = next(edge for edge in graph["edges"] if edge["id"] == "governance-sfmea")
    edge["source"]["port_id"] = "stable-generated-port"

    compiled = compile_workflow_contract_v3(
        graph,
        capabilities=_capabilities(),
        workflow_version_id="wfv-governance-custom-filename",
    )

    assert compiled["validation_result"]["valid"] is True
    declared = next(
        output for output in compiled["compiled_definition"]["declared_outputs"]
        if output["output_id"] == "custom_professional_result"
    )
    assert declared["artifact"] == "risk-register.custom-name"
    assert declared["producer_port_key"] == "sfmea"


def test_governance_node_factory_uses_registered_semantic_port_contract() -> None:
    from app.services.workflow_authoring_factory import build_v3_node

    node = build_v3_node(
        "governance",
        label="Storage design",
        config={"handler_id": "storage_test_design"},
    )

    assert node["config"]["handler_id"] == "storage_test_design"
    assert [port["binding_key"] for port in node["ports"]["inputs"]] == [
        "source_evidence"
    ]
    assert [port["binding_key"] for port in node["ports"]["outputs"]] == [
        "sfmea",
        "black_box_cases",
    ]


def test_required_governance_source_evidence_must_be_bound_before_compile():
    from app.services.workflow_contract_v3 import compile_workflow_contract_v3

    graph = _with_governance()
    graph["edges"] = [
        edge for edge in graph["edges"] if edge["id"] != "analyze-governance"
    ]

    compiled = compile_workflow_contract_v3(
        graph,
        capabilities=_capabilities(),
        workflow_version_id="wfv-governance-unbound-evidence",
    )

    assert compiled["compiled_definition"] is None
    issue = next(
        item
        for item in compiled["validation_result"]["errors"]
        if item["code"] == "required_input_unbound"
    )
    assert issue["node_id"] == "design-tests"
    assert issue["field"] == "evidence_input_01j"
    assert "源码证据" in issue["message"]
    assert "未连接" in issue["message"]


def test_handler_owned_port_ids_may_differ_when_semantic_contract_matches():
    from app.services.workflow_contract_v3 import compile_workflow_contract_v3

    graph = _with_governance()
    governance = next(node for node in graph["nodes"] if node["id"] == "design-tests")
    governance["ports"]["inputs"][0]["id"] = "random-input-id"
    governance["ports"]["outputs"][0]["id"] = "random-sfmea-id"
    governance["ports"]["outputs"][1]["id"] = "random-black-box-id"
    for edge in graph["edges"]:
        if edge["id"] == "analyze-governance":
            edge["target"]["port_id"] = "random-input-id"
        elif edge["id"] == "governance-sfmea":
            edge["source"]["port_id"] = "random-sfmea-id"
        elif edge["id"] == "governance-black-box":
            edge["source"]["port_id"] = "random-black-box-id"

    compiled = compile_workflow_contract_v3(
        graph,
        capabilities=_capabilities(),
        workflow_version_id="wfv-handler-port-random-ids",
    )

    assert compiled["validation_result"]["valid"] is True


def test_handler_owned_ports_must_match_registered_semantic_contract():
    from app.services.workflow_contract_v3 import validate_workflow_contract_v3

    graph = _with_governance()
    governance = next(node for node in graph["nodes"] if node["id"] == "design-tests")
    governance["ports"]["inputs"][0]["binding_key"] = "source_report"

    validation = validate_workflow_contract_v3(
        graph, capabilities=_capabilities(), require_executable=True
    )

    issue = next(
        item
        for item in validation["errors"]
        if item["code"] == "handler_port_contract_mismatch"
    )
    assert issue["node_id"] == "design-tests"
    assert issue["handler_id"] == "storage_test_design"
    assert issue["field"] == "inputs"
    assert "处理器端口契约" in issue["message"]


def test_optional_governance_output_may_remain_unconnected():
    from app.services.workflow_contract_v3 import validate_workflow_contract_v3

    graph = _with_governance()
    governance = next(node for node in graph["nodes"] if node["id"] == "design-tests")
    governance["ports"]["outputs"][1]["required"] = False
    graph["nodes"] = [
        node for node in graph["nodes"] if node["id"] != "black-box-output"
    ]
    graph["edges"] = [
        edge for edge in graph["edges"] if edge["id"] != "governance-black-box"
    ]
    capabilities = _capabilities()
    capabilities["handlers"]["storage_test_design"]["output_ports"][1][
        "required"
    ] = False

    validation = validate_workflow_contract_v3(
        graph, capabilities=capabilities, require_executable=True
    )

    assert validation["valid"] is True


def test_governance_generated_port_must_connect_to_one_declared_output():
    from app.services.workflow_contract_v3 import validate_workflow_contract_v3

    graph = _with_governance()
    graph["edges"] = [
        edge for edge in graph["edges"] if edge["id"] != "governance-sfmea"
    ]
    result = validate_workflow_contract_v3(
        graph, capabilities=_capabilities(), require_executable=True
    )

    assert any(
        issue["code"] == "governance_output_not_declared"
        and issue.get("node_id") == "design-tests"
        and issue.get("field") == "sfmea"
        for issue in result["errors"]
    )


def test_governance_requires_at_least_one_generated_output_port():
    from app.services.workflow_contract_v3 import validate_workflow_contract_v3

    graph = _with_governance()
    governance = next(
        node for node in graph["nodes"] if node["id"] == "design-tests"
    )
    governance["ports"]["outputs"] = []
    graph["edges"] = [
        edge for edge in graph["edges"]
        if edge["id"] not in {"governance-sfmea", "governance-black-box"}
    ]

    result = validate_workflow_contract_v3(
        graph, capabilities=_capabilities(), require_executable=True
    )

    assert any(
        issue["code"] == "governance_output_ports_required"
        and issue.get("node_id") == "design-tests"
        for issue in result["errors"]
    )


def test_governance_output_rejects_multiple_declared_targets():
    from app.services.workflow_contract_v3 import validate_workflow_contract_v3

    graph = _with_governance()
    duplicate = copy.deepcopy(next(node for node in graph["nodes"] if node["id"] == "sfmea-output"))
    duplicate["id"] = "sfmea-copy-output"
    duplicate["config"]["output_id"] = "sfmea_copy"
    duplicate["config"]["artifact"] = "sfmea-copy.json"
    graph["nodes"].append(duplicate)
    graph["edges"].append({
        "id": "governance-sfmea-copy",
        "kind": "data",
        "source": {"node_id": "design-tests", "port_id": "sfmea"},
        "target": {"node_id": "sfmea-copy-output", "port_id": "value"},
    })

    result = validate_workflow_contract_v3(
        graph, capabilities=_capabilities(), require_executable=True
    )

    assert any(
        issue["code"] == "governance_output_multiple_declarations"
        and issue.get("node_id") == "design-tests"
        and issue.get("field") == "sfmea"
        for issue in result["errors"]
    )


def test_v3_palette_exposes_only_registered_validator_and_governance_handlers():
    from app.services.workflow_node_registry import node_registry_payload

    registry = node_registry_payload(schema_version=3, capabilities=_capabilities())
    by_kind = {node["kind"]: node for node in registry["nodes"]}

    assert {"input", "output", "agent", "validator", "governance"} <= set(by_kind)
    validator = by_kind["validator"]
    governance = by_kind["governance"]
    assert validator["execution"]["available"] is True
    assert [item["value"] for item in validator["config_schema"]["handler_id"]["options"]] == [
        "artifact_exists",
        "independent_review",
        "json_schema",
        "source_evidence",
    ]
    assert [item["value"] for item in governance["config_schema"]["handler_id"]["options"]] == [
        "storage_test_design"
    ]
    assert all(node["execution"].get("runtime_behavior") != "skipped" for node in by_kind.values())

    no_governance = copy.deepcopy(_capabilities())
    no_governance["handlers"].pop("storage_test_design")
    kinds = {
        node["kind"]
        for node in node_registry_payload(schema_version=3, capabilities=no_governance)["nodes"]
    }
    assert "validator" in kinds
    assert "governance" not in kinds


def test_v3_palette_consumes_phase5_node_kind_capability_metadata():
    from app.services.workflow_node_registry import node_registry_payload

    capabilities = {
        "handlers": {
            "agent": {"versions": [1], "node_kind": "agent"},
            "custom_fact_gate": {"versions": [1], "node_kind": "validator"},
            "custom_report_builder": {"versions": [1], "node_kind": "governance"},
        }
    }

    by_kind = {
        node["kind"]: node
        for node in node_registry_payload(
            schema_version=3, capabilities=capabilities
        )["nodes"]
    }

    assert by_kind["validator"]["execution"]["handler_options"] == [
        "custom_fact_gate"
    ]
    assert by_kind["governance"]["execution"]["handler_options"] == [
        "custom_report_builder"
    ]
