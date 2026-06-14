"""Unit tests for app/services/coverage_analyzer.py.

Covers _analyze_module (lines 74-99), the unsupported-extension skip branch
in parse_and_store (lines 133-134), and the LLM analysis loop in run_analysis
(lines 238-263).
"""

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from app.adapters.coverage import FunctionHit, ModuleCoverage
from app.llm.base import LLMResponse
from app.services.coverage_analyzer import (
    CoverageAnalyzer,
    _analyze_module,
    _build_black_box_function_recommendations,
    _black_box_scenario_has_white_box_leak,
    _lint_test_case_drafts,
    _normalize_ai_scenario,
    _scenario_is_executable_black_box,
    _scenario_rejection_reason,
    build_coverage_test_context,
    build_coverage_test_design,
)

pytestmark = [pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _disable_real_external_agents_by_default(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "external_agents_enabled", False)


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
    @staticmethod
    def _make_repo(tmp_path):
        src = tmp_path / "src"
        src.mkdir(exist_ok=True)
        (src / "api.c").write_text(
            "int api_handle_request(request_t *req) {\n"
            "    int rc = do_work(req);\n"
            "    if (rc < 0) {\n"
            "        recover_session(req->session);\n"
            "        return -1;\n"
            "    }\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "session.c").write_text(
            "void recover_session(session_t *s) {\n"
            "    if (s == NULL) {\n"
            "        return;\n"
            "    }\n"
            "    cleanup(s);\n"
            "}\n",
            encoding="utf-8",
        )
        return src

    @staticmethod
    def _modules(csv_text):
        from app.adapters.coverage import parse_internal_function_hits
        return parse_internal_function_hits(csv_text).modules

    async def test_internal_function_hits_use_weighted_aggregate(self, sqlite_db):
        analyzer = CoverageAnalyzer()
        analysis_id = str(uuid.uuid4())
        csv_text = """feature,module,code_location,function,triggered,hit_count
app,app/main,app/main.c:1-5,main,true,1
lib,lib/core,lib/core.c:1-5,a,true,1
lib,lib/core,lib/core.c:6-10,b,false,0
lib,lib/core,lib/core.c:11-15,c,false,0
"""

        report = await analyzer.parse_and_store(
            analysis_id,
            [("internal.csv", csv_text)],
            name="weighted-hit-test",
        )

        assert report.overall_function_rate == pytest.approx(0.5)
        assert report.overall_line_rate == pytest.approx(0.5)
        assert report.overall_branch_rate == pytest.approx(0.0)

    async def test_function_hit_csv_accepts_split_file_and_line_columns(self):
        from app.adapters.coverage import parse_internal_function_hits

        csv_text = """function_name,file_path,line_start,line_end,triggered,hit_count
happy_path,src/service.c,10,20,true,8
error_recovery,src/service.c,40,55,false,0
"""
        report = parse_internal_function_hits(csv_text)
        rec = [
            hit
            for module in report.modules
            for hit in module.function_hits
            if hit.function_name == "error_recovery"
        ][0]

        assert rec.file_path == "src/service.c"
        assert rec.line_start == 40
        assert rec.line_end == 55
        assert rec.raw["line_start"] == "40"
        assert rec.raw["line_end"] == "55"

    async def test_function_hit_csv_keeps_line_record_without_function_name(self):
        from app.adapters.coverage import parse_internal_function_hits

        csv_text = """function_name,file_path,line_start,line_end,triggered,hit_count
,src/routes.py,8,8,false,0
"""
        report = parse_internal_function_hits(csv_text)
        rec = report.modules[0].function_hits[0]

        assert rec.function_name == ""
        assert rec.file_path == "src/routes.py"
        assert rec.line_start == 8
        assert rec.hit_count == 0

    async def test_function_hit_csv_accepts_compiler_style_line_column_location(self):
        from app.adapters.coverage import parse_internal_function_hits

        csv_text = """function_name,code_location,triggered,hit_count
happy_path,src/service.c:10:3,true,8
error_recovery,src/service.c:40:5,false,0
"""
        report = parse_internal_function_hits(csv_text)
        rec = [
            hit
            for module in report.modules
            for hit in module.function_hits
            if hit.function_name == "error_recovery"
        ][0]

        assert rec.file_path == "src/service.c"
        assert rec.line_start == 40
        assert rec.line_end == 40

    async def test_function_hit_csv_accepts_link_and_text_line_locations(self):
        from app.adapters.coverage import parse_internal_function_hits

        csv_text = """function_name,code_location,triggered,hit_count
link_range,src/service.c#L10-L20,false,0
text_line,src/service.c line 40,false,0
at_line,src/service.c@55,false,0
"""
        report = parse_internal_function_hits(csv_text)
        by_name = {
            hit.function_name: hit
            for module in report.modules
            for hit in module.function_hits
        }

        assert by_name["link_range"].file_path == "src/service.c"
        assert by_name["link_range"].line_start == 10
        assert by_name["link_range"].line_end == 20
        assert by_name["text_line"].file_path == "src/service.c"
        assert by_name["text_line"].line_start == 40
        assert by_name["text_line"].line_end == 40
        assert by_name["at_line"].file_path == "src/service.c"
        assert by_name["at_line"].line_start == 55
        assert by_name["at_line"].line_end == 55

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

    async def test_run_analysis_preserves_split_file_and_line_columns(self, sqlite_db):
        analyzer = CoverageAnalyzer()
        analysis_id = str(uuid.uuid4())
        csv_text = """function_name,file_path,line_start,line_end,triggered,hit_count
happy_path,src/service.c,10,20,true,8
error_recovery,src/service.c,40,55,false,0
"""

        await analyzer.parse_and_store(
            analysis_id,
            [("internal.csv", csv_text)],
            name="split-line-hit-test",
            workspace_id="ws-1",
            repo_path="/repo",
        )

        results = await analyzer.run_analysis(analysis_id)

        assert len(results) == 1
        rec = results[0]
        assert rec["function_name"] == "error_recovery"
        assert rec["file_path"] == "src/service.c"
        assert rec["line_start"] == 40
        assert rec["line_end"] == 55
        assert rec["evidence"]["coverage"]["hit_count"] == 0

    async def test_context_loads_completed_reports_and_active_materials(
        self, sqlite_db, tmp_path
    ):
        repo = self._make_repo(tmp_path)
        material = tmp_path / "design.md"
        material.write_text("登录流程材料：外部请求进入后会检查状态并返回错误码。", encoding="utf-8")
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path) VALUES (?, ?, ?)",
                ("ws-ctx", "ctx", str(tmp_path)),
            )
            await db.execute(
                """INSERT INTO workspace_reports
                   (id, workspace_id, report_type, title, content, status, task_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    "r1",
                    "ws-ctx",
                    "business_flow",
                    "关键业务流程分析",
                    "登录成功路径：外部连接建立后进入已登录状态。异常路径：认证失败返回错误码并记录日志。",
                    "completed",
                    "task-ctx",
                ),
            )
            await db.execute(
                """INSERT INTO workspace_materials
                   (id, workspace_id, filename, content_type, file_path, is_active)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("m1", "ws-ctx", "design.md", "design", str(material), True),
            )
            await db.commit()

        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "rec,session,src/session.c:1-6,recover_session,false,0\n"
        )
        context = await build_coverage_test_context(
            modules,
            workspace_id="ws-ctx",
            repo_path=str(tmp_path),
            deterministic_gaps=[],
        )

        assert context["evidence_source_counts"]["report"] == 1
        assert context["evidence_source_counts"]["material"] == 1
        assert context["evidence_source_counts"]["coverage"] >= 1
        assert "登录成功路径" in json.dumps(context["reports"], ensure_ascii=False)
        assert "登录流程材料" in json.dumps(context["materials"], ensure_ascii=False)
        assert context["repo_path"] == str(tmp_path)
        assert repo.exists()

    async def test_ai_design_uses_structured_scenarios_and_lints_black_box(
        self, sqlite_db, tmp_path
    ):
        self._make_repo(tmp_path)
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path) VALUES (?, ?, ?)",
                ("ws-ai", "ai", str(tmp_path)),
            )
            await db.execute(
                """INSERT INTO workspace_reports
                   (id, workspace_id, report_type, title, content, status, task_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    "r-ai",
                    "ws-ai",
                    "test_design",
                    "测试视角代码理解",
                    "测试从外部 API 发起请求；正常路径返回成功；异常路径返回错误码并产生日志。",
                    "completed",
                    "task-ai",
                ),
            )
            await db.commit()

        class FakeLLM:
            async def complete(self, messages, max_tokens=4096, temperature=0.1):
                payload = {
                    "scenarios": [
                        {
                            "scenario_id": "S1",
                            "priority": "high",
                            "case_type": "black_box_ready",
                            "flow_purpose": "验证外部请求失败后能进入受控恢复流程。",
                            "external_trigger": "通过公开 API 发送会触发失败恢复的请求。",
                            "input_construction": "准备合法请求和一个会导致依赖返回失败的边界请求。",
                            "normal_path": "合法请求返回成功并保持会话状态稳定。",
                            "error_path": "失败请求返回错误码，连接状态不应残留为处理中。",
                            "key_call_chain": ["api_handle_request", "recover_session"],
                            "expected_result": "返回受控错误，资源计数回到基线。",
                            "observable_signals": ["返回码", "日志关键字", "资源计数"],
                            "gray_box_aid": "打开 trace 日志辅助确认恢复动作完成，不作为黑盒步骤。",
                            "sfmea": {
                                "failure_mode": "恢复流程未触发",
                                "trigger_condition": "依赖返回失败",
                                "propagation_effect": "会话状态残留",
                                "observable_effect": "错误码、日志、资源计数异常",
                                "recommended_test": "构造依赖失败请求并观察外部状态",
                            },
                            "evidence_refs": ["report:r-ai", "coverage:recover_session"],
                            "related_gaps": ["recover_session"],
                            "confidence": "high",
                            "verification_gaps": [],
                        },
                        {
                            "scenario_id": "S2",
                            "priority": "high",
                            "case_type": "black_box_ready",
                            "flow_purpose": "bad",
                            "external_trigger": "调用 recover_session()",
                            "input_construction": "修改 src/session.c 的内部变量",
                            "normal_path": "bad",
                            "error_path": "bad",
                            "key_call_chain": ["recover_session"],
                            "expected_result": "bad",
                            "observable_signals": ["logs"],
                            "gray_box_aid": "",
                            "sfmea": {
                                "failure_mode": "bad",
                                "trigger_condition": "bad",
                                "propagation_effect": "bad",
                                "observable_effect": "bad",
                                "recommended_test": "bad",
                            },
                            "evidence_refs": [],
                            "related_gaps": ["recover_session"],
                            "confidence": "low",
                            "verification_gaps": [],
                        },
                    ]
                }
                return LLMResponse(
                    content=json.dumps(payload, ensure_ascii=False),
                    model="fake-llm",
                    usage={},
                )

        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "rec,session,src/session.c:1-6,recover_session,false,0\n"
        )
        design = await build_coverage_test_design(
            modules,
            workspace_id="ws-ai",
            repo_path=str(tmp_path),
            use_ai=True,
            llm=FakeLLM(),
        )

        assert design["summary"]["ai_status"] == "available"
        assert design["summary"]["ai_scenario_count"] == 1
        assert design["summary"]["ai_rejected_scenario_count"] == 1
        scenario = design["test_scenarios"][0]
        assert scenario["flow_purpose"].startswith("验证外部请求")
        assert "recover_session()" not in scenario["external_trigger"]
        assert scenario["sfmea"]["failure_mode"] == "恢复流程未触发"
        assert design["test_scenario_validation"]["rejected"][0]["scenario_id"] == "S2"

    async def test_run_analysis_writes_context_and_design_artifacts(
        self, sqlite_db, tmp_path, monkeypatch
    ):
        from app.config import settings

        self._make_repo(tmp_path)
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path) VALUES (?, ?, ?)",
                ("ws-run", "run", str(tmp_path)),
            )
            await db.execute(
                """INSERT INTO workspace_reports
                   (id, workspace_id, report_type, title, content, status, task_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    "r-run",
                    "ws-run",
                    "business_flow",
                    "关键业务流程分析",
                    "外部 API 触发恢复流程，异常时返回错误码并记录日志。",
                    "completed",
                    "task-run",
                ),
            )
            await db.commit()

        class FakeLLM:
            async def complete(self, messages, max_tokens=4096, temperature=0.1):
                payload = {
                    "scenarios": [
                        {
                            "scenario_id": "S1",
                            "priority": "high",
                            "case_type": "black_box_ready",
                            "flow_purpose": "验证外部 API 异常恢复。",
                            "external_trigger": "通过公开 API 发起失败请求。",
                            "input_construction": "构造依赖失败输入。",
                            "normal_path": "正常请求返回成功。",
                            "error_path": "失败请求返回错误码并清理状态。",
                            "key_call_chain": ["api_handle_request", "recover_session"],
                            "expected_result": "返回错误码，资源计数回到基线。",
                            "observable_signals": ["返回码", "日志", "资源计数"],
                            "gray_box_aid": "开启 trace 日志。",
                            "sfmea": {
                                "failure_mode": "清理遗漏",
                                "trigger_condition": "依赖失败",
                                "propagation_effect": "资源泄漏",
                                "observable_effect": "资源计数不回落",
                                "recommended_test": "失败请求后检查资源基线",
                            },
                            "evidence_refs": ["report:r-run"],
                            "related_gaps": ["recover_session"],
                            "confidence": "high",
                            "verification_gaps": [],
                        }
                    ]
                }
                return LLMResponse(json.dumps(payload, ensure_ascii=False), "fake-llm", {})

        monkeypatch.setattr(
            "app.services.coverage_analyzer.create_llm_client_from_active",
            AsyncMock(return_value=FakeLLM()),
        )
        analyzer = CoverageAnalyzer()
        analysis_id = str(uuid.uuid4())
        await analyzer.parse_and_store(
            analysis_id,
            [(
                "internal.csv",
                "feature,module,code_location,function,triggered,hit_count\n"
                "rec,session,src/session.c:1-6,recover_session,false,0\n",
            )],
            name="artifact-run",
            workspace_id="ws-run",
            repo_path=str(tmp_path),
        )

        results = await analyzer.run_analysis(analysis_id)

        assert results[0]["function_name"] == "recover_session"
        output_dir = settings.outputs_path / "coverage" / analysis_id
        assert (output_dir / "coverage_test_context.json").exists()
        assert (output_dir / "coverage_test_design.json").exists()
        assert any(output_dir.glob("debug/coverage_ai_*.json"))

    async def test_ai_prompt_warns_trace_gap_is_not_final_gray_box(self, tmp_path):
        from app.services.coverage_analyzer import (
            _coverage_ai_prompt,
            build_coverage_test_context,
        )

        gap = {
            "kind": "function",
            "function_name": "recover_session",
            "file_path": "src/session.c",
            "line_start": 1,
            "line_end": 6,
            "gray_box_required": True,
            "entry_paths": [],
            "black_box_readiness": {"case_type": "gray_box_required"},
            "evidence_gaps": ["4 跳内未追踪到外部入口"],
            "source_window": {
                "available": True,
                "path": "src/session.c",
                "start": 1,
                "end": 6,
                "text": "int recover_session(void) { return 0; }",
            },
            "evidence": {},
        }

        context = await build_coverage_test_context(
            [],
            workspace_id=None,
            repo_path=str(tmp_path),
            deterministic_gaps=[gap],
        )
        context["reports"] = [{
            "report_type": "business_flow",
            "title": "业务流程",
            "excerpt": "测试人员通过公开 API 发起恢复请求，异常时观察返回码、日志和资源计数。",
        }]
        context["external_trigger_candidates"] = [{
            "surface": "api",
            "trigger": "公开 API 请求",
            "evidence": "report:业务流程",
            "confidence": "medium",
        }]
        context["entry_discovery"] = {
            "cards": [{
                "function_name": "recover_session",
                "entry_trace_status": "entry_found",
                "candidate_external_entries": [{
                    "entry_type": "api",
                    "entry_symbol": "公开 API 请求",
                    "evidence": "report:业务流程",
                    "confidence": "medium",
                }],
            }],
        }

        prompt = _coverage_ai_prompt(context)

        assert "确定性追踪没找到入口" in prompt
        assert "不是最终灰盒结论" in prompt
        assert "external_trigger_candidates" in prompt
        assert "entry_discovery" in prompt
        assert "公开 API 请求" in prompt

    async def test_ai_failure_marks_no_valid_recommendation_instead_of_templates(
        self, tmp_path
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "session.c").write_text(
            "void internal_helper(void) {\n"
            "    return;\n"
            "}\n",
            encoding="utf-8",
        )

        class EmptyLLM:
            async def complete(self, messages, max_tokens=4096, temperature=0.1):
                return LLMResponse(content='{"scenarios": []}', model="fake", usage={})

        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "rec,session,src/session.c:1-3,internal_helper,false,0\n"
        )
        design = await build_coverage_test_design(
            modules,
            workspace_id="ws-ai-empty",
            repo_path=str(tmp_path),
            use_ai=True,
            llm=EmptyLLM(),
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert design["summary"]["ai_status"] == "available"
        assert design["summary"]["ai_scenario_count"] == 0
        assert design["summary"]["ai_rejected_scenario_count"] == 1
        assert design["summary"]["recommendation_source"] == "deterministic_fallback"
        assert design["summary"]["black_box_ready_count"] == 0
        assert design["summary"]["gray_box_required_count"] == 1
        assert design["summary"]["gap_gray_box_required_count"] == 1
        assert design["test_scenarios"] == []
        assert gap["ai_generation_status"] == "available"
        assert gap["ai_recommendation_status"] == "no_valid_ai_scenarios"
        assert gap["deterministic_case_role"] == "fallback_recommendation"

    async def test_ai_rejects_all_gray_box_batch_when_external_trigger_exists(self):
        from app.services.coverage_analyzer import _generate_ai_test_scenarios

        gray_scenario = {
            "scenario_id": "S-gray",
            "priority": "high",
            "case_type": "gray_box_required",
            "flow_purpose": "验证服务请求失败后的恢复行为。",
            "external_trigger": "通过公开 API 发起请求。",
            "input_construction": "构造会触发失败响应的请求参数。",
            "normal_path": "发送有效请求后，服务返回成功并保持连接状态。",
            "error_path": "发送异常请求后，服务返回受控错误并记录日志。",
            "key_call_chain": ["recover_session"],
            "expected_result": "客户端收到受控错误，服务端保持可继续处理后续请求。",
            "observable_signals": ["返回码", "日志关键字", "连接状态"],
            "gray_box_aid": "辅助查看 trace 日志，不作为执行步骤。",
            "sfmea": {
                "failure_mode": "恢复路径未执行",
                "trigger_condition": "请求处理失败",
                "propagation_effect": "连接状态残留",
                "observable_effect": "返回码、日志和连接状态异常",
                "recommended_test": "构造失败请求并观察外部结果",
            },
            "evidence_refs": ["coverage:recover_session"],
            "related_gaps": ["recover_session"],
            "confidence": "high",
            "verification_gaps": [],
        }

        class GrayLLM:
            async def complete(self, messages, max_tokens=4096, temperature=0.1):
                return LLMResponse(
                    content=json.dumps({"scenarios": [gray_scenario, {**gray_scenario, "scenario_id": "S-gray-2"}]}),
                    model="fake",
                    usage={},
                )

        result = await _generate_ai_test_scenarios(
            GrayLLM(),
            {
                "external_trigger_candidates": [{
                    "surface": "api",
                    "trigger": "公开 API 请求",
                    "evidence": "report:业务流程",
                    "confidence": "medium",
                }]
            },
        )

        assert result["accepted"] == []
        assert any("黑盒比例不足" in item["reason"] for item in result["rejected"])

    async def test_ai_accepts_gray_box_batch_when_entry_discovery_has_no_actionable_entry(self):
        from app.services.coverage_analyzer import _generate_ai_test_scenarios

        gray_scenario = {
            "scenario_id": "S-gray-rejected-entry",
            "priority": "high",
            "case_type": "gray_box_required",
            "flow_purpose": "verify internal recovery behavior without a public entry",
            "external_trigger": "no verified public entry; use a test fixture or injection point",
            "input_construction": "construct the target error state through gray-box assistance",
            "normal_path": "set a normal state with the fixture and observe stable state",
            "error_path": "set an abnormal state with the fixture and observe recovery signals",
            "key_call_chain": ["helper_wrapper", "internal_gap"],
            "expected_result": "the abnormal state is logged and recovered without silent success",
            "observable_signals": ["log signal", "state counter", "error return"],
            "gray_box_aid": "requires injection or a test fixture; not a black-box step",
            "sfmea": {
                "failure_mode": "recovery path is not executed",
                "trigger_condition": "internal helper has no public trigger",
                "propagation_effect": "abnormal state remains",
                "observable_effect": "logs and state counters diverge",
                "recommended_test": "trigger target state with a fixture and observe recovery",
            },
            "evidence_refs": ["entry_discovery:rejected_external_entry_candidate"],
            "related_gaps": ["internal_gap"],
            "confidence": "medium",
            "verification_gaps": ["no verified public entry"],
        }

        class GrayLLM:
            async def complete(self, messages, max_tokens=4096, temperature=0.1):
                return LLMResponse(
                    content=json.dumps({"scenarios": [gray_scenario]}),
                    model="fake",
                    usage={},
                )

        result = await _generate_ai_test_scenarios(
            GrayLLM(),
            {
                "external_trigger_candidates": [],
                "entry_discovery": {
                    "cards": [{
                        "function_name": "internal_gap",
                        "source_verification_status": "rejected_external_entry_candidate",
                        "gray_box_allowed": True,
                        "candidate_external_entries": [{
                            "entry_type": "function",
                            "entry_symbol": "helper_wrapper",
                            "validation_error": "not_public_trigger_surface",
                        }],
                    }],
                },
            },
        )

        assert result["rejected"] == []
        assert "rejected_external_entry_candidate" in result["prompt"]
        assert "not actionable external entries" in result["prompt"]
        assert [item["scenario_id"] for item in result["accepted"]] == [
            "S-gray-rejected-entry"
        ]

    async def test_ai_accepts_gray_box_batch_when_entry_candidate_is_low_confidence(self):
        from app.services.coverage_analyzer import _generate_ai_test_scenarios

        gray_scenario = {
            "scenario_id": "S-gray-low-confidence-entry",
            "priority": "high",
            "case_type": "gray_box_required",
            "flow_purpose": "verify behavior when only weak entry evidence exists",
            "external_trigger": "no verified public entry; use a test fixture or injection point",
            "input_construction": "construct the target state through gray-box assistance",
            "normal_path": "set a normal state and observe stable state",
            "error_path": "set an abnormal state and observe recovery signals",
            "key_call_chain": ["internal_gap"],
            "expected_result": "the abnormal state is logged and recovered without silent success",
            "observable_signals": ["log signal", "state counter"],
            "gray_box_aid": "requires injection or a test fixture; not a black-box step",
            "sfmea": {
                "failure_mode": "recovery path is not executed",
                "trigger_condition": "low-confidence external entry evidence",
                "propagation_effect": "abnormal state remains",
                "observable_effect": "logs and state counters diverge",
                "recommended_test": "trigger target state with a fixture and observe recovery",
            },
            "evidence_refs": ["entry_discovery:low_confidence_candidate"],
            "related_gaps": ["internal_gap"],
            "confidence": "medium",
            "verification_gaps": ["no verified public entry"],
        }

        class GrayLLM:
            async def complete(self, messages, max_tokens=4096, temperature=0.1):
                return LLMResponse(
                    content=json.dumps({"scenarios": [gray_scenario]}),
                    model="fake",
                    usage={},
                )

        result = await _generate_ai_test_scenarios(
            GrayLLM(),
            {
                "external_trigger_candidates": [],
                "entry_discovery": {
                    "cards": [{
                        "function_name": "internal_gap",
                        "source_verification_status": "needs_source_verification",
                        "gray_box_allowed": True,
                        "candidate_external_entries": [{
                            "entry_type": "api",
                            "entry_symbol": "maybe_public_api",
                            "confidence": "low",
                            "source_verification": "needs_source_verification",
                        }],
                    }],
                },
            },
        )

        assert result["rejected"] == []
        assert [item["scenario_id"] for item in result["accepted"]] == [
            "S-gray-low-confidence-entry"
        ]

    async def test_ai_accepts_gray_box_batch_when_entry_candidate_needs_source_verification(self):
        from app.services.coverage_analyzer import _generate_ai_test_scenarios

        gray_scenario = {
            "scenario_id": "S-gray-unverified-entry",
            "priority": "high",
            "case_type": "gray_box_required",
            "flow_purpose": "verify behavior while an external entry candidate is not source-backed",
            "external_trigger": "no verified public entry; keep this as gray-box until source verification succeeds",
            "input_construction": "construct the target state through gray-box assistance",
            "normal_path": "set a normal state and observe stable state",
            "error_path": "set an abnormal state and observe recovery signals",
            "key_call_chain": ["maybe_public_api", "internal_gap"],
            "expected_result": "the abnormal state is logged and recovered without silent success",
            "observable_signals": ["log signal", "state counter"],
            "gray_box_aid": "requires injection or a test fixture; the API candidate is not accepted evidence",
            "sfmea": {
                "failure_mode": "recovery path is not executed",
                "trigger_condition": "unverified external entry evidence",
                "propagation_effect": "abnormal state remains",
                "observable_effect": "logs and state counters diverge",
                "recommended_test": "trigger target state with a fixture and observe recovery",
            },
            "evidence_refs": ["entry_discovery:needs_source_verification"],
            "related_gaps": ["internal_gap"],
            "confidence": "medium",
            "verification_gaps": ["external entry candidate still needs source verification"],
        }

        class GrayLLM:
            async def complete(self, messages, max_tokens=4096, temperature=0.1):
                return LLMResponse(
                    content=json.dumps({"scenarios": [gray_scenario]}),
                    model="fake",
                    usage={},
                )

        result = await _generate_ai_test_scenarios(
            GrayLLM(),
            {
                "external_trigger_candidates": [],
                "entry_discovery": {
                    "cards": [{
                        "function_name": "internal_gap",
                        "source_verification_status": "needs_source_verification",
                        "gray_box_allowed": True,
                        "candidate_external_entries": [{
                            "entry_type": "api",
                            "entry_symbol": "maybe_public_api",
                            "confidence": "medium",
                            "source_verification": "needs_source_verification",
                            "validation_error": "",
                        }],
                    }],
                },
            },
        )

        assert result["rejected"] == []
        assert [item["scenario_id"] for item in result["accepted"]] == [
            "S-gray-unverified-entry"
        ]

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


class TestCoverageTestDesign:
    """coverage-test-design-v1 entry-oriented tracing engine."""

    async def test_function_name_candidates_include_common_qualified_leaf_names(self):
        from app.services.coverage_analyzer import _function_name_candidates

        assert _function_name_candidates("InvoiceConsumer.consumeInvoice") == [
            "InvoiceConsumer.consumeInvoice",
            "consumeInvoice",
        ]
        assert _function_name_candidates("billing::InvoiceConsumer::consumeInvoice") == [
            "billing::InvoiceConsumer::consumeInvoice",
            "consumeInvoice",
        ]
        assert _function_name_candidates("(*PaymentServer).ProcessPayment") == [
            "(*PaymentServer).ProcessPayment",
            "ProcessPayment",
        ]

    @staticmethod
    def _make_repo(tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        # External API entry that reaches recover_session inside an error branch.
        (src / "api.c").write_text(
            "int api_handle_request(request_t *req) {\n"
            "    int rc = do_work(req);\n"
            "    if (rc < 0) {\n"
            "        recover_session(req->session);\n"
            "        return -1;\n"
            "    }\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "session.c").write_text(
            "void recover_session(session_t *s) {\n"
            "    if (s == NULL) {\n"
            "        return;\n"
            "    }\n"
            "    cleanup(s);\n"
            "}\n",
            encoding="utf-8",
        )
        return src

    @staticmethod
    def _modules(csv_text):
        from app.adapters.coverage import parse_internal_function_hits
        return parse_internal_function_hits(csv_text).modules

    async def test_agent_duplicate_entry_confirms_existing_path_without_duplicate_case(self):
        from app.services.coverage_analyzer import _build_black_box_cases, _merge_agent_entry_paths

        hit = FunctionHit(
            function_name="tls_recover_session",
            file_path="src/tls.c",
            line_start=1,
            triggered=False,
            hit_count=0,
        )
        existing = [{
            "entry_kind": "rpc",
            "entry_symbol": "rpc_tls_entry",
            "entry_file": "src/rpc.c",
            "entry_label": "RPC rpc_tls_entry",
            "chain": ["rpc_tls_entry", "tls_recover_session"],
            "tool": "cgc",
        }]
        agent_context = {
            "validated_entries": [{
                "provider": "claude-code",
                "turn_id": "coverage:tls_recover_session",
                "entry_kind": "rpc",
                "entry_symbol": "rpc_tls_entry",
                "entry_file": "src/rpc.c",
                "chain": ["rpc_tls_entry", "tls_recover_session"],
                "external_trigger": "RPC rpc_tls_entry",
                "reason": "agent confirmed the same public RPC reaches TLS recovery",
                "source_verification": "source_backed",
                "input_hints": ["ctx", "tenant_id", "request", "amount"],
            }]
        }

        merged = _merge_agent_entry_paths(existing, agent_context, hit)

        assert len(merged) == 1
        assert merged[0]["tool"] == "cgc"
        assert merged[0]["entry_kind"] == "rpc"
        assert merged[0]["entry_label"] == "RPC rpc_tls_entry"
        assert merged[0]["provider"] == "claude-code"
        assert merged[0]["turn_id"] == "coverage:tls_recover_session"
        assert merged[0]["confirming_providers"] == ["claude-code"]
        assert merged[0]["confirming_turn_ids"] == ["coverage:tls_recover_session"]
        assert merged[0]["external_trigger"] == "RPC rpc_tls_entry"
        assert merged[0]["input_hints"] == ["tenant_id", "amount"]
        cases = _build_black_box_cases(hit, merged, [])
        assert cases[0]["provider"] == "claude-code"
        assert cases[0]["confirming_providers"] == ["claude-code"]
        assert cases[0]["confirming_turn_ids"] == ["coverage:tls_recover_session"]
        assert "RPC rpc_tls_entry" in cases[0]["external_trigger"]
        assert "tenant_id" in cases[0]["inputs"]
        assert "amount" in cases[0]["inputs"]

    async def test_duplicate_entry_path_merge_preserves_external_trigger(self):
        from app.services.coverage_analyzer import _merge_duplicate_entry_path

        existing = {
            "entry_kind": "route",
            "entry_symbol": "process_payment",
            "entry_file": "src/routes.py",
            "input_hints": ["amount"],
            "tool": "ripgrep",
        }
        incoming = {
            "entry_kind": "route",
            "entry_symbol": "process_payment",
            "entry_file": "src/routes.py",
            "external_trigger": "POST /payments",
            "entry_label": "route POST /payments",
            "input_hints": ["tenant_id"],
            "evidence": "src/routes.py:10 app.post('/payments', process_payment)",
            "tool": "source-registration",
        }

        _merge_duplicate_entry_path(existing, incoming)

        assert existing["external_trigger"] == "POST /payments"
        assert existing["entry_label"] == "route POST /payments"
        assert existing["input_hints"] == ["amount", "tenant_id"]
        assert existing["confirming_tools"] == ["source-registration"]
        assert existing["confirming_evidence"] == [
            "src/routes.py:10 app.post('/payments', process_payment)"
        ]

    async def test_agent_entry_chain_accepts_qualified_target_symbol(self):
        from app.services.coverage_analyzer import _build_black_box_cases, _merge_agent_entry_paths

        hit = FunctionHit(
            function_name="normalize_record",
            file_path="src/service.py",
            line_start=1,
            triggered=False,
            hit_count=0,
        )
        agent_context = {
            "validated_entries": [{
                "provider": "claude-code",
                "turn_id": "coverage:normalize_record",
                "entry_kind": "api",
                "entry_symbol": "public_records_api",
                "entry_file": "src/api.py",
                "chain": ["public_records_api -> service.normalize_record() @ src/service.py:1"],
                "external_trigger": "POST /records",
                "reason": "source-backed public API reaches normalize_record",
                "source_verification": "source_backed",
                "input_hints": ["record_id", "payload"],
            }]
        }

        merged = _merge_agent_entry_paths([], agent_context, hit)

        assert len(merged) == 1
        assert merged[0]["entry_symbol"] == "public_records_api"
        assert merged[0]["external_trigger"] == "POST /records"
        cases = _build_black_box_cases(hit, merged, [])
        assert cases
        assert cases[0]["case_type"] == "black_box_ready"
        assert cases[0]["provider"] == "claude-code"
        assert "record_id" in cases[0]["inputs"]
        assert "payload" in cases[0]["inputs"]

    async def test_agent_entry_chain_rejects_qualified_self_target(self):
        from app.services.coverage_analyzer import _merge_agent_entry_paths

        hit = FunctionHit(
            function_name="normalize_record",
            file_path="src/service.py",
            line_start=1,
            triggered=False,
            hit_count=0,
        )
        agent_context = {
            "validated_entries": [
                {
                    "provider": "claude-code",
                    "entry_kind": "api",
                    "entry_symbol": "normalize_record()",
                    "entry_file": "src/service.py",
                    "chain": ["normalize_record()"],
                    "external_trigger": "POST /records",
                    "source_verification": "source_backed",
                },
                {
                    "provider": "claude-code",
                    "entry_kind": "api",
                    "entry_symbol": "service.normalize_record",
                    "entry_file": "src/service.py",
                    "chain": ["service.normalize_record"],
                    "external_trigger": "POST /records",
                    "source_verification": "source_backed",
                },
                {
                    "provider": "claude-code",
                    "entry_kind": "api",
                    "entry_symbol": "public_records_api",
                    "entry_file": "src/api.py",
                    "chain": ["public_records_api -> service.normalize_record()"],
                    "external_trigger": "POST /records",
                    "source_verification": "source_backed",
                },
            ]
        }

        merged = _merge_agent_entry_paths([], agent_context, hit)

        assert len(merged) == 1
        assert merged[0]["entry_symbol"] == "public_records_api"

    async def test_black_box_cases_filter_internal_symbols_from_input_hints(self):
        from app.services.coverage_analyzer import _build_black_box_cases

        hit = FunctionHit(
            function_name="recover_session",
            file_path="src/session.c",
            line_start=1,
            triggered=False,
            hit_count=0,
        )
        entry_paths = [{
            "entry_kind": "api",
            "entry_symbol": "api_handle_request",
            "entry_label": "public recovery API",
            "chain": ["api_handle_request", "recover_session"],
            "input_hints": [
                "recover_session",
                "recover_session()",
                "api_handle_request",
                "api_handle_request()",
                "tenant_id",
                "payload",
            ],
        }]

        cases = _build_black_box_cases(hit, entry_paths, [])
        execution_text = json.dumps(
            [
                {
                    "inputs": case.get("inputs"),
                    "steps": case.get("steps"),
                    "external_trigger": case.get("external_trigger"),
                }
                for case in cases
            ],
            ensure_ascii=False,
        )

        assert "tenant_id" in execution_text
        assert "payload" in execution_text
        assert "recover_session" not in execution_text
        assert "api_handle_request" not in execution_text

    async def test_black_box_cases_filter_internal_context_access_input_hints(self):
        from app.services.coverage_analyzer import _build_black_box_cases

        hit = FunctionHit(
            function_name="process_invoice",
            file_path="src/processor.ts",
            line_start=1,
            triggered=False,
            hit_count=0,
        )
        entry_paths = [{
            "entry_kind": "message",
            "entry_symbol": "consumeInvoice",
            "entry_label": "message invoice.created",
            "chain": ["consumeInvoice", "process_invoice"],
            "input_hints": [
                "request.body",
                "ctx.user_id",
                "this.payload",
                "self.invoice_id",
                "invoice.created",
                "billing.process_invoice",
                "amount",
            ],
        }]

        cases = _build_black_box_cases(hit, entry_paths, [])
        execution_text = json.dumps(
            [
                {
                    "inputs": case.get("inputs"),
                    "steps": case.get("steps"),
                    "external_trigger": case.get("external_trigger"),
                }
                for case in cases
            ],
            ensure_ascii=False,
        )

        assert "invoice.created" in execution_text
        assert "billing.process_invoice" in execution_text
        assert "amount" in execution_text
        assert "request.body" not in execution_text
        assert "ctx.user_id" not in execution_text
        assert "this.payload" not in execution_text
        assert "self.invoice_id" not in execution_text

    async def test_black_box_cases_filter_optional_and_bracket_context_access_hints(self):
        from app.services.coverage_analyzer import _build_black_box_cases

        hit = FunctionHit(
            function_name="process_payment",
            file_path="src/processor.ts",
            line_start=1,
            triggered=False,
            hit_count=0,
        )
        entry_paths = [{
            "entry_kind": "route",
            "entry_symbol": "paymentRoute",
            "entry_label": "POST /payments",
            "chain": ["paymentRoute", "process_payment"],
            "input_hints": [
                "request?.body.amount",
                "req['body']['currency']",
                'ctx["tenant_id"]',
                "this?.payload.card_token",
                "payment.created",
                "amount",
                "currency",
            ],
        }]

        cases = _build_black_box_cases(hit, entry_paths, [])
        execution_text = json.dumps(
            [
                {
                    "inputs": case.get("inputs"),
                    "steps": case.get("steps"),
                    "external_trigger": case.get("external_trigger"),
                }
                for case in cases
            ],
            ensure_ascii=False,
        )

        assert "payment.created" in execution_text
        assert "amount" in execution_text
        assert "currency" in execution_text
        assert "request?.body.amount" not in execution_text
        assert "req['body']['currency']" not in execution_text
        assert 'ctx["tenant_id"]' not in execution_text
        assert "this?.payload.card_token" not in execution_text

    async def test_black_box_cases_filter_primitive_type_input_hints(self):
        from app.services.coverage_analyzer import _build_black_box_cases

        hit = FunctionHit(
            function_name="process_payment",
            file_path="src/service.py",
            line_start=1,
            triggered=False,
            hit_count=0,
        )
        entry_paths = [{
            "entry_kind": "route",
            "entry_symbol": "paymentRoute",
            "entry_label": "POST /payments",
            "chain": ["paymentRoute", "process_payment"],
            "input_hints": [
                "String",
                "Boolean",
                "Path",
                "Json",
                "tenant_id",
                "amount",
                "PaymentRequest",
            ],
        }]

        cases = _build_black_box_cases(hit, entry_paths, [])
        execution_text = json.dumps(
            [
                {
                    "inputs": case.get("inputs"),
                    "steps": case.get("steps"),
                    "external_trigger": case.get("external_trigger"),
                }
                for case in cases
            ],
            ensure_ascii=False,
        )

        assert "tenant_id" in execution_text
        assert "amount" in execution_text
        assert "PaymentRequest" in execution_text
        assert "String" not in execution_text
        assert "Boolean" not in execution_text
        assert "Path" not in execution_text
        assert "Json" not in execution_text

    async def test_traces_external_entry_and_builds_black_box(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        self._make_repo(tmp_path)
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "rec,session,src/session.c:1-6,recover_session,false,0\n"
        )
        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )
        assert design["version"] == "coverage-test-design-v1"
        assert design["summary"]["workspace_bound"] is True

        fn_gaps = [g for g in design["gaps"] if g.get("kind") == "function"]
        assert len(fn_gaps) == 1
        gap = fn_gaps[0]
        assert gap["function_name"] == "recover_session"
        assert gap["source_window"]["available"] is True
        # Entry-oriented trace finds the API entry → black-box ready, not gray.
        assert gap["gray_box_required"] is False
        kinds = {e["entry_kind"] for e in gap["entry_paths"]}
        assert "api" in kinds
        assert any("api_handle_request" in (e.get("chain") or []) for e in gap["entry_paths"])
        # Trigger conditions include the caller guard and the self guard.
        conditions = {b["condition"] for b in gap["trigger_branches"]}
        assert any("rc < 0" in c for c in conditions)
        assert any("s == NULL" in c for c in conditions)
        assert gap["black_box_cases"]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["branch_fact_card"]["uncovered_location"].startswith("src/session.c")
        assert gap["external_entry_card"]["has_external_entry"] is True
        assert gap["test_case_drafts"]
        assert gap["white_box_leak_check"]["passed"] is True
        execution_text = "\n".join(
            str(value)
            for draft in gap["test_case_drafts"]
            if draft["case_type"] == "black_box_ready"
            for value in (draft["test_execution"].values())
        )
        assert "recover_session" not in execution_text
        assert "src/session.c" not in execution_text
        assert "if (" not in execution_text
        assert design["summary"]["black_box_ready_count"] == 1

    async def test_local_entry_trace_survives_gitnexus_and_agent_unavailable(
        self,
        tmp_path,
        monkeypatch,
    ):
        import app.services.coverage_analyzer as coverage_mod
        from app.config import settings
        from app.services.coverage_analyzer import build_coverage_test_design
        from app.services.external_agent_discovery import AgentDiscoveryResult

        monkeypatch.setattr(settings, "external_agents_enabled", True)
        self._make_repo(tmp_path)
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "rec,session,src/session.c:1-6,recover_session,false,0\n"
        )

        async def fake_discovery(_request, **_kwargs):
            return [
                AgentDiscoveryResult(
                    provider="claude-code",
                    status="unavailable",
                    raw_summary="command not found: ccr",
                )
            ]

        monkeypatch.setattr(coverage_mod, "run_external_agent_discovery", fake_discovery, raising=False)

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )
        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]

        assert design["summary"]["tool_status"]["gitnexus"] == "unavailable"
        assert design["summary"]["tool_status"]["external_agent"] == "unavailable"
        assert gap["tool_status"]["gitnexus"] == "unavailable"
        assert gap["tool_status"]["external_agent"] == "unavailable"
        assert gap["entry_trace_status"] == "entry_found"
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["black_box_cases"]
        assert gap["evidence"]["external_agent"]["raw_results"][0]["status"] == "unavailable"

    async def test_c_command_table_entry_is_black_box_when_graph_and_agent_unavailable(
        self,
        tmp_path,
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "ops.c").write_text(
            "struct command_entry { const char *name; int (*handler)(struct request *req); };\n"
            "void recover_session(struct session *s) {\n"
            "    if (s == 0) {\n"
            "        return;\n"
            "    }\n"
            "    cleanup_session(s);\n"
            "}\n"
            "static int handle_recover(struct request *req) {\n"
            "    if (req->reset) {\n"
            "        recover_session(req->session);\n"
            "    }\n"
            "    return 0;\n"
            "}\n"
            "static struct command_entry command_table[] = {\n"
            "    { \"recover\", handle_recover },\n"
            "};\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "rec,ops,src/ops.c:2-7,recover_session,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )
        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]

        assert gap["tool_status"]["gitnexus"] == "unavailable"
        assert gap["tool_status"]["external_agent"] in {"disabled", "unavailable"}
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"]
        entry = gap["entry_paths"][0]
        assert entry["tool"] == "source-table"
        assert entry["entry_kind"] == "cli"
        assert entry["entry_symbol"] == "handle_recover"
        assert entry["input_hints"] == ["recover"]

    async def test_c_parenthesized_command_table_entry_is_black_box_without_graph(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "ops.c").write_text(
            "struct command_entry { const char *name; int (*handler)(struct request *req); };\n"
            "void recover_session(struct session *s) {\n"
            "    if (s == 0) {\n"
            "        return;\n"
            "    }\n"
            "    cleanup_session(s);\n"
            "}\n"
            "static int handle_recover(struct request *req) {\n"
            "    if (req->reset) {\n"
            "        recover_session(req->session);\n"
            "    }\n"
            "    return 0;\n"
            "}\n"
            "static struct command_entry command_table[] = {\n"
            "    { \"recover\", (handle_recover) },\n"
            "};\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "rec,ops,src/ops.c:2-7,recover_session,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )
        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]

        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["tool"] == "source-table"
        assert entry["entry_kind"] == "cli"
        assert entry["entry_symbol"] == "handle_recover"
        assert entry["input_hints"] == ["recover"]
        assert '{ "recover", (handle_recover) }' in entry["evidence"]

    async def test_c_method_path_dispatch_table_entry_is_black_box_without_graph(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "routes.c").write_text(
            "struct route_entry { const char *method; const char *path; int (*handler)(struct request *req); };\n"
            "void recover_session(struct session *s) {\n"
            "    if (s == 0) {\n"
            "        return;\n"
            "    }\n"
            "    cleanup_session(s);\n"
            "}\n"
            "static int handle_recover(struct request *req) {\n"
            "    if (req->id == 0) {\n"
            "        return -1;\n"
            "    }\n"
            "    recover_session(req->session);\n"
            "    return 0;\n"
            "}\n"
            "static const struct route_entry routes[] = {\n"
            "    { \"POST\", \"/sessions/{id}/recover\", handle_recover },\n"
            "};\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "rec,routes,src/routes.c:2-7,recover_session,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )
        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]

        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["tool"] == "source-table"
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "handle_recover"
        assert entry["external_trigger"] == "POST /sessions/{id}/recover"
        assert entry["input_hints"] == ["id"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "POST /sessions/{id}/recover" in case_text

    async def test_c_casted_positional_command_table_entry_is_black_box_without_graph(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "ops.c").write_text(
            "struct command_entry { const char *name; int (*handler)(struct request *req); };\n"
            "typedef int (*command_handler_t)(struct request *req);\n"
            "void recover_session(struct session *s) {\n"
            "    if (s == 0) {\n"
            "        return;\n"
            "    }\n"
            "    cleanup_session(s);\n"
            "}\n"
            "static int handle_recover(struct request *req) {\n"
            "    if (req->reset) {\n"
            "        recover_session(req->session);\n"
            "    }\n"
            "    return 0;\n"
            "}\n"
            "static struct command_entry command_table[] = {\n"
            "    { \"recover\", (command_handler_t)handle_recover },\n"
            "};\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "rec,ops,src/ops.c:3-8,recover_session,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )
        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]

        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["tool"] == "source-table"
        assert entry["entry_kind"] == "cli"
        assert entry["entry_symbol"] == "handle_recover"
        assert entry["input_hints"] == ["recover"]
        assert '{ "recover", (command_handler_t)handle_recover }' in entry["evidence"]

    async def test_cpp_qualified_command_table_entry_is_black_box_without_graph(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "ops.cc").write_text(
            "struct command_entry { const char *name; int (*handler)(request *req); };\n"
            "void recover_session(session *s) {\n"
            "    if (s == nullptr) {\n"
            "        return;\n"
            "    }\n"
            "    cleanup_session(s);\n"
            "}\n"
            "namespace ops {\n"
            "int handle_recover(request *req) {\n"
            "    if (req->reset) {\n"
            "        recover_session(req->session);\n"
            "    }\n"
            "    return 0;\n"
            "}\n"
            "}\n"
            "static command_entry command_table[] = {\n"
            "    { \"recover\", ops::handle_recover },\n"
            "};\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "rec,ops,src/ops.cc:2-7,recover_session,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )
        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]

        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["tool"] == "source-table"
        assert entry["entry_kind"] == "cli"
        assert entry["entry_symbol"] == "handle_recover"
        assert entry["input_hints"] == ["recover"]
        assert '{ "recover", ops::handle_recover }' in entry["evidence"]

    async def test_cpp_static_cast_command_table_entry_is_black_box_without_graph(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "ops.cc").write_text(
            "using command_handler_t = int (*)(request *req);\n"
            "struct command_entry { const char *name; command_handler_t handler; };\n"
            "void recover_session(session *s) {\n"
            "    if (s == nullptr) {\n"
            "        return;\n"
            "    }\n"
            "    cleanup_session(s);\n"
            "}\n"
            "int handle_recover(request *req) {\n"
            "    if (req->reset) {\n"
            "        recover_session(req->session);\n"
            "    }\n"
            "    return 0;\n"
            "}\n"
            "static command_entry command_table[] = {\n"
            "    { \"recover\", static_cast<command_handler_t>(handle_recover) },\n"
            "};\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "rec,ops,src/ops.cc:3-8,recover_session,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )
        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]

        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["tool"] == "source-table"
        assert entry["entry_kind"] == "cli"
        assert entry["entry_symbol"] == "handle_recover"
        assert entry["input_hints"] == ["recover"]
        assert 'static_cast<command_handler_t>(handle_recover)' in entry["evidence"]

    async def test_c_macro_wrapped_command_table_entry_is_black_box_without_graph(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "ops.c").write_text(
            "#define COMMAND_HANDLER(fn) fn\n"
            "struct command_entry { const char *name; int (*handler)(struct request *req); };\n"
            "void recover_session(struct session *s) {\n"
            "    if (s == 0) {\n"
            "        return;\n"
            "    }\n"
            "    cleanup_session(s);\n"
            "}\n"
            "static int handle_recover(struct request *req) {\n"
            "    if (req->reset) {\n"
            "        recover_session(req->session);\n"
            "    }\n"
            "    return 0;\n"
            "}\n"
            "static struct command_entry command_table[] = {\n"
            "    { \"recover\", COMMAND_HANDLER(handle_recover) },\n"
            "};\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "rec,ops,src/ops.c:3-8,recover_session,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )
        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]

        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["tool"] == "source-table"
        assert entry["entry_kind"] == "cli"
        assert entry["entry_symbol"] == "handle_recover"
        assert entry["input_hints"] == ["recover"]
        assert 'COMMAND_HANDLER(handle_recover)' in entry["evidence"]

    async def test_c_designated_command_table_entry_is_black_box_without_graph(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "ops.c").write_text(
            "struct command_entry { const char *name; int (*handler)(struct request *req); };\n"
            "void recover_session(struct session *s) {\n"
            "    if (s == 0) {\n"
            "        return;\n"
            "    }\n"
            "    cleanup_session(s);\n"
            "}\n"
            "static int handle_recover(struct request *req) {\n"
            "    if (req->reset) {\n"
            "        recover_session(req->session);\n"
            "    }\n"
            "    return 0;\n"
            "}\n"
            "static const struct command_entry command_table[] = {\n"
            "    {\n"
            "        .name = \"recover\",\n"
            "        .handler = handle_recover,\n"
            "    },\n"
            "};\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "rec,ops,src/ops.c:2-7,recover_session,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )
        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]

        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["tool"] == "source-table"
        assert entry["entry_kind"] == "cli"
        assert entry["entry_symbol"] == "handle_recover"
        assert entry["input_hints"] == ["recover"]

    async def test_c_casted_command_table_handler_is_black_box_without_graph(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "ops.c").write_text(
            "struct command_entry { const char *name; int (*handler)(struct request *req); };\n"
            "typedef int (*command_handler_t)(struct request *req);\n"
            "void recover_session(struct session *s) {\n"
            "    if (s == 0) {\n"
            "        return;\n"
            "    }\n"
            "    cleanup_session(s);\n"
            "}\n"
            "static int handle_recover(struct request *req) {\n"
            "    if (req->reset) {\n"
            "        recover_session(req->session);\n"
            "    }\n"
            "    return 0;\n"
            "}\n"
            "static const struct command_entry command_table[] = {\n"
            "    {\n"
            "        .name = \"recover\",\n"
            "        .handler = (command_handler_t)handle_recover,\n"
            "    },\n"
            "};\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "rec,ops,src/ops.c:3-8,recover_session,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )
        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]

        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["tool"] == "source-table"
        assert entry["entry_kind"] == "cli"
        assert entry["entry_symbol"] == "handle_recover"
        assert entry["input_hints"] == ["recover"]
        assert ".handler = (command_handler_t)handle_recover" in entry["evidence"]

    async def test_internal_event_named_helper_is_not_black_box_entry_without_registration(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "service.c").write_text(
            "void recover_session(struct session *s) {\n"
            "    if (s == 0) {\n"
            "        return;\n"
            "    }\n"
            "    cleanup_session(s);\n"
            "}\n"
            "static void process_event_cache(struct session *s) {\n"
            "    recover_session(s);\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "rec,service,src/service.c:1-5,recover_session,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )
        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]

        assert gap["entry_paths"] == []
        assert gap["black_box_readiness"]["case_type"] != "black_box_ready"
        assert all(case["case_type"] != "black_box_ready" for case in gap["black_box_cases"])

    async def test_resolves_function_when_coverage_path_is_directory(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        self._make_repo(tmp_path)
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "rec,session,src,recover_session,false,0\n"
        )
        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["source_window"]["available"] is True
        assert gap["source_window"]["path"] == "src/session.c"
        assert any("api_handle_request" in (e.get("chain") or []) for e in gap["entry_paths"])

    async def test_cgc_callers_seed_entry_trace_when_rg_call_site_missing(self, tmp_path):
        from app.adapters.coverage import FunctionHit, ModuleCoverage
        from app.services.coverage_analyzer import _design_function_gap

        self._make_repo(tmp_path)
        hit = FunctionHit(
            function_name="recover_session",
            file_path="src/session.c",
            line_start=1,
            line_end=6,
            triggered=False,
            hit_count=0,
        )
        module = ModuleCoverage(
            module_path="src",
            line_rate=0.0,
            branch_rate=0.0,
            function_rate=0.0,
            function_hits=[hit],
        )
        result = _design_function_gap(
            module,
            hit,
            workspace_id="ws-1",
            repo_path=str(tmp_path),
            repo_root=tmp_path,
            rg_available=False,
            scope={},
            cgc_context={
                "available": True,
                "callers": [{"name": "api_handle_request", "location": "src/api.c"}],
            },
            trace=True,
        )

        assert result["gray_box_required"] is False
        assert result["entry_paths"][0]["tool"] == "cgc"
        assert result["entry_paths"][0]["entry_symbol"] == "api_handle_request"

    async def test_ripgrep_call_sites_skip_vendor_and_build_dirs(self, tmp_path):
        from app.services.coverage_analyzer import _ripgrep_call_sites

        vendor = tmp_path / "node_modules"
        vendor.mkdir()
        (vendor / "lib.c").write_text(
            "void wrapper(void) { recover_session(0); }\n",
            encoding="utf-8",
        )

        assert _ripgrep_call_sites(tmp_path, "recover_session") == []

    async def test_spdk_rpc_multiline_handler_becomes_black_box_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        mod = tmp_path / "module" / "bdev" / "zone_block"
        mod.mkdir(parents=True)
        (mod / "vbdev_zone_block.h").write_text(
            "int vbdev_zone_block_create(const char *bdev_name, const char *vbdev_name,\n"
            "                             uint64_t zone_capacity,\n"
            "                             uint64_t optimal_open_zones);\n",
            encoding="utf-8",
        )
        (mod / "vbdev_zone_block_rpc.c").write_text(
            "static void\n"
            "rpc_bdev_zone_block_create(struct spdk_jsonrpc_request *request,\n"
            "                           const struct spdk_json_val *params)\n"
            "{\n"
            "    struct rpc_bdev_zone_block_create_ctx req = {};\n"
            "    int rc;\n"
            "    if (decode(params, &req)) {\n"
            "        goto cleanup;\n"
            "    }\n"
            "    rc = vbdev_zone_block_create(req.base_bdev, req.name, req.zone_capacity,\n"
            "                                 req.optimal_open_zones);\n"
            "cleanup:\n"
            "    return;\n"
            "}\n"
            "SPDK_RPC_REGISTER(\"bdev_zone_block_create\", rpc_bdev_zone_block_create, SPDK_RPC_RUNTIME)\n",
            encoding="utf-8",
        )
        (mod / "vbdev_zone_block.c").write_text(
            "int\n"
            "vbdev_zone_block_create(const char *bdev_name, const char *vbdev_name, uint64_t zone_capacity,\n"
            "                        uint64_t optimal_open_zones)\n"
            "{\n"
            "    if (zone_capacity == 0) {\n"
            "        return -EINVAL;\n"
            "    }\n"
            "    if (optimal_open_zones == 0) {\n"
            "        return -EINVAL;\n"
            "    }\n"
            "    return zone_block_register(bdev_name);\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "zone,bdev_zone_block,module/bdev/zone_block/vbdev_zone_block.c:2-13,"
            "vbdev_zone_block_create,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        entry = gap["entry_paths"][0]
        assert entry["entry_symbol"] == "rpc_bdev_zone_block_create"
        assert entry["entry_label"] == "JSON-RPC bdev_zone_block_create"
        assert entry["chain"] == ["rpc_bdev_zone_block_create", "vbdev_zone_block_create"]
        assert set(entry["input_hints"]) == {
            "base_bdev",
            "name",
            "zone_capacity",
            "optimal_open_zones",
        }
        assert not any((b.get("file") or "").endswith(".h") for b in gap["trigger_branches"])
        assert not any(
            str(b.get("condition") or "").startswith("int vbdev_zone_block_create")
            for b in gap["trigger_branches"]
        )
        assert not any(
            str(b.get("condition") or "").startswith("rc = vbdev_zone_block_create")
            for b in gap["trigger_branches"]
        )
        assert not any(
            "Insert the bdev" in str(b.get("condition") or "")
            for b in gap["trigger_branches"]
        )
        case_text = "\n".join(
            str(value)
            for case in gap["black_box_cases"]
            for value in case.values()
        )
        assert "JSON-RPC bdev_zone_block_create" in case_text
        assert "zone_capacity" in case_text
        assert "optimal_open_zones" in case_text
        assert "Drive the nearest public API" not in case_text

    async def test_grpc_servicer_registration_becomes_black_box_api_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payment_service.py").write_text(
            "class PaymentService:\n"
            "    def ProcessPayment(self, request, context):\n"
            "        if not request.amount:\n"
            "            return PaymentReply(status='missing')\n"
            "        return PaymentReply(status='ok')\n\n"
            "def serve(server):\n"
            "    service = PaymentService()\n"
            "    service.ProcessPayment(HealthCheck(amount=1), None)\n"
            "    payment_pb2_grpc.add_PaymentServiceServicer_to_server(service, server)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,grpc,src/payment_service.py:2-5,ProcessPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "api"
        assert entry["entry_symbol"] == "serve"
        assert entry["tool"] == "source-registration"
        assert "add_PaymentServiceServicer_to_server" in entry["evidence"]

    async def test_go_grpc_receiver_registration_becomes_black_box_api_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payment_server.go").write_text(
            "package payments\n\n"
            "type PaymentServer struct{}\n\n"
            "func (s *PaymentServer) ProcessPayment(ctx context.Context, req *pb.PaymentRequest) (*pb.PaymentReply, error) {\n"
            "    if req.Amount == 0 {\n"
            "        return nil, status.Error(codes.InvalidArgument, \"missing amount\")\n"
            "    }\n"
            "    return &pb.PaymentReply{}, nil\n"
            "}\n\n"
            "func Register(grpcServer *grpc.Server) {\n"
            "    pb.RegisterPaymentServiceServer(grpcServer, &PaymentServer{})\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,grpc,src/payment_server.go:5-10,ProcessPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "grpc"
        assert entry["entry_symbol"] == "Register"
        assert entry["tool"] == "source-grpc-registration"
        assert "RegisterPaymentServiceServer" in entry["evidence"]
        assert "PaymentRequest" in entry["input_hints"]

    async def test_java_grpc_service_registration_becomes_black_box_api_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentServiceImpl.java").write_text(
            "public final class PaymentServiceImpl extends PaymentServiceGrpc.PaymentServiceImplBase {\n"
            "  @Override\n"
            "  public void processPayment(PaymentRequest request, StreamObserver<PaymentReply> observer) {\n"
            "    if (request.getAmount() == 0) {\n"
            "      observer.onError(new IllegalArgumentException(\"missing amount\"));\n"
            "      return;\n"
            "    }\n"
            "    observer.onNext(PaymentReply.newBuilder().build());\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "GrpcServer.java").write_text(
            "public final class GrpcServer {\n"
            "  public void start() throws Exception {\n"
            "    Server server = ServerBuilder.forPort(9090)\n"
            "      .addService(new PaymentServiceImpl())\n"
            "      .build()\n"
            "      .start();\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,grpc,src/PaymentServiceImpl.java:3-9,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "grpc"
        assert entry["entry_symbol"] == "start"
        assert entry["tool"] == "source-grpc-registration"
        assert "addService(new PaymentServiceImpl())" in entry["evidence"]
        assert "PaymentRequest" in entry["input_hints"]

    async def test_python_webhook_call_site_becomes_black_box_entry_without_agent(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "billing.py").write_text(
            "def reconcile_invoice(payload):\n"
            "    if not payload:\n"
            "        return 'missing'\n"
            "    return 'ok'\n",
            encoding="utf-8",
        )
        (src / "webhooks.py").write_text(
            "def payment_webhook(request):\n"
            "    return reconcile_invoice(request.json)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,billing,src/billing.py:1-4,reconcile_invoice,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["entry_kind"] == "webhook"
        assert gap["entry_paths"][0]["entry_symbol"] == "payment_webhook"

    async def test_file_upload_call_site_becomes_black_box_entry_without_agent(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "records.py").write_text(
            "def normalize_record(row):\n"
            "    if not row:\n"
            "        return 'missing'\n"
            "    return 'ok'\n",
            encoding="utf-8",
        )
        (src / "uploads.py").write_text(
            "def csv_upload(file_obj):\n"
            "    return normalize_record(file_obj.readline())\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "records,records,src/records.py:1-4,normalize_record,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["entry_kind"] == "file"
        assert gap["entry_paths"][0]["entry_symbol"] == "csv_upload"
        assert "CSV file" in gap["entry_paths"][0]["input_hints"]
        assert "file_obj" not in gap["entry_paths"][0]["input_hints"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "csv_upload" in case_text
        assert "CSV file" in case_text
        assert "file_obj" not in case_text

    async def test_filesystem_glob_worker_becomes_black_box_file_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "orders.py").write_text(
            "def process_order(payload):\n"
            "    if not payload:\n"
            "        return 'missing'\n"
            "    return 'processed'\n",
            encoding="utf-8",
        )
        (src / "worker.py").write_text(
            "from pathlib import Path\n"
            "from orders import process_order\n\n"
            "def run_once(inbox_dir):\n"
            "    for path in Path(inbox_dir).glob('*.json'):\n"
            "        process_order(path.read_text())\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "orders,orders,src/orders.py:1-4,process_order,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "file"
        assert entry["entry_symbol"] == "run_once"
        assert "Path(inbox_dir).glob('*.json')" in entry["evidence"]
        assert entry["input_hints"] == ["JSON file", "input directory", "inbox_dir"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "JSON file" in case_text
        assert "inbox_dir" in case_text

    async def test_filesystem_literal_read_feeds_specific_file_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "orders.py").write_text(
            "def process_order(payload):\n"
            "    if not payload:\n"
            "        return 'missing'\n"
            "    return 'processed'\n",
            encoding="utf-8",
        )
        (src / "seed_loader.py").write_text(
            "from pathlib import Path\n"
            "from orders import process_order\n\n"
            "def load_seed_orders():\n"
            "    payload = Path('fixtures/orders.json').read_text()\n"
            "    return process_order(payload)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "orders,orders,src/orders.py:1-4,process_order,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "file"
        assert entry["entry_symbol"] == "load_seed_orders"
        assert entry["input_hints"] == ["fixtures/orders.json", "JSON file"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "fixtures/orders.json" in case_text
        assert "JSON file" in case_text

    async def test_pandas_csv_loader_becomes_black_box_file_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "orders.py").write_text(
            "def process_order(payload):\n"
            "    if not payload:\n"
            "        return 'missing'\n"
            "    return 'processed'\n",
            encoding="utf-8",
        )
        (src / "loader.py").write_text(
            "import pandas as pd\n"
            "from orders import process_order\n\n"
            "def load_orders(csv_path):\n"
            "    frame = pd.read_csv(csv_path)\n"
            "    return process_order(frame.to_json())\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "orders,orders,src/orders.py:1-4,process_order,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "file"
        assert entry["entry_symbol"] == "load_orders"
        assert entry["input_hints"] == ["CSV file", "csv_path"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "CSV file" in case_text
        assert "csv_path" in case_text

    async def test_node_fs_read_file_becomes_black_box_file_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "orders.ts").write_text(
            "export function processOrder(payload: any) {\n"
            "  if (!payload) return 'missing';\n"
            "  return 'processed';\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "loader.ts").write_text(
            "import fs from 'fs';\n"
            "import { processOrder } from './orders';\n\n"
            "export function loadOrders(inputPath: string) {\n"
            "  const raw = fs.readFileSync(inputPath, 'utf8');\n"
            "  return processOrder(JSON.parse(raw));\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "orders,orders,src/orders.ts:1-4,processOrder,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "file"
        assert entry["entry_symbol"] == "loadOrders"
        assert entry["input_hints"] == ["input file", "inputPath"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "input file" in case_text
        assert "inputPath" in case_text

    async def test_java_files_read_string_becomes_black_box_file_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "OrderProcessor.java").write_text(
            "public class OrderProcessor {\n"
            "  public static String processOrder(String payload) {\n"
            "    if (payload == null || payload.isEmpty()) return \"missing\";\n"
            "    return \"processed\";\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "OrderLoader.java").write_text(
            "import java.nio.file.Files;\n"
            "import java.nio.file.Path;\n\n"
            "public class OrderLoader {\n"
            "  public String loadOrders(Path inputPath) throws Exception {\n"
            "    String raw = Files.readString(inputPath);\n"
            "    return OrderProcessor.processOrder(raw);\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "orders,orders,src/OrderProcessor.java:2-5,processOrder,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "file"
        assert entry["entry_symbol"] == "loadOrders"
        assert entry["input_hints"] == ["input file", "inputPath"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "input file" in case_text
        assert "inputPath" in case_text

    async def test_c_fopen_loader_becomes_black_box_file_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "processor.c").write_text(
            "int process_order(const char *payload) {\n"
            "    if (!payload || !payload[0]) return -1;\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "loader.c").write_text(
            "#include <stdio.h>\n\n"
            "extern int process_order(const char *payload);\n\n"
            "int load_orders(const char *input_path) {\n"
            "    FILE *fp = fopen(input_path, \"r\");\n"
            "    char buffer[256] = {0};\n"
            "    if (fp) { fread(buffer, 1, sizeof(buffer) - 1, fp); }\n"
            "    return process_order(buffer);\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "orders,orders,src/processor.c:1-4,process_order,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "file"
        assert entry["entry_symbol"] == "load_orders"
        assert entry["input_hints"] == ["input file", "input_path"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "input file" in case_text
        assert "input_path" in case_text

    async def test_go_os_read_file_becomes_black_box_file_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "processor.go").write_text(
            "package main\n\n"
            "func processOrder(payload []byte) string {\n"
            "    if len(payload) == 0 { return \"missing\" }\n"
            "    return \"processed\"\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "loader.go").write_text(
            "package main\n\n"
            "import \"os\"\n\n"
            "func loadOrders(inputPath string) string {\n"
            "    payload, _ := os.ReadFile(inputPath)\n"
            "    return processOrder(payload)\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "orders,orders,src/processor.go:3-6,processOrder,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "file"
        assert entry["entry_symbol"] == "loadOrders"
        assert entry["input_hints"] == ["input file", "inputPath"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "input file" in case_text
        assert "inputPath" in case_text

    async def test_rust_fs_read_to_string_becomes_black_box_file_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "processor.rs").write_text(
            "pub fn process_order(payload: &str) -> &'static str {\n"
            "    if payload.is_empty() { return \"missing\"; }\n"
            "    \"processed\"\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "loader.rs").write_text(
            "use std::fs;\n"
            "use crate::processor::process_order;\n\n"
            "pub fn load_orders(input_path: &str) -> &'static str {\n"
            "    let payload = fs::read_to_string(input_path).unwrap();\n"
            "    process_order(&payload)\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "orders,orders,src/processor.rs:1-4,process_order,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "file"
        assert entry["entry_symbol"] == "load_orders"
        assert entry["input_hints"] == ["input file", "input_path"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "input file" in case_text
        assert "input_path" in case_text

    async def test_route_call_site_keeps_route_entry_kind_without_agent(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "refunds.py").write_text(
            "def process_refund(payload):\n"
            "    if not payload:\n"
            "        return 'missing'\n"
            "    return 'refunded'\n",
            encoding="utf-8",
        )
        (src / "routes.py").write_text(
            "def refund_route(request):\n"
            "    return process_refund(request.json)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "refunds,refunds,src/refunds.py:1-4,process_refund,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["entry_kind"] == "route"
        assert gap["entry_paths"][0]["entry_symbol"] == "refund_route"

    async def test_route_json_request_fields_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "refunds.py").write_text(
            "def process_refund(payload):\n"
            "    if not payload:\n"
            "        return 'missing'\n"
            "    return 'refunded'\n",
            encoding="utf-8",
        )
        (src / "routes.py").write_text(
            "def refund_route(request):\n"
            "    payload = {\n"
            "        'amount': request.json['amount'],\n"
            "        'currency': request.json.get('currency'),\n"
            "    }\n"
            "    return process_refund(payload)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "refunds,refunds,src/refunds.py:1-4,process_refund,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["input_hints"] == ["amount", "currency"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text
        assert "currency" in case_text

    async def test_route_json_field_names_that_match_containers_are_not_dropped(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "refunds.py").write_text(
            "def process_refund(payload):\n"
            "    if not payload:\n"
            "        return 'missing'\n"
            "    return 'refunded'\n",
            encoding="utf-8",
        )
        (src / "routes.py").write_text(
            "def refund_route(request):\n"
            "    payload = {\n"
            "        'data': request.json['data'],\n"
            "        'params': request.json.get('params'),\n"
            "    }\n"
            "    return process_refund(payload)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "refunds,refunds,src/refunds.py:1-4,process_refund,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["input_hints"] == ["data", "params"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "data" in case_text
        assert "params" in case_text

    async def test_route_dot_request_fields_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "refunds.py").write_text(
            "def process_refund(payload):\n"
            "    if not payload:\n"
            "        return 'missing'\n"
            "    return 'refunded'\n",
            encoding="utf-8",
        )
        (src / "routes.py").write_text(
            "def refund_route(request):\n"
            "    payload = {\n"
            "        'amount': request.body.amount,\n"
            "        'user_id': request.query.user_id,\n"
            "    }\n"
            "    return process_refund(payload)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "refunds,refunds,src/refunds.py:1-4,process_refund,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["input_hints"] == ["amount", "user_id"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text
        assert "user_id" in case_text

    async def test_route_optional_chain_request_fields_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "refunds.ts").write_text(
            "export function processRefund(payload) {\n"
            "  if (!payload) {\n"
            "    return 'missing';\n"
            "  }\n"
            "  return 'refunded';\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "routes.ts").write_text(
            "export function refundRoute(req) {\n"
            "  const payload = {\n"
            "    amount: req.body?.amount,\n"
            "    currency: req.query?.['currency'],\n"
            "  };\n"
            "  return processRefund(payload);\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "refunds,refunds,src/refunds.ts:1-6,processRefund,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["input_hints"] == ["amount", "currency"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text
        assert "currency" in case_text

    async def test_route_destructured_request_fields_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "refunds.js").write_text(
            "function processRefund(payload) {\n"
            "  if (!payload) {\n"
            "    return 'missing';\n"
            "  }\n"
            "  return 'refunded';\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "routes.js").write_text(
            "function refundRoute(request) {\n"
            "  const { amount, user_id } = request.body;\n"
            "  return processRefund({ amount, user_id });\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "refunds,refunds,src/refunds.js:1-6,processRefund,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["input_hints"] == ["amount", "user_id"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text
        assert "user_id" in case_text

    async def test_route_input_hints_ignore_nested_arrow_helper_boundary(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "refunds.js").write_text(
            "function processRefund(payload) {\n"
            "  if (!payload) {\n"
            "    return 'missing';\n"
            "  }\n"
            "  return 'refunded';\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "routes.js").write_text(
            "function refundRoute(request) {\n"
            "  const normalize = (value) => value;\n"
            "  const payload = { amount: normalize(request.body.amount) };\n"
            "  return processRefund(payload);\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "refunds,refunds,src/refunds.js:1-6,processRefund,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["entry_symbol"] == "refundRoute"
        assert gap["entry_paths"][0]["input_hints"] == ["amount"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text

    async def test_route_signature_params_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "refunds.py").write_text(
            "def process_refund(payload):\n"
            "    if not payload:\n"
            "        return 'missing'\n"
            "    return 'refunded'\n",
            encoding="utf-8",
        )
        (src / "routes.py").write_text(
            "def refund_route(amount: int, currency: str, request=None):\n"
            "    return process_refund({'amount': amount, 'currency': currency})\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "refunds,refunds,src/refunds.py:1-4,process_refund,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["input_hints"] == ["amount", "currency"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text
        assert "currency" in case_text

    async def test_route_header_cookie_fields_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "sessions.py").write_text(
            "def validate_session(payload):\n"
            "    if not payload:\n"
            "        return 'missing'\n"
            "    return 'ok'\n",
            encoding="utf-8",
        )
        (src / "routes.py").write_text(
            "def session_route(request):\n"
            "    payload = {\n"
            "        'user_id': request.headers.get('X-User-Id'),\n"
            "        'session_id': request.cookies['session_id'],\n"
            "    }\n"
            "    return validate_session(payload)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "sessions,sessions,src/sessions.py:1-4,validate_session,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["input_hints"] == ["X-User-Id", "session_id"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "X-User-Id" in case_text
        assert "session_id" in case_text

    async def test_route_request_header_method_feeds_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "routes.ts").write_text(
            "export function processPayment(req, res) {\n"
            "  const traceId = req.header('X-Trace-Id');\n"
            "  const auth = req.get('Authorization');\n"
            "  const amount = req.body.amount;\n"
            "  if (!traceId || !auth || !amount) {\n"
            "    return res.status(400).json({ error: 'missing input' });\n"
            "  }\n"
            "  return res.json({ traceId, amount });\n"
            "}\n\n"
            "router.post('/payments/:tenantId/process', processPayment);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,routes,src/routes.ts:1-8,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert "X-Trace-Id" in entry["input_hints"]
        assert "Authorization" in entry["input_hints"]
        assert "amount" in entry["input_hints"]
        assert "tenantId" in entry["input_hints"]
        assert "header" not in entry["input_hints"]
        assert "get" not in entry["input_hints"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "X-Trace-Id" in case_text
        assert "Authorization" in case_text
        assert "header" not in case_text
        assert "get" not in case_text

    async def test_route_input_hints_do_not_cross_function_boundaries(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "refunds.py").write_text(
            "def process_refund(payload):\n"
            "    if not payload:\n"
            "        return 'missing'\n"
            "    return 'refunded'\n",
            encoding="utf-8",
        )
        (src / "routes.py").write_text(
            "def admin_route(request):\n"
            "    token = request.headers.get('Admin-Token')\n"
            "    return {'token': token}\n"
            "\n"
            "def refund_route(request):\n"
            "    payload = {'amount': request.json['amount']}\n"
            "    return process_refund(payload)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "refunds,refunds,src/refunds.py:1-4,process_refund,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["entry_symbol"] == "refund_route"
        assert gap["entry_paths"][0]["input_hints"] == ["amount"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text
        assert "Admin-Token" not in case_text

    async def test_route_env_fields_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.py").write_text(
            "def process_payment(payload):\n"
            "    if not payload:\n"
            "        return 'missing'\n"
            "    return 'processed'\n",
            encoding="utf-8",
        )
        (src / "routes.py").write_text(
            "import os\n\n"
            "def payment_route(request):\n"
            "    payload = {\n"
            "        'timeout': os.environ.get('PAYMENT_TIMEOUT'),\n"
            "        'amount': request.json['amount'],\n"
            "    }\n"
            "    return process_payment(payload)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.py:1-4,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["input_hints"] == ["PAYMENT_TIMEOUT", "amount"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "PAYMENT_TIMEOUT" in case_text
        assert "amount" in case_text

    async def test_env_bootstrap_becomes_black_box_config_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.py").write_text(
            "def configure_payment_mode(mode):\n"
            "    if mode == 'disabled':\n"
            "        return 'skip'\n"
            "    return 'enabled'\n",
            encoding="utf-8",
        )
        (src / "bootstrap.py").write_text(
            "import os\n"
            "from payments import configure_payment_mode\n\n"
            "def bootstrap():\n"
            "    mode = os.environ.get('PAYMENT_MODE', 'enabled')\n"
            "    return configure_payment_mode(mode)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.py:1-4,configure_payment_mode,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "config"
        assert entry["entry_symbol"] == "bootstrap"
        assert "os.environ.get('PAYMENT_MODE'" in entry["evidence"]
        assert entry["input_hints"] == ["PAYMENT_MODE"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "PAYMENT_MODE" in case_text

    async def test_process_env_destructuring_feeds_black_box_config_input_hint(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.ts").write_text(
            "export function configurePaymentMode(mode: string) {\n"
            "  if (mode === 'disabled') {\n"
            "    return 'skip';\n"
            "  }\n"
            "  return 'enabled';\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "bootstrap.ts").write_text(
            "import { configurePaymentMode } from './payments';\n\n"
            "export function bootstrap() {\n"
            "  const { PAYMENT_MODE } = process.env;\n"
            "  return configurePaymentMode(PAYMENT_MODE ?? 'enabled');\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.ts:1-6,configurePaymentMode,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "config"
        assert entry["entry_symbol"] == "bootstrap"
        assert entry["input_hints"] == ["PAYMENT_MODE"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "PAYMENT_MODE" in case_text
        assert "process.env" not in case_text

    async def test_dotnet_environment_variable_feeds_black_box_config_input_hint(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentConfig.cs").write_text(
            "public class PaymentConfig {\n"
            "  public static bool ConfigurePaymentMode(string mode) {\n"
            "    if (string.IsNullOrWhiteSpace(mode)) {\n"
            "      return false;\n"
            "    }\n"
            "    return true;\n"
            "  }\n"
            "  public static bool Bootstrap() {\n"
            "    var mode = Environment.GetEnvironmentVariable(\"PAYMENT_MODE\");\n"
            "    return ConfigurePaymentMode(mode);\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,config,src/PaymentConfig.cs:2-7,ConfigurePaymentMode,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "config"
        assert entry["input_hints"] == ["PAYMENT_MODE"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "PAYMENT_MODE" in case_text
        assert "GetEnvironmentVariable" not in case_text

    async def test_dotnet_configuration_indexer_feeds_black_box_config_input_hint(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentConfig.cs").write_text(
            "public class PaymentConfig {\n"
            "  public static bool ConfigurePaymentMode(string mode) {\n"
            "    if (string.IsNullOrWhiteSpace(mode)) {\n"
            "      return false;\n"
            "    }\n"
            "    return true;\n"
            "  }\n"
            "  public static bool Bootstrap(IConfiguration configuration) {\n"
            "    var mode = configuration[\"Payment:Mode\"];\n"
            "    return ConfigurePaymentMode(mode);\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,config,src/PaymentConfig.cs:2-7,ConfigurePaymentMode,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "config"
        assert entry["entry_symbol"] == "Bootstrap"
        assert entry["input_hints"] == ["Payment:Mode"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "Payment:Mode" in case_text
        assert "configuration[" not in case_text

    async def test_spring_value_annotation_feeds_black_box_config_input_hint(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentConfig.java").write_text(
            "import org.springframework.beans.factory.annotation.Value;\n\n"
            "public class PaymentConfig {\n"
            "  public boolean configurePaymentMode(String mode) {\n"
            "    if (mode == null || mode.isBlank()) {\n"
            "      return false;\n"
            "    }\n"
            "    return true;\n"
            "  }\n"
            "  public boolean bootstrap(@Value(\"${payment.mode:enabled}\") String mode) {\n"
            "    return configurePaymentMode(mode);\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,config,src/PaymentConfig.java:4-9,configurePaymentMode,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "config"
        assert entry["entry_symbol"] == "bootstrap"
        assert entry["input_hints"] == ["payment.mode"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "payment.mode" in case_text
        assert "@Value" not in case_text

    async def test_java_environment_getproperty_feeds_black_box_config_input_hint(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentConfig.java").write_text(
            "import org.springframework.core.env.Environment;\n\n"
            "public class PaymentConfig {\n"
            "  public boolean configurePaymentMode(String mode) {\n"
            "    if (mode == null || mode.isBlank()) {\n"
            "      return false;\n"
            "    }\n"
            "    return true;\n"
            "  }\n"
            "  public boolean bootstrap(Environment environment) {\n"
            "    String mode = environment.getProperty(\"payment.mode\", \"enabled\");\n"
            "    return configurePaymentMode(mode);\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,config,src/PaymentConfig.java:4-9,configurePaymentMode,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "config"
        assert entry["entry_symbol"] == "bootstrap"
        assert entry["input_hints"] == ["payment.mode"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "payment.mode" in case_text
        assert "getProperty" not in case_text

    async def test_go_viper_getstring_feeds_black_box_config_input_hint(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentConfig.go").write_text(
            "package payments\n\n"
            "import \"github.com/spf13/viper\"\n\n"
            "func ConfigurePaymentMode(mode string) bool {\n"
            "    if mode == \"\" {\n"
            "        return false\n"
            "    }\n"
            "    return true\n"
            "}\n\n"
            "func Bootstrap() bool {\n"
            "    mode := viper.GetString(\"payment.mode\")\n"
            "    return ConfigurePaymentMode(mode)\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,config,src/PaymentConfig.go:5-10,ConfigurePaymentMode,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "config"
        assert entry["entry_symbol"] == "Bootstrap"
        assert entry["input_hints"] == ["payment.mode"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "payment.mode" in case_text
        assert "viper.GetString" not in case_text

    async def test_python_settings_attribute_feeds_black_box_config_input_hint(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payment_config.py").write_text(
            "from django.conf import settings\n\n"
            "def configure_payment_mode(mode):\n"
            "    if not mode:\n"
            "        return False\n"
            "    return True\n\n"
            "def bootstrap():\n"
            "    mode = settings.PAYMENT_MODE\n"
            "    return configure_payment_mode(mode)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,config,src/payment_config.py:3-6,configure_payment_mode,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "config"
        assert entry["entry_symbol"] == "bootstrap"
        assert entry["input_hints"] == ["PAYMENT_MODE"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "PAYMENT_MODE" in case_text
        assert "settings.PAYMENT_MODE" not in case_text

    async def test_rails_configuration_x_feeds_black_box_config_input_hint(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        app = tmp_path / "app"
        app.mkdir()
        (app / "payment_config.rb").write_text(
            "def configure_payment_mode(mode)\n"
            "  return false if mode.nil? || mode.empty?\n"
            "  true\n"
            "end\n\n"
            "def bootstrap\n"
            "  mode = Rails.configuration.x.payment_mode\n"
            "  configure_payment_mode(mode)\n"
            "end\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,config,app/payment_config.rb:1-4,configure_payment_mode,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "config"
        assert entry["entry_symbol"] == "bootstrap"
        assert entry["input_hints"] == ["payment_mode"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "payment_mode" in case_text
        assert "Rails.configuration.x.payment_mode" not in case_text

    async def test_rails_credentials_dig_feeds_black_box_config_input_hint(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        app = tmp_path / "app"
        app.mkdir()
        (app / "payment_config.rb").write_text(
            "def configure_payment_mode(mode)\n"
            "  return false if mode.nil? || mode.empty?\n"
            "  true\n"
            "end\n\n"
            "def bootstrap\n"
            "  mode = Rails.application.credentials.dig(:payments, :mode)\n"
            "  configure_payment_mode(mode)\n"
            "end\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,config,app/payment_config.rb:1-4,configure_payment_mode,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "config"
        assert entry["entry_symbol"] == "bootstrap"
        assert entry["input_hints"] == ["payments.mode"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "payments.mode" in case_text
        assert "credentials.dig" not in case_text

    async def test_rails_credentials_direct_reads_feed_black_box_config_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        app = tmp_path / "app"
        app.mkdir()
        (app / "payment_config.rb").write_text(
            "def configure_payment_mode(mode)\n"
            "  return false if mode.nil? || mode.empty?\n"
            "  true\n"
            "end\n\n"
            "def bootstrap\n"
            "  mode = Rails.application.credentials.fetch(:payment_mode)\n"
            "  fallback = Rails.application.credentials[:fallback_mode]\n"
            "  audit = Rails.application.credentials.audit_mode\n"
            "  configure_payment_mode(mode || fallback || audit)\n"
            "end\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,config,app/payment_config.rb:1-4,configure_payment_mode,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "config"
        assert entry["entry_symbol"] == "bootstrap"
        assert entry["input_hints"] == ["payment_mode", "fallback_mode", "audit_mode"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "payment_mode" in case_text
        assert "fallback_mode" in case_text
        assert "audit_mode" in case_text
        assert "credentials.fetch" not in case_text

    async def test_route_registration_reference_becomes_black_box_entry_without_agent(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "refunds.py").write_text(
            "def process_refund(request):\n"
            "    if not request:\n"
            "        return 'missing'\n"
            "    return 'refunded'\n",
            encoding="utf-8",
        )
        (src / "routes.py").write_text(
            "from refunds import process_refund\n"
            "app.add_url_rule('/refunds', view_func=process_refund, methods=['POST'])\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "refunds,refunds,src/refunds.py:1-4,process_refund,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["entry_kind"] == "route"
        assert gap["entry_paths"][0]["entry_symbol"] == "process_refund"

    async def test_direct_route_registration_reads_handler_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.py").write_text(
            "def process_payment(request):\n"
            "    payload = {\n"
            "        'amount': request.json['amount'],\n"
            "        'currency': request.args.get('currency'),\n"
            "    }\n"
            "    if not payload:\n"
            "        return 'missing'\n"
            "    return 'processed'\n",
            encoding="utf-8",
        )
        (src / "routes.py").write_text(
            "from payments import process_payment\n"
            "app.add_url_rule('/payments', view_func=process_payment, methods=['POST'])\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.py:1-8,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["entry_kind"] == "route"
        assert gap["entry_paths"][0]["entry_symbol"] == "process_payment"
        assert gap["entry_paths"][0]["input_hints"] == ["amount", "currency"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text
        assert "currency" in case_text

    async def test_direct_route_registration_path_params_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.py").write_text(
            "def process_payment(request):\n"
            "    payload = {\n"
            "        'amount': request.json['amount'],\n"
            "        'currency': request.args.get('currency'),\n"
            "    }\n"
            "    if not payload:\n"
            "        return 'missing'\n"
            "    return 'processed'\n",
            encoding="utf-8",
        )
        (src / "routes.py").write_text(
            "from payments import process_payment\n"
            "app.add_url_rule('/accounts/{account_id}/payments/:payment_id', view_func=process_payment, methods=['POST'])\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.py:1-8,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["external_trigger"] == "POST /accounts/{account_id}/payments/:payment_id"
        assert gap["entry_paths"][0]["input_hints"] == [
            "amount", "currency", "account_id", "payment_id",
        ]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "POST /accounts/{account_id}/payments/:payment_id" in case_text
        assert "account_id" in case_text
        assert "payment_id" in case_text

    async def test_flask_method_view_registration_becomes_black_box_entry(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "views.py").write_text(
            "from flask import request\n"
            "from flask.views import MethodView\n\n"
            "class PaymentView(MethodView):\n"
            "    def post(self, tenant_id):\n"
            "        amount = request.json['amount']\n"
            "        if not amount:\n"
            "            return {'status': 400}\n"
            "        return {'tenant_id': tenant_id, 'status': 200}\n",
            encoding="utf-8",
        )
        (src / "routes.py").write_text(
            "from views import PaymentView\n\n"
            "app.add_url_rule(\n"
            "    '/payments/<tenant_id>',\n"
            "    view_func=PaymentView.as_view('payment_view'),\n"
            "    methods=['POST'],\n"
            ")\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,views,src/views.py:5-9,views.PaymentView.post,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "views.PaymentView.post"
        assert entry["external_trigger"] == "POST /payments/<tenant_id>"
        assert entry["input_hints"] == ["amount", "tenant_id"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "POST /payments/<tenant_id>" in case_text
        assert "amount" in case_text
        assert "tenant_id" in case_text

    async def test_add_api_route_registration_becomes_black_box_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.py").write_text(
            "def process_payment(request, tenant_id: str):\n"
            "    payload = {\n"
            "        'amount': request.json['amount'],\n"
            "        'tenant_id': tenant_id,\n"
            "    }\n"
            "    if not payload['amount']:\n"
            "        return {'status': 400}\n"
            "    return {'status': 200}\n",
            encoding="utf-8",
        )
        (src / "routes.py").write_text(
            "from payments import process_payment\n"
            "app.add_api_route('/tenants/{tenant_id}/payments', endpoint=process_payment, methods=['POST'])\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.py:1-8,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "process_payment"
        assert "app.add_api_route" in entry["evidence"]
        assert entry["external_trigger"] == "POST /tenants/{tenant_id}/payments"
        assert entry["input_hints"] == ["amount", "tenant_id"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "POST /tenants/{tenant_id}/payments" in case_text
        assert "amount" in case_text
        assert "tenant_id" in case_text

    async def test_aiohttp_router_add_post_registration_becomes_black_box_entry(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.py").write_text(
            "async def process_payment(request):\n"
            "    payload = await request.json()\n"
            "    if not payload['amount']:\n"
            "        return {'status': 400}\n"
            "    return {'status': 200}\n",
            encoding="utf-8",
        )
        (src / "routes.py").write_text(
            "from payments import process_payment\n\n"
            "def setup_routes(app):\n"
            "    app.router.add_post('/payments/{tenant_id}', process_payment)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.py:1-5,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "process_payment"
        assert entry["external_trigger"] == "POST /payments/{tenant_id}"
        assert entry["input_hints"] == ["amount", "tenant_id"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "POST /payments/{tenant_id}" in case_text
        assert "amount" in case_text
        assert "tenant_id" in case_text

    async def test_aiohttp_add_routes_container_does_not_become_input_hint(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.py").write_text(
            "async def process_payment(request):\n"
            "    payload = await request.json()\n"
            "    if not payload['amount']:\n"
            "        return {'status': 400}\n"
            "    return {'status': 200}\n",
            encoding="utf-8",
        )
        (src / "routes.py").write_text(
            "from aiohttp import web\n"
            "from payments import process_payment\n\n"
            "def setup_routes(app):\n"
            "    app.add_routes([web.post('/payments/{tenant_id}', process_payment)])\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.py:1-5,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["external_trigger"] == "POST /payments/{tenant_id}"
        assert entry["input_hints"] == ["amount", "tenant_id"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "app," not in case_text
        assert "amount" in case_text
        assert "tenant_id" in case_text

    async def test_js_route_table_handler_object_becomes_black_box_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.ts").write_text(
            "export function processPayment(req) {\n"
            "  const payload = {\n"
            "    amount: req.body.amount,\n"
            "    tenantId: req.params.tenantId,\n"
            "  };\n"
            "  if (!payload.amount) {\n"
            "    return { status: 400 };\n"
            "  }\n"
            "  return { status: 200 };\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "routes.ts").write_text(
            "import { processPayment } from './payments';\n\n"
            "export const routes = [\n"
            "  {\n"
            "    method: 'POST',\n"
            "    path: '/tenants/:tenantId/payments',\n"
            "    handler: processPayment,\n"
            "  },\n"
            "];\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.ts:1-10,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "processPayment"
        assert entry["tool"] == "source-table"
        assert "handler: processPayment" in entry["evidence"]
        assert entry["external_trigger"] == "POST /tenants/:tenantId/payments"
        assert entry["input_hints"] == ["tenantId", "amount"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "POST /tenants/:tenantId/payments" in case_text

    async def test_js_route_table_method_array_feeds_black_box_trigger(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.ts").write_text(
            "export function processPayment(request) {\n"
            "  const amount = request.body.amount;\n"
            "  if (!amount) {\n"
            "    return { status: 400 };\n"
            "  }\n"
            "  return { ok: true };\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "routes.ts").write_text(
            "import { processPayment } from './payments';\n"
            "export const routes = [\n"
            "  {\n"
            "    method: ['POST'],\n"
            "    path: '/tenants/:tenantId/payments',\n"
            "    handler: processPayment,\n"
            "  },\n"
            "];\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.ts:1-7,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["tool"] == "source-table"
        assert entry["external_trigger"] == "POST /tenants/:tenantId/payments"
        assert entry["input_hints"] == ["tenantId", "amount"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "POST /tenants/:tenantId/payments" in case_text

    async def test_js_route_table_controller_method_becomes_black_box_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.ts").write_text(
            "export class PaymentController {\n"
            "  processPayment(req) {\n"
            "    const payload = {\n"
            "      amount: req.body.amount,\n"
            "      tenantId: req.params.tenantId,\n"
            "    };\n"
            "    if (!payload.amount) {\n"
            "      return { status: 400 };\n"
            "    }\n"
            "    return { status: 200 };\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "routes.ts").write_text(
            "import { PaymentController } from './payments';\n\n"
            "const controller = new PaymentController();\n"
            "export const routes = [\n"
            "  {\n"
            "    method: 'POST',\n"
            "    path: '/tenants/:tenantId/payments',\n"
            "    handler: controller.processPayment,\n"
            "  },\n"
            "];\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.ts:2-11,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "processPayment"
        assert entry["tool"] == "source-table"
        assert "handler: controller.processPayment" in entry["evidence"]
        assert entry["external_trigger"] == "POST /tenants/:tenantId/payments"
        assert entry["input_hints"] == ["tenantId", "amount"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "POST /tenants/:tenantId/payments" in case_text

    async def test_route_object_handler_filters_response_toolkit_signature_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "routes.ts").write_text(
            "export function processPayment(request, reply) {\n"
            "  const amount = request.body.amount;\n"
            "  if (!amount) {\n"
            "    return reply.code(400).send();\n"
            "  }\n"
            "  return reply.send({ ok: true });\n"
            "}\n\n"
            "fastify.route({\n"
            "  method: 'POST',\n"
            "  url: '/payments/:paymentId',\n"
            "  handler: processPayment,\n"
            "});\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,routes,src/routes.ts:1-7,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["external_trigger"] == "POST /payments/:paymentId"
        assert entry["input_hints"] == ["paymentId", "amount"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "paymentId" in case_text
        assert "amount" in case_text
        assert "reply" not in case_text

    async def test_gin_route_registration_reads_context_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "controller.go").write_text(
            "package payments\n\n"
            "func (ctl *PaymentController) ProcessPayment(c *gin.Context) {\n"
            "    tenantID := c.Param(\"tenantId\")\n"
            "    amount := c.Query(\"amount\")\n"
            "    if amount == \"\" {\n"
            "        c.JSON(400, gin.H{\"error\": tenantID})\n"
            "        return\n"
            "    }\n"
            "    c.JSON(200, gin.H{\"ok\": true})\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "server.go").write_text(
            "package payments\n\n"
            "func RegisterRoutes(r *gin.Engine, controller *PaymentController) {\n"
            "    r.POST(\"/payments/:tenantId\", controller.ProcessPayment)\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/controller.go:3-11,ProcessPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "ProcessPayment"
        assert entry["input_hints"] == ["tenantId", "amount"]

    async def test_echo_context_methods_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "controller.go").write_text(
            "package payments\n\n"
            "func ProcessPayment(c echo.Context) error {\n"
            "    tenantID := c.Param(\"tenantId\")\n"
            "    amount := c.QueryParam(\"amount\")\n"
            "    currency := c.FormValue(\"currency\")\n"
            "    if amount == \"\" {\n"
            "        return c.JSON(400, map[string]string{\"error\": tenantID})\n"
            "    }\n"
            "    return processPayment(amount, currency)\n"
            "}\n\n"
            "func processPayment(amount string, currency string) error {\n"
            "    if amount == \"\" {\n"
            "        return errors.New(\"missing\")\n"
            "    }\n"
            "    return nil\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "server.go").write_text(
            "package payments\n\n"
            "func RegisterRoutes(e *echo.Echo) {\n"
            "    e.POST(\"/payments/:tenantId\", ProcessPayment)\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/controller.go:13-18,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "ProcessPayment"
        assert entry["input_hints"] == ["tenantId", "amount", "currency"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "tenantId" in case_text
        assert "amount" in case_text
        assert "currency" in case_text

    async def test_gin_bind_json_struct_fields_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "controller.go").write_text(
            "package payments\n\n"
            "type PaymentRequest struct {\n"
            "    Amount int `json:\"amount\"`\n"
            "    Currency string `json:\"currency,omitempty\"`\n"
            "}\n\n"
            "func (ctl *PaymentController) ProcessPayment(c *gin.Context) {\n"
            "    tenantID := c.Param(\"tenantId\")\n"
            "    var req PaymentRequest\n"
            "    if err := c.ShouldBindJSON(&req); err != nil {\n"
            "        c.JSON(400, gin.H{\"error\": tenantID})\n"
            "        return\n"
            "    }\n"
            "    c.JSON(200, gin.H{\"amount\": req.Amount})\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "server.go").write_text(
            "package payments\n\n"
            "func RegisterRoutes(r *gin.Engine, controller *PaymentController) {\n"
            "    r.POST(\"/payments/:tenantId\", controller.ProcessPayment)\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/controller.go:8-16,ProcessPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "ProcessPayment"
        assert entry["input_hints"] == ["tenantId", "amount", "currency"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text
        assert "currency" in case_text

    async def test_go_http_handlefunc_becomes_black_box_route(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "handler.go").write_text(
            "package payments\n\n"
            "import \"net/http\"\n\n"
            "func processPayment(w http.ResponseWriter, r *http.Request) {\n"
            "    tenantID := r.PathValue(\"tenantId\")\n"
            "    amount := r.URL.Query().Get(\"amount\")\n"
            "    if amount == \"\" {\n"
            "        http.Error(w, tenantID, http.StatusBadRequest)\n"
            "        return\n"
            "    }\n"
            "    w.WriteHeader(http.StatusOK)\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "server.go").write_text(
            "package payments\n\n"
            "import \"net/http\"\n\n"
            "func RegisterRoutes() {\n"
            "    http.HandleFunc(\"/payments/{tenantId}\", processPayment)\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,handler,src/handler.go:5-12,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "processPayment"
        assert entry["external_trigger"] == "/payments/{tenantId}"
        assert entry["input_hints"] == ["tenantId", "amount"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "/payments/{tenantId}" in case_text
        assert "amount" in case_text

    async def test_go_http_handle_handlerfunc_wrapper_becomes_black_box_route(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "handler.go").write_text(
            "package payments\n\n"
            "import \"net/http\"\n\n"
            "func processPayment(w http.ResponseWriter, r *http.Request) {\n"
            "    currency := r.URL.Query().Get(\"currency\")\n"
            "    if currency == \"\" {\n"
            "        http.Error(w, \"missing\", http.StatusBadRequest)\n"
            "        return\n"
            "    }\n"
            "    w.WriteHeader(http.StatusOK)\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "server.go").write_text(
            "package payments\n\n"
            "import \"net/http\"\n\n"
            "func RegisterRoutes(mux *http.ServeMux) {\n"
            "    mux.Handle(\"/payments\", http.HandlerFunc(processPayment))\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,handler,src/handler.go:5-11,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "processPayment"
        assert entry["external_trigger"] == "/payments"
        assert entry["input_hints"] == ["currency"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "/payments" in case_text
        assert "currency" in case_text

    async def test_go_mux_methods_chain_feeds_black_box_http_method(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "handler.go").write_text(
            "package payments\n\n"
            "import \"net/http\"\n\n"
            "func processPayment(w http.ResponseWriter, r *http.Request) {\n"
            "    tenantID := r.PathValue(\"tenantId\")\n"
            "    amount := r.URL.Query().Get(\"amount\")\n"
            "    if amount == \"\" {\n"
            "        http.Error(w, tenantID, http.StatusBadRequest)\n"
            "        return\n"
            "    }\n"
            "    w.WriteHeader(http.StatusOK)\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "server.go").write_text(
            "package payments\n\n"
            "func RegisterRoutes(r *mux.Router) {\n"
            "    r.HandleFunc(\"/payments/{tenantId}\", processPayment).Methods(\"POST\")\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,handler,src/handler.go:5-12,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "processPayment"
        assert entry["external_trigger"] == "POST /payments/{tenantId}"
        assert entry["input_hints"] == ["tenantId", "amount"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "POST /payments/{tenantId}" in case_text

    async def test_ktor_route_dsl_call_site_becomes_black_box_route(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentService.kt").write_text(
            "fun processPayment(call: ApplicationCall) {\n"
            "    val tenantId = call.parameters[\"tenantId\"]\n"
            "    val amount = call.request.queryParameters[\"amount\"]\n"
            "    if (amount == null) {\n"
            "        call.respond(HttpStatusCode.BadRequest, tenantId ?: \"missing\")\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "Routes.kt").write_text(
            "fun Application.configureRoutes() {\n"
            "    routing {\n"
            "        post(\"/tenants/{tenantId}/payments\") {\n"
            "            processPayment(call)\n"
            "        }\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,service,src/PaymentService.kt:1-7,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "processPayment"
        assert entry["external_trigger"] == "POST /tenants/{tenantId}/payments"
        assert entry["input_hints"] == ["tenantId", "amount"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "POST /tenants/{tenantId}/payments" in case_text
        assert "tenantId" in case_text
        assert "amount" in case_text

    async def test_rust_actix_route_macro_becomes_black_box_route(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "handlers.rs").write_text(
            "use actix_web::{post, web, HttpResponse};\n\n"
            "#[post(\"/payments/{tenant_id}\")]\n"
            "async fn process_payment(path: web::Path<String>) -> HttpResponse {\n"
            "    let tenant_id = path.into_inner();\n"
            "    if tenant_id.is_empty() {\n"
            "        return HttpResponse::BadRequest().finish();\n"
            "    }\n"
            "    HttpResponse::Ok().finish()\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,handlers,src/handlers.rs:4-10,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "process_payment"
        assert entry["external_trigger"] == "POST /payments/{tenant_id}"
        assert entry["input_hints"] == ["tenant_id"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "POST /payments/{tenant_id}" in case_text
        assert "tenant_id" in case_text

    async def test_axum_route_method_wrapper_feeds_black_box_trigger(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "handlers.rs").write_text(
            "use axum::{extract::Path, Json};\n\n"
            "async fn process_payment(Path(tenant_id): Path<String>) -> Json<String> {\n"
            "    if tenant_id.is_empty() {\n"
            "        return Json(\"missing\".to_string());\n"
            "    }\n"
            "    Json(tenant_id)\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "routes.rs").write_text(
            "use axum::{routing::post, Router};\n"
            "use crate::handlers::process_payment;\n\n"
            "pub fn app() -> Router {\n"
            "    Router::new().route(\"/tenants/{tenant_id}/payments\", post(process_payment))\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,handlers,src/handlers.rs:3-8,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "process_payment"
        assert entry["external_trigger"] == "POST /tenants/{tenant_id}/payments"
        assert entry["input_hints"] == ["tenant_id"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "POST /tenants/{tenant_id}/payments" in case_text
        assert "tenant_id" in case_text

    async def test_ktor_receive_data_class_fields_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentService.kt").write_text(
            "data class PaymentRequest(\n"
            "    val amount: Int,\n"
            "    val currency: String,\n"
            ")\n\n"
            "suspend fun processPayment(call: ApplicationCall) {\n"
            "    val tenantId = call.parameters[\"tenantId\"]\n"
            "    val payload = call.receive<PaymentRequest>()\n"
            "    if (!validatePayment(payload)) {\n"
            "        call.respond(HttpStatusCode.BadRequest, tenantId ?: \"missing\")\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "Routes.kt").write_text(
            "fun Application.configureRoutes() {\n"
            "    routing {\n"
            "        post(\"/tenants/{tenantId}/payments\") {\n"
            "            processPayment(call)\n"
            "        }\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,service,src/PaymentService.kt:6-12,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["external_trigger"] == "POST /tenants/{tenantId}/payments"
        assert entry["input_hints"] == ["tenantId", "amount", "currency"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "tenantId" in case_text
        assert "amount" in case_text
        assert "currency" in case_text

    async def test_kotlin_handler_data_class_parameter_feeds_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentRoutes.kt").write_text(
            "data class PaymentRequest(\n"
            "    val amount: Int,\n"
            "    val currency: String,\n"
            ")\n\n"
            "fun processPayment(payload: PaymentRequest, tenantId: String) {\n"
            "    if (!validatePayment(payload)) {\n"
            "        throw IllegalArgumentException(tenantId)\n"
            "    }\n"
            "}\n\n"
            "fun Application.configureRoutes() {\n"
            "    routing {\n"
            "        post(\"/tenants/{tenantId}/payments\") {\n"
            "            processPayment(call.receive(), call.parameters[\"tenantId\"] ?: \"\")\n"
            "        }\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,routes,src/PaymentRoutes.kt:6-10,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["external_trigger"] == "POST /tenants/{tenantId}/payments"
        assert entry["input_hints"] == ["tenantId", "amount", "currency"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "tenantId" in case_text
        assert "amount" in case_text
        assert "currency" in case_text

    async def test_direct_websocket_registration_becomes_black_box_route(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "handlers.py").write_text(
            "async def stream_updates(websocket, client_id):\n"
            "    if not client_id:\n"
            "        await websocket.close()\n"
            "    return client_id\n",
            encoding="utf-8",
        )
        (src / "gateway.py").write_text(
            "from handlers import stream_updates\n"
            "app.websocket('/ws/{client_id}', stream_updates)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "updates,handlers,src/handlers.py:1-4,stream_updates,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "stream_updates"
        assert "app.websocket" in entry["evidence"]
        assert "client_id" in entry["input_hints"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "client_id" in case_text

    async def test_aws_lambda_handler_is_black_box_event_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "lambda"
        src.mkdir()
        (src / "billing.py").write_text(
            "def lambda_handler(event, context):\n"
            "    invoice_id = event.get('invoice_id')\n"
            "    if not invoice_id:\n"
            "        return {'statusCode': 400}\n"
            "    return {'statusCode': 200, 'invoice_id': invoice_id}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,lambda,lambda/billing.py:1-5,lambda_handler,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "event"
        assert entry["entry_symbol"] == "lambda_handler"
        assert entry["tool"] == "source-serverless-handler"
        assert "lambda_handler(event, context)" in entry["evidence"]
        assert entry["input_hints"] == ["invoice_id", "event"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "invoice_id" in case_text

    async def test_lambda_event_nested_request_fields_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "lambda"
        src.mkdir()
        (src / "billing.py").write_text(
            "def lambda_handler(event, context):\n"
            "    params = event.get('queryStringParameters') or {}\n"
            "    amount = params.get('amount')\n"
            "    tenant_id = event['pathParameters']['tenant_id']\n"
            "    if not amount:\n"
            "        return {'statusCode': 400}\n"
            "    return {'statusCode': 200, 'body': tenant_id}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,lambda,lambda/billing.py:1-7,lambda_handler,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "event"
        assert entry["entry_symbol"] == "lambda_handler"
        assert entry["input_hints"] == ["amount", "tenant_id", "event"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text
        assert "tenant_id" in case_text

    async def test_node_lambda_exports_handler_is_black_box_event_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "functions"
        src.mkdir()
        (src / "billing.js").write_text(
            "exports.handler = async (event, context) => {\n"
            "  const invoiceId = event.invoice_id;\n"
            "  if (!invoiceId) {\n"
            "    return { statusCode: 400 };\n"
            "  }\n"
            "  return { statusCode: 200, invoiceId };\n"
            "};\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,lambda,functions/billing.js:1-7,handler,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "event"
        assert entry["entry_symbol"] == "handler"
        assert entry["tool"] == "source-serverless-handler"
        assert "exports.handler" in entry["evidence"]
        assert entry["input_hints"] == ["invoice_id", "event"]

    async def test_azure_function_json_http_trigger_is_black_box_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "HttpTrigger"
        src.mkdir()
        (src / "__init__.py").write_text(
            "import azure.functions as func\n"
            "\n"
            "def main(req: func.HttpRequest) -> func.HttpResponse:\n"
            "    order_id = req.route_params.get('order_id')\n"
            "    dry_run = req.params.get('dry_run')\n"
            "    if not order_id:\n"
            "        return func.HttpResponse('missing', status_code=400)\n"
            "    return func.HttpResponse(dry_run or order_id)\n",
            encoding="utf-8",
        )
        (src / "function.json").write_text(
            json.dumps({
                "bindings": [{
                    "authLevel": "function",
                    "type": "httpTrigger",
                    "direction": "in",
                    "name": "req",
                    "methods": ["post"],
                    "route": "orders/{order_id}",
                }],
            }),
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "orders,HttpTrigger,HttpTrigger/__init__.py:3-8,main,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "main"
        assert entry["tool"] == "source-serverless-config"
        assert entry["external_trigger"] == "POST /orders/{order_id}"
        assert "order_id" in entry["input_hints"]
        assert "dry_run" in entry["input_hints"]
        assert "function.json" in entry["evidence"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "order_id" in case_text
        assert "dry_run" in case_text

    async def test_gcp_functions_framework_http_decorator_is_black_box_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "functions"
        src.mkdir()
        (src / "payments.py").write_text(
            "import functions_framework\n"
            "\n"
            "@functions_framework.http\n"
            "def process_payment(request):\n"
            "    body = request.get_json(silent=True) or {}\n"
            "    amount = body.get('amount')\n"
            "    dry_run = request.args.get('dry_run')\n"
            "    if not amount:\n"
            "        return ('missing', 400)\n"
            "    return {'dry_run': dry_run, 'amount': amount}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,functions,functions/payments.py:4-10,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "process_payment"
        assert entry["tool"] == "source-serverless-decorator"
        assert entry["external_trigger"] == "HTTP process_payment"
        assert "amount" in entry["input_hints"]
        assert "dry_run" in entry["input_hints"]
        assert "functions_framework.http" in entry["evidence"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text
        assert "dry_run" in case_text

    async def test_js_arrow_route_handler_is_source_backed_with_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.js").write_text(
            "export const processPayment = async (request) => {\n"
            "  const { amount, currency } = request.body;\n"
            "  if (!amount) {\n"
            "    return { status: 400 };\n"
            "  }\n"
            "  return { amount, currency };\n"
            "};\n",
            encoding="utf-8",
        )
        (src / "routes.js").write_text(
            "import { processPayment } from './payments';\n"
            "app.post('/payments', processPayment);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.js:1-7,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["source_window"]["available"] is True
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "processPayment"
        assert entry["input_hints"] == ["amount", "currency"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text
        assert "currency" in case_text

    async def test_express_router_mount_prefix_feeds_black_box_trigger_and_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.js").write_text(
            "export async function processPayment(request) {\n"
            "  const amount = request.body.amount;\n"
            "  if (!amount) {\n"
            "    return { status: 400 };\n"
            "  }\n"
            "  return { status: 200 };\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "routes.js").write_text(
            "import express from 'express';\n"
            "import { processPayment } from './payments';\n"
            "const router = express.Router();\n"
            "router.post('/payments/:payment_id', processPayment);\n"
            "app.use('/tenants/:tenant_id', router);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.js:1-7,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["external_trigger"] == "POST /tenants/:tenant_id/payments/:payment_id"
        assert "tenant_id" in entry["input_hints"]
        assert "payment_id" in entry["input_hints"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "/tenants/:tenant_id/payments/:payment_id" in case_text

    async def test_multiline_express_router_mount_prefix_feeds_black_box_trigger(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.js").write_text(
            "export async function processPayment(request) {\n"
            "  const amount = request.body.amount;\n"
            "  if (!amount) {\n"
            "    return { status: 400 };\n"
            "  }\n"
            "  return { status: 200 };\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "routes.js").write_text(
            "import express from 'express';\n"
            "import { processPayment } from './payments';\n"
            "const router = express.Router();\n"
            "router.post(\n"
            "  '/payments/:payment_id',\n"
            "  processPayment,\n"
            ");\n"
            "app.use(\n"
            "  '/tenants/:tenant_id',\n"
            "  router,\n"
            ");\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.js:1-7,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["external_trigger"] == "POST /tenants/:tenant_id/payments/:payment_id"
        assert "tenant_id" in entry["input_hints"]
        assert "payment_id" in entry["input_hints"]

    async def test_multiline_express_router_route_chain_becomes_black_box_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.js").write_text(
            "export async function processPayment(request) {\n"
            "  const amount = request.body.amount;\n"
            "  if (!amount) {\n"
            "    return { status: 400 };\n"
            "  }\n"
            "  return { status: 200 };\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "routes.js").write_text(
            "import express from 'express';\n"
            "import { processPayment } from './payments';\n"
            "const router = express.Router();\n"
            "router.route('/payments/:payment_id')\n"
            "  .post(processPayment);\n"
            "app.use('/tenants/:tenant_id', router);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.js:1-7,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["external_trigger"] == "POST /tenants/:tenant_id/payments/:payment_id"
        assert "tenant_id" in entry["input_hints"]
        assert "payment_id" in entry["input_hints"]

    async def test_express_zod_schema_fields_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.js").write_text(
            "const paymentSchema = z.object({\n"
            "  amount: z.number(),\n"
            "  currency: z.string().optional(),\n"
            "});\n\n"
            "export async function processPayment(request) {\n"
            "  const payload = paymentSchema.parse(request.body);\n"
            "  if (!payload.amount) {\n"
            "    return { status: 400 };\n"
            "  }\n"
            "  return { status: 200 };\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "routes.js").write_text(
            "import { processPayment } from './payments';\n"
            "app.post('/payments', processPayment);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.js:6-11,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "processPayment"
        assert entry["input_hints"] == ["amount", "currency"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text
        assert "currency" in case_text

    async def test_node_mjs_route_handler_without_extension_is_source_backed(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.mjs").write_text(
            "export async function processPayment(request) {\n"
            "  const amount = request.body.amount;\n"
            "  if (!amount) {\n"
            "    return { status: 400 };\n"
            "  }\n"
            "  return { status: 200 };\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "routes.mjs").write_text(
            "import { processPayment } from './payments.mjs';\n"
            "app.post('/payments/:payment_id', processPayment);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments:1-7,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["source_window"]["available"] is True
        assert gap["source_window"]["path"] == "src/payments.mjs"
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "processPayment"
        assert set(entry["input_hints"]) == {"payment_id", "amount"}

    async def test_multiline_anonymous_route_callback_feeds_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.js").write_text(
            "function processPayment(payload) {\n"
            "  if (!payload) {\n"
            "    return 'missing';\n"
            "  }\n"
            "  return 'processed';\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "routes.js").write_text(
            "app.post('/accounts/{account_id}/payments/:payment_id', (request) => {\n"
            "  const payload = {\n"
            "    amount: request.body.amount,\n"
            "    currency: request.query.currency,\n"
            "  };\n"
            "  return processPayment(payload);\n"
            "});\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.js:1-6,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["input_hints"] == ["amount", "currency", "account_id", "payment_id"]
        assert "app.post" in entry["evidence"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text
        assert "currency" in case_text
        assert "account_id" in case_text
        assert "payment_id" in case_text

    async def test_ts_class_field_handler_is_read_as_source_window(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "controller.ts").write_text(
            "class PaymentController {\n"
            "  public processPayment = async (request: Request) => {\n"
            "    const amount = request.body.amount;\n"
            "    if (!amount) {\n"
            "      return { status: 400 };\n"
            "    }\n"
            "    return { amount };\n"
            "  };\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/controller.ts:2-8,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["source_window"]["available"] is True
        assert gap["source_window"]["definition_line"] == 2
        assert "public processPayment" in gap["source_window"]["text"]

    async def test_ruby_function_without_extension_is_read_as_source_window(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payment_service.rb").write_text(
            "def process_payment(request)\n"
            "  return nil unless request\n"
            "  request\n"
            "end\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,service,src/payment_service:1-4,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["source_window"]["available"] is True
        assert gap["source_window"]["definition_line"] == 1
        assert "def process_payment" in gap["source_window"]["text"]
        assert any(
            branch.get("condition") == "unless (request)"
            for branch in gap["trigger_branches"]
        )

    async def test_ruby_class_method_is_read_as_source_window(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payment_service.rb").write_text(
            "class PaymentService\n"
            "  def self.process_payment(request)\n"
            "    return nil unless request\n"
            "    request\n"
            "  end\n"
            "end\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,service,src/payment_service.rb:2-5,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["source_window"]["available"] is True
        assert gap["source_window"]["definition_line"] == 2
        assert "def self.process_payment" in gap["source_window"]["text"]

    async def test_sidekiq_worker_perform_is_black_box_job_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        workers = tmp_path / "app" / "workers"
        workers.mkdir(parents=True)
        (workers / "invoice_worker.rb").write_text(
            "class InvoiceWorker\n"
            "  include Sidekiq::Worker\n"
            "  sidekiq_options queue: 'invoice_queue'\n\n"
            "  def perform(invoice_id)\n"
            "    return :missing unless invoice_id\n"
            "    :processed\n"
            "  end\n"
            "end\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,worker,app/workers/invoice_worker.rb:5-8,perform,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "job"
        assert entry["entry_symbol"] == "perform"
        assert entry["tool"] == "source-ruby-worker"
        assert "Sidekiq::Worker" in entry["evidence"]
        assert entry["input_hints"] == ["invoice_queue", "invoice_id"]

    async def test_php_open_tag_function_is_read_as_source_window(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payment_service.php").write_text(
            "<?php function process_payment($request) {\n"
            "    if (!$request) {\n"
            "        return null;\n"
            "    }\n"
            "    return $request;\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,service,src/payment_service.php:1-6,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["source_window"]["available"] is True
        assert gap["source_window"]["definition_line"] == 1
        assert "<?php function process_payment" in gap["source_window"]["text"]

    async def test_laravel_should_queue_job_handle_is_black_box_job_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        jobs = tmp_path / "app" / "Jobs"
        jobs.mkdir(parents=True)
        (jobs / "ProcessInvoice.php").write_text(
            "<?php\n"
            "namespace App\\Jobs;\n\n"
            "use Illuminate\\Bus\\Queueable;\n"
            "use Illuminate\\Contracts\\Queue\\ShouldQueue;\n\n"
            "class ProcessInvoice implements ShouldQueue\n"
            "{\n"
            "    use Queueable;\n\n"
            "    public $queue = 'invoice_queue';\n\n"
            "    public function __construct(public int $invoiceId) {}\n\n"
            "    public function handle(): void\n"
            "    {\n"
            "        if (!$this->invoiceId) {\n"
            "            return;\n"
            "        }\n"
            "        app('billing')->process($this->invoiceId);\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,job,app/Jobs/ProcessInvoice.php:14-20,handle,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "job"
        assert entry["entry_symbol"] == "handle"
        assert entry["tool"] == "source-php-job"
        assert "ShouldQueue" in entry["evidence"]
        assert entry["input_hints"] == ["invoice_queue", "invoiceId"]

    async def test_commonjs_exported_handler_is_read_as_source_window(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "handlers.js").write_text(
            "module.exports.processPayment = async (request) => {\n"
            "  const amount = request.body.amount;\n"
            "  if (!amount) {\n"
            "    return { status: 400 };\n"
            "  }\n"
            "  return { status: 200 };\n"
            "};\n",
            encoding="utf-8",
        )
        (src / "routes.js").write_text(
            "const handlers = require('./handlers');\n"
            "router.post('/payments/:payment_id', handlers.processPayment);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,handlers,src/handlers.js:1-7,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["source_window"]["available"] is True
        assert gap["source_window"]["definition_line"] == 1
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "processPayment"
        assert entry["input_hints"] == ["amount", "payment_id"]

    async def test_go_receiver_method_is_read_as_source_window(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "service.go").write_text(
            "package payments\n\n"
            "func (s *PaymentService) ProcessPayment(req Request) Response {\n"
            "    if req.Amount == 0 {\n"
            "        return Response{Status: 400}\n"
            "    }\n"
            "    return Response{Status: 200}\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,service,src/service.go:3-8,ProcessPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["source_window"]["available"] is True
        assert gap["source_window"]["definition_line"] == 3
        assert "func (s *PaymentService) ProcessPayment" in gap["source_window"]["text"]

    async def test_cpp_hh_header_without_extension_is_read_as_source_window(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        include = tmp_path / "include"
        include.mkdir()
        (include / "payment_service.hh").write_text(
            "inline bool processPayment(const PaymentRequest& request) {\n"
            "    if (!request.amount) {\n"
            "        return false;\n"
            "    }\n"
            "    return true;\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,service,include/payment_service:1-6,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["source_window"]["available"] is True
        assert gap["source_window"]["path"] == "include/payment_service.hh"
        assert "inline bool processPayment" in gap["source_window"]["text"]

    async def test_coverage_source_file_iterator_includes_supported_script_languages(self, tmp_path):
        from app.services.coverage_analyzer import _iter_source_files

        src = tmp_path / "src"
        src.mkdir()
        for name in (
            "payment_service.rb",
            "payment_service.php",
            "payment_script.kts",
            "payment_service.hh",
            "payment_service.hxx",
        ):
            (src / name).write_text("source\n", encoding="utf-8")
        (src / "payment.md").write_text("docs\n", encoding="utf-8")

        rel_paths = {
            path.relative_to(tmp_path).as_posix()
            for path in _iter_source_files(tmp_path, limit=10)
        }

        assert "src/payment_service.rb" in rel_paths
        assert "src/payment_service.php" in rel_paths
        assert "src/payment_script.kts" in rel_paths
        assert "src/payment_service.hh" in rel_paths
        assert "src/payment_service.hxx" in rel_paths
        assert "src/payment.md" not in rel_paths

    async def test_coverage_definition_detection_handles_ruby_class_methods(self):
        from app.services.coverage_analyzer import _match_def_name

        assert _match_def_name("def self.process_payment(request)") == "process_payment"
        assert _match_def_name("PaymentService.process_payment(request)") is None

    async def test_coverage_definition_detection_handles_php_open_tag_functions(self):
        from app.services.coverage_analyzer import _match_def_name

        assert _match_def_name("<?php function process_payment($request) {") == "process_payment"
        assert _match_def_name("    public function handle(): void") == "handle"
        assert _match_def_name("process_payment($request);") is None

    async def test_coverage_definition_detection_handles_commonjs_exports(self):
        from app.services.coverage_analyzer import _match_def_name

        assert _match_def_name("exports.processPayment = function(request) {") == "processPayment"
        assert _match_def_name(
            "module.exports.processPayment = async (request) => {"
        ) == "processPayment"
        assert _match_def_name("handlers.processPayment(request);") is None

    async def test_coverage_definition_detection_handles_go_receiver_methods(self):
        from app.services.coverage_analyzer import _match_def_name

        assert _match_def_name(
            "func (s *PaymentService) ProcessPayment(req Request) Response {"
        ) == "ProcessPayment"
        assert _match_def_name(
            "return service.ProcessPayment(req)"
        ) is None

    async def test_coverage_definition_detection_rejects_indented_bare_calls(self):
        from app.services.coverage_analyzer import _match_def_name

        assert _match_def_name("            processPayment(call)") is None
        assert _match_def_name('            processPayment(call.receive(), call.parameters["tenantId"])') is None
        assert _match_def_name("  processPayment(payload)") is None
        assert _match_def_name("fun processPayment(call: ApplicationCall) {") == "processPayment"

    async def test_ripgrep_line_parser_accepts_windows_drive_paths(self):
        from app.services.coverage_analyzer import _parse_ripgrep_line

        parsed = _parse_ripgrep_line(
            "E:/repo/src/routes.kt:42:            processPayment(call)"
        )

        assert parsed == (
            "E:/repo/src/routes.kt",
            42,
            "            processPayment(call)",
        )

    async def test_coverage_definition_detection_handles_swift_functions(self):
        from app.services.coverage_analyzer import _match_def_name

        assert _match_def_name(
            "public func processPayment(_ request: PaymentRequest) -> PaymentResult {"
        ) == "processPayment"
        assert _match_def_name(
            "return service.processPayment(request)"
        ) is None

    async def test_coverage_definition_detection_handles_scala_functions(self):
        from app.services.coverage_analyzer import _match_def_name

        assert _match_def_name(
            "private def processPayment(request: PaymentRequest): PaymentResult = {"
        ) == "processPayment"
        assert _match_def_name(
            "def processPayment = Action(parse.json) { implicit request =>"
        ) == "processPayment"
        assert _match_def_name(
            "service.processPayment(request)"
        ) is None

    async def test_coverage_source_window_resolves_scala_function_definition(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentService.scala").write_text(
            "class PaymentService {\n"
            "  private def processPayment(request: PaymentRequest): PaymentResult = {\n"
            "    if (request == null) {\n"
            "      PaymentResult.failed\n"
            "    } else {\n"
            "      PaymentResult.ok\n"
            "    }\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,service,src/PaymentService.scala:2-8,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["source_window"]["available"] is True
        assert gap["source_window"]["definition_line"] == 2
        assert "private def processPayment" in gap["source_window"]["text"]

    async def test_scala_play_action_is_black_box_route_without_caller(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentController.scala").write_text(
            "class PaymentController {\n"
            "  def processPayment = Action(parse.json) { implicit request =>\n"
            "    if (request.body == null) {\n"
            "      BadRequest\n"
            "    } else {\n"
            "      Ok\n"
            "    }\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/PaymentController.scala:2-8,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "processPayment"
        assert entry["tool"] == "source-inline-entry"
        assert "Action(parse.json)" in entry["evidence"]

    async def test_rails_route_table_action_is_black_box_route_with_params(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        app = tmp_path / "app" / "controllers"
        app.mkdir(parents=True)
        config = tmp_path / "config"
        config.mkdir()
        (app / "payments_controller.rb").write_text(
            "class PaymentsController < ApplicationController\n"
            "  def process\n"
            "    amount = params[:amount]\n"
            "    if amount.blank?\n"
            "      render json: { error: params[:tenant_id] }, status: :bad_request\n"
            "      return\n"
            "    end\n"
            "    render json: { ok: true }\n"
            "  end\n"
            "end\n",
            encoding="utf-8",
        )
        (config / "routes.rb").write_text(
            "Rails.application.routes.draw do\n"
            "  post '/payments/:tenant_id', to: 'payments#process'\n"
            "end\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,app/controllers/payments_controller.rb:2-10,process,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "process"
        assert entry["input_hints"] == ["amount", "tenant_id"]

    async def test_rails_strong_parameters_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        app = tmp_path / "app" / "controllers"
        app.mkdir(parents=True)
        config = tmp_path / "config"
        config.mkdir()
        (app / "payments_controller.rb").write_text(
            "class PaymentsController < ApplicationController\n"
            "  def create\n"
            "    attrs = params.require(:payment).permit(:amount, :currency)\n"
            "    if attrs[:amount].blank?\n"
            "      render json: { error: params[:tenant_id] }, status: :bad_request\n"
            "      return\n"
            "    end\n"
            "    render json: { ok: true }\n"
            "  end\n"
            "end\n",
            encoding="utf-8",
        )
        (config / "routes.rb").write_text(
            "Rails.application.routes.draw do\n"
            "  post '/tenants/:tenant_id/payments', to: 'payments#create'\n"
            "end\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,app/controllers/payments_controller.rb:2-9,create,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "create"
        assert entry["input_hints"] == ["payment", "amount", "currency", "tenant_id"]

    async def test_laravel_route_table_action_is_black_box_route_with_request_input(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        controller = tmp_path / "app" / "Http" / "Controllers"
        controller.mkdir(parents=True)
        routes = tmp_path / "routes"
        routes.mkdir()
        (controller / "PaymentController.php").write_text(
            "<?php\n"
            "class PaymentController {\n"
            "  public function process(Request $request) {\n"
            "    $amount = $request->input('amount');\n"
            "    if (!$amount) {\n"
            "      return response()->json(['error' => $request->route('tenantId')], 400);\n"
            "    }\n"
            "    return response()->json(['ok' => true]);\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        (routes / "web.php").write_text(
            "<?php\n"
            "Route::post('/payments/{tenantId}', [PaymentController::class, 'process']);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,app/Http/Controllers/PaymentController.php:3-9,process,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "process"
        assert entry["input_hints"] == ["amount", "tenantId"]

    async def test_laravel_typed_request_accessors_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        controller = tmp_path / "app" / "Http" / "Controllers"
        controller.mkdir(parents=True)
        routes = tmp_path / "routes"
        routes.mkdir()
        (controller / "PaymentController.php").write_text(
            "<?php\n"
            "class PaymentController {\n"
            "  public function process(Request $request) {\n"
            "    $amount = $request->integer('amount');\n"
            "    $draft = $request->boolean('draft');\n"
            "    $receipt = $request->file('receipt');\n"
            "    $tenant = $request->validated('tenant_id');\n"
            "    if ($amount <= 0 || !$receipt) {\n"
            "      return response()->json(['error' => $draft], 400);\n"
            "    }\n"
            "    return response()->json(['tenant' => $tenant]);\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        (routes / "web.php").write_text(
            "<?php\n"
            "Route::post('/payments/{tenantId}', [PaymentController::class, 'process']);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,app/Http/Controllers/PaymentController.php:3-12,process,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        process_entries = [
            item for item in gap["entry_paths"] if item.get("entry_symbol") == "process"
        ]
        assert process_entries
        entry = process_entries[0]
        assert entry["entry_kind"] == "route"
        assert entry["input_hints"] == [
            "amount", "draft", "receipt", "tenant_id", "tenantId",
        ]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text
        assert "draft" in case_text
        assert "receipt" in case_text
        assert "tenant_id" in case_text
        assert "tenantId" in case_text
        assert "integer" not in case_text
        assert "boolean" not in case_text

    async def test_laravel_route_table_without_path_params_keeps_controller_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        controller = tmp_path / "app" / "Http" / "Controllers"
        controller.mkdir(parents=True)
        routes = tmp_path / "routes"
        routes.mkdir()
        (controller / "PaymentController.php").write_text(
            "<?php\n"
            "class PaymentController {\n"
            "  public function process(Request $request) {\n"
            "    $amount = $request->input('amount');\n"
            "    $currency = $request->query('currency');\n"
            "    if (!$amount || !$currency) {\n"
            "      return response()->json(['error' => 'missing'], 400);\n"
            "    }\n"
            "    return response()->json(['ok' => true]);\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        (routes / "web.php").write_text(
            "<?php\n"
            "Route::post('/payments', [PaymentController::class, 'process']);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,app/Http/Controllers/PaymentController.php:3-10,process,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        process_entries = [
            item for item in gap["entry_paths"] if item.get("entry_symbol") == "process"
        ]
        assert process_entries
        entry = process_entries[0]
        assert entry["entry_kind"] == "route"
        assert entry["external_trigger"] == "POST /payments"
        assert entry["input_hints"] == ["amount", "currency"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text
        assert "currency" in case_text

    async def test_coverage_source_window_reads_vue_component_script(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src" / "components"
        src.mkdir(parents=True)
        (src / "PaymentWidget.vue").write_text(
            "<script setup lang=\"ts\">\n"
            "function processPayment(amount: number) {\n"
            "  if (!amount) {\n"
            "    return 'missing'\n"
            "  }\n"
            "  return 'ok'\n"
            "}\n"
            "</script>\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,component,src/components/PaymentWidget.vue:2-7,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["source_window"]["available"] is True
        assert gap["source_window"]["path"] == "src/components/PaymentWidget.vue"
        assert "function processPayment" in gap["source_window"]["text"]

    async def test_coverage_source_window_reads_astro_component_frontmatter(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src" / "pages"
        src.mkdir(parents=True)
        (src / "PaymentPage.astro").write_text(
            "---\n"
            "function processPayment(amount: number) {\n"
            "  if (!amount) {\n"
            "    return 'missing'\n"
            "  }\n"
            "  return 'ok'\n"
            "}\n"
            "---\n"
            "<main />\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,page,src/pages/PaymentPage.astro:2-7,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["source_window"]["available"] is True
        assert gap["source_window"]["path"] == "src/pages/PaymentPage.astro"
        assert "function processPayment" in gap["source_window"]["text"]

    async def test_django_urlpattern_view_is_black_box_route_with_query_input(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        app = tmp_path / "payments"
        app.mkdir()
        (app / "views.py").write_text(
            "def process_payment(request, tenant_id):\n"
            "    amount = request.GET.get('amount')\n"
            "    if not amount:\n"
            "        return JsonResponse({'error': tenant_id}, status=400)\n"
            "    return JsonResponse({'ok': True})\n",
            encoding="utf-8",
        )
        (app / "urls.py").write_text(
            "from django.urls import path\n"
            "from .views import process_payment\n\n"
            "urlpatterns = [\n"
            "    path('payments/<str:tenant_id>/', process_payment, name='process-payment'),\n"
            "]\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,views,payments/views.py:1-5,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "process_payment"
        assert entry["input_hints"] == ["amount", "tenant_id"]

    async def test_django_class_based_view_unqualified_method_is_black_box_route(
        self,
        tmp_path,
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        app = tmp_path / "payments"
        app.mkdir()
        (app / "views.py").write_text(
            "from django.views import View\n"
            "from django.http import JsonResponse\n\n"
            "class PaymentView(View):\n"
            "    def post(self, request, tenant_id):\n"
            "        amount = request.POST.get('amount')\n"
            "        if not amount:\n"
            "            return JsonResponse({'error': tenant_id}, status=400)\n"
            "        return JsonResponse({'ok': True})\n",
            encoding="utf-8",
        )
        (app / "urls.py").write_text(
            "from django.urls import path\n"
            "from .views import PaymentView\n\n"
            "urlpatterns = [\n"
            "    path('payments/<str:tenant_id>/', PaymentView.as_view(), name='payment'),\n"
            "]\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,views,payments/views.py:5-9,post,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "PaymentView.post"
        assert entry["external_trigger"] == "POST /payments/<str:tenant_id>/"
        assert entry["input_hints"] == ["amount", "tenant_id"]
        assert "PaymentView.as_view" in entry["evidence"]

    async def test_drf_serializer_fields_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        app = tmp_path / "payments"
        app.mkdir()
        (app / "views.py").write_text(
            "from rest_framework import serializers\n\n"
            "class PaymentSerializer(serializers.Serializer):\n"
            "    amount = serializers.IntegerField()\n"
            "    currency = serializers.CharField(required=False)\n\n"
            "def process_payment(request, tenant_id):\n"
            "    serializer = PaymentSerializer(data=request.data)\n"
            "    if not serializer.is_valid():\n"
            "        return Response({'error': tenant_id}, status=400)\n"
            "    return Response({'ok': True})\n",
            encoding="utf-8",
        )
        (app / "urls.py").write_text(
            "from django.urls import path\n"
            "from .views import process_payment\n\n"
            "urlpatterns = [\n"
            "    path('payments/<str:tenant_id>/', process_payment, name='process-payment'),\n"
            "]\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,views,payments/views.py:7-11,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "process_payment"
        assert entry["input_hints"] == ["amount", "currency", "tenant_id"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text
        assert "currency" in case_text

    async def test_starlette_route_table_is_black_box_route_with_path_and_method(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        app = tmp_path / "payments"
        app.mkdir()
        (app / "views.py").write_text(
            "async def process_payment(request):\n"
            "    tenant_id = request.path_params['tenant_id']\n"
            "    amount = request.query_params.get('amount')\n"
            "    if not amount:\n"
            "        return JSONResponse({'error': tenant_id}, status_code=400)\n"
            "    return JSONResponse({'ok': True})\n",
            encoding="utf-8",
        )
        (app / "routes.py").write_text(
            "from starlette.routing import Route\n"
            "from .views import process_payment\n\n"
            "routes = [\n"
            "    Route('/payments/{tenant_id}', process_payment, methods=['POST']),\n"
            "]\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,views,payments/views.py:1-6,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "process_payment"
        assert entry["entry_file"] == "payments/views.py"
        assert entry["external_trigger"] == "POST /payments/{tenant_id}"
        assert entry["input_hints"] == ["tenant_id", "amount"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "POST /payments/{tenant_id}" in case_text

    async def test_coverage_definition_detection_handles_objc_methods(self):
        from app.services.coverage_analyzer import _match_def_name

        assert _match_def_name(
            "- (void)processPayment:(PaymentRequest *)request {"
        ) == "processPayment"
        assert _match_def_name(
            "[service processPayment:request];"
        ) is None

    async def test_branch_condition_extraction_handles_unparenthesized_guards(self):
        from app.services.coverage_analyzer import _extract_branch_condition

        assert _extract_branch_condition("    if not amount:") == "if (not amount)"
        assert _extract_branch_condition(
            "guard request.isValid else { return .badRequest }"
        ) == "guard (request.isValid)"
        assert _extract_branch_condition("return nil unless request") == "unless (request)"

    async def test_decorated_route_function_is_black_box_entry_without_caller(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "routes.py").write_text(
            "@app.post('/payments')\n"
            "def process_payment(request):\n"
            "    if not request:\n"
            "        return 'missing'\n"
            "    return 'processed'\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,routes,src/routes.py:2-5,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "process_payment"
        assert entry["tool"] == "source-decorator"
        assert "@app.post" in entry["evidence"]
        assert gap["black_box_cases"][0]["case_type"] == "black_box_ready"

    async def test_decorated_error_handler_is_black_box_api_without_agent(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "errors.py").write_text(
            "@app.errorhandler(404)\n"
            "def not_found(error):\n"
            "    if error is None:\n"
            "        return 'missing', 404\n"
            "    return 'not found', 404\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "errors,errors,src/errors.py:2-5,not_found,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "api"
        assert entry["entry_symbol"] == "not_found"
        assert "@app.errorhandler" in entry["evidence"]
        assert "404" in entry["input_hints"]

    async def test_fastapi_pydantic_body_model_fields_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "routes.py").write_text(
            "from pydantic import BaseModel\n\n"
            "class PaymentRequest(BaseModel):\n"
            "    amount: int\n"
            "    currency: str = 'USD'\n\n"
            "@app.post('/tenants/{tenant_id}/payments')\n"
            "def create_payment(payload: PaymentRequest, tenant_id: str):\n"
            "    if payload.amount <= 0:\n"
            "        return {'status': 400}\n"
            "    return {'status': 200, 'tenant_id': tenant_id}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,routes,src/routes.py:8-11,create_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "create_payment"
        assert entry["input_hints"] == ["amount", "currency", "tenant_id"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text
        assert "currency" in case_text

    async def test_nestjs_controller_decorator_feeds_route_and_dto_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.controller.ts").write_text(
            "import { Body, Controller, Param, Post } from '@nestjs/common';\n\n"
            "class CreatePaymentDto {\n"
            "  amount: number;\n"
            "  currency: string;\n"
            "}\n\n"
            "@Controller('tenants/:tenantId/payments')\n"
            "export class PaymentsController {\n"
            "  @Post('process')\n"
            "  processPayment(\n"
            "    @Param('tenantId') tenantId: string,\n"
            "    @Body() payload: CreatePaymentDto,\n"
            "  ) {\n"
            "    if (!payload.amount) {\n"
            "      return { status: 'missing' };\n"
            "    }\n"
            "    return { tenantId };\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/payments.controller.ts:11-19,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "processPayment"
        assert entry["external_trigger"] == "POST /tenants/:tenantId/payments/process"
        assert entry["input_hints"] == ["amount", "tenantId", "currency"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "tenantId" in case_text
        assert "amount" in case_text
        assert "currency" in case_text

    async def test_coverage_line_hit_without_function_name_infers_enclosing_route(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "routes.py").write_text(
            "from fastapi import APIRouter\n\n"
            "router = APIRouter()\n\n"
            "@router.post('/payments/{tenant_id}/process')\n"
            "def process_payment(tenant_id: str, payload: dict):\n"
            "    amount = payload.get('amount')\n"
            "    if not amount:\n"
            "        return {'status': 'missing'}\n"
            "    return {'tenant_id': tenant_id, 'amount': amount}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,routes,src/routes.py:8,,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["function_name"] == "process_payment"
        assert gap["source_window"]["path"] == "src/routes.py"
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["external_trigger"] == "POST /payments/{tenant_id}/process"
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "tenant_id" in case_text
        assert "amount" in case_text

    async def test_coverage_top_level_line_without_function_name_does_not_infer_next_route(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "routes.py").write_text(
            "from fastapi import APIRouter\n\n"
            "router = APIRouter()\n\n"
            "@router.post('/payments/{tenant_id}/process')\n"
            "def process_payment(tenant_id: str, payload: dict):\n"
            "    amount = payload.get('amount')\n"
            "    return {'tenant_id': tenant_id, 'amount': amount}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,routes,src/routes.py:1,,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["function_name"] == ""
        assert gap["entry_paths"] == []
        assert gap["black_box_readiness"]["case_type"] != "black_box_ready"

    async def test_coverage_line_between_functions_without_name_does_not_infer_previous_route(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "routes.py").write_text(
            "from fastapi import APIRouter\n\n"
            "router = APIRouter()\n\n"
            "@router.get('/health')\n"
            "def health_check():\n"
            "    return {'ok': True}\n\n"
            "MODULE_READY = True\n\n"
            "@router.post('/payments/{tenant_id}/process')\n"
            "def process_payment(tenant_id: str, payload: dict):\n"
            "    amount = payload.get('amount')\n"
            "    return {'tenant_id': tenant_id, 'amount': amount}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,routes,src/routes.py:9,,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["function_name"] == ""
        assert gap["entry_paths"] == []
        assert gap["black_box_readiness"]["case_type"] != "black_box_ready"

    async def test_coverage_c_line_after_function_without_name_does_not_infer_previous_function(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "service.c").write_text(
            "int recover_service(void) {\n"
            "    return 0;\n"
            "}\n\n"
            "static int module_ready = 1;\n\n"
            "int process_payment(void) {\n"
            "    return module_ready;\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,service,src/service.c:5,,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["function_name"] == ""
        assert gap["entry_paths"] == []
        assert gap["black_box_readiness"]["case_type"] != "black_box_ready"

    async def test_coverage_c_multiline_signature_line_without_name_infers_function(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "service.c").write_text(
            "int process_payment(\n"
            "    int tenant_id,\n"
            "    int amount\n"
            ") {\n"
            "    if (amount <= 0) {\n"
            "        return -1;\n"
            "    }\n"
            "    return tenant_id;\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,service,src/service.c:2,,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["function_name"] == "process_payment"
        assert gap["source_window"]["path"] == "src/service.c"
        assert "amount <= 0" in json.dumps(gap["trigger_branches"], ensure_ascii=False)

    async def test_coverage_decorator_line_without_function_name_infers_next_route(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "routes.py").write_text(
            "from fastapi import APIRouter\n\n"
            "router = APIRouter()\n\n"
            "@router.post('/payments/{tenant_id}/process')\n"
            "def process_payment(tenant_id: str, payload: dict):\n"
            "    amount = payload.get('amount')\n"
            "    return {'tenant_id': tenant_id, 'amount': amount}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,routes,src/routes.py:5,,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["function_name"] == "process_payment"
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["external_trigger"] == "POST /payments/{tenant_id}/process"

    async def test_coverage_multiline_signature_line_without_function_name_infers_route(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.controller.ts").write_text(
            "import { Body, Controller, Param, Post } from '@nestjs/common';\n\n"
            "class CreatePaymentDto {\n"
            "  amount: number;\n"
            "}\n\n"
            "@Controller('tenants/:tenantId/payments')\n"
            "export class PaymentsController {\n"
            "  @Post('process')\n"
            "  processPayment(\n"
            "    @Param('tenantId') tenantId: string,\n"
            "    @Body() payload: CreatePaymentDto,\n"
            "  ) {\n"
            "    return { tenantId, amount: payload.amount };\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/payments.controller.ts:12,,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["function_name"] == "processPayment"
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["external_trigger"] == "POST /tenants/:tenantId/payments/process"

    async def test_coverage_multiline_arrow_signature_without_function_name_infers_route(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "routes.ts").write_text(
            "import express from 'express';\n\n"
            "export const processPayment = (\n"
            "  req: Request,\n"
            "  res: Response,\n"
            ") => {\n"
            "  const amount = req.body.amount;\n"
            "  return res.json({ amount });\n"
            "};\n\n"
            "const app = express();\n"
            "app.post('/payments/:tenantId/process', processPayment);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,routes,src/routes.ts:4,,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["function_name"] == "processPayment"
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["external_trigger"] == "POST /payments/:tenantId/process"
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text

    async def test_coverage_class_field_arrow_without_function_name_infers_route(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "routes.ts").write_text(
            "import express from 'express';\n\n"
            "class PaymentController {\n"
            "  processPayment = (\n"
            "    req: Request,\n"
            "    res: Response,\n"
            "  ) => {\n"
            "    const amount = req.body.amount;\n"
            "    return res.json({ amount });\n"
            "  };\n"
            "}\n\n"
            "const app = express();\n"
            "const controller = new PaymentController();\n"
            "app.post('/payments/:tenantId/process', controller.processPayment);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,routes,src/routes.ts:5,,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["function_name"] == "processPayment"
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["external_trigger"] == "POST /payments/:tenantId/process"
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text

    async def test_coverage_bound_class_field_arrow_without_function_name_infers_route(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "routes.ts").write_text(
            "import express from 'express';\n\n"
            "class PaymentController {\n"
            "  processPayment = (\n"
            "    req: Request,\n"
            "    res: Response,\n"
            "  ) => {\n"
            "    const amount = req.body.amount;\n"
            "    return res.json({ amount });\n"
            "  };\n"
            "}\n\n"
            "const app = express();\n"
            "const controller = new PaymentController();\n"
            "app.post('/payments/:tenantId/process', controller.processPayment.bind(controller));\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,routes,src/routes.ts:5,,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["function_name"] == "processPayment"
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "processPayment"
        assert entry["external_trigger"] == "POST /payments/:tenantId/process"
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text

    async def test_coverage_anonymous_default_function_without_name_is_not_named_function(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "handler.ts").write_text(
            "export default function (\n"
            "  req: Request,\n"
            "  res: Response,\n"
            ") {\n"
            "  const amount = req.body.amount;\n"
            "  return res.json({ amount });\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,handler,src/handler.ts:5,,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["function_name"] == ""
        assert gap["entry_paths"] == []
        assert gap["black_box_readiness"]["case_type"] != "black_box_ready"

    async def test_fastapi_pydantic_field_aliases_feed_external_body_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "routes.py").write_text(
            "from pydantic import BaseModel, Field\n\n"
            "class PaymentRequest(BaseModel):\n"
            "    amount: int = Field(alias='amount_cents')\n"
            "    currency: str = Field(default='USD', alias='currency_code')\n\n"
            "@app.post('/payments')\n"
            "def create_payment(payload: PaymentRequest):\n"
            "    if payload.amount <= 0:\n"
            "        return {'status': 400}\n"
            "    return {'status': 200}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,routes,src/routes.py:8-11,create_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["input_hints"] == ["amount_cents", "currency_code"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount_cents" in case_text
        assert "currency_code" in case_text
        assert "payload.amount" not in case_text

    async def test_fastapi_query_path_aliases_feed_external_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "routes.py").write_text(
            "from fastapi import Path, Query\n\n"
            "@app.post('/customers/{customerId}/payments')\n"
            "def create_payment(\n"
            "    customer_id: str = Path(alias='customerId'),\n"
            "    amount: int = Query(alias='amount_cents'),\n"
            "):\n"
            "    if amount <= 0:\n"
            "        return {'status': 400}\n"
            "    return {'status': 200, 'customer_id': customer_id}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,routes,src/routes.py:4-10,create_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["external_trigger"] == "POST /customers/{customerId}/payments"
        assert entry["input_hints"] == ["customerId", "amount_cents"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "customerId" in case_text
        assert "amount_cents" in case_text
        assert "customer_id" not in case_text

    async def test_fastapi_router_prefix_feeds_black_box_trigger_and_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "routes.py").write_text(
            "from fastapi import APIRouter\n\n"
            "tenant_router = APIRouter(prefix='/tenants/{tenant_id}')\n\n"
            "@tenant_router.post('/payments/{payment_id}')\n"
            "def update_payment(payment_id: str, payload: dict):\n"
            "    if not payload.get('amount'):\n"
            "        return {'status': 400}\n"
            "    return {'status': 200, 'payment_id': payment_id}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,routes,src/routes.py:6-9,update_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["external_trigger"] == "POST /tenants/{tenant_id}/payments/{payment_id}"
        assert "tenant_id" in entry["input_hints"]
        assert "payment_id" in entry["input_hints"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "/tenants/{tenant_id}/payments/{payment_id}" in case_text
        assert "tenant_id" in case_text
        assert "payment_id" in case_text

    async def test_fastapi_include_router_prefix_feeds_black_box_trigger(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.py").write_text(
            "from fastapi import APIRouter\n\n"
            "router = APIRouter()\n\n"
            "@router.post('/payments/{payment_id}')\n"
            "def update_payment(payment_id: str, payload: dict):\n"
            "    if not payload.get('amount'):\n"
            "        return {'status': 400}\n"
            "    return {'status': 200, 'payment_id': payment_id}\n",
            encoding="utf-8",
        )
        (src / "main.py").write_text(
            "from payments import router\n\n"
            "app.include_router(router, prefix='/tenants/{tenant_id}')\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.py:6-9,update_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["external_trigger"] == "POST /tenants/{tenant_id}/payments/{payment_id}"
        assert "tenant_id" in entry["input_hints"]
        assert "payment_id" in entry["input_hints"]

    async def test_fastapi_include_router_alias_prefix_feeds_black_box_trigger(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.py").write_text(
            "from fastapi import APIRouter\n\n"
            "router = APIRouter()\n\n"
            "@router.post('/payments/{payment_id}')\n"
            "def update_payment(payment_id: str, payload: dict):\n"
            "    if not payload.get('amount'):\n"
            "        return {'status': 400}\n"
            "    return {'status': 200, 'payment_id': payment_id}\n",
            encoding="utf-8",
        )
        (src / "main.py").write_text(
            "from payments import router as payment_router\n\n"
            "app.include_router(\n"
            "    payment_router,\n"
            "    prefix='/tenants/{tenant_id}',\n"
            ")\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.py:6-9,update_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        entry = gap["entry_paths"][0]
        assert entry["external_trigger"] == "POST /tenants/{tenant_id}/payments/{payment_id}"
        assert "tenant_id" in entry["input_hints"]
        assert "payment_id" in entry["input_hints"]

    async def test_fastapi_api_route_methods_array_feeds_black_box_trigger(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.py").write_text(
            "from fastapi import APIRouter\n\n"
            "router = APIRouter(prefix='/tenants/{tenant_id}')\n\n"
            "@router.api_route('/payments/{payment_id}', methods=['POST'])\n"
            "def upsert_payment(payment_id: str, payload: dict):\n"
            "    amount = payload.get('amount')\n"
            "    if not amount:\n"
            "        return {'status': 400}\n"
            "    return {'status': 200, 'payment_id': payment_id}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.py:6-10,upsert_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["external_trigger"] == "POST /tenants/{tenant_id}/payments/{payment_id}"
        assert "tenant_id" in entry["input_hints"]
        assert "payment_id" in entry["input_hints"]
        assert "amount" in entry["input_hints"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "POST /tenants/{tenant_id}/payments/{payment_id}" in case_text

    async def test_flask_register_blueprint_prefix_feeds_black_box_trigger(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.py").write_text(
            "from flask import Blueprint, request\n\n"
            "bp = Blueprint('payments', __name__)\n\n"
            "@bp.route('/payments/<payment_id>', methods=['POST'])\n"
            "def update_payment(payment_id):\n"
            "    amount = request.json['amount']\n"
            "    if not amount:\n"
            "        return {'status': 400}\n"
            "    return {'status': 200, 'payment_id': payment_id}\n",
            encoding="utf-8",
        )
        (src / "app.py").write_text(
            "from payments import bp as payments_bp\n\n"
            "app.register_blueprint(\n"
            "    payments_bp,\n"
            "    url_prefix='/tenants/<tenant_id>',\n"
            ")\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.py:6-10,update_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["external_trigger"] == "POST /tenants/<tenant_id>/payments/<payment_id>"
        assert "tenant_id" in entry["input_hints"]
        assert "payment_id" in entry["input_hints"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "/tenants/<tenant_id>/payments/<payment_id>" in case_text

    async def test_route_methods_tuple_feeds_black_box_trigger_method(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.py").write_text(
            "from flask import Flask, request\n\n"
            "app = Flask(__name__)\n\n"
            "@app.route('/payments/<payment_id>', methods=('POST',))\n"
            "def update_payment(payment_id):\n"
            "    amount = request.json['amount']\n"
            "    if not amount:\n"
            "        return {'status': 400}\n"
            "    return {'status': 200, 'payment_id': payment_id}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.py:6-10,update_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["external_trigger"] == "POST /payments/<payment_id>"
        assert "payment_id" in entry["input_hints"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "POST /payments/<payment_id>" in case_text

    async def test_decorated_route_path_params_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "routes.py").write_text(
            "@app.get('/accounts/{account_id}/payments/:payment_id')\n"
            "def get_payment():\n"
            "    if not load_payment():\n"
            "        return 'missing'\n"
            "    return 'ok'\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,routes,src/routes.py:2-5,get_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["external_trigger"] == "GET /accounts/{account_id}/payments/:payment_id"
        assert entry["input_hints"] == ["account_id", "payment_id"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "GET /accounts/{account_id}/payments/:payment_id" in case_text
        assert "account_id" in case_text
        assert "payment_id" in case_text

    async def test_decorated_websocket_function_is_black_box_route(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "ws.py").write_text(
            "@app.websocket('/ws/{client_id}')\n"
            "async def stream_updates(websocket, client_id):\n"
            "    if not client_id:\n"
            "        await websocket.close()\n"
            "    return client_id\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "updates,ws,src/ws.py:2-5,stream_updates,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "stream_updates"
        assert entry["tool"] == "source-decorator"
        assert "@app.websocket" in entry["evidence"]
        assert "client_id" in entry["input_hints"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "client_id" in case_text

    async def test_spring_mapping_annotation_is_black_box_route_without_caller(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentController.java").write_text(
            "class PaymentController {\n"
            "  @PostMapping(\"/payments\")\n"
            "  public Response processPayment(PaymentRequest request) {\n"
            "    if (request == null) {\n"
            "      return Response.badRequest().build();\n"
            "    }\n"
            "    return Response.ok().build();\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/PaymentController.java:3-8,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "processPayment"
        assert entry["tool"] == "source-decorator"
        assert "@PostMapping" in entry["evidence"]

    async def test_spring_controller_prefix_feeds_black_box_trigger_and_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentController.java").write_text(
            "@RequestMapping(\"/tenants/{tenantId}\")\n"
            "class PaymentController {\n"
            "  @PostMapping(\"/payments\")\n"
            "  public Response processPayment(PaymentRequest request, String tenantId) {\n"
            "    if (request == null) {\n"
            "      return Response.badRequest().build();\n"
            "    }\n"
            "    return Response.ok().build();\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/PaymentController.java:4-9,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["external_trigger"] == "POST /tenants/{tenantId}/payments"
        assert "tenantId" in entry["input_hints"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "POST /tenants/{tenantId}/payments" in case_text

    async def test_aspnet_http_attribute_is_black_box_route_without_caller(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentController.cs").write_text(
            "public class PaymentController {\n"
            "  [HttpPost(\"/payments\")]\n"
            "  public IActionResult ProcessPayment(PaymentRequest request) {\n"
            "    if (request == null) {\n"
            "      return BadRequest();\n"
            "    }\n"
            "    return Ok();\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/PaymentController.cs:3-8,ProcessPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "ProcessPayment"
        assert entry["tool"] == "source-decorator"
        assert "[HttpPost" in entry["evidence"]

    async def test_aspnet_body_dto_fields_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentController.cs").write_text(
            "public class PaymentController {\n"
            "  [HttpPost(\"/tenants/{tenantId}/payments\")]\n"
            "  public IActionResult ProcessPayment(\n"
            "      [FromBody] PaymentRequest request,\n"
            "      string tenantId) {\n"
            "    if (request.Amount <= 0) {\n"
            "      return BadRequest();\n"
            "    }\n"
            "    return Ok();\n"
            "  }\n"
            "}\n\n"
            "public class PaymentRequest {\n"
            "  public decimal Amount { get; set; }\n"
            "  public string Currency { get; set; }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/PaymentController.cs:3-10,ProcessPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "ProcessPayment"
        assert entry["input_hints"] == ["Amount", "Currency", "tenantId"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "Amount" in case_text
        assert "Currency" in case_text

    async def test_aspnet_minimal_api_method_group_is_black_box_route(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "Program.cs").write_text(
            "public class Program {\n"
            "  public static void Main() {\n"
            "    var app = WebApplication.Create();\n"
            "    app.MapPost(\"/tenants/{tenantId}/payments\", ProcessPayment);\n"
            "  }\n"
            "  public static IResult ProcessPayment([FromBody] PaymentRequest request, string tenantId) {\n"
            "    if (request.Amount <= 0) {\n"
            "      return Results.BadRequest();\n"
            "    }\n"
            "    return Results.Ok(request.Currency + tenantId);\n"
            "  }\n"
            "}\n\n"
            "public class PaymentRequest {\n"
            "  public decimal Amount { get; set; }\n"
            "  public string Currency { get; set; }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,minimal-api,src/Program.cs:6-11,ProcessPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        process_entries = [
            item for item in gap["entry_paths"] if item.get("entry_symbol") == "ProcessPayment"
        ]
        assert process_entries
        entry = process_entries[0]
        assert entry["entry_kind"] == "route"
        assert entry["external_trigger"] == "POST /tenants/{tenantId}/payments"
        assert entry["input_hints"] == ["Amount", "Currency", "tenantId"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "POST /tenants/{tenantId}/payments" in case_text
        assert "Amount" in case_text
        assert "Currency" in case_text

    async def test_aspnet_minimal_api_map_methods_preserves_http_method(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "Program.cs").write_text(
            "public class Program {\n"
            "  public static void Main() {\n"
            "    var app = WebApplication.Create();\n"
            "    app.MapMethods(\"/payments/{paymentId}/confirm\", new[] { \"POST\" }, ConfirmPayment);\n"
            "  }\n"
            "  public static IResult ConfirmPayment(string paymentId, [FromBody] ConfirmRequest request) {\n"
            "    if (request.Amount <= 0) {\n"
            "      return Results.BadRequest();\n"
            "    }\n"
            "    return Results.Ok(paymentId + request.Currency);\n"
            "  }\n"
            "}\n\n"
            "public class ConfirmRequest {\n"
            "  public decimal Amount { get; set; }\n"
            "  public string Currency { get; set; }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,minimal-api,src/Program.cs:6-11,ConfirmPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entries = [
            item for item in gap["entry_paths"] if item.get("entry_symbol") == "ConfirmPayment"
        ]
        assert entries
        entry = entries[0]
        assert entry["entry_kind"] == "route"
        assert entry["external_trigger"] == "POST /payments/{paymentId}/confirm"
        assert entry["input_hints"] == ["paymentId", "Amount", "Currency"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "POST /payments/{paymentId}/confirm" in case_text
        assert "paymentId" in case_text
        assert "Amount" in case_text

    async def test_aspnet_minimal_api_map_group_prefix_feeds_route_trigger(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "Program.cs").write_text(
            "public class Program {\n"
            "  public static void Main() {\n"
            "    var app = WebApplication.Create();\n"
            "    var payments = app.MapGroup(\"/tenants/{tenantId}\");\n"
            "    payments.MapPost(\"/payments\", ProcessPayment);\n"
            "  }\n"
            "  public static IResult ProcessPayment([FromBody] PaymentRequest request, string tenantId) {\n"
            "    if (request.Amount <= 0) {\n"
            "      return Results.BadRequest();\n"
            "    }\n"
            "    return Results.Ok(request.Currency + tenantId);\n"
            "  }\n"
            "}\n\n"
            "public class PaymentRequest {\n"
            "  public decimal Amount { get; set; }\n"
            "  public string Currency { get; set; }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,minimal-api,src/Program.cs:7-12,ProcessPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entries = [
            item for item in gap["entry_paths"] if item.get("entry_symbol") == "ProcessPayment"
        ]
        assert entries
        entry = entries[0]
        assert entry["entry_kind"] == "route"
        assert entry["external_trigger"] == "POST /tenants/{tenantId}/payments"
        assert entry["input_hints"] == ["tenantId", "Amount", "Currency"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "POST /tenants/{tenantId}/payments" in case_text
        assert "tenantId" in case_text
        assert "Amount" in case_text

    async def test_aspnet_request_collections_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentController.cs").write_text(
            "public class PaymentController {\n"
            "  [HttpPost(\"/payments\")]\n"
            "  public IActionResult ProcessPayment() {\n"
            "    var amount = Request.Query[\"amount\"];\n"
            "    var tenantId = Request.Headers[\"X-Tenant-Id\"];\n"
            "    var sessionId = Request.Cookies[\"session_id\"];\n"
            "    if (string.IsNullOrEmpty(amount)) {\n"
            "      return BadRequest();\n"
            "    }\n"
            "    return Ok(tenantId + sessionId);\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/PaymentController.cs:3-11,ProcessPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "ProcessPayment"
        assert entry["input_hints"] == ["amount", "X-Tenant-Id", "session_id"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text
        assert "X-Tenant-Id" in case_text
        assert "session_id" in case_text
        assert "Request.Query" not in case_text

    async def test_aspnet_request_try_get_value_feeds_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentController.cs").write_text(
            "public class PaymentController {\n"
            "  [HttpPost(\"/payments\")]\n"
            "  public IActionResult ProcessPayment() {\n"
            "    Request.Query.TryGetValue(\"amount\", out var amount);\n"
            "    Request.Headers.TryGetValue(\"X-Tenant-Id\", out var tenantId);\n"
            "    Request.Cookies.TryGetValue(\"session_id\", out var sessionId);\n"
            "    if (string.IsNullOrEmpty(amount)) {\n"
            "      return BadRequest();\n"
            "    }\n"
            "    return Ok(tenantId + sessionId);\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/PaymentController.cs:3-11,ProcessPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "ProcessPayment"
        assert entry["input_hints"] == ["amount", "X-Tenant-Id", "session_id"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text
        assert "X-Tenant-Id" in case_text
        assert "session_id" in case_text
        assert "TryGetValue" not in case_text

    async def test_aspnet_controller_token_route_feeds_black_box_trigger(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentController.cs").write_text(
            "[Route(\"api/[controller]\")]\n"
            "public class PaymentController {\n"
            "  [HttpPost(\"{tenantId}\")]\n"
            "  public IActionResult ProcessPayment([FromBody] PaymentRequest request, string tenantId) {\n"
            "    if (request.Amount <= 0) {\n"
            "      return BadRequest();\n"
            "    }\n"
            "    return Ok();\n"
            "  }\n"
            "}\n\n"
            "public class PaymentRequest {\n"
            "  public decimal Amount { get; set; }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/PaymentController.cs:4-9,ProcessPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["external_trigger"] == "POST /api/payment/{tenantId}"
        assert "tenantId" in entry["input_hints"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "POST /api/payment/{tenantId}" in case_text

    async def test_aspnet_action_token_route_without_method_path_is_black_box_trigger(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentController.cs").write_text(
            "[Route(\"api/[controller]/[action]\")]\n"
            "public class PaymentController {\n"
            "  [HttpPost]\n"
            "  public IActionResult ProcessPayment([FromBody] PaymentRequest request) {\n"
            "    if (request.Amount <= 0) {\n"
            "      return BadRequest();\n"
            "    }\n"
            "    return Ok();\n"
            "  }\n"
            "}\n\n"
            "public class PaymentRequest {\n"
            "  public decimal Amount { get; set; }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/PaymentController.cs:4-9,ProcessPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["external_trigger"] == "POST /api/payment/process-payment"
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "POST /api/payment/process-payment" in case_text

    async def test_aspnet_named_fromquery_feeds_external_input_hint(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentController.cs").write_text(
            "[Route(\"api/[controller]\")]\n"
            "public class PaymentController {\n"
            "  [HttpGet(\"search\")]\n"
            "  public IActionResult SearchPayments(\n"
            "      [FromQuery(Name = \"tenant_id\")] string tenantId,\n"
            "      [FromQuery(Name = \"currency\")] string ccy) {\n"
            "    if (string.IsNullOrEmpty(tenantId)) {\n"
            "      return BadRequest();\n"
            "    }\n"
            "    return Ok();\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/PaymentController.cs:4-11,SearchPayments,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["external_trigger"] == "GET /api/payment/search"
        assert entry["input_hints"] == ["tenant_id", "currency"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "tenant_id" in case_text
        assert "tenantId" not in case_text

    async def test_aspnet_named_fromcookie_feeds_external_input_hint(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentController.cs").write_text(
            "[Route(\"api/[controller]\")]\n"
            "public class PaymentController {\n"
            "  [HttpPost(\"confirm\")]\n"
            "  public IActionResult ConfirmPayment(\n"
            "      [FromHeader(Name = \"X-Trace-Id\")] string traceId,\n"
            "      [FromCookie(Name = \"session_id\")] string sessionId) {\n"
            "    if (string.IsNullOrEmpty(traceId) || string.IsNullOrEmpty(sessionId)) {\n"
            "      return BadRequest();\n"
            "    }\n"
            "    return Ok();\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/PaymentController.cs:4-10,ConfirmPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["external_trigger"] == "POST /api/payment/confirm"
        assert entry["input_hints"] == ["X-Trace-Id", "session_id"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "X-Trace-Id" in case_text
        assert "session_id" in case_text
        assert "sessionId" not in case_text

    async def test_nestjs_route_decorator_is_black_box_route_without_caller(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payment.controller.ts").write_text(
            "export class PaymentController {\n"
            "  @Post('/payments/:tenantId')\n"
            "  async processPayment(@Body() paymentRequest: PaymentRequest, @Param('tenantId') tenantId: string) {\n"
            "    if (!paymentRequest.amount) {\n"
            "      return { status: 400 };\n"
            "    }\n"
            "    return { status: 200, tenantId };\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/payment.controller.ts:3-8,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "processPayment"
        assert entry["tool"] == "source-decorator"
        assert "@Post" in entry["evidence"]
        assert entry["input_hints"] == ["paymentRequest", "tenantId"]

    async def test_nestjs_controller_prefix_feeds_black_box_trigger_and_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payment.controller.ts").write_text(
            "@Controller('/tenants/:tenantId')\n"
            "export class PaymentController {\n"
            "  @Post('/payments')\n"
            "  async processPayment(@Body() paymentRequest: PaymentRequest, @Param('tenantId') tenantId: string) {\n"
            "    if (!paymentRequest.amount) {\n"
            "      return { status: 400 };\n"
            "    }\n"
            "    return { status: 200, tenantId };\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/payment.controller.ts:4-9,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["external_trigger"] == "POST /tenants/:tenantId/payments"
        assert "tenantId" in entry["input_hints"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "POST /tenants/:tenantId/payments" in case_text

    async def test_nestjs_relative_controller_prefix_feeds_black_box_trigger(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payment.controller.ts").write_text(
            "@Controller('tenants/:tenantId')\n"
            "export class PaymentController {\n"
            "  @Post('payments')\n"
            "  async processPayment(@Body() paymentRequest: PaymentRequest, @Param('tenantId') tenantId: string) {\n"
            "    if (!paymentRequest.amount) {\n"
            "      return { status: 400 };\n"
            "    }\n"
            "    return { status: 200, tenantId };\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/payment.controller.ts:4-9,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["external_trigger"] == "POST /tenants/:tenantId/payments"
        assert "tenantId" in entry["input_hints"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "POST /tenants/:tenantId/payments" in case_text

    async def test_nestjs_body_dto_fields_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payment.controller.ts").write_text(
            "interface PaymentRequest {\n"
            "  amount: number;\n"
            "  currency?: string;\n"
            "}\n\n"
            "export class PaymentController {\n"
            "  @Post('/payments/:tenantId')\n"
            "  async processPayment(\n"
            "    @Body() paymentRequest: PaymentRequest,\n"
            "    @Param('tenantId') tenantId: string,\n"
            "  ) {\n"
            "    if (!paymentRequest.amount) {\n"
            "      return { status: 400 };\n"
            "    }\n"
            "    return { status: 200, tenantId };\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/payment.controller.ts:9-16,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "processPayment"
        assert entry["input_hints"] == ["amount", "currency", "tenantId"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text
        assert "currency" in case_text

    async def test_graphql_mutation_decorator_is_black_box_api_without_caller(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payment.resolver.ts").write_text(
            "export class PaymentResolver {\n"
            "  @Mutation(() => PaymentResult)\n"
            "  async processPayment(@Args('amount') amount: number, @Args('tenantId') tenantId: string) {\n"
            "    if (!amount) {\n"
            "      return { status: 'missing' };\n"
            "    }\n"
            "    return { status: 'ok', tenantId };\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,resolver,src/payment.resolver.ts:3-8,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "api"
        assert entry["entry_symbol"] == "processPayment"
        assert entry["tool"] == "source-decorator"
        assert "@Mutation" in entry["evidence"]
        assert entry["input_hints"] == ["amount", "tenantId"]

    async def test_graphql_resolver_map_becomes_black_box_api_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payment_service.ts").write_text(
            "export async function processPayment(args) {\n"
            "  if (!args.amount) {\n"
            "    return { status: 'missing' };\n"
            "  }\n"
            "  return { status: 'ok' };\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "schema.ts").write_text(
            "import { makeExecutableSchema } from '@graphql-tools/schema';\n"
            "import { processPayment } from './payment_service';\n\n"
            "const resolvers = {\n"
            "  Mutation: {\n"
            "    processPayment,\n"
            "  },\n"
            "};\n\n"
            "export const schema = makeExecutableSchema({ typeDefs, resolvers });\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,service,src/payment_service.ts:1-6,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "api"
        assert entry["entry_symbol"] == "processPayment"
        assert entry["tool"] == "source-graphql-schema"
        assert "Mutation" in entry["evidence"]

    async def test_typed_route_signature_params_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentController.cs").write_text(
            "public class PaymentController {\n"
            "  [HttpPost(\"/payments/{tenantId}\")]\n"
            "  public IActionResult ProcessPayment(PaymentRequest paymentRequest, string tenantId) {\n"
            "    if (paymentRequest == null) {\n"
            "      return BadRequest();\n"
            "    }\n"
            "    return Ok();\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/PaymentController.cs:3-8,ProcessPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["input_hints"] == ["paymentRequest", "tenantId"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "paymentRequest" in case_text
        assert "tenantId" in case_text

    async def test_request_body_type_feeds_black_box_input_hint_when_param_is_request(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentController.java").write_text(
            "public class PaymentController {\n"
            "  @PostMapping(\"/payments\")\n"
            "  public ResponseEntity<?> processPayment(@RequestBody PaymentRequest request) {\n"
            "    if (request == null) {\n"
            "      return ResponseEntity.badRequest().build();\n"
            "    }\n"
            "    return ResponseEntity.ok().build();\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/PaymentController.java:3-8,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["input_hints"] == ["PaymentRequest"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "PaymentRequest" in case_text
        assert '"request"' not in case_text

    async def test_spring_request_body_dto_fields_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentController.java").write_text(
            "public class PaymentController {\n"
            "  @PostMapping(\"/tenants/{tenantId}/payments\")\n"
            "  public ResponseEntity<?> processPayment(\n"
            "      @RequestBody PaymentRequest request,\n"
            "      @PathVariable String tenantId) {\n"
            "    if (request.amount == null) {\n"
            "      return ResponseEntity.badRequest().build();\n"
            "    }\n"
            "    return ResponseEntity.ok().build();\n"
            "  }\n"
            "}\n\n"
            "class PaymentRequest {\n"
            "  private BigDecimal amount;\n"
            "  private String currency;\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/PaymentController.java:3-8,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "route"
        assert entry["entry_symbol"] == "processPayment"
        assert entry["input_hints"] == ["amount", "currency", "tenantId"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text
        assert "currency" in case_text
        assert "tenantId" in case_text

    async def test_spring_named_request_param_feeds_external_input_hint(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentController.java").write_text(
            "@RequestMapping(\"/payments\")\n"
            "class PaymentController {\n"
            "  @PostMapping(\"/search\")\n"
            "  public Response processPayment(\n"
            "      @RequestParam(\"amount\") BigDecimal rawAmount,\n"
            "      @RequestParam(name = \"currency\") String ccy) {\n"
            "    if (rawAmount.signum() <= 0) {\n"
            "      return Response.badRequest().build();\n"
            "    }\n"
            "    return Response.ok().build();\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/PaymentController.java:4-10,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["external_trigger"] == "POST /payments/search"
        assert entry["input_hints"] == ["amount", "currency"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text
        assert "currency" in case_text
        assert "rawAmount" not in case_text

    async def test_spring_header_cookie_annotations_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentController.java").write_text(
            "@RequestMapping(\"/payments\")\n"
            "class PaymentController {\n"
            "  @PostMapping(\"/confirm\")\n"
            "  public Response processPayment(\n"
            "      @RequestHeader(\"X-Trace-Id\") String trace,\n"
            "      @CookieValue(name = \"session_id\") String sessionId,\n"
            "      @RequestParam(\"amount\") BigDecimal rawAmount) {\n"
            "    if (trace == null || sessionId == null || rawAmount.signum() <= 0) {\n"
            "      return Response.badRequest().build();\n"
            "    }\n"
            "    return Response.ok().build();\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/PaymentController.java:4-11,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["external_trigger"] == "POST /payments/confirm"
        assert entry["input_hints"] == ["X-Trace-Id", "session_id", "amount"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "X-Trace-Id" in case_text
        assert "session_id" in case_text
        assert "rawAmount" not in case_text

    async def test_spring_request_part_name_feeds_black_box_input_hint(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentController.java").write_text(
            "@RequestMapping(\"/payments\")\n"
            "class PaymentController {\n"
            "  @PostMapping(\"/upload\")\n"
            "  public Response processPayment(\n"
            "      @RequestPart(\"receipt\") MultipartFile file,\n"
            "      @RequestParam(\"amount\") BigDecimal rawAmount) {\n"
            "    if (file.isEmpty() || rawAmount.signum() <= 0) {\n"
            "      return Response.badRequest().build();\n"
            "    }\n"
            "    return Response.ok().build();\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,controller,src/PaymentController.java:4-10,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["external_trigger"] == "POST /payments/upload"
        assert entry["input_hints"] == ["receipt", "amount"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "receipt" in case_text
        assert "MultipartFile" not in case_text
        assert "rawAmount" not in case_text

    async def test_java_servlet_request_methods_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentService.java").write_text(
            "public final class PaymentService {\n"
            "  public String processPayment(String amount, String tenantId, Object receipt) {\n"
            "    if (amount == null) {\n"
            "      return \"missing\";\n"
            "    }\n"
            "    return \"processed\";\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "PaymentServlet.java").write_text(
            "import jakarta.servlet.http.HttpServlet;\n"
            "import jakarta.servlet.http.HttpServletRequest;\n"
            "import jakarta.servlet.http.HttpServletResponse;\n\n"
            "public final class PaymentServlet extends HttpServlet {\n"
            "  private final PaymentService service = new PaymentService();\n\n"
            "  protected void doPost(HttpServletRequest request, HttpServletResponse response) {\n"
            "    String amount = request.getParameter(\"amount\");\n"
            "    String tenantId = request.getHeader(\"X-Tenant-Id\");\n"
            "    Object receipt = request.getPart(\"receipt\");\n"
            "    service.processPayment(amount, tenantId, receipt);\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,service,src/PaymentService.java:2-7,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] in {"endpoint", "api", "route"}
        assert entry["entry_symbol"] == "doPost"
        assert entry["input_hints"] == ["amount", "X-Tenant-Id", "receipt"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "amount" in case_text
        assert "X-Tenant-Id" in case_text
        assert "receipt" in case_text
        assert "HttpServletRequest" not in case_text

    async def test_decorated_message_consumer_is_black_box_entry_without_caller(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "consumers.py").write_text(
            "@bus.subscribe('invoice.created')\n"
            "def consume_invoice(event):\n"
            "    if not event:\n"
            "        return 'missing'\n"
            "    return 'consumed'\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,consumers,src/consumers.py:2-5,consume_invoice,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "message"
        assert entry["entry_symbol"] == "consume_invoice"
        assert entry["tool"] == "source-decorator"
        assert "@bus.subscribe" in entry["evidence"]
        assert entry["input_hints"] == ["invoice.created", "event"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "invoice.created" in case_text

    async def test_multiline_java_message_listener_annotation_is_black_box_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "InvoiceConsumer.java").write_text(
            "public final class InvoiceConsumer {\n"
            "  @KafkaListener(\n"
            "    topics = \"invoice.created\",\n"
            "    groupId = \"billing-workers\"\n"
            "  )\n"
            "  public void consumeInvoice(PaymentEvent event) {\n"
            "    if (event == null) {\n"
            "      throw new IllegalArgumentException(\"missing event\");\n"
            "    }\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,consumer,src/InvoiceConsumer.java:6-10,consumeInvoice,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "message"
        assert entry["entry_symbol"] == "consumeInvoice"
        assert entry["tool"] == "source-decorator"
        assert "KafkaListener" in entry["evidence"]
        assert "invoice.created" in entry["input_hints"]
        assert "PaymentEvent" in entry["input_hints"]

    async def test_qualified_java_method_name_uses_leaf_for_entry_discovery(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "InvoiceConsumer.java").write_text(
            "public final class InvoiceConsumer {\n"
            "  @KafkaListener(topics = \"invoice.created\")\n"
            "  public void consumeInvoice(PaymentEvent event) {\n"
            "    if (event == null) {\n"
            "      throw new IllegalArgumentException(\"missing event\");\n"
            "    }\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,consumer,src/InvoiceConsumer.java:3-7,InvoiceConsumer.consumeInvoice,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "message"
        assert entry["entry_symbol"] == "consumeInvoice"
        assert entry["chain"] == ["consumeInvoice"]
        assert "invoice.created" in entry["input_hints"]

    async def test_kafkajs_consumer_registration_is_message_entry_with_topic(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "processor.ts").write_text(
            "export async function processInvoice(message) {\n"
            "  if (!message.value) {\n"
            "    return { status: 'missing' };\n"
            "  }\n"
            "  return { status: 'processed' };\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "consumer.ts").write_text(
            "import { processInvoice } from './processor';\n\n"
            "const consumer = kafka.consumer({ groupId: 'billing' });\n"
            "await consumer.subscribe({ topic: 'invoice.created' });\n"
            "await consumer.run({ eachMessage: processInvoice });\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,processor,src/processor.ts:1-6,processInvoice,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "message"
        assert entry["entry_symbol"] == "processInvoice"
        assert entry["tool"] == "source-kafka-consumer"
        assert entry["input_hints"] == ["invoice.created"]

    async def test_celery_task_decorator_is_black_box_job_without_caller(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "tasks.py").write_text(
            "@app.task(name='billing.process_invoice')\n"
            "def process_invoice(invoice_id):\n"
            "    if not invoice_id:\n"
            "        return 'missing'\n"
            "    return 'processed'\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,tasks,src/tasks.py:2-5,process_invoice,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "job"
        assert entry["entry_symbol"] == "process_invoice"
        assert entry["tool"] == "source-decorator"
        assert "@app.task" in entry["evidence"]
        assert entry["input_hints"] == ["billing.process_invoice", "invoice_id"]

    async def test_rq_job_decorator_is_black_box_job_with_queue_hint(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "jobs.py").write_text(
            "@job('invoice_queue')\n"
            "def process_invoice(invoice_id):\n"
            "    if not invoice_id:\n"
            "        return 'missing'\n"
            "    return 'processed'\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,jobs,src/jobs.py:2-5,process_invoice,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "job"
        assert entry["entry_symbol"] == "process_invoice"
        assert entry["tool"] == "source-decorator"
        assert "@job" in entry["evidence"]
        assert entry["input_hints"] == ["invoice_queue", "invoice_id"]

    async def test_queue_worker_call_site_keeps_queue_entry_kind_without_agent(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "notifications.py").write_text(
            "def send_receipt(event):\n"
            "    if not event:\n"
            "        return 'missing'\n"
            "    return 'sent'\n",
            encoding="utf-8",
        )
        (src / "queue_worker.py").write_text(
            "def invoice_queue_consumer(event):\n"
            "    return send_receipt(event)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,notifications,src/notifications.py:1-4,send_receipt,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["entry_kind"] == "queue"
        assert gap["entry_paths"][0]["entry_symbol"] == "invoice_queue_consumer"
        assert gap["entry_paths"][0]["input_hints"] == ["invoice_queue", "event"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "invoice_queue" in case_text

    async def test_bullmq_worker_registration_feeds_queue_input_hint(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "processor.ts").write_text(
            "export async function processInvoice(job) {\n"
            "  if (!job.data.invoiceId) {\n"
            "    return { status: 'missing' };\n"
            "  }\n"
            "  return { status: 'processed' };\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "worker.ts").write_text(
            "import { Worker } from 'bullmq';\n"
            "import { processInvoice } from './processor';\n\n"
            "export const invoiceWorker = new Worker('invoice_queue', processInvoice);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,processor,src/processor.ts:1-6,processInvoice,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "queue"
        assert entry["entry_symbol"] == "processInvoice"
        assert entry["input_hints"] == ["invoice_queue", "invoiceId"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "invoice_queue" in case_text
        assert "invoiceId" in case_text
        assert "job.data" not in case_text

    async def test_timer_call_site_keeps_timer_entry_kind_without_agent(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "sessions.py").write_text(
            "def renew_session(ctx):\n"
            "    if not ctx:\n"
            "        return 'missing'\n"
            "    return 'renewed'\n",
            encoding="utf-8",
        )
        (src / "timers.py").write_text(
            "def session_timer_tick(ctx):\n"
            "    return renew_session(ctx)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "session,sessions,src/sessions.py:1-4,renew_session,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["entry_kind"] == "timer"
        assert gap["entry_paths"][0]["entry_symbol"] == "session_timer_tick"
        assert gap["entry_paths"][0]["input_hints"] == ["session_timer"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "session_timer" in case_text

    async def test_js_set_interval_registration_is_timer_entry_without_agent(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "metrics.ts").write_text(
            "export function collectMetrics(sample) {\n"
            "  if (!sample) {\n"
            "    return 'missing';\n"
            "  }\n"
            "  return 'collected';\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "timers.ts").write_text(
            "import { collectMetrics } from './metrics';\n\n"
            "setInterval(collectMetrics, 30000);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "metrics,metrics,src/metrics.ts:1-6,collectMetrics,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "timer"
        assert entry["entry_symbol"] == "collectMetrics"
        assert "setInterval(collectMetrics, 30000)" in entry["evidence"]
        assert "30000" in entry["input_hints"]

    async def test_c_uv_timer_start_registration_is_timer_entry_without_agent(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "metrics_timer.c").write_text(
            "#include <uv.h>\n\n"
            "static void collect_metrics(uv_timer_t *timer) {\n"
            "    if (timer == NULL) {\n"
            "        return;\n"
            "    }\n"
            "    flush_metrics(timer);\n"
            "}\n\n"
            "void start_metrics_timer(uv_loop_t *loop) {\n"
            "    static uv_timer_t timer;\n"
            "    uv_timer_init(loop, &timer);\n"
            "    uv_timer_start(&timer, collect_metrics, 1000, 30000);\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "metrics,timer,src/metrics_timer.c:3-8,collect_metrics,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "timer"
        assert entry["entry_symbol"] == "collect_metrics"
        assert "uv_timer_start(&timer, collect_metrics, 1000, 30000)" in entry["evidence"]
        assert "1000" in entry["input_hints"]
        assert "30000" in entry["input_hints"]

    async def test_js_set_immediate_registration_is_callback_entry_without_agent(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "batches.ts").write_text(
            "export function flushBatch(batch) {\n"
            "  if (!batch) {\n"
            "    return 'missing';\n"
            "  }\n"
            "  return 'flushed';\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "runtime.ts").write_text(
            "import { flushBatch } from './batches';\n\n"
            "setImmediate(flushBatch);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "batches,batches,src/batches.ts:1-6,flushBatch,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "callback"
        assert entry["entry_symbol"] == "flushBatch"
        assert "setImmediate(flushBatch)" in entry["evidence"]
        assert entry["input_hints"] == ["batch"]

    async def test_c_uv_async_init_registration_is_callback_entry_without_agent(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "async_flush.c").write_text(
            "#include <uv.h>\n\n"
            "static void on_async_flush(uv_async_t *handle) {\n"
            "    if (handle == NULL) {\n"
            "        return;\n"
            "    }\n"
            "    flush_pending(handle);\n"
            "}\n\n"
            "void init_async_flush(uv_loop_t *loop) {\n"
            "    static uv_async_t async_handle;\n"
            "    uv_async_init(loop, &async_handle, on_async_flush);\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "flush,async,src/async_flush.c:3-8,on_async_flush,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "callback"
        assert entry["entry_symbol"] == "on_async_flush"
        assert "uv_async_init(loop, &async_handle, on_async_flush)" in entry["evidence"]

    async def test_c_libevent_event_new_registration_is_callback_entry_without_agent(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "event_loop.c").write_text(
            "#include <event2/event.h>\n\n"
            "static void on_socket_ready(evutil_socket_t fd, short events, void *ctx) {\n"
            "    if (ctx == NULL) {\n"
            "        return;\n"
            "    }\n"
            "    flush_pending(ctx);\n"
            "}\n\n"
            "void init_socket_event(struct event_base *base, evutil_socket_t fd, void *ctx) {\n"
            "    struct event *ev = event_new(base, fd, EV_READ | EV_PERSIST, on_socket_ready, ctx);\n"
            "    event_add(ev, NULL);\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "socket,event,src/event_loop.c:3-8,on_socket_ready,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "callback"
        assert entry["entry_symbol"] == "on_socket_ready"
        assert "event_new(base, fd, EV_READ | EV_PERSIST, on_socket_ready, ctx)" in entry["evidence"]
        assert "ctx" in entry["input_hints"]

    async def test_argparse_main_entry_feeds_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "cli.py").write_text(
            "import argparse\n\n"
            "def process_payment(config, tenant_id):\n"
            "    if not config:\n"
            "        return 'missing'\n"
            "    return tenant_id\n\n"
            "def main(argv=None):\n"
            "    parser = argparse.ArgumentParser()\n"
            "    parser.add_argument('--config', required=True)\n"
            "    parser.add_argument('--tenant-id', dest='tenant_id')\n"
            "    args = parser.parse_args(argv)\n"
            "    return process_payment(args.config, args.tenant_id)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,cli,src/cli.py:3-6,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["entry_kind"] == "cli"
        assert gap["entry_paths"][0]["entry_symbol"] == "main"
        assert gap["entry_paths"][0]["input_hints"] == ["--config", "--tenant-id"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "--config" in case_text
        assert "--tenant-id" in case_text

    async def test_sys_argv_main_entry_feeds_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "cli.py").write_text(
            "import sys\n\n"
            "def process_payment(config, tenant_id):\n"
            "    if not config:\n"
            "        return 'missing'\n"
            "    return tenant_id\n\n"
            "def main():\n"
            "    config = sys.argv[1]\n"
            "    tenant_id = sys.argv[2]\n"
            "    return process_payment(config, tenant_id)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,cli,src/cli.py:3-6,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "cli"
        assert entry["entry_symbol"] == "main"
        assert entry["input_hints"] == ["config", "tenant_id"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "config" in case_text
        assert "tenant_id" in case_text
        assert "sys.argv" not in case_text

    async def test_process_argv_destructuring_feeds_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "cli.ts").write_text(
            "export function processPayment(config: string, tenantId: string) {\n"
            "  if (!config) {\n"
            "    return 'missing';\n"
            "  }\n"
            "  return tenantId;\n"
            "}\n\n"
            "export function main() {\n"
            "  const [, , config, tenantId] = process.argv;\n"
            "  return processPayment(config, tenantId);\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,cli,src/cli.ts:1-6,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "cli"
        assert entry["entry_symbol"] == "main"
        assert entry["input_hints"] == ["config", "tenantId"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "config" in case_text
        assert "tenantId" in case_text
        assert "process.argv" not in case_text

    async def test_getopt_long_main_entry_feeds_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "tool.c").write_text(
            "#include <getopt.h>\n\n"
            "int process_payment(const char *config, const char *tenant_id) {\n"
            "    if (config == 0) {\n"
            "        return -1;\n"
            "    }\n"
            "    return tenant_id != 0;\n"
            "}\n\n"
            "int main(int argc, char **argv) {\n"
            "    static struct option long_options[] = {\n"
            "        {\"config\", required_argument, 0, 'c'},\n"
            "        {\"tenant-id\", required_argument, 0, 't'},\n"
            "        {0, 0, 0, 0},\n"
            "    };\n"
            "    const char *config = 0;\n"
            "    const char *tenant_id = 0;\n"
            "    int opt;\n"
            "    while ((opt = getopt_long(argc, argv, \"c:t:\", long_options, 0)) != -1) {\n"
            "        if (opt == 'c') config = optarg;\n"
            "        if (opt == 't') tenant_id = optarg;\n"
            "    }\n"
            "    return process_payment(config, tenant_id);\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,tool,src/tool.c:3-8,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["entry_kind"] == "cli"
        assert gap["entry_paths"][0]["entry_symbol"] == "main"
        assert gap["entry_paths"][0]["input_hints"] == ["--config", "--tenant-id"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "--config" in case_text
        assert "--tenant-id" in case_text

    async def test_click_command_decorator_feeds_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "tasks.py").write_text(
            "import click\n\n"
            "def process_payment(config):\n"
            "    if not config:\n"
            "        return 'missing'\n"
            "    return 'processed'\n\n"
            "@click.command()\n"
            "@click.option('--config', required=True)\n"
            "@click.argument('tenant_id')\n"
            "def pay(config, tenant_id):\n"
            "    return process_payment(config)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,tasks,src/tasks.py:3-6,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["entry_kind"] == "cli"
        assert gap["entry_paths"][0]["entry_symbol"] == "pay"
        assert gap["entry_paths"][0]["input_hints"] == ["--config", "tenant_id"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "--config" in case_text
        assert "tenant_id" in case_text

    async def test_typer_annotated_option_feeds_black_box_cli_option_hint(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "tasks.py").write_text(
            "import typer\n"
            "from typing import Annotated\n\n"
            "app = typer.Typer()\n\n"
            "def process_payment(config, tenant_id):\n"
            "    if not config:\n"
            "        return 'missing'\n"
            "    return tenant_id\n\n"
            "@app.command()\n"
            "def pay(\n"
            "    tenant_id: Annotated[str, typer.Argument()],\n"
            "    config: Annotated[str, typer.Option('--config')],\n"
            "):\n"
            "    return process_payment(config, tenant_id)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,tasks,src/tasks.py:6-9,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "cli"
        assert entry["entry_symbol"] == "pay"
        assert entry["input_hints"] == ["tenant_id", "--config"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "tenant_id" in case_text
        assert "--config" in case_text

    async def test_typer_option_without_explicit_name_ignores_help_text_hint(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "tasks.py").write_text(
            "import typer\n"
            "from typing import Annotated\n\n"
            "app = typer.Typer()\n\n"
            "def process_payment(config, tenant_id):\n"
            "    if not config:\n"
            "        return 'missing'\n"
            "    return tenant_id\n\n"
            "@app.command()\n"
            "def pay(\n"
            "    tenant_id: Annotated[str, typer.Argument(help='Tenant id')],\n"
            "    config: Annotated[str, typer.Option(help='Config path')],\n"
            "):\n"
            "    return process_payment(config, tenant_id)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,tasks,src/tasks.py:6-9,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "cli"
        assert entry["input_hints"] == ["tenant_id", "config"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "tenant_id" in case_text
        assert "config" in case_text
        assert "Config path" not in case_text
        assert "Tenant id" not in case_text

    async def test_commander_action_registration_feeds_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "cli.ts").write_text(
            "import { Command } from 'commander';\n\n"
            "export function runSync(tenantId, options) {\n"
            "  if (!options.config) {\n"
            "    return 'missing';\n"
            "  }\n"
            "  return tenantId;\n"
            "}\n\n"
            "const program = new Command();\n"
            "program\n"
            "  .command('sync <tenantId>')\n"
            "  .requiredOption('--config <path>')\n"
            "  .option('--dry-run')\n"
            "  .action(runSync);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "sync,cli,src/cli.ts:3-8,runSync,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "cli"
        assert entry["entry_symbol"] == "runSync"
        assert entry["input_hints"] == ["tenantId", "--config", "--dry-run"]
        assert ".action(runSync)" in entry["evidence"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "tenantId" in case_text
        assert "--config" in case_text
        assert "--dry-run" in case_text

    async def test_yargs_command_registration_feeds_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "cli.ts").write_text(
            "import yargs from 'yargs';\n\n"
            "export function runSync(argv) {\n"
            "  if (!argv.config) {\n"
            "    return 'missing';\n"
            "  }\n"
            "  return argv.tenantId;\n"
            "}\n\n"
            "yargs\n"
            "  .command('sync <tenantId>', 'Run sync', (cmd) => cmd\n"
            "    .option('config', { demandOption: true })\n"
            "    .option('dry-run', { type: 'boolean' }), runSync);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "sync,cli,src/cli.ts:3-8,runSync,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "cli"
        assert entry["entry_symbol"] == "runSync"
        assert entry["input_hints"] == ["tenantId", "--config", "--dry-run"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "tenantId" in case_text
        assert "--config" in case_text
        assert "--dry-run" in case_text

    async def test_go_cobra_run_callback_feeds_black_box_cli_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        cmd = tmp_path / "cmd"
        cmd.mkdir()
        (cmd / "pay.go").write_text(
            "package cmd\n\n"
            "import \"github.com/spf13/cobra\"\n\n"
            "func processPayment(config string, tenantID string) error {\n"
            "    if config == \"\" {\n"
            "        return ErrMissingConfig\n"
            "    }\n"
            "    return nil\n"
            "}\n\n"
            "var payCmd = &cobra.Command{\n"
            "    Use:   \"pay <tenant-id>\",\n"
            "    Short: \"Process a payment\",\n"
            "    RunE: func(cmd *cobra.Command, args []string) error {\n"
            "        config, _ := cmd.Flags().GetString(\"config\")\n"
            "        return processPayment(config, args[0])\n"
            "    },\n"
            "}\n\n"
            "func init() {\n"
            "    payCmd.Flags().String(\"config\", \"\", \"config path\")\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,cmd,cmd/pay.go:5-10,processPayment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "cli"
        assert entry["entry_symbol"] == "processPayment"
        assert "RunE" in entry["evidence"]
        assert entry["input_hints"] == ["tenant-id", "--config"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "tenant-id" in case_text
        assert "--config" in case_text

    async def test_go_standard_flag_cli_feeds_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        cmd = tmp_path / "cmd"
        cmd.mkdir()
        (cmd / "sync.go").write_text(
            "package main\n\n"
            "import \"flag\"\n\n"
            "func syncTenant(config string, tenantID string) error {\n"
            "    if config == \"\" {\n"
            "        return ErrMissingConfig\n"
            "    }\n"
            "    return nil\n"
            "}\n\n"
            "func main() {\n"
            "    config := flag.String(\"config\", \"\", \"config path\")\n"
            "    tenantID := flag.String(\"tenant-id\", \"\", \"tenant id\")\n"
            "    flag.Parse()\n"
            "    _ = syncTenant(*config, *tenantID)\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "sync,cmd,cmd/sync.go:5-10,syncTenant,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "cli"
        assert entry["entry_symbol"] == "main"
        assert entry["input_hints"] == ["--config", "--tenant-id"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "--config" in case_text
        assert "--tenant-id" in case_text

    async def test_go_goroutine_call_site_is_worker_entry_without_agent(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "worker"
        src.mkdir()
        (src / "processor.go").write_text(
            "package worker\n\n"
            "type Batch struct { ID string }\n\n"
            "func flushBatch(batch Batch) error {\n"
            "    if batch.ID == \"\" {\n"
            "        return ErrMissingBatch\n"
            "    }\n"
            "    return nil\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "runtime.go").write_text(
            "package worker\n\n"
            "func StartRuntime(batch Batch) {\n"
            "    go flushBatch(batch)\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "batch,worker,worker/processor.go:5-10,flushBatch,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "worker"
        assert entry["entry_symbol"] == "flushBatch"
        assert "go flushBatch(batch)" in entry["evidence"]
        assert "batch" in entry["input_hints"]

    async def test_callback_registration_entry_discovery_prevents_final_gray_box(
        self, tmp_path
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "session.c").write_text(
            "void internal_recover(void *ctx) {\n"
            "    if (ctx == 0) {\n"
            "        return;\n"
            "    }\n"
            "    cleanup(ctx);\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "service.c").write_text(
            "static void on_session_timeout(void *ctx) {\n"
            "    internal_recover(ctx);\n"
            "}\n\n"
            "static struct service_ops ops = {\n"
            "    .timeout_cb = on_session_timeout,\n"
            "};\n\n"
            "SERVICE_REGISTER(\"session\", &ops);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "recover,session,src/session.c:1-6,internal_recover,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        card = design["entry_discovery"]["cards"][0]
        assert gap["gray_box_required"] is False
        assert gap["entry_trace_status"] == "entry_found"
        assert gap["entry_discovery"]["entry_trace_status"] == "entry_found"
        assert card["function_name"] == "internal_recover"
        assert card["candidate_external_entries"]
        assert card["candidate_external_entries"][0]["entry_symbol"] == "on_session_timeout"
        assert card["candidate_external_entries"][0]["entry_type"] == "callback"
        assert "SERVICE_REGISTER" in card["candidate_external_entries"][0]["evidence"]

    async def test_address_of_callback_registration_prevents_final_gray_box(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "session.c").write_text(
            "void recover_session(void *ctx) {\n"
            "    if (ctx == 0) {\n"
            "        return;\n"
            "    }\n"
            "    cleanup(ctx);\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "service.c").write_text(
            "static struct service_ops ops = {\n"
            "    .timeout_cb = &recover_session,\n"
            "};\n\n"
            "SERVICE_REGISTER(\"session\", &ops);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "recover,session,src/session.c:1-6,recover_session,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "callback"
        assert entry["entry_symbol"] == "recover_session"
        assert ".timeout_cb = &recover_session" in entry["evidence"]

    async def test_cross_file_transport_ops_registration_is_callback_entry_without_agent(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "nvmf_tcp" / "transport" / "tls"
        src.mkdir(parents=True)
        (src / "tls.c").write_text(
            "struct nvmf_transport_ops {\n"
            "    const char *name;\n"
            "    int (*listen)(struct nvmf_transport *transport);\n"
            "};\n\n"
            "static int nvmf_tcp_tls_listen(struct nvmf_transport *transport) {\n"
            "    if (transport == 0) {\n"
            "        return -1;\n"
            "    }\n"
            "    return 0;\n"
            "}\n\n"
            "const struct nvmf_transport_ops nvmf_tcp_tls_ops = {\n"
            "    .name = \"tcp-tls\",\n"
            "    .listen = nvmf_tcp_tls_listen,\n"
            "};\n",
            encoding="utf-8",
        )
        (src / "transport.c").write_text(
            "struct nvmf_transport_ops;\n"
            "extern const struct nvmf_transport_ops nvmf_tcp_tls_ops;\n"
            "SPDK_NVMF_TRANSPORT_REGISTER(tcp_tls, &nvmf_tcp_tls_ops);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "tls,nvmf_tcp,nvmf_tcp/transport/tls/tls.c:6-11,nvmf_tcp_tls_listen,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "callback"
        assert entry["entry_symbol"] == "nvmf_tcp_tls_listen"
        assert entry["external_trigger"] == "tcp-tls"
        assert ".listen = nvmf_tcp_tls_listen" in entry["evidence"]
        assert "SPDK_NVMF_TRANSPORT_REGISTER" in entry["evidence"]

    async def test_callback_assignment_parser_accepts_address_of_symbol(self):
        from app.services.coverage_analyzer import _callback_symbol_from_assignment

        assert _callback_symbol_from_assignment(".timeout_cb = &recover_session") == "recover_session"

    async def test_callback_assignment_parser_accepts_casted_symbol(self):
        from app.services.coverage_analyzer import _callback_symbol_from_assignment

        assert (
            _callback_symbol_from_assignment(".timeout_cb = (service_cb) recover_session")
            == "recover_session"
        )

    async def test_event_dispatcher_register_entry_prevents_final_gray_box(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.py").write_text(
            "def process_payment(event):\n"
            "    if not event:\n"
            "        return 'missing'\n"
            "    return 'processed'\n",
            encoding="utf-8",
        )
        (src / "bootstrap.py").write_text(
            "from payments import process_payment\n\n"
            "def consume(payload):\n"
            "    return process_payment(payload)\n\n"
            "registry.register('payment.created', consume)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.py:1-4,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        card = design["entry_discovery"]["cards"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["entry_symbol"] == "consume"
        assert gap["entry_paths"][0]["entry_kind"] in {"callback", "message"}
        assert card["candidate_external_entries"][0]["entry_symbol"] == "consume"
        assert "registry.register" in card["candidate_external_entries"][0]["evidence"]

    async def test_signal_handler_registration_is_callback_entry_without_agent(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "signals.py").write_text(
            "import signal\n\n"
            "def shutdown(signum, frame):\n"
            "    if signum == signal.SIGTERM:\n"
            "        return 'term'\n"
            "    return 'other'\n\n"
            "signal.signal(signal.SIGTERM, shutdown)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "runtime,signals,src/signals.py:3-6,shutdown,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "callback"
        assert entry["entry_symbol"] == "shutdown"
        assert "signal.signal" in entry["evidence"]
        assert "SIGTERM" in entry["input_hints"]

    async def test_node_signal_handler_registration_is_callback_entry_without_agent(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "signals.js").write_text(
            "function shutdown(signal) {\n"
            "  if (signal === 'SIGTERM') {\n"
            "    return 'term';\n"
            "  }\n"
            "  return 'other';\n"
            "}\n\n"
            "process.on('SIGTERM', shutdown);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "runtime,signals,src/signals.js:1-6,shutdown,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "callback"
        assert entry["entry_symbol"] == "shutdown"
        assert "process.on('SIGTERM', shutdown)" in entry["evidence"]
        assert "SIGTERM" in entry["input_hints"]

    async def test_node_process_lifecycle_registration_is_callback_entry_without_agent(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "runtime.js").write_text(
            "function reportFatal(error) {\n"
            "  if (!error) {\n"
            "    return 'missing';\n"
            "  }\n"
            "  return 'reported';\n"
            "}\n\n"
            "process.on('uncaughtException', reportFatal);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "runtime,runtime,src/runtime.js:1-6,reportFatal,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "callback"
        assert entry["entry_symbol"] == "reportFatal"
        assert "process.on('uncaughtException', reportFatal)" in entry["evidence"]
        assert "uncaughtException" in entry["input_hints"]

    async def test_python_atexit_registration_is_callback_entry_without_agent(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "shutdown.py").write_text(
            "import atexit\n\n"
            "def flush_metrics(reason=None):\n"
            "    if reason is None:\n"
            "        return 'default'\n"
            "    return 'flushed'\n\n"
            "atexit.register(flush_metrics)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "runtime,shutdown,src/shutdown.py:3-6,flush_metrics,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "callback"
        assert entry["entry_symbol"] == "flush_metrics"
        assert "atexit.register(flush_metrics)" in entry["evidence"]
        assert "atexit" in entry["input_hints"]

    async def test_browser_lifecycle_property_registration_is_callback_entry_without_agent(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "errors.ts").write_text(
            "export function reportError(message) {\n"
            "  if (!message) {\n"
            "    return 'missing';\n"
            "  }\n"
            "  return 'reported';\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "browser.ts").write_text(
            "import { reportError } from './errors';\n\n"
            "window.onerror = reportError;\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "browser,errors,src/errors.ts:1-6,reportError,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "callback"
        assert entry["entry_symbol"] == "reportError"
        assert "window.onerror = reportError" in entry["evidence"]
        assert "onerror" in entry["input_hints"]

    async def test_property_callback_assignment_is_black_box_entry_without_agent(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "events.py").write_text(
            "def process_event(payload):\n"
            "    if not payload:\n"
            "        return 'missing'\n"
            "    return 'processed'\n",
            encoding="utf-8",
        )
        (src / "bootstrap.py").write_text(
            "from events import process_event\n\n"
            "def handle_event(payload):\n"
            "    return process_event(payload)\n\n"
            "app.on_event = handle_event\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "events,events,src/events.py:1-4,process_event,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "message"
        assert entry["entry_symbol"] == "handle_event"
        assert "app.on_event = handle_event" in entry["evidence"]
        assert entry["input_hints"] == ["on_event", "payload"]

    async def test_registry_index_assignment_is_black_box_entry_without_agent(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.py").write_text(
            "def process_payment(payload):\n"
            "    if not payload:\n"
            "        return 'missing'\n"
            "    return 'processed'\n",
            encoding="utf-8",
        )
        (src / "registry.py").write_text(
            "from payments import process_payment\n\n"
            "def handle_payment(payload):\n"
            "    return process_payment(payload)\n\n"
            "event_handlers['payment.created'] = handle_payment\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.py:1-4,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "message"
        assert entry["entry_symbol"] == "handle_payment"
        assert "event_handlers['payment.created'] = handle_payment" in entry["evidence"]
        assert entry["input_hints"] == ["payment.created", "payload"]

    async def test_casted_registry_index_assignment_is_black_box_entry_without_agent(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "payments.c").write_text(
            "int process_payment(void *payload) {\n"
            "    if (payload == 0) {\n"
            "        return -1;\n"
            "    }\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "registry.c").write_text(
            "static int handle_payment(void *payload) {\n"
            "    return process_payment(payload);\n"
            "}\n\n"
            "void setup_handlers(void) {\n"
            "    event_handlers[\"payment.created\"] = (event_handler_t)handle_payment;\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "payments,payments,src/payments.c:1-6,process_payment,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] in {"callback", "message"}
        assert entry["entry_symbol"] == "handle_payment"
        assert "event_handlers[\"payment.created\"]" in entry["evidence"]
        assert "payment.created" in entry["input_hints"]

    async def test_scheduler_add_job_registration_keeps_scheduler_entry_kind(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "cleanup.py").write_text(
            "def purge_expired(payload):\n"
            "    if not payload:\n"
            "        return 'missing'\n"
            "    return 'purged'\n",
            encoding="utf-8",
        )
        (src / "jobs.py").write_text(
            "from cleanup import purge_expired\n\n"
            "def run(payload):\n"
            "    return purge_expired(payload)\n\n"
            "scheduler.add_job('nightly-cleanup', run)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "cleanup,cleanup,src/cleanup.py:1-4,purge_expired,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["entry_symbol"] == "run"
        assert gap["entry_paths"][0]["entry_kind"] == "scheduler"
        assert "scheduler.add_job" in gap["entry_paths"][0]["evidence"]

    async def test_python_sched_enter_registration_keeps_delay_hint_without_agent(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "digest.py").write_text(
            "def send_digest(payload):\n"
            "    if not payload:\n"
            "        return 'missing'\n"
            "    return 'sent'\n",
            encoding="utf-8",
        )
        (src / "runtime.py").write_text(
            "import sched\n"
            "from digest import send_digest\n\n"
            "scheduler = sched.scheduler()\n"
            "scheduler.enter(60, 1, send_digest, argument=({'force': True},))\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "digest,digest,src/digest.py:1-4,send_digest,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "scheduler"
        assert entry["entry_symbol"] == "send_digest"
        assert "scheduler.enter(60, 1, send_digest" in entry["evidence"]
        assert "60" in entry["input_hints"]

    async def test_python_schedule_every_registration_keeps_interval_hint_without_agent(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "digest.py").write_text(
            "def send_digest(payload):\n"
            "    if not payload:\n"
            "        return 'missing'\n"
            "    return 'sent'\n",
            encoding="utf-8",
        )
        (src / "runtime.py").write_text(
            "import schedule\n"
            "from digest import send_digest\n\n"
            "schedule.every(5).minutes.do(send_digest, {'force': True})\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "digest,digest,src/digest.py:1-4,send_digest,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "scheduler"
        assert entry["entry_symbol"] == "send_digest"
        assert "schedule.every(5).minutes.do(send_digest" in entry["evidence"]
        assert "5 minutes" in entry["input_hints"]

    async def test_apscheduler_function_ref_add_job_keeps_job_id_and_signature_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "jobs.py").write_text(
            "def cleanup_expired_sessions(batch_size):\n"
            "    if batch_size <= 0:\n"
            "        return 'skip'\n"
            "    return 'ok'\n",
            encoding="utf-8",
        )
        (src / "scheduler.py").write_text(
            "from apscheduler.schedulers.background import BackgroundScheduler\n"
            "from jobs import cleanup_expired_sessions\n\n"
            "scheduler = BackgroundScheduler()\n"
            "scheduler.add_job(cleanup_expired_sessions, 'cron', id='session_cleanup', args=[100])\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "sessions,jobs,src/jobs.py:1-4,cleanup_expired_sessions,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "scheduler"
        assert entry["entry_symbol"] == "cleanup_expired_sessions"
        assert "scheduler.add_job" in entry["evidence"]
        assert entry["input_hints"] == ["session_cleanup", "cron", "batch_size"]

    async def test_python_thread_target_registration_is_worker_entry_without_agent(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "workers.py").write_text(
            "def flush_pending_batches(batch_size):\n"
            "    if batch_size <= 0:\n"
            "        return 'skip'\n"
            "    return 'flushed'\n",
            encoding="utf-8",
        )
        (src / "bootstrap.py").write_text(
            "import threading\n"
            "from workers import flush_pending_batches\n\n"
            "threading.Thread(\n"
            "    name='batch-flusher',\n"
            "    target=flush_pending_batches,\n"
            "    kwargs={'batch_size': 250},\n"
            "    daemon=True,\n"
            ").start()\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "batches,workers,src/workers.py:1-4,flush_pending_batches,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "worker"
        assert entry["entry_symbol"] == "flush_pending_batches"
        assert "threading.Thread" in entry["evidence"]
        assert entry["input_hints"] == ["batch-flusher", "batch_size"]

    async def test_java_thread_method_reference_is_worker_entry_without_agent(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "InvoiceWorker.java").write_text(
            "public class InvoiceWorker {\n"
            "  public void flushQueue() {\n"
            "    if (!queueReady()) {\n"
            "      return;\n"
            "    }\n"
            "    drainInvoices();\n"
            "  }\n\n"
            "  public void start() {\n"
            "    new Thread(this::flushQueue, \"invoice-flusher\").start();\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,worker,src/InvoiceWorker.java:2-7,flushQueue,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "worker"
        assert entry["entry_symbol"] == "flushQueue"
        assert "this::flushQueue" in entry["evidence"]

    async def test_java_thread_lambda_is_worker_entry_without_agent(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "InvoiceWorker.java").write_text(
            "public class InvoiceWorker {\n"
            "  public void flushQueue() {\n"
            "    if (!queueReady()) {\n"
            "      return;\n"
            "    }\n"
            "    drainInvoices();\n"
            "  }\n\n"
            "  public void start() {\n"
            "    new Thread(() -> flushQueue(), \"invoice-flusher\").start();\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,worker,src/InvoiceWorker.java:2-7,flushQueue,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "worker"
        assert entry["entry_symbol"] == "flushQueue"
        assert "new Thread(() -> flushQueue()" in entry["evidence"]

    async def test_cpp_std_thread_function_is_worker_entry_without_agent(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "batch_worker.cpp").write_text(
            "#include <thread>\n\n"
            "void processBatch(int limit) {\n"
            "    if (limit <= 0) {\n"
            "        return;\n"
            "    }\n"
            "    flush(limit);\n"
            "}\n\n"
            "void startWorkers() {\n"
            "    std::thread worker(processBatch, 100);\n"
            "    worker.detach();\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "batch,worker,src/batch_worker.cpp:3-8,processBatch,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "worker"
        assert entry["entry_symbol"] == "processBatch"
        assert "std::thread worker(processBatch, 100)" in entry["evidence"]

    async def test_c_pthread_create_function_is_worker_entry_without_agent(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "batch_worker.c").write_text(
            "#include <pthread.h>\n\n"
            "static void *process_batch(void *ctx) {\n"
            "    if (ctx == NULL) {\n"
            "        return NULL;\n"
            "    }\n"
            "    flush(ctx);\n"
            "    return NULL;\n"
            "}\n\n"
            "void start_workers(void *ctx) {\n"
            "    pthread_t tid;\n"
            "    pthread_create(&tid, NULL, process_batch, ctx);\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "batch,worker,src/batch_worker.c:3-9,process_batch,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "worker"
        assert entry["entry_symbol"] == "process_batch"
        assert "pthread_create(&tid, NULL, process_batch, ctx)" in entry["evidence"]

    async def test_rust_thread_spawn_is_worker_entry_without_agent(
        self, tmp_path, monkeypatch
    ):
        from app.services.coverage_analyzer import build_coverage_test_design

        monkeypatch.setattr(
            "app.services.coverage_analyzer.settings.external_agents_enabled",
            False,
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "runtime.rs").write_text(
            "use std::thread;\n\n"
            "fn process_batch(limit: usize) {\n"
            "    if limit == 0 {\n"
            "        return;\n"
            "    }\n"
            "    flush(limit);\n"
            "}\n\n"
            "fn start_runtime() {\n"
            "    thread::spawn(|| process_batch(100));\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "batch,runtime,src/runtime.rs:3-8,process_batch,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "worker"
        assert entry["entry_symbol"] == "process_batch"
        assert "thread::spawn(|| process_batch(100))" in entry["evidence"]

    async def test_asyncio_create_task_registration_is_worker_entry_without_agent(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "jobs.py").write_text(
            "async def refresh_cache(force):\n"
            "    if not force:\n"
            "        return 'skip'\n"
            "    return 'refreshed'\n",
            encoding="utf-8",
        )
        (src / "bootstrap.py").write_text(
            "import asyncio\n"
            "from jobs import refresh_cache\n\n"
            "def start_background_tasks():\n"
            "    asyncio.create_task(refresh_cache(force=True), name='cache-refresh')\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "cache,jobs,src/jobs.py:1-4,refresh_cache,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "worker"
        assert entry["entry_symbol"] == "refresh_cache"
        assert "asyncio.create_task" in entry["evidence"]
        assert entry["input_hints"] == ["cache-refresh", "force"]

    async def test_asyncio_call_later_registration_is_timer_entry_without_agent(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "jobs.py").write_text(
            "def refresh_cache(force):\n"
            "    if not force:\n"
            "        return 'skip'\n"
            "    return 'refreshed'\n",
            encoding="utf-8",
        )
        (src / "bootstrap.py").write_text(
            "from jobs import refresh_cache\n\n"
            "def start_background_tasks(loop):\n"
            "    loop.call_later(30, refresh_cache, True)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "cache,jobs,src/jobs.py:1-4,refresh_cache,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "timer"
        assert entry["entry_symbol"] == "refresh_cache"
        assert "loop.call_later(30, refresh_cache, True)" in entry["evidence"]
        assert "30" in entry["input_hints"]

    async def test_message_subscribe_registration_keeps_message_entry_kind(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "invoices.py").write_text(
            "def reconcile_invoice(payload):\n"
            "    if not payload:\n"
            "        return 'missing'\n"
            "    return 'reconciled'\n",
            encoding="utf-8",
        )
        (src / "subscriptions.py").write_text(
            "from invoices import reconcile_invoice\n\n"
            "def consume(payload):\n"
            "    return reconcile_invoice(payload)\n\n"
            "bus.subscribe('invoice.created', consume)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "invoices,invoices,src/invoices.py:1-4,reconcile_invoice,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["entry_paths"][0]["entry_symbol"] == "consume"
        assert gap["entry_paths"][0]["entry_kind"] == "message"
        assert "bus.subscribe" in gap["entry_paths"][0]["evidence"]
        assert gap["entry_paths"][0]["input_hints"] == ["invoice.created", "payload"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "invoice.created" in case_text

    async def test_message_payload_fields_feed_black_box_input_hints_without_ack_context(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "events.ts").write_text(
            "export function processInvoice(payload, ctx) {\n"
            "  const amount = payload.amount;\n"
            "  if (!amount) {\n"
            "    return 'missing';\n"
            "  }\n"
            "  return ctx.ack();\n"
            "}\n\n"
            "eventBus.subscribe('invoice.created', processInvoice);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,events,src/events.ts:1-7,processInvoice,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "message"
        assert entry["input_hints"] == ["invoice.created", "amount"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "invoice.created" in case_text
        assert "amount" in case_text
        assert "payload" not in case_text
        assert "ctx.ack" not in case_text
        assert '"ack"' not in case_text

    async def test_rabbitmq_consume_registration_feeds_message_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "consumer.ts").write_text(
            "export function processInvoice(message) {\n"
            "  const payload = JSON.parse(message.content.toString());\n"
            "  if (!payload.amount) {\n"
            "    return 'missing';\n"
            "  }\n"
            "  return payload.currency;\n"
            "}\n\n"
            "channel.consume('invoice_queue', processInvoice, { noAck: false });\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,consumer,src/consumer.ts:1-7,processInvoice,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "message"
        assert entry["entry_symbol"] == "processInvoice"
        assert "channel.consume" in entry["evidence"]
        assert entry["input_hints"] == ["invoice_queue", "amount", "currency"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "invoice_queue" in case_text
        assert "amount" in case_text
        assert "currency" in case_text
        assert "content" not in case_text

    async def test_queue_job_data_fields_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "worker.ts").write_text(
            "export function processInvoice(amount, currency) {\n"
            "  if (!amount || !currency) {\n"
            "    return 'missing';\n"
            "  }\n"
            "  return `${amount}:${currency}`;\n"
            "}\n\n"
            "invoiceQueue.process('invoice_queue', async (job) => {\n"
            "  return processInvoice(job.data.amount, job.data.currency);\n"
            "});\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,worker,src/worker.ts:1-6,processInvoice,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "queue"
        assert entry["input_hints"] == ["invoice_queue", "amount", "currency"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "invoice_queue" in case_text
        assert "amount" in case_text
        assert "currency" in case_text
        assert "job.data" not in case_text

    async def test_message_record_body_parse_skips_envelope_input_hint(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "events.ts").write_text(
            "export function processInvoice(record) {\n"
            "  const payload = JSON.parse(record.body);\n"
            "  if (!payload.amount) {\n"
            "    return 'missing';\n"
            "  }\n"
            "  return payload.currency;\n"
            "}\n\n"
            "eventBus.subscribe('invoice.created', processInvoice);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,events,src/events.ts:1-7,processInvoice,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "message"
        assert entry["input_hints"] == ["invoice.created", "amount", "currency"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "invoice.created" in case_text
        assert "amount" in case_text
        assert "currency" in case_text
        assert "body" not in case_text

    async def test_message_headers_feed_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "events.ts").write_text(
            "export function processInvoice(record) {\n"
            "  const tenantId = record.properties.headers['tenant_id'];\n"
            "  const payload = JSON.parse(record.body);\n"
            "  if (!tenantId || !payload.amount) {\n"
            "    return 'missing';\n"
            "  }\n"
            "  return payload.currency;\n"
            "}\n\n"
            "eventBus.subscribe('invoice.created', processInvoice);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,events,src/events.ts:1-8,processInvoice,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "message"
        assert entry["input_hints"] == [
            "invoice.created", "tenant_id", "amount", "currency",
        ]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "tenant_id" in case_text
        assert "amount" in case_text
        assert "properties" not in case_text
        assert "headers" not in case_text

    async def test_message_payload_destructuring_feeds_black_box_input_hints(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "events.ts").write_text(
            "export function processInvoice(payload, ctx) {\n"
            "  const { amount, currency } = payload;\n"
            "  if (!amount) {\n"
            "    return 'missing';\n"
            "  }\n"
            "  return ctx.ack(currency);\n"
            "}\n\n"
            "eventBus.subscribe('invoice.created', processInvoice);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,events,src/events.ts:1-7,processInvoice,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "message"
        assert entry["input_hints"] == ["invoice.created", "amount", "currency"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "invoice.created" in case_text
        assert "amount" in case_text
        assert "currency" in case_text
        assert "payload" not in case_text
        assert "ctx.ack" not in case_text

    async def test_message_record_value_destructuring_skips_envelope_input_hint(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "events.ts").write_text(
            "export function processInvoice(record) {\n"
            "  const { amount } = record.value;\n"
            "  if (!amount) {\n"
            "    return 'missing';\n"
            "  }\n"
            "  return 'processed';\n"
            "}\n\n"
            "eventBus.subscribe('invoice.created', processInvoice);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,events,src/events.ts:1-7,processInvoice,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "message"
        assert entry["input_hints"] == ["invoice.created", "amount"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "invoice.created" in case_text
        assert "amount" in case_text
        assert "record.value" not in case_text

    async def test_message_record_value_field_feeds_black_box_input_hint(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "events.ts").write_text(
            "export function processInvoice(record) {\n"
            "  const amount = record.value.amount;\n"
            "  if (!amount) {\n"
            "    return 'missing';\n"
            "  }\n"
            "  return 'processed';\n"
            "}\n\n"
            "eventBus.subscribe('invoice.created', processInvoice);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,events,src/events.ts:1-7,processInvoice,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "message"
        assert entry["input_hints"] == ["invoice.created", "amount"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "invoice.created" in case_text
        assert "amount" in case_text
        assert "record.value" not in case_text

    async def test_event_on_registration_keeps_message_entry_kind(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "invoices.ts").write_text(
            "export function reconcileInvoice(payload) {\n"
            "  if (!payload) {\n"
            "    return 'missing';\n"
            "  }\n"
            "  return 'reconciled';\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "events.ts").write_text(
            "import { reconcileInvoice } from './invoices';\n\n"
            "function consume(payload) {\n"
            "  return reconcileInvoice(payload);\n"
            "}\n\n"
            "bus.on('invoice.created', consume);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "invoices,invoices,src/invoices.ts:1-6,reconcileInvoice,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_symbol"] == "consume"
        assert entry["entry_kind"] == "message"
        assert "bus.on" in entry["evidence"]
        assert entry["input_hints"] == ["invoice.created", "payload"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "invoice.created" in case_text

    async def test_event_prepend_listener_registration_keeps_message_entry_kind(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "invoices.ts").write_text(
            "export function reconcileInvoice(payload) {\n"
            "  if (!payload) {\n"
            "    return 'missing';\n"
            "  }\n"
            "  return 'reconciled';\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "events.ts").write_text(
            "import { reconcileInvoice } from './invoices';\n\n"
            "function consume(payload) {\n"
            "  return reconcileInvoice(payload);\n"
            "}\n\n"
            "bus.prependListener('invoice.created', consume);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "invoices,invoices,src/invoices.ts:1-6,reconcileInvoice,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_symbol"] == "consume"
        assert entry["entry_kind"] == "message"
        assert "prependListener" in entry["evidence"]
        assert entry["input_hints"] == ["invoice.created", "payload"]

    async def test_add_event_listener_registration_keeps_message_entry_kind(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "checkout.ts").write_text(
            "export function submitCheckout(event) {\n"
            "  const amount = event.detail.amount;\n"
            "  if (!amount) {\n"
            "    return 'missing';\n"
            "  }\n"
            "  return 'submitted';\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "ui.ts").write_text(
            "import { submitCheckout } from './checkout';\n\n"
            "checkoutButton.addEventListener('submit', submitCheckout);\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "checkout,checkout,src/checkout.ts:1-7,submitCheckout,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["gray_box_required"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_symbol"] == "submitCheckout"
        assert entry["entry_kind"] == "message"
        assert "addEventListener" in entry["evidence"]
        assert entry["input_hints"] == ["submit", "amount"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "submit" in case_text
        assert "amount" in case_text

    async def test_entry_discovery_artifact_and_context_are_written(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        self._make_repo(tmp_path)
        artifact_dir = tmp_path / "artifacts"
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "rec,session,src/session.c:1-6,recover_session,false,0\n"
        )

        design = await build_coverage_test_design(
            modules,
            workspace_id="ws-1",
            repo_path=str(tmp_path),
            artifact_dir=artifact_dir,
        )

        assert design["entry_discovery"]["cards"]
        entry_path = artifact_dir / "coverage_entry_discovery.json"
        context_path = artifact_dir / "coverage_test_context.json"
        assert entry_path.exists()
        assert context_path.exists()
        entry_data = json.loads(entry_path.read_text(encoding="utf-8"))
        context = json.loads(context_path.read_text(encoding="utf-8"))
        assert entry_data["cards"][0]["function_name"] == "recover_session"
        assert context["entry_discovery"]["cards"][0]["function_name"] == "recover_session"
        assert context["evidence_source_counts"]["entry_discovery"] >= 1

    async def test_disabled_external_agents_do_not_create_empty_agent_session(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        self._make_repo(tmp_path)
        artifact_dir = tmp_path / "artifacts"
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "rec,session,src/session.c:1-6,recover_session,false,0\n"
        )

        design = await build_coverage_test_design(
            modules,
            workspace_id="ws-1",
            repo_path=str(tmp_path),
            artifact_dir=artifact_dir,
        )

        assert design["agent_discovery_session_id"] is None
        assert design["summary"]["tool_status"]["external_agent"] == "disabled"
        assert not (artifact_dir / "agent_discovery_session.json").exists()
        assert not (artifact_dir / "agent_discovery_ledger.json").exists()

    async def test_ai_debug_artifact_write_failure_does_not_drop_design(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        class FakeLLM:
            async def complete(self, messages, max_tokens=4096, temperature=0.1):
                return LLMResponse(
                    content=json.dumps({"scenarios": []}, ensure_ascii=False),
                    model="fake-llm",
                    usage={},
                )

        self._make_repo(tmp_path)
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir()
        (artifact_dir / "debug").write_text("not a directory\n", encoding="utf-8")
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "rec,session,src/session.c:1-6,recover_session,false,0\n"
        )

        design = await build_coverage_test_design(
            modules,
            workspace_id="ws-1",
            repo_path=str(tmp_path),
            use_ai=True,
            llm=FakeLLM(),
            artifact_dir=artifact_dir,
        )

        assert design["version"] == "coverage-test-design-v1"
        assert design["summary"]["ai_status"] == "available"
        assert any("coverage artifact write failed" in item for item in design["warnings"])
        assert (artifact_dir / "coverage_test_design.json").exists()
        assert (artifact_dir / "coverage_entry_discovery.json").exists()

    async def test_coverage_external_agent_artifact_summarizes_provider_status(self):
        from app.services.coverage_analyzer import _coverage_external_agent_artifact

        artifact = _coverage_external_agent_artifact({
            "agent_discovery_session_id": "agent-session-1",
            "gaps": [
                {
                    "kind": "function",
                    "function_name": "tls_gap_a",
                    "file_path": "src/tls.c",
                    "evidence": {
                        "external_agent": {
                            "provider_status": {
                                "claude-code": "ok",
                                "opencode": "unavailable",
                            },
                            "validated_entries": [{"entry_symbol": "rpc_tls"}],
                        }
                    },
                },
                {
                    "kind": "function",
                    "function_name": "tls_gap_b",
                    "file_path": "src/tls.c",
                    "evidence": {
                        "external_agent": {
                            "provider_status": {
                                "claude-code": "timeout",
                                "opencode": "unavailable",
                            },
                            "unverified_entries": [{"entry_symbol": "maybe_rpc"}],
                        }
                    },
                },
            ],
        })

        assert artifact["summary"]["provider_status_counts"] == {
            "claude-code": {"ok": 1, "timeout": 1},
            "opencode": {"unavailable": 2},
        }
        assert artifact["summary"]["provider_count"] == 2

    async def test_ai_scenario_related_gap_substring_attaches_to_function_gap(self):
        from app.services.coverage_analyzer import _attach_scenarios_to_gaps

        gaps = [
            {
                "kind": "function",
                "function_name": "iscsi_conn_write_pdu",
                "file_path": "lib/iscsi/conn.c",
            }
        ]
        scenarios = [
            {
                "case_type": "black_box_ready",
                "related_gaps": ["iscsi_conn_write_pdu 未覆盖"],
                "key_call_chain": ["iscsi_pdu_hdr_op_login -> iscsi_conn_write_pdu"],
                "evidence_refs": ["lib/iscsi/conn.c:787-842"],
            }
        ]

        _attach_scenarios_to_gaps(gaps, scenarios)

        assert gaps[0]["test_scenarios"] == scenarios

    async def test_gray_box_when_no_external_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        # internal_helper has no caller anywhere in the repo → no entry path.
        src = tmp_path / "src"
        src.mkdir()
        (src / "util.c").write_text(
            "void internal_helper(ctx_t *c) {\n"
            "    if (c->flag) {\n"
            "        free(c->buf);\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "h,util,src/util.c:1-5,internal_helper,false,0\n"
        )
        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )
        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["entry_paths"] == []
        assert gap["gray_box_required"] is True
        assert gap["black_box_readiness"]["case_type"] == "gray_box_required"
        assert gap["test_case_drafts"][0]["case_type"] == "gray_box_required"
        assert gap["gray_box"]["required"] is True
        assert gap["gray_box"]["scheme"]
        assert "internal_helper" in gap["gray_box"]["scheme"]
        assert "c->flag" in gap["gray_box"]["scheme"]
        assert any("入口" in g for g in gap["evidence_gaps"])
        assert design["summary"]["gray_box_required_count"] == 1

    async def test_string_literal_function_mention_does_not_become_black_box_ready(
        self,
        tmp_path,
        monkeypatch,
    ):
        import app.services.coverage_analyzer as mod
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        (src / "api.c").write_text(
            "int api_handle_request(request_t *req) {\n"
            "    log_error(\"recover_session failed previously\");\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "session.c").write_text(
            "void recover_session(session_t *s) {\n"
            "    if (s == NULL) {\n"
            "        return;\n"
            "    }\n"
            "    cleanup(s);\n"
            "}\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(mod.shutil, "which", lambda _name: None)
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "rec,session,src/session.c:1-6,recover_session,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["entry_paths"] == []
        assert gap["black_box_readiness"]["case_type"] == "gray_box_required"
        assert all(case["case_type"] != "black_box_ready" for case in gap["black_box_cases"])

    async def test_internal_handler_name_alone_does_not_become_black_box_ready(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src" / "helpers"
        src.mkdir(parents=True)
        (src / "records.py").write_text(
            "def normalize_record(data):\n"
            "    if not data:\n"
            "        return None\n"
            "    return data.strip()\n",
            encoding="utf-8",
        )
        (src / "handlers.py").write_text(
            "from records import normalize_record\n"
            "def internal_handler(data):\n"
            "    return normalize_record(data)\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "records,helpers,src/helpers/records.py:1-4,normalize_record,false,0\n"
        )

        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )

        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["entry_paths"] == []
        assert gap["gray_box_required"] is True
        assert gap["black_box_readiness"]["case_type"] == "gray_box_required"
        assert all(case["case_type"] != "black_box_ready" for case in gap["black_box_cases"])

    async def test_agent_entry_with_validation_error_does_not_become_black_box_ready(self, tmp_path):
        from app.services.coverage_analyzer import _design_function_gap

        src = tmp_path / "src"
        src.mkdir()
        (src / "tls.c").write_text(
            "void tls_recover_session(void) {\n"
            "    if (1) { return; }\n"
            "}\n",
            encoding="utf-8",
        )
        module = ModuleCoverage(
            module_path="src",
            line_rate=0.0,
            branch_rate=0.0,
            function_rate=0.0,
            function_hits=[],
        )
        hit = FunctionHit(
            function_name="tls_recover_session",
            file_path="src/tls.c",
            line_start=1,
            triggered=False,
            hit_count=0,
        )

        gap = _design_function_gap(
            module,
            hit,
            workspace_id="ws-1",
            repo_path=str(tmp_path),
            repo_root=tmp_path,
            rg_available=True,
            scope={},
            cgc_context={},
            agent_context={
                "validated_entries": [{
                    "provider": "claude-code",
                    "entry_kind": "rpc",
                    "entry_symbol": "rpc_tls_recover",
                    "entry_file": "src/rpc.c",
                    "chain": ["rpc_tls_recover", "tls_recover_session"],
                    "external_trigger": "RPC tls-recover",
                    "reason": "agent entry still failed local source validation",
                    "source_verification": "needs_source_verification",
                    "validation_error": "entry_file_missing",
                }]
            },
            trace=True,
        )

        assert gap["entry_paths"] == []
        assert gap["black_box_readiness"]["case_type"] == "gray_box_required"
        assert all(case["case_type"] != "black_box_ready" for case in gap["black_box_cases"])

    async def test_white_box_leak_downgrades_black_box_cases_consistently(
        self,
        tmp_path,
        monkeypatch,
    ):
        import app.services.coverage_analyzer as coverage_mod
        from app.services.coverage_analyzer import _design_function_gap

        src = tmp_path / "src"
        src.mkdir()
        (src / "session.c").write_text(
            "void recover_session(void) {\n"
            "    if (1) { return; }\n"
            "}\n",
            encoding="utf-8",
        )
        module = ModuleCoverage(
            module_path="src",
            line_rate=0.0,
            branch_rate=0.0,
            function_rate=0.0,
            function_hits=[],
        )
        hit = FunctionHit(
            function_name="recover_session",
            file_path="src/session.c",
            line_start=1,
            triggered=False,
            hit_count=0,
        )

        def leaky_cases(*_args, **_kwargs):
            return [{
                "case_type": "black_box_ready",
                "title": "leaky ready case",
                "entry_kind": "api",
                "external_trigger": "Send a public API request.",
                "preconditions": "Use source src/session.c:1 before executing.",
                "inputs": "Use boundary input.",
                "steps": ["Observe src/session.c:2 and then call recover_session()."],
                "expected": "The request returns a controlled response.",
                "observable_signals": ["client response", "service logs"],
            }]

        monkeypatch.setattr(coverage_mod, "_build_black_box_cases", leaky_cases)

        gap = _design_function_gap(
            module,
            hit,
            workspace_id="ws-1",
            repo_path=str(tmp_path),
            repo_root=tmp_path,
            rg_available=True,
            scope={},
            cgc_context={},
            agent_context={
                "validated_entries": [{
                    "provider": "claude-code",
                    "entry_kind": "api",
                    "entry_symbol": "public_recover_api",
                    "entry_file": "src/api.c",
                    "chain": ["public_recover_api", "recover_session"],
                    "external_trigger": "public recovery API",
                    "reason": "source-backed external entry reaches recovery",
                    "source_verification": "source_backed",
                }]
            },
            trace=True,
        )

        assert gap["white_box_leak_check"]["passed"] is False
        assert gap["black_box_readiness"]["case_type"] == "black_box_hypothesis"
        assert all(case["case_type"] == "black_box_hypothesis" for case in gap["black_box_cases"])
        assert all(draft["case_type"] == "black_box_hypothesis" for draft in gap["test_case_drafts"])

    async def test_agent_entry_budget_prioritizes_late_high_risk_hits(self, tmp_path, monkeypatch):
        import app.services.coverage_analyzer as coverage_mod
        from app.config import settings
        from app.services.coverage_analyzer import build_coverage_test_design
        from app.services.external_agent_discovery import AgentCandidateEntry, AgentDiscoveryResult

        monkeypatch.setattr(settings, "external_agents_enabled", True)

        src = tmp_path / "src"
        src.mkdir()
        rows = ["feature,module,code_location,function,triggered,hit_count"]
        for idx in range(24):
            (src / f"helper_{idx}.c").write_text(
                f"void helper_{idx}(void) {{}}\n",
                encoding="utf-8",
            )
            rows.append(f"h,util,src/helper_{idx}.c:1-1,helper_{idx},false,0")
        (src / "zz_tls.c").write_text(
            "void tls_recover_session(void) {\n"
            "    if (1) { return; }\n"
            "}\n",
            encoding="utf-8",
        )
        (src / "rpc.c").write_text(
            "void rpc_recover_tls(void) { tls_recover_session(); }\n",
            encoding="utf-8",
        )
        rows.append("h,zz_tls,src/zz_tls.c:1-3,tls_recover_session,false,0")
        modules = self._modules("\n".join(rows) + "\n")
        requested: list[str] = []

        async def fake_discovery(request, **_kwargs):
            requested.append(request.analysis_object_text)
            if request.analysis_object_text != "tls_recover_session":
                return [AgentDiscoveryResult(provider="claude-code", status="ok")]
            return [
                AgentDiscoveryResult(
                    provider="claude-code",
                    status="ok",
                    turn_id="coverage:tls_recover_session",
                    candidate_entries=[
                        AgentCandidateEntry(
                            entry_kind="rpc",
                            entry_symbol="rpc_recover_tls",
                            entry_file="src/rpc.c",
                            chain=["rpc_recover_tls", "tls_recover_session"],
                            external_trigger="RPC recover TLS session",
                            reason="public RPC handler reaches TLS recovery",
                            validated=True,
                        )
                    ],
                )
            ]

        monkeypatch.setattr(coverage_mod, "run_external_agent_discovery", fake_discovery, raising=False)

        design = await build_coverage_test_design(
            modules,
            workspace_id="ws-1",
            repo_path=str(tmp_path),
        )

        assert "tls_recover_session" in requested
        gap = next(g for g in design["gaps"] if g.get("function_name") == "tls_recover_session")
        assert gap["entry_paths"]
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        assert gap["black_box_cases"]
        agent_cases = [
            case for case in gap["black_box_cases"]
            if case.get("provider") == "claude-code"
        ]
        assert agent_cases
        assert agent_cases[0]["turn_id"] == "coverage:tls_recover_session"
        assert agent_cases[0]["source_verification"] == "source_backed"

    async def test_source_window_prefers_path_suffix_over_duplicate_basename(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        decoy = tmp_path / "aaa"
        decoy.mkdir()
        (decoy / "tls.c").write_text(
            "void unrelated_tls(void) {\n"
            "    return;\n"
            "}\n",
            encoding="utf-8",
        )
        tls_dir = tmp_path / "nvmf_tcp" / "transport" / "tls"
        tls_dir.mkdir(parents=True)
        (tls_dir / "tls.c").write_text(
            "void nvmf_tcp_tls_recover(void) {\n"
            "    recover_tls_state();\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "tls,nvmf_tcp,frontend/nof/nvmf_tcp/transport/tls/tls.c:1-3,nvmf_tcp_tls_recover,false,0\n"
        )

        design = await build_coverage_test_design(
            modules,
            workspace_id="ws-1",
            repo_path=str(tmp_path),
        )

        gap = next(g for g in design["gaps"] if g.get("function_name") == "nvmf_tcp_tls_recover")
        assert gap["source_window"]["path"] == "nvmf_tcp/transport/tls/tls.c"
        assert "nvmf_tcp_tls_recover" in json.dumps(gap["source_window"], ensure_ascii=False)

    async def test_source_window_resolves_cuda_source_from_stale_path(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "gpu" / "tls"
        src.mkdir(parents=True)
        (src / "handshake.cu").write_text(
            "void tls_handshake_kernel(int *state) {\n"
            "    if (state == 0) {\n"
            "        return;\n"
            "    }\n"
            "    state[0] = 1;\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "tls,gpu,frontend/nof/gpu/tls/handshake.cu:1-6,tls_handshake_kernel,false,0\n"
        )

        design = await build_coverage_test_design(
            modules,
            workspace_id="ws-1",
            repo_path=str(tmp_path),
        )

        gap = next(g for g in design["gaps"] if g.get("function_name") == "tls_handshake_kernel")
        assert gap["source_window"]["available"] is True
        assert gap["source_window"]["path"] == "gpu/tls/handshake.cu"
        assert "tls_handshake_kernel" in json.dumps(gap["source_window"], ensure_ascii=False)

    async def test_source_window_resolves_schema_source_from_stale_path(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "api" / "billing"
        src.mkdir(parents=True)
        (src / "billing.proto").write_text(
            "syntax = \"proto3\";\n"
            "service Billing {\n"
            "  rpc CreateInvoice(CreateInvoiceRequest) returns (CreateInvoiceReply);\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,api,frontend/nof/api/billing/billing.proto:1-4,CreateInvoice,false,0\n"
        )

        design = await build_coverage_test_design(
            modules,
            workspace_id="ws-1",
            repo_path=str(tmp_path),
        )

        gap = next(g for g in design["gaps"] if g.get("function_name") == "CreateInvoice")
        assert gap["source_window"]["available"] is True
        assert gap["source_window"]["path"] == "api/billing/billing.proto"
        assert "CreateInvoice" in json.dumps(gap["source_window"], ensure_ascii=False)

    async def test_proto_rpc_contract_becomes_black_box_grpc_entry(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "api" / "billing"
        src.mkdir(parents=True)
        (src / "billing.proto").write_text(
            "syntax = \"proto3\";\n"
            "package billing.v1;\n"
            "message CreateInvoiceRequest {\n"
            "  string tenant_id = 1;\n"
            "  int64 amount_cents = 2;\n"
            "}\n"
            "message CreateInvoiceReply { string invoice_id = 1; }\n"
            "service BillingService {\n"
            "  rpc CreateInvoice(CreateInvoiceRequest) returns (CreateInvoiceReply);\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "billing,api,frontend/nof/api/billing/billing.proto:8-10,CreateInvoice,false,0\n"
        )

        design = await build_coverage_test_design(
            modules,
            workspace_id="ws-1",
            repo_path=str(tmp_path),
        )

        gap = next(g for g in design["gaps"] if g.get("function_name") == "CreateInvoice")
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"
        entry = gap["entry_paths"][0]
        assert entry["entry_kind"] == "grpc"
        assert entry["entry_symbol"] == "CreateInvoice"
        assert entry["entry_label"] == "gRPC BillingService/CreateInvoice"
        assert entry["external_trigger"] == "gRPC BillingService/CreateInvoice"
        assert entry["input_hints"] == ["tenant_id", "amount_cents"]
        case_text = json.dumps(gap["black_box_cases"], ensure_ascii=False)
        assert "BillingService/CreateInvoice" in case_text
        assert "tenant_id" in case_text
        assert "amount_cents" in case_text

    async def test_source_window_basename_fallback_prefers_matching_function(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        decoy = tmp_path / "aaa"
        decoy.mkdir()
        (decoy / "tls.c").write_text(
            "void unrelated_tls(void) {\n"
            "    return;\n"
            "}\n",
            encoding="utf-8",
        )
        target = tmp_path / "zzz"
        target.mkdir()
        (target / "tls.c").write_text(
            "void nvmf_tcp_tls_recover(void) {\n"
            "    recover_tls_state();\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "tls,nvmf_tcp,tls.c:1-3,nvmf_tcp_tls_recover,false,0\n"
        )

        design = await build_coverage_test_design(
            modules,
            workspace_id="ws-1",
            repo_path=str(tmp_path),
        )

        gap = next(g for g in design["gaps"] if g.get("function_name") == "nvmf_tcp_tls_recover")
        assert gap["source_window"]["path"] == "zzz/tls.c"
        assert "nvmf_tcp_tls_recover" in json.dumps(gap["source_window"], ensure_ascii=False)

    async def test_source_window_decodes_file_uri_before_suffix_resolution(self, tmp_path):
        from app.adapters.coverage import FunctionHit
        from app.services.coverage_analyzer import _read_source_window

        decoy = tmp_path / "aaa"
        decoy.mkdir()
        (decoy / "payment service.py").write_text(
            "def process_payment(request):\n"
            "    return 'wrong file'\n",
            encoding="utf-8",
        )
        src = tmp_path / "src"
        src.mkdir()
        target = src / "payment service.py"
        target.write_text(
            "def process_payment(request):\n"
            "    return 'correct file'\n",
            encoding="utf-8",
        )
        hit = FunctionHit(
            function_name="process_payment",
            file_path=target.as_uri(),
            line_start=1,
            triggered=False,
            hit_count=0,
        )

        window = _read_source_window(tmp_path, hit)

        assert window is not None
        assert window["path"] == "src/payment service.py"
        assert "correct file" in window["text"]
        assert "wrong file" not in window["text"]

    async def test_source_window_strips_compiler_style_line_column_suffix(self, tmp_path):
        from app.adapters.coverage import FunctionHit
        from app.services.coverage_analyzer import _normalize_coverage_source_path, _read_source_window

        assert _normalize_coverage_source_path("src/service.c:1:7") == "src/service.c"

        src = tmp_path / "src"
        src.mkdir()
        (src / "service.c").write_text(
            "void recover_service(void) {\n"
            "    recover_state();\n"
            "}\n",
            encoding="utf-8",
        )
        hit = FunctionHit(
            function_name="recover_service",
            file_path="src/service.c:1:7",
            line_start=1,
            triggered=False,
            hit_count=0,
        )

        window = _read_source_window(tmp_path, hit)

        assert window is not None
        assert window["path"] == "src/service.c"
        assert "recover_state" in window["text"]

    async def test_source_window_strips_file_symbol_suffix_before_function_fallback(self, tmp_path):
        from app.adapters.coverage import FunctionHit
        from app.services.coverage_analyzer import _normalize_coverage_source_path, _read_source_window

        assert _normalize_coverage_source_path("src/service.py:normalize_record") == "src/service.py"

        decoy = tmp_path / "aaa"
        decoy.mkdir()
        (decoy / "other.py").write_text(
            "def normalize_record(record):\n"
            "    return 'wrong file'\n",
            encoding="utf-8",
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "service.py").write_text(
            "def normalize_record(record):\n"
            "    return 'correct file'\n",
            encoding="utf-8",
        )
        hit = FunctionHit(
            function_name="normalize_record",
            file_path="src/service.py:normalize_record",
            line_start=1,
            triggered=False,
            hit_count=0,
        )

        window = _read_source_window(tmp_path, hit)

        assert window is not None
        assert window["path"] == "src/service.py"
        assert "correct file" in window["text"]
        assert "wrong file" not in window["text"]

    async def test_source_window_reads_assembly_label_source_files(self, tmp_path):
        from app.adapters.coverage import FunctionHit
        from app.services.coverage_analyzer import _read_source_window

        src = tmp_path / "arch" / "x86" / "tls"
        src.mkdir(parents=True)
        (src / "tls_switch.S").write_text(
            ".globl tls_switch\n"
            "tls_switch:\n"
            "    ret\n",
            encoding="utf-8",
        )
        hit = FunctionHit(
            function_name="tls_switch",
            file_path="arch/x86/tls/tls_switch.S",
            line_start=2,
            triggered=False,
            hit_count=0,
        )

        window = _read_source_window(tmp_path, hit)

        assert window is not None
        assert window["path"] == "arch/x86/tls/tls_switch.S"
        assert "tls_switch:" in window["text"]

    async def test_source_window_resolves_dotted_module_symbol_before_global_fallback(self, tmp_path):
        from app.adapters.coverage import FunctionHit
        from app.services.coverage_analyzer import _read_source_window

        decoy = tmp_path / "aaa"
        decoy.mkdir()
        (decoy / "other.py").write_text(
            "def handle(request):\n"
            "    return 'wrong file'\n",
            encoding="utf-8",
        )
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "module.py").write_text(
            "def handle(request):\n"
            "    return 'correct module file'\n",
            encoding="utf-8",
        )
        hit = FunctionHit(
            function_name="handle",
            file_path="pkg.module:handle",
            line_start=1,
            triggered=False,
            hit_count=0,
        )

        window = _read_source_window(tmp_path, hit)

        assert window is not None
        assert window["path"] == "pkg/module.py"
        assert "correct module file" in window["text"]
        assert "wrong file" not in window["text"]

    async def test_source_window_normalizes_remote_code_urls(self, tmp_path):
        from app.adapters.coverage import FunctionHit
        from app.services.coverage_analyzer import _normalize_coverage_source_path, _read_source_window

        github_url = "https://github.com/acme/project/blob/main/src/service.c#L1-L3"
        gitlab_url = "https://gitlab.local/acme/project/-/blob/main/src/service.c?ref=main#L1"
        assert _normalize_coverage_source_path(github_url) == "src/service.c"
        assert _normalize_coverage_source_path(gitlab_url) == "src/service.c"

        decoy = tmp_path / "docs" / "src"
        decoy.mkdir(parents=True)
        (decoy / "service.c").write_text(
            "void recover_service(void) {\n"
            "    wrong_documented_example();\n"
            "}\n",
            encoding="utf-8",
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "service.c").write_text(
            "void recover_service(void) {\n"
            "    recover_state();\n"
            "}\n",
            encoding="utf-8",
        )
        hit = FunctionHit(
            function_name="recover_service",
            file_path=github_url,
            line_start=1,
            triggered=False,
            hit_count=0,
        )

        window = _read_source_window(tmp_path, hit)

        assert window is not None
        assert window["path"] == "src/service.c"
        assert "recover_state" in window["text"]
        assert "wrong_documented_example" not in window["text"]

    async def test_source_window_uses_workspace_scope_candidate_when_coverage_path_is_stale(
        self,
        tmp_path,
        monkeypatch,
    ):
        import app.services.coverage_analyzer as coverage_mod
        from app.services.coverage_analyzer import build_coverage_test_design

        legacy = tmp_path / "aaa_legacy"
        legacy.mkdir()
        (legacy / "legacy_tls.c").write_text(
            "void nvmf_tcp_tls_recover(void) {\n"
            "    legacy_recover_tls_state();\n"
            "}\n",
            encoding="utf-8",
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "actual_tls.c").write_text(
            "void nvmf_tcp_tls_recover(void) {\n"
            "    recover_tls_state();\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "tls,nvmf_tcp,stale/generated/tls.c:1-3,nvmf_tcp_tls_recover,false,0\n"
        )
        hit_key = "stale/generated/tls.c:nvmf_tcp_tls_recover:1"

        async def fake_scope(*_args, **_kwargs):
            return {
                hit_key: {
                    "gitnexus_available": False,
                    "candidate_files": [{"path": "src/actual_tls.c"}],
                    "candidate_symbols": [],
                    "related_communities": [],
                    "warnings": [],
                }
            }

        monkeypatch.setattr(coverage_mod, "_resolve_workspace_scope_for_hits", fake_scope)

        design = await build_coverage_test_design(
            modules,
            workspace_id="ws-1",
            repo_path=str(tmp_path),
        )

        gap = next(g for g in design["gaps"] if g.get("function_name") == "nvmf_tcp_tls_recover")
        assert gap["source_window"]["path"] == "src/actual_tls.c"
        assert "legacy_recover_tls_state" not in json.dumps(gap["source_window"], ensure_ascii=False)
        assert gap["entry_trace_status"] != "source_not_found"

    async def test_source_window_uses_workspace_scope_symbol_path_when_file_candidates_empty(
        self,
        tmp_path,
        monkeypatch,
    ):
        import app.services.coverage_analyzer as coverage_mod
        from app.services.coverage_analyzer import build_coverage_test_design

        legacy = tmp_path / "aaa_legacy"
        legacy.mkdir()
        (legacy / "legacy_tls.c").write_text(
            "void nvmf_tcp_tls_recover(void) {\n"
            "    legacy_recover_tls_state();\n"
            "}\n",
            encoding="utf-8",
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "symbol_tls.c").write_text(
            "void nvmf_tcp_tls_recover(void) {\n"
            "    recover_tls_state();\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "tls,nvmf_tcp,stale/generated/tls.c:1-3,nvmf_tcp_tls_recover,false,0\n"
        )
        hit_key = "stale/generated/tls.c:nvmf_tcp_tls_recover:1"

        async def fake_scope(*_args, **_kwargs):
            return {
                hit_key: {
                    "gitnexus_available": True,
                    "candidate_files": [],
                    "candidate_symbols": [
                        {"symbol": "nvmf_tcp_tls_recover", "path": "src/symbol_tls.c"}
                    ],
                    "related_communities": [],
                    "warnings": [],
                }
            }

        monkeypatch.setattr(coverage_mod, "_resolve_workspace_scope_for_hits", fake_scope)

        design = await build_coverage_test_design(
            modules,
            workspace_id="ws-1",
            repo_path=str(tmp_path),
        )

        gap = next(g for g in design["gaps"] if g.get("function_name") == "nvmf_tcp_tls_recover")
        assert gap["source_window"]["path"] == "src/symbol_tls.c"
        assert "legacy_recover_tls_state" not in json.dumps(gap["source_window"], ensure_ascii=False)

    async def test_source_window_anchors_on_definition_when_coverage_line_is_stale(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        src = tmp_path / "src"
        src.mkdir()
        padding = "\n".join(f"int pad_{idx};" for idx in range(80))
        (src / "tls.c").write_text(
            padding
            + "\nvoid nvmf_tcp_tls_recover(void) {\n"
            "    if (1) { recover_tls_state(); }\n"
            "}\n",
            encoding="utf-8",
        )
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "tls,nvmf_tcp,src/tls.c:1-3,nvmf_tcp_tls_recover,false,0\n"
        )

        design = await build_coverage_test_design(
            modules,
            workspace_id="ws-1",
            repo_path=str(tmp_path),
        )

        gap = next(g for g in design["gaps"] if g.get("function_name") == "nvmf_tcp_tls_recover")
        assert gap["source_window"]["definition_line"] == 81
        assert gap["source_window"]["start"] >= 78
        assert "nvmf_tcp_tls_recover" in json.dumps(gap["source_window"], ensure_ascii=False)

    async def test_source_window_falls_back_when_existing_coverage_file_lacks_function(self, tmp_path):
        from app.services.coverage_analyzer import _read_source_window

        stale = tmp_path / "stale" / "generated"
        stale.mkdir(parents=True)
        (stale / "tls.c").write_text(
            "void unrelated_tls(void) {\n"
            "    return;\n"
            "}\n",
            encoding="utf-8",
        )
        actual = tmp_path / "zzz"
        actual.mkdir()
        (actual / "tls.c").write_text(
            "void nvmf_tcp_tls_recover(void) {\n"
            "    recover_tls_state();\n"
            "}\n",
            encoding="utf-8",
        )
        hit = FunctionHit(
            function_name="nvmf_tcp_tls_recover",
            file_path="stale/generated/tls.c",
            line_start=1,
            line_end=3,
            triggered=False,
            hit_count=0,
        )

        window = _read_source_window(tmp_path, hit)

        assert window is not None
        assert window["path"] == "zzz/tls.c"
        assert "recover_tls_state" in window["text"]

    async def test_external_agent_entry_discovery_respects_global_parallel_limit(
        self,
        tmp_path,
        monkeypatch,
    ):
        import app.services.coverage_analyzer as coverage_mod
        from app.adapters.coverage import FunctionHit, ModuleCoverage
        from app.config import settings
        from app.services.external_agent_discovery import AgentDiscoveryResult

        monkeypatch.setattr(settings, "external_agents_enabled", True)
        monkeypatch.setattr(settings, "external_agent_max_parallel", 2)

        src = tmp_path / "src"
        src.mkdir()
        hits: list[FunctionHit] = []
        for idx in range(6):
            (src / f"recover_{idx}.c").write_text(
                f"void recover_{idx}(void) {{}}\n",
                encoding="utf-8",
            )
            hits.append(
                FunctionHit(
                    function_name=f"recover_{idx}",
                    file_path=f"src/recover_{idx}.c",
                    line_start=1,
                    triggered=False,
                    hit_count=0,
                )
            )
        module = ModuleCoverage(
            module_path="src",
            line_rate=0.0,
            branch_rate=0.0,
            function_rate=0.0,
            function_hits=hits,
        )
        active = 0
        max_active = 0

        async def fake_discovery(_request, **_kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            active -= 1
            return [AgentDiscoveryResult(provider="claude-code", status="ok")]

        monkeypatch.setattr(coverage_mod, "run_external_agent_discovery", fake_discovery, raising=False)

        await coverage_mod._resolve_external_agent_entries_for_hits(
            [(module, hit) for hit in hits],
            repo_path=str(tmp_path),
        )

        assert max_active <= 2

    async def test_unbound_workspace_does_not_fabricate_paths(self, tmp_path):
        from app.services.coverage_analyzer import build_coverage_test_design

        self._make_repo(tmp_path)  # repo exists, but we pass no workspace binding
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "rec,session,src/session.c:1-6,recover_session,false,0\n"
        )
        design = await build_coverage_test_design(
            modules, workspace_id=None, repo_path=None
        )
        assert design["summary"]["workspace_bound"] is False
        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["entry_paths"] == []
        assert gap["trigger_branches"] == []
        assert gap["source_window"] is None
        assert any("未绑定工作区" in w for w in design["warnings"])

    async def test_python_source_scan_traces_entry_when_ripgrep_missing(self, tmp_path, monkeypatch):
        import app.services.coverage_analyzer as mod
        from app.services.coverage_analyzer import build_coverage_test_design

        self._make_repo(tmp_path)
        monkeypatch.setattr(mod.shutil, "which", lambda _name: None)  # rg unavailable
        modules = self._modules(
            "feature,module,code_location,function,triggered,hit_count\n"
            "rec,session,src/session.c:1-6,recover_session,false,0\n"
        )
        design = await build_coverage_test_design(
            modules, workspace_id="ws-1", repo_path=str(tmp_path)
        )
        ts = design["summary"]["tool_status"]
        assert ts["ripgrep"] == "unavailable"
        assert ts["joern"].startswith("unavailable")
        # Source window and caller tracing still work through bounded Python source scan.
        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["entry_paths"]
        assert any(entry.get("tool") == "source-scan" for entry in gap["entry_paths"])
        assert gap["gray_box_required"] is False
        assert gap["entry_trace_status"] == "entry_found"
        assert gap["entry_discovery"]["entry_trace_status"] == "entry_found"
        assert gap["entry_discovery"]["candidate_external_entries"][0]["tool"] == "source-scan"
        assert gap["entry_discovery"]["candidate_external_entries"][0]["confidence"] == "high"
        assert gap["black_box_readiness"]["case_type"] == "black_box_ready"

    async def test_branch_gaps_designed_from_conditions(self, tmp_path):
        from app.adapters.coverage import ModuleCoverage
        from app.services.coverage_analyzer import build_coverage_test_design

        module = ModuleCoverage(
            module_path="app/calc",
            line_rate=0.5,
            branch_rate=0.4,
            function_rate=1.0,
            uncovered_branches=["app/calc.py:L15 if (value < 0)"],
        )
        design = await build_coverage_test_design(
            [module], workspace_id="ws-1", repo_path=str(tmp_path)
        )
        branch_gaps = [g for g in design["gaps"] if g.get("kind") == "branch"]
        assert len(branch_gaps) == 1
        assert branch_gaps[0]["black_box_cases"]
        assert branch_gaps[0]["black_box_readiness"]["case_type"] == "black_box_hypothesis"
        assert branch_gaps[0]["test_case_drafts"][0]["case_type"] == "black_box_hypothesis"
        assert design["summary"]["uncovered_branch_count"] == 1

    async def test_white_box_leak_lint_flags_black_box_execution_terms(self):
        drafts = [{
            "case_type": "black_box_ready",
            "test_execution": {
                "title": "Cover parse_psk branch",
                "external_trigger": "call nvme_tcp_parse_interchange_psk()",
                "preconditions": "source lib/nvme/nvme_tcp.c:2758 exists",
                "inputs": "set ctrlr->opts.tls_psk and make if (hash == 0) true",
                "steps": ["mock internal function", "覆盖分支"],
                "expected": "returns 0",
                "observable_signals": ["logs"],
            },
        }]

        result = _lint_test_case_drafts(drafts)

        assert result["passed"] is False
        rules = {finding["rule"] for finding in result["findings"]}
        assert {"function_call", "source_path", "branch_expression", "private_member", "gray_box_action"} <= rules

    async def test_white_box_leak_lint_flags_all_supported_source_paths(self):
        drafts = [{
            "case_type": "black_box_ready",
            "test_execution": {
                "title": "Cover payment branch",
                "external_trigger": "Send a public API request.",
                "preconditions": "source src/PaymentService.scala:42 exists",
                "inputs": "Follow src/PaymentController.swift:15 and src/routes.kts:8",
                "steps": ["Observe src/handler.rb:7 before retrying"],
                "expected": "The request returns a controlled error.",
                "observable_signals": ["logs mention src/Fallback.php:9"],
            },
        }]

        result = _lint_test_case_drafts(drafts)

        assert result["passed"] is False
        source_findings = [
            finding["text"]
            for finding in result["findings"]
            if finding["rule"] == "source_path"
        ]
        assert any(text.endswith(".scala:42") for text in source_findings)

    async def test_white_box_leak_lint_flags_schema_source_paths(self):
        for source_path in (
            "api/billing.proto:3",
            "api/schema.graphql:12",
            "api/events.gql:4",
            "idl/payment.thrift:8",
        ):
            drafts = [{
                "case_type": "black_box_ready",
                "test_execution": {
                    "title": "Cover public contract branch",
                    "external_trigger": "Send a public API request.",
                    "preconditions": f"Follow {source_path} before sending the request.",
                    "inputs": "Use a boundary request value.",
                    "steps": ["Send the request and observe the response."],
                    "expected": "The request returns a controlled error.",
                    "observable_signals": ["public response", "service logs"],
                },
            }]

            result = _lint_test_case_drafts(drafts)

            assert result["passed"] is False
            assert any(
                finding["rule"] == "source_path" and finding["text"] == source_path
                for finding in result["findings"]
            )

    async def test_white_box_lint_allows_public_rpc_and_cli_entry_names(self):
        drafts = [{
            "case_type": "black_box_ready",
            "test_execution": {
                "title": "Exercise public NVMe/TCP TLS setup entry",
                "external_trigger": "Send JSON-RPC bdev_malloc_create() through the management API.",
                "preconditions": "The public RPC service and NVMe/TCP target are running.",
                "inputs": "Use a valid JSON-RPC request and a boundary TLS PSK value.",
                "steps": [
                    "Run CLI spdk_nvme_perf(...) with the documented TLS parameters.",
                    "Observe the client-visible connection result and logs.",
                ],
                "expected": "The request returns a controlled success or validation error.",
                "observable_signals": ["JSON-RPC response", "CLI exit code", "target logs"],
            },
        }]

        result = _lint_test_case_drafts(drafts)

        assert result["passed"] is True
        assert result["findings"] == []

    async def test_ai_black_box_hypothesis_rejects_internal_call_in_steps(self):
        scenario = {
            "scenario_id": "S-hyp",
            "priority": "high",
            "case_type": "black_box_hypothesis",
            "flow_purpose": "验证外部请求失败后的恢复路径。",
            "external_trigger": "通过公开 API 发起失败请求。",
            "input_construction": "准备边界请求。",
            "normal_path": "服务端处理请求时调用 recover_session 并返回成功。",
            "error_path": "失败请求返回错误码。",
            "key_call_chain": ["recover_session"],
            "expected_result": "返回受控错误。",
            "observable_signals": ["返回码", "日志"],
            "gray_box_aid": "查看 trace 日志。",
            "sfmea": {
                "failure_mode": "恢复未触发",
                "trigger_condition": "依赖失败",
                "propagation_effect": "状态残留",
                "observable_effect": "返回码和日志异常",
                "recommended_test": "构造失败请求并观察外部状态",
            },
            "evidence_refs": ["coverage:recover_session"],
            "related_gaps": ["recover_session"],
            "confidence": "medium",
            "verification_gaps": [],
        }

        assert "黑盒步骤包含内部函数" in (_scenario_rejection_reason(scenario) or "")

    async def test_ai_normalization_keeps_string_fields_as_single_items(self):
        scenario = {
            field: "x" for field in (
                "scenario_id", "priority", "case_type", "flow_purpose",
                "external_trigger", "input_construction", "normal_path",
                "error_path", "expected_result", "gray_box_aid",
                "confidence",
            )
        }
        scenario.update({
            "key_call_chain": "src/a.c:1",
            "observable_signals": "日志关键字和返回码",
            "evidence_refs": "src/a.c:1-3",
            "related_gaps": "recover_session",
            "verification_gaps": "需要确认外部入口",
            "sfmea": {
                "failure_mode": "x",
                "trigger_condition": "x",
                "propagation_effect": "x",
                "observable_effect": "x",
                "recommended_test": "x",
            },
        })

        normalized = _normalize_ai_scenario(scenario)

        assert normalized["observable_signals"] == ["日志关键字和返回码"]
        assert normalized["evidence_refs"] == ["src/a.c:1-3"]
        assert normalized["related_gaps"] == ["recover_session"]

    async def test_ai_normalization_promotes_executable_hypothesis_to_black_box_ready(self):
        scenario = {
            "scenario_id": "S-ready",
            "priority": "high",
            "case_type": "black_box_hypothesis",
            "flow_purpose": "验证登录失败时返回受控错误。",
            "external_trigger": "通过公开客户端发起登录请求。",
            "input_construction": "把目标名称设置为不存在的名称。",
            "normal_path": "启动服务后使用有效目标名称登录，连接建立成功。",
            "error_path": "启动服务后使用不存在的目标名称登录，登录被拒绝。",
            "key_call_chain": ["login_check_target"],
            "expected_result": "客户端收到登录失败响应，服务端记录目标不存在日志。",
            "observable_signals": ["返回码", "日志关键字", "连接状态"],
            "gray_box_aid": "可辅助查看 trace 日志，但不是执行步骤。",
            "sfmea": {
                "failure_mode": "目标不存在",
                "trigger_condition": "目标名称配置错误",
                "propagation_effect": "登录失败",
                "observable_effect": "返回错误响应和日志",
                "recommended_test": "使用无效目标名称发起登录",
            },
            "evidence_refs": ["lib/iscsi/iscsi.c:1403"],
            "related_gaps": ["login_check_target"],
            "confidence": "medium",
            "verification_gaps": [],
        }

        assert _scenario_is_executable_black_box(scenario) is True
        normalized = _normalize_ai_scenario(scenario)
        assert normalized["case_type"] == "black_box_ready"
        assert "classification_reason" in normalized

    async def test_black_box_ready_allows_empty_gray_box_and_verification_gap(self):
        scenario = {
            "scenario_id": "S-ready",
            "priority": "high",
            "case_type": "black_box_ready",
            "flow_purpose": "验证外部配置错误返回受控错误。",
            "external_trigger": "通过 JSON-RPC 发送配置请求。",
            "input_construction": "把配置字段设置为不存在的对象。",
            "normal_path": "发送有效配置请求后返回成功。",
            "error_path": "发送无效配置请求后返回错误。",
            "key_call_chain": "rpc_handler -> validate_config",
            "expected_result": "返回错误响应并记录日志。",
            "observable_signals": "JSON-RPC error 字段和日志关键字",
            "gray_box_aid": "",
            "sfmea": {
                "failure_mode": "配置对象不存在",
                "trigger_condition": "请求引用不存在的配置对象",
                "propagation_effect": "配置失败且状态不变",
                "observable_effect": "返回错误响应和日志",
                "recommended_test": "发送无效配置对象请求",
            },
            "evidence_refs": "src/config.c:10",
            "related_gaps": "validate_config",
            "confidence": "high",
            "verification_gaps": "",
        }

        assert _scenario_rejection_reason(scenario) is None
        normalized = _normalize_ai_scenario(scenario)
        assert normalized["case_type"] == "black_box_ready"
        assert normalized["gray_box_aid"].startswith("不需要灰盒辅助")
        assert normalized["verification_gaps"] == []
        scenario["verification_gaps"] = "无"
        assert _normalize_ai_scenario(scenario)["verification_gaps"] == []

    async def test_black_box_lint_allows_protocol_domain_identifiers(self):
        scenario = {
            "external_trigger": "iSCSI 发起端发送登录请求。",
            "input_construction": "TargetName 设置为 'iqn.2024-01.com.example:nonexistent'。",
            "normal_path": "使用有效目标名登录成功。",
            "error_path": "使用不存在目标名登录失败。",
            "expected_result": "发起端收到 Target Not Found 响应。",
            "observable_signals": ["登录响应状态类", "目标端日志"],
        }

        assert _black_box_scenario_has_white_box_leak(scenario) is False

    async def test_ai_black_box_leak_flags_supported_source_paths(self):
        scenario = {
            "external_trigger": "Send a public API request.",
            "input_construction": "Use boundary input from src/PaymentService.scala:42.",
            "normal_path": "The public request succeeds.",
            "error_path": "The public request returns a controlled error.",
            "expected_result": "The client sees a response and logs are emitted.",
            "observable_signals": ["service log references src/PaymentController.swift:15"],
        }

        assert _black_box_scenario_has_white_box_leak(scenario) is True

    async def test_ai_black_box_leak_flags_bare_internal_function_call(self):
        scenario = {
            "external_trigger": "Send a public API request.",
            "input_construction": "Use a boundary request value.",
            "normal_path": "recover_session() returns success after the public request.",
            "error_path": "The public request returns a controlled error.",
            "expected_result": "The client sees a response and logs are emitted.",
            "observable_signals": ["client response", "service logs"],
        }

        assert _black_box_scenario_has_white_box_leak(scenario) is True
