"""Independent L2 validation for open-world source behaviour claims."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from app.config import settings
from app.services.agent_cli_bridge import (
    AGENT_ANSWER_DELTA_PREFIX,
    AGENT_FINAL_ANSWER_PREFIX,
    stream_agent_runtime,
)
from app.services.agent_runtimes import get_agent_runtime_sync
from app.services.test_activity_contract import build_behavior_claim_validation_request


_BEHAVIOR_VALIDATION_SCHEMA_VERSION = 2
_FIELD_PATCH_ALLOWLIST = {
    "sfmea_row_behavior": {
        "failure_mode",
        "cause",
        "effect",
        "detection",
        "mitigation",
        "test_mapping",
    },
    "black_box_case_behavior": {
        "scenario_name",
        "preconditions",
        "steps",
        "expected_result",
        "observability",
        "failure_diagnostics",
        "mapped_test_dir",
        "test_dimension",
    },
}


def build_behavior_claim_audit_prompt(request: dict[str, Any]) -> str:
    return "\n".join(
        [
            "你是独立、只读的源码事实审计器。生成模型无权影响你的判断。",
            "逐条判断 claim 是否被给定源码上下文支持。只允许三种状态：",
            "- supports：源码直接支持完整陈述；",
            "- contradicts：源码与陈述相反；",
            "- insufficient：上下文不足，或陈述混合了已证实与未证实内容。",
            "禁止因为文件、符号或 quote 真实就推断整条 claim 为真。",
            "按 claim.type 分层判断，不要用同一条 source-entailment 规则处理所有字段。",
            "source_behavior：完整陈述必须被源码直接支持，否则为 contradicts 或 insufficient。",
            "sfmea_row_behavior：failure_mode、cause、effect、detection 中的实现事实必须被源码支持；mitigation 是建议动作，只检查是否具体、可执行、可验证，不要要求建议动作已经存在于源码；test_mapping 是候选落点，只检查路径真实且场景相关，除非陈述明确声称现有测试已经覆盖。",
            "black_box_case_behavior：核对外部步骤是否可执行、expected_result 与 observability 中的实现事实是否有源码依据、诊断线索是否可行动；test_mapping 是候选落点，不自动等价于已有覆盖。",
            "如果一行混合了已证实事实和错误事实，判 contradicts；只有确实缺少必要上下文时才判 insufficient。",
            "必要时可在当前只读仓库中用 rg/git grep 阅读引用函数及邻近调用方；不得修改文件。",
            "不要修改仓库或报告文件；你的任务是给出三值判断，并为错误行提供最小字段修正建议。",
            "supports 必须返回空 field_patch。contradicts/insufficient 若 claim.type 是 sfmea_row_behavior 或 black_box_case_behavior，field_patch 只填写需要替换的字段，替换值必须与源码上下文一致；禁止修改 ID、评分、technical_claims 或 source_evidence。",
            "证据不足时，field_patch 应删除无依据的实现结论并改写为可执行的待验证操作与 oracle，不能继续把猜测写成事实。",
            "每条结果必须逐字回传输入中的 claim_id 和 binding；claim_id 可能在不同 artifact 中重复，禁止省略 binding。",
            "最终只输出一个 JSON 对象，不要 Markdown：",
            '{"claims":[{"claim_id":"...","binding":"...","status":"supports|contradicts|insufficient","reason":"简洁、具体、可核查","field_patch":{"字段":"与源码一致的替换值"}}]}',
            "VALIDATION_REQUEST:",
            json.dumps(request, ensure_ascii=False, separators=(",", ":")),
        ]
    )


def normalize_behavior_claim_verdicts(
    *,
    raw_output: str,
    request: dict[str, Any],
    validator: dict[str, Any],
) -> dict[str, Any]:
    payload = _extract_json_object(raw_output)
    raw_claims = payload.get("claims") if isinstance(payload, dict) else []
    raw_items = [
        item
        for item in raw_claims or []
        if isinstance(item, dict) and str(item.get("claim_id") or "").strip()
    ]
    if not raw_items:
        detail = str(payload.get("detail") or payload.get("error") or "").strip()
        raise ValueError(
            "独立行为审计器没有返回任何 claim 判断"
            + (f"：{detail}" if detail else "")
        )
    by_bound_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    unbound_by_id: dict[str, list[dict[str, Any]]] = {}
    for item in raw_items:
        claim_id = str(item.get("claim_id") or "").strip()
        binding = str(item.get("binding") or "").strip()
        if binding:
            by_bound_key.setdefault((claim_id, binding), []).append(item)
        else:
            unbound_by_id.setdefault(claim_id, []).append(item)
    normalized: list[dict[str, Any]] = []
    for requested in request.get("claims") or []:
        if not isinstance(requested, dict):
            continue
        claim_id = str(requested.get("claim_id") or "").strip()
        binding = str(requested.get("binding") or "").strip()
        bound_candidates = by_bound_key.get((claim_id, binding), [])
        unbound_candidates = unbound_by_id.get(claim_id, [])
        verdict = (
            bound_candidates.pop(0)
            if bound_candidates
            else unbound_candidates.pop(0)
            if unbound_candidates
            else {}
        )
        status = _normalize_verdict_status(verdict.get("status"))
        reason = str(verdict.get("reason") or "").strip()
        field_patch = _normalize_behavior_field_patch(
            claim_type=str(requested.get("type") or ""),
            value=verdict.get("field_patch"),
        )
        if not verdict:
            status = "insufficient"
            reason = "独立审计器未返回该 claim 的判断"
        elif not reason:
            reason = "独立审计器未给出可核查理由"
            status = "insufficient"
        if status == "supports":
            field_patch = {}
        normalized.append(
            {
                "claim_id": claim_id,
                "binding": binding,
                "status": status,
                "reason": reason,
                "field_patch": field_patch,
                "context_ids": [
                    str(value)
                    for value in requested.get("context_ids") or []
                    if str(value).strip()
                ],
            }
        )
    return {
        "kind": "behavior_claim_validation",
        "schema_version": _BEHAVIOR_VALIDATION_SCHEMA_VERSION,
        "status": "completed",
        "request_sha256": str(request.get("request_sha256") or ""),
        "validator": dict(validator),
        "raw_verdict_count": len(raw_items),
        "claims": normalized,
    }


def partition_behavior_claim_request(
    request: dict[str, Any], *, batch_size: int
) -> list[dict[str, Any]]:
    claims = [item for item in request.get("claims") or [] if isinstance(item, dict)]
    contexts = {
        str(item.get("context_id") or ""): item
        for item in request.get("contexts") or []
        if isinstance(item, dict) and str(item.get("context_id") or "")
    }
    size = max(1, int(batch_size))
    batches: list[dict[str, Any]] = []
    for start in range(0, len(claims), size):
        batch_claims = claims[start : start + size]
        used_context_ids = {
            str(context_id)
            for claim in batch_claims
            for context_id in claim.get("context_ids") or []
            if str(context_id)
        }
        batches.append(
            {
                **request,
                "batch_index": len(batches) + 1,
                "claims": batch_claims,
                "contexts": [
                    context
                    for context_id, context in contexts.items()
                    if context_id in used_context_ids
                ],
            }
        )
    return batches


async def materialize_behavior_claim_validation(
    *,
    artifact_dir: str | Path,
    repo_path: str | Path,
    generator_identity: str,
    request: dict[str, Any] | None = None,
    runtime_loader: Callable[[str], dict[str, Any] | None] = get_agent_runtime_sync,
    streamer: Callable[..., AsyncIterator[str]] = stream_agent_runtime,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    root = Path(artifact_dir)
    root.mkdir(parents=True, exist_ok=True)
    output_path = root / "behavior_claim_validation.json"
    request_payload = request or build_behavior_claim_validation_request(
        artifact_dir=root,
        repo_path=repo_path,
        max_claims=int(settings.behavior_claim_audit_max_claims),
        context_chars=int(settings.behavior_claim_audit_context_chars),
    )
    if not request_payload.get("claims"):
        result = {
            "kind": "behavior_claim_validation",
            "schema_version": _BEHAVIOR_VALIDATION_SCHEMA_VERSION,
            "status": "not_applicable",
            "request_sha256": str(request_payload.get("request_sha256") or ""),
            "validator": {"independent": False},
            "claims": [],
        }
        _write_json(output_path, result)
        return result

    existing = _read_json(output_path)
    if _validation_covers_request(existing, request_payload):
        _notify_progress(
            on_progress,
            {
                "kind": "stage_reused",
                "stage_id": "behavior_claim_validation",
                "status": "completed",
                "claim_count": len(request_payload.get("claims") or []),
                "model": str(settings.behavior_claim_audit_model or ""),
                "user_message": "已复用与当前断言绑定一致的独立源码事实核验",
            },
        )
        return {**existing, "reused": True}

    runtime_id = str(settings.behavior_claim_audit_runtime_id or "default-codex")
    runtime = runtime_loader(runtime_id)
    validator = {
        "provider": str((runtime or {}).get("provider") or ""),
        "runtime_id": runtime_id,
        "model": str(settings.behavior_claim_audit_model or ""),
        "reasoning_effort": str(settings.behavior_claim_audit_reasoning_effort or ""),
        "generator_identity": str(generator_identity or ""),
        "independent": bool(
            runtime
            and str((runtime or {}).get("provider") or "").strip()
            and str((runtime or {}).get("provider") or "").strip().lower()
            not in str(generator_identity or "").strip().lower()
        ),
    }
    if not settings.behavior_claim_audit_enabled or not runtime or not runtime.get("enabled", True):
        return _write_unavailable_validation(
            output_path=output_path,
            request=request_payload,
            validator=validator,
            reason="独立行为审计执行器未启用或不可用",
        )
    if not validator["independent"]:
        return _write_unavailable_validation(
            output_path=output_path,
            request=request_payload,
            validator=validator,
            reason="行为审计器与生成执行器不独立",
        )

    reusable_verdicts = _reusable_bound_verdicts(
        existing=existing,
        validator=validator,
    )
    pending_claims = [
        claim
        for claim in request_payload.get("claims") or []
        if isinstance(claim, dict)
        and (
            str(claim.get("claim_id") or ""),
            str(claim.get("binding") or ""),
        )
        not in reusable_verdicts
    ]
    reused_claim_count = len(request_payload.get("claims") or []) - len(pending_claims)
    if not pending_claims:
        result = {
            **existing,
            "request_sha256": str(request_payload.get("request_sha256") or ""),
            "validator": validator,
            "raw_verdict_count": len(reusable_verdicts),
            "claims": [
                reusable_verdicts[
                    (
                        str(claim.get("claim_id") or ""),
                        str(claim.get("binding") or ""),
                    )
                ]
                for claim in request_payload.get("claims") or []
                if isinstance(claim, dict)
            ],
            "reused": True,
            "reused_claim_count": reused_claim_count,
            "validated_claim_count": 0,
            "duration_ms": 0.0,
        }
        _write_json(output_path, result)
        _notify_progress(
            on_progress,
            {
                "kind": "stage_reused",
                "stage_id": "behavior_claim_validation",
                "status": "completed",
                "claim_count": reused_claim_count,
                "model": str(settings.behavior_claim_audit_model or ""),
                "user_message": "已复用与当前断言绑定一致的独立源码事实核验",
            },
        )
        return result

    incremental_request = _behavior_claim_request_subset(
        request_payload,
        pending_claims,
    )

    runtime = _audit_runtime(runtime, artifact_dir=root)
    diagnostics_dir = root / "behavior_claim_audit"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    reset_behavior_claim_audit_diagnostics(diagnostics_dir)
    (diagnostics_dir / "request.json").write_text(
        json.dumps(request_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if reused_claim_count:
        (diagnostics_dir / "incremental_request.json").write_text(
            json.dumps(incremental_request, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    batches = partition_behavior_claim_request(
        incremental_request,
        batch_size=int(settings.behavior_claim_audit_batch_size),
    )
    (diagnostics_dir / "prompt.txt").write_text(
        "\n\n".join(
            f"# Batch {index}\n{build_behavior_claim_audit_prompt(batch)}"
            for index, batch in enumerate(batches, start=1)
        ),
        encoding="utf-8",
    )
    _notify_progress(
        on_progress,
        {
            "kind": "stage_provider_started",
            "stage_id": "behavior_claim_validation",
            "status": "running",
            "claim_count": len(pending_claims),
            "model": str(settings.behavior_claim_audit_model or ""),
            "user_message": (
                f"已复用 {reused_claim_count} 条绑定未变的判断，正在核验 "
                f"{len(pending_claims)} 条变更事实"
                if reused_claim_count
                else "正在使用独立审计器核验 "
                f"{len(pending_claims)} 条源码事实"
            ),
        },
    )
    started = time.monotonic()
    semaphore = asyncio.Semaphore(int(settings.behavior_claim_audit_concurrency))
    tasks = [
        asyncio.create_task(
            _validate_behavior_claim_batch(
                index=index,
                request=batch,
                runtime=runtime,
                validator=validator,
                streamer=streamer,
                repo_path=str(Path(repo_path)),
                diagnostics_dir=diagnostics_dir,
                semaphore=semaphore,
            )
        )
        for index, batch in enumerate(batches, start=1)
    ]
    effective_timeout_seconds = float(settings.behavior_claim_audit_timeout_seconds)
    if timeout_seconds is not None:
        effective_timeout_seconds = min(
            effective_timeout_seconds,
            max(0.001, float(timeout_seconds)),
        )
    try:
        done, pending = await asyncio.wait(
            tasks,
            timeout=effective_timeout_seconds,
        )
    except asyncio.CancelledError:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    batch_results: list[dict[str, Any]] = []
    for index, (task, batch) in enumerate(zip(tasks, batches), start=1):
        if task in done:
            try:
                batch_results.append(task.result())
                continue
            except (OSError, RuntimeError, ValueError) as exc:
                reason = f"独立行为审计批次 {index} 失败：{type(exc).__name__}: {exc}"
        else:
            reason = f"独立行为审计批次 {index} 超过总时间预算"
        batch_results.append(
            _unavailable_validation(
                request=batch,
                validator=validator,
                reason=reason,
            )
        )
    ordered_claims: list[dict[str, Any]] = []
    verdicts = {
        (
            str(item.get("claim_id") or ""),
            str(item.get("binding") or ""),
        ): item
        for batch_result in batch_results
        for item in batch_result.get("claims") or []
        if isinstance(item, dict)
    }
    verdicts = {**reusable_verdicts, **verdicts}
    for claim in request_payload.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        claim_id = str(claim.get("claim_id") or "")
        binding = str(claim.get("binding") or "")
        ordered_claims.append(
            verdicts.get((claim_id, binding))
            or {
                "claim_id": claim_id,
                "binding": binding,
                "status": "insufficient",
                "reason": "独立审计器未返回该 claim 的判断",
                "context_ids": list(claim.get("context_ids") or []),
            }
        )
    completed_batches = sum(
        str(item.get("status") or "") == "completed" for item in batch_results
    )
    result = {
        "kind": "behavior_claim_validation",
        "schema_version": _BEHAVIOR_VALIDATION_SCHEMA_VERSION,
        "status": "completed" if completed_batches else "unavailable",
        "request_sha256": str(request_payload.get("request_sha256") or ""),
        "validator": validator,
        "batch_count": len(batches),
        "completed_batch_count": completed_batches,
        "raw_verdict_count": len(ordered_claims),
        "claims": ordered_claims,
        "reused_claim_count": reused_claim_count,
        "validated_claim_count": len(pending_claims),
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
    }
    (diagnostics_dir / "raw_output.txt").write_text(
        json.dumps(batch_results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_json(output_path, result)
    _notify_progress(
        on_progress,
        {
            "kind": (
                "stage_completed"
                if str(result.get("status") or "") == "completed"
                else "stage_unavailable"
            ),
            "stage_id": "behavior_claim_validation",
            "status": (
                "completed"
                if str(result.get("status") or "") == "completed"
                else "partial"
            ),
            "claim_count": len(result.get("claims") or []),
            "model": str(settings.behavior_claim_audit_model or ""),
            "duration_ms": result.get("duration_ms"),
            "user_message": (
                "独立源码事实核验完成"
                if str(result.get("status") or "") == "completed"
                else "独立源码事实核验不可用，当前产物不会被标记为可交付"
            ),
        },
    )
    return result


def _reusable_bound_verdicts(
    *,
    existing: Any,
    validator: dict[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    if (
        not isinstance(existing, dict)
        or existing.get("status") != "completed"
        or int(existing.get("schema_version") or 0)
        != _BEHAVIOR_VALIDATION_SCHEMA_VERSION
    ):
        return {}
    previous_validator = existing.get("validator")
    if not isinstance(previous_validator, dict) or not previous_validator.get(
        "independent"
    ):
        return {}
    for key in ("provider", "runtime_id", "model", "reasoning_effort"):
        if str(previous_validator.get(key) or "") != str(validator.get(key) or ""):
            return {}
    reusable: dict[tuple[str, str], dict[str, Any]] = {}
    for item in existing.get("claims") or []:
        if not isinstance(item, dict):
            continue
        claim_id = str(item.get("claim_id") or "")
        binding = str(item.get("binding") or "")
        if not claim_id or not binding:
            continue
        reusable[(claim_id, binding)] = dict(item)
    return reusable


def _behavior_claim_request_subset(
    request: dict[str, Any],
    claims: list[dict[str, Any]],
) -> dict[str, Any]:
    used_context_ids = {
        str(context_id)
        for claim in claims
        for context_id in claim.get("context_ids") or []
        if str(context_id)
    }
    return {
        **request,
        "claims": list(claims),
        "contexts": [
            context
            for context in request.get("contexts") or []
            if isinstance(context, dict)
            and str(context.get("context_id") or "") in used_context_ids
        ],
    }


async def _validate_behavior_claim_batch(
    *,
    index: int,
    request: dict[str, Any],
    runtime: dict[str, Any],
    validator: dict[str, Any],
    streamer: Callable[..., AsyncIterator[str]],
    repo_path: str,
    diagnostics_dir: Path,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    batch_dir = diagnostics_dir / f"batch_{index:02d}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    prompt = build_behavior_claim_audit_prompt(request)
    _write_json(batch_dir / "request.json", request)
    (batch_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    batch_runtime = {**runtime, "env": dict(runtime.get("env") or {})}
    runtime_dir = (batch_dir / "runtime").resolve()
    batch_runtime["env"]["CODETALK_AGENT_ARTIFACT_DIR"] = str(runtime_dir)
    try:
        async with semaphore:
            raw_output = await _collect_agent_answer(
                streamer=streamer,
                runtime=batch_runtime,
                prompt=prompt,
                cwd=repo_path,
            )
    finally:
        shutil.rmtree(runtime_dir, ignore_errors=True)
    (batch_dir / "raw_output.txt").write_text(raw_output, encoding="utf-8")
    return normalize_behavior_claim_verdicts(
        raw_output=raw_output,
        request=request,
        validator=validator,
    )


def _notify_progress(
    callback: Callable[[dict[str, Any]], None] | None,
    payload: dict[str, Any],
) -> None:
    if callback is not None:
        callback(payload)


def reset_behavior_claim_audit_diagnostics(diagnostics_dir: Path) -> None:
    for path in diagnostics_dir.glob("batch_*"):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    (diagnostics_dir / "raw_output.txt").unlink(missing_ok=True)


async def _collect_agent_answer(
    *,
    streamer: Callable[..., AsyncIterator[str]],
    runtime: dict[str, Any],
    prompt: str,
    cwd: str,
) -> str:
    streamed: list[str] = []
    final: list[str] = []
    async for delta in streamer(
        runtime=runtime,
        prompt=prompt,
        cwd=cwd,
        resume_session_id=None,
    ):
        text = str(delta or "")
        if text.startswith(AGENT_FINAL_ANSWER_PREFIX):
            final.append(text[len(AGENT_FINAL_ANSWER_PREFIX) :])
        elif text.startswith(AGENT_ANSWER_DELTA_PREFIX):
            streamed.append(text[len(AGENT_ANSWER_DELTA_PREFIX) :])
        else:
            streamed.append(text)
    return "".join(final or streamed).strip()


def _audit_runtime(runtime: dict[str, Any], *, artifact_dir: Path) -> dict[str, Any]:
    configured = {**runtime, "env": dict(runtime.get("env") or {})}
    configured["timeout_seconds"] = int(settings.behavior_claim_audit_timeout_seconds)
    configured["env"]["CODETALK_AGENT_ARTIFACT_DIR"] = str(
        (artifact_dir / "behavior_claim_audit" / "runtime").resolve()
    )
    if str(configured.get("provider") or "").strip().lower() == "codex":
        configured["args"] = [
            "-m",
            str(settings.behavior_claim_audit_model),
            "-c",
            f'model_reasoning_effort="{settings.behavior_claim_audit_reasoning_effort}"',
            "exec",
            "--ephemeral",
            "--ignore-rules",
            "-s",
            "read-only",
        ]
        configured["resume_args"] = []
        configured["session_persistence"] = "none"
    return configured


def _normalize_verdict_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    aliases = {
        "supported": "supports",
        "verified": "supports",
        "contradicted": "contradicts",
        "contradiction": "contradicts",
        "unknown": "insufficient",
        "not_enough_evidence": "insufficient",
    }
    status = aliases.get(status, status)
    return status if status in {"supports", "contradicts", "insufficient"} else "insufficient"


def _normalize_behavior_field_patch(*, claim_type: str, value: Any) -> dict[str, Any]:
    allowed = _FIELD_PATCH_ALLOWLIST.get(str(claim_type or "").strip(), set())
    if not allowed or not isinstance(value, dict):
        return {}
    normalized: dict[str, Any] = {}
    for key, replacement in value.items():
        if key not in allowed:
            continue
        if isinstance(replacement, str):
            text = replacement.strip()
            if text:
                normalized[key] = text[:4000]
        elif isinstance(replacement, list):
            items = [
                str(item).strip()[:1000]
                for item in replacement
                if str(item).strip()
            ][:20]
            if items:
                normalized[key] = items
    return normalized


def _extract_json_object(raw_output: str) -> dict[str, Any]:
    text = str(raw_output or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("独立行为审计器没有返回可解析的 JSON 对象")


def _validation_covers_request(existing: Any, request: dict[str, Any]) -> bool:
    if (
        not isinstance(existing, dict)
        or existing.get("status") != "completed"
        or int(existing.get("schema_version") or 0)
        != _BEHAVIOR_VALIDATION_SCHEMA_VERSION
    ):
        return False
    if str(existing.get("request_sha256") or "") != str(request.get("request_sha256") or ""):
        return False
    if not bool((existing.get("validator") or {}).get("independent")):
        return False
    if int(existing.get("raw_verdict_count") or 0) <= 0:
        return False
    current = {
        (str(item.get("claim_id") or ""), str(item.get("binding") or ""))
        for item in existing.get("claims") or []
        if isinstance(item, dict)
    }
    requested = {
        (str(item.get("claim_id") or ""), str(item.get("binding") or ""))
        for item in request.get("claims") or []
        if isinstance(item, dict)
    }
    return bool(requested) and requested <= current


def _write_unavailable_validation(
    *,
    output_path: Path,
    request: dict[str, Any],
    validator: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    result = _unavailable_validation(
        request=request,
        validator=validator,
        reason=reason,
    )
    _write_json(output_path, result)
    return result


def _unavailable_validation(
    *,
    request: dict[str, Any],
    validator: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    return {
        "kind": "behavior_claim_validation",
        "schema_version": _BEHAVIOR_VALIDATION_SCHEMA_VERSION,
        "status": "unavailable",
        "request_sha256": str(request.get("request_sha256") or ""),
        "validator": dict(validator),
        "claims": [
            {
                "claim_id": str(item.get("claim_id") or ""),
                "binding": str(item.get("binding") or ""),
                "status": "insufficient",
                "reason": reason,
                "context_ids": list(item.get("context_ids") or []),
            }
            for item in request.get("claims") or []
            if isinstance(item, dict)
        ],
    }


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
