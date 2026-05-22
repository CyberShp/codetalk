import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from app.config import settings
from app.prompts.schemas import REPORT_FILE_MAP

logger = logging.getLogger(__name__)

_DEFAULT_ANALYSIS_FOCUS = "全面分析代码库的架构、模块关系和关键业务流程"
_DEFAULT_PROMPT = "请对该代码仓库进行全面的架构分析，包括模块结构、依赖关系和核心业务逻辑"

_MAX_MATERIAL_BYTES = 100_000


def _read_material_file(file_path: str) -> str:
    p = Path(file_path)
    if not p.is_file():
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        if len(text) > _MAX_MATERIAL_BYTES:
            text = text[:_MAX_MATERIAL_BYTES] + "\n\n…（已截断）"
        return text
    except OSError:
        return ""


class WorkspacePipeline:
    """Run AnalysisPipeline for a workspace via a shadow task, then harvest reports."""

    async def run(self, ws_id: str, repo_path: str) -> None:
        task_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

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

            await db.execute(
                """INSERT INTO tasks
                       (id, name, repo_path, status, tools,
                        analysis_focus, prompt_content, deepwiki_depth,
                        requirements_doc, design_doc, material_ids,
                        progress, error_message, created_at, updated_at)
                   VALUES (?, ?, ?, 'pending', ?, ?, ?, 'balanced', ?, ?, ?, 0, NULL, ?, ?)""",
                (
                    task_id,
                    f"__ws_{ws_id}",
                    repo_path,
                    json.dumps(["gitnexus"]),
                    _DEFAULT_ANALYSIS_FOCUS,
                    _DEFAULT_PROMPT,
                    requirements_doc,
                    design_doc,
                    material_ids_json,
                    now,
                    now,
                ),
            )
            await db.commit()

        try:
            from app.services.analysis_pipeline import AnalysisPipeline

            await AnalysisPipeline().run(task_id)
            await self._harvest_reports(ws_id, task_id)

        finally:
            async with aiosqlite.connect(settings.sqlite_db) as db:
                await db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                await db.commit()

    async def _harvest_reports(self, ws_id: str, task_id: str) -> None:
        output_dir = settings.outputs_path / task_id
        now = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(settings.sqlite_db) as db:
            await db.execute(
                "DELETE FROM workspace_reports WHERE workspace_id = ?", (ws_id,)
            )

            for report_type, filename in REPORT_FILE_MAP.items():
                filepath = output_dir / filename
                if not filepath.exists():
                    continue
                content = await asyncio.to_thread(filepath.read_text, "utf-8")
                await db.execute(
                    """INSERT INTO workspace_reports
                           (id, workspace_id, report_type, title, content, status, created_at)
                       VALUES (?, ?, ?, ?, ?, 'completed', ?)""",
                    (str(uuid.uuid4()), ws_id, report_type, filename, content, now),
                )

            await db.execute(
                "UPDATE workspaces SET analyze_status = 'done', analyze_progress = 100, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (ws_id,),
            )
            await db.commit()

        logger.info("Workspace %s: reports harvested successfully", ws_id)
