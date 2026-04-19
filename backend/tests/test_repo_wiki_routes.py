import unittest
import uuid
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.api import repo_wiki as repo_wiki_api
from app.main import app


class _FakeResult:
    def __init__(self, scalar_value=None):
        self._scalar_value = scalar_value

    def scalar_one_or_none(self):
        return self._scalar_value


class _FakeDB:
    def __init__(self, *, execute_results=None, get_map=None):
        self._execute_results = list(execute_results or [])
        self._get_map = dict(get_map or {})
        self.closed = False
        self.deleted = []
        self.commits = 0

    async def execute(self, _query):
        assert self._execute_results, "unexpected execute() call"
        return self._execute_results.pop(0)

    async def get(self, _model, key):
        return self._get_map.get(key)

    async def close(self):
        self.closed = True

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.commits += 1


class _FakeWikiExportClient:
    def __init__(self, response: httpx.Response):
        self.response = response
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, path: str, json=None):
        self.calls.append((path, json))
        return self.response


class RepoWikiRouteContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.holder = {"db": None}

        async def _fake_db():
            yield self.holder["db"]

        app.dependency_overrides[repo_wiki_api.get_db] = _fake_db
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )
        self._saved_generation = dict(repo_wiki_api._generation_status)
        repo_wiki_api._generation_status.clear()

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()
        repo_wiki_api._generation_status.clear()
        repo_wiki_api._generation_status.update(self._saved_generation)

    async def test_get_repo_wiki_not_generated_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(id=repo_id, local_path="/data/repos/open-iscsi")
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        with patch.object(
            repo_wiki_api._orchestrator,
            "get_cached_wiki",
            new=AsyncMock(return_value=None),
        ):
            response = await self.client.get(f"/api/repos/{repo_id}/wiki")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "not_generated", "wiki": None, "stale": False},
        )

    async def test_repo_wiki_status_default_contract(self) -> None:
        response = await self.client.get(f"/api/repos/{uuid.uuid4()}/wiki/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "running": False,
                "current": 0,
                "total": 0,
                "page_title": "",
                "error": None,
            },
        )

    async def test_generate_repo_wiki_conflict_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            local_path="/data/repos/open-iscsi",
            branch="main",
            last_indexed_at=None,
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})
        repo_wiki_api._generation_status[str(repo_id)] = {"running": True}

        response = await self.client.post(
            f"/api/repos/{repo_id}/wiki/generate",
            json={"comprehensive": True, "force_refresh": False},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {
                "detail": "Wiki generation already in progress for this repository"
            },
        )

    async def test_generate_repo_wiki_started_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            local_path="/data/repos/open-iscsi",
            branch="main",
            last_indexed_at=None,
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        def _fake_create_task(coro):
            coro.close()
            return object()

        with patch.object(
            repo_wiki_api,
            "_get_llm_options",
            new=AsyncMock(
                return_value={
                    "provider": "openai",
                    "model": "gpt-4o",
                    "proxy_mode": "system",
                }
            ),
        ), patch("asyncio.create_task", side_effect=_fake_create_task), patch.object(
            repo_wiki_api._orchestrator,
            "delete_cache",
            new=AsyncMock(),
        ):
            response = await self.client.post(
                f"/api/repos/{repo_id}/wiki/generate",
                json={"comprehensive": True, "force_refresh": False},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "started",
                "message": "Wiki generation started in background",
            },
        )
        self.assertTrue(self.holder["db"].closed)
        self.assertTrue(repo_wiki_api._generation_status[str(repo_id)]["running"])

    async def test_regenerate_wiki_page_value_error_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            local_path="/data/repos/open-iscsi",
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        with patch.object(
            repo_wiki_api,
            "_get_llm_options",
            new=AsyncMock(
                return_value={
                    "provider": "openai",
                    "model": "gpt-4o",
                    "proxy_mode": "system",
                }
            ),
        ), patch.object(
            repo_wiki_api._orchestrator,
            "regenerate_page",
            new=AsyncMock(side_effect=ValueError("bad page")),
        ):
            response = await self.client.post(
                f"/api/repos/{repo_id}/wiki/regenerate-page",
                json={
                    "page_id": "p1",
                    "page_title": "Overview",
                    "file_paths": [],
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "bad page"})

    async def test_regenerate_wiki_page_success_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(
            id=repo_id,
            local_path="/data/repos/open-iscsi",
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        with patch.object(
            repo_wiki_api,
            "_get_llm_options",
            new=AsyncMock(
                return_value={
                    "provider": "openai",
                    "model": "gpt-4o",
                    "proxy_mode": "system",
                }
            ),
        ), patch.object(
            repo_wiki_api._orchestrator,
            "regenerate_page",
            new=AsyncMock(return_value="# Overview"),
        ):
            response = await self.client.post(
                f"/api/repos/{repo_id}/wiki/regenerate-page",
                json={
                    "page_id": "p1",
                    "page_title": "Overview",
                    "file_paths": [],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ok", "content": "# Overview"},
        )

    async def test_delete_repo_wiki_cache_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(id=repo_id, local_path="/data/repos/open-iscsi")
        meta = SimpleNamespace(repository_id=repo_id)
        self.holder["db"] = _FakeDB(
            execute_results=[_FakeResult(scalar_value=meta)],
            get_map={repo_id: repo},
        )

        with patch.object(
            repo_wiki_api,
            "_cache_owner_repo",
            return_value=("local", "open-iscsi"),
        ), patch.object(
            repo_wiki_api._orchestrator,
            "delete_cache",
            new=AsyncMock(),
        ) as delete_cache_mock:
            response = await self.client.delete(f"/api/repos/{repo_id}/wiki/cache")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "deleted"})
        self.assertEqual(self.holder["db"].deleted, [meta])
        self.assertEqual(self.holder["db"].commits, 1)
        delete_cache_mock.assert_awaited_once_with(owner="local", repo="open-iscsi")

    async def test_export_repo_wiki_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(id=repo_id, local_path="/data/repos/open-iscsi")
        cached = {
            "generated_pages": {
                "p1": {
                    "id": "p1",
                    "title": "Overview",
                    "content": "# Overview",
                    "filePaths": ["README.md"],
                    "importance": "high",
                    "relatedPages": ["p2"],
                }
            }
        }
        request = httpx.Request("POST", "http://deepwiki.test/export/wiki")
        fake_client = _FakeWikiExportClient(
            httpx.Response(
                200,
                request=request,
                content=b"# Overview",
                headers={"content-type": "text/markdown"},
            )
        )
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        with patch.object(
            repo_wiki_api,
            "_cache_owner_repo",
            return_value=("local", "open-iscsi"),
        ), patch.object(
            repo_wiki_api._orchestrator,
            "get_cached_wiki",
            new=AsyncMock(return_value=cached),
        ), patch.object(httpx, "AsyncClient", return_value=fake_client):
            response = await self.client.post(
                f"/api/repos/{repo_id}/wiki/export",
                json={"format": "markdown"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "# Overview")
        self.assertEqual(
            response.headers["content-disposition"],
            'attachment; filename="wiki.md"',
        )
        self.assertEqual(
            fake_client.calls,
            [
                (
                    "/export/wiki",
                    {
                        "repo_url": "local/local/open-iscsi",
                        "pages": [
                            {
                                "id": "p1",
                                "title": "Overview",
                                "content": "# Overview",
                                "filePaths": ["README.md"],
                                "importance": "high",
                                "relatedPages": ["p2"],
                            }
                        ],
                        "format": "markdown",
                    },
                )
            ],
        )

    async def test_export_repo_wiki_missing_cache_returns_404(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(id=repo_id, local_path="/data/repos/open-iscsi")
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        with patch.object(
            repo_wiki_api._orchestrator,
            "get_cached_wiki",
            new=AsyncMock(return_value=None),
        ):
            response = await self.client.post(
                f"/api/repos/{repo_id}/wiki/export",
                json={"format": "markdown"},
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "No wiki generated yet"})


if __name__ == "__main__":
    unittest.main()
