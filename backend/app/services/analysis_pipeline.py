"""Core analysis pipeline -- orchestrates data collection, per-module analysis,
and report generation in a MapReduce pattern.

Phases:
  0. Preparation  -- validate repo, git init, GitNexus index
  1. Data Collection (no AI) -- GitNexus graph, DeepWiki wiki
  2. Per-module Analysis (MapReduce) -- LLM summarizes each module
  3. Report Generation -- LLM generates each report from summaries
  4. Cross-enhancement (optional) -- enrich data across tools
"""

import asyncio
import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import httpx

from app.config import settings
from app.llm.base import BaseLLMClient, LLMResponse
from app.llm.factory import create_llm_client_from_active
from app.prompts.templates import MODULE_SUMMARY_PROMPT
from app.services.report_generator import ReportGenerator

logger = logging.getLogger(__name__)

MAX_TOKENS_PER_CALL = 40000
MAX_OUTPUT_TOKENS = 4096
SUMMARY_MAX_WORDS = 200


class AnalysisPipeline:
    """Stateless pipeline that runs the full analysis for a task."""

    def __init__(self, task_id: str) -> None:
        self._task_id = task_id
        self._gitnexus_data: dict = {}
        self._deepwiki_data: dict = {}
        self._module_summaries: list[dict] = []

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, task_id: str) -> None:
        """Execute the full pipeline, updating task progress in the DB."""
        logger.info("Pipeline started for task %s", task_id)

        try:
            task = await self._load_task(task_id)
            repo_path = task["repo_path"]
            tools = json.loads(task.get("tools") or "[]")

            await self._update_progress(task_id, 0, "running", None)

            # Phase 0: Preparation
            await self._phase_prepare(repo_path, tools)
            await self._update_progress(task_id, 10, "running", None)

            # Phase 1: Data Collection
            await self._phase_collect(repo_path, tools)
            await self._update_progress(task_id, 40, "running", None)

            # Phase 2: Per-module Analysis (MapReduce)
            llm_client = await self._try_create_llm_client()
            if llm_client:
                await self._phase_module_analysis(llm_client)
                await self._update_progress(task_id, 70, "running", None)

                # Phase 3: Report Generation
                output_dir = settings.outputs_path / task_id
                output_dir.mkdir(parents=True, exist_ok=True)

                generator = ReportGenerator(
                    llm_client=llm_client,
                    output_dir=output_dir,
                    task_id=task_id,
                )
                await generator.generate_all(
                    module_summaries=self._module_summaries,
                    gitnexus_data=self._gitnexus_data,
                    deepwiki_data=self._deepwiki_data,
                    requirements_doc=task.get("requirements_doc"),
                    design_doc=task.get("design_doc"),
                )
                await self._update_progress(task_id, 90, "running", None)

                # Phase 4: Cross-enhancement (optional, best-effort)
                await self._phase_cross_enhance()
            else:
                logger.warning("No LLM client available, skipping AI phases")
                output_dir = settings.outputs_path / task_id
                output_dir.mkdir(parents=True, exist_ok=True)
                await self._save_raw_data(output_dir)

            await self._update_progress(task_id, 100, "completed", None)
            logger.info("Pipeline completed for task %s", task_id)

        except Exception as exc:
            logger.exception("Pipeline failed for task %s", task_id)
            await self._update_progress(task_id, -1, "failed", str(exc))

    # ------------------------------------------------------------------
    # Phase 0: Preparation
    # ------------------------------------------------------------------

    async def _phase_prepare(self, repo_path: str, tools: list[str]) -> None:
        """Validate repo path and ensure git is initialized."""
        path = Path(repo_path)
        if not path.exists():
            raise FileNotFoundError(f"代码路径不存在: {repo_path}")

        git_dir = path / ".git"
        if not git_dir.exists():
            logger.info("Initializing git repo at %s", repo_path)
            proc = await asyncio.to_thread(
                subprocess.run,
                ["git", "init"],
                cwd=str(path),
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                logger.warning("git init failed: %s", proc.stderr)

    # ------------------------------------------------------------------
    # Phase 1: Data Collection
    # ------------------------------------------------------------------

    async def _phase_collect(self, repo_path: str, tools: list[str]) -> None:
        """Collect data from GitNexus and DeepWiki in parallel."""
        coros = []

        if "gitnexus" in tools:
            coros.append(self._collect_gitnexus(repo_path))
        if "deepwiki" in tools:
            coros.append(self._collect_deepwiki(repo_path))

        if coros:
            results = await asyncio.gather(*coros, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.error("Data collection error: %s", result)

    async def _collect_gitnexus(self, repo_path: str) -> None:
        """Call GitNexus to get the knowledge graph."""
        base_url = settings.gitnexus_base_url
        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(600, connect=10),
        ) as client:
            resp = await client.post("/api/analyze", json={"path": repo_path})
            resp.raise_for_status()
            job = resp.json()
            job_id = job.get("jobId", "")
            logger.info("GitNexus indexing started: %s", job_id)

            # Poll for completion
            for _ in range(300):  # 10 min max
                await asyncio.sleep(2)
                status_resp = await client.get(f"/api/analyze/{job_id}")
                status = status_resp.json()
                if status["status"] == "complete":
                    break
                if status["status"] == "failed":
                    raise RuntimeError(
                        "GitNexus indexing failed: "
                        + status.get("error", "unknown")
                    )
            else:
                raise RuntimeError("GitNexus indexing timed out")

            # Fetch graph
            repo_name = status.get("repoName", "")
            params = {"repo": repo_name} if repo_name else {}
            graph_resp = await client.get("/api/graph", params=params, timeout=120)
            graph_resp.raise_for_status()
            self._gitnexus_data = graph_resp.json()
            logger.info(
                "GitNexus data collected: %d nodes, %d edges",
                len(self._gitnexus_data.get("nodes", [])),
                len(self._gitnexus_data.get("relationships", [])),
            )

    async def _collect_deepwiki(self, repo_path: str) -> None:
        """Call DeepWiki to generate wiki content."""
        base_url = settings.deepwiki_api_url
        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(1800, connect=10),
        ) as client:
            payload = {
                "repo_url": repo_path,
                "type": "local",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "分析整个仓库，生成全面的中文技术文档。"
                            "包含：架构概览、核心组件、数据流。"
                        ),
                    }
                ],
                "excluded_dirs": "\n".join([
                    "node_modules", ".git", "dist", "build", "__pycache__",
                    ".next", "vendor", "coverage", ".venv", "venv",
                ]),
            }

            full_content = ""
            async with client.stream(
                "POST", "/chat/completions/stream", json=payload, timeout=1800,
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_text():
                    full_content += chunk

            self._deepwiki_data = {"documentation": full_content}
            logger.info("DeepWiki data collected: %d chars", len(full_content))

    # ------------------------------------------------------------------
    # Phase 2: Per-module Analysis (MapReduce)
    # ------------------------------------------------------------------

    async def _phase_module_analysis(self, llm_client: BaseLLMClient) -> None:
        """Analyze each community/module via LLM."""
        communities = self._extract_communities()
        if not communities:
            logger.warning("No communities found, treating entire repo as single module")
            communities = [self._build_single_module()]

        for community in communities:
            try:
                summary = await self._analyze_module(llm_client, community)
                self._module_summaries.append({
                    "module_name": community["name"],
                    "summary": summary,
                    "files": community.get("files", []),
                })
            except Exception as exc:
                logger.error(
                    "Module analysis failed for %s: %s",
                    community["name"],
                    exc,
                )
                self._module_summaries.append({
                    "module_name": community["name"],
                    "summary": f"（分析失败: {exc}）",
                    "files": community.get("files", []),
                })

    def _extract_communities(self) -> list[dict]:
        """Extract community/module groups from GitNexus data."""
        nodes = self._gitnexus_data.get("nodes", [])
        relationships = self._gitnexus_data.get("relationships", [])

        communities: dict[str, dict] = {}
        for node in nodes:
            if node.get("label") == "Community":
                cid = node["id"]
                communities[cid] = {
                    "id": cid,
                    "name": node.get("properties", {}).get("name", cid),
                    "files": [],
                    "calls": [],
                }

        # Map members to communities
        for edge in relationships:
            if edge.get("type") == "MEMBER_OF":
                cid = edge["targetId"]
                if cid in communities:
                    communities[cid]["files"].append(edge["sourceId"])

        # Collect call relations within communities
        member_to_community: dict[str, str] = {}
        for edge in relationships:
            if edge.get("type") == "MEMBER_OF":
                member_to_community[edge["sourceId"]] = edge["targetId"]

        for edge in relationships:
            if edge.get("type") in ("CALLS", "IMPORTS", "DEPENDS_ON"):
                src_community = member_to_community.get(edge["sourceId"])
                tgt_community = member_to_community.get(edge["targetId"])
                if src_community and src_community == tgt_community:
                    communities[src_community]["calls"].append(
                        edge["sourceId"] + " -> " + edge["targetId"]
                    )

        return list(communities.values())

    def _build_single_module(self) -> dict:
        """Fallback: treat all nodes as a single module."""
        nodes = self._gitnexus_data.get("nodes", [])
        file_nodes = [
            n["id"] for n in nodes
            if n.get("label") in ("File", "Module", "Class", "Function")
        ]
        return {
            "id": "root",
            "name": "全项目",
            "files": file_nodes[:50],
            "calls": [],
        }

    async def _analyze_module(
        self, llm_client: BaseLLMClient, community: dict
    ) -> str:
        """Call LLM to summarize a single module."""
        file_list = "\n".join(f"- {f}" for f in community.get("files", [])[:30])
        call_relations = "\n".join(community.get("calls", [])[:20])

        wiki_content = self._extract_module_wiki(community["name"])

        prompt = MODULE_SUMMARY_PROMPT.format(
            module_name=community["name"],
            file_list=file_list or "（无文件信息）",
            call_relations=call_relations or "（无调用关系信息）",
            wiki_content=wiki_content or "（无 Wiki 文档）",
        )

        messages = [{"role": "user", "content": prompt}]
        response: LLMResponse = await llm_client.complete(
            messages=messages,
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=0.3,
        )

        logger.info(
            "Module %s analyzed: %d tokens used",
            community["name"],
            response.usage.get("total_tokens", 0),
        )
        return response.content

    def _extract_module_wiki(self, module_name: str) -> str:
        """Extract relevant wiki content for a module (substring match)."""
        doc = self._deepwiki_data.get("documentation", "")
        if not doc:
            return ""

        lines = doc.split("\n")
        relevant: list[str] = []
        capturing = False
        for line in lines:
            if module_name.lower() in line.lower():
                capturing = True
            if capturing:
                relevant.append(line)
                if len(relevant) > 30:
                    break
            elif relevant and line.strip() == "":
                capturing = False

        result = "\n".join(relevant)
        if len(result) > 3000:
            result = result[:3000] + "\n...（已截断）"
        return result

    # ------------------------------------------------------------------
    # Phase 4: Cross-enhancement (optional)
    # ------------------------------------------------------------------

    async def _phase_cross_enhance(self) -> None:
        """Best-effort cross-enhancement between tool outputs."""
        logger.info("Cross-enhancement phase: skipped (not yet implemented)")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _save_raw_data(self, output_dir: Path) -> None:
        """Save raw tool data for inspection when no LLM is available."""
        if self._gitnexus_data:
            raw_path = output_dir / "00-gitnexus-raw.json"
            raw_path.write_text(
                json.dumps(self._gitnexus_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if self._deepwiki_data:
            raw_path = output_dir / "00-deepwiki-raw.md"
            raw_path.write_text(
                self._deepwiki_data.get("documentation", ""),
                encoding="utf-8",
            )

    async def _try_create_llm_client(self) -> BaseLLMClient | None:
        """Attempt to create an LLM client; return None if not configured."""
        try:
            return await create_llm_client_from_active()
        except ValueError as exc:
            logger.warning("LLM client not available: %s", exc)
            return None

    @staticmethod
    async def _load_task(task_id: str) -> dict:
        """Load task record from SQLite."""
        async with aiosqlite.connect(settings.sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ) as cur:
                row = await cur.fetchone()
            if not row:
                raise ValueError(f"任务不存在: {task_id}")
            return dict(row)

    @staticmethod
    async def _update_progress(
        task_id: str,
        progress: int,
        status: str,
        error_message: str | None,
    ) -> None:
        """Update task progress and status in SQLite."""
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(settings.sqlite_db) as db:
            if error_message is not None:
                await db.execute(
                    "UPDATE tasks SET progress = ?, status = ?, error_message = ?, "
                    "updated_at = ? WHERE id = ?",
                    (progress, status, error_message, now, task_id),
                )
            else:
                await db.execute(
                    "UPDATE tasks SET progress = ?, status = ?, updated_at = ? "
                    "WHERE id = ?",
                    (progress, status, now, task_id),
                )
            await db.commit()