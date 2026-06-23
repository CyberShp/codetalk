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
