import asyncio
import unittest
from unittest.mock import patch

from app.adapters.base import AnalysisRequest
from app.adapters.gitnexus import GitNexusAdapter
from app.config import settings


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.content = b"{}" if payload else b""

    @property
    def is_error(self) -> bool:
        return self.status_code >= 400

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeAsyncClient:
    def __init__(self, get_responses=None, post_responses=None):
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])
        self.get_calls: list[tuple[str, dict | None, int | None]] = []
        self.post_calls: list[tuple[str, dict | None]] = []
        self.is_closed = False

    async def get(self, path: str, params: dict | None = None, timeout: int | None = None):
        self.get_calls.append((path, params, timeout))
        assert self.get_responses, f"unexpected GET {path}"
        return self.get_responses.pop(0)

    async def post(self, path: str, json: dict | None = None, **kwargs):
        self.post_calls.append((path, json))
        if path == "/api/embed":
            return _FakeResponse(202, {"jobId": "embed-job"})
        assert self.post_responses, f"unexpected POST {path}"
        return self.post_responses.pop(0)


class _ConcurrentAnalyzeClient:
    active_jobs = 0
    max_active_jobs = 0
    post_order: list[str] = []

    def __init__(self, repo_name: str):
        self.repo_name = repo_name
        self.is_closed = False

    async def get(self, path: str, params: dict | None = None, timeout: int | None = None):
        if path == "/api/repos":
            return _FakeResponse(200, {"repos": []})
        if path.startswith("/api/analyze/"):
            await asyncio.sleep(0.02)
            type(self).active_jobs -= 1
            return _FakeResponse(200, {"status": "complete", "repoName": self.repo_name})
        raise AssertionError(f"unexpected GET {path}")

    async def post(self, path: str, json: dict | None = None, **kwargs):
        if path != "/api/analyze":
            raise AssertionError(f"unexpected POST {path}")
        type(self).post_order.append(str(json.get("path") if json else ""))
        type(self).active_jobs += 1
        type(self).max_active_jobs = max(type(self).max_active_jobs, type(self).active_jobs)
        return _FakeResponse(200, {"jobId": f"job-{self.repo_name}"})


class GitNexusAdapterConfigTests(unittest.TestCase):
    def test_default_base_url_uses_runtime_settings(self) -> None:
        with patch.object(settings, "gitnexus_base_url", "http://127.0.0.1:7100"):
            adapter = GitNexusAdapter()

        self.assertEqual(adapter.base_url, "http://127.0.0.1:7100")


class GitNexusAdapterPrepareTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        GitNexusAdapter._indexed_repo_by_path.clear()
        GitNexusAdapter._prepare_locks.clear()
        GitNexusAdapter._analyze_locks.clear()
        GitNexusAdapter._next_analyze_start_at.clear()
        _ConcurrentAnalyzeClient.active_jobs = 0
        _ConcurrentAnalyzeClient.max_active_jobs = 0
        _ConcurrentAnalyzeClient.post_order = []

    def tearDown(self) -> None:
        GitNexusAdapter._indexed_repo_by_path.clear()
        GitNexusAdapter._prepare_locks.clear()
        GitNexusAdapter._analyze_locks.clear()
        GitNexusAdapter._next_analyze_start_at.clear()

    async def test_prepare_reuses_indexed_repo_across_fresh_instances(self) -> None:
        request = AnalysisRequest(repo_local_path="/tmp/repos/open-iscsi")

        first_client = _FakeAsyncClient(
            get_responses=[
                _FakeResponse(200, {"repos": []}),
                _FakeResponse(200, {"status": "complete", "repoName": "open-iscsi"}),
            ],
            post_responses=[
                _FakeResponse(200, {"jobId": "job-1"}),
            ],
        )
        first = GitNexusAdapter(base_url="http://gitnexus:7100")
        first._client = first_client

        second_client = _FakeAsyncClient(
            get_responses=[
                _FakeResponse(200, {"repos": ["open-iscsi"]}),
            ],
        )
        second = GitNexusAdapter(base_url="http://gitnexus:7100")
        second._client = second_client

        with patch("app.adapters.gitnexus.to_tool_repo_path", side_effect=lambda repo_local_path, **_: repo_local_path), patch("app.adapters.gitnexus._POLL_INTERVAL", 0):
            await first.prepare(request)
            await second.prepare(request)

        self.assertEqual(first_client.post_calls, [
            ("/api/analyze", {"path": "/tmp/repos/open-iscsi"}),
        ])
        self.assertEqual(second_client.post_calls, [])
        self.assertEqual(
            second_client.get_calls,
            [("/api/repos", {"repo": "open-iscsi"}, 10)],
        )
        self.assertEqual(second.current_repo_name, "open-iscsi")

    async def test_prepare_reindexes_when_cached_repo_is_missing(self) -> None:
        request = AnalysisRequest(repo_local_path="/tmp/repos/open-iscsi")
        adapter = GitNexusAdapter(base_url="http://gitnexus:7100")
        adapter._client = _FakeAsyncClient(
            get_responses=[
                _FakeResponse(200, {"repos": []}),
                _FakeResponse(200, {"repos": []}),
                _FakeResponse(200, {"status": "complete", "repoName": "open-iscsi"}),
            ],
            post_responses=[
                _FakeResponse(200, {"jobId": "job-2"}),
            ],
        )
        GitNexusAdapter._indexed_repo_by_path[("http://gitnexus:7100", "/tmp/repos/open-iscsi")] = "open-iscsi"

        with patch("app.adapters.gitnexus.to_tool_repo_path", side_effect=lambda repo_local_path, **_: repo_local_path), patch("app.adapters.gitnexus._POLL_INTERVAL", 0):
            await adapter.prepare(request)

        self.assertEqual(
            adapter._client.get_calls,
            [
                ("/api/repos", {"repo": "open-iscsi"}, 10),
                ("/api/repos", None, 10),
                ("/api/analyze/job-2", None, None),
            ],
        )
        self.assertEqual(adapter._client.post_calls, [
            ("/api/analyze", {"path": "/tmp/repos/open-iscsi"}),
        ])
        self.assertEqual(adapter.current_repo_name, "open-iscsi")

    async def test_prepare_retries_analyze_when_gitnexus_is_temporarily_busy(self) -> None:
        request = AnalysisRequest(repo_local_path="/tmp/repos/spdk")
        adapter = GitNexusAdapter(base_url="http://gitnexus:7100")
        adapter._client = _FakeAsyncClient(
            get_responses=[
                _FakeResponse(200, {"repos": []}),
                _FakeResponse(200, {"repos": []}),
                _FakeResponse(200, {"status": "complete", "repoName": "spdk"}),
            ],
            post_responses=[
                _FakeResponse(429, {"error": "too many requests"}),
                _FakeResponse(200, {"jobId": "job-retry"}),
            ],
        )

        with (
            patch("app.adapters.gitnexus.to_tool_repo_path", side_effect=lambda repo_local_path, **_: repo_local_path),
            patch("app.adapters.gitnexus._POLL_INTERVAL", 0),
            patch("app.adapters.gitnexus._ANALYZE_BUSY_RETRY_INTERVAL", 0),
        ):
            await adapter.prepare(request)

        self.assertEqual(adapter._client.post_calls[:2], [
            ("/api/analyze", {"path": "/tmp/repos/spdk"}),
            ("/api/analyze", {"path": "/tmp/repos/spdk"}),
        ])
        self.assertEqual(adapter._client.get_calls[:2], [
            ("/api/repos", None, 10),
            ("/api/repos", None, 10),
        ])
        self.assertEqual(adapter.current_repo_name, "spdk")

    async def test_prepare_serializes_analyze_jobs_for_different_paths(self) -> None:
        first = GitNexusAdapter(base_url="http://gitnexus:7100")
        first._client = _ConcurrentAnalyzeClient("alpha")
        second = GitNexusAdapter(base_url="http://gitnexus:7100")
        second._client = _ConcurrentAnalyzeClient("beta")

        with (
            patch("app.adapters.gitnexus.to_tool_repo_path", side_effect=lambda repo_local_path, **_: repo_local_path),
            patch("app.adapters.gitnexus._POLL_INTERVAL", 0),
            patch("app.adapters.gitnexus._ANALYZE_START_COOLDOWN", 0),
        ):
            await asyncio.gather(
                first.prepare(AnalysisRequest(repo_local_path="/tmp/repos/alpha")),
                second.prepare(AnalysisRequest(repo_local_path="/tmp/repos/beta")),
            )

        self.assertEqual(_ConcurrentAnalyzeClient.max_active_jobs, 1)
        self.assertCountEqual(
            _ConcurrentAnalyzeClient.post_order,
            ["/tmp/repos/alpha", "/tmp/repos/beta"],
        )

    async def test_prepare_recovers_immediately_when_busy_analyze_finishes_existing_repo(self) -> None:
        request = AnalysisRequest(repo_local_path="/tmp/repos/spdk")
        adapter = GitNexusAdapter(base_url="http://gitnexus:7100")
        adapter._client = _FakeAsyncClient(
            get_responses=[
                _FakeResponse(200, {"repos": []}),
                _FakeResponse(
                    200,
                    {
                        "repos": [
                            {
                                "name": "spdk",
                                "path": "/tmp/repos/spdk",
                                "fileCount": 42,
                            }
                        ]
                    },
                ),
            ],
            post_responses=[
                _FakeResponse(429, {"error": "too many requests"}),
            ],
        )

        with (
            patch("app.adapters.gitnexus.to_tool_repo_path", side_effect=lambda repo_local_path, **_: repo_local_path),
            patch("app.adapters.gitnexus._ANALYZE_BUSY_RETRY_ATTEMPTS", 45),
            patch("app.adapters.gitnexus._ANALYZE_BUSY_RETRY_INTERVAL", 0),
            patch.object(settings, "gitnexus_auto_embed_enabled", False),
        ):
            await adapter.prepare(request)

        self.assertEqual(adapter._client.post_calls, [
            ("/api/analyze", {"path": "/tmp/repos/spdk"}),
        ])
        self.assertEqual(adapter._client.get_calls, [
            ("/api/repos", None, 10),
            ("/api/repos", None, 10),
        ])
        self.assertEqual(adapter.current_repo_name, "spdk")

    async def test_prepare_does_not_trigger_embed_by_default(self) -> None:
        request = AnalysisRequest(repo_local_path="/tmp/repos/spdk")
        adapter = GitNexusAdapter(base_url="http://gitnexus:7100")
        adapter._client = _FakeAsyncClient(
            get_responses=[
                _FakeResponse(200, {"repos": []}),
                _FakeResponse(200, {"status": "complete", "repoName": "spdk"}),
            ],
            post_responses=[
                _FakeResponse(200, {"jobId": "job-no-embed"}),
            ],
        )

        with (
            patch("app.adapters.gitnexus.to_tool_repo_path", side_effect=lambda repo_local_path, **_: repo_local_path),
            patch("app.adapters.gitnexus._POLL_INTERVAL", 0),
            patch.object(settings, "gitnexus_auto_embed_enabled", False),
        ):
            await adapter.prepare(request)
            await asyncio.sleep(0)

        self.assertEqual(adapter._client.post_calls, [
            ("/api/analyze", {"path": "/tmp/repos/spdk"}),
        ])

    async def test_prepare_triggers_embed_when_explicitly_enabled(self) -> None:
        request = AnalysisRequest(repo_local_path="/tmp/repos/spdk")
        adapter = GitNexusAdapter(base_url="http://gitnexus:7100")
        adapter._client = _FakeAsyncClient(
            get_responses=[
                _FakeResponse(200, {"repos": []}),
                _FakeResponse(200, {"status": "complete", "repoName": "spdk"}),
            ],
            post_responses=[
                _FakeResponse(200, {"jobId": "job-with-embed"}),
            ],
        )

        with (
            patch("app.adapters.gitnexus.to_tool_repo_path", side_effect=lambda repo_local_path, **_: repo_local_path),
            patch("app.adapters.gitnexus._POLL_INTERVAL", 0),
            patch.object(settings, "gitnexus_auto_embed_enabled", True),
        ):
            await adapter.prepare(request)
            await asyncio.sleep(0)

        self.assertIn(("/api/embed", None), adapter._client.post_calls)


class GitNexusAdapterProgressParsingTests(unittest.IsolatedAsyncioTestCase):
    """Verify on_progress handles all known GitNexus progress field shapes."""

    def setUp(self) -> None:
        GitNexusAdapter._indexed_repo_by_path.clear()
        GitNexusAdapter._prepare_locks.clear()
        GitNexusAdapter._analyze_locks.clear()
        GitNexusAdapter._next_analyze_start_at.clear()

    tearDown = setUp

    async def _run_with_progress(self, progress_value) -> list[int]:
        """Run prepare() with a single pending poll followed by complete; collect progress callbacks."""
        adapter = GitNexusAdapter(base_url="http://gitnexus:7100")
        adapter._client = _FakeAsyncClient(
            get_responses=[
                _FakeResponse(200, {"repos": []}),
                _FakeResponse(200, {"status": "pending", "progress": progress_value}),
                _FakeResponse(200, {"status": "complete", "repoName": "myrepo"}),
            ],
            post_responses=[
                _FakeResponse(200, {"jobId": "job-x"}),
            ],
        )
        recorded: list[int] = []

        async def _cb(pct: int) -> None:
            recorded.append(pct)

        with (
            patch("app.adapters.gitnexus.to_tool_repo_path", side_effect=lambda repo_local_path, **_: repo_local_path),
            patch("app.adapters.gitnexus._POLL_INTERVAL", 0),
        ):
            from app.adapters.base import AnalysisRequest
            await adapter.prepare(AnalysisRequest(repo_local_path="/tmp/repos/myrepo"), on_progress=_cb)
        return recorded

    async def test_progress_dict_current_key(self):
        """dict with 'current' key must not crash and return current value."""
        recorded = await self._run_with_progress({"current": 50, "total": 100})
        self.assertEqual(len(recorded), 2)
        self.assertEqual(recorded[0], 50)

    async def test_progress_dict_percent_key(self):
        """dict with only 'percent' key uses that value."""
        recorded = await self._run_with_progress({"percent": 75})
        self.assertEqual(recorded[0], 75)

    async def test_progress_dict_all_zero_falls_back_to_elapsed(self):
        """dict with all falsy values falls back to elapsed-based estimate (no crash)."""
        recorded = await self._run_with_progress({"current": 0, "total": 0})
        self.assertIsInstance(recorded[0], int)

    async def test_progress_int(self):
        """Plain int still works as before."""
        recorded = await self._run_with_progress(42)
        self.assertEqual(recorded[0], 42)

    async def test_progress_string(self):
        """String numeric value is parsed correctly."""
        recorded = await self._run_with_progress("30")
        self.assertEqual(recorded[0], 30)

    async def test_progress_none_falls_back_to_elapsed(self):
        """None progress field falls back to elapsed-based estimate."""
        recorded = await self._run_with_progress(None)
        self.assertIsInstance(recorded[0], int)


class GitNexusHealthIndexedReposTests(unittest.IsolatedAsyncioTestCase):
    """health_check() indexed_repos reflects real _indexed_repo_by_path cache."""

    def setUp(self) -> None:
        GitNexusAdapter._indexed_repo_by_path.clear()
        GitNexusAdapter._analyze_locks.clear()
        GitNexusAdapter._next_analyze_start_at.clear()

    def tearDown(self) -> None:
        GitNexusAdapter._indexed_repo_by_path.clear()
        GitNexusAdapter._analyze_locks.clear()
        GitNexusAdapter._next_analyze_start_at.clear()

    async def test_health_check_reports_zero_when_nothing_indexed(self) -> None:
        adapter = GitNexusAdapter(base_url="http://gitnexus:7100")
        adapter._client = _FakeAsyncClient(
            get_responses=[
                _FakeResponse(200, {"version": "1.0"}),
                _FakeResponse(200, {"repos": []}),
            ],
        )
        health = await adapter.health_check()
        self.assertEqual(health.indexed_repos, 0)
        self.assertEqual(health.container_status, "running_no_index")
        self.assertIn("no indexed repos", health.last_check)

    async def test_health_check_reports_actual_count_after_indexing(self) -> None:
        GitNexusAdapter._indexed_repo_by_path[("http://gitnexus:7100", "/repo/a")] = "a"
        GitNexusAdapter._indexed_repo_by_path[("http://gitnexus:7100", "/repo/b")] = "b"
        adapter = GitNexusAdapter(base_url="http://gitnexus:7100")
        adapter._client = _FakeAsyncClient(
            get_responses=[
                _FakeResponse(200, {"version": "1.0"}),
                _FakeResponse(200, {"repos": ["a", "b"]}),
            ],
        )
        health = await adapter.health_check()
        self.assertEqual(health.indexed_repos, 2)
        self.assertEqual(health.container_status, "running")

    async def test_health_check_reports_degraded_fallback_detail(self) -> None:
        adapter = GitNexusAdapter(base_url="http://gitnexus:7100")
        adapter._client = _FakeAsyncClient(
            get_responses=[],
            post_responses=[_FakeResponse(400, {"error": "path required"})],
        )
        health = await adapter.health_check()
        self.assertTrue(health.is_healthy)
        self.assertEqual(health.container_status, "running_degraded")
        self.assertIn("/api/info failed", health.last_check)
        self.assertIn("/api/analyze accepted probe", health.last_check)

    async def test_health_check_reports_count_even_when_unhealthy(self) -> None:
        GitNexusAdapter._indexed_repo_by_path[("http://gitnexus:7100", "/repo/c")] = "c"
        adapter = GitNexusAdapter(base_url="http://gitnexus:7100")
        adapter._client = _FakeAsyncClient(
            get_responses=[],
            post_responses=[_FakeResponse(503, {})],
        )
        health = await adapter.health_check()
        self.assertFalse(health.is_healthy)
        self.assertEqual(health.indexed_repos, 1)


if __name__ == "__main__":
    unittest.main()
