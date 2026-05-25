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
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import httpx

from app.adapters.base import AnalysisRequest
from app.config import settings
from app.llm.base import BaseLLMClient, LLMResponse, current_task_id
from app.llm.factory import create_llm_client_from_active
from app.prompts.templates import MODULE_SUMMARY_PROMPT
from app.services.report_generator import ReportGenerator

logger = logging.getLogger(__name__)

MAX_TOKENS_PER_CALL = 40000
MAX_OUTPUT_TOKENS = 8192  # per-module summary; matches report generator budget
MAX_DEP_FILES = 8
DEP_BUDGET_BYTES = 15_000
_PIPELINE_VERSION = "2"

# Directories to skip when doing directory-structure module discovery
_DIR_SKIP = frozenset({
    "node_modules", "__pycache__", ".git", ".venv", "venv", "dist",
    "build", ".next", "vendor", "coverage", ".tox", ".mypy_cache",
    ".pytest_cache", "target", "out", "bin", "obj",
})

# Source file extensions for directory-structure module discovery
_SOURCE_EXTS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go", ".rs",
    ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp",
})


class AnalysisPipeline:
    """Stateless pipeline that runs the full analysis for a task."""

    def __init__(self) -> None:
        self._gitnexus_data: dict = {}
        self._deepwiki_data: dict = {}
        self._module_summaries: list[dict] = []
        self._analysis_focus: str = ""
        self._prompt_content: str = ""
        self._task_id: str = ""
        self._data_quality: str = "good"  # "good" | "degraded" | "poor"
        self._repo_path: str = ""
        self._output_dir: Path | None = None
        self._llm_client: BaseLLMClient | None = None
        self._deepwiki_depth: str = ""

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, task_id: str) -> None:
        """Execute the full pipeline, updating task progress in the DB."""
        logger.info("Pipeline started for task %s", task_id, extra={"task_id": task_id})
        self._task_id = task_id
        _ctx_token = current_task_id.set(task_id)

        try:
            task = await self._load_task(task_id)
            repo_path = task["repo_path"]
            self._repo_path = repo_path  # store for use by new methods
            tools = json.loads(task.get("tools") or "[]")
            self._tools = tools
            self._analysis_focus = task.get("analysis_focus") or ""
            self._prompt_content = task.get("prompt_content") or ""
            self._deepwiki_depth = task.get("deepwiki_depth") or ""

            await self._update_progress(task_id, 0, "running", None, "启动分析管道…")

            # Phase 0: Preparation
            await self._phase_prepare(repo_path, tools)
            await self._update_progress(task_id, 10, "running", None, "环境准备完成，开始采集数据…")

            # Phase 1: Data Collection
            await self._phase_collect(repo_path, tools)
            await self._update_progress(task_id, 40, "running", None, "数据采集完成，开始 AI 模块分析…")

            # Task 5: Save DeepWiki documentation as independent output
            output_dir = settings.outputs_path / task_id
            output_dir.mkdir(parents=True, exist_ok=True)
            await self._save_deepwiki_output(output_dir, task_id)

            # Phase 2: Per-module Analysis (MapReduce)
            llm_client = await self._try_create_llm_client()
            if llm_client:
                await self._phase_module_analysis(llm_client)

                all_modules_failed = bool(self._module_summaries) and all(
                    s.get("summary", "").startswith("（分析失败")
                    for s in self._module_summaries
                )
                if all_modules_failed:
                    await self._update_progress(
                        task_id, 70, "running", None,
                        "⚠️ 所有模块分析均失败，尝试生成报告…",
                    )
                else:
                    await self._update_progress(task_id, 70, "running", None, "模块分析完成，生成报告中…")

                # Phase 3: Report Generation
                generator = ReportGenerator(
                    llm_client=llm_client,
                    output_dir=output_dir,
                    task_id=task_id,
                )
                _REPORT_LABELS: dict[str, str] = {
                    "module_map": "项目与模块地图",
                    "business_flow": "关键业务流程分析",
                    "source_reading": "源码定向阅读记录",
                    "test_design": "测试设计输入",
                    "requirements": "需求与设计理解",
                    "traceability": "需求-设计-代码追踪",
                }

                async def _on_report_done(rtype: str, _fname: str, tokens: int) -> None:
                    label = _REPORT_LABELS.get(rtype, rtype)
                    await self._log_step(
                        task_id, 80, f"✅ {label} 已生成（{tokens:,} tokens）"
                    )

                async def _on_report_start(rtype: str, idx: int, total: int) -> None:
                    label = _REPORT_LABELS.get(rtype, rtype)
                    await self._log_step(task_id, 75, f"📝 正在生成：{label}")

                async def _on_report_failed(rtype: str, error: str) -> None:
                    label = _REPORT_LABELS.get(rtype, rtype)
                    await self._log_step(task_id, 80, f"❌ {label} 生成失败：{error}")

                await generator.generate_all(
                    module_summaries=self._module_summaries,
                    gitnexus_data=self._gitnexus_data,
                    deepwiki_data=self._deepwiki_data,
                    requirements_doc=task.get("requirements_doc"),
                    design_doc=task.get("design_doc"),
                    analysis_focus=self._analysis_focus,
                    prompt_content=self._prompt_content,
                    on_report_done=_on_report_done,
                    on_report_start=_on_report_start,
                    on_report_failed=_on_report_failed,
                    max_concurrency=settings.llm_max_concurrency,
                    data_quality=self._data_quality,
                    use_streaming=True,
                    repo_path=self._repo_path,
                )
                await self._update_progress(task_id, 90, "running", None, "报告生成完成，收尾处理…")

                # Phase 4: Cross-enhancement (optional, best-effort)
                self._output_dir = output_dir
                self._llm_client = llm_client
                await self._phase_cross_enhance()

                no_reports = not generator.generated_files
                if all_modules_failed and no_reports:
                    final_status = "failed"
                elif generator.status_override:
                    final_status = generator.status_override
                elif all_modules_failed:
                    final_status = "completed_with_warnings"
                else:
                    final_status = "completed"
            else:
                logger.warning("No LLM client available, skipping AI phases", extra={"task_id": task_id})
                await self._save_raw_data(output_dir)
                final_status = "completed"

            if final_status == "failed":
                done_msg = "分析失败：所有 AI 输出均未成功"
            elif final_status == "completed_with_warnings":
                done_msg = "分析完成（部分内容生成失败）"
            else:
                done_msg = "分析完成"
            await self._update_progress(task_id, 100, final_status, None, done_msg)
            logger.info("Pipeline completed for task %s (status=%s)", task_id, final_status, extra={"task_id": task_id})

        except Exception as exc:
            logger.exception("Pipeline failed for task %s", task_id, extra={"task_id": task_id})
            await self._update_progress(task_id, -1, "failed", str(exc), f"分析失败：{exc}")

        finally:
            current_task_id.reset(_ctx_token)

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
                    logger.exception("Data collection error: %s", result)

        # Validate data quality — only report failures for tools that were selected.
        # An unselected tool naturally produces no data; that is expected, not a failure.
        want_gitnexus = "gitnexus" in tools
        want_deepwiki = "deepwiki" in tools
        has_gitnexus = bool(self._gitnexus_data and self._gitnexus_data.get("nodes"))
        has_deepwiki = bool(self._deepwiki_data and self._deepwiki_data.get("documentation"))

        gitnexus_failed = want_gitnexus and not has_gitnexus
        deepwiki_failed = want_deepwiki and not has_deepwiki

        if gitnexus_failed and deepwiki_failed:
            self._data_quality = "poor"
            logger.warning("Data quality: poor -- GitNexus and DeepWiki both selected but data missing")
            await self._update_progress(
                self._task_id, 35, "running",
                "WARNING: GitNexus and DeepWiki data missing; analysis will be limited",
                "⚠️ 数据采集不完整（GitNexus + DeepWiki 均失败），分析将受限",
            )
        elif gitnexus_failed:
            self._data_quality = "degraded"
            logger.warning("Data quality: degraded -- GitNexus data missing, continuing with DeepWiki only")
            await self._update_progress(
                self._task_id, 35, "running",
                "WARNING: GitNexus data unavailable; continuing in degraded mode",
                "⚠️ GitNexus 数据不可用，仅使用 DeepWiki 数据继续",
            )
        elif deepwiki_failed:
            self._data_quality = "degraded"
            logger.warning("Data quality: degraded -- DeepWiki data missing, continuing with GitNexus only")
        else:
            self._data_quality = "good"

    async def _collect_gitnexus(self, repo_path: str) -> None:
        """Call GitNexus to get the knowledge graph, with commit-based caching."""
        # Task 10: check cache before calling API
        cache_key = await self._build_gitnexus_cache_key(repo_path)
        if cache_key:
            cached = await self._load_gitnexus_cache(cache_key)
            if cached is not None:
                self._gitnexus_data = cached
                logger.info(
                    "GitNexus cache hit (%s): %d nodes, %d edges",
                    cache_key,
                    len(cached.get("nodes", [])),
                    len(cached.get("relationships", [])),
                )
                return

        base_url = settings.gitnexus_base_url
        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(1800, connect=10),
            trust_env=False,
        ) as client:
            resp = await client.post("/api/analyze", json={"path": repo_path})

            job_id: str | None = None
            repo_name: str | None = None
            if resp.status_code == 409:
                body = resp.json() if resp.content else {}
                if body.get("jobId"):
                    job_id = body["jobId"]
                    logger.info("GitNexus 409 — joining existing job %s", job_id)
                else:
                    repo_name = body.get("repoName") or body.get("repo") or Path(repo_path).name
                    logger.info("GitNexus 409 — repo already indexed as %s", repo_name)
            elif resp.is_error:
                resp.raise_for_status()
            else:
                job = resp.json()
                job_id = job.get("jobId", "")
                logger.info("GitNexus indexing started: %s", job_id)

            if job_id is not None:
                # Poll for completion (30 min max)
                for _ in range(900):
                    await asyncio.sleep(2)
                    status_resp = await client.get(f"/api/analyze/{job_id}")
                    status = status_resp.json()
                    if status["status"] == "complete":
                        repo_name = status.get("repoName", "") or Path(repo_path).name
                        if not status.get("repoName"):
                            logger.warning(
                                "GitNexus status missing repoName; falling back to dir name: %s",
                                repo_name,
                            )
                        break
                    if status["status"] == "failed":
                        raise RuntimeError(
                            "GitNexus indexing failed: "
                            + status.get("error", "unknown")
                        )
                else:
                    raise RuntimeError("GitNexus indexing timed out")

            # repo_name is set from 409 body or poll status
            repo_name = repo_name or Path(repo_path).name
            graph_resp = await client.get("/api/graph", params={"repo": repo_name}, timeout=120)
            if graph_resp.status_code == 404:
                logger.warning(
                    "GitNexus graph 404 for repo=%s; retrying without repo filter",
                    repo_name,
                )
                graph_resp = await client.get("/api/graph", timeout=120)
            graph_resp.raise_for_status()
            self._gitnexus_data = graph_resp.json()
            logger.info(
                "GitNexus data collected: %d nodes, %d edges",
                len(self._gitnexus_data.get("nodes", [])),
                len(self._gitnexus_data.get("relationships", [])),
            )

        # Task 10: save to cache
        if cache_key:
            await self._save_gitnexus_cache(cache_key, self._gitnexus_data)

    async def _collect_deepwiki(self, repo_path: str) -> None:
        """Call DeepWiki to generate wiki content."""
        base_url = settings.deepwiki_api_url
        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(1800, connect=10),
            trust_env=False,
        ) as client:
            depth = self._deepwiki_depth or settings.deepwiki_default_depth

            if depth == "fast":
                deepwiki_message = (
                    "快速分析仓库核心架构，生成精简的中文技术文档。"
                    "仅覆盖：核心入口、主要模块概述。控制篇幅在2000字以内。"
                )
            elif depth == "deep":
                deepwiki_message = (
                    "深度分析整个仓库，生成详尽的中文技术文档。"
                    "包含：架构概览、核心组件、数据流、API接口、"
                    "配置管理、错误处理、安全机制、部署架构。不限篇幅。"
                )
            else:  # balanced (default)
                deepwiki_message = (
                    "分析整个仓库，生成全面的中文技术文档。"
                    "包含：架构概览、核心组件、数据流。"
                )

            if self._analysis_focus:
                deepwiki_message = (
                    f"针对以下分析目标，生成中文技术文档：\n"
                    f"{self._analysis_focus}\n\n"
                    + deepwiki_message
                )

            payload = {
                "repo_url": repo_path,
                "type": "local",
                "provider": settings.deepwiki_provider,
                "messages": [
                    {"role": "user", "content": deepwiki_message}
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
    # Task 5: DeepWiki independent output
    # ------------------------------------------------------------------

    async def _save_deepwiki_output(self, output_dir: Path, task_id: str) -> None:
        """Save DeepWiki documentation as a standalone markdown file with YAML frontmatter."""
        doc = self._deepwiki_data.get("documentation", "")
        if not doc:
            return

        ts = datetime.now(timezone.utc).isoformat()
        frontmatter = (
            f"---\n"
            f"source: deepwiki\n"
            f"task_id: {task_id}\n"
            f"generated_at: {ts}\n"
            f"---\n\n"
        )
        content = frontmatter + doc

        def _write() -> None:
            out_path = output_dir / "00-知识库文档.md"
            out_path.write_text(content, encoding="utf-8")

        await asyncio.to_thread(_write)
        logger.info("DeepWiki knowledge base saved: %d chars", len(doc))

    # ------------------------------------------------------------------
    # Phase 2: Per-module Analysis (MapReduce)
    # ------------------------------------------------------------------

    async def _phase_module_analysis(self, llm_client: BaseLLMClient) -> None:
        """Analyze each community/module via LLM (bounded parallel)."""
        repo_path = self._repo_path

        # Task 13: check module summary cache before running LLM
        # Cache key includes commit + task intent hash so different analysis_focus /
        # prompt_content / tool selections produce separate cache entries.
        commit_hash = await self._get_repo_commit_hash(repo_path)
        cache_key = self._build_module_cache_key(commit_hash) if commit_hash else ""
        if cache_key:
            cached_summaries = await self._load_module_summaries_cache(cache_key)
            if cached_summaries is not None:
                logger.info(
                    "Module summaries cache hit (%s): %d modules",
                    cache_key,
                    len(cached_summaries),
                )
                self._module_summaries = cached_summaries
                return

        # Task 15: check for previous-commit cached summaries for incremental analysis
        prev_summaries, prev_commit = await self._load_latest_module_summaries_cache()
        # None = diff unknown/failed; set() = confirmed zero-diff; set[...] = specific changes
        changed_files: set[str] | None = None
        if prev_summaries and prev_commit and commit_hash and prev_commit != commit_hash:
            changed_files = await self._get_changed_files(repo_path, prev_commit, commit_hash)
            if changed_files is not None:
                logger.info(
                    "Incremental analysis: %d changed files since %s",
                    len(changed_files),
                    prev_commit,
                )
            else:
                logger.warning(
                    "Could not determine changed files since %s; skipping incremental reuse",
                    prev_commit,
                )

        # Task 6: module discovery with fallback chain
        communities = self._extract_communities()  # GitNexus first
        if not communities and self._deepwiki_data:
            communities = self._extract_modules_from_wiki()  # DeepWiki fallback
        if not communities:
            communities = self._extract_modules_from_dirs(repo_path)  # Directory fallback
        if not communities:
            communities = [self._build_single_module()]  # Last resort

        sem = asyncio.Semaphore(settings.analysis_concurrency)
        data_quality_note = ""
        if self._data_quality == "poor":
            data_quality_note = (
                "\n\n> **Note**: Data quality is poor -- both GitNexus and DeepWiki "
                "data were unavailable.  Analysis is based on limited information.\n"
            )
        elif self._data_quality == "degraded":
            data_quality_note = (
                "\n\n> **Note**: Data quality is degraded -- some data sources were "
                "unavailable.  Analysis may be incomplete.\n"
            )

        total = len(communities)

        async def analyze_one(community: dict) -> dict:
            # Task 15: reuse cached summary if module files are unchanged.
            # Use `is not None` so an empty changed_files (zero-diff) still triggers full reuse.
            if prev_summaries is not None:
                module_files = set(community.get("files", []))
                normalized = AnalysisPipeline._normalize_to_relative(module_files, repo_path)
                if changed_files is not None and (
                    not changed_files or not normalized.intersection(changed_files)
                ):
                    cached_entry = next(
                        (s for s in prev_summaries if s.get("module_name") == community["name"]),
                        None,
                    )
                    if cached_entry:
                        logger.info(
                            "Reusing cached summary for unchanged module: %s",
                            community["name"],
                        )
                        return cached_entry

            async with sem:
                try:
                    summary = await self._analyze_module(llm_client, community)
                    if data_quality_note:
                        summary += data_quality_note
                    await AnalysisPipeline._log_step(
                        self._task_id, 55,
                        f"模块分析完成：{community['name']}（共 {total} 个模块）",
                    )
                except Exception as exc:
                    logger.exception(
                        "Module analysis failed for %s: %s",
                        community["name"],
                        exc,
                    )
                    summary = f"（分析失败: {exc}）"
                    await AnalysisPipeline._log_step(
                        self._task_id, 55,
                        f"模块分析失败：{community['name']} — {exc}",
                    )
                return {
                    "module_name": community["name"],
                    "summary": summary,
                    "files": community.get("files", []),
                }

        self._module_summaries = list(
            await asyncio.gather(*(analyze_one(c) for c in communities))
        )

        # Task 13: persist module summaries to cache
        if cache_key:
            await self._save_module_summaries_cache(cache_key, self._module_summaries)

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

    def _extract_modules_from_wiki(self) -> list[dict]:
        """Task 6 fallback: derive modules from DeepWiki markdown headers (## or ###)."""
        doc = self._deepwiki_data.get("documentation", "")
        if not doc:
            return []

        header_pattern = re.compile(r"^#{2,3}\s+(.+)", re.MULTILINE)
        matches = header_pattern.findall(doc)

        modules: list[dict] = []
        seen: set[str] = set()
        for header in matches:
            name = header.strip()
            if name and name not in seen:
                seen.add(name)
                modules.append({
                    "id": f"wiki_{len(modules)}",
                    "name": name,
                    "files": [],
                    "calls": [],
                })

        logger.info("DeepWiki fallback: discovered %d modules from headers", len(modules))
        return modules

    def _extract_modules_from_dirs(self, repo_path: str) -> list[dict]:
        """Task 6 fallback: derive modules from top-level directories containing source files."""
        path = Path(repo_path)
        if not path.is_dir():
            return []

        modules: list[dict] = []
        try:
            for entry in sorted(path.iterdir()):
                if not entry.is_dir():
                    continue
                if entry.name.startswith(".") or entry.name in _DIR_SKIP:
                    continue

                # Collect source files in this directory (recursive, limited to 50)
                source_files: list[str] = []
                for root, dirs, files in os.walk(entry):
                    # Prune skip dirs in-place to avoid descending into them
                    dirs[:] = [
                        d for d in dirs
                        if not d.startswith(".") and d not in _DIR_SKIP
                    ]
                    for fname in files:
                        if Path(fname).suffix in _SOURCE_EXTS:
                            source_files.append(str(Path(root) / fname))
                            if len(source_files) >= 50:
                                break
                    if len(source_files) >= 50:
                        break

                if source_files:
                    modules.append({
                        "id": f"dir_{entry.name}",
                        "name": entry.name,
                        "files": source_files,
                        "calls": [],
                    })
        except PermissionError as exc:
            logger.warning("Directory scan permission error: %s", exc)

        logger.info("Directory fallback: discovered %d modules from top-level dirs", len(modules))
        return modules

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

    def _build_structural_info(self, file_ids: list[str]) -> str:
        """Build human-readable structural info from DEFINES/CONTAINS edges."""
        relationships = self._gitnexus_data.get("relationships", [])
        nodes_by_id = {n["id"]: n for n in self._gitnexus_data.get("nodes", [])}

        file_defines: dict[str, list[str]] = {}
        for edge in relationships:
            if edge.get("type") in ("DEFINES", "CONTAINS"):
                src = edge["sourceId"]
                tgt = edge["targetId"]
                tgt_node = nodes_by_id.get(tgt, {})
                tgt_label = tgt_node.get("label", "")
                tgt_name = tgt_node.get("properties", {}).get("name", tgt)
                if tgt_label in ("Function", "Class", "Struct", "Variable", "Section"):
                    file_defines.setdefault(src, []).append(f"{tgt_label}:{tgt_name}")

        lines: list[str] = []
        for fid in file_ids:
            node = nodes_by_id.get(fid, {})
            fname = node.get("properties", {}).get("name", fid)
            defines = file_defines.get(fid, [])
            if defines:
                lines.append(f"- {fname} 定义了: {', '.join(defines)}")
            else:
                lines.append(f"- {fname}")
        return "\n".join(lines) if lines else "（无结构数据）"

    @staticmethod
    def _read_source_files(
        repo_path: str,
        file_names: list[str],
        max_total_bytes: int = 50_000,
    ) -> str:
        """Read actual source files from disk when data is sparse."""
        repo = Path(repo_path)
        collected: list[str] = []
        total = 0

        for name in file_names:
            if Path(name).suffix not in _SOURCE_EXTS:
                continue
            candidate = repo / name
            if not candidate.is_file():
                for p in repo.rglob(name):
                    if p.is_file():
                        candidate = p
                        break
                else:
                    continue
            try:
                size = candidate.stat().st_size
                if total + size > max_total_bytes:
                    break
                content = candidate.read_text(encoding="utf-8", errors="replace")
                collected.append(
                    f"### {candidate.relative_to(repo)}\n```{candidate.suffix.lstrip('.')}\n{content}\n```"
                )
                total += size
            except OSError:
                continue

        if not collected:
            return ""
        return "## 源代码内容（以下为实际文件内容，请据此分析）\n\n" + "\n\n".join(collected)

    def _find_cross_module_deps(self, module_file_ids: list[str]) -> list[str]:
        """Find 1-hop dependency files from other modules via CALLS/IMPORTS/DEPENDS_ON edges."""
        relationships = self._gitnexus_data.get("relationships", [])
        nodes = self._gitnexus_data.get("nodes", [])
        if not relationships:
            return []

        module_set = set(module_file_ids)
        nodes_by_id = {n["id"]: n for n in nodes}

        # Build symbol→file reverse mapping from DEFINES/CONTAINS edges
        symbol_to_file: dict[str, str] = {}
        for edge in relationships:
            if edge.get("type") in ("DEFINES", "CONTAINS"):
                file_id = edge.get("sourceId", "")
                symbol_id = edge.get("targetId", "")
                if symbol_id and file_id:
                    symbol_to_file[symbol_id] = file_id

        dep_ids: set[str] = set()
        for edge in relationships:
            if edge.get("type") not in ("CALLS", "IMPORTS", "DEPENDS_ON"):
                continue
            src, tgt = edge.get("sourceId", ""), edge.get("targetId", "")
            # Resolve source: if src is a symbol, check its parent file
            src_file = symbol_to_file.get(src, src)
            if src_file not in module_set:
                continue
            # Resolve target: if tgt is a symbol, find its parent file
            tgt_file = symbol_to_file.get(tgt, tgt)
            if tgt_file not in module_set:
                dep_ids.add(tgt_file)

        dep_names: list[str] = []
        for did in dep_ids:
            node = nodes_by_id.get(did, {})
            name = node.get("properties", {}).get("name", did)
            if name.startswith("File:"):
                name = name[5:]
            if Path(name).suffix in _SOURCE_EXTS:
                dep_names.append(name)
            if len(dep_names) >= MAX_DEP_FILES:
                break

        if dep_names:
            logger.info(
                "Module deps: %d cross-module files found (from %d edges)",
                len(dep_names),
                len(dep_ids),
            )
        return dep_names

    async def _analyze_module(
        self, llm_client: BaseLLMClient, community: dict
    ) -> str:
        """Call LLM to summarize a single module."""
        files = community.get("files", [])[:30]
        file_list = "\n".join(f"- {f}" for f in files)
        call_relations = "\n".join(community.get("calls", [])[:20])

        wiki_content = self._extract_module_wiki(community["name"])

        structural_info = self._build_structural_info(files)

        source_code_section = ""
        if not community.get("calls"):
            file_names = []
            nodes_by_id = {n["id"]: n for n in self._gitnexus_data.get("nodes", [])}
            for fid in files:
                node = nodes_by_id.get(fid, {})
                name = node.get("properties", {}).get("name", fid)
                if name.startswith("File:"):
                    name = name[5:]
                file_names.append(name)

            if not file_names:
                file_names = [Path(fid).name for fid in files if Path(fid).suffix in _SOURCE_EXTS]

            repo_path = self._repo_path or ""
            if repo_path and file_names:
                source_code_section = self._read_source_files(repo_path, file_names)

        dep_section = ""
        dep_names = self._find_cross_module_deps(files)
        if dep_names and self._repo_path:
            dep_source = self._read_source_files(
                self._repo_path, dep_names, max_total_bytes=DEP_BUDGET_BYTES
            )
            if dep_source:
                dep_section = dep_source.replace(
                    "## 源代码内容（以下为实际文件内容，请据此分析）",
                    "## 跨模块依赖源码（以下为本模块直接调用的外部文件，仅供上下文参考）",
                )

        combined_source = source_code_section
        if dep_section:
            combined_source = (combined_source + "\n\n" + dep_section).strip()

        prompt = MODULE_SUMMARY_PROMPT.format(
            module_name=community["name"],
            file_list=file_list or "（无文件信息）",
            structural_info=structural_info,
            call_relations=call_relations or "（无调用关系信息）",
            wiki_content=wiki_content or "（无 Wiki 文档）",
            source_code_section=combined_source,
        )

        if self._prompt_content:
            prompt = (
                f"## 用户自定义分析提示词\n{self._prompt_content[:4000]}\n\n"
                "请参考以上分析方法论和要求展开模块分析。\n\n"
                + prompt
            )
        elif self._analysis_focus:
            prompt = (
                f"## 用户分析目标\n{self._analysis_focus}\n"
                "请在模块分析时重点关注与此目标相关的内容。\n\n"
                + prompt
            )

        messages = [{"role": "user", "content": prompt}]

        has_real_streaming = (
            type(llm_client).stream_complete is not BaseLLMClient.stream_complete
        )
        if has_real_streaming:
            content = await llm_client.stream_complete_collected(
                messages=messages,
                max_tokens=min(MAX_OUTPUT_TOKENS, settings.llm_max_output_tokens),
                temperature=0.3,
            )
            tokens = BaseLLMClient.estimate_tokens(content)
        else:
            response: LLMResponse = await llm_client.complete(
                messages=messages,
                max_tokens=min(MAX_OUTPUT_TOKENS, settings.llm_max_output_tokens),
                temperature=0.3,
            )
            content = response.content
            tokens = response.usage.get("total_tokens", 0)

        logger.info(
            "Module %s analyzed: %d tokens used",
            community["name"],
            tokens,
        )
        return content

    def _extract_module_wiki(self, module_name: str) -> str:
        """Extract relevant wiki content for a module (word-boundary match)."""
        doc = self._deepwiki_data.get("documentation", "")
        if not doc:
            return ""

        # Use word-boundary regex to avoid false positives for short names
        pattern = re.compile(r"\b" + re.escape(module_name) + r"\b", re.IGNORECASE)

        lines = doc.split("\n")
        relevant: list[str] = []
        capturing = False
        for line in lines:
            if pattern.search(line):
                capturing = True
            if capturing:
                relevant.append(line)
                if len(relevant) > 30:
                    break
            elif relevant and line.strip() == "":
                capturing = False

        result = "\n".join(relevant)
        max_chars = 3000
        if len(result) > max_chars:
            # Preserve sentence boundary: find last period or newline before limit
            truncated = result[:max_chars]
            last_period = truncated.rfind(".")
            last_newline = truncated.rfind("\n")
            cut_at = max(last_period, last_newline)
            if cut_at > max_chars // 2:
                result = result[: cut_at + 1]
            else:
                result = truncated
            logger.info(
                "Wiki content for module %s truncated from %d to %d chars",
                module_name,
                len("\n".join(relevant)),
                len(result),
            )
            result += "\n...（已截断）"
        return result

    # ------------------------------------------------------------------
    # Phase 4: Cross-enhancement (optional)
    # ------------------------------------------------------------------

    async def _phase_cross_enhance(self) -> None:
        """Best-effort cross-enhancement: synthesise GitNexus + DeepWiki insights via LLM."""
        has_gitnexus = bool(self._gitnexus_data and self._gitnexus_data.get("nodes"))
        has_deepwiki = bool(self._deepwiki_data and self._deepwiki_data.get("documentation"))

        if not (has_gitnexus and has_deepwiki):
            logger.info("Cross-enhancement phase: skipping — single source")
            return

        if self._output_dir is None or self._llm_client is None:
            logger.warning("Cross-enhancement phase: skipping — output_dir or llm_client not set")
            return

        try:
            # Build module summary highlights (first 2000 chars of joined summaries)
            joined_summaries = "\n\n".join(
                f"### {ms['module_name']}\n{ms['summary']}"
                for ms in self._module_summaries
            )
            summary_highlights = joined_summaries[:2000]

            # Build GitNexus structural insights
            nodes = self._gitnexus_data.get("nodes", [])
            relationships = self._gitnexus_data.get("relationships", [])
            node_count = len(nodes)
            edge_count = len(relationships)

            # Top cross-module deps: source_community -> target_community
            member_to_community: dict[str, str] = {}
            for edge in relationships:
                if edge.get("type") == "MEMBER_OF":
                    member_to_community[edge["sourceId"]] = edge["targetId"]

            cross_dep_counts: dict[str, int] = {}
            for edge in relationships:
                if edge.get("type") in ("CALLS", "IMPORTS", "DEPENDS_ON"):
                    src_c = member_to_community.get(edge["sourceId"])
                    tgt_c = member_to_community.get(edge["targetId"])
                    if src_c and tgt_c and src_c != tgt_c:
                        key = f"{src_c} -> {tgt_c}"
                        cross_dep_counts[key] = cross_dep_counts.get(key, 0) + 1

            top_cross_deps = sorted(cross_dep_counts.items(), key=lambda x: x[1], reverse=True)[:10]
            cross_deps_text = "\n".join(f"- {dep} ({count} 次)" for dep, count in top_cross_deps)

            gitnexus_insights = (
                f"节点数: {node_count}，边数: {edge_count}\n"
                f"跨模块依赖（Top 10）:\n{cross_deps_text or '（无跨模块依赖）'}"
            )

            # Build DeepWiki documentation highlights (first 2000 chars)
            deepwiki_doc = self._deepwiki_data.get("documentation", "")
            deepwiki_highlights = deepwiki_doc[:2000]

            prompt = (
                "## 任务：交叉增强分析\n\n"
                "你是一位资深软件架构师。请综合以下三个来源的信息，生成一份交叉增强分析报告。\n"
                "报告应重点揭示：代码结构与文档描述之间的差异或一致性、潜在的架构风险、"
                "以及可从多源数据交叉印证的关键洞察。\n\n"
                "### 模块摘要亮点\n"
                f"{summary_highlights}\n\n"
                "### GitNexus 结构洞察\n"
                f"{gitnexus_insights}\n\n"
                "### DeepWiki 文档亮点\n"
                f"{deepwiki_highlights}\n\n"
                "请输出一份结构化的交叉增强分析，包含：\n"
                "1. 代码结构与文档的一致性评估\n"
                "2. 发现的主要矛盾或盲点\n"
                "3. 跨工具综合洞察（架构风险、优化方向）\n"
            )

            messages = [{"role": "user", "content": prompt}]

            has_real_streaming = (
                type(self._llm_client).stream_complete
                is not BaseLLMClient.stream_complete
            )
            if has_real_streaming:
                cross_content = await self._llm_client.stream_complete_collected(
                    messages=messages,
                    max_tokens=min(MAX_OUTPUT_TOKENS, settings.llm_max_output_tokens),
                    temperature=0.3,
                )
                cross_tokens = BaseLLMClient.estimate_tokens(cross_content)
                model_name = type(self._llm_client).__name__
            else:
                response: LLMResponse = await self._llm_client.complete(
                    messages=messages,
                    max_tokens=min(MAX_OUTPUT_TOKENS, settings.llm_max_output_tokens),
                    temperature=0.3,
                )
                cross_content = response.content
                cross_tokens = response.usage.get("total_tokens", 0)
                model_name = response.model

            # Save with YAML frontmatter
            now = datetime.now(timezone.utc).isoformat()
            header = (
                f"---\n"
                f"report_type: cross_enhancement\n"
                f"task_id: {self._task_id}\n"
                f"generated_at: {now}\n"
                f"model: {model_name}\n"
                f"tokens: {cross_tokens}\n"
                f"---\n\n"
            )

            out_path = self._output_dir / "05-交叉增强分析.md"

            def _write() -> None:
                out_path.write_text(header + cross_content, encoding="utf-8")

            await asyncio.to_thread(_write)
            logger.info(
                "Cross-enhancement analysis saved: %d tokens",
                cross_tokens,
            )

        except Exception as exc:
            logger.warning("Cross-enhancement phase failed (best-effort): %s", exc)

    # ------------------------------------------------------------------
    # Task 10 & 13: Cache helpers
    # ------------------------------------------------------------------

    async def _get_repo_commit_hash(self, repo_path: str) -> str:
        """Return the HEAD commit hash of the repo, or '' on failure."""
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                ["git", "-C", repo_path, "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0:
                return proc.stdout.strip()
        except Exception as exc:
            logger.warning("Failed to get commit hash for %s: %s", repo_path, exc)
        return ""

    async def _build_gitnexus_cache_key(self, repo_path: str) -> str:
        """Build cache key: <path_hash>_<commit_hash>."""
        commit = await self._get_repo_commit_hash(repo_path)
        if not commit:
            return ""
        import hashlib
        path_hash = hashlib.md5(str(Path(repo_path).resolve()).encode()).hexdigest()[:8]
        return f"{path_hash}_{commit}"

    def _compute_intent_hash(self) -> str:
        """8-char hash of task intent + data quality for cache keying."""
        import hashlib
        intent_parts = [
            _PIPELINE_VERSION,
            self._analysis_focus or "",
            self._prompt_content or "",
            self._deepwiki_depth or "",
            json.dumps(sorted(self._tools)) if hasattr(self, "_tools") else "",
            self._data_quality or "good",
        ]
        return hashlib.sha256("|".join(intent_parts).encode()).hexdigest()[:8]

    def _repo_path_hash(self) -> str:
        """8-char hash of repo absolute path for cache isolation."""
        import hashlib
        return hashlib.md5(str(Path(self._repo_path).resolve()).encode()).hexdigest()[:8]

    def _build_module_cache_key(self, commit: str) -> str:
        """Build module-summary cache key: path_hash + commit + intent/quality hash."""
        return f"{self._repo_path_hash()}_{commit}_{self._compute_intent_hash()}"

    def _cache_dir(self) -> Path:
        """Return (and create) the .cache directory under outputs."""
        cache_path = settings.outputs_path / ".cache"
        cache_path.mkdir(parents=True, exist_ok=True)
        return cache_path

    async def _load_gitnexus_cache(self, cache_key: str) -> dict | None:
        """Load cached GitNexus data if it exists."""
        cache_file = self._cache_dir() / f"gitnexus_{cache_key}.json"

        def _read() -> dict | None:
            if cache_file.exists():
                try:
                    return json.loads(cache_file.read_text(encoding="utf-8"))
                except Exception as exc:
                    logger.warning("GitNexus cache read error (%s): %s", cache_key, exc)
            return None

        return await asyncio.to_thread(_read)

    async def _save_gitnexus_cache(self, cache_key: str, data: dict) -> None:
        """Persist GitNexus data to cache."""
        cache_file = self._cache_dir() / f"gitnexus_{cache_key}.json"

        def _write() -> None:
            try:
                cache_file.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as exc:
                logger.warning("GitNexus cache write error (%s): %s", cache_key, exc)

        await asyncio.to_thread(_write)

    async def _load_module_summaries_cache(self, cache_key: str) -> list[dict] | None:
        """Load cached module summaries if they exist for this commit."""
        cache_file = self._cache_dir() / f"modules_{cache_key}.json"

        def _read() -> list[dict] | None:
            if cache_file.exists():
                try:
                    return json.loads(cache_file.read_text(encoding="utf-8"))
                except Exception as exc:
                    logger.warning("Module cache read error (%s): %s", cache_key, exc)
            return None

        return await asyncio.to_thread(_read)

    async def _save_module_summaries_cache(self, cache_key: str, summaries: list[dict]) -> None:
        """Persist module summaries to cache."""
        cache_file = self._cache_dir() / f"modules_{cache_key}.json"

        def _write() -> None:
            try:
                cache_file.write_text(
                    json.dumps(summaries, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as exc:
                logger.warning("Module cache write error (%s): %s", cache_key, exc)

        await asyncio.to_thread(_write)
        logger.info("Module summaries cached: %d modules (key=%s)", len(summaries), cache_key)

    async def _load_latest_module_summaries_cache(self) -> tuple[list[dict] | None, str]:
        """
        Task 15: find the most recently written modules_*.json cache file
        whose repo path hash AND intent hash match the current task.
        Returns (summaries, commit_hash) or (None, '').
        """
        current_repo = self._repo_path_hash()
        current_intent = self._compute_intent_hash()

        def _find() -> tuple[list[dict] | None, str]:
            cache_dir = self._cache_dir()
            candidates = sorted(
                cache_dir.glob("modules_*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for candidate in candidates:
                try:
                    # stem: modules_{path_hash}_{commit}_{intent_hash}
                    parts = candidate.stem.split("_")
                    if len(parts) < 4:
                        continue
                    path_part = parts[1]
                    commit_part = parts[2]
                    intent_part = parts[3]
                    if path_part != current_repo:
                        continue
                    if not re.fullmatch(r"[0-9a-f]{40}", commit_part):
                        continue
                    if intent_part != current_intent:
                        continue
                    data = json.loads(candidate.read_text(encoding="utf-8"))
                    return data, commit_part
                except Exception as exc:
                    logger.warning("Failed reading cache candidate %s: %s", candidate, exc)
            return None, ""

        return await asyncio.to_thread(_find)

    async def _get_changed_files(
        self, repo_path: str, old_commit: str, new_commit: str
    ) -> set[str] | None:
        """Return repo-relative paths changed between two commits, or None if the diff fails."""
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                ["git", "-C", repo_path, "diff", "--name-only", old_commit, new_commit],
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0:
                return {line.strip() for line in proc.stdout.splitlines() if line.strip()}
        except Exception as exc:
            logger.warning("Failed to get changed files (%s..%s): %s", old_commit, new_commit, exc)
        return None

    @staticmethod
    def _normalize_to_relative(paths: set[str], repo_root: str) -> set[str]:
        """Normalize paths to repo-relative POSIX strings for intersection with git diff output."""
        root = Path(repo_root).resolve()
        result: set[str] = set()
        for p in paths:
            try:
                rel = Path(p).resolve().relative_to(root)
                result.add(rel.as_posix())
            except ValueError:
                # Already relative or outside repo — normalize separators only
                result.add(Path(p).as_posix())
        return result

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
        current_step: str | None = None,
    ) -> None:
        """Update task progress, status, and current step in SQLite."""
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(settings.sqlite_db) as db:
            if error_message is not None:
                await db.execute(
                    "UPDATE tasks SET progress = ?, status = ?, error_message = ?, "
                    "updated_at = ?, current_step = COALESCE(?, current_step) WHERE id = ?",
                    (progress, status, error_message, now, current_step, task_id),
                )
            else:
                await db.execute(
                    "UPDATE tasks SET progress = ?, status = ?, updated_at = ?, "
                    "current_step = COALESCE(?, current_step) WHERE id = ?",
                    (progress, status, now, current_step, task_id),
                )
            await db.commit()

        if current_step is not None:
            await AnalysisPipeline._log_step(task_id, progress, current_step)

    @staticmethod
    async def _log_step(task_id: str, progress: int, step: str) -> None:
        """Append a timestamped step entry to outputs/{task_id}/steps.jsonl."""
        ts = datetime.now(timezone.utc).isoformat()
        entry = json.dumps(
            {"timestamp": ts, "progress": progress, "step": step},
            ensure_ascii=False,
        )

        def _write() -> None:
            step_file = settings.outputs_path / task_id / "steps.jsonl"
            step_file.parent.mkdir(parents=True, exist_ok=True)
            with step_file.open("a", encoding="utf-8") as f:
                f.write(entry + "\n")

        try:
            await asyncio.to_thread(_write)
        except Exception as exc:
            logger.warning("Step log write failed for %s: %s", task_id, exc)
