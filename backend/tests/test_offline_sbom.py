"""Offline dependency evidence must not turn locally available license data into UNKNOWN."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_offline_sbom_reads_local_python_and_node_license_metadata(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "sbom.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "generate-offline-sbom.py"),
            "--root",
            str(root),
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    components = {item["name"]: item for item in payload["components"]}
    assert payload["network_used"] is False
    assert components["fastapi"]["license"] == "MIT"
    assert components["next"]["license"] == "MIT"
    review = json.loads((tmp_path / "license-review.json").read_text(encoding="utf-8"))
    assert review["network_used"] is False
    assert review["status"] in {"ready_for_human_approval", "needs_human_review"}
    assert review["unknown_license_count"] == sum(
        item["license"] == "UNKNOWN" for item in payload["components"]
    )
