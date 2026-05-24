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
