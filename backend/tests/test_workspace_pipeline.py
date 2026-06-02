"""Sprint 3: workspace_pipeline service tests.

Tests for _read_material_file (pure), _harvest_reports (DB integration),
and WorkspacePipeline.run (orchestration with mocked AnalysisPipeline).
"""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import aiosqlite
import pytest

from app.services.workspace_pipeline import (
    WorkspacePipeline,
    _read_material_file,
)

pytestmark = [pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# _read_material_file — pure function tests
# ---------------------------------------------------------------------------


class TestReadMaterialFile:
    def test_reads_normal_file(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("Hello world", encoding="utf-8")
        assert _read_material_file(str(f)) == "Hello world"

    def test_returns_empty_for_nonexistent(self, tmp_path):
        assert _read_material_file(str(tmp_path / "nope.md")) == ""

    def test_returns_empty_for_directory(self, tmp_path):
        d = tmp_path / "subdir"
        d.mkdir()
        assert _read_material_file(str(d)) == ""

    def test_truncates_large_file(self, tmp_path):
        f = tmp_path / "big.md"
        f.write_bytes(b"A" * 200)
        result = _read_material_file(str(f), max_bytes=100)
        assert len(result) < 200
        assert result.endswith("…（已截断）")

    def test_within_limit_not_truncated(self, tmp_path):
        f = tmp_path / "small.md"
        f.write_text("short", encoding="utf-8")
        result = _read_material_file(str(f), max_bytes=1000)
        assert result == "short"
        assert "截断" not in result

    def test_oserror_returns_empty(self, tmp_path):
        """Lines 32-33: OSError during file read returns empty string."""
        from pathlib import Path as _Path

        f = tmp_path / "error.md"
        f.write_text("data", encoding="utf-8")
        with patch.object(_Path, "read_text", side_effect=OSError("permission denied")):
            result = _read_material_file(str(f))
        assert result == ""


# ---------------------------------------------------------------------------
# _harvest_reports — DB integration
# ---------------------------------------------------------------------------


class TestHarvestReports:
    async def test_ingests_existing_report_files(self, sqlite_db):
        ws_id = "ws-harv"
        task_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'harv', '/r', 1, ?, ?)",
                (ws_id, now, now),
            )
            await db.commit()

        from app.config import settings

        output_dir = settings.outputs_path / task_id
        output_dir.mkdir(parents=True)
        (output_dir / "01-项目与模块地图.md").write_text(
            "# Map", encoding="utf-8"
        )
        (output_dir / "02-关键业务流程分析.md").write_text(
            "# Flow", encoding="utf-8"
        )

        pipeline = WorkspacePipeline()
        await pipeline._harvest_reports(ws_id, task_id)

        async with aiosqlite.connect(sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM workspace_reports WHERE workspace_id = ?", (ws_id,)
            ) as cur:
                reports = await cur.fetchall()
            async with db.execute(
                "SELECT analyze_status, analyze_progress FROM workspaces WHERE id = ?",
                (ws_id,),
            ) as cur:
                ws = await cur.fetchone()

        assert len(reports) == 2
        types = {r["report_type"] for r in reports}
        assert types == {"module_map", "business_flow"}
        assert ws["analyze_status"] == "done"
        assert ws["analyze_progress"] == 100

    async def test_skips_missing_files(self, sqlite_db):
        ws_id = "ws-harv2"
        task_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'harv', '/r', 1, ?, ?)",
                (ws_id, now, now),
            )
            await db.commit()

        from app.config import settings

        output_dir = settings.outputs_path / task_id
        output_dir.mkdir(parents=True)

        pipeline = WorkspacePipeline()
        await pipeline._harvest_reports(ws_id, task_id)

        async with aiosqlite.connect(sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM workspace_reports WHERE workspace_id = ?", (ws_id,)
            ) as cur:
                reports = await cur.fetchall()

        assert len(reports) == 0

    async def test_deletes_old_reports_before_harvesting(self, sqlite_db):
        ws_id = "ws-harv3"
        task_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'harv', '/r', 1, ?, ?)",
                (ws_id, now, now),
            )
            await db.execute(
                "INSERT INTO workspace_reports "
                "(id, workspace_id, report_type, title, content, status, created_at) "
                "VALUES ('old-r', ?, 'module_map', 'old', 'stale', 'completed', ?)",
                (ws_id, now),
            )
            await db.commit()

        from app.config import settings

        output_dir = settings.outputs_path / task_id
        output_dir.mkdir(parents=True)
        (output_dir / "01-项目与模块地图.md").write_text(
            "# New", encoding="utf-8"
        )

        pipeline = WorkspacePipeline()
        await pipeline._harvest_reports(ws_id, task_id)

        async with aiosqlite.connect(sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM workspace_reports WHERE workspace_id = ?", (ws_id,)
            ) as cur:
                reports = await cur.fetchall()

        assert len(reports) == 1
        assert reports[0]["content"] == "# New"
        assert reports[0]["id"] != "old-r"


# ---------------------------------------------------------------------------
# WorkspacePipeline.run — orchestration with mocked AnalysisPipeline
# ---------------------------------------------------------------------------


class TestPipelineRun:
    async def test_shadow_task_lifecycle(self, sqlite_db):
        ws_id = "ws-run"
        now = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'run', '/repo', 1, ?, ?)",
                (ws_id, now, now),
            )
            await db.commit()

        captured_task_ids = []

        async def mock_pipeline_run(self_inner, task_id):
            captured_task_ids.append(task_id)
            from app.config import settings

            output_dir = settings.outputs_path / task_id
            output_dir.mkdir(parents=True)
            (output_dir / "01-项目与模块地图.md").write_text(
                "# Test", encoding="utf-8"
            )

        with patch(
            "app.services.analysis_pipeline.AnalysisPipeline.run",
            mock_pipeline_run,
        ):
            pipeline = WorkspacePipeline()
            await pipeline.run(ws_id, "/repo")

        assert len(captured_task_ids) == 1

        async with aiosqlite.connect(sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM tasks") as cur:
                tasks = await cur.fetchall()
            async with db.execute(
                "SELECT * FROM workspace_reports WHERE workspace_id = ?", (ws_id,)
            ) as cur:
                reports = await cur.fetchall()

        assert len(tasks) == 1
        assert tasks[0]["id"] == captured_task_ids[0]
        assert tasks[0]["workspace_id"] == ws_id
        assert len(reports) == 1

    async def test_material_classification(self, sqlite_db, tmp_path):
        ws_id = "ws-mat"
        now = datetime.now(timezone.utc).isoformat()

        req_file = tmp_path / "req.md"
        req_file.write_text("# Requirements", encoding="utf-8")
        design_file = tmp_path / "arch.md"
        design_file.write_text("# Architecture", encoding="utf-8")

        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'mat', '/repo', 1, ?, ?)",
                (ws_id, now, now),
            )
            await db.execute(
                "INSERT INTO workspace_materials "
                "(id, workspace_id, filename, content_type, file_path, is_active, created_at) "
                "VALUES ('m-req', ?, 'req.md', 'requirements', ?, TRUE, ?)",
                (ws_id, str(req_file), now),
            )
            await db.execute(
                "INSERT INTO workspace_materials "
                "(id, workspace_id, filename, content_type, file_path, is_active, created_at) "
                "VALUES ('m-des', ?, 'arch.md', 'design', ?, TRUE, ?)",
                (ws_id, str(design_file), now),
            )
            await db.commit()

        captured = {}

        async def mock_pipeline_run(self_inner, task_id):
            async with aiosqlite.connect(sqlite_db) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT requirements_doc, design_doc, material_ids FROM tasks WHERE id = ?",
                    (task_id,),
                ) as cur:
                    row = await cur.fetchone()
                captured["requirements_doc"] = row["requirements_doc"]
                captured["design_doc"] = row["design_doc"]
                captured["material_ids"] = row["material_ids"]

        with patch(
            "app.services.analysis_pipeline.AnalysisPipeline.run",
            mock_pipeline_run,
        ):
            pipeline = WorkspacePipeline()
            await pipeline.run(ws_id, "/repo")

        assert "# Requirements" in captured["requirements_doc"]
        assert "# Architecture" in captured["design_doc"]
        ids = json.loads(captured["material_ids"])
        assert set(ids) == {"m-req", "m-des"}

    async def test_inactive_materials_excluded(self, sqlite_db, tmp_path):
        ws_id = "ws-inact"
        now = datetime.now(timezone.utc).isoformat()

        f = tmp_path / "inactive.md"
        f.write_text("should not appear", encoding="utf-8")

        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'inact', '/repo', 1, ?, ?)",
                (ws_id, now, now),
            )
            await db.execute(
                "INSERT INTO workspace_materials "
                "(id, workspace_id, filename, content_type, file_path, is_active, created_at) "
                "VALUES ('m-off', ?, 'inactive.md', 'other', ?, FALSE, ?)",
                (ws_id, str(f), now),
            )
            await db.commit()

        captured = {}

        async def mock_pipeline_run(self_inner, task_id):
            async with aiosqlite.connect(sqlite_db) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT material_ids FROM tasks WHERE id = ?", (task_id,)
                ) as cur:
                    row = await cur.fetchone()
                captured["material_ids"] = row["material_ids"]

        with patch(
            "app.services.analysis_pipeline.AnalysisPipeline.run",
            mock_pipeline_run,
        ):
            pipeline = WorkspacePipeline()
            await pipeline.run(ws_id, "/repo")

        assert captured["material_ids"] is None

    async def test_empty_content_material_skipped(self, sqlite_db, tmp_path):
        """Line 61: material whose file doesn't exist gets an empty content string,
        triggering the 'if not content: continue' skip — material_id is still tracked
        but content doesn't appear in requirements_doc."""
        ws_id = "ws-empty-content"
        now = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'ec', '/repo', 1, ?, ?)",
                (ws_id, now, now),
            )
            await db.execute(
                "INSERT INTO workspace_materials "
                "(id, workspace_id, filename, content_type, file_path, is_active, created_at) "
                "VALUES ('m-missing', ?, 'ghost.md', 'requirements', ?, TRUE, ?)",
                (ws_id, str(tmp_path / "does_not_exist.md"), now),
            )
            await db.commit()

        captured = {}

        async def mock_pipeline_run(self_inner, task_id):
            async with aiosqlite.connect(sqlite_db) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT requirements_doc, material_ids FROM tasks WHERE id = ?",
                    (task_id,),
                ) as cur:
                    row = await cur.fetchone()
                captured["requirements_doc"] = row["requirements_doc"]
                captured["material_ids"] = row["material_ids"]

        with patch(
            "app.services.analysis_pipeline.AnalysisPipeline.run",
            mock_pipeline_run,
        ):
            pipeline = WorkspacePipeline()
            await pipeline.run(ws_id, "/repo")

        assert captured["requirements_doc"] is None
        ids = json.loads(captured["material_ids"])
        assert "m-missing" in ids

    async def test_other_content_type_goes_to_requirements(self, sqlite_db, tmp_path):
        """Line 68: materials with content_type not in ('requirements', 'design')
        fall through to the else branch and are appended to requirements_parts."""
        ws_id = "ws-other-type"
        now = datetime.now(timezone.utc).isoformat()

        other_file = tmp_path / "other.md"
        other_file.write_text("# Other Content", encoding="utf-8")

        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'ot', '/repo', 1, ?, ?)",
                (ws_id, now, now),
            )
            await db.execute(
                "INSERT INTO workspace_materials "
                "(id, workspace_id, filename, content_type, file_path, is_active, created_at) "
                "VALUES ('m-other', ?, 'other.md', 'changelog', ?, TRUE, ?)",
                (ws_id, str(other_file), now),
            )
            await db.commit()

        captured = {}

        async def mock_pipeline_run(self_inner, task_id):
            async with aiosqlite.connect(sqlite_db) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT requirements_doc, design_doc FROM tasks WHERE id = ?",
                    (task_id,),
                ) as cur:
                    row = await cur.fetchone()
                captured["requirements_doc"] = row["requirements_doc"]
                captured["design_doc"] = row["design_doc"]

        with patch(
            "app.services.analysis_pipeline.AnalysisPipeline.run",
            mock_pipeline_run,
        ):
            pipeline = WorkspacePipeline()
            await pipeline.run(ws_id, "/repo")

        assert "# Other Content" in captured["requirements_doc"]
        assert captured["design_doc"] is None

    async def test_cleanup_on_analysis_failure(self, sqlite_db):
        ws_id = "ws-fail"
        now = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed, created_at, updated_at) "
                "VALUES (?, 'fail', '/repo', 1, ?, ?)",
                (ws_id, now, now),
            )
            await db.commit()

        async def mock_raise(self_inner, task_id):
            raise RuntimeError("analysis boom")

        with patch(
            "app.services.analysis_pipeline.AnalysisPipeline.run",
            mock_raise,
        ):
            pipeline = WorkspacePipeline()
            with pytest.raises(RuntimeError, match="analysis boom"):
                await pipeline.run(ws_id, "/repo")

        async with aiosqlite.connect(sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM tasks") as cur:
                tasks = await cur.fetchall()

        assert len(tasks) == 1
        assert tasks[0]["workspace_id"] == ws_id


class TestApplyCoverageTestDesign:
    """WorkspacePipeline folds analyzed coverage into the test_design report."""

    @staticmethod
    def _make_repo(tmp_path):
        repo = tmp_path / "repo"
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "api.c").write_text(
            "int api_handle_request(req_t *r) {\n"
            "    if (do_work(r) < 0) {\n"
            "        recover_session(r->s);\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        (repo / "src" / "session.c").write_text(
            "void recover_session(s_t *s) {\n"
            "    if (s == NULL) { return; }\n"
            "    cleanup(s);\n"
            "}\n",
            encoding="utf-8",
        )
        return repo

    async def _seed(self, sqlite_db, ws_id, cov_id, repo):
        from app.adapters.coverage import parse_internal_function_hits
        from app.services.coverage_analyzer import _module_to_dict

        report = parse_internal_function_hits(
            "feature,module,code_location,function,triggered,hit_count\n"
            "rec,session,src/session.c:1-4,recover_session,false,0\n"
        )
        modules_json = json.dumps(
            [_module_to_dict(m) for m in report.modules], ensure_ascii=False
        )
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed) VALUES (?,?,?,1)",
                (ws_id, "WS", str(repo)),
            )
            await db.execute(
                "INSERT INTO coverage_analyses "
                "(id, name, status, workspace_id, repo_path, modules_json, source_format) "
                "VALUES (?,?,?,?,?,?,?)",
                (cov_id, "cov", "analyzed", ws_id, str(repo), modules_json,
                 "internal_function_hits"),
            )
            await db.commit()

    async def test_writes_artifact_and_appends_report_section(self, sqlite_db, tmp_path):
        from app.config import settings

        ws_id, cov_id, task_id = "ws-ctd", "cov-ctd", str(uuid.uuid4())
        repo = self._make_repo(tmp_path)
        await self._seed(sqlite_db, ws_id, cov_id, repo)

        output_dir = settings.outputs_path / task_id
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "report_manifest.json").write_text(
            json.dumps({"reports": [{"report_type": "test_design",
                                     "filename": "15-测试视角代码理解.md",
                                     "status": "completed"}]}),
            encoding="utf-8",
        )
        report_file = output_dir / "15-测试视角代码理解.md"
        report_file.write_text("# 测试视角代码理解\n\n（LLM 内容）\n", encoding="utf-8")

        await WorkspacePipeline()._apply_coverage_test_design(
            ws_id, str(repo), task_id, None
        )

        artifact = output_dir / "coverage_test_design.json"
        assert artifact.exists()
        design = json.loads(artifact.read_text(encoding="utf-8"))
        assert design["version"] == "coverage-test-design-v1"
        assert design["summary"]["uncovered_function_count"] == 1

        report_md = report_file.read_text(encoding="utf-8")
        assert "覆盖率缺口测试设计矩阵" in report_md
        assert "recover_session" in report_md
        assert "api_handle_request" in report_md  # external entry traced

    async def test_no_coverage_is_noop(self, sqlite_db, tmp_path):
        from app.config import settings

        ws_id, task_id = "ws-nocov", str(uuid.uuid4())
        repo = self._make_repo(tmp_path)
        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed) VALUES (?,?,?,1)",
                (ws_id, "WS", str(repo)),
            )
            await db.commit()

        await WorkspacePipeline()._apply_coverage_test_design(
            ws_id, str(repo), task_id, None
        )
        assert not (settings.outputs_path / task_id / "coverage_test_design.json").exists()

    async def test_explicit_coverage_ids_are_merged_and_must_be_analyzed(
        self, sqlite_db, tmp_path
    ):
        from app.adapters.coverage import parse_internal_function_hits
        from app.services.coverage_analyzer import _module_to_dict

        ws_id = "ws-cov-merge"
        repo = self._make_repo(tmp_path)

        def modules_json(function_name: str) -> str:
            report = parse_internal_function_hits(
                "feature,module,code_location,function,triggered,hit_count\n"
                f"rec,session,src/session.c:1-4,{function_name},false,0\n"
            )
            return json.dumps(
                [_module_to_dict(m) for m in report.modules], ensure_ascii=False
            )

        async with aiosqlite.connect(sqlite_db) as db:
            await db.execute(
                "INSERT INTO workspaces (id, name, repo_path, indexed) VALUES (?,?,?,1)",
                (ws_id, "WS", str(repo)),
            )
            for cov_id, status, function_name, updated_at in [
                ("cov-a", "analyzed", "recover_session", "2026-06-02T01:00:00Z"),
                ("cov-b", "analyzed", "internal_helper", "2026-06-02T02:00:00Z"),
                ("cov-parsed", "parsed", "must_not_appear", "2026-06-02T03:00:00Z"),
            ]:
                await db.execute(
                    "INSERT INTO coverage_analyses "
                    "(id, name, status, workspace_id, repo_path, modules_json, source_format, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        cov_id,
                        cov_id,
                        status,
                        ws_id,
                        str(repo),
                        modules_json(function_name),
                        "internal_function_hits",
                        updated_at,
                    ),
                )
            await db.commit()

        merged_json = await WorkspacePipeline()._resolve_coverage_modules_json(
            ws_id, ["cov-a", "cov-b", "cov-parsed"]
        )
        assert merged_json is not None
        names = [
            hit["function_name"]
            for module in json.loads(merged_json)
            for hit in module.get("function_hits", [])
        ]
        assert "recover_session" in names
        assert "internal_helper" in names
        assert "must_not_appear" not in names
