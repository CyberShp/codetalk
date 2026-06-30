"""Tests for the /api/tasks endpoints (CRUD + output/debug/steps/chat)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _task_payload(tmp_path, **overrides):
    base = {
        "name": "my-task",
        "repo_path": str(tmp_path),
        "analysis_focus": "architecture overview",
        "prompt_content": "Analyze the codebase structure",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def test_list_tasks_empty(client):
    response = await client.get("/api/tasks")
    assert response.status_code == 200
    assert response.json() == []


async def test_create_task(client, tmp_path):
    response = await client.post("/api/tasks", json=_task_payload(tmp_path))
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "my-task"
    assert data["repo_path"] == str(tmp_path)
    assert data["status"] == "pending"
    assert data["progress"] == 0
    assert data["error_message"] is None
    assert "id" in data


async def test_create_task_default_tools(client, tmp_path):
    response = await client.post("/api/tasks", json=_task_payload(tmp_path, name="t"))
    assert response.status_code == 201
    assert response.json()["tools"] == ["gitnexus"]


async def test_create_task_custom_tools(client, tmp_path):
    response = await client.post(
        "/api/tasks",
        json=_task_payload(tmp_path, tools=["gitnexus", "cgc"]),
    )
    assert response.status_code == 201
    assert response.json()["tools"] == ["gitnexus", "cgc"]


async def test_create_task_rejects_removed_deepwiki_tool(client, tmp_path):
    response = await client.post(
        "/api/tasks",
        json=_task_payload(tmp_path, tools=["deepwiki"]),
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["removed_tools"] == ["deepwiki"]
    assert "DeepWiki" in detail["hint"]


async def test_create_task_rejects_unknown_tool(client, tmp_path):
    response = await client.post(
        "/api/tasks",
        json=_task_payload(tmp_path, tools=["gitnexus", "unknown-tool"]),
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["unsupported_tools"] == ["unknown-tool"]
    assert "gitnexus" in detail["supported_tools"]


async def test_create_task_nonexistent_path(client):
    response = await client.post(
        "/api/tasks",
        json={
            "name": "bad",
            "repo_path": "/nonexistent/__xyz__",
            "analysis_focus": "x",
            "prompt_content": "y",
        },
    )
    assert response.status_code == 422


async def test_create_task_missing_required_fields(client, tmp_path):
    response = await client.post(
        "/api/tasks",
        json={"name": "bad", "repo_path": str(tmp_path)},
    )
    assert response.status_code == 422


async def test_create_task_rejects_removed_deepwiki_depth_field(client, tmp_path):
    response = await client.post(
        "/api/tasks",
        json=_task_payload(tmp_path, deepwiki_depth="balanced"),
    )

    assert response.status_code == 422
    errors = response.json()["detail"]
    assert any(error["loc"] == ["body", "deepwiki_depth"] for error in errors)


async def test_list_tasks_after_create(client, tmp_path):
    await client.post("/api/tasks", json=_task_payload(tmp_path, name="t1"))
    await client.post("/api/tasks", json=_task_payload(tmp_path, name="t2"))

    response = await client.get("/api/tasks")
    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_list_tasks_excludes_workspace_shadow(client, tmp_path, db):
    await client.post("/api/tasks", json=_task_payload(tmp_path, name="visible"))
    await db.execute(
        "INSERT INTO tasks (id, name, repo_path, status, tools, analysis_focus, "
        "prompt_content, progress, created_at, updated_at) "
        "VALUES ('ws-shadow', '__ws_hidden', ?, 'pending', '[]', 'x', 'y', 0, "
        "'2026-01-01', '2026-01-01')",
        (str(tmp_path),),
    )
    await db.commit()

    response = await client.get("/api/tasks")
    names = [t["name"] for t in response.json()]
    assert "visible" in names
    assert "__ws_hidden" not in names


async def test_get_task(client, tmp_path):
    created = await client.post(
        "/api/tasks", json=_task_payload(tmp_path, name="get-me")
    )
    task_id = created.json()["id"]

    response = await client.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    assert response.json()["id"] == task_id
    assert response.json()["name"] == "get-me"


async def test_get_task_not_found(client):
    response = await client.get("/api/tasks/nonexistent-id")
    assert response.status_code == 404


async def test_delete_task(client, tmp_path):
    created = await client.post(
        "/api/tasks", json=_task_payload(tmp_path, name="delete-me")
    )
    task_id = created.json()["id"]

    delete_resp = await client.delete(f"/api/tasks/{task_id}")
    assert delete_resp.status_code == 204

    get_resp = await client.get(f"/api/tasks/{task_id}")
    assert get_resp.status_code == 404


async def test_delete_task_not_found(client):
    response = await client.delete("/api/tasks/nonexistent-id")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Output files
# ---------------------------------------------------------------------------


async def test_list_output_empty(client, tmp_path):
    created = await client.post("/api/tasks", json=_task_payload(tmp_path))
    task_id = created.json()["id"]

    response = await client.get(f"/api/tasks/{task_id}/output")
    assert response.status_code == 200
    assert response.json() == []


async def test_list_output_returns_md_files(client, tmp_path):
    created = await client.post("/api/tasks", json=_task_payload(tmp_path))
    task_id = created.json()["id"]

    from app.config import settings
    out_dir = settings.outputs_path / task_id
    out_dir.mkdir(parents=True)
    (out_dir / "report.md").write_text("# Report", encoding="utf-8")
    (out_dir / "summary.md").write_text("# Summary", encoding="utf-8")
    (out_dir / "data.json").write_text("{}", encoding="utf-8")

    response = await client.get(f"/api/tasks/{task_id}/output")
    assert response.status_code == 200
    filenames = [f["filename"] for f in response.json()]
    assert "report.md" in filenames
    assert "summary.md" in filenames
    assert "data.json" not in filenames


async def test_list_output_task_not_found(client):
    response = await client.get("/api/tasks/no-such-task/output")
    assert response.status_code == 404


async def test_read_output_file(client, tmp_path):
    created = await client.post("/api/tasks", json=_task_payload(tmp_path))
    task_id = created.json()["id"]

    from app.config import settings
    out_dir = settings.outputs_path / task_id
    out_dir.mkdir(parents=True)
    (out_dir / "report.md").write_text("hello world", encoding="utf-8")

    response = await client.get(f"/api/tasks/{task_id}/output/report.md")
    assert response.status_code == 200
    assert response.json()["content"] == "hello world"
    assert response.json()["filename"] == "report.md"


async def test_read_output_file_not_found(client, tmp_path):
    created = await client.post("/api/tasks", json=_task_payload(tmp_path))
    task_id = created.json()["id"]

    response = await client.get(f"/api/tasks/{task_id}/output/nonexistent.md")
    assert response.status_code == 404


async def test_read_output_path_traversal(client, tmp_path):
    created = await client.post("/api/tasks", json=_task_payload(tmp_path))
    task_id = created.json()["id"]

    from app.config import settings
    out_dir = settings.outputs_path / task_id
    out_dir.mkdir(parents=True)

    response = await client.get(f"/api/tasks/{task_id}/output/..%2F..%2Fetc%2Fpasswd")
    assert response.status_code in (400, 404)


# ---------------------------------------------------------------------------
# Debug files
# ---------------------------------------------------------------------------


async def test_list_debug_empty(client, tmp_path):
    created = await client.post("/api/tasks", json=_task_payload(tmp_path))
    task_id = created.json()["id"]

    response = await client.get(f"/api/tasks/{task_id}/debug")
    assert response.status_code == 200
    assert response.json() == []


async def test_list_debug_returns_files(client, tmp_path):
    created = await client.post("/api/tasks", json=_task_payload(tmp_path))
    task_id = created.json()["id"]

    from app.config import settings
    debug_dir = settings.outputs_path / task_id / "debug"
    debug_dir.mkdir(parents=True)
    (debug_dir / "prompt_01.txt").write_text("prompt", encoding="utf-8")

    response = await client.get(f"/api/tasks/{task_id}/debug")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["filename"] == "prompt_01.txt"


async def test_list_debug_task_not_found(client):
    response = await client.get("/api/tasks/no-such-task/debug")
    assert response.status_code == 404


async def test_read_debug_file(client, tmp_path):
    created = await client.post("/api/tasks", json=_task_payload(tmp_path))
    task_id = created.json()["id"]

    from app.config import settings
    debug_dir = settings.outputs_path / task_id / "debug"
    debug_dir.mkdir(parents=True)
    (debug_dir / "snap.txt").write_text("debug content", encoding="utf-8")

    response = await client.get(f"/api/tasks/{task_id}/debug/snap.txt")
    assert response.status_code == 200
    assert response.json()["content"] == "debug content"


async def test_read_debug_file_not_found(client, tmp_path):
    created = await client.post("/api/tasks", json=_task_payload(tmp_path))
    task_id = created.json()["id"]

    response = await client.get(f"/api/tasks/{task_id}/debug/nope.txt")
    assert response.status_code == 404


async def test_read_debug_path_traversal(client, tmp_path):
    created = await client.post("/api/tasks", json=_task_payload(tmp_path))
    task_id = created.json()["id"]

    from app.config import settings
    (settings.outputs_path / task_id / "debug").mkdir(parents=True)

    response = await client.get(f"/api/tasks/{task_id}/debug/..%2F..%2Fetc%2Fpasswd")
    assert response.status_code in (400, 404)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


async def test_get_steps_empty(client, tmp_path):
    created = await client.post("/api/tasks", json=_task_payload(tmp_path))
    task_id = created.json()["id"]

    response = await client.get(f"/api/tasks/{task_id}/steps")
    assert response.status_code == 200
    assert response.json() == []


async def test_get_steps_returns_entries(client, tmp_path):
    created = await client.post("/api/tasks", json=_task_payload(tmp_path))
    task_id = created.json()["id"]

    from app.config import settings
    out_dir = settings.outputs_path / task_id
    out_dir.mkdir(parents=True)
    lines = [
        json.dumps({"step": "gitnexus", "status": "done"}),
        json.dumps({"step": "cgc", "status": "running"}),
    ]
    (out_dir / "steps.jsonl").write_text("\n".join(lines), encoding="utf-8")

    response = await client.get(f"/api/tasks/{task_id}/steps")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["step"] == "gitnexus"


async def test_get_steps_task_not_found(client):
    response = await client.get("/api/tasks/no-such-task/steps")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Run task
# ---------------------------------------------------------------------------


async def test_run_task_not_found(client):
    response = await client.post("/api/tasks/nonexistent/run")
    assert response.status_code == 404


async def test_run_task_already_running(client, tmp_path, db):
    created = await client.post("/api/tasks", json=_task_payload(tmp_path))
    task_id = created.json()["id"]

    await db.execute(
        "UPDATE tasks SET status = 'running' WHERE id = ?", (task_id,)
    )
    await db.commit()

    response = await client.post(f"/api/tasks/{task_id}/run")
    assert response.status_code == 409


async def test_run_task_gitnexus_launch_contract(client, tmp_path):
    created = await client.post("/api/tasks", json=_task_payload(tmp_path, tools=["gitnexus"]))
    task_id = created.json()["id"]

    with patch(
        "app.services.analysis_pipeline.AnalysisPipeline.run",
        new_callable=AsyncMock,
    ):
        response = await client.post(f"/api/tasks/{task_id}/run")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"
    assert data["task_id"] == task_id
    assert data["warnings"] == []


async def test_run_task_success_launches_pipeline(client, tmp_path):
    created = await client.post(
        "/api/tasks",
        json=_task_payload(tmp_path, tools=[]),
    )
    task_id = created.json()["id"]

    with patch(
        "app.services.analysis_pipeline.AnalysisPipeline.run",
        new_callable=AsyncMock,
    ):
        response = await client.post(f"/api/tasks/{task_id}/run")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"
    assert data["task_id"] == task_id


async def test_run_task_filters_removed_legacy_tool_selection(client, tmp_path, db):
    created = await client.post(
        "/api/tasks",
        json=_task_payload(tmp_path, tools=["gitnexus"]),
    )
    task_id = created.json()["id"]
    await db.execute(
        "UPDATE tasks SET tools = ? WHERE id = ?",
        (json.dumps(["deepwiki"]), task_id),
    )
    await db.commit()

    with patch(
        "app.services.analysis_pipeline.AnalysisPipeline.run",
        new_callable=AsyncMock,
    ):
        response = await client.post(f"/api/tasks/{task_id}/run")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"
    assert data["warnings"] == [
        "DeepWiki 已移除，请改用 GitNexus、AI 线程或 Workbench 智能体编排。"
    ]

    stored = await client.get(f"/api/tasks/{task_id}")
    assert stored.json()["tools"] == []


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


async def test_get_chat_history_empty(client, tmp_path):
    created = await client.post("/api/tasks", json=_task_payload(tmp_path))
    task_id = created.json()["id"]

    response = await client.get(f"/api/tasks/{task_id}/chat")
    assert response.status_code == 200
    assert response.json() == []


async def test_get_chat_history_task_not_found(client):
    response = await client.get("/api/tasks/no-such-task/chat")
    assert response.status_code == 404


async def test_send_chat_no_reports(client, tmp_path):
    created = await client.post("/api/tasks", json=_task_payload(tmp_path))
    task_id = created.json()["id"]

    response = await client.post(
        f"/api/tasks/{task_id}/chat",
        json={"message": "hello"},
    )
    assert response.status_code == 400
    assert "尚无分析报告" in response.json()["detail"]


async def test_send_chat_task_not_found(client):
    response = await client.post(
        "/api/tasks/no-such-task/chat",
        json={"message": "hello"},
    )
    assert response.status_code == 404


async def test_send_chat_streams_response(client, tmp_path):
    created = await client.post("/api/tasks", json=_task_payload(tmp_path))
    task_id = created.json()["id"]

    from app.config import settings
    out_dir = settings.outputs_path / task_id
    out_dir.mkdir(parents=True)
    (out_dir / "report.md").write_text("# Analysis report", encoding="utf-8")

    async def fake_stream(*args, **kwargs):
        yield "Hello "
        yield "world"

    mock_llm = AsyncMock()
    mock_llm.stream_complete = fake_stream

    with patch(
        "app.llm.factory.create_llm_client_from_active",
        new_callable=AsyncMock,
        return_value=mock_llm,
    ):
        response = await client.post(
            f"/api/tasks/{task_id}/chat",
            json={"message": "summarize"},
        )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]

    body = response.text
    assert "Hello " in body
    assert "world" in body


async def test_send_chat_llm_unavailable(client, tmp_path):
    created = await client.post("/api/tasks", json=_task_payload(tmp_path))
    task_id = created.json()["id"]

    from app.config import settings
    out_dir = settings.outputs_path / task_id
    out_dir.mkdir(parents=True)
    (out_dir / "report.md").write_text("# Report", encoding="utf-8")

    with patch(
        "app.llm.factory.create_llm_client_from_active",
        new_callable=AsyncMock,
        side_effect=RuntimeError("no active model"),
    ):
        response = await client.post(
            f"/api/tasks/{task_id}/chat",
            json={"message": "hello"},
        )

    assert response.status_code == 503
    assert "LLM" in response.json()["detail"]


async def test_send_chat_stream_error_yields_error_event(client, tmp_path):
    created = await client.post("/api/tasks", json=_task_payload(tmp_path))
    task_id = created.json()["id"]

    from app.config import settings
    out_dir = settings.outputs_path / task_id
    out_dir.mkdir(parents=True)
    (out_dir / "report.md").write_text("# Report", encoding="utf-8")

    async def _failing_stream(messages, **kwargs):
        raise RuntimeError("LLM stream failed")
        yield  # makes it an async generator

    mock_llm = MagicMock()
    mock_llm.stream_complete = _failing_stream

    with patch(
        "app.llm.factory.create_llm_client_from_active",
        new_callable=AsyncMock,
        return_value=mock_llm,
    ):
        response = await client.post(
            f"/api/tasks/{task_id}/chat",
            json={"message": "hello"},
        )

    assert response.status_code == 200
    assert "error" in response.text


async def test_send_chat_db_persist_exception_swallowed(client, tmp_path):
    created = await client.post("/api/tasks", json=_task_payload(tmp_path))
    task_id = created.json()["id"]

    from app.config import settings
    out_dir = settings.outputs_path / task_id
    out_dir.mkdir(parents=True)
    (out_dir / "report.md").write_text("# Report", encoding="utf-8")

    async def _ok_stream(messages, **kwargs):
        yield "Hello"

    mock_llm = MagicMock()
    mock_llm.stream_complete = _ok_stream

    import aiosqlite as _aiosqlite

    with patch(
        "app.llm.factory.create_llm_client_from_active",
        new_callable=AsyncMock,
        return_value=mock_llm,
    ):
        with patch(
            "app.api.tasks.aiosqlite.connect",
            side_effect=_aiosqlite.OperationalError("DB failed"),
        ):
            response = await client.post(
                f"/api/tasks/{task_id}/chat",
                json={"message": "hello"},
            )

    assert response.status_code == 200
    assert "Hello" in response.text
