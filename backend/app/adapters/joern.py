"""Joern CPG adapter.

IRON LAW: analyze() may ONLY do:
  (a) HTTP calls to the Joern server
  (b) Response format conversion
No CPG construction, no AST traversal, no graph building.

CAPABILITY UTILIZATION RULE: Every CPGQL query category listed in the
capability matrix MUST have a corresponding method. No "deployed but unused."
"""

import asyncio

import json as _json
import logging
import re
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

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

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
    _loaded_project_by_base_url: dict[str, str] = {}
    _prepare_locks: dict[tuple[str, int], asyncio.Lock] = {}

    def __init__(self, base_url: str = "http://joern:8080"):
        self.base_url = base_url
        self._imported_project: str | None = None

    @classmethod
    def _prepare_lock_for(cls, base_url: str) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        key = (base_url, id(loop))
        lock = cls._prepare_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            cls._prepare_locks[key] = lock
        return lock

    @classmethod
    def clear_cached_project(cls, base_url: str) -> None:
        cls._loaded_project_by_base_url.pop(base_url, None)

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
            await self._query("val x = 1; x", timeout=3)
            return ToolHealth(is_healthy=True, container_status="running")
        except httpx.TimeoutException as exc:
            return ToolHealth(
                is_healthy=True,
                container_status="busy",
                last_check=str(exc),
            )
        except Exception as exc:
            return ToolHealth(
                is_healthy=False,
                container_status="error",
                last_check=str(exc),
            )

    async def prepare(self, request: AnalysisRequest) -> None:
        """Import code into Joern's CPG.

        Skips import if the same project is already loaded in the Joern
        workspace, avoiding a 3+ minute re-import for large C codebases.
        """
        project_name = request.repo_local_path.rstrip("/").split("/")[-1]
        async with self._prepare_lock_for(self.base_url):
            loaded_project = self._loaded_project_by_base_url.get(self.base_url)

            # Check if already loaded in Joern workspace
            if loaded_project == project_name:
                try:
                    check = await self._query("cpg.method.size")
                    if isinstance(check, (int, str)):
                        self._imported_project = project_name
                        logger.info("joern: CPG already loaded for %s, skipping import", project_name)
                        return
                except Exception:
                    self.clear_cached_project(self.base_url)
                    # CPG stale or closed — re-import

            await self._query(
                f'importCode("{request.repo_local_path}", "{project_name}")',
                timeout=600,
            )
            self._imported_project = project_name
            self._loaded_project_by_base_url[self.base_url] = project_name
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
            '.map(e => Map("code" -> e.code, '
            '"filename" -> e.filename, '
            '"line_number" -> e.lineNumber.getOrElse(-1))).l).l.toJson'
        )
        return await self._query(query)

    async def function_branches(self, method_name: str) -> Any:
        """Get all branches within a specific function."""
        query = (
            f'cpg.method.nameExact("{method_name}")'
            ".controlStructure"
            ".map(cs => Map("
            '"control_structure_type" -> cs.controlStructureType, '
            '"condition" -> cs.condition.code.headOption.getOrElse(""), '
            '"line_number" -> cs.lineNumber.getOrElse(-1), '
            '"filename" -> cs.file.name.headOption.getOrElse(""), '
            '"children" -> cs.astChildren.l.take(5).map(c => Map('
            '"code" -> c.code.take(120), '
            '"label" -> c.label))'
            ")).l.toJson"
        )
        return await self._query(query)

    async def error_paths(self, method_name: str) -> Any:
        """Get all throw/catch/error-return paths in a function."""
        query = (
            f'val method = cpg.method.nameExact("{method_name}")\n'
            "val throws = method.ast.isCall"
            '.nameExact("<operator>.throw")'
            '.map(t => Map("kind" -> "throw", '
            '"code" -> t.code, '
            '"line_number" -> t.lineNumber.getOrElse(-1), '
            '"filename" -> t.file.name.headOption.getOrElse(""))).l\n'
            "val catches = method.tryBlock"
            '.map(t => Map("kind" -> "try-catch", '
            '"code" -> t.code, '
            '"line_number" -> t.lineNumber.getOrElse(-1), '
            '"filename" -> t.file.name.headOption.getOrElse(""))).l\n'
            "val errorReturns = method.ast.isReturn"
            '.where(_.code(".*[Ee]rr.*|.*null.*|.*None.*|.*false.*"))'
            '.map(r => Map("kind" -> "error-return", '
            '"code" -> r.code, '
            '"line_number" -> r.lineNumber.getOrElse(-1), '
            '"filename" -> r.file.name.headOption.getOrElse(""))).l\n'
            "(throws ++ catches ++ errorReturns).toJson"
        )
        return await self._query(query)

    async def boundary_values(self, method_name: str) -> Any:
        """Find boundary value comparisons in a function."""
        query = (
            f'cpg.method.nameExact("{method_name}")'
            ".ast.isCall"
            '.name("<operator>.(greaterThan|lessThan|greaterEqualsThan|lessEqualsThan)")'
            ".map(c => Map("
            '"code" -> c.code, '
            '"line_number" -> c.lineNumber.getOrElse(-1), '
            '"filename" -> c.file.name.headOption.getOrElse(""), '
            '"operands" -> c.argument.map(a => Map('
            '"code" -> a.code, '
            '"type" -> a.typeFullName)).l'
            ")).l.toJson"
        )
        return await self._query(query)

    async def call_context(self, method_name: str) -> Any:
        """Cross-function: who calls this function and from what control flow context.

        Returns callers, their branches leading to the call, and the arguments passed.
        This enables understanding how upstream decisions affect the target function.
        """
        query = (
            f'val target = cpg.method.nameExact("{method_name}")\n'
            # Direct callers and their call sites
            "val callers = target.callIn.method.l\n"
            "callers.map(caller => {\n"
            '  val callSites = caller.call.nameExact("' + method_name + '").l\n'
            "  val branchesBeforeCall = caller.controlStructure.l\n"
            '  Map(\n'
            '    "caller" -> caller.name,\n'
            '    "callerFile" -> caller.filename,\n'
            '    "callerLine" -> caller.lineNumber.getOrElse(-1),\n'
            '    "callSites" -> callSites.map(cs => Map(\n'
            '      "line" -> cs.lineNumber.getOrElse(-1),\n'
            '      "args" -> cs.argument.code.l\n'
            "    )),\n"
            '    "callerBranches" -> branchesBeforeCall.map(cs => Map(\n'
            '      "type" -> cs.controlStructureType,\n'
            '      "condition" -> cs.condition.code.headOption.getOrElse(""),\n'
            '      "line" -> cs.lineNumber.getOrElse(-1)\n'
            "    ))\n"
            "  )\n"
            "}).l.toJson"
        )
        return await self._query(query)

    async def callee_impact(self, method_name: str) -> Any:
        """Cross-function: what does this function call and how do returns propagate.

        Shows the callees, their error returns, and how the target function
        handles (or doesn't handle) those return values in its branches.
        """
        query = (
            f'val target = cpg.method.nameExact("{method_name}")\n'
            "val callees = target.call.callee.internal.l\n"
            "callees.map(callee => {\n"
            '  val errorReturns = callee.ast.isReturn'
            '.where(_.code(".*[Ee]rr.*|.*null.*|.*NULL.*|.*-1.*|.*false.*")).l\n'
            "  val callSitesInTarget = target.call.nameExact(callee.name).l\n"
            '  Map(\n'
            '    "callee" -> callee.name,\n'
            '    "calleeFile" -> callee.filename,\n'
            '    "calleeLine" -> callee.lineNumber.getOrElse(-1),\n'
            '    "errorReturns" -> errorReturns.map(r => Map(\n'
            '      "code" -> r.code,\n'
            '      "line" -> r.lineNumber.getOrElse(-1)\n'
            "    )),\n"
            '    "callSitesInTarget" -> callSitesInTarget.map(cs => Map(\n'
            '      "line" -> cs.lineNumber.getOrElse(-1),\n'
            '      "code" -> cs.code\n'
            "    ))\n"
            "  )\n"
            "}).l.toJson"
        )
        return await self._query(query)

    async def method_list(self) -> Any:
        """Get all method names — lightweight query for UI."""
        query = (
            'cpg.method.internal.map(m => Map('
            '"name" -> m.name, '
            '"filename" -> m.filename, '
            '"line" -> m.lineNumber.getOrElse(-1), '
            '"lineEnd" -> m.lineNumberEnd.getOrElse(-1), '
            '"paramCount" -> m.parameter.size'
            ")).l.toJson"
        )
        return await self._query(query)

    async def stream_logs(self, run_id: str) -> AsyncIterator[str]:
        yield "joern: importing code into CPG..."
        yield "joern: running control flow analysis..."
        yield "joern: running data flow analysis..."
        yield "joern: completed"

    async def cleanup(self, request: AnalysisRequest) -> None:
        """No-op: keep the CPG loaded for subsequent queries.

        Joern CPG import is expensive (3+ min for large repos). Closing
        after every request would force a re-import on the next call.
        The project is only evicted when a different repo is loaded.
        """
        pass

    # ── internal ──

    async def _query(self, cpgql: str, timeout: int = 120) -> Any:
        """POST to Joern /query-sync and parse the response.

        Strips ANSI escape codes from Joern's REPL output, then
        extracts JSON from the Scala REPL wrapper format:
          val resN: Type = <json_or_scala_value>

        Queries using .toJson produce: val resN: String = "..."
        where the inner string is valid JSON.
        """
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=10),
        ) as client:
            resp = await client.post(
                "/query-sync",
                json={"query": cpgql},
            )
            resp.raise_for_status()
            data = resp.json()

            if "stdout" not in data:
                return data

            clean = _ANSI_RE.sub("", data["stdout"])

            # Try direct JSON parse first (works for simple values)
            try:
                return _json.loads(clean)
            except (_json.JSONDecodeError, TypeError):
                pass

            # Multi-statement queries produce multiple "val ..." lines.
            # The final result is always the last "val resN: ... = ..."
            # For .toJson queries, the value is a quoted JSON string.
            lines = clean.strip().split("\n")
            for line in reversed(lines):
                line = line.strip()
                eq_pos = line.find("= ")
                if eq_pos == -1:
                    continue
                value_part = line[eq_pos + 2:].strip()
                # Scala triple-quoted string: """..."""
                if value_part.startswith('"""') and value_part.endswith('"""'):
                    inner_str = value_part[3:-3]
                    try:
                        return _json.loads(inner_str)
                    except (_json.JSONDecodeError, TypeError):
                        pass
                if value_part.startswith('"'):
                    try:
                        inner = _json.loads(value_part)
                        if isinstance(inner, str):
                            return _json.loads(inner)
                        return inner
                    except (_json.JSONDecodeError, TypeError):
                        continue
                # Non-string value (Int, etc.)
                try:
                    return _json.loads(value_part)
                except (_json.JSONDecodeError, TypeError):
                    continue

            return clean
