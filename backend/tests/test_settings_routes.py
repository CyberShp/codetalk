import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.api import settings as settings_api
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
        self.flushes = 0

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

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        self.flushes += 1


class _FakeLLMAsyncClient:
    def __init__(self, *, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, path: str, headers=None, json=None):
        self.calls.append((path, headers, json))
        if self.error:
            raise self.error
        request = httpx.Request("POST", f"http://llm.test{path}")
        return httpx.Response(200, request=request, json=self.payload)


class SettingsRouteContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.holder = {"db": None}

        async def _fake_db():
            yield self.holder["db"]

        app.dependency_overrides[settings_api.get_db] = _fake_db
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()

    async def test_get_llm_configs_contract(self) -> None:
        cfg_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        config = SimpleNamespace(
            id=cfg_id,
            provider="custom",
            model_name="mimo-v2-pro",
            api_key_encrypted="enc",
            base_url="https://llm.example/v1",
            proxy_mode="system",
            is_default=True,
            created_at=now,
        )
        self.holder["db"] = _FakeDB(
            execute_results=[_FakeResult(items=[config])]
        )

        response = await self.client.get("/api/settings/llm")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "id": str(cfg_id),
                    "provider": "custom",
                    "model_name": "mimo-v2-pro",
                    "has_api_key": True,
                    "base_url": "https://llm.example/v1",
                    "proxy_mode": "system",
                    "is_default": True,
                    "created_at": now.isoformat().replace("+00:00", "Z"),
                }
            ],
        )

    async def test_save_llm_config_contract(self) -> None:
        self.holder["db"] = _FakeDB(execute_results=[_FakeResult(items=[])])

        response = await self.client.post(
            "/api/settings/llm",
            json={
                "provider": "custom",
                "model_name": "mimo-v2-pro",
                "api_key": "sk-test",
                "base_url": "https://llm.example/v1",
                "proxy_mode": "direct",
                "is_default": True,
            },
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["provider"], "custom")
        self.assertEqual(body["model_name"], "mimo-v2-pro")
        self.assertTrue(body["has_api_key"])
        self.assertEqual(body["base_url"], "https://llm.example/v1")
        self.assertEqual(body["proxy_mode"], "direct")
        self.assertTrue(body["is_default"])
        self.assertEqual(len(self.holder["db"].added), 1)
        self.assertEqual(self.holder["db"].commits, 1)
        self.assertEqual(self.holder["db"].refreshes, 1)

    async def test_update_llm_config_contract(self) -> None:
        cfg_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        config = SimpleNamespace(
            id=cfg_id,
            provider="custom",
            model_name="mimo-v1",
            api_key_encrypted="enc-old",
            base_url="https://old.example/v1",
            proxy_mode="system",
            is_default=False,
            created_at=now,
        )
        self.holder["db"] = _FakeDB(get_map={cfg_id: config})

        with patch.object(settings_api, "encrypt_key", return_value="enc-new"):
            response = await self.client.put(
                f"/api/settings/llm/{cfg_id}",
                json={
                    "provider": "openai",
                    "model_name": "gpt-5.4",
                    "api_key": "sk-updated",
                    "base_url": "",
                    "proxy_mode": "direct",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "id": str(cfg_id),
                "provider": "openai",
                "model_name": "gpt-5.4",
                "has_api_key": True,
                "base_url": None,
                "proxy_mode": "direct",
                "is_default": False,
                "created_at": now.isoformat().replace("+00:00", "Z"),
            },
        )
        self.assertEqual(config.api_key_encrypted, "enc-new")
        self.assertEqual(self.holder["db"].commits, 1)
        self.assertEqual(self.holder["db"].refreshes, 1)

    async def test_set_default_llm_config_contract(self) -> None:
        target_id = uuid.uuid4()
        old_default = SimpleNamespace(is_default=True)
        now = datetime.now(timezone.utc)
        config = SimpleNamespace(
            id=target_id,
            provider="custom",
            model_name="mimo-v2-pro",
            api_key_encrypted="enc",
            base_url="https://llm.example/v1",
            proxy_mode="system",
            is_default=False,
            created_at=now,
        )
        self.holder["db"] = _FakeDB(
            execute_results=[_FakeResult(items=[old_default])],
            get_map={target_id: config},
        )

        response = await self.client.patch(f"/api/settings/llm/{target_id}/default")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_default"])
        self.assertFalse(old_default.is_default)
        self.assertTrue(config.is_default)
        self.assertEqual(self.holder["db"].commits, 1)
        self.assertEqual(self.holder["db"].refreshes, 1)

    async def test_delete_llm_config_promotes_oldest_remaining_default(self) -> None:
        cfg_id = uuid.uuid4()
        config = SimpleNamespace(id=cfg_id, is_default=True)
        promoted = SimpleNamespace(is_default=False)
        self.holder["db"] = _FakeDB(
            execute_results=[_FakeResult(scalar_value=promoted)],
            get_map={cfg_id: config},
        )

        response = await self.client.delete(f"/api/settings/llm/{cfg_id}")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.holder["db"].deleted, [config])
        self.assertEqual(self.holder["db"].flushes, 1)
        self.assertEqual(self.holder["db"].commits, 1)
        self.assertTrue(promoted.is_default)

    async def test_update_llm_config_404_contract(self) -> None:
        cfg_id = uuid.uuid4()
        self.holder["db"] = _FakeDB(get_map={})

        response = await self.client.put(
            f"/api/settings/llm/{cfg_id}",
            json={"model_name": "gpt-5.4"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "LLM config not found"})

    async def test_test_llm_config_rejects_missing_api_key(self) -> None:
        cfg_id = uuid.uuid4()
        config = SimpleNamespace(
            id=cfg_id,
            provider="custom",
            model_name="mimo-v2-pro",
            api_key_encrypted=None,
            base_url="https://llm.example/v1",
            proxy_mode="system",
        )
        self.holder["db"] = _FakeDB(get_map={cfg_id: config})

        response = await self.client.post(f"/api/settings/llm/{cfg_id}/test")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"success": False, "message": "未设置 API Key"},
        )

    async def test_test_llm_config_rejects_missing_base_url(self) -> None:
        cfg_id = uuid.uuid4()
        config = SimpleNamespace(
            id=cfg_id,
            provider="custom",
            model_name="mimo-v2-pro",
            api_key_encrypted="enc-value",
            base_url=None,
            proxy_mode="system",
        )
        self.holder["db"] = _FakeDB(get_map={cfg_id: config})

        response = await self.client.post(f"/api/settings/llm/{cfg_id}/test")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"success": False, "message": "未设置 Base URL"},
        )

    async def test_test_llm_config_success_contract(self) -> None:
        cfg_id = uuid.uuid4()
        config = SimpleNamespace(
            id=cfg_id,
            provider="custom",
            model_name="mimo-v2-pro",
            api_key_encrypted="enc-value",
            base_url="https://llm.example/v1",
            proxy_mode="direct",
        )
        self.holder["db"] = _FakeDB(get_map={cfg_id: config})
        fake_client = _FakeLLMAsyncClient(
            payload={
                "choices": [
                    {
                        "message": {
                            "content": "pong from model"
                        }
                    }
                ]
            }
        )

        with patch.object(
            settings_api, "decrypt_key", return_value="sk-live"
        ), patch.object(
            settings_api.httpx, "AsyncClient", return_value=fake_client
        ):
            response = await self.client.post(
                f"/api/settings/llm/{cfg_id}/test"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"success": True, "message": "pong from model"},
        )
        self.assertEqual(len(fake_client.calls), 1)
        path, headers, payload = fake_client.calls[0]
        self.assertEqual(path, "/chat/completions")
        self.assertEqual(headers, {"Authorization": "Bearer sk-live"})
        self.assertEqual(payload["model"], "mimo-v2-pro")


if __name__ == "__main__":
    unittest.main()
