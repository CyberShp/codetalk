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

from app.adapters import create_adapter
from app.adapters.base import AnalysisRequest, BaseToolAdapter
from app.config import settings
from app.llm.base import (
    BaseLLMClient,
    LLMResponse,
    current_task_id,
    reset_truncation_count,
)
from app.llm.factory import create_llm_client_from_active
from app.prompts.templates import MODULE_SUMMARY_PROMPT
from app.schemas.workspace_analysis import AnalysisPlan, ScopePreview
from app.services.analysis_artifacts import write_analysis_artifacts
from app.services.evidence_card_builder import EvidenceCard, EvidenceCardBuilder
from app.services.report_generator import ReportGenerator
from app.services.process_manager import ProcessManager
from app.services.workspace_scope_resolver import (
    normalize_file_key,
    plan_analysis_units,
)
from app.utils.repo_paths import to_tool_repo_path

logger = logging.getLogger(__name__)


# Per-task locks serialising appends to steps.jsonl.  Under high
# LLM/report concurrency, unsynchronised ``to_thread`` appends interleaved and
# corrupted the file with half-written lines (Round 2/3: ``fo"}`` /
# ``lestone", ...``).  One async lock per task makes every append atomic.
_STEPS_LOCKS: dict[str, asyncio.Lock] = {}


def _steps_lock_for(task_id: str) -> asyncio.Lock:
    lock = _STEPS_LOCKS.get(task_id)
    if lock is None:
        lock = asyncio.Lock()
        _STEPS_LOCKS[task_id] = lock
    return lock


class _CancelledError(Exception):
    """Raised inside the pipeline when the user cancels the task."""


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
    ".py", ".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".java", ".go", ".rs",
    ".c", ".h", ".hh", ".hpp", ".hxx", ".cc", ".cpp", ".cxx", ".ipp", ".inl",
    ".cs", ".rb", ".php", ".kt", ".kts", ".swift", ".m", ".scala",
    ".vue", ".svelte", ".astro", ".mdx",
})

# CGC business-level query limits
_CGC_MAX_SYMBOLS_PER_OBJECT: int = 3
_CGC_NAME_DISPLAY_LIMIT: int = 6


def _extract_cgc_names(items: list, *, limit: int = _CGC_NAME_DISPLAY_LIMIT) -> list[str]:
    """Extract readable symbol names from a CGC result list."""
    names: list[str] = []
    for item in items[:limit]:
        if isinstance(item, dict):
            name = (
                item.get("name")
                or item.get("symbol")
                or item.get("function")
                or str(item)
            )
        else:
            name = str(item)
        names.append(name)
    return names


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


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
        # F-WORKSPACE-GITNEXUS-ANALYSIS-TASK-REDESIGN
        self._analysis_plan: AnalysisPlan | None = None
        self._scope_preview: ScopePreview | None = None
        self._evidence_cards: list[EvidenceCard] = []
        self._analysis_units: list[dict] = []
        # Adapter instances created during prepare phase, keyed by tool name
        self._tool_adapters: dict[str, BaseToolAdapter] = {}
        # Set by _assess_tool_health() after prepare; controls data-collection and injection gating
        self._pipeline_mode: str = "dual"  # "dual" | "gitnexus_only" | "cgc_only" | "llm_direct"
        self._tool_health_warning: str = ""  # user-visible degradation notice
        self._gitnexus_index_root: str = ""  # repo name/path GitNexus actually used
        self._gitnexus_index_path: str = ""  # resolved on-disk path of the indexed repo
        self._gitnexus_stats: dict = {}      # {expected:{...}, actual:{nodes,edges}, matched:bool}
        self._gitnexus_extra_degraded: list[str] = []  # e.g. ["gitnexus_repo_ambiguous"]
        self._cgc_index_paths: list[str] = []

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def _check_cancelled(self, cancel_event: asyncio.Event | None) -> None:
        if cancel_event and cancel_event.is_set():
            raise _CancelledError()

    async def run(self, task_id: str, cancel_event: asyncio.Event | None = None) -> None:
        """Execute the full pipeline, updating task progress in the DB."""
        logger.info("Pipeline started for task %s", task_id, extra={"task_id": task_id})
        self._task_id = task_id
        _ctx_token = current_task_id.set(task_id)
        # Start each run with a clean truncation tally (Round 2/3 health signal).
        reset_truncation_count(task_id)

        try:
            task = await self._load_task(task_id)
            repo_path = task["repo_path"]
            self._repo_path = repo_path  # store for use by new methods
            tools = json.loads(task.get("tools") or "[]")
            self._tools = tools
            self._analysis_focus = task.get("analysis_focus") or ""
            self._prompt_content = task.get("prompt_content") or ""
            self._deepwiki_depth = task.get("deepwiki_depth") or ""

            # Plan-driven path (F-WORKSPACE-GITNEXUS-ANALYSIS-TASK-REDESIGN).
            plan_raw = task.get("analysis_plan_json")
            preview_raw = task.get("scope_preview_json")
            if plan_raw:
                try:
                    self._analysis_plan = AnalysisPlan.model_validate_json(plan_raw)
                except Exception as exc:
                    logger.warning("Could not parse analysis_plan_json: %s", exc)
                    self._analysis_plan = None
            if preview_raw:
                try:
                    self._scope_preview = ScopePreview.model_validate_json(preview_raw)
                except Exception as exc:
                    logger.warning("Could not parse scope_preview_json: %s", exc)
                    self._scope_preview = None

            await self._update_progress(task_id, 0, "running", None, "启动分析管道…")

            # Phase 0: Preparation
            self._check_cancelled(cancel_event)
            await self._phase_prepare(repo_path, tools)
            await self._assess_tool_health()
            if self._tool_health_warning:
                await self._log_step(task_id, 10, self._tool_health_warning)
            await self._update_progress(task_id, 10, "running", None, "环境准备完成，开始采集数据…")

            # Phase 1: Data Collection
            self._check_cancelled(cancel_event)
            await self._phase_collect(repo_path, tools)
            await self._update_progress(task_id, 40, "running", None, "数据采集完成，开始 AI 模块分析…")

            # Task 5: Save DeepWiki documentation as independent output
            output_dir = settings.outputs_path / task_id
            output_dir.mkdir(parents=True, exist_ok=True)
            await self._save_deepwiki_output(output_dir, task_id)

            # Phase 2: Per-module Analysis (MapReduce)
            llm_client = await self._try_create_llm_client()
            if llm_client:
                self._check_cancelled(cancel_event)
                if self._analysis_plan is not None:
                    await self._phase_plan_driven_analysis(llm_client)
                else:
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
                self._check_cancelled(cancel_event)
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

                # Single global semaphore owned by the pipeline — both report and
                # section LLM calls compete for the same budget so total concurrent
                # LLM calls never exceed LLM_MAX_CONCURRENCY.
                llm_sem = asyncio.Semaphore(max(1, settings.llm_max_concurrency))

                if self._analysis_plan is not None:
                    await generator.generate_from_plan(
                        plan=self._analysis_plan,
                        scope_preview=self._scope_preview,
                        analysis_units=self._analysis_units,
                        evidence_cards=self._evidence_cards,
                        module_summaries=self._module_summaries,
                        gitnexus_data=self._gitnexus_data,
                        deepwiki_data=self._deepwiki_data,
                        requirements_doc=task.get("requirements_doc"),
                        design_doc=task.get("design_doc"),
                        on_report_done=_on_report_done,
                        on_report_start=_on_report_start,
                        on_report_failed=_on_report_failed,
                        sem=llm_sem,
                        data_quality=self._data_quality,
                        repo_path=self._repo_path,
                        pipeline_mode=self._pipeline_mode,
                        extra_degraded=self._gitnexus_extra_degraded,
                        index_coverage={
                            "agent_cwd": os.getcwd(),
                            "target_path": self._repo_path,
                            "gitnexus_index_root": self._gitnexus_index_root or "(未解析)",
                            "gitnexus_index_path": self._gitnexus_index_path or "(未解析)",
                            "gitnexus_stats": self._gitnexus_stats or {},
                            "cgc_index_root": (
                                ", ".join(self._cgc_index_paths)
                                if self._pipeline_mode in ("dual", "cgc_only") and self._cgc_index_paths
                                else ("已索引" if self._pipeline_mode in ("dual", "cgc_only") else "不可用")
                            ),
                        },
                    )
                else:
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
                        sem=llm_sem,
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

            done_msg = self._final_done_message(
                final_status,
                degraded=self._gitnexus_extra_degraded,
                all_modules_failed=all_modules_failed,
                no_reports=no_reports,
            )
            await self._update_progress(task_id, 100, final_status, None, done_msg)
            logger.info("Pipeline completed for task %s (status=%s)", task_id, final_status, extra={"task_id": task_id})

        except _CancelledError:
            logger.info("Pipeline cancelled for task %s", task_id, extra={"task_id": task_id})
        except Exception as exc:
            logger.exception("Pipeline failed for task %s", task_id, extra={"task_id": task_id})
            await self._update_progress(task_id, -1, "failed", str(exc), f"分析失败：{exc}")

        finally:
            current_task_id.reset(_ctx_token)

    # ------------------------------------------------------------------
    # Phase 0: Preparation
    # ------------------------------------------------------------------

    def _derive_cgc_index_paths(self, repo_path: str) -> list[str]:
        """Pick the smallest source directories CGC should index for this plan."""
        root = Path(repo_path).resolve()
        candidates: list[Path] = []

        def _add(raw: str | None) -> None:
            text = (raw or "").strip()
            if not text:
                return
            path = Path(text)
            if not path.is_absolute():
                path = root / text
            try:
                resolved = path.resolve(strict=False)
            except OSError:
                resolved = path
            try:
                resolved.relative_to(root)
            except ValueError:
                return
            if resolved == root:
                return
            if path.exists() and path.is_dir():
                index_path = resolved
            elif (path.exists() and path.is_file()) or resolved.suffix.lower() in _SOURCE_EXTS:
                index_path = resolved.parent
            else:
                index_path = resolved if not resolved.suffix else resolved.parent
            if index_path != root:
                candidates.append(index_path)

        scope = self._scope_preview
        if scope is not None:
            for resolved in scope.resolved_objects:
                for cand in resolved.candidate_files:
                    _add(cand.path)

        plan = self._analysis_plan
        if plan is not None:
            for obj in plan.analysis_objects:
                for hint in obj.path_hints:
                    _add(hint)

        if not candidates:
            return [str(root)]

        deduped: list[Path] = []
        seen: set[str] = set()
        for path in candidates:
            key = os.path.normcase(str(path))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(path)

        narrow_first = sorted(deduped, key=lambda p: len(p.parts), reverse=True)
        scoped: list[Path] = []
        for path in narrow_first:
            if any(_is_relative_to(existing, path) for existing in scoped):
                continue
            scoped.append(path)
        scoped.sort(key=lambda p: os.path.normcase(str(p)))
        return [str(path) for path in scoped[:8]] or [str(root)]

    @staticmethod
    def _final_done_message(
        final_status: str,
        *,
        degraded: list[str] | None = None,
        all_modules_failed: bool = False,
        no_reports: bool = False,
    ) -> str:
        if final_status == "failed":
            return "分析失败：所有 AI 输出均未成功"
        if final_status == "completed_with_warnings":
            if all_modules_failed or no_reports:
                return "分析完成（部分内容生成失败）"
            if degraded:
                return "分析完成（存在降级警告）"
            return "分析完成（存在警告）"
        return "分析完成"

    async def _phase_prepare(self, repo_path: str, tools: list[str]) -> None:
        """Validate repo path, git init, and run adapter prepare in parallel."""
        path = Path(repo_path)
        if not path.exists():
            raise FileNotFoundError(f"代码路径不存在: {repo_path}")

        req = AnalysisRequest(repo_local_path=repo_path)
        self._cgc_index_paths = self._derive_cgc_index_paths(repo_path)
        cgc_req = AnalysisRequest(
            repo_local_path=repo_path,
            options={"cgc_index_paths": self._cgc_index_paths},
        )
        self._tool_adapters = {}

        # During prepare(), GitNexus may index for several minutes.  Surface its
        # progress into the 1-9% band so the page shows movement instead of a
        # frozen 0% (P0-002 UX half).
        async def _gitnexus_prepare_progress(pct: int) -> None:
            mapped = 1 + int(max(0, min(100, pct)) * 0.08)  # 1..9
            await self._update_progress(
                self._task_id, mapped, "running", None,
                f"GitNexus 索引中… {max(0, min(100, pct))}%",
            )

        adapter_coros = []
        for tool_name in tools:
            try:
                adapter = create_adapter(tool_name)
                self._tool_adapters[tool_name] = adapter
                if tool_name == "gitnexus":
                    adapter_coros.append(
                        adapter.prepare(req, on_progress=_gitnexus_prepare_progress)
                    )
                elif tool_name == "cgc":
                    adapter_coros.append(adapter.prepare(cgc_req))
                else:
                    adapter_coros.append(adapter.prepare(req))
            except KeyError:
                pass  # no registered adapter for this tool

        # Soft-add CGC for health-checking and evidence injection even when not in user's tools list.
        if "cgc" not in self._tool_adapters:
            try:
                cgc_adapter = create_adapter("cgc")
                self._tool_adapters["cgc"] = cgc_adapter
                adapter_coros.append(cgc_adapter.prepare(cgc_req))
            except Exception:
                pass  # CGC unavailable — will degrade gracefully in _assess_tool_health

        if self._cgc_index_paths and self._cgc_index_paths != [str(path.resolve())]:
            await AnalysisPipeline._log_step(
                self._task_id, 8,
                "CGC scope-aware index paths selected",
                event_type="scope", phase="prepare",
                detail={"paths": self._cgc_index_paths},
            )

        results = await asyncio.gather(
            self._ensure_git_init(path),
            *adapter_coros,
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                logger.warning("Prepare step error (non-fatal): %s", result)

    async def _assess_tool_health(self) -> None:
        """Check adapter health after prepare and set _pipeline_mode for graceful degradation.

        Modes:
          dual          — both CGC and GitNexus healthy (default)
          gitnexus_only — CGC unavailable; evidence injection skipped
          cgc_only      — GitNexus unavailable; gitnexus data collection skipped
          llm_direct    — both unavailable; pipeline runs on file-content only
        """
        async def _check(tool_name: str, adapter: BaseToolAdapter | None) -> bool:
            if adapter is None:
                return False
            try:
                health = await asyncio.wait_for(adapter.health_check(), timeout=5.0)
                if bool(health.is_healthy):
                    return True
            except Exception as exc:
                logger.debug("%s health check failed before managed start: %s", tool_name, exc)
            if tool_name == "gitnexus":
                return await self._try_start_managed_gitnexus(adapter)
            return False

        gitnexus_ok, cgc_ok = await asyncio.gather(
            _check("gitnexus", self._tool_adapters.get("gitnexus")),
            _check("cgc", self._tool_adapters.get("cgc")),
        )

        if gitnexus_ok and cgc_ok:
            self._pipeline_mode = "dual"
        elif gitnexus_ok:
            self._pipeline_mode = "gitnexus_only"
            self._tool_health_warning = "⚠️ CGC 不可用，已降级为单图模式（GitNexus）"
            logger.warning("CGC unavailable — degrading to gitnexus_only mode")
        elif cgc_ok:
            self._pipeline_mode = "cgc_only"
            self._tool_health_warning = "⚠️ GitNexus 不可用，已降级为单图模式（CGC）"
            logger.warning("GitNexus unavailable — degrading to cgc_only mode")
        else:
            self._pipeline_mode = "llm_direct"
            self._tool_health_warning = "⚠️ 图谱工具均不可用，已降级为 LLM 直读模式（结果质量受影响）"
            logger.warning("Both GitNexus and CGC unavailable — degrading to llm_direct mode")

    async def _try_start_managed_gitnexus(self, adapter: BaseToolAdapter) -> bool:
        """Start locally managed GitNexus once before accepting degradation."""
        try:
            process_manager = ProcessManager.get_instance()
            started = await asyncio.wait_for(process_manager.start("gitnexus"), timeout=10.0)
        except Exception as exc:
            logger.warning("GitNexus managed start failed before degradation: %s", exc)
            return False
        if not started:
            logger.warning("GitNexus managed start returned false before degradation")
            return False

        for attempt in range(8):
            if attempt:
                await asyncio.sleep(1)
            try:
                health = await asyncio.wait_for(adapter.health_check(), timeout=5.0)
                if health.is_healthy:
                    logger.info("GitNexus became healthy after managed start")
                    return True
            except Exception as exc:
                logger.debug("GitNexus recheck after managed start failed: %s", exc)
        logger.warning("GitNexus remained unhealthy after managed start")
        return False

    async def _ensure_git_init(self, path: Path) -> None:
        """Ensure git is initialized for the repo path."""
        git_dir = path / ".git"
        if not git_dir.exists():
            logger.info("Initializing git repo at %s", path)
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

        if "gitnexus" in tools and self._pipeline_mode not in ("cgc_only", "llm_direct"):
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

    @staticmethod
    async def _gitnexus_resolve_repo(
        client: httpx.AsyncClient, tool_repo_path: str
    ) -> dict | None:
        """Resolve the indexed GitNexus repo *descriptor* for *tool_repo_path*.

        Returns ``{name, path, id, node_count, edge_count, ambiguous, ...}`` or
        None.  Matches by normalized path (handles the real /api/repos top-level
        array with duplicate names), and flags same-name ambiguity so the graph
        fetch can verify it pulled the right repo (Round 4 P1).  Never raises.
        """
        try:
            from app.adapters.gitnexus import resolve_indexed_repo
            resp = await client.get("/api/repos", timeout=10)
            if resp.status_code != 200:
                return None
            return resolve_indexed_repo(resp.json(), tool_repo_path)
        except Exception as exc:
            logger.debug("GitNexus repo resolve probe failed (non-fatal): %s", exc)
            return None

    @staticmethod
    def _gitnexus_graph_counts(graph: dict) -> tuple[int, int]:
        """Return node/edge counts across GitNexus graph response variants."""
        edges = graph.get("relationships")
        if edges is None:
            edges = graph.get("edges", [])
        return len(graph.get("nodes", [])), len(edges or [])

    async def _fetch_gitnexus_graph(
        self,
        client: httpx.AsyncClient,
        repo_name: str,
        descriptor: dict | None,
    ) -> dict:
        """GET the knowledge graph, disambiguating same-named repos by stats.

        When two indexed repos share ``repo_name``, ``GET /api/graph?repo=<name>``
        is ambiguous: GitNexus resolves by name and may return the wrong repo
        (Round 4 P1 — fetched D:\\...\\spdk instead of the target E:\\...\\spdk).
        We try the most path/id-specific query first and verify the returned
        node/edge counts against the resolved repo's expected stats.  If we
        cannot get a matching graph, we keep the response but record a degraded
        flag so the run is not silently trusted.
        """
        expected_nodes = (descriptor or {}).get("node_count")
        expected_edges = (descriptor or {}).get("edge_count")
        has_expected = expected_nodes is not None or expected_edges is not None
        ambiguous = bool(descriptor and descriptor.get("ambiguous"))

        # Most-specific identifiers first; only bother with extras when ambiguous.
        param_sets: list[dict] = []
        if ambiguous and descriptor:
            if descriptor.get("id"):
                param_sets.append({"repo": str(descriptor["id"])})
                param_sets.append({"repoId": str(descriptor["id"])})
            if descriptor.get("path"):
                param_sets.append({"repo": repo_name, "path": descriptor["path"]})
                param_sets.append({"path": descriptor["path"]})
        param_sets.append({"repo": repo_name})

        last_graph: dict | None = None
        last_params: dict | None = None
        matched = False
        seen_counts: set[tuple[int, int]] = set()

        for params in param_sets:
            resp = await client.get("/api/graph", params=params, timeout=120)
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            graph = resp.json()
            nodes, edges = self._gitnexus_graph_counts(graph)
            last_graph, last_params = graph, params
            if has_expected:
                ok_nodes = expected_nodes is None or expected_nodes == nodes
                ok_edges = expected_edges is None or expected_edges == edges
                if ok_nodes and ok_edges:
                    matched = True
                    break
                # If repeated param sets return identical counts, GitNexus is
                # ignoring our disambiguators — stop hammering the heavy endpoint.
                if (nodes, edges) in seen_counts:
                    break
                seen_counts.add((nodes, edges))
            else:
                matched = True  # nothing to verify against
                break

        if last_graph is None:
            resp = await client.get("/api/graph", timeout=120)
            resp.raise_for_status()
            last_graph = resp.json()
            last_params = {}

        actual_nodes, actual_edges = self._gitnexus_graph_counts(last_graph)
        self._gitnexus_stats = {
            "expected": {"nodes": expected_nodes, "edges": expected_edges},
            "actual": {"nodes": actual_nodes, "edges": actual_edges},
            "matched": matched or not has_expected,
            "query": last_params,
        }
        if ambiguous and has_expected and not matched:
            self._gitnexus_extra_degraded.append("gitnexus_repo_ambiguous")
            logger.error(
                "GitNexus same-name repo ambiguity: expected ~%s nodes for %s but "
                "graph returned %d nodes; reports may reference the wrong repo",
                expected_nodes, (descriptor or {}).get("path"), actual_nodes,
            )
            await AnalysisPipeline._log_step(
                self._task_id, 18,
                "⚠️ GitNexus 同名仓库歧义：按名取图与目标路径 stats 不一致，已标记 degraded",
                event_type="warning", phase="data_collection", level="warning",
                detail={
                    "expected_nodes": expected_nodes,
                    "actual_nodes": actual_nodes,
                    "target_path": (descriptor or {}).get("path"),
                },
            )
        return last_graph

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

        # Translate host path to the path GitNexus sees (matches prepare() in gitnexus.py).
        # In Docker mode the host path and the container-visible path differ; sending the raw
        # host path would cause GitNexus to return a 409 without repoName (unknown path) instead
        # of the 409+repoName it returns for a path it actually indexed.
        tool_repo_path = to_tool_repo_path(
            repo_path,
            host_base_path=settings.repos_base_path,
            tool_base_path=settings.tool_repos_base_path,
            local_host_path=settings.local_repos_host_path,
            local_container_path=settings.local_repos_container_path,
        )

        base_url = settings.gitnexus_base_url

        # P0-001: skip POST /api/analyze if GitNexusAdapter.prepare() already indexed this path,
        # avoiding redundant re-indexing (3-15 minutes) on every analyze call.
        repo_name: str | None = None
        try:
            from app.adapters.gitnexus import GitNexusAdapter  # lazy import, no circular risk
            cached = GitNexusAdapter._indexed_repo_by_path.get((base_url, tool_repo_path))
            if cached:
                repo_name = cached
                logger.info(
                    "GitNexus: skipping re-analyze, adapter already indexed as %s", repo_name
                )
        except Exception as exc:
            logger.debug("GitNexus adapter cache check failed (non-fatal): %s", exc)

        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(1800, connect=10),
            trust_env=False,
        ) as client:
            # P0-002: even with a cold in-process cache, GitNexus itself may
            # already hold this repo's graph.  Probe /api/repos for the derived
            # name first; if it's there, skip straight to GET /api/graph instead
            # of re-running a multi-minute POST /api/analyze.
            descriptor: dict | None = None
            if repo_name is None:
                descriptor = await self._gitnexus_resolve_repo(client, tool_repo_path)
                if descriptor:
                    repo_name = descriptor["name"]
                    self._gitnexus_index_path = descriptor.get("path") or ""
                    try:
                        from app.adapters.gitnexus import GitNexusAdapter
                        GitNexusAdapter._indexed_repo_by_path[(base_url, tool_repo_path)] = repo_name
                    except Exception:
                        pass
                    logger.info(
                        "GitNexus: repo already indexed as %s (resolved by path %s), "
                        "skipping re-analyze", repo_name, descriptor.get("path"),
                    )
                    await AnalysisPipeline._log_step(
                        self._task_id, 15, f"GitNexus 已索引（{repo_name}），跳过重复分析",
                        event_type="api_done", phase="data_collection",
                        detail={
                            "repo": repo_name,
                            "path": descriptor.get("path"),
                            "ambiguous": descriptor.get("ambiguous"),
                            "source": "repo_probe",
                        },
                    )

            if repo_name is None:
                await AnalysisPipeline._log_step(
                    self._task_id, 10, "GitNexus 开始索引代码库...",
                    event_type="api_call", phase="data_collection",
                    target={"path": tool_repo_path},
                    detail={"api": "POST /api/analyze", "endpoint": base_url},
                )
                resp = await client.post("/api/analyze", json={"path": tool_repo_path})

                job_id: str | None = None
                if resp.status_code == 409:
                    body = resp.json() if resp.content else {}
                    if body.get("jobId"):
                        job_id = body["jobId"]
                        logger.info("GitNexus 409 — joining existing job %s", job_id)
                    else:
                        repo_name = body.get("repoName") or body.get("repo")
                        if repo_name:
                            logger.info("GitNexus 409 — repo already indexed as %s", repo_name)
                        else:
                            raise RuntimeError(
                                "GitNexus 正在分析一个包含此路径的父项目，请等待该任务完成后再试"
                            )
                elif resp.is_error:
                    resp.raise_for_status()
                else:
                    job = resp.json()
                    job_id = job.get("jobId", "")
                    logger.info("GitNexus indexing started: %s", job_id)

                if job_id is not None:
                    # Poll for completion (30 min max)
                    for _poll_idx in range(900):
                        await asyncio.sleep(2)
                        status_resp = await client.get(f"/api/analyze/{job_id}")
                        status = status_resp.json()
                        # P0-002: status=complete but phase=retrying means worker crashed;
                        # keep polling until phase clears.
                        _raw_prog = status.get("progress")
                        _prog_dict = _raw_prog if isinstance(_raw_prog, dict) else {}
                        _phase = str(_prog_dict.get("phase") or "")
                        if status["status"] == "complete" and _phase not in ("retrying", "error"):
                            repo_name = status.get("repoName", "") or Path(tool_repo_path).name
                            if not status.get("repoName"):
                                logger.warning(
                                    "GitNexus status missing repoName; falling back to dir name: %s",
                                    repo_name,
                                )
                            await AnalysisPipeline._log_step(
                                self._task_id, 15, "GitNexus 索引完成",
                                event_type="api_done", phase="data_collection",
                                detail={"job_id": job_id, "repo": repo_name},
                            )
                            break
                        if status["status"] == "failed":
                            raise RuntimeError(
                                "GitNexus indexing failed: "
                                + status.get("error", "unknown")
                            )
                        if _poll_idx % 5 == 0:
                            elapsed_s = (_poll_idx + 1) * 2
                            await AnalysisPipeline._log_step(
                                self._task_id, 12,
                                f"GitNexus 索引中... 已等待 {elapsed_s}s",
                                event_type="api_poll", phase="data_collection",
                                detail={"job_id": job_id, "elapsed_s": elapsed_s},
                            )
                    else:
                        raise RuntimeError("GitNexus indexing timed out")

            # repo_name is set from 409 body or poll status
            repo_name = repo_name or Path(tool_repo_path).name
            self._gitnexus_index_root = repo_name  # for the 00 index-coverage table
            # Resolve the descriptor (path + expected stats + same-name ambiguity)
            # so the graph fetch can verify it pulled the RIGHT repo (Round 4 P1).
            if descriptor is None:
                descriptor = await self._gitnexus_resolve_repo(client, tool_repo_path)
                if descriptor and not self._gitnexus_index_path:
                    self._gitnexus_index_path = descriptor.get("path") or ""
            await AnalysisPipeline._log_step(
                self._task_id, 16, f"获取代码图谱（仓库：{repo_name}）...",
                event_type="api_call", phase="data_collection",
                detail={
                    "api": "GET /api/graph",
                    "repo": repo_name,
                    "path": (descriptor or {}).get("path"),
                    "ambiguous": (descriptor or {}).get("ambiguous"),
                },
            )
            self._gitnexus_data = await self._fetch_gitnexus_graph(
                client, repo_name, descriptor
            )
            nodes, edges = self._gitnexus_graph_counts(self._gitnexus_data)
            logger.info("GitNexus data collected: %d nodes, %d edges", nodes, edges)
            _stats = self._gitnexus_stats or {}
            await AnalysisPipeline._log_step(
                self._task_id, 18,
                f"代码图谱获取完成：{nodes} 节点，{edges} 关系"
                + ("（⚠️ 与目标仓库 stats 不一致）" if _stats.get("matched") is False else ""),
                event_type="api_done", phase="data_collection",
                detail={"nodes": nodes, "edges": edges, "stats": _stats},
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
                await AnalysisPipeline._log_step(
                    self._task_id, 50,
                    f"开始分析模块：{community['name']}",
                    event_type="llm_start", phase="module_analysis",
                    target={"module": community["name"]},
                    detail={"file_count": len(community.get("files", []))},
                )
                _t0 = asyncio.get_event_loop().time()
                try:
                    summary = await self._analyze_module(llm_client, community)
                    if data_quality_note:
                        summary += data_quality_note
                    _dur = int((asyncio.get_event_loop().time() - _t0) * 1000)
                    await AnalysisPipeline._log_step(
                        self._task_id, 55,
                        f"模块分析完成：{community['name']}（共 {total} 个模块）",
                        event_type="llm_done", phase="module_analysis",
                        target={"module": community["name"]},
                        detail={"duration_ms": _dur},
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
                        event_type="llm_error", phase="module_analysis",
                        target={"module": community["name"]},
                        level="error",
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

    # ------------------------------------------------------------------
    # F-WORKSPACE-GITNEXUS-ANALYSIS-TASK-REDESIGN
    # Plan-driven analysis: bounded units derived from AnalysisPlan +
    # ScopePreview rather than the GitNexus community count.
    # ------------------------------------------------------------------

    async def _phase_plan_driven_analysis(self, llm_client: BaseLLMClient) -> None:
        """Build analysis units & evidence cards from the user's plan."""
        plan = self._analysis_plan
        if plan is None:
            return

        scope = self._scope_preview
        if scope is None or not scope.resolved_objects:
            # Resolver was skipped or failed — synthesise an empty scope so
            # the rest of the pipeline still runs but produces clear warnings.
            from app.schemas.workspace_analysis import (
                ResolvedAnalysisObject,
                ScopePreview as _Sp,
            )
            scope = _Sp(
                workspace_id="",
                resolved_objects=[
                    ResolvedAnalysisObject(object_id=o.id, text=o.text)
                    for o in plan.analysis_objects
                ],
                estimated_analysis_units=len(plan.analysis_objects),
                estimated_evidence_cards=0,
                warnings=["未提供 ScopePreview，已退回到最小解析模式"],
                gitnexus_available=False,
            )
            self._scope_preview = scope

        # 1. Read graph products first, then source cards.
        cgc_cards = await self._build_evidence_cards_product_first(scope, plan)

        await self._log_step(
            self._task_id, 50,
            f"📋 已构建 {len(self._evidence_cards)} 张证据卡"
            f"（其中 CGC 图谱 {len(cgc_cards)} 张，上限 {plan.llm_limits.max_evidence_cards}）",
        )

        # 2. Group objects into analysis units.  Closely related objects
        #    (sharing files/symbols) collapse to the same unit.  Hard cap
        #    via LLMLimits.max_analysis_units (AC-P2).
        self._analysis_units = self._group_analysis_units(scope.resolved_objects, plan)
        unit_mapping = self._build_analysis_unit_mapping(
            scope.resolved_objects, plan, self._analysis_units
        )
        await self._write_analysis_unit_mapping(unit_mapping)
        await self._write_analysis_artifacts(unit_mapping)
        await self._log_step(
            self._task_id, 55,
            f"🧩 已规划 {len(self._analysis_units)} 个分析单元（GitNexus 社区数与之无关）",
        )

        # 3. Generate a small summary per unit for downstream report sections.
        sem = asyncio.Semaphore(settings.analysis_concurrency)
        out_chars = plan.llm_limits.max_output_chars_per_section

        async def summarize_one(unit: dict) -> dict:
            async with sem:
                await AnalysisPipeline._log_step(
                    self._task_id, 58,
                    f"开始分析单元：{unit['title']}",
                    event_type="llm_start", phase="plan_analysis",
                    target={"unit": unit["title"]},
                )
                _t0 = asyncio.get_event_loop().time()
                summary = await self._summarize_analysis_unit(
                    llm_client, unit, out_chars,
                )
                _dur = int((asyncio.get_event_loop().time() - _t0) * 1000)
                await AnalysisPipeline._log_step(
                    self._task_id, 60,
                    f"单元分析完成：{unit['title']}",
                    event_type="llm_done", phase="plan_analysis",
                    target={"unit": unit["title"]},
                    detail={"duration_ms": _dur},
                )
            return {
                "module_name": unit["title"],
                "summary": summary,
                "files": [c.get("file_path") for c in unit["card_dicts"] if c.get("file_path")],
                "unit_id": unit["id"],
            }

        results: list[dict] = []
        for fut in asyncio.as_completed([summarize_one(u) for u in self._analysis_units]):
            try:
                results.append(await fut)
            except Exception as exc:
                logger.exception("Analysis-unit summary failed: %s", exc)
                results.append(
                    {"module_name": "(failed)", "summary": f"（分析失败: {exc}）", "files": []}
                )

        self._module_summaries = results

    async def _build_evidence_cards_product_first(
        self,
        scope: ScopePreview,
        plan: AnalysisPlan,
    ) -> list[EvidenceCard]:
        """Build evidence in product-first order: CGC graph cards before source."""
        self._evidence_cards = []
        cgc_cards = await self._inject_cgc_evidence_cards(
            scope.resolved_objects, plan.llm_limits.max_evidence_cards
        )
        self._evidence_cards = list(cgc_cards)

        builder = EvidenceCardBuilder(
            repo_path=self._repo_path,
            limits=plan.llm_limits,
            gitnexus_repo=self._gitnexus_index_root or Path(self._repo_path).name,
        )
        source_cards = await builder.build_cards(scope.resolved_objects)
        remaining = max(0, plan.llm_limits.max_evidence_cards - len(self._evidence_cards))
        self._evidence_cards.extend(source_cards[:remaining])
        return cgc_cards

    def _group_analysis_units(
        self, resolved_objects, plan: AnalysisPlan,
    ) -> list[dict]:
        cards_by_object: dict[str, list[EvidenceCard]] = {}
        for card in self._evidence_cards:
            cards_by_object.setdefault(card.object_id, []).append(card)

        # Delegate the grouping + cap to the SHARED planner that the preview
        # also uses, so the unit count the user saw in "预览分析范围" matches the
        # count we actually execute (P0: preview ↔ execution consistency).
        object_files: list[tuple[str, list[str]]] = []
        for obj in resolved_objects:
            keys = [
                normalize_file_key(self._repo_path, card.file_path)
                for card in cards_by_object.get(obj.object_id, [])
                if card.file_path
            ]
            object_files.append((obj.object_id, keys))

        groups = plan_analysis_units(object_files, plan.llm_limits.max_analysis_units)

        text_by_id = {o.object_id: o.text for o in resolved_objects}
        units: list[dict] = []
        for idx, members in enumerate(groups):
            unit_cards: list[EvidenceCard] = []
            for mid in members:
                unit_cards.extend(cards_by_object.get(mid, []))
            titles = [text_by_id[m] for m in members if m in text_by_id]
            title = "；".join(titles[:3]) or f"分析单元 {idx + 1}"
            units.append({
                "id": f"unit_{idx + 1}",
                "title": title,
                "object_ids": members,
                "object_texts": titles,
                "cards": unit_cards,
                "card_dicts": [c.to_dict() for c in unit_cards],
            })
        return units

    def _build_analysis_unit_mapping(
        self, resolved_objects, plan: AnalysisPlan, units: list[dict],
    ) -> dict:
        cards_by_object: dict[str, list[EvidenceCard]] = {}
        for card in self._evidence_cards:
            cards_by_object.setdefault(card.object_id, []).append(card)

        unit_by_object: dict[str, dict] = {}
        for unit in units:
            for object_id in unit.get("object_ids", []):
                unit_by_object[object_id] = unit

        resolved_by_id = {obj.object_id: obj for obj in resolved_objects}
        objects: list[dict] = []
        for planned in plan.analysis_objects:
            resolved = resolved_by_id.get(planned.id)
            unit = unit_by_object.get(planned.id)
            cards = cards_by_object.get(planned.id, [])
            candidates = []
            if resolved is not None:
                candidates = [
                    *(c.model_dump() for c in resolved.candidate_files),
                    *(c.model_dump() for c in resolved.candidate_symbols),
                ]
            if cards:
                coverage_status = "direct_evidence"
            elif candidates:
                coverage_status = "resolved_without_evidence_cards"
            else:
                coverage_status = "unresolved"
            objects.append({
                "object_id": planned.id,
                "text": planned.text,
                "kind": planned.kind,
                "priority": planned.priority,
                "coverage_status": coverage_status,
                "unit_id": unit.get("id") if unit else None,
                "unit_title": unit.get("title") if unit else None,
                "candidate_count": len(candidates),
                "candidates": candidates,
                "evidence_card_ids": [card.card_id for card in cards],
                "warnings": list(resolved.warnings) if resolved is not None else ["not resolved"],
            })

        mapping_units: list[dict] = []
        for unit in units:
            unit_cards = unit.get("cards", [])
            files = sorted({
                card.file_path for card in unit_cards
                if getattr(card, "file_path", None)
            })
            mapping_units.append({
                "unit_id": unit.get("id"),
                "title": unit.get("title"),
                "object_ids": list(unit.get("object_ids", [])),
                "object_texts": list(unit.get("object_texts", [])),
                "evidence_card_ids": [card.card_id for card in unit_cards],
                "files": files,
            })

        return {
            "version": "analysis-unit-mapping-v1",
            "task_id": self._task_id,
            "plan_object_count": len(plan.analysis_objects),
            "resolved_object_count": len(resolved_objects),
            "unit_count": len(units),
            "objects": objects,
            "units": mapping_units,
        }

    async def _write_analysis_unit_mapping(self, mapping: dict) -> None:
        if not self._task_id:
            return
        out_dir = settings.outputs_path / self._task_id
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "analysis_unit_mapping.json"
        try:
            await asyncio.to_thread(
                path.write_text,
                json.dumps(mapping, ensure_ascii=False, indent=2),
                "utf-8",
            )
        except Exception as exc:
            logger.warning("Failed to write analysis unit mapping: %s", exc)

    async def _write_analysis_artifacts(self, unit_mapping: dict) -> None:
        if not self._task_id:
            return
        try:
            await write_analysis_artifacts(
                output_dir=settings.outputs_path / self._task_id,
                task_id=self._task_id,
                analysis_unit_mapping=unit_mapping,
                evidence_cards=self._evidence_cards,
                analysis_units=self._analysis_units,
            )
        except Exception as exc:
            logger.warning("Failed to write analysis artifacts: %s", exc)

    async def _inject_cgc_evidence_cards(
        self,
        resolved_objects: list,
        budget: int,
    ) -> list[EvidenceCard]:
        """Augment evidence cards with CGC graph queries.

        Queries callers/callees (Type 1), call chains (Type 2), and module
        dependency maps (Type 3) for each resolved object's gitnexus symbols.
        Silently degrades when CGC is unavailable or individual queries fail.
        """
        cgc_adapter = self._tool_adapters.get("cgc")
        if cgc_adapter is None:
            return []
        health = await cgc_adapter.health_check()
        if not health.is_healthy:
            logger.info("CGC unavailable — skipping CGC evidence injection")
            return []

        cgc = cgc_adapter._cgc
        repo_path = self._cgc_index_paths[0] if self._cgc_index_paths else self._repo_path
        cards: list[EvidenceCard] = []
        existing = len(self._evidence_cards)

        def _remaining() -> int:
            return budget - existing - len(cards)

        # Type 3 — module dependency map (once per repo, attached to first object)
        if _remaining() > 0 and resolved_objects:
            try:
                deps = await cgc.module_deps(target=repo_path, repo_path=repo_path)
                if deps:
                    dep_lines: list[str] = []
                    if isinstance(deps, dict):
                        for k, v in list(deps.items())[:8]:
                            dep_lines.append(f"{k}: {v}")
                    cards.append(EvidenceCard(
                        card_id="cgc_module_deps",
                        object_id=resolved_objects[0].object_id,
                        title="CGC 模块依赖图",
                        source="cgc",
                        confidence="medium",
                        snippet="",
                        notes=dep_lines or ["依赖数据结构不可解析"],
                        needs_verification=True,
                    ))
            except Exception as exc:
                logger.debug("CGC module_deps failed: %s", exc)

        for resolved in resolved_objects:
            if _remaining() <= 0:
                break

            # Only use symbols that gitnexus already confirmed exist in the graph
            gitnexus_syms = [
                c.symbol
                for c in resolved.candidate_symbols
                if c.symbol and c.source == "gitnexus"
            ][:_CGC_MAX_SYMBOLS_PER_OBJECT]

            # Type 1 — callers / callees per symbol
            for sym in gitnexus_syms:
                if _remaining() <= 0:
                    break
                try:
                    callers = await cgc.find_callers(sym, repo_path=repo_path)
                    if callers:
                        names = _extract_cgc_names(callers)
                        cards.append(EvidenceCard(
                            card_id=f"cgc_callers_{resolved.object_id}_{sym}",
                            object_id=resolved.object_id,
                            title=f"CGC 调用者：{sym}",
                            source="cgc",
                            confidence="medium",
                            symbol=sym,
                            snippet="",
                            notes=[f"调用者（共 {len(callers)} 个）：{', '.join(names)}"],
                            needs_verification=True,
                        ))
                except Exception as exc:
                    logger.debug("CGC find_callers(%s): %s", sym, exc)

                if _remaining() <= 0:
                    break
                try:
                    callees = await cgc.find_callees(sym, repo_path=repo_path)
                    if callees:
                        names = _extract_cgc_names(callees)
                        cards.append(EvidenceCard(
                            card_id=f"cgc_callees_{resolved.object_id}_{sym}",
                            object_id=resolved.object_id,
                            title=f"CGC 被调用：{sym}",
                            source="cgc",
                            confidence="medium",
                            symbol=sym,
                            snippet="",
                            notes=[f"被调用（共 {len(callees)} 个）：{', '.join(names)}"],
                            needs_verification=True,
                        ))
                except Exception as exc:
                    logger.debug("CGC find_callees(%s): %s", sym, exc)

            # Type 2 — call chain (exception propagation, first symbol pair)
            if len(gitnexus_syms) >= 2 and _remaining() > 0:
                entry, sink = gitnexus_syms[0], gitnexus_syms[1]
                try:
                    chain = await cgc.call_chain(entry, sink, repo_path=repo_path)
                    if chain:
                        path_data = (
                            chain.get("chain") or chain.get("path") or []
                            if isinstance(chain, dict) else []
                        )
                        chain_notes: list[str] = []
                        if isinstance(path_data, list) and path_data:
                            chain_notes.append(
                                "调用链：" + " → ".join(str(n) for n in path_data[:10])
                            )
                        cards.append(EvidenceCard(
                            card_id=f"cgc_chain_{resolved.object_id}_{entry}_{sink}",
                            object_id=resolved.object_id,
                            title=f"CGC 调用链：{entry} → {sink}",
                            source="cgc",
                            confidence="medium",
                            snippet="",
                            notes=chain_notes or ["调用链数据不可解析"],
                            needs_verification=True,
                        ))
                except Exception as exc:
                    logger.debug("CGC call_chain(%s→%s): %s", entry, sink, exc)

        return cards

    async def _summarize_analysis_unit(
        self,
        llm_client: BaseLLMClient,
        unit: dict,
        max_output_chars: int,
    ) -> str:
        """Tiny LLM call that condenses a unit's evidence into 600-1000 chars."""
        cards_md = "\n\n".join(
            card.to_markdown() for card in self._cards_for_unit_prompt(unit)
        ) or "（无证据卡）"
        prompt = (
            "你是资深代码分析与测试专家。请基于以下证据卡，为一个分析单元生成"
            "结构化摘要。输出严格控制在 800 字以内，包含：核心职责、关键调用链、"
            "异常分支与待验证项三个小节。无证据时明确写明“数据不足”而不要编造。\n\n"
            f"## 分析单元\n{unit['title']}\n\n"
            f"## 涉及的分析对象\n" + "\n".join(f"- {t}" for t in unit["object_texts"]) + "\n\n"
            f"## 证据卡\n{cards_md}\n"
        )
        messages = [{"role": "user", "content": prompt}]
        has_real_streaming = (
            type(llm_client).stream_complete is not BaseLLMClient.stream_complete
        )
        budget = min(max_output_chars * 2, settings.llm_max_output_tokens)
        try:
            if has_real_streaming:
                content = await llm_client.stream_complete_collected(
                    messages=messages, max_tokens=budget, temperature=0.3,
                )
            else:
                resp = await llm_client.complete(
                    messages=messages, max_tokens=budget, temperature=0.3,
                )
                content = resp.content
        except Exception as exc:
            return f"（分析失败: {exc}）"
        return content or "（LLM 未返回内容）"

    def _cards_for_unit_prompt(self, unit: dict, limit: int = 12) -> list[EvidenceCard]:
        """Select balanced, high-signal evidence for a unit summary prompt."""
        cards: list[EvidenceCard] = list(unit.get("cards") or [])
        if not cards:
            return []

        def score(card: EvidenceCard) -> int:
            path = (card.file_path or "").replace("\\", "/").lower()
            value = 0
            if card.source == "repo_search" and card.symbol:
                value += 120
            elif card.source == "repo_search":
                value += 80
            elif card.source == "gitnexus":
                value += 50
            elif card.source == "cgc":
                value += 180
            elif card.source == "material":
                value += 40
            if card.confidence == "high":
                value += 20
            elif card.confidence == "medium":
                value += 10
            if "/lib/log/" in path:
                value += 50
            if "/include/spdk/log.h" in path:
                value += 35
            if "/app/" in path or "/examples/" in path:
                value -= 20
            if "/test/" in path:
                value -= 10
            return value

        object_order = {oid: idx for idx, oid in enumerate(unit.get("object_ids") or [])}
        ranked = sorted(
            cards,
            key=lambda c: (
                object_order.get(c.object_id, 999),
                -score(c),
                c.file_path or c.title,
            ),
        )

        selected: list[EvidenceCard] = []
        selected_keys: set[tuple[str, str | None]] = set()
        per_object = max(1, min(3, limit // max(1, len(object_order) or 1)))
        counts: dict[str, int] = {}

        for card in ranked:
            if counts.get(card.object_id, 0) >= per_object:
                continue
            key = (card.file_path or card.title, card.symbol)
            if key in selected_keys:
                continue
            selected.append(card)
            selected_keys.add(key)
            counts[card.object_id] = counts.get(card.object_id, 0) + 1
            if len(selected) >= limit:
                return selected

        for card in sorted(cards, key=lambda c: (-score(c), c.file_path or c.title)):
            key = (card.file_path or card.title, card.symbol)
            if key in selected_keys:
                continue
            selected.append(card)
            selected_keys.add(key)
            if len(selected) >= limit:
                break
        return selected

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
        if not task_id:
            return
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
    async def _log_step(
        task_id: str,
        progress: int,
        step: str,
        *,
        event_type: str = "milestone",
        phase: str | None = None,
        target: dict | None = None,
        detail: dict | None = None,
        level: str = "info",
    ) -> None:
        """Append a timestamped structured event to outputs/{task_id}/steps.jsonl and broadcast via WS."""
        ts = datetime.now(timezone.utc).isoformat()
        entry_data: dict = {
            "timestamp": ts,
            "progress": progress,
            "step": step,
            "event_type": event_type,
            "level": level,
        }
        if phase is not None:
            entry_data["phase"] = phase
        if target is not None:
            entry_data["target"] = target
        if detail is not None:
            entry_data["detail"] = detail

        entry = json.dumps(entry_data, ensure_ascii=True)

        def _write() -> None:
            step_file = settings.outputs_path / task_id / "steps.jsonl"
            step_file.parent.mkdir(parents=True, exist_ok=True)
            # Write the whole line in one call and flush so concurrent tasks
            # never observe a partially written record.
            with step_file.open("a", encoding="utf-8") as f:
                f.write(entry + "\n")
                f.flush()

        try:
            # Serialise per task so concurrent report/section events cannot
            # interleave their appends and corrupt the JSONL.
            async with _steps_lock_for(task_id):
                await asyncio.to_thread(_write)
        except Exception as exc:
            logger.warning("Step log write failed for %s: %s", task_id, exc)

        try:
            from app.api.ws import broadcast_task_event  # lazy — avoids circular import
            await broadcast_task_event(task_id, {"type": "event", **entry_data})
        except Exception:
            pass
