import unittest
from unittest.mock import AsyncMock, call, patch

import httpx

from app.adapters.base import AnalysisRequest
from app.adapters.joern import JoernAdapter


class JoernAdapterHealthCheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_check_treats_query_timeout_as_busy(self) -> None:
        adapter = JoernAdapter(base_url="http://joern:8080")

        with patch.object(
            adapter,
            "_query",
            AsyncMock(side_effect=httpx.ReadTimeout("timed out")),
        ):
            health = await adapter.health_check()

        self.assertTrue(health.is_healthy)
        self.assertEqual(health.container_status, "busy")


class JoernAdapterPrepareTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        JoernAdapter._loaded_project_by_base_url.clear()
        JoernAdapter._prepare_locks.clear()

    def tearDown(self) -> None:
        JoernAdapter._loaded_project_by_base_url.clear()
        JoernAdapter._prepare_locks.clear()

    async def test_prepare_reuses_loaded_cpg_across_fresh_instances(self) -> None:
        request = AnalysisRequest(repo_local_path="/tmp/repos/open-iscsi")
        first = JoernAdapter(base_url="http://joern:8080")
        second = JoernAdapter(base_url="http://joern:8080")

        with patch.object(first, "_query", AsyncMock(return_value=None)) as first_query:
            await first.prepare(request)

        with patch.object(second, "_query", AsyncMock(return_value=42)) as second_query:
            await second.prepare(request)

        first_query.assert_awaited_once_with(
            'importCode("/tmp/repos/open-iscsi", "open-iscsi")',
            timeout=600,
        )
        second_query.assert_awaited_once_with("cpg.method.size")
        self.assertEqual(second._imported_project, "open-iscsi")

    async def test_prepare_reimports_when_shared_cache_is_stale(self) -> None:
        request = AnalysisRequest(repo_local_path="/tmp/repos/open-iscsi")
        adapter = JoernAdapter(base_url="http://joern:8080")
        JoernAdapter._loaded_project_by_base_url[adapter.base_url] = "open-iscsi"

        with patch.object(
            adapter,
            "_query",
            AsyncMock(side_effect=[RuntimeError("stale"), None]),
        ) as query:
            await adapter.prepare(request)

        query.assert_has_awaits(
            [
                call("cpg.method.size"),
                call('importCode("/tmp/repos/open-iscsi", "open-iscsi")', timeout=600),
            ]
        )
        self.assertEqual(adapter._imported_project, "open-iscsi")
