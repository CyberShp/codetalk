import unittest
import asyncio
from contextlib import ExitStack
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.adapters.base import ToolCapability, ToolHealth
from app.api import components as components_api
from app.api import settings as settings_api
from app.api import tools as tools_api
from app.main import app
from app.schemas.component_config import (
    ComponentContract,
    ConfigDomain,
    ConfigField,
)


class _FakeAdapter:
    def __init__(
        self,
        name: str,
        capabilities: list[ToolCapability],
        health: ToolHealth | None = None,
        error: Exception | None = None,
    ) -> None:
        self._name = name
        self._capabilities = capabilities
        self._health = health
        self._error = error

    def name(self) -> str:
        return self._name

    def capabilities(self) -> list[ToolCapability]:
        return self._capabilities

    async def health_check(self) -> ToolHealth:
        if self._error:
            raise self._error
        assert self._health is not None
        return self._health


class _SlowBusyJoernAdapter(_FakeAdapter):
    async def health_check(self) -> ToolHealth:
        await asyncio.sleep(3.2)
        return ToolHealth(True, "busy")


class _BudgetTimedOutJoernAdapter(_FakeAdapter):
    async def health_check(self) -> ToolHealth:
        await asyncio.sleep(4.2)
        return ToolHealth(True, "running")


async def _fake_db():
    yield object()


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://testserver/models/config")
            response = httpx.Response(
                self.status_code,
                request=request,
                json=self._payload,
            )
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=request,
                response=response,
            )


class _FakeAsyncClient:
    def __init__(
        self,
        response: _FakeResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, path: str):
        if self.error:
            raise self.error
        assert self.response is not None
        return self.response


class OpsRouteContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        app.dependency_overrides[components_api.get_db] = _fake_db
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()

    async def test_tools_list_contract(self) -> None:
        adapters = [
            _FakeAdapter(
                "deepwiki",
                [
                    ToolCapability.DOCUMENTATION,
                    ToolCapability.KNOWLEDGE_GRAPH,
                ],
                ToolHealth(True, "running", version="1.0.0"),
            ),
            _FakeAdapter(
                "joern",
                [
                    ToolCapability.CALL_GRAPH,
                    ToolCapability.TAINT_ANALYSIS,
                ],
                error=RuntimeError("boom"),
            ),
        ]

        with patch.object(tools_api, "get_all_adapters", return_value=adapters):
            response = await self.client.get("/api/tools")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload,
            [
                {
                    "name": "deepwiki",
                    "capabilities": [
                        "documentation",
                        "knowledge_graph",
                    ],
                    "healthy": True,
                    "container_status": "running",
                },
                {
                    "name": "joern",
                    "capabilities": [
                        "call_graph",
                        "taint_analysis",
                    ],
                    "healthy": False,
                    "container_status": "error",
                },
            ],
        )

    async def test_tools_list_allows_busy_joern_to_surface(self) -> None:
        adapters = [
            _FakeAdapter(
                "deepwiki",
                [ToolCapability.DOCUMENTATION],
                ToolHealth(True, "running", version="1.0.0"),
            ),
            _SlowBusyJoernAdapter(
                "joern",
                [ToolCapability.CALL_GRAPH, ToolCapability.TAINT_ANALYSIS],
            ),
        ]

        with patch.object(tools_api, "get_all_adapters", return_value=adapters):
            response = await self.client.get("/api/tools")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload[1]["name"], "joern")
        self.assertTrue(payload[1]["healthy"])
        self.assertEqual(payload[1]["container_status"], "busy")

    async def test_tools_list_maps_joern_budget_timeout_to_busy(self) -> None:
        adapters = [
            _FakeAdapter(
                "deepwiki",
                [ToolCapability.DOCUMENTATION],
                ToolHealth(True, "running", version="1.0.0"),
            ),
            _BudgetTimedOutJoernAdapter(
                "joern",
                [ToolCapability.CALL_GRAPH, ToolCapability.TAINT_ANALYSIS],
            ),
        ]

        with patch.object(tools_api, "get_all_adapters", return_value=adapters):
            response = await self.client.get("/api/tools")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload[1]["name"], "joern")
        self.assertTrue(payload[1]["healthy"])
        self.assertEqual(payload[1]["container_status"], "busy")

    async def test_tool_health_contract(self) -> None:
        adapter = _FakeAdapter(
            "semgrep",
            [ToolCapability.SECURITY_SCAN],
            ToolHealth(True, "running", version="2.1.3"),
        )

        with patch.object(tools_api, "get_adapter", return_value=adapter):
            response = await self.client.get("/api/tools/semgrep/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "name": "semgrep",
                "healthy": True,
                "container_status": "running",
                "version": "2.1.3",
            },
        )

    async def test_components_list_contract(self) -> None:
        now = datetime.now(timezone.utc)
        contract = ComponentContract(
            component="deepwiki",
            label="DeepWiki",
            domains=[
                ConfigDomain(
                    domain="chat",
                    label="Chat 模型",
                    env_map={"base_url": "OPENAI_BASE_URL"},
                    fields=[
                        ConfigField(
                            name="base_url",
                            label="Base URL",
                            field_type="url",
                        )
                    ],
                )
            ],
        )
        stored_cfg = SimpleNamespace(
            component="deepwiki",
            domain="chat",
            config={"base_url": "http://example.test", "api_key": "enc"},
            applied_at=now,
            updated_at=now,
        )
        adapters = [
            _FakeAdapter(
                "deepwiki",
                [ToolCapability.DOCUMENTATION],
                ToolHealth(True, "running", version="2026.04"),
            )
        ]

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    components_api,
                    "get_all_adapters",
                    return_value=adapters,
                )
            )
            stack.enter_context(
                patch.object(
                    components_api.cm,
                    "get_contracts",
                    return_value=[contract],
                )
            )
            stack.enter_context(
                patch.object(
                    components_api.cm,
                    "get_all_configs",
                    return_value=[stored_cfg],
                )
            )
            stack.enter_context(
                patch.object(
                    components_api.cm,
                    "config_to_display",
                    return_value={
                        "base_url": "http://example.test",
                        "api_key": "••••••••",
                    },
                )
            )
            response = await self.client.get("/api/components")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["component"], "deepwiki")
        self.assertTrue(payload[0]["health"]["healthy"])
        self.assertEqual(payload[0]["health"]["version"], "2026.04")
        self.assertEqual(payload[0]["domains"][0]["domain"], "chat")
        self.assertEqual(
            payload[0]["domains"][0]["config"]["api_key"],
            "••••••••",
        )

    async def test_components_contracts_contract(self) -> None:
        contract = ComponentContract(
            component="deepwiki",
            label="DeepWiki",
            domains=[
                ConfigDomain(
                    domain="chat",
                    label="Chat 模型",
                    env_map={"base_url": "OPENAI_BASE_URL"},
                    fields=[
                        ConfigField(
                            name="base_url",
                            label="Base URL",
                            field_type="url",
                        )
                    ],
                )
            ],
        )

        with patch.object(
            components_api.cm, "get_contracts", return_value=[contract]
        ):
            response = await self.client.get("/api/components/contracts")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "component": "deepwiki",
                    "label": "DeepWiki",
                    "domains": [
                        {
                            "domain": "chat",
                            "label": "Chat 模型",
                            "fields": [
                                {
                                    "name": "base_url",
                                    "label": "Base URL",
                                    "field_type": "url",
                                    "options": None,
                                    "placeholder": None,
                                }
                            ],
                            "env_map": {
                                "base_url": "OPENAI_BASE_URL",
                            },
                        }
                    ],
                }
            ],
        )

    async def test_component_health_contract_via_adapter(self) -> None:
        adapters = [
            _FakeAdapter(
                "joern",
                [ToolCapability.CALL_GRAPH],
                ToolHealth(False, "timeout"),
            )
        ]

        with patch.object(
            components_api, "get_all_adapters", return_value=adapters
        ):
            response = await self.client.get("/api/components/joern/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "component": "joern",
                "healthy": False,
                "container_status": "timeout",
                "version": None,
            },
        )

    async def test_component_health_contract_via_container_manager(self) -> None:
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    components_api,
                    "get_all_adapters",
                    return_value=[],
                )
            )
            stack.enter_context(
                patch.object(
                    components_api.cm,
                    "get_container_status",
                    return_value=(True, "running"),
                )
            )
            response = await self.client.get(
                "/api/components/codecompass/health"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "component": "codecompass",
                "healthy": True,
                "container_status": "running",
                "version": None,
            },
        )

    async def test_component_restart_contract_for_known_component(self) -> None:
        contract = ComponentContract(
            component="joern",
            label="Joern",
            domains=[],
        )

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    components_api.cm,
                    "get_contract",
                    return_value=contract,
                )
            )
            stack.enter_context(
                patch.object(
                    components_api.cm,
                    "restart_container",
                    return_value=(
                        True,
                        "容器 codetalk-joern-1 已重启",
                    ),
                )
            )
            response = await self.client.post("/api/components/joern/restart")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "success": True,
                "message": "容器 codetalk-joern-1 已重启",
            },
        )

    async def test_component_apply_contract_for_known_component(self) -> None:
        contract = ComponentContract(
            component="deepwiki",
            label="DeepWiki",
            domains=[],
        )

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    components_api.cm,
                    "get_contract",
                    return_value=contract,
                )
            )
            stack.enter_context(
                patch.object(
                    components_api.cm,
                    "apply_config",
                    return_value=(
                        True,
                        "配置已写入 override 文件",
                        {"OPENAI_API_KEY": "sk-12••••"},
                    ),
                )
            )
            response = await self.client.post("/api/components/deepwiki/apply")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "success": True,
                "message": "配置已写入 override 文件",
                "override_preview": {
                    "OPENAI_API_KEY": "sk-12••••",
                },
            },
        )

    async def test_backend_restart_returns_self_restart_refusal(self) -> None:
        response = await self.client.post("/api/components/backend/restart")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertIn("无法自行重启", body["message"])

    async def test_backend_apply_restart_returns_self_restart_refusal(self) -> None:
        response = await self.client.post("/api/components/backend/apply-restart")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertIn("无法自行重启", body["message"])

    async def test_deepwiki_models_proxy_success_contract(self) -> None:
        payload = {"providers": [{"name": "openai", "models": ["gpt-4.1"]}]}
        fake_client = _FakeAsyncClient(response=_FakeResponse(payload))

        with patch.object(
            settings_api.httpx, "AsyncClient", return_value=fake_client
        ):
            response = await self.client.get("/api/settings/deepwiki/models")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), payload)

    async def test_deepwiki_models_proxy_connect_error_maps_to_502(self) -> None:
        fake_client = _FakeAsyncClient(
            error=httpx.ConnectError("boom", request=httpx.Request("GET", "http://deepwiki/models/config"))
        )

        with patch.object(
            settings_api.httpx, "AsyncClient", return_value=fake_client
        ):
            response = await self.client.get("/api/settings/deepwiki/models")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json(),
            {"detail": "Cannot connect to deepwiki service"},
        )


if __name__ == "__main__":
    unittest.main()
