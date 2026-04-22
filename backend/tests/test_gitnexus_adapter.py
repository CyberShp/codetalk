import unittest
from unittest.mock import patch

from app.adapters.base import AnalysisRequest
from app.adapters.gitnexus import GitNexusAdapter


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

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

    async def post(self, path: str, json: dict | None = None):
        self.post_calls.append((path, json))
        assert self.post_responses, f"unexpected POST {path}"
        return self.post_responses.pop(0)


class GitNexusAdapterPrepareTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        GitNexusAdapter._indexed_repo_by_path.clear()
        GitNexusAdapter._prepare_locks.clear()

    def tearDown(self) -> None:
        GitNexusAdapter._indexed_repo_by_path.clear()
        GitNexusAdapter._prepare_locks.clear()

    async def test_prepare_reuses_indexed_repo_across_fresh_instances(self) -> None:
        request = AnalysisRequest(repo_local_path="/tmp/repos/open-iscsi")

        first_client = _FakeAsyncClient(
            get_responses=[
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
            ("/api/analyze", {"path": "/tmp/repos/open-iscsi"})
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
                ("/api/analyze/job-2", None, None),
            ],
        )
        self.assertEqual(adapter._client.post_calls, [
            ("/api/analyze", {"path": "/tmp/repos/open-iscsi"})
        ])
        self.assertEqual(adapter.current_repo_name, "open-iscsi")


if __name__ == "__main__":
    unittest.main()
