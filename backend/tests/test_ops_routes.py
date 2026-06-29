"""Route-level contracts for currently mounted ops/tool APIs."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.adapters.base import ToolCapability, ToolHealth
from app.api import tools as tools_api
from app.main import app


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


class _SlowAdapter(_FakeAdapter):
    async def health_check(self) -> ToolHealth:
        await asyncio.sleep(4.2)
        return ToolHealth(True, "running")


class OpsRouteContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_tools_list_contract(self) -> None:
        adapters = [
            _FakeAdapter(
                "gitnexus",
                [ToolCapability.CODE_SEARCH],
                ToolHealth(True, "running"),
            ),
            _FakeAdapter(
                "joern",
                [ToolCapability.CALL_GRAPH, ToolCapability.TAINT_ANALYSIS],
                error=RuntimeError("boom"),
            ),
        ]

        with patch.object(
            tools_api, "apply_persisted_agent_provider_settings", new_callable=AsyncMock
        ), patch.object(
            tools_api, "get_all_adapters", return_value=adapters
        ), patch.object(
            tools_api, "_runtime_external_agent_adapters", return_value=[]
        ):
            response = await self.client.get("/api/tools")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "name": "gitnexus",
                    "capabilities": ["code_search"],
                    "healthy": True,
                    "container_status": "running",
                },
                {
                    "name": "joern",
                    "capabilities": ["call_graph", "taint_analysis"],
                    "healthy": False,
                    "container_status": "error",
                },
            ],
        )

    async def test_tools_list_maps_slow_adapter_to_busy(self) -> None:
        adapters = [
            _FakeAdapter("gitnexus", [ToolCapability.CODE_SEARCH], ToolHealth(True, "running")),
            _SlowAdapter("joern", [ToolCapability.CALL_GRAPH]),
        ]

        with patch.object(
            tools_api, "apply_persisted_agent_provider_settings", new_callable=AsyncMock
        ), patch.object(
            tools_api, "get_all_adapters", return_value=adapters
        ), patch.object(
            tools_api, "_runtime_external_agent_adapters", return_value=[]
        ):
            response = await self.client.get("/api/tools")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload[1]["name"], "joern")
        self.assertTrue(payload[1]["healthy"])
        self.assertEqual(payload[1]["container_status"], "busy")

    async def test_tool_health_contract_includes_diagnostic_fields(self) -> None:
        adapter = _FakeAdapter(
            "semgrep",
            [ToolCapability.SECURITY_SCAN],
            ToolHealth(True, "running", version="2.1.3"),
        )

        with patch.object(tools_api, "get_adapter", return_value=adapter):
            response = await self.client.get("/api/tools/semgrep/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["name"], "semgrep")
        self.assertTrue(payload["healthy"])
        self.assertEqual(payload["container_status"], "running")
        self.assertEqual(payload["version"], "2.1.3")
        self.assertEqual(payload["message"], "2.1.3")
        self.assertEqual(payload["agent_provider"], {})


if __name__ == "__main__":
    unittest.main()
