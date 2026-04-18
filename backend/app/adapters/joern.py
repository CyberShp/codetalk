"""Joern CPG adapter.

IRON LAW: analyze() may ONLY do:
  (a) HTTP calls to the Joern server
  (b) Response format conversion
No CPG construction, no AST traversal, no graph building.

CAPABILITY UTILIZATION RULE: Every CPGQL query category listed in the
capability matrix MUST have a corresponding method. No "deployed but unused."
"""

import json as _json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import (
    AnalysisRequest,
    BaseToolAdapter,
    ToolCapability,
    ToolHealth,
    UnifiedResult,
)

logger = logging.getLogger(__name__)

# ── Predefined CPGQL queries for each analysis category ──

QUERIES: dict[str, str] = {
    # 基础结构
    "methods": (
        "cpg.method.map(m => "
        "(m.name, m.filename, m.lineNumber, m.lineNumberEnd, m.parameter.size)).l"
    ),
    "calls": (
        "cpg.call.map(c => "
        "(c.name, c.methodFullName, c.filename, c.lineNumber)).l"
    ),

    # 控制流分析 — 核心
    "control_structures": (
        "cpg.controlStructure.map(cs => ("
        "cs.controlStructureType, cs.code, cs.filename, "
        "cs.lineNumber, cs.method.name)).l"
    ),
    "if_conditions": (
        "cpg.controlStructure.isIf.condition"
        ".map(c => (c.code, c.filename, c.lineNumber, c.method.name)).l"
    ),
    "switch_cases": (
        "cpg.controlStructure.isSwitchCase"
        ".map(sc => (sc.code, sc.filename, sc.lineNumber)).l"
    ),

    # 异常处理 — 核心
    "try_blocks": (
        "cpg.tryBlock.map(t => ("
        "t.filename, t.lineNumber, t.lineNumberEnd, t.method.name)).l"
    ),
    "throw_points": (
        'cpg.call.nameExact("<operator>.throw")'
        ".map(t => (t.code, t.filename, t.lineNumber, "
        "t.method.name, t.argument.code.l)).l"
    ),

    # 边界值和字面量 — 核心
    "numeric_literals": (
        "cpg.literal"
        '.typeFullName("(int|long|float|double|number|Integer|Long|Float|Double)")'
        ".map(l => (l.code, l.filename, l.lineNumber, l.method.name)).l"
    ),
    "comparison_operators": (
        "cpg.call"
        '.name("<operator>.(greaterThan|lessThan|greaterEqualsThan|lessEqualsThan|equals|notEquals)")'
        ".map(c => (c.code, c.filename, c.lineNumber, c.method.name)).l"
    ),
    "null_checks": (
        "cpg.call"
        '.name("<operator>.(equals|notEquals)")'
        '.where(_.argument.isLiteral.code("null|None|nil|undefined"))'
        ".map(c => (c.code, c.filename, c.lineNumber, c.method.name)).l"
    ),

    # 数据流
    "method_parameters": (
        "cpg.method.internal"
        ".map(m => (m.name, m.filename, "
        "m.parameter.map(p => (p.name, p.typeFullName)).l)).l"
    ),
    "return_points": (
        "cpg.ret.map(r => (r.code, r.filename, r.lineNumber, r.method.name)).l"
    ),

    # 安全相关
    "external_calls": (
        "cpg.call.where(_.callee.isExternal)"
        ".map(c => (c.name, c.filename, c.lineNumber, c.method.name)).l"
    ),
}


class JoernAdapter(BaseToolAdapter):
    def __init__(self, base_url: str = "http://joern:8080"):
        self.base_url = base_url
        self._imported_project: str | None = None

    def name(self) -> str:
        return "joern"

    def capabilities(self) -> list[ToolCapability]:
        return [
            ToolCapability.CALL_GRAPH,
            ToolCapability.TAINT_ANALYSIS,
            ToolCapability.AST_ANALYSIS,
            ToolCapability.SECURITY_SCAN,
        ]

    async def health_check(self) -> ToolHealth:
        try:
            result = await self._query("val x = 1; x")
            return ToolHealth(is_healthy=True, container_status="running")
        except Exception as exc:
            return ToolHealth(
                is_healthy=False,
                container_status="error",
                last_check=str(exc),
            )

    async def prepare(self, request: AnalysisRequest) -> None:
        """Import code into Joern's CPG."""
        project_name = request.repo_local_path.rstrip("/").split("/")[-1]
        await self._query(
            f'importCode("{request.repo_local_path}", "{project_name}")'
        )
        self._imported_project = project_name
        logger.info("joern: CPG imported for %s", project_name)

    async def analyze(self, request: AnalysisRequest) -> UnifiedResult:
        """Run all predefined queries and return structured results.

        HTTP calls + response format conversion ONLY.
        """
        results: dict[str, Any] = {}

        for query_name, query in QUERIES.items():
            try:
                result = await self._query(query)
                results[query_name] = result
                logger.info(
                    "joern: %s returned %d items",
                    query_name,
                    len(result) if isinstance(result, list) else 1,
                )
            except Exception as exc:
                logger.warning("joern: query %s failed: %s", query_name, exc)
                results[query_name] = {"error": str(exc)}

        return UnifiedResult(
            tool_name="joern",
            capability=ToolCapability.AST_ANALYSIS,
            data={"cpg_analysis": results},
            raw_output=f"{len(results)} query categories executed",
            metadata={
                "project": self._imported_project,
                "query_count": len(results),
            },
        )

    # ── High-level query methods (exposed to API) ──

    async def query_custom(self, cpgql: str) -> Any:
        """Execute arbitrary CPGQL query — exposed for advanced users and LLM."""
        return await self._query(cpgql)

    async def taint_analysis(
        self, source_pattern: str, sink_pattern: str
    ) -> Any:
        """Cross-function taint tracking from source to sink."""
        query = (
            f'val source = cpg.call.name("{source_pattern}").l\n'
            f'val sink = cpg.call.name("{sink_pattern}").l\n'
            "sink.reachableBy(source)"
            ".map(path => path.elements"
            ".map(e => (e.code, e.filename, e.lineNumber)).l).l"
        )
        return await self._query(query)

    async def function_branches(self, method_name: str) -> Any:
        """Get all branches within a specific function."""
        query = (
            f'cpg.method.nameExact("{method_name}")'
            ".controlStructure"
            ".map(cs => ("
            "cs.controlStructureType, "
            "cs.condition.code.headOption, "
            "cs.lineNumber, "
            "cs.astChildren.map(c => (c.code.take(100), c.label)).l"
            ")).l"
        )
        return await self._query(query)

    async def error_paths(self, method_name: str) -> Any:
        """Get all throw/catch/error-return paths in a function."""
        query = (
            f'val method = cpg.method.nameExact("{method_name}")\n'
            "val throws = method.ast.isCall"
            '.nameExact("<operator>.throw")'
            '.map(t => ("throw", t.code, t.lineNumber)).l\n'
            "val catches = method.tryBlock"
            '.map(t => ("try-catch", t.code.take(200), t.lineNumber)).l\n'
            "val errorReturns = method.ast.isReturn"
            '.where(_.code(".*[Ee]rr.*|.*null.*|.*None.*|.*false.*"))'
            '.map(r => ("error-return", r.code, r.lineNumber)).l\n'
            "throws ++ catches ++ errorReturns"
        )
        return await self._query(query)

    async def boundary_values(self, method_name: str) -> Any:
        """Find boundary value comparisons in a function."""
        query = (
            f'cpg.method.nameExact("{method_name}")'
            ".ast.isCall"
            '.name("<operator>.(greaterThan|lessThan|greaterEqualsThan|lessEqualsThan)")'
            ".map(c => ("
            "c.code, c.lineNumber, "
            "c.argument.map(a => (a.code, a.typ.name)).l"
            ")).l"
        )
        return await self._query(query)

    async def method_list(self) -> Any:
        """Get all method names — lightweight query for UI."""
        return await self._query(QUERIES["methods"])

    async def stream_logs(self, run_id: str) -> AsyncIterator[str]:
        yield "joern: importing code into CPG..."
        yield "joern: running control flow analysis..."
        yield "joern: running data flow analysis..."
        yield "joern: completed"

    async def cleanup(self, request: AnalysisRequest) -> None:
        if self._imported_project:
            try:
                await self._query(f'close("{self._imported_project}")')
            except Exception as exc:
                logger.warning("joern: cleanup failed: %s", exc)
            self._imported_project = None

    # ── internal ──

    async def _query(self, cpgql: str) -> Any:
        """POST to Joern /query-sync and parse the response."""
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(120, connect=10),
        ) as client:
            resp = await client.post(
                "/query-sync",
                json={"query": cpgql},
            )
            resp.raise_for_status()
            data = resp.json()

            if "stdout" in data:
                try:
                    return _json.loads(data["stdout"])
                except (_json.JSONDecodeError, TypeError):
                    return data["stdout"]
            return data
