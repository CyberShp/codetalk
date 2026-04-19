import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.api import tasks as tasks_api
from app.main import app
from app.models.repository import Repository


class _FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _FakeResult:
    def __init__(self, items=None, scalar_value=None):
        self._items = items
        self._scalar_value = scalar_value

    def scalars(self):
        return _FakeScalars(self._items or [])

    def scalar_one_or_none(self):
        return self._scalar_value


class _FakeDB:
    def __init__(self, *, execute_results=None, get_map=None):
        self._execute_results = list(execute_results or [])
        self._get_map = dict(get_map or {})
        self.added = []
        self.deleted = []
        self.commits = 0
        self.refreshes = 0

    async def execute(self, _query):
        assert self._execute_results, "unexpected execute() call"
        return self._execute_results.pop(0)

    async def get(self, _model, key):
        return self._get_map.get(key)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj, attribute_names=None):
        self.refreshes += 1
        now = datetime.now(timezone.utc)
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = now
        if getattr(obj, "status", None) is None:
            obj.status = "pending"
        if getattr(obj, "progress", None) is None:
            obj.progress = 0
        if (
            attribute_names
            and "repository" in attribute_names
            and (not hasattr(obj, "repository") or obj.repository is None)
        ):
            obj.repository = Repository(
                project_id=uuid.uuid4(),
                name="open-iscsi",
                source_type="local_path",
                source_uri="/data/repos/open-iscsi",
                branch="main",
            )

    async def delete(self, obj):
        self.deleted.append(obj)


class TaskRouteContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.holder = {"db": None}

        async def _fake_db():
            yield self.holder["db"]

        app.dependency_overrides[tasks_api.get_db] = _fake_db
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()

    async def test_list_tasks_contract(self) -> None:
        repo_id = uuid.uuid4()
        task_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        task = SimpleNamespace(
            id=task_id,
            repository_id=repo_id,
            repository=SimpleNamespace(name="open-iscsi"),
            task_type="full_repo",
            status="completed",
            tools=["semgrep", "joern"],
            ai_enabled=True,
            progress=100,
            error=None,
            ai_summary="done",
            started_at=now,
            completed_at=now,
            created_at=now,
        )
        self.holder["db"] = _FakeDB(
            execute_results=[_FakeResult(items=[task])]
        )

        response = await self.client.get("/api/tasks")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "id": str(task_id),
                    "repository_id": str(repo_id),
                    "repository_name": "open-iscsi",
                    "task_type": "full_repo",
                    "status": "completed",
                    "tools": ["semgrep", "joern"],
                    "ai_enabled": True,
                    "progress": 100,
                    "error": None,
                    "ai_summary": "done",
                    "started_at": now.isoformat().replace("+00:00", "Z"),
                    "completed_at": now.isoformat().replace("+00:00", "Z"),
                    "created_at": now.isoformat().replace("+00:00", "Z"),
                }
            ],
        )

    async def test_create_task_contract(self) -> None:
        repo_id = uuid.uuid4()
        self.holder["db"] = _FakeDB()
        fake_handle = object()

        def _fake_create_task(coro):
            coro.close()
            return fake_handle

        with patch.object(
            tasks_api.task_engine, "run_task", new=AsyncMock()
        ), patch.object(
            tasks_api.asyncio, "create_task", side_effect=_fake_create_task
        ) as create_task_mock, patch.object(
            tasks_api.task_engine, "register_task"
        ) as register_mock:
            response = await self.client.post(
                "/api/tasks",
                json={
                    "repository_id": str(repo_id),
                    "task_type": "full_repo",
                    "tools": ["semgrep"],
                    "ai_enabled": False,
                    "target_spec": {},
                },
            )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["repository_id"], str(repo_id))
        self.assertEqual(body["status"], "pending")
        self.assertEqual(body["tools"], ["semgrep"])
        self.assertEqual(len(self.holder["db"].added), 1)
        self.assertEqual(self.holder["db"].commits, 1)
        self.assertEqual(self.holder["db"].refreshes, 2)
        create_task_mock.assert_called_once()
        register_mock.assert_called_once()

    async def test_get_task_404_contract(self) -> None:
        task_id = uuid.uuid4()
        self.holder["db"] = _FakeDB(
            execute_results=[_FakeResult(scalar_value=None)]
        )

        response = await self.client.get(f"/api/tasks/{task_id}")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Task not found"})

    async def test_get_task_detail_contract(self) -> None:
        repo_id = uuid.uuid4()
        task_id = uuid.uuid4()
        run_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        task = SimpleNamespace(
            id=task_id,
            repository_id=repo_id,
            repository=SimpleNamespace(name="open-iscsi"),
            task_type="full_repo",
            status="completed",
            tools=["semgrep", "joern"],
            ai_enabled=True,
            progress=100,
            error=None,
            ai_summary="summary",
            started_at=now,
            completed_at=now,
            created_at=now,
            tool_runs=[
                SimpleNamespace(
                    id=run_id,
                    tool_name="semgrep",
                    status="completed",
                    started_at=now,
                    completed_at=now,
                    result={"findings": 50},
                    error=None,
                )
            ],
        )
        self.holder["db"] = _FakeDB(
            execute_results=[_FakeResult(scalar_value=task)]
        )

        response = await self.client.get(f"/api/tasks/{task_id}")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], str(task_id))
        self.assertEqual(body["repository_name"], "open-iscsi")
        self.assertEqual(len(body["tool_runs"]), 1)
        self.assertEqual(body["tool_runs"][0]["id"], str(run_id))
        self.assertEqual(body["tool_runs"][0]["result"], {"findings": 50})

    async def test_get_task_results_contract(self) -> None:
        task_id = uuid.uuid4()
        run_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        run = SimpleNamespace(
            id=run_id,
            tool_name="joern",
            status="completed",
            started_at=now,
            completed_at=now,
            result={"branches": 292},
            error=None,
        )
        self.holder["db"] = _FakeDB(execute_results=[_FakeResult(items=[run])])

        response = await self.client.get(f"/api/tasks/{task_id}/results")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "task_id": str(task_id),
                "tool_runs": [
                    {
                        "id": str(run_id),
                        "tool_name": "joern",
                        "status": "completed",
                        "started_at": now.isoformat(),
                        "completed_at": now.isoformat(),
                        "result": {"branches": 292},
                        "error": None,
                    }
                ],
            },
        )

    async def test_get_task_file_returns_line_slice_contract(self) -> None:
        task_id = uuid.uuid4()
        repo_id = uuid.uuid4()
        with TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            target = repo_root / "src" / "main.c"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")
            task = SimpleNamespace(id=task_id, repository_id=repo_id)
            repo = SimpleNamespace(id=repo_id, local_path=str(repo_root))
            self.holder["db"] = _FakeDB(get_map={task_id: task, repo_id: repo})

            response = await self.client.get(
                f"/api/tasks/{task_id}/file",
                params={"path": "src/main.c", "start": 2, "end": 3},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "content": "line2\nline3\n",
                "startLine": 2,
                "endLine": 3,
                "totalLines": 4,
                "actualPath": "src/main.c",
            },
        )

    async def test_get_task_file_supports_basename_fallback(self) -> None:
        task_id = uuid.uuid4()
        repo_id = uuid.uuid4()
        with TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            target = repo_root / "drivers" / "iface.c"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("alpha\nbeta\n", encoding="utf-8")
            task = SimpleNamespace(id=task_id, repository_id=repo_id)
            repo = SimpleNamespace(id=repo_id, local_path=str(repo_root))
            self.holder["db"] = _FakeDB(get_map={task_id: task, repo_id: repo})

            response = await self.client.get(
                f"/api/tasks/{task_id}/file",
                params={"path": "iface.c"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["actualPath"], "drivers/iface.c")
        self.assertEqual(response.json()["content"], "alpha\nbeta\n")

    async def test_get_task_file_rejects_path_traversal(self) -> None:
        task_id = uuid.uuid4()
        repo_id = uuid.uuid4()
        task = SimpleNamespace(id=task_id, repository_id=repo_id)
        repo = SimpleNamespace(id=repo_id, local_path="/tmp/repo-root")
        self.holder["db"] = _FakeDB(get_map={task_id: task, repo_id: repo})

        response = await self.client.get(
            f"/api/tasks/{task_id}/file",
            params={"path": "../secrets.txt"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "Invalid path"})

    async def test_get_task_file_rejects_unsynced_repository(self) -> None:
        task_id = uuid.uuid4()
        repo_id = uuid.uuid4()
        task = SimpleNamespace(id=task_id, repository_id=repo_id)
        repo = SimpleNamespace(id=repo_id, local_path=None)
        self.holder["db"] = _FakeDB(get_map={task_id: task, repo_id: repo})

        response = await self.client.get(
            f"/api/tasks/{task_id}/file",
            params={"path": "main.c"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "Repository not synced"})

    async def test_get_task_file_missing_file_returns_404(self) -> None:
        task_id = uuid.uuid4()
        repo_id = uuid.uuid4()
        with TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            task = SimpleNamespace(id=task_id, repository_id=repo_id)
            repo = SimpleNamespace(id=repo_id, local_path=str(repo_root))
            self.holder["db"] = _FakeDB(get_map={task_id: task, repo_id: repo})

            response = await self.client.get(
                f"/api/tasks/{task_id}/file",
                params={"path": "missing.c"},
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "File not found: missing.c"})

    async def test_cancel_task_contract(self) -> None:
        task_id = uuid.uuid4()
        task = SimpleNamespace(
            id=task_id,
            status="running",
            completed_at=None,
        )
        self.holder["db"] = _FakeDB(get_map={task_id: task})

        with patch.object(
            tasks_api.task_engine, "cancel_task", new=AsyncMock(return_value=False)
        ):
            response = await self.client.post(f"/api/tasks/{task_id}/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "cancelled"})
        self.assertEqual(task.status, "cancelled")
        self.assertIsNotNone(task.completed_at)
        self.assertEqual(self.holder["db"].commits, 1)

    async def test_delete_task_contract(self) -> None:
        task_id = uuid.uuid4()
        task = SimpleNamespace(id=task_id, status="running")
        self.holder["db"] = _FakeDB(get_map={task_id: task})

        with patch.object(
            tasks_api.task_engine, "cancel_task", new=AsyncMock(return_value=True)
        ):
            response = await self.client.delete(f"/api/tasks/{task_id}")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.holder["db"].deleted, [task])
        self.assertEqual(self.holder["db"].commits, 1)


if __name__ == "__main__":
    unittest.main()
