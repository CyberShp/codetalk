"""Coverage analysis service — parses coverage data and uses LLM to
recommend uncovered branches, test points, and test cases."""

from __future__ import annotations

import json
import logging
import asyncio
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
    """Build black-box test recommendations for uncovered function-hit rows."""
    uncovered = _collect_uncovered_function_hits(modules)
    scope_by_function = await _resolve_workspace_scope_for_hits(
        uncovered, workspace_id=workspace_id, repo_path=repo_path
    )
    cgc_by_function = await _resolve_cgc_context_for_hits(uncovered, repo_path=repo_path)

    results: list[dict] = []
    for module, hit in uncovered[:50]:
        risk_level = _risk_level_for_hit(hit)
        scope = scope_by_function.get(_hit_key(hit), {})
        cgc_context = cgc_by_function.get(_hit_key(hit), {})
        result = {
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
            "confidence": _confidence_for_context(scope, cgc_context),
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
        results.append(result)

    return sorted(
        results,
        key=lambda r: {"high": 0, "medium": 1, "low": 2}[r["risk_level"]],
    )


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
    return (
        "### 黑盒测试建议\n"
        f"- 测试目标: {result['scenario']}\n"
        f"- 输入/前置条件: {result['input_conditions']}\n"
        f"- 预期行为: {result['expected_behavior']}\n"
        f"- 可观测信号: {signals}\n"
        f"- 风险等级: {result['risk_level']}\n"
        f"- 证据: {result['file_path']}:{result.get('line_start') or '?'} "
        f"hit_count={result['hit_count']}\n"
    )


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
