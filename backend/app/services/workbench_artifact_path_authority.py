"""Reconcile persisted Workbench artifact paths with their authoritative disk layout.

Older and alternate execution paths could persist human-readable workflow or node
labels in ``artifact_dir``. Those strings are metadata, not filesystem authority.
The directory containing ``task_run.json`` and the frozen step id are the only
stable sources used for execution.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

_INSTALLED = False
SAFE_RUNTIME_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def install_workbench_artifact_path_authority() -> None:
    """Patch WorkbenchTaskRunStore once so every execution receives canonical paths."""

    global _INSTALLED
    if _INSTALLED:
        return

    from app.services.workbench_task_run import WorkbenchTaskRunStore

    if getattr(WorkbenchTaskRunStore, "_artifact_path_authority_installed", False):
        _INSTALLED = True
        return

    original_load = WorkbenchTaskRunStore.load
    original_list = WorkbenchTaskRunStore.list

    def authoritative_load(self: Any, task_run_id: str):
        task_run = original_load(self, task_run_id)
        task_root = Path(self.artifact_root) / _safe_segment(task_run_id)
        return _reconcile_task_run(
            task_run,
            task_root=task_root,
            store_root=Path(self.artifact_root),
            persist=True,
        )

    def authoritative_list(self: Any, **kwargs: Any):
        runs = original_list(self, **kwargs)
        reconciled = []
        for task_run in runs:
            try:
                task_root = Path(self.artifact_root) / _safe_segment(task_run.task_run_id)
                reconciled.append(
                    _reconcile_task_run(
                        task_run,
                        task_root=task_root,
                        store_root=Path(self.artifact_root),
                        persist=True,
                    )
                )
            except (KeyError, OSError, ValueError):
                reconciled.append(task_run)
        return reconciled

    WorkbenchTaskRunStore.load = authoritative_load
    WorkbenchTaskRunStore.list = authoritative_list
    WorkbenchTaskRunStore._artifact_path_authority_installed = True
    _INSTALLED = True


def _reconcile_task_run(
    task_run: Any,
    *,
    task_root: Path,
    store_root: Path,
    persist: bool,
):
    task_root = task_root.expanduser().resolve()
    store_root = store_root.expanduser().resolve()
    task_root.relative_to(store_root)
    if not (task_root / "task_run.json").is_file():
        return task_run

    canonical_task_run_id = task_root.name
    step_ids, labels_to_ids = _workflow_step_identity(task_run.workflow_snapshot)
    corrected_runs: list[dict[str, Any]] = []
    changes: list[dict[str, str]] = []

    for index, raw in enumerate(task_run.agent_runs or []):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        raw_step_id = str(item.get("step_id") or "").strip()
        canonical_step_id = _canonical_step_id(
            raw_step_id,
            step_ids=step_ids,
            labels_to_ids=labels_to_ids,
            index=index,
        )
        expected = task_root / "agent_runs" / canonical_step_id
        current_text = str(item.get("artifact_dir") or "").strip()
        current = Path(current_text).expanduser() if current_text else expected

        if not _same_path(current, expected):
            _migrate_agent_directory(
                current=current,
                expected=expected,
                store_root=store_root,
            )
            changes.append(
                {
                    "step_id_before": raw_step_id,
                    "step_id_after": canonical_step_id,
                    "artifact_dir_before": current_text,
                    "artifact_dir_after": str(expected),
                }
            )
        else:
            expected.mkdir(parents=True, exist_ok=True)

        item["step_id"] = canonical_step_id
        item["artifact_dir"] = str(expected)
        _rewrite_agent_envelope(expected, item, task_run_id=canonical_task_run_id)
        corrected_runs.append(item)

    task_id_changed = str(task_run.task_run_id) != canonical_task_run_id
    task_path_changed = not _same_path(Path(str(task_run.artifact_dir)), task_root)
    if task_id_changed or task_path_changed:
        changes.insert(
            0,
            {
                "task_run_id_before": str(task_run.task_run_id),
                "task_run_id_after": canonical_task_run_id,
                "artifact_dir_before": str(task_run.artifact_dir),
                "artifact_dir_after": str(task_root),
            },
        )

    if persist and (changes or corrected_runs != list(task_run.agent_runs or [])):
        _persist_reconciliation(
            task_root=task_root,
            task_run_id=canonical_task_run_id,
            corrected_runs=corrected_runs,
            changes=changes,
        )

    corrected_bundle = dict(task_run.task_bundle or {})
    if corrected_bundle:
        corrected_bundle["task_run_id"] = canonical_task_run_id

    return replace(
        task_run,
        task_run_id=canonical_task_run_id,
        artifact_dir=str(task_root),
        task_bundle=corrected_bundle,
        agent_runs=corrected_runs,
    )


def _workflow_step_identity(
    workflow_snapshot: Any,
) -> tuple[set[str], dict[str, str]]:
    step_ids: set[str] = set()
    labels_to_ids: dict[str, str] = {}
    if not isinstance(workflow_snapshot, dict):
        return step_ids, labels_to_ids
    for step in workflow_snapshot.get("steps") or []:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id") or "").strip()
        if not step_id or not _is_safe_segment(step_id):
            continue
        step_ids.add(step_id)
        for key in ("label", "name", "title"):
            label = str(step.get(key) or "").strip()
            if label:
                labels_to_ids.setdefault(label, step_id)
    return step_ids, labels_to_ids


def _canonical_step_id(
    raw_step_id: str,
    *,
    step_ids: set[str],
    labels_to_ids: dict[str, str],
    index: int,
) -> str:
    if raw_step_id in step_ids and _is_safe_segment(raw_step_id):
        return raw_step_id
    mapped = labels_to_ids.get(raw_step_id)
    if mapped:
        return mapped
    if _is_safe_segment(raw_step_id) and raw_step_id:
        return raw_step_id
    digest = hashlib.sha256(
        f"{index}:{raw_step_id}".encode("utf-8", errors="replace")
    ).hexdigest()[:16]
    return f"node_{digest}"


def _migrate_agent_directory(
    *,
    current: Path,
    expected: Path,
    store_root: Path,
) -> None:
    try:
        current_resolved = current.expanduser().resolve()
        expected_resolved = expected.expanduser().resolve()
    except OSError:
        expected.mkdir(parents=True, exist_ok=True)
        return

    try:
        current_resolved.relative_to(store_root)
        expected_resolved.relative_to(store_root)
    except ValueError:
        expected.mkdir(parents=True, exist_ok=True)
        return

    if _same_path(current_resolved, expected_resolved):
        expected.mkdir(parents=True, exist_ok=True)
        return

    # Never copy a directory into one of its own descendants. This can happen
    # when an old record stored the Task root itself as the Agent artifact root.
    try:
        expected_resolved.relative_to(current_resolved)
    except ValueError:
        pass
    else:
        expected.mkdir(parents=True, exist_ok=True)
        return

    expected.mkdir(parents=True, exist_ok=True)
    if not current_resolved.is_dir():
        return
    shutil.copytree(current_resolved, expected_resolved, dirs_exist_ok=True)


def _rewrite_agent_envelope(
    agent_root: Path,
    agent_run: dict[str, Any],
    *,
    task_run_id: str,
) -> None:
    path = agent_root / "agent_run.json"
    payload = _read_json(path)
    if isinstance(payload, dict):
        payload["artifact_dir"] = str(agent_root)
        _atomic_write_json(path, payload)

    bundle_path = agent_root / "task_bundle.json"
    bundle = _read_json(bundle_path)
    if isinstance(bundle, dict):
        bundle["task_run_id"] = task_run_id
        bundle["step_id"] = str(agent_run.get("step_id") or bundle.get("step_id") or "")
        _atomic_write_json(bundle_path, bundle)


def _persist_reconciliation(
    *,
    task_root: Path,
    task_run_id: str,
    corrected_runs: list[dict[str, Any]],
    changes: list[dict[str, str]],
) -> None:
    task_path = task_root / "task_run.json"
    payload = _read_json(task_path)
    if isinstance(payload, dict):
        payload["task_run_id"] = task_run_id
        payload["artifact_dir"] = str(task_root)
        payload["agent_runs"] = corrected_runs
        task_bundle = payload.get("task_bundle")
        if isinstance(task_bundle, dict):
            task_bundle["task_run_id"] = task_run_id
        _atomic_write_json(task_path, payload)

    descriptors_path = task_root / "agent_execution_descriptors.json"
    descriptors = _read_json(descriptors_path)
    if isinstance(descriptors, dict):
        descriptors["agent_runs"] = corrected_runs
        _atomic_write_json(descriptors_path, descriptors)

    _atomic_write_json(
        task_root / "artifact_path_reconciliation.json",
        {
            "schema_version": 1,
            "authority": "task_run_json_parent_and_frozen_step_id",
            "changes": changes,
        },
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=".ct-",
            suffix=".tmp",
            dir=str(path.parent),
            delete=False,
        ) as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            temporary = Path(stream.name)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _safe_segment(value: str) -> str:
    text = str(value or "").strip()
    if not _is_safe_segment(text):
        raise KeyError(value)
    return text


def _is_safe_segment(value: str) -> bool:
    return bool(value) and value not in {".", ".."} and ".." not in value and bool(SAFE_RUNTIME_ID_RE.fullmatch(value))


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.expanduser().resolve() == right.expanduser().resolve()
    except OSError:
        return str(left) == str(right)
