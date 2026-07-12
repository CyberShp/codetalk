from __future__ import annotations

import hashlib
import json
import zipfile
from io import BytesIO

import pytest

from app.services.ai_thread_artifacts import (
    ArtifactContractError,
    build_ai_thread_delivery_zip,
    materialize_ai_thread_manifest,
    resolve_ai_thread_artifact,
)


def test_manifest_records_validated_multi_file_deliverables(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    (root / "business_flow.md").write_text("# Flow\n\nSource: lib/iscsi/iscsi.c\n", encoding="utf-8")
    (root / "sfmea.json").write_text(
        json.dumps([{"failure_mode": "login timeout"}]),
        encoding="utf-8",
    )

    manifest = materialize_ai_thread_manifest(
        root,
        run_id="run-1",
        declared_artifacts=[
            {"artifact": "business_flow.md", "type": "markdown", "required": True},
            {
                "artifact": "sfmea.json",
                "type": "json",
                "required": True,
                "schema": {"type": "array", "minItems": 1},
            },
        ],
        producer="builtin_llm:deepseek",
    )

    assert manifest["version"] == "ai-thread-artifact-manifest-v1"
    assert manifest["status"] == "accepted"
    assert manifest["artifact_count"] == 2
    entries = {item["relative_path"]: item for item in manifest["artifacts"]}
    assert entries["business_flow.md"]["media_type"] == "text/markdown"
    assert entries["sfmea.json"]["schema_status"] == "accepted"
    assert entries["sfmea.json"]["validation_status"] == "accepted"
    assert entries["sfmea.json"]["producer"] == "builtin_llm:deepseek"
    assert entries["sfmea.json"]["sha256"] == hashlib.sha256(
        (root / "sfmea.json").read_bytes()
    ).hexdigest()
    persisted = json.loads((root / "artifact_manifest.json").read_text(encoding="utf-8"))
    assert persisted == manifest


def test_manifest_fails_closed_for_missing_required_or_invalid_json(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    (root / "sfmea.json").write_text("not-json", encoding="utf-8")

    with pytest.raises(ArtifactContractError) as exc_info:
        materialize_ai_thread_manifest(
            root,
            run_id="run-2",
            declared_artifacts=[
                {"artifact": "sfmea.json", "type": "json", "required": True},
                {"artifact": "black_box_cases.json", "type": "json", "required": True},
            ],
            producer="agent_runtime:codex",
        )

    error = exc_info.value
    assert error.manifest["status"] == "rejected"
    rejected = {item["relative_path"]: item for item in error.manifest["artifacts"]}
    assert rejected["sfmea.json"]["validation_status"] == "rejected"
    assert rejected["black_box_cases.json"]["validation_status"] == "missing"
    assert (root / "artifact_manifest.json").exists()


@pytest.mark.parametrize("path", ["../secret.txt", "/tmp/secret.txt", "nested/../../secret.txt"])
def test_resolve_artifact_rejects_path_escape(tmp_path, path):
    with pytest.raises(ArtifactContractError):
        resolve_ai_thread_artifact(tmp_path, path)


def test_delivery_zip_contains_only_accepted_deliverables_and_redacts_text(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    (root / "report.md").write_text(
        "# Report\nAuthorization: Bearer zip-secret-value\n",
        encoding="utf-8",
    )
    (root / "diagnostics.log").write_text("internal", encoding="utf-8")
    manifest = materialize_ai_thread_manifest(
        root,
        run_id="run-zip",
        declared_artifacts=[
            {"artifact": "report.md", "type": "markdown", "required": True},
        ],
        producer="agent_runtime:codex",
    )

    payload = build_ai_thread_delivery_zip(root, manifest)

    with zipfile.ZipFile(BytesIO(payload)) as archive:
        assert sorted(archive.namelist()) == ["artifact_manifest.json", "report.md"]
        report = archive.read("report.md").decode("utf-8")
        assert "zip-secret-value" not in report
        assert "<redacted>" in report


def test_delivery_zip_fails_closed_when_an_accepted_file_changes(tmp_path):
    root = tmp_path / "delivery"
    root.mkdir()
    report = root / "report.md"
    report.write_text("# accepted report\n", encoding="utf-8")
    manifest = materialize_ai_thread_manifest(
        root,
        run_id="run-tampered",
        declared_artifacts=[{"artifact": "report.md", "required": True}],
        producer="agent:test",
    )

    report.write_text("# replaced after validation\n", encoding="utf-8")

    with pytest.raises(ArtifactContractError, match="哈希|大小"):
        build_ai_thread_delivery_zip(root, manifest)
