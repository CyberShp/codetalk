"""Unit tests for app/adapters/coverage.py — covers adapter stubs and parse paths."""

import zipfile
from io import BytesIO

import pytest

from app.adapters.coverage import (
    IntranetCoverageAdapter,
    parse_internal_function_hits,
    parse_internal_function_hits_xlsx,
    parse_html_coverage,
    parse_jacoco_xml,
)

pytestmark = [pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# IntranetCoverageAdapter — lines 80-81, 84
# ---------------------------------------------------------------------------


class TestIntranetCoverageAdapter:
    def test_init_stores_url_and_key(self):
        """Lines 80-81: __init__ sets base_url and api_key."""
        adapter = IntranetCoverageAdapter(base_url="http://intranet.local", api_key="secret")
        assert adapter.base_url == "http://intranet.local"
        assert adapter.api_key == "secret"

    def test_init_defaults(self):
        adapter = IntranetCoverageAdapter()
        assert adapter.base_url == ""
        assert adapter.api_key == ""

    async def test_fetch_report_raises_not_implemented(self):
        """Line 84: fetch_report raises NotImplementedError."""
        adapter = IntranetCoverageAdapter()
        with pytest.raises(NotImplementedError):
            await adapter.fetch_report()


# ---------------------------------------------------------------------------
# parse_html_coverage — lines 297-302 (regex match path)
# ---------------------------------------------------------------------------


class TestParseHtmlCoverage:
    def test_extracts_modules_from_html_with_percentages(self):
        """Lines 297-302: regex finds percentage rows in HTML."""
        html = """<html><body><table>
        <tr><td><a href="app/service.html">app/service</a></td><td>85.0%</td></tr>
        <tr><td><a href="app/utils.html">app/utils</a></td><td>62.5%</td></tr>
        </table></body></html>"""
        report = parse_html_coverage(html)
        assert report.source_format == "html"
        assert len(report.modules) >= 1
        rates = [m.line_rate for m in report.modules]
        assert any(abs(r - 0.85) < 0.01 for r in rates)

    def test_path_without_href_uses_unknown(self):
        """Line 297: path is None → fallback to 'unknown'."""
        html = "<table><tr><td>75%</td></tr></table>"
        report = parse_html_coverage(html)
        assert any(m.module_path == "unknown" for m in report.modules)

    def test_html_extension_stripped_from_path(self):
        """Line 300: .html suffix is stripped from path."""
        html = '<table><tr><td><a href="module.html">x</a></td><td>50%</td></tr></table>'
        report = parse_html_coverage(html)
        assert any(m.module_path == "module" for m in report.modules)

    def test_empty_html_returns_zero_overall(self):
        report = parse_html_coverage("<html><body>no percentages here</body></html>")
        assert report.overall_line_rate == 0.0
        assert report.modules == []


# ---------------------------------------------------------------------------
# parse_jacoco_xml — line 214 (_counter fallback)
# ---------------------------------------------------------------------------


class TestJacocoCounterFallback:
    def test_missing_branch_counter_returns_zero(self):
        """Line 214: _counter returns (0, 0) when type not found in class element."""
        xml_no_branch = """<?xml version="1.0" encoding="UTF-8"?>
<report name="JaCoCo">
  <package name="com/example">
    <class name="com/example/Main" sourcefilename="Main.java">
      <counter type="LINE" missed="2" covered="8"/>
    </class>
    <counter type="LINE" missed="2" covered="8"/>
  </package>
  <counter type="LINE" missed="2" covered="8"/>
</report>"""
        report = parse_jacoco_xml(xml_no_branch)
        assert report.source_format == "jacoco"
        assert len(report.modules) == 1
        mod = report.modules[0]
        assert len(mod.files) == 1
        assert mod.files[0].branch_rate == 1.0


class TestParseInternalFunctionHits:
    def test_parses_function_hit_table_and_uncovered_functions(self):
        csv_text = """function_name,code_location,triggered,hit_count
init_session,src/session.c:12-30,yes,4
recover_session,src/session.c:42-66,no,0
"""

        report = parse_internal_function_hits(csv_text)

        assert report.source_format == "internal_function_hits"
        assert report.overall_function_rate == 0.5
        assert len(report.modules) == 1
        module = report.modules[0]
        assert module.module_path == "src"
        assert module.function_rate == 0.5
        assert module.uncovered_functions == ["src/session.c:recover_session:L42-L66"]
        assert len(module.function_hits) == 2
        uncovered = module.function_hits[1]
        assert uncovered.function_name == "recover_session"
        assert uncovered.file_path == "src/session.c"
        assert uncovered.line_start == 42
        assert uncovered.line_end == 66
        assert uncovered.triggered is False
        assert uncovered.hit_count == 0

    def test_parses_headerless_four_column_table(self):
        table = """init|src/a.c:1|1|9
cleanup|src/a.c:9|0|0
"""

        report = parse_internal_function_hits(table)

        assert report.source_format == "internal_function_hits"
        assert report.overall_function_rate == 0.5
        assert report.modules[0].function_hits[0].function_name == "init"
        assert report.modules[0].function_hits[1].line_start == 9

    def test_parses_utf8_bom_header(self):
        csv_text = "\ufefffunction_name,code_location,triggered,hit_count\n" \
            "cleanup_temp,src/session.c:42,false,0\n"

        report = parse_internal_function_hits(csv_text)

        assert report.source_format == "internal_function_hits"
        assert report.overall_function_rate == 0.0
        assert report.modules[0].function_hits[0].function_name == "cleanup_temp"

    def test_parses_intranet_six_column_chinese_table(self):
        csv_text = (
            "特性名称,模块名称,代码路径,函数名称,是否覆盖,覆盖次数\n"
            "网卡初始化,net_mgmt,net_mgmt/dpdknetmgmt/dpdkxxx,dpdknet_mgmt_init,否,0\n"
            "网卡初始化,net_mgmt,net_mgmt/dpdknetmgmt/dpdkxxx,dpdknet_probe,是,3\n"
        )

        report = parse_internal_function_hits(csv_text)

        assert report.source_format == "internal_function_hits"
        assert report.modules[0].module_path == "net_mgmt"
        hits = report.modules[0].function_hits
        assert hits[0].feature_name == "网卡初始化"
        assert hits[0].module_name == "net_mgmt"
        assert hits[0].file_path == "net_mgmt/dpdknetmgmt/dpdkxxx"
        assert hits[0].function_name == "dpdknet_mgmt_init"
        assert hits[0].triggered is False
        assert hits[1].hit_count == 3

    def test_parses_xlsx_intranet_six_column_table(self):
        workbook = _minimal_xlsx(
            [
                ["特性名称", "模块名称", "代码路径", "函数名称", "是否覆盖", "覆盖次数"],
                ["异常恢复", "net_mgmt", "net_mgmt/dpdknetmgmt/dpdkxxx", "dpdknet_recover", "否", "0"],
                ["异常恢复", "net_mgmt", "net_mgmt/dpdknetmgmt/dpdkxxx", "dpdknet_start", "是", "5"],
            ]
        )

        report = parse_internal_function_hits_xlsx(workbook)

        assert report.source_format == "internal_function_hits"
        assert report.overall_function_rate == 0.5
        assert report.modules[0].module_path == "net_mgmt"
        assert report.modules[0].uncovered_functions == [
            "net_mgmt/dpdknetmgmt/dpdkxxx:dpdknet_recover"
        ]


def _minimal_xlsx(rows: list[list[str]]) -> bytes:
    def col_name(index: int) -> str:
        name = ""
        while index:
            index, rem = divmod(index - 1, 26)
            name = chr(65 + rem) + name
        return name

    sheet_rows = []
    for row_idx, row in enumerate(rows, start=1):
        cells = []
        for col_idx, value in enumerate(row, start=1):
            ref = f"{col_name(col_idx)}{row_idx}"
            cells.append(
                f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'
            )
        sheet_rows.append(f'<row r="{row_idx}">{"".join(cells)}</row>')

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData>'
        "</worksheet>"
    )
    out = BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return out.getvalue()
