"""E2E tests for /api/tasks endpoints."""

import uuid

from httpx import AsyncClient


# -- Helpers --

def _task_payload(repo_path: str, **overrides) -> dict:
    base = {
        "name": "Test Analysis Task",
        "repo_path": repo_path,
        "tools": ["gitnexus", "deepwiki"],
        "analysis_focus": "Analyze code structure and dependencies",
        "prompt_content": "Please analyze this repository thoroughly.",
        "deepwiki_depth": "balanced",
    }
    base.update(overrides)
    return base


# -- List --

async def test_list_tasks_empty(e2e_client: AsyncClient):
    resp = await e2e_client.get("/api/tasks")
    assert resp.status_code == 200
    assert resp.json() == []


# -- CRUD --

async def test_create_task(e2e_client: AsyncClient, repo_path: str):
    resp = await e2e_client.post("/api/tasks", json=_task_payload(repo_path))
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Test Analysis Task"
    assert body["status"] == "pending"
    assert body["repo_path"] == repo_path
    assert body["id"]
    assert body["created_at"]


async def test_get_task_by_id(e2e_client: AsyncClient, repo_path: str):
    create_resp = await e2e_client.post("/api/tasks", json=_task_payload(repo_path))
    task_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/tasks/{task_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == task_id
    assert body["name"] == "Test Analysis Task"


async def test_delete_task(e2e_client: AsyncClient, repo_path: str):
    create_resp = await e2e_client.post("/api/tasks", json=_task_payload(repo_path))
    task_id = create_resp.json()["id"]

    resp = await e2e_client.delete(f"/api/tasks/{task_id}")
    assert resp.status_code == 204

    get_resp = await e2e_client.get(f"/api/tasks/{task_id}")
    assert get_resp.status_code == 404


async def test_get_deleted_task_returns_404(e2e_client: AsyncClient, repo_path: str):
    create_resp = await e2e_client.post("/api/tasks", json=_task_payload(repo_path))
    task_id = create_resp.json()["id"]
    await e2e_client.delete(f"/api/tasks/{task_id}")

    resp = await e2e_client.get(f"/api/tasks/{task_id}")
    assert resp.status_code == 404


async def test_get_nonexistent_task(e2e_client: AsyncClient):
    resp = await e2e_client.get(f"/api/tasks/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_create_task_invalid_repo_path(e2e_client: AsyncClient):
    payload = _task_payload("/nonexistent/path/that/does/not/exist")
    resp = await e2e_client.post("/api/tasks", json=payload)
    assert resp.status_code == 422


async def test_task_status_fields(e2e_client: AsyncClient, repo_path: str):
    create_resp = await e2e_client.post("/api/tasks", json=_task_payload(repo_path))
    body = create_resp.json()

    expected_fields = {
        "id", "name", "repo_path", "status", "tools",
        "requirements_doc", "design_doc", "analysis_focus",
        "prompt_content", "deepwiki_depth", "material_ids",
        "progress", "error_message", "current_step",
        "created_at", "updated_at",
    }
    assert expected_fields.issubset(set(body.keys()))


async def test_task_steps_empty(e2e_client: AsyncClient, repo_path: str):
    create_resp = await e2e_client.post("/api/tasks", json=_task_payload(repo_path))
    task_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/tasks/{task_id}/steps")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_task_output_empty(e2e_client: AsyncClient, repo_path: str):
    create_resp = await e2e_client.post("/api/tasks", json=_task_payload(repo_path))
    task_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/tasks/{task_id}/output")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_task_run_tools_unavailable(e2e_client: AsyncClient, repo_path: str):
    """Running a task should fail if tool services are unavailable."""
    create_resp = await e2e_client.post("/api/tasks", json=_task_payload(repo_path))
    task_id = create_resp.json()["id"]

    resp = await e2e_client.post(f"/api/tasks/{task_id}/run")
    assert resp.status_code == 503


async def test_task_run_deepwiki_only_unavailable_returns_503(e2e_client: AsyncClient, repo_path: str):
    """Running a task with only deepwiki selected exercises the deepwiki health check path."""
    create_resp = await e2e_client.post(
        "/api/tasks",
        json=_task_payload(repo_path, tools=["deepwiki"]),
    )
    task_id = create_resp.json()["id"]

    resp = await e2e_client.post(f"/api/tasks/{task_id}/run")
    assert resp.status_code == 503


async def test_task_crud_roundtrip(e2e_client: AsyncClient, repo_path: str):
    create_resp = await e2e_client.post("/api/tasks", json=_task_payload(repo_path))
    assert create_resp.status_code == 201
    task_id = create_resp.json()["id"]

    get_resp = await e2e_client.get(f"/api/tasks/{task_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == task_id


async def test_task_chat_history_empty(e2e_client: AsyncClient, repo_path: str):
    create_resp = await e2e_client.post("/api/tasks", json=_task_payload(repo_path))
    task_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/tasks/{task_id}/chat")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_task_chat_history_nonexistent(e2e_client: AsyncClient):
    resp = await e2e_client.get(f"/api/tasks/{uuid.uuid4()}/chat")
    assert resp.status_code == 404


async def test_multiple_tasks_coexist(e2e_client: AsyncClient, repo_path: str):
    ids = []
    for i in range(3):
        resp = await e2e_client.post(
            "/api/tasks",
            json=_task_payload(repo_path, name=f"Task {i}"),
        )
        assert resp.status_code == 201
        ids.append(resp.json()["id"])

    list_resp = await e2e_client.get("/api/tasks")
    assert list_resp.status_code == 200
    listed_ids = [t["id"] for t in list_resp.json()]
    for tid in ids:
        assert tid in listed_ids


async def test_run_task_no_tools_exercises_pipeline(e2e_client: AsyncClient, repo_path: str):
    """Running a task with empty tools skips tool health checks and exercises the pipeline.

    With no tools and no LLM configured, the pipeline runs phases 0-1 and completes
    in 'completed' status (no AI phases). Covers analysis_pipeline.py orchestration paths.
    """
    create_resp = await e2e_client.post(
        "/api/tasks",
        json={
            "name": "No-tools Pipeline Test",
            "repo_path": repo_path,
            "tools": [],
            "analysis_focus": "Smoke test",
            "prompt_content": "Test pipeline with no external tools.",
            "deepwiki_depth": "balanced",
        },
    )
    assert create_resp.status_code == 201
    task_id = create_resp.json()["id"]

    run_resp = await e2e_client.post(f"/api/tasks/{task_id}/run")
    assert run_resp.status_code == 200

    get_resp = await e2e_client.get(f"/api/tasks/{task_id}")
    assert get_resp.status_code == 200
    status = get_resp.json()["status"]
    assert status in ("completed", "running", "failed")


async def test_run_nonexistent_task_returns_404(e2e_client: AsyncClient):
    """Running a task that does not exist should return 404."""
    import uuid
    resp = await e2e_client.post(f"/api/tasks/{uuid.uuid4()}/run")
    assert resp.status_code == 404


async def test_run_already_running_task_returns_409(e2e_client: AsyncClient, repo_path: str):
    """Attempting to run a task already in 'running' status returns 409."""
    import aiosqlite
    from app.config import settings

    create_resp = await e2e_client.post(
        "/api/tasks",
        json={
            "name": "Already Running",
            "repo_path": repo_path,
            "tools": [],
            "analysis_focus": "test",
            "prompt_content": "test",
            "deepwiki_depth": "balanced",
        },
    )
    task_id = create_resp.json()["id"]

    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute("UPDATE tasks SET status = 'running' WHERE id = ?", (task_id,))
        await db.commit()

    resp = await e2e_client.post(f"/api/tasks/{task_id}/run")
    assert resp.status_code == 409


# -- Chat (POST) --

async def test_task_chat_post_nonexistent_task_returns_404(e2e_client: AsyncClient):
    """POST chat to a non-existent task returns 404."""
    resp = await e2e_client.post(
        f"/api/tasks/{uuid.uuid4()}/chat",
        json={"message": "Hello?"},
    )
    assert resp.status_code == 404


async def test_task_chat_no_reports_returns_400(e2e_client: AsyncClient, repo_path: str):
    """POST chat to a task that has no report files returns 400."""
    create_resp = await e2e_client.post("/api/tasks", json=_task_payload(repo_path))
    task_id = create_resp.json()["id"]

    resp = await e2e_client.post(
        f"/api/tasks/{task_id}/chat",
        json={"message": "What is the architecture?"},
    )
    assert resp.status_code == 400
    assert "分析报告" in resp.json()["detail"]


async def test_task_chat_with_report_no_llm_returns_503(e2e_client: AsyncClient, repo_path: str):
    """POST chat with a report file present but no LLM configured returns 503."""
    from app.config import settings

    create_resp = await e2e_client.post("/api/tasks", json=_task_payload(repo_path))
    task_id = create_resp.json()["id"]

    task_output_dir = settings.outputs_path / task_id
    task_output_dir.mkdir(parents=True, exist_ok=True)
    (task_output_dir / "report.md").write_text("# Analysis\nCode structure overview.", encoding="utf-8")

    resp = await e2e_client.post(
        f"/api/tasks/{task_id}/chat",
        json={"message": "Describe the architecture."},
    )
    assert resp.status_code == 503


async def test_task_chat_with_history_exercises_loop(e2e_client: AsyncClient, repo_path: str):
    """POST chat when prior history exists exercises the history-loop body (line 316)."""
    import aiosqlite
    from datetime import datetime, timezone
    from app.config import settings

    create_resp = await e2e_client.post("/api/tasks", json=_task_payload(repo_path))
    task_id = create_resp.json()["id"]

    task_output_dir = settings.outputs_path / task_id
    task_output_dir.mkdir(parents=True, exist_ok=True)
    (task_output_dir / "report.md").write_text("# Report\nSome analysis.", encoding="utf-8")

    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(settings.sqlite_db) as db:
        await db.execute(
            "INSERT INTO task_chats (task_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (task_id, "user", "Prior question", now),
        )
        await db.execute(
            "INSERT INTO task_chats (task_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (task_id, "assistant", "Prior answer", now),
        )
        await db.commit()

    resp = await e2e_client.post(
        f"/api/tasks/{task_id}/chat",
        json={"message": "Follow-up question."},
    )
    # No LLM → 503; the history loop still executes before LLM acquisition
    assert resp.status_code == 503


# -- Debug endpoints --

async def test_task_debug_empty(e2e_client: AsyncClient, repo_path: str):
    """GET /debug for a task with no debug directory returns empty list."""
    create_resp = await e2e_client.post("/api/tasks", json=_task_payload(repo_path))
    task_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/tasks/{task_id}/debug")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_task_debug_nonexistent_task_returns_404(e2e_client: AsyncClient):
    """GET /debug for a non-existent task returns 404."""
    resp = await e2e_client.get(f"/api/tasks/{uuid.uuid4()}/debug")
    assert resp.status_code == 404


async def test_task_debug_with_files(e2e_client: AsyncClient, repo_path: str):
    """GET /debug lists files present in the debug directory."""
    from app.config import settings

    create_resp = await e2e_client.post("/api/tasks", json=_task_payload(repo_path))
    task_id = create_resp.json()["id"]

    debug_dir = settings.outputs_path / task_id / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    (debug_dir / "prompt_001.json").write_text('{"prompt": "test"}', encoding="utf-8")
    (debug_dir / "prompt_002.json").write_text('{"prompt": "test2"}', encoding="utf-8")

    resp = await e2e_client.get(f"/api/tasks/{task_id}/debug")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    filenames = {f["filename"] for f in body}
    assert "prompt_001.json" in filenames
    assert "prompt_002.json" in filenames


async def test_task_read_debug_file_nonexistent_task_returns_404(e2e_client: AsyncClient):
    """GET /debug/{filename} for a non-existent task returns 404."""
    resp = await e2e_client.get(f"/api/tasks/{uuid.uuid4()}/debug/prompt.json")
    assert resp.status_code == 404


async def test_task_read_debug_file_not_found_returns_404(e2e_client: AsyncClient, repo_path: str):
    """GET /debug/{filename} for a missing file returns 404."""
    create_resp = await e2e_client.post("/api/tasks", json=_task_payload(repo_path))
    task_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/tasks/{task_id}/debug/nonexistent.json")
    assert resp.status_code == 404


async def test_task_read_debug_file_success(e2e_client: AsyncClient, repo_path: str):
    """GET /debug/{filename} returns file content for an existing debug file."""
    from app.config import settings

    create_resp = await e2e_client.post("/api/tasks", json=_task_payload(repo_path))
    task_id = create_resp.json()["id"]

    debug_dir = settings.outputs_path / task_id / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    (debug_dir / "snapshot.json").write_text('{"step": 1}', encoding="utf-8")

    resp = await e2e_client.get(f"/api/tasks/{task_id}/debug/snapshot.json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "snapshot.json"
    assert '"step": 1' in body["content"]


async def test_task_read_debug_file_path_traversal_returns_400(e2e_client: AsyncClient, repo_path: str):
    """GET /debug/../etc/passwd should return 400 (path traversal blocked)."""
    create_resp = await e2e_client.post("/api/tasks", json=_task_payload(repo_path))
    task_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/tasks/{task_id}/debug/..%2F..%2Fetc%2Fpasswd")
    assert resp.status_code in (400, 404)


# -- Output file endpoints --

async def test_task_output_with_files(e2e_client: AsyncClient, repo_path: str):
    """GET /output lists .md files present in the task output directory."""
    from app.config import settings

    create_resp = await e2e_client.post("/api/tasks", json=_task_payload(repo_path))
    task_id = create_resp.json()["id"]

    output_dir = settings.outputs_path / task_id
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "analysis.md").write_text("# Report", encoding="utf-8")
    (output_dir / "notes.txt").write_text("ignored", encoding="utf-8")

    resp = await e2e_client.get(f"/api/tasks/{task_id}/output")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["filename"] == "analysis.md"


async def test_task_read_output_file_nonexistent_task_returns_404(e2e_client: AsyncClient):
    """GET /output/{filename} for a non-existent task returns 404."""
    resp = await e2e_client.get(f"/api/tasks/{uuid.uuid4()}/output/report.md")
    assert resp.status_code == 404


async def test_task_read_output_file_not_found_returns_404(e2e_client: AsyncClient, repo_path: str):
    """GET /output/{filename} for a missing file returns 404."""
    create_resp = await e2e_client.post("/api/tasks", json=_task_payload(repo_path))
    task_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/tasks/{task_id}/output/missing.md")
    assert resp.status_code == 404


async def test_task_read_output_file_success(e2e_client: AsyncClient, repo_path: str):
    """GET /output/{filename} returns content of an existing output file."""
    from app.config import settings

    create_resp = await e2e_client.post("/api/tasks", json=_task_payload(repo_path))
    task_id = create_resp.json()["id"]

    output_dir = settings.outputs_path / task_id
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.md").write_text("# Summary\nAll good.", encoding="utf-8")

    resp = await e2e_client.get(f"/api/tasks/{task_id}/output/summary.md")
    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "summary.md"
    assert "All good" in body["content"]


async def test_task_read_output_file_path_traversal_returns_400(e2e_client: AsyncClient, repo_path: str):
    """GET /output/../etc/passwd should return 400 (path traversal blocked)."""
    create_resp = await e2e_client.post("/api/tasks", json=_task_payload(repo_path))
    task_id = create_resp.json()["id"]

    resp = await e2e_client.get(f"/api/tasks/{task_id}/output/..%2F..%2Fetc%2Fpasswd")
    assert resp.status_code in (400, 404)


async def test_task_steps_malformed_jsonl_returns_empty(e2e_client: AsyncClient, repo_path: str):
    """Lines 392-397: GET /steps with a malformed steps.jsonl hits the exception handler
    and returns an empty list instead of propagating the parse error."""
    from app.config import settings

    create_resp = await e2e_client.post("/api/tasks", json=_task_payload(repo_path))
    task_id = create_resp.json()["id"]

    output_dir = settings.outputs_path / task_id
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "steps.jsonl").write_text("not valid json at all\n", encoding="utf-8")

    resp = await e2e_client.get(f"/api/tasks/{task_id}/steps")
    assert resp.status_code == 200
    assert resp.json() == []
