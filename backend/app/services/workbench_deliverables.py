"""Stable user-facing deliverable envelopes for Workbench task runs."""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.artifact_profiles import validate_profile_artifacts
from app.services.skill_judge import evaluate_skill_judge
from app.services.workbench_artifact_manifest import build_task_artifact_manifest


_ENVELOPE_DIRECTORY = "deliverables"
_BUNDLE_NAME = "deliverables.zip"
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def build_deliverable_bundle(
    task_dir: str | Path,
    *,
    task_run_id: str,
    summary: str,
    validation: dict[str, Any],
    include_paths: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    root = Path(task_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    envelope_dir = root / _ENVELOPE_DIRECTORY
    if envelope_dir.exists():
        shutil.rmtree(envelope_dir)
    envelope_dir.mkdir()

    explicit_paths = (
        {str(item) for item in include_paths if str(item)}
        if include_paths is not None
        else None
    )
    artifacts = [
        _public_manifest_item(item)
        for item in build_task_artifact_manifest(root)
        if (
            (explicit_paths is None and item.get("audience") == "deliverable")
            or (
                explicit_paths is not None
                and str(item.get("relative_path") or "") in explicit_paths
            )
        )
        and not str(item.get("relative_path") or "").startswith(f"{_ENVELOPE_DIRECTORY}/")
        and item.get("relative_path") != _BUNDLE_NAME
    ]
    artifacts.sort(key=lambda item: item["relative_path"])
    manifest = {
        "schema_version": 1,
        "task_run_id": task_run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }
    summary_text = str(summary or "").strip() + "\n"
    (envelope_dir / "summary.md").write_text(summary_text, encoding="utf-8")
    _write_json(envelope_dir / "manifest.json", manifest)
    _write_json(envelope_dir / "artifact_validation.json", dict(validation or {}))

    bundle_path = root / _BUNDLE_NAME
    temporary_path = root / f".{_BUNDLE_NAME}.tmp"
    temporary_path.unlink(missing_ok=True)
    with zipfile.ZipFile(
        temporary_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for name in ("summary.md", "manifest.json", "artifact_validation.json"):
            _write_zip_entry(archive, name, (envelope_dir / name).read_bytes())
        for item in artifacts:
            relative_path = str(item["relative_path"])
            source = (root / relative_path).resolve()
            if not source.is_relative_to(root) or not source.is_file():
                continue
            _write_zip_entry(
                archive,
                f"artifacts/{relative_path}",
                source.read_bytes(),
            )
    temporary_path.replace(bundle_path)
    bundle_data = bundle_path.read_bytes()
    return {
        "task_run_id": task_run_id,
        "artifact_count": len(artifacts),
        "envelope_dir": str(envelope_dir),
        "bundle_path": str(bundle_path),
        "bundle_size_bytes": len(bundle_data),
        "bundle_sha256": hashlib.sha256(bundle_data).hexdigest(),
        "manifest": manifest,
    }


def build_task_run_deliverables(task_run: Any) -> dict[str, Any]:
    task_dir = Path(str(task_run.artifact_dir)).resolve()
    execution = _read_json(task_dir / "workflow_execution.json")
    output_contract = _read_json(task_dir / "output_contract.json")
    task_bundle = getattr(task_run, "task_bundle", {}) or {}
    invocation = (
        _read_json(task_dir / "skill_invocation.json")
        if (task_dir / "skill_invocation.json").exists()
        else {}
    )
    invocation_judge = invocation.get("judge") if isinstance(invocation.get("judge"), dict) else {}
    judge_required = bool(task_bundle.get("skill_judge_required") or invocation_judge.get("required"))
    judge = (
        evaluate_skill_judge(task_dir, required=judge_required)
        if invocation
        else {}
    )
    include_paths: list[str] | None = None
    if isinstance(output_contract, dict) and isinstance(output_contract.get("artifacts"), list):
        profile = {
            "id": output_contract.get("profile_id"),
            "version": output_contract.get("profile_version"),
            "name": output_contract.get("name") or "Resolved output profile",
            "artifacts": output_contract["artifacts"],
        }
        _materialize_profile_outputs(task_dir, profile, execution)
        validation = validate_profile_artifacts(task_dir, profile)
        include_paths = [
            str(item.get("filename") or "")
            for item in validation.get("artifacts") or []
            if isinstance(item, dict) and item.get("status") == "accepted"
        ]
    else:
        outputs = execution.get("outputs") if isinstance(execution, dict) else []
        output_items = [item for item in outputs or [] if isinstance(item, dict)]
        rejected = [item for item in output_items if item.get("status") not in {"ok", "accepted"}]
        validation = {
            "accepted": not rejected,
            "profile_id": "",
            "profile_version": 0,
            "artifacts": output_items,
        }
        include_paths = [
            str(item.get("path") or "")
            for item in output_items
            if item.get("status") in {"ok", "accepted"} and item.get("path")
        ] or None
    if judge_required and not judge.get("ready"):
        validation["accepted"] = False
        validation["judge_status"] = judge.get("status") or "PENDING_VALIDATION"
        validation["blocking_reasons"] = [
            *[str(item) for item in validation.get("blocking_reasons") or []],
            "skill_judge_required",
        ]
    elif judge:
        validation["judge_status"] = judge.get("status")
    workflow_snapshot = getattr(task_run, "workflow_snapshot", {}) or {}
    workflow_name = str(
        workflow_snapshot.get("name")
        or workflow_snapshot.get("title")
        or getattr(task_run, "workflow_id", "Workflow")
    )
    run_status = str(execution.get("status") or "prepared")
    summary = (
        f"# {workflow_name}\n\n"
        f"- Run: `{task_run.task_run_id}`\n"
        f"- Status: `{run_status}`\n"
        f"- Deliverables accepted: `{'yes' if validation.get('accepted') else 'no'}`\n"
    )
    result = build_deliverable_bundle(
        task_dir,
        task_run_id=str(task_run.task_run_id),
        summary=summary,
        validation=validation,
        include_paths=include_paths,
    )
    result["validation"] = validation
    return result


def _materialize_profile_outputs(
    task_dir: Path,
    profile: dict[str, Any],
    execution: dict[str, Any],
) -> None:
    outputs_by_id = {
        str(item.get("id") or ""): item
        for item in execution.get("outputs") or []
        if isinstance(item, dict) and item.get("id")
    }
    root = task_dir.resolve()
    for artifact in profile.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        output = outputs_by_id.get(str(artifact.get("id") or ""))
        if not output or output.get("status") not in {"ok", "accepted", "completed"}:
            continue
        source_path = str(output.get("path") or "")
        target_path = str(artifact.get("filename") or "")
        if not source_path or not target_path:
            continue
        source = (root / source_path).resolve()
        target = (root / target_path).resolve()
        if (
            not source.is_relative_to(root)
            or not target.is_relative_to(root)
            or not source.is_file()
        ):
            continue
        if source != target:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


def _public_manifest_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item[key]
        for key in ("relative_path", "kind", "size_bytes", "sha256")
        if key in item
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_zip_entry(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, _FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}
