from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.config import settings
from app.llm.base import BaseLLMClient, current_finish_reason
from app.services.ai_thread_artifacts import _validate_schema, materialize_ai_thread_manifest
from app.services.flow_evidence import (
    FLOW_EVIDENCE_VERSION,
    build_business_flow_context,
    build_flow_evidence_pack,
    build_flow_outline,
    render_business_flow_markdown,
    stable_payload_sha256,
)
from app.services.regular_stage_governance import (
    StageExecutionPolicy,
    cache_root as regular_stage_cache_root,
    regular_stage_cache_key,
    restore_regular_stage_cache,
    stage_execution_policy,
    store_regular_stage_cache,
)
from app.services.source_driven_test_design import (
    MINDMAP_ARTIFACTS,
    SOURCE_DRIVEN_V2_ARTIFACTS,
    build_source_driven_test_design,
    build_test_design_mindmap,
    render_test_design_mindmap_html,
    render_test_design_mindmap_svg,
    verify_technical_claims,
)
from app.services.workflow_presets import BLACK_BOX_CASES_SCHEMA, SFMEA_SCHEMA


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
CancellationCallback = Callable[[], Awaitable[bool] | bool]
_CANCELLATION_POLL_INTERVAL = 0.1
_SOURCE_EVIDENCE_PACK_VERSION = "source-evidence-pack-v1"
_SOURCE_ANALYSIS_CACHE_VERSION = "source-analysis-cache-v3"
_FLOW_DETERMINISTIC_STAGES = {"flow_evidence_pack", "flow_outline"}
_SOURCE_DRIVEN_STAGE_GROUPS = {
    "breadth_inventory": {
        "anchor": "entrypoints.json",
        "artifacts": (
            "entrypoints.json", "flows.json", "states.json", "resources.json",
            "model_applicability.json",
        ),
        "depends_on": ("flow_outline",),
    },
    "developer_explanation": {
        "anchor": "flow_cards.json",
        "artifacts": (
            "flow_cards.json", "developer_explanation_coverage.json",
            "branch_disposition.json", "state_transition_disposition.json",
            "resource_lifecycle_disposition.json", "error_propagation_chains.json",
            "evidence_consumption_ledger.json",
        ),
        "depends_on": ("breadth_inventory",),
    },
    "scenario_expansion": {
        "anchor": "scenario_candidates.json",
        "artifacts": ("scenario_candidates.json",),
        "depends_on": ("developer_explanation",),
    },
    "test_design_governance": {
        "anchor": "traceability_matrix.json",
        "artifacts": (
            "risk_register.json", "blackbox_control_observation.json",
            "test_basis.json", "test_scenarios.json", "test_flows.json",
            "traceability_matrix.json",
        ),
        "depends_on": ("developer_explanation", "sfmea", "black_box_cases"),
    },
    "coverage_judge": {
        "anchor": "judge_report.json",
        "artifacts": ("judge_report.json",),
        "depends_on": ("test_design_governance",),
    },
    "test_design_mindmap": {
        "anchor": MINDMAP_ARTIFACTS[0],
        "artifacts": MINDMAP_ARTIFACTS,
        "depends_on": ("coverage_judge",),
    },
}
_SOURCE_DRIVEN_STAGE_BY_ARTIFACT = {
    artifact: stage_id
    for stage_id, spec in _SOURCE_DRIVEN_STAGE_GROUPS.items()
    for artifact in spec["artifacts"]
}
_SOURCE_DRIVEN_DETERMINISTIC_STAGES = frozenset(_SOURCE_DRIVEN_STAGE_GROUPS)
_PROVIDER_CAPACITY_LOCK = threading.Lock()
_PROVIDER_CAPACITY: tuple[int, "_ProcessProviderCapacity"] | None = None


class _ProcessProviderCapacity:
    """Process-wide capacity shared by the per-task asyncio event loops."""

    def __init__(self, limit: int) -> None:
        self._semaphore = threading.BoundedSemaphore(limit)

    async def acquire(
        self,
        timeout_seconds: float,
        *,
        is_cancelled: CancellationCallback | None = None,
    ) -> bool:
        deadline = time.monotonic() + max(0.001, float(timeout_seconds))
        while True:
            if await _callback_true(is_cancelled):
                raise StagedExecutionCancelled("任务已取消，已停止等待 Provider 容量")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            waiter = asyncio.create_task(
                asyncio.to_thread(
                    self._semaphore.acquire,
                    True,
                    min(0.05, remaining),
                )
            )
            try:
                acquired = bool(await asyncio.shield(waiter))
            except asyncio.CancelledError:
                acquired = bool(await asyncio.shield(waiter))
                if acquired:
                    self._semaphore.release()
                raise
            if acquired:
                return True

    def release(self) -> None:
        self._semaphore.release()

    def release_after(self, tasks: list[asyncio.Task[Any]]) -> None:
        pending = {task for task in tasks if not task.done()}
        if not pending:
            self.release()
            return
        lock = threading.Lock()

        def completed(task: asyncio.Task[Any]) -> None:
            nonlocal pending
            with lock:
                pending.discard(task)
                if not pending:
                    self.release()

        for task in pending:
            task.add_done_callback(completed)


def _shared_provider_capacity() -> _ProcessProviderCapacity:
    global _PROVIDER_CAPACITY
    limit = max(1, int(settings.llm_max_concurrency))
    with _PROVIDER_CAPACITY_LOCK:
        current = _PROVIDER_CAPACITY
        if current is None or current[0] != limit:
            current = (limit, _ProcessProviderCapacity(limit))
            _PROVIDER_CAPACITY = current
        return current[1]


def build_source_analysis_context(
    *,
    plan: dict[str, Any],
    staged_context: dict[str, Any],
    max_files: int | None = None,
    excerpt_chars: int | None = None,
    max_evidence_anchors: int | None = None,
) -> dict[str, Any]:
    """Project the full execution context into the source-analysis contract."""
    return _project_source_analysis_context(
        plan=plan,
        staged_context=staged_context,
        max_files=max_files,
        excerpt_chars=excerpt_chars,
        max_evidence_anchors=max_evidence_anchors,
    )


def _project_source_analysis_context(
    *,
    plan: dict[str, Any],
    staged_context: dict[str, Any],
    max_files: int | None = None,
    excerpt_chars: int | None = None,
    max_evidence_anchors: int | None = None,
) -> dict[str, Any]:
    materials = _source_input_material_summaries(staged_context)
    gitnexus_summary, cgc_summary = _source_tool_summaries(staged_context)
    return _assemble_source_analysis_context(
        plan=plan,
        staged_context=staged_context,
        max_files=max_files,
        excerpt_chars=excerpt_chars,
        max_evidence_anchors=max_evidence_anchors,
        materials=materials,
        gitnexus_summary=gitnexus_summary,
        cgc_summary=cgc_summary,
    )


def _project_source_analysis_context_from_memory(
    *,
    plan: dict[str, Any],
    staged_context: dict[str, Any],
    max_files: int | None = None,
    excerpt_chars: int | None = None,
    max_evidence_anchors: int | None = None,
) -> dict[str, Any]:
    """Budget fallback that performs no filesystem reads or full MCP serialization."""
    return _assemble_source_analysis_context(
        plan=plan,
        staged_context=staged_context,
        max_files=max_files,
        excerpt_chars=excerpt_chars,
        max_evidence_anchors=max_evidence_anchors,
        materials=_source_input_material_summaries(
            staged_context,
            read_parsed_text=False,
        ),
        gitnexus_summary="",
        cgc_summary="",
    )


def _assemble_source_analysis_context(
    *,
    plan: dict[str, Any],
    staged_context: dict[str, Any],
    max_files: int | None,
    excerpt_chars: int | None,
    max_evidence_anchors: int | None,
    materials: list[dict[str, str]],
    gitnexus_summary: str,
    cgc_summary: str,
) -> dict[str, Any]:
    min_source_files, min_test_files = _source_evidence_minimums(plan)
    required_file_count = min_source_files + min_test_files
    file_limit = max(
        1,
        int(max_files or settings.source_analysis_max_files),
        required_file_count,
    )
    excerpt_limit = max(200, int(excerpt_chars or settings.source_analysis_excerpt_chars))
    anchor_limit = max(
        1,
        int(max_evidence_anchors or settings.source_analysis_max_evidence_anchors),
        required_file_count,
    )
    source_context = (
        staged_context.get("source_context")
        if isinstance(staged_context.get("source_context"), dict)
        else staged_context
    )
    files: list[dict[str, Any]] = []
    evidence_limit = min(file_limit, anchor_limit)
    selected_source_items = _select_bounded_source_context_files(
        source_context.get("files") or [],
        limit=evidence_limit,
        min_source_files=min_source_files,
        min_test_files=min_test_files,
    )
    for item in selected_source_items:
        path = str(item.get("file_path") or "").strip()
        evidence_id = f"SRC-{len(files) + 1:02d}"
        files.append(
            {
                "evidence_id": evidence_id,
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


def _select_bounded_source_context_files(
    values: Any,
    *,
    limit: int,
    min_source_files: int = 1,
    min_test_files: int = 1,
) -> list[dict[str, Any]]:
    candidates = [
        item
        for item in values
        if isinstance(item, dict) and str(item.get("file_path") or "").strip()
    ]
    selected = list(candidates[: max(0, limit)])
    if limit < 2 or not selected:
        return selected
    selected_paths = {str(item.get("file_path") or "") for item in selected}
    desired = {
        "source": min(max(0, int(min_source_files)), limit),
        "test": min(max(0, int(min_test_files)), limit),
    }
    for required_class in ("source", "test"):
        while sum(
            str(
                item.get("classification")
                or _source_file_classification(str(item.get("file_path") or ""))
            ) == required_class
            for item in selected
        ) < desired[required_class]:
            replacement = next(
                (
                    item
                    for item in candidates
                    if str(item.get("file_path") or "") not in selected_paths
                    and str(
                        item.get("classification")
                        or _source_file_classification(str(item.get("file_path") or ""))
                    ) == required_class
                ),
                None,
            )
            if replacement is None:
                break
            replace_index = next(
                (
                    index
                    for index in range(len(selected) - 1, -1, -1)
                    if str(
                        selected[index].get("classification")
                        or _source_file_classification(
                            str(selected[index].get("file_path") or "")
                        )
                    ) != required_class
                    and sum(
                        str(
                            item.get("classification")
                            or _source_file_classification(str(item.get("file_path") or ""))
                        )
                        == str(
                            selected[index].get("classification")
                            or _source_file_classification(
                                str(selected[index].get("file_path") or "")
                            )
                        )
                        for item in selected
                    ) > desired.get(
                        str(
                            selected[index].get("classification")
                            or _source_file_classification(
                                str(selected[index].get("file_path") or "")
                            )
                        ),
                        0,
                    )
                ),
                -1,
            )
            if replace_index < 0:
                break
            selected_paths.discard(str(selected[replace_index].get("file_path") or ""))
            selected[replace_index] = replacement
            selected_paths.add(str(replacement.get("file_path") or ""))
    return selected


def _source_evidence_minimums(plan: dict[str, Any]) -> tuple[int, int]:
    min_source_files = 1
    min_test_files = 1
    for stage in plan.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        contract = stage.get("output_contract")
        if not isinstance(contract, dict):
            continue
        min_source_files = max(
            min_source_files,
            int(contract.get("min_source_paths") or 0),
        )
        min_test_files = max(
            min_test_files,
            int(contract.get("min_test_paths") or 0),
        )
    return min_source_files, min_test_files


def build_source_evidence_pack(context: dict[str, Any]) -> dict[str, Any]:
    """Create model-independent source scope and evidence cards."""
    cards: list[dict[str, Any]] = []
    for index, item in enumerate(context.get("files") or [], 1):
        if not isinstance(item, dict):
            continue
        cards.append(
            {
                "evidence_id": str(item.get("evidence_id") or f"SRC-{index:02d}"),
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
    verified_literals = _verified_source_literals(cards)
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
        "verified_literals": verified_literals,
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


def _verified_source_literals(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    literals: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    pattern = re.compile(
        r"^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s+([^\s/]+)",
        flags=re.MULTILINE,
    )
    for card in cards:
        excerpt = str(card.get("excerpt") or "")
        excerpt_start = int(card.get("start_line") or 0)
        for match in pattern.finditer(excerpt):
            name = match.group(1)
            value = match.group(2).rstrip(";,)")
            key = (name, value, str(card.get("evidence_id") or ""))
            if key in seen:
                continue
            seen.add(key)
            line_offset = excerpt[: match.start()].count("\n")
            literals.append({
                "name": name,
                "value": value,
                "evidence_id": str(card.get("evidence_id") or ""),
                "file_path": str(card.get("file_path") or ""),
                "line": excerpt_start + line_offset if excerpt_start else 0,
            })
    return literals


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


def _source_input_material_summaries(
    staged_context: dict[str, Any],
    *,
    read_parsed_text: bool = True,
) -> list[dict[str, str]]:
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
        if (
            read_parsed_text
            and parsed_text_path
            and (not summary or summary == item.get("material_role"))
        ):
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


def _consume_detached_task(task: asyncio.Task[Any]) -> None:
    with suppress(BaseException):
        task.result()


async def _cancel_task_bounded(task: asyncio.Task[Any]) -> bool:
    if task.done():
        _consume_detached_task(task)
        return True
    task.cancel()
    done, _ = await asyncio.wait(
        {task},
        timeout=float(settings.regular_stage_cancel_grace_seconds),
    )
    if task in done:
        _consume_detached_task(task)
        return True
    task.add_done_callback(_consume_detached_task)
    return False


async def _complete_with_cancellation(
    *,
    llm: Any,
    prompt: str,
    max_tokens: int,
    is_cancelled: CancellationCallback | None,
    timeout_seconds: float | None = None,
    single_attempt: bool = False,
    on_detached_task: Callable[[asyncio.Task[Any]], None] | None = None,
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
    detached_reported = False

    async def cancel_provider() -> None:
        nonlocal detached_reported
        terminated = await _cancel_task_bounded(provider_task)
        if not terminated and on_detached_task is not None and not detached_reported:
            detached_reported = True
            on_detached_task(provider_task)

    try:
        while True:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                await cancel_provider()
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
                await cancel_provider()
                raise StagedExecutionCancelled("任务已取消，已停止当前模型调用和后续阶段")
    finally:
        if not provider_task.done():
            await cancel_provider()


_STAGE_BY_ARTIFACT = {
    "source_scope.json": ("source_scope", ["source_analysis"]),
    "evidence_cards.json": ("evidence_cards", ["source_analysis"]),
    "flow_evidence_pack.json": ("flow_evidence_pack", ["source_analysis"]),
    "flow_outline.json": ("flow_outline", ["flow_evidence_pack"]),
    "flow_map.md": ("business_flow", ["flow_outline"]),
    "project_structure.md": ("project_structure", ["source_analysis"]),
    "source_reading_plan.md": ("source_reading_plan", ["source_analysis"]),
    "module_map.md": ("module_map", ["source_analysis"]),
    "tester_code_understanding.md": ("tester_code_understanding", ["source_analysis"]),
    "business_flow.md": ("business_flow", ["flow_outline"]),
    "sfmea.json": ("sfmea", ["source_analysis", "flow_outline"]),
    "black_box_cases.json": (
        "black_box_cases",
        ["source_analysis", "flow_outline", "sfmea"],
    ),
    "black_box_cases.md": (
        "black_box_cases",
        ["source_analysis", "flow_outline", "sfmea"],
    ),
    "test_strategy.md": ("test_strategy", ["source_analysis", "flow_outline"]),
    "test_design.md": (
        "test_design",
        ["source_analysis", "flow_outline", "sfmea", "black_box_cases"],
    ),
    "coverage_gap_report.md": ("coverage_gap", ["source_analysis"]),
    "risk_review.md": ("risk_review", ["source_analysis", "sfmea"]),
    "execution_checklist.md": (
        "execution_checklist",
        ["flow_outline", "black_box_cases"],
    ),
}
for _stage_id, _stage_spec in _SOURCE_DRIVEN_STAGE_GROUPS.items():
    _STAGE_BY_ARTIFACT[str(_stage_spec["anchor"])] = (
        _stage_id,
        list(_stage_spec["depends_on"]),
    )

_CANONICAL_STAGE_ORDER = (
    "source_analysis",
    "source_scope",
    "evidence_cards",
    "flow_evidence_pack",
    "flow_outline",
    "breadth_inventory",
    "developer_explanation",
    "scenario_expansion",
    "project_structure",
    "source_reading_plan",
    "module_map",
    "tester_code_understanding",
    "business_flow",
    "sfmea",
    "black_box_cases",
    "test_design_governance",
    "coverage_judge",
    "test_design_mindmap",
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
    "flow_evidence_pack": "flow_evidence_pack.json",
    "flow_outline": "flow_outline.json",
    "business_flow": "business_flow.md",
    "sfmea": "sfmea.json",
    "black_box_cases": "black_box_cases.json",
    **{
        stage_id: str(spec["anchor"])
        for stage_id, spec in _SOURCE_DRIVEN_STAGE_GROUPS.items()
    },
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
    combined_report_contract = next(
        (
            dict(artifact_contract.get(artifact) or {})
            for artifact in outputs
            if isinstance(artifact_contract.get(artifact), dict)
            and artifact.endswith(".md")
            and (
                artifact_contract[artifact].get("min_sfmea_rows")
                or artifact_contract[artifact].get("min_black_box_cases")
            )
        ),
        {},
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
    requested_source_driven_groups: set[str] = set()
    v2_requested = any(
        artifact in _SOURCE_DRIVEN_STAGE_BY_ARTIFACT for artifact in outputs
    )
    for output_index, artifact in enumerate(outputs):
        source_driven_stage = _SOURCE_DRIVEN_STAGE_BY_ARTIFACT.get(artifact)
        if source_driven_stage:
            if source_driven_stage in requested_source_driven_groups:
                continue
            requested_source_driven_groups.add(source_driven_stage)
            group = _SOURCE_DRIVEN_STAGE_GROUPS[source_driven_stage]
            requested.append(
                (
                    output_index,
                    str(group["anchor"]),
                    source_driven_stage,
                    list(group["depends_on"]),
                )
            )
            continue
        stage_id, dependencies = _STAGE_BY_ARTIFACT.get(
            artifact,
            (f"artifact_{output_index + 1}", ["source_analysis"]),
        )
        contract_for_artifact = artifact_contract.get(artifact)
        if (
            isinstance(contract_for_artifact, dict)
            and artifact.endswith(".md")
            and (
                contract_for_artifact.get("min_sfmea_rows")
                or contract_for_artifact.get("min_black_box_cases")
            )
        ):
            dependencies = ["business_flow", "sfmea", "black_box_cases"]
        if v2_requested and stage_id == "sfmea" and "scenario_expansion" not in dependencies:
            dependencies = [*dependencies, "scenario_expansion"]
        if v2_requested and stage_id == "black_box_cases" and "scenario_expansion" not in dependencies:
            dependencies = [*dependencies, "scenario_expansion"]
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
        if output_index < 0 and artifact == "sfmea.json" and combined_report_contract:
            schema = json.loads(json.dumps(SFMEA_SCHEMA))
            schema["minItems"] = int(combined_report_contract.get("min_sfmea_rows") or 1)
            _require_technical_claims(schema)
            output_contract = {"artifact": artifact, "schema": schema}
        elif output_index < 0 and artifact == "black_box_cases.json" and combined_report_contract:
            schema = json.loads(json.dumps(BLACK_BOX_CASES_SCHEMA))
            schema["minItems"] = int(
                combined_report_contract.get("min_black_box_cases") or 1
            )
            _require_technical_claims(schema)
            output_contract = {"artifact": artifact, "schema": schema}
        output_contract["artifact"] = artifact
        if output_contract.get("schema") is None:
            output_contract.pop("schema", None)
        combined_markdown = bool(
            artifact.endswith(".md")
            and (
                output_contract.get("min_sfmea_rows")
                or output_contract.get("min_black_box_cases")
            )
        )
        stage_limits = _stage_execution_limits(base_stage_id)
        if output_index < 0 and base_stage_id in {"sfmea", "black_box_cases"} and combined_report_contract:
            minimum_items = int(
                combined_report_contract.get(
                    "min_sfmea_rows" if base_stage_id == "sfmea" else "min_black_box_cases"
                )
                or 0
            )
            stage_limits = {
                "max_tokens": (
                    settings.black_box_cases_max_tokens
                    if base_stage_id == "black_box_cases"
                    else 9000
                ),
                "output_limits": {
                    **dict(stage_limits.get("output_limits") or {}),
                    "max_items": max(
                        minimum_items,
                        12,
                    ),
                },
            }
        stages.append(
            {
                "id": stage_id,
                "artifact": artifact,
                "depends_on": projected_dependencies,
                "purpose": _stage_purpose(stage_id),
                "support": output_index < 0,
                "output_contract": output_contract,
                **(
                    {
                        "deterministic": True,
                        "produces_artifacts": list(
                            _SOURCE_DRIVEN_STAGE_GROUPS[base_stage_id]["artifacts"]
                        ),
                    }
                    if base_stage_id in _SOURCE_DRIVEN_STAGE_GROUPS
                    else {}
                ),
                **({"deterministic": True} if combined_markdown else {}),
                **(
                    {
                        "streaming": True,
                        "continue_on_length": True,
                        "max_continuations": 2,
                    }
                    if combined_markdown
                    else {}
                ),
                **(
                    {
                        "streaming": True,
                        "continue_on_length": True,
                        "max_continuations": 1,
                    }
                    if output_index < 0 and base_stage_id == "business_flow" and combined_report_contract
                    else {}
                ),
                **stage_limits,
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


def _require_technical_claims(schema: dict[str, Any]) -> None:
    items = schema.get("items") if isinstance(schema.get("items"), dict) else {}
    required = list(items.get("required") or [])
    if "technical_claims" not in required:
        required.append("technical_claims")
    items["required"] = required
    claims = (
        items.get("properties", {}).get("technical_claims")
        if isinstance(items.get("properties"), dict)
        else None
    )
    if isinstance(claims, dict):
        claims["minItems"] = 1
        claims["maxItems"] = 1
        claim_item = claims.get("items") if isinstance(claims.get("items"), dict) else {}
        evidence = (
            claim_item.get("properties", {}).get("evidence")
            if isinstance(claim_item.get("properties"), dict)
            else None
        )
        if isinstance(evidence, dict):
            evidence["minItems"] = 1
            evidence["maxItems"] = 1
            evidence_item = (
                evidence.get("items")
                if isinstance(evidence.get("items"), dict)
                else {}
            )
            evidence_required = list(evidence_item.get("required") or [])
            for field in ("evidence_id", "path", "quote"):
                if field not in evidence_required:
                    evidence_required.append(field)
            evidence_item["required"] = evidence_required
            evidence["items"] = evidence_item
    schema["items"] = items


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
    regular_stage_cache_dir: Path | None = None,
    regular_stage_limits: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    source_context_payload = (
        source_analysis_context if isinstance(source_analysis_context, dict) else {}
    )
    nested_source = (
        source_context_payload.get("source_context")
        if isinstance(source_context_payload.get("source_context"), dict)
        else source_context_payload
    )
    if nested_source.get("repo_revision"):
        plan["repo_revision"] = str(nested_source.get("repo_revision") or "")
    _write_json(artifact_dir / "staged_execution_plan.json", plan)
    completed: dict[str, Path] = {}
    models: set[str] = set()
    provider_capacity = _shared_provider_capacity()
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
        existing_quality_result = _existing_quality_stage_result(
            plan=plan,
            artifact_dir=artifact_dir,
            stage_dir=stage_dir,
            stage=stage,
        )
        if existing_quality_result is not None:
            completed[stage_id] = Path(existing_quality_result["output_path"])
            await _emit_progress(
                on_progress,
                {
                    "event_type": "stage_reused",
                    "stage_id": stage_id,
                    "status": "completed",
                    "current": index + 1,
                    "total": len(stages),
                    "artifact": artifact,
                    "reuse_source": existing_quality_result["reuse_source"],
                    "user_message": f"质量修复复用已通过的 {artifact}",
                },
            )
            if stage_id == "source_analysis":
                await _execute_ready_stage_levels(
                    llm=llm,
                    auxiliary_llm=source_analysis_llm or llm,
                    plan=plan,
                    stages=stages,
                    artifact_dir=artifact_dir,
                    context_prompt=context_prompt,
                    completed=completed,
                    models=models,
                    is_cancelled=is_cancelled,
                    on_progress=on_progress,
                    max_tokens=max_tokens,
                    provider_capacity=provider_capacity,
                    regular_stage_cache_dir=regular_stage_cache_dir,
                    regular_stage_limits=regular_stage_limits,
                )
                break
            continue
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
                provider_capacity=provider_capacity,
            )
            output_path = Path(source_outcome["output_path"])
            completed[stage_id] = output_path
            response_model = str(source_outcome.get("model") or "").strip()
            if response_model:
                models.add(response_model)
            await _emit_progress(
                on_progress,
                {
                    "event_type": "stage_completed",
                    "stage_id": stage_id,
                    "status": "completed",
                    "current": index + 1,
                    "total": len(stages),
                    "artifact": artifact,
                    "degraded": bool(source_outcome.get("degraded")),
                    "user_message": (
                        "源码证据阶段已复用通过校验的结果"
                        if source_outcome.get("reused")
                        else "源码证据阶段已完成，产物已保存"
                    ),
                },
            )
            await _execute_ready_stage_levels(
                llm=llm,
                auxiliary_llm=source_analysis_llm or llm,
                plan=plan,
                stages=stages,
                artifact_dir=artifact_dir,
                context_prompt=context_prompt,
                completed=completed,
                models=models,
                is_cancelled=is_cancelled,
                on_progress=on_progress,
                max_tokens=max_tokens,
                provider_capacity=provider_capacity,
                regular_stage_cache_dir=regular_stage_cache_dir,
                regular_stage_limits=regular_stage_limits,
            )
            break
        outcome = await _execute_regular_stage(
            llm=llm,
            auxiliary_llm=source_analysis_llm or llm,
            plan=plan,
            stage=stage,
            stage_dir=stage_dir,
            artifact_dir=artifact_dir,
            context_prompt=context_prompt,
            completed=completed,
            is_cancelled=is_cancelled,
            max_tokens=max_tokens,
            on_progress=on_progress,
            provider_capacity=provider_capacity,
            regular_stage_cache_dir=regular_stage_cache_dir,
            regular_stage_limits=regular_stage_limits,
        )
        output_path = Path(outcome["output_path"])
        response_model = str(outcome.get("model") or "").strip()
        if response_model:
            models.add(response_model)
        completed[stage_id] = output_path
        await _emit_progress(
            on_progress,
            {
                "event_type": "stage_completed",
                "stage_id": stage_id,
                "status": str(outcome.get("status") or "completed"),
                "current": index + 1,
                "total": len(stages),
                "artifact": artifact,
                "degraded": bool(outcome.get("degraded")),
                "user_message": _regular_stage_completion_message(outcome),
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
    stage_statuses: dict[str, str] = {}
    for index, stage in enumerate(stages):
        stage_id = str(stage.get("id") or f"stage_{index + 1}")
        stage_result = _read_json_file(
            artifact_dir / "stages" / stage_id / "stage_result.json"
        )
        stage_statuses[stage_id] = str(stage_result.get("status") or "unknown")
    partial_stages = [
        stage_id for stage_id, status in stage_statuses.items() if status == "partial"
    ]
    execution = {
        "version": "ai-staged-execution-result-v1",
        "status": "partial" if partial_stages else "completed",
        "completed_stages": sum(
            status == "completed" for status in stage_statuses.values()
        ),
        "partial_stages": partial_stages,
        "stage_statuses": stage_statuses,
        "total_stages": len(stages),
        "manifest": "artifact_manifest.json",
        "models": sorted(models),
    }
    _write_json(artifact_dir / "staged_execution_result.json", execution)
    return {**execution, "artifact_manifest": manifest}


def _existing_quality_stage_result(
    *,
    plan: dict[str, Any],
    artifact_dir: Path,
    stage_dir: Path,
    stage: dict[str, Any],
) -> dict[str, Any] | None:
    """Reuse same-run artifacts that the quality audit did not reject."""
    feedback = plan.get("quality_retry_feedback")
    if not isinstance(feedback, dict) or not feedback:
        return None
    artifact = str(stage.get("artifact") or "").strip()
    if not artifact:
        return None
    bypass = {
        str(value).strip()
        for value in plan.get("cache_bypass_artifacts") or []
        if str(value).strip()
    }
    if artifact in bypass:
        return None
    output_path = artifact_dir / artifact
    if not output_path.is_file():
        return None
    result = {
        "stage_id": str(stage.get("id") or ""),
        "status": "completed",
        "artifact": artifact,
        "attempts": 0,
        "attempt_count": 0,
        "repair_attempt_count": 0,
        "reused": True,
        "reuse_source": "same_run_quality_accepted_artifact",
        "size_bytes": output_path.stat().st_size,
        "model": "",
        "output_path": str(output_path),
    }
    stage_dir.mkdir(parents=True, exist_ok=True)
    _write_json(stage_dir / "stage_result.json", result)
    return result


async def _execute_ready_stage_levels(
    *,
    llm: Any,
    auxiliary_llm: Any,
    plan: dict[str, Any],
    stages: list[dict[str, Any]],
    artifact_dir: Path,
    context_prompt: str,
    completed: dict[str, Path],
    models: set[str],
    is_cancelled: CancellationCallback | None,
    on_progress: ProgressCallback | None,
    max_tokens: int,
    provider_capacity: _ProcessProviderCapacity,
    regular_stage_cache_dir: Path | None,
    regular_stage_limits: dict[str, dict[str, Any]] | None,
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
            existing_quality_result = _existing_quality_stage_result(
                plan=plan,
                artifact_dir=artifact_dir,
                stage_dir=stage_dir,
                stage=stage,
            )
            if existing_quality_result is not None:
                completed[stage_id] = Path(existing_quality_result["output_path"])
                await _emit_progress(
                    on_progress,
                    {
                        "event_type": "stage_reused",
                        "stage_id": stage_id,
                        "status": "completed",
                        "current": positions[stage_id] + 1,
                        "total": len(stages),
                        "artifact": artifact,
                        "reuse_source": existing_quality_result["reuse_source"],
                        "user_message": f"质量修复复用已通过的 {artifact}",
                    },
                )
                continue
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
            if stage_id in _FLOW_DETERMINISTIC_STAGES:
                outcome = await _execute_flow_deterministic_stage(
                    plan=plan,
                    stage=stage,
                    stage_dir=stage_dir,
                    artifact_dir=artifact_dir,
                    is_cancelled=is_cancelled,
                    on_progress=on_progress,
                    cache_dir=regular_stage_cache_dir,
                )
                completed[stage_id] = Path(outcome["output_path"])
                await _emit_progress(
                    on_progress,
                    {
                        "event_type": "stage_completed",
                        "stage_id": stage_id,
                        "status": "completed",
                        "current": positions[stage_id] + 1,
                        "total": len(stages),
                        "artifact": artifact,
                        "degraded": bool(outcome.get("degraded")),
                        "user_message": _regular_stage_completion_message(outcome),
                    },
                )
                continue
            if stage_id in _SOURCE_DRIVEN_DETERMINISTIC_STAGES:
                outcome = await _execute_source_driven_deterministic_stage(
                    plan=plan,
                    stage=stage,
                    stage_dir=stage_dir,
                    artifact_dir=artifact_dir,
                    is_cancelled=is_cancelled,
                    on_progress=on_progress,
                )
                completed[stage_id] = Path(outcome["output_path"])
                await _emit_progress(
                    on_progress,
                    {
                        "event_type": "stage_completed",
                        "stage_id": stage_id,
                        "status": "completed",
                        "current": positions[stage_id] + 1,
                        "total": len(stages),
                        "artifact": artifact,
                        "degraded": False,
                        "user_message": _regular_stage_completion_message(outcome),
                    },
                )
                continue
            if _is_combined_report_stage(stage):
                outcome = await _execute_combined_report_stage(
                    plan=plan,
                    stage=stage,
                    stage_dir=stage_dir,
                    artifact_dir=artifact_dir,
                    completed=completed,
                    is_cancelled=is_cancelled,
                    on_progress=on_progress,
                )
                completed[stage_id] = Path(outcome["output_path"])
                await _emit_progress(
                    on_progress,
                    {
                        "event_type": "stage_completed",
                        "stage_id": stage_id,
                        "status": "completed",
                        "current": positions[stage_id] + 1,
                        "total": len(stages),
                        "artifact": artifact,
                        "degraded": False,
                        "user_message": "已从通过校验的阶段产物生成最终报告",
                    },
                )
                continue
            tasks[stage_id] = asyncio.create_task(
                _execute_regular_stage(
                    llm=llm,
                    auxiliary_llm=auxiliary_llm,
                    plan=plan,
                    stage=stage,
                    stage_dir=stage_dir,
                    artifact_dir=artifact_dir,
                    context_prompt=context_prompt,
                    completed=dict(completed),
                    is_cancelled=is_cancelled,
                    max_tokens=max_tokens,
                    on_progress=on_progress,
                    provider_capacity=provider_capacity,
                    regular_stage_cache_dir=regular_stage_cache_dir,
                    regular_stage_limits=regular_stage_limits,
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
                        "event_type": "stage_completed",
                        "stage_id": stage_id,
                        "status": str(outcome.get("status") or "completed"),
                        "current": positions[stage_id] + 1,
                        "total": len(stages),
                        "artifact": str(stage.get("artifact") or f"{stage_id}.md"),
                        "degraded": bool(outcome.get("degraded")),
                        "user_message": _regular_stage_completion_message(outcome),
                    },
                )
        for stage_id, _stage in ready:
            remaining.pop(stage_id, None)


def _is_combined_report_stage(stage: dict[str, Any]) -> bool:
    contract = (
        stage.get("output_contract")
        if isinstance(stage.get("output_contract"), dict)
        else {}
    )
    return bool(
        str(stage.get("artifact") or "").endswith(".md")
        and (
            contract.get("min_sfmea_rows")
            or contract.get("min_black_box_cases")
        )
    )


async def _execute_combined_report_stage(
    *,
    plan: dict[str, Any],
    stage: dict[str, Any],
    stage_dir: Path,
    artifact_dir: Path,
    completed: dict[str, Path],
    is_cancelled: CancellationCallback | None,
    on_progress: ProgressCallback | None,
) -> dict[str, Any]:
    started = time.monotonic()
    stage_id = str(stage.get("id") or "combined_report")
    artifact = str(stage.get("artifact") or "report.md")
    output_path = artifact_dir / artifact
    if await _callback_true(is_cancelled):
        raise StagedExecutionCancelled("任务已取消，已停止最终报告生成")
    await _emit_progress(
        on_progress,
        {
            "event_type": "stage_report_materialization_started",
            "stage_id": stage_id,
            "status": "running",
            "artifact": artifact,
            "user_message": "正在汇总已通过校验的流程、SFMEA 和黑盒用例",
        },
    )
    source_pack = _read_json_file(
        artifact_dir / "stages" / "source_analysis" / "source_evidence_pack.json"
    )
    flow_path = completed.get("business_flow") or artifact_dir / "business_flow.md"
    flow = (
        Path(flow_path).read_text(encoding="utf-8", errors="replace")
        if Path(flow_path).is_file()
        else ""
    )
    sfmea = _read_json_file(artifact_dir / "sfmea.json", default=[])
    black_box_cases = _read_json_file(
        artifact_dir / "black_box_cases.json", default=[]
    )
    report = _render_deterministic_combined_report(
        plan=plan,
        source_pack=source_pack,
        business_flow=flow,
        sfmea=sfmea if isinstance(sfmea, list) else [],
        black_box_cases=(
            black_box_cases if isinstance(black_box_cases, list) else []
        ),
    )
    output_contract = (
        stage.get("output_contract")
        if isinstance(stage.get("output_contract"), dict)
        else {}
    )
    report, removed_unverified_paths = _finalize_combined_markdown_report(
        content=report,
        source_pack=source_pack,
        output_contract=output_contract,
        extract_delivery_body=False,
    )
    _write_text(output_path, report)
    harness_validation = (
        _materialize_and_validate_raw_pdu_harness(artifact_dir)
        if _is_iscsi_login_report(plan)
        else {}
    )
    duration_ms = round((time.monotonic() - started) * 1000, 1)
    result = {
        "stage_id": stage_id,
        "status": "completed",
        "artifact": artifact,
        "attempts": 0,
        "attempt_count": 0,
        "provider_call_count": 0,
        "continuation_count": 0,
        "repair_attempt_count": 0,
        "producer": "deterministic_combined_report",
        "source_artifacts": [
            "source_analysis.md",
            "business_flow.md",
            "sfmea.json",
            "black_box_cases.json",
        ],
        "removed_unverified_paths": removed_unverified_paths,
        "harness_validation": harness_validation,
        "prompt_characters": 0,
        "prompt_estimated_tokens": 0,
        "provider_wait_ms": 0.0,
        "output_tokens": BaseLLMClient.estimate_tokens(report),
        "finish_reason": "deterministic_materialization",
        "total_duration_ms": duration_ms,
        "duration_ms": duration_ms,
        "size_bytes": output_path.stat().st_size,
        "model": "",
        "output_path": str(output_path),
    }
    _write_json(stage_dir / "stage_result.json", result)
    return result


def _render_deterministic_combined_report(
    *,
    plan: dict[str, Any],
    source_pack: dict[str, Any],
    business_flow: str,
    sfmea: list[dict[str, Any]],
    black_box_cases: list[dict[str, Any]],
) -> str:
    target = str(
        plan.get("original_user_request") or plan.get("target") or "测试分析"
    ).strip()
    revision = str(
        source_pack.get("repo_revision") or plan.get("repo_revision") or "待确认"
    ).strip()
    cards = [
        item
        for item in source_pack.get("evidence_cards") or []
        if isinstance(item, dict) and str(item.get("file_path") or "").strip()
    ]
    gaps = [
        str(item).strip()
        for item in source_pack.get("evidence_gaps") or []
        if str(item).strip()
    ]
    lines = [
        "# 源码证据驱动测试分析报告",
        "",
        f"- 分析目标：{target}",
        f"- Repo revision：`{revision}`",
        "- 生成方式：模型负责流程、风险与用例分析；CodeTalk 按已验证阶段产物确定性组装报告。",
        "",
        "## 分析范围与证据缺口",
        "",
        f"本报告基于 {len(cards)} 张经本地读取与 SHA256 校验的源码/测试证据卡。",
    ]
    if gaps:
        lines.extend(["", *[f"- {item}" for item in gaps]])
    else:
        lines.extend(["", "- 未由现有证据直接证明的行为仍应在实机测试中复核。"])
    lines.extend(["", "## 关键源码证据", ""])
    for card in cards:
        path = str(card.get("file_path") or "").strip()
        start = int(card.get("start_line") or 0)
        end = int(card.get("end_line") or 0)
        anchor = f"{path}:{start}-{end}" if start > 0 and end >= start else path
        symbols = ", ".join(str(item) for item in card.get("symbols") or [])
        fact = str(card.get("fact") or card.get("summary") or "").strip()
        suffix = f"；{fact}" if fact else ""
        lines.append(f"- `{anchor}` · {symbols or '未提取符号'}{suffix}")
    lines.extend(["", "## 主流程与异常/恢复流程", ""])
    lines.append(
        _demote_markdown_headings(business_flow.strip(), minimum_level=3)
        or "流程阶段未形成可交付叙述，请查看 `flow_outline.json`。"
    )
    lines.extend(
        [
            "",
            "## SFMEA",
            "",
            "评分采用 1-10：Severity 表示业务影响，Occurrence 表示发生可能性，Detection 表示失效发生前难以发现的程度；RPN=S×O×D。RPN≥200 优先处理，100-199 纳入近期整改，<100 持续监控。Occurrence 无缺陷历史、登录流量分布或测试统计支撑时应标记待采样，不把估计值写成事实发生率。",
            "",
            "| ID | Failure mode | Cause | Effect | Detection | S | O | D | RPN | Mitigation | Evidence / test mapping |",
            "|---|---|---|---|---|---:|---:|---:|---:|---|---|",
        ]
    )
    for index, row in enumerate(sfmea, start=1):
        row = row if isinstance(row, dict) else {}
        stable_id = str(
            row.get("sfmea_id") or row.get("risk_id") or row.get("id") or ""
        ).strip()
        display_id = stable_id or f"FMEA-{index:02d}"
        evidence = _markdown_list_value(row.get("source_evidence"))
        mapping = _markdown_list_value(row.get("test_mapping"))
        lines.append(
            "| {display_id} | {failure_mode} | {cause} | {effect} | "
            "{detection} | {severity} | {occurrence} | {detection_score} | "
            "{rpn} | {mitigation} | {evidence}{mapping} |".format(
                display_id=_markdown_table_cell(display_id),
                failure_mode=_markdown_table_cell(row.get("failure_mode")),
                cause=_markdown_table_cell(row.get("cause")),
                effect=_markdown_table_cell(row.get("effect")),
                detection=_markdown_table_cell(row.get("detection")),
                severity=_markdown_table_cell(row.get("severity")),
                occurrence=_markdown_table_cell(row.get("occurrence")),
                detection_score=_markdown_table_cell(row.get("detection_score")),
                rpn=_markdown_table_cell(row.get("rpn")),
                mitigation=_markdown_table_cell(row.get("mitigation")),
                evidence=_markdown_table_cell(evidence),
                mapping=(
                    "；" + _markdown_table_cell(mapping) if mapping else ""
                ),
            )
        )
    lines.extend(["", "## 黑盒测试用例", ""])
    for index, row in enumerate(black_box_cases, start=1):
        row = row if isinstance(row, dict) else {}
        case_id = str(row.get("case_id") or f"TC-{index:02d}").strip()
        if not re.match(r"(?i)^(?:BB|TC|CASE|用例)[-_ ]?\d+", case_id):
            case_id = f"TC-{index:02d}"
        scenario = str(row.get("scenario_name") or "未命名场景").strip()
        lines.extend(
            [
                f"### {case_id} {scenario}",
                "",
                f"- 测试维度：{_markdown_list_value(row.get('test_dimension'))}",
                f"- 前置条件：{_markdown_list_value(row.get('preconditions'))}",
                f"- 操作步骤：{_markdown_list_value(row.get('steps'))}",
                f"- 预期结果：{_markdown_list_value(row.get('expected_result'))}",
                f"- 观测点：{_markdown_list_value(row.get('observability'))}",
                f"- 失败诊断：{_markdown_list_value(row.get('failure_diagnostics'))}",
                f"- 测试映射：{_markdown_list_value(row.get('mapped_test_dir'))}",
                f"- 证据：{_markdown_list_value(row.get('source_or_test_evidence'))}",
                "",
            ]
        )
    if _is_iscsi_login_report(plan):
        lines.extend(["## 附录：iSCSI raw-PDU 复验工具", "", _ISCSI_RAW_PDU_APPENDIX])
    return "\n".join(lines).rstrip() + "\n"


def _markdown_list_value(value: Any) -> str:
    if isinstance(value, list):
        return "；".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value or "待补充").strip()


def _markdown_table_cell(value: Any) -> str:
    return _markdown_list_value(value).replace("|", "\\|").replace("\n", " ")


def _demote_markdown_headings(content: str, *, minimum_level: int) -> str:
    def replace(match: re.Match[str]) -> str:
        level = min(6, max(minimum_level, len(match.group(1)) + minimum_level - 1))
        return "#" * level + " " + match.group(2).strip()

    return re.sub(r"(?m)^(#{1,6})\s+(.+?)\s*$", replace, content)


def _is_iscsi_login_report(plan: dict[str, Any]) -> bool:
    contract = (
        ((plan.get("execution_input_contract") or {}).get("test_activity_contract"))
        if isinstance(plan.get("execution_input_contract"), dict)
        else {}
    )
    profiles = {
        str(item).strip()
        for item in (contract or {}).get("domain_profiles") or []
        if str(item).strip()
    }
    target = " ".join(
        [str(plan.get("target") or ""), str(plan.get("original_user_request") or "")]
    ).lower()
    return "iscsi_login" in profiles or ("iscsi" in target and "login" in target)


_ISCSI_RAW_PDU_APPENDIX = '''将以下代码保存为 `iscsi_login_raw_pdu.py`，仅对隔离测试 target 使用：

```bash
python3 iscsi_login_raw_pdu.py --host 127.0.0.1 --port 3260
tcpdump -i any -s 0 -w /tmp/iscsi-login.pcap tcp port 3260
tshark -r /tmp/iscsi-login.pcap -Y iscsi.opcode==0x23 -T fields -e iscsi.login_transit -e iscsi.login_continue -e iscsi.login_csg -e iscsi.login_nsg -e iscsi.login_status
```

```python
from __future__ import annotations

import argparse
import hashlib
import socket
import threading


def chap_digest(identifier: int, secret: bytes, challenge: bytes) -> bytes:
    return hashlib.md5(bytes([identifier]) + secret + challenge).digest()


def build_login_pdu(
    data: bytes,
    *,
    isid: bytes,
    cid: int,
    itt: int,
    cmdsn: int,
    tsih: int = 0,
    login_flags: int = 0x87,
    version_max: int = 0,
    version_min: int = 0,
) -> bytes:
    if len(isid) != 6:
        raise ValueError("ISID must contain 6 bytes")
    bhs = bytearray(48)
    bhs[0] = 0x03
    bhs[1] = login_flags
    bhs[2] = version_max
    bhs[3] = version_min
    bhs[5:8] = len(data).to_bytes(3, "big")
    bhs[8:14] = isid
    bhs[14:16] = tsih.to_bytes(2, "big")
    bhs[16:20] = itt.to_bytes(4, "big")
    bhs[20:22] = cid.to_bytes(2, "big")
    bhs[24:28] = cmdsn.to_bytes(4, "big")
    padding = bytes((-len(data)) % 4)
    return bytes(bhs) + data + padding


def recv_pdu(sock: socket.socket) -> tuple[bytes, bytes]:
    bhs = b""
    while len(bhs) < 48:
        chunk = sock.recv(48 - len(bhs))
        if not chunk:
            raise ConnectionError("peer closed before complete BHS")
        bhs += chunk
    data_segment_length = int.from_bytes(bhs[5:8], "big")
    padded = (data_segment_length + 3) & ~3
    payload = b""
    while len(payload) < padded:
        chunk = sock.recv(padded - len(payload))
        if not chunk:
            raise ConnectionError("peer closed before complete data segment")
        payload += chunk
    return bhs, payload[:data_segment_length]


def assert_login_response(
    bhs: bytes,
    *,
    expected_class: int | None = None,
    expected_detail: int | None = None,
) -> tuple[int, int]:
    if len(bhs) != 48 or (bhs[0] & 0x3f) != 0x23:
        raise AssertionError("peer did not return an iSCSI Login Response")
    status_class = bhs[36]
    status_detail = bhs[37]
    if expected_class is not None:
        assert status_class == expected_class, (status_class, status_detail)
    if expected_detail is not None:
        assert status_detail == expected_detail, (status_class, status_detail)
    return status_class, status_detail


def run(host: str, port: int) -> None:
    isid = bytes.fromhex("800000000001")
    cid = 1
    itt = 0x1001
    cmdsn = 1
    text = b"InitiatorName=iqn.2026-07.test:codetalk\\x00SessionType=Discovery\\x00AuthMethod=None\\x00"
    request = build_login_pdu(text, isid=isid, cid=cid, itt=itt, cmdsn=cmdsn)
    with socket.create_connection((host, port), timeout=10) as sock:
        sock.sendall(request)
        response_bhs, response_data = recv_pdu(sock)
        assert_login_response(response_bhs)
    print(response_bhs.hex(), response_data.decode("utf-8", errors="replace"))


def run_fragmented_login(host: str, port: int) -> None:
    isid = bytes.fromhex("800000000002")
    with socket.create_connection((host, port), timeout=10) as sock:
        first = build_login_pdu(
            b"InitiatorName=iqn.2026-07.test:fragmented\\x00",
            isid=isid,
            cid=1,
            itt=0x2001,
            cmdsn=1,
            login_flags=0x40,
        )
        sock.sendall(first)
        first_bhs, _ = recv_pdu(sock)
        assert_login_response(first_bhs)
        final = build_login_pdu(
            b"SessionType=Discovery\\x00AuthMethod=None\\x00",
            isid=isid,
            cid=1,
            itt=0x2001,
            cmdsn=1,
            login_flags=0x87,
        )
        sock.sendall(final)
        final_bhs, _ = recv_pdu(sock)
        assert_login_response(final_bhs)


def run_unsupported_version(host: str, port: int) -> None:
    request = build_login_pdu(
        b"InitiatorName=iqn.2026-07.test:version\\x00SessionType=Discovery\\x00",
        isid=bytes.fromhex("800000000003"),
        cid=1,
        itt=0x3001,
        cmdsn=1,
        version_max=0xff,
        version_min=0xfe,
    )
    with socket.create_connection((host, port), timeout=10) as sock:
        sock.sendall(request)
        response_bhs, _ = recv_pdu(sock)
        assert_login_response(response_bhs, expected_class=0x02, expected_detail=0x05)


def run_mcs(host: str, port: int, expected_class: int, expected_detail: int) -> None:
    isid = bytes.fromhex("800000000004")
    first_sock = socket.create_connection((host, port), timeout=10)
    second_sock = socket.create_connection((host, port), timeout=10)
    try:
        first_sock.sendall(
            build_login_pdu(
                b"InitiatorName=iqn.2026-07.test:mcs\\x00SessionType=Normal\\x00AuthMethod=None\\x00",
                isid=isid,
                cid=1,
                itt=0x4001,
                cmdsn=1,
            )
        )
        first_bhs, _ = recv_pdu(first_sock)
        assert_login_response(first_bhs, expected_class=0x00, expected_detail=0x00)
        tsih = int.from_bytes(first_bhs[14:16], "big")
        assert tsih != 0
        second_sock.sendall(
            build_login_pdu(
                b"InitiatorName=iqn.2026-07.test:mcs\\x00SessionType=Normal\\x00AuthMethod=None\\x00",
                isid=isid,
                cid=2,
                itt=0x4002,
                cmdsn=2,
                tsih=tsih,
            )
        )
        second_bhs, _ = recv_pdu(second_sock)
        assert_login_response(
            second_bhs,
            expected_class=expected_class,
            expected_detail=expected_detail,
        )
    finally:
        second_sock.close()
        first_sock.close()


def self_test() -> None:
    sample = build_login_pdu(b"abc", isid=b"ABCDEF", cid=1, itt=2, cmdsn=3)
    assert sample[5:8] == (3).to_bytes(3, "big")
    assert sample[1] == 0x87
    assert sample[2:4] == b"\\x00\\x00"
    assert sample[16:20] == (2).to_bytes(4, "big")
    assert sample[20:22] == (1).to_bytes(2, "big")
    assert sample[24:28] == (3).to_bytes(4, "big")
    assert chap_digest(7, b"secret", b"challenge") == hashlib.md5(
        b"\\x07secretchallenge"
    ).digest()

    received: dict[str, bytes] = {}
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def serve_once() -> None:
        conn, _ = server.accept()
        with conn:
            request_bhs, request_data = recv_pdu(conn)
            received["bhs"] = request_bhs
            received["data"] = request_data
            response = bytearray(48)
            response[0] = 0x23
            conn.sendall(response)

    worker = threading.Thread(target=serve_once, daemon=True)
    worker.start()
    try:
        run("127.0.0.1", server.getsockname()[1])
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert received["bhs"][0] == 0x03
        assert b"SessionType=Discovery" in received["data"]
    finally:
        server.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", "--target_ip", dest="host", required=True)
    parser.add_argument("--port", type=int, default=3260)
    parser.add_argument("--scenario", default="basic")
    parser.add_argument("--expected-class", type=lambda value: int(value, 0), default=0x02)
    parser.add_argument("--expected-detail", type=lambda value: int(value, 0), default=0x06)
    args = parser.parse_args()
    self_test()
    scenario = args.scenario.lower()
    if "fragment" in scenario or "c-bit" in scenario:
        run_fragmented_login(args.host, args.port)
    elif "version" in scenario:
        run_unsupported_version(args.host, args.port)
    elif "mcs" in scenario:
        run_mcs(args.host, args.port, args.expected_class, args.expected_detail)
    else:
        run(args.host, args.port)


if __name__ == "__main__":
    main()
```

`DataSegmentLength` 由 BHS bytes 5-7 写入和解析；byte 4 保留为 `TotalAHSLength`。脚本显式携带 ISID、CID、ITT、CmdSN，并使用 socket `sendall`/`recv` 与 `hashlib.md5`。它只提供协议构造与捕获基线，具体异常场景仍以对应黑盒用例的输入和断言为准。'''


def _materialize_and_validate_raw_pdu_harness(artifact_dir: Path) -> dict[str, Any]:
    """Execute the deterministic harness self-test over a real loopback TCP socket."""
    match = re.search(
        r"```python\s*\n([\s\S]*?)```",
        _ISCSI_RAW_PDU_APPENDIX,
        flags=re.IGNORECASE,
    )
    started = time.monotonic()
    validation_path = artifact_dir / "raw_pdu_harness_validation.json"
    if match is None:
        result = {
            "status": "failed",
            "validation_layer": "L3_executable",
            "reason": "确定性 raw-PDU harness 缺少 Python 代码块",
        }
        _write_json(validation_path, result)
        return result

    support_dir = artifact_dir / "support"
    support_dir.mkdir(parents=True, exist_ok=True)
    harness_path = support_dir / "iscsi_login_raw_pdu.py"
    _write_text(harness_path, match.group(1).rstrip() + "\n")
    runner = (
        "import runpy,sys; "
        "ns=runpy.run_path(sys.argv[1],run_name='codetalk_harness'); "
        "fn=ns.get('self_test'); "
        "assert callable(fn),'self_test missing'; "
        "fn(); print('CODETALK_RAW_PDU_SELF_TEST_OK')"
    )
    interpreter = shutil.which("python3") or sys.executable
    try:
        completed = subprocess.run(
            [interpreter, "-I", "-c", runner, str(harness_path)],
            cwd=str(support_dir),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        passed = completed.returncode == 0 and "CODETALK_RAW_PDU_SELF_TEST_OK" in completed.stdout
        result = {
            "status": "passed" if passed else "failed",
            "validation_layer": "L3_executable",
            "transport": "tcp_loopback",
            "checks": [
                "bhs_layout",
                "chap_md5",
                "tcp_connect",
                "first_pdu_sendall",
                "login_response_recv",
                "status_oracle",
            ],
            "exit_code": completed.returncode,
            "interpreter": interpreter,
            "duration_ms": round((time.monotonic() - started) * 1000, 1),
            "stdout_tail": completed.stdout[-500:],
            "stderr_tail": completed.stderr[-500:],
            "harness": str(harness_path.relative_to(artifact_dir)),
        }
    except subprocess.TimeoutExpired:
        result = {
            "status": "failed",
            "validation_layer": "L3_executable",
            "transport": "tcp_loopback",
            "reason": "raw-PDU harness 自检超过 5 秒",
            "duration_ms": round((time.monotonic() - started) * 1000, 1),
            "harness": str(harness_path.relative_to(artifact_dir)),
            "interpreter": interpreter,
        }
    _write_json(validation_path, result)
    return result


async def _execute_flow_deterministic_stage(
    *,
    plan: dict[str, Any],
    stage: dict[str, Any],
    stage_dir: Path,
    artifact_dir: Path,
    is_cancelled: CancellationCallback | None,
    on_progress: ProgressCallback | None,
    cache_dir: Path | None,
) -> dict[str, Any]:
    started = time.monotonic()
    stage_id = str(stage.get("id") or "")
    artifact = str(stage.get("artifact") or f"{stage_id}.json")
    output_path = artifact_dir / artifact
    source_pack = _read_json_file(artifact_dir / "stages" / "source_analysis" / "source_evidence_pack.json")
    if not source_pack:
        source_pack = {
            "analysis_target": str(plan.get("original_user_request") or ""),
            "repo_revision": str(plan.get("repo_revision") or ""),
            "source_scope": _read_json_file(artifact_dir / "source_scope.json"),
            "evidence_cards": _read_json_file(artifact_dir / "evidence_cards.json", default=[]),
        }
    flow_pack = _read_json_file(artifact_dir / "flow_evidence_pack.json")
    policy = stage_execution_policy(stage=stage, global_max_tokens=256)
    cache_key = regular_stage_cache_key(
        stage=stage,
        plan=plan,
        prompt=f"deterministic-flow-stage:{FLOW_EVIDENCE_VERSION}",
        policy=policy,
        source_fingerprint=stable_payload_sha256(source_pack),
        flow_fingerprint=(
            stable_payload_sha256(flow_pack)
            if stage_id == "flow_outline" and flow_pack
            else ""
        ),
    )
    cache = regular_stage_cache_root(cache_dir)
    cached = restore_regular_stage_cache(
        cache_root=cache,
        cache_key=cache_key,
        artifact=artifact,
        output_path=output_path,
    )
    if cached is not None:
        result = {
            **cached,
            "status": "completed",
            "attempts": 0,
            "attempt_count": 0,
            "cache_status": "hit",
            "reused": True,
            "total_duration_ms": round((time.monotonic() - started) * 1000, 1),
        }
        reuse_metrics: dict[str, int] = {}
        if stage_id == "flow_evidence_pack":
            reused_pack = _read_json_file(output_path)
            reuse_metrics = {
                "entry_point_count": len(reused_pack.get("entry_points") or []),
                "call_edge_count": len(reused_pack.get("call_edges") or []),
                "test_reference_count": len(reused_pack.get("related_tests") or []),
            }
        if stage_id == "flow_outline":
            outline = _read_json_file(output_path)
            _write_text(artifact_dir / "business_flow.md", render_business_flow_markdown(outline))
        _write_json(stage_dir / "stage_result.json", result)
        await _emit_progress(
            on_progress,
            {
                "event_type": "stage_reused",
                "stage_id": stage_id,
                "status": "completed",
                "artifact": artifact,
                "cache_key": cache_key,
                **reuse_metrics,
                "user_message": f"已复用通过校验的 {artifact}",
            },
        )
        return {**result, "output_path": str(output_path)}

    if await _callback_true(is_cancelled):
        raise StagedExecutionCancelled("任务已取消，已停止流程证据准备")
    degraded = False
    degradation_reason = ""
    if stage_id == "flow_evidence_pack":
        await _emit_progress(
            on_progress,
            {
                "event_type": "stage_flow_evidence_started",
                "stage_id": stage_id,
                "status": "running",
                "artifact": artifact,
                "user_message": "正在准备调用链证据",
            },
        )
        try:
            payload = await asyncio.wait_for(
                asyncio.to_thread(
                    build_flow_evidence_pack,
                    source_pack,
                    repo_path=str((source_pack.get("source_scope") or {}).get("repo") or ""),
                    max_files=int(settings.flow_evidence_max_files),
                ),
                timeout=float(settings.flow_evidence_timeout_seconds),
            )
        except asyncio.TimeoutError:
            degraded = True
            degradation_reason = "flow_evidence_budget_exceeded"
            payload = build_flow_evidence_pack(source_pack, repo_path="", max_files=6)
        _write_json(output_path, payload)
        await _emit_progress(
            on_progress,
            {
                "event_type": "stage_flow_evidence_ready",
                "stage_id": stage_id,
                "status": "completed",
                "artifact": artifact,
                "entry_point_count": len(payload.get("entry_points") or []),
                "call_edge_count": len(payload.get("call_edges") or []),
                "test_reference_count": len(payload.get("related_tests") or []),
                "user_message": (
                    f"已找到 {len(payload.get('entry_points') or [])} 个入口、"
                    f"{len(payload.get('call_edges') or [])} 条调用边、"
                    f"{len(payload.get('related_tests') or [])} 个测试引用"
                ),
            },
        )
    elif stage_id == "flow_outline":
        await _emit_progress(
            on_progress,
            {
                "event_type": "stage_flow_outline_started",
                "stage_id": stage_id,
                "status": "running",
                "artifact": artifact,
                "user_message": "正在生成流程骨架",
            },
        )
        if not flow_pack:
            raise RuntimeError("flow_evidence_pack.json 缺失，无法生成流程骨架")
        payload = build_flow_outline(flow_pack)
        _write_json(output_path, payload)
        _write_text(artifact_dir / "business_flow.md", render_business_flow_markdown(payload))
    else:
        raise RuntimeError(f"未知确定性阶段：{stage_id}")

    duration_ms = round((time.monotonic() - started) * 1000, 1)
    result = {
        "stage_id": stage_id,
        "status": "completed",
        "artifact": artifact,
        "attempts": 0,
        "attempt_count": 0,
        "full_retry_performed": False,
        "repair_attempt_count": 0,
        "queue_wait_ms": 0.0,
        "provider_wait_ms": 0.0,
        "time_to_first_token_ms": 0.0,
        "generation_ms": 0.0,
        "validation_ms": 0.0,
        "repair_ms": 0.0,
        "total_duration_ms": duration_ms,
        "duration_ms": duration_ms,
        "degraded": degraded,
        "degradation_reason": degradation_reason,
        "cache_status": "miss" if cache is not None else "disabled",
        "cache_key": cache_key,
        "size_bytes": output_path.stat().st_size,
        "model": "deterministic",
    }
    _write_json(stage_dir / "stage_result.json", result)
    store_regular_stage_cache(
        cache_root=cache,
        cache_key=cache_key,
        artifact=artifact,
        output_path=output_path,
        stage_result=result,
        quality_status="verified",
    )
    return {**result, "output_path": str(output_path)}


async def _execute_source_driven_deterministic_stage(
    *,
    plan: dict[str, Any],
    stage: dict[str, Any],
    stage_dir: Path,
    artifact_dir: Path,
    is_cancelled: CancellationCallback | None,
    on_progress: ProgressCallback | None,
) -> dict[str, Any]:
    started = time.monotonic()
    stage_id = str(stage.get("id") or "")
    spec = _SOURCE_DRIVEN_STAGE_GROUPS.get(stage_id)
    if not spec:
        raise RuntimeError(f"未知源码驱动测试设计阶段：{stage_id}")
    if await _callback_true(is_cancelled):
        raise StagedExecutionCancelled("任务已取消，已停止测试设计台账生成")

    messages = {
        "breadth_inventory": "正在盘点入口、流程、状态、资源与模型适用性",
        "developer_explanation": "正在生成逐流程开发讲解和分支/状态/资源处置台账",
        "scenario_expansion": "正在按八类证据来源扩展测试场景",
        "test_design_governance": "正在建立风险、黑盒控制观测和端到端追溯矩阵",
        "coverage_judge": "正在独立核验事实、可执行性和覆盖处置",
        "test_design_mindmap": "正在物化可离线预览的测试设计脑图",
    }
    await _emit_progress(
        on_progress,
        {
            "event_type": f"stage_{stage_id}_started",
            "stage_id": stage_id,
            "status": "running",
            "artifact": str(spec["anchor"]),
            "user_message": messages.get(stage_id, "正在生成测试设计工件"),
        },
    )

    source_pack = _read_json_file(
        artifact_dir / "stages" / "source_analysis" / "source_evidence_pack.json"
    )
    if not source_pack:
        source_pack = {
            "analysis_target": str(plan.get("original_user_request") or ""),
            "repo_revision": str(plan.get("repo_revision") or ""),
            "source_scope": _read_json_file(artifact_dir / "source_scope.json"),
            "evidence_cards": _read_json_file(
                artifact_dir / "evidence_cards.json", default=[]
            ),
        }
    flow_pack = _read_json_file(artifact_dir / "flow_evidence_pack.json")
    flow_outline = _read_json_file(artifact_dir / "flow_outline.json")
    sfmea_payload = _read_json_file(artifact_dir / "sfmea.json", default=[])
    cases_payload = _read_json_file(
        artifact_dir / "black_box_cases.json", default=[]
    )
    sfmea = sfmea_payload if isinstance(sfmea_payload, list) else []
    black_box_cases = cases_payload if isinstance(cases_payload, list) else []

    fact_verification: dict[str, Any] | None = None
    if stage_id in {"coverage_judge", "test_design_mindmap"}:
        fact_verification = verify_technical_claims(
            source_pack=source_pack,
            sfmea=sfmea,
            black_box_cases=black_box_cases,
        )
        _write_json(
            artifact_dir / "independent_fact_verification.json",
            fact_verification,
        )

    bundle = build_source_driven_test_design(
        source_pack=source_pack,
        flow_pack=flow_pack,
        flow_outline=flow_outline,
        sfmea=sfmea,
        black_box_cases=black_box_cases,
        fact_verification=fact_verification,
    )
    produced = [str(value) for value in spec["artifacts"]]
    if stage_id == "test_design_mindmap":
        governed = {
            name: _read_json_file(artifact_dir / name)
            for name in SOURCE_DRIVEN_V2_ARTIFACTS
            if (artifact_dir / name).is_file()
        }
        if "judge_report.json" not in governed:
            governed["judge_report.json"] = bundle["judge_report.json"]
        mindmap = build_test_design_mindmap(governed)
        _write_json(artifact_dir / MINDMAP_ARTIFACTS[0], mindmap)
        _write_text(
            artifact_dir / MINDMAP_ARTIFACTS[1],
            render_test_design_mindmap_html(mindmap),
        )
        _write_text(
            artifact_dir / MINDMAP_ARTIFACTS[2],
            render_test_design_mindmap_svg(mindmap),
        )
    else:
        for artifact in produced:
            payload = bundle.get(artifact)
            if payload is None:
                raise RuntimeError(f"{stage_id} 未生成必需工件 {artifact}")
            _write_json(artifact_dir / artifact, payload)

    validation_errors: list[str] = []
    for artifact in produced:
        path = artifact_dir / artifact
        if not path.is_file() or path.stat().st_size == 0:
            validation_errors.append(f"{artifact}:missing_or_empty")
            continue
        if path.suffix.lower() == ".json":
            payload = _read_json_file(path, default=None)
            if not isinstance(payload, (dict, list)):
                validation_errors.append(f"{artifact}:invalid_json")
        elif path.suffix.lower() == ".html":
            content = path.read_text(encoding="utf-8", errors="replace")
            if "data-mindmap-root" not in content or "mindmap-data" not in content:
                validation_errors.append(f"{artifact}:invalid_offline_viewer")
        elif path.suffix.lower() == ".svg":
            content = path.read_text(encoding="utf-8", errors="replace")
            if "<svg" not in content or "test-design-mindmap-v1" not in content:
                validation_errors.append(f"{artifact}:invalid_svg")
    if validation_errors:
        raise RuntimeError(
            "测试设计工件未通过确定性校验：" + "，".join(validation_errors)
        )

    output_path = artifact_dir / str(spec["anchor"])
    judge = _read_json_file(artifact_dir / "judge_report.json")
    duration_ms = round((time.monotonic() - started) * 1000, 1)
    result = {
        "stage_id": stage_id,
        "status": "completed",
        "artifact": str(spec["anchor"]),
        "produced_artifacts": produced,
        "artifact_count": len(produced),
        "attempts": 0,
        "attempt_count": 0,
        "provider_call_count": 0,
        "provider_wait_ms": 0.0,
        "generation_ms": 0.0,
        "validation_ms": duration_ms,
        "total_duration_ms": duration_ms,
        "duration_ms": duration_ms,
        "model": "deterministic",
        "producer": "source_driven_test_design_v2",
        "gate_status": (
            str(judge.get("status") or "")
            if stage_id in {"coverage_judge", "test_design_mindmap"}
            else "validated"
        ),
        "size_bytes": sum((artifact_dir / name).stat().st_size for name in produced),
    }
    _write_json(stage_dir / "stage_result.json", result)
    await _emit_progress(
        on_progress,
        {
            "event_type": f"stage_{stage_id}_ready",
            "stage_id": stage_id,
            "status": "completed",
            "artifact": str(spec["anchor"]),
            "artifact_count": len(produced),
            "gate_status": result["gate_status"],
            "user_message": f"已生成并校验 {len(produced)} 个测试设计工件",
        },
    )
    return {**result, "output_path": str(output_path)}


def _select_regular_stage_llm(
    llm: Any,
    auxiliary_llm: Any,
    artifact: str,
    *,
    quality_repair: bool = False,
) -> Any:
    """Route bounded structured output away from reasoning-token starvation."""
    if quality_repair and settings.regular_stage_quality_repair_use_primary_model:
        return llm
    if (
        not settings.regular_stage_structured_fast_model_enabled
        or not artifact.endswith(".json")
        or auxiliary_llm is llm
    ):
        return llm
    model = str(
        getattr(llm, "_model", "")
        or getattr(llm, "model", "")
        or ""
    ).lower()
    return auxiliary_llm if "reasoner" in model else llm


async def _execute_regular_stage(
    *,
    llm: Any,
    auxiliary_llm: Any,
    plan: dict[str, Any],
    stage: dict[str, Any],
    stage_dir: Path,
    artifact_dir: Path,
    context_prompt: str,
    completed: dict[str, Path],
    is_cancelled: CancellationCallback | None,
    max_tokens: int,
    on_progress: ProgressCallback | None,
    provider_capacity: _ProcessProviderCapacity,
    regular_stage_cache_dir: Path | None,
    regular_stage_limits: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    started = time.monotonic()
    stage_id = str(stage.get("id") or "stage")
    base_stage_id = stage_id.split("__", 1)[0]
    artifact = str(stage.get("artifact") or f"{stage_id}.md")
    output_path = artifact_dir / artifact
    output_path.parent.mkdir(parents=True, exist_ok=True)
    source_pack = _read_json_file(
        artifact_dir / "stages" / "source_analysis" / "source_evidence_pack.json"
    )
    flow_pack = _read_json_file(artifact_dir / "flow_evidence_pack.json")
    outline = _read_json_file(artifact_dir / "flow_outline.json")
    claim_catalog = _build_verified_claim_catalog(source_pack)
    legacy_prompt = _stage_prompt(
        plan=plan, stage=stage, context_prompt=context_prompt, completed=completed
    )
    dependency_artifact_characters = _dependency_artifact_characters(
        stage=stage,
        completed=completed,
    )
    quality_feedback = plan.get("quality_retry_feedback")
    quality_affected = {
        Path(str(value)).name
        for value in (quality_feedback or {}).get("affected_artifacts") or []
        if str(value).strip()
    } if isinstance(quality_feedback, dict) else set()
    current_artifact_seed = (
        output_path.read_text(encoding="utf-8", errors="replace")
        if artifact in quality_affected and output_path.is_file()
        else ""
    )
    stage_llm = _select_regular_stage_llm(
        llm,
        auxiliary_llm,
        artifact,
        quality_repair=bool(current_artifact_seed.strip()),
    )
    allowed_existing_repair_row_ids = (
        _quality_repair_row_ids(
            artifact=artifact,
            quality_feedback=quality_feedback,
        )
        if current_artifact_seed.strip() and isinstance(quality_feedback, dict)
        else None
    )
    allow_new_repair_items = (
        _quality_repair_allows_new_items(
            artifact=artifact,
            quality_feedback=quality_feedback,
        )
        if current_artifact_seed.strip() and isinstance(quality_feedback, dict)
        else True
    )
    deterministic_base = ""
    if base_stage_id == "business_flow":
        existing_content = (
            output_path.read_text(encoding="utf-8", errors="replace")
            if output_path.is_file()
            else ""
        )
        deterministic_base = _business_flow_deterministic_base(
            outline=outline,
            existing_content=existing_content,
        )
        if deterministic_base:
            _write_text(output_path, deterministic_base)
        prompt = _business_flow_stage_prompt(
            plan=plan,
            stage=stage,
            source_pack=source_pack,
            flow_pack=flow_pack,
            outline=outline,
            current_artifact_seed=current_artifact_seed,
        )
    else:
        prompt = _regular_stage_prompt(
            plan=plan,
            stage=stage,
            source_pack=source_pack,
            flow_pack=flow_pack,
            outline=outline,
            completed=completed,
            current_artifact_seed=current_artifact_seed,
        )
    _write_text(stage_dir / "stage_prompt.txt", prompt)
    overrides = (
        (regular_stage_limits or {}).get(base_stage_id)
        if isinstance((regular_stage_limits or {}).get(base_stage_id), dict)
        else None
    )
    policy = stage_execution_policy(
        stage=stage,
        global_max_tokens=max_tokens,
        overrides=overrides,
    )
    if not policy.model:
        policy = StageExecutionPolicy(
            **{
                **policy.as_dict(),
                "model": str(
                    getattr(stage_llm, "_model", "")
                    or getattr(stage_llm, "model", "")
                    or stage_llm.__class__.__name__
                ),
            }
        )
    prompt_characters_before_compaction = len(legacy_prompt)
    prompt_characters = len(prompt)
    prompt_estimated_tokens = BaseLLMClient.estimate_tokens(prompt)
    if current_artifact_seed.strip() and artifact.endswith(".json"):
        try:
            current_payload = _render_stage_artifact(current_artifact_seed, artifact)
        except (TypeError, ValueError, json.JSONDecodeError):
            current_payload = None
        if current_payload is not None:
            repaired_payload, repaired_fields = _deterministic_quality_claim_repair(
                current_payload,
                artifact=artifact,
                quality_feedback=(
                    quality_feedback if isinstance(quality_feedback, dict) else None
                ),
            )
            if repaired_fields:
                _write_json(output_path, repaired_payload)
                duration_ms = round((time.monotonic() - started) * 1000, 1)
                result = {
                    "stage_id": stage_id,
                    "status": "completed",
                    "artifact": artifact,
                    "attempts": 0,
                    "attempt_count": 0,
                    "full_retry_performed": False,
                    "repair_attempt_count": 0,
                    "provider_call_count": 0,
                    "queue_wait_ms": 0.0,
                    "provider_wait_ms": 0.0,
                    "time_to_first_token_ms": 0.0,
                    "generation_ms": 0.0,
                    "validation_ms": 0.0,
                    "repair_ms": duration_ms,
                    "total_duration_ms": duration_ms,
                    "duration_ms": duration_ms,
                    "finish_reason": "deterministic_claim_repair",
                    "prompt_characters": 0,
                    "prompt_estimated_tokens": 0,
                    "prompt_characters_before_compaction": (
                        prompt_characters_before_compaction
                    ),
                    "prepared_prompt_characters": prompt_characters,
                    "output_tokens": 0,
                    "size_bytes": output_path.stat().st_size,
                    "model": "deterministic",
                    "cache_status": "disabled",
                    "deterministic_repair_fields": repaired_fields,
                }
                _write_json(stage_dir / "stage_result.json", result)
                await _emit_progress(
                    on_progress,
                    {
                        "event_type": "stage_claim_repaired",
                        "stage_id": stage_id,
                        "status": "completed",
                        "artifact": artifact,
                        "repaired_fields": repaired_fields,
                        "user_message": (
                            f"已按确定性验证结果修复 {artifact}，无需再次调用模型"
                        ),
                    },
                )
                return {**result, "output_path": str(output_path)}
    source_fingerprint = stable_payload_sha256(source_pack) if source_pack else ""
    flow_fingerprint = stable_payload_sha256(flow_pack) if flow_pack else ""
    cache_key = regular_stage_cache_key(
        stage=stage,
        plan=plan,
        prompt=prompt,
        policy=policy,
        source_fingerprint=source_fingerprint,
        flow_fingerprint=flow_fingerprint,
    )
    cache_bypass_artifacts = {
        str(value)
        for value in plan.get("cache_bypass_artifacts") or []
        if str(value).strip()
    }
    cache = (
        None
        if artifact in cache_bypass_artifacts
        else regular_stage_cache_root(regular_stage_cache_dir)
    )
    cached = restore_regular_stage_cache(
        cache_root=cache,
        cache_key=cache_key,
        artifact=artifact,
        output_path=output_path,
    )
    partial_seed = ""
    if cached is not None and str(cached.get("status") or "") != "partial":
        duration_ms = round((time.monotonic() - started) * 1000, 1)
        result = {
            **cached,
            "status": "completed",
            "attempts": 0,
            "attempt_count": 0,
            "repair_attempt_count": 0,
            "cache_status": "hit",
            "reused": True,
            "total_duration_ms": duration_ms,
            "duration_ms": duration_ms,
        }
        _write_json(stage_dir / "stage_result.json", result)
        await _emit_progress(
            on_progress,
            {
                "event_type": "stage_reused",
                "stage_id": stage_id,
                "status": "completed",
                "artifact": artifact,
                "cache_key": cache_key,
                "user_message": f"已复用通过校验的 {artifact}",
            },
        )
        return {**result, "output_path": str(output_path)}
    if cached is not None:
        partial_seed = _extract_partial_narrative(
            output_path.read_text(encoding="utf-8", errors="replace")
        )
        await _emit_progress(
            on_progress,
            {
                "event_type": "stage_reused",
                "stage_id": stage_id,
                "status": "running",
                "artifact": artifact,
                "reuse_source": "partial_checkpoint",
                "user_message": f"已恢复 {len(partial_seed)} 个字符，将从上次部分结果继续生成",
            },
        )
        if base_stage_id == "business_flow":
            prompt = _business_flow_stage_prompt(
                plan=plan,
                stage=stage,
                source_pack=source_pack,
                flow_pack=flow_pack,
                outline=outline,
                partial_seed=partial_seed,
            )
        else:
            prompt = _regular_stage_prompt(
                plan=plan,
                stage=stage,
                source_pack=source_pack,
                flow_pack=flow_pack,
                outline=outline,
                completed=completed,
                partial_seed=partial_seed,
                current_artifact_seed=current_artifact_seed,
            )
        _write_text(stage_dir / "stage_prompt.txt", prompt)
        prompt_characters = len(prompt)
        prompt_estimated_tokens = BaseLLMClient.estimate_tokens(prompt)

    await _emit_progress(
        on_progress,
        {
            "event_type": "stage_provider_started",
            "stage_id": stage_id,
            "status": "running",
            "artifact": artifact,
            "model": policy.model
            or str(getattr(stage_llm, "_model", "") or "active-model"),
            "attempt_count": 1,
            "total_budget_seconds": policy.total_timeout_seconds,
            "user_message": f"{stage_id} 已提交模型，正在等待首段输出",
        },
    )
    queue_started = time.monotonic()
    try:
        acquired = await provider_capacity.acquire(
            policy.total_timeout_seconds,
            is_cancelled=is_cancelled,
        )
        if not acquired:
            raise asyncio.TimeoutError
    except asyncio.TimeoutError:
        raise RuntimeError(f"阶段 {stage_id} 等待 Provider 容量超过总预算")
    queue_wait_ms = round((time.monotonic() - queue_started) * 1000, 1)
    provider_started = time.monotonic()
    attempt_count = 1
    repair_attempt_count = 0
    repair_ms = 0.0
    validation_ms = 0.0
    deterministic_repair_fields: list[str] = []
    removed_unverified_paths: list[str] = []
    provider_wait_ms = 0.0
    provider_call_count = 0
    continuation_count = 0
    continuation_model = ""
    time_to_first_token_ms = 0.0
    raw_content = ""
    rendered: Any = None
    model = policy.model or str(getattr(stage_llm, "_model", "") or "")
    finish_reason = "not_started"
    last_error = ""
    timed_out = False
    status = "completed"
    detached_provider_tasks: list[asyncio.Task[Any]] = []
    try:
        remaining_total = max(
            0.001,
            policy.total_timeout_seconds - (time.monotonic() - started),
        )
        provider_timeout = min(policy.provider_timeout_seconds, remaining_total)
        if policy.streaming and not artifact.endswith(".json"):
            continuation_prompt = prompt
            continuation_seed = partial_seed
            max_continuations = max(0, int(stage.get("max_continuations") or 0))
            while True:
                provider_call_count += 1
                remaining_total = max(
                    0.001,
                    policy.total_timeout_seconds - (time.monotonic() - started),
                )
                stream_result = await _stream_regular_markdown_stage(
                    llm=llm,
                    prompt=continuation_prompt,
                    max_tokens=policy.max_tokens,
                    timeout_seconds=min(policy.provider_timeout_seconds, remaining_total),
                    total_deadline=started + policy.total_timeout_seconds,
                    is_cancelled=is_cancelled,
                    on_progress=on_progress,
                    stage_id=stage_id,
                    artifact=artifact,
                    stage_dir=stage_dir,
                    output_path=output_path,
                    deterministic_base=deterministic_base,
                    partial_seed=continuation_seed,
                    on_detached_task=detached_provider_tasks.append,
                )
                raw_content = stream_result["content"]
                provider_wait_ms += float(stream_result["provider_wait_ms"] or 0)
                if not time_to_first_token_ms:
                    time_to_first_token_ms = float(
                        stream_result["time_to_first_token_ms"] or 0
                    )
                finish_reason = str(stream_result["finish_reason"])
                timed_out = bool(stream_result["timed_out"])
                model = str(stream_result.get("model") or model)
                if (
                    not timed_out
                    and finish_reason in {"length", "max_tokens"}
                    and bool(stage.get("continue_on_length"))
                    and continuation_count < max_continuations
                ):
                    continuation_count += 1
                    continuation_seed = raw_content
                    if base_stage_id == "business_flow":
                        continuation_prompt = _business_flow_stage_prompt(
                            plan=plan,
                            stage=stage,
                            source_pack=source_pack,
                            flow_pack=flow_pack,
                            outline=outline,
                            partial_seed=raw_content,
                            current_artifact_seed=current_artifact_seed,
                        )
                    else:
                        continuation_prompt = _regular_stage_prompt(
                            plan=plan,
                            stage=stage,
                            source_pack=source_pack,
                            flow_pack=flow_pack,
                            outline=outline,
                            completed=completed,
                            partial_seed=raw_content,
                            current_artifact_seed=current_artifact_seed,
                        )
                    _write_text(
                        stage_dir / f"stage_prompt_continuation_{continuation_count}.txt",
                        continuation_prompt,
                    )
                    await _emit_progress(
                        on_progress,
                        {
                            "event_type": "stage_continuation_started",
                            "stage_id": stage_id,
                            "status": "running",
                            "artifact": artifact,
                            "attempt_count": attempt_count,
                            "continuation_count": continuation_count,
                            "output_characters": len(raw_content),
                            "user_message": (
                                f"{artifact} 已达到单段输出上限，正在从断点续写第 "
                                f"{continuation_count + 1} 段"
                            ),
                        },
                    )
                    continue
                break
            if timed_out:
                status = "partial"
                last_error = "provider_timeout"
                rendered = output_path.read_text(encoding="utf-8", errors="replace")
            elif finish_reason in {"length", "max_tokens"}:
                status = "partial"
                last_error = "provider_output_truncated"
                rendered = output_path.read_text(encoding="utf-8", errors="replace")
            else:
                rendered = output_path.read_text(encoding="utf-8", errors="replace")
        else:
            provider_call_count += 1
            current_finish_reason.set(None)
            response = await _complete_with_cancellation(
                llm=stage_llm,
                prompt=prompt,
                max_tokens=policy.max_tokens,
                is_cancelled=is_cancelled,
                timeout_seconds=provider_timeout,
                single_attempt=True,
                on_detached_task=detached_provider_tasks.append,
            )
            provider_wait_ms = round((time.monotonic() - provider_started) * 1000, 1)
            time_to_first_token_ms = provider_wait_ms
            raw_content = str(getattr(response, "content", "") or "").strip()
            model = str(getattr(response, "model", "") or model)
            finish_reason = str(
                getattr(response, "finish_reason", "")
                or current_finish_reason.get()
                or ("length" if bool(getattr(response, "truncated", False)) else "stop")
            )
            if raw_content:
                _write_text(stage_dir / "raw_output_attempt_1.txt", raw_content)
                _write_text(stage_dir / "raw_output.txt", raw_content)
            if not raw_content:
                raise ValueError("provider_output_empty")

            validation_started = time.monotonic()
            validation_error = ""
            schema = (
                stage.get("output_contract", {}).get("schema")
                if isinstance(stage.get("output_contract"), dict)
                else None
            )
            output_truncated = bool(getattr(response, "truncated", False)) or (
                finish_reason == "length"
            )
            array_continuation_attempted = False
            try:
                if (
                    output_truncated
                    and artifact.endswith(".json")
                    and isinstance(schema, dict)
                    and schema.get("type") == "array"
                ):
                    accepted_prefix = _salvage_truncated_json_array(raw_content)
                    previous_item_count = _json_array_item_count(current_artifact_seed)
                    target_count = max(
                        int(schema.get("minItems") or 0),
                        len(accepted_prefix),
                        previous_item_count,
                    )
                    if _json_array_has_unfinished_tail(raw_content):
                        target_count = max(target_count, len(accepted_prefix) + 1)
                    if not accepted_prefix or target_count <= 0:
                        raise ValueError("provider_output_truncated")
                    if len(accepted_prefix) < target_count:
                        rendered = list(accepted_prefix)
                        max_array_continuations = 2
                        while (
                            len(rendered) < target_count
                            and continuation_count < max_array_continuations
                        ):
                            array_continuation_attempted = True
                            remaining_count = target_count - len(rendered)
                            continuation_count += 1
                            repair_attempt_count = continuation_count
                            provider_call_count += 1
                            continuation_prompt = _json_array_continuation_prompt(
                                stage=stage,
                                existing_items=rendered,
                                remaining_count=remaining_count,
                                evidence_ids=_required_evidence_ids(source_pack, outline),
                                claim_evidence_catalog=claim_catalog,
                            )
                            prompt_name = (
                                "json_array_continuation_prompt.txt"
                                if continuation_count == 1
                                else f"json_array_continuation_prompt_{continuation_count}.txt"
                            )
                            _write_text(stage_dir / prompt_name, continuation_prompt)
                            await _emit_progress(
                                on_progress,
                                {
                                    "event_type": "stage_continuation_started",
                                    "stage_id": stage_id,
                                    "status": "running",
                                    "artifact": artifact,
                                    "attempt_count": attempt_count,
                                    "continuation_count": continuation_count,
                                    "output_characters": len(raw_content),
                                    "user_message": (
                                        f"{artifact} 已保留 {len(rendered)} 个完整条目，"
                                        f"正在补齐剩余 {remaining_count} 个条目"
                                    ),
                                },
                            )
                            remaining_total = max(
                                0.001,
                                policy.total_timeout_seconds
                                - (time.monotonic() - started),
                            )
                            continuation_started = time.monotonic()
                            continued = await _complete_with_cancellation(
                                llm=auxiliary_llm,
                                prompt=continuation_prompt,
                                max_tokens=min(
                                    policy.max_tokens,
                                    max(2400, remaining_count * 1800 + 800),
                                ),
                                is_cancelled=is_cancelled,
                                timeout_seconds=min(
                                    policy.provider_timeout_seconds,
                                    remaining_total,
                                ),
                                single_attempt=True,
                                on_detached_task=detached_provider_tasks.append,
                            )
                            continuation_ms = round(
                                (time.monotonic() - continuation_started) * 1000,
                                1,
                            )
                            repair_ms += continuation_ms
                            provider_wait_ms += continuation_ms
                            continued_content = str(
                                getattr(continued, "content", "") or ""
                            ).strip()
                            continuation_model = str(
                                getattr(continued, "model", "") or continuation_model
                            )
                            _write_text(
                                stage_dir
                                / f"raw_output_continuation_{continuation_count}.txt",
                                continued_content,
                            )
                            if not continued_content:
                                raise ValueError("json_continuation_empty")
                            if bool(getattr(continued, "truncated", False)):
                                additional_items = _salvage_truncated_json_array(
                                    continued_content
                                )
                            else:
                                try:
                                    additional_items = _render_stage_artifact(
                                        continued_content,
                                        artifact,
                                    )
                                except (RuntimeError, ValueError):
                                    additional_items = _salvage_truncated_json_array(
                                        continued_content
                                    )
                            if not isinstance(additional_items, list):
                                raise ValueError("json_continuation_not_array")
                            additional_items = _canonicalize_technical_claim_evidence(
                                additional_items,
                                claim_catalog,
                            )
                            before_count = len(rendered)
                            rendered = _merge_json_array_items(
                                rendered,
                                additional_items,
                            )
                            if len(rendered) == before_count:
                                raise ValueError("json_continuation_no_new_items")
                    else:
                        rendered = accepted_prefix
                    raw_content = json.dumps(rendered, ensure_ascii=False)
                    finish_reason = "json_array_continuation_stop"
                elif output_truncated:
                    raise ValueError("provider_output_truncated")
                else:
                    try:
                        rendered = _render_stage_artifact(raw_content, artifact)
                    except (RuntimeError, ValueError):
                        if not (
                            artifact.endswith(".json")
                            and isinstance(schema, dict)
                            and schema.get("type") == "array"
                        ):
                            raise
                        rendered = _salvage_truncated_json_array(raw_content)
                        target_count = int(schema.get("minItems") or 0)
                        if not rendered:
                            raise
                        if len(rendered) < target_count:
                            array_continuation_attempted = True
                            remaining_count = target_count - len(rendered)
                            continuation_count += 1
                            repair_attempt_count = continuation_count
                            provider_call_count += 1
                            continuation_prompt = _json_array_continuation_prompt(
                                stage=stage,
                                existing_items=rendered,
                                remaining_count=remaining_count,
                                evidence_ids=_required_evidence_ids(source_pack, outline),
                                claim_evidence_catalog=claim_catalog,
                            )
                            _write_text(
                                stage_dir / "json_array_salvage_prompt.txt",
                                continuation_prompt,
                            )
                            await _emit_progress(
                                on_progress,
                                {
                                    "event_type": "stage_continuation_started",
                                    "stage_id": stage_id,
                                    "status": "running",
                                    "artifact": artifact,
                                    "attempt_count": attempt_count,
                                    "continuation_count": continuation_count,
                                    "output_characters": len(raw_content),
                                    "user_message": (
                                        f"{artifact} 已保留 {len(rendered)} 个合法条目，"
                                        f"正在补齐 {remaining_count} 个格式受损条目"
                                    ),
                                },
                            )
                            remaining_total = max(
                                0.001,
                                policy.total_timeout_seconds
                                - (time.monotonic() - started),
                            )
                            continuation_started = time.monotonic()
                            continued = await _complete_with_cancellation(
                                llm=auxiliary_llm,
                                prompt=continuation_prompt,
                                max_tokens=min(
                                    policy.max_tokens,
                                    max(2400, remaining_count * 1800 + 800),
                                ),
                                is_cancelled=is_cancelled,
                                timeout_seconds=min(
                                    policy.provider_timeout_seconds,
                                    remaining_total,
                                ),
                                single_attempt=True,
                                on_detached_task=detached_provider_tasks.append,
                            )
                            continuation_ms = round(
                                (time.monotonic() - continuation_started) * 1000,
                                1,
                            )
                            repair_ms += continuation_ms
                            provider_wait_ms += continuation_ms
                            continued_content = str(
                                getattr(continued, "content", "") or ""
                            ).strip()
                            continuation_model = str(
                                getattr(continued, "model", "") or continuation_model
                            )
                            _write_text(
                                stage_dir / "raw_output_salvage_continuation.txt",
                                continued_content,
                            )
                            if not continued_content:
                                raise ValueError("json_salvage_continuation_empty")
                            additional_items = _render_stage_artifact(
                                continued_content,
                                artifact,
                            )
                            if not isinstance(additional_items, list):
                                raise ValueError("json_salvage_continuation_not_array")
                            additional_items = _canonicalize_technical_claim_evidence(
                                additional_items,
                                claim_catalog,
                            )
                            rendered = _merge_json_array_items(
                                rendered,
                                additional_items,
                            )
                            if len(rendered) < target_count:
                                raise ValueError(
                                    "json_salvage_continuation_missing_items"
                                )
                        raw_content = json.dumps(rendered, ensure_ascii=False)
                        finish_reason = "json_array_salvage_stop"

                if current_artifact_seed.strip() and isinstance(rendered, list):
                    previous_items = _json_array_items(current_artifact_seed)
                    if previous_items:
                        rendered = _apply_quality_feedback_field_patches(
                            rendered,
                            artifact=artifact,
                            quality_feedback=quality_feedback or {},
                            base_items=previous_items,
                        )
                        if not allow_new_repair_items:
                            missing_repair_rows = _missing_quality_repair_row_ids(
                                rendered,
                                allowed_existing_repair_row_ids or set(),
                            )
                            if missing_repair_rows:
                                raise ValueError(
                                    "quality_repair_missing_rows: "
                                    + ", ".join(sorted(missing_repair_rows))
                                )
                        # A quality repair is a field patch over the accepted array.
                        # Preserve its order and untouched fields so a partial model
                        # response cannot evict rows at the output item limit.
                        rendered = _merge_json_array_patch(
                            previous_items,
                            rendered,
                            allowed_existing_row_ids=allowed_existing_repair_row_ids,
                            allow_new_items=allow_new_repair_items,
                        )
                        if base_stage_id == "sfmea":
                            before_count = len(rendered)
                            rendered = _deduplicate_sfmea_semantic_categories(rendered)
                            if len(rendered) < before_count:
                                deterministic_repair_fields.append(
                                    "sfmea_semantic_duplicates_removed"
                                )

                rendered = _canonicalize_technical_claim_evidence(
                    rendered,
                    claim_catalog,
                )
                rendered = _apply_regular_stage_output_limits(
                    rendered,
                    stage,
                    minimum_items=(
                        len(rendered)
                        if current_artifact_seed.strip() and isinstance(rendered, list)
                        else 0
                    ),
                )

                if isinstance(schema, dict):
                    schema_errors = _validate_schema(rendered, schema)
                    if schema_errors:
                        repaired, repair_fields = _deterministic_schema_repair(
                            rendered,
                            schema,
                        )
                        repaired_errors = _validate_schema(repaired, schema)
                        if repair_fields and not repaired_errors:
                            rendered = repaired
                            deterministic_repair_fields = repair_fields
                            schema_errors = []
                            finish_reason = "deterministic_schema_repair"
                    if schema_errors:
                        raise ValueError(
                            "schema_invalid: " + "; ".join(schema_errors[:5])
                        )
            except (RuntimeError, ValueError) as exc:
                validation_error = str(exc) or exc.__class__.__name__
            validation_ms = round((time.monotonic() - validation_started) * 1000, 1)

            if validation_error:
                if array_continuation_attempted:
                    raise ValueError(validation_error)
                if not policy.allow_format_repair or not raw_content:
                    raise ValueError(validation_error)
                repair_attempt_count = 1
                provider_call_count += 1
                repair_prompt = _regular_stage_repair_prompt(
                    stage=stage,
                    raw_content=raw_content,
                    validation_error=validation_error,
                    evidence_ids=_required_evidence_ids(source_pack, outline),
                )
                _write_text(stage_dir / "repair_prompt.txt", repair_prompt)
                remaining_total = max(
                    0.001,
                    policy.total_timeout_seconds - (time.monotonic() - started),
                )
                repair_timeout = min(policy.repair_timeout_seconds, remaining_total)
                repair_started = time.monotonic()
                repaired = await _complete_with_cancellation(
                    llm=auxiliary_llm,
                    prompt=repair_prompt,
                    max_tokens=policy.repair_max_tokens,
                    is_cancelled=is_cancelled,
                    timeout_seconds=min(
                        repair_timeout,
                        max(
                            0.001,
                            policy.total_timeout_seconds - (time.monotonic() - started),
                        ),
                    ),
                    single_attempt=True,
                    on_detached_task=detached_provider_tasks.append,
                )
                repair_ms = round((time.monotonic() - repair_started) * 1000, 1)
                provider_wait_ms += repair_ms
                repaired_content = str(getattr(repaired, "content", "") or "").strip()
                continuation_model = str(
                    getattr(repaired, "model", "") or continuation_model
                )
                _write_text(stage_dir / "raw_output_repair.txt", repaired_content)
                if not repaired_content or bool(getattr(repaired, "truncated", False)):
                    raise ValueError("repair_output_invalid")
                rendered = _render_stage_artifact(repaired_content, artifact)
                if current_artifact_seed.strip() and isinstance(rendered, list):
                    previous_items = _json_array_items(current_artifact_seed)
                    if previous_items:
                        rendered = _apply_quality_feedback_field_patches(
                            rendered,
                            artifact=artifact,
                            quality_feedback=quality_feedback or {},
                            base_items=previous_items,
                        )
                        if not allow_new_repair_items:
                            missing_repair_rows = _missing_quality_repair_row_ids(
                                rendered,
                                allowed_existing_repair_row_ids or set(),
                            )
                            if missing_repair_rows:
                                raise ValueError(
                                    "quality_repair_missing_rows: "
                                    + ", ".join(sorted(missing_repair_rows))
                                )
                        rendered = _merge_json_array_patch(
                            previous_items,
                            rendered,
                            allowed_existing_row_ids=allowed_existing_repair_row_ids,
                            allow_new_items=allow_new_repair_items,
                        )
                        if base_stage_id == "sfmea":
                            before_count = len(rendered)
                            rendered = _deduplicate_sfmea_semantic_categories(rendered)
                            if len(rendered) < before_count:
                                deterministic_repair_fields.append(
                                    "sfmea_semantic_duplicates_removed"
                                )
                rendered = _canonicalize_technical_claim_evidence(
                    rendered,
                    claim_catalog,
                )
                rendered = _apply_regular_stage_output_limits(
                    rendered,
                    stage,
                    minimum_items=(
                        len(rendered)
                        if current_artifact_seed.strip() and isinstance(rendered, list)
                        else 0
                    ),
                )
                if isinstance(schema, dict):
                    schema_errors = _validate_schema(rendered, schema)
                    if schema_errors:
                        raise ValueError(
                            "repair_schema_invalid: " + "; ".join(schema_errors[:5])
                        )
                raw_content = repaired_content
                finish_reason = "repair_stop"
    except asyncio.TimeoutError:
        timed_out = True
        provider_wait_ms = round((time.monotonic() - provider_started) * 1000, 1)
        finish_reason = "provider_timeout"
        last_error = "provider_timeout"
        if policy.allow_degraded_output and output_path.is_file():
            status = "partial"
            rendered = output_path.read_text(encoding="utf-8", errors="replace")
        else:
            status = "failed"
    except StagedExecutionCancelled:
        raise
    except Exception as exc:
        provider_wait_ms = max(
            provider_wait_ms,
            round((time.monotonic() - provider_started) * 1000, 1),
        )
        last_error = str(exc) or exc.__class__.__name__
        finish_reason = "validation_error" if raw_content else "transport_error"
        if policy.allow_degraded_output and output_path.is_file():
            status = "partial"
            rendered = output_path.read_text(encoding="utf-8", errors="replace")
        else:
            status = "failed"
    finally:
        provider_capacity.release_after(detached_provider_tasks)

    output_contract = (
        stage.get("output_contract")
        if isinstance(stage.get("output_contract"), dict)
        else {}
    )
    if status == "completed" and isinstance(rendered, str) and artifact.endswith(".md"):
        rendered, removed_unverified_paths = _finalize_combined_markdown_report(
            content=rendered,
            source_pack=source_pack,
            output_contract=output_contract,
        )
        if removed_unverified_paths:
            deterministic_repair_fields.append("unverified_repo_paths")
        _write_text(output_path, rendered)

    if rendered is not None and status == "completed" and not (
        policy.streaming and not artifact.endswith(".json")
    ):
        if isinstance(rendered, str):
            _write_text(output_path, rendered)
        else:
            _write_json(output_path, rendered)
    if timed_out:
        await _emit_progress(
            on_progress,
            {
                "event_type": "stage_timed_out",
                "stage_id": stage_id,
                "status": status,
                "artifact": artifact,
                "output_characters": len(raw_content),
                "attempt_count": attempt_count,
                "remaining_seconds": 0,
                "can_retry": True,
                "user_message": (
                    f"{stage_id} 已达到时间预算，已保留 {len(raw_content)} 个字符的部分结果；"
                    "可继续生成或从本阶段重试。"
                ),
            },
        )
    total_duration_ms = round((time.monotonic() - started) * 1000, 1)
    generation_ms = max(0.0, round(provider_wait_ms - repair_ms, 1))
    result = {
        "stage_id": stage_id,
        "status": status,
        "artifact": artifact,
        "attempts": attempt_count,
        "attempt_count": attempt_count,
        "provider_call_count": provider_call_count,
            "continuation_count": continuation_count,
            "continuation_model": continuation_model,
        "full_retry_performed": False,
        "repair_attempt_count": repair_attempt_count,
        "deterministic_repair_fields": deterministic_repair_fields,
        "removed_unverified_paths": removed_unverified_paths,
        "max_full_attempts": policy.max_full_attempts,
        "prompt_characters_before_compaction": prompt_characters_before_compaction,
        "prompt_characters": prompt_characters,
        "prompt_estimated_tokens": prompt_estimated_tokens,
        "dependency_artifact_characters": dependency_artifact_characters,
        "queue_wait_ms": round(queue_wait_ms, 1),
        "provider_wait_ms": round(provider_wait_ms, 1),
        "time_to_first_token_ms": round(time_to_first_token_ms, 1),
        "generation_ms": generation_ms,
        "validation_ms": round(validation_ms, 1),
        "repair_ms": round(repair_ms, 1),
        "total_duration_ms": total_duration_ms,
        "duration_ms": total_duration_ms,
        "output_tokens": BaseLLMClient.estimate_tokens(raw_content) if raw_content else 0,
        "finish_reason": finish_reason,
        "degraded": status == "partial",
        "degradation_reason": last_error if status == "partial" else "",
        "cache_status": "miss" if cache is not None else "disabled",
        "cache_key": cache_key,
        "policy": policy.as_dict(),
        "size_bytes": output_path.stat().st_size if output_path.is_file() else 0,
        "model": model,
        "reason": last_error,
    }
    _write_json(stage_dir / "stage_result.json", result)
    if status == "failed" or not output_path.is_file():
        raise RuntimeError(
            f"阶段 {stage_id} 单次完整生成失败，已停止后续阶段：{last_error or finish_reason}"
        )
    store_regular_stage_cache(
        cache_root=cache,
        cache_key=cache_key,
        artifact=artifact,
        output_path=output_path,
        stage_result=result,
    )
    return {**result, "output_path": str(output_path)}


def _provider_wait_user_message(
    *,
    output_characters: int,
    elapsed_seconds: float,
    remaining_seconds: int,
    heartbeat_seconds: float,
) -> str:
    if output_characters:
        return "模型仍在生成，后端心跳正常"
    stalled_after = max(30.0, heartbeat_seconds * 2)
    if elapsed_seconds < stalled_after:
        return "模型已提交，正在等待首段输出"
    return f"Provider 尚未返回首段输出，系统将在剩余 {remaining_seconds} 秒后停止"


async def _stream_regular_markdown_stage(
    *,
    llm: Any,
    prompt: str,
    max_tokens: int,
    timeout_seconds: float,
    total_deadline: float,
    is_cancelled: CancellationCallback | None,
    on_progress: ProgressCallback | None,
    stage_id: str,
    artifact: str,
    stage_dir: Path,
    output_path: Path,
    deterministic_base: str,
    partial_seed: str,
    on_detached_task: Callable[[asyncio.Task[Any]], None] | None = None,
) -> dict[str, Any]:
    provider_started = time.monotonic()
    partial_path = stage_dir / f"{Path(artifact).name}.partial"
    _write_text(partial_path, partial_seed)
    content = partial_seed
    first_token_ms = 0.0
    last_checkpoint_chars = len(content)
    timed_out = False
    finish_reason = ""
    model = str(getattr(llm, "_model", "") or "")
    stream_method = getattr(llm, "stream_complete", None)
    has_direct_stream = callable(stream_method) and (
        not isinstance(llm, BaseLLMClient)
        or type(llm).stream_complete is not BaseLLMClient.stream_complete
    )

    async def chunks():
        if has_direct_stream:
            async for delta in stream_method(
                [{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.2,
            ):
                yield str(delta)
            return
        complete = (
            getattr(llm, "complete_once")
            if callable(getattr(llm, "complete_once", None))
            else llm.complete
        )
        response = await complete(
            [{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.2,
        )
        nonlocal model, finish_reason
        model = str(getattr(response, "model", "") or model)
        finish_reason = str(
            getattr(response, "finish_reason", "")
            or ("length" if bool(getattr(response, "truncated", False)) else "stop")
        )
        if getattr(response, "content", ""):
            yield str(response.content)

    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    async def consume() -> None:
        try:
            current_finish_reason.set(None)
            async for delta in chunks():
                await queue.put(("delta", delta))
            await queue.put(
                (
                    "done",
                    {
                        "finish_reason": current_finish_reason.get() or finish_reason,
                        "model": model,
                    },
                )
            )
        except BaseException as exc:
            await queue.put(("error", exc))

    consumer = asyncio.create_task(consume())
    provider_deadline = min(total_deadline, provider_started + timeout_seconds)
    heartbeat_seconds = max(0.01, float(settings.regular_stage_heartbeat_seconds))
    last_heartbeat_at = provider_started - heartbeat_seconds
    pending_delta = ""
    last_delta_emit_at = provider_started

    async def emit_pending_delta(*, force: bool = False) -> None:
        nonlocal pending_delta, last_delta_emit_at
        if not pending_delta:
            return
        if (
            not force
            and len(pending_delta) < 240
            and time.monotonic() - last_delta_emit_at < 0.25
        ):
            return
        delta = pending_delta
        pending_delta = ""
        last_delta_emit_at = time.monotonic()
        for offset in range(0, len(delta), 1000):
            await _emit_progress(
                on_progress,
                {
                    "event_type": "stage_output_delta",
                    "stage_id": stage_id,
                    "status": "running",
                    "artifact": artifact,
                    "delta": delta[offset : offset + 1000],
                    "output_characters": len(content),
                    "user_message": f"当前已生成 {len(content)} 个字符",
                },
            )

    async def emit_heartbeat_if_due() -> None:
        nonlocal last_heartbeat_at
        now = time.monotonic()
        if now - last_heartbeat_at < heartbeat_seconds:
            return
        last_heartbeat_at = now
        remaining_seconds = max(0, int(provider_deadline - now))
        elapsed_seconds = round(now - provider_started, 1)
        await _emit_progress(
            on_progress,
            {
                "event_type": "stage_heartbeat",
                "stage_id": stage_id,
                "status": "running",
                "artifact": artifact,
                "output_characters": len(content),
                "remaining_seconds": remaining_seconds,
                "last_activity_seconds": elapsed_seconds,
                "user_message": _provider_wait_user_message(
                    output_characters=len(content),
                    elapsed_seconds=elapsed_seconds,
                    remaining_seconds=remaining_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                ),
            },
        )

    try:
        while True:
            if await _callback_true(is_cancelled):
                raise StagedExecutionCancelled("任务已取消，已停止当前模型流和后续阶段")
            remaining = provider_deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                finish_reason = "provider_timeout"
                break
            await emit_heartbeat_if_due()
            try:
                kind, value = await asyncio.wait_for(
                    queue.get(),
                    timeout=min(
                        heartbeat_seconds,
                        remaining,
                        _CANCELLATION_POLL_INTERVAL,
                    ),
                )
            except asyncio.TimeoutError:
                await emit_heartbeat_if_due()
                continue
            if kind == "error":
                if isinstance(value, asyncio.CancelledError):
                    raise value
                raise value
            if kind == "done":
                if isinstance(value, dict):
                    finish_reason = str(value.get("finish_reason") or "stop")
                    model = str(value.get("model") or model)
                break
            delta = str(value or "")
            if not delta:
                continue
            if not first_token_ms:
                first_token_ms = round((time.monotonic() - provider_started) * 1000, 1)
                await _emit_progress(
                    on_progress,
                    {
                        "event_type": "stage_first_token",
                        "stage_id": stage_id,
                        "status": "running",
                        "artifact": artifact,
                        "time_to_first_token_ms": first_token_ms,
                        "user_message": "已收到首段输出",
                    },
                )
            content += delta
            with partial_path.open("a", encoding="utf-8") as handle:
                handle.write(delta)
            pending_delta += delta
            await emit_pending_delta()
            await emit_heartbeat_if_due()
            if (
                len(content) - last_checkpoint_chars
                >= int(settings.business_flow_checkpoint_characters)
                or not last_checkpoint_chars
            ):
                _write_streamed_markdown(
                    output_path=output_path,
                    deterministic_base=deterministic_base,
                    narrative=content,
                    partial=True,
                )
                last_checkpoint_chars = len(content)
                await _emit_progress(
                    on_progress,
                    {
                        "event_type": "stage_output_checkpoint",
                        "stage_id": stage_id,
                        "status": "running",
                        "artifact": artifact,
                        "output_characters": len(content),
                        "user_message": f"已保存 {len(content)} 个字符的阶段检查点",
                    },
                )
    finally:
        await emit_pending_delta(force=True)
        terminated = await _cancel_task_bounded(consumer)
        if not terminated and on_detached_task is not None:
            on_detached_task(consumer)
    if content and len(content) != last_checkpoint_chars:
        await _emit_progress(
            on_progress,
            {
                "event_type": "stage_output_checkpoint",
                "stage_id": stage_id,
                "status": "partial" if timed_out else "running",
                "artifact": artifact,
                "output_characters": len(content),
                "user_message": f"已保存 {len(content)} 个字符的阶段检查点",
            },
        )
    _write_streamed_markdown(
        output_path=output_path,
        deterministic_base=deterministic_base,
        narrative=content,
        partial=timed_out,
    )
    _write_text(stage_dir / "raw_output_attempt_1.txt", content)
    _write_text(stage_dir / "raw_output.txt", content)
    return {
        "content": content,
        "provider_wait_ms": round((time.monotonic() - provider_started) * 1000, 1),
        "time_to_first_token_ms": first_token_ms,
        "finish_reason": finish_reason or "stop",
        "timed_out": timed_out,
        "model": model,
    }


def _write_streamed_markdown(
    *,
    output_path: Path,
    deterministic_base: str,
    narrative: str,
    partial: bool,
) -> None:
    heading = "## 模型叙述增强（部分）" if partial else "## 模型叙述增强"
    narrative = (
        narrative.strip()
        if partial
        else _extract_business_flow_narrative(narrative)
    )
    if deterministic_base:
        body = deterministic_base.rstrip()
        if narrative:
            body += "\n\n" + heading + "\n\n" + narrative
    else:
        body = narrative
    _write_text(output_path, body.rstrip() + "\n")


def _business_flow_deterministic_base(
    *,
    outline: dict[str, Any],
    existing_content: str,
) -> str:
    """Rebuild the trusted flow base; an earlier model narrative is never a base."""
    if outline:
        return render_business_flow_markdown(outline)
    return existing_content


def _extract_business_flow_narrative(content: str) -> str:
    """Keep the user-facing Markdown and discard model planning commentary."""
    text = str(content or "").strip()
    if not text:
        return ""
    fenced = [
        match.group(1).strip()
        for match in re.finditer(
            r"```(?:markdown|md)\s*\n([\s\S]*?)\n```",
            text,
            re.IGNORECASE,
        )
        if re.search(r"(?m)^#{1,6}\s+\S", match.group(1))
    ]
    if fenced:
        text = max(fenced, key=len)
    headings = list(re.finditer(r"(?m)^#{1,3}\s+(.+?)\s*$", text))
    if not headings:
        return re.sub(r"\n```\s*$", "", text).strip()
    preferred = [
        match
        for match in headings
        if any(
            marker in match.group(1).lower()
            for marker in ("业务流程", "流程补充", "business flow", "login")
        )
    ]
    start = (preferred[0] if preferred else headings[0]).start()
    narrative = text[start:].strip()
    meta_markers = [
        r"我们被要求(?:修复|对)",
        r"PARTIAL_OUTPUT_TO_CONTINUE",
        r"CURRENT_ARTIFACT_TO_REPAIR",
        r"MANDATORY_QUALITY_REPAIR_CHECKLIST",
        r"修改后的产物[:：]\s*复制",
    ]
    marker_offsets = [
        match.start()
        for pattern in meta_markers
        if (match := re.search(pattern, narrative, flags=re.IGNORECASE))
    ]
    if marker_offsets:
        narrative = narrative[: min(marker_offsets)].rstrip()
    return re.sub(r"\n```\s*$", "", narrative).strip()


def _business_flow_stage_prompt(
    *,
    plan: dict[str, Any],
    stage: dict[str, Any],
    source_pack: dict[str, Any],
    flow_pack: dict[str, Any],
    outline: dict[str, Any],
    partial_seed: str = "",
    current_artifact_seed: str = "",
) -> str:
    compact = build_business_flow_context(
        plan=plan,
        source_pack=source_pack,
        flow_pack=flow_pack,
        outline=outline,
    )
    compact["execution_inputs"] = _compact_execution_input_contract(
        plan.get("execution_input_contract")
    )
    artifact = str(stage.get("artifact") or "business_flow.md")
    quality_feedback = plan.get("quality_retry_feedback")
    affected_artifacts = {
        Path(str(value)).name
        for value in (quality_feedback or {}).get("affected_artifacts") or []
        if str(value).strip()
    } if isinstance(quality_feedback, dict) else set()
    scoped_quality_feedback = (
        _quality_feedback_for_artifact(quality_feedback, artifact)
        if isinstance(quality_feedback, dict)
        and Path(artifact).name in affected_artifacts
        else None
    )
    if scoped_quality_feedback:
        compact["quality_retry_feedback"] = _compact_stage_value(
            scoped_quality_feedback
        )
    parts = [
        f"STAGE_ID: {stage.get('id')}",
        f"OUTPUT_ARTIFACT: {stage.get('artifact')}",
        "PURPOSE: 仅对已验证 Flow Outline 做公开叙述增强",
        "BUSINESS_FLOW_CONTEXT:",
        json.dumps(compact, ensure_ascii=False, indent=2),
        "",
        "RULES:",
        "- 只补充外部触发、主流程、分支、异常、清理、恢复和观测点。",
        "- 每个事实引用 required_evidence_ids 中的证据；缺证据必须明确标记。",
        "- 不得重新发现源码，不得生成 SFMEA、黑盒用例或其他交付件。",
        "- 不要输出思维链、终端初始化信息或 artifact 容器。",
        "- 返回 Markdown 叙述片段；确定性流程图和表格已由系统生成。",
    ]
    if scoped_quality_feedback and current_artifact_seed.strip():
        parts.extend(
            [
                "- CURRENT_ARTIFACT_TO_REPAIR 是已通过部分校验的上一版流程；保留其正确内容，只补充或纠正门禁指出的场景。",
                "- 只返回面向用户的最终流程叙述；不要描述旧版本、修复动作、修改说明或要求系统把文字补到其他段落。",
                "- 不得输出‘请在上述内容补充’、‘错误描述修正’、‘之前版本’或类似编辑指令；直接陈述校正后的事实、分支和证据。",
                "",
                "CURRENT_ARTIFACT_TO_REPAIR:",
                current_artifact_seed[:60_000],
            ]
        )
    if scoped_quality_feedback:
        checklist: list[str] = []
        for raw_issue in scoped_quality_feedback.get("issues") or []:
            if not isinstance(raw_issue, dict):
                continue
            code = str(raw_issue.get("code") or "quality_issue").strip()
            message = str(
                raw_issue.get("message") or raw_issue.get("reason") or ""
            ).strip()
            scenarios = [
                str(value).strip()
                for value in raw_issue.get("scenarios") or []
                if str(value).strip()
            ]
            if scenarios:
                message = f"{message}；必须覆盖: {', '.join(scenarios)}".strip("；")
            checklist.append(f"- [{code}] {message}".strip())
        if checklist:
            parts.extend(
                [
                    "",
                    "MANDATORY_QUALITY_REPAIR_CHECKLIST:",
                    "- 以下每一项必须在本次流程叙述中明确补齐，不得只解释或承诺。",
                    *checklist,
                ]
            )
    if partial_seed:
        parts.extend(
            [
                "",
                "PARTIAL_NARRATIVE_TO_CONTINUE:",
                partial_seed[-6000:],
                "",
                "- 从未完成处继续，禁止重复上述部分。",
            ]
        )
    return "\n".join(parts)


_MARKDOWN_REPO_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_/])(?:lib|test|include|module|app)/"
    r"[A-Za-z0-9_.+@%/\-]+(?::L?\d+(?:-L?\d+)?)?"
)


def _split_markdown_table_cells(line: str) -> list[str]:
    text = str(line or "").strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith(r"\|"):
        text = text[:-1]
    cells: list[str] = []
    current: list[str] = []
    in_code = False
    escaped = False
    for character in text:
        if escaped:
            current.append(character)
            escaped = False
            continue
        if character == "\\":
            current.append(character)
            escaped = True
            continue
        if character == "`":
            in_code = not in_code
            current.append(character)
            continue
        if character == "|" and not in_code:
            cells.append("".join(current).strip())
            current = []
            continue
        current.append(character)
    cells.append("".join(current).strip())
    return cells


def _repair_duplicated_markdown_table_prefixes(content: str) -> str:
    """Repair only table rows with a provably duplicated first-cell prefix."""
    lines = str(content or "").splitlines()
    fenced = False
    fence_state: list[bool] = []
    for line in lines:
        fence_state.append(fenced)
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fenced = not fenced

    for delimiter_index, delimiter in enumerate(lines):
        if fence_state[delimiter_index]:
            continue
        delimiter_cells = _split_markdown_table_cells(delimiter)
        if len(delimiter_cells) < 2 or not all(
            re.fullmatch(r":?-{3,}:?", cell.strip())
            for cell in delimiter_cells
        ):
            continue
        header_index = delimiter_index - 1
        while header_index >= 0 and not lines[header_index].strip():
            header_index -= 1
        if header_index < 0 or fence_state[header_index]:
            continue
        expected_cells = len(_split_markdown_table_cells(lines[header_index]))
        if expected_cells < 2:
            continue
        row_index = delimiter_index + 1
        while row_index < len(lines):
            row = lines[row_index].strip()
            if (
                not row
                or row.startswith("#")
                or row.startswith(("```", "~~~"))
                or not row.startswith("|")
            ):
                break
            if not row.endswith("|") and row_index + 1 < len(lines):
                continuation = lines[row_index + 1].strip()
                joined = row + "<br>" + continuation
                if (
                    continuation
                    and not continuation.startswith(("|", "#", "```", "~~~"))
                    and joined.endswith("|")
                    and len(_split_markdown_table_cells(joined)) == expected_cells
                ):
                    indent = lines[row_index][
                        : len(lines[row_index]) - len(lines[row_index].lstrip())
                    ]
                    lines[row_index] = indent + joined
                    lines[row_index + 1] = ""
                    row = joined
            cells = _split_markdown_table_cells(row)
            if len(cells) > expected_cells:
                first_cell = cells[0].strip()
                for duplicate_start in range(1, len(cells) - expected_cells + 1):
                    candidate = cells[duplicate_start:]
                    if (
                        len(candidate) == expected_cells
                        and candidate[0].strip() == first_cell
                    ):
                        indent = lines[row_index][
                            : len(lines[row_index]) - len(lines[row_index].lstrip())
                        ]
                        lines[row_index] = (
                            indent + "| " + " | ".join(candidate) + " |"
                        )
                        break
            row_index += 1
    trailing_newline = "\n" if str(content or "").endswith("\n") else ""
    return "\n".join(lines) + trailing_newline


def _finalize_combined_markdown_report(
    *,
    content: str,
    source_pack: dict[str, Any],
    output_contract: dict[str, Any],
    extract_delivery_body: bool = True,
) -> tuple[str, list[str]]:
    """Enforce the verified evidence boundary before a combined report ships."""
    if not (
        output_contract.get("min_sfmea_rows")
        or output_contract.get("min_black_box_cases")
    ):
        return content, []
    if extract_delivery_body:
        content = _extract_markdown_delivery_body(
            content,
            required_sections=[
                str(value) for value in output_contract.get("sections") or []
                if str(value).strip()
            ],
        )
    if content.startswith("## "):
        content = "# 测试分析报告\n\n" + content
    cards = [
        item
        for item in source_pack.get("evidence_cards") or []
        if isinstance(item, dict) and str(item.get("file_path") or "").strip()
    ]
    allowed_paths = {str(item.get("file_path") or "").strip() for item in cards}
    removed: list[str] = []

    def replace_unverified(match: re.Match[str]) -> str:
        original = match.group(0)
        path = re.sub(r":L?\d+(?:-L?\d+)?$", "", original)
        if path in allowed_paths:
            return original
        if path not in removed:
            removed.append(path)
        return f"待补证据（{Path(path).name} 未在已验证证据包中）"

    content = _repair_duplicated_markdown_table_prefixes(content)
    finalized = _MARKDOWN_REPO_PATH_PATTERN.sub(replace_unverified, content).rstrip()
    index_lines = [
        "",
        "## 已验证证据索引（系统核验）",
        "",
        "以下证据均由 CodeTalk 本地读取并完成 SHA256 校验；报告中的源码与测试路径仅允许引用此索引。",
        "",
    ]
    for card in cards:
        path = str(card.get("file_path") or "").strip()
        start_line = int(card.get("start_line") or 0)
        end_line = int(card.get("end_line") or 0)
        anchor = path
        if start_line > 0 and end_line >= start_line:
            anchor = f"{path}:{start_line}-{end_line}"
        symbols = ", ".join(str(value) for value in card.get("symbols") or [])
        digest = str(card.get("sha256") or "")
        index_lines.append(
            f"- `{anchor}` · {card.get('classification') or 'source'} · "
            f"symbols: {symbols or '未提取'} · sha256: `{digest}`"
        )
    if removed:
        index_lines.extend(
            [
                "",
                f"系统已将 {len(removed)} 条不在证据包中的路径引用降级为待补证据，未将其作为事实依据。",
            ]
        )
    return finalized + "\n" + "\n".join(index_lines).rstrip() + "\n", removed


def _extract_markdown_delivery_body(
    content: str,
    *,
    required_sections: list[str] | None = None,
) -> str:
    fenced = [
        match.group(1).strip()
        for match in re.finditer(
            r"```(?:markdown|md)\s*\n([\s\S]*?)\n```",
            content,
            re.IGNORECASE,
        )
        if re.search(r"(?m)^#{1,6}\s+\S", match.group(1))
    ]
    if fenced:
        return max(fenced, key=len)
    heading = re.search(r"(?m)^#\s+\S", content)
    if heading:
        return re.sub(r"\n```\s*$", "", content[heading.start():].strip()).strip()
    normalized_sections = [
        str(value).strip().lower()
        for value in required_sections or []
        if str(value).strip()
    ]
    if normalized_sections:
        for candidate in re.finditer(r"(?m)^#{2,6}\s+(.+?)\s*$", content):
            title = re.sub(
                r"^\s*\d+(?:\.\d+)*(?:[.、)]\s*|\s+)",
                "",
                candidate.group(1),
            ).strip().lower()
            if any(section in title or title in section for section in normalized_sections):
                return re.sub(
                    r"\n```\s*$",
                    "",
                    content[candidate.start():].strip(),
                ).strip()
    return content.strip()


def _build_verified_claim_catalog(
    source_pack: dict[str, Any],
    *,
    max_entries: int = 128,
    requested_evidence_ids: set[str] | None = None,
) -> list[dict[str, str]]:
    """Project verified excerpts into exact, model-selectable source anchors."""
    catalog: list[dict[str, str]] = []
    seen: set[tuple[str, int, str]] = set()
    for raw_card in source_pack.get("evidence_cards") or []:
        if not isinstance(raw_card, dict):
            continue
        card_id = str(raw_card.get("evidence_id") or "").strip()
        path = str(raw_card.get("file_path") or "").strip()
        excerpt = str(raw_card.get("excerpt") or "")
        start_line = int(raw_card.get("start_line") or 0)
        if not card_id or not path or not excerpt or start_line <= 0:
            continue
        symbols = [
            str(value).strip()
            for value in raw_card.get("symbols") or []
            if str(value).strip()
        ]
        candidates: list[tuple[int, int, str, str]] = []
        for offset, raw_line in enumerate(excerpt.splitlines()):
            quote = raw_line.strip()
            if not quote or quote in {"{", "}", "};", ";"}:
                continue
            if quote.startswith(("/*", "*", "//")):
                continue
            line_number = start_line + offset
            symbol = next((value for value in symbols if value in quote), "")
            is_fact_literal = quote.startswith("#define ") or any(
                marker in quote
                for marker in ("SPDK_ERRLOG(", "SPDK_WARNLOG(", "SPDK_NOTICELOG(")
            )
            is_state_fact = bool(
                re.search(
                    r"\b(?:status_class|status_detail|state|authenticated)\s*=",
                    quote,
                )
            )
            priority = (
                0
                if is_fact_literal
                else 1
                if symbol or is_state_fact
                else 2
            )
            candidates.append((priority, line_number, quote[:500], symbol))
        candidates.sort(key=lambda value: (value[0], value[1]))
        per_card_limit = (
            16
            if any(
                item[2].startswith("#define ") or "SPDK_" in item[2]
                for item in candidates
            )
            else 6
        )
        selected_candidates = (
            [
                item
                for item in candidates
                if _requested_claim_evidence_matches(
                    f"{card_id}:L{item[1]}",
                    requested_evidence_ids,
                )
            ]
            if requested_evidence_ids
            else candidates[:per_card_limit]
        )
        for _, line_number, quote, symbol in selected_candidates:
            dedupe_key = (path, line_number, quote)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            catalog.append(
                {
                    "evidence_id": f"{card_id}:L{line_number}",
                    "path": path,
                    "symbol": symbol,
                    "lines": f"L{line_number}",
                    "quote": quote,
                }
            )
            if len(catalog) >= max_entries:
                return catalog
    return catalog


def _requested_claim_evidence_matches(
    candidate: str,
    requested: set[str],
) -> bool:
    """Match exact claim lines while still accepting an explicit card-wide ID."""
    value = str(candidate or "").strip()
    for raw_expected in requested:
        expected = str(raw_expected or "").strip()
        if not expected:
            continue
        if ":L" in expected:
            if value == expected:
                return True
        elif value == expected or value.startswith(f"{expected}:L"):
            return True
    return False


def _canonicalize_technical_claim_evidence(
    rendered: Any,
    catalog: list[dict[str, str]],
) -> Any:
    """Replace model-copied evidence fields with their verified canonical values."""
    by_id = {
        str(item.get("evidence_id") or ""): dict(item)
        for item in catalog
        if str(item.get("evidence_id") or "")
    }
    if not isinstance(rendered, list):
        return rendered
    for row in rendered:
        if not isinstance(row, dict):
            continue
        claims = row.get("technical_claims")
        if not isinstance(claims, list):
            continue
        preferred_claim = next(
            (
                claim
                for claim in claims
                if isinstance(claim, dict)
                and any(
                    isinstance(evidence, dict)
                    and str(evidence.get("evidence_id") or "") in by_id
                    for evidence in claim.get("evidence") or []
                )
            ),
            next((claim for claim in claims if isinstance(claim, dict)), None),
        )
        if preferred_claim is None:
            continue
        claims = [preferred_claim]
        row["technical_claims"] = claims
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            evidence_items = claim.get("evidence")
            if not isinstance(evidence_items, list):
                continue
            preferred_evidence = next(
                (
                    evidence
                    for evidence in evidence_items
                    if isinstance(evidence, dict)
                    and str(evidence.get("evidence_id") or "") in by_id
                ),
                next(
                    (
                        evidence
                        for evidence in evidence_items
                        if isinstance(evidence, dict)
                    ),
                    None,
                ),
            )
            if preferred_evidence is None:
                continue
            evidence_items = [preferred_evidence]
            claim["evidence"] = evidence_items
            for index, evidence in enumerate(evidence_items):
                if not isinstance(evidence, dict):
                    continue
                canonical = by_id.get(str(evidence.get("evidence_id") or ""))
                if canonical:
                    evidence_items[index] = dict(canonical)
    return rendered


def _apply_regular_stage_output_limits(
    rendered: Any,
    stage: dict[str, Any],
    *,
    minimum_items: int = 0,
) -> Any:
    output_limits = (
        stage.get("output_limits")
        if isinstance(stage.get("output_limits"), dict)
        else {}
    )
    max_items = int(output_limits.get("max_items") or 0)
    max_items = max(max_items, int(minimum_items or 0))
    if isinstance(rendered, list) and max_items > 0 and len(rendered) > max_items:
        return rendered[:max_items]
    return rendered


def _regular_stage_prompt(
    *,
    plan: dict[str, Any],
    stage: dict[str, Any],
    source_pack: dict[str, Any],
    flow_pack: dict[str, Any],
    outline: dict[str, Any],
    completed: dict[str, Path],
    partial_seed: str = "",
    current_artifact_seed: str = "",
) -> str:
    artifact = str(stage.get("artifact") or "")
    base_stage_id = str(stage.get("id") or "stage").split("__", 1)[0]
    compact_flow = build_business_flow_context(
        plan=plan,
        source_pack=source_pack,
        flow_pack=flow_pack,
        outline=outline,
    )
    verified_literal_facts = [
        dict(item)
        for item in source_pack.get("verified_literals") or []
        if isinstance(item, dict)
    ][:24]
    dependencies = {str(value) for value in stage.get("depends_on") or []}
    accepted_dependencies: dict[str, Any] = {}
    for stage_id, path in completed.items():
        if stage_id not in dependencies or not path.is_file():
            continue
        if stage_id in {"source_analysis", "flow_evidence_pack", "flow_outline"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() == ".json":
            try:
                accepted_dependencies[path.name] = _compact_stage_value(json.loads(text))
            except json.JSONDecodeError:
                accepted_dependencies[path.name] = text[:6000]
        else:
            accepted_dependencies[path.name] = text[:6000]
    context = {
        "version": "regular-stage-context-v2",
        "analysis_target": str(
            plan.get("original_user_request") or plan.get("target") or ""
        )[:2400],
        "repo_revision": str(source_pack.get("repo_revision") or ""),
        "verified_evidence_cards": compact_flow.get("verified_evidence_cards") or [],
        "verified_evidence_anchors": [
            (
                f"{card.get('file_path')}:{int(card.get('start_line') or 0)}"
                f"-{int(card.get('end_line') or 0)}"
            )
            for card in compact_flow.get("verified_evidence_cards") or []
            if isinstance(card, dict) and card.get("file_path")
        ],
        "flow_evidence_pack": compact_flow.get("flow_evidence_pack") or {},
        "flow_evidence_artifact": "flow_evidence_pack.json",
        "flow_outline": compact_flow.get("flow_outline") or {},
        "flow_outline_artifact": "flow_outline.json",
        "input_materials": compact_flow.get("input_materials") or [],
        "execution_inputs": _compact_execution_input_contract(
            plan.get("execution_input_contract")
        ),
        "accepted_dependencies": accepted_dependencies,
    }
    quality_feedback = plan.get("quality_retry_feedback")
    affected_artifacts = {
        str(value)
        for value in (quality_feedback or {}).get("affected_artifacts") or []
    } if isinstance(quality_feedback, dict) else set()
    scoped_quality_feedback: dict[str, Any] | None = None
    repair_prompt_seed = current_artifact_seed[:60_000]
    repair_evidence_ids: set[str] = set()
    repair_relevant_cards: list[dict[str, Any]] = []
    if isinstance(quality_feedback, dict) and artifact in affected_artifacts:
        scoped_quality_feedback = _quality_feedback_for_artifact(
            quality_feedback, artifact
        )
        repair_prompt_seed = _quality_repair_prompt_seed(
            current_artifact_seed=current_artifact_seed,
            artifact=artifact,
            quality_feedback=scoped_quality_feedback,
        )
        repair_evidence_ids = _quality_repair_evidence_ids(
            quality_feedback=scoped_quality_feedback,
            repair_prompt_seed=repair_prompt_seed,
        )
        feedback_text = json.dumps(
            scoped_quality_feedback, ensure_ascii=False, sort_keys=True
        ).lower()
        evidence_cards = [
            card
            for card in source_pack.get("evidence_cards") or []
            if isinstance(card, dict)
        ]
        relevant_cards = _quality_repair_evidence_cards(
            evidence_cards=evidence_cards,
            evidence_ids=repair_evidence_ids,
            feedback_text=feedback_text,
        )
        if not relevant_cards:
            relevant_cards = evidence_cards[:4]
        repair_relevant_cards = relevant_cards[:8]
        context = {
            "version": "regular-stage-quality-repair-context-v1",
            "analysis_target": str(
                plan.get("original_user_request") or plan.get("target") or ""
            )[:1200],
            "repo_revision": str(source_pack.get("repo_revision") or ""),
            "verified_evidence_cards": [
                _compact_quality_repair_evidence_card(card)
                for card in repair_relevant_cards
            ],
            "input_materials": _compact_quality_repair_input_materials(
                compact_flow.get("input_materials") or []
            ),
        }
    output_contract = (
        stage.get("output_contract")
        if isinstance(stage.get("output_contract"), dict)
        else {}
    )
    prompt_evidence_cards = (
        repair_relevant_cards
        if scoped_quality_feedback is not None
        else [
            card
            for card in source_pack.get("evidence_cards") or []
            if isinstance(card, dict)
        ]
    )
    verified_repo_paths = [
        str(card.get("file_path") or "").strip()
        for card in prompt_evidence_cards
        if isinstance(card, dict) and str(card.get("file_path") or "").strip()
    ]
    if scoped_quality_feedback is not None:
        verified_literal_facts = [
            dict(item)
            for item in source_pack.get("verified_literals") or []
            if isinstance(item, dict)
            and _evidence_id_matches(
                str(item.get("evidence_id") or ""),
                repair_evidence_ids,
            )
        ][:8]
    output_limits = (
        stage.get("output_limits")
        if isinstance(stage.get("output_limits"), dict)
        else {}
    )
    claim_catalog = _build_verified_claim_catalog(source_pack)
    if scoped_quality_feedback and repair_evidence_ids:
        repair_claim_catalog = _build_verified_claim_catalog(
            source_pack,
            requested_evidence_ids=repair_evidence_ids,
        )
        scoped_claim_catalog = [
            item
            for item in repair_claim_catalog
            if _evidence_id_matches(
                str(item.get("evidence_id") or ""),
                repair_evidence_ids,
            )
        ]
        claim_catalog = scoped_claim_catalog
    source_bound_domain_facts = _source_bound_domain_facts(
        plan=plan,
        source_pack=source_pack,
    )
    if scoped_quality_feedback is not None:
        relevant_card_ids = {
            str(card.get("evidence_id") or "").strip()
            for card in repair_relevant_cards
            if str(card.get("evidence_id") or "").strip()
        }
        source_bound_domain_facts = [
            fact
            for fact in source_bound_domain_facts
            if any(
                _evidence_id_matches(
                    str(evidence_id or ""),
                    relevant_card_ids,
                )
                for reference in fact.get("evidence") or []
                if isinstance(reference, dict)
                for evidence_id in reference.get("evidence_ids") or []
            )
        ][:8]
    rules = [
        "- 只使用当前紧凑上下文中的已验证源码、测试与上游产物，不重新发现源码。",
        "- 任何源码或测试路径都必须逐字来自该白名单 VERIFIED_REPO_PATH_ALLOWLIST；白名单外路径只能写成待补证据，不得输出为路径。",
        "- 用户目标必须完整执行；证据不足时明确写待验证，不得补造事实。",
        "- 协议状态码、超时秒数、容量阈值和日志原文只有逐字出现在证据片段或 VERIFIED_LITERAL_FACTS 时才能写成事实；不得用经验值替代。",
        "- 一个函数证据只支持片段中实际出现的行为；禁止仅凭函数名推断资源泄漏、常量时间比较、NULL 风险或连接关闭。",
        "- 上游产物与已验证证据矛盾时必须以证据为准并在当前产物中纠正，不得原样继承矛盾。",
        "- SOURCE_BOUND_DOMAIN_FACTS 是已绑定当前源码路径/符号的领域事实；生成与修复不得与其矛盾，但它与已验证源码片段矛盾时以源码片段为准。",
        "- 只返回当前 artifact 的顶层值，不输出思维链、终端信息或 artifact 容器。",
        *_stage_format_rules(base_stage_id, artifact),
    ]
    if base_stage_id in {"sfmea", "black_box_cases"}:
        rules.extend(
            [
                "- 每个条目只能包含一个 technical_claims 条目，每个 claim 只能引用一个 evidence。",
                "- technical claim 只能逐字选择一个 evidence_id，并完整复制 VERIFIED_CLAIM_EVIDENCE_CATALOG 中对应对象；禁止填写省略号、推测、Function not inspected、No excerpt 或自造 quote。",
                "- 没有证据支持的事实必须改写为证据缺口或测试假设，不能伪造 technical claim。",
            ]
        )
    schema = output_contract.get("schema") if isinstance(output_contract, dict) else None
    is_array_quality_patch = bool(
        current_artifact_seed.strip()
        and scoped_quality_feedback
        and isinstance(schema, dict)
        and schema.get("type") == "array"
    )
    allow_new_repair_items = _quality_repair_allows_new_items(
        artifact=artifact,
        quality_feedback=scoped_quality_feedback or {},
    )
    quality_issue_codes = {
        str(issue.get("code") or "")
        for issue in (scoped_quality_feedback or {}).get("issues") or []
        if isinstance(issue, dict)
    }
    if is_array_quality_patch:
        rules.extend(
            [
                "- CURRENT_ARTIFACT_TO_REPAIR 是已通过部分校验的上一版数组；仅返回需要新增或替换的独立条目数组，不要完整重写上一版数组。",
                "- 系统会按稳定 ID 合并补丁与上一版条目并保留未被门禁否定的条目；替换条目必须沿用原 ID，新增条目必须使用唯一 ID。",
                "- 质量清单中的每个必测场景必须是一个独立条目，不得把多个场景合并在同一个条目中。",
                "- 场景名称必须逐字包含门禁给出的场景名，不能只在其他字段中顺带提及关键词。",
            ]
        )
        if not allow_new_repair_items:
            rules.extend(
                [
                    "- 本轮只有既有行的事实或语义问题；严禁新增任何 ID，只能返回门禁点名的既有 ID。",
                    "- 未被门禁点名的既有行不得返回、改写或复制；系统会拒绝所有越界新增行。",
                ]
            )
    elif current_artifact_seed.strip():
        rules.extend(
            [
                "- CURRENT_ARTIFACT_TO_REPAIR 是本次运行已经生成的上一版产物；保留未被门禁否定的条目和字段，只修改或补充当前 artifact 的失败项。",
                "- 必须返回完整的修复后顶层值，禁止丢弃无关的已验证条目，禁止为修复一个问题而重写其他事实。",
            ]
        )
    if "missing_performance_statistical_basis" in quality_issue_codes:
        rules.append(
            "- 性能判定必须依据同环境样本的标准差、方差、置信区间或历史波动推导；禁止直接写固定百分比，数据不足时标为待采样并给出统计采样方法。"
        )
    if "behavior_claim_contradicted" in quality_issue_codes:
        rules.extend(
            [
                "- 独立审计器判定为 contradicted 的语句必须从对应字段中删除或按审计给出的源码真值重写；保留该行未被否定的字段，不能通过删除整行规避修复。不能仅添加“待验证”、‘可能’或括号说明后继续保留相反结论。",
                "- 场景前提本身与源码相反时，必须重构为同一测试维度下真实可执行的场景；不得把不可能的操作继续放在 steps、expected_result 或 observability。",
            ]
        )
    if "behavior_claim_insufficient" in quality_issue_codes:
        rules.append(
            "- 独立审计器判定为 insufficient 的实现结论必须删除，或改造成带明确操作与 oracle 的待执行测试；不得继续把它写成 expected_result、effect 或已实现行为。"
        )
    if (
        isinstance(schema, dict)
        and int(schema.get("minItems") or 0) > 0
        and not is_array_quality_patch
    ):
        rules.append(f"- 必须输出至少 {int(schema['minItems'])} 个互不重复的条目。")
    required_evidence_terms = [
        str(value).strip()
        for value in output_contract.get("required_evidence_terms") or []
        if str(value).strip()
    ]
    if required_evidence_terms:
        rules.append(
            "- 交付件必须包含关键证据锚点: " + ", ".join(required_evidence_terms)
        )
    forbidden_claim_terms = [
        str(value).strip()
        for value in output_contract.get("forbidden_claim_terms") or []
        if str(value).strip()
    ]
    if forbidden_claim_terms:
        rules.append(
            "- 禁止输出以下已知冲突结论: " + ", ".join(forbidden_claim_terms)
        )
    if output_limits.get("max_items"):
        rules.append(f"- 最多输出 {int(output_limits['max_items'])} 个最高价值条目。")
    if output_limits.get("max_field_characters"):
        rules.append(
            f"- 每个叙述字段最多 {int(output_limits['max_field_characters'])} 个字符。"
        )
    parts = [
        f"STAGE_ID: {stage.get('id')}",
        f"OUTPUT_ARTIFACT: {artifact}",
        f"PURPOSE: {stage.get('purpose')}",
        "ORIGINAL_USER_REQUEST:",
        str(plan.get("original_user_request") or ""),
        "",
    ]
    if scoped_quality_feedback:
        parts.extend(
            [
                "QUALITY_REPAIR_ISSUES:",
                json.dumps(scoped_quality_feedback, ensure_ascii=False, indent=2),
                "",
            ]
        )
        if current_artifact_seed.strip():
            parts.extend(
                [
                    "CURRENT_ARTIFACT_TO_REPAIR:",
                    repair_prompt_seed,
                    "",
                ]
            )
    parts.extend(
        [
            (
                "RELEVANT_VERIFIED_CONTEXT:"
                if scoped_quality_feedback
                else "COMPACT_VERIFIED_CONTEXT:"
            ),
            json.dumps(context, ensure_ascii=False, indent=2),
            "",
        ]
    )
    parts.extend(
        [
            "OUTPUT_CONTRACT:",
            json.dumps(output_contract, ensure_ascii=False, indent=2),
            "",
            "VERIFIED_CLAIM_EVIDENCE_CATALOG:",
            json.dumps(claim_catalog, ensure_ascii=False, indent=2),
            "",
            "VERIFIED_REPO_PATH_ALLOWLIST:",
            json.dumps(verified_repo_paths, ensure_ascii=False, indent=2),
            "",
            "VERIFIED_LITERAL_FACTS:",
            json.dumps(verified_literal_facts, ensure_ascii=False, indent=2),
            "",
            "SOURCE_BOUND_DOMAIN_FACTS:",
            json.dumps(source_bound_domain_facts, ensure_ascii=False, indent=2),
            "",
        ]
    )
    if partial_seed:
        parts.extend(
            [
                "PARTIAL_OUTPUT_TO_CONTINUE:",
                partial_seed[-6000:],
                "",
                "- 从上述未完成位置继续生成，禁止重复已经存在的章节和段落。",
                "",
            ]
        )
    parts.extend(
        [
            "CURRENT_STAGE_ONLY:",
            f"- 当前只生成 {artifact}；其他交付件由其依赖阶段独立生成。",
            "- 不要在当前响应中生成其他阶段、artifact 容器或总报告。",
            "",
            "RULES:",
            *rules,
        ]
    )
    if scoped_quality_feedback:
        checklist: list[str] = []
        for raw_issue in scoped_quality_feedback.get("issues") or []:
            if not isinstance(raw_issue, dict):
                continue
            code = str(raw_issue.get("code") or "quality_issue").strip()
            message = str(
                raw_issue.get("message") or raw_issue.get("reason") or ""
            ).strip()
            scenarios = [
                str(value).strip()
                for value in raw_issue.get("scenarios") or []
                if str(value).strip()
            ]
            detail = message
            if scenarios:
                detail = f"{detail}；必须覆盖: {', '.join(scenarios)}".strip("；")
            line = f"- [{code}] {detail}".strip()
            if line not in checklist:
                checklist.append(line)
        if checklist:
            checklist_instruction = (
                "- 以下每一项都必须由本次返回的独立补丁条目明确满足；不得合并场景、只解释、承诺或省略。"
                if is_array_quality_patch
                else "- 以下每一项都必须在本次返回的完整 artifact 中明确满足；不得只解释、承诺或省略。"
            )
            parts.extend(
                [
                    "",
                    "MANDATORY_QUALITY_REPAIR_CHECKLIST:",
                    checklist_instruction,
                    *checklist,
                ]
            )
    return "\n".join(parts)


def _quality_repair_prompt_seed(
    *,
    current_artifact_seed: str,
    artifact: str,
    quality_feedback: dict[str, Any],
) -> str:
    """Expose only rejected rows to an array repair call.

    The complete accepted artifact remains the merge base outside the prompt.  This
    keeps exact audit feedback salient without allowing the model to rewrite rows
    that the gate already accepted.
    """
    seed = str(current_artifact_seed or "")
    if not seed.strip():
        return ""
    row_ids = _quality_repair_row_ids(
        artifact=artifact,
        quality_feedback=quality_feedback,
    )
    if not row_ids:
        return seed[:60_000]
    rows = _json_array_items(seed)
    selected = [
        row
        for row in rows
        if isinstance(row, dict)
        and str(
            row.get("case_id")
            or row.get("sfmea_id")
            or row.get("risk_id")
            or row.get("id")
            or ""
        ).strip()
        in row_ids
    ]
    if not selected:
        return seed[:60_000]
    return json.dumps(selected, ensure_ascii=False, indent=2)[:60_000]


def _quality_repair_row_ids(
    *,
    artifact: str,
    quality_feedback: dict[str, Any],
) -> set[str]:
    row_ids: set[str] = set()
    prefix = f"ROW:{Path(artifact).name}:"
    for issue in quality_feedback.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        if Path(str(issue.get("artifact") or "")).name != Path(artifact).name:
            continue
        claim_id = str(issue.get("claim_id") or "").strip()
        if claim_id.startswith(prefix):
            row_id = claim_id[len(prefix) :].strip()
            if row_id:
                row_ids.add(row_id)
        elif re.match(r"^(?:SFMEA|BBC)-[A-Za-z0-9_-]+:", claim_id):
            row_ids.add(claim_id.split(":", 1)[0])
        for key in ("row_id", "case_id", "sfmea_id", "risk_id"):
            row_id = str(issue.get(key) or "").strip()
            if row_id:
                row_ids.add(row_id)
    return row_ids


def _quality_repair_allows_new_items(
    *,
    artifact: str,
    quality_feedback: dict[str, Any],
) -> bool:
    """Allow additions only when the gate explicitly asks for missing coverage."""
    additive_codes = {
        "harness_case_not_registered",
        "incomplete_mcs_black_box_oracle",
        "insufficient_black_box_cases",
        "insufficient_sfmea_rows",
        "missing_black_box_dimensions",
        "missing_c_bit_fragmentation_case",
        "missing_chap_negative_scenarios",
        "missing_extended_chap_negative_scenarios",
        "missing_iscsi_professional_scenarios",
        "missing_max_connections_target_setup",
        "missing_mcs_capable_client",
    }
    artifact_name = Path(artifact).name
    for issue in quality_feedback.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        if Path(str(issue.get("artifact") or "")).name != artifact_name:
            continue
        if any(str(value).strip() for value in issue.get("scenarios") or []):
            return True
        code = str(issue.get("code") or "").strip()
        if code in additive_codes or (
            code.startswith("missing_")
            and any(
                marker in code
                for marker in ("case", "scenario", "dimension", "coverage")
            )
        ):
            return True
    return False


def _quality_repair_evidence_ids(
    *,
    quality_feedback: dict[str, Any],
    repair_prompt_seed: str,
) -> set[str]:
    evidence_ids: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            evidence_id = str(value.get("evidence_id") or "").strip()
            if evidence_id:
                evidence_ids.add(evidence_id)
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(quality_feedback.get("issues") or [])
    try:
        visit(json.loads(repair_prompt_seed))
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return evidence_ids


def _evidence_id_matches(candidate: str, requested: set[str]) -> bool:
    value = str(candidate or "").strip()
    if not value:
        return False
    value_base = value.split(":L", 1)[0]
    return any(
        value == expected
        or value_base == expected.split(":L", 1)[0]
        or value.startswith(f"{expected}:")
        or expected.startswith(f"{value}:")
        for expected in requested
        if expected
    )


def _quality_repair_evidence_cards(
    *,
    evidence_cards: list[dict[str, Any]],
    evidence_ids: set[str],
    feedback_text: str,
) -> list[dict[str, Any]]:
    exact = [
        card
        for card in evidence_cards
        if _evidence_id_matches(
            str(card.get("evidence_id") or ""),
            evidence_ids,
        )
    ]
    contextual = [
        card
        for card in evidence_cards
        if str(card.get("file_path") or "").lower() in feedback_text
        or any(
            str(symbol).lower() in feedback_text
            for symbol in card.get("symbols") or []
            if str(symbol).strip()
        )
    ]
    selected: list[dict[str, Any]] = []
    selected_ids: set[int] = set()
    for card in [*exact, *contextual]:
        card_identity = id(card)
        if card_identity in selected_ids:
            continue
        selected_ids.add(card_identity)
        selected.append(card)
        if len(selected) >= 8:
            break
    return selected


def _compact_quality_repair_evidence_card(card: dict[str, Any]) -> dict[str, Any]:
    """Keep exact source identity while bounding excerpts for a repair call."""
    return {
        "evidence_id": str(card.get("evidence_id") or ""),
        "file_path": str(card.get("file_path") or ""),
        "classification": str(card.get("classification") or ""),
        "start_line": int(card.get("start_line") or 0),
        "end_line": int(card.get("end_line") or 0),
        "excerpt": str(card.get("excerpt") or "")[:1800],
        "symbols": [
            str(value)
            for value in card.get("symbols") or []
            if str(value).strip()
        ][:12],
        "matched_terms": [
            str(value)
            for value in card.get("matched_terms") or []
            if str(value).strip()
        ][:12],
        "sha256": str(card.get("sha256") or ""),
    }


def _compact_quality_repair_input_materials(materials: Any) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for raw in materials or []:
        if not isinstance(raw, dict):
            continue
        item = {
            key: raw.get(key)
            for key in (
                "input_id",
                "id",
                "name",
                "label",
                "path",
                "source_ref",
                "sha256",
                "summary",
            )
            if raw.get(key) not in (None, "")
        }
        content = str(raw.get("content") or raw.get("text") or "")
        if content:
            item["content_excerpt"] = content[:1200]
        compact.append(item)
        if len(compact) >= 4:
            break
    return compact


def _source_bound_domain_facts(
    *,
    plan: dict[str, Any],
    source_pack: dict[str, Any],
) -> list[dict[str, Any]]:
    cards_by_path: dict[str, list[dict[str, Any]]] = {}
    for raw_card in source_pack.get("evidence_cards") or []:
        if not isinstance(raw_card, dict):
            continue
        path = str(raw_card.get("file_path") or "").strip()
        if path:
            cards_by_path.setdefault(path, []).append(raw_card)

    bound: list[dict[str, Any]] = []
    for raw_fact in plan.get("source_bound_domain_fact_candidates") or []:
        if not isinstance(raw_fact, dict):
            continue
        fact_id = str(raw_fact.get("id") or "").strip()
        assertion = str(raw_fact.get("assertion") or "").strip()
        references = [
            str(value).strip()
            for value in raw_fact.get("evidence") or []
            if str(value).strip()
        ][:8]
        if not fact_id or not assertion or not references:
            continue

        resolved: list[dict[str, Any]] = []
        for reference in references:
            path, _, symbol = reference.partition("::")
            matching_cards = cards_by_path.get(path.strip(), [])
            if symbol:
                symbol = symbol.strip()
                matching_cards = [
                    card
                    for card in matching_cards
                    if symbol in {
                        str(value).strip()
                        for value in card.get("symbols") or []
                        if str(value).strip()
                    }
                    or symbol in str(card.get("excerpt") or "")
                ]
            if not matching_cards:
                resolved = []
                break
            resolved.append(
                {
                    "reference": reference,
                    "evidence_ids": [
                        str(card.get("evidence_id") or "").strip()
                        for card in matching_cards
                        if str(card.get("evidence_id") or "").strip()
                    ][:4],
                }
            )
        if resolved:
            bound.append(
                {
                    "id": fact_id,
                    "assertion": assertion,
                    "evidence": resolved,
                }
            )
    return bound[:64]


def _quality_feedback_for_artifact(
    feedback: dict[str, Any], artifact: str
) -> dict[str, Any]:
    scoped = {
        key: value
        for key, value in feedback.items()
        if key
        not in {
            "issues",
            "issue_groups",
            "affected_artifacts",
            "score",
            "status",
            "quality_artifact",
            "recommendations",
        }
    }
    artifact_name = Path(artifact).name
    issues: list[dict[str, Any]] = []
    for raw_issue in feedback.get("issues") or []:
        if not isinstance(raw_issue, dict):
            continue
        issue_artifact = Path(str(raw_issue.get("artifact") or "")).name
        code = str(raw_issue.get("code") or "")
        applies = issue_artifact == artifact_name
        if issue_artifact.endswith((".md", ".txt")):
            if code == "professional_fact_conflict" and artifact_name in {
                "business_flow.md",
                "sfmea.json",
                "black_box_cases.json",
            }:
                applies = True
            elif (
                artifact_name == "business_flow.md"
                and code == "missing_iscsi_professional_scenarios"
            ):
                applies = True
            elif artifact_name == "sfmea.json" and (
                "sfmea" in code or "chap" in code
            ):
                applies = True
            elif artifact_name == "black_box_cases.json" and any(
                marker in code
                for marker in ("black_box", "raw_pdu", "hazardous", "performance")
            ):
                applies = True
            elif artifact_name == "black_box_cases.json" and code in {
                "missing_iscsi_professional_scenarios",
                "missing_max_connections_target_setup",
                "incomplete_mcs_black_box_oracle",
                "harness_case_not_registered",
            }:
                applies = True
        if applies:
            issues.append(dict(raw_issue))
    scoped["issues"] = issues
    issue_groups = [
        dict(group)
        for group in feedback.get("issue_groups") or []
        if isinstance(group, dict)
        and Path(str(group.get("artifact") or "")).name == artifact_name
    ]
    scoped["issue_groups"] = issue_groups
    scoped["issue_count"] = len(issues) or sum(
        int(group.get("count") or 1) for group in issue_groups
    )
    scoped["affected_artifacts"] = [artifact_name]
    return scoped


def _compact_execution_input_contract(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    def record_sha256(item: Any) -> str:
        return hashlib.sha256(
            json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()

    def identity_record(item: Any, *, source_ref: str, index: int) -> dict[str, Any]:
        identity: dict[str, Any] = {"index": index}
        if isinstance(item, dict):
            for key in (
                "id",
                "input_id",
                "name",
                "label",
                "type",
                "profile_id",
                "skill_id",
                "path",
            ):
                text = str(item.get(key) or "").strip()
                if text:
                    identity[key] = text[:200]
        elif isinstance(item, str):
            identity["value"] = item[:200]
        else:
            identity["value"] = str(item)[:200]
        identity["sha256"] = record_sha256(item)
        identity["source_ref"] = source_ref
        return identity

    def bounded_string(item: str, *, limit: int, source_ref: str) -> Any:
        if len(item) <= limit:
            return item
        return {
            "preview": item[:limit],
            "characters": len(item),
            "sha256": hashlib.sha256(item.encode("utf-8")).hexdigest(),
            "source_ref": source_ref,
        }

    def compact_record(
        item: Any,
        *,
        string_limit: int = 600,
        source_ref: str = "execution_input_contract",
        depth: int = 0,
    ) -> Any:
        if depth >= 4:
            return bounded_string(str(item), limit=240, source_ref=source_ref)
        if isinstance(item, dict):
            entries = list(item.items())
            compacted = {
                str(key): compact_record(
                    child,
                    string_limit=string_limit,
                    source_ref=f"{source_ref}/{key}",
                    depth=depth + 1,
                )
                for key, child in entries[:32]
            }
            if len(entries) > 32:
                compacted["__overflow__"] = {
                    "count": len(entries) - 32,
                    "entries": [
                        {
                            "key": str(key)[:200],
                            "sha256": record_sha256(child),
                            "source_ref": f"{source_ref}/{key}",
                        }
                        for key, child in entries[32:]
                    ],
                    "sha256": record_sha256(item),
                    "source_ref": source_ref,
                }
            return compacted
        if isinstance(item, list):
            compacted = [
                compact_record(
                    child,
                    string_limit=string_limit,
                    source_ref=f"{source_ref}/{index}",
                    depth=depth + 1,
                )
                for index, child in enumerate(item[:64])
            ]
            if len(item) > 64:
                compacted.append(
                    {
                        "__overflow__": {
                            "count": len(item) - 64,
                            "items": [
                                identity_record(
                                    child,
                                    source_ref=f"{source_ref}/{index}",
                                    index=index,
                                )
                                for index, child in enumerate(item[64:], start=64)
                            ],
                            "sha256": record_sha256(item),
                            "source_ref": source_ref,
                        }
                    }
                )
            return compacted
        if isinstance(item, str):
            return bounded_string(item, limit=string_limit, source_ref=source_ref)
        return item

    materials = value.get("input_materials")
    materials = materials if isinstance(materials, dict) else {}
    return {
        "goal": str(value.get("goal") or "")[:2400],
        "user_inputs": [
            (
                compact_record(
                    item,
                    string_limit=320,
                    source_ref=f"staged_execution_plan.json#/execution_input_contract/user_inputs/{index}",
                )
                if index < 64
                else {
                    "id": str(item.get("id") or "")[:200],
                    "name": str(item.get("name") or "")[:200],
                    "type": str(item.get("type") or "")[:120],
                    "sha256": hashlib.sha256(
                        json.dumps(item, ensure_ascii=False, sort_keys=True).encode("utf-8")
                    ).hexdigest(),
                    "source_ref": f"staged_execution_plan.json#/execution_input_contract/user_inputs/{index}",
                }
            )
            for index, item in enumerate(value.get("user_inputs") or [])
            if isinstance(item, dict)
        ],
        "input_materials": {
            "read_order": compact_record(
                materials.get("read_order") or [],
                string_limit=600,
                source_ref="staged_execution_plan.json#/execution_input_contract/input_materials/read_order",
            ),
            "materials": [
                (
                    compact_record(
                        item,
                        string_limit=1000,
                        source_ref=f"staged_execution_plan.json#/execution_input_contract/input_materials/materials/{index}",
                    )
                    if index < 16
                    else identity_record(
                        item,
                        source_ref=f"staged_execution_plan.json#/execution_input_contract/input_materials/materials/{index}",
                        index=index,
                    )
                )
                for index, item in enumerate(materials.get("materials") or [])
                if isinstance(item, dict)
            ],
            "rules": compact_record(materials.get("rules") or {}, string_limit=1000),
        },
        "mcp": compact_record(value.get("mcp") or {}, string_limit=2400),
        "skills": compact_record(value.get("skills") or {}, string_limit=1200),
        "test_activity_contract": _compact_test_activity_contract(
            value.get("test_activity_contract")
        ),
    }


def _compact_test_activity_contract(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact: dict[str, Any] = {}
    for key in (
        "contract_version",
        "project_profile",
        "domain_profiles",
        "domain_requirements",
        "evidence_policy",
        "executor_requirements",
        "quality_gates",
        "black_box_boundary",
        "required_outputs",
    ):
        if key in value:
            compact[key] = _compact_stage_value(value[key])
    all_constraints = [
        item
        for item in value.get("professional_constraints") or []
        if isinstance(item, dict)
    ]
    if all_constraints:
        compact["professional_constraint_catalog"] = {
            "role": "lint_only_not_generation_context",
            "count": len(all_constraints),
            "ids": [str(item.get("id") or "")[:160] for item in all_constraints[:64]],
            "sha256": hashlib.sha256(
                json.dumps(all_constraints, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "source_ref": (
                "staged_execution_plan.json#/execution_input_contract/"
                "test_activity_contract/professional_constraints"
            ),
        }
    return compact


def _compact_stage_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return str(value)[:300]
    if isinstance(value, dict):
        return {
            str(key): _compact_stage_value(item, depth=depth + 1)
            for key, item in list(value.items())[:24]
        }
    if isinstance(value, list):
        return [_compact_stage_value(item, depth=depth + 1) for item in value[:12]]
    if isinstance(value, str):
        return value[:600]
    return value


def _deterministic_schema_repair(
    payload: Any,
    schema: dict[str, Any],
) -> tuple[Any, list[str]]:
    repaired = json.loads(json.dumps(payload, ensure_ascii=False))
    fields: list[str] = []
    repairable_observation_fields = {"detection"}

    def visit(value: Any, current_schema: dict[str, Any], path: str) -> None:
        schema_type = current_schema.get("type")
        if schema_type == "array" and isinstance(value, list):
            item_schema = current_schema.get("items")
            if isinstance(item_schema, dict):
                for index, item in enumerate(value):
                    visit(item, item_schema, f"{path}[{index}]")
            return
        if schema_type != "object" or not isinstance(value, dict):
            return
        properties = current_schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        for field in current_schema.get("required") or []:
            field_name = str(field)
            field_schema = properties.get(field_name)
            if field_name in value or not isinstance(field_schema, dict):
                continue
            if (
                field_schema.get("type") != "string"
                or field_name not in repairable_observation_fields
            ):
                continue
            value[field_name] = (
                "待验证：模型未提供该观测字段，需基于本条 source_evidence 的日志、状态和外部结果补充。"
            )
            fields.append(f"{path}.{field_name}")
        for field_name, field_schema in properties.items():
            if field_name in value and isinstance(field_schema, dict):
                if (
                    field_schema.get("type") == "array"
                    and isinstance(value[field_name], str)
                    and isinstance(field_schema.get("items"), dict)
                    and field_schema["items"].get("type") == "string"
                ):
                    value[field_name] = [value[field_name]]
                    fields.append(f"{path}.{field_name}")
                visit(value[field_name], field_schema, f"{path}.{field_name}")

    visit(repaired, schema, "$")
    return repaired, fields


def _deterministic_quality_claim_repair(
    payload: Any,
    *,
    artifact: str,
    quality_feedback: dict[str, Any] | None,
) -> tuple[Any, list[str]]:
    """Apply bounded repairs for deterministic validator findings only."""
    repaired = json.loads(json.dumps(payload, ensure_ascii=False))
    issues = [
        item
        for item in (quality_feedback or {}).get("issues") or []
        if isinstance(item, dict)
        and Path(str(item.get("artifact") or "")).name == Path(artifact).name
    ]
    issue_codes = {str(item.get("code") or "") for item in issues}
    supported_codes = {
        "invalid_capture_filter",
        "black_box_test_mapping_contradiction",
    }
    if not issue_codes or not issue_codes.issubset(supported_codes):
        return repaired, []

    fields: list[str] = []

    mcs_mapping_issues = [
        item
        for item in issues
        if str(item.get("code") or "") == "black_box_test_mapping_contradiction"
        and str(item.get("constraint_id") or "")
        == "iscsi_multiconnection_mapping_scope"
    ]
    if mcs_mapping_issues and isinstance(repaired, list):
        scenario_names = {
            str(item.get("scenario") or "").strip().lower()
            for item in mcs_mapping_issues
            if str(item.get("scenario") or "").strip()
        }
        for index, row in enumerate(repaired):
            if not isinstance(row, dict):
                continue
            scenario_name = str(row.get("scenario_name") or "").strip().lower()
            is_target = (
                any(
                    scenario_name == expected
                    or scenario_name in expected
                    or expected in scenario_name
                    for expected in scenario_names
                    if scenario_name and expected
                )
                or (not scenario_names and "mcs" in scenario_name)
            )
            if not is_target:
                continue
            steps = row.get("steps")
            if isinstance(steps, list) and any(
                "iscsiadm" in str(step).lower() for step in steps
            ):
                row["steps"] = [
                    "Login second connection using raw-PDU harness with the same ISID, "
                    "non-zero TSIH, a different CID, and the first socket kept online"
                    if "iscsiadm" in str(step).lower()
                    else step
                    for step in steps
                ]
                fields.append(f"$[{index}].steps")
            preconditions = row.get("preconditions")
            if isinstance(preconditions, list) and any(
                "iscsiadm" in str(item).lower() for item in preconditions
            ):
                row["preconditions"] = [
                    "A runnable raw-PDU harness can preserve the first socket and control "
                    "ISID, non-zero TSIH, and CID values"
                    if "iscsiadm" in str(item).lower()
                    else item
                    for item in preconditions
                ]
                fields.append(f"$[{index}].preconditions")
            mapping = str(row.get("mapped_test_dir") or "")
            if "multiconnection.sh" in mapping.lower():
                row["mapped_test_dir"] = (
                    "需新增 raw-PDU harness；multiconnection.sh 仅作环境搭建参考，"
                    "不覆盖同一 session 的 MCS；仅使用隔离测试盘，存在数据销毁风险"
                )
                fields.append(f"$[{index}].mapped_test_dir")

    def normalize_command(match: re.Match[str]) -> str:
        command = match.group(0)
        normalized = re.sub(
            r"\s+(?:and\s+)?iscsi\.[a-z0-9_.]+\s*(?:==|=)\s*(?:0x[0-9a-f]+|\d+)",
            "",
            command,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"(?<![a-z])port\s+(\d+)",
            r"tcp port \1",
            normalized,
            count=1,
            flags=re.IGNORECASE,
        )
        return normalized

    def visit(value: Any, path: str) -> Any:
        if isinstance(value, dict):
            for key, item in list(value.items()):
                value[key] = visit(item, f"{path}.{key}")
            return value
        if isinstance(value, list):
            for index, item in enumerate(value):
                value[index] = visit(item, f"{path}[{index}]")
            return value
        if not isinstance(value, str) or "tcpdump" not in value.lower():
            return value
        normalized = re.sub(
            r"(?im)\btcpdump\b[^`\n；;]*",
            normalize_command,
            value,
        )
        if normalized != value:
            fields.append(path)
        return normalized

    repaired = visit(repaired, "$")
    return repaired, fields


def _regular_stage_repair_prompt(
    *,
    stage: dict[str, Any],
    raw_content: str,
    validation_error: str,
    evidence_ids: list[str],
) -> str:
    output_contract = (
        stage.get("output_contract")
        if isinstance(stage.get("output_contract"), dict)
        else {}
    )
    return "\n".join(
        [
            "SMALL_FORMAT_REPAIR",
            f"STAGE_ID: {stage.get('id')}",
            f"OUTPUT_ARTIFACT: {stage.get('artifact')}",
            f"VALIDATION_ERROR: {validation_error[:800]}",
            "OUTPUT_SCHEMA:",
            json.dumps(output_contract.get("schema") or {}, ensure_ascii=False, sort_keys=True),
            "REQUIRED_EVIDENCE_IDS:",
            json.dumps(evidence_ids[:80], ensure_ascii=False),
            "FIRST_OUTPUT:",
            raw_content[:2600],
            "",
            "Repair syntax/schema only. Preserve evidence and meaning. Return only the artifact value.",
        ]
    )


def _salvage_truncated_json_array(content: str) -> list[Any]:
    text = str(content or "").strip()
    opening_fence = re.match(r"^\s*```(?:json)?\s*", text, re.IGNORECASE)
    if opening_fence:
        text = text[opening_fence.end() :]
    start = text.find("[")
    if start < 0:
        return []
    decoder = json.JSONDecoder()
    index = start + 1
    items: list[Any] = []
    while index < len(text):
        while index < len(text) and (text[index].isspace() or text[index] == ","):
            index += 1
        if index >= len(text) or text[index] == "]":
            break
        try:
            value, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            end = _balanced_json_value_end(text, index)
            if end is None:
                break
            index = end
            continue
        items.append(value)
        index = end
    return items


def _balanced_json_value_end(text: str, start: int) -> int | None:
    """Find the end of one structurally balanced, potentially malformed value."""
    if start >= len(text) or text[start] not in "[{":
        return None
    stack: list[str] = []
    in_string = False
    escaped = False
    pairs = {"}": "{", "]": "["}
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            stack.append(char)
        elif char in pairs:
            if not stack or stack[-1] != pairs[char]:
                continue
            stack.pop()
            if not stack:
                return index + 1
    return None


def _json_array_items(content: str) -> list[Any]:
    try:
        value = json.loads(str(content or "").strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _json_array_item_count(content: str) -> int:
    return len(_json_array_items(content))


def _json_array_has_unfinished_tail(content: str) -> bool:
    text = str(content or "").strip()
    opening_fence = re.match(r"^\s*```(?:json)?\s*", text, re.IGNORECASE)
    if opening_fence:
        text = text[opening_fence.end() :].strip()
    if text.endswith("```"):
        text = text[:-3].rstrip()
    try:
        return not isinstance(json.loads(text), list)
    except (TypeError, ValueError, json.JSONDecodeError):
        return True


def _json_array_item_identity(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("case_id", "sfmea_id", "id", "scenario_name", "failure_mode"):
            value = str(item.get(key) or "").strip()
            if value:
                return f"{key}:{value}"
    return json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _merge_json_array_items(existing: list[Any], additional: list[Any]) -> list[Any]:
    merged = list(existing)
    identities = {_json_array_item_identity(item) for item in existing}
    for item in additional:
        identity = _json_array_item_identity(item)
        if identity in identities:
            continue
        identities.add(identity)
        merged.append(item)
    return merged


def _missing_quality_repair_row_ids(
    patch: list[Any],
    requested_row_ids: set[str],
) -> set[str]:
    """Return requested row IDs omitted by a non-additive quality patch."""
    present = {
        str(
            item.get("case_id")
            or item.get("sfmea_id")
            or item.get("risk_id")
            or item.get("id")
            or ""
        ).strip()
        for item in patch
        if isinstance(item, dict)
    }
    return {row_id for row_id in requested_row_ids if row_id not in present}


def _apply_quality_feedback_field_patches(
    rendered: list[Any],
    *,
    artifact: str,
    quality_feedback: dict[str, Any],
    base_items: list[Any] | None = None,
) -> list[Any]:
    """Apply independently audited semantic field fixes before row merging."""
    artifact_name = Path(artifact).name
    if artifact_name == "sfmea.json":
        allowed_fields = {
            "failure_mode",
            "cause",
            "effect",
            "detection",
            "mitigation",
            "test_mapping",
        }
    elif artifact_name == "black_box_cases.json":
        allowed_fields = {
            "scenario_name",
            "preconditions",
            "steps",
            "expected_result",
            "observability",
            "failure_diagnostics",
            "mapped_test_dir",
            "test_dimension",
        }
    else:
        return list(rendered)

    patches: dict[str, dict[str, Any]] = {}
    for issue in quality_feedback.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        if Path(str(issue.get("artifact") or "")).name != artifact_name:
            continue
        row_id = str(
            issue.get("row_id")
            or issue.get("case_id")
            or issue.get("sfmea_id")
            or issue.get("risk_id")
            or ""
        ).strip()
        field_patch = issue.get("field_patch")
        if not row_id or not isinstance(field_patch, dict):
            continue
        scoped = {
            key: value
            for key, value in field_patch.items()
            if key in allowed_fields and isinstance(value, (str, list))
        }
        if scoped:
            patches.setdefault(row_id, {}).update(scoped)
    if not patches:
        return list(rendered)

    base_by_id = {
        _json_array_row_id(item): item
        for item in (base_items or [])
        if isinstance(item, dict) and _json_array_row_id(item)
    }
    result: list[Any] = []
    seen: set[str] = set()
    for item in rendered:
        if not isinstance(item, dict):
            result.append(item)
            continue
        row_id = _json_array_row_id(item)
        replacement = patches.get(row_id)
        result.append({**item, **replacement} if replacement else item)
        if row_id:
            seen.add(row_id)
    for row_id, field_patch in patches.items():
        if row_id in seen or row_id not in base_by_id:
            continue
        result.append({**base_by_id[row_id], **field_patch})
    return result


def _json_array_row_id(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    return str(
        item.get("case_id")
        or item.get("sfmea_id")
        or item.get("risk_id")
        or item.get("id")
        or ""
    ).strip()


def _merge_json_array_patch(
    previous: list[Any],
    patch: list[Any],
    *,
    allowed_existing_row_ids: set[str] | None = None,
    allow_new_items: bool = True,
) -> list[Any]:
    """Apply array row patches without reordering or dropping accepted rows."""
    patches_by_identity: dict[str, Any] = {}
    new_items: list[Any] = []
    new_identities: set[str] = set()
    previous_identities = {
        _json_array_item_identity(item)
        for item in previous
    }
    for item in patch:
        identity = _json_array_item_identity(item)
        if identity in previous_identities:
            row_id = ""
            if isinstance(item, dict):
                row_id = str(
                    item.get("case_id")
                    or item.get("sfmea_id")
                    or item.get("risk_id")
                    or item.get("id")
                    or ""
                ).strip()
            if (
                allowed_existing_row_ids is not None
                and row_id not in allowed_existing_row_ids
            ):
                continue
            patches_by_identity[identity] = item
        elif allow_new_items and identity not in new_identities:
            new_identities.add(identity)
            new_items.append(item)

    merged: list[Any] = []
    for item in previous:
        identity = _json_array_item_identity(item)
        replacement = patches_by_identity.get(identity)
        if isinstance(item, dict) and isinstance(replacement, dict):
            merged.append({**item, **replacement})
        elif replacement is not None:
            merged.append(replacement)
        else:
            merged.append(item)
    merged.extend(new_items)
    return merged


def _deduplicate_sfmea_semantic_categories(items: list[Any]) -> list[Any]:
    """Prefer quality-patch rows when a known SFMEA semantic category repeats."""
    from app.services.test_activity_contract import _sfmea_semantic_category

    seen_categories: set[str] = set()
    deduplicated: list[Any] = []
    for item in items:
        category = (
            _sfmea_semantic_category(str(item.get("failure_mode") or ""))
            if isinstance(item, dict)
            else ""
        )
        if category and category in seen_categories:
            continue
        if category:
            seen_categories.add(category)
        deduplicated.append(item)
    return deduplicated


def _json_array_continuation_prompt(
    *,
    stage: dict[str, Any],
    existing_items: list[Any],
    remaining_count: int,
    evidence_ids: list[str],
    claim_evidence_catalog: list[dict[str, str]],
) -> str:
    output_contract = (
        stage.get("output_contract")
        if isinstance(stage.get("output_contract"), dict)
        else {}
    )
    summaries: list[str] = []
    for item in existing_items:
        if isinstance(item, dict):
            identity = _json_array_item_identity(item)
            dimension = str(item.get("test_dimension") or "").strip()
            scenario = str(item.get("scenario_name") or item.get("failure_mode") or "").strip()
            summaries.append(" | ".join(value for value in (identity, dimension, scenario) if value))
        else:
            summaries.append(_json_array_item_identity(item))
    return "\n".join(
        [
            "JSON_ARRAY_CONTINUATION",
            f"STAGE_ID: {stage.get('id')}",
            f"OUTPUT_ARTIFACT: {stage.get('artifact')}",
            f"REMAINING_ITEM_COUNT: {max(1, int(remaining_count))}",
            "OUTPUT_SCHEMA:",
            json.dumps(output_contract.get("schema") or {}, ensure_ascii=False, sort_keys=True),
            "ALREADY_ACCEPTED_ITEMS:",
            *(summaries or ["(none)"]),
            "ALLOWED_EVIDENCE_IDS:",
            ", ".join(evidence_ids[:80]),
            "VERIFIED_CLAIM_EVIDENCE_CATALOG:",
            json.dumps(claim_evidence_catalog, ensure_ascii=False, separators=(",", ":")),
            "",
            "Return only a JSON array containing exactly the missing additional items.",
            "Do not repeat or rewrite an accepted item. Keep every required field concise.",
            "For technical_claims evidence, copy one complete catalog object verbatim; never invent a path, line, quote, or evidence_id.",
            "Use only the allowed evidence IDs and preserve the current stage semantics.",
        ]
    )


def _required_evidence_ids(
    source_pack: dict[str, Any], outline: dict[str, Any]
) -> list[str]:
    values = [str(value) for value in outline.get("evidence_ids") or [] if str(value)]
    values.extend(
        str(item.get("evidence_id") or "")
        for item in source_pack.get("evidence_cards") or []
        if isinstance(item, dict) and str(item.get("evidence_id") or "")
    )
    return list(dict.fromkeys(values))[:80]


def _dependency_artifact_characters(
    *, stage: dict[str, Any], completed: dict[str, Path]
) -> int:
    dependencies = {str(value) for value in stage.get("depends_on") or []}
    total = 0
    for stage_id, path in completed.items():
        if stage_id not in dependencies or not path.is_file():
            continue
        total += len(path.read_text(encoding="utf-8", errors="replace"))
    return total


def _extract_partial_narrative(content: str) -> str:
    marker = "## 模型叙述增强"
    index = content.find(marker)
    if index < 0:
        return content[-6000:]
    remainder = content[index:].split("\n", 1)
    return remainder[1].strip() if len(remainder) > 1 else ""


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
    provider_capacity: _ProcessProviderCapacity,
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
    context_projection_timed_out = False
    context_timeout = min(
        float(effective["context_timeout_seconds"]),
        float(effective["total_timeout_seconds"]),
    )
    try:
        compact = await asyncio.wait_for(
            asyncio.to_thread(
                build_source_analysis_context,
                plan=plan,
                staged_context=staged_context,
                max_files=effective["max_files"],
                excerpt_chars=effective["excerpt_chars"],
                max_evidence_anchors=effective["max_evidence_anchors"],
            ),
            timeout=max(0.001, context_timeout),
        )
    except asyncio.TimeoutError:
        context_projection_timed_out = True
        compact = _project_source_analysis_context_from_memory(
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
    if (
        context_projection_timed_out
        and context_timeout >= float(effective["total_timeout_seconds"])
    ) or elapsed_after_context >= float(effective["total_timeout_seconds"]):
        budget_degradation_reason = "total_budget_exceeded_during_context"
    elif context_projection_timed_out or context_prepare_ms >= float(
        effective["context_timeout_seconds"]
    ) * 1000:
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
    detached_provider_tasks: list[asyncio.Task[Any]] = []
    source_capacity_acquired = False
    if budget_degradation_reason:
        finish_reason = "budget_exceeded"
    elif not pack.get("evidence_cards"):
        degraded = True
        degradation_reason = "no_verified_evidence"
    else:
        remaining_capacity_budget = max(
            0.001,
            float(effective["total_timeout_seconds"]) - (time.monotonic() - started),
        )
        source_capacity_acquired = await provider_capacity.acquire(
            remaining_capacity_budget,
            is_cancelled=is_cancelled,
        )
        if not source_capacity_acquired:
            degraded = True
            degradation_reason = "provider_capacity_timeout"
            finish_reason = "budget_exceeded"
        else:
            attempt_count = 1
    if source_capacity_acquired:
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
                on_detached_task=(
                    detached_provider_tasks.append
                ),
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
            if not raw_content:
                raise ValueError("provider_output_empty")
            output_truncated = bool(getattr(response, "truncated", False)) or (
                finish_reason == "length"
            )
            grounding_errors = (
                []
                if output_truncated
                else _source_analysis_raw_grounding_errors(raw_content, pack=pack)
            )
            if grounding_errors:
                degraded = True
                degradation_reason = (
                    "model_output_unverified: " + "; ".join(grounding_errors[:5])
                )
                finish_reason = "grounding_rejected"
            format_errors = (
                ["第一次输出达到长度上限，未形成闭合 JSON"]
                if output_truncated
                else (
                    _source_analysis_json_format_errors(raw_content)
                    if not degraded
                    else []
                )
            )
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
                    on_detached_task=(
                        detached_provider_tasks.append
                    ),
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
                repair_grounding_errors = _source_analysis_raw_grounding_errors(
                    repaired_content,
                    pack=pack,
                )
                if repair_grounding_errors:
                    degraded = True
                    degradation_reason = (
                        "model_output_unverified: "
                        + "; ".join(repair_grounding_errors[:5])
                    )
                    finish_reason = "grounding_rejected"
                remaining_errors = _source_analysis_json_format_errors(
                    repaired_content
                )
                if remaining_errors:
                    raise ValueError(
                        "repair_output_invalid: " + "; ".join(remaining_errors[:3])
                    )
                raw_content = repaired_content
                if not degraded:
                    finish_reason = f"repair_{repair_finish_reason}"
            if not degraded:
                ranking, ranking_errors = _parse_source_analysis_ranking(
                    raw_content,
                    pack=pack,
                    max_evidence_anchors=int(effective["max_evidence_anchors"]),
                    max_characters=int(effective["max_chinese_characters"]),
                )
                if ranking_errors:
                    degraded = True
                    degradation_reason = (
                        "model_output_unverified: "
                        + "; ".join(ranking_errors[:5])
                    )
                    finish_reason = "grounding_rejected"
                else:
                    enhancement = _render_source_analysis_ranking(
                        ranking,
                        pack=pack,
                    )
            if not degraded:
                deterministic = output_path.read_text(encoding="utf-8")
                _write_text(
                    output_path,
                    deterministic.rstrip()
                    + "\n\n## 模型排序、归纳与缺口标记\n\n"
                    + enhancement.rstrip()
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
        finally:
            provider_capacity.release_after(detached_provider_tasks)
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


def _source_analysis_raw_grounding_errors(
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
    referenced_paths = sorted(
        set(
            re.findall(
                r"(?<![A-Za-z0-9_.-])(?:[A-Za-z0-9_.-]+/)+"
                r"[A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+",
                stripped,
            )
        )
    )
    if referenced_paths:
        errors.append("模型排序契约不得包含文件路径：" + ", ".join(referenced_paths[:5]))
    referenced_calls = sorted(
        set(
            re.findall(
                r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\([^()\n]*\)",
                stripped,
            )
        )
    )
    if referenced_calls:
        errors.append("模型排序契约不得包含函数调用：" + ", ".join(referenced_calls[:5]))
    contract_candidate = stripped
    if contract_candidate.startswith("```json"):
        contract_candidate = contract_candidate[len("```json") :].lstrip()
        if contract_candidate.endswith("```"):
            contract_candidate = contract_candidate[:-3].rstrip()
    if not contract_candidate.startswith("{"):
        errors.append("未返回 source analysis 排序 JSON 契约")
        return errors
    expected_keys = {"ranked_evidence_ids", "gap_evidence_ids"}
    candidate_keys = set(re.findall(r'"([^"\\]+)"\s*:', contract_candidate))
    extra_keys = sorted(candidate_keys - expected_keys)
    if extra_keys:
        errors.append("排序契约包含未允许字段：" + ", ".join(extra_keys))
    scrubbed = re.sub(
        r'"(?:ranked_evidence_ids|gap_evidence_ids)"',
        "",
        contract_candidate,
    )
    scrubbed = re.sub(r'"SRC-\d+"', "", scrubbed)
    scrubbed = re.sub(r'[\s{}\[\],:\"]+', "", scrubbed)
    if scrubbed:
        errors.append("排序契约包含不可修复的自由文本或值")
    return errors


def _source_analysis_json_format_errors(content: str) -> list[str]:
    stripped = content.strip()
    if stripped.startswith("```json") and stripped.endswith("```"):
        return ["JSON 被 Markdown 围栏包裹"]
    try:
        json.loads(stripped)
    except json.JSONDecodeError:
        return ["JSON 未闭合或格式错误"]
    return []


def _parse_source_analysis_ranking(
    content: str,
    *,
    pack: dict[str, Any],
    max_evidence_anchors: int,
    max_characters: int,
) -> tuple[dict[str, list[str]], list[str]]:
    try:
        payload = json.loads(content.strip())
    except json.JSONDecodeError:
        return {}, ["JSON 未闭合或格式错误"]
    if not isinstance(payload, dict):
        return {}, ["排序契约顶层必须是对象"]
    expected_keys = {"ranked_evidence_ids", "gap_evidence_ids"}
    extra_keys = sorted(set(payload) - expected_keys)
    missing_keys = sorted(expected_keys - set(payload))
    errors: list[str] = []
    if len(content) > max_characters:
        errors.append(f"排序 JSON 超过 {max_characters} 字符上限")
    if extra_keys:
        errors.append("排序契约包含未允许字段：" + ", ".join(extra_keys))
    if missing_keys:
        errors.append("排序契约缺少字段：" + ", ".join(missing_keys))
    ranked = payload.get("ranked_evidence_ids")
    gaps = payload.get("gap_evidence_ids")
    if not isinstance(ranked, list) or not all(isinstance(item, str) for item in ranked):
        errors.append("ranked_evidence_ids 必须是字符串数组")
        ranked = []
    if not isinstance(gaps, list) or not all(isinstance(item, str) for item in gaps):
        errors.append("gap_evidence_ids 必须是字符串数组")
        gaps = []
    if not ranked:
        errors.append("ranked_evidence_ids 至少包含一个已验证证据 ID")
    if len(ranked) > max_evidence_anchors:
        errors.append(f"排序证据超过 {max_evidence_anchors} 个上限")
    if len(set(ranked)) != len(ranked) or len(set(gaps)) != len(gaps):
        errors.append("排序契约不得包含重复证据 ID")
    allowed_ids = {
        str(card.get("evidence_id") or "")
        for card in pack.get("evidence_cards") or []
        if isinstance(card, dict)
    }
    unknown_ids = sorted((set(ranked) | set(gaps)) - allowed_ids)
    if unknown_ids:
        errors.append("排序契约包含未知证据 ID：" + ", ".join(unknown_ids[:5]))
    return {
        "ranked_evidence_ids": list(ranked),
        "gap_evidence_ids": list(gaps),
    }, errors


def _render_source_analysis_ranking(
    ranking: dict[str, list[str]],
    *,
    pack: dict[str, Any],
) -> str:
    card_by_id = {
        str(card.get("evidence_id") or ""): card
        for card in pack.get("evidence_cards") or []
        if isinstance(card, dict)
    }
    lines = ["### 已验证证据排序"]
    for index, evidence_id in enumerate(ranking["ranked_evidence_ids"], 1):
        card = card_by_id[evidence_id]
        symbols = ", ".join(str(value) for value in card.get("symbols") or [])
        lines.append(
            f"{index}. **{evidence_id}** — `{card.get('file_path')}:"
            f"{card.get('start_line')}-{card.get('end_line')}`"
            + (f"；symbols: {symbols}" if symbols else "")
        )
    lines.extend(["", "### 待补证据"])
    if ranking["gap_evidence_ids"]:
        for evidence_id in ranking["gap_evidence_ids"]:
            card = card_by_id[evidence_id]
            lines.append(
                f"- **{evidence_id}** — `{card.get('file_path')}:"
                f"{card.get('start_line')}-{card.get('end_line')}` 需要补充证据。"
            )
    else:
        lines.append("- 模型未标记额外证据缺口；后续阶段仍需执行质量门禁。")
    return "\n".join(lines)


def _source_analysis_repair_prompt(
    *,
    raw_content: str,
    validation_errors: list[str],
    pack: dict[str, Any],
) -> str:
    required_evidence = [
        str(card.get("evidence_id") or "")
        for card in pack.get("evidence_cards") or []
        if isinstance(card, dict)
    ]
    return "\n".join(
        [
            "TASK: Repair source-analysis ranking JSON formatting only.",
            "Do not add prose, paths, symbols, facts, or unknown evidence IDs.",
            "Return exactly one JSON object with two string-array fields:",
            '{"ranked_evidence_ids":["SRC-01"],"gap_evidence_ids":[]}',
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
            "- 只允许对已提供并经过 SHA256 校验的 evidence_id 做排序和缺口标记。",
            "- 禁止重新发现文件、猜测未提供的源码、生成 SFMEA、黑盒用例或后续阶段内容。",
            "- 禁止输出路径、行号、symbol、函数名、事实摘要、解释或 Markdown。",
            f"- 最多 {max_anchors} 个证据锚点。",
            "- 只返回一个 JSON 对象，且只能包含 ranked_evidence_ids 与 gap_evidence_ids 两个字符串数组。",
            '- 示例：{"ranked_evidence_ids":["SRC-01"],"gap_evidence_ids":["SRC-02"]}',
            f"- JSON 总长度不得超过 {max_characters} 字符。",
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
        "cache_contract_version": _SOURCE_ANALYSIS_CACHE_VERSION,
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
    temporary = cache_root / f".{cache_key}.{time.time_ns()}.tmp"
    stale: Path | None = None
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
        if entry.exists():
            stale = cache_root / f".{cache_key}.{time.time_ns()}.stale"
            try:
                entry.rename(stale)
            except OSError:
                shutil.rmtree(temporary, ignore_errors=True)
                return
        try:
            temporary.rename(entry)
        except FileExistsError:
            shutil.rmtree(temporary, ignore_errors=True)
    except OSError:
        shutil.rmtree(temporary, ignore_errors=True)
    finally:
        if stale is not None:
            if stale.is_dir():
                shutil.rmtree(stale, ignore_errors=True)
            else:
                with suppress(OSError):
                    stale.unlink()


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
    else:
        opening_fence = re.match(r"^\s*```(?:json)?\s*", content, re.IGNORECASE)
        if opening_fence:
            candidates.insert(0, content[opening_fence.end() :].strip())
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


def _regular_stage_completion_message(outcome: dict[str, Any]) -> str:
    if str(outcome.get("status") or "") == "partial":
        return "已保留部分结果，可继续生成或从本阶段重试"
    if outcome.get("reused"):
        return "已复用通过校验的阶段结果"
    if outcome.get("degraded"):
        return "阶段已降级完成，确定性产物已保存"
    return "阶段已完成，产物已保存"


def _write_json(path: Path, payload: Any) -> None:
    _write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
    )


def _read_json_file(path: Path, default: Any | None = None) -> Any:
    if not path.is_file():
        return {} if default is None else default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = handle.name
        os.replace(temporary_path, path)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)


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
        "business_flow": (
            settings.business_flow_max_tokens,
            {"max_chinese_characters": 4000},
        ),
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
            "- 只返回最高风险 SFMEA JSON 数组；每条必须明确源码机制、触发条件、"
            "局部/上游/下游/最终影响、潜伏性、现有控制、控制缺口、恢复验证、"
            "评分依据、mitigation 和源码/测试映射；每条 mitigation 必须同时写明"
            "具体整改和可执行的测试或监控验证动作。"
        ),
        "black_box_cases": (
            "- 只返回黑盒用例 JSON 数组；条目数量以当前 OUTPUT_SCHEMA 和输出上限为准；"
            "每条必须通过 risk_ids 显式引用现有 SFMEA ID，所有高 RPN 风险必须至少映射一条用例；"
            "test_dimension 必须覆盖且逐字使用 "
            "normal_path、invalid_input、resource_pressure、timeout、reconnect、concurrency、"
            "recovery、performance 八个值，每个至少一条；步骤只能使用外部操作和可观测结果。\n"
            "- 如果包含 MCS/MaxConnections 容量用例，前置条件必须逐字给出 target 启动前命令 "
            "`scripts/rpc.py iscsi_set_options -c 1`；不得写成 "
            "`-c MaxConnectionsPerSession=1`，也不得用客户端连接参数代替。"
        ),
    }
    rule = rules.get(base_stage_id)
    if rule:
        return [rule]
    if artifact.endswith(".json"):
        return ["- 只返回当前 JSON 文件的顶层值，不要包裹 summary/artifacts/path/content。"]
    return []
