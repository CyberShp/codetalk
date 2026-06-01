"""Coverage analysis service — parses coverage data and uses LLM to
recommend uncovered branches, test points, and test cases."""

from __future__ import annotations

import json
import logging
import asyncio
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from app.adapters.coverage import (
    CoverageReport,
    FileCoverage,
    FunctionHit,
    ModuleCoverage,
    detect_and_parse_xml,
    parse_internal_function_hits,
    parse_internal_function_hits_xlsx,
    parse_html_coverage,
)
from app.config import settings
from app.llm.base import BaseLLMClient
from app.llm.factory import create_llm_client_from_active

logger = logging.getLogger(__name__)
WORKSPACE_SCOPE_ENRICHMENT_TIMEOUT_SECONDS = 5.0

# Coverage gap test-design constants (coverage-test-design-v1).
COVERAGE_TEST_DESIGN_VERSION = "coverage-test-design-v1"
# Entry-oriented layered tracing: how far up the caller chain we walk to find an
# external entry (CLI / API / message handler / config / file input) before we
# fall back to a gray-box injection scheme.
ENTRY_TRACE_MAX_HOPS = 4
# Cap how many uncovered functions get the (more expensive) source-window +
# caller-chain trace so a large upload never blocks the request.
MAX_TRACED_FUNCTION_GAPS = 24
RIPGREP_TIMEOUT_SECONDS = 4.0
SOURCE_WINDOW_BEFORE = 3
SOURCE_WINDOW_AFTER = 60
_SOURCE_EXTENSION_CANDIDATES = (
    "",
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp",
    ".py", ".go", ".rs", ".java", ".js", ".jsx", ".ts", ".tsx", ".cs",
)

# A function-*definition* line: optional return type / modifiers, then the
# function name, a parenthesised parameter list, and a trailing block opener
# (``{``), Python/label ``:``, or end of line.  Plain call sites end in ``;`` and
# are rejected by the trailing-token requirement.
_FUNC_DEF_RE = re.compile(
    r"^[\w\s\*&:<>,~\[\]]*?\b([A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:\{|:|$)"
)
# Control-flow / declaration keywords that look like a call but are not a
# function definition.
_NON_FUNCTION_NAMES = {
    "if", "else", "elif", "for", "while", "switch", "case", "default", "return",
    "sizeof", "catch", "except", "do", "goto", "typedef", "struct", "union",
    "enum", "when", "guard", "with", "and", "or", "not", "in", "is",
}


def _match_def_name(line: str) -> str | None:
    """Return the defined function's name if ``line`` is a definition, else None."""
    match = _FUNC_DEF_RE.match(line)
    if not match:
        return None
    name = match.group(1)
    return None if name in _NON_FUNCTION_NAMES else name
_BRANCH_KEYWORD_RE = re.compile(
    r"\b(if|else\s+if|elif|switch|case|default|while|for|catch|except|when|guard)\b"
    r"|return\s+-[A-Za-z0-9_]+|goto\s+\w+",
    re.IGNORECASE,
)
_ERROR_CONDITION_RE = re.compile(
    r"(<\s*0|<=\s*0|==\s*NULL|!=\s*0|==\s*-1|!\s*[A-Za-z_]\w*|\bNULL\b|\berr|"
    r"\berror|\bfail|errno|timeout|exception|panic|E[A-Z0-9_]{2,})",
    re.IGNORECASE,
)

# Entry classification heuristics: path/symbol fragments -> external entry kind.
_ENTRY_SIGNATURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cli", ("cli", "/cmd", "command", "argv", "getopt", "main(", "_main", "console", "shell")),
    ("api", ("/api", "api_", "_api", "route", "router", "handler", "handle_request",
             "controller", "endpoint", "server", "rest", "grpc", "http", "rpc",
             "view", "/web", "servlet")),
    ("message", ("message", "/msg", "event", "consumer", "subscriber", "publish", "queue",
                 "kafka", "/mq", "callback", "signal", "/irq", "isr", "dispatch", "listener",
                 "notify", "on_")),
    ("config", ("config", "/conf", "settings", "option", ".ini", ".yaml", ".yml", ".toml",
                "parse_args", "load_config", "env")),
    ("file", ("readfile", "read_file", "loadfile", "load_file", "fread", "fopen", "open(",
              "ingest", "import", "/io", "input", "stdin", "scan")),
)

COVERAGE_ANALYSIS_PROMPT = """\
你是一名资深测试工程师和代码分析专家。请根据以下代码覆盖率数据，分析未覆盖的代码分支和函数，\
并给出精准的测试建议。

## 模块信息
- 模块路径: {module_path}
- 行覆盖率: {line_rate:.1%}
- 分支覆盖率: {branch_rate:.1%}
- 函数覆盖率: {function_rate:.1%}

## 未覆盖的函数
{uncovered_functions}

## 未覆盖的代码行（示例）
{uncovered_lines}

## 未覆盖的分支
{uncovered_branches}

## 文件覆盖明细
{file_details}

## 输出要求
请用中文输出，包含以下部分（使用 Markdown 格式）：

### 1. 覆盖率概况分析
简要说明当前模块覆盖率状况，指出关键风险区域。

### 2. 未覆盖代码分支分析
列出最重要的未覆盖分支，说明这些分支可能涉及的业务场景。

### 3. 推荐测试点
按优先级列出需要补充的测试点，每个测试点包含：
- **测试目标**: 要验证什么
- **前置条件**: 测试所需环境和数据
- **关键断言**: 期望的行为

### 4. 推荐测试用例
给出 3-5 个具体的测试用例（伪代码或测试框架代码），覆盖最关键的未测试路径。

### 5. 优先级建议
根据代码复杂度和业务影响排列修复优先级。
"""


async def _analyze_module(
    llm: BaseLLMClient,
    module: ModuleCoverage,
) -> dict:
    """Use LLM to analyze a single module's coverage gaps."""
    file_details_lines = []
    for f in module.files[:20]:
        file_details_lines.append(
            f"- {f.filename}: 行覆盖 {f.line_rate:.1%}, "
            f"分支覆盖 {f.branch_rate:.1%}, "
            f"未覆盖行 {len(f.uncovered_lines)} 个"
        )

    prompt = COVERAGE_ANALYSIS_PROMPT.format(
        module_path=module.module_path,
        line_rate=module.line_rate,
        branch_rate=module.branch_rate,
        function_rate=module.function_rate,
        uncovered_functions="\n".join(
            f"- {fn}" for fn in module.uncovered_functions[:30]
        ) or "无",
        uncovered_lines="\n".join(module.uncovered_lines[:30]) or "无",
        uncovered_branches="\n".join(
            f"- {b}" for b in module.uncovered_branches[:30]
        ) or "无",
        file_details="\n".join(file_details_lines) or "无文件详情",
    )

    resp = await llm.complete(prompt, max_tokens=min(4096, settings.llm_max_output_tokens))

    return {
        "module_path": module.module_path,
        "line_rate": module.line_rate,
        "branch_rate": module.branch_rate,
        "function_rate": module.function_rate,
        "analysis": resp.text,
        "uncovered_function_count": len(module.uncovered_functions),
        "uncovered_branch_count": len(module.uncovered_branches),
    }


def _coverage_text(content: str | bytes) -> str:
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    return content


def _coverage_bytes(content: str | bytes) -> bytes:
    if isinstance(content, bytes):
        return content
    return content.encode("utf-8")


class CoverageAnalyzer:
    """Orchestrates coverage parsing and AI analysis."""

    async def parse_and_store(
        self,
        analysis_id: str,
        files: list[tuple[str, str | bytes]],
        name: str = "",
        workspace_id: str | None = None,
        repo_path: str | None = None,
    ) -> CoverageReport:
        """Parse uploaded coverage files and store structured data."""
        merged_modules: list[ModuleCoverage] = []
        source_format = "mixed"

        for filename, content in files:
            lower = filename.lower()
            if lower.endswith(".xml"):
                try:
                    report = detect_and_parse_xml(_coverage_text(content))
                except Exception as exc:
                    raise ValueError(f"文件 {filename} XML 格式无效: {exc}") from exc
            elif lower.endswith((".html", ".htm")):
                report = parse_html_coverage(_coverage_text(content))
            elif lower.endswith(".xlsx"):
                try:
                    report = parse_internal_function_hits_xlsx(_coverage_bytes(content))
                except Exception as exc:
                    logger.warning("Skipping invalid Excel coverage file %s: %s", filename, exc)
                    continue
            elif lower.endswith(".xls"):
                try:
                    report = parse_internal_function_hits(_coverage_text(content))
                except Exception as exc:
                    logger.warning("Skipping invalid legacy Excel coverage file %s: %s", filename, exc)
                    continue
            elif lower.endswith((".csv", ".tsv", ".txt")):
                try:
                    report = parse_internal_function_hits(_coverage_text(content))
                except Exception as exc:
                    logger.warning("Skipping invalid internal coverage file %s: %s", filename, exc)
                    continue
            else:
                logger.warning("Skipping unsupported file: %s", filename)
                continue

            merged_modules.extend(report.modules)
            source_format = report.source_format

        if not merged_modules:
            raise ValueError("未能从上传文件中解析到任何覆盖率数据")

        total_line = sum(m.line_rate for m in merged_modules) / len(merged_modules)
        total_branch = sum(m.branch_rate for m in merged_modules) / len(merged_modules)
        total_func = sum(m.function_rate for m in merged_modules) / len(merged_modules)

        merged_report = CoverageReport(
            overall_line_rate=total_line,
            overall_branch_rate=total_branch,
            overall_function_rate=total_func,
            modules=merged_modules,
            source_format=source_format,
        )

        now = datetime.now(timezone.utc).isoformat()
        modules_json = json.dumps(
            [_module_to_dict(m) for m in merged_modules],
            ensure_ascii=False,
        )

        async with aiosqlite.connect(settings.sqlite_db) as db:
            await db.execute(
                """INSERT INTO coverage_analyses
                   (id, name, source_type, status, overall_line_rate,
                    overall_branch_rate, overall_function_rate,
                    module_count, modules_json, source_format, workspace_id,
                    repo_path, created_at, updated_at)
                   VALUES (?, ?, 'upload', 'parsed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    analysis_id,
                    name or f"覆盖率分析 {now[:10]}",
                    total_line,
                    total_branch,
                    total_func,
                    len(merged_modules),
                    modules_json,
                    source_format,
                    workspace_id,
                    repo_path,
                    now,
                    now,
                ),
            )
            await db.commit()

        logger.info(
            "Coverage parsed: %d modules, line=%.1f%%, branch=%.1f%%",
            len(merged_modules),
            total_line * 100,
            total_branch * 100,
        )

        return merged_report

    async def run_analysis(self, analysis_id: str) -> list[dict]:
        """Run AI analysis on a parsed coverage report."""
        # Atomic status transition: only one concurrent caller will see rowcount==1.
        # This eliminates the TOCTOU window between the status-check and the UPDATE.
        async with aiosqlite.connect(settings.sqlite_db) as db:
            now = datetime.now(timezone.utc).isoformat()
            cursor = await db.execute(
                "UPDATE coverage_analyses SET status = 'analyzing', updated_at = ? "
                "WHERE id = ? AND status IN ('parsed', 'analyzed')",
                (now, analysis_id),
            )
            await db.commit()
            if cursor.rowcount == 0:
                raise ValueError("分析已在进行中或状态不允许，请勿重复触发")

        # Fetch modules data after the atomic status transition is committed.
        async with aiosqlite.connect(settings.sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            row = await db.execute_fetchall(
                "SELECT modules_json, source_format, workspace_id, repo_path FROM coverage_analyses WHERE id = ?",
                (analysis_id,),
            )
            if not row:  # pragma: no cover
                raise ValueError(f"覆盖率分析 {analysis_id} 不存在")
            record = dict(row[0])
            modules_json = record["modules_json"]

        modules_data: list[dict] = json.loads(modules_json)
        modules = [_dict_to_module(d) for d in modules_data]

        if record.get("source_format") == "internal_function_hits":
            results = await _build_black_box_function_recommendations(
                modules,
                workspace_id=record.get("workspace_id"),
                repo_path=record.get("repo_path"),
            )
            now = datetime.now(timezone.utc).isoformat()
            async with aiosqlite.connect(settings.sqlite_db) as db:
                await db.execute(
                    """UPDATE coverage_analyses
                       SET status = 'analyzed',
                           analysis_results_json = ?,
                           updated_at = ?
                       WHERE id = ?""",
                    (json.dumps(results, ensure_ascii=False), now, analysis_id),
                )
                await db.commit()
            return results

        low_coverage = [
            m for m in modules
            if m.line_rate < 0.8 or m.branch_rate < 0.6 or m.function_rate < 0.8
        ]
        targets = low_coverage or modules[:5]

        try:
            llm = await create_llm_client_from_active()
        except ValueError as exc:
            logger.warning("Coverage analysis skipped — no LLM configured: %s", exc)
            now = datetime.now(timezone.utc).isoformat()
            async with aiosqlite.connect(settings.sqlite_db) as db:
                await db.execute(
                    "UPDATE coverage_analyses SET status = 'parsed', updated_at = ? WHERE id = ?",
                    (now, analysis_id),
                )
                await db.commit()
            return []

        results: list[dict] = []

        for module in targets:
            try:
                result = await _analyze_module(llm, module)
                results.append(result)
            except Exception:
                logger.exception("Failed to analyze module %s", module.module_path)
                results.append({
                    "module_path": module.module_path,
                    "error": "AI 分析失败，请稍后重试",
                })

        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(settings.sqlite_db) as db:
            await db.execute(
                """UPDATE coverage_analyses
                   SET status = 'analyzed',
                       analysis_results_json = ?,
                       updated_at = ?
                   WHERE id = ?""",
                (json.dumps(results, ensure_ascii=False), now, analysis_id),
            )
            await db.commit()

        return results


async def _build_black_box_function_recommendations(
    modules: list[ModuleCoverage],
    *,
    workspace_id: str | None,
    repo_path: str | None,
) -> list[dict]:
    """Build entry-oriented test recommendations for uncovered function-hit rows.

    Each result is a *superset* of the legacy black-box recommendation dict: it
    keeps every legacy key (``scenario``, ``input_conditions``, ``confidence``,
    ``evidence`` …) and adds the coverage-test-design fields (``source_window``,
    ``trigger_branches``, ``entry_paths``, ``black_box_cases``, ``gray_box`` …).
    """
    uncovered = _collect_uncovered_function_hits(modules)
    scope_by_function = await _resolve_workspace_scope_for_hits(
        uncovered, workspace_id=workspace_id, repo_path=repo_path
    )
    cgc_by_function = await _resolve_cgc_context_for_hits(uncovered, repo_path=repo_path)

    repo_root = _existing_repo_root(repo_path)
    rg_available = shutil.which("rg") is not None

    results: list[dict] = []
    for idx, (module, hit) in enumerate(uncovered[:50]):
        scope = scope_by_function.get(_hit_key(hit), {})
        cgc_context = cgc_by_function.get(_hit_key(hit), {})
        # Only the first N gaps get the expensive source-window + caller-chain
        # trace; the long tail still gets coverage + heuristic guidance.
        trace = idx < MAX_TRACED_FUNCTION_GAPS
        result = await asyncio.to_thread(
            _design_function_gap,
            module,
            hit,
            workspace_id=workspace_id,
            repo_path=repo_path,
            repo_root=repo_root,
            rg_available=rg_available,
            scope=scope,
            cgc_context=cgc_context,
            trace=trace,
        )
        results.append(result)

    return sorted(
        results,
        key=lambda r: {"high": 0, "medium": 1, "low": 2}[r["risk_level"]],
    )


def _design_function_gap(
    module: ModuleCoverage,
    hit: FunctionHit,
    *,
    workspace_id: str | None,
    repo_path: str | None,
    repo_root: Path | None,
    rg_available: bool,
    scope: dict,
    cgc_context: dict,
    trace: bool,
) -> dict:
    """Build one uncovered-function gap with source-backed trigger analysis.

    Runs entirely synchronously (file reads + ripgrep); callers offload it to a
    thread.  Never raises — every external lookup degrades to an evidence gap.
    """
    risk_level = _risk_level_for_hit(hit)
    cgc_callers = cgc_context.get("callers") if isinstance(cgc_context, dict) else None

    source_window = _read_source_window(repo_root, hit) if trace else None
    self_branches = _branches_from_window(source_window, source="self")

    entry_paths: list[dict] = []
    trigger_branches: list[dict] = list(self_branches)
    if trace:
        entry_paths, caller_branches = _trace_entry_paths(
            repo_root,
            hit.function_name,
            rg_available=rg_available,
            cgc_callers=cgc_callers,
        )
        trigger_branches = _dedupe_branches([*caller_branches, *self_branches])

    has_black_box_entry = bool(entry_paths)
    gray_box_required = not has_black_box_entry
    black_box_cases = _build_black_box_cases(hit, entry_paths, trigger_branches)
    gray_box = _build_gray_box_scheme(
        hit,
        repo_root=repo_root,
        cgc_callers=cgc_callers,
        trigger_branches=trigger_branches,
        required=gray_box_required,
    )
    tool_status = _gap_tool_status(
        repo_root=repo_root,
        rg_available=rg_available,
        source_window=source_window,
        cgc_context=cgc_context,
        scope=scope,
    )
    evidence_gaps = _function_evidence_gaps(
        workspace_bound=repo_root is not None and bool(workspace_id),
        source_window=source_window,
        entry_paths=entry_paths,
        trigger_branches=trigger_branches,
        tool_status=tool_status,
    )

    result = {
        "kind": "function",
        "module_path": module.module_path,
        "line_rate": module.line_rate,
        "branch_rate": module.branch_rate,
        "function_rate": module.function_rate,
        "feature_name": hit.feature_name,
        "function_name": hit.function_name,
        "file_path": hit.file_path,
        "line_start": hit.line_start,
        "line_end": hit.line_end,
        "hit_count": hit.hit_count,
        "risk_level": risk_level,
        "category": "black_box_function_gap",
        "scenario": _scenario_for_hit(hit),
        "input_conditions": _input_conditions_for_hit(hit),
        "expected_behavior": _expected_behavior_for_hit(hit),
        "observable_signals": _observable_signals_for_hit(hit),
        # coverage-test-design-v1 enrichment
        "source_window": source_window,
        "trigger_branches": trigger_branches,
        "entry_paths": entry_paths,
        "black_box_cases": black_box_cases,
        "gray_box": gray_box,
        "gray_box_required": gray_box_required,
        "evidence_gaps": evidence_gaps,
        "tool_status": tool_status,
        "confidence": _confidence_for_gap(scope, cgc_context, source_window, entry_paths),
        "evidence": {
            "coverage": {
                "workspace_id": workspace_id,
                "repo_path": repo_path,
                "module_path": module.module_path,
                "feature_name": hit.feature_name,
                "module_name": hit.module_name,
                "file_path": hit.file_path,
                "function_name": hit.function_name,
                "line_start": hit.line_start,
                "line_end": hit.line_end,
                "triggered": hit.triggered,
                "hit_count": hit.hit_count,
            },
            "gitnexus_scope": scope,
            "cgc": cgc_context,
        },
    }
    result["analysis"] = _recommendation_markdown(result)
    return result


def _collect_uncovered_function_hits(
    modules: list[ModuleCoverage],
) -> list[tuple[ModuleCoverage, FunctionHit]]:
    uncovered: list[tuple[ModuleCoverage, FunctionHit]] = []
    for module in modules:
        hits = module.function_hits
        if not hits:
            hits = [hit for f in module.files for hit in f.function_hits]
        for hit in hits:
            if not (hit.triggered or hit.hit_count > 0):
                uncovered.append((module, hit))
    return uncovered


async def _resolve_workspace_scope_for_hits(
    uncovered: list[tuple[ModuleCoverage, FunctionHit]],
    *,
    workspace_id: str | None,
    repo_path: str | None,
) -> dict[str, dict]:
    if not workspace_id or not repo_path or not Path(repo_path).exists() or not uncovered:
        return {}

    try:
        from app.schemas.workspace_analysis import AnalysisObject, AnalysisPlan, LLMLimits
        from app.services.workspace_scope_resolver import WorkspaceScopeResolver

        objects = [
            AnalysisObject(
                id=f"cov_{idx}",
                text=hit.function_name,
                kind="function",
                priority="high" if _risk_level_for_hit(hit) == "high" else "medium",
                path_hints=[hit.file_path],
            )
            for idx, (_, hit) in enumerate(uncovered[:24])
        ]
        plan = AnalysisPlan(
            analysis_objects=objects,
            llm_limits=LLMLimits(
                max_files_per_object=4,
                max_functions_per_object=6,
                max_communities_per_object=4,
                max_analysis_units=24,
            ),
        )
        preview = await asyncio.wait_for(
            WorkspaceScopeResolver().resolve(
                ws_id=workspace_id,
                repo_path=repo_path,
                plan=plan,
            ),
            timeout=WORKSPACE_SCOPE_ENRICHMENT_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.info("Coverage workspace scope enrichment unavailable: %s", exc)
        return {}

    by_key: dict[str, dict] = {}
    for idx, resolved in enumerate(preview.resolved_objects):
        if idx >= len(uncovered):
            continue
        _, hit = uncovered[idx]
        by_key[_hit_key(hit)] = {
            "gitnexus_available": preview.gitnexus_available,
            "candidate_files": [c.model_dump() for c in resolved.candidate_files[:6]],
            "candidate_symbols": [c.model_dump() for c in resolved.candidate_symbols[:6]],
            "related_communities": resolved.related_communities[:4],
            "warnings": [*preview.warnings, *resolved.warnings],
        }
    return by_key


async def _resolve_cgc_context_for_hits(
    uncovered: list[tuple[ModuleCoverage, FunctionHit]],
    *,
    repo_path: str | None,
) -> dict[str, dict]:
    if not repo_path or not Path(repo_path).exists() or not uncovered:
        return {}
    try:
        from app.adapters.cgc import CGCClient

        cgc = CGCClient(timeout=2.0)
        if not await cgc.is_healthy():
            await cgc.close()
            return {}

        contexts: dict[str, dict] = {}
        for _, hit in uncovered[:12]:
            callers = await cgc.find_callers(hit.function_name, repo_path=repo_path)
            callees = await cgc.find_callees(hit.function_name, repo_path=repo_path)
            contexts[_hit_key(hit)] = {
                "available": True,
                "callers": _summarize_cgc_items(callers),
                "callees": _summarize_cgc_items(callees),
            }
        await cgc.close()
        return contexts
    except Exception as exc:
        logger.info("Coverage CGC enrichment unavailable: %s", exc)
        return {}


def _summarize_cgc_items(items: object) -> list[dict]:
    if not isinstance(items, list):
        return []
    summary: list[dict] = []
    for item in items[:8]:
        if isinstance(item, dict):
            summary.append({
                "name": item.get("name") or item.get("function") or item.get("caller"),
                "location": item.get("location") or item.get("file") or item.get("path"),
            })
        else:
            summary.append({"name": str(item), "location": None})
    return summary


def _hit_key(hit: FunctionHit) -> str:
    return f"{hit.file_path}:{hit.function_name}:{hit.line_start or ''}"


def _risk_level_for_hit(hit: FunctionHit) -> str:
    text = f"{hit.function_name} {hit.file_path}".lower()
    high_terms = (
        "error", "fail", "recover", "rollback", "cleanup", "free", "close",
        "timeout", "retry", "auth", "permission", "security", "panic",
    )
    medium_terms = (
        "init", "start", "stop", "config", "parse", "validate", "state",
        "open", "read", "write", "connect", "disconnect",
    )
    if any(term in text for term in high_terms):
        return "high"
    if any(term in text for term in medium_terms):
        return "medium"
    return "low"


def _scenario_for_hit(hit: FunctionHit) -> str:
    name = hit.function_name.replace("_", " ")
    return f"Exercise the external workflow that should reach the behavior represented by `{name}`."


def _input_conditions_for_hit(hit: FunctionHit) -> str:
    text = hit.function_name.lower()
    if any(term in text for term in ("error", "fail", "recover", "rollback")):
        return "Use an externally visible failure condition: invalid input, unavailable dependency, timeout, or retry exhaustion."
    if any(term in text for term in ("cleanup", "free", "close", "stop")):
        return "Run the workflow through normal completion and forced interruption so resource release can be observed."
    if any(term in text for term in ("parse", "validate", "config")):
        return "Prepare valid, boundary, malformed, and missing configuration or request data."
    return "Drive the nearest public API, CLI command, UI action, message, or file input that owns this behavior."


def _expected_behavior_for_hit(hit: FunctionHit) -> str:
    return (
        "The user-visible workflow completes with the documented result or a controlled "
        "error response; no silent success, crash, hang, leaked resource, or inconsistent "
        "state is acceptable."
    )


def _observable_signals_for_hit(hit: FunctionHit) -> list[str]:
    signals = ["return value / response code", "user-visible status", "logs"]
    text = hit.function_name.lower()
    if any(term in text for term in ("cleanup", "free", "close")):
        signals.append("resource count returns to baseline")
    if any(term in text for term in ("state", "start", "stop", "recover")):
        signals.append("state transition is externally observable")
    return signals


def _confidence_for_context(scope: dict, cgc_context: dict) -> str:
    if cgc_context.get("callers") or scope.get("candidate_symbols"):
        return "high"
    if scope.get("candidate_files") or scope.get("related_communities"):
        return "medium"
    return "low"


def _recommendation_markdown(result: dict) -> str:
    signals = ", ".join(result.get("observable_signals") or [])
    lines = [
        "### 黑盒测试建议",
        f"- 测试目标: {result['scenario']}",
        f"- 输入/前置条件: {result['input_conditions']}",
        f"- 预期行为: {result['expected_behavior']}",
        f"- 可观测信号: {signals}",
        f"- 风险等级: {result['risk_level']}",
        f"- 证据: {result['file_path']}:{result.get('line_start') or '?'} "
        f"hit_count={result['hit_count']}",
    ]
    triggers = result.get("trigger_branches") or []
    if triggers:
        lines.append("- 触发分支:")
        for branch in triggers[:6]:
            origin = branch.get("source") or "self"
            lines.append(
                f"  - [{origin}] {branch.get('condition')}"
                + (f"  ({branch.get('file')}:{branch.get('line_number')})"
                   if branch.get("file") else "")
            )
    entries = result.get("entry_paths") or []
    if entries:
        lines.append("- 外部入口路径:")
        for entry in entries[:4]:
            chain = " -> ".join(entry.get("chain") or [])
            lines.append(f"  - [{entry.get('entry_kind')}] {chain}")
    elif result.get("gray_box_required"):
        gray = result.get("gray_box") or {}
        lines.append(
            "- 未找到 4 跳内的外部入口，需灰盒方案: " + (gray.get("scheme") or "桩件/故障注入")
        )
    gaps = result.get("evidence_gaps") or []
    if gaps:
        lines.append("- 证据缺口: " + "；".join(gaps[:4]))
    return "\n".join(lines) + "\n"


# ── Coverage gap test-design engine ────────────────────────────────────
#
# Entry-oriented layered tracing for uncovered functions:
#   1. read the function's source window,
#   2. extract its own + its callers' guarding branch conditions,
#   3. walk up the caller chain (<= ENTRY_TRACE_MAX_HOPS) until an external
#      entry (CLI / API / message / config / file input) is reached,
#   4. if no external entry is reachable, emit a gray-box injection scheme and
#      mark ``gray_box_required`` instead of fabricating a black-box path.
#
# Joern is the preferred backend but is not wired up yet, so the engine runs on
# CGC callers when available and degrades to ripgrep text search otherwise.  All
# degraded results are labelled in ``tool_status`` / ``warnings``.


def _existing_repo_root(repo_path: str | None) -> Path | None:
    if not repo_path:
        return None
    try:
        root = Path(repo_path)
        return root if root.exists() and root.is_dir() else None
    except OSError:
        return None


def _resolve_source_file(repo_root: Path | None, file_path: str) -> Path | None:
    """Resolve a coverage code-path to a real file inside the repo.

    The intranet code-path column may be a real relative path, a path without an
    extension, or just a module stem.  We try direct joins (with a few common
    source extensions) first, then a bounded basename search.
    """
    if repo_root is None or not file_path:
        return None
    rel = file_path.replace("\\", "/").lstrip("/")
    for ext in _SOURCE_EXTENSION_CANDIDATES:
        candidate = repo_root / (rel + ext)
        try:
            if candidate.is_file() and _is_within(repo_root, candidate):
                return candidate
        except OSError:
            continue
    basename = Path(rel).name
    if not basename:
        return None
    # Bounded basename search across the repo (ignores VCS / build dirs).
    try:
        for ext in _SOURCE_EXTENSION_CANDIDATES:
            target = basename + ext
            matches = 0
            for candidate in repo_root.rglob(target):
                matches += 1
                if candidate.is_file() and _is_within(repo_root, candidate):
                    return candidate
                if matches >= 50:
                    break
    except OSError:
        return None
    return None


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _read_source_window(repo_root: Path | None, hit: FunctionHit) -> dict | None:
    """Return the source window around an uncovered function, or None."""
    source_file = _resolve_source_file(repo_root, hit.file_path)
    if source_file is None:
        return None
    try:
        text = source_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = text.splitlines()
    total = len(lines)
    if not total:
        return None

    start_line = hit.line_start
    if not start_line or start_line < 1 or start_line > total:
        start_line = _find_definition_line(lines, hit.function_name)
    if not start_line:
        # Function not locatable in this file — treat as no usable window.
        return None

    end_anchor = hit.line_end if (hit.line_end and hit.line_end >= start_line) else start_line
    window_start = max(1, start_line - SOURCE_WINDOW_BEFORE)
    window_end = min(total, end_anchor + SOURCE_WINDOW_AFTER)
    window_lines = [
        {"n": window_start + offset, "text": lines[window_start - 1 + offset]}
        for offset in range(window_end - window_start + 1)
    ]
    rel_path = _relative_path(repo_root, source_file)
    return {
        "available": True,
        "path": rel_path,
        "definition_line": start_line,
        "start": window_start,
        "end": window_end,
        "lines": window_lines,
        "text": "\n".join(item["text"] for item in window_lines),
        "tool": "filesystem",
    }


def _find_definition_line(lines: list[str], function_name: str) -> int | None:
    if not function_name:
        return None
    name_re = re.compile(rf"\b{re.escape(function_name)}\s*\(")
    for idx, line in enumerate(lines):
        if name_re.search(line) and _match_def_name(line) == function_name:
            return idx + 1
    # Fallback: first textual occurrence of "name(".
    for idx, line in enumerate(lines):
        if name_re.search(line):
            return idx + 1
    return None


def _relative_path(repo_root: Path | None, path: Path) -> str:
    if repo_root is None:
        return str(path)
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except (ValueError, OSError):
        return path.as_posix()


def _extract_branch_condition(line: str) -> str:
    clean = " ".join(line.split())
    for keyword in ("if", "else if", "elif", "switch", "while", "for", "catch",
                    "except", "when", "guard"):
        match = re.search(rf"\b{keyword}\b\s*\(([^)]*)\)", clean, re.IGNORECASE)
        if match:
            return f"{keyword} ({match.group(1).strip()})"
    case_match = re.search(r"\b(case\s+[^:]+:|default\s*:)", clean, re.IGNORECASE)
    if case_match:
        return case_match.group(1).strip()
    goto_match = re.search(r"\b(return\s+-[A-Za-z0-9_]+|goto\s+\w+)", clean, re.IGNORECASE)
    if goto_match:
        return goto_match.group(1).strip()
    return clean[:160]


def _branch_category(line: str) -> str:
    lowered = line.lower()
    if "return -" in lowered or _ERROR_CONDITION_RE.search(line):
        return "error_or_negative_return"
    if "switch" in lowered or "case " in lowered:
        return "dispatch"
    if "while" in lowered or "for " in lowered or "for(" in lowered:
        return "loop"
    return "condition"


def _branches_from_window(window: dict | None, *, source: str) -> list[dict]:
    if not window:
        return []
    branches: list[dict] = []
    for item in window.get("lines") or []:
        text = item.get("text") or ""
        if not _BRANCH_KEYWORD_RE.search(text):
            continue
        branches.append({
            "condition": _extract_branch_condition(text),
            "line": text.strip()[:200],
            "line_number": item.get("n"),
            "category": _branch_category(text),
            "source": source,
            "file": window.get("path"),
            "is_error_path": bool(_ERROR_CONDITION_RE.search(text)),
        })
        if len(branches) >= 12:
            break
    return branches


def _dedupe_branches(branches: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for branch in branches:
        key = (branch.get("file"), branch.get("line_number"), branch.get("condition"))
        if key in seen:
            continue
        seen.add(key)
        out.append(branch)
    return out[:16]


def _ripgrep_call_sites(repo_root: Path, function_name: str) -> list[dict]:
    """Find textual call sites of ``function_name`` via ripgrep (degraded mode)."""
    if not function_name or shutil.which("rg") is None:
        return []
    pattern = rf"\b{re.escape(function_name)}\s*\("
    try:
        proc = subprocess.run(
            ["rg", "--no-heading", "--line-number", "--color", "never",
             "--max-count", "40", "-e", pattern, str(repo_root)],
            capture_output=True,
            text=True,
            timeout=RIPGREP_TIMEOUT_SECONDS,
        )
    except (subprocess.SubprocessError, OSError):
        return []
    sites: list[dict] = []
    for raw in proc.stdout.splitlines():
        # Windows paths include a drive separator (for example ``E:\``), so
        # split from the right to preserve the full path before line/text.
        parts = raw.rsplit(":", 2)
        if len(parts) < 3:
            continue
        file_str, line_str, text = parts
        try:
            line_number = int(line_str)
        except ValueError:
            continue
        stripped = text.strip()
        # Skip the definition itself and obvious comment lines.
        if _match_def_name(text) == function_name:
            continue
        if stripped.startswith(("//", "#", "*", "/*")):
            continue
        sites.append({
            "file": _relative_path(repo_root, Path(file_str)),
            "abs_file": file_str,
            "line_number": line_number,
            "text": stripped[:200],
        })
        if len(sites) >= 40:
            break
    return sites


def _caller_context(abs_file: str, line_number: int) -> tuple[str | None, dict | None]:
    """Return (enclosing_function, guarding_branch) for a call site.

    ``guarding_branch`` is the nearest ``if/switch/case/while/for`` condition in
    the few lines above the call — i.e. *what condition triggers* the call.
    """
    try:
        lines = Path(abs_file).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None, None
    upper = min(line_number, len(lines)) - 1

    enclosing: str | None = None
    for idx in range(upper, -1, -1):
        name = _match_def_name(lines[idx])
        if name:
            enclosing = name
            break

    guard: dict | None = None
    low = max(0, line_number - 8)
    for idx in range(upper, low - 1, -1):
        text = lines[idx]
        if _match_def_name(text) is not None:
            break  # reached the enclosing definition without a guard
        if _BRANCH_KEYWORD_RE.search(text):
            guard = {
                "condition": _extract_branch_condition(text),
                "line": text.strip()[:200],
                "line_number": idx + 1,
                "category": _branch_category(text),
                "is_error_path": bool(_ERROR_CONDITION_RE.search(text)),
            }
            break
    return enclosing, guard


def _classify_entry(file_path: str, enclosing_fn: str | None, line_text: str) -> str | None:
    """Classify a call site as an external entry kind, or None for internal."""
    blob = " ".join(filter(None, [file_path or "", enclosing_fn or "", line_text or ""])).lower()
    if enclosing_fn and enclosing_fn.lower() in {"main", "_main", "wmain"}:
        return "cli"
    for kind, needles in _ENTRY_SIGNATURES:
        if any(needle in blob for needle in needles):
            return kind
    return None


def _trace_entry_paths(
    repo_root: Path | None,
    function_name: str,
    *,
    rg_available: bool,
    cgc_callers: object,
) -> tuple[list[dict], list[dict]]:
    """Walk up the caller chain to external entries (entry-oriented tracing).

    Returns ``(entry_paths, caller_branches)``.  ``caller_branches`` are the
    branch conditions guarding the direct call sites of ``function_name``.
    """
    if repo_root is None or not function_name:
        return [], []

    caller_branches: list[dict] = []
    entry_paths: list[dict] = []
    visited: set[str] = {function_name}
    # BFS frontier of (chain_so_far, current_symbol).
    frontier: list[tuple[list[str], str]] = [([function_name], function_name)]

    direct = True
    for _hop in range(ENTRY_TRACE_MAX_HOPS):
        next_frontier: list[tuple[list[str], str]] = []
        for chain, symbol in frontier:
            sites = _ripgrep_call_sites(repo_root, symbol) if rg_available else []
            for site in sites[:6]:
                enclosing, guard = _caller_context(site["abs_file"], site["line_number"])
                if direct:
                    branch = dict(guard) if guard else {
                        "condition": _extract_branch_condition(site["text"]),
                        "line": site["text"],
                        "line_number": site["line_number"],
                        "category": _branch_category(site["text"]),
                        "is_error_path": bool(_ERROR_CONDITION_RE.search(site["text"])),
                    }
                    branch.update({"source": "caller", "file": site["file"]})
                    caller_branches.append(branch)
                entry_kind = _classify_entry(site["file"], enclosing, site["text"])
                caller_chain = ([enclosing, *chain] if enclosing else chain)
                if entry_kind:
                    entry_paths.append({
                        "entry_kind": entry_kind,
                        "entry_symbol": enclosing,
                        "entry_file": site["file"],
                        "call_line": site["line_number"],
                        "chain": caller_chain,
                        "depth": len(caller_chain) - 1,
                        "evidence": f"{site['file']}:{site['line_number']} {site['text']}",
                        "tool": "ripgrep" if rg_available else "cgc",
                    })
                    continue
                if enclosing and enclosing not in visited:
                    visited.add(enclosing)
                    next_frontier.append((caller_chain, enclosing))
        direct = False
        if entry_paths or not next_frontier:
            break
        frontier = next_frontier

    # De-duplicate entry paths by (kind, symbol, file).
    seen: set[tuple] = set()
    unique_entries: list[dict] = []
    for entry in entry_paths:
        key = (entry["entry_kind"], entry.get("entry_symbol"), entry.get("entry_file"))
        if key in seen:
            continue
        seen.add(key)
        unique_entries.append(entry)
    return unique_entries[:6], _dedupe_branches(caller_branches)


def _build_black_box_cases(
    hit: FunctionHit,
    entry_paths: list[dict],
    trigger_branches: list[dict],
) -> list[dict]:
    """Construct concrete black-box cases from entries + branch conditions."""
    cases: list[dict] = []
    base_inputs = _input_conditions_for_hit(hit)
    expected = _expected_behavior_for_hit(hit)
    signals = _observable_signals_for_hit(hit)

    for entry in entry_paths[:3]:
        chain = " -> ".join(entry.get("chain") or [])
        cases.append({
            "title": f"经 {entry.get('entry_kind')} 入口触达 {hit.function_name}",
            "entry_kind": entry.get("entry_kind"),
            "preconditions": f"通过外部入口 {entry.get('entry_symbol') or entry.get('entry_kind')} 驱动调用链：{chain}",
            "inputs": base_inputs,
            "steps": [
                f"从 {entry.get('entry_kind')} 入口 ({entry.get('entry_file')}) 发起请求/命令/消息",
                f"构造输入使调用沿 {chain} 到达 {hit.function_name}",
            ],
            "expected": expected,
            "observable_signals": signals,
            "evidence": entry.get("evidence"),
        })

    for branch in trigger_branches[:3]:
        if not branch.get("is_error_path") and branch.get("source") != "caller":
            continue
        cases.append({
            "title": f"覆盖触发分支 {branch.get('condition')}",
            "entry_kind": entry_paths[0]["entry_kind"] if entry_paths else "unknown",
            "preconditions": f"使外部输入/状态满足分支条件：{branch.get('condition')}",
            "inputs": base_inputs,
            "steps": [
                f"构造数据令 {branch.get('file')}:{branch.get('line_number')} 处条件 "
                f"`{branch.get('condition')}` 成立",
                f"驱动到 {hit.function_name} 并观察行为",
            ],
            "expected": expected,
            "observable_signals": signals,
            "evidence": (f"{branch.get('file')}:{branch.get('line_number')}"
                         if branch.get("file") else None),
        })

    if not cases:
        # No source-backed entry/branch; still give a coverage-driven sketch.
        cases.append({
            "title": f"驱动拥有 {hit.function_name} 行为的最近公开入口",
            "entry_kind": "unknown",
            "preconditions": "需绑定工作区/源码以定位精确入口；当前基于覆盖率证据给出方向",
            "inputs": base_inputs,
            "steps": [
                "定位 API / CLI / 消息 / 配置 / 文件输入中负责该行为的入口",
                f"构造输入触发 {hit.function_name} 所代表的路径",
            ],
            "expected": expected,
            "observable_signals": signals,
            "evidence": f"{hit.file_path}:{hit.line_start or '?'} hit_count={hit.hit_count}",
        })
    return cases[:5]


def _build_gray_box_scheme(
    hit: FunctionHit,
    *,
    repo_root: Path | None,
    cgc_callers: object,
    trigger_branches: list[dict],
    required: bool,
) -> dict:
    """Gray-box injection / stub / fault-injection scheme for hard-to-reach code."""
    text = hit.function_name.lower()
    if any(term in text for term in ("error", "fail", "recover", "rollback", "retry", "timeout")):
        technique = "fault_injection"
        scheme = "对依赖项注入故障（错误返回 / 超时 / 资源不可用 / 重试耗尽）以强制进入该错误处理路径"
    elif any(term in text for term in ("cleanup", "free", "close", "release", "destroy")):
        technique = "resource_interception"
        scheme = "拦截资源分配/释放，在正常完成与强制中断两种情形下断言资源回收"
    else:
        technique = "stub_and_drive"
        scheme = "桩件直接调用该函数 / 短接守卫条件，覆盖目标分支并观察状态与返回"

    injection_points: list[str] = []
    if isinstance(cgc_callers, list):
        for caller in cgc_callers[:4]:
            if isinstance(caller, dict) and caller.get("name"):
                injection_points.append(
                    f"{caller.get('name')} ({caller.get('location') or '位置未知'})"
                )
    for branch in trigger_branches[:4]:
        if branch.get("file"):
            injection_points.append(
                f"守卫条件 {branch.get('condition')} @ {branch.get('file')}:{branch.get('line_number')}"
            )
    if not injection_points:
        injection_points.append(f"目标符号 {hit.function_name} ({hit.file_path})")

    return {
        "required": required,
        "technique": technique,
        "scheme": scheme,
        "injection_points": injection_points[:8],
        "stub_or_fault": scheme,
        "observable_signals": _observable_signals_for_hit(hit),
    }


def _gap_tool_status(
    *,
    repo_root: Path | None,
    rg_available: bool,
    source_window: dict | None,
    cgc_context: dict,
    scope: dict,
) -> dict:
    cgc_ok = bool(isinstance(cgc_context, dict) and cgc_context.get("available"))
    gitnexus_ok = bool(isinstance(scope, dict) and scope.get("gitnexus_available"))
    return {
        # Joern is reserved but not yet wired up.
        "joern": "unavailable_reserved",
        "cgc": "available" if cgc_ok else "unavailable",
        "gitnexus": "available" if gitnexus_ok else "unavailable",
        "ripgrep": "available" if rg_available else "unavailable",
        "source": "available" if source_window else (
            "available_no_match" if repo_root is not None else "unavailable"
        ),
    }


def _function_evidence_gaps(
    *,
    workspace_bound: bool,
    source_window: dict | None,
    entry_paths: list[dict],
    trigger_branches: list[dict],
    tool_status: dict,
) -> list[str]:
    gaps: list[str] = []
    if not workspace_bound:
        gaps.append("未绑定工作区/仓库：仅解析覆盖率，未生成源码触发路径（绑定后可深度追踪）")
        return gaps
    if not source_window:
        gaps.append("源码窗口不可用：仓库内未定位到该函数文件，触发分析受限")
    if not trigger_branches:
        gaps.append("未在源码窗口/调用点发现显式守卫分支，触发条件需人工确认")
    if not entry_paths:
        gaps.append(
            f"在 {ENTRY_TRACE_MAX_HOPS} 跳内未追踪到外部入口（CLI/API/消息/配置/文件），需灰盒注入"
        )
    if tool_status.get("joern") != "available":
        gaps.append("Joern 未接入：缺少精确分支/错误路径/边界值分析，结果为降级模式")
    if tool_status.get("cgc") != "available":
        gaps.append("CGC 不可用：调用链来自 ripgrep 文本匹配，可能存在同名/遗漏")
    return gaps


def _confidence_for_gap(
    scope: dict,
    cgc_context: dict,
    source_window: dict | None,
    entry_paths: list[dict],
) -> str:
    has_symbol_evidence = (
        bool(entry_paths)
        or (isinstance(cgc_context, dict) and cgc_context.get("callers"))
        or (isinstance(scope, dict) and scope.get("candidate_symbols"))
    )
    if has_symbol_evidence:
        return "high"
    if source_window or (
        isinstance(scope, dict)
        and (scope.get("candidate_files") or scope.get("related_communities"))
    ):
        return "medium"
    return "low"


def _build_branch_gaps(
    modules: list[ModuleCoverage],
    *,
    workspace_bound: bool,
) -> list[dict]:
    """Design cases directly from uncovered branch conditions (no source needed)."""
    gaps: list[dict] = []
    for module in modules:
        for raw in module.uncovered_branches[:40]:
            condition = _extract_branch_condition(str(raw))
            is_error = bool(_ERROR_CONDITION_RE.search(str(raw)))
            gaps.append({
                "kind": "branch",
                "module_path": module.module_path,
                "branch": str(raw),
                "condition": condition,
                "category": _branch_category(str(raw)),
                "risk_level": "high" if is_error else "medium",
                "black_box_cases": [{
                    "title": f"覆盖未触发分支：{condition}",
                    "preconditions": f"构造输入/状态使分支条件成立：{condition}",
                    "inputs": "为条件的真/假两侧分别准备数据（含边界值与非法值）",
                    "steps": [
                        f"令条件 `{condition}` 成立并执行",
                        "再令其不成立，验证另一侧行为",
                    ],
                    "expected": "两侧分支均产生文档化的、可观测的结果；错误侧不得静默成功",
                    "observable_signals": ["返回值/状态码", "日志", "状态迁移"],
                }],
                "gray_box": {
                    "required": False,
                    "technique": "input_shaping",
                    "scheme": "通过外部输入直接构造分支条件；无法构造时短接守卫条件",
                    "injection_points": [f"分支条件 {condition}"],
                },
                "gray_box_required": False,
                "evidence_gaps": (
                    [] if workspace_bound
                    else ["未绑定工作区：分支来自覆盖率文件，未做源码定位"]
                ),
            })
            if len(gaps) >= 60:
                return gaps
    return gaps


async def build_coverage_test_design(
    modules: list[ModuleCoverage],
    *,
    workspace_id: str | None,
    repo_path: str | None,
) -> dict:
    """Build the ``coverage-test-design-v1`` structure for a coverage report.

    Produces ``{version, summary, gaps, warnings}`` where ``gaps`` mixes
    uncovered-function gaps (with entry-oriented trigger paths + black/gray-box
    cases) and uncovered-branch gaps (designed straight from the condition).
    When the coverage is not bound to a workspace/repo on disk, no source-backed
    trigger paths are fabricated — only parse-level guidance is returned.
    """
    workspace_bound = _existing_repo_root(repo_path) is not None and bool(workspace_id)

    function_gaps = await _build_black_box_function_recommendations(
        modules, workspace_id=workspace_id, repo_path=repo_path
    )
    branch_gaps = _build_branch_gaps(modules, workspace_bound=workspace_bound)
    gaps = [*function_gaps, *branch_gaps]

    tool_status = _aggregate_tool_status(function_gaps, repo_path=repo_path)
    warnings = _design_warnings(
        workspace_bound=workspace_bound,
        workspace_id=workspace_id,
        repo_path=repo_path,
        tool_status=tool_status,
        function_gaps=function_gaps,
    )

    summary = {
        "module_count": len(modules),
        "uncovered_function_count": len(function_gaps),
        "uncovered_branch_count": len(branch_gaps),
        "black_box_ready_count": sum(
            1 for g in function_gaps if g.get("entry_paths")
        ),
        "gray_box_required_count": sum(
            1 for g in function_gaps if g.get("gray_box_required")
        ),
        "high_risk_count": sum(1 for g in gaps if g.get("risk_level") == "high"),
        "workspace_bound": workspace_bound,
        "tool_status": tool_status,
    }

    return {
        "version": COVERAGE_TEST_DESIGN_VERSION,
        "workspace_id": workspace_id,
        "repo_path": repo_path,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "gaps": gaps,
        "warnings": warnings,
    }


def _aggregate_tool_status(function_gaps: list[dict], *, repo_path: str | None) -> dict:
    repo_root = _existing_repo_root(repo_path)
    rg_available = shutil.which("rg") is not None
    cgc_ok = any(
        (g.get("tool_status") or {}).get("cgc") == "available" for g in function_gaps
    )
    gitnexus_ok = any(
        (g.get("tool_status") or {}).get("gitnexus") == "available" for g in function_gaps
    )
    source_ok = any(g.get("source_window") for g in function_gaps)
    return {
        "joern": "unavailable_reserved",
        "cgc": "available" if cgc_ok else "unavailable",
        "gitnexus": "available" if gitnexus_ok else "unavailable",
        "ripgrep": "available" if rg_available else "unavailable",
        "source": "available" if source_ok else (
            "available_no_match" if repo_root is not None else "unavailable"
        ),
    }


def _design_warnings(
    *,
    workspace_bound: bool,
    workspace_id: str | None,
    repo_path: str | None,
    tool_status: dict,
    function_gaps: list[dict],
) -> list[str]:
    warnings: list[str] = []
    if not workspace_bound:
        if not workspace_id:
            warnings.append("覆盖率未绑定工作区：仅解析覆盖率，未做深度触发路径设计；请绑定工作区后重试")
        elif not _existing_repo_root(repo_path):
            warnings.append("绑定的仓库路径在本机不可访问：无法读取源码，未生成触发路径")
    if tool_status.get("joern", "").startswith("unavailable"):
        warnings.append("Joern 工具未接入（预留）：已使用 CGC/ripgrep 降级，缺少精确边界值/错误路径分析")
    if tool_status.get("cgc") != "available" and workspace_bound:
        warnings.append("CGC 不可用：调用链/调用点来自 ripgrep 文本搜索，可能不完整")
    if tool_status.get("gitnexus") != "available" and workspace_bound:
        warnings.append("GitNexus 图谱不可用：缺少 scope/社区上下文增强")
    if tool_status.get("ripgrep") != "available" and workspace_bound:
        warnings.append("ripgrep 不可用：无法进行调用点搜索，触发路径分析严重受限")
    gray_only = [g for g in function_gaps if g.get("gray_box_required")]
    if gray_only:
        warnings.append(
            f"{len(gray_only)} 个未覆盖函数在 {ENTRY_TRACE_MAX_HOPS} 跳内未找到外部入口，已给出灰盒注入方案"
        )
    return warnings


def _module_to_dict(m: ModuleCoverage) -> dict:
    return {
        "module_path": m.module_path,
        "line_rate": m.line_rate,
        "branch_rate": m.branch_rate,
        "function_rate": m.function_rate,
        "uncovered_lines": m.uncovered_lines[:200],
        "uncovered_branches": m.uncovered_branches[:100],
        "uncovered_functions": m.uncovered_functions[:100],
        "function_hits": [_function_hit_to_dict(hit) for hit in m.function_hits[:500]],
        "files": [
            {
                "filename": f.filename,
                "line_rate": f.line_rate,
                "branch_rate": f.branch_rate,
                "lines_covered": f.lines_covered,
                "lines_total": f.lines_total,
                "uncovered_lines": f.uncovered_lines[:50],
                "uncovered_functions": f.uncovered_functions[:30],
                "function_hits": [
                    _function_hit_to_dict(hit) for hit in f.function_hits[:100]
                ],
            }
            for f in m.files[:50]
        ],
    }


def _dict_to_module(d: dict) -> ModuleCoverage:
    return ModuleCoverage(
        module_path=d["module_path"],
        line_rate=d.get("line_rate", 0),
        branch_rate=d.get("branch_rate", 0),
        function_rate=d.get("function_rate", 0),
        uncovered_lines=d.get("uncovered_lines", []),
        uncovered_branches=d.get("uncovered_branches", []),
        uncovered_functions=d.get("uncovered_functions", []),
        function_hits=[_dict_to_function_hit(h) for h in d.get("function_hits", [])],
        files=[
            FileCoverage(
                filename=f.get("filename", ""),
                line_rate=f.get("line_rate", 0),
                branch_rate=f.get("branch_rate", 0),
                lines_covered=f.get("lines_covered", 0),
                lines_total=f.get("lines_total", 0),
                uncovered_lines=f.get("uncovered_lines", []),
                uncovered_functions=f.get("uncovered_functions", []),
                function_hits=[
                    _dict_to_function_hit(h) for h in f.get("function_hits", [])
                ],
            )
            for f in d.get("files", [])
        ],
    )


def _function_hit_to_dict(hit: FunctionHit) -> dict:
    return {
        "feature_name": hit.feature_name,
        "module_name": hit.module_name,
        "function_name": hit.function_name,
        "file_path": hit.file_path,
        "line_start": hit.line_start,
        "line_end": hit.line_end,
        "triggered": hit.triggered,
        "hit_count": hit.hit_count,
        "raw_location": hit.raw_location,
        "raw": hit.raw,
    }


def _dict_to_function_hit(d: dict) -> FunctionHit:
    return FunctionHit(
        function_name=d.get("function_name", ""),
        file_path=d.get("file_path", ""),
        feature_name=d.get("feature_name", ""),
        module_name=d.get("module_name", ""),
        line_start=d.get("line_start"),
        line_end=d.get("line_end"),
        triggered=bool(d.get("triggered", False)),
        hit_count=int(d.get("hit_count", 0) or 0),
        raw_location=d.get("raw_location", ""),
        raw=d.get("raw", {}),
    )
