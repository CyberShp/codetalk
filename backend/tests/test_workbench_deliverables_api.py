import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


class _TaskRunStore:
    def __init__(self, task_run):
        self.task_run = task_run

    def load(self, task_run_id):
        if task_run_id != self.task_run.task_run_id:
            raise KeyError(task_run_id)
        return self.task_run


@pytest.fixture
async def deliverables_client(tmp_path):
    from app.api import workbench_deliverables as deliverables_api

    task_dir = tmp_path / "run"
    (task_dir / "steps").mkdir(parents=True)
    (task_dir / "steps" / "test_plan.json").write_text(
        '{"cases": []}', encoding="utf-8"
    )
    (task_dir / "workflow_execution.json").write_text(
        json.dumps({"status": "completed", "outputs": []}), encoding="utf-8"
    )
    task_run = SimpleNamespace(
        task_run_id="run-api",
        workflow_id="workflow-1",
        workflow_snapshot={"name": "Module risk"},
        artifact_dir=str(task_dir),
    )

    @asynccontextmanager
    async def lifespan(app):
        yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(deliverables_api.router)
    app.dependency_overrides[deliverables_api.get_task_run_store] = lambda: _TaskRunStore(
        task_run
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def test_deliverables_api_builds_metadata_and_downloads_user_bundle(deliverables_client):
    metadata = await deliverables_client.post(
        "/api/workbench/task-runs/run-api/deliverables"
    )

    assert metadata.status_code == 200
    assert metadata.json()["task_run_id"] == "run-api"
    assert metadata.json()["artifact_count"] == 1
    assert "bundle_path" not in metadata.json()

    package = await deliverables_client.get(
        "/api/workbench/task-runs/run-api/deliverable-package"
    )
    assert package.status_code == 200
    assert package.headers["content-type"] == "application/zip"
    assert "run-api-deliverables.zip" in package.headers["content-disposition"]
    assert package.content.startswith(b"PK")


async def test_deliverables_api_returns_not_found_for_unknown_run(deliverables_client):
    response = await deliverables_client.get(
        "/api/workbench/task-runs/missing/deliverable-package"
    )
    assert response.status_code == 404
