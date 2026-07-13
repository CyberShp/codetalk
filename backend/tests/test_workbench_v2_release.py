import json
import sqlite3

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _legacy_database(path):
    with sqlite3.connect(path) as db:
        db.execute(
            """
            CREATE TABLE workflow_definitions (
                workflow_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                definition_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            "INSERT INTO workflow_definitions VALUES (?, ?, ?, ?, ?)",
            (
                "legacy-flow",
                "Legacy flow",
                '{"id":"legacy-flow","inputs":[],"steps":[],"outputs":[]}',
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )


def test_first_v2_migration_creates_one_verified_backup_before_writing(tmp_path):
    from app.services.workbench_sqlite_backup import ensure_workbench_migration_backup
    from app.services.workflow_version_store import WorkflowVersionStore

    db_path = tmp_path / "workflows.db"
    _legacy_database(db_path)

    first_backup = ensure_workbench_migration_backup(db_path)
    assert first_backup is not None
    assert first_backup.exists()
    with sqlite3.connect(first_backup) as backup_db:
        tables = {
            row[0] for row in backup_db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "workflow_definitions" in tables
        assert "workflow_headers" not in tables

    result = WorkflowVersionStore(db_path).initialize_and_migrate()
    second_backup = ensure_workbench_migration_backup(db_path)
    assert result["migrated_workflows"] == 1
    assert second_backup == first_backup
    with sqlite3.connect(first_backup) as backup_db:
        assert backup_db.execute(
            "SELECT name FROM workflow_definitions WHERE workflow_id = 'legacy-flow'"
        ).fetchone() == ("Legacy flow",)


def test_backup_failure_is_fatal_and_does_not_leave_partial_file(tmp_path):
    from app.services.workbench_sqlite_backup import ensure_workbench_migration_backup

    db_path = tmp_path / "workflows.db"
    db_path.write_bytes(b"not a sqlite database")

    with pytest.raises(sqlite3.DatabaseError):
        ensure_workbench_migration_backup(db_path)

    assert list(tmp_path.glob("workflows.pre-workbench-v2.*.bak")) == []


@pytest.mark.asyncio
async def test_release_status_exposes_only_the_v2_switch(monkeypatch):
    from app.api import workbench_v2_release
    from app.config import settings

    app = FastAPI()
    app.include_router(workbench_v2_release.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        monkeypatch.setattr(settings, "workbench_v2_enabled", True)
        enabled = await client.get("/api/workbench/release")
        assert enabled.status_code == 200
        assert enabled.json() == {"workbench_v2_enabled": True}

        monkeypatch.setattr(settings, "workbench_v2_enabled", False)
        disabled = await client.get("/api/workbench/release")
        assert disabled.status_code == 200
        assert disabled.json() == {"workbench_v2_enabled": False}


@pytest.mark.asyncio
async def test_legacy_run_and_artifact_remain_readable_after_v2_store_migration(
    tmp_path, monkeypatch
):
    from app.api import agent_workbench
    from app.config import settings
    from app.services.workflow_version_store import WorkflowVersionStore

    data_dir = tmp_path / "data"
    run_dir = data_dir / "workbench" / "task_runs" / "task_run_before_v2"
    run_dir.mkdir(parents=True)
    report = "# Historical report\n\nThe pre-V2 artifact is still available.\n"
    (run_dir / "report.md").write_text(report, encoding="utf-8")
    (run_dir / "task_run.json").write_text(
        json.dumps(
            {
                "task_run_id": "task_run_before_v2",
                "workflow_id": "legacy-flow",
                "workspace_id": "ws-legacy",
                "repo_path": "/legacy/repo",
                "artifact_dir": str(run_dir),
                "workflow_snapshot": {
                    "id": "legacy-flow",
                    "name": "Legacy flow",
                    "inputs": [],
                    "steps": [],
                    "outputs": [{"id": "report", "type": "markdown"}],
                },
                "input_snapshot": {},
                "task_bundle": {},
                "agent_runs": [],
                "created_at": "2026-01-01T00:00:00+00:00",
                "status": "completed",
                "runtime": {"status": "completed"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "data_dir", str(data_dir))

    WorkflowVersionStore(data_dir / "workbench" / "workflows.db").initialize_and_migrate()

    app = FastAPI()
    app.include_router(agent_workbench.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        run = await client.get("/api/workbench/task-runs/task_run_before_v2")
        artifacts = await client.get(
            "/api/workbench/task-runs/task_run_before_v2/artifacts"
        )
        content = await client.get(
            "/api/workbench/task-runs/task_run_before_v2/artifacts/content/report.md"
        )

    assert run.status_code == 200
    assert run.json()["task_run_id"] == "task_run_before_v2"
    assert artifacts.status_code == 200
    assert any(
        item["relative_path"] == "report.md" for item in artifacts.json()["artifacts"]
    )
    assert content.status_code == 200
    assert content.json()["content"] == report
