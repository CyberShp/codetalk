from pathlib import Path

from app.services.workbench_artifact_manifest import artifact_preview_with_redaction_status


def test_artifact_preview_redacts_before_truncating_secret_boundary(tmp_path):
    secret = "boundaryPreviewSecretLeakValue1234567890"
    artifact = tmp_path / "diagnostics.log"
    artifact.write_text(
        ("x" * 1170) + f"\nAuthorization: Bearer {secret}\n",
        encoding="utf-8",
    )

    preview, redacted = artifact_preview_with_redaction_status(
        Path(artifact),
        artifact.read_bytes(),
        max_chars=1200,
    )

    assert redacted is True
    assert secret not in preview
    assert "boundary" not in preview
    assert "<redacted>" in preview
