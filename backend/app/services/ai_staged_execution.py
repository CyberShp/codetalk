from __future__ import annotations

import asyncio
import inspect
import json
import re
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.services.ai_thread_artifacts import _validate_schema, materialize_ai_thread_manifest


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
CancellationCallback = Callable[[], Awaitable[bool] | bool]
_CANCELLATION_POLL_INTERVAL = 0.1


class StagedExecutionCancelled(RuntimeError):
    """Raised after cancelling an in-flight staged provider request."""


async def _complete_with_cancellation(
    *,
    llm: Any,
    prompt: str,
    max_tokens: int,
    is_cancelled: CancellationCallback | None,
) -> Any:
    provider_task = asyncio.create_task(
        llm.complete(
            [{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.2,
        )
    )
    if is_cancelled is None:
        return await provider_task
    try:
        while True:
            done, _ = await asyncio.wait(
                {provider_task},
                timeout=_CANCELLATION_POLL_INTERVAL,
            )
            if provider_task in done:
                return provider_task.result()
            if await _callback_true(is_cancelled):
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
            "purpose": "读取源码、测试目录和输入材料，形成可验证证据锚点",
            "support": True,
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
) -> dict[str, Any]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    _write_json(artifact_dir / "staged_execution_plan.json", plan)
    completed: dict[str, Path] = {}
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
        while attempts < 2:
            attempts += 1
            try:
                response = await _complete_with_cancellation(
                    llm=llm,
                    prompt=prompt,
                    max_tokens=max_tokens,
                    is_cancelled=is_cancelled,
                )
                if await _callback_true(is_cancelled):
                    raise StagedExecutionCancelled("任务已取消，已停止后续阶段")
                if bool(getattr(response, "truncated", False)):
                    raise ValueError("provider_output_truncated")
                raw_content = str(getattr(response, "content", "") or "").strip()
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
                _write_text(stage_dir / f"raw_output_attempt_{attempts}.txt", raw_content)
                break
            except Exception as exc:
                if isinstance(exc, StagedExecutionCancelled):
                    raise
                last_error = str(exc) or exc.__class__.__name__
                response = None
                rendered = None
            prompt = "\n".join(
                [
                    prompt,
                    "",
                    "RETRY_AFTER_STAGE_FAILURE:",
                    f"  previous attempt failed validation or transport: {last_error}",
                    "  return only the declared artifact, complete and valid.",
                ]
            )
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
        _write_text(stage_dir / "raw_output.txt", raw_content)
        output_path = artifact_dir / artifact
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(rendered, str):
            _write_text(output_path, rendered)
        else:
            _write_json(output_path, rendered)
        completed[stage_id] = output_path
        result = {
            "stage_id": stage_id,
            "status": "completed",
            "artifact": artifact,
            "attempts": attempts,
            "size_bytes": output_path.stat().st_size,
        }
        _write_json(stage_dir / "stage_result.json", result)
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
    }
    _write_json(artifact_dir / "staged_execution_result.json", execution)
    return {**execution, "artifact_manifest": manifest}


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
    for stage_id in stage.get("depends_on") or []:
        path = completed.get(str(stage_id))
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
            "RULES:",
            "- Read and preserve the complete original user request.",
            "- Use only source/test evidence supplied here or in accepted previous artifacts.",
            "- Mark unverified design proposals explicitly; do not claim execution.",
            f"- {output_rule}",
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
