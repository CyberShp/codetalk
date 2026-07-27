from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import settings


pytestmark = [pytest.mark.asyncio]


@asynccontextmanager
async def _no_lifespan(app: FastAPI):
    yield


@pytest.fixture
async def upload_client(tmp_path, monkeypatch):
    from app.api import agent_workbench

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data_dir))
    monkeypatch.setattr(settings, "sqlite_db", str(data_dir / "codetalk.sqlite3"))

    app = FastAPI(lifespan=_no_lifespan)
    app.include_router(agent_workbench.router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def _create_v3_draft():
    from app.services.workflow_authoring_factory import build_canvas_graph
    from app.services.workflow_version_store import WorkflowVersionStore

    workflow_id = "upload_lifecycle"
    graph = build_canvas_graph(
        workflow_id=workflow_id,
        name="Upload lifecycle",
        description="",
        template="free_source_analysis",
    )
    store = WorkflowVersionStore(settings.data_path / "workbench" / "workflows.db")
    _, draft = store.create_canvas_workflow(
        workflow_id=workflow_id,
        name="Upload lifecycle",
        description="",
        authoring_graph=graph,
    )
    return store, draft


async def _upload(client: AsyncClient, draft) -> dict:
    response = await client.post(
        "/api/workbench/input-files/upload",
        files={"file": ("design.md", b"# Design\n", "text/markdown")},
        data={
            "input_id": "design_doc",
            "workflow_id": draft.workflow_id,
            "workflow_version_id": draft.version_id,
            "expected_revision": str(draft.draft_revision),
        },
    )
    assert response.status_code == 201
    return response.json()


def _bump_revision(store, draft):
    graph = deepcopy(draft.authoring_graph)
    graph["name"] = "Concurrent edit"
    return store.update_draft(
        draft.version_id,
        authoring_graph=graph,
        expected_revision=draft.draft_revision,
    )


def _expire_upload(uploaded: dict) -> Path:
    upload_path = settings.data_path / "workbench" / uploaded["path"]
    metadata_path = upload_path.parent / "upload_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=5)
    ).isoformat()
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return upload_path


def _reset_reconcile_queue(agent_workbench, upload_ids: list[str]) -> None:
    root_key = str(agent_workbench._input_uploads_dir().resolve())
    agent_workbench._INPUT_UPLOAD_RECONCILE_CYCLES.pop(root_key, None)
    agent_workbench._input_upload_reconcile_queue_path().unlink(missing_ok=True)
    agent_workbench._input_upload_reconcile_cursor_path().unlink(missing_ok=True)
    for upload_id in upload_ids:
        agent_workbench._register_input_upload_for_reconciliation(upload_id)


async def test_stale_v3_trial_upload_can_be_released_without_orphan(upload_client):
    store, draft = _create_v3_draft()
    uploaded = await _upload(upload_client, draft)
    upload_path = settings.data_path / "workbench" / uploaded["path"]
    assert upload_path.exists()
    assert uploaded["cleanup_token"]
    metadata_text = (upload_path.parent / "upload_metadata.json").read_text(
        encoding="utf-8"
    )
    assert uploaded["cleanup_token"] not in metadata_text
    assert "cleanup_token_sha256" in metadata_text

    _bump_revision(store, draft)
    released = await upload_client.post(
        f"/api/workbench/input-files/{uploaded['upload_id']}/release",
        json={"cleanup_token": uploaded["cleanup_token"]},
    )

    assert released.status_code == 200
    assert released.json() == {
        "status": "released",
        "upload_id": uploaded["upload_id"],
    }
    assert not upload_path.parent.exists()


async def test_second_upload_reports_structured_stale_draft(upload_client):
    store, draft = _create_v3_draft()
    first = await _upload(upload_client, draft)
    first_path = settings.data_path / "workbench" / first["path"]
    _bump_revision(store, draft)

    second = await upload_client.post(
        "/api/workbench/input-files/upload",
        files={"file": ("coverage.json", b"{}", "application/json")},
        data={
            "input_id": "coverage",
            "workflow_id": draft.workflow_id,
            "workflow_version_id": draft.version_id,
            "expected_revision": str(draft.draft_revision),
        },
    )

    assert second.status_code == 409
    assert second.json()["detail"] == {
        "code": "stale_draft",
        "message": "画布已被其他窗口更新。请刷新后重新选择文件。",
        "draft_revision": draft.draft_revision + 1,
    }
    assert first_path.exists()


async def test_upload_release_rejects_wrong_token_and_non_opaque_id(upload_client):
    store, draft = _create_v3_draft()
    uploaded = await _upload(upload_client, draft)
    upload_path = settings.data_path / "workbench" / uploaded["path"]
    _bump_revision(store, draft)

    wrong_token = await upload_client.post(
        f"/api/workbench/input-files/{uploaded['upload_id']}/release",
        json={"cleanup_token": "not-the-issued-token"},
    )
    invalid_id = await upload_client.post(
        "/api/workbench/input-files/not-an-opaque-upload/release",
        json={"cleanup_token": uploaded["cleanup_token"]},
    )

    assert wrong_token.status_code == 403
    assert invalid_id.status_code == 404
    assert upload_path.exists()


async def test_consumed_upload_is_retained_for_successful_task_snapshot(upload_client):
    store, draft = _create_v3_draft()
    uploaded = await _upload(upload_client, draft)
    upload_path = settings.data_path / "workbench" / uploaded["path"]
    task_dir = settings.data_path / "workbench" / "task_runs" / "task_consumed"
    task_dir.mkdir(parents=True)
    (task_dir / "input_snapshot.json").write_text(
        json.dumps(
            {
                "design_doc": {
                    "kind": "file",
                    "original_path": str(upload_path),
                    "copied_path": str(task_dir / "inputs" / "design.md"),
                }
            }
        ),
        encoding="utf-8",
    )
    _bump_revision(store, draft)

    released = await upload_client.post(
        f"/api/workbench/input-files/{uploaded['upload_id']}/release",
        json={"cleanup_token": uploaded["cleanup_token"]},
    )

    assert released.status_code == 409
    assert released.json()["detail"] == "上传文件已被任务使用，不能删除"
    assert upload_path.exists()


async def test_release_fails_closed_when_snapshot_read_is_uncertain(
    upload_client,
    monkeypatch,
):
    from app.api import agent_workbench

    store, draft = _create_v3_draft()
    uploaded = await _upload(upload_client, draft)
    upload_path = settings.data_path / "workbench" / uploaded["path"]
    task_dir = settings.data_path / "workbench" / "task_runs" / "task_read_error"
    task_dir.mkdir(parents=True)
    snapshot_path = task_dir / "input_snapshot.json"
    snapshot_path.write_text(
        json.dumps({"design_doc": {"original_path": str(upload_path)}}),
        encoding="utf-8",
    )
    _bump_revision(store, draft)
    original_read_text = Path.read_text

    def fail_snapshot_read(path, *args, **kwargs):
        if path == snapshot_path:
            raise OSError("transient snapshot read failure")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_snapshot_read)

    released = await upload_client.post(
        f"/api/workbench/input-files/{uploaded['upload_id']}/release",
        json={"cleanup_token": uploaded["cleanup_token"]},
    )

    assert released.status_code == 409
    assert (
        released.json()["detail"]
        == "无法确认上传文件是否已被任务使用，未删除文件"
    )
    assert upload_path.exists()
    assert agent_workbench._upload_reference_status(
        uploaded["upload_id"], uploaded["path"]
    ).value == "unknown"


async def test_ttl_reconciliation_fails_closed_for_malformed_snapshot(
    upload_client,
    monkeypatch,
):
    from app.api import agent_workbench

    monkeypatch.setenv("CODETALK_INPUT_UPLOAD_RECONCILE_LIMIT", "1")
    monkeypatch.setenv("CODETALK_INPUT_UPLOAD_SNAPSHOT_SCAN_LIMIT", "4")
    _, draft = _create_v3_draft()
    uploaded = await _upload(upload_client, draft)
    upload_path = _expire_upload(uploaded)
    task_dir = settings.data_path / "workbench" / "task_runs" / "task_bad_snapshot"
    task_dir.mkdir(parents=True)
    (task_dir / "input_snapshot.json").write_text("{not-json", encoding="utf-8")
    _reset_reconcile_queue(agent_workbench, [uploaded["upload_id"]])

    assert agent_workbench._reconcile_expired_input_uploads() == 0
    assert upload_path.exists()


async def test_ttl_reconciliation_fails_closed_across_rounds_for_non_utf8_snapshot(
    upload_client,
    monkeypatch,
):
    from app.api import agent_workbench

    monkeypatch.setenv("CODETALK_INPUT_UPLOAD_RECONCILE_LIMIT", "1")
    monkeypatch.setenv("CODETALK_INPUT_UPLOAD_SNAPSHOT_SCAN_LIMIT", "1")
    _, draft = _create_v3_draft()
    uploaded = await _upload(upload_client, draft)
    upload_path = _expire_upload(uploaded)
    task_dir = settings.data_path / "workbench" / "task_runs" / "task_non_utf8"
    task_dir.mkdir(parents=True)
    snapshot_path = task_dir / "input_snapshot.json"
    snapshot_path.write_bytes(
        json.dumps({"design_doc": {"original_path": str(upload_path)}}).encode()
        + b"\xff"
    )
    _reset_reconcile_queue(agent_workbench, [uploaded["upload_id"]])

    for _ in range(2):
        assert agent_workbench._reconcile_expired_input_uploads() == 0
        assert upload_path.exists()


async def test_snapshot_processing_exception_fails_closed_for_release_and_ttl(
    upload_client,
    monkeypatch,
):
    from app.api import agent_workbench

    monkeypatch.setenv("CODETALK_INPUT_UPLOAD_RECONCILE_LIMIT", "1")
    monkeypatch.setenv("CODETALK_INPUT_UPLOAD_SNAPSHOT_SCAN_LIMIT", "1")
    store, draft = _create_v3_draft()
    uploaded = await _upload(upload_client, draft)
    upload_path = _expire_upload(uploaded)
    task_dir = settings.data_path / "workbench" / "task_runs" / "task_deep_json"
    task_dir.mkdir(parents=True)
    snapshot_path = task_dir / "input_snapshot.json"
    snapshot_path.write_text(
        "[" * 2000
        + json.dumps({"design_doc": {"original_path": str(upload_path)}})
        + "]" * 2000,
        encoding="utf-8",
    )
    _bump_revision(store, draft)

    released = await upload_client.post(
        f"/api/workbench/input-files/{uploaded['upload_id']}/release",
        json={"cleanup_token": uploaded["cleanup_token"]},
    )
    assert released.status_code == 409
    assert released.json()["detail"] == (
        "无法确认上传文件是否已被任务使用，未删除文件"
    )
    assert upload_path.exists()

    _reset_reconcile_queue(agent_workbench, [uploaded["upload_id"]])
    for _ in range(2):
        assert agent_workbench._reconcile_expired_input_uploads() == 0
        assert upload_path.exists()


async def test_ttl_reconciliation_retains_referenced_upload_on_snapshot_oserror(
    upload_client,
    monkeypatch,
):
    from app.api import agent_workbench

    monkeypatch.setenv("CODETALK_INPUT_UPLOAD_RECONCILE_LIMIT", "1")
    monkeypatch.setenv("CODETALK_INPUT_UPLOAD_SNAPSHOT_SCAN_LIMIT", "4")
    _, draft = _create_v3_draft()
    uploaded = await _upload(upload_client, draft)
    upload_path = _expire_upload(uploaded)
    task_dir = settings.data_path / "workbench" / "task_runs" / "task_transient_io"
    task_dir.mkdir(parents=True)
    snapshot_path = task_dir / "input_snapshot.json"
    snapshot_path.write_text(
        json.dumps({"design_doc": {"original_path": str(upload_path)}}),
        encoding="utf-8",
    )
    _reset_reconcile_queue(agent_workbench, [uploaded["upload_id"]])
    original_read_text = Path.read_text

    def fail_snapshot_read(path, *args, **kwargs):
        if path == snapshot_path:
            raise OSError("transient snapshot read failure")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_snapshot_read)

    assert agent_workbench._reconcile_expired_input_uploads() == 0
    assert upload_path.exists()


async def test_expired_unreferenced_upload_is_reclaimed_but_task_reference_is_retained(
    upload_client,
    monkeypatch,
):
    monkeypatch.setenv("CODETALK_INPUT_UPLOAD_LEASE_TTL_SECONDS", "60")
    store, draft = _create_v3_draft()
    expired_orphan = await _upload(upload_client, draft)
    expired_referenced = await _upload(upload_client, draft)
    orphan_path = settings.data_path / "workbench" / expired_orphan["path"]
    referenced_path = settings.data_path / "workbench" / expired_referenced["path"]

    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    for upload_path in (orphan_path, referenced_path):
        metadata_path = upload_path.parent / "upload_metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["expires_at"] = expired_at
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    task_dir = settings.data_path / "workbench" / "task_runs" / "task_referenced_ttl"
    task_dir.mkdir(parents=True)
    (task_dir / "input_snapshot.json").write_text(
        json.dumps(
            {
                "design_doc": {
                    "kind": "file",
                    "original_path": str(referenced_path),
                }
            }
        ),
        encoding="utf-8",
    )

    trigger = await _upload(upload_client, draft)

    assert trigger["upload_id"]
    assert not orphan_path.parent.exists()
    assert referenced_path.exists()


async def test_reconciliation_does_not_starve_expired_upload_behind_live_lease(
    upload_client,
    monkeypatch,
):
    monkeypatch.setenv("CODETALK_INPUT_UPLOAD_RECONCILE_LIMIT", "1")
    store, draft = _create_v3_draft()
    live = await _upload(upload_client, draft)
    expired = await _upload(upload_client, draft)
    live_path = settings.data_path / "workbench" / live["path"]
    expired_path = settings.data_path / "workbench" / expired["path"]
    metadata_path = expired_path.parent / "upload_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=5)
    ).isoformat()
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    uploads_root = live_path.parent.parent.resolve()
    original_iterdir = Path.iterdir

    def ordered_iterdir(path):
        if path.resolve() == uploads_root:
            return iter((live_path.parent, expired_path.parent))
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", ordered_iterdir)

    await _upload(upload_client, draft)

    assert live_path.exists()
    assert not expired_path.parent.exists()


async def test_reconciliation_candidate_work_is_bounded_and_advances_without_starvation(
    upload_client,
    monkeypatch,
):
    from app.api import agent_workbench

    monkeypatch.setenv("CODETALK_INPUT_UPLOAD_RECONCILE_LIMIT", "1")
    _, draft = _create_v3_draft()
    live_uploads = [await _upload(upload_client, draft) for _ in range(6)]
    malformed_uploads = [await _upload(upload_client, draft) for _ in range(6)]
    expired = await _upload(upload_client, draft)
    live_paths = [
        settings.data_path / "workbench" / uploaded["path"]
        for uploaded in live_uploads
    ]
    malformed_paths = [
        settings.data_path / "workbench" / uploaded["path"]
        for uploaded in malformed_uploads
    ]
    expired_path = _expire_upload(expired)
    for malformed_path in malformed_paths:
        (malformed_path.parent / "upload_metadata.json").write_text(
            "{not-json",
            encoding="utf-8",
        )
    _reset_reconcile_queue(
        agent_workbench,
        [
            *(uploaded["upload_id"] for uploaded in live_uploads),
            *(uploaded["upload_id"] for uploaded in malformed_uploads),
            expired["upload_id"],
        ],
    )
    metadata_reads: list[Path] = []
    original_read_text = Path.read_text

    def count_metadata_reads(path, *args, **kwargs):
        if path.name == "upload_metadata.json":
            metadata_reads.append(path)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", count_metadata_reads)

    for round_index in range(13):
        metadata_reads.clear()
        agent_workbench._reconcile_expired_input_uploads()
        assert len(metadata_reads) <= 1
        assert expired_path.exists() is (round_index < 12)

    assert all(path.exists() for path in live_paths)
    assert all(path.exists() for path in malformed_paths)


async def test_reconciliation_snapshot_scan_is_bounded_and_late_reference_is_retained(
    upload_client,
    monkeypatch,
):
    from app.api import agent_workbench

    monkeypatch.setenv("CODETALK_INPUT_UPLOAD_RECONCILE_LIMIT", "1")
    monkeypatch.setenv("CODETALK_INPUT_UPLOAD_SNAPSHOT_SCAN_LIMIT", "2")
    _, draft = _create_v3_draft()
    uploaded = await _upload(upload_client, draft)
    upload_path = _expire_upload(uploaded)
    task_root = settings.data_path / "workbench" / "task_runs"
    task_root.mkdir(parents=True, exist_ok=True)
    snapshots: list[Path] = []
    for index in range(5):
        task_dir = task_root / f"task_{index}_{uuid4().hex}"
        task_dir.mkdir()
        snapshot_path = task_dir / "input_snapshot.json"
        payload = {"plain": f"task-{index}"}
        if index == 4:
            payload = {"design_doc": {"original_path": str(upload_path)}}
        snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
        snapshots.append(snapshot_path)
    _reset_reconcile_queue(agent_workbench, [uploaded["upload_id"]])
    snapshot_reads: list[Path] = []
    all_snapshot_reads: list[Path] = []
    original_read_text = Path.read_text

    def count_snapshot_reads(path, *args, **kwargs):
        if path.name == "input_snapshot.json":
            snapshot_reads.append(path)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", count_snapshot_reads)

    for _ in range(3):
        snapshot_reads.clear()
        assert agent_workbench._reconcile_expired_input_uploads() == 0
        assert len(snapshot_reads) <= 2
        all_snapshot_reads.extend(snapshot_reads)
        assert upload_path.exists()

    assert snapshots[-1] in all_snapshot_reads
