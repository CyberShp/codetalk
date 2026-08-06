import json
import zipfile
from types import SimpleNamespace


def test_deliverable_bundle_has_stable_envelope_and_excludes_diagnostics(tmp_path):
    from app.services.workbench_deliverables import build_deliverable_bundle

    task_dir = tmp_path / "run"
    (task_dir / "steps" / "report").mkdir(parents=True)
    (task_dir / "steps" / "report" / "report.md").write_text(
        "# Risk report\n", encoding="utf-8"
    )
    (task_dir / "steps" / "report" / "risk_findings.json").write_text(
        '{"items": []}', encoding="utf-8"
    )
    (task_dir / "provider_snapshot.json").write_text(
        '{"secret": "diagnostic only"}', encoding="utf-8"
    )

    result = build_deliverable_bundle(
        task_dir,
        task_run_id="run-1",
        summary="# Summary\n\nCompleted.\n",
        validation={"accepted": True, "artifacts": []},
    )

    envelope_dir = task_dir / "deliverables"
    assert (envelope_dir / "summary.md").read_text("utf-8").startswith("# Summary")
    manifest = json.loads((envelope_dir / "manifest.json").read_text("utf-8"))
    assert manifest["task_run_id"] == "run-1"
    assert [item["relative_path"] for item in manifest["artifacts"]] == [
        "steps/report/report.md",
        "steps/report/risk_findings.json",
    ]
    assert all("path" not in item for item in manifest["artifacts"])
    assert result["artifact_count"] == 2

    with zipfile.ZipFile(result["bundle_path"]) as archive:
        assert sorted(archive.namelist()) == [
            "artifact_validation.json",
            "artifacts/steps/report/report.md",
            "artifacts/steps/report/risk_findings.json",
            "manifest.json",
            "summary.md",
        ]
        assert "provider_snapshot.json" not in "\n".join(archive.namelist())


def test_deliverable_bundle_is_deterministic_and_replaces_stale_envelope(tmp_path):
    from app.services.workbench_deliverables import build_deliverable_bundle

    task_dir = tmp_path / "run"
    (task_dir / "steps").mkdir(parents=True)
    artifact = task_dir / "steps" / "test_plan.json"
    artifact.write_text('{"cases": [1]}', encoding="utf-8")

    first = build_deliverable_bundle(
        task_dir,
        task_run_id="run-2",
        summary="First",
        validation={"accepted": True},
    )
    first_hash = first["bundle_sha256"]
    (task_dir / "deliverables" / "stale.txt").write_text("stale", encoding="utf-8")

    second = build_deliverable_bundle(
        task_dir,
        task_run_id="run-2",
        summary="Second",
        validation={"accepted": True},
    )

    assert not (task_dir / "deliverables" / "stale.txt").exists()
    assert first_hash != second["bundle_sha256"]


def test_build_task_run_deliverables_uses_execution_and_profile_validation(tmp_path):
    from app.services.workbench_deliverables import build_task_run_deliverables

    task_dir = tmp_path / "run"
    (task_dir / "steps" / "report").mkdir(parents=True)
    (task_dir / "steps" / "report" / "test_design.md").write_text(
        "# Test design\n\n## Cases\nOne.\n", encoding="utf-8"
    )
    (task_dir / "workflow_execution.json").write_text(
        json.dumps({"status": "completed", "outputs": [{"status": "ok"}]}),
        encoding="utf-8",
    )
    (task_dir / "output_contract.json").write_text(
        json.dumps(
            {
                "profile_id": "apro_test",
                "profile_version": 1,
                "name": "Test",
                "artifacts": [
                    {
                        "id": "test_design",
                        "filename": "steps/report/test_design.md",
                        "format": "markdown",
                        "required": True,
                        "schema": {"required_sections": ["Cases"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    task_run = SimpleNamespace(
        task_run_id="run-3",
        artifact_dir=str(task_dir),
        workflow_snapshot={"name": "Defect retest"},
    )

    result = build_task_run_deliverables(task_run)

    assert result["validation"]["accepted"] is True
    assert result["manifest"]["artifacts"][0]["relative_path"] == (
        "steps/report/test_design.md"
    )
    summary = (task_dir / "deliverables" / "summary.md").read_text("utf-8")
    assert "Defect retest" in summary
    assert "completed" in summary


def test_profile_filename_is_materialized_from_matching_workflow_output(tmp_path):
    from app.services.workbench_deliverables import build_task_run_deliverables

    task_dir = tmp_path / "run"
    source = task_dir / "steps" / "report" / "original.md"
    source.parent.mkdir(parents=True)
    source.write_text("# Review\n\n## Evidence\nCurrent run.\n", encoding="utf-8")
    (task_dir / "workflow_execution.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "outputs": [
                    {
                        "id": "review",
                        "status": "ok",
                        "path": "steps/report/original.md",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (task_dir / "output_contract.json").write_text(
        json.dumps(
            {
                "profile_id": "apro_review",
                "profile_version": 2,
                "name": "Review",
                "artifacts": [
                    {
                        "id": "review",
                        "filename": "protocol-review.md",
                        "format": "markdown",
                        "required": True,
                        "schema": {"required_sections": ["Evidence"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    task_run = SimpleNamespace(
        task_run_id="run-profile",
        artifact_dir=str(task_dir),
        workflow_snapshot={"name": "Review"},
    )

    result = build_task_run_deliverables(task_run)

    assert result["validation"]["accepted"] is True
    assert (task_dir / "protocol-review.md").read_text("utf-8") == source.read_text("utf-8")
    assert [item["relative_path"] for item in result["manifest"]["artifacts"]] == [
        "protocol-review.md"
    ]


def test_required_skill_judge_blocks_delivery_until_ready(tmp_path):
    from app.services.workbench_deliverables import build_task_run_deliverables

    task_dir = tmp_path / "run"
    output = task_dir / "report.md"
    task_dir.mkdir(parents=True)
    output.write_text("# Report\n", encoding="utf-8")
    (task_dir / "skill_invocation.json").write_text(
        json.dumps(
            {
                "schema_version": "skill-run-invocation-v1",
                "skill_version_id": "skill_version_1",
                "skill_content_digest": "sha256:" + "1" * 64,
                "input_snapshot": {"ref": "skill_input_snapshot.json", "digest": "sha256:" + "2" * 64},
                "selected_delivery_ids": ["delivery.report"],
                "judge": {
                    "required": True,
                    "isolated_session": True,
                    "artifact_ids": ["artifact.report"],
                },
            }
        ),
        encoding="utf-8",
    )
    (task_dir / "workflow_execution.json").write_text(
        json.dumps({"status": "completed", "outputs": [{"id": "report", "status": "ok", "path": "report.md"}]}),
        encoding="utf-8",
    )
    task_run = SimpleNamespace(
        task_run_id="run-judge",
        artifact_dir=str(task_dir),
        workflow_snapshot={"name": "Judge gated"},
        task_bundle={},
    )

    pending = build_task_run_deliverables(task_run)
    assert pending["validation"]["accepted"] is False
    assert pending["validation"]["judge_status"] == "PENDING_VALIDATION"

    (task_dir / "skill_judge_report.json").write_text(
        json.dumps({"status": "READY", "ready": True}),
        encoding="utf-8",
    )
    ready = build_task_run_deliverables(task_run)
    assert ready["validation"]["accepted"] is True
    assert ready["validation"]["judge_status"] == "READY"
