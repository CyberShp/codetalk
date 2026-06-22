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
    for input_id, value in (inputs or {}).items():
        definition = defs_by_id.get(str(input_id), {})
        input_type = str(definition.get("type") or "")
        if input_type in {"file", "coverage_report", "diff", "patch"}:
            snapshot[str(input_id)] = _ingest_file(
                input_id=str(input_id),
                value=value,
                root=root,
            )
        else:
            snapshot[str(input_id)] = value
    return snapshot


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
