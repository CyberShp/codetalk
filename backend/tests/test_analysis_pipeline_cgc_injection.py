"""Unit tests for AnalysisPipeline._inject_cgc_evidence_cards (Subtask 3)."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from app.adapters.base import ToolHealth
from app.schemas.workspace_analysis import ResolvedAnalysisObject, ScopeCandidate
from app.services.analysis_pipeline import AnalysisPipeline


def _make_resolved(
    object_id: str,
    symbols: list[str] | None = None,
    source: str = "gitnexus",
) -> ResolvedAnalysisObject:
    candidates = [
        ScopeCandidate(symbol=s, source=source, confidence="high", reason="test")
        for s in (symbols or [])
    ]
    return ResolvedAnalysisObject(
        object_id=object_id,
        text=f"test object {object_id}",
        candidate_symbols=candidates,
    )


def _make_pipeline(cgc_healthy: bool = True, cgc_in_adapters: bool = True) -> AnalysisPipeline:
    pipeline = AnalysisPipeline()
    pipeline._repo_path = "/fake/repo"
    pipeline._evidence_cards = []
    pipeline._task_id = "task-test"

    if cgc_in_adapters:
        mock_adapter = MagicMock()
        mock_adapter.health_check = AsyncMock(
            return_value=ToolHealth(
                is_healthy=cgc_healthy,
                container_status="running" if cgc_healthy else "unreachable",
            )
        )
        mock_cgc = MagicMock()
        mock_adapter._cgc = mock_cgc
        pipeline._tool_adapters = {"cgc": mock_adapter}
    else:
        pipeline._tool_adapters = {}

    return pipeline


class TestCGCInjectionNoAdapter(unittest.IsolatedAsyncioTestCase):
    async def test_returns_empty_when_no_cgc_adapter(self) -> None:
        pipeline = _make_pipeline(cgc_in_adapters=False)
        resolved = [_make_resolved("obj1", symbols=["MyFunc"])]
        cards = await pipeline._inject_cgc_evidence_cards(resolved, budget=10)
        self.assertEqual(cards, [])

    async def test_returns_empty_when_cgc_unhealthy(self) -> None:
        pipeline = _make_pipeline(cgc_healthy=False)
        resolved = [_make_resolved("obj1", symbols=["MyFunc"])]
        cards = await pipeline._inject_cgc_evidence_cards(resolved, budget=10)
        self.assertEqual(cards, [])


class TestCGCInjectionCallerCallee(unittest.IsolatedAsyncioTestCase):
    async def test_callers_card_created_for_gitnexus_symbol(self) -> None:
        pipeline = _make_pipeline()
        cgc = pipeline._tool_adapters["cgc"]._cgc
        cgc.module_deps = AsyncMock(return_value={})
        cgc.find_callers = AsyncMock(return_value=[{"name": "callerA"}, {"name": "callerB"}])
        cgc.find_callees = AsyncMock(return_value=[])

        resolved = [_make_resolved("obj1", symbols=["MyFunc"])]
        cards = await pipeline._inject_cgc_evidence_cards(resolved, budget=20)

        callers_cards = [c for c in cards if "调用者" in c.title]
        self.assertEqual(len(callers_cards), 1)
        self.assertIn("callerA", callers_cards[0].notes[0])
        self.assertEqual(callers_cards[0].source, "cgc")
        self.assertTrue(callers_cards[0].needs_verification)
        self.assertEqual(callers_cards[0].snippet, "")

    async def test_callees_card_created_for_gitnexus_symbol(self) -> None:
        pipeline = _make_pipeline()
        cgc = pipeline._tool_adapters["cgc"]._cgc
        cgc.module_deps = AsyncMock(return_value={})
        cgc.find_callers = AsyncMock(return_value=[])
        cgc.find_callees = AsyncMock(return_value=[{"name": "calleeX"}])

        resolved = [_make_resolved("obj1", symbols=["MyFunc"])]
        cards = await pipeline._inject_cgc_evidence_cards(resolved, budget=20)

        callees_cards = [c for c in cards if "被调用" in c.title]
        self.assertEqual(len(callees_cards), 1)
        self.assertIn("calleeX", callees_cards[0].notes[0])

    async def test_non_gitnexus_symbols_skipped(self) -> None:
        pipeline = _make_pipeline()
        cgc = pipeline._tool_adapters["cgc"]._cgc
        cgc.module_deps = AsyncMock(return_value={})
        cgc.find_callers = AsyncMock(return_value=[{"name": "x"}])
        cgc.find_callees = AsyncMock(return_value=[])

        resolved = [_make_resolved("obj1", symbols=["ManualSym"], source="manual")]
        cards = await pipeline._inject_cgc_evidence_cards(resolved, budget=20)

        caller_cards = [c for c in cards if "调用者" in c.title]
        self.assertEqual(len(caller_cards), 0)

    async def test_callers_error_skipped_gracefully(self) -> None:
        pipeline = _make_pipeline()
        cgc = pipeline._tool_adapters["cgc"]._cgc
        cgc.module_deps = AsyncMock(return_value={})
        cgc.find_callers = AsyncMock(side_effect=Exception("network error"))
        cgc.find_callees = AsyncMock(return_value=[{"name": "calleeY"}])

        resolved = [_make_resolved("obj1", symbols=["BrokenFunc"])]
        cards = await pipeline._inject_cgc_evidence_cards(resolved, budget=20)

        self.assertTrue(any("被调用" in c.title for c in cards))


class TestCGCInjectionCallChain(unittest.IsolatedAsyncioTestCase):
    async def test_call_chain_card_created_for_two_symbols(self) -> None:
        pipeline = _make_pipeline()
        cgc = pipeline._tool_adapters["cgc"]._cgc
        cgc.module_deps = AsyncMock(return_value={})
        cgc.find_callers = AsyncMock(return_value=[])
        cgc.find_callees = AsyncMock(return_value=[])
        cgc.call_chain = AsyncMock(
            return_value={"chain": ["FuncA", "FuncB", "FuncC"]}
        )

        resolved = [_make_resolved("obj1", symbols=["FuncA", "FuncB"])]
        cards = await pipeline._inject_cgc_evidence_cards(resolved, budget=20)

        chain_cards = [c for c in cards if "调用链" in c.title]
        self.assertEqual(len(chain_cards), 1)
        self.assertIn("FuncA", chain_cards[0].notes[0])
        self.assertIn("FuncB", chain_cards[0].notes[0])

    async def test_no_chain_card_for_single_symbol(self) -> None:
        pipeline = _make_pipeline()
        cgc = pipeline._tool_adapters["cgc"]._cgc
        cgc.module_deps = AsyncMock(return_value={})
        cgc.find_callers = AsyncMock(return_value=[])
        cgc.find_callees = AsyncMock(return_value=[])

        resolved = [_make_resolved("obj1", symbols=["OnlyOne"])]
        cards = await pipeline._inject_cgc_evidence_cards(resolved, budget=20)

        self.assertFalse(any("调用链" in c.title for c in cards))


class TestCGCInjectionModuleDeps(unittest.IsolatedAsyncioTestCase):
    async def test_module_deps_card_created(self) -> None:
        pipeline = _make_pipeline()
        cgc = pipeline._tool_adapters["cgc"]._cgc
        cgc.module_deps = AsyncMock(return_value={"moduleA": ["moduleB"], "moduleC": []})
        cgc.find_callers = AsyncMock(return_value=[])
        cgc.find_callees = AsyncMock(return_value=[])

        resolved = [_make_resolved("obj1", symbols=["Sym"])]
        cards = await pipeline._inject_cgc_evidence_cards(resolved, budget=20)

        dep_cards = [c for c in cards if "依赖" in c.title]
        self.assertEqual(len(dep_cards), 1)
        self.assertEqual(dep_cards[0].source, "cgc")


class TestCGCInjectionBudget(unittest.IsolatedAsyncioTestCase):
    async def test_budget_respected(self) -> None:
        pipeline = _make_pipeline()
        pipeline._evidence_cards = [MagicMock()] * 9  # 9 existing cards
        cgc = pipeline._tool_adapters["cgc"]._cgc
        cgc.module_deps = AsyncMock(return_value={"mod": ["dep"]})
        cgc.find_callers = AsyncMock(return_value=[{"name": "c1"}])
        cgc.find_callees = AsyncMock(return_value=[{"name": "e1"}])

        resolved = [_make_resolved("obj1", symbols=["FuncA", "FuncB"])]
        # budget=10, existing=9 → only 1 more card allowed
        cards = await pipeline._inject_cgc_evidence_cards(resolved, budget=10)

        self.assertLessEqual(len(cards), 1)


if __name__ == "__main__":
    unittest.main()
