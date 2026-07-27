"""Independent L2 validation for open-world source behaviour claims."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import shutil
import sqlite3
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
        "oracle_basis",
    },
}


def build_behavior_claim_audit_readiness(
    *,
    required: bool,
    generator_identities: list[str] | None = None,
) -> dict[str, Any]:
    """Check the local configuration needed for independent fact validation.

    This deliberately performs no model request.  It is used both when a task
    is prepared and immediately before it starts, so a missing audit route is
    reported in seconds instead of after an otherwise successful Agent turn.
    """
    generators = [str(item or "").strip().lower() for item in generator_identities or []]
    if not required or not settings.behavior_claim_audit_enabled:
        return {
            "status": "not_required",
            "required": False,
            "mode": "disabled" if not settings.behavior_claim_audit_enabled else "not_applicable",
            "message": "当前工作流不要求独立源码事实核验。",
            "recommended_action": "",
        }

    try:
        connection = sqlite3.connect(str(settings.sqlite_db))
        try:
            rows = connection.execute(
                "SELECT key, value FROM settings WHERE key IN (?, ?)",
                ("behavior_claim_audit_model_id", "active_chat_model_id"),
            ).fetchall()
            values = {str(key): str(value or "") for key, value in rows}
            audit_id = values.get("behavior_claim_audit_model_id", "").strip()
            active_id = values.get("active_chat_model_id", "").strip()

            def configured_chat_model(config_id: str) -> bool:
                if not config_id:
                    return False
                row = connection.execute(
                    "SELECT is_chat_model FROM llm_configs WHERE id = ?", (config_id,)
                ).fetchone()
                return bool(row and row[0])

            if audit_id:
                if configured_chat_model(audit_id):
                    return {
                        "status": "ready",
                        "required": True,
                        "mode": "configured_independent_model",
                        "model_config_id": audit_id,
                        "message": "独立源码事实核验模型已配置。",
                        "recommended_action": "",
                    }
                return {
                    "status": "blocked",
                    "required": True,
                    "mode": "invalid_independent_model",
                    "message": "独立质量核验模型配置不存在或不是聊天模型。",
                    "recommended_action": "请在设置中重新选择一个可用的独立质量核验模型。",
                }

            # For an external Codex run, the historical automatic route uses
            # the active chat model as the independent auditor.  Validate that
            # exact dependency up front rather than letting final delivery
            # turn every unreviewed claim into 'insufficient'.
            needs_active_chat = (
                str(settings.behavior_claim_audit_runtime_id or "auto") == "auto"
                and any("codex" in identity for identity in generators)
            )
            if needs_active_chat:
                if configured_chat_model(active_id):
                    return {
                        "status": "ready",
                        "required": True,
                        "mode": "active_chat_model_for_codex_audit",
                        "model_config_id": active_id,
                        "message": "将使用当前活跃聊天模型执行独立源码事实核验。",
                        "recommended_action": "建议在设置中单独指定独立质量核验模型，以固定审计路由。",
                    }
                return {
                    "status": "blocked",
                    "required": True,
                    "mode": "missing_active_chat_model",
                    "message": "本工作流需要独立源码事实核验，但未配置可用的活跃聊天模型。",
                    "recommended_action": "请在设置中选择活跃聊天模型，或单独指定独立质量核验模型后重新启动。",
                }
        finally:
            connection.close()
    except (OSError, sqlite3.Error) as exc:
        return {
            "status": "blocked",
            "required": True,
            "mode": "configuration_store_unavailable",
            "message": "无法读取独立质量核验配置。",
            "recommended_action": "请确认 CodeTalk 设置数据库可访问后重试。",
            "diagnostic": f"{type(exc).__name__}: {exc}",
        }

    return {
        "status": "ready",
        "required": True,
        "mode": "agent_runtime_auditor",
        "message": "将使用已配置的独立 Agent 审计器执行源码事实核验。",
        "recommended_action": "",
    }


def build_behavior_claim_audit_prompt(request: dict[str, Any]) -> str:
    return "\n".join(
        [
            "你是独立、只读的源码事实审计器。生成模型无权影响你的判断。",
            "逐条判断 claim 是否被给定源码上下文支持。只允许三种状态：",
            "- supports：源码直接支持陈述；",
            "- contradicts：源码与陈述相反，或遗漏的关键条件会反转陈述含义；",
            "- insufficient：上下文不足，或陈述混合了已证实与未证实内容。",
            "禁止因为文件、符号或 quote 真实就推断整条 claim 为真。",
            "不得把准确的局部源码事实误判为 contradicts：若 quote 直接展示该赋值、调用、返回或释放，"
            "外围 guard、调用者或后续分支没有改变该局部事实时，应判 supports。准确的常用语义概括"
            "（例如 rejects、frees、returns a negative error）也应支持；日志文字同样允许保持对象与信息内容的"
            "同义概括，不要求逐字复述 format string。只有概括与源码值、对象或条件冲突时才阻断。",
            "每个 claim 的 evidence_bindings 给出经哈希绑定的 symbol、行号和 quote。symbol 是该代码片段"
            "所属函数/声明的权威名称；即使截取上下文未包含声明行，也不得把这个函数内的 return/assignment"
            "误归属到调用者。",
            "按 claim.type 分层判断，不要用同一条 source-entailment 规则处理所有字段。",
            "source_behavior：完整陈述必须被源码直接支持，否则为 contradicts 或 insufficient。",
            "sfmea_row_behavior：failure_mode、cause、effect、detection 中的实现事实必须被源码支持；mitigation 是建议动作，只检查是否具体、可执行、可验证，不要要求建议动作已经存在于源码；test_mapping 是候选落点，只检查路径真实且场景相关，除非陈述明确声称现有测试已经覆盖。",
            "若 sfmea_row_behavior 的 statement.risk_status 是 test_hypothesis，且 statement.evidence_interpretation 明确说明它是待故障注入验证的假设：不得把它当作已观测源码缺陷。只核对源码锚点没有被误述，以及假设是否已明确限定为待验证；满足时返回 supports，field_patch 为空。",
            "SFMEA 行若只描述正常保护行为或测试覆盖缺口，或者只描述预期错误返回、构建配置差异，必须判 contradicts；这些不是可评分失效模式。",
            "black_box_case_behavior：核对外部步骤是否可执行、expected_result 与 observability 中的实现事实是否有源码依据、诊断线索是否可行动；必须核对命令行选项的真实语义，不得把重连期限、初始连接超时或其他参数角色混淆；test_mapping 是候选落点，不自动等价于已有覆盖。",
            "若 black_box_case_behavior 的 statement.case_type 是 black_box_hypothesis，预期结果、观测和诊断必须明确是待验证假设；不得要求源码已经实现该测试的测量点或 harness。它仍必须包含可执行的外部操作和可观测 oracle。",
            "对资源上限、超时、长稳态、计数翻转和性能阈值，必须检查 oracle_basis 是否来自源码常量、真实配置、规范或同环境基线；性能还必须有预热、重复采样和 P50/P95。没有依据时判 insufficient，依据与源码冲突时判 contradicts。",
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
        elif _verdict_reason_explicitly_confirms_support(
            status=status,
            reason=reason,
            field_patch=field_patch,
        ):
            # Some providers occasionally emit an internally inconsistent JSON
            # verdict: `contradicts` with a final conclusion that the claim is
            # supported and no corrective patch.  Preserve a real negative
            # verdict whenever it proposes a patch; otherwise trust its explicit
            # conclusion rather than blocking a source-backed deliverable.
            status = "supports"
            reason = f"审计器状态自相矛盾，按其最终结论归一化为支持：{reason}"
        if status == "supports":
            field_patch = {}
        elif (
            str(requested.get("type") or "") == "black_box_case_behavior"
            and "oracle_basis" not in field_patch
        ):
            field_patch["oracle_basis"] = (
                "判据以运行前登记的公开配置、命令退出码、stderr、日志及资源状态"
                "前后差异为准；当前源码证据不足的结果标记为待验证，不预设具体错误码、"
                "恢复或清理结论。"
            )
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
        **_request_coverage_fields(request),
        "claims": normalized,
    }


def _request_coverage_fields(request: dict[str, Any]) -> dict[str, Any]:
    claims = [item for item in request.get("claims") or [] if isinstance(item, dict)]
    candidate_count = max(len(claims), int(request.get("candidate_count") or 0))
    requested_count = int(request.get("requested_count") or len(claims))
    return {
        "candidate_count": candidate_count,
        "requested_count": requested_count,
        "truncated": bool(request.get("truncated")) or candidate_count > requested_count,
    }


def _verdict_reason_explicitly_confirms_support(
    *, status: str, reason: str, field_patch: dict[str, Any]
) -> bool:
    if status != "contradicts" or field_patch:
        return False
    normalized = " ".join(str(reason or "").strip().lower().split())
    return bool(
        re.search(
            r"(?:the )?(?:claim|statement|assertion) is (?:fully )?supported\.?$"
            r"|(?:changing|change) to supports\.?$",
            normalized,
        )
    )


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
    llm_factory: Callable[[], Any] | None = None,
    builtin_audit_loader: Callable[[], Any] | None = None,
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
            **_request_coverage_fields(request_payload),
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
                "model": str(
                    (existing.get("validator") or {}).get("model") or ""
                ),
                "user_message": "已复用与当前断言绑定一致的独立源码事实核验",
            },
        )
        return {**existing, "reused": True}

    generator_key = str(generator_identity or "").strip().lower()
    generator_model = generator_key.removeprefix("builtin-llm:").strip()
    configured_audit: tuple[Any, str, str] | None = None
    if generator_key.startswith("builtin-llm:"):
        try:
            if builtin_audit_loader is None:
                from app.llm.factory import create_behavior_claim_audit_llm_client

                builtin_audit_loader = create_behavior_claim_audit_llm_client
            loaded = builtin_audit_loader()
            configured_audit = await loaded if inspect.isawaitable(loaded) else loaded
        except (OSError, RuntimeError, ValueError) as exc:
            return _write_unavailable_validation(
                output_path=output_path,
                request=request_payload,
                validator={"provider": "builtin-llm", "independent": False},
                reason=f"无法创建独立质量核验模型：{type(exc).__name__}: {exc}",
            )
    configured_runtime_id = str(settings.behavior_claim_audit_runtime_id or "auto")
    use_builtin_llm = bool(configured_audit) or (
        configured_runtime_id == "auto" and "codex" in generator_key
    )
    runtime_id = (
        f"llm-config:{configured_audit[1]}"
        if configured_audit
        else "active-chat-model"
        if use_builtin_llm
        else (
        "default-codex" if configured_runtime_id == "auto" else configured_runtime_id
        )
    )
    runtime = None if use_builtin_llm else runtime_loader(runtime_id)
    runtime_provider = str((runtime or {}).get("provider") or "").strip().lower()
    if runtime_provider and runtime_provider in generator_key and "codex" in generator_key:
        use_builtin_llm = True
        runtime_id = "active-chat-model"
        runtime = None
        runtime_provider = "builtin-llm"
    validator = {
        "provider": "builtin-llm" if use_builtin_llm else runtime_provider,
        "runtime_id": runtime_id,
        "model": (
            str(configured_audit[2] or "")
            if configured_audit
            else "active-chat-model"
            if use_builtin_llm
            else str(settings.behavior_claim_audit_model or "")
        ),
        "reasoning_effort": "provider-default" if use_builtin_llm else str(settings.behavior_claim_audit_reasoning_effort or ""),
        "generator_identity": str(generator_identity or ""),
        "independent": bool(
            (
                configured_audit
                and bool(configured_audit[2])
                and str(configured_audit[2]).strip().lower() != generator_model
            )
            or (not configured_audit and use_builtin_llm)
            or (runtime_provider and runtime_provider not in generator_key)
        ),
    }
    if not settings.behavior_claim_audit_enabled or (
        not use_builtin_llm and (not runtime or not runtime.get("enabled", True))
    ):
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
            **_request_coverage_fields(request_payload),
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
                "model": str(validator.get("model") or ""),
                "user_message": "已复用与当前断言绑定一致的独立源码事实核验",
            },
        )
        return result

    incremental_request = _behavior_claim_request_subset(
        request_payload,
        pending_claims,
    )

    llm_client = None
    if use_builtin_llm:
        try:
            if configured_audit:
                llm_client = configured_audit[0]
            elif llm_factory is None:
                from app.llm.factory import create_llm_client_from_active

                llm_factory = create_llm_client_from_active
            if not configured_audit:
                created = llm_factory()
                llm_client = await created if inspect.isawaitable(created) else created
        except (OSError, RuntimeError, ValueError) as exc:
            return _write_unavailable_validation(
                output_path=output_path,
                request=request_payload,
                validator=validator,
                reason=f"无法创建独立内置模型审计器：{type(exc).__name__}: {exc}",
            )
    else:
        runtime = _audit_runtime(runtime or {}, artifact_dir=root)
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
            "model": str(validator.get("model") or ""),
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
            _validate_behavior_claim_batch_with_adaptive_split(
                index=index,
                request=batch,
                runtime=runtime,
                validator=validator,
                streamer=streamer,
                llm_client=llm_client,
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
    done: set[asyncio.Task[dict[str, Any]]] = set()
    pending: set[asyncio.Task[dict[str, Any]]] = set(tasks)
    heartbeat_seconds = float(settings.behavior_claim_audit_heartbeat_seconds)
    deadline = started + effective_timeout_seconds
    next_heartbeat = started + heartbeat_seconds
    try:
        while pending:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                break
            completed, pending = await asyncio.wait(
                pending,
                timeout=min(remaining, max(0.001, next_heartbeat - now)),
                return_when=asyncio.FIRST_COMPLETED,
            )
            done.update(completed)
            now = time.monotonic()
            if pending and now >= next_heartbeat:
                _notify_progress(
                    on_progress,
                    {
                        "kind": "stage_heartbeat",
                        "stage_id": "behavior_claim_validation",
                        "status": "running",
                        "claim_count": len(pending_claims),
                        "pending_batch_count": len(pending),
                        "elapsed_ms": round((now - started) * 1000, 1),
                        "model": str(validator.get("model") or ""),
                        "user_message": (
                            "事实核验仍在进行，"
                            f"剩余 {len(pending)} 个审计批次"
                        ),
                    },
                )
                next_heartbeat = now + heartbeat_seconds
    except asyncio.CancelledError:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await _close_llm_client(llm_client)
        raise
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    await _close_llm_client(llm_client)
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
        **_request_coverage_fields(request_payload),
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
            "model": str(validator.get("model") or ""),
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
    index: int | str,
    request: dict[str, Any],
    runtime: dict[str, Any] | None,
    validator: dict[str, Any],
    streamer: Callable[..., AsyncIterator[str]],
    llm_client: Any | None,
    repo_path: str,
    diagnostics_dir: Path,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    batch_label = f"{index:02d}" if isinstance(index, int) else str(index)
    batch_dir = diagnostics_dir / f"batch_{batch_label}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    prompt = build_behavior_claim_audit_prompt(request)
    _write_json(batch_dir / "request.json", request)
    (batch_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    runtime_dir = (batch_dir / "runtime").resolve()
    async with semaphore:
        if llm_client is not None:
            response = await llm_client.complete_once(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=int(settings.behavior_claim_audit_max_tokens),
                temperature=0.0,
            )
            raw_output = str(response.content or "").strip()
            response_model = str(getattr(response, "model", "") or "").strip()
            if response_model:
                validator["model"] = response_model
        else:
            batch_runtime = {**(runtime or {}), "env": dict((runtime or {}).get("env") or {})}
            batch_runtime["env"]["CODETALK_AGENT_ARTIFACT_DIR"] = str(runtime_dir)
            raw_output = await _collect_agent_answer(
                streamer=streamer,
                runtime=batch_runtime,
                prompt=prompt,
                cwd=repo_path,
            )
            shutil.rmtree(runtime_dir, ignore_errors=True)
    (batch_dir / "raw_output.txt").write_text(raw_output, encoding="utf-8")
    return normalize_behavior_claim_verdicts(
        raw_output=raw_output,
        request=request,
        validator=validator,
    )


async def _validate_behavior_claim_batch_with_adaptive_split(
    *,
    index: int | str,
    request: dict[str, Any],
    runtime: dict[str, Any] | None,
    validator: dict[str, Any],
    streamer: Callable[..., AsyncIterator[str]],
    llm_client: Any | None,
    repo_path: str,
    diagnostics_dir: Path,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    """Retry only a malformed multi-claim response with smaller requests.

    Providers occasionally truncate a syntactically valid beginning of a large
    JSON verdict.  Treat that as transport/output capacity, preserve the first
    raw response, and split the affected batch.  A singleton still fails
    normally, so this never turns an unparseable answer into a false pass.
    """
    try:
        return await _validate_behavior_claim_batch(
            index=index,
            request=request,
            runtime=runtime,
            validator=validator,
            streamer=streamer,
            llm_client=llm_client,
            repo_path=repo_path,
            diagnostics_dir=diagnostics_dir,
            semaphore=semaphore,
        )
    except ValueError:
        claims = [item for item in request.get("claims") or [] if isinstance(item, dict)]
        if len(claims) <= 1:
            raise
        midpoint = len(claims) // 2
        child_results = await asyncio.gather(
            _validate_behavior_claim_batch_with_adaptive_split(
                index=f"{index}-a",
                request=_behavior_claim_request_subset(request, claims[:midpoint]),
                runtime=runtime,
                validator=validator,
                streamer=streamer,
                llm_client=llm_client,
                repo_path=repo_path,
                diagnostics_dir=diagnostics_dir,
                semaphore=semaphore,
            ),
            _validate_behavior_claim_batch_with_adaptive_split(
                index=f"{index}-b",
                request=_behavior_claim_request_subset(request, claims[midpoint:]),
                runtime=runtime,
                validator=validator,
                streamer=streamer,
                llm_client=llm_client,
                repo_path=repo_path,
                diagnostics_dir=diagnostics_dir,
                semaphore=semaphore,
            ),
        )
        return {
            "kind": "behavior_claim_validation",
            "schema_version": _BEHAVIOR_VALIDATION_SCHEMA_VERSION,
            "status": "completed",
            "request_sha256": str(request.get("request_sha256") or ""),
            "validator": dict(validator),
            "raw_verdict_count": sum(
                int(item.get("raw_verdict_count") or 0) for item in child_results
            ),
            "adaptive_split": True,
            "claims": [
                claim
                for item in child_results
                for claim in item.get("claims") or []
                if isinstance(claim, dict)
            ],
        }


async def _close_llm_client(client: Any | None) -> None:
    if client is None:
        return
    closer = getattr(client, "close", None) or getattr(client, "aclose", None)
    if closer is None:
        return
    result = closer()
    if inspect.isawaitable(result):
        await result


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
        **_request_coverage_fields(request),
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
