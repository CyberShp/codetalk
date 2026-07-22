"""Deterministic input-consumption ledger for frozen workflow inputs."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


def build_input_consumption_ledger(*, input_snapshot: dict[str, Any], stage_specs: list[dict[str, Any]]) -> dict[str, Any]:
    stages = [str(item.get("stage_id") or "") for item in stage_specs if str(item.get("stage_id") or "")]
    return {
        "schema_version": "input-consumption-v1",
        "inputs": [
            {
                "input_id": str(input_id),
                "sha256": _input_hash(value),
                "summary": _summary(value),
                "consumed_by_stages": stages,
                "consumption_mode": "frozen_task_bundle",
            }
            for input_id, value in input_snapshot.items()
        ],
    }


def _input_hash(value: Any) -> str:
    if isinstance(value, dict) and str(value.get("sha256") or ""):
        return str(value["sha256"])
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _summary(value: Any) -> str:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()[:240]
    if isinstance(value, dict):
        return str(value.get("original_name") or value.get("original_path") or value.get("kind") or "结构化输入")[:240]
    return str(value)[:240]
