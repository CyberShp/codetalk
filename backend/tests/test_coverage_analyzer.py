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
    _lint_test_case_drafts,
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
        assert gap["gray_box_required"] is True

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
