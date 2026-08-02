import pytest


def _profile(name: str = "Storage default") -> dict:
    return {
        "name": name,
        "description": "Local storage feature deliverables",
        "artifacts": [
            {
                "id": "test_design",
                "filename": "test_design.md",
                "format": "markdown",
                "required": True,
                "schema": {
                    "required_sections": ["Risk", "Cases"],
                },
                "instructions": "Keep current evidence links on every finding.",
            }
        ],
    }


def test_artifact_profile_versions_are_immutable_and_restore_creates_a_version(tmp_path):
    from app.services.artifact_profiles import ArtifactProfileStore

    store = ArtifactProfileStore(tmp_path / "profiles.db")
    created = store.create_profile(_profile())
    updated = store.update_profile(
        created["id"],
        {
            **_profile("Storage concise"),
            "artifacts": [
                {
                    **_profile()["artifacts"][0],
                    "instructions": "Use a concise table.",
                }
            ],
        },
        expected_version=1,
    )

    assert created["version"] == 1
    assert updated["version"] == 2
    assert store.get_profile(created["id"], version=1)["name"] == "Storage default"

    restored = store.restore_version(created["id"], version=1)

    assert restored["version"] == 3
    assert restored["name"] == "Storage default"
    assert [item["version"] for item in store.list_versions(created["id"])] == [3, 2, 1]


def test_artifact_profile_resolution_is_single_and_deterministic(tmp_path):
    from app.services.artifact_profiles import ArtifactProfileStore

    store = ArtifactProfileStore(tmp_path / "profiles.db")
    selected = store.create_profile(_profile("Selected"))
    workspace = store.create_profile(_profile("Workspace"))
    first_tag = store.create_profile(_profile("First tag"))
    second_tag = store.create_profile(_profile("Second tag"))
    default = store.create_profile(_profile("User default"))

    store.bind_workspace("ws-1", workspace["id"])
    store.bind_feature_tag("iscsi", first_tag["id"])
    store.bind_feature_tag("recovery", second_tag["id"])
    store.set_user_default(default["id"])

    assert store.resolve_profile(selected_profile_id=selected["id"])["source"] == "run_selection"
    assert store.resolve_profile(workspace_id="ws-1", feature_tags=["iscsi"])["profile"]["name"] == "Workspace"
    resolved_tag = store.resolve_profile(feature_tags=["recovery", "iscsi"])
    assert resolved_tag["source"] == "feature_tag:recovery"
    assert resolved_tag["profile"]["name"] == "Second tag"
    assert store.resolve_profile()["profile"]["name"] == "User default"

    builtin = _profile("Built in") | {"id": "builtin-core", "version": 1}
    store.clear_user_default()
    assert store.resolve_profile(builtin_profile=builtin)["profile"] == builtin


def test_artifact_profile_never_merges_profiles(tmp_path):
    from app.services.artifact_profiles import ArtifactProfileStore

    store = ArtifactProfileStore(tmp_path / "profiles.db")
    workspace = store.create_profile(_profile("Workspace"))
    tagged = store.create_profile(
        {
            **_profile("Tagged"),
            "artifacts": [
                {
                    "id": "risk_matrix",
                    "filename": "risk_matrix.json",
                    "format": "json",
                    "required": True,
                }
            ],
        }
    )
    store.bind_workspace("ws-1", workspace["id"])
    store.bind_feature_tag("iscsi", tagged["id"])

    resolved = store.resolve_profile(workspace_id="ws-1", feature_tags=["iscsi"])

    assert [item["id"] for item in resolved["profile"]["artifacts"]] == ["test_design"]


@pytest.mark.parametrize(
    ("artifacts", "message"),
    [
        (
            [
                {"id": "same", "filename": "one.md", "format": "markdown"},
                {"id": "same", "filename": "two.md", "format": "markdown"},
            ],
            "artifact id",
        ),
        (
            [
                {"id": "one", "filename": "Report.md", "format": "markdown"},
                {"id": "two", "filename": "report.md", "format": "markdown"},
            ],
            "filename",
        ),
        (
            [{"id": "escape", "filename": "../outside.md", "format": "markdown"}],
            "workspace-relative",
        ),
        (
            [{"id": "manifest", "filename": "manifest.json", "format": "json"}],
            "reserved",
        ),
    ],
)
def test_artifact_profile_rejects_collisions_and_unsafe_paths(tmp_path, artifacts, message):
    from app.services.artifact_profiles import ArtifactProfileStore, ArtifactProfileValidationError

    store = ArtifactProfileStore(tmp_path / "profiles.db")

    with pytest.raises(ArtifactProfileValidationError, match=message):
        store.create_profile({**_profile(), "artifacts": artifacts})


@pytest.mark.parametrize(
    "unsafe",
    [
        {"allow_unverified_evidence": True},
        {"allow_external_paths": True},
        {"skip_manifest": True},
    ],
)
def test_artifact_profile_cannot_weaken_global_safety(tmp_path, unsafe):
    from app.services.artifact_profiles import ArtifactProfileStore, ArtifactProfileValidationError

    store = ArtifactProfileStore(tmp_path / "profiles.db")

    with pytest.raises(ArtifactProfileValidationError, match="global safety"):
        store.create_profile({**_profile(), "safety": unsafe})


def test_artifact_profile_update_uses_optimistic_version_check(tmp_path):
    from app.services.artifact_profiles import (
        ArtifactProfileConflictError,
        ArtifactProfileStore,
    )

    store = ArtifactProfileStore(tmp_path / "profiles.db")
    created = store.create_profile(_profile())
    store.update_profile(created["id"], _profile("Version two"), expected_version=1)

    with pytest.raises(ArtifactProfileConflictError, match="current version is 2"):
        store.update_profile(created["id"], _profile("Stale edit"), expected_version=1)


def test_validate_profile_artifacts_checks_required_files_and_markdown_sections(tmp_path):
    from app.services.artifact_profiles import validate_profile_artifacts

    profile = _profile()
    (tmp_path / "test_design.md").write_text(
        "# Design\n\n## Risk\nCurrent evidence.\n\n## Cases\nOne case.\n",
        encoding="utf-8",
    )

    accepted = validate_profile_artifacts(tmp_path, profile)

    assert accepted["accepted"] is True
    assert accepted["artifacts"][0]["status"] == "accepted"
    assert accepted["artifacts"][0]["size"] > 0

    (tmp_path / "test_design.md").write_text("# Design\n", encoding="utf-8")
    rejected = validate_profile_artifacts(tmp_path, profile)
    assert rejected["accepted"] is False
    assert "missing Markdown sections" in rejected["artifacts"][0]["errors"][0]


def test_validate_profile_artifacts_reports_missing_required_and_invalid_json(tmp_path):
    from app.services.artifact_profiles import validate_profile_artifacts

    profile = {
        "name": "Machine outputs",
        "artifacts": [
            {
                "id": "risk_matrix",
                "filename": "risk_matrix.json",
                "format": "json",
                "required": True,
                "schema": {"required_keys": ["risks"]},
            },
            {
                "id": "optional_notes",
                "filename": "notes.txt",
                "format": "text",
                "required": False,
            },
        ],
    }

    missing = validate_profile_artifacts(tmp_path, profile)
    assert missing["accepted"] is False
    assert missing["artifacts"][0]["status"] == "missing"
    assert missing["artifacts"][1]["status"] == "optional_missing"

    (tmp_path / "risk_matrix.json").write_text("{}", encoding="utf-8")
    invalid = validate_profile_artifacts(tmp_path, profile)
    assert invalid["artifacts"][0]["status"] == "rejected"
    assert invalid["artifacts"][0]["errors"] == ["missing JSON keys: risks"]


def test_output_contract_snapshot_is_immutable_and_injected_without_mutating_bundle(tmp_path):
    from app.services.artifact_profiles import (
        apply_artifact_profile_to_task_bundle,
        write_output_contract_snapshot,
    )

    profile = _profile() | {"id": "apro_storage", "version": 4}
    resolution = {"source": "workspace_binding", "profile": profile}

    snapshot = write_output_contract_snapshot(
        tmp_path,
        task_run_id="run-1",
        resolution=resolution,
    )

    assert snapshot["profile_id"] == "apro_storage"
    assert snapshot["profile_version"] == 4
    assert snapshot["safety"] == {
        "evidence_validation_required": True,
        "manifest_required": True,
        "workspace_relative_paths_required": True,
    }
    assert len(snapshot["sha256"]) == 64
    assert (tmp_path / "output_contract.json").exists()

    original = {"task_run_id": "run-1", "agent_output_contract": {"existing": True}}
    injected = apply_artifact_profile_to_task_bundle(original, snapshot)
    assert "artifact_profile" not in original
    assert injected["artifact_profile"]["sha256"] == snapshot["sha256"]
    assert injected["agent_output_contract"] == {"existing": True}
