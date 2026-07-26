"""Offline SDK bundle evidence must be complete and hash-addressable."""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path


def _write_wheel(path: Path, *, name: str, version: str, license_name: str) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"{name}-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\nLicense: {license_name}\n",
        )


def test_offline_bundle_manifest_hashes_local_wheels_without_network(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    bundle = tmp_path / "wheels"
    bundle.mkdir()
    _write_wheel(bundle / "example-1.0.0-py3-none-any.whl", name="example", version="1.0.0", license_name="MIT")
    _write_wheel(bundle / "review-1.0.0-py3-none-any.whl", name="review", version="1.0.0", license_name="SEE LICENSE IN README.md")
    output = tmp_path / "offline-bundle-manifest.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "generate-offline-bundle-manifest.py"),
            "--bundle-dir",
            str(bundle),
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["network_used"] is False
    assert payload["component_count"] == 2
    assert payload["unknown_license_count"] == 1
    assert payload["status"] == "needs_human_review"
    components = {item["name"]: item for item in payload["components"]}
    assert components["example"]["license"] == "MIT"
    assert components["review"]["license"] == "UNKNOWN"
    assert len(components["example"]["sha256"]) == 64
