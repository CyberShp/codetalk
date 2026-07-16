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
            "不要修复报告；你的唯一任务是逐条给出三值事实判断。",
            "最终只输出一个 JSON 对象，不要 Markdown：",
            '{"claims":[{"claim_id":"...","status":"supports|contradicts|insufficient","reason":"简洁、具体、可核查"}]}',
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
    by_id = {
        str(item.get("claim_id") or "").strip(): item
        for item in raw_claims or []
        if isinstance(item, dict) and str(item.get("claim_id") or "").strip()
    }
    if not by_id:
        detail = str(payload.get("detail") or payload.get("error") or "").strip()
        raise ValueError(
            "独立行为审计器没有返回任何 claim 判断"
            + (f"：{detail}" if detail else "")
        )
    normalized: list[dict[str, Any]] = []
    for requested in request.get("claims") or []:
        if not isinstance(requested, dict):
            continue
        claim_id = str(requested.get("claim_id") or "").strip()
        verdict = by_id.get(claim_id, {})
        status = _normalize_verdict_status(verdict.get("status"))
        reason = str(verdict.get("reason") or "").strip()
        if not verdict:
            status = "insufficient"
            reason = "独立审计器未返回该 claim 的判断"
        elif not reason:
            reason = "独立审计器未给出可核查理由"
            status = "insufficient"
        normalized.append(
            {
                "claim_id": claim_id,
                "binding": str(requested.get("binding") or ""),
                "status": status,
                "reason": reason,
                "context_ids": [
                    str(value)
                    for value in requested.get("context_ids") or []
                    if str(value).strip()
                ],
            }
        )
    return {
        "kind": "behavior_claim_validation",
        "schema_version": 1,
        "status": "completed",
        "request_sha256": str(request.get("request_sha256") or ""),
        "validator": dict(validator),
        "raw_verdict_count": len(by_id),
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
            "schema_version": 1,
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

    runtime = _audit_runtime(runtime, artifact_dir=root)
    diagnostics_dir = root / "behavior_claim_audit"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    reset_behavior_claim_audit_diagnostics(diagnostics_dir)
    (diagnostics_dir / "request.json").write_text(
        json.dumps(request_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    batches = partition_behavior_claim_request(
        request_payload,
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
            "claim_count": len(request_payload.get("claims") or []),
            "model": str(settings.behavior_claim_audit_model or ""),
            "user_message": (
                "正在使用独立审计器核验 "
                f"{len(request_payload.get('claims') or [])} 条源码事实"
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
    done, pending = await asyncio.wait(
        tasks,
        timeout=float(settings.behavior_claim_audit_timeout_seconds),
    )
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
        str(item.get("claim_id") or ""): item
        for batch_result in batch_results
        for item in batch_result.get("claims") or []
        if isinstance(item, dict)
    }
    for claim in request_payload.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        claim_id = str(claim.get("claim_id") or "")
        ordered_claims.append(
            verdicts.get(claim_id)
            or {
                "claim_id": claim_id,
                "binding": str(claim.get("binding") or ""),
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
        "schema_version": 1,
        "status": "completed" if completed_batches else "unavailable",
        "request_sha256": str(request_payload.get("request_sha256") or ""),
        "validator": validator,
        "batch_count": len(batches),
        "completed_batch_count": completed_batches,
        "raw_verdict_count": sum(
            int(item.get("raw_verdict_count") or 0) for item in batch_results
        ),
        "claims": ordered_claims,
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
    batch_runtime["env"]["CODETALK_AGENT_ARTIFACT_DIR"] = str(
        (batch_dir / "runtime").resolve()
    )
    async with semaphore:
        raw_output = await _collect_agent_answer(
            streamer=streamer,
            runtime=batch_runtime,
            prompt=prompt,
            cwd=repo_path,
        )
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
    if not isinstance(existing, dict) or existing.get("status") != "completed":
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
        "schema_version": 1,
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
