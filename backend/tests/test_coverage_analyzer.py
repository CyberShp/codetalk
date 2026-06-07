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
        assert design["summary"]["recommendation_source"] == "none"
        assert design["summary"]["black_box_ready_count"] == 0
        assert design["summary"]["gray_box_required_count"] == 0
        assert design["summary"]["gap_gray_box_required_count"] == 1
        assert gap["ai_generation_status"] == "available"
        assert gap["ai_recommendation_status"] == "no_valid_ai_scenarios"
        assert gap["deterministic_case_role"] == "evidence_scaffold"

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
        assert any("入口" in g for g in gap["evidence_gaps"])
        assert design["summary"]["gray_box_required_count"] == 1

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

    async def test_tool_unavailable_markers_when_ripgrep_missing(self, tmp_path, monkeypatch):
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
        # Source window still works (filesystem read), but no caller trace.
        gap = [g for g in design["gaps"] if g.get("kind") == "function"][0]
        assert gap["entry_paths"] == []
        assert gap["gray_box_required"] is False
        assert gap["entry_trace_status"] == "tool_unavailable"
        assert gap["entry_discovery"]["entry_trace_status"] == "tool_unavailable"

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
