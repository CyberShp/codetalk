"""Tests for parallel adapter prepare in AnalysisPipeline._phase_prepare (Part 3A)."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.adapters.base import AnalysisRequest
from app.schemas.workspace_analysis import (
    AnalysisObject,
    AnalysisPlan,
    ResolvedAnalysisObject,
    ScopeCandidate,
    ScopePreview,
)
from app.services.analysis_pipeline import AnalysisPipeline

_FAKE_REPO = "/fake/repo"


def _path_exists_true(_self):
    return True


class ParallelPrepareTests(unittest.IsolatedAsyncioTestCase):
    """Verify that _phase_prepare runs git init + all adapter prepares in parallel."""

    def _make_pipeline(self) -> AnalysisPipeline:
        p = AnalysisPipeline()
        p._task_id = "test-task-1"
        return p

    async def test_prepare_calls_both_adapters_in_parallel(self) -> None:
        """Both cgc and gitnexus adapters have prepare() called."""
        cgc_adapter = MagicMock()
        cgc_adapter.prepare = AsyncMock()

        gitnexus_adapter = MagicMock()
        gitnexus_adapter.prepare = AsyncMock()

        pipeline = self._make_pipeline()

        def _fake_create(name: str):
            if name == "cgc":
                return cgc_adapter
            if name == "gitnexus":
                return gitnexus_adapter
            raise KeyError(name)

        with (
            patch.object(Path, "exists", _path_exists_true),
            patch("app.services.analysis_pipeline.create_adapter", side_effect=_fake_create),
            patch.object(pipeline, "_ensure_git_init", new=AsyncMock()),
        ):
            await pipeline._phase_prepare(_FAKE_REPO, ["cgc", "gitnexus"])

        cgc_adapter.prepare.assert_called_once()
        gitnexus_adapter.prepare.assert_called_once()

    async def test_prepare_stores_adapters_on_self(self) -> None:
        """Adapters created during prepare are stored in _tool_adapters."""
        adapter = MagicMock()
        adapter.prepare = AsyncMock()

        pipeline = self._make_pipeline()

        with (
            patch.object(Path, "exists", _path_exists_true),
            patch("app.services.analysis_pipeline.create_adapter", return_value=adapter),
            patch.object(pipeline, "_ensure_git_init", new=AsyncMock()),
        ):
            await pipeline._phase_prepare(_FAKE_REPO, ["cgc"])

        self.assertIn("cgc", pipeline._tool_adapters)
        self.assertIs(pipeline._tool_adapters["cgc"], adapter)

    async def test_prepare_unknown_tool_skipped_gracefully(self) -> None:
        """Tools without a registered adapter are silently skipped."""
        pipeline = self._make_pipeline()

        with (
            patch.object(Path, "exists", _path_exists_true),
            patch("app.services.analysis_pipeline.create_adapter", side_effect=KeyError),
            patch.object(pipeline, "_ensure_git_init", new=AsyncMock()),
        ):
            await pipeline._phase_prepare(_FAKE_REPO, ["unknown_tool"])

        self.assertEqual(pipeline._tool_adapters, {})

    async def test_prepare_adapter_error_is_non_fatal(self) -> None:
        """An adapter whose prepare() raises does not abort the pipeline."""
        failing_adapter = MagicMock()
        failing_adapter.prepare = AsyncMock(side_effect=RuntimeError("index failed"))

        pipeline = self._make_pipeline()

        with (
            patch.object(Path, "exists", _path_exists_true),
            patch("app.services.analysis_pipeline.create_adapter", return_value=failing_adapter),
            patch.object(pipeline, "_ensure_git_init", new=AsyncMock()),
        ):
            await pipeline._phase_prepare(_FAKE_REPO, ["cgc"])

        self.assertIn("cgc", pipeline._tool_adapters)

    async def test_ensure_git_init_called_regardless_of_tools(self) -> None:
        """_ensure_git_init is always called even when no tools are selected."""
        pipeline = self._make_pipeline()
        git_init_mock = AsyncMock()

        with (
            patch.object(Path, "exists", _path_exists_true),
            patch("app.services.analysis_pipeline.create_adapter", side_effect=KeyError),
            patch.object(pipeline, "_ensure_git_init", new=git_init_mock),
        ):
            await pipeline._phase_prepare(_FAKE_REPO, [])

        git_init_mock.assert_called_once_with(Path(_FAKE_REPO))

    async def test_prepare_passes_scope_aware_paths_only_to_cgc(self) -> None:
        """CGC indexes plan-relevant subtrees while GitNexus still sees the full repo."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "lib" / "log").mkdir(parents=True)
            (repo / "include" / "spdk").mkdir(parents=True)
            (repo / "lib" / "log" / "log.c").write_text("void spdk_vlog(void) {}", encoding="utf-8")
            (repo / "lib" / "log" / "log_flags.c").write_text("void spdk_log_set_flag(void) {}", encoding="utf-8")
            (repo / "include" / "spdk" / "log.h").write_text("void SPDK_NOTICELOG(void);", encoding="utf-8")

            cgc_adapter = MagicMock()
            cgc_adapter.prepare = AsyncMock()
            gitnexus_adapter = MagicMock()
            gitnexus_adapter.prepare = AsyncMock()

            pipeline = self._make_pipeline()
            pipeline._analysis_plan = AnalysisPlan(
                analysis_objects=[
                    AnalysisObject(
                        id="log",
                        text="log path focused analysis",
                        kind="file",
                        path_hints=["lib/log/log.c", "include/spdk/log.h"],
                    )
                ]
            )
            pipeline._scope_preview = ScopePreview(
                workspace_id="ws",
                resolved_objects=[
                    ResolvedAnalysisObject(
                        object_id="log",
                        text="log path focused analysis",
                        candidate_files=[
                            ScopeCandidate(
                                path="lib/log/log.c",
                                source="repo_search",
                                confidence="high",
                                reason="exact path hint",
                            ),
                            ScopeCandidate(
                                path="lib/log/log_flags.c",
                                source="repo_search",
                                confidence="high",
                                reason="exact path hint",
                            ),
                        ],
                    )
                ],
            )

            def _fake_create(name: str):
                if name == "cgc":
                    return cgc_adapter
                if name == "gitnexus":
                    return gitnexus_adapter
                raise KeyError(name)

            with (
                patch("app.services.analysis_pipeline.create_adapter", side_effect=_fake_create),
                patch.object(pipeline, "_ensure_git_init", new=AsyncMock()),
            ):
                await pipeline._phase_prepare(str(repo), ["gitnexus"])

        git_req = gitnexus_adapter.prepare.call_args.args[0]
        cgc_req = cgc_adapter.prepare.call_args.args[0]

        self.assertIsInstance(git_req, AnalysisRequest)
        self.assertEqual(Path(git_req.repo_local_path), repo)
        self.assertEqual(git_req.options.get("cgc_index_paths"), None)

        self.assertIsInstance(cgc_req, AnalysisRequest)
        self.assertEqual(Path(cgc_req.repo_local_path), repo)
        cgc_paths = {Path(p) for p in cgc_req.options["cgc_index_paths"]}
        self.assertEqual(
            cgc_paths,
            {repo / "lib" / "log", repo / "include" / "spdk"},
        )


class CGCAdapterDedupTests(unittest.IsolatedAsyncioTestCase):
    """Verify CGCAdapter.prepare() deduplicates concurrent calls for the same path."""

    async def test_concurrent_prepare_calls_are_deduped(self) -> None:
        """Two concurrent prepare() calls on the same repo path submit only one job."""
        from app.adapters.cgc import CGCAdapter

        call_log: list[str] = []

        async def _fake_index_repo(path, **_):
            await asyncio.sleep(0)  # yield so concurrent task can see the inflight map
            call_log.append(f"index:{path}")
            return "job-1"

        async def _fake_wait(job_id, **_):
            await asyncio.sleep(0)
            call_log.append(f"wait:{job_id}")

        adapter = CGCAdapter(base_url="http://cgc:7072")
        adapter._cgc.index_repo = _fake_index_repo
        adapter._cgc.wait_for_index = _fake_wait

        from app.adapters.base import AnalysisRequest
        req = AnalysisRequest(repo_local_path="/same/repo")

        await asyncio.gather(adapter.prepare(req), adapter.prepare(req))

        # True dedup: only one index_repo() job submitted regardless of concurrency
        index_calls = [e for e in call_log if e.startswith("index:")]
        wait_calls = [e for e in call_log if e.startswith("wait:")]
        self.assertEqual(len(index_calls), 1)
        self.assertEqual(len(wait_calls), 1)


if __name__ == "__main__":
    unittest.main()
