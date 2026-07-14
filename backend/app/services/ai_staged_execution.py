from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
import shutil
import time
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.config import settings
from app.llm.base import BaseLLMClient, current_finish_reason
from app.services.ai_thread_artifacts import _validate_schema, materialize_ai_thread_manifest


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
CancellationCallback = Callable[[], Awaitable[bool] | bool]
_CANCELLATION_POLL_INTERVAL = 0.1
_SOURCE_EVIDENCE_PACK_VERSION = "source-evidence-pack-v1"
_SOURCE_ANALYSIS_CACHE_VERSION = "source-analysis-cache-v2"


def build_source_analysis_context(
    *,
    plan: dict[str, Any],
    staged_context: dict[str, Any],
    max_files: int | None = None,
    excerpt_chars: int | None = None,
    max_evidence_anchors: int | None = None,
) -> dict[str, Any]:
    """Project the full execution context into the source-analysis contract."""
    file_limit = max(1, int(max_files or settings.source_analysis_max_files))
    excerpt_limit = max(200, int(excerpt_chars or settings.source_analysis_excerpt_chars))
    anchor_limit = max(
        1,
        int(max_evidence_anchors or settings.source_analysis_max_evidence_anchors),
    )
    source_context = (
        staged_context.get("source_context")
        if isinstance(staged_context.get("source_context"), dict)
        else staged_context
    )
    candidates = [
        item for item in source_context.get("files") or [] if isinstance(item, dict)
    ]
    files: list[dict[str, Any]] = []
    for item in candidates[: min(file_limit, anchor_limit)]:
        path = str(item.get("file_path") or "").strip()
        if not path:
            continue
        files.append(
            {
                "file_path": path,
                "classification": str(
                    item.get("classification") or _source_file_classification(path)
                ),
                "start_line": int(item.get("start_line") or 0),
                "end_line": int(item.get("end_line") or 0),
                "excerpt": str(item.get("excerpt") or "")[:excerpt_limit],
                "symbols": [
                    str(value)
                    for value in item.get("symbols") or []
                    if str(value).strip()
                ][:12],
                "matched_terms": [
                    str(value)
                    for value in item.get("matched_terms") or []
                    if str(value).strip()
                ][:16],
                "sha256": str(item.get("sha256") or ""),
                "validation_status": str(
                    item.get("status") or "validated_source_file"
                ),
            }
        )
    materials = _source_input_material_summaries(staged_context)
    gitnexus_summary, cgc_summary = _source_tool_summaries(staged_context)
    gaps: list[str] = []
    if not files:
        gaps.append("没有可用的 SHA256 校验源码片段")
    if files and not any(item["classification"] == "test" for item in files):
        gaps.append("最高相关证据中缺少测试目录文件")
    if files and not any(item.get("symbols") for item in files):
        gaps.append("已验证片段未提取到符号")
    analysis_target = str(plan.get("original_user_request") or "").strip()
    if not analysis_target:
        analysis_target = str(plan.get("target") or "").strip()
    return {
        "version": "source-analysis-context-v1",
        "analysis_target": analysis_target[:4000],
        "repo_path": str(
            source_context.get("repo_path") or staged_context.get("repo_path") or ""
        ),
        "repo_revision": str(source_context.get("repo_revision") or ""),
        "files": files,
        "verified_symbols": sorted(
            {symbol for item in files for symbol in item.get("symbols") or []}
        )[:64],
        "input_materials": materials,
        "gitnexus_summary": gitnexus_summary,
        "cgc_summary": cgc_summary,
        "evidence_gaps": gaps,
        "output_constraints": {
            "max_chinese_characters": settings.source_analysis_max_chinese_characters,
            "max_evidence_anchors": anchor_limit,
            "allowed_work": ["证据排序", "事实归纳", "缺口标记"],
            "forbidden_work": ["重新发现源码", "生成 SFMEA", "生成黑盒用例"],
        },
    }


def build_source_evidence_pack(context: dict[str, Any]) -> dict[str, Any]:
    """Create model-independent source scope and evidence cards."""
    cards: list[dict[str, Any]] = []
    for index, item in enumerate(context.get("files") or [], 1):
        if not isinstance(item, dict):
            continue
        cards.append(
            {
                "evidence_id": f"SRC-{index:02d}",
                "file_path": str(item.get("file_path") or ""),
                "classification": str(item.get("classification") or "source"),
                "kind": str(item.get("classification") or "source"),
                "start_line": int(item.get("start_line") or 0),
                "end_line": int(item.get("end_line") or 0),
                "line_count": max(
                    0,
                    int(item.get("end_line") or 0)
                    - int(item.get("start_line") or 0)
                    + 1,
                ),
                "excerpt": str(item.get("excerpt") or ""),
                "symbols": [str(value) for value in item.get("symbols") or []],
                "matched_terms": [
                    str(value) for value in item.get("matched_terms") or []
                ],
                "sha256": str(item.get("sha256") or ""),
                "reason": _source_evidence_reason(item),
                "source": "local-source-search",
                "validation_status": str(
                    item.get("validation_status") or "validated_source_file"
                ),
            }
        )
    invalid = [
        card["evidence_id"]
        for card in cards
        if not card["file_path"]
        or not card["sha256"]
        or card["start_line"] <= 0
        or card["end_line"] < card["start_line"]
    ]
    missing_symbols = [
        card["evidence_id"] for card in cards if not card.get("symbols")
    ]
    has_verified_symbol = any(card.get("symbols") for card in cards)
    source_files = [
        card["file_path"] for card in cards if card["classification"] == "source"
    ]
    test_files = [
        card["file_path"] for card in cards if card["classification"] == "test"
    ]
    scope_seed = "\n".join(
        [
            str(context.get("repo_revision") or ""),
            str(context.get("analysis_target") or ""),
            *[f"{card['file_path']}:{card['sha256']}" for card in cards],
        ]
    )
    entry_points = [
        {
            "file_path": card["file_path"],
            "symbol": str((card.get("symbols") or [""])[0]),
            "reason": card["reason"],
        }
        for card in cards
        if card.get("symbols")
    ]
    return {
        "version": _SOURCE_EVIDENCE_PACK_VERSION,
        "analysis_target": str(context.get("analysis_target") or ""),
        "repo_revision": str(context.get("repo_revision") or ""),
        "source_scope": {
            "scope_id": "source-scope-"
            + hashlib.sha256(scope_seed.encode("utf-8")).hexdigest()[:16],
            "query": str(context.get("analysis_target") or ""),
            "repo": str(context.get("repo_path") or ""),
            "discovery": {
                "provider": "local-source-search",
                "method": "sha256-validated-evidence-pack",
                "file_count": len(cards),
            },
            "files": [card["file_path"] for card in cards],
            "entry_points": entry_points,
            "analysis_target": str(context.get("analysis_target") or ""),
            "repo_revision": str(context.get("repo_revision") or ""),
            "source_files": source_files,
            "test_files": test_files,
            "file_count": len(cards),
            "verified_symbols": [
                str(value) for value in context.get("verified_symbols") or []
            ],
            "evidence_gaps": [
                str(value) for value in context.get("evidence_gaps") or []
            ],
        },
        "evidence_cards": cards,
        "input_materials": context.get("input_materials") or [],
        "tool_summaries": {
            "gitnexus": str(context.get("gitnexus_summary") or ""),
            "cgc": str(context.get("cgc_summary") or ""),
        },
        "quality_gate": {
            "status": (
                "passed"
                if cards and not invalid and has_verified_symbol
                else "limited"
            ),
            "invalid_evidence_ids": invalid,
            "missing_symbol_evidence_ids": missing_symbols,
            "sha256_validated_count": sum(bool(card["sha256"]) for card in cards),
            "source_file_count": len(source_files),
            "test_file_count": len(test_files),
        },
    }


def _source_evidence_reason(item: dict[str, Any]) -> str:
    terms = [str(value) for value in item.get("matched_terms") or [] if str(value)]
    symbols = [str(value) for value in item.get("symbols") or [] if str(value)]
    details: list[str] = []
    if terms:
        details.append("matched terms: " + ", ".join(terms[:8]))
    if symbols:
        details.append("verified symbols: " + ", ".join(symbols[:6]))
    return "; ".join(details) or "SHA256-validated local source excerpt"


def materialize_source_evidence_pack(
    pack: dict[str, Any], artifact_dir: Path
) -> dict[str, Path]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "source_analysis": artifact_dir / "source_analysis.md",
        "source_scope": artifact_dir / "source_scope.json",
        "evidence_cards": artifact_dir / "evidence_cards.json",
    }
    _write_text(paths["source_analysis"], _source_analysis_markdown(pack))
    if not paths["source_scope"].exists():
        _write_json(paths["source_scope"], pack.get("source_scope") or {})
    if not paths["evidence_cards"].exists():
        _write_json(paths["evidence_cards"], pack.get("evidence_cards") or [])
    return paths


def _source_analysis_markdown(pack: dict[str, Any]) -> str:
    lines = [
        "# Source Analysis",
        "",
        f"- 分析目标：{pack.get('analysis_target') or '(未提供)'}",
        f"- Repo revision：{pack.get('repo_revision') or '(未记录)'}",
        f"- 已验证证据：{len(pack.get('evidence_cards') or [])} 个",
        "",
        "## 已验证证据",
    ]
    for card in pack.get("evidence_cards") or []:
        if not isinstance(card, dict):
            continue
        symbols = ", ".join(str(value) for value in card.get("symbols") or [])
        terms = ", ".join(str(value) for value in card.get("matched_terms") or [])
        lines.append(
            f"- `{card.get('file_path')}:{card.get('start_line')}-{card.get('end_line')}` "
            f"[{card.get('classification')}] symbols={symbols or '(none)'}; "
            f"matched={terms or '(none)'}; sha256={card.get('sha256')}"
        )
    lines.extend(["", "## 证据缺口"])
    gaps = (pack.get("source_scope") or {}).get("evidence_gaps") or []
    lines.extend(f"- {gap}" for gap in gaps)
    if not gaps:
        lines.append("- 当前确定性证据包未发现结构性缺口；业务完整性仍由后续阶段复核。")
    return "\n".join(lines).rstrip() + "\n"


def _source_file_classification(path: str) -> str:
    normalized = path.replace("\\", "/").lower().lstrip("./")
    return "test" if normalized.startswith(("test/", "tests/", "spec/")) else "source"


def _source_input_material_summaries(staged_context: dict[str, Any]) -> list[dict[str, str]]:
    container = staged_context.get("input_materials")
    materials = container.get("materials") if isinstance(container, dict) else []
    summaries: list[dict[str, str]] = []
    for item in materials or []:
        if not isinstance(item, dict):
            continue
        summary = next(
            (
                str(item.get(key) or "").strip()
                for key in (
                    "text_preview",
                    "summary",
                    "content_preview",
                    "material_role",
                    "role",
                )
                if str(item.get(key) or "").strip()
            ),
            "",
        )
        parsed_text_path = str(item.get("parsed_text_path") or "")
        if parsed_text_path and (not summary or summary == item.get("material_role")):
            try:
                summary = Path(parsed_text_path).read_text(
                    encoding="utf-8", errors="replace"
                )[:1000]
            except OSError:
                pass
        summaries.append(
            {
                "input_id": str(item.get("input_id") or ""),
                "sha256": str(item.get("sha256") or ""),
                "summary": summary[:1000],
            }
        )
    return summaries[:8]


def _source_tool_summaries(staged_context: dict[str, Any]) -> tuple[str, str]:
    candidates = [
        staged_context.get("mcp"),
        staged_context.get("prefetched_evidence"),
    ]
    serialized = "\n".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True)
        for item in candidates
        if isinstance(item, dict)
    )
    gitnexus = _summary_around_keyword(serialized, "gitnexus")
    cgc = _summary_around_keyword(serialized, "cgc")
    return gitnexus, cgc


def _summary_around_keyword(text: str, keyword: str, limit: int = 1200) -> str:
    lower = text.lower()
    index = lower.find(keyword)
    if index < 0:
        return ""
    start = max(0, index - 200)
    return text[start : start + limit]


class StagedExecutionCancelled(RuntimeError):
    """Raised after cancelling an in-flight staged provider request."""


async def _complete_with_cancellation(
    *,
    llm: Any,
    prompt: str,
    max_tokens: int,
    is_cancelled: CancellationCallback | None,
    timeout_seconds: float | None = None,
    single_attempt: bool = False,
) -> Any:
    complete = (
        getattr(llm, "complete_once")
        if single_attempt and callable(getattr(llm, "complete_once", None))
        else llm.complete
    )
    provider_task = asyncio.create_task(
        complete(
            [{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.2,
        )
    )
    deadline = (
        time.monotonic() + max(0.001, float(timeout_seconds))
        if timeout_seconds is not None
        else None
    )
    try:
        while True:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                provider_task.cancel()
                with suppress(asyncio.CancelledError):
                    await provider_task
                raise asyncio.TimeoutError
            done, _ = await asyncio.wait(
                {provider_task},
                timeout=(
                    _CANCELLATION_POLL_INTERVAL
                    if remaining is None
                    else min(_CANCELLATION_POLL_INTERVAL, remaining)
                ),
            )
            if provider_task in done:
                return provider_task.result()
            if is_cancelled is not None and await _callback_true(is_cancelled):
                provider_task.cancel()
                with suppress(asyncio.CancelledError):
                    await provider_task
                raise StagedExecutionCancelled("任务已取消，已停止当前模型调用和后续阶段")
    finally:
        if not provider_task.done():
            provider_task.cancel()
            with suppress(asyncio.CancelledError):
                await provider_task


_STAGE_BY_ARTIFACT = {
    "source_scope.json": ("source_scope", ["source_analysis"]),
    "evidence_cards.json": ("evidence_cards", ["source_analysis"]),
    "flow_map.md": ("business_flow", ["source_analysis"]),
    "project_structure.md": ("project_structure", ["source_analysis"]),
    "source_reading_plan.md": ("source_reading_plan", ["source_analysis"]),
    "module_map.md": ("module_map", ["source_analysis"]),
    "tester_code_understanding.md": ("tester_code_understanding", ["source_analysis"]),
    "business_flow.md": ("business_flow", ["source_analysis"]),
    "sfmea.json": ("sfmea", ["source_analysis", "business_flow"]),
    "black_box_cases.json": (
        "black_box_cases",
        ["source_analysis", "business_flow", "sfmea"],
    ),
    "black_box_cases.md": (
        "black_box_cases",
        ["source_analysis", "business_flow", "sfmea"],
    ),
    "test_strategy.md": ("test_strategy", ["source_analysis", "business_flow"]),
    "test_design.md": (
        "test_design",
        ["source_analysis", "business_flow", "sfmea", "black_box_cases"],
    ),
    "coverage_gap_report.md": ("coverage_gap", ["source_analysis"]),
    "risk_review.md": ("risk_review", ["source_analysis", "sfmea"]),
    "execution_checklist.md": (
        "execution_checklist",
        ["business_flow", "black_box_cases"],
    ),
}

_CANONICAL_STAGE_ORDER = (
    "source_analysis",
    "source_scope",
    "evidence_cards",
    "project_structure",
    "source_reading_plan",
    "module_map",
    "tester_code_understanding",
    "business_flow",
    "sfmea",
    "black_box_cases",
    "test_strategy",
    "test_design",
    "coverage_gap",
    "risk_review",
    "execution_checklist",
)
_CANONICAL_STAGE_RANK = {
    stage_id: index for index, stage_id in enumerate(_CANONICAL_STAGE_ORDER)
}
_SUPPORT_ARTIFACT = {
    "business_flow": "business_flow.md",
    "sfmea": "sfmea.json",
    "black_box_cases": "black_box_cases.json",
}


def build_staged_execution_plan(
    *,
    contract: dict[str, Any],
    original_user_request: str,
) -> dict[str, Any]:
    outputs = [
        str(value).strip()
        for value in contract.get("required_outputs") or []
        if str(value).strip()
    ]
    artifact_contract = (
        contract.get("artifact_contract")
        if isinstance(contract.get("artifact_contract"), dict)
        else {}
    )
    stages: list[dict[str, Any]] = [
        {
            "id": "source_analysis",
            "artifact": "source_analysis.md",
            "depends_on": [],
            "purpose": "读取源码、测试目录和输入材料，形成紧凑、可验证的证据索引",
            "support": True,
            "max_tokens": settings.source_analysis_max_tokens,
            "output_limits": {
                "max_chinese_characters": settings.source_analysis_max_chinese_characters,
                "max_evidence_anchors": settings.source_analysis_max_evidence_anchors,
            },
        }
    ]
    requested: list[tuple[int, str, str, list[str]]] = []
    for output_index, artifact in enumerate(outputs):
        stage_id, dependencies = _STAGE_BY_ARTIFACT.get(
            artifact,
            (f"artifact_{output_index + 1}", ["source_analysis"]),
        )
        requested.append((output_index, artifact, stage_id, list(dependencies)))

    requested_stage_ids = {item[2] for item in requested}
    required_support_ids: set[str] = set()
    for _, _, _, dependencies in requested:
        required_support_ids.update(
            dependency
            for dependency in dependencies
            if dependency != "source_analysis" and dependency not in requested_stage_ids
        )
    while True:
        expanded = set(required_support_ids)
        for support_id in required_support_ids:
            support_artifact = _SUPPORT_ARTIFACT.get(support_id, f"{support_id}.md")
            _, support_dependencies = _STAGE_BY_ARTIFACT.get(
                support_artifact, (support_id, ["source_analysis"])
            )
            expanded.update(
                dependency
                for dependency in support_dependencies
                if dependency != "source_analysis"
                and dependency not in requested_stage_ids
            )
        if expanded == required_support_ids:
            break
        required_support_ids = expanded
    for support_id in sorted(
        required_support_ids,
        key=lambda item: _CANONICAL_STAGE_RANK.get(item, 10_000),
    ):
        artifact = _SUPPORT_ARTIFACT.get(support_id, f"{support_id}.md")
        _, dependencies = _STAGE_BY_ARTIFACT.get(
            artifact, (support_id, ["source_analysis"])
        )
        requested.append((-1, artifact, support_id, list(dependencies)))

    requested.sort(
        key=lambda item: (
            _CANONICAL_STAGE_RANK.get(item[2], 10_000),
            item[0] if item[0] >= 0 else -1,
        )
    )
    stage_counts: dict[str, int] = {}
    available_stage_ids = {"source_analysis", *(item[2] for item in requested)}
    for output_index, artifact, base_stage_id, dependencies in requested:
        stage_counts[base_stage_id] = stage_counts.get(base_stage_id, 0) + 1
        occurrence = stage_counts[base_stage_id]
        stage_id = base_stage_id if occurrence == 1 else f"{base_stage_id}__{occurrence}"
        projected_dependencies = [
            item for item in dependencies if item in available_stage_ids
        ]
        raw_contract = artifact_contract.get(artifact)
        output_contract = dict(raw_contract) if isinstance(raw_contract, dict) else {"artifact": artifact}
        output_contract["artifact"] = artifact
        if output_contract.get("schema") is None:
            output_contract.pop("schema", None)
        stages.append(
            {
                "id": stage_id,
                "artifact": artifact,
                "depends_on": projected_dependencies,
                "purpose": _stage_purpose(stage_id),
                "support": output_index < 0,
                "output_contract": output_contract,
                **_stage_execution_limits(base_stage_id),
            }
        )
    return {
        "version": "ai-staged-execution-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "original_user_request": str(original_user_request),
        "target": str(contract.get("target") or ""),
        "required_outputs": outputs,
        "stages": stages,
    }


async def execute_staged_builtin_plan(
    *,
    llm: Any,
    plan: dict[str, Any],
    artifact_dir: Path,
    context_prompt: str,
    on_progress: ProgressCallback | None = None,
    is_cancelled: CancellationCallback | None = None,
    max_tokens: int = 4096,
    source_analysis_context: dict[str, Any] | None = None,
    source_analysis_llm: Any | None = None,
    source_analysis_cache_dir: Path | None = None,
    source_analysis_limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    _write_json(artifact_dir / "staged_execution_plan.json", plan)
    completed: dict[str, Path] = {}
    models: set[str] = set()
    stages = [item for item in plan.get("stages") or [] if isinstance(item, dict)]
    for index, stage in enumerate(stages):
        stage_id = str(stage.get("id") or f"stage_{index + 1}")
        artifact = str(stage.get("artifact") or f"{stage_id}.md")
        stage_dir = artifact_dir / "stages" / stage_id
        stage_dir.mkdir(parents=True, exist_ok=True)
        if await _callback_true(is_cancelled):
            _write_json(
                stage_dir / "stage_result.json",
                {"stage_id": stage_id, "status": "cancelled", "artifact": artifact},
            )
            raise StagedExecutionCancelled("任务已取消，已停止后续阶段")
        await _emit_progress(
            on_progress,
            {
                "stage_id": stage_id,
                "status": "running",
                "current": index + 1,
                "total": len(stages),
                "artifact": artifact,
            },
        )
        if stage_id == "source_analysis":
            source_outcome = await _execute_source_analysis_stage(
                llm=source_analysis_llm or llm,
                plan=plan,
                stage=stage,
                stage_dir=stage_dir,
                artifact_dir=artifact_dir,
                context_prompt=context_prompt,
                staged_context=(
                    source_analysis_context
                    if isinstance(source_analysis_context, dict)
                    else _decode_source_context(context_prompt)
                ),
                is_cancelled=is_cancelled,
                cache_dir=source_analysis_cache_dir,
                limits=source_analysis_limits,
                on_progress=on_progress,
            )
            output_path = Path(source_outcome["output_path"])
            completed[stage_id] = output_path
            response_model = str(source_outcome.get("model") or "").strip()
            if response_model:
                models.add(response_model)
            await _emit_progress(
                on_progress,
                {
                    "stage_id": stage_id,
                    "status": "completed",
                    "current": index + 1,
                    "total": len(stages),
                    "artifact": artifact,
                    "degraded": bool(source_outcome.get("degraded")),
                },
            )
            await _execute_ready_stage_levels(
                llm=llm,
                plan=plan,
                stages=stages,
                artifact_dir=artifact_dir,
                context_prompt=context_prompt,
                completed=completed,
                models=models,
                is_cancelled=is_cancelled,
                on_progress=on_progress,
                max_tokens=max_tokens,
            )
            break
        outcome = await _execute_regular_stage(
            llm=llm,
            plan=plan,
            stage=stage,
            stage_dir=stage_dir,
            artifact_dir=artifact_dir,
            context_prompt=context_prompt,
            completed=completed,
            is_cancelled=is_cancelled,
            max_tokens=max_tokens,
        )
        output_path = Path(outcome["output_path"])
        response_model = str(outcome.get("model") or "").strip()
        if response_model:
            models.add(response_model)
        completed[stage_id] = output_path
        await _emit_progress(
            on_progress,
            {
                "stage_id": stage_id,
                "status": "completed",
                "current": index + 1,
                "total": len(stages),
                "artifact": artifact,
            },
        )
    declared_artifacts = []
    for stage in stages:
        if stage.get("support"):
            continue
        contract = stage.get("output_contract")
        item = dict(contract) if isinstance(contract, dict) else {}
        item["artifact"] = str(stage.get("artifact") or "")
        item["required"] = True
        if item["artifact"].endswith(".json"):
            item.setdefault("type", "json")
        declared_artifacts.append(item)
    manifest = materialize_ai_thread_manifest(
        artifact_dir,
        run_id=str(plan.get("run_id") or "staged-run"),
        declared_artifacts=declared_artifacts,
        producer="builtin_llm:staged",
    )
    execution = {
        "version": "ai-staged-execution-result-v1",
        "status": "completed",
        "completed_stages": len(stages),
        "total_stages": len(stages),
        "manifest": "artifact_manifest.json",
        "models": sorted(models),
    }
    _write_json(artifact_dir / "staged_execution_result.json", execution)
    return {**execution, "artifact_manifest": manifest}


async def _execute_ready_stage_levels(
    *,
    llm: Any,
    plan: dict[str, Any],
    stages: list[dict[str, Any]],
    artifact_dir: Path,
    context_prompt: str,
    completed: dict[str, Path],
    models: set[str],
    is_cancelled: CancellationCallback | None,
    on_progress: ProgressCallback | None,
    max_tokens: int,
) -> None:
    positions = {
        str(stage.get("id") or f"stage_{index + 1}"): index
        for index, stage in enumerate(stages)
    }
    remaining = {
        str(stage.get("id") or f"stage_{index + 1}"): stage
        for index, stage in enumerate(stages)
        if str(stage.get("id") or f"stage_{index + 1}") not in completed
    }
    while remaining:
        ready = [
            (stage_id, stage)
            for stage_id, stage in remaining.items()
            if all(
                str(dependency) in completed
                for dependency in stage.get("depends_on") or []
            )
        ]
        ready.sort(key=lambda item: positions[item[0]])
        if not ready:
            unresolved = {
                stage_id: [str(value) for value in stage.get("depends_on") or []]
                for stage_id, stage in remaining.items()
            }
            raise RuntimeError(f"阶段依赖无法解析或存在循环：{unresolved}")
        tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
        for stage_id, stage in ready:
            artifact = str(stage.get("artifact") or f"{stage_id}.md")
            stage_dir = artifact_dir / "stages" / stage_id
            stage_dir.mkdir(parents=True, exist_ok=True)
            if await _callback_true(is_cancelled):
                _write_json(
                    stage_dir / "stage_result.json",
                    {"stage_id": stage_id, "status": "cancelled", "artifact": artifact},
                )
                raise StagedExecutionCancelled("任务已取消，已停止后续阶段")
            await _emit_progress(
                on_progress,
                {
                    "stage_id": stage_id,
                    "status": "running",
                    "current": positions[stage_id] + 1,
                    "total": len(stages),
                    "artifact": artifact,
                },
            )
            if (
                stage_id in {"source_scope", "evidence_cards"}
                and (artifact_dir / artifact).is_file()
            ):
                output_path = artifact_dir / artifact
                result = {
                    "stage_id": stage_id,
                    "status": "completed",
                    "artifact": artifact,
                    "attempts": 0,
                    "attempt_count": 0,
                    "reused": True,
                    "reuse_source": "deterministic_source_evidence_pack",
                    "size_bytes": output_path.stat().st_size,
                    "model": "",
                }
                _write_json(stage_dir / "stage_result.json", result)
                completed[stage_id] = output_path
                await _emit_progress(
                    on_progress,
                    {
                        "event_type": "stage_reused",
                        "stage_id": stage_id,
                        "status": "completed",
                        "current": positions[stage_id] + 1,
                        "total": len(stages),
                        "artifact": artifact,
                        "reuse_source": "deterministic_source_evidence_pack",
                    },
                )
                continue
            tasks[stage_id] = asyncio.create_task(
                _execute_regular_stage(
                    llm=llm,
                    plan=plan,
                    stage=stage,
                    stage_dir=stage_dir,
                    artifact_dir=artifact_dir,
                    context_prompt=context_prompt,
                    completed=dict(completed),
                    is_cancelled=is_cancelled,
                    max_tokens=max_tokens,
                )
            )
        if tasks:
            try:
                outcomes = await asyncio.gather(*tasks.values())
            except BaseException:
                for task in tasks.values():
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks.values(), return_exceptions=True)
                raise
            for stage_id, outcome in zip(tasks, outcomes):
                completed[stage_id] = Path(outcome["output_path"])
                response_model = str(outcome.get("model") or "").strip()
                if response_model:
                    models.add(response_model)
                stage = remaining[stage_id]
                await _emit_progress(
                    on_progress,
                    {
                        "stage_id": stage_id,
                        "status": "completed",
                        "current": positions[stage_id] + 1,
                        "total": len(stages),
                        "artifact": str(stage.get("artifact") or f"{stage_id}.md"),
                    },
                )
        for stage_id, _stage in ready:
            remaining.pop(stage_id, None)


async def _execute_regular_stage(
    *,
    llm: Any,
    plan: dict[str, Any],
    stage: dict[str, Any],
    stage_dir: Path,
    artifact_dir: Path,
    context_prompt: str,
    completed: dict[str, Path],
    is_cancelled: CancellationCallback | None,
    max_tokens: int,
) -> dict[str, Any]:
    stage_id = str(stage.get("id") or "stage")
    artifact = str(stage.get("artifact") or f"{stage_id}.md")
    prompt = _stage_prompt(
        plan=plan,
        stage=stage,
        context_prompt=context_prompt,
        completed=completed,
    )
    _write_text(stage_dir / "stage_prompt.txt", prompt)
    response = None
    rendered: Any = None
    last_error = ""
    attempts = 0
    stage_max_tokens = min(
        max_tokens,
        max(256, int(stage.get("max_tokens") or max_tokens)),
    )
    while attempts < 2:
        attempts += 1
        try:
            response = await _complete_with_cancellation(
                llm=llm,
                prompt=prompt,
                max_tokens=stage_max_tokens,
                is_cancelled=is_cancelled,
            )
            if await _callback_true(is_cancelled):
                raise StagedExecutionCancelled("任务已取消，已停止后续阶段")
            raw_content = str(getattr(response, "content", "") or "").strip()
            if raw_content:
                _write_text(
                    stage_dir / f"raw_output_attempt_{attempts}.txt",
                    raw_content,
                )
            if bool(getattr(response, "truncated", False)):
                raise ValueError("provider_output_truncated")
            if not raw_content:
                raise ValueError("provider_output_empty")
            rendered = _render_stage_artifact(raw_content, artifact)
            schema = (
                stage.get("output_contract", {}).get("schema")
                if isinstance(stage.get("output_contract"), dict)
                else None
            )
            if isinstance(schema, dict):
                schema_errors = _validate_schema(rendered, schema)
                if schema_errors:
                    raise ValueError("schema_invalid: " + "; ".join(schema_errors[:5]))
            break
        except Exception as exc:
            if isinstance(exc, StagedExecutionCancelled):
                raise
            last_error = str(exc) or exc.__class__.__name__
            response = None
            rendered = None
        retry_rules = [
            "RETRY_AFTER_STAGE_FAILURE:",
            f"  previous attempt failed validation or transport: {last_error}",
            "  return only the declared artifact, complete and valid.",
        ]
        if last_error == "provider_output_truncated":
            retry_rules.extend(
                [
                    "  上次输出因过长被截断；压缩到原输出的一半以内。",
                    "  只保留最强源码证据、关键测试入口和未验证缺口，不写背景科普或重复叙述。",
                ]
            )
            output_limits = (
                stage.get("output_limits")
                if isinstance(stage.get("output_limits"), dict)
                else {}
            )
            if output_limits.get("max_items"):
                retry_rules.append(
                    f"  本次最多返回 {min(8, int(output_limits['max_items']))} 项，必须闭合 JSON 后再结束。"
                )
            if output_limits.get("max_field_characters"):
                retry_rules.append(
                    f"  每个叙述字段最多 {int(output_limits['max_field_characters'])} 个字符，使用短句。"
                )
        prompt = "\n".join([prompt, "", *retry_rules])
    if response is None or rendered is None:
        result = {
            "stage_id": stage_id,
            "status": "failed",
            "artifact": artifact,
            "attempts": attempts,
            "reason": last_error or "provider_output_invalid",
        }
        _write_json(stage_dir / "stage_result.json", result)
        raise RuntimeError(
            f"阶段 {stage_id} 连续 {attempts} 次输出失败，已停止后续阶段：{result['reason']}"
        )
    raw_content = str(getattr(response, "content", "") or "").strip()
    response_model = str(getattr(response, "model", "") or "").strip()
    _write_text(stage_dir / "raw_output.txt", raw_content)
    output_path = artifact_dir / artifact
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(rendered, str):
        _write_text(output_path, rendered)
    else:
        _write_json(output_path, rendered)
    result = {
        "stage_id": stage_id,
        "status": "completed",
        "artifact": artifact,
        "attempts": attempts,
        "size_bytes": output_path.stat().st_size,
        "model": response_model,
    }
    _write_json(stage_dir / "stage_result.json", result)
    return {**result, "output_path": str(output_path)}


async def _execute_source_analysis_stage(
    *,
    llm: Any,
    plan: dict[str, Any],
    stage: dict[str, Any],
    stage_dir: Path,
    artifact_dir: Path,
    context_prompt: str,
    staged_context: dict[str, Any],
    is_cancelled: CancellationCallback | None,
    cache_dir: Path | None,
    limits: dict[str, Any] | None,
    on_progress: ProgressCallback | None,
) -> dict[str, Any]:
    started = time.monotonic()
    effective = _source_analysis_limits(limits)
    legacy_prompt = _stage_prompt(
        plan=plan,
        stage=stage,
        context_prompt=context_prompt,
        completed={},
    )
    context_started = time.monotonic()
    compact = build_source_analysis_context(
        plan=plan,
        staged_context=staged_context,
        max_files=effective["max_files"],
        excerpt_chars=effective["excerpt_chars"],
        max_evidence_anchors=effective["max_evidence_anchors"],
    )
    context_prepare_ms = round((time.monotonic() - context_started) * 1000, 1)
    _write_json(stage_dir / "source_analysis_context.json", compact)
    pack = build_source_evidence_pack(compact)
    _write_json(stage_dir / "source_evidence_pack.json", pack)
    materialize_source_evidence_pack(pack, artifact_dir)
    output_path = artifact_dir / "source_analysis.md"
    cache_root = _source_analysis_cache_root(cache_dir)
    cache_key = _source_analysis_cache_key(plan=plan, context=compact)
    elapsed_after_context = time.monotonic() - started
    budget_degradation_reason = ""
    if elapsed_after_context >= float(effective["total_timeout_seconds"]):
        budget_degradation_reason = "total_budget_exceeded_during_context"
    elif context_prepare_ms >= float(effective["context_timeout_seconds"]) * 1000:
        budget_degradation_reason = "context_budget_exceeded"
    if (
        not budget_degradation_reason
        and cache_root is not None
        and _restore_source_analysis_cache(
        cache_root=cache_root,
        cache_key=cache_key,
        artifact_dir=artifact_dir,
        expected_pack=pack,
        )
    ):
        duration_ms = round((time.monotonic() - started) * 1000, 1)
        result = {
            "stage_id": "source_analysis",
            "status": "completed",
            "artifact": "source_analysis.md",
            "attempts": 0,
            "attempt_count": 0,
            "prompt_characters_before_compaction": len(legacy_prompt),
            "prompt_characters": 0,
            "prompt_estimated_tokens": 0,
            "provider_wait_ms": 0.0,
            "output_tokens": 0,
            "finish_reason": "cache_hit",
            "full_retry_performed": False,
            "repair_attempt_count": 0,
            "degraded": False,
            "degradation_reason": "",
            "cache_status": "hit",
            "cache_key": cache_key,
            "context_prepare_ms": context_prepare_ms,
            "duration_ms": duration_ms,
            "size_bytes": output_path.stat().st_size,
            "model": "",
            "quality_gate": pack.get("quality_gate") or {},
        }
        _write_json(stage_dir / "stage_result.json", result)
        await _emit_progress(
            on_progress,
            {
                "event_type": "stage_reused",
                "stage_id": "source_analysis",
                "status": "completed",
                "artifact": "source_analysis.md",
                "cache_key": cache_key,
            },
        )
        return {**result, "output_path": str(output_path)}

    prompt = _source_analysis_prompt(stage=stage, compact_context=compact)
    _write_text(stage_dir / "stage_prompt.txt", prompt)
    prompt_tokens = BaseLLMClient.estimate_tokens(prompt)
    provider_wait_ms = 0.0
    output_tokens = 0
    finish_reason = "not_called"
    model = ""
    raw_content = ""
    attempt_count = 0
    repair_attempt_count = 0
    repair_provider_wait_ms = 0.0
    degraded = bool(budget_degradation_reason)
    degradation_reason = budget_degradation_reason
    provider_phase = "full"
    quality_gate = pack.get("quality_gate") or {}
    if budget_degradation_reason:
        finish_reason = "budget_exceeded"
    elif not pack.get("evidence_cards"):
        degraded = True
        degradation_reason = "no_verified_evidence"
    else:
        attempt_count = 1
        provider_started = time.monotonic()
        elapsed = provider_started - started
        provider_timeout = min(
            float(effective["timeout_seconds"]),
            max(0.001, float(effective["total_timeout_seconds"]) - elapsed),
        )
        current_finish_reason.set(None)
        try:
            response = await _complete_with_cancellation(
                llm=llm,
                prompt=prompt,
                max_tokens=min(
                    int(stage.get("max_tokens") or effective["max_tokens"]),
                    int(effective["max_tokens"]),
                ),
                is_cancelled=is_cancelled,
                timeout_seconds=provider_timeout,
                single_attempt=True,
            )
            provider_wait_ms = round((time.monotonic() - provider_started) * 1000, 1)
            raw_content = str(getattr(response, "content", "") or "").strip()
            if raw_content:
                _write_text(stage_dir / "raw_output_attempt_1.txt", raw_content)
                _write_text(stage_dir / "raw_output.txt", raw_content)
            usage = getattr(response, "usage", {})
            if not isinstance(usage, dict):
                usage = {}
            output_tokens = int(
                usage.get("completion_tokens")
                or (BaseLLMClient.estimate_tokens(raw_content) if raw_content else 0)
            )
            finish_reason = str(
                getattr(response, "finish_reason", "")
                or current_finish_reason.get()
                or ("length" if bool(getattr(response, "truncated", False)) else "stop")
            )
            model = str(getattr(response, "model", "") or "")
            if bool(getattr(response, "truncated", False)) or finish_reason == "length":
                raise ValueError("provider_output_truncated")
            if not raw_content:
                raise ValueError("provider_output_empty")
            format_errors = _source_analysis_format_errors(raw_content)
            if format_errors:
                provider_phase = "repair"
                repair_attempt_count = 1
                repair_prompt = _source_analysis_repair_prompt(
                    raw_content=raw_content,
                    validation_errors=format_errors,
                    pack=pack,
                )
                _write_text(stage_dir / "repair_prompt.txt", repair_prompt)
                remaining_total = max(
                    0.001,
                    float(effective["total_timeout_seconds"])
                    - (time.monotonic() - started),
                )
                repair_timeout = min(
                    float(effective["repair_timeout_seconds"]),
                    remaining_total,
                )
                repair_started = time.monotonic()
                repaired = await _complete_with_cancellation(
                    llm=llm,
                    prompt=repair_prompt,
                    max_tokens=int(effective["repair_max_tokens"]),
                    is_cancelled=is_cancelled,
                    timeout_seconds=repair_timeout,
                    single_attempt=True,
                )
                repair_provider_wait_ms = round(
                    (time.monotonic() - repair_started) * 1000,
                    1,
                )
                provider_wait_ms = round(
                    provider_wait_ms + repair_provider_wait_ms,
                    1,
                )
                repaired_content = str(getattr(repaired, "content", "") or "").strip()
                if repaired_content:
                    _write_text(stage_dir / "raw_output_repair.txt", repaired_content)
                repair_usage = getattr(repaired, "usage", {})
                if not isinstance(repair_usage, dict):
                    repair_usage = {}
                output_tokens += int(
                    repair_usage.get("completion_tokens")
                    or (
                        BaseLLMClient.estimate_tokens(repaired_content)
                        if repaired_content
                        else 0
                    )
                )
                repair_finish_reason = str(
                    getattr(repaired, "finish_reason", "")
                    or current_finish_reason.get()
                    or (
                        "length"
                        if bool(getattr(repaired, "truncated", False))
                        else "stop"
                    )
                )
                if (
                    bool(getattr(repaired, "truncated", False))
                    or repair_finish_reason == "length"
                    or not repaired_content
                ):
                    raise ValueError("repair_output_invalid")
                remaining_errors = _source_analysis_format_errors(repaired_content)
                if remaining_errors:
                    raise ValueError(
                        "repair_output_invalid: " + "; ".join(remaining_errors[:3])
                    )
                raw_content = repaired_content
                finish_reason = f"repair_{repair_finish_reason}"
            grounding_errors = _source_analysis_grounding_errors(
                raw_content,
                pack=pack,
            )
            if grounding_errors:
                degraded = True
                degradation_reason = (
                    "model_output_unverified: " + "; ".join(grounding_errors[:5])
                )
                finish_reason = "grounding_rejected"
            else:
                summary = _truncate_model_enhancement(
                    raw_content,
                    int(effective["max_chinese_characters"]),
                )
                deterministic = output_path.read_text(encoding="utf-8")
                _write_text(
                    output_path,
                    deterministic.rstrip()
                    + "\n\n## 模型排序、归纳与缺口标记\n\n"
                    + summary.rstrip()
                    + "\n",
                )
        except asyncio.TimeoutError:
            elapsed_provider_ms = round(
                (time.monotonic() - provider_started) * 1000,
                1,
            )
            provider_wait_ms = max(provider_wait_ms, elapsed_provider_ms)
            degraded = True
            degradation_reason = (
                "repair_timeout" if provider_phase == "repair" else "provider_timeout"
            )
            finish_reason = degradation_reason
        except StagedExecutionCancelled:
            raise
        except Exception as exc:
            provider_wait_ms = round((time.monotonic() - provider_started) * 1000, 1)
            degraded = True
            error = str(exc) or exc.__class__.__name__
            degradation_reason = (
                f"repair_failed: {error}" if provider_phase == "repair" else error
            )
            if degradation_reason == "provider_output_truncated":
                finish_reason = "length"
            elif provider_phase == "repair":
                finish_reason = "repair_error"
            elif finish_reason == "not_called":
                finish_reason = "transport_error"
    duration_ms = round((time.monotonic() - started) * 1000, 1)
    result = {
        "stage_id": "source_analysis",
        "status": "completed",
        "artifact": "source_analysis.md",
        "attempts": attempt_count,
        "attempt_count": attempt_count,
        "prompt_characters_before_compaction": len(legacy_prompt),
        "prompt_characters": len(prompt),
        "prompt_estimated_tokens": prompt_tokens,
        "provider_wait_ms": provider_wait_ms,
        "output_tokens": output_tokens,
        "finish_reason": finish_reason,
        "full_retry_performed": False,
        "repair_attempt_count": repair_attempt_count,
        "repair_provider_wait_ms": repair_provider_wait_ms,
        "degraded": degraded,
        "degradation_reason": degradation_reason,
        "cache_status": "miss" if cache_root is not None else "disabled",
        "cache_key": cache_key,
        "context_prepare_ms": context_prepare_ms,
        "context_budget_seconds": effective["context_timeout_seconds"],
        "provider_budget_seconds": effective["timeout_seconds"],
        "total_budget_seconds": effective["total_timeout_seconds"],
        "duration_ms": duration_ms,
        "size_bytes": output_path.stat().st_size,
        "model": model,
        "quality_gate": quality_gate,
    }
    _write_json(stage_dir / "stage_result.json", result)
    if degraded:
        await _emit_progress(
            on_progress,
            {
                "event_type": "stage_degraded",
                "stage_id": "source_analysis",
                "status": "completed",
                "artifact": "source_analysis.md",
                "reason": degradation_reason,
                "user_message": (
                    "源码分析模型增强未完成，已使用 SHA256 校验的确定性证据包继续运行。"
                ),
            },
        )
    elif cache_root is not None and quality_gate.get("status") == "passed":
        _store_source_analysis_cache(
            cache_root=cache_root,
            cache_key=cache_key,
            artifact_dir=artifact_dir,
            pack=pack,
        )
    return {**result, "output_path": str(output_path)}


def _truncate_model_enhancement(content: str, limit: int) -> str:
    stripped = content.strip()
    if len(stripped) <= limit:
        return stripped
    cutoff = max(1, int(limit))
    prefix = stripped[:cutoff]
    minimum_boundary = max(1, int(cutoff * 0.6))
    boundary = prefix.rfind("\n\n", minimum_boundary)
    if boundary < 0:
        boundary = prefix.rfind("\n", minimum_boundary)
    if boundary < 0:
        boundary = prefix.rfind("。", minimum_boundary)
        if boundary >= 0:
            boundary += 1
    if boundary < 0:
        boundary = cutoff
    return (
        prefix[:boundary].rstrip()
        + "\n\n> 模型增强内容已按阶段预算截断；确定性证据不受影响。"
    )


def _source_analysis_format_errors(content: str) -> list[str]:
    errors: list[str] = []
    stripped = content.strip()
    if stripped.count("```") % 2:
        errors.append("未闭合 Markdown 代码围栏")
    if stripped.startswith(("{", "[")):
        errors.append("返回了 JSON，而不是 Markdown 正文")
    return errors


def _source_analysis_grounding_errors(
    content: str,
    *,
    pack: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    stripped = content.strip()
    cards = [
        card
        for card in pack.get("evidence_cards") or []
        if isinstance(card, dict)
    ]
    allowed_ids = {
        str(card.get("evidence_id") or "")
        for card in cards
    }
    referenced_ids = set(re.findall(r"\bSRC-\d+\b", stripped))
    unknown_ids = sorted(referenced_ids - allowed_ids)
    if unknown_ids:
        errors.append("引用了未知证据 ID：" + ", ".join(unknown_ids[:5]))
    allowed_paths = {str(card.get("file_path") or ""): card for card in cards}
    path_pattern = re.compile(
        r"(?P<path>(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+)"
        r"(?::(?P<start>\d+)(?:-(?P<end>\d+))?)?"
    )
    for match in path_pattern.finditer(stripped):
        path = match.group("path")
        card = allowed_paths.get(path)
        if card is None:
            errors.append(f"引用了未验证文件：{path}")
            continue
        if match.group("start"):
            cited_start = int(match.group("start"))
            cited_end = int(match.group("end") or cited_start)
            verified_start = int(card.get("start_line") or 0)
            verified_end = int(card.get("end_line") or 0)
            if cited_start < verified_start or cited_end > verified_end:
                errors.append(
                    f"引用行号超出证据范围：{path}:{cited_start}-{cited_end}"
                )
    allowed_symbols = {
        str(symbol)
        for card in cards
        for symbol in card.get("symbols") or []
        if str(symbol)
    }
    referenced_calls = set(
        re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)", stripped)
    )
    unknown_symbols = sorted(referenced_calls - allowed_symbols)
    if unknown_symbols:
        errors.append("引用了未验证函数：" + ", ".join(unknown_symbols[:5]))
    return errors


def _source_analysis_repair_prompt(
    *,
    raw_content: str,
    validation_errors: list[str],
    pack: dict[str, Any],
) -> str:
    required_evidence = [
        {
            "evidence_id": str(card.get("evidence_id") or ""),
            "file_path": str(card.get("file_path") or ""),
            "start_line": int(card.get("start_line") or 0),
            "end_line": int(card.get("end_line") or 0),
            "symbols": [str(value) for value in card.get("symbols") or []][:6],
        }
        for card in pack.get("evidence_cards") or []
        if isinstance(card, dict)
    ]
    return "\n".join(
        [
            "TASK: Repair source-analysis Markdown formatting only.",
            "Do not discover, add, or infer source facts.",
            "Return only complete Markdown with the same supported meaning.",
            "",
            "VALIDATION_ERRORS:",
            json.dumps(validation_errors, ensure_ascii=False),
            "",
            "MUST_PRESERVE_VERIFIED_EVIDENCE:",
            json.dumps(required_evidence, ensure_ascii=False, separators=(",", ":")),
            "",
            "FIRST_OUTPUT:",
            raw_content,
        ]
    )


def _source_analysis_limits(overrides: dict[str, Any] | None) -> dict[str, Any]:
    values: dict[str, Any] = {
        "max_tokens": settings.source_analysis_max_tokens,
        "max_chinese_characters": settings.source_analysis_max_chinese_characters,
        "max_evidence_anchors": settings.source_analysis_max_evidence_anchors,
        "max_files": settings.source_analysis_max_files,
        "excerpt_chars": settings.source_analysis_excerpt_chars,
        "context_timeout_seconds": settings.source_analysis_context_timeout_seconds,
        "timeout_seconds": settings.source_analysis_timeout_seconds,
        "repair_max_tokens": settings.source_analysis_repair_max_tokens,
        "repair_timeout_seconds": settings.source_analysis_repair_timeout_seconds,
        "total_timeout_seconds": settings.source_analysis_total_timeout_seconds,
    }
    if isinstance(overrides, dict):
        for key in values:
            if key in overrides and overrides[key] is not None:
                values[key] = overrides[key]
    return values


def _source_analysis_prompt(
    *, stage: dict[str, Any], compact_context: dict[str, Any]
) -> str:
    output_limits = (
        stage.get("output_limits")
        if isinstance(stage.get("output_limits"), dict)
        else {}
    )
    max_characters = int(
        output_limits.get("max_chinese_characters")
        or settings.source_analysis_max_chinese_characters
    )
    max_anchors = int(
        output_limits.get("max_evidence_anchors")
        or settings.source_analysis_max_evidence_anchors
    )
    evidence_context = dict(compact_context)
    analysis_target = str(evidence_context.pop("analysis_target", ""))
    return "\n".join(
        [
            "STAGE_ID: source_analysis",
            "OUTPUT_ARTIFACT: source_analysis.md",
            "ROLE: verified evidence ranking and gap analysis",
            "",
            "ANALYSIS_TARGET:",
            analysis_target,
            "",
            "SOURCE_ANALYSIS_CONTEXT:",
            json.dumps(evidence_context, ensure_ascii=False, indent=2, sort_keys=True),
            "",
            "RULES:",
            "- 只允许对已提供并经过 SHA256 校验的证据做排序、事实归纳和缺口标记。",
            "- 禁止重新发现文件、猜测未提供的源码、生成 SFMEA、黑盒用例或后续阶段内容。",
            "- 每项判断必须引用 context 中的 file_path、start_line/end_line 和 symbol。",
            f"- 正文最多 {max_characters} 个中文字符；这是支持索引，不是最终报告。",
            f"- 最多 {max_anchors} 个证据锚点。",
            "- 只返回 Markdown 正文，不返回 JSON、代码围栏、终端说明或 artifact 容器。",
        ]
    )


def _decode_source_context(context_prompt: str) -> dict[str, Any]:
    try:
        decoded = json.loads(context_prompt)
    except (TypeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _source_analysis_cache_root(cache_dir: Path | None) -> Path | None:
    return Path(cache_dir) if cache_dir is not None else None


def _source_analysis_cache_key(
    *, plan: dict[str, Any], context: dict[str, Any]
) -> str:
    payload = {
        "repo_commit_sha": str(context.get("repo_revision") or ""),
        "analysis_target": str(context.get("analysis_target") or ""),
        "file_sha256": [
            [str(item.get("file_path") or ""), str(item.get("sha256") or "")]
            for item in context.get("files") or []
            if isinstance(item, dict)
        ],
        "input_material_sha256": [
            str(item.get("sha256") or "")
            for item in context.get("input_materials") or []
            if isinstance(item, dict) and str(item.get("sha256") or "")
        ],
        "workflow_version": str(plan.get("workflow_version") or ""),
        "source_analysis_schema_version": settings.source_analysis_schema_version,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _restore_source_analysis_cache(
    *,
    cache_root: Path,
    cache_key: str,
    artifact_dir: Path,
    expected_pack: dict[str, Any],
) -> bool:
    entry = cache_root / cache_key
    metadata_path = entry / "cache_metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        cards = json.loads((entry / "evidence_cards.json").read_text(encoding="utf-8"))
        scope = json.loads((entry / "source_scope.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    expected_evidence = [
        (
            str(card.get("file_path") or ""),
            str(card.get("sha256") or ""),
            int(card.get("start_line") or 0),
            int(card.get("end_line") or 0),
        )
        for card in expected_pack.get("evidence_cards") or []
        if isinstance(card, dict)
    ]
    cached_evidence = [
        (
            str(card.get("file_path") or ""),
            str(card.get("sha256") or ""),
            int(card.get("start_line") or 0),
            int(card.get("end_line") or 0),
        )
        for card in cards
        if isinstance(card, dict)
    ]
    if (
        metadata.get("version") != _SOURCE_ANALYSIS_CACHE_VERSION
        or metadata.get("cache_key") != cache_key
        or metadata.get("quality_status") != "passed"
        or cards != (expected_pack.get("evidence_cards") or [])
        or scope != (expected_pack.get("source_scope") or {})
        or not isinstance(cards, list)
        or not isinstance(scope, dict)
        or cached_evidence != expected_evidence
        or not all(
            isinstance(card, dict)
            and card.get("file_path")
            and card.get("sha256")
            and int(card.get("start_line") or 0) > 0
            for card in cards
        )
    ):
        return False
    artifact_sha256 = metadata.get("artifact_sha256")
    if not isinstance(artifact_sha256, dict):
        return False
    for name in ("source_analysis.md", "source_scope.json", "evidence_cards.json"):
        source = entry / name
        if (
            not source.is_file()
            or str(artifact_sha256.get(name) or "") != _sha256_path(source)
        ):
            return False
        target = artifact_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name != "source_analysis.md" and target.exists():
            continue
        shutil.copy2(source, target)
    return True


def _store_source_analysis_cache(
    *,
    cache_root: Path,
    cache_key: str,
    artifact_dir: Path,
    pack: dict[str, Any],
) -> None:
    entry = cache_root / cache_key
    if entry.is_dir():
        return
    temporary = cache_root / f".{cache_key}.{time.time_ns()}.tmp"
    try:
        temporary.mkdir(parents=True, exist_ok=False)
        for name in ("source_analysis.md", "source_scope.json", "evidence_cards.json"):
            shutil.copy2(artifact_dir / name, temporary / name)
        _write_json(
            temporary / "cache_metadata.json",
            {
                "version": _SOURCE_ANALYSIS_CACHE_VERSION,
                "cache_key": cache_key,
                "quality_status": str((pack.get("quality_gate") or {}).get("status") or ""),
                "artifact_sha256": {
                    name: _sha256_path(temporary / name)
                    for name in (
                        "source_analysis.md",
                        "source_scope.json",
                        "evidence_cards.json",
                    )
                },
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        cache_root.mkdir(parents=True, exist_ok=True)
        try:
            temporary.rename(entry)
        except FileExistsError:
            shutil.rmtree(temporary, ignore_errors=True)
    except OSError:
        shutil.rmtree(temporary, ignore_errors=True)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def _callback_true(callback: CancellationCallback | None) -> bool:
    if callback is None:
        return False
    result = callback()
    if inspect.isawaitable(result):
        result = await result
    return bool(result)


def _stage_prompt(
    *,
    plan: dict[str, Any],
    stage: dict[str, Any],
    context_prompt: str,
    completed: dict[str, Path],
) -> str:
    artifact = str(stage.get("artifact") or "")
    previous_sections: list[str] = []
    dependencies = {
        str(item).strip()
        for item in stage.get("depends_on") or []
        if str(item).strip()
    }
    for stage_id, path in completed.items():
        if stage_id not in dependencies:
            continue
        if not path or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        previous_sections.extend(
            [
                f"--- {path.name} (accepted previous artifact) ---",
                text[:12000],
            ]
        )
    output_contract = stage.get("output_contract") if isinstance(stage.get("output_contract"), dict) else {}
    output_rule = (
        "Return only valid JSON for this file, without Markdown fences."
        if artifact.endswith(".json")
        else "Return the complete Markdown file body, without terminal chatter."
    )
    output_limits = (
        stage.get("output_limits")
        if isinstance(stage.get("output_limits"), dict)
        else {}
    )
    limit_rules: list[str] = []
    if output_limits.get("max_chinese_characters"):
        limit_rules.append(
            f"- 正文最多 {int(output_limits['max_chinese_characters'])} 个中文字符；这是支持索引，不是最终报告。"
        )
    if output_limits.get("max_evidence_anchors"):
        limit_rules.append(
            f"- 最多 {int(output_limits['max_evidence_anchors'])} 个证据锚点，每个锚点只写文件、符号/行号、事实和关联测试。"
        )
    if output_limits.get("max_items"):
        limit_rules.append(
            f"- JSON 数组或条目列表最多 {int(output_limits['max_items'])} 项，优先保留高风险和高证据强度内容。"
        )
    if output_limits.get("max_field_characters"):
        limit_rules.append(
            f"- 每个叙述字段最多 {int(output_limits['max_field_characters'])} 个字符，数组步骤也使用短句。"
        )
    other_artifacts = [
        str(item)
        for item in plan.get("required_outputs") or []
        if str(item) and str(item) != artifact
    ]
    return "\n".join(
        [
            f"STAGE_ID: {stage.get('id')}",
            f"OUTPUT_ARTIFACT: {artifact}",
            f"PURPOSE: {stage.get('purpose')}",
            "ORIGINAL_USER_REQUEST:",
            str(plan.get("original_user_request") or ""),
            "",
            "SOURCE_AND_INPUT_CONTEXT:",
            context_prompt,
            "",
            "OUTPUT_CONTRACT:",
            json.dumps(output_contract, ensure_ascii=False, indent=2),
            "",
            "CURRENT_STAGE_ONLY:",
            f"- 当前只生成 {artifact}。原始请求中的其他交付件由后续独立阶段处理。",
            "- 不要在当前响应中生成其他阶段、artifact 容器、总报告或用户未要求的附加文件。",
            f"- 本阶段禁止输出：{', '.join(other_artifacts) if other_artifacts else '(none)'}",
            *_stage_format_rules(str(stage.get("id") or ""), artifact),
            "",
            "RULES:",
            "- Read and preserve the complete original user request.",
            "- Use only source/test evidence supplied here or in accepted previous artifacts.",
            "- Mark unverified design proposals explicitly; do not claim execution.",
            f"- {output_rule}",
            *limit_rules,
            "",
            "PRIOR_ACCEPTED_ARTIFACTS:",
            *(previous_sections or ["(none)"]),
        ]
    )


def _render_stage_artifact(content: str, artifact: str) -> Any:
    if not artifact.endswith(".json"):
        if not content:
            raise RuntimeError(f"阶段交付文件 {artifact} 为空")
        return content.rstrip() + "\n"
    candidates = [content]
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", content, re.IGNORECASE)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise RuntimeError(f"阶段交付文件 {artifact} 不是有效 JSON")


async def _emit_progress(callback: ProgressCallback | None, payload: dict[str, Any]) -> None:
    if callback is None:
        return
    result = callback(payload)
    if inspect.isawaitable(result):
        await result


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _stage_purpose(stage_id: str) -> str:
    return {
        "business_flow": "基于源码证据梳理外部触发、主流程、异常恢复和观测点",
        "sfmea": "基于已验收流程生成可追踪、可评分、可转测试的 SFMEA",
        "black_box_cases": "生成只使用外部输入与观测点的八维黑盒测试用例",
        "test_strategy": "形成范围、风险、资源、优先级和准入准出策略",
        "test_design": "聚合证据、流程、风险和用例形成可执行测试设计",
        "coverage_gap": "识别入口、覆盖缺口和补充测试建议",
        "risk_review": "复核高风险、证据缺口和未验证建议",
        "execution_checklist": "形成环境、数据、步骤、观测和复跑检查清单",
    }.get(stage_id, "按声明契约生成独立交付文件")


def _stage_execution_limits(stage_id: str) -> dict[str, Any]:
    base_stage_id = stage_id.split("__", 1)[0]
    limits = {
        "source_scope": (2600, {"max_items": 24}),
        "evidence_cards": (4000, {"max_items": 24}),
        "business_flow": (4000, {"max_chinese_characters": 6000}),
        "sfmea": (5500, {"max_items": 10, "max_field_characters": 180}),
        "black_box_cases": (6000, {"max_items": 12, "max_field_characters": 180}),
    }.get(base_stage_id)
    if limits is None:
        return {}
    max_tokens, output_limits = limits
    return {"max_tokens": max_tokens, "output_limits": output_limits}


def _stage_format_rules(stage_id: str, artifact: str) -> list[str]:
    base_stage_id = stage_id.split("__", 1)[0]
    rules = {
        "source_scope": "- 只写范围、真实文件和入口点；不要写流程、SFMEA 或测试用例。",
        "evidence_cards": (
            "- 每项只写可核验的文件、符号/行号、事实和证据来源；不要推演测试设计；"
            "每张卡至少提供一个真实的 file-local symbol；每个 symbol 必须逐字出现在对应 "
            "file_path，跨文件定义必须填写真正的定义文件。"
        ),
        "business_flow": (
            "- 只写流程和证据引用，不要写 SFMEA 表或测试用例；"
            "必须使用四个独立二级标题：## 外部触发、## 流程步骤、## 异常分支、## 观测点；"
            "至少引用一个真实源码路径和一个真实测试路径。"
        ),
        "sfmea": (
            "- 只返回 8-10 条最高风险 SFMEA JSON 数组；每条必须有评分依据、mitigation 和源码/测试映射；"
            "每条 mitigation 必须同时写明具体整改和可执行的测试或监控验证动作。"
        ),
        "black_box_cases": "- 只返回 8-12 条黑盒用例 JSON 数组；八个必需维度各至少一条，步骤只能使用外部操作和可观测结果。",
    }
    rule = rules.get(base_stage_id)
    if rule:
        return [rule]
    if artifact.endswith(".json"):
        return ["- 只返回当前 JSON 文件的顶层值，不要包裹 summary/artifacts/path/content。"]
    return []
