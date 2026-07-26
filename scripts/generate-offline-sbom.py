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


LICENSE_ALIASES = {
    "mit license": "MIT",
    "bsd license": "BSD",
    "bsd 3-clause": "BSD-3-Clause",
    "bsd-3-clause license": "BSD-3-Clause",
    "apache software license": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache-2.0": "Apache-2.0",
}

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def component(name: str, version: str, source: str, license_name: str = "UNKNOWN") -> dict:
    return {"name": name, "version": version, "source": source, "license": license_name or "UNKNOWN"}


def normalize_license(value: object) -> str:
    text = str(value or "").strip()
    if not text or text.upper() in {"UNKNOWN", "UNLICENSED", "SEE LICENSE IN LICENSE"}:
        return "UNKNOWN"
    return LICENSE_ALIASES.get(text.lower(), text)


def python_license(meta: importlib.metadata.PackageMetadata) -> str:
    for field in ("License-Expression", "License"):
        resolved = normalize_license(meta.get(field))
        if resolved != "UNKNOWN":
            return resolved
    for classifier in meta.get_all("Classifier", []):
        if classifier.startswith("License ::"):
            resolved = normalize_license(classifier.rsplit("::", 1)[-1])
            if resolved != "UNKNOWN":
                return resolved
    return "UNKNOWN"


def node_license(package_path: Path) -> str:
    try:
        metadata = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "UNKNOWN"
    raw = metadata.get("license")
    if isinstance(raw, dict):
        raw = raw.get("type")
    return normalize_license(raw)


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
            components.append(component(name, importlib.metadata.version(name), "python-installed", python_license(meta)))
        except importlib.metadata.PackageNotFoundError:
            components.append(component(name, "UNRESOLVED", "backend-requirements"))
    package_lock = json.loads(lock.read_text())
    for path, item in sorted((package_lock.get("packages") or {}).items()):
        if not path.startswith("node_modules/"):
            continue
        name = path.removeprefix("node_modules/")
        components.append(component(
            name,
            str(item.get("version") or "UNRESOLVED"),
            "frontend-package-lock",
            node_license(root / "frontend" / path / "package.json"),
        ))
    payload = {
        "bomFormat": "CodeTalk-offline-sbom", "specVersion": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "network_used": False,
        "manifests": [{"path": str(backend.relative_to(root)), "sha256": sha256(backend)}, {"path": str(lock.relative_to(root)), "sha256": sha256(lock)}],
        "components": sorted(components, key=lambda item: (item["name"], item["version"])),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n")
    unknown = [item for item in payload["components"] if item["license"] == "UNKNOWN"]
    review = {
        "review_format": "CodeTalk-offline-license-review",
        "generated_at": payload["generated_at"],
        "network_used": False,
        "sbom_sha256": sha256(args.output),
        "component_count": len(payload["components"]),
        "unknown_license_count": len(unknown),
        "status": "needs_human_review" if unknown else "ready_for_human_approval",
        "unknown_components": unknown,
        "approval": {
            "decision": "pending",
            "note": "This local report is an inventory, not a license approval."
        },
    }
    review_path = args.output.with_name("license-review.json")
    review_path.write_text(json.dumps(review, ensure_ascii=True, indent=2) + "\n")


if __name__ == "__main__":
    main()
