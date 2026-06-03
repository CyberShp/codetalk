import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from app.config import settings
from app.prompts.schemas import REPORT_FILE_MAP
from app.schemas.workspace_analysis import AnalysisPlan, ScopePreview

logger = logging.getLogger(__name__)

_DEFAULT_ANALYSIS_FOCUS = "全面分析代码库的架构、模块关系和关键业务流程"
_DEFAULT_PROMPT = "请对该代码仓库进行全面的架构分析，包括模块结构、依赖关系和核心业务逻辑"

_MAX_MATERIAL_BYTES = 100_000


def _read_material_file(file_path: str, max_bytes: int = _MAX_MATERIAL_BYTES) -> str:
    p = Path(file_path)
    if not p.is_file():
        return ""
    try:
        size = p.stat().st_size
        if size <= max_bytes:
            return p.read_text(encoding="utf-8", errors="replace")
        raw = p.read_bytes()[:max_bytes]
        text = raw.decode("utf-8", errors="replace")
        return text + "\n\n…（已截断）"
    except OSError:
        return ""


class WorkspacePipeline:
    """Run AnalysisPipeline for a workspace via a shadow task, then harvest reports."""

    async def run(
        self,
        ws_id: str,
        repo_path: str,
        plan: AnalysisPlan | None = None,
        scope_preview: ScopePreview | None = None,
        task_id: str | None = None,
        include_coverage_gaps: bool = True,
        coverage_analysis_ids: list[str] | None = None,
    ) -> None:
        task_id = task_id or str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # If no scope preview was provided (legacy callers / preview skipped),
        # resolve one now so the pipeline still operates on a bounded scope.
        if plan is not None and scope_preview is None:
            try:
                from app.services.workspace_scope_resolver import WorkspaceScopeResolver

                scope_preview = await WorkspaceScopeResolver().resolve(
                    ws_id=ws_id, repo_path=repo_path, plan=plan,
                )
            except Exception as exc:
                logger.warning(
                    "Inline scope resolution failed for ws=%s: %s", ws_id, exc,
                )
                scope_preview = None

        plan_json = plan.model_dump_json() if plan else None
        preview_json = scope_preview.model_dump_json() if scope_preview else None
        report_plan_json = (
            json.dumps([r.model_dump() for r in plan.enabled_reports()])
            if plan else None
        )

        # Derive a focus/prompt string from the plan so legacy code paths (chat
        # context, debug snapshots) still get useful intent text.
        if plan and plan.analysis_objects:
            analysis_focus = (
                "用户定义的分析对象：\n"
                + "\n".join(f"- {o.text}" for o in plan.analysis_objects[:16])
            )
        else:
            analysis_focus = _DEFAULT_ANALYSIS_FOCUS
        prompt_content = plan.user_guidance if (plan and plan.user_guidance) else _DEFAULT_PROMPT

        async with aiosqlite.connect(settings.sqlite_db) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute(
                "SELECT id, filename, content_type, file_path FROM workspace_materials"
                " WHERE workspace_id = ? AND is_active = TRUE ORDER BY created_at",
                (ws_id,),
            ) as cur:
                material_rows = await cur.fetchall()

            material_ids: list[str] = []
            requirements_parts: list[str] = []
            design_parts: list[str] = []

            for mat in material_rows:
                material_ids.append(mat["id"])
                content = await asyncio.to_thread(_read_material_file, mat["file_path"])
                if not content:
                    continue
                section = f"### {mat['filename']}\n{content}"
                if mat["content_type"] == "requirements":
                    requirements_parts.append(section)
                elif mat["content_type"] == "design":
                    design_parts.append(section)
                else:
                    requirements_parts.append(section)

            requirements_doc = "\n\n".join(requirements_parts) if requirements_parts else None
            design_doc = "\n\n".join(design_parts) if design_parts else None
            material_ids_json = json.dumps(material_ids) if material_ids else None

            if material_ids:
                logger.info(
                    "Workspace %s: binding %d active materials to shadow task",
                    ws_id, len(material_ids),
                )

            # GitNexus is always the primary tool now; DeepWiki is optional and
            # never required (see §16 AC-P3).
            tools = ["gitnexus"]

            await db.execute(
                """INSERT OR REPLACE INTO tasks
                       (id, name, repo_path, status, tools,
                        analysis_focus, prompt_content, deepwiki_depth,
                        requirements_doc, design_doc, material_ids,
                        analysis_plan_json, scope_preview_json, report_plan_json,
                        workspace_id,
                        progress, error_message, created_at, updated_at)
                   VALUES (?, ?, ?, 'pending', ?, ?, ?, 'balanced', ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)""",
                (
                    task_id,
                    f"__ws_{ws_id}",
                    repo_path,
                    json.dumps(tools),
                    analysis_focus,
                    prompt_content,
                    requirements_doc,
                    design_doc,
                    material_ids_json,
                    plan_json,
                    preview_json,
                    report_plan_json,
                    ws_id,
                    now,
                    now,
                ),
            )
            await db.commit()

        from app.services.analysis_pipeline import AnalysisPipeline

        await AnalysisPipeline().run(task_id)
        if include_coverage_gaps:
            await self._apply_coverage_test_design(
                ws_id, repo_path, task_id, coverage_analysis_ids
            )
        await self._harvest_reports(ws_id, task_id)

    async def _apply_coverage_test_design(
        self,
        ws_id: str,
        repo_path: str,
        task_id: str,
        coverage_analysis_ids: list[str] | None,
    ) -> None:
        """Fold the workspace's analyzed coverage into a coverage-test-design-v1
        artifact and the deterministic test_design report section.

        Best-effort: a failure here must never fail the whole analysis run.
        """
        try:
            from app.services.coverage_analyzer import (
                build_coverage_test_design,
                _dict_to_module,
            )
            from app.services.analysis_artifacts import write_coverage_test_design
            from app.services.report_generator import build_coverage_test_design_section

            modules_json = await self._resolve_coverage_modules_json(
                ws_id, coverage_analysis_ids
            )
            if not modules_json:
                return

            modules = [_dict_to_module(d) for d in json.loads(modules_json)]
            if not modules:
                return

            output_dir = settings.outputs_path / task_id
            design = await build_coverage_test_design(
                modules,
                workspace_id=ws_id,
                repo_path=repo_path,
                use_ai=True,
                artifact_dir=output_dir,
                analysis_id=task_id,
                report_output_dir=output_dir,
            )
            if not design.get("gaps"):
                return

            await write_coverage_test_design(output_dir, design)

            section = build_coverage_test_design_section(design)
            if section:
                await asyncio.to_thread(
                    self._append_test_design_section, output_dir, section
                )
            logger.info(
                "Workspace %s: coverage test design written (%d gaps)",
                ws_id, len(design.get("gaps") or []),
            )
        except Exception as exc:
            logger.warning(
                "Coverage test design skipped for ws=%s: %s", ws_id, exc
            )

    async def _resolve_coverage_modules_json(
        self,
        ws_id: str,
        coverage_analysis_ids: list[str] | None,
    ) -> str | None:
        """Return merged modules_json for the selected coverage analyses.

        Explicit ids win and all analyzed matching rows are merged; otherwise
        the workspace's most-recently analyzed coverage is used (per the product
        decision to auto-include the latest).
        """
        async with aiosqlite.connect(settings.sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            if coverage_analysis_ids:
                placeholders = ",".join("?" for _ in coverage_analysis_ids)
                async with db.execute(
                    f"SELECT modules_json FROM coverage_analyses "
                    f"WHERE id IN ({placeholders}) AND workspace_id = ? "
                    f"AND status = 'analyzed' "
                    f"ORDER BY updated_at DESC",
                    (*coverage_analysis_ids, ws_id),
                ) as cur:
                    rows = await cur.fetchall()
            else:
                async with db.execute(
                    "SELECT modules_json FROM coverage_analyses "
                    "WHERE workspace_id = ? AND status = 'analyzed' "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (ws_id,),
                ) as cur:
                    rows = await cur.fetchall()

        merged: list[dict] = []
        for row in rows:
            raw = row["modules_json"] if row and row["modules_json"] else None
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if isinstance(data, list):
                merged.extend(item for item in data if isinstance(item, dict))
        return json.dumps(merged, ensure_ascii=False) if merged else None

    @staticmethod
    def _append_test_design_section(output_dir: Path, section: str) -> None:
        """Append (or create) the test_design report with the coverage section."""
        target = WorkspacePipeline._resolve_test_design_path(output_dir)
        block = "\n\n" + section.rstrip() + "\n"
        if target.exists():
            existing = target.read_text(encoding="utf-8", errors="replace")
            target.write_text(existing.rstrip() + block, encoding="utf-8")
            return
        # The test_design report was not generated; create a standalone file so
        # the matrix is still surfaced and harvested.
        from datetime import datetime, timezone

        header = (
            "---\n"
            "report_type: test_design\n"
            "template_id: test_design\n"
            f"generated_at: {datetime.now(timezone.utc).isoformat()}\n"
            "---\n\n# 测试设计输入\n"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(header + block, encoding="utf-8")

    @staticmethod
    def _resolve_test_design_path(output_dir: Path) -> Path:
        """Find the test_design report file written by the generator.

        Prefers the run manifest's filename, then known candidate filenames,
        and finally falls back to the schema map name (used when creating one).
        """
        manifest_path = output_dir / "report_manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                for entry in manifest.get("reports", []) or []:
                    rtype = entry.get("report_type") or entry.get("template_id")
                    filename = entry.get("filename")
                    if rtype == "test_design" and filename:
                        return output_dir / filename
            except Exception:
                pass
        candidates = ["15-测试视角代码理解.md", REPORT_FILE_MAP.get("test_design", "")]
        for name in candidates:
            if name and (output_dir / name).exists():
                return output_dir / name
        return output_dir / (REPORT_FILE_MAP.get("test_design") or "04-测试设计输入.md")

    async def _harvest_reports(self, ws_id: str, task_id: str) -> None:
        output_dir = settings.outputs_path / task_id
        now = datetime.now(timezone.utc).isoformat()

        # Load the report manifest written by the new ReportGenerator (if
        # present).  It tells us per-report status/error/metadata so the UI
        # can render "partial" or "failed" badges rather than silently
        # exposing an empty file as "completed".
        manifest_path = output_dir / "report_manifest.json"
        manifest: dict = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(await asyncio.to_thread(manifest_path.read_text, "utf-8"))
            except Exception as exc:
                logger.warning("Failed to read report manifest: %s", exc)
                manifest = {}
        entries = manifest.get("reports", []) if isinstance(manifest, dict) else []

        async with aiosqlite.connect(settings.sqlite_db) as db:
            written_keys: set[str] = set()
            any_completed = False
            any_failed = False
            await db.execute(
                "DELETE FROM workspace_reports WHERE workspace_id = ?",
                (ws_id,),
            )

            if entries:
                for entry in entries:
                    report_type = entry.get("report_type") or entry.get("template_id")
                    filename = entry.get("filename")
                    status = entry.get("status", "failed")
                    title = entry.get("title", filename)
                    error = entry.get("error")
                    metadata = entry.get("metadata") or {}

                    if not (report_type and filename):
                        continue
                    written_keys.add(report_type)

                    filepath = output_dir / filename
                    content = ""
                    if filepath.exists():
                        try:
                            content = await asyncio.to_thread(filepath.read_text, "utf-8")
                        except Exception as exc:
                            logger.warning("Read report %s failed: %s", filename, exc)
                    if status == "completed":
                        any_completed = True
                    if status == "failed":
                        any_failed = True
                    await db.execute(
                        """INSERT INTO workspace_reports
                               (id, workspace_id, task_id, report_type, title, content, status, error, metadata_json, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            str(uuid.uuid4()),
                            ws_id,
                            task_id,
                            report_type,
                            title,
                            content,
                            status,
                            error,
                            json.dumps(metadata, ensure_ascii=False) if metadata else None,
                            now,
                        ),
                    )

            # Legacy file map — used when the new generator did not run (e.g.
            # downgraded code path) or for templates not yet in the manifest.
            for report_type, filename in REPORT_FILE_MAP.items():
                if report_type in written_keys:
                    continue
                filepath = output_dir / filename
                if not filepath.exists():
                    continue
                content = await asyncio.to_thread(filepath.read_text, "utf-8")
                status = "completed" if content.strip() else "failed"
                if status == "completed":
                    any_completed = True
                else:
                    any_failed = True
                await db.execute(
                    """INSERT INTO workspace_reports
                           (id, workspace_id, task_id, report_type, title, content, status, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (str(uuid.uuid4()), ws_id, task_id, report_type, filename, content, status, now),
                )

            if any_completed and any_failed:
                final_status = "partial"
            elif any_completed:
                final_status = "done"
            elif any_failed:
                final_status = "failed"
            else:
                # Nothing was produced at all — surface as failed instead of done.
                final_status = "failed"

            await db.execute(
                "UPDATE workspaces SET analyze_status = ?, analyze_progress = 100, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (final_status, ws_id),
            )
            await db.commit()

        logger.info(
            "Workspace %s: reports harvested (status=%s)", ws_id, final_status,
        )
