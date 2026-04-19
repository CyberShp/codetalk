import unittest
from unittest.mock import AsyncMock, patch

from app.services.wiki_orchestrator import WikiOrchestrator, WikiPage


class WikiOrchestratorPayloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_determine_structure_payload_marks_local_repo(self) -> None:
        orchestrator = WikiOrchestrator()
        captured: dict = {}

        async def fake_stream_collect(client, payload):
            captured.update(payload)
            return "<wiki_structure><title>t</title><description>d</description><pages></pages></wiki_structure>"

        with patch.object(orchestrator, "_stream_collect", AsyncMock(side_effect=fake_stream_collect)):
            await orchestrator._determine_structure(
                client=object(),  # type: ignore[arg-type]
                file_tree="tree",
                readme="readme",
                repo_local_path="/data/repos/repo-uuid",
                language="zh",
                provider="openai",
                model="gpt-4o",
                comprehensive=True,
            )

        self.assertEqual(captured["repo_url"], "/data/repos/repo-uuid")
        self.assertEqual(captured["type"], "local")

    async def test_generate_page_payload_uses_newline_separated_included_files(self) -> None:
        orchestrator = WikiOrchestrator()
        captured: dict = {}

        async def fake_stream_collect(client, payload):
            captured.update(payload)
            return "# Page"

        with patch.object(orchestrator, "_stream_collect", AsyncMock(side_effect=fake_stream_collect)):
            await orchestrator._generate_page(
                client=object(),  # type: ignore[arg-type]
                page=WikiPage(
                    id="p1",
                    title="Storage Path",
                    file_paths=["lib/iscsi.c", "lib/login.c"],
                ),
                repo_local_path="/data/repos/repo-uuid",
                language="zh",
                provider="openai",
                model="gpt-4o",
            )

        self.assertEqual(captured["type"], "local")
        self.assertEqual(captured["included_files"], "lib/iscsi.c\nlib/login.c")


if __name__ == "__main__":
    unittest.main()
