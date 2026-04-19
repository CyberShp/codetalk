import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.api import repo_chat as repo_chat_api
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

    async def refresh(self, obj):
        self.refreshes += 1
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = now
        if getattr(obj, "updated_at", None) is None:
            obj.updated_at = now

    async def delete(self, obj):
        self.deleted.append(obj)

    async def close(self):
        return None


class RepoChatRouteContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.holder = {"db": None}

        async def _fake_db():
            yield self.holder["db"]

        app.dependency_overrides[repo_chat_api.get_db] = _fake_db
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()

    async def test_list_chat_sessions_contract(self) -> None:
        repo_id = uuid.uuid4()
        session_id = uuid.uuid4()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session = SimpleNamespace(
            id=session_id,
            repo_id=repo_id,
            title="Discuss graph",
            created_at=now,
            updated_at=now,
        )
        self.holder["db"] = _FakeDB(
            execute_results=[_FakeResult(items=[session])]
        )

        response = await self.client.get(f"/api/repos/{repo_id}/chat/sessions")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "id": str(session_id),
                    "repo_id": str(repo_id),
                    "title": "Discuss graph",
                    "messages": [],
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                }
            ],
        )

    async def test_create_chat_session_auto_title_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(id=repo_id)
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        response = await self.client.post(
            f"/api/repos/{repo_id}/chat/sessions",
            json={
                "messages": [
                    {"role": "user", "content": "Explain the graph loading path"}
                ]
            },
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["repo_id"], str(repo_id))
        self.assertEqual(body["title"], "Explain the graph loading path")
        self.assertEqual(
            body["messages"],
            [{"role": "user", "content": "Explain the graph loading path"}],
        )
        self.assertEqual(len(self.holder["db"].added), 1)
        self.assertEqual(self.holder["db"].commits, 1)
        self.assertEqual(self.holder["db"].refreshes, 1)

    async def test_get_chat_session_404_contract(self) -> None:
        repo_id = uuid.uuid4()
        session_id = uuid.uuid4()
        self.holder["db"] = _FakeDB(get_map={})

        response = await self.client.get(
            f"/api/repos/{repo_id}/chat/sessions/{session_id}"
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Session not found"})

    async def test_get_chat_session_contract(self) -> None:
        repo_id = uuid.uuid4()
        session_id = uuid.uuid4()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session = SimpleNamespace(
            id=session_id,
            repo_id=repo_id,
            title="Investigate restore flow",
            messages=[{"role": "user", "content": "why did graph vanish?"}],
            created_at=now,
            updated_at=now,
        )
        self.holder["db"] = _FakeDB(get_map={session_id: session})

        response = await self.client.get(
            f"/api/repos/{repo_id}/chat/sessions/{session_id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "id": str(session_id),
                "repo_id": str(repo_id),
                "title": "Investigate restore flow",
                "messages": [{"role": "user", "content": "why did graph vanish?"}],
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            },
        )

    async def test_update_chat_session_contract(self) -> None:
        repo_id = uuid.uuid4()
        session_id = uuid.uuid4()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session = SimpleNamespace(
            id=session_id,
            repo_id=repo_id,
            title="Old title",
            messages=[{"role": "user", "content": "old"}],
            created_at=now,
            updated_at=now,
        )
        self.holder["db"] = _FakeDB(get_map={session_id: session})

        with patch.object(repo_chat_api, "flag_modified") as flag_modified_mock:
            response = await self.client.put(
                f"/api/repos/{repo_id}/chat/sessions/{session_id}",
                json={
                    "title": "New title",
                    "messages": [{"role": "assistant", "content": "updated"}],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["title"], "New title")
        self.assertEqual(
            response.json()["messages"],
            [{"role": "assistant", "content": "updated"}],
        )
        self.assertEqual(self.holder["db"].commits, 1)
        self.assertEqual(self.holder["db"].refreshes, 1)
        flag_modified_mock.assert_called_once_with(session, "messages")

    async def test_delete_chat_session_contract(self) -> None:
        repo_id = uuid.uuid4()
        session_id = uuid.uuid4()
        session = SimpleNamespace(id=session_id, repo_id=repo_id)
        self.holder["db"] = _FakeDB(get_map={session_id: session})

        response = await self.client.delete(
            f"/api/repos/{repo_id}/chat/sessions/{session_id}"
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.holder["db"].deleted, [session])
        self.assertEqual(self.holder["db"].commits, 1)

    async def test_repo_chat_stream_unsynced_repo_contract(self) -> None:
        repo_id = uuid.uuid4()
        repo = SimpleNamespace(id=repo_id, local_path=None)
        self.holder["db"] = _FakeDB(get_map={repo_id: repo})

        response = await self.client.post(
            f"/api/repos/{repo_id}/chat/stream",
            json={
                "repo_id": str(repo_id),
                "messages": [{"role": "user", "content": "hello"}],
                "deep_research": False,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "Repository not synced"})


if __name__ == "__main__":
    unittest.main()
