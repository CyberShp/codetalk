"""Read-only, truth-safe task-run projections of F012 evaluations."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import settings
from app.services.quality_evaluation_contract import EvaluationScope
from app.services.quality_evaluator import aggregate_quality_evaluation
from app.services.workbench_task_run import WorkbenchTaskRunStore


router = APIRouter(tags=["quality-evaluations"])

_PUBLIC_MISS_COPY = {
    "accuracy": {
        "label": "关键事实",
        "reason": "公开证据未能闭合该事实陈述",
        "recommended_action": "核对公开源码证据与事实陈述，并修正不一致内容",
    },
    "breadth": {
        "label": "关键覆盖项",
        "reason": "关键场景缺少闭环覆盖",
        "recommended_action": "补充该关键场景及其对应测试证据",
    },
    "depth": {
        "label": "关键因果链",
        "reason": "关键因果链缺少闭环验证",
        "recommended_action": "补充入口、状态转换、错误传播和验证结果的闭环证据",
    },
}

_BREADTH_LABELS = {
    "entrypoints": "入口覆盖项",
    "flows": "端到端流程覆盖项",
    "branches": "分支覆盖项",
    "states": "状态覆盖项",
    "resources": "资源生命周期覆盖项",
    "boundaries": "系统边界覆盖项",
    "concurrency": "并发覆盖项",
    "errors": "错误恢复覆盖项",
    "protocol": "协议覆盖项",
    "historical": "历史行为覆盖项",
    "mutation": "变异覆盖项",
}


def get_task_run_store() -> WorkbenchTaskRunStore:
    return WorkbenchTaskRunStore(settings.data_path / "workbench" / "task_runs")


@router.get("/api/workbench/task-runs/{run_ref}/quality-evaluation")
def get_quality_evaluation(
    run_ref: str,
    scope: EvaluationScope | None = Query(default=None),
    store: WorkbenchTaskRunStore = Depends(get_task_run_store),
) -> dict[str, object]:
    try:
        task_run = store.load(run_ref)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="quality evaluation was not found") from exc
    execution_status = str(
        getattr(task_run, "status", None)
        or getattr(task_run, "execution_status", "")
    ).strip().lower()
    if execution_status not in {
        "completed",
        "failed",
        "blocked",
        "quality_blocked",
        "cancelled",
        "timed_out",
    }:
        raise HTTPException(
            status_code=409,
            detail="quality evaluation is incomplete or invalid",
        )
    report_path = Path(str(task_run.artifact_dir)) / "quality_evaluation_report.json"
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        report = aggregate_quality_evaluation(payload)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="quality evaluation was not found") from exc
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="quality evaluation is incomplete or invalid") from exc
    if scope is not None and report.scope is not scope:
        raise HTTPException(status_code=422, detail="quality evaluation scope mismatch")

    return _public_report(report.model_dump(mode="json", by_alias=True))


def _public_report(
    value: object,
    *,
    axis: str | None = None,
    run_ref: str | None = None,
) -> object:
    if isinstance(value, Mapping):
        if run_ref is None and isinstance(value.get("run_ref"), str):
            run_ref = str(value["run_ref"])
        projected: dict[str, object] = {}
        for key, nested in value.items():
            public_key = str(key)
            child_axis = public_key if public_key in _PUBLIC_MISS_COPY else axis
            if public_key == "critical_misses" and isinstance(nested, list):
                copy = _PUBLIC_MISS_COPY.get(axis or "")
                public_misses = []
                for index, item in enumerate(nested, start=1):
                    if not isinstance(item, Mapping):
                        continue
                    public_id, public_label = _public_miss_identity(
                        axis=axis,
                        item=item,
                        run_ref=run_ref,
                        fallback_index=index,
                    )
                    public_miss = {
                        "item_id": public_id,
                        "reason": (
                            copy["reason"]
                            if copy
                            else "critical obligation is not satisfied"
                        ),
                        "validation_layer": str(
                            item.get("validation_layer") or "L2"
                        ),
                    }
                    if copy:
                        public_miss.update({
                            "public_label": public_label,
                            "recommended_action": copy["recommended_action"],
                        })
                    public_misses.append(public_miss)
                projected[public_key] = public_misses
            elif public_key not in {
                "miss_ids",
                "critical_miss_ids",
                "evidence_refs",
            }:
                projected[public_key] = _public_report(
                    nested,
                    axis=child_axis,
                    run_ref=run_ref,
                )
        return projected
    if isinstance(value, list):
        return [_public_report(item, axis=axis, run_ref=run_ref) for item in value]
    return value


def _public_miss_identity(
    *,
    axis: str | None,
    item: Mapping[str, object],
    run_ref: str | None,
    fallback_index: int,
) -> tuple[str, str]:
    raw_id = str(item.get("item_id") or f"missing-{fallback_index}")
    digest = hashlib.sha256(
        f"{run_ref or 'unbound'}\0{axis or 'unknown'}\0{raw_id}".encode("utf-8")
    ).hexdigest()[:10].upper()
    public_id = f"public-{axis or 'quality'}-{digest.lower()}"
    reference = f"REF-{digest}"
    reason = str(item.get("reason") or "").lower()

    if axis == "accuracy":
        if raw_id.startswith("claim:"):
            claim_id = raw_id.removeprefix("claim:")
            if re.fullmatch(r"[A-Za-z0-9_.:-]{1,48}", claim_id):
                return public_id, f"生成事实 {claim_id}"
            return public_id, f"生成事实 {reference}"
        if raw_id.startswith("gold:"):
            return public_id, f"遗漏的关键基准事实 {reference}"
        if raw_id.startswith("oracle:"):
            return public_id, f"执行验证 {reference}"
        return public_id, f"事实校验项 {reference}"

    if axis == "breadth":
        dimension = next(
            (name for name in _BREADTH_LABELS if f"critical {name} obligation" in reason),
            None,
        )
        return public_id, f"{_BREADTH_LABELS.get(dimension, '关键覆盖项')} {reference}"

    if axis == "depth":
        category = "关键因果链"
        if "/node:" in raw_id:
            category = "因果链节点"
        elif "/edge:" in raw_id:
            category = "因果链边"
        elif "/check:" in raw_id:
            category = "反证检查"
        elif "/l3:" in raw_id or "executable oracle" in reason:
            category = "执行 Oracle"
        return public_id, f"{category} {reference}"

    return public_id, f"关键义务 {reference}"
