"""Coverage data adapters — supports file upload (XML/HTML) and reserved
intranet API integration.

Supported XML formats:
  - Cobertura (pytest-cov, Istanbul/nyc, gcc/gcovr)
  - JaCoCo

HTML parsing: best-effort extraction from common coverage report generators.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Protocol
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)


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
