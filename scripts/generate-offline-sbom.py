#!/usr/bin/env python3
"""Generate a local-only CycloneDX-like dependency evidence document.

The command intentionally has no network client. It reads pinned frontend
metadata, backend requirements and Python distribution metadata already
available on the machine, then hashes the input manifests for release review.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def component(name: str, version: str, source: str, license_name: str = "UNKNOWN") -> dict:
    return {"name": name, "version": version, "source": source, "license": license_name or "UNKNOWN"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=Path(__file__).resolve().parents[1], type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    backend = root / "backend" / "requirements.txt"
    lock = root / "frontend" / "package-lock.json"
    components: list[dict] = []
    for line in backend.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = line.split("==", 1)[0].split(">=", 1)[0].split("[", 1)[0]
        try:
            meta = importlib.metadata.metadata(name)
            components.append(component(name, importlib.metadata.version(name), "python-installed", meta.get("License", "UNKNOWN")))
        except importlib.metadata.PackageNotFoundError:
            components.append(component(name, "UNRESOLVED", "backend-requirements"))
    package_lock = json.loads(lock.read_text())
    for path, item in sorted((package_lock.get("packages") or {}).items()):
        if not path.startswith("node_modules/"):
            continue
        name = path.removeprefix("node_modules/")
        components.append(component(name, str(item.get("version") or "UNRESOLVED"), "frontend-package-lock"))
    payload = {
        "bomFormat": "CodeTalk-offline-sbom", "specVersion": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "network_used": False,
        "manifests": [{"path": str(backend.relative_to(root)), "sha256": sha256(backend)}, {"path": str(lock.relative_to(root)), "sha256": sha256(lock)}],
        "components": sorted(components, key=lambda item: (item["name"], item["version"])),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n")


if __name__ == "__main__":
    main()
