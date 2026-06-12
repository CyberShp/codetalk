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
from urllib.parse import unquote, urlparse

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
    merge_agent_provider_status,
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
    ".c", ".h", ".hh", ".hpp", ".hxx", ".cc", ".cpp", ".cxx", ".ipp", ".inl",
    ".py", ".go", ".rs", ".java",
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".cs",
    ".rb", ".php", ".kt", ".kts", ".swift", ".m", ".scala",
    ".vue", ".svelte", ".astro", ".mdx",
)
_SOURCE_FILE_EXTS = {ext for ext in _SOURCE_EXTENSION_CANDIDATES if ext}
_SOURCE_PATH_EXTENSION_PATTERN = "|".join(
    re.escape(ext.lstrip("."))
    for ext in sorted(_SOURCE_FILE_EXTS, key=lambda value: (-len(value), value))
)
_SOURCE_PATH_RE = re.compile(
    rf"\b[\w./\\-]+\.(?:{_SOURCE_PATH_EXTENSION_PATTERN})(?::\d+)?\b"
)
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
    r"^[\w\s\*&:<>,~\[\]\?]*?\b([A-Za-z_]\w*)\s*\([^;{}]*\)"
    r"\s*(?::\s*[\w\\|?\[\]]+)?\s*(?:\{|:|$)"
)
_ASSIGNED_FUNCTION_DEF_RES = (
    re.compile(
        r"^\s*[-+]\s*\([^)]*\)\s*(?P<name>[A-Za-z_$][\w$]*)\s*(?::|\{)"
    ),
    re.compile(
        r"^\s*func\s+\([^)]*\)\s+(?P<name>[A-Za-z_$][\w$]*)\s*\("
    ),
    re.compile(
        r"^\s*(?:(?:pub(?:\([^)]*\))?|async|const|unsafe)\s+)*"
        r"(?:extern\s+(?:\"[^\"]+\"\s+)?)?"
        r"fn\s+(?P<name>[A-Za-z_]\w*)\s*(?:<[^>{}]*>)?\s*\("
    ),
    re.compile(
        r"^\s*def\s+(?:(?:self|[A-Z][\w:]*)\.)"
        r"(?P<name>[A-Za-z_$][\w$]*[!?=]?)\b"
    ),
    re.compile(
        r"^\s*(?:module\.)?exports\.(?P<name>[A-Za-z_$][\w$]*)"
        r"\s*=\s*(?:async\s*)?"
        r"(?:function\b|(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>)"
    ),
    re.compile(
        r"^\s*(?:(?:private|protected|override|final|abstract|implicit|inline|"
        r"given|transparent)\s+)*def\s+(?P<name>[A-Za-z_$][\w$]*)"
        r"\s*(?:\([^)]*\))?\s*(?::\s*[^=]+)?\s*(?:=|\{|$)"
    ),
    re.compile(
        r"^\s*(?:(?:public|private|fileprivate|internal|open|static|class|"
        r"mutating|nonmutating|override|final|required|convenience)\s+)*"
        r"func\s+(?P<name>[A-Za-z_$][\w$]*)\s*\("
    ),
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
    assigned_name = _match_assigned_function_def_name(line)
    if assigned_name:
        return assigned_name
    before_paren = stripped.split("(", 1)[0]
    if "=" in before_paren and not stripped.startswith("def "):
        return None
    match = _FUNC_DEF_RE.match(line)
    if not match:
        return None
    name = match.group(1)
    if _looks_like_bare_function_invocation(stripped):
        return None
    return None if name in _NON_FUNCTION_NAMES else name


def _looks_like_bare_function_invocation(stripped_line: str) -> bool:
    value = str(stripped_line or "").strip().rstrip(";")
    if value.endswith(("{", ":")):
        return False
    return bool(re.match(r"^[A-Za-z_]\w*\s*\(.*\)\s*$", value))


def _match_assigned_function_def_name(line: str) -> str | None:
    for pattern in _ASSIGNED_FUNCTION_DEF_RES:
        assigned = pattern.match(line)
        if assigned:
            name = assigned.group("name")
            return None if name in _NON_FUNCTION_NAMES else name
    return None


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


def _line_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _definition_encloses_line(lines: list[str], def_idx: int, target_idx: int) -> bool:
    if def_idx < 0 or def_idx >= len(lines) or target_idx <= def_idx:
        return True
    if not _match_assigned_function_def_name(lines[def_idx]):
        return True

    balance = 0
    saw_block = False
    for idx in range(def_idx, min(target_idx, len(lines) - 1) + 1):
        text = lines[idx]
        balance += text.count("{") - text.count("}")
        if "{" in text:
            saw_block = True
        if idx < target_idx and ";" in text and balance <= 0:
            return False
    return saw_block and balance > 0


def _is_sibling_definition_boundary(lines: list[str], fn_start: int, pos: int) -> bool:
    if pos <= fn_start or pos >= len(lines):
        return False
    if _line_indent(lines[pos]) > _line_indent(lines[fn_start]):
        return False
    return (
        _match_def_name(lines[pos]) is not None
        or _match_multiline_def_name(lines, pos) is not None
    )
_BRANCH_KEYWORD_RE = re.compile(
    r"\b(if|unless|else\s+if|elif|switch|case|default|while|for|catch|except|when|guard)\b"
    r"|return\s+-[A-Za-z0-9_]+|goto\s+\w+",
    re.IGNORECASE,
)
_CALLER_GUARD_RE = re.compile(
    r"\b(if|unless|else\s+if|elif|switch|case|default|while|for|catch|except|when|guard)\b",
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
_REQUEST_CONTAINER_NAMES = (
    "json", "args", "form", "query", "body", "data", "params",
    "headers", "cookies", "values", "files", "query_params", "path_params",
)
_REQUEST_CONTAINER_PATTERN = "|".join(re.escape(name) for name in _REQUEST_CONTAINER_NAMES)
_REQ_FIELD_RE = re.compile(
    rf"\b(?:req|attrs|opts|ctx)\.(?!(?:{_REQUEST_CONTAINER_PATTERN})\b)([A-Za-z_]\w*)\b"
)
_REQUEST_FIELD_RES = (
    re.compile(
        r"\b(?P<container>payload|message|msg|record)"
        r"(?:\??\.)\s*(?P<field>[A-Za-z_][\w-]*)\b"
    ),
    re.compile(
        r"\b(?P<container>payload|message|msg|record)"
        r"\s*\[\s*['\"](?P<field>[A-Za-z_][\w-]*)['\"]\s*\]"
    ),
    re.compile(
        r"\b(?:event|evt)"
        r"(?:\??\.(?:detail|data|payload))"
        r"(?:\??\.)\s*([A-Za-z_][\w-]*)\b"
    ),
    re.compile(
        r"\b(?:event|evt)"
        r"(?:\??\.(?:detail|data|payload))"
        r"\s*\[\s*['\"]([A-Za-z_][\w-]*)['\"]\s*\]"
    ),
    re.compile(
        r"\b(?:request|req|payload|body|params|query|data)"
        rf"(?:\??\.(?:{_REQUEST_CONTAINER_PATTERN}))?"
        r"(?:\??\.)?\s*\[\s*['\"]([A-Za-z_][\w-]*)['\"]\s*\]"
    ),
    re.compile(
        r"\b(?:request|req|payload|body|params|query|data)"
        rf"(?:\??\.(?:{_REQUEST_CONTAINER_PATTERN}))?"
        r"\??\.get\s*\(\s*['\"]([A-Za-z_][\w-]*)['\"]"
    ),
    re.compile(
        r"\b(?:request|req)"
        rf"\??\.(?:{_REQUEST_CONTAINER_PATTERN})"
        r"\??\.(?!get\b)([A-Za-z_][\w-]*)\b"
    ),
    re.compile(
        r"\b(?:request|req)"
        r"\.(?:GET|POST|FILES|COOKIES|headers|META)"
        r"(?:\s*\[\s*|\s*\.get\s*\(\s*)['\"]([A-Za-z_][\w-]*)['\"]"
    ),
    re.compile(
        r"\b(?:c|ctx|context)\."
        r"(?:Param|Query|DefaultQuery|PostForm|DefaultPostForm|GetHeader)"
        r"\s*\(\s*['\"]([A-Za-z_][\w-]*)['\"]"
    ),
    re.compile(
        r"\b[A-Za-z_]\w*\.PathValue\s*\(\s*['\"]([A-Za-z_][\w-]*)['\"]"
    ),
    re.compile(
        r"\b[A-Za-z_]\w*\.URL\.Query\s*\(\s*\)\.Get\s*\(\s*['\"]([A-Za-z_][\w-]*)['\"]"
    ),
    re.compile(
        r"\bcall\."
        r"(?:parameters|request\.queryParameters|request\.headers|request\.cookies)"
        r"\s*\[\s*['\"]([A-Za-z_][\w-]*)['\"]\s*\]"
    ),
    re.compile(
        r"\bcall\.request\."
        r"(?:queryParameters|headers|cookies)"
        r"\.get\s*\(\s*['\"]([A-Za-z_][\w-]*)['\"]"
    ),
    re.compile(
        r"\b(?:params|request\.params)"
        r"\s*\[\s*:([A-Za-z_][\w-]*)\s*\]"
    ),
    re.compile(
        r"\$(?:request|req)"
        r"\s*->\s*(?:input|query|post|get|route|header|cookie)"
        r"\s*\(\s*['\"]([A-Za-z_][\w-]*)['\"]"
    ),
)
_MESSAGE_ENVELOPE_FIELD_NAMES = {
    "attributes", "headers", "key", "offset", "partition", "timestamp",
    "topic", "value",
}
_REQUEST_DESTRUCTURE_RE = re.compile(
    r"\{(?P<fields>[^{}]+)\}\s*=\s*"
    r"\b(?:request|req)"
    r"\.(?:json|args|form|query|body|data|params|headers|cookies|values|files)\b"
)
_RAILS_STRONG_PARAM_REQUIRE_RE = re.compile(
    r"\bparams\s*\.\s*require\s*\(\s*:([A-Za-z_][\w-]*)\s*\)"
)
_RAILS_STRONG_PARAM_PERMIT_RE = re.compile(
    r"\.\s*permit\s*\((?P<fields>[^)]*)\)"
)
_RUBY_SYMBOL_ARG_RE = re.compile(r":([A-Za-z_][\w-]*)")
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
    r"|\badd_[A-Za-z0-9_]*Servicer_to_server\s*\("
    r"|\b(?:[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\s*\.\s*)?Handle(?:Func)?\s*\("
    r"|\.[ \t]*(?:register|subscribe|on|once|listen|addEventListener|addListener|"
    r"addHandler|add_listener|add_handler|add_job|schedule)\s*\(",
    re.IGNORECASE,
)
_INLINE_ROUTE_DEFINITION_RE = re.compile(
    r"\bAction(?:\.async)?\s*(?:\(|\{)",
    re.IGNORECASE,
)
_ENTRY_DECORATOR_KIND_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("webhook", ("webhook", "hook")),
    ("cli", ("click.command", "typer.command", "cli.command", ".command", "console")),
    ("route", ("route", "router", "endpoint", "controller", "view",
               "requestmapping", "getmapping", "postmapping", "putmapping",
               "patchmapping", "deletemapping", "headmapping", "optionsmapping",
               "httpget", "httppost", "httpput", "httppatch", "httpdelete",
               "httphead", "httpoptions",
               "@get", "@post", "@put", "@patch", "@delete", "@head", "@options",
               ".get", ".post", ".put", ".patch", ".delete", ".head", ".options",
               ".api_route", ".websocket", "websocket", "socket_route")),
    ("api", (
        "api", "rpc", "grpc", "http", "request",
        "graphql", "resolver", "query", "mutation", "subscription",
    )),
    ("scheduler", ("schedule", "scheduler", "scheduled", "cron", "periodic", "add_job")),
    ("timer", ("timer", "timeout", "poller", "interval")),
    ("job", (
        "celery", "shared_task", "dramatiq", "huey", "rq",
        ".task", "@task", "@job", ".job", "job(",
    )),
    ("message", ("subscribe", "subscriber", "topic", "queue", "message", "event", "listener",
                 ".on", ".listen", "addeventlistener", "addlistener", "consumer")),
    ("callback", ("callback", ".callback")),
)
_PUBLIC_CALLBACK_START_RE = re.compile(
    r"(?:\.\s*(?:"
    r"get|post|put|patch|delete|head|options|route|use|"
    r"subscribe|on|listen|addEventListener|addListener|addHandler|add_listener|add_handler|register|"
    r"add_job|schedule"
    r")|(?:^|\b)(?:path|re_path))\s*\(",
    re.IGNORECASE,
)
_ROUTE_DSL_START_RE = re.compile(
    r"(?<![\w.])(?:get|post|put|patch|delete|head|options|any|route|websocket)"
    r"\s*\(\s*['\"]",
    re.IGNORECASE,
)
_CALLBACK_ASSIGN_RE = re.compile(
    r"\.(?:[A-Za-z_]\w*(?:cb|callback|handler|fn|op|ops)|(?:cb|callback|handler|fn|op|ops))\s*="
    r"\s*(?P<symbol>[A-Za-z_]\w*)",
    re.IGNORECASE,
)
_DISPATCH_TABLE_ENTRY_RE = re.compile(
    r"""(?P<quote>['"])(?P<key>[A-Za-z0-9_.:/-]{1,80})(?P=quote)\s*,\s*&?(?P<symbol>[A-Za-z_]\w*)\b"""
)
_DISPATCH_TABLE_HANDLER_RE = re.compile(
    r"(?:\.(?:handler|handlers|callback|cb|fn|func|function|method|op|ops|entry)\s*="
    r"|\b(?:handler|handlers|callback|cb|fn|func|function|method|op|ops|entry)\s*:)"
    r"\s*&?(?P<symbol>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)\b",
    re.IGNORECASE,
)
_DISPATCH_TABLE_KEY_RE = re.compile(
    r"(?:\.(?:name|cmd|command|key|op|operation|route|path|url|topic|event|message|type|id)\s*="
    r"|\b(?:name|cmd|command|key|op|operation|route|path|url|topic|event|message|type|id)\s*:)"
    r"\s*(?P<quote>['\"])(?P<key>[A-Za-z0-9_.:/-]{1,80})(?P=quote)",
    re.IGNORECASE,
)
_DISPATCH_TABLE_CONTEXT_RE = re.compile(
    r"\b(?:cmd|command|cli|rpc|api|request|handler|handlers|op|ops|operation|"
    r"dispatch|route|endpoint|message|event|callback|table|registry)\b",
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
    "file", "upload", "download", "import", "export", "stdin", "filesystem",
    "watcher", "environment", "env var", "env",
)

_WHITE_BOX_LEAK_RULES: tuple[tuple[str, re.Pattern], ...] = (
    ("source_path", _SOURCE_PATH_RE),
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
    ("cli", ("/cli.", "/cli/", "_cli", "cli_", "/cmd", "command", "argv", "getopt", "main(", "_main", "console", "shell")),
    ("webhook", ("webhook", "webhooks", "hook_handler", "hook_delivery")),
    ("route", ("route", "routes", "router", "controller", "view",
               ".get", ".post", ".put", ".patch", ".delete", ".head", ".options", ".any",
               "path(", "re_path(", "urlpatterns",
               ".websocket", "websocket(")),
    ("endpoint", ("endpoint", "endpoints", "servlet")),
    ("api", ("/api", "api_", "_api", "route", "router", "handle_request",
             "controller", "endpoint", "server", "rest", "grpc", "http", "rpc",
             "view", "/web", "servlet")),
    ("queue", ("queue", "topic", "consumer", "subscriber", "producer", "work_queue")),
    ("scheduler", ("scheduler", "schedule", "scheduled", "cron", "periodic", "add_job")),
    ("timer", ("timer", "timers", "poller", "polling", "timeout", "interval", "tick")),
    ("job", ("job", "jobs", "worker", "task", "background")),
    ("message", ("message", "/msg", "event", "consumer", "subscriber", "publish", "queue",
                 "kafka", "/mq", "callback", "signal", "/irq", "isr", "dispatch", "listener",
                 "notify", "on_")),
    ("config", ("config", "/conf", "settings", "option", ".ini", ".yaml", ".yml", ".toml",
                "parse_args", "load_config", "env")),
    ("file", ("readfile", "read_file", "loadfile", "load_file", "fread", "fopen", "open(",
              "ingest", "import", "upload", "download", "export", "filesystem",
              "watch", "watcher", "/io", "input", "stdin", "scan")),
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
        if repo_root is not None:
            _augment_entry_paths_input_hints(repo_root, entry_paths)
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
        black_box_cases = _downgrade_ready_black_box_cases(
            black_box_cases,
            "black-box execution leaked white-box terms and needs rewrite",
        )
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
            "input_hints": _coerce_input_hints(item.get("input_hints")),
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
    _merge_entry_external_trigger(existing, agent_entry)
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
    input_hints = _merge_ordered_input_hints(
        existing.get("input_hints"),
        agent_entry.get("input_hints"),
    )
    if input_hints:
        existing["input_hints"] = input_hints
    reason = str(agent_entry.get("reason") or "").strip()
    if reason:
        confirmations = list(existing.get("confirming_evidence") or [])
        if reason not in confirmations:
            confirmations.append(reason)
        existing["confirming_evidence"] = confirmations[:4]


def _merge_entry_external_trigger(existing: dict, incoming: dict) -> None:
    incoming_trigger = str(incoming.get("external_trigger") or "").strip()
    if not incoming_trigger:
        return
    existing_trigger = str(existing.get("external_trigger") or "").strip()
    if not existing_trigger:
        existing["external_trigger"] = incoming_trigger
        return
    if existing_trigger == incoming_trigger:
        return
    triggers = _merge_ordered_strings(
        existing.get("confirming_external_triggers"),
        [incoming_trigger],
    )
    if triggers:
        existing["confirming_external_triggers"] = triggers[:4]


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
    normalized_target = _agent_chain_symbol_key(function_name)
    entry_symbol = str(item.get("entry_symbol") or item.get("entry_label") or "").strip()
    normalized_entry_symbol = _agent_chain_symbol_key(entry_symbol)
    if entry_symbol and normalized_entry_symbol != normalized_target:
        return False
    chain = _normalize_agent_entry_chain(item.get("chain"))
    normalized_chain = [_agent_chain_symbol_key(value) for value in chain]
    if normalized_chain and any(value != normalized_target for value in normalized_chain):
        return False
    entry_file = str(item.get("entry_file") or "").replace("\\", "/")
    hit_file = str(hit.file_path or "").replace("\\", "/")
    if entry_file and hit_file and entry_file != hit_file and not hit_file.endswith(entry_file):
        return False
    return bool(normalized_entry_symbol == normalized_target or normalized_chain == [normalized_target])


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
    normalized_target = _agent_chain_symbol_key(target)
    chain = _normalize_agent_entry_chain(item.get("chain"))
    return bool(chain and normalized_target not in {
        _agent_chain_symbol_key(segment) for segment in chain
    })


def _agent_chain_symbol_key(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.split("@", 1)[0].strip()
    text = re.sub(r"\([^)]*\)\s*$", "", text).strip()
    text = re.sub(r"[:#]L?\d+(?:[-,~]L?\d+)?$", "", text, flags=re.IGNORECASE).strip()
    text = text.replace("::", ".")
    text = text.rsplit(".", 1)[-1]
    text = text.rsplit("/", 1)[-1]
    return text.strip()


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
        status_by_provider[result.provider] = merge_agent_provider_status(
            status_by_provider.get(result.provider),
            result.status,
        )
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
                "input_hints": _coerce_input_hints(entry.input_hints),
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
    provider_status["external_agent"] = merge_agent_provider_status(
        provider_status.get("external_agent"),
        "error",
    )
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
                "input_hints": _merge_ordered_input_hints(
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


_INTERNAL_INPUT_HINTS = {
    "self", "cls", "this", "ctx", "context", "request", "req", "response", "res",
    "next", "scope", "receive", "send", "argv", "argc", "env", "logger", "log",
    "mock", "stub", "fixture", "helper", "file_obj", "file_object", "file_handle",
    "stream", "reader", "ack", "nack", "reject", "commit", "rollback",
}

_INTERNAL_CONTEXT_HINT_PREFIXES = {
    "self",
    "this",
    "cls",
    "ctx",
    "context",
    "request",
    "req",
    "response",
    "res",
}


def _coerce_input_hints(value: object) -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()
    for item in _coerce_string_list(value):
        text = str(item).strip()
        if not text:
            continue
        if _input_hint_is_internal_context(text):
            continue
        key = _input_hint_dedupe_key(text)
        if key not in seen:
            seen.add(key)
            hints.append(text)
    return hints


def _input_hint_dedupe_key(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"^-+", "", text)
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or str(value or "").strip()


def _merge_ordered_input_hints(*values: object) -> list[str]:
    return _coerce_input_hints([
        item
        for value in values
        for item in _coerce_string_list(value)
    ])


def _black_box_input_hints(entry: dict, hit: FunctionHit) -> list[str]:
    banned = {
        _input_hint_dedupe_key(value)
        for value in [
            hit.function_name,
            entry.get("entry_symbol"),
            *_coerce_string_list(entry.get("chain")),
        ]
        if str(value or "").strip()
    }
    hints: list[str] = []
    for hint in _coerce_input_hints(entry.get("input_hints")):
        normalized = _input_hint_dedupe_key(re.sub(r"\(\s*\)$", "", hint.strip()))
        if normalized in banned:
            continue
        hints.append(hint)
    return hints


def _input_hint_is_internal_context(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    normalized = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    if normalized in _INTERNAL_INPUT_HINTS:
        return True
    prefix_match = re.match(
        r"^([A-Za-z_][A-Za-z0-9_]*)\s*(?:\?\.|\.|->|\[\s*['\"])",
        text,
    )
    if prefix_match and prefix_match.group(1).lower() in _INTERNAL_CONTEXT_HINT_PREFIXES:
        return True
    if normalized.endswith(("_ctx", "_context")):
        return True
    return False


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
            "input_hints": _coerce_input_hints(entry.get("input_hints")),
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
    for key in ("external_trigger", "entry_label", "entry_symbol"):
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
        "job": "job",
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
            if rule == "private_member" and _private_member_looks_like_public_surface(text or "", match):
                continue
            findings.append({
                "rule": rule,
                "text": match.group(0)[:120],
            })
            break
    return findings


def _private_member_looks_like_public_surface(text: str, match: re.Match) -> bool:
    value = match.group(0).strip()
    if "->" in value:
        return False
    window = text[max(0, match.start() - 100): min(len(text), match.end() + 80)].lower()
    public_tokens = (
        "message", "event", "topic", "queue", "channel", "subscription",
        "subscriber", "consumer", "producer", "job", "scheduler", "cron",
        "external", "input", "parameter", "public",
        "消息", "事件", "主题", "队列", "通道", "任务", "调度", "外部", "参数", "输入",
    )
    internal_tokens = ("internal", "private", "内部", "私有")
    return any(token in window for token in public_tokens) and not any(
        token in window for token in internal_tokens
    )


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


def _downgrade_ready_black_box_cases(cases: list[dict], reason: str) -> list[dict]:
    downgraded: list[dict] = []
    for case in cases:
        if not isinstance(case, dict):
            downgraded.append(case)
            continue
        if case.get("case_type") != BLACK_BOX_READY:
            downgraded.append(case)
            continue
        downgraded.append({
            **case,
            "case_type": BLACK_BOX_HYPOTHESIS,
            "downgrade_reason": reason,
        })
    return downgraded


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
    rel = _normalize_coverage_source_path(file_path)
    absolute_source = _resolve_absolute_coverage_source(repo_root, rel, function_name)
    if absolute_source is not None:
        return absolute_source
    rel = rel.replace("\\", "/").lstrip("/")
    rel_variants = _coverage_source_path_variants(rel, function_name)
    for rel_variant in rel_variants:
        for ext in _SOURCE_EXTENSION_CANDIDATES:
            candidate = repo_root / (rel_variant + ext)
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
        for rel_variant in rel_variants:
            hinted_parent = repo_root / rel_variant
            if hinted_parent.is_file():
                hinted_parent = hinted_parent.parent
            if hinted_parent.is_dir() and _is_within(repo_root, hinted_parent):
                found = _find_source_file_defining_function(repo_root, hinted_parent, function_name)
                if found is not None:
                    return found
    for rel_variant in rel_variants:
        suffix_match = _resolve_source_file_by_suffix(repo_root, rel_variant)
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


def _coverage_source_path_variants(rel: str, function_name: str | None = None) -> list[str]:
    normalized = str(rel or "").replace("\\", "/").strip("/")
    if not normalized:
        return []
    variants: list[str] = []

    def add(value: str) -> None:
        value = str(value or "").replace("\\", "/").strip("/")
        if value and value not in variants:
            variants.append(value)

    add(normalized)
    base, symbol = _split_trailing_coverage_symbol(normalized)
    if base != normalized:
        add(base)
    for dotted in _dotted_module_path_variants(base, function_name, symbol):
        add(dotted)
    return variants


def _split_trailing_coverage_symbol(value: str) -> tuple[str, str]:
    match = re.match(
        r"^(?P<base>(?:[A-Za-z]:/)?[^:]+):(?P<symbol>[^/\\:]+)$",
        str(value or ""),
    )
    if not match:
        return value, ""
    symbol = (match.group("symbol") or "").strip()
    if not symbol or re.fullmatch(r"\d+(?::\d+)?(?:-\d+)?", symbol):
        return value, ""
    return match.group("base"), symbol


def _dotted_module_path_variants(
    value: str,
    function_name: str | None,
    symbol: str = "",
) -> list[str]:
    normalized = str(value or "").replace("\\", "/").strip("/")
    if not normalized or "." not in normalized:
        return []
    if Path(normalized).suffix.lower() in _SOURCE_FILE_EXTS:
        return []
    converted = "/".join(part.replace(".", "/") for part in normalized.split("/"))
    variants = [converted] if converted != normalized else []
    symbol_names = _coverage_symbol_simple_names(function_name, symbol)
    parts = [part for part in converted.split("/") if part]
    if len(parts) > 1 and parts[-1].lower() in symbol_names:
        parent = "/".join(parts[:-1])
        if parent and parent not in variants:
            variants.append(parent)
    return variants


def _coverage_symbol_simple_names(*values: str | None) -> set[str]:
    names: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        text = re.sub(r"\(.*\)$", "", text).strip()
        for separator in ("::", ".", "#"):
            if separator in text:
                text = text.rsplit(separator, 1)[-1]
        text = text.strip()
        if text:
            names.add(text.lower())
    return names


def _normalize_coverage_source_path(file_path: str) -> str:
    value = str(file_path or "").strip().strip('"').strip("'")
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme.lower() == "file":
        if parsed.netloc:
            value = f"//{parsed.netloc}{parsed.path}"
        else:
            value = parsed.path
    elif parsed.scheme.lower() in {"http", "https"}:
        value = _normalize_remote_code_url_path(parsed.path)
    else:
        value = value.split("#", 1)[0].split("?", 1)[0]
    value = unquote(value).replace("\\", "/")
    if re.match(r"^/[A-Za-z]:/", value):
        value = value[1:]
    value = re.sub(r":\d+:\d+$", "", value)
    value = re.sub(r":\d+(?:-\d+)?$", "", value)
    value = _strip_coverage_symbol_suffix(value)
    return value


def _strip_coverage_symbol_suffix(value: str) -> str:
    """Strip ``path.ext:symbol`` while preserving drives and line suffixes."""
    match = re.match(
        rf"^(?P<path>.+\.(?:{_SOURCE_PATH_EXTENSION_PATTERN})):(?P<symbol>[^/\\]+)$",
        value,
        flags=re.IGNORECASE,
    )
    if not match:
        return value
    symbol = (match.group("symbol") or "").strip()
    if not symbol or re.fullmatch(r"\d+(?::\d+)?(?:-\d+)?", symbol):
        return value
    if not re.search(r"[^\W\d_]", symbol, flags=re.UNICODE):
        return value
    return match.group("path")


def _normalize_remote_code_url_path(path: str) -> str:
    value = unquote(path or "").replace("\\", "/").strip("/")
    if not value:
        return ""
    parts = [part for part in value.split("/") if part]
    markers = {"blob", "raw", "src"}
    for index, part in enumerate(parts):
        if part == "-":
            continue
        if part not in markers:
            continue
        next_index = index + 1
        if next_index < len(parts) and parts[next_index] == "-":
            next_index += 1
        # Drop the branch/ref segment after blob/raw/src. This covers common
        # GitHub, GitLab, Gitea, and Bitbucket source links.
        file_start = next_index + 1
        if file_start < len(parts):
            return "/".join(parts[file_start:])
    return value


def _resolve_absolute_coverage_source(
    repo_root: Path,
    normalized_path: str,
    function_name: str | None,
) -> Path | None:
    if not normalized_path:
        return None
    if not (Path(normalized_path).is_absolute() or re.match(r"^[A-Za-z]:/", normalized_path)):
        return None
    for ext in _SOURCE_EXTENSION_CANDIDATES:
        candidate = Path(f"{normalized_path}{ext}")
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
    names = _function_name_candidates(function_name)
    try:
        lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    return any(
        _definition_line_matches_any_name(lines, idx, names)
        for idx, _line in enumerate(lines)
    )


def _definition_line_matches_any_name(
    lines: list[str],
    idx: int,
    names: list[str],
) -> bool:
    defined = _match_def_name(lines[idx]) or _match_multiline_def_name(lines, idx)
    return bool(defined and defined in names)


def _function_name_candidates(function_name: str | None) -> list[str]:
    value = str(function_name or "").strip()
    if not value:
        return []
    candidates: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        candidate = str(candidate or "").strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    add(value)
    no_args = re.sub(r"\([^()]*\)\s*$", "", value).strip()
    add(no_args)
    normalized = no_args.replace("->", ".").replace("::", ".").replace("#", ".")
    normalized = normalized.replace("/", ".").replace("\\", ".")
    for match in reversed(re.findall(r"[A-Za-z_]\w*", normalized)):
        if match not in _NON_FUNCTION_NAMES:
            add(match)
            break
    return candidates


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
    names = _function_name_candidates(function_name)
    if not names:
        return None
    name_re = re.compile(
        r"\b(?:"
        + "|".join(re.escape(name) for name in names)
        + r")\s*\("
    )
    for idx, line in enumerate(lines):
        if name_re.search(line):
            return idx + 1
    return None


def _find_strict_definition_line(lines: list[str], function_name: str) -> int | None:
    match = _find_strict_definition_match(lines, function_name)
    return match[0] if match else None


def _find_strict_definition_match(
    lines: list[str],
    function_name: str,
) -> tuple[int, str] | None:
    names = _function_name_candidates(function_name)
    if not names:
        return None
    for idx, line in enumerate(lines):
        for name in names:
            if _line_matches_signature_name(lines, idx, name):
                return idx + 1, name
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
    postfix_match = re.search(
        r"\b(?P<keyword>unless|if)\b\s+(?P<condition>[^;{}]+)$",
        clean,
        re.IGNORECASE,
    )
    if postfix_match and not clean.lower().startswith(postfix_match.group("keyword").lower()):
        keyword = postfix_match.group("keyword")
        condition = _trim_bare_branch_condition(postfix_match.group("condition"))
        if condition:
            return f"{keyword} ({condition})"
    for keyword in ("if", "else if", "elif", "switch", "while", "for", "catch",
                    "except", "when", "guard", "unless"):
        match = re.search(rf"\b{keyword}\b\s*\(([^)]*)\)", clean, re.IGNORECASE)
        if match:
            return f"{keyword} ({match.group(1).strip()})"
    bare_match = re.search(
        r"\b(?P<keyword>if|unless|else\s+if|elif|while|for|catch|except|when|guard)\b\s+"
        r"(?P<condition>.+)",
        clean,
        re.IGNORECASE,
    )
    if bare_match:
        keyword = bare_match.group("keyword")
        condition = _trim_bare_branch_condition(bare_match.group("condition"))
        if condition:
            return f"{keyword} ({condition})"
    case_match = re.search(r"\b(case\s+[^:]+:|default\s*:)", clean, re.IGNORECASE)
    if case_match:
        return case_match.group(1).strip()
    goto_match = re.search(r"\b(return\s+-[A-Za-z0-9_]+|goto\s+\w+)", clean, re.IGNORECASE)
    if goto_match:
        return goto_match.group(1).strip()
    return clean[:160]


def _trim_bare_branch_condition(condition: str) -> str:
    value = str(condition or "").strip()
    value = re.split(r"\belse\b", value, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    value = re.split(r"[:{]", value, maxsplit=1)[0].strip()
    return value[:160]


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
    return (
        _definition_name_for_file(lines, idx, Path(abs_file).suffix.lower()) == function_name
    )


def _definition_name_for_file(lines: list[str], idx: int, suffix: str) -> str | None:
    if idx < 0 or idx >= len(lines):
        return None
    line = lines[idx]
    stripped = line.strip()
    if suffix == ".py" and not (
        re.match(r"^(?:async\s+)?def\s+", stripped)
        or re.search(r"=\s*lambda\b", stripped)
    ):
        return None
    if suffix == ".rb" and not stripped.startswith("def "):
        return None
    if suffix in {".js", ".jsx", ".ts", ".tsx"} and not (
        re.search(r"\bfunction\b", stripped)
        or "=>" in stripped
        or re.match(r"^(?:export\s+)?(?:async\s+)?function\s+", stripped)
    ):
        return None
    if suffix == ".java":
        java_name = _java_method_definition_name(line)
        if java_name:
            return java_name
    name = _match_def_name(line) or _match_multiline_def_name(lines, idx)
    if (
        name
        and suffix in {".py", ".js", ".jsx", ".ts", ".tsx", ".rb"}
        and re.match(rf"^\s*{re.escape(name)}\s*\(", line)
    ):
        return None
    return name


def _java_method_definition_name(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith(_EXPRESSION_CALL_PREFIXES):
        return None
    if re.match(r"^(?:if|for|while|switch|catch|return|throw|new)\b", stripped):
        return None
    match = re.match(
        r"^(?:@\w+(?:\([^)]*\))?\s*)*"
        r"(?:(?:public|private|protected|static|final|synchronized|native|abstract|default)\s+)*"
        r"(?:<[^>]+>\s*)?"
        r"[\w.$<>,?\[\]\s]+\s+"
        r"(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\)"
        r"(?:\s+throws\s+[\w.$,\s]+)?\s*(?:\{|$)",
        stripped,
    )
    if not match:
        return None
    name = match.group("name")
    return None if name in _NON_FUNCTION_NAMES else name


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
    suffix = Path(abs_file).suffix.lower()

    enclosing: str | None = None
    for idx in range(upper, -1, -1):
        name = _definition_name_for_file(lines, idx, suffix)
        if name and _definition_encloses_line(lines, idx, upper):
            enclosing = name
            break

    guard: dict | None = None
    low = max(0, line_number - 8)
    for idx in range(upper, low - 1, -1):
        text = lines[idx]
        if (
            _definition_name_for_file(lines, idx, suffix) is not None
            and _definition_encloses_line(lines, idx, upper)
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
            if kind == "message" and not _has_explicit_message_entry_surface(
                file_path,
                line_text,
            ):
                continue
            return kind
    return None


def _has_explicit_message_entry_surface(file_path: str, line_text: str) -> bool:
    """Avoid treating internal helpers named *event/message* as public entries."""
    text = " ".join([str(file_path or ""), str(line_text or "")]).lower()
    if re.search(
        r"(?:\.\s*(?:subscribe|on|once|listen|add(?:event)?listener|consumer)\s*\()"
        r"|\b(?:subscribe|subscriber|topic|queue|message[_ -]?bus|event[_ -]?bus|"
        r"listener|consumer|kafka|rabbit|sqs|pubsub|webhook)\b",
        text,
        re.IGNORECASE,
    ):
        return True
    parts = {
        part
        for part in re.split(r"[/\\._\-\s]+", str(file_path or "").lower())
        if part
    }
    return bool(parts & {"consumer", "consumers", "subscriber", "subscribers", "listener", "listeners"})


def _entry_symbol_for_site(
    entry_kind: str,
    enclosing_fn: str | None,
    traced_symbol: str,
    line_text: str,
) -> str:
    if (
        entry_kind == "route"
        and traced_symbol
        and _PUBLIC_CALLBACK_START_RE.search(line_text or "")
        and re.search(rf"\b{re.escape(traced_symbol)}\b", line_text or "")
    ):
        return traced_symbol
    return enclosing_fn or traced_symbol


def _entry_metadata_for_site(abs_file: str, line_number: int, enclosing_fn: str | None) -> dict:
    metadata: dict = {}
    if not enclosing_fn:
        return metadata
    rpc_method = _spdk_rpc_method_for_handler(abs_file, enclosing_fn)
    if rpc_method:
        metadata["entry_label"] = f"JSON-RPC {rpc_method}"
    hints = _request_field_hints(abs_file, line_number, enclosing_fn)
    for hint in _specific_signature_input_hints(
        _handler_signature_input_hints(abs_file, enclosing_fn),
        hints,
    ):
        if hint not in hints:
            hints.append(hint)
    if _is_cli_entry_symbol(enclosing_fn):
        hints = _merge_ordered_strings(hints, _cli_option_input_hints(abs_file, enclosing_fn))
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
    if not entry_symbol:
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
    for hint in _specific_signature_input_hints(
        _handler_signature_input_hints(str(symbol_file), entry_symbol),
        hints,
    ):
        if hint not in hints:
            hints.append(hint)
    if hints:
        metadata["input_hints"] = _merge_ordered_input_hints(
            metadata.get("input_hints"),
            hints,
        )
    return metadata


def _specific_signature_input_hints(
    signature_hints: list[str],
    source_hints: list[str],
) -> list[str]:
    if not source_hints:
        return signature_hints
    generic_payload_names = {
        "event", "evt", "message", "msg", "payload", "record",
        "request", "req", "response", "res", "reply", "next", "h",
    }
    return [
        hint for hint in signature_hints
        if str(hint or "").strip().lower() not in generic_payload_names
    ]


def _anonymous_entry_metadata_for_site(
    repo_root: Path,
    abs_file: str,
    line_number: int,
) -> dict:
    try:
        path = Path(abs_file)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    call_idx = line_number - 1
    if call_idx < 0 or call_idx >= len(lines):
        return {}
    start_idx = _route_call_context_start_index(lines, call_idx)
    if start_idx is None:
        return {}

    end_idx = _call_expression_window_end(lines, start_idx, call_idx)
    window = lines[start_idx:end_idx]
    window_text = " ".join(line.strip() for line in window)
    hints = _request_field_hints_from_text(window_text)
    hints = _merge_ordered_strings(hints, _route_template_input_hints([window_text]))
    evidence = f"{_relative_path(repo_root, path)}:{start_idx + 1} {lines[start_idx].strip()}"
    metadata: dict = {
        "_anonymous_entry_evidence": evidence,
    }
    if hints:
        metadata["input_hints"] = hints
    return metadata


def _route_call_context_for_site_file(abs_file: str, line_number: int) -> str | None:
    try:
        lines = Path(abs_file).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    call_idx = line_number - 1
    if call_idx < 0 or call_idx >= len(lines):
        return None
    start_idx = _route_call_context_start_index(lines, call_idx)
    if start_idx is None:
        return None
    end_idx = _call_expression_window_end(lines, start_idx, call_idx)
    return " ".join(line.strip() for line in lines[start_idx:end_idx] if line.strip())


def _route_call_context_start_index(lines: list[str], call_idx: int) -> int | None:
    for idx in range(call_idx, max(-1, call_idx - 16), -1):
        if _PUBLIC_CALLBACK_START_RE.search(lines[idx]) or _ROUTE_DSL_START_RE.search(lines[idx]):
            chain_idx = _route_chain_start_index(lines, idx)
            return chain_idx if chain_idx is not None else idx
    return None


def _route_chain_start_index(lines: list[str], method_idx: int) -> int | None:
    """Find ``router.route('/x')`` preceding a chained ``.post(handler)`` line."""
    if method_idx <= 0 or method_idx >= len(lines):
        return None
    if not re.match(
        r"^\s*\.\s*(?:get|post|put|patch|delete|head|options|any|websocket)\s*\(",
        lines[method_idx] or "",
        re.IGNORECASE,
    ):
        return None
    for idx in range(method_idx - 1, max(-1, method_idx - 8), -1):
        text = (lines[idx] or "").strip()
        if not text:
            continue
        if re.search(r"\.\s*route\s*\(\s*['\"]", text, re.IGNORECASE):
            return idx
        if text.endswith((";", "{", "}")):
            break
    return None


def _call_expression_window_end(lines: list[str], start_idx: int, call_idx: int) -> int:
    balance = 0
    saw_open = False
    upper = min(len(lines), start_idx + 40)
    for idx in range(start_idx, upper):
        text = lines[idx]
        balance += text.count("(") - text.count(")")
        if "(" in text:
            saw_open = True
        if idx >= call_idx and saw_open and balance <= 0:
            return idx + 1
    return min(len(lines), max(call_idx + 1, start_idx + 1))


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
    hints = _request_field_hints_from_text(statement_text)
    if Path(abs_file).suffix.lower() == ".go":
        hints = _merge_ordered_input_hints(
            hints,
            _go_bind_input_hints(lines, start, end),
        )
    elif Path(abs_file).suffix.lower() == ".py":
        hints = _merge_ordered_input_hints(
            hints,
            _python_serializer_input_hints(lines, start, end),
        )
    elif Path(abs_file).suffix.lower() in {
        ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts",
    }:
        hints = _merge_ordered_input_hints(
            hints,
            _javascript_schema_input_hints(lines, start, end),
        )
    elif Path(abs_file).suffix.lower() in {".kt", ".kts"}:
        hints = _merge_ordered_input_hints(
            hints,
            _kotlin_receive_input_hints(lines, start, end),
        )
    return hints


def _request_field_hints_from_text(statement_text: str) -> list[str]:
    positioned_fields: list[tuple[int, str]] = []
    for match in _REQ_FIELD_RE.finditer(statement_text):
        positioned_fields.append((match.start(), match.group(1)))
    for pattern in _REQUEST_FIELD_RES:
        for match in pattern.finditer(statement_text):
            field = _request_field_from_match(match)
            if _is_message_envelope_field(match, field):
                continue
            positioned_fields.append((match.start(), field))
    for match in _RAILS_STRONG_PARAM_REQUIRE_RE.finditer(statement_text):
        positioned_fields.append((match.start(), match.group(1)))
    for match in _RAILS_STRONG_PARAM_PERMIT_RE.finditer(statement_text):
        for field_match in _RUBY_SYMBOL_ARG_RE.finditer(match.group("fields")):
            positioned_fields.append((match.start() + field_match.start(), field_match.group(1)))
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


def _request_field_from_match(match: re.Match[str]) -> str:
    groups = match.groupdict()
    field = groups.get("field")
    if field is not None:
        return field
    return match.group(1)


def _is_message_envelope_field(match: re.Match[str], field: str) -> bool:
    container = str(match.groupdict().get("container") or "").lower()
    return (
        container in {"message", "msg"}
        and str(field or "").strip().lower() in _MESSAGE_ENVELOPE_FIELD_NAMES
    )


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
        if line_def == enclosing_fn and _definition_encloses_line(lines, pos, call_idx):
            fn_start = pos
            break
    if fn_start is None:
        return fallback
    fn_end = len(lines)
    for pos in range(fn_start + 1, len(lines)):
        if _is_sibling_definition_boundary(lines, fn_start, pos):
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
    if not enclosing_fn or Path(abs_file).suffix.lower() not in {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".cs", ".rb",
        ".kt", ".kts",
    }:
        return []
    try:
        lines = Path(abs_file).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for idx, line in enumerate(lines):
        if not _line_matches_signature_name(lines, idx, enclosing_fn):
            continue
        signature = _collect_signature_text(lines, idx)
        model_fields_by_type = _source_model_fields_by_class(
            lines, Path(abs_file).suffix.lower()
        )
        return _signature_input_params(signature, model_fields_by_type=model_fields_by_type)
    return []


def _collect_signature_text(lines: list[str], start_idx: int) -> str:
    if start_idx < 0 or start_idx >= len(lines):
        return ""
    parts: list[str] = []
    depth = 0
    started = False
    for pos in range(start_idx, min(len(lines), start_idx + 12)):
        text = lines[pos].strip()
        if not text:
            continue
        parts.append(text)
        for char in text:
            if char == "(":
                depth += 1
                started = True
            elif char == ")" and depth > 0:
                depth -= 1
        if started and depth == 0:
            break
        if "{" in text and started:
            break
    return " ".join(parts)


def _line_matches_signature_name(lines: list[str], idx: int, name: str) -> bool:
    line = lines[idx] if 0 <= idx < len(lines) else ""
    if _match_def_name(line) == name or _match_multiline_def_name(lines, idx) == name:
        return True
    stripped = line.strip()
    if stripped.startswith(_EXPRESSION_CALL_PREFIXES):
        return False
    match = re.search(rf"\b{re.escape(name)}\s*\(", line)
    if not match:
        return False
    prefix = stripped[: stripped.find(name)].strip()
    if not prefix or prefix.endswith((".", "->")):
        return False
    if "=" in prefix and not stripped.startswith(("def ", "async def ")):
        return False
    return True


def _signature_input_params(
    signature: str,
    *,
    model_fields_by_type: dict[str, list[str]] | None = None,
) -> list[str]:
    params = _signature_param_section(signature or "")
    if params is None:
        return []
    framework_params = {
        "self", "cls", "request", "req", "response", "res", "next",
        "reply", "h", "context", "ctx", "scope", "receive", "send", "argv", "argc",
        "call", "httpcontext", "applicationcall", "routingcontext",
        "cancellationtoken", "modelstate",
    }
    hints: list[str] = []
    seen: set[str] = set()

    def add_hint(value: str) -> None:
        if value and value not in seen:
            seen.add(value)
            hints.append(value)

    for raw_param in _split_signature_params(params):
        external_param = _signature_external_param_name(raw_param)
        param = _signature_param_name(raw_param)
        if not param:
            continue
        if external_param:
            add_hint(external_param)
            continue
        type_hint = _signature_param_type_hint(raw_param, param)
        model_fields = (model_fields_by_type or {}).get(type_hint or "")
        if model_fields:
            for field in model_fields:
                add_hint(field)
            continue
        if param.lower() in framework_params:
            param = _signature_external_type_hint(raw_param, param, framework_params)
            if not param:
                continue
            model_fields = (model_fields_by_type or {}).get(param)
            if model_fields:
                for field in model_fields:
                    add_hint(field)
                continue
        add_hint(param)
    return hints


def _signature_param_section(signature: str) -> str | None:
    start = signature.find("(")
    if start < 0:
        return None
    depth = 0
    for index in range(start, len(signature)):
        char = signature[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return signature[start + 1:index]
    return None


def _split_signature_params(params: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    for char in params:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}" and depth > 0:
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if current:
        parts.append("".join(current).strip())
    return parts


def _signature_param_type_hint(raw_param: str, param_name: str) -> str | None:
    declaration = _strip_parameter_decorators(str(raw_param or "").split("=", 1)[0].strip())
    skip = {
        "annotated", "optional", "union", "list", "dict", "tuple", "set",
        "sequence", "mapping", "body", "query", "path", "header", "cookie",
        "str", "int", "float", "bool", "bytes", "none", "any",
        "final", "readonly", "public", "private", "protected", "static",
        "requestbody", "frombody", "fromroute", "fromquery", "requestparam",
        "pathvariable", "valid", "validated", "notnull", "nullable",
        "string", "integer", "long", "double", "decimal", "boolean",
        "responseentity", "iactionresult", "applicationcall", "routingcontext",
    }
    if ":" in declaration:
        annotation = declaration.split(":", 1)[1]
        for identifier in re.findall(r"[A-Za-z_][\w]*", annotation):
            normalized = identifier.lower()
            if normalized in skip or identifier == param_name:
                continue
            return identifier
        return None
    annotations = {
        match.group(1).lower()
        for match in re.finditer(r"@([A-Za-z_][\w]*)", declaration)
    }
    identifiers = re.findall(r"[A-Za-z_][\w]*", declaration)
    for identifier in identifiers:
        normalized = identifier.lower()
        if normalized in skip or normalized in annotations or normalized == param_name.lower():
            continue
        return identifier
    return None


def _strip_parameter_decorators(declaration: str) -> str:
    text = declaration
    pattern = re.compile(r"^\s*@[A-Za-z_][\w.]*(?:\([^()]*\))?\s*")
    while True:
        stripped = pattern.sub("", text, count=1).strip()
        if stripped == text:
            return stripped
        text = stripped


def _signature_external_param_name(raw_param: str) -> str | None:
    text = str(raw_param or "")
    if not text:
        return None
    fastapi_match = re.search(
        r"\b(?:Path|Query|Body|Header|Cookie|Form)\s*\([^)]*"
        r"\balias\s*=\s*(['\"])(?P<name>[A-Za-z_][\w.-]*)\1",
        text,
    )
    if fastapi_match:
        return fastapi_match.group("name")
    annotation_match = re.search(
        r"[@\[]\s*(?:RequestParam|PathVariable|RequestHeader|CookieValue|"
        r"FromQuery|FromRoute|FromHeader|FromForm|Param|Query|Header|Cookie)"
        r"(?:Attribute)?\s*(?:\((?P<body>[^)]*)\))?",
        text,
    )
    if not annotation_match:
        return None
    body = annotation_match.group("body") or ""
    for pattern in (
        r"\b(?:name|value)\s*=\s*(['\"])(?P<name>[A-Za-z_][\w.-]*)\1",
        r"^\s*(['\"])(?P<name>[A-Za-z_][\w.-]*)\1",
    ):
        match = re.search(pattern, body, re.IGNORECASE)
        if match:
            return match.group("name")
    return None


def _source_model_fields_by_class(lines: list[str], suffix: str) -> dict[str, list[str]]:
    if suffix == ".py":
        return _python_model_fields_by_class(lines)
    if suffix == ".java":
        return _java_model_fields_by_class(lines)
    if suffix == ".cs":
        return _csharp_model_fields_by_class(lines)
    if suffix == ".go":
        return _go_model_fields_by_struct(lines)
    if suffix in {".ts", ".tsx", ".mts", ".cts"}:
        return _typescript_model_fields_by_class(lines)
    if suffix in {".kt", ".kts"}:
        return _kotlin_model_fields_by_class(lines)
    return {}


def _python_model_fields_by_class(lines: list[str]) -> dict[str, list[str]]:
    fields_by_class: dict[str, list[str]] = {}
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        match = re.match(
            r"^(?P<indent>\s*)class\s+(?P<name>[A-Za-z_]\w*)"
            r"\s*(?:\((?P<bases>[^)]*)\))?\s*:",
            line,
        )
        if not match:
            idx += 1
            continue
        class_name = match.group("name")
        bases = match.group("bases") or ""
        decorators = _decorator_lines_before_definition(lines, idx + 1)
        decorator_text = "\n".join(text for _line_no, text in decorators).lower()
        if (
            "basemodel" not in bases.lower()
            and "serializer" not in bases.lower()
            and "dataclass" not in decorator_text
        ):
            idx += 1
            continue
        class_indent = len(match.group("indent"))
        fields: list[str] = []
        seen: set[str] = set()
        pos = idx + 1
        while pos < len(lines):
            child = lines[pos]
            if child.strip() and len(child) - len(child.lstrip()) <= class_indent:
                break
            field_match = re.match(
                r"^\s+(?P<field>[A-Za-z_]\w*)\s*:\s*(?P<annotation>[^#=]+)",
                child,
            )
            if field_match:
                field = field_match.group("field")
                annotation = field_match.group("annotation").strip()
                external_field = _python_model_field_external_name(child, field)
                if (
                    not field.startswith("_")
                    and external_field not in seen
                    and not annotation.startswith(("ClassVar", "typing.ClassVar"))
                ):
                    seen.add(external_field)
                    fields.append(external_field)
                    if len(fields) >= 12:
                        break
            else:
                serializer_field = re.match(
                    r"^\s+(?P<field>[A-Za-z_]\w*)\s*=\s*"
                    r"serializers\.[A-Za-z_]\w*Field\s*\(",
                    child,
                )
                if serializer_field:
                    field = serializer_field.group("field")
                    if not field.startswith("_") and field not in seen:
                        seen.add(field)
                        fields.append(field)
                        if len(fields) >= 12:
                            break
            pos += 1
        if fields:
            fields_by_class[class_name] = fields
        idx = max(pos, idx + 1)
    return fields_by_class


def _python_model_field_external_name(line: str, field: str) -> str:
    match = re.search(
        r"\b(?:Field|pydantic\.Field)\s*\([^)]*"
        r"\balias\s*=\s*(['\"])(?P<alias>[A-Za-z_][\w.-]*)\1",
        line or "",
    )
    return match.group("alias") if match else field


def _java_model_fields_by_class(lines: list[str]) -> dict[str, list[str]]:
    fields_by_class: dict[str, list[str]] = {}
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        match = re.match(
            r"^\s*(?:(?:public|private|protected|static|final|abstract)\s+)*"
            r"(?P<kind>class|record)\s+(?P<name>[A-Za-z_]\w*)"
            r"(?:\s*\((?P<params>[^)]*)\))?",
            line,
        )
        if not match:
            idx += 1
            continue
        class_name = match.group("name")
        fields: list[str] = []
        seen: set[str] = set()

        if match.group("kind") == "record":
            for raw_param in _split_signature_params(match.group("params") or ""):
                field = _java_field_name_from_declaration(raw_param)
                if field and field not in seen:
                    seen.add(field)
                    fields.append(field)

        brace_depth = line.count("{") - line.count("}")
        pos = idx + 1
        while pos < len(lines):
            child = lines[pos]
            if brace_depth <= 0 and child.strip():
                break
            if brace_depth == 1:
                field = _java_field_name_from_declaration(child)
                if field and field not in seen:
                    seen.add(field)
                    fields.append(field)
                    if len(fields) >= 12:
                        break
            brace_depth += child.count("{") - child.count("}")
            pos += 1
        if fields:
            fields_by_class[class_name] = fields
        idx = max(pos, idx + 1)
    return fields_by_class


def _java_field_name_from_declaration(raw_line: str) -> str | None:
    line = str(raw_line or "").strip()
    if not line or line.startswith(("//", "*", "@")):
        return None
    if "(" in line and not line.startswith("record "):
        return None
    line = line.split("//", 1)[0].strip().rstrip(",;")
    if "=" in line:
        line = line.split("=", 1)[0].strip()
    tokens = re.findall(r"[A-Za-z_]\w*", line)
    skip = {
        "public", "private", "protected", "static", "final", "transient",
        "volatile", "class", "record", "extends", "implements", "new",
    }
    filtered = [token for token in tokens if token.lower() not in skip]
    if len(filtered) < 2:
        return None
    field = filtered[-1]
    if field and not field[0].isupper():
        return field
    return None


def _go_bind_input_hints(lines: list[str], start: int, end: int) -> list[str]:
    window = "\n".join(lines[start:end])
    if not re.search(r"\b(?:ShouldBindJSON|BindJSON|ShouldBind|Bind)\s*\(", window):
        return []
    fields_by_struct = _go_model_fields_by_struct(lines)
    if not fields_by_struct:
        return []
    type_by_var: dict[str, str] = {}
    for match in re.finditer(r"\bvar\s+([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\b", window):
        type_by_var[match.group(1)] = match.group(2)
    for match in re.finditer(
        r"\b([A-Za-z_]\w*)\s*:=\s*(?:&\s*)?(?:new\s*\(\s*)?"
        r"([A-Za-z_]\w*)\s*(?:\{\s*\}|\))",
        window,
    ):
        type_by_var[match.group(1)] = match.group(2)

    hints: list[str] = []
    seen: set[str] = set()

    def add_fields(type_name: str | None) -> None:
        if not type_name:
            return
        for field in fields_by_struct.get(type_name, []):
            if field not in seen:
                seen.add(field)
                hints.append(field)

    for match in re.finditer(
        r"\b(?:ShouldBindJSON|BindJSON|ShouldBind|Bind)"
        r"\s*\(\s*&?\s*([A-Za-z_]\w*)\s*\)",
        window,
    ):
        add_fields(type_by_var.get(match.group(1)))
    for match in re.finditer(
        r"\b(?:ShouldBindJSON|BindJSON|ShouldBind|Bind)\s*\(\s*&\s*([A-Za-z_]\w*)\s*\{",
        window,
    ):
        add_fields(match.group(1))
    return hints[:12]


def _python_serializer_input_hints(lines: list[str], start: int, end: int) -> list[str]:
    window = "\n".join(lines[start:end])
    if "request.data" not in window and ".data" not in window:
        return []
    fields_by_class = _python_model_fields_by_class(lines)
    if not fields_by_class:
        return []
    hints: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(
        r"\b([A-Za-z_]\w*Serializer)\s*\([^)]*\bdata\s*=\s*request\.data\b",
        window,
    ):
        for field in fields_by_class.get(match.group(1), []):
            if field not in seen:
                seen.add(field)
                hints.append(field)
                if len(hints) >= 12:
                    return hints
    return hints


def _kotlin_receive_input_hints(lines: list[str], start: int, end: int) -> list[str]:
    window = "\n".join(lines[start:end])
    if "receive<" not in window:
        return []
    fields_by_class = _kotlin_model_fields_by_class(lines)
    if not fields_by_class:
        return []
    hints: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"\breceive\s*<\s*([A-Za-z_]\w*)\s*>\s*\(", window):
        for field in fields_by_class.get(match.group(1), []):
            if field not in seen:
                seen.add(field)
                hints.append(field)
                if len(hints) >= 12:
                    return hints
    return hints


def _javascript_schema_input_hints(lines: list[str], start: int, end: int) -> list[str]:
    window = "\n".join(lines[start:end])
    if not re.search(
        r"\b(?:parse|safeParse|validate|validateSync)\s*\(\s*(?:request|req)\.body\b",
        window,
    ):
        return []
    schema_fields = _javascript_object_schema_fields(lines)
    if not schema_fields:
        return []
    hints: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(
        r"\b([A-Za-z_]\w*)\s*\.\s*(?:parse|safeParse|validate|validateSync)"
        r"\s*\(\s*(?:request|req)\.body\b",
        window,
    ):
        for field in schema_fields.get(match.group(1), []):
            if field not in seen:
                seen.add(field)
                hints.append(field)
                if len(hints) >= 12:
                    return hints
    return hints


def _javascript_object_schema_fields(lines: list[str]) -> dict[str, list[str]]:
    text = "\n".join(lines)
    fields_by_schema: dict[str, list[str]] = {}
    schema_re = re.compile(
        r"\b(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*"
        r"(?:z|Joi|joi|yup)\.object\s*\(\s*\{",
        re.MULTILINE,
    )
    for match in schema_re.finditer(text):
        body_start = match.end() - 1
        body_end = _balanced_block_end(text, body_start, "{", "}")
        if body_end is None:
            continue
        fields = _javascript_schema_fields_from_object_body(text[body_start + 1:body_end])
        if fields:
            fields_by_schema[match.group(1)] = fields
    return fields_by_schema


def _balanced_block_end(
    text: str,
    start: int,
    open_char: str,
    close_char: str,
) -> int | None:
    depth = 0
    quote: str | None = None
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
            continue
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return index
    return None


def _javascript_schema_fields_from_object_body(body: str) -> list[str]:
    fields: list[str] = []
    seen: set[str] = set()
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "/*", "*")):
            continue
        match = re.match(r"^['\"]?([A-Za-z_][\w-]*)['\"]?\s*:", stripped)
        if not match:
            continue
        field = match.group(1)
        if field not in seen:
            seen.add(field)
            fields.append(field)
            if len(fields) >= 12:
                break
    return fields


def _go_model_fields_by_struct(lines: list[str]) -> dict[str, list[str]]:
    fields_by_struct: dict[str, list[str]] = {}
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        match = re.match(r"^\s*type\s+([A-Za-z_]\w*)\s+struct\s*\{", line)
        if not match:
            idx += 1
            continue
        struct_name = match.group(1)
        fields: list[str] = []
        seen: set[str] = set()
        pos = idx + 1
        while pos < len(lines):
            child = lines[pos].strip()
            if child.startswith("}"):
                break
            field = _go_json_field_name_from_struct_line(child)
            if field and field not in seen:
                seen.add(field)
                fields.append(field)
                if len(fields) >= 12:
                    break
            pos += 1
        if fields:
            fields_by_struct[struct_name] = fields
        idx = max(pos + 1, idx + 1)
    return fields_by_struct


def _go_json_field_name_from_struct_line(line: str) -> str | None:
    if not line or line.startswith(("//", "/*", "*")):
        return None
    tag_match = re.search(r"`[^`]*\bjson:\"([^\",]+)", line)
    if tag_match:
        tag = tag_match.group(1).strip()
        if tag and tag != "-":
            return tag
    tokens = re.findall(r"[A-Za-z_]\w*", line.split("`", 1)[0])
    if len(tokens) < 2:
        return None
    field = tokens[0]
    if not field or not field[0].isupper():
        return None
    return field[:1].lower() + field[1:]


def _typescript_model_fields_by_class(lines: list[str]) -> dict[str, list[str]]:
    fields_by_type: dict[str, list[str]] = {}
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        match = re.match(
            r"^\s*(?:export\s+)?(?P<kind>interface|class)\s+(?P<name>[A-Za-z_]\w*)\b",
            line,
        )
        type_match = re.match(
            r"^\s*(?:export\s+)?type\s+(?P<name>[A-Za-z_]\w*)\s*=\s*\{",
            line,
        )
        if not match and not type_match:
            idx += 1
            continue
        type_name = (match or type_match).group("name")
        fields: list[str] = []
        seen: set[str] = set()
        brace_depth = line.count("{") - line.count("}")
        pos = idx + 1
        while pos < len(lines):
            child = lines[pos]
            if brace_depth <= 0 and child.strip():
                break
            if brace_depth == 1:
                field = _typescript_field_name_from_declaration(child)
                if field and field not in seen:
                    seen.add(field)
                    fields.append(field)
                    if len(fields) >= 12:
                        break
            brace_depth += child.count("{") - child.count("}")
            pos += 1
        if fields:
            fields_by_type[type_name] = fields
        idx = max(pos, idx + 1)
    return fields_by_type


def _kotlin_model_fields_by_class(lines: list[str]) -> dict[str, list[str]]:
    text = "\n".join(lines)
    fields_by_type: dict[str, list[str]] = {}
    for match in re.finditer(r"\bdata\s+class\s+([A-Za-z_]\w*)\s*\(", text):
        class_name = match.group(1)
        params_start = match.end() - 1
        params_end = _balanced_block_end(text, params_start, "(", ")")
        if params_end is None:
            continue
        fields: list[str] = []
        seen: set[str] = set()
        for raw_param in _split_signature_params(text[params_start + 1:params_end]):
            field = _kotlin_constructor_field_name(raw_param)
            if field and field not in seen:
                seen.add(field)
                fields.append(field)
                if len(fields) >= 12:
                    break
        if fields:
            fields_by_type[class_name] = fields
    return fields_by_type


def _kotlin_constructor_field_name(raw_param: str) -> str | None:
    text = str(raw_param or "").strip()
    if not text:
        return None
    text = re.sub(r"^@[A-Za-z_][\w.]*(?:\([^()]*\))?\s*", "", text).strip()
    match = re.match(
        r"(?:(?:public|private|protected|internal)\s+)?(?:val|var)\s+"
        r"([A-Za-z_]\w*)\s*:",
        text,
    )
    if not match:
        return None
    field = match.group(1)
    return None if field.startswith("_") else field


def _csharp_model_fields_by_class(lines: list[str]) -> dict[str, list[str]]:
    fields_by_class: dict[str, list[str]] = {}
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        match = re.match(
            r"^\s*(?:(?:public|private|protected|internal|static|sealed|partial|abstract)\s+)*"
            r"(?P<kind>class|record)\s+(?P<name>[A-Za-z_]\w*)"
            r"(?:\s*\((?P<params>[^)]*)\))?",
            line,
        )
        if not match:
            idx += 1
            continue
        class_name = match.group("name")
        fields: list[str] = []
        seen: set[str] = set()

        if match.group("kind") == "record":
            for raw_param in _split_signature_params(match.group("params") or ""):
                field = _csharp_member_name_from_declaration(raw_param)
                if field and field not in seen:
                    seen.add(field)
                    fields.append(field)

        brace_depth = line.count("{") - line.count("}")
        pos = idx + 1
        while pos < len(lines):
            child = lines[pos]
            if brace_depth <= 0 and child.strip():
                break
            if brace_depth == 1:
                field = _csharp_member_name_from_declaration(child)
                if field and field not in seen:
                    seen.add(field)
                    fields.append(field)
                    if len(fields) >= 12:
                        break
            brace_depth += child.count("{") - child.count("}")
            pos += 1
        if fields:
            fields_by_class[class_name] = fields
        idx = max(pos, idx + 1)
    return fields_by_class


def _csharp_member_name_from_declaration(raw_line: str) -> str | None:
    line = str(raw_line or "").strip()
    if not line or line.startswith(("//", "/*", "*", "[")):
        return None
    if "(" in line and "{" not in line:
        return None
    line = line.split("//", 1)[0].strip().rstrip(";")
    if "=" in line:
        line = line.split("=", 1)[0].strip()
    tokens = re.findall(r"[A-Za-z_]\w*", line)
    skip = {
        "public", "private", "protected", "internal", "static", "readonly",
        "required", "virtual", "override", "sealed", "partial", "class", "record",
        "get", "set", "init", "new",
    }
    filtered = [token for token in tokens if token.lower() not in skip]
    if len(filtered) < 2:
        return None
    member = filtered[-1]
    if member and member[0].isupper():
        return member
    return None


def _typescript_field_name_from_declaration(raw_line: str) -> str | None:
    line = str(raw_line or "").strip()
    if not line or line.startswith(("//", "/*", "*", "@")):
        return None
    if "(" in line:
        return None
    line = line.split("//", 1)[0].strip().rstrip(",;")
    if ":" not in line:
        return None
    left = line.split(":", 1)[0].strip()
    left = re.sub(
        r"^(?:public|private|protected|readonly|static|declare|abstract)\s+",
        "",
        left,
    ).strip()
    match = re.match(r"^['\"]?([A-Za-z_][\w-]*)['\"]?\??$", left)
    if not match:
        return None
    field = match.group(1)
    if field.startswith("_"):
        return None
    return field


def _signature_external_type_hint(
    raw_param: str,
    param_name: str,
    framework_params: set[str],
) -> str | None:
    declaration = str(raw_param or "").split("=", 1)[0]
    annotations = {
        match.group(1).lower()
        for match in re.finditer(r"@([A-Za-z_][\w]*)", declaration)
    }
    skip = set(framework_params) | annotations | {
        "final", "readonly", "public", "private", "protected", "static",
        "requestbody", "frombody", "fromroute", "fromquery", "requestparam",
        "pathvariable", "valid", "validated", "notnull", "nullable",
        "request", "httprequest", "httpservletrequest", "servletrequest",
        "response", "httpresponse", "httpservletresponse", "servletresponse",
        "applicationcall", "routingcontext",
        "map", "hashmap", "dict", "dictionary", "list", "arraylist", "object",
        "string", "str", "int", "integer", "long", "float", "double", "decimal",
        "boolean", "bool", "void", "none", "null", "true", "false",
        "task", "responseentity", "iactionresult",
    }
    identifiers = re.findall(r"[A-Za-z_][\w]*", declaration)
    for identifier in reversed(identifiers):
        normalized = identifier.lower()
        if identifier == param_name or normalized == param_name.lower():
            continue
        if normalized in skip:
            continue
        return identifier
    return None


def _signature_param_name(raw_param: str) -> str | None:
    param = raw_param.strip()
    if not param or param.startswith(("*", "...")):
        return None
    param = re.sub(
        r"^(?:(?:@[A-Za-z_][\w.]*(?:\([^)]*\))?)|"
        r"(?:\[[A-Za-z_][\w.]*(?:\([^]]*\))?\])\s*)+",
        "",
        param,
    ).strip()
    param = param.split("=", 1)[0].strip()
    if ":" in param:
        param = param.split(":", 1)[0].strip()
    param = param.lstrip("*").strip()
    if re.match(r"^[A-Za-z_][\w-]*$", param):
        return param
    identifiers = re.findall(r"[A-Za-z_][\w-]*", param)
    return identifiers[-1] if identifiers else None


def _is_cli_entry_symbol(symbol: str | None) -> bool:
    return bool(symbol and symbol.lower() in {"main", "_main", "wmain"})


def _cli_option_input_hints(abs_file: str, enclosing_fn: str | None) -> list[str]:
    if not enclosing_fn:
        return []
    try:
        lines = Path(abs_file).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    fn_start: int | None = None
    for idx, line in enumerate(lines):
        if (_match_def_name(line) or _match_multiline_def_name(lines, idx)) == enclosing_fn:
            fn_start = idx
            break
    if fn_start is None:
        return []
    decorators = _decorator_lines_before_definition(lines, fn_start + 1)
    window_start = decorators[0][0] - 1 if decorators else fn_start
    fn_end = len(lines)
    for pos in range(fn_start + 1, len(lines)):
        if _is_sibling_definition_boundary(lines, fn_start, pos):
            fn_end = pos
            break
    return _cli_option_input_hints_from_text("\n".join(lines[window_start:fn_end]))


def _cli_option_input_hints_from_text(text: str) -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(
        r"(?:add_argument|click\.(?:option|argument)|typer\.(?:Option|Argument))"
        r"\s*\((?P<args>[^)]*)\)",
        text or "",
        flags=re.DOTALL,
    ):
        quoted = re.findall(r"['\"]([^'\"]+)['\"]", match.group("args"))
        if not quoted:
            continue
        hint = next((item for item in quoted if item.startswith("--")), None)
        if hint is None:
            hint = next((item for item in quoted if not item.startswith("-")), None)
        if hint and hint not in seen:
            seen.add(hint)
            hints.append(hint)
    for hint in _getopt_input_hints_from_text(text):
        if hint not in seen:
            seen.add(hint)
            hints.append(hint)
    return hints[:12]


def _cli_registration_input_hints(abs_file: str, line_number: int) -> list[str]:
    context = _cli_registration_context_for_site_file(abs_file, line_number)
    if not context:
        return []
    return _commander_input_hints_from_text(context)


def _cli_registration_context_for_site_file(abs_file: str, line_number: int) -> str | None:
    try:
        lines = Path(abs_file).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    call_idx = line_number - 1
    if call_idx < 0 or call_idx >= len(lines):
        return None
    start_idx: int | None = None
    for idx in range(call_idx, max(-1, call_idx - 24), -1):
        if re.search(r"(?:^|\.)\s*(?:command|argument|requiredOption|option|action)\s*\(", lines[idx]):
            start_idx = idx
        elif start_idx is not None and lines[idx].strip().endswith((".", ",")):
            start_idx = idx
        elif start_idx is not None and re.search(r"\b(?:program|commander|new\s+Command)\b", lines[idx]):
            start_idx = idx
            break
    if start_idx is None:
        return None
    end_idx = _call_expression_window_end(lines, start_idx, call_idx)
    return " ".join(line.strip() for line in lines[start_idx:end_idx] if line.strip())


def _commander_input_hints_from_text(text: str) -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        if value and value not in seen:
            seen.add(value)
            hints.append(value)

    for match in re.finditer(
        r"\.\s*(?:command|argument)\s*\(\s*(['\"])(?P<value>(?:\\.|(?!\1).)*?)\1",
        text or "",
        re.IGNORECASE,
    ):
        for arg in _cli_positional_args_from_command_spec(match.group("value")):
            add(arg)
    for match in re.finditer(
        r"\.\s*(?:requiredOption|option)\s*\(\s*(['\"])(?P<value>(?:\\.|(?!\1).)*?)\1",
        text or "",
        re.IGNORECASE,
    ):
        option = _cli_long_option_from_spec(match.group("value"))
        if option:
            add(option)
    return hints[:12]


def _cli_positional_args_from_command_spec(spec: str) -> list[str]:
    args: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"[<\[]\s*(?:\.\.\.)?(?P<name>[A-Za-z_][\w-]*)", spec or ""):
        name = match.group("name")
        if name not in seen:
            seen.add(name)
            args.append(name)
    return args


def _cli_long_option_from_spec(spec: str) -> str | None:
    match = re.search(r"(?<![\w-])(?P<option>--[A-Za-z0-9][\w-]*)\b", spec or "")
    if match:
        return match.group("option")
    value = str(spec or "").strip()
    if re.fullmatch(r"[A-Za-z][\w-]*", value):
        return f"--{value}"
    return None


def _merge_cli_input_hints(registration_hints: object, metadata_hints: object) -> list[str]:
    filtered_metadata = _filter_cli_signature_input_hints(metadata_hints, registration_hints)
    return _merge_ordered_input_hints(registration_hints, filtered_metadata)


def _filter_generic_cli_input_hints(hints: object) -> list[str]:
    return _filter_cli_signature_input_hints(hints, None)


def _filter_cli_signature_input_hints(
    hints: object,
    cli_hints: object | None,
) -> list[str]:
    generic_cli_containers = {"args", "argv", "opts", "options", "cmd", "command"}
    covered_by_cli = {
        key
        for hint in _coerce_input_hints(cli_hints)
        for key in [_cli_option_name_key(hint)]
        if key
    }
    return [
        hint for hint in _coerce_input_hints(hints)
        if _input_hint_dedupe_key(hint) not in generic_cli_containers
        and _cli_option_name_key(hint) not in covered_by_cli
    ]


def _merge_decorated_cli_input_hints(cli_hints: object, metadata_hints: object) -> list[str]:
    cli_items = _coerce_input_hints(cli_hints)
    option_by_key = {
        key: hint
        for hint in cli_items
        if hint.startswith("--")
        for key in [_cli_option_name_key(hint)]
        if key
    }
    merged: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        if value and value not in seen:
            seen.add(value)
            merged.append(value)

    for hint in _coerce_input_hints(metadata_hints):
        if _input_hint_dedupe_key(hint) in {"args", "argv", "opts", "options", "cmd", "command"}:
            continue
        add(option_by_key.get(_cli_option_name_key(hint), hint))
    for hint in cli_items:
        add(hint)
    return merged[:12]


def _cli_option_name_key(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text.startswith("--"):
        text = text[2:]
    elif text.startswith("-"):
        return ""
    text = text.split()[0].strip("<>[].,;:")
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _getopt_input_hints_from_text(text: str) -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r'\{\s*"([A-Za-z_][\w-]*)"', text or ""):
        hint = f"--{match.group(1)}"
        if hint not in seen:
            seen.add(hint)
            hints.append(hint)
    if hints:
        return hints
    for match in re.finditer(r"\bgetopt(?:_long)?\s*\([^)]*?['\"]([^'\"]+)['\"]", text or ""):
        for opt in _short_getopt_hints(match.group(1)):
            if opt not in seen:
                seen.add(opt)
                hints.append(opt)
    return hints


def _short_getopt_hints(spec: str) -> list[str]:
    hints: list[str] = []
    idx = 0
    while idx < len(spec):
        char = spec[idx]
        if char.isalnum():
            hints.append(f"-{char}")
        idx += 1
        while idx < len(spec) and spec[idx] == ":":
            idx += 1
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
    definition_match = _find_strict_definition_match(lines, function_name)
    if not definition_match:
        return None
    definition_line, source_function_name = definition_match
    go_grpc_entry = _go_grpc_registration_entry_for_symbol(
        repo_root,
        source_file,
        source_function_name,
        lines,
        definition_line,
    )
    if go_grpc_entry:
        return go_grpc_entry
    java_grpc_entry = _java_grpc_registration_entry_for_symbol(
        repo_root,
        source_file,
        source_function_name,
        lines,
        definition_line,
    )
    if java_grpc_entry:
        return java_grpc_entry
    serverless_entry = _serverless_handler_entry_for_symbol(
        repo_root,
        source_file,
        source_function_name,
        lines,
        definition_line,
    )
    if serverless_entry:
        return serverless_entry
    ruby_worker_entry = _ruby_worker_entry_for_symbol(
        repo_root,
        source_file,
        source_function_name,
        lines,
        definition_line,
    )
    if ruby_worker_entry:
        return ruby_worker_entry
    php_job_entry = _php_queue_job_entry_for_symbol(
        repo_root,
        source_file,
        source_function_name,
        lines,
        definition_line,
    )
    if php_job_entry:
        return php_job_entry
    definition_text = lines[definition_line - 1] if 0 < definition_line <= len(lines) else ""
    decorators = _decorator_lines_before_definition(lines, definition_line)
    decorator_texts = [text for _, text in decorators]
    entry_kind = _classify_entry_decorator(decorator_texts)
    tool = "source-decorator"
    if not entry_kind:
        entry_kind = _classify_inline_entry_definition(
            str(source_file),
            source_function_name,
            definition_text,
        )
        tool = "source-inline-entry"
    if not entry_kind:
        return None

    if decorators and tool == "source-decorator":
        evidence_line_number = decorators[0][0]
        evidence_text = " ".join(text.strip() for _, text in decorators)
    else:
        evidence_line_number, evidence_text = definition_line, definition_text
    rel_file = _relative_path(repo_root, source_file)
    metadata = _entry_metadata_for_site(str(source_file), definition_line, source_function_name)
    if entry_kind == "cli":
        cli_hints = _cli_option_input_hints(str(source_file), source_function_name)
        if cli_hints:
            metadata["input_hints"] = _merge_decorated_cli_input_hints(
                cli_hints,
                metadata.get("input_hints"),
            )
    if entry_kind in {"message", "queue"}:
        payload_type_hints = _message_payload_type_input_hints(
            _collect_signature_text(lines, definition_line - 1)
        )
        if payload_type_hints:
            metadata["input_hints"] = _merge_ordered_strings(
                payload_type_hints,
                metadata.get("input_hints"),
            )
    if entry_kind == "route":
        receiver_prefix = _route_prefix_for_decorator_receiver(
            repo_root,
            source_file,
            lines,
            decorator_texts,
            definition_line,
        )
        class_prefix = _route_class_prefix_for_definition(lines, definition_line)
        route_prefix = _combine_route_prefixes(class_prefix, receiver_prefix)
        route_hints = _route_template_input_hints([*decorator_texts, definition_text])
        if route_prefix:
            route_hints = _merge_ordered_strings(
                _route_template_input_hints([route_prefix]),
                route_hints,
            )
        if route_hints:
            metadata["input_hints"] = _merge_ordered_strings(
                metadata.get("input_hints"),
                route_hints,
            )
        route_trigger = _route_external_trigger_from_texts([*decorator_texts, definition_text])
        if route_prefix and route_trigger:
            route_trigger = _route_trigger_with_prefix(route_trigger, route_prefix)
        elif route_prefix:
            route_method = _route_method_from_text(" ".join([*decorator_texts, definition_text]))
            if route_method:
                route_trigger = f"{route_method} {route_prefix}"
        if route_trigger:
            route_trigger = _expand_route_tokens(
                route_trigger,
                lines=lines,
                definition_line=definition_line,
                function_name=source_function_name,
            )
            metadata["external_trigger"] = route_trigger
    if entry_kind != "route":
        channel_hints = _registration_channel_input_hints(
            " ".join(text for _, text in decorators),
            entry_kind,
        )
        if channel_hints:
            metadata["input_hints"] = _merge_ordered_strings(
                channel_hints,
                metadata.get("input_hints"),
            )
            metadata["entry_label"] = (
                f"{_ENTRY_DISCOVERY_KIND_LABELS.get(entry_kind, '外部入口')} {channel_hints[0]}"
            )
    entry_label = metadata.pop("entry_label", None)
    entry = {
        "entry_kind": entry_kind,
        "entry_symbol": source_function_name,
        "entry_file": rel_file,
        "entry_label": entry_label or _public_entry_label(entry_kind, source_function_name),
        "call_line": definition_line,
        "chain": [source_function_name],
        "depth": 0,
        "evidence": f"{rel_file}:{evidence_line_number} {evidence_text.strip()}",
        "tool": tool,
    }
    entry.update(metadata)
    return entry


def _go_grpc_registration_entry_for_symbol(
    repo_root: Path,
    source_file: Path,
    function_name: str,
    lines: list[str],
    definition_line: int,
) -> dict | None:
    if source_file.suffix.lower() != ".go":
        return None
    signature = _collect_go_signature_text(lines, definition_line)
    receiver = _go_receiver_type_for_method(signature, function_name)
    if not receiver:
        return None
    registration = _find_go_grpc_registration_for_receiver(repo_root, receiver)
    if registration is None:
        return None
    reg_file, line_number, registration_line = registration
    enclosing, _guard = _caller_context(str(reg_file), line_number)
    rel_file = _relative_path(repo_root, reg_file)
    method_hints = _go_grpc_request_type_hints(signature)
    service_name = _go_grpc_service_name_from_registration(registration_line)
    entry_label = f"gRPC {service_name}" if service_name else _public_entry_label("grpc", enclosing or receiver)
    return {
        "entry_kind": "grpc",
        "entry_symbol": enclosing or receiver,
        "entry_file": rel_file,
        "entry_label": entry_label,
        "call_line": line_number,
        "chain": [enclosing, function_name] if enclosing else [function_name],
        "depth": 1 if enclosing else 0,
        "evidence": f"{rel_file}:{line_number} {registration_line.strip()}",
        "tool": "source-grpc-registration",
        "input_hints": method_hints,
    }


def _collect_go_signature_text(lines: list[str], definition_line: int) -> str:
    idx = max(0, definition_line - 1)
    parts: list[str] = []
    paren_depth = 0
    for line in lines[idx:min(len(lines), idx + 8)]:
        stripped = line.strip()
        parts.append(stripped)
        paren_depth += stripped.count("(") - stripped.count(")")
        if "{" in stripped and paren_depth <= 0:
            break
    return " ".join(parts)


def _go_receiver_type_for_method(signature: str, function_name: str) -> str | None:
    match = re.search(
        rf"\bfunc\s*\(\s*\w+\s+\*?(?P<receiver>[A-Za-z_]\w*)\s*\)\s+{re.escape(function_name)}\s*\(",
        signature or "",
    )
    return match.group("receiver") if match else None


def _go_grpc_request_type_hints(signature: str) -> list[str]:
    match = re.search(
        r"\b\w+\s+\*?(?:[A-Za-z_]\w*\.)?(?P<type>[A-Za-z_]\w*Request)\b",
        signature or "",
    )
    return [match.group("type")] if match else []


def _find_go_grpc_registration_for_receiver(
    repo_root: Path,
    receiver: str,
) -> tuple[Path, int, str] | None:
    receiver_pattern = re.escape(receiver)
    register_re = re.compile(
        rf"\b(?:[A-Za-z_]\w*\.)?Register[A-Za-z_]\w*Server\s*\([^)]*(?:&\s*)?{receiver_pattern}\s*(?:\{{\s*\}})?",
    )
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in _DIR_SKIP and not d.startswith(".")]
        for fname in files:
            path = Path(root) / fname
            if path.suffix.lower() != ".go":
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for idx, line in enumerate(lines, start=1):
                if register_re.search(line):
                    return path, idx, line
    return None


def _go_grpc_service_name_from_registration(registration_line: str) -> str | None:
    match = re.search(r"\bRegister(?P<service>[A-Za-z_]\w*)Server\s*\(", registration_line or "")
    return match.group("service") if match else None


def _java_grpc_registration_entry_for_symbol(
    repo_root: Path,
    source_file: Path,
    function_name: str,
    lines: list[str],
    definition_line: int,
) -> dict | None:
    if source_file.suffix.lower() != ".java":
        return None
    class_info = _java_enclosing_grpc_service_class(lines, definition_line)
    if class_info is None:
        return None
    class_name, class_header = class_info
    registration = _find_java_grpc_registration_for_class(repo_root, class_name)
    if registration is None:
        return None
    reg_file, line_number, registration_line = registration
    enclosing, _guard = _caller_context(str(reg_file), line_number)
    rel_file = _relative_path(repo_root, reg_file)
    signature = _collect_signature_text(lines, definition_line - 1)
    method_hints = _signature_input_params(
        signature,
        model_fields_by_type=_source_model_fields_by_class(lines, ".java"),
    )
    service_name = _java_grpc_service_name_from_class_header(class_name, class_header)
    entry_label = f"gRPC {service_name}" if service_name else _public_entry_label("grpc", enclosing or class_name)
    return {
        "entry_kind": "grpc",
        "entry_symbol": enclosing or class_name,
        "entry_file": rel_file,
        "entry_label": entry_label,
        "call_line": line_number,
        "chain": [enclosing, function_name] if enclosing else [function_name],
        "depth": 1 if enclosing else 0,
        "evidence": f"{rel_file}:{line_number} {registration_line.strip()}",
        "tool": "source-grpc-registration",
        "input_hints": method_hints,
    }


def _java_enclosing_grpc_service_class(
    lines: list[str],
    definition_line: int,
) -> tuple[str, str] | None:
    definition_idx = definition_line - 1
    for idx in range(definition_idx, -1, -1):
        header = _java_collect_class_header(lines, idx)
        if not header:
            continue
        match = re.search(r"\bclass\s+(?P<name>[A-Za-z_]\w*)\b", header)
        if not match:
            continue
        if not _java_class_header_looks_like_grpc_service(header):
            continue
        return match.group("name"), header
    return None


def _java_collect_class_header(lines: list[str], idx: int) -> str | None:
    if idx < 0 or idx >= len(lines):
        return None
    if not re.search(r"\bclass\s+[A-Za-z_]\w*\b", lines[idx]):
        return None
    parts: list[str] = []
    for pos in range(idx, min(len(lines), idx + 8)):
        text = lines[pos].strip()
        if not text:
            continue
        parts.append(text)
        if "{" in text:
            break
    return " ".join(parts)


def _java_class_header_looks_like_grpc_service(header: str) -> bool:
    if re.search(r"\bimplements\b[^{};]*\bBindableService\b", header):
        return True
    return bool(re.search(
        r"\bextends\b[^{};]*\b[A-Za-z_]\w*Grpc\s*\.\s*[A-Za-z_]\w*ImplBase\b",
        header,
    ))


def _find_java_grpc_registration_for_class(
    repo_root: Path,
    class_name: str,
) -> tuple[Path, int, str] | None:
    class_pattern = re.escape(class_name)
    register_re = re.compile(
        rf"\.addService\s*\(\s*new\s+{class_pattern}\s*\(",
    )
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in _DIR_SKIP and not d.startswith(".")]
        for fname in files:
            path = Path(root) / fname
            if path.suffix.lower() != ".java":
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for idx, line in enumerate(lines, start=1):
                if "addService" not in line:
                    continue
                window = " ".join(part.strip() for part in lines[idx - 1:min(len(lines), idx + 4)])
                if register_re.search(window):
                    return path, idx, line
    return None


def _java_grpc_service_name_from_class_header(class_name: str, class_header: str) -> str | None:
    match = re.search(r"\bextends\b[^{};]*\b(?P<service>[A-Za-z_]\w*)Grpc\s*\.", class_header or "")
    if match:
        return match.group("service")
    if class_name.endswith("Impl") and len(class_name) > len("Impl"):
        return class_name[: -len("Impl")]
    return class_name or None


def _serverless_handler_entry_for_symbol(
    repo_root: Path,
    source_file: Path,
    function_name: str,
    lines: list[str],
    definition_line: int,
) -> dict | None:
    if source_file.suffix.lower() not in {".py", ".js", ".jsx", ".ts", ".tsx"}:
        return None
    definition_idx = definition_line - 1
    if definition_idx < 0 or definition_idx >= len(lines):
        return None
    definition_text = lines[definition_idx].strip()
    params = _signature_input_params(definition_text)
    normalized_params = {param.lower() for param in params}
    if "event" not in normalized_params:
        return None

    path_text = source_file.as_posix().lower()
    symbol = function_name.lower()
    strong_path = any(
        token in path_text
        for token in ("/lambda/", "/lambdas/", "/functions/", "/serverless/")
    )
    if symbol != "lambda_handler" and not (symbol == "handler" and strong_path):
        return None

    rel_file = _relative_path(repo_root, source_file)
    event_hints = _event_payload_input_hints(lines, definition_idx)
    input_hints = _merge_ordered_strings(event_hints, ["event"])
    entry = {
        "entry_kind": "event",
        "entry_symbol": function_name,
        "entry_file": rel_file,
        "entry_label": "serverless event handler",
        "call_line": definition_line,
        "chain": [function_name],
        "depth": 0,
        "evidence": f"{rel_file}:{definition_line} {definition_text}",
        "tool": "source-serverless-handler",
    }
    if input_hints:
        entry["input_hints"] = input_hints
    return entry


def _event_payload_input_hints(lines: list[str], definition_idx: int) -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        value = str(value or "").strip()
        if value in {
            "get", "body", "headers", "queryStringParameters", "pathParameters",
            "multiValueQueryStringParameters", "multiValueHeaders",
        }:
            return
        if not value or value in seen:
            return
        seen.add(value)
        hints.append(value)

    fn_end = len(lines)
    for pos in range(definition_idx + 1, len(lines)):
        if _is_sibling_definition_boundary(lines, definition_idx, pos):
            fn_end = pos
            break
    text = "\n".join(lines[definition_idx:fn_end])
    event_request_containers = (
        "queryStringParameters", "pathParameters", "headers",
        "multiValueQueryStringParameters", "multiValueHeaders",
    )
    container_pattern = "|".join(re.escape(item) for item in event_request_containers)
    alias_names: set[str] = set()
    for match in re.finditer(
        rf"\b(?P<alias>[A-Za-z_]\w*)\s*=\s*event"
        rf"(?:\??\.\s*(?P<dot>{container_pattern})"
        rf"|\s*\[\s*['\"](?P<bracket>{container_pattern})['\"]\s*\]"
        rf"|\.\s*get\s*\(\s*['\"](?P<get>{container_pattern})['\"]\s*\))",
        text,
    ):
        alias = match.group("alias")
        if alias and alias != "event":
            alias_names.add(alias)
    nested_patterns = []
    for alias in sorted(alias_names):
        escaped = re.escape(alias)
        nested_patterns.extend([
            rf"\b{escaped}\s*\.\s*get\s*\(\s*['\"](?P<get>[A-Za-z_][\w-]*)['\"]",
            rf"\b{escaped}\s*\[\s*['\"](?P<bracket>[A-Za-z_][\w-]*)['\"]\s*\]",
            rf"\b{escaped}\??\.\s*(?P<dot>[A-Za-z_][\w-]*)\b",
        ])
    nested_patterns.append(
        rf"\bevent(?:\??\.\s*(?:{container_pattern})"
        rf"|\s*\[\s*['\"](?:{container_pattern})['\"]\s*\])"
        rf"(?:\??\.\s*(?P<dot>[A-Za-z_][\w-]*)"
        rf"|\s*\[\s*['\"](?P<bracket>[A-Za-z_][\w-]*)['\"]\s*\])"
    )
    for pattern in nested_patterns:
        for match in re.finditer(pattern, text):
            add(match.groupdict().get("dot") or match.groupdict().get("bracket") or match.groupdict().get("get") or "")
    for pattern in (
        r"\bevent\s*\.\s*get\s*\(\s*['\"]([A-Za-z_][\w-]*)['\"]",
        r"\bevent\s*\[\s*['\"]([A-Za-z_][\w-]*)['\"]\s*\]",
        r"\bevent\s*\??\.\s*([A-Za-z_][\w-]*)\b",
    ):
        for match in re.finditer(pattern, text):
            value = match.group(1).strip()
            if value in {"get", "body", "headers", "queryStringParameters", "pathParameters"}:
                continue
            add(value)
    return hints[:12]


def _ruby_worker_entry_for_symbol(
    repo_root: Path,
    source_file: Path,
    function_name: str,
    lines: list[str],
    definition_line: int,
) -> dict | None:
    if source_file.suffix.lower() != ".rb" or function_name != "perform":
        return None
    definition_idx = definition_line - 1
    if definition_idx < 0 or definition_idx >= len(lines):
        return None
    class_start = _ruby_enclosing_class_start(lines, definition_idx)
    if class_start is None:
        return None
    class_end = _ruby_class_context_end(lines, class_start)
    class_lines = lines[class_start:class_end]
    marker = _ruby_worker_marker(class_lines, class_start)
    if marker is None:
        return None
    evidence_line, evidence_text = marker
    rel_file = _relative_path(repo_root, source_file)
    definition_text = lines[definition_idx]
    queue_hints = _ruby_worker_queue_hints(class_lines)
    signature_hints = _signature_input_params(definition_text.strip())
    input_hints = _merge_ordered_strings(queue_hints, signature_hints)
    entry = {
        "entry_kind": "job",
        "entry_symbol": function_name,
        "entry_file": rel_file,
        "entry_label": (
            f"job {queue_hints[0]}" if queue_hints
            else _public_entry_label("job", function_name)
        ),
        "call_line": definition_line,
        "chain": [function_name],
        "depth": 0,
        "evidence": f"{rel_file}:{evidence_line} {evidence_text.strip()}",
        "tool": "source-ruby-worker",
    }
    if input_hints:
        entry["input_hints"] = input_hints
    return entry


def _ruby_enclosing_class_start(lines: list[str], definition_idx: int) -> int | None:
    def_indent = _line_indent(lines[definition_idx])
    for idx in range(definition_idx - 1, -1, -1):
        stripped = lines[idx].strip()
        if not stripped:
            continue
        if re.match(r"^(?:class|module)\s+[A-Za-z_:][\w:]*\b", stripped):
            if _line_indent(lines[idx]) < def_indent:
                return idx
    return None


def _ruby_class_context_end(lines: list[str], class_start: int) -> int:
    depth = 0
    block_start_re = re.compile(
        r"^(?:class|module|def|if|unless|case|begin|while|until|for)\b"
    )
    for idx in range(class_start, len(lines)):
        stripped = lines[idx].strip()
        if not stripped:
            continue
        if block_start_re.match(stripped):
            depth += 1
        if stripped == "end" or stripped.startswith("end "):
            depth -= 1
            if depth <= 0:
                return idx + 1
    return len(lines)


def _ruby_worker_marker(
    class_lines: list[str],
    class_start: int,
) -> tuple[int, str] | None:
    for offset, line in enumerate(class_lines):
        text = line.strip()
        if (
            "Sidekiq::Worker" in text
            or "Sidekiq::Job" in text
            or text.startswith("sidekiq_options")
        ):
            return class_start + offset + 1, line
    return None


def _ruby_worker_queue_hints(class_lines: list[str]) -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()
    text = "\n".join(class_lines)
    for pattern in (
        r"\bqueue:\s*['\"](?P<queue>[^'\"]+)['\"]",
        r"\bqueue:\s*:(?P<queue>[A-Za-z_]\w*)",
        r"\bqueue_as\s+['\"](?P<queue>[^'\"]+)['\"]",
        r"\bqueue_as\s+:(?P<queue>[A-Za-z_]\w*)",
    ):
        for match in re.finditer(pattern, text):
            queue = match.group("queue").strip()
            if queue and queue not in seen:
                seen.add(queue)
                hints.append(queue)
    return hints[:4]


def _php_queue_job_entry_for_symbol(
    repo_root: Path,
    source_file: Path,
    function_name: str,
    lines: list[str],
    definition_line: int,
) -> dict | None:
    if source_file.suffix.lower() != ".php" or function_name != "handle":
        return None
    definition_idx = definition_line - 1
    if definition_idx < 0 or definition_idx >= len(lines):
        return None
    class_start = _php_enclosing_class_start(lines, definition_idx)
    if class_start is None:
        return None
    class_end = _php_class_context_end(lines, class_start)
    if definition_idx >= class_end:
        return None
    class_lines = lines[class_start:class_end]
    marker = _php_queue_job_marker(class_lines, class_start)
    if marker is None:
        return None
    evidence_line, evidence_text = marker
    rel_file = _relative_path(repo_root, source_file)
    queue_hints = _php_queue_job_queue_hints(class_lines)
    constructor_hints = _php_constructor_input_hints(class_lines)
    handle_hints = _signature_input_params(lines[definition_idx].strip())
    input_hints = _merge_ordered_strings(queue_hints, constructor_hints, handle_hints)
    entry = {
        "entry_kind": "job",
        "entry_symbol": function_name,
        "entry_file": rel_file,
        "entry_label": (
            f"job {queue_hints[0]}" if queue_hints
            else _public_entry_label("job", function_name)
        ),
        "call_line": definition_line,
        "chain": [function_name],
        "depth": 0,
        "evidence": f"{rel_file}:{evidence_line} {evidence_text.strip()}",
        "tool": "source-php-job",
    }
    if input_hints:
        entry["input_hints"] = input_hints
    return entry


def _php_enclosing_class_start(lines: list[str], definition_idx: int) -> int | None:
    for idx in range(definition_idx - 1, -1, -1):
        stripped = lines[idx].strip()
        if not stripped:
            continue
        if re.match(r"^(?:abstract\s+|final\s+)?class\s+[A-Za-z_]\w*\b", stripped):
            return idx
    return None


def _php_class_context_end(lines: list[str], class_start: int) -> int:
    depth = 0
    saw_class_open = False
    for idx in range(class_start, len(lines)):
        text = lines[idx]
        depth += text.count("{")
        if "{" in text:
            saw_class_open = True
        depth -= text.count("}")
        if saw_class_open and depth <= 0:
            return idx + 1
    return len(lines)


def _php_queue_job_marker(
    class_lines: list[str],
    class_start: int,
) -> tuple[int, str] | None:
    marker_tokens = (
        "ShouldQueue",
        "ShouldBeUnique",
        "ShouldBeEncrypted",
        "ShouldQueueAfterCommit",
    )
    for offset, line in enumerate(class_lines):
        if any(token in line for token in marker_tokens):
            return class_start + offset + 1, line
    return None


def _php_queue_job_queue_hints(class_lines: list[str]) -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()
    text = "\n".join(class_lines)
    for pattern in (
        r"\$(?:queue|connection)\s*=\s*['\"](?P<queue>[^'\"]+)['\"]",
        r"->onQueue\(\s*['\"](?P<queue>[^'\"]+)['\"]\s*\)",
    ):
        for match in re.finditer(pattern, text):
            queue = match.group("queue").strip()
            if queue and queue not in seen:
                seen.add(queue)
                hints.append(queue)
    return hints[:4]


def _php_constructor_input_hints(class_lines: list[str]) -> list[str]:
    for idx, line in enumerate(class_lines):
        if _match_def_name(line) != "__construct":
            continue
        signature = line.strip()
        while ")" not in signature and idx + 1 < len(class_lines):
            idx += 1
            signature += " " + class_lines[idx].strip()
        return _signature_input_params(signature)
    return []


def _decorator_lines_before_definition(
    lines: list[str],
    definition_line: int,
) -> list[tuple[int, str]]:
    decorators: list[tuple[int, str]] = []
    idx = definition_line - 2
    while idx >= 0:
        if not lines[idx].strip():
            break
        block = _decorator_block_ending_at(lines, idx)
        if not block:
            break
        start_idx, block_lines = block
        decorators[0:0] = block_lines
        idx = start_idx - 1
    return decorators


def _decorator_block_ending_at(
    lines: list[str],
    end_idx: int,
) -> tuple[int, list[tuple[int, str]]] | None:
    max_start = max(0, end_idx - 24)
    for start_idx in range(end_idx, max_start - 1, -1):
        if not lines[start_idx].strip():
            break
        if not _decorator_start_line(lines[start_idx]):
            continue
        block_texts = lines[start_idx:end_idx + 1]
        if not _decorator_block_is_balanced(block_texts):
            continue
        return (
            start_idx,
            [(idx + 1, lines[idx]) for idx in range(start_idx, end_idx + 1)],
        )
    return None


def _decorator_start_line(line: str) -> bool:
    stripped = line.strip()
    return bool(
        re.match(r"^@[A-Za-z_][\w.]*\b", stripped)
        or re.match(r"^\[[A-Za-z_][\w.]*\b", stripped)
        or re.match(r"^#\s*\[\s*[A-Za-z_][\w.]*\b", stripped)
    )


def _decorator_block_is_balanced(lines: list[str]) -> bool:
    joined = "\n".join(lines)
    balances = {
        "(": joined.count("(") - joined.count(")"),
        "[": joined.count("[") - joined.count("]"),
        "{": joined.count("{") - joined.count("}"),
    }
    return all(value == 0 for value in balances.values())


def _classify_entry_decorator(decorator_lines: list[str]) -> str | None:
    text = " ".join(line.strip() for line in decorator_lines).lower()
    if not text:
        return None
    if re.search(
        r"#\s*\[\s*(?:get|post|put|patch|delete|head|options|route)\b",
        text,
    ):
        return "route"
    for kind, tokens in _ENTRY_DECORATOR_KIND_TOKENS:
        if any(token in text for token in tokens):
            return kind
    return None


def _classify_inline_entry_definition(
    file_path: str,
    function_name: str | None,
    definition_text: str,
) -> str | None:
    if not _INLINE_ROUTE_DEFINITION_RE.search(definition_text or ""):
        return None
    entry_kind = _classify_entry(file_path, function_name, definition_text)
    if entry_kind in {"route", "endpoint", "api"}:
        return "route"
    return None


def _route_template_input_hints(decorator_lines: list[str]) -> list[str]:
    text = " ".join(line.strip() for line in decorator_lines)
    hints: list[str] = []
    seen: set[str] = set()
    for pattern in (
        r"\{([A-Za-z_][\w-]*)\}",
        r"/:([A-Za-z_][\w-]*)\b",
    ):
        for match in re.finditer(pattern, text):
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                hints.append(name)
    for match in re.finditer(r"<(?:[A-Za-z_][\w.]*:)?([A-Za-z_][\w-]*)>", text):
        prefix = text[match.start() - 1] if match.start() > 0 else ""
        if prefix and re.match(r"[\w:]", prefix):
            continue
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            hints.append(name)
    return hints[:12]


def _route_external_trigger_from_texts(texts: list[str]) -> str | None:
    text = " ".join(str(line or "").strip() for line in texts if str(line or "").strip())
    if not text:
        return None
    path = _route_path_from_text(text)
    if not path:
        return None
    method = _route_method_from_text(text)
    return f"{method} {path}" if method else path


def _route_prefix_for_decorator_receiver(
    repo_root: Path,
    source_file: Path,
    lines: list[str],
    decorator_texts: list[str],
    definition_line: int,
) -> str | None:
    receiver = _route_decorator_receiver(decorator_texts)
    if not receiver:
        return None
    receiver_pattern = re.escape(receiver)
    scan_until = max(0, definition_line - 1)
    for idx in range(scan_until - 1, -1, -1):
        statement = _collect_assignment_statement_ending_at(lines, idx)
        if not statement:
            continue
        if not re.search(rf"\b{receiver_pattern}\s*=", statement):
            continue
        prefix = _router_prefix_from_assignment(statement)
        if prefix:
            return prefix
    return _route_include_prefix_for_decorator_receiver(repo_root, source_file, receiver)


def _route_include_prefix_for_decorator_receiver(
    repo_root: Path,
    source_file: Path,
    receiver: str,
) -> str | None:
    receiver_name = receiver.split(".")[-1]
    source_stem = source_file.stem
    try:
        rel_source = source_file.resolve().relative_to(repo_root.resolve())
    except Exception:
        rel_source = source_file
    module_parts = [part for part in rel_source.with_suffix("").parts if part]
    module_suffixes = {
        ".".join(module_parts[index:])
        for index in range(len(module_parts))
        if module_parts[index:]
    }
    module_suffixes.add(source_stem)
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in _DIR_SKIP and not d.startswith(".")]
        for name in files:
            path = Path(root) / name
            if path.suffix.lower() != ".py":
                continue
            try:
                candidate_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            aliases = _router_import_aliases_for_source(
                candidate_lines,
                module_suffixes,
                receiver_name,
            )
            names = {receiver_name, *aliases}
            for idx, line in enumerate(candidate_lines):
                if "include_router" not in line and "register_blueprint" not in line:
                    continue
                statement = _call_expression_context_from_start(candidate_lines, idx)
                prefix = _mounted_route_prefix_from_statement(statement, names)
                if prefix:
                    return prefix
    return None


def _route_class_prefix_for_definition(lines: list[str], definition_line: int) -> str | None:
    class_line = _nearest_enclosing_class_line(lines, definition_line)
    if class_line is None:
        return None
    class_decorators = _decorator_lines_before_definition(lines, class_line)
    if not class_decorators:
        return None
    return _route_prefix_from_class_decorators([text for _, text in class_decorators])


def _nearest_enclosing_class_line(lines: list[str], definition_line: int) -> int | None:
    start = max(0, definition_line - 2)
    for idx in range(start, -1, -1):
        stripped = lines[idx].strip()
        if re.match(
            r"^(?:export\s+|public\s+|private\s+|protected\s+|abstract\s+|final\s+|sealed\s+|partial\s+)*"
            r"class\s+[A-Za-z_]\w*\b",
            stripped,
        ):
            return idx + 1
    return None


def _nearest_enclosing_class_name(lines: list[str], definition_line: int) -> str | None:
    class_line = _nearest_enclosing_class_line(lines, definition_line)
    if class_line is None or class_line < 1 or class_line > len(lines):
        return None
    match = re.search(r"\bclass\s+(?P<name>[A-Za-z_]\w*)\b", lines[class_line - 1])
    return match.group("name") if match else None


def _route_prefix_from_class_decorators(decorator_texts: list[str]) -> str | None:
    text = " ".join(str(line or "").strip() for line in decorator_texts if str(line or "").strip())
    if not text:
        return None
    if not re.search(r"[@\[]\s*(?:Controller|RequestMapping|Route)\b", text):
        return None
    path = _route_path_from_text(text)
    return path if path and _looks_like_route_path(path) else None


def _combine_route_prefixes(*prefixes: str | None) -> str | None:
    combined = ""
    for prefix in prefixes:
        if not prefix:
            continue
        combined = _join_route_paths(combined, prefix) if combined else prefix
    return combined or None


def _expand_route_tokens(
    trigger: str,
    *,
    lines: list[str],
    definition_line: int,
    function_name: str,
) -> str:
    value = str(trigger or "")
    if "[" not in value:
        return value
    class_name = _nearest_enclosing_class_name(lines, definition_line)
    controller = _route_token_name_from_class(class_name)
    action = _route_token_name_from_symbol(function_name)
    replacements = {
        "controller": controller,
        "action": action,
    }

    def replace(match: re.Match) -> str:
        key = match.group("token").lower()
        replacement = replacements.get(key)
        return replacement if replacement else match.group(0)

    return re.sub(r"\[(?P<token>controller|action)\]", replace, value, flags=re.IGNORECASE)


def _route_token_name_from_class(class_name: str | None) -> str:
    name = str(class_name or "").strip()
    if name.endswith("Controller") and len(name) > len("Controller"):
        name = name[: -len("Controller")]
    return _route_token_slug(name)


def _route_token_name_from_symbol(symbol: str | None) -> str:
    name = str(symbol or "").strip()
    if name.endswith("Async") and len(name) > len("Async"):
        name = name[: -len("Async")]
    return _route_token_slug(name)


def _route_token_slug(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1-\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", text)
    text = re.sub(r"[_\s]+", "-", text)
    return text.strip("-").lower()


def _router_import_aliases_for_source(
    lines: list[str],
    module_suffixes: set[str],
    receiver_name: str,
) -> set[str]:
    aliases: set[str] = set()
    import_re = re.compile(
        rf"\bfrom\s+(?P<module>[A-Za-z_][\w.]*|(?:\.+[A-Za-z_][\w.]*)?)\s+import\s+(?P<items>.+)"
    )
    item_re = re.compile(
        rf"\b{re.escape(receiver_name)}\b(?:\s+as\s+(?P<alias>[A-Za-z_]\w*))?"
    )
    for line in lines:
        match = import_re.search(line.strip())
        if not match:
            continue
        module = match.group("module").lstrip(".")
        if module and not any(module == suffix or module.endswith("." + suffix) for suffix in module_suffixes):
            continue
        for item in match.group("items").split(","):
            item_match = item_re.search(item.strip())
            if item_match:
                aliases.add(item_match.group("alias") or receiver_name)
    return aliases


def _include_router_statement_uses_name(statement: str, name: str) -> bool:
    return bool(re.search(
        rf"\.include_router\s*\(\s*(?:[A-Za-z_]\w*\.)?{re.escape(name)}\b",
        statement or "",
    ))


def _include_router_prefix_from_statement(statement: str) -> str | None:
    match = re.search(
        r"\bprefix\s*=\s*(['\"])(?P<prefix>(?:\\.|(?!\1).)*?)\1",
        statement or "",
    )
    if not match:
        return None
    prefix = match.group("prefix").strip()
    return prefix if _looks_like_route_path(prefix) else None


def _mounted_route_prefix_from_statement(statement: str, names: set[str]) -> str | None:
    if any(_include_router_statement_uses_name(statement, item) for item in names):
        return _include_router_prefix_from_statement(statement)
    if any(_register_blueprint_statement_uses_name(statement, item) for item in names):
        return _register_blueprint_prefix_from_statement(statement)
    return None


def _register_blueprint_statement_uses_name(statement: str, name: str) -> bool:
    return bool(re.search(
        rf"\.register_blueprint\s*\(\s*(?:[A-Za-z_]\w*\.)?{re.escape(name)}\b",
        statement or "",
    ))


def _register_blueprint_prefix_from_statement(statement: str) -> str | None:
    match = re.search(
        r"\burl_prefix\s*=\s*(['\"])(?P<prefix>(?:\\.|(?!\1).)*?)\1",
        statement or "",
    )
    if not match:
        return None
    prefix = match.group("prefix").strip()
    return prefix if _looks_like_route_path(prefix) else None


def _route_mount_prefix_for_site(lines: list[str], site_text: str) -> str | None:
    receiver = _route_call_receiver(site_text)
    if not receiver:
        return None
    receiver_pattern = re.escape(receiver)
    for idx, line in enumerate(lines):
        if not re.search(r"\.\s*(?:use|mount)\s*\(", line or "", re.IGNORECASE):
            continue
        statement = _call_expression_context_from_start(lines, idx)
        match = re.search(
            rf"\.\s*(?:use|mount)\s*\(\s*(['\"])(?P<prefix>(?:\\.|(?!\1).)*?)\1\s*,\s*"
            rf"(?:[A-Za-z_]\w*\.)?{receiver_pattern}\b",
            statement or "",
            re.IGNORECASE,
        )
        if not match:
            continue
        prefix = match.group("prefix").strip()
        if _looks_like_route_path(prefix):
            return prefix
    return None


def _call_expression_context_from_start(lines: list[str], start_idx: int) -> str:
    end_idx = _call_expression_window_end(lines, start_idx, start_idx)
    return " ".join(line.strip() for line in lines[start_idx:end_idx] if line.strip())


def _route_mount_prefix_for_site_file(abs_file: str, site_text: str) -> str | None:
    try:
        lines = Path(abs_file).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    return _route_mount_prefix_for_site(lines, site_text)


def _route_call_receiver(text: str) -> str | None:
    match = re.search(
        r"\b(?P<receiver>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\.\s*"
        r"(?:get|post|put|patch|delete|head|options|any|route|api_route|websocket)\s*\(",
        text or "",
        re.IGNORECASE,
    )
    if match:
        return match.group("receiver").split(".")[-1]
    return None


def _route_decorator_receiver(decorator_texts: list[str]) -> str | None:
    for text in reversed(decorator_texts):
        match = re.search(
            r"@\s*(?P<receiver>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\.\s*"
            r"(?:get|post|put|patch|delete|head|options|any|route|api_route|websocket)\s*\(",
            text or "",
            re.IGNORECASE,
        )
        if match:
            return match.group("receiver")
    return None


def _collect_assignment_statement_ending_at(lines: list[str], end_index: int) -> str:
    collected: list[str] = []
    depth = 0
    for idx in range(end_index, max(-1, end_index - 12), -1):
        text = lines[idx].strip()
        if not text:
            if not collected:
                continue
            break
        collected.insert(0, text)
        depth += text.count(")") - text.count("(")
        depth += text.count("]") - text.count("[")
        depth += text.count("}") - text.count("{")
        if "=" in text and depth >= 0:
            break
    return " ".join(collected).strip()


def _router_prefix_from_assignment(statement: str) -> str | None:
    if not re.search(r"\b(?:APIRouter|Blueprint|Router)\s*\(", statement or ""):
        return None
    for key in ("prefix", "url_prefix"):
        match = re.search(
            rf"\b{key}\s*=\s*(['\"])(?P<prefix>(?:\\.|(?!\1).)*?)\1",
            statement,
        )
        if match:
            prefix = match.group("prefix").strip()
            if _looks_like_route_path(prefix):
                return prefix
    return None


def _route_trigger_with_prefix(trigger: str, prefix: str) -> str:
    value = str(trigger or "").strip()
    if not value or not prefix:
        return value
    match = re.match(r"^(?P<method>[A-Z]+)\s+(?P<path>.+)$", value)
    if match:
        return f"{match.group('method')} {_join_route_paths(prefix, match.group('path'))}"
    return _join_route_paths(prefix, value)


def _join_route_paths(prefix: str, path: str) -> str:
    left = str(prefix or "").strip()
    right = str(path or "").strip()
    if not left:
        return right
    if not right:
        return left
    if right == "/":
        return left if left.startswith("/") else f"/{left}"
    return f"{left.rstrip('/')}/{right.lstrip('/')}"


def _route_path_from_text(text: str) -> str | None:
    patterns = (
        (
            r"\b(?:route|path|url)\s*[:=]\s*(['\"])(?P<path>(?:\\.|(?!\1).)*?)\1",
            False,
        ),
        (
            r"\badd_url_rule\s*\(\s*(['\"])(?P<path>(?:\\.|(?!\1).)*?)\1",
            True,
        ),
        (
            r"(?:@?[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)?\s*\.\s*"
            r"(?:get|post|put|patch|delete|head|options|any|route|api_route|websocket)\s*"
            r"\(\s*(['\"])(?P<path>(?:\\.|(?!\1).)*?)\1",
            True,
        ),
        (
            r"\b(?:get|post|put|patch|delete|head|options|any|route|api_route|websocket)\s*"
            r"\(\s*(['\"])(?P<path>(?:\\.|(?!\1).)*?)\1",
            True,
        ),
        (
            r"[@\[]\s*(?:Controller|RequestMapping|Route|"
            r"Get|Post|Put|Patch|Delete|Head|Options|"
            r"GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping|"
            r"HttpGet|HttpPost|HttpPut|HttpPatch|HttpDelete)\s*"
            r"\(\s*(['\"])(?P<path>(?:\\.|(?!\1).)*?)\1",
            True,
        ),
        (
            r"\b(?:get|post|put|patch|delete|head|options|any|route|api_route|websocket)\s+"
            r"(['\"])(?P<path>(?:\\.|(?!\1).)*?)\1",
            True,
        ),
        (
            r"\(\s*(['\"])(?P<path>(?:\\.|(?!\1).)*?)\1",
            False,
        ),
    )
    for pattern, allow_relative in patterns:
        for match in re.finditer(pattern, text or "", re.IGNORECASE):
            path = match.group("path").strip()
            normalized = _normalize_route_path(path, allow_relative=allow_relative)
            if normalized:
                return normalized
    return None


def _looks_like_route_path(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return text.startswith(("/", "{", "<", ":")) or "/{" in text or "/:" in text


def _normalize_route_path(value: str, *, allow_relative: bool = False) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if allow_relative and not text.startswith(("/", "{", "<", ":")) and ("/{" in text or "/:" in text):
        return f"/{text.lstrip('/')}"
    if _looks_like_route_path(text):
        return text
    if not allow_relative:
        return None
    if re.search(r"\s", text):
        return None
    if text.startswith((".", "*")):
        return None
    return f"/{text.lstrip('/')}"


def _route_method_from_text(text: str) -> str | None:
    method_patterns = (
        r"\bmethods?\s*=\s*[\[\(\{]?\s*(['\"])(?P<method>get|post|put|patch|delete|head|options|any)\1",
        r"\bmethods?\s*:\s*[\[\(\{]?\s*(['\"])(?P<method>get|post|put|patch|delete|head|options|any)\1",
        r"\broute\s*\(\s*(['\"])(?:\\.|(?!\1).)*?\1\s*,\s*(?P<method>get|post|put|patch|delete|head|options|any)\s*\(",
        r"\bRequestMethod\.(?P<method>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b",
        r"[@\[]\s*(?P<method>Get|Post|Put|Patch|Delete|Head|Options)Mapping\s*\(",
        r"[@\[]\s*(?P<method>Get|Post|Put|Patch|Delete|Head|Options)Mapping\b",
        r"[@\[]\s*Http(?P<method>Get|Post|Put|Patch|Delete|Head|Options)\s*\(",
        r"[@\[]\s*Http(?P<method>Get|Post|Put|Patch|Delete|Head|Options)\b",
        r"\bmethod\s*[:=]\s*(['\"])(?P<method>get|post|put|patch|delete|head|options|any)\1",
        r"\.\s*methods?\s*\(\s*(['\"])(?P<method>get|post|put|patch|delete|head|options|any)\1",
        r"\bhttp\.Method(?P<method>Get|Post|Put|Patch|Delete|Head|Options)\b",
        r"[@.]\s*(?P<method>get|post|put|patch|delete|head|options|any|websocket)\s*\(",
        r"(?<![\w.])(?P<method>get|post|put|patch|delete|head|options|any|websocket)\s*\(\s*['\"]",
        r"\b(?P<method>get|post|put|patch|delete|head|options|any|websocket)\s+['\"]",
    )
    for pattern in method_patterns:
        match = re.search(pattern, text or "", re.IGNORECASE)
        if not match:
            continue
        method = match.group("method").upper()
        return "WEBSOCKET" if method == "WEBSOCKET" else method
    return None


def _message_payload_type_input_hints(signature: str) -> list[str]:
    params = _signature_param_section(signature or "")
    if params is None:
        return []
    payload_param_names = {
        "event", "evt", "message", "msg", "payload", "record", "consumerrecord",
    }
    hints: list[str] = []
    seen: set[str] = set()
    for raw_param in _split_signature_params(params):
        param = _signature_param_name(raw_param)
        if not param or param.lower() not in payload_param_names:
            continue
        type_hint = _signature_param_type_hint(raw_param, param)
        if not type_hint or type_hint in seen:
            continue
        seen.add(type_hint)
        hints.append(type_hint)
    return hints[:4]


def _registration_channel_input_hints(registration_line: str, entry_type: str) -> list[str]:
    if entry_type not in {"message", "queue", "scheduler", "job", "timer"}:
        return []
    hints: list[str] = []
    seen: set[str] = set()
    if entry_type in {"scheduler", "job", "timer"}:
        for match in re.finditer(
            r"""\b(?:id|job_id|name|task_id)\s*=\s*(['"])(?P<value>(?:\\.|(?!\1).)*?)\1""",
            registration_line or "",
        ):
            value = match.group("value").strip()
            if value and value not in seen:
                seen.add(value)
                hints.append(value)
    for match in re.finditer(r"""(['"])(?P<value>(?:\\.|(?!\1).)*?)\1""", registration_line or ""):
        value = match.group("value").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        hints.append(value)
    return hints[:8]


def _queue_registration_input_hints(site_text: str, entry_type: str) -> list[str]:
    if entry_type != "queue":
        return []
    text = site_text or ""
    if not re.search(r"\b(?:new\s+Worker|Worker\s*\(|Queue\s*\(|\.process\s*\()", text):
        return []
    hints: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"""(['"])(?P<value>(?:\\.|(?!\1).)*?)\1""", text):
        value = match.group("value").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        hints.append(value)
    return hints[:4]


def _symbol_channel_input_hints(symbol: str | None, entry_type: str) -> list[str]:
    if entry_type not in {"message", "queue", "scheduler", "job", "timer"}:
        return []
    text = str(symbol or "").strip()
    if not text:
        return []
    normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", text).lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    if not normalized:
        return []
    surface_tokens = (
        "queue", "topic", "event", "message", "job", "task", "worker",
        "cron", "timer", "poller", "timeout", "interval",
    )
    if not any(token in normalized.split("_") for token in surface_tokens):
        return []
    suffix_tokens = (
        "consumer", "subscriber", "producer", "handler", "listener", "worker",
        "processor", "process", "runner", "run", "callback", "cb", "tick",
    )
    parts = [part for part in normalized.split("_") if part]
    while len(parts) > 1 and parts[-1] in suffix_tokens:
        parts = parts[:-1]
    candidate = "_".join(parts)
    if not candidate or candidate == normalized and normalized in suffix_tokens:
        return []
    return [candidate]


def _file_entry_input_hints(symbol: str | None) -> list[str]:
    text = str(symbol or "").strip()
    if not text:
        return []
    normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", text).lower()
    parts = [part for part in re.split(r"[^a-z0-9]+", normalized) if part]
    if not parts:
        return []
    hints: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        if value and value not in seen:
            seen.add(value)
            hints.append(value)

    format_labels = {
        "csv": "CSV file",
        "tsv": "TSV file",
        "json": "JSON file",
        "xml": "XML file",
        "yaml": "YAML file",
        "yml": "YAML file",
        "xlsx": "XLSX file",
        "xls": "XLS file",
        "pdf": "PDF file",
    }
    for part in parts:
        if part in format_labels:
            add(format_labels[part])
    if any(part in parts for part in ("upload", "uploaded")):
        add("uploaded file")
    if "import" in parts or "ingest" in parts:
        add("import file")
    if "download" in parts or "export" in parts:
        add("download/export file")
    if any(part in parts for part in ("watch", "watcher")):
        add("watched file change")
    if not hints and any(part in parts for part in ("file", "input", "stdin", "scan")):
        add("input file")
    return hints[:6]


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
                graphql_entry = _graphql_schema_entry_for_site(
                    repo_root,
                    site["abs_file"],
                    site["line_number"],
                    symbol,
                    site["text"],
                    caller_chain,
                )
                if graphql_entry:
                    entry_paths.append(graphql_entry)
                    continue
                kafka_entry = _kafka_consumer_entry_for_site(
                    repo_root,
                    site["abs_file"],
                    site["line_number"],
                    symbol,
                    site["text"],
                    caller_chain,
                )
                if kafka_entry:
                    entry_paths.append(kafka_entry)
                    continue
                table_entry = _dispatch_table_entry_for_site(
                    repo_root,
                    site["abs_file"],
                    site["line_number"],
                    symbol,
                    site["text"],
                    caller_chain,
                )
                if table_entry:
                    entry_paths.append(table_entry)
                    continue
                filesystem_entry = _filesystem_entry_for_site(
                    repo_root,
                    site["abs_file"],
                    site["line_number"],
                    enclosing,
                    site["text"],
                    caller_chain,
                )
                if filesystem_entry:
                    entry_paths.append(filesystem_entry)
                    continue
                if enclosing:
                    decorated_caller_entry = _decorated_entry_for_symbol(
                        repo_root,
                        enclosing,
                        site["file"],
                    )
                    if decorated_caller_entry:
                        decorated_caller_entry["chain"] = caller_chain
                        decorated_caller_entry["depth"] = len(caller_chain) - 1
                        entry_paths.append(decorated_caller_entry)
                        continue
                entry_kind = _classify_entry(site["file"], enclosing, site["text"])
                if entry_kind:
                    entry_symbol = _entry_symbol_for_site(entry_kind, enclosing, symbol, site["text"])
                    metadata = _entry_metadata_for_symbol(
                        repo_root,
                        site["abs_file"],
                        site["line_number"],
                        enclosing,
                        entry_symbol,
                    )
                    anonymous_metadata = (
                        _anonymous_entry_metadata_for_site(
                            repo_root,
                            site["abs_file"],
                            site["line_number"],
                        )
                        if enclosing is None else {}
                    )
                    if anonymous_metadata.get("input_hints"):
                        existing_hints = _specific_signature_input_hints(
                            metadata.get("input_hints") or [],
                            anonymous_metadata["input_hints"],
                        )
                        metadata["input_hints"] = _merge_ordered_strings(
                            existing_hints,
                            anonymous_metadata["input_hints"],
                        )
                    anonymous_evidence = anonymous_metadata.pop("_anonymous_entry_evidence", None)
                    if entry_kind == "route":
                        route_site_text = _route_call_context_for_site_file(
                            site["abs_file"],
                            site["line_number"],
                        ) or site["text"]
                        route_prefix = _route_mount_prefix_for_site_file(
                            site["abs_file"],
                            route_site_text,
                        )
                        route_hints = _route_template_input_hints([route_site_text])
                        if route_prefix:
                            route_hints = _merge_ordered_strings(
                                _route_template_input_hints([route_prefix]),
                                route_hints,
                            )
                        if route_hints:
                            metadata["input_hints"] = _merge_ordered_strings(
                                metadata.get("input_hints"),
                                route_hints,
                            )
                        route_trigger = _route_external_trigger_from_texts([route_site_text])
                        if route_prefix and route_trigger:
                            route_trigger = _route_trigger_with_prefix(route_trigger, route_prefix)
                        if route_trigger:
                            metadata["external_trigger"] = route_trigger
                    elif entry_kind == "cli":
                        cli_hints = _cli_registration_input_hints(
                            site["abs_file"],
                            site["line_number"],
                        )
                        if cli_hints:
                            metadata["input_hints"] = _merge_cli_input_hints(
                                cli_hints,
                                metadata.get("input_hints"),
                            )
                    else:
                        channel_hints = _merge_ordered_strings(
                            _registration_channel_input_hints(site["text"], entry_kind),
                            _queue_registration_input_hints(site["text"], entry_kind),
                            _symbol_channel_input_hints(entry_symbol, entry_kind),
                        )
                        if channel_hints:
                            metadata["input_hints"] = _merge_ordered_strings(
                                channel_hints,
                                metadata.get("input_hints"),
                            )
                            metadata["entry_label"] = (
                                f"{_ENTRY_DISCOVERY_KIND_LABELS.get(entry_kind, '外部入口')} {channel_hints[0]}"
                            )
                    if entry_kind == "file":
                        metadata["input_hints"] = _merge_ordered_input_hints(
                            _file_entry_input_hints(entry_symbol),
                            metadata.get("input_hints"),
                        )
                    config_evidence = (
                        _config_operation_evidence_for_site(
                            site["abs_file"],
                            site["line_number"],
                        )
                        if entry_kind == "config" else ""
                    )
                    entry_paths.append({
                        "entry_kind": entry_kind,
                        "entry_symbol": entry_symbol,
                        "entry_file": site["file"],
                        "entry_label": metadata.pop("entry_label", None)
                        or _public_entry_label(entry_kind, entry_symbol),
                        "call_line": site["line_number"],
                        "chain": caller_chain,
                        "depth": len(caller_chain) - 1,
                        "evidence": (
                            f"{anonymous_evidence} | {site['file']}:{site['line_number']} {site['text']}"
                            if anonymous_evidence
                            else (
                                f"{site['file']}:{site['line_number']} {site['text']} | {config_evidence}"
                                if config_evidence
                                else f"{site['file']}:{site['line_number']} {site['text']}"
                            )
                        ),
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

    for entry in entry_paths:
        _rebind_entry_path_file_to_symbol_definition(repo_root, entry)

    # De-duplicate entry paths by (kind, symbol, file).
    seen: dict[tuple, dict] = {}
    unique_entries: list[dict] = []
    for entry in entry_paths:
        key = (entry["entry_kind"], entry.get("entry_symbol"), entry.get("entry_file"))
        if key in seen:
            existing = seen[key]
            _merge_duplicate_entry_path(existing, entry)
            continue
        seen[key] = entry
        unique_entries.append(entry)
    for entry in unique_entries:
        _augment_entry_input_hints_from_symbol_source(repo_root, entry)
    return unique_entries[:6], _dedupe_branches(caller_branches)


def _merge_duplicate_entry_path(existing: dict, incoming: dict) -> None:
    _merge_entry_external_trigger(existing, incoming)
    merged_hints = _merge_ordered_input_hints(
        existing.get("input_hints"),
        incoming.get("input_hints"),
    )
    if merged_hints:
        existing["input_hints"] = merged_hints
    for key in ("entry_label", "source_verification", "provider", "turn_id"):
        if not existing.get(key) and incoming.get(key):
            existing[key] = incoming[key]
    incoming_evidence = str(incoming.get("evidence") or "").strip()
    if incoming_evidence and incoming_evidence != str(existing.get("evidence") or "").strip():
        confirmations = list(existing.get("confirming_evidence") or [])
        if incoming_evidence not in confirmations:
            confirmations.append(incoming_evidence)
        existing["confirming_evidence"] = confirmations[:4]
    incoming_tool = str(incoming.get("tool") or "").strip()
    if incoming_tool and incoming_tool != str(existing.get("tool") or "").strip():
        tools = list(existing.get("confirming_tools") or [])
        if incoming_tool not in tools:
            tools.append(incoming_tool)
        existing["confirming_tools"] = tools[:4]


def _rebind_entry_path_file_to_symbol_definition(repo_root: Path, entry: dict) -> None:
    entry_symbol = str(entry.get("entry_symbol") or "").strip()
    entry_file = str(entry.get("entry_file") or "").strip()
    if not entry_symbol or not entry_file:
        return
    try:
        current_file = (repo_root / entry_file).resolve()
    except OSError:
        return
    rebound = _rebind_entry_file_to_symbol_definition(
        repo_root,
        current_file,
        entry_symbol,
    )
    if rebound is None:
        return
    entry["entry_file"] = _relative_path(repo_root, rebound)


def _augment_entry_input_hints_from_symbol_source(
    repo_root: Path,
    entry: dict,
) -> None:
    entry_symbol = entry.get("entry_symbol")
    entry_file = entry.get("entry_file")
    if not entry_symbol or not entry_file:
        return
    try:
        abs_file = (repo_root / str(entry_file)).resolve()
    except OSError:
        return
    source_hints = _request_field_hints_for_symbol_source(
        repo_root,
        str(abs_file),
        str(entry_symbol),
    )
    existing_hints = _merge_ordered_input_hints(entry.get("input_hints"))
    signature_hints = _handler_signature_input_hints(str(abs_file), str(entry_symbol))
    signature_hints = _specific_signature_input_hints(
        signature_hints,
        _merge_ordered_input_hints(source_hints, existing_hints),
    )
    if str(entry.get("entry_kind") or "").strip().lower() == "cli" and entry.get("input_hints"):
        signature_hints = _filter_cli_signature_input_hints(
            signature_hints,
            entry.get("input_hints"),
        )
    source_hints = _merge_ordered_input_hints(
        source_hints,
        signature_hints,
    )
    merged_hints = _merge_ordered_input_hints(
        existing_hints,
        source_hints,
    )
    if merged_hints:
        entry["input_hints"] = merged_hints


def _augment_entry_paths_input_hints(repo_root: Path, entry_paths: list[dict]) -> None:
    for entry in entry_paths:
        _augment_entry_input_hints_from_symbol_source(repo_root, entry)


def _request_field_hints_for_symbol_source(
    repo_root: Path,
    abs_file: str,
    entry_symbol: str,
) -> list[str]:
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
        return []
    try:
        lines = symbol_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    definition_line = _find_strict_definition_line(lines, entry_symbol)
    if not definition_line:
        return []
    return _request_field_hints(str(symbol_file), definition_line, entry_symbol)


_FILESYSTEM_OPERATION_RE = re.compile(
    r"\b(?:glob|rglob|iterdir|listdir|scandir|walk)\s*\("
    r"|\.read_text\s*\("
    r"|\.read_bytes\s*\("
    r"|\bopen\s*\(",
    re.IGNORECASE,
)


def _filesystem_entry_for_site(
    repo_root: Path,
    abs_file: str,
    line_number: int,
    enclosing: str | None,
    site_text: str,
    caller_chain: list[str],
) -> dict | None:
    if not enclosing:
        return None
    try:
        path = Path(abs_file)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    idx = max(0, line_number - 1)
    start = max(0, idx - 8)
    end = min(len(lines), idx + 8)
    window = lines[start:end]
    evidence_line = next(
        (line.strip() for line in window if _FILESYSTEM_OPERATION_RE.search(line)),
        "",
    )
    if not evidence_line:
        return None

    rel_file = _relative_path(repo_root, path)
    metadata = _entry_metadata_for_symbol(
        repo_root,
        str(path),
        line_number,
        enclosing,
        enclosing,
    )
    fs_hints = _filesystem_operation_input_hints("\n".join(window))
    input_hints = _merge_ordered_input_hints(fs_hints, metadata.pop("input_hints", []))
    entry_label = metadata.pop("entry_label", None)
    entry = {
        "entry_kind": "file",
        "entry_symbol": enclosing,
        "entry_file": rel_file,
        "call_line": line_number,
        "chain": caller_chain,
        "depth": max(0, len(caller_chain) - 1),
        "evidence": f"{rel_file}:{line_number} {site_text.strip()} | {evidence_line}",
        "tool": "source-filesystem",
        "entry_label": entry_label or _public_entry_label("file", enclosing),
    }
    if input_hints:
        entry["input_hints"] = input_hints
    entry.update(metadata)
    return entry


def _filesystem_operation_input_hints(window_text: str) -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        if value and value not in seen:
            seen.add(value)
            hints.append(value)

    format_labels = {
        "csv": "CSV file",
        "tsv": "TSV file",
        "json": "JSON file",
        "jsonl": "JSONL file",
        "xml": "XML file",
        "yaml": "YAML file",
        "yml": "YAML file",
        "xlsx": "XLSX file",
        "xls": "XLS file",
        "txt": "text file",
        "log": "log file",
    }
    for match in re.finditer(r"""\*\.(?P<ext>[A-Za-z0-9]+)""", window_text or ""):
        label = format_labels.get(match.group("ext").lower())
        if label:
            add(label)
    lowered = (window_text or "").lower()
    if any(token in lowered for token in ("glob(", "rglob(", "iterdir(", "listdir(", "scandir(", "walk(")):
        add("input directory")
    if not hints and any(token in lowered for token in ("read_text(", "read_bytes(", "open(")):
        add("input file")
    return hints[:6]


_CONFIG_OPERATION_RE = re.compile(
    r"\b(?:os\.)?environ\b"
    r"|\b(?:os\.)?getenv\s*\("
    r"|\bprocess\.env\b"
    r"|\bgetenv\s*\("
    r"|\b(?:load_config|read_config|parse_config)\s*\("
    r"|\.ya?ml\b|\.toml\b|\.ini\b|\.conf\b",
    re.IGNORECASE,
)


def _config_operation_evidence_for_site(abs_file: str, line_number: int) -> str:
    try:
        path = Path(abs_file)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    idx = max(0, line_number - 1)
    start = max(0, idx - 10)
    end = min(len(lines), idx + 6)
    for pos in range(start, end):
        text = lines[pos].strip()
        if text and _CONFIG_OPERATION_RE.search(text):
            return text[:200]
    return ""


def _graphql_schema_entry_for_site(
    repo_root: Path,
    abs_file: str,
    line_number: int,
    symbol: str,
    site_text: str,
    caller_chain: list[str],
) -> dict | None:
    if not symbol or not re.search(rf"\b{re.escape(symbol)}\b", site_text or ""):
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
    window_text = "\n".join(window)
    lowered = window_text.lower()
    graphql_surface = (
        "graphql" in lowered
        or "makeexecutableschema" in lowered
        or "apolloserver" in lowered
        or "graphqlhttp" in lowered
        or "buildschema" in lowered
    )
    resolver_surface = any(
        token in lowered
        for token in (
            "resolver", "resolvers", "rootvalue",
            "mutation", "query", "subscription",
        )
    )
    if not (graphql_surface and resolver_surface):
        return None

    operation = _graphql_operation_from_window(window, idx - start)
    rel_file = _relative_path(repo_root, path)
    metadata = _entry_metadata_for_symbol(repo_root, str(path), line_number, None, symbol)
    entry_label = metadata.pop("entry_label", None)
    entry = {
        "entry_kind": "api",
        "entry_symbol": symbol,
        "entry_file": rel_file,
        "call_line": line_number,
        "chain": caller_chain,
        "depth": max(0, len(caller_chain) - 1),
        "evidence": (
            f"{rel_file}:{line_number} GraphQL {operation} {site_text.strip()}"
            if operation else f"{rel_file}:{line_number} {site_text.strip()}"
        ),
        "tool": "source-graphql-schema",
        "entry_label": entry_label or f"GraphQL {operation or 'resolver'} {symbol}",
    }
    input_hints = metadata.pop("input_hints", [])
    if input_hints:
        entry["input_hints"] = input_hints
    entry.update(metadata)
    return entry


def _graphql_operation_from_window(window: list[str], relative_idx: int) -> str | None:
    for idx in range(relative_idx, -1, -1):
        match = re.search(r"\b(Mutation|Query|Subscription)\b", window[idx] or "")
        if match:
            return match.group(1)
    for line in window:
        match = re.search(r"\b(Mutation|Query|Subscription)\b", line or "")
        if match:
            return match.group(1)
    return None


def _kafka_consumer_entry_for_site(
    repo_root: Path,
    abs_file: str,
    line_number: int,
    symbol: str,
    site_text: str,
    caller_chain: list[str],
) -> dict | None:
    if not symbol or not re.search(rf"\b{re.escape(symbol)}\b", site_text or ""):
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
    window_text = "\n".join(window)
    lowered = window_text.lower()
    if not (
        "kafka" in lowered
        and "consumer.subscribe" in lowered
        and ("eachmessage" in lowered or "consumer.run" in lowered)
    ):
        return None

    topic_hints = _kafka_topic_input_hints(window_text)
    rel_file = _relative_path(repo_root, path)
    metadata = _entry_metadata_for_symbol(repo_root, str(path), line_number, None, symbol)
    merged_hints = _merge_ordered_input_hints(
        topic_hints,
        metadata.pop("input_hints", []),
    )
    entry_label = metadata.pop("entry_label", None)
    label = (
        entry_label
        or (f"Kafka topic {topic_hints[0]}" if topic_hints else f"Kafka consumer {symbol}")
    )
    entry = {
        "entry_kind": "message",
        "entry_symbol": symbol,
        "entry_file": rel_file,
        "call_line": line_number,
        "chain": caller_chain,
        "depth": max(0, len(caller_chain) - 1),
        "evidence": f"{rel_file}:{line_number} Kafka consumer {site_text.strip()}",
        "tool": "source-kafka-consumer",
        "entry_label": label,
    }
    if merged_hints:
        entry["input_hints"] = merged_hints
    entry.update(metadata)
    return entry


def _kafka_topic_input_hints(window_text: str) -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()
    patterns = (
        r"\btopics?\s*:\s*\[\s*(['\"])(?P<value>(?:\\.|(?!\1).)*?)\1",
        r"\btopic\s*:\s*(['\"])(?P<value>(?:\\.|(?!\1).)*?)\1",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, window_text or "", re.IGNORECASE):
            value = match.group("value").strip()
            if value and value not in seen:
                seen.add(value)
                hints.append(value)
    return hints[:6]


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
            "register", "subscribe", ".on", ".once", "listener", "schedule", "scheduler", "job",
            "grpc", "servicer_to_server",
        )
    )
    if not (assignment_seen and registration_line and callback_like):
        return None

    rel_file = _relative_path(repo_root, path)
    entry_type = _registered_entry_type(registration_line, window)
    if entry_type == "route":
        route_symbol = _registered_route_symbol(site_text, caller_chain)
        if route_symbol:
            symbol = route_symbol
    metadata = _entry_metadata_for_symbol(repo_root, str(path), line_number, enclosing, symbol)
    if entry_type == "route":
        route_prefix = _route_mount_prefix_for_site(lines, site_text)
        route_hints = _route_template_input_hints([site_text, registration_line])
        if route_prefix:
            route_hints = _merge_ordered_strings(
                _route_template_input_hints([route_prefix]),
                route_hints,
            )
        if route_hints:
            metadata["input_hints"] = _merge_ordered_strings(
                metadata.get("input_hints"),
                route_hints,
            )
        route_trigger = _route_external_trigger_from_texts([site_text, registration_line, *window])
        if route_prefix and route_trigger:
            route_trigger = _route_trigger_with_prefix(route_trigger, route_prefix)
        if route_trigger:
            metadata["external_trigger"] = route_trigger
    else:
        channel_hints = _registration_channel_input_hints(registration_line, entry_type)
        if channel_hints:
            metadata["input_hints"] = _merge_ordered_strings(
                channel_hints,
                metadata.get("input_hints"),
            )
            metadata["entry_label"] = (
                f"{_ENTRY_DISCOVERY_KIND_LABELS.get(entry_type, '外部入口')} {channel_hints[0]}"
            )
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


def _dispatch_table_entry_for_site(
    repo_root: Path,
    abs_file: str,
    line_number: int,
    traced_symbol: str,
    site_text: str,
    caller_chain: list[str],
) -> dict | None:
    if not traced_symbol:
        return None
    try:
        path = Path(abs_file)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    idx = max(0, line_number - 1)
    start = max(0, idx - 10)
    end = min(len(lines), idx + 12)
    window = lines[start:end]
    context_text = "\n".join(window)
    if not _DISPATCH_TABLE_CONTEXT_RE.search(context_text):
        return None
    key = _dispatch_table_key_for_symbol(site_text, traced_symbol, window)
    if not key:
        return None

    rel_file = _relative_path(repo_root, path)
    entry_type = _dispatch_table_entry_type(context_text, window)
    metadata = _entry_metadata_for_symbol(
        repo_root,
        str(path),
        line_number,
        None,
        traced_symbol,
    )
    if entry_type == "route":
        key_hints = _route_template_input_hints([key])
    else:
        key_hints = [key]
    input_hints = _merge_ordered_strings(key_hints, metadata.pop("input_hints", []))
    route_method = (
        _dispatch_table_route_method_for_symbol(site_text, traced_symbol, window)
        if entry_type == "route"
        else None
    )
    external_trigger = f"{route_method} {key}" if route_method else key
    entry = {
        "entry_kind": entry_type,
        "entry_symbol": traced_symbol,
        "entry_file": rel_file,
        "entry_label": _dispatch_table_entry_label(entry_type, key, traced_symbol, route_method),
        "external_trigger": external_trigger,
        "call_line": line_number,
        "chain": caller_chain,
        "depth": max(0, len(caller_chain) - 1),
        "evidence": f"{rel_file}:{line_number} {site_text.strip()}",
        "tool": "source-table",
        "input_hints": input_hints,
    }
    entry.update(metadata)
    return entry


def _dispatch_table_key_for_symbol(
    site_text: str,
    traced_symbol: str,
    window: list[str],
) -> str | None:
    for match in _DISPATCH_TABLE_ENTRY_RE.finditer(site_text or ""):
        if match.group("symbol") == traced_symbol:
            return match.group("key")
    handler_line_index = _dispatch_table_handler_line_index(window, traced_symbol)
    if handler_line_index is None:
        return None
    block_text = _dispatch_table_initializer_block(window, handler_line_index)
    key_match = _DISPATCH_TABLE_KEY_RE.search(block_text)
    if key_match:
        return key_match.group("key")
    positional_match = _DISPATCH_TABLE_ENTRY_RE.search(block_text)
    if positional_match and positional_match.group("symbol") == traced_symbol:
        return positional_match.group("key")
    return None


def _dispatch_table_route_method_for_symbol(
    site_text: str,
    traced_symbol: str,
    window: list[str],
) -> str | None:
    candidates = [site_text or ""]
    handler_line_index = _dispatch_table_handler_line_index(window, traced_symbol)
    if handler_line_index is not None:
        candidates.append(_dispatch_table_initializer_block(window, handler_line_index))
    candidates.append("\n".join(window))
    for text in candidates:
        method = _route_method_from_text(text or "")
        if method:
            return method
    return None


def _dispatch_table_handler_line_index(window: list[str], traced_symbol: str) -> int | None:
    for index, line in enumerate(window):
        for match in _DISPATCH_TABLE_HANDLER_RE.finditer(line or ""):
            if _dispatch_table_handler_symbol_matches(match.group("symbol"), traced_symbol):
                return index
    return None


def _dispatch_table_handler_symbol_matches(handler_symbol: str, traced_symbol: str) -> bool:
    value = str(handler_symbol or "").strip()
    traced = str(traced_symbol or "").strip()
    if not value or not traced:
        return False
    return value == traced or value.rsplit(".", 1)[-1] == traced


def _dispatch_table_initializer_block(window: list[str], handler_line_index: int) -> str:
    start = handler_line_index
    while start > 0:
        if "{" in window[start]:
            break
        start -= 1
    end = handler_line_index
    while end + 1 < len(window):
        if "}" in window[end]:
            break
        end += 1
        if "}" in window[end]:
            break
    return "\n".join(window[start:end + 1])


def _dispatch_table_entry_type(context_text: str, window: list[str]) -> str:
    lowered = (context_text or "").lower()
    if re.search(r"\b(?:path|route|url)\s*:", context_text or "", re.IGNORECASE):
        return "route"
    if _route_method_from_text(context_text or ""):
        return "route"
    if re.search(r"\b(?:cli|cmd|command)(?:s|_table|_entry)?\b", lowered):
        return "cli"
    return _registered_entry_type(context_text, window)


def _dispatch_table_entry_label(
    entry_type: str,
    key: str,
    symbol: str,
    method: str | None = None,
) -> str:
    label = _ENTRY_DISCOVERY_KIND_LABELS.get(entry_type, "external entry")
    if entry_type == "cli":
        return f"CLI command {key}"
    if entry_type in {"api", "route", "endpoint"}:
        if method:
            return f"{label} {method} {key}"
        return f"{label} {key}"
    return f"{label} {key or symbol}"


def _registered_route_symbol(site_text: str, caller_chain: list[str]) -> str | None:
    for candidate in reversed(caller_chain or []):
        if candidate and re.search(rf"\b{re.escape(candidate)}\b", site_text or ""):
            return candidate
    match = re.search(r"\b[A-Za-z_]\w*\.([A-Za-z_]\w*)\s*(?:,|\))", site_text or "")
    return match.group(1) if match else None


def _callback_symbol_from_assignment(text: str) -> str | None:
    match = _CALLBACK_ASSIGN_RE.search(text or "")
    return match.group("symbol") if match else None


def _registered_entry_type(registration_line: str, window: list[str]) -> str:
    text = (registration_line + "\n" + "\n".join(window)).lower()
    if re.search(r"\.\s*(?:get|post|put|patch|delete|head|options|any|route)\s*\(", text):
        return "route"
    if re.search(r"\bhandle(?:func)?\s*\(", text) and _route_path_from_text(text):
        return "route"
    if "rpc" in text or "api" in text or "request" in text:
        return "api"
    if "grpc" in text or "servicer_to_server" in text:
        return "api"
    if "cli" in text or "cmd" in text:
        return "cli"
    if re.search(r"\.\s*(?:on|once)\s*\(", text):
        return "message"
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
    ):
        value = entry.get(key)
        if value is not None and value != "":
            provenance[key] = value
    return provenance


def _black_box_case_evidence(entry: dict) -> str | None:
    tool = str(entry.get("tool") or entry.get("provider") or "").strip()
    entry_label = _safe_external_label(entry)
    entry_file = str(entry.get("entry_file") or entry.get("file_path") or "").strip()

    parts: list[str] = []
    if tool:
        parts.append(f"{tool} confirmed")
    else:
        parts.append("Confirmed")
    if entry_label:
        parts.append(entry_label)
    if entry_file:
        parts.append(f"in {entry_file}")
    if len(parts) <= 1:
        return None
    return " ".join(parts)


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
        input_hints = _black_box_input_hints(entry, hit)
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
            "evidence": _black_box_case_evidence(entry),
            **_entry_case_provenance(entry),
        })

    primary_entry = actionable_entry_paths[0] if actionable_entry_paths else {}
    primary_entry_label = _safe_external_label(primary_entry) if primary_entry else None
    primary_input_hints = _black_box_input_hints(primary_entry, hit) if primary_entry else []

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
    verified_actionable_candidates = [
        candidate for candidate in actionable_candidates
        if _entry_discovery_candidate_is_verified_actionable(candidate)
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
        "gray_box_allowed": not verified_actionable_candidates and status in {
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


def _entry_discovery_candidate_is_verified_actionable(candidate: dict) -> bool:
    if not _entry_discovery_candidate_is_actionable(candidate):
        return False
    source_verification = str(candidate.get("source_verification") or "").strip().lower()
    if source_verification in {
        "needs_source_verification",
        "unverified",
        "rejected",
        "invalid",
    }:
        return False
    return source_verification in {"source_backed", "graph_backed"}


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
            "input_hints": _coerce_input_hints(entry.get("input_hints")),
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
    normalized_target = _agent_chain_symbol_key(function_name)
    entry_symbol = str(entry.get("entry_symbol") or entry.get("entry_label") or "").strip()
    normalized_entry_symbol = _agent_chain_symbol_key(entry_symbol)
    if entry_symbol and normalized_entry_symbol != normalized_target:
        return False
    chain = _normalize_agent_entry_chain(entry.get("chain"))
    normalized_chain = [_agent_chain_symbol_key(value) for value in chain]
    if normalized_chain and any(value != normalized_target for value in normalized_chain):
        return False
    entry_file = str(entry.get("entry_file") or "").replace("\\", "/")
    gap_file = str(gap.get("file_path") or "").replace("\\", "/")
    if entry_file and gap_file and entry_file != gap_file and not gap_file.endswith(entry_file):
        return False
    return bool(normalized_entry_symbol == normalized_target or normalized_chain == [normalized_target])


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
            "input_hints": _coerce_input_hints(entry.get("input_hints")),
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
            "input_hints": _coerce_input_hints(entry.get("input_hints")),
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
    for status in (
        "configuration_error",
        "invalid_output",
        "error",
        "timeout",
        "rejected_command",
    ):
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
            if (
                isinstance(candidate, dict)
                and _entry_discovery_candidate_is_verified_actionable(candidate)
            ):
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
        _SOURCE_PATH_RE,
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
