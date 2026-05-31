"""Coverage data adapters — supports file upload (XML/HTML) and reserved
intranet API integration.

Supported XML formats:
  - Cobertura (pytest-cov, Istanbul/nyc, gcc/gcovr)
  - JaCoCo

HTML parsing: best-effort extraction from common coverage report generators.
"""

from __future__ import annotations

import logging
import csv
import io
import re
from dataclasses import dataclass, field
from typing import Protocol
import defusedxml.ElementTree as ET

logger = logging.getLogger(__name__)


@dataclass
class FunctionHit:
    function_name: str
    file_path: str
    line_start: int | None = None
    line_end: int | None = None
    triggered: bool = False
    hit_count: int = 0
    raw_location: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class FileCoverage:
    filename: str
    line_rate: float
    branch_rate: float
    lines_covered: int = 0
    lines_total: int = 0
    branches_covered: int = 0
    branches_total: int = 0
    uncovered_lines: list[int] = field(default_factory=list)
    uncovered_branches: list[str] = field(default_factory=list)
    uncovered_functions: list[str] = field(default_factory=list)
    function_hits: list[FunctionHit] = field(default_factory=list)


@dataclass
class ModuleCoverage:
    module_path: str
    line_rate: float
    branch_rate: float
    function_rate: float
    files: list[FileCoverage] = field(default_factory=list)
    uncovered_lines: list[str] = field(default_factory=list)
    uncovered_branches: list[str] = field(default_factory=list)
    uncovered_functions: list[str] = field(default_factory=list)
    function_hits: list[FunctionHit] = field(default_factory=list)


@dataclass
class CoverageReport:
    overall_line_rate: float
    overall_branch_rate: float
    overall_function_rate: float
    modules: list[ModuleCoverage] = field(default_factory=list)
    source_format: str = "unknown"
    raw_metadata: dict = field(default_factory=dict)


class CoverageDataSource(Protocol):
    """Protocol for coverage data sources.

    Implementations:
      - FileUploadSource: parses user-uploaded XML/HTML files
      - IntranetApiSource: (reserved) fetches from intranet instrumentation tool
    """

    async def fetch_report(self, **kwargs: object) -> CoverageReport: ...


# ── Reserved intranet API adapter ──────────────────────────────────────

class IntranetCoverageAdapter:
    """Reserved adapter for the intranet precise testing tool.

    The actual API endpoint and authentication scheme are TBD.
    When the intranet tool's API is finalized, implement fetch_report()
    to call the real endpoint.
    """

    def __init__(self, base_url: str = "", api_key: str = "") -> None:
        self.base_url = base_url
        self.api_key = api_key

    async def fetch_report(self, **kwargs: object) -> CoverageReport:
        raise NotImplementedError(
            "内网精准测试工具 API 尚未对接，请使用文件上传方式"
        )


# ── File-based parsers ─────────────────────────────────────────────────

def parse_cobertura_xml(xml_content: str) -> CoverageReport:
    """Parse Cobertura-format XML coverage report."""
    root = ET.fromstring(xml_content)

    overall_line = float(root.get("line-rate", "0"))
    overall_branch = float(root.get("branch-rate", "0"))

    modules: list[ModuleCoverage] = []
    total_functions = 0
    covered_functions = 0

    for package in root.findall(".//package"):
        pkg_name = package.get("name", "unknown")
        pkg_line = float(package.get("line-rate", "0"))
        pkg_branch = float(package.get("branch-rate", "0"))

        files: list[FileCoverage] = []
        pkg_uncov_lines: list[str] = []
        pkg_uncov_branches: list[str] = []
        pkg_uncov_funcs: list[str] = []
        pkg_total_funcs = 0
        pkg_covered_funcs = 0

        for cls in package.findall(".//class"):
            fname = cls.get("filename", "")
            cls_line_rate = float(cls.get("line-rate", "0"))
            cls_branch_rate = float(cls.get("branch-rate", "0"))

            uncov_lines: list[int] = []
            uncov_branches: list[str] = []
            uncov_funcs: list[str] = []
            lines_total = 0
            lines_covered = 0
            branches_total = 0
            branches_covered = 0

            for line in cls.findall(".//line"):
                lines_total += 1
                hits = int(line.get("hits", "0"))
                if hits > 0:
                    lines_covered += 1
                else:
                    uncov_lines.append(int(line.get("number", "0")))

                if line.get("branch") == "true":
                    cond = line.get("condition-coverage", "")
                    match = re.match(r"(\d+)%\s*\((\d+)/(\d+)\)", cond)
                    if match:
                        bc = int(match.group(2))
                        bt = int(match.group(3))
                        branches_total += bt
                        branches_covered += bc
                        if bc < bt:
                            uncov_branches.append(
                                f"{fname}:L{line.get('number', '?')} ({cond})"
                            )

            for method in cls.findall(".//method"):
                pkg_total_funcs += 1
                total_functions += 1
                method_hits = sum(
                    int(l.get("hits", "0")) for l in method.findall(".//line")
                )
                if method_hits > 0:
                    pkg_covered_funcs += 1
                    covered_functions += 1
                else:
                    mname = method.get("name", "unknown")
                    uncov_funcs.append(f"{fname}:{mname}")

            fc = FileCoverage(
                filename=fname,
                line_rate=cls_line_rate,
                branch_rate=cls_branch_rate,
                lines_covered=lines_covered,
                lines_total=lines_total,
                branches_covered=branches_covered,
                branches_total=branches_total,
                uncovered_lines=uncov_lines,
                uncovered_branches=uncov_branches,
                uncovered_functions=uncov_funcs,
            )
            files.append(fc)

            for ul in uncov_lines:
                pkg_uncov_lines.append(f"{fname}:{ul}")
            pkg_uncov_branches.extend(uncov_branches)
            pkg_uncov_funcs.extend(uncov_funcs)

        func_rate = (pkg_covered_funcs / pkg_total_funcs) if pkg_total_funcs > 0 else 1.0

        modules.append(ModuleCoverage(
            module_path=pkg_name,
            line_rate=pkg_line,
            branch_rate=pkg_branch,
            function_rate=func_rate,
            files=files,
            uncovered_lines=pkg_uncov_lines[:200],
            uncovered_branches=pkg_uncov_branches[:100],
            uncovered_functions=pkg_uncov_funcs[:100],
        ))

    overall_func = (covered_functions / total_functions) if total_functions > 0 else 1.0

    return CoverageReport(
        overall_line_rate=overall_line,
        overall_branch_rate=overall_branch,
        overall_function_rate=overall_func,
        modules=modules,
        source_format="cobertura",
    )


def parse_jacoco_xml(xml_content: str) -> CoverageReport:
    """Parse JaCoCo XML coverage report."""
    root = ET.fromstring(xml_content)

    def _counter(el: ET.Element, ctype: str) -> tuple[int, int]:
        for c in el.findall("counter"):
            if c.get("type") == ctype:
                missed = int(c.get("missed", "0"))
                covered = int(c.get("covered", "0"))
                return covered, missed + covered
        return 0, 0

    line_c, line_t = _counter(root, "LINE")
    branch_c, branch_t = _counter(root, "BRANCH")
    method_c, method_t = _counter(root, "METHOD")

    overall_line = (line_c / line_t) if line_t > 0 else 1.0
    overall_branch = (branch_c / branch_t) if branch_t > 0 else 1.0
    overall_func = (method_c / method_t) if method_t > 0 else 1.0

    modules: list[ModuleCoverage] = []

    for pkg in root.findall(".//package"):
        pkg_name = pkg.get("name", "unknown").replace("/", ".")
        plc, plt = _counter(pkg, "LINE")
        pbc, pbt = _counter(pkg, "BRANCH")
        pmc, pmt = _counter(pkg, "METHOD")

        uncov_funcs: list[str] = []
        files: list[FileCoverage] = []

        for src in pkg.findall("class"):
            src_name = src.get("sourcefilename", src.get("name", ""))
            slc, slt = _counter(src, "LINE")
            sbc, sbt = _counter(src, "BRANCH")

            for method in src.findall("method"):
                mc, mt = _counter(method, "LINE")
                if mt > 0 and mc == 0:
                    uncov_funcs.append(f"{src_name}:{method.get('name', '?')}")

            files.append(FileCoverage(
                filename=src_name,
                line_rate=(slc / slt) if slt > 0 else 1.0,
                branch_rate=(sbc / sbt) if sbt > 0 else 1.0,
                lines_covered=slc,
                lines_total=slt,
                branches_covered=sbc,
                branches_total=sbt,
            ))

        modules.append(ModuleCoverage(
            module_path=pkg_name,
            line_rate=(plc / plt) if plt > 0 else 1.0,
            branch_rate=(pbc / pbt) if pbt > 0 else 1.0,
            function_rate=(pmc / pmt) if pmt > 0 else 1.0,
            files=files,
            uncovered_functions=uncov_funcs[:100],
        ))

    return CoverageReport(
        overall_line_rate=overall_line,
        overall_branch_rate=overall_branch,
        overall_function_rate=overall_func,
        modules=modules,
        source_format="jacoco",
    )


def detect_and_parse_xml(xml_content: str) -> CoverageReport:
    """Auto-detect XML format (Cobertura vs JaCoCo) and parse."""
    content_stripped = xml_content.strip()
    if "<report " in content_stripped[:500] or 'name="JaCoCo"' in content_stripped[:500]:
        return parse_jacoco_xml(xml_content)
    return parse_cobertura_xml(xml_content)


_FUNC_HEADERS = {
    "function", "functionname", "name", "symbol", "symbolname",
    "函数", "函数名", "方法", "方法名",
}
_LOCATION_HEADERS = {
    "location", "codelocation", "source", "path", "file", "filename",
    "filepath", "代码位置", "源码位置", "文件位置", "文件", "路径",
}
_TRIGGER_HEADERS = {
    "triggered", "covered", "hit", "executed", "visited", "是否触发",
    "触发", "是否覆盖", "覆盖", "是否执行", "执行",
}
_COUNT_HEADERS = {
    "hitcount", "hits", "count", "times", "触发次数", "命中次数",
    "执行次数", "次数",
}


def parse_internal_function_hits(content: str) -> CoverageReport:
    """Parse the intranet precise-testing function hit table.

    Expected logical columns are function name, code location, triggered flag,
    and hit count. The exact intranet header names are still unsettled, so this
    parser accepts common English/Chinese aliases and a headerless four-column
    fallback.
    """
    rows = _read_delimited_rows(content)
    if not rows:
        raise ValueError("No rows found in internal function-hit coverage file")

    header = [_normalize_header(c) for c in rows[0]]
    has_header = any(h in _FUNC_HEADERS | _LOCATION_HEADERS for h in header)
    if has_header:
        data_rows = rows[1:]
        indexes = _internal_hit_indexes(header)
    else:
        data_rows = rows
        indexes = {"function": 0, "location": 1, "triggered": 2, "hit_count": 3}

    hits: list[FunctionHit] = []
    for row in data_rows:
        if not any(c.strip() for c in row):
            continue
        if not has_header and len(row) < 4:
            continue
        hit = _row_to_function_hit(row, indexes)
        if hit.function_name and hit.file_path:
            hits.append(hit)

    if not hits:
        raise ValueError("No function hit records found in internal coverage file")

    return _function_hits_to_report(hits)


def _read_delimited_rows(content: str) -> list[list[str]]:
    sample = next((line for line in content.splitlines() if line.strip()), "")
    delimiter = "\t" if "\t" in sample else "|" if "|" in sample else ";" if ";" in sample else ","
    reader = csv.reader(io.StringIO(content), delimiter=delimiter)
    return [[cell.strip() for cell in row] for row in reader if any(c.strip() for c in row)]


def _normalize_header(value: str) -> str:
    return re.sub(r"[\s_\-:/()（）]+", "", value.strip().lstrip("\ufeff").lower())


def _internal_hit_indexes(header: list[str]) -> dict[str, int]:
    indexes: dict[str, int] = {}
    for idx, name in enumerate(header):
        if name in _FUNC_HEADERS and "function" not in indexes:
            indexes["function"] = idx
        elif name in _LOCATION_HEADERS and "location" not in indexes:
            indexes["location"] = idx
        elif name in _TRIGGER_HEADERS and "triggered" not in indexes:
            indexes["triggered"] = idx
        elif name in _COUNT_HEADERS and "hit_count" not in indexes:
            indexes["hit_count"] = idx

    missing = [key for key in ("function", "location") if key not in indexes]
    if missing:
        raise ValueError(
            "Internal coverage file missing required columns: " + ", ".join(missing)
        )
    return indexes


def _row_to_function_hit(row: list[str], indexes: dict[str, int]) -> FunctionHit:
    def cell(key: str) -> str:
        idx = indexes.get(key)
        if idx is None or idx >= len(row):
            return ""
        return row[idx].strip()

    raw_location = cell("location")
    file_path, line_start, line_end = _parse_location(raw_location)
    hit_count = _parse_hit_count(cell("hit_count"))
    triggered = _parse_triggered(cell("triggered"), hit_count)

    return FunctionHit(
        function_name=cell("function"),
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        triggered=triggered,
        hit_count=hit_count,
        raw_location=raw_location,
        raw={
            "function_name": cell("function"),
            "code_location": raw_location,
            "triggered": cell("triggered"),
            "hit_count": cell("hit_count"),
        },
    )


def _parse_location(value: str) -> tuple[str, int | None, int | None]:
    text = value.strip().strip("\"'")
    match = re.match(
        r"^(?P<path>.+?)[(:\[]\s*(?:L|line\s*)?"
        r"(?P<start>\d+)(?:\s*[-,~]\s*(?:L)?(?P<end>\d+))?\s*[\])]*$",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return _normalize_path(text), None, None
    file_path = _normalize_path(match.group("path"))
    line_start = int(match.group("start"))
    line_end = int(match.group("end") or line_start)
    return file_path, line_start, line_end


def _normalize_path(value: str) -> str:
    path = value.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def _parse_hit_count(value: str) -> int:
    if not value:
        return 0
    match = re.search(r"-?\d+", value)
    return max(0, int(match.group(0))) if match else 0


def _parse_triggered(value: str, hit_count: int) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return hit_count > 0
    if normalized in {"1", "true", "yes", "y", "covered", "hit", "executed", "是", "已触发", "触发", "已覆盖", "执行"}:
        return True
    if normalized in {"0", "false", "no", "n", "uncovered", "missed", "notexecuted", "否", "未触发", "未覆盖", "未执行"}:
        return False
    if re.fullmatch(r"\d+", normalized):
        return int(normalized) > 0
    return hit_count > 0


def _function_hits_to_report(hits: list[FunctionHit]) -> CoverageReport:
    by_module: dict[str, list[FunctionHit]] = {}
    for hit in hits:
        by_module.setdefault(_module_from_path(hit.file_path), []).append(hit)

    modules: list[ModuleCoverage] = []
    for module_path, module_hits in sorted(by_module.items()):
        covered = sum(1 for hit in module_hits if hit.triggered or hit.hit_count > 0)
        rate = covered / len(module_hits)
        by_file: dict[str, list[FunctionHit]] = {}
        for hit in module_hits:
            by_file.setdefault(hit.file_path, []).append(hit)

        files: list[FileCoverage] = []
        for filename, file_hits in sorted(by_file.items()):
            file_covered = sum(1 for hit in file_hits if hit.triggered or hit.hit_count > 0)
            file_rate = file_covered / len(file_hits)
            files.append(
                FileCoverage(
                    filename=filename,
                    line_rate=file_rate,
                    branch_rate=0.0,
                    lines_covered=file_covered,
                    lines_total=len(file_hits),
                    uncovered_functions=[
                        _function_hit_label(hit) for hit in file_hits
                        if not (hit.triggered or hit.hit_count > 0)
                    ],
                    function_hits=file_hits,
                )
            )

        uncovered = [
            _function_hit_label(hit)
            for hit in module_hits
            if not (hit.triggered or hit.hit_count > 0)
        ]
        modules.append(
            ModuleCoverage(
                module_path=module_path,
                line_rate=rate,
                branch_rate=0.0,
                function_rate=rate,
                files=files,
                uncovered_lines=[
                    f"{hit.file_path}:{hit.line_start}"
                    for hit in module_hits
                    if not (hit.triggered or hit.hit_count > 0) and hit.line_start
                ][:200],
                uncovered_functions=uncovered[:100],
                function_hits=module_hits,
            )
        )

    covered_total = sum(1 for hit in hits if hit.triggered or hit.hit_count > 0)
    overall = covered_total / len(hits)
    return CoverageReport(
        overall_line_rate=overall,
        overall_branch_rate=0.0,
        overall_function_rate=overall,
        modules=modules,
        source_format="internal_function_hits",
        raw_metadata={"record_count": len(hits), "covered_count": covered_total},
    )


def _module_from_path(path: str) -> str:
    normalized = _normalize_path(path)
    if "/" not in normalized:
        return "(root)"
    parent = normalized.rsplit("/", 1)[0]
    return parent or "(root)"


def _function_hit_label(hit: FunctionHit) -> str:
    if hit.line_start is None:
        return f"{hit.file_path}:{hit.function_name}"
    if hit.line_end and hit.line_end != hit.line_start:
        return f"{hit.file_path}:{hit.function_name}:L{hit.line_start}-L{hit.line_end}"
    return f"{hit.file_path}:{hit.function_name}:L{hit.line_start}"


def parse_html_coverage(html_content: str) -> CoverageReport:
    """Best-effort extraction from HTML coverage reports.

    Supports common patterns from: pytest-cov HTML, Istanbul/nyc, JaCoCo HTML.
    """
    modules: list[ModuleCoverage] = []

    row_pattern = re.compile(
        r'<tr[^>]*>.*?'
        r'(?:href=["\']([^"\']+)["\'].*?)?'
        r'(\d+(?:\.\d+)?)\s*%'
        r'.*?</tr>',
        re.DOTALL,
    )

    for match in row_pattern.finditer(html_content):
        path = match.group(1) or "unknown"
        pct = float(match.group(2))

        path = re.sub(r'\.html?$', '', path)

        modules.append(ModuleCoverage(
            module_path=path,
            line_rate=pct / 100.0,
            branch_rate=0.0,
            function_rate=0.0,
        ))

    overall = sum(m.line_rate for m in modules) / len(modules) if modules else 0.0

    return CoverageReport(
        overall_line_rate=overall,
        overall_branch_rate=0.0,
        overall_function_rate=0.0,
        modules=modules,
        source_format="html",
        raw_metadata={"note": "HTML 解析为尽力提取，精度可能低于 XML 格式"},
    )
