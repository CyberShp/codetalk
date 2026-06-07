"""Coverage analysis service — parses coverage data and uses LLM to
recommend uncovered branches, test points, and test cases."""

from __future__ import annotations

import json
import logging
import asyncio
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from app.adapters.coverage import (
    CoverageReport,
    FileCoverage,
    FunctionHit,
    ModuleCoverage,
    detect_and_parse_xml,
    parse_internal_function_hits,
    parse_internal_function_hits_xlsx,
    parse_html_coverage,
)
from app.config import settings
from app.llm.base import BaseLLMClient
from app.llm.factory import create_llm_client_from_active
from app.services.external_agent_discovery import (
    AgentDiscoveryRequest,
    run_external_agent_discovery,
    validate_agent_candidate_file,
)
from app.services.agent_discovery_session import (
    AgentContextPacketInput,
    AgentDiscoverySession,
    create_agent_discovery_session,
)

logger = logging.getLogger(__name__)
WORKSPACE_SCOPE_ENRICHMENT_TIMEOUT_SECONDS = 25.0

# Coverage gap test-design constants (coverage-test-design-v1).
COVERAGE_TEST_DESIGN_VERSION = "coverage-test-design-v1"
COVERAGE_TEST_CONTEXT_VERSION = "coverage-test-context-v1"
COVERAGE_ENTRY_DISCOVERY_VERSION = "coverage-entry-discovery-v1"
AI_TEST_DESIGN_VERSION = "coverage-ai-test-scenarios-v1"
BLACK_BOX_READY = "black_box_ready"
BLACK_BOX_HYPOTHESIS = "black_box_hypothesis"
GRAY_BOX_REQUIRED = "gray_box_required"
AI_REQUIRED_SCENARIO_FIELDS = (
    "scenario_id",
    "priority",
    "case_type",
    "flow_purpose",
    "external_trigger",
    "input_construction",
    "normal_path",
    "error_path",
    "key_call_chain",
    "expected_result",
    "observable_signals",
    "gray_box_aid",
    "sfmea",
    "evidence_refs",
    "related_gaps",
    "confidence",
    "verification_gaps",
)
AI_REQUIRED_SFMEA_FIELDS = (
    "failure_mode",
    "trigger_condition",
    "propagation_effect",
    "observable_effect",
    "recommended_test",
)
# Entry-oriented layered tracing: how far up the caller chain we walk to find an
# external entry (CLI / API / message handler / config / file input) before we
# fall back to a gray-box injection scheme.
ENTRY_TRACE_MAX_HOPS = 4
# Cap how many uncovered functions get the (more expensive) source-window +
# caller-chain trace so a large upload never blocks the request.
MAX_TRACED_FUNCTION_GAPS = 24
RIPGREP_TIMEOUT_SECONDS = 4.0
SOURCE_WINDOW_BEFORE = 3
SOURCE_WINDOW_AFTER = 60
_SOURCE_EXTENSION_CANDIDATES = (
    "",
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp",
    ".py", ".go", ".rs", ".java", ".js", ".jsx", ".ts", ".tsx", ".cs",
)
_SOURCE_FILE_EXTS = {ext for ext in _SOURCE_EXTENSION_CANDIDATES if ext}
_DIR_SKIP = {
    ".git", ".hg", ".svn", "node_modules", "dist", "build", "out", "target",
    ".next", "vendor", "coverage", ".tox", ".mypy_cache", ".pytest_cache",
    "__pycache__", ".venv", "venv",
}
_RIPGREP_EXCLUDE_GLOBS = tuple(
    glob
    for name in sorted(_DIR_SKIP)
    for glob in (f"!{name}/**", f"!**/{name}/**")
)

# A function-*definition* line: optional return type / modifiers, then the
# function name, a parenthesised parameter list, and a trailing block opener
# (``{``), Python/label ``:``, or end of line.  Plain call sites end in ``;`` and
# are rejected by the trailing-token requirement.
_FUNC_DEF_RE = re.compile(
    r"^[\w\s\*&:<>,~\[\]]*?\b([A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:\{|:|$)"
)
_ASSIGNED_FUNCTION_DEF_RES = (
    re.compile(
        r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)"
        r"(?:\s*:\s*[^=]+)?\s*=\s*(?:async\s*)?"
        r"(?:(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>|function\b)"
    ),
    re.compile(
        r"^\s*(?:(?:public|private|protected|static|readonly)\s+)*"
        r"(?P<name>[A-Za-z_$][\w$]*)"
        r"(?:\s*:\s*[^=]+)?\s*=\s*(?:async\s*)?"
        r"(?:(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>|function\b)"
    ),
    re.compile(
        r"^\s*(?P<name>[A-Za-z_$][\w$]*)\s*:\s*(?:async\s*)?"
        r"(?:(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>|function\b)"
    ),
    re.compile(r"^\s*(?P<name>[A-Za-z_$][\w$]*)\s*=\s*lambda\b"),
)
# Control-flow / declaration keywords that look like a call but are not a
# function definition.
_NON_FUNCTION_NAMES = {
    "if", "else", "elif", "for", "while", "switch", "case", "default", "return",
    "sizeof", "catch", "except", "do", "goto", "typedef", "struct", "union",
    "enum", "when", "guard", "with", "and", "or", "not", "in", "is",
}
_EXPRESSION_CALL_PREFIXES = (
    "return ",
    "yield ",
    "await ",
    "raise ",
    "throw ",
)


def _match_def_name(line: str) -> str | None:
    """Return the defined function's name if ``line`` is a definition, else None."""
    stripped = line.strip()
    if stripped.startswith(_EXPRESSION_CALL_PREFIXES):
        return None
    for pattern in _ASSIGNED_FUNCTION_DEF_RES:
        assigned = pattern.match(line)
        if assigned:
            name = assigned.group("name")
            return None if name in _NON_FUNCTION_NAMES else name
    before_paren = stripped.split("(", 1)[0]
    if "=" in before_paren and not stripped.startswith("def "):
        return None
    match = _FUNC_DEF_RE.match(line)
    if not match:
        return None
    name = match.group(1)
    return None if name in _NON_FUNCTION_NAMES else name


def _match_multiline_def_name(lines: list[str], idx: int) -> str | None:
    """Return a C-like function name for multi-line definitions/prototypes.

    SPDK commonly writes return types and long parameter lists across several
    lines.  A textual call like ``rc = fn(...`` must not be mistaken for a
    definition, so a match needs a type-like prefix on the same line or the
    previous non-empty line.
    """
    if idx < 0 or idx >= len(lines):
        return None
    stripped = lines[idx].strip()
    if stripped.startswith(_EXPRESSION_CALL_PREFIXES):
        return None
    before_paren = stripped.split("(", 1)[0]
    if "=" in before_paren and not stripped.startswith("def "):
        return None
    line_name = re.search(r"\b([A-Za-z_]\w*)\s*\(", lines[idx])
    if not line_name or line_name.group(1) in _NON_FUNCTION_NAMES:
        return None

    for start in range(idx, max(-1, idx - 3), -1):
        start_text = lines[start].strip()
        if not start_text or start_text.startswith(("//", "/*", "*")):
            break
        fragment_lines: list[str] = []
        for end in range(start, min(len(lines), idx + 8)):
            text = lines[end].strip()
            if not text:
                continue
            fragment_lines.append(text)
            if "{" in text or ";" in text:
                break
        signature = " ".join(fragment_lines)
        match = _SIGNATURE_RE.match(signature)
        if not match:
            continue
        name = match.group("name")
        if name in _NON_FUNCTION_NAMES:
            continue
        prefix = (match.group("prefix") or "").strip()
        prev = _previous_nonempty_line(lines, start)
        if prefix or (prev and _TYPE_ONLY_LINE_RE.match(prev.strip())):
            return name
    return None


def _previous_nonempty_line(lines: list[str], idx: int) -> str | None:
    for pos in range(idx - 1, -1, -1):
        text = lines[pos].strip()
        if text:
            return text
    return None
_BRANCH_KEYWORD_RE = re.compile(
    r"\b(if|else\s+if|elif|switch|case|default|while|for|catch|except|when|guard)\b"
    r"|return\s+-[A-Za-z0-9_]+|goto\s+\w+",
    re.IGNORECASE,
)
_CALLER_GUARD_RE = re.compile(
    r"\b(if|else\s+if|elif|switch|case|default|while|for|catch|except|when|guard)\b",
    re.IGNORECASE,
)
_ERROR_CONDITION_RE = re.compile(
    r"(<\s*0|<=\s*0|==\s*NULL|!=\s*0|==\s*-1|!\s*[A-Za-z_]\w*|\bNULL\b|\berr|"
    r"\berror|\bfail|errno|timeout|exception|panic|E[A-Z0-9_]{2,})",
    re.IGNORECASE,
)
_TYPE_ONLY_LINE_RE = re.compile(
    r"^(?:static\s+)?(?:inline\s+)?(?:const\s+)?(?:unsigned\s+|signed\s+)?"
    r"(?:void|bool|char|int|long|short|size_t|ssize_t|uint\d+_t|int\d+_t|"
    r"struct\s+[A-Za-z_]\w*|enum\s+[A-Za-z_]\w*|[A-Za-z_]\w*(?:\s*[*&]+)?)$"
)
_SIGNATURE_RE = re.compile(
    r"^(?P<prefix>[\w\s\*&:<>,~\[\]]*?)\b"
    r"(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:\{|;|$)"
)
_REQ_FIELD_RE = re.compile(r"\b(?:req|attrs|opts|ctx)\.([A-Za-z_]\w*)\b")
_REQUEST_FIELD_RES = (
    re.compile(
        r"\b(?:request|req|payload|body|params|query|data)"
        r"(?:\.(?:json|args|form|query|body|data|params|headers|cookies|values|files))?"
        r"\s*\[\s*['\"]([A-Za-z_][\w-]*)['\"]\s*\]"
    ),
    re.compile(
        r"\b(?:request|req|payload|body|params|query|data)"
        r"(?:\.(?:json|args|form|query|body|data|params|headers|cookies|values|files))?"
        r"\.get\s*\(\s*['\"]([A-Za-z_][\w-]*)['\"]"
    ),
    re.compile(
        r"\b(?:request|req)"
        r"\.(?:json|args|form|query|body|data|params|headers|cookies|values|files)"
        r"\.(?!get\b)([A-Za-z_][\w-]*)\b"
    ),
)
_REQUEST_DESTRUCTURE_RE = re.compile(
    r"\{(?P<fields>[^{}]+)\}\s*=\s*"
    r"\b(?:request|req)"
    r"\.(?:json|args|form|query|body|data|params|headers|cookies|values|files)\b"
)
_ENV_FIELD_RES = (
    re.compile(
        r"\b(?:os\.)?environ"
        r"(?:\.(?:get|getenv))?\s*(?:\[\s*|\(\s*)['\"]([A-Za-z_][\w.-]*)['\"]"
    ),
    re.compile(r"\b(?:os\.)?getenv\s*\(\s*['\"]([A-Za-z_][\w.-]*)['\"]"),
    re.compile(r"\bprocess\.env\.([A-Za-z_][\w.-]*)\b"),
    re.compile(r"\bprocess\.env\s*\[\s*['\"]([A-Za-z_][\w.-]*)['\"]\s*\]"),
    re.compile(r"\bgetenv\s*\(\s*['\"]([A-Za-z_][\w.-]*)['\"]"),
)
_REGISTRATION_LINE_RE = re.compile(
    r"\b(?:[A-Z0-9_]*REGISTER[A-Z0-9_]*|register_[A-Za-z0-9_]+)\s*\("
    r"|\.[ \t]*(?:register|subscribe|add_listener|add_handler|add_job|schedule)\s*\(",
    re.IGNORECASE,
)
_DECORATOR_LINE_RE = re.compile(r"^\s*@(?P<decorator>[A-Za-z_][\w.]*)\b(?P<rest>.*)$")
_ENTRY_DECORATOR_KIND_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("webhook", ("webhook", "hook")),
    ("route", ("route", "router", "endpoint", "controller", "view", ".get", ".post", ".put",
               ".patch", ".delete", ".head", ".options", ".api_route")),
    ("api", ("api", "rpc", "grpc", "http", "request")),
    ("message", ("subscribe", "subscriber", "topic", "queue", "message", "event", "listener",
                 ".on", ".listen", "consumer")),
    ("scheduler", ("schedule", "scheduler", "scheduled", "cron", "job", ".task")),
    ("timer", ("timer", "timeout", "poller", "interval")),
    ("callback", ("callback", ".callback")),
)
_CALLBACK_ASSIGN_RE = re.compile(
    r"\.(?:[A-Za-z_]\w*(?:cb|callback|handler|fn|op|ops)|(?:cb|callback|handler|fn|op|ops))\s*="
    r"\s*(?P<symbol>[A-Za-z_]\w*)",
    re.IGNORECASE,
)
_ENTRY_DISCOVERY_KIND_LABELS = {
    "api": "公开 API/请求入口",
    "cli": "命令行入口",
    "message": "消息/事件入口",
    "webhook": "Webhook 入口",
    "route": "路由/端点入口",
    "endpoint": "路由/端点入口",
    "queue": "队列入口",
    "job": "任务入口",
    "scheduler": "调度入口",
    "config": "配置入口",
    "file": "文件输入入口",
    "callback": "注册回调入口",
    "timer": "定时任务入口",
    "service": "服务启动入口",
}

_PUBLIC_ENTRY_KIND_ALIASES = {
    "rpc",
    "http",
    "rest",
    "grpc",
    "event",
    "connection",
    "ui",
    "resource",
    "public",
    "controller",
    "consumer",
    "subscriber",
    "producer",
    "listener",
    "cron",
    "worker",
}

_PUBLIC_TRIGGER_SURFACE_TOKENS = (
    "rpc", "api", "cli", "command", "config", "message", "event", "timer",
    "callback", "service", "connection", "socket", "http", "request",
    "route", "router", "endpoint", "controller", "webhook", "hook delivery",
    "queue", "topic", "consumer", "subscriber", "producer", "job",
    "scheduler", "schedule", "cron", "worker", "listener", "notification",
)

_WHITE_BOX_LEAK_RULES: tuple[tuple[str, re.Pattern], ...] = (
    ("source_path", re.compile(r"\b[\w./\\-]+\.(?:c|h|cc|cpp|cxx|hpp|py|go|rs|java|js|jsx|ts|tsx)(?::\d+)?\b")),
    ("function_call", re.compile(r"\b[A-Za-z_]\w*\s*\(")),
    ("branch_expression", re.compile(r"\b(?:if|else\s+if|switch|while|for)\s*\(|\bcase\s+[^:]+:")),
    ("private_member", re.compile(r"\b[A-Za-z_]\w*(?:->|\.)[A-Za-z_]\w*\b")),
    ("gray_box_action", re.compile(r"\b(?:mock|stub|hook|fault[_ -]?injection)\b|覆盖(?:某)?(?:行|分支|if)", re.IGNORECASE)),
)


@dataclass(frozen=True)
class BranchFactCard:
    """Source/coverage facts. This card is evidence, not tester instructions."""

    uncovered_location: str
    branch_conditions: list[str]
    behavior_impact: str
    source_evidence: list[str]
    possible_observable_signals: list[str]


@dataclass(frozen=True)
class ExternalEntryCard:
    has_external_entry: bool
    entries: list[dict]
    missing_evidence: list[str]


@dataclass(frozen=True)
class BlackBoxReadinessCard:
    case_type: str
    has_external_entry: bool
    has_constructible_input: bool
    has_observable_signal: bool
    rationale: str


@dataclass(frozen=True)
class WhiteBoxLeakCheckResult:
    passed: bool
    findings: list[dict]
    action: str

# Entry classification heuristics: path/symbol fragments -> external entry kind.
_ENTRY_SIGNATURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cli", ("cli", "/cmd", "command", "argv", "getopt", "main(", "_main", "console", "shell")),
    ("webhook", ("webhook", "webhooks", "hook_handler", "hook_delivery")),
    ("route", ("route", "routes", "router", "controller", "view")),
    ("endpoint", ("endpoint", "endpoints", "servlet")),
    ("api", ("/api", "api_", "_api", "route", "router", "handler", "handle_request",
             "controller", "endpoint", "server", "rest", "grpc", "http", "rpc",
             "view", "/web", "servlet")),
    ("queue", ("queue", "topic", "consumer", "subscriber", "producer", "work_queue")),
    ("job", ("job", "jobs", "worker", "task", "background")),
    ("scheduler", ("scheduler", "schedule", "scheduled", "cron", "periodic")),
    ("message", ("message", "/msg", "event", "consumer", "subscriber", "publish", "queue",
                 "kafka", "/mq", "callback", "signal", "/irq", "isr", "dispatch", "listener",
                 "notify", "on_")),
    ("config", ("config", "/conf", "settings", "option", ".ini", ".yaml", ".yml", ".toml",
                "parse_args", "load_config", "env")),
    ("file", ("readfile", "read_file", "loadfile", "load_file", "fread", "fopen", "open(",
              "ingest", "import", "/io", "input", "stdin", "scan")),
)

COVERAGE_ANALYSIS_PROMPT = """\
你是一名资深测试工程师和代码分析专家。请根据以下代码覆盖率数据，分析未覆盖的代码分支和函数，\
并给出精准的测试建议。

## 模块信息
- 模块路径: {module_path}
- 行覆盖率: {line_rate:.1%}
- 分支覆盖率: {branch_rate:.1%}
- 函数覆盖率: {function_rate:.1%}

## 未覆盖的函数
{uncovered_functions}

## 未覆盖的代码行（示例）
{uncovered_lines}

## 未覆盖的分支
{uncovered_branches}

## 文件覆盖明细
{file_details}

## 输出要求
请用中文输出，包含以下部分（使用 Markdown 格式）：

### 1. 覆盖率概况分析
简要说明当前模块覆盖率状况，指出关键风险区域。

### 2. 未覆盖代码分支分析
列出最重要的未覆盖分支，说明这些分支可能涉及的业务场景。

### 3. 推荐测试点
按优先级列出需要补充的测试点，每个测试点包含：
- **测试目标**: 要验证什么
- **前置条件**: 测试所需环境和数据
- **关键断言**: 期望的行为

### 4. 推荐测试用例
给出 3-5 个具体的测试用例（伪代码或测试框架代码），覆盖最关键的未测试路径。

### 5. 优先级建议
根据代码复杂度和业务影响排列修复优先级。
"""


async def _analyze_module(
    llm: BaseLLMClient,
    module: ModuleCoverage,
) -> dict:
    """Use LLM to analyze a single module's coverage gaps."""
    file_details_lines = []
    for f in module.files[:20]:
        file_details_lines.append(
            f"- {f.filename}: 行覆盖 {f.line_rate:.1%}, "
            f"分支覆盖 {f.branch_rate:.1%}, "
            f"未覆盖行 {len(f.uncovered_lines)} 个"
        )

    prompt = COVERAGE_ANALYSIS_PROMPT.format(
        module_path=module.module_path,
        line_rate=module.line_rate,
        branch_rate=module.branch_rate,
        function_rate=module.function_rate,
        uncovered_functions="\n".join(
            f"- {fn}" for fn in module.uncovered_functions[:30]
        ) or "无",
        uncovered_lines="\n".join(module.uncovered_lines[:30]) or "无",
        uncovered_branches="\n".join(
            f"- {b}" for b in module.uncovered_branches[:30]
        ) or "无",
        file_details="\n".join(file_details_lines) or "无文件详情",
    )

    resp = await llm.complete(prompt, max_tokens=min(4096, settings.llm_max_output_tokens))

    return {
        "module_path": module.module_path,
        "line_rate": module.line_rate,
        "branch_rate": module.branch_rate,
        "function_rate": module.function_rate,
        "analysis": resp.text,
        "uncovered_function_count": len(module.uncovered_functions),
        "uncovered_branch_count": len(module.uncovered_branches),
    }


def _coverage_text(content: str | bytes) -> str:
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    return content


def _coverage_bytes(content: str | bytes) -> bytes:
    if isinstance(content, bytes):
        return content
    return content.encode("utf-8")


def _coverage_totals(modules: list[ModuleCoverage], source_format: str) -> tuple[float, float, float]:
    """Return aggregate rates.

    Internal function-hit reports do not contain real line/branch counters.  For
    those uploads, the honest aggregate is covered functions / total functions;
    branch coverage is unavailable and kept at 0 for backward-compatible API
    fields.
    """
    if source_format == "internal_function_hits":
        total = 0
        covered = 0
        for module in modules:
            hits = module.function_hits or [hit for f in module.files for hit in f.function_hits]
            total += len(hits)
            covered += sum(1 for hit in hits if hit.triggered or hit.hit_count > 0)
        function_rate = (covered / total) if total else 0.0
        return function_rate, 0.0, function_rate

    total_line = sum(m.line_rate for m in modules) / len(modules)
    total_branch = sum(m.branch_rate for m in modules) / len(modules)
    total_func = sum(m.function_rate for m in modules) / len(modules)
    return total_line, total_branch, total_func


class CoverageAnalyzer:
    """Orchestrates coverage parsing and AI analysis."""

    async def parse_and_store(
        self,
        analysis_id: str,
        files: list[tuple[str, str | bytes]],
        name: str = "",
        workspace_id: str | None = None,
        repo_path: str | None = None,
    ) -> CoverageReport:
        """Parse uploaded coverage files and store structured data."""
        merged_modules: list[ModuleCoverage] = []
        source_format = "mixed"

        for filename, content in files:
            lower = filename.lower()
            if lower.endswith(".xml"):
                try:
                    report = detect_and_parse_xml(_coverage_text(content))
                except Exception as exc:
                    raise ValueError(f"文件 {filename} XML 格式无效: {exc}") from exc
            elif lower.endswith((".html", ".htm")):
                report = parse_html_coverage(_coverage_text(content))
            elif lower.endswith(".xlsx"):
                try:
                    report = parse_internal_function_hits_xlsx(_coverage_bytes(content))
                except Exception as exc:
                    logger.warning("Skipping invalid Excel coverage file %s: %s", filename, exc)
                    continue
            elif lower.endswith(".xls"):
                try:
                    report = parse_internal_function_hits(_coverage_text(content))
                except Exception as exc:
                    logger.warning("Skipping invalid legacy Excel coverage file %s: %s", filename, exc)
                    continue
            elif lower.endswith((".csv", ".tsv", ".txt")):
                try:
                    report = parse_internal_function_hits(_coverage_text(content))
                except Exception as exc:
                    logger.warning("Skipping invalid internal coverage file %s: %s", filename, exc)
                    continue
            else:
                logger.warning("Skipping unsupported file: %s", filename)
                continue

            merged_modules.extend(report.modules)
            source_format = report.source_format

        if not merged_modules:
            raise ValueError("未能从上传文件中解析到任何覆盖率数据")

        total_line, total_branch, total_func = _coverage_totals(
            merged_modules, source_format
        )

        merged_report = CoverageReport(
            overall_line_rate=total_line,
            overall_branch_rate=total_branch,
            overall_function_rate=total_func,
            modules=merged_modules,
            source_format=source_format,
        )

        now = datetime.now(timezone.utc).isoformat()
        modules_json = json.dumps(
            [_module_to_dict(m) for m in merged_modules],
            ensure_ascii=False,
        )

        async with aiosqlite.connect(settings.sqlite_db) as db:
            await db.execute(
                """INSERT INTO coverage_analyses
                   (id, name, source_type, status, overall_line_rate,
                    overall_branch_rate, overall_function_rate,
                    module_count, modules_json, source_format, workspace_id,
                    repo_path, created_at, updated_at)
                   VALUES (?, ?, 'upload', 'parsed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    analysis_id,
                    name or f"覆盖率分析 {now[:10]}",
                    total_line,
                    total_branch,
                    total_func,
                    len(merged_modules),
                    modules_json,
                    source_format,
                    workspace_id,
                    repo_path,
                    now,
                    now,
                ),
            )
            await db.commit()

        logger.info(
            "Coverage parsed: %d modules, line=%.1f%%, branch=%.1f%%",
            len(merged_modules),
            total_line * 100,
            total_branch * 100,
        )

        return merged_report

    async def run_analysis(self, analysis_id: str) -> list[dict]:
        """Run AI analysis on a parsed coverage report."""
        # Atomic status transition: only one concurrent caller will see rowcount==1.
        # This eliminates the TOCTOU window between the status-check and the UPDATE.
        async with aiosqlite.connect(settings.sqlite_db) as db:
            now = datetime.now(timezone.utc).isoformat()
            cursor = await db.execute(
                "UPDATE coverage_analyses SET status = 'analyzing', updated_at = ? "
                "WHERE id = ? AND status IN ('parsed', 'analyzed')",
                (now, analysis_id),
            )
            await db.commit()
            if cursor.rowcount == 0:
                raise ValueError("分析已在进行中或状态不允许，请勿重复触发")

        # Fetch modules data after the atomic status transition is committed.
        async with aiosqlite.connect(settings.sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            row = await db.execute_fetchall(
                "SELECT modules_json, source_format, workspace_id, repo_path FROM coverage_analyses WHERE id = ?",
                (analysis_id,),
            )
            if not row:  # pragma: no cover
                raise ValueError(f"覆盖率分析 {analysis_id} 不存在")
            record = dict(row[0])
            modules_json = record["modules_json"]

        modules_data: list[dict] = json.loads(modules_json)
        modules = [_dict_to_module(d) for d in modules_data]

        if record.get("source_format") == "internal_function_hits":
            design = await build_coverage_test_design(
                modules,
                workspace_id=record.get("workspace_id"),
                repo_path=record.get("repo_path"),
                use_ai=True,
                artifact_dir=settings.outputs_path / "coverage" / analysis_id,
                analysis_id=analysis_id,
            )
            results = design.get("gaps") or []
            now = datetime.now(timezone.utc).isoformat()
            async with aiosqlite.connect(settings.sqlite_db) as db:
                await db.execute(
                    """UPDATE coverage_analyses
                       SET status = 'analyzed',
                           analysis_results_json = ?,
                           updated_at = ?
                       WHERE id = ?""",
                    (json.dumps(results, ensure_ascii=False), now, analysis_id),
                )
                await db.commit()
            return results

        low_coverage = [
            m for m in modules
            if m.line_rate < 0.8 or m.branch_rate < 0.6 or m.function_rate < 0.8
        ]
        targets = low_coverage or modules[:5]

        try:
            llm = await create_llm_client_from_active()
        except ValueError as exc:
            logger.warning("Coverage analysis skipped — no LLM configured: %s", exc)
            now = datetime.now(timezone.utc).isoformat()
            async with aiosqlite.connect(settings.sqlite_db) as db:
                await db.execute(
                    "UPDATE coverage_analyses SET status = 'parsed', updated_at = ? WHERE id = ?",
                    (now, analysis_id),
                )
                await db.commit()
            return []

        results: list[dict] = []

        for module in targets:
            try:
                result = await _analyze_module(llm, module)
                results.append(result)
            except Exception:
                logger.exception("Failed to analyze module %s", module.module_path)
                results.append({
                    "module_path": module.module_path,
                    "error": "AI 分析失败，请稍后重试",
                })

        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(settings.sqlite_db) as db:
            await db.execute(
                """UPDATE coverage_analyses
                   SET status = 'analyzed',
                       analysis_results_json = ?,
                       updated_at = ?
                   WHERE id = ?""",
                (json.dumps(results, ensure_ascii=False), now, analysis_id),
            )
            await db.commit()

        return results


async def _build_black_box_function_recommendations(
    modules: list[ModuleCoverage],
    *,
    workspace_id: str | None,
    repo_path: str | None,
    agent_session: AgentDiscoverySession | None = None,
) -> list[dict]:
    """Build entry-oriented test recommendations for uncovered function-hit rows.

    Each result is a *superset* of the legacy black-box recommendation dict: it
    keeps every legacy key (``scenario``, ``input_conditions``, ``confidence``,
    ``evidence`` …) and adds the coverage-test-design fields (``source_window``,
    ``trigger_branches``, ``entry_paths``, ``black_box_cases``, ``gray_box`` …).
    """
    uncovered = _collect_uncovered_function_hits(modules)
    prioritized = _prioritize_uncovered_hits(uncovered)
    design_targets = prioritized[:50]
    traced_keys = {
        _hit_key(hit)
        for _module, hit in prioritized[:MAX_TRACED_FUNCTION_GAPS]
    }
    scope_by_function = await _resolve_workspace_scope_for_hits(
        prioritized, workspace_id=workspace_id, repo_path=repo_path
    )
    cgc_by_function = await _resolve_cgc_context_for_hits(prioritized, repo_path=repo_path)
    agent_by_function = await _resolve_external_agent_entries_for_hits(
        prioritized, repo_path=repo_path, agent_session=agent_session
    )

    repo_root = _existing_repo_root(repo_path)
    rg_available = shutil.which("rg") is not None

    results: list[dict] = []
    for module, hit in design_targets:
        scope = scope_by_function.get(_hit_key(hit), {})
        cgc_context = cgc_by_function.get(_hit_key(hit), {})
        agent_context = agent_by_function.get(_hit_key(hit), {})
        # Only the highest-priority N gaps get the expensive source-window +
        # caller-chain trace; low-risk CSV rows must not starve later risky
        # recovery/error/auth functions of entry discovery.
        trace = _hit_key(hit) in traced_keys
        result = await asyncio.to_thread(
            _design_function_gap,
            module,
            hit,
            workspace_id=workspace_id,
            repo_path=repo_path,
            repo_root=repo_root,
            rg_available=rg_available,
            scope=scope,
            cgc_context=cgc_context,
            agent_context=agent_context,
            trace=trace,
        )
        results.append(result)

    return sorted(
        results,
        key=lambda r: {"high": 0, "medium": 1, "low": 2}[r["risk_level"]],
    )


def _prioritize_uncovered_hits(
    uncovered: list[tuple[ModuleCoverage, FunctionHit]],
) -> list[tuple[ModuleCoverage, FunctionHit]]:
    risk_order = {"high": 0, "medium": 1, "low": 2}
    ranked: list[tuple[int, int, tuple[ModuleCoverage, FunctionHit]]] = []
    for idx, item in enumerate(uncovered):
        _module, hit = item
        ranked.append((risk_order[_risk_level_for_hit(hit)], idx, item))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [item for _risk, _idx, item in ranked]


def _design_function_gap(
    module: ModuleCoverage,
    hit: FunctionHit,
    *,
    workspace_id: str | None,
    repo_path: str | None,
    repo_root: Path | None,
    rg_available: bool,
    scope: dict,
    cgc_context: dict,
    agent_context: dict | None = None,
    trace: bool,
) -> dict:
    """Build one uncovered-function gap with source-backed trigger analysis.

    Runs entirely synchronously (file reads + ripgrep); callers offload it to a
    thread.  Never raises — every external lookup degrades to an evidence gap.
    """
    risk_level = _risk_level_for_hit(hit)
    cgc_callers = cgc_context.get("callers") if isinstance(cgc_context, dict) else None

    source_window = _read_source_window(repo_root, hit) if trace else None
    if trace:
        scoped_source_window = _read_source_window_from_scope(repo_root, hit, scope)
        if (
            source_window is None
            or (
                scoped_source_window is not None
                and _source_window_is_function_fallback(repo_root, hit, source_window)
            )
        ):
            source_window = scoped_source_window or source_window
    self_branches = _branches_from_window(source_window, source="self")

    entry_paths: list[dict] = []
    trigger_branches: list[dict] = list(self_branches)
    if trace:
        entry_paths, caller_branches = _trace_entry_paths(
            repo_root,
            hit.function_name,
            source_file_hint=hit.file_path,
            rg_available=rg_available,
            cgc_callers=cgc_callers,
        )
        entry_paths = _merge_agent_entry_paths(entry_paths, agent_context, hit)
        trigger_branches = _dedupe_branches([*caller_branches, *self_branches])

    tool_status = _gap_tool_status(
        repo_root=repo_root,
        rg_available=rg_available,
        source_window=source_window,
        cgc_context=cgc_context,
        agent_context=agent_context or {},
        scope=scope,
    )
    entry_trace_status = _entry_trace_status(
        workspace_bound=repo_root is not None and bool(workspace_id),
        trace=trace,
        source_window=source_window,
        entry_paths=entry_paths,
        tool_status=tool_status,
    )
    has_black_box_entry = bool(entry_paths)
    gray_box_required = entry_trace_status == "source_read_ok_entry_not_found"
    evidence_gaps = _function_evidence_gaps(
        workspace_bound=repo_root is not None and bool(workspace_id),
        source_window=source_window,
        entry_paths=entry_paths,
        trigger_branches=trigger_branches,
        tool_status=tool_status,
    )
    branch_fact_card = _build_branch_fact_card(hit, source_window, trigger_branches)
    external_entry_card = _build_external_entry_card(entry_paths, evidence_gaps)
    readiness_card = _build_readiness_card(
        external_entry_card,
        branch_fact_card,
        evidence_gaps,
        gray_box_required=gray_box_required,
    )
    black_box_cases = _build_black_box_cases(hit, entry_paths, trigger_branches)
    gray_box = _build_gray_box_scheme(
        hit,
        repo_root=repo_root,
        cgc_callers=cgc_callers,
        trigger_branches=trigger_branches,
        required=gray_box_required,
    )
    test_case_drafts = _build_test_case_drafts(
        hit,
        black_box_cases,
        gray_box,
        branch_fact_card,
        external_entry_card,
        readiness_card,
    )
    white_box_leak_check = _lint_test_case_drafts(test_case_drafts)
    if not white_box_leak_check.get("passed") and readiness_card.get("case_type") == BLACK_BOX_READY:
        readiness_card = {
            **readiness_card,
            "case_type": BLACK_BOX_HYPOTHESIS,
            "rationale": readiness_card.get("rationale", "") + "; black-box execution leaked white-box terms and needs rewrite",
        }
        for draft in test_case_drafts:
            if draft.get("case_type") == BLACK_BOX_READY:
                draft["case_type"] = BLACK_BOX_HYPOTHESIS

    result = {
        "kind": "function",
        "module_path": module.module_path,
        "line_rate": module.line_rate,
        "branch_rate": module.branch_rate,
        "function_rate": module.function_rate,
        "feature_name": hit.feature_name,
        "function_name": hit.function_name,
        "file_path": hit.file_path,
        "line_start": hit.line_start,
        "line_end": hit.line_end,
        "hit_count": hit.hit_count,
        "risk_level": risk_level,
        "category": "black_box_function_gap",
        "scenario": _scenario_for_hit(hit),
        "input_conditions": _input_conditions_for_hit(hit),
        "expected_behavior": _expected_behavior_for_hit(hit),
        "observable_signals": _observable_signals_for_hit(hit),
        # coverage-test-design-v1 enrichment
        "source_window": source_window,
        "trigger_branches": trigger_branches,
        "entry_paths": entry_paths,
        "black_box_cases": black_box_cases,
        "gray_box": gray_box,
        "gray_box_required": gray_box_required,
        "entry_trace_status": entry_trace_status,
        "branch_fact_card": branch_fact_card,
        "external_entry_card": external_entry_card,
        "black_box_readiness": readiness_card,
        "test_case_drafts": test_case_drafts,
        "white_box_leak_check": white_box_leak_check,
        "evidence_gaps": evidence_gaps,
        "tool_status": tool_status,
        "confidence": _confidence_for_gap(scope, cgc_context, source_window, entry_paths),
        "evidence": {
            "coverage": {
                "workspace_id": workspace_id,
                "repo_path": repo_path,
                "module_path": module.module_path,
                "feature_name": hit.feature_name,
                "module_name": hit.module_name,
                "file_path": hit.file_path,
                "function_name": hit.function_name,
                "line_start": hit.line_start,
                "line_end": hit.line_end,
                "triggered": hit.triggered,
                "hit_count": hit.hit_count,
            },
            "gitnexus_scope": scope,
            "cgc": cgc_context,
            "external_agent": agent_context or {},
        },
    }
    result["analysis"] = _recommendation_markdown(result)
    return result


def _merge_agent_entry_paths(
    entry_paths: list[dict],
    agent_context: dict | None,
    hit: FunctionHit,
) -> list[dict]:
    if not isinstance(agent_context, dict):
        return _filter_actionable_entry_paths(entry_paths)
    merged = _filter_actionable_entry_paths(entry_paths)
    by_entry_key = {
        _entry_execution_key(entry): entry
        for entry in merged
        if _entry_execution_key(entry)
    }
    for item in agent_context.get("validated_entries") or []:
        if not _entry_path_is_actionable(item):
            continue
        if _agent_entry_is_self_target(item, hit):
            continue
        if _agent_entry_chain_missing_target(item, hit.function_name):
            continue
        if not _agent_entry_has_public_trigger_surface(item):
            continue
        key = _entry_execution_key(item)
        if key and key in by_entry_key:
            _merge_agent_entry_confirmation(by_entry_key[key], item)
            continue
        chain = _normalize_agent_entry_chain(item.get("chain"))
        if hit.function_name and not chain:
            chain.append(hit.function_name)
        entry_kind = item.get("entry_kind") or "external"
        entry_symbol = item.get("entry_symbol") or item.get("entry_label")
        new_entry = {
            "entry_kind": entry_kind,
            "entry_symbol": entry_symbol,
            "entry_file": item.get("entry_file"),
            "entry_label": item.get("external_trigger")
            or _public_entry_label(entry_kind, entry_symbol)
            or entry_symbol
            or "External agent entry",
            "external_trigger": item.get("external_trigger"),
            "chain": chain,
            "evidence": item.get("reason") or item.get("external_trigger"),
            "tool": item.get("provider") or "external_agent",
            "provider": item.get("provider") or "external_agent",
            "turn_id": item.get("turn_id"),
            "source_verification": item.get("source_verification") or "source_backed",
            "validation_error": item.get("validation_error"),
            "input_hints": _coerce_string_list(item.get("input_hints")),
        }
        merged.append(new_entry)
        if key:
            by_entry_key[key] = new_entry
    return merged


def _filter_actionable_entry_paths(entry_paths: list[dict]) -> list[dict]:
    return [
        entry for entry in entry_paths
        if _entry_path_is_actionable(entry)
    ]


def _entry_path_is_actionable(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    if str(entry.get("validation_error") or "").strip():
        return False
    source_verification = str(entry.get("source_verification") or "").strip().lower()
    if source_verification in {
        "needs_source_verification",
        "unverified",
        "rejected",
        "invalid",
    }:
        return False
    return True


def _entry_execution_key(entry: dict) -> tuple[str, str] | None:
    symbol = str(entry.get("entry_symbol") or entry.get("entry_label") or "").strip()
    file_path = _entry_file_key(entry)
    if not symbol or not file_path:
        return None
    return (symbol, file_path)


def _entry_file_key(entry: dict) -> str:
    return str(entry.get("entry_file") or "").replace("\\", "/").strip().lower()


def _merge_agent_entry_confirmation(existing: dict, agent_entry: dict) -> None:
    _merge_agent_entry_label_confirmation(existing, agent_entry)
    provider = str(agent_entry.get("provider") or "external_agent").strip()
    if provider:
        if not existing.get("provider"):
            existing["provider"] = provider
        providers = list(existing.get("confirming_providers") or [])
        if provider not in providers:
            providers.append(provider)
        existing["confirming_providers"] = providers
    turn_id = str(agent_entry.get("turn_id") or "").strip()
    if turn_id:
        if not existing.get("turn_id"):
            existing["turn_id"] = turn_id
        turn_ids = list(existing.get("confirming_turn_ids") or [])
        if turn_id not in turn_ids:
            turn_ids.append(turn_id)
        existing["confirming_turn_ids"] = turn_ids
    source_verification = str(agent_entry.get("source_verification") or "").strip()
    if source_verification and not existing.get("source_verification"):
        existing["source_verification"] = source_verification
    input_hints = _merge_ordered_strings(existing.get("input_hints"), agent_entry.get("input_hints"))
    if input_hints:
        existing["input_hints"] = input_hints
    reason = str(agent_entry.get("reason") or "").strip()
    if reason:
        confirmations = list(existing.get("confirming_evidence") or [])
        if reason not in confirmations:
            confirmations.append(reason)
        existing["confirming_evidence"] = confirmations[:4]


def _merge_agent_entry_label_confirmation(existing: dict, agent_entry: dict) -> None:
    agent_kind = str(agent_entry.get("entry_kind") or "").strip().lower()
    agent_symbol = agent_entry.get("entry_symbol") or agent_entry.get("entry_label")
    agent_label = (
        str(agent_entry.get("external_trigger") or "").strip()
        or _public_entry_label(agent_kind, agent_symbol)
    )
    if not agent_kind or not agent_label:
        return
    current_kind = str(existing.get("entry_kind") or "").strip().lower()
    if current_kind in {"", "api", "external", "public"} and agent_kind not in {
        "api",
        "external",
        "public",
    }:
        existing["entry_kind"] = agent_kind
        existing["entry_label"] = agent_label
    elif not str(existing.get("entry_label") or "").strip():
        existing["entry_label"] = agent_label


def _agent_entry_is_self_target(item: dict, hit: FunctionHit) -> bool:
    function_name = str(hit.function_name or "").strip()
    if not function_name:
        return False
    entry_symbol = str(item.get("entry_symbol") or item.get("entry_label") or "").strip()
    if entry_symbol and entry_symbol != function_name:
        return False
    chain = _normalize_agent_entry_chain(item.get("chain"))
    if chain and any(value != function_name for value in chain):
        return False
    entry_file = str(item.get("entry_file") or "").replace("\\", "/")
    hit_file = str(hit.file_path or "").replace("\\", "/")
    if entry_file and hit_file and entry_file != hit_file and not hit_file.endswith(entry_file):
        return False
    return bool(entry_symbol == function_name or chain == [function_name])


def _agent_entry_has_public_trigger_surface(item: dict) -> bool:
    kind = str(item.get("entry_kind") or "").strip().lower()
    if kind in _ENTRY_DISCOVERY_KIND_LABELS or kind in _PUBLIC_ENTRY_KIND_ALIASES:
        return True
    trigger = str(item.get("external_trigger") or item.get("entry_label") or "").lower()
    if trigger and any(token in trigger for token in _PUBLIC_TRIGGER_SURFACE_TOKENS):
        return True
    return False


def _agent_entry_chain_missing_target(item: dict, function_name: object) -> bool:
    target = str(function_name or "").strip()
    if not target:
        return False
    chain = _normalize_agent_entry_chain(item.get("chain"))
    return bool(chain and target not in chain)


def _normalize_agent_entry_chain(value: object) -> list[str]:
    chain: list[str] = []
    seen: set[str] = set()
    for item in _coerce_string_list(value):
        for segment in _split_agent_entry_chain_text(item):
            if segment in seen:
                continue
            seen.add(segment)
            chain.append(segment)
    return chain


def _split_agent_entry_chain_text(value: object) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    return [
        segment.strip()
        for segment in re.split(r"\s*(?:->|=>|\u2192|\u21d2|,|;|\||\r?\n)\s*", text)
        if segment.strip()
    ]


def _entry_trace_status(
    *,
    workspace_bound: bool,
    trace: bool,
    source_window: dict | None,
    entry_paths: list[dict],
    tool_status: dict,
) -> str:
    if not workspace_bound:
        return "workspace_not_bound"
    if not trace:
        return "trace_skipped_by_cap"
    if not source_window:
        return "source_not_found"
    if entry_paths:
        return "entry_found"
    if tool_status.get("ripgrep") != "available":
        return "tool_unavailable"
    return "source_read_ok_entry_not_found"


def _collect_uncovered_function_hits(
    modules: list[ModuleCoverage],
) -> list[tuple[ModuleCoverage, FunctionHit]]:
    uncovered: list[tuple[ModuleCoverage, FunctionHit]] = []
    for module in modules:
        hits = module.function_hits
        if not hits:
            hits = [hit for f in module.files for hit in f.function_hits]
        for hit in hits:
            if not (hit.triggered or hit.hit_count > 0):
                uncovered.append((module, hit))
    return uncovered


async def _resolve_workspace_scope_for_hits(
    uncovered: list[tuple[ModuleCoverage, FunctionHit]],
    *,
    workspace_id: str | None,
    repo_path: str | None,
) -> dict[str, dict]:
    if not workspace_id or not repo_path or not Path(repo_path).exists() or not uncovered:
        return {}

    try:
        from app.schemas.workspace_analysis import AnalysisObject, AnalysisPlan, LLMLimits
        from app.services.workspace_scope_resolver import WorkspaceScopeResolver

        objects = [
            AnalysisObject(
                id=f"cov_{idx}",
                text=hit.function_name,
                kind="function",
                priority="high" if _risk_level_for_hit(hit) == "high" else "medium",
                path_hints=[hit.file_path],
            )
            for idx, (_, hit) in enumerate(uncovered[:24])
        ]
        plan = AnalysisPlan(
            analysis_objects=objects,
            llm_limits=LLMLimits(
                max_files_per_object=4,
                max_functions_per_object=6,
                max_communities_per_object=4,
                max_analysis_units=24,
            ),
        )
        preview = await asyncio.wait_for(
            WorkspaceScopeResolver().resolve(
                ws_id=workspace_id,
                repo_path=repo_path,
                plan=plan,
                external_agents_enabled=False,
            ),
            timeout=WORKSPACE_SCOPE_ENRICHMENT_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.info("Coverage workspace scope enrichment unavailable: %s", exc)
        return {}

    by_key: dict[str, dict] = {}
    for idx, resolved in enumerate(preview.resolved_objects):
        if idx >= len(uncovered):
            continue
        _, hit = uncovered[idx]
        by_key[_hit_key(hit)] = {
            "gitnexus_available": preview.gitnexus_available,
            "candidate_files": [c.model_dump() for c in resolved.candidate_files[:6]],
            "candidate_symbols": [c.model_dump() for c in resolved.candidate_symbols[:6]],
            "related_communities": resolved.related_communities[:4],
            "warnings": [*preview.warnings, *resolved.warnings],
        }
    return by_key


async def _resolve_cgc_context_for_hits(
    uncovered: list[tuple[ModuleCoverage, FunctionHit]],
    *,
    repo_path: str | None,
) -> dict[str, dict]:
    if not repo_path or not Path(repo_path).exists() or not uncovered:
        return {}
    try:
        from app.adapters.cgc import CGCClient

        cgc = CGCClient(timeout=2.0)
        if not await cgc.is_healthy():
            await cgc.close()
            return {}

        contexts: dict[str, dict] = {}
        for _, hit in uncovered[:12]:
            callers = await cgc.find_callers(hit.function_name, repo_path=repo_path)
            callees = await cgc.find_callees(hit.function_name, repo_path=repo_path)
            contexts[_hit_key(hit)] = {
                "available": True,
                "callers": _summarize_cgc_items(callers),
                "callees": _summarize_cgc_items(callees),
            }
        await cgc.close()
        return contexts
    except Exception as exc:
        logger.info("Coverage CGC enrichment unavailable: %s", exc)
        return {}


async def _resolve_external_agent_entries_for_hits(
    uncovered: list[tuple[ModuleCoverage, FunctionHit]],
    *,
    repo_path: str | None,
    agent_session: AgentDiscoverySession | None = None,
) -> dict[str, dict]:
    repo_root = _existing_repo_root(repo_path)
    if repo_root is None or not uncovered or not settings.external_agents_enabled:
        return {}

    async def _one(module: ModuleCoverage, hit: FunctionHit) -> tuple[str, dict]:
        object_id = _hit_key(hit)
        if agent_session is not None:
            agent_session.objects.append({
                "object_id": object_id,
                "function_name": hit.function_name,
                "file_path": hit.file_path,
                "module_path": module.module_path,
                "goal": "coverage_entry",
            })
            context_packet = agent_session.build_context_packet(
                AgentContextPacketInput(
                    object_id=object_id,
                    current_goal="coverage_entry",
                    analysis_object_text=hit.function_name,
                    expanded_terms=[hit.function_name, hit.file_path, module.module_path],
                    path_hints=[hit.file_path] if hit.file_path else [],
                    coverage_hit={
                        "function_name": hit.function_name,
                        "file_path": hit.file_path,
                        "line_start": hit.line_start,
                        "module_path": module.module_path,
                    },
                    round_index=1,
                )
            )
        else:
            context_packet = None
        request = AgentDiscoveryRequest(
            request_id=f"coverage:{object_id}",
            repo_path=str(repo_root),
            analysis_object_text=hit.function_name,
            path_hints=[hit.file_path] if hit.file_path else [],
            coverage_hit={
                "function_name": hit.function_name,
                "file_path": hit.file_path,
                "line_start": hit.line_start,
                "module_path": module.module_path,
            },
            existing_candidates=[],
            context_packet=context_packet,
            goal="coverage_entry",
        )
        try:
            results = await run_external_agent_discovery(request, session=agent_session)
        except Exception as exc:
            logger.info("Coverage external-agent discovery failed for %s: %s", hit.function_name, exc)
            status_by_provider: dict[str, str] = {}
            raw_results: list[dict] = []
            _record_agent_round_error(
                provider_status=status_by_provider,
                raw_results=raw_results,
                turn_id=f"coverage:{object_id}",
                exc=exc,
            )
            return object_id, {
                "status": "error",
                "provider_status": status_by_provider,
                "validated_entries": [],
                "unverified_entries": [],
                "raw_results": raw_results,
                "warnings": [str(exc)],
            }

        validated_entries: list[dict] = []
        unverified_entries: list[dict] = []
        status_by_provider: dict[str, str] = {}
        raw_results: list[dict] = []
        _collect_agent_entry_results(
            results,
            repo_root=repo_root,
            object_id=object_id,
            turn_id=f"coverage:{object_id}",
            agent_session=agent_session,
            validated_entries=validated_entries,
            unverified_entries=unverified_entries,
            status_by_provider=status_by_provider,
            raw_results=raw_results,
        )
        explicit_slice_requests = any(result.need_source_slices for result in results)
        if (
            agent_session is not None
            and settings.agent_discovery_max_rounds > 1
            and not validated_entries
            and (explicit_slice_requests or unverified_entries)
        ):
            for result in results:
                if result.need_source_slices:
                    agent_session.add_source_slices_from_requests(
                        result.need_source_slices,
                        object_id=object_id,
                    )
            round2_packet = agent_session.build_context_packet(
                AgentContextPacketInput(
                    object_id=object_id,
                    current_goal="coverage_entry",
                    analysis_object_text=hit.function_name,
                    expanded_terms=[hit.function_name, hit.file_path, module.module_path],
                    path_hints=[hit.file_path] if hit.file_path else [],
                    coverage_hit={
                        "function_name": hit.function_name,
                        "file_path": hit.file_path,
                        "line_start": hit.line_start,
                        "module_path": module.module_path,
                    },
                    round_index=2,
                )
            )
            round2_turn_id = f"coverage:{object_id}:round2"
            try:
                round2_results = await run_external_agent_discovery(
                    AgentDiscoveryRequest(
                        request_id=round2_turn_id,
                        repo_path=str(repo_root),
                        analysis_object_text=hit.function_name,
                        path_hints=[hit.file_path] if hit.file_path else [],
                        coverage_hit={
                            "function_name": hit.function_name,
                            "file_path": hit.file_path,
                            "line_start": hit.line_start,
                            "module_path": module.module_path,
                        },
                        context_packet=round2_packet,
                        goal="coverage_entry",
                    ),
                    session=agent_session,
                )
            except Exception as exc:
                logger.info(
                    "Coverage external-agent round2 discovery failed for %s: %s",
                    hit.function_name,
                    exc,
                )
                _record_agent_round_error(
                    provider_status=status_by_provider,
                    raw_results=raw_results,
                    turn_id=round2_turn_id,
                    exc=exc,
                )
            else:
                _collect_agent_entry_results(
                    round2_results,
                    repo_root=repo_root,
                    object_id=object_id,
                    turn_id=round2_turn_id,
                    agent_session=agent_session,
                    validated_entries=validated_entries,
                    unverified_entries=unverified_entries,
                    status_by_provider=status_by_provider,
                    raw_results=raw_results,
                )
        return object_id, {
            "status": _external_agent_status_from_provider_status(status_by_provider),
            "provider_status": status_by_provider,
            "validated_entries": validated_entries,
            "unverified_entries": unverified_entries,
            "raw_results": raw_results,
        }

    try:
        parallel_limit = int(getattr(settings, "external_agent_max_parallel", 2) or 1)
    except (TypeError, ValueError):
        parallel_limit = 1
    agent_semaphore = asyncio.Semaphore(max(1, parallel_limit))

    async def _one_safe(module: ModuleCoverage, hit: FunctionHit) -> tuple[str, dict]:
        try:
            async with agent_semaphore:
                return await _one(module, hit)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            object_id = _hit_key(hit)
            logger.info(
                "Coverage external-agent processing failed for %s: %s",
                hit.function_name,
                exc,
            )
            status_by_provider: dict[str, str] = {}
            raw_results: list[dict] = []
            _record_agent_round_error(
                provider_status=status_by_provider,
                raw_results=raw_results,
                turn_id=f"coverage:{object_id}",
                exc=exc,
            )
            return object_id, {
                "status": "error",
                "provider_status": status_by_provider,
                "validated_entries": [],
                "unverified_entries": [],
                "raw_results": raw_results,
                "warnings": [str(exc)],
            }

    pairs = await asyncio.gather(*[
        _one_safe(module, hit) for module, hit in uncovered[:MAX_TRACED_FUNCTION_GAPS]
    ])
    return {key: value for key, value in pairs}


def _collect_agent_entry_results(
    results: list,
    *,
    repo_root: Path,
    object_id: str,
    turn_id: str,
    agent_session: AgentDiscoverySession | None,
    validated_entries: list[dict],
    unverified_entries: list[dict],
    status_by_provider: dict[str, str],
    raw_results: list[dict],
) -> None:
    for result in results:
        result_turn_id = getattr(result, "turn_id", None) or turn_id
        status_by_provider[result.provider] = result.status
        raw_results.append({
            "provider": result.provider,
            "turn_id": result_turn_id,
            "status": result.status,
            "candidate_file_count": len(result.candidate_files),
            "candidate_entry_count": len(result.candidate_entries),
            "need_source_slice_count": len(getattr(result, "need_source_slices", []) or []),
            "warnings": result.warnings,
            "raw_summary": result.raw_summary,
        })
        for entry in result.candidate_entries:
            item = {
                "object_id": object_id,
                "provider": result.provider,
                "turn_id": result_turn_id,
                "entry_kind": entry.entry_kind,
                "entry_symbol": entry.entry_symbol,
                "entry_file": entry.entry_file,
                "chain": entry.chain,
                "external_trigger": entry.external_trigger,
                "input_hints": entry.input_hints,
                "reason": entry.reason,
                "source_verification": "source_backed" if entry.validated else "needs_source_verification",
                "validation_error": entry.validation_error,
            }
            if entry.entry_file:
                validation = validate_agent_candidate_file(
                    repo_root,
                    entry.entry_file,
                    allow_directory_candidates=False,
                )
                if (
                    not validation.validated
                    and validation.validation_error == "directory_candidate_not_allowed"
                    and entry.entry_symbol
                    and validation.resolved_path
                ):
                    resolved_entry_file = _resolve_entry_file_from_directory_symbol(
                        repo_root,
                        Path(validation.resolved_path),
                        entry.entry_symbol,
                    )
                    if resolved_entry_file is not None:
                        validation.path = _relative_path(repo_root, resolved_entry_file)
                        validation.resolved_path = str(resolved_entry_file)
                        validation.validated = True
                        validation.validation_error = None
                if validation.validated and entry.entry_symbol and validation.resolved_path:
                    rebound_entry_file = _rebind_entry_file_to_symbol_definition(
                        repo_root,
                        Path(validation.resolved_path),
                        entry.entry_symbol,
                    )
                    if rebound_entry_file is not None:
                        validation.path = _relative_path(repo_root, rebound_entry_file)
                        validation.resolved_path = str(rebound_entry_file)
                item["entry_file"] = validation.path or entry.entry_file
                if validation.validated:
                    item["source_verification"] = "source_backed"
                    item["validation_error"] = None
                    _upsert_agent_entry(validated_entries, item)
                    if agent_session is not None:
                        agent_session.ledger.add_validated_entry(item)
                else:
                    item["source_verification"] = "needs_source_verification"
                    item["validation_error"] = validation.validation_error
                    _upsert_agent_entry(unverified_entries, item)
                    if agent_session is not None:
                        agent_session.ledger.add_rejected_entry(item)
            else:
                resolved_entry_file = (
                    _resolve_entry_file_from_symbol(repo_root, entry.entry_symbol)
                    if entry.entry_symbol else None
                )
                if resolved_entry_file is not None:
                    item["entry_file"] = _relative_path(repo_root, resolved_entry_file)
                    item["source_verification"] = "source_backed"
                    item["validation_error"] = None
                    _upsert_agent_entry(validated_entries, item)
                    if agent_session is not None:
                        agent_session.ledger.add_validated_entry(item)
                else:
                    item["source_verification"] = "needs_source_verification"
                    item["validation_error"] = item.get("validation_error") or "entry_file_missing"
                    _upsert_agent_entry(unverified_entries, item)
                    if agent_session is not None:
                        agent_session.ledger.add_rejected_entry(item)
    if agent_session is not None:
        agent_session.save()


def _resolve_entry_file_from_directory_symbol(
    repo_root: Path,
    directory: Path,
    entry_symbol: str,
) -> Path | None:
    try:
        resolved_dir = directory.resolve()
        if not resolved_dir.is_dir() or not _is_within(repo_root, resolved_dir):
            return None
    except OSError:
        return None
    found = _find_source_file_defining_function(repo_root, resolved_dir, entry_symbol)
    if found is None:
        return None
    return found.resolve()


def _resolve_entry_file_from_symbol(repo_root: Path, entry_symbol: str) -> Path | None:
    if not entry_symbol:
        return None
    found = _find_source_file_defining_function(repo_root, repo_root, entry_symbol)
    if found is None:
        return None
    return found.resolve()


def _rebind_entry_file_to_symbol_definition(
    repo_root: Path,
    current_file: Path,
    entry_symbol: str,
) -> Path | None:
    try:
        resolved_current = current_file.resolve()
        if not resolved_current.is_file() or not _is_within(repo_root, resolved_current):
            return None
    except OSError:
        return None
    if _source_file_defines_function(resolved_current, entry_symbol):
        return None
    symbol_file = _resolve_entry_file_from_symbol(repo_root, entry_symbol)
    if symbol_file is None or symbol_file == resolved_current:
        return None
    return symbol_file


def _record_agent_round_error(
    *,
    provider_status: dict[str, str],
    raw_results: list[dict],
    turn_id: str,
    exc: Exception,
) -> None:
    summary = str(exc).strip() or exc.__class__.__name__
    provider_status["external_agent"] = "error"
    raw_results.append({
        "provider": "external_agent",
        "turn_id": turn_id,
        "status": "error",
        "candidate_file_count": 0,
        "candidate_entry_count": 0,
        "need_source_slice_count": 0,
        "warnings": [summary],
        "raw_summary": summary,
    })


def _upsert_agent_entry(target: list[dict], item: dict) -> None:
    key = (
        str(item.get("object_id") or ""),
        str(item.get("provider") or ""),
        str(item.get("entry_symbol") or ""),
        str(item.get("entry_file") or ""),
        str(item.get("validation_error") or ""),
    )
    for existing in target:
        existing_key = (
            str(existing.get("object_id") or ""),
            str(existing.get("provider") or ""),
            str(existing.get("entry_symbol") or ""),
            str(existing.get("entry_file") or ""),
            str(existing.get("validation_error") or ""),
        )
        if existing_key == key:
            item = {
                **item,
                "external_trigger": _prefer_non_empty_text(
                    existing.get("external_trigger"),
                    item.get("external_trigger"),
                ),
                "reason": _prefer_non_empty_text(
                    existing.get("reason"),
                    item.get("reason"),
                ),
                "input_hints": _merge_ordered_strings(
                    existing.get("input_hints"),
                    item.get("input_hints"),
                ),
                "chain": _merge_ordered_strings(
                    existing.get("chain"),
                    item.get("chain"),
                ),
            }
            existing.update(item)
            return
    target.append(item)


def _prefer_non_empty_text(existing: object, incoming: object) -> str:
    incoming_text = str(incoming).strip() if incoming is not None else ""
    if incoming_text:
        return incoming_text
    return str(existing).strip() if existing is not None else ""


def _merge_ordered_strings(*values: object) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, list):
            continue
        for item in value:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
    return merged


def _summarize_cgc_items(items: object) -> list[dict]:
    if not isinstance(items, list):
        return []
    summary: list[dict] = []
    for item in items[:8]:
        if isinstance(item, dict):
            summary.append({
                "name": item.get("name") or item.get("function") or item.get("caller"),
                "location": item.get("location") or item.get("file") or item.get("path"),
            })
        else:
            summary.append({"name": str(item), "location": None})
    return summary


def _hit_key(hit: FunctionHit) -> str:
    return f"{hit.file_path}:{hit.function_name}:{hit.line_start or ''}"


def _risk_level_for_hit(hit: FunctionHit) -> str:
    text = f"{hit.function_name} {hit.file_path}".lower()
    high_terms = (
        "error", "fail", "recover", "rollback", "cleanup", "free", "close",
        "timeout", "retry", "auth", "permission", "security", "panic",
    )
    medium_terms = (
        "init", "start", "stop", "config", "parse", "validate", "state",
        "open", "read", "write", "connect", "disconnect",
    )
    if any(term in text for term in high_terms):
        return "high"
    if any(term in text for term in medium_terms):
        return "medium"
    return "low"


def _scenario_for_hit(hit: FunctionHit) -> str:
    name = hit.function_name.replace("_", " ")
    return f"补充能够从外部触发 `{name}` 对应行为的测试流程。"


def _input_conditions_for_hit(hit: FunctionHit) -> str:
    text = hit.function_name.lower()
    if any(term in text for term in ("error", "fail", "recover", "rollback")):
        return "构造外部可见的失败条件：非法输入、依赖不可用、超时或重试耗尽。"
    if any(term in text for term in ("cleanup", "free", "close", "stop")):
        return "让流程分别走正常完成和强制中断，观察资源释放和状态回收。"
    if any(term in text for term in ("parse", "validate", "config")):
        return "准备合法、边界、畸形和缺失的配置或请求数据。"
    return "通过最近的公开 API、CLI、页面操作、消息、配置或文件输入触发该行为。"


def _expected_behavior_for_hit(hit: FunctionHit) -> str:
    return (
        "用户可见流程应返回文档化结果或受控错误；不能静默成功、崩溃、卡死、泄漏资源或留下不一致状态。"
    )


def _observable_signals_for_hit(hit: FunctionHit) -> list[str]:
    signals = ["返回值/响应码", "用户可见状态", "日志"]
    text = hit.function_name.lower()
    if any(term in text for term in ("cleanup", "free", "close")):
        signals.append("资源计数回到基线")
    if any(term in text for term in ("state", "start", "stop", "recover")):
        signals.append("状态切换可从外部观察")
    return signals


def _build_branch_fact_card(
    hit: FunctionHit,
    source_window: dict | None,
    trigger_branches: list[dict],
) -> dict:
    source_evidence = [
        f"{hit.file_path}:{hit.line_start or '?'} hit_count={hit.hit_count}",
    ]
    if source_window and source_window.get("available"):
        source_evidence.append(
            "{path}:{start}-{end}".format(
                path=source_window.get("path"),
                start=source_window.get("start"),
                end=source_window.get("end"),
            )
        )
    branch_conditions = [
        str(branch.get("condition"))
        for branch in trigger_branches
        if branch.get("condition")
    ]
    card = BranchFactCard(
        uncovered_location=f"{hit.file_path}:{hit.line_start or '?'}-{hit.line_end or '?'}",
        branch_conditions=branch_conditions[:8],
        behavior_impact=_expected_behavior_for_hit(hit),
        source_evidence=source_evidence,
        possible_observable_signals=_observable_signals_for_hit(hit),
    )
    return asdict(card)


def _build_external_entry_card(
    entry_paths: list[dict],
    evidence_gaps: list[str],
) -> dict:
    entries = [
        {
            "entry_kind": entry.get("entry_kind"),
            "entry_label": entry.get("entry_label") or entry.get("entry_symbol") or entry.get("entry_kind"),
            "external_trigger": entry.get("external_trigger"),
            "input_hints": entry.get("input_hints") or [],
            "evidence": entry.get("evidence"),
            "tool": entry.get("tool"),
            "provider": entry.get("provider"),
            "turn_id": entry.get("turn_id"),
            "source_verification": entry.get("source_verification"),
            "validation_error": entry.get("validation_error"),
        }
        for entry in _filter_actionable_entry_paths(entry_paths)[:6]
    ]
    return asdict(ExternalEntryCard(
        has_external_entry=bool(entries),
        entries=entries,
        missing_evidence=evidence_gaps[:6],
    ))


def _build_readiness_card(
    entry_card: dict,
    branch_fact_card: dict,
    evidence_gaps: list[str],
    *,
    gray_box_required: bool,
) -> dict:
    has_external_entry = bool(entry_card.get("has_external_entry"))
    has_observable_signal = bool(branch_fact_card.get("possible_observable_signals"))
    has_constructible_input = has_external_entry
    if has_external_entry and has_constructible_input and has_observable_signal:
        case_type = BLACK_BOX_READY
        rationale = "external entry, input construction, and observable signals are all present"
    elif gray_box_required:
        case_type = GRAY_BOX_REQUIRED
        rationale = "no confirmed external entry; use gray-box injection/observation"
    else:
        case_type = BLACK_BOX_HYPOTHESIS
        rationale = "external behavior is plausible but evidence is incomplete"
    if evidence_gaps and case_type == BLACK_BOX_READY:
        rationale += "; evidence gaps remain in appendix"
    return asdict(BlackBoxReadinessCard(
        case_type=case_type,
        has_external_entry=has_external_entry,
        has_constructible_input=has_constructible_input,
        has_observable_signal=has_observable_signal,
        rationale=rationale,
    ))


def _safe_external_label(entry: dict) -> str:
    for key in ("entry_label", "external_trigger", "entry_symbol"):
        label = str(entry.get(key) or "").strip()
        if label and _safe_external_label_text(label):
            return label
    kind = str(entry.get("entry_kind") or "public").strip()
    return f"{kind} entry"


def _public_entry_label(entry_kind: object, symbol: object) -> str | None:
    symbol_text = str(symbol or "").strip()
    if not symbol_text:
        return None
    kind = str(entry_kind or "").strip().lower()
    labels = {
        "rpc": "RPC",
        "api": "API",
        "http": "HTTP",
        "rest": "REST",
        "grpc": "gRPC",
        "cli": "CLI",
        "command": "CLI",
        "config": "config",
        "file": "file input",
        "message": "message",
        "event": "event",
        "timer": "timer",
        "callback": "callback",
        "service": "service",
        "connection": "connection",
        "public": "public",
    }
    label = labels.get(kind)
    return f"{label} {symbol_text}" if label else None


def _safe_external_label_text(label: str) -> bool:
    text = str(label or "").strip()
    if not text:
        return False
    if text.startswith("JSON-RPC "):
        return True
    if _lint_black_box_text(text):
        return False
    if re.fullmatch(r"[A-Za-z_]\w*", text) and "_" in text:
        return False
    return True


def _lint_black_box_text(text: str) -> list[dict]:
    findings: list[dict] = []
    for rule, pattern in _WHITE_BOX_LEAK_RULES:
        for match in pattern.finditer(text or ""):
            if rule == "function_call" and _function_call_looks_like_public_surface(text or "", match):
                continue
            findings.append({
                "rule": rule,
                "text": match.group(0)[:120],
            })
            break
    return findings


def _function_call_looks_like_public_surface(text: str, match: re.Match) -> bool:
    prefix = text[max(0, match.start() - 40): match.start()].lower()
    if re.search(r"(?:\bcall|\binvoke|调用)\s*$", prefix):
        return False
    window = text[max(0, match.start() - 80): min(len(text), match.end() + 60)].lower()
    public_tokens = (
        "json-rpc",
        "rpc",
        "cli",
        "command",
        "api",
        "http",
        "rest",
        "grpc",
        "endpoint",
        "request",
        "management",
        "client",
        "public",
        "公开",
        "命令",
        "接口",
        "请求",
        "客户端",
    )
    return any(token in window for token in public_tokens)


def _lint_test_case_drafts(drafts: list[dict]) -> dict:
    findings: list[dict] = []
    for idx, draft in enumerate(drafts):
        if draft.get("case_type") != BLACK_BOX_READY:
            continue
        execution = draft.get("test_execution") or {}
        text = "\n".join(
            str(value)
            for value in [
                execution.get("title"),
                execution.get("external_trigger"),
                execution.get("preconditions"),
                execution.get("inputs"),
                *(execution.get("steps") or []),
                execution.get("expected"),
                *(execution.get("observable_signals") or []),
            ]
            if value
        )
        for finding in _lint_black_box_text(text):
            findings.append({"case_index": idx, **finding})
    action = "pass" if not findings else "downgrade_or_rewrite"
    return asdict(WhiteBoxLeakCheckResult(
        passed=not findings,
        findings=findings,
        action=action,
    ))


def _build_test_case_drafts(
    hit: FunctionHit,
    cases: list[dict],
    gray_box: dict,
    branch_fact_card: dict,
    external_entry_card: dict,
    readiness_card: dict,
) -> list[dict]:
    case_type = readiness_card.get("case_type") or GRAY_BOX_REQUIRED
    drafts: list[dict] = []
    if not cases:
        cases = [{
            "title": "无法从外部触达的覆盖率缺口灰盒验证",
            "entry_kind": "gray_box",
            "preconditions": "尚未确认能够触达该行为的公开入口。",
            "inputs": "使用受控注入点，并准备外部可观察信号。",
            "steps": ["注入目标条件", "观察文档化结果"],
            "expected": _expected_behavior_for_hit(hit),
            "observable_signals": _observable_signals_for_hit(hit),
        }]

    for case in cases[:5]:
        entry_kind = case.get("entry_kind") or "public"
        draft_case_type = (
            GRAY_BOX_REQUIRED
            if case_type == GRAY_BOX_REQUIRED
            else case.get("case_type") or case_type
        )
        execution = {
            "title": case.get("title"),
            "external_trigger": case.get("external_trigger") or f"触发公开 {entry_kind} 流程。",
            "preconditions": case.get("preconditions"),
            "inputs": case.get("inputs"),
            "steps": case.get("steps") or [],
            "expected": case.get("expected"),
            "observable_signals": case.get("observable_signals") or [],
        }
        drafts.append({
            "case_type": draft_case_type,
            "test_execution": execution,
            "gray_box_aid": {
                "required": bool(gray_box.get("required")),
                "technique": gray_box.get("technique"),
                "scheme": gray_box.get("scheme"),
                "injection_points": gray_box.get("injection_points") or [],
            },
            "evidence_section": {
                "coverage_gap": branch_fact_card.get("uncovered_location"),
                "source_evidence": branch_fact_card.get("source_evidence") or [],
                "branch_conditions": branch_fact_card.get("branch_conditions") or [],
                "external_entries": external_entry_card.get("entries") or [],
            },
            "verification_gaps": external_entry_card.get("missing_evidence") or [],
        })
    return drafts


def _confidence_for_context(scope: dict, cgc_context: dict) -> str:
    if cgc_context.get("callers") or scope.get("candidate_symbols"):
        return "high"
    if scope.get("candidate_files") or scope.get("related_communities"):
        return "medium"
    return "low"


def _recommendation_markdown(result: dict) -> str:
    signals = ", ".join(result.get("observable_signals") or [])
    lines = [
        "### 黑盒测试建议",
        f"- 测试目标: {result['scenario']}",
        f"- 输入/前置条件: {result['input_conditions']}",
        f"- 预期行为: {result['expected_behavior']}",
        f"- 可观测信号: {signals}",
        f"- 风险等级: {result['risk_level']}",
        f"- 证据: {result['file_path']}:{result.get('line_start') or '?'} "
        f"hit_count={result['hit_count']}",
    ]
    triggers = result.get("trigger_branches") or []
    if triggers:
        lines.append("- 触发分支:")
        for branch in triggers[:6]:
            origin = branch.get("source") or "self"
            lines.append(
                f"  - [{origin}] {branch.get('condition')}"
                + (f"  ({branch.get('file')}:{branch.get('line_number')})"
                   if branch.get("file") else "")
            )
    entries = result.get("entry_paths") or []
    if entries:
        lines.append("- 外部入口路径:")
        for entry in entries[:4]:
            chain = " -> ".join(entry.get("chain") or [])
            lines.append(f"  - [{entry.get('entry_kind')}] {chain}")
    elif result.get("gray_box_required"):
        gray = result.get("gray_box") or {}
        lines.append(
            "- 确定性追踪未确认外部入口，需结合入口发现继续验证；灰盒仅作为辅助方案: "
            + (gray.get("scheme") or "桩件/故障注入")
        )
    gaps = result.get("evidence_gaps") or []
    if gaps:
        lines.append("- 证据缺口: " + "；".join(gaps[:4]))
    return "\n".join(lines) + "\n"


# ── Coverage gap test-design engine ────────────────────────────────────
#
# Entry-oriented layered tracing for uncovered functions:
#   1. read the function's source window,
#   2. extract its own + its callers' guarding branch conditions,
#   3. walk up the caller chain (<= ENTRY_TRACE_MAX_HOPS) and then combine
#      report/material/GitNexus/CGC/source clues to discover external entries,
#   4. use gray-box guidance only when multi-source entry discovery still cannot
#      validate an external trigger.
#
# Joern is the preferred backend but is not wired up yet, so the engine runs on
# CGC callers when available and degrades to ripgrep text search otherwise.  All
# degraded results are labelled in ``tool_status`` / ``warnings``.


def _existing_repo_root(repo_path: str | None) -> Path | None:
    if not repo_path:
        return None
    try:
        root = Path(repo_path)
        return root if root.exists() and root.is_dir() else None
    except OSError:
        return None


def _resolve_source_file(
    repo_root: Path | None,
    file_path: str,
    function_name: str | None = None,
) -> Path | None:
    """Resolve a coverage code-path to a real file inside the repo.

    The intranet code-path column may be a real relative path, a path without an
    extension, or just a module stem.  We try direct joins (with a few common
    source extensions) first, then a bounded basename search.
    """
    if repo_root is None or not file_path:
        return None
    rel = file_path.replace("\\", "/").lstrip("/")
    for ext in _SOURCE_EXTENSION_CANDIDATES:
        candidate = repo_root / (rel + ext)
        try:
            if candidate.is_file() and _is_within(repo_root, candidate):
                if function_name and not _source_file_defines_function(candidate, function_name):
                    continue
                return candidate
            if candidate.is_dir() and function_name and _is_within(repo_root, candidate):
                found = _find_source_file_defining_function(repo_root, candidate, function_name)
                if found is not None:
                    return found
        except OSError:
            continue
    if function_name:
        hinted_parent = repo_root / rel
        if hinted_parent.is_file():
            hinted_parent = hinted_parent.parent
        if hinted_parent.is_dir() and _is_within(repo_root, hinted_parent):
            found = _find_source_file_defining_function(repo_root, hinted_parent, function_name)
            if found is not None:
                return found
    suffix_match = _resolve_source_file_by_suffix(repo_root, rel)
    if suffix_match is not None:
        if not function_name or _source_file_defines_function(suffix_match, function_name):
            return suffix_match
    basename = Path(rel).name
    if not basename:
        return (
            _find_source_file_defining_function(repo_root, repo_root, function_name)
            if function_name else None
        )
    # Bounded basename search across the repo, skipping dependency/build dirs.
    for ext in _SOURCE_EXTENSION_CANDIDATES:
        target = basename + ext
        matches = 0
        first_match: Path | None = None
        for candidate in _iter_source_files(repo_root, name_filter=target, limit=50):
            matches += 1
            if candidate.is_file() and _is_within(repo_root, candidate):
                if first_match is None:
                    first_match = candidate
                if function_name:
                    if _source_file_defines_function(candidate, function_name):
                        return candidate
                else:
                    return candidate
            if matches >= 50:
                break
        if first_match is not None and not function_name:
            return first_match
    if function_name:
        return _find_source_file_defining_function(repo_root, repo_root, function_name)
    return None


def _resolve_source_file_by_suffix(repo_root: Path, rel: str) -> Path | None:
    """Resolve paths that include parent directories outside the bound repo.

    Coverage exports often preserve the submitter's working directory, e.g.
    ``frontend/nof/nvmf_tcp/transport/tls/tls.c`` while the bound repo root is
    already ``nof``.  Prefer the longest repo-internal suffix before falling
    back to basename search, otherwise duplicate names such as ``tls.c`` can
    silently bind to an unrelated file.
    """
    suffixes = _source_path_suffixes(rel)
    for suffix in suffixes:
        if "/" not in suffix:
            continue
        for ext in _SOURCE_EXTENSION_CANDIDATES:
            target_suffix = f"{suffix}{ext}".lower()
            if not target_suffix or "/" not in target_suffix:
                continue
            name_filter = Path(target_suffix).name
            for candidate in _iter_source_files(repo_root, name_filter=name_filter, limit=500):
                try:
                    candidate_rel = candidate.relative_to(repo_root).as_posix().lower()
                except ValueError:
                    continue
                if candidate_rel.endswith(target_suffix) and _is_within(repo_root, candidate):
                    return candidate
    return None


def _source_path_suffixes(rel: str) -> list[str]:
    normalized = str(rel or "").replace("\\", "/").strip("/")
    if not normalized:
        return []
    parts = [part for part in normalized.split("/") if part]
    suffixes: list[str] = []
    for index in range(len(parts)):
        suffix = "/".join(parts[index:])
        if suffix and suffix not in suffixes:
            suffixes.append(suffix)
    return suffixes


def _iter_source_files(
    root: Path,
    *,
    name_filter: str | None = None,
    limit: int = 500,
) -> list[Path]:
    """Iterate source files below root while skipping generated/vendor dirs."""
    results: list[Path] = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if d not in _DIR_SKIP and not d.startswith(".")
            ]
            for filename in filenames:
                if name_filter and filename != name_filter:
                    continue
                path = Path(dirpath) / filename
                if path.suffix.lower() not in _SOURCE_FILE_EXTS:
                    continue
                results.append(path)
                if len(results) >= limit:
                    return results
    except OSError:
        return results
    return results


def _find_source_file_defining_function(
    repo_root: Path,
    search_root: Path,
    function_name: str,
) -> Path | None:
    if not function_name:
        return None
    for candidate in _iter_source_files(search_root, limit=1000):
        try:
            if not _is_within(repo_root, candidate):
                continue
        except OSError:
            continue
        if _source_file_defines_function(candidate, function_name):
            return candidate
    return None


def _source_file_defines_function(candidate: Path, function_name: str) -> bool:
    try:
        lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    return any(
        _match_def_name(line) == function_name
        or _match_multiline_def_name(lines, idx) == function_name
        for idx, line in enumerate(lines)
    )


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _read_source_window(repo_root: Path | None, hit: FunctionHit) -> dict | None:
    """Return the source window around an uncovered function, or None."""
    return _read_source_window_for_path(repo_root, hit, hit.file_path)


def _read_source_window_from_scope(
    repo_root: Path | None,
    hit: FunctionHit,
    scope: dict,
) -> dict | None:
    if repo_root is None or not isinstance(scope, dict):
        return None
    for path in _scope_candidate_source_paths(scope):
        window = _read_source_window_for_path(repo_root, hit, path)
        if window is not None:
            window["tool"] = "workspace_scope"
            return window
    return None


def _scope_candidate_source_paths(scope: dict) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for bucket in ("candidate_files", "candidate_symbols"):
        for candidate in scope.get(bucket) or []:
            if not isinstance(candidate, dict):
                continue
            path = str(
                candidate.get("path")
                or candidate.get("file_path")
                or candidate.get("file")
                or ""
            ).strip()
            if not path or path in seen:
                continue
            seen.add(path)
            paths.append(path)
    return paths[:8]


def _source_window_is_function_fallback(
    repo_root: Path | None,
    hit: FunctionHit,
    source_window: dict,
) -> bool:
    """Whether the current window came from broad function search, not path evidence."""
    if repo_root is None or not isinstance(source_window, dict):
        return False
    resolved_from_hit = _resolve_source_file(repo_root, hit.file_path, None)
    if resolved_from_hit is None:
        return True
    return _relative_path(repo_root, resolved_from_hit) != source_window.get("path")


def _read_source_window_for_path(
    repo_root: Path | None,
    hit: FunctionHit,
    file_path: str,
) -> dict | None:
    source_file = _resolve_source_file(repo_root, file_path, hit.function_name)
    if source_file is None:
        return None
    try:
        text = source_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = text.splitlines()
    total = len(lines)
    if not total:
        return None

    definition_line = _find_strict_definition_line(lines, hit.function_name)
    if hit.function_name and not definition_line:
        return None
    start_line = definition_line or hit.line_start
    if not start_line or start_line < 1 or start_line > total:
        start_line = None
    if not start_line:
        # Function not locatable in this file — treat as no usable window.
        return None

    end_anchor = hit.line_end if (hit.line_end and hit.line_end >= start_line) else start_line
    window_start = max(1, start_line - SOURCE_WINDOW_BEFORE)
    window_end = min(total, end_anchor + SOURCE_WINDOW_AFTER)
    window_lines = [
        {"n": window_start + offset, "text": lines[window_start - 1 + offset]}
        for offset in range(window_end - window_start + 1)
    ]
    rel_path = _relative_path(repo_root, source_file)
    return {
        "available": True,
        "path": rel_path,
        "definition_line": definition_line or start_line,
        "start": window_start,
        "end": window_end,
        "lines": window_lines,
        "text": "\n".join(item["text"] for item in window_lines),
        "tool": "filesystem",
    }


def _find_definition_line(lines: list[str], function_name: str) -> int | None:
    strict = _find_strict_definition_line(lines, function_name)
    if strict:
        return strict
    # Fallback: first textual occurrence of "name(".
    if not function_name:
        return None
    name_re = re.compile(rf"\b{re.escape(function_name)}\s*\(")
    for idx, line in enumerate(lines):
        if name_re.search(line):
            return idx + 1
    return None


def _find_strict_definition_line(lines: list[str], function_name: str) -> int | None:
    if not function_name:
        return None
    for idx, line in enumerate(lines):
        if (
            _match_def_name(line) == function_name
            or _match_multiline_def_name(lines, idx) == function_name
        ):
            return idx + 1
    return None


def _relative_path(repo_root: Path | None, path: Path) -> str:
    if repo_root is None:
        return str(path)
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except (ValueError, OSError):
        return path.as_posix()


def _extract_branch_condition(line: str) -> str:
    clean = " ".join(line.split())
    for keyword in ("if", "else if", "elif", "switch", "while", "for", "catch",
                    "except", "when", "guard"):
        match = re.search(rf"\b{keyword}\b\s*\(([^)]*)\)", clean, re.IGNORECASE)
        if match:
            return f"{keyword} ({match.group(1).strip()})"
    case_match = re.search(r"\b(case\s+[^:]+:|default\s*:)", clean, re.IGNORECASE)
    if case_match:
        return case_match.group(1).strip()
    goto_match = re.search(r"\b(return\s+-[A-Za-z0-9_]+|goto\s+\w+)", clean, re.IGNORECASE)
    if goto_match:
        return goto_match.group(1).strip()
    return clean[:160]


def _branch_category(line: str) -> str:
    lowered = line.lower()
    if "return -" in lowered or _ERROR_CONDITION_RE.search(line):
        return "error_or_negative_return"
    if "switch" in lowered or "case " in lowered:
        return "dispatch"
    if "while" in lowered or "for " in lowered or "for(" in lowered:
        return "loop"
    return "condition"


def _branches_from_window(window: dict | None, *, source: str) -> list[dict]:
    if not window:
        return []
    branches: list[dict] = []
    for item in window.get("lines") or []:
        text = item.get("text") or ""
        stripped = text.strip()
        if stripped.startswith(("//", "#", "*", "/*")):
            continue
        if not _BRANCH_KEYWORD_RE.search(text):
            continue
        branches.append({
            "condition": _extract_branch_condition(text),
            "line": stripped[:200],
            "line_number": item.get("n"),
            "category": _branch_category(text),
            "source": source,
            "file": window.get("path"),
            "is_error_path": bool(_ERROR_CONDITION_RE.search(text)),
        })
        if len(branches) >= 12:
            break
    return branches


def _dedupe_branches(branches: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for branch in branches:
        key = (branch.get("file"), branch.get("line_number"), branch.get("condition"))
        if key in seen:
            continue
        seen.add(key)
        out.append(branch)
    return out[:16]


def _is_definition_or_declaration_site(
    abs_file: str,
    line_number: int,
    function_name: str,
) -> bool:
    try:
        lines = Path(abs_file).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    idx = line_number - 1
    if idx < 0 or idx >= len(lines):
        return False
    line = lines[idx]
    return (
        _match_def_name(line) == function_name
        or _match_multiline_def_name(lines, idx) == function_name
    )


def _is_non_executable_symbol_reference(line_text: str, function_name: str) -> bool:
    stripped = (line_text or "").strip()
    if not stripped:
        return True
    lowered = stripped.lower()
    if lowered.startswith(("import ", "from ", "#include", "using ", "package ")):
        return True
    if re.match(rf"^(?:extern\s+)?[A-Za-z_][\w\s\*&:<>,~\[\]]*\b{re.escape(function_name)}\s*[;=]", stripped):
        return True
    return False


def _parse_ripgrep_line(raw: str) -> tuple[str, int, str] | None:
    match = re.match(r"^(?P<file>.*?):(?P<line>\d+):(?P<text>.*)$", raw or "")
    if not match:
        return None
    try:
        line_number = int(match.group("line"))
    except ValueError:
        return None
    return match.group("file"), line_number, match.group("text")


def _ripgrep_call_sites(repo_root: Path, function_name: str) -> list[dict]:
    """Find textual call sites of ``function_name`` via ripgrep (degraded mode)."""
    if not function_name or shutil.which("rg") is None:
        return []
    pattern = rf"\b{re.escape(function_name)}\b"
    exclude_args = [
        item
        for glob in _RIPGREP_EXCLUDE_GLOBS
        for item in ("--glob", glob)
    ]
    try:
        proc = subprocess.run(
            ["rg", "--no-heading", "--line-number", "--color", "never",
             "--max-count", "40", *exclude_args,
             "-e", pattern, str(repo_root)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=RIPGREP_TIMEOUT_SECONDS,
        )
    except (subprocess.SubprocessError, OSError):
        return []
    sites: list[dict] = []
    for raw in (proc.stdout or "").splitlines():
        parsed = _parse_ripgrep_line(raw)
        if parsed is None:
            continue
        file_str, line_number, text = parsed
        site_path = Path(file_str)
        if site_path.suffix.lower() not in _SOURCE_FILE_EXTS:
            continue
        if not _is_within(repo_root, site_path):
            continue
        stripped = text.strip()
        # Skip the definition itself and obvious comment lines.
        if _is_definition_or_declaration_site(file_str, line_number, function_name):
            continue
        if stripped.startswith(("//", "#", "*", "/*")):
            continue
        if _is_non_executable_symbol_reference(stripped, function_name):
            continue
        sites.append({
            "file": _relative_path(repo_root, Path(file_str)),
            "abs_file": file_str,
            "line_number": line_number,
            "text": stripped[:200],
        })
        if len(sites) >= 40:
            break
    return sites


def _caller_context(abs_file: str, line_number: int) -> tuple[str | None, dict | None]:
    """Return (enclosing_function, guarding_branch) for a call site.

    ``guarding_branch`` is the nearest ``if/switch/case/while/for`` condition in
    the few lines above the call — i.e. *what condition triggers* the call.
    """
    try:
        lines = Path(abs_file).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None, None
    upper = min(line_number, len(lines)) - 1

    enclosing: str | None = None
    for idx in range(upper, -1, -1):
        name = _match_def_name(lines[idx]) or _match_multiline_def_name(lines, idx)
        if name:
            enclosing = name
            break

    guard: dict | None = None
    low = max(0, line_number - 8)
    for idx in range(upper, low - 1, -1):
        text = lines[idx]
        if (
            _match_def_name(text) is not None
            or _match_multiline_def_name(lines, idx) is not None
        ):
            break  # reached the enclosing definition without a guard
        if _CALLER_GUARD_RE.search(text):
            guard = {
                "condition": _extract_branch_condition(text),
                "line": text.strip()[:200],
                "line_number": idx + 1,
                "category": _branch_category(text),
                "is_error_path": bool(_ERROR_CONDITION_RE.search(text)),
            }
            break
    return enclosing, guard


def _classify_entry(file_path: str, enclosing_fn: str | None, line_text: str) -> str | None:
    """Classify a call site as an external entry kind, or None for internal."""
    if _callback_symbol_from_assignment(line_text):
        return "callback"
    blob = " ".join(filter(None, [file_path or "", enclosing_fn or "", line_text or ""])).lower()
    if enclosing_fn and enclosing_fn.lower() in {"main", "_main", "wmain"}:
        return "cli"
    for kind, needles in _ENTRY_SIGNATURES:
        if any(needle in blob for needle in needles):
            return kind
    return None


def _entry_metadata_for_site(abs_file: str, line_number: int, enclosing_fn: str | None) -> dict:
    metadata: dict = {}
    if not enclosing_fn:
        return metadata
    rpc_method = _spdk_rpc_method_for_handler(abs_file, enclosing_fn)
    if rpc_method:
        metadata["entry_label"] = f"JSON-RPC {rpc_method}"
    hints = _request_field_hints(abs_file, line_number, enclosing_fn)
    for hint in _handler_signature_input_hints(abs_file, enclosing_fn):
        if hint not in hints:
            hints.append(hint)
    if hints:
        metadata["input_hints"] = hints
    return metadata


def _entry_metadata_for_symbol(
    repo_root: Path,
    abs_file: str,
    line_number: int,
    enclosing_fn: str | None,
    entry_symbol: str | None,
) -> dict:
    metadata = _entry_metadata_for_site(abs_file, line_number, enclosing_fn)
    if metadata.get("input_hints") or not entry_symbol:
        return metadata

    symbol_file: Path | None = None
    current_file = Path(abs_file)
    try:
        if current_file.is_file() and _source_file_defines_function(current_file, entry_symbol):
            symbol_file = current_file
    except OSError:
        symbol_file = None
    if symbol_file is None:
        symbol_file = _resolve_entry_file_from_symbol(repo_root, entry_symbol)
    if symbol_file is None:
        return metadata

    try:
        lines = symbol_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return metadata
    definition_line = _find_strict_definition_line(lines, entry_symbol)
    if not definition_line:
        return metadata

    rpc_method = _spdk_rpc_method_for_handler(str(symbol_file), entry_symbol)
    if rpc_method and not metadata.get("entry_label"):
        metadata["entry_label"] = f"JSON-RPC {rpc_method}"
    hints = _request_field_hints(str(symbol_file), definition_line, entry_symbol)
    for hint in _handler_signature_input_hints(str(symbol_file), entry_symbol):
        if hint not in hints:
            hints.append(hint)
    if hints:
        metadata["input_hints"] = hints
    return metadata


def _spdk_rpc_method_for_handler(abs_file: str, handler_name: str) -> str | None:
    try:
        text = Path(abs_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    pattern = re.compile(
        rf"SPDK_RPC_REGISTER\s*\(\s*\"([^\"]+)\"\s*,\s*{re.escape(handler_name)}\b",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    return match.group(1) if match else None


def _request_field_hints(abs_file: str, line_number: int, enclosing_fn: str | None = None) -> list[str]:
    try:
        lines = Path(abs_file).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    idx = line_number - 1
    if idx < 0 or idx >= len(lines):
        return []
    statement: list[str] = []
    start, end = _request_hint_scan_bounds(lines, idx, enclosing_fn)
    for pos in range(start, end):
        text = lines[pos].strip()
        if not text:
            continue
        statement.append(text)
        if pos >= idx and ";" in text:
            break
    statement_text = " ".join(statement)
    positioned_fields: list[tuple[int, str]] = []
    for match in _REQ_FIELD_RE.finditer(statement_text):
        positioned_fields.append((match.start(), match.group(1)))
    for pattern in _REQUEST_FIELD_RES:
        for match in pattern.finditer(statement_text):
            positioned_fields.append((match.start(), match.group(1)))
    for pattern in _ENV_FIELD_RES:
        for match in pattern.finditer(statement_text):
            positioned_fields.append((match.start(), match.group(1)))
    seen: set[str] = set()
    hints: list[str] = []
    for _, field in sorted(positioned_fields, key=lambda item: item[0]):
        if field not in seen:
            seen.add(field)
            hints.append(field)
    for field in _request_destructured_fields(statement_text):
        if field not in seen:
            seen.add(field)
            hints.append(field)
    return hints[:12]


def _request_hint_scan_bounds(
    lines: list[str],
    call_idx: int,
    enclosing_fn: str | None,
) -> tuple[int, int]:
    fallback = (max(0, call_idx - 8), min(len(lines), call_idx + 8))
    if not enclosing_fn:
        return fallback
    fn_start: int | None = None
    for pos in range(call_idx, -1, -1):
        line_def = _match_def_name(lines[pos]) or _match_multiline_def_name(lines, pos)
        if line_def == enclosing_fn:
            fn_start = pos
            break
    if fn_start is None:
        return fallback
    fn_end = len(lines)
    for pos in range(fn_start + 1, len(lines)):
        line_def = _match_def_name(lines[pos]) or _match_multiline_def_name(lines, pos)
        if line_def is not None:
            fn_end = pos
            break
    return max(fn_start + 1, call_idx - 8), min(fn_end, call_idx + 8)


def _request_destructured_fields(text: str) -> list[str]:
    fields: list[str] = []
    for match in _REQUEST_DESTRUCTURE_RE.finditer(text or ""):
        raw_fields = match.group("fields")
        for raw_field in raw_fields.split(","):
            field = raw_field.strip()
            if not field or "..." in field or "{" in field or "}" in field:
                continue
            field = field.split(":", 1)[0].split("=", 1)[0].strip()
            if re.match(r"^[A-Za-z_][\w-]*$", field):
                fields.append(field)
    return fields


def _handler_signature_input_hints(abs_file: str, enclosing_fn: str | None) -> list[str]:
    if not enclosing_fn or Path(abs_file).suffix.lower() not in {".py", ".js", ".jsx", ".ts", ".tsx"}:
        return []
    try:
        lines = Path(abs_file).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for idx, line in enumerate(lines):
        if _match_def_name(line) != enclosing_fn:
            continue
        signature = line.strip()
        while ")" not in signature and idx + 1 < len(lines):
            idx += 1
            signature += " " + lines[idx].strip()
        return _signature_input_params(signature)
    return []


def _signature_input_params(signature: str) -> list[str]:
    match = re.search(r"\((?P<params>[^)]*)\)", signature or "")
    if not match:
        return []
    framework_params = {
        "self", "cls", "request", "req", "response", "res", "next",
        "context", "ctx", "scope", "receive", "send",
    }
    hints: list[str] = []
    seen: set[str] = set()
    for raw_param in match.group("params").split(","):
        param = raw_param.strip()
        if not param or param.startswith(("*", "...")):
            continue
        param = param.split("=", 1)[0].split(":", 1)[0].strip()
        param = param.lstrip("*").strip()
        if not re.match(r"^[A-Za-z_][\w-]*$", param):
            continue
        if param.lower() in framework_params or param in seen:
            continue
        seen.add(param)
        hints.append(param)
    return hints


def _split_cgc_location(location: object) -> tuple[str | None, int | None]:
    text = str(location or "").strip()
    if not text:
        return None, None
    file_part = text
    line_number: int | None = None
    if ":" in text:
        maybe_file, maybe_line = text.rsplit(":", 1)
        if maybe_line.isdigit():
            file_part = maybe_file
            line_number = int(maybe_line)
    return file_part.replace("\\", "/"), line_number


def _cgc_caller_seed_paths(
    function_name: str,
    cgc_callers: object,
) -> tuple[list[dict], list[tuple[list[str], str]]]:
    """Turn CGC callers into entry paths or BFS seeds.

    CGC often has a call graph even when text call-site search is unavailable or
    incomplete.  It may not include the exact branch line, but it can still
    identify a likely public entry/caller and keep the result source-labelled.
    """
    if not isinstance(cgc_callers, list):
        return [], []
    entries: list[dict] = []
    frontier: list[tuple[list[str], str]] = []
    for caller in cgc_callers[:8]:
        if not isinstance(caller, dict):
            continue
        name = str(caller.get("name") or "").strip()
        if not name:
            continue
        location, line_number = _split_cgc_location(caller.get("location"))
        entry_kind = _classify_entry(location or "", name, "")
        chain = [name, function_name]
        if entry_kind:
            entries.append({
                "entry_kind": entry_kind,
                "entry_symbol": name,
                "entry_file": location,
                "entry_label": _public_entry_label(entry_kind, name),
                "call_line": line_number,
                "chain": chain,
                "depth": len(chain) - 1,
                "evidence": (
                    f"{location}:{line_number}" if location and line_number
                    else location or name
                ),
                "tool": "cgc",
            })
        else:
            frontier.append((chain, name))
    return entries, frontier


def _decorated_entry_for_symbol(
    repo_root: Path,
    function_name: str,
    source_file_hint: str | None,
) -> dict | None:
    if not function_name:
        return None
    source_file = (
        _resolve_source_file(repo_root, source_file_hint or "", function_name)
        if source_file_hint else None
    )
    if source_file is None:
        source_file = _resolve_entry_file_from_symbol(repo_root, function_name)
    if source_file is None or source_file.suffix.lower() not in _SOURCE_FILE_EXTS:
        return None
    try:
        lines = source_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    definition_line = _find_strict_definition_line(lines, function_name)
    if not definition_line:
        return None
    decorators = _decorator_lines_before_definition(lines, definition_line)
    if not decorators:
        return None
    entry_kind = _classify_entry_decorator([text for _, text in decorators])
    if not entry_kind:
        return None

    decorator_line_number, decorator_text = decorators[-1]
    rel_file = _relative_path(repo_root, source_file)
    metadata = _entry_metadata_for_site(str(source_file), definition_line, function_name)
    entry_label = metadata.pop("entry_label", None)
    entry = {
        "entry_kind": entry_kind,
        "entry_symbol": function_name,
        "entry_file": rel_file,
        "entry_label": entry_label or _public_entry_label(entry_kind, function_name),
        "call_line": definition_line,
        "chain": [function_name],
        "depth": 0,
        "evidence": f"{rel_file}:{decorator_line_number} {decorator_text.strip()}",
        "tool": "source-decorator",
    }
    entry.update(metadata)
    return entry


def _decorator_lines_before_definition(
    lines: list[str],
    definition_line: int,
) -> list[tuple[int, str]]:
    decorators: list[tuple[int, str]] = []
    for idx in range(definition_line - 2, -1, -1):
        text = lines[idx]
        stripped = text.strip()
        if not stripped:
            break
        if not _DECORATOR_LINE_RE.match(text):
            break
        decorators.append((idx + 1, text))
    decorators.reverse()
    return decorators


def _classify_entry_decorator(decorator_lines: list[str]) -> str | None:
    text = " ".join(line.strip() for line in decorator_lines).lower()
    if not text:
        return None
    for kind, tokens in _ENTRY_DECORATOR_KIND_TOKENS:
        if any(token in text for token in tokens):
            return kind
    return None


def _trace_entry_paths(
    repo_root: Path | None,
    function_name: str,
    *,
    source_file_hint: str | None = None,
    rg_available: bool,
    cgc_callers: object,
) -> tuple[list[dict], list[dict]]:
    """Walk up the caller chain to external entries (entry-oriented tracing).

    Returns ``(entry_paths, caller_branches)``.  ``caller_branches`` are the
    branch conditions guarding the direct call sites of ``function_name``.
    """
    if repo_root is None or not function_name:
        return [], []

    caller_branches: list[dict] = []
    entry_paths: list[dict] = []
    visited: set[str] = {function_name}
    decorated_entry = _decorated_entry_for_symbol(repo_root, function_name, source_file_hint)
    if decorated_entry:
        entry_paths.append(decorated_entry)
    # BFS frontier of (chain_so_far, current_symbol).
    frontier: list[tuple[list[str], str]] = [([function_name], function_name)]
    cgc_entries, cgc_frontier = _cgc_caller_seed_paths(function_name, cgc_callers)
    entry_paths.extend(cgc_entries)
    for chain, symbol in cgc_frontier:
        if symbol not in visited:
            visited.add(symbol)
            frontier.append((chain, symbol))

    for _hop in range(ENTRY_TRACE_MAX_HOPS):
        next_frontier: list[tuple[list[str], str]] = []
        for chain, symbol in frontier:
            sites = _ripgrep_call_sites(repo_root, symbol) if rg_available else []
            for site in sites[:6]:
                enclosing, guard = _caller_context(site["abs_file"], site["line_number"])
                if len(chain) == 1 and guard:
                    branch = dict(guard)
                    branch.update({"source": "caller", "file": site["file"]})
                    caller_branches.append(branch)
                caller_chain = ([enclosing, *chain] if enclosing else chain)
                registration_entry = _registration_entry_for_site(
                    repo_root,
                    site["abs_file"],
                    site["line_number"],
                    enclosing,
                    site["text"],
                    caller_chain,
                )
                if registration_entry:
                    entry_paths.append(registration_entry)
                    continue
                entry_kind = _classify_entry(site["file"], enclosing, site["text"])
                if entry_kind:
                    entry_symbol = enclosing or symbol
                    metadata = _entry_metadata_for_symbol(
                        repo_root,
                        site["abs_file"],
                        site["line_number"],
                        enclosing,
                        entry_symbol,
                    )
                    entry_paths.append({
                        "entry_kind": entry_kind,
                        "entry_symbol": entry_symbol,
                        "entry_file": site["file"],
                        "entry_label": _public_entry_label(entry_kind, entry_symbol),
                        "call_line": site["line_number"],
                        "chain": caller_chain,
                        "depth": len(caller_chain) - 1,
                        "evidence": f"{site['file']}:{site['line_number']} {site['text']}",
                        "tool": "ripgrep" if rg_available else "cgc",
                        **metadata,
                    })
                    continue
                if enclosing and enclosing not in visited:
                    visited.add(enclosing)
                    next_frontier.append((caller_chain, enclosing))
        if entry_paths or not next_frontier:
            break
        frontier = next_frontier

    # De-duplicate entry paths by (kind, symbol, file).
    seen: set[tuple] = set()
    unique_entries: list[dict] = []
    for entry in entry_paths:
        key = (entry["entry_kind"], entry.get("entry_symbol"), entry.get("entry_file"))
        if key in seen:
            continue
        seen.add(key)
        unique_entries.append(entry)
    return unique_entries[:6], _dedupe_branches(caller_branches)


def _registration_entry_for_site(
    repo_root: Path,
    abs_file: str,
    line_number: int,
    enclosing: str | None,
    site_text: str,
    caller_chain: list[str],
) -> dict | None:
    symbol = enclosing or _callback_symbol_from_assignment(site_text)
    if not symbol:
        return None
    try:
        path = Path(abs_file)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    idx = max(0, line_number - 1)
    start = max(0, idx - 12)
    end = min(len(lines), idx + 28)
    window = lines[start:end]
    assignment_seen = any(
        _callback_symbol_from_assignment(line) == symbol
        or re.search(rf"\b{re.escape(symbol)}\b", line)
        for line in window
    )
    registration_line = next(
        (line.strip() for line in window if _REGISTRATION_LINE_RE.search(line)),
        "",
    )
    callback_like = any(
        token in " ".join(window).lower()
        for token in (
            "callback", "_cb", "handler", "ops", "poller", "timer", "event",
            "register", "subscribe", "listener", "schedule", "scheduler", "job",
        )
    )
    if not (assignment_seen and registration_line and callback_like):
        return None

    rel_file = _relative_path(repo_root, path)
    entry_type = _registered_entry_type(registration_line, window)
    metadata = _entry_metadata_for_symbol(repo_root, str(path), line_number, enclosing, symbol)
    entry_label = metadata.pop("entry_label", None)
    entry = {
        "entry_kind": entry_type,
        "entry_symbol": symbol,
        "entry_file": rel_file,
        "call_line": line_number,
        "chain": caller_chain,
        "depth": max(0, len(caller_chain) - 1),
        "evidence": f"{rel_file}:{line_number} {site_text.strip()} | {registration_line}",
        "tool": "source-registration",
        "entry_label": f"{_ENTRY_DISCOVERY_KIND_LABELS.get(entry_type, '外部入口')} {symbol}",
        "input_hints": [],
    }
    if entry_label:
        entry["entry_label"] = entry_label
    input_hints = metadata.pop("input_hints", [])
    if input_hints:
        entry["input_hints"] = input_hints
    entry.update(metadata)
    return entry


def _callback_symbol_from_assignment(text: str) -> str | None:
    match = _CALLBACK_ASSIGN_RE.search(text or "")
    return match.group("symbol") if match else None


def _registered_entry_type(registration_line: str, window: list[str]) -> str:
    text = (registration_line + "\n" + "\n".join(window)).lower()
    if "rpc" in text or "api" in text or "request" in text:
        return "api"
    if "cli" in text or "cmd" in text:
        return "cli"
    if any(token in text for token in ("subscribe", "subscriber", "topic", "queue", "message", "event", "listener")):
        return "message"
    if "service_register" in text or "ops" in text or "callback" in text or "_cb" in text:
        return "callback"
    if any(token in text for token in ("scheduler", "schedule", "scheduled", "cron", "add_job", ".job", " job")):
        return "scheduler"
    if "poller" in text or "timer" in text or "timeout" in text:
        return "timer"
    return "callback"


def _entry_case_provenance(entry: dict) -> dict:
    provenance: dict = {}
    for key in (
        "tool",
        "provider",
        "turn_id",
        "source_verification",
        "validation_error",
        "entry_file",
        "entry_symbol",
        "entry_label",
        "confirming_providers",
        "confirming_turn_ids",
        "confirming_evidence",
    ):
        value = entry.get(key)
        if value is not None and value != "":
            provenance[key] = value
    return provenance


def _build_black_box_cases(
    hit: FunctionHit,
    entry_paths: list[dict],
    trigger_branches: list[dict],
) -> list[dict]:
    """Construct concrete black-box cases from entries + branch conditions."""
    cases: list[dict] = []
    actionable_entry_paths = _filter_actionable_entry_paths(entry_paths)
    base_inputs = _input_conditions_for_hit(hit)
    expected = _expected_behavior_for_hit(hit)
    signals = _observable_signals_for_hit(hit)

    for entry in actionable_entry_paths[:3]:
        entry_label = _safe_external_label(entry)
        entry_kind = str(entry.get("entry_kind") or "外部")
        input_hints = _coerce_string_list(entry.get("input_hints"))
        entry_inputs = (
            "使用外部请求/配置参数构造合法值、边界值和畸形值："
            + ", ".join(input_hints)
            if input_hints else base_inputs
        )
        steps = [
            f"通过公开{entry_kind}入口 {entry_label} 触发流程。",
        ]
        if input_hints:
            steps.append("设置外部参数：" + ", ".join(input_hints))
        steps.extend([
            "分别执行合法输入、边界输入和畸形输入。",
            "从组件外部观察响应、状态、日志和资源信号。",
        ])
        cases.append({
            "case_type": BLACK_BOX_READY,
            "title": f"公开{entry_kind}流程覆盖未命中行为",
            "entry_kind": entry.get("entry_kind"),
            "external_trigger": f"通过公开接口触发 {entry_label}。",
            "preconditions": f"公开{entry_kind}入口可用，且测试环境已完成该流程所需配置。",
            "inputs": entry_inputs,
            "steps": steps,
            "expected": expected,
            "observable_signals": signals,
            "evidence": entry.get("evidence"),
            **_entry_case_provenance(entry),
        })

    primary_entry = actionable_entry_paths[0] if actionable_entry_paths else {}
    primary_entry_label = _safe_external_label(primary_entry) if primary_entry else None
    primary_input_hints = _coerce_string_list(primary_entry.get("input_hints"))

    for branch in trigger_branches[:3]:
        if not branch.get("is_error_path") and branch.get("source") != "caller":
            continue
        cases.append({
            "case_type": BLACK_BOX_READY if actionable_entry_paths else BLACK_BOX_HYPOTHESIS,
            "title": "公开流程覆盖边界或错误条件",
            "entry_kind": actionable_entry_paths[0]["entry_kind"] if actionable_entry_paths else "unknown",
            "external_trigger": (
                f"通过公开接口触发 {primary_entry_label}。"
                if primary_entry_label else "先确认公开入口，再作为黑盒用例执行。"
            ),
            "preconditions": "外部流程能构造可控的边界、错误或状态条件。",
            "inputs": base_inputs,
            "steps": [
                "准备能触发边界或失败行为的外部输入。",
                "执行公开流程并观察外部可见结果。",
            ],
            "expected": expected,
            "observable_signals": signals,
            "evidence": (f"{branch.get('file')}:{branch.get('line_number')}"
                         if branch.get("file") else None),
            **(_entry_case_provenance(primary_entry) if primary_entry else {}),
        })
        if primary_entry_label:
            branch_inputs = (
                f"通过 {primary_entry_label} 调整外部请求、配置或状态，触发相关边界或失败行为。"
            )
            if primary_input_hints:
                branch_inputs += " 外部参数：" + ", ".join(primary_input_hints)
            branch_steps = [
                f"通过 {primary_entry_label} 启动公开{primary_entry.get('entry_kind')}流程。",
            ]
            if primary_input_hints:
                branch_steps.append("设置外部参数：" + ", ".join(primary_input_hints))
            branch_steps.append("使用面向边界/失败的输入执行流程。")
            branch_steps.append("验证外部可观测结果，并确认没有静默成功。")
            cases[-1].update({
                "inputs": branch_inputs,
                "steps": branch_steps,
            })

    if not cases:
        # No source-backed entry/branch; emit a hypothesis, not a ready black-box case.
        cases.append({
            "case_type": BLACK_BOX_HYPOTHESIS,
            "title": "先确认公开流程再执行黑盒测试",
            "entry_kind": "unknown",
            "external_trigger": "尚未确认公开入口。",
            "preconditions": "绑定工作区/源码证据或补充入口映射后，才能标记为黑盒可执行。",
            "inputs": base_inputs,
            "steps": [
                "定位负责该行为的 API、CLI、消息、配置或文件输入。",
                "先把外部输入映射到可观测行为，再编写执行步骤。",
            ],
            "expected": expected,
            "observable_signals": signals,
            "evidence": f"{hit.file_path}:{hit.line_start or '?'} hit_count={hit.hit_count}",
        })
    return cases[:5]


def _build_gray_box_scheme(
    hit: FunctionHit,
    *,
    repo_root: Path | None,
    cgc_callers: object,
    trigger_branches: list[dict],
    required: bool,
) -> dict:
    """Gray-box injection / stub / fault-injection scheme for hard-to-reach code."""
    text = hit.function_name.lower()
    if any(term in text for term in ("error", "fail", "recover", "rollback", "retry", "timeout")):
        technique = "fault_injection"
        scheme = "对依赖项注入故障（错误返回 / 超时 / 资源不可用 / 重试耗尽）以强制进入该错误处理路径"
    elif any(term in text for term in ("cleanup", "free", "close", "release", "destroy")):
        technique = "resource_interception"
        scheme = "拦截资源分配/释放，在正常完成与强制中断两种情形下断言资源回收"
    else:
        technique = "stub_and_drive"
        scheme = "桩件直接调用该函数 / 短接守卫条件，覆盖目标分支并观察状态与返回"

    injection_points: list[str] = []
    if isinstance(cgc_callers, list):
        for caller in cgc_callers[:4]:
            if isinstance(caller, dict) and caller.get("name"):
                injection_points.append(
                    f"{caller.get('name')} ({caller.get('location') or '位置未知'})"
                )
    for branch in trigger_branches[:4]:
        if branch.get("file"):
            injection_points.append(
                f"守卫条件 {branch.get('condition')} @ {branch.get('file')}:{branch.get('line_number')}"
            )
    if not injection_points:
        injection_points.append(f"目标符号 {hit.function_name} ({hit.file_path})")
    guard_conditions = [
        str(branch.get("condition")).strip()
        for branch in trigger_branches[:3]
        if str(branch.get("condition") or "").strip()
    ]
    source_detail = f"目标 {hit.function_name} @ {hit.file_path}"
    if guard_conditions:
        source_detail += "；优先控制守卫条件：" + "；".join(guard_conditions)
    scheme = f"{scheme}。{source_detail}"

    return {
        "required": required,
        "technique": technique,
        "scheme": scheme,
        "injection_points": injection_points[:8],
        "stub_or_fault": scheme,
        "observable_signals": _observable_signals_for_hit(hit),
    }


def _gap_tool_status(
    *,
    repo_root: Path | None,
    rg_available: bool,
    source_window: dict | None,
    cgc_context: dict,
    agent_context: dict,
    scope: dict,
) -> dict:
    cgc_ok = bool(isinstance(cgc_context, dict) and cgc_context.get("available"))
    gitnexus_ok = bool(isinstance(scope, dict) and scope.get("gitnexus_available"))
    external_agent_status = "unavailable"
    if isinstance(agent_context, dict):
        provider_status = agent_context.get("provider_status") or {}
        external_agent_status = _external_agent_status_from_provider_status(provider_status)
    return {
        # Joern is reserved but not yet wired up.
        "joern": "unavailable_reserved",
        "cgc": "available" if cgc_ok else "unavailable",
        "gitnexus": "available" if gitnexus_ok else "unavailable",
        "external_agent": external_agent_status,
        "ripgrep": "available" if rg_available else "unavailable",
        "source": "available" if source_window else (
            "available_no_match" if repo_root is not None else "unavailable"
        ),
    }


def _function_evidence_gaps(
    *,
    workspace_bound: bool,
    source_window: dict | None,
    entry_paths: list[dict],
    trigger_branches: list[dict],
    tool_status: dict,
) -> list[str]:
    gaps: list[str] = []
    if not workspace_bound:
        gaps.append("未绑定工作区/仓库：仅解析覆盖率，未生成源码触发路径（绑定后可深度追踪）")
        return gaps
    if not source_window:
        gaps.append("源码窗口不可用：仓库内未定位到该函数文件，触发分析受限")
    if not trigger_branches:
        gaps.append("未在源码窗口/调用点发现显式守卫分支，触发条件需人工确认")
    if not entry_paths:
        gaps.append(
            f"确定性 {ENTRY_TRACE_MAX_HOPS} 跳追踪未确认外部入口，已进入多源入口发现；"
            "是否灰盒需结合报告、材料、GitNexus、CGC 和源码线索继续验证"
        )
    if tool_status.get("joern") != "available":
        gaps.append("Joern 未接入：缺少精确分支/错误路径/边界值分析，结果为降级模式")
    if tool_status.get("cgc") != "available":
        gaps.append("CGC 不可用：调用链来自 ripgrep 文本匹配，可能存在同名/遗漏")
    return gaps


def _confidence_for_gap(
    scope: dict,
    cgc_context: dict,
    source_window: dict | None,
    entry_paths: list[dict],
) -> str:
    has_symbol_evidence = (
        bool(entry_paths)
        or (isinstance(cgc_context, dict) and cgc_context.get("callers"))
        or (isinstance(scope, dict) and scope.get("candidate_symbols"))
    )
    if has_symbol_evidence:
        return "high"
    if source_window or (
        isinstance(scope, dict)
        and (scope.get("candidate_files") or scope.get("related_communities"))
    ):
        return "medium"
    return "low"


def _build_branch_gaps(
    modules: list[ModuleCoverage],
    *,
    workspace_bound: bool,
) -> list[dict]:
    """Design cases directly from uncovered branch conditions (no source needed)."""
    gaps: list[dict] = []
    for module in modules:
        for raw in module.uncovered_branches[:40]:
            condition = _extract_branch_condition(str(raw))
            is_error = bool(_ERROR_CONDITION_RE.search(str(raw)))
            branch_fact_card = {
                "uncovered_location": module.module_path,
                "branch_conditions": [condition],
                "behavior_impact": "该分支行为必须通过外部契约验证；无法映射外部入口时降级为灰盒辅助。",
                "source_evidence": [str(raw)],
                "possible_observable_signals": ["返回值/响应码", "日志", "状态切换"],
            }
            external_entry_card = {
                "has_external_entry": False,
                "entries": [],
                "missing_evidence": (
                    [] if workspace_bound else ["缺少工作区/源码绑定"]
                ),
            }
            readiness_card = {
                "case_type": BLACK_BOX_HYPOTHESIS,
                "has_external_entry": False,
                "has_constructible_input": False,
                "has_observable_signal": True,
                "rationale": "覆盖率分支存在，但尚未确认外部入口映射",
            }
            gray_box = {
                "required": False,
                "technique": "input_shaping",
                "scheme": "通过外部输入直接构造分支条件；无法构造时短接守卫条件",
                "injection_points": [f"分支条件 {condition}"],
            }
            case = {
                "case_type": BLACK_BOX_HYPOTHESIS,
                "title": "验证未覆盖分支的外部可见行为",
                "entry_kind": "unknown",
                "external_trigger": "先确认 API、CLI、消息、配置或文件输入，再执行黑盒测试。",
                "preconditions": "外部入口映射尚未确认。",
                "inputs": "入口确认后准备合法值、边界值和非法值。",
                "steps": [
                    "把该覆盖率分支映射到公开流程。",
                    "使用外部可控输入执行流程。",
                    "从组件外部观察文档化结果。",
                ],
                "expected": "分支两侧都应产生文档化、可观察的结果；错误侧不能静默成功。",
                "observable_signals": ["返回值/响应码", "日志", "状态切换"],
            }
            test_case_drafts = _build_test_case_drafts(
                FunctionHit(
                    function_name=str(module.module_path or "branch_gap"),
                    file_path=str(module.module_path or ""),
                    line_start=None,
                    line_end=None,
                    triggered=False,
                    hit_count=0,
                ),
                [case],
                gray_box,
                branch_fact_card,
                external_entry_card,
                readiness_card,
            )
            gaps.append({
                "kind": "branch",
                "module_path": module.module_path,
                "branch": str(raw),
                "condition": condition,
                "category": _branch_category(str(raw)),
                "risk_level": "high" if is_error else "medium",
                "black_box_cases": [case],
                "gray_box": gray_box,
                "gray_box_required": False,
                "branch_fact_card": branch_fact_card,
                "external_entry_card": external_entry_card,
                "black_box_readiness": readiness_card,
                "test_case_drafts": test_case_drafts,
                "white_box_leak_check": _lint_test_case_drafts(test_case_drafts),
                "evidence_gaps": (
                    [] if workspace_bound
                    else ["未绑定工作区：分支来自覆盖率文件，未做源码定位"]
                ),
            })
            if len(gaps) >= 60:
                return gaps
    return gaps


async def build_coverage_test_context(
    modules: list[ModuleCoverage],
    *,
    workspace_id: str | None,
    repo_path: str | None,
    deterministic_gaps: list[dict],
    report_output_dir: Path | None = None,
) -> dict:
    """Build the evidence package used by AI test-case generation."""
    reports = await _load_coverage_report_context(workspace_id, report_output_dir)
    materials = await _load_coverage_material_context(workspace_id)
    coverage = _coverage_context_from_modules(modules)
    source = _source_context_from_gaps(deterministic_gaps)
    gitnexus = _tool_context_from_gaps(deterministic_gaps, "gitnexus_scope")
    cgc = _tool_context_from_gaps(deterministic_gaps, "cgc")
    external_trigger_candidates = _external_trigger_candidates(
        deterministic_gaps,
        reports=reports,
        materials=materials,
    )
    entry_discovery = _build_coverage_entry_discovery(
        deterministic_gaps,
        reports=reports,
        materials=materials,
    )
    evidence_counts = {
        "coverage": len(coverage.get("uncovered_functions") or []) + len(coverage.get("uncovered_branches") or []),
        "source": len(source),
        "gitnexus": len(gitnexus),
        "cgc": len(cgc),
        "report": len(reports),
        "material": len(materials),
        "entry_discovery": len(entry_discovery.get("cards") or []),
    }
    return {
        "version": COVERAGE_TEST_CONTEXT_VERSION,
        "workspace_id": workspace_id,
        "repo_path": repo_path,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "coverage": coverage,
        "source": source,
        "gitnexus": gitnexus,
        "cgc": cgc,
        "reports": reports,
        "materials": materials,
        "external_trigger_candidates": external_trigger_candidates,
        "entry_discovery": entry_discovery,
        "deterministic_gaps": _context_safe_gaps(deterministic_gaps),
        "evidence_source_counts": evidence_counts,
        "warnings": _coverage_context_warnings(evidence_counts),
    }


_TRIGGER_SURFACE_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("api", re.compile(r"\b(api|rpc|rest|http|endpoint|request|controller|route|handler)\b|接口|请求", re.IGNORECASE)),
    ("cli", re.compile(r"\b(cli|command|cmd|shell|console)\b|命令行|命令|脚本", re.IGNORECASE)),
    ("config", re.compile(r"\b(config|setting|option|env|yaml|json|toml|ini)\b|配置|环境变量|参数", re.IGNORECASE)),
    ("file", re.compile(r"\b(file|csv|xlsx|xml|upload|download|import|export)\b|文件|上传|导入|导出", re.IGNORECASE)),
    ("message", re.compile(r"\b(message|event|queue|topic|consumer|producer|timer|scheduler|job)\b|消息|事件|队列|定时|任务", re.IGNORECASE)),
    ("connection", re.compile(r"\b(connect|connection|login|logout|session|socket|network|timeout|retry)\b|连接|登录|会话|网络|超时|重试", re.IGNORECASE)),
    ("ui", re.compile(r"\b(ui|page|button|form|screen|web)\b|页面|按钮|表单|点击", re.IGNORECASE)),
    ("resource", re.compile(r"\b(memory|disk|quota|resource|pool|limit|capacity)\b|资源|内存|磁盘|限额|容量", re.IGNORECASE)),
)


def _external_trigger_candidates(
    gaps: list[dict],
    *,
    reports: list[dict],
    materials: list[dict],
) -> list[dict]:
    """Infer broad external trigger surfaces for AI classification.

    These are hints, not proof.  They prevent the deterministic caller tracer's
    "no entry within N hops" result from becoming an automatic gray-box verdict.
    """
    candidates: list[dict] = []

    def add(surface: str, trigger: str, evidence: str, confidence: str = "medium") -> None:
        item = {
            "surface": surface,
            "trigger": trigger[:180],
            "evidence": evidence[:180],
            "confidence": confidence,
        }
        key = (item["surface"], item["trigger"], item["evidence"])
        if key not in {
            (old["surface"], old["trigger"], old["evidence"])
            for old in candidates
        }:
            candidates.append(item)

    for gap in gaps[:40]:
        for entry in gap.get("entry_paths") or []:
            label = entry.get("entry_label") or entry.get("entry_symbol") or entry.get("entry_kind")
            if label:
                add(
                    str(entry.get("entry_kind") or "public"),
                    str(label),
                    str(entry.get("evidence") or gap.get("function_name") or gap.get("file_path") or "entry_path"),
                    "high",
                )
        symbol_text = " ".join(
            str(value or "")
            for value in (
                gap.get("function_name"),
                gap.get("file_path"),
                gap.get("module_path"),
                gap.get("feature_name"),
            )
        )
        for surface, pattern in _TRIGGER_SURFACE_PATTERNS:
            if pattern.search(symbol_text):
                add(
                    surface,
                    f"从覆盖率符号/路径推断可能存在 {surface} 触发面",
                    str(gap.get("function_name") or gap.get("file_path") or "coverage_gap"),
                    "low",
                )

    for source_name, items in (("report", reports), ("material", materials)):
        for item in items[:12]:
            text = " ".join(
                str(item.get(key) or "")
                for key in ("title", "filename", "report_type", "excerpt")
            )
            for surface, pattern in _TRIGGER_SURFACE_PATTERNS:
                if pattern.search(text):
                    title = item.get("title") or item.get("filename") or item.get("report_id") or source_name
                    add(
                        surface,
                        f"{source_name} 中出现 {surface} 触发线索：{_excerpt(text, 140)}",
                        f"{source_name}:{title}",
                        "medium",
                    )
    return candidates[:20]


def _build_coverage_entry_discovery(
    gaps: list[dict],
    *,
    reports: list[dict],
    materials: list[dict],
) -> dict:
    cards: list[dict] = []
    for gap in gaps:
        if gap.get("kind") != "function":
            continue
        card = _entry_discovery_card_for_gap(
            gap,
            reports=reports,
            materials=materials,
        )
        gap["entry_discovery"] = card
        cards.append(card)
    return {
        "version": COVERAGE_ENTRY_DISCOVERY_VERSION,
        "cards": cards,
        "summary": {
            "card_count": len(cards),
            "entry_found_count": sum(
                1 for card in cards
                if card.get("entry_trace_status") == "entry_found"
            ),
            "candidate_entry_count": sum(
                len(card.get("candidate_external_entries") or [])
                for card in cards
            ),
        },
    }


def _entry_discovery_card_for_gap(
    gap: dict,
    *,
    reports: list[dict],
    materials: list[dict],
) -> dict:
    source_window = gap.get("source_window") or None
    evidence = gap.get("evidence") or {}
    gitnexus_scope = evidence.get("gitnexus_scope") if isinstance(evidence, dict) else {}
    cgc = evidence.get("cgc") if isinstance(evidence, dict) else {}
    external_agent = evidence.get("external_agent") if isinstance(evidence, dict) else {}
    entry_paths = gap.get("entry_paths") or []
    candidates = _entry_candidates_from_paths(entry_paths)
    if isinstance(external_agent, dict):
        candidates.extend(_entry_candidates_from_agent_rejected_validated(
            gap,
            external_agent.get("validated_entries") or [],
            entry_paths,
        ))
        candidates.extend(_entry_candidates_from_agent_unverified(
            _filter_resolved_agent_unverified_entries(
                external_agent.get("unverified_entries") or [],
                entry_paths,
            )
        ))
    report_material_clues = _report_material_entry_clues(
        gap,
        reports=reports,
        materials=materials,
    )
    if not candidates:
        candidates.extend(_entry_candidates_from_clues(report_material_clues))
    actionable_candidates = [
        candidate for candidate in candidates
        if _entry_discovery_candidate_is_actionable(candidate)
    ]
    status = gap.get("entry_trace_status") or _entry_trace_status(
        workspace_bound=bool((evidence.get("coverage") or {}).get("workspace_id"))
        if isinstance(evidence, dict) else False,
        trace=True,
        source_window=source_window,
        entry_paths=gap.get("entry_paths") or [],
        tool_status=gap.get("tool_status") or {},
    )
    unresolved = _entry_discovery_unresolved_reasons(gap, candidates, status)
    return {
        "function_name": gap.get("function_name"),
        "file_path": gap.get("file_path"),
        "module_path": gap.get("module_path"),
        "entry_trace_status": status,
        "source_window": {
            "available": bool(source_window),
            "path": source_window.get("path") if isinstance(source_window, dict) else None,
            "start": source_window.get("start") if isinstance(source_window, dict) else None,
            "end": source_window.get("end") if isinstance(source_window, dict) else None,
        },
        "candidate_external_entries": candidates[:8],
        "gitnexus_scope": _compact_gitnexus_scope(gitnexus_scope),
        "cgc": _compact_cgc_context(cgc),
        "external_agent": _compact_external_agent_context(external_agent),
        "report_material_clues": report_material_clues[:8],
        "source_verification_status": _entry_discovery_source_verification_status(candidates),
        "unresolved_reasons": unresolved,
        "gray_box_allowed": not actionable_candidates and status in {
            "source_read_ok_entry_not_found",
            "source_not_found",
            "tool_unavailable",
        },
    }


def _filter_resolved_agent_unverified_entries(
    unverified_entries: list[dict],
    entry_paths: list[dict],
) -> list[dict]:
    resolved_execution_keys: set[tuple[str, str]] = set()
    resolved_file_keys: set[str] = set()
    for entry in entry_paths:
        if not isinstance(entry, dict):
            continue
        execution_key = _entry_execution_key(entry)
        if execution_key:
            resolved_execution_keys.add(execution_key)
        file_key = _entry_file_key(entry)
        if file_key:
            resolved_file_keys.add(file_key)

    filtered: list[dict] = []
    for entry in unverified_entries:
        if not isinstance(entry, dict):
            continue
        execution_key = _entry_execution_key(entry)
        if execution_key and execution_key in resolved_execution_keys:
            continue
        if not execution_key and _entry_file_key(entry) in resolved_file_keys:
            continue
        filtered.append(entry)
    return filtered


def _entry_discovery_source_verification_status(candidates: list[dict]) -> str:
    actionable = [
        candidate for candidate in candidates
        if _entry_discovery_candidate_is_actionable(candidate)
    ]
    if any(candidate.get("source_verification") == "source_backed" for candidate in actionable):
        return "source_backed"
    if actionable:
        return "needs_source_verification"
    if candidates:
        return "rejected_external_entry_candidate"
    return "no_external_entry_candidate"


def _entry_discovery_candidate_is_actionable(candidate: dict) -> bool:
    if str(candidate.get("validation_error") or "").strip():
        return False
    return str(candidate.get("confidence") or "").strip().lower() != "low"


def _entry_candidates_from_agent_rejected_validated(
    gap: dict,
    entries: list[dict],
    entry_paths: list[dict],
) -> list[dict]:
    accepted_keys = _entry_candidate_keys(entry_paths)
    candidates: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        provider = str(entry.get("provider") or "external_agent")
        if _entry_execution_key(entry) in accepted_keys:
            continue
        rejection = _agent_entry_rejection_reason_for_gap(entry, gap)
        if not rejection:
            continue
        entry_type = str(entry.get("entry_kind") or "external")
        entry_symbol = entry.get("entry_symbol")
        candidates.append({
            "entry_type": entry_type,
            "entry_symbol": entry_symbol,
            "entry_file": entry.get("entry_file"),
            "entry_label": entry.get("external_trigger")
            or _public_entry_label(entry_type, entry_symbol)
            or entry_symbol
            or _ENTRY_DISCOVERY_KIND_LABELS.get(entry_type, "external entry"),
            "external_trigger": entry.get("external_trigger"),
            "chain": entry.get("chain") or [],
            "evidence": entry.get("reason") or entry.get("external_trigger"),
            "confidence": "medium",
            "source_verification": entry.get("source_verification") or "source_backed",
            "tool": provider,
            "provider": provider,
            "turn_id": entry.get("turn_id"),
            "validation_error": rejection,
            "input_hints": _coerce_string_list(entry.get("input_hints")),
        })
    return candidates


def _entry_candidate_keys(entries: list[dict]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = _entry_execution_key(entry)
        if key:
            keys.add(key)
    return keys


def _agent_entry_rejection_reason_for_gap(entry: dict, gap: dict) -> str | None:
    if _agent_entry_is_self_target_for_gap(entry, gap):
        return "self_target_entry"
    if _agent_entry_chain_missing_target(entry, gap.get("function_name")):
        return "chain_missing_target"
    if not _agent_entry_has_public_trigger_surface(entry):
        return "not_public_trigger_surface"
    return None


def _agent_entry_is_self_target_for_gap(entry: dict, gap: dict) -> bool:
    function_name = str(gap.get("function_name") or "").strip()
    if not function_name:
        return False
    entry_symbol = str(entry.get("entry_symbol") or entry.get("entry_label") or "").strip()
    if entry_symbol and entry_symbol != function_name:
        return False
    chain = _normalize_agent_entry_chain(entry.get("chain"))
    if chain and any(value != function_name for value in chain):
        return False
    entry_file = str(entry.get("entry_file") or "").replace("\\", "/")
    gap_file = str(gap.get("file_path") or "").replace("\\", "/")
    if entry_file and gap_file and entry_file != gap_file and not gap_file.endswith(entry_file):
        return False
    return bool(entry_symbol == function_name or chain == [function_name])


def _entry_candidates_from_paths(entry_paths: list[dict]) -> list[dict]:
    candidates: list[dict] = []
    for entry in entry_paths:
        entry_type = str(entry.get("entry_kind") or "external")
        tool = str(entry.get("tool") or "")
        source_verification = (
            entry.get("source_verification")
            or ("source_backed" if tool != "cgc" else "graph_backed")
        )
        candidates.append({
            "entry_type": entry_type,
            "entry_symbol": entry.get("entry_symbol") or entry.get("entry_label"),
            "entry_file": entry.get("entry_file"),
            "entry_label": entry.get("entry_label")
            or _ENTRY_DISCOVERY_KIND_LABELS.get(entry_type, "外部入口"),
            "external_trigger": entry.get("external_trigger"),
            "chain": entry.get("chain") or [],
            "evidence": entry.get("evidence"),
            "confidence": "high" if tool in {"ripgrep", "source-registration"} else "medium",
            "source_verification": source_verification,
            "tool": tool,
            "provider": entry.get("provider") or (tool if tool in {"claude-code", "opencode"} else None),
            "turn_id": entry.get("turn_id"),
            "validation_error": entry.get("validation_error"),
            "input_hints": _coerce_string_list(entry.get("input_hints")),
        })
    return candidates


def _entry_candidates_from_agent_unverified(entries: list[dict]) -> list[dict]:
    candidates: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_type = str(entry.get("entry_kind") or "external")
        entry_symbol = entry.get("entry_symbol")
        candidates.append({
            "entry_type": entry_type,
            "entry_symbol": entry_symbol,
            "entry_file": entry.get("entry_file"),
            "entry_label": entry.get("external_trigger")
            or _public_entry_label(entry_type, entry_symbol)
            or entry_symbol
            or _ENTRY_DISCOVERY_KIND_LABELS.get(entry_type, "external entry"),
            "external_trigger": entry.get("external_trigger"),
            "chain": entry.get("chain") or [],
            "evidence": entry.get("reason"),
            "confidence": "low",
            "source_verification": "needs_source_verification",
            "tool": entry.get("provider") or "external_agent",
            "provider": entry.get("provider"),
            "turn_id": entry.get("turn_id"),
            "validation_error": entry.get("validation_error"),
            "input_hints": _coerce_string_list(entry.get("input_hints")),
        })
    return candidates


def _entry_candidates_from_clues(clues: list[dict]) -> list[dict]:
    candidates: list[dict] = []
    for clue in clues:
        surface = str(clue.get("surface") or "external")
        candidates.append({
            "entry_type": surface,
            "entry_symbol": clue.get("trigger") or clue.get("title"),
            "entry_file": None,
            "entry_label": clue.get("trigger") or _ENTRY_DISCOVERY_KIND_LABELS.get(surface, "外部入口"),
            "chain": [],
            "evidence": clue.get("evidence"),
            "confidence": clue.get("confidence") or "low",
            "source_verification": "needs_source_verification",
            "tool": clue.get("source") or "report_material",
        })
    return candidates


def _report_material_entry_clues(
    gap: dict,
    *,
    reports: list[dict],
    materials: list[dict],
) -> list[dict]:
    needles = [
        str(gap.get("function_name") or ""),
        str(gap.get("module_path") or ""),
        str(gap.get("feature_name") or ""),
        Path(str(gap.get("file_path") or "")).stem,
    ]
    needles = [needle for needle in needles if needle]
    clues: list[dict] = []
    for source_name, items in (("report", reports), ("material", materials)):
        for item in items[:16]:
            text = " ".join(
                str(item.get(key) or "")
                for key in ("title", "filename", "report_type", "excerpt")
            )
            if needles and not any(needle in text for needle in needles):
                if not any(pattern.search(text) for _, pattern in _TRIGGER_SURFACE_PATTERNS):
                    continue
            for surface, pattern in _TRIGGER_SURFACE_PATTERNS:
                if pattern.search(text):
                    title = item.get("title") or item.get("filename") or item.get("report_id") or source_name
                    clues.append({
                        "source": source_name,
                        "surface": surface,
                        "title": title,
                        "trigger": f"{source_name} 提到 {surface} 触发面",
                        "evidence": _excerpt(text, 240),
                        "confidence": "medium",
                    })
    return _dedupe_context_items(clues, ("source", "surface", "title"))[:10]


def _compact_gitnexus_scope(scope: object) -> dict:
    if not isinstance(scope, dict):
        return {}
    return {
        "gitnexus_available": scope.get("gitnexus_available"),
        "candidate_files": scope.get("candidate_files") or [],
        "candidate_symbols": scope.get("candidate_symbols") or [],
        "related_communities": scope.get("related_communities") or [],
        "warnings": scope.get("warnings") or [],
    }


def _compact_cgc_context(cgc: object) -> dict:
    if not isinstance(cgc, dict):
        return {}
    return {
        "available": cgc.get("available"),
        "callers": cgc.get("callers") or [],
        "callees": cgc.get("callees") or [],
    }


def _compact_external_agent_context(context: object) -> dict:
    if not isinstance(context, dict):
        return {}
    raw_results = context.get("raw_results") or []
    warnings = [
        str(item).strip()
        for item in (context.get("warnings") or [])
        if str(item).strip()
    ]
    for warning in _external_agent_warnings(raw_results):
        if not _warning_already_present(warnings, warning):
            warnings.append(warning)
    return {
        "status": context.get("status"),
        "provider_status": context.get("provider_status") or {},
        "validated_entry_count": len(context.get("validated_entries") or []),
        "unverified_entries": (context.get("unverified_entries") or [])[:8],
        "warnings": warnings[:12],
    }


def _warning_already_present(warnings: list[str], candidate: str) -> bool:
    text = str(candidate or "").strip()
    if not text:
        return True
    if text in warnings:
        return True
    suffix = text.split(":", 1)[1].strip() if ":" in text else text
    return any(existing == suffix or existing.endswith(suffix) for existing in warnings)


def _external_agent_status_from_provider_status(provider_status: object) -> str:
    if not isinstance(provider_status, dict) or not provider_status:
        return "unavailable"
    statuses = {str(status) for status in provider_status.values() if status}
    if "ok" in statuses:
        return "available"
    for status in ("invalid_output", "error", "timeout", "rejected_command"):
        if status in statuses:
            return status
    return "unavailable"


def _external_agent_warnings(raw_results: object) -> list[str]:
    warnings: list[str] = []
    if not isinstance(raw_results, list):
        return warnings
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or "external_agent")
        for warning in item.get("warnings") or []:
            text = str(warning).strip()
            if text:
                warnings.append(f"{provider}: {text}")
        if not item.get("warnings"):
            text = str(item.get("raw_summary") or "").strip()
            if text:
                warnings.append(f"{provider}: {text}")
        if len(warnings) >= 12:
            break
    return warnings[:12]


def _entry_discovery_unresolved_reasons(
    gap: dict,
    candidates: list[dict],
    status: str,
) -> list[str]:
    reasons = list(gap.get("evidence_gaps") or [])
    if candidates:
        if any(c.get("source_verification") == "needs_source_verification" for c in candidates):
            reasons.append("存在外部入口线索，但仍需源码确认触发链。")
        return reasons
    if status == "workspace_not_bound":
        reasons.append("覆盖率未绑定工作区，无法读取源码和工具索引。")
    elif status == "trace_skipped_by_cap":
        reasons.append("该缺口超过源码追踪上限，本轮未展开入口发现。")
    elif status == "source_not_found":
        reasons.append("覆盖率路径或函数名未能解析到真实源码窗口。")
    elif status == "tool_unavailable":
        reasons.append("源码可读，但 CGC/ripgrep 不可用，无法完成入口追踪。")
    elif status == "source_read_ok_entry_not_found":
        reasons.append("源码窗口已读取，但多源入口发现仍未确认外部触发入口。")
    return _coerce_string_list(reasons)


async def _load_coverage_report_context(
    workspace_id: str | None,
    report_output_dir: Path | None,
) -> list[dict]:
    reports: list[dict] = []
    if report_output_dir and report_output_dir.exists():
        for path in sorted(report_output_dir.glob("*.md"))[:8]:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not text.strip():
                continue
            reports.append({
                "source": "output_dir",
                "report_id": path.stem,
                "report_type": _guess_report_type(path.name),
                "title": path.stem,
                "task_id": report_output_dir.name,
                "excerpt": _excerpt(text, 1600),
            })
    if workspace_id:
        try:
            async with aiosqlite.connect(settings.sqlite_db) as db:
                db.row_factory = aiosqlite.Row
                rows = await db.execute_fetchall(
                    """SELECT id, report_type, title, content, status, task_id, created_at
                       FROM workspace_reports
                       WHERE workspace_id = ? AND status IN ('completed', 'partial')
                       ORDER BY created_at DESC
                       LIMIT 12""",
                    (workspace_id,),
                )
        except Exception as exc:
            logger.info("Coverage report context unavailable: %s", exc)
            rows = []
        for row in rows:
            data = dict(row)
            content = data.get("content") or ""
            if not content.strip():
                continue
            reports.append({
                "source": "workspace_reports",
                "report_id": data.get("id"),
                "report_type": data.get("report_type"),
                "title": data.get("title") or data.get("report_type"),
                "task_id": data.get("task_id"),
                "status": data.get("status"),
                "excerpt": _excerpt(content, 1800),
            })
    return _dedupe_context_items(reports, ("report_id", "title"))[:10]


async def _load_coverage_material_context(workspace_id: str | None) -> list[dict]:
    if not workspace_id:
        return []
    try:
        async with aiosqlite.connect(settings.sqlite_db) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                """SELECT id, filename, content_type, file_path
                   FROM workspace_materials
                   WHERE workspace_id = ? AND is_active = 1
                   ORDER BY created_at DESC
                   LIMIT 8""",
                (workspace_id,),
            )
    except Exception as exc:
        logger.info("Coverage material context unavailable: %s", exc)
        return []
    materials: list[dict] = []
    for row in rows:
        data = dict(row)
        excerpt = ""
        try:
            path = Path(data.get("file_path") or "")
            if path.exists() and path.is_file():
                excerpt = _excerpt(path.read_text(encoding="utf-8", errors="replace"), 1600)
        except OSError:
            excerpt = ""
        materials.append({
            "material_id": data.get("id"),
            "filename": data.get("filename"),
            "content_type": data.get("content_type"),
            "excerpt": excerpt,
        })
    return materials


def _coverage_context_from_modules(modules: list[ModuleCoverage]) -> dict:
    uncovered_functions: list[dict] = []
    uncovered_branches: list[dict] = []
    total_functions = 0
    covered_functions = 0
    for module in modules:
        hits = module.function_hits or [hit for f in module.files for hit in f.function_hits]
        total_functions += len(hits)
        covered_functions += sum(1 for hit in hits if hit.triggered or hit.hit_count > 0)
        for hit in hits:
            if hit.triggered or hit.hit_count > 0:
                continue
            uncovered_functions.append({
                "module_path": module.module_path,
                "feature_name": hit.feature_name,
                "file_path": hit.file_path,
                "function_name": hit.function_name,
                "line_start": hit.line_start,
                "line_end": hit.line_end,
                "hit_count": hit.hit_count,
            })
        for branch in module.uncovered_branches[:40]:
            uncovered_branches.append({
                "module_path": module.module_path,
                "branch": str(branch),
            })
    return {
        "module_count": len(modules),
        "function_total": total_functions,
        "function_covered": covered_functions,
        "function_rate": covered_functions / total_functions if total_functions else 0.0,
        "branch_coverage_available": any(m.uncovered_branches for m in modules),
        "uncovered_functions": uncovered_functions[:80],
        "uncovered_branches": uncovered_branches[:80],
    }


def _source_context_from_gaps(gaps: list[dict]) -> list[dict]:
    source: list[dict] = []
    for gap in gaps[:80]:
        window = gap.get("source_window") or {}
        if isinstance(window, dict) and window.get("available"):
            source.append({
                "gap": gap.get("function_name") or gap.get("condition"),
                "file_path": window.get("path"),
                "start": window.get("start"),
                "end": window.get("end"),
                "excerpt": _excerpt(window.get("text") or "", 1200),
            })
    return source


def _tool_context_from_gaps(gaps: list[dict], key: str) -> list[dict]:
    items: list[dict] = []
    for gap in gaps[:80]:
        evidence = gap.get("evidence") or {}
        value = evidence.get(key) if isinstance(evidence, dict) else None
        if value:
            items.append({
                "gap": gap.get("function_name") or gap.get("condition"),
                "evidence": value,
            })
    return items[:24]


def _context_safe_gaps(gaps: list[dict]) -> list[dict]:
    safe: list[dict] = []
    for gap in gaps[:80]:
        safe.append({
            "kind": gap.get("kind"),
            "module_path": gap.get("module_path"),
            "function_name": gap.get("function_name"),
            "file_path": gap.get("file_path"),
            "risk_level": gap.get("risk_level"),
            "entry_paths": gap.get("entry_paths") or [],
            "trigger_branches": gap.get("trigger_branches") or [],
            "gray_box_required": gap.get("gray_box_required"),
            "entry_trace_status": gap.get("entry_trace_status"),
            "entry_discovery": gap.get("entry_discovery"),
            "evidence_gaps": gap.get("evidence_gaps") or [],
        })
    return safe


def _coverage_context_warnings(counts: dict) -> list[str]:
    warnings: list[str] = []
    if counts.get("report", 0) == 0:
        warnings.append("未找到已生成分析报告：AI 用例只能基于覆盖率、源码和工具证据，业务语义需要人工确认。")
    if counts.get("material", 0) == 0:
        warnings.append("未找到工作区材料：需求/设计语义不足，不能把推断当成最终事实。")
    if counts.get("gitnexus", 0) == 0:
        warnings.append("GitNexus 证据未进入覆盖率上下文：调用/模块结论需要降级处理。")
    if counts.get("cgc", 0) == 0:
        warnings.append("CGC 证据未进入覆盖率上下文：调用链可能不完整。")
    return warnings


def _guess_report_type(filename: str) -> str:
    lowered = filename.lower()
    if "源码" in filename or "source" in lowered:
        return "source_reading"
    if "流程" in filename or "business" in lowered:
        return "business_flow"
    if "测试" in filename or "test" in lowered:
        return "test_design"
    return "report"


def _excerpt(text: str, limit: int) -> str:
    clean = "\n".join(line.rstrip() for line in str(text or "").splitlines())
    return clean[:limit]


def _dedupe_context_items(items: list[dict], keys: tuple[str, ...]) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for item in items:
        key = tuple(item.get(k) for k in keys)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


async def _generate_ai_test_scenarios(
    llm: BaseLLMClient,
    context: dict,
) -> dict:
    prompt = _coverage_ai_prompt(context)
    messages = [
        {
            "role": "system",
            "content": (
                "你是面向测试人员的代码覆盖率分析助手。只能依据输入证据生成测试场景；"
                "黑盒步骤必须使用外部触发方式，不能要求调用内部函数、进入源码分支或修改内部变量。"
                "只输出 JSON，不输出 Markdown。"
            ),
        },
        {"role": "user", "content": prompt},
    ]
    resp = await llm.complete(
        messages,
        max_tokens=min(8192, max(4096, settings.llm_max_output_tokens)),
        temperature=0.1,
    )
    parsed = _parse_json_object(resp.content)
    parse_error = parsed.get("_parse_error") if isinstance(parsed, dict) else None
    scenarios = parsed.get("scenarios") if isinstance(parsed, dict) else []
    if not isinstance(scenarios, list):
        scenarios = []
    accepted: list[dict] = []
    rejected: list[dict] = []
    for idx, raw in enumerate(scenarios):
        scenario = raw if isinstance(raw, dict) else {}
        reason = _scenario_rejection_reason(scenario)
        if reason:
            rejected.append({
                "scenario_id": scenario.get("scenario_id") or f"scenario_{idx + 1}",
                "reason": reason,
            })
            continue
        accepted.append(_normalize_ai_scenario(scenario))
    if not scenarios:
        rejected.append({
            "scenario_id": "response",
            "reason": parse_error or "AI 响应中没有 scenarios 数组",
        })
    _enforce_ai_scenario_batch_gate(context, accepted, rejected)
    return {
        "prompt": prompt,
        "raw_response": resp.content,
        "model": getattr(resp, "model", ""),
        "accepted": accepted,
        "rejected": rejected,
    }


def _coverage_ai_prompt(context: dict) -> str:
    compact = {
        "version": context.get("version"),
        "workspace_id": context.get("workspace_id"),
        "repo_path": context.get("repo_path"),
        "coverage": context.get("coverage"),
        "deterministic_gaps": context.get("deterministic_gaps"),
        "reports": context.get("reports"),
        "materials": context.get("materials"),
        "source": context.get("source"),
        "gitnexus": context.get("gitnexus"),
        "cgc": context.get("cgc"),
        "external_trigger_candidates": context.get("external_trigger_candidates"),
        "entry_discovery": context.get("entry_discovery"),
        "warnings": context.get("warnings"),
    }
    return (
        "请基于以下 CodeTalk 覆盖率上下文，生成测试人员可执行的覆盖率补充用例。\n"
        "要求：\n"
        "1. 不要套用具体协议或项目模板，要从证据中识别外部触发面、输入面、状态面、配置环境面、时序面和可观测面。\n"
        "2. 只生成 3 到 4 个最高价值场景，优先覆盖不同外部触发面和不同故障模式，避免输出过长导致 JSON 截断。\n"
        "3. 必须先阅读 entry_discovery.cards：候选外部入口、入口类型、源码验证状态和 unresolved_reasons 是判定黑盒/灰盒的主依据。\n"
        "4. deterministic_gaps 里的 gray_box_required 只表示确定性追踪没找到入口，不是最终灰盒结论；必须先结合 entry_discovery、reports、materials、external_trigger_candidates、GitNexus、CGC 和 source 证据重新判断外部触发方式。\n"
        "5. 如果入口发现卡、报告、文档或工具证据能说明测试人员可通过请求、连接、配置、文件、消息、页面、服务重启、异常输入、网络异常或资源不足触发，就优先生成 `black_box_ready`，不要因为调用链追踪不完整而降级灰盒。\n"
        "6. 最终 scenarios 中至少 70% 必须是 `black_box_ready`，最多 30% 可以是 `gray_box_required`；不要把可从外部执行的场景标成 `black_box_hypothesis`。\n"
        "7. 每个 high 优先级场景必须回答：流程做什么、外部怎么触发、输入怎么构造、正常路径、异常路径、预期结果、可观测信号、灰盒辅助、SFMEA。\n"
        "8. normal_path/error_path/external_trigger/input_construction/expected_result 只能写测试人员从外部执行和观察的步骤，不要写函数名、源码文件、源码行号、内部变量或“调用 xxx”。源码函数/文件只能放在 key_call_chain/evidence_refs/gray_box_aid。\n"
        "9. 只有 entry_discovery 明确显示没有外部入口候选，或必须靠注入/trace/内部状态辅助观察时，才使用 `gray_box_required`；这种场景最多 1 个。`black_box_hypothesis` 只作为无法分类的临时状态，最终验收会失败。\n"
        "9b. If entry_discovery shows source_verification_status=no_external_entry_candidate or rejected_external_entry_candidate, or gray_box_allowed=true, gray_box_required is valid; candidates with validation_error are rejected evidence, not actionable external entries.\n"
        "10. 只输出 JSON，格式为 {\"scenarios\": [...]}，禁止输出 Markdown 或解释文字。\n"
        f"必填字段：{', '.join(AI_REQUIRED_SCENARIO_FIELDS)}。\n"
        f"sfmea 必填字段：{', '.join(AI_REQUIRED_SFMEA_FIELDS)}。\n\n"
        "上下文 JSON：\n"
        + json.dumps(compact, ensure_ascii=False)[:50000]
    )


def _enforce_ai_scenario_batch_gate(
    context: dict,
    accepted: list[dict],
    rejected: list[dict],
) -> None:
    if not accepted:
        return
    black_box_count = sum(1 for item in accepted if item.get("case_type") == BLACK_BOX_READY)
    gray_box_count = sum(1 for item in accepted if item.get("case_type") == GRAY_BOX_REQUIRED)
    black_box_ratio = black_box_count / max(1, len(accepted))
    has_external_trigger_hint = _context_has_actionable_external_trigger_hint(context)
    if black_box_ratio >= 0.7 and gray_box_count <= 1:
        return
    if (
        not has_external_trigger_hint
        and gray_box_count == len(accepted)
        and _context_allows_gray_box_without_actionable_entry(context)
    ):
        return

    if has_external_trigger_hint:
        reason = (
            "黑盒比例不足：上下文已有外部触发线索，但 AI 输出没有达到 70% black_box_ready，"
            "不能把可外部触发的场景整批降级为灰盒。"
        )
    else:
        reason = (
            "黑盒比例不足：AI 输出没有达到 70% black_box_ready，"
            "不能作为正式覆盖率推荐用例。"
        )
    for scenario in accepted:
        rejected.append({
            "scenario_id": scenario.get("scenario_id") or "scenario",
            "reason": reason,
        })
    accepted.clear()


def _context_has_actionable_external_trigger_hint(context: dict) -> bool:
    for item in context.get("external_trigger_candidates") or []:
        if not isinstance(item, dict):
            continue
        confidence = str(item.get("confidence") or "").strip().lower()
        if str(item.get("trigger") or "").strip() and confidence != "low":
            return True
    entry_discovery = context.get("entry_discovery") or {}
    for card in entry_discovery.get("cards") or []:
        if not isinstance(card, dict):
            continue
        if card.get("entry_trace_status") == "entry_found":
            return True
        for candidate in card.get("candidate_external_entries") or []:
            if isinstance(candidate, dict) and _entry_discovery_candidate_is_actionable(candidate):
                return True
    return False


def _context_allows_gray_box_without_actionable_entry(context: dict) -> bool:
    entry_discovery = context.get("entry_discovery") or {}
    cards = entry_discovery.get("cards") or []
    if not cards:
        return False
    for card in cards:
        if not isinstance(card, dict):
            continue
        if card.get("gray_box_allowed") is True:
            return True
        if card.get("source_verification_status") in {
            "no_external_entry_candidate",
            "rejected_external_entry_candidate",
        }:
            return True
    return False


def _parse_json_object(text: str) -> dict:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError as first_exc:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(raw[start:end + 1])
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError as second_exc:
                return {"_parse_error": f"AI 响应不是完整 JSON：{second_exc}"}
        return {"_parse_error": f"AI 响应不是完整 JSON：{first_exc}"}
    return {"_parse_error": "AI 响应为空或不是 JSON 对象"}


def _scenario_rejection_reason(scenario: dict) -> str | None:
    case_type = scenario.get("case_type")
    missing = [
        field for field in AI_REQUIRED_SCENARIO_FIELDS
        if field not in scenario
        or (
            scenario.get(field) in (None, "")
            and not (
                field == "verification_gaps"
                or (field == "gray_box_aid" and case_type == BLACK_BOX_READY)
            )
        )
        or (
            scenario.get(field) == []
            and field not in {"verification_gaps"}
        )
    ]
    if missing:
        return "缺少必填字段：" + ", ".join(missing)
    sfmea = scenario.get("sfmea")
    if not isinstance(sfmea, dict):
        return "sfmea 必须是对象"
    sfmea_missing = [
        field for field in AI_REQUIRED_SFMEA_FIELDS
        if field not in sfmea or sfmea.get(field) in (None, "", [])
    ]
    if sfmea_missing:
        return "SFMEA 缺少必填字段：" + ", ".join(sfmea_missing)
    if case_type not in {BLACK_BOX_READY, BLACK_BOX_HYPOTHESIS, GRAY_BOX_REQUIRED}:
        return f"case_type 不合法：{case_type}"
    if case_type in {BLACK_BOX_READY, BLACK_BOX_HYPOTHESIS} and _black_box_scenario_has_white_box_leak(scenario):
        return "黑盒步骤包含内部函数、源码路径、分支或内部变量操作"
    if len([s for s in scenario.get("observable_signals") or [] if str(s).strip()]) < 1:
        return "缺少可观测信号"
    return None


def _black_box_scenario_has_white_box_leak(scenario: dict) -> bool:
    text = "\n".join(
        str(scenario.get(key) or "")
        for key in (
            "external_trigger",
            "input_construction",
            "normal_path",
            "error_path",
            "expected_result",
        )
    )
    for value in scenario.get("observable_signals") or []:
        text += "\n" + str(value)
    for match in re.finditer(r"\b[A-Za-z_]\w*\s*\(", text or ""):
        name = match.group(0).split("(", 1)[0].strip()
        if name in _NON_FUNCTION_NAMES:
            continue
        if not _scenario_function_call_looks_like_public_surface(text or "", match):
            return True
    leak_rules = (
        re.compile(r"\b(call|invoke)\s+[A-Za-z_]\w*\s*\(", re.IGNORECASE),
        re.compile(r"调用\s*[A-Za-z_]\w*\s*\("),
        re.compile(r"调用\s*[A-Za-z_]\w*\b"),
        re.compile(r"\b[\w./\\-]+\.(?:c|h|cc|cpp|py|go|rs|java|js|ts)(?::\d+)?\b"),
        re.compile(r"\bif\s*\(|进入.*分支|覆盖.*分支"),
        re.compile(r"\b[A-Za-z_]\w*->[A-Za-z_]\w*\b"),
        re.compile(r"修改.*内部变量|设置.*内部变量"),
    )
    return any(rule.search(text) for rule in leak_rules)


def _scenario_function_call_looks_like_public_surface(text: str, match: re.Match) -> bool:
    line_start = text.rfind("\n", 0, match.start()) + 1
    prefix = text[line_start:match.start()].lower()
    if re.search(r"(?:\bcall|\binvoke)\s*$", prefix):
        return False
    public_tokens = (
        "json-rpc",
        "rpc",
        "cli",
        "command",
        "api",
        "http",
        "rest",
        "grpc",
        "endpoint",
        "management",
        "client",
        "public",
    )
    return any(token in prefix for token in public_tokens)


def _normalize_ai_scenario(scenario: dict) -> dict:
    normalized = {field: scenario.get(field) for field in AI_REQUIRED_SCENARIO_FIELDS}
    normalized["version"] = AI_TEST_DESIGN_VERSION
    normalized["key_call_chain"] = _coerce_string_list(scenario.get("key_call_chain"))
    normalized["observable_signals"] = _coerce_string_list(scenario.get("observable_signals"))
    normalized["evidence_refs"] = _coerce_string_list(scenario.get("evidence_refs"))
    normalized["related_gaps"] = _coerce_string_list(scenario.get("related_gaps"))
    normalized["verification_gaps"] = [
        item for item in _coerce_string_list(scenario.get("verification_gaps"))
        if not _is_no_gap_marker(item)
    ]
    if normalized.get("case_type") == BLACK_BOX_READY and not str(normalized.get("gray_box_aid") or "").strip():
        normalized["gray_box_aid"] = "不需要灰盒辅助；可按外部触发、预期结果和可观测信号执行。"
    if (
        normalized.get("case_type") == BLACK_BOX_HYPOTHESIS
        and _scenario_is_executable_black_box(normalized)
        and not _verification_gap_requires_gray(normalized.get("verification_gaps"))
    ):
        normalized["case_type"] = BLACK_BOX_READY
        normalized["classification_reason"] = (
            "模型原始标记为 black_box_hypothesis，但外部触发、输入、正常/异常路径、"
            "预期结果和可观测信号完整，且黑盒步骤无白盒泄漏；已归一化为 black_box_ready。"
        )
    return normalized


def _scenario_is_executable_black_box(scenario: dict) -> bool:
    required = (
        "external_trigger",
        "input_construction",
        "normal_path",
        "error_path",
        "expected_result",
    )
    if any(not str(scenario.get(field) or "").strip() for field in required):
        return False
    if not _coerce_string_list(scenario.get("observable_signals")):
        return False
    if _black_box_scenario_has_white_box_leak(scenario):
        return False
    text = "\n".join(str(scenario.get(field) or "") for field in required)
    blockers = (
        "尚未确认公开入口",
        "无法从外部",
        "必须灰盒",
        "需要灰盒",
        "内部变量",
        "源码分支",
        "mock",
        "stub",
        "hook",
    )
    return not any(token in text for token in blockers)


def _verification_gap_requires_gray(value: object) -> bool:
    text = "\n".join(_coerce_string_list(value)).lower()
    blockers = (
        "需要确认外部入口",
        "尚未确认公开入口",
        "无法从外部",
        "需要灰盒",
        "必须灰盒",
        "需要注入",
        "mock",
        "stub",
        "hook",
        "内部变量",
    )
    return any(token.lower() in text for token in blockers)


def _is_no_gap_marker(value: object) -> bool:
    return str(value or "").strip().lower() in {"", "无", "none", "n/a", "na", "no"}


def _coerce_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _attach_scenarios_to_gaps(gaps: list[dict], scenarios: list[dict]) -> None:
    for gap in gaps:
        name = str(gap.get("function_name") or gap.get("condition") or "")
        file_path = str(gap.get("file_path") or "")
        related: list[dict] = []
        for scenario in scenarios:
            related_gaps = [str(item) for item in scenario.get("related_gaps") or []]
            chain = [str(item) for item in scenario.get("key_call_chain") or []]
            evidence_refs = [str(item) for item in scenario.get("evidence_refs") or []]
            haystack = "\n".join([*related_gaps, *chain, *evidence_refs])
            if name and (
                name in haystack
                or any(name == item for item in related_gaps)
                or any(name == item for item in chain)
            ):
                related.append(scenario)
            elif file_path and file_path in haystack:
                related.append(scenario)
        if related:
            gap["test_scenarios"] = related[:5]


def _annotate_ai_recommendation_status(
    gaps: list[dict],
    *,
    use_ai: bool,
    ai_status: str,
    scenarios: list[dict],
    deterministic_fallback: bool = False,
) -> None:
    if not use_ai:
        return
    for gap in gaps:
        related = gap.get("test_scenarios") or []
        gap["ai_generation_status"] = ai_status
        gap["ai_scenario_count"] = len(related)
        gap["deterministic_case_role"] = (
            "fallback_recommendation" if deterministic_fallback else "evidence_scaffold"
        )
        if related:
            gap["ai_recommendation_status"] = "has_ai_scenarios"
        elif ai_status == "available" and not scenarios:
            gap["ai_recommendation_status"] = "no_valid_ai_scenarios"
        elif ai_status in {"failed", "unavailable"}:
            gap["ai_recommendation_status"] = f"ai_{ai_status}"
        else:
            gap["ai_recommendation_status"] = "no_related_ai_scenario"


async def _write_coverage_design_artifacts(
    artifact_dir: Path | None,
    *,
    context: dict,
    entry_discovery: dict,
    design: dict,
    ai_debug: dict | None,
) -> list[str]:
    if artifact_dir is None:
        return []

    def _write() -> list[str]:
        artifact_warnings: list[str] = []

        def write_json(path: Path, payload: dict, *, label: str) -> None:
            try:
                path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as exc:
                artifact_warnings.append(_coverage_artifact_warning(label, path, exc))

        try:
            artifact_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return [_coverage_artifact_warning("artifact_dir", artifact_dir, exc)]

        write_json(
            artifact_dir / "coverage_test_context.json",
            context,
            label="coverage_test_context",
        )
        write_json(
            artifact_dir / "coverage_entry_discovery.json",
            entry_discovery,
            label="coverage_entry_discovery",
        )
        external_agent = _coverage_external_agent_artifact(design)
        write_json(
            artifact_dir / "coverage_external_agent_discovery.json",
            external_agent,
            label="coverage_external_agent_discovery",
        )
        if ai_debug:
            debug_dir = artifact_dir / "debug"
            try:
                debug_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")[:17]
                write_json(
                    debug_dir / f"coverage_ai_{ts}.json",
                    ai_debug,
                    label="coverage_ai_debug",
                )
            except OSError as exc:
                artifact_warnings.append(_coverage_artifact_warning("coverage_ai_debug", debug_dir, exc))
        if artifact_warnings:
            design.setdefault("warnings", []).extend(artifact_warnings)
        write_json(
            artifact_dir / "coverage_test_design.json",
            design,
            label="coverage_test_design",
        )
        return artifact_warnings

    return await asyncio.to_thread(_write)


def _coverage_artifact_warning(label: str, path: Path, exc: OSError) -> str:
    reason = str(exc).strip() or exc.__class__.__name__
    return (
        "coverage artifact write failed: "
        f"{label} at {path}: {reason}"
    )


def _coverage_external_agent_artifact(design: dict) -> dict:
    rows: list[dict] = []
    provider_status_counts: dict[str, dict[str, int]] = {}
    for gap in design.get("gaps") or []:
        if gap.get("kind") != "function":
            continue
        evidence = gap.get("evidence") or {}
        context = evidence.get("external_agent") if isinstance(evidence, dict) else {}
        if not isinstance(context, dict) or not context:
            continue
        provider_status = context.get("provider_status") or {}
        if isinstance(provider_status, dict):
            for provider, status in provider_status.items():
                provider_name = str(provider or "external_agent")
                status_name = str(status or "unknown")
                bucket = provider_status_counts.setdefault(provider_name, {})
                bucket[status_name] = bucket.get(status_name, 0) + 1
        rows.append({
            "function_name": gap.get("function_name"),
            "file_path": gap.get("file_path"),
            "provider_status": provider_status if isinstance(provider_status, dict) else {},
            "validated_entries": context.get("validated_entries") or [],
            "unverified_entries": context.get("unverified_entries") or [],
            "raw_results": context.get("raw_results") or [],
        })
    return {
        "version": "coverage-external-agent-discovery-v1",
        "agent_discovery_session_id": design.get("agent_discovery_session_id"),
        "items": rows,
        "summary": {
            "function_count": len(rows),
            "validated_entry_count": sum(len(row.get("validated_entries") or []) for row in rows),
            "unverified_entry_count": sum(len(row.get("unverified_entries") or []) for row in rows),
            "provider_count": len(provider_status_counts),
            "provider_status_counts": provider_status_counts,
        },
    }


async def build_coverage_test_design(
    modules: list[ModuleCoverage],
    *,
    workspace_id: str | None,
    repo_path: str | None,
    use_ai: bool = False,
    llm: BaseLLMClient | None = None,
    artifact_dir: Path | None = None,
    analysis_id: str | None = None,
    report_output_dir: Path | None = None,
) -> dict:
    """Build the ``coverage-test-design-v1`` structure for a coverage report.

    Produces ``{version, summary, gaps, warnings}`` where ``gaps`` mixes
    uncovered-function gaps (with entry-oriented trigger paths + black/gray-box
    cases) and uncovered-branch gaps (designed straight from the condition).
    When the coverage is not bound to a workspace/repo on disk, no source-backed
    trigger paths are fabricated — only parse-level guidance is returned.
    """
    workspace_bound = _existing_repo_root(repo_path) is not None and bool(workspace_id)
    agent_session: AgentDiscoverySession | None = None
    if (
        settings.external_agents_enabled
        and settings.agent_discovery_session_enabled
        and artifact_dir is not None
        and _existing_repo_root(repo_path) is not None
    ):
        agent_session = create_agent_discovery_session(
            repo_path=str(_existing_repo_root(repo_path)),
            goal="coverage_entry",
            artifact_dir=artifact_dir,
            coverage_analysis_id=analysis_id,
            workspace_id=workspace_id,
        )

    function_gaps = await _build_black_box_function_recommendations(
        modules,
        workspace_id=workspace_id,
        repo_path=repo_path,
        agent_session=agent_session,
    )
    branch_gaps = _build_branch_gaps(modules, workspace_bound=workspace_bound)
    gaps = [*function_gaps, *branch_gaps]
    context = await build_coverage_test_context(
        modules,
        workspace_id=workspace_id,
        repo_path=repo_path,
        deterministic_gaps=gaps,
        report_output_dir=report_output_dir,
    )
    entry_discovery = context.get("entry_discovery") or {
        "version": COVERAGE_ENTRY_DISCOVERY_VERSION,
        "cards": [],
        "summary": {},
    }

    tool_status = _aggregate_tool_status(function_gaps, repo_path=repo_path)
    warnings = _design_warnings(
        workspace_bound=workspace_bound,
        workspace_id=workspace_id,
        repo_path=repo_path,
        tool_status=tool_status,
        function_gaps=function_gaps,
    )
    warnings.extend(context.get("warnings") or [])

    ai_status = "skipped"
    ai_debug: dict | None = None
    test_scenarios: list[dict] = []
    rejected_scenarios: list[dict] = []
    if use_ai:
        try:
            active_llm = llm or await create_llm_client_from_active()
            ai_debug = await _generate_ai_test_scenarios(active_llm, context)
            test_scenarios = ai_debug.get("accepted") or []
            rejected_scenarios = ai_debug.get("rejected") or []
            ai_status = "available"
        except ValueError as exc:
            ai_status = "unavailable"
            warnings.append(f"真实 AI 未配置或不可用：{exc}")
        except Exception as exc:
            ai_status = "failed"
            warnings.append(f"真实 AI 覆盖率用例生成失败：{exc}")
            logger.warning("Coverage AI scenario generation failed: %s", exc)
    _attach_scenarios_to_gaps(gaps, test_scenarios)
    _annotate_ai_recommendation_status(
        gaps,
        use_ai=use_ai,
        ai_status=ai_status,
        scenarios=test_scenarios,
        deterministic_fallback=use_ai and not test_scenarios,
    )

    gap_black_box_ready_count = sum(
        1 for g in gaps
        if (g.get("black_box_readiness") or {}).get("case_type") == BLACK_BOX_READY
    )
    gap_black_box_hypothesis_count = sum(
        1 for g in gaps
        if (g.get("black_box_readiness") or {}).get("case_type") == BLACK_BOX_HYPOTHESIS
    )
    gap_gray_box_required_count = sum(
        1 for g in gaps
        if (
            (g.get("black_box_readiness") or {}).get("case_type") == GRAY_BOX_REQUIRED
            or g.get("gray_box_required")
        )
    )
    if use_ai:
        if test_scenarios:
            recommendation_source = test_scenarios
            recommendation_source_label = "ai_scenarios"
        else:
            recommendation_source = gaps
            recommendation_source_label = "deterministic_fallback"
    else:
        recommendation_source = gaps
        recommendation_source_label = "deterministic_gaps"

    def _case_type(item: dict) -> str | None:
        return (
            item.get("case_type")
            or (item.get("black_box_readiness") or {}).get("case_type")
        )

    summary = {
        "module_count": len(modules),
        "uncovered_function_count": len(function_gaps),
        "uncovered_branch_count": len(branch_gaps),
        "black_box_ready_count": sum(1 for s in recommendation_source if _case_type(s) == BLACK_BOX_READY),
        "black_box_hypothesis_count": sum(1 for s in recommendation_source if _case_type(s) == BLACK_BOX_HYPOTHESIS),
        "gray_box_required_count": sum(1 for s in recommendation_source if _case_type(s) == GRAY_BOX_REQUIRED),
        "recommendation_source": recommendation_source_label,
        "gap_black_box_ready_count": gap_black_box_ready_count,
        "gap_black_box_hypothesis_count": gap_black_box_hypothesis_count,
        "gap_gray_box_required_count": gap_gray_box_required_count,
        "white_box_lint_failed_count": sum(
            1 for g in gaps
            if not (g.get("white_box_leak_check") or {}).get("passed", True)
        ),
        "high_risk_count": sum(1 for g in gaps if g.get("risk_level") == "high"),
        "workspace_bound": workspace_bound,
        "tool_status": tool_status,
        "ai_status": ai_status,
        "ai_scenario_count": len(test_scenarios),
        "ai_rejected_scenario_count": len(rejected_scenarios),
        "evidence_source_counts": context.get("evidence_source_counts") or {},
    }

    design = {
        "version": COVERAGE_TEST_DESIGN_VERSION,
        "analysis_id": analysis_id,
        "workspace_id": workspace_id,
        "repo_path": repo_path,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "agent_discovery_session_id": agent_session.session_id if agent_session else None,
        "test_context": {
            "version": context.get("version"),
            "evidence_source_counts": context.get("evidence_source_counts") or {},
            "warnings": context.get("warnings") or [],
        },
        "entry_discovery": entry_discovery,
        "test_scenarios": test_scenarios,
        "test_scenario_validation": {
            "accepted_count": len(test_scenarios),
            "rejected_count": len(rejected_scenarios),
            "rejected": rejected_scenarios,
        },
        "gaps": gaps,
        "warnings": warnings,
    }
    artifact_warnings = await _write_coverage_design_artifacts(
        artifact_dir,
        context=context,
        entry_discovery=entry_discovery,
        design=design,
        ai_debug=ai_debug,
    )
    for warning in artifact_warnings:
        if warning not in warnings:
            warnings.append(warning)
    return design


def _aggregate_tool_status(function_gaps: list[dict], *, repo_path: str | None) -> dict:
    repo_root = _existing_repo_root(repo_path)
    rg_available = shutil.which("rg") is not None
    cgc_ok = any(
        (g.get("tool_status") or {}).get("cgc") == "available" for g in function_gaps
    )
    gitnexus_ok = any(
        (g.get("tool_status") or {}).get("gitnexus") == "available" for g in function_gaps
    )
    external_agent_statuses = [
        (g.get("tool_status") or {}).get("external_agent") for g in function_gaps
    ]
    if not settings.external_agents_enabled:
        external_agent = "disabled"
    elif any(status == "available" for status in external_agent_statuses):
        external_agent = "available"
    elif any(status == "invalid_output" for status in external_agent_statuses):
        external_agent = "invalid_output"
    elif any(status == "error" for status in external_agent_statuses):
        external_agent = "error"
    elif any(status == "timeout" for status in external_agent_statuses):
        external_agent = "timeout"
    elif any(status == "rejected_command" for status in external_agent_statuses):
        external_agent = "rejected_command"
    else:
        external_agent = "unavailable"
    source_ok = any(g.get("source_window") for g in function_gaps)
    return {
        "joern": "unavailable_reserved",
        "cgc": "available" if cgc_ok else "unavailable",
        "gitnexus": "available" if gitnexus_ok else "unavailable",
        "external_agent": external_agent,
        "ripgrep": "available" if rg_available else "unavailable",
        "source": "available" if source_ok else (
            "available_no_match" if repo_root is not None else "unavailable"
        ),
    }


def _design_warnings(
    *,
    workspace_bound: bool,
    workspace_id: str | None,
    repo_path: str | None,
    tool_status: dict,
    function_gaps: list[dict],
) -> list[str]:
    warnings: list[str] = []
    if not workspace_bound:
        if not workspace_id:
            warnings.append("覆盖率未绑定工作区：仅解析覆盖率，未做深度触发路径设计；请绑定工作区后重试")
        elif not _existing_repo_root(repo_path):
            warnings.append("绑定的仓库路径在本机不可访问：无法读取源码，未生成触发路径")
    if tool_status.get("joern", "").startswith("unavailable"):
        warnings.append("Joern 工具未接入（预留）：已使用 CGC/ripgrep 降级，缺少精确边界值/错误路径分析")
    if tool_status.get("cgc") != "available" and workspace_bound:
        warnings.append("CGC 不可用：调用链/调用点来自 ripgrep 文本搜索，可能不完整")
    if tool_status.get("gitnexus") != "available" and workspace_bound:
        warnings.append("GitNexus 图谱不可用：缺少 scope/社区上下文增强")
    if tool_status.get("ripgrep") != "available" and workspace_bound:
        warnings.append("ripgrep 不可用：无法进行调用点搜索，触发路径分析严重受限")
    gray_only = [g for g in function_gaps if g.get("gray_box_required")]
    if gray_only:
        warnings.append(
            f"{len(gray_only)} 个未覆盖函数的多源入口发现仍未确认外部触发，"
            "已给出灰盒辅助观察方案"
        )
    return warnings


def _module_to_dict(m: ModuleCoverage) -> dict:
    return {
        "module_path": m.module_path,
        "line_rate": m.line_rate,
        "branch_rate": m.branch_rate,
        "function_rate": m.function_rate,
        "uncovered_lines": m.uncovered_lines[:200],
        "uncovered_branches": m.uncovered_branches[:100],
        "uncovered_functions": m.uncovered_functions[:100],
        "function_hits": [_function_hit_to_dict(hit) for hit in m.function_hits[:500]],
        "files": [
            {
                "filename": f.filename,
                "line_rate": f.line_rate,
                "branch_rate": f.branch_rate,
                "lines_covered": f.lines_covered,
                "lines_total": f.lines_total,
                "uncovered_lines": f.uncovered_lines[:50],
                "uncovered_functions": f.uncovered_functions[:30],
                "function_hits": [
                    _function_hit_to_dict(hit) for hit in f.function_hits[:100]
                ],
            }
            for f in m.files[:50]
        ],
    }


def _dict_to_module(d: dict) -> ModuleCoverage:
    return ModuleCoverage(
        module_path=d["module_path"],
        line_rate=d.get("line_rate", 0),
        branch_rate=d.get("branch_rate", 0),
        function_rate=d.get("function_rate", 0),
        uncovered_lines=d.get("uncovered_lines", []),
        uncovered_branches=d.get("uncovered_branches", []),
        uncovered_functions=d.get("uncovered_functions", []),
        function_hits=[_dict_to_function_hit(h) for h in d.get("function_hits", [])],
        files=[
            FileCoverage(
                filename=f.get("filename", ""),
                line_rate=f.get("line_rate", 0),
                branch_rate=f.get("branch_rate", 0),
                lines_covered=f.get("lines_covered", 0),
                lines_total=f.get("lines_total", 0),
                uncovered_lines=f.get("uncovered_lines", []),
                uncovered_functions=f.get("uncovered_functions", []),
                function_hits=[
                    _dict_to_function_hit(h) for h in f.get("function_hits", [])
                ],
            )
            for f in d.get("files", [])
        ],
    )


def _function_hit_to_dict(hit: FunctionHit) -> dict:
    return {
        "feature_name": hit.feature_name,
        "module_name": hit.module_name,
        "function_name": hit.function_name,
        "file_path": hit.file_path,
        "line_start": hit.line_start,
        "line_end": hit.line_end,
        "triggered": hit.triggered,
        "hit_count": hit.hit_count,
        "raw_location": hit.raw_location,
        "raw": hit.raw,
    }


def _dict_to_function_hit(d: dict) -> FunctionHit:
    return FunctionHit(
        function_name=d.get("function_name", ""),
        file_path=d.get("file_path", ""),
        feature_name=d.get("feature_name", ""),
        module_name=d.get("module_name", ""),
        line_start=d.get("line_start"),
        line_end=d.get("line_end"),
        triggered=bool(d.get("triggered", False)),
        hit_count=int(d.get("hit_count", 0) or 0),
        raw_location=d.get("raw_location", ""),
        raw=d.get("raw", {}),
    )
