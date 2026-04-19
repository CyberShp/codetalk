import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.api import repos as repos_api
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
        self.deleted = []
        self.commits = 0

    async def execute(self, _query):
        assert self._execute_results, "unexpected execute() call"
        return self._execute_results.pop(0)

    async def get(self, _model, key):
        return self._get_map.get(key)

    async def commit(self):
        self.commits += 1

    async def delete(self, obj):
        self.deleted.append(obj)


class RepoRouteContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.holder = {"db": None}

        async def _fake_db():
            yield self.holder["db"]

        app.dependency_overrides[repos_api.get_db] = _fake_db
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()

    async def test_sync_repository_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            name="open-iscsi",
            local_path=None,
            last_indexed_at=None,
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        with patch.object(
            repos_api.source_manager,
            "resolve_source",
            new=AsyncMock(return_value="/data/repos/open-iscsi"),
        ):
            response = await self.client.post(f"/api/repos/{repo_id}/sync")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "synced")
        self.assertEqual(body["local_path"], "/data/repos/open-iscsi")
        self.assertIsNotNone(body["last_indexed_at"])
        self.assertEqual(repo.local_path, "/data/repos/open-iscsi")
        self.assertEqual(self.holder["db"].commits, 1)

    async def test_search_repository_rejects_empty_query(self) -> None:
        repo_id = uuid.uuid4()
        self.holder["db"] = _FakeDB()

        response = await self.client.post(
            f"/api/repos/{repo_id}/search",
            json={"query": "   ", "num": 50},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "query 不能为空"})

    async def test_search_repository_rejects_unsynced_repo(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            name="open-iscsi",
            local_path=None,
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        response = await self.client.post(
            f"/api/repos/{repo_id}/search",
            json={"query": "main", "num": 20},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {"detail": "仓库尚未同步，请先执行 sync 再搜索"},
        )

    async def test_cancel_sync_conflict_contract(self) -> None:
        with patch.object(
            repos_api.source_manager,
            "cancel_sync",
            new=AsyncMock(return_value=False),
        ):
            response = await self.client.post(
                f"/api/repos/{uuid.uuid4()}/sync/cancel"
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {"detail": "No active sync to cancel"},
        )

    async def test_get_repo_detail_contract(self) -> None:
        repo_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        repo = SimpleNamespace(
            id=repo_id,
            name="open-iscsi",
            source_type="local_path",
            source_uri="/data/repos/open-iscsi",
            local_path="/data/repos/open-iscsi",
            branch="main",
            last_indexed_at=now,
        )
        wiki_meta = SimpleNamespace(
            generated_at=now,
            branch="main",
            last_indexed_at=now,
        )
        task = SimpleNamespace(
            tool_runs=[
                SimpleNamespace(
                    tool_name="gitnexus",
                    result={
                        "metadata": {
                            "node_count": 8213,
                            "edge_count": 16072,
                            "process_count": 300,
                            "community_count": 175,
                        }
                    },
                )
            ],
            completed_at=now,
        )
        self.holder["db"] = _FakeDB(
            execute_results=[
                _FakeResult(scalar_value=wiki_meta),
                _FakeResult(items=[task]),
            ],
            get_map={repo_id: repo},
        )

        response = await self.client.get(f"/api/repos/{repo_id}")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["repo"]["id"], str(repo_id))
        self.assertEqual(body["wiki"]["status"], "ready")
        self.assertFalse(body["wiki"]["stale"])
        self.assertEqual(body["graph"]["status"], "ready")
        self.assertEqual(body["graph"]["stats"]["node_count"], 8213)
        self.assertEqual(body["graph"]["stats"]["community_count"], 175)


if __name__ == "__main__":
    unittest.main()
