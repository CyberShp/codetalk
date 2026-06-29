def test_builtin_workflow_presets_are_valid_and_cover_core_scenarios():
    from app.services.workflow_dsl import validate_workflow_definition
    from app.services.workflow_presets import builtin_workflow_presets

    presets = builtin_workflow_presets()

    assert {
        "module_analysis",
        "resource_leak_hunt",
        "mr_blackbox_test",
        "patch_impact_review",
    }.issubset({item["id"] for item in presets})

    for preset in presets:
        workflow = validate_workflow_definition(preset["definition"])
        assert workflow.id == preset["definition"]["id"]
        assert workflow.steps
        assert workflow.outputs

    mr_preset = next(item for item in presets if item["id"] == "mr_blackbox_test")
    assert any(
        item["type"] == "mr_link" and item.get("resolver") == "agent_mcp"
        for item in mr_preset["definition"]["inputs"]
    )
    assert "mr_snapshot.json" in mr_preset["definition"]["steps"][0]["required_artifacts"]
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
    impact_output = next(
        item
        for item in patch_preset["definition"]["outputs"]
        if item["id"] == "impact_scope"
    )
    assert impact_output["artifact"] == "impact_scope.json"
    assert impact_output["evidence_memory"]["enabled"] is True
    assert impact_output["evidence_memory"]["kind"] == "patch_impact_scope"


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
