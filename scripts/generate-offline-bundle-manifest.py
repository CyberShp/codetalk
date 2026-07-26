#!/usr/bin/env python3
"""Inventory a supplied SDK wheel/tarball bundle without touching the network.

This is release evidence, not an installer.  Deployment owners can review the
result before promoting a developer POC bundle into the approved intranet
artifact repository.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import tarfile
import zipfile
from datetime import datetime, timezone
from email.parser import Parser
from pathlib import Path
from typing import Any


_SUFFIXES = (".whl", ".tgz", ".tar.gz", ".zip")
_LICENSE_ALIASES = {
    "mit license": "MIT",
    "apache software license": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "bsd license": "BSD",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_license(value: object) -> str:
    text = str(value or "").strip()
    normalized = text.lower()
    if (
        not text
        or normalized in {"unknown", "unlicensed", "see license in license"}
        or normalized.startswith("see license")
    ):
        return "UNKNOWN"
    return _LICENSE_ALIASES.get(normalized, text)


def _metadata_license(metadata: str) -> str:
    parsed = Parser().parsestr(metadata)
    for field in ("License-Expression", "License"):
        license_name = _normalize_license(parsed.get(field))
        if license_name != "UNKNOWN":
            return license_name
    for classifier in parsed.get_all("Classifier", []):
        if classifier.startswith("License ::"):
            license_name = _normalize_license(classifier.rsplit("::", 1)[-1])
            if license_name != "UNKNOWN":
                return license_name
    return "UNKNOWN"


def _wheel_metadata(path: Path) -> dict[str, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            candidate = next(
                name for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            )
            metadata = archive.read(candidate).decode("utf-8", errors="replace")
    except (OSError, StopIteration, zipfile.BadZipFile):
        return {}
    parsed = Parser().parsestr(metadata)
    return {
        "name": str(parsed.get("Name") or ""),
        "version": str(parsed.get("Version") or ""),
        "license": _metadata_license(metadata),
    }


def _npm_metadata(path: Path) -> dict[str, str]:
    try:
        with tarfile.open(path, "r:*") as archive:
            candidate = next(
                member for member in archive.getmembers()
                if member.name.rstrip("/").endswith("package/package.json")
                or member.name.rstrip("/").endswith("package.json")
            )
            handle = archive.extractfile(candidate)
            if handle is None:
                return {}
            payload = json.loads(handle.read().decode("utf-8", errors="replace"))
    except (OSError, StopIteration, tarfile.TarError, json.JSONDecodeError):
        return {}
    raw_license: Any = payload.get("license")
    if isinstance(raw_license, dict):
        raw_license = raw_license.get("type")
    return {
        "name": str(payload.get("name") or ""),
        "version": str(payload.get("version") or ""),
        "license": _normalize_license(raw_license),
    }


def _component(path: Path, bundle_dir: Path) -> dict[str, object]:
    metadata = _wheel_metadata(path) if path.suffix == ".whl" else _npm_metadata(path)
    return {
        "path": str(path.relative_to(bundle_dir)),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        "format": "wheel" if path.suffix == ".whl" else "npm-tarball",
        "name": str(metadata.get("name") or path.stem),
        "version": str(metadata.get("version") or "UNKNOWN"),
        "license": str(metadata.get("license") or "UNKNOWN"),
    }


def build_manifest(bundle_dir: Path) -> dict[str, object]:
    artifacts = sorted(
        path for path in bundle_dir.rglob("*")
        if path.is_file() and path.name.lower().endswith(_SUFFIXES)
    )
    components = [_component(path, bundle_dir) for path in artifacts]
    unknown = [item["path"] for item in components if item["license"] == "UNKNOWN"]
    return {
        "format": "codetalk-offline-bundle-manifest-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "network_used": False,
        "bundle_path": str(bundle_dir),
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "component_count": len(components),
        "unknown_license_count": len(unknown),
        "status": "needs_human_review" if unknown else "ready_for_human_approval",
        "components": components,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    bundle_dir = args.bundle_dir.resolve()
    if not bundle_dir.is_dir():
        raise SystemExit(f"bundle directory does not exist: {bundle_dir}")
    payload = build_manifest(bundle_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
