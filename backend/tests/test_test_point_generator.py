import json
import unittest
from unittest.mock import patch

from app.adapters.base import ToolCapability, UnifiedResult
from app.services import test_point_generator


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self.posts: list[tuple[str, dict | None, dict | None]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, path: str, params: dict | None = None, json: dict | None = None):
        self.posts.append((path, params, json))
        return _FakeResponse(
            200,
            {
                "results": [
                    {"name": "spdk_iscsi_read", "label": "Function", "filePath": "lib/iscsi.c"}
                ]
            },
        )


class _FakeGitNexusAdapter:
    def __init__(self):
        self.current_repo_name = ""
        self.prepare_calls = 0
        self.cleanup_calls = 0
        self.analyze_calls = 0

    async def prepare(self, request):
        self.prepare_calls += 1
        self.current_repo_name = "repo-uuid"

    async def analyze(self, request):
        self.analyze_calls += 1
        return UnifiedResult(
            tool_name="gitnexus",
            capability=ToolCapability.KNOWLEDGE_GRAPH,
            data={
                "graph": {
                    "processes": [
                        {"id": "p1", "label": "Process", "properties": {"name": "iSCSI login"}}
                    ]
                }
            },
            raw_output="",
            metadata={},
        )

    async def cleanup(self, request):
        self.cleanup_calls += 1


class GitNexusContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_targeted_context_prepares_repo_and_uses_post_search(self) -> None:
        fake_adapter = _FakeGitNexusAdapter()
        fake_client = _FakeAsyncClient()

        with (
            patch.object(test_point_generator, "create_adapter", return_value=fake_adapter),
            patch.object(test_point_generator.httpx, "AsyncClient", return_value=fake_client),
        ):
            result = await test_point_generator._get_gitnexus_context(
                "/data/repos/repo-uuid",
                "spdk_iscsi_read",
            )

        self.assertEqual(fake_adapter.prepare_calls, 1)
        self.assertEqual(fake_adapter.cleanup_calls, 1)
        self.assertEqual(fake_adapter.analyze_calls, 0)
        self.assertEqual(len(fake_client.posts), 1)
        path, params, payload = fake_client.posts[0]
        self.assertEqual(path, "/api/search")
        self.assertEqual(params, {"repo": "repo-uuid"})
        self.assertEqual(payload, {
            "query": "spdk_iscsi_read",
            "mode": "hybrid",
            "limit": 10,
            "enrich": True,
        })
        self.assertIn("spdk_iscsi_read", result["call_chain"])
        self.assertEqual(result["process"], "N/A")

    async def test_full_repo_context_uses_graph_processes_when_target_missing(self) -> None:
        fake_adapter = _FakeGitNexusAdapter()

        with patch.object(test_point_generator, "create_adapter", return_value=fake_adapter):
            result = await test_point_generator._get_gitnexus_context(
                "/data/repos/repo-uuid",
                None,
            )

        self.assertEqual(fake_adapter.prepare_calls, 1)
        self.assertEqual(fake_adapter.analyze_calls, 1)
        self.assertEqual(fake_adapter.cleanup_calls, 1)
        self.assertEqual(result["call_chain"], "N/A")
        self.assertEqual(
            json.loads(result["process"]),
            [{"id": "p1", "label": "Process", "properties": {"name": "iSCSI login"}}],
        )


if __name__ == "__main__":
    unittest.main()
