import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx

from app.api import projects as projects_api
from app.main import app


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

    def scalar(self):
        return self._scalar_value

    def scalar_one_or_none(self):
        return self._scalar_value


class _FakeDB:
    def __init__(self, *, execute_results=None, get_map=None):
        self._execute_results = list(execute_results or [])
        self._get_map = dict(get_map or {})
        self.added = []
        self.deleted = []
        self.commits = 0
        self.flushes = 0
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

    async def refresh(self, obj):
        self.refreshes += 1
        now = datetime.now(timezone.utc)
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = now
        if hasattr(obj, "updated_at") and getattr(obj, "updated_at", None) is None:
            obj.updated_at = now

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        self.flushes += 1


class ProjectRouteContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.holder = {"db": None}

        async def _fake_db():
            yield self.holder["db"]

        app.dependency_overrides[projects_api.get_db] = _fake_db
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()

    async def test_list_projects_contract(self) -> None:
        project_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        project = SimpleNamespace(
            id=project_id,
            name="iscsi",
            description="open-iscsi",
            created_at=now,
            updated_at=now,
        )
        self.holder["db"] = _FakeDB(
            execute_results=[
                _FakeResult(items=[project]),
                _FakeResult(scalar_value=3),
            ]
        )

        response = await self.client.get("/api/projects")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "id": str(project_id),
                    "name": "iscsi",
                    "description": "open-iscsi",
                    "created_at": now.isoformat().replace("+00:00", "Z"),
                    "updated_at": now.isoformat().replace("+00:00", "Z"),
                    "repo_count": 3,
                }
            ],
        )

    async def test_get_project_404_contract(self) -> None:
        project_id = uuid.uuid4()
        self.holder["db"] = _FakeDB(get_map={})

        response = await self.client.get(f"/api/projects/{project_id}")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Project not found"})

    async def test_list_repositories_contract(self) -> None:
        project_id = uuid.uuid4()
        repo_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        project = SimpleNamespace(id=project_id)
        repo = SimpleNamespace(
            id=repo_id,
            project_id=project_id,
            name="open-iscsi",
            source_type="local_path",
            source_uri="/data/repos/open-iscsi",
            local_path="/data/repos/open-iscsi",
            branch="main",
            last_indexed_at=None,
            created_at=now,
        )
        self.holder["db"] = _FakeDB(
            execute_results=[_FakeResult(items=[repo])],
            get_map={project_id: project},
        )

        response = await self.client.get(
            f"/api/projects/{project_id}/repositories"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "id": str(repo_id),
                    "project_id": str(project_id),
                    "name": "open-iscsi",
                    "source_type": "local_path",
                    "source_uri": "/data/repos/open-iscsi",
                    "local_path": "/data/repos/open-iscsi",
                    "branch": "main",
                    "last_indexed_at": None,
                    "created_at": now.isoformat().replace("+00:00", "Z"),
                }
            ],
        )

    async def test_create_project_contract(self) -> None:
        self.holder["db"] = _FakeDB()

        response = await self.client.post(
            "/api/projects",
            json={
                "name": "SPDK",
                "description": "storage performance toolkit",
            },
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["name"], "SPDK")
        self.assertEqual(
            body["description"], "storage performance toolkit"
        )
        self.assertEqual(body["repo_count"], 0)
        self.assertEqual(len(self.holder["db"].added), 1)
        self.assertEqual(self.holder["db"].commits, 1)
        self.assertEqual(self.holder["db"].refreshes, 1)

    async def test_update_project_contract(self) -> None:
        project_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        project = SimpleNamespace(
            id=project_id,
            name="old",
            description="before",
            created_at=now,
            updated_at=now,
        )
        self.holder["db"] = _FakeDB(
            execute_results=[_FakeResult(scalar_value=5)],
            get_map={project_id: project},
        )

        response = await self.client.put(
            f"/api/projects/{project_id}",
            json={"name": "new", "description": "after"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["name"], "new")
        self.assertEqual(body["description"], "after")
        self.assertEqual(body["repo_count"], 5)
        self.assertEqual(self.holder["db"].commits, 1)
        self.assertEqual(self.holder["db"].refreshes, 1)

    async def test_add_repository_contract(self) -> None:
        project_id = uuid.uuid4()
        project = SimpleNamespace(id=project_id)
        self.holder["db"] = _FakeDB(get_map={project_id: project})

        response = await self.client.post(
            f"/api/projects/{project_id}/repositories",
            json={
                "name": "open-iscsi",
                "source_type": "local_path",
                "source_uri": "/data/repos/open-iscsi",
                "branch": "main",
            },
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["project_id"], str(project_id))
        self.assertEqual(body["name"], "open-iscsi")
        self.assertEqual(body["source_type"], "local_path")
        self.assertEqual(body["branch"], "main")
        self.assertEqual(len(self.holder["db"].added), 1)
        self.assertEqual(self.holder["db"].commits, 1)
        self.assertEqual(self.holder["db"].refreshes, 1)


if __name__ == "__main__":
    unittest.main()
