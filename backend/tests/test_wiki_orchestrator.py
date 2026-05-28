import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.services.wiki_orchestrator import EmptyEmbeddingError, WikiOrchestrator, WikiPage, WikiStructure


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


class EmptyEmbeddingPatternTests(unittest.IsolatedAsyncioTestCase):
    def _make_stream_client(self, body: str, status_code: int = 500) -> MagicMock:
        response = MagicMock()
        response.status_code = status_code
        response.aread = AsyncMock(return_value=body.encode())
        response.request = MagicMock()
        response.aiter_text = AsyncMock(return_value=iter([]))

        async_cm = MagicMock()
        async_cm.__aenter__ = AsyncMock(return_value=response)
        async_cm.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=async_cm)
        return mock_client

    async def test_deepwiki_actual_error_message_raises_empty_embedding_error(self) -> None:
        """DeepWiki's real 500 body triggers EmptyEmbeddingError (MAS-45)."""
        actual_error = (
            '{"detail":"No valid document embeddings found. This may be due to '
            "embedding size inconsistencies or API errors during document processing.\"}"
        )
        orchestrator = WikiOrchestrator()
        mock_client = self._make_stream_client(actual_error)

        with self.assertRaises(EmptyEmbeddingError):
            await orchestrator._stream_collect(mock_client, {"messages": []})

    async def test_generic_500_does_not_raise_empty_embedding_error(self) -> None:
        """A generic 500 body raises HTTPStatusError, not EmptyEmbeddingError."""
        orchestrator = WikiOrchestrator()
        mock_client = self._make_stream_client('{"detail":"Internal server error"}')

        with self.assertRaises(httpx.HTTPStatusError):
            await orchestrator._stream_collect(mock_client, {"messages": []})


class ParseStructureXmlTests(unittest.TestCase):
    _MINIMAL_XML = (
        "<wiki_structure>"
        "<title>Test Repo</title>"
        "<description>A test wiki</description>"
        "<pages></pages>"
        "</wiki_structure>"
    )

    def test_markdown_codefenced_xml_parses_successfully(self) -> None:
        fenced = f"```xml\n{self._MINIMAL_XML}\n```"
        result = WikiOrchestrator._parse_structure_xml(fenced)
        self.assertIsInstance(result, WikiStructure)
        self.assertEqual(result.title, "Test Repo")
        self.assertEqual(result.description, "A test wiki")

    def test_no_xml_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            WikiOrchestrator._parse_structure_xml("Sorry, I cannot generate a wiki structure.")


if __name__ == "__main__":
    unittest.main()
