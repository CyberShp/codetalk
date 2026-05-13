"""Tests for the /api/tasks CRUD endpoints."""


async def test_list_tasks_empty(client):
    response = await client.get("/api/tasks")
    assert response.status_code == 200
    assert response.json() == []


async def test_create_task(client, tmp_path):
    response = await client.post(
        "/api/tasks",
        json={"name": "my-task", "repo_path": str(tmp_path)},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "my-task"
    assert data["repo_path"] == str(tmp_path)
    assert data["status"] == "pending"
    assert data["progress"] == 0
    assert data["error_message"] is None
    assert "id" in data


async def test_create_task_default_tools(client, tmp_path):
    response = await client.post(
        "/api/tasks",
        json={"name": "t", "repo_path": str(tmp_path)},
    )
    assert response.status_code == 201
    assert response.json()["tools"] == ["gitnexus", "deepwiki"]


async def test_create_task_nonexistent_path(client):
    response = await client.post(
        "/api/tasks",
        json={"name": "bad", "repo_path": "/nonexistent/__xyz__"},
    )
    assert response.status_code == 422


async def test_list_tasks_after_create(client, tmp_path):
    await client.post("/api/tasks", json={"name": "t1", "repo_path": str(tmp_path)})
    await client.post("/api/tasks", json={"name": "t2", "repo_path": str(tmp_path)})

    response = await client.get("/api/tasks")
    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_get_task(client, tmp_path):
    created = await client.post(
        "/api/tasks",
        json={"name": "get-me", "repo_path": str(tmp_path)},
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
        "/api/tasks",
        json={"name": "delete-me", "repo_path": str(tmp_path)},
    )
    task_id = created.json()["id"]

    delete_resp = await client.delete(f"/api/tasks/{task_id}")
    assert delete_resp.status_code == 204

    get_resp = await client.get(f"/api/tasks/{task_id}")
    assert get_resp.status_code == 404


async def test_delete_task_not_found(client):
    response = await client.delete("/api/tasks/nonexistent-id")
    assert response.status_code == 404
