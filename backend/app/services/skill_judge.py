"""Skill Judge status and isolated input construction."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def evaluate_skill_judge(
    task_dir: str | Path,
    *,
    required: bool,
    judge_report_name: str = "skill_judge_report.json",
) -> dict[str, Any]:
    root = Path(task_dir)
    invocation = _read_json(root / "skill_invocation.json")
    report = _read_json(root / judge_report_name)
    judge_scope = invocation.get("judge") if isinstance(invocation.get("judge"), dict) else {}
    judge_input = {
        "skill_version_id": invocation.get("skill_version_id", ""),
        "skill_content_digest": invocation.get("skill_content_digest", ""),
        "input_snapshot": invocation.get("input_snapshot") or {},
        "selected_delivery_ids": (
            invocation.get("selected_delivery_ids")
            or invocation.get("selected_deliveries")
            or []
        ),
        "artifact_root": invocation.get("artifact_root") or str(root),
        "required_artifact_ids": [str(item) for item in invocation.get("required_artifact_ids") or []],
        "judge": {
            "required": bool(judge_scope.get("required")),
            "isolated_session": bool(judge_scope.get("isolated_session", True)),
            "artifact_ids": [str(item) for item in judge_scope.get("artifact_ids") or []],
        },
    }
    if not report:
        status = "PENDING_VALIDATION" if required else "WARNING"
        result = {
            "status": status,
            "ready": False,
            "required": bool(required),
            "warnings": ["judge_report_missing"],
            "judge_input": judge_input,
        }
    else:
        ready = bool(report.get("ready")) and str(report.get("status") or "") in {
            "READY",
            "READY_WITH_WARNINGS",
        }
        result = {
            "status": "READY" if ready else "PENDING_VALIDATION",
            "ready": ready,
            "required": bool(required),
            "warnings": [str(item) for item in report.get("warnings") or []],
            "judge_input": judge_input,
        }
    _write_json(root / "skill_judge_status.json", result)
    return result


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
