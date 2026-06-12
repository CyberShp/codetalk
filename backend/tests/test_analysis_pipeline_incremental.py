"""Regression tests for AnalysisPipeline incremental reuse — Task 15.

Covers the `changed_files: set[str] | None` three-state semantics:

  set()  (zero-diff confirmed) -> all modules served from cache, no LLM call
  None   (diff failed/unknown) -> all modules re-analysed via LLM
"""
import unittest
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import httpx

from app.services.analysis_pipeline import AnalysisPipeline


_PREV_COMMIT = "aaaa1111"
_CURR_COMMIT = "bbbb2222"

_CACHED_ENTRY: dict = {
    "module_name": "backend",
    "summary": "cached summary",
    "files": ["backend/app/main.py"],
}

_COMMUNITY: dict = {
    "id": "dir_backend",
    "name": "backend",
    "files": ["backend/app/main.py"],
    "calls": [],
}


def _make_pipeline() -> AnalysisPipeline:
    p = AnalysisPipeline()
    p._repo_path = "/fake/repo"
    return p


class TestIncrementalReuse(unittest.IsolatedAsyncioTestCase):
    """Verify three-state changed_files semantics in _phase_module_analysis."""

    async def _run_phase(
        self, pipeline: AnalysisPipeline, changed_files_result
    ) -> AsyncMock:
        """Run _phase_module_analysis with all I/O and LLM calls mocked.

        Returns the AsyncMock for _analyze_module so callers can assert on it.
        """
        analyze_mock = AsyncMock(return_value="fresh summary")
        llm_client = MagicMock()

        with (
            patch.object(
                pipeline,
                "_get_repo_commit_hash",
                new=AsyncMock(return_value=_CURR_COMMIT),
            ),
            patch.object(
                pipeline,
                "_load_module_summaries_cache",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                pipeline,
                "_load_latest_module_summaries_cache",
                new=AsyncMock(return_value=([_CACHED_ENTRY], _PREV_COMMIT)),
            ),
            patch.object(
                pipeline,
                "_get_changed_files",
                new=AsyncMock(return_value=changed_files_result),
            ),
            patch.object(pipeline, "_extract_communities", return_value=[_COMMUNITY]),
            patch.object(pipeline, "_analyze_module", new=analyze_mock),
            patch.object(
                pipeline,
                "_save_module_summaries_cache",
                new=AsyncMock(),
            ),
            patch.object(AnalysisPipeline, "_log_step", new=AsyncMock()),
        ):
            await pipeline._phase_module_analysis(llm_client)

        return analyze_mock

    async def test_zero_diff_reuses_all_modules(self):
        """set() -> git confirmed nothing changed -> every module comes from cache."""
        pipeline = _make_pipeline()
        analyze_mock = await self._run_phase(pipeline, set())

        self.assertEqual(len(pipeline._module_summaries), 1)
        self.assertEqual(pipeline._module_summaries[0]["summary"], "cached summary")
        analyze_mock.assert_not_called()

    async def test_diff_failure_triggers_full_reanalysis(self):
        """None -> diff failed/unknown -> must not reuse stale cache, must call LLM."""
        pipeline = _make_pipeline()
        analyze_mock = await self._run_phase(pipeline, None)

        self.assertEqual(len(pipeline._module_summaries), 1)
        analyze_mock.assert_called_once()
        self.assertEqual(pipeline._module_summaries[0]["summary"], "fresh summary")


class TestCollectGitnexusPathTranslation(unittest.IsolatedAsyncioTestCase):
    """_collect_gitnexus must translate the host path via to_tool_repo_path before calling GitNexus,
    matching the behaviour of GitNexusAdapter.prepare()."""

    def _make_pipeline(self) -> AnalysisPipeline:
        p = AnalysisPipeline()
        p._repo_path = "/host/repo"
        return p

    async def test_translated_path_sent_to_gitnexus(self):
        """The path sent in the POST body must be the tool-translated path, not the raw host path."""
        pipeline = self._make_pipeline()
        fake_ok = MagicMock()
        fake_ok.status_code = 200
        fake_ok.is_error = False
        fake_ok.json.return_value = {"jobId": "job-1"}

        fake_complete = MagicMock()
        fake_complete.json.return_value = {"status": "complete", "repoName": "repo"}

        fake_graph = MagicMock()
        fake_graph.status_code = 200
        fake_graph.raise_for_status = MagicMock()
        fake_graph.json.return_value = {"nodes": [], "relationships": []}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=fake_ok)
        mock_client.get = AsyncMock(side_effect=[fake_complete, fake_graph])

        with (
            patch("app.services.analysis_pipeline.httpx.AsyncClient", return_value=mock_client),
            patch.object(pipeline, "_build_gitnexus_cache_key", new=AsyncMock(return_value=None)),
            patch.object(pipeline, "_save_gitnexus_cache", new=AsyncMock()),
            patch.object(pipeline, "_gitnexus_resolve_repo", new=AsyncMock(return_value=None)),
            patch(
                "app.services.analysis_pipeline.to_tool_repo_path",
                return_value="/container/repo",
            ) as mock_translate,
        ):
            await pipeline._collect_gitnexus("/host/repo")

        mock_translate.assert_called_once_with(
            "/host/repo",
            host_base_path=ANY,
            tool_base_path=ANY,
            local_host_path=ANY,
            local_container_path=ANY,
        )
        # The POST must use the translated path, not the host path
        mock_client.post.assert_called_once_with(
            "/api/analyze", json={"path": "/container/repo"}
        )


class TestCollectGitnexus409(unittest.IsolatedAsyncioTestCase):
    """Regression: 409 handling in _collect_gitnexus must match GitNexusAdapter."""

    def _make_pipeline(self) -> AnalysisPipeline:
        p = AnalysisPipeline()
        p._repo_path = "/fake/repo"
        return p

    def _mock_client(self, post_resp: MagicMock, get_resp: MagicMock | None = None) -> AsyncMock:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(return_value=post_resp)
        if get_resp is not None:
            client.get = AsyncMock(return_value=get_resp)
        return client

    async def test_409_no_job_id_no_repo_name_raises(self):
        """409 with neither jobId nor repoName must raise, not silently pull wrong graph."""
        pipeline = self._make_pipeline()
        fake_409 = MagicMock()
        fake_409.status_code = 409
        fake_409.is_error = False
        fake_409.content = b"{}"
        fake_409.json.return_value = {}

        mock_client = self._mock_client(fake_409)

        with (
            patch("app.services.analysis_pipeline.httpx.AsyncClient", return_value=mock_client),
            patch.object(pipeline, "_build_gitnexus_cache_key", new=AsyncMock(return_value=None)),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                await pipeline._collect_gitnexus("/fake/repo")
        self.assertIn("父项目", str(ctx.exception))

    async def test_409_with_repo_name_skips_poll(self):
        """409 with repoName must reuse the indexed repo and call /api/graph once."""
        pipeline = self._make_pipeline()
        fake_409 = MagicMock()
        fake_409.status_code = 409
        fake_409.is_error = False
        fake_409.content = b'{"repoName":"myrepo"}'
        fake_409.json.return_value = {"repoName": "myrepo"}

        fake_graph = MagicMock()
        fake_graph.status_code = 200
        fake_graph.raise_for_status = MagicMock()
        fake_graph.json.return_value = {"nodes": [], "relationships": []}

        mock_client = self._mock_client(fake_409, fake_graph)

        with (
            patch("app.services.analysis_pipeline.httpx.AsyncClient", return_value=mock_client),
            patch.object(pipeline, "_build_gitnexus_cache_key", new=AsyncMock(return_value=None)),
            patch.object(pipeline, "_save_gitnexus_cache", new=AsyncMock()),
            patch.object(pipeline, "_gitnexus_resolve_repo", new=AsyncMock(return_value=None)),
        ):
            await pipeline._collect_gitnexus("/fake/repo")

        mock_client.post.assert_called_once()
        mock_client.get.assert_called_once_with("/api/graph", params={"repo": "myrepo"}, timeout=120)
