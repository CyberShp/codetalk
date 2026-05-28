"""Unit tests for AnalysisPipeline three-level fallback (Subtask 4)."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.adapters.base import ToolHealth
from app.services.analysis_pipeline import AnalysisPipeline


def _mock_adapter(healthy: bool) -> MagicMock:
    adapter = MagicMock()
    adapter.health_check = AsyncMock(
        return_value=ToolHealth(
            is_healthy=healthy,
            container_status="running" if healthy else "unreachable",
        )
    )
    return adapter


def _pipeline_with_adapters(
    gitnexus_healthy: bool | None = True,
    cgc_healthy: bool | None = True,
) -> AnalysisPipeline:
    pipeline = AnalysisPipeline()
    pipeline._tool_adapters = {}
    if gitnexus_healthy is not None:
        pipeline._tool_adapters["gitnexus"] = _mock_adapter(gitnexus_healthy)
    if cgc_healthy is not None:
        pipeline._tool_adapters["cgc"] = _mock_adapter(cgc_healthy)
    return pipeline


class TestAssessToolHealth(unittest.IsolatedAsyncioTestCase):
    async def test_dual_mode_when_both_healthy(self) -> None:
        pipeline = _pipeline_with_adapters(gitnexus_healthy=True, cgc_healthy=True)
        await pipeline._assess_tool_health()
        self.assertEqual(pipeline._pipeline_mode, "dual")
        self.assertEqual(pipeline._tool_health_warning, "")

    async def test_gitnexus_only_when_cgc_unhealthy(self) -> None:
        pipeline = _pipeline_with_adapters(gitnexus_healthy=True, cgc_healthy=False)
        await pipeline._assess_tool_health()
        self.assertEqual(pipeline._pipeline_mode, "gitnexus_only")
        self.assertIn("CGC", pipeline._tool_health_warning)

    async def test_cgc_only_when_gitnexus_unhealthy(self) -> None:
        pipeline = _pipeline_with_adapters(gitnexus_healthy=False, cgc_healthy=True)
        await pipeline._assess_tool_health()
        self.assertEqual(pipeline._pipeline_mode, "cgc_only")
        self.assertIn("GitNexus", pipeline._tool_health_warning)

    async def test_llm_direct_when_both_unhealthy(self) -> None:
        pipeline = _pipeline_with_adapters(gitnexus_healthy=False, cgc_healthy=False)
        await pipeline._assess_tool_health()
        self.assertEqual(pipeline._pipeline_mode, "llm_direct")
        self.assertIn("LLM", pipeline._tool_health_warning)

    async def test_llm_direct_when_no_adapters(self) -> None:
        pipeline = AnalysisPipeline()
        pipeline._tool_adapters = {}
        await pipeline._assess_tool_health()
        self.assertEqual(pipeline._pipeline_mode, "llm_direct")

    async def test_llm_direct_when_health_check_raises(self) -> None:
        pipeline = AnalysisPipeline()
        broken = MagicMock()
        broken.health_check = AsyncMock(side_effect=RuntimeError("connection refused"))
        pipeline._tool_adapters = {"gitnexus": broken, "cgc": broken}
        await pipeline._assess_tool_health()
        self.assertEqual(pipeline._pipeline_mode, "llm_direct")


class TestPhasePrepareAutoAddsCGC(unittest.IsolatedAsyncioTestCase):
    async def test_cgc_added_even_when_not_in_tools(self) -> None:
        """CGC should be soft-added to _tool_adapters regardless of user tools list."""
        pipeline = AnalysisPipeline()

        mock_cgc_adapter = MagicMock()
        mock_cgc_adapter.prepare = AsyncMock(return_value=None)

        mock_gitnexus_adapter = MagicMock()
        mock_gitnexus_adapter.prepare = AsyncMock(return_value=None)

        def fake_create(name: str):
            if name == "cgc":
                return mock_cgc_adapter
            if name == "gitnexus":
                return mock_gitnexus_adapter
            raise KeyError(name)

        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.services.analysis_pipeline.create_adapter", side_effect=fake_create):
                await pipeline._phase_prepare(tmp, ["gitnexus"])

        self.assertIn("cgc", pipeline._tool_adapters)
        mock_cgc_adapter.prepare.assert_awaited_once()

    async def test_cgc_not_double_added_if_already_in_tools(self) -> None:
        """CGC must not be prepared twice if user explicitly listed it."""
        pipeline = AnalysisPipeline()

        mock_cgc_adapter = MagicMock()
        mock_cgc_adapter.prepare = AsyncMock(return_value=None)

        def fake_create(name: str):
            if name == "cgc":
                return mock_cgc_adapter
            raise KeyError(name)

        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.services.analysis_pipeline.create_adapter", side_effect=fake_create):
                await pipeline._phase_prepare(tmp, ["cgc"])

        # prepare called exactly once (from the user tools loop, not the soft-add)
        mock_cgc_adapter.prepare.assert_awaited_once()


class TestPhaseCollectGating(unittest.IsolatedAsyncioTestCase):
    async def test_gitnexus_skipped_in_cgc_only_mode(self) -> None:
        pipeline = AnalysisPipeline()
        pipeline._pipeline_mode = "cgc_only"
        collected: list[str] = []

        async def fake_collect_gitnexus(_):
            collected.append("gitnexus")

        async def fake_collect_deepwiki(_):
            collected.append("deepwiki")

        pipeline._collect_gitnexus = fake_collect_gitnexus  # type: ignore[method-assign]
        pipeline._collect_deepwiki = fake_collect_deepwiki  # type: ignore[method-assign]

        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            await pipeline._phase_collect(tmp, ["gitnexus", "deepwiki"])

        self.assertNotIn("gitnexus", collected)
        self.assertIn("deepwiki", collected)

    async def test_gitnexus_skipped_in_llm_direct_mode(self) -> None:
        pipeline = AnalysisPipeline()
        pipeline._pipeline_mode = "llm_direct"
        collected: list[str] = []

        async def fake_collect_gitnexus(_):
            collected.append("gitnexus")

        async def fake_collect_deepwiki(_):
            collected.append("deepwiki")

        pipeline._collect_gitnexus = fake_collect_gitnexus  # type: ignore[method-assign]
        pipeline._collect_deepwiki = fake_collect_deepwiki  # type: ignore[method-assign]

        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            await pipeline._phase_collect(tmp, ["gitnexus", "deepwiki"])

        self.assertNotIn("gitnexus", collected)

    async def test_gitnexus_runs_in_dual_mode(self) -> None:
        pipeline = AnalysisPipeline()
        pipeline._pipeline_mode = "dual"
        collected: list[str] = []

        async def fake_collect_gitnexus(_):
            collected.append("gitnexus")

        async def fake_collect_deepwiki(_):
            collected.append("deepwiki")

        pipeline._collect_gitnexus = fake_collect_gitnexus  # type: ignore[method-assign]
        pipeline._collect_deepwiki = fake_collect_deepwiki  # type: ignore[method-assign]

        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            await pipeline._phase_collect(tmp, ["gitnexus", "deepwiki"])

        self.assertIn("gitnexus", collected)
        self.assertIn("deepwiki", collected)

    async def test_gitnexus_runs_in_gitnexus_only_mode(self) -> None:
        pipeline = AnalysisPipeline()
        pipeline._pipeline_mode = "gitnexus_only"
        collected: list[str] = []

        async def fake_collect_gitnexus(_):
            collected.append("gitnexus")

        pipeline._collect_gitnexus = fake_collect_gitnexus  # type: ignore[method-assign]
        pipeline._collect_deepwiki = AsyncMock()  # type: ignore[method-assign]

        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            await pipeline._phase_collect(tmp, ["gitnexus"])

        self.assertIn("gitnexus", collected)
