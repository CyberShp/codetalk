"""Coverage input adapters used by the coverage-gap workflow step."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.adapters.coverage import (
    detect_and_parse_xml,
    parse_html_coverage,
    parse_internal_function_hits,
    parse_internal_function_hits_xlsx,
)


def parse_coverage_inputs(input_snapshot: dict[str, Any]) -> dict[str, Any]:
    coverage_inputs = _coverage_input_payloads(input_snapshot)
    files: list[dict[str, Any]] = []
    uncovered_functions: list[dict[str, Any]] = []
    warnings: list[str] = []
    source_formats: list[str] = []
    for item in coverage_inputs:
        filename = str(item.get("filename") or item.get("path") or "coverage")
        suffix = str(item.get("suffix") or Path(filename).suffix).lower()
        try:
            parsed = _parse_coverage_document(item, suffix=suffix)
        except (OSError, ValueError, TypeError) as exc:
            warnings.append(f"{filename}: coverage parse failed: {exc}")
            continue
        source_format = str(parsed.get("source_format") or "unknown")
        if source_format not in source_formats:
            source_formats.append(source_format)
        files.extend(parsed["files"])
        uncovered_functions.extend(parsed["uncovered_functions"])
    return {
        "kind": "coverage_parse",
        "inputs": coverage_inputs,
        "files": files,
        "uncovered_functions": uncovered_functions,
        "summary": _coverage_summary(
            files,
            uncovered_functions,
            warnings,
            source_formats,
        ),
    }


def coverage_report_payload(report: Any) -> dict[str, Any]:
    """Normalize a parser-specific report without collapsing file identity."""

    files: list[dict[str, Any]] = []
    uncovered: list[dict[str, Any]] = []
    modules = report.modules if isinstance(getattr(report, "modules", None), list) else []
    for module in modules:
        module_files = module.files if isinstance(getattr(module, "files", None), list) else []
        if not module_files:
            module_files = [
                type(
                    "CoverageFile",
                    (),
                    {
                        "filename": module.module_path,
                        "line_rate": module.line_rate,
                        "branch_rate": module.branch_rate,
                        "uncovered_functions": module.uncovered_functions,
                        "function_hits": module.function_hits,
                    },
                )()
            ]
        for file_item in module_files:
            hits = list(getattr(file_item, "function_hits", None) or [])
            uncovered_names = list(getattr(file_item, "uncovered_functions", None) or [])
            covered_functions = sum(
                1
                for hit in hits
                if bool(getattr(hit, "triggered", False))
                or int(getattr(hit, "hit_count", 0) or 0) > 0
            )
            function_count = len(hits) or len(uncovered_names)
            file_path = str(
                getattr(file_item, "filename", "") or module.module_path
            )
            files.append(
                {
                    "file_path": file_path,
                    "function_count": function_count,
                    "covered_function_count": covered_functions,
                    "uncovered_function_count": max(
                        function_count - covered_functions,
                        len(uncovered_names),
                    ),
                    "line_rate": float(getattr(file_item, "line_rate", 0.0) or 0.0),
                    "branch_rate": float(getattr(file_item, "branch_rate", 0.0) or 0.0),
                }
            )
            for hit in hits:
                if bool(getattr(hit, "triggered", False)) or int(
                    getattr(hit, "hit_count", 0) or 0
                ) > 0:
                    continue
                uncovered.append(
                    {
                        "file_path": str(
                            getattr(hit, "file_path", "") or file_path
                        ),
                        "function_name": str(
                            getattr(hit, "function_name", "") or "unknown"
                        ),
                        "line_start": getattr(hit, "line_start", None),
                        "hit_count": int(getattr(hit, "hit_count", 0) or 0),
                    }
                )
            for name in uncovered_names:
                if any(
                    item["function_name"] == str(name)
                    and item["file_path"] == file_path
                    for item in uncovered
                ):
                    continue
                uncovered.append(
                    {
                        "file_path": file_path,
                        "function_name": str(name),
                        "line_start": None,
                        "hit_count": 0,
                    }
                )
    return {
        "source_format": str(
            getattr(report, "source_format", "unknown") or "unknown"
        ),
        "files": files,
        "uncovered_functions": uncovered,
    }


def _parse_coverage_document(
    payload: dict[str, Any],
    *,
    suffix: str,
) -> dict[str, Any]:
    if suffix in {".lcov", ".info"} or suffix == "":
        text = _read_text(payload)
        if not text:
            raise ValueError("empty coverage text")
        return {**_parse_lcov(text), "source_format": "lcov"}
    if suffix in {".xlsx", ".xlsm"}:
        raw = _read_bytes(payload)
        if not raw:
            raise ValueError("empty XLSX coverage file")
        return coverage_report_payload(parse_internal_function_hits_xlsx(raw))
    text = _read_text(payload)
    if not text:
        raise ValueError("empty coverage text")
    if suffix in {".html", ".htm"} or "<html" in text[:500].lower():
        return coverage_report_payload(parse_html_coverage(text))
    if suffix == ".xml" or text.lstrip().startswith("<"):
        return coverage_report_payload(detect_and_parse_xml(text))
    if suffix in {".csv", ".tsv", ".txt"}:
        return coverage_report_payload(parse_internal_function_hits(text))
    raise ValueError(f"unsupported coverage format: {suffix or 'unknown'}")


def _coverage_input_payloads(input_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for input_id, value in input_snapshot.items():
        if not isinstance(value, dict):
            continue
        if value.get("kind") == "file_set":
            for file_item in value.get("files") or []:
                if isinstance(file_item, dict) and _is_coverage_file(file_item):
                    payload = dict(file_item)
                    payload.setdefault("input_id", str(input_id))
                    payloads.append(payload)
            continue
        if _is_coverage_file(value):
            payload = dict(value)
            payload.setdefault("input_id", str(input_id))
            payloads.append(payload)
    return payloads


def _is_coverage_file(payload: dict[str, Any]) -> bool:
    suffix = str(payload.get("suffix") or "").lower()
    filename = str(payload.get("filename") or "").lower()
    return suffix in {
        ".lcov",
        ".info",
        ".xml",
        ".html",
        ".htm",
        ".csv",
        ".tsv",
        ".txt",
        ".xlsx",
        ".xlsm",
    } or "coverage" in filename


def _parse_lcov(text: str) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    uncovered: list[dict[str, Any]] = []
    current_file = ""
    function_lines: dict[str, int] = {}
    function_hits: dict[str, int] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("SF:"):
            current_file = line[3:].replace("\\", "/")
            function_lines = {}
            function_hits = {}
        elif line.startswith("FN:"):
            line_text, _, function_name = line[3:].partition(",")
            if function_name:
                function_lines[function_name] = _safe_int(line_text)
        elif line.startswith("FNDA:"):
            hit_text, _, function_name = line[5:].partition(",")
            if function_name:
                function_hits[function_name] = _safe_int(hit_text)
        elif line == "end_of_record":
            if current_file:
                file_uncovered = []
                for function_name, line_start in function_lines.items():
                    hit_count = function_hits.get(function_name, 0)
                    if hit_count == 0:
                        item = {
                            "file_path": current_file,
                            "function_name": function_name,
                            "line_start": line_start,
                            "hit_count": hit_count,
                        }
                        file_uncovered.append(item)
                        uncovered.append(item)
                files.append(
                    {
                        "file_path": current_file,
                        "function_count": len(function_lines),
                        "covered_function_count": sum(
                            1
                            for name in function_lines
                            if function_hits.get(name, 0) > 0
                        ),
                        "uncovered_function_count": len(file_uncovered),
                    }
                )
            current_file = ""
            function_lines = {}
            function_hits = {}
    return {"files": files, "uncovered_functions": uncovered}


def _coverage_summary(
    files: list[dict[str, Any]],
    uncovered_functions: list[dict[str, Any]],
    warnings: list[str],
    source_formats: list[str],
) -> dict[str, Any]:
    function_count = sum(int(item.get("function_count") or 0) for item in files)
    covered_count = sum(
        int(item.get("covered_function_count") or 0) for item in files
    )
    return {
        "files_count": len(files),
        "function_count": function_count,
        "covered_function_count": covered_count,
        "uncovered_function_count": len(uncovered_functions),
        "function_coverage_percent": (
            round(covered_count * 100 / function_count, 2) if function_count else 0.0
        ),
        "source_formats": source_formats,
        "warnings": warnings,
    }


def _read_text(payload: dict[str, Any]) -> str:
    text = str(payload.get("text") or payload.get("content") or "")
    if text:
        return text
    for key in ("parsed_text_path", "copied_path", "original_path", "path"):
        path_text = str(payload.get(key) or "")
        if not path_text:
            continue
        try:
            path = Path(path_text)
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return ""


def _read_bytes(payload: dict[str, Any]) -> bytes:
    for key in ("copied_path", "parsed_text_path", "original_path", "path"):
        path_text = str(payload.get(key) or "")
        if not path_text:
            continue
        try:
            path = Path(path_text)
            if path.is_file():
                return path.read_bytes()
        except OSError:
            continue
    return b""


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
