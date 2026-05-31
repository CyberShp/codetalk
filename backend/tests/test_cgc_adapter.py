"""Unit tests for the CGC adapter (mock httpx — no live daemon required)."""

import asyncio
import unittest
import unittest.mock
from pathlib import Path

import httpx

from app.adapters.cgc import (
    CGCAdapter,
    CGCCLIClient,
    CGCClient,
    CGCIndexFailed,
    CGCQueryError,
    CGCUnavailable,
    _complexity_value,
)


# ---------------------------------------------------------------------------
# Fake HTTP infrastructure
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | list):
        self.status_code = status_code
        self._payload = payload

    @property
    def is_error(self) -> bool:
        return self.status_code >= 400

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, get_responses=None, post_responses=None):
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])
        self.get_calls: list[tuple] = []
        self.post_calls: list[tuple] = []
        self.is_closed = False

    async def get(self, path: str, **kwargs):
        self.get_calls.append((path, kwargs))
        assert self.get_responses, f"unexpected GET {path}"
        return self.get_responses.pop(0)

    async def post(self, path: str, json=None, **kwargs):
        self.post_calls.append((path, json))
        assert self.post_responses, f"unexpected POST {path}"
        resp = self.post_responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def _ok(data) -> _FakeResponse:
    return _FakeResponse(200, {"status": "ok", "data": data})


def _err(message: str, status_code: int = 200) -> _FakeResponse:
    return _FakeResponse(status_code, {"status": "error", "error": message})


# ---------------------------------------------------------------------------
# is_healthy tests
# ---------------------------------------------------------------------------


class IsHealthyTests(unittest.IsolatedAsyncioTestCase):
    async def test_healthy_when_status_ok(self):
        client = CGCClient(base_url="http://cgc:7072")
        client._client = _FakeAsyncClient(
            get_responses=[_FakeResponse(200, {"status": "ok", "message": "Connected"})]
        )
        self.assertTrue(await client.is_healthy())

    async def test_unhealthy_when_status_not_ok(self):
        client = CGCClient(base_url="http://cgc:7072")
        client._client = _FakeAsyncClient(
            get_responses=[_FakeResponse(200, {"status": "error"})]
        )
        self.assertFalse(await client.is_healthy())

    async def test_unhealthy_when_network_down(self):
        client = CGCClient(base_url="http://cgc:7072")

        async def _raise(*_a, **_kw):
            raise httpx.ConnectError("connection refused")

        client._client = _FakeAsyncClient()
        client._client.get = _raise
        self.assertFalse(await client.is_healthy())


# ---------------------------------------------------------------------------
# _call_tool tests
# ---------------------------------------------------------------------------


class CallToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_data_on_success(self):
        client = CGCClient(base_url="http://cgc:7072")
        client._client = _FakeAsyncClient(
            post_responses=[_ok({"results": [{"name": "foo"}]})]
        )
        result = await client._call_tool("find_code", {"query": "foo"})
        self.assertEqual(result, [{"name": "foo"}])

    async def test_raises_cgc_query_error_on_error_response(self):
        client = CGCClient(base_url="http://cgc:7072")
        client._client = _FakeAsyncClient(
            post_responses=[_err("tool not found")]
        )
        with self.assertRaises(CGCQueryError):
            await client._call_tool("bad_tool", {})

    async def test_raises_cgc_unavailable_on_network_error(self):
        client = CGCClient(base_url="http://cgc:7072")

        async def _raise(*_a, **_kw):
            raise httpx.RequestError("refused")

        client._client = _FakeAsyncClient()
        client._client.post = _raise

        with self.assertRaises(CGCUnavailable):
            await client._call_tool("find_code", {"query": "x"})

    async def test_raises_cgc_query_error_on_http_error_status(self):
        client = CGCClient(base_url="http://cgc:7072")
        client._client = _FakeAsyncClient(
            post_responses=[_FakeResponse(500, {"status": "ok"})]
        )
        with self.assertRaises(CGCQueryError):
            await client._call_tool("find_code", {"query": "x"})


# ---------------------------------------------------------------------------
# index_repo tests
# ---------------------------------------------------------------------------


class IndexRepoTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_job_id(self):
        client = CGCClient(base_url="http://cgc:7072")
        client._client = _FakeAsyncClient(
            post_responses=[_ok({"job_id": "job-abc-123"})]
        )
        job_id = await client.index_repo("/tmp/myrepo")
        self.assertEqual(job_id, "job-abc-123")

    async def test_raises_when_no_job_id_returned(self):
        client = CGCClient(base_url="http://cgc:7072")
        client._client = _FakeAsyncClient(
            post_responses=[_ok({"message": "started"})]
        )
        with self.assertRaises(CGCQueryError):
            await client.index_repo("/tmp/myrepo")

    async def test_passes_repo_name_when_provided(self):
        client = CGCClient(base_url="http://cgc:7072")
        client._client = _FakeAsyncClient(post_responses=[_ok({"job_id": "job-abc"})])
        await client.index_repo("/tmp/myrepo", repo_name="my-project")
        _, body = client._client.post_calls[0]
        self.assertEqual(body["arguments"]["repo_name"], "my-project")

    async def test_omits_repo_name_when_not_provided(self):
        client = CGCClient(base_url="http://cgc:7072")
        client._client = _FakeAsyncClient(post_responses=[_ok({"job_id": "job-abc"})])
        await client.index_repo("/tmp/myrepo")
        _, body = client._client.post_calls[0]
        self.assertNotIn("repo_name", body["arguments"])


# ---------------------------------------------------------------------------
# wait_for_index tests
# ---------------------------------------------------------------------------


class WaitForIndexTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_true_when_completed(self):
        client = CGCClient(base_url="http://cgc:7072")
        client._client = _FakeAsyncClient(
            post_responses=[_ok({"status": "completed"})]
        )
        with unittest.mock.patch("app.adapters.cgc._INDEX_POLL_INTERVAL", 0):
            result = await client.wait_for_index("job-1")
        self.assertTrue(result)

    async def test_raises_cgc_index_failed_on_failure(self):
        client = CGCClient(base_url="http://cgc:7072")
        client._client = _FakeAsyncClient(
            post_responses=[_ok({"status": "failed", "error": "parse error"})]
        )
        with unittest.mock.patch("app.adapters.cgc._INDEX_POLL_INTERVAL", 0):
            with self.assertRaises(CGCIndexFailed):
                await client.wait_for_index("job-1")

    async def test_raises_timeout_when_never_completes(self):
        client = CGCClient(base_url="http://cgc:7072")
        # Infinite "running" responses — timeout=0 means immediate failure after 1 poll
        client._client = _FakeAsyncClient(
            post_responses=[_ok({"status": "running"})] * 10
        )
        with unittest.mock.patch("app.adapters.cgc._INDEX_POLL_INTERVAL", 0):
            with self.assertRaises(asyncio.TimeoutError):
                await client.wait_for_index("job-1", timeout=0)

    async def test_returns_true_for_real_cgc_response_shape(self):
        """Real CGC: {"success": True, "job": {"status": "completed", ...}}"""
        client = CGCClient(base_url="http://cgc:7072")
        client._client = _FakeAsyncClient(
            post_responses=[_ok({"success": True, "job": {"status": "completed"}})]
        )
        with unittest.mock.patch("app.adapters.cgc._INDEX_POLL_INTERVAL", 0):
            result = await client.wait_for_index("job-real")
        self.assertTrue(result)

    async def test_raises_index_failed_for_real_cgc_failed_response(self):
        """Real CGC failed: {"success": True, "job": {"status": "failed", "error": "..."}}"""
        client = CGCClient(base_url="http://cgc:7072")
        client._client = _FakeAsyncClient(
            post_responses=[_ok({"success": True, "job": {"status": "failed", "error": "index error"}})]
        )
        with unittest.mock.patch("app.adapters.cgc._INDEX_POLL_INTERVAL", 0):
            with self.assertRaises(CGCIndexFailed) as cm:
                await client.wait_for_index("job-real")
        self.assertIn("index error", str(cm.exception))


# ---------------------------------------------------------------------------
# find_callers / find_callees tests
# ---------------------------------------------------------------------------


class RelationshipQueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_find_callers_happy_path(self):
        client = CGCClient(base_url="http://cgc:7072")
        callers = [{"name": "caller_a", "path": "app/main.py"}]
        client._client = _FakeAsyncClient(
            post_responses=[_ok(callers)]
        )
        result = await client.find_callers("my_func", repo_path="/repo")
        self.assertEqual(result, callers)
        _, body = client._client.post_calls[0]
        self.assertEqual(body["arguments"]["query_type"], "find_callers")
        self.assertEqual(body["arguments"]["target"], "my_func")

    async def test_find_callees_happy_path(self):
        client = CGCClient(base_url="http://cgc:7072")
        callees = [{"name": "helper", "path": "app/utils.py"}]
        client._client = _FakeAsyncClient(
            post_responses=[_ok(callees)]
        )
        result = await client.find_callees("my_func")
        self.assertEqual(result, callees)

    async def test_find_callers_propagates_query_error(self):
        client = CGCClient(base_url="http://cgc:7072")
        client._client = _FakeAsyncClient(
            post_responses=[_err("function not found")]
        )
        with self.assertRaises(CGCQueryError):
            await client.find_callers("nonexistent_func")

    async def test_find_callers_real_cgc_response_shape(self):
        """Real CGC wraps: {"success": True, "query_type": ..., "results": [...]}"""
        client = CGCClient(base_url="http://cgc:7072")
        callers = [{"name": "caller_a", "path": "app/main.py"}]
        client._client = _FakeAsyncClient(
            post_responses=[_ok({
                "success": True, "query_type": "find_callers",
                "target": "my_func", "context": None, "results": callers,
            })]
        )
        result = await client.find_callers("my_func", repo_path="/repo")
        self.assertEqual(result, callers)

    async def test_find_callers_passes_depth_to_cgc(self):
        client = CGCClient(base_url="http://cgc:7072")
        client._client = _FakeAsyncClient(post_responses=[_ok([])])
        await client.find_callers("my_func", depth=3)
        _, body = client._client.post_calls[0]
        self.assertEqual(body["arguments"]["depth"], 3)


# ---------------------------------------------------------------------------
# call_chain tests
# ---------------------------------------------------------------------------


class CallChainTests(unittest.IsolatedAsyncioTestCase):
    async def test_combines_from_to_as_arrow_target(self):
        """call_chain(from_func, to_func) passes 'from_func->to_func' as target (CGC arrow format)."""
        client = CGCClient(base_url="http://cgc:7072")
        chain_data = [{"from": "func_a", "to": "func_b"}]
        client._client = _FakeAsyncClient(post_responses=[_ok(chain_data)])
        result = await client.call_chain("func_a", "func_b", repo_path="/repo")
        self.assertEqual(result, {"chain": chain_data})
        _, body = client._client.post_calls[0]
        self.assertEqual(body["arguments"]["target"], "func_a->func_b")
        self.assertEqual(body["arguments"]["query_type"], "call_chain")


# ---------------------------------------------------------------------------
# find_code tests
# ---------------------------------------------------------------------------


class FindCodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_happy_path_returns_list(self):
        client = CGCClient(base_url="http://cgc:7072")
        snippets = [{"name": "parse_config", "path": "config.py", "line": 42}]
        client._client = _FakeAsyncClient(
            post_responses=[_ok(snippets)]
        )
        result = await client.find_code("parse_config", repo_path="/repo")
        self.assertEqual(result, snippets)

    async def test_network_error_raises_unavailable(self):
        client = CGCClient(base_url="http://cgc:7072")

        async def _raise(*_a, **_kw):
            raise httpx.RequestError("refused")

        client._client = _FakeAsyncClient()
        client._client.post = _raise

        with self.assertRaises(CGCUnavailable):
            await client.find_code("something")


# ---------------------------------------------------------------------------
# find_complexity tests
# ---------------------------------------------------------------------------


class FindComplexityTests(unittest.IsolatedAsyncioTestCase):
    async def test_filters_by_threshold(self):
        client = CGCClient(base_url="http://cgc:7072")
        raw = [
            {"name": "simple", "cyclomatic_complexity": 3},
            {"name": "complex", "cyclomatic_complexity": 15},
            {"name": "medium", "cyclomatic_complexity": 10},
        ]
        client._client = _FakeAsyncClient(post_responses=[_ok(raw)])
        result = await client.find_complexity(repo_path="/repo", threshold=10)
        names = [r["name"] for r in result]
        self.assertIn("complex", names)
        self.assertIn("medium", names)
        self.assertNotIn("simple", names)


# ---------------------------------------------------------------------------
# _complexity_value helper tests
# ---------------------------------------------------------------------------


class ComplexityValueTests(unittest.TestCase):
    def test_reads_cyclomatic_complexity_key(self):
        self.assertEqual(_complexity_value({"cyclomatic_complexity": 7}), 7)

    def test_reads_complexity_key(self):
        self.assertEqual(_complexity_value({"complexity": 5}), 5)

    def test_returns_zero_for_missing_key(self):
        self.assertEqual(_complexity_value({"name": "foo"}), 0)

    def test_handles_float(self):
        self.assertEqual(_complexity_value({"complexity": 8.9}), 8)


class CGCAdapterHealthTrackingTests(unittest.IsolatedAsyncioTestCase):
    """health_check() reflects real indexed_count and last_index_error state."""

    def setUp(self) -> None:
        from app.adapters.cgc import CGCAdapter
        CGCAdapter._indexed_count = 0
        CGCAdapter._last_index_error = None
        CGCAdapter._prepare_inflight.clear()

    def tearDown(self) -> None:
        from app.adapters.cgc import CGCAdapter
        CGCAdapter._indexed_count = 0
        CGCAdapter._last_index_error = None
        CGCAdapter._prepare_inflight.clear()

    async def test_health_check_reports_zero_before_prepare(self) -> None:
        from app.adapters.cgc import CGCAdapter
        adapter = CGCAdapter(base_url="http://cgc:7072")
        adapter._cgc.is_healthy = unittest.mock.AsyncMock(return_value=True)
        health = await adapter.health_check()
        self.assertEqual(health.indexed_repos, 0)
        self.assertIsNone(health.last_index_error)

    async def test_indexed_count_increments_on_successful_prepare(self) -> None:
        from app.adapters.cgc import CGCAdapter
        from app.adapters.base import AnalysisRequest

        adapter = CGCAdapter(base_url="http://cgc:7072")
        adapter._cgc.index_repo = unittest.mock.AsyncMock(return_value="job-1")
        adapter._cgc.wait_for_index = unittest.mock.AsyncMock(return_value=True)
        adapter._cgc.is_healthy = unittest.mock.AsyncMock(return_value=True)

        await adapter.prepare(AnalysisRequest(repo_local_path="/repo/x"))
        health = await adapter.health_check()
        self.assertEqual(health.indexed_repos, 1)
        self.assertIsNone(health.last_index_error)

    async def test_last_index_error_set_on_failed_prepare(self) -> None:
        from app.adapters.cgc import CGCAdapter, CGCIndexFailed
        from app.adapters.base import AnalysisRequest

        adapter = CGCAdapter(base_url="http://cgc:7072")
        adapter._cgc.index_repo = unittest.mock.AsyncMock(return_value="job-err")
        adapter._cgc.wait_for_index = unittest.mock.AsyncMock(
            side_effect=CGCIndexFailed("indexing failed")
        )
        adapter._cgc.is_healthy = unittest.mock.AsyncMock(return_value=True)
        adapter._cli.index_repo = unittest.mock.AsyncMock(
            side_effect=CGCIndexFailed("cli indexing failed")
        )

        with self.assertRaises(CGCIndexFailed):
            await adapter.prepare(AnalysisRequest(repo_local_path="/repo/y"))

        health = await adapter.health_check()
        self.assertEqual(health.indexed_repos, 0)
        self.assertIsNotNone(health.last_index_error)
        self.assertIn("cli indexing failed", health.last_index_error)


class CGCCLIFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_cli_index_uses_codegraphcontext_index(self) -> None:
        calls: list[list[str]] = []

        def _run(cmd, **kwargs):
            calls.append(list(cmd))
            return unittest.mock.Mock(returncode=0, stdout="Successfully finished indexing", stderr="")

        cli = CGCCLIClient(python_exe="python-test", run=_run)
        await cli.index_repo(r"E:\repo\spdk")

        self.assertEqual(
            calls[0],
            ["python-test", "-m", "codegraphcontext", "index", str(Path(r"E:\repo\spdk"))],
        )

    async def test_adapter_prepare_falls_back_to_cli_when_gateway_unavailable(self) -> None:
        from app.adapters.base import AnalysisRequest

        adapter = CGCAdapter(base_url="http://cgc-unavailable:7072")
        adapter._cgc.index_repo = unittest.mock.AsyncMock(side_effect=CGCUnavailable("down"))
        adapter._cgc.is_healthy = unittest.mock.AsyncMock(return_value=False)

        cli = unittest.mock.MagicMock()
        cli.index_repo = unittest.mock.AsyncMock(return_value="cli-index")
        cli.is_healthy = unittest.mock.AsyncMock(return_value=True)
        adapter._cli = cli

        await adapter.prepare(AnalysisRequest(repo_local_path=r"E:\repo\spdk"))

        cli.index_repo.assert_awaited_once_with(r"E:\repo\spdk")
        self.assertIs(adapter._cgc, cli)
        health = await adapter.health_check()
        self.assertTrue(health.is_healthy)

    async def test_prepare_indexes_each_scope_path_when_provided(self) -> None:
        from app.adapters.base import AnalysisRequest

        adapter = CGCAdapter(base_url="http://cgc:7072")
        adapter._cgc.index_repo = unittest.mock.AsyncMock(return_value="job-1")
        adapter._cgc.wait_for_index = unittest.mock.AsyncMock(return_value=True)

        await adapter.prepare(
            AnalysisRequest(
                repo_local_path=r"E:\repo\spdk",
                options={
                    "cgc_index_paths": [
                        r"E:\repo\spdk\lib\log",
                        r"E:\repo\spdk\include\spdk",
                    ]
                },
            )
        )

        adapter._cgc.index_repo.assert_has_awaits(
            [
                unittest.mock.call(r"E:\repo\spdk\lib\log"),
                unittest.mock.call(r"E:\repo\spdk\include\spdk"),
            ]
        )
        self.assertEqual(adapter._cgc.wait_for_index.await_count, 2)

    async def test_cli_find_callers_parses_table_names(self) -> None:
        def _run(cmd, **kwargs):
            return unittest.mock.Mock(
                returncode=0,
                stdout=(
                    "Function 'spdk_thread_poll' is called by:\n"
                    "| caller_a | E:/repo/file.c:10 |\n"
                    "| caller_b | E:/repo/file.c:20 |\n"
                    "Total: 2 function(s)\n"
                ),
                stderr="",
            )

        cli = CGCCLIClient(python_exe="python-test", run=_run)
        callers = await cli.find_callers("spdk_thread_poll", repo_path=r"E:\repo")

        self.assertEqual([c["name"] for c in callers], ["caller_a", "caller_b"])


if __name__ == "__main__":
    unittest.main()
