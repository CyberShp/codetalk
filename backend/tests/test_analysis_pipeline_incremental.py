"""Regression tests for AnalysisPipeline incremental reuse — Task 15.

Covers the `changed_files: set[str] | None` three-state semantics:

  set()  (zero-diff confirmed) -> all modules served from cache, no LLM call
  None   (diff failed/unknown) -> all modules re-analysed via LLM
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

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
