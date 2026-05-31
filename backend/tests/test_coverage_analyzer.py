"""Unit tests for app/services/coverage_analyzer.py.

Covers _analyze_module (lines 74-99), the unsupported-extension skip branch
in parse_and_store (lines 133-134), and the LLM analysis loop in run_analysis
(lines 238-263).
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.coverage import FunctionHit, ModuleCoverage
from app.services.coverage_analyzer import (
    CoverageAnalyzer,
    _analyze_module,
    _build_black_box_function_recommendations,
)

pytestmark = [pytest.mark.asyncio]

_MINIMAL_LOW_COVERAGE_XML = """<?xml version="1.0" ?>
<coverage line-rate="0.5" branch-rate="0.4" version="1.0">
  <packages>
    <package name="app" line-rate="0.5" branch-rate="0.4" complexity="0">
      <classes>
        <class name="x.py" filename="app/x.py" line-rate="0.5" branch-rate="0.4">
          <lines>
            <line number="1" hits="0"/>
            <line number="2" hits="1"/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>"""


class TestAnalyzeModule:
    async def test_calls_llm_and_returns_dict(self):
        """Lines 74-99: _analyze_module builds prompt, calls llm.complete, returns result."""
        module = ModuleCoverage(
            module_path="app/service",
            line_rate=0.5,
            branch_rate=0.4,
            function_rate=0.6,
            uncovered_functions=["do_thing"],
            uncovered_lines=["42", "43"],
            uncovered_branches=["branch1"],
        )
        mock_response = MagicMock()
        mock_response.text = "# AI Analysis\n\n测试建议内容"
        mock_llm = AsyncMock()
        mock_llm.complete.return_value = mock_response

        result = await _analyze_module(mock_llm, module)

        assert result["module_path"] == "app/service"
        assert result["line_rate"] == 0.5
        assert result["branch_rate"] == 0.4
        assert result["function_rate"] == 0.6
        assert "analysis" in result
        assert "测试建议" in result["analysis"]
        assert result["uncovered_function_count"] == 1
        assert result["uncovered_branch_count"] == 1
        mock_llm.complete.assert_called_once()

    async def test_prompt_includes_module_info(self):
        """Lines 82-95: the formatted prompt contains module_path and coverage rates."""
        module = ModuleCoverage(
            module_path="com/example/Service",
            line_rate=0.3,
            branch_rate=0.2,
            function_rate=0.4,
        )
        captured_prompt = []

        async def capture_complete(prompt, **kwargs):
            captured_prompt.append(prompt)
            resp = MagicMock()
            resp.text = "分析完成"
            return resp

        mock_llm = AsyncMock()
        mock_llm.complete.side_effect = capture_complete

        await _analyze_module(mock_llm, module)

        assert captured_prompt
        assert "com/example/Service" in captured_prompt[0]

    async def test_with_file_list_populates_file_details(self):
        """Line 76: file_details_lines is populated when module.files is non-empty."""
        from app.adapters.coverage import FileCoverage

        module = ModuleCoverage(
            module_path="app/svc",
            line_rate=0.6,
            branch_rate=0.5,
            function_rate=0.7,
            files=[
                FileCoverage(filename="svc.py", line_rate=0.6, branch_rate=0.5),
            ],
        )
        captured_prompt = []

        async def capture_complete(prompt, **kwargs):
            captured_prompt.append(prompt)
            resp = MagicMock()
            resp.text = "ok"
            return resp

        mock_llm = AsyncMock()
        mock_llm.complete.side_effect = capture_complete

        await _analyze_module(mock_llm, module)

        assert captured_prompt
        assert "svc.py" in captured_prompt[0]


class TestParseAndStore:
    async def test_skips_unsupported_file_and_parses_xml(self, sqlite_db):
        """Lines 133-134: parse_and_store logs a warning and skips files with
        unsupported extensions, then continues to process supported files."""
        analyzer = CoverageAnalyzer()
        analysis_id = str(uuid.uuid4())

        report = await analyzer.parse_and_store(
            analysis_id,
            [
                ("notes.txt", "plain text — unsupported, should be skipped"),
                ("coverage.xml", _MINIMAL_LOW_COVERAGE_XML),
            ],
            name="skip-test",
        )

        assert report.source_format == "cobertura"
        assert len(report.modules) == 1

    async def test_malformed_xml_raises_value_error(self, sqlite_db):
        """Lines 128-129: malformed XML triggers the except branch and raises ValueError."""
        analyzer = CoverageAnalyzer()
        with pytest.raises(ValueError, match="XML 格式无效"):
            await analyzer.parse_and_store(
                str(uuid.uuid4()),
                [("broken.xml", "<?xml version='1.0'?><unclosed>")],
            )

    async def test_html_file_parsed_correctly(self, sqlite_db):
        """Line 131: HTML files go through the parse_html_coverage branch."""
        html = """<html><body><table>
        <tr><td><a href="app/s.html">app/s</a></td><td>80.0%</td></tr>
        </table></body></html>"""
        analyzer = CoverageAnalyzer()
        analysis_id = str(uuid.uuid4())
        report = await analyzer.parse_and_store(
            analysis_id,
            [("report.html", html)],
            name="html-test",
        )
        assert report.source_format == "html"

    async def test_all_files_unsupported_raises(self, sqlite_db):
        """Line 140: all files skipped → no modules → ValueError."""
        analyzer = CoverageAnalyzer()
        with pytest.raises(ValueError, match="未能从上传文件中解析到任何覆盖率数据"):
            await analyzer.parse_and_store(
                str(uuid.uuid4()),
                [("data.csv", "a,b,c")],
            )


class TestRunAnalysisWithMockedLLM:
    async def test_calls_llm_and_stores_results(self, sqlite_db):
        """Lines 238-263: run_analysis loops over low-coverage modules,
        calls _analyze_module, and stores results in the DB."""
        analyzer = CoverageAnalyzer()
        analysis_id = str(uuid.uuid4())

        await analyzer.parse_and_store(
            analysis_id,
            [("cov.xml", _MINIMAL_LOW_COVERAGE_XML)],
            name="llm-analysis-test",
        )

        mock_response = MagicMock()
        mock_response.text = "AI 分析结果"
        mock_llm = AsyncMock()
        mock_llm.complete.return_value = mock_response

        with patch(
            "app.services.coverage_analyzer.create_llm_client_from_active",
            AsyncMock(return_value=mock_llm),
        ):
            results = await analyzer.run_analysis(analysis_id)

        assert isinstance(results, list)
        assert len(results) > 0
        assert results[0]["module_path"] == "app"
        assert "analysis" in results[0]

    async def test_no_llm_configured_returns_empty(self, sqlite_db):
        """Lines 227-236: when create_llm_client_from_active raises ValueError (no LLM
        configured), run_analysis updates status to 'parsed' and returns []."""
        analyzer = CoverageAnalyzer()
        analysis_id = str(uuid.uuid4())

        await analyzer.parse_and_store(
            analysis_id,
            [("cov.xml", _MINIMAL_LOW_COVERAGE_XML)],
            name="no-llm-test",
        )

        with patch(
            "app.services.coverage_analyzer.create_llm_client_from_active",
            AsyncMock(side_effect=ValueError("no LLM configured")),
        ):
            results = await analyzer.run_analysis(analysis_id)

        assert results == []

    async def test_exception_in_module_analysis_returns_error_entry(self, sqlite_db):
        """Lines 244-249: if _analyze_module raises, the loop catches it and
        appends an error dict rather than propagating the exception."""
        analyzer = CoverageAnalyzer()
        analysis_id = str(uuid.uuid4())

        await analyzer.parse_and_store(
            analysis_id,
            [("cov.xml", _MINIMAL_LOW_COVERAGE_XML)],
            name="exception-test",
        )

        mock_llm = AsyncMock()
        mock_llm.complete.side_effect = RuntimeError("LLM timeout")

        with patch(
            "app.services.coverage_analyzer.create_llm_client_from_active",
            AsyncMock(return_value=mock_llm),
        ):
            results = await analyzer.run_analysis(analysis_id)

        assert len(results) > 0
        assert "error" in results[0]


class TestInternalFunctionHitRecommendations:
    async def test_run_analysis_generates_black_box_recommendations_without_llm(self, sqlite_db):
        analyzer = CoverageAnalyzer()
        analysis_id = str(uuid.uuid4())
        csv_text = """function_name,code_location,triggered,hit_count
happy_path,src/service.c:10-20,true,8
error_recovery,src/service.c:40-55,false,0
"""

        await analyzer.parse_and_store(
            analysis_id,
            [("internal.csv", csv_text)],
            name="internal-hit-test",
            workspace_id="ws-1",
            repo_path="/repo",
        )

        results = await analyzer.run_analysis(analysis_id)

        assert len(results) == 1
        rec = results[0]
        assert rec["function_name"] == "error_recovery"
        assert rec["file_path"] == "src/service.c"
        assert rec["line_start"] == 40
        assert rec["risk_level"] in ("high", "medium", "low")
        assert rec["category"] == "black_box_function_gap"
        assert "scenario" in rec
        assert "expected_behavior" in rec
        assert rec["evidence"]["coverage"]["hit_count"] == 0

        import aiosqlite
        from app.config import settings

        async with aiosqlite.connect(settings.sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            row = (
                await db.execute_fetchall(
                    "SELECT status, analysis_results_json, workspace_id, repo_path "
                    "FROM coverage_analyses WHERE id = ?",
                    (analysis_id,),
                )
            )[0]

        assert row["status"] == "analyzed"
        assert row["workspace_id"] == "ws-1"
        assert row["repo_path"] == "/repo"
        assert "error_recovery" in row["analysis_results_json"]

    async def test_workspace_scope_timeout_does_not_block_recommendations(
        self, sqlite_db, tmp_path
    ):
        analyzer = CoverageAnalyzer()
        analysis_id = str(uuid.uuid4())
        csv_text = """function_name,code_location,triggered,hit_count
recover_session,src/service.c:40-55,false,0
"""

        await analyzer.parse_and_store(
            analysis_id,
            [("internal.csv", csv_text)],
            name="scope-timeout-test",
            workspace_id="ws-1",
            repo_path=str(tmp_path),
        )

        async def slow_resolve(self, *args, **kwargs):
            await asyncio.sleep(0.2)
            raise AssertionError("scope resolver should have timed out")

        with (
            patch(
                "app.services.coverage_analyzer.WORKSPACE_SCOPE_ENRICHMENT_TIMEOUT_SECONDS",
                0.01,
            ),
            patch(
                "app.services.workspace_scope_resolver.WorkspaceScopeResolver.resolve",
                new=slow_resolve,
            ),
            patch(
                "app.services.coverage_analyzer._resolve_cgc_context_for_hits",
                AsyncMock(return_value={}),
            ),
        ):
            results = await analyzer.run_analysis(analysis_id)

        assert len(results) == 1
        assert results[0]["function_name"] == "recover_session"
        assert results[0]["evidence"]["gitnexus_scope"] == {}

    async def test_recommendations_include_gitnexus_and_cgc_evidence(self):
        hit = FunctionHit(
            function_name="recover_session",
            file_path="src/service.c",
            line_start=40,
            line_end=55,
            triggered=False,
            hit_count=0,
            raw_location="src/service.c:40-55",
        )
        module = ModuleCoverage(
            module_path="src",
            line_rate=0.0,
            branch_rate=0.0,
            function_rate=0.0,
            function_hits=[hit],
        )
        key = "src/service.c:recover_session:40"

        with (
            patch(
                "app.services.coverage_analyzer._resolve_workspace_scope_for_hits",
                AsyncMock(return_value={
                    key: {
                        "gitnexus_available": True,
                        "candidate_symbols": [{"name": "recover_session"}],
                        "candidate_files": [{"path": "src/service.c"}],
                        "related_communities": ["session"],
                        "warnings": [],
                    }
                }),
            ),
            patch(
                "app.services.coverage_analyzer._resolve_cgc_context_for_hits",
                AsyncMock(return_value={
                    key: {
                        "available": True,
                        "callers": [{"name": "handle_session", "location": "src/api.c"}],
                        "callees": [{"name": "reset_state", "location": "src/state.c"}],
                    }
                }),
            ),
        ):
            results = await _build_black_box_function_recommendations(
                [module],
                workspace_id="ws-1",
                repo_path="/repo",
            )

        assert len(results) == 1
        rec = results[0]
        assert rec["confidence"] == "high"
        assert rec["evidence"]["gitnexus_scope"]["gitnexus_available"] is True
        assert rec["evidence"]["cgc"]["callers"][0]["name"] == "handle_session"
