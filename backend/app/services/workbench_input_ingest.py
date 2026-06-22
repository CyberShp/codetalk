"""Input ingestion for workbench task runs."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


TEXT_EXTS = frozenset({
    ".md", ".txt", ".patch", ".diff", ".json", ".xml", ".lcov", ".info",
    ".csv", ".tsv", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".log",
})


def ingest_workbench_inputs(
    *,
    input_definitions: list[dict[str, Any]],
    inputs: dict[str, Any],
    artifact_dir: str | Path,
) -> dict[str, Any]:
    root = Path(artifact_dir) / "inputs"
    root.mkdir(parents=True, exist_ok=True)
    defs_by_id = {
        str(item.get("id")): item
        for item in input_definitions
        if isinstance(item, dict) and item.get("id")
    }
    snapshot: dict[str, Any] = {}
    for input_id, definition in defs_by_id.items():
        if bool(definition.get("required", False)) and _is_missing_input((inputs or {}).get(input_id)):
            raise ValueError(f"required input {input_id} is missing")
    for input_id, value in (inputs or {}).items():
        input_key = str(input_id)
        definition = defs_by_id.get(input_key, {})
        input_type = str(definition.get("type") or "")
        if input_type in {"file", "coverage_report", "diff", "patch"}:
            snapshot[input_key] = _ingest_file(
                input_id=input_key,
                value=value,
                root=root,
            )
        elif input_type == "file_set":
            snapshot[input_key] = _ingest_file_set(
                input_id=input_key,
                value=value,
                root=root,
            )
        else:
            snapshot[input_key] = value
    return snapshot


def _is_missing_input(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return False


def _ingest_file_set(*, input_id: str, value: Any, root: Path) -> dict[str, Any]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"file_set input {input_id} must be a list of file paths")
    input_root = root / _safe_name(input_id)
    files: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        file_info = _ingest_file(
            input_id=f"{input_id}_{index + 1}",
            value=item,
            root=input_root,
        )
        file_info["file_set_index"] = index
        files.append(file_info)
    if not files:
        raise ValueError(f"file_set input {input_id} is missing files")
    manifest = {
        "kind": "file_set",
        "input_id": input_id,
        "count": len(files),
        "files": files,
    }
    manifest_path = input_root / "file_set_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def _ingest_file(*, input_id: str, value: Any, root: Path) -> dict[str, Any]:
    path_text = str(value.get("path") if isinstance(value, dict) else value or "").strip()
    if not path_text:
        raise ValueError(f"file input {input_id} is missing path")
    source = Path(path_text)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(path_text)
    input_root = root / _safe_name(input_id)
    original_dir = input_root / "original"
    original_dir.mkdir(parents=True, exist_ok=True)
    copied = original_dir / source.name
    shutil.copy2(source, copied)
    data = copied.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()

    parsed_text = _extract_text(copied)
    parsed_text_path = input_root / "parsed_text.txt"
    parsed_text_path.write_text(parsed_text, encoding="utf-8")
    chunks = _chunks(parsed_text)
    chunks_path = input_root / "chunks.json"
    chunks_path.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
    metadata = {
        "kind": "file",
        "input_id": input_id,
        "original_path": str(source),
        "copied_path": str(copied),
        "filename": source.name,
        "suffix": source.suffix.lower(),
        "size_bytes": len(data),
        "sha256": sha256,
        "parsed_text_path": str(parsed_text_path),
        "chunks_path": str(chunks_path),
        "parse_warnings": [] if parsed_text else ["text_extraction_empty"],
    }
    metadata_path = input_root / "file_metadata.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    metadata["metadata_path"] = str(metadata_path)
    return metadata


def _extract_text(path: Path) -> str:
    if path.suffix.lower() not in TEXT_EXTS:
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _chunks(text: str, *, chunk_size: int = 4000) -> list[dict[str, Any]]:
    if not text:
        return []
    chunks: list[dict[str, Any]] = []
    for index, start in enumerate(range(0, len(text), chunk_size)):
        chunk = text[start:start + chunk_size]
        chunks.append({
            "chunk_index": index,
            "start_char": start,
            "end_char": start + len(chunk),
            "content": chunk,
        })
    return chunks


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value) or "input"
