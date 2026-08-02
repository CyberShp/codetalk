"""User-facing Workbench deliverable bundle endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.config import settings
from app.services.workbench_deliverables import build_task_run_deliverables
from app.services.workbench_task_run import WorkbenchTaskRunStore


router = APIRouter(prefix="/api/workbench/task-runs", tags=["Workbench deliverables"])


def get_task_run_store() -> WorkbenchTaskRunStore:
    return WorkbenchTaskRunStore(settings.data_path / "workbench" / "task_runs")


@router.post("/{task_run_id}/deliverables")
def build_deliverables(
    task_run_id: str,
    store: WorkbenchTaskRunStore = Depends(get_task_run_store),
) -> dict[str, Any]:
    task_run = _load_task_run(store, task_run_id)
    result = build_task_run_deliverables(task_run)
    return _public_result(result)


@router.get("/{task_run_id}/deliverable-package")
def download_deliverables(
    task_run_id: str,
    store: WorkbenchTaskRunStore = Depends(get_task_run_store),
) -> FileResponse:
    task_run = _load_task_run(store, task_run_id)
    result = build_task_run_deliverables(task_run)
    return FileResponse(
        result["bundle_path"],
        media_type="application/zip",
        filename=f"{task_run_id}-deliverables.zip",
    )


def _load_task_run(store: WorkbenchTaskRunStore, task_run_id: str) -> Any:
    try:
        return store.load(task_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown task run: {task_run_id}") from exc


def _public_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: result[key]
        for key in (
            "task_run_id",
            "artifact_count",
            "bundle_size_bytes",
            "bundle_sha256",
            "manifest",
            "validation",
        )
        if key in result
    }
