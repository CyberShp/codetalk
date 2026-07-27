"""Contracts for the V3 declared-output and validation-profile compiler."""

from __future__ import annotations

import copy


def _capabilities(*, include_professional: bool = False) -> dict:
    handlers = {
        "agent": {"versions": [1]},
        "artifact_exists": {"versions": [1]},
        "json_schema": {"versions": [1]},
    }
    if include_professional:
        handlers.update({
            "source_evidence": {"versions": [1]},
            "storage_test_design": {"versions": [1]},
            "independent_review": {"versions": [1]},
            "human_approval": {"versions": [1]},
        })
    return {"handlers": handlers}


def _graph(*, profile: str | None = None, schema: dict | None = None) -> dict:
    settings = {"stop_on_error": True, "max_parallelism": 1}
    if profile is not None:
        settings["validation_profile"] = profile
    return {
        "schema_version": 3,
        "workflow_id": "source-analysis",
        "name": "SFMEA report.md prompt must not infer governance",
        "description": "A deliberately misleading name for the no-inference contract.",
        "settings": settings,
        "nodes": [
            {"id": "repo", "kind": "input", "label": "Source repository", "position": {"x": 0, "y": 0}, "ports": {"inputs": [], "outputs": [{"id": "value", "type": "directory"}]}, "config": {"input_id": "repo", "type": "directory", "required": True}},
            {"id": "analyze", "kind": "agent", "label": "Analyze source", "position": {"x": 300, "y": 0}, "ports": {"inputs": [{"id": "repo_path", "type": "directory", "required": True}], "outputs": [{"id": "report", "type": "artifact", "required": True}]}, "config": {"handler_id": "agent", "handler_version": 1, "provider_ref": "provider_codex_default", "provider_capabilities_required": ["streaming", "cancellation"], "mcp_profiles": ["gitnexus"], "skill_ids": ["source-evidence-first"], "skill_instructions": ["Read the user input verbatim."], "goal": "Generate SFMEA and black-box cases only if explicitly declared.", "prompt_template_version": 7, "prompt_template": "{{node_goal}}\n{{bound_inputs}}\n{{output_contract}}", "input_rendering": {"preserve_user_text_verbatim": True, "binding_order": ["repo_path"]}, "timeout_sec": 1200, "idle_timeout_sec": 180, "retry_policy": {"max_attempts": 2, "backoff_seconds": 3}, "failure_policy": "stop", "required_outputs": ["ghost-output-must-not-leak"]}},
            {"id": "report-output", "kind": "output", "label": "Analysis report", "position": {"x": 600, "y": 0}, "ports": {"inputs": [{"id": "value", "type": "artifact", "required": True}], "outputs": []}, "config": {"output_id": "report", "artifact": "report.md", "media_type": "text/markdown", "required": True, "schema": schema}},
        ],
        "edges": [
            {"id": "repo-to-analyze", "kind": "data", "source": {"node_id": "repo", "port_id": "value"}, "target": {"node_id": "analyze", "port_id": "repo_path"}},
            {"id": "analyze-to-report", "kind": "data", "source": {"node_id": "analyze", "port_id": "report"}, "target": {"node_id": "report-output", "port_id": "value"}},
        ],
    }


def _generated_validators(compiled: dict) -> list[dict]:
    return [node for node in compiled["compiled_plan"]["nodes"] if node.get("generated_by_validation_profile")]


def test_v3_default_profile_is_artifact_only_and_declared_outputs_are_authoritative():
    from app.services.workflow_contract_v3 import COMPILED_CONTRACT_VERSION, compile_workflow_contract_v3

    compiled = compile_workflow_contract_v3(_graph(), capabilities=_capabilities(), workflow_version_id="wfv_42")

    assert compiled["validation_result"] == {"valid": True, "errors": [], "warnings": []}
    assert COMPILED_CONTRACT_VERSION == 3
    definition = compiled["compiled_definition"]
    assert {"id", "name", "version", "inputs", "steps", "outputs", "compiled_contract_version", "validation_profile", "declared_inputs", "declared_outputs", "validators"} <= definition.keys()
    assert definition["compiled_contract_version"] == 3
    assert definition["validation_profile"] == "artifact_only"
    assert definition["outputs"] == definition["declared_outputs"] == [{"output_id": "report", "id": "report", "label": "Analysis report", "artifact": "report.md", "media_type": "text/markdown", "type": "markdown", "required": True, "schema": None, "producer_step_id": "analyze", "from": "analyze"}]
    assert definition["inputs"] == definition["declared_inputs"] == [{"input_id": "repo", "id": "repo", "label": "Source repository", "type": "directory", "required": True, "resolver": "manual"}]
    analyze = next(node for node in definition["nodes"] if node["node_id"] == "analyze")
    assert analyze["required_outputs"] == ["report"]
    assert "ghost-output-must-not-leak" not in str(definition)
    validators = _generated_validators(compiled)
    assert [(node["handler_id"], node["required_outputs"]) for node in validators] == [("artifact_exists", ["report"])]
    assert definition["validators"] == validators
    assert {"plan_version", "workflow_version_id", "topological_order", "nodes", "settings"} <= compiled["compiled_plan"].keys()
    assert compiled["compiled_plan"]["plan_version"] == 1
    assert compiled["compiled_plan"]["compiled_contract_version"] == 3
    assert compiled["compiled_plan"]["max_parallelism"] == 1
    assert compiled["compiled_plan"]["stop_on_error"] is True

    runtime_step = next(step for step in definition["steps"] if step["id"] == "analyze")
    assert runtime_step == {
        "id": "analyze",
        "type": "agent_task",
        "handler_id": "agent",
        "handler_version": 1,
        "depends_on": [],
        "provider": "provider_codex_default",
        "provider_capabilities_required": ["cancellation", "streaming"],
        "mcp_profiles": ["gitnexus"],
        "skills": ["source-evidence-first"],
        "skill_instructions": ["Read the user input verbatim."],
        "goal": "Generate SFMEA and black-box cases only if explicitly declared.",
        "prompt_template_version": 7,
        "prompt_template": "{{node_goal}}\n{{bound_inputs}}\n{{output_contract}}",
        "input_rendering": {
            "preserve_user_text_verbatim": True,
            "binding_order": ["repo_path"],
        },
        "timeout_sec": 1200,
        "idle_timeout_sec": 180,
        "retry_policy": {"max_attempts": 2, "backoff_seconds": 3},
        "failure_policy": "stop",
        "required_outputs": ["report"],
        "required_artifacts": ["report.md"],
    }
    plan_step = next(node for node in compiled["compiled_plan"]["nodes"] if node["node_id"] == "analyze")
    assert plan_step["type"] == "agent_task"


def test_profiles_expand_only_explicitly_and_schema_only_targets_schema_outputs():
    from app.services.workflow_contract_v3 import compile_workflow_contract_v3

    none = compile_workflow_contract_v3(_graph(profile="none"), capabilities=_capabilities(), workflow_version_id="wfv_none")
    assert none["validation_result"]["valid"] is True
    assert _generated_validators(none) == []
    artifact_only = compile_workflow_contract_v3(_graph(profile="artifact_only"), capabilities=_capabilities(), workflow_version_id="wfv_artifact")
    assert [node["handler_id"] for node in _generated_validators(artifact_only)] == ["artifact_exists"]
    schema = compile_workflow_contract_v3(_graph(profile="schema", schema={"type": "object"}), capabilities=_capabilities(), workflow_version_id="wfv_schema")
    assert [(node["handler_id"], node["required_outputs"]) for node in _generated_validators(schema)] == [("artifact_exists", ["report"]), ("json_schema", ["report"])]
    no_schema = compile_workflow_contract_v3(_graph(profile="schema"), capabilities=_capabilities(), workflow_version_id="wfv_no_schema")
    assert [node["handler_id"] for node in _generated_validators(no_schema)] == ["artifact_exists"]

    source_evidence = compile_workflow_contract_v3(_graph(profile="source_evidence"), capabilities=_capabilities(include_professional=True), workflow_version_id="wfv_evidence")
    assert [node["handler_id"] for node in _generated_validators(source_evidence)] == ["artifact_exists", "source_evidence"]
    formal_release = compile_workflow_contract_v3(_graph(profile="formal_release"), capabilities=_capabilities(include_professional=True), workflow_version_id="wfv_formal")
    assert [node["handler_id"] for node in _generated_validators(formal_release)] == ["artifact_exists", "storage_test_design", "independent_review", "human_approval"]


def test_profile_is_never_inferred_from_name_filename_prompt_or_goal():
    from app.services.workflow_contract_v3 import compile_workflow_contract_v3

    graph = _graph()
    graph["name"] = "formal_release storage_test_design sfmea"
    graph["nodes"][1]["config"]["goal"] = "Run a formal release independent review for SFMEA."
    graph["nodes"][1]["config"]["prompt_template"] = "Create black_box_cases.json and source evidence."
    graph["nodes"][2]["config"]["artifact"] = "sfmea.json"
    compiled = compile_workflow_contract_v3(graph, capabilities=_capabilities(), workflow_version_id="wfv_no_inference")

    assert compiled["validation_result"]["valid"] is True
    assert compiled["compiled_definition"]["validation_profile"] == "artifact_only"
    assert [node["handler_id"] for node in _generated_validators(compiled)] == ["artifact_exists"]


def test_invalid_profile_and_validator_output_outside_declaration_are_located():
    from app.services.workflow_contract_v3 import validate_workflow_contract_v3

    invalid_profile = _graph(profile="surprise_governance")
    profile_result = validate_workflow_contract_v3(invalid_profile, capabilities=_capabilities())
    assert profile_result["valid"] is False
    assert any(issue["code"] == "validation_profile_invalid" for issue in profile_result["errors"])
    graph = _graph()
    graph["nodes"].append({"id": "validate-source", "kind": "validator", "label": "Validate source", "position": {"x": 600, "y": 160}, "ports": {"inputs": [], "outputs": []}, "config": {"handler_id": "source_evidence", "handler_version": 1, "required_outputs": ["report", "sfmea"]}})
    result = validate_workflow_contract_v3(graph, capabilities=_capabilities(include_professional=True))

    assert result["valid"] is False
    assert {(issue.get("node_id"), issue.get("output_id")) for issue in result["errors"] if issue["code"] == "validator_output_not_declared"} == {("validate-source", "sfmea")}


def test_draft_warns_for_unknown_handlers_but_executable_contract_fails_closed():
    from app.services.workflow_contract_v3 import compile_workflow_contract_v3, validate_workflow_contract_v3

    graph = _graph()
    graph["nodes"][1]["config"]["handler_id"] = "unregistered-agent"
    draft = validate_workflow_contract_v3(graph, capabilities=_capabilities(), require_executable=False)
    assert draft["valid"] is True
    assert any(issue["code"] == "handler_unavailable_draft" for issue in draft["warnings"])
    executable = compile_workflow_contract_v3(graph, capabilities=_capabilities(), workflow_version_id="wfv_unknown_handler", require_executable=True)
    assert executable["compiled_definition"] is None
    assert executable["compiled_plan"] is None
    assert executable["validation_result"]["valid"] is False
    assert any(issue["code"] == "handler_unavailable" for issue in executable["validation_result"]["errors"])
    unavailable = validate_workflow_contract_v3(_graph(profile="storage_test_design"), capabilities=_capabilities(), require_executable=True)
    assert unavailable["valid"] is False
    assert any(issue["code"] == "handler_unavailable" for issue in unavailable["errors"])


def test_compiled_contract_freezes_execution_fields_and_is_deterministic():
    from app.services.workflow_contract_v3 import compile_workflow_contract_v3

    graph = _graph()
    first = compile_workflow_contract_v3(graph, capabilities=_capabilities(), workflow_version_id="wfv_frozen", workflow_version_number=4)
    shuffled = copy.deepcopy(graph)
    shuffled["nodes"].reverse()
    shuffled["edges"].reverse()
    second = compile_workflow_contract_v3(shuffled, capabilities=_capabilities(), workflow_version_id="wfv_frozen", workflow_version_number=4)
    assert first == second
    analyze = next(node for node in first["compiled_definition"]["nodes"] if node["node_id"] == "analyze")
    assert analyze == {"node_id": "analyze", "graph_node_id": "analyze", "kind": "agent", "handler_id": "agent", "handler_version": 1, "depends_on": [], "resolved_input_bindings": {"repo_path": {"source_node_id": "repo", "source_port_id": "value", "source_input_id": "repo"}}, "input_ports": [{"id": "repo_path", "type": "directory", "required": True}], "output_ports": [{"id": "report", "type": "artifact", "required": True}], "provider_ref": "provider_codex_default", "provider_capabilities_required": ["cancellation", "streaming"], "mcp_profiles": ["gitnexus"], "skill_ids": ["source-evidence-first"], "skill_instructions": ["Read the user input verbatim."], "goal": "Generate SFMEA and black-box cases only if explicitly declared.", "prompt_template_version": 7, "prompt_template": "{{node_goal}}\n{{bound_inputs}}\n{{output_contract}}", "input_rendering": {"preserve_user_text_verbatim": True, "binding_order": ["repo_path"]}, "timeout_sec": 1200, "idle_timeout_sec": 180, "retry_policy": {"max_attempts": 2, "backoff_seconds": 3}, "failure_policy": "stop", "required_outputs": ["report"]}


def test_v3_graph_fails_closed_for_cycles_and_duplicate_scalar_bindings():
    from app.services.workflow_contract_v3 import validate_workflow_contract_v3

    cycle = _graph()
    cycle["nodes"].append({
        "id": "review",
        "kind": "validator",
        "label": "Review",
        "position": {"x": 450, "y": 180},
        "ports": {
            "inputs": [{"id": "value", "type": "artifact", "required": True}],
            "outputs": [{"id": "result", "type": "artifact"}],
        },
        "config": {
            "handler_id": "artifact_exists",
            "handler_version": 1,
            "required_outputs": ["report"],
        },
    })
    cycle["edges"].extend([
        {
            "id": "analyze-review",
            "kind": "dependency",
            "source": {"node_id": "analyze", "port_id": "report"},
            "target": {"node_id": "review", "port_id": "value"},
        },
        {
            "id": "review-analyze",
            "kind": "dependency",
            "source": {"node_id": "review", "port_id": "result"},
            "target": {"node_id": "analyze", "port_id": "repo_path"},
        },
    ])
    cycle_result = validate_workflow_contract_v3(
        cycle, capabilities=_capabilities(), require_executable=True
    )
    assert cycle_result["valid"] is False
    assert "graph_cycle" in {issue["code"] for issue in cycle_result["errors"]}

    duplicate = _graph()
    duplicate["nodes"].append({
        "id": "second-repo",
        "kind": "input",
        "label": "Second repository",
        "position": {"x": 0, "y": 160},
        "ports": {"inputs": [], "outputs": [{"id": "value", "type": "directory"}]},
        "config": {"input_id": "second_repo", "type": "directory"},
    })
    duplicate["edges"].append({
        "id": "second-repo-to-analyze",
        "kind": "data",
        "source": {"node_id": "second-repo", "port_id": "value"},
        "target": {"node_id": "analyze", "port_id": "repo_path"},
    })
    duplicate_result = validate_workflow_contract_v3(
        duplicate, capabilities=_capabilities(), require_executable=True
    )
    assert duplicate_result["valid"] is False
    assert "multiple_edges_to_single_input" in {
        issue["code"] for issue in duplicate_result["errors"]
    }


def test_v3_data_edges_require_existing_compatible_ports():
    from app.services.workflow_contract_v3 import validate_workflow_contract_v3

    incompatible = _graph()
    incompatible["nodes"][0]["ports"]["outputs"][0]["type"] = "file"
    incompatible["nodes"][0]["config"]["type"] = "file"
    incompatible_result = validate_workflow_contract_v3(
        incompatible, capabilities=_capabilities(), require_executable=True
    )
    assert incompatible_result["valid"] is False
    assert any(
        issue["code"] == "port_type_mismatch"
        and issue.get("edge_id") == "repo-to-analyze"
        and issue.get("source_type") == "file"
        and issue.get("target_type") == "directory"
        for issue in incompatible_result["errors"]
    )

    missing = _graph()
    missing["edges"][0]["target"]["port_id"] = "missing_repo_port"
    missing_result = validate_workflow_contract_v3(
        missing, capabilities=_capabilities(), require_executable=True
    )
    assert missing_result["valid"] is False
    assert any(
        issue["code"] == "target_port_missing"
        and issue.get("edge_id") == "repo-to-analyze"
        for issue in missing_result["errors"]
    )


def test_v3_declared_output_rejects_artifact_path_escape():
    from app.services.workflow_contract_v3 import validate_workflow_contract_v3

    graph = _graph()
    graph["nodes"][2]["config"]["artifact"] = "../outside.md"
    result = validate_workflow_contract_v3(
        graph, capabilities=_capabilities(), require_executable=True
    )

    assert result["valid"] is False
    assert any(
        issue["code"] == "output_artifact_unsafe"
        and issue.get("output_id") == "report"
        for issue in result["errors"]
    )
