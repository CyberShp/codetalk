import unittest
from unittest.mock import patch

from app.adapters.base import ToolCapability, UnifiedResult
from app.api import repo_graph


class _FakeGitNexusAdapter:
    def __init__(self):
        self.prepared = False
        self.cleaned = False

    async def prepare(self, request):
        self.prepared = True

    async def analyze(self, request):
        return UnifiedResult(
            tool_name="gitnexus",
            capability=ToolCapability.KNOWLEDGE_GRAPH,
            data={"graph": {"nodes": [{"id": "n1"}], "edges": []}},
            metadata={"repo_name": "repo-uuid", "node_count": 1},
        )

    async def cleanup(self, request):
        self.cleaned = True


class RepoGraphFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_graph_fallback_uses_gitnexus_adapter(self) -> None:
        fake = _FakeGitNexusAdapter()

        with patch.object(repo_graph, "create_adapter", return_value=fake):
            result = await repo_graph._build_live_graph_response("/data/repos/repo-uuid")

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["graph"], {"nodes": [{"id": "n1"}], "edges": []})
        self.assertEqual(result["metadata"], {"repo_name": "repo-uuid", "node_count": 1})
        self.assertTrue(fake.prepared)
        self.assertTrue(fake.cleaned)
        self.assertIsNotNone(result["analyzed_at"])


if __name__ == "__main__":
    unittest.main()
