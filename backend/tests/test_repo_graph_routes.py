import asyncio
import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.api import repo_graph as repo_graph_api
from app.main import app


class _FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _FakeResult:
    def __init__(self, items=None):
        self._items = items or []

    def scalars(self):
        return _FakeScalars(self._items)


class _FakeDB:
    def __init__(self, *, execute_results=None, get_map=None):
        self._execute_results = list(execute_results or [])
        self._get_map = dict(get_map or {})

    async def execute(self, _query):
        assert self._execute_results, "unexpected execute() call"
        return self._execute_results.pop(0)

    async def get(self, _model, key):
        return self._get_map.get(key)


class RepoGraphRouteContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.holder = {"db": None}
        repo_graph_api._live_graph_cache.clear()
        repo_graph_api._live_graph_inflight.clear()

        async def _fake_db():
            yield self.holder["db"]

        app.dependency_overrides[repo_graph_api.get_db] = _fake_db
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()
        repo_graph_api._live_graph_cache.clear()
        repo_graph_api._live_graph_inflight.clear()

    async def test_get_repo_graph_cached_contract(self) -> None:
        repo_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        repo = SimpleNamespace(id=repo_id, local_path="/data/repos/open-iscsi")
        task = SimpleNamespace(
            tool_runs=[
                SimpleNamespace(
                    tool_name="gitnexus",
                    status="completed",
                    result={
                        "graph": {"nodes": [{"id": "n1"}], "edges": []},
                        "metadata": {"node_count": 1},
                    },
                )
            ],
            completed_at=now,
        )
        self.holder["db"] = _FakeDB(
            get_map={repo_id: repo},
            execute_results=[_FakeResult(items=[task])],
        )

        response = await self.client.get(f"/api/repos/{repo_id}/graph")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ready",
                "graph": {"nodes": [{"id": "n1"}], "edges": []},
                "metadata": {"node_count": 1},
                "analyzed_at": now.isoformat(),
            },
        )

    async def test_get_repo_graph_unsynced_repo_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(id=repo_id, local_path=None)
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        response = await self.client.get(f"/api/repos/{repo_id}/graph")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "not_analyzed",
                "graph": None,
                "metadata": None,
                "analyzed_at": None,
            },
        )

    async def test_get_repo_graph_reuses_live_cache_across_requests(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(id=repo_id, local_path="/data/repos/open-iscsi")
        self.holder["db"] = _FakeDB(
            get_map={repo_id: repo},
            execute_results=[_FakeResult(items=[]), _FakeResult(items=[])],
        )
        live_payload = {
            "status": "ready",
            "graph": {"nodes": [{"id": "n1"}], "edges": []},
            "metadata": {"node_count": 1},
            "analyzed_at": "2026-04-22T00:00:00+00:00",
        }

        with patch.object(
            repo_graph_api,
            "_build_live_graph_response",
            AsyncMock(return_value=live_payload),
        ) as build_live:
            first = await self.client.get(f"/api/repos/{repo_id}/graph")
            second = await self.client.get(f"/api/repos/{repo_id}/graph")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json(), live_payload)
        self.assertEqual(second.json(), live_payload)
        self.assertEqual(build_live.await_count, 1)

    async def test_get_repo_graph_dedupes_concurrent_live_builds(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(id=repo_id, local_path="/data/repos/open-iscsi")
        self.holder["db"] = _FakeDB(
            get_map={repo_id: repo},
            execute_results=[
                _FakeResult(items=[]),
                _FakeResult(items=[]),
            ],
        )
        live_payload = {
            "status": "ready",
            "graph": {"nodes": [{"id": "n1"}], "edges": []},
            "metadata": {"node_count": 1},
            "analyzed_at": "2026-04-22T00:00:00+00:00",
        }

        async def slow_build(_repo_local_path: str) -> dict:
            await asyncio.sleep(0.05)
            return live_payload

        with patch.object(
            repo_graph_api,
            "_build_live_graph_response",
            AsyncMock(side_effect=slow_build),
        ) as build_live:
            first, second = await asyncio.gather(
                self.client.get(f"/api/repos/{repo_id}/graph"),
                self.client.get(f"/api/repos/{repo_id}/graph"),
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json(), live_payload)
        self.assertEqual(second.json(), live_payload)
        self.assertEqual(build_live.await_count, 1)


if __name__ == "__main__":
    unittest.main()
